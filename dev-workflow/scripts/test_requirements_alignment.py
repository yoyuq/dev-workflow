"""Requirement alignment gates: fixture confirmations are simulated, never user evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import benchmark_fixtures as fixtures
import check_night_run as checks
import run_benchmarks
import workflow_coordinator as coordinator
import workflow_memory as memory
import workflow_runner as runner
import workflow_runtime as runtime


class AlignmentTests(unittest.TestCase):
    def record(self):
        return copy.deepcopy(fixtures.SIMULATED_ALIGNMENT)

    def test_missing_draft_pending_and_incomplete_records_are_rejected(self):
        variants = [None, {}, {**self.record(), "status":"draft"},
                    {**self.record(), "open_questions":["Which users should access this data?"]},
                    {**self.record(), "open_questions":None},
                    {**self.record(), "user_confirmation":{"reference":"", "quote":""}},
                    {**self.record(), "user_confirmation":{"reference":"user-message", "quote":True}},
                    {**self.record(), "acceptance":""},
                    {**self.record(), "method":"model-assumption"},
                    {**self.record(), "mode":"reuse"}]
        for value in variants:
            with self.subTest(record=value), self.assertRaises(checks.Rejected):
                checks.check_requirements_alignment({"requirements_alignment":value})

    def test_confirmed_record_and_traced_reuse_pass(self):
        record = self.record()
        self.assertEqual(checks.check_requirements_alignment({"requirements_alignment":record}), record)
        record.update(mode="reuse", reused_from="test-fixture://previous-confirmed-goal")
        self.assertEqual(checks.check_requirements_alignment({"requirements_alignment":record}), record)

    def test_user_requested_skip_still_needs_confirmation_evidence(self):
        record = self.record()
        record["mode"] = "user_requested_skip"
        del record["user_confirmation"]
        with self.assertRaises(checks.Rejected):
            checks.check_requirements_alignment({"requirements_alignment":record})
        record["user_confirmation"] = {"reference":"test-fixture://explicit-user-choice", "quote":"SIMULATED: skip the interview and use the supplied scope"}
        checks.check_requirements_alignment({"requirements_alignment":record})

    def test_both_controllers_refuse_missing_alignment_before_side_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = {"format_version":1,"harness":{"kind":"command","executable":sys.executable},
                      "limits":{"attempt_seconds":10,"no_progress_seconds":5,"max_output_bytes":10000}}
            memory.atomic_json(root / "runtime.json", {"worker":config})
            memory.atomic_json(root / "plan.json", {"format_version":2,"kind":"coordinated"})
            with patch.object(coordinator, "git") as git:
                with self.assertRaisesRegex(checks.Rejected, "requirements_alignment"):
                    coordinator.initialize(root / "plan.json", root / "runtime.json", root / "coordinated")
                git.assert_not_called()
            memory.atomic_json(root / "runtime.json", config)
            memory.atomic_json(root / "plan.json", {"format_version":4,"status":"ready","mode":"execute",
                "run_id":"missing-alignment","delivery":"branch_commit_and_report",
                "decision_record":"supplied plan","trusted_authorization_ref":"fixture authorization"})
            with patch.object(checks, "git_state") as git:
                with self.assertRaisesRegex(checks.Rejected, "requirements_alignment"):
                    runner.initialize(root / "plan.json", root / "runtime.json", root / "serial")
                git.assert_not_called()
            self.assertFalse((root / "coordinated").exists())
            self.assertFalse((root / "serial").exists())

    def test_draft_can_be_rendered_but_cannot_start(self):
        with tempfile.TemporaryDirectory() as temp:
            _, _, plan, _ = fixtures.prepare(Path(temp) / "fixture")
            plan["requirements_alignment"]["status"] = "draft"
            checks.validate_plan(plan, preparation=True)
            self.assertTrue(checks.render_execution_plan(plan))
            with self.assertRaisesRegex(checks.Rejected, "confirmed"):
                checks.validate_plan(plan)

    def test_workers_inherit_alignment_context(self):
        with tempfile.TemporaryDirectory() as temp:
            _, _, plan, _ = fixtures.prepare(Path(temp) / "fixture")
            card, _ = runtime.task_brief(plan, plan["tasks"][0], {"attempts":0}, Path(temp))
            self.assertEqual(card["requirements_alignment"], plan["requirements_alignment"])
            task = {"id":"A", "goal":"Fixture task", "contracts":[]}
            coordinated = {"goal":"Fixture goal", "requirements_alignment":self.record(),
                           "skill_root":temp,"tasks":[task]}
            prompt = coordinator.worker_prompt(coordinated, task, {"attempts":1,"history":[]},
                                                {"contracts":{},"milestones":{}})
            data = json.loads(prompt.split("Task card (data):\n",1)[1])
            self.assertEqual(data["requirements_alignment"], coordinated["requirements_alignment"])

    def test_real_benchmark_cannot_manufacture_fixture_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "real"
            with self.assertRaisesRegex(checks.Rejected, "requirements_alignment"):
                run_benchmarks.execute(output, "codex")
            self.assertFalse(output.exists())

    def test_bundled_examples_are_not_preconfirmed(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("night-run.template.json", "coordination-plan.example.json", "requirements-alignment.example.json"):
            with self.subTest(filename=filename):
                data = checks.read_json(root / "assets" / filename)
                if filename == "requirements-alignment.example.json":
                    data = {"requirements_alignment":data}
                with self.assertRaises(checks.Rejected):
                    checks.check_requirements_alignment(data)


if __name__ == "__main__":
    unittest.main()
