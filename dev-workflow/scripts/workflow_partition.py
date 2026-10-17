"""Evidence-based branch sizing; thresholds request a decision, not extra scope."""
import argparse
import json
from pathlib import Path

import check_night_run as checks

DEFAULTS = {'max_outcomes': 3, 'max_dependencies': 3, 'max_estimated_seconds': 1800,
            'max_changed_files': 8, 'max_changed_lines': 400, 'max_attempt_seconds': 900}
LEVELS = {'low', 'medium', 'high'}


def policy(plan):
    value = plan.get('partitioning')
    if value is None:
        return None
    checks.require(isinstance(value, dict) and set(value) <= {'thresholds'}, 'invalid partitioning policy')
    thresholds = value.get('thresholds', {})
    checks.require(isinstance(thresholds, dict) and set(thresholds) <= DEFAULTS.keys(), 'unknown partitioning threshold')
    result = {**DEFAULTS, **thresholds}
    for name, limit in result.items():
        checks.integer(limit, 'partitioning.' + name)
    return result


def validate_profile(task):
    p = task.get('partition')
    checks.require(isinstance(p, dict) and set(p) == {'outcomes', 'complexity', 'risk', 'risk_reasons',
                   'estimate_seconds', 'boundary', 'decision', 'rationale', 'evidence'}, 'invalid task partition profile')
    checks.strings(p['outcomes'], 'partition.outcomes', nonempty=True)
    checks.require(len(set(p['outcomes'])) == len(p['outcomes']), 'duplicate partition outcome')
    for key in ('complexity', 'risk'):
        checks.require(p[key] in LEVELS, 'invalid partition.' + key)
    checks.strings(p['risk_reasons'], 'partition.risk_reasons', nonempty=p['risk'] != 'low')
    checks.integer(p['estimate_seconds'], 'partition.estimate_seconds')
    checks.require(p['boundary'] in {'independent', 'atomic'}, 'invalid partition boundary')
    checks.require(p['decision'] in {'keep', 'split', 'investigate'}, 'invalid partition decision')
    checks.text(p['rationale'], 'partition rationale')
    checks.strings(p['evidence'], 'partition evidence', nonempty=True)
    return p


def assess(plan, task):
    limits = policy(plan)
    if limits is None or not task.get('partition'):
        return {'task': task['id'], 'signals': [], 'recommendation': 'keep'}
    p = validate_profile(task)
    dependencies = set(task.get('depends_on', [])) | set(task.get('requires', []))
    signals = []
    if len(p['outcomes']) > limits['max_outcomes']: signals.append('multiple_outcomes')
    if len(dependencies) > limits['max_dependencies']: signals.append('dependency_fan_in')
    if p['estimate_seconds'] > limits['max_estimated_seconds']: signals.append('estimated_duration')
    if p['complexity'] == 'high': signals.append('high_complexity')
    if p['risk'] == 'high': signals.append('high_risk')
    recommendation = 'keep'
    if signals:
        recommendation = 'inspect_atomic_boundary' if p['boundary'] == 'atomic' else 'consider_split'
    if p['decision'] == 'investigate': recommendation = 'investigate'
    return {'task': task['id'], 'signals': signals, 'recommendation': recommendation,
            'outcomes': p['outcomes'], 'dependencies': sorted(dependencies), 'risk': p['risk'],
            'risk_reasons': p['risk_reasons'], 'decision': p['decision'], 'rationale': p['rationale'],
            'evidence': p['evidence']}


def validate(plan, tasks):
    if policy(plan) is None:
        return
    checks.require(plan.get('replanning', {}).get('allow_feature_split') is True,
                   'partitioning requires scoped feature replanning')
    for task in tasks.values():
        if task.get('support_for') and task.get('support_kind') != 'feature': continue
        p = validate_profile(task)
        # A recorded split must be performed before execution; atomic exceptions
        # have a concrete rationale and retain all fixed checks/review.
        checks.require(p['decision'] != 'split' or task.get('split_children'),
                       'partition decision split needs a validated split revision: ' + task['id'])


def runtime_report(plan, task, entry, workspace, names):
    limits = policy(plan)
    if limits is None or task.get('split_children') or not names:
        return None
    # Inspect each changed leaf independently. Includes untracked/explicitly
    # ignored deliverables; never reads a directory or follows a link.
    additions = deletions = 0
    for name in names:
        path = workspace / name
        checks.require(not path.is_symlink(), 'linked partition input')
        try:
            before = checks.git(workspace, 'show', 'HEAD:' + name, binary=True)
        except checks.Rejected:
            before = b''
        after = path.read_bytes() if path.is_file() else b''
        if b'\0' in before or b'\0' in after:
            continue  # Binary changes count as files, never invented line counts.
        import difflib
        for line in difflib.ndiff(before.decode('utf-8', errors='replace').splitlines(),
                                 after.decode('utf-8', errors='replace').splitlines()):
            additions += line.startswith('+ ')
            deletions += line.startswith('- ')
    seconds = entry.get('process', {}).get('duration_seconds', 0)
    signals = []
    if len(names) > limits['max_changed_files']: signals.append('changed_files')
    if additions + deletions > limits['max_changed_lines']: signals.append('changed_lines')
    if seconds > limits['max_attempt_seconds']: signals.append('attempt_duration')
    if not signals:
        return None
    return {'task': task['id'], 'attempt': entry['attempts'], 'signals': signals,
            'changed_files': names, 'added_lines': additions, 'deleted_lines': deletions,
            'draft_files': {name: checks.digest(workspace / name) if (workspace / name).is_file() else None for name in names},
            'duration_seconds': seconds, 'thresholds': limits, 'profile': assess(plan, task),
            'draft_workspace': str(workspace),
            'instruction': 'Inspect this preserved draft. Split independently testable in-scope behavior using original check IDs, or justify an atomic keep decision. No new goal, scope, checks or budgets.'}


def accept_keep(entry, report):
    """A coordinator exception applies only to this recorded draft/attempt."""
    value = entry.get('partition_exception')
    return bool(value and value['attempt'] == entry['attempts'] and value['report'] == report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['audit'])
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args(argv)
    plan = checks.read_json(args.plan)
    tasks = {t['id']: t for t in plan['tasks']}
    try:
        validate(plan, tasks)
        print(json.dumps({'ready': True, 'thresholds': policy(plan),
                          'tasks': [assess(plan, t) for t in tasks.values()],
                          'note': 'Sizing decisions only; no business acceptance or Git mutation.'}, ensure_ascii=False))
        return 0
    except (checks.Rejected, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({'ready': False, 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__': raise SystemExit(main())
