# `modules/` 体系架构（modules-system 设计文档）

本文档定义 Workframe `modules/` 体系的目录结构、跨层胶水、双向闭环与索引机制。

- `module-init` / `module-index-refresh` / `code-to-doc` / `migrate-to-modules` / `doc-graph-health` / `requirement-archiving` / `material-intake` 7 个 modules-system skill 依此规范工作
- `prd-writer` / `test-case-design` 输出路径默认到本规范定义的需求资产包内
- 文档归属、frontmatter、overview 三段制等通用规范见 skill: `document-norms`

## 1. 设计目标

modules/ 体系以"功能模块"为产品研发的一等公民，解决以下痛点：

- 一个需求物料散落（调研 / 计划 / 方案 / demo / 资料 / 分析）
- obsidian 链接补丁式维护，文件物理位置仍分散
- 缺少功能模块作为一等公民
- PM 拿不到代码时缺反向桥梁
- AI 冷启动困难
- 无双向闭环

modules/ 体系对所有 Workframe 项目默认启用（项目骨架恒建 `projects/modules/`）。

## 2. 四层定位

| 层 | 路径 | 性质 |
|---|---|---|
| 运行时层 | `.claude/` | Claude Code + Workframe 状态 |
| 治理资产层 | `projects/` | 关于"工作"的文档（modules/ + 计划 + 规格 + 进度 + 问题 + 决策）|
| 业务层 | 顶层（`miniprogram/` `src/` `deliverables/` 等）| 实际产物 |
| 周边资产层 | `company-context/` `my-workspace/` | 项目环境 + 个人产出（手动）|

modules/ 体系新增的内容主要在**治理资产层**的 `projects/modules/` 子树，并通过 `code_paths` 胶水反向指向业务层。

## 3. `projects/modules/` 树结构

### 3.1 两层嵌套 + 资产包

```
projects/modules/
├── overview.md                            # 全产品架构总图（只放基础模块摘要+链接）
└── <basic-module>/                        # 第一层（领域级，10-20 个）
    ├── module.yaml
    ├── overview.md                        # 三段制
    ├── shared/                            # 跨子模块复用实体
    │   ├── shared-data-model.md
    │   ├── design-principles.md
    │   └── (其他)
    └── <sub-module>/                      # 第二层
        ├── submodule.yaml
        ├── overview.md
        ├── current-state/                 # 【代码 → 文档】
        │   ├── architecture.md
        │   ├── api-surface.md
        │   ├── data-model.md
        │   └── code-map.md
        ├── requirements/                  # 【文档 → 代码】
        │   ├── overview.md
        │   ├── <req_slug>/
        │   │   ├── meta.yaml
        │   │   ├── overview.md
        │   │   ├── <sub_req_slug>/        # 默认 main；按场景拆多个
        │   │   │   ├── prd.md
        │   │   │   ├── prototypes/
        │   │   │   ├── test-cases/
        │   │   │   └── reviews/           # 上线后复盘（手动维护）
        │   │   └── <另一个 sub_req_slug>/ # 拆出新子需求时
        │   └── _draft/<slug>.md
        ├── decisions/
        ├── research/
        └── others/
            └── .gitkeep
```

### 3.2 两层嵌套理由

- **第一层**（basic）= 领域级（profile / payment / messaging / ...），10-20 个上限，避免顶层文件过载
- **第二层**（sub）= 功能级（profile/edit / profile/avatar / payment/checkout / ...），按需扩展
- 两层封顶；超出时拆基础模块或在 sub 下用普通子目录分类
- 大产品多 PM 协作时按 basic 分配 ownership

### 3.3 三段制 overview.md（机器维护索引段）

每层 `overview.md` 三段：positioning（人写）+ 中间内容 + 机器维护索引段（HTML 注释边界）。

