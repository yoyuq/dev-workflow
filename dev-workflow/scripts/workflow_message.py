#!/usr/bin/env python3
"""Worker-side mailbox client. Never opens the coordinator database."""
import argparse
import json
from pathlib import Path
import time
import uuid

import workflow_memory as memory
from workflow_bus import MAILBOX, WORKER_TYPES


def post(workspace, kind, to="coordinator", text="", data=None, reply_to=None, ids=None):
    folder = Path(workspace) / MAILBOX / "outbox"
    if not folder.is_dir() or folder.is_symlink():
        raise ValueError("No controller-prepared mailbox in this workspace")
    mid = uuid.uuid4().hex
    value = {"id": mid, "type": kind, "to": to, "text": text, "data": data or {}, "reply_to": reply_to}
    if ids is not None:
        value["ids"] = ids
    memory.atomic_json(folder / (mid + ".json"), value)
    return value


def read(workspace, acknowledge=False):
    value = json.loads((Path(workspace) / MAILBOX / "inbox.json").read_text(encoding="utf-8"))
    if acknowledge and value["messages"]:
        post(workspace, "ack", ids=[m["id"] for m in value["messages"]])
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("read", "wait"):
        p = commands.add_parser(name)
        p.add_argument("--ack", action="store_true")
        if name == "wait":
            p.add_argument("--seconds", type=int, default=30)
    p = commands.add_parser("post")
    p.add_argument("--type", choices=sorted(WORKER_TYPES - {"ack"}), required=True)
    p.add_argument("--to", default="coordinator")
    p.add_argument("--text", required=True)
    p.add_argument("--data-file", type=Path)
    p.add_argument("--reply-to")
    args = parser.parse_args(argv)
    if args.command == "post":
        data = json.loads(args.data_file.read_text(encoding="utf-8")) if args.data_file else {}
        result = post(args.workspace, args.type, args.to, args.text, data, args.reply_to)
    else:
        if args.command == "wait":
            deadline = time.monotonic() + max(0, min(args.seconds, 60))
            while not read(args.workspace)["messages"] and time.monotonic() < deadline:
                time.sleep(0.25)
        result = read(args.workspace, args.ack)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
