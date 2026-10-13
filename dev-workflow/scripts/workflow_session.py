"""Visible progress for an interactive coding session; never executes project work."""
import argparse
from collections import Counter
from contextlib import contextmanager
import copy
import hashlib
import hmac
from http.server import ThreadingHTTPServer
from http.cookies import SimpleCookie, CookieError
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request

import check_night_run as checks
import workflow_memory as memory
import workflow_observer as observer
import workflow_locale as locale
import workflow_display as display
from workflow_service import handler, service_owner

STATUSES = {'pending', 'running', 'blocked', 'failed', 'completed'}
MAX_NODES = 64


def safe_root(path):
    root = Path(path).absolute()
    for parent in (root, *root.parents):
        checks.require(not parent.is_symlink() and not (hasattr(parent, 'is_junction') and parent.is_junction()), 'linked session directory')
    return root.resolve()


def bounded(value, limit, name):
    checks.require(isinstance(value, str) and 0 < len(value.strip()) <= limit, 'invalid ' + name)
    return value.strip()


@contextmanager
def database(root):
    root = safe_root(root)
    path = root/'session.sqlite3'
    checks.require(path.is_file() and not path.is_symlink(), 'interactive session does not exist')
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn: yield conn
    finally: conn.close()


def validate_tasks(tasks):
    checks.require(isinstance(tasks, dict) and 0 < len(tasks) <= MAX_NODES, 'invalid task count')
    remaining = set(tasks)
    for tid, task in tasks.items():
        checks.identifier(tid, 'task id')
        bounded(task['goal'], 1000, 'task goal')
        deps = task['depends_on']
        checks.require(isinstance(deps, list) and len(deps) == len(set(deps)) and all(d in tasks and d != tid for d in deps), 'invalid dependency')
        checks.require(task['status'] in STATUSES, 'invalid task status')
        if task['status'] == 'completed':
            checks.require(task['evidence'] and all(tasks[d]['status'] == 'completed' for d in deps), 'completion needs evidence and completed prerequisites')
    while remaining:
        ready = {tid for tid in remaining if not set(tasks[tid]['depends_on']) & remaining}
        checks.require(ready, 'dependency cycle')
        remaining -= ready


def new_task(raw, revision=0):
    import workflow_titles as titles
    checks.require(isinstance(raw, dict) and set(raw) <= {'id', 'title', 'titles', 'display', 'goal', 'module', 'depends_on'}, 'invalid new task fields')
    titles.validate(raw)
    display.validate(raw, display.TASK_LIMITS)
    tid = raw['id']; checks.identifier(tid, 'task id')
    return {'id': tid, **({key: copy.deepcopy(raw[key]) for key in ('title', 'titles', 'display') if key in raw}), 'goal': bounded(raw['goal'], 1000, 'task goal'), 'module': str(raw.get('module', 'Task'))[:80],
            'depends_on': raw.get('depends_on', []), 'status': 'pending', 'attempts': 0, 'action': '', 'reason': '',
            'evidence': [], 'introduced_revision': revision, 'updated': time.time()}


def initialize(root, value):
    root = safe_root(root)
    checks.require(isinstance(value, dict) and set(value) <= {'goal', 'name', 'tasks', 'locale', 'display'}, 'invalid session plan')
    display.validate(value, display.ROOT_LIMITS)
    checks.require(isinstance(value['tasks'], list) and 0 < len(value['tasks']) <= MAX_NODES, 'invalid tasks')
    tasks = {}
    for raw in value['tasks']:
        task = new_task(raw)
        checks.require(task['id'] not in tasks, 'duplicate task')
        tasks[task['id']] = task
    validate_tasks(tasks)
    state = {'kind': 'interactive-session', 'format_version': 1, 'id': secrets.token_hex(12),
             'name': bounded(value.get('name', root.name), 100, 'name'),
             'goal': bounded(value['goal'], 2000, 'goal'), 'display': copy.deepcopy(value.get('display', {})), 'locale': locale.resolve(value.get('locale')), 'tasks': tasks, 'sequence': 0,
             'plan_changes': [], 'created': time.time(), 'updated': time.time()}
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Refuse collisions before SQLite creates anything in an existing task folder.
    with (root/'session.sqlite3').open('xb'): pass
    with database(root) as db:
        db.executescript('CREATE TABLE facts(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);'
                        'CREATE TABLE events(seq INTEGER PRIMARY KEY,at REAL NOT NULL,kind TEXT NOT NULL,data TEXT NOT NULL);')
        db.execute('INSERT INTO facts VALUES(1,?)', (json.dumps(state, ensure_ascii=False),))
    return state


