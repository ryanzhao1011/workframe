---
name: task-management
description: '管理项目看板 board.yaml 的任务创建、状态流转与进度统计。用于 board.yaml 任务创建 / 状态变更 / 流转规则查阅 / schema 约束确认。典型触发："看板 X" / "任务状态改 Y" / "怎么流转研发任务到 pending_qa" / "签发权限"。不用于：summary 数字重算（由 SessionEnd hook 自动 + 用户显式 `workframe-recompute-board-summary` 命令）/ 节奏复盘与周报（暂无对应 skill，由主 Claude 自由发挥）。'
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
  - 信号入账的 P0「需求变更」场景捕获到上线时间
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
| `completed` | 已完成 | 研发任务：从 pending_qa 流转（**baseline 角色里仅 @qa 可操作**；主 Claude 自签的准入见 §签发权限 该行）；非研发任务：从 in_progress 直接流转 |
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

**项目自定义角色**（`assigned_to: <project-role>`）：按 `role-customization-guide.md` §任务状态流转约定的三分类归类——「产出修改线上内容」类（content-operator / designer 等）视同研发类走 pending_qa，其 `in_progress → pending_qa` 由该角色自己操作、签发仍仅 @qa（**自定义角色一律不获得签发权**，四段闸门的自签只对主 Claude 开）；「分析/咨询」「协调」类视同非研发类直接流转。项目在角色定义的 Step 3 段写明归类，不明确时按研发类处理（fail-safe：多过一道 QA 优于漏签发）。

### 签发块 schema（`qa_signoff` / `verified_ranges`）

`qa_signoff` 接受**两种形态**，按谁签发分：

| 形态 | 用在哪 | 谁校验 |
|---|---|---|
| **字符串**（散文签注） | @qa 签发。写清验了什么、没验什么 | 无机器校验 |
| **映射**（四段举证） | **主 Claude 自签或轻签注时必须用这种** | `signoff-guard.py` hook 逐字段校验 ＋ 重算四段闸门 |

```yaml
- id: TASK-042
  status: completed
  qa_signoff:
    tier: 自签                    # 自签 / 轻签注 / 完整验证 / 你拍板
    same_party: true              # 实现方与签发方是否同一方（**必填**）
    admission:                    # ① 准入凭据——**hook 按这几个输入重算**，不读你写的结论
      radius: 2                   # 爆炸半径（1-4，模型判的输入）
      since: "a1b2c3d4"           # 切片起点 = 上一个签发点
      diff_ref: "e5f6a7b8"        # 被判 diff 的锚点
      at: "2026-09-10T01:02:03+00:00"   # UTC ＋ 秒级
      passport:                   # 通行证；没有就写 null
        task: TASK-041
        round: "第二轮"
        covered: "改动面与本轮 delta 的关系，一句话说清"
        ref: "a1b2c3d4"
    verified: |                   # ② 验了什么，逐条
      ...
    not_verified: |               # ③ 没验什么 ＋ 为什么，逐条
      ...
    unreviewed: |                 # ④ 未独立复核的部分
      ...
  verified_ranges: |              # 本次签发覆盖到哪儿（**必填**，与 qa_signoff 同级）
    ...
```

**四个字段各自防什么**（缺一即被 hook 拒，红灯会点名是哪一段）：

- **`admission` 是四段里唯一由机器重算的那段。** hook 只吃 `radius` / `since` / `passport`
  这几个**输入**，自己调判定脚本重跑一遍，把结果与 `tier` 比——**写入的档位比重算结果松即拒**。
  所以「把否决项写成未命中」这种事做不到：那不是输入，是结论，hook 根本不读它。
- **`same_party`** 把「实现方与签发方是同一个」从散文变成可分组字段。允许同一方时这是唯一的
  补偿控制：缺了它，事后问题率就只剩一个混合池，两种成色分不开。
- **`unreviewed` 的判据是二元口诀**：本轮有没有第二个 context 从零看过同一份产出？
  **没有 → 写「全部」**，然后列出你据以自信的机器证据。同一方签发时恒命中「没有」。
- **`verified_ranges`** 是下一轮增量复核的锚点。不写它，下一轮只能退回全量复核或凭印象划范围。

> **`tier: 自签` / `轻签注` 默认可用**：`.workframe-config.json` 的
> `signoff.self_signoff_enabled` **缺键即开启**（默认 true）。要关掉得显式写
> `false`；关掉之后 hook 会拒绝这两档，红灯说明要动的是配置而不是那一行档位。
>
> **默认开启是一个被知情接受的取舍**：它让能力对多数项目真正可用，代价是升级到本版
> 的项目不做任何动作就获得了自签权，且 **Codex 门下本 hook 实测不起把关作用**（Codex 改文件
> 走 `apply_patch`，载荷没有 `file_path`，hook 起了也直接放行——用它改看板不拦也不报；经 shell
> 改看板只提示）⇒ **那一门下的把关按不存在对待**（自签照样生效，四段举证纯靠自觉）。要求更严的项目请显式关闭。

### 签发权限

| 状态变更 | 允许操作者 |
|---------|-----------|
| `pending → in_progress` | 该任务 `assigned_to` 的角色（认领即开工，不需要谁签发） |
| `in_progress → pending_qa` | @dev、@prompt-eng（研发任务必经）；走 pending_qa 类的项目自定义角色（归类见 §pending_qa 适用范围）同此；**主 Claude 直做时代行**——`assigned_to` 仍填对应角色（域语义不变），并打 `tags: [main-executed]` |
| `pending_qa → completed` | @qa（**baseline 角色里**唯一可签发研发任务完成的，含自定义角色的研发类任务）。**代行不含签发权**——主 Claude 直做研发任务不因此获得签发权。主 Claude **自签**的准入由四段闸门判定（判据见必载片 §谁签发这次收口），且 **自签档默认开启**（`.workframe-config.json` 的 `signoff.self_signoff_enabled` **缺键即 true**）；要求更严的项目显式写 `false`，写了之后本行等价于「**签发权仍仅 @qa**」，直做后仍须实际调度 @qa |
| `pending_qa → blocked` | @qa（测试不通过时） |
| `in_progress → completed` | @pm、@dev（非研发任务）、@qa（非研发任务）、@prompt-eng（非研发类 Prompt 咨询）；分析/咨询/协调类的项目自定义角色同此 |
| `任意 → blocked` | 任何角色（遇阻即可标，必须同时写 `blocked_reason`）。非 QA 角色直接标 blocked 是被允许的路径——必载片 §Step 1 — 事件流 的 task_blocked fallback 正是为它准备的 |
| `blocked → in_progress` | 阻塞解除后由 `assigned_to` 角色自行恢复；QA 打回的任务由完成研发交付的角色（@dev / @prompt-eng / 走 pending_qa 类的自定义角色）修复后恢复，不需要 @qa 再签一次 |
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
python "$(cat .workframe/state/plugin-root.txt)/bin/workframe-recompute-board-summary"
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
