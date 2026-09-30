# 核心概念

本文档解释 Workframe 依赖的几个关键 Claude Code 机制，以及本框架如何与它们协作。框架是一份源、两扇门——Codex 侧的对应物（插件缓存、hook 信任门、项目层 `.codex/config.toml`）见 [setup-guide.md](./setup-guide.md) §Codex 门；下文按 Claude Code 讲。

## Claude Code 的三级资源加载

Claude Code 在启动会话时会从三个层级加载 agents / skills / hooks / rules：

| 层级 | 位置 | 作用域 | Git 是否提交 |
|---|---|---|---|
| **项目级** | `<project>/.claude/` | 当前项目 | ✅（团队共享） |
| **用户级** | `~/.claude/` | 所有项目 | 个人习惯 |
| **Plugin 级** | Plugin 的 `agents/`、`skills/`、`hooks/` | Plugin 启用时 | 随 plugin 分发 |

### 优先级规则（官方）

**项目级 > 用户级 > Plugin 级**

同名资源（如项目级与用户级各有一份 agent `dev.md`）由高优先级层**完全覆盖**低优先级层，没有合并。**Plugin 级的 agent 与 skill 带插件命名空间**（`core:pm`、`/core:code-review`），和项目级、用户级的同名文件不是同一个名字——它们不会被覆盖，而是并存（见下文 §项目级同名 agent：并存，不是覆盖）。

## 本框架的分工

本框架的角色、skills、hooks 与纪律源只放在 Plugin 级；项目级放项目自己的东西，用户级框架不放任何东西（装机时写的两处用户级配置见下文 §用户级）：

### Plugin 级（本框架管理）
- 4 通用角色：`pm`, `dev`, `qa`, `prompt-eng`
- 37 skills，分四类（完整名单）：
  - **13 domain skills**：`task-management`；产品 6 个 `requirement-analysis` / `feature-breakdown` / `acceptance-criteria` / `competitive-analysis` / `product-metrics-design` / `user-feedback-analysis`；研发 2 个 `technical-design` / `systematic-debugging`；qa 2 个 `test-case-design` / `code-review`；prompt 2 个 `prompt-design` / `prompt-evaluation`
  - **9 system/maintenance skills**：`librarian` / `self-iteration` / `session-digest` / `signal-intake` / `audit` / `rollback` / `memory-log` / `onboard` / `maintenance-review`。hooks 只写运行状态和维护信号；`librarian` 的两条触发通道是 SessionStart 询问式开场卡与 `workframe-maintenance` 批处理命令（后者是 CLI 入口，**不是 skill**，不计入这 9 个），`self-iteration` / `session-digest` 由 Claude best-effort 调用，三者均可经 `/core:maintenance-review` 进入流程；`signal-intake` 是信号入账的落盘工序（判定要写之后怎么写），由主会话调用；用户可直接调用 `/core:audit` / `/core:rollback` / `/core:memory-log` / `/core:onboard`
  - **8 docs/publishing skills**：`prd-writer` / `html-demo` / `screenshot` / 4 个 `obsidian-*`（`obsidian-doc-structure` / `obsidian-link-audit` / `obsidian-safe-write` / `obsidian-history-check`）/ `document-norms`，与平台无关；外部发布器 `feishu-publish` / `notion-publish` 等保持项目级
  - **7 modules-system skills**：`module-init` / `code-to-doc` / `module-index-refresh` / `migrate-to-modules` / `doc-graph-health` / `requirement-archiving` / `material-intake`
- 必载纪律：由 SessionStart / SubagentStart 直接注入上下文，源在 `plugins/core/context/{both,main,sub}/`（领域无关；项目目录里不落文件）
- 11 段 hook 链路：SessionStart（含 memory-ask 询问式记忆整理触发）/ **Setup**（--maintenance 维护批处理工单聚合）/ UserPromptSubmit / **PostToolUse**（on Edit/Write/NotebookEdit 命中代码或 submodule.yaml → 反向索引 + stale 标记）/ Stop / **SubagentStart**（角色记忆注入：shared 全量 + role 按目录映射）/ SubagentStop / **StopFailure**（turn 级 API 失败审计）/ **ConfigChange**（配置变更审计）/ SessionEnd / **UserPromptExpansion**（用户直敲 `/skill-name` 时记 `skill_invoked`）