def read(root):
    with database(root) as db:
        return json.loads(db.execute('SELECT value FROM facts WHERE id=1').fetchone()[0])


def update(root, value):
    checks.require(isinstance(value, dict) and set(value) <= {'expected_sequence', 'updates', 'add', 'dependencies', 'reason', 'display'}, 'invalid update fields')
    checks.require(type(value.get('expected_sequence')) is int, 'expected_sequence is required')
    with database(root) as db:
        db.execute('BEGIN IMMEDIATE')
        state = json.loads(db.execute('SELECT value FROM facts WHERE id=1').fetchone()[0])
        checks.require(state['sequence'] == value['expected_sequence'], 'stale session sequence; read current state first')
        tasks = state['tasks']; before = copy.deepcopy(tasks)
        if 'display' in value:
            display.validate(state, display.ROOT_LIMITS, value['display'])
            state['display'] = copy.deepcopy(value['display'])
        added, changed = value.get('add', []), value.get('dependencies', {})
        checks.require(isinstance(added, list) and isinstance(changed, dict), 'invalid plan change')
        reason = bounded(value.get('reason', ''), 2000, 'change reason') if added or changed else value.get('reason', '')
        checks.require(isinstance(reason, str) and len(reason) <= 2000, 'invalid reason')
        revision = len(state['plan_changes']) + 1
        for raw in added:
            task = new_task(raw, revision)
            checks.require(task['id'] not in tasks, 'duplicate task')
            tasks[task['id']] = task
        for tid, deps in changed.items():
            checks.require(tid in tasks and before.get(tid, {}).get('status') != 'completed', 'cannot change completed or unknown task dependencies')
            tasks[tid]['depends_on'] = deps
        updates = value.get('updates', [])
        checks.require(isinstance(updates, list) and len(updates) <= MAX_NODES, 'invalid updates')
        seen = set()
        for patch in updates:
            checks.require(isinstance(patch, dict) and set(patch) <= {'id', 'status', 'action', 'evidence', 'reason', 'display'}, 'invalid task update')
            tid = patch['id']; checks.require(tid in tasks and tid not in seen, 'unknown or repeated task update'); seen.add(tid)
            task = tasks[tid]; status = patch.get('status', task['status'])
            checks.require(status in STATUSES, 'invalid status')
            checks.require(task['status'] != 'completed' or status == 'completed', 'completed tasks stay complete; add follow-up work')
            if status == 'running' and task['status'] != 'running':
                task['attempts'] += 1
                task['evidence'] = []
            task['status'] = status
            if 'action' in patch: task['action'] = bounded(patch['action'], 280, 'public action')
            if 'reason' in patch:
                checks.require(isinstance(patch['reason'], str) and len(patch['reason']) <= 2000, 'invalid reason')
                task['reason'] = patch['reason']
            if 'evidence' in patch:
                evidence = patch['evidence']
                checks.require(isinstance(evidence, list) and len(evidence) <= 12, 'invalid evidence')
                task['evidence'] = [bounded(item, 2000, 'evidence') for item in evidence]
            if 'display' in patch:
                display.validate(task, display.TASK_LIMITS, patch['display'])
                task['display'] = copy.deepcopy(patch['display'])
            if set(patch) - {'id', 'display'}: task['updated'] = time.time()
        validate_tasks(tasks)
        changes = [{'task': tid, 'before': before[tid]['depends_on'], 'after': t['depends_on']}
                   for tid, t in tasks.items() if tid in before and before[tid]['depends_on'] != t['depends_on']]
        if added or changes:
            checks.require(revision <= 100, 'session revision limit reached')
            state['plan_changes'].append({'revision': revision, 'reason': reason,
                'added': [t['id'] for t in added], 'dependencies': changes})
        state['sequence'] += 1; state['updated'] = time.time()
        db.execute('UPDATE facts SET value=? WHERE id=1', (json.dumps(state, ensure_ascii=False),))
        db.execute('INSERT INTO events(at,kind,data) VALUES(?,?,?)', (state['updated'], '会话进度更新',
            json.dumps({'sequence': state['sequence'], 'reason': reason,
                        'tasks': [t['id'] for t in updates]}, ensure_ascii=False)))
    return state


