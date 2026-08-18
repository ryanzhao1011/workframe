---
name: auto-update
created_at: 2026-04-23
scope: project
action: auto-capture
---

# 信息自动更新规则（全局生效 - 通用协议）

> 此文件由 Claude Code 运行时自动加载（`.claude/rules/` 系统能力），所有 Agent 无需引用即生效。
>
> **本文件是领域无关的通用协议**。业务/场景特定触发词（如客户线索、合规预警、社媒发布、功效宣称等）应以项目级 rule 的形式写入 `.claude/rules/local/auto-update-<domain>.md`，由项目维护者按需生成。

## 排除条件（不触发更新）

**判断标准**：用户是在**陈述新事实或下达指令**（触发），还是在提问咨询 / 假设讨论 / 转述他人未确认采纳的说法 / 回顾已有事实（**均不触发**）？歧义时先向用户澄清。

## 优先级定义

| 级别 | 含义 | 确认要求 | 关联动作 |
|------|------|---------|---------|
| **P0** | 错误代价高：系统安全 / 重大泄露 / 线上故障，或产品方向性变更（需求范围 / 优先级 / 上线时间） | 写入前须用户确认 | 自动追加 `projects/board.yaml` 任务 + 必要时创建 `projects/issues/` |
| **P1** | 影响产品方向或技术架构，但无即时风险 | 回显摘要后可直接写入 `notes.md` / 角色级 `MEMORY.md`；`board.yaml` 任务一律走 `task-management` skill / 主 Claude 落盘（不直接写）；触及受保护资产须用户确认并交由 owner role 执行 | 追加任务草稿/提案草稿 |
| **P2** | 有参考价值，不影响当前迭代 | 即时归档，无需确认 | 仅日志记录 |

## 通用触发类别（core-level）

### P0 — 立即处理（需确认后写入）

#### 安全问题
→ `projects/issues/SEC-{序号}.yaml`
→ 关联：`board.yaml` 追加 P0 任务（`assigned_to: dev`，`tags: [auto-update, P0, security, needs-qa-regression]`）

#### Prompt 泄露风险
→ `projects/issues/SEC-{序号}.yaml`（title 前缀用 `[Prompt泄露]`）
→ 关联：同安全问题

#### 需求变更（含范围 / 优先级 / 上线时间调整）
→ **响应中输出 PRD 草稿 + board task 草稿**（不直接写文件）
→ 落盘路径见下方 §受保护资产约束 的「需求文档（PRD）」条目；board task 由主 Claude 落盘 `projects/board.yaml`，task 必填二段式 `module: <basic>/<sub>`；挂需求的还要 `req_slug` + `sub_req_slug` **一起填**（`main` 也显式写，见 `document-norms` §2.6），不挂需求的维护类任务两个都不填

#### 线上故障
→ `projects/issues/BUG-{序号}.yaml` + `board.yaml`
→ 关联：追加 P0 任务（`assigned_to: dev`）

**需求变更 vs 线上故障判断标准**：系统按预期运行但输出不满足用户预期 → **需求变更**（走需求文档 + board：modules/<basic>/<sub>/requirements/）；系统未按预期运行 → **线上故障**（走 issues + board）。

### P1 — 尽快处理

#### 技术决策（架构选型 / 数据库 / API 设计 / 依赖选择）
→ 先过归属分流（`agent-protocols.md` Step 2，判据 = 谁消费）：**决策本身落文档**——单模块的进
`projects/modules/<basic>/<sub>/decisions/`，跨模块的进 `projects/specs/plans/`；`@dev MEMORY.md`
**只留一行指针**。团队级技术共识的消费者是所有查这个模块的人，不是 dev 一个角色
→ 只有**纯执行经验**（踩坑、工具用法、本机环境这类别人查文档查不到的）才直接进
`@dev MEMORY.md`（满足 D/U/R/A ≥2 项）
→ 关联：如需实施，**响应中输出 board task 草稿**，由 `task-management` skill / 主 Claude 落盘到 `projects/board.yaml`

#### Prompt 策略变更
→ `@prompt-eng notes.md`（策略变更信号）+ `@prompt-eng MEMORY.md`（高置信事实）
→ 关联：**响应中输出 board task 草稿**（`tags: [prompt-review]`），由 `task-management` / 主 Claude 落盘；@prompt-eng 仅产出**评估结论和修改建议**；涉及 `.claude/skills/**` / core plugin 文件的修改走 `/core:self-iteration` L2 提案路径或用户显式审批，**不由 @prompt-eng 直接落盘**
→ 升级条件：质量下降导致影响用户 → 升级 P0

