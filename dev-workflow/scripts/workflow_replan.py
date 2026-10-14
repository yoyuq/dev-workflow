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
    required = {"max_revisions", "max_added_tasks", "max_total_attempts", "preparation_checks"}
    checks.require(isinstance(value, dict) and required <= set(value) <= required | {'allow_feature_split'}, "invalid replanning policy")
    checks.require(type(value.get('allow_feature_split', False)) is bool, 'allow_feature_split must be boolean')
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
    """Add scoped work under original owners; every owner retains full acceptance."""
    bound = policy(plan)
    checks.require(bound is not None, "replanning was not enabled in the confirmed plan")
    fields = {"id", "expected_revision", "reason", "add", "dependencies"}
    checks.require(fields <= set(proposal) <= fields | {'split'}, "unsupported replan fields; goal/acceptance changes require grill-me")
    checks.identifier(proposal["id"], "proposal.id")
    checks.text(proposal["reason"], "proposal.reason")
    checks.require(type(proposal["expected_revision"]) is int and proposal["expected_revision"] >= 0, "invalid revision")
    checks.require(isinstance(proposal["add"], list) and isinstance(proposal["dependencies"], dict), "invalid operations")
    splits = proposal.get('split', [])
    checks.require(isinstance(splits, list), 'invalid functional splits')
    checks.require(not splits or bound.get('allow_feature_split') is True, 'functional splitting was not authorized in this plan')
    checks.require(proposal["add"] or proposal["dependencies"] or splits, "empty revision")
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
    for operation in splits:
        checks.require(isinstance(operation, dict) and set(operation) == {'parent', 'reason', 'children'}, 'invalid functional split')
        parent = tasks.get(operation['parent'])
        checks.require(parent and not parent.get('split_children') and
                       (not parent.get('support_for') or parent.get('support_kind') == 'feature'), 'unknown/already split functional parent')
        checks.text(operation['reason'], 'functional split reason')
        children = operation['children']
        checks.require(isinstance(children, list) and 2 <= len(children) <= 16, 'functional split needs 2..16 reviewable children')
        original = {c['id']: c for c in parent['verification']}
        original_dependencies = list(parent.get('depends_on', []))
        parent['split_children'] = []
        parent['split_dependencies'] = original_dependencies
        for spec in children:
            required = {'id', 'goal', 'allowed_scope', 'checks', 'depends_on'}
            checks.require(isinstance(spec, dict) and required <= set(spec) <= required | {'title', 'titles', 'display', 'partition'}, 'invalid functional child')
            tid = checks.identifier(spec['id'], 'functional child ID')
            checks.require(tid not in tasks, 'duplicate functional child ID')
            checks.text(spec['goal'], 'functional child goal')
            checks.strings(spec['allowed_scope'], 'functional child scope', nonempty=True)
            checks.require(all(any(p == s or p.startswith(s + '/') for s in parent['allowed_scope']) for p in spec['allowed_scope']), 'functional scope exceeds original owner')
            checks.strings(spec['checks'], 'functional child checks', nonempty=True)
            checks.require(len(set(spec['checks'])) == len(spec['checks']) and set(spec['checks']) <= original.keys(), 'functional checks must select original owner checks')
            checks.strings(spec['depends_on'], 'functional child dependencies')
            child = {'id': tid, 'module': parent['module'], 'goal': spec['goal'],
                     'support_for': parent['id'], 'support_kind': 'feature',
                     'allowed_scope': copy.deepcopy(spec['allowed_scope']),
                     'depends_on': list(dict.fromkeys(original_dependencies + spec['depends_on'])),
                     'inherited_dependencies': original_dependencies,
                     'requires': list(parent.get('requires', [])), 'produces': [],
                     'resources': list(parent.get('resources', [])), 'contracts': list(parent.get('contracts', [])),
                     'verification': [copy.deepcopy(original[k]) for k in spec['checks']],
                     'internal_guides': list(parent.get('internal_guides', []))}
            for key in ('title', 'titles', 'display', 'partition'):
                if key in spec: child[key] = copy.deepcopy(spec[key])
            titles.validate(child); display.validate(child, display.TASK_LIMITS)
            tasks[tid] = child; result['tasks'].append(child); parent['split_children'].append(tid)
        parent['depends_on'] = list(dict.fromkeys(original_dependencies + parent['split_children']))
    for tid, deps in proposal["dependencies"].items():
        checks.require(tid in tasks and isinstance(deps, list), "unknown task/dependencies")
        # Preparation edges are mandatory: the parent retains original acceptance.
        required = [t["id"] for t in tasks.values() if t.get("support_for") == tid]
        tasks[tid]["depends_on"] = list(dict.fromkeys(deps + required + tasks[tid].get('split_dependencies', []) + tasks[tid].get('inherited_dependencies', [])))
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
    for operation in proposal.get('split', []):
        entry = state['tasks'][operation['parent']]
        checks.require(not entry.get('retry_gate'), 'diagnose and resolve the original retry gate before functional splitting')
        checks.require(entry['attempts'] < plan['limits']['max_attempts'], 'functional parent has no attempt left for full acceptance')
    checks.require(sum(e['attempts'] for e in state['tasks'].values()) < bound['max_total_attempts'], 'total attempt budget exhausted')
    new_count = len(candidate['tasks']) - len(plan['tasks'])
    checks.require(sum(e['attempts'] for e in state['tasks'].values()) + new_count + len(proposal.get('split', [])) <= bound['max_total_attempts'], 'not enough original attempts for children and full parent acceptance')
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
        if task['id'] not in old and task.get('support_kind') == 'feature':
            owner = state['tasks'][task['support_for']]
            updated['tasks'][task['id']]['split_origin'] = {'owner': task['support_for'], 'workspace': owner.get('workspace'),
                'attempts': owner['attempts'], 'partition_request': copy.deepcopy(owner.get('partition_request'))}
        if task["id"] in old and task != old[task["id"]]:
            updated["tasks"][task["id"]]["status"] = "pending"
            if task.get('split_children') and not old[task['id']].get('split_children'):
                updated['tasks'][task['id']]['split_base'] = state['integration_head']
                updated['tasks'][task['id']]['reason'] = 'Awaiting functional children and original full acceptance'
    # The former owner must wait for its children; release the serial remote
    # dispatch slot without accepting its goal or dropping consumed attempts.
    owner = updated.get('remote_active_task')
    if owner and candidate['tasks'] != plan['tasks'] and any(t.get('support_for') == owner for t in candidate['tasks']):
        updated.pop('remote_active_task', None)
    updated.pop("final_checks", None)
    updated["status"] = "running" if updated.get('session_started') else "ready"
    with bus.transaction():
        bus.save(updated, "plan_revised", revision=revision)
    state.clear()
    state.update(updated)
    return candidate
