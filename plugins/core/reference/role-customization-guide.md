# 角色扩展规范

本文档定义如何在项目内扩展或定制 agent。

## 4 通用角色（来自 core plugin）

| 角色 | 核心定位 | 绑定 skills（core 默认） |
|---|---|---|
| `pm` | 需求分析、功能拆解、PRD 撰写、验收标准、竞品调研 | requirement-analysis, prd-writer, acceptance-criteria（高频预载；功能拆解 / 竞品 / 指标 / 反馈分析 / 交互 demo 等其余能力按需经 Skill 调用） |
| `dev` | 全栈工程师（前后端、数据库、部署、Bug 修复、技术方案） | technical-design, systematic-debugging |
| `qa` | 测试用例设计、代码审查、Issue 管理、研发任务签发 | test-case-design, code-review |
| `prompt-eng` | Prompt 设计优化、AI 策略、实验评估 | prompt-design, prompt-evaluation |

订阅 core plugin 后这 4 个角色自动可用，**不需要**在项目本地 `.claude/agents/` 下重复定义。
看板维护、节奏把关、summary 手工兜底等项目管理工作**不设专职角色**——由主 Claude 直接承担（`task-management` skill + `workframe-recompute-board-summary` 命令）。

**默认路由偏好**：`project_scaffold.py` 根据 `role_profile` 字段在生成的 `CLAUDE.md` 中渲染"路由偏好"段，决定 4 角色的默认优先级（如内容运营项目 prompt-eng 默认权重低）。这是**软提示，不禁用任何 core agent**——用户始终可 `@角色名` 直接调用。完整 3 profile 定义见 [`role-profile-catalog.md`](./role-profile-catalog.md)；profile 与 override 的关系：profile 不限制项目级 override 行为，用户随时可全量覆盖任何 core agent。

**系统 skills 不绑 agent**：`librarian` / `self-iteration` / `session-digest` 为内部调用，由 hook 链路或 `/core:maintenance-review` 等命令触发；`audit` / `rollback` / `memory-log` / `maintenance-review` / `onboard` 为用户直接 `/core:<name>` 调用。它们**不 preload** 给任何 agent。

**HEARTBEAT / Librarian / self-iteration 的执行主体说明**：周期性维护由 hook 链路（`heartbeat-check.py` / `session-end-flush.py` 等）自动驱动，不由任何 agent 主动派发。HEARTBEAT 提醒以 stdout 注入主 Claude 上下文（不落报告全文文件），由主 Claude 直接处理；`.claude/workframe-state/heartbeat-state.json` 只保存周期标记防重复，不存报告内容。

## 项目级扩展（override 或新增）

### 何时 override 现有角色

- 给通用角色加项目特殊约束（如"@dev 在本项目必须优先用 TypeScript"）
- 给通用角色绑定项目特有 skill（如 @pm 绑 `feishu-publish` 等项目配备的发布 skill）
- 调整通用角色的产出细节（如周报 / 分析报告落盘到项目自定义目录；PRD 章节结构本身改项目 `.claude/skills/prd-style/` 即可，无需 override agent）

**做法**：在项目本地 `.claude/agents/<role>.md` 放同名文件，Claude Code 官方优先级规则会让项目版覆盖 plugin 版。

**建议**：override 时基于 plugin 版全量复制后修改，避免遗漏 frontmatter 必填字段或 Step 3 状态流转规则。**不要重复 `agent-protocols.md` / `response-output.md` / `auto-update.md` 已定义的通用协议**——通用协议自动加载，override 文件只需写差异。

### 何时新增角色

- 项目有通用 4 角色覆盖不到的职责域（如 CEO 协调、设计师、内容运营、法务）
- 职责分工明显不同于通用角色（不只是"给 @dev 换个名字"）
- core dev 是"全栈"定位，需要专业化分工时（前端独立 / DBA 独立 / DevOps 独立）可拆分为 `frontend-dev` / `backend-dev` / `devops` 等

