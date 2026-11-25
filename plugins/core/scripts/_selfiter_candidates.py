#!/usr/bin/env python3
"""Select self-iteration candidates: what is waiting, and why it is (not) selected.

Read only. A candidate is one fact (a correction, a notes entry, a failed
re-check); deciding which candidates belong together is the model's work, so
nothing here requires two identically worded facts. Selection asks only whether
enough is waiting or something has waited long enough. Every dropped row keeps a
reason, so an empty result can be explained.

The caller owns progress, first-seen times, old proposals and manual reviews and
passes them in. Evidence text is untrusted data and grants no write permission.
"""

from datetime import datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

from _notes_entries import BACKLOG_CHARS, scan_notes
from _selfiter_evidence import _Inputs, _events, _provenance
from _selfiter_policy import digest, parse_time, problem_key
from _state_io import memory_dir_of, state_dir_of

CORRECTION_COUNT = 3        # waiting corrections, project wide
WAIT_DAYS = 14              # the oldest waiting candidate
HISTORY_LIMIT = 40          # earlier corrections shown for comparison, per scope
FUNNEL_OF = {'user_correction': 'correction', 'notes': 'notes', 'repair': 'repair'}
MAX_LOOKS = 3               # the first analysis and at most two more
MAX_MISSES = 3              # consecutive analyses that left it without a conclusion
OUTCOME_LABELS = {  # what happened to a row, for people
    'selectable': '可选入', 'seen': '看过、可随包再看', 'seen_finished': '看过、不再看', 'consumed': '旧版已处理',
    'disposed': '已处置', 'duplicate': '内容重复', 'paused': '连续没有结论、暂停', 'manual_review': '转人工',
    'previously_rejected': '用户驳回过', 'awaiting_old_proposal': '旧提案待定', 'empty_summary': '没有内容',
    'acknowledged': '用户确认过', 'invalid_summary': '内容不是文字', 'unreadable': '文件读不出',
    'main_session_memory': '落点在主会话记忆', 'unresolved_scope': '归属对不上', 'unsafe_target': '目标路径不可用'}
REPEAT_RE = re.compile(r'重复强调|再次强调|第\s*(?![一1]\s*次)[一二三四五六七八九十\d]+\s*次|又一次')
DOC_RE = re.compile(r'[^\s`"\'（）()\[\]|,，。；;：:<>*“”「」、]+\.md(?![A-Za-z0-9])')
LINK_RE = re.compile(r'\[\[([^\]|#]+)')
OLD_ENTRY_RE = re.compile(r'^(?:###\s+|- \d{4}-\d{2}-\d{2}:)')  # what the previous release took as one entry


def _old_blocks(text):
    """(content hash, first line, last line) of the previous release's entries: an id it
    settled covers exactly those lines, not similar text elsewhere."""
    start, lines = None, text.replace('\r\n', '\n').split('\n')
    for index, line in enumerate(lines + ['# ']):
        begins = bool(OLD_ENTRY_RE.match(line))
        if start is not None and (begins or re.match(r'^#{1,2}\s', line)):
            content = '\n'.join(lines[start:index]).strip()
            yield hashlib.sha256(content.encode('utf-8')).hexdigest(), start + 1, index
            start = None
        if begins:
            start = index


def _old_ids(blocks, entry):
    """Settled old ids whose lines, taken together, hold every line of this entry."""
    covered = {line for _, first, last in blocks for line in range(first, last + 1)}
    if not covered.issuperset(range(entry['line'], entry['end'] + 1)):
        return []
    return [i for ids, first, last in blocks if first <= entry['end'] and entry['line'] <= last for i in ids]


def _number(record, key, default=0):
    value = record.get(key, default)
    return value if type(value) is int else default


def _when(value):
    try:
        return parse_time(value)
    except (ValueError, TypeError, AttributeError):
        return None


def _plugin_roots(inputs, state):
    # Installed plugin roots may live inside a project; prohibit their aliases too.
    for marker in inputs.children(state, 'plugin-root*.txt'):
        text = inputs.read(inputs.rel(marker))
        if text and text.strip():
            plugin = Path(text.strip())
            inputs.plugin_roots.append((plugin if plugin.is_absolute() else inputs.root / plugin).resolve())


def correction_keys(row):
    """Stable problem keys of one correction: exact facts and explicit links only."""
    target = row.get('target_path')
    keys = {problem_key(row)}
    for link in (row.get('topic_key'), row.get('source_entry_id')):
        if link:
            keys.add(digest(['user_correction', row.get('scope'), target, link]))
    return keys


