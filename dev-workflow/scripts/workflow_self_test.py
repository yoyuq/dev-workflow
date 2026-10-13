"""An offline onboarding demonstration. The worker is explicitly simulated."""
from pathlib import Path
import sys

import check_night_run as checks
import workflow_coordinator as coord
import workflow_memory as memory
import workflow_portable as portable
import workflow_onboarding as onboarding

WORKER = '''import json, sys
from pathlib import Path
if '--version' in sys.argv:
    print('workflow-offline-simulated-worker'); raise SystemExit(0)
sys.stdin.read()
box=Path('.workflow-mailbox')
inbox=json.loads((box/'inbox.json').read_text(encoding='utf-8'))
def post(mid, kind, **data):
    path=box/'outbox'/(mid+'.tmp')
    path.write_text(json.dumps(dict(id=mid,type=kind,to='coordinator',**data)),encoding='utf-8')
    path.replace(path.with_suffix('.json'))
post('ack','ack',ids=[m['id'] for m in inbox['messages']])
post('cap','capabilities',data=dict(transport='files',local_checks='not_probed',evidence='Offline simulated worker; file round trip.'))
Path('app.py').write_text('def sum_values(values):\\n    return sum(values)\\n',encoding='utf-8')
post('ready','ready_for_verification',data=dict(implementation='ready',local_checks='not_run',evidence='Simulated implementation; controller must check actual behavior.'))
'''
CHECK = '''import importlib.util, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('sample',Path(sys.argv[1])/'app.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
for values, expected in [([],0),([2,3],5),([-2,2],0),([1.25,2.75],4)]:
    assert module.sum_values(values)==expected, (values,expected)
print('PASS: four external behavior checks; offline simulated worker')
'''


def execute(output):
    import workflow
    portable.check_package()
    dest = onboarding.fresh_directory(output)
    skill = dest / 'skill'
    portable.copy_exact(onboarding.ROOT, skill)
    repo = dest / 'repository'
    repo.mkdir()
    coord.git(repo, 'init', '--template=', '--initial-branch=main')
    coord.git(repo, 'config', 'user.name', 'Workflow Offline Demo')
    coord.git(repo, 'config', 'user.email', 'offline-demo@example.invalid')
    (repo / 'app.py').write_text('def sum_values(values):\n    return len(values)\n', encoding='utf-8')
    coord.git(repo, 'add', 'app.py')
    coord.git(repo, 'commit', '-m', 'Synthetic onboarding baseline')
    base = coord.git(repo, 'rev-parse', 'HEAD')
    (dest / 'worker.py').write_text(WORKER, encoding='utf-8')
    (dest / 'check.py').write_text(CHECK, encoding='utf-8')
    goal = 'Offline synthetic demonstration: repair sum_values and verify a local branch commit.'
    (dest / 'plan.md').write_text(goal + '\nModule SUM: edit app.py, then four external checks. No model calls or remote writes. All requirements and worker behavior are simulated.', encoding='utf-8')
    task = {'id': 'SUM', 'module': 'sample', 'goal': goal, 'allowed_scope': ['app.py'],
            'depends_on': [], 'requires': [], 'produces': [], 'contracts': [], 'resources': [],
            'verification': [{'id': 'behavior', 'argv': [sys.executable, '-B', str(dest / 'check.py'), '{repo}'], 'timeout_seconds': 10}]}
    alignment = {'method': 'grill-me', 'mode': 'reuse', 'status': 'confirmed', 'goal': goal,
                 'reused_from': 'Built-in explicitly simulated offline self-test; not real product authorization',
                 'scope': 'Synthetic repository app.py only', 'acceptance': 'Four independent assertions and a local branch commit',
                 'autonomy': 'Offline synthetic demonstration, no actual model or remote operations',
                 'user_confirmation': {'reference': 'Explicit self-test invocation; synthetic scenario only',
                                       'quote': 'workflow.py self-test --output ' + str(dest)}, 'open_questions': []}
    plan = {'format_version': 2, 'kind': 'coordinated', 'run_id': 'offline-demo', 'goal': goal,
            'requirements_alignment': alignment, 'authorization_ref': 'Explicit offline self-test, simulated requirements only',
            'repository': str(repo), 'base_commit': base, 'skill_root': str(skill), 'execution_plan': str(dest / 'plan.md'),
            'delivery': 'branch_commit_and_report', 'limits': {'parallelism': 1, 'max_attempts': 1, 'max_coordinator_rounds': 1, 'max_run_seconds': 60},
            'contracts': {}, 'tasks': [task], 'integration_checks': [], 'requirements': [
                {'id': 'R-SUM', 'text': goal, 'task_ids': ['SUM'], 'acceptance': [
                    {'id': 'AC-SUM', 'text': 'External behavior checks', 'type': 'automated', 'checks': [{'task_id': 'SUM', 'check_id': 'behavior'}]}]}]}
    config = {'format_version': 1, 'harness': {'kind': 'command', 'executable': sys.executable, 'args': ['-B', str(dest / 'worker.py')]},
              'limits': {'attempt_seconds': 20, 'no_progress_seconds': 15, 'max_output_bytes': 100000}, 'worker_preflight_seconds': 10}
    memory.atomic_json(dest / 'plan.json', plan)
    memory.atomic_json(dest / 'runtime.json', {'worker': config})
    workflow.initialize(dest / 'plan.json', dest / 'runtime.json', dest / 'run', 'quick')
    code = coord.run(dest / 'run')
    result = workflow.snapshot(dest / 'run')
    checks.require(code == 0 and result['status'] == 'completed', '离线演示失败，请检查输出目录中的日志。')
    checks.require(coord.git(repo, 'rev-parse', 'main') == base, '演示主分支不应被修改。')
    return {'status': 'completed', 'worker': 'explicitly_simulated', 'real_model_verified': False,
            'behavior_checks': 4, 'source_main_unchanged': True, 'branch': result['branch'], 'head': result['head'],
            'report': str(dest / 'run/delivery.json'), 'next_step': '本机控制流程可运行；下一步用 doctor --live 检查实际模型，再让 AI 准备真实任务。'}
