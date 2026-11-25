#!/usr/bin/env python3
"""Read project files as frozen, safe inputs; never infer permission from evidence.

Path safety, byte snapshots, the event stream and what a text says about its own
source. Selecting what to analyse is `_selfiter_candidates`. Nothing is truncated.
"""

import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re

from _selfiter_policy import event_id
from _state_io import SPILL_SUFFIX


class _Inputs:
    def __init__(self, project):
        self.root = Path(project).resolve()
        self.sources = {}
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

    def read(self, value):
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
        return self.sources[rel]['text']

    def raw_lines(self, value):
        """Lines as bytes, so that one undecodable line does not hide the others."""
        rel = self.safe(value)
        try:
            return (self.root / rel).read_bytes().split(b'\n') if rel else []
        except FileNotFoundError:
            return []
        except OSError as exc:
            self.diagnostic('source_unreadable', rel, str(exc))
            return []

    def file_hash(self, value):
        """Current bytes, readable as text or not; None only when there is no such file."""
        rel = self.safe(value)
        if rel is None:
            return 'unsafe'
        try:
            return hashlib.sha256((self.root / rel).read_bytes()).hexdigest()
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError:
            return 'unreadable'  # locked or forbidden is not the same as absent

    def rel(self, path):
        return Path(path).relative_to(self.root).as_posix()

    def children(self, directory, pattern):
        rel = self.rel(directory)
        if self.safe(rel) is None:
            return []
        try:
            return sorted(directory.glob(pattern))
        except OSError as exc:
            self.diagnostic('source_unreadable', rel, str(exc))
            return []


def _events(inputs, state, repeated=None):
    """Every distinct event of the project itself; `repeated` receives the ids seen again."""
    files = [state / 'events.jsonl'] + inputs.children(state, 'events.*' + SPILL_SUFFIX)
    seen = set()
    rows = []
    for path in files:
        rel = inputs.rel(path)
        for number, line in enumerate(inputs.raw_lines(rel), 1):
            if not line.strip():
                continue
            try:
                raw = line.decode('utf-8')
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise ValueError('event must be an object')
            except ValueError as exc:
                inputs.diagnostic('event_invalid', rel, f'line {number}: {exc}')
                continue
            identity = event_id(row)
            if row.get('job_id'):
                continue
            if identity in seen:
                if repeated is not None:
                    repeated.append((identity, row))
                continue
            seen.add(identity)
            rows.append((identity, row, {'path': rel, 'line': number, 'raw': raw}))
    return rows


TESTED_RE = re.compile(r'亲测|实测|复现')
HEDGE_RE = re.compile(r'未(?:亲|实)?测|未验|待验|待(?:亲|实)测|纸面|推断|仅一次|单次'
                      r'|没有?(?:亲测|实测|复现)|(?:未能?|无法|不能|难以|不可|不易)复现')


def _provenance(text):
    """What the text itself says about where the fact came from.

    Literal words only: an explicit label, or the body saying it was tested with
    no word that takes it back. This is not independent verification of the claim.
    """
    if re.search(r'\[亲测(?:[^\]]*)\]|来源[：:]\s*亲测', text):
        return 'self_tested_claim'
    if re.search(r'\[用户确认\]|来源[：:]\s*用户确认', text):
        return 'user_confirmed_claim'
    if TESTED_RE.search(text) and not HEDGE_RE.search(text):
        return 'tested_in_body'
    return 'unknown'
