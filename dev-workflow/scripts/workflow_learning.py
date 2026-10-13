"""Failure -> candidate -> independently supported lesson -> observed use feedback."""
import copy
from pathlib import Path
import uuid

import check_night_run as checks
import workflow_memory as memory
import workflow_runner as serial


def capture(root, config, tid, entry, reason, environment):
    location = serial.memory_location(root, config)
    key = "case-" + uuid.uuid4().hex[:20]
    folder = location / "evidence" / key
    folder.mkdir(parents=True)
    records = copy.deepcopy(entry.get("last_checks", []) + [r for r in entry.get("integration_checks", []) if r["task_id"] == tid and r["result"] == "failed"])
    logs = []
    for i, record in enumerate(records):
        path = Path(record["evidence_path"])
        if path.is_file():
            dest = folder / f"check-{i}.log"
            dest.write_bytes(path.read_bytes())
            logs.append(str(dest.relative_to(location)).replace("\\", "/"))
    evidence = {"task_id": tid, "attempt": entry["attempts"], "run_dir": str(root), "reason": reason,
                "checks": records, "process": entry.get("process", {}), "environment": environment,
                "diagnostic": entry.get("feedback", {}).get("diagnostic"),
                "worker_preflight": entry.get("worker_preflight"), "verification_handoff": entry.get("verification_handoff")}
    memory.atomic_json(folder / "failure.json", evidence)
    path = str((folder / "failure.json").relative_to(location)).replace("\\", "/")
    result = memory.lesson(location, {"id": key, "status": "unresolved", "summary": f"{tid}: {reason}",
        "context": "Observed failure; root cause awaits a specific diagnosis.", "symptom": reason,
        "attempts": [f"Attempt {entry['attempts']}: {reason}"], "lesson": "Inspect this failure before choosing a changed approach.",
        "tags": ["workflow-failure", environment["harness"]], "code_ref": entry.get("launch_base", "see preserved workspace"),
        "scope": {"kind": "environment", "match": {"harness": environment["harness"], "os": environment["os"], "harness_version": environment["version"]}},
        "evidence": [path, *logs]})
    entry.setdefault("failure_cases", []).append({"id": key, "memory_dir": str(location), "failure": evidence})
    return result


def read_proposals(entry):
    path = Path(entry["workspace"]) / ".workflow-mailbox" / "learning.json"
    if not path.exists(): return {"diagnoses": [], "feedback": []}
    checks.require(not path.is_symlink() and path.stat().st_size <= 64000, "invalid learning proposal file")
    data = checks.read_json(path)
    checks.require(set(data) <= {"diagnoses", "feedback"}, "unknown learning fields")
    checks.require(isinstance(data.get("diagnoses", []), list) and isinstance(data.get("feedback", []), list), "invalid learning proposal")
    known = {c["id"] for c in entry.get("failure_cases", [])}
    seen = set()
    for item in data.get("diagnoses", []):
        checks.require(item["case_id"] in known and item["case_id"] not in seen, "unknown or duplicate failure case")
        seen.add(item["case_id"])
        for field in ("root_cause", "fix", "prevention"): checks.text(item.get(field), "diagnosis." + field)
        checks.strings(item.get("check_ids"), "diagnosis.check_ids", nonempty=True)
    return data


def finish(root, config, plan, state, bus):
    for tid, entry in state["tasks"].items():
        if entry["status"] != "integrated": continue
        proposals = entry.get("learning_proposals", {})
        cases = {c["id"]: c for c in entry.get("failure_cases", [])}
        review = entry.get("ai_review", {})
        approvals = {a["case_id"] for a in review.get("lesson_assessments", []) if a.get("decision") == "supported"}
        for diagnosis in proposals.get("diagnoses", []):
            case = cases[diagnosis["case_id"]]
            location = memory.state_root(Path(case["memory_dir"]))
            current = next(r for r in memory.records(location) if r["id"] == case["id"])
            if current.get("status") in {"validated", "retired"} or current["needs_review"]: continue
            old_checks = {r["check_id"] for r in case["failure"]["checks"] if r["result"] == "failed"}
            new_checks = {r["check_id"] for r in state.get("final_checks", []) if r["task_id"] == tid and r["result"] == "passed"}
            promoted = (state["status"] == "completed" and bool(set(diagnosis["check_ids"]) & old_checks) and
                        set(diagnosis["check_ids"]) <= new_checks and review.get("decision") == "approve" and case["id"] in approvals)
            status = "validated" if promoted else "candidate"
            marker = f"{case['id']}:{entry.get('commit')}:{status}"
            if marker in entry.get("learning_saved", []): continue
            # Copy immutable before/after evidence into the transferable notebook.
            folder = location / "evidence" / case["id"]
            after = folder / ("fix-" + uuid.uuid4().hex[:8] + ".json")
            memory.atomic_json(after, {"diagnosis": diagnosis, "commit": entry.get("commit"), "final_checks": state.get("final_checks", []), "review": review})
            paths = [e["path"] for e in current["evidence"]] + [after.relative_to(location).as_posix()]
            for i, record in enumerate(state.get("final_checks", [])):
                if record["task_id"] != tid: continue
                target = folder / f"{after.stem}-check-{i}.log"
                target.write_bytes(Path(record["evidence_path"]).read_bytes())
                paths.append(target.relative_to(location).as_posix())
            if review.get("report_path"):
                original = Path(review["report_path"])
                checks.require(checks.digest(original) == review["report_sha256"], "lesson review evidence drift")
                target = folder / (after.stem + "-review.json")
                target.write_bytes(original.read_bytes())
                paths.append(target.relative_to(location).as_posix())
            scope = {"kind": "project", "match": {"project": config.get("memory_project") or plan["repository"],
                     "harness": state["environment"]["harness"], "os": state["environment"]["os"], "harness_version": state["environment"]["version"]}}
            memory.lesson(location, {"id": case["id"], "status": status, "summary": diagnosis["root_cause"],
                "context": f"Observed in {plan['repository']} task {tid}; do not generalize outside scope.", "symptom": case["failure"]["reason"],
                "lesson": diagnosis["prevention"], "attempts": [*current["attempts"], diagnosis["fix"]], "tags": current["tags"],
                "code_ref": entry["commit"], "scope": scope, "evidence": paths,
                "validation_note": "Failed external check, matching final passing check, and independent reviewer explicitly supports this diagnosis." if promoted else "Diagnosis remains a candidate; required verification is incomplete."})
            entry.setdefault("learning_saved", []).append(marker)
            bus.event("lesson_recorded", task=tid, lesson_id=case["id"], status=status)
        for feedback in proposals.get("feedback", []):
            fid = feedback.get("lesson_id")
            checks.require(fid in {l["id"] for l in entry.get("lessons", [])}, "feedback must cite an actually recalled lesson")
            marker = f"{entry['attempts']}:{fid}"
            if marker in entry.get("feedback_saved", []): continue
            location = serial.memory_location(root, config)
            path = location / "evidence" / ("use-" + uuid.uuid4().hex + ".json")
            memory.atomic_json(path, {"task": tid, "commit": entry["commit"], "feedback": feedback,
                "worker_summary_is_not_causality_proof": True, "checks": entry.get("last_checks", [])})
            memory.feedback(location, {"lesson_id": fid, "outcome": feedback["outcome"], "note": feedback["note"],
                "code_ref": entry["commit"], "evidence": [path.relative_to(location).as_posix()]})
            entry.setdefault("feedback_saved", []).append(marker)
    bus.save(state, "learning_checkpoint")