def detail(root, token=''):
    with database(root) as db:
        db.execute('BEGIN')
        state = json.loads(db.execute('SELECT value FROM facts WHERE id=1').fetchone()[0])
        events = list(db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 201'))
    tasks = []
    for task in state['tasks'].values():
        tasks.append({**task, 'requires': [], 'produces': [], 'branch': None, 'commit': None,
            'activity': {'text': task['action'], 'attempt': task['attempts'], 'at': task['updated']}
                        if task['status'] == 'running' and task['action'] else None,
            'next_step': '查看当前会话中的阻塞说明和验证依据。', 'processes': [], 'checks': [], 'failure_cases': []})
    counts = dict(Counter(t['status'] for t in tasks))
    phase = 'completed' if counts.get('completed') == len(tasks) else 'running' if counts.get('running') else 'blocked' if counts.get('blocked') or counts.get('failed') else 'pending'
    return observer.redact({'job_id': state['id'], 'source': 'interactive', 'service_status': 'observing',
        'goal': state['goal'], 'display': state.get('display', {}), 'reported_at': state['updated'], 'sequence': state['sequence'],
        'snapshot': {'run_id': state['name'], 'mode': 'interactive', 'phase': phase, 'status': phase,
            'revision': len(state['plan_changes']), 'task_counts': counts, 'human_requests': [],
            'budget': {'remaining_seconds': None, 'elapsed_seconds': None}}, 'tasks': tasks,
        'plan_changes': state['plan_changes'], 'planning': {'active': False, 'pending': False},
        'timeline': [{'seq': e['seq'], 'at': e['at'], 'kind': e['kind'], 'data': json.loads(e['data'])} for e in reversed(events[:200])],
        'timeline_truncated': len(events) > 200, 'messages': [], 'artifacts': [], 'artifacts_truncated': False,
        'read_at': time.time(), 'read_only': True}, token)


class SessionViewer:
    """Only the read side of the dashboard contract; no scheduling methods."""
    def __init__(self, root, token=None):
        self.root = safe_root(root)
        read(self.root)
        self.token = token or secrets.token_hex(32)
        self.last_read = time.monotonic()
        self.connect_lock = threading.Lock()
        self.cookie_name = 'workflow_view_' + secrets.token_hex(8)
        self.browser_sessions = {}

    def observe(self):
        d = detail(self.root, self.token); self.last_read = time.monotonic()
        return {'runs': [{'id': d['job_id'], 'run_id': d['snapshot']['run_id'], 'display': d.get('display', {}), 'status': d['snapshot']['phase'],
                         'counts': d['snapshot']['task_counts'], 'updated': d['reported_at']}],
                'truncated': False, 'read_at': time.time()}

    def observe_run(self, job_id):
        d = detail(self.root, self.token)
        checks.require(job_id == d['job_id'], 'unknown session')
        self.last_read = time.monotonic()
        return d

    def observe_log(self, *args): raise checks.Rejected('interactive progress does not expose arbitrary log files')
    def submit(self, *args): raise checks.Rejected('session viewer is read-only; it cannot schedule work')
    def listing(self): return self.observe()

    def connect_viewer(self, code):
        with self.connect_lock:
            grant = read_private(self.root/'connect.json')
            checks.require(isinstance(code, str) and len(code) == 64 and grant['expires'] > time.time()
                           and hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(), grant['digest']), 'invalid connection code')
            private_json(self.root/'connect.json', {'digest': '', 'expires': 0})
            value = secrets.token_hex(32)
            self.browser_sessions = {k: v for k, v in self.browser_sessions.items() if v > time.time()}
            while len(self.browser_sessions) >= 32:
                self.browser_sessions.pop(next(iter(self.browser_sessions)))
            self.browser_sessions[hashlib.sha256(value.encode()).hexdigest()] = time.time() + 8*3600
        self.last_read = time.monotonic()
        return self.browser_cookie(value)

    def browser_cookie(self, value):
        # The local HTTP viewer uses a host-only, HttpOnly browser-session cookie.
        return f'{self.cookie_name}={value}; Path=/api; HttpOnly; SameSite=Strict' + ('' if value else '; Max-Age=0')

    def browser_key(self, raw):
        try:
            cookie = SimpleCookie(); cookie.load(raw)
            value = cookie[self.cookie_name].value
            if len(value) != 64: return None
            return hashlib.sha256(value.encode('ascii')).hexdigest()
        except (CookieError, KeyError, ValueError, UnicodeError): return None

    def browser_authorized(self, raw):
        with self.connect_lock:
            return self.browser_sessions.get(self.browser_key(raw), 0) > time.time()

    def disconnect_browser(self, raw):
        with self.connect_lock:
            self.browser_sessions.pop(self.browser_key(raw), None)
        return self.browser_cookie('')


