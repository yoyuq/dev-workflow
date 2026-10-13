"""Append-only implementation revisions. Original requirements and checks stay pinned."""
import copy
import hashlib

import check_night_run as checks
import workflow_titles as titles
import workflow_display as display
from workflow_bus import encoded


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def policy(plan):
    value = plan.get("replanning")
    if value is None:
        return None
    checks.require(set(value) == {"max_revisions", "max_added_tasks", "max_total_attempts", "preparation_checks"}, "invalid replanning policy")
    for key in ("max_revisions", "max_added_tasks", "max_total_attempts"):
        checks.integer(value[key], "replanning." + key)
    checks.require(value["max_added_tasks"] <= 32 and value["max_revisions"] <= 20, "replanning bounds too large")
    checks.require(isinstance(value["preparation_checks"], list), "preparation_checks must be a list")
    return value


def materialize(base, state):
    """Replay validated, hashed task overlays; never rewrite plan.json or pins."""
    current = copy.deepcopy(base)
    previous = digest(base)
    seen = set()
    for index, revision in enumerate(state.get("plan_revisions", []), 1):
        checks.require(revision["number"] == index and revision["previous"] == previous, "broken revision chain")
        checks.require(revision["id"] not in seen, "duplicate revision")
        seen.add(revision["id"])
        proposal = revision["proposal"]
        candidate = build(current, proposal)
        checks.require(revision["digest"] == digest(candidate), "revision content drift")
        current, previous = candidate, revision["digest"]
    return current


def build(plan, proposal):
    """Only two expressible operations: add preparation tasks and edit dependencies."""
    bound = policy(plan)
    checks.require(bound is not None, "replanning was not enabled in the confirmed plan")
    checks.require(set(proposal) == {"id", "expected_revision", "reason", "add", "dependencies"}, "unsupported replan fields; goal/acceptance changes require grill-me")
    checks.identifier(proposal["id"], "proposal.id")
    checks.text(proposal["reason"], "proposal.reason")
    checks.require(type(proposal["expected_revision"]) is int and proposal["expected_revision"] >= 0, "invalid revision")
    checks.require(isinstance(proposal["add"], list) and isinstance(proposal["dependencies"], dict), "invalid operations")
    checks.require(proposal["add"] or proposal["dependencies"], "empty revision")
    result = copy.deepcopy(plan)
    tasks = {t["id"]: t for t in result["tasks"]}
    catalogue = {c["id"]: c for c in bound["preparation_checks"]}
    for spec in proposal["add"]:
        checks.require(set(spec) - {"title", "titles", "display"} == {"id", "parent", "goal", "allowed_scope", "checks", "depends_on"}, "invalid preparation task")
        titles.validate(spec)
        display.validate(spec, display.TASK_LIMITS)
        tid, parent = spec["id"], tasks.get(spec["parent"])
        checks.identifier(tid, "new task ID")
        checks.require(tid not in tasks and parent and not parent.get("support_for"), "unknown parent or duplicate/nested preparation task")
        checks.text(spec["goal"], "preparation goal")
        checks.require(spec["allowed_scope"] and all(any(p == s or p.startswith(s + "/") for s in parent["allowed_scope"]) for p in spec["allowed_scope"]), "preparation scope exceeds parent")
        checks.require(spec["checks"] and set(spec["checks"]) <= catalogue.keys(), "preparation checks must come from pinned catalogue")
        child = {"id": tid, "module": parent["module"], "goal": spec["goal"], "support_for": parent["id"],
                 "allowed_scope": spec["allowed_scope"], "depends_on": spec["depends_on"], "requires": list(parent.get("requires", [])),
                 "produces": [], "resources": list(parent.get("resources", [])), "contracts": list(parent.get("contracts", [])),
                 "verification": [copy.deepcopy(catalogue[k]) for k in spec["checks"]],
                 "internal_guides": list(parent.get("internal_guides", []))}
        if "title" in spec:
            child["title"] = spec["title"]
        if 'titles' in spec:
            child['titles'] = copy.deepcopy(spec['titles'])
        if 'display' in spec:
            child['display'] = copy.deepcopy(spec['display'])
        tasks[tid] = child
        result["tasks"].append(child)
        parent["depends_on"] = list(dict.fromkeys(parent.get("depends_on", []) + [tid]))
    for tid, deps in proposal["dependencies"].items():
        checks.require(tid in tasks and isinstance(deps, list), "unknown task/dependencies")
        # Preparation edges are mandatory: the parent retains original acceptance.
        required = [t["id"] for t in tasks.values() if t.get("support_for") == tid]
        tasks[tid]["depends_on"] = list(dict.fromkeys(deps + required))
    checks.require(sum(bool(t.get("support_for")) for t in tasks.values()) <= bound["max_added_tasks"], "added-task budget exhausted")
    return result


def quiescent(state):
    """A proposal waits until pending worker results have also been accepted."""
    return (not state.get('integration_intent') and not state.get('coordinator_active') and
            all(e['status'] not in {'running', 'creating', 'committing', 'finished', 'verified', 'awaiting_review'}
                for e in state['tasks'].values()))


def activate(base, plan, state, bus, proposal, validator):
    revisions = state.get("plan_revisions", [])
    prior = next((r for r in revisions if r["id"] == proposal.get("id")), None)
    if prior:
        checks.require(prior["proposal"] == proposal, "revision ID reused with different content")
        return plan
    bound = policy(plan)
    checks.require(bound and len(revisions) < bound["max_revisions"], "revision budget exhausted/disabled")
    checks.require(proposal["expected_revision"] == len(revisions), "stale revision proposal")
    checks.require(quiescent(state), "replan requires a quiescent checkpoint")
    candidate = build(plan, proposal)
    validator(candidate)
    old = {t["id"]: t for t in plan["tasks"]}
    for task in candidate["tasks"]:
        tid = task["id"]
        if tid in old and task != old[tid]:
            entry = state["tasks"][tid]
            checks.require(entry["status"] != "integrated" and not entry.get("needs_human"), "cannot revise accepted or human-blocked task")
            checks.require(not any(m["sender"] == tid for m in bus.unresolved()), "resolve outstanding task decisions before replanning")
    revision = {"number": len(revisions) + 1, "id": proposal["id"], "proposal": copy.deepcopy(proposal),
                "previous": digest(plan), "digest": digest(candidate)}
    updated = copy.deepcopy(state)
    updated.setdefault("plan_revisions", []).append(revision)
    for task in candidate["tasks"]:
        updated["tasks"].setdefault(task["id"], {"status": "pending", "attempts": 0, "history": [], "pid": None, "reason": "", "priority": 0})
        if task["id"] in old and task != old[task["id"]]:
            updated["tasks"][task["id"]]["status"] = "pending"
    updated.pop("final_checks", None)
    updated["status"] = "ready"
    with bus.transaction():
        bus.save(updated, "plan_revised", revision=revision)
    state.clear()
    state.update(updated)
    return candidate
