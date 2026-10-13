"""Operator control plane: version-bound tickets, durable responses, no budget resets.

This cooperative protocol cannot authenticate a human against a hostile same-user
process. Only a trusted host/operator may submit actual user responses here.
"""
import copy
import hashlib
import json
import time
from pathlib import Path

import check_night_run as checks
import workflow_memory as memory
from workflow_bus import Bus, encoded


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def versions(state):
    return {n: c["version"] for n, c in state["contracts"].items()}


def review_evidence(plan, task, state, head):
    # Include removals, modes and ignored-but-committed files. No workspace reads.
    tree = checks.git(Path(plan["repository"]), "--literal-pathspecs", "ls-tree", "-r", "-z", head, "--", *task["allowed_scope"], binary=True)
    return {"scope_sha256": hashlib.sha256(tree).hexdigest(), "contracts": versions(state)}


def review_valid(plan, task, state, head):
    if not task.get("manual_review"):
        return True
    approval = state["tasks"][task["id"]].get("human_review")
    return bool(approval and approval["decision"] == "approve" and approval["evidence"] == review_evidence(plan, task, state, head))


def tickets(root, plan, tasks, state, bus):
    result = []
    pin = checks.read_json(root / "pins.json")["plan"]
    for tid, task in tasks.items():
        entry = state["tasks"][tid]
        if entry["status"] in {"running", "creating", "committing"}:
            continue  # A running worker can still change the context or resolve a request.
        base = {"run_id": plan["run_id"], "plan_sha256": pin, "task_id": tid,
                "attempt": entry["attempts"], "commit": entry.get("commit"),
                "integration_head": state["integration_head"], "contracts": versions(state),
                "plan_revision": len(state.get("plan_revisions", []))}
        questions = [m for m in bus.unresolved() if m["sender"] == tid and m["recipient"] == "coordinator"]
        for question in questions:
            context = {**base, "kind": "answer", "request_id": question["id"]}
            result.append({"ticket_id": digest(context), "context": context,
                           "text": entry.get("needs_human") or question["payload"]["text"]})
        head = state["integration_head"] if entry["status"] == "integrated" else entry.get("review_head")
        if head and task.get("manual_review") and entry["status"] in {"awaiting_review", "verified", "integrated"} and not review_valid(plan, task, state, head):
            context = {**base, "kind": "review", "review_head": head,
                       "evidence": review_evidence(plan, task, state, head)}
            result.append({"ticket_id": digest(context), "context": context,
                           "text": task["manual_review"], "workspace": state.get("review_workspace") if entry["status"] == "integrated" else entry.get("candidate_workspace")})
    return result


def export(root, plan, tasks, state, bus):
    current = tickets(root, plan, tasks, state, bus)
    memory.atomic_json(root / "human-requests.json", current)
    return current


def submit(root, response):
    """Queue only. Never overwrite the controller's in-memory facts."""
    import workflow_coordinator as coord
    plan, tasks, _ = coord.load(root)
    checks.require(isinstance(response, dict) and set(response) == {"id", "ticket_id", "decision", "text", "reference", "quote"}, "response accepts only id, ticket_id, decision, text, reference and quote")
    checks.identifier(response["id"], "response.id")
    checks.require(response.get("decision") in {"answer", "approve", "reject"}, "invalid human decision")
    for field in ("ticket_id", "text", "reference", "quote"):
        checks.text(response.get(field), "response." + field)
        checks.require(len(response[field]) <= 8000, "response field too long")
    bus = Bus(root)
    payload = encoded(response)
    with bus.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT payload,status FROM human_responses WHERE id=?", (response["id"],)).fetchone()
        if old:
            checks.require(old["payload"] == payload, "response ID reused with different content")
            return old["status"]
        current = tickets(root, plan, tasks, bus.state(), bus)
        ticket = next((t for t in current if t["ticket_id"] == response["ticket_id"]), None)
        checks.require(ticket is not None, "stale or unknown human ticket; read human-requests again")
        checks.require((ticket["context"]["kind"] == "answer") == (response["decision"] == "answer"), "wrong decision for ticket")
        db.execute("INSERT INTO human_responses(id,payload,status) VALUES(?,?,'queued')", (response["id"], payload))
    return "queued"


def receipt(root, rid):
    with Bus(root).connection() as db:
        row = db.execute("SELECT id,status,result FROM human_responses WHERE id=?", (rid,)).fetchone()
        return dict(row) if row else None


