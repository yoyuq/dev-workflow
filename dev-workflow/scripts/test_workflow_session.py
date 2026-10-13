"""Interactive progress reports are distinct from independently verified runs."""
import hashlib
import http.cookiejar
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import check_night_run as checks
import workflow_session as session
from workflow_service import handler


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'view'
        self.plan={'name':'test-session','goal':'Build the agreed feature','tasks':[
            {'id':'A','goal':'API'}, {'id':'B','goal':'UI','depends_on':['A']}]}
        session.initialize(self.root,self.plan)

    def tearDown(self): self.tmp.cleanup()

    def test_short_title_survives_dynamic_plan_without_changing_goal(self):
        session.update(self.root,{'expected_sequence':0,'reason':'补充检查',
            'add':[{'id':'TEST','title':'验证接口','goal':'Full detailed behavior requirements','depends_on':['A']}]})
        task=session.detail(self.root)['tasks'][-1]
        self.assertEqual(task['title'],'验证接口')
        self.assertEqual(task['goal'],'Full detailed behavior requirements')
        self.assertEqual(task['depends_on'],['A'])
        for title in ['x'*41,'中'*13,'中文\n换行',' 中文']:
            with self.subTest(title=title),self.assertRaises(checks.Rejected):
                session.new_task({'id':'BAD','goal':'full','title':title})

    def test_progress_and_completion_need_actual_recorded_evidence(self):
        session.update(self.root,{'expected_sequence':0,'updates':[{'id':'A','status':'running','action':'实现接口'}]})
        d=session.detail(self.root)
        self.assertEqual(d['source'],'interactive');self.assertEqual(d['tasks'][0]['activity']['text'],'实现接口')
        with self.assertRaisesRegex(checks.Rejected,'evidence'):
            session.update(self.root,{'expected_sequence':1,'updates':[{'id':'A','status':'completed'}]})
        self.assertEqual(session.read(self.root)['sequence'],1)
        session.update(self.root,{'expected_sequence':1,'updates':[{'id':'A','status':'completed','evidence':['pytest: 8 passed']}]})
        session.update(self.root,{'expected_sequence':2,'updates':[{'id':'B','status':'completed','evidence':['Browser form validation passed']}]})
        self.assertEqual(session.detail(self.root)['snapshot']['phase'],'completed')
        self.assertIsNone(session.detail(self.root)['tasks'][0]['commit'])

    def test_completion_with_unfinished_dependency_is_atomic(self):
        with self.assertRaisesRegex(checks.Rejected,'prerequisites'):
            session.update(self.root,{'expected_sequence':0,'updates':[{'id':'B','status':'completed','evidence':['claim']}]})
        self.assertEqual(session.read(self.root)['sequence'],0)

    def test_replan_changes_graph_and_preserves_completed_task(self):
        session.update(self.root,{'expected_sequence':0,'updates':[{'id':'A','status':'completed','evidence':['checks passed']}]})
        session.update(self.root,{'expected_sequence':1,'reason':'Need input samples',
            'add':[{'id':'PREP','goal':'Input samples','depends_on':['A']}], 'dependencies':{'B':['PREP']}})
        d=session.detail(self.root)
        self.assertEqual(d['snapshot']['revision'],1)
        self.assertEqual(d['plan_changes'][0]['dependencies'],[{'task':'B','before':['A'],'after':['PREP']}])
        self.assertEqual(d['tasks'][2]['introduced_revision'],1)
        with self.assertRaises(checks.Rejected):session.update(self.root,{'expected_sequence':2,'updates':[{'id':'A','status':'running'}]})

    def test_stale_writers_and_cycles_cannot_overwrite_progress(self):
        session.update(self.root,{'expected_sequence':0,'updates':[{'id':'A','status':'running','action':'First writer'}]})
        with self.assertRaisesRegex(checks.Rejected,'stale'):
            session.update(self.root,{'expected_sequence':0,'updates':[{'id':'A','status':'failed'}]})
        with self.assertRaisesRegex(checks.Rejected,'cycle'):
            session.update(self.root,{'expected_sequence':1,'reason':'bad','dependencies':{'A':['B']}})
        self.assertEqual(session.read(self.root)['sequence'],1)

    def test_retry_clears_old_evidence_and_updates_action(self):
        session.update(self.root,{'expected_sequence':0,'updates':[{'id':'A','status':'running','action':'Old action'}]})
        session.update(self.root,{'expected_sequence':1,'updates':[{'id':'A','status':'failed','evidence':['failed check']}]})
        session.update(self.root,{'expected_sequence':2,'updates':[{'id':'A','status':'running','action':'New approach'}]})
        task=session.detail(self.root)['tasks'][0]
        self.assertEqual(task['attempts'],2);self.assertEqual(task['evidence'],[])
        self.assertEqual(task['activity']['text'],'New approach')

    def test_duplicate_initialize_does_not_damage_existing_session(self):
        before=session.read(self.root)
        with self.assertRaises(FileExistsError):session.initialize(self.root,self.plan)
        self.assertEqual(session.read(self.root),before)

    def test_public_projection_redacts_reports(self):
        session.update(self.root,{'expected_sequence':0,'updates':[{'id':'A','status':'running',
            'action':'password="PRIVATE WITH SPACES"','evidence':['api_key=PRIVATE_KEY']}]})
        self.assertNotIn('PRIVATE',json.dumps(session.detail(self.root)))

    def test_bounded_history_and_no_arbitrary_logs_or_scheduling(self):
        for seq in range(201):session.update(self.root,{'expected_sequence':seq,'updates':[{'id':'A','status':'running','action':str(seq)}]})
        app=session.SessionViewer(self.root);d=app.observe_run(session.read(self.root)['id'])
        self.assertEqual(len(d['timeline']),200);self.assertTrue(d['timeline_truncated'])
        for fn in (lambda:app.observe_log('../secret','x'),lambda:app.submit({'run_dir':'x'}),lambda:app.observe_run('unknown')):
            with self.assertRaises(checks.Rejected):fn()

    def test_connection_grant_is_single_use_expiring_and_auth_is_required(self):
        app=session.SessionViewer(self.root)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(app))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        def connect(code):
            request=urllib.request.Request(url+'/api/session/connect',data=b'',headers={'X-Workflow-Connect':code})
            with urllib.request.urlopen(request) as response:return json.load(response)
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(url+'/api/observe')
            self.assertEqual(error.exception.code,401);error.exception.close()
            with urllib.request.urlopen(url) as response:self.assertNotIn(app.token,response.read().decode())
            grant=session.connect_url(self.root,{'url':url}).split('#connect=')[1]
            self.assertEqual(connect(grant),{'connected':True})
            with self.assertRaises(urllib.error.HTTPError) as error:connect(grant)
            self.assertEqual(error.exception.code,401);error.exception.close()
            session.private_json(self.root/'connect.json',{'digest':hashlib.sha256(grant.encode()).hexdigest(),'expires':time.time()-1})
            with self.assertRaises(urllib.error.HTTPError) as error:connect(grant)
            error.exception.close()
            request=urllib.request.Request(url+'/api/observe',headers={'Authorization':'Bearer '+app.token})
            with urllib.request.urlopen(request) as response:self.assertEqual(len(json.load(response)['runs']),1)
        finally:server.shutdown();server.server_close();thread.join()

    def test_browser_connection_survives_navigation_but_rejects_foreign_origin_and_logout(self):
        app=session.SessionViewer(self.root)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(app))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url=f'http://127.0.0.1:{server.server_port}'
        jar=http.cookiejar.CookieJar()
        browser=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def get(path):
            with browser.open(url+path) as response:return json.load(response)
        try:
            self.assertEqual(get('/api/session/status'),{'connected':False})
            grant=session.connect_url(self.root,{'url':url}).split('#connect=')[1]
            request=urllib.request.Request(url+'/api/session/connect',data=b'',headers={'X-Workflow-Connect':grant})
            with browser.open(request) as response:
                self.assertEqual(json.load(response),{'connected':True})
                header=response.headers['Set-Cookie']
                self.assertIn('HttpOnly',header);self.assertIn('SameSite=Strict',header)
                self.assertNotIn(app.token,header)
            self.assertEqual(get('/api/session/status'),{'connected':True})
            with browser.open(url) as response:self.assertIn('data-session-view="true"',response.read().decode())
            self.assertEqual(len(get('/api/observe')['runs']),1)
            # A new tab in the same browser shares the cookie jar, not JS memory.
            second=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            with second.open(url+'/api/observe') as response:self.assertEqual(len(json.load(response)['runs']),1)
            for headers in ({'Origin':'https://other.example'}, {'Host':'other.example'}, {'Sec-Fetch-Site':'cross-site'}):
                request=urllib.request.Request(url+'/api/observe',headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as error:browser.open(request)
                self.assertEqual(error.exception.code,403);error.exception.close()
            cookie='; '.join(c.name+'='+c.value for c in jar)
            # A different or restarted viewer cannot use this connection.
            self.assertFalse(session.SessionViewer(self.root).browser_authorized(cookie))
            with browser.open(urllib.request.Request(url+'/api/session/disconnect',data=b'')) as response:
                self.assertEqual(json.load(response),{'connected':False})
            self.assertEqual(get('/api/session/status'),{'connected':False})
            self.assertFalse(app.browser_authorized(cookie))
            with self.assertRaises(urllib.error.HTTPError) as error:browser.open(url+'/api/observe')
            self.assertEqual(error.exception.code,401);error.exception.close()
        finally:server.shutdown();server.server_close();thread.join()

    def test_open_reuses_local_viewer_and_close_only_stops_preview(self):
        first=session.open_viewer(self.root)
        info=session.alive(self.root)
        self.assertTrue(first['url'].startswith('http://127.0.0.1:'))
        self.assertNotIn(info['token'],first['url'])
        try:
            second=session.open_viewer(self.root)
            self.assertEqual(session.alive(self.root)['pid'],info['pid'])
            self.assertNotEqual(first['url'],second['url'])
            self.assertFalse((self.root/'coordination.sqlite3').exists())
            self.assertEqual(session.read(self.root)['sequence'],0)
        finally:
            session.private_json(self.root/'close.json',{'instance':info['instance']})
            end=time.monotonic()+5
            while time.monotonic()<end and session.alive(self.root):time.sleep(.1)
        self.assertIsNone(session.alive(self.root))
        self.assertTrue((self.root/'session.sqlite3').exists())


if __name__=='__main__':unittest.main()
