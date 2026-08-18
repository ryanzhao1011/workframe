# 核心概念

本文档解释 Workframe 依赖的几个关键 Claude Code 机制，以及本框架如何与它们协作。

## Claude Code 的三级资源加载

Claude Code 在启动会话时会从三个层级加载 agents / skills / hooks / rules：

| 层级 | 位置 | 作用域 | Git 是否提交 |
|---|---|---|---|
| **项目级** | `<project>/.claude/` | 当前项目 | ✅（团队共享） |
| **用户级** | `~/.claude/` | 所有项目 | 个人习惯 |
| **Plugin 级** | Plugin 的 `agents/`、`skills/`、`hooks/` | Plugin 启用时 | 随 plugin 分发 |

### 优先级规则（官方）

**项目级 > 用户级 > Plugin 级**

同名资源（如同名 agent `dev.md`）由高优先级层**完全覆盖**低优先级层，没有合并。

## 本框架的分工

本框架只占用 Plugin 级。两个其他层对框架是透明的：

### Plugin 级（本框架管理）
- 4 通用角色：`pm`, `dev`, `qa`, `prompt-eng`
- 36 skills：**13 domain skills**（task-management + 产品 6 + 研发 2 + qa 2 + prompt 2）+ **8 system/maintenance skills**（hooks 只写运行状态和维护信号；维护类 skills：`librarian` 的两条触发通道是 SessionStart 询问式开场卡与 `workframe-maintenance` 批处理命令（后者是 CLI 入口，**不是 skill**，不计入 8 个），`self-iteration` / `session-digest` 由 Claude best-effort 调用；三者均可经 `/core:maintenance-review` 进入流程；用户可直接调用 `/core:audit` / `/core:rollback` / `/core:memory-log` / `/core:onboard`）+ **8 docs/publishing skills**（`prd-writer` / `html-demo` / `screenshot` / 4 个 `obsidian-*` / `document-norms`，与平台无关；外部发布器 `feishu-publish` / `notion-publish` 等保持项目级）+ **7 modules-system skills**（`module-init` / `code-to-doc` / `module-index-refresh` / `migrate-to-modules` / `doc-graph-health` / `requirement-archiving` / `material-intake`）
- 4 通用 rules：`correction-detection`, `response-output`, `auto-update`, `agent-protocols`（领域无关）
- 11 段 hook 链路：SessionStart（含 memory-ask 询问式记忆整理触发）/ **Setup**（--maintenance 维护批处理工单聚合）/ UserPromptSubmit / **PostToolUse**（on Edit/Write/NotebookEdit 命中代码或 submodule.yaml → 反向索引 + stale 标记）/ Stop / **SubagentStart**（角色记忆注入：shared 全量 + role 按目录映射）/ SubagentStop / **StopFailure**（turn 级 API 失败审计）/ **ConfigChange**（配置变更审计）/ SessionEnd / **UserPromptExpansion**（用户直敲 `/skill-name` 时记 `skill_invoked`）

### modules/ 体系

modules/ 体系把"功能模块"作为产品研发的一等公民：`projects/modules/<basic>/<sub>/` 两层嵌套 + 每个子模块下 `requirements/`（文档→代码）+ `current-state/`（代码→文档）+ `submodule.yaml.code_paths`（治理层 ↔ 业务层胶水）。**体系恒启用**——骨架只落一份 `projects/modules/overview.md` 总图，首个具体模块由 `/core:module-init` 按需创建，不用不占地方。详见 `plugins/core/reference/module-architecture.md` 与 skill: `document-norms`。

### 项目级（项目自治）
- 业务数据（`projects/`、`knowledge-base/`、`crm/` 等）
- 项目专有 agent（同名可 override plugin 版）/ 项目专有 skill（**同名覆盖不可靠**——两个同名 skill 会并存，扩展用不同名 skill 叠加，如 `prd-style`）
- 项目专有 rules（放 `.claude/rules/` 根或 `.claude/rules/local/`）
- 记忆文件（`.claude/agent-memory/<role>/`）
- Plugin 运行时状态（`.claude/workframe-state/`）

