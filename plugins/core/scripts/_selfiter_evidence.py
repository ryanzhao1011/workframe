#!/usr/bin/env python3
"""Freeze read-only self-iteration inputs; never infer permission from evidence.

The controller owns consumed IDs, proposal suppression, job records and the
input-size limit. Missing YAML support disables blocked-task evidence. Exact
summary groups are review candidates, not proof of semantic equivalence (event
summaries may already be truncated by their producer). No source is truncated.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
import re

from _selfiter_policy import digest, event_id, problem_key
from _state_io import SPILL_SUFFIX, archive_dir_of, memory_dir_of, state_dir_of
from _yaml_probe import safe_load


class _Inputs:
    def __init__(self, project):
        self.root = Path(project).resolve()
        self.sources = {}
        self.targets = {}
        self.diagnostics = []
        self.plugin_roots = [Path(__file__).resolve().parents[1]]

    def diagnostic(self, code, path, detail):
        self.diagnostics.append({'code': code, 'path': str(path), 'detail': detail})

    def safe(self, value):
        """Only canonical project-relative paths; reject aliases, even in-root ones.

        Rejecting all symlink/junction ancestors also handles missing leaf targets.
        This is snapshot-time validation, not protection from later filesystem races.
        """
        if not isinstance(value, str) or not value:
            self.diagnostic('unsafe_path', value, 'expected a nonempty relative path')
            return None
        rel = value.replace('\\', '/')
        parts = rel.split('/')
        if (PureWindowsPath(value).drive or PurePosixPath(rel).is_absolute()
                or any(p in ('', '.', '..') or ':' in p or p.endswith((' ', '.')) for p in parts)
                or parts[0].casefold() == 'plugins'):
            self.diagnostic('unsafe_path', value, 'absolute, traversal, alias or plugin path')
            return None
        path = self.root.joinpath(*parts)
        try:
            cursor = self.root
            for part in parts:
                cursor = cursor / part
                if cursor.is_symlink() or getattr(cursor, 'is_junction', lambda: False)():
                    raise ValueError('symlink or junction ancestor')
            resolved = path.resolve()
            if not resolved.is_relative_to(self.root):
                raise ValueError('outside project')
            if resolved.relative_to(self.root).as_posix() != rel:
                raise ValueError('noncanonical filesystem spelling')
            if any(resolved.is_relative_to(p) for p in self.plugin_roots):
                raise ValueError('inside a plugin root')
        except (OSError, ValueError, RuntimeError) as exc:
            self.diagnostic('unsafe_path', value, str(exc))
            return None
        return rel

    def read(self, value, target=False):
        rel = self.safe(value)
        if rel is None:
            return None
        if rel not in self.sources:
            try:
                raw = (self.root / rel).read_bytes()
                snapshot = {'text': raw.decode('utf-8'), 'sha256': hashlib.sha256(raw).hexdigest()}
            except FileNotFoundError:
                snapshot = {'text': None, 'sha256': None}
            except (OSError, UnicodeError, ValueError) as exc:
                self.diagnostic('source_unreadable', rel, str(exc))
                return None  # Unreadable is not a missing file/new target.
            self.sources[rel] = snapshot
        if target:
            self.targets[rel] = dict(self.sources[rel])
        return self.sources[rel]['text']

    def rel(self, path):
        return Path(path).relative_to(self.root).as_posix()

    def children(self, directory, pattern):
        rel = self.rel(directory)
        if self.safe(rel) is None:
            return []
        try:
            if pattern == '**/*.md':
                pending, files = [directory], []
                while pending:
                    current = pending.pop()
                    if not current.exists():
                        continue
                    for entry in current.iterdir():
                        if self.safe(self.rel(entry)) is None:
                            continue
                        if entry.is_dir():
                            pending.append(entry)
                        elif entry.suffix == '.md':
                            files.append(entry)
                return sorted(files)
            return sorted(directory.glob(pattern))
        except OSError as exc:
            self.diagnostic('source_unreadable', rel, str(exc))
            return []


def _events(inputs, state):
    files = [state / 'events.jsonl'] + inputs.children(state, 'events.*' + SPILL_SUFFIX)
    seen = set()
    rows = []
    for path in files:
        rel = inputs.rel(path)
        text = inputs.read(rel)
        for number, raw in enumerate((text or '').splitlines(), 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise ValueError('event must be an object')
            except ValueError as exc:
                inputs.diagnostic('event_invalid', rel, f'line {number}: {exc}')
                continue
            identity = event_id(row)
            if identity in seen or row.get('job_id'):
                continue
            seen.add(identity)
            rows.append((identity, row, {'path': rel, 'line': number, 'raw': raw}))
    return rows


def _board(inputs):
    text = inputs.read('projects/board.yaml')
    if text is None:
        inputs.diagnostic('board_missing', 'projects/board.yaml', 'blocked tasks cannot be checked')
        return {}
    try:
        data = safe_load(text)
    except ImportError as exc:
        inputs.diagnostic('yaml_unavailable', 'projects/board.yaml', str(exc))
        return {}
    except Exception as exc:
        inputs.diagnostic('yaml_invalid', 'projects/board.yaml', str(exc))
        return {}
    tasks = data.get('tasks') if isinstance(data, dict) else None
    if not isinstance(tasks, list) or any(not isinstance(t, dict) for t in tasks):
        inputs.diagnostic('board_invalid', 'projects/board.yaml', 'expected tasks list of objects')
        return {}
    by_id = {}
    for task in tasks:
        task_id = task.get('id')
        if not isinstance(task_id, str) or task_id in by_id:
            inputs.diagnostic('board_invalid', 'projects/board.yaml', 'missing or duplicate task id')
            return {}
        by_id[task_id] = task
    return by_id


def _dismissal_baseline(inputs, state):
    """Read the existing problem_threshold dismissal semantics without CLI import.

    check-iteration-trigger's dismissal_baseline cannot be imported here: its
    module replaces stdout/stderr and reads process-global project state on import.
    Keep its relevant contract: latest closed timestamp, same dedup_key only,
    naive timestamps interpreted as UTC, future/invalid values give no suppression.
    """
    text = inputs.read(inputs.rel(state / 'activity-state.json'))
    if text is None:
        return None
    try:
        data = json.loads(text)
        items = data.get('pending_maintenance', [])
        if not isinstance(items, list):
            raise ValueError('pending_maintenance must be a list')
    except (ValueError, AttributeError) as exc:
        inputs.diagnostic('state_invalid', inputs.rel(state / 'activity-state.json'), str(exc))
        return None
    latest = None
    now = datetime.now(timezone.utc)
    for item in items:
        if not isinstance(item, dict) or item.get('dedup_key') != 'problem_threshold' or item.get('status') != 'closed':
            continue
        try:
            ts = datetime.fromisoformat(item.get('closed_at').replace('Z', '+00:00'))
            ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
        except (AttributeError, TypeError, ValueError):
            continue
        if ts <= now and (latest is None or ts > latest):
            latest = ts
    return latest


def _content_lines(text):
    # Same content-line definition as the existing backlog scanner; threshold
    # here is >=80 per the automatic-consumer contract, not an entry count.
    return sum(bool(s) and not s.startswith(('#', '>', '---', '<!--', '_（', '_('))
               for s in (line.strip() for line in text.splitlines()))


def _notes_blocks(text):
    """Recognize the two public notes forms, keeping continuation text in full.

    Unrecognized content contributes to health counts, not automatic evidence.
    Old annotations are context only; consumed content IDs are authoritative.
    """
    lines = text.splitlines(keepends=True)
    start = None
    for index, line in enumerate(lines):
        if re.match(r'^(?:###\s+|- \d{4}-\d{2}-\d{2}:)', line):
            if start is not None:
                yield start + 1, ''.join(lines[start:index])
            start = index
        elif start is not None and re.match(r'^#{1,2}\s', line):
            yield start + 1, ''.join(lines[start:index])
            start = None
    if start is not None:
        yield start + 1, ''.join(lines[start:])


def _provenance(text):
    # Report explicit labels; this is not independent verification of the claim.
    if re.search(r'\[亲测(?:[^\]]*)\]|来源[：:]\s*亲测', text):
        return 'self_tested_claim'
    if re.search(r'\[用户确认\]|来源[：:]\s*用户确认', text):
        return 'user_confirmed_claim'
    return 'unknown'


def _context(inputs, roles, modules, authorities):
    memory = memory_dir_of(inputs.root)
    inputs.read(inputs.rel(memory / 'shared/MEMORY.md'))
    roles.add('shared')
    for role in sorted(roles):
        if not re.fullmatch(r'[\w-]+', role) or role in ('main', 'module'):
            continue
        for name in ('notes.md', 'MEMORY.md', 'notes-archive.md'):
            inputs.read(inputs.rel(memory / role / name), target=(name == 'notes.md'
                        or (name == 'MEMORY.md' and role != 'shared')))
        archive = archive_dir_of(inputs.root) / role
        for path in inputs.children(archive, '**/*.md'):
            inputs.read(inputs.rel(path))
    for module in sorted(modules):
        if not isinstance(module, str):
            continue
        base = 'projects/modules/' + module
        if inputs.safe(base) is None:
            continue
        for name in ('overview.md', 'module-memory.md'):
            rel = base + '/' + name
            text = inputs.read(rel, target=name == 'module-memory.md')
            # Only explicit local links, one hop, provide associated authority.
            links = re.findall(r'\[\[([^\]|#]+)', text or '')
            links += re.findall(r'\]\(([^\s)#]+)', text or '')
            for link in links:
                if '://' in link:
                    continue
                linked = link if link.startswith('projects/') else base + '/' + link
                if not PurePosixPath(linked).suffix:
                    linked += '.md'
                if linked.startswith(base + '/') and inputs.safe(linked) is not None:
                    inputs.read(linked)
    for authority in sorted(authorities):
        inputs.read(authority)  # Authoritative documents never become automatic targets.


def collect_evidence(project, consumed_ids=(), *, verification_failures=()) -> dict:
    """Return {evidence, sources, targets, fingerprint, diagnostics}, without writes.

    consumed_ids accepts event IDs or notes evidence/content IDs. Corrections
    have one evidence row per event, so controller source_ids can be persisted
    directly; consumed history can establish recurrence but is never re-emitted.
    Notes evidence IDs are digest(["notes", relative_path, content_sha256]); the
    content hash ignores surrounding blank space and normalizes CRLF. Threshold
    uses *unconsumed* recognized content lines >=80, never moves/deletes notes.

    verification_failures is the controller's iterable of persisted records with
    result="fail" (job_id is allowed here). The collector deliberately does not
    search ordinary events for these failures or guess a controller state file.
    Explicit topic_key/source_entry_id can associate corrections across summaries;
    otherwise policy.problem_key groups exact normalized summary snapshots only.
    All correction groups remain semantic-review candidates, never auto-eligible.
    """
    inputs = _Inputs(project)
    consumed = set(consumed_ids)
    state = state_dir_of(inputs.root)
    # Installed plugin roots may live inside a project; prohibit their aliases too.
    for marker in inputs.children(state, 'plugin-root*.txt'):
        text = inputs.read(inputs.rel(marker))
        if text and text.strip():
            plugin = Path(text.strip())
            inputs.plugin_roots.append((plugin if plugin.is_absolute() else inputs.root / plugin).resolve())
    rows = _events(inputs, state)
    baseline = _dismissal_baseline(inputs, state)
    if baseline is not None:
        newer = []
        for identity, row, location in rows:
            if row.get('type') not in ('user_correction', 'task_blocked'):
                newer.append((identity, row, location))
                continue
            try:
                ts = datetime.fromisoformat(row.get('ts').replace('Z', '+00:00'))
                ts = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
            except (AttributeError, TypeError, ValueError):
                inputs.diagnostic('event_timestamp_invalid', location['path'], 'cannot compare event to dismissal baseline')
                continue
            if ts > baseline:
                newer.append((identity, row, location))
        rows = newer
    evidence = []
    groups = {}
    roles, modules, authorities = set(), set(), set()

    def related(row):
        role = row.get('role') or row.get('assigned_to') or row.get('scope')
        if isinstance(role, str) and role not in ('main', 'module'):
            roles.add(role)
        module = row.get('module')
        if isinstance(module, str) and module:
            modules.add(module)
        path = row.get('target_path')
        if isinstance(path, str) and path:
            authorities.add(path)

    for identity, row, location in rows:
        if row.get('type') != 'user_correction':
            continue
        target = row.get('target_path')
        if target is not None and inputs.safe(target) is None:
            continue
        association = row.get('topic_key') or row.get('source_entry_id')
        if not association and not str(row.get('summary') or '').strip():
            continue
        key = (digest(['user_correction', row.get('scope'), target, association])
               if association else problem_key(row))
        groups.setdefault(key, []).append((identity, row, location))
    for key, group in sorted(groups.items()):
        ids = sorted(item[0] for item in group)
        if len(ids) < 2:
            continue
        for identity, row, location in group:
            if identity in consumed:
                continue
            evidence.append({'id': identity, 'problem_key': key, 'type': 'user_correction',
                             'event_ids': [identity], 'group_event_ids': ids, 'events': [row],
                             'source_path': location['path'], 'locations': [location],
                             'auto_eligible': False, 'provenance': 'user_correction_event'})
            related(row)

    blocked = [r for r in rows if r[1].get('type') == 'task_blocked' and r[0] not in consumed]
    tasks = _board(inputs) if blocked else {}
    for identity, row, location in blocked:
        task_id = row.get('task_id')
        if not isinstance(task_id, str):
            inputs.diagnostic('event_invalid', location['path'], 'task_id must be a string')
            continue
        task = tasks.get(task_id)
        if not task or task.get('status') != 'blocked':
            continue
        tags = task.get('tags', [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            inputs.diagnostic('board_invalid', 'projects/board.yaml', 'tags must be a list')
            continue
        if 'suspended' in tags:
            continue
        target = task.get('target_path')
        if target is not None and inputs.safe(target) is None:
            continue
        evidence.append({'id': identity, 'problem_key': digest(['task_blocked', row.get('task_id')]),
                         'type': 'task_blocked', 'task_id': row.get('task_id'),
                         'task': task, 'event_ids': [identity], 'events': [row], 'locations': [location]})
        related(task)

    for failure in verification_failures:
        if not isinstance(failure, dict) or failure.get('result') != 'fail':
            continue
        identity = digest(['verification_fail', failure])
        if identity in consumed:
            continue
        target = failure.get('target_path')
        if target is not None and inputs.safe(target) is None:
            continue
        evidence.append({'id': identity, 'problem_key': failure.get('problem_key')
                         or digest(['verification_fail', failure.get('proposal_id'), failure.get('job_id')]),
                         'type': 'verification_fail', 'record': failure})
        related(failure)

    memory = memory_dir_of(inputs.root)
    for path in inputs.children(memory, '*/notes.md'):
        rel = inputs.rel(path)
        text = inputs.read(rel)
        pending = []
        for line, block in _notes_blocks(text or ''):
            content = block.replace('\r\n', '\n').strip()
            content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
            identity = digest(['notes', rel, content_hash])
            if identity in consumed or content_hash in consumed:
                continue
            provenance = _provenance(content)
            pending.append({'id': identity, 'problem_key': digest(['memory_backlog', rel, content_hash]),
                            'type': 'memory_backlog', 'source_path': rel, 'line': line,
                            'text': block, 'content_sha256': content_hash,
                            'content_lines': _content_lines(block), 'role': path.parent.name,
                            'provenance': provenance, 'auto_eligible': False})
        if sum(row['content_lines'] for row in pending) >= 80:
            evidence.extend(pending)
            roles.add(path.parent.name)
    if evidence:
        _context(inputs, roles, modules, authorities)
    evidence = sorted({row['id']: row for row in evidence}.values(), key=lambda row: row['id'])
    fingerprint = digest(sorted(row['id'] for row in evidence))[:16]
    return {'evidence': evidence, 'sources': inputs.sources, 'targets': inputs.targets,
            'fingerprint': fingerprint, 'diagnostics': inputs.diagnostics}


def prepare_model_input(project, bundle, max_chars):
    """Pack complete problem groups, never truncate rules or target text.

    Full source snapshots remain in the returned audit bundle. Model sources
    distinguish current authority from board/event history and superseded
    archives. Targets appear in full exactly once. Oversized necessary context
    is a durable human-review candidate, not a retryable transport failure.
    Unselected problems are neither consumed nor included in the batch identity.
    Start from the cheapest group in each complete context class, then add the
    smallest fitting marginal input until no further group fits. Rank packs by
    most themes, shortest serialized input, then stable problem keys. This is a
    multi-start heuristic, not a globally optimal subset search.
    """
    root = Path(project).resolve()
    memory = memory_dir_of(root).relative_to(root).as_posix() + '/'
    state = state_dir_of(root).relative_to(root).as_posix() + '/'
    archive = archive_dir_of(root).relative_to(root).as_posix() + '/'
    groups = {}
    for row in bundle['evidence']:
        groups.setdefault(row.get('problem_key', row['id']), []).append(row)
    contexts = {}
    for problem, rows in sorted(groups.items()):
        rows.sort(key=lambda row: row['id'])
        roles, modules, authorities, sources = set(), set(), set(), set()
        for row in rows:
            facts = [row, row.get('task', {}), row.get('record', {}), *row.get('events', [])]
            sources.update([row['source_path']] if row.get('source_path') else [])
            sources.update(loc['path'] for loc in row.get('locations', []) if loc.get('path'))
            for fact in facts:
                role = fact.get('assigned_to') or fact.get('scope') or fact.get('role')
                if isinstance(role, str) and role not in ('main', 'module', 'shared'):
                    roles.add(role)
                if fact.get('module'):
                    modules.add(fact['module'])
                if fact.get('target_path'):
                    authorities.add(fact['target_path'])
        targets = {rel: snap for rel, snap in bundle['targets'].items()
                   if (not roles and not modules) or any(rel.startswith(memory + role + '/') for role in roles)
                   or any(rel.startswith('projects/modules/' + module + '/') for module in modules)}
        required = set(targets) | sources | authorities | {memory + 'shared/MEMORY.md'}
        required.update(rel for rel in bundle['sources'] if any(
            rel.startswith('projects/modules/' + module + '/') for module in modules))
        contexts[problem] = (set(targets), required, sources)

    def build(problems):
        rows = [row for problem in sorted(problems) for row in groups[problem]]
        target_paths, required, sources = set(), set(), set()
        for problem in problems:
            group_targets, group_required, group_sources = contexts[problem]
            target_paths.update(group_targets)
            required.update(group_required)
            sources.update(group_sources)
        targets = {rel: snap for rel, snap in bundle['targets'].items() if rel in target_paths}
        # Include target context selected by the collector even for event-only
        # role facts; its source snapshot is represented by the full target.
        model_sources = {}
        context_paths = []
        for rel, snap in bundle['sources'].items():
            historical = (rel == 'projects/board.yaml' or rel.startswith(state)
                          or rel.startswith(archive) or Path(rel).name == 'notes-archive.md')
            if rel in targets:
                model_sources[rel] = {'sha256': snap['sha256'], 'text_in_targets': True}
                context_paths.append(rel)
            elif rel in required and not historical:
                model_sources[rel] = snap
                context_paths.append(rel)
            else:
                model_sources[rel] = {'sha256': snap['sha256'], 'audit_only': True}
                if rel in sources and rel.startswith(state):
                    context_paths.append(rel)  # Selected original rows are in evidence.
                if rel == 'projects/board.yaml' and any(r.get('type') == 'task_blocked' for r in rows):
                    context_paths.append(rel)
        fingerprint = digest(sorted(row['id'] for row in rows))[:16]
        decisions = [p for p in bundle.get('prior_decisions', [])
                     if any(problem in p.get('problem_keys', []) for problem in problems)]
        model = {'evidence': rows, 'sources': model_sources, 'targets': targets,
                 'fingerprint': fingerprint, 'prior_decisions': decisions,
                 'context_policy': 'Full targets/current authority; selected facts replace history. No omitted file grants write authority.'}
        chars = len(json.dumps(model, ensure_ascii=False))
        audit = dict(bundle, evidence=rows, targets=targets, fingerprint=fingerprint,
                     prior_decisions=decisions, context_source_paths=sorted(context_paths))
        return (audit, model), chars, required

    manual = []
    remaining = []
    seeds = {}
    for problem in sorted(groups):
        candidate, chars, required = build([problem])
        if chars > max_chars:
            model = candidate[1]
            rows = model['evidence']
            review_identity = digest({'evidence': rows, 'targets': model['targets'], 'context': {
                rel: snap for rel, snap in model['sources'].items() if not snap.get('audit_only')}})
            manual.append({'fingerprint': review_identity, 'evidence_ids': [r['id'] for r in rows],
                           'problem_key': problem, 'reason': 'required_context_exceeds_input_budget',
                           'required_chars': chars, 'max_chars': max_chars,
                           'required_paths': sorted(required), 'evidence': rows, 'context': model})
            continue
        remaining.append(problem)
        # Snapshots are frozen within this bundle. Target paths, readable
        # context paths and prior decisions identify the complete context cost.
        signature = (tuple(sorted(candidate[1]['targets'])),
                     tuple(candidate[0]['context_source_paths']),
                     tuple(digest(p) for p in candidate[1]['prior_decisions']))
        seed = (chars, problem)
        if signature not in seeds or seed < seeds[signature]:
            seeds[signature] = seed

    selected, best_rank = None, None
    for _, seed in sorted(seeds.values()):
        chosen = [seed]
        packed, chars, _ = build(chosen)
        pending = [problem for problem in remaining if problem != seed]
        while pending:
            best = None
            for problem in pending:
                candidate, candidate_chars, _ = build(chosen + [problem])
                if candidate_chars <= max_chars and (best is None or (candidate_chars, problem) < best[:2]):
                    best = (candidate_chars, problem, candidate)
            if best is None:
                break
            chars, problem, packed = best
            chosen.append(problem)
            pending.remove(problem)
        rank = (-len(chosen), chars, tuple(sorted(chosen)))
        if best_rank is None or rank < best_rank:
            best_rank, selected = rank, packed
        if len(chosen) == len(remaining):
            break
    return selected, manual
