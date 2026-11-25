#!/usr/bin/env python3
"""Check a model's per-topic conclusions against the frozen input; no writes.

Three things stay separate: what the model concluded, what the controller makes
of each operation, and whether an evidence item has a conclusion at all. Quotes
are checked to exist in the frozen text, which does not prove that the quoted
rule really prevents the problem; keyword checks are literal, not semantic.
"""

import re

from _notes_entries import opaque_lines
from _selfiter_evidence import HEDGE_RE
from workframe_doctor import MODULE_MEMORY_MAX_CHARS, ROLE_MEMORY_MAX_CHARS, SHARED_MEMORY_MAX_CHARS

OUTCOMES = ('stale', 'belongs_in_docs', 'fix_tool', 'upstream', 'covered', 'conflict', 'insufficient', 'write')
ELSEWHERE = ('belongs_in_docs', 'fix_tool', 'upstream')  # somebody has to act there, so it must be named
REFERRED = ELSEWHERE + ('conflict',)
OUTSIDE_RE = re.compile(r'(?<![A-Za-z0-9])[A-Za-z]:[\\/]|(?<![\w\\])\\\\[\w.$-]+\\'
                        r'|(?<=[A-Za-z0-9]):/(?!/)|(?<![\w./~-])(?:(?:~|/(?:home|Users|root|mnt|etc|var|tmp|opt|usr|srv|run|proc|sys|dev|data))/|/(?:[\w.-]+/)*[\w-]+\.[A-Za-z]{1,5}\b)')
SECRET_RE = re.compile(r'(?i)\b(?:sk|ghp|gho|xox[abp]|AKIA)[-_]?[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY'
                       r'|\b(?:password|passwd|secret|token|api[_-]?key)\b\s*[=:：]\s*[^\s，。；]{6,}|(?:密码|口令|密钥)\s*[=:：]\s*[^\s，。；]{4,}')
MIN_QUOTE = 12
_TEXT = {'type': 'string'}
_IDS = {'type': 'array', 'items': _TEXT, 'minItems': 1}


def _object(**properties):
    return {'type': 'object', 'additionalProperties': False, 'properties': properties, 'required': list(properties)}


def _list(items):
    return {'type': 'array', 'items': items}


_OPERATION = _object(target=_TEXT, section=_TEXT, old_text=_TEXT, new_text=_TEXT, conditions=_TEXT,
                     evidence_ids=_IDS, consumers=_list(_object(role=_TEXT, decision=_TEXT)))
_TOPIC = _object(title=_TEXT, evidence_ids=_IDS, outcome={'type': 'string', 'enum': list(OUTCOMES)}, reason=_TEXT,
                 destination=_TEXT,
                 covered_by=_list(_object(evidence_id=_TEXT, path=_TEXT, quote=_TEXT)), operations=_list(_OPERATION))
RESULT_SCHEMA = _object(summary=_TEXT, topics=_list(_TOPIC))


def _fits(value, schema):
    kind = schema['type']
    if kind == 'string':
        return isinstance(value, str) and value in schema.get('enum', (value,))
    if kind == 'array':
        return (isinstance(value, list) and len(value) >= schema.get('minItems', 0)
                and all(_fits(item, schema['items']) for item in value))
    return (isinstance(value, dict) and set(value) == set(schema['properties'])
            and all(_fits(value[k], sub) for k, sub in schema['properties'].items()))


def _plain(text):
    return (text or '').replace('\r\n', '\n')


def capacity_of(target):
    if not target.endswith(('/MEMORY.md', '/module-memory.md')):
        return None
    return (SHARED_MEMORY_MAX_CHARS if target.endswith('/shared/MEMORY.md') else
            ROLE_MEMORY_MAX_CHARS if target.endswith('/MEMORY.md') else MODULE_MEMORY_MAX_CHARS)


