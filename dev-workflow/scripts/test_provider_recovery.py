"""Actual subprocess/service recovery with simulated upstream quota responses, not real AI."""
import copy,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import check_night_run as checks
import workflow
import workflow_coordinator as coord
import workflow_memory as memory
import workflow_metrics as metrics
import workflow_providers as providers
import workflow_runtime as runtime
import workflow_service as service
from workflow_bus import Bus
from test_workflow_coordinator import fixture,WORKER,HERE
from test_workflow_review import REVIEWER

GATE='''
import json,sys
from pathlib import Path
if '--version' not in sys.argv:
    label,ready=sys.argv[-2:]
    if label=='auth' or label=='primary' or not Path(ready).exists():
        if not Path('context.json').exists() and not Path('a.txt').exists(): Path('a.txt').write_text('DRAFT')
        error='authentication_error' if label=='auth' else 'insufficient_quota'
        print(json.dumps({'type':'result','is_error':True,'error':{'type':error,'retry_after':1}}),flush=True)
        raise SystemExit(75)
    if not Path('context.json').exists() and Path('a.txt').exists(): assert Path('a.txt').read_text()=='DRAFT'
'''

class ProviderRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name).resolve()
    def tearDown(self):self.tmp.cleanup()

    def prepare(self, *, fallback=True, auth=False, reviewer=False):
        plan,old=fixture(self.base)
        plan['run_id']='quota-case';plan['skill_root']=str(coord.ROOT);plan['limits'].update(max_attempts=1,parallelism=1)
        configs=checks.read_json(old/'runtime.json');configs.pop('coordinator',None)
        ready=self.base/'upstream-ready.flag'
        role='reviewer' if reviewer else 'worker'
        script=self.base/'quota-provider.py';script.write_text(GATE+(REVIEWER if reviewer else WORKER),encoding='utf8')
        config=copy.deepcopy(configs['worker'])
        args=['-B',str(script),'approve' if reviewer else str(HERE)]
        if not reviewer:args.append('simple')
        label='single' if reviewer or not fallback else 'auth' if auth else 'primary'
        config['harness'].update(args=args+[label,str(ready)])
        config.update(provider_id='one',quota_recovery={'enabled':True,'initial_seconds':1,'max_seconds':2})
        config['protected_files'].append(str(script))
        if fallback:
            alternate=copy.deepcopy(config['harness']);alternate['args']=args+['backup',str(ready)]
            config['fallbacks']=[{'id':'two','harness':alternate,'protected_files':[str(script)]}]
        configs[role]=config
        if reviewer:plan['review']={'max_rounds':2,'max_recovery_attempts':0}
        memory.atomic_json(self.base/'new-plan.json',plan);memory.atomic_json(self.base/'new-runtime.json',configs)
        root=self.base/'runs'/'quota';coord.initialize(self.base/'new-plan.json',self.base/'new-runtime.json',root)
        service.initialize(self.base/'control',self.base/'runs')
        controller=service.Service(self.base/'control');controller.submit({'id':'start','run_dir':str(root)})
        return root,controller,ready

    def test_primary_quota_switches_to_backup_and_keeps_partial_draft(self):
        root,controller,ready=self.prepare();ready.touch()
        pins=checks.digest(root/'pins.json')
        self.assertTrue(controller.dispatch_once())
        state=Bus(root).state();self.assertEqual(state['status'],'completed')
        self.assertEqual(state['tasks']['A']['attempts'],1)
        self.assertEqual(checks.digest(root/'pins.json'),pins)
        logs=list((root/'attempts').rglob('process.json'))
        self.assertTrue(any(checks.read_json(p).get('provider_id')=='two' for p in logs))
        self.assertEqual(len(list((root/'attempts').rglob('events.jsonl'))),2)

    def test_all_limited_waits_then_service_restart_resumes_same_attempt(self):
        root,controller,ready=self.prepare();pins=checks.digest(root/'pins.json')
        self.assertTrue(controller.dispatch_once())
        state=Bus(root).state();entry=state['tasks']['A'];workspace=entry['workspace'];used=state['elapsed_seconds']
        self.assertEqual(state['status'],'waiting_for_quota');self.assertFalse((root/'cancel.request').exists())
        self.assertEqual(Path(workspace,'a.txt').read_text(),'DRAFT')
        with patch.object(service.time,'time',return_value=state['quota_wait']['retry_at']-.1):
            self.assertFalse(controller.dispatch_once())
        self.assertEqual(controller.listing()[0]['status'],'waiting_quota')
        ready.touch();time.sleep(1.1)
        fresh=service.Service(self.base/'control');fresh.recover_jobs()
        self.assertTrue(fresh.dispatch_once())
        final=Bus(root).state();self.assertEqual(final['status'],'completed')
        self.assertEqual(final['tasks']['A']['workspace'],workspace)
        self.assertEqual(final['tasks']['A']['attempts'],1)
        self.assertGreater(final['elapsed_seconds'],used)
        self.assertGreater(final['quota_wait_seconds'],0)
        self.assertEqual(checks.digest(root/'pins.json'),pins)
        self.assertFalse(fresh.dispatch_once())

    def test_single_provider_also_recovers_without_operator_continue(self):
        root,controller,ready=self.prepare(fallback=False)
        controller.dispatch_once();self.assertEqual(Bus(root).state()['status'],'waiting_for_quota')
        ready.touch();time.sleep(1.1);controller.dispatch_once()
        self.assertEqual(Bus(root).state()['status'],'completed')
        self.assertEqual(Bus(root).state()['tasks']['A']['attempts'],1)

    def test_authentication_failure_does_not_rotate_or_poll(self):
        root,controller,ready=self.prepare(auth=True);ready.touch()
        controller.dispatch_once();state=Bus(root).state()
        self.assertNotEqual(state['status'],'waiting_for_quota')
        self.assertEqual(state['tasks']['A']['reason'],'authentication')
        self.assertEqual(len(list((root/'attempts').rglob('events.jsonl'))),1)
        self.assertFalse(controller.dispatch_once())

    def test_cancel_during_cooldown_prevents_automatic_resume(self):
        root,controller,ready=self.prepare();controller.dispatch_once()
        controller.submit({'id':'cancel','run_dir':str(root),'cancel':True})
        ready.touch();time.sleep(1.1)
        self.assertFalse(controller.dispatch_once());self.assertEqual(controller.listing()[0]['status'],'cancelled')
        self.assertEqual(Bus(root).state()['tasks']['A']['attempts'],1)

    def test_review_quota_preserves_candidate_and_quality_round_limit(self):
        root,controller,ready=self.prepare(fallback=False,reviewer=True)
        controller.dispatch_once();state=Bus(root).state()
        self.assertEqual(state['status'],'waiting_for_quota');commit=state['tasks']['A']['commit']
        self.assertEqual(state['tasks']['A']['status'],'verified')
        ready.touch();time.sleep(1.1);controller.dispatch_once()
        final=Bus(root).state();self.assertEqual(final['status'],'completed')
        self.assertEqual(final['tasks']['A']['commit'],commit)
        self.assertEqual(final['tasks']['A']['attempts'],1)
        self.assertEqual(final['review_rounds']-final['review_quota_rounds'],2)

    def test_original_time_budget_exhaustion_stops_quota_polling(self):
        root,controller,ready=self.prepare();controller.dispatch_once()
        state=Bus(root).state();state['elapsed_seconds']=100000
        Bus(root).save(state,'test_declared_budget_exhaustion')
        ready.touch();time.sleep(1.1);controller.dispatch_once()
        self.assertEqual(Bus(root).state()['status'],'partial')
        self.assertEqual(controller.listing()[0]['status'],'failed')
        self.assertFalse(controller.dispatch_once())

    def test_service_condition_timer_wakes_without_new_event(self):
        root,controller,ready=self.prepare(fallback=False)
        import threading
        thread=threading.Thread(target=controller.loop,daemon=True);thread.start()
        try:
            end=time.monotonic()+15
            while time.monotonic()<end and Bus(root).state()['status']!='waiting_for_quota':time.sleep(.05)
            self.assertEqual(Bus(root).state()['status'],'waiting_for_quota')
            ready.touch()
            end=time.monotonic()+15
            while time.monotonic()<end and Bus(root).state()['status']!='completed':time.sleep(.05)
            self.assertEqual(Bus(root).state()['status'],'completed')
        finally:
            with controller.condition:controller.stopping=True;controller.condition.notify_all()
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())