### modules/ 体系

modules/ 体系把"功能模块"作为产品研发的一等公民：`projects/modules/<basic>/<sub>/` 两层嵌套 + 每个子模块下 `requirements/`（文档→代码）+ `current-state/`（代码→文档）+ `submodule.yaml.code_paths`（治理层 ↔ 业务层胶水）。**体系恒启用**——骨架只落一份 `projects/modules/overview.md` 总图，首个具体模块由 `/core:module-init` 按需创建，不用不占地方。详见 `plugins/core/reference/module-architecture.md` 与 skill: `document-norms`。

### 项目级（项目自治）
- 业务数据（`projects/`、`knowledge-base/`、`crm/` 等）
- 项目专有 agent（与 plugin 角色同名时是**并存**、不是覆盖，见下文 §项目级同名 agent：并存，不是覆盖）/ 项目专有 skill（**plugin skill 带命名空间、无法被项目同名 skill 覆盖**——项目 `/code-review` 与插件 `/core:code-review` 是两个标识、会并存（实测确认）；扩展 core skill 用不同名 skill 叠加避免歧义，如 `prd-style`）
- 项目自有判据（项目根 `AGENTS.md`，两扇门都读）与项目自己的 skill
- 记忆文件（`.workframe/agent-memory/<role>/`）
- Plugin 运行时状态（`.workframe/state/`）

### 用户级（个人跨项目空间）
**框架不在这层放任何 agents / skills / rules**，你自己往 `~/.claude/` 放的跨项目私有工具（个人角色档案、跨项目偏好等）框架也不改。

**例外是下面几处用户级配置**：

- Claude Code 的市场注册表 `~/.claude/plugins/known_marketplaces.json`——装机时补登 workframe 市场（已登记同一个源时一个字节不动）
- 这台机器装了 `codex` 时，Codex 的用户级 `config.toml`——装机时写本插件 hook 的信任、本项目的 trust，并把本插件的 `enabled` 改为 `false`（插件从此只在跑过 Codex 门的项目里加载）。**这一步代你信任本插件的全部 hook，而受信 hook 脱离沙盒执行**。门同时经 `codex plugin` 命令登记市场、装插件——市场登记与启用位写在同一份 `config.toml` 里，插件文件落在 `<CODEX_HOME>/plugins/cache/`——并在 `<CODEX_HOME>/.workframe/` 下写一份种信任记录（审计用）
- **升级之后**：Codex 会话启动时，本插件的种信任 hook 会把此刻未受信的全部本插件 hook（升级后变动过的，以及新版本新增、从没被信任过的）写成受信（同一份 `config.toml`，并更新上面那份种信任记录），在会话里明说信任了哪几条、下次会话生效，**不再问你**——接 Codex 门即视为同意这一点（经 launcher 装机时，确认页的 Codex 门那一行会写明这一点；直接敲 `workframe-door --codex` / `--backfill` 时，写入前的打印目前还没写这一点）。这份信任按 `CODEX_HOME` 共用（通常即整台机器）：同一个 `CODEX_HOME` 下任何一个接了 Codex 门的项目开会话都会补种，不想要就得关掉你接过 Codex 门的每个项目的门（见 [setup-guide.md](./setup-guide.md)「只用一门时怎么关另一门」）
- `/core:onboard` 只有在你选「当前用户全局」并二次确认后，才写 `~/.claude/settings.json`（见 [onboarding.md](./onboarding.md)）

