"""Durable coordinator-owned facts and acknowledged messages (Python stdlib)."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

import check_night_run as checks
import workflow_memory as memory
import workflow_protocol as protocol

MAILBOX = ".workflow-mailbox"
WORKER_TYPES = {"progress", "question", "reply", "blocked", "change_request", "artifact", "ack", "capabilities", "ready_for_verification"}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class Bus:
    def __init__(self, root):
        self.root = Path(root)
        self.db = self.root / "coordination.sqlite3"
        self._transaction = None

    @contextmanager
    def connection(self):
        if self._transaction is not None:
            yield self._transaction
            return
        conn = sqlite3.connect(self.db, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        if self._transaction is not None:
            yield
            return
        with self.connection() as db:
            self._transaction = db
            try:
                yield
            finally:
                self._transaction = None

    def initialize(self, state):
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE facts (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
                CREATE TABLE events (seq INTEGER PRIMARY KEY, at REAL NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE messages (seq INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE,
                    sender TEXT NOT NULL, recipient TEXT NOT NULL, kind TEXT NOT NULL,
                    payload TEXT NOT NULL, required INTEGER NOT NULL, acked INTEGER NOT NULL DEFAULT 0,
                    at REAL NOT NULL, resolved INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE receipts (id TEXT PRIMARY KEY, digest TEXT NOT NULL);
                CREATE TABLE processes (id TEXT PRIMARY KEY, pid INTEGER NOT NULL, finished INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE human_responses (seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL, status TEXT NOT NULL, result TEXT);
            ''')
            db.execute("INSERT INTO facts VALUES(1, ?)", (encoded(state),))

    def process_started(self, key, pid):
        with self.connection() as db:
            db.execute("INSERT INTO processes(id,pid) VALUES(?,?)", (key, pid))

    def process_finished(self, key):
        with self.connection() as db:
            db.execute("UPDATE processes SET finished=1 WHERE id=?", (key,))

    def live_candidates(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM processes WHERE finished=0")]

    def state(self):
        with self.connection() as db:
            return json.loads(db.execute("SELECT value FROM facts WHERE id=1").fetchone()[0])

    def save(self, state, event, **data):
        with self.connection() as db:
            db.execute("UPDATE facts SET value=? WHERE id=1", (encoded(state),))
            db.execute("INSERT INTO events(at,kind,data) VALUES(?,?,?)", (time.time(), event, encoded(data)))

    def event(self, kind, **data):
        with self.connection() as db:
            db.execute("INSERT INTO events(at,kind,data) VALUES(?,?,?)", (time.time(), kind, encoded(data)))

    def send(self, recipient, kind, payload, *, sender="coordinator", required=False, message_id=None):
        mid = message_id or uuid.uuid4().hex
        with self.connection() as db:
            old = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
            if old:
                checks.require((old["sender"], old["recipient"], old["kind"], old["payload"], old["required"]) ==
                               (sender, recipient, kind, encoded(payload), int(required)), "message ID reused with different content")
                return mid
            db.execute("INSERT INTO messages(id,sender,recipient,kind,payload,required,at) VALUES(?,?,?,?,?,?,?)",
                       (mid, sender, recipient, kind, encoded(payload), int(required), time.time()))
        return mid

    def messages(self, recipient=None, unacked=False):
        where, values = [], []
        if recipient:
            where.append("recipient=?")
            values.append(recipient)
        if unacked:
            where.append("acked=0")
        with self.connection() as db:
            rows = db.execute("SELECT * FROM messages" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY seq", values)
            return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def ack(self, recipient, ids):
        with self.connection() as db:
            for mid in ids:
                row = db.execute("SELECT recipient FROM messages WHERE id=?", (mid,)).fetchone()
                checks.require(row and row[0] == recipient, "cannot acknowledge someone else's message")
                db.execute("UPDATE messages SET acked=1 WHERE id=?", (mid,))

    def resolve(self, ids):
        with self.connection() as db:
            for mid in ids:
                db.execute("UPDATE messages SET resolved=1 WHERE id=?", (mid,))
                row = db.execute("SELECT payload FROM messages WHERE id=?", (mid,)).fetchone()
                if row:
                    original = json.loads(row[0]).get("original_request")
                    if original:
                        db.execute("UPDATE messages SET resolved=1 WHERE id=?", (original,))
                # A late peer reply also closes an escalation of the same request.
                db.execute("UPDATE messages SET resolved=1,acked=1 WHERE id=?", ("escalated-" + mid,))

    def unresolved(self):
        return [m for m in self.messages() if m["kind"] in {"question", "blocked", "change_request"} and not m["resolved"]]

    def ingest(self, tid, attempt, workspace, task_ids):
        """Only the controller reads outbox files; file placement establishes sender identity."""
        mailbox = Path(workspace) / MAILBOX
        checks.require(not mailbox.is_symlink(), "mailbox must not be a symlink")
        outbox = mailbox / "outbox"
        if not outbox.exists():
            return []
        checks.require(not outbox.is_symlink(), "outbox must not be a symlink")
        accepted = []
        paths = sorted(outbox.glob("*.json"))
        checks.require(len(paths) <= 500, "worker mailbox message limit exceeded")
        for path in paths:
            checks.require(not path.is_symlink() and path.stat().st_size <= 16384, "invalid message file")
            raw = checks.read_json(path)
            checks.require(isinstance(raw, dict), "message must be an object")
            kind, recipient = raw.get("type"), raw.get("to", "coordinator")
            checks.require(kind in WORKER_TYPES and recipient in {*task_ids, "coordinator"}, "invalid message type/recipient")
            mid = raw.get("id")
            checks.identifier(mid, "message.id")
            key = f"{tid}/{attempt}/{mid}"
            digest = hashlib.sha256(encoded(raw).encode()).hexdigest()
            with self.connection() as db:
                old = db.execute("SELECT digest FROM receipts WHERE id=?", (key,)).fetchone()
                if old:
                    checks.require(old[0] == digest, "worker rewrote an already delivered message")
                    continue
                if kind == "ack":
                    ids = raw.get("ids")
                    checks.require(isinstance(ids, list) and len(ids) <= 500, "invalid acknowledgements")
                    for message_id in ids:
                        row = db.execute("SELECT recipient FROM messages WHERE id=?", (message_id,)).fetchone()
                        checks.require(row and row[0] == tid, "acknowledgement belongs to another task")
                        db.execute("UPDATE messages SET acked=1 WHERE id=?", (message_id,))
                else:
                    payload = {"text": raw.get("text", ""), "data": raw.get("data", {}),
                               "reply_to": raw.get("reply_to"), "attempt": attempt}
                    checks.require(isinstance(payload["text"], str) and len(payload["text"]) <= 8000,
                                   "message text too large")
                    protocol.validate_message(kind, recipient, payload["data"])
                    if kind == "reply":
                        question = db.execute("SELECT * FROM messages WHERE id=?", (payload["reply_to"],)).fetchone()
                        checks.require(question and question["recipient"] == tid and question["sender"] == recipient and
                                       question["kind"] == "question", "reply must reference a question addressed to this task")
                        db.execute("UPDATE messages SET resolved=1 WHERE id=?", (payload["reply_to"],))
                        db.execute("UPDATE messages SET resolved=1,acked=1 WHERE id=?", ("escalated-" + payload["reply_to"],))
                    # Reports and requests never mutate task completion or shared contracts.
                    db.execute("INSERT INTO messages(id,sender,recipient,kind,payload,required,at) VALUES(?,?,?,?,?,?,?)",
                               (key, tid, recipient, kind, encoded(payload), int(kind == "reply"), time.time()))
                db.execute("INSERT INTO receipts VALUES(?,?)", (key, digest))
                db.execute("INSERT INTO events(at,kind,data) VALUES(?,?,?)",
                           (time.time(), "message_received", encoded({"task": tid, "type": kind, "id": key, "to": recipient})))
            if kind != "ack":
                accepted.append({"id": key, "sender": tid, "recipient": recipient, "kind": kind, "payload": payload})
        return accepted

    def deliver(self, tid, workspace, shared):
        folder = Path(workspace) / MAILBOX
        checks.require(not folder.is_symlink(), "mailbox link refused")
        folder.mkdir(exist_ok=True)
        (folder / "outbox").mkdir(exist_ok=True)
        # The DB remains authoritative if a worker tampers with its own inbox copy.
        target = folder / "inbox.json"
        checks.require(not target.is_symlink(), "inbox link refused")
        value = {"task_id": tid, "messages": self.messages(tid, unacked=True), "shared": shared}
        if not target.exists() or checks.read_json(target) != value:
            memory.atomic_json(target, value)

    def export(self):
        with self.connection() as db:
            events = [{**dict(row), "data": json.loads(row["data"])} for row in db.execute("SELECT * FROM events ORDER BY seq")]
        memory.atomic_json(self.root / "state.json", self.state())
        memory.atomic_json(self.root / "messages.json", self.messages())
        memory.atomic_json(self.root / "events.json", events)

