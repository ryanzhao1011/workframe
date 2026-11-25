#!/usr/bin/env python3
"""Reading project evidence safely, against isolated workspace fixtures."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/core/scripts'))
import _selfiter_candidates as collector
import _selfiter_evidence as evidence
import _selfiter_policy as policy
from _state_io import memory_dir_of, state_dir_of


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / 'tmp'
        scratch.mkdir(exist_ok=True)
        self.fixture = tempfile.TemporaryDirectory(prefix='selfiter-evidence-', dir=scratch)
        self.addCleanup(self.fixture.cleanup)
        self.project = Path(self.fixture.name) / 'project'
        self.project.mkdir()
        self.state = state_dir_of(self.project)
        self.memory = memory_dir_of(self.project)
        (self.memory / 'dev').mkdir(parents=True)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='')

    def events(self, rows, filename='events.jsonl'):
        self.write(self.state / filename, '\n'.join(json.dumps(x) for x in rows) + '\n')

    def correction(self, n=1, **kwargs):
        return dict({'type': 'user_correction', 'ts': f'2026-11-{n:02d}T00:00:00Z',
                     'scope': 'dev', 'summary': 'Preserve original bytes',
                     'entry_key': f'dev:2026-11-{n:02d}:Preserve'}, **kwargs)

    def collect(self, consumed_ids=()):
        return collector.collect_candidates(self.project, {'consumed_ids': list(consumed_ids)})

    def ids(self, **kwargs):
        return sorted(c['id'] for c in self.collect(**kwargs)['candidates'])

    def test_main_and_spill_dedup_consumed_and_job_events(self):
        a, b = self.correction(), self.correction(2)
        self.events([a])
        self.events([a], 'events.123.spill.jsonl')
        self.assertEqual(self.ids(), [policy.event_id(a)])
        self.events([a, dict(a, job_id='self'), {'type': 'skill_used'}])
        self.events([a, b], 'events.123.spill.jsonl')
        self.events([self.correction(3)], 'events.123.spill.jsonl.merged.old')
        both = sorted([policy.event_id(a), policy.event_id(b)])
        self.assertEqual(self.ids(), both)
        self.assertEqual(self.ids(consumed_ids=both[:1]), both[1:])
        self.assertEqual(self.ids(consumed_ids=both), [])
        self.events([a, b, self.correction(4)])
        self.assertEqual(self.ids(consumed_ids=both), [policy.event_id(self.correction(4))])

    def test_notes_content_identity_survives_line_changes_but_detects_changed_block(self):
        notes = self.memory / 'dev/notes.md'
        text = '### [亲测] bytes\n' + 'preserve bytes\n' * 80
        self.write(notes, text)
        row, = self.collect()['candidates']
        self.assertEqual(row['provenance'], 'self_tested_claim')
        self.write(notes, ('# new heading\n\n' + text).replace('\n', '\r\n'))
        self.assertEqual(self.ids(consumed_ids=[row['id']]), [])
        self.write(notes, text.replace('preserve bytes', 'preserve raw bytes', 1))
        self.assertEqual(len(self.ids(consumed_ids=[row['id']])), 1)

    def test_dismissed_cards_and_broken_state_do_not_filter_evidence(self):
        rows = [self.correction(), self.correction(2), self.correction(3)]
        wanted = sorted(policy.event_id(r) for r in rows)
        self.events(rows)
        for text in (json.dumps({'pending_maintenance': [
                {'kind': 'problem_threshold', 'dedup_key': 'problem_threshold', 'status': status,
                 'closed_at': '2026-11-04T00:00:00Z'}]}) for status in ('closed', 'retired', 'open')):
            self.write(self.state / 'activity-state.json', text)
            self.assertEqual(self.ids(), wanted)
        self.write(self.state / 'activity-state.json', '{broken')
        self.assertEqual(self.ids(), wanted)
        self.assertEqual(self.collect()['diagnostics'], [])

    def test_context_is_full_hashes_are_bytes_and_missing_targets_explicit(self):
        self.events([self.correction()])
        full = '# memory\r\n' + 'context\r\n' * 600
        self.write(self.memory / 'dev/MEMORY.md', full)
        self.write(self.memory / 'shared/MEMORY.md', '# shared authority\n')
        self.write(self.memory / 'dev/notes-archive.md', '# archive\nold\n')
        bundle, _, _ = collector.build_input(self.collect(), 80000)
        rel = (self.memory / 'dev/MEMORY.md').relative_to(self.project).as_posix()
        self.assertEqual(bundle['sources'][rel], {'text': full, 'sha256': hashlib.sha256(full.encode()).hexdigest()})
        self.assertEqual(bundle['model']['targets'], {rel: full})
        self.assertEqual(len(bundle['sources']), 2, 'archives are not attached')
        self.assertEqual(bundle['evidence'][0]['context'][rel], bundle['sources'][rel]['sha256'])

    def test_module_context_only_related_full_docs_authorities_not_targets(self):
        base = 'projects/modules/basic/sub'
        authority = base + '/current-state/data-model.md'
        self.events([self.correction(scope='module', module='basic/sub', target_path=authority)])
        self.write(self.project / base / 'submodule.yaml', 'name: sub\n')
        self.write(self.project / base / 'overview.md', '# overview\n')
        self.write(self.project / authority, '# authority\n')
        self.write(self.project / 'projects/modules/unrelated/overview.md', '# irrelevant\n')
        bundle, _, _ = collector.build_input(self.collect(), 80000)
        self.assertEqual(sorted(bundle['model']['authority']), [authority, base + '/overview.md'])
        self.assertEqual(bundle['targets'], [], 'a reminder file that does not exist is not offered')
        self.write(self.project / base / 'module-memory.md', '- When x, read y.\n')
        bundle, _, _ = collector.build_input(self.collect(), 80000)
        self.assertEqual(bundle['targets'], [base + '/module-memory.md'])

    def test_paths_reject_traversal_absolute_plugin_and_escaping_links(self):
        for target in ('../outside.md', '/outside.md', 'C:/outside.md', 'plugins/core/context/a.md',
                       'projects/../private.md', 'a/./b.md', 'a//b.md', 'name:stream.md', 'trailing. /x.md'):
            self.events([self.correction(target_path=target)])
            result = self.collect()
            self.assertEqual(result['candidates'], [], target)
            self.assertEqual(result['funnel']['correction']['unsafe_target'], 1, target)
            self.assertTrue(any(x['code'] == 'unsafe_path' for x in result['diagnostics']), target)
        outside = Path(self.fixture.name) / 'outside'
        outside.mkdir()
        link = self.memory / 'qa'
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                raise
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        self.events([self.correction(scope='qa')])
        result = self.collect()
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['funnel']['correction']['unresolved_scope'], 1)
        self.assertTrue(result['diagnostics'])

    def test_plugin_installed_inside_the_project_is_out_of_reach(self):
        self.write(self.project / 'vendor/core/rules.md', '# rules\n')
        self.events([self.correction(target_path='vendor/core/rules.md')])
        self.assertEqual(len(self.ids()), 1)
        self.write(self.state / 'plugin-root.txt', 'vendor/core')
        self.assertEqual(self.ids(), [])

    def test_malformed_event_diagnostic_and_no_import_stream_replacement(self):
        self.write(self.state / 'events.jsonl', 'broken\n[]\n')
        self.assertEqual([d['code'] for d in self.collect()['diagnostics']], ['event_invalid'] * 2)
        for module in (evidence, collector):
            spec = importlib.util.spec_from_file_location('again', module.__file__)
            original = sys.stdout
            spec.loader.exec_module(importlib.util.module_from_spec(spec))
            self.assertIs(sys.stdout, original)

    def test_predicate_mutations_are_detected_without_disk_edits(self):
        source = Path(evidence.__file__).read_text(encoding='utf-8')
        mutations = [
            ("if row.get('job_id'):", 'if False:', 'test_main_and_spill_dedup_consumed_and_job_events'),
            ('if identity in seen:', 'if False:', 'test_main_and_spill_dedup_consumed_and_job_events'),
            ("or parts[0].casefold() == 'plugins'", 'or False', 'test_paths_reject_traversal_absolute_plugin_and_escaping_links'),
            ('if any(resolved.is_relative_to(p) for p in self.plugin_roots):', 'if False:',
             'test_plugin_installed_inside_the_project_is_out_of_reach'),
        ]
        for before, after, method in mutations:
            with self.subTest(predicate=before):
                self.assertEqual(source.count(before), 1)
                mutated = types.ModuleType('evidence_mutant')
                mutated.__file__ = evidence.__file__
                exec(compile(source.replace(before, after), evidence.__file__, 'exec'), mutated.__dict__)
                result = unittest.TestResult()
                with patch.object(collector, '_Inputs', mutated._Inputs), patch.object(collector, '_events', mutated._events):
                    EvidenceTests(method).run(result)
                self.assertEqual(result.testsRun, 1)
                self.assertFalse(result.errors, result.errors)
                self.assertTrue(result.failures, 'mutation survived: ' + before)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    unittest.main()
