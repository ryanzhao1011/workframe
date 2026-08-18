# 项目目录结构规范

本文档定义一个 Workframe 项目的推荐目录结构。

- `project_scaffold.py` 建新项目 / 接入已有项目时按同一组模板初始化（已存在文件不覆盖）
- 项目维护者手动扩展时也应遵循

## 目录定位（一目了然版）

- **`projects/`** = **项目治理资产层**（看板 / 规格 / 问题 / 提案 / 变更日志 / eval / 归档）。由 core skill 与 hook 共同维护。**不**用于存放业务文件。
- **业务文件** = 放在**项目根目录**（与 `projects/` 同级），如 `src/` `tests/` 等，跟随项目自身的脚手架约定——框架不预建、也不接管。
- **`.claude/`** = Claude Code 与本框架的运行时层（agents / skills / rules / 记忆 / 运行状态）。
- **`logs/`** = hook 输出 + Librarian 快照。

## 通用结构

```
<project>/
├── CLAUDE.md                           ← 项目顶层说明 + 路由规则 + 订阅声明
├── .workframe-config.json              ← 框架接入配置
├── .claude/
│   ├── settings.json                   ← 订阅 core plugin（官方 CLI 生成）
│   ├── settings.local.json             ← 个人/本地覆盖（gitignore）
│   ├── agents/                         ← 项目级 agent override 或新增
│   ├── skills/                         ← 项目级 skill
│   ├── rules/
│   │   ├── <project-rules>.md          ← 项目专有 rules
│   │   ├── local/                      ← 项目专有 rules 的组织目录
│   │   └── workframe/core/             ← sync-rules.py 同步的 core rules（只读，不要手改）
│   ├── agent-memory/
│   │   ├── shared/                     ← 跨角色权威事实（agent 启动必读）
│   │   │   ├── MEMORY.md
│   │   │   └── notes.md
│   │   ├── <role>/MEMORY.md            ← 每个角色的高置信事实（≤8000 字符）
│   │   └── <role>/notes.md             ← 每个角色的低置信笔记缓冲区
│   └── workframe-state/                ← 框架运行时状态（hook + 脚本维护，不进 git）
├── projects/                           ← 项目治理资产层（详见 §projects/ 子目录说明）
│   ├── modules/                        ← ★ 功能模块树 <basic>/<sub>/（体系恒启用，模块按需创建）
│   │   └── overview.md                 ←   全产品架构总图（scaffold 建项目时落）
│   ├── board.yaml                      ← 任务看板（summary + tasks）
│   ├── specs/                          ← 内部规格 / 方案 / 策略 / 决策记录
│   │   └── overview.md
│   ├── issues/                         ← 结构化问题记录（**扁平结构**）
│   │   ├── TEMPLATES.md                ← SEC / BUG 模板（含归属字段）
│   │   └── {BUG|SEC}-{seq}.yaml        ← 具体 Issue 记录
│   ├── proposals/                      ← self-iteration 提案流转
│   │   ├── pending/                    ← 待审批
│   │   ├── applied/                    ← 已执行（含 verified 标记）
│   │   └── rejected/                   ← 驳回归档
│   ├── evals/                          ← 项目级 eval cases 骨架
│   │   ├── rules/
│   │   ├── skills/
│   │   └── agents/
│   ├── changelog.md                    ← 项目变更日志（librarian + self-iteration + rollback 共写）
│   └── archive/                        ← **手动**归档区（主 Claude 在用户确认后执行）
└── logs/                               ← Librarian 快照 + hook 输出
    ├── librarian-snapshots/{date}/...  ← Librarian 变更快照
    └── subagent-activity.log           ← SubagentStop hook 输出
```

## projects/ 子目录说明

### `projects/modules/` — 功能模块树

`modules/` 是 modules/ 体系下产品研发的一等公民组织轴。两层嵌套 `<basic>/<sub>/`，每个子模块下含 `requirements/`（文档→代码）+ `current-state/`（代码→文档）+ `submodule.yaml.code_paths` 反向桥梁。详见 `module-architecture.md`。

- 由 modules-system 类 skill 维护（`module-init` / `module-index-refresh` / `code-to-doc` / `migrate-to-modules` / `doc-graph-health` / `requirement-archiving` / `material-intake`）
- `project_scaffold.py` 建项目时自动建 `projects/modules/overview.md`（modules/ 体系永远启用），首次具体子模块由用户运行 `/core:module-init` 创建

