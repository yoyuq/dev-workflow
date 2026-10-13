"""Audit interactive project plans and generate their document and visible nodes.

No execution, model calls, inferred confirmations, or completion claims.
"""
import argparse
import json
from pathlib import Path

import check_night_run as checks
import workflow_session as session
import workflow_titles as titles
import workflow_locale as locale
import workflow_display as display

CONCERNS = {'frontend': 'ui_states', 'backend': 'data_contract',
            'data_change': 'migration', 'access': 'access_control',
            'external': 'dependency_failure', 'operations': 'operation_limits'}
KINDS = {'investigation', 'design', 'implementation', 'validation', 'review', 'delivery'}


def catalog(rows, label):
    checks.require(isinstance(rows, list) and rows, label + ' must be nonempty')
    result = {}
    for row in rows:
        checks.require(isinstance(row, dict), 'invalid ' + label)
        ident = row.get('id'); checks.identifier(ident, label + '.id')
        checks.require(ident not in result, 'duplicate ' + label + ' id')
        result[ident] = row
    return result


def brief(task, language=None, translated=True):
    language = locale.resolve(language); chinese = language == 'zh-CN'
    owner = {**task, 'verification': '; '.join(task['verification'])}
    def value(key): return display.field(owner, key, language) if translated else owner[key]
    return (value('goal') + ('\n产出：' if chinese else '\nOutput: ') + value('output') +
            ('\n验收条件：' if chinese else '\nAcceptance: ') +
            ((', '.join(task['criterion_ids'])) or ('见节点检查' if chinese else 'See node checks')) +
            ('\n检查：' if chinese else '\nChecks: ') + value('verification'))


def project_task(task, modules, language):
    goal = brief(task, language, translated=False)
    return {'id': task['id'], 'title': task['title'], **({'titles':task['titles']} if 'titles' in task else {}),
            'goal': goal, 'module': '、'.join(modules[m]['name'] for m in task['module_ids']),
            'display': {lang: {'goal': {'source': goal, 'text': brief(task, lang)}} for lang in task.get('display', {})},
            'depends_on': task['depends_on']}


def structure(plan):
    if 'locale' in plan: locale.resolve(plan['locale'])
    display.validate(plan, display.ROOT_LIMITS)
    checks.require(isinstance(plan, dict) and type(plan.get('format_version')) is int
                   and plan['format_version'] == 1, 'unsupported interactive plan')
    session.bounded(plan.get('name'), 100, 'name')
    session.bounded(plan.get('goal'), 2000, 'goal')
    checks.text(plan.get('depth_reason'), 'depth_reason')
    checks.require(plan.get('depth') in {'focused', 'feature', 'project'}, 'invalid depth')
    concerns = plan.get('concerns'); checks.strings(concerns, 'concerns')
    checks.require(set(concerns) <= CONCERNS.keys() and len(concerns) == len(set(concerns)), 'invalid concerns')
    decisions = plan.get('decisions')
    checks.require(isinstance(decisions, list), 'decisions must be a list')
    for item in decisions:
        checks.require(isinstance(item, dict), 'invalid decision')
        checks.text(item.get('topic'), 'decision.topic')
        checks.require(item.get('status') in {'answered', 'researched', 'delegated', 'not_applicable', 'open'}, 'invalid decision status')
        if item['status'] == 'open':
            for key in ('question', 'recommendation'): checks.text(item.get(key), 'decision.' + key)
        else:
            for key in ('answer', 'source'): checks.text(item.get(key), 'decision.' + key)
    modules = catalog(plan.get('modules'), 'module')
    for module in modules.values():
        checks.text(module.get('name'), 'module.name'); checks.text(module.get('responsibility'), 'module.responsibility')
    requirements = catalog(plan.get('requirements'), 'requirement')
    criteria = {}
    for rid, req in requirements.items():
        for key in ('text', 'basis'): checks.text(req.get(key), 'requirement.' + key)
        checks.require(req.get('disposition') in {'implement', 'existing', 'deferred', 'out_of_scope'}, 'invalid disposition')
        rows = req.get('acceptance')
        checks.require(isinstance(rows, list), 'acceptance must be a list')
        for cid, row in (catalog(rows, 'criterion') if rows else {}).items():
            checks.require(cid not in criteria, 'duplicate criterion id')
            checks.text(row.get('text'), 'criterion.text'); criteria[cid] = rid
        if req['disposition'] == 'existing': checks.strings(req.get('evidence'), 'existing evidence', nonempty=True)
    tasks = catalog(plan.get('tasks'), 'task')
    checks.require(len(tasks) <= session.MAX_NODES, 'too many visible tasks; refine by real phases')
    for task in tasks.values():
        titles.validate(task)
        checks.require('title' in task, 'task.title required')
        for key in ('goal', 'output'): checks.text(task.get(key), 'task.' + key)
        checks.require(task.get('kind') in KINDS, 'invalid task kind')
        for key in ('module_ids', 'scope', 'skills', 'criterion_ids', 'verification', 'depends_on'):
            checks.strings(task.get(key), 'task.' + key, nonempty=key in {'module_ids', 'scope', 'verification'})
        display.validate({**task, 'verification': '; '.join(task['verification'])}, display.DEPTH_LIMITS)
        checks.require(set(task['module_ids']) <= modules.keys(), 'unknown task module')
        checks.require(set(task['criterion_ids']) <= criteria.keys(), 'unknown task criterion')
        checks.require(len(task['criterion_ids']) == len(set(task['criterion_ids'])), 'duplicate task criterion')
    projected = {tid: session.new_task(project_task(task, modules, plan.get('locale'))) for tid, task in tasks.items()}
    session.validate_tasks(projected)
    return modules, requirements, criteria, tasks


