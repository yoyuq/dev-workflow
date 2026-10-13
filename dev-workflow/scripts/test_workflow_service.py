import json
import os
from pathlib import Path
import tempfile
import threading
import shutil
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import check_night_run as checks
import workflow_service as service
import workflow_coordinator as coord
from workflow_bus import Bus
from test_workflow_coordinator import fixture
from test_workflow_replan import repin_fixture


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.runs = self.base / "runs"
        self.runs.mkdir()
        self.plan, self.run = fixture(self.runs)
        self.plan["skill_root"] = str(coord.ROOT)
        repin_fixture(self.run, self.plan)
        self.control = self.base / "control"
        service.initialize(self.control, self.runs)
        self.service = service.Service(self.control)

    def tearDown(self): self.tmp.cleanup()

    def submit(self): return self.service.submit({"id": "event-1", "run_dir": str(self.run)})

    def test_event_runs_in_child_and_duplicate_does_not_rerun(self):
        self.submit()
        self.assertTrue(self.service.dispatch_once())
        self.assertEqual(self.service.listing()[0]["status"], "completed")
        attempts = Bus(self.run).state()["tasks"]["A"]["attempts"]
        self.assertTrue(self.submit()["duplicate"])
        self.assertFalse(self.service.dispatch_once())
        self.assertEqual(Bus(self.run).state()["tasks"]["A"]["attempts"], attempts)

    def test_restart_retains_queue_and_rejects_delayed_child_generation(self):
        self.submit()
        old = self.service.claim()
        fresh = service.Service(self.control)
        fresh.recover_jobs()
        with self.assertRaisesRegex(checks.Rejected, "stale"):
            service.execute(fresh, old["id"], old["generation"])
        self.assertTrue(fresh.dispatch_once())
        self.assertEqual(fresh.listing()[0]["status"], "completed")

    def test_live_controller_not_replaced(self):
        self.submit()
        job = self.service.claim()
        with self.service.db() as db:
            db.execute("UPDATE jobs SET status='running',pid=? WHERE id=?", (os.getpid(), job["id"]))
        self.service.recover_jobs()
        self.assertEqual(self.service.listing()[0]["status"], "recovery_needed")
        self.assertFalse(self.service.dispatch_once())

    def test_cancel_persists_across_restart(self):
        self.submit()
        self.service.submit({"id": "stop", "run_dir": str(self.run), "cancel": True})
        fresh = service.Service(self.control)
        fresh.recover_jobs()
        self.assertFalse(fresh.dispatch_once())
        self.assertTrue((self.run / "cancel.request").exists())

    def test_kernel_owner_lock_excludes_second_service(self):
        with service.service_owner(self.control):
            with self.assertRaisesRegex(checks.Rejected, "active owner"):
                with service.service_owner(self.control): pass
        with service.service_owner(self.control): pass

    def test_prelock_controller_death_has_no_unregistered_child(self):
        frozen = self.base / "frozen"
        shutil.copytree(coord.ROOT, frozen, ignore=shutil.ignore_patterns("__pycache__"))
        controller = frozen / "scripts/workflow_coordinator.py"
        text = controller.read_text(encoding="utf-8")
        needle = "def main(argv=None):\n"
        hook = needle + '''    if argv is None and 'run' in sys.argv:
        test_root = Path(sys.argv[sys.argv.index('--run-dir') + 1])
        (test_root / 'controller-pid.txt').write_text(str(os.getpid()))
        deadline = time.monotonic() + 20
        while not (test_root / 'release.flag').exists() and time.monotonic() < deadline:
            time.sleep(.05)
'''
        self.assertEqual(text.count(needle), 1)
        controller.write_text(text.replace(needle, hook), encoding="utf-8")
        self.plan["skill_root"] = str(frozen)
        repin_fixture(self.run, self.plan)
        self.submit()
        job = self.service.claim()
        proc = subprocess.Popen([sys.executable, "-B", service.__file__, "execute", "--state-dir", str(self.control),
            "--job", job["id"], "--generation", str(job["generation"])], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 15
            marker = self.run / "controller-pid.txt"
            while not marker.exists() and proc.poll() is None and time.monotonic() < deadline: time.sleep(.05)
            self.assertTrue(marker.exists())
            self.assertEqual(int(marker.read_text()), proc.pid)
            with self.service.db() as db:
                self.assertEqual(db.execute("SELECT pid FROM jobs WHERE id=?", (job["id"],)).fetchone()[0], proc.pid)
            self.assertFalse((self.run / "runner.lock").exists())
            proc.kill()
            proc.wait(timeout=5)
            self.service.recover_jobs()
            (self.run / "release.flag").write_text("resume known test run")
            self.assertTrue(self.service.dispatch_once())
            self.assertEqual(self.service.listing()[0]["status"], "completed")
            self.assertEqual(Bus(self.run).state()["tasks"]["A"]["attempts"], 1)
        finally:
            (self.run / "release.flag").write_text("cleanup")
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    def test_authenticated_http_and_path_allowlist(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), service.handler(self.service))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(url + "/api/runs")
            self.assertEqual(error.exception.code, 401)
            req = urllib.request.Request(url + "/api/runs", headers={"Authorization": "Bearer " + self.service.token})
            self.assertEqual(json.load(urllib.request.urlopen(req)), [])
            bad = {"id": "escape", "run_dir": str(self.base / "outside")}
            req = urllib.request.Request(url + "/api/events", data=json.dumps(bad).encode(), headers={"Authorization": "Bearer " + self.service.token})
            with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(req)
            self.assertEqual(error.exception.code, 400)
            self.assertEqual(self.service.listing(), [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__": unittest.main()