**做法**：在项目本地 `.claude/agents/<new-role>.md` 创建新文件。新增角色自动受 `agent-protocols.md` 约束，无需重复通用协议。

## Agent 文件规范

所有 agent（含项目级新增）必须遵循以下结构。**Core 4 agent 已是这套结构的标准实现**，可作为参考。

### Frontmatter（必填字段）

```yaml
---
name: <角色名>                          # 必填，小写字母+短横线，如 content-operator
description: |                          # 必填，多行描述
  <一句话定位>。<具体负责什么>。
  触发场景：<什么场景会调这个角色>。
  <核心约束，如"不直接编码">。
tools:                                  # 必填，该角色允许用的工具列表
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - AskUserQuestion
  # 按需加 WebSearch / WebFetch
# model 字段留空，默认 inherit 主会话模型（开源兼容最佳实践）
# 如需锁定模型：只用别名 `model: opus` / `sonnet` / `haiku`——**不要写完整 model ID**
# （理由见本文 §编写约束 第 5 条：具体 ID 随模型换代失效，validate 会拦）
# 不写 memory 字段：官方注入目录键名带 plugin 前缀（core:pm → agent-memory/core-pm/），
# 与框架 <role>/ 布局不符且维护指令与 D/U/R/A 冲突；角色记忆由 SubagentStart hook 注入（agent-protocols §1）
skills:                                  # 可选，该角色绑定的 skill 名列表
  - skill-a
  - skill-b
---
```

### 正文结构

```markdown
# <中文名> @<name>

> 启动协议、协作边界、通用收尾协议（Step 0-3 通用骨架）见 workframe core rule: `agent-protocols`（项目内同步路径 `.claude/rules/workframe/core/agent-protocols.md`）。本文件只定义 @<name> 的角色特质。

## 角色定位

<一段话定义角色的本质——是把什么转化为什么>

## 核心职责

1. **<职责 1>**：<业务术语描述，不引用具体 skill 名>
2. **<职责 2>**：<业务术语描述>
...

## 特有写入边界（可选）

- **可写**：<本角色允许写的具体路径>
- **禁写**：<本角色独有的禁写约束；受保护资产清单见 auto-update.md 不重复>

## 特有约束

- <本角色独有的约束，如 qa 测试独立性、dev 工程纪律>

## Step 3 扩展 — <角色名> 任务流转

通用 Step 3 规则见 `agent-protocols.md`。@<name> 特有：

- <本角色的状态流转规则，如 "dev 研发任务从 in_progress 只能流转到 pending_qa"、"qa 可签发 pending_qa → completed"、"pm 非研发任务可直接 completed">
```

**agent body 不内联通用协议**：启动/收尾协议（`agent-protocols.md`）、响应正文优先（`response-output.md`）、受保护资产清单（`auto-update.md`）均由 core rules 自动加载，body 只写角色特质。

## 任务状态流转约定

> **完整签发表以 skill: `task-management` §签发权限 为准**（8 行，含 blocked 出边、
> cancelled 主体、pending→in_progress）。下表只是新增角色最常用的几行摘录——
> 照它写 Step 3 会漏掉整组 blocked / cancelled 规则。

| 角色 | 允许的状态流转 |
|---|---|
| `pm` | `pending → in_progress → completed`（非研发任务） |
| `dev` | 研发任务 `in_progress → pending_qa`（不能直接 completed）；非研发任务可直接 `completed` |
| `prompt-eng` | Prompt 变更类任务同 dev（`in_progress → pending_qa`）；非研发类咨询可直接 `completed` |
| `qa` | `pending_qa → completed` / `pending_qa → blocked`（唯一签发角色）；自身非研发任务可直接流转 |
| 主 Claude | summary 手工兜底重算（常规自动重算由 SessionEnd hook 完成）；归档任务（用户确认后执行） |

新增角色时应决定它属于哪个类别：

