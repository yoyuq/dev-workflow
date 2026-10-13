"""Translations are source-bound metadata, never execution or acceptance inputs."""
import copy,json
from pathlib import Path
import tempfile,unittest,sqlite3
import check_night_run as checks
import workflow_depth as depth
import workflow_display as display
import workflow_session as session
import workflow_observer as observer


def entry(source,text): return {'source':source,'text':text}


class DisplayTests(unittest.TestCase):
    def test_source_binding_falls_back_when_an_action_changes(self):
        task={'action':'旧动作','display':{'en':{'action':entry('旧动作','Old action')}}}
        self.assertEqual(display.field(task,'action','en'),'Old action')
        task['action']='新的实际动作'
        self.assertEqual(display.field(task,'action','en'),'新的实际动作')

    def test_invalid_unknown_stale_or_evidence_overrides_are_rejected(self):
        task={'goal':'原始目标'}
        for value in ({'fr':{}},{'en':{'evidence':entry('原始目标','override')}},
                      {'en':{'goal':entry('different source','new')}},
                      {'en':{'goal':entry('原始目标','bad\x00text')}},
                      {'en':{'goal':{'source':'原始目标','text':'new','scope':'outside'}}}):
            with self.subTest(value=value),self.assertRaises(checks.Rejected):display.validate(task,display.TASK_LIMITS,value)

    def test_display_update_preserves_goal_status_attempts_evidence_and_dependencies(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'view'
            session.initialize(root,{'name':'原始项目','goal':'总目标','tasks':[{'id':'A','goal':'接口要求'},{'id':'B','goal':'页面要求','depends_on':['A']}]})
            before=session.read(root)
            updated=session.update(root,{'expected_sequence':0,'display':{'en':{'name':entry('原始项目','Original project'),'goal':entry('总目标','Overall goal')}},'updates':[{'id':'B','display':{'en':{'goal':entry('页面要求','Page requirements')}}}]})
            for tid in before['tasks']:
                actual={k:v for k,v in updated['tasks'][tid].items() if k!='display'}
                self.assertEqual(actual,before['tasks'][tid])
            self.assertEqual(updated['goal'],before['goal']);self.assertEqual(updated['name'],before['name'])
            observed=session.detail(root);self.assertEqual(observed['tasks'][1]['display']['en']['goal']['text'],'Page requirements')
            with self.assertRaises(checks.Rejected):session.update(root,{'expected_sequence':1,'updates':[{'id':'B','status':'completed','evidence':['translated claim']}]})
            self.assertEqual(session.read(root),updated)

    def test_source_mismatch_rolls_back_the_entire_update(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'view'
            session.initialize(root,{'goal':'原目标','tasks':[{'id':'A','goal':'任务目标'}]})
            before=session.read(root)
            with self.assertRaises(checks.Rejected):session.update(root,{'expected_sequence':0,'display':{'en':{'goal':entry('原目标','Overall goal')}},'updates':[{'id':'A','display':{'en':{'goal':entry('wrong','Task goal')}}}]})
            self.assertEqual(session.read(root),before)

    def test_project_projection_has_complete_english_copy_and_keeps_canonical_facts(self):
        plan=json.loads((Path(__file__).resolve().parents[1]/'assets/project-plan.example.json').read_text(encoding='utf-8'))
        canonical=copy.deepcopy(plan);view=depth.view(plan)
        self.assertEqual(display.field(view,'name','en'),'Course booking upgrade example')
        for raw,projected in zip(plan['tasks'],view['tasks']):
            self.assertIn(raw['goal'],projected['goal']);self.assertEqual(raw['depends_on'],projected['depends_on'])
            translated=display.field(projected,'goal','en')
            self.assertTrue(all(not '\u4e00'<=c<='\u9fff' for c in translated))
            for cid in raw['criterion_ids']:self.assertIn(cid,translated)
        self.assertEqual(plan,canonical);self.assertFalse(depth.audit(plan)['ready'])

    def test_public_progress_translation_is_bound_to_current_attempt_and_text(self):
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE messages(id TEXT,payload TEXT,at REAL,sender TEXT,recipient TEXT,kind TEXT,seq INTEGER)')
        payload={'attempt':2,'text':'读取接口','data':{'display':{'en':{'text':entry('读取接口','Inspecting API')}}}}
        db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)',('m',json.dumps(payload),1,'A','coordinator','progress',1))
        try:
            activity=observer.current_activity(db,'A',{'status':'running','attempts':2})
            self.assertEqual(display.field(activity,'text','en'),'Inspecting API');self.assertEqual(activity['text'],'读取接口')
            self.assertIsNone(observer.current_activity(db,'A',{'status':'running','attempts':3}))
            payload['data']['display']['en']['text']['source']='stale'
            db.execute('UPDATE messages SET payload=?',(json.dumps(payload),))
            self.assertNotIn('display',observer.current_activity(db,'A',{'status':'running','attempts':2}))
        finally:db.close()

    def test_artifact_copy_changes_only_labels_not_file_paths_or_raw_logs(self):
        with tempfile.TemporaryDirectory() as folder:
            # Registered runs use canonical roots; Windows TEMP may use an 8.3 alias.
            root=Path(folder).resolve();attempt=root/'attempts/A/1';attempt.mkdir(parents=True)
            raw='Original 中文 log';(attempt/'stderr.log').write_text(raw,encoding='utf-8')
            rows=observer.catalog(root);self.assertEqual(len(rows),1)
            item=rows[0];self.assertEqual(display.field(item,'label','en'),'A / Attempt 1 · Error output')
            self.assertEqual(item['path'],'attempts/A/1/stderr.log');self.assertEqual((attempt/'stderr.log').read_text(encoding='utf-8'),raw)


if __name__=='__main__':unittest.main()