#### LLM 模型/成本（涨价 / 新模型 / API 变更 / 下线 / 降级链）
→ `@dev MEMORY.md`（直接写入）
→ 关联：**响应中输出 board task 草稿**（评估路由策略），由 `task-management` / 主 Claude 落盘
→ 升级条件：模型下线或 API 破坏性变更 → 升级 P0

#### 质量标准调整（敏感词库 / 质量分权重等判定标准）
→ `@qa MEMORY.md` + `@prompt-eng MEMORY.md`

#### 工作方式/流程（"以后 XX 要这样做" 类指令、对产出的正负面评价）
→ 正面/负面反馈 → 对应角色 `notes.md`（D/U/R/A 2/4 通过则提升到 MEMORY.md）
→ 流程调整 → `shared/notes.md`（流程影响 ≥2 角色，进跨角色缓冲区由 librarian 按 D/U/R/A 评估提升）+ **响应中输出 board task 草稿**（由 `task-management` / 主 Claude 落盘）；由主 Claude 评估后产出**流程调整建议**；涉及 `agents/rules/skills` 或 core plugin 文件的修改走 `/core:self-iteration` L2 提案路径或用户显式审批，**不直接落盘**
→ 模式经验 → 对应角色 `notes.md`

### P2 — 常规记录

#### 行业动态 / 通用参考信息（趋势、市场、技术动向）
→ `@pm notes.md`

## 项目级扩展

core 只含以上通用触发。业务特定触发词写在 `.claude/rules/local/auto-update-<domain>.md`，继承本文件的排除规则、优先级定义与执行规范。

## Issue 模板

创建 issue 时使用标准模板（随项目骨架初始化，见项目内 `projects/issues/TEMPLATES.md`）：
- **SEC-{序号}.yaml**：安全类（权限、Prompt 泄露）
- **BUG-{序号}.yaml**：故障类

## 受保护资产约束

以下文件/目录为系统定义资产，auto-update **不直接写入**，只能回显摘要 + 在 `board.yaml` 创建任务/提案，交由对应 owner role 在用户确认后执行：

- `CLAUDE.md`
- `.claude/agents/**`
- `.claude/rules/**`（含 `.claude/rules/workframe/core/**`、`.claude/rules/local/**`）
- `.claude/skills/**`
- `.claude/settings*.json`
- `.workframe-config.json`（项目身份与运行档位配置；由 launcher / scaffold merge 维护，字段变更须用户确认）
- `.claude/workframe-state/**`（events.jsonl / activity-state.json / skill-metrics.yaml / rollback-index.json / memory-index.json 等运行态；由 hooks / system skills / 专门 rule 维护，auto-update 不参与）
- `.claude/agent-memory/shared/MEMORY.md`（跨角色权威事实；auto-update 默认不直接写——影响 ≥2 角色的共识需用户显式确认或走 librarian 提升路径）
  - **例外**：`correction-detection` 处理的用户纠正**本身就是显式授权**（用户当场说的就是权威口径），
    按该规则第 3 步分流直接写入高置信区，不受本条限制；其第 2 步的回显即是确认动作。
- `projects/proposals/**`（self-iteration 闭环资产；auto-update 不创建/修改提案，相关变更走 `/core:self-iteration`）
- 仓库根 `README.md` / `LICENSE`

**可直接写入的资产**：
- `notes.md`、role 级 `MEMORY.md`：可按本规则 P0/P1/P2 直接写入
- `projects/issues/`：P0 安全/故障可在用户确认后直接创建（由本 rule §执行规范第 2 项 P0 先确认再写入流程保证）
- `projects/board.yaml`：**仅 P0 单条紧急任务**（安全 / 线上故障类）允许在用户确认后直接追加 `status: pending` + `tags: [auto-update]`；其他场景（批量任务、需求拆分产生的任务）一律走 `task-management` skill 或由主 Claude 落盘
- **需求文档（PRD）**：**默认只输出草稿到响应**，由 @pm 或用户确认后落盘；非 PM 角色触发"需求变更"信号时只输出变更摘要 + 落盘草稿到响应，不直接写需求文件
  - 落盘走 `prd-writer` skill 到 `projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md`；首次需求需要先调 `module-init` 建子模块/需求资产包（默认子需求 `main/`）

