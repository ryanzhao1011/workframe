"""Independent board, completion and event statistics for read-only queries."""

from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json

from _harness import event_availability
from _query_board import _text, decode_error_line, dependencies as board_dependencies, normalized_date, read_board
from _state_io import MERGED_SUFFIX, SPILL_SUFFIX, state_dir_of
from recompute_skill_metrics import _in_window, _parse_ts
from workframe_doctor import parse_event_lines


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
    return board_dependencies(project) + [state_dir_of(project).relative_to(project).as_posix() + "/"]


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


def read_events(state, project, *, error):
    files = [state / "events.jsonl"] + sorted(state.glob("events.*" + SPILL_SUFFIX))
    files += sorted(state.glob("events.*" + SPILL_SUFFIX + MERGED_SUFFIX + ".*"))
    seen, lines = set(), []
    for path in files:
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


def event_stats(events, malformed, now, days):
    usage, signals = Counter(), Counter()
    earliest = None
    signal_types = ("user_correction", "turn_failed", "task_blocked")
    for row in events:
        timestamp = _parse_ts(row.get("ts"))
        if timestamp is None:
            malformed += 1
            continue
        if not _in_window(row, now, days):
            continue
        kind = row.get("type")
        counted = False
        # Match recompute_skill_metrics: skill_used is the usage record.
        # event-schema explicitly says skill_invoked COEXIST, NEVER SUM.
        if kind == "skill_used" and isinstance(row.get("skill"), str) and row["skill"]:
            usage[row["skill"]] += 1
            counted = True
        elif kind in signal_types:
            signals[kind] += 1
            counted = True
        if counted and (earliest is None or timestamp < earliest):
            earliest = timestamp
    sources = {}
    for kind in signal_types:
        availability = event_availability(kind)
        if not availability:
            raise ValueError("Event availability registry is unavailable for " + kind)
        sources[kind] = {"count": signals[kind], "sources": [
            label for key, label in (("cc", "claude"), ("codex", "codex"))
            if availability.get(key) in ("full", "partial")]}
    expires = None
    if earliest is not None:
        # The framework window includes its lower bound. Refresh at the next
        # second after it, so a refresh on the inclusive boundary cannot loop.
        expires = (earliest + timedelta(days=days)).replace(microsecond=0) + timedelta(seconds=1)
    return ({"state": "ok", "range": {"days": days,
                                       "since": (now - timedelta(days=days)).isoformat(timespec="seconds"),
                                       "until": now.isoformat(timespec="seconds")},
             "skill_usage": [{"skill": key, "count": count} for key, count in sorted(usage.items(), key=lambda item: (-item[1], item[0]))],
             "signals": sources, "unparsed_lines": malformed}, expires)


def _failure(exc):
    return {"state": "error", "error": {"code": getattr(exc, "code", "internal"), "message": str(exc),
                                         "detail": getattr(exc, "detail", None)}}


def run(project, warnings, depends_on, now, days, weeks, *, error):
    try:
        tasks = read_board(project, warnings, depends_on, error=error)
        board, completion = board_stats(tasks, weeks, warnings)
    except Exception as exc:
        board, completion = _failure(exc), _failure(exc)
    expires = None
    try:
        rows, malformed = read_events(state_dir_of(project), project, error=error)
        events, expires = event_stats(rows, malformed, now, days)
    except Exception as exc:
        events = _failure(exc)
    times = ([expires] if expires is not None else []) + ([weeks[0]["end"]] if weeks else [])
    valid_until = min(times).astimezone(timezone.utc).isoformat(timespec="seconds") if times else None
    return {"board": board, "completion": completion, "events": events}, valid_until
