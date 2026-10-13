import copy
from pathlib import Path
import tempfile
import unittest

import check_night_run as checks
import workflow
import workflow_memory as memory
import workflow_runner as runner
import benchmark_fixtures
from test_workflow_coordinator import fixture
from test_workflow_review import REVIEWER


class EntrypointTests(unittest.TestCase):
    def setUp(self): self.tmp = tempfile.TemporaryDirectory()
    def tearDown(self): self.tmp.cleanup()

    def prepare(self):
        base = Path(self.tmp.name).resolve()
        plan, old = fixture(base)
        plan["run_id"] = "unified"
        memory.atomic_json(base / "new-plan.json", plan)
        return base, plan

    def test_quick_uses_shared_protocol(self):
        base, plan = self.prepare()
        root = base / "unified"
        workflow.initialize(base / "new-plan.json", base / "runtime.json", root, "quick")
        self.assertEqual(workflow.main(["run", "--run-dir", str(root)]), 0)
        snapshot = workflow.snapshot(root)
        self.assertEqual(snapshot["mode"], "quick")
        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["task_counts"], {"integrated": 1})
        self.assertEqual(checks.read_json(base / "new-plan.json"), plan)

    def test_full_requires_reviewer_before_branch_creation(self):
        base, plan = self.prepare()
        root = base / "unified"
        with self.assertRaisesRegex(checks.Rejected, "reviewer"):
            workflow.initialize(base / "new-plan.json", base / "runtime.json", root, "full")
        self.assertFalse(root.exists())

    def test_full_same_entry_with_independent_review(self):
        base, plan = self.prepare()
        path = base / "review.py"
        path.write_text(REVIEWER)
        configs = checks.read_json(base / "runtime.json")
        configs["reviewer"] = copy.deepcopy(configs["worker"])
        configs["reviewer"]["harness"]["args"] = [str(path), "approve"]
        memory.atomic_json(base / "new-runtime.json", configs)
        root = base / "unified"
        workflow.initialize(base / "new-plan.json", base / "new-runtime.json", root, "full")
        self.assertEqual(workflow.main(["run", "--run-dir", str(root)]), 0)
        self.assertEqual(workflow.snapshot(root)["budget"]["review_rounds_used"], 2)

    def test_serial_v4_dispatches_its_recorded_runner(self):
        base = Path(self.tmp.name).resolve()
        folder, repo, plan, config = benchmark_fixtures.prepare(base / "serial", skill_root=Path(workflow.__file__).resolve().parents[1])
        root = folder / "run"
        runner.initialize(folder / "plan.json", folder / "runtime.json", root)
        self.assertEqual(workflow.main(["run", "--run-dir", str(root)]), 0)
        self.assertEqual(checks.read_json(root / "state.json")["status"], "completed")


if __name__ == "__main__": unittest.main()
