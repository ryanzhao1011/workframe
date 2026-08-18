---
name: requirement-archiving
description: 历史需求资产归档入库：把项目外的原始需求资料（TAPD 单 / 飞书导出 docx / xls 清单 / 截图原型等异构收料）重整为 modules/ 体系下的现状基线 PRD + shared 事实源 + 溯源台账。九段流水线（Phase 0-8）：收料冻结 → 通读建模 → 方案拍板 → 骨架与 shared → PRD 回填 → 反向对账 → 原型校准 → 关联收口 → 审查清源。
when_to_use: |
  - 用户提供一批历史需求原始资料（本地文件夹 / 网盘导出），要求「归档 / 入库 / 整理成 PRD」时；
  - 为已上线的生产功能补建 modules/ 需求事实源（现状基线形态）时；
  - 新建 basic / sub 模块并批量回填历史需求时。
  边界：项目内已有 specs/ 下 md 的一次性搬迁 → migrate-to-modules；写全新需求 → prd-writer；
  从代码反解实现现状 → code-to-doc。本 skill 只管「外部异构原始资料 → 需求事实源」。
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion]
---

# Requirement Archiving 历史需求归档 Skill

## 前置依赖

- skill: `document-norms` §1（归属矩阵）/ §2（frontmatter 与时间戳）/ §3（overview 三段制）/ §11（写作纪律 + 交付前自查）
- 项目 PRD 框架 `.claude/skills/prd-style/SKILL.md`（章节结构 / 各章写法 / 标注习惯的项目层事实源，装机放入；缺失时 fallback 读 core `templates/project-skills/prd-style/SKILL.md`——与 prd-writer 同机制）+ prd-writer `writing-guide.md`（通用写作纪律）
- skill: `module-init`（建骨架）与 `module-index-refresh`（索引刷新）——作为被调用件，不重复其职责
- skill: `html-demo`（Phase 6 原型校准的执行引擎，有 UI 的需求才需加载）
- `projects/specs/_meta/taxonomy.md`（tags 受控词表，**如项目维护**；缺失时 check_archive 自动跳过词表检查）
- **环境依赖（仅 Phase 0 收料脚本）**：docx / PDF / xls 解析需 6 个 Python 库，一次安装：
  `pip install python-docx pdfplumber pypdfium2 openpyxl xlrd Pillow`
  缺库不中断收料——对应格式文件在 inventory 备注标「缺哪个库怎么装」，装完重跑即可

## 定位

**外部异构原始资料 → modules/ 需求事实源** 的唯一工作流。三个近亲的分工：

| skill | 输入 | 本 skill 的关系 |
|---|---|---|
| `migrate-to-modules` | 项目内已有的 specs/ md | 不重叠——那是搬迁，这是重建 |
| `prd-writer` | 新需求共识 | 复用其写作规范；工作流不同（归档无「澄清需求」阶段，只有「对账原文」） |
| `code-to-doc` | 代码仓 | 互补——本 skill 建意图基线，code-to-doc 建实现现状 |

**核心形态约定**：已上线需求写成**现状基线**（frontmatter `status: shipped`、验收标准章改为「现状口径（回归参照）」checkbox）；未开发需求写成规划稿（`draft`）。PRD 正文只写**当前生效的规格**——废弃方案不进正文，过程性中间记录用完即删。

## 九段工作流（Phase 0-8）

```
0 收料冻结 → 1 通读建模 → 2 方案拍板 → 3 骨架与shared → 4 PRD回填 → 5 反向对账 → 6 原型校准 → 7 关联收口 → 8 审查清源
```

每阶段有**门禁**，过不了不进下一阶段。

### Phase 0 收料冻结

- 跑 `scripts/extract_assets.py <源目录> <工作目录>` 产出：文件清单、**模态矩阵**（文字/图片/表格/外链/二进制）、逐文件正文、全部内嵌图片（无损）、外链清单
- `<工作目录>` = 项目临时加工区 `tmp/req-archive-<slug>/`（不进 git）。它只是中转：Phase 4 回填时把选用图片从工作目录归位到资产包 `assets/`，Phase 8 审查清源后随任务清理工作目录
- 识别**不可转写资产**（.rp 原型、二进制模板等）与**源内容已丢失项**（如「点击图片查看完整表格」占位但无图）
- 门禁：丢失内容与可补外链**第一时间问用户**能否补源；原件处置方案（哪些归档哪些可删）此时初判，**不在模态盘点前拍板删除**

### Phase 1 通读建模

- 通读全部正文提取；重建需求树（父子单、空壳单）与时间线
- **信源质量分级**：同一内容多信源（如 TAPD PDF 压缩图 vs 原 docx 高清图）时登记配对，**取最高质量源，低清版只做佐证**
- 识别横切字典类内容（编码表 / 状态映射 / 语言口径）→ 预定为 shared 事实源
- 门禁：每份文件读过；高低清信源配对完成

### Phase 2 方案拍板