前两处：经 launcher 装机时都在确认页的动作清单里逐项点名、确认前不写；直接在命令行跑 `workframe-door --codex` / `--backfill` / `--upgrade`（后者不是装机，但同样重写上面那份 `config.toml` 里的 hook 信任）时没有确认页——授权就是你亲手敲了这条命令（`--codex` 在写入之前会先打印要信任哪几条 hook、要改你用户配置里的哪一键，见 [setup-guide.md](./setup-guide.md) §Codex 门）。

## 项目级同名 agent：并存，不是覆盖

**场景**：你的某个项目里 `@pm` 的职责和通用版不太一样（例如要求 PM 兼任数据分析师）。

**做法**：在项目本地 `.claude/agents/pm.md` 放一份自定义版。

**实际行为**（Claude Code 2.1.285 实测）：plugin 的角色带命名空间，全名是 `core:pm`；项目这份叫 `pm`。两个名字不同，所以**不是覆盖而是并存**——会话的 agent 列表里 `core:pm` 与 `pm` 同时在。

- 你写 `@pm`、或主会话派 `pm`，用的是项目这份；派 `core:pm`，用的仍是插件那份
- 你没点名、只描述任务时，由主会话按两者的 description 挑一个。挑哪个是模型行为，不保证每次都选项目这份——要让它稳定，在 `AGENTS.md` 的项目级角色段写明这类活交给 `pm`、不用 `core:pm`
- 两者收到的子 agent 纪律注入与 `pm` 角色记忆相同（`SubagentStart` hook 按去掉命名空间后的角色名取记忆目录）
- Codex 门的角色由插件 `agents/*.md` 生成，不读 `.claude/agents/`；Codex 侧怎么定制见 [setup-guide.md](./setup-guide.md) §角色与项目配置

**建议**：自定义版**基于 plugin 版全量复制后修改**，不要只写增量——项目这份被派到时，它就是这个角色的全部定义，不会与插件版合并。你要保留原来的收尾协议、约束规则，然后改动你想改的部分。

## 必载纪律的特殊性

框架有一批**每次都得生效**的纪律（收口闸门、事件流协议、受保护资产清单、响应输出口径）。它们不靠模型「记得去读某个文件」，也不占用项目自己的 `CLAUDE.md`——**内容留在插件里，由 hook 在会话与子 agent 启动时直接投进上下文**。项目目录里因此不落任何框架纪律文件。源在哪、谁投递、生效时序与故障排查，见专文 [context-injection.md](./context-injection.md)。

## Plugin 的自包含原则

**核心约束**：Plugin 安装后会被复制或缓存到 Claude Code 管理的位置（如 `~/.claude/plugins/cache/<plugin-name>/`）。Plugin 的 hooks 和 skill 运行时无法稳定引用 plugin 目录外的文件——那些文件不会跟 plugin 一起被拷贝。

因此本框架的所有 plugin 运行时依赖都放在 plugin 内：
- Hook 脚本：`plugins/core/scripts/*.py`
- 必载纪律源：`plugins/core/context/{both,main,sub}/*.md`
- launcher setup 与各 core skill 的知识源：`plugins/core/reference/*.md`
- 骨架与文档模板：`plugins/core/templates/*.md`

`tools/` 目录下的脚本是**仓库根工具**（`validate.py` 质量闸与配套单测），给 clone 框架仓的贡献者用，不是 plugin 运行时依赖——marketplace 订阅的用户只有插件本体、没有仓根 `tools/`，所以运行时能力必须长在插件里。

## 两扇门：一份源、两种投递

框架只有一份源——hook 脚本、纪律片、角色、skills——Claude Code 与 Codex 各是一扇门。两门的差别全在**投递层**：

- **hook 清单**：Claude Code 读 `plugins/core/hooks/hooks.json`；Codex 读由它派生的 `hooks.codex.json`（去掉 Codex 没有的事件、多片合成一条、补 Windows 命令形态、追加两条 Codex 专有 hook 等）。派生规则只在 `scripts/_codex_hooks.py` 一处。
- **角色**：Claude Code 直接读插件的 `agents/*.md`；Codex 要的是项目里的 `.codex/roles/<role>.toml`，由装机器从同一份 `agents/*.md` 生成。
- **项目自有判据**：`AGENTS.md` 两门共读；Claude Code 侧经 `CLAUDE.md` 里的 `@AGENTS.md` 导入读到它（`CLAUDE.md` 另有订阅声明与快速入门，那些只有 Claude Code 读）。
- **运行态**：同一目录、同一份 `events.jsonl`，每行的 `harness` 字段标明来自哪扇门。

