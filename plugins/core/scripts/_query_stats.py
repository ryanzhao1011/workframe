"""Independent board, completion and event statistics for read-only queries."""

from bisect import bisect_left, bisect_right
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
import os
import sys

from _harness import event_availability
from _query_board import _text, decode_error_line, dependencies as board_dependencies, normalized_date, read_board
from _state_io import MERGED_SUFFIX, SPILL_SUFFIX, state_dir_of
import _skill_identity
from recompute_skill_metrics import _parse_ts
from workframe_doctor import parse_event_lines


STATS_STREAM = "workframe.stats-stream.v1"
_SIGNALS = ("user_correction", "turn_failed", "task_blocked")
_CACHE_BYTES = 64 * 1024 * 1024
_CACHE_PROJECTS = 4
_MAX_LINE_BYTES = 1024 * 1024
_INDEX_BATCH = 4096
_ANCHOR_BYTES = 1024


def parse_weeks(raw, *, error):
    try:
        values = json.loads(raw)
        if not isinstance(values, list):
            raise ValueError("expected a JSON array")
        out = []
        for row in values:
            if not isinstance(row, dict):
                raise ValueError("each week must be an object")
            day = date.fromisoformat(row["date"])
            start = datetime.fromisoformat(row["start"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(row["end"].replace("Z", "+00:00"))
            if day.weekday() != 0 or start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0) or start >= end:
                raise ValueError("weeks require a Monday date and increasing UTC start/end timestamps")
            if out and (day >= out[-1]["day"] or end > out[-1]["start"]):
                raise ValueError("weeks must be ordered newest first without overlap")
            out.append({"date": day.isoformat(), "day": day, "start": start, "end": end})
        return out
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise error("bad_usage", f"stats requires valid --weeks JSON: {exc}", exit_code=2) from exc


def dependencies(project):
    # 项目 skills 的两种布局都在内：技能身份归一看「项目里有没有同名技能」，那里新增或删掉一个同名技能会改变结果；
    # 只列当前生效的那一个的话，旧布局项目新建 `.agents/skills/` 切换布局时调用方收不到失效信号
    import _harness
    return (board_dependencies(project) + [state_dir_of(project).relative_to(project).as_posix() + "/"]
            + [d.as_posix() + "/" for d in (_harness.SKILLS_DIR_NEUTRAL, _harness.SKILLS_DIR_CC)])


def _counts(counter):
    return [{"key": key, "count": counter[key]} for key in sorted(counter)]


def board_stats(tasks, weeks, warnings):
    by_status, by_role = Counter(), Counter()
    completion = [{"week_start": week["date"], "count": 0} for week in weeks]
    without_date = 0
    for task in tasks:
        status = _text(task.get("status")) or "unknown"
        if status != "cancelled":
            by_status[status] += 1
            by_role[_text(task.get("assigned_to")) or "unknown"] += 1
        if status != "completed":
            continue
        value = normalized_date(task.get("completed_at"), warnings, str(task.get("id")) + ".completed_at")
        if value is None:
            without_date += 1
            continue
        day = date.fromisoformat(value) if len(value) == 10 else None
        instant = datetime.fromisoformat(value) if day is None else None
        for week, count in zip(weeks, completion):
            if (day is not None and week["day"] <= day < week["day"] + timedelta(days=7) or
                    instant is not None and week["start"] <= instant < week["end"]):
                count["count"] += 1
                break
    return ({"state": "ok", "tasks_by_status": _counts(by_status), "tasks_by_role": _counts(by_role)},
            {"state": "ok", "weeks": completion, "without_date": without_date})


def _event_files(state):
    return ([state / "events.jsonl"] + sorted(state.glob("events.*" + SPILL_SUFFIX))
            + sorted(state.glob("events.*" + SPILL_SUFFIX + MERGED_SUFFIX + ".*")))


# No event file at all (main file, pending spills, merged copies): the project has never
# recorded events, which must not read as "zero corrections / zero blocks". An existing empty
# file is still `ok` with zero counts.
_UNRECORDED = {"state": "unrecorded"}


def _any_event_file(state):
    return any(path.is_file() for path in _event_files(state))


def read_events(state, project, *, error):
    seen, lines = set(), []
    for path in _event_files(state):
        try:
            raw_bytes = path.read_bytes()
            text = raw_bytes.decode("utf-8-sig")
        except FileNotFoundError:
            # A spill can be renamed between enumeration and opening. Its
            # retained merged copy is read on this query's next refresh.
            continue
        except UnicodeDecodeError as exc:
            raise error("parse_error", f"Cannot decode event file: {exc}",
                        {"file": path.relative_to(project).as_posix(),
                         "line": decode_error_line(exc)}) from exc
        except OSError as exc:
            raise error("internal", str(exc), {"file": path.relative_to(project).as_posix()}) from exc
        for raw in text.splitlines():
            if raw not in seen:
                seen.add(raw)
                lines.append(raw)
    events, malformed, _ = parse_event_lines(lines)
    return events, len(malformed)


def _event_entry(row):
    timestamp = _parse_ts(row.get("ts"))
    if timestamp is None:
        return None, None
    kind = row.get("type")
    # skill_used is the self-reported usage record; skill_invoked is the hook-recorded call.
    # They are counted separately and never added. The index keeps the raw name and plugin;
    # identities are normalized only when the result is projected (_event_result), so a
    # change to the project's skills cannot leave a cached index on the old identity.
    if kind in ("skill_used", "skill_invoked") and isinstance(row.get("skill"), str) and row["skill"].strip():
        plugin = row.get("plugin")
        return timestamp, (kind, row["skill"], plugin if isinstance(plugin, str) and plugin else None)
    if kind in _SIGNALS:
        return timestamp, (kind, "", None)
    return None


def _tally(entries, counts, malformed, earliest, since, now):
    for entry in entries:
        if entry is None:
            continue
        timestamp, key = entry
        if timestamp is None:
            malformed += 1
        elif since <= timestamp <= now:
            counts[key] += 1
            if earliest is None or timestamp < earliest:
                earliest = timestamp
    return malformed, earliest


def event_stats(events, malformed, now, days, resolve=None):
    since = now - timedelta(days=days)
    counts = Counter()
    malformed, earliest = _tally(map(_event_entry, events), counts, malformed, None, since, now)
    return _event_result(counts, malformed, earliest, since, now, days, resolve)


def _skill_counts(counts, kind, resolve):
    """One event kind's counts per normalized skill identity (`_skill_identity`), most-used first."""
    merged = Counter()
    for (row_kind, label, plugin), count in counts.items():
        if row_kind == kind:
            merged[resolve(label, plugin) or label] += count
    return [{"skill": key, "count": count} for key, count in sorted(merged.items(), key=lambda item: (-item[1], item[0]))]


def _event_result(counts, malformed, earliest, since, now, days, resolve=None):
    resolve = resolve or _skill_identity.resolver(None)
    sources = {}
    for kind in _SIGNALS:
        availability = event_availability(kind)
        if not availability:
            raise ValueError("Event availability registry is unavailable for " + kind)
        sources[kind] = {"count": counts[kind, "", None], "sources": [
            label for key, label in (("cc", "claude"), ("codex", "codex"))
            if availability.get(key) in ("full", "partial")]}
    expires = None
    if earliest is not None:
        # The framework window includes its lower bound. Refresh at the next
        # second after it, so a refresh on the inclusive boundary cannot loop.
        expires = (earliest + timedelta(days=days)).replace(microsecond=0) + timedelta(seconds=1)
    return ({"state": "ok", "range": {"days": days,
                                       "since": since.isoformat(timespec="seconds"),
                                       "until": now.isoformat(timespec="seconds")},
             "skill_usage": _skill_counts(counts, "skill_used", resolve),
             "skill_invoked": _skill_counts(counts, "skill_invoked", resolve),
             "signals": sources, "unparsed_lines": malformed}, expires)


class _RebuildIndex(Exception):
    pass


class _UseColdReader(Exception):
    pass


class _IndexTooLarge(Exception):
    pass


@dataclass
class _EventCursor:
    version: tuple
    end: int
    anchors: list
    tail: list
    cost: int


def _read_at(file, offset, length):
    file.seek(offset)
    return file.read(length)


def _entry_from_line(raw):
    rows, malformed, _ = parse_event_lines([raw])
    if malformed:
        return None, None
    return _event_entry(rows[0]) if rows else None


class _EventIndex:
    """Deduplicate exact raw lines and index occurrences of each type by UTC time."""

    def __init__(self):
        self.files = {}
        self.seen = set()
        self.ordered = {}
        self.delta = defaultdict(list)
        self.delta_count = self.malformed = self.line_memory = self.file_memory = 0

    def _add_line(self, raw):
        if raw in self.seen:
            return
        entry = _entry_from_line(raw)
        if entry is None:
            return
        timestamp, key = entry
        self.seen.add(raw)
        # Account conservatively for set capacity and each indexed timestamp.
        self.line_memory += sys.getsizeof(raw) + 144
        if key is not None and key not in self.ordered and key not in self.delta:
            self.line_memory += sys.getsizeof(key[1]) + 256
        if self.cost() > _CACHE_BYTES:
            raise _IndexTooLarge
        if timestamp is None:
            self.malformed += 1
        else:
            self.delta[key].append(timestamp)
            self.delta_count += 1

    def _read_file(self, file, previous):
        stat = os.fstat(file.fileno())
        identity = (stat.st_dev, stat.st_ino)
        size = stat.st_size
        version = (identity, size, stat.st_mtime_ns, stat.st_ctime_ns)
        offset = 0
        if previous:
            if not all(_read_at(file, start, len(anchor)) == anchor for start, anchor in previous.anchors):
                raise _RebuildIndex
            if version == previous.version:
                return previous
            # The framework writes whole lines in append mode. An overwrite at
            # the same size, truncation, or new file identity invalidates the index.
            if identity != previous.version[0] or size <= previous.version[1]:
                raise _RebuildIndex
            if not stat.st_ino:
                raise _RebuildIndex
            offset = previous.end
        file.seek(offset)
        tail = []
        while offset < size:
            raw = file.readline(min(_MAX_LINE_BYTES + 1, size - offset))
            if not raw or len(raw) > _MAX_LINE_BYTES:
                raise _UseColdReader
            lines = raw.decode("utf-8-sig" if offset == 0 else "utf-8").splitlines()
            if not raw.endswith(b"\n"):
                # A trailing record is visible to a cold reader, but an append
                # may change it. Leave it outside the durable deduplication set.
                if offset + len(raw) != size:
                    raise _UseColdReader
                tail = lines
                break
            for line in lines:
                self._add_line(line)
            offset += len(raw)
        tail_start = max(0, offset - _ANCHOR_BYTES)
        anchor_starts = sorted({0, min(tail_start, size // 2), tail_start})
        anchors = [(start, _read_at(file, start, min(size - start, _ANCHOR_BYTES)))
                   for start in anchor_starts]
        after = os.fstat(file.fileno())
        if after.st_size < size or (after.st_size == size and after.st_mtime_ns != stat.st_mtime_ns):
            raise _UseColdReader
        cost = sum(sys.getsizeof(line) for line in tail) + sum(len(anchor) for _, anchor in anchors) + 256
        return _EventCursor(version, offset, anchors, tail, cost)

    def refresh(self, state):
        files = _event_files(state)
        if len(files) > 1024:
            raise _IndexTooLarge
        if not self.files.keys() <= set(files):
            raise _RebuildIndex
        for path in files:
            previous = self.files.get(path)
            if previous is not None:
                self.file_memory -= previous.cost
            try:
                with path.open("rb") as file:
                    cursor = self._read_file(file, previous)
            except FileNotFoundError:
                if previous is not None:
                    raise _RebuildIndex from None
                continue
            self.files[path] = cursor
            self.file_memory += cursor.cost
            if self.cost() > _CACHE_BYTES:
                raise _IndexTooLarge

    def cost(self):
        return self.line_memory + self.file_memory

    def result(self, now, days, resolve=None):
        if self.delta_count >= _INDEX_BATCH or not self.ordered:
            for key, new in self.delta.items():
                timestamps = self.ordered.setdefault(key, [])
                timestamps.extend(new)
                timestamps.sort()
            self.delta.clear()
            self.delta_count = 0
        since = now - timedelta(days=days)
        counts, earliest = Counter(), None
        for key, timestamps in self.ordered.items():
            start = bisect_left(timestamps, since)
            end = bisect_right(timestamps, now)
            if start < end:
                counts[key] += end - start
                timestamp = timestamps[start]
                if earliest is None or timestamp < earliest:
                    earliest = timestamp
        entries = ((timestamp, key) for key, timestamps in self.delta.items() for timestamp in timestamps)
        malformed, earliest = _tally(entries, counts, self.malformed, earliest, since, now)
        tails = dict.fromkeys(line for cursor in self.files.values() for line in cursor.tail if line not in self.seen)
        malformed, earliest = _tally(map(_entry_from_line, tails), counts, malformed, earliest, since, now)
        return _event_result(counts, malformed, earliest, since, now, days, resolve)


class EventStatsCache:
    """A bounded in-memory cache for consecutive requests on a private pipe."""

    def __init__(self):
        self.projects = OrderedDict()

    def _remember(self, state, index):
        cost = (index.cost() if index else 0) + sum(
            cached.cost() for cached in self.projects.values() if cached is not None
        )
        while self.projects and (len(self.projects) >= _CACHE_PROJECTS or cost > _CACHE_BYTES):
            _, evicted = self.projects.popitem(last=False)
            if evicted is not None:
                cost -= evicted.cost()
        self.projects[state] = index

    def read(self, state, project, now, days, *, error):
        if not _any_event_file(state):
            # Nothing recorded is not the same answer as zero events; no index is kept for it.
            self.projects.pop(state, None)
            return _UNRECORDED, None
        resolve = _skill_identity.resolver(project)
        if state not in self.projects:
            self.projects[state] = _EventIndex()
        index = self.projects.pop(state)
        remember = True
        if index is not None:
            try:
                try:
                    index.refresh(state)
                except _RebuildIndex:
                    index = _EventIndex()
                    index.refresh(state)
                result = index.result(now, days, resolve)
            except _IndexTooLarge:
                # Avoid filling the same oversized index on every refresh.
                index = None
            except (_UseColdReader, OSError, UnicodeDecodeError, OverflowError):
                # The cold reader also supplies the established per-file error detail.
                index = None
                remember = False
        if remember:
            self._remember(state, index)
        if index is None:
            rows, malformed = read_events(state, project, error=error)
            return event_stats(rows, malformed, now, days, resolve)
        return result


def serve_stats(main, stdin, stdout):
    cache = EventStatsCache()

    def emit(code, value):
        stdout.write(json.dumps({"exit_code": code, "result": value}, ensure_ascii=False) + "\n")
        stdout.flush()

    limit = 64 * 1024
    while raw := stdin.readline(limit + 1):
        if len(raw) > limit or not raw.endswith(b"\n"):
            return 2
        try:
            argv = json.loads(raw.decode("utf-8"))
        except (ValueError, RecursionError):
            return 2
        if not isinstance(argv, list) or argv[:1] != ["stats"] or not all(isinstance(arg, str) for arg in argv):
            return 2
        main(argv, event_cache=cache, emit=emit)
    return 0


def _failure(exc):
    return {"state": "error", "error": {"code": getattr(exc, "code", "internal"), "message": str(exc),
                                         "detail": getattr(exc, "detail", None)}}


def run(project, warnings, depends_on, now, days, weeks, *, error, event_cache=None):
    try:
        tasks = read_board(project, warnings, depends_on, error=error)
        board, completion = board_stats(tasks, weeks, warnings)
    except Exception as exc:
        board, completion = _failure(exc), _failure(exc)
    expires = None
    try:
        state = state_dir_of(project)
        if event_cache is None:
            if not _any_event_file(state):
                events, expires = _UNRECORDED, None
            else:
                rows, malformed = read_events(state, project, error=error)
                events, expires = event_stats(rows, malformed, now, days, _skill_identity.resolver(project))
        else:
            events, expires = event_cache.read(state, project, now, days, error=error)
    except Exception as exc:
        events = _failure(exc)
    times = ([expires] if expires is not None else []) + ([weeks[0]["end"]] if weeks else [])
    valid_until = min(times).astimezone(timezone.utc).isoformat(timespec="seconds") if times else None
    return {"board": board, "completion": completion, "events": events}, valid_until
