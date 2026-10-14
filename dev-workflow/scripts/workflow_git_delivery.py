"""Feature PR delivery: exact heads, durable remote intents, post-merge revert/retry."""
from pathlib import Path
import time
import uuid

import check_night_run as checks
import workflow_human as human
import workflow_review as review
from workflow_github import GitHub, Blocked, identity


class CIFailed(checks.Rejected): pass


def enabled(plan): return plan.get('delivery') == 'feature_prs'


def validate(plan):
    if not enabled(plan):
        checks.require('remote_git' not in plan, 'remote_git requires feature_prs delivery')
        return
    p = plan.get('remote_git', {})
    checks.require(isinstance(p, dict) and set(p) <= {'provider', 'repository_url', 'base_branch', 'authorization', 'required_checks', 'ci_wait_seconds', 'rollback_reserve_seconds', 'post_merge_checks', 'parent_pr'}, 'unknown remote_git fields')
    checks.require(p.get('provider') == 'github', 'feature_prs currently supports GitHub')
    identity(p['repository_url']);checks.text(p['base_branch'], 'remote_git.base_branch')
    checks.require(plan['limits']['parallelism'] == 1, 'feature_prs advances one accepted feature at a time; use parallelism=1')
    auth = p.get('authorization', {})
    checks.require(auth.get('merge_features') is True and auth.get('revert_failed_merge') is True, 'feature PR merge and revert require actual user authorization')
    checks.text(auth.get('reference'), 'remote Git authorization reference');checks.text(auth.get('quote'), 'remote Git authorization quote')
    checks.strings(p.get('required_checks', []), 'remote Git required_checks')
    checks.integer(p.get('ci_wait_seconds', 120), 'remote Git ci_wait_seconds')
    checks.integer(p.get('rollback_reserve_seconds', 60), 'remote Git rollback reserve', minimum=10)
    checks.require(type(p.get('parent_pr', True)) is bool, 'parent_pr must be boolean')
    checks.require(p.get('post_merge_checks'), 'feature_prs requires fixed post_merge_checks after every merge')


def client(plan, remaining): return GitHub(identity(plan['remote_git']['repository_url']), remaining)


def assert_identity(plan, git):
    expected = identity(plan['remote_git']['repository_url'])
    checks.require(identity(git(Path(plan['repository']), 'remote', 'get-url', 'origin')) == expected, 'project origin does not match authorized remote repository')


def prepare(plan, state, bus, git, remaining):
    assert_identity(plan, git);api = client(plan, remaining);info = api.inspect()
    branch = state['integration_branch'];checks.require(branch.startswith('workflow/' + plan['run_id'] + '/'), 'work branch outside run namespace')
    checks.require(branch != plan['remote_git']['base_branch'] and branch != info['default_branch'], 'work branch must differ from project base/default branch')
    remote = state.setdefault('remote_git', {'parent_branch': branch, 'base_branch': plan['remote_git']['base_branch'], 'prs': [], 'rollbacks': []})
    if not remote.get('prepared'):
        checks.require(api.ref(remote['base_branch']) == plan['base_commit'], 'remote base branch differs from pinned project baseline')
        save(bus, state, 'remote_parent_prepared', head=plan['base_commit'])
        api.ensure_parent(branch, plan['base_commit'])
        remote['prepared'] = True;save(bus, state, 'remote_parent_created', branch=branch, head=plan['base_commit'])
    if api.ref(branch) != state['integration_head'] and not state.get('integration_intent'):
        raise Blocked('Remote work branch changed outside the recorded transaction')
    return api


def current(entry):
    for item in reversed(entry.get('feature_prs', [])):
        if item['attempt'] == entry['attempts']: return item
    return None


def summary(state):
    if 'remote_git' not in state: return None
    return {**state['remote_git'], 'prs': [dict(p, task_id=tid) for tid, entry in state['tasks'].items() for p in entry.get('feature_prs', [])]}


def save(bus, state, event, **data):
    state['remote_git'].update(summary(state))
    bus.save(state, event, **data)


def record_pr(state, entry, pr, attempt):
    item = {'attempt': attempt, 'number': pr['number'], 'url': pr['html_url'], 'head': pr['head']['sha'],
            'head_branch': pr['head']['ref'], 'base_branch': pr['base']['ref'], 'phase': 'published'}
    entry.setdefault('feature_prs', []).append(item)
    return item


