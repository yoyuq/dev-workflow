"""Portable installs, bundled entry points and original-layout runtime copies."""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import workflow_portable as portable


class PortableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="portable-test-", dir=os.environ.get("WORKFLOW_TEST_TMP"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_published_package_has_one_entry_and_eight_preserved_modules(self):
        info = portable.check_package()
        self.assertEqual(info["modules"], 8)
        self.assertEqual(info["version"], "2.17.2")

    def test_install_spaces_unicode_and_repeat_is_idempotent(self):
        dest = self.root / "项目 with spaces" / "dev-workflow"
        self.assertEqual(portable.copy_exact(portable.ROOT, dest)["status"], "installed")
        self.assertEqual(portable.copy_exact(portable.ROOT, dest)["status"], "already_identical")
        self.assertEqual(portable.check_package(dest), portable.check_package())

    def test_existing_changed_install_is_preserved(self):
        dest = self.root / "existing"
        dest.mkdir()
        (dest / "SKILL.md").write_text("user content", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different content"):
            portable.copy_exact(portable.ROOT, dest)
        self.assertEqual((dest / "SKILL.md").read_text(), "user content")

    def test_overlap_refused_without_creating_files(self):
        for dest in (portable.ROOT, portable.ROOT / "nested", portable.ROOT.parent):
            with self.assertRaisesRegex(ValueError, "overlap"):
                portable.ensure_destination(portable.ROOT, dest)

    def test_tamper_and_extra_files_are_detected(self):
        dest = self.root / "copy"
        portable.copy_exact(portable.ROOT, dest)
        skill = dest / "SKILL.md"
        before = skill.read_bytes()
        skill.write_bytes(before + b"\nchanged")
        with self.assertRaisesRegex(ValueError, "changed=.*SKILL.md"):
            portable.check_package(dest)
        skill.write_bytes(before)
        (dest / "extra.txt").write_text("extra", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "extra=.*extra.txt"):
            portable.check_package(dest)

    def test_module_runtime_layout_restores_entry_and_adjacent_resources(self):
        source = portable.ROOT / "references/modules/impeccable"
        dest = self.root / "runtime" / "impeccable"
        portable.copy_exact(source, dest, rename_entry=True)
        self.assertFalse((dest / "guide.md").exists())
        self.assertEqual((dest / "SKILL.md").read_bytes(), (source / "guide.md").read_bytes())
        self.assertEqual((dest / "scripts/impeccable.cmd").read_bytes(), (source / "scripts/impeccable.cmd").read_bytes())
        self.assertEqual(portable.copy_exact(source, dest, rename_entry=True)["status"], "already_identical")

    def test_codex_and_claude_project_paths(self):
        for harness, folder in (("codex", ".agents"), ("claude", ".claude")):
            args = argparse.Namespace(dest=None, harness=harness, user=False, project=str(self.root))
            self.assertEqual(portable.install_destination(args), self.root / folder / "skills/dev-workflow")

    def test_existing_legacy_codex_install_avoids_duplicate(self):
        (self.root / ".codex/skills/dev-workflow").mkdir(parents=True)
        with patch.object(Path, "home", return_value=self.root):
            args = argparse.Namespace(dest=None, harness="codex", user=True, project=None)
            with self.assertRaisesRegex(ValueError, "duplicate"):
                portable.install_destination(args)

    def test_explicit_destination_rejects_ambiguous_flags(self):
        args = argparse.Namespace(dest=str(self.root), harness="claude", user=True, project=None)
        with self.assertRaises(ValueError):
            portable.install_destination(args)

    def test_runtime_copy_cannot_be_prepared_in_discovery_folder(self):
        self.assertEqual(portable.main(["prepare-module", "grill-me", "--dest", str(self.root / ".claude/skills/grill-me")]), 2)
        self.assertFalse((self.root / ".claude").exists())

    def test_unknown_module_cannot_supply_path(self):
        self.assertEqual(portable.main(["prepare-module", "../other", "--dest", str(self.root / "run")]), 2)
        self.assertFalse((self.root / "run").exists())

    def test_index_is_an_independent_snapshot_check(self):
        dest = self.root / "index-test"
        portable.copy_exact(portable.ROOT, dest)
        manifest_file = dest / portable.MANIFEST
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        key = "references/modules/grill-me/guide.md"
        (dest / key).write_text("modified module", encoding="utf-8")
        manifest["files"][key] = portable.digest(dest / key)
        manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs from snapshot"):
            portable.check_package(dest)


if __name__ == "__main__":
    unittest.main()
