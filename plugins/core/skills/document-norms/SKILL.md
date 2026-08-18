---
name: document-norms
description: 写或改任何 markdown 文档（含评审报告 / 实施清单 / 问题清单 / 方案等交付物）时的规范来源——写作质量分级与交付前自查、文档该放哪（归属矩阵）、frontmatter 与 `updated` 时间戳规则（含何时不 bump）、拆分改名前后的查漏 SOP、overview 三段制与索引同步、链接与资源引用、反模式。分 §1-§11，可只读单章。
when_to_use: |
  按「正在做什么」对号入座，只读对应章节、不整篇加载：
  1. 把分析 / 审查 / 调研结论落成正式文档，交付给研发、客户或其他角色前 → §11 写作纪律分级 + §11.4 交付前自查（最常漏，优先读）
  2. 新建任何文档、或拿不准该放哪个目录 → §1 归属矩阵
  3. 填 / 改 frontmatter、维护 `updated` 时间戳 → §2、§2.7
  4. 拆分 / 合并 / 重命名 / 删除已有文档 → §8 修改前后查漏 SOP（防落空引用）
  5. 建或改 overview、改完 status 要同步上级索引 → §3、§4
  6. 放 demo / 截图 / 临时产物拿不准位置 → §6；写跨文档链接或 current-state 行级锚点 → §5
  业务 skill（prd-writer / module-init / test-case-design / module-index-refresh / code-to-doc）
  按前置依赖 `skill: document-norms §X` 只读对应章节。
user-invocable: true
allowed-tools: [Read, Write, Edit, Grep, Glob]
---

# Document Norms 文档规范 Skill

## 定位

本 skill 是 **modules/ 体系下文档写作和管理的单一规范来源**，覆盖文档归属、frontmatter、overview、索引、SOP、反模式、写作质量分级 11 个章节。

**章节化引用模式**：业务 skill（prd-writer / module-init / test-case-design / module-index-refresh / code-to-doc / migrate-to-modules / 其他文档创作 skill）在 SKILL.md 顶部声明前置依赖 `skill: document-norms §X §Y`，按需读对应章节而非整 skill——本 skill 章节多、篇幅大，整读成本远高于按需读单章。

**适用范围**：

- modules/ 体系下的项目
- 其他形态项目选用，frontmatter 标准与 obsidian 增强部分通用

**不替代**：

- `obsidian-doc-structure` / `obsidian-link-audit` / `obsidian-safe-write` / `obsidian-history-check` 4 个 obsidian-* skill —— 本 skill 只在 §9 说明何时调用它们
- 项目配备的发布 skill（如 `feishu-publish`）—— 平台 specific 字段由发布 skill 自管

## 章节索引

| § | 章节 | 主要调用方 |
|---|---|---|
| §1 | 完整文档归属矩阵 | prd-writer / module-init / test-case-design / code-to-doc |
| §2 | frontmatter 字段标准（含 §2.7 `updated` 时间戳规范）| prd-writer / module-init / test-case-design / code-to-doc / 任何文档落盘 skill |
| §3 | 三段制 overview.md 通用约定 + 机器维护段边界格式 | module-init / module-index-refresh |
| §4 | 索引层级与同步规则 | module-init / module-index-refresh |
| §5 | 链接与资源引用 | prd-writer / 任何文档创作 skill |
| §6 | 资源位置（prototypes/ assets/ tmp/）| prd-writer |
| §7 | 操作分类（系统维护 vs 实质性产出）| librarian / self-iteration |
| §8 | 修改前/后查漏 SOP（含 L2 obsidian 反链查漏）| 任何文档修改 skill |
| §9 | obsidian 增强（何时调 4 个 obsidian-* skill）| obsidian-link-audit / obsidian-doc-structure |
| §10 | 反模式清单 | 全场景兜底 |
| §11 | 写作质量与精简纪律分级 | 任何文档创作/修改 skill |

---

## §1 完整文档归属矩阵

### 1.1 modules/ 体系下治理资产层（projects/）归属

