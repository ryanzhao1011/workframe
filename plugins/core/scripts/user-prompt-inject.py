#!/usr/bin/env python3
"""
UserPromptSubmit Hook — 每会话首次 prompt 注入维护上下文（v0.2.1）

v0.2.1 变更：
  - pending_maintenance 改为对象列表读取（按 §17.8 schema），展示 item["details"] 而非字符串 slice
  - wake_up_pending=true 时与 dormant=true 并列静默（首次唤醒会话用户只看 wake-up 摘要，不叠加维护提示）
  - 仅展示 status=open 的条目；按 severity 排序（critical > warn > info）

顺带：提示里写了 `$skill` 时投该 skill 映射到的角色记忆（主会话角色记忆投递的 Codex 门入口；Codex 用 `$skill`
显式选用 skill，CC 的对应入口是 UserPromptExpansion，在 log-skill-invoked.py）。与维护提醒合并成一次输出。

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
import _harness  # noqa: E402
from _harness import project_dir as _project_dir  # noqa: E402
from _state_io import (  # noqa: E402
    load_activity, save_activity as _save_activity, state_dir_of,
)


PROJECT_DIR = _project_dir()
STATE_DIR = state_dir_of(PROJECT_DIR)
ACTIVITY_FILE = STATE_DIR / "activity-state.json"

# critical 是**预留档**：当前 producer（check-iteration-trigger）只写 info / warn，
# 模板 schema 也只声明这两值。保留它是为了将来新增更高优先级信号时排序不用改这里；
# 排序表容纳未使用的档位无副作用（未知 severity 走 .get 的默认值 9 排最后）。
SEVERITY_ORDER = {"critical": 0, "warn": 1, "info": 2}
# 维护提醒的长度上限：它与 `$skill` 投递的角色记忆合并成一条输出，记忆的预算要扣掉提醒占用，提醒不封顶的话
# 记忆能剩下的预算可以是零（正常提醒五条、每条约百字，远到不了这个数）
REMINDER_MAX = 2000


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


def mentioned_skill_memory(payload, used=0):
    """提示里写了 `$skill`（Codex 显式选用 skill 的写法）→ 该 skill 映射到的角色记忆正文，或 None。

    主会话角色记忆投递的入口之一（判定与去重在 `_role_memory`）；与维护提醒无关——dormant、本会话已提醒过都不挡投递。
    `used`：同一条输出里维护提醒已占的 UTF-16 码元数，从投递预算里扣掉（两段合并后仍在 CC 单条上限内）。任何异常都吞掉。
    """
    try:
        prompt = payload.get("prompt") if isinstance(payload, dict) else None
        if not isinstance(prompt, str) or "$" not in prompt:
            return None
        import _role_memory
        skills = _role_memory.skills_mentioned(prompt, PROJECT_DIR)
        return _role_memory.deliver_for_skills(PROJECT_DIR, payload, skills, _role_memory.TRIGGER_SKILL_MENTION,
                                               budget=_role_memory.DELIVER_BUDGET - used)
    except Exception:
        return None


def read_payload():
    """hook 载荷（bytes ＋ UTF-8）；非管道 / 空 / 不合法一律当空字典。"""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def maintenance_text(state):
    """每会话首次 prompt 的维护提醒正文，或 None（dormant / 唤醒 / 本会话已提醒 / 无待处理项）。会改 state。"""
    # dormant 或 wake_up_pending 都不注入（首次唤醒会话用户只看 wake-up 摘要）
    if state.get("dormant") or state.get("wake_up_pending"):
        return None

    session_marker = str(state.get("session_counter", 0))
    last_injected = state.get("last_prompt_injected_session_id")
    if last_injected == session_marker:
        return None

    items = state.get("pending_maintenance") or []
    open_items = format_pending_items(items)
    from _selfiter_hooks import suppress_cadence
    if suppress_cadence(PROJECT_DIR):
        open_items = [item for item in open_items if item.get('kind') not in (
            'cadence_timeout', 'activity_threshold', 'completed_delta')]
    text = None
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
        text = "\n".join(lines)

    state["last_prompt_injected_session_id"] = session_marker
    try:
        save_state(state)
    except Exception as e:
        print(f"[warn] failed to save activity-state: {e}", file=sys.stderr)
    return text


def main():
    # Codex 门下会话不在 workframe 项目内：零写入、零输出退出（判定与门条件只在 _harness 一处）
    if _harness.hook_outside_project():
        return 0
    from _selfiter_policy import in_job
    if in_job():
        return 0
    payload = read_payload()
    reminder = maintenance_text(load_state())
    if reminder and len(reminder) > REMINDER_MAX:
        reminder = reminder[:REMINDER_MAX].rstrip() + "…（已截断，/core:audit 查看全部）"
    used = len(reminder.encode("utf-16-le")) // 2 + 2 if reminder else 0   # UTF-16 码元 ＋ 两段之间的空行
    memory = mentioned_skill_memory(payload, used)
    from _selfiter_hooks import on_hook
    selfiter = on_hook(PROJECT_DIR, _harness.harness(), 'UserPromptSubmit', _harness.session_id(payload))
    parts = [t for t in (reminder, memory, selfiter) if t]
    if parts:
        emit_additional_context("\n\n".join(parts))
    sys.exit(0)


if __name__ == "__main__":
    main()
