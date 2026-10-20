"""Deterministic local fixtures. A command worker is a simulation, never a model trial."""
from pathlib import Path
import copy
import json
import os
import sys

import check_night_run as checks
import workflow_memory as memory

HERE = Path(__file__).resolve().parent
EXPENSE_BEFORE = '''from decimal import Decimal

def summarize(rows):
    return str(sum(float(row["amount"]) for row in rows))
'''
EXPENSE_AFTER = '''from decimal import Decimal

def summarize(rows):
    total = sum((Decimal(str(row["amount"])) for row in rows if row["status"] == "paid"), Decimal("0"))
    return format(total, ".2f")
'''
SERVICE_BEFORE = 'def total():\n    return 999\n'
SERVICE_AFTER = 'def total():\n    return 30\n'
PAGE_BEFORE = '<!doctype html><html lang="en"><title>Total</title><body><h1>Total</h1><p>TODO</p></body></html>'
PAGE_AFTER = '''<!doctype html><html lang="en"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Total</title>
<body><h1>Total</h1><button id="refresh">Refresh total</button><output id="total" aria-live="polite">Ready</output>
<script>document.getElementById('refresh').addEventListener('click', async () => {
 const output = document.getElementById('total'); output.textContent = 'Loading';
 try { const response = await fetch('/api/total'); if (!response.ok) throw new Error('Request failed');
 output.textContent = String((await response.json()).total); } catch (error) { output.textContent = 'Could not load total'; }
});</script></body></html>'''
SERVER = '''from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import service

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/total":
            body = json.dumps({"total": service.total()}).encode()
            content = "application/json"
        elif self.path == "/":
            body = (Path(__file__).parent / "static/index.html").read_bytes()
            content = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content)
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
'''
CHECK = '''import importlib.util, json, sys, threading, urllib.request, re
from pathlib import Path
sys.dont_write_bytecode = True
repo = Path(sys.argv[1]); mode = sys.argv[2]
sys.path.insert(0, str(repo))
if mode == "expense":
    import expense
    cases = [([], "0.00"), ([{"amount":"0.1", "status":"paid"}, {"amount":"0.2", "status":"paid"}], "0.30"),
             ([{"amount":"10", "status":"paid"}, {"amount":"99", "status":"cancelled"}], "10.00"),
             ([{"amount":"10.25", "status":"paid"}, {"amount":"-1.20", "status":"paid"}], "9.05")]
    for rows, expected in cases:
        actual = expense.summarize(rows)
        assert actual == expected, (rows, actual, expected)
    print("PASS 4 behavioral expense cases: empty, decimal, cancelled, refund")
elif mode == "backend":
    import service
    assert service.total() == 30, service.total()
    print("PASS backend contract")
elif mode == "fullstack":
    import server
    http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
    url = "http://127.0.0.1:" + str(http.server_port)
    try:
        with urllib.request.urlopen(url + "/api/total", timeout=5) as response:
            assert response.headers["Content-Type"].startswith("application/json")
            assert json.load(response) == {"total": 30}
        with urllib.request.urlopen(url, timeout=5) as response:
            html = response.read().decode()
        assert re.search(r'<button[^>]+id=["\\\']refresh["\\\']', html)
        assert re.search(r'<output[^>]+id=["\\\']total["\\\']', html)
        assert "aria-live" in html and "/api/total" in html
        print("PASS live HTTP API, page delivery and DOM contract; browser behavior/visual review separate")
    finally:
        http.shutdown(); http.server_close(); thread.join(timeout=5)
'''


SIMULATED_ALIGNMENT = {
    "method": "grill-me", "mode": "interview", "status": "confirmed",
    "goal": "SIMULATED requirement alignment for deterministic tests only",
    "scope": "Temporary fixture repository; no external writes",
    "acceptance": "Independent fixture checks pass",
    "autonomy": "Bounded local attempts and branch/report delivery",
    "user_confirmation": {"reference": "test-fixture://simulated-human", "quote": "SIMULATED: proceed with this fixture specification"},
    "open_questions": [],
}


