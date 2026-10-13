"""Pinned extra checks run before acceptance and use normal automatic repair."""
import copy
from pathlib import Path
import sys,tempfile,unittest
import check_night_run as checks
import workflow_coordinator as coord
import workflow_memory as memory
from workflow_bus import Bus
from test_workflow_coordinator import fixture
from test_workflow_review import REVIEWER

class AssuranceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve()
        self.plan,old=fixture(self.base)
        self.original_requirements=copy.deepcopy(self.plan['requirements'])
        self.configs=checks.read_json(old/'runtime.json')
        self.plan['run_id']='automatic-assurance'
        self.plan['tasks'][0]['allowed_scope'].append('assurance.txt')
        worker=self.base/'worker.py'
        worker.write_text(worker.read_text()+"\nnumber=next(m['payload']['attempt'] for m in box['messages'] if m['kind']=='assignment')\nPath('assurance.txt').write_text('missing' if number==1 else 'shared OK')\n")
        self.extra=self.base/'shared_check.py'
        self.extra.write_text("from pathlib import Path\nimport sys\nassert (Path(sys.argv[1])/'assurance.txt').read_text()=='shared OK'\n")
        self.plan['assurance']={'checks':[{'id':'shared-context','task_id':'A','risk':'Original unit check misses shared context',
            'argv':[sys.executable,'-B',str(self.extra),'{repo}'],'timeout_seconds':5}]}
        self.root=self.base/'assurance-run'

    def tearDown(self):self.temp.cleanup()

    def initialize(self):
        memory.atomic_json(self.base/'assurance-plan.json',self.plan)
        memory.atomic_json(self.base/'assurance-runtime.json',self.configs)
        coord.initialize(self.base/'assurance-plan.json',self.base/'assurance-runtime.json',self.root)

    def test_shared_context_failure_repairs_without_changing_original_acceptance(self):
        self.initialize();pins=checks.digest(self.root/'pins.json')
        self.assertEqual(coord.run(self.root),0)
        state=Bus(self.root).state()
        self.assertEqual(state['tasks']['A']['attempts'],2)
        first=checks.read_json(self.root/'checks/task-A-1/results.json')
        self.assertEqual([(r['check_id'],r['result']) for r in first],[('behavior','passed'),('shared-context','failed')])
        self.assertTrue(state['tasks']['A']['failure_cases'])
        self.assertTrue(any(r['check_id']=='shared-context' and r['result']=='passed' for r in state['final_checks']))
        self.assertEqual(checks.read_json(self.root/'plan.json')['requirements'],self.original_requirements)
        self.assertEqual(checks.digest(self.root/'pins.json'),pins)

    def test_extra_check_is_pinned_without_manual_protected_file_registration(self):
        self.initialize()
        self.assertIn(str(self.extra),checks.read_json(self.root/'pins.json')['protected'])
        self.extra.write_text('print("fake success")')
        with self.assertRaisesRegex(checks.Rejected,'protected input drift'):coord.load(self.root)

    def test_failed_original_check_cannot_be_replaced_by_passing_assurance(self):
        self.extra.write_text('print("supplement passed")')
        worker=self.base/'worker.py';worker.write_text(worker.read_text().replace("write_text('OK')","write_text('wrong')"))
        self.initialize();self.assertEqual(coord.run(self.root),3)
        self.assertNotEqual(Bus(self.root).state()['status'],'completed')

    def test_unknown_owner_duplicate_and_non_external_command_are_rejected(self):
        original=copy.deepcopy(self.plan)
        for bad in ('owner','duplicate','inline'):
            with self.subTest(bad=bad):
                plan=copy.deepcopy(original);spec=plan['assurance']['checks'][0]
                if bad=='owner':spec['task_id']='unknown'
                elif bad=='duplicate':spec['id']='behavior'
                else:spec['argv']=[sys.executable,'-c','print("pass")']
                with self.assertRaises(checks.Rejected):coord.validate(plan)

    def final_case(self,maximum,permanent=False):
        self.plan['assurance']['max_final_repairs']=maximum
        self.extra.write_text("from pathlib import Path\nimport sys\nr=Path(sys.argv[1])\n"
            +"if r.name.startswith('audit-'): assert (r/'assurance.txt').read_text()=="
            +repr('impossible' if permanent else 'shared OK')+"\n")
        reviewer=self.base/'reviewer.py';reviewer.write_text(REVIEWER)
        self.configs['reviewer']=copy.deepcopy(self.configs['worker'])
        self.configs['reviewer']['harness']['args']=['-B',str(reviewer),'approve']
        self.plan['review']={'max_rounds':6}
        self.initialize();code=coord.run(self.root)
        return code,Bus(self.root).state()

    def test_final_assurance_failure_reopens_owner_and_repeats_whole_review(self):
        code,state=self.final_case(1)
        self.assertEqual(code,0);self.assertEqual(state['tasks']['A']['attempts'],2)
        self.assertEqual(len(state['assurance_repairs']),1)
        self.assertEqual([r['binding']['task_id'] for r in state['review_history']],['A','integration','A','integration'])
        self.assertNotEqual(state['review_history'][1]['binding']['head'],state['final_review']['binding']['head'])
        self.assertEqual(state['final_audit_head'],state['integration_head'])
        self.assertTrue(all(r['result']=='passed' for r in state['final_checks']))

    def test_final_repairs_are_opt_in_and_bounded(self):
        for maximum,permanent in ((0,False),(1,True)):
            with self.subTest(maximum=maximum),tempfile.TemporaryDirectory() as folder:
                previous=(self.temp,self.base,self.plan,self.root,self.configs,self.extra,self.original_requirements)
                self.setUp()
                try:
                    code,state=self.final_case(maximum,permanent)
                    self.assertEqual(code,3)
                    self.assertEqual(len(state.get('assurance_repairs',[])),maximum)
                    self.assertEqual(state['tasks']['A']['attempts'],maximum+1)
                finally:
                    self.tearDown()
                    self.temp,self.base,self.plan,self.root,self.configs,self.extra,self.original_requirements=previous

if __name__=='__main__':unittest.main()
