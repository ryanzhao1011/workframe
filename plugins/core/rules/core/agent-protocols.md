---
name: agent-protocols
created_at: 2026-04-24
scope: project
action: agent-protocol
---

# Agent 通用协议（全局生效）

> 定义所有 core agent 共享的**启动 / 协作 / 收尾**协议。角色身份、职责、特有写入边界与状态流转由各 agent 文件自身定义。
>
> **适用对象含主 Claude**：它直做角色工作时（不派 subagent，自己完成需求分析 / 代码变更 / 测试 / Prompt 优化等），同样受本协议约束。别因为标题写着「Agent」、§1 写着「被调度时」就认为整份不适用——**收尾协议尤其适用**，否则直做的工作不进事件流、经验不落盘、看板不更新。
>
> **总纲（主 Claude 直做时如何读本文件）**：凡描述 **subagent 运行环境**的条款（记忆由 hook 自动注入、无法派发其他角色等），主 Claude 走其兜底路径或按自身能力执行；**工程纪律／产物合同／状态流转照常适用**，不因执行者是主 Claude 而豁免。
>
> 本协议是运行时指令约束，非硬执行——需要强保证的状态更新由 Python hook 或显式工具完成，不假设 agent 一定按协议写。

## 1. 启动协议（每次被调度时首先执行）

**必读记忆（两份）由 SubagentStart hook 自动注入**（`subagent-memory-inject.py`，代码保证）：

1. `.claude/agent-memory/shared/MEMORY.md`（跨角色权威事实；所有 agent 都注入）
2. `.claude/agent-memory/<role>/MEMORY.md`（本角色高置信记忆；按 agent 名映射角色目录注入）

上下文中已有「[workframe] 角色记忆注入」标记段 → 记忆已送达，**不要再显式 Read 这两份文件**；无该标记段（hook 未生效，如 Python 缺失）→ 按上列清单显式 Read 兜底，文件不存在则跳过。

**主 Claude 直做时**：SubagentStart 不触发，**永远没有**那个标记段，因此恒走显式 Read 兜底——读 `shared/MEMORY.md` + **所直做角色域**的 `<role>/MEMORY.md`（做哪个域的活就读哪个域；跨域任务读涉及的每个域）。这不是可选项：不读就等于拿不到该域已沉淀的踩坑与用户偏好，直做质量必然低于委派。

角色记忆是 workframe 应用层机制，**不使用** CC 官方 agent `memory` frontmatter（目录布局与维护指令同本框架协议不兼容）。

**冲突处理优先级**（应用层权威顺序）：

```
shared/MEMORY.md            # 跨角色权威事实
  > <role>/MEMORY.md        # 本角色高置信记忆
  > <role>/notes.md         # 缓冲区，未达 D/U/R/A
```

当 shared 与 role 内容不一致时，以 shared 为准；shared 与 role 都未记录的话题沿用常识。

## 2. 协作边界

**核心原则**：subagent 内的所有动作都由 agent 自己完成；需要其他角色继续介入时，**通过响应文字明确标注 + 看板 status + Issue / board tag** 表达，由主 Claude / 用户 / task-management 状态机继续调度。

**反模式**：写"通知 @qa 测试""让 @dev 修复"这类派发式祈使句——它暗示 agent 能直接调度他人，实际不能。改为陈述需求（"需要 @qa 介入验证"）并落到 `status` / `assigned_to` / board tag 上，由状态机接手。

## 3. 收尾协议（每次响应结束前执行）

### Step 0 — 判断是否需要收尾

| 工作类型 | 是否走 Step 1-3 |
|---|---|
| 仅回答咨询问题（查状态、问定义、技术咨询等） | 跳过 |
| 仅执行系统维护操作（更新 board.yaml status / 追加 events.jsonl / 追加记忆条目） | 直接执行，不走 Step 1-3 |
| 执行了实质性工作（需求分析 / 代码变更 / 测试 / Prompt 优化 / 评估 / 审查等） | 走 Step 1-3 |

