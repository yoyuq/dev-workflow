"""Behavioral regression cases; subprocess workers here are simulations, never real AI."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import check_night_run as checks
import workflow_coordinator as coord
import workflow_memory as memory
import workflow_message as message
from workflow_bus import Bus, MAILBOX
from benchmark_fixtures import SIMULATED_ALIGNMENT

HERE = Path(__file__).resolve().parent
WORKER = r'''
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import workflow_message as msg
if '--version' in sys.argv:
    print('simulated-worker-1'); raise SystemExit(0)
prompt = sys.stdin.read()
mode = sys.argv[2]
if mode == 'coordinator':
    context = json.loads(Path('context.json').read_text(encoding='utf-8'))
    req = context['requests'][0]
    Path('decision.json').write_text(json.dumps({'actions':[{'type':'reply','task_id':req['sender'],'request_id':req['id'],'text':'confirmed: OK'}]}))
    raise SystemExit(0)
box = msg.read('.', True); tid = box['task_id']
def wait_kind(kind):
    end = time.monotonic() + 15
    while time.monotonic() < end:
        box = msg.read('.', True)
        found = [m for m in box['messages'] if m['kind'] == kind]
        if found: return found[0]
        time.sleep(.15)
    raise RuntimeError('no message of kind '+kind)
if mode == 'peer':
    if tid == 'A':
        question = wait_kind('question')
        msg.post('.', 'reply', 'B', 'OK', reply_to=question['id'])
        Path('a.txt').write_text('OK')
    elif tid == 'B':
        msg.post('.', 'question', 'A', 'What is the shared output?')
        reply = wait_kind('reply')
        Path('b.txt').write_text(reply['payload']['text'])
    else:
        assert Path('a.txt').read_text() == Path('b.txt').read_text() == 'OK'
        Path('result.txt').write_text('integrated dependencies OK')
elif mode == 'coordinator_question':
    msg.post('.', 'question', text='Confirm required output from the fixed task goal')
    decision = wait_kind('decision')
    assert 'OK' in decision['payload']['text']
    Path('a.txt').write_text('OK')
elif mode == 'late_question':
    assignment = [m for m in box['messages'] if m['kind']=='assignment'][-1]
    if assignment['payload']['attempt']==1:
        msg.post('.', 'question', text='Confirm contract before accepting this work')
    Path('a.txt').write_text('OK')
elif mode == 'scope':
    Path('outside.txt').write_text('unapproved')
    Path('a.txt').write_text('OK')
elif mode == 'conflict':
    time.sleep(.6)
    Path('shared.txt').write_text(tid)
elif mode == 'semantic':
    if tid == 'A': Path('a.txt').write_text('2')
    else: Path('b.txt').write_text(Path('a.txt').read_text() if Path('a.txt').exists() else '1')
else:
    Path('a.txt').write_text('OK')
msg.read('.', True)
'''
CHECK = r'''
from pathlib import Path
import sys
r=Path(sys.argv[1]); mode=sys.argv[2]
if mode=='A': assert (r/'a.txt').read_text()=='OK'
elif mode=='B': assert (r/'b.txt').read_text()=='OK'
elif mode=='C': assert (r/'result.txt').read_text()=='integrated dependencies OK'
elif mode.startswith('conflict'): assert (r/'shared.txt').read_text()==mode[-1]
elif mode=='semanticA': assert (r/'a.txt').read_text()=='2'
elif mode=='semanticB': assert (r/'b.txt').read_text() in ('1','2')
elif mode=='combined': assert (r/'a.txt').read_text()==(r/'b.txt').read_text()
print('PASS',mode)
'''


def coverage_for(plan):
    plan["requirements"] = [{"id": "R-" + t["id"], "text": t["goal"], "task_ids": [t["id"]], "acceptance": [
        {"id": "AC-" + t["id"], "text": "Independent behavior checks", "type": "automated",
         "checks": [{"task_id": t["id"], "check_id": c["id"]} for c in t["verification"]]}
    ] + [{"id": f"MANUAL-{t['id']}-{i}", "text": text, "type": "manual", "task_id": t["id"], "review_index": i}
         for i, text in enumerate(t.get("manual_review", []))]} for t in plan["tasks"]]


def fixture(base, mode="simple", count=1):
    repo = base / "repo"
    repo.mkdir()
    coord.git(repo, "init", "--template=", "--initial-branch=main")
    coord.git(repo, "config", "user.name", "Coordination Test")
    coord.git(repo, "config", "user.email", "test@example.invalid")
    (repo / "base.txt").write_text("baseline")
    if mode == "ignored":
        (repo / ".gitignore").write_text("a.txt\ncache/\n")
    if mode == "conflict":
        (repo / "shared.txt").write_text("base")
    coord.git(repo, "add", "--all")
    coord.git(repo, "commit", "-m", "baseline")
    skill = base / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: fixture\ndescription: Simulated test fixture.\n---\n")
    worker = base / "worker.py"
    worker.write_text(WORKER, encoding="utf-8")
    check = base / "accept.py"
    check.write_text(CHECK, encoding="utf-8")
    document = base / "plan.md"
    document.write_text("Modules: A/B parallel; C consumes accepted A/B. Local test commits only.")
    tasks = []
    for tid in "ABC"[:count]:
        scope = {"A": "a.txt", "B": "b.txt", "C": "result.txt"}[tid]
        check_mode = tid
        if mode == "conflict":
            scope, check_mode = "shared.txt", "conflict" + tid
        elif mode == "semantic":
            check_mode = "semantic" + tid
        tasks.append({"id": tid, "module": tid, "goal": "Produce verified " + tid + " with output OK; follow peer facts.",
            "allowed_scope": [scope], "depends_on": [], "requires": ["api-ready"] if tid == "C" else [],
            "produces": ["api-ready"] if tid == "B" else [], "contracts": ["api"], "resources": [],
            "verification": [{"id": "behavior", "argv": [sys.executable, "-B", str(check), "{repo}", check_mode], "timeout_seconds": 5}]})
    if count == 3:
        tasks[2]["depends_on"] = ["A"]
    plan = {"format_version": 2, "kind": "coordinated", "run_id": "case", "goal": "Validate coordinator with deterministic subprocess simulations",
            "requirements_alignment": copy.deepcopy(SIMULATED_ALIGNMENT),
            "repository": str(repo), "base_commit": coord.git(repo, "rev-parse", "HEAD"), "skill_root": str(skill),
            "authorization_ref": "isolated regression test", "execution_plan": str(document), "delivery": "branch_commit_and_report",
            "limits": {"parallelism": 2, "max_attempts": 2, "max_coordinator_rounds": 2, "max_run_seconds": 90},
            "contracts": {"api": {"text": "Output OK", "editable": True}}, "tasks": tasks,
            "integration_checks": ([{"id":"combined", "argv":[sys.executable,"-B",str(check),"{repo}","combined"],"timeout_seconds":5}] if mode == "semantic" else [])}
    config = {"format_version": 1, "harness": {"kind": "command", "executable": sys.executable,
              "args": ["-B", str(worker), str(HERE), mode]},
              "limits": {"attempt_seconds": 25, "no_progress_seconds": 20, "max_output_bytes": 100000},
              "protected_files": [str(worker), str(check)]}
    coordinator = copy.deepcopy(config)
    coordinator["harness"]["args"][-1] = "coordinator"
    coverage_for(plan)
    memory.atomic_json(base / "plan.json", plan)
    memory.atomic_json(base / "runtime.json", {"worker": config, "coordinator": coordinator})
    coord.initialize(base / "plan.json", base / "runtime.json", base / "run")
    return plan, base / "run"


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def test_explicit_ignored_deliverable_survives_commit_and_final_audit(self):
        plan, root = fixture(self.base, "ignored")
        self.assertEqual(coord.run(root), 0)
        state = Bus(root).state()
        review = Path(state["review_workspace"])
        self.assertEqual((review / "a.txt").read_text(), "OK")
        self.assertEqual(coord.git(review, "ls-files", "a.txt"), "a.txt")
        self.assertEqual(coord.git(Path(plan["repository"]), "rev-parse", "main"), plan["base_commit"])

    def test_directory_scope_does_not_collect_ignored_caches(self):
        plan, _ = fixture(self.base, "ignored")
        repo = Path(plan["repository"])
        (repo / "cache").mkdir()
        (repo / "cache/generated.txt").write_text("generated, not a deliverable")
        snapshot = coord.source_snapshot(repo, [{"allowed_scope": ["cache"]}])
        self.assertNotIn("cache/generated.txt", snapshot["files"])
        explicit = coord.source_snapshot(repo, [{"allowed_scope": ["cache/generated.txt"]}])
        self.assertIn("cache/generated.txt", explicit["files"])

    def test_tracked_file_replaced_by_directory_cannot_force_add_children(self):
        plan, _ = fixture(self.base)
        repo = Path(plan["repository"])
        path = repo / "base.txt"
        path.unlink()
        path.mkdir()
        (path / "hidden.txt").write_text("must not enter the index")
        with self.assertRaises(checks.Rejected):
            coord.stage_files(repo, ["base.txt"])
        self.assertEqual(coord.git(repo, "diff", "--cached", "--name-only"), "")

    def test_parallel_peer_messages_and_milestone_dependency(self):
        plan, root = fixture(self.base, "peer", 3)
        self.assertEqual(coord.run(root), 0)
        bus = Bus(root)
        state = bus.state()
        self.assertTrue(all(v["status"] == "integrated" for v in state["tasks"].values()))
        self.assertEqual(state["tasks"]["A"]["baseline"]["head"], state["tasks"]["B"]["baseline"]["head"])
        self.assertNotEqual(state["tasks"]["C"]["baseline"]["head"], plan["base_commit"])
        self.assertTrue(any(m["kind"] == "question" and m["sender"] == "B" for m in bus.messages()))
        self.assertTrue(any(m["kind"] == "reply" and m["recipient"] == "B" and m["acked"] for m in bus.messages()))
        self.assertEqual(coord.git(Path(plan["repository"]), "rev-parse", "main"), plan["base_commit"])
        self.assertEqual(coord.run(root), 0)  # No worker is launched twice after completion.
        self.assertEqual(Bus(root).state()["tasks"]["A"]["attempts"], 1)

    def test_coordinator_question_answer_is_delivered_and_acked(self):
        _, root = fixture(self.base, "coordinator_question")
        self.assertEqual(coord.run(root), 0)
        self.assertEqual(Bus(root).state()["coordinator_rounds"], 1)
        self.assertTrue(any(m["kind"] == "decision" and m["acked"] for m in Bus(root).messages()))

    def test_late_answer_reopens_finished_worker_before_acceptance(self):
        _, root = fixture(self.base, "late_question")
        self.assertEqual(coord.run(root), 0)
        self.assertEqual(Bus(root).state()["tasks"]["A"]["attempts"], 2)
        self.assertTrue(all(m["acked"] for m in Bus(root).messages() if m["required"]))

    def test_commit_recovery_keeps_manual_review_gate(self):
        plan, root = fixture(self.base)
        plan["tasks"][0]["manual_review"] = ["Human visual review"]
        coverage_for(plan)
        memory.atomic_json(root / "plan.json", plan)
        pins = checks.read_json(root / "pins.json")
        pins["plan"] = checks.digest(root / "plan.json")
        memory.atomic_json(root / "pins.json", pins)
        path = coord.new_worktree(root, plan, "workflow/case/A-a1", plan["base_commit"], "A-a1")
        baseline = coord.serial.snapshot(path)
        (path / "a.txt").write_text("OK")
        coord.git(path, "add", "a.txt")
        tree = coord.git(path, "write-tree")
        bus = Bus(root)
        state = bus.state()
        state["tasks"]["A"].update(status="committing", attempts=1, branch="workflow/case/A-a1", workspace=str(path),
                                    baseline=baseline, commit_intent={"tree":tree})
        bus.save(state,"commit_intent")
        coord.git(path,"commit","-m","verified task")
        coord.recover(root)
        self.assertEqual(bus.state()["tasks"]["A"]["status"], "awaiting_review")
        self.assertEqual(bus.state()["integration_head"], plan["base_commit"])

    def test_out_of_scope_changes_never_integrate(self):
        plan, root = fixture(self.base, "scope")
        self.assertEqual(coord.run(root), 3)
        self.assertEqual(Bus(root).state()["integration_head"], plan["base_commit"])
        self.assertEqual(Bus(root).state()["tasks"]["A"]["status"], "failed")
        self.assertTrue((root / "workspaces/A-a1/outside.txt").exists())

    def test_text_conflict_preserves_workspace_and_bounded_retry(self):
        plan, root = fixture(self.base, "conflict", 2)
        self.assertEqual(coord.run(root), 3)
        state = Bus(root).state()
        self.assertEqual(sum(e["status"] == "integrated" for e in state["tasks"].values()), 1)
        self.assertTrue(any("integration" in e.get("reason", "") or "conflict" in e.get("reason", "").lower() for e in state["tasks"].values()))
        self.assertTrue(any((p / "shared.txt").exists() for p in (root / "workspaces").glob("candidate-*")))

    def test_clean_merge_semantic_failure_requires_new_attempt(self):
        _, root = fixture(self.base, "semantic", 2)
        self.assertEqual(coord.run(root), 0)
        state = Bus(root).state()
        self.assertEqual(state["tasks"]["B"]["attempts"], 2)
        self.assertTrue(all(r["result"] == "passed" for r in state["final_checks"]))

    def test_pinned_acceptance_drift_stops_before_execution(self):
        _, root = fixture(self.base)
        (self.base / "accept.py").write_text("print('fake pass')")
        with self.assertRaises(checks.Rejected):
            coord.run(root)
        self.assertEqual(Bus(root).state()["tasks"]["A"]["attempts"], 0)

    def test_prior_failure_is_recalled_without_promoting_it_to_verified_advice(self):
        plan, root = fixture(self.base)
        config = checks.read_json(root / "runtime.json")["worker"]
        env = coord.runtime.preflight(config, Path(plan["repository"]))
        coord.serial.failure_memory(root, config, "previous", 1, "Produce verified output failed before this run", env, {})
        self.assertEqual(coord.run(root), 0)
        lessons = Bus(root).state()["tasks"]["A"]["lessons"]
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0]["status"], "unresolved")
        self.assertFalse(lessons[0]["recommendation_eligible"])

    def test_dependency_cycle_rejected(self):
        plan, _ = fixture(self.base, count=2)
        plan["tasks"][0]["depends_on"] = ["B"]
        plan["tasks"][1]["depends_on"] = ["A"]
        with self.assertRaises(checks.Rejected):
            coord.validate(plan)

    def test_duplicate_delivery_ack_ownership_and_rewrite(self):
        _, root = fixture(self.base, count=2)
        bus = Bus(root)
        workspace = self.base / "mailtest"
        workspace.mkdir()
        bus.deliver("A", workspace, {})
        mid = bus.send("A", "decision", {"text": "approved scope"}, required=True)
        bus.deliver("A", workspace, {})
        message.read(workspace, True)
        msg = message.post(workspace, "question", "B", "interface?")
        bus.ingest("A", 1, workspace, {"A", "B"})
        bus.ingest("A", 1, workspace, {"A", "B"})
        self.assertEqual(len(bus.messages("B")), 1)
        self.assertTrue(next(m for m in bus.messages("A") if m["id"] == mid)["acked"])
        with self.assertRaises(checks.Rejected):
            bus.ack("B", [mid])
        path = workspace / MAILBOX / "outbox" / (msg["id"] + ".json")
        msg["text"] = "rewritten"
        memory.atomic_json(path, msg)
        with self.assertRaises(checks.Rejected):
            bus.ingest("A", 1, workspace, {"A", "B"})

    def test_contract_update_invalidates_consumers_and_milestones(self):
        plan, root = fixture(self.base, count=3)
        bus = Bus(root)
        state = bus.state()
        state["tasks"]["B"]["status"] = "integrated"
        state["milestones"]["api-ready"] = {"owner": "B"}
        coord.publish_contract(plan, coord.validate(plan), state, bus, "api", 1, "new compatible output")
        self.assertEqual(state["contracts"]["api"]["version"], 2)
        self.assertEqual(state["tasks"]["B"]["status"], "pending")
        self.assertNotIn("api-ready", state["milestones"])
        with self.assertRaises(checks.Rejected):
            coord.publish_contract(plan, coord.validate(plan), state, bus, "api", 1, "stale")

    def test_ai_cannot_mark_done_or_change_goal(self):
        plan, root = fixture(self.base)
        bus = Bus(root)
        state = bus.state()
        with self.assertRaises(checks.Rejected):
            coord.apply_decisions(plan, coord.validate(plan), state, bus,
                                  {"actions": [{"type": "complete", "task_id": "A", "request_id": "x"}]}, {"x"})
        self.assertEqual(bus.state()["tasks"]["A"]["status"], "pending")

    def test_empty_and_partial_decisions_do_not_drop_unanswered_requests(self):
        plan, root = fixture(self.base)
        bus = Bus(root)
        first = bus.send("coordinator", "question", {"text": "one"}, sender="A")
        second = bus.send("coordinator", "question", {"text": "two"}, sender="A")
        with self.assertRaises(checks.Rejected):
            coord.apply_decisions(plan, coord.validate(plan), bus.state(), bus, {"actions": []}, {first, second})
        coord.apply_decisions(plan, coord.validate(plan), bus.state(), bus, {"actions": [
            {"type":"reply", "task_id":"A", "request_id":first, "text":"one answered"}]}, {first,second})
        self.assertEqual([m["id"] for m in bus.messages("coordinator", unacked=True)], [second])
        self.assertEqual([m["id"] for m in bus.unresolved()], [second])

    def test_unavailable_peer_question_escalates_until_answered(self):
        plan, root = fixture(self.base, count=2)
        bus = Bus(root)
        state = bus.state()
        state["tasks"]["B"]["status"] = "integrated"
        question = bus.send("B", "question", {"text":"which interface?"}, sender="A")
        coord.route_unanswered(bus, state, 60)
        pending = bus.messages("coordinator", unacked=True)
        self.assertEqual(len(pending), 1)
        coord.apply_decisions(plan, coord.validate(plan), state, bus, {"actions":[
            {"type":"reply","task_id":"A","request_id":pending[0]["id"],"text":"Read contract v1"}]}, {pending[0]["id"]})
        self.assertEqual(bus.unresolved(), [])

    def test_decision_transaction_rolls_back_messages_and_state_together(self):
        plan, root = fixture(self.base)
        bus = Bus(root)
        state = bus.state()
        before = copy.deepcopy(state)
        request = bus.send("coordinator", "change_request", {"text":"update"}, sender="A")
        original_save = bus.save
        def fail_save(*args, **kwargs):
            raise OSError("simulated storage failure")
        bus.save = fail_save
        with self.assertRaises(OSError):
            coord.apply_decisions(plan, coord.validate(plan), state, bus, {"actions":[
                {"type":"publish_contract","name":"api","expected_version":1,"text":"v2","request_id":request}]}, {request})
        bus.save = original_save
        self.assertEqual(bus.state(), before)
        self.assertEqual(state, before)
        self.assertEqual(bus.messages("A"), [])
        self.assertEqual(len(bus.unresolved()), 1)

    def test_launch_intent_recovery_preserves_created_worktree(self):
        plan, root = fixture(self.base)
        bus = Bus(root)
        state = bus.state()
        branch = "workflow/case/A-a1"
        workspace = root / "workspaces/A-a1"
        state["tasks"]["A"].update(status="creating", attempts=1, branch=branch, workspace=str(workspace), launch_base=plan["base_commit"])
        bus.save(state, "launch_intent")
        coord.new_worktree(root, plan, branch, plan["base_commit"], "A-a1")
        coord.recover(root)
        self.assertTrue(workspace.exists())
        self.assertEqual(bus.state()["tasks"]["A"]["attempts"], 1)
        self.assertEqual(coord.run(root), 0)
        self.assertEqual(bus.state()["tasks"]["A"]["attempts"], 2)

    def test_live_registered_process_prevents_recovery(self):
        import os
        _, root = fixture(self.base)
        Bus(root).process_started("unreported-to-main-loop", os.getpid())
        with self.assertRaises(checks.Rejected):
            coord.recover(root)

    def test_manual_review_prevents_completion_and_dependency_release(self):
        plan, root = fixture(self.base)
        plan["tasks"][0]["manual_review"] = ["Human reviews visual quality"]
        coverage_for(plan)
        memory.atomic_json(root / "plan.json", plan)
        pins = checks.read_json(root / "pins.json")
        pins["plan"] = checks.digest(root / "plan.json")
        memory.atomic_json(root / "pins.json", pins)
        self.assertEqual(coord.run(root), 3)
        self.assertEqual(Bus(root).state()["tasks"]["A"]["status"], "awaiting_review")
        self.assertEqual(Bus(root).state()["integration_head"], plan["base_commit"])

    def test_recover_ref_advanced_before_state_save(self):
        plan, root = fixture(self.base)
        bus = Bus(root)
        state = bus.state()
        repo = Path(plan["repository"])
        path = coord.new_worktree(root, plan, "test-result", plan["base_commit"], "crash-result")
        (path / "a.txt").write_text("OK")
        coord.git(path, "add", "a.txt")
        coord.git(path, "commit", "-m", "verified candidate")
        head = coord.git(path, "rev-parse", "HEAD")
        state["integration_intent"] = {"task_id": "A", "before": plan["base_commit"], "after": head}
        bus.save(state, "simulated_crash_window")
        coord.git(repo, "update-ref", "refs/heads/" + state["integration_branch"], head, plan["base_commit"])
        coord.recover(root)
        self.assertEqual(bus.state()["tasks"]["A"]["status"], "integrated")
        self.assertIsNone(bus.state()["integration_intent"])


if __name__ == "__main__":
    unittest.main()
