---
name: task-management
description: '管理项目看板 board.yaml 的任务创建、状态流转与签发。用于 board.yaml 任务创建 / 状态变更 / 流转规则查阅 / 签注（`qa_signoff`）写法 / schema 约束确认。典型触发："看板 X" / "任务状态改 Y" / "怎么流转研发任务到 pending_qa" / "签发权限" / "签注怎么写"。不用于：summary 数字的日常维护（SessionEnd hook 自动重算，本 skill 只给手工兜底的调用方式）/ 节奏复盘与周报（暂无对应 skill，由主 Claude 自由发挥）。'
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
    assigned_to: dev         # pm | dev | qa | prompt-eng | <project-role>
    created_at: "YYYY-MM-DD"
    updated_at: "YYYY-MM-DD"
    deadline: null           # "YYYY-MM-DD" 可选；有截止要求才填。会话启动时据此提醒逾期
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
    # ── 生命周期字段：到那个状态才出现，出现后按下文 §生命周期字段 填 ──
    # completed_at: "YYYY-MM-DD"    # status 改为 completed 时必填
    # actual_output: ""              # completed 时建议填，记录实际产出
    # blocked_reason: ""             # status 改为 blocked 时必填
    # cancelled_at: "YYYY-MM-DD"     # status 改为 cancelled 时必填
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
  - 信号入账的 P0「需求变更」场景捕获到上线时间
  - 迭代计划任务（QBR / 发布窗口）
- 何时不填：常规开发任务无刚性 deadline
- 会话启动（`session-start-prep.py`，休眠 / 唤醒会话不提）使用：仅当 `deadline < today` 且 `status ∉ {completed, cancelled}` 时计为逾期；每个逾期任务只列一次，一次最多列 5 个，没列到的下次接着列

### 生命周期字段（到那个状态才填）

**任务没走到那个状态就不该有这个字段**；走到了，下表的「必填」是**状态触发后的硬要求**。
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
| `auto-update` | 信号入账直接追加的任务 | 仅 P0 安全 / 线上故障类信号，经用户确认后追加的单条 `pending` 任务；其余信号产生的任务走本 skill 或由主会话落盘 |
| `needs-qa-regression` | 需要 @qa 执行回归验证 | 安全类修复完成后 |
| `prompt-review` | 需要 @prompt-eng 介入评估 | 需求变更涉及 Prompt 质量时 |
| `security` | 安全相关任务 | SEC issue 关联任务 |
| `main-executed` | 主 Claude 实际完成了使任务进入 `pending_qa` 的研发交付 | 主 Claude 直做研发任务、代行 `in_progress → pending_qa` 时 |

> tags 用于协同角色标注：当 `assigned_to` 为单值时，通过 tags 标注需要协同的其他角色。例如 `assigned_to: dev` + `tags: [needs-qa-regression]` 表示 dev 完成后需 qa 回归验证。
>
> `main-executed` 是**执行事实**标记，不是角色标记——判据为「谁完成了使任务进入 `pending_qa` 的那份研发交付」：主 Claude 实际做完才打，**只是参与讨论 / 出方案不打**。`assigned_to` 始终保持域角色不变，便于按域统计。

## 任务状态定义

| 状态 | 含义 | 流转规则 |
|------|------|---------|
| `pending` | 待开始 | 初始状态 |
| `in_progress` | 进行中 | 从 pending 流转，依赖项须全部 completed |
| `pending_qa` | 待 QA 验证 | 从 in_progress 流转，仅研发类任务经过此状态 |
| `completed` | 已完成 | 研发任务：从 pending_qa 流转（**baseline 角色里仅 @qa 可操作**；主 Claude 签发见 §签发权限 该行）；非研发任务：从 in_progress 直接流转 |
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

**项目自定义角色**（`assigned_to: <project-role>`）：按 `role-customization-guide.md` §任务状态流转约定的三分类归类——「产出修改线上内容」类（content-operator / designer 等）视同研发类走 pending_qa，其 `in_progress → pending_qa` 由该角色自己操作、签发仍仅 @qa（**自定义角色一律不获得签发权**，§谁签发这次收口 的自签通道只对主 Claude 开）；「分析/咨询」「协调」类视同非研发类直接流转。项目在角色定义的 Step 3 段写明归类，不明确时按研发类处理（fail-safe：多过一道 QA 优于漏签发）。