def correction_rows(project):
    """[(id, event)] of every correction that says something, whether or not anybody has dealt with it."""
    inputs = _Inputs(project)
    return [(identity, row) for identity, row, _ in _events(inputs, state_dir_of(inputs.root))
            if row.get('type') == 'user_correction' and isinstance(row.get('summary'), str) and row['summary'].strip()]


def _scope_key(inputs, memory, scope, module):
    """A real role directory, or a real module; anything else is only diagnosed."""
    if scope == 'module':
        if not isinstance(module, str) or not module:
            return None
        base = 'projects/modules/' + module
        if inputs.safe(base) is None:
            return None
        marked = any((inputs.root / base / name).is_file() for name in ('module.yaml', 'submodule.yaml'))
        return 'module:' + module if marked else None
    if not isinstance(scope, str) or not re.fullmatch(r'[\w-]+', scope) or not (memory / scope).is_dir():
        return None
    return scope if inputs.safe(inputs.rel(memory / scope)) is not None else None


def target_scope(inputs, memory, target):
    """The scope that owns an asset path, resolved from the path and never guessed."""
    if not isinstance(target, str) or not target:
        return None
    base = inputs.rel(memory) + '/'
    if target.startswith(base):
        parts = target[len(base):].split('/')
        return _scope_key(inputs, memory, parts[0], None) if len(parts) == 2 else None
    found = re.fullmatch(r'projects/modules/(.+)/module-memory\.md', target)
    return _scope_key(inputs, memory, 'module', found.group(1)) if found else None


