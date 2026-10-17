#!/usr/bin/env python3
"""One portable entry point for prepared development runs and their control plane."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import time

import check_night_run as checks
import workflow_coordinator as coord
import workflow_human as human
import workflow_memory as memory
import workflow_runner as serial
import workflow_onboarding as onboarding
from workflow_bus import Bus


def initialize(plan_path, runtime_path, root, mode=None):
    plan = checks.read_json(plan_path)
    configs = checks.read_json(runtime_path)
    checks.require(plan.get("kind") == "coordinated", "new runs use the coordinated protocol; resume frozen serial runs via their original entry")
    chosen = mode or plan.get("execution_mode") or ("full" if len(plan["tasks"]) > 1 or plan.get("replanning") else "quick")
    checks.require(chosen in {"quick", "full"}, "unknown workflow mode")
    if chosen == "quick":
        checks.require(len(plan["tasks"]) == 1 and not plan.get("replanning"), "quick mode needs one task without dynamic decomposition; use full")
        plan["limits"]["parallelism"] = 1
    else:
        checks.require(configs.get("reviewer"), "full mode requires an independent reviewer configuration")
    plan["execution_mode"] = chosen
    coord.validate(plan)
    # The user's input remains intact. The compiled mode choice is itself pinned.
    prepared = root.parent / (root.name + "-prepared-" + human.digest(plan)[:12] + ".json")
    if prepared.exists():
        checks.require(checks.read_json(prepared) == plan, "prepared plan collision")
    else:
        memory.atomic_json(prepared, plan)
    return coord.initialize(prepared, runtime_path, root)


def snapshot(root, *, state=None):
    bus = Bus(root)
    state = bus.state() if state is None else state
    plan, tasks, _ = coord.load(root, state=state)
    used = state["elapsed_seconds"] + (max(0, time.time() - state["session_started"]) if state.get("session_started") else 0)
    requests = human.tickets(root, plan, tasks, state, bus)
    cancelled = (root / "cancel.request").exists()
    lock = root / "runner.lock"
    live = bool(lock.exists() and serial.process_alive(checks.read_json(lock)["pid"]))
    phase = "running" if live and state.get("session_started") and state["status"] != "completed" else state["status"]
    return {"protocol_version": 1, "run_id": state["run_id"], "mode": plan.get("execution_mode", "coordinated-legacy"),
            "status": state["status"], "phase": "cancelled" if cancelled else ("waiting_human" if requests else phase),
            "revision": len(state.get("plan_revisions", [])), "task_counts": dict(Counter(e["status"] for e in state["tasks"].values())),
            "tasks": {t: {k: e.get(k) for k in ("status", "attempts", "reason", "commit")} for t, e in state["tasks"].items()},
            "budget": {"elapsed_seconds": used, "remaining_seconds": max(0, plan["limits"]["max_run_seconds"] - used),
                       "max_attempts_per_task": plan["limits"]["max_attempts"], "review_rounds_used": state.get("review_rounds", 0)},
            "human_requests": requests, "branch": state["integration_branch"], "head": state["integration_head"],
            "remote_git": coord.remote_git.summary(state), "remote_hold": state.get('remote_hold'),
            "quota_wait": state.get('quota_wait'), "quota_wait_seconds": state.get('quota_wait_seconds', 0),
            "delivery_path": str(root / "delivery.json"), "cancelled": cancelled,
            "next_action": "respond" if requests else ("wait_for_quota_retry" if state.get('quota_wait') else "observe_running" if live else "inspect_and_recover" if state.get("session_started") else "review_delivery" if state["status"] == "completed" else "run_or_inspect_blocker")}


def retry_review(root, tid):
    with serial.run_lock(root):
        plan, tasks, configs = coord.load(root)
        bus, state = Bus(root), Bus(root).state()
        checks.require(not state.get("session_started") and configs.get("reviewer"), "review retry requires a stopped review-enabled run")
        entry = state["tasks"][tid]
        checks.require(entry["status"] == "blocked" and entry["reason"].startswith("Independent review unavailable:") and entry.get("commit"), "task is not waiting for review recovery")
        entry.update(status=coord.committed_status(tasks[tid], entry), reason="Retry independent review after environment repair")
        bus.save(state, "review_retry_requested", task=tid)


def legacy(root, command):
    plan = checks.read_json(root / "plan.json")
    checks.require(plan.get("kind") != "coordinated", "not a serial snapshot")
    saved = plan.get("skill_versions", {}).get("dev-workflow", {})
    checks.require(saved.get("path"), "legacy plan does not identify a frozen dev-workflow Skill; use its original runner")
    script = Path(saved["path"]) / "scripts/workflow_runner.py"
    checks.require(script.is_file(), "original frozen runner is unavailable; restore the recorded Skill")
    checks.require(command in {"run", "status", "cancel", "clear-cancel"}, "legacy operation must use the original frozen runner CLI")
    # That runner validates its own historical pins and protocol. Never migrate them here.
    return subprocess.call([sys.executable, "-B", str(script), command, "--run-dir", str(root)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    onboarding.add_commands(commands)
    metric_parser = commands.add_parser("metrics")
    metric_parser.add_argument("--run-dir", required=True, type=Path)
    for name in ("init", "run", "status", "report", "requests", "respond", "replan", "recover", "cancel", "clear-cancel", "retry-review"):
        p = commands.add_parser(name)
        p.add_argument("--run-dir", required=True, type=Path)
        if name == "init":
            p.add_argument("--plan", required=True, type=Path)
            p.add_argument("--runtime", required=True, type=Path)
            p.add_argument("--mode", choices=["quick", "full"])
        if name in {"respond", "replan"}: p.add_argument("--input", required=True, type=Path)
        if name == "retry-review": p.add_argument("--task", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in {"doctor", "setup", "self-test"}:
            return onboarding.dispatch(args)
        if args.command == "metrics":
            import workflow_metrics
            print(json.dumps(workflow_metrics.collect(args.run_dir), ensure_ascii=False, indent=2))
            return 0
        root = args.run_dir.resolve()
        if args.command == "init":
            initialize(args.plan.resolve(), args.runtime.resolve(), root, args.mode)
        elif checks.read_json(root / "plan.json").get("kind") != "coordinated":
            return legacy(root, args.command)
        elif args.command == "status":
            print(json.dumps(snapshot(root), ensure_ascii=False, indent=2))
        elif args.command == "report":
            coord.load(root)
            print(json.dumps(checks.read_json(root / "delivery.json"), ensure_ascii=False, indent=2))
        elif args.command == "retry-review":
            retry_review(root, args.task)
            return coord.run(root)
        else:
            command = "human-requests" if args.command == "requests" else args.command
            items = [command, "--run-dir", str(root)]
            if args.command in {"respond", "replan"}: items += ["--response" if args.command == "respond" else "--proposal", str(args.input)]
            return coord.main(items)
        return 0
    except (checks.Rejected, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__": raise SystemExit(main())