def ancestors(tid, tasks):
    result = set(); remaining = list(tasks[tid]['depends_on'])
    while remaining:
        item = remaining.pop()
        if item not in result: result.add(item); remaining.extend(tasks[item]['depends_on'])
    return result


def audit(plan):
    modules, requirements, criteria, tasks = structure(plan)
    errors, warnings, coverage, next_questions = [], [], [], []
    topics = {'outcome', 'scope', 'acceptance', 'autonomy'}
    if plan['depth'] != 'focused': topics |= {'journeys', 'baseline'}
    if plan['depth'] == 'project': topics.add('delivery')
    topics |= {CONCERNS[c] for c in plan['concerns']}
    present = {d['topic'] for d in plan['decisions']}
    for topic in sorted(topics - present): errors.append('missing decision topic: ' + topic)
    for item in plan['decisions']:
        if item['status'] == 'open':
            errors.append('unresolved decision: ' + item['topic'])
            next_questions.append({k: item[k] for k in ('topic', 'question', 'recommendation')})
    if plan['depth'] == 'focused' and (len(plan['concerns']) > 1 or
            sum(r['disposition'] == 'implement' for r in requirements.values()) > 1):
        warnings.append({'task': None, 'reason': 'Focused depth spans multiple concerns or requirements; verify that a full feature/project has not been reduced to a local repair.'})
    try: checks.check_requirements_alignment(plan)
    except (checks.Rejected, KeyError, TypeError) as exc: errors.append(str(exc))
    for mid in modules:
        if not any(mid in t['module_ids'] for t in tasks.values()): errors.append('module has no task: ' + mid)
    for rid, req in requirements.items():
        rows = []
        if req['disposition'] == 'implement' and not req['acceptance']: errors.append('requirement has no acceptance: ' + rid)
        for criterion in req['acceptance']:
            cid = criterion['id']
            owners = [tid for tid,t in tasks.items() if cid in t['criterion_ids'] and t['kind'] == 'implementation']
            validators = [tid for tid,t in tasks.items() if cid in t['criterion_ids'] and t['kind'] == 'validation']
            if req['disposition'] == 'implement':
                if not owners: errors.append('criterion has no implementation: ' + cid)
                if plan['depth'] == 'project':
                    if not validators: errors.append('criterion has no validation node: ' + cid)
                    elif owners and not any(set(owners) <= ancestors(v,tasks) for v in validators):
                        errors.append('validation does not wait for all implementation: ' + cid)
            rows.append({'id': cid, 'implementation': owners, 'validation': validators})
        coverage.append({'id': rid, 'disposition': req['disposition'], 'criteria': rows,
                         'basis': req['basis'], 'status': 'planned' if req['disposition'] == 'implement' else req['disposition']})
    for tid,task in tasks.items():
        owners = {criteria[cid] for cid in task['criterion_ids']}
        if task['kind'] == 'implementation' and (len(owners) > 1 or len(task['module_ids']) > 1):
            warnings.append({'task': tid, 'reason': 'Multiple requirements or modules share one implementation; inspect outputs, dependencies and recovery boundaries.'})
        if task['kind'] in {'implementation', 'validation'} and not task['criterion_ids']:
            errors.append('implementation/validation has no acceptance link: ' + tid)
        if any(requirements[criteria[cid]]['disposition'] in {'deferred', 'out_of_scope'} for cid in task['criterion_ids']):
            errors.append('task executes excluded requirement: ' + tid)
    if plan['depth'] == 'project':
        reviews = [tid for tid,t in tasks.items() if t['kind'] == 'review']
        deliveries = [tid for tid,t in tasks.items() if t['kind'] == 'delivery']
        if not reviews: errors.append('project has no review node')
        if not deliveries: errors.append('project has no delivery node')
        work = {tid for tid,t in tasks.items() if t['kind'] in {'implementation', 'validation', 'review'}}
        for tid in deliveries:
            if not work <= ancestors(tid,tasks): errors.append('delivery can precede unfinished work: ' + tid)
        implementation = {tid for tid,t in tasks.items() if t['kind'] == 'implementation'}
        if reviews and not any(implementation <= ancestors(tid,tasks) for tid in reviews):
            errors.append('review does not cover final implementation')
    return {'ready': not errors, 'depth': plan['depth'], 'node_count': len(tasks),
            'decision_count': len(plan['decisions']), 'errors': errors, 'warnings': warnings,
            'next_questions': next_questions, 'coverage': coverage,
            'note': 'Plan readiness only; no requirement or test is certified as completed.'}


