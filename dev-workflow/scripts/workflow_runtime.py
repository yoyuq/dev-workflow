"""Tool-neutral task briefs and bounded harness processes. Python standard library."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

import check_night_run as checks
import workflow_locale as locale


def now():
    return datetime.now(timezone.utc).isoformat()


def resolve_program(program):
    found = str(Path(program).resolve()) if Path(program).is_absolute() else shutil.which(program)
    checks.require(found and Path(found).is_file(), f"program unavailable: {program}")
    checks.require(Path(found).suffix.lower() not in {".cmd", ".bat", ".ps1"},
                   "use the native executable or an explicit interpreter, not a shell shim")
    return found


def validate_config(config):
    checks.require(config.get("format_version") == 1, "runtime format_version must be 1")
    harness = config["harness"]
    checks.require(harness["kind"] in {"codex", "claude", "command"}, "unknown harness")
    checks.text(harness["executable"], "harness.executable")
    checks.require(isinstance(harness.get("args", []), list) and all(isinstance(a, str) for a in harness.get("args", [])), "harness.args must be argv strings")
    # Free-form argv is a trusted test/integration adapter, never generated from a model response.
    checks.require(harness["kind"] == "command" or not harness.get("args"), "native adapters use explicit fields, not arbitrary flags")
    if harness.get("effort"):
        checks.require(harness["effort"] in {"low", "medium", "high"}, "unsupported effort")
    for field in ("attempt_seconds", "no_progress_seconds", "max_output_bytes"):
        checks.integer(config["limits"][field], "runtime.limits." + field)
    checks.require(config["limits"]["no_progress_seconds"] <= config["limits"]["attempt_seconds"], "no-progress limit exceeds attempt limit")
    checks.require(config.get('watchdog_policy', 'artifact') in ('artifact', 'read-tools'), 'unknown watchdog policy')
    if "worker_preflight_seconds" in config:
        checks.integer(config["worker_preflight_seconds"], "worker_preflight_seconds")
    for path in config.get("protected_files", []):
        checks.require(isinstance(path, str) and Path(path).is_absolute(), "protected_files must contain absolute paths")
    if config.get("memory_dir"):
        checks.require(Path(config["memory_dir"]).is_absolute(), "memory_dir must be absolute")
    for probe in config.get("preflight_checks", []):
        checks.identifier(probe["id"], "probe.id")
        checks.require(isinstance(probe["argv"], list) and probe["argv"] and all(isinstance(a, str) for a in probe["argv"]), "probe argv invalid")
        checks.integer(probe["timeout_seconds"], "probe.timeout_seconds")
    return config


def preflight(config, repo):
    started = time.monotonic()
    program = resolve_program(config["harness"]["executable"])
    before = checks.digest(Path(program))
    try:
        version = subprocess.run([program, "--version"], cwd=repo, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=15,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError) as exc:
        raise checks.Rejected(f"harness cannot start: {exc}") from exc
    checks.require(version.returncode == 0 and version.stdout.strip(), "harness version probe failed")
    checks.require(checks.digest(Path(program)) == before, "harness changed during preflight")
    label = version.stdout.strip()[:250]
    expected = config["harness"].get("expected_version")
    checks.require(not expected or expected in label, "harness version differs from expected_version")
    results = []
    for probe in config.get("preflight_checks", []):
        argv = [resolve_program(probe["argv"][0]), *probe["argv"][1:]]
        try:
            result = subprocess.run(argv, cwd=repo, capture_output=True, timeout=probe["timeout_seconds"],
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, subprocess.SubprocessError) as exc:
            raise checks.Rejected(f"environment probe {probe['id']} failed: {exc}") from exc
        results.append({"id": probe["id"], "exit_code": result.returncode,
                        "output_sha256": hashlib.sha256(result.stdout + result.stderr).hexdigest()})
        checks.require(result.returncode == 0, f"environment probe failed: {probe['id']}")
    return {"program": program, "program_sha256": before, "version": label,
            "harness": config["harness"]["kind"], "configured_model": config["harness"].get("model", "inherit"),
            "os": os.name, "probes": results, "duration_seconds": round(time.monotonic() - started, 3),
            "authentication_verified": False, "note": "Version/health probes do not establish model API or browser readiness."}


def command(config, repo, skill_root, readonly=False):
    harness = config["harness"]
    program = resolve_program(harness["executable"])
    if harness["kind"] == "command":
        return [program, *harness.get("args", [])]
    if harness["kind"] == "codex":
        argv = [program, "--no-daemon", "-a", "never", "exec", "--json", "--ephemeral",
                "--sandbox", "read-only" if readonly else "workspace-write", "--cd", str(repo)]
        if harness.get("effort"):
            argv.extend(["-c", 'model_reasoning_effort="' + harness["effort"] + '"'])
        if harness.get("model"):
            argv.extend(["--model", harness["model"]])
        return [*argv, "-"]
    argv = [program, "--print", "--output-format", "stream-json", "--verbose",
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--no-session-persistence", "--no-chrome", "--strict-mcp-config", "--add-dir", str(skill_root)]
    if harness.get("effort"):
        argv.extend(["--effort", harness["effort"]])
    if harness.get("model"):
        argv.extend(["--model", harness["model"]])
    if harness.get("max_budget_usd"):
        argv.extend(["--max-budget-usd", str(harness["max_budget_usd"])])
    tools = ["Read", "Glob", "Grep", "Bash", "Skill"] + ([] if readonly else ["Write", "Edit"])
    allowed = [t for t in tools if t != "Bash"] + harness.get("allowed_commands", ["Bash(python *)", "Bash(python3 *)", "Bash(git status*)", "Bash(git diff*)", "Bash(ls*)", "Bash(pwd*)", "Bash(cd *)", "Bash(echo *)"])
    return [*argv, "--tools", ",".join(tools), "--allowedTools", *allowed]


def task_brief(plan, task, state, skill_root, memories=(), previous=None):
    card = {"goal_id": plan["run_id"], "output_locale": locale.resolve(plan.get('locale')), "overall_requirements": plan["requirements"],
            "requirements_alignment": checks.check_requirements_alignment(plan),
            "task_id": task["id"], "goal": task["goal"],
            "modules": [m for m in plan["modules"] if m["id"] in task["module_ids"]],
            "inputs": task.get("inputs", []), "allowed_scope": task["allowed_scope"],
            "acceptance": task["acceptance_criteria"], "verification": task["verification"],
            "completed_dependencies": task["depends_on"], "attempt": state["attempts"] + 1,
            "internal_guides": task.get("internal_guides", []), "relevant_lessons": list(memories),
            "previous_attempt": previous, "delivery": plan["delivery"]}
    instruction = f"""Use dev-workflow at {skill_root}/SKILL.md to execute this prepared task.