| 层级 | 文件 | 中间内容 | 机器维护索引段 |
|---|---|---|---|
| 全局 | `modules/overview.md` | 总体架构图 | basic-modules-index（**只放摘要+链接**）|
| 基础模块 | `<basic>/overview.md` | architecture | submodules-index |
| 子模块 | `<sub>/overview.md` | current-state-summary | requirements-index |
| 子模块需求清单 | `<sub>/requirements/overview.md` | 省略 | 全文（按状态分组的需求清单）|
| 需求 | `<req_slug>/overview.md` | 省略 | sub-requirements-index |

**机器维护段边界标记**：

```markdown
<!-- WORKFRAME:AUTO-INDEX:START -->
> ⚙️ 此段由 `module-index-refresh` 自动维护，请勿手动编辑。

| 子模块 | 状态 | 摘要 |
|---|---|---|
| ... | ... | ... |

<!-- WORKFRAME:AUTO-INDEX:END -->
```

详细规范见 skill: `document-norms` §3。

### 3.4 全局 overview "摘要+下沉"

200 子模块规模下保持可读：全局 overview 只放基础模块摘要+链接，子模块详情下沉到 `<basic>/overview.md` 的 submodules-index 段。

## 4. 双向闭环

```
AI 解析代码 → current-state/ → PM 消费 → PRD + 测试用例 → 开发 vibecoding → 上线 → 反向更新 → 循环
```

### 4.1 代码 → 文档（current-state/）

- 由 `code-to-doc` skill 解析代码生成
- 4 个文件：`architecture.md` / `api-surface.md` / `data-model.md` / `code-map.md`
- frontmatter 必填来源证据字段（`source_repo` / `source_ref` / `source_paths` / `source_exported_at` / `verifier` / `confidence`）
- 正文每条关键结论必须带行级 source path（详见 skill: `document-norms` §5.3）

代码在哪，决定 current-state/ 怎么来：

- **代码在外部仓库**（PM 拿不到代码库的典型情况）：靠**研发/AI 解析后回填**；frontmatter `source_repo` 必填
- **代码在本仓库**：可以**自动从 code_paths 反向同步**；`source_repo` 留空（视为本仓）

### 4.2 文档 → 代码（requirements/）

- 由 `prd-writer` 输出 PRD（流程图内嵌其「业务流程与逻辑」章）到 `<sub>/requirements/<req_slug>/<sub_req_slug>/`；prototypes/ 由 `html-demo` 归档（demo 先行）或 `prd-writer` S5 产出（展示型）
- 由 `test-case-design` 输出测试用例到 `test-cases/`
- 上线后人工写复盘到 `reviews/`
- 同一子需求的小版本演进走 `prd.md` 正文「变更与决策记录」；范围显著变化时拆出新的 `<sub_req_slug>/`

### 4.3 默认 `main/` 子需求 + 何时拆分

所有需求**默认建 `main/` 子需求目录**：路径稳定 + 引用契约可二字段（`req_slug` + `sub_req_slug`）默认值兼容。

- `module-init` 创建需求资产包时同步建一个子需求目录，默认名为 `main`，允许创建时改名（如 `phase-1`）
- 简单需求一辈子只用 `main/` 也完全 OK，不强迫拆分
- 老引用只含 `req_slug` 时视为 `sub_req_slug: main`（详见 `document-norms` §2.6 引用契约段）

**何时改 `prd.md` 内容 vs 何时新建 `<sub_req_slug>/`**（决策树）：

| 场景 | 动作 |
|---|---|
| 微调 / 文案 / 字段补充 / 小 AC 修正 | 改同一 `prd.md`，在「变更与决策记录」表追加一行 |
| 新用户故事 / 新主流程 / 范围显著变化 / 测试矩阵独立 | 新建 `<另一个 sub_req_slug>/`（含独立 prd / test-cases / reviews） |
| 历史版本留痕 | `prd.md` 正文「变更与决策记录」表；不建 `v2/` 目录 |

