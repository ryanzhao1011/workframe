---
name: prd-writer
description: 写需求文档 / PRD 的主力工具：读模块 overview 与历史需求推导功能范围，按项目 PRD 框架（.claude/skills/prd-style/）的工序形态与章节结构，草稿分步确认后自动填充详情，产出 modules/ 体系下的 prd.md 及项目框架声明的附属产物（流程图件 / HTML 原型等）。
when_to_use: |
  用户说「写需求文档 / 写 PRD / 新建需求 / 把这个需求写成文档」时；
  需求已有共识、要落成正式文档时；demo 先行流程中 demo 拍板后补写 PRD 时；
  用户要建立 / 更新本项目 PRD 风格（「按我的风格写」「总结我的 PRD 规范」）时（§风格定制与萃取）。
  边界：需求本身还模糊、范围未定 → 先走 requirement-analysis；只要拆功能点 → feature-breakdown。
  项目若配了发布器（`<platform>-publish`），发布是用户明确触发的独立动作，不在本 skill 内。
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# PRD 需求文档编写 Skill

## 前置依赖

1. skill: `document-norms` §1（文档归属矩阵）/ §2（frontmatter 字段标准，特别是 §2.6
   `req_slug` + `sub_req_slug` 引用契约）。输出路径为
   `projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md`，
   frontmatter `type: prd` + `module` + `req_slug` + `sub_req_slug` 必填（`version` 字段
   已废除——版本演进进 `prd.md` 正文「变更与决策记录」）。
2. **项目 PRD 框架**：Read `.claude/skills/prd-style/SKILL.md`——本项目 PRD 的工序形态
   （§1）、维度开关（§2）、章节结构（§3）、各章写法（§4）、标注习惯（§5）的唯一事实源。
   **缺失时 fallback**：读框架默认模板
   `<插件根>/templates/project-skills/prd-style/SKILL.md`（插件根 =
   `cat .claude/workframe-state/plugin-root.txt`）照用，并提示用户
   「本项目未配置 PRD 框架，本次按框架默认执行；正常装机会自动放入，可跑 doctor 检查，
   或从上述模板复制到项目 `.claude/skills/prd-style/` 后按需修改」。
3. `writing-guide.md`（S3 填充前读）：core 通用写作纪律。与项目框架冲突时**以项目为准**
   （机器契约除外，见项目框架 §0）。

> **环境依赖**：Node.js 18+（仅 S5 HTML 原型预览/截图需要，可选）
>
> **可选委托**（按项目配备，不强依赖）：截图归档委托 `screenshot` skill（详见
> html-prototype.md）；外部发布由项目配备的发布 skill 处理，不在本 skill 内；Obsidian CLI
> 增强按需委托 `obsidian-doc-structure` / `obsidian-link-audit` / `obsidian-safe-write`，
> CLI 不可用时 fallback 到 `Read` / `rg` / `Edit`。

---

## Workflow：六段骨架

「做什么」由本 skill 固定；「用什么形态做」读项目框架 §1 工序形态声明。

```
S1 理解      读上下文 + 维度推断 → 呈现理解摘要                    [固定]
S2 骨架确认  范围骨架（形态读框架 §1）→（启用图件时）结构图草稿
             → 章节预告（按框架 §3 裁剪）→（框架声明时）页面骨架    [形态可配]
S3 填充      按框架章节分波写，[待确认] 汇总一次问                  [章节/波次可配]
S4 落盘      机器契约段：触发词 → 路径/frontmatter/索引             [固定]
S5 附属产物  按框架 §1 声明执行（图件渲染 / HTML 原型 / 无）        [整段可配]
S6 发布      仅用户显式触发                                        [固定]
```

**核心原则**：AI 主动分析填充，用户确认补充。不确定的内容标 `[待确认]`，汇总后**一次性**
问用户。所有交付物中不保留需求确认过程标记（如"Q12"、"用户澄清 #2"），确认结论作为正常
需求点融入正文对应位置。

**demo 先行变体**（项目框架启用原型工序、且用户已有可交互 demo 时的标准路径）：

- S2 范围与结构草稿**从 demo 反推**（仍需用户确认——demo 可能只覆盖部分需求）；页面骨架
  确认**跳过**（以 demo 为准）