### `projects/specs/` — 跨模块规范 / 方案 / 策略 / 决策记录

`specs/` 是项目内部规格的默认根目录。**不存放对外交付物**——交付物放项目顶层（`deliverables/` / `published/` / `src/` 等）。

内容形态：**缩小到跨模块规范** —— design-system / api-conventions / compliance / `plans/` 跨模块实施方案。
**单模块需求规格、技术方案、验收标准已下沉到 `projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/`**。

子目录组织：`design-system/` / `api-conventions/` / `compliance/` / `plans/` 四类**规范**子目录按需创建，外加装机放入的 `_meta/taxonomy.md`（tag 受控词表）。跨模块分析产物（`METRICS-` / `FEEDBACK-` / `COMP-{序号}.md`）直接落 specs 根级，不进这四个子目录。完整归属见 skill: `document-norms` §1.1，模板见 `templates/specs-overview-template.md`。

### `projects/issues/` — 扁平结构 + YAML 内归属字段

**约定**：

- 文件命名 `{TYPE}-{seq}.yaml`（例：`BUG-001.yaml` / `SEC-001.yaml`）
- 序号 = 扫描同类型现有 Issue 最大值 + 1，**全局唯一**（跨归属维度）
- **不**按模块分子目录（`projects/issues/<module>/...` 是反模式：会引发 ID 歧义、横切问题难安置、跨模块统计成本上升）
- 归属维度通过 YAML 内字段表达（`area` / `module` / `component` / `spec_ref` / `related_task` / `source`）

**归属字段在不同项目类型下的取值**（`area` 是中性字段，可适配非软件项目）：

| 字段 | 软件项目示例 | 非软件项目示例 |
|---|---|---|
| `area` | backend / frontend / infra | 内容平台 / 业务线 / 客户阶段 |
| `module` | auth / payment / billing | （视场景，可留空）|
| `component` | login-api / settle-job | （视场景，可留空）|
| `spec_ref` | `modules/auth/login/requirements/sso/main/prd.md` | 同 |
| `related_task` | `TASK-001` | 同 |
| `source` | qa / auto-update / user / monitoring / client | 同 |

完整字段定义见 `projects/issues/TEMPLATES.md`（由 `project_scaffold.py` 从 `templates/issues-templates-template.md` 初始化）。

### `projects/archive/` — 手动归档区

- **不是自动机制**：core skill 不会自动移动任务 / issue / spec 到此处
- **执行方式**：由主 Claude 在用户明确确认后归档
- **建议子结构**：`archive/{board,issues,specs}/`，但 scaffold 不预创建这三层（仅写 `.gitkeep`）

### `projects/proposals/` — self-iteration 提案流转

`pending/` → 用户选定候选并批准 → `applied/`（写 `applied_at` / `applied_option` / `verified: null`）→ 下次自迭代阶段 1(b) 闭环验证 → `verified: true | false`。全部驳回时 → `rejected/`（写 `rejected_at` / `rejection_reason`）。

详见 `plugins/core/skills/self-iteration/SKILL.md`。

### `projects/changelog.md` — 项目变更日志

由 `librarian` / `self-iteration` / `rollback` 三个 skill 共同维护。追加格式统一为 `## YYYY-MM-DD <type> <summary>`，`<type>` ∈ {`iteration` | `rollback` | `librarian` | `manual`}。

## 业务目录（**放项目顶层，框架不预建**）

业务文件与 `projects/` 同级，**不放在 `projects/` 下**——`projects/` 只放治理资产，业务文件归项目根目录管理。

**业务层一律不预建**——跟随项目自身的框架 / 脚手架社区约定。框架只加治理层，不接管也不改造已有工程结构。单模块需求/规范进 `projects/modules/<basic>/<sub>/`，跨模块规范进 `projects/specs/`，**不用顶层 `docs/specs/`**（与 skill: `document-norms` §1.4 "新方案没有顶层 docs/" 对齐）。

常见业务层布局参考（按用户实际脚手架决定）：

```
<project>/
├── src/                                ← Web 应用源代码（Next.js / Express 等）
├── miniprogram/                        ← 微信小程序（云开发结构）
├── cloudfunctions/                     ← 云函数
├── apps/web/                           ← Monorepo workspace
├── tests/                              ← 测试代码
└── deploy/                             ← 部署配置（如需）
```

## Git 策略

**默认进 git**（团队协作的治理资产 + 共享记忆）：

