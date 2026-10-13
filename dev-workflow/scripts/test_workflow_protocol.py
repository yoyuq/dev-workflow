"""Protocol behavior with real file-only subprocesses; these are simulated agents."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import check_night_run as checks
import workflow_coordinator as coord
import workflow_memory as memory
import workflow_protocol as protocol
from workflow_bus import Bus
from test_workflow_coordinator import fixture


FILE_WORKER = r'''
import json, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('file-only-simulated-worker'); raise SystemExit(0)
sys.stdin.read()
mode = sys.argv[2]
box = Path('.workflow-mailbox')
inbox = json.loads((box/'inbox.json').read_text(encoding='utf-8'))
tid = inbox['task_id']
attempt = [m for m in inbox['messages'] if m['kind']=='assignment'][-1]['payload']['attempt']
def post(mid, kind, **data):
    value = dict(id=mid, type=kind, to='coordinator', **data)
    temp = box/'outbox'/(mid+'.tmp')
    temp.write_text(json.dumps(value), encoding='utf-8')
    temp.replace(temp.with_suffix('.json'))
if mode != 'no_ack':
    post('ack', 'ack', ids=[m['id'] for m in inbox['messages']])
if mode != 'no_probe':
    post('cap', 'capabilities', data=dict(transport='files', local_checks='unavailable', evidence='No worker-local interpreter permission; file tools work.'))
if mode in ('no_ack', 'no_probe'):
    time.sleep(5); raise SystemExit(0)
if mode == 'wrong_recipient':
    post('bad', 'progress', text='invalid later transport')
    value = json.loads((box/'outbox/bad.json').read_text())
    value['type']='ready_for_verification'; value['to']=tid
    (box/'outbox/bad.json').write_text(json.dumps(value))
else:
    Path('a.txt').write_text('WRONG' if mode == 'bad_code' or mode == 'fix_third' and attempt < 3 else 'OK')
if mode in ('blocked', 'contradictory'):
    post('block', 'blocked', text='Missing implementation requirement, cannot decide it.')
if mode not in ('blocked', 'no_handoff'):
    post('ready', 'ready_for_verification', data=dict(implementation='ready', local_checks='unavailable', evidence='a.txt is implemented; controller must run its original check.'))
if mode == 'nonzero': raise SystemExit(1)
if mode == 'late_failure':
    Path('a.txt').write_text('WRONG')
'''


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, mode='simple'):
        plan, _ = fixture(self.base)
        (self.base / 'worker.py').write_text(FILE_WORKER, encoding='utf-8')
        plan['run_id'] = 'protocol'
        plan['limits'].update(max_attempts=4, max_run_seconds=70)
        config = memory.read_json(self.base / 'runtime.json')['worker']
        config['harness']['args'][-1] = mode
        config['worker_preflight_seconds'] = 1
        memory.atomic_json(self.base / 'new-plan.json', plan)
        memory.atomic_json(self.base / 'new-runtime.json', {'worker': config})
        root = self.base / 'protocol-run'
        coord.initialize(self.base / 'new-plan.json', self.base / 'new-runtime.json', root)
        return plan, root

    def test_file_only_worker_delegates_checks_and_completes(self):
        plan, root = self.prepare()
        self.assertEqual(coord.run(root), 0)
        bus = Bus(root)
        entry = bus.state()['tasks']['A']
        self.assertTrue(entry['worker_preflight']['observed']['assignment_ack'])
        self.assertEqual(entry['worker_preflight']['reported']['local_checks'], 'unavailable')
        self.assertEqual(entry['verification_handoff']['reported']['local_checks'], 'unavailable')
        self.assertTrue(all(r['result'] == 'passed' for r in bus.state()['final_checks']))
        self.assertEqual(coord.git(Path(plan['repository']), 'rev-parse', 'main'), plan['base_commit'])
        self.assertEqual(entry['attempts'], 1)

    def test_claimed_readiness_does_not_bypass_actual_failed_check(self):
        plan, root = self.prepare('bad_code')
        self.assertEqual(coord.run(root), 3)
        state = Bus(root).state()
        entry = state['tasks']['A']
        self.assertEqual(state['integration_head'], plan['base_commit'])
        self.assertEqual(entry['attempts'], 2)  # Remaining budget is not blind retry permission.
        self.assertEqual(entry['failure_diagnostics'][-1]['category'], 'verification')
        self.assertTrue(entry['retry_gate'])
        self.assertTrue((root / 'workspaces/A-a2/a.txt').exists())

    def test_changes_after_ready_are_checked_at_process_exit(self):
        plan, root = self.prepare('late_failure')
        self.assertEqual(coord.run(root), 3)
        self.assertEqual(Bus(root).state()['integration_head'], plan['base_commit'])

    def test_actual_blocker_wins_over_ready_signal(self):
        plan, root = self.prepare('contradictory')
        self.assertEqual(coord.run(root), 3)
        state = Bus(root).state()
        self.assertEqual(state['tasks']['A']['status'], 'blocked')
        self.assertEqual(state['tasks']['A']['attempts'], 1)
        self.assertNotIn('last_checks', state['tasks']['A'])
        self.assertEqual(state['integration_head'], plan['base_commit'])

    def test_handoff_cannot_accept_unsuccessful_process(self):
        plan, root = self.prepare('nonzero')
        self.assertEqual(coord.run(root), 3)
        state = Bus(root).state()
        self.assertEqual(state['integration_head'], plan['base_commit'])
        self.assertNotIn('last_checks', state['tasks']['A'])

    def test_no_probe_stops_early_without_business_changes(self):
        plan, root = self.prepare('no_probe')
        self.assertEqual(coord.run(root), 3)
        entry = Bus(root).state()['tasks']['A']
        self.assertEqual(entry['attempts'], 2)
        self.assertEqual(entry['failure_diagnostics'][-1]['category'], 'protocol')
        self.assertFalse((Path(entry['workspace']) / 'a.txt').exists())
        self.assertLess(Bus(root).state()['elapsed_seconds'], 20)

    def test_capability_claim_without_assignment_ack_is_not_probe_success(self):
        _, root = self.prepare('no_ack')
        self.assertEqual(coord.run(root), 3)
        self.assertNotIn('confirmed_at', Bus(root).state()['tasks']['A']['worker_preflight'])

    def test_implementation_requires_explicit_handoff(self):
        _, root = self.prepare('no_handoff')
        self.assertEqual(coord.run(root), 3)
        entry = Bus(root).state()['tasks']['A']
        self.assertEqual(entry['attempts'], 2)
        self.assertEqual(entry['failure_diagnostics'][-1]['symptom'], 'verification-handoff')
        self.assertNotIn('commit', entry)

    def test_retry_requires_current_evidence_and_changed_strategy_without_budget_reset(self):
        plan, root = self.prepare('fix_third')
        self.assertEqual(coord.run(root), 3)
        bus = Bus(root)
        state = bus.state()
        before = copy.deepcopy(state)
        request = bus.unresolved()[0]
        action = {'type': 'retry', 'task_id': 'A', 'request_id': request['id'], 'text': 'Try again'}
        with self.assertRaises(checks.Rejected):
            coord.apply_decisions(plan, coord.validate(plan), state, bus, {'actions': [action]}, {request['id']})
        self.assertEqual(state, before)
        gate = state['tasks']['A']['retry_gate']
        change = {'failure_signature': gate['signature'], 'case_id': gate['case_id'],
                  'diagnosis': 'Observed exact content mismatch in external check', 'strategy': 'Use the explicit OK contract instead of WRONG'}
        action['changed_approach'] = change
        coord.apply_decisions(plan, coord.validate(plan), state, bus, {'actions': [action]}, {request['id']})
        self.assertEqual(state['elapsed_seconds'], before['elapsed_seconds'])
        self.assertEqual(state['tasks']['A']['attempts'], 2)
        self.assertEqual(coord.run(root), 0)
        self.assertEqual(bus.state()['tasks']['A']['attempts'], 3)
        trial = copy.deepcopy(bus.state()['tasks']['A'])
        trial['retry_gate'] = gate
        with self.assertRaises(checks.Rejected):
            protocol.validate_retry(trial, action)

    def test_special_protocol_schema_and_destination_are_validated(self):
        for kind, to, data in [('capabilities', 'A', {}), ('capabilities', 'coordinator', {'transport': 'magic'}),
                               ('ready_for_verification', 'coordinator', {'implementation': 'complete'})]:
            with self.subTest(kind=kind, to=to, data=data), self.assertRaises(checks.Rejected):
                protocol.validate_message(kind, to, data)


if __name__ == '__main__':
    unittest.main()