def prepare_child(root, plan, tasks, state, bus, tid, git, new_worktree, verify, remaining):
    entry = state['tasks'][tid];api = client(plan, remaining)
    pr = current(entry)
    if pr:
        observed = api.pr(pr['number'])
        if observed['head']['sha'] != entry['commit'] or observed['base']['ref'] != state['integration_branch'] or observed.get('merged') or observed.get('state') != 'open' or observed.get('draft'):
            raise Blocked('Recorded feature PR changed or closed before acceptance')
        if pr.get('validated'): return pr
    expected = state['integration_head'];repo = Path(plan['repository'])
    if api.ref(state['integration_branch']) != expected: raise Blocked('Remote work branch changed before feature PR publication')
    existing = api.ref(entry['branch'])
    if existing is not None and existing != entry['commit']: raise Blocked('Remote feature branch differs from the recorded commit')
    save(bus, state, 'feature_push_prepared', task=tid, head=entry['commit'])
    api.push(repo, entry['branch'], entry['branch'], entry['commit'])
    spec = tasks[tid]
    # Do not publish raw prompts, private paths, logs or user authorization quotes.
    body = 'Feature ' + tid + ' for this work branch.\n\nFixed feature checks and independent review are required before merge; cumulative regression follows on the actual work branch.'
    observed = api.ensure_pr(entry['branch'], state['integration_branch'], spec.get('title', tid), body)
    if observed['head']['sha'] != entry['commit'] or observed['base']['ref'] != state['integration_branch']:
        raise Blocked('PR identity/head does not match the recorded feature')
    pr = record_pr(state, entry, observed, entry['attempts']) if not pr else pr
    save(bus, state, 'feature_pr_created', task=tid, pr=pr['url'], head=pr['head'])
    if observed.get('merged') or observed.get('state') != 'open' or observed.get('draft'):
        raise Blocked('Feature PR is not an open ready PR at the publication checkpoint')
    api.status(entry['commit'], 'pending', 'Fixed PR checks and independent review in progress')
    token = uuid.uuid4().hex[:8]
    workspace = new_worktree(root, plan, 'workflow/' + plan['run_id'] + '/pr-check-' + token, entry['commit'], 'pr-check-' + token)
    records = verify(plan, {tid}, workspace, root/'checks'/('pr-' + tid + '-' + token), remaining())
    pr['checks'] = records;entry['last_checks'] = records
    if not all(c['result'] == 'passed' for c in records):
        pr['phase'] = 'checks_failed';save(bus, state, 'feature_pr_checks_failed', task=tid, pr=pr['url'])
        api.status(entry['commit'], 'failure', 'Pinned feature checks failed; repair required')
        raise checks.Rejected('Fixed feature checks failed on the published PR head')
    pr.update(validated=True, phase='tested');save(bus, state, 'feature_pr_tested', task=tid, pr=pr['url'])
    return pr


def wait_ci(api, plan, head, branch, remaining):
    required = sorted(set(plan['remote_git'].get('required_checks', [])) | set(api.required_checks(branch)))
    deadline = time.monotonic() + min(plan['remote_git'].get('ci_wait_seconds', 120), remaining())
    while True:
        evidence = api.ci(head, required)
        if evidence['state'] in ('passed', 'not_configured') and not evidence['missing']: return evidence
        if evidence['state'] == 'failed': raise CIFailed('GitHub CI failed on exact head ' + head + ': ' + repr(evidence['failed']))
        if time.monotonic() >= deadline: raise Blocked('GitHub CI still pending/missing; do not merge or advance')
        time.sleep(min(2, max(0, deadline - time.monotonic())))


def post_checks(root, plan, ids, workspace, folder, verify, remaining):
    records = verify(plan, ids, workspace, folder, remaining(), final=len(ids) == len(plan['tasks']))
    before = None
    import workflow_coordinator as coord
    before = coord.source_snapshot(workspace, plan['tasks'])
    for c in plan['remote_git']['post_merge_checks']:
        spec = {**c, 'cwd': c.get('cwd', '.'), 'argv': [a.replace('{repo}', str(workspace)) for a in c['argv']]}
        checks.require(checks.inside((workspace/spec['cwd']).resolve(), workspace), 'post-merge check cwd escapes workspace')
        record = checks.run_check('remote-integration', spec, workspace, folder, remaining())
        record['evidence_path'] = str(folder / record['log']);records.append(record)
    checks.require(coord.source_snapshot(workspace, plan['tasks']) == before, 'post-merge verification modified source')
    import workflow_memory as memory
    memory.atomic_json(folder/'results.json', records)
    return records


