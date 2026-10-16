"""Tool-neutral task briefs and bounded harness processes. Python standard library."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

import check_night_run as checks
import workflow_locale as locale
import workflow_providers as providers


def now():
    return datetime.now(timezone.utc).isoformat()


def resolve_program(program):
    # A venv's Python may be a symlink. Its invocation path selects pyvenv.cfg;
    # dereferencing it launches the system environment even with identical bytes.
    found = str(Path(program).absolute()) if Path(program).is_absolute() else shutil.which(program)
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
    for field in ("max_budget_usd", "budget_reference_usd"):
        if field in harness:
            value = harness[field]
            checks.require(type(value) in (int, float) and math.isfinite(value) and value > 0,
                           "harness." + field + " must be a finite positive number")
    if "budget_reference_usd" in harness:
        checks.require(harness.get("max_budget_usd", 0) >= harness["budget_reference_usd"],
                       "max_budget_usd is below the supplied measured budget reference")
    for path in config.get("additional_directories", []):
        checks.require(isinstance(path, str) and Path(path).is_absolute() and Path(path).is_dir(),
                       "additional_directories must contain existing absolute directories")
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
    providers.validate(config, validate_config)
    return config


def preflight(config, repo, remaining=None):
    started = time.monotonic()
    def deadline(seconds):
        if remaining is None:
            return seconds
        left = remaining - (time.monotonic() - started)
        checks.require(left > 0, 'preflight run budget exhausted')
        return min(seconds, left)
    program = resolve_program(config["harness"]["executable"])
    before = checks.digest(Path(program))
    try:
        version = subprocess.run([program, "--version"], cwd=repo, capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=deadline(15),
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
            result = subprocess.run(argv, cwd=repo, capture_output=True, timeout=deadline(probe["timeout_seconds"]),
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
    for path in config.get("additional_directories", []):
        argv.extend(["--add-dir", path])
    tools = ["Read", "Glob", "Grep", "Bash", "Skill"] + ([] if readonly else ["Write", "Edit"])
    allowed = [t for t in tools if t != "Bash"] + allowed_commands(config)
    return [*argv, "--tools", ",".join(tools), "--allowedTools", *allowed]


def allowed_commands(config):
    return config["harness"].get("allowed_commands", ["Bash(python *)", "Bash(python3 *)",
        "Bash(git status *)", "Bash(git diff *)", "Bash(ls *)", "Bash(pwd)", "Bash(echo *)"])


def command_guidance(config, repo):
    if config["harness"]["kind"] != "claude":
        return ""
    return ("\nController runtime: the process working directory is " + str(repo) +
        ". Bash permission patterns are " + json.dumps(allowed_commands(config)) +
        ". Use Read/Glob/Grep with absolute paths in authorized directories. "
        "For Bash, use one single-line direct allowed command per call (no literal newlines even inside quoted arguments); do not prepend cd, environment assignments, "
        "shell wrappers or pipelines. They require separate permissions. Use the supplied interpreter path "
        "and Python's sys.path for project-local imports in supplementary probes. Never widen permissions "
        "or retry a denied form; record a genuinely unavailable check.\n")


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


def failure_hint(message):
    """Stable stop reasons only; raw credential/error content is not returned."""
    message = message.casefold()
    if any(term in message for term in ('invalid api key', 'authentication_error', 'not logged in', 'login required')):
        return 'authentication'
    if any(term in message for term in ('usage limit', 'hit your limit', 'insufficient_quota', 'quota exceeded', 'quota has been exceeded', 'quota exhausted', 'quota is exhausted', 'quota limit reached', 'quotalimitexceeded', 'exceeded your current quota', 'credit balance is too low', '额度已用完', '额度耗尽', '配额已耗尽')):
        return 'account_quota'
    if any(term in message for term in ('rate_limit_error', 'rate_limit_exceeded', 'rate limit reached', 'rate limit exceeded', 'too many requests')):
        return 'rate_limit'
    if 'model' in message and any(term in message for term in ('not supported', 'model_not_found', 'does not exist', 'not available')):
        return 'model_unavailable'
    if any(term in message for term in ('permission denied', 'access is denied', 'access denied')):
        return 'permission'
    return None


def normalize(event):
    """Return observable metadata only. Thinking/token streams are liveness, not progress."""
    result = {}
    if event.get("type") == "system" and event.get("subtype") == "init":
        result["reported_model"] = event.get("model")
    if event.get("type") == "result":
        result.update(reported_cost_usd=event.get("total_cost_usd"), harness_error=bool(event.get("is_error")))
        if event.get("subtype") == "error_max_budget_usd":
            result.update(harness_error=True, environment_failure="budget_exhausted", budget_exhausted=True)
    if event.get("type") in {"error", "turn.failed"}:
        result["harness_error"] = True
    if result.get("harness_error") and not result.get('budget_exhausted'):
        hint = failure_hint(json.dumps(event, ensure_ascii=False))
        if hint and hint != 'permission': result['environment_failure'] = hint
        elif hint == 'permission': result['permission_denied'] = True
        delay = providers.retry_after(event)
        if delay: result['retry_after_seconds'] = delay
        message = event.get('message')
        if (event.get('type') == 'error' and isinstance(message, str) and
                message.startswith('Reconnecting...') and 'stream disconnected before completion' in message and
                not result.get('environment_failure') and not result.get('permission_denied')):
            result['retrying_stream'] = True
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


def run_worker_once(config, repo, skill_root, prompt, output, snapshot, remaining, cancel_path, on_start=lambda pid: None):
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
    terminal_error, pending_stream_error = False, False
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
    prompt = command_guidance(config, repo) + prompt
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
                    if name == 'stderr':
                        diagnostic = data.decode('utf-8', errors='replace')
                        hint = failure_hint(diagnostic)
                        if hint: record.setdefault('stderr_failure_hint', hint)
                        if providers.enabled(config) and hint in providers.QUOTA_REASONS and any(term in diagnostic.casefold() for term in ('error','reached','exceeded','exhausted','too many')):
                            record['environment_failure'] = hint
                        delay = providers.retry_after(data.decode('utf-8', errors='replace'))
                        if delay: record['retry_after_seconds'] = delay
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
                            retrying = metadata.pop('retrying_stream', False)
                            if event_error:
                                if retrying:
                                    pending_stream_error = True
                                    record['stream_retry_events'] = record.get('stream_retry_events', 0) + 1
                                else: terminal_error = True
                            usage = event.get('usage')
                            if (event.get('type') == 'turn.completed' and isinstance(usage, dict) and
                                    all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('input_tokens', 'output_tokens'))):
                                if pending_stream_error: record['recovered_stream_retry_events'] = record.get('stream_retry_events', 0)
                                pending_stream_error = False
                            record['harness_error'] = terminal_error or pending_stream_error
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
                elif providers.enabled(config) and record.get('environment_failure') in providers.QUOTA_REASONS:
                    record['reason'] = 'provider_quota'
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
    # Native CLIs can report startup/account failures only on stderr. Classify
    # failed invocations conservatively; successful tool warnings are not failure.
    hint = record.get('stderr_failure_hint')
    if hint and (process.returncode != 0 or record['harness_error']):
        if hint == 'permission': record['permission_denials'] += 1
        else: record['environment_failure'] = hint
    (output / "process.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


def run_worker(config, repo, skill_root, prompt, output, snapshot, remaining, cancel_path, on_start=lambda pid: None):
    if not providers.enabled(config):
        return run_worker_once(config, repo, skill_root, prompt, output, snapshot, remaining, cancel_path, on_start)
    output.mkdir(parents=True, exist_ok=False)
    cooldowns = providers.Cooldowns(config.get('_provider_state', output.parent/'provider-cooldowns.sqlite3'))
    started = time.monotonic()
    total = min(remaining, config['limits']['attempt_seconds'])
    attempts, waits = [], []
    result = {'reason': 'waiting_for_quota', 'exit_code': 75, 'harness_error': True,
              'environment_failure': 'account_quota', 'permission_denials': 0}
    for ident, candidate in providers.candidates(config):
        if cancel_path.exists():
            result = {'reason':'cancelled','exit_code':75,'harness_error':True,'permission_denials':0};break
        left = total - (time.monotonic() - started)
        if left <= 0:
            result = {'reason':'time_limit','exit_code':75,'harness_error':True,'permission_denials':0};break
        due = cooldowns.due(ident)
        if due > time.time(): waits.append(due);continue
        # All alternates are fixed in runtime.json. Keep the original artifact,
        # tools and time contract; never retry a generic code/auth/permission error.
        candidate['_provider_state'] = str(cooldowns.path)
        attempt_path = output / 'providers' / str(len(attempts) + 1)
        continued = prompt + ('\nA previous provider hit its account quota. Inspect and preserve existing partial changes within the same task/scope; complete the original fixed checks and mailbox protocol. Use new unique outbox message IDs.\n' if attempts else '')
        result = run_worker_once(candidate, repo, skill_root, continued, attempt_path, snapshot, left, cancel_path, on_start)
        result.update(provider_id=ident, harness=candidate['harness']['kind'])
        (attempt_path/'process.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        attempts.append({'provider_id':ident,'artifact':str(attempt_path/'process.json'),'reason':result['reason'],
                         'environment_failure':result.get('environment_failure')})
        if result.get('environment_failure') not in providers.QUOTA_REASONS:
            if result['reason']=='exited' and result['exit_code']==0 and not result['harness_error']: cooldowns.success(ident)
            break
        waits.append(cooldowns.limited(ident, config['quota_recovery'], result.get('retry_after_seconds')))
    if result.get('environment_failure') in providers.QUOTA_REASONS and waits:
        result = {**result,'reason':'waiting_for_quota','quota_retry_at':min(waits)}
    result = {**result,'duration_seconds':round(time.monotonic()-started,3),'provider_attempts':attempts,
              'artifact_change_is_not_acceptance':True}
    (output/'process.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return result