def collect_candidates(project, progress=None, *, repairs=(), decisions=(), manual_ids=(),
                       first_seen=None, now=None):
    """Return {candidates, trigger, funnel, history, undated, diagnostics, inputs}; never writes.

    Outcomes in `funnel[source]` add up to its `total`. `status` of a candidate is
    'new', or 'seen' when it already has a conclusion and may only ride along.
    """
    now = now or datetime.now(timezone.utc)
    progress, first_seen = progress or {}, first_seen or {}
    consumed, book = set(progress.get('consumed_ids', ())), progress.get('evidence', {})
    if not isinstance(book, dict):
        raise ValueError('progress.json: evidence must be an object')
    manual_ids = set(manual_ids)
    inputs = _Inputs(project)
    state, memory = state_dir_of(inputs.root), memory_dir_of(inputs.root)
    _plugin_roots(inputs, state)
    funnel = {source: {'total': 0} for source in ('correction', 'notes', 'repair')}
    candidates, history, undated = [], {}, []
    decided = {}
    for decision in decisions:
        used = list(decision.get('source_ids') or ())
        for more in (decision.get('additional_evidence') or {}).values():
            used += more
        if decision.get('status') in ('pending', 'rejected'):
            decided.update((identity, decision['status']) for identity in used)

    def count(source, outcome):
        funnel[source]['total'] += 1
        funnel[source][outcome] = funnel[source].get(outcome, 0) + 1

    def offer(source, row):
        """Apply progress to one otherwise selectable candidate."""
        identity = row['id']
        names = {identity, *row.pop('old_ids', ())}
        if names & consumed:
            return count(source, 'consumed')
        old = [decided[i] for i in names if i in decided]
        if old:
            return count(source, 'previously_rejected' if 'rejected' in old else 'awaiting_old_proposal')
        if names & manual_ids:
            return count(source, 'manual_review')
        row['prior_decisions'] = [
            {'status': d.get('status'), 'reason': d.get('reason'), 'operation': d.get('operation')}
            for d in decisions if d.get('status') in ('pending', 'rejected')
            and set(d.get('problem_keys') or ()) & set(row['keys'])]
        if row['when'] is None:
            undated.append(identity)
            row['when'] = _when(first_seen.get(identity)) or now
        record = book.get(identity)
        record = record if isinstance(record, dict) else {}
        context = record.get('context')
        changed = isinstance(context, dict) and any(
            inputs.file_hash(path) != old for path, old in context.items())
        if record.get('state') == 'paused' and not changed:
            return count(source, 'paused')
        if record.get('state') == 'seen':
            if _number(record, 'looks', 1) >= MAX_LOOKS:
                return count(source, 'seen_finished')
            count(source, 'seen')
            row.update(status='seen', context_changed=changed)
        else:
            count(source, 'selectable')
            row['status'] = 'new'
        candidates.append(row)

    repeated = []
    for identity, row, location in _events(inputs, state, repeated):
        if row.get('type') != 'user_correction':
            continue
        scope, summary = row.get('scope'), row.get('summary') or ''
        if not isinstance(summary, str):
            count('correction', 'invalid_summary')
            inputs.diagnostic('event_invalid', location['path'], 'line %s: summary is not text' % location['line'])
            continue
        summary = summary.strip()
        target = row.get('target_path')
        if scope in ('role', '<role>') and isinstance(row.get('entry_key'), str):
            scope = row['entry_key'].split(':', 1)[0]  # an old placeholder; the key names the real scope
        key = _scope_key(inputs, memory, scope, row.get('module'))
        if summary and key:
            history.setdefault(key, []).append({'id': identity, 'date': str(row.get('ts'))[:10], 'summary': summary})
        if identity in consumed:
            count('correction', 'consumed')
        elif not summary:
            count('correction', 'empty_summary')
        elif scope == 'main':
            count('correction', 'main_session_memory')  # lands outside the project by design
        elif key is None:
            count('correction', 'unresolved_scope')
            inputs.diagnostic('unresolved_scope', location['path'],
                              'line %s: scope %r module %r' % (location['line'], scope, row.get('module')))
        elif target is not None and inputs.safe(target) is None:
            count('correction', 'unsafe_target')
        else:
            offer('correction', {
                'id': identity, 'type': 'user_correction', 'scope': key, 'text': summary,
                'target_path': target, 'date': str(row.get('ts'))[:10], 'quote': row.get('quote'),
                'repeat': bool(REPEAT_RE.search(summary)), 'when': _when(row.get('ts')),
                'keys': sorted(correction_keys(row)), 'provenance': 'user_correction_event'})

    for _, row in repeated:
        if row.get('type') == 'user_correction':
            count('correction', 'duplicate')

    lines = {}
    for path in inputs.children(memory, '*/notes.md'):
        rel, scope = inputs.rel(path), path.parent.name
        if inputs.safe(rel) is None:
            continue
        text = inputs.read(rel)
        if text is None:
            count('notes', 'unreadable')
            continue
        found, seen, settled = scan_notes(text), set(), consumed | set(decided) | manual_ids
        old_blocks = [({digest(['notes', rel, h]), h}, first, last) for h, first, last in _old_blocks(text)]
        old_blocks = [b for b in old_blocks if b[0] & settled]
        for kind, n in found['lines'].items():
            lines[kind] = lines.get(kind, 0) + n
        lines['unrecognized_chars'] = lines.get('unrecognized_chars', 0) + found['unrecognized_chars']
        for entry in found['entries']:
            content_hash = hashlib.sha256(entry['text'].encode('utf-8')).hexdigest()
            identity = digest(['notes', rel, content_hash])
            if entry['disposed']:
                count('notes', 'disposed')
            elif identity in seen:
                count('notes', 'duplicate')
            else:
                day = entry['date']
                offer('notes', {
                    'id': identity, 'type': 'notes', 'scope': scope, 'text': entry['text'],
                    'old_ids': [content_hash] + _old_ids(old_blocks, entry),
                    'source_path': rel, 'date': day.isoformat() if day else None,
                    'when': datetime.combine(day, time(), timezone.utc) if day else None,
                    'keys': [digest(['memory_backlog', rel, content_hash])],
                    'provenance': _provenance(entry['text'])})
            seen.add(identity)
    funnel['notes']['lines'] = lines

    for record in repairs:
        identity = digest(['repair', record['op_id']])
        key = target_scope(inputs, memory, record.get('target_path'))
        if record.get('acknowledged'):
            count('repair', 'acknowledged')
        elif key is None:
            count('repair', 'unresolved_scope')
            inputs.diagnostic('unresolved_scope', record.get('target_path'), 'failed re-check ' + str(record['op_id']))
        else:
            offer('repair', {
                'id': identity, 'old_ids': record.get('old_ids', []), 'type': 'repair', 'scope': key, 'target_path': record['target_path'],
                'text': json.dumps({'written': record.get('new_text'), 'came_back': record.get('recurrence')},
                                   ensure_ascii=False),
                'date': str(record.get('failed_at'))[:10], 'when': _when(record.get('failed_at')),
                'keys': sorted(record.get('problem_keys') or ()), 'provenance': 'failed_recheck'})

    fresh = [c for c in candidates if c['status'] == 'new']
    scopes = {c['scope'] for c in fresh}
    for c in list(candidates):
        if c['status'] == 'seen' and not (c['scope'] in scopes or c['context_changed']):
            candidates.remove(c)  # nothing around it moved; it keeps waiting without spending a look
    reasons, chars = [], {}
    for c in fresh:
        if c['type'] == 'notes':
            chars[c['scope']] = chars.get(c['scope'], 0) + len(c['text'])
    reasons += ['notes:%s=%d' % item for item in sorted(chars.items()) if item[1] >= BACKLOG_CHARS]
    corrections = [c for c in fresh if c['type'] == 'user_correction']
    if len(corrections) >= CORRECTION_COUNT:
        reasons.append('corrections=%d' % len(corrections))
    if any(c['repeat'] for c in corrections):
        reasons.append('repeat_marked')
    if any(c['type'] == 'repair' for c in fresh):
        reasons.append('repair')
    oldest = min((c['when'] for c in fresh), default=None)
    if oldest and now - oldest >= timedelta(days=WAIT_DAYS):
        reasons.append('oldest=' + oldest.date().isoformat())
    return {'candidates': candidates, 'trigger': reasons, 'funnel': funnel, 'undated': sorted(undated),
            'history': history,
            'diagnostics': inputs.diagnostics, 'inputs': inputs}


