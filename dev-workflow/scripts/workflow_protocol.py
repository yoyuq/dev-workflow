"""Observed worker capabilities, explicit verification handoff, and bounded retries.

Worker declarations are diagnostic data, never permission grants or test results.
"""
import hashlib
import json
import time

import check_night_run as checks


def validate_message(kind, recipient, data):
    if kind not in {"capabilities", "ready_for_verification"}:
        return
    checks.require(recipient == "coordinator" and isinstance(data, dict), "protocol message must address coordinator with object data")
    if kind == "capabilities":
        checks.require(data.get("transport") in {"files", "helper"}, "unknown mailbox transport")
        checks.require(data.get("local_checks") in {"available", "unavailable", "not_probed"}, "invalid local check capability")
        checks.require(isinstance(data.get("evidence"), str) and 0 < len(data["evidence"]) <= 2000, "capability needs bounded evidence or explicit not-probed explanation")
    else:
        checks.require(data.get("implementation") == "ready", "handoff requires explicit implementation readiness")
        checks.require(data.get("local_checks") in {"passed", "failed", "unavailable", "not_run"}, "invalid local check outcome")
        checks.require(isinstance(data.get("evidence"), str) and 0 < len(data["evidence"]) <= 2000, "handoff needs bounded evidence")


def preflight_limit(config):
    # Legacy command adapters opt in. Native workers always use the file protocol.
    default = 90 if config["harness"]["kind"] in {"codex", "claude"} else 0
    return min(config.get("worker_preflight_seconds", default), config["limits"]["attempt_seconds"])


def observe_preflight(bus, tid, entry, *, finished=False):
    probe = entry.get("worker_preflight", {})
    if not probe.get("required") or probe.get("confirmed_at"):
        return
    messages = bus.messages()
    declarations = [m for m in messages if m["sender"] == tid and m["kind"] == "capabilities"
                    and m["payload"].get("attempt") == entry["attempts"]]
    assignments = [m for m in messages if m["recipient"] == tid and m["kind"] == "assignment"
                   and m["payload"].get("attempt") == entry["attempts"]]
    if declarations and assignments and all(m["acked"] for m in assignments):
        # Receiving valid outbox JSON and an ack for this attempt establishes the
        # mailbox round trip. Shell/browser capability is only self-reported.
        declaration = declarations[-1]
        probe.update(confirmed_at=time.time(), message_id=declaration["id"],
                     observed={"outbox_json": True, "assignment_ack": True},
                     reported=declaration["payload"]["data"])
        bus.event("worker_preflight_confirmed", task=tid, attempt=entry["attempts"], evidence=probe)
        return
    checks.require(not finished and time.time() - probe["started_at"] < probe["timeout_seconds"],
                   "worker preflight failed: no capability declaration and acknowledged assignment")


def handoff(bus, tid, entry):
    declarations = [m for m in bus.messages("coordinator") if m["sender"] == tid
                    and m["kind"] == "ready_for_verification" and m["payload"].get("attempt") == entry["attempts"]]
    checks.require(not entry.get("worker_preflight", {}).get("required") or declarations,
                   "worker handoff missing: post ready_for_verification after implementation")
    if declarations:
        checks.require(not any(m["sender"] == tid for m in bus.unresolved()), "verification handoff has unresolved requests")
        entry["verification_handoff"] = {"message_id": declarations[-1]["id"],
                                          "reported": declarations[-1]["payload"]["data"]}
        bus.event("verification_handoff_received", task=tid, attempt=entry["attempts"], evidence=entry["verification_handoff"])


def failure_diagnosis(entry, reason):
    """Stable symptoms omit per-attempt paths, durations and process identifiers."""
    if "preflight" in reason or "acknowledge required" in reason or "worker mailbox" in reason:
        category, symptom = "protocol", "mailbox"
        strategy = "Use permitted file read/edit tools for inbox.json and unique outbox JSON; acknowledge actual message IDs before coding. Do not retry a denied Python helper."
    elif "handoff" in reason:
        category, symptom = "protocol", "verification-handoff"
        strategy = "Preserve and inspect the previous draft. If implementation is ready, report ready_for_verification and delegate fixed checks; blocked is only for an unresolved implementation prerequisite."
    elif reason.startswith("worker process failed:"):
        category, symptom = "runtime", entry.get("process", {}).get("reason", "unknown")
        strategy = "Inspect preserved changes and process evidence first. Limit context to the task and one relevant guide, implement one bounded change, then hand off. A timeout is not acceptance; do not change budgets or permissions."
    elif reason == "task verification failed":
        category = "verification"
        symptom = sorted(r["check_id"] for r in entry.get("last_checks", []) if r["result"] != "passed")
        strategy = "Read the failed named checks and reproduce the defect; change the implementation, then submit the same pinned checks."
    else:
        category, symptom = "execution", reason
        strategy = "Inspect the preserved diff and specific failure evidence; correct the cause within the existing scope before retrying."
    signature = hashlib.sha256(json.dumps([category, symptom], sort_keys=True).encode()).hexdigest()
    failures = entry.setdefault("failure_diagnostics", [])
    repeated = sum(f["signature"] == signature for f in failures) + 1
    diagnostic = {"attempt": entry["attempts"], "category": category, "symptom": symptom,
                  "signature": signature, "occurrences": repeated, "next_strategy": strategy,
                  "diagnosis_status": "observed_symptom_not_verified_root_cause"}
    failures.append(diagnostic)
    return diagnostic


def validate_retry(entry, action):
    if not entry.get("retry_gate"):
        return
    change = action.get("changed_approach")
    checks.require(isinstance(change, dict), "repeated failure retry requires changed_approach")
    checks.require(change.get("failure_signature") == entry["retry_gate"]["signature"], "retry cites stale failure")
    known = {c["id"] for c in entry.get("failure_cases", [])}
    checks.require(change.get("case_id") in known and change["case_id"] == entry["retry_gate"]["case_id"], "retry must cite current failure evidence")
    for key in ("diagnosis", "strategy"):
        checks.require(isinstance(change.get(key), str) and 0 < len(change[key].strip()) <= 2000, "retry needs bounded diagnosis and changed strategy")
    fingerprint = " ".join(change["strategy"].casefold().split())
    checks.require(fingerprint not in entry.get("retry_strategies", []), "retry strategy already attempted")
    return fingerprint
