"""Behavior tests using temporary Git repositories, no model/network calls."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import check_night_run as checker
from benchmark_fixtures import SIMULATED_ALIGNMENT


SCRIPT = Path(checker.__file__).resolve()


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow-check-", dir=os.environ.get("WORKFLOW_TEST_TMP"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "--template=", "--initial-branch=main")
        self.git("config", "user.name", "Workflow Test")
        self.git("config", "user.email", "workflow-test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        (self.repo / "app.txt").write_text("before\n", encoding="utf-8")
        self.commit("baseline")
        base = self.git("rev-parse", "HEAD")
        self.git("switch", "-c", "night/test")
        skill = self.root / "example-skill"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: example\ndescription: fixture\n---\n", encoding="utf-8")
        document = self.root / "execution-plan.md"
        document.write_text("# Fixture execution plan\n\n## Modules\nM1: app content, input app.txt, output updated text.\n\n## Execution\nT1 (M1): change app.txt, then assert the expected content.\n", encoding="utf-8")
        self.plan = {
            "format_version": 4, "run_id": "test-run", "status": "ready", "mode": "execute",
            "requirements_alignment": copy.deepcopy(SIMULATED_ALIGNMENT),
            "requirements": [{"id": "R1", "description": "app.txt contains after followed by a newline"}],
            "execution_plan": {"path": str(document), "sha256": checker.digest(document)},
            "modules": [{"id": "M1", "name": "Application text", "responsibility": "Own app content",
                         "inputs": ["app.txt"], "outputs": ["Updated app.txt"], "depends_on": []}],
            "project": {"repository": str(self.repo), "base_ref": "main", "base_commit": base,
                        "work_branch": "night/test", "frontend_surface": "none", "backend_stack": "none"},
            "decision_record": "fixture decisions", "trusted_authorization_ref": "fixture authorization",
            "skill_versions": {"example": {"path": str(skill), "sha256": checker.skill_digest(skill)}},
            "limits": {"max_run_minutes": 1, "max_tasks": 3, "max_attempts_per_task": 2, "cost_limit": None},
            "delivery": "branch_commit_and_report",
            "tasks": [{"id": "T1", "module_ids": ["M1"], "module_dependencies": {}, "goal": "change app text", "status": "ready", "depends_on": [],
                       "allowed_scope": ["app.txt"], "required_skills": ["example"],
                       "acceptance_criteria": [{"id": "AC1", "description": "app.txt contains after followed by a newline",
                                                "requirement_ids": ["R1"], "mode": "automated", "verification_ids": ["content"]}], "blockers": [],
                       "verification": [{"id": "content", "cwd": ".", "timeout_seconds": 5,
                                         "argv": [sys.executable, "-c", "from pathlib import Path; assert Path('app.txt').read_text() == 'after\\n'; print('verified actual content')"]}]}]
        }
        self.counter = 0
        self.sync_document = True
        self.prepare_document()

    def prepare_document(self):
        document = Path(self.plan["execution_plan"]["path"])
        document.write_text(checker.render_execution_plan(self.plan), encoding="utf-8", newline="\n")
        self.plan["execution_plan"]["sha256"] = checker.digest(document)

    def git(self, *args):
        result = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def commit(self, message):
        self.git("add", "--all")
        self.git("-c", "core.hooksPath=", "commit", "-m", message)

    def implemented(self):
        (self.repo / "app.txt").write_text("after\n", encoding="utf-8")
        self.commit("implement task")
        return {"run_id": "test-run", "status": "completed", "branch": "night/test",
                "commits": self.git("rev-list", "--reverse", self.plan["project"]["base_commit"] + "..HEAD").splitlines(),
                "completed_task_ids": ["T1"],
                "checks": [{"task_id": "T1", "check_id": "content", "action": "check contents",
                            "result": "passed", "evidence": "agent's claim; independently rerun by checker"}],
                "pending_tasks": [], "summary": "implemented text change"}

    def run_checker(self, report=None, pinned_hash=None):
        self.counter += 1
        if self.sync_document:
            try:
                checker.validate_plan(self.plan, preparation=True)
            except checker.Rejected:
                pass  # Deliberately invalid plans must be rejected by the CLI.
            else:
                self.prepare_document()
        plan_file = self.root / "plan.json"
        plan_file.write_text(json.dumps(self.plan, ensure_ascii=False), encoding="utf-8")
        output = self.root / f"audit-{self.counter}"
        command = [sys.executable, str(SCRIPT), "verify" if report is not None else "preflight",
                   "--plan", str(plan_file), "--plan-sha256", pinned_hash or checker.digest(plan_file),
                   "--output-dir", str(output)]
        if report is not None:
            report_file = self.root / "report.json"
            report_file.write_text(json.dumps(report), encoding="utf-8")
            command.extend(["--report", str(report_file)])
        result = subprocess.run(command, capture_output=True, encoding="utf-8", timeout=25,
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertIn(result.returncode, (0, 1, 3), result.stderr)
        audit = json.loads(result.stdout)
        if (output / "audit.json").exists():
            self.assertEqual(json.loads((output / "audit.json").read_text(encoding="utf-8")), audit)
        return result.returncode, audit, output

    def rejected(self, report=None, contains=None):
        code, audit, _ = self.run_checker(report)
        self.assertEqual(code, 1, audit)
        self.assertFalse(audit["completed"])
        if contains:
            self.assertIn(contains, " ".join(audit["errors"]))
        return audit

    def test_valid_preflight_does_not_execute_tests(self):
        code, audit, _ = self.run_checker()
        self.assertEqual(code, 0, audit)
        self.assertEqual(audit["checks"], [])
        self.assertFalse(audit["completed"])

    def test_valid_delivery_reruns_test_and_saves_hash_bound_log(self):
        code, audit, output = self.run_checker(self.implemented())
        self.assertEqual(code, 0, audit)
        self.assertTrue(audit["completed"])
        record = audit["checks"][0]
        log = output / record["log"]
        self.assertIn("verified actual content", log.read_text())
        self.assertEqual(record["log_sha256"], hashlib.sha256(log.read_bytes()).hexdigest())
        self.assertEqual(record["exit_code"], 0)
        self.assertEqual(record["commit"], self.git("rev-parse", "HEAD"))

    def test_draft_plan(self):
        self.plan["status"] = "draft"
        self.rejected(contains="status=ready")

    def test_legacy_plan_cannot_skip_planning(self):
        self.plan["format_version"] = 2
        self.rejected(contains="format_version must be 4")

    def test_missing_execution_plan(self):
        del self.plan["execution_plan"]
        self.rejected(contains="execution_plan")

    def test_unwritten_execution_plan(self):
        self.sync_document = False
        Path(self.plan["execution_plan"]["path"]).unlink()
        self.rejected()

    def test_empty_execution_plan(self):
        self.sync_document = False
        path = Path(self.plan["execution_plan"]["path"])
        path.write_text(" \n", encoding="utf-8")
        self.plan["execution_plan"]["sha256"] = checker.digest(path)
        self.rejected(contains="document is empty")

    def test_execution_plan_tamper(self):
        self.sync_document = False
        Path(self.plan["execution_plan"]["path"]).write_text("Different plan", encoding="utf-8")
        self.rejected(contains="document SHA-256 mismatch")

    def test_execution_plan_tamper_during_verification(self):
        report = self.implemented()
        path = self.plan["execution_plan"]["path"]
        self.plan["tasks"][0]["verification"][0]["argv"] = [sys.executable, "-c", f"from pathlib import Path; Path({path!r}).write_text('changed')"]
        self.rejected(report, "document SHA-256 mismatch")

    def test_execution_plan_inside_checkout_rejected(self):
        self.sync_document = False
        path = self.repo / "execution-plan.md"
        path.write_text("Plan inside AI checkout", encoding="utf-8")
        self.commit("store plan")
        self.plan["project"]["base_commit"] = self.git("rev-parse", "HEAD")
        self.plan["execution_plan"] = {"path": str(path), "sha256": checker.digest(path)}
        self.rejected(contains="execution plan snapshot outside")

    def test_empty_module_decomposition(self):
        self.plan["modules"] = []
        self.rejected(contains="modules: must not be empty")

    def test_duplicate_module(self):
        self.plan["modules"].append(copy.deepcopy(self.plan["modules"][0]))
        self.rejected(contains="duplicate module")

    def test_module_dependency_cycle(self):
        self.plan["modules"][0]["depends_on"] = ["M1"]
        self.rejected(contains="module dependency cycle")

    def test_unknown_module_dependency(self):
        self.plan["modules"][0]["depends_on"] = ["missing"]
        self.rejected(contains="unknown module dependency")

    def test_task_without_module(self):
        self.plan["tasks"][0]["module_ids"] = []
        self.rejected(contains="module_ids: must not be empty")

    def test_task_with_unknown_module(self):
        self.plan["tasks"][0]["module_ids"] = ["missing"]
        self.rejected(contains="unknown module")

    def test_module_without_task(self):
        second = copy.deepcopy(self.plan["modules"][0])
        second["id"] = "M2"
        self.plan["modules"].append(second)
        self.rejected(contains="every module must have")

    def test_valid_multi_module_plan(self):
        second = copy.deepcopy(self.plan["modules"][0])
        second.update(id="M2", name="Integration", depends_on=["M1"])
        self.plan["modules"].append(second)
        task = copy.deepcopy(self.plan["tasks"][0])
        task.update(id="T2", module_ids=["M2"], depends_on=["T1"], module_dependencies={"M1": ["T1"]})
        self.plan["tasks"].append(task)
        code, audit, _ = self.run_checker()
        self.assertEqual(code, 0, audit)

    def test_empty_plan(self):
        self.plan["tasks"] = []
        self.rejected(contains="must not be empty")

    def test_duplicate_task(self):
        self.plan["tasks"].append(copy.deepcopy(self.plan["tasks"][0]))
        self.rejected(contains="duplicate task")

    def test_dependency_cycle(self):
        self.plan["tasks"][0]["depends_on"] = ["T1"]
        self.rejected(contains="cycle")

    def test_unknown_dependency(self):
        self.plan["tasks"][0]["depends_on"] = ["missing"]
        self.rejected(contains="unknown dependency")

    def test_changed_skill_support_file(self):
        skill = Path(self.plan["skill_versions"]["example"]["path"])
        (skill / "helper.py").write_text("print('new content')")
        self.rejected(contains="skill missing or changed")

    def test_unpinned_skill(self):
        self.plan["skill_versions"] = {}
        self.rejected(contains="unpinned required skill")

    def test_wrong_baseline(self):
        self.implemented()
        self.rejected(contains="differs from pinned base_commit")

    def test_dirty_worktree(self):
        (self.repo / "untracked.txt").write_text("leftover")
        self.rejected(contains="uncommitted or untracked")

    def test_wrong_branch(self):
        self.git("switch", "main")
        self.rejected(contains="wrong branch")

    def test_pinned_plan_tamper(self):
        code, audit, _ = self.run_checker(pinned_hash="0" * 64)
        self.assertEqual(code, 1)
        self.assertIn("SHA-256 mismatch", str(audit["errors"]))

    def test_path_traversal_in_scope(self):
        self.plan["tasks"][0]["allowed_scope"] = ["../other"]
        self.rejected(contains="repository-relative")

    def test_path_traversal_in_cwd(self):
        self.plan["tasks"][0]["verification"][0]["cwd"] = "../"
        self.rejected(contains="repository-relative")

    def test_missing_executable(self):
        self.plan["tasks"][0]["verification"][0]["argv"] = ["no-such-workflow-test-program"]
        self.rejected(contains="program unavailable")

    def test_empty_completed_report(self):
        report = self.implemented()
        report.update(completed_task_ids=[], checks=[], commits=[], branch=None)
        self.rejected(report)

    def test_fake_commit(self):
        report = self.implemented()
        report["commits"] = ["0" * 40]
        self.rejected(report, "commits differ")

    def test_missing_check(self):
        report = self.implemented()
        report["checks"] = []
        self.rejected(report, "missing passed check")

    def test_real_test_failure_overrides_passed_claim(self):
        report = self.implemented()
        self.plan["tasks"][0]["verification"][0]["argv"] = [sys.executable, "-c", "raise SystemExit(7)"]
        audit = self.rejected(report, "verification failed")
        self.assertEqual(audit["checks"][0]["exit_code"], 7)

    def test_timeout_overrides_passed_claim(self):
        report = self.implemented()
        check = self.plan["tasks"][0]["verification"][0]
        check.update(argv=[sys.executable, "-c", "import time; time.sleep(30)"], timeout_seconds=1)
        audit = self.rejected(report, "verification failed")
        self.assertTrue(audit["checks"][0]["timed_out"])

    def test_out_of_scope_change(self):
        report = self.implemented()
        (self.repo / "unrelated.txt").write_text("unexpected")
        self.commit("unrelated modification")
        report["commits"].append(self.git("rev-parse", "HEAD"))
        self.rejected(report, "out-of-scope")

    def test_out_of_scope_change_then_revert_still_rejected(self):
        report = self.implemented()
        (self.repo / "unrelated.txt").write_text("unexpected")
        self.commit("temporary unrelated modification")
        report["commits"].append(self.git("rev-parse", "HEAD"))
        (self.repo / "unrelated.txt").unlink()
        self.commit("remove unrelated modification")
        report["commits"].append(self.git("rev-parse", "HEAD"))
        self.rejected(report, "out-of-scope")

    def test_verification_modifies_tracked_file(self):
        report = self.implemented()
        self.plan["tasks"][0]["verification"][0]["argv"] = [sys.executable, "-c", "from pathlib import Path; Path('app.txt').write_text('changed by test')"]
        self.rejected(report, "uncommitted or untracked")

    def test_partial_is_not_full_success(self):
        report = self.implemented()
        second = copy.deepcopy(self.plan["tasks"][0])
        second.update(id="T2", goal="future work", status="blocked", blockers=["needs decision"])
        self.plan["tasks"].append(second)
        report.update(status="partial", pending_tasks=[{"task_id": "T2", "status": "blocked",
                                                       "reason": "needs decision", "next_action": "ask in morning"}])
        code, audit, _ = self.run_checker(report)
        self.assertEqual(code, 3, audit)
        self.assertEqual(audit["status"], "incomplete")
        self.assertFalse(audit["completed"])

    def test_blocked_task_missing_program_does_not_block_independent_work(self):
        second = copy.deepcopy(self.plan["tasks"][0])
        second.update(id="T2", status="blocked", blockers=["needs unavailable tool"])
        second["verification"][0]["argv"] = ["no-such-workflow-test-program"]
        self.plan["tasks"].append(second)
        code, audit, _ = self.run_checker()
        self.assertEqual(code, 0, audit)
        report = self.implemented()
        report.update(status="partial", pending_tasks=[{"task_id": "T2", "status": "blocked",
                                                       "reason": "missing tool", "next_action": "install in preparation"}])
        code, audit, _ = self.run_checker(report)
        self.assertEqual(code, 3, audit)

    def test_completed_dependency_missing(self):
        report = self.implemented()
        second = copy.deepcopy(self.plan["tasks"][0])
        second.update(id="T2")
        self.plan["tasks"].append(second)
        self.plan["tasks"][0]["depends_on"] = ["T2"]
        report.update(status="partial", pending_tasks=[{"task_id": "T2", "status": "failed",
                                                       "reason": "failed", "next_action": "repair"}])
        self.rejected(report, "dependency was not completed")

    def test_fully_blocked_report_is_incomplete_and_runs_no_commands(self):
        report = {"run_id": "test-run", "status": "blocked", "branch": "night/test", "commits": [],
                  "completed_task_ids": [], "checks": [],
                  "pending_tasks": [{"task_id": "T1", "status": "blocked", "reason": "dependency unavailable",
                                     "next_action": "fix environment"}], "summary": "blocked before implementation"}
        code, audit, _ = self.run_checker(report)
        self.assertEqual(code, 3, audit)
        self.assertFalse(audit["completed"])
        self.assertEqual(audit["checks"], [])

    def test_completion_with_no_new_commit(self):
        report = {"run_id": "test-run", "status": "completed", "branch": "night/test", "commits": [],
                  "completed_task_ids": ["T1"], "checks": [], "pending_tasks": [], "summary": "fake done"}
        self.rejected(report, "require actual branch commits")

    def test_unknown_completed_task(self):
        report = self.implemented()
        report["completed_task_ids"].append("invented")
        self.rejected(report, "unknown completed task")

    def test_malformed_report_returns_failure_json(self):
        report = self.implemented()
        report["status"] = []
        self.rejected(report)

    def test_report_action_is_never_executed(self):
        report = self.implemented()
        report["checks"][0]["action"] = "this is not a valid executable"
        code, audit, _ = self.run_checker(report)
        self.assertEqual(code, 0, audit)


if __name__ == "__main__":
    unittest.main(verbosity=2)
