---
name: session-digest
description: 生成本次会话的变更摘要，写入 session-digest-latest.md 供下次会话启动时展示
user-invocable: false
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# Session Digest Skill

## 用途

⚠️ **重要——执行模型说明**：SessionEnd hook 由 Python 脚本独立运行（见 `session-end-flush.py`）。两个关键事实：

1. hook 结束后 Claude Code runtime **不会**再调度任何 skill 执行
2. hook 在 SessionEnd 时**无条件覆盖** `.claude/workframe-state/session-digest-latest.md`（见 `session-end-flush.py` 的 `write_digest_skeleton()`：`DIGEST_FILE.write_text(content)` 不做任何"是否已富填充"的检查）

因此本 skill 的**唯一有效触发路径**是：

- **下次 SessionStart 二次填充**：Claude 看到 hook 写的骨架内容简陋时主动调用本 skill，从 events.jsonl / activity-state.json / changelog.md 中重建本次会话摘要，**覆写** `session-digest-latest.md`

**不存在**"会话末尾 best-effort 预写覆盖 hook 骨架"的有效路径——hook 总是后写，任何会话末尾的 skill 预写都会被 hook 骨架覆盖。富内容只能在下次 SessionStart 重建。

## 输入

无参数。读取以下文件：
- `.claude/workframe-state/events.jsonl`（本 session 内的事件，按 ts 范围过滤）
- `.claude/workframe-state/activity-state.json`（last_session_at / pending_maintenance）
- `.claude/workframe-state/session-digest-latest.md`（骨架或上次内容）
- `projects/changelog.md`（如有）近期条目

## 输出

覆盖写入 `.claude/workframe-state/session-digest-latest.md`：

```markdown
# Session Digest (latest)

- session_ended_at: <ISO-8601>
- session_counter: <N>
- exit_reason: <clear|resume|logout|prompt_input_exit|bypass_permissions_disabled|other>

## Auto changes (T1/T2)
- <T1 静默：events append / metrics 重算次数>
- <T2 摘要：Librarian 自动提升 ×N；memory decay 提案 ×M>

## Pending maintenance (T3/T4)
- <T3 eval 等待执行>
- <T4 需用户确认：如 core 文件变更提案、MEMORY 冲突>

## Flags
- dormant_profile: <profile>
```

## 何时调用

- **下次 SessionStart 二次填充**（唯一有效写入路径）：SessionStart hook 打印 `[last-session-digest]` 时若内容仍是骨架占位，Claude 在响应中主动调用本 skill，从 events.jsonl 中重建摘要并覆写 digest 文件
- **/core:audit 间接读取**：audit 只读 digest 文件做汇总，不重写本文件本身

> **不要在会话末尾调用本 skill**：SessionEnd hook 会在你写入后无条件覆盖 digest 文件（见上文执行模型说明），任何会话末尾的预写都是沉没成本。

## 约束

- digest 必须人类可读，不放 raw JSON
- 不改动 events.jsonl / activity-state.json，只读 + 写 digest
- 若 events.jsonl 近期无新事件，digest 写 "No auto changes this session."