- S3 交互细节**直接读 demo 的 HTML / JS 提取**（状态文案、禁用逻辑、显隐条件、状态机以
  代码为准），对话记录仅作补充
- S5 改为**归档**——demo 复制到 `prototypes/`（不重新生成），删除 tmp/ 原件避免双源；
  按需截图归档 `assets/`
- demo 本身的生成规范见 skill: `html-demo`

---

## S1 理解

### 只需用户提供

1. **需求简述**（1-3 句）
2. **所属模块/子模块**（如"小模型话术 / 对话逻辑"）
3. **（可选）已有原型路径**——该需求若已有迭代拍板的可交互 demo，提供路径即进入
   demo 先行变体

### 主动读取（找不到则跳过）

```
projects/modules/<basic>/overview.md                                  ← 基础模块定位
projects/modules/<basic>/<sub>/overview.md                            ← 子模块定位
projects/modules/<basic>/<sub>/current-state/architecture.md          ← 实现现状（关键上下文）
projects/modules/<basic>/<sub>/current-state/api-surface.md
projects/modules/<basic>/<sub>/requirements/overview.md               ← 需求清单
projects/modules/<basic>/<sub>/requirements/<req_slug>/*/prd.md       ← 同需求下其他子需求（拆子需求时必读，避免重复设计）
```

若 Obsidian CLI 可用，可先调 `obsidian-doc-structure` 读候选文档的 outline 与 properties
判断精读范围；修改或新建正式文档前后可调 `obsidian-link-audit` 做断链检查。

### 维度推断

按项目框架 **§2 维度开关**逐维推断（默认 V1-V5，项目可增删），不确定才问。维度决定
章节裁剪与工序启停（如 V1 → 页面骨架确认与原型工序）。

### 呈现理解摘要（在响应中输出）

```
**我的理解**：{一句话总结}
**功能范围**（草稿）：- {功能点1} - {功能点2} ...
**维度判断**：{按框架 §2 逐维标注 是/否}
**待确认**：{S2 展示骨架时一并提问}
```

---

## S2 骨架确认

按项目框架 §1 声明的形态逐个呈现、逐个确认，不要一次性倒给用户：

1. **范围骨架**（框架 §1「S2 范围确认形态」，默认 Mermaid 思维导图）：在响应中直接输出，
   不落盘。提问一次：功能范围是否准确？有遗漏或需调整吗？框架 §2 维度中不确定的请补充
2. **结构图草稿**（框架启用图件时）：选型与画法按 `diagram-guide.md`；选型理由对话中说明
   即可，不写入 PRD 正文。提问：流程是否准确？
3. **章节预告**：从框架 §3 章节总览按本次维度裁剪出启用章节列表，一行预告（便于用户提前
   纠偏维度）。提问：章节有遗漏或多余吗？
4. **页面骨架**（框架声明启用且 V1 时；demo 先行变体跳过）：用**简洁文字**描述每个功能
   模块的页面骨架（区域布局 / 核心元素 / 空状态），**不生成 HTML**。提问：骨架对吗？

第 2、3 步可合并一轮；整体遵循「草稿先行、用户确认后再进下一段」。

---

## S3 填充

> **执行前读取 `writing-guide.md`**（通用写作纪律）+ 项目框架 §4（各章写法）。
> 冲突以项目框架为准（机器契约除外）。

**分波输出**（波次读框架 §1，默认两波：先「背景与目标 + 方案概览」校准方向，等用户隐式
确认后再写其余全部章节）。

填充原则：

- 能从上下文推断 → 直接填，不问用户
- demo 先行变体：交互细节从 demo 的 HTML / JS 提取（状态机、文案、禁用与显隐逻辑以代码为准）
- 不确定的细节 → 标 `[待确认: {说明}]`
- 全部波次完成后，汇总待确认项**一次性**提问：

```
**以下内容需要补充确认（{N} 项）**
1. {问题}
...
其余内容已根据上下文推导填充，如需调整请直接告知。
```

---

## S4 落盘（机器契约段）

**触发条件（同时满足）**：

1. 所有 `[待确认]` 项已补齐
2. 用户给出明确触发词：`写入本地` / `落盘` / `保存` / `可以了` / `确认` / `ok`

