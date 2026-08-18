#!/usr/bin/env python3
"""
Recompute .claude/workframe-state/skill-metrics.yaml from events.jsonl.

This is the deterministic implementation for the skill/rule metrics summary.
Librarian may invoke it manually, and SessionEnd calls it automatically through
session-end-flush.py. The script intentionally does not use PyYAML so it works
in a fresh Python installation on Windows/macOS/Linux.

Usage:
    python recompute_skill_metrics.py
    python recompute_skill_metrics.py --window-days 14
"""

import argparse
import io
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
STATE_DIR = PROJECT_DIR / ".claude" / "workframe-state"
EVENTS_FILE = STATE_DIR / "events.jsonl"
METRICS_FILE = STATE_DIR / "skill-metrics.yaml"


def _now():
    return datetime.now(timezone.utc)


def _parse_ts(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _load_events(path):
    if not path.exists():
        return []

    events = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "__schema__" in obj:
                continue
            if isinstance(obj, dict):
                events.append(obj)
    return events


def _in_window(event, generated_at, window_days):
    ts = _parse_ts(event.get("ts"))
    if ts is None:
        return False
    age_seconds = (generated_at - ts).total_seconds()
    return 0 <= age_seconds <= window_days * 86400


def _yaml_quote(value):
    return json.dumps(value, ensure_ascii=False)


def _render_metrics(metrics):
    generated_at = _yaml_quote(metrics["generated_at"])
    lines = [
        "# Workframe skill-metrics — deterministic summary generated from events.jsonl",
        "# Non-official Claude Code state file. Do not edit by hand; run recompute_skill_metrics.py to refresh.",
        "",
        f"generated_at: {generated_at}",
        f"window_days: {metrics['window_days']}",
        "",
    ]

    skills = metrics["skills"]
    if skills:
        lines.append("skills:")
        for name in sorted(skills):
            data = skills[name]
            lines.extend(
                [
                    f"  {name}:",
                    f"    invocations: {data['invocations']}",
                    f"    successes: {data['successes']}",
                    f"    last_used: {_yaml_quote(data['last_used']) if data['last_used'] else 'null'}",
                ]
            )
    else:
        lines.append("skills: {}")

    lines.append("")
    # skill_used 按 role 的分布，供 main-led 观测（主 Claude 直做占比）。追加段而非改动
    # 既有 skills 段——旧消费方不读它也照常工作。
    by_role = metrics.get("skills_by_role") or {}
    if by_role:
        lines.append("skills_by_role:")
        for role in sorted(by_role):
            lines.append(f"  {role}: {by_role[role]}")
    else:
        lines.append("skills_by_role: {}")

    lines.append("")
    # rule_triggered is intentionally removed as a deterministic source; keep the
    # schema branch for downstream compatibility.
    lines.append("rules: {}")
    lines.append("")
    lines.extend(
        [
            f"corrections_count: {metrics['corrections_count']}",
            f"blocks_count: {metrics['blocks_count']}",
            f"proposal_failures_count: {metrics['proposal_failures_count']}",
            "",
        ]
    )
    return "\n".join(lines)


def append_event(ev_type, **extra):
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": _now().isoformat(timespec="seconds"),
        "type": ev_type,
        **extra,
    }
    with EVENTS_FILE.open("a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def recompute_skill_metrics(window_days=30, write_event=True):
    """Aggregate type=skill_used and related maintenance signals into YAML."""
    generated_at_dt = _now()
    events = _load_events(EVENTS_FILE)
    scoped_events = [ev for ev in events if _in_window(ev, generated_at_dt, window_days)]

    skill_stats = defaultdict(lambda: {"invocations": 0, "successes": 0, "last_used_ts": None})
    # main-led 观测（2026-08-16 增）：此前 role 字段被完全忽略，无法回答「主 Claude 直做
    # 占多大比例」。缺失 role 的历史事件归 unspecified，不臆测归属。
    role_stats = defaultdict(int)
    corrections_count = 0
    blocks_count = 0
    proposal_failures_count = 0

    for ev in scoped_events:
        ev_type = ev.get("type")
        if ev_type == "skill_used":
            skill = ev.get("skill")
            if isinstance(skill, str) and skill:
                stats = skill_stats[skill]
                stats["invocations"] += 1
                if ev.get("success") is True:
                    stats["successes"] += 1
                ts = _parse_ts(ev.get("ts"))
                if ts is not None and (stats["last_used_ts"] is None or ts > stats["last_used_ts"]):
                    stats["last_used_ts"] = ts
                role = ev.get("role")
                role_stats[role if isinstance(role, str) and role else "unspecified"] += 1
        elif ev_type == "user_correction":
            corrections_count += 1
        elif ev_type == "task_blocked":
            blocks_count += 1
        elif ev_type == "proposal_verified" and ev.get("signal_met") is False:
            proposal_failures_count += 1

    skills = {}
    for name, stats in skill_stats.items():
        last_used_ts = stats["last_used_ts"]
        skills[name] = {
            "invocations": stats["invocations"],
            "successes": stats["successes"],
            "last_used": last_used_ts.date().isoformat() if last_used_ts else None,
        }

    metrics = {
        "generated_at": generated_at_dt.isoformat(),
        "window_days": window_days,
        "skills": skills,
        "skills_by_role": dict(role_stats),
        "rules": {},
        "corrections_count": corrections_count,
        "blocks_count": blocks_count,
        "proposal_failures_count": proposal_failures_count,
    }

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_FILE.write_text(_render_metrics(metrics), encoding="utf-8", newline="")

    if write_event:
        append_event(
            "skill_metrics_recomputed",
            status="ok",
            window_days=window_days,
            events_read=len(events),
            events_counted=len(scoped_events),
            skills_count=len(skills),
        )

    return {
        "status": "ok",
        "window_days": window_days,
        "events_read": len(events),
        "events_counted": len(scoped_events),
        "skills_count": len(skills),
        "metrics_file": str(METRICS_FILE),
    }


def main():
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Recompute Workframe skill metrics from events.jsonl")
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--no-event", action="store_true", help="Do not append skill_metrics_recomputed event")
    args = parser.parse_args()

    if args.window_days <= 0:
        result = {"status": "error", "reason": "window_days must be positive"}
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)

    try:
        result = recompute_skill_metrics(window_days=args.window_days, write_event=not args.no_event)
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(0)
    except Exception as e:
        try:
            append_event(
                "skill_metrics_recomputed",
                status="error",
                reason=f"{type(e).__name__}: {str(e)[:200]}",
            )
        except Exception:
            pass
        print(json.dumps({"status": "error", "reason": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
