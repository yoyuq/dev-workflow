"""Observed sizing, immutable acceptance and real-Git repartition transactions."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import check_night_run as checks
import workflow_coordinator as coord
import workflow_memory as memory
import workflow_partition as sizing
import workflow_replan as replan
from workflow_bus import Bus
from test_workflow_coordinator import coverage_for
import test_workflow_git_delivery as feature_cases


def profile(outcome='Verified original behavior', boundary='independent', **changes):
    return {'outcomes': [outcome], 'complexity': 'low', 'risk': 'low', 'risk_reasons': [],
            'estimate_seconds': 60, 'boundary': boundary, 'decision': 'keep',
            'rationale': 'One observable behavior with a fixed original check', 'evidence': ['original check and project source'], **changes}


WORKER = r'''
import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import workflow_message as msg
if '--version' in sys.argv:print('SIMULATED scoped partition test');raise SystemExit(0)
sys.stdin.read()
if sys.argv[2]=='coordinator':
 c=json.loads(Path('context.json').read_text());r=c['requests'][0];parent=c['tasks'][0]
 if sys.argv[3]=='keep':
  action={'type':'partition_keep','task_id':'A','request_id':r['id'],'draft_files':r['payload']['data']['draft_files'],'text':'One atomic shared format requires both files in the same PR, confirmed by the original joint invariant'}
 else:
  p=parent['partition'];children=[]
  for tid,path,check in [('X','a.txt','a'),('Y','b.txt','b')]:
   children.append({'id':tid,'title':'Deliver '+tid,'goal':'Deliver original behavior '+tid,'allowed_scope':[path],
    'checks':[check],'depends_on':[] if tid=='X' else ['X'], 'partition':{**p,'outcomes':['Original '+tid]}})
  proposal={'id':'observed-split','expected_revision':c['revision'],'reason':'Observed draft contains two independently verified outputs',
    'add':[],'dependencies':{},'split':[{'parent':'A','reason':'Separate independent outputs and preserve original joint acceptance','children':children}]}
  action={'type':'replan','request_id':r['id'],'proposal':proposal}
 Path('decision.json').write_text(json.dumps({'actions':[action]}));raise SystemExit(0)
box=msg.read('.',True);tid=box['task_id'];attempt=[m for m in box['messages'] if m['kind']=='assignment'][-1]['payload']['attempt']
if tid=='A' and attempt==1:
 Path('a.txt').write_text('OK');Path('b.txt').write_text('OK')
elif tid=='X':Path('a.txt').write_text('OK')
elif tid=='Y':
 assert Path('a.txt').read_text()=='OK';Path('b.txt').write_text('OK')
else:assert Path('a.txt').read_text()==Path('b.txt').read_text()=='OK'
msg.read('.',True)
'''


class PartitionTests(unittest.TestCase):
    def setUp(self):
        # Reuse only the isolated real-Git/fake-provider setup, not its tests.
        self.case = feature_cases.FeaturePRTests('test_authorization_identity_and_serial_policy')
        self.case.setUp();c = self.case
        c.marker.write_text('no planned fault')
        c.plan['tasks'] = c.plan['tasks'][:1]
        task = c.plan['tasks'][0];task['allowed_scope'] = ['a.txt', 'b.txt', 'joint.txt']
        checker = c.base/'functional-check.py'
        checker.write_text("from pathlib import Path\nimport sys\nr=Path(sys.argv[1]);mode=sys.argv[2]\nif mode in ('a','b'):assert (r/(mode+'.txt')).read_text()=='OK'\nelse:assert (r/'joint.txt').exists()\n")
        task['verification'] = [{'id':t, 'argv':[sys.executable,'-B',str(checker),'{repo}',t], 'timeout_seconds':5} for t in ('a','b')]
        task['partition'] = profile(outcomes=['Output A','Output B'])
        task['produces'] = ['original-complete']
        c.plan['partitioning'] = {'thresholds': {'max_changed_files':1, 'max_changed_lines':1000}}
        c.plan['replanning'] = {'max_revisions':2, 'max_added_tasks':4, 'max_total_attempts':6, 'preparation_checks':[], 'allow_feature_split':True}
        worker = c.base/'partition-worker.py';worker.write_text(WORKER, encoding='utf-8')
        c.configs['worker']['harness']['args'] = ['-B',str(worker),str(Path(coord.__file__).parent),'worker','split']
        c.configs['worker']['protected_files'].append(str(worker))
        c.configs['coordinator'] = copy.deepcopy(c.configs['worker'])
        c.configs['coordinator']['harness']['args'][-2] = 'coordinator'
        coverage_for(c.plan)

    def tearDown(self): self.case.tearDown()

    def test_observed_split_delivers_children_then_full_parent_without_empty_pr(self):
        c=self.case;c.initialize();pins=checks.digest(c.root/'pins.json');original=copy.deepcopy(c.plan)
        self.assertEqual(coord.run(c.root),0)
        state=c.state();plan,_,_=coord.load(c.root)
        self.assertEqual(state['status'],'completed');self.assertEqual(state['tasks']['A']['attempts'],2)
        self.assertEqual(state['tasks']['A']['completion_kind'],'validated_children')
        self.assertEqual(state['tasks']['A'].get('feature_prs',[]),[])
        self.assertEqual(c.api.merges,2);self.assertEqual(len(state['remote_git']['prs']),2)
        self.assertEqual(plan['requirements'],original['requirements']);self.assertEqual(plan['limits'],original['limits'])
        self.assertEqual(plan['tasks'][0]['verification'],original['tasks'][0]['verification'])
        self.assertEqual(checks.digest(c.root/'pins.json'),pins)
        self.assertEqual(c.api.ref('main'),original['base_commit'])
        self.assertEqual(state['milestones']['original-complete']['owner'],'A')
        self.assertTrue(state['tasks']['X']['split_origin']['workspace'])
        with Bus(c.root).connection() as db:
            events=[dict(r) for r in db.execute('SELECT seq,kind,data FROM events ORDER BY seq')]
        integrated=[json.loads(e['data'])['task'] for e in events if e['kind']=='task_integrated']
        self.assertEqual(integrated,['X','Y','A'])
        self.assertTrue(any(e['kind']=='partition_requested' for e in events))
        self.assertEqual(replan.materialize(original,state),plan)

    def test_atomic_keep_reuses_exact_draft_and_attempt_with_full_checks(self):
        c=self.case;c.configs['coordinator']['harness']['args'][-1]='keep'
        c.initialize();self.assertEqual(coord.run(c.root),0);state=c.state()
        self.assertEqual(state['tasks']['A']['attempts'],1);self.assertEqual(c.api.merges,1)
        self.assertEqual(state.get('plan_revisions',[]),[])
        self.assertEqual(state['tasks']['A']['partition_exception']['attempt'],1)

    def test_interrupted_child_merge_replays_split_without_reset_or_duplicate_pr(self):
        c=self.case;c.api.crash=True;c.initialize();pins=checks.digest(c.root/'pins.json')
        with self.assertRaises(KeyboardInterrupt):coord.run(c.root)
        s=c.state();self.assertEqual(len(s['plan_revisions']),1);self.assertEqual(s['tasks']['A']['attempts'],1)
        self.assertEqual(c.api.merges,1)
        elapsed=s['elapsed_seconds'];coord.recover(c.root);(c.root/'cancel.request').unlink()
        self.assertEqual(coord.run(c.root),0);s=c.state()
        self.assertEqual(c.api.merges,2);self.assertEqual(s['tasks']['X']['attempts'],1)
        self.assertEqual(s['tasks']['A']['attempts'],2);self.assertGreaterEqual(s['elapsed_seconds'],elapsed)
        self.assertEqual(len(s['plan_revisions']),1);self.assertEqual(checks.digest(c.root/'pins.json'),pins)

    def test_local_child_passes_cannot_replace_original_joint_acceptance(self):
        c=self.case;t=c.plan['tasks'][0];checker=t['verification'][0]['argv'][2]
        t['verification'].append({'id':'joint','argv':[sys.executable,'-B',checker,'{repo}','joint'],'timeout_seconds':5})
        coverage_for(c.plan);c.initialize();self.assertEqual(coord.run(c.root),3);state=c.state()
        self.assertEqual(state['tasks']['X']['status'],'integrated');self.assertEqual(state['tasks']['Y']['status'],'integrated')
        self.assertNotEqual(state['tasks']['A']['status'],'integrated')
        self.assertNotIn('original-complete',state['milestones']);self.assertNotIn('parent_pr',state['remote_git'])
        self.assertEqual(c.api.ref('main'),c.plan['base_commit'])

    def test_split_scope_checks_budgets_and_accepted_parent_fail_closed(self):
        c=self.case;c.initialize();bus=Bus(c.root);base=c.plan;state=bus.state()
        child=lambda tid,path,check:{'id':tid,'goal':'Original result','allowed_scope':[path],'checks':[check],'depends_on':[], 'partition':profile()}
        p={'id':'split','expected_revision':0,'reason':'Independent verified outputs','add':[],'dependencies':{},
           'split':[{'parent':'A','reason':'Independently reviewable','children':[child('X','a.txt','a'),child('Y','b.txt','b')]}]}
        for mutate in (lambda v:v['split'][0]['children'][0].update(allowed_scope=['outside.txt']),
                       lambda v:v['split'][0]['children'][0].update(checks=['new-weaker-check']),
                       lambda v:v['split'][0]['children'][0].update(depends_on=['A']),
                       lambda v:v.update(goal='expanded objective')):
            proposal=copy.deepcopy(p);mutate(proposal)
            before=copy.deepcopy(state)
            with self.assertRaises(checks.Rejected):replan.activate(base,base,state,bus,proposal,coord.validate)
            self.assertEqual(state,before);self.assertEqual(bus.state(),before)
        for reason in ('accepted','budget','retry_gate','human'):
            current=copy.deepcopy(state)
            if reason=='accepted':current['tasks']['A']['status']='integrated'
            elif reason=='budget':current['tasks']['A']['attempts']=base['limits']['max_attempts']
            elif reason=='retry_gate':current['tasks']['A']['retry_gate']={'signature':'unresolved'}
            else:current['tasks']['A']['needs_human']='Product decision still missing'
            with self.assertRaises(checks.Rejected):replan.activate(base,base,current,bus,p,coord.validate)

    def test_complexity_dependency_and_atomic_risk_assessment(self):
        c=self.case;t=copy.deepcopy(c.plan['tasks'][0])
        t['partition']=profile(complexity='high',risk='high',risk_reasons=['Destructive migration compatibility'],
            boundary='atomic',estimate_seconds=1900)
        t['depends_on']=['a','b','c','d'];r=sizing.assess(c.plan,t)
        self.assertEqual(set(r['signals']),{'high_complexity','high_risk','estimated_duration','dependency_fan_in'})
        self.assertEqual(r['recommendation'],'inspect_atomic_boundary')
        t['partition']['boundary']='independent'
        self.assertEqual(sizing.assess(c.plan,t)['recommendation'],'consider_split')

    def test_child_prerequisites_cannot_be_removed_and_parent_human_gate_blocks_dispatch(self):
        c=self.case;plan=copy.deepcopy(c.plan)
        plan['tasks'][0]['depends_on']=['ORIGINAL-DEPENDENCY']
        child=lambda tid,path,check:{'id':tid,'goal':'Original result','allowed_scope':[path], 'checks':[check], 'depends_on':[], 'partition':profile()}
        proposal={'id':'dependency-split','expected_revision':0,'reason':'Separate original outputs', 'add':[],
            'dependencies':{'X':[]},'split':[{'parent':'A','reason':'Independent outputs', 'children':[child('X','a.txt','a'),child('Y','b.txt','b')]}]}
        candidate=replan.build(plan,proposal);tasks={t['id']:t for t in candidate['tasks']}
        self.assertIn('ORIGINAL-DEPENDENCY',tasks['X']['depends_on'])
        state={'tasks':{'A':{'status':'pending','needs_human':'Unresolved actual product choice'},'X':{'status':'pending'},
                        'ORIGINAL-DEPENDENCY':{'status':'integrated'}}, 'milestones':{}}
        self.assertFalse(coord.ready('X',tasks,state))


if __name__=='__main__':unittest.main()
