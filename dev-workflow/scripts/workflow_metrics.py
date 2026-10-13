"""Read-only timings and provider-reported usage; missing billing data stays unknown."""
from collections import Counter, defaultdict
from contextlib import closing
import argparse
import json
import math
from pathlib import Path
import sqlite3


def nonnegative(value, integer=False):
    if isinstance(value, bool):
        return False
    if integer:
        return isinstance(value, int) and 0 <= value <= 2**63-1
    try:
        return isinstance(value, (int, float)) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def read_json(path):
    if path.is_symlink():
        raise ValueError('Linked telemetry refused')
    return json.loads(path.read_text(encoding='utf-8'))


def process_usage(path, harness, category):
    """Keep each SDK's field semantics; never add a cache subset to input_tokens."""
    for parent in (path, *path.parents):
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('Linked telemetry refused')
    usage, costs, invalid, events = [], [], 0, 0
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                invalid += 1
                continue
            if not isinstance(event, dict):
                invalid += 1
                continue
            events += 1
            kind = event.get('type')
            if kind in ('turn.completed', 'result') and isinstance(event.get('usage'), dict):
                fields = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens',
                          'reasoning_output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')
                raw = event['usage']
                values = {k: raw[k] for k in fields if k in raw and nonnegative(raw[k], integer=True)}
                if 'input_tokens' in values and 'output_tokens' in values and all(k not in raw or k in values for k in fields):
                    usage.append({'format': 'codex' if kind == 'turn.completed' else 'claude', 'tokens': values})
                else:
                    invalid += 1
            if kind == 'result' and nonnegative(event.get('total_cost_usd')):
                costs.append(event['total_cost_usd'])
    record_error = False
    try:
        record = read_json(path.with_name('process.json')) if path.with_name('process.json').is_file() else {}
        if not isinstance(record, dict):
            record = {}
            record_error = True
    except (OSError, ValueError):
        # A crash during the final write must not turn missing telemetry into zero.
        record = {}
        record_error = True
    duration = record.get('duration_seconds')
    return {'category': category, 'harness': harness, 'model_execution_configured': harness in ('codex', 'claude'),
            'record_complete': bool(record), 'record_parse_error': record_error,
            'reason': record.get('reason', 'incomplete_or_active'),
            'duration_seconds': duration if nonnegative(duration) else None,
            'exit_code': record.get('exit_code'), 'event_count': events, 'invalid_events': invalid,
            'usage_events': usage, 'provider_reported_cost_usd': costs[-1] if costs else None}


def collect(run_dir):
    raw = Path(run_dir).expanduser().absolute()
    for parent in (raw, *raw.parents):
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise ValueError('Linked run directories refused')
    root = raw.resolve()
    configs = read_json(root/'runtime.json')
    if not isinstance(configs, dict):
        raise ValueError('Expected runtime role configuration')
    processes = []
    for folder, role in (('attempts', 'worker'), ('reviews', 'reviewer'), ('decisions', 'coordinator')):
        base = root/folder
        if base.is_symlink() or (hasattr(base, 'is_junction') and base.is_junction()):
            raise ValueError('Linked telemetry directory refused')
        harness = configs.get(role, {}).get('harness', {}).get('kind', 'unknown')
        for path in sorted(base.rglob('events.jsonl')):
            item = process_usage(path, harness, role)
            item['artifact'] = path.relative_to(root).as_posix()
            processes.append(item)
    actual_model_logs = [p for p in processes if p['model_execution_configured']]
    totals = defaultdict(Counter)
    for process in actual_model_logs:
        for event in process['usage_events']:
            totals[event['format']].update(event['tokens'])
            totals[event['format']]['usage_event_count'] += 1
    known_costs = [p['provider_reported_cost_usd'] for p in actual_model_logs if p['provider_reported_cost_usd'] is not None]
    count = len(actual_model_logs)
    human_count = None
    database = root/'coordination.sqlite3'
    if database.is_symlink():
        raise ValueError('Linked metrics database refused')
    if database.is_file():
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='human_responses'").fetchone()
            if exists:
                human_count = db.execute('SELECT COUNT(*) FROM human_responses').fetchone()[0]
    return {'format_version': 1, 'process_log_count': len(processes), 'configured_model_process_count': count,
            'simulated_or_unknown_process_count': len(processes)-count,
            'known_process_duration_sum_seconds': sum(p['duration_seconds'] for p in processes if p['duration_seconds'] is not None),
            'duration_unknown_processes': sum(p['duration_seconds'] is None for p in processes),
            'usage_totals_by_format': {k: dict(v) for k, v in totals.items()},
            'usage_coverage': {'known_model_processes': sum(bool(p['usage_events']) for p in actual_model_logs),
                              'unknown_model_processes': sum(not p['usage_events'] for p in actual_model_logs)},
            'money': {'currency': 'USD', 'actual_invoice_total': None, 'actual_invoice_available': False,
                      'known_provider_reported_sum': sum(known_costs) if known_costs else None,
                      'reported_known_processes': len(known_costs), 'reported_unknown_processes': count-len(known_costs)},
            'human_response_records': human_count,
            'host_rescue_actions': None,
            'notes': ['Usage is provider-reported telemetry, not an invoice. Unknown amounts remain null.',
                      'Usage sums exclude simulated/unknown adapters. Interrupted calls may lack final usage; sums can be lower bounds.',
                      'Cache fields retain each SDK format; they are not added to its input total.',
                      'Summed process duration includes parallel work and differs from controller wall time.',
                      'Human response records do not prove human provenance. Host interventions need an explicit operator journal.'],
            'processes': processes}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(collect(args.run_dir), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({'error': str(exc)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