def changed_files(project, frozen):
    """Of {path: hash at selection time}, the paths that differ now; reads the way selection did."""
    inputs = _Inputs(project)
    return [rel for rel, sha in sorted(frozen.items()) if inputs.file_hash(rel) != sha]


def present_notes(project, rels):
    """Ids of the entries the given notes files hold right now."""
    inputs = _Inputs(project)
    return {digest(['notes', rel, hashlib.sha256(entry['text'].encode('utf-8')).hexdigest()])
            for rel in rels for entry in scan_notes(inputs.read(rel) or '')['entries'] if not entry['disposed']}


def _scope_context(inputs, memory, key):
    """Of one scope: assets the model may propose changing, read-only rules (readable files only),
    and the state of every file it depends on, missing ones included."""
    if key.startswith('module:'):
        base = 'projects/modules/' + key[7:] + '/'
        targets, authority = [base + 'module-memory.md'], [base + 'overview.md']
    else:
        targets, authority = [inputs.rel(memory) + '/' + key + '/MEMORY.md'], []
    if key != 'shared':
        authority.append(inputs.rel(memory) + '/shared/MEMORY.md')
    readable = [[rel for rel in group if inputs.read(rel) is not None] for group in (targets, authority)]
    # The hash of the very bytes whose text is given; a second read could see another file.
    return (*readable, {rel: inputs.sources[rel]['sha256'] if rel in inputs.sources else inputs.file_hash(rel)
                        for rel in targets + authority})


def _skill_files(inputs):
    from _harness import project_skills_dir
    return [inputs.rel(p) for p in inputs.children(inputs.root / project_skills_dir(inputs.root), '*/SKILL.md')]


def _named_documents(inputs, row, skills):
    """(existing, missing) project documents and skills the evidence itself names; read only."""
    text = row['text'] + ' ' + str(row.get('target_path') or '')
    names = DOC_RE.findall(text) + [link + ('' if link.endswith('.md') else '.md') for link in LINK_RE.findall(text)]
    names += [rel for rel in skills if re.search(
        r'(?<![\w-])' + re.escape(rel.split('/')[-2]) + r'(?![\w-])', text)]
    state = inputs.rel(state_dir_of(inputs.root)) + '/'
    out, missing = [], []
    for name in names:
        rel = name.replace('\\', '/')
        rel = rel[2:] if rel.startswith('./') else rel
        if rel in out or rel.startswith((state, '/', '../')) or ':' in rel or rel.endswith('/notes.md'):
            continue
        try:
            present = (inputs.root / rel).is_file()
        except (OSError, ValueError):
            present = False
        if not present:
            if rel not in missing and not any(p in ('', '.', '..') for p in rel.split('/')) and inputs.safe(rel):
                missing.append(rel)
        elif inputs.safe(rel) is not None and inputs.read(rel) is not None:
            out.append(rel)
    return out, missing


def _skill_index(inputs, skills):
    index = []
    for rel in skills:
        found = re.search(r'^description:\s*(.+)$', inputs.read(rel) or '', re.M)
        index.append({'name': rel.split('/')[-2], 'path': rel,
                      'description': (found.group(1) if found else '')[:160]})
    return index


