#!/usr/bin/env python3
"""Read-only evidence collection against isolated workspace fixtures."""

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
import _selfiter_policy as policy
from _state_io import memory_dir_of, state_dir_of

try:
    import _selfiter_evidence as collector
except ModuleNotFoundError:
    collector = None


class EvidenceTests(unittest.TestCase):
    def test_batch_keeps_complete_target_and_shared_authority_once(self):
        memory = self.memory.relative_to(self.project).as_posix()
        target = memory + '/dev/MEMORY.md'
        shared = memory + '/shared/MEMORY.md'
        snap = {'text': '# dev\r\n## Work\r\n- Current rule.\r\n', 'sha256': 'target'}
        bundle = {'evidence': [{'id': 'new', 'problem_key': 'one', 'role': 'dev'}],
                  'sources': {target: snap, shared: {'text': '- [纠正] Keep exceptions.', 'sha256': 'shared'}},
                  'targets': {target: snap}, 'fingerprint': 'all', 'diagnostics': []}
        selected, manual = collector.prepare_model_input(self.project, bundle, 40000)
        self.assertFalse(manual)
        self.assertEqual(selected[1]['targets'][target], snap)
        self.assertNotIn('text', selected[1]['sources'][target])
        self.assertEqual(selected[1]['sources'][shared]['text'], '- [纠正] Keep exceptions.')

    def test_necessary_authority_is_not_truncated_to_make_batch_fit(self):
        memory = self.memory.relative_to(self.project).as_posix()
        bundle = {'evidence': [{'id': 'new', 'role': 'dev'}], 'targets': {}, 'sources': {
            memory + '/shared/MEMORY.md': {'text': 'protected condition ' * 4000, 'sha256': 'full'}}}
        selected, manual = collector.prepare_model_input(self.project, bundle, 40000)
        self.assertIsNone(selected)
        self.assertEqual(manual[0]['reason'], 'required_context_exceeds_input_budget')
        self.assertGreater(manual[0]['required_chars'], 40000)

    def test_manual_identity_ignores_unrelated_event_log_growth(self):
        memory = self.memory.relative_to(self.project).as_posix()
        state = self.state.relative_to(self.project).as_posix()
        bundle = {'evidence': [{'id': 'same', 'role': 'dev'}], 'targets': {}, 'sources': {
            memory + '/shared/MEMORY.md': {'text': 'required authority ' * 4000, 'sha256': 'authority'},
            state + '/events.jsonl': {'text': 'history', 'sha256': 'before'}}}
        _, first = collector.prepare_model_input(self.project, bundle, 40000)
        bundle['sources'][state + '/events.jsonl'] = {'text': 'more history', 'sha256': 'after'}
        _, second = collector.prepare_model_input(self.project, bundle, 40000)
        self.assertEqual(first[0]['fingerprint'], second[0]['fingerprint'])

    def test_one_theme_is_selected_without_consuming_remaining_theme(self):
        bundle = {'evidence': [{'id': 'one', 'problem_key': 'a'}, {'id': 'two', 'problem_key': 'b'}],
                  'targets': {}, 'sources': {}}
        limit = self.model_chars([bundle['evidence'][0]])
        selected, manual = collector.prepare_model_input(self.project, bundle, limit)
        self.assertEqual([e['id'] for e in selected[0]['evidence']], ['one'])
        self.assertFalse(manual)
        remaining = dict(bundle, evidence=[bundle['evidence'][1]])
        following, _ = collector.prepare_model_input(self.project, remaining, 40000)
        self.assertEqual([e['id'] for e in following[0]['evidence']], ['two'])
        self.assertNotEqual(selected[0]['fingerprint'], following[0]['fingerprint'])

    def model_chars(self, rows, *, sources=None, targets=None, decisions=None):
        # Budget oracle is a literal wire-format fixture, not collector output.
        model = {'evidence': rows, 'sources': sources or {}, 'targets': targets or {},
                 'fingerprint': '0' * 16, 'prior_decisions': decisions or [],
                 'context_policy': 'Full targets/current authority; selected facts replace history. No omitted file grants write authority.'}
        return len(json.dumps(model, ensure_ascii=False))

    def test_batch_packs_multiple_complete_groups_in_stable_order(self):
        rows = [{'id': name, 'problem_key': name[0], 'text': '全文\\\"\r\n'}
                for name in ('a1', 'a2', 'b1', 'b2', 'c1')]
        bundle = {'evidence': rows, 'sources': {}, 'targets': {}}
        original = json.loads(json.dumps(bundle))
        selected, manual = collector.prepare_model_input(self.project, bundle, 40000)
        self.assertFalse(manual)
        self.assertEqual(selected[0]['evidence'], rows)
        self.assertEqual(selected[1]['evidence'], rows)
        self.assertEqual(bundle, original, 'preparation must not consume or alter input')
        reordered, _ = collector.prepare_model_input(self.project, dict(bundle, evidence=rows[::-1]), 40000)
        self.assertEqual(reordered, selected)
        expected = hashlib.sha256(json.dumps([r['id'] for r in rows],
                                  separators=(',', ':')).encode()).hexdigest()[:16]
        self.assertEqual(selected[0]['fingerprint'], expected)

    def test_batch_deduplicates_shared_context_at_exact_serialized_boundary(self):
        memory = self.memory.relative_to(self.project).as_posix()
        target, shared = memory + '/dev/MEMORY.md', memory + '/shared/MEMORY.md'
        snap = {'text': '# dev\r\n' + '完整规则\\\"\n' * 50, 'sha256': 'target'}
        authority = {'text': '[纠正] exception ' * 50, 'sha256': 'shared'}
        rows = [{'id': n, 'problem_key': n, 'role': 'dev'} for n in ('a', 'b')]
        sources = {target: snap, shared: authority}
        model_sources = {target: {'sha256': 'target', 'text_in_targets': True}, shared: authority}
        limit = self.model_chars(rows, sources=model_sources, targets={target: snap})
        bundle = {'evidence': rows, 'sources': sources, 'targets': {target: snap}}
        selected, manual = collector.prepare_model_input(self.project, bundle, limit)
        self.assertFalse(manual)
        self.assertEqual(selected[1]['evidence'], rows)
        self.assertEqual(selected[1]['targets'], {target: snap})
        self.assertEqual(selected[1]['sources'], model_sources)
        self.assertEqual(len(json.dumps(selected[1], ensure_ascii=False)), limit)
        smaller, requests = collector.prepare_model_input(self.project, bundle, limit - 1)
        self.assertFalse(requests, 'a group that fits alone must remain queued, not manual')
        self.assertEqual(smaller[1]['evidence'], rows[:1])
        self.assertEqual(smaller[1]['targets'][target], snap)
        self.assertEqual(smaller[1]['sources'][shared], authority)

    def test_batch_recomputes_marginal_cost_after_shared_context(self):
        memory = self.memory.relative_to(self.project).as_posix()
        dev, qa = memory + '/dev/MEMORY.md', memory + '/qa/MEMORY.md'
        targets = {dev: {'text': 'd' * 600, 'sha256': 'dev'},
                   qa: {'text': 'q' * 500, 'sha256': 'qa'}}
        rows = [{'id': 'a', 'problem_key': 'a', 'role': 'dev', 'text': ''},
                {'id': 'b', 'problem_key': 'b', 'role': 'qa', 'text': 'b' * 200},
                {'id': 'c', 'problem_key': 'c', 'role': 'dev', 'text': 'c' * 300}]
        model_sources = {dev: {'sha256': 'dev', 'text_in_targets': True},
                         qa: {'sha256': 'qa', 'audit_only': True}}
        chosen = [rows[0], rows[2]]
        limit = self.model_chars(chosen, sources=model_sources, targets={dev: targets[dev]})
        selected, manual = collector.prepare_model_input(
            self.project, {'evidence': rows, 'sources': targets, 'targets': targets}, limit)
        self.assertFalse(manual)
        self.assertEqual(selected[1]['evidence'], chosen)
        self.assertEqual(selected[1]['targets'], {dev: targets[dev]})
        self.assertEqual(selected[1]['sources'], model_sources)

    def test_oversized_first_group_does_not_block_later_complete_groups(self):
        large = [{'id': 'a1', 'problem_key': 'a', 'text': 'huge' * 10000},
                 {'id': 'a2', 'problem_key': 'a', 'text': 'retain every row'}]
        small = [{'id': n, 'problem_key': n} for n in ('b', 'c')]
        bundle = {'evidence': large + small, 'sources': {}, 'targets': {}}
        selected, manual = collector.prepare_model_input(self.project, bundle, 2000)
        self.assertEqual(selected[1]['evidence'], small)
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]['problem_key'], 'a')
        self.assertEqual(manual[0]['evidence_ids'], ['a1', 'a2'])
        self.assertEqual(manual[0]['evidence'], large)
        self.assertEqual(manual[0]['context']['evidence'], large)
        self.assertEqual(manual[0]['required_chars'], self.model_chars(large))

    def test_fitting_first_large_group_is_skipped_for_more_small_groups(self):
        large = {'id': 'a', 'problem_key': 'a', 'text': 'a' * 400}
        small = [{'id': n, 'problem_key': n, 'text': 's' * 100} for n in ('b', 'c')]
        limit = self.model_chars([large])
        bundle = {'evidence': [large] + small, 'sources': {}, 'targets': {}}
        selected, manual = collector.prepare_model_input(self.project, bundle, limit)
        self.assertFalse(manual)
        self.assertEqual(selected[1]['evidence'], small)
        changed = dict(bundle, evidence=[dict(large, id='unselected-change', text='x' * 500)] + small)
        following, _ = collector.prepare_model_input(self.project, changed, limit)
        self.assertEqual(following[0]['fingerprint'], selected[0]['fingerprint'])

    def test_batch_unions_permissions_context_and_prior_decisions(self):
        memory = self.memory.relative_to(self.project).as_posix()
        state = self.state.relative_to(self.project).as_posix()
        dev, qa = memory + '/dev/MEMORY.md', memory + '/qa/MEMORY.md'
        unrelated = memory + '/pm/MEMORY.md'
        base = 'projects/modules/basic/sub/'
        authority, target = base + 'current-state/data-model.md', base + 'module-memory.md'
        history = state + '/events.jsonl'
        rows = [{'id': 'a', 'problem_key': 'a', 'role': 'dev', 'source_path': history},
                {'id': 'b', 'problem_key': 'b', 'type': 'task_blocked',
                 'task': {'assigned_to': 'qa', 'module': 'basic/sub', 'target_path': authority}}]
        targets = {p: {'text': 'full ' + p, 'sha256': p} for p in (dev, qa, target, unrelated)}
        sources = dict(targets, **{authority: {'text': 'full authority', 'sha256': 'auth'},
                                  history: {'text': 'history ' * 10000, 'sha256': 'history'},
                                  'projects/board.yaml': {'text': 'old board ' * 10000, 'sha256': 'board'}})
        decisions = [{'id': 'both', 'problem_keys': ['a', 'b']},
                     {'id': 'b', 'problem_keys': ['b']}, {'id': 'other', 'problem_keys': ['z']}]
        selected, manual = collector.prepare_model_input(self.project, {
            'evidence': rows, 'sources': sources, 'targets': targets, 'prior_decisions': decisions}, 40000)
        self.assertFalse(manual)
        audit, model = selected
        self.assertEqual(model['evidence'], rows)
        self.assertEqual(model['targets'], {p: targets[p] for p in (dev, qa, target)})
        self.assertEqual(audit['targets'], model['targets'])
        self.assertEqual(model['sources'][authority], sources[authority])
        self.assertEqual(audit['sources'], sources, 'audit retains full history and original hashes')
        for p in (history, 'projects/board.yaml', unrelated):
            self.assertEqual(model['sources'][p], {'sha256': sources[p]['sha256'], 'audit_only': True})
        self.assertEqual(set(audit['context_source_paths']),
                         {dev, qa, target, authority, history, 'projects/board.yaml'})
        self.assertEqual(model['prior_decisions'], decisions[:2])
        self.assertEqual(audit['prior_decisions'], decisions[:2])

    def test_batch_consumed_ids_only_cover_selected_complete_groups(self):
        rows = [self.correction(n, topic_key=topic, summary=topic + ' ' * padding)
                for n, topic, padding in ((1, 'large', 1000), (2, 'large', 1000),
                                          (3, 'small', 0), (4, 'small', 0),
                                          (5, 'other', 0), (6, 'other', 0))]
        self.events(rows)
        bundle = self.collect()
        limit = self.model_chars(bundle['evidence']) - 500
        selected, manual = collector.prepare_model_input(self.project, bundle, limit)
        self.assertFalse(manual)
        consumed = [r['id'] for r in selected[0]['evidence']]
        expected = sorted(policy.event_id(r) for r in rows[2:])
        self.assertEqual(sorted(consumed), expected)
        pending = self.collect(consumed_ids=consumed)
        self.assertEqual(sorted(r['id'] for r in pending['evidence']),
                         sorted(policy.event_id(r) for r in rows[:2]))

    def test_complete_group_is_never_split_to_fit_single_row_budget(self):
        rows = [{'id': n, 'problem_key': 'same', 'text': '完整\\\"\r\n' * 50}
                for n in ('a', 'b')]
        bundle = {'evidence': rows, 'sources': {}, 'targets': {}}
        exact = self.model_chars(rows)
        selected, manual = collector.prepare_model_input(self.project, bundle, exact)
        self.assertFalse(manual)
        self.assertEqual(selected[1]['evidence'], rows)
        self.assertEqual(len(json.dumps(selected[1], ensure_ascii=False)), exact)
        for limit in (exact - 1, self.model_chars(rows[:1])):
            with self.subTest(limit=limit):
                selected, manual = collector.prepare_model_input(self.project, bundle, limit)
                self.assertIsNone(selected)
                self.assertEqual(manual[0]['evidence_ids'], ['a', 'b'])
                self.assertEqual(manual[0]['context']['evidence'], rows)
                self.assertEqual(manual[0]['required_chars'], exact)

    def test_tied_groups_keep_stable_selection_and_empty_input(self):
        rows = [{'id': n, 'problem_key': n} for n in ('a', 'b')]
        bundle = {'evidence': rows, 'sources': {}, 'targets': {}}
        limit = self.model_chars(rows[:1])
        selected, manual = collector.prepare_model_input(self.project, bundle, limit)
        reversed_selection, reversed_manual = collector.prepare_model_input(
            self.project, dict(bundle, evidence=rows[::-1]), limit)
        self.assertEqual(selected[1]['evidence'], rows[:1])
        self.assertEqual((reversed_selection, reversed_manual), (selected, manual))
        self.assertFalse(manual)
        self.assertEqual(collector.prepare_model_input(self.project, dict(bundle, evidence=[]), 0),
                         (None, []))

    def test_shared_context_fork_prefers_two_topics_over_cheaper_singleton(self):
        memory = self.memory.relative_to(self.project).as_posix()
        dev, qa = memory + '/dev/MEMORY.md', memory + '/qa/MEMORY.md'
        targets = {dev: {'text': 'd' * 600, 'sha256': 'dev'},
                   qa: {'text': 'q' * 800, 'sha256': 'qa'}}
        rows = [{'id': 'a', 'problem_key': 'a', 'role': 'dev'},
                {'id': 'b', 'problem_key': 'b', 'role': 'qa'},
                {'id': 'c', 'problem_key': 'c', 'role': 'qa'}]
        bundle = {'evidence': rows, 'sources': targets, 'targets': targets}
        selected, manual = collector.prepare_model_input(self.project, bundle, 1350)
        self.assertFalse(manual)
        self.assertEqual(selected[1]['evidence'], rows[1:])
        self.assertEqual(selected[1]['targets'], {qa: targets[qa]})
        self.assertEqual(len(json.dumps(selected[1], ensure_ascii=False)), 1350)
        reverse, _ = collector.prepare_model_input(self.project, dict(bundle, evidence=rows[::-1]), 1350)
        self.assertEqual(reverse, selected)
        # Literal union fixture proves termination cannot still add the omitted a.
        sources = {p: {'sha256': snap['sha256'], 'text_in_targets': True}
                   for p, snap in targets.items()}
        self.assertEqual(self.model_chars(rows, sources=sources, targets=targets), 2075)

    def test_multistart_pack_prefers_shortest_equal_count_and_is_maximal(self):
        memory = self.memory.relative_to(self.project).as_posix()
        dev, qa = memory + '/dev/MEMORY.md', memory + '/qa/MEMORY.md'
        targets = {dev: {'text': 'd' * 600, 'sha256': 'dev'},
                   qa: {'text': 'q' * 800, 'sha256': 'qa'}}
        rows = [{'id': 'a', 'problem_key': 'a', 'role': 'dev'},
                {'id': 'b', 'problem_key': 'b', 'role': 'qa'},
                {'id': 'c', 'problem_key': 'c', 'role': 'qa'},
                {'id': 'd', 'problem_key': 'd', 'role': 'dev', 'text': 'd' * 100}]
        bundle = {'evidence': rows, 'sources': targets, 'targets': targets}
        for limit in (1350, 2075, 2300):
            with self.subTest(limit=limit):
                selected, manual = collector.prepare_model_input(self.project, bundle, limit)
                self.assertFalse(manual)
                chosen = selected[1]['evidence']
                if limit == 1350:
                    self.assertEqual(chosen, [rows[0], rows[3]])
                    self.assertLess(len(json.dumps(selected[1], ensure_ascii=False)), 1350)
                for row in rows:
                    if row in chosen:
                        continue
                    expanded = chosen + [row]
                    paths = {dev if r['role'] == 'dev' else qa for r in expanded}
                    expanded_targets = {p: snap for p, snap in targets.items() if p in paths}
                    sources = {p: {'sha256': snap['sha256'],
                                   **({'text_in_targets': True} if p in paths else {'audit_only': True})}
                               for p, snap in targets.items()}
                    self.assertGreater(self.model_chars(expanded, sources=sources, targets=expanded_targets),
                                       limit, 'termination left a complete fitting theme queued')

    def test_eighty_notes_with_shared_context_do_not_repeat_whole_pack(self):
        self.write(self.memory / 'dev/notes.md', '# notes\n' + ''.join(
            f'- 2026-11-01: [亲测] observation {n}\n' for n in range(80)))
        bundle = self.collect()
        self.assertEqual(len(bundle['evidence']), 80)
        model_serializations = 0
        original_dumps = json.dumps

        def counted(value, *args, **kwargs):
            nonlocal model_serializations
            if isinstance(value, dict) and 'context_policy' in value:
                model_serializations += 1
                if model_serializations > 80 * 80:
                    self.fail('same-context notes repeated the full pack for every seed')
            return original_dumps(value, *args, **kwargs)

        with patch.object(collector.json, 'dumps', new=counted):
            selected, manual = collector.prepare_model_input(self.project, bundle, 18000)
        self.assertFalse(manual)
        self.assertGreater(len(selected[1]['evidence']), 0)
        self.assertLess(len(selected[1]['evidence']), 80)
        notes = (self.memory / 'dev/notes.md').relative_to(self.project).as_posix()
        self.assertEqual(selected[1]['targets'][notes], bundle['targets'][notes])
        self.assertLessEqual(len(json.dumps(selected[1], ensure_ascii=False)), 18000)
        self.assertLessEqual(model_serializations, 80 * 80,
                             'same-context notes repeated the full pack for every seed')

    def test_selected_task_is_kept_verbatim_when_whole_board_is_audit_only(self):
        task = {'id': 'TASK-001', 'status': 'blocked', 'assigned_to': 'dev', 'notes': 'Keep this exact condition.'}
        bundle = {'evidence': [{'id': 'blocked', 'type': 'task_blocked', 'task': task}], 'targets': {},
                  'sources': {'projects/board.yaml': {'text': 'old rows\n' * 100000, 'sha256': 'full-board'}}}
        selected, manual = collector.prepare_model_input(self.project, bundle, 40000)
        self.assertFalse(manual)
        self.assertEqual(selected[1]['evidence'][0]['task'], task)
        self.assertNotIn('text', selected[1]['sources']['projects/board.yaml'])
        self.assertIn('projects/board.yaml', selected[0]['context_source_paths'])

    def setUp(self):
        self.assertIsNotNone(collector, 'evidence collector is not implemented')
        scratch = ROOT / 'tmp'
        scratch.mkdir(exist_ok=True)
        self.fixture = tempfile.TemporaryDirectory(prefix='selfiter-evidence-', dir=scratch)
        self.addCleanup(self.fixture.cleanup)
        self.project = Path(self.fixture.name) / 'project'
        self.project.mkdir()
        self.state = state_dir_of(self.project)
        self.memory = memory_dir_of(self.project)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='')

    def events(self, rows, filename='events.jsonl'):
        self.write(self.state / filename, '\n'.join(json.dumps(x) for x in rows) + '\n')

    def correction(self, n=1, **kwargs):
        return dict({'type': 'user_correction', 'ts': f'2026-11-{n:02d}T00:00:00Z',
                     'scope': 'dev', 'summary': 'Preserve original bytes',
                     'entry_key': f'dev:2026-11-{n:02d}:Preserve'}, **kwargs)

    def collect(self, **kwargs):
        return collector.collect_evidence(self.project, **kwargs)

    def test_empty_shape_and_stable_fingerprint(self):
        result = self.collect()
        self.assertEqual(set(result), {'evidence', 'sources', 'targets', 'fingerprint', 'diagnostics'})
        self.assertEqual(result['evidence'], [])
        self.assertEqual(result, self.collect())

    def test_main_and_spill_dedup_consumed_and_job_events(self):
        a, b = self.correction(), self.correction(2)
        self.events([a])
        self.events([a], 'events.123.spill.jsonl')
        # A duplicate must not manufacture the >=2 distinct-correction trigger.
        self.assertEqual(self.collect()['evidence'], [])
        self.events([a, dict(a, job_id='self'), {'type': 'skill_used'}])
        self.events([a, b], 'events.123.spill.jsonl')
        self.events([self.correction(3)], 'events.123.spill.jsonl.merged.old')
        rows = self.collect()['evidence']
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(x['id'] for x in rows), sorted([policy.event_id(a), policy.event_id(b)]))
        self.assertTrue(all(x['group_event_ids'] == sorted([policy.event_id(a), policy.event_id(b)])
                            for x in rows))
        self.assertEqual([x['id'] for x in self.collect(consumed_ids=[policy.event_id(a)])['evidence']], [policy.event_id(b)])
        handled = [x['id'] for x in rows]
        self.assertEqual(self.collect(consumed_ids=handled)['evidence'], [])
        self.events([a, b, self.correction(4)])
        self.assertEqual([x['id'] for x in self.collect(consumed_ids=handled)['evidence']],
                         [policy.event_id(self.correction(4))])

    def test_same_module_target_does_not_merge_unrelated_facts(self):
        kw = {'scope': 'module', 'module': 'basic/sub',
              'target_path': 'projects/modules/basic/sub/current-state/data-model.md'}
        a = self.correction(**kw)
        b = self.correction(2, **dict(kw, summary='Use UTC'))
        self.events([a, b])
        self.assertEqual(self.collect()['evidence'], [])
        self.events([a, b, self.correction(3, **kw)])
        self.assertEqual(len(self.collect()['evidence']), 2)

    def test_explicit_topic_groups_but_empty_summary_does_not(self):
        self.events([self.correction(topic_key='bytes'),
                     self.correction(2, topic_key='bytes', summary='Keep CRLF')])
        self.assertEqual(len(self.collect()['evidence']), 2)
        self.events([self.correction(summary=''), self.correction(2, summary='')])
        self.assertEqual(self.collect()['evidence'], [])

    @unittest.skipUnless(importlib.util.find_spec('yaml'), 'PyYAML optional')
    def test_blocked_uses_actual_yaml_including_flow_tags(self):
        self.write(self.project / 'projects/board.yaml', '''tasks:
  - {id: TASK-001, status: blocked, assigned_to: dev, tags: []}
  - id: TASK-002
    status: blocked
    tags: [suspended]
  - {id: TASK-003, status: completed}
''')
        self.events([{'type': 'task_blocked', 'task_id': f'TASK-00{n}'} for n in (1, 2, 3)])
        rows = self.collect()['evidence']
        self.assertEqual([x['task_id'] for x in rows], ['TASK-001'])

    def test_yaml_missing_or_bad_never_guesses_blocked(self):
        self.events([{'type': 'task_blocked', 'task_id': 'TASK-001'}])
        self.write(self.project / 'projects/board.yaml', 'tasks: [{id: TASK-001, status: blocked}]')
        with patch.object(collector, 'safe_load', side_effect=ImportError('PyYAML unavailable')):
            result = self.collect()
        self.assertEqual(result['evidence'], [])
        self.assertTrue(any(x['code'] == 'yaml_unavailable' for x in result['diagnostics']))
        self.write(self.project / 'projects/board.yaml', 'tasks: [broken')
        self.assertEqual(self.collect()['evidence'], [])
        self.assertTrue(self.collect()['diagnostics'])

    @unittest.skipUnless(importlib.util.find_spec('yaml'), 'PyYAML optional')
    def test_malformed_task_fields_and_duplicate_ids_fail_closed(self):
        self.events([{'type': 'task_blocked', 'task_id': ['TASK-001']},
                     {'type': 'task_blocked', 'task_id': 'TASK-001'}])
        self.write(self.project / 'projects/board.yaml', '''tasks:
  - {id: TASK-001, status: blocked, tags: [123]}
''')
        result = self.collect()
        self.assertEqual(result['evidence'], [])
        self.assertTrue(result['diagnostics'])
        self.write(self.project / 'projects/board.yaml', '''tasks:
  - {id: TASK-001, status: blocked}
  - {id: TASK-001, status: completed}
''')
        self.assertEqual(self.collect()['evidence'], [])

    def test_verification_failures_are_explicit_controller_records(self):
        failure = {'job_id': 'job-old', 'proposal_id': 'P1', 'result': 'fail', 'role': 'dev'}
        self.events([dict(failure, type='proposal_verified')])
        self.assertEqual(self.collect()['evidence'], [])
        rows = self.collect(verification_failures=[failure, failure, dict(failure, result='inconclusive')])['evidence']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['type'], 'verification_fail')
        self.assertEqual(self.collect(consumed_ids=[rows[0]['id']], verification_failures=[failure])['evidence'], [])

    def test_backlog_80_unprocessed_lines_and_content_fingerprint(self):
        notes = self.memory / 'dev/notes.md'
        text = '# notes\n> explanation\n\n' + ''.join(f'- 2026-11-01: item {i}\n' for i in range(80))
        self.write(notes, text)
        result = self.collect()
        rows = result['evidence']
        self.assertEqual(len(rows), 80)
        self.assertEqual({x['type'] for x in rows}, {'memory_backlog'})
        self.assertEqual(self.collect(consumed_ids=[x['id'] for x in rows])['evidence'], [])
        self.assertEqual(self.collect(consumed_ids=[rows[0]['id']])['evidence'], [])
        self.assertEqual(notes.read_text(encoding='utf-8'), text)
        self.write(notes, text.replace('item 0\n', 'item 0 changed\n'))
        # Processed blocks remain physically present, but are not the backlog queue.
        self.assertEqual(self.collect(consumed_ids=[x['id'] for x in rows])['evidence'], [])

    def test_heading_blocks_and_continuations_count_content_not_entry_count(self):
        text = '# notes\n### existing observation\n' + 'details\n' * 80
        self.write(self.memory / 'dev/notes.md', text)
        rows = self.collect()['evidence']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['content_lines'], 80)
        self.assertEqual(rows[0]['provenance'], 'unknown')
        self.assertFalse(rows[0]['auto_eligible'])

    def test_notes_content_identity_survives_line_changes_but_detects_changed_block(self):
        notes = self.memory / 'dev/notes.md'
        text = '### [亲测] bytes\n' + 'preserve bytes\n' * 80
        self.write(notes, text)
        row = self.collect()['evidence'][0]
        self.assertEqual(row['provenance'], 'self_tested_claim')
        self.write(notes, '# new heading\n\n' + text)
        self.assertEqual(self.collect(consumed_ids=[row['id']])['evidence'], [])
        self.write(notes, text.replace('preserve bytes', 'preserve raw bytes', 1))
        changed = self.collect(consumed_ids=[row['id']])['evidence']
        self.assertEqual(len(changed), 1)
        self.assertNotEqual(changed[0]['content_sha256'], row['content_sha256'])

    def test_dismissal_baseline_only_suppresses_older_problem_events(self):
        self.events([self.correction(), self.correction(2), self.correction(3)])
        self.write(self.state / 'activity-state.json', json.dumps({'pending_maintenance': [
            {'dedup_key': 'problem_threshold', 'status': 'closed', 'closed_at': '2026-11-02T00:00:00Z'}]}))
        self.assertEqual(self.collect()['evidence'], [])
        self.events([self.correction(), self.correction(2), self.correction(3), self.correction(4)])
        result = self.collect()['evidence']
        self.assertEqual(len(result), 2)
        self.assertEqual(sorted(x['id'] for x in result), sorted([policy.event_id(self.correction(3)),
                                                                policy.event_id(self.correction(4))]))
        self.write(self.state / 'activity-state.json', json.dumps({'pending_maintenance': [
            {'dedup_key': 'cadence_timeout', 'status': 'closed', 'closed_at': '2026-11-04T00:00:00Z'}]}))
        self.assertEqual(len(self.collect()['evidence']), 4)

    def test_missing_context_is_not_qualified_for_automatic_writes(self):
        self.write(self.memory / 'dev/notes.md', '### no origin\n' + 'detail\n' * 80)
        result = self.collect()
        self.assertFalse(result['evidence'][0]['auto_eligible'])
        rel = (self.memory / 'dev/MEMORY.md').relative_to(self.project).as_posix()
        self.assertEqual(result['targets'][rel], {'text': None, 'sha256': None})

    def test_context_is_full_hashes_are_bytes_and_missing_targets_explicit(self):
        self.events([self.correction(), self.correction(2)])
        full = '# memory\r\n' + 'context\r\n' * 6000
        self.write(self.memory / 'dev/MEMORY.md', full)
        self.write(self.memory / 'shared/MEMORY.md', '# shared authority\n')
        self.write(self.memory / 'dev/notes-archive.md', '# archive\nreal source\n')
        result = self.collect()
        rel = (self.memory / 'dev/MEMORY.md').relative_to(self.project).as_posix()
        self.assertEqual(result['sources'][rel], {'text': full, 'sha256': hashlib.sha256(full.encode()).hexdigest()})
        self.assertIn((self.memory / 'shared/MEMORY.md').relative_to(self.project).as_posix(), result['sources'])
        self.assertIn((self.memory / 'dev/notes-archive.md').relative_to(self.project).as_posix(), result['sources'])
        notes = (self.memory / 'dev/notes.md').relative_to(self.project).as_posix()
        self.assertEqual(result['targets'][notes], {'text': None, 'sha256': None})
        self.assertEqual(self.collect(), result)

    def test_module_context_only_related_full_docs_authorities_not_targets(self):
        base = 'projects/modules/basic/sub'
        authority = base + '/current-state/data-model.md'
        self.events([self.correction(scope='module', module='basic/sub', target_path=authority),
                     self.correction(2, scope='module', module='basic/sub', target_path=authority)])
        self.write(self.project / base / 'overview.md', '# overview\n[[projects/modules/basic/sub/current-state/api-surface]]\n')
        self.write(self.project / authority, '# authority\n')
        self.write(self.project / base / 'current-state/api-surface.md', '# api\n')
        self.write(self.project / 'projects/modules/unrelated/overview.md', '# irrelevant\n')
        result = self.collect()
        self.assertIn(authority, result['sources'])
        self.assertIn(base + '/current-state/api-surface.md', result['sources'])
        self.assertNotIn(authority, result['targets'])
        self.assertEqual(result['targets'][base + '/module-memory.md'], {'text': None, 'sha256': None})
        self.assertNotIn('projects/modules/unrelated/overview.md', result['sources'])

    def test_paths_reject_traversal_absolute_plugin_and_escaping_links(self):
        for target in ('../outside.md', '/outside.md', 'C:/outside.md',
                       'plugins/core/context/a.md', 'projects/../private.md'):
            self.events([self.correction(target_path=target), self.correction(2, target_path=target)])
            result = self.collect()
            self.assertEqual(result['evidence'], [], target)
            self.assertTrue(any(x['code'] == 'unsafe_path' for x in result['diagnostics']), target)
        outside = Path(self.fixture.name) / 'outside'
        outside.mkdir()
        link = self.memory / 'dev'
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                raise
            import _winapi
            _winapi.CreateJunction(str(outside), str(link))
        self.events([self.correction(), self.correction(2)])
        result = self.collect()
        self.assertFalse(any('/dev/' in k for k in result['targets']))
        self.assertTrue(result['diagnostics'])

    def test_malformed_event_diagnostic_and_no_import_stream_replacement(self):
        self.write(self.state / 'events.jsonl', 'broken\n[]\n')
        self.assertTrue(self.collect()['diagnostics'])
        spec = importlib.util.spec_from_file_location('evidence_again', collector.__file__)
        module = importlib.util.module_from_spec(spec)
        original = sys.stdout
        spec.loader.exec_module(module)
        self.assertIs(sys.stdout, original)

    def test_predicate_mutations_are_detected_without_disk_edits(self):
        source = Path(collector.__file__).read_text(encoding='utf-8')
        mutations = [
            ('len(ids) < 2', 'len(ids) < 1', 'test_main_and_spill_dedup_consumed_and_job_events'),
            ("if 'suspended' in tags:", 'if False:', 'test_blocked_uses_actual_yaml_including_flow_tags'),
            ("or row.get('job_id')", 'or False', 'test_main_and_spill_dedup_consumed_and_job_events'),
            ('identity in seen', 'False', 'test_main_and_spill_dedup_consumed_and_job_events'),
            ("or parts[0].casefold() == 'plugins'", 'or False', 'test_paths_reject_traversal_absolute_plugin_and_escaping_links'),
            ("sum(row['content_lines'] for row in pending) >= 80", "sum(row['content_lines'] for row in pending) >= 79",
             'test_backlog_80_unprocessed_lines_and_content_fingerprint'),
            ('if identity in consumed or content_hash in consumed:', 'if False:',
             'test_backlog_80_unprocessed_lines_and_content_fingerprint'),
            ('if ts > baseline:', 'if True:', 'test_dismissal_baseline_only_suppresses_older_problem_events'),
        ]
        for before, after, method in mutations:
            if 'blocked_uses' in method and not importlib.util.find_spec('yaml'):
                continue
            with self.subTest(predicate=before):
                self.assertEqual(source.count(before), 1)
                mutated = types.ModuleType('evidence_mutant')
                mutated.__file__ = collector.__file__
                exec(compile(source.replace(before, after), collector.__file__, 'exec'), mutated.__dict__)
                result = unittest.TestResult()
                with patch.dict(globals(), collector=mutated):
                    EvidenceTests(method).run(result)
                self.assertEqual(result.testsRun, 1)
                self.assertFalse(result.errors, result.errors)
                self.assertTrue(result.failures, 'mutation survived: ' + before)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    unittest.main()
