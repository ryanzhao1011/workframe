# {{PROJECT_NAME}}

> 基于 Workframe {{FRAMEWORK_VERSION}} 搭建的多角色协作项目
> 项目类型：{{PROJECT_TYPE}}
> 创建日期：{{CREATED_DATE}}

## 项目目标

{{PROJECT_ONE_LINE_GOAL}}

## 业务背景

{{PROJECT_BUSINESS_CONTEXT}}

## 订阅声明

本项目订阅 `core@workframe` plugin，订阅配置见 `.claude/settings.json`。核心通用能力（4 通用角色 + 全套 skills + 4 通用 rules + hook 链路）通过 plugin 自动获得，**不**在项目本地 `.claude/` 下重复定义；系统级 skills 不 preload 给任何 agent。

skill 名称与用途由会话启动时的 available-skills 列表给出，本文件不复述——**枚举会随 skill 增减漂移，而列表恒为当前态**。

## 角色体系

### 通用角色（来自 core plugin）

| 角色 | 核心能力 |
|------|---------|
| @pm | 需求分析、功能拆解、验收标准、竞品调研、PRD 创作 |
| @dev | 代码实施、技术方案、Bug 调试、部署 |
| @qa | 测试用例设计、代码审查、Issue 管理、任务验证签发 |
| @prompt-eng | Prompt 设计优化、AI 策略、实验评估 |

### 项目级角色（本地新增/override）

{{PROJECT_LEVEL_ROLES}}

## 路由规则

- 消息以 `@角色名` 开头 → 直接调度对应角色
- 未指定角色 → 主 Claude 按任务性质与预期效果**自行决定直做或委派**
- **主 Claude 必须将 subagent 返回的结果完整呈现给用户**

**主 Claude 直做角色工作时**（读 agent 文件 ≠ 获得 subagent 运行语义）：

- 直做前读两份：该角色 agent 契约（工程纪律／产物合同／状态流转）+ `.claude/agent-memory/<scope>/MEMORY.md`；路径见 SessionStart 记忆地图
- frontmatter 声明的 skills 不会自动调用——按任务显式触发
- 仍须委派：需独立 QA／工具隔离／多领域长任务／依赖 agent 专属上下文
- **硬约束：直做研发任务后不得自签 QA**，签发权仍仅 @qa

{{ROLE_PROFILE_ROUTING}}

## 任务状态流转与签发权限

研发类任务（`assigned_to: dev / prompt-eng` 的**编码 / 配置 / 变更类**任务，或 tags 含
`needs-qa-regression`）**必须经 QA 验证**。@dev 的技术咨询与方案评估、@prompt-eng 的非研发类
咨询、PM 分析、纯文档与系统维护任务都属非研发任务，不走 pending_qa（完整判据见
skill: `task-management` §pending_qa 适用范围）：

```
研发任务：pending → in_progress → pending_qa → completed
非研发任务：pending → in_progress → completed
```

| 状态变更 | 允许操作者 |
|---------|-----------|
| `in_progress → pending_qa` | @dev / @prompt-eng（研发任务完成时）；**主 Claude 直做时代行** |
| `pending_qa → completed` | 仅 @qa（签发研发任务完成）——**签发权仍仅 @qa**，不随代行转移 |
| `pending_qa → blocked` | @qa（测试不通过时） |
| `in_progress → completed` | @pm / @dev（非研发任务）/ @qa（非研发任务）/ @prompt-eng（非研发类咨询） |
| `pending → in_progress` | 该任务 `assigned_to` 的角色（认领即开工） |
| `任意 → blocked` | 任何角色（遇阻即可标，必须写 `blocked_reason`） |
| `blocked → in_progress` | 阻塞解除后由 `assigned_to` 角色自行恢复（QA 打回的由 dev / prompt-eng 修完直接恢复） |
| `任意 → cancelled` | @pm 或用户（必须写 `cancelled_at` + `cancel_reason`） |

主 Claude 直做研发任务时代行上述流转，`assigned_to` 仍填对应角色（域语义不变），并打
`tags: [main-executed]`（语义：主 Claude 实际完成了使任务进入 `pending_qa` 的研发交付；
只是参与讨论 / 出方案不打）。**签发权仍仅 @qa**——`pending_qa → completed` 仍须实际调度 @qa。

## 项目特殊约束

### 两套记忆分工

项目并行两套跨会话记忆，落点判据 = **谁消费**：

| 记忆 | 消费者 | 装什么 | 维护 |
|---|---|---|---|
| auto-memory（CC 官方记忆目录，主会话注入） | 主 Claude | 用户偏好 / 协作习惯 / 项目状态**指针** | 主 Claude 自维护 |
| `.claude/agent-memory/<role>/MEMORY.md` | 单个角色 | 该角色执行经验（D/U/R/A ≥2，四条定义见 `agent-protocols.md` Step 2） | librarian + 角色 |
| `.claude/agent-memory/shared/MEMORY.md` | ≥2 角色 | 跨角色权威事实（写入条件更严） | librarian |

