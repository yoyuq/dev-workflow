"""Actual subprocess faults, finite retries and fail-closed integrity cases; simulated reviewers."""
import copy
from pathlib import Path
import sys,tempfile,unittest
import check_night_run as checks
import workflow_coordinator as coord
import workflow_memory as memory
from workflow_bus import Bus
from test_workflow_coordinator import fixture
from test_workflow_review import REVIEWER

FAULTS='''
prior=list(Path.cwd().parent.glob('*/process/process.json'))
if mode=='always_fault' or mode in ('fault_once','auth_once','permission_once','stderr_auth','stderr_permission','stderr_quota','mutate_fail') and not prior:
    if mode=='auth_once': print(json.dumps({'type':'turn.failed','error':{'message':'authentication_error'}}),flush=True)
    if mode=='permission_once': print(json.dumps({'type':'system','subtype':'permission_denied'}),flush=True)
    if mode=='mutate_fail': (Path(c['workspace'])/'a.txt').write_text('tampered')
    if mode=='stderr_auth': print('Error: authentication_error / invalid api key',file=sys.stderr,flush=True)
    if mode=='stderr_permission': print('Error: access denied',file=sys.stderr,flush=True)
    if mode=='stderr_quota': print('Error: usage limit reached',file=sys.stderr,flush=True)
    raise SystemExit(75)
if mode=='timeout_once' and not prior:
    import time
    time.sleep(10)
'''

class RecoveryTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve()
    def tearDown(self):self.temp.cleanup()

    def run_case(self,mode,maximum=1,rounds=5):
        plan,old=fixture(self.base)
        configs=checks.read_json(old/'runtime.json')
        script=self.base/'reviewer.py';script.write_text(REVIEWER.replace("if mode=='mutate':",FAULTS+"\nif mode=='mutate':"))
        configs['reviewer']=copy.deepcopy(configs['worker'])
        configs['reviewer']['harness']['args']=['-B',str(script),mode]
        if mode=='timeout_once':
            configs['reviewer']['limits']['attempt_seconds']=1
            configs['reviewer']['limits']['no_progress_seconds']=1
        plan['run_id']='review-recovery';plan['review']={'max_rounds':rounds,'max_recovery_attempts':maximum}
        memory.atomic_json(self.base/'plan-recovery.json',plan);memory.atomic_json(self.base/'runtime-recovery.json',configs)
        root=self.base/'recovery-run';coord.initialize(self.base/'plan-recovery.json',self.base/'runtime-recovery.json',root)
        pins=checks.digest(root/'pins.json');code=coord.run(root)
        self.assertEqual(checks.digest(root/'pins.json'),pins)
        return code,Bus(root).state(),root

    def test_transient_fault_uses_new_context_and_preserves_failed_evidence(self):
        code,state,root=self.run_case('fault_once')
        self.assertEqual(code,0);self.assertEqual(state['review_rounds'],3)
        self.assertEqual(len(state['review_recoveries']),1)
        failure=state['review_recoveries'][0]
        self.assertTrue(Path(failure['process_path']).is_file())
        self.assertEqual(failure['binding'],state['review_history'][0]['binding'])
        self.assertEqual(state['tasks']['A']['attempts'],1)
        self.assertGreater(state['elapsed_seconds'],0)
        self.assertEqual(len(list((root/'reviews').glob('*/process/process.json'))),3)

    def test_attempt_timeout_recovers_but_consumes_the_original_limits(self):
        code,state,_=self.run_case('timeout_once')
        self.assertEqual(code,0);self.assertEqual(state['review_recoveries'][0]['process']['reason'],'time_limit')
        self.assertGreaterEqual(state['elapsed_seconds'],1)

    def test_repeated_process_fault_stops_at_declared_retry_count(self):
        code,state,_=self.run_case('always_fault')
        self.assertEqual(code,3);self.assertEqual(state['review_rounds'],2)
        self.assertEqual(len(state['review_recoveries']),1)
        self.assertEqual(len(state['review_process_failures']),2)

    def test_legacy_policy_has_no_automatic_retry(self):
        code,state,_=self.run_case('fault_once',maximum=0)
        self.assertEqual(code,3);self.assertEqual(state['review_rounds'],1)
        self.assertFalse(state.get('review_recoveries'))

    def test_global_review_limit_still_wins(self):
        code,state,_=self.run_case('fault_once',rounds=1)
        self.assertEqual(code,3);self.assertEqual(state['review_rounds'],1)
        self.assertFalse(state.get('review_history'))

    def test_authentication_permission_and_source_drift_do_not_retry(self):
        for mode in ('auth_once','permission_once','stderr_auth','stderr_permission','stderr_quota','mutate_fail'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as folder:
                previous=self.base;self.base=Path(folder).resolve()
                try:
                    code,state,_=self.run_case(mode)
                    self.assertEqual(code,3);self.assertEqual(state['review_rounds'],1)
                    self.assertFalse(state.get('review_recoveries'))
                finally:self.base=previous

    def test_invalid_report_remains_blocked_without_process_retry(self):
        code,state,_=self.run_case('wrong_binding')
        self.assertEqual(code,3);self.assertEqual(state['review_rounds'],1)
        self.assertFalse(state.get('review_recoveries'))

    def test_environment_probes_respect_remaining_run_time(self):
        import time
        import workflow_runtime as runtime
        script=self.base/'health.py';script.write_text('import time\ntime.sleep(5)\n')
        config={'format_version':1,'harness':{'kind':'command','executable':sys.executable},
            'limits':{'attempt_seconds':10,'no_progress_seconds':10,'max_output_bytes':10000},
            'preflight_checks':[{'id':'health','argv':[sys.executable,str(script)],'timeout_seconds':8}]}
        started=time.monotonic()
        with self.assertRaises(checks.Rejected):runtime.preflight(config,self.base,remaining=.8)
        self.assertLess(time.monotonic()-started,2)

    def test_failed_initial_preflight_preserves_time_across_repeated_run(self):
        plan,old=fixture(self.base)
        configs=checks.read_json(old/'runtime.json')
        script=self.base/'health.py';script.write_text('import time\ntime.sleep(5)\n')
        configs['worker']['preflight_checks']=[{'id':'health','argv':[sys.executable,str(script)],'timeout_seconds':8}]
        plan['run_id']='initial-preflight';plan['limits']['max_run_seconds']=1
        memory.atomic_json(self.base/'plan-preflight.json',plan);memory.atomic_json(self.base/'runtime-preflight.json',configs)
        root=self.base/'preflight-run';coord.initialize(self.base/'plan-preflight.json',self.base/'runtime-preflight.json',root)
        with self.assertRaises(checks.Rejected):coord.run(root)
        first=Bus(root).state();self.assertGreaterEqual(first['elapsed_seconds'],1)
        self.assertEqual(first['status'],'blocked_environment');self.assertEqual(first['tasks']['A']['attempts'],0)
        self.assertEqual(coord.run(root),3)
        second=Bus(root).state();self.assertGreaterEqual(second['elapsed_seconds'],first['elapsed_seconds'])
        self.assertLess(second['elapsed_seconds']-first['elapsed_seconds'],.8)
        self.assertEqual(len(second['preflight_failures']),1)

if __name__=='__main__':unittest.main()
