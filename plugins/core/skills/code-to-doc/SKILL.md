---
name: code-to-doc
description: '解析子模块的 code_paths 涉及的代码文件，生成/更新 current-state/ 4 个文件（architecture / api-surface / data-model / code-map）。自动适配 wechat-miniprogram（miniprogramRoot）/ Next.js Monorepo（workspaces）/ 跨技术栈数组 等形态。所有结论必须带行级 source path 锚点。触发词：解析代码、code-to-doc、生成 current-state、反向同步、刷新实现状态。modules/ 体系下用于：创建子模块后首次生成 current-state/；改动命中某子模块的 code_paths 后刷新它的 current-state/——命中即刷，哪怕这次只改了注释（PostToolUse 已写 stale 标记时优先做）；代码在外部仓库时 dev 解析后的 paste 入口；migrate-to-modules 完成后建议调用一次。'
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# Code to Doc Skill

## 前置依赖

调用本 skill 前需读 skill: `document-norms` §1（归属）/ §2（frontmatter，特别是 current-state 类）/ §5.3（current-state 正文 source path 行级 anchor 强约束）。

参考插件根 `reference/module-architecture.md` §4.1（current-state 双向闭环）/ §6.2（跨形态适配）。

## 定位

**双向闭环的"代码 → 文档"半边**。LLM 解析 `code_paths` 涉及的代码文件，输出结构化 current-state/，让 PM 不读代码也能理解架构、API、数据模型。

**适用范围**：

- 子模块已创建（`submodule.yaml` 存在）且 `code_paths` 已配置
- 代码可读（本仓 ∪ dev 已 paste 跨仓代码到 tmp 目录）

**不适用**：

- 子模块未创建 → 先调 `module-init`
- `code_paths` 为空 → 先在 `submodule.yaml` 配置
- **`submodule.yaml` 里 `code_map_coverage: exempt`** → 该子模块**有意不设 code-map**（纯文档档等裁量），
  不要为它生成 current-state/；豁免理由写在该子模块 overview，`module-close-check` 的检查 6 会跳过它并报一条 info
- 仅刷新 frontmatter `updated` → 不需要本 skill
- 写 PRD / 测试用例 → `prd-writer` / `test-case-design`

## 输入

| 模式 | 输入 |
|---|---|
| 单子模块（推荐）| 二段式 `<basic>/<sub>` |
| 多子模块批量 | 数组 `[<basic1>/<sub1>, <basic2>/<sub2>, ...]` |
| stale 触发 | 读 `.workframe/state/stale-modules.yaml`，按列表处理 |

可选：`dev_paste_path`（代码在外部仓库时，dev 把跨仓代码导出到本地 tmp，本 skill 解析此路径）。

## 工作流

### Step 1: 形态适配 + 路径解析

#### 1.1 读项目配置

- 读 `.workframe-config.json` 获取 `monorepo.workspaces`（如有）
- 读 `project.config.json` 获取 `miniprogramRoot`（如有）
- 读 `package.json` 推断框架（next / nuxt / nestjs / 等）

#### 1.2 解析 code_paths 实际位置

按 `tech_stack[].stack` + 配置自动加正确根前缀：

| stack | 适配规则 |
|---|---|
| `wechat-miniprogram` | `miniprogramRoot=miniprogram/` 时已包含前缀；`./` 时需加 |
| `wechat-cloudfunction` | 项目根直接（`cloudfunctions/...`）|
| `nextjs` 单仓 | 项目根直接（`app/...` / `pages/...`）|
| `nextjs` Monorepo | 检查 `workspaces`，如 `apps/web/` 则补前缀 |
| 跨技术栈数组 | 按数组 `path` 字段逐条处理 |

报告：
```
解析 code_paths：
  - miniprogram/pages/profile/edit/**  →  ./miniprogram/pages/profile/edit/   (12 个 .js + 3 个 .wxml)
  - cloudfunctions/profile/get/**      →  ./cloudfunctions/profile/get/        (2 个 .js + 1 个 .json)
  ...
```

### Step 2: 代码扫描

按 stack 类型采用不同解析策略：