def headings(text):
    """Heading lines a reader sees as headings: not the ones inside code or comments."""
    lines = text.split('\n')
    hidden = opaque_lines(lines)
    return [line.strip() for i, line in enumerate(lines) if i not in hidden and re.match(r'^#{1,6}\s', line)]


def one_item(text):
    lines = text.strip().splitlines()
    return bool(lines and re.match(r'^-\s+\S', lines[0]) and all(
        not line.strip() or line.startswith((' ', '\t')) for line in lines[1:]))


def _operation(op, topic_ids, rows, targets, added):
    """(problems, blockers): problems make it unusable, blockers only rule out automatic writing."""
    problems, blockers = [], []
    target, text = op['target'], _plain(targets.get(op['target']))
    own = list(dict.fromkeys(op['evidence_ids']))
    if not set(own) <= set(topic_ids):
        problems.append('evidence_not_in_topic')
    own = [i for i in own if i in rows]
    if not op['new_text'].strip() or not op['conditions'].strip():
        problems.append('empty_text_or_conditions')
    if target not in targets:
        problems.append('target_not_offered')
    elif op['old_text']:
        blockers.append('replacement')
        if text.count(_plain(op['old_text'])) != 1:
            problems.append('old_text_not_unique')
    elif op['section']:
        if not re.match(r'^#{1,6}\s', op['section']) or headings(text).count(op['section'].strip()) != 1:
            problems.append('section_missing_or_ambiguous')
    elif any(line.startswith('##') for line in headings(text)) or len(headings(text)) > 1:  # one title alone is no section
        problems.append('section_required')
    if target.endswith('/shared/MEMORY.md'):
        blockers.append('shared_memory')
        if len({c['role'].strip() for c in op['consumers'] if c['role'].strip() and c['decision'].strip()}) < 2:
            blockers.append('needs_two_consumers')
    elif target.endswith('/SKILL.md'):
        blockers.append('skill_text')
    if not target.endswith('/SKILL.md') and not one_item(op['new_text']):
        blockers.append('not_one_item')
    if '[纠正]' in op['new_text'] or '[纠正]' in op['old_text']:
        blockers.append('protected_correction')
    if OUTSIDE_RE.search(op['new_text']):
        blockers.append('outside_path')
    if SECRET_RE.search(op['new_text']):
        blockers.append('secret')
    hedged = [i for i in own if HEDGE_RE.search(rows[i]['text'])]
    if hedged and not HEDGE_RE.search(op['new_text']):
        blockers.append('hedge_dropped:' + ','.join(hedged))
    unsourced = [i for i in own if rows[i]['type'] == 'notes' and rows[i].get('provenance') == 'unknown']
    if unsourced:
        blockers.append('unknown_source:' + ','.join(unsourced))
    if any(rows[i].get('prior_decisions') for i in own):
        blockers.append('prior_decision')
    limit = capacity_of(target)
    if limit is not None and target in targets:
        grown = len(op['new_text']) - len(op['old_text']) + (0 if op['old_text'] else 1)
        if len(text) + added.get(target, 0) + grown > limit:
            blockers.append('capacity')
        elif not problems:
            added[target] = added.get(target, 0) + grown
    return problems, blockers