「两门跑的是同一套」靠两层验证：**静态对账**在 `tools/validate.py`（hook 清单按事件 × matcher × 脚本 × 固定参数对账、角色渲染与仓内样本逐字节对账）——验的是声明面，hook 在 Codex 侧的命中面不在其内；**动态对照**是 `tools/twin_parity_probe.py`，需要两门凭据、由维护者跑。装机、并存、切换与各自的能力边界见 `setup-guide.md` 的「Codex 门」一段。

## 记忆系统

每个角色有自己的记忆空间：

```
<project>/.workframe/agent-memory/<role>/
├── MEMORY.md        # 高置信事实（role ≤8000 字符 / shared ≤4000），SubagentStart hook 自动注入
└── notes.md         # 低置信笔记缓冲区，无行数上限，按需读取
```

由 `librarian` skill 维护：notes → MEMORY 的非冲突提升可自动执行；超容量或低置信条目只生成降级候选，需 `/core:maintenance-review` 确认后执行；永不清理 `[纠正]` 标记的条目。

### 两套记忆，按「谁消费」分工

主 Claude 直做工作时不经过 subagent，因此它有自己的一层记忆：

| 记忆 | 消费者 | 装什么 | 注入时机 |
|---|---|---|---|
| **auto-memory**（Claude Code 官方记忆目录） | 主 Claude | 用户偏好、协作习惯、项目状态**指针** | 主会话启动，官方注入 |
| `.workframe/agent-memory/<role>/` | 单个角色 | 该角色的执行经验 | 该 subagent 被调度时，SubagentStart hook 注入 |
| `.workframe/agent-memory/shared/` | ≥2 个角色 | 跨角色权威事实（写入条件更严） | 每个 subagent 启动时全量注入 |

**落点判据是「未来谁要读它」，不是「这次谁做的」**——主 Claude 做 dev 域的活，经验该落 dev 域给未来的 @dev 读。业务知识（需求口径 / 方案结论）一律进 `projects/` 文档，记忆里至多留一行指针。完整判据在每会话注入的必载纪律里（§Step 2 — 经验沉淀，源文件 [`20-protocol-common.md`](../plugins/core/context/both/20-protocol-common.md)；两套记忆分工表在同一批注入片的「两套记忆分工」一节，源文件 [`50-roles-and-flow.md`](../plugins/core/context/both/50-roles-and-flow.md)）。

条目跨记忆域搬家时记 `memory_migrated` 事件，与同域内 notes→MEMORY 的 `memory_promoted` **分立不混用**：后者是消费方判断「这个域多久没消化 notes 了」的依据，混用会污染这个信号。

### 三账本对齐

记忆层有三份账，必须互相对得上：`MEMORY.md`（人读正文）↔ `memory-index.json`（sidecar：provenance / protected / created_at）↔ `events.jsonl`（变动流水）。
`plugins/core/eval-cases/memory-pipeline/scripts/assert_three_ledgers.py` 做四向互查，退出码即违例数——记忆改坏时它比人眼先发现。

**记忆默认进 git**（别和下一段的"不进 Plugin"混淆——那说的是跨项目共享，这说的是版本管理）：记忆是跨会话积累的资产，误删或改坏无法重建，进 git 才有回溯与多端同步。同进 git 的还有 `.workframe/state/memory-index.json`——记忆的 sidecar，存 `provenance` / `protected` / `created_at`，`[纠正]` 条目的保护标记就在里面，同样无法从正文重算。它虽住在整体不进 git 的 `.workframe/state/` 下，`.gitignore` 用 `.workframe/state/*` + `!.workframe/state/memory-index.json` 的成对写法单独放行（不能简写成 `.workframe/state/`，那样 git 会静默无视例外行）。