只满足其一时不触发。若用户跳过待确认项直接说"保存"：明确说"其余你看着填" → 用上下文
默认值填入并**保留 `[待确认]` 标记**；未说 → 再次汇总提问，不自行放行。

**落盘动作**：

1. 落盘路径：`projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md`
   - **新需求**：`<req_slug>/` 不存在时**先调 `module-init`** 创建需求资产包骨架（含默认
     子需求 `main/` 与 `prd.md` 占位骨架）；落盘时覆盖占位骨架
   - **新增子需求**（已有 `<req_slug>/main/` 想拆新子需求）：从
     `<插件根>/templates/modules-template/requirement/main/`（插件根 =
     `cat .claude/workframe-state/plugin-root.txt`）复制整个子需求
     骨架到 `<req_slug>/<sub_req_slug>/`，逐文件替换 `{{SUB_REQ_SLUG}}` / `{{REQ_SLUG}}` /
     `{{MODULE_PATH}}` / `{{REQ_TITLE}}` / `{{NOW_ISO}}` / `{{TODAY}}`（子需求骨架共 6 个占位符；`{{OWNER_ROLE}}` 只在 req 级 meta.yaml/overview.md 与模块级 yaml 里，不在本流程的复制范围）
     占位符；不挪已有子需求内容
   - **改既有 prd.md vs 新建 `<sub_req_slug>/`**（详见 `module-architecture.md` §4.3
     决策树）：微调 / 文案 / 字段补充 / 小 AC 修正 → 改既有 `prd.md` + 变更记录一行；
     新用户故事 / 新主流程 / 范围显著变化 / 测试矩阵独立 → 新建 `<sub_req_slug>/`；
     历史版本留痕 → 正文变更记录，不建 `v2/` 目录
   - **格式跟随（硬纪律）**：迭代的既有 PRD 若是接入前的用户自有格式（章节与项目框架
     不一致），**格式跟随原文档，只动内容**——不经用户同意不做格式规范化；可弱提醒一次
     「要把这份迁移到项目 PRD 框架 / 把你的风格固化进框架吗」，用户不接就照原样写
   - **覆盖占位骨架检测**：写盘前先 Read 目标 `prd.md`；仍是模板占位（含 `{{REQ_TITLE}}`
     等未替换占位符或空骨架章节）→ 直接覆盖；已有用户实质内容 → 警示确认，避免静默覆盖
   - `req_slug` / `sub_req_slug` 命名约束：允许英文 / 数字 / 短横线 / 下划线 / 中文，
     3 条 OS 硬约束（不含 `/` `\`、不含 Windows 禁字符 `< > : " | ? *`、**不含空格**）
2. frontmatter 必填字段（按 skill: `document-norms` §2.3 type=prd）：

   ```yaml
   type: prd
   status: draft                       # 流转见 §2.2
   owner_role: pm
   updated: <ISO-8601 带时区>           # 格式与 bump 规则见 §2.7
   module: <basic>/<sub>               # 二段式必填
   req_slug: <req_slug>
   sub_req_slug: <sub_req_slug>        # 默认子需求填 main
   description: <一句话摘要>            # ★ 必填：≤200 字；缺失不落盘
   related: []                         # 跨模块/跨需求关联 wikilink（整体加引号）；有明确依赖时填写
   tags: []
   ```

   `version` 字段不使用；版本演进进正文「变更与决策记录」（该章存在性是契约——项目框架
   可改表格式，不能没有这章）。外部发布相关字段由项目发布 skill 自管，本 skill 不写入。
3. **流程图内嵌**（框架启用图件时）：落点、PNG+Mermaid 双格式、渲染与同步纪律
   见 `diagram-guide.md`（单一实现，此处不复写）
4. 同步上级 overview：调 `module-index-refresh` 限定路径
   `<basic>/<sub>/requirements/<req_slug>` 刷新索引段
5. 写入完成提示路径，进入 S5（有启用的附属产物）或 S6；附一句弱提醒：「评审通过后记得把
   frontmatter `status` 改为 `approved`（影响上级 overview 索引状态列）」，本 skill 不自动改

---

## S5 附属产物（按框架 §1 声明执行）