### 用户级（个人跨项目空间）
**本框架显式不碰**。用户可以自己往 `~/.claude/` 放跨项目私有工具（个人角色档案、跨项目偏好等），框架的任何工具不会读写这层。

## 同名覆盖的典型用法

**场景**：你的某个项目里 `@pm` 的职责和通用版不太一样（例如要求 PM 兼任数据分析师）。

**做法**：在项目本地 `.claude/agents/pm.md` 放一份自定义版。Claude Code 启动时会先加载 plugin 的 pm，再看到项目级同名的，以项目级为准。Plugin 版完全不影响。

**建议**：project-level override 时**基于 plugin 版全量复制后修改**，不要只写增量——因为是完全覆盖，不是合并。你要保留原来的收尾协议、约束规则，然后改动你想改的部分。

## Rules 的特殊性

Rules 的加载（`.claude/rules/*.md`）是 Claude Code 的"上下文注入"机制：所有项目级 rule 文件都会被读入上下文，但加载器**默认不会**读取 plugin 内的 rules 目录。因此本框架的 4 份通用 rules 需要从 plugin 内**同步**到项目的 `.claude/rules/workframe/core/` 子目录——为什么放 plugin 内、三条同步路径、生效时序与故障排查，见专文 [rules-sync.md](./rules-sync.md)。

## Plugin 的自包含原则

**核心约束**：Plugin 安装后会被复制或缓存到 Claude Code 管理的位置（如 `~/.claude/plugins/cache/<plugin-name>/`）。Plugin 的 hooks 和 skill 运行时无法稳定引用 plugin 目录外的文件——那些文件不会跟 plugin 一起被拷贝。

因此本框架的所有 plugin 运行时依赖都放在 plugin 内：
- Hook 脚本：`plugins/core/scripts/*.py`
- Rules 源：`plugins/core/rules/core/*.md`
- launcher setup 与各 core skill 的知识源：`plugins/core/reference/*.md`
- 骨架与文档模板：`plugins/core/templates/*.md`

`tools/` 目录下的脚本是**仓库根工具**（`validate.py` 质量闸 + `sync-rules.py` 薄壳），给 clone 框架仓的贡献者用，不是 plugin 运行时依赖——marketplace 订阅的用户只有插件本体、没有仓根 `tools/`，所以运行时能力必须长在插件里。

## 记忆系统

每个角色有自己的记忆空间：

```
<project>/.claude/agent-memory/<role>/
├── MEMORY.md        # 高置信事实（role ≤8000 字符 / shared ≤4000），SubagentStart hook 自动注入
└── notes.md         # 低置信笔记缓冲区，无行数上限，按需读取
```

由 `librarian` skill 维护：notes → MEMORY 的非冲突提升可自动执行；超容量或低置信条目只生成降级候选，需 `/core:maintenance-review` 确认后执行；永不清理 `[纠正]` 标记的条目。

### 两套记忆，按「谁消费」分工

主 Claude 直做工作时不经过 subagent，因此它有自己的一层记忆：

| 记忆 | 消费者 | 装什么 | 注入时机 |
|---|---|---|---|
| **auto-memory**（Claude Code 官方记忆目录） | 主 Claude | 用户偏好、协作习惯、项目状态**指针** | 主会话启动，官方注入 |
| `.claude/agent-memory/<role>/` | 单个角色 | 该角色的执行经验 | 该 subagent 被调度时，SubagentStart hook 注入 |
| `.claude/agent-memory/shared/` | ≥2 个角色 | 跨角色权威事实（写入条件更严） | 每个 subagent 启动时全量注入 |

**落点判据是「未来谁要读它」，不是「这次谁做的」**——主 Claude 做 dev 域的活，经验该落 dev 域给未来的 @dev 读。业务知识（需求口径 / 方案结论）一律进 `projects/` 文档，记忆里至多留一行指针。完整判据见 CLAUDE.md 的「工种知识的落点判据」段与 core rule `agent-protocols` Step 2。

条目跨记忆域搬家时记 `memory_migrated` 事件，与同域内 notes→MEMORY 的 `memory_promoted` **分立不混用**：后者是消费方判断「这个域多久没消化 notes 了」的依据，混用会污染这个信号。

### 三账本对齐

