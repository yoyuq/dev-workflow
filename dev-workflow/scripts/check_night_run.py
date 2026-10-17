#!/usr/bin/env python3
"""Deterministic preflight and delivery checks. Python 3.10+, Git, no pip deps.

Run from a trusted controller, outside the coding agent's writable environment.
Only verification argv in a pinned plan is executed; report text is never code.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone


class Rejected(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Rejected(message)


def text(value, label):
    require(isinstance(value, str) and bool(value.strip()) and "\0" not in value,
            f"{label}: expected a nonempty string")
    return value


def obj(value, label):
    require(isinstance(value, dict), f"{label}: expected an object")
    return value


def array(value, label, nonempty=False):
    require(isinstance(value, list), f"{label}: expected an array")
    require(not nonempty or bool(value), f"{label}: must not be empty")
    return value


def strings(value, label, nonempty=False):
    for item in array(value, label, nonempty):
        text(item, label)
    require(len(value) == len(set(value)), f"{label}: duplicate entries")
    return value


def integer(value, label, minimum=1):
    require(type(value) is int and value >= minimum,
            f"{label}: expected an integer >= {minimum}")
    return value


def identifier(value, label):
    text(value, label)
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value),
            f"{label}: invalid identifier")
    return value


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def skill_digest(root):
    root = root.resolve(strict=True)
    require((root / "SKILL.md").is_file(), f"missing SKILL.md: {root}")
    h = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if any(p in {".git", "__pycache__"} for p in relative.parts):
            continue
        require(not path.is_symlink(), f"skill symlink is unsupported: {path}")
        if path.is_file() and path.suffix not in {".pyc", ".pyo"}:
            h.update(relative.as_posix().encode("utf-8") + b"\0")
            h.update(bytes.fromhex(digest(path)))
    return h.hexdigest()


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def invalid_constant(value):
    raise Rejected(f"invalid JSON constant: {value}")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=no_duplicate_keys,
                      parse_constant=invalid_constant)


def relative_path(value, label, allow_dot=False):
    text(value, label)
    p = PurePosixPath(value)
    require("\\" not in value and ":" not in value and not p.is_absolute()
            and ".." not in p.parts and ".git" not in p.parts
            and not any(c in value for c in "*?[]\r\n"),
            f"{label}: use a literal repository-relative path with / separators")
    require(allow_dot or p.as_posix() != ".", f"{label}: repository-wide scope is unsupported")
    return p.as_posix()


def inside(path, root):
    return path == root or root in path.parents


def validate_modules(plan, preparation=False):
    document = obj(plan.get("execution_plan"), "execution_plan")
    if not preparation:
        text(document.get("path"), "execution_plan.path")
        require(Path(document["path"]).is_absolute(), "execution_plan.path must be absolute")
        require(isinstance(document.get("sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", document["sha256"]),
                "execution_plan.sha256 must pin the written plan document")
    modules = {}
    for module in array(plan.get("modules"), "modules", True):
        obj(module, "module")
        mid = identifier(module.get("id"), "module.id")
        require(mid not in modules, f"duplicate module: {mid}")
        for key in ("name", "responsibility"):
            text(module.get(key), f"{mid}.{key}")
        strings(module.get("inputs"), f"{mid}.inputs")
        strings(module.get("outputs"), f"{mid}.outputs", True)
        strings(module.get("depends_on"), f"{mid}.depends_on")
        modules[mid] = module
    visited, visiting = set(), set()

    def visit(mid):
        require(mid not in visiting, f"module dependency cycle at {mid}")
        if mid in visited:
            return
        visiting.add(mid)
        for dep in modules[mid]["depends_on"]:
            require(dep in modules, f"{mid}: unknown module dependency {dep}")
            visit(dep)
        visiting.remove(mid)
        visited.add(mid)

    for mid in modules:
        visit(mid)
    return modules


def check_execution_document(plan, repo):
    pin = plan["execution_plan"]
    path = Path(pin["path"]).resolve(strict=True)
    require(path.is_file(), "execution_plan.path must name a file")
    require(not inside(path, repo), "keep the execution plan snapshot outside the AI checkout")
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == pin["sha256"], "execution plan document SHA-256 mismatch")
    content = raw.decode("utf-8-sig").replace("\r\n", "\n")
    require(bool(content.strip()), "execution plan document is empty")
    require(content == render_execution_plan(plan),
            "execution plan content differs from prepared task data; regenerate before pinning")
    return {"path": str(path), "sha256": pin["sha256"]}


def check_requirements_alignment(plan):
    """Check the pinned record, not the authenticity of a claimed user reply."""
    record = obj(plan.get("requirements_alignment"), "requirements_alignment")
    require(record.get("method") == "grill-me", "requirements_alignment.method must be grill-me")
    require(record.get("status") == "confirmed", "requirements_alignment must be confirmed before execution")
    require(record.get("mode") in {"interview", "reuse", "user_requested_skip"},
            "requirements_alignment.mode is unsupported")
    for key in ("goal", "scope", "acceptance", "autonomy"):
        text(record.get(key), f"requirements_alignment.{key}")
    confirmation = obj(record.get("user_confirmation"), "requirements_alignment.user_confirmation")
    for key in ("reference", "quote"):
        text(confirmation.get(key), f"requirements_alignment.user_confirmation.{key}")
    questions = array(record.get("open_questions"), "requirements_alignment.open_questions")
    require(not questions, "requirements_alignment has unresolved questions; return to grill-me")
    if record["mode"] == "reuse":
        text(record.get("reused_from"), "requirements_alignment.reused_from")
    return record


def validate_plan(plan, preparation=False):
    obj(plan, "plan")
    require(type(plan.get("format_version")) is int and plan["format_version"] == 4,
            "plan: format_version must be 4 (acceptance coverage and plan consistency required)")
    require(plan.get("status") in (("draft", "ready") if preparation else ("ready",)) and plan.get("mode") == "execute",
            "plan must have status=ready and mode=execute")
    identifier(plan.get("run_id"), "run_id")
    require(plan.get("delivery") == "branch_commit_and_report", "unsupported delivery mode")
    for key in ("decision_record", "trusted_authorization_ref"):
        text(plan.get(key), key)
    if not preparation:
        check_requirements_alignment(plan)
    modules = validate_modules(plan, preparation)
    requirements = {}
    for requirement in array(plan.get("requirements"), "requirements", True):
        obj(requirement, "requirement")
        rid = identifier(requirement.get("id"), "requirement.id")
        require(rid not in requirements, f"duplicate requirement: {rid}")
        text(requirement.get("description"), f"{rid}.description")
        requirements[rid] = requirement
    project = obj(plan.get("project"), "project")
    text(project.get("repository"), "project.repository")
    require(Path(project["repository"]).is_absolute(), "repository must be an absolute local path")
    for key in ("base_ref", "base_commit", "work_branch", "frontend_surface", "backend_stack"):
        text(project.get(key), f"project.{key}")
    require(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", project["base_commit"]),
            "base_commit must be a full Git object id")
    require(project["work_branch"] not in {"main", "master", "trunk", project["base_ref"],
                                           project["base_ref"].removeprefix("refs/heads/")},
            "work_branch must be a separate delivery branch")
    limits = obj(plan.get("limits"), "limits")
    for key in ("max_run_minutes", "max_tasks", "max_attempts_per_task"):
        integer(limits.get(key), f"limits.{key}")
    cost = limits.get("cost_limit")
    require(cost is None or (type(cost) in (int, float) and cost > 0),
            "cost_limit must be null or a positive number")
    skills = obj(plan.get("skill_versions"), "skill_versions")
    for name, pin in skills.items():
        identifier(name, "skill name")
        obj(pin, f"skill {name}")
        text(pin.get("path"), f"skill {name}.path")
        require(Path(pin["path"]).is_absolute(), f"skill {name}: path must be absolute")
        require(isinstance(pin.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]),
                f"skill {name}: invalid directory sha256")
    tasks = {}
    for task in array(plan.get("tasks"), "tasks", True):
        obj(task, "task")
        tid = identifier(task.get("id"), "task.id")
        require(tid not in tasks, f"duplicate task: {tid}")
        tasks[tid] = task
        text(task.get("goal"), f"{tid}.goal")
        for mid in strings(task.get("module_ids"), f"{tid}.module_ids", True):
            require(mid in modules, f"{tid}: unknown module {mid}")
        obj(task.get("module_dependencies"), f"{tid}.module_dependencies")
        require(task.get("status") in ("ready", "blocked"), f"{tid}: invalid starting status")
        strings(task.get("depends_on"), f"{tid}.depends_on")
        blockers = strings(task.get("blockers"), f"{tid}.blockers")
        require(bool(blockers) == (task["status"] == "blocked"), f"{tid}: blockers/status disagree")
        for scope in strings(task.get("allowed_scope"), f"{tid}.allowed_scope", True):
            relative_path(scope, f"{tid}.allowed_scope")
        for name in strings(task.get("required_skills"), f"{tid}.required_skills", True):
            require(name in skills, f"{tid}: unpinned required skill {name}")
        array(task.get("acceptance_criteria"), f"{tid}.acceptance_criteria", True)
        check_ids = set()
        for check in array(task.get("verification"), f"{tid}.verification"):
            obj(check, f"{tid}.verification entry")
            cid = identifier(check.get("id"), "verification.id")
            require(cid not in check_ids, f"{tid}: duplicate verification id {cid}")
            check_ids.add(cid)
            # Repeated argv elements are legitimate (e.g. repeated -k options).
            for arg in array(check.get("argv"), f"{tid}/{cid}.argv", True):
                require(isinstance(arg, str) and "\0" not in arg, "argv elements must be strings")
            text(check["argv"][0], "argv[0]")
            relative_path(check.get("cwd"), "verification.cwd", allow_dot=True)
            integer(check.get("timeout_seconds"), "verification.timeout_seconds")
        criterion_ids, used_checks = set(), set()
        for criterion in task["acceptance_criteria"]:
            obj(criterion, f"{tid}.acceptance_criteria entry")
            aid = identifier(criterion.get("id"), f"{tid}.criterion.id")
            require(aid not in criterion_ids, f"{tid}: duplicate acceptance criterion {aid}")
            criterion_ids.add(aid)
            text(criterion.get("description"), f"{tid}/{aid}.description")
            for rid in strings(criterion.get("requirement_ids"), f"{tid}/{aid}.requirement_ids", True):
                require(rid in requirements, f"{tid}/{aid}: unknown requirement {rid}")
            mode = criterion.get("mode")
            require(mode in ("automated", "manual"), f"{tid}/{aid}: invalid acceptance mode")
            links = strings(criterion.get("verification_ids"), f"{tid}/{aid}.verification_ids", mode == "automated")
            if mode == "manual":
                require(not links, f"{tid}/{aid}: manual criterion cannot be passed by commands")
                text(criterion.get("review_instructions"), f"{tid}/{aid}.review_instructions")
            for cid in links:
                require(cid in check_ids, f"{tid}/{aid}: unknown verification {cid}")
            used_checks.update(links)
        require(used_checks == check_ids, f"{tid}: every verification must map to an automated criterion")
    referenced_modules = {mid for task in tasks.values() for mid in task["module_ids"]}
    require(referenced_modules == modules.keys(), "every module must have at least one planned task")
    require(sum(t["status"] == "ready" for t in tasks.values()) <= limits["max_tasks"],
            "ready task count exceeds max_tasks; reduce this run's plan")
    require(any(t["status"] == "ready" for t in tasks.values()), "no ready tasks")
    visited, visiting = set(), set()

    def visit(tid):
        require(tid not in visiting, f"dependency cycle at {tid}")
        if tid in visited:
            return
        visiting.add(tid)
        for dep in tasks[tid]["depends_on"]:
            require(dep in tasks, f"{tid}: unknown dependency {dep}")
            require(tasks[tid]["status"] != "ready" or tasks[dep]["status"] == "ready",
                    f"{tid}: ready task depends on blocked task {dep}")
            visit(dep)
        visiting.remove(tid)
        visited.add(tid)

    for tid in tasks:
        visit(tid)
    covered = {rid for task in tasks.values() for criterion in task["acceptance_criteria"]
               for rid in criterion["requirement_ids"]}
    require(covered == requirements.keys(), "every requirement must have at least one acceptance criterion")
    for tid, task in tasks.items():
        expected = {dep for mid in task["module_ids"] for dep in modules[mid]["depends_on"]} - set(task["module_ids"])
        bindings = task["module_dependencies"]
        require(set(bindings) == expected, f"{tid}: module dependency bindings differ from module prerequisites")
        ancestors = task_ancestors(tid, tasks)
        for mid, producers in bindings.items():
            for producer in strings(producers, f"{tid}.module_dependencies.{mid}", True):
                require(producer in tasks and mid in tasks[producer]["module_ids"],
                        f"{tid}: producer {producer} does not belong to module {mid}")
                require(producer in ancestors,
                        f"{tid}: module prerequisite task {producer} is not a task dependency")
    return tasks


def task_ancestors(tid, tasks):
    found, pending = set(), list(tasks[tid]["depends_on"])
    while pending:
        dep = pending.pop()
        if dep not in found:
            found.add(dep)
            pending.extend(tasks[dep]["depends_on"])
    return found


def task_order(tasks):
    ordered, visited = [], set()

    def visit(tid):
        if tid not in visited:
            visited.add(tid)
            for dep in tasks[tid]["depends_on"]:
                visit(dep)
            ordered.append(tid)

    for tid in tasks:
        visit(tid)
    return ordered


def render_execution_plan(plan):
    """A single source for human-readable instructions and executable task data.

    Only status (draft -> ready) and the document's own pin are excluded.
    All other fields, including any future extension fields, are rendered.
    """
    lines = ["# 执行计划书", "", "本计划由准备数据生成。变更内容须在准备阶段修改数据并重新生成；运行中不能自行改写。", ""]

    def cell(value):
        if isinstance(value, list):
            return "、".join(cell(item) for item in value) or "无"
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("`", "&#96;").replace("\r", "").replace("\n", "<br>")

    lines.extend(["## 目标需求", ""])
    for requirement in plan["requirements"]:
        lines.append(f"- {cell(requirement['id'])}：{cell(requirement['description'])}")
    alignment = plan.get("requirements_alignment", {})
    if alignment:
        lines.extend(["", "## grill-me 需求对齐", "",
                      "- 状态：" + cell(alignment.get("status", "draft")),
                      "- 用户确认来源：" + cell(alignment.get("user_confirmation", {}).get("reference", "待确认")),
                      "- 用户原话：" + cell(alignment.get("user_confirmation", {}).get("quote", "待确认"))])
    lines.extend(["", "## 模块拆解", "", "| 模块 | 职责 | 输入 | 输出 | 依赖模块 |",
                  "| --- | --- | --- | --- | --- |"])
    for module in plan["modules"]:
        lines.append("| " + " | ".join(cell(value) for value in (
            module["id"] + " · " + module["name"], module["responsibility"],
            module["inputs"], module["outputs"], module["depends_on"])) + " |")
    tasks = {task["id"]: task for task in plan["tasks"]}
    lines.extend(["", "## 执行顺序", "", " → ".join(task_order(tasks)), ""])
    for tid in task_order(tasks):
        task = tasks[tid]
        lines.extend(["### " + tid + " · " + cell(task["goal"]), "",
                      "- 所属模块：" + cell(task["module_ids"]),
                      "- 前置任务：" + cell(task["depends_on"]),
                      "- 改动范围：" + cell(task["allowed_scope"]),
                      "- 指定技能：" + cell(task["required_skills"]),
                      "- 初始状态：" + cell(task["status"]) + "；阻塞项：" + cell(task["blockers"])])
        for mid, providers in sorted(task["module_dependencies"].items()):
            lines.append("- 模块 " + cell(mid) + " 的前置产物由任务提供：" + cell(providers))
        lines.extend(["", "| 验收条件 | 对应需求 | 验收方式 | 检查 / 人工操作 |",
                      "| --- | --- | --- | --- |"])
        for criterion in task["acceptance_criteria"]:
            operation = criterion["verification_ids"] if criterion["mode"] == "automated" else criterion["review_instructions"]
            lines.append("| " + " | ".join(cell(value) for value in (
                criterion["id"] + "：" + criterion["description"], criterion["requirement_ids"],
                "自动检查" if criterion["mode"] == "automated" else "人工待验收", operation)) + " |")
        lines.append("")

    def block(title, value):
        lines.extend([f"## {title}", "", "```json", json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), "```", ""])

    # The appendix preserves exact commands, scope and all preparation fields;
    # the checker compares the complete document, including the readable section.
    contract = {key: value for key, value in plan.items() if key not in {"status", "execution_plan"}}
    block("执行数据附录（由同一份准备数据生成）", contract)
    return "\n".join(lines)


def acceptance_evidence(plan, records, review_pending):
    by_check = {(row["task_id"], row["check_id"]): row for row in records}
    evidence = []
    for task in plan["tasks"]:
        for criterion in task["acceptance_criteria"]:
            checks = [by_check.get((task["id"], cid), {"check_id": cid, "result": "not_run"})
                      for cid in criterion["verification_ids"]]
            if criterion["mode"] == "manual":
                result = "awaiting_review" if task["id"] in review_pending else "not_run"
            elif any(check["result"] == "failed" for check in checks):
                result = "failed"
            else:
                result = "passed" if all(check["result"] == "passed" for check in checks) else "not_run"
            evidence.append({"task_id": task["id"], "criterion_id": criterion["id"],
                             "requirement_ids": criterion["requirement_ids"], "mode": criterion["mode"],
                             "result": result, "checks": checks,
                             "review_instructions": criterion.get("review_instructions", "")})
    return evidence


def git(repo, *args, binary=False):
    command = ["git", "-C", str(repo), *args]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=30, check=False)
    require(result.returncode == 0,
            f"Git check failed ({args[0]}): {result.stderr.decode('utf-8', errors='replace').strip()}")
    return result.stdout if binary else result.stdout.decode("utf-8", errors="replace").strip()


def git_state(plan, preflight=False):
    project = plan["project"]
    repo = Path(project["repository"]).resolve(strict=True)
    require(Path(git(repo, "rev-parse", "--show-toplevel")).resolve() == repo,
            "repository must be the Git workspace root")
    git(repo, "check-ref-format", "--branch", project["work_branch"])
    branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    require(branch == project["work_branch"], "wrong branch or detached HEAD")
    require(not git(repo, "status", "--porcelain=v1", "--untracked-files=all"),
            "workspace has uncommitted or untracked changes")
    head = git(repo, "rev-parse", "HEAD")
    git(repo, "cat-file", "-e", project["base_commit"] + "^{commit}")
    git(repo, "merge-base", "--is-ancestor", project["base_commit"], head)
    if preflight:
        require(head == project["base_commit"], "preflight HEAD differs from pinned base_commit")
    commit_range = project["base_commit"] + ".." + head
    require(not git(repo, "rev-list", "--min-parents=2", commit_range),
            "merge commits are unsupported by this sequential checker")
    commits = git(repo, "rev-list", "--reverse", commit_range).splitlines()
    changed = set()
    for commit in commits:
        raw = git(repo, "diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", "-z",
                  commit, binary=True)
        changed.update(p.decode("utf-8", errors="strict") for p in raw.split(b"\0") if p)
    return repo, {"branch": branch, "head": head, "commits": commits,
                  "changed_files": sorted(changed)}


def check_environment(plan, repo, task_ids):
    selected = [task for task in plan["tasks"] if task["id"] in task_ids]
    skills = {name for task in selected for name in task["required_skills"]}
    for name in sorted(skills):
        pin = plan["skill_versions"][name]
        require(skill_digest(Path(pin["path"])) == pin["sha256"],
                f"skill missing or changed: {name}")
    for task in selected:
        for check in task["verification"]:
            cwd = (repo / check["cwd"]).resolve(strict=True)
            require(cwd.is_dir() and inside(cwd, repo), "verification cwd escapes repository")
            executable = check["argv"][0]
            if Path(executable).is_absolute():
                target = Path(executable)
            else:
                require("/" not in executable and "\\" not in executable,
                        "argv[0] must be absolute or a program on PATH")
                found = shutil.which(executable)
                require(found is not None, f"verification program unavailable: {executable}")
                target = Path(found)
            require(target.is_file(), f"verification program unavailable: {executable}")
            require(target.suffix.lower() not in {".bat", ".cmd"},
                    "use an explicit interpreter for .bat/.cmd (e.g. node with npm-cli.js)")


def validate_report(report, plan, tasks, state):
    obj(report, "report")
    fields = {"run_id", "status", "branch", "commits", "completed_task_ids", "checks", "pending_tasks", "summary"}
    require(set(report) == fields, "report fields differ from night-result.schema.json")
    require(report["run_id"] == plan["run_id"], "report run_id differs from plan")
    require(report["status"] in {"completed", "partial", "blocked", "failed", "timeboxed"}, "invalid report status")
    require(report["branch"] == state["branch"], "report branch differs from actual Git branch")
    strings(report["commits"], "commits")
    require(report["commits"] == state["commits"], "report commits differ from actual base..HEAD history")
    text(report["summary"], "summary")
    done = set(strings(report["completed_task_ids"], "completed_task_ids"))
    require(done <= tasks.keys(), "report contains unknown completed task")
    pending, review_pending = set(), set()
    for entry in array(report["pending_tasks"], "pending_tasks"):
        obj(entry, "pending task")
        require(set(entry) == {"task_id", "status", "reason", "next_action"}, "invalid pending task fields")
        tid = text(entry["task_id"], "pending task_id")
        require(tid in tasks and tid not in pending and tid not in done, "unknown/duplicate/conflicting pending task")
        require(entry["status"] in {"pending", "blocked", "failed", "timeboxed", "awaiting_review"}, "invalid pending status")
        text(entry["reason"], "pending reason")
        text(entry["next_action"], "pending next_action")
        pending.add(tid)
        if entry["status"] == "awaiting_review":
            review_pending.add(tid)
    require(done | pending == tasks.keys(), "report must account for every planned task")
    require((report["status"] == "completed") == (not pending), "completed status disagrees with pending tasks")
    if report["status"] == "partial":
        require(bool(done | review_pending) and bool(pending), "partial requires delivered and pending tasks")
    candidates = done | review_pending
    require(not candidates or bool(state["commits"]), "completed tasks require actual branch commits")
    require(bool(candidates) or not state["commits"], "uncompleted work must not enter the delivery branch")
    checks = {}
    for entry in array(report["checks"], "checks"):
        obj(entry, "report check")
        require(set(entry) == {"task_id", "check_id", "action", "result", "evidence"}, "invalid report check fields")
        tid = text(entry["task_id"], "check task_id")
        cid = text(entry["check_id"], "check_id")
        require(tid in tasks and cid in {c["id"] for c in tasks[tid]["verification"]}, "unknown report check")
        require((tid, cid) not in checks, "duplicate report check")
        require(entry["result"] in {"passed", "failed", "not_run"}, "invalid check result")
        text(entry["action"], "check action")
        text(entry["evidence"], "check evidence")
        checks[tid, cid] = entry
    scopes = []
    for tid in candidates:
        require(tasks[tid]["status"] == "ready", f"{tid}: was not ready in pinned plan")
        require(set(tasks[tid]["depends_on"]) <= done, f"{tid}: dependency was not completed")
        manual = any(criterion["mode"] == "manual" for criterion in tasks[tid]["acceptance_criteria"])
        require(not manual or tid not in done, f"{tid}: manual acceptance must remain awaiting_review")
        require(manual or tid not in review_pending, f"{tid}: awaiting_review requires a manual criterion")
        scopes.extend(relative_path(s, "allowed_scope") for s in tasks[tid]["allowed_scope"])
        for check in tasks[tid]["verification"]:
            require(checks.get((tid, check["id"]), {}).get("result") == "passed",
                    f"{tid}/{check['id']}: missing passed check in report")
    for path in state["changed_files"]:
        require(any(path == scope or path.startswith(scope + "/") for scope in scopes),
                f"out-of-scope committed change: {path}")
    return done, review_pending


def stop_process_tree(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=15)


def run_check(task_id, check, repo, output, remaining):
    log_key = hashlib.sha256((task_id + "\0" + check["id"]).encode("utf-8")).hexdigest()
    log = output / f"check-{log_key}.log"
    start = time.monotonic()
    timeout = min(check["timeout_seconds"], remaining)
    record = {"task_id": task_id, "check_id": check["id"], "argv": check["argv"],
              "cwd": str(repo / check["cwd"]), "result": "failed", "exit_code": None,
              "timed_out": False, "log": log.name}
    with log.open("wb") as stream:
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        command = list(check["argv"])
        # Preserve the executable's invocation path (notably symlinked venv Python).
        command[0] = str(Path(command[0]).absolute()) if Path(command[0]).is_absolute() else shutil.which(command[0])
        require(command[0] is not None, "verification executable disappeared")
        process = subprocess.Popen(command, cwd=repo / check["cwd"], stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT, shell=False, **kwargs)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            record["timed_out"] = True
            stop_process_tree(process)
        except BaseException:
            stop_process_tree(process)
            raise
        record["exit_code"] = process.returncode
        if process.returncode == 0 and not record["timed_out"]:
            record["result"] = "passed"
    record.update(duration_seconds=round(time.monotonic() - start, 3), log_sha256=digest(log))
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    hashing = sub.add_parser("hash-skill", help="hash a complete installed skill directory")
    hashing.add_argument("path", type=Path)
    rendering = sub.add_parser("render-plan", help="generate the execution plan before the controller pins it")
    rendering.add_argument("--plan", type=Path, required=True)
    rendering.add_argument("--output", type=Path, required=True, help="new document outside the repository")
    for mode in ("preflight", "verify"):
        command = sub.add_parser(mode)
        command.add_argument("--plan", type=Path, required=True)
        command.add_argument("--plan-sha256", required=True, help="pinned by the external controller before AI execution")
        command.add_argument("--output-dir", type=Path, required=True, help="new directory outside the repository")
        if mode == "verify":
            command.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == "render-plan":
        try:
            plan = read_json(args.plan)
            validate_plan(plan, preparation=True)
            output = args.output.resolve()
            repo = Path(plan["project"]["repository"]).resolve()
            require(not inside(output, repo), "keep the execution plan snapshot outside the AI checkout")
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(render_execution_plan(plan))
            print(json.dumps({"path": str(output), "sha256": digest(output)}, ensure_ascii=False))
            return 0
        except (Rejected, OSError, ValueError, TypeError, RecursionError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if args.mode == "hash-skill":
        try:
            print(skill_digest(args.path))
            return 0
        except (Rejected, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    audit = {"format_version": 1, "phase": args.mode, "status": "failed", "completed": False,
             "started_at": datetime.now(timezone.utc).isoformat(), "errors": [], "checks": [],
             "limitations": ["Authorization, sandboxing, scheduling, AI cost and full-run limits are enforced by the external controller.",
                             "Passing commands does not prove acceptance criteria or visual quality; keep independent tests and human review."]}
    output = None
    validated_plan, review_pending = None, set()
    exit_code = 1
    start = time.monotonic()
    try:
        # Create evidence outside the checkout before checks; never overwrite an earlier run.
        plan_path = args.plan.resolve(strict=True)
        raw_plan = plan_path.read_bytes()
        require(hashlib.sha256(raw_plan).hexdigest() == args.plan_sha256, "plan SHA-256 mismatch")
        plan = json.loads(raw_plan.decode("utf-8-sig"), object_pairs_hook=no_duplicate_keys,
                          parse_constant=invalid_constant)
        tasks = validate_plan(plan)
        validated_plan = plan
        repo = Path(plan["project"]["repository"]).resolve(strict=True)
        require(not inside(plan_path, repo), "keep the trusted plan outside the AI checkout")
        target = args.output_dir.resolve()
        require(not inside(target, repo) and not inside(repo, target), "output directory must be separate from repository")
        target.mkdir(parents=True, exist_ok=False)
        output = target
        audit.update(run_id=plan["run_id"], plan_sha256=args.plan_sha256)
        repo, state = git_state(plan, preflight=args.mode == "preflight")
        audit["git"] = state
        audit["execution_plan"] = check_execution_document(plan, repo)
        if args.mode == "preflight":
            check_environment(plan, repo, {tid for tid, task in tasks.items() if task["status"] == "ready"})
            audit["status"] = "passed"
            exit_code = 0
        else:
            report = read_json(args.report)
            audit["report_sha256"] = digest(args.report)
            done, review_pending = validate_report(report, plan, tasks, state)
            candidates = done | review_pending
            check_environment(plan, repo, candidates)
            deadline = start + plan["limits"]["max_run_minutes"] * 60
            for tid in task_order(tasks):
                task = tasks[tid]
                if tid not in candidates:
                    continue
                for check in task["verification"]:
                    require(time.monotonic() < deadline, "verification time budget exhausted")
                    record = run_check(task["id"], check, repo, output, deadline - time.monotonic())
                    record["commit"] = state["head"]
                    audit["checks"].append(record)
                    require(record["result"] == "passed", f"verification failed: {task['id']}/{check['id']}")
            _, after = git_state(plan)
            require(after == state, "Git state changed while verifying")
            check_environment(plan, repo, candidates)
            check_execution_document(plan, repo)
            require(digest(plan_path) == args.plan_sha256, "plan changed while verifying")
            audit["completed_task_ids"] = sorted(done)
            audit["awaiting_review_task_ids"] = sorted(review_pending)
            audit["completed"] = report["status"] == "completed"
            audit["status"] = "passed" if audit["completed"] else "incomplete"
            exit_code = 0 if audit["completed"] else 3
    except (Rejected, OSError, ValueError, TypeError, RecursionError, subprocess.SubprocessError) as exc:
        audit["errors"].append(str(exc))
    except KeyboardInterrupt:
        audit["errors"].append("checker interrupted")
        exit_code = 130
    audit["duration_seconds"] = round(time.monotonic() - start, 3)
    if validated_plan is not None:
        audit["acceptance"] = acceptance_evidence(validated_plan, audit["checks"], review_pending)
    serialized = json.dumps(audit, ensure_ascii=False, indent=2)
    if output:
        try:
            (output / "audit.json").write_text(serialized + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"Cannot save audit: {exc}", file=sys.stderr)
            exit_code = 1
    print(serialized)
    return exit_code


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
