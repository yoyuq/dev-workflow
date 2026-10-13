"""Behavioral tests: real subprocesses, Git commits and checks; simulated model output."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

import benchmark_fixtures as fixtures
import workflow_runner as runner
import workflow_memory as memory
import check_night_run as checks


class RunnerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="workflow-runner-", dir=os.environ.get("WORKFLOW_TEST_TMP"))
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name).resolve()

    def prepare(self, case="bugfix", behavior="success"):
        self.root, self.repo, self.plan, self.config = fixtures.prepare(self.base / "case", case, behavior)
        self.run_dir = self.root / "run"

    def init(self):
        fixtures.repin(self.root, self.plan, self.config)
        with redirect_stdout(io.StringIO()):
            runner.initialize(self.root / "plan.json", self.root / "runtime.json", self.run_dir)

    def run_case(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            result = runner.run(self.run_dir, **kwargs)
        return result, memory.read_json(self.run_dir / "state.json")

    def test_bugfix_is_committed_only_after_behavioral_checks(self):
        self.prepare(); self.init()
        code, state = self.run_case()
        self.assertEqual(code, 0, state)
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["tasks"]["T1"]["attempts"], 1)
        self.assertFalse(checks.git(self.repo, "status", "--porcelain"))
        self.assertEqual(len(memory.read_json(self.run_dir / "delivery.json")["commits"]), 1)

    def test_failed_check_is_fed_back_and_second_attempt_repairs(self):
        self.prepare(behavior="fail_then_fix"); self.init()
        code, state = self.run_case()
        self.assertEqual(code, 0, state)
        self.assertEqual(state["tasks"]["T1"]["attempts"], 2)
        self.assertEqual(state["tasks"]["T1"]["history"][0]["checks"][0]["result"], "failed")
        self.assertEqual(len(memory.records(self.run_dir / "memory")), 1)
        card = memory.read_json(self.run_dir / "attempts/T1/task-card-2.json")
        self.assertIn("AssertionError", card["previous_attempt"]["failed_checks"][0]["log_tail_untrusted_data"])

    def test_resume_does_not_repeat_accepted_dependency(self):
        self.prepare("resume"); self.init()
        code, first = self.run_case(max_tasks=1)
        self.assertEqual(code, 3, first)
        self.assertEqual(first["tasks"]["T1"]["status"], "done")
        self.assertEqual(first["tasks"]["T2"]["attempts"], 0)
        code, final = self.run_case()
        self.assertEqual(code, 0, final)
        self.assertEqual(final["tasks"]["T1"]["attempts"], 1)
        self.assertGreater(final["elapsed_seconds"], first["elapsed_seconds"])
        self.assertEqual(len(memory.read_json(self.run_dir / "delivery.json")["commits"]), 2)

    def test_false_completion_claim_is_rejected(self):
        self.prepare(behavior="false_done"); self.init()
        code, state = self.run_case()
        self.assertNotEqual(code, 0)
        self.assertEqual(state["tasks"]["T1"]["status"], "failed")
        self.assertEqual(checks.git(self.repo, "rev-parse", "HEAD"), self.plan["project"]["base_commit"])

    def test_lively_output_without_artifact_times_out(self):
        self.prepare(behavior="thinking")
        # Allow interpreter startup on a loaded Windows host before testing event-only liveness.
        self.config["limits"].update(no_progress_seconds=5, attempt_seconds=20)
        self.plan["limits"]["max_attempts_per_task"] = 1
        self.init(); code, state = self.run_case()
        process = state["tasks"]["T1"]["history"][0]["process"]
        self.assertNotEqual(code, 0)
        self.assertEqual(process["reason"], "no_progress")
        self.assertGreater(process["event_count"], 0)
        self.assertIsNone(process["first_artifact_seconds"])

    def test_missing_runtime_blocks_before_consuming_attempt(self):
        self.prepare(); self.config["harness"]["executable"] = str(self.base / "missing.exe")
        self.init(); code, state = self.run_case()
        self.assertEqual(code, 2)
        self.assertEqual(state["status"], "blocked_environment")
        self.assertEqual(state["tasks"]["T1"]["attempts"], 0)

    def test_manual_criterion_keeps_dependent_task_waiting(self):
        self.prepare("manual"); self.init(); code, state = self.run_case()
        self.assertEqual(code, 3, state)
        self.assertEqual(state["status"], "awaiting_review")
        self.assertEqual(state["tasks"]["T1"]["status"], "awaiting_review")
        self.assertEqual(state["tasks"]["T2"]["attempts"], 0)

    def test_later_task_regression_prevents_its_commit(self):
        self.prepare("regression", "regression"); self.init(); code, state = self.run_case()
        self.assertNotEqual(code, 0)
        self.assertEqual(state["tasks"]["T1"]["status"], "done")
        self.assertEqual(state["tasks"]["T2"]["status"], "failed")
        self.assertEqual(checks.git(self.repo, "rev-list", "--count", self.plan["project"]["base_commit"] + "..HEAD"), "1")
        failed = state["tasks"]["T2"]["history"][0]["checks"]
        self.assertTrue(any(c["task_id"] == "T1" and c["result"] == "failed" for c in failed))

    def test_scope_violation_preserves_files_without_committing(self):
        self.prepare(behavior="scope_escape"); self.init(); code, state = self.run_case()
        self.assertEqual(code, 2)
        self.assertIn("outside", state["error"])
        self.assertTrue((self.repo / "outside.txt").exists())
        self.assertEqual(checks.git(self.repo, "rev-parse", "HEAD"), self.plan["project"]["base_commit"])

    def test_protected_checker_drift_is_rejected(self):
        self.prepare(); self.init()
        (self.root / "frozen_check.py").write_text("print('fake pass')", encoding="utf-8")
        with self.assertRaisesRegex(checks.Rejected, "protected input changed"):
            self.run_case()

    def test_cancellation_and_explicit_resume(self):
        self.prepare(); self.init()
        (self.run_dir / "cancel.request").write_text("cancel", encoding="utf-8")
        code, state = self.run_case()
        self.assertNotEqual(code, 0)
        self.assertEqual(state["tasks"]["T1"]["attempts"], 0)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(["clear-cancel", "--run-dir", str(self.run_dir)]), 0)
        code, state = self.run_case()
        self.assertEqual(code, 0, state)

    def test_reconcile_keeps_partial_scope_and_retry_budget(self):
        self.prepare(); self.init()
        state = memory.read_json(self.run_dir / "state.json")
        state["tasks"]["T1"].update(status="running", attempts=1, baseline_workspace=state["expected_workspace"])
        memory.atomic_json(self.run_dir / "state.json", state)
        (self.repo / "expense.py").write_text("# interrupted partial implementation", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            runner.reconcile(self.run_dir, "T1", self.plan["project"]["base_commit"], "Inspected in-scope partial work")
        code, state = self.run_case()
        self.assertEqual(code, 0, state)
        self.assertEqual(state["tasks"]["T1"]["attempts"], 2)

    def test_earlier_error_event_cannot_be_erased_by_final_success(self):
        self.prepare(behavior="error_then_success_event"); self.init(); code, state = self.run_case()
        self.assertNotEqual(code, 0)
        self.assertTrue(state["tasks"]["T1"]["history"][0]["process"]["harness_error"])

    def test_non_enforceable_dollar_budget_is_not_silently_accepted(self):
        self.prepare(); self.plan["limits"]["cost_limit"] = 1
        with self.assertRaisesRegex(checks.Rejected, "dollar cap"):
            self.init()

    def test_elapsed_budget_is_not_reset_by_resume(self):
        self.prepare(); self.init()
        state = memory.read_json(self.run_dir / "state.json")
        state["elapsed_seconds"] = 121
        memory.atomic_json(self.run_dir / "state.json", state)
        code, state = self.run_case()
        self.assertNotEqual(code, 0)
        self.assertEqual(state["tasks"]["T1"]["attempts"], 0)
        self.assertEqual(state["final_verification"]["reason"], "run_budget_exhausted")

    def test_quota_failure_stops_queue_without_repeating_model_calls(self):
        self.prepare("fullstack", "quota"); self.init(); code, state = self.run_case()
        self.assertNotEqual(code, 0)
        self.assertEqual(state["status"], "blocked_environment")
        self.assertEqual(state["tasks"]["T1"]["attempts"], 1)
        self.assertEqual(state["tasks"]["T2"]["attempts"], 0)
        self.assertEqual(state["tasks"]["T1"]["reason"], "environment_failure: account_quota")

    def test_unassessed_layers_are_not_reported_as_passed(self):
        self.prepare(); self.init(); code, state = self.run_case()
        self.assertEqual(code, 0, state)
        layers = memory.read_json(self.run_dir / "layered-acceptance.json")["layers"]
        self.assertEqual(layers["functional"]["status"], "passed")
        self.assertEqual(layers["visual"]["status"], "not_assessed")

    def test_final_verifier_obeys_remaining_budget(self):
        self.prepare(); self.init()
        with redirect_stdout(io.StringIO()):
            result = runner.final_verify(self.run_dir, 0)
        self.assertEqual(result["reason"], "run_budget_exhausted")
        self.assertEqual(list(self.run_dir.glob("final-verification-*")), [])


if __name__ == "__main__":
    unittest.main()
