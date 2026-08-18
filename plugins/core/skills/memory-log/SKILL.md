---
name: memory-log
description: 查看记忆层活动流水（promotion / migration / decay / correction / memory rollback）——从 events.jsonl 重组时间线，并用 memory-index sidecar 补充每条的当前状态。只读，不改记忆。
when_to_use: |
  用户问「记忆里最近变了什么 / 这条是什么时候记的 / 为什么这条没了 / 它怎么跑到别的域去了 / 谁改的记忆」时；
  排查记忆条目异常（该在的不在、该降级的还在）时；回滚记忆前先看变更历史时。
  边界：要执行整理提升 → librarian；要回滚 → rollback。
user-invocable: true
disable-model-invocation: true
effort: low
allowed-tools: [Read, Glob, Grep, Bash]
---

# /core:memory-log 记忆活动流水

## 用途

用户显式 `/core:memory-log` 时执行。**Claude 不会自动调用**。

回答：最近记忆层发生了什么？谁加了什么？谁降级了什么？哪些纠正进入了高置信记忆？shared 有没有新共识？

## 输入

- 无参数 → 默认近 14 天
- `7d` / `30d` → 窗口
- `shared` → 仅 shared/ 范围
- `<role>` → 仅某个角色

## 执行步骤

1. Read `.claude/workframe-state/events.jsonl`，逐行 JSON 解析；跳过 `__schema__` 描述行和 malformed 行。
2. 按窗口过滤事件：
   - `type ∈ {memory_promoted, memory_decayed, user_correction, memory_migrated}`
   - `rollback_applied` 仅在 `target` 或 `source` 指向 `.claude/agent-memory/` 或 `.claude/workframe-state/memory-index.json` 时纳入。
3. Read `.claude/workframe-state/memory-index.json` 拿当前 sidecar 元数据；若文件不存在，按空索引 `{entries:{}}` 处理，并在输出中提示 `memory-index.json 不存在，仅展示事件快照，当前状态元数据（protected / provenance）不可用。`。
4. 展示时优先使用事件自带快照字段：`summary` / `entry_key` / `source` / `age_days` / `provenance`（events 是只追加历史层，早期事件可能带旧的数字型 `confidence` 快照字段——按原样展示，不换算、不回写）。sidecar 只用于补充当前仍存在条目的 `protected` / `provenance` 等当前状态，不作为历史事实唯一来源。
5. 按时间倒序 + scope 分组输出。`scope` 优先取事件字段；memory rollback 可从路径 `.claude/agent-memory/<scope>/...` 推断，无法推断时归入 `unknown/`。

## 输出格式

```markdown
## 📔 记忆层活动（近 <N> 天）

### shared/
- 2026-04-20 [promoted] "研发任务签发仅由 qa 执行" (entry=shared:2026-04-20:研发任务签发仅由qa执行, source=notes.md, protected=true)

### pm/
- 2026-04-22 [decayed] "旧的 baseline 路径" (entry=pm:2026-02-16:旧的baseline路径, age_days=65, provenance=external)
- 2026-04-18 [promoted] "自迭代走 proposal 闭环" (entry=pm:2026-04-18:自迭代走proposal闭环)

### dev/
- 2026-04-21 [migrated] "构建脚本必须在仓库根执行" (entry=dev:2026-04-21:构建脚本必须在仓库根执行, source=auto-memory, from_ref=build-script-root-only.md)
- 2026-04-19 [correction] "不要 mock 数据库" (entry=dev:2026-04-19:不要mock数据库, protected=true)
- 2026-04-17 [rolled-back] dev/MEMORY.md → 回滚至 logs/librarian-snapshots/2026-04-15/10-30-dev-MEMORY.md
```

示例的三条纪律，照抄即合规：**组内按日期倒序**（与步骤 5 一致）；**所有条目落在同一窗口内**（上例跨 04-17~04-22，任何 ≥7d 的窗口都自洽——`decayed` 那条的 `entry` 日期是条目**创建日**，可以早于窗口，`age_days` 由它与事件日之差算得）；**示例值一律虚构通用**，不写任何真实项目的条目内容或文件名。

`[migrated]` 与 `[promoted]` 展示上要能区分：前者是**跨 scope 搬迁**（`source` 指向另一个记忆域），后者是**同域内 notes 提升**（`source` 是 `notes.md`）。搬迁事件带 `from_ref`，展示时一并给出，否则读者无法回答「它原来在哪」。
`[rolled-back]` 没有 `entry`——回滚的对象是整个文件而非单条，故格式为 `<文件> → 回滚至 <快照路径>`。

## 约束

- 只读
- `disable-model-invocation: true`
- 按 scope 过滤时，若无匹配输出 "No memory activity in scope=<scope> window."
- 若窗口内无上述事件，输出 "No memory activity in the last <N> days. If Librarian ran recently, check whether it emitted memory_promoted / memory_migrated / memory_decayed events."
