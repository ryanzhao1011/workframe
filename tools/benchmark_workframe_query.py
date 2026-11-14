#!/usr/bin/env python3
"""Benchmark real query processes on a disposable 1000-task/200-document project.

Cold means a fresh file-path copy, not a claim that OS caches were flushed.
Each cold copy is followed by a warm read of the same copy; a final group uses
the original fixture path. Profiling is diagnostic and never mixed into those
distributions. Do not redirect tempfile when a sandbox refuses its fixtures.
"""

import time
_ENTRY_NS = time.perf_counter_ns()

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/core/scripts"
QUERY = SCRIPTS / "workframe_query.py"
TOPICS = ("project-status", "board", "modules", "stats")


def summarize(values):
    values = sorted(values)
    if not values:
        raise ValueError("Cannot summarize an empty sample group")
    return {"n": len(values), "median_ms": statistics.median(values),
            "p95_ms": values[math.ceil(len(values) * 0.95) - 1], "max_ms": values[-1],
            "over_1000_ms": sum(value > 1000 for value in values)}


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def weeks_at(now):
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    return [{"date": (start - timedelta(weeks=i)).date().isoformat(),
             "start": (start - timedelta(weeks=i)).isoformat(),
             "end": (start - timedelta(weeks=i - 1)).isoformat()} for i in range(8)]