- 项目治理资产：`projects/board.yaml` / **`projects/modules/**`**（modules/ 体系下；含 overview / module.yaml / submodule.yaml / current-state / requirements / decisions / research / others 全部子树）/ `projects/specs/**` / `projects/issues/TEMPLATES.md` / `projects/issues/*.yaml` / `projects/proposals/**` / `projects/evals/**` / `projects/changelog.md`
- 共享记忆：`.claude/agent-memory/shared/MEMORY.md` / `.claude/agent-memory/shared/notes.md`
- 角色记忆：`.claude/agent-memory/<role>/MEMORY.md` / `.claude/agent-memory/<role>/notes.md`（默认进 git；按项目协作模式可调整）
- 记忆 sidecar：`.claude/workframe-state/memory-index.json`——**唯一进 git 的 workframe-state 文件**，理由见下方"默认不进 git"条目
- 记忆归档区：`.claude/memory-archive/**`（归档 = 移出 MEMORY.md 索引，不等于销毁；进 git 才谈得上"文件不灭"）

> **入库前先扫敏感内容**：记忆正文常年积累业务细节（客户名、未公开数据、内部话术），首次入库
> 等于把它们永久写进 git 历史，事后难以抹除。含此类内容的项目自行在 `.gitignore` 加回
> `.claude/agent-memory/` 即可——框架不替项目做这个判断。
- Core rules 镜像：`.claude/rules/workframe/core/**`（由 sync-rules.py 维护；进 git 便于审计同步状态）
- 业务数据：按项目自身约定决定（`src/` 等进；含 PII / 客户合同等敏感文件除外）

**默认不进 git**（运行时本地状态 + 敏感文件）：

- `.claude/workframe-state/**`：所有 hook / 脚本维护的运行时状态（events.jsonl / activity-state.json / skill-metrics.yaml / session-digest-latest.md / rollback-index.json）——属于**本地派生状态**，每端在自己的会话中独立产生与积累；不进 git。跨端协作依赖 git 中的治理资产（`projects/**`）+ 共享记忆（`agent-memory/**/MEMORY.md` / `notes.md`）+ rules / proposals / changelog，**不**依赖共享 workframe-state
  - **例外：`memory-index.json` 进 git**。它虽然住在这个目录下，却不是派生状态——`provenance`（user-decree / 模型沉淀）、`protected`、`created_at` 无法从记忆正文重算，丢了就永久丢了，`[纠正]` 条目会因此失去保护标记。记忆本身进 git 而它的保护元数据不进，等于跨端只同步了一半。
  - 写法是硬契约：`.claude/workframe-state/*` + `!.claude/workframe-state/memory-index.json`。**不能简写成 `.claude/workframe-state/`**——git 不会重新纳入被排除父目录下的文件，父目录整个被忽略时 `!` 例外被静默无视（已跟踪的项目看不出异样，新项目的 sidecar 直接失踪且零报错）。`gitignore-template` 与 `validate.py` 的 gitignore 闸共同锁住这个形态。
  - 多人协作时 sidecar 若产生合并冲突：**删本地重建/重算，不手工 merge JSON**。
- `logs/**`：所有日志（librarian-snapshots / subagent-activity.log）
- `.claude/settings.local.json`：个人本地覆盖
- 含 PII / API key 的业务文件
- 合同 / 客户敏感数据

**`projects/archive/`**：默认仅提交 `.gitkeep` 和明确归档的治理资产；不归档运行时日志。

## 关键设计原则

1. **订阅即获得通用能力**：订阅 core plugin 后，4 baseline agents + 36 skills + 4 rules + 11 段 hook 链路自动可用，项目本地 `.claude/agents/` 和 `.claude/skills/` 只放**项目特有**内容。
2. **同名覆盖机制**：项目本地的同名 agent 覆盖 plugin 版本（Claude Code 官方优先级规则）；skill 的同名覆盖**不可靠**（实测两个同名 skill 会并存），项目扩展 skill 用不同名字叠加。
3. **Rules 分层**：core rules 放 `.claude/rules/workframe/core/`（只读镜像）；项目专有 rules 放 `.claude/rules/*.md` 或 `.claude/rules/local/*.md`。
4. **记忆隔离**：`.claude/agent-memory/` 永远属于项目，不进 core plugin。
5. **运行时状态本地化**：`.claude/workframe-state/` 由 hook + 脚本维护，是每端本地独立产生的派生状态，不进 git（**`memory-index.json` 除外**——它是记忆的保护元数据，无法从正文重建，随记忆一起进 git，详见 §Git 策略）；跨端协作依赖 git 中的治理资产 / 共享记忆 / rules / proposals / changelog，**不**依赖共享 workframe-state。
6. **新建 vs 接入对齐**：两条路径都走 `project_scaffold.py` 的 `ensure_project_scaffold(project_dir)`，共用 `templates/` 下同一组模板，避免漂移；接入已有项目时不创建任何业务目录（框架不接管工程结构），仅初始化治理资产骨架。
7. **projects/ 不放业务文件**：`src/` / `tests/` 等放在项目顶层（与 `projects/` 同级），**不**放在 `projects/` 下。

