"""Optional presentation labels; task IDs and full goals remain authoritative."""
import re
import check_night_run as checks
import workflow_locale as locale


def valid_title(title):
    return (isinstance(title, str) and title == title.strip() and
            2 <= len(title) <= (12 if re.search(r"[\u4e00-\u9fff]", title) else 40) and
            any(c.isalnum() for c in title) and
            not any(ord(c) < 32 or ord(c) == 127 or c in "\u2028\u2029" for c in title))


def validate(task):
    if "title" in task:
        checks.require(valid_title(task['title']), 'task.title must be short and single-line (CJK 2-12, other 2-40 characters)')
    if 'titles' in task:
        labels = task['titles']
        checks.require(isinstance(labels, dict) and labels and set(labels) <= locale.SUPPORTED and
                       all(valid_title(value) for value in labels.values()), 'invalid localized task titles')


def label(task, language):
    return task.get('titles', {}).get(language, task.get('title', task['id']))

