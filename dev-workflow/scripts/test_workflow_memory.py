"""State migration, evidence freshness, failed hypotheses and non-overwriting I/O."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

import workflow_memory as memory


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="memory-test-", dir=os.environ.get("WORKFLOW_TEST_TMP"))
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "state"
        self.root.mkdir()
        self.progress = {"goal": "Build feature", "task_id": "task-one", "plan": "../plan.md", "code_ref": "abc123 + uncommitted",
                         "summary": "Module planned", "next_action": "Implement T1", "status": "running"}
        self.item = {"id": "api-timeout", "status": "candidate", "summary": "API timeout", "context": "Project A / Python 3.14 / HTTP module",
                     "symptom": "Request times out", "attempts": ["Inspected failed log"], "lesson": "Check connection reuse hypothesis",
                     "tags": ["python", "api"], "code_ref": "abc123", "evidence": []}

    def evidence(self):
        path = self.root / "evidence" / "check.log"
        path.parent.mkdir(exist_ok=True)
        path.write_text("before: fail; after: passed targeted check", encoding="utf-8")
        return path

    def validated(self):
        self.evidence()
        return {**self.item, "status": "validated", "evidence": ["evidence/check.log"], "validation_note": "Reproduction fails before fix, passes after"}

    def test_checkpoint_update_keeps_history_and_next_action(self):
        memory.checkpoint(self.root, self.progress)
        memory.checkpoint(self.root, {**self.progress, "summary": "Implemented", "next_action": "Run integration", "status": "running"})
        self.assertEqual(memory.read_json(self.root / "checkpoint.json")["next_action"], "Run integration")
        old = memory.read_json(next((self.root / "history").glob("*.json")))
        self.assertEqual(old["summary"], "Module planned")

    def test_different_task_cannot_replace_checkpoint(self):
        memory.checkpoint(self.root, self.progress)
        with self.assertRaisesRegex(ValueError, "different task"):
            memory.checkpoint(self.root, {**self.progress, "task_id": "another"})
        self.assertEqual(memory.read_json(self.root / "checkpoint.json")["task_id"], "task-one")

    def test_migration_preserves_relative_evidence_and_progress(self):
        memory.checkpoint(self.root, self.progress)
        memory.lesson(self.root, self.validated())
        moved = self.base / "另一个 harness" / "state"
        shutil.copytree(self.root, moved)
        results = memory.recall(moved, "timeout", ["python"])
        self.assertTrue(results[0]["validation_evidence_intact"])
        self.assertTrue(results[0]["conclusion_requires_review"])
        self.assertEqual(memory.read_json(moved / "checkpoint.json")["task_id"], "task-one")

    def test_changed_evidence_invalidates_previous_verified_claim(self):
        memory.lesson(self.root, self.validated())
        (self.root / "evidence/check.log").write_text("now fails", encoding="utf-8")
        result = memory.recall(self.root)[0]
        self.assertFalse(result["validation_evidence_intact"])
        self.assertEqual(result["evidence"][0]["integrity"], "changed")

    def test_missing_evidence_does_not_look_verified(self):
        memory.lesson(self.root, self.validated())
        (self.root / "evidence/check.log").unlink()
        result = memory.recall(self.root)[0]
        self.assertFalse(result["validation_evidence_intact"])

    def test_validated_requires_evidence_and_explanation(self):
        for data in ({**self.item, "status": "validated"}, {**self.item, "status": "validated", "validation_note": "claim"}):
            with self.assertRaises(ValueError):
                memory.lesson(self.root, data)
        self.assertEqual(memory.records(self.root), [])

    def test_candidate_and_unresolved_do_not_become_validated(self):
        for status in ("candidate", "unresolved"):
            memory.lesson(self.root, {**self.item, "id": status, "status": status})
        results = memory.recall(self.root, "timeout")
        self.assertEqual({r["status"] for r in results}, {"candidate", "unresolved"})
        self.assertTrue(all(not r["validation_evidence_intact"] for r in results))

    def test_retired_record_hidden_from_recall_but_in_export_and_history(self):
        memory.lesson(self.root, self.item)
        memory.lesson(self.root, {**self.item, "status": "retired", "lesson": "Contradicted by later evidence"})
        self.assertEqual(memory.recall(self.root), [])
        target = self.root / "view.md"
        memory.export_markdown(self.root, target)
        self.assertIn("retired", target.read_text(encoding="utf-8"))
        self.assertEqual(len(list((self.root / "history").glob("*.json"))), 1)

    def test_tags_and_nonmatching_queries_do_not_return_unrelated_lessons(self):
        memory.lesson(self.root, self.item)
        self.assertEqual(memory.recall(self.root, "css"), [])
        self.assertEqual(memory.recall(self.root, tags=["javascript"]), [])
        self.assertEqual(len(memory.recall(self.root, "timeout", ["python"])), 1)

    def test_evidence_must_stay_within_state_directory(self):
        for path in ("../external.log", "C:/outside/log.txt", "/tmp/out", "evidence\\log.txt"):
            with self.assertRaises(ValueError):
                memory.lesson(self.root, {**self.item, "evidence": [path]})
        self.assertEqual(memory.records(self.root), [])

    def test_duplicate_or_malformed_evidence_is_rejected(self):
        for paths in (["a", "a"], [{}], "a"):
            with self.assertRaises(ValueError):
                memory.lesson(self.root, {**self.item, "evidence": paths})

    def test_invalid_id_cannot_change_other_files(self):
        with self.assertRaises(ValueError):
            memory.lesson(self.root, {**self.item, "id": "../checkpoint"})
        self.assertFalse((self.root / "checkpoint.json").exists())

    def test_state_cannot_be_inside_skill(self):
        with self.assertRaises(ValueError):
            memory.state_root(memory.SKILL_ROOT / "new-state")

    def test_held_lock_preserves_current_record(self):
        memory.checkpoint(self.root, self.progress)
        with memory.lock(self.root):
            with self.assertRaisesRegex(ValueError, "locked"):
                memory.checkpoint(self.root, {**self.progress, "summary": "must not save"})
        self.assertEqual(memory.read_json(self.root / "checkpoint.json")["summary"], "Module planned")

    def test_export_does_not_overwrite_existing_notes_or_evidence(self):
        existing = self.root / "notes.md"
        existing.write_text("user notes", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            memory.export_markdown(self.root, existing)
        with self.assertRaises(ValueError):
            memory.export_markdown(self.root, self.root / "evidence/report.md")
        self.assertEqual(existing.read_text(), "user notes")

    def test_cli_persists_checkpoint_from_utf8_bom_json(self):
        source = self.base / "input.json"
        source.write_text(json.dumps(self.progress), encoding="utf-8-sig")
        self.assertEqual(memory.main(["--state-dir", str(self.root), "checkpoint", "--input", str(source)]), 0)
        self.assertEqual(memory.read_json(self.root / "checkpoint.json")["task_id"], "task-one")

    def test_project_memory_does_not_leak_to_other_project(self):
        memory.lesson(self.root, {**self.item, "scope": {"kind": "project", "match": {"project": "A"}}})
        self.assertEqual(len(memory.recall(self.root, context={"project": "A"})), 1)
        self.assertEqual(memory.recall(self.root, context={"project": "B"}), [])

    def test_environment_memory_requires_matching_version(self):
        memory.lesson(self.root, {**self.item, "scope": {"kind": "environment", "match": {"harness": "claude", "harness_version": "v1"}}})
        self.assertEqual(memory.recall(self.root, context={"harness": "codex", "harness_version": "v1"}), [])
        self.assertEqual(memory.recall(self.root, context={"harness": "claude", "harness_version": "v2"}), [])
        self.assertEqual(len(memory.recall(self.root, context={"harness": "claude", "harness_version": "v1"})), 1)

    def test_expired_memory_is_auditable_but_not_recalled(self):
        memory.lesson(self.root, {**self.item, "expires_at": "2000-01-01T00:00:00Z"})
        self.assertEqual(memory.recall(self.root), [])
        self.assertTrue(memory.records(self.root)[0]["expired"])

    def test_legacy_unscoped_memory_requires_explicit_rescoping(self):
        memory.lesson(self.root, self.item)
        self.assertEqual(len(memory.recall(self.root)), 1)
        self.assertEqual(memory.recall(self.root, context={"project": "A"}), [])

    def record_feedback(self, outcome):
        self.evidence()
        return memory.feedback(self.root, {"lesson_id": self.item["id"], "outcome": outcome,
            "note": "Observed in a subsequent run", "code_ref": "def456", "evidence": ["evidence/check.log"]})

    def test_contradiction_removes_recommendation_and_preserves_history(self):
        memory.lesson(self.root, {**self.validated(), "scope": {"kind": "general", "match": {}}})
        self.record_feedback("contradicted")
        self.assertEqual(memory.recall(self.root), [])
        item = memory.records(self.root)[0]
        self.assertTrue(item["needs_review"])
        self.assertFalse(item["recommendation_eligible"])
        self.assertEqual(len(list((self.root / "history").glob("*.json"))), 1)
        # Updating prose must not silently erase contradictory evidence.
        memory.lesson(self.root, self.validated())
        self.assertEqual(memory.recall(self.root), [])

    def test_helpful_feedback_does_not_promote_candidate_to_validated(self):
        memory.lesson(self.root, self.item)
        self.record_feedback("helped")
        item = memory.records(self.root)[0]
        self.assertEqual(item["status"], "candidate")
        self.assertFalse(item["recommendation_eligible"])

    def test_changed_feedback_evidence_prevents_contextual_recommendation(self):
        memory.lesson(self.root, {**self.validated(), "scope": {"kind": "general", "match": {}}})
        self.record_feedback("helped")
        (self.root / "evidence/check.log").write_text("different evidence", encoding="utf-8")
        self.assertEqual(memory.recall(self.root, context={}), [])

    def test_malformed_scope_and_naive_expiry_are_rejected(self):
        for extra in ({"scope": {"kind": "project", "match": {}}}, {"scope": {"kind": "environment", "match": {}}},
                      {"scope": {"kind": "general", "match": {"project": "A"}}}, {"expires_at": "2026-01-01"}):
            with self.assertRaises(ValueError):
                memory.lesson(self.root, {**self.item, **extra})


if __name__ == "__main__":
    unittest.main()