- **"产出修改线上内容"类**（如 content-operator、designer）：建议走 `pending_qa`
- **"分析/咨询"类**（如 legal-advisor 输出建议）：可直接 completed
- **"协调"类**（如 ceo）：直接更新 status

## 项目级路径与命名约定

Core agent **不硬编码项目特定路径**（具体子目录结构、文件命名格式等），由各项目在 `CLAUDE.md` 自定义。常见可定制项：

### 需求文档目录结构

需求事实源统一走 modules/ 体系：`projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md`（二段式 modules + 需求资产包 + 子需求目录）。

**版本演进**：默认建 `main/` 子需求目录；范围显著变化时新建另一个 `<sub_req_slug>/`（不挪 `main/`，跨子需求引用 `[[<req_slug>/overview]]` 始终有效）；同一子需求的小版本演进走 `prd.md` 正文「变更与决策记录」。详见 `module-architecture.md` §4.3。

文件命名格式由对应 skill 决定（`prd-writer` 走 `prd.md`），不由 agent 文档强制；存量项目接入时的遗留结构由 `migrate-to-modules` 收编，不另立组织约定。

### Prompt 资产目录（仅 prompt-eng 项目相关）

如项目需要 prompt 工程师管理生产 prompt：

| 产出类型 | 推荐路径 | 备注 |
|---|---|---|
| Prompt 文件本体 | `projects/prompts/<场景>/<name>.md` | 一文件一 Prompt（生产资产，不是需求文档） |
| 策略变更说明 | 需求形态 → `modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md`；决策记录形态 → `modules/<basic>/<sub>/decisions/` | 与其他需求同走 modules/ 体系 |
| 评估实验数据 | `projects/evals/prompts/eval-{date}.md` | 含基线对照 |

### 测试目录

| 项目栈 | 推荐目录 |
|---|---|
| 通用 | `tests/` |
| Node.js（Jest 默认） | `__tests__/` |
| 端到端 | `e2e/` |
| Python（pytest） | `tests/` |

### 任务归档

任务归档不是自动动作，需用户确认后由主 Claude 执行。归档文件命名格式由项目约定（如 `projects/board-archive-2026-Q2.yaml`）。

### 外部文档同步

PRD 创作（产出本地 `prd.md` + HTML 原型 + 截图）由 core skill `prd-writer` 提供，**与平台无关**，落 `projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md`；章节框架与写作风格读项目 `.claude/skills/prd-style/`（装机放入，项目可自由修改）。

如需把 specs 单向发布到飞书 / Notion / Confluence 等外部系统，按以下步骤：

1. 在项目 `.claude/skills/` 下新增**发布器**（命名规范 `<platform>-publish`，如 `feishu-publish` / `notion-publish`）；这类 skill **只负责本地 Markdown → 外部文档** 的单向同步，不参与 PRD 内容生成
2. 在项目 `.claude/agents/<role>.md` override 中把发布器追加到 `skills:` 列表（如 @pm 挂 `feishu-publish`）
3. 用户明确触发"发布到 X"才启动发布器；本地写完不会自动发布

**Core plugin 的 agent 和 `prd-writer` 都不绑定任何外部文档系统**；`prd-writer` 的 S6（发布段）是可选发布的衔接点，由发布器接管。

## 常见扩展角色示例

| 角色名 | 定位 | 什么时候需要 |
|---|---|---|
| `designer` | 视觉设计、UI 设计、品牌资产 | 产品有独立设计职能 |
| `data-analyst` | 数据分析、KPI 追踪 | 需求决策强依赖数据 |
| `researcher` | 技术调研、资料整理 | 前期调研量大 |
| `content-operator` | 内容运营、社媒发布、内容日历 | 产品带内容运营侧 |
| `market-researcher` | 市场调研、竞品分析、情报收集 | 竞争激烈、需常态化情报 |
| `legal-advisor` | 合规审查、合同起草 | 业务涉合规红线 |
| `customer-manager` | 客户关系、订单跟进、客户沟通 | 有直接对客交付职责 |
| `frontend-dev` / `backend-dev` / `devops` | core dev 拆分版 | 研发需要专业化分工 |