- **HTML 原型**（默认启用）：读 `html-prototype.md` 按规范生成 / 归档
  - 标准路径：基于 S2 页面骨架 + S3 交互细节生成完整交互级 demo → 预览 →（可选）截图归档
  - demo 先行变体：已有 demo 复制到 `prototypes/`，删 tmp/ 原件，按需截图
- **未声明任何附属产物**：跳过本段
- 项目框架新增的其他附属产物：按其声明的规范文件执行

---

## S6 发布（仅用户显式触发）

prd-writer **不直接调用**任何外部文档发布能力。本地 PRD 落盘后：

- 用户明确触发"发布到 {外部平台} / 发给评审" → 切换到项目配备的 `<platform>-publish`
  发布 skill
- 用户未触发 → 流程结束于本地文件，不做任何在线动作

PM 类文档完成后允许一次**弱提醒**（项目配备发布器时，固定文案，不强制）：
「需要的话，我可以继续把这份文档同步到 {外部系统}。」仅在用户未触发时用一次。

---

## 风格定制与萃取（建立 / 更新项目 PRD 框架）

**触发**：① 装机接入时 launcher 读本节照做（B 路径检测到 ≥3 份风格趋同的存量 PRD，
用户在节奏闸选了「按我的风格定制」）；② 用户显式说「按我的风格写 PRD」「总结我的 PRD
规范」；③ S4 格式跟随弱提醒被用户接受时。

**工序**：

1. **选样例**：3-8 份代表性存量 PRD（覆盖不同类型 / 模块；趋同判据 = ≥2/3 样本共享章节
   骨架主干）。样本仅 3-4 份时照做，但产物标注「低置信，样本较少，后续可随用随修」
2. **抽取**：章节骨架（共性序列 + 可选章）、范式（表格式样 / 编号习惯 / 图与原型使用习惯）、
   标注写法
3. **对照**：与默认模板逐项对照出**差异与冲突表**——含与 core 通用纪律的冲突项（项目可
   覆盖，但必须让用户知情逐条拍，不静默替用户选）；机器契约项（模板 §0）不参与对照、
   不可定制
4. **过目**：以「你的风格 vs 框架默认，差这几点」的差异对照表在响应中呈现，用户确认
5. **写入**：基于默认模板改写 `.claude/skills/prd-style/SKILL.md`（§0 机器契约红线整段
   原样保留；§1-§6 按拍板结果改），已有该文件时走更新（保留项目补充口径 §6 内容）

---

## 执行清单

- [ ] 读项目 PRD 框架（缺失走 fallback 并提示）
- [ ] **S1**：读取上下文，按框架 §2 推断维度，呈现理解摘要
- [ ] **S2**：按框架 §1 形态逐个确认——范围骨架 /（图件）结构图 / 章节预告 /（声明时）页面骨架
- [ ] **S3**：读 writing-guide + 框架 §4，按框架波次填充，汇总待确认项一次性提问
- [ ] 用户补齐待确认项 + 给出触发词
- [ ] **S4**：落盘（module-init 建包 / frontmatter 合规 / 格式跟随判断 / 覆盖检测），
  调 `module-index-refresh` 同步上级
- [ ] **S5**：按框架 §1 执行附属产物（默认 HTML 原型 → html-prototype.md）
- [ ] **S6**：仅用户显式触发时走项目发布 skill

## 与其他 skill 的衔接

| 对象 | 关系 |
|---|---|
| 项目 `.claude/skills/prd-style/` | 本项目 PRD 框架唯一事实源（工序/章节/写法/习惯）；缺失 fallback 读 core 模板 |
| `writing-guide.md` / `diagram-guide.md` / `html-prototype.md` | core 通用纪律 / 图件能力库 / 原型能力库（后两者按框架声明启用） |
| `document-norms` §1 §2 | 归属与 frontmatter 机器契约 |
| `module-init` / `module-index-refresh` | 建需求资产包 / 索引同步 |
| `acceptance-criteria` | 验收标准格式与 AC 编号契约 |
| `html-demo` / `screenshot` | demo 生成规范 / HTML→PNG 渲染引擎 |
| `requirement-analysis` / `feature-breakdown` | 前置：需求还模糊 / 只拆功能点时先走它们 |
