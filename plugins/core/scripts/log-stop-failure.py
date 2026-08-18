#!/usr/bin/env python3
"""
StopFailure Hook — turn 因 API 错误终止时的审计日志（v0.4 M5）

CC 2.1.78 新增 StopFailure 事件：turn 因 rate limit / auth 失败等 API 错误结束时触发。
本 hook 只做一件事：向 events.jsonl append 一条 `turn_failed` 事件，为 audit /
session-digest 提供 "SessionEnd 链路可能没拿到完整状态" 的旁证。

设计约束（与其余 workframe hooks 一致）：
  - 纯 best-effort：任何异常都吞掉，永远 exit 0，绝不阻塞会话
  - stdin JSON 字段防御性读取（CC 未来增删字段不破坏本脚本）
  - 不产生 stdout 输出（StopFailure 无注入需求）
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


def main():
    # Windows 的 sys.stdin 默认走 locale codec（cp936）：hook payload 里带中文时
    # 解出来是 mojibake，甚至产生无法再编码回 utf-8 的 surrogate，事件随之写不进去。
    # 与 log-subagent-activity.py 同款：走 buffer 二进制读 + utf-8 显式解码。
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace") \
            if not sys.stdin.isatty() else ""
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        # 顶层不是 object（`[]` / `42`）时 .get() 会抛，与 docstring 承诺的
        # 「永远 exit 0」冲突
        data = {}

    event = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "type": "turn_failed",
    }
    session_id = data.get("session_id")
    if session_id:
        event["session_id"] = session_id
    # 错误信息字段名做多候选防御（CC schema 未硬承诺）
    detail = None
    for key in ("error", "error_message", "errorMessage", "reason", "message"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            detail = val.strip()
            break
    if detail:
        event["detail"] = detail[:200]

    try:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_FILE.open("a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[log-stop-failure] skipped: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
