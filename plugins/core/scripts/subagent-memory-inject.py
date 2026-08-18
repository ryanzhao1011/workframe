#!/usr/bin/env python3
"""
SubagentStart Hook — 角色记忆注入

每次 subagent 派发时，把跨角色权威事实（shared/MEMORY.md）与该角色高置信记忆
（<role>/MEMORY.md）作为 additionalContext 注入 subagent 上下文，把启动协议的
"必读记忆"从协议约定（靠模型显式 Read）升级为代码保证（hook 注入）。

注入规则：
- shared/MEMORY.md：对所有 agent 注入（含 Explore / general-purpose 等内置 agent）——
  跨角色纪律（时间戳规范等）对任何执行者都适用。
- <role>/MEMORY.md：仅当 agent_type 映射出的角色记忆目录存在时追加注入。
  映射规则：agent_type 去掉 plugin 前缀（"core:pm" → "pm"）；项目级 agent 用裸名。
- notes.md 不注入（缓冲区，未达 D/U/R/A，维持启动协议口径）。
- 两份都不存在或为空 → 静默退出，零注入。

stdin: Claude Code SubagentStart JSON（含 agent_type / agent_id / cwd 等）。
stdout: hookSpecificOutput.additionalContext JSON。中文内容必须显式 UTF-8 输出，
否则 Windows 默认 GBK 会乱码（实测）。

标记头 "[workframe] 角色记忆注入" 与 agent-protocols.md §1 的兜底判据配对：
subagent 上下文有此标记 → 记忆已送达；无标记 → 按 §1 清单显式 Read 兜底。
"""

import io
import json
import os
import re
import sys
from pathlib import Path

MARKER = "[workframe] 角色记忆注入（SubagentStart hook 自动）"
ROLE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _read_memory(path: Path):
    """读取记忆文件；不存在 / 为空 / 读取失败均返回 None。

    编码用 utf-8-sig + errors="replace"：此前只捕 OSError，MEMORY.md 里混进非法字节
    就抛 UnicodeDecodeError 直接掀掉整个 hook，角色一条记忆都拿不到且没有任何提示。
    坏字节替换成 U+FFFD 至少让其余内容照常注入。
    """
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8-sig", errors="replace").strip()
        return text or None
    except OSError:
        return None


def main():
    # 包装本身也可能失败（stdin/stdout 已被关闭或替换过），失败就用原样的流继续
    try:
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or "."
    memory_dir = Path(project_dir).resolve() / ".claude" / "agent-memory"

    # agent_type → role：去 plugin 前缀（"core:pm" → "pm"）；防路径注入，只放行常规命名
    agent_type = payload.get("agent_type") or ""
    role = agent_type.rsplit(":", 1)[-1] if agent_type else ""
    if role and not ROLE_NAME_RE.match(role):
        role = ""

    sections = []
    shared = _read_memory(memory_dir / "shared" / "MEMORY.md")
    if shared is not None:
        sections.append(
            "=== .claude/agent-memory/shared/MEMORY.md（跨角色权威事实）===\n" + shared
        )
    if role and role != "shared":
        role_memory = _read_memory(memory_dir / role / "MEMORY.md")
        if role_memory is not None:
            sections.append(
                f"=== .claude/agent-memory/{role}/MEMORY.md（本角色高置信记忆）===\n" + role_memory
            )

    if not sections:
        return  # 零注入：无记忆可送（内置 agent + 无 shared，或非 workframe 项目）

    # 说明句按实际注入内容拼装——只注 shared 时明示原因，防 agent 误以为漏发角色记忆
    if len(sections) == 1:
        note = "启动协议必读记忆已由本段送达（本 agent 无角色记忆目录，仅含 shared），无需再显式 Read。\n\n"
    else:
        note = "启动协议必读记忆已由本段送达，无需再显式 Read 下列文件。\n\n"
    context = f"{MARKER}\n" + note + "\n\n".join(sections)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": context,
        }
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