记忆层有三份账，必须互相对得上：`MEMORY.md`（人读正文）↔ `memory-index.json`（sidecar：provenance / protected / created_at）↔ `events.jsonl`（变动流水）。
`plugins/core/eval-cases/memory-pipeline/scripts/assert_three_ledgers.py` 做四向互查，退出码即违例数——记忆改坏时它比人眼先发现。

**记忆默认进 git**（别和下一段的"不进 Plugin"混淆——那说的是跨项目共享，这说的是版本管理）：记忆是跨会话积累的资产，误删或改坏无法重建，进 git 才有回溯与多端同步。同进 git 的还有 `.claude/workframe-state/memory-index.json`——记忆的 sidecar，存 `provenance` / `protected` / `created_at`，`[纠正]` 条目的保护标记就在里面，同样无法从正文重算。它虽住在整体不进 git 的 `workframe-state/` 下，`.gitignore` 用 `.claude/workframe-state/*` + `!.claude/workframe-state/memory-index.json` 的成对写法单独放行（不能简写成 `.claude/workframe-state/`，那样 git 会静默无视例外行）。

> 记忆正文常年积累业务细节，首次入库等于永久写进 git 历史。含客户名 / 未公开数据的项目，自行在 `.gitignore` 加回 `.claude/agent-memory/`；框架不替项目做这个判断。

**为什么记忆不进 Plugin**：记忆是项目特定的经验沉淀，共享没有意义，反而会污染其他项目的上下文。跨项目通用的个人信息（如你的工作偏好）建议放在用户级 `~/.claude/`（官方天然支持），本框架不管理用户级。

## 任务看板

由 `task-management` skill 定义 schema，所有角色统一用：

```yaml
summary:
  total: N
  pending: N
  in_progress: N
  pending_qa: N       # 研发任务待 QA 验证
  completed: N
  blocked: N
  cancelled: N
  last_updated: "YYYY-MM-DD"
tasks:
  - id: "TASK-..."
    ...
```

研发任务有强制状态流转：`pending → in_progress → pending_qa → completed`，其中 `pending_qa → completed` 只能由 `@qa` 签发。详见 `plugins/core/skills/task-management/SKILL.md`。

## 总结一张图

```
你的项目 <project>/
├── CLAUDE.md                                   ← 项目顶层说明 + 订阅声明 + 路由偏好段
├── .workframe-config.json                      ← 项目名 + 创建期配置（project_type / dormant_profile / role_profile）
├── .claude/
│   ├── settings.json                           ← 订阅 core plugin
│   ├── agents/<role>.md                        ← (可选) 项目级 override/新增
│   ├── skills/prd-style/                       ← 项目 PRD 框架（装机实例化，随项目自由改）
│   ├── skills/<skill>/SKILL.md                 ← (可选) 其他项目专有 skill
│   ├── rules/
│   │   ├── <project-rule>.md                   ← 项目专有 rule
│   │   ├── local/                              ← (可选) 项目专有 rule 子目录
│   │   └── workframe/core/                     ← 框架同步的只读镜像（勿手改）
│   ├── agent-memory/<role>/{MEMORY,notes}.md   ← 项目独立记忆
│   └── workframe-state/                        ← hook 运行状态
├── projects/
│   ├── board.yaml                              ← 任务看板
│   ├── modules/                                ← 功能模块树 <basic>/<sub>/（体系恒启用，模块按需创建）
│   │   └── <basic>/<sub>/                       ←   含 requirements/<req_slug>/<sub_req_slug>/ + current-state/ + submodule.yaml
│   ├── specs/                                  ← 需求规格（modules/ 体系下缩小到跨模块规范：design-system / api-conventions / compliance / plans + _meta/taxonomy 词表）
│   └── issues/                                 ← Issue 记录（扁平 + 全局序号 BUG-*.yaml / SEC-*.yaml）
├── logs/                                       ← Librarian 快照 + hook 输出
└── <业务目录>/                                  ← 跟随你的实际业务形态，框架不预建
```

**你修改项目内任何文件 → 立即生效**
**框架侧修改 plugin → 下次重启会话后生效（rules 镜像由 SessionStart 自愈同步跟平，见 [rules-sync.md](./rules-sync.md)）**