def commit_shape(api, merged, expected, child, tree):
    info = api.commit(merged)
    parents = [p['sha'] for p in info['parents']]
    if parents != [expected, child] or info['tree']['sha'] != tree:
        raise Blocked('Remote merge ancestry/tree differs from the tested candidate; no automatic overwrite/revert of unknown changes')


def rollback(root, plan, tasks, state, bus, entry, intent, api, git, new_worktree, verify, remaining):
    repo = Path(plan['repository']);remote = state['remote_git'];merged = intent['merged']
    target = intent.get('rollback_head')
    if not target:
        if api.ref(state['integration_branch']) != merged: raise Blocked('Work branch changed after failed merge; rollback cannot overwrite external work')
        token = uuid.uuid4().hex[:8];branch = 'workflow/' + plan['run_id'] + '/rollback-' + token
        workspace = new_worktree(root, plan, branch, merged, 'rollback-' + token)
        git(workspace, 'revert', '--no-edit', '-m', '1', merged)
        target = git(workspace, 'rev-parse', 'HEAD')
        checks.require(git(workspace, 'rev-parse', 'HEAD^{tree}') == git(repo, 'rev-parse', intent['before'] + '^{tree}'), 'revert did not restore the last accepted tree')
        intent.update(rollback_head=target, rollback_branch=branch)
        save(bus, state, 'feature_revert_prepared', task=intent['task_id'], head=target)
    observed = api.ref(state['integration_branch'])
    if observed not in (merged, target): raise Blocked('Work branch changed during revert transaction')
    if observed != target: api.push(repo, intent['rollback_branch'], state['integration_branch'], target)
    token = uuid.uuid4().hex[:8]
    workspace = new_worktree(root, plan, 'workflow/' + plan['run_id'] + '/restore-check-' + token, target, 'restore-check-' + token)
    ids = {t for t, e in state['tasks'].items() if e['status'] == 'integrated'}
    records = post_checks(root, plan, ids, workspace, root/'checks'/('restored-' + token), verify, remaining)
    intent['rollback_checks'] = records
    if not all(r['result'] == 'passed' for r in records):
        save(bus, state, 'restored_regression_failed', task=intent['task_id'], head=target)
        raise Blocked('Revert restored the accepted tree but regression remains unavailable/failed; no feature dispatched')
    if api.ref(state['integration_branch']) != target: raise Blocked('Remote branch changed during restored regression')
    local = git(repo, 'rev-parse', state['integration_branch'])
    if local != target:
        checks.require(local == intent['before'], 'local work branch changed during revert')
        git(repo, 'update-ref', 'refs/heads/' + state['integration_branch'], target, local)
    pr = current(entry);pr.update(phase='reverted', merge_head=merged, rollback_head=target)
    remote['rollbacks'].append({'task_id': intent['task_id'], 'pr': pr['url'], 'merged': merged, 'restored': target, 'tree_restored': True, 'checks': records})
    state.update(integration_head=target, integration_intent=None)
    entry['feedback'] = {'reason': 'Post-merge regression failed; PR ' + pr['url'] + ' reverted, repair against the restored work branch',
                         'previous_workspace': entry['workspace'], 'checks': intent['checks'], 'pr': pr['url']}
    entry.update(status='pending', reason=entry['feedback']['reason'], last_checks=intent['checks'])
    save(bus, state, 'feature_merge_reverted', task=intent['task_id'], pr=pr['url'], head=target)
    return False