### 签发块 schema（`qa_signoff` / `verified_ranges`）

研发任务转 `completed` 时写 `qa_signoff`，**一律映射形态**——主会话签、@qa 签都一样。
签发分哪一类（主会话自理 / 需要独立验证）、否决项怎么交用户，判据在必载片 §谁签发这次收口，
本节只管字段：

```yaml
- id: TASK-042
  status: completed
  completed_at: "2026-11-05"
  qa_signoff:
    mode: 自理                    # 自理 / 独立验证 / 你拍板
    since: "a1b2c3d4"             # 切片起点：已提交的 commit hash（单仓）
    diff_ref: "e5f6a7b8"          # 切片终点：已提交的 commit hash
    # 多仓按仓给，键是仓相对项目根的路径，根仓写 "."：
    #   since: {".": "a1b2c3d4", "vendor/lib": "c9d0e1f2"}
    #   diff_ref: {".": "e5f6a7b8", "vendor/lib": "a3b4c5d6"}
    verifier:                     # mode 为「独立验证」时必填
      who: "@qa"                  # 新 @qa / 另一个模型 / 外部 agent
      report: "报告路径或会话 id"
    approval: "…"                 # mode 为「你拍板」时必填：用户批准的原话或时间与范围
    ruling: "…"                   # 可选：重算出待裁决项而不交用户时，逐条写裁决
    verified: |                   # 验了什么，逐条
      ...
    not_verified: |               # 没验什么 ＋ 为什么，逐条
      ...
  verified_ranges: |              # 本次签发覆盖到哪儿（**必填**，与 qa_signoff 同级）
    ...
```

**`signoff-guard.py` hook 查什么**（缺一即拒，红灯会点名是哪一项）：

- **切片两端只收十六进制 commit**，签发前先提交——hook 按 `since..diff_ref` **自己重算否决项**，
  不读你写的结论。符号引用（`HEAD`、分支名）会随提交移动，钉不住签发时判的是哪一段。
  **没声明的仓不在切片里**：改了两个仓就两个都写——漏写一个仓，hook 发现不了（它只重算你声明的那几段）。
- **命中否决项**而 `mode` 不是「你拍板」即拒；重算出**待裁决项**（如触及受保护资产的路径）时，
  要么交用户拍板，要么在 `ruling` 里逐条写出裁决——拿不准一律算命中。
- **独立验证必须写 `verifier`**——hook 只查有没有写，不判写得真不真。
- **类别判得对不对，hook 不判**：把该独立验证的写成「自理」，没有任何机器面会说话（判据在必载片 §谁签发这次收口）。
- **`verified_ranges`** 是下一轮增量复核的锚点。不写它，下一轮只能退回全量复核或凭印象划范围。
- 通过时 hook 不写事件，也不用手写。

**查哪些、不查哪些**：`completed_at` 不早于 2026-11-01、且带签注或打了 `main-executed` /
`needs-qa-regression` 的已完成任务。之前完成的不追溯；被拦的只限**本次新出现或改过**的签注——原样未动的
存量问题只报不拦。关掉自签开关之后，「你拍板」也要写 `verifier`（拍板不免除独立验证）。

> **自理默认可用**：`.workframe-config.json` 的 `signoff.self_signoff_enabled` **缺键即开启**
> （默认 true）。显式写 `false` ⇒ 研发任务一律按「独立验证」签发，写「自理」hook 会拒，
> 红灯说明要动的是配置而不是那一行类别。
>
> **默认开启是一个被知情接受的取舍**：它让能力对多数项目真正可用，代价是从默认关闭的
> 旧版升级上来的项目不做任何动作就获得了自理签发权，且 **Codex 门下本 hook 的拦截效果未经真实会话验证**
> （`apply_patch` 改看板时从补丁头认出看板、与 Edit 同样 exit 2，但 Codex 怎么处置这个 exit 2 没验过；
> 经 shell 改看板只报不拦）。收口检查（`module-close-check`）
> **不查签注格式**，不替这道 hook 兜底。要求更严的项目请显式关闭。

### 签发权限

