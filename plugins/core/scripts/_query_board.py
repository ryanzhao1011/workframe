"""Read and normalize board data without the guard's snapshot/write paths."""

from datetime import date, datetime
import importlib

from recompute_board_summary import KNOWN_FIELDS
from _query_paths import project_relative
from _yaml_probe import safe_load


def board_path(project):
    return importlib.import_module("signoff-guard").board_path(project)


def dependencies(project):
    path = board_path(project)
    try:
        rel = project_relative(path, project).as_posix()
    except ValueError:
        rel = "projects/board.yaml"
    return [".workframe-config.json", rel]


def yaml_warning(warnings):
    import yaml
    if getattr(yaml, "CSafeLoader", None) is None:
        message = "libyaml is unavailable; using PyYAML SafeLoader (queries may be slower)"
        if message not in warnings:
            warnings.append(message)


def decode_error_line(exc):
    """Decode errors index their own bytes (UTF-8-SIG has removed its BOM)."""
    prefix = exc.object[:exc.start].replace(bytes([13, 10]), bytes([10])).replace(bytes([13]), bytes([10]))
    return prefix.count(bytes([10])) + 1


def parse_detail(raw, rel):
    """Recover a parser location only after the shared board reader rejected it."""
    detail = {"file": rel, "line": 1}
    try:
        safe_load(raw.decode("utf-8-sig"))
    except Exception as exc:
        mark = getattr(exc, "problem_mark", None)
        if mark is not None:
            detail["line"] = mark.line + 1
        elif isinstance(exc, UnicodeDecodeError):
            detail["line"] = decode_error_line(exc)
        elif isinstance(getattr(exc, "position", None), int):
            prefix = raw.decode("utf-8-sig", "replace")[:exc.position]
            detail["line"] = prefix.replace("\r\n", "\n").replace("\r", "\n").count("\n") + 1
    return detail


def read_board(project, warnings, depends_on, *, error):
    path = board_path(project)
    try:
        rel = project_relative(path, project).as_posix()
    except ValueError as exc:
        raise error("parse_error", "Configured board must be inside the project",
                    {"file": ".workframe-config.json", "line": 1}) from exc
    if rel not in depends_on:
        depends_on.append(rel)
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise error("not_found", "Board file does not exist", {"file": rel}) from exc
    except OSError as exc:
        raise error("internal", str(exc), {"file": rel}) from exc
    try:
        yaml_warning(warnings)
    except ImportError as exc:
        raise error("internal", "Reading the board requires PyYAML", {"file": rel}) from exc
    # The existing guard owns board loading/shape validation. A rejected board
    # is never replaced with a regex count or an apparently empty task list.
    tasks, problem = importlib.import_module("signoff-guard").load_board(path, raw=raw)
    if problem:
        raise error("parse_error", problem, parse_detail(raw, rel))
    seen = set()
    for task in tasks:
        identity = task.get("id") if isinstance(task, dict) else None
        if not isinstance(identity, str) or not identity or identity in seen:
            raise error("parse_error", "Tasks require unique, nonempty string IDs", {"file": rel, "line": 1})
        seen.add(identity)
    return tasks


def normalized_date(value, warnings, label):
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, date):
            return value.isoformat()
        elif isinstance(value, str):
            if len(value) == 10:
                return date.fromisoformat(value).isoformat()
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            raise ValueError("expected an ISO date or timestamp")
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamp has no timezone")
        return parsed.isoformat()
    except (ValueError, TypeError, OverflowError) as exc:
        warnings.append(f"{label}: unrecognized date/time ({exc})")
        return None


def _text(value):
    return None if value is None else str(value)


def normalize_tasks(tasks, warnings):
    statuses = [key for key in KNOWN_FIELDS if key not in ("total", "last_updated")]
    rows = []
    for task in tasks:
        identity = task["id"]
        row = {"id": identity, "title": _text(task.get("title")) or "",
               "status": _text(task.get("status")) or "unknown"}
        for field in ("priority", "assigned_to", "module", "notes", "description",
                      "blocked_reason", "cancel_reason", "actual_output"):
            row[field] = _text(task.get(field))
        for field in ("tags", "depends_on"):
            values = task.get(field)
            row[field] = [str(value) for value in values] if isinstance(values, list) else []
            if values is not None and not isinstance(values, list):
                warnings.append(f"{identity}.{field}: expected a list")
        for field in ("created_at", "updated_at", "completed_at", "cancelled_at"):
            row[field] = normalized_date(task.get(field), warnings, f"{identity}.{field}")
        signoff = task.get("qa_signoff")
        if signoff is not None and not isinstance(signoff, str):
            import yaml
            signoff = yaml.safe_dump(signoff, allow_unicode=True, sort_keys=False,
                                     default_flow_style=False, width=10**9).rstrip()
        row["signoff_text"] = signoff
        if row["status"] not in statuses:
            statuses.append(row["status"])
        rows.append(row)
    return {"statuses": [{"key": key, "order": i} for i, key in enumerate(statuses)], "tasks": rows}


def run(project, warnings, depends_on, *, error):
    return normalize_tasks(read_board(project, warnings, depends_on, error=error), warnings)