具体角色的职责细节与技能绑定按项目实际需要自行定义。

## 如何从 @dev 拆分专业工程角色

需要更细粒度源码写入权限分配时，可在项目本地 `.claude/agents/` 新增 `frontend-dev` / `backend-dev` / `devops` 等替代 core `@dev`。本节只给原则和最小示例，完整 agent 结构沿用 §Agent 文件规范。

### 4 条原则

1. **覆盖优先于并存**：项目本地同名文件直接覆盖 plugin 版；如必须与 core `@dev` 并存（如保留 core `@dev` 作为跨栈协调者），必须在项目 `CLAUDE.md` 路由规则段明确各角色负责的目录或文件类型，避免主 Claude 路由摇摆
2. **签发权限沿用**：拆分角色的研发任务仍走 `pending_qa → completed`，仍由 @qa 签发；**不**给新角色额外加签发权
3. **工程纪律一致**：拆分角色继承 workframe core rule: `agent-protocols` + `technical-design` skill 的 `engineering-discipline` reference 的工程原则，新 agent 文件不重复
4. **写入边界互斥**：每个拆分角色的"特有写入边界"段必须列出本角色独占的目录 + 明确禁写其他拆分角色的目录，防止双向越权

### 最小示例 frontmatter

```yaml
---
name: frontend-dev
description: |
  前端工程师。负责 React / Vue 组件、页面布局、前端构建链。
  触发场景：前端编码、组件实现、UI 调试、前端构建配置。
  与 backend-dev 通过 API 契约对接；不修改后端代码。
tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion]
skills: [technical-design, systematic-debugging]
---
```

`backend-dev` / `devops` 同结构，按职责改 description 和写入路径段。

### 项目 CLAUDE.md 路由配套

```markdown
## 路由规则（项目专属）
- 前端代码（`web/`、`packages/ui/`）→ @frontend-dev
- 后端代码（`api/`、`packages/server/`）→ @backend-dev
- 部署 / CI / 容器化 / 监控 → @devops
```

## 编写约束

为保持 core agent 的开源可移植性 + 多项目可复用性，agent 文件**禁止**：

1. **不内联通用协议**——启动协议 / 收尾协议 / 受保护资产清单 / 响应正文优先等由 rule 自动加载，agent body 不重复
2. **不引用具体业务 skill 名**——核心职责段用业务术语描述（如"需求分析"），不写"用 X skill"。frontmatter `skills:` 列表 + skill description 让 Claude 自动匹配
3. **不硬编码项目特定路径**——不写 `projects/specs/<模块>/<子模块>/<迭代>/`、`REQ-{序号}.md`、`board-archive-<YYYYMM>.yaml` 等占位符。框架契约根路径（`projects/specs/` / `projects/board.yaml` 等）可保留，子结构和命名格式由项目 `CLAUDE.md` / 对应 skill 决定
4. **不使用派发式语言**——不写"通知 @qa 测试" / "请 @pm 评估"。改为响应文字标注 + 看板状态 / Issue tag 表达，由用户/主 Claude 调度（详见 `agent-protocols.md` §2 协作边界）
5. **不硬编码具体 model ID**（如 `model: claude-opus-4-8`）——默认 `inherit` 主会话模型；锁定模型时使用别名 `opus` / `sonnet` / `haiku`

`tools/validate.py` 自动校验上述约束，新增 / override agent 文件后跑 validate 即可发现违规。

## 注意事项

