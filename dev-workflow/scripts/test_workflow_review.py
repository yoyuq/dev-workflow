import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import check_night_run as checks
import workflow_coordinator as coord
import workflow_review as review
from workflow_bus import Bus
from test_workflow_coordinator import fixture
from test_workflow_replan import repin_fixture

REVIEWER = r'''
import json, sys
from pathlib import Path
if '--version' in sys.argv: print('simulated-reviewer'); raise SystemExit(0)
sys.stdin.read()
c=json.loads(Path('context.json').read_text())
mode=sys.argv[1]
first=c['binding']['task_id']=='A' and len(list(Path.cwd().parent.glob('*/report.json')))==0
bad=mode=='reject_first' and first or mode=='reject_final' and c['binding']['task_id']=='integration'
r={'binding':c['binding'],'decision':'changes_requested' if bad else 'approve','summary':'Simulated independent assessment',
   'findings':[{'task_id':'A','file':'a.txt','line':1,'category':'quality','severity':'blocking','reason':'Simulated fix request'}] if bad else [],
   'validation':['Deterministic simulated review; not actual model quality judgment']}
if mode=='mutate': (Path(c['workspace'])/'a.txt').write_text('tampered')
if mode=='mutate_context': Path('context.json').write_text('{}')
if mode=='mutate_brief': Path('brief.json').write_text('{}')
if mode=='mutate_baseline': Path(c['fresh_baseline']['records'][0]['evidence_path']).write_text('fake pass')
if mode=='wrong_binding': r['binding']['head']='wrong'
if mode!='missing': Path('report.json').write_text(json.dumps(r))
if mode=='failed_with_report': raise SystemExit(1)
'''


