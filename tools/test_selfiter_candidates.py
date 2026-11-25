#!/usr/bin/env python3
"""Candidate selection, packing and per-topic checks against disposable projects."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/core/scripts'))
import _notes_entries as notes
import _selfiter_candidates as candidates
import _selfiter_evidence as evidence
import _selfiter_topics as topics
import _selfiter_policy as policy
from _state_io import memory_dir_of, state_dir_of

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)


class NotesTests(unittest.TestCase):
    def texts(self, text):
        return [e['text'].split('\n')[0] for e in notes.scan_notes(text)['entries']]

    def test_both_forms_tags_colons_and_brackets(self):
        for line in ('- 2026-09-29: a', '- 2026-09-29：a', '- 2026-08-26（后补）a', '- 2026-08-26(x) a', '- [纠正] 2026-01-02: y',
                     '- [亲测][windows] 2026-01-02: y', '- 2026-01-02 a', '- 2026-01-02', '### title', '- 2026-01-02起 a'):
            with self.subTest(line=line):
                self.assertEqual(self.texts('# notes\n' + line + '\n'), [line])

    def test_look_alikes_are_not_entries(self):
        for line in ('- 2026-11-20abc', '- 2026-11-200', '- 2026-11-20-1', '- 2026-11-20_x', '  - 2026-11-20: nested',
                     '- undated', '* 2026-11-20: star', '###', '###title', '#### deep', '- 20261120: x'):
            with self.subTest(line=line):
                self.assertEqual(self.texts('# notes\n' + line + '\n'), [])

    def test_continuation_belongs_to_the_entry_until_the_next_section(self):
        found = notes.scan_notes('# n\n- 2026-01-01: a\n  more\n\nloose but below\n## next\nafter\n- 2026-01-02: b\n')
        self.assertEqual([e['text'] for e in found['entries']],
                         ['- 2026-01-01: a\n  more\n\nloose but below', '- 2026-01-02: b'])
        self.assertEqual(found['unrecognized_chars'], len('after'))

    def test_template_comments_and_fences_never_create_entries(self):
        found = notes.scan_notes('<!-- 示例：\n- 2026-01-01: sample\n-->\n```\n### sample\n```\n- 2026-01-02: real\n'
                                 '```\n### inside\n- 2026-01-03: inside\n```\n<!-- 2026\n- 2026-01-04: gone\n-->\n')
        self.assertEqual(len(found['entries']), 1)
        self.assertIn('- 2026-01-03: inside', found['entries'][0]['text'])
        self.assertNotIn('gone', found['entries'][0]['text'])

    def test_unclosed_fence_or_comment_hides_nothing(self):
        for opener in ('```', '~~~', '<!-- note'):
            with self.subTest(opener=opener):
                self.assertEqual(self.texts(opener + '\n- 2026-01-01: a\n- 2026-01-02: b\n'),
                                 ['- 2026-01-01: a', '- 2026-01-02: b'])
        self.assertEqual(self.texts('- 2026-01-01: mentions `<!--` inline\n- 2026-01-02: and --> later\n'),
                         ['- 2026-01-01: mentions `<!--` inline', '- 2026-01-02: and --> later'])
        # A longer opener needs an equally long closer; a shorter one inside is text.
        self.assertEqual(self.texts('````\n```\n- 2026-01-01: a\n````\n'), [])

    def test_group_heading_is_not_an_entry_but_a_bare_heading_is(self):
        self.assertEqual(self.texts('### group\n- 2026-01-01: a\n- 2026-01-02: b\n'), ['- 2026-01-01: a', '- 2026-01-02: b'])
        self.assertEqual(self.texts('### a\n### b\nbody\n'), ['### a', '### b'])
        self.assertEqual(self.texts('### own fact\nbody\n- 2026-01-01: a\n'), ['### own fact', '- 2026-01-01: a'])
        self.assertEqual(self.texts('### last\n## Section\n- 2026-01-01: a\n'), ['### last', '- 2026-01-01: a'])

    def test_disposed_marks(self):
        for mark in ('→ 已提升至 dev/MEMORY.md（新增，2026-01-01）', '→ 不变：已由 x 覆盖', '→ 拒绝：一次性',
                     '→ 已被 2026-01-01 纠正取代', '→ 已处置：x', '→ 已归档'):
            with self.subTest(mark=mark):
                self.assertEqual(notes.open_entries('- 2026-01-01: a\n  ' + mark + '\n'), [])
        self.assertEqual(len(notes.open_entries('- 2026-01-01: 改 a → 已发布 b\n')), 1)
        for example in ('```\n→ 已提升至 foo\n```', '写成 `→ 不变：x` 这样'):
            found = notes.scan_notes('- 2026-01-01: a\n' + example + '\n')
            self.assertEqual((found['lines']['disposed'], len(notes.open_entries('- 2026-01-01: a\n' + example))), (0, 1))
        self.assertEqual(len(notes.open_entries('- 2026-01-01: a\n- 2026-01-01: a\n')), 1)

    def test_every_line_has_exactly_one_class(self):
        text = ('# t\r\n> q\r\n\r\nloose\r\n<!-- a\r\nb\r\n-->\r\n```\r\nx\r\n```\r\n### g\r\n- 2026-01-01: a\r\n  more\r\n'
                '- 2026-01-02: b → 不变：x\r\n---\r\n## s\r\n_（空）_\r\nend')
        lines = notes.scan_notes(text)['lines']
        self.assertEqual(sum(lines.values()), len(text.split('\r\n')))
        self.assertEqual(lines, {'entry': 2, 'disposed': 2, 'template': 3, 'fence': 3, 'structure': 5, 'blank': 1,
                                 'unrecognized': 2})
        for sample, loose in ((text, 8), ('', 0), ('\n', 0), (text.replace('\r\n', '\n'), 8),
                              ('- 2026-01-01: a\n' * 3 + '```\n', 0), ('loose\n- undated\n> quote\n', 14)):
            found = notes.scan_notes(sample)
            self.assertEqual(sum(found['lines'].values()), len(sample.replace('\r\n', '\n').split('\n')))
            self.assertEqual(found['unrecognized_chars'], loose, sample)

    def test_dates_must_exist(self):
        dates = [e['date'] and e['date'].isoformat() for e in notes.scan_notes(
            '- 2026-02-30: a\n- 2026-02-28: b\n### t 2026-03-01\nx\n')['entries']]
        self.assertEqual(dates, [None, '2026-02-28', '2026-03-01'])

    def test_three_consumers_share_the_definition(self):
        for name in ('maintenance_workorder', 'check-iteration-trigger', '_selfiter_candidates', 'workframe_doctor'):
            source = (ROOT / 'plugins/core/scripts' / (name + '.py')).read_text(encoding='utf-8')
            self.assertIn('from _notes_entries import', source, name)
            self.assertNotIn(r'^- (?:\[', source, name)

    def test_provenance_reads_labels_and_body_but_not_retracted_claims(self):
        for text, want in (('[亲测] x', 'self_tested_claim'), ('来源：用户确认', 'user_confirmed_claim'),
                           ('本机实测两次', 'tested_in_body'), ('复现步骤如下', 'tested_in_body'), ('看代码像是', 'unknown'),
                           ('实测过 A，B 未实测', 'unknown'), ('无法复现', 'unknown'), ('纸面归因，待实测', 'unknown'),
                           ('由日志推断，亲测另一半', 'unknown'), ('没有实测', 'unknown')):
            with self.subTest(text=text):
                self.assertEqual(evidence._provenance(text), want)


class Fixture(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / 'tmp'
        scratch.mkdir(exist_ok=True)
        temp = tempfile.TemporaryDirectory(prefix='selfiter-candidates-', dir=scratch)
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name) / 'project'
        self.memory, self.state = memory_dir_of(self.project), state_dir_of(self.project)
        self.rel = self.memory.relative_to(self.project).as_posix()
        for role in ('dev', 'qa', 'shared'):
            self.write(self.memory / role / 'MEMORY.md', '# %s\n\n## Work\n\n- Rule of %s stays.\n' % (role, role))

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='')

    def events(self, *rows, name='events.jsonl'):
        self.write(self.state / name, ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))

    def correction(self, day=20, **kw):
        return dict({'type': 'user_correction', 'ts': '2026-11-%02dT00:00:00Z' % day, 'scope': 'dev',
                     'summary': 'Preserve original bytes'}, **kw)

    def collect(self, **kw):
        return candidates.collect_candidates(self.project, now=kw.pop('now', NOW), **kw)

    def build(self, chars=80000, **kw):
        return candidates.build_input(self.collect(**kw), chars)


class SelectionTests(Fixture):
    def test_outcomes_add_up_and_each_drop_has_a_reason(self):
        rows = [self.correction(), self.correction(scope='main'), self.correction(summary=' '), self.correction(scope='ghost'),
                self.correction(scope='module', module='no/where'), self.correction(scope='module'),
                self.correction(target_path='../x.md'), self.correction(scope='role', entry_key='qa:2026-11-20:x'),
                self.correction(scope='<role>'), self.correction(summary='done')]
        self.events(*rows)
        self.write(self.memory / 'dev/notes.md', '- 2026-11-20: a\n- 2026-11-20: a\n- 2026-11-20: b → 不变：x\n- 2026-11-20: c\n')
        consumed = [policy.event_id(rows[-1]), policy.digest(['notes', self.rel + '/dev/notes.md', __import__('hashlib').sha256(
            b'- 2026-11-20: c').hexdigest()])]
        selection = self.collect(progress={'consumed_ids': consumed})
        self.assertEqual(selection['funnel']['correction'], {
            'total': 10, 'selectable': 2, 'main_session_memory': 1, 'empty_summary': 1, 'unresolved_scope': 4,
            'unsafe_target': 1, 'consumed': 1})
        lines = selection['funnel']['notes'].pop('lines')
        self.assertEqual(lines['entry'], 3)
        self.assertEqual(selection['funnel']['notes'], {'total': 4, 'selectable': 1, 'duplicate': 1, 'disposed': 1, 'consumed': 1})
        self.assertEqual(sorted(c['scope'] for c in selection['candidates']), ['dev', 'dev', 'qa'])
        self.assertEqual(sum(d['code'] == 'unresolved_scope' for d in selection['diagnostics']), 4)

    def test_spill_duplicates_job_events_and_broken_lines(self):
        row = self.correction()
        self.events(row, dict(row, job_id='self'))
        self.events(row, name='events.1.spill.jsonl')
        self.write(self.state / 'events.2.spill.jsonl', 'broken\n[]\n')
        with (self.state / 'events.jsonl').open('ab') as stream:
            stream.write(b'{"type": "user_correction", "summary": "\xff"}\n')
            stream.write(json.dumps(self.correction(summary=['a', 'list'])).encode() + b'\n')
        selection = self.collect()
        self.assertEqual(selection['funnel']['correction'], {
            'total': 3, 'selectable': 1, 'duplicate': 1, 'invalid_summary': 1})
        self.assertEqual(sum(d['code'] == 'event_invalid' for d in selection['diagnostics']), 4)

    def test_unreadable_notes_are_counted_not_taken_for_empty(self):
        (self.memory / 'dev/notes.md').write_bytes(b'- 2026-05-01: \xff\n')
        selection = self.collect()
        self.assertEqual({k: v for k, v in selection['funnel']['notes'].items() if k != 'lines'}, {'total': 1, 'unreadable': 1})
        self.assertTrue(selection['diagnostics'])

    def test_ids_settled_by_the_previous_release_cover_the_same_text(self):
        # It took a full-width colon for continuation text, so one of its ids covers both entries.
        text = '- 2026-05-01: a\n- [亲测] 2026-05-02：b\n- 2026-05-03: c\n'
        self.write(self.memory / 'dev/notes.md', text)
        rel, digest_of = self.rel + '/dev/notes.md', lambda s: __import__('hashlib').sha256(s.encode()).hexdigest()
        content = digest_of('- 2026-05-01: a\n- [亲测] 2026-05-02：b')
        identity = policy.digest(['notes', rel, content])
        left = ['- 2026-05-03: c']
        for settled in (identity, content):
            self.assertEqual([c['text'] for c in self.collect(progress={'consumed_ids': [settled]})['candidates']], left)
        for status, reason in (('rejected', 'previously_rejected'), ('pending', 'awaiting_old_proposal')):
            selection = self.collect(decisions=[{'status': status, 'source_ids': [identity]}])
            self.assertEqual([c['text'] for c in selection['candidates']], left)
            self.assertEqual(selection['funnel']['notes'][reason], 2)
        self.assertEqual([c['text'] for c in self.collect(manual_ids=[identity])['candidates']], left)
        self.assertEqual(len(self.collect(progress={'consumed_ids': ['0' * 64]})['candidates']), 3)
        # Similar text elsewhere is new evidence; a comment inside the old block changes nothing.
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: a long\n<!-- x -->\n  tail\n## s\n- 2026-05-01: a\n')
        identity = policy.digest(['notes', rel, digest_of('- 2026-05-01: a long\n<!-- x -->\n  tail')])
        selection = self.collect(decisions=[{'status': 'rejected', 'source_ids': [identity]}])
        self.assertEqual([c['text'] for c in selection['candidates']], ['- 2026-05-01: a'])
        self.assertEqual(selection['funnel']['notes']['previously_rejected'], 1)
        # A dated example inside a fence used to end the entry: two old ids, one entry now.
        fenced = '- 2026-05-01: a\n  ```\n- 2026-05-02: sample\n  ```\n'
        self.write(self.memory / 'dev/notes.md', fenced)
        halves = [policy.digest(['notes', rel, digest_of(half)]) for half in (
            '- 2026-05-01: a\n  ```', '- 2026-05-02: sample\n  ```')]
        self.assertEqual(self.collect(decisions=[{'status': 'rejected', 'source_ids': halves}])['candidates'], [])
        self.assertEqual(len(self.collect(decisions=[{'status': 'rejected', 'source_ids': halves[:1]}])['candidates']), 1)
        self.write(self.memory / 'dev/notes.md', text)
        record = {'op_id': 'op', 'target_path': self.rel + '/dev/MEMORY.md', 'old_ids': ['old-failure']}
        selection = self.collect(progress={'consumed_ids': ['old-failure']}, repairs=[record, dict(record, op_id='x', old_ids=[],
                                                                                              acknowledged=True)])
        self.assertEqual(selection['funnel']['repair'], {'total': 2, 'consumed': 1, 'acknowledged': 1})

    def test_damaged_progress_records_do_not_stop_the_others(self):
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: a\n- 2026-05-02: b\n')
        ids = [c['id'] for c in self.collect()['candidates']]
        for broken in ({'state': 'seen', 'looks': '3', 'context': []}, 'text', {'state': 'paused', 'context': None}, None):
            selection = self.collect(progress={'evidence': {ids[0]: broken}})
            self.assertIn(ids[1], [c['id'] for c in selection['candidates']])
        with self.assertRaises(ValueError):
            self.collect(progress={'evidence': []})

    def test_a_file_that_appears_counts_as_changed_material(self):
        (self.memory / 'dev/MEMORY.md').unlink()
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: a\n')
        bundle, _, _ = self.build()
        row, = bundle['evidence']
        progress = {'evidence': {row['id']: {'state': 'paused', 'misses': 3, 'context': row['context']}}}
        self.assertEqual(self.collect(progress=progress)['trigger'], [])
        self.write(self.memory / 'dev/MEMORY.md', '# dev\n')
        self.assertTrue(self.collect(progress=progress)['trigger'])
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: 见 README.md 与 docs/x.md、../y.md\n')
        bundle, _, _ = self.build()
        row, = bundle['evidence']
        self.assertEqual({k for k, v in row['context'].items() if v is None and 'memory' not in k}, {'README.md', 'docs/x.md'})
        progress = {'evidence': {row['id']: {'state': 'paused', 'misses': 3, 'context': row['context']}}}
        self.assertEqual(self.collect(progress=progress)['trigger'], [])
        self.write(self.project / 'README.md', '# now here\n')
        self.assertTrue(self.collect(progress=progress)['trigger'])

    def test_frozen_text_and_hash_come_from_the_same_read(self):
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: a\n')
        selection = self.collect()
        real, path = selection['inputs'].read, self.memory / 'dev/MEMORY.md'

        def read(rel):
            text = real(rel)
            if rel.endswith('dev/MEMORY.md'):
                self.write(path, '# dev\n\n## Other\n')
            return text
        with patch.object(selection['inputs'], 'read', side_effect=read):
            bundle, _, _ = candidates.build_input(selection, 80000)
        rel = self.rel + '/dev/MEMORY.md'
        self.assertIn('## Work', bundle['model']['targets'][rel])
        self.assertEqual(bundle['evidence'][0]['context'][rel], bundle['sources'][rel]['sha256'])
        self.assertNotEqual(bundle['sources'][rel]['sha256'], selection['inputs'].file_hash(rel))

    def test_module_scope_needs_a_real_module(self):
        row = self.correction(scope='module', module='basic/sub')
        self.events(row)
        self.assertEqual(self.collect()['candidates'], [])
        self.write(self.project / 'projects/modules/basic/sub/submodule.yaml', 'name: sub\n')
        self.assertEqual([c['scope'] for c in self.collect()['candidates']], ['module:basic/sub'])

    def test_triggers_each_at_its_own_boundary(self):
        def trigger(*rows, notes_text='', **kw):
            self.events(*rows)
            self.write(self.memory / 'dev/notes.md', notes_text)
            return self.collect(**kw)['trigger']
        self.assertEqual(trigger(), [])
        self.assertEqual(trigger(self.correction(20), self.correction(21)), [])
        self.assertEqual(trigger(*(self.correction(d) for d in (20, 21, 22))), ['corrections=3'])
        for text in ('用户重复强调', '第 3 次', '第三次', '第十一次', '第12次', '又一次说'):
            self.assertEqual(trigger(self.correction(summary=text)), ['repeat_marked'], text)
        for text in ('第一次', '第 1 次', '强调', '三次'):
            self.assertEqual(trigger(self.correction(summary=text)), [], text)
        head = '- 2026-11-20: '
        self.assertEqual(trigger(notes_text=head + 'x' * (2999 - len(head))), [])
        self.assertEqual(trigger(notes_text=head + 'x' * (3000 - len(head))), ['notes:dev=3000'])
        self.assertEqual(trigger(notes_text='- 2026-11-11: a'), [])
        self.assertEqual(trigger(notes_text='- 2026-11-10: a'), ['oldest=2026-11-10'])
        undated = trigger(notes_text='### a\nx')
        self.assertEqual(undated, [])
        identity, = self.collect()['undated']
        self.assertEqual(self.collect(first_seen={identity: '2026-11-01T00:00:00+00:00'})['trigger'], ['oldest=2026-11-01'])

    def test_thresholds_do_not_add_up_across_scopes(self):
        for role in ('dev', 'qa'):
            self.write(self.memory / role / 'notes.md', '- 2026-11-20: ' + 'x' * 2000)
        self.assertEqual(self.collect()['trigger'], [])

    def test_seen_rides_with_new_scope_mates_or_changed_files_only(self):
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: old\n')
        self.write(self.memory / 'qa/notes.md', '- 2026-05-01: other\n')
        bundle, _, _ = self.build()
        book = {r['id']: {'state': 'seen', 'looks': 1, 'context': r['context']} for r in bundle['evidence'] if r['scope'] == 'dev'}
        progress = {'evidence': book}
        self.assertEqual([c['scope'] for c in self.collect(progress=progress)['candidates']], ['qa'])
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: old\n- 2026-05-02: new\n')
        self.assertEqual(len(self.collect(progress=progress)['candidates']), 3)
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: old\n')
        self.write(self.memory / 'dev/MEMORY.md', '# dev\n\n## Work\n\n- Other.\n')
        self.assertEqual(sorted(c['status'] for c in self.collect(progress=progress)['candidates']), ['new', 'seen'])
        for record in book.values():
            record['looks'] = candidates.MAX_LOOKS
        selection = self.collect(progress=progress)
        self.assertEqual([c['scope'] for c in selection['candidates']], ['qa'])
        self.assertEqual(selection['funnel']['notes']['seen_finished'], 1)

    def test_seen_alone_never_triggers(self):
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: ' + 'x' * 5000)
        bundle, _, _ = self.build()
        row, = bundle['evidence']
        progress = {'evidence': {row['id']: {'state': 'seen', 'looks': 1, 'context': {}}}}
        self.assertEqual(self.collect(progress=progress)['trigger'], [])

    def test_repair_scope_comes_from_its_target_and_is_never_guessed(self):
        def record(target):
            return {'op_id': target, 'target_path': target, 'new_text': '- x', 'failed_at': '2026-11-20T00:00:00+00:00'}
        self.write(self.project / 'projects/modules/basic/sub/submodule.yaml', 'name: sub\n')
        good = [self.rel + '/dev/MEMORY.md', 'projects/modules/basic/sub/module-memory.md']
        bad = [self.rel + '/ghost/MEMORY.md', self.rel + '/MEMORY.md', 'projects/modules/x/module-memory.md', 'AGENTS.md', '../x']
        selection = self.collect(repairs=[record(t) for t in good + bad])
        self.assertEqual([c['scope'] for c in selection['candidates']], ['dev', 'module:basic/sub'])
        self.assertEqual(selection['funnel']['repair']['unresolved_scope'], len(bad))
        self.assertEqual(selection['trigger'], ['repair'])

    def test_history_shows_consumed_corrections_of_the_same_scope(self):
        rows = [self.correction(d, summary='s%d' % d) for d in (1, 2, 3)] + [self.correction(4, scope='qa')]
        self.events(*rows)
        bundle, _, _ = self.build(progress={'consumed_ids': [policy.event_id(r) for r in rows[:2] + rows[3:]]})
        self.assertEqual([h['summary'] for h in bundle['model']['correction_history']['dev']], ['s1', 's2'],
                         'what is being analysed is not repeated as history')
        self.assertNotIn('qa', bundle['model']['correction_history'])
        self.events(*(self.correction(summary='s%d' % n) for n in range(45)))
        ids = [policy.event_id(self.correction(summary='s%d' % n)) for n in range(44)]
        bundle, _, _ = self.build(progress={'consumed_ids': ids})
        self.assertEqual([h['summary'] for h in bundle['model']['correction_history']['dev']], ['s%d' % n for n in range(4, 44)])

    def test_linked_role_directory_is_not_read(self):
        outside = self.project.parent / 'outside'
        self.write(outside / 'notes.md', '- 2026-05-01: foreign\n')
        link = self.memory / 'linked'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            if sys.platform != 'win32':
                raise
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        selection = self.collect()
        self.assertEqual(selection['candidates'], [])
        self.assertTrue(any(d['code'] == 'unsafe_path' for d in selection['diagnostics']))

    def test_a_missing_file_behind_a_link_is_no_dependency(self):
        (self.project / '.agents/skills').mkdir(parents=True)
        link = self.project / '.claude/skills'
        link.parent.mkdir()
        try:
            link.symlink_to(self.project / '.agents/skills', target_is_directory=True)
        except OSError:
            if sys.platform != 'win32':
                raise
            import _winapi
            _winapi.CreateJunction(str(self.project / '.agents/skills'), str(link))
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: 建 .claude/skills/new-role/SKILL.md 与 docs/x.md\n')
        bundle, _, _ = self.build()
        context = bundle['evidence'][0]['context']
        self.assertIn('docs/x.md', context)
        self.assertNotIn('.claude/skills/new-role/SKILL.md', context)
        self.assertEqual(candidates.changed_files(self.project, context), [])
        self.assertEqual(candidates.changed_files(self.project, {'.claude/skills/a.md': None}), ['.claude/skills/a.md'])
        self.write(self.project / 'docs/x.md', 'locked')
        real = Path.read_bytes

        def locked(path):
            if path.name == 'x.md':
                raise PermissionError(13, 'locked', str(path))
            return real(path)
        with patch.object(Path, 'read_bytes', locked):  # absent before, not readable now: that is a change
            self.assertEqual(candidates.changed_files(self.project, context), ['docs/x.md'])

    def test_collection_writes_nothing(self):
        self.write(self.memory / 'dev/notes.md', '### undated\nx\n')
        before = {p: p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        self.build()
        self.assertEqual({p: p.read_bytes() for p in self.project.rglob('*') if p.is_file()}, before)


class PackingTests(Fixture):
    def size(self, bundle):
        return len(json.dumps(bundle['model'], ensure_ascii=False))

    def test_oldest_first_across_scopes_repairs_and_repeats_before_them(self):
        self.write(self.memory / 'dev/notes.md', '- 2026-05-03: c\n')
        self.write(self.memory / 'qa/notes.md', ''.join('- 2026-05-0%d: %s\n' % (d, 'b' * 500) for d in (1, 2)))
        self.events(self.correction(20, summary='第二次'))
        full, _, _ = self.build()
        self.assertEqual(len(full['evidence']), 4)
        small, oversized, left = self.build(self.size(full) - 1)
        self.assertEqual(oversized, [])
        self.assertEqual([c['text'] for c in left], ['- 2026-05-03: c'], 'the youngest waits, not the smallest scope')
        self.assertIn('第二次', [r['text'] for r in small['evidence']])
        exact, _, left = self.build(self.size(full))
        self.assertEqual((len(exact['evidence']), left), (4, []))

    def test_one_oversized_item_goes_to_a_person_and_does_not_stop_the_rest(self):
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: %s\n- 2026-05-02: short\n' % ('x' * 9000))
        bundle, oversized, left = self.build(5000)
        self.assertEqual([r['text'] for r in bundle['evidence']], ['- 2026-05-02: short'])
        self.assertEqual([c['text'][:17] for c in oversized], ['- 2026-05-01: xxx'])
        self.assertGreater(oversized[0]['required_chars'], 5000)
        self.assertEqual(left, [])
        bundle, oversized, left = self.build(300)
        self.assertIsNone(bundle)
        self.assertEqual(len(oversized), 2)

    def test_a_huge_correction_does_not_drag_its_scope_mates_to_a_person(self):
        self.events(self.correction(summary='x' * 81000))
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: short\n')
        bundle, oversized, left = self.build()
        self.assertEqual([r['text'] for r in bundle['evidence']], ['- 2026-05-01: short'])
        self.assertEqual([len(c['text']) for c in oversized], [81000])
        self.assertLess(oversized[0]['required_chars'], 82000)
        self.assertEqual(bundle['model']['correction_history'], {}, 'history only fills what is left')
        self.assertEqual(bundle['packing'], {'notes': {'analysed': 1}, 'correction': {'to_a_person': 1}})
        self.assertIn(self.rel + '/dev/MEMORY.md', oversized[0]['required_context'])

    def test_lack_of_room_this_time_is_not_oversize(self):
        self.write(self.memory / 'dev/notes.md', ''.join('- 2026-05-0%d: %s\n' % (d, 'x' * 3000) for d in (1, 2, 3)))
        bundle, oversized, left = self.build(7500)
        self.assertEqual((len(bundle['evidence']), len(oversized), len(left)), (2, 0, 1))

    def test_targets_are_existing_readable_files_and_rules_are_separate(self):
        (self.memory / 'qa/MEMORY.md').unlink()
        (self.memory / 'dev/MEMORY.md').write_bytes(b'\xff\xfe broken')
        for role in ('dev', 'qa'):
            self.write(self.memory / role / 'notes.md', '- 2026-05-01: a\n')
        bundle, _, _ = self.build()
        self.assertEqual(bundle['targets'], [])
        self.assertEqual(sorted(bundle['model']['authority']), [self.rel + '/shared/MEMORY.md'])
        self.write(self.memory / 'shared/notes.md', '- 2026-05-01: a\n')
        bundle, _, _ = self.build()
        self.assertEqual(bundle['targets'], [self.rel + '/shared/MEMORY.md'])
        self.assertEqual(bundle['model']['authority'], {})

    def test_notes_files_are_not_repeated_in_full(self):
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: fact\n- 2026-05-02: gone → 不变：x\n')
        bundle, _, _ = self.build()
        self.assertEqual(json.dumps(bundle['model'], ensure_ascii=False).count('- 2026-05-01: fact'), 1)
        self.assertNotIn('gone', json.dumps(bundle['model'], ensure_ascii=False))

    def test_named_documents_and_skills_fill_the_rest_but_never_displace_evidence(self):
        doc = 'projects/modules/基础/子/prd.md'
        self.write(self.project / doc, '# prd\n' + 'x' * 4000)
        self.write(self.project / '.agents/skills/my-skill/SKILL.md', '---\ndescription: what it does\n---\nbody\n')
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: 见 `%s`、[[projects/none]] 和 my-skill，../x.md\n' % doc)
        bundle, _, _ = self.build()
        self.assertIn(doc, bundle['model']['authority'])
        self.assertNotIn(doc, bundle['targets'])
        self.assertIn('.agents/skills/my-skill/SKILL.md', bundle['targets'])
        self.assertEqual(bundle['model']['skill_index'][0]['description'], 'what it does')
        self.assertIn(doc, bundle['evidence'][0]['context'])
        smaller, oversized, _ = self.build(3000)
        self.assertEqual((len(smaller['evidence']), oversized), (1, []))
        self.assertNotIn(doc, smaller['model']['authority'])
        self.assertEqual(smaller['documents_left_out'], [doc])

    def test_only_real_project_skills_may_be_proposed_for(self):
        self.write(self.project / 'README.md', '# readme\n')
        self.write(self.project / 'docs/SKILL.md', '# a document with that name\n')
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: 见 README.md 与 docs/SKILL.md\n')
        bundle, _, _ = self.build()
        self.assertEqual(bundle['targets'], [self.rel + '/dev/MEMORY.md'])
        self.assertLessEqual({'README.md', 'docs/SKILL.md'}, set(bundle['model']['authority']))

    def test_state_files_and_notes_are_never_attached(self):
        self.write(self.state / 'x.md', 'secret state')
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: %s/x.md 与 %s/qa/notes.md\n' % (
            self.state.relative_to(self.project).as_posix(), self.rel))
        self.write(self.memory / 'qa/notes.md', '# notes\n')
        bundle, _, _ = self.build()
        self.assertFalse([p for p in bundle['sources'] if p.endswith(('x.md', 'notes.md'))])


class TopicTests(Fixture):
    def setUp(self):
        super().setUp()
        self.target = self.rel + '/dev/MEMORY.md'
        self.write(self.memory / 'dev/MEMORY.md', '# dev\n\n## Work\n\n- Rule of dev stays.\n\n```\n## Example\n```\n')
        self.write(self.memory / 'dev/notes.md', '- 2026-05-01: [亲测] one\n- 2026-05-02: 实测 two\n- 2026-05-03: 未实测 three\n')
        self.bundle, _, _ = self.build()
        self.ids = {r['short_id']: r['id'] for r in self.bundle['evidence']}

    def topic(self, *ids, **kw):
        return dict({'title': 't', 'evidence_ids': list(ids), 'outcome': 'insufficient', 'reason': 'r',
                     'destination': 'd', 'covered_by': [], 'operations': []}, **kw)

    def op(self, *ids, **kw):
        return dict({'target': self.target, 'section': '## Work', 'old_text': '', 'new_text': '- New rule.',
                     'conditions': 'c', 'evidence_ids': list(ids), 'consumers': []}, **kw)

    def review(self, *items, changed=()):
        return topics.review_output(self.bundle, {'summary': 's', 'topics': list(items)}, changed)

    def verdicts(self, *items, changed=()):
        result = self.review(*items, changed=changed)
        return {short: result['evidence'][full]['concluded'] for short, full in self.ids.items()}

    def written(self, op, *ids):
        result = self.review(self.topic(*(ids or op['evidence_ids']), outcome='write', operations=[op]))
        return result['topics'][0]['operations'][0]

    def test_unanswered_unknown_and_malformed_cost_only_their_own_evidence(self):
        self.assertEqual(self.verdicts(), dict.fromkeys(self.ids, False))
        self.assertEqual(self.verdicts(self.topic('E01', 'E99')), {'E01': True, 'E02': False, 'E03': False})
        broken = self.topic('E02')
        broken.pop('reason')
        self.assertEqual(self.verdicts(self.topic('E01'), broken, 'junk', self.topic('E03', outcome='maybe')),
                         {'E01': True, 'E02': False, 'E03': False})
        self.assertFalse(self.verdicts(self.topic('E01', reason=' '))['E01'])
        for outcome in topics.OUTCOMES:
            if outcome in topics.ELSEWHERE:  # somebody has to act there
                self.assertFalse(self.verdicts(self.topic('E01', outcome=outcome, destination=' '))['E01'], outcome)
            elif outcome not in ('covered', 'write'):
                self.assertTrue(self.verdicts(self.topic('E01', outcome=outcome, destination=''))['E01'], outcome)

    def test_withdrawn_evidence_gets_no_conclusion(self):
        result = topics.review_output(self.bundle, {'summary': 's', 'topics': [
            self.topic('E01', 'E02', outcome='write', operations=[self.op('E01'), self.op('E02')])]},
            withdrawn={self.ids['E01']})
        self.assertEqual([r['concluded'] for r in result['evidence'].values()], [False, True, False])
        self.assertEqual([op['status'] for op in result['topics'][0]['operations']], ['rejected', 'suggested'])
        result = topics.review_output(self.bundle, {'summary': 's', 'topics': [self.topic('E01', 'E02')]},
                                      withdrawn={self.ids['E01']})
        self.assertEqual([r['concluded'] for r in result['evidence'].values()], [False, True, False])

    def test_every_outcome_without_a_claim_to_check_concludes(self):
        for outcome in topics.OUTCOMES:
            if outcome not in ('covered', 'write'):
                self.assertTrue(self.verdicts(self.topic('E01', outcome=outcome))['E01'], outcome)

    def test_covered_needs_a_contiguous_quote_per_evidence(self):
        def covered(*quotes):
            return self.topic('E01', 'E02', outcome='covered', covered_by=[
                {'evidence_id': i, 'path': p, 'quote': q} for i, p, q in quotes])
        good = 'Rule of dev stays.'
        self.assertEqual(self.verdicts(covered(('E01', self.target, good))), {'E01': True, 'E02': False, 'E03': False})
        for path, quote in ((self.target, 'Ruleofdevstays.'), (self.target, 'Rule of dev'), (self.target, 'Rule of qa stays.'),
                            ('AGENTS.md', good), (self.target, '')):
            with self.subTest(quote=quote, path=path):
                self.assertFalse(self.verdicts(covered(('E01', path, quote)))['E01'])
        self.assertFalse(self.verdicts(covered(('E01', self.target, good)), changed=[self.target])['E01'])

    def test_one_failed_part_keeps_the_whole_item_waiting(self):
        bad = self.topic('E01', outcome='write', operations=[self.op('E01', section='## Nowhere')])
        self.assertFalse(self.verdicts(self.topic('E01', outcome='upstream'), bad)['E01'])
        mixed = self.topic('E01', 'E02', outcome='write', operations=[self.op('E01'), self.op('E02', target='x.md')])
        self.assertEqual(self.verdicts(mixed), {'E01': True, 'E02': False, 'E03': False})
        self.assertFalse(self.verdicts(self.topic('E01', outcome='write'))['E01'])
        # Listed by the topic but used by no operation: nothing was done about it.
        unused = self.topic('E01', 'E02', outcome='write', operations=[self.op('E01')])
        self.assertEqual(self.verdicts(unused), {'E01': True, 'E02': False, 'E03': False})
        # A broken operation that names a foreign id leaves that id's own conclusion alone.
        foreign = self.topic('E01', outcome='write', operations=[self.op('E01', 'E02')])
        self.assertEqual(self.verdicts(foreign, self.topic('E02')), {'E01': False, 'E02': True, 'E03': False})
        self.assertFalse(self.verdicts(self.topic('E01', outcome='covered', covered_by=[{
            'evidence_id': 'E01', 'path': self.target, 'quote': 'nowhere to be found'}]))['E01'])
        # An unproven quote beside a real conclusion does not take that conclusion away.
        self.assertTrue(self.verdicts(self.topic('E01', outcome='upstream'), self.topic('E01', outcome='covered'))['E01'])

    def test_operation_falls_with_anything_its_evidence_was_judged_against(self):
        result = self.review(self.topic('E01', outcome='write', operations=[self.op('E01')]),
                             changed=[self.rel + '/shared/MEMORY.md'])
        self.assertEqual(result['topics'][0]['operations'][0]['problems'], ['baseline_changed'])

    def test_a_file_without_headings_takes_an_empty_section(self):
        self.write(self.memory / 'dev/MEMORY.md', '- Rule of dev stays.\n')
        self.bundle, _, _ = self.build()
        self.assertEqual(self.written(self.op('E01', section=''))['status'], 'suggested')
        self.write(self.memory / 'dev/MEMORY.md', '# dev\n\n- Rule of dev stays.\n')
        self.bundle, _, _ = self.build()
        self.assertEqual(self.written(self.op('E01', section=''))['status'], 'suggested', 'a title alone is no section')
        for text in ('# dev\n\n# Exceptions\n', '# dev\n\n## Work\n'):
            self.write(self.memory / 'dev/MEMORY.md', text)
            self.bundle, _, _ = self.build()
            self.assertEqual(self.written(self.op('E01', section=''))['problems'], ['section_required'], text)
        self.write(self.memory / 'dev/MEMORY.md', '# dev\n\n### Work\n')
        self.bundle, _, _ = self.build()
        self.assertEqual(self.written(self.op('E01', section=''))['problems'], ['section_required'])

    def test_unusable_operations_are_named(self):
        for op, problem in ((self.op('E01', target=self.rel + '/shared/MEMORY.md'), 'target_not_offered'),
                            (self.op('E01', target='AGENTS.md'), 'target_not_offered'),
                            (self.op('E01', section='## Missing'), 'section_missing_or_ambiguous'),
                            (self.op('E01', section='- Rule of dev stays.'), 'section_missing_or_ambiguous'),
                            (self.op('E01', section='## Example'), 'section_missing_or_ambiguous'),
                            (self.op('E01', section=''), 'section_required'),
                            (self.op('E01', old_text='not there'), 'old_text_not_unique'),
                            (self.op('E01', conditions=' '), 'empty_text_or_conditions'),
                            (self.op('E02'), 'evidence_not_in_topic')):
            with self.subTest(problem=problem):
                got = self.written(op, 'E01')
                self.assertEqual((got['status'], got['problems']), ('rejected', [problem]))

    def test_blockers_rule_out_automatic_writing_but_keep_the_suggestion(self):
        self.assertEqual(self.written(self.op('E01'))['auto_write_blockers'], [])
        self.assertEqual(self.written(self.op('E02'))['auto_write_blockers'], [])
        for text in ('- 改 `.workframe/agent-memory/dev/notes.md` 与 projects/board.yaml。', '- 路由 /api/users 返回 token 字段。',
                     '- 比率 a/b/c，http://x.y/home/z。'):
            self.assertEqual(self.written(self.op('E01', new_text=text))['auto_write_blockers'], [], text)
        for op, blocker in ((self.op('E01', old_text='- Rule of dev stays.'), 'replacement'),
                            (self.op('E01', new_text='- [纠正] New.'), 'protected_correction'),
                            (self.op('E01', new_text='- One.\n- Two.'), 'not_one_item'),
                            (self.op('E01', new_text='- 读 D:\\work\\a.md。'), 'outside_path'),
                            (self.op('E01', new_text='- 读 ~/.ssh/config。'), 'outside_path'),
                            (self.op('E01', new_text='- 读 /srv/team/rules.md。'), 'outside_path'),
                            (self.op('E01', new_text='- 读 /data/rules.md。'), 'outside_path'),
                            (self.op('E01', new_text='- 读 ' + chr(92) * 2 + 'host' + chr(92) + 'share。'), 'outside_path'),
                            (self.op('E01', new_text='- 读 /opt/x/a。'), 'outside_path'),
                            (self.op('E01', new_text='- 读 /ho' + 'me/x/a。'), 'outside_path'),
                            (self.op('E01', new_text='- 读 /run/secrets/a。'), 'outside_path'),
                            (self.op('E01', new_text='- cat /proc/self/environ。'), 'outside_path'),
                            (self.op('E01', new_text='- scp host:/backups/current .'), 'outside_path'),
                            (self.op('E01', new_text='- 读 user@host:/ho' + 'me/x。'), 'outside_path'),
                            (self.op('E01', new_text='- 用 ghp_0123456789abcdefghij。'), 'secret'),
                            (self.op('E01', new_text='- 密码：abcd1234。'), 'secret'),
                            (self.op('E01', new_text='- ' + 'x' * 8000), 'capacity'),
                            (self.op('E03', new_text='- New（未实测）.'), 'unknown_source:E03'),
                            (self.op('E01', 'E03', new_text='- New（未实测）.'), 'unknown_source:E03')):
            with self.subTest(blocker=blocker):
                got = self.written(op)
                self.assertEqual((got['status'], got['auto_write_blockers']), ('suggested', [blocker]))
        self.assertEqual(self.written(self.op('E03'))['auto_write_blockers'], ['hedge_dropped:E03', 'unknown_source:E03'])

    def test_hedge_is_checked_per_operation(self):
        result = self.review(self.topic('E01', 'E03', outcome='write', operations=[
            self.op('E01', new_text='- 未实测 kept here.'), self.op('E03', new_text='- Claimed flatly.')]))
        self.assertEqual([op['auto_write_blockers'] for op in result['topics'][0]['operations']],
                         [[], ['hedge_dropped:E03', 'unknown_source:E03']])

    def test_capacity_counts_earlier_operations_on_the_same_file(self):
        first = self.op('E01', new_text='- ' + 'x' * 5000)
        result = self.review(self.topic('E01', outcome='write', operations=[first, self.op('E01', new_text='- ' + 'y' * 5000)]))
        self.assertEqual([op['auto_write_blockers'] for op in result['topics'][0]['operations']], [[], ['capacity']])

    def test_shared_needs_two_readers_and_operations_elsewhere_are_ignored(self):
        self.write(self.memory / 'shared/notes.md', '- 2026-05-01: [亲测] s\n')
        self.bundle, _, _ = self.build()
        shared = next(r['short_id'] for r in self.bundle['evidence'] if r['scope'] == 'shared')
        op = self.op(shared, target=self.rel + '/shared/MEMORY.md')
        self.assertEqual(self.written(op)['auto_write_blockers'], ['shared_memory', 'needs_two_consumers'])
        op['consumers'] = [{'role': r, 'decision': 'd'} for r in ('dev', 'qa')]
        self.assertEqual(self.written(op)['auto_write_blockers'], ['shared_memory'])
        result = self.review(self.topic('E01', outcome='stale', operations=[self.op('E01')]))
        self.assertEqual(result['topics'][0]['operations'], [])
        self.assertIn('operations_ignored_on_stale', result['topics'][0]['problems'])

    def test_result_schema_is_closed_and_fully_required(self):
        def walk(schema):
            if schema['type'] == 'object':
                self.assertFalse(schema['additionalProperties'])
                self.assertEqual(set(schema['required']), set(schema['properties']))
            for sub in [*schema.get('properties', {}).values(), *([schema['items']] if 'items' in schema else [])]:
                walk(sub)
        walk(topics.RESULT_SCHEMA)
        text = (ROOT / 'plugins/core/skills/self-iteration/reference/job-instructions.md').read_text(encoding='utf-8')
        for word in topics.OUTCOMES + tuple(topics.RESULT_SCHEMA['properties']) + ('covered_by', 'evidence_ids', 'consumers'):
            self.assertIn('`%s`' % word, text, word)


class MutationTests(unittest.TestCase):
    """Each rule is broken alone, in memory; the named test has to notice."""
    CASES = [
        ('_notes_entries', r'(?![0-9A-Za-z_-]))', r')', NotesTests, 'test_look_alikes_are_not_entries'),
        ('_notes_entries', 'if end is not None:\n                kinds.update', 'if True:\n                end = end or len(lines) - 1\n                kinds.update', NotesTests, 'test_unclosed_fence_or_comment_hides_nothing'),
        ('_notes_entries', "'structure' if title_only else", "'entry' if title_only else", NotesTests, 'test_every_line_has_exactly_one_class'),
        ('_notes_entries', '        if not title_only:\n', '        if True:\n', NotesTests, 'test_group_heading_is_not_an_entry_but_a_bare_heading_is'),
        ('_selfiter_candidates', "elif scope == 'main':", 'elif False:', SelectionTests, 'test_outcomes_add_up_and_each_drop_has_a_reason'),
        ('_selfiter_candidates', 'elif identity in seen:', 'elif False:', SelectionTests, 'test_outcomes_add_up_and_each_drop_has_a_reason'),
        ('_selfiter_candidates', 'item[1] >= BACKLOG_CHARS', 'item[1] > BACKLOG_CHARS', SelectionTests, 'test_triggers_each_at_its_own_boundary'),
        ('_selfiter_candidates', 'len(corrections) >= CORRECTION_COUNT', 'len(corrections) > CORRECTION_COUNT', SelectionTests, 'test_triggers_each_at_its_own_boundary'),
        ('_selfiter_candidates', 'now - oldest >= timedelta', 'now - oldest > timedelta', SelectionTests, 'test_triggers_each_at_its_own_boundary'),
        ('_selfiter_candidates', "(c['scope'] in scopes or c['context_changed'])", "(c['scope'] in scopes)", SelectionTests, 'test_seen_rides_with_new_scope_mates_or_changed_files_only'),
        ('_selfiter_candidates', "fresh = [c for c in candidates if c['status'] == 'new']", 'fresh = list(candidates)', SelectionTests, 'test_seen_alone_never_triggers'),
        ('_selfiter_candidates', 'if size([c]) > max_chars:', 'if False:', PackingTests, 'test_one_oversized_item_goes_to_a_person_and_does_not_stop_the_rest'),
        ('_selfiter_candidates', "not c.get('repeat'), c['when'], c['id']", "not c.get('repeat'), c['scope'], c['id']", PackingTests, 'test_oldest_first_across_scopes_repairs_and_repeats_before_them'),
        ('_selfiter_candidates', 'history) <= max_chars else omitted', 'history) <= 10 ** 9 else omitted', PackingTests, 'test_named_documents_and_skills_fill_the_rest_but_never_displace_evidence'),
        ('_selfiter_topics', "quote in _plain(texts[item['path']])", "''.join(quote.split()) in ''.join(_plain(texts[item['path']]).split())", TopicTests, 'test_covered_needs_a_contiguous_quote_per_evidence'),
        ('_selfiter_topics', "done = short in concluded and not why & {'operation_rejected', 'baseline_changed'}", 'done = short in concluded', TopicTests, 'test_one_failed_part_keeps_the_whole_item_waiting'),
        ('_selfiter_topics', 'for short in sorted(ok - proven):', 'for short in ():', TopicTests, 'test_covered_needs_a_contiguous_quote_per_evidence'),
        ('_selfiter_candidates', "if not covered.issuperset(range(entry['line'], entry['end'] + 1)):", 'if False:', SelectionTests, 'test_ids_settled_by_the_previous_release_cover_the_same_text'),
        ('_selfiter_candidates', "inputs.sources[rel]['sha256'] if rel in inputs.sources else", "", SelectionTests, 'test_frozen_text_and_hash_come_from_the_same_read'),
        ('_selfiter_topics', "| {rel for i in op['evidence_ids'] if i in rows for rel in rows[i]['context']}", '', TopicTests, 'test_operation_falls_with_anything_its_evidence_was_judged_against'),
        ('_selfiter_topics', 'for short in sorted(ok - used):', 'for short in ():', TopicTests, 'test_one_failed_part_keeps_the_whole_item_waiting'),
        ('_selfiter_topics', "own = set(op['evidence_ids']) & set(known)", "own = set(op['evidence_ids']) & set(rows)", TopicTests, 'test_one_failed_part_keeps_the_whole_item_waiting'),
        ('_selfiter_topics', "or rows[short]['id'] in withdrawn", '', TopicTests, 'test_withdrawn_evidence_gets_no_conclusion'),
        ('_selfiter_candidates', '[-HISTORY_LIMIT:]', '[-400:]', SelectionTests, 'test_history_shows_consumed_corrections_of_the_same_scope'),
        ('_selfiter_candidates', "if first <= entry['end'] and entry['line'] <= last for i", "if False for i", SelectionTests, 'test_ids_settled_by_the_previous_release_cover_the_same_text'),
        ('_notes_entries', "if opaque.get(i) != 'fence')", ')', NotesTests, 'test_disposed_marks'),
        ('_selfiter_topics', "blockers.append('hedge_dropped:'", "[].append('hedge_dropped:'", TopicTests, 'test_hedge_is_checked_per_operation'),
        ('_selfiter_topics', "if not set(own) <= set(topic_ids):", 'if False:', TopicTests, 'test_unusable_operations_are_named'),
        ('_selfiter_topics', 'added[target] = added.get(target, 0) + grown', 'pass', TopicTests, 'test_capacity_counts_earlier_operations_on_the_same_file'),
        ('_selfiter_topics', "elif any(line.startswith('##') for line in headings(text)) or len(headings(text)) > 1:", 'elif False:', TopicTests, 'test_unusable_operations_are_named'),
    ]

    def test_each_mutation_is_noticed(self):
        names = {'_notes_entries': 'notes', '_selfiter_candidates': 'candidates', '_selfiter_topics': 'topics'}
        for module, before, after, case, method in self.CASES:
            with self.subTest(module=module, before=before):
                path = ROOT / 'plugins/core/scripts' / (module + '.py')
                source = path.read_text(encoding='utf-8')
                self.assertEqual(source.count(before), 1)
                intact = unittest.TestResult()
                case(method).run(intact)
                self.assertTrue(intact.testsRun == 1 and intact.wasSuccessful(), 'the named test does not pass unmutated')
                mutated = types.ModuleType(module + '_mutant')
                mutated.__file__ = str(path)
                exec(compile(source.replace(before, after), str(path), 'exec'), mutated.__dict__)
                result = unittest.TestResult()
                replaced = {names[module]: mutated}
                with patch.dict(globals(), replaced):
                    case(method).run(result)
                self.assertEqual(result.testsRun, 1)
                self.assertFalse(result.errors, result.errors)
                self.assertTrue(result.failures, 'mutation survived: ' + before)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    unittest.main()
