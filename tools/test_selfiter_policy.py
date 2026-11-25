#!/usr/bin/env python3
"""Deterministic self-iteration policy; no model, CLI, or user config writes."""

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins' / 'core' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    import _selfiter_policy as policy
except ModuleNotFoundError:
    policy = None


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(policy, 'self-iteration policy is not implemented')

    def test_default_no_money_limit(self):
        c = policy.merge_config(None, {})
        self.assertEqual(c['mode'], 'propose')
        self.assertEqual(c['limits'], {
            'per_project_days': 7, 'global_per_day': 1,
            'timeout_seconds': 900, 'max_evidence_chars': 80000,
        })

    def test_project_can_only_tighten(self):
        c = policy.merge_config(None, {'self_iteration': {
            'mode': 'propose', 'limits': {'per_project_days': 14, 'timeout_seconds': 120}}})
        self.assertEqual(c['mode'], 'propose')
        self.assertEqual(c['limits']['per_project_days'], 14)
        self.assertEqual(c['limits']['timeout_seconds'], 120)
        c = policy.merge_config(None, {'self_iteration': {
            'limits': {'per_project_days': 1, 'timeout_seconds': 1800}}})
        self.assertEqual(c['limits']['per_project_days'], 7)
        self.assertEqual(c['limits']['timeout_seconds'], 900)

    def test_writing_needs_the_project_itself_and_the_user_ceiling(self):
        for user in (None, 'apply', 'propose', 'off'):
            for project in (None, 'apply', 'propose', 'off'):
                u = {'version': 1, 'self_iteration': {'mode': user}} if user else None
                p = {'self_iteration': {'mode': project}} if project else {}
                want = ('off' if 'off' in (user, project) else
                        'apply' if project == 'apply' and user in (None, 'apply') else 'propose')
                with self.subTest(user=user, project=project):
                    self.assertEqual(policy.merge_config(u, p)['mode'], want)

    def test_bad_user_configuration_is_not_default_apply(self):
        for c in [{}, {'version': 2}, {'version': True},
                  {'version': 1, 'self_iteration': {'limits': {'global_per_day': True}}},
                  {'version': 1, 'self_iteration': {'mode': 'typo'}}]:
            with self.subTest(c=c), self.assertRaises(ValueError):
                policy.merge_config(c, {})

    def test_doors_intersect_and_off_is_stronger(self):
        c = policy.merge_config({'version': 1, 'self_iteration': {'mode': 'propose', 'doors': ['cc']}},
                                {'self_iteration': {'mode': 'apply', 'doors': ['codex']}})
        self.assertEqual(c['mode'], 'propose')
        self.assertEqual(c['doors'], [])

    def test_old_events_have_stable_identity(self):
        a = {'ts': '2026-11-01T00:00:00Z', 'type': 'user_correction', 'summary': 'x'}
        self.assertEqual(policy.event_id(a), policy.event_id(dict(reversed(list(a.items())))))
        self.assertNotEqual(policy.event_id(a), policy.event_id(dict(a, summary='y')))

    def test_same_module_file_does_not_merge_different_corrections(self):
        a = {'scope': 'module', 'target_path': 'a.md', 'summary': 'Use UTC'}
        b = dict(a, summary='Preserve bytes')
        self.assertNotEqual(policy.problem_key(a), policy.problem_key(b))

    def test_key_dates_do_not_split_exact_same_fact(self):
        a = {'scope': 'dev', 'entry_key': 'dev:2026-11-01:fact', 'summary': 'Preserve bytes'}
        b = dict(a, entry_key='dev:2026-11-02:fact')
        self.assertEqual(policy.problem_key(a), policy.problem_key(b))

    def test_frequency_is_utc_and_rolling(self):
        now = datetime(2026, 11, 10, 5, tzinfo=timezone.utc)
        rows = [{'project': 'a', 'started_at': '2026-11-04T00:00:00+00:00'}]
        limits = policy.merge_config(None, {})['limits']
        self.assertEqual(policy.frequency_reason(rows, 'a', now, limits), 'project_interval')
        self.assertIsNone(policy.frequency_reason(rows, 'b', now, limits))
        rows.append({'project': 'b', 'started_at': '2026-11-09T23:30:00-08:00'})
        self.assertEqual(policy.frequency_reason(rows, 'c', now, limits), 'global_daily')


if __name__ == '__main__':
    unittest.main(verbosity=2)
