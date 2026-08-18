---
name: task-management
description: 管理项目看板 board.yaml 的任务创建、状态流转与进度统计
when_to_use: |
  用于 board.yaml 任务创建 / 状态变更 / 流转规则查阅 / schema 约束确认时调用。
  典型触发："看板 X" / "任务状态改 Y" / "怎么流转研发任务到 pending_qa" / "签发权限"。
  不用于：summary 数字重算（由 SessionEnd hook 自动 + 用户显式 `workframe-recompute-board-summary` 命令）/ 节奏复盘与周报（暂无对应 skill，由主 Claude 自由发挥）。
user-invocable: false
allowed-tools: [Read, Write, Edit, Glob, Grep]
---

# 任务管理技能

## board.yaml 格式规范

```yaml
# projects/board.yaml
summary:
  total: 0
  pending: 0
  in_progress: 0
  pending_qa: 0
  completed: 0
  blocked: 0
  cancelled: 0
  last_updated: "YYYY-MM-DD"

tasks:
  - id: "TASK-001"
    title: "任务标题"
    description: "任务描述"
    status: pending          # pending | in_progress | pending_qa | completed | blocked | cancelled
    priority: P1             # P0 | P1 | P2
    assigned_to: dev         # pm | dev | qa | prompt-eng
    created_at: "YYYY-MM-DD"
    updated_at: "YYYY-MM-DD"
    deadline: null           # "YYYY-MM-DD" 可选；有截止要求才填。heartbeat-check.py 会扫此字段判逾期
    depends_on: []           # 依赖的任务 ID 列表
    tags: []                 # 标签（合法值见下方约定）
    estimate_hours: 4        # 预估工时（小时）
    notes: ""                # 备注
    # ── modules/ 体系叠加字段 ──
    # module: profile/edit             # ★ 二段式 basic/sub；modules/ 体系下必填
    # req_slug: avatar-cropper          # 可选；需求级任务用，对应 modules/<basic>/<sub>/requirements/<req_slug>/
    # sub_req_slug: main                # 与 req_slug 同进同出；main 也要显式写
    #                                   # （缺省按 main 解释仅对**存量**条目成立）
    # affected_modules: []              # 可选；横切多模块任务用二段式数组
    # ── 可选生命周期字段（按事件触发写入）─────────────────────
    # completed_at: "YYYY-MM-DD"    # status 改为 completed 时填写
    # actual_output: ""              # completed 时补充实际产出描述
    # blocked_reason: ""             # status 改为 blocked 时必填
    # cancelled_at: "YYYY-MM-DD"     # status 改为 cancelled 时填写
    # cancel_reason: ""              # cancelled 时必填
```

### modules/ 体系字段

modules/ 体系下，task 字段叠加 modules 归属：

| 字段 | 取值 | 必填条件 |
|---|---|---|
| `module` | 二段式 `<basic>/<sub>`，如 `profile/edit` | modules/ 体系下**必填**（与 issue 字段策略一致）|
| `req_slug` | 父需求 slug，如 `avatar-cropper` | **需求级任务必填**（document-norms §2.6 引用契约）；维护 / 看板 / 记忆整理这类不挂需求的任务留空 |
| `sub_req_slug` | 子需求 slug，如 `main` 或 `phase-1` | **与 `req_slug` 同进同出**——填了 req_slug 就必须填它（`main` 也显式写）。只有**存量**条目缺它时才按 `main` 解释，那是兼容条款不是写法（`document-norms` §2.6）|
| `affected_modules` | 二段式数组 `[a/b, c/d]` | 横切多模块任务可选 |

未启用 modules/ 的存量项目：上述字段全部可选；`module` 可单值或留空（兼容现有结构）。

**框架维护类任务例外**：无业务模块归属的任务（如 self-iteration 的 `iteration-tracking` 提案追踪、框架升级跟进等）可不填 `module`——「必填」针对的是业务任务。

> **创建/状态变更任务时（modules/ 体系下）**：必须确认 `module` 二段式合规（grep `projects/modules/<basic>/<sub>/` 是否存在）；不合规建议先调 `module-init` 创建对应子模块再回写 `module` 字段。
> 字段叠加策略与 `projects/issues/TEMPLATES.md` 一致；详见 skill: `document-norms` §1。

### deadline 字段约定

- 类型：ISO 日期字符串 `"YYYY-MM-DD"` 或 `null`
- 何时填：
  - 用户显式提出截止时间（"下周五要上线"、"月底前完成"等）
  - auto-update.md 的 P0「需求变更」场景捕获到上线时间
  - 迭代计划任务（QBR / 发布窗口）