def prepare(root, case="bugfix", behavior="success", skill_root=None, harness=None, alignment=None):
    if harness:
        checks.check_requirements_alignment({"requirements_alignment": alignment})
    root.mkdir(parents=True, exist_ok=False)
    repo = root / "project"
    repo.mkdir()
    checks.git(repo, "init", "--template=", "--initial-branch=main")
    for key, value in (("user.name", "Workflow Benchmark"), ("user.email", "workflow-benchmark@example.invalid"),
                       ("commit.gpgsign", "false"), ("core.autocrlf", "false")):
        checks.git(repo, "config", key, value)
    fullstack = case in {"fullstack", "resume", "manual", "regression"}
    if fullstack:
        (repo / "static").mkdir()
        (repo / "service.py").write_text(SERVICE_BEFORE, encoding="utf-8")
        (repo / "server.py").write_text(SERVER, encoding="utf-8")
        (repo / "static/index.html").write_text(PAGE_BEFORE, encoding="utf-8")
    else:
        (repo / "expense.py").write_text(EXPENSE_BEFORE, encoding="utf-8")
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    checks.git(repo, "add", "--all")
    checks.git(repo, "-c", "core.hooksPath=", "commit", "-m", "benchmark baseline")
    base = checks.git(repo, "rev-parse", "HEAD")
    checks.git(repo, "switch", "-c", "workflow/benchmark")
    if skill_root is None:
        skill_root = root / "minimal-test-skill"
        skill_root.mkdir()
        (skill_root / "SKILL.md").write_text("---\nname: dev-workflow\ndescription: Controller fixture, no real model.\n---\n", encoding="utf-8")
    skill_root = skill_root.resolve()
    frozen_check = root / "frozen_check.py"
    frozen_check.write_text(CHECK, encoding="utf-8")
    goal = "Fix expense.summarize: use decimal arithmetic; include only status=paid rows; accept numeric strings; return two decimal places; empty is 0.00; refunds subtract."
    task = {"id": "T1", "module_ids": ["M1"], "module_dependencies": {}, "goal": goal,
            "status": "ready", "depends_on": [], "allowed_scope": ["expense.py"], "required_skills": ["dev-workflow"],
            "internal_guides": ["references/modules/backend-development/guide.md"], "blockers": [],
            "acceptance_criteria": [{"id": "AC1", "description": goal, "requirement_ids": ["R1"], "layer": "functional", "mode": "automated", "verification_ids": ["behavior"]}],
            "verification": [{"id": "behavior", "argv": [sys.executable, "-B", str(frozen_check), str(repo), "expense"], "cwd": ".", "timeout_seconds": 10}]}
    modules = [{"id": "M1", "name": "Business logic", "responsibility": "Implement the requested business behavior", "inputs": ["existing source"], "outputs": ["verified behavior"], "depends_on": []}]
    tasks = [task]
    if fullstack:
        task.update(goal="Fix service.total() to return integer 30; preserve server API shape.", allowed_scope=["service.py"])
        task["acceptance_criteria"][0]["description"] = task["goal"]
        task["verification"][0]["argv"][-1] = "backend"
        modules.append({"id": "M2", "name": "Frontend", "responsibility": "Display the API result", "inputs": ["GET /api/total"], "outputs": ["usable page"], "depends_on": ["M1"]})
        second = copy.deepcopy(task)
        second.update(id="T2", module_ids=["M2"], module_dependencies={"M1": ["T1"]}, depends_on=["T1"], allowed_scope=["static/index.html"],
                      goal="Build a small responsive Total page. Keep the existing server. Add button id=refresh, output id=total with aria-live. On click fetch /api/total and display total; show loading and a readable error on failure. Native HTML/CSS/JS only, no dependencies.",
                      internal_guides=["references/frontend.md"])
        second["acceptance_criteria"][0]["description"] = second["goal"]
        second["verification"][0]["argv"][-1] = "fullstack"
        if case == "regression":
            second["allowed_scope"].append("service.py")
        if case == "manual":
            task["acceptance_criteria"].append({"id": "review", "description": "Review the chosen API contract", "requirement_ids": ["R1"],
                "layer": "specification", "mode": "manual", "verification_ids": [], "review_instructions": "Human confirms the API contract."})
        tasks.append(second)
    plan = {"format_version": 4, "run_id": "benchmark-" + case, "status": "ready", "mode": "execute",
            "requirements_alignment": copy.deepcopy(alignment if harness else SIMULATED_ALIGNMENT),
            "requirements": [{"id": "R1", "description": "Deliver the isolated benchmark behavior with independent evidence"}],
            "execution_plan": {}, "modules": modules, "tasks": tasks,
            "project": {"repository": str(repo), "base_ref": "main", "base_commit": base, "work_branch": "workflow/benchmark", "frontend_surface": "HTML" if fullstack else "none", "backend_stack": "Python standard library"},
            "decision_record": "Fixed local benchmark, supplied fixtures; serial execution and no external effects.",
            "trusted_authorization_ref": "User authorized implementing workflow enhancements and case validation.",
            "skill_versions": {"dev-workflow": {"path": str(skill_root), "sha256": checks.skill_digest(skill_root)}},
            "limits": {"max_run_minutes": 10 if harness else 2, "max_tasks": 3, "max_attempts_per_task": 2, "cost_limit": None},
            "delivery": "branch_commit_and_report"}
    document = root / "execution-plan.md"
    document.write_text(checks.render_execution_plan(plan), encoding="utf-8", newline="\n")
    plan["execution_plan"] = {"path": str(document), "sha256": checks.digest(document)}
    config = {"format_version": 1, "harness": harness or {"kind": "command", "executable": sys.executable,
              "args": ["-B", str(HERE / "benchmark_worker.py"), behavior]},
              "limits": {"attempt_seconds": 180 if harness else 10, "no_progress_seconds": 90 if harness else 4, "max_output_bytes": 4000000},
              "protected_files": [str(frozen_check)], "preflight_checks": [], "memory_project": "benchmark"}
    memory.atomic_json(root / "plan.json", plan)
    memory.atomic_json(root / "runtime.json", config)
    return root, repo, plan, config


def repin(root, plan, config):
    document = Path(plan["execution_plan"]["path"])
    document.write_text(checks.render_execution_plan(plan), encoding="utf-8", newline="\n")
    plan["execution_plan"]["sha256"] = checks.digest(document)
    memory.atomic_json(root / "plan.json", plan)
    memory.atomic_json(root / "runtime.json", config)