- basic/sub 定位：对照常见 4 种切分范式（按能力面 / 按系统运行环节 / 与生产子系统 1:1 / 按能力链分层）选型
- 产出「文档 → 落点」映射表，每份原文标三类处理之一：**整合出 PRD / 并入所属 PRD 变更历史（小优化单）/ 拆分并入多处（跨落点单）**
- 汇总第一批**口径冲突清单**（新旧版本打架、图文不一致）
- **PRD 框架依据**一并进集中拍板：产出 PRD 默认按项目 `prd-style`；项目存量 PRD 风格趋同（≥3 份）而 `prd-style` 仍是出厂默认版时，提议先走 prd-writer §风格定制与萃取再回填
- 门禁：映射表 + 原件处置方案 + 冲突清单经 **AskUserQuestion 集中拍板**；不散落在写作过程中零碎问

### Phase 3 骨架与 shared

- 走 `module-init` 建 basic / sub / 需求资产包（批量时可自写简单循环调用——框架不提供现成批量脚本；占位符替换与 meta 状态必须逐一落实）
- **shared 事实源先行**：编码字典 / 全局认知 / 需求台账（含 TAPD ID × 上线版本 × 归档落点的溯源表）——PRD 引用它们，不重复枚举
- 填各层 overview 人写段与 frontmatter `description`，调 `module-index-refresh`
- 门禁：占位符零残留；索引连跑两次幂等；description ≤200 字

### Phase 4 PRD 回填

- 分批（按 sub，体量大的先），每批落盘后跑 `scripts/check_archive.py <模块路径>` 自查
- 图片按质量分级归位进各需求包 `assets/`，**图中规格必须转写进正文**（图是证据，不是正文的替代）
- 拍板结论直接写为需求事实；被覆盖的旧口径进「变更与决策记录」表
- 门禁：自查零异常（frontmatter / tags 词表 / 禁用标签 / 断链断图 / AI 病灶）

### Phase 5 反向对账（最容易被省略、也最容易翻车的一步）

- **源→目标覆盖矩阵**：源文档每一章节、**每一张图**逐条判定「已转写 / 并入变更史 / 弃用+理由」，不允许存在无落点也无弃用理由的源内容
- 图片评估必须 **100% 留痕**——看过多少张就是多少张，不许在记录里写「全量」实际抽样
- 外链清单逐条判定覆盖性（已覆盖 / 未覆盖需导出 / 放弃），未覆盖项写进台账「删源前知情项」
- 对账中暴露的新冲突 → 二次拍板 → 按变更三步融入正文
- 门禁：覆盖矩阵无空洞；新冲突全部拍板闭环

### Phase 6 原型校准（有 UI 的需求必做）

归档产物是「文档说应该怎样」，线上是「实际怎样」。**用截图建仿真 demo 是把两者对上的唯一有效手段**——某次 16 份 PRD 的归档实战中，这一步校准出 30 处偏差：既有文档写了但线上没做的（已停用页签的专属列），也有线上做了但文档全无记载的（列表刷新入口、步骤区可折叠、已关闭项仍可启用）。

- **按页面单元分批**，一次一个单元：列清单要图 → 收图 → 出差异清单 → 建 demo → 用户确认 → 归档 `prototypes/` + 同步 PRD。不要攒到最后一起做，差异会淹没在批量里
- **要图清单要写明「怎么截」和「验证什么」**，并明确要求悬浮态、展开态、空态；线上取不到的状态（如无命中数据）改用 demo 的**模拟开关**按 PRD 补齐
- demo 走 `html-demo` skill 口径：自包含单文件、复刻截图视觉、全状态可达、落 `tmp/` 就地迭代、拍板后归档 `prototypes/index.html` 并删 tmp 原件
- **差异三分类**：① 线上口径明确的 → 直接改 PRD；② 图文冲突 → AskUserQuestion 拍板；③ 疑似线上缺陷 → 先问用户确认是不是缺陷，再决定 demo 复刻现状还是呈现合理行为
- 一份 demo 可被多份 PRD 共用（如同一配置区页面同时覆盖 4 份 PRD），在各 PRD 关联资产互相指明
- PRD 补一句分工声明：**视觉与控件行为以原型为准，正文只写影响开发逻辑的规则**
- 门禁：每个单元的差异清单都经用户确认；原型已归档且 PRD 关联资产已指向

### Phase 7 关联收口

- **跨模块双向回写**：本模块 module.yaml 声明依赖之外，被依赖方的 module.yaml / overview 也要反向登记（遵循 scope 最小化，仅回写直接相关方）
- 代码在外部仓的模块：submodule.yaml `sync_method: dev-paste` + 注释说明，避免 stale 巡检误报
- 跑 `doc-graph-health` 全库巡检，确认本模块零断链零孤儿
- 写 `projects/changelog.md`；遗留待确认项建 board task（走 task-management 口径）
- 门禁：巡检通过；changelog 与 board 落账

### Phase 8 审查清源

