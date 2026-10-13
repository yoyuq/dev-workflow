"""Language changes must preserve task facts, authority and raw evidence."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import urllib.request

import check_night_run as checks
import workflow_locale as locale
import workflow_titles as titles
import workflow_depth as depth
import workflow_session as session
from workflow_service import handler


class LocaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root/'preferences.json'
        self.patch = patch.object(locale, 'preference_path', return_value=self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def test_portable_default_is_english_without_browser_or_host_inference(self):
        self.assertEqual(locale.resolve(), 'en')
        self.assertFalse(self.path.exists())

    def test_explicit_language_overrides_saved_preference(self):
        locale.save('zh-CN')
        self.assertEqual(locale.resolve(), 'zh-CN')
        self.assertEqual(locale.resolve('en-US'), 'en')
        self.assertEqual(json.loads(self.path.read_text())['locale'], 'zh-CN')
        with self.assertRaises(ValueError): locale.resolve('fr')

    def test_corrupt_missing_or_unknown_preference_falls_back_without_rewriting(self):
        for raw in ('bad json', '[]', '{"locale":"fr"}', '{"locale":42}'):
            self.path.write_text(raw)
            self.assertEqual(locale.resolve(), 'en')
            self.assertEqual(self.path.read_text(), raw)

    def test_saving_preserves_other_fields_and_does_not_clobber_corrupt_file(self):
        self.path.write_text('{"other":"keep"}')
        locale.save('zh-Hans')
        self.assertEqual(json.loads(self.path.read_text()), {'other':'keep','locale':'zh-CN'})
        self.path.write_text('invalid')
        with self.assertRaises(ValueError): locale.save('en')
        self.assertEqual(self.path.read_text(), 'invalid')

    def test_english_and_bilingual_titles_validate_without_weakening_shortness(self):
        titles.validate({'title':'Create booking','titles':{'en':'Create booking','zh-CN':'创建预约'}})
        for item in ('A', 'x'*41, '中'*13, ' leading', 'line\nbreak', 'a\x7fb', '!!!'):
            with self.subTest(item=item), self.assertRaises(checks.Rejected): titles.validate({'title':item})
        for mapping in ({}, {'fr':'Bonjour'}, {'en':42}, {'en':'line\nbreak'}):
            with self.subTest(mapping=mapping), self.assertRaises(checks.Rejected): titles.validate({'titles':mapping})

    def test_bilingual_session_additions_preserve_raw_action_evidence_and_gate(self):
        labels={'en':'Create booking','zh-CN':'创建预约'}
        session.initialize(self.root/'session', {'goal':'Original 目标','locale':'en','tasks':[{'id':'A','title':'Define API','goal':'Unchanged API'}]})
        session.update(self.root/'session', {'expected_sequence':0,'reason':'Add dependent behavior','add':[{'id':'B','title':'Create booking','titles':labels,'goal':'Original detail 中文','depends_on':['A']}]})
        labels['en']='External mutation'
        d=session.detail(self.root/'session')
        self.assertEqual(d['tasks'][1]['titles']['en'], 'Create booking')
        self.assertEqual(d['tasks'][1]['goal'], 'Original detail 中文')
        before=session.read(self.root/'session')
        with self.assertRaises(checks.Rejected): session.update(self.root/'session', {'expected_sequence':1,'updates':[{'id':'B','status':'completed','evidence':['claim']}]})
        self.assertEqual(session.read(self.root/'session'), before)

    def test_plan_languages_preserve_coverage_dependencies_and_draft_authority(self):
        p=json.loads((Path(__file__).resolve().parents[1]/'assets/project-plan.example.json').read_text(encoding='utf-8'))
        before=copy.deepcopy(p);p['locale']='en'
        en=depth.render(p);view=depth.view(p);english_audit=depth.audit(p)
        p['locale']='zh-CN';zh=depth.render(p)
        self.assertIn('## Execution nodes', en);self.assertIn('## 执行节点', zh)
        self.assertIn('Create booking', en);self.assertIn('创建预约', zh)
        self.assertEqual(depth.audit(p), english_audit);self.assertFalse(english_audit['ready'])
        self.assertEqual(view['locale'], 'en')
        for actual, original in zip(view['tasks'],before['tasks']):
            self.assertEqual(actual['id'],original['id']);self.assertEqual(actual['depends_on'],original['depends_on'])
            self.assertIn(original['goal'],actual['goal'])
        self.assertEqual(p, before)

    def test_http_default_preference_asset_and_authentication_are_independent(self):
        session.initialize(self.root/'session', {'goal':'Observe only','tasks':[{'id':'A','goal':'No execution'}]})
        app=session.SessionViewer(self.root/'session')
        server=ThreadingHTTPServer(('127.0.0.1',0),handler(app))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            with urllib.request.urlopen(base) as response:
                self.assertIn('data-preferred-locale="en"',response.read().decode())
                self.assertIn("script-src 'self'",response.headers['Content-Security-Policy'])
            locale.save('zh-CN')
            with urllib.request.urlopen(base) as response:self.assertIn('data-preferred-locale="zh-CN"',response.read().decode())
            with urllib.request.urlopen(base+'/i18n.js') as response:self.assertIn('text/javascript',response.headers['Content-Type'])
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(base+'/api/observe')
            self.assertEqual(error.exception.code,401)
            error.exception.close()
            self.assertEqual(session.read(self.root/'session')['sequence'],0)
        finally:server.shutdown();server.server_close();thread.join()


if __name__ == '__main__': unittest.main()