**已存在 `main/` 后拆分的处理**：保留 `main/` 不自动改名（避免破坏现有 wikilink / spec_ref 引用），新场景直接新建 `<sub_req_slug>/`。如确实要把 `main` 改成更具体的名字（如 `core`），由用户**显式做一次重命名迁移**（grep 全引用 → 重命名 → 替换 → 二次 grep 验证）。

## 5. `submodule.yaml` schema

```yaml
parent_module: profile                  # 父基础模块的 name（值为父模块 yaml 的 name 字段）
name: edit                              # 模块名（单字段承担路径段名与展示名）
status: active                          # planning | active | done | dropped
owner: pm-zhao
created_at: "2026-05-09"                # 子模块创建日期（module-init 写入）
tech_stack:                             # 数组对象，支持 monorepo / 跨技术栈
  - path: miniprogram/pages/profile/edit/**
    stack: wechat-miniprogram
  - path: cloudfunctions/profile/**
    stack: wechat-cloudfunction
code_paths:                             # ★ 项目根相对路径
  - miniprogram/pages/profile/edit/**
  - miniprogram/components/avatar-cropper/**
  - miniprogram/services/profile.js
  - cloudfunctions/profile/get/**
api_dependencies: []                    # 跨模块依赖（仅声明，不强校验）
last_synced_at: null                    # current-state 最近同步时间（code-to-doc 写入）
sync_method: manual                     # auto | manual | dev-paste（current-state 同步策略，code-to-doc 维护）
related_tasks: []                       # board.yaml 任务 ID
related_issues: []                      # issues 文件名（不含 .yaml）
```

`module.yaml`（基础层）字段类似但更精简（无 code_paths / sync_method / last_synced_at）。
`last_index_refreshed_at` 字段已废弃——索引刷新由 PostToolUse hook 监听 submodule.yaml 改动自动触发，不需要在 yaml 内冗余记录。

### 5.1 `name` 字段语义

`name` 是**路径段名 + 展示名同源**的单字段（合并原 `slug` + `display_name`），为模块的唯一标识。语义定位：

- **作为目录段名**：`projects/modules/<name>/` 或 `projects/modules/<basic>/<name>/`，文件系统直接看到
- **作为人读展示名**：overview.md 一级标题 / 索引段表格列、对话流里的称呼
- **作为跨文档引用**：`module:` 二段式字段值（`<basic-name>/<sub-name>`）、`parent_module:` 字段值

**不要按传统 ASCII slug 理解**——这里的 `name` 允许中文，因为 modules/ 体系面向中文产品团队的实际工作流（中文 PM 写 PRD / 中文需求评审）。

**允许字符**：英文 / 数字 / 短横线（`-`）/ 下划线（`_`）/ 中文。例：`profile` / `用户档案` / `avatar-cropper` / `编辑资料`。

**3 条 OS 硬约束**（避不开）：

- 不能含路径分隔符 `/` `\`（否则破坏目录层级）
- 不能含 Windows 禁字符 `< > : " | ? *`（否则跨平台失败）
- **不能含空格**（含中间空格）——首尾空格致 git / shell 行为不可预期；中间空格会被 stale
  索引的 `\S+` 切分截断，code_paths 反查永远命中不了该子模块（module_init 入口已拒）

**推荐**英文短词（跨语言团队 + GitHub Pages 静态化 URL 友好），但中文也允许（subprocess / hooks 已统一 utf-8 处理；`module-init` skill 内有 `name` 命名约定段权威）。

**模板写法约定（防 YAML 1.1 类型推断）**：模板里所有用户输入字段（`name:` / `parent_module:` / `basic_name:` / `module:` / `req_slug:` / `req_title:` 等占位符引用）**必须用双引号包裹** —— 例：`name: "{{BASIC_NAME}}"`、`module: "{{MODULE_PATH}}"`。理由：YAML 1.1（PyYAML 默认行为）会把 `123` / `2026` / `yes` / `no` / `true` / `false` / `null` / `2026-05-09` 等推断为 number / bool / null / date，破坏字符串语义。框架硬编码值（`status: active` / `owner: pm` / `sync_method: manual` / `updated: {{NOW_ISO}}` 等）无此风险，可保持不加引号。`tools/validate.py` 有 `check_modules_template_user_input_fields_quoted` 防回潮检查。

