#!/usr/bin/env python3
"""PreToolUse Hook（matcher `Agent`）—— 主会话派发 `core:*` 角色时带了 `name` 即拦下。

## 拦的是什么

Claude Code 开着 agent teams（`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`）时，主会话在交互会话里调
Agent 工具带 `name`，子 agent 会以 **in-process 队友**形态启动，而不是普通 subagent。对出厂角色的后果：

- SubagentStart 载荷的 `agent_type` 变成那个名字（不再是 `core:<role>`），`subagent-memory-inject.py`
  解析不到角色 → 只投 shared、**角色记忆投不到**；
- 官方口径里队友不应用定义的 `skills`；实测过的版本里角色正文与 `tools` 白名单也没有应用。

起名唯一换来的能力是「按名字续派」，而续派用派发结果返回的 agentId 走 `SendMessage` 同样成立
（官方：`SendMessage` 的 `to` 收 agent ID 或名字，且不要求开 teams）。所以纪律是**派发 `core:*`
不传 `name`**，本 hook 是它的机器闸。

## 形态：deny，不改写

返回 `permissionDecision: "deny"` ＋ 给模型看的理由（exit 0、JSON 走 stdout），模型按理由去掉
`name` 重新派发。**不用 `updatedInput` 把 `name` 静默删掉**：那等于替主会话改了派发语义（它以为
起了名、后面按名字续派会落空），且 `updatedInput` 必须搭配 `allow` / `ask`——替这次 Agent 调用做了
权限决策。拦下并说清怎么改，代价是多一轮重派，有界且看得见。

## 判定（四条全中才拦，其余一律零输出放行）

1. 载荷**不带 `agent_id`**——带它说明 hook 触发在 subagent / 队友内部，「带 name 变队友」只发生在
   主会话派发上；
2. `tool_input.subagent_type` 是以 `core:` 开头的字符串；
3. `tool_input.name` 是去空白后非空的字符串；
4. 载荷与 `tool_input` 都是 JSON 对象。

**不区分 teams 开没开**：判 teams 状态要读 hook 进程的环境变量，而开启面有 settings `env` / shell
环境多处，另有「交互会话才转队友」「带 `isolation` 不转」等条件——误判成「没开」就是静默放行。
没开 teams 时拦下的代价只是多一轮重派（`name` 能给的，agentId 都给得了），方向 fail-safe。

## 能力边界

- **只挂 Claude Code 门**：Codex 没有 Agent 工具与 agent teams，它的 `spawn_agent` 也不带 `name`，
  派生 Codex 清单时本脚本被显式排除（`_codex_hooks.CC_ONLY_SCRIPTS`）。
- **只管 `core:*`**：项目级角色（`.claude/agents/` 下的裸名定义）带 `name` 派发同样会变队友，本闸不拦。
- **只认工具名 `Agent`**：Claude Code 2.1.63 起子 agent 工具才叫 `Agent`（此前叫 `Task`）。更早的版本上
  matcher 不命中、本闸静默不触发；框架不为旧工具名兜底。
- 载荷读不了 / 形态不对 → 放行（fail-open）：一道解析不了载荷就拦下全部派发的闸，代价远大于收益。
- 只证「给定载荷判对了」；CC 是否真按 deny 拦下这次调用，要真实会话才验得了。
"""

import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

GUARDED_PREFIX = "core:"


def _read_payload():
    """stdin → dict；读不了 / 不是对象 → None。bytes 读 ＋ 显式 UTF-8（Windows 文本模式按本机代码页解码）。"""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return None
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        data = json.loads(raw) if raw.strip() else None
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def blocked_reason(payload):
    """命中拦截条件时返回给模型看的理由，否则返回 None。纯函数，单测直接调。"""
    if not isinstance(payload, dict) or payload.get("agent_id"):
        return None
    ti = payload.get("tool_input")
    if not isinstance(ti, dict):
        return None
    st, name = ti.get("subagent_type"), ti.get("name")
    if not (isinstance(st, str) and st.startswith(GUARDED_PREFIX)):
        return None
    if not (isinstance(name, str) and name.strip()):
        return None
    return (
        f"[workframe] 已拦下这次派发：派发 `{st}` 时带了 `name: {name!r}`。"
        "开着 agent teams 时，带 name 的派发会以通用队友形态启动——SubagentStart 拿到的 agent_type "
        "变成这个名字，角色记忆投不到，角色定义也可能不生效。"
        "改法：去掉 `name` 参数、其余参数原样重新调用 Agent；"
        "之后要续派这个 agent，用本次派发结果返回的 agentId 作为 SendMessage 的 `to`。"
        "（不论 teams 是否开启都按此拦：name 能给的按名续派，agentId 同样给得了。）"
    )


def main():
    reason = blocked_reason(_read_payload())
    if reason is None:
        return
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