| 状态变更 | 允许操作者 |
|---------|-----------|
| `pending → in_progress` | 该任务 `assigned_to` 的角色（认领即开工，不需要谁签发） |
| `in_progress → pending_qa` | @dev、@prompt-eng（研发任务必经）；走 pending_qa 类的项目自定义角色（归类见 §pending_qa 适用范围）同此；**主 Claude 直做时代行**——`assigned_to` 仍填对应角色（域语义不变），并打 `tags: [main-executed]` |
| `pending_qa → completed` | @qa（**baseline 角色里**唯一可签发研发任务完成的，含自定义角色的研发类任务）。**代行不含签发权**——主 Claude 直做研发任务不因此获得签发权。主 Claude **自签**按必载片 §谁签发这次收口 的两类签发（自理，或经独立验证之后由主 Claude 签），且 **自理默认开启**（`.workframe-config.json` 的 `signoff.self_signoff_enabled` **缺键即 true**）；要求更严的项目显式写 `false`，写了之后研发任务一律经独立验证才能签 |
| `pending_qa → blocked` | @qa（测试不通过时） |
| `in_progress → completed` | @pm、@dev（非研发任务）、@qa（非研发任务）、@prompt-eng（非研发类 Prompt 咨询）；分析/咨询/协调类的项目自定义角色同此 |
| `任意 → blocked` | 任何角色（遇阻即可标，必须同时写 `blocked_reason`）。非 QA 角色直接标 blocked 是被允许的路径——必载片 §Step 1 — 事件流 的 task_blocked fallback 正是为它准备的 |
| `blocked → in_progress` | 阻塞解除后由 `assigned_to` 角色自行恢复；QA 打回的任务由完成研发交付的角色（@dev / @prompt-eng / 走 pending_qa 类的自定义角色）修复后恢复，不需要 @qa 再签一次 |
| `任意 → cancelled` | @pm 或用户（范围决策，必须同时写 `cancelled_at` + `cancel_reason`） |

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

主 Claude 通过 Bash 调用插件内兜底命令（不重新实现统计公式；插件根取 `$WF_PLUGIN_ROOT`，即会话开场注入的「本会话插件根」，不依赖 PATH）：

```bash
python "$WF_PLUGIN_ROOT/bin/workframe-recompute-board-summary"
```

`$WF_PLUGIN_ROOT` 是会话开场注入的「本会话插件根」（正在执行的这扇门的那一份，正斜杠绝对路径），执行前换成那个路径。若环境里 `workframe-recompute-board-summary` 恰好已在 PATH（CC plugin `bin/` 注入生效的环境），裸调等价。

脚本返回 JSON 表明结果（`status: ok / skipped / error`），并在 `events.jsonl` 写一条 `summary_recomputed` 事件。

**强制终止 caveat**：如果 Claude Code 被强制终止（关闭窗口 / SIGKILL / OS shutdown），SessionEnd hook 不会触发——summary 会保持上次正常 flush 的值。

**自动兜底**：`session-start-prep.py` 在 SessionStart 时（休眠与等待唤醒的会话除外）执行 drift check（对比 summary vs tasks 实际计数），发现不一致自动调用 recompute 修复并写 `summary_drift_repaired` 事件。手工兜底主要用于"用户希望立即看到最新 summary 不等下次 session"或"drift check 因 board.yaml 异常而 skip"等场景。

**SessionEnd timeout caveat**（Claude Code；Codex 单条 hook 最多 3 秒、没有可调的变量）：SessionEnd hook 默认 1.5 秒整体 budget，**plugin-provided hooks 的 timeout 配置不会提升整体 budget**。如果项目 board.yaml 大到 recompute 超时（耗时随文件字节数增长，不随任务条数——任务正文写得长的看板，几百条就能到 MB 级），用户可设环境变量 `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS=5000`（或更高）。SessionEnd 超时后，由之后开会话时的 drift check 补上（条件见上一段）。

## 操作规范

1. **创建任务**：追加到 tasks 列表末尾，ID 递增，初始状态 pending。**modules/ 体系下**（`projects/modules/` 存在）必须填 `module` 二段式 `<basic>/<sub>`；如对应子模块未建，先调 `module-init` 再回写 `module` 字段，不要凭空写不存在的模块路径
2. **更新状态**：修改 status 字段 + updated_at，遵循流转规则
3. **批量操作**：一次可修改多个任务，但每个都须更新 updated_at
4. **summary 重算**：由 SessionEnd hook 自动执行（调用 `recompute_board_summary.py`）；主 Claude 可在用户要求或发现异常时手工兜底调用该脚本。**各角色只更新任务条目，不修改 summary 段**
5. **任务拆分**：单个任务预估工时不超过 4 小时，超出须拆分