def private_json(path, value):
    checks.require(not path.is_symlink(), 'linked viewer file')
    memory.atomic_json(path, value)
    if os.name != 'nt': path.chmod(0o600)


def read_private(path):
    checks.require(not path.is_symlink() and path.is_file() and path.stat().st_size <= 16384, 'missing/invalid viewer record')
    return checks.read_json(path)


def connect_url(root, info):
    code = secrets.token_hex(32)
    private_json(root/'connect.json', {'digest': hashlib.sha256(code.encode()).hexdigest(), 'expires': time.time()+120})
    # Exchange this single-use grant for a scoped HttpOnly session cookie.
    return info['url']+'/#connect='+code


def alive(root):
    try:
        info = read_private(root/'viewer.json')
        port = info['port']; checks.require(type(port) is int and 0 < port < 65536, 'invalid port')
        url = f'http://127.0.0.1:{port}'
        request = urllib.request.Request(url+'/api/observe', headers={'Authorization': 'Bearer '+info['token']})
        with urllib.request.urlopen(request, timeout=2) as response: result = json.load(response)
        checks.require(result['runs'][0]['id'] == read(root)['id'], 'wrong viewer')
        info['url'] = url
        return info
    except (OSError, ValueError, KeyError, TypeError, checks.Rejected): return None


def open_viewer(root):
    root = safe_root(root); read(root)
    info = alive(root)
    if info is None:
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        log = root/'viewer.log'; checks.require(not log.is_symlink(), 'linked viewer log')
        with log.open('ab') as output:
            subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), 'serve', '--session-dir', str(root)],
                stdin=subprocess.DEVNULL, stdout=output, stderr=output, **kwargs)
        end = time.monotonic()+8
        while time.monotonic() < end:
            info = alive(root)
            if info: break
            time.sleep(.1)
        checks.require(info is not None, 'viewer did not start; inspect viewer.log')
    return {'url': connect_url(root, info), 'session_dir': str(root), 'read_only': True,
            'note': 'Open this URL within 120 seconds. It connects once; the fragment is cleared. No model or controller launched.'}


def serve(root):
    root = safe_root(root)
    with service_owner(root):
        app = SessionViewer(root)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(app)); server.timeout = 1
        instance = secrets.token_hex(16)
        private_json(root/'viewer.json', {'port': server.server_port, 'pid': os.getpid(), 'token': app.token, 'instance': instance})
        try:
            while time.monotonic()-app.last_read < 600:
                if (root/'close.json').exists() and read_private(root/'close.json').get('instance') == instance: break
                server.handle_request()
        finally: server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'update', 'status', 'open', 'serve', 'close'):
        p = sub.add_parser(name); p.add_argument('--session-dir', type=Path, required=True)
        if name in ('init', 'update'): p.add_argument('--input', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        root = safe_root(args.session_dir)
        if args.command == 'init': result = initialize(root, checks.read_json(args.input))
        elif args.command == 'update': result = update(root, checks.read_json(args.input))
        elif args.command == 'status': result = read(root)
        elif args.command == 'open': result = open_viewer(root)
        elif args.command == 'serve': serve(root); return 0
        else:
            info = alive(root)
            if info: private_json(root/'close.json', {'instance': info['instance']})
            result = {'close_requested': bool(info)}
        print(json.dumps(observer.redact(result), ensure_ascii=False, indent=2)); return 0
    except (checks.Rejected, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False)); return 2


if __name__ == '__main__': raise SystemExit(main())