def build_fixture(project, now):
    import yaml
    put(project / ".workframe-config.json", {"project_name": "query benchmark"})
    statuses = ("pending", "in_progress", "pending_qa", "completed", "blocked", "cancelled")
    tasks = []
    for i in range(1000):
        row = {"id": "T-%04d" % i, "title": "Synthetic task %d" % i, "status": statuses[i % 6],
               "assigned_to": ("dev", "qa", "pm")[i % 3], "created_at": "2026-10-01",
               "completed_at": (now - timedelta(days=i % 60)).date().isoformat(),
               "notes": "Synthetic benchmark notes. " * 80, "tags": ["benchmark"]}
        if i % 5 == 0:
            row["qa_signoff"] = {"reviewer": "qa", "checked": ["contract", "read only"], "detail": "line one\nline two"}
        tasks.append(row)
    put(project / "projects/board.yaml", yaml.safe_dump({"tasks": tasks}, allow_unicode=True, sort_keys=False, width=10**9))
    names = ["overview.md"] + ["current-state/" + name + ".md" for name in ("architecture", "api-surface", "data-model", "code-map")]
    names += ["decisions/one.md", "decisions/two.md", "research/one.md", "research/two.md", "guide.md"]
    for i in range(20):
        basic = project / "projects/modules" / ("basic-%d" % (i // 4))
        sub = basic / ("sub-%d" % i)
        put(basic / "module.yaml", "name: " + basic.name + "\nstatus: active\n")
        put(sub / "submodule.yaml", "name: " + sub.name + "\nstatus: active\n")
        for name in names:
            put(sub / name, "---\ntype: current-state\nstatus: draft\nupdated: 2026-11-02\n---\n# Synthetic document\n\n" + "Body content. " * 50)
    state = project / ".workframe/state"
    lines = [json.dumps({"ts": (now - timedelta(minutes=i)).isoformat(timespec="seconds"),
                         "type": ("skill_used", "skill_invoked", "user_correction", "turn_failed", "task_blocked")[i % 5],
                         "skill": "skill-%d" % (i % 20), "harness": "cc" if i % 2 else "codex"}) for i in range(5000)]
    put(state / "events.jsonl", "\n".join(lines) + "\n")
    put(state / "events.1.spill.jsonl", "\n".join(lines[:20]) + "\n")
    put(state / "events.2.spill.jsonl.merged.fixture", "\n".join(lines[20:40]) + "\n")
    return {"tasks": len(tasks), "documents": len(list(project.rglob("*.md"))),
            "board_bytes": (project / "projects/board.yaml").stat().st_size, "event_lines": len(lines)}


def fingerprint(root):
    out = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file():
            out[rel] = [hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns]
        elif path.is_dir():
            out[rel + "/"] = "directory"
    return out


def measure(command, env, cwd):
    started = time.perf_counter_ns()
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, timeout=30)
    received = time.perf_counter_ns()
    value = json.loads(result.stdout.decode("utf-8"))
    decoded = time.perf_counter_ns()
    return {"elapsed_ms": (received - started) / 1e6, "decode_ms": (decoded - received) / 1e6,
            "exit_code": result.returncode, "stderr": result.stderr.decode("utf-8", "replace")}, value


def worker(args):
    """Measure startup marker, query bootstrap, metadata and event phases."""
    sys.path.insert(0, str(SCRIPTS))
    import workframe_query as query
    phases = {"module_metadata_ms": 0.0, "event_files_and_aggregation_ms": 0.0}

    def instrument(module, name, label):
        original = getattr(module, name)
        def wrapped(*positional, **keywords):
            started = time.perf_counter_ns()
            try:
                return original(*positional, **keywords)
            finally:
                phases[label] += (time.perf_counter_ns() - started) / 1e6
        setattr(module, name, wrapped)

    if args.profile_worker == "modules":
        import _query_modules
        instrument(_query_modules, "run", "module_metadata_ms")
    elif args.profile_worker == "stats":
        import _query_stats
        instrument(_query_stats, "read_events", "event_files_and_aggregation_ms")
        instrument(_query_stats, "event_stats", "event_files_and_aggregation_ms")
    ready = time.perf_counter_ns()
    command = [args.profile_worker, "--project", args.project]
    if args.profile_worker == "stats":
        command += ["--weeks", args.weeks]
    code = query.main(command)
    sys.stdout.flush()
    phases.update({"process_start_ms": (_ENTRY_NS - int(os.environ["WF_QUERY_BENCH_PARENT_START_NS"])) / 1e6,
                   "bootstrap_ms": (ready - _ENTRY_NS) / 1e6,
                   "query_ms": (time.perf_counter_ns() - ready) / 1e6})
    if min(phases.values()) < 0:
        raise RuntimeError("Invalid cross-process timing clock")
    print("WF_QUERY_PROFILE=" + json.dumps(phases), file=sys.stderr)
    return code


def benchmark(args):
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("samples.jsonl", "summary.json", "failure.json")):
        raise ValueError("Use a new output directory to preserve earlier evidence")
    rows, reference = [], {}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        # Keep the standard isolated fixture, even under a restrictive sandbox.
        with tempfile.TemporaryDirectory(prefix="wf-query-benchmark-") as temp:
            root = Path(temp)
            project, home = root / "project", root / "home"
            home.mkdir()
            env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), CLAUDE_CONFIG_DIR=str(home / ".claude"),
                       CODEX_HOME=str(home / ".codex"), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
                       PYTHONIOENCODING="utf-8", GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
            fixture = build_fixture(project, now)
            if (fixture["tasks"], fixture["documents"]) != (1000, 200):
                raise RuntimeError("Benchmark fixture must contain exactly 1000 tasks and 200 documents")
            init = subprocess.run(["git", "init", "-q", str(project)], env=env, capture_output=True, timeout=15)
            if init.returncode:
                raise RuntimeError(init.stderr.decode("utf-8", "replace"))
            before = fingerprint(project)
            plugin_before = fingerprint(ROOT / "plugins/core")
            week_json = json.dumps(weeks_at(now))

            def sample(topic, path, group, index):
                command = [sys.executable, "-B", str(QUERY), topic, "--project", str(path)]
                if topic == "stats":
                    command += ["--weeks", week_json]
                record, value = measure(command, env, root)
                record.update({"topic": topic, "group": group, "index": index})
                if record["exit_code"] or "error" in value:
                    raise RuntimeError("Query failed: " + json.dumps(value, ensure_ascii=False))
                data = json.loads(json.dumps(value["data"]))
                if topic == "stats":
                    for group_data in data.values():
                        if group_data["state"] != "ok":
                            raise RuntimeError("Statistics group failed: " + json.dumps(data))
                    data["events"]["range"].pop("since")
                    data["events"]["range"].pop("until")
                digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
                if topic in reference and reference[topic] != digest:
                    raise RuntimeError("Query data changed across samples for " + topic)
                reference[topic] = digest
                rows.append(dict(record, data_sha256=digest))

            rng = random.Random(338)
            for index in range(args.cold_samples):
                topics = list(TOPICS)
                rng.shuffle(topics)
                for topic in topics:
                    cold = root / ("copy-%d-%s" % (index, topic))
                    shutil.copytree(project, cold)
                    sample(topic, cold, "cold_file", index)
                    sample(topic, cold, "warm_pair", index)
                    if fingerprint(cold) != before:
                        raise RuntimeError("Query wrote its cold-copy fixture")
                    cold.resolve().relative_to(root.resolve())
                    shutil.rmtree(cold)
            for index in range(args.hot_samples):
                for topic in TOPICS:
                    sample(topic, project, "original_hot", index)
            profiles = []
            for topic in TOPICS:
                command = [sys.executable, "-B", str(Path(__file__).resolve()), "--profile-worker", topic,
                           "--project", str(project), "--weeks", week_json]
                worker_env = dict(env, WF_QUERY_BENCH_PARENT_START_NS=str(time.perf_counter_ns()))
                record, value = measure(command, worker_env, root)
                if record["exit_code"] or "error" in value:
                    raise RuntimeError("Profiling failed: " + str(record))
                timing = next(line.split("=", 1)[1] for line in record["stderr"].splitlines() if line.startswith("WF_QUERY_PROFILE="))
                profiles.append({"topic": topic, **json.loads(timing), "decode_ms": record["decode_ms"]})
            if fingerprint(project) != before or fingerprint(ROOT / "plugins/core") != plugin_before:
                raise RuntimeError("Query wrote the original fixture or plugin")
            import yaml
            summary = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "python": sys.version, "pyyaml": yaml.__version__, "libyaml": bool(getattr(yaml, "CSafeLoader", None)),
                       "fixture": fixture, "seed": 338, "profiles": profiles, "groups": {}}
            for topic in TOPICS:
                summary["groups"][topic] = {group: summarize([row["elapsed_ms"] for row in rows if row["topic"] == topic and row["group"] == group])
                                             for group in ("cold_file", "warm_pair", "original_hot")}
            summary["within_target"] = all(row["elapsed_ms"] <= 1000 for row in rows)
            summary["zero_write"] = True
            put(output / "samples.jsonl", "".join(json.dumps(row) + "\n" for row in rows))
            put(output / "summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    except Exception as exc:
        put(output / "failure.json", {"type": type(exc).__name__, "message": str(exc), "completed_samples": rows})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output")
    parser.add_argument("--cold-samples", type=int, default=100)
    parser.add_argument("--hot-samples", type=int, default=50)
    parser.add_argument("--profile-worker", choices=TOPICS, help=argparse.SUPPRESS)
    parser.add_argument("--project", help=argparse.SUPPRESS)
    parser.add_argument("--weeks", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.profile_worker:
        return worker(args)
    if not args.output or min(args.cold_samples, args.hot_samples) < 1:
        parser.error("--output and positive sample counts are required")
    benchmark(args)
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