- 何时不填：常规开发任务无刚性 deadline
- heartbeat-check.py 使用：仅当 `deadline < today` 且 `status ∉ {completed, cancelled}` 时计为逾期

### 生命周期字段（到那个状态才填）

「可选」指的是**任务没走到那个状态就不该有这个字段**，不是「到了也可以不填」——下表的
"必填"是**状态触发后的硬要求**（原标题写「可选」与表内「必填」互相打架）。
流转到对应状态的角色负责当场填，不留给下一个人补。

| 字段 | 填写时机 | 状态触发后 |
|------|---------|---------|
| `completed_at` | status 流转到 `completed` 时 | 必填 |
| `actual_output` | status 流转到 `completed` 时 | 建议填（区别于 notes，记录实际产出） |
| `blocked_reason` | status 流转到 `blocked` 时 | 必填 |
| `cancelled_at` | status 流转到 `cancelled` 时 | 必填 |
| `cancel_reason` | status 流转到 `cancelled` 时 | 必填 |

> `notes` 是自由备注，`actual_output` 是结构化交付物描述（如"已交付 specs/REQ-003.md + 3 个 Task 条目"），两者互补不重复。

### tags 约定值

| tag | 含义 | 使用场景 |
|-----|------|---------|
| `auto-update` | 由 auto-update 规则自动创建的任务 | P0/P1 信号触发时自动追加 |
| `needs-qa-regression` | 需要 @qa 执行回归验证 | 安全类修复完成后 |
| `prompt-review` | 需要 @prompt-eng 介入评估 | 需求变更涉及 Prompt 质量时 |
| `security` | 安全相关任务 | SEC issue 关联任务 |
| `main-executed` | 主 Claude 实际完成了使任务进入 `pending_qa` 的研发交付 | 主 Claude 直做研发任务、代行 `in_progress → pending_qa` 时 |
| `P0` / `P1` / `P2` | 优先级标签 | 与 issue severity 对应 |

> tags 用于协同角色标注：当 `assigned_to` 为单值时，通过 tags 标注需要协同的其他角色。例如 `assigned_to: dev` + `tags: [needs-qa-regression]` 表示 dev 完成后需 qa 回归验证。
>
> `main-executed` 是**执行事实**标记，不是角色标记——判据为「谁完成了使任务进入 `pending_qa` 的那份研发交付」：主 Claude 实际做完才打，**只是参与讨论 / 出方案不打**。`assigned_to` 始终保持域角色不变，便于按域统计。

## 任务状态定义

| 状态 | 含义 | 流转规则 |
|------|------|---------|
| `pending` | 待开始 | 初始状态 |
| `in_progress` | 进行中 | 从 pending 流转，依赖项须全部 completed |
| `pending_qa` | 待 QA 验证 | 从 in_progress 流转，仅研发类任务经过此状态 |
| `completed` | 已完成 | 研发任务：从 pending_qa 流转（仅 @qa 可操作）；非研发任务：从 in_progress 直接流转 |
| `blocked` | 被阻塞 | 从任意状态流转，须注明阻塞原因（QA 不通过时也使用此状态） |
| `cancelled` | 已取消 | 从任意状态流转，须注明取消原因 |

### pending_qa 适用范围

**需经 pending_qa 的任务（研发类）**：
- `assigned_to` 为 `dev` 或 `prompt-eng` 的编码/配置/变更类任务
- tags 包含 `needs-qa-regression` 的任务（安全修复回归验证等）

**不需经 pending_qa 的任务（非研发类）**：
- PM 分析任务（需求分析、竞品调研、用户反馈分析等）
- 系统维护任务（看板维护、记忆整理、自迭代等）
- 纯技术咨询、方案评估（@dev 的咨询类交付物）
- 纯文档任务

### 签发权限

| 状态变更 | 允许操作者 |
|---------|-----------|
| `pending → in_progress` | 该任务 `assigned_to` 的角色（认领即开工，不需要谁签发） |
| `in_progress → pending_qa` | @dev、@prompt-eng（研发任务必经）；**主 Claude 直做时代行**——`assigned_to` 仍填对应角色（域语义不变），并打 `tags: [main-executed]` |
| `pending_qa → completed` | @qa（唯一可签发研发任务完成的角色）。**代行不含签发权，签发权仍仅 @qa**——主 Claude 直做研发任务后仍须实际调度 @qa，不得自签 |
| `pending_qa → blocked` | @qa（测试不通过时） |
| `in_progress → completed` | @pm、@dev（非研发任务）、@qa（非研发任务）、@prompt-eng（非研发类 Prompt 咨询） |
| `任意 → blocked` | 任何角色（遇阻即可标，必须同时写 `blocked_reason`）。非 QA 角色直接标 blocked 是被允许的路径——`agent-protocols.md` 的 task_blocked fallback 正是为它准备的 |
| `blocked → in_progress` | 阻塞解除后由 `assigned_to` 角色自行恢复；QA 打回的任务由 @dev / @prompt-eng 修复后恢复，不需要 @qa 再签一次 |
| `任意 → cancelled` | @pm 或用户（范围决策，必须同时写 `cancelled_at` + `cancel_reason`） |

