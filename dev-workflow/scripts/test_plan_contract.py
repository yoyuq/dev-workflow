"""Acceptance coverage and plan consistency regressions; isolated local fixtures."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_check_night_run as fixtures
import check_night_run as checker

SCRIPT = fixtures.SCRIPT


class ContractTests(unittest.TestCase):
    setUp = fixtures.CheckerTests.setUp
    git = fixtures.CheckerTests.git
    commit = fixtures.CheckerTests.commit
    implemented = fixtures.CheckerTests.implemented
    prepare_document = fixtures.CheckerTests.prepare_document
    run_checker = fixtures.CheckerTests.run_checker
    rejected = fixtures.CheckerTests.rejected

    def add_consumer(self):
        module = copy.deepcopy(self.plan["modules"][0])
        module.update(id="M2", name="Consumer", depends_on=["M1"])
        self.plan["modules"].append(module)
        task = copy.deepcopy(self.plan["tasks"][0])
        task.update(id="T2", module_ids=["M2"], depends_on=["T1"], module_dependencies={"M1": ["T1"]})
        self.plan["tasks"].append(task)
        return task

    def add_manual(self):
        criterion = {"id": "visual", "description": "Design matches the selected direction",
                     "requirement_ids": ["R1"], "mode": "manual", "verification_ids": [],
                     "review_instructions": "User inspects the preview and confirms the design."}
        self.plan["tasks"][0]["acceptance_criteria"].append(criterion)
        return criterion

    def manual_report(self):
        report = self.implemented()
        report.update(status="partial", completed_task_ids=[], pending_tasks=[{
            "task_id": "T1", "status": "awaiting_review", "reason": "Visual judgment required",
            "next_action": "Review the preview in the morning"}])
        return report

    def test_added_uncovered_criterion_rejected(self):
        self.plan["tasks"][0]["acceptance_criteria"].append({
            "id": "missing-file", "description": "required.csv must exist", "requirement_ids": ["R1"],
            "mode": "automated", "verification_ids": []})
        self.rejected(self.implemented(), "verification_ids: must not be empty")

    def test_missing_requirements(self):
        del self.plan["requirements"]
        self.rejected(contains="requirements")

    def test_duplicate_requirement(self):
        self.plan["requirements"].append(copy.deepcopy(self.plan["requirements"][0]))
        self.rejected(contains="duplicate requirement")

    def test_requirement_without_acceptance(self):
        self.plan["requirements"].append({"id": "R2", "description": "another user outcome"})
        self.rejected(contains="every requirement")

    def test_unknown_requirement(self):
        self.plan["tasks"][0]["acceptance_criteria"][0]["requirement_ids"] = ["unknown"]
        self.rejected(contains="unknown requirement")

    def test_duplicate_criterion(self):
        criteria = self.plan["tasks"][0]["acceptance_criteria"]
        criteria.append(copy.deepcopy(criteria[0]))
        self.rejected(contains="duplicate acceptance criterion")

    def test_unknown_check_link(self):
        self.plan["tasks"][0]["acceptance_criteria"][0]["verification_ids"] = ["unknown"]
        self.rejected(contains="unknown verification")

    def test_unmapped_check(self):
        check = copy.deepcopy(self.plan["tasks"][0]["verification"][0])
        check["id"] = "unmapped"
        self.plan["tasks"][0]["verification"].append(check)
        self.rejected(contains="every verification must map")

    def test_all_checks_of_a_criterion_must_pass(self):
        report = self.implemented()
        self.plan["tasks"][0]["verification"].append({"id": "second", "argv": [sys.executable, "-c", "raise SystemExit(8)"],
                                                    "cwd": ".", "timeout_seconds": 5})
        self.plan["tasks"][0]["acceptance_criteria"][0]["verification_ids"].append("second")
        report["checks"].append({"task_id": "T1", "check_id": "second", "action": "check", "result": "passed", "evidence": "claim"})
        audit = self.rejected(report, "verification failed")
        criterion = audit["acceptance"][0]
        self.assertEqual(criterion["result"], "failed")
        self.assertEqual([c["exit_code"] for c in criterion["checks"]], [0, 8])

    def test_evidence_connects_requirement_criterion_log_and_commit(self):
        code, audit, output = self.run_checker(self.implemented())
        self.assertEqual(code, 0, audit)
        criterion = audit["acceptance"][0]
        self.assertEqual(criterion["requirement_ids"], ["R1"])
        self.assertEqual(criterion["criterion_id"], "AC1")
        self.assertEqual(criterion["result"], "passed")
        evidence = criterion["checks"][0]
        self.assertEqual(evidence["commit"], self.git("rev-parse", "HEAD"))
        self.assertEqual(evidence["log_sha256"], checker.digest(output / evidence["log"]))

    def test_manual_cannot_be_claimed_completed(self):
        self.add_manual()
        self.rejected(self.implemented(), "manual acceptance must remain awaiting_review")

    def test_manual_pending_runs_automated_checks_but_remains_incomplete(self):
        self.add_manual()
        code, audit, _ = self.run_checker(self.manual_report())
        self.assertEqual(code, 3, audit)
        self.assertFalse(audit["completed"])
        self.assertEqual(audit["completed_task_ids"], [])
        self.assertEqual(audit["awaiting_review_task_ids"], ["T1"])
        self.assertEqual([row["result"] for row in audit["acceptance"]], ["passed", "awaiting_review"])

    def test_manual_only_task_can_be_delivered_for_review(self):
        criterion = self.add_manual()
        self.plan["tasks"][0]["acceptance_criteria"] = [criterion]
        self.plan["tasks"][0]["verification"] = []
        report = self.manual_report()
        report["checks"] = []
        code, audit, _ = self.run_checker(report)
        self.assertEqual(code, 3, audit)
        self.assertEqual(audit["checks"], [])
        self.assertEqual(audit["acceptance"][0]["result"], "awaiting_review")

    def test_manual_mode_rejects_automatic_check(self):
        self.add_manual()["verification_ids"] = ["content"]
        self.rejected(contains="manual criterion cannot be passed by commands")

    def test_manual_mode_needs_review_instructions(self):
        del self.add_manual()["review_instructions"]
        self.rejected(contains="review_instructions")

    def test_automatic_failure_cannot_be_disguised_as_manual_pending(self):
        self.rejected(self.manual_report(), "awaiting_review requires a manual criterion")

    def test_depends_on_unreviewed_task_cannot_complete(self):
        self.add_consumer()
        self.add_manual()
        report = self.manual_report()
        report["completed_task_ids"] = ["T2"]
        report["checks"].append({"task_id": "T2", "check_id": "content", "action": "check", "result": "passed", "evidence": "claim"})
        self.rejected(report, "dependency was not completed")

    def test_module_dependency_requires_binding_to_task(self):
        task = self.add_consumer()
        task["module_dependencies"] = {}
        self.rejected(contains="bindings differ")

    def test_bound_producer_requires_task_dependency(self):
        task = self.add_consumer()
        task["depends_on"] = []
        self.rejected(contains="is not a task dependency")

    def test_bound_producer_must_belong_to_source_module(self):
        task = self.add_consumer()
        task["module_dependencies"] = {"M1": ["T2"]}
        self.rejected(contains="does not belong")

    def test_transitive_task_dependency_satisfies_module_binding(self):
        consumer = self.add_consumer()
        intermediate = copy.deepcopy(self.plan["tasks"][0])
        intermediate.update(id="Tmid", depends_on=["T1"])
        self.plan["tasks"].append(intermediate)
        consumer["depends_on"] = ["Tmid"]
        self.plan["tasks"].reverse()  # Input order need not be dependency order.
        code, audit, _ = self.run_checker()
        self.assertEqual(code, 0, audit)
        self.assertIn("T1 → Tmid → T2", Path(self.plan["execution_plan"]["path"]).read_text(encoding="utf-8"))

    def test_rehashed_but_inconsistent_document_rejected(self):
        self.sync_document = False
        path = Path(self.plan["execution_plan"]["path"])
        path.write_text("# Different plan\nExecute unrelated task Z99.\n", encoding="utf-8")
        self.plan["execution_plan"]["sha256"] = checker.digest(path)
        self.rejected(contains="content differs from prepared task data")

    def test_stale_document_after_scope_change_rejected(self):
        self.sync_document = False
        self.plan["tasks"][0]["allowed_scope"].append("new-area")
        self.rejected(contains="content differs from prepared task data")

    def test_renderer_creates_ready_to_pin_document_from_draft(self):
        self.plan["status"] = "draft"
        self.plan["execution_plan"] = {}
        plan_path = self.root / "draft.json"
        plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        before = plan_path.read_bytes()
        output = self.root / "generated.md"
        command = [sys.executable, str(SCRIPT), "render-plan", "--plan", str(plan_path), "--output", str(output)]
        result = subprocess.run(command, capture_output=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.plan["execution_plan"] = json.loads(result.stdout)
        self.assertEqual(self.plan["execution_plan"]["sha256"], checker.digest(output))
        self.assertEqual(plan_path.read_bytes(), before)
        self.plan["status"] = "ready"
        self.sync_document = False
        code, audit, _ = self.run_checker()
        self.assertEqual(code, 0, audit)
        result = subprocess.run(command, capture_output=True, encoding="utf-8", timeout=20)
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
