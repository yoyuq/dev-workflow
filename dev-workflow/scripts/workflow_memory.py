#!/usr/bin/env python3
"""Portable checkpoints and evidence-linked lessons, without external dependencies."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

SKILL_ROOT = Path(__file__).resolve().parents[1]
STATUSES = {"unresolved", "candidate", "validated", "retired"}
PROGRESS = {"running", "blocked", "awaiting_review", "completed", "timeboxed"}


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def timestamp(value: str):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed


def validate_scope(data):
    scope = data.get("scope")
    if scope is not None:
        if not isinstance(scope, dict) or scope.get("kind") not in {"project", "environment", "general"}:
            raise ValueError("scope.kind must be project, environment or general")
        match = scope.get("match", {})
        if not isinstance(match, dict) or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in match.items()):
            raise ValueError("scope.match must contain nonempty string values")
        if scope["kind"] == "project" and not match.get("project"):
            raise ValueError("project scope needs match.project")
        if scope["kind"] == "environment" and not match:
            raise ValueError("environment scope needs matching context")
        if scope["kind"] == "general" and match:
            raise ValueError("general scope must not contain context filters")
    if data.get("expires_at"):
        timestamp(data["expires_at"])


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def state_root(path: Path) -> Path:
    raw = path.expanduser().absolute()
    for ancestor in (raw, *raw.parents):
        if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
            raise ValueError("State directory must not traverse links/junctions")
    root = raw.resolve()
    if root == SKILL_ROOT or SKILL_ROOT in root.parents:
        raise ValueError("Keep project memory outside the Skill package")
    return root


def within(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or "\\" in relative:
        raise ValueError("Use nonempty relative paths with forward slashes")
    if re.match(r"^[A-Za-z]:", relative) or ".." in Path(relative).parts:
        raise ValueError("Evidence/state paths must stay inside state-dir")
    candidate = root / relative
    for ancestor in (candidate, *candidate.parents):
        if ancestor == root:
            break
        if ancestor.is_symlink() or (hasattr(ancestor, "is_junction") and ancestor.is_junction()):
            raise ValueError("State/evidence paths must not use links")
    resolved = candidate.resolve()
    if root not in resolved.parents:
        raise ValueError("Path escapes state-dir")
    return resolved


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_text(data: dict, fields: tuple) -> None:
    for key in fields:
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Nonempty string required: {key}")


@contextmanager
def lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".write.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("State writer locked; check whether another writer is active before removing a stale lock") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def save(root: Path, relative: str, data: dict) -> dict:
    with lock(root):
        target = within(root, relative)
        if target.exists():
            old = read_json(target)
            if relative == "checkpoint.json" and old.get("task_id") != data["task_id"]:
                raise ValueError("This state directory belongs to a different task; choose a separate directory")
            history = within(root, f"history/{target.stem}-{uuid.uuid4().hex}.json")
            atomic_json(history, old)
            if relative.startswith("lessons/"):
                data = {**data, "feedback": old.get("feedback", [])}
        data = {**data, "recorded_at": stamp(), "format_version": 1}
        atomic_json(target, data)
    return data


def checkpoint(root: Path, data: dict) -> dict:
    require_text(data, ("goal", "task_id", "plan", "code_ref", "summary", "next_action", "status"))
    if data["status"] not in PROGRESS:
        raise ValueError("Unknown checkpoint status")
    # This record is model-authored context, never independent acceptance evidence.
    return save(root, "checkpoint.json", data)


def lesson(root: Path, data: dict) -> dict:
    require_text(data, ("id", "status", "summary", "context", "symptom", "lesson", "code_ref"))
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", data["id"]):
        raise ValueError("Lesson id must contain 1-80 lowercase letters, digits or hyphens")
    if data["status"] not in STATUSES:
        raise ValueError("Unknown lesson status")
    validate_scope(data)
    if "feedback" in data:
        raise ValueError("Use the feedback command to record lesson use")
    for field in ("tags", "attempts"):
        if not isinstance(data.get(field), list) or not all(isinstance(x, str) and x.strip() for x in data[field]):
            raise ValueError(f"Expected list of nonempty strings: {field}")
    paths = data.get("evidence", [])
    if not isinstance(paths, list) or len(paths) != len(set(p for p in paths if isinstance(p, str))):
        raise ValueError("Evidence must be a list of unique relative path strings")
    if data["status"] == "validated":
        require_text(data, ("validation_note",))
        if not paths:
            raise ValueError("Validated lessons need evidence; file hashes do not validate the conclusion")
    evidence = []
    for path in paths:
        evidence.append({"path": path, "sha256": sha(within(root, path))})
    return save(root, f"lessons/{data['id']}.json", {**data, "evidence": evidence})


def feedback(root: Path, data: dict) -> dict:
    require_text(data, ("lesson_id", "outcome", "note", "code_ref"))
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", data["lesson_id"]):
        raise ValueError("Invalid lesson_id")
    if data["outcome"] not in {"helped", "not_applicable", "contradicted"}:
        raise ValueError("Unknown feedback outcome")
    paths = data.get("evidence", [])
    if not isinstance(paths, list) or not paths or not all(isinstance(p, str) for p in paths) or len(set(paths)) != len(paths):
        raise ValueError("Feedback needs unique relative evidence paths")
    item = {**data, "recorded_at": stamp(), "evidence": [{"path": p, "sha256": sha(within(root, p))} for p in paths]}
    with lock(root):
        target = within(root, f"lessons/{data['lesson_id']}.json")
        old = read_json(target)
        atomic_json(within(root, f"history/{target.stem}-{uuid.uuid4().hex}.json"), old)
        updated = {**old, "feedback": [*old.get("feedback", []), item], "recorded_at": stamp()}
        atomic_json(target, updated)
    return updated


def integrity(root: Path, data: dict) -> list[dict]:
    evidence = []
    for entry in data.get("evidence", []):
        try:
            match = sha(within(root, entry["path"])) == entry["sha256"]
            status = "matches" if match else "changed"
        except (OSError, ValueError, KeyError, TypeError):
            status = "missing_or_invalid"
        evidence.append({**entry, "integrity": status})
    return evidence


def records(root: Path) -> list[dict]:
    folder = within(root, "lessons")
    result = []
    for path in sorted(folder.glob("*.json")):
        data = read_json(within(root, "lessons/" + path.name))
        evidence = integrity(root, data)
        uses = [{**item, "evidence": integrity(root, item)} for item in data.get("feedback", [])]
        contradicted = any(item["outcome"] == "contradicted" for item in uses)
        drift = any(e["integrity"] != "matches" for e in evidence) or any(e["integrity"] != "matches" for item in uses for e in item["evidence"])
        expired = bool(data.get("expires_at") and timestamp(data["expires_at"]) <= datetime.now(timezone.utc))
        needs_review = contradicted or drift or expired
        result.append({**data, "evidence": evidence,
                       "validation_evidence_intact": data.get("status") == "validated" and bool(evidence) and all(e["integrity"] == "matches" for e in evidence),
                       "feedback": uses, "expired": expired, "needs_review": needs_review,
                       "recommendation_eligible": data.get("status") == "validated" and bool(evidence) and not needs_review,
                       "conclusion_requires_review": True})
    return result


def recall(root: Path, query: str = "", tags: list[str] | None = None, limit: int = 5, context: dict | None = None) -> list[dict]:
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    terms = query.casefold().split()
    matches = []
    for record in records(root):
        if record.get("status") == "retired" or not set(tags or []).issubset(record.get("tags", [])):
            continue
        if record["expired"] or any(f["outcome"] == "contradicted" for f in record["feedback"]):
            continue
        if context is not None:
            scope = record.get("scope")
            if not scope or any(context.get(k) != v for k, v in scope.get("match", {}).items()):
                continue
            # Evidence drift is visible in audits, but never injected as advice.
            if record["needs_review"]:
                continue
        haystack = " ".join(str(record.get(k, "")) for k in ("summary", "context", "symptom", "lesson", "tags", "attempts")).casefold()
        score = sum(term in haystack for term in terms)
        if terms and score == 0:
            continue
        matches.append((score, record.get("recorded_at", ""), record))
    return [entry[2] for entry in sorted(matches, key=lambda x: (x[0], x[1]), reverse=True)[:limit]]


def export_markdown(root: Path, output: Path) -> None:
    target = output.expanduser().resolve()
    if target.suffix.lower() != ".md" or target == SKILL_ROOT or SKILL_ROOT in target.parents:
        raise ValueError("Export to a Markdown file outside the Skill package")
    if root in target.parents and target.parts[len(root.parts)] in {"lessons", "history", "evidence"}:
        raise ValueError("Export must not overwrite state evidence or source records")
    if output.is_symlink():
        raise ValueError("Export target must not be a symlink")
    lines = ["# 工作进度与错题本", "", "这是源记录的导出视图。证据摘要只证明文件一致，经验结论仍需核实。", ""]
    current = within(root, "checkpoint.json")
    if current.exists():
        lines.extend(["## 当前检查点", "", "```json", json.dumps(read_json(current), ensure_ascii=False, indent=2), "```", ""])
    for record in records(root):
        lines.extend([f"## {record['id']} — {record['status']}", "", "```json", json.dumps(record, ensure_ascii=False, indent=2), "```", ""])
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exports are views; require a new filename to avoid overwriting unrelated notes.
    with target.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("checkpoint", "lesson", "feedback"):
        commands.add_parser(command).add_argument("--input", required=True)
    search = commands.add_parser("recall")
    search.add_argument("--query", default="")
    search.add_argument("--tag", action="append", default=[])
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--context", type=Path, help="JSON context used to match scoped lessons")
    export = commands.add_parser("export")
    export.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        root = state_root(Path(args.state_dir))
        if args.command in {"checkpoint", "lesson", "feedback"}:
            value = globals()[args.command](root, read_json(Path(args.input)))
        elif args.command == "recall":
            value = recall(root, args.query, args.tag, args.limit, read_json(args.context) if args.context else None)
        else:
            export_markdown(root, Path(args.output))
            value = {"exported": str(Path(args.output).resolve())}
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