- 新增角色后，在 `.claude/agent-memory/<new-role>/` 下创建 `MEMORY.md` 和 `notes.md` 空骨架（否则 Librarian 第 1 步动态遍历时会找不到）
- Agent 正文里的所有路径都是**项目内部相对路径**（`.claude/agent-memory/...`、`projects/...` 等），不要硬编码绝对路径
- 若项目要 override core 角色，建议在 override 正文顶部注明"基于 core plugin dev.md @ {framework_version} 扩展"（version 取 `.workframe-config.json.framework_version`）以便追溯

## 协议契约（手工新增 / 接入旧 agent 必读）

**前提认知**：subagent 是独立 context window，body 是它的 system prompt，不接收完整的主 Claude system prompt。但官方 [sub-agents](https://code.claude.com/docs/en/sub-agents) §What loads at startup 已明确把 **project rules**（即 `.claude/rules/`）与 CLAUDE.md 层级一并列入 non-fork subagent 的初始 context。

两条边界仍需注意：

- **内置 Explore / Plan 例外**：官方原文 "Explore and Plan are the only subagents that omit CLAUDE.md and git status"，且无 frontmatter 开关可改。项目级 named agent 不受此限。
- **path-scoped rules（带 `paths:` frontmatter）的 subagent 行为，官方文档**没有明确保证****——其触发条件是"Claude 读到匹配文件时"，在 subagent 独立 context 中的表现未文档化。协议类 rule 不要加 `paths:`。

所以：**协议可达性本身已由 runtime 保证**，body 顶部的协议引用不再是功能必需，降为可读性与 reminder 用途（见下 §强烈建议）。

另见 workframe core rule `agent-protocols`；事件可靠性分层的机器可读定义见 `.workframe-meta/event-schema.json`。

### 必备 1 项

**frontmatter 不写 `memory` 字段**（与 core 4 agent 一致）

```yaml
---
name: finance
description: ...
tools:
  - Read
  - Write
# 不写 memory 字段——角色记忆由 SubagentStart hook 自动注入 shared/ 与 <role>/ 的 MEMORY.md（agent-protocols §1）
---
```

写了 `memory: project` 的后果：CC 会按 agent 全名建独立记忆目录（plugin agent `core:pm` → `agent-memory/core-pm/`；项目级 agent 为 `agent-memory/<name>/`）并向 subagent 注入一套官方记忆维护指令——与框架的 `<role>/` 仓 + D/U/R/A + librarian 体系并行分叉（2026-07-18 在消费项目实测确认分叉 2 个月后拍板移除）。

### 强烈建议 2 项（reliability recommendation）

**1. body 顶部（H1 之后）含 agent-protocols 引用一行**

```markdown
# 财务分析师 @finance

> 启动协议、协作边界、通用收尾协议(Step 0-3 通用骨架)见 workframe core rule: `agent-protocols`（项目内同步路径 `.claude/rules/workframe/core/agent-protocols.md`）。本文件只定义 @finance 的角色特质。

## 角色定位
...
```

价值（协议可达性已由 runtime 保证）：
- **文档可读性**：让 agent.md 维护者 / 后来 reader 一眼知道协议来源
- **模型 reminder**：rules 虽自动注入，但在 context 压力下 body 内重申协议存在有助于不跳过收尾步骤

**2. `.claude/agent-memory/<role>/` 目录预创建**

agent wrap-up Step 2 写入 `<role>/notes.md` 时，若目录不存在，Write 工具会自动 mkdir parents（实测过），所以**最终能 work**。但建议预创建空骨架的理由：
- librarian 第 1 步用 Glob 扫所有 `agent-memory/*/`；目录不存在时 librarian 第一次扫不到该 role
- 显式骨架避免依赖工具的隐式行为，更稳

baseline 4 个 core role（pm/dev/qa/prompt-eng）已由 scaffold 预创建；项目级 custom role（designer / data-analyst 等手工新增的）由创建者自行补建记忆骨架。

### 自动检测与修复（Phase 2 planned，尚未实现）

检查方式：手工对照本节清单。用 `templates/agent-template.md` 创建项目级角色时**默认含必备 + 建议项**，无需手工补。
