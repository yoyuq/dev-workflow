"""Real isolated Git transactions, simulated models and fake provider; no network."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import check_night_run as checks
import workflow_coordinator as coord
import workflow_git_delivery as delivery
import workflow_memory as memory
from workflow_bus import Bus
from test_workflow_coordinator import fixture
from test_workflow_review import REVIEWER
from workflow_github import Blocked, identity


class Provider:
    def __init__(self, base, repo):
        self.remote = base/'provider.git';self.scratch = base/'provider-work'
        coord.git(base, 'clone', '--bare', str(repo), str(self.remote))
        coord.git(base, 'clone', str(self.remote), str(self.scratch))
        coord.git(self.scratch, 'config', 'user.name', 'Fake GitHub')
        coord.git(self.scratch, 'config', 'user.email', 'fake@example.invalid')
        self.prs = {};self.statuses = {};self.merges = 0;self.pending = False;self.crash = False;self.drift = False;self.crash_revert=False;self.ci_fail=False;self.ci_calls=0;self.crash_before_merge=False

    def inspect(self): return {'default_branch':'main','allow_merge_commit':True,'permissions':{'push':True}}
    def ref(self, branch):
        return coord.git(self.remote, 'rev-parse', 'refs/heads/'+branch) if coord.git(self.remote, 'branch', '--list', branch) else None
    def ensure_parent(self, branch, head):
        previous = self.ref(branch)
        if previous and previous != head: raise Blocked('parent changed')
        coord.git(self.remote, 'update-ref', 'refs/heads/'+branch, head)
    def push(self, repo, source, target, expected):
        coord.git(repo, 'push', str(self.remote), 'refs/heads/'+source+':refs/heads/'+target)
        assert self.ref(target) == expected
        if self.crash_revert and '/rollback-' in source:
            self.crash_revert=False;raise KeyboardInterrupt('planned crash after revert push')
    def ensure_pr(self, head, base, title, body, draft=False):
        for item in self.prs.values():
            if item['head']['ref'] == head and item['base']['ref'] == base: return copy.deepcopy(item)
        number = len(self.prs)+1
        item = {'number':number,'html_url':'https://github.com/example/test/pull/'+str(number),
            'head':{'ref':head,'sha':self.ref(head)},'base':{'ref':base},'merged':False,'draft':draft,'state':'open'}
        self.prs[number] = item;return copy.deepcopy(item)
    def pr(self, number):
        value = copy.deepcopy(self.prs[number])
        if self.drift and not value['draft']: value['head']['sha'] = 'f'*40
        return value
    def status(self, head, state, description): self.statuses[head] = state
    def ci(self, head, required):
        self.ci_calls+=1
        if self.crash_before_merge and self.ci_calls==2:
            self.crash_before_merge=False;raise KeyboardInterrupt('planned crash before remote merge')
        if self.ci_fail:
            self.ci_fail=False;return {'state':'failed','head':head,'checks':{'fake-ci':'failure'},'missing':[],'failed':{'fake-ci':'failure'}}
        return {'state':'pending' if self.pending else 'passed','head':head,'checks':{'fake-ci':'pending' if self.pending else 'success'},'missing':[],'failed':{}}
    def required_checks(self, branch): return []
    def merge(self, number, head, title):
        pr = self.prs[number];assert not pr['draft'] and pr['head']['sha'] == head
        coord.git(self.scratch, 'fetch', 'origin')
        coord.git(self.scratch, 'checkout', '-B', 'candidate', self.ref(pr['base']['ref']))
        coord.git(self.scratch, 'merge', '--no-ff', '-m', title, head)
        merged = coord.git(self.scratch, 'rev-parse', 'HEAD')
        coord.git(self.scratch, 'push', 'origin', 'HEAD:refs/heads/'+pr['base']['ref'])
        pr.update(merged=True, state='closed', merge_commit_sha=merged);self.merges += 1
        if self.crash:
            self.crash = False;raise KeyboardInterrupt('planned crash after remote mutation')
        return merged
    def fetch(self, repo, head): coord.git(repo, 'fetch', '--no-tags', str(self.remote), head)
    def commit(self, head):
        return {'tree':{'sha':coord.git(self.remote,'rev-parse',head+'^{tree}')},
            'parents':[{'sha':p} for p in coord.git(self.remote,'show','-s','--format=%P',head).split()]}


class FeaturePRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.base = Path(self.temp.name).resolve()
        self.plan, old = fixture(self.base, count=2)
        self.configs = checks.read_json(old/'runtime.json')
        self.plan.update(run_id='feature-pr-case', delivery='feature_prs')
        self.plan['limits'].update(parallelism=1, max_run_seconds=300)
        self.plan['tasks'][1]['depends_on'] = ['A']
        self.plan['review'] = {'max_rounds':8}
        self.marker = self.base/'planned-fault.txt'
        check = self.base/'post.py'
        check.write_text("from pathlib import Path\nimport sys\nr=Path(sys.argv[1]);marker=Path(sys.argv[2])\nassert (r/'base.txt').read_text()=='baseline'\nif r.name.startswith('parent-check-') and not marker.exists():\n marker.write_text('planned once');raise AssertionError('planned post-merge fault')\n",encoding='utf-8')
        self.plan['remote_git'] = {'provider':'github','repository_url':'https://github.com/example/test','base_branch':'main',
            'authorization':{'merge_features':True,'revert_failed_merge':True,'reference':'test fixture','quote':'isolated simulated remote merge and revert'},
            'ci_wait_seconds':1,'rollback_reserve_seconds':10,
            'post_merge_checks':[{'id':'actual-parent','argv':[sys.executable,'-B',str(check),'{repo}',str(self.marker)],'timeout_seconds':5}]}
        worker = self.base/'worker.py'
        worker.write_text(worker.read_text(encoding='utf-8').replace("else:\n    Path('a.txt').write_text('OK')", "else:\n    if tid == 'B': assert Path('a.txt').read_text() == 'OK'\n    Path(tid.lower()+'.txt').write_text('OK')"),encoding='utf-8')
        reviewer = self.base/'reviewer.py';reviewer.write_text(REVIEWER,encoding='utf-8')
        self.configs['reviewer'] = copy.deepcopy(self.configs['worker'])
        self.configs['reviewer']['harness']['args'] = ['-B',str(reviewer),'approve']
        self.configs['reviewer']['protected_files'].append(str(reviewer))
        repo = Path(self.plan['repository']);self.api = Provider(self.base, repo)
        coord.git(repo,'remote','add','origin',self.plan['remote_git']['repository_url'])
        self.root = self.base/'feature-run'
        self.patch = patch.object(delivery,'client',return_value=self.api);self.patch.start()

    def tearDown(self): self.patch.stop();self.temp.cleanup()
    def initialize(self):
        memory.atomic_json(self.base/'feature-plan.json',self.plan);memory.atomic_json(self.base/'feature-runtime.json',self.configs)
        coord.initialize(self.base/'feature-plan.json',self.base/'feature-runtime.json',self.root)
    def state(self): return Bus(self.root).state()

    def test_merge_regression_revert_repair_and_parent_draft(self):
        self.initialize();pins = checks.digest(self.root/'pins.json')
        self.assertEqual(coord.run(self.root),0);s=self.state()
        self.assertEqual(s['tasks']['A']['attempts'],2);self.assertEqual(s['tasks']['B']['attempts'],1)
        self.assertEqual([p['phase'] for p in s['remote_git']['prs']],['reverted','accepted','accepted'])
        self.assertEqual(len(s['remote_git']['rollbacks']),1)
        self.assertTrue(all(c['result']=='passed' for c in s['remote_git']['rollbacks'][0]['checks']))
        self.assertEqual(self.api.merges,3);self.assertEqual(self.api.ref('main'),self.plan['base_commit'])
        self.assertEqual(self.api.ref(s['integration_branch']),s['integration_head'])
        self.assertTrue(self.api.pr(s['remote_git']['parent_pr']['number'])['draft'])
        self.assertEqual(checks.digest(self.root/'pins.json'),pins)
        self.assertTrue(s['tasks']['A']['failure_cases'])
        self.assertEqual(s['tasks']['A']['reason'],'')
        self.assertEqual(checks.read_json(self.root/'delivery.json')['remote_git']['prs'],s['remote_git']['prs'])

    def test_pending_ci_holds_following_feature_and_resumes_same_attempt(self):
        self.marker.write_text('disable planned fault');self.api.pending=True;self.initialize()
        self.assertEqual(coord.run(self.root),3);s=self.state()
        self.assertIn('pending',s['remote_hold']);self.assertEqual(s['tasks']['B']['attempts'],0);self.assertEqual(self.api.merges,0)
        self.api.pending=False;self.assertEqual(coord.run(self.root),0)
        self.assertEqual(self.state()['tasks']['A']['attempts'],1)

    def test_remote_write_crash_recovers_without_duplicate_merge(self):
        self.marker.write_text('disable planned fault');self.api.crash=True;self.initialize()
        with self.assertRaises(KeyboardInterrupt): coord.run(self.root)
        self.assertTrue(self.state()['integration_intent']['remote']);self.assertEqual(self.api.merges,1)
        coord.recover(self.root);(self.root/'cancel.request').unlink()
        self.assertEqual(coord.run(self.root),0);self.assertEqual(self.api.merges,2)
        self.assertEqual(len(self.state()['remote_git']['prs']),2)
        self.assertEqual(self.state()['tasks']['A']['attempts'],1)

    def test_changed_pr_head_blocks_merging_and_new_dispatch(self):
        self.marker.write_text('disable planned fault');self.api.drift=True;self.initialize()
        self.assertEqual(coord.run(self.root),3);self.assertEqual(self.api.merges,0)
        self.assertEqual(self.state()['tasks']['B']['attempts'],0)

    def test_failed_ci_returns_to_worker_before_any_merge(self):
        self.marker.write_text('disable planned fault');self.api.ci_fail=True;self.initialize()
        self.assertEqual(coord.run(self.root),0)
        self.assertEqual(self.state()['tasks']['A']['attempts'],2)
        self.assertEqual(self.api.merges,2)
        self.assertFalse(self.api.prs[1]['merged'])

    def test_revert_push_crash_resumes_restored_validation_without_duplicate_revert(self):
        self.api.crash_revert=True;self.initialize()
        with self.assertRaises(KeyboardInterrupt):coord.run(self.root)
        target=self.state()['integration_intent']['rollback_head']
        self.assertEqual(self.api.ref(self.state()['integration_branch']),target)
        coord.recover(self.root);(self.root/'cancel.request').unlink()
        self.assertEqual(coord.run(self.root),0)
        self.assertEqual(len(self.state()['remote_git']['rollbacks']),1)
        self.assertEqual(self.state()['remote_git']['rollbacks'][0]['restored'],target)

    def test_failed_ci_during_resume_repairs_under_original_attempt_budget(self):
        self.marker.write_text('disable planned fault');self.api.crash_before_merge=True;self.initialize()
        with self.assertRaises(KeyboardInterrupt):coord.run(self.root)
        self.assertEqual(self.api.merges,0);self.assertTrue(self.state()['integration_intent'])
        coord.recover(self.root);(self.root/'cancel.request').unlink();self.api.ci_fail=True
        self.assertEqual(coord.run(self.root),0)
        self.assertEqual(self.state()['tasks']['A']['attempts'],2);self.assertEqual(self.api.merges,2)

    def test_insufficient_budget_never_merges_unchecked_parent(self):
        self.marker.write_text('disable planned fault');self.plan['remote_git']['rollback_reserve_seconds']=300;self.initialize()
        self.assertEqual(coord.run(self.root),3);self.assertEqual(self.api.merges,0)
        self.assertEqual(self.state()['tasks']['B']['attempts'],0)
        self.assertIn('budget',self.state()['remote_hold'])

    def test_fixed_post_merge_script_is_pinned(self):
        self.initialize();script=Path(self.plan['remote_git']['post_merge_checks'][0]['argv'][2])
        script.write_text('print("fake success")')
        with self.assertRaisesRegex(checks.Rejected,'protected input drift'):coord.load(self.root)

    def test_authorization_identity_and_serial_policy(self):
        for change in ('authorization','parallelism','url'):
            with self.subTest(change=change):
                p=copy.deepcopy(self.plan)
                if change=='authorization':p['remote_git']['authorization']['revert_failed_merge']=False
                elif change=='parallelism':p['limits']['parallelism']=2
                else:p['remote_git']['repository_url']='https://token@github.com/example/test'
                with self.assertRaises(checks.Rejected):coord.validate(p)
        self.assertEqual(identity('git@github.com:example/test.git'),'example/test')

if __name__=='__main__':unittest.main()