- **收料来自当前项目之外时，本阶段整段不适用**——源目录只读，不删不动不写。典型场景：用户
  新建项目后把老项目 / 网盘导出目录喂进来分析，那是他仍在使用的资料，动它一个字都是越界。
  这种情况下台账即交付物，Phase 8 只做「确认产出完整」不做清源
- 用户审查全部产出后，**单独二次确认**才删原件
- 删除前：grep 引用清零 + 不可替代资产复核（已归档 shared/assets/原始资料/）+ 台账「外链知情项」再提示一次
- 删除后：台账成为唯一溯源
- **原件不是只有删/不删两态**——第三态是「保留但不再维护」（归 `<sub>/others/` 或留原位）。
  选它必须**当场定主次**：原件标注「原始资料，不再维护，事实源见 <产出路径>」，台账同步记一行。
  不标的话就是两份同主题内容并存，半年后漂开了没人知道该信哪份——比删错更难查

## 脚本

| 脚本 | 用法（插件根从 `plugin-root.txt` 取，不依赖 PATH） | 产出 |
|---|---|---|
| `scripts/extract_assets.py` | `python "$(cat .claude/workframe-state/plugin-root.txt)/skills/requirement-archiving/scripts/extract_assets.py" <源目录> <工作目录>` | `inventory.md` 模态矩阵 / `text/` 逐文件正文 / `images/` 无损图片 / `links.md` 外链清单 |
| `scripts/check_archive.py` | `python "$(cat .claude/workframe-state/plugin-root.txt)/skills/requirement-archiving/scripts/check_archive.py" <projects/modules/<basic>>` | 终端报告：frontmatter、tags 词表（无 taxonomy 自动跳过）、禁用标签、AI 病灶、wikilink 与图片断链、孤儿图、AUTO-INDEX 配对 |

索引刷新**不在**本 skill 脚本内——调 core `module-index-refresh`，避免双实现。

## 质量自检

- [ ] Phase 0 模态矩阵先于任何删除决策
- [ ] 同内容多信源取最高质量版（PDF 内嵌图用**原始位图提取**，禁「渲染页面放大裁切」——插值放大只会更糊）
- [ ] **低清图不作为改动原文口径的依据**（硬约束）：从压缩截图读出的数字 / 字段名 / 按钮文案与原始需求文档不一致时，**默认原文正确**，只在「待确认项」标存疑；仅当拿到高清源或用户口头确认后才可改写正文
- [ ] 已上线需求 = 现状基线形态；正文无废弃方案、无归档过程元记录
- [ ] 小优化单并入变更历史，不单独建包；「跨页面改动集合」不是功能模块，拆分并入
- [ ] Phase 5 覆盖矩阵含**全部图片**的逐张判定记录
- [ ] 外链清单已产出且未覆盖项经用户知情
- [ ] 跨模块关联为**双向**
- [ ] 删源走 Phase 8 双确认，不可转写资产已实体归档

## 反模式（实战实证）

- ❌ **只提取文字就开写 PRD**——归档实战首轮 145 张图零评估，阈值表、关闭原因枚举、分期规划全在图里，靠用户质疑才补
- ❌ **渲染放大代替原始位图提取**——700px 源图放大到 1206px 反而更糊，且导致把「7 天」误读成「30 天」、虚报了一条图文冲突
- ❌ **拿低清图当权威去改事实源**——归档实战三次踩中：「7 天」被误读成「30 天」；带前缀的术语被误删前缀（「XX服务器」→「服务器」）；按钮文案的全角加号被误改（「＋添加XX比例」→「+ 添加比例」），三处高清截图证实**需求文档原文全对**。低清图只能提出存疑，不能改写口径
- ❌ **删除决策先于模态盘点**——先拍「原件全删」，后发现 .rp 原型不可转写又推翻重议
- ❌ **只有正向映射、没有反向对账**——主文档 §2.4 评审问题记录（8 条）无任何落点，Phase 5 缺失导致的真实遗漏
- ❌ **跨模块关联只写单向**——本模块声明了依赖，被依赖方无反向登记，知识网成了单行道
- ❌ **过程记录混进事实源**——归档期的口径对账表遗留在 shared 文档里（用户明确要求：中间记录用完即删）
- ❌ **覆盖记录夸大**——changelog 写「145 张逐张评估」实际约 35 张；看了多少写多少
- ❌ 外链内容不清点就删源——链接背后的技术文档可能是唯一事实源

## 与其他 skill 的衔接

- **module-init / module-index-refresh**：Phase 3 的建骨架与索引（必调）
- **项目 prd-style + prd-writer writing-guide**：Phase 4 的写作口径（前置加载）
- **document-norms §11.4**：每批 PRD 落盘前自查（脚本覆盖不了的语义项）
- **html-demo**：Phase 6 仿真 demo 的生成与归档规范（本 skill 只管什么时候做、按什么单元切、差异怎么分类）
- **doc-graph-health**：Phase 7 收口巡检
- **task-management**：遗留待确认项落 board
- **code-to-doc**：归档完成后按需反解 current-state（外部仓走 dev-paste）
