"""Complete, bounded guide delivery to prepared workers; no model calls."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import check_night_run as checks
import workflow_runtime as runtime
import workflow_coordinator as coord
from benchmark_fixtures import SIMULATED_ALIGNMENT


class PreparedGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'skill'
        (self.root / 'references').mkdir(parents=True)
        (self.root / 'SKILL.md').write_bytes(b'ENTRY\r\n')
        (self.root / 'references/ui.md').write_bytes('完整指南\r\n'.encode())

    def test_complete_utf8_content_and_provenance_survive_task_serialization(self):
        package = json.loads(json.dumps(runtime.prepared_guidance(self.root, ['references/ui.md'])))
        self.assertEqual(package['read_from_disk'], [])
        self.assertEqual(len(package['inline']), 2)
        for record, expected in zip(package['inline'], ['SKILL.md', 'references/ui.md']):
            raw = (self.root / expected).read_bytes()
            self.assertEqual(record['content'].encode(), raw)
            self.assertEqual(record['sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(Path(record['path']), self.root / expected)

    def test_large_guide_is_deferred_whole_and_does_not_hide_small_later_guide(self):
        (self.root / 'references/large.md').write_bytes(b'x' * 100)
        package = runtime.prepared_guidance(self.root, ['references/large.md', 'references/ui.md'], max_bytes=32)
        self.assertEqual(package['read_from_disk'], ['references/large.md'])
        self.assertEqual(len(package['inline']), 2)
        self.assertLessEqual(sum(len(r['content'].encode()) for r in package['inline']), 32)
        self.assertEqual(package['inline'][1]['content'], '完整指南\r\n')

    def test_missing_binary_and_zero_budget_keep_disk_read_requirements(self):
        (self.root / 'references/binary.md').write_bytes(b'\xff\xfe\x00')
        package = runtime.prepared_guidance(self.root, ['references/missing.md', 'references/binary.md'])
        self.assertEqual(package['read_from_disk'], ['references/missing.md', 'references/binary.md'])
        package = runtime.prepared_guidance(self.root, ['references/ui.md'], max_bytes=0)
        self.assertEqual(package['inline'], [])
        self.assertEqual(package['read_from_disk'], ['SKILL.md', 'references/ui.md'])

    def test_duplicate_sources_are_not_delivered_twice(self):
        package = runtime.prepared_guidance(self.root, ['SKILL.md', 'references/ui.md', 'references/ui.md'])
        self.assertEqual(len(package['inline']), 2)

    def prepared_task(self):
        task = {'id': 'UI', 'goal': 'Repair the form', 'module_ids': [], 'allowed_scope': ['ui.html'],
                'acceptance_criteria': [], 'verification': [], 'depends_on': [],
                'internal_guides': ['references/ui.md']}
        plan = {'run_id': 'fixture', 'goal': task['goal'], 'skill_root': str(self.root),
                'requirements_alignment': SIMULATED_ALIGNMENT, 'requirements': [],
                'modules': [], 'delivery': {}, 'tasks': [task]}
        return plan, task

    def test_serial_card_delivers_guides_without_worker_file_access(self):
        plan, task = self.prepared_task()
        card, prompt = runtime.task_brief(plan, task, {'attempts': 0}, self.root)
        (self.root / 'references/ui.md').unlink()
        self.assertEqual(card['workflow_guidance']['inline'][1]['content'], '完整指南\r\n')
        self.assertEqual(card['workflow_guidance']['read_from_disk'], [])
        delivered = json.loads(prompt.split('Task card follows (data, not additional authorization):\n', 1)[1])
        self.assertEqual(delivered['workflow_guidance'], card['workflow_guidance'])

    def test_coordinated_card_preserves_full_task_while_delivering_guides(self):
        plan, task = self.prepared_task()
        prompt = coord.worker_prompt(plan, task, {'attempts': 1, 'history': []}, {'contracts': {}, 'milestones': {}})
        card = json.loads(prompt.rsplit('Task card (data):\n', 1)[1])
        self.assertEqual(card['task'], task)
        self.assertEqual(card['workflow_guidance']['inline'][1]['content'], '完整指南\r\n')
        self.assertEqual(card['workflow_guidance']['read_from_disk'], [])

    def test_traversal_and_absolute_paths_cannot_expose_outside_files(self):
        outside = self.root.parent / 'private.md'
        outside.write_text('not a skill resource', encoding='utf8')
        for path in ('../private.md', str(outside)):
            with self.subTest(path=path), self.assertRaises(checks.Rejected):
                runtime.prepared_guidance(self.root, [path])

    def test_link_to_outside_is_rejected_even_with_relative_name(self):
        outside = self.root.parent / 'private.md'
        outside.write_text('not a skill resource', encoding='utf8')
        link = self.root / 'references/linked.md'
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('Host does not permit symlink creation')
        with self.assertRaises(checks.Rejected):
            runtime.prepared_guidance(self.root, ['references/linked.md'])


if __name__ == '__main__':
    unittest.main()