def view(plan):
    modules, _, _, tasks = structure(plan)
    return {'name': plan['name'], 'goal': plan['goal'], 'display': plan.get('display', {}),
            'locale': locale.resolve(plan.get('locale')), 'tasks': [project_task(t, modules, plan.get('locale')) for t in tasks.values()]}


def render(plan):
    report = audit(plan)
    language = locale.resolve(plan.get('locale'))
    def w(en, zh): return zh if language == 'zh-CN' else en
    lines = ['# ' + display.field(plan, 'name', language) + w(' — Execution plan', '执行计划'), '', display.field(plan, 'goal', language), '',
             w('Depth: ', '深度：') + plan['depth'] + w('; Basis: ', '；依据：') + plan['depth_reason'],
             w('Plan structure: ', '计划结构：') + (w('Ready (not acceptance approval)', '就绪（不代表验收通过）') if report['ready'] else w('Draft; not ready', '草稿，未就绪')), '']
    alignment = plan.get('requirements_alignment', {})
    lines += [w('## Requirements source', '## 需求来源'), '', json.dumps(alignment, ensure_ascii=False, indent=2), '', w('## Decision record', '## 决定记录'), '']
    for d in plan['decisions']:
        lines += ['- ' + d['topic'] + ' · ' + d['status'] + ': ' + d.get('answer', ''),
                  w('  Basis: ', '  依据：') + d.get('source', w('Unresolved', '待解决'))]
        if d['status'] == 'open': lines.append(w('  Question: ', '  问题：') + d['question'] + w('; Recommended: ', '；推荐：') + d['recommendation'])
    lines += ['', w('## Scope and acceptance', '## 范围与验收'), '']
    for req in plan['requirements']:
        lines += ['- ' + req['id'] + ' · ' + req['disposition'] + ': ' + req['text'], w('  Basis: ', '  依据：') + req['basis']]
        lines += ['  - ' + c['id'] + ': ' + c['text'] for c in req['acceptance']]
        lines += [w('  Existing evidence: ', '  现有证据：') + item for item in req.get('evidence', [])]
    lines += ['', w('## Modules', '## 模块'), '']
    lines += ['- ' + m['id'] + ' · ' + m['name'] + ': ' + m['responsibility'] for m in plan['modules']]
    lines += ['', w('## Execution nodes', '## 执行节点'), '']
    for task in plan['tasks']:
        lines += ['### ' + task['id'] + ' · ' + titles.label(task, language), '', brief(task, language),
                  w('Modules: ', '模块：') + ', '.join(task['module_ids']) + w('; Kind: ', '；类型：') + task['kind'],
                  w('Prerequisites: ', '前置：') + (', '.join(task['depends_on']) or w('None', '无')),
                  w('Scope: ', '范围：') + ', '.join(task['scope']), w('Skills: ', '技能：') + (', '.join(task['skills']) or w('Existing project tools', '沿用项目工具')), '']
    lines += [w('## Readiness check', '## 就绪检查'), '', '```json', json.dumps(report, ensure_ascii=False, indent=2), '```', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('audit', 'render', 'view'):
        sub = commands.add_parser(name); sub.add_argument('--input', required=True, type=Path)
        if name != 'audit': sub.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        checks.require(args.input.stat().st_size <= 2_000_000, 'plan too large')
        plan = checks.read_json(args.input)
        if args.command == 'audit':
            result = audit(plan); print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['ready'] else 2
        result = render(plan) if args.command == 'render' else json.dumps(view(plan), ensure_ascii=False, indent=2)
        # Claim a new output; existing specs, views and unrelated files stay intact.
        with args.output.open('x', encoding='utf-8') as file: file.write(result)
        print(json.dumps({'path': str(args.output.resolve()), 'generated_view': True}, ensure_ascii=False))
        return 0
    except (checks.Rejected, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False)); return 2


if __name__ == '__main__': raise SystemExit(main())