def consume(root, plan, tasks, state, bus):
    """Called only by the lock-owning controller, between worker transitions."""
    with bus.connection() as db:
        queued = [dict(r) for r in db.execute("SELECT * FROM human_responses WHERE status='queued' ORDER BY seq")]
    for row in queued:
        previous = copy.deepcopy(state)
        try:
            import workflow_coordinator as coord
            coord.load(root)
            response = json.loads(row["payload"])
            ticket = next((t for t in tickets(root, plan, tasks, state, bus) if t["ticket_id"] == response["ticket_id"]), None)
            checks.require(ticket is not None, "stale human response; no changes applied")
            context, decision = ticket["context"], response["decision"]
            checks.require((context["kind"] == "answer") == (decision == "answer"), "wrong decision for ticket")
            tid = context["task_id"]
            entry = state["tasks"][tid]
            with bus.transaction():
                if decision == "answer":
                    mid = context["request_id"]
                    bus.ack("coordinator", [mid])
                    bus.resolve([mid])
                    bus.send(tid, "human_answer", {"request_id": mid, "text": response["text"],
                             "reference": response["reference"], "quote": response["quote"]}, required=True,
                             message_id="human-" + response["id"])
                    if not any(m["sender"] == tid for m in bus.unresolved()):
                        entry.pop("needs_human", None)
                        if entry.get("retry_gate"):
                            entry.setdefault("operator_retries", []).append({"gate": entry.pop("retry_gate"), "response": response})
                        entry.update(status="pending", reason="Human answered; consume the answer in the next bounded attempt",
                                     feedback={**entry.get("feedback", {}), "previous_workspace": entry.get("workspace"), "reason": response["text"]})
                    else:
                        entry.update(status="blocked", needs_human="Additional unanswered questions remain")
                elif decision == "approve":
                    entry["human_review"] = {**response, "context": context, "evidence": context["evidence"]}
                    if entry["status"] != "integrated":
                        entry.update(status="verified", reason="Human reviewed candidate; independent checks still required")
                else:
                    # A rejection requires implementation work, not just another signoff.
                    entry.pop("human_review", None)
                    entry.pop("review_head", None)
                    entry.update(status="pending", reason=response["text"],
                                 feedback={"previous_workspace": entry.get("workspace"), "reason": response["text"]})
                    coord_invalidate_dependents(tasks, state, tid)
                state.pop("final_checks", None)
                state.pop("review_workspace", None)
                state["status"] = "running"
                bus.save(state, "human_response_applied", response=response, ticket=ticket)
                with bus.connection() as db:
                    db.execute("UPDATE human_responses SET status='applied',result=? WHERE id=?", (encoded({"ticket_id": response["ticket_id"]}), row["id"]))
        except checks.Rejected as exc:
            state.clear()
            state.update(previous)
            with bus.connection() as db:
                db.execute("UPDATE human_responses SET status='rejected',result=? WHERE id=?", (str(exc), row["id"]))
            bus.event("human_response_rejected", response_id=row["id"], reason=str(exc))
        except BaseException:
            state.clear()
            state.update(previous)
            raise


def coord_invalidate_dependents(tasks, state, tid):
    affected = {tid}
    while True:
        more = {t for t, task in tasks.items() if set(task.get("depends_on", [])) & affected or
                set(task.get("requires", [])) & {m for a in affected for m in tasks[a].get("produces", [])}}
        if more <= affected:
            break
        affected |= more
    for name in affected:
        entry = state["tasks"][name]
        entry["invalidated"] = True
        entry.pop("human_review", None)
        entry.pop("review_head", None)
        if entry["status"] not in {"running", "creating", "committing"}:
            entry["status"] = "pending"
        for milestone in tasks[name].get("produces", []):
            state["milestones"].pop(milestone, None)


def respond(root, response):
    """Wait for receipt; if the runner stops before consuming, take over and resume.

    No background shell helper, no timer automation. Queue and SQLite receipt survive
    this command dying; a subsequent run drains them once. Cancel is never cleared.
    """
    import workflow_coordinator as coord
    import workflow_runner as serial
    submit(root, response)
    invalid_lock_since = None
    while True:
        result = receipt(root, response["id"])
        if result["status"] != "queued":
            # A prior responder can die after committing the receipt and before
            # launching work. Replaying the same response must still resume it.
            state = Bus(root).state()
            if result["status"] == "applied" and state["status"] != "completed" and not state["session_started"] and not (root / "runner.lock").exists() and not (root / "cancel.request").exists():
                plan, tasks, _ = coord.load(root)
                runnable = any((coord.ready(tid, tasks, state) and entry["attempts"] < plan["limits"]["max_attempts"]) or entry["status"] == "verified" or
                               (entry["status"] == "awaiting_review" and not entry.get("review_head") and not entry.get("needs_human"))
                               for tid, entry in state["tasks"].items())
                audit_pending = all(e["status"] == "integrated" for e in state["tasks"].values()) and not state.get("final_checks")
                if (runnable or audit_pending) and state["elapsed_seconds"] < plan["limits"]["max_run_seconds"]:
                    try:
                        coord.run(root)
                    except checks.Rejected:
                        if not (root / "runner.lock").exists():
                            raise
            return result
        lock = root / "runner.lock"
        if lock.exists():
            try:
                owner = checks.read_json(lock)
            except (OSError, ValueError):
                invalid_lock_since = invalid_lock_since or time.monotonic()
                checks.require(time.monotonic() - invalid_lock_since < 2, "unreadable runner lock; inspect controller state before recovery")
                time.sleep(.1)
                continue
            invalid_lock_since = None
            checks.require(serial.process_alive(owner["pid"]), "stale runner lock; recover before resuming queued response")
            time.sleep(.25)
            continue
        checks.require(not (root / "cancel.request").exists(), "response queued; cancel remains set, clear-cancel explicitly before run")
        try:
            coord.run(root)
        except checks.Rejected:
            if not lock.exists():
                raise