def build_input(selection, max_chars):
    """Pack candidates one by one, oldest first; earlier corrections and named documents fill what is left.

    Returns (bundle | None, oversized, left). `oversized` holds candidates whose
    own required context exceeds the whole budget: they need a person, and do not
    stop shorter ones behind them. `left` counts what only lacked room this time.
    Nothing is truncated. Short ids (E01…) exist only inside one bundle.
    """
    inputs, candidates = selection['inputs'], selection['candidates']
    memory = memory_dir_of(inputs.root)
    skill_files = _skill_files(inputs)
    skills, contexts = _skill_index(inputs, skill_files), {}
    for c in candidates:
        if c['scope'] not in contexts:
            contexts[c['scope']] = _scope_context(inputs, memory, c['scope'])

    def model_for(rows, documents=(), history=None):
        rows = sorted(rows, key=lambda r: (r['scope'], r['when'], r['id']))
        scopes = sorted({r['scope'] for r in rows})
        targets = sorted({rel for s in scopes for rel in contexts[s][0]}
                         | (set(documents) & set(skill_files)))
        authority = sorted(({rel for s in scopes for rel in contexts[s][1]} | set(documents)) - set(targets))
        ids = {r['id']: 'E%02d' % n for n, r in enumerate(rows, 1)}
        evidence = [{k: v for k, v in dict(r, id=ids[r['id']]).items() if k in (
            'id', 'type', 'scope', 'text', 'target_path', 'date', 'quote', 'repeat', 'source_path',
            'provenance', 'prior_decisions') and v not in (None, False, [])} for r in rows]
        model = {'evidence': evidence,
                 'correction_history': history or {},
                 'targets': {rel: inputs.sources[rel]['text'] for rel in targets},
                 'authority': {rel: inputs.sources[rel]['text'] for rel in authority},
                 'skill_index': skills}
        return rows, ids, model

    def size(rows, documents=(), history=None):
        return len(json.dumps(model_for(rows, documents, history)[2], ensure_ascii=False))

    order = sorted(candidates, key=lambda c: (c['status'] == 'seen', c['type'] != 'repair',
                                              not c.get('repeat'), c['when'], c['id']))
    chosen, oversized, left = [], [], []
    for c in order:
        if size([c]) > max_chars:
            paths = sorted(contexts[c['scope']][0] + contexts[c['scope']][1])
            oversized.append(dict(c, required_chars=size([c]), required_paths=paths,
                                  required_context={rel: inputs.sources[rel] for rel in paths}))
        elif size(chosen + [c]) <= max_chars:
            chosen.append(c)
        else:
            left.append(c)
    left = [c for c in left if c['status'] == 'new']
    oversized = [c for c in oversized if c['status'] == 'new']
    if not any(c['status'] == 'new' for c in chosen):
        return None, oversized, left
    given, history = {c['id'] for c in chosen}, {}
    for key in sorted({c['scope'] for c in chosen}):
        facts = [{k: v for k, v in h.items() if k != 'id'}
                 for h in selection['history'].get(key, ()) if h['id'] not in given][-HISTORY_LIMIT:]
        while facts and size(chosen, (), dict(history, **{key: facts})) > max_chars:
            facts = facts[1:]  # the oldest first; whole facts only
        if facts:
            history[key] = facts
    documents, omitted, named = [], [], {c['id']: _named_documents(inputs, c, skill_files) for c in chosen}
    for c in order:
        for rel in named.get(c['id'], ((), ()))[0]:
            if rel in documents or rel in omitted or rel in contexts[c['scope']][2]:
                continue
            (documents if size(chosen, documents + [rel], history) <= max_chars else omitted).append(rel)
    rows, ids, model = model_for(chosen, documents, history)
    evidence = []
    for r in rows:
        context = dict(contexts[r['scope']][2])
        context.update((rel, inputs.sources[rel]['sha256']) for rel in named[r['id']][0] if rel in documents)
        context.update(dict.fromkeys(named[r['id']][1]))  # named but absent: its arrival is a change
        evidence.append(dict({k: v for k, v in r.items() if k != 'when'}, short_id=ids[r['id']], context=context))
    packing = {}
    for outcome, group in (('analysed', chosen), ('left_for_next_run', left), ('to_a_person', oversized)):
        for c in group:
            source = packing.setdefault(FUNNEL_OF[c['type']], {})
            source[outcome] = source.get(outcome, 0) + 1
    files = sorted(set(model['targets']) | set(model['authority']))
    bundle = {'version': 2, 'evidence': evidence, 'model': model,
              'sources': {rel: dict(inputs.sources[rel]) for rel in files},
              'targets': sorted(model['targets']), 'documents_left_out': omitted, 'packing': packing,
              'fingerprint': digest(sorted(r['id'] for r in rows))[:16],
              'trigger': selection['trigger'], 'funnel': selection['funnel']}
    return bundle, oversized, left