### 5.2 需求资产包 `meta.yaml` 字段命名差异

需求 `meta.yaml` 仍用 `slug:` 字段（不改 `name:`），且独立的人读名走 `title:` 字段（无重复）。理由：

- `req_slug:` 作为需求级 frontmatter 引用契约，散落在 `board.yaml` / `issues` / `prd.md` / `test-cases` / 多个 skill 的 frontmatter 标准里（10+ 处）；改名将破坏已有 frontmatter 引用
- 在 RESTful / Web 语境下 `slug` 是"资源标识符"的标准词，跟"模块名"（`name`）语义有意区分
- 需求资产包没有"标识 + 展示名"二字段重复的痛点（`slug` + `title` 是不同概念）

`<req_slug>/` `<sub_req_slug>/` 在路径树中保留 `slug` 命名以保持引用契约一致。

**取值约束**：`req_slug` / `sub_req_slug` 的**取值约束与模块 `name` 一致**——允许英文 / 数字 / 短横线 / 下划线 / 中文，3 条 OS 硬约束同 §5.1（不含 `/` `\`、不含 Windows 禁字符 `< > : " | ? *`、**不含空格**）。**仅字段名因引用契约保留 `slug` 命名**；不要按传统 ASCII slug 理解，中文产品团队的中文需求名（如"头像裁剪"）合法。

### 5.3 子需求资产 `sub_req_slug` 字段

每个需求资产包默认建一个子需求目录 `<req_slug>/<sub_req_slug>/`（默认值 `main`），承载实际的 PRD / prototypes / test-cases / reviews。同一需求的子需求拆分原则见 §4.3 决策树。

**引用契约**（详见 `document-norms` §2.6）：

- 新写入 frontmatter / yaml 必须二字段：`req_slug:` + `sub_req_slug:`
- 老引用只含 `req_slug:` 时隐式视为 `sub_req_slug: main`
- `spec_ref:` 继续允许完整路径作为最精确引用

## 6. `code_paths` 胶水规则

### 6.1 项目根相对路径

`code_paths` 一律项目根相对路径（不是小程序根相对，不是绝对路径）：

| ❌ 错 | ✅ 对 |
|---|---|
| `pages/profile/edit/**`（小程序根相对，含义不明）| `miniprogram/pages/profile/edit/**` |
| `C:\Users\<username>\...\pages\edit\**` | `miniprogram/pages/profile/edit/**` |
| `apps/web/profile/...`（在 monorepo 但缺 workspace 前缀） | `apps/web/app/profile/edit/**` |

### 6.2 跨形态适配

业务层目录跟着平台/框架社区约定走：

| 形态 | 项目根 → 代码根 | code_paths 示例 | tech_stack |
|---|---|---|---|
| 微信小程序（云开发）| `project.config.json.miniprogramRoot=miniprogram/` | `miniprogram/pages/profile/edit/**` | `wechat-miniprogram` |
| 微信小程序（传统）| `miniprogramRoot=./` | `pages/profile/edit/**` | `wechat-miniprogram` |
| Next.js 单仓 | 项目根即 app 根 | `app/profile/edit/**` | `nextjs` |
| Next.js Monorepo | `apps/web/` 是 app 根 | `apps/web/app/profile/edit/**` | `nextjs` |
| SaaS 跨前后端 | Monorepo workspaces | `apps/web/...` + `apps/api/...` + `packages/shared/...` | 数组：`[nextjs, nestjs]` |

`code-to-doc` skill 自动适配：读 `project.config.json` 的 `miniprogramRoot`、读 `.workframe-config.json` 的 `monorepo.workspaces`、读 `package.json`，解析时自动加正确根前缀。

