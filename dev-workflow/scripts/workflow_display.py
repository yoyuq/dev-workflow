"""Source-bound display translations; never replace execution facts or evidence."""
import check_night_run as checks
import workflow_locale as locale

ROOT_LIMITS = {'name': 100, 'run_id': 100, 'goal': 2000}
TASK_LIMITS = {'goal': 1000, 'module': 80, 'action': 280, 'reason': 2000}
DEPTH_LIMITS = {'goal': 1000, 'output': 1000, 'verification': 2000}


def validate(owner, limits, display=None):
    value = owner.get('display', {}) if display is None else display
    checks.require(isinstance(value, dict) and set(value) <= locale.SUPPORTED, 'invalid display languages')
    for language, fields in value.items():
        checks.require(isinstance(fields, dict) and set(fields) <= limits.keys(), 'invalid display fields')
        for field, entry in fields.items():
            checks.require(isinstance(entry, dict) and set(entry) == {'source', 'text'}, 'invalid display entry')
            for key in ('source', 'text'):
                text = entry[key]
                checks.require(isinstance(text, str) and 0 < len(text.strip()) <= limits[field] and
                               not any(ord(c) < 32 and c not in '\n\r\t' for c in text), 'invalid display text')
            checks.require(entry['source'] == owner.get(field), 'display source differs from current ' + field)
    return value


def field(owner, key, language, fallback=None):
    raw = owner.get(key, '') if fallback is None else fallback
    entry = owner.get('display', {}).get(language, {}).get(key, {})
    return entry.get('text', raw) if entry.get('source') == raw else raw