| 内容类型 | 归属路径 | type | 创建 skill |
|---|---|---|---|
| 全产品架构总图 | `projects/modules/overview.md` | `overview` | `module-init` / `module-index-refresh` |
| 基础模块导览 | `projects/modules/<basic>/overview.md` | `overview` | 同上 |
| 基础模块 schema | `projects/modules/<basic>/module.yaml` | YAML | `module-init` |
| 跨子模块共享实体 | `projects/modules/<basic>/shared/<name>.md` | `concept` | 手动 |
| 子模块导览 | `projects/modules/<basic>/<sub>/overview.md` | `overview` | `module-init` / `module-index-refresh` |
| 子模块 schema | `projects/modules/<basic>/<sub>/submodule.yaml` | YAML | `module-init` |
| 代码现状（架构）| `<sub>/current-state/architecture.md` | `current-state` | `code-to-doc` |
| 代码现状（API）| `<sub>/current-state/api-surface.md` | `current-state` | `code-to-doc` |
| 代码现状（数据模型）| `<sub>/current-state/data-model.md` | `current-state` | `code-to-doc` |
| 代码现状（代码索引）| `<sub>/current-state/code-map.md` | `current-state` | `code-to-doc` |
| 需求清单导览 | `<sub>/requirements/overview.md` | `overview` | `module-init` / `module-index-refresh` |
| 需求 meta | `<sub>/requirements/<req_slug>/meta.yaml` | YAML | `module-init` |
| 需求总览（跨子需求）| `<sub>/requirements/<req_slug>/overview.md` | `overview` | `module-init` |
| PRD（按子需求；流程图内嵌其「业务流程与逻辑」章）| `<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md` | `prd` | `prd-writer` |
| HTML 原型 | `<sub>/requirements/<req_slug>/<sub_req_slug>/prototypes/` | `prototype` | `prd-writer` |
| 测试用例 | `<sub>/requirements/<req_slug>/<sub_req_slug>/test-cases/` | `test-case` | `test-case-design` |
| 评审与复盘产物（上线复盘 / 代码评审 / 方案评审）| `<sub>/requirements/<req_slug>/<sub_req_slug>/reviews/` | `review` | 手动 |
| 草稿需求（未立项）| `<sub>/requirements/_draft/<slug>.md` | `concept` | 手动 |
| 子模块决策记录 ADR | `<sub>/decisions/<YYYY-MM-DD>-<title>.md` | `decision` | 手动 |
| 子模块调研 | `<sub>/research/<topic>.md` | `research` | 手动 |
| 子模块杂项 | `<sub>/others/<name>.md` | `concept` | 手动 |
| 跨模块技术方案 | `projects/specs/plans/<YYYY-MM-DD>-<plan-name>.md` | `plan` | 手动 |
| 跨模块设计规范 | `projects/specs/{design-system,api-conventions,compliance}/` | `concept` | 手动 |
| **单模块**分析产物（指标 / 反馈 / 竞品）| `<sub>/research/{METRICS,FEEDBACK,COMP}-{序号}.md`（研究类归 research/，**不是 others/**——后者是杂项黑洞，见 §10 反模式）| `research` | 对应 PM skill |
| 跨模块分析产物（指标体系 / 反馈分析 / 竞品分析）| `projects/specs/{METRICS,FEEDBACK,COMP}-{序号}.md` | `research` / `concept` | `product-metrics-design` / `user-feedback-analysis` / `competitive-analysis` |
| Issues | `projects/issues/{TYPE}-{seq}.yaml`（扁平 + 全局序号；TYPE = BUG / SEC）| YAML schema（`projects/issues/TEMPLATES.md`）| 手动 / qa |
| Tasks | `projects/board.yaml` | YAML | `task-management` |

### 1.2 周边资产层归属（手动维护）

| 内容类型 | 归属路径 | 备注 |
|---|---|---|
| 公司政策 / 组织结构 / 内部 wiki 摘录 | `company-context/` | ⚠️ 含敏感内容；README 强提示，不预设 .gitignore |
| 个人评估 / 周报 / 演示稿 | `my-workspace/` | 同上 |

### 1.3 业务层（顶层）归属

| 形态 | 业务层路径 | code_paths 形式 |
|---|---|---|
| 微信小程序（云开发）| `miniprogram/` + `cloudfunctions/` | 项目根相对 `miniprogram/...` / `cloudfunctions/...` |
| 微信小程序（传统）| `pages/` 直接在项目根 | 项目根相对 `pages/...` |
| Next.js 单仓 | `app/` `pages/` 在项目根 | `app/...` |
| Next.js Monorepo | `apps/web/` | `apps/web/app/...` |
| 跨前后端 monorepo | `apps/web/` + `apps/api/` + `packages/shared/` | 数组多前缀 |
| 客户交付 | `deliverables/` | `deliverables/...` |
| 内容工作室 | `content/` `assets/` | `content/...` |

业务层目录跟着平台/框架社区约定走；code_paths **一律项目根相对路径**。

### 1.4 不允许出现的归属

- ❌ `projects/issues/<module>/` 子目录组织（issues 必须扁平 + 全局序号）
- ❌ 顶层 `docs/` 目录（modules/ 体系无顶层 docs/；老项目迁移时清空）
- ❌ PRD 写到 `src/`、代码写到 `projects/`、交付物放 `projects/`
- ❌ `_draft/` 永远草稿区（立项后必须改建目录）
- ❌ `others/` 黑洞（≥5 份时拆出新目录）

---

## §2 frontmatter 字段标准

### 2.1 通用必填字段

```yaml
---
type: prd                              # 类型，见 §2.3 取值表
status: in_progress                    # 见 §2.2 status 流转
owner_role: pm                         # pm | dev | qa | prompt-eng
updated: 2026-05-09T10:30:00+08:00     # ISO-8601 带时区，见 §2.7
module: profile/edit                   # ★ modules/ 体系下二段式 basic/sub
req_slug: avatar-cropper               # 可选，仅需求级文档用
description: ""                        # ★ 一句话摘要；prd / overview / current-state 必填，其余 type 推荐
related: []                            # 关联文档 wikilink 数组；wikilink 必须整体加引号，见下方说明
tags: []
---
```

**`description` 写作口径**（prd / overview / current-state 必填，其余 type 推荐）：

- 1-2 句、≤200 字，说清「这是什么 / 解决什么」
- 供各层 overview 索引摘要列（`module-index-refresh` 摘要提取链第二优先级）与检索预判使用；写不出 description 通常说明文档定位不清，先回答定位再落盘
- **从正文内容提炼，不从文件名或标题猜**
- **内容不足时只写状态事实，不发挥业务含义**——如空骨架 prd 写「资产包导览 + 骨架占位 + PRD 未启动」，不臆造该需求要解决什么
- 这类骨架/占位文档 `tags` 只打 `moc`，不硬套能力域标签
- 仅批量补齐存量 description / tags 等元数据（正文零变化）时**不更新 `updated`**（元数据回填例外，详见 §2.7）

**`related` 格式**：YAML 中 wikilink **必须整体加引号**——`related: ["[[projects/modules/agent话术/overview|agent话术]]"]`；不加引号时 `[[` 会被 YAML 解析为嵌套 flow 序列导致 parse 错误。

### 2.2 status 流转

**适用范围**：本表是 **markdown frontmatter `status`** 的合规枚举（PRD / spec / decision / current-state / overview 等文档）。**`*.yaml` 实体（submodule.yaml / module.yaml / meta.yaml / board.yaml）有自己的 status 词表**（如 `planning | active | done | dropped`），不受本表约束。

| status | 含义 | 允许的下一态 |
|---|---|---|
| `planning` | 规划中（仅 overview / requirement overview 类，对应"已立项但 v1 未启动"或子模块刚建空骨架）| `draft` / `in_progress` / `superseded` |
| `draft` | 草稿，未对齐 | `in_progress` / `superseded` |
| `in_progress` | 推进中 | `approved` / `superseded` |
| `approved` | 评审通过 | `implemented` / `superseded` |
| `implemented` | 已实现，等验证 | `shipped` / `deprecated` / `superseded` |
| `shipped` | 已上线（现状基线形态——归档入库的已上线需求用此终态，见 skill: `requirement-archiving` §核心形态约定） | `deprecated` / `superseded` |
| `deprecated` | 已弃用 | `superseded` |
| `superseded` | 已被新版本取代 | （终态）|

### 2.3 type 取值与路径对应

| type | 文档 | 路径 |
|---|---|---|
| `prd` | PRD（modules/ 体系下首选）| `requirements/<req_slug>/<sub_req_slug>/prd.md` |
| `spec` | 遗留需求规格（legacy 兼容枚举，仅存量文档；新需求一律 `prd`）| `projects/specs/<...>/spec.md`（历史遗留位置；迁移走 `migrate-to-modules`）|
| `flowchart` | 流程图（legacy；不建独立流程图文件——流程图直接写入 prd.md「业务流程与逻辑」章；存量文件保留不强迁）| 历史遗留位置 |
| `prototype` | 原型 | `prototypes/` |
| `test-case` | 测试用例 | `test-cases/` |
| `review` | 上线复盘 | `reviews/` |
| `current-state` | 反向同步状态 | `<sub>/current-state/` |
| `decision` | ADR | `<sub>/decisions/` |
| `research` | 调研 | `research/` |
| `overview` | 各层导览 | 各 `overview.md` |
| `plan` | 跨模块实施方案 | `projects/specs/plans/` |
| `concept` / `meeting` / `scratch` | 杂项 | 自由 |

### 2.4 按 type 选填字段

#### current-state 类（来源证据完整版）

```yaml
generator: code-to-doc-skill | manual | dev-paste
source_repo: https://gitlab.company.com/your-main-repo  # 跨仓时填
source_ref: abc123def | branch:main | tag:v2.3.1
source_paths: [...]                                    # glob 数组，整体范围
source_exported_at: 2026-05-09T10:00:00                # 跨仓 export 时间
verifier: dev-reviewer                                 # 谁核对过
confidence: high | medium | low
```

正文每条关键结论必须带行级 source path（详见 §5.3）。

#### requirements 内文档（PRD / prototype / test-case / review）

```yaml
req_slug: <req_slug>                   # 与父需求目录一致
sub_req_slug: <sub_req_slug>           # 与子需求目录一致；默认子需求填 main
```

> `version` 字段不使用；需求版本演进进 `prd.md` 正文「变更与决策记录」章，目录层级不表达版本（详见 `module-architecture.md` §4.3）。

#### review 类

```yaml
review_type: launch-retro | data-review | user-feedback | others
launched_at: 2026-05-10T00:00:00
```

#### overview 类

```yaml
overview_level: global | basic-module | sub-module | requirements | req
basic_name: <basic 目录名>              # 仅 overview_level=basic-module；供索引显示与反查
auto_sections:                         # 由 module-index-refresh 维护的段名
  - submodules-index
  - current-state-summary
  - requirements-index
```

#### test-case 类（用例细分类）

```yaml
case_type: happy_path | sad_path | boundary    # 用例细分类，独立于 §2.3 文档 type
case_id: TC-HP-001                             # 用例 ID（命名约定：TC-<HP|SP|BP>-<seq>）
ac_ref: AC-01                                  # 关联的验收标准 ID（编号零填充，与 acceptance-criteria 示例一致）
```

`type` 与 `case_type` 关系：`type: test-case` 是 §2.3 文档类型枚举（与 prd / spec / decision 等并列）；`case_type` 是 test-case 内部的细分类，独立字段，**不与 type 冲突**。详见 skill: `test-case-design` §2 用例矩阵生成。

### 2.5 平台 specific 字段

`feishu_synced_at` / `notion_page_id` 等 由对应平台 skill（feishu-publish / notion-publish）自定义和维护，**不进** framework `document-norms`。

### 2.6 module 字段二段式约束 + req_slug/sub_req_slug 引用契约

**module 字段**：

- modules/ 体系下：`module` **必填二段式** `basic/sub`（如 `profile/edit`）
- 尚未迁入 modules/ 的存量遗留文档：`module` 可单值或留空（迁移入库时补二段式）
- 横切到多模块：`affected_modules: ["a/b", "c/d"]` 数组

**req_slug + sub_req_slug 引用契约**：

- 新写入 frontmatter / yaml（PRD / test-case / board.yaml task / issue / 等）**只要挂需求，就必须二字段**：`req_slug: <req_slug>` + `sub_req_slug: <sub_req_slug>`——两个一起填，不能只填前者。
  **不挂需求的条目**（维护 / 看板 / 记忆整理类 task、非需求级 issue）两个字段都留空，不要为了「必填」硬编一个 slug（口径与 skill: `task-management` §modules 体系字段一致）
- **老引用兼容**：老 frontmatter / yaml 只含 `req_slug:`（无 `sub_req_slug:`），隐式视为 `sub_req_slug: main`；不强制迁移老 issue / task，新建用新格式
- **`spec_ref:` 继续允许完整路径**作为最精确引用，如 `modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md`；当 `req_slug` + `sub_req_slug` 二字段已能唯一定位时，`spec_ref` 可省略
- **取值约束**：`req_slug` / `sub_req_slug` 取值约束与模块 `name` 一致——允许英文 / 数字 / 短横线 / 下划线 / 中文，3 条 OS 硬约束（不含 `/` `\`、不含 Windows 禁字符 `< > : " | ? *`、**不含空格**（含中间空格——module_init 入口按此拒绝））；仅字段名因引用契约保留 `slug` 命名（详见 `module-architecture.md` §5.2）

### 2.7 `updated` 时间戳规范

适用于所有表征「文档最后修改时刻」的字段：markdown frontmatter `updated`、`meta.yaml` / `submodule.yaml` 等的 `updated_at`、HTML `<meta name="doc:updated">`。
**不适用**：`created_at`（ISO-8601 日期即可，无需时分秒）、用户业务数据中的时间戳（按业务自身约定）。

**格式**：ISO-8601 **带时区偏移**，`HH:mm:ss` 24h 制零填充。

```yaml
updated: 2026-05-19T12:51:12+08:00
```

框架默认时区 `+08:00`；跨时区项目在 `.claude/rules/local/` 声明覆盖。

**时间戳粒度按场景区分**：

| 场景 | 粒度 | 示例 |
|---|---|---|
| 实时落盘（PRD / spec / 设计文档 / current-state / 提案等） | 实时时间戳 | `2026-05-19T12:51:12+08:00` |
| 归档批次（一次性批量归档 / 重命名搬迁，无需溯源到具体时刻） | 按日粒度 | `2026-05-19T00:00:00+08:00` |
| 纯元数据回填（仅补 description / tags 等，**正文零变化**） | **不更新**，保持原值 | 原值原样保留 |

#### status 终态翻转必须同步 `updated_at`

把**需求的 `meta.yaml`** 的 `status` 改成**终态**（`done` / `dropped`）时，
**同一次编辑里必须把 `updated_at` 一起 bump 到当前时刻**。

**只约束 `meta.yaml`**：`submodule.yaml` / `module.yaml` 至今没有 `updated_at` 字段，
也没有消费方需要它——不为「形式统一」给它们引入一个无人读的新字段（那只会多一个漂移源）。

理由是有下游消费方：`module-index-refresh` 的 requirements-by-status 段直接把
`meta.yaml.updated_at` 当作「完成时间 / 放弃时间」列渲染——只改 status 不动 updated_at，
那一列显示的就是需求的**创建日期**，读表的人会以为它当天就完结了。

这条纪律一度只被索引 skill 引用、却从没在任何地方定义过。非终态之间的流转
（如 `planning → active`）按上表常规规则处理即可。

**判断标准**：

- 写完立刻落盘 → 实时
- 多个文档共享同一归档动作（如 9 份 tmp/ 资产一次性归档到 main/）→ 可用 `T00:00:00+08:00`
- 只动 frontmatter 元数据、正文零变化 → 不更新（保护文档新鲜度信号供 stale 巡检）；但改 `status` 等语义字段、或任何正文改动 → 仍按实时
- 正文**纯链接化**（现有文字原样包 `[[wikilink|原文字]]`，展示文字与语义零变化）→ 视同元数据回填，不更新；但链接化同时新增/改写了句子 → 按正文改动实时 bump
- 模糊时 → 倾向实时

**反模式**：实时落盘文档写 `T00:00:00`（无法溯源）；归档批次写 `T00:00:00` 但不带时区；用无时区的本地时间。

**取实时时间戳**：

```powershell
(Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")                              # PowerShell
```
```bash
date +"%Y-%m-%dT%H:%M:%S%z" | sed 's/\([0-9][0-9]\)$/:\1/'                 # Bash (mingw/WSL)
```
```python
datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')   # Python
```

---

## §3 三段制 overview.md 通用约定

### 3.1 三段结构

每层 overview.md 都用三段制：

```markdown
---
type: overview
overview_level: sub-module
... (其他 frontmatter)
---

## Positioning（人写）

[本层定位、边界、归属、命名缘由——稳定，不随机器维护刷新]

## 中间内容（按层级有差异）

[architecture / current-state-summary / 总体架构图等]

<!-- WORKFRAME:AUTO-INDEX:START -->
> ⚙️ 此段由 `module-index-refresh` 自动维护，请勿手动编辑。

| 子模块 | 状态 | 摘要 |
|---|---|---|
| ... | ... | ... |

<!-- WORKFRAME:AUTO-INDEX:END -->
```

### 3.2 各层中间内容差异

| 层级 | 文件 | positioning | 中间内容 | 机器维护索引段 |
|---|---|---|---|---|
| 全局 | `modules/overview.md` | 产品定位 | 总体架构图（mermaid 推荐）| basic-modules-index（**只放摘要+链接**）|
| 基础模块 | `<basic>/overview.md` | 基础模块定位 | architecture | submodules-index |
| 子模块 | `<sub>/overview.md` | 子模块定位 | current-state-summary（不重复 current-state/ 全量内容）| requirements-index |
| 子模块需求清单 | `<sub>/requirements/overview.md` | 省略（一句话）| 按状态分组的需求清单 | 全文（无人写部分）|
| 需求 | `<req_slug>/overview.md` | 跨子需求总览 | 省略 | sub-requirements-index |

### 3.3 机器维护段边界格式（强制）

```markdown
<!-- WORKFRAME:AUTO-INDEX:START -->
> ⚙️ 此段由 `module-index-refresh` 自动维护，请勿手动编辑。

[内容]

<!-- WORKFRAME:AUTO-INDEX:END -->
```

**规则**（两种语法二选一，**不可混用**）：

- **匿名段**（推荐用于单段文件，如 basic-module overview / requirement overview / 全局 overview）：
  - `<!-- WORKFRAME:AUTO-INDEX:START -->` 单独一行
  - 与 `<!-- WORKFRAME:AUTO-INDEX:END -->` 配对
- **命名段**（必须用于一个文件含多段的场景，如 sub-module overview 同时含 `current-state-summary` + `requirements-index`）：
  - `<!-- WORKFRAME:AUTO-INDEX:START:<name> -->` 单独一行（`<name>` 与 frontmatter `auto_sections` 列表中的某项一致）
  - **必须**与同名 `<!-- WORKFRAME:AUTO-INDEX:END:<name> -->` 配对（不能配匿名 END）
  - 同一文件多段时**所有段一律用命名形式**——一旦混用匿名 + 命名，replace regex 会贪婪跨段吞数据
- 紧随一行警告 `> ⚙️ 此段由 ... 自动维护`
- `module-index-refresh` skill 严格只重写 START/END 之间内容；段外人写部分（positioning + 中间内容）零接触
- regex 实现：`<!-- WORKFRAME:AUTO-INDEX:START(:<name>)? -->[\s\S]*?<!-- WORKFRAME:AUTO-INDEX:END\1 -->`（`\1` 反向引用保证 START/END 对称，匿名段反向引用为空字符串自动匹配匿名 END）

### 3.4 全局 overview "摘要+下沉"原则

`projects/modules/overview.md` 的机器维护段在 200 子模块规模下仍要保持可读：

- **只放基础模块级摘要 + 链接**（一行一个基础模块）
- 子模块详情**不要展开**到全局 overview，保持在 `<basic>/overview.md` 的 submodules-index 段
- 摘要长度建议 ≤80 字符，超出截断

---

## §4 索引层级与同步规则

### 4.1 三层索引层级

| 层 | 索引文件 | 内容 | 维护方 |
|---|---|---|---|
| 全局 | `modules/overview.md` 机器维护段 | 基础模块摘要+链接 | `module-index-refresh` |
| 基础 | `<basic>/overview.md` 机器维护段 | 子模块表 | 同上 |
| 子模块 | `<sub>/overview.md` + `<sub>/requirements/overview.md` 机器维护段 | requirements / current-state 摘要 | 同上 |

### 4.2 触发同步的场景

| 触发源 | 同步范围 | 触发方 |
|---|---|---|
| `module-init` 创建子模块 / 需求 | 该子模块所在路径所有上级 overview | skill 内显式调用 |
| 修改 `submodule.yaml.status` / `meta.yaml.status` | 该子模块所在路径所有上级 overview | 手动调 `module-index-refresh` |
| **`code-to-doc` 完成 current-state/ 写入** | 该子模块 `<sub>/overview.md` 的 `current-state-summary` 段（基于 4 个 current-state 文件的 frontmatter + 首段提炼摘要）| `code-to-doc` skill 内显式调用 |
| 大规模迁移 | 全量递归 | `migrate-to-modules` skill 内 |
| 用户主动 | 全量或路径限定 | `/core:module-index-refresh` |

### 4.3 不触发自动同步的场景

- 修改 frontmatter `updated` 字段（仅 timestamp 刷新，不影响索引内容）
- 修改 PRD / test-case 正文内容（不影响参与索引段提炼的 frontmatter 字段时不触发）
- 修改 `<sub>/decisions/` / `<sub>/research/` / `<sub>/others/` 下文件（这些目录不在索引清单内）

> **会触发**的需求层场景（按字段位置分流）：
> - 父需求 `<req_slug>/meta.yaml.status` 变更 → 走 §4.2 的 `meta.yaml.status` 路径（影响 requirements-by-status 段）
> - 子需求 `<sub_req_slug>/prd.md` frontmatter `status` / `owner_role` 变更 → 触发 `<basic>/<sub>/requirements/<req_slug>` 路径增量刷新（sub-requirements-index 段从 prd.md frontmatter 提炼，详见 `module-index-refresh` §Step 2 数据源表）
> - 新增 / 删除 `<sub_req_slug>/` 目录或其中 `prd.md` → 同上触发

> **注意**：`current-state/` 内容**会**触发 `<sub>/overview.md` 的 `current-state-summary` 段刷新（详见 §4.2 表第 3 行）；本节"不触发"指的是更纤细的 frontmatter 刷新，不包括 current-state 内容写入。`code-to-doc` skill Step 6 会主动调 `module-index-refresh` 限定路径同步。

### 4.4 同步原则

- 增量优先：能只重建一个子模块的索引段就不全量重建
- 只触发受影响的层（如改 `<basic>/<sub>/submodule.yaml` → 只重建 `<basic>/overview.md` 和 `modules/overview.md`，不动其他 basic）
- 索引段产物必须可 diff 复现（同输入 → 同输出，便于 review）

---

## §5 链接与资源引用

### 5.1 文档间链接

优先使用相对路径或 obsidian wikilink：

```markdown
✅ [profile / edit submodule](../../profile/edit/overview.md)
✅ [[profile/edit/overview]]                  # obsidian wikilink
❌ [edit](C:\Users\...)                        # 绝对路径
❌ [edit](https://github.com/.../blob/main/...)  # 跨仓库前先确认是否需要 source_repo
```

frontmatter `related:` 数组中使用 wikilink 时必须整体加引号（YAML 语法约束），格式见 §2.1。示例性占位路径（如模板说明文字中的 `<新-req_slug>/overview`）用行内代码书写，**不要**包 `[[ ]]`——否则会被 Obsidian / 链接审计当成真实 wikilink 产生假信号。

### 5.2 引用 framework skill / rule 的方式

不写物理路径；统一用 name + § anchor：

| ❌ 旧 | ✅ 新 |
|---|---|
| "详见 `.claude/rules/workframe/core/document-structure.md`" | "详见 skill: `document-norms` §1" |
| "见 `${CLAUDE_PLUGIN_ROOT}/skills/obsidian-doc-structure/SKILL.md`" | "见 skill: `obsidian-doc-structure`" |
| "参考 `<插件根>/skills/document-norms/SKILL.md` §3" | "参考 skill: `document-norms` §3" |

理由：path 解耦，plugin install 后物理路径会变（cache 路径），name 引用始终有效。

### 5.3 current-state/ 正文 source path（强制）

current-state/ 下的 `architecture.md` / `api-surface.md` / `data-model.md` / `code-map.md`，**正文每条关键结论必须带行级 source path**：

```markdown
## 用户登录流程

入口：`miniprogram/pages/login/index.js:23`
→ 验证：`cloudfunctions/auth/verify/index.js:45-67`
→ 写库：`cloudfunctions/auth/store/index.js:12`

## 数据模型 user_session

表定义：`cloudfunctions/auth/store/schema.sql:1-15`
索引：`(user_id, expires_at)` desc
```

理由：
- LLM 解析结果可能漂移；source path 让用户/dev 可快速 verify
- 反向同步过期时 PM 能直接定位代码源头追问
- 是 frontmatter `source_paths` 字段（整体范围 glob）的正文延伸（具体行级 anchor）

### 5.4 跨仓引用

跨仓引用代码（如公司主代码仓库）必须 frontmatter 明确：

```yaml
source_repo: https://gitlab.company.com/your-main-repo
source_ref: tag:v2.3.1                        # 或 branch:main / commit:abc123
source_exported_at: 2026-05-09T10:00:00
```

正文 source path 仍写仓内相对路径（不写绝对 URL）；用户结合 frontmatter 的 source_repo + ref 可定位。

---

## §6 资源位置（prototypes / assets / tmp）

### 6.1 各类资源归属

| 资源类型 | 归属路径 | 备注 |
|---|---|---|
| HTML 原型 | `<sub>/requirements/<req_slug>/<sub_req_slug>/prototypes/` | 由 `html-demo` 归档（demo 先行）或 `prd-writer` S5 产出（展示型）；只放 HTML 自身资源（CSS/JS/字体/SVG），**不放截图** |
| HTML 原型截图（长期引用）| `<sub>/requirements/<req_slug>/<sub_req_slug>/assets/` | 由 `screenshot` skill 输出到 `tmp/`，PRD 引用时移到此处（详见 §6.2） |
| 流程图源文件 | Mermaid 直接写入 `prd.md`「业务流程与逻辑」章（渲染 PNG 进同子需求 `assets/`） | draw.io / figma 等外部源链接进 frontmatter `related` |
| PRD / spec 配图 | 同子需求目录下 `assets/` 子目录 | 跟子需求走 |
| 全局共享配图 | `projects/specs/{design-system,api-conventions,plans}/assets/` | 跨需求复用 |
| 临时文件 | `<sub>/others/tmp/` 或项目根 `.tmp/` | `.gitignore` 排除 |
| **项目根 `tmp/`** | 项目根 `tmp/`（骨架产物，`.gitignore` 必含此条，doctor 会查） | 跨模块的加工区：HTML demo 就地迭代、待归档资产暂存、脚本中间产物。**任务结束即清理**，不是长期存放点；要留下来的产物按上面几行迁到正式落点 |
| 截图临时区 | `tmp/screenshots/<task_id>/` | screenshot skill 默认输出；调用方自清（详见 §6.2） |

### 6.2 prototypes 子目录约定

```
prototypes/
├── index.html                          # 主入口（推荐）
├── pages/                              # 多页原型
├── assets/{img,css,js}/                # 原型 HTML 自身资源（CSS/JS/字体/SVG 等）
└── screenshot-config.json              # screenshot skill 配置（prd-writer S5 / html-demo 按需截图生成）
```

`prototypes/` 只放 HTML 原型自身静态资源；**截图归档不在这里**。

**截图实际归档流程**（与 prd-writer html-prototype.md §S5.3 / html-demo 按需截图 / screenshot SKILL.md §9 对齐）：

1. `screenshot` skill 默认输出到 `tmp/screenshots/<task_id>/`（项目级临时区，不进 git）
2. 调用方判断用途：
   - **PRD 长期引用** → 移到 `<iteration-dir>/assets/`（与 `prd.md` 平级，进 git）
   - **仅作外部发布插图、不引用** → 留在 `tmp/screenshots/<task_id>/`，发布完后由调用方手动清理
3. SessionEnd hook **存在**（执行 events flush + digest + GC），但**不清理 `tmp/screenshots/`**——调用方必须自己清

### 6.3 不放在子需求目录下的内容

- 跨需求复用的设计语言文件 → `projects/specs/design-system/`
- 全局通用 API schema → `projects/specs/api-conventions/`
- 项目全局图标库 → `projects/specs/design-system/assets/icons/`

---

## §7 操作分类（系统维护 vs 实质性产出）

为 librarian / self-iteration / audit 区分"操作权重"，按以下分类：

### 7.1 系统维护操作（不计入产出统计）

- 重建机器维护段（`module-index-refresh`）
- 反向索引重建（`code-paths-index.json`）
- frontmatter `updated` 时间刷新
- broken link 修复
- formatter / linter 自动整理
- rules 同步（`sync-rules.py`）

### 7.2 实质性产出操作（计入 librarian / metrics 统计）

- 创建 / 修改 PRD / spec / 测试用例 / 复盘
- 创建 / 修改 ADR
- 编写 / 更新 current-state/ 解析结论
- 创建 / 修改 module / submodule schema
- 创建 / 修改 issues / tasks 业务字段（不含 status 自动流转）

### 7.3 灰色区操作

- positioning 段大改（手写部分）→ 实质性
- 索引段重排（仅排序未变更内容）→ 维护
- 修复反模式（如把误存到 root 的 PRD 移到正确路径）→ 实质性

灰色区由产生方 skill 自我判断；判断不准时按"实质性"计入。

---

## §8 修改前/后查漏 SOP

### 8.1 修改前查漏（防漂移）

```
Step 1: 确认归属
  - 查 §1 文档归属矩阵 → 确认目标路径正确
  - 查 §2 frontmatter 标准 → 确认必填字段
  - 跨模块写入时查 §3 三段制 → 确认有无 overview 同步需求

Step 2: 查现有内容
  - 同名/相似文档已存在？→ 优先编辑而非新建
  - 是否有上级 overview 已索引？→ 修改后必须同步索引段

Step 3: L2 obsidian 反链查漏
  - 改前调用 `obsidian-link-audit` 查 backlinks
  - 修改可能影响这些反链文档的描述/链接
```

### 8.2 修改后查漏

```
Step 1: 上级 overview 同步
  - 改 status / 元数据 → 调 `module-index-refresh` 限定路径

Step 2: 反向索引同步
  - 改 submodule.yaml.code_paths → PostToolUse 自动重建（无需手动）
  - 损坏报警时手动跑 `python "$(cat .claude/workframe-state/plugin-root.txt)/scripts/check-stale-modules.py" rebuild-index` 全量重建

Step 3: L2 broken link 检查
  - 大改文件名/路径 → 调 `obsidian-link-audit` 全仓 broken-link 扫描
  - 修复或更新引用方

Step 4: frontmatter updated
  - 按 §2.7 刷新 `updated`（正文有改动 → 实时带时区时间戳；
    纯元数据回填 / 纯链接化 → 保持原值不动）
```

### 8.3 删除/重命名 SOP（强约束）

删除/重命名前必须先 grep 引用：

```bash
# Step 1：grep 全仓引用
grep -rln "<file-or-keyword>" .claude/ projects/ company-context/ my-workspace/

# Step 2：审查结果
# - 0 引用 → 安全删除/重命名
# - 有引用 → 改引用为新路径或 skill name 后再删

# Step 3：删除/重命名

# Step 4：再次 grep 验证 0 引用
```

特别注意：删除整个目录（如 老项目顶层 docs/）必须先按本 §8.3 完整 SOP 走 4 子步（grep 找全引用 / 备份到 `_legacy/<old-path>/` / 引用替换 0 残留 / 二次 grep 验证）；具体执行交给 skill: `migrate-to-modules` 工作流 Step 4。

---

## §9 obsidian 增强（何时调 4 个 obsidian-* skill）

本 skill **不替代** obsidian-* 4 个 skill，只说明何时调用：

### 9.1 触发表

| 场景 | 调用 skill | 工具优势 |
|---|---|---|
| 只想先看 outline / properties / wordcount / tags / aliases | `obsidian-doc-structure` | CLI 比 Read 快、不污染上下文 |
| 改前/后查 backlinks / unresolved / broken-link | `obsidian-link-audit` | 反链查漏 / broken link 报警 |
| 创建/写文档（自动维护 frontmatter `updated`、规避 callout / dataview / block ref 与平台兼容性问题） | `obsidian-safe-write` | 安全 frontmatter 维护 |
| 历史版本 / 修复 git 上的旧版本 | `obsidian-history-check` | git 历史查询 |

### 9.2 不可用降级

CLI 不可用时全部 fallback 到 `Read` / `Grep` / `Edit` 直接操作，本 skill 的所有规范仍适用。

### 9.3 与 document-norms 章节呼应

- §8.1 Step 3 "L2 obsidian 反链查漏" → `obsidian-link-audit`
- §2 frontmatter `updated` 维护 → `obsidian-safe-write`
- §1 查现有归属时先看 outline → `obsidian-doc-structure`

---

## §10 反模式清单

> 本节是**各章红线的汇总索引** —— 条目在对应章节有完整口径，此处聚合便于交付前一次扫完。
> 与各章内容重复是设计使然（多个 skill 以「完整反模式见 §10」引用本节），**不要按冗余删除**。

### 10.1 归属反模式（完整口径见 §1）

- ❌ PRD 写到 `src/`、代码写到 `projects/`、交付物放 `projects/`
- ❌ 草率新建文件而不查归属（应先调本 skill §1）
- ❌ `projects/issues/<module>/` 子目录组织
- ❌ 在新方案里保留顶层 `docs/` 目录
- ❌ `_draft/` 滥用为永远草稿区（立项后必须改建目录）
- ❌ `others/` 变成黑洞（≥5 份时拆出新目录）

### 10.2 引用与路径反模式（完整口径见 §5）

- ❌ rule / skill 之间用绝对路径互引（应 `skill: <name> §X`）
- ❌ `${CLAUDE_PLUGIN_ROOT}/...` 物理路径出现在 skill 引用中
- ❌ `code_paths` 写小程序根相对路径（应项目根相对，含 `miniprogram/` 前缀）

### 10.3 frontmatter 反模式（完整口径见 §2）

- ❌ 自造 status / severity / type 取值（应在 §2.2 / §2.3 范围）
- ❌ 外部平台相关字段进 framework `document-norms` skill（属于项目配备的发布 skill，如 `feishu-publish`）
- ❌ `tech_stack` 单值表达 monorepo（应数组对象，详见插件根 `reference/module-architecture.md` §5 submodule.yaml schema）
- ❌ current-state/ 正文不带 source path 行级 anchor

### 10.4 索引与机器维护段反模式（完整口径见 §3 / §4）

- ❌ **手动编辑 overview 的机器维护段（HTML 注释 START/END 之间区域）**
- ❌ 反向索引手工编辑（应让 PostToolUse 自动重建或 fallback 全量重建）
- ❌ 全局 overview 展开子模块详情（应只放基础模块摘要+链接）

### 10.5 issues / tasks 字段反模式（完整口径见 §1.1 / §2.6）

- ❌ issues 字段全替换现有 6 字段（应叠加保留，兼容存量 issue 结构）
- ❌ modules/ 体系下 `module` 字段填单段（必须二段式 `basic/sub`）
- ❌ 老 issue 强制迁移到新格式（老的不动，新建用新格式）

### 10.6 调用模式反模式

- ❌ 业务方整读 document-norms 全文（应只读对应 § anchor）
- ❌ 业务 skill 不声明 `前置：document-norms §X §Y`（漏触发降低产出合规率）
- ❌ 用 hook 校验 + 自动修复实现文档规范的"100% 触发"（设计决策保持极简：skill 提供规范，CLAUDE.md / 项目 `.claude/rules/local/` 提供触发指针，不做强校验与自动改写）

### 10.7 删除/重命名反模式（完整口径见 §8.3）

- ❌ 删除文件 / 整目录前不 grep 引用
- ❌ 删除老项目内 `.claude/rules/local/obsidian-knowledge.md` 类的归属规范文件不先 grep 引用
- ❌ 删除老项目顶层 `docs/` 整目录不做完整迁移校验（必须按 §8.3 完整 SOP 走 4 子步：grep / 备份 `_legacy/` / 引用替换 / 二次 grep；具体执行走 skill: `migrate-to-modules` 工作流 Step 4）

---

## §11 写作质量与精简纪律分级

> 纪律标准原文 = skill: `prd-writer` 的 `writing-guide.md`（精简表达 / 段落组织），单一来源不复制；本节管「哪类文档适用到什么程度」与交付前自查。项目可在 `.claude/rules/local/` 细化分级边界。

### 11.1 分级适用矩阵

先看 frontmatter `type`，再看路径；拿不准按内容目的判：给人快速抓信息 = A，论证 = B，发散 = C，叙事说服 = D。

| 类 | 覆盖（type / 位置） | 纪律 |
|---|---|---|
| **A 需求/规范/技术** | `prd` / `spec` / `decision` / `plan` / `current-state` / `test-case` / 规范类 `concept`；`requirements/`、`specs/` 下正式文档 | 全套 writing-guide 通用纪律 + §11.2 补丁 |
| **B 调研/分析/评估** | `research`；调研 / 评估报告 / 复盘 / 审查 | **结论先行 + 分层展开**（结论→论据→细节）；不设字数约束，守 §11.3 |
| **C 构思/发散** | `_draft/` / 头脑风暴 / 方案发散 / 早期草稿 | 豁免；留档建议尾部给结论/候选清单收口段（不强制） |
| **D 汇报/叙事/对客** | 工作汇报 / 对客 / 投标材料 | 豁免；走各自叙事/对客规范 |

### 11.2 A 类补丁（优先级高于 writing-guide 字面标准，冲突以本节为准）

1. **精确 > 精简**：冲突时保精确；规则句/判定条件/正则豁免「每点 30 字」（参考信号非硬限）。
2. **表述精简 ≠ 内容取舍**：压句子可自主；**删内容**（词条/变体/规则/示例）是业务决策，须用户显式授权，否则单独列「建议删除清单」待拍板。
3. **删减红线**（见删必究）：规则口径、判定条件、正则与代码块、逐字文案、装配示例、AC 条文、变更记录、⚠️ 歧义标注。
4. **受控冗余合法**：跨节「一句话回显 + 引用」允许（读者跳读局部自足）；整段复制禁止。
5. **引用完整性**：精简/删改后 grep 被删概念的引用点，确认无落空引用。

### 11.3 AI 病灶负面清单（各类通吃，见即删）

口号式收尾、空洞总结句、同义反复、过度铺垫、无信息量过渡句、括号里解释的解释。

### 11.4 交付前自查（A/B 类产出落盘前过一遍）

- [ ] 每点一句话说清（规则句豁免）
- [ ] §11.3 病灶零残留
- [ ] 红线内容未删；内容删减已授权或单独列出待拍板
- [ ] 被删概念无落空引用
- [ ] （B 类）结论先行

> **委派 subagent 写文档时，本自查清单随指令一并附带**——subagent 是独立 context，不会自动继承主 Claude 当前的纪律判断。

---

## 与其他 skill 的衔接

- **业务文档创作（prd-writer / test-case-design）**：必须前置 `document-norms §1 §2` 保证归属与 frontmatter 合规；A/B 类产出建议同时前置 `§11`（prd-writer 已内置 writing-guide，可免）
- **modules/ 体系操作（module-init / module-index-refresh / migrate-to-modules / code-to-doc）**：前置 `document-norms §1 §3 §4` 保证索引段格式与归属
- **obsidian-* 增强**：本 skill §9 给出何时调用决策；具体 CLI 操作由 obsidian-* skill 自身负责
- **项目配备的发布 skill（如 `feishu-publish`）**：本 skill 不涉及平台 specific 字段；发布 skill 自管自己的 frontmatter 字段

## 质量自检（业务方 skill 调用本 skill 前自检）

- [ ] 已声明前置依赖具体到 § anchor（如 `§1 §2`），未整读
- [ ] 改文档前确认归属（§1）和 frontmatter 字段（§2）
- [ ] 改 status 后调 `module-index-refresh` 同步上级 overview
- [ ] 改文件名/路径前 grep 引用，改后再 grep 验证
- [ ] current-state/ 类文档正文带行级 source path（§5.3）
- [ ] 未触碰 overview 机器维护段（HTML 注释边界外维护手写部分即可）
