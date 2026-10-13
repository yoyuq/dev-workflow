import copy
from pathlib import Path
import tempfile
import unittest

import check_night_run as checks
import workflow_coordinator as coord
import workflow_memory as memory
from workflow_bus import Bus
from test_workflow_coordinator import fixture
from test_workflow_replan import repin_fixture
from test_workflow_review import REVIEWER

WORKER = r'''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import workflow_message as msg
if '--version' in sys.argv: print('simulated-learning-worker');raise SystemExit(0)
prompt=sys.stdin.read()
card=json.loads(prompt.split('Task card (data):\n')[1])
msg.read('.',True)
Path('a.txt').write_text('BAD' if card['attempt']==1 else 'OK')
if card['failure_cases'] and sys.argv[2]!='no_diagnosis':
    diagnosis={'case_id':card['failure_cases'][-1]['id'],'root_cause':'The output literal was BAD instead of the confirmed OK contract',
      'fix':'Changed the literal to OK','prevention':'Check the confirmed output contract before selecting a literal','check_ids':['behavior']}
    Path('.workflow-mailbox/learning.json').write_text(json.dumps({'diagnoses':[diagnosis],'feedback':[]}))
msg.read('.',True)
'''


class LearningTests(unittest.TestCase):
    def setUp(self): self.tmp = tempfile.TemporaryDirectory()
    def tearDown(self): self.tmp.cleanup()

    def execute(self, reviewer=True, diagnosis=True):
        base = Path(self.tmp.name).resolve()
        plan, root = fixture(base)
        (base / "worker.py").write_text(WORKER)
        configs = checks.read_json(root / "runtime.json")
        configs.pop("coordinator")
        configs["worker"]["harness"]["args"][-1] = "diagnose" if diagnosis else "no_diagnosis"
        if reviewer:
            path = base / "reviewer.py"
            path.write_text(REVIEWER.replace("if mode=='mutate':", "r['lesson_assessments']=[{'case_id':d['case_id'],'decision':'supported','reason':'Simulation: failure, changed literal and matching passing check'} for d in c['learning_proposals'].get('diagnoses',[])]\nif mode=='mutate':"))
            configs["reviewer"] = copy.deepcopy(configs["worker"])
            configs["reviewer"]["harness"]["args"] = [str(path), "approve"]
        repin_fixture(root, plan, configs)
        self.assertEqual(coord.run(root), 0)
        self.assertNotIn("learning_error", Bus(root).state())
        return root, plan, memory.records(root / "memory")

    def test_failure_fix_review_recall_and_contradiction(self):
        root, plan, records = self.execute()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "validated")
        self.assertTrue(records[0]["recommendation_eligible"])
        context = records[0]["scope"]["match"]
        self.assertEqual(len(memory.recall(root / "memory", context=context)), 1)
        self.assertEqual(memory.recall(root / "memory", context={**context, "project": "different"}), [])
        evidence = records[0]["evidence"][0]["path"]
        memory.feedback(root / "memory", {"lesson_id": records[0]["id"], "outcome": "contradicted", "note": "New case invalidates this advice", "code_ref": "new-case", "evidence": [evidence]})
        self.assertEqual(memory.recall(root / "memory", context=context), [])

    def test_success_without_independent_diagnosis_support_stays_candidate(self):
        root, plan, records = self.execute(reviewer=False)
        self.assertEqual(records[0]["status"], "candidate")
        self.assertFalse(records[0]["recommendation_eligible"])

    def test_success_alone_does_not_promote_and_evidence_drift_is_filtered(self):
        root, plan, records = self.execute(diagnosis=False)
        self.assertEqual(records[0]["status"], "unresolved")
        path = root / "memory" / records[0]["evidence"][0]["path"]
        path.write_text("changed")
        self.assertEqual(memory.recall(root / "memory", context=records[0]["scope"]["match"]), [])


if __name__ == "__main__": unittest.main()
