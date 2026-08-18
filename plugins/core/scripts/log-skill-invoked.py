#!/usr/bin/env python3
"""统一 skill 调用 logger：把两个入口的 skill 调用都记成 `skill_invoked` 事件。

## 为什么需要它

`skill_used` 由 agent 在 wrap-up 里**自己写**（protocol_expected 层），漏写就没有。
更要命的是**用户直敲 `/core:module-init` 这类命令时根本不经过 Skill 工具**——命令被展开成
prompt 直接送给模型，`PostToolUse(matcher: Skill)` 永远等不到。于是「谁用了哪个 skill」
这件事在最常见的入口上是盲的。

本脚本挂两个入口，产出 hook_deterministic 层的 `skill_invoked`：

  - `UserPromptExpansion`：用户直敲 `/skill-name`，命令展开成 prompt 之前触发
  - `PostToolUse(matcher: Skill)`：模型主动调用 Skill 工具

## 与 skill_used 的关系：并存，不合并

`skill_invoked` 回答「**被调起了**」（代码保证），`skill_used` 回答「**用了并产出了什么**」
（含 success 自评，模型写）。二者可靠性层级不同、语义不同，**任何消费方都不要把它们相加**
——同一次调用两个事件都会有，相加即双计。

## 幂等与去重

同一次调用只应产生一条。两个入口理论上互斥（直敲走展开、模型调走工具），但为防将来
CC 行为变化，这里按 `(session_id, skill, 分钟级 ts)` 做一次轻量去重：同窗口同 skill
已有记录则跳过。**去重是尽力而为，不保证跨进程原子**——事件流是观测用途，宁可偶尔多一条
也不要为它上锁拖慢 hook。
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

EVENT_TYPE = "skill_invoked"


def _state_dir():
    proj = os.environ.get("CLAUDE_PROJECT_DIR") or "."
    return Path(proj).resolve() / ".claude" / "workframe-state"


SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+)?$")


def _normalize(raw, require_slash):
    """把 `/core:module-init`、`core:module-init`、`module-init` 统一成裸 skill 名。

    **与 `skill_used` 的 skill 字段对齐**：agent wrap-up 按协议写的是裸名（`module-init`），
    而命令展开入口拿到的是带 plugin 前缀的全名。两边不统一时，同一次调用产生的两条事件
    会用两个名字，任何按 skill 名对账 / 聚合的消费方都会把它算成两个不同的 skill。

    `require_slash`：取自**自然文本字段**（prompt 等）时必须为 True。那些字段装的可能是
    用户随手打的一句话，而「首 token + 命名正则」对英文句子照样成立——实测
    `please help me fix this bug` 会被记成 `skill='please'`，往事件流里灌垃圾。
    只有以 `/` 开头才当命令解析；`command` 这类字段本身就是命令名，不受此限。
    """
    s = str(raw or "").strip()
    if not s:
        return None, None
    if require_slash and not s.startswith("/"):
        return None, None
    name = s.lstrip("/").strip()
    # 命令行形态：`/core:audit 7d` —— 只取首个 token
    name = name.split()[0] if name else ""
    if not SKILL_NAME_RE.match(name):
        return None, None
    plugin = name.split(":")[0] if ":" in name else None
    return name.split(":")[-1], plugin


def _extract(payload):
    """从 hook stdin 里取 (skill_name, entry, plugin)。取不到 skill 名就返回 None——
    宁可不记，也不要往事件流里灌一堆 skill=null 的噪声。

    UserPromptExpansion 的字段名做**多候选防御**（同 log-stop-failure / log-config-change）：
    CC 未对该 hook 的 stdin schema 做硬承诺，命令名可能在 `command` / `command_name`，
    也可能只给展开前的原始 prompt 文本（形如 `/core:audit 7d`）。此前只认 `command`，
    若实际字段是 `prompt`，这个**最常用的 skill 入口**会 100% 静默失效——事件一条不产，
    而所有消费方都以为「没人用 skill」。宁可多认几个字段名，也不要赌单一字段。
    """
    ev = payload.get("hook_event_name") or ""
    if ev == "UserPromptExpansion":
        # 两类字段的解析口径不同：命令名字段可以不带 `/`；自然文本字段必须带，
        # 否则用户随手一句英文就被记成 skill（见 _normalize 的 require_slash）
        for key, need_slash in (("command", False), ("command_name", False),
                                ("prompt", True), ("user_prompt", True),
                                ("expanded_prompt", True)):
            name, plugin = _normalize(payload.get(key), need_slash)
            if name:
                return (name, "command_expansion", plugin)
        return (None, None, None)
    if ev == "PostToolUse":
        ti = payload.get("tool_input") or {}
        name, plugin = _normalize(ti.get("skill") or ti.get("name"), False)
        return (name, "skill_tool", plugin) if name else (None, None, None)
    return (None, None, None)


def _is_dup(events_file, session_id, skill, minute):
    """同 session + 同 skill + 同分钟已记过则跳过。

    实现是「读全文、只比对尾部 200 行」——不是 seek 回退。events.jsonl 通常在几 MB
    量级，一次性读入比分块回读简单得多，不值得为它引入额外复杂度；真正要防的是
    **比对**范围过大，而不是读盘本身。去重尽力而为、不保证跨进程原子：事件流是观测
    用途，宁可偶尔多一条，也不要为它上锁拖慢每一次 hook。
    """
    if not events_file.exists():
        return False
    try:
        with events_file.open("r", encoding="utf-8", errors="replace") as f:
            tail = f.readlines()[-200:]
    except OSError:
        return False
    for line in reversed(tail):
        line = line.strip()
        if not line or EVENT_TYPE not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if (e.get("type") == EVENT_TYPE and e.get("skill") == skill
                and e.get("session_id") == session_id
                and str(e.get("ts", ""))[:16] == minute):
            return True
    return False


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0  # stdin 不是 JSON：静默退出，绝不阻塞用户的命令

    skill, entry, plugin = _extract(payload)
    if not skill:
        return 0

    # ts 用 UTC + 秒级（见文件头 ts 口径说明）
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    session_id = payload.get("session_id") or os.environ.get("CLAUDE_CODE_SESSION_ID") or ""

    state = _state_dir()
    events_file = state / "events.jsonl"
    if _is_dup(events_file, session_id, skill, ts[:16]):
        return 0

    record = {"ts": ts, "type": EVENT_TYPE, "skill": skill, "entry": entry}
    if plugin:
        record["plugin"] = plugin      # 前缀单独存，skill 字段与 skill_used 保持同一命名空间
    if session_id:
        record["session_id"] = session_id
    if payload.get("agent_type") or payload.get("subagent_type"):
        record["agent_type"] = payload.get("agent_type") or payload.get("subagent_type")

    try:
        state.mkdir(parents=True, exist_ok=True)
        with events_file.open("a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return 0  # 写不进去也不能拦住用户
    return 0


if __name__ == "__main__":
    # exit-audited: main() 的全部 return 均为 0——设计上「写不进去也不能拦住用户」
    sys.exit(main())