### 6.3 `stack` 字段枚举

| 类别 | 取值 |
|---|---|
| 微信生态 | `wechat-miniprogram` / `wechat-cloudfunction` |
| 前端框架 | `nextjs` / `nuxt` / `vue3` / `react` / `svelte` |
| 后端框架 | `nestjs` / `express` / `django` / `rails` / `fastapi` / `spring-boot` |
| 移动端 | `swift` / `kotlin` / `flutter` / `react-native` |
| 其他 | 使用社区惯例 short name；新加值通过 PR 反哺 framework |

命名约定：小写 + 短横线分隔。

### 6.4 Monorepo 配置

`.workframe-config.json` 加段：

```json
{
  "monorepo": {
    "enabled": true,
    "tool": "turborepo",
    "workspaces": ["apps/*", "packages/*"]
  }
}
```

## 7. 反向索引 `code-paths-index.json`

PostToolUse 每次写代码避免做全量 glob 扫描（200 子模块×3 paths ≈ 600 次 fnmatch），改为反向索引 lookup。

实际 schema（`__schema__: workframe.code-paths-index.v1`）：按 glob **首段静态前缀**分桶，桶 key 是 path 第一段（如 `miniprogram` / `cloudfunctions` / `apps`），无静态前缀的 pattern 进 `""` 通配桶：

```json
{
  "__schema__": "workframe.code-paths-index.v1",
  "buckets": {
    "miniprogram": {
      "miniprogram/pages/profile/edit/**": ["profile/edit"],
      "miniprogram/components/avatar-cropper/**": ["profile/edit"],
      "miniprogram/pages/payment/**": ["payment/checkout", "payment/refund"]
    },
    "cloudfunctions": {
      "cloudfunctions/profile/**": ["profile/edit"]
    },
    "": {
      "**/auth/**": ["auth/login", "auth/oauth"]
    }
  }
}
```

lookup 流程（`check-stale-modules.py:lookup_submodules`）：

```
changed file: 'miniprogram/pages/profile/edit/index.js'
  → bucket key = 'miniprogram'
  → 在 buckets['miniprogram'] + buckets[''] 内做 fnmatch
  → 命中 'miniprogram/pages/profile/edit/**' → ['profile/edit']
```

### 7.1 性能复杂度

- 复杂度：**O(P)，P = code_paths 总数**（**非 O(1)**——给定 changed file 仍需对桶内 pattern 做 fnmatch）
- 优化：按 glob 首段静态前缀分桶（如 `miniprogram`、`cloudfunctions`、`apps`），命中桶后只在桶内做 fnmatch
- 目标：单次 lookup ≤100ms（200 子模块×3 paths 规模）

### 7.2 写入责任三个点

| 触发源 | 写入动作 | 实现方式 |
|---|---|---|
| `module-init` skill | 创建子模块时初始化该子模块的索引段 | skill 内显式调用 |
| 修改 `**/submodule.yaml` | 重建该子模块的索引段 | PostToolUse 自动触发 |
| 索引文件损坏 | fallback 全量重建 | hook 检测到 JSON 损坏 → glob 全量扫描重建 + 提示用户 |

### 7.3 并发与原子写入

PostToolUse 可能并发触发（多文件批量 Edit / 并发 subagent），需保证索引文件不损坏：

| 场景 | 策略 |
|---|---|
| 单进程并发写 | 内存中合并改动 → 原子 rename（写 `.tmp` → `os.rename` → 原文件） |
| 多进程并发写 | 文件锁（POSIX `fcntl` / Windows `msvcrt`）排队；锁超时 5s → 跳过本次更新 + 标记 stale |
| 损坏检测 | 每次读取做 JSON validate；损坏时触发 fallback 全量重建 + 写 stale-modules.yaml 提示 |
| 跨平台 | 通过现有 `workframe-python` launcher 屏蔽差异 |