> 上表补齐前只定义了 4 行，而状态定义表写着 blocked / cancelled
> 「从任意状态流转」——出边和执行主体都没有落点，dev 修完被打回的任务找不到恢复依据。

## summary 重算（由 SessionEnd hook 自动执行；主 Claude 手工兜底）

每次 Claude Code 会话结束时，`session-end-flush.py` hook 自动调用 `recompute_board_summary.py`，根据 tasks 列表重算 summary 块：

```
summary.total = count(all tasks)
summary.pending = count(tasks where status == "pending")
summary.in_progress = count(tasks where status == "in_progress")
summary.pending_qa = count(tasks where status == "pending_qa")
summary.completed = count(tasks where status == "completed")
summary.blocked = count(tasks where status == "blocked")
summary.cancelled = count(tasks where status == "cancelled")
summary.last_updated = 当前日期
```

**所有角色（@pm / @dev / @qa / @prompt-eng）更新 tasks 条目时不修改 summary 段**——hook 会在会话结束时自动同步，避免多角色并发写 summary 导致的不一致。

**手工兜底**：以下场景用户可要求立即重算（由主 Claude 执行）：

- 用户在会话中途需要看到最新 summary（未等到 SessionEnd）
- 发现 summary 数字与实际 tasks 不符（可能是 hook 跳过 / 被强制退出 / 用户手工改动 board.yaml 格式）

主 Claude 通过 Bash 调用插件内兜底命令（不重新实现统计公式；插件根路径从 `plugin-root.txt` 取，不依赖 PATH）：

```bash
python "$(cat .claude/workframe-state/plugin-root.txt)/bin/workframe-recompute-board-summary"
```

`plugin-root.txt` 由 SessionStart hook 每次会话刷新为当前插件根（正斜杠绝对路径，Git Bash / macOS 通用）。若环境里 `workframe-recompute-board-summary` 恰好已在 PATH（CC plugin `bin/` 注入生效的环境），裸调等价。

脚本返回 JSON 表明结果（`status: ok / skipped / error`），并在 `events.jsonl` 写一条 `summary_recomputed` 事件。

**强制终止 caveat**：如果 Claude Code 被强制终止（关闭窗口 / SIGKILL / OS shutdown），SessionEnd hook 不会触发——summary 会保持上次正常 flush 的值。

**自动兜底**：`session-start-prep.py` 在每次 SessionStart 时执行 drift check（对比 summary vs tasks 实际计数），发现不一致自动调用 recompute 修复并写 `summary_drift_repaired` 事件。手工兜底主要用于"用户希望立即看到最新 summary 不等下次 session"或"drift check 因 board.yaml 异常而 skip"等场景。

**SessionEnd timeout caveat**：SessionEnd hook 默认 1.5 秒整体 budget，**plugin-provided hooks 的 timeout 配置不会提升整体 budget**。如果项目 board.yaml 超大（>5000 tasks）导致 recompute 超时，用户可设环境变量 `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS=5000`（或更高）。SessionStart drift check 兜底机制保证即使 SessionEnd 超时，下次启动仍能修复。

## 操作规范

1. **创建任务**：追加到 tasks 列表末尾，ID 递增，初始状态 pending。**modules/ 体系下**（`projects/modules/` 存在）必须填 `module` 二段式 `<basic>/<sub>`；如对应子模块未建，先调 `module-init` 再回写 `module` 字段，不要凭空写不存在的模块路径
2. **更新状态**：修改 status 字段 + updated_at，遵循流转规则
3. **批量操作**：一次可修改多个任务，但每个都须更新 updated_at
4. **summary 重算**：由 SessionEnd hook 自动执行（调用 `recompute_board_summary.py`）；主 Claude 可在用户要求或发现异常时手工兜底调用该脚本。**各角色只更新任务条目，不修改 summary 段**
5. **任务拆分**：单个任务预估工时不超过 4 小时，超出须拆分