**受保护资产例外**：

- `.claude/settings*.json` 与 `~/.claude/settings.json`：**`/core:onboard` 是唯一豁免写入入口**，且须用户当面确认 + 备份 + JSON merge；前提不满足时它自身不写入，只输出手动补丁。其余任何路径都不得写 settings。
- 完整前提清单与决策依据见 `onboard/SKILL.md`

## 执行规范

1. **写入前检查（含 A.U.N. 工序，适用于所有 notes / MEMORY 直接写入路径）**：更新前确认目标文件存在且结构匹配。若文件不存在，参照同目录已有文件的格式创建；若 YAML 字段名与现有条目不一致，以现有条目为准。绝不猜测字段结构。**写入 MEMORY.md 时先过归属分流（`agent-protocols.md` Step 2：业务知识落对应 modules/ 文档，记忆至多留一行指针），再确认信息满足 D/U/R/A 准入标准 ≥2 项**（`correction-detection.md` 触发的 `[纠正]` 条目除外——由该规则跳过 D/U/R/A 直接写入高置信区，详见 `correction-detection.md` 第 3 步）。**落笔前先 Read 目标文件、检索同主题条目**（同一事实/口径/对象，表述可能不同），三选一：无同主题 → 新增；有同主题 → 改写合并成一条（保留信息量更大的表述，条目内标注更新时间与来源）；已有等价记录 → 不写，响应中说明。查重合并判据同 `librarian/SKILL.md` §融合整理 SOP（第 2.5 步）。
2. **P0 先确认再写入**：P0 级别变更必须**先向用户回显摘要并获得确认，再写入文件**。任何 P0 变更（含 specs / issues / board.yaml）一律遵循"回显 → 等确认 → 写入"顺序，不允许先写后确认。
3. **P1/P2 即时更新**：通过排除规则后立即更新，不等额外确认。但若目标位于受保护资产清单或需求文档（`projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md`）、批量 `board.yaml` 任务，仍走草稿/owner role 路径。
4. **同主题合并、异主题追加**：同主题信息按第 1 条 A.U.N. 工序合并/更新——历史语义在条目内标注更新时间与来源，被取代的旧表述移对应 `notes.md` 留档，**不并排堆同主题的多条版本**；不同主题才追加新条目。
5. **冲突处理**：新信息与现有数据冲突时，先提示用户确认，不自动覆盖。
6. **step 2 自检**：本规则在 `agent-protocols.md` §同消息内 rule 处理顺序中位于 step 2。写 MEMORY / event 前必须自检该事实是否已被 step 1（correction-detection）消费；已写入则跳过，只处理未消费的事实与指令。
7. **升级条件**：P1/P2 类别满足升级条件时，按升级后的优先级处理。
8. **关联任务**：P0 类紧急变更（安全 / 线上故障）在用户确认后，在 `projects/board.yaml` 的 `tasks:` 末尾追加**单条** `status: pending` 任务（`tags: [auto-update, <类别>]`），**仅作为信号标记进入待处理队列**——下次 session 由用户 / 主 Claude 看到 board 时分配处理（不自动派发，详见 `agent-protocols.md` §2 协作边界）。

> 「工作方式 / 流程」类纠正信号一律由 `correction-detection.md` 处理，本规则不处理。
> 追加 `board.yaml` 任务时遵循 `task-management` SKILL.md 定义的 schema 与状态流转。

## 示例

**P0 需求变更** — 用户："这个新功能要支持批量导出，优先级比之前讨论的高。"

1. 排除检查通过（陈述新事实 + 下达指令）→ 分类：需求变更 → P0
2. **回显摘要 + 等待确认**（P0 必须先于写入）：告知将输出 PRD 草稿 + board task 草稿，询问是否继续
3. 确认后：响应中输出两份草稿；PRD 由 @pm 落盘、board task 由主 Claude 落盘——**本规则不直接写**
   （安全 / 线上故障类 P0 例外：确认后可直接 append `issues/` + 单条 `tags: [auto-update]` 的 board pending 任务）

**不触发** — 用户："最近竞品有什么新动态吗？" → 提问句式命中排除条件。