class CooldownRules(unittest.TestCase):
    def test_observed_quota_interrupts_native_internal_retry_sleep(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name).resolve();program=root/'quota.py'
            program.write_text("import json,time\nprint(json.dumps({'type':'result','is_error':True,'error':{'type':'insufficient_quota','retry_after':1}}),flush=True)\ntime.sleep(20)\n")
            cfg={'format_version':1,'provider_id':'one','quota_recovery':{'enabled':True,'initial_seconds':1,'max_seconds':2},'harness':{'kind':'command','executable':sys.executable,'args':['-B',str(program)]},'limits':{'attempt_seconds':5,'no_progress_seconds':5,'max_output_bytes':10000}}
            began=time.monotonic()
            result=runtime.run_worker(cfg,root,root,'task',root/'process',lambda:'',5,root/'cancel')
            self.assertEqual(result['reason'],'waiting_for_quota')
            self.assertLess(time.monotonic()-began,3)
    def test_backoff_persists_and_respects_server_delay(self):
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'state.sqlite3';p=providers.Cooldowns(path);policy={'initial_seconds':60,'max_seconds':900}
            self.assertEqual(p.limited('a',policy,now=0),60)
            self.assertEqual(providers.Cooldowns(path).limited('a',policy,now=1),121)
            self.assertEqual(p.limited('a',policy,server_delay=18000,now=2),18002)
            p.success('a');self.assertEqual(p.due('a'),0)
    def test_explicit_retry_after_only(self):
        self.assertEqual(providers.retry_after({'retry_after':12}),12)
        self.assertEqual(providers.retry_after('retry-after: 60'),60)
        self.assertIsNone(providers.retry_after('retry_after: NaN'))
        self.assertIsNone(providers.retry_after('5 hour plan; no actual reset time provided'))
    def test_error_categories_do_not_confuse_budget_with_quota(self):
        self.assertEqual(runtime.failure_hint('rate_limit_error'),'rate_limit')
        self.assertEqual(runtime.failure_hint("You've hit your limit; resets later"),'account_quota')
        self.assertEqual(runtime.failure_hint('invalid api key'),'authentication')
        self.assertEqual(runtime.failure_hint('invalid api key; quota exceeded'),'authentication')
        self.assertEqual(runtime.normalize({'type':'result','is_error':True,'subtype':'error_max_budget_usd'})['environment_failure'],'budget_exhausted')
        self.assertEqual(runtime.normalize({'type':'result','is_error':True,'subtype':'error_max_budget_usd','message':'quota exceeded'})['environment_failure'],'budget_exhausted')
        self.assertEqual(runtime.normalize({'type':'result','is_error':True,'error':{'type':'insufficient_quota','retry_after':37}})['retry_after_seconds'],37)
    def test_unknown_policy_and_unpinned_paths_rejected(self):
        cfg={'format_version':1,'harness':{'kind':'command','executable':sys.executable},'limits':{'attempt_seconds':10,'no_progress_seconds':10,'max_output_bytes':1000},'quota_recovery':{'enabled':True},'provider_id':'a'}
        runtime.validate_config(cfg)
        bad=copy.deepcopy(cfg);bad['quota_recovery']['unknown']=True
        with self.assertRaises(checks.Rejected):runtime.validate_config(bad)
        bad=copy.deepcopy(cfg);bad['harness']['executable']='python'
        with self.assertRaises(checks.Rejected):runtime.validate_config(bad)

if __name__=='__main__':unittest.main(verbosity=2)
