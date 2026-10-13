"""Real HTTP/files/Git evidence; model workers are explicitly simulated."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import check_night_run as checks
import workflow_coordinator as coord
import workflow_memory as memory
import workflow_observer as observer
import workflow_runtime as runtime
import workflow_replan as replan
import workflow_service as service
from workflow_bus import Bus
from test_workflow_coordinator import fixture
from test_workflow_replan import repin_fixture


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.runs = self.base / 'runs'
        self.runs.mkdir()
        self.plan, self.run = fixture(self.runs, count=2)
        service.initialize(self.base / 'control', self.runs)
        self.service = service.Service(self.base / 'control')
        self.job = self.service.submit({'id': 'observe-test', 'run_dir': str(self.run)})['job_id']

    def tearDown(self): self.temp.cleanup()

    def attempt(self, tid='A'):
        p = self.run / 'attempts' / tid / '1'
        p.mkdir(parents=True, exist_ok=True)
        return p

    def test_detail_reads_current_database_and_does_not_schedule(self):
        bus = Bus(self.run)
        state = bus.state()
        state['tasks']['B'].update(status='blocked', reason='model_unavailable', attempts=1)
        bus.save(state, 'task_failed', task='B', reason='model_unavailable')
        before = (self.run / 'plan.json').read_bytes()
        detail = observer.detail(self.service, self.job)
        task = next(t for t in detail['tasks'] if t['id'] == 'B')
        self.assertEqual(task['status'], 'blocked')
        self.assertEqual(task['produces'], ['api-ready'])
        self.assertEqual(task['requires'], [])
        self.assertIn('模型', task['next_step'])
        self.assertEqual(detail['timeline'][-1]['data']['task'], 'B')
        self.assertEqual((self.run / 'plan.json').read_bytes(), before)
        self.assertEqual(bus.state()['tasks']['A']['attempts'], 0)
        with self.service.db() as db:
            self.assertEqual(db.execute('SELECT generation FROM jobs').fetchone()[0], 0)

    def test_real_failed_check_is_visible_with_its_log(self):
        worker = self.runs / 'worker.py'
        text = worker.read_text(encoding='utf-8')
        before, after = text.rsplit("Path('a.txt').write_text('OK')", 1)
        worker.write_text(before + "Path('a.txt' if tid=='A' else 'b.txt').write_text('OK' if tid=='A' else 'BAD')" + after, encoding='utf-8')
        repin_fixture(self.run, self.plan)
        self.assertNotEqual(coord.run(self.run), 0)
        detail = observer.detail(self.service, self.job)
        task = next(t for t in detail['tasks'] if t['id'] == 'B')
        self.assertEqual(task['status'], 'failed')
        self.assertTrue(any(c['result'] != 'passed' for c in task['checks']))
        log = next(a for a in detail['artifacts'] if a['path'].startswith('checks/task-B-') and a['kind'] == 'text')
        output = observer.log_window(self.run, log['id'])
        self.assertIn('AssertionError', output['text'])
        self.assertTrue(task['processes'])

    def test_live_activity_changes_and_never_reuses_previous_attempt(self):
        bus = Bus(self.run)
        state = bus.state()
        state['tasks']['A'].update(status='running', attempts=1)
        bus.save(state, 'test_attempt')
        workspace = self.base/'reporter'
        outbox = workspace/'.workflow-mailbox/outbox'
        outbox.mkdir(parents=True)

        def report(mid, text, attempt):
            memory.atomic_json(outbox/(mid+'.json'), {'id': mid, 'type': 'progress',
                'to': 'coordinator', 'text': text, 'data': {'private': 'NOT_PROJECTED'}})
            bus.ingest('A', attempt, workspace, ['A', 'B'])

        report('first', '检查接口约定', 1)
        task = lambda: next(t for t in observer.detail(self.service, self.job)['tasks'] if t['id']=='A')
        self.assertEqual(task()['activity']['text'], '检查接口约定')
        report('second', '修复校验逻辑 password="SIMULATED SECRET WITH SPACES" '+self.service.token+' '+'x'*400, 1)
        activity = task()['activity']
        self.assertIn('修复校验逻辑', activity['text'])
        self.assertNotIn('SIMULATED', str(activity))
        self.assertNotIn(self.service.token, str(activity))
        self.assertNotIn('NOT_PROJECTED', str(activity))
        self.assertTrue(activity['truncated'])
        self.assertLessEqual(len(activity['text']), 280)
        state['tasks']['A'].update(attempts=2)
        bus.save(state, 'test_retry')
        self.assertIsNone(task()['activity'])
        # New attempt uses a fresh worktree/outbox, as the actual coordinator does.
        workspace = self.base/'reporter2'; outbox = workspace/'.workflow-mailbox/outbox'; outbox.mkdir(parents=True)
        report('third', '复验修复结果', 2)
        self.assertEqual(task()['activity']['text'], '复验修复结果')
        state['tasks']['A']['status']='integrated'; bus.save(state, 'test_accepted')
        self.assertIsNone(task()['activity'])

    def prepare_replan(self):
        self.plan['replanning'] = {'max_revisions': 2, 'max_added_tasks': 2, 'max_total_attempts': 6,
                                  'preparation_checks': self.plan['tasks'][0]['verification']}
        repin_fixture(self.run, self.plan)
        return {'id':'split', 'expected_revision':0, 'reason':'发现输入格式需要先准备',
                'add':[{'id':'PREP', 'parent':'A', 'goal':'准备格式', 'allowed_scope':['a.txt'],
                        'checks':['behavior'], 'depends_on':[]}], 'dependencies':{}}

    def test_accepted_replan_changes_nodes_edges_and_reason(self):
        proposal=self.prepare_replan(); bus=Bus(self.run); state=bus.state()
        replan.activate(self.plan, self.plan, state, bus, proposal, coord.validate)
        result=observer.detail(self.service, self.job)
        tasks={t['id']:t for t in result['tasks']}
        self.assertEqual(result['snapshot']['revision'],1)
        self.assertEqual(tasks['A']['depends_on'],['PREP'])
        self.assertEqual(tasks['PREP']['support_for'],'A')
        self.assertEqual(tasks['PREP']['introduced_revision'],1)
        self.assertEqual(result['plan_changes'],[{'revision':1,'reason':proposal['reason'],
            'added':['PREP'],'dependencies':[{'task':'A','before':[],'after':['PREP']}]}])

    def test_replan_during_observation_does_not_mix_revisions(self):
        proposal=self.prepare_replan(); bus=Bus(self.run); original=coord.load; changed=False
        def concurrent_load(root, **kwargs):
            nonlocal changed
            if not changed and kwargs.get('state') is not None:
                changed=True
                replan.activate(self.plan, self.plan, bus.state(), bus, proposal, coord.validate)
            return original(root, **kwargs)
        with patch.object(coord,'load',side_effect=concurrent_load):
            result=observer.detail(self.service,self.job)
        self.assertEqual(result['snapshot']['revision'],0)
        self.assertEqual(len(result['tasks']),2)
        self.assertEqual(result['snapshot']['task_counts'],{'pending':2})
        self.assertEqual(result['plan_changes'],[])
        self.assertEqual(observer.detail(self.service,self.job)['snapshot']['revision'],1)

    def test_live_controller_remains_running_after_ready_replan_checkpoint(self):
        bus=Bus(self.run);state=bus.state()
        state.update(status='ready',session_started=time.time())
        bus.save(state,'test_replan_checkpoint')
        memory.atomic_json(self.run/'runner.lock',{'pid':os.getpid()})
        self.assertEqual(observer.detail(self.service,self.job)['snapshot']['phase'],'running')
        (self.run/'cancel.request').write_text('test stop')
        self.assertEqual(observer.detail(self.service,self.job)['snapshot']['phase'],'cancelled')

    def test_known_secrets_redacted_in_log_and_task_reason(self):
        p = self.attempt()
        (p/'stderr.log').write_text('Authorization: Bearer abc-secret\napi_key=xyz-secret\n' + self.service.token)
        artifact = observer.catalog(self.run)[0]
        text = observer.log_window(self.run, artifact['id'], self.service.token)['text']
        for secret in ('abc-secret', 'xyz-secret', self.service.token): self.assertNotIn(secret, text)
        state = Bus(self.run).state()
        state['tasks']['A']['reason'] = self.service.token
        Bus(self.run).save(state, 'failed', reason=self.service.token)
        self.assertNotIn(self.service.token, json.dumps(observer.detail(self.service, self.job)))

    def test_event_projection_excludes_model_body_and_reasoning(self):
        p = self.attempt()
        events = [{'type':'item.completed', 'item':{'type':'reasoning','text':'PRIVATE_REASONING'}},
                  {'type':'assistant','message':{'content':[{'text':'PRIVATE_MESSAGE'}]}},
                  {'type':'item.completed','item':{'type':'command_execution','status':'completed','exit_code':1,'aggregated_output':'PRIVATE_OUTPUT'}},
                  {'type':'error','message':'model not supported'}]
        (p/'events.jsonl').write_text('\n'.join(json.dumps(x) for x in events))
        text = observer.log_window(self.run, observer.catalog(self.run)[0]['id'])['text']
        self.assertNotIn('PRIVATE', text)
        self.assertIn('exit=1', text)
        self.assertIn('model not supported', text)

    def test_quoted_credentials_are_fully_redacted(self):
        p = self.attempt()
        samples = ['password="NIGHT SHIFT PASSWORD 42"',
                   "{'api_key': 'SIMULATED_SINGLE_QUOTED_KEY_CANARY'}",
                   json.dumps({'secret': 'SIMULATED "quoted" CANARY'}),
                   "access_token='SIMULATED TOKEN WITH SPACES'"]
        (p/'stderr.log').write_text('\n'.join(samples), encoding='utf-8')
        text = observer.log_window(self.run, observer.catalog(self.run)[0]['id'])['text']
        for fragment in ('NIGHT', 'SHIFT', 'PASSWORD 42', 'SIMULATED', 'CANARY', 'SPACES'):
            self.assertNotIn(fragment, text)

    def test_malformed_optional_evidence_does_not_hide_valid_records(self):
        folder = self.run/'checks/task-A-1'; folder.mkdir(parents=True)
        (folder/'check-abc.log').write_text('valid check output')
        memory.atomic_json(folder/'results.json', [{'log':None}, {'log':'check-abc.log', 'task_id':'A'}])
        review = self.run/'reviews/damaged'; review.mkdir(parents=True)
        memory.atomic_json(review/'context.json', [])
        memory.atomic_json(review/'report.json', {'decision':'revise'})
        (self.attempt()/'process.json').write_text('null')
        (self.attempt()/'events.jsonl').write_text('{"type":null}\n{"type":"turn.completed"}\n')
        detail = observer.detail(self.service, self.job)
        self.assertEqual(len(detail['tasks']), 2)
        event = next(a for a in detail['artifacts'] if a['kind']=='events')
        self.assertEqual(observer.log_window(self.run, event['id'])['text'], 'turn.completed')
        self.assertTrue(any(a['path'].endswith('check-abc.log') for a in detail['artifacts']))
        process = next(a for a in detail['artifacts'] if a['kind']=='process')
        with self.assertRaises(checks.Rejected): observer.log_window(self.run, process['id'])

    def test_structured_log_projection_respects_byte_cap(self):
        folder = self.run/'reviews/large'; folder.mkdir(parents=True)
        memory.atomic_json(folder/'report.json', {'summary':'失败'*40000, 'decision':'revise'})
        artifact = next(a for a in observer.catalog(self.run) if a['kind']=='review')
        result = observer.log_window(self.run, artifact['id'])
        self.assertLessEqual(len(result['text'].encode('utf-8')), observer.MAX_BYTES)
        self.assertTrue(result['truncated'])

    def test_arbitrary_path_unknown_id_and_unregistered_run_rejected(self):
        for path in ('../control/auth.token', 'a'*24, str(self.base/'control/auth.token')):
            with self.assertRaises(checks.Rejected): observer.log_window(self.run, path)
        with self.assertRaises(checks.Rejected): observer.detail(self.service, 'a'*24)
        with self.assertRaises(checks.Rejected): observer.safe(self.run, self.base/'control/auth.token')

    def test_symlink_log_is_never_served(self):
        p = self.attempt()
        target = self.base/'secret.txt'; target.write_text('outside secret')
        try: (p/'stderr.log').symlink_to(target)
        except OSError: self.skipTest('Host cannot create symlinks')
        self.assertEqual(observer.catalog(self.run), [])

    def test_forged_check_path_is_not_read(self):
        folder = self.run/'checks/task-A-1'; folder.mkdir(parents=True)
        memory.atomic_json(folder/'results.json', [{'log':'../../outside.log','evidence_path':str(self.base/'control/auth.token'),'task_id':'A'}])
        self.assertEqual(observer.catalog(self.run), [])

    def test_tail_has_bound_and_partial_line_is_dropped(self):
        p = self.attempt(); (p/'stderr.log').write_text('X'*70000+'\nlast line\n')
        result = observer.log_window(self.run, observer.catalog(self.run)[0]['id'])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['text'].splitlines(),['last line'])

    def test_partial_process_record_does_not_break_run_details(self):
        (self.attempt()/'process.json').write_text('{')
        result = observer.detail(self.service, self.job)
        self.assertEqual(result['tasks'][0]['processes'], [])
        self.assertTrue(result['artifacts'])

    def test_timeline_is_bounded_and_filters_unsafe_payloads(self):
        bus = Bus(self.run)
        for i in range(205): bus.event('progress', task='A', attempt=i, prompt='do not display')
        result = observer.detail(self.service, self.job)
        self.assertEqual(len(result['timeline']), 200)
        self.assertTrue(result['timeline_truncated'])
        self.assertTrue(all('prompt' not in e['data'] for e in result['timeline']))

    def test_corrupt_run_is_visible_as_recovery_needed_in_overview(self):
        (self.run/'plan.json').write_text('{}')
        overview = observer.overview(self.service)
        self.assertEqual(overview['runs'][0]['status'], 'recovery_needed')
        self.assertIn('error', overview['runs'][0])

    def test_http_observation_requires_auth_and_assets_have_no_project_data(self):
        server = ThreadingHTTPServer(('127.0.0.1',0), service.handler(self.service))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            for suffix in ('/api/observe', '/api/observe/'+self.job, '/api/observe/'+self.job+'/logs/nope'):
                with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(url+suffix)
                self.assertEqual(e.exception.code,401)
                e.exception.close()
            for suffix in ('/','/dashboard.js','/dashboard.css'):
                with urllib.request.urlopen(url+suffix) as response:
                    self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
                    body=response.read().decode()
                    self.assertNotIn(self.service.token,body)
            request=urllib.request.Request(url+'/api/observe/'+self.job, headers={'Authorization':'Bearer '+self.service.token})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.load(response)['job_id'],self.job)
            with urllib.request.urlopen(url+'/favicon.ico') as response:
                self.assertEqual(response.status,204)
                self.assertEqual(response.read(),b'')
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_worker_stdout_visible_before_process_exit(self):
        script=self.base/'stream.py'; release=self.base/'release'
        script.write_text("import sys,time\nfrom pathlib import Path\nprint('{\"type\":\"turn.started\"}',flush=True)\nend=time.monotonic()+6\nwhile not Path(sys.argv[1]).exists() and time.monotonic()<end: time.sleep(.05)\n")
        config={'harness':{'kind':'command','executable':sys.executable,'args':['-u',str(script),str(release)]},
                'limits':{'attempt_seconds':10,'no_progress_seconds':10,'max_output_bytes':100000}}
        output=self.base/'stream-output'; results=[]
        t=threading.Thread(target=lambda:results.append(runtime.run_worker(config,self.base,coord.ROOT,'probe',output,lambda:'',10,self.base/'cancel')))
        t.start()
        try:
            end=time.monotonic()+4
            while time.monotonic()<end:
                if (output/'events.jsonl').exists() and (output/'events.jsonl').stat().st_size:break
                time.sleep(.05)
            self.assertTrue(t.is_alive())
            self.assertIn('turn.started',(output/'events.jsonl').read_text())
        finally:release.write_text('stop');t.join(timeout=12)
        self.assertEqual(results[0]['exit_code'],0)


if __name__=='__main__': unittest.main()