def settle(root, plan, tasks, state, bus, git, new_worktree, verify, accept, remaining):
    """Resume a persisted merge/revert intent without reissuing accepted work."""
    intent = state['integration_intent'];tid = intent['task_id'];entry = state['tasks'][tid]
    require_approval(plan, state, entry, tid)
    api = client(plan, remaining);pr = current(entry)
    observed = api.pr(pr['number'])
    if observed['head']['sha'] != intent['feature_head'] or observed['base']['ref'] != state['integration_branch']:
        raise Blocked('Feature PR changed after merge authorization')
    if not intent.get('merged'):
        if not observed.get('merged'):
            if observed.get('state') != 'open' or observed.get('draft'): raise Blocked('Pending merge PR is no longer open/ready')
            if api.ref(state['integration_branch']) != intent['before']: raise Blocked('Work branch changed before recorded merge')
            # Binding CI is rechecked after a crash; approval remains pinned to child head.
            try:
                wait_ci(api, plan, intent['feature_head'], state['integration_branch'], remaining)
            except CIFailed:
                if api.ref(state['integration_branch']) != intent['before'] or api.pr(pr['number']).get('merged'):
                    raise Blocked('CI failed but recorded merge outcome changed; reconcile before repair')
                state['integration_intent'] = None
                save(bus, state, 'feature_ci_failed', task=tid)
                raise
            require_reserve(plan, state, tasks, tid, remaining)
        merged = observed['merge_commit_sha'] if observed.get('merged') else api.merge(pr['number'], intent['feature_head'], 'Workflow merge ' + tid)
        commit_shape(api, merged, intent['before'], intent['feature_head'], intent['candidate_tree'])
        intent['merged'] = merged;pr.update(phase='merged', merge_head=merged)
        save(bus, state, 'feature_pr_merged', task=tid, pr=pr['url'], head=merged)
    else:
        if not observed.get('merged') or observed.get('merge_commit_sha') != intent['merged']:
            raise Blocked('Recorded merge no longer matches the GitHub PR fact')
        commit_shape(api, intent['merged'], intent['before'], intent['feature_head'], intent['candidate_tree'])
    api.fetch(Path(plan['repository']), intent['merged'])
    if intent.get('rollback_head') or intent.get('checks_failed'):
        return rollback(root, plan, tasks, state, bus, entry, intent, api, git, new_worktree, verify, remaining)
    if api.ref(state['integration_branch']) != intent['merged']: raise Blocked('Work branch changed before post-merge regression')
    token = uuid.uuid4().hex[:8]
    workspace = new_worktree(root, plan, 'workflow/' + plan['run_id'] + '/parent-check-' + token, intent['merged'], 'parent-check-' + token)
    save(bus, state, 'parent_regression_started', task=tid, head=intent['merged'])
    ids = {t for t, e in state['tasks'].items() if e['status'] == 'integrated'} | {tid}
    reserve = rollback_budget(plan, state, tasks)
    try:
        records = post_checks(root, plan, ids, workspace, root/'checks'/('parent-' + tid + '-' + token), verify, lambda: max(0, remaining() - reserve))
    except checks.Rejected as exc:
        folder=root/'checks'/('parent-' + tid + '-' + token);folder.mkdir(parents=True,exist_ok=True)
        evidence=folder/'regression-unavailable.log';evidence.write_text(str(exc),encoding='utf-8')
        intent.update(checks_failed=True, checks=[{'task_id':'remote-integration','check_id':'regression-unavailable','result':'failed','log':evidence.name,'evidence_path':str(evidence)}])
        save(bus, state, 'parent_regression_unavailable', task=tid, reason=str(exc))
        return rollback(root, plan, tasks, state, bus, entry, intent, api, git, new_worktree, verify, remaining)
    intent['checks'] = records;pr['post_merge_checks'] = records
    if not all(c['result'] == 'passed' for c in records):
        intent['checks_failed'] = True;save(bus, state, 'parent_regression_failed', task=tid, head=intent['merged'])
        return rollback(root, plan, tasks, state, bus, entry, intent, api, git, new_worktree, verify, remaining)
    if api.ref(state['integration_branch']) != intent['merged']: raise Blocked('Work branch changed during post-merge regression')
    repo = Path(plan['repository']);local = git(repo, 'rev-parse', state['integration_branch'])
    checks.require(local in (intent['before'], intent['merged']), 'local work branch changed outside transaction')
    if local != intent['merged']: git(repo, 'update-ref', 'refs/heads/' + state['integration_branch'], intent['merged'], local)
    pr['phase'] = 'accepted';entry['integration_checks'] = records
    save(bus, state, 'parent_regression_passed', task=tid, head=intent['merged'])
    accept(plan, state, bus, tid, intent['merged'])
    return True


def rollback_budget(plan, state, tasks):
    ids = {t for t, e in state['tasks'].items() if e['status'] == 'integrated'}
    specs = [c for t in tasks.values() if t['id'] in ids for c in t['verification']]
    specs += [c for c in plan.get('assurance', {}).get('checks', []) if c['task_id'] in ids]
    specs += plan['remote_git']['post_merge_checks']
    return sum(c['timeout_seconds'] for c in specs) + plan['remote_git'].get('rollback_reserve_seconds', 60)


def require_reserve(plan, state, tasks, tid, remaining):
    ids = {t for t, e in state['tasks'].items() if e['status'] == 'integrated'} | {tid}
    specs = [c for t in tasks.values() if t['id'] in ids for c in t['verification']]
    specs += [c for c in plan.get('assurance', {}).get('checks', []) if c['task_id'] in ids]
    specs += plan.get('integration_checks', []) if len(ids) == len(tasks) else []
    specs += plan['remote_git']['post_merge_checks']
    required = sum(c['timeout_seconds'] for c in specs) + rollback_budget(plan, state, tasks)
    if remaining() < required: raise Blocked('Insufficient remaining budget for fixed parent regression and reserved revert; feature not merged')


