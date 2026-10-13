#!/usr/bin/env python3
"""Run reproducible controller scenarios or opt-in real harness trials in NEW isolated Git repos."""
import argparse
from pathlib import Path
import time

import benchmark_fixtures as fixtures
import check_night_run as checks
import workflow_memory as memory
import workflow_runner as runner


def execute(output, harness, executable=None, skill_root=None, selected=None, alignment=None):
    real = harness != "simulation"
    if real:
        checks.check_requirements_alignment({"requirements_alignment": alignment})
    output.mkdir(parents=True, exist_ok=False)
    scenarios = [("bugfix", "success"), ("fullstack", "success")] if real else [
        ("bugfix", "success"), ("fullstack", "success"), ("resume", "success"),
        ("environment", "success"), ("verification", "fail_then_fix"),
        ("false_done", "false_done"), ("thinking", "thinking"), ("manual", "success"), ("quota", "quota")]
    if selected:
        scenarios = [row for row in scenarios if row[0] in selected]
        checks.require(scenarios, "No matching benchmark cases")
    summaries = []
    for name, behavior in scenarios:
        started = time.monotonic()
        adapter = {"kind": harness, "executable": executable or harness, "effort": "low"} if real else None
        root, repo, plan, config = fixtures.prepare(output / name, name, behavior, skill_root if real else None, adapter, alignment)
        if name == "environment":
            config["harness"]["executable"] = str(root / "missing-executable")
        if name == "thinking":
            config["limits"]["no_progress_seconds"] = 2
            plan["limits"]["max_attempts_per_task"] = 1
        fixtures.repin(root, plan, config)
        run_dir = root / "run"
        runner.initialize(root / "plan.json", root / "runtime.json", run_dir)
        exit_code = runner.run(run_dir, max_tasks=1 if name == "resume" else None)
        if name == "resume":
            exit_code = runner.run(run_dir)
        state = memory.read_json(run_dir / "state.json")
        expected = {"environment": "blocked_environment", "quota": "blocked_environment", "false_done": "blocked", "thinking": "blocked", "manual": "awaiting_review"}.get(name, "completed")
        records = [h["process"] for entry in state["tasks"].values() for h in entry["history"]]
        first = [p["first_artifact_seconds"] for p in records if p["first_artifact_seconds"] is not None]
        row = {"case": name, "trial_type": "real_harness" if real else "scripted_controller_simulation", "harness": harness,
               "expected_status": expected, "observed_status": state["status"], "case_passed": state["status"] == expected,
               "exit_code": exit_code, "duration_seconds": round(time.monotonic() - started, 2),
               "first_artifact_seconds": min(first) if first else None,
               "attempts": {k: v["attempts"] for k, v in state["tasks"].items()},
               "reported_models": sorted({p["reported_model"] for p in records if p.get("reported_model")}),
               "reported_cost_usd": sum(p.get("reported_cost_usd") or 0 for p in records) if any(p.get("reported_cost_usd") is not None for p in records) else None,
               "run_dir": str(run_dir), "limitations": ["HTTP/DOM checks do not establish browser interaction or visual quality"] if name == "fullstack" else []}
        summaries.append(row)
        memory.atomic_json(output / "benchmark-results.json", {"format_version": 1, "cases": summaries,
            "planned_case_count": len(scenarios), "finished_case_count": len(summaries),
            "suite_finished": len(summaries) == len(scenarios),
            "all_expected_outcomes": len(summaries) == len(scenarios) and all(x["case_passed"] for x in summaries), "statistical_generalization": False})
        print(row, flush=True)
        if real and state["status"] == "blocked_environment":
            break
    return all(row["case_passed"] for row in summaries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory outside the skill and any production checkout")
    parser.add_argument("--harness", choices=["simulation", "codex", "claude"], default="simulation")
    parser.add_argument("--executable")
    parser.add_argument("--case", action="append")
    parser.add_argument("--alignment", type=Path, help="Confirmed grill-me record JSON, required for real harness runs")
    parser.add_argument("--skill-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        checks.require(not checks.inside(args.output.resolve(), args.skill_root.resolve()), "Keep benchmarks outside the skill")
        alignment = checks.read_json(args.alignment) if args.alignment else None
        return 0 if execute(args.output.resolve(), args.harness, args.executable, args.skill_root.resolve(), args.case, alignment) else 1
    except (checks.Rejected, OSError, ValueError, KeyError) as exc:
        print({"error": str(exc)}, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
