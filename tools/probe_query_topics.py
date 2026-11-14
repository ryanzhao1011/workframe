#!/usr/bin/env python3
"""Exercise individual query-test failures with in-memory output mutations.

No source file or fixture is changed. This covers the pure contract assertions;
filesystem/zero-write probes still require the isolated CLI suite. A test error
does not count as a killed mutation. Expected values remain in the tests, never
in the production adapters or in a generated snapshot of their output.
"""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "plugins/core/scripts"))


def outcome(test):
    result = unittest.TestResult()
    test.run(result)
    if result.errors or result.testsRun != 1:
        return "error"
    return "killed" if result.failures else "survived"


def _replace(value, path, replacement):
    if not path:
        if value == replacement:
            raise ValueError("Mutation would not change the output")
        return replacement
    key, rest = path[0], path[1:]
    result = list(value) if isinstance(value, tuple) else copy.deepcopy(value)
    result[key] = _replace(result[key], rest, replacement)
    return tuple(result) if isinstance(value, tuple) else result


def cases():
    rows = []
    def add(module, function, test, path, replacement, call=None):
        rows.append({"module": module, "function": function, "test": test,
                     "path": path, "replacement": replacement, "call": call})
    defaults = "TopicBoardUnitTests.test_board_defaults_and_unknown_status_are_preserved"
    for field in ("id", "title", "status", "tags", "depends_on", "priority", "assigned_to", "module", "notes", "description",
                  "blocked_reason", "cancel_reason", "actual_output", "signoff_text", "created_at", "updated_at", "completed_at", "cancelled_at"):
        add("_query_board", "normalize_tasks", defaults, ("tasks", 0, field), "__mutated__")
    for index in range(8):
        for field in ("key", "order"):
            add("_query_board", "normalize_tasks", defaults, ("statuses", index, field), "__mutated__")
    complete = "TopicBoardUnitTests.test_complete_task_fields_are_not_lost"
    for field in ("id", "title", "status", "priority", "assigned_to", "module", "tags", "depends_on", "notes", "description",
                  "blocked_reason", "cancel_reason", "actual_output"):
        add("_query_board", "normalize_tasks", complete, ("tasks", 0, field), "__mutated__")
    for field in ("created_at", "updated_at", "completed_at", "cancelled_at"):
        add("_query_board", "normalize_tasks", "TopicBoardUnitTests.test_dates_distinguish_calendar_dates_from_instants",
            ("tasks", 0, field), "__mutated__")
    add("_query_board", "normalize_tasks", "TopicBoardUnitTests.test_naive_timestamp_is_not_silently_given_a_timezone",
        ("tasks", 0, "completed_at"), "2026-11-02T12:30:00+00:00")
    metadata = "TopicModulesUnitTests.test_metadata_and_first_heading_fallback"
    for field in ("kind", "title", "path", "type", "status", "updated", "meta_error"):
        add("_query_modules", "document_text", metadata, (field,), "__mutated__", call=0)
    add("_query_modules", "document_text", metadata, ("title",), "Fake", call=1)
    for index in range(4):
        for field in ("title", "meta_error", "type", "status", "updated"):
            add("_query_modules", "document_text", "TopicModulesUnitTests.test_bad_metadata_is_isolated_and_retains_a_title",
                (field,), None if field == "meta_error" else "__mutated__", call=index)
    for index in range(4):
        for field in ("kind", "path", "message"):
            add("_query_modules", "stale_details", "TopicModulesUnitTests.test_stale_reason_shape_preserves_unknown_kinds_and_deduplicates_files",
                ("reasons", index, field), "__mutated__")
    add("_query_modules", "stale_details", "TopicModulesUnitTests.test_stale_reason_shape_preserves_unknown_kinds_and_deduplicates_files",
        ("files",), ["src/a.py", "src/a.py"])
    weekly = "TopicStatsUnitTests.test_weeks_use_supplied_dst_boundaries_and_date_only_labels"
    for index in range(2):
        add("_query_stats", "board_stats", weekly, (1, "weeks", index, "count"), -1)
        add("_query_stats", "board_stats", weekly, (1, "weeks", index, "week_start"), "__mutated__")
    add("_query_stats", "board_stats", weekly, (1, "without_date"), -1)
    add("_query_stats", "board_stats", weekly, (0, "tasks_by_status", 0, "count"), -1)
    add("_query_stats", "board_stats", weekly, (0, "tasks_by_role"), [{"key": "cancelled-role", "count": 1}])
    event = "TopicStatsUnitTests.test_event_window_usage_and_signal_sources_follow_registry"
    for field in ("days", "since", "until"):
        add("_query_stats", "event_stats", event, (0, "range", field), "__mutated__")
    for index in range(2):
        for field in ("skill", "count"):
            add("_query_stats", "event_stats", event, (0, "skill_usage", index, field), "__mutated__")
    for kind in ("user_correction", "turn_failed", "task_blocked"):
        add("_query_stats", "event_stats", event, (0, "signals", kind, "count"), -1)
        add("_query_stats", "event_stats", event, (0, "signals", kind, "sources"), [])
    add("_query_stats", "event_stats", event, (0, "unparsed_lines"), -1)
    add("_query_stats", "event_stats", event, (1,), datetime(2000, 1, 1, tzinfo=timezone.utc))
    for field in ("n", "median_ms", "p95_ms", "max_ms", "over_1000_ms"):
        add("benchmark_workframe_query", "summarize", "QueryBenchmarkUnitTests.test_summary_uses_nearest_rank_and_reports_every_overrun",
            (field,), -1)
    return rows


def run():
    tests = importlib.import_module("test_workframe_query")
    rows = cases()
    modules = {row["module"]: importlib.import_module(row["module"]) for row in rows}
    sources = {Path(module.__file__) for module in modules.values()} | {Path(tests.__file__), Path(__file__)}
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    findings = []
    for index, row in enumerate(rows):
        module = modules[row["module"]]
        cls, method = row["test"].split(".")
        factory = lambda: getattr(tests, cls)(method)
        baseline = outcome(factory())
        if baseline != "survived":
            raise RuntimeError("Baseline is not green: " + row["test"] + " / " + baseline)
        original = getattr(module, row["function"])
        called = [0]
        def mutated(*args, **kwargs):
            value = original(*args, **kwargs)
            call = called[0]
            called[0] += 1
            if row["call"] is None or row["call"] == call:
                return _replace(value, row["path"], row["replacement"])
            return value
        with patch.object(module, row["function"], mutated):
            status = outcome(factory())
        if getattr(module, row["function"]) is not original:
            raise RuntimeError("In-memory mutation was not restored")
        findings.append({"index": index, "target": row["module"] + "." + row["function"],
                         "test": row["test"], "path": row["path"], "call": row["call"], "outcome": status})
    if {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources} != before:
        raise RuntimeError("Source bytes changed during in-memory probes")
    return {"total": len(findings), "killed": sum(row["outcome"] == "killed" for row in findings),
            "survived": [row for row in findings if row["outcome"] == "survived"],
            "errors": [row for row in findings if row["outcome"] == "error"], "cases": findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    args = parser.parse_args()
    report = run()
    if args.output:
        with Path(args.output).open("w", encoding="utf-8", newline="") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}))
    return 1 if report["survived"] or report["errors"] else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
