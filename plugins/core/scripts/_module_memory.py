"""On-demand basic/submodule work reminders, independent of role memory.

These are post-tool reminders, not a write guard. Shell recognition covers
module-document paths and explicit directory prefixes, not arbitrary code
paths or shell expansion. Only complete output blocks enter dedup state.
Subthreads retain their own state when the parent compacts; subthread compact
recovery is deliberately not implemented.
"""
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

import _harness
from _module_paths import edited_paths, lookup_submodules
from _role_memory import DELIVER_BUDGET, MAX_SESSIONS, u16len
from _state_io import FileLock, append_line, atomic_write, event_json, read_json_object, state_dir_of

STATE_FILE = 'module-memory-state.json'
_WORDS = re.compile(r'''(?:[^\s'";&|]+|'[^']*'|"[^"]*")+|[;&|]+''')


def _path(raw, cwd):
    if not isinstance(raw, str) or not raw or '\x00' in raw:
        return None
    if os.name == 'nt' and re.match(r'^/[a-zA-Z]/', raw):
        raw = raw[1] + ':' + raw[2:]
    path = Path(raw)
    return (path if path.is_absolute() else Path(cwd) / path).resolve()


def _direct_modules(project, path):
    try:
        parts = path.relative_to(project / 'projects/modules').parts
    except ValueError:
        return []
    if not parts:
        return []
    base = project / 'projects/modules' / parts[0]
    if not (base / 'module.yaml').is_file():
        return []
    out = [parts[0]]
    if len(parts) > 1 and parts[1] != 'shared' and (base / parts[1] / 'submodule.yaml').is_file():
        out.append('/'.join(parts[:2]))
    return out


def _shell_paths(project, payload, raw):
    command = raw.get('command')
    if not isinstance(command, str):
        return []
    cwd = _path(raw.get('workdir') or payload.get('workdir') or payload.get('cwd') or str(project), project)
    words = [re.sub(r'''['"]([^'"]*)['"]''', lambda m:m.group(1), m.group())
             for m in _WORDS.finditer(command)]
    paths, at_start, index = [cwd], True, 0
    while index < len(words):
        token = words[index]
        if token in (';', '&&', '||', '|', '&'):
            at_start = True
            index += 1
            continue
        if at_start and token.lower() in ('cd', 'chdir', 'set-location', 'pushd'):
            index += 1
            if index < len(words) and words[index].lower() in ('-literalpath', '-path', '/d'):
                index += 1
            if index < len(words):
                target = _path(words[index], cwd)
                if target is not None:
                    cwd = target
                    paths.append(cwd)
                index += 1
            at_start = False
            continue
        at_start = False
        if not token.startswith(('-', '$')):
            path = _path(token, cwd)
            if path is not None:
                paths.append(path)
        index += 1
    return paths


def modules_touched(project_dir, payload):
    """Return normalized module ids in first-seen order, parent before child."""
    project = Path(project_dir).resolve()
    tool, raw = payload.get('tool_name'), payload.get('tool_input') or {}
    if tool in ('Bash', 'PowerShell'):
        paths = _shell_paths(project, payload, raw) if isinstance(raw, dict) else []
        reverse = False
    else:
        if tool == 'Read' and isinstance(raw, dict):
            originals = [raw.get('file_path')]
        else:
            originals = edited_paths(payload)
        cwd = payload.get('cwd') or str(project)
        paths = [_path(path, cwd) for path in originals]
        reverse = True
    modules = []
    for path in paths:
        if path is None:
            continue
        try:
            relative = path.relative_to(project).as_posix()
        except ValueError:
            continue
        ids = _direct_modules(project, path)
        if reverse:
            for sub in lookup_submodules(project, relative):
                # The index can outlive a module; validate both marker levels.
                ids.extend(_direct_modules(project, project / 'projects/modules' / sub / 'submodule.yaml'))
        for module in ids:
            if module not in modules:
                modules.append(module)
    return modules


def _event(project, payload, module, cycle, outcome, chars=0, **extra):
    row = {'ts':datetime.now(timezone.utc).isoformat(timespec='seconds'),
           'type':'module_memory_injected', 'session_id':_harness.session_id(payload),
           'cycle':cycle, 'module':module, 'tool':payload.get('tool_name', ''),
           'chars':chars, 'outcome':outcome, **extra}
    if payload.get('agent_id'):
        row['agent_id'] = payload['agent_id']
    try:
        append_line(state_dir_of(project) / 'events.jsonl', event_json(row))
    except Exception as error:
        print('[warn] module memory event: ' + type(error).__name__, file=sys.stderr)