> 响应正文完整性（正文 > 文件写入）由 `response-output.md` 保证，本文件不重复。

### Step 1 — 事件流

**事件流**（驱动 `recompute_skill_metrics.py` 重算 `skill-metrics.yaml` 和 self-iteration 决策）：

→ 为每个实际使用的 skill 向 `.claude/workframe-state/events.jsonl` append 一行 JSON：

```json
{"ts":"<ISO-8601>","type":"skill_used","skill":"<skill-name>","role":"<role>","success":true,"session_id":"<$CLAUDE_CODE_SESSION_ID 若可用，否则省略此字段>"}
```

→ **`<ISO-8601>` 的唯一合法形态：UTC + 秒级**，例 `2026-08-16T07:24:04+00:00`。全框架事件
（含本文与各 skill 里所有写 `<ISO-8601>` 的地方）都按这一条。取值：

```bash
python -c "from datetime import datetime,timezone;print(datetime.now(timezone.utc).isoformat(timespec='seconds'))"
```

**别写本地时区（`+08:00`）、别带微秒。** events.jsonl 是多方共写的单一文件，消费方按窗口
过滤与排序；混入本地时区的事件会被算错窗口——实测一条真实早于 30 天窗口 1 分钟、却写成
`+08:00` 的事件，被 doctor 判成「窗口内」。脚本 producer 已全部统一，模型手写的这部分靠本条。

→ **role 取值**：subagent 填自己的角色名（`pm` / `dev` / `qa` / `prompt-eng` 或项目自定义角色）；**主 Claude 直做时填 `main`**。本 Step 对主 Claude 同样生效——直做的实质性工作若不记账，skill 使用率与问题信号就只反映委派出去的那部分，self-iteration 的判断依据随之失真。

→ **success 取值**：产出了承诺的交付物填 `true`；未产出（内部错误 / 工具失败 / 信息不足放弃 / 阻塞退出）或用户当场否定产出填 `false`。

> 该字段是自评，**不作质量信号**（不驱动自动触发）；但 `recompute_skill_metrics.py` 与 `/core:audit` 仍消费，勿删字段。真实失败信号看 `turn_failed` 与 `user_correction`。单纯咨询 / 维护操作按 Step 0 跳过，不写事件。

→ **session_id 写入**：若运行环境有 `$CLAUDE_CODE_SESSION_ID` 环境变量（CC 2.1.132 起注入 Bash 子进程，与 hooks stdin 的 `session_id` 同值），写入；不可用时省略此字段（`check-iteration-trigger.py` 会回退到按事件时间戳分桶，但 per-session cap 会失效）。注意旧名 `$CLAUDE_SESSION_ID` 从来不是 Bash 环境变量（那是 SKILL.md 正文的字符串替换语法），不要使用。

→ **反斜杠必须转义**：事件字段值含 `\`（正则 `\b` `\s`、Windows 路径 `C:\x` 等）时，写进 JSON 必须写成 `\\`。JSON 只认 `\" \\ \/ \b \f \n \r \t \uXXXX` 这几种转义，`\s` `\w` 等会让整行解析失败，污染 events.jsonl——严格解析的消费方会在该行抛异常。手拼 JSON 字符串有风险时，改用 `python -c "import json;print(json.dumps({...}, ensure_ascii=False))"` 生成后再追加。

→ 无 skill 使用时跳过事件写入

→ 用户纠正事件由 `correction-detection.md` 在写入 `[纠正]` MEMORY 条目时立即 append `user_correction`；本 wrap-up 步骤不要为同一纠正再写一条 `user_correction`。

→ **task_blocked 事件 producer 策略**（防止重复计数）：
- **主 producer = `test-case-design` skill**（QA 测试失败创建 Issue → 任务状态改 blocked → skill 内部 append 一条 `task_blocked`）。走 test-case-design 的场景，wrap-up **不要重复 append**（避免 problem 加权分 2.0 双倍计入）
- **fallback producer = 当前角色的 wrap-up**，仅限以下场景：
  - 非 QA 角色直接把任务状态标为 blocked（未走 test-case-design skill）
  - 用户/主 Claude 手动改任务为 blocked
