"""Portable English default and optional local, non-secret display preference."""
import argparse
import json
import os
from pathlib import Path
import tempfile

DEFAULT = 'en'
SUPPORTED = {'en', 'zh-CN'}


def preference_path():
    return Path.home() / '.config/dev-workflow/preferences.json'


def normalize(value):
    if not isinstance(value, str): return None
    value = value.lower()
    if value == 'en' or value.startswith('en-'): return 'en'
    if value == 'zh' or value.startswith('zh-'): return 'zh-CN'
    return None


def resolve(explicit=None, path=None):
    if explicit is not None:
        locale = normalize(explicit)
        if locale is None: raise ValueError('unsupported locale; use en or zh-CN')
        return locale
    path = Path(path) if path is not None else preference_path()
    try:
        if path.stat().st_size > 16384: return DEFAULT
        value = json.loads(path.read_text(encoding='utf-8'))
        return (normalize(value.get('locale')) if isinstance(value, dict) else None) or DEFAULT
    except (OSError, ValueError): return DEFAULT


def save(value, path=None):
    locale = resolve(value)
    path = Path(path) if path is not None else preference_path()
    # Preserve unrelated preferences; refuse to overwrite an unreadable file.
    prefs = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(prefs, dict): raise ValueError('preferences must be an object')
    prefs['locale'] = locale
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.locale-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(prefs, stream, ensure_ascii=False, indent=2); stream.write('\n')
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return {'locale': locale, 'path': str(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--set', choices=sorted(SUPPORTED))
    args = parser.parse_args()
    result = save(args.set) if args.set else {'locale': resolve(), 'path': str(preference_path()), 'default': DEFAULT}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