> 记忆正文常年积累业务细节，首次入库等于永久写进 git 历史。含客户名 / 未公开数据的项目，自行在 `.gitignore` 加回 `.workframe/agent-memory/`；框架不替项目做这个判断。

**为什么记忆不进 Plugin**：记忆是项目特定的经验沉淀，共享没有意义，反而会污染其他项目的上下文。跨项目通用的个人信息（如你的工作偏好）建议放在用户级 `~/.claude/`（官方天然支持），本框架不往那里写记忆，也不管理你放在那里的内容。

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

研发任务有强制状态流转：`pending → in_progress → pending_qa → completed`，其中 `pending_qa → completed` 在角色侧只能由 `@qa` 签发。主 Claude 自签是另一条通道，由**四段闸门**（默认起点 → 通行证 → 地板 → 封顶）判定准入。签发档位从严到松是：**@qa 完整验证 → 轻签注 → 主 Claude 自签**；封顶命中时另交用户拍板。四段依次算：

- **默认起点**：恒为「@qa 完整验证」——没有任何独立视角看过的东西，默认就该被独立看一次
- **通行证**：一份覆盖**本轮改动**（上一个签发点到现在的 diff）的独立结论。它是唯一能让档位往下降的东西，没有就停在默认起点
- **地板**：按改动的爆炸半径算出的最低档，通行证再好也降不到它下面。爆炸半径是改动影响面的分级（1–4），由模型判、机器只读取值：档 4 降不下去，档 3 最低到轻签注，档 1–2 可到自签；没声明按最严处理
- **封顶**：命中六条否决项之一（结构化条目删除 / 发版动作 / 判定命题被推翻 / 产品决策 / 两个独立结论冲突 / 受保护资产的契约变更），一律交用户拍板，无论前三段算出什么

**自签档默认开启**（`.workframe-config.json` 的 `signoff.self_signoff_enabled` **缺键即 true**；要求更严的项目显式写 `false`）。详见 [`task-management` 的 SKILL.md](../plugins/core/skills/task-management/SKILL.md) 与必载纪律 §谁签发这次收口（源文件 [`10-closeout.md`](../plugins/core/context/both/10-closeout.md)）。

## 收口闸

前面几节讲的是「东西放在哪、谁能覆盖谁」。这一节讲**一轮改动怎么算结束**。

框架里大部分环节是「hook 提醒 → 模型自行处理 → 模型自行声明完成」——最后那一环没有机器验收。`module-close-check` 补的就是它：一条独立 CLI（`workframe-module-close-check`），不挂任何 hook，靠纪律在正确时点手动跑。

- **判定全是确定性的**：文件系统 + git，无模型裁量。任一 error 则 exit 1。
- **不改动任何被检对象**：写入只有它自己的计数日志——往状态目录的日志里追加一行，连带该日志的锁文件（`.lock`；锁超时时另落一份 spill 文件）。「纪律产物痕迹」那一项读这份日志算升级计数。
- **正确时点**：模块文档与账本写完之后、外层仓收口提交之前（代码仓应已提交——基准 hash 只有提交后才存在）。

它主要盯两类事实的一致性：

1. **「这份文档基于哪个 commit」有三个落点**——`current-state/*.md` 的 `source_ref`、`submodule.yaml` 的 `last_synced_ref`、子模块 `overview.md` 的「基准 commit」括注。三处必须同值，漏一处就是同一事实有了第二个来源，检查 3 当场比对报红。**另有两处随基准一起刷新、但检查 3 不看**：`submodule.yaml` 的 `last_synced_at`，以及 `current-state/*.md` 的 `verifier`（这次核对了哪几项、**不含**哪几项）。它们各自只有一个落点，没有第二处装同一个值可比，漏刷不会报红——靠纪律，代价是文档声称的核对范围停留在上一份代码上。
2. **「改动命中的模块是否已刷新」**——只要改的文件落在某个子模块的 `code_paths` 里就得刷，哪怕只改了注释（命中即刷）。改动不在任何模块清单内时不必刷。检查 4 按此判定。