- **业务知识不进任何记忆**：需求口径 / 方案论证 / 领域事实 → 业务文档；记忆只留指针。
- **auto-memory 写入纪律**（叠加在官方记忆指令之上）：单条 ≤3000 字符；**指针优先**——权威文档能推出的内容不复制进记忆，只存文档推不出的判断（环境坑 / 未决项 / 拍板理由）；**工作纪实**进 changelog / 文档，不进记忆；超预算 → 先落文档再指回，不硬塞不硬砍；**scope 级指针** = 工种知识迁往 `.claude/agent-memory/<scope>/` 后，auto-memory 每 scope 至多保留一行权威指针（如「dev 域执行经验权威见 `.claude/agent-memory/dev/MEMORY.md`」），**不写条目级指针**。
- **auto-memory 清理判据**（模型只提候选，归档/删除一律用户拍板）：需求完结后其记忆压成一行指针；同模块 ≥3 条记忆合并为一条（**scope 级指针行豁免**）；已完结且文档承载的工作记忆 → 归档 = 文件移入 `.claude/memory-archive/` + 删索引行（**文件不灭：git 可溯 + 仓库外备份**）。

### 工种知识的落点判据（主 Claude 与 subagent 收尾时共用）

落点由「**未来谁要读它**」决定，不由「这次谁做的」决定。subagent 干活时两者重合；**主 Claude 直做时不重合**——它可能在做 dev 域的活，经验却该落 dev 域给未来的 @dev 读。

| 判据 | 落点 |
|---|---|
| 业务知识（需求口径／方案结论／领域事实） | 业务文档为权威，记忆至多一行指针 |
| 主 Claude 协作行为／用户偏好／项目状态指针 | auto-memory |
| 工种知识，未来场景可点名**恰 1 个角色域** | 该角色 `notes.md`（格式须为 `### ` 章节头或 `- YYYY-MM-DD:` 列表，否则积压计数扫不到）；高置信（D/U/R/A ≥2 **且**亲测／用户确认）直写 role `MEMORY.md` |
| 工种知识，未来场景可点名 **≥2 个角色域** | `shared/notes.md`；判据 = 该角色未来在什么决策／操作时要**读**它，工具重叠不构成证据。**shared 不许直写 MEMORY**，由 librarian 评估提升 |
| 拿不准 | scope 最小化落最可能的单角色，并反问「是不是只有该域会读」——答不出即列为 shared 候选，等第二次真被另一域用到再上提 |
| 当轮即弃 | 不入记忆 |

主 Claude 恒为潜在消费者，**不参与落点计数**。完整协议与 **D/U/R/A 四条定义**见 `agent-protocols.md` Step 2（每会话必载，此处不复述）。

{{PROJECT_SPECIFIC_CONSTRAINTS}}

## 业务目录速查

{{PROJECT_BUSINESS_DIRECTORIES}}

## 文档与结构约定

本项目所有 markdown 文档的归属、frontmatter 标准、索引规则等请参考 `document-norms` skill（章节化 §1-§11）。新建/修改文档前调用一次该 skill 即可获得完整规范，业务方按需读对应 § anchor 而非整 skill。

**文件放哪儿**查 `document-norms` §1 归属矩阵——那里是唯一事实源（28 行、含 `type` 字段与反模式警告）。本文件不再内嵌简表副本：副本一旦与 §1 分叉，读到副本的人就照错的做，而它无条件渲染进每个新项目的必载上下文。

## 快速入门

- `看板` / `项目进度` — 直接问主 Claude，读 `projects/board.yaml` 汇报
- `@pm 需求分析 [需求]` / `@pm 写需求文档` / `@pm 拆解功能`
- `@dev 做技术方案 [需求]` / `@dev 修 bug：[现象]`
- `@qa 测试 [任务ID]` / `@qa 审查这个 PR / 这段代码`
- `@prompt-eng 设计 prompt [场景]`
- `/workframe-launcher:setup`（另建新项目 / 接入已有项目）/ `/core:audit`（看维护活动）/ `/core:maintenance-review`（进维护流程）
- 直接说任务 — 主 Claude 自行决定直做或委派（指定 `@角色名` 则强制委派）

## 框架相关

- 框架文档：框架仓 `docs/`（quickstart / setup-guide / concepts / onboarding / rules-sync）；**仓库位置读 `.workframe-config.json` 的 `framework_path`**（marketplace 安装的见框架 GitHub 仓库）
- 通用 rules：同步在 `.claude/rules/workframe/core/`（只读镜像，请勿手改）
- 项目专有 rules：`.claude/rules/*.md` 或 `.claude/rules/local/*.md`
- 项目 PRD 框架：`.claude/skills/prd-style/`（装机放入出厂默认版，属于项目、可自由修改；`prd-writer` 写 PRD 时读取）
- 记忆文件：`.claude/agent-memory/<role>/{MEMORY.md, notes.md}`
- 运行时状态：`.claude/workframe-state/`

**升级框架后请重启 Claude Code 会话**，确保最新 rules 和 hooks 生效。