def _load(project):
    data = read_json_object(state_dir_of(project) / STATE_FILE, {})
    if not isinstance(data, dict) or not isinstance(data.get('sessions'), dict):
        data = {'__schema__':'workframe.module-memory-state.v1','sessions':{}}
    return data


def _thread(state, payload):
    # Keep only the most recently touched sessions, as role-memory dedup does;
    # a pruned session that touches the module again simply gets it once more.
    sessions, sid = state['sessions'], _harness.session_id(payload)
    session = sessions.pop(sid, None)
    sessions[sid] = session = session if isinstance(session, dict) else {}
    while len(sessions) > MAX_SESSIONS:
        sessions.pop(next(iter(sessions)))
    key = payload.get('agent_id') or 'main'
    record = session.get(key)
    if not isinstance(record, dict) or not isinstance(record.get('delivered'), list):
        record = {'cycle':0,'delivered':[]}
        session[key] = record
    return record


def _compose(project, payload, modules, record, budget, mark=True):
    parts, used = [], 0
    for module in modules:
        if module in record['delivered']:
            _event(project, payload, module, record['cycle'], 'skipped_dedup')
            continue
        path = project / 'projects/modules' / module / 'module-memory.md'
        rel = path.relative_to(project).as_posix()
        try:
            text = path.read_text(encoding='utf-8-sig').replace('\r\n','\n').replace('\r','\n').strip()
        except FileNotFoundError:
            _event(project, payload, module, record['cycle'], 'missing')
            continue
        except (OSError, UnicodeError):
            _event(project, payload, module, record['cycle'], 'read_failed')
            continue
        if not text:
            _event(project, payload, module, record['cycle'], 'missing')
            continue
        block = (f'[workframe] 模块记忆——{module}｜来源：{rel}\n'
                 '这是作业提醒，不是产品口径；与文档冲突时信文档。\n'
                 f'条目内相对路径以 {path.parent.relative_to(project).as_posix()} 为基准；projects/... 从项目根解析。\n'
                 + text)
        room = budget - used - (2 if parts else 0)
        full = u16len(block) <= room
        if not full:
            block = f'[workframe] 模块记忆——{module}：未投全文，动手前读 {rel}'
        placed = u16len(block) <= room
        if placed:
            parts.append(block)
            used += u16len(block) + (2 if len(parts)>1 else 0)
        if full and placed and mark:
            record['delivered'].append(module)
        outcome = ('injected' if mark else 'lock_timeout') if full and placed else 'over_budget'
        _event(project, payload, module, record['cycle'], outcome,
               u16len(block) if placed else 0)
    return '\n\n'.join(parts) or None


def deliver(project_dir, payload, budget=None):
    """Generate bounded reminders; failures do not block the completed tool."""
    if not isinstance(payload, dict):
        return None
    project = Path(project_dir).resolve()
    budget = DELIVER_BUDGET if budget is None else max(0, budget)
    modules = []
    try:
        # Most modules have no reminder file; skip them before taking the lock so
        # ordinary reads write neither state nor an event per touch.
        modules = [module for module in modules_touched(project, payload)
                   if (project / 'projects/modules' / module / 'module-memory.md').is_file()]
        if not modules:
            return None
        state_path = state_dir_of(project) / STATE_FILE
        if not _harness.session_id(payload):
            return _compose(project, payload, modules, {'cycle':0,'delivered':[]}, budget, mark=False)
        try:
            with FileLock(state_path.with_suffix('.lock'), timeout=1.0):
                state = _load(project)
                result = _compose(project, payload, modules, _thread(state,payload), budget)
                atomic_write(state_path, json.dumps(state, ensure_ascii=False) + '\n')
                return result
        except TimeoutError:
            return _compose(project, payload, modules, {'cycle':0,'delivered':[]}, budget, mark=False)
    except Exception as error:
        for module in modules:
            _event(project, payload, module, 0, 'error', error=type(error).__name__)
        print('[warn] module memory delivery: ' + type(error).__name__, file=sys.stderr)
        return None


def rotate_after_compact(project_dir, payload):
    """Clear only this session's main-thread records; do not eagerly redeliver."""
    if payload.get('agent_id') or not _harness.session_id(payload) or _harness.rollout_forked(payload.get('transcript_path')):
        return None
    project = Path(project_dir)
    state_path = state_dir_of(project) / STATE_FILE
    try:
        with FileLock(state_path.with_suffix('.lock'), timeout=1.0):
            state = _load(project)
            record = _thread(state, payload)
            record['cycle'] += 1
            record['delivered'] = []
            atomic_write(state_path, json.dumps(state, ensure_ascii=False) + '\n')
    except Exception as error:
        print('[warn] module memory compact: ' + type(error).__name__, file=sys.stderr)
    return None
