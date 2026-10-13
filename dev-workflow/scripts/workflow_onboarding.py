"""Small setup/diagnostic helpers. No project edits, installs, or implicit model calls."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import check_night_run as checks
import workflow_memory as memory
import workflow_portable as portable
import workflow_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]


def discover(kind, explicit=None):
    if explicit:
        return runtime.resolve_program(explicit)
    candidates = [shutil.which(kind + '.exe'), shutil.which(kind)]
    if os.name == 'nt':
        local = os.environ.get('LOCALAPPDATA')
        roaming = os.environ.get('APPDATA')
        if kind == 'codex' and local:
            candidates.append(str(Path(local) / 'Programs/OpenAI/Codex/bin/codex.exe'))
        if kind == 'claude':
            candidates.append(str(Path.home() / '.local/bin/claude.exe'))
            if roaming:
                candidates.append(str(Path(roaming) / 'npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe'))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return runtime.resolve_program(candidate)
        except checks.Rejected:
            pass
    raise checks.Rejected(f'未找到可直接执行的 {kind} CLI。先安装并登录，或用 --executable 指定原生程序；不执行 .cmd/.ps1 包装脚本。')


def generated_config(kind, executable, model=None):
    result = {'format_version': 1, 'harness': {'kind': kind, 'executable': executable},
              'limits': {'attempt_seconds': 600, 'no_progress_seconds': 300, 'max_output_bytes': 8000000},
              'worker_preflight_seconds': 90, 'protected_files': []}
    if model:
        checks.text(model, 'model')
        result['harness']['model'] = model
    return result


def project_info(project):
    project = project.expanduser().resolve()
    checks.require(project.is_dir(), f'项目目录不存在：{project}')
    result = {'path': str(project), 'git': False, 'git_on_path': bool(shutil.which('git')),
              'markers': [n for n in ('pyproject.toml', 'requirements.txt', 'package.json', 'Cargo.toml', 'go.mod') if (project / n).is_file()],
              'project_checks': 'not_run'}
    if result['git_on_path']:
        try:
            result.update(git=True, root=checks.git(project, 'rev-parse', '--show-toplevel'),
                          head=checks.git(project, 'rev-parse', 'HEAD'),
                          dirty=bool(checks.git(project, 'status', '--porcelain=v1')))
        except checks.Rejected:
            result['git'] = False
    return result


def configs_for(args):
    if getattr(args, 'runtime', None):
        configs = checks.read_json(args.runtime.resolve())
        checks.require(isinstance(configs, dict) and 'worker' in configs and set(configs) <= {'worker', 'reviewer', 'coordinator'}, 'runtime 需要 worker，可包含 reviewer/coordinator。')
        checks.require(not args.harness and not args.executable and not args.model, '--runtime 不与 --harness/--executable/--model 混用。')
    else:
        checks.require(args.harness, '请选择 --harness codex 或 --harness claude。')
        config = generated_config(args.harness, discover(args.harness, args.executable), args.model)
        configs = {'worker': config}
        if getattr(args, 'mode', 'quick') == 'full':
            configs.update(reviewer=copy.deepcopy(config), coordinator=copy.deepcopy(config))
    for config in configs.values():
        runtime.validate_config(config)
    return configs


def fresh_directory(output, project=None):
    dest = portable.ensure_destination(ROOT, output)
    if project:
        project = project.resolve()
        checks.require(not checks.inside(dest, project) and not checks.inside(project, dest), '准备/探针目录必须在项目仓库之外，例如项目旁边的 .workflow-项目名。')
    checks.require(not dest.exists(), f'输出目录已存在，已保留：{dest}。请指定新目录；检查已有配置可用 doctor --runtime。')
    dest.mkdir(parents=True)
    return dest


def live_probe(config, folder, seconds):
    """Same native adapter as a worker/reviewer; a fresh, synthetic Git workspace."""
    import workflow_coordinator as coord
    folder.mkdir(parents=True)
    workspace = folder / 'workspace'
    workspace.mkdir()
    coord.git(workspace, 'init', '--template=', '--initial-branch=probe')
    nonce = uuid.uuid4().hex
    (workspace / 'challenge.txt').write_text(nonce, encoding='utf-8')
    prompt = ('This is a small environment probe, not a development task. Read challenge.txt in this directory. '
              'Using permitted file tools, create probe.json here containing {"nonce":"the exact file contents"}, then finish. '
              'Do not inspect user projects, load skills, run project tests, install anything, change settings, or start other agents. '
              'If unable to read/write, report that honestly; do not seek extra permissions.')
    probe_config = copy.deepcopy(config)
    probe_config['limits'].update(attempt_seconds=seconds, no_progress_seconds=seconds, max_output_bytes=1000000)
    output = workspace / 'probe.json'
    process = runtime.run_worker(probe_config, workspace, ROOT, prompt, folder / 'process',
        lambda: checks.digest(output) if output.is_file() and not output.is_symlink() else '', seconds, folder / 'cancel.request')
    observed = None
    if output.is_file() and not output.is_symlink() and output.stat().st_size < 4096:
        try:
            observed = checks.read_json(output)
        except (ValueError, OSError):
            pass
    passed = process['reason'] == 'exited' and process['exit_code'] == 0 and not process['harness_error'] and observed == {'nonce': nonce}
    failure = process.get('environment_failure') or process['reason'] if not passed else None
    if not passed and failure == 'exited':
        failure = 'missing_or_invalid_file_evidence' if not process['harness_error'] else 'harness_error'
    return {'status': 'passed' if passed else 'failed', 'failure': failure,
            'model_request_and_file_roundtrip': passed, 'configured_model': config['harness'].get('model', 'inherit'),
            'process': process, 'evidence': str(folder), 'project_commands_and_browser': 'not_tested'}


def doctor(args):
    report = {'package': portable.check_package(), 'python': sys.version.split()[0],
              'project': project_info(args.project), 'offline_ok': False, 'live_status': 'not_requested',
              'issues': [], 'warnings': [], 'roles': {}, 'ready_for_project_execution': False}
    info = report['project']
    if not info['git_on_path']:
        report['issues'].append('找不到 Git；控制器需要 Git。安装后重新检查。')
    elif not info['git']:
        report['warnings'].append('项目尚无可读取的 Git 提交；控制器启动前需准备基线，轻量对话开发不受此检查限制。')
    elif info.get('dirty'):
        report['warnings'].append('项目有未提交内容；先由 AI 说明如何保留这些修改，控制器基线只包含已提交内容。')
    try:
        configs = configs_for(args)
    except (checks.Rejected, OSError, ValueError) as exc:
        report['issues'].append(str(exc))
        report['next_step'] = '先修复 issues 中的具体问题，再重新检查。'
        return report
    for role, config in configs.items():
        try:
            # Offline diagnostics do not run arbitrary configured project probes.
            env = runtime.preflight({**config, 'preflight_checks': []}, Path(info['path']))
            kind = config['harness']['kind']
            checks.require(kind == 'command' or kind in env['version'].casefold(), f'{role} 程序的版本输出不像 {kind}；检查 --executable 是否指向正确 CLI。')
            report['roles'][role] = {'offline': 'passed', 'environment': env}
            if not config['harness'].get('model'):
                report['warnings'].append(f'{role} 沿用宿主模型；以后宿主配置改变会影响运行。无人值守时用已验证的 --model 固定模型。')
        except (checks.Rejected, OSError, ValueError) as exc:
            report['roles'][role] = {'offline': 'failed', 'error': str(exc)}
            report['issues'].append(f'{role}: {exc}')
    report['offline_ok'] = not report['issues']
    if args.live:
        checks.require(args.output is not None, '--live 会实际调用模型并消耗额度，请用 --output 指定新的探针证据目录。')
        checks.require(5 <= args.seconds <= 300, '探针 --seconds 需为 5..300；这是每个不同角色配置的上限。')
        if not report['offline_ok']:
            report['live_status'] = 'skipped_offline_failure'
            return report
        dest = fresh_directory(args.output, Path(info.get('root', info['path'])))
        # Identical role configurations use the same adapter and permission mode.
        cache = {}
        for role, config in configs.items():
            key = json.dumps(config, sort_keys=True)
            if key not in cache:
                cache[key] = (role, live_probe(config, dest / role, args.seconds))
            owner, proof = cache[key]
            report['roles'][role]['live'] = {**proof, 'shared_with': owner if owner != role else None}
        report['live_status'] = 'passed' if all(r['live']['status'] == 'passed' for r in report['roles'].values()) else 'failed'
        if report['live_status'] == 'failed':
            report['issues'].append('真实探针失败：先解决模型/登录/额度/文件权限或响应超时，再启动业务任务；详见各角色证据。')
    report['next_step'] = ('先修复 issues 中的具体问题。' if report['issues'] else
        '让 AI 对齐需求并准备固定验收。此检查不等于项目依赖、浏览器或业务验收通过。')
    if args.live and report['offline_ok']:
        memory.atomic_json(dest / 'doctor.json', report)
    return report


def setup(args):
    configs = configs_for(args)
    info = project_info(args.project)
    project = Path(info.get('root', info['path']))
    dest = args.output or project.parent / ('.workflow-' + project.name)
    dest = fresh_directory(dest, project)
    memory.atomic_json(dest / 'runtime.json', configs)
    memory.atomic_json(dest / 'project.json', info)
    (dest / 'START.md').write_text(f'''# 项目准备已生成

项目：{project}
运行配置：{dest / 'runtime.json'}
档位：{args.mode}。这不是已确认的任务计划，也未启动模型或修改项目。

把本文件交给 AI 并描述目标。AI 应先复用对话中的需求确认，只问必要问题；默认轻量开发。需要控制器时，由 AI 生成模块拆解、执行计划、plan.json、独立验收和实际确认引用，用户不必手填 JSON。

先用 workflow.py doctor --project <项目> --runtime <这里的 runtime.json> 检查。要验证模型实际可用，再加 --live --output <新的项目外探针目录>；此步骤会消耗模型额度。full 的 worker/reviewer/coordinator 均检查；相同配置共用探针。尚未指定模型时沿用宿主，自动运行前优先使用已经验证的明确模型。

本目录留给任务输入；初始化运行放在本目录的新子目录 run。项目检查、需求确认和预算由 AI 在开跑前准备并固定。不要把示例或本文件当作用户确认，也不要把项目中的未提交修改丢掉。使用统一入口 init/run/status/report；遇到环境阻塞保留现场。
''', encoding='utf-8')
    return {'status': 'prepared', 'directory': str(dest), 'runtime': str(dest / 'runtime.json'),
            'next_step': '把 START.md 和目标交给 AI；无需手写运行配置。未启动模型，未修改项目。'}


def add_commands(commands):
    for name in ('doctor', 'setup'):
        parser = commands.add_parser(name, help='环境检查（默认离线）' if name == 'doctor' else '自动准备项目运行配置，不启动任务')
        parser.add_argument('--project', type=Path, default=Path.cwd())
        parser.add_argument('--harness', choices=('codex', 'claude'), required=name == 'setup')
        parser.add_argument('--executable')
        parser.add_argument('--model')
        parser.add_argument('--output', type=Path)
        parser.add_argument('--mode', choices=('quick', 'full'), default='quick')
        parser.add_argument('--json', action='store_true', help='输出机器可读 JSON；默认显示简短中文结果')
        if name == 'doctor':
            parser.add_argument('--runtime', type=Path)
            parser.add_argument('--live', action='store_true')
            parser.add_argument('--seconds', type=int, default=90)
    parser = commands.add_parser('self-test', help='在新隔离目录演示提交与验收；模拟工作者，不调用模型')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--json', action='store_true')


def dispatch(args):
    if args.command == 'self-test':
        from workflow_self_test import execute
        result = execute(args.output)
    else:
        result = doctor(args) if args.command == 'doctor' else setup(args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == 'doctor':
        print('环境检查：' + ('需要处理' if result['issues'] else '基础检查通过'))
        print('项目：' + result['project']['path'])
        for role, item in result['roles'].items():
            live = item.get('live', {})
            detail = ('模型及文件读写通过' if live.get('status') == 'passed' else
                      '真实探针失败：' + str(live.get('failure')) if live else
                      'CLI 可用，模型尚未实际检查' if item['offline'] == 'passed' else item['error'])
            print(f'{role}：{detail}')
            if live.get('evidence'): print('  证据：' + live['evidence'])
        for label, key in (('待处理', 'issues'), ('提醒', 'warnings')):
            for message in dict.fromkeys(result[key]): print(label + '：' + message)
        print('下一步：' + result.get('next_step', '解决上述问题后重新检查。'))
    elif args.command == 'setup':
        print('准备完成：' + result['directory'])
        print('把其中的 START.md 和你的目标交给 AI。未启动模型，未修改项目。')
    else:
        print('离线演示通过：4 项行为检查、分支提交和报告。工作者为模拟程序，没有调用模型。')
        print('报告：' + result['report'])
        print('下一步：' + result['next_step'])
    return 2 if result.get('issues') else 0