| stack | 解析重点 |
|---|---|
| `wechat-miniprogram` | `Page({})` / `Component({})` 入口 + `data` / `methods` / `lifetimes` + `usingComponents` |
| `wechat-cloudfunction` | `exports.main` 入口 + `event.action` 路由 + `cloud.database()` 集合 |
| `nextjs` (App Router) | `app/<route>/page.tsx` / `route.ts` + Server Component 标记 + 中间件 |
| `nestjs` | `@Controller` / `@Module` / `@Injectable` 装饰器 + DTO + 数据库 entity |
| `express` | `app.get/post/...` 路由 + middleware + handler |
| 通用 | grep TODO / FIXME / HACK 标记 |

实现：用 `Glob` 收集文件 → `Read` 关键文件 → `Grep` 找 import / export / route 定义。**避免读全部文件**——只读入口 + 入口直接依赖的 1-2 跳。

### Step 3: 生成 current-state/ 4 文件

#### 3.1 architecture.md

- 主流程：从 page entry 跟踪到 service 调用到云函数
- 核心组件：列出 component / service / utility 模块
- 依赖关系：跨模块依赖
- 关键设计决策：从代码 pattern 反推
- 已知问题：grep TODO / FIXME

**强约束**：每条结论带 `path:line` 行级 anchor（详见 `document-norms` §5.3）。

#### 3.2 api-surface.md

- HTTP / 云函数接口：路由 + 入参 + 出参
- 内部模块导出：function / class / const 导出
- 事件 / 消息：如有 pub-sub
- 配置入口：app.json / package.json / .env 中本模块相关配置

#### 3.3 data-model.md

- 持久化表 / 集合：字段 + 索引 + 约束
- 内存数据结构：state / cache / session
- 数据流向：mermaid 图
- 关键约束：唯一性 / 并发 / 一致性 / TTL
- 已知数据风险：未加索引的 hot path / 缺迁移方案的 schema

#### 3.4 code-map.md

- 文件清单（按子目录分组）：pages / components / services / cloudfunctions / config
- 文件关系图：mermaid
- 每条文件带角色描述

### Step 4: frontmatter 写入

每个 current-state/ 文件写入完整 frontmatter（详见 `document-norms` §2.4 current-state 类）：

```yaml
---
type: current-state                      # 固定值,照抄
status: in_progress                      # 固定值,照抄
owner_role: dev                          # 固定值,照抄
updated: <NOW_ISO>                       # 产出时间
module: <basic>/<sub>                    # PM 补(模块归属;跨仓研发不一定知道)
description: <一句话摘要>                 # ★ 必填：本文件讲什么（如"话术管理子模块的 API 面：xx 个接口按 xx 分组"），口径见 document-norms §2.1
generator: code-to-doc-skill             # 本仓自跑填 code-to-doc-skill;跨仓研发反解填 dev-paste
source_repo: ""                          # 跨仓填(可脱敏标识如"内部仓/项目名";内部 URL 可能敏感),本仓留空;三态见下
source_ref: <commit-or-branch-or-tag>    # ★必填,反解基于哪个版本(可复现关键);跨仓研发必给
source_paths: [<...code_paths...>]       # 与 code_paths 在 source_repo 那个仓内的部分同指一组文件;仓相对;行内数组
source_exported_at: null                 # 跨仓填(研发反解/导出时间)
verifier: ""                             # PM 补:这次核对了哪几项、不含哪几项;待核留空
confidence: medium                       # high | medium | low,不确定标 low
related: []                              # PM 补(跨模块依赖 wikilink);研发可留空;与「相关模块」段同步（加引号,§2.1）
---
```

#### `source_paths` 的语义与写法（**有机器对账，写错会红**）

`source_paths` 与 `code_paths` **在 `source_repo` 所指那个仓内的那部分**同指一组文件。
坐标系为**该仓相对**（`source_repo` 为空即项目根仓，此时仓相对 ≡ 项目根相对；
而 `code_paths` 恒为项目根相对，见 `reference/module-architecture.md` §6.1）。

写法与 `code_paths` 同族——**目录写 `foo/**`，单文件写完整相对路径**。两条形态收窄：

| ❌ 别这么写 | ✅ 这么写 | 为什么 |
|---|---|---|
| `src/services/`（尾斜杠目录） | `src/services/**` | 尾斜杠不是合法 glob：路径匹配会把它当成一个**名字带斜杠的文件**去找,命中 0 个;形态判定还会把它归成 literal,于是它变成「一个不存在的文件」 |
| 块状 YAML 列表（`- item` 多行） | 行内数组 `["src/services/**", "src/db.py"]` | 出厂模板即行内数组,对账只认这一种形态——只认一种换来「一个失败模式、红灯文案能点名」 |