- **fallback dedup 检查（强制）**：append 之前必须扫描 `.claude/workframe-state/events.jsonl`，确认尚无相同 `task_id` 的 `task_blocked` 事件；若已存在则跳过（task_blocked schema 不含 session_id，dedup 仅以 `task_id` 为准；详见下文 §同消息内 rule 处理顺序 / task_blocked fallback dedup 的可执行检查）
- 事件格式：
  ```json
  {"ts":"<ISO-8601>","type":"task_blocked","task_id":"<TASK-ID>","role":"<role>"}
  ```

### Step 2 — 经验沉淀（先分归属，再走 D/U/R/A 准入）

**归属判据 = 谁消费**：查这个模块的人要用（口径 / 规格 / 结论）→ 文档；本角色跨需求执行时要用（工作方式 / 踩坑 / 用户偏好）→ 记忆。权威文档能承载的内容不复制进记忆——**指针优先**（完整落点分层见 `librarian/SKILL.md` §落点分层决策）。

> **执行者 ≠ 消费者**：落点由「**未来谁要读它**」决定，不由「这次谁做的」决定。subagent 干活时两者恰好重合，直接写自己域即可；**主 Claude 直做时不重合**——它可能在做 dev 域的活，经验却该落 dev 域给未来的 @dev 读。判断时先问「未来在什么场景、谁会需要这条」，再定域；别因为是自己做的就往 auto-memory 塞。

| 产出类型 | 去向 |
|---|---|
| 业务知识（需求口径 / 方案结论 / 领域事实 / 接口约定） | 对应 `projects/modules/` 文档：shared 事实源 / PRD「变更与决策记录」/ `<sub>/decisions/` ADR（归属查 skill: `document-norms` §1）；记忆**至多留一行指针** |
| 工种知识，未来场景可点名**恰 1 个角色域** | 该角色 `.claude/agent-memory/<role>/notes.md`（缓冲区）；满足 D/U/R/A ≥2 **且**来源为亲测／用户确认 → 直写 `<role>/MEMORY.md`（总量 ≤8000 字符） |
| 工种知识，未来场景可点名 **≥2 个角色域** | `.claude/agent-memory/shared/notes.md`。判据 = **该角色未来在什么决策／操作时要读它**——"两个角色都会用这个工具"这类工具重叠**不构成证据**。与单角色域不同，**shared 不许直写 MEMORY**：由 librarian 按「≥2 消费域 + 单条压缩 ≤200 字符」评估提升到 `shared/MEMORY.md`（总量 ≤4000 字符） |
| 主 Claude 协作行为 / 用户偏好 / 项目状态指针（**仅主 Claude 可写**） | CC 官方 auto-memory。subagent 无此落点，遇到这类产出改为在响应中说明，由主 Claude 决定是否记 |
| 拿不准 | **scope 最小化**：先落最可能的那**一个**角色域，并反问「是不是只有该域会读」——答不出即列为 shared 候选，等**第二次真被另一个域用到**再上提（promote-on-second-use），不预先上提 |
| 无特别收获 / 当轮即弃 | 跳过此步 |

> **notes.md 的格式是硬要求**：条目必须是 `### ` 章节头或 `- YYYY-MM-DD:` 列表项两种形态之一。写成别的形态，memory-ask 的积压计数扫不到它——**内容还在，但从此没人会来评估提升**，等同于写进了黑洞。

> **目录/文件不存在的兜底**：写入前若 `.claude/agent-memory/<role>/` 目录或 `MEMORY.md` / `notes.md` 不存在，先创建空骨架（H1 + 一行说明），再写入本次内容。这是为项目级新增 role（ceo / designer / content-operator 等手工创建场景）兜底；4 个 baseline core role（pm/dev/qa/prompt-eng）已由 scaffold 的 `ensure_project_scaffold` 预创建，正常情况下不会触发兜底路径。

**D/U/R/A 准入标准**：