## `.workframe-config.json` schema

`.workframe-config.json` 是项目接入框架的配置文件，由 `project_scaffold.py` 生成（merge 模式：框架字段刷新，用户字段保留）。

```json
{
  "project_name": "my-project",
  "framework_version": "1.0.0",
  "project_type": "product-work",
  "dormant_profile": "normal",
  "role_profile": "solo-pm"
}
```

| 字段 | 来源 | 说明 |
|---|---|---|
| `project_name` | 对话采集 | 展示名，用于 SessionStart 横幅、heartbeat-check；可与目录名不同 |
| `framework_version` | 自动写入 | 从 core 的 `plugin.json` 动态读取 |
| `framework_path` | 仅 clone 框架仓的入口会写 | marketplace 订阅场景没有仓根，**不写该字段**；读取方必须有缺省兜底 |
| `project_type` | scaffold 默认 | 固定 `product-work`（历史兼容字段，取值单一；保留仅为兼容既有消费方如 module-init，读取方不据此分支）|
| `dormant_profile` | scaffold 默认 | 4 档（见 §Dormant profiles），默认 `normal` |
| `role_profile` | 对话推断 | 3 档（见 §Role profile），默认 `software-team`；脚本读取必须默认兜底 |

**关键约束**：脚本读取 `.workframe-config.json` 时，对所有"可缺省"字段必须有默认兜底逻辑，**不强依赖**——已有项目可能缺字段。

## Role profile（角色路由偏好）

由 launcher 在创建对话中推断、写入 `.workframe-config.json` 的 `role_profile` 字段。**不**写入 `activity-state.json`（`role_profile` 是项目配置不是运行期状态）。

| Profile | 主力 | 适用场景 |
|---|---|---|
| `software-team` | pm / dev / qa | **默认**，有研发协作的产品项目 |
| `solo-pm` | pm | 个人 PM，无固定研发配合 |
| `ai-product` | prompt-eng / pm / dev | 项目目标明显以 AI/LLM/Prompt/Agent 能力为核心 |

**作用域**：`role_profile` **只**影响 `CLAUDE.md` 的"路由偏好"段渲染，作为路由**软提示**——不禁用任何 core agent，用户始终可 `@角色名` 直接调用。完整定义和路由偏好渲染文本见 `role-profile-catalog.md`。

修改 profile：直接改 `.workframe-config.json` 的 `role_profile` 字段并同步重新渲染 `CLAUDE.md` 的"路由偏好"段（手动改或主 Claude 协助）。

## Dormant profiles（活跃度档位）

写入 `.workframe-config.json` 与 `.claude/workframe-state/activity-state.json` 的 `dormant_profile` 字段，默认 `normal`；观测驱动调档，创建时不设问。

| Profile | 阈值 | 适用场景 |
|---|---|---|
| `high-frequency` | 30 天无 session → dormant | 主力产品项目 |
| `normal` | 60 天 | **默认**，一般迭代项目 |
| `low-frequency` | 90 天 | 慢节奏项目 |
| `archive` | 立即冻结 | 已结项保留只读 |

**dormant 行为**（由 heartbeat-check.py + session-start-prep.py 共同驱动）：
- 暂停所有 decay / promotion / self-iteration 触发
- 不阻止 Read 操作和新事件 append
- 重新打开时走 wake-up review：**这一个会话**只展示保留状态摘要、不自动跑维护（`wake_up_pending=true`）；可执行 `/core:maintenance-review` 当场处理积压，不做的话下一个会话自动恢复常规维护链路

修改 profile：`.workframe-config.json` 直接改；`activity-state.json` 的同步字段走代码通道
`maintenance_workorder.py --set-activity`（或 `--wake-done`）——那份文件由 5 个写入方共写，
手工整份重写会盖掉并发 hook 刚写进去的 `session_counter` / `pending_maintenance`。下次 SessionStart 生效。
