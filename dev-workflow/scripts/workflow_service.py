#!/usr/bin/env python3
"""Durable event-driven local control service. Python stdlib, no cron or shell eval."""
import argparse
from contextlib import contextmanager, closing
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import runpy
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import check_night_run as checks
import workflow
import workflow_coordinator as coord
import workflow_human as human
import workflow_memory as memory
import workflow_runner as serial
from workflow_bus import Bus, encoded
import workflow_observer as observer


@contextmanager
def service_owner(root):
    """Kernel-held single-owner lock, automatically released even on a hard crash."""
    with (root / "service.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise checks.Rejected("service already has an active owner") from exc
        try: yield
        finally:
            stream.seek(0)
            if os.name == "nt": msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def initialize(root, runs_root):
    checks.require(not root.exists(), "service state already exists")
    checks.require(runs_root.is_dir() and runs_root.is_absolute(), "runs-root must be an existing absolute directory")
    checks.require(not checks.inside(root, runs_root), "keep service credentials outside the allowed run tree")
    root.mkdir(parents=True, mode=0o700)
    (root / "logs").mkdir()
    memory.atomic_json(root / "service.json", {"format_version": 1, "runs_root": str(runs_root.resolve())})
    token = root / "auth.token"
    with token.open("x", encoding="ascii") as stream: stream.write(secrets.token_hex(32))
    if os.name != "nt": token.chmod(0o600)
    with closing(sqlite3.connect(root / "service.sqlite3")) as db:
        db.executescript('''
        CREATE TABLE jobs (id TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
            generation INTEGER NOT NULL DEFAULT 0, pid INTEGER, updated REAL NOT NULL, detail TEXT NOT NULL DEFAULT '');
        CREATE TABLE requests (id TEXT PRIMARY KEY, payload TEXT NOT NULL, job_id TEXT NOT NULL);
        CREATE TABLE events (seq INTEGER PRIMARY KEY, at REAL NOT NULL, job_id TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
        ''')
        db.commit()


class Service:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.runs_root = Path(checks.read_json(self.root / "service.json")["runs_root"]).resolve()
        self.token = (self.root / "auth.token").read_text(encoding="ascii").strip()
        checks.require(len(self.token) >= 32, "weak/missing service token")
        self.condition = threading.Condition()
        self.stopping = False

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root / "service.sqlite3", timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def run_path(self, path):
        raw = Path(path).absolute()
        for p in (raw, *raw.parents):
            checks.require(not p.is_symlink() and not (hasattr(p, "is_junction") and p.is_junction()), "linked run path")
        root = raw.resolve()
        checks.require(root != self.runs_root and checks.inside(root, self.runs_root), "run path outside allowlisted runs-root")
        coord.load(root)
        return root

    def event(self, db, jid, kind, detail=""):
        db.execute("INSERT INTO events(at,job_id,kind,detail) VALUES(?,?,?,?)", (time.time(), jid, kind, detail))

    def observe(self):
        return observer.overview(self)

    def observe_run(self, job_id):
        return observer.detail(self, job_id)

    def observe_log(self, job_id, artifact_id):
        _, root = observer.registered(self, job_id)
        return observer.log_window(root, artifact_id, self.token)

    def submit(self, request):
        checks.require(set(request) in ({"id", "run_dir"}, {"id", "run_dir", "response"}, {"id", "run_dir", "cancel"}), "invalid service event")
        checks.identifier(request["id"], "request.id")
        checks.require("cancel" not in request or request["cancel"] is True, "cancel must be true")
        root = self.run_path(request["run_dir"])
        jid = human.digest(str(root))[:24]
        payload = encoded(request)
        with self.condition, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM requests WHERE id=?", (request["id"],)).fetchone()
            if old:
                checks.require(old["payload"] == payload, "event ID reused with different content")
                return {"job_id": old["job_id"], "duplicate": True}
            row = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            if "response" in request:
                # Queue human response only; coordinator lock owner consumes it.
                human.submit(root, request["response"])
            if request.get("cancel") is True:
                (root / "cancel.request").write_text("Service operator cancelled", encoding="utf-8")
            state = Bus(root).state()
            status = row["status"] if row and row["status"] in {"starting", "running"} else (
                "cancelled" if (root / "cancel.request").exists() else "completed" if state["status"] == "completed" and "response" not in request else "queued")
            db.execute("INSERT INTO jobs(id,root,status,updated) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,updated=excluded.updated", (jid, str(root), status, time.time()))
            db.execute("INSERT INTO requests VALUES(?,?,?)", (request["id"], payload, jid))
            self.event(db, jid, "operator_event", payload)
            self.condition.notify_all()
        return {"job_id": jid, "duplicate": False}

    def recover_jobs(self):
        """Never launch a replacement while an old controller/worker might live."""
        with self.db() as db: rows = list(db.execute("SELECT * FROM jobs WHERE status IN ('starting','running')"))
        for row in rows:
            status, detail = "queued", "Recovered abandoned service generation"
            try:
                checks.require(not row["pid"] or not serial.process_alive(row["pid"]), "recorded controller may still live; inspect before restarting service")
                root = self.run_path(row["root"])
                state = Bus(root).state()
                if state.get("session_started") or (root / "runner.lock").exists(): coord.recover(root)
                if (root / "cancel.request").exists(): status = "cancelled"
                elif Bus(root).state()["status"] == "completed": status = "completed"
            except (checks.Rejected, OSError, KeyError, ValueError) as exc:
                status, detail = "recovery_needed", str(exc)
            with self.db() as db:
                db.execute("UPDATE jobs SET status=?,generation=generation+1,pid=NULL,updated=?,detail=? WHERE id=?", (status, time.time(), detail, row["id"]))
                self.event(db, row["id"], "recovery", detail)

    def claim(self):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY updated,id LIMIT 1").fetchone()
            if not row: return None
            generation = row["generation"] + 1
            db.execute("UPDATE jobs SET status='starting',generation=?,pid=NULL,updated=? WHERE id=?", (generation, time.time(), row["id"]))
            self.event(db, row["id"], "dispatch", str(generation))
            return {**dict(row), "generation": generation}

    def finish(self, jid, generation, returncode):
        with self.condition, self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
            if not row or row["generation"] != generation: return
            root = Path(row["root"])
            status, detail = "failed", f"controller exit {returncode}"
            try:
                snapshot = workflow.snapshot(root)
                with Bus(root).connection() as run_db:
                    pending = run_db.execute("SELECT COUNT(*) FROM human_responses WHERE status='queued'").fetchone()[0]
                status = "cancelled" if snapshot["cancelled"] else "queued" if pending else "completed" if snapshot["status"] == "completed" else "waiting" if snapshot["human_requests"] else "failed"
                detail = encoded(snapshot)
            except (checks.Rejected, OSError, ValueError, KeyError) as exc: detail = str(exc)
            db.execute("UPDATE jobs SET status=?,pid=NULL,updated=?,detail=? WHERE id=?", (status, time.time(), detail, jid))
            self.event(db, jid, "controller_finished", detail)
            self.condition.notify_all()

    def dispatch_once(self):
        job = self.claim()
        if not job: return False
        log = self.root / "logs" / f"{job['id']}-{job['generation']}.log"
        try:
            with log.open("xb") as output:
                env = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
                process = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "execute", "--state-dir", str(self.root),
                    "--job", job["id"], "--generation", str(job["generation"])], stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                code = process.wait()
        except OSError as exc:
            code = 2
            with self.db() as db: self.event(db, job["id"], "launch_failed", str(exc))
        self.finish(job["id"], job["generation"], code)
        return True

    def loop(self):
        while True:
            with self.condition:
                if self.stopping: return
            if self.dispatch_once(): continue
            # Completion wakes the next iteration; an idle service blocks on an event.
            with self.condition:
                with self.db() as db: queued = db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued'").fetchone()[0]
                if not queued and not self.stopping: self.condition.wait()

    def listing(self):
        with self.db() as db: rows = [dict(r) for r in db.execute("SELECT * FROM jobs ORDER BY updated DESC")]
        for row in rows:
            try: row["workflow"] = workflow.snapshot(Path(row["root"]))
            except (checks.Rejected, OSError, ValueError, KeyError) as exc: row["error"] = str(exc)
        return rows