def review_output(bundle, output, changed=(), withdrawn=()):
    """Return {'summary', 'topics', 'evidence'}; raise ValueError if it is not a result at all.

    One bad topic costs only its own evidence. `changed` names frozen files that
    no longer match and `withdrawn` evidence whose text is gone: conclusions that
    rest on either do not count.
    """
    if (not isinstance(output, dict) or set(output) != {'summary', 'topics'}
            or not isinstance(output['summary'], str) or not isinstance(output['topics'], list)):
        raise ValueError('invalid result object')
    rows = {r['short_id']: r for r in bundle['evidence']}
    texts = {rel: snap['text'] for rel, snap in bundle['sources'].items()}
    targets = {rel: texts[rel] for rel in bundle['targets']}
    changed = set(changed)
    topics, added = [], {}
    concluded, problems = {}, {}

    def miss(short, why):
        problems.setdefault(short, set()).add(why)

    for number, topic in enumerate(output['topics'], 1):
        if not _fits(topic, _TOPIC):
            topics.append({'topic': number, 'status': 'invalid', 'problems': ['invalid_shape']})
            listed = topic.get('evidence_ids') if isinstance(topic, dict) else None
            for short in listed if isinstance(listed, list) else ():
                if isinstance(short, str) and short in rows:
                    miss(short, 'invalid_shape')
            continue
        ids = list(dict.fromkeys(topic['evidence_ids']))
        known = [i for i in ids if i in rows]
        record = {'topic': number, 'title': topic['title'], 'outcome': topic['outcome'], 'reason': topic['reason'],
                  'destination': topic['destination'],
                  'evidence': known, 'problems': [], 'covered_by': [], 'operations': []}
        if len(known) < len(ids):
            record['problems'].append('unknown_evidence:' + ','.join(i for i in ids if i not in rows))
        ok = set(known)
        for part, needed in (('reason', True), ('destination', topic['outcome'] in ELSEWHERE)):
            if needed and not topic[part].strip():
                record['problems'].append(part + '_missing')
                ok.clear()
                for short in known:
                    miss(short, part + '_missing')
        for short in known:
            if changed & set(rows[short]['context']) or rows[short]['id'] in withdrawn:
                ok.discard(short)
                miss(short, 'baseline_changed')
        if topic['outcome'] == 'covered':
            proven = set()
            for item in topic['covered_by']:
                quote = _plain(item['quote']).strip()
                found = (len(quote) >= MIN_QUOTE and item['path'] in texts and item['path'] not in changed
                         and quote in _plain(texts[item['path']]))
                record['covered_by'].append(dict(item, found=found))
                if found:
                    proven.add(item['evidence_id'])
            for short in sorted(ok - proven):
                ok.discard(short)
                miss(short, 'quote_not_found')
        elif topic['outcome'] == 'write':
            used = set()
            for op in topic['operations']:
                bad, blockers = _operation(op, known, rows, targets, added)
                # Its own file, and everything the evidence it names was judged against.
                if changed & ({op['target']} | {rel for i in op['evidence_ids'] if i in rows for rel in rows[i]['context']}):
                    bad.append('baseline_changed')
                if any(rows[i]['id'] in withdrawn for i in op['evidence_ids'] if i in rows):
                    bad.append('evidence_withdrawn')
                record['operations'].append(dict(op, status='rejected' if bad else 'suggested',
                                                 problems=bad, auto_write_blockers=blockers))
                # Only this topic's own evidence: naming a foreign id must not undo it elsewhere.
                own = set(op['evidence_ids']) & set(known)
                used |= own
                if bad:
                    for short in own:
                        ok.discard(short)
                        miss(short, 'operation_rejected')
            for short in sorted(ok - used):
                ok.discard(short)  # listed, but no operation deals with it
                miss(short, 'no_usable_operation')
            if not any(op['status'] == 'suggested' for op in record['operations']):
                record['problems'].append('no_usable_operation')
        elif topic['operations']:
            record['problems'].append('operations_ignored_on_' + topic['outcome'])
        for short in sorted(ok):
            concluded.setdefault(short, []).append({'topic': number, 'outcome': topic['outcome']})
        record['status'] = 'concluded' if ok else 'not_concluded'
        topics.append(record)
    evidence = {}
    for short, row in rows.items():
        why = problems.get(short, set())
        # A rejected operation or a stale baseline leaves part of the fact undone,
        # whatever another topic said about the same item.
        done = short in concluded and not why & {'operation_rejected', 'baseline_changed'}
        evidence[row['id']] = {'short_id': short, 'concluded': done, 'conclusions': concluded.get(short, []),
                               'problems': sorted(why) or ([] if done else ['not_answered'])}
    return {'summary': output['summary'], 'topics': topics, 'evidence': evidence}