`module-close-check` 的检查 6（`code-map-coverage`）按**展开文件集相等**对账：
两侧各自展开成文件集合后必须逐个相同，**少一个和多一个都是 error**。

- **少**：`code_paths` 认领了某文件而这里没列——stale 会因它把本子模块标脏，
  而顺着 code-map 做影响分析的人看不到它，两张清单指的不是同一片区域
- **多**：这里列了 `code_paths` 没认领的文件——多半归别的子模块；
  同一文件被两份 code-map 声称覆盖时，描述分叉没有任何东西会报警

#### `source_repo` 的三态（决定对账在哪个坐标系里做）

| 取值 | 语义 | 机器行为 |
|---|---|---|
| 空 | 本仓（项目根仓） | 参与对账 |
| 某个**项目内嵌套仓的项目根相对路径**（如 `vendor/sdk`） | 该嵌套仓 | 参与对账 |
| 其他（**项目外仓的脱敏标识**，dev-paste 典型形态） | 机器定位不到该仓 | 覆盖率对账与基准精判**整条跳过并报 info** |

第三态不是边角——「代码在外部仓库」是 PM 拿不到代码库时的典型情况。
本地没有那个仓的文件视野，硬对账只会报出「缺掉全部文件」的假红，所以跳过是正确行为。

**字段填写分工（跨仓 dev-paste 场景——研发不必全懂框架约定）**：

- **研发主给**：`source_ref`（反解的 commit,可复现关键）+ `source_paths`（**仓相对**,坐标系由 `source_repo` 定,见上）+ `source_exported_at` + `confidence` + 正文实质内容与每条 `file:line`
- **研发脱敏给**：`source_repo`（内部仓 URL 可能敏感,用"内部仓/项目名"标识即可,不强求完整 URL）
- **PM 入库时补**：`verifier`（**下一行那三项审查的结果**——核对了哪几项、**不含**哪几项;只写一个名字等于把审查信息在写入那一刻全丢掉）/ `related`（跨模块依赖）/ `module`（模块归属）;固定值字段（type/status/owner_role/generator）研发照抄
- **PM 顺带审查**：走本 skill dev-paste 入口入库时,审 `confidence` 诚实性 / `file:line` 齐备 / faithfulness（结论是否忠于代码,见反模式）

`source_ref` 取值：

- 本仓：自动 `git rev-parse HEAD` 取当前 commit（跨平台用 `Bash` 或 fallback）
- 跨仓：用户输入或从 `dev_paste_path/source-meta.txt` 读取

### Step 4.5: 相关模块段（regen-safe 横向关联）

每次生成/重写 `architecture.md` 时，在正文末尾维护 `## 相关模块` 段：

- 基于代码证据（import / API 调用 / 共享数据表等）列出本子模块依赖或被依赖的其他 `<basic>/<sub>`，每行一个 wikilink + 一句关系说明 + 行级 source path（同 §5.3 约束）
- frontmatter `related:` 同步写入这些 wikilink（整体加引号）
- 本段由本 skill 每次再生成时**基于代码证据重建**（与其他机器产出一致）；人工发现的业务级关联不要补在这里——会被下次重跑覆盖，应落在 PRD 正文或 overview positioning（人写区）
- 无跨模块依赖时写 `_(无跨模块依赖)_`，不留空

### Step 5: 同步 submodule.yaml

更新 `submodule.yaml` 字段：

```yaml
last_synced_at: <NOW_ISO>
last_synced_ref: <同 Step 4 写进 frontmatter 的 source_ref>
sync_method: auto                        # 或 manual / dev-paste
```

**`last_synced_ref` 必须与 current-state/ 各文件 frontmatter 的 `source_ref` 同值**——
它俩是同一个基准的两个落点，`module-close-check` 的检查 3 会对账（外加 overview 的
「基准 commit」行，三者任意两个不同值即 error）。只写 `last_synced_at` 不写 ref 时，
基准落点少一个，那道对账拿不到足够的比对项会**整条跳过**——不报错，只是不再有人看着。

### Step 6: 同步上级 overview 的 current-state-summary 段

调 `module-index-refresh` skill 限定路径 `<basic>/<sub>` 同步：