- 索引文件不进 git（运行时派生状态，损坏可全量重建；`.gitignore` 的 `.claude/workframe-state/*` 已覆盖。该目录下唯一进 git 的是 `memory-index.json`——它是记忆的保护元数据，无法重建，详见 `project-architecture.md` §Git 策略）
- 损坏时 fallback 自动恢复，零用户介入
- 手动编辑反模式（详见 skill: `document-norms` §10.4）

## 8. 关联检测三层机制

| 层 | 机制 | 解决什么关联 | 触发时机 | 性能 |
|---|---|---|---|---|
| L1 | `code-paths-index.json` 反向索引 | 代码 ↔ 子模块 | PostToolUse on 代码/submodule.yaml 自动 | O(P)，目标 ≤100ms |
| L2 | obsidian `related` / wikilink 反链查漏 | 文档 ↔ 文档 | 修改文档时 `obsidian-link-audit` skill 或 rg | O(N) 几秒 |
| L3 | 模型语义检索 | 潜在影响 | 重大变更时按需手动 | 数十秒 |

三层互补不替代。L3 不进自动 hook 链路，由 `technical-design` skill 的影响面评估（Blast Radius）在重大变更时按需承担。

## 9. PostToolUse 触发条件

PostToolUse hook 段（11 段链路之一）触发条件：

```
触发 = (Edit/Write/NotebookEdit 命中代码路径) ∪ (同上命中 **/submodule.yaml)
```

工具集以 `hooks.json` 的 `matcher` 为准（当前 `Edit|Write|NotebookEdit`）；脚本白名单里的
`MultiEdit` 是历史工具残留，保留只为向后兼容，不代表 matcher 应当补它。

- 代码改动 → 写 stale-modules.yaml（标记对应子模块 current-state/ 待刷新）
- submodule.yaml 改动 → 重建该子模块的 code-paths-index 段（保证索引与 yaml 一致）
- 外部工具改代码：SessionStart 扫 git diff
- 手动命令兜底（插件根从 `plugin-root.txt` 取，SessionStart hook 每会话刷新——`${CLAUDE_PLUGIN_ROOT}` 在 agent 的 Bash 上下文不可用，写它会静默展开成空、命令以「找不到 /scripts/xxx」的姿态翻车）：

  ```bash
  python "$(cat .claude/workframe-state/plugin-root.txt)/scripts/check-stale-modules.py" scan-git-diff      # 扫 git diff 写 stale
  python "$(cat .claude/workframe-state/plugin-root.txt)/scripts/check-stale-modules.py" rebuild-index      # 全量重建反向索引
  python "$(cat .claude/workframe-state/plugin-root.txt)/scripts/check-stale-modules.py" init-submodule <basic>/<sub>
  ```

实现：`plugins/core/scripts/check-stale-modules.py`。

## 10. issues / tasks 字段叠加策略

### 10.1 字段叠加而非替换

modules/ 体系下 issues / tasks 在现有 6 字段（`area / module / component / spec_ref / related_task / source`）基础上**叠加**新字段：

```yaml
# 既有字段（保留语义，多 project_type 兼容）
area: backend
module: profile/edit                # ★ 语义升级：modules/ 体系下二段式 basic/sub
component: avatar-cropper-ui
spec_ref: requirements/<req_slug>/<sub_req_slug>/prd.md
related_task: TASK-001
source: qa

# modules/ 体系下扩展字段
req_slug: avatar-cropper            # 挂需求就填，且与 sub_req_slug 同进同出
sub_req_slug: main                  # 与 req_slug 同进同出；缺省按 main 解释仅对存量条目成立
affected_modules: []                # 横切 issue 可选，二段式数组
```

### 10.2 约束

