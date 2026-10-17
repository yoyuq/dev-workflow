"""Read-only dashboard projections over existing workflow evidence; no execution."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

import check_night_run as checks
import workflow
from workflow_bus import Bus

MAX_BYTES = 65536
MAX_ITEMS = 300
PROCESS_KEYS = ('started_at', 'reason', 'exit_code', 'duration_seconds', 'environment_failure',
                'permission_denials', 'harness_error', 'reported_model', 'first_artifact_seconds')
EVENT_KEYS = ('task', 'attempt', 'reason', 'commit', 'head', 'status', 'round', 'case_id', 'pr', 'branch')


def redact(value, secret=''):
    if isinstance(value, dict):
        return {k: '[REDACTED]' if re.fullmatch(r'(?i)(authorization|api[_-]?key|access[_-]?token|password|secret)', k) else redact(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secret) for v in value]
    if not isinstance(value, str):
        return value
    if secret: value = value.replace(secret, '[REDACTED]')
    value = re.sub(r'(?i)(bearer\s+)[^\s"\',;]+', r'\1[REDACTED]', value)
    # Consume a complete quoted value (including spaces and escaped quotes) before
    # the unquoted alternative. Covers shell assignments, JSON and Python reprs.
    value = re.sub(r'''(?ix)(\b(?:api[_-]?key|access[_-]?token|password|secret|authorization)["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s"',;]+)''',
                   r'\1[REDACTED]', value)
    value = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9]{15,})\b', '[REDACTED]', value)
    return value


def safe(root, path):
    """Evidence is untrusted, including path strings and links created by workers."""
    path = Path(path).absolute()
    root = Path(root).resolve()
    checks.require(path != root and checks.inside(path, root), '日志不在受管理运行目录内')
    for p in (path, *path.parents):
        checks.require(not p.is_symlink() and not (hasattr(p, 'is_junction') and p.is_junction()), '不读取链接日志')
        if p == root: break
    checks.require(checks.inside(path.resolve(), root), '日志路径越界')
    return path


def read_json(root, path, limit=2000000):
    path = safe(root, path)
    checks.require(path.is_file() and path.stat().st_size <= limit, '证据缺失或超过读取上限')
    return checks.read_json(path)


def children(root, path):
    if not path.exists(): return []
    try:
        safe(root, path)
        return sorted(path.iterdir())[:MAX_ITEMS]
    except (OSError, ValueError, checks.Rejected):
        return []


def catalog(root):
    """Only known artifact kinds; opaque ids never become arbitrary file paths."""
    items = []
    def add(path, label, kind, task='', attempt=None, english=None):
        if len(items) >= MAX_ITEMS: return
        try:
            path = safe(root, path)
            if not path.is_file(): return
            rel = path.relative_to(root).as_posix()
            items.append({'id': hashlib.sha256(rel.encode()).hexdigest()[:24], 'path': rel,
                          'label': label, 'display': {'en': {'label': {'source': label, 'text': english}}} if english else {}, 'kind': kind, 'task_id': task, 'attempt': attempt,
                          'bytes': path.stat().st_size})
        except (OSError, ValueError, checks.Rejected):
            return
    def process(folder, label, task='', attempt=None, english=None):
        en = english or label
        add(folder / 'process.json', label + ' · 进程结果', 'process', task, attempt, en + ' · Process result')
        add(folder / 'stderr.log', label + ' · 错误输出', 'text', task, attempt, en + ' · Error output')
        add(folder / 'events.jsonl', label + ' · 执行事件', 'events', task, attempt, en + ' · Execution events')
    for task in children(root, root / 'attempts'):
        for attempt in children(root, task):
            if attempt.name.isdigit():
                process(attempt, f'{task.name} / 尝试 {attempt.name}', task.name, int(attempt.name), f'{task.name} / Attempt {attempt.name}')
    for area, label in (('reviews', '独立审阅'), ('decisions', '总控决策')):
        for folder in children(root, root / area):
            task = ''
            try:
                context = read_json(root, folder / 'context.json')
                binding = context.get('binding') if isinstance(context, dict) else None
                if isinstance(binding, dict) and isinstance(binding.get('task_id'), str):
                    task = binding['task_id']
            except (OSError, ValueError, checks.Rejected): pass
            en = ('Independent review' if area == 'reviews' else 'Coordinator decision') + ' ' + folder.name
            process(folder / 'process', f'{label} {folder.name}', task, english=en)
            if area == 'reviews': add(folder / 'report.json', f'{label} {folder.name} · 结论', 'review', task, english=en + ' · Result')
    for folder in children(root, root / 'checks'):
        try:
            records = read_json(root, folder / 'results.json')
            if not isinstance(records, list): continue
            for r in records:
                if not isinstance(r, dict): continue
                # Derive from the local check folder, never trust evidence_path.
                name = r.get('log', '')
                if isinstance(name, str) and re.fullmatch(r'check-[a-f0-9]+\.log', name):
                    attempt = re.fullmatch(r'task-(.+)-(\d+)', folder.name)
                    add(folder / name, f"{folder.name} / {r.get('check_id', '检查')}", 'text', r.get('task_id', ''), int(attempt[2]) if attempt else None, f"{folder.name} / {r.get('check_id', 'Check')}")
        except (OSError, ValueError, checks.Rejected): pass
    return items


def event_lines(text):
    """Deliberate allowlist: do not serve raw model messages/reasoning/tool payloads."""
    lines = []
    for line in text.splitlines():
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict): continue
            kind = obj.get('type', '')
            if not isinstance(kind, str): continue
            item = obj.get('item') or {}
            if kind in ('thread.started', 'turn.started', 'turn.completed', 'turn.failed'):
                lines.append(kind)
            elif kind == 'error':
                error = obj.get('error', obj.get('message', 'CLI error'))
                if isinstance(error, dict): error = error.get('message', error.get('type', 'CLI error'))
                lines.append('ERROR ' + str(error)[:2000])
            elif kind == 'result':
                lines.append('result: ' + str(obj.get('subtype', 'finished')) + (' [error]' if obj.get('is_error') else ''))
            elif kind.startswith('item.') and isinstance(item, dict) and item.get('type') in ('command_execution', 'file_change', 'mcp_tool_call'):
                lines.append(kind + ' / ' + item['type'] + ' / ' + str(item.get('status', '')) +
                             (' / exit=' + str(item['exit_code']) if 'exit_code' in item else ''))
            elif kind == 'system' and obj.get('subtype') == 'init':
                lines.append('session initialized / model=' + str(obj.get('model', 'unknown')))
        except (ValueError, TypeError): continue
    return '\n'.join(lines) or '当前窗口暂无可展示的执行事件；原始模型正文不在此视图中展示。'


def log_window(root, artifact_id, secret=''):
    item = next((i for i in catalog(root) if i['id'] == artifact_id), None)
    checks.require(item is not None, '日志不存在或不在可展示列表中')
    path = safe(root, root / item['path'])
    size = path.stat().st_size
    with path.open('rb') as stream:
        stream.seek(max(0, size - MAX_BYTES))
        data = stream.read(MAX_BYTES)
    # Drop a partial first line after tailing; never reinterpret a JSON fragment.
    if size > MAX_BYTES: data = data.partition(b'\n')[2]
    text = data.decode('utf-8', errors='replace')
    if item['kind'] == 'events': text = event_lines(text)
    elif item['kind'] == 'process':
        obj = read_json(root, path)
        checks.require(isinstance(obj, dict), '进程证据格式损坏；其他日志仍可查看')
        text = json.dumps({k: obj[k] for k in PROCESS_KEYS if k in obj}, ensure_ascii=False, indent=2)
    elif item['kind'] == 'review':
        obj = read_json(root, path)
        checks.require(isinstance(obj, dict), '审阅证据格式损坏；其他日志仍可查看')
        text = json.dumps({k: obj[k] for k in ('binding', 'decision', 'summary', 'findings', 'validation') if k in obj}, ensure_ascii=False, indent=2)
    # Structured evidence is projected as a whole before applying the same byte
    # cap. Redact before clipping so a truncated quoted secret cannot escape.
    text = redact(text, secret)
    encoded = text.encode('utf-8')
    clipped = len(encoded) > MAX_BYTES
    if clipped: text = encoded[:MAX_BYTES].decode('utf-8', errors='ignore')
    return {'artifact': redact(item, secret), 'text': text, 'truncated': size > MAX_BYTES or clipped,
            'window_bytes': MAX_BYTES, 'read_at': time.time()}


def advice(reason, status):
    if status in ('quota_wait', 'waiting_for_quota', 'waiting_quota'):
        return '已保存进度，监控脚本会在冷却结束后自动重试；无需人工继续。'
    lowered = reason.lower()
    for key, hint in (
        ('quota', '查看额度等待状态与下次重试时间；已启用恢复策略的运行会自动续跑。'),
        ('authentication', '检查服务器上 CLI 的登录状态，再执行真实环境探针。'),
        ('model_unavailable', '检查本次配置的模型是否可用，再执行真实环境探针。'),
        ('preflight', '先查看文件消息预检和进程记录，确认读写工具与回执。'),
        ('no_progress', '查看最近一次尝试的产物与进程记录；没有可观察变化不等于已确定根因。'),
        ('review', '查看独立审阅记录，区分审阅进程故障与代码修改意见。'),
        ('verification', '打开失败检查的日志，按真实断言与错误输出定位。'),
        ('budget', '本轮预算已用尽；保留现场，重新评估剩余工作，不重置旧预算。')):
        if key in lowered: return hint
    if status == 'awaiting_review': return '查看待回答问题与验收要求，由真实用户完成确认。'
    if status in ('failed', 'blocked'): return '先查看最新失败尝试与检查日志，确认原因再选择恢复方式。'
    return '继续查看任务进度；本看板只读，不会重新派工。'


def registered(service, job_id):
    checks.require(re.fullmatch(r'[a-f0-9]{24}', job_id) is not None, '无效运行 ID')
    with service.db() as db: row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    checks.require(row is not None, '运行不存在')
    root = service.run_path(row['root'])
    return row, root


def plan_changes(base, state):
    """Small, factual diffs of accepted overlays; never expose full proposals."""
    import workflow_replan as replan
    plan, changes, origins = base, [], {}
    for revision in state.get('plan_revisions', []):
        before = {t['id']: t for t in plan['tasks']}
        plan = replan.build(plan, revision['proposal'])
        added, dependencies = [], []
        for task in plan['tasks']:
            tid = task['id']
            if tid not in before:
                added.append(tid)
                origins[tid] = revision['number']
            elif task.get('depends_on', []) != before[tid].get('depends_on', []):
                dependencies.append({'task': tid, 'before': before[tid].get('depends_on', []),
                                     'after': task.get('depends_on', [])})
        changes.append({'revision': revision['number'], 'reason': revision['proposal']['reason'],
                        'added': added, 'dependencies': dependencies})
    return changes, origins


def current_activity(db, tid, entry):
    # Only an explicit public report from the active attempt, not model stdout.
    if entry['status'] != 'running' or not entry['attempts']:
        return None
    row = db.execute("SELECT id,payload,at FROM messages WHERE sender=? AND recipient='coordinator' "
                     "AND kind='progress' ORDER BY seq DESC LIMIT 1", (tid,)).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row['payload'])
        if not isinstance(payload, dict) or payload.get('attempt') != entry['attempts']:
            return None
        text = payload.get('text')
        if not isinstance(text, str) or not text.strip():
            return None
        # Redaction happens before clipping at the response boundary.
        activity = {'text': text, 'at': row['at'], 'attempt': entry['attempts'], 'message_id': row['id']}
        data = payload.get('data', {})
        if isinstance(data, dict) and 'display' in data:
            import workflow_display as display
            try:
                activity['display'] = display.validate({'text': text}, {'text': 280}, data['display'])
            except checks.Rejected: pass  # Invalid optional copy never hides actual progress.
        return activity
    except (ValueError, TypeError):
        return None


def detail(service, job_id):
    row, root = registered(service, job_id)
    plan = checks.read_json(root / 'plan.json')
    bus = Bus(root)
    # One SQLite read snapshot prevents a replan from mixing old nodes and new edges.
    with bus.connection() as db:
        db.execute('BEGIN')
        state = json.loads(db.execute('SELECT value FROM facts WHERE id=1').fetchone()[0])
        activities = {tid: current_activity(db, tid, entry) for tid, entry in state['tasks'].items()}
        events = list(db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 201'))
        messages = list(db.execute('SELECT id,sender,recipient,kind,acked,resolved,at FROM messages ORDER BY seq DESC LIMIT 100'))
    # Replanning definitions are preserved in the loaded contract.
    import workflow_coordinator as coord
    _, definitions, _ = coord.load(root, state=state)
    revisions, origins = plan_changes(plan, state)
    artifacts = catalog(root)
    task_rows = []
    for tid, entry in state['tasks'].items():
        spec = definitions.get(tid, {})
        attempts = []
        for artifact in artifacts:
            if artifact['task_id'] == tid and artifact['kind'] == 'process' and artifact['attempt'] is not None:
                try:
                    raw = read_json(root, root / artifact['path'])
                    if not isinstance(raw, dict): continue
                    attempts.append({'attempt': artifact['attempt'], **{k: raw[k] for k in PROCESS_KEYS if k in raw}})
                except (OSError, ValueError, checks.Rejected): pass
        task_rows.append({'id': tid, 'title': spec.get('title'), 'titles': spec.get('titles', {}), 'display': spec.get('display', {}), 'goal': spec.get('goal', ''), 'module': spec.get('module', ''),
            'depends_on': spec.get('depends_on', []), 'status': entry['status'], 'branch': entry.get('branch'),
            'requires': spec.get('requires', []), 'produces': spec.get('produces', []),
            'activity': activities[tid], 'introduced_revision': origins.get(tid, 0),
            'support_for': spec.get('support_for'),
            'support_kind': spec.get('support_kind'), 'split_children': spec.get('split_children', []),
            'completion_kind': entry.get('completion_kind'),
            'attempts': entry['attempts'], 'commit': entry.get('commit'), 'reason': entry.get('reason', ''),
            'feature_prs': [{k: p.get(k) for k in ('number', 'url', 'phase', 'head', 'merge_head', 'rollback_head', 'attempt')} for p in entry.get('feature_prs', [])],
            'next_step': advice(entry.get('reason', ''), entry['status']), 'processes': attempts,
            'checks': [{k: c.get(k) for k in ('check_id', 'result', 'exit_code', 'duration_seconds', 'timed_out')}
                       for c in entry.get('last_checks', [])],
            'failure_cases': [{'id': c['id'], 'reason': c.get('failure', {}).get('reason', '')}
                              for c in entry.get('failure_cases', [])]})
    timeline = []
    for row_event in reversed(events[:200]):
        data = json.loads(row_event['data'])
        timeline.append({'seq': row_event['seq'], 'at': row_event['at'], 'kind': row_event['kind'],
                         'data': {k: data[k] for k in EVENT_KEYS if k in data}})
    result = redact({'job_id': job_id, 'service_status': row['status'], 'goal': plan['goal'], 'display': plan.get('display', {}),
        'snapshot': workflow.snapshot(root, state=state), 'tasks': task_rows, 'timeline': timeline,
        'plan_changes': revisions,
        'planning': {'active': bool(state.get('coordinator_active')),
                     'pending': bool(state.get('pending_replan'))},
        'timeline_truncated': len(events) > 200, 'messages': [dict(m) for m in messages],
        'artifacts': artifacts, 'artifacts_truncated': len(artifacts) >= MAX_ITEMS,
        'read_at': time.time(), 'read_only': True}, service.token)
    for task in result['tasks']:
        if task['activity']:
            text = task['activity']['text']
            task['activity'].update(text=text[:280], truncated=len(text) > 280)
    return result


def overview(service):
    with service.db() as db:
        rows = list(db.execute('SELECT id,root,status,updated FROM jobs ORDER BY updated DESC LIMIT 101'))
    runs = []
    for row in rows[:100]:
        item = {'id': row['id'], 'service_status': row['status'], 'updated': row['updated']}
        try:
            root = service.run_path(row['root'])
            snap = workflow.snapshot(root)
            plan = checks.read_json(root / 'plan.json')
            item['display'] = plan.get('display', {})
            item.update(run_id=snap['run_id'], status=snap['phase'], counts=snap['task_counts'], quota_wait=snap.get('quota_wait'),
                        budget=snap['budget'], questions=len(snap['human_requests']))
        except (OSError, ValueError, KeyError, TypeError, checks.Rejected, sqlite3.Error) as exc:
            item.update(run_id=Path(row['root']).name, status='recovery_needed', error=str(exc))
        runs.append(item)
    return redact({'runs': runs, 'truncated': len(rows) > 100, 'read_at': time.time()}, service.token)
