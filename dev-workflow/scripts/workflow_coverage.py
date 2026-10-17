"""Requirement coverage for coordinated v2; evidence, never model self-certification."""
import check_night_run as checks


def validate(plan, tasks):
    requirements = plan.get("requirements")
    checks.require(isinstance(requirements, list) and requirements, "requirements must be a nonempty list")
    catalog = {tid: {c["id"] for c in task["verification"]} for tid, task in tasks.items()}
    catalog["integration"] = {c["id"] for c in plan.get("integration_checks", [])}
    for tid, items in [(t, task["verification"]) for t, task in tasks.items()] + [("integration", plan.get("integration_checks", []))]:
        checks.require(len(catalog[tid]) == len(items), "duplicate check ID in " + tid)
    seen, criteria, covered, reviews = set(), set(), set(), set()
    for req in requirements:
        rid = checks.identifier(req["id"], "requirement.id")
        checks.require(rid not in seen, "duplicate requirement ID")
        seen.add(rid)
        checks.text(req["text"], "requirement.text")
        checks.strings(req["task_ids"], "requirement.task_ids", nonempty=True)
        owners = set(req["task_ids"])
        checks.require(owners <= tasks.keys(), "unknown requirement task")
        covered |= owners
        acceptance = req.get("acceptance")
        checks.require(isinstance(acceptance, list) and acceptance, "requirement has no acceptance")
        evidenced = set()
        for criterion in acceptance:
            cid = checks.identifier(criterion["id"], "acceptance.id")
            checks.require(cid not in criteria, "duplicate acceptance ID")
            criteria.add(cid)
            checks.text(criterion["text"], "acceptance.text")
            if criterion["type"] == "automated":
                refs = criterion.get("checks")
                checks.require(isinstance(refs, list) and refs, "automated acceptance needs checks")
                for ref in refs:
                    tid = ref["task_id"]
                    checks.require(tid in owners | {"integration"} and ref["check_id"] in catalog.get(tid, set()), "unknown or unrelated acceptance check")
                    if tid != "integration":
                        evidenced.add(tid)
            elif criterion["type"] == "manual":
                tid, index = criterion["task_id"], criterion["review_index"]
                checks.require(tid in owners and type(index) is int and 0 <= index < len(tasks[tid].get("manual_review", [])), "unknown manual review")
                reviews.add((tid, index))
                evidenced.add(tid)
            else:
                raise checks.Rejected("acceptance.type must be automated or manual")
        checks.require(owners <= evidenced, "each requirement task needs its own acceptance evidence")
    support = set()
    while True:
        found = {tid for tid, t in tasks.items() if t.get('support_for') in covered | support and
                 tid in tasks[t['support_for']].get('depends_on', []) and not t.get('manual_review')}
        if found <= support: break
        support |= found
    checks.require(covered | support == tasks.keys(), "tasks missing requirement coverage")
    checks.require(reviews == {(tid, i) for tid, task in tasks.items() for i in range(len(task.get("manual_review", [])))}, "manual reviews missing requirement coverage")


def report(plan, state, review_valid):
    final = {(r["task_id"], r["check_id"]): r for r in state.get("final_checks", [])}
    result = []
    for req in plan["requirements"]:
        rows = []
        for criterion in req["acceptance"]:
            if criterion["type"] == "automated":
                evidence = [final.get((r["task_id"], r["check_id"])) for r in criterion["checks"]]
                passed = bool(evidence) and all(r and r["result"] == "passed" for r in evidence)
            else:
                tid = criterion["task_id"]
                evidence = [state["tasks"][tid].get("human_review")]
                passed = review_valid(tid)
            rows.append({"id": criterion["id"], "text": criterion["text"], "type": criterion["type"],
                         "status": "passed" if passed else "pending", "evidence": evidence})
        passed = all(state["tasks"][tid]["status"] == "integrated" for tid in req["task_ids"]) and all(r["status"] == "passed" for r in rows)
        result.append({"id": req["id"], "text": req["text"], "task_ids": req["task_ids"],
                       "status": "passed" if passed else "pending", "acceptance": rows})
    return result