- `module` **必填二段式**（如 `profile/edit`）；老 issue 的单值 / 留空形态兼容保留
- 老 issue 不必迁移；新 issue 在 modules/ 体系下默认采用新格式（新写入推荐二字段 `req_slug` + `sub_req_slug`；老引用仅 `req_slug` 隐式视为 `sub_req_slug: main`）
- tasks（`board.yaml`）同步采用上述字段叠加策略

## 11. 周边资产层

```
company-context/                        # 公司环境
├── README.md                           # ⚠️ 敏感内容提示
├── policies/ org/ intro/ templates/

my-workspace/                           # 个人工作区
├── README.md                           # ⚠️ 敏感内容提示
├── reports/ reviews/ presentations/ notes/
```

README 强提示（取代默认 .gitignore）：由用户按实际情况决定是否进 git。由 `project_scaffold.py` 建项目时创建。

## 12. 治理层其他目录

### 12.1 `projects/specs/`（modules/ 体系下缩小）

```
projects/specs/
├── overview.md
├── design-system/
├── api-conventions/
├── compliance/
└── plans/<YYYY-MM-DD>-<plan-name>.md   # 跨模块的实施方案
```

- 单模块 plans 融入 `<sub>/decisions/` 或 `<sub>/requirements/<req_slug>/<sub_req_slug>/`
- 跨模块的实施方案进 `projects/specs/plans/`
- **新方案没有顶层 docs/**——老项目（含散落顶层 docs/）在迁移时清空到对应 modules/ 子树（详见 skill: `document-norms` §8.3 删除/重命名 SOP；具体执行交给 skill: `migrate-to-modules` 工作流 Step 4）

### 12.2 `projects/issues/`

- 扁平 + 全局序号（不按模块分子目录）
- 字段叠加策略（见 §10）

## 13. 建项目时的集成

由 `project_scaffold.py` 在建新项目 / 接入已有项目时统一完成：

- 自动建 modules/ 骨架（`overview.md`，**不预建业务层**）；首个 basic/sub 由用户运行 `/core:module-init` 长出来
- 创建 `company-context/` `my-workspace/`（各带一份敏感内容提示 README）
- CLAUDE.md 模板含 §文档与结构约定 段（指向 `document-norms` §1 归属矩阵，不内嵌副本）

## 14. 与现有 4 obsidian-* skill 的关系

modules/ 体系不替代 obsidian-* 4 个 skill；通过 skill: `document-norms` §9 给出何时调用决策：

- 改前/后查 backlinks → `obsidian-link-audit`
- 写 frontmatter / 新建文档 → `obsidian-safe-write`
- 只读 outline / properties → `obsidian-doc-structure`
- 历史版本查询 → `obsidian-history-check`

`module-init` / `module-index-refresh` 在内部按需调用上述 skill，CLI 不可用时 fallback 到 `Read` / `Grep` / `Edit`。

## 15. 反模式

完整反模式清单见 skill: `document-norms` §10。modules/ 体系特有反模式：

- ❌ `code_paths` 写小程序根相对路径（必须项目根相对，含 `miniprogram/` 前缀）
- ❌ `tech_stack` 单值表达 monorepo（必须数组对象）
- ❌ 三层及以上嵌套（modules/ 两层封顶；超出拆基础模块或在 sub 下用普通子目录分类）
- ❌ 全局 overview 展开子模块详情（必须只放基础模块摘要+链接）
- ❌ 反向索引手工编辑（应让 PostToolUse 自动重建或 fallback 全量重建）
- ❌ 手动编辑 overview 机器维护段（HTML 注释 START/END 之间）

## 参考

- skill: `document-norms` §1（文档归属矩阵）/ §2（frontmatter 标准）/ §3（三段制 overview）/ §5.3（current-state source path）/ §10（反模式）
- skill: `module-init`（创建模块/子模块/需求资产包）
- skill: `module-index-refresh`（递归刷新机器维护段）
- skill: `code-to-doc`（LLM 解析代码生成 current-state/）
- skill: `migrate-to-modules`（一次性迁移）
- `project-architecture.md`（通用项目目录结构）
