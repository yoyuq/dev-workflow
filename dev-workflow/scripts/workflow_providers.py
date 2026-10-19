"""Pinned provider failover and durable quota cooldowns; credentials stay in native profiles."""
from contextlib import contextmanager
import copy
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time

import check_night_run as checks

QUOTA_REASONS = {'account_quota', 'rate_limit'}

class QuotaWait(checks.Rejected):
    def __init__(self, retry_at):
        self.retry_at = retry_at
        super().__init__('All configured providers are cooling down until ' + str(retry_at))

def enabled(config):
    return config.get('quota_recovery', {}).get('enabled') is True

def candidates(config):
    primary = copy.deepcopy(config)
    primary.pop('fallbacks', None)
    items = [(config.get('provider_id', 'primary'), primary)]
    for item in config.get('fallbacks', []):
        value = copy.deepcopy(primary)
        value['harness'] = copy.deepcopy(item['harness'])
        for key in ('protected_files', 'additional_directories', 'preflight_checks'):
            value[key] = list(primary.get(key, [])) + list(item.get(key, []))
        items.append((item['id'], value))
    return items

def validate(config, validate_base):
    policy = config.get('quota_recovery', {})
    checks.require(isinstance(policy, dict) and set(policy) <= {'enabled', 'initial_seconds', 'max_seconds'}, 'unknown quota_recovery policy')
    checks.require(type(policy.get('enabled', False)) is bool, 'quota_recovery.enabled must be boolean')
    if config.get('fallbacks'):
        checks.require(enabled(config), 'fallbacks require quota_recovery.enabled')
        checks.identifier(config.get('provider_id'), 'provider_id')
    if not enabled(config): return
    checks.identifier(config.get('provider_id'), 'provider_id')
    initial, maximum = policy.get('initial_seconds', 60), policy.get('max_seconds', 900)
    checks.integer(initial, 'quota.initial_seconds')
    checks.integer(maximum, 'quota.max_seconds')
    checks.require(initial <= maximum <= 86400, 'quota retry interval must be ordered and at most one day')
    checks.require(isinstance(config.get('fallbacks', []), list) and len(config.get('fallbacks', [])) <= 7, 'too many fallback providers')
    seen = set()
    for ident, candidate in candidates(config):
        checks.identifier(ident, 'provider.id')
        checks.require(ident not in seen, 'duplicate provider id')
        seen.add(ident)
        checks.require(Path(candidate['harness']['executable']).is_absolute(), 'quota provider executable must be absolute and pinned')
        candidate.pop('quota_recovery', None)
        validate_base(candidate)

def protected(config):
    paths = set()
    for _, candidate in candidates(config):
        paths.update(candidate.get('protected_files', []))
        program = candidate['harness']['executable']
        if Path(program).is_absolute() and Path(program).is_file(): paths.add(program)
        paths.update(a for a in candidate['harness'].get('args', []) if Path(a).is_absolute() and Path(a).is_file())
    return paths

def retry_after(message):
    """Accept explicit bounded durations only, never guess a five-hour start/reset."""
    if not isinstance(message, str): message = json.dumps(message, ensure_ascii=False)
    match = re.search(r'(?:retry[-_ ]after(?:_seconds)?)["\s:=>]+([0-9]+(?:\.[0-9]+)?)', message, re.I)
    if match:
        value = float(match.group(1))
        if math.isfinite(value) and 0 < value <= 31 * 86400: return value
    return None

class Cooldowns:
    def __init__(self, path): self.path = Path(path)

    @contextmanager
    def db(self):
        checks.require(not self.path.is_symlink(), 'linked provider cooldown state')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        if os.name != 'nt': self.path.chmod(0o600)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS providers(id TEXT PRIMARY KEY, failures INTEGER NOT NULL, retry_at REAL NOT NULL)')
                yield db
        finally: db.close()

    def due(self, ident, now=None):
        with self.db() as db: row = db.execute('SELECT retry_at FROM providers WHERE id=?', (ident,)).fetchone()
        return row['retry_at'] if row else 0

    def limited(self, ident, policy, server_delay=None, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT failures FROM providers WHERE id=?', (ident,)).fetchone()
            failures = (row['failures'] if row else 0) + 1
            delay = min(policy.get('max_seconds', 900), policy.get('initial_seconds', 60) * 2 ** min(failures-1, 20))
            due = now + max(delay, server_delay or 0)
            db.execute('INSERT INTO providers VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET failures=excluded.failures,retry_at=excluded.retry_at', (ident, failures, due))
        return due

    def success(self, ident):
        with self.db() as db: db.execute('DELETE FROM providers WHERE id=?', (ident,))

def pause(state, bus, retry_at, *, task=None):
    old = state.get('quota_wait', {})
    state['quota_wait'] = {'retry_at': min(old.get('retry_at', retry_at), retry_at), 'since': old.get('since', time.time()), 'task': task}
    state['status'] = 'waiting_for_quota'
    bus.save(state, 'waiting_for_quota', task=task, retry_at=retry_at)
