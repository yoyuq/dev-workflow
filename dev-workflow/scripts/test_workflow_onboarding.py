"""Onboarding outcomes with real Git/files/subprocesses; command probes are simulated."""
import argparse
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import check_night_run as checks
import workflow
import workflow_coordinator as coord
import workflow_memory as memory
import workflow_onboarding as onboarding
import workflow_runtime as runtime

PROBE = '''import json,sys,time
from pathlib import Path
sys.stdin.read()
mode=sys.argv[1]
if mode=='timeout': time.sleep(15)
elif mode=='error':
    print(json.dumps({'type':'error','message':'The requested model is not supported for this account'}),flush=True)
    raise SystemExit(1)
elif mode!='empty':
    nonce=Path('challenge.txt').read_text() if mode=='pass' else 'wrong'
    Path('probe.json').write_text(json.dumps({'nonce':nonce}))
'''


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.repo = self.base / '我的 project'
        self.repo.mkdir()
        coord.git(self.repo, 'init', '--template=', '--initial-branch=main')
        coord.git(self.repo, 'config', 'user.name', 'Onboarding Test')
        coord.git(self.repo, 'config', 'user.email', 'test@example.invalid')
        (self.repo / 'app.py').write_text('original = True\n')
        coord.git(self.repo, 'add', 'app.py')
        coord.git(self.repo, 'commit', '-m', 'baseline')
        self.script = self.base / 'probe.py'
        self.script.write_text(PROBE)

    def config(self, mode='pass'):
        return {'format_version': 1, 'harness': {'kind': 'command', 'executable': sys.executable, 'args': [str(self.script), mode]},
                'limits': {'attempt_seconds': 10, 'no_progress_seconds': 10, 'max_output_bytes': 10000}}

    def args(self, configs=None, **changes):
        path = self.base / 'runtime.json'
        memory.atomic_json(path, configs or {'worker': self.config()})
        fields = dict(command='doctor', project=self.repo, runtime=path, harness=None, executable=None, model=None,
                      output=self.base / 'probe-output', live=False, seconds=5, mode='quick')
        fields.update(changes)
        return argparse.Namespace(**fields)

    def test_setup_generates_complete_full_runtime_without_touching_project_or_auth(self):
        args = self.args(command='setup', runtime=None, harness='codex', executable=sys.executable, model='user-selected', mode='full')
        before = coord.git(self.repo, 'status', '--porcelain=v1')
        with patch.object(runtime, 'run_worker', side_effect=AssertionError('No implicit model call')):
            result = onboarding.setup(args)
        config = checks.read_json(Path(result['runtime']))
        self.assertEqual(set(config), {'worker','reviewer','coordinator'})
        self.assertEqual(config['reviewer']['harness']['model'], 'user-selected')
        for role in config.values(): runtime.validate_config(role)
        self.assertFalse((args.output / 'plan.json').exists())
        self.assertTrue((args.output / 'START.md').is_file())
        self.assertEqual(coord.git(self.repo, 'status', '--porcelain=v1'), before)

    def test_existing_setup_is_preserved(self):
        args = self.args(command='setup', runtime=None, harness='codex', executable=sys.executable)
        onboarding.setup(args)
        target = args.output / 'runtime.json'
        target.write_text('user-edited')
        with self.assertRaises(checks.Rejected): onboarding.setup(args)
        self.assertEqual(target.read_text(), 'user-edited')

    def test_output_inside_project_or_skill_is_refused(self):
        for dest in (self.repo / 'setup', onboarding.ROOT / 'setup'):
            with self.subTest(dest=dest), self.assertRaises((checks.Rejected, ValueError)):
                onboarding.fresh_directory(dest, self.repo)
            self.assertFalse(dest.exists())

    def test_offline_doctor_does_not_call_models_or_configured_project_commands(self):
        config = self.config()
        config['preflight_checks'] = [{'id':'project-side-effect','argv':[sys.executable,'-c','raise AssertionError("must not run")'],'timeout_seconds':1}]
        args = self.args({'worker':config})
        with patch.object(runtime, 'run_worker', side_effect=AssertionError('unexpected model call')):
            report = onboarding.doctor(args)
        self.assertTrue(report['offline_ok'])
        self.assertEqual(report['live_status'], 'not_requested')
        self.assertFalse(report['ready_for_project_execution'])
        self.assertFalse(args.output.exists())

    def test_live_probe_needs_actual_file_roundtrip_and_deduplicates_equal_roles(self):
        config = self.config()
        args = self.args({'worker':config, 'reviewer':copy.deepcopy(config)}, live=True)
        report = onboarding.doctor(args)
        self.assertEqual(report['live_status'], 'passed')
        self.assertEqual(report['roles']['reviewer']['live']['shared_with'], 'worker')
        self.assertFalse((args.output / 'reviewer').exists())
        self.assertFalse(report['ready_for_project_execution'])
        self.assertEqual(checks.read_json(args.output / 'doctor.json'), report)

    def test_false_success_and_wrong_nonce_are_not_accepted(self):
        for mode in ('empty','wrong'):
            with self.subTest(mode=mode):
                result = onboarding.live_probe(self.config(mode), self.base / mode, 5)
                self.assertEqual(result['status'], 'failed')
                self.assertFalse(result['model_request_and_file_roundtrip'])

    def test_unavailable_reviewer_prevents_live_pass_even_if_worker_passes(self):
        args = self.args({'worker':self.config(), 'reviewer':self.config('error')}, live=True)
        result = onboarding.doctor(args)
        self.assertEqual(result['live_status'], 'failed')
        self.assertEqual(result['roles']['worker']['live']['status'], 'passed')
        self.assertEqual(result['roles']['reviewer']['live']['failure'], 'model_unavailable')

    def test_probe_timeout_is_bounded_and_leaves_process_evidence(self):
        result = onboarding.live_probe(self.config('timeout'), self.base / 'slow', 1)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['failure'], 'time_limit')
        self.assertLess(result['process']['duration_seconds'], 6)
        self.assertTrue((self.base / 'slow/process/process.json').exists())

    def test_missing_cli_has_actionable_failure_without_starting_probe(self):
        args = self.args(runtime=None, harness='codex', executable=str(self.base / 'absent.exe'), live=True)
        result = onboarding.doctor(args)
        self.assertFalse(result['offline_ok'])
        self.assertTrue(result['issues'])
        self.assertIn('next_step',result)
        self.assertFalse(args.output.exists())

    def test_wrong_native_executable_is_not_reported_as_installed_cli(self):
        args = self.args(runtime=None, harness='codex', executable=sys.executable)
        result = onboarding.doctor(args)
        self.assertFalse(result['offline_ok'])
        self.assertEqual(result['roles']['worker']['offline'], 'failed')

    def test_cli_without_runtime_returns_helpful_nonzero_not_traceback(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = workflow.main(['doctor','--project',str(self.repo),'--json'])
        self.assertEqual(code,2)
        self.assertTrue(json.loads(out.getvalue())['issues'])

    def test_dirty_project_reports_caution_without_changing_files(self):
        (self.repo/'app.py').write_text('uncommitted = True\n')
        report = onboarding.doctor(self.args())
        self.assertTrue(report['project']['dirty'])
        self.assertEqual((self.repo/'app.py').read_text(),'uncommitted = True\n')

    def test_self_test_uses_controller_and_keeps_main_unchanged_without_model(self):
        output = self.base / 'demo'
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = workflow.main(['self-test','--output',str(output),'--json'])
        self.assertEqual(code,0,out.getvalue())
        result = json.loads(out.getvalue())
        self.assertEqual(result['worker'],'explicitly_simulated')
        self.assertFalse(result['real_model_verified'])
        self.assertTrue(result['source_main_unchanged'])
        delivery = checks.read_json(Path(result['report']))
        self.assertEqual(delivery['status'],'completed')
        self.assertTrue(all(c['result']=='passed' for c in delivery['final_checks']))
        self.assertEqual((output/'repository/app.py').read_text(),'def sum_values(values):\n    return len(values)\n')


if __name__ == '__main__': unittest.main()