def execute(service, jid, generation):
    # Child claims its generation before touching project state. A delayed child
    # from a crashed dispatcher cannot start after recovery changed that generation.
    with service.db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
        checks.require(row and row["status"] == "starting" and row["generation"] == generation, "stale service generation")
        db.execute("UPDATE jobs SET status='running',pid=?,updated=? WHERE id=?", (os.getpid(), time.time(), jid))
    root = service.run_path(row["root"])
    plan = checks.read_json(root / "plan.json")
    frozen = Path(plan["skill_root"]) / "scripts/workflow_coordinator.py"
    checks.require(frozen.is_file(), "frozen coordinator is unavailable; restore the run's Skill snapshot")
    # Use the run's frozen implementation, never substitute an upgraded service's engine.
    # Execute the frozen controller in this registered process. A subprocess
    # wrapper could die before its child acquires runner.lock, leaving an
    # unregistered live controller during service recovery.
    previous_argv = sys.argv
    sys.argv = [str(frozen), "run", "--run-dir", str(root)]
    sys.path.insert(0, str(frozen.parent))
    # The service may be newer than this run. Fresh imports must resolve to the
    # frozen directory instead of reusing the service's imported controller.
    for name in list(sys.modules):
        if name == "check_night_run" or name == "workflow" or name.startswith("workflow_"):
            del sys.modules[name]
    try:
        runpy.run_path(str(frozen), run_name="__main__")
        return 0
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        sys.argv = previous_argv


ASSETS = Path(__file__).resolve().parents[1] / 'assets/dashboard'
DASHBOARD = (ASSETS / 'index.html').read_text(encoding='utf-8')


