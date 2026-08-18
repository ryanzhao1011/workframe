#!/usr/bin/env python3
"""
SubagentStop Hook — 活动日志

每次 subagent 结束时记录活动，作为收尾协议的安全网。
从 stdin 读取 Claude Code 提供的 JSON metadata，追加日志到项目的 logs/subagent-activity.log。

路径策略（v7.1 plugin 化后）：
- 项目根：$CLAUDE_PROJECT_DIR
- Log 文件：<project>/logs/subagent-activity.log

环境依赖：仅 Python 标准库；hook command 默认用 `python`，macOS/Linux 用户如环境只有 `python3`，
请在项目 `.claude/settings.json` 中自行覆盖 hooks.command 或调整 shell 别名（框架仓用户文档 quickstart 有各平台说明）。
"""

import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path


PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
LOG_FILE = PROJECT_DIR / "logs" / "subagent-activity.log"


# Claude Code 内置 agent 名（不会出现在 .claude/agents/ 或 plugin agents/ 下，必须硬编码）
# "claude" = FleetView 默认 agent（CC 2.1.1xx 起）；新增内置名漏列时只影响日志归属显示（降级为 "subagent"），不影响功能
BUILTIN_AGENTS = [
    "Explore", "Plan", "general-purpose", "claude-code-guide", "statusline-setup", "claude",
]


def _discover_known_agents():
    """动态发现项目可用的 agent 名（M1 P0 修复）。

    旧版硬编码固定的 core role 与内置 agent 名单，项目级 custom role（如 finance / ceo）
    的 subagent 活动会被记成 "subagent" 通用名，影响日志可读性。

    新版扫描两个来源拼出实际 agent 列表：
      - 项目级：${CLAUDE_PROJECT_DIR}/.claude/agents/*.md（含用户手工新增 + override）
      - Plugin 级：${CLAUDE_PLUGIN_ROOT}/agents/*.md（core baseline 角色）
    再加 Claude Code 内置 agent 名（Explore/Plan 等不通过 .md 文件注册）。

    任一来源缺失 / 读取失败时只跳过该来源，不影响其他来源；最终至少返回内置名单。
    """
    discovered = set(BUILTIN_AGENTS)

    # 项目级 agents
    project_agents_dir = PROJECT_DIR / ".claude" / "agents"
    if project_agents_dir.exists():
        try:
            for agent_file in project_agents_dir.glob("*.md"):
                discovered.add(agent_file.stem)
        except OSError:
            pass

    # Plugin 级 agents
    # Codex F3 fixup: ${CLAUDE_PLUGIN_ROOT} 在某些 Claude Code 版本/上下文可能不可用，
    # 加 __file__ 推导 fallback（本脚本位于 plugins/core/scripts/，向上 2 级即 plugin root）
    plugin_root_env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    plugin_agents_candidates = []
    if plugin_root_env:
        plugin_agents_candidates.append(Path(plugin_root_env) / "agents")
    plugin_agents_candidates.append(Path(__file__).resolve().parent.parent / "agents")

    for plugin_agents_dir in plugin_agents_candidates:
        if plugin_agents_dir.exists():
            try:
                # CC 对 plugin agent 用带前缀的全名（core:pm），transcript 文件名为 core-pm-{id} 形态；
                # 两种命名都注册，保证前缀形态也能归属到具体角色而非降级 "subagent"
                plugin_name = plugin_agents_dir.parent.name  # plugins/core → "core"
                for agent_file in plugin_agents_dir.glob("*.md"):
                    discovered.add(agent_file.stem)
                    discovered.add(f"{plugin_name}-{agent_file.stem}")
                break  # 第一个找到的 plugin agents 目录即可，不重复扫
            except OSError:
                continue

    return discovered


def extract_agent_type_from_path(transcript_path):
    """从 transcript_path 中提取 subagent 类型。
    路径格式示例：
      .../subagents/agent-{id}.jsonl （通用格式，返回 "subagent"）
      .../subagents/{agent-name}-{id}.jsonl （命名格式，返回具体名称）
    """
    if not transcript_path:
        return ""
    if "subagents" not in transcript_path:
        return ""
    filename = os.path.basename(transcript_path).replace(".jsonl", "")
    known_agents = _discover_known_agents()
    # 长名优先匹配，避免短名角色误吞长名前缀（如项目自定义 "dev-ops" 被 "dev" 抢先命中）
    for name in sorted(known_agents, key=len, reverse=True):
        if filename.startswith(name + "-") or filename == name:
            return name
    return "subagent"


def main():
    # Windows 上强制 UTF-8 读写三条流，防止 GBK/UTF-8 编码冲突
    try:
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

    raw_input = ""
    data = {}
    error_info = None

    try:
        # Windows 下 sys.stdin 默认走 locale codec（cp936），含中文字段时 mojibake；
        # 强制 stdin.buffer 二进制读 + utf-8 decode，跨 Win/Mac/Linux 一致
        if not sys.stdin.isatty():
            raw_input = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        if raw_input:
            data = json.loads(raw_input)
    except json.JSONDecodeError as e:
        error_info = f"parse_error=JSONDecodeError pos={e.pos}"
        if raw_input:
            error_info += f" raw_input={raw_input[:300]!r}"
    except Exception as e:
        error_info = f"parse_error={type(e).__name__}"
        if raw_input:
            error_info += f" raw_input={raw_input[:300]!r}"

    if not isinstance(data, dict):
        # 顶层非 object（`[]` / `42`）时下面的 .get() 会抛，与本 hook「不阻塞会话」
        # 的定位冲突
        error_info = (error_info or "") + f" non_object_payload={type(data).__name__}"
        data = {}

    # 字段映射：兼容 Claude Code 实际 schema
    # 实际 schema: session_id, transcript_path, cwd, permission_mode, agent_id, agent_type (有时)
    agent_type = data.get("agent_type", "")
    agent_id_raw = data.get("agent_id", "")
    transcript_path = data.get("transcript_path", "") or data.get("agent_transcript_path", "")
    last_message = data.get("last_assistant_message", "")

    # 若 agent_type 缺失，尝试从 transcript_path 推断
    if not agent_type and transcript_path:
        agent_type = extract_agent_type_from_path(transcript_path) or "unknown"
    elif not agent_type:
        agent_type = "unknown"

    # agent_id 截断到 8 位
    agent_id = agent_id_raw[:8] if agent_id_raw else "unknown"

    # 摘要：取 last_assistant_message 前 200 字符
    message_summary = ""
    if last_message:
        message_summary = last_message[:200].replace("\n", " ").strip()

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 写日志失败（磁盘满 / 权限 / 路径被占）不能让 hook 非零退出——其余 hook 都吞了，
    # 只有这里裸奔
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8", newline="") as f:
            line = f"{timestamp} | {agent_type} | {agent_id} | stopped"
            if transcript_path:
                line += f" | transcript={transcript_path}"
            if message_summary:
                line += f" | summary={message_summary}"
            f.write(line + "\n")
            # 必须在 with 块内：给主写入加 try 时，这两行被留在了 except
            # 之后——正常路径永远走不到（PARSE_ERROR 审计静默失效），异常路径又对着
            # 已关闭的文件对象 write，反而让这个「永远 exit 0」的 hook 非零退出。
            if error_info and not data:
                f.write(f"{timestamp} | PARSE_ERROR | {error_info}\n")
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