def integrate(root, plan, tasks, state, bus, tid, git, new_worktree, verify, accept, remaining):
    if state.get('integration_intent'):
        return settle(root, plan, tasks, state, bus, git, new_worktree, verify, accept, remaining)
    entry = state['tasks'][tid];pr = current(entry)
    checks.require(pr and pr.get('validated'), 'published PR requires fresh fixed validation')
    require_approval(plan, state, entry, tid)
    api = client(plan, remaining);expected = state['integration_head']
    if api.ref(state['integration_branch']) != expected: raise Blocked('Remote work branch changed before candidate preparation')
    token = uuid.uuid4().hex[:8]
    candidate = new_worktree(root, plan, 'workflow/' + plan['run_id'] + '/candidate-' + token, expected, 'candidate-' + token)
    entry['candidate_workspace'] = str(candidate)
    git(candidate, 'merge', '--no-ff', '-m', 'Workflow candidate ' + tid, entry['commit'])
    ids = {t for t, e in state['tasks'].items() if e['status'] == 'integrated'} | {tid}
    records = verify(plan, ids, candidate, root/'checks'/('candidate-' + tid + '-' + token), remaining(), final=len(ids) == len(tasks))
    entry['integration_checks'] = records
    entry['last_checks'] = records
    checks.require(all(r['result'] == 'passed' for r in records), 'candidate integration checks failed')
    head = git(candidate, 'rev-parse', 'HEAD');entry['review_head'] = head
    if not human.review_valid(plan, tasks[tid], state, head):
        entry.update(status='awaiting_review', reason='Review independently checked merge candidate')
        save(bus, state, 'human_review_required', task=tid, head=head)
        return True
    require_reserve(plan, state, tasks, tid, remaining)
    api.status(entry['commit'], 'success', 'Fixed PR checks and exact-head independent review passed')
    pr['ci'] = wait_ci(api, plan, entry['commit'], state['integration_branch'], remaining)
    observed = api.pr(pr['number'])
    if observed['head']['sha'] != entry['commit'] or observed['base']['ref'] != state['integration_branch'] or observed.get('merged'):
        raise Blocked('PR identity changed before the merge checkpoint')
    if api.ref(state['integration_branch']) != expected: raise Blocked('Work branch changed while CI was checked')
    require_reserve(plan, state, tasks, tid, remaining)
    state['integration_intent'] = {'remote': True, 'task_id': tid, 'before': expected, 'feature_head': entry['commit'],
                                 'candidate_tree': git(candidate, 'rev-parse', 'HEAD^{tree}'), 'pr': pr['number']}
    save(bus, state, 'feature_merge_prepared', task=tid, pr=pr['url'], head=entry['commit'])
    return settle(root, plan, tasks, state, bus, git, new_worktree, verify, accept, remaining)


def require_approval(plan, state, entry, tid):
    approved = entry.get('ai_review', {})
    evidence = Path(approved.get('report_path', ''))
    if (approved.get('decision') != 'approve' or approved.get('binding') != review.binding(plan, state, entry['commit'], tid)
            or not evidence.is_file() or checks.digest(evidence) != approved.get('report_sha256')):
        raise Blocked('Exact-head independent approval/evidence unavailable or changed; no merge/acceptance')


def finish(plan, state, bus, remaining):
    api = client(plan, remaining)
    if api.ref(state['integration_branch']) != state['integration_head']: raise Blocked('Final remote work head differs from accepted delivery')
    if plan['remote_git'].get('parent_pr', True):
        body = 'Completed feature work branch.\n\n' + '\n'.join('- ' + p['url'] + ' — ' + p['phase'] for p in summary(state)['prs'])
        body += '\n\nFinal fixed regression and independent whole-branch review passed. This draft PR requires project review before integration into the base branch.'
        pr = api.ensure_pr(state['integration_branch'], plan['remote_git']['base_branch'], 'Workflow ' + plan['run_id'], body, draft=True)
        if pr['head']['sha'] != state['integration_head'] or pr.get('merged') or not pr.get('draft') or pr.get('state') != 'open': raise Blocked('Parent PR must remain an open draft on the accepted final work head')
        state['remote_git']['parent_pr'] = {'number': pr['number'], 'url': pr['html_url'], 'head': state['integration_head']}
        save(bus, state, 'parent_pr_created', pr=pr['html_url'], head=state['integration_head'])
    state['remote_git']['completed'] = True
