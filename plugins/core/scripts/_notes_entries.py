#!/usr/bin/env python3
"""The one definition of a notes.md entry, and where every other line went.

Shared by background candidate selection, the maintenance work order and the
backlog reminder, so the three never disagree about what is waiting. Import has
no side effects. Nothing here judges whether an entry deserves promotion.

An entry starts at column 0 with a `### ` heading or a dated bullet; the bullet
may carry `[tags]`, and the date may be followed by an ASCII or full-width
colon, a bracket, a space or text, but not by a longer token (`2026-11-20abc`).
Everything up to the next entry or the next `#`/`##` heading belongs to it.
"""

from datetime import date
import re

BACKLOG_CHARS = 3000        # waiting text of one scope that is worth a look
ENTRY_RE = re.compile(r'^(?:###\s+\S|- (?:\[[^\]\n]+\]\s*)*\d{4}-\d{2}-\d{2}(?![0-9A-Za-z_-]))')
DISPOSED_RE = re.compile(r'→\s*(?:已(?:处置|提升至|归档|被.{0,24}?纠正取代)|不变：|拒绝：)')
DATE_RE = re.compile(r'(?<!\d)(\d{4})-(\d{2})-(\d{2})(?![0-9A-Za-z_-])')
FENCE_RE = re.compile(r'^\s*(`{3,}|~{3,})(.*)$')
LINE_CLASSES = ('entry', 'disposed', 'template', 'fence', 'structure', 'blank', 'unrecognized')


def _split(text):
    return text.replace('\r\n', '\n').replace('\r', '\n').split('\n')


def opaque_lines(lines):
    """{line index: 'fence' | 'template'}; an unclosed fence or comment hides nothing."""
    kinds, i = {}, 0
    while i < len(lines):
        fence = FENCE_RE.match(lines[i])
        if fence and not (fence.group(1)[0] == '`' and '`' in fence.group(2)):
            mark = fence.group(1)
            closer = re.compile(r'^\s*' + re.escape(mark[0]) + '{%d,}\\s*$' % len(mark))
            end = next((j for j in range(i + 1, len(lines)) if closer.match(lines[j])), None)
            if end is not None:
                kinds.update((j, 'fence') for j in range(i, end + 1))
                i = end + 1
                continue
        elif re.match(r'^ {0,3}<!--', lines[i]):
            end = next((j for j in range(i, len(lines)) if '-->' in lines[j]), None)
            # Text after the closing mark is visible to a reader, so the line stays visible here.
            if end is not None and not lines[end].split('-->', 1)[1].strip():
                kinds.update((j, 'template') for j in range(i, end + 1))
                i = end + 1
                continue
        i += 1
    return kinds


def _date_of(first_line):
    found = DATE_RE.search(first_line)
    try:
        return date(*map(int, found.groups())) if found else None
    except ValueError:
        return None


def scan_notes(text):
    """Return {'entries', 'lines', 'unrecognized_chars'}; each line has exactly one class.

    `entries` holds {'line', 'end', 'text', 'date', 'disposed'} in file order. A `###`
    heading with nothing under it but dated bullets is a group title, not an
    entry of its own.
    """
    lines = _split(text)
    opaque = opaque_lines(lines)
    owner, entries, current = [None] * len(lines), [], None
    classes = [None] * len(lines)
    for index, line in enumerate(lines):
        kind = opaque.get(index)
        stripped = line.strip()
        if kind == 'template':
            classes[index] = kind
        elif kind == 'fence':
            owner[index], classes[index] = current, kind
        elif ENTRY_RE.match(line):
            current = {'line': index + 1, 'indexes': []}
            entries.append(current)
            owner[index] = current
        elif re.match(r'^#{1,2}\s', line):
            current = None
            classes[index] = 'structure'
        elif not stripped:
            owner[index], classes[index] = current, 'blank'
        elif current is not None:
            owner[index] = current
        elif stripped.startswith(('#', '>', '---', '_（', '_(')):
            classes[index] = 'structure'
        else:
            classes[index] = 'unrecognized'
        if owner[index] is not None:
            owner[index]['indexes'].append(index)
    result = []
    for position, entry in enumerate(entries):
        body = '\n'.join(lines[i] for i in entry['indexes']).strip()
        first = lines[entry['line'] - 1]
        following = entries[position + 1] if position + 1 < len(entries) else None
        title_only = (first.startswith('###') and '\n' not in body and following is not None
                      and lines[following['line'] - 1].startswith('- ')
                      and all(not re.match(r'^#{1,2}\s', lines[i]) or opaque.get(i)
                              for i in range(entry['line'], following['line'] - 1)))
        # A mark shown as code is an example, not a decision about this entry.
        prose = '\n'.join(lines[i] for i in entry['indexes'] if opaque.get(i) != 'fence')
        disposed = bool(DISPOSED_RE.search(re.sub(r'`[^`\n]*`', '', prose)))
        kind = 'structure' if title_only else 'disposed' if disposed else 'entry'
        for i in entry['indexes']:
            if lines[i].strip():
                classes[i] = kind  # a fence inside an entry is that entry's text
        if not title_only:
            result.append({'line': entry['line'], 'end': max(i for i in entry['indexes'] if lines[i].strip()) + 1,
                           'text': body, 'date': _date_of(first), 'disposed': disposed})
    counts = dict.fromkeys(LINE_CLASSES, 0)
    for kind in classes:
        counts[kind] += 1
    return {'entries': result, 'lines': counts, 'unrecognized_chars': sum(
        len(line.strip()) for line, kind in zip(lines, classes) if kind == 'unrecognized')}


def open_entries(text):
    """Entries nobody has disposed of yet; the same text twice in one file is one entry."""
    found = {entry['text']: entry for entry in reversed(scan_notes(text)['entries']) if not entry['disposed']}
    return sorted(found.values(), key=lambda entry: entry['line'])
