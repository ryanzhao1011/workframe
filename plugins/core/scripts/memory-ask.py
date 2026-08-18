#!/usr/bin/env python3
"""
SessionStart Hook — 记忆整理询问式触发（v0.4 G2#7）

频控条件满足时，通过 `initialUserMessage` 在会话开场注入一条系统自启动的询问消息，
让模型用 AskUserQuestion 问用户「现在整理记忆积压吗？」。这是记忆整理的**主通道**：
触发权在代码（hook_deterministic），内容判断（哪些条目值得提升）仍在 librarian skill。

四道闸门（2026-08-06 用户拍板「更克制」，全过才问）：
  1. source == startup（CC 对 initialUserMessage 原生只在 startup 生效，此处双保险）
  2. notes 积压 ≥ ASK_BACKLOG_MIN_ENTRIES 条（`### ` 章节头计数，与排空跑「段」口径一致）
  3. 每日 ≤ 1 次（last_asked_date 日期比较）
  4. 拒绝后冷却 ASK_REFUSAL_COOLDOWN_SESSIONS 个会话（以 activity-state session_counter 计）

另有两道静默条件：
  - dormant / wake_up_pending（与其他 hook 一致，维护链路暂停时不打扰）
  - maintenance 批处理旗标新鲜（--maintenance 会话中 SessionStart 照常触发——2026-08-06
    沙盒实测；工单已含积压项，重复询问无意义，见 maintenance_workorder.py）

子命令：
  --record-refusal   用户在开场卡选「跳过」后由模型调用，记录冷却起点

实测依据（2026-08-06 沙盒）：initialUserMessage 是真用户消息、模型会先完整响应它一轮；
hook stdout 中文在 Windows 默认 GBK 会乱码，必须显式 UTF-8。
"""

import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
STATE_DIR = PROJECT_DIR / ".claude" / "workframe-state"
ACTIVITY_FILE = STATE_DIR / "activity-state.json"
ASK_STATE_FILE = STATE_DIR / "memory-ask-state.json"
MAINT_FLAG_FILE = STATE_DIR / "maintenance-run.flag"
MEMORY_DIR = PROJECT_DIR / ".claude" / "agent-memory"
CANDIDATES_FILE = STATE_DIR / "promotion-candidates.md"

# 频控参数（2026-08-06 用户拍板「更克制」；validate.py 锁定取值防漂移）
ASK_BACKLOG_MIN_ENTRIES = 5          # 积压 ≥5 条才问
ASK_REFUSAL_COOLDOWN_SESSIONS = 5    # 拒绝后冷却 5 个会话
MAINT_FLAG_MAX_AGE_MIN = 30          # maintenance 旗标有效期（分钟），过期视为无效


def count_notes_entries(memory_dir):
    """数各角色 notes.md 的积压条目数——`### ` 章节头与顶层日期列表条目两种口径取 max。

    真实写入存在两种条目形态（rules 不约束 notes 格式）：`### ` 章节式与
    `- YYYY-MM-DD：` 顶层列表式（含 `- [纠正] …` 变体）。二者取 max：
    漏计 = 积压对真实数据失明（高危）；过计只是提早询问（fail-safe 方向）。
    返回 {scope: count}（仅收录 count>0）。纯前缀统计，无语义判断——
    条目够不够提升、是不是同主题，交给 librarian skill 判断。
    本函数在 memory-ask.py 与 maintenance_workorder.py 各有一份，validate.py 锁定逐字一致。
    """
    out = {}
    if not memory_dir.exists():
        return out
    for notes in sorted(memory_dir.glob("*/notes.md")):
        try:
            text = notes.read_text(encoding="utf-8")
        except Exception:
            continue
        n_hash = n_list = 0
        for ln in text.splitlines():
            if ln.startswith("### "):
                n_hash += 1
            elif re.match(r"^- (?:\[[^\]]+\] )?\d{4}-\d{2}-\d{2}", ln):
                n_list += 1
        n = max(n_hash, n_list)
        if n > 0:
            out[notes.parent.name] = n
    return out


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def save_ask_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    ASK_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                              encoding="utf-8", newline="")


def maintenance_flag_fresh():
    """maintenance 批处理旗标存在且未过期 → 本会话静默（工单流程已覆盖积压处理）。"""
    if not MAINT_FLAG_FILE.exists():
        return False
    try:
        age_min = (datetime.now(timezone.utc).timestamp() - MAINT_FLAG_FILE.stat().st_mtime) / 60
        return age_min < MAINT_FLAG_MAX_AGE_MIN
    except Exception:
        return False


