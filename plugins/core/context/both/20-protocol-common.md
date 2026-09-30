---
supersedes:
  - "agent-protocols.md#1. 启动协议（每次被调度时首先执行）"
  - "agent-protocols.md#3. 收尾协议（每次响应结束前执行）"
  - "agent-protocols.md#Step 0 — 判断是否需要收尾"
  - "agent-protocols.md#Step 1 — 事件流"
  - "agent-protocols.md#Step 2 — 经验沉淀（先分归属，再走 D/U/R/A 准入）"
  - "agent-protocols.md#Step 3 — 更新任务看板"
  - "agent-protocols.md#task_blocked fallback dedup 的可执行检查"
---

# 通用协议

## 记忆的权威顺序

```
shared/MEMORY.md            # 跨角色权威事实
  > <role>/MEMORY.md        # 本角色高置信记忆
  > <role>/notes.md         # 缓冲区，未达 D/U/R/A
```

shared 与 role 不一致时以 shared 为准；两者都未记录的话题沿用常识。角色记忆是本框架的应用层机制，**不使用** Claude Code 官方 agent `memory` frontmatter（目录布局与维护指令同本框架协议不兼容）。

## 收尾：先判要不要走

| 工作类型 | 是否走下面三步 |
|---|---|
| 仅回答咨询问题（查状态、问定义、技术咨询等） | 跳过 |
| 仅执行系统维护操作（更新看板 status / 追加事件 / 追加记忆条目） | 直接执行，不走三步 |
| 执行了实质性工作（需求分析 / 代码变更 / 测试 / Prompt 优化 / 评估 / 审查等） | 走三步 |

## Step 1 — 事件流

为每个**实际使用**的 skill 向 `<state>/events.jsonl` append 一行 `skill_used`；任务转 blocked 时按下方 producer 策略 append `task_blocked`。事件流驱动 skill 指标重算与自迭代决策，不记账等于让那套判断只看得见一部分工作。

**机械写法一律用 `workframe-event`**（`bin/` 下的命令），它做掉四件手拼必翻车的事：ts 口径（**UTC + 秒级**，例 `2026-08-16T07:24:04+00:00`，别写本地时区、别带微秒）、反斜杠转义（`\b` `\s`、Windows 路径写进 JSON 必须写成 `\\`，否则**整行**解析失败、污染事件流）、`entry_key` 归一、`task_blocked` 去重。**它不判断该不该写**——那是本段的判据，仍要自己过一遍。

- **`role` 取值**：subagent 填自己的角色名（`pm` / `dev` / `qa` / `prompt-eng` 或项目自定义角色）；**主会话直做时填 `main`**。
- **`success` 取值**：产出了承诺的交付物填 `true`；未产出（内部错误 / 工具失败 / 信息不足放弃 / 阻塞退出）或用户当场否定产出填 `false`。该字段是自评，**不作质量信号**，但消费方仍在读，勿删。真实失败信号看 `turn_failed` 与 `user_correction`。
- 无 skill 使用时跳过事件写入。
- **`session_id`**：由 `workframe-event` 自己取（三级兜底），手写时不必也不该自拼。它只对 `skill_used` 有消费方（自迭代触发的 per-session cap）；取不到时该字段**省略**，消费方回退到按 ts 分桶、per-session cap 在那一门下失效。
- 用户纠正事件在写入 `[纠正]` 记忆条目时**当场**写一条 `user_correction`；收尾时不要为同一条纠正再补一条。

**`task_blocked` 的 producer 策略**（防重复计数）：主 producer = `test-case-design` skill（QA 测试失败创建 Issue → 任务转 blocked → skill 内部已 append）。走了它就**不要**在收尾再补。fallback producer = 当前角色的收尾，仅限「非 QA 角色直接把任务标 blocked」与「用户／主会话手动改 blocked」两种。**fallback 前必须先扫事件流确认没有同 `task_id` 的 `task_blocked`**（文件不存在视为无命中；schema 不含 session_id，dedup 只以 `task_id` 为准）——`workframe-event` 已内建这条检查。

## Step 2 — 经验沉淀（先分归属，再走准入）

**归属判据 = 谁消费**：查这个模块的人要用（口径 / 规格 / 结论）→ 文档；本角色跨需求执行时要用（工作方式 / 踩坑 / 用户偏好）→ 记忆。权威文档能承载的内容不复制进记忆——**指针优先**。

> **执行者 ≠ 消费者**：落点由「**未来谁要读它**」决定，不由「这次谁做的」决定。subagent 干活时两者恰好重合；**主会话直做时不重合**——它可能在做 dev 域的活，经验却该落 dev 域给未来的 @dev 读。判断时先问「未来在什么场景、谁会需要这条」，再定域。主会话恒为潜在消费者，**不参与落点计数**。

| 产出类型 | 去向 |
|---|---|
| 业务知识（需求口径 / 方案结论 / 领域事实 / 接口约定） | 对应模块文档（shared 事实源 / PRD「变更与决策记录」/ decisions ADR）；记忆**至多留一行指针** |
| 工种知识，未来场景可点名**恰 1 个角色域** | 该角色 `notes.md`（缓冲区）；D/U/R/A ≥2 **且**来源为亲测／用户确认 → 直写 `<role>/MEMORY.md`（≤8000 字符） |
| 工种知识，未来场景可点名 **≥2 个角色域** | `shared/notes.md`。判据 = **该角色未来在什么决策／操作时要读它**，「两个角色都会用这个工具」这类工具重叠**不构成证据**。**shared 不许直写 MEMORY**，由 `librarian` 评估提升（≤4000 字符） |
| 主会话协作行为 / 用户偏好 / 项目状态指针 | 主会话侧的官方 auto-memory。**subagent 无此落点**，遇到这类产出改为在响应中说明，由主会话决定是否记 |
| 拿不准 | **scope 最小化**：先落最可能的那**一个**角色域，并反问「是不是只有该域会读」——答不出即列为 shared 候选，等**第二次真被另一个域用到**再上提，不预先上提 |
| 无特别收获 / 当轮即弃 | 跳过此步 |

**D/U/R/A 准入**：**D**urability（30 天后还重要？）／**U**niqueness（尚未记录过？）／**R**etrievable（未来需要回忆？）／**A**uthority（来源可靠：亲测 / 用户确认？）。用户纠正跳过这四条，直接进高置信区。

> **`notes.md` 的格式是硬要求**：条目必须是 `### ` 章节头或 `- YYYY-MM-DD:` 列表项两种形态之一。写成别的形态，积压计数扫不到它——**内容还在，但从此没人会来评估提升**，等同于写进了黑洞。

**目录/文件不存在的兜底**：写入前若角色记忆目录或 `MEMORY.md` / `notes.md` 不存在，先创建空骨架（H1 + 一行说明）再写。这是为项目级新增角色兜底；出厂 4 角色（`pm` / `dev` / `qa` / `prompt-eng`）已由装机预创建。

## Step 3 — 更新任务看板

- 当前工作对应看板中的已有任务 → 直接更新该条目的 `status` 与 `updated_at`
- 不在看板中的临时工作 → 跳过
- **不修改 `summary:` 段**——由 `SessionEnd` hook 自动重算；发现统计异常时由主会话调 `workframe-recompute-board-summary` 手工兜底

谁能做哪一次状态变更（谁能把研发任务流转到 `pending_qa`、谁能签发 `pending_qa → completed` 等）见「任务状态流转与签发权限」一节，各角色契约里另有本角色的扩展；本段只管通用骨架。