def handler(service):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass  # Never log auth headers or response bodies.
        def send(self, status, value, html=False, content_type=None, cookie=None):
            raw = (value if html or content_type else json.dumps(value, ensure_ascii=False)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type or ("text/html; charset=utf-8" if html else "application/json; charset=utf-8"))
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            if cookie: self.send_header('Set-Cookie', cookie)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(raw)
        def authorized(self):
            value = self.headers.get("Authorization", "")
            return hmac.compare_digest(value, "Bearer " + service.token) or (hasattr(service, 'browser_authorized')
                and service.browser_authorized(self.headers.get('Cookie', '')))
        def local_request(self):
            if not hasattr(service, 'connect_viewer'): return True
            host = f'127.0.0.1:{self.server.server_port}'
            return (self.headers.get('Host') == host and self.headers.get('Origin', 'http://' + host) == 'http://' + host
                    and self.headers.get('Sec-Fetch-Site', '') != 'cross-site')
        def do_GET(self):
            path = urlsplit(self.path).path
            if not self.local_request(): return self.send(403, {'error': 'local viewer origin required'})
            if path == "/":
                import workflow_locale as locale
                page = DASHBOARD.replace('<html lang="en">', '<html lang="en" data-preferred-locale="' + locale.resolve() + '">')
                if hasattr(service, 'connect_viewer'):
                    page = page.replace('<body>', '<body data-session-view="true">').replace('id="login"', 'id="login" hidden')
                return self.send(200, page, True)
            if path == '/favicon.ico': return self.send(204, '', content_type='image/x-icon')
            if path in ('/dashboard.js', '/dashboard.css', '/i18n.js'):
                mime = 'text/javascript' if path.endswith('.js') else 'text/css'
                return self.send(200, (ASSETS / path[1:]).read_text(encoding='utf-8'), content_type=mime + '; charset=utf-8')
            if path == '/api/session/status' and hasattr(service, 'connect_viewer'):
                return self.send(200, {'connected': self.authorized()})
            if not self.authorized(): return self.send(401, {"error": "authentication required"})
            try:
                if path == "/api/runs": return self.send(200, service.listing())
                if path == '/api/observe': return self.send(200, service.observe())
                parts = path.strip('/').split('/')
                if len(parts) == 3 and parts[:2] == ['api', 'observe']:
                    return self.send(200, service.observe_run(parts[2]))
                if len(parts) == 5 and parts[:2] == ['api', 'observe'] and parts[3] == 'logs':
                    return self.send(200, service.observe_log(parts[2], parts[4]))
                return self.send(404, {"error": "unknown endpoint"})
            except (checks.Rejected, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
                return self.send(400, {"error": observer.redact(str(exc), service.token)})
        def do_POST(self):
            if not self.local_request(): return self.send(403, {'error': 'local viewer origin required'})
            if self.path == '/api/session/connect' and hasattr(service, 'connect_viewer'):
                try:
                    cookie = service.connect_viewer(self.headers.get('X-Workflow-Connect', ''))
                    return self.send(200, {'connected': True}, cookie=cookie)
                except (checks.Rejected, OSError, ValueError, KeyError, TypeError):
                    return self.send(401, {'error': '连接入口已失效，请让 AI 重新打开会话面板。'})
            if self.path == '/api/session/disconnect' and hasattr(service, 'disconnect_browser'):
                return self.send(200, {'connected': False}, cookie=service.disconnect_browser(self.headers.get('Cookie', '')))
            if not self.authorized(): return self.send(401, {"error": "authentication required"})
            if self.path != "/api/events": return self.send(404, {"error": "unknown endpoint"})
            try:
                self.connection.settimeout(10)
                length = int(self.headers.get("Content-Length", "0"))
                checks.require(0 < length <= 65536, "invalid request size")
                value = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send(200, service.submit(value))
            except (checks.Rejected, OSError, ValueError, KeyError, TypeError) as exc:
                self.send(400, {"error": str(exc)})
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "serve", "status", "execute"):
        p = sub.add_parser(name)
        p.add_argument("--state-dir", required=True, type=Path)
        if name == "init": p.add_argument("--runs-root", required=True, type=Path)
        if name == "serve":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=8765)
        if name == "execute":
            p.add_argument("--job", required=True)
            p.add_argument("--generation", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        root = args.state_dir.resolve()
        if args.command == "init":
            initialize(root, args.runs_root.resolve())
            print(json.dumps({"state_dir": str(root), "token_file": str(root / "auth.token")}))
            return 0
        service = Service(root)
        if args.command == "status":
            print(json.dumps(service.listing(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "execute": return execute(service, args.job, args.generation)
        with service_owner(root):
            service.recover_jobs()
            server = ThreadingHTTPServer((args.host, args.port), handler(service))
            worker = threading.Thread(target=service.loop, daemon=True)
            worker.start()
            try: server.serve_forever(poll_interval=0.5)
            finally:
                with service.condition:
                    service.stopping = True
                    service.condition.notify_all()
                server.server_close()
        return 0
    except (checks.Rejected, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__": raise SystemExit(main())
