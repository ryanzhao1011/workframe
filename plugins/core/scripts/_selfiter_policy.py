#!/usr/bin/env python3
"""Pure policy for unattended maintenance; configuration errors fail closed.

Money is deliberately absent: call frequency and elapsed time are limits, while
provider usage is only an observation. No import-time environment or disk reads.
"""

import copy
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULTS = {
    'mode': 'apply', 'doors': ['cc', 'codex'],
    'model': {'cc': None, 'codex': None},
    'limits': {'per_project_days': 7, 'global_per_day': 1,
               'timeout_seconds': 900, 'max_evidence_chars': 80000},
}
MODES = ('off', 'propose', 'apply')


def in_job():
    """Recursion marker, never an authorization or hook-trust bypass."""
    return bool(os.environ.get('WORKFRAME_SELFITER_JOB'))


def background_off():
    """Explicit process-level off switch, also used to seal maintenance tests."""
    return os.environ.get('WORKFRAME_SELFITER_OFF', '').lower() in ('1', 'true', 'yes')


def digest(value):
    """Content identity, independent of mapping insertion order."""
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def canonical_project(path):
    return os.path.normcase(str(Path(path).resolve()))


def parse_time(value):
    t = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('timestamp has no UTC offset')
    return t.astimezone(timezone.utc)


def _validate_section(section):
    if not isinstance(section, dict):
        raise ValueError('self_iteration must be an object')
    if 'mode' in section and section['mode'] not in MODES:
        raise ValueError('unknown self_iteration mode')
    if 'doors' in section:
        doors = section['doors']
        if not isinstance(doors, list) or any(x not in ('cc', 'codex') for x in doors):
            raise ValueError('doors must be a list of cc/codex')
    if 'limits' in section:
        limits = section['limits']
        if not isinstance(limits, dict):
            raise ValueError('limits must be an object')
        for key, value in limits.items():
            if key in DEFAULTS['limits'] and (type(value) is not int or value < 1):
                raise ValueError('limit must be a positive integer: ' + key)
    if 'model' in section:
        models = section['model']
        if not isinstance(models, dict) or any(
                value is not None and (not isinstance(value, str) or not value.strip())
                for key, value in models.items() if key in ('cc', 'codex')):
            raise ValueError('model must contain nonempty names or null')


def merge_config(user, project):
    """Missing user file uses defaults; present invalid data never does.

    The user mode is a ceiling. Writing also needs the project itself to say `apply`:
    a project that says nothing only gets suggestions. Other project settings can only
    tighten user policy. Longer intervals are tighter; smaller maxima are tighter. Unknown keys are inert, never executable options.
    """
    if user is not None and (not isinstance(user, dict)
                             or type(user.get('version')) is not int or user['version'] != 1):
        raise ValueError('unsupported user configuration version')
    if not isinstance(project, dict):
        raise ValueError('project configuration must be an object')
    u = (user or {}).get('self_iteration', {})
    p = project.get('self_iteration', {})
    _validate_section(u)
    _validate_section(p)
    result = copy.deepcopy(DEFAULTS)
    result['mode'] = u.get('mode', result['mode'])
    result['doors'] = list(u.get('doors', result['doors']))
    result['model'].update({k: v for k, v in u.get('model', {}).items() if k in ('cc', 'codex')})
    result['limits'].update({k: v for k, v in u.get('limits', {}).items() if k in result['limits']})
    # User policy also cannot silently exceed the published frequency ceilings.
    result['limits']['per_project_days'] = max(7, result['limits']['per_project_days'])
    result['limits']['global_per_day'] = min(1, result['limits']['global_per_day'])
    result['mode'] = MODES[min(MODES.index(result['mode']), MODES.index(p.get('mode', 'propose')))]
    result['doors'] = [x for x in result['doors'] if x in p.get('doors', result['doors'])]
    for key, value in p.get('limits', {}).items():
        if key in result['limits']:
            chooser = max if key == 'per_project_days' else min
            result['limits'][key] = chooser(result['limits'][key], value)
    command = (user or {}).get('post_job_command')
    if command is not None and (not isinstance(command, list) or not command
                                or any(not isinstance(x, str) or not x for x in command)):
        raise ValueError('post_job_command must be a nonempty argv list or null')
    result['post_job_command'] = command
    return result


def event_id(event):
    return digest(event)


def problem_key(event):
    """Conservative exact-fact grouping, never entire module-file grouping.

    Truncated summaries do not establish semantic equivalence. This key only
    groups exact snapshots for proposing/review; it does not authorize a write.
    """
    return digest([event.get('type', 'user_correction'), event.get('scope'),
                   event.get('target_path'), ''.join(str(event.get('summary', '')).split())])


def frequency_reason(rows, project, now, limits):
    """Rows represent actually launched or uncertain launches, not mere queues."""
    today = now.astimezone(timezone.utc).date()
    daily = 0
    for row in rows:
        started = parse_time(row['started_at'])
        if row.get('project') == project and started > now - timedelta(days=limits['per_project_days']):
            return 'project_interval'
        if started.date() == today:
            daily += 1
    return 'global_daily' if daily >= limits['global_per_day'] else None
