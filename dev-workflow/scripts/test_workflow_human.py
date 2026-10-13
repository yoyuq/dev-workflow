"""Isolated simulations of coverage failures and operator-response recovery."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import check_night_run as checks
import workflow_coordinator as coord
import workflow_coverage as coverage
import workflow_human as human
import workflow_memory as memory
from workflow_bus import Bus, encoded
from test_workflow_coordinator import fixture, coverage_for


def repin(root, plan):
    # Test-only preparation, before any simulated work starts. Never migration.
    coverage_for(plan)
    memory.atomic_json(root / "plan.json", plan)
    pins = checks.read_json(root / "pins.json")
    pins["plan"] = checks.digest(root / "plan.json")
    memory.atomic_json(root / "pins.json", pins)


def response(ticket, decision="answer", rid="human-1"):
    return {"id": rid, "ticket_id": ticket["ticket_id"], "decision": decision,
            "text": "SIMULATED: output OK, within prepared scope", "reference": "test-fixture://human-response",
            "quote": "SIMULATED user response; not real authorization"}


class HumanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def blocked(self, count=1):
        plan, root = fixture(self.base, count=count)
        bus = Bus(root)
        state = bus.state()
        state["tasks"]["A"].update(status="blocked", needs_human="Which approved output?", attempts=1)
        mid = bus.send("coordinator", "question", {"text": "Which approved output?", "attempt": 1}, sender="A")
        bus.save(state, "simulated_human_block")
        tasks = coord.validate(plan)
        return plan, root, bus, tasks, mid

    def ticket(self, root, plan, bus, tasks):
        return human.tickets(root, plan, tasks, bus.state(), bus)[0]

    def manual(self, count=1):
        plan, root = fixture(self.base, "peer" if count == 3 else "simple", count)
        plan["tasks"][0]["manual_review"] = ["Review the task's scoped deliverable"]
        repin(root, plan)
        self.assertEqual(coord.run(root), 3)
        return plan, root, Bus(root), coord.validate(plan)

    def test_uncovered_requirement_task_and_unknown_acceptance_rejected_before_side_effects(self):
        plan, root = fixture(self.base, count=2)
        variants = []
        missing = copy.deepcopy(plan); missing.pop("requirements"); variants.append(missing)
        missing = copy.deepcopy(plan); missing["requirements"].pop(); variants.append(missing)
        missing = copy.deepcopy(plan); missing["requirements"][0]["acceptance"] = []; variants.append(missing)
        missing = copy.deepcopy(plan); missing["requirements"][0]["acceptance"][0]["checks"][0]["check_id"] = "unknown"; variants.append(missing)
        missing = copy.deepcopy(plan); missing["requirements"][0]["task_ids"].append("B"); variants.append(missing)
        missing = copy.deepcopy(plan); missing["tasks"][0]["verification"] *= 2; variants.append(missing)
        missing = copy.deepcopy(plan); missing["tasks"][0]["manual_review"] = ["Unmapped review"]; variants.append(missing)
        for index, bad in enumerate(variants):
            with self.subTest(index=index):
                memory.atomic_json(self.base / "bad.json", bad)
                with patch.object(coord, "git") as git:
                    with self.assertRaises(checks.Rejected):
                        coord.initialize(self.base / "bad.json", root / "runtime.json", self.base / f"bad-{index}")
                    git.assert_not_called()
                self.assertFalse((self.base / f"bad-{index}").exists())

    def test_answer_resumes_same_run_and_worker_consumes_message_without_resetting_budget(self):
        plan, root, bus, tasks, mid = self.blocked()
        before = bus.state(); before["elapsed_seconds"] = 7; bus.save(before, "budget_used")
        result = human.respond(root, response(self.ticket(root, plan, bus, tasks)))
        self.assertEqual(result["status"], "applied")
        state = bus.state()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["tasks"]["A"]["attempts"], 2)
        self.assertGreater(state["elapsed_seconds"], 7)
        self.assertFalse(state["tasks"]["A"].get("needs_human"))
        self.assertFalse(bus.unresolved())
        self.assertTrue(next(m for m in bus.messages("A") if m["kind"] == "human_answer")["acked"])
        report = checks.read_json(root / "delivery.json")
        self.assertEqual(report["requirements"][0]["status"], "passed")
        self.assertTrue(Path(report["requirements"][0]["acceptance"][0]["evidence"][0]["evidence_path"]).is_file())

    def test_manual_approval_releases_dependency_without_repeating_worker(self):
        plan, root, bus, tasks = self.manual(count=3)
        state = bus.state()
        self.assertEqual(state["tasks"]["C"]["attempts"], 0)
        self.assertEqual(state["tasks"]["A"]["status"], "awaiting_review")
        ticket = self.ticket(root, plan, bus, tasks)
        self.assertEqual(ticket["context"]["kind"], "review")
        self.assertEqual(human.respond(root, response(ticket, "approve"))["status"], "applied")
        state = bus.state()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["tasks"]["A"]["attempts"], 1)
        self.assertEqual(state["tasks"]["C"]["attempts"], 1)
        self.assertTrue(all(r["status"] == "passed" for r in checks.read_json(root / "delivery.json")["requirements"]))

    def test_duplicate_answer_idempotent_and_changed_duplicate_rejected(self):
        plan, root, bus, tasks, _ = self.blocked()
        answer = response(self.ticket(root, plan, bus, tasks))
        human.respond(root, answer)
        state = bus.state()
        self.assertEqual(human.respond(root, answer)["status"], "applied")
        self.assertEqual(bus.state(), state)
        answer["text"] = "different answer"
        with self.assertRaisesRegex(checks.Rejected, "reused"):
            human.submit(root, answer)
        self.assertEqual(len([m for m in bus.messages() if m["kind"] == "human_answer"]), 1)

    def test_wrong_kind_and_missing_human_evidence_rejected(self):
        plan, root, bus, tasks, _ = self.blocked()
        answer = response(self.ticket(root, plan, bus, tasks), "approve")
        with self.assertRaises(checks.Rejected): human.submit(root, answer)
        answer["decision"] = "answer"; answer["reference"] = ""
        with self.assertRaises(checks.Rejected): human.submit(root, answer)
        self.assertEqual(bus.state()["tasks"]["A"]["status"], "blocked")

    def test_old_attempt_contract_commit_and_plan_answers_rejected(self):
        plan, root, bus, tasks, _ = self.blocked()
        original = bus.state()
        answer = response(self.ticket(root, plan, bus, tasks))
        for field in ("attempt", "contract", "commit", "head"):
            changed = copy.deepcopy(original)
            if field == "attempt": changed["tasks"]["A"]["attempts"] += 1
            if field == "contract": changed["contracts"]["api"]["version"] += 1
            if field == "commit": changed["tasks"]["A"]["commit"] = plan["base_commit"]
            if field == "head": changed["integration_head"] = "different-head"
            bus.save(changed, "simulated_context_change")
            with self.subTest(field=field), self.assertRaisesRegex(checks.Rejected, "stale"):
                human.submit(root, answer)
        bus.save(original, "restore_fixture")
        (root / "plan.json").write_text("{}")
        with self.assertRaisesRegex(checks.Rejected, "plan drift"):
            human.submit(root, answer)

    def test_queued_answer_is_revalidated_after_contract_changes(self):
        plan, root, bus, tasks, _ = self.blocked()
        answer = response(self.ticket(root, plan, bus, tasks))
        human.submit(root, answer)
        state = bus.state()
        coord.publish_contract(plan, tasks, state, bus, "api", 1, "Compatible new contract")
        human.consume(root, plan, tasks, state, bus)
        self.assertEqual(human.receipt(root, answer["id"])["status"], "rejected")
        self.assertTrue(state["tasks"]["A"]["needs_human"])
        self.assertFalse(any(m["kind"] == "human_answer" for m in bus.messages()))

    def test_response_transaction_failure_rolls_back_state_messages_and_receipt(self):
        plan, root, bus, tasks, mid = self.blocked()
        answer = response(self.ticket(root, plan, bus, tasks)); human.submit(root, answer)
        state = bus.state(); before = copy.deepcopy(state)
        with patch.object(bus, "save", side_effect=OSError("simulated disk failure")):
            with self.assertRaises(OSError): human.consume(root, plan, tasks, state, bus)
        self.assertEqual(state, before)
        self.assertEqual(bus.state(), before)
        self.assertEqual(human.receipt(root, answer["id"])["status"], "queued")
        self.assertFalse(any(m["kind"] == "human_answer" for m in bus.messages()))
        human.consume(root, plan, tasks, state, bus)
        self.assertEqual(human.receipt(root, answer["id"])["status"], "applied")

    def test_multiple_questions_keep_blocked_until_all_answered(self):
        plan, root, bus, tasks, _ = self.blocked()
        bus.send("coordinator", "question", {"text": "second prerequisite"}, sender="A")
        human.respond(root, response(self.ticket(root, plan, bus, tasks)))
        self.assertEqual(bus.state()["tasks"]["A"]["status"], "blocked")
        human.respond(root, response(self.ticket(root, plan, bus, tasks), rid="human-2"))
        self.assertEqual(bus.state()["status"], "completed")

    def test_respond_during_live_controller_is_queued_not_a_concurrent_state_write(self):
        plan, root, bus, tasks, _ = self.blocked()
        answer = response(self.ticket(root, plan, bus, tasks))
        result, failures = [], []
        def submit():
            try: result.append(human.respond(root, answer))
            except BaseException as exc: failures.append(exc)
        with coord.serial.run_lock(root):
            state = bus.state()
            thread = threading.Thread(target=submit); thread.start()
            deadline = time.monotonic() + 5
            while not human.receipt(root, answer["id"]) and time.monotonic() < deadline: time.sleep(.05)
            self.assertEqual(bus.state(), state)
            human.consume(root, plan, tasks, state, bus)
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures)
        self.assertEqual(result[0]["status"], "applied")

    def test_queued_response_survives_cancel_and_exhausted_attempts(self):
        plan, root, bus, tasks, _ = self.blocked()
        state = bus.state(); state["tasks"]["A"]["attempts"] = plan["limits"]["max_attempts"]; bus.save(state, "spent")
        answer = response(self.ticket(root, plan, bus, tasks))
        (root / "cancel.request").write_text("user stopped")
        with self.assertRaisesRegex(checks.Rejected, "cancel"):
            human.respond(root, answer)
        self.assertEqual(human.receipt(root, answer["id"])["status"], "queued")
        self.assertTrue((root / "cancel.request").exists())
        coord.main(["clear-cancel", "--run-dir", str(root)])
        human.respond(root, answer)
        self.assertEqual(bus.state()["tasks"]["A"]["attempts"], plan["limits"]["max_attempts"])
        self.assertEqual(bus.state()["tasks"]["A"]["status"], "failed")

    def test_manual_approval_does_not_override_failing_checks(self):
        plan, root, bus, tasks = self.manual()
        answer = response(self.ticket(root, plan, bus, tasks), "approve")
        original = coord.verify
        def fail(*args, **kwargs):
            records = original(*args, **kwargs)
            for row in records: row["result"] = "failed"
            return records
        with patch.object(coord, "verify", side_effect=fail):
            human.respond(root, answer)
        self.assertNotEqual(bus.state()["status"], "completed")
        self.assertEqual(bus.state()["integration_head"], plan["base_commit"])

    def test_review_invalidation_tracks_scope_changes_not_unrelated_files(self):
        plan, root, bus, tasks = self.manual()
        human.respond(root, response(self.ticket(root, plan, bus, tasks), "approve"))
        state = bus.state()
        workspace = coord.new_worktree(root, plan, "review-changes", state["integration_head"], "review-changes")
        (workspace / "other.txt").write_text("unrelated")
        coord.git(workspace, "add", "other.txt"); coord.git(workspace, "commit", "-m", "unrelated")
        head = coord.git(workspace, "rev-parse", "HEAD")
        self.assertTrue(human.review_valid(plan, tasks["A"], state, head))
        (workspace / "a.txt").write_text("changed reviewed content")
        coord.git(workspace, "add", "a.txt"); coord.git(workspace, "commit", "-m", "changed scope")
        head = coord.git(workspace, "rev-parse", "HEAD")
        self.assertFalse(human.review_valid(plan, tasks["A"], state, head))
        state["integration_head"] = head
        rows = coverage.report(plan, state, lambda tid: human.review_valid(plan, tasks[tid], state, head))
        self.assertEqual(rows[0]["status"], "pending")
        self.assertEqual(human.tickets(root, plan, tasks, state, bus)[0]["context"]["review_head"], head)

    def test_ai_cannot_release_a_deferred_human_question(self):
        plan, root, bus, tasks, mid = self.blocked()
        with self.assertRaisesRegex(checks.Rejected, "human decision pending"):
            coord.apply_decisions(plan, tasks, bus.state(), bus, {"actions": [{"type": "reply", "task_id": "A", "request_id": mid, "text": "Invented user answer"}]}, {mid})

    def test_response_takes_over_when_active_controller_exits_without_consuming(self):
        plan, root, bus, tasks, _ = self.blocked()
        answer = response(self.ticket(root, plan, bus, tasks))
        result, failures = [], []
        def submit():
            try: result.append(human.respond(root, answer))
            except BaseException as exc: failures.append(exc)
        with coord.serial.run_lock(root):
            thread = threading.Thread(target=submit); thread.start()
            deadline = time.monotonic() + 5
            while not human.receipt(root, answer["id"]) and time.monotonic() < deadline: time.sleep(.05)
        thread.join(30)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failures)
        self.assertEqual(result[0]["status"], "applied")
        self.assertEqual(bus.state()["status"], "completed")

    def test_rejection_invalidates_downstream_work_without_budget_reset(self):
        plan, root, bus, tasks = self.manual(count=3)
        ticket = self.ticket(root, plan, bus, tasks)
        rejection = response(ticket, "reject")
        state = bus.state()
        state["tasks"]["C"].update(status="integrated", attempts=1)
        # Use an existing dependent task's status to inspect transaction behavior.
        bus.save(state, "simulated_dependents")
        human.submit(root, rejection)
        human.consume(root, plan, tasks, state, bus)
        self.assertEqual(state["tasks"]["A"]["status"], "pending")
        self.assertEqual(state["tasks"]["C"]["status"], "pending")
        self.assertEqual(state["tasks"]["A"]["attempts"], 1)
        self.assertEqual(state["tasks"]["C"]["attempts"], 1)
        self.assertTrue(state["tasks"]["C"]["invalidated"])
        self.assertIn("api-ready", state["milestones"])
        self.assertTrue(Path(state["tasks"]["A"]["workspace"]).exists())

    def test_competing_answers_only_apply_once(self):
        plan, root, bus, tasks, _ = self.blocked()
        ticket = self.ticket(root, plan, bus, tasks)
        human.submit(root, response(ticket))
        human.submit(root, response(ticket, rid="human-2"))
        state = bus.state(); human.consume(root, plan, tasks, state, bus)
        self.assertEqual(human.receipt(root, "human-1")["status"], "applied")
        self.assertEqual(human.receipt(root, "human-2")["status"], "rejected")
        self.assertEqual(len([m for m in bus.messages() if m["kind"] == "human_answer"]), 1)

    def test_defer_cannot_be_resolved_in_same_ai_batch(self):
        plan, root = fixture(self.base)
        bus = Bus(root); state = bus.state()
        mid = bus.send("coordinator", "question", {"text": "Need user decision"}, sender="A")
        with self.assertRaisesRegex(checks.Rejected, "defer"):
            coord.apply_decisions(plan, coord.validate(plan), state, bus, {"actions": [
                {"type": "defer", "task_id": "A", "request_id": mid, "text": "Ask user"},
                {"type": "reply", "task_id": "A", "request_id": mid, "text": "AI guessed"}]}, {mid})
        self.assertEqual(len(bus.unresolved()), 1)

    def test_receipt_committed_before_responder_dies_still_resumes_on_duplicate(self):
        plan, root, bus, tasks, _ = self.blocked()
        answer = response(self.ticket(root, plan, bus, tasks))
        human.submit(root, answer)
        state = bus.state(); human.consume(root, plan, tasks, state, bus)
        self.assertEqual(state["tasks"]["A"]["status"], "pending")
        self.assertEqual(human.respond(root, answer)["status"], "applied")
        self.assertEqual(bus.state()["status"], "completed")
        self.assertEqual(bus.state()["tasks"]["A"]["attempts"], 2)

    def test_answer_after_time_budget_exhaustion_is_recorded_without_new_worker(self):
        plan, root, bus, tasks, _ = self.blocked()
        state = bus.state(); state["elapsed_seconds"] = plan["limits"]["max_run_seconds"]; bus.save(state, "spent_time")
        answer = response(self.ticket(root, plan, bus, tasks))
        with patch.object(coord, "supervised_worker") as worker:
            self.assertEqual(human.respond(root, answer)["status"], "applied")
            worker.assert_not_called()
        self.assertEqual(bus.state()["status"], "partial")
        self.assertEqual(bus.state()["tasks"]["A"]["attempts"], 1)

    def test_completed_run_rejects_a_response_queued_after_its_last_drain(self):
        plan, root, bus, tasks = self.manual()
        ticket = self.ticket(root, plan, bus, tasks)
        human.respond(root, response(ticket, "approve"))
        before = bus.state()
        late = response(ticket, "approve", "late-response")
        # Simulate acceptance just after the last queue SELECT, before the first
        # response committed, so this stale row survives completion of the run.
        with bus.connection() as db:
            db.execute("INSERT INTO human_responses(id,payload,status) VALUES(?,?,'queued')", (late["id"], encoded(late)))
        self.assertEqual(coord.run(root), 0)
        self.assertEqual(human.receipt(root, late["id"])["status"], "rejected")
        self.assertEqual(human.respond(root, late)["status"], "rejected")
        self.assertEqual(bus.state(), before)


if __name__ == "__main__":
    unittest.main()
