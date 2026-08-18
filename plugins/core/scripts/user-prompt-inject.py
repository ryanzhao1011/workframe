#!/usr/bin/env python3
"""
UserPromptSubmit Hook — 每会话首次 prompt 注入维护上下文（v0.2.1）

v0.2.1 变更：
  - pending_maintenance 改为对象列表读取（按 §17.8 schema），展示 item["details"] 而非字符串 slice
  - wake_up_pending=true 时与 dormant=true 并列静默（首次唤醒会话用户只看 wake-up 摘要，不叠加维护提示）
  - 仅展示 status=open 的条目；按 severity 排序（critical > warn > info）

输出协议：向 stdout 写 JSON：
  {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "<text>"}}
"""

import io
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


# 同目录公共模块：状态文件的锁 / 原子写 / 损坏隔离只有一份实现（见 _state_io.py 抬头）
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from _state_io import load_activity, save_activity as _save_activity  # noqa: E402


PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
STATE_DIR = PROJECT_DIR / ".claude" / "workframe-state"
ACTIVITY_FILE = STATE_DIR / "activity-state.json"

# critical 是**预留档**：当前 producer（check-iteration-trigger）只写 info / warn，
# 模板 schema 也只声明这两值。保留它是为了将来新增更高优先级信号时排序不用改这里；
# 排序表容纳未使用的档位无副作用（未知 severity 走 .get 的默认值 9 排最后）。
SEVERITY_ORDER = {"critical": 0, "warn": 1, "info": 2}


def load_state():
    """字段全集与损坏处理见 `_state_io`（四个 hook 共用一份实现）。

    此前这里损坏时返回 `{}`——写回就把 session_counter / pending_maintenance 抹平了。
    """
    return load_activity(STATE_DIR)


def save_state(state):
    _save_activity(STATE_DIR, state)


def emit_additional_context(text):
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        }
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def format_pending_items(items):
    """按 severity 排序，返回 (lines, total_open_count)。只处理 status=open 的对象条目。"""
    open_items = [
        it for it in items
        if isinstance(it, dict) and it.get("status") == "open"
    ]
    open_items.sort(key=lambda it: SEVERITY_ORDER.get(it.get("severity", "info"), 9))
    return open_items


def main():
    state = load_state()
    # dormant 或 wake_up_pending 都不注入（首次唤醒会话用户只看 wake-up 摘要）
    if state.get("dormant") or state.get("wake_up_pending"):
        sys.exit(0)

    session_marker = str(state.get("session_counter", 0))
    last_injected = state.get("last_prompt_injected_session_id")
    if last_injected == session_marker:
        sys.exit(0)

    items = state.get("pending_maintenance") or []
    open_items = format_pending_items(items)

    if open_items:
        lines = [f"[workframe] 待处理维护项 {len(open_items)} 条（按 severity 排序）："]
        for item in open_items[:5]:
            sev = item.get("severity", "info")
            pid = item.get("id", "?")
            kind = item.get("kind", "?")
            details = item.get("details", "")
            lines.append(f"  - [{sev}] {pid} ({kind}) — {details}")
        if len(open_items) > 5:
            lines.append(f"  - …另 {len(open_items) - 5} 条，/core:audit 查看全部")
        emit_additional_context("\n".join(lines))

    state["last_prompt_injected_session_id"] = session_marker
    try:
        save_state(state)
    except Exception as e:
        print(f"[warn] failed to save activity-state: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