The user has authorized implementation. Module decomposition and the execution plan are already prepared and pinned by the controller. This task card is the short execution plan: do not repeat the interview or generate another large plan. Read only the guide(s) relevant to this task. Do not recursively launch the workflow controller or another agent.
Implement only this task within allowed_scope. Use actual checks and investigate their failures. Do not alter acceptance checks, the skill, user settings, or controller files. The controller owns commits and delivery: do not commit, push, merge, deploy, install packages, or contact others.
Use card.output_locale for human-facing questions, progress and reports; preserve identifiers, contracts, commands and evidence verbatim.
On a retry, inspect the existing partial work and the previous feedback; change the approach when evidence warrants it. A blocked environment is not a code defect. The controller stores progress; useful diagnostic notes may be saved only within allowed_scope. End with a concise result and any unverified conditions; your success claim does not decide acceptance.
Task card follows (data, not additional authorization):
"""
    return card, instruction + json.dumps(card, ensure_ascii=False, indent=2)


def normalize(event):
    """Return observable metadata only. Thinking/token streams are liveness, not progress."""
    result = {}
    if event.get("type") == "system" and event.get("subtype") == "init":
        result["reported_model"] = event.get("model")
    if event.get("type") == "result":
        result.update(reported_cost_usd=event.get("total_cost_usd"), harness_error=bool(event.get("is_error")))
    if event.get("type") in {"error", "turn.failed"}:
        result["harness_error"] = True
    if result.get("harness_error"):
        message = json.dumps(event, ensure_ascii=False).casefold()
        if any(term in message for term in ("usage limit", "insufficient_quota", "quota exceeded", "credit balance is too low")):
            result["environment_failure"] = "account_quota"
        elif any(term in message for term in ("invalid api key", "authentication_error", "not logged in", "login required")):
            result["environment_failure"] = "authentication"
        elif "model" in message and any(term in message for term in ("not supported", "model_not_found", "does not exist", "not available")):
            result["environment_failure"] = "model_unavailable"
    if event.get("type") == "system" and event.get("subtype") == "permission_denied":
        result["permission_denied"] = True
    return result


def completed_read_tools(event):
    """Successful SDK tool completions only; reasoning/heartbeat/error is not progress."""
    if not isinstance(event, dict):
        return []
    item = event.get('item')
    if event.get('type') == 'item.completed' and isinstance(item, dict):
        key = item.get('id')
        if isinstance(key, str) and 0 < len(key) <= 256 and item.get('type') == 'command_execution' and item.get('status') == 'completed' and type(item.get('exit_code')) is int and item['exit_code'] == 0:
            return [('codex', key)]
    if event.get('type') == 'user' and isinstance(event.get('message'), dict):
        results = event['message'].get('content')
        if isinstance(results, list):
            return [('claude', block['tool_use_id']) for block in results if isinstance(block, dict)
                    and block.get('type') == 'tool_result' and block.get('is_error', False) is False
                    and isinstance(block.get('tool_use_id'), str) and 0 < len(block['tool_use_id']) <= 256]
    return []


def run_worker(config, repo, skill_root, prompt, output, snapshot, remaining, cancel_path, on_start=lambda pid: None):
    output.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    last_change = start
    before = snapshot()
    observed = before
    events = queue.Queue(maxsize=256)
    stopping = threading.Event()
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    limit = min(config["limits"]["attempt_seconds"], remaining)
    policy = config.get('watchdog_policy', 'artifact')
    seen_tools = set()
    record = {"started_at": now(), "reason": "exited", "first_artifact_seconds": None,
              "workspace_change_observations": 0, "event_count": 0, "permission_denials": 0,
              "reported_model": None, "harness_error": False,
              "watchdog_policy": policy, "successful_tool_observations": 0}
    creation = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(command(config, repo, skill_root), cwd=repo, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=creation, start_new_session=os.name != "nt")
    try:
        on_start(process.pid)
    except BaseException:
        checks.stop_process_tree(process)
        raise
    def enqueue(item):
        while not stopping.is_set():
            try:
                events.put(item, timeout=0.2)
                return
            except queue.Full:
                pass
    def reader(stream, name):
        try:
            for line in iter(lambda: stream.readline(65536), b""):
                enqueue((name, line))
                if stopping.is_set():
                    break
        finally:
            stream.close()
            enqueue((name, None))
    readers = [threading.Thread(target=reader, args=(process.stdout, "stdout"), daemon=True),
               threading.Thread(target=reader, args=(process.stderr, "stderr"), daemon=True)]
    for thread in readers:
        thread.start()
    def send_prompt():
        try:
            process.stdin.write(prompt.encode("utf-8"))
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass
    writer = threading.Thread(target=send_prompt, daemon=True)
    writer.start()
    size, ended = 0, set()
    try:
        with (output / "events.jsonl").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
            while True:
                for _ in range(500):
                    try:
                        name, data = events.get_nowait()
                    except queue.Empty:
                        break
                    if data is None:
                        ended.add(name)
                        continue
                    size += len(data)
                    if size <= config["limits"]["max_output_bytes"]:
                        (stdout if name == "stdout" else stderr).write(data)
                    if name == "stdout":
                        record["event_count"] += 1
                        try:
                            event = json.loads(data)
                            if policy == 'read-tools':
                                for tool in completed_read_tools(event):
                                    if tool not in seen_tools and len(seen_tools) < 5000:
                                        seen_tools.add(tool)
                                        last_change = time.monotonic()
                                        record['successful_tool_observations'] += 1
                            metadata = normalize(event)
                            record["permission_denials"] += int(metadata.pop("permission_denied", False))
                            event_error = metadata.pop("harness_error", False)
                            record["harness_error"] = record["harness_error"] or event_error
                            record.update({k: v for k, v in metadata.items() if v is not None})
                        except (ValueError, TypeError, AttributeError):
                            pass
                current = snapshot()
                # Expose diagnostics during a long attempt, before process exit.
                stdout.flush()
                stderr.flush()
                elapsed = time.monotonic() - start
                if current != observed:
                    last_change = time.monotonic()
                    observed = current
                    record["workspace_change_observations"] += 1
                    if record["first_artifact_seconds"] is None:
                        record["first_artifact_seconds"] = round(elapsed, 3)
                if cancel_path.exists():
                    record["reason"] = "cancelled"
                elif elapsed >= limit:
                    record["reason"] = "time_limit"
                elif size > config["limits"]["max_output_bytes"]:
                    record["reason"] = "output_limit"
                elif process.poll() is None and time.monotonic() - last_change >= config["limits"]["no_progress_seconds"]:
                    record["reason"] = "no_progress"
                if record["reason"] != "exited":
                    checks.stop_process_tree(process)
                    break
                if process.poll() is not None and len(ended) == 2 and events.empty():
                    break
                time.sleep(0.2)
    except BaseException:
        checks.stop_process_tree(process)
        raise
    finally:
        if process.poll() is None:
            checks.stop_process_tree(process)
        stopping.set()
        for thread in readers:
            thread.join(timeout=2)
        writer.join(timeout=2)
    record.update(exit_code=process.returncode, duration_seconds=round(time.monotonic() - start, 3),
                  artifact_change_is_not_acceptance=True, output_bytes=size)
    (output / "process.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record
