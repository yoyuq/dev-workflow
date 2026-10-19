"""GitHub repository adapter. Credentials stay in memory; writes reconcile before retry."""
import base64
import json
import os
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import check_night_run as checks


class Blocked(checks.Rejected):
    """An external prerequisite/unknown outcome is not a business-code failure."""


def identity(url):
    match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?', url.strip())
    checks.require(match and '..' not in match[1], 'expected a credential-free GitHub repository URL')
    return match[1]


class GitHub:
    def __init__(self, repository, remaining):
        self.repository, self.remaining = repository, remaining
        self.token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
        if not self.token:
            env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'Never'}
            try:
                result = subprocess.run(['git', '-c', 'credential.interactive=false', 'credential', 'fill'],
                    input='protocol=https\nhost=github.com\n\n', capture_output=True, text=True,
                    env=env, timeout=max(1, min(20, remaining())))
                fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
                if result.returncode == 0: self.token = fields.get('password')
            except (OSError, subprocess.TimeoutExpired): pass
        if not self.token or '\n' in self.token or '\r' in self.token:
            raise Blocked('GitHub authentication unavailable: configure repository credentials or GH_TOKEN; no secret belongs in the plan')

    def request(self, path, method='GET', payload=None, missing=False):
        # Read retries only. Mutating callers reconcile by ref/PR identity.
        for attempt in range(3 if method == 'GET' else 1):
            if self.remaining() <= 0: raise Blocked('GitHub operation budget exhausted')
            request = Request('https://api.github.com/repos/' + self.repository + path, method=method,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                         'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'dev-workflow', 'Content-Type': 'application/json'})
            try:
                with urlopen(request, timeout=max(1, min(15, self.remaining()))) as response:
                    return json.load(response)
            except HTTPError as exc:
                if missing and exc.code == 404: return None
                retryable = exc.code in (429, 500, 502, 503, 504)
                detail = 'GitHub HTTP ' + str(exc.code)  # Never include body/headers/token.
            except (URLError, TimeoutError, OSError, ValueError):
                retryable, detail = True, 'GitHub transport unavailable or response incomplete'
            if method != 'GET' or not retryable or attempt == 2:
                raise Blocked(detail + '; reconcile remote state before resuming')
            time.sleep(min(attempt + 1, max(0, self.remaining())))

    def inspect(self):
        data = self.request('')
        if not data.get('permissions', {}).get('push'): raise Blocked('GitHub repository push permission unavailable')
        if not data.get('allow_merge_commit'): raise Blocked('Repository does not allow merge commits; feature mode preserves explicit merge/revert history')
        return data

    def ref(self, branch):
        data = self.request('/git/ref/heads/' + quote(branch, safe='/'), missing=True)
        return data['object']['sha'] if data else None

    def ensure_parent(self, branch, head):
        old = self.ref(branch)
        if old is None:
            try: self.request('/git/refs', 'POST', {'ref': 'refs/heads/' + branch, 'sha': head})
            except Blocked:
                if self.ref(branch) != head: raise
            old = self.ref(branch)
        if old != head: raise Blocked('Remote work branch differs from the recorded accepted head')

    def push(self, repo, source, target, expected):
        if self.ref(target) == expected: return
        # Authentication is process-local config, never argv, a Git config file, or logs.
        env = self.git_environment()
        try:
            p = subprocess.run(['git', '-c', 'core.hooksPath=', '-C', str(repo), 'push', '--porcelain', 'origin',
                                'refs/heads/' + source + ':refs/heads/' + target], capture_output=True, env=env,
                               timeout=max(1, min(40, self.remaining())))
            succeeded = p.returncode == 0
        except (OSError, subprocess.TimeoutExpired): succeeded = False
        # Unknown push outcomes are safe only when the exact intended ref is observed.
        if self.ref(target) != expected:
            raise Blocked('Git push did not establish the exact recorded remote head' if succeeded else 'Git push unavailable; ref outcome not confirmed')

    def git_environment(self):
        env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
        for key in ('GIT_TRACE', 'GIT_TRACE_CURL', 'GIT_CURL_VERBOSE'): env.pop(key, None)
        count = int(env.get('GIT_CONFIG_COUNT', '0'))
        header = base64.b64encode(('x-access-token:' + self.token).encode()).decode()
        for key, value in (('credential.helper', ''), ('http.https://github.com/.extraHeader', 'AUTHORIZATION: basic ' + header)):
            env['GIT_CONFIG_KEY_' + str(count)] = key; env['GIT_CONFIG_VALUE_' + str(count)] = value; count += 1
        env['GIT_CONFIG_COUNT'] = str(count)
        return env

    def fetch(self, repo, head):
        try:
            p = subprocess.run(['git', '-c', 'core.hooksPath=', '-C', str(repo), 'fetch', '--no-tags', 'origin', head], capture_output=True, env=self.git_environment(),
                               timeout=max(1, min(40, self.remaining())))
            if p.returncode: raise Blocked('Could not fetch the recorded remote commit')
        except (OSError, subprocess.TimeoutExpired): raise Blocked('Remote commit fetch unavailable') from None

    def find_pr(self, head, base):
        query = urlencode({'state': 'all', 'head': self.repository.split('/')[0] + ':' + head, 'base': base, 'per_page': 100})
        rows = self.request('/pulls?' + query)
        if len(rows) >= 100: raise Blocked('PR identity query exceeds bounded result window')
        if len(rows) > 1: raise Blocked('Multiple PRs match the recorded feature identity')
        return rows[0] if rows else None

    def ensure_pr(self, head, base, title, body, draft=False):
        old = self.find_pr(head, base)
        if old: return old
        try: return self.request('/pulls', 'POST', {'head': head, 'base': base, 'title': title, 'body': body, 'draft': draft})
        except Blocked:
            old = self.find_pr(head, base)
            if old: return old
            raise

    def pr(self, number): return self.request('/pulls/' + str(number))

    def status(self, head, state, description):
        self.request('/statuses/' + head, 'POST', {'state': state, 'context': 'dev-workflow/feature', 'description': description[:140]})

    def ci(self, head, required):
        status = self.request('/commits/' + head + '/status?per_page=100')
        runs = self.request('/commits/' + head + '/check-runs?per_page=100&filter=latest')
        if status.get('total_count', 0) > 100 or runs.get('total_count', 0) > 100: raise Blocked('CI result window truncated; cannot establish all checks')
        entries = {}
        apps = {}
        for item in status.get('statuses', []): entries.setdefault(item['context'], item['state'])
        for item in runs.get('check_runs', []):
            value = item.get('conclusion') if item.get('status') == 'completed' else 'pending'
            name = item['name'];apps.setdefault(name, {})[item.get('app', {}).get('id')] = value
            # Same-named checks from different apps may not mask one another.
            previous = entries.get(name)
            if name not in entries or previous == 'success' or value not in ('success', 'pending', None, 'queued', 'in_progress'):
                entries[name] = value
        missing = set(required) - entries.keys()
        for name, required_apps in getattr(self, 'required_apps', {}).items():
            if any(apps.get(name, {}).get(app) != 'success' for app in required_apps): missing.add(name)
        failed = {name: value for name, value in entries.items() if value not in ('success', 'pending', None, 'queued', 'in_progress')}
        pending = missing or any(v in ('pending', None, 'queued', 'in_progress') for v in entries.values())
        return {'head': head, 'checks': entries, 'missing': sorted(missing), 'failed': failed,
                'state': 'failed' if failed else 'pending' if pending else 'passed' if entries else 'not_configured'}

    def required_checks(self, branch):
        required = set()
        self.required_apps = {}
        def bind(context, app):
            required.add(context)
            if app is not None and app > 0: self.required_apps.setdefault(context, set()).add(app)
        policy = self.request('/branches/' + quote(branch, safe='') + '/protection', missing=True)
        branch_info = self.request('/branches/' + quote(branch, safe=''))
        if policy:
            reviews = policy.get('required_pull_request_reviews', {})
            if reviews.get('required_approving_review_count', 0) or reviews.get('require_code_owner_reviews') or policy.get('required_conversation_resolution', {}).get('enabled'):
                raise Blocked('Work branch requires human/owner review or resolved conversations; automatic model review does not satisfy GitHub approval policy')
            status = policy.get('required_status_checks') or {}
            required.update(status.get('contexts', []))
            for c in status.get('checks', []): bind(c['context'], c.get('app_id'))
        rules = self.request('/rules/branches/' + quote(branch, safe=''))
        if branch_info.get('protected') and policy is None and not rules:
            raise Blocked('Protected work branch policy is unavailable; do not use administrative bypass')
        for rule in rules:
            params = rule.get('parameters', {})
            if rule['type'] == 'merge_queue': raise Blocked('Work branch uses a merge queue; direct feature merging is unsupported')
            if rule['type'] == 'pull_request' and (params.get('required_approving_review_count', 0) or params.get('require_code_owner_review') or params.get('required_review_thread_resolution')):
                raise Blocked('Work branch rules require actual human/owner approval; no automatic bypass')
            if rule['type'] == 'required_status_checks':
                for c in params.get('required_status_checks', []): bind(c['context'], c.get('integration_id'))
        return sorted(required)

    def merge(self, number, head, title):
        existing = self.pr(number)
        if existing.get('merged'): return existing['merge_commit_sha']
        try:
            result = self.request('/pulls/' + str(number) + '/merge', 'PUT',
                                  {'sha': head, 'merge_method': 'merge', 'commit_title': title, 'commit_message': 'Accepted feature checks and independent review; parent regression follows.'})
            if result.get('merged'): return result['sha']
        except Blocked:
            observed = self.pr(number)
            if observed.get('merged') and observed['head']['sha'] == head: return observed['merge_commit_sha']
            raise
        raise Blocked('GitHub did not merge the approved feature; retain checks and inspect branch rules')

    def commit(self, head): return self.request('/git/commits/' + head)