def record_refusal():
    activity = load_json(ACTIVITY_FILE, {})
    counter = activity.get("session_counter", 0)
    state = load_json(ASK_STATE_FILE, {"__schema__": "workframe.memory-ask-state.v1"})
    state["last_refused_session"] = counter
    state["last_refused_at"] = datetime.now(timezone.utc).isoformat()
    save_ask_state(state)
    print(f"[memory-ask] 已记录跳过：{ASK_REFUSAL_COOLDOWN_SESSIONS} 个会话内不再询问"
          f"（当前 session_counter={counter}）。")
    return 0


def build_message(backlog):
    total = sum(backlog.values())
    detail = " / ".join(f"{scope} {n} 条" for scope, n in sorted(backlog.items()))
    py = sys.executable or "python"
    script = str(Path(__file__).resolve())
    return (
        f"[workframe 记忆巡检 · 这是系统定期自启动的检查，不是用户输入] "
        f"notes 积压 {total} 条待整理（{detail}）。请按以下步骤执行：\n"
        f"1. 立即用 AskUserQuestion 向用户提问「现在整理记忆积压吗？」，三个选项：\n"
        f"   「全部处理」/「只处理最旧 1 条（条目日期最早者）」/「本次跳过」。\n"
        f"2. 用户选处理 → 调用 core:librarian skill 按所选范围整理。本场景约束：\n"
        f"   L1（角色记忆提升/归档/事件记账）照常自动完成；L2 候选（涉及 rules / CLAUDE.md /\n"
        f"   skills / 删除 / 降级）一律不当场处理，追加到 .claude/workframe-state/promotion-candidates.md\n"
        f"   攒卡（格式见 librarian SKILL），仅在结果里提示用户待拍板。\n"
        f"   兜底：Skill 工具不可用时改为 Read \"{Path(script).parents[1] / 'skills' / 'librarian' / 'SKILL.md'}\"\n"
        f"   按 SOP 执行；.claude/workframe-state/ 写入被拦时不要绕过——把 sidecar/事件记账\n"
        f"   诉求整理成清单在结果中输出，由用户在交互会话补齐。\n"
        f"3. 用户选「本次跳过」→ 用 Bash 运行：\n"
        f"   \"{py}\" \"{script}\" --record-refusal\n"
        f"   （记录后 {ASK_REFUSAL_COOLDOWN_SESSIONS} 个会话内不再问），然后结束本轮等待用户输入。"
    )


def main():
    if "--record-refusal" in sys.argv[1:]:
        return record_refusal()

    # 闸门 0：交互入口才问——headless（claude -p）撞开场卡会空耗一轮响应，且脚本化
    # 接续时敏感闸拦记账留三账不齐（G7 J4 实证）。判据 = CLAUDE_CODE_ENTRYPOINT
    # 白名单静默（2026-08-07 沙盒实测：交互 cli / print 模式 sdk-cli）；未知值照常
    # 出卡——CC 新增入口名时最坏回到旧行为（headless 误问），绝不新增交互失明
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "") in ("sdk-cli", "sdk-ts", "sdk-py"):
        return 0

    # 闸门 1：source == startup（stdin 解析失败时按 startup 继续——可用性优先，
    # CC 对非 startup 来源本就忽略 initialUserMessage）
    source = "startup"
    try:
        payload = json.load(sys.stdin)
        source = payload.get("source", "startup")
    except Exception:
        pass
    if source != "startup":
        return 0

    activity = load_json(ACTIVITY_FILE, {})
    if activity.get("dormant") or activity.get("wake_up_pending"):
        return 0
    if maintenance_flag_fresh():
        return 0

    # 闸门 2：积压条数
    backlog = count_notes_entries(MEMORY_DIR)
    if sum(backlog.values()) < ASK_BACKLOG_MIN_ENTRIES:
        return 0

    ask_state = load_json(ASK_STATE_FILE, {"__schema__": "workframe.memory-ask-state.v1"})

    # 闸门 3：每日 ≤1 次
    today = datetime.now().date().isoformat()
    if ask_state.get("last_asked_date") == today:
        return 0

    # 闸门 4：拒绝冷却
    counter = activity.get("session_counter", 0)
    refused_at = ask_state.get("last_refused_session")
    if isinstance(refused_at, int) and counter - refused_at < ASK_REFUSAL_COOLDOWN_SESSIONS:
        return 0

    # 全过 → 记账（先记后问：即使本轮意外中断，明天才会再问）+ 注入询问
    ask_state["last_asked_date"] = today
    ask_state["last_asked_session"] = counter
    try:
        save_ask_state(ask_state)
    except Exception as e:
        print(f"[warn] memory-ask state save failed: {e}", file=sys.stderr)
        return 0  # 记不了账就不问，防止每会话重复打扰

    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "initialUserMessage": build_message(backlog),
        }
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    # exit-audited: main() 的全部 return 均为 0（含各早退分支），无非零路径
    sys.exit(main())
