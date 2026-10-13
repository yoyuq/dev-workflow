#!/usr/bin/env python3
"""Offline integrity checks and non-overwriting installation. Python 3.10+."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "package-manifest.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root: Path) -> dict[str, str]:
    result = {}
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Expected a real directory: {root}")
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError(f"Linked files/directories are not supported: {path}")
        if "__pycache__" in path.relative_to(root).parts:
            continue
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path)
    return result


def check_package(root: Path = ROOT) -> dict:
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    actual = inventory(root)
    actual.pop(MANIFEST, None)
    expected = manifest["files"]
    if actual != expected:
        missing = sorted(expected.keys() - actual.keys())
        extra = sorted(actual.keys() - expected.keys())
        changed = sorted(k for k in actual.keys() & expected.keys() if actual[k] != expected[k])
        raise ValueError(f"Package integrity mismatch: missing={missing}, extra={extra}, changed={changed}")
    entries = [k for k in actual if Path(k).name.lower() == "skill.md"]
    if entries != ["SKILL.md"]:
        raise ValueError(f"Expected exactly one Skill entry, found: {entries}")
    modules = json.loads((root / "module-index.json").read_text(encoding="utf-8"))["modules"]
    for module in modules:
        base = Path(module["entry"]).parent
        for entry in module["files"]:
            key = (base / entry["path"]).as_posix()
            if actual.get(key) != entry["sha256"]:
                raise ValueError(f"Bundled module differs from snapshot: {key}")
    return {"version": manifest["version"], "file_count": len(actual), "modules": len(modules)}


def ensure_destination(source: Path, destination: Path) -> Path:
    # Check each existing ancestor before resolving, to avoid following a junction.
    raw = destination.expanduser().absolute()
    for parent in (raw, *raw.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise ValueError(f"Destination contains a link/junction: {parent}")
    dest = raw.resolve()
    source = source.resolve()
    if dest == source or source in dest.parents or dest in source.parents:
        raise ValueError("Source and destination must not overlap")
    return dest


def copy_exact(source: Path, destination: Path, rename_entry: bool = False) -> dict:
    dest = ensure_destination(source, destination)
    expected = inventory(source)
    if rename_entry:
        expected["SKILL.md"] = expected.pop("guide.md")
    if dest.exists():
        if dest.is_dir() and inventory(dest) == expected:
            return {"destination": str(dest), "status": "already_identical"}
        raise ValueError(f"Destination exists with different content; back it up explicitly: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Claim a new directory exclusively; never merge into or delete user content.
    dest.mkdir()
    try:
        for relative in sorted(expected):
            source_name = "guide.md" if rename_entry and relative == "SKILL.md" else relative
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / source_name, target)
        if inventory(dest) != expected:
            raise ValueError("Destination verification failed")
    except Exception as exc:
        raise ValueError(f"Copy incomplete; partial directory retained at {dest}: {exc}") from exc
    return {"destination": str(dest), "status": "installed"}


def install_destination(args) -> Path:
    if args.dest:
        if args.harness or args.user or args.project:
            raise ValueError("--dest cannot be combined with --harness/--user/--project")
        return Path(args.dest)
    if not args.harness or bool(args.user) == bool(args.project):
        raise ValueError("Use --dest, or --harness with exactly one of --user/--project")
    base = Path.home() if args.user else Path(args.project).expanduser()
    folder = ".agents" if args.harness == "codex" else ".claude"
    dest = base / folder / "skills" / "dev-workflow"
    if args.harness == "codex" and args.user:
        legacy = base / ".codex" / "skills" / "dev-workflow"
        if legacy.exists():
            raise ValueError(f"Existing Codex installation at {legacy}; upgrade it explicitly instead of creating a duplicate")
    return dest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    install = commands.add_parser("install")
    install.add_argument("--dest")
    install.add_argument("--harness", choices=("codex", "claude"))
    install.add_argument("--user", action="store_true")
    install.add_argument("--project")
    module = commands.add_parser("prepare-module")
    module.add_argument("module")
    module.add_argument("--dest", required=True)
    args = parser.parse_args(argv)
    try:
        info = check_package()
        if args.command == "doctor":
            result = {"integrity": "passed", **info, "python": sys.version.split()[0],
                      "programs_on_path": {name: shutil.which(name) is not None for name in ("git", "node", "npx", "codex", "claude")},
                      "runtime_verified": False,
                      "note": "No network calls. PATH presence does not verify login, browser, sandbox or project readiness."}
        elif args.command == "install":
            result = copy_exact(ROOT, install_destination(args))
        else:
            modules = json.loads((ROOT / "module-index.json").read_text(encoding="utf-8"))["modules"]
            selected = next((m for m in modules if m["id"] == args.module), None)
            if selected is None:
                raise ValueError(f"Unknown bundled module: {args.module}")
            dest = ensure_destination(ROOT, Path(args.dest))
            if "skills" in (p.lower() for p in dest.parts):
                raise ValueError("Prepare runtime modules outside any skills discovery directory")
            result = copy_exact((ROOT / selected["entry"]).parent, dest, rename_entry=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