- **D**urability：30 天后还重要？
- **U**niqueness：尚未记录过？
- **R**etrievable：未来需要回忆？
- **A**uthority：来源可靠（亲测 / 用户确认）？

用户纠正信号走 `correction-detection.md`（按其第 3 步分流落点、跳过 D/U/R/A），本步骤只处理常规沉淀。

### Step 3 — 更新任务看板

**通用规则**（所有 agent 共享）：

- 当前工作对应 `projects/board.yaml` 中的已有任务 → 直接更新该任务条目 `status` 和 `updated_at`
- 不在看板中的临时工作 → 跳过此步
- **不修改 `summary:` 段**——由 `SessionEnd` hook（`session-end-flush.py`）自动重算；用户明确要求或发现统计异常时，由主 Claude 调用 `workframe-recompute-board-summary` 命令手工兜底重算

**角色特有的状态流转规则**（如 "dev 研发任务从 in_progress 只能流转到 pending_qa，不得直接 completed"、"qa 可签发 pending_qa → completed"、"pm 非研发任务可直接 completed" 等）由各 agent 文件自身 Step 3 段落定义。本文件只管通用骨架，不管角色差异。

## 同消息内 rule 处理顺序

> 当一条用户消息同时触发多条 core rule 时按以下顺序处理。**前后两层职责不同**：步 1-2 是消息事实处理（写入 MEMORY / events 关于"用户说了什么"）；步 5 是本轮工作收尾（写入 events 关于"我做了什么"）。两层不混用 dedup 逻辑。
>
> **主 Claude 直做时格外容易双写**：委派场景下步 1-2 由主 Claude 执行、步 5 由 subagent 执行，两层天然隔离在不同 context；**直做时五步全在同一实例、同一轮内完成**，"刚刚已经写过一条"很容易再写一遍。落笔前按上面那句分清这条事件属于哪一层——记的是**用户说了什么**（步 1-2）还是**我做了什么**（步 5）。同一事实同时满足两层时**只记一次**，按更早的那层记。

| # | 阶段 | 写入 | 关键约束 |
|---|---|---|---|
| 1 | correction-detection (rule) | `[纠正]` MEMORY 高置信区 + memory-index sidecar + `user_correction` event | 命中信号词立即处理；处理过的事实在本轮内由 model 内部 tracking 跳过 auto-update（**无物理 marker**，仅靠模型在 step 2 自检）|
| 2 | auto-update (rule) | board.yaml / issues / notes / MEMORY（按 P0/P1 路径）| 仅处理 step 1 **未消费**的事实/指令；触及 MEMORY/event 写入前先自检"该事实是否在 step 1 已写" |
| 3 | agent 主任务执行 (agent + skill) | 业务产出 + skill body 内的 events | skill body 内 append 的 events 属于对应 skill 的 producer policy，不属于 agent wrap-up。若本轮已走 test-case-design 失败路径并由该 skill append `task_blocked`，wrap-up 不得再补写 |
| 4 | response-output (rule) | — | 完整内容先输出；改写结构 / 否定方向的写入需二次确认；系统维护操作（状态更新 / events.jsonl 追加）直接执行 |
| 5 | agent wrap-up Step 1 (agent) | `skill_used` + `task_blocked` fallback | fallback 仅当：(a) 本轮未走 test-case-design 路径 + (b) events.jsonl 中无同 `task_id` 的 `task_blocked` 时 append |

### task_blocked fallback dedup 的可执行检查

wrap-up 准备 append `task_blocked` 前**必须**先扫 `.claude/workframe-state/events.jsonl`，确认不存在同一 `task_id` 的 `task_blocked`（判定以「同一行同时命中 `type` 与 `task_id`」为准）：

- 文件不存在 → 视为无命中
- 已命中 → 跳过 append
- 无命中 → append `{"ts":"<ISO-8601>","type":"task_blocked","task_id":"<TASK-ID>","role":"<role>"}`

dedup 只以 `task_id` 为准——`task_blocked` schema 不含 session_id 字段。