- 重建 `<sub>/overview.md` 的 `current-state-summary` 机器维护段（基于 4 个 current-state/*.md 的 frontmatter `updated` / `confidence` / `source_paths` + 第一个 H2 段首句）
- 段外人写部分（positioning / 中间内容）零接触
- 触发依据 skill: `document-norms` §4.2（current-state 写入是触发同步的场景）

### Step 7: 清理 stale 标记

调脚本清，**不要自己读写 `stale-modules.yaml`**——那份文件由 PostToolUse hook 并发写入，
手工 Edit 既持不住文件锁也不是原子写：

```bash
python "$(cat .workframe/state/plugin-root.txt)/scripts/check-stale-modules.py" \
    clear-stale "<basic>/<sub>" --reason "<一句话处置理由，如：code-map 已随本次改动更新>"
```

脚本内部走 FileLock + 原子写；条目本来就不存在时也返回 0，可安全重跑。
`--reason` 可选但建议带上：清除后条目即消失，「更新文档后清」与「评估不改而清」
在清单上长得一样，随 `stale_cleared` 事件留下的理由是唯一能区分二者的处置记录。

### Step 8: 报告

```
✅ current-state 已生成

📁 modules/profile/edit/current-state/
  - architecture.md   (12 条结论，11 带 source path，confidence=medium)
  - api-surface.md    (5 接口，全部 source path)
  - data-model.md     (3 表 + 2 内存结构，全部 source path)
  - code-map.md       (28 文件分类)

📊 解析统计：
  - source_ref: commit:abc123def
  - source_paths: 4 个 glob，共 28 文件
  - 跳过文件：3（生成代码 / .min.js）
  - TODO / FIXME 标记：8 处

⚠️ 待人工核对：
  - architecture.md 第 3 条结论 confidence=low（动态导入难追踪）
  - data-model.md user_session 表索引信息缺失

🔄 已清理 stale-modules.yaml 中 profile/edit 标记

下一步建议：
  - PM review current-state/ 内容并填 verifier 字段（核对了哪几项、不含哪几项）
  - 如需对应 PRD，调 `/prd-writer`
```

## 输出

- current-state/ 4 文件（含完整 frontmatter + 行级 source path）
- 更新 submodule.yaml 的 last_synced_at + last_synced_ref + sync_method
- 清理 stale-modules.yaml 标记
- 待人工核对清单 + confidence 报告

## 质量自检

- [ ] 4 文件全部生成
- [ ] frontmatter 完整（type / source_ref / source_paths / generator / confidence 必填）
- [ ] 正文每条关键结论带 `path:line` 行级 anchor（§5.3）
- [ ] confidence 字段诚实（不确定的结论标 low）
- [ ] source_ref 准确（commit / branch / tag 形式）
- [ ] submodule.yaml 已更新 last_synced_at + last_synced_ref（ref 与 frontmatter `source_ref` 同值）
- [ ] stale-modules.yaml 已清理对应条目
- [ ] 跨形态前缀正确（miniprogramRoot / monorepo workspaces）

## 反模式

- ❌ 正文不带 source path 行级 anchor（强约束，违反 §5.3）
- ❌ confidence 全标 high（实际部分推断不确定时应标 medium / low）
- ❌ 读全部代码文件（应只读入口 + 1-2 跳依赖）
- ❌ 跳过 frontmatter `source_ref`（reproducibility 失效）
- ❌ 直接写 current-state/ 不更新 submodule.yaml.last_synced_at（同步状态失效）
- ❌ 只写 `last_synced_at` 漏写 `last_synced_ref`（基准落点少一个，检查 3 的三落点对账会整条跳过——不报错，静默失去看守）
- ❌ 只刷部分基准落点（**共五处**：三处装同一个 commit 值须逐字相同——current-state 的 `source_ref`、submodule 的 `last_synced_ref`、子模块 overview「最近同步」行的括注；另两处随基准一起刷但不参与同值对账——submodule 的 `last_synced_at`、current-state 的 `verifier`。清单与「命中即刷」口径见 `reference/module-architecture.md` §5.4）
- ❌ 刷了基准却把上一批的 `verifier` 原样留着（它是基准的从属字段，**基准变则整体重写**；留旧值等于让文档声称核对了它没核对的那份代码，而这件事没有任何闸会发现）
- ❌ 改动命中了某模块的 `code_paths` 却不刷它的基准，理由是「只改了注释、行为没变」（**命中即刷**：命中就刷，`verifier` 按本次实际核对写——注释级改动的典型写法是「机器自证零行为改动（`git diff -w` 非注释行 = 0）；**不含**行为测试重跑」；`module-close-check` 的刷新检查按此口径判，命中未刷即红）

**收口前跑一次 `workframe-module-close-check`**（不在 PATH 上时直接跑插件目录下的 `scripts/module_close_check.py`）：正确时点 = 模块文档与账本写完之后、外层仓收口提交之前，代码仓本身应已提交（基准 hash 只有提交后才存在）。除它自己的计数日志外，它不改动任何被检对象，任一 error 则 exit 1。
- ❌ 不清理 stale 标记（反复触发解析浪费）
- ❌ **把脆弱推断当事实**（faithfulness 铁律：每条结论必须忠于实际读到的代码;推断/不确定的标 `confidence=low` + 写清 gap（哪里没查到）。错误反解会污染下游对账,比标"不确定"危害大——业界 reverse-doc 共识）
- ❌ **敏感信息入文档**（`source_repo` 内部 URL 应脱敏;不贴密钥 / 敏感配置 / 大段源码——current-state 是**文档**,非代码副本）

完整反模式见 skill: `document-norms` §10。

## 与其他 skill 的衔接

- **document-norms** §1 §2 §5.3：归属 / frontmatter / source path 强约束（前置）
- **module-init**：创建子模块后建议接着调用本 skill
- **module-index-refresh**：current-state 更新后调用（同步 `<sub>/overview.md` 的 current-state-summary 段）
- **migrate-to-modules**：迁移完成后建议调用本 skill 全量生成
- **prd-writer**：PRD 写作时读 current-state 作上下文
- **check-stale-modules.py**：消费本 skill 写入的 last_synced_at；提供 stale-modules.yaml 列表

## 外部代码仓降级

项目代码在外部仓库（本项目仓拿不到 git 写权限）时：

| 子场景 | 流程 |
|---|---|
| dev 已 export 跨仓代码到本地 tmp | 本 skill 通过 `dev_paste_path` 输入路径解析；`source_repo` 填外部仓（可脱敏标识）|
| **研发按「反解作业单」反解回传**（PM 完全拿不到代码,如纯对接外部研发）| 研发**尽量填全** frontmatter（固定值照抄 + 主给 source_ref/内容/file:line/confidence,见 Step 4「字段填写分工」）+ 正文实质内容 + 差异归因表 → **PM 走本 skill dev-paste 入口：审查（confidence 诚实 / file:line 齐 / faithfulness）+ 把这三项审查的结果写进 `verifier`（各审到什么程度、哪一项没做）+ 补 related·module + 入库**（generator=dev-paste）|
| 敏感与权限 | 产出是**文档**（架构/API/数据模型,非大段源码）,敏感度可控；`source_repo` 脱敏；提醒研发别贴密钥/敏感配置；PM 无研发权限时,正文 `file:line` 作研发侧可验证锚点存档 |
| 跨仓 source 证据字段 | `source_ref` 研发必给（可复现）；`source_repo` / `source_exported_at` 跨仓填（可脱敏）；`verifier` **PM 核对后补**（研发交回时天然为空,不再"缺一报错"——改为 PM 入库时补齐 + 审查;填的是**核对范围**：核对了哪几项、**不含**哪几项,不是一个名字）|

> **本 skill 两种用法**：① **PM 自建、能拿到代码** → 直接跑本 skill 反解入库（Step 1-8 全走）；② **跨仓、研发反解** → 研发按「反解作业单」填 → PM 走 dev-paste 入口审查+补字段+入库（本节）。作业单**无独立模板文件**——由 PM 按 Step 4「字段填写分工」+ §3 四文件内容规范现场拟一份给研发（字段与规范一一对齐即可）。

详见插件根 `reference/module-architecture.md` §4.1。

## 失败模式与降级

| 失败 | 降级 |
|---|---|
| code_paths 为空 | 报错并提示先配 `submodule.yaml.code_paths` |
| 解析失败（某文件语法错）| 跳过该文件 + 警告，继续处理其他 |
| 跨形态识别失败 | 报告并请用户确认 stack；fallback 到通用 grep 模式 |
| confidence 整体 low | 报告并请用户做人工 verify；不阻塞写入（保留 confidence 标记给后续 review） |
