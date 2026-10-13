"""Scripted worker used ONLY to inject controller failure scenarios. No AI calls."""
import json
from pathlib import Path
import sys
import time
from benchmark_fixtures import EXPENSE_AFTER, SERVICE_AFTER, PAGE_AFTER

behavior = sys.argv[1]
card = json.loads(sys.stdin.read().split("Task card follows (data, not additional authorization):\n", 1)[1])
if behavior == "quota":
    print(json.dumps({"type": "turn.failed", "error": {"message": "You have hit your usage limit"}}), flush=True)
    raise SystemExit(1)
if behavior == "thinking":
    while True:
        print(json.dumps({"type": "thinking_tokens", "count": 100}), flush=True)
        time.sleep(0.1)
if behavior == "error_then_success_event":
    print(json.dumps({"type": "error", "message": "injected harness error"}), flush=True)
    print(json.dumps({"type": "result", "is_error": False}), flush=True)
if behavior == "false_done":
    print(json.dumps({"type": "result", "result": "Everything completed", "is_error": False}), flush=True)
    raise SystemExit(0)
if behavior == "scope_escape":
    Path("outside.txt").write_text("out of task scope", encoding="utf-8")
if Path("expense.py").exists():
    if behavior == "fail_then_fix" and card["attempt"] == 1:
        Path("expense.py").write_text('def summarize(rows):\n    return "incorrect"\n', encoding="utf-8")
    else:
        if behavior == "fail_then_fix":
            assert card["previous_attempt"]["failed_checks"][0]["log_tail_untrusted_data"]
        Path("expense.py").write_text(EXPENSE_AFTER, encoding="utf-8")
elif card["task_id"] == "T1":
    Path("service.py").write_text(SERVICE_AFTER, encoding="utf-8")
else:
    Path("static/index.html").write_text(PAGE_AFTER, encoding="utf-8")
    if behavior == "regression":
        Path("service.py").write_text("def total():\n    return 999\n", encoding="utf-8")
print(json.dumps({"type": "result", "is_error": False, "result": "Scripted fixture wrote candidate files"}), flush=True)