账本检查（`[ledger]` 项）**默认开启**：装机时 `.workframe-config.json` 里就写好了 `close_check.ledger` 与 `required_fields`，账本文件 `projects/dev-log.md` 也一并生成，带一条以装机日为期的首条——所以新项目第一次跑它是绿的。不想记账就把那个键改成空壳 `"close_check": {}`（**改成空壳，不是删键**——删了下次重跑脚手架会写回来），该项随即整项跳过，不影响其余八项。

完整操作顺序见 [一轮活怎么干](./one-round.md)。

## 总结一张图

```
你的项目 <project>/
├── CLAUDE.md                                   ← 头部信息 + `@AGENTS.md` 导入 + 订阅声明 / 快速入门 / 框架指路
├── AGENTS.md                                   ← 项目是什么 + 项目自有判据 + 项目级角色 + 路由偏好 + 业务目录 + 委派授权（两扇门共用）
├── .workframe-config.json                      ← 项目名 + 创建期配置（project_type / dormant_profile / role_profile / close_check）
├── .agents/
│   └── skills/                                 ← 项目 skills 的真实源（两扇门共读）
│       ├── prd-style/                          ← 项目 PRD 框架（装机实例化，随项目自由改）
│       └── <skill>/SKILL.md                    ← (可选) 其他项目专有 skill
├── .claude/
│   ├── settings.json                           ← 订阅 core plugin（enabledPlugins + extraKnownMarketplaces 两项声明）
│   ├── skills                                  ← 指向 .agents/skills 的目录链接（.gitignore 忽略它）；上一版装的项目这里是真目录
│   ├── agents/<role>.md                        ← (可选) 项目级新增角色，或与 plugin 角色同名的自定义版（并存，见上文）
│   └── rules/                                  ← (可选) 项目自己的 rule：Claude Code 原生读取、Codex 不读；框架不再推荐这个落点，也不往里写
│       ├── <project-rule>.md                   ← 项目专有 rule
│       └── local/                              ← (可选) 项目专有 rule 子目录
├── .codex/                                     ← 装了 Codex 门才有：config.toml 的 managed 段注册 4 个角色 + roles/<role>.toml
├── .workframe/
│   ├── managed-files.json                      ← Codex 门的产物账本（进 git）
│   ├── agent-memory/<role>/{MEMORY,notes}.md   ← 项目独立记忆
│   └── state/                                  ← hook 运行状态（gitignore；memory-index.json 除外）
├── projects/
│   ├── board.yaml                              ← 任务看板
│   ├── dev-log.md                              ← 开发账本（每轮收口手写一条，收口检查 `[ledger]` 项读它）
│   ├── modules/                                ← 功能模块树 <basic>/<sub>/（体系恒启用，模块按需创建）
│   │   └── <basic>/<sub>/                       ←   含 requirements/<req_slug>/<sub_req_slug>/ + current-state/ + submodule.yaml
│   ├── specs/                                  ← 需求规格（modules/ 体系下缩小到跨模块规范：design-system / api-conventions / compliance / plans + _meta/taxonomy 词表）
│   └── issues/                                 ← Issue 记录（扁平 + 全局序号 BUG-*.yaml / SEC-*.yaml）
├── company-context/README.md                   ← 公司资料的起步目录（README 带敏感内容提示，进不进 git 由你定）
├── my-workspace/README.md                      ← 个人产出的起步目录（同上）
├── logs/                                       ← Librarian 快照 + hook 输出
└── <业务目录>/                                  ← 跟随你的实际业务形态（`src/` 等），业务目录框架不预建
```

**你修改项目内任何文件 → 立即生效**
**框架侧修改 plugin → 下次重启会话后生效（必载纪律由启动时的注入通道投递，见 [context-injection.md](./context-injection.md)）**
