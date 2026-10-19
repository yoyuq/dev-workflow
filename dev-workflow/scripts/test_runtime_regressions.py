"""Regressions from the failed Linux endurance cases; no model quality claims."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import venv

import check_night_run as checks
import workflow_runtime as runtime
import workflow_coordinator as coord
import workflow_review as review
from workflow_bus import Bus
from test_workflow_coordinator import fixture
from test_workflow_replan import repin_fixture


def config(executable=sys.executable):
    return {'format_version': 1, 'harness': {'kind': 'command', 'executable': executable},
            'limits': {'attempt_seconds': 20, 'no_progress_seconds': 10, 'max_output_bytes': 100000}}


class RuntimeRegressionTests(unittest.TestCase):
    def test_review_probe_contract_preserves_inputs_and_uses_reproducible_scripts(self):
        prompt = review.reviewer_prompt()
        self.assertIn('supplementary probe scripts under probes/', prompt)
        self.assertIn('Do not change existing inputs or source', prompt)
        self.assertIn('do not put multiline code in Bash', prompt)
        cfg = config(); cfg['harness']['kind'] = 'claude'
        self.assertIn('no literal newlines even inside quoted arguments', runtime.command_guidance(cfg, Path.cwd()))

    def test_real_venv_imports_through_exact_controller_and_preflight(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve()
            environment = root / 'venv'
            venv.EnvBuilder(with_pip=False, symlinks=os.name != 'nt').create(environment)
            python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            if os.name != 'nt':
                self.assertTrue(python.is_symlink(), 'Linux regression must exercise the original symlink failure')
            site = Path(subprocess.check_output([str(python), '-c',
                "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip())
            (site / 'venv_only_sentinel.py').write_text('VALUE = 73\n', encoding='utf-8')
            argv = [str(python), '-c', 'import sys,venv_only_sentinel; '
                    'assert venv_only_sentinel.VALUE == 73; assert sys.prefix == ' + repr(str(environment))]
            record = checks.run_check('DB', {'id': 'venv', 'argv': argv, 'cwd': '.',
                                            'timeout_seconds': 15}, root, root, 20)
            self.assertEqual(record['result'], 'passed', (root / record['log']).read_text())
            cfg = config(str(python))
            cfg['preflight_checks'] = [{'id': 'venv', 'argv': argv, 'timeout_seconds': 15}]
            self.assertEqual(runtime.preflight(cfg, root)['probes'][0]['exit_code'], 0)
            with patch.dict(os.environ, {'PATH': str(python.parent)}):
                self.assertEqual(Path(runtime.resolve_program(python.name)), python)

    def test_budget_rejects_invalid_and_known_insufficient_limits(self):
        for value in (False, 0, -1, float('nan'), float('inf'), '2'):
            for field in ('max_budget_usd', 'budget_reference_usd'):
                cfg = config(); cfg['harness'][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(checks.Rejected):
                    runtime.validate_config(cfg)
        cfg = config(); cfg['harness'].update(max_budget_usd=.5, budget_reference_usd=.5379)
        with self.assertRaisesRegex(checks.Rejected, 'measured budget reference'):
            runtime.validate_config(cfg)
        cfg['harness']['max_budget_usd'] = 2
        runtime.validate_config(cfg)

    def test_budget_terminal_preserves_cost_and_blocks_recovery(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name).resolve()
            plan, root = fixture(base)
            script = base / 'budget-review.py'
            script.write_text('import json,sys\n'
                'if "--version" in sys.argv: print("budget-fixture"); raise SystemExit(0)\n'
                'sys.stdin.read()\n'
                'print(json.dumps({"type":"result","subtype":"error_max_budget_usd",'
                '"is_error":True,"total_cost_usd":.5379}),flush=True)\nraise SystemExit(1)\n', encoding='utf-8')
            configs = checks.read_json(root / 'runtime.json')
            configs['reviewer'] = copy.deepcopy(configs['worker'])
            configs['reviewer']['harness']['args'] = [str(script)]
            configs['reviewer']['protected_files'].append(str(script))
            plan['review'] = {'max_rounds': 4, 'max_recovery_attempts': 2}
            repin_fixture(root, plan, configs)
            self.assertEqual(coord.run(root), 3)
            state = Bus(root).state()
            self.assertEqual(state['review_rounds'], 1)
            self.assertFalse(state.get('review_recoveries'))
            process = state['review_process_failures'][0]['process']
            self.assertEqual(process['environment_failure'], 'budget_exhausted')
            self.assertEqual(process['reported_cost_usd'], .5379)
            self.assertNotEqual(state['status'], 'completed')

    def test_review_gets_exact_source_directory_without_broad_bash(self):
        from test_workflow_review import REVIEWER
        with tempfile.TemporaryDirectory() as name:
            base = Path(name).resolve(); plan, root = fixture(base)
            script = base / 'reviewer.py'; script.write_text(REVIEWER, encoding='utf-8')
            configs = checks.read_json(root / 'runtime.json')
            configs['reviewer'] = copy.deepcopy(configs['worker'])
            configs['reviewer']['harness']['args'] = [str(script), 'approve']
            configs['reviewer']['protected_files'].append(str(script))
            repin_fixture(root, plan, configs)
            with patch.object(coord, 'supervised_worker', wraps=coord.supervised_worker) as worker:
                self.assertEqual(coord.run(root), 0)
            reviews = [c for c in worker.call_args_list if c.args[1].startswith('review-')]
            self.assertEqual(len(reviews), 2)
            for call in reviews:
                cfg, folder = copy.deepcopy(call.args[2]), call.args[3]
                workspace = checks.read_json(folder / 'context.json')['workspace']
                self.assertIn(workspace, cfg['additional_directories'])
                cfg['harness'] = {'kind': 'claude', 'executable': sys.executable,
                                  'allowed_commands': ['Bash(python3 *)']}
                argv = runtime.command(cfg, folder, base)
                self.assertIn(workspace, argv)
                self.assertEqual(runtime.allowed_commands(cfg), ['Bash(python3 *)'])
                self.assertNotIn('Bash', argv[argv.index('--allowedTools') + 1:])
                runtime.validate_config(cfg)


if __name__ == '__main__':
    unittest.main()
