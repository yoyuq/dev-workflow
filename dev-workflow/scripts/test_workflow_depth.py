"""Observable plan coverage, interview gaps and faithful diagram projection."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import check_night_run as checks
import workflow_depth as depth
import workflow_session as session


class DepthTests(unittest.TestCase):
    def setUp(self):
        self.plan = json.loads((Path(__file__).resolve().parents[1]/'assets/project-plan.example.json').read_text(encoding='utf-8'))
        # A synthetic fixture only; never install or start a real project with it.
        self.plan['requirements_alignment'].update(status='confirmed', open_questions=[],
            user_confirmation={'reference': 'unit-test-fixture', 'quote': 'Synthetic test confirmation; not user authorization.'})

    def test_complete_project_has_coverage_without_completion_claims(self):
        result = depth.audit(self.plan)
        self.assertTrue(result['ready']); self.assertFalse(result['warnings'])
        self.assertTrue(all(r['status']=='planned' for r in result['coverage'] if r['disposition']=='implement'))
        for req in result['coverage']:
            for criterion in req['criteria']:
                self.assertTrue(criterion['implementation']); self.assertTrue(criterion['validation'])

    def test_missing_ui_state_and_unanswered_business_decision_are_detected(self):
        self.plan['decisions'] = [d for d in self.plan['decisions'] if d['topic']!='ui_states']
        self.plan['decisions'].append({'topic':'cancellation_window','status':'open',
            'question':'何时还能取消预约？','recommendation':'开课前可取消；需确认业务规则'})
        result = depth.audit(self.plan)
        self.assertFalse(result['ready'])
        self.assertIn('missing decision topic: ui_states',result['errors'])
        self.assertEqual(result['next_questions'][0]['topic'],'cancellation_window')

    def test_lost_behavior_and_validation_cannot_be_hidden_by_delivery(self):
        self.plan['tasks'][5]['criterion_ids']=[]  # cancel implementation dropped
        self.plan['tasks'][9]['criterion_ids']=[]  # cancel UI dropped
        self.plan['tasks'][12]['criterion_ids']=[] # cancel validation dropped
        result=depth.audit(self.plan)
        self.assertFalse(result['ready'])
        self.assertIn('criterion has no implementation: AC-cancel',result['errors'])
        self.assertIn('criterion has no validation node: AC-cancel',result['errors'])

    def test_final_delivery_and_validation_wait_for_relevant_work(self):
        self.plan['tasks'][-1]['depends_on']=['check-create']
        self.plan['tasks'][11]['depends_on']=['contract']
        result=depth.audit(self.plan)
        self.assertFalse(result['ready'])
        self.assertIn('delivery can precede unfinished work: deliver',result['errors'])
        self.assertIn('validation does not wait for all implementation: AC-create',result['errors'])

    def test_coarse_task_is_reported_but_not_split_by_arbitrary_quota(self):
        self.plan['tasks'][4]['criterion_ids'].append('AC-cancel')
        result=depth.audit(self.plan)
        self.assertIn('create',[w['task'] for w in result['warnings']])

    def test_focused_repair_does_not_inherit_project_nodes_or_question_quota(self):
        plan=copy.deepcopy(self.plan)
        plan.update(depth='focused',depth_reason='One local behavior',concerns=[])
        plan['decisions']=[d for d in plan['decisions'] if d['topic'] in {'outcome','scope','acceptance','autonomy'}]
        plan['modules']=[plan['modules'][1]]
        plan['requirements']=[plan['requirements'][0]]
        plan['tasks']=[plan['tasks'][4]];plan['tasks'][0]['depends_on']=[]
        result=depth.audit(plan)
        self.assertTrue(result['ready']);self.assertEqual(result['node_count'],1)

    def test_draft_example_never_becomes_ready_or_completed(self):
        self.plan['requirements_alignment'].update(status='draft')
        self.assertFalse(depth.audit(self.plan)['ready'])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'view';session.initialize(root,depth.view(self.plan))
            observed=session.detail(root)
            self.assertEqual(observed['snapshot']['phase'],'pending')
            self.assertTrue(all(t['status']=='pending' and not t['evidence'] for t in observed['tasks']))

    def test_projection_preserves_all_nodes_edges_and_acceptance_details(self):
        view=depth.view(self.plan)
        self.assertEqual([t['id'] for t in view['tasks']],[t['id'] for t in self.plan['tasks']])
        for source,visible in zip(self.plan['tasks'],view['tasks']):
            self.assertEqual(source['depends_on'],visible['depends_on'])
            self.assertIn(source['output'],visible['goal'])
            for cid in source['criterion_ids']:self.assertIn(cid,visible['goal'])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'view';session.initialize(root,view)
            session.update(root,{'expected_sequence':0,'updates':[{'id':'baseline','status':'running','action':'核对示例任务图'}]})
            before=session.read(root)
            with self.assertRaises(checks.Rejected):
                session.update(root,{'expected_sequence':1,'updates':[{'id':'deliver','status':'completed','evidence':['claim']}]})
            self.assertEqual(session.read(root),before)

    def test_invalid_references_and_cycles_are_rejected(self):
        for field,value in [('module_ids',['unknown']),('criterion_ids',['unknown']),('depends_on',['create'])]:
            plan=copy.deepcopy(self.plan);plan['tasks'][0][field]=value
            with self.subTest(field=field),self.assertRaises(checks.Rejected):depth.audit(plan)

    def test_excluded_work_and_unfounded_existing_claims_are_rejected(self):
        self.plan['requirements'][0]['disposition']='deferred'
        self.assertFalse(depth.audit(self.plan)['ready'])
        self.plan['requirements'][0]['disposition']='existing'
        with self.assertRaises(checks.Rejected):depth.audit(self.plan)

    def test_generation_never_overwrites_spec_or_output(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'plan.json';target=Path(folder)/'plan.md'
            source.write_text(json.dumps(self.plan),encoding='utf-8')
            before=source.read_bytes()
            self.assertEqual(depth.main(['render','--input',str(source),'--output',str(target)]),0)
            rendered=target.read_bytes()
            self.assertEqual(depth.main(['render','--input',str(source),'--output',str(target)]),2)
            self.assertEqual(source.read_bytes(),before);self.assertEqual(target.read_bytes(),rendered)


if __name__=='__main__':unittest.main()
