"""Isolated deterministic replan behavior; no model or real human responses."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest

import check_night_run as checks
import workflow_coordinator as coord
import workflow_memory as memory
import workflow_replan as replan
from workflow_bus import Bus
from test_workflow_coordinator import fixture


def repin_fixture(root, plan, configs=None):
    """Test setup only, before execution; never a migration API."""
    memory.atomic_json(root / "plan.json", plan)
    if configs is not None:
        memory.atomic_json(root / "runtime.json", configs)
    pins = checks.read_json(root / "pins.json")
    pins.update(plan=checks.digest(root / "plan.json"), runtime=checks.digest(root / "runtime.json"))
    pins["skill"] = checks.skill_digest(Path(plan["skill_root"]))
    pins["protected"] = {p: checks.digest(Path(p)) for p in pins["protected"]}
    for c in plan.get("replanning", {}).get("preparation_checks", []):
        for p in c["argv"][1:]:
            if Path(p).is_absolute() and Path(p).is_file():
                pins["protected"][p] = checks.digest(Path(p))
    memory.atomic_json(root / "pins.json", pins)


class ReplanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.plan, self.root = fixture(self.base)
        self.plan["tasks"][0]["allowed_scope"].append("prep.txt")
        preparation = self.base / "preparation.py"
        preparation.write_text("from pathlib import Path\nimport sys\nassert (Path(sys.argv[1])/'prep.txt').read_text()=='prepared'\n")
        self.plan["replanning"] = {"max_revisions": 3, "max_added_tasks": 2, "max_total_attempts": 4,
            "preparation_checks": [{"id": "prepared", "argv": [sys.executable, str(preparation), "{repo}"], "timeout_seconds": 5}]}
        worker = self.base / "worker.py"
        worker.write_text(worker.read_text().replace("if mode == 'peer':", "if tid == 'PREP':\n    Path('prep.txt').write_text('prepared')\nelif mode == 'peer':"))
        repin_fixture(self.root, self.plan)
        self.bus = Bus(self.root)
        self.state = self.bus.state()
        self.proposal = {"id": "split-1", "expected_revision": 0, "reason": "Prepare shared fixture before implementation",
            "add": [{"id": "PREP", "parent": "A", "goal": "prepare input", "allowed_scope": ["prep.txt"], "checks": ["prepared"], "depends_on": []}], "dependencies": {}}

    def tearDown(self):
        self.tmp.cleanup()

    def activate(self, proposal=None):
        plan, _, _ = coord.load(self.root)
        return replan.activate(self.plan, plan, self.state, self.bus, proposal or self.proposal, coord.validate)

    def test_split_runs_and_original_requirements_stay_pinned(self):
        pins = checks.digest(self.root / "pins.json")
        updated = self.activate()
        self.assertEqual(updated["requirements"], self.plan["requirements"])
        self.assertEqual(self.activate(), updated)  # idempotent replay
        self.assertEqual(coord.run(self.root), 0)
        state = self.bus.state()
        self.assertEqual({e["status"] for e in state["tasks"].values()}, {"integrated"})
        self.assertEqual(checks.digest(self.root / "pins.json"), pins)
        self.assertEqual(coord.git(self.base / "repo", "rev-parse", "main"), self.plan["base_commit"])

    def test_chinese_title_is_preserved_in_hashed_revision(self):
        self.proposal['add'][0]['title']='准备输入'
        updated=self.activate()
        self.assertEqual(updated['tasks'][-1]['title'],'准备输入')
        loaded,_,_=coord.load(self.root)
        self.assertEqual(loaded['tasks'][-1]['title'],'准备输入')
        self.assertEqual(loaded['tasks'][-1]['goal'],'prepare input')

    def test_rejects_cycle_scope_and_unknown_checks_atomically(self):
        for mutate in (lambda p: p["add"][0].update(allowed_scope=["../evil"]),
                       lambda p: p["add"][0].update(depends_on=["A"]),
                       lambda p: p["add"][0].update(checks=["invented"]),
                       lambda p: p.update(goal="new goal")):
            p = copy.deepcopy(self.proposal)
            mutate(p)
            with self.assertRaises(checks.Rejected): self.activate(p)
            self.assertEqual(self.state, self.bus.state())
            self.assertNotIn("plan_revisions", self.state)

    def test_stale_revision_and_accepted_tasks_rejected(self):
        self.activate()
        p = copy.deepcopy(self.proposal)
        p.update(id="stale", add=[], dependencies={"A": []})
        with self.assertRaisesRegex(checks.Rejected, "stale"): self.activate(p)
        self.state["tasks"]["A"].update(status="integrated", attempts=2)
        p.update(expected_revision=1, dependencies={"A": ["PREP"], "PREP": ["A"]})
        with self.assertRaises(checks.Rejected): self.activate(p)

    def test_budgets_preserved_and_chain_detects_drift(self):
        self.state["elapsed_seconds"] = 12
        self.state["tasks"]["A"]["attempts"] = 1
        self.activate()
        self.assertEqual(self.state["elapsed_seconds"], 12)
        self.assertEqual(self.state["tasks"]["A"]["attempts"], 1)
        self.state["plan_revisions"][0]["digest"] = "corrupt"
        self.bus.save(self.state, "simulated_corruption")
        with self.assertRaisesRegex(checks.Rejected, "drift"): coord.load(self.root)

    def test_active_session_replan_keeps_running_status_and_clock(self):
        self.state['session_started'] = 1234.5
        self.state['status'] = 'running'
        self.state['elapsed_seconds'] = 12
        self.activate()
        self.assertEqual(self.state['status'], 'running')
        self.assertEqual(self.state['session_started'], 1234.5)
        self.assertEqual(self.state['elapsed_seconds'], 12)

    def test_existing_feature_split_checkpoint_replays_without_new_fields(self):
        fixture = checks.read_json(Path(__file__).resolve().parents[1] / 'assets/feature-split-v1.fixture.json')
        self.assertEqual(replan.materialize(fixture['base'], fixture['state']), fixture['expected'])

    def test_accepted_definition_and_state_are_preserved(self):
        self.state["tasks"]["A"].update(status="integrated", attempts=1, commit=self.plan["base_commit"])
        self.bus.save(self.state, "simulated_accepted_task")
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(checks.Rejected, "accepted"):
            self.activate()
        self.assertEqual(self.bus.state(), before)
        self.assertEqual(self.state, before)

    def test_ai_replan_waits_for_finished_parallel_worker_to_integrate(self):
        base = self.base / 'parallel'; base.mkdir()
        plan, root = fixture(base, count=2)
        plan['tasks'][0]['allowed_scope'].append('prep.txt')
        prep = base / 'prepare_check.py'
        prep.write_text("from pathlib import Path\nimport sys\nassert (Path(sys.argv[1])/'prep.txt').read_text()=='OK'\n")
        plan['replanning'] = {'max_revisions':1,'max_added_tasks':1,'max_total_attempts':4,
            'preparation_checks':[{'id':'prepared','argv':[sys.executable,str(prep),'{repo}'],'timeout_seconds':5}]}
        worker = base / 'worker.py'
        worker.write_text(r'''
import json,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import workflow_message as msg
from workflow_bus import Bus
if '--version' in sys.argv: print('SIMULATED parallel checkpoint');raise SystemExit(0)
sys.stdin.read()
base=Path(__file__).parent
bus=Bus(base/'run')
if sys.argv[2]=='coordinator':
    context=json.loads(Path('context.json').read_text())
    (base/'coordinator-started').write_text('started')
    end=time.monotonic()+15
    while bus.state()['tasks']['B']['status']!='finished':
        assert time.monotonic()<end
        time.sleep(.05)
    proposal={'id':'split-after-peer','expected_revision':0,'reason':'Scoped preparation after peer completion',
       'add':[{'id':'PREP','parent':'A','goal':'prepare','allowed_scope':['prep.txt'],'checks':['prepared'],'depends_on':[]}],'dependencies':{}}
    Path('decision.json').write_text(json.dumps({'actions':[{'type':'replan','request_id':context['requests'][0]['id'],'proposal':proposal}]}))
else:
    box=msg.read('.',True);tid=box['task_id']
    attempt=[m for m in box['messages'] if m['kind']=='assignment'][-1]['payload']['attempt']
    if tid=='A' and attempt==1:msg.post('.','blocked',text='Prepare shared input first')
    elif tid=='A':
        assert Path('prep.txt').read_text()=='OK'
        Path('a.txt').write_text('OK')
    elif tid=='B':
        end=time.monotonic()+15
        while not (base/'coordinator-started').exists():
            assert time.monotonic()<end
            time.sleep(.05)
        Path('b.txt').write_text('OK')
    else:Path('prep.txt').write_text('OK')
    msg.read('.',True)
''', encoding='utf-8')
        repin_fixture(root, plan)
        pins = checks.digest(root / 'pins.json')
        self.assertEqual(coord.run(root), 0)
        state = Bus(root).state()
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(len(state['plan_revisions']), 1)
        self.assertTrue(all(t['status']=='integrated' for t in state['tasks'].values()))
        self.assertEqual(state['tasks']['A']['attempts'], 2)
        with Bus(root).connection() as db:
            events=[dict(r) for r in db.execute("SELECT seq,kind,data FROM events WHERE kind IN ('task_integrated','plan_revised','replan_rejected') ORDER BY seq")]
        self.assertFalse(any(e['kind']=='replan_rejected' for e in events))
        self.assertEqual(checks.digest(root/'pins.json'), pins)


if __name__ == "__main__": unittest.main()
