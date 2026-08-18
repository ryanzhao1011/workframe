#!/usr/bin/env python3
"""
ConfigChange Hook — 会话内配置文件变更审计（v0.4 M5）

CC 2.1.49 新增 ConfigChange 事件：会话期间配置文件（settings / rules / agents 等）
发生变化时触发。本 hook 向 events.jsonl append 一条 `config_changed` 事件，为
auto-update「受保护资产清单」提供运行时审计面——受保护资产被改动时事件流里留痕，
/core:audit 可回放。

设计约束（与其余 workframe hooks 一致）：
  - 纯审计，不阻塞（不返回 block decision）；任何异常吞掉，永远 exit 0
  - stdin JSON 字段防御性读取
"""

import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
EVENTS_FILE = PROJECT_DIR / ".claude" / "workframe-state" / "events.jsonl"


def _collect_files(data):
    """多候选字段防御性提取变更文件列表（CC schema 未硬承诺字段名）。"""
    for key in ("file_path", "file", "path", "config_file"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return [val.strip()]
    for key in ("files", "paths", "changed_files"):
        val = data.get(key)
        if isinstance(val, list):
            out = [str(v).strip() for v in val if str(v).strip()]
            if out:
                return out
    return []


def main():
    # 同 log-stop-failure：Windows 上 sys.stdin 的 locale codec 会把中文 payload 解成
    # mojibake / surrogate，导致事件写不进去
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace") \
            if not sys.stdin.isatty() else ""
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}  # 顶层非 object 时 .get() 会抛，与「永远 exit 0」的承诺冲突

    event = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "type": "config_changed",
    }
    session_id = data.get("session_id")
    if session_id:
        event["session_id"] = session_id
    files = _collect_files(data)
    if files:
        event["files"] = files[:10]

    try:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_FILE.open("a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[log-config-change] skipped: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