class ReviewTests(unittest.TestCase):
    def execute(self, mode):
        base = Path(self.temp.name).resolve()
        plan, root = fixture(base)
        executable = base / "reviewer.py"
        executable.write_text(REVIEWER)
        configs = checks.read_json(root / "runtime.json")
        configs["reviewer"] = copy.deepcopy(configs["worker"])
        configs["reviewer"]["harness"]["args"] = [str(executable), mode]
        configs["reviewer"]["protected_files"].append(str(executable))
        repin_fixture(root, plan, configs)
        return coord.run(root), Bus(root).state()

    def setUp(self): self.temp = tempfile.TemporaryDirectory()
    def tearDown(self): self.temp.cleanup()

    def test_task_and_whole_branch_are_separate_processes(self):
        code, state = self.execute("approve")
        self.assertEqual(code, 0)
        self.assertEqual(state["review_rounds"], 2)
        self.assertEqual([r["binding"]["task_id"] for r in state["review_history"]], ["A", "integration"])
        self.assertTrue(all(Path(r["report_path"]).is_file() for r in state["review_history"]))

    def test_blocking_review_returns_to_worker_with_feedback(self):
        code, state = self.execute("reject_first")
        self.assertEqual(code, 0)
        self.assertEqual(state["tasks"]["A"]["attempts"], 2)
        self.assertEqual(state["review_rounds"], 3)
        contexts = [checks.read_json(Path(r["report_path"]).parent / "context.json") for r in state["review_history"]]
        self.assertEqual(contexts[1]["prior_reviews"][0]["findings"][0]["reason"], "Simulated fix request")
        self.assertEqual(contexts[2]["base_commit"], contexts[2]["project_base_commit"])
        self.assertTrue(contexts[2]["checks"][0]["task_checks"])

    def test_final_review_blocks_delivery_and_bounded_rework(self):
        code, state = self.execute("reject_final")
        self.assertEqual(code, 3)
        self.assertNotEqual(state["status"], "completed")
        self.assertEqual(state["tasks"]["A"]["attempts"], 2)

    def test_missing_report_cannot_approve(self):
        code, state = self.execute("missing")
        self.assertEqual(code, 3)
        self.assertEqual(state["tasks"]["A"]["status"], "blocked")

    def test_valid_draft_from_failed_process_cannot_approve(self):
        code,state=self.execute('failed_with_report')
        self.assertEqual(code,3)
        self.assertEqual(state['tasks']['A']['status'],'blocked')
        self.assertIn('review process failed',state['tasks']['A']['reason'])
        self.assertFalse(state.get('review_history'))

    def test_modified_source_cannot_approve(self):
        code, state = self.execute("mutate")
        self.assertEqual(code, 3)
        self.assertIn("modified project", state["tasks"]["A"]["reason"])

    def test_modified_review_input_cannot_approve(self):
        code, state = self.execute('mutate_context')
        self.assertEqual(code,3)
        self.assertIn('modified review inputs',state['tasks']['A']['reason'])

    def test_wrong_binding_cannot_approve(self):
        code, state = self.execute("wrong_binding")
        self.assertEqual(code, 3)
        self.assertIn("binding mismatch", state["tasks"]["A"]["reason"])

    def test_brief_and_fresh_baseline_cannot_be_rewritten(self):
        for mode in ('mutate_brief', 'mutate_baseline'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                previous = self.temp
                class Location: name = tmp
                self.temp = Location()
                try:
                    code, state = self.execute(mode)
                    self.assertEqual(code, 3)
                    self.assertIn('modified review inputs', state['tasks']['A']['reason'])
                finally:
                    self.temp = previous

    def test_review_baseline_is_fresh_on_bound_workspace(self):
        code, state = self.execute('approve')
        self.assertEqual(code, 0)
        for record in state['review_history']:
            folder = Path(record['report_path']).parent
            context = checks.read_json(folder/'context.json')
            baseline = context['fresh_baseline']
            self.assertEqual(baseline['binding'], record['binding'])
            self.assertTrue(all(r['result']=='passed' and r['cwd']==context['workspace'] for r in baseline['records']))
            self.assertTrue(all(Path(r['evidence_path']).is_relative_to(folder/'baseline') for r in baseline['records']))
            self.assertGreaterEqual(record['timing']['total_seconds'], record['timing']['model_seconds'])

    def test_failing_review_baseline_never_launches_model(self):
        plan, root = fixture(Path(self.temp.name).resolve())
        state = Bus(root).state()
        # Base has no a.txt: the real pinned check fails, regardless of earlier claims.
        config = checks.read_json(root/'runtime.json')['worker']
        with patch.object(coord, 'supervised_worker') as model:
            with self.assertRaisesRegex(checks.Rejected, 'fresh review baseline failed'):
                review.evaluate(root, plan, state, Bus(root), config, plan['base_commit'], 'A', 30)
            model.assert_not_called()

    def test_repair_delta_keeps_full_diff_and_falls_back_on_changed_contract_or_base(self):
        plan, root = fixture(Path(self.temp.name).resolve())
        repo = Path(plan['repository']); state = Bus(root).state()
        (repo/'a.txt').write_text('bad')
        coord.git(repo,'add','a.txt');coord.git(repo,'commit','-m','first')
        oldhead=coord.git(repo,'rev-parse','HEAD')
        priorfolder=root/'first';priorfolder.mkdir()
        oldcontext=review.prepare_context(plan,state,oldhead,'A',repo,priorfolder)
        report={'binding':oldcontext['binding'],'decision':'changes_requested','summary':'bad value',
                'findings':[{'task_id':'A','file':'a.txt','line':1,'category':'spec','severity':'blocking','reason':'must be OK'}],
                'validation':['read actual bad value']}
        (priorfolder/'report.json').write_text(json.dumps(report))
        state['review_history']=[{**report,'report_path':str(priorfolder/'report.json'),
            'report_sha256':checks.digest(priorfolder/'report.json'),'input_evidence':{'base_commit':plan['base_commit']}}]
        # Retry starts from launch base, a sibling rather than descendant of oldhead.
        coord.git(repo,'checkout','--detach',plan['base_commit'])
        (repo/'a.txt').write_text('OK')
        coord.git(repo,'add','a.txt');coord.git(repo,'commit','-m','repair')
        head=coord.git(repo,'rev-parse','HEAD');folder=root/'repair';folder.mkdir()
        packet=review.prepare_context(plan,state,head,'A',repo,folder)
        self.assertEqual(packet['focus']['mode'],'repair')
        self.assertIn('-bad',(folder/'focus.patch').read_text())
        self.assertIn('+OK',(folder/'changes.patch').read_text())
        self.assertTrue(packet['prior_reviews'][0]['findings'])
        full=review.prepare_context(plan,state,head,'integration',repo,folder)
        self.assertEqual(full['focus']['mode'],'full')
        for changed in ('contracts','revision','base','same_head','missing_head'):
            altered=copy.deepcopy(state);r=altered['review_history'][0]
            if changed=='contracts':r['binding']['contracts']={'different':9}
            elif changed=='revision':r['binding']['revision']+=1
            elif changed=='base':r['input_evidence']['base_commit']=oldhead
            elif changed=='same_head':r['binding']['head']=head
            else:r['binding']['head']='f'*40
            self.assertEqual(review.prepare_context(plan,altered,head,'A',repo,folder)['focus']['mode'],'full',changed)

    def test_task_packet_excludes_already_integrated_diff_but_keeps_contracts(self):
        plan, root = fixture(Path(self.temp.name).resolve(), count=3)
        repo = Path(plan['repository'])
        state = Bus(root).state()
        (repo/'a.txt').write_text('previously accepted')
        coord.git(repo, 'add', 'a.txt'); coord.git(repo, 'commit', '-m', 'accepted A')
        launch = coord.git(repo, 'rev-parse', 'HEAD')
        (repo/'result.txt').write_text('new work')
        coord.git(repo, 'add', 'result.txt'); coord.git(repo, 'commit', '-m', 'new C')
        head = coord.git(repo, 'rev-parse', 'HEAD')
        state['tasks']['C']['launch_base'] = launch
        folder = root/'packet'; folder.mkdir()
        packet = review.prepare_context(plan,state,head,'C',repo,folder)
        self.assertEqual(packet['base_commit'], launch)
        self.assertNotIn('a.txt',packet['changes']['files'])
        self.assertIn('result.txt',packet['changes']['files'])
        self.assertEqual([t['id'] for t in packet['tasks']],['C'])
        self.assertEqual({t['id'] for t in packet['dependencies']},{'A','B'})
        self.assertEqual(packet['contracts'],state['contracts'])
        self.assertEqual(packet['requirements'],plan['requirements'])
        self.assertEqual(packet['check_commands'][0]['argv'],plan['tasks'][2]['verification'][0]['argv'])
        dependency_report={'binding':packet['binding'],'decision':'changes_requested','summary':'Dependency error affects C',
            'findings':[{'task_id':'A','file':'a.txt','line':1,'category':'quality','severity':'blocking','reason':'Observable upstream defect'}],
            'validation':['Reproduced through the changed consumer']}
        review.validate_report(dependency_report,packet)
        state['tasks']['A']['status']='integrated';state['tasks']['C']['status']='verified'
        review.request_fixes(plan,state,Bus(root),dependency_report)
        self.assertEqual(state['tasks']['A']['status'],'pending')
        self.assertEqual(state['tasks']['C']['status'],'pending')
        full = review.prepare_context(plan,state,head,'integration',repo,folder)
        self.assertIn('a.txt',full['changes']['files'])
        self.assertEqual(len(full['tasks']),3)

    def test_prior_report_tampering_blocks_packet(self):
        _, state = self.execute('approve')
        record = state['review_history'][0]
        Path(record['report_path']).write_text('{}')
        root=Path(self.temp.name).resolve()/'run'
        plan,_,_=coord.load(root)
        folder=root/'tamper-packet';folder.mkdir()
        with self.assertRaisesRegex(checks.Rejected,'prior review evidence drift'):
            review.prepare_context(plan,state,state['integration_head'],'integration',Path(plan['repository']),folder)

    def test_empty_validation_cannot_approve(self):
        context={'binding':{},'task_ids':['A']}
        report={'binding':{},'decision':'approve','summary':'claim','findings':[],'validation':[]}
        with self.assertRaisesRegex(checks.Rejected,'actual evidence'):
            review.validate_report(report,context)

    def test_prompt_embeds_small_complete_inputs_and_leaves_large_inputs_readable(self):
        folder=Path(self.temp.name)
        (folder/'brief.json').write_text(json.dumps({'focus':{'mode':'full','patch':'focus.patch'},'requirement':'exact sentinel'}))
        (folder/'focus.patch').write_text('+ complete delta sentinel')
        small=review.reviewer_prompt(folder)
        self.assertIn('exact sentinel',small)
        self.assertIn('+ complete delta sentinel',small)
        large='+' + 'x'*(49*1024)
        (folder/'focus.patch').write_text(large)
        prompt=review.reviewer_prompt(folder)
        self.assertIn('exact sentinel',prompt)
        self.assertNotIn(large,prompt)
        self.assertEqual((folder/'focus.patch').read_text(),large)

    def test_small_repair_receives_full_index_and_both_diffs_once(self):
        folder=Path(self.temp.name)
        (folder/'brief.json').write_text(json.dumps({'focus':{'mode':'repair','patch':'focus.patch'}}))
        (folder/'context.json').write_text(json.dumps({'complete_history':'unique history marker'}))
        (folder/'focus.patch').write_text('unique repair marker')
        (folder/'changes.patch').write_text('unique full task marker')
        prompt=review.reviewer_prompt(folder)
        for marker in ('unique history marker','unique repair marker','unique full task marker'):
            self.assertEqual(prompt.count(marker),1)


if __name__ == "__main__": unittest.main()
