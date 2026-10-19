"""Actual subprocess/service recovery with simulated upstream quota responses, not real AI."""
import copy,json,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import check_night_run as checks
import workflow
import workflow_coordinator as coord
import workflow_human as human
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

    def prepare_coordinator(self, *, auth=False):
        plan, old = fixture(self.base, 'coordinator_question')
        plan['run_id'] = 'coordinator-quota-case'
        plan['skill_root'] = str(coord.ROOT)
        plan['limits'].update(max_attempts=1, max_coordinator_rounds=2 if auth else 1, parallelism=1)
        worker = self.base / 'worker.py'
        worker_code = WORKER.replace('time.monotonic() + 15', 'time.monotonic() + 3')
        worker_code = worker_code.replace("    msg.post('.', 'question', text='Confirm required output from the fixed task goal')",
            "    old = [json.loads(p.read_text()) for p in Path('.workflow-mailbox/outbox').glob('*.json')]\n"
            "    if not any(m.get('type') == 'question' for m in old):\n"
            "        msg.post('.', 'question', text='Confirm required output from the fixed task goal')\n"
            "        Path('a.txt').write_text('Question sent; preserve across provider pause')")
        worker.write_text(worker_code, encoding='utf8')
        configs = checks.read_json(old / 'runtime.json')
        ready = self.base / 'coordinator-ready.flag'
        script = self.base / 'coordinator-provider.py'
        script.write_text(GATE + WORKER, encoding='utf8')
        config = configs['coordinator']
        config['harness']['args'] = ['-B', str(script), str(HERE), 'coordinator', 'auth' if auth else 'single', str(ready)]
        config.update(provider_id='coordinator', quota_recovery={'enabled':True, 'initial_seconds':1, 'max_seconds':1})
        config['protected_files'].append(str(script))
        memory.atomic_json(self.base / 'new-plan.json', plan)
        memory.atomic_json(self.base / 'new-runtime.json', configs)
        root = self.base / 'runs' / 'coordinator-quota'
        coord.initialize(self.base / 'new-plan.json', self.base / 'new-runtime.json', root)
        service.initialize(self.base / 'control', self.base / 'runs')
        controller = service.Service(self.base / 'control')
        controller.submit({'id':'start', 'run_dir':str(root)})
        return root, controller, ready

    def test_coordinator_quota_pauses_waiting_worker_and_service_resumes_same_attempt(self):
        root, controller, ready = self.prepare_coordinator()
        self.assertTrue(controller.dispatch_once())
        state = Bus(root).state()
        entry = state['tasks']['A']
        workspace, spent = entry['workspace'], entry['quota_attempt_elapsed_seconds']
        self.assertEqual(state['status'], 'waiting_for_quota')
        self.assertEqual(entry['status'], 'quota_wait')
        self.assertEqual(entry['attempts'], 1)
        self.assertLess(spent, 3, 'Worker must pause before its simulated answer deadline')
        self.assertTrue(workflow.snapshot(root)['human_requests'])
        original_question = next(m for m in Bus(root).unresolved() if m['kind'] == 'question')
        self.assertEqual(controller.listing()[0]['status'], 'waiting_quota')
        ready.touch()
        time.sleep(1.1)
        fresh = service.Service(self.base / 'control')
        fresh.recover_jobs()
        self.assertTrue(fresh.dispatch_once())
        final = Bus(root).state()
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['tasks']['A']['workspace'], workspace)
        self.assertEqual(final['tasks']['A']['attempts'], 1)
        self.assertGreater(final['tasks']['A']['quota_attempt_elapsed_seconds'], spent)
        self.assertEqual(final['coordinator_rounds'] - final['coordinator_quota_rounds'], 1)
        questions = [m for m in Bus(root).messages() if m['kind'] == 'question']
        self.assertEqual([m['id'] for m in questions], [original_question['id']])
        self.assertTrue(questions[0]['resolved'])
        decisions = [m for m in Bus(root).messages() if m['kind'] == 'decision']
        self.assertEqual(decisions[0]['payload']['request_id'], original_question['id'])
        self.assertTrue(decisions[0]['acked'])
        self.assertFalse(fresh.dispatch_once())

    def test_explicit_human_decision_still_prevents_automatic_quota_resume(self):
        root, controller, ready = self.prepare_coordinator()
        controller.dispatch_once()
        state = Bus(root).state()
        state['tasks']['A']['needs_human'] = 'User must decide the output'
        Bus(root).save(state, 'test_explicit_human_decision')
        row = controller.listing()[0]
        controller.finish(row['id'], row['generation'], 3)
        self.assertEqual(controller.listing()[0]['status'], 'waiting')
        ready.touch()
        time.sleep(1.1)
        self.assertFalse(controller.dispatch_once())

    def test_coordinator_authentication_failure_is_not_retried_as_another_round(self):
        root, controller, ready = self.prepare_coordinator(auth=True)
        controller.dispatch_once()
        state = Bus(root).state()
        self.assertEqual(state['status'], 'blocked_environment')
        self.assertEqual(state['coordinator_environment_failure'], 'authentication')
        self.assertEqual(state['coordinator_rounds'], 1)
        self.assertEqual(len(list((root / 'decisions').glob('*/process/process.json'))), 1)
        self.assertFalse(controller.dispatch_once())

    def test_human_response_during_cooldown_retains_timer_without_an_early_model_call(self):
        root, controller, _ = self.prepare_coordinator()
        controller.dispatch_once()
        state = Bus(root).state()
        retry_at = time.time() + 60
        state['quota_wait']['retry_at'] = retry_at
        state['tasks']['A']['needs_human'] = 'Human must decide this output'
        Bus(root).save(state, 'test_explicit_human_decision_during_quota')
        row = controller.listing()[0]
        controller.finish(row['id'], row['generation'], 3)
        self.assertEqual(controller.listing()[0]['status'], 'waiting')
        ticket = workflow.snapshot(root)['human_requests'][0]
        response = {'id':'quota-answer', 'ticket_id':ticket['ticket_id'], 'decision':'answer',
            'text':'confirmed: OK', 'reference':'isolated regression', 'quote':'Confirm required output'}
        controller.submit({'id':'answer-event', 'run_dir':str(root), 'response':response})
        self.assertTrue(controller.dispatch_once())
        final = Bus(root).state()
        self.assertEqual(final['status'], 'waiting_for_quota')
        self.assertEqual(final['quota_wait']['retry_at'], retry_at)
        self.assertNotIn('needs_human', final['tasks']['A'])
        self.assertEqual(final['tasks']['A']['attempts'], 1)
        self.assertEqual(human.receipt(root, response['id'])['status'], 'applied')
        self.assertFalse(Bus(root).unresolved())
        self.assertEqual(len(list((root / 'decisions').glob('*/process/process.json'))), 1)
        self.assertEqual(controller.listing()[0]['status'], 'waiting_quota')
        self.assertEqual(controller.listing()[0]['retry_at'], retry_at)
        self.assertFalse(controller.dispatch_once())

    def test_cancel_during_coordinator_quota_prevents_resume(self):
        root, controller, ready = self.prepare_coordinator()
        controller.dispatch_once()
        controller.submit({'id':'cancel', 'run_dir':str(root), 'cancel':True})
        ready.touch()
        time.sleep(1.1)
        self.assertFalse(controller.dispatch_once())
        self.assertEqual(controller.listing()[0]['status'], 'cancelled')
        self.assertEqual(Bus(root).state()['tasks']['A']['attempts'], 1)

    def test_recover_coordinator_quota_uses_process_time_or_pause_boundary_not_offline_wait(self):
        original = self.base
        for recorded in (False, True):
            with self.subTest(process_duration=recorded):
                self.base = original / ('recorded' if recorded else 'missing')
                self.base.mkdir()
                root, _, _ = self.prepare_coordinator()
                plan, _, _ = coord.load(root)
                workspace = coord.new_worktree(root, plan, 'workflow/recovery/A-a1', plan['base_commit'], 'A-a1')
                baseline = coord.source_snapshot(workspace, plan['tasks'])
                (workspace / 'a.txt').write_text('Preserved draft')
                state = Bus(root).state()
                state.update(status='waiting_for_quota', session_started=100, elapsed_seconds=2,
                    coordinator_quota_pause=True, quota_wait={'since':110, 'retry_at':111, 'task':'coordinator'})
                state['tasks']['A'].update(status='running', attempts=1, workspace=str(workspace),
                    branch='workflow/recovery/A-a1', baseline=baseline, worker_preflight={'started_at':100},
                    quota_attempt_elapsed_seconds=3, quota_resumptions=1)
                if recorded:
                    process_folder = root / 'attempts/A/1/quota-resumes/1'
                    process_folder.mkdir(parents=True)
                    memory.atomic_json(process_folder / 'process.json', {'duration_seconds':4})
                Bus(root).save(state, 'test_crash_after_quota_pause')
                with patch.object(coord.time, 'time', return_value=10000):
                    coord.recover(root)
                recovered = Bus(root).state()
                entry = recovered['tasks']['A']
                self.assertEqual(entry['status'], 'quota_wait')
                self.assertEqual(entry['workspace'], str(workspace))
                self.assertEqual(entry['attempts'], 1)
                self.assertEqual(entry['quota_attempt_elapsed_seconds'], 7 if recorded else 13)
                self.assertEqual(entry['quota_recovery_timing']['source'], 'process' if recorded else 'quota_pause_boundary')
                self.assertEqual(recovered['elapsed_seconds'], 12)

    def test_recover_ordinary_quota_excludes_offline_wait_only_after_the_wave_pauses(self):
        cases = [('quota_wait', None, False), ('verified', None, False),
            ('quota_wait', 'creating', False), ('quota_wait', 'running', False),
            ('quota_wait', 'committing', False), ('quota_wait', None, True)]
        for index, (status, peer_status, coordinator_active) in enumerate(cases):
            with self.subTest(task=status, peer=peer_status, coordinator_active=coordinator_active):
                base = self.base / str(index)
                base.mkdir()
                plan, root = fixture(base, count=2 if peer_status else 1)
                state = Bus(root).state()
                state.update(status='waiting_for_quota', session_started=100, elapsed_seconds=2,
                    coordinator_active=coordinator_active, quota_wait={'since':110, 'retry_at':111, 'task':'A'})
                state['tasks']['A'].update(status=status, attempts=1)
                if peer_status:
                    branch = 'workflow/recovery/B-a1'
                    workspace = coord.new_worktree(root, plan, branch, plan['base_commit'], 'B-a1')
                    state['tasks']['B'].update(status=peer_status, attempts=1, workspace=str(workspace),
                        branch=branch, launch_base=plan['base_commit'], baseline=coord.source_snapshot(workspace, plan['tasks']))
                Bus(root).save(state, 'test_crash_during_ordinary_quota_wait')
                with patch.object(coord.time, 'time', return_value=10000):
                    coord.recover(root)
                recovered = Bus(root).state()
                self.assertEqual(recovered['elapsed_seconds'], 9902 if peer_status or coordinator_active else 12)
                self.assertEqual(recovered['tasks']['A']['status'], status)
                self.assertEqual(recovered['tasks']['A']['attempts'], 1)
                self.assertEqual(recovered['quota_wait'], state['quota_wait'])

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
