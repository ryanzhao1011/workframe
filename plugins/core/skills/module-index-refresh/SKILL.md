---
name: module-index-refresh
description: '递归刷新 modules/ 体系各层 overview.md 的机器维护索引段（基于 HTML 注释边界 `<!-- WORKFRAME:AUTO-INDEX:START/END -->`）。严格只重写 START/END 之间内容；段外人写部分（positioning + 中间内容）零接触。支持限定路径增量刷新与全量递归两种模式。触发词：刷新模块索引、重建 overview、module-index-refresh、同步 overview 索引。用于创建/修改/删除子模块或需求后（module-init 内部已自动调用）、修改 submodule.yaml.status / meta.yaml.status 后手动同步上级、大规模迁移完成后全量重建（migrate-to-modules 内部已自动调用）、用户主动调用 `/core:module-index-refresh` 手动兜底。'
user-invocable: true
effort: low
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# Module Index Refresh Skill

## 前置依赖

调用本 skill 前需读 skill: `document-norms` §3（机器维护段边界格式）/ §4（索引层级与同步规则）。

## 定位

modules/ 体系下机器维护索引段的唯一刷新入口。

**适用范围**：

- 全局 `projects/modules/overview.md` 的 `basic-modules-index` 段
- 基础模块 `<basic>/overview.md` 的 `submodules-index` 段
- 子模块 `<sub>/overview.md` 的 `current-state-summary` + `requirements-index` 段
- 子模块需求清单 `<sub>/requirements/overview.md` 的 `requirements-by-status` 段
- 需求 `<req_slug>/overview.md` 的 `sub-requirements-index` 段

**严格保证**：

- 只重写 `<!-- WORKFRAME:AUTO-INDEX:START[:<name>] -->` 与配对 `<!-- WORKFRAME:AUTO-INDEX:END[:<name>] -->` 之间内容（`:<name>` 可选；命名段格式见 document-norms §3.3 + 子模块 overview 模板的 `current-state-summary` / `requirements-index` 双段示例）
- 段外人写部分（positioning / 中间内容）零接触
- 同输入 → 同输出（可 diff 复现）

**不适用**：

- 修改 frontmatter `updated` 字段 → 由 `obsidian-safe-write` 或文档创建 skill 维护
- 修改 PRD 内容 → 由 `prd-writer`
- 修改 current-state/ 内容 → 由 `code-to-doc`
- 修复 broken link → 由 `obsidian-link-audit`

## 输入

| 模式 | 输入 |
|---|---|
| 增量（推荐）| 限定路径，如 `<basic>` / `<basic>/<sub>` / `<basic>/<sub>/requirements/<req_slug>` |
| 全量 | 无输入（递归 `projects/modules/`）|

## 工作流

### Step 1: 路径解析与影响范围

按输入路径计算需刷新的 overview 文件：

| 输入路径 | 受影响 overview |
|---|---|
| 全量 | `modules/overview.md` + 所有 `<basic>/overview.md` + 所有 `<sub>/overview.md` + 所有 `requirements/overview.md` + 所有 `<req_slug>/overview.md` |
| `<basic>` | `modules/overview.md` + `<basic>/overview.md` |
| `<basic>/<sub>` | `modules/overview.md` + `<basic>/overview.md` + `<basic>/<sub>/overview.md` + `<basic>/<sub>/requirements/overview.md` |
| `<basic>/<sub>/requirements/<req_slug>` | 上一行 + `<req_slug>/overview.md` |

**优化原则**：能只刷一个子模块就不全量刷。同一文件的多个 `auto_sections` 一次性处理，不重复读写。

### Step 1.5: 全部段交给脚本（单一实现，防漂移）

六类段都由 `module_init.py` 渲染与写入（规则只在 `_module_nav.py` 一处；建树时也调同一入口），
模型不渲染、不手改段体：

```bash
# $WF_PLUGIN_ROOT = 会话开场注入的「本会话插件根」，执行前换成那个路径
R="$WF_PLUGIN_ROOT/scripts/module_init.py"
python -B "$R" --project . --refresh-index [--scope <Step 1 的输入路径或刚写的文件>…]   # 写有差异的段
python -B "$R" --project . --check [--scope …]                                          # 零写入比较
python -B "$R" --project . --convert-layout [--scope …]                                 # 旧版式转换预览
```

- **退出码**：`--refresh-index` 0 完成 / 3 有冲突（其余已写，冲突那份未写）/ 2 参数或段问题；
  `--check` 0 无差异 / 1 有差异或旧版式 / 2 无法检查。`--scope` 空或非法即 2，不退化成全量
- **旧版式段**（后四类段的旧表头、旧说明行）`--refresh-index` 只报「旧版式，待转换」、不写。
  转换：跑 `--convert-layout` 预览，把**将消失的旧行、项目自加的列及其有值单元格、基础模块总览的
  示例图**逐项呈给用户；用户确认后带预览给的确认码 `--apply --confirm <码>`（示例图按 `--choice
  <路径>=replace-example|keep` 逐份选，预览与执行带同样的选项）。确认码不符整批拒绝、零写入
- 跑完把输出转述（逐行 written / legacy / conflict / problem），problem 行按原因码报给用户
- 脚本不可达时**不写**：按 Step 2 的取数表列出可能过期的段与理由，在报告中注明降级

### Step 2: 数据收集

按层级读取数据源：

| overview 层级 | 数据源 |
|---|---|
| `modules/overview.md` basic-modules-index | 所有 `<basic>/module.yaml` 的 `name` / `status` / 摘要（取自 overview.md positioning 段首句）|
| `<basic>/overview.md` submodules-index | 该 basic 下所有 `<sub>/submodule.yaml` 的 `name` / `status` / 摘要 |
| `<sub>/overview.md` current-state-summary | 行语法 `- **<标签>**：<机器 token>[；<人写尾注>]`：机器 token 是「：」之后到第一个「；」之前，**尾注原样保留**；找不到标签的行原样保留。**架构 / API / 数据模型 / 代码索引**：对应 `current-state/*.md` 判为 `written` 或 `unstamped`（后者按已编写展示）→ token 换成该文件 frontmatter 的 **`description`**（清洗后 ≤80 字，「；」换成「，」；没填 → `_（未填摘要）_`，不回落到正文首句）；`unwritten` 或文件不存在 → token 换成 `_（未编写）_`，**不保留旧的真实摘要**（否则文档退回骨架后导航仍显示旧结论）；文件读不了 → 整行不动。**最近同步**=`submodule.yaml.last_synced_at` 的日期（不是 4 个 current-state 文件的 `updated`——那是文档改动时间，前者才是代码反解时间），键缺失或为空则 `_（未同步）_`；同行再读 `submodule.yaml.last_synced_ref`，非空则在日期后附括注，**新写时形态为 `- **最近同步**：<日期>（基准 commit <ref>）`**，ref 为空则不渲染空括注。**本行机器只拥有两个 token——日期与 ref**：①只就地替换这两个 token，**行内其余文字一律不动**；②括注里当前提不出 ref 而 `last_synced_ref` 非空时，把括注**插在日期之后、行内既有其余文字之前**；③`last_synced_ref` 由非空变空时，只移除 ref 与它所属的「基准 commit …」短语，**括注里的其余文字仍不动**。这个括注是基准引用的**第三个落点**：`module-close-check` 的基准同值性检查按此形态从 overview 提取它；不产出它，三方对账静默降为两方，**不报错**。**版式**：段首说明行是 v2 那句（写明「；」之后归人写）即 v2，否则是旧版式、只报不写 |
| `<sub>/overview.md` requirements-index | 该 sub 下各 `requirements/<req_slug>/meta.yaml.status` 的计数：一行「进行中 a · 规划 b · 已完成 c · 已放弃 d · 状态未知 e」＋「完整清单见 requirements/overview.md」——**完整清单只在后者**，这里不复制。带旧表格（`req_slug | status | …`）的是旧版式 |
| `<sub>/requirements/overview.md` requirements-by-status | 同上，按 status 分成**五张表**（active / planning / done / dropped + `_未知_`）。`_未知_` 表收 `meta.yaml` 缺 status 或取值不在四态内的需求，列为 req_slug / 实际取值 / owner / 摘要——它和四张状态表一样在 AUTO-INDEX 段**内**渲染（曾把它放在 END 之外，而本 skill 的硬纪律是段外零接触，那张表于是永远不会被填）。四张状态表逐列取数：**子需求数**=该 `<req_slug>/` 下 `<sub_req_slug>/` 目录数；**owner**=`meta.yaml.owner`；**摘要**=`main/prd.md` 的 `description`（PRD 未处于未编写状态时）→ 需求 overview 的 `description` → `-`，只清洗与截断到 80 字、不切句（五张表都有这一列）；**完成时间 / 放弃时间**=`meta.yaml.updated_at`（纪律见 skill: `document-norms` §2.7「status 终态翻转」；只改 status 不动 updated_at 时这一列会显示创建日期，属已知失真）；**原因**=`meta.yaml.notes`，空则 `-` |
| `<req_slug>/overview.md` sub-requirements-index | 子需求目录列表（含 `main/` + 拆出的其他子需求），表头 `子需求 | 状态 | 摘要 | PRD | 评审 | 用例`。逐列取数：**子需求**=目录名；**状态**=PRD 判为 `unwritten` →「已立项、未开写」，否则 PRD `status` 原值（需求级状态在需求清单里显示 `meta.yaml.status` 原值，两者不互译）；**摘要**=PRD `description`（PRD 未编写时 `-`）；**PRD**=`prd.md` 存在则链接该文件，缺则状态与摘要两列写 `_缺 prd.md_`；**评审 / 用例**=`reviews/`、`test-cases/` 下（含子目录）`.gitkeep` 等点文件以外的文件，按相对路径排序**逐个链接到文件**、不截断（Mortar 只接受点击文件，链接目录点不开；骨架自带 `.gitkeep`，把它算进去会让每个新需求都显示已评审），链接目标用 `<相对路径>` 形式以容下空格与括号，没有则 `-`。表头是旧的「复盘」列即旧版式 |

**所有权边界（AUTO-INDEX 段内的唯一规则）**：

> **段内机器拥有的是「有数据源的 token」，不是整行。** 渲染时只写它有数据的那些 token，其余原样保留。每一行的 token 边界由上方取数表逐行给出。

这一条取代了此前「机器全权重写 + 一条行级例外」的表述——那种写法给不出「下一行算不算例外」的判据。按本规则逐行推导，四类行的行为与现状逐一相符：

| 段内的行 | 有数据源的 token | 导出的行为 |
|---|---|---|
| 架构 / API / 数据模型 / 代码索引 行 | 「：」到第一个「；」之间的摘要 token（未编写时是 `_（未编写）_`）；斜体占位 `_（…）_` 整个算 token | 只换 token；「；」之后的尾注原样保留，旧占位里「；」之后的注记挪成尾注 |
| 最近同步行 | 日期、ref 两个 | 只换这两个，行内其余文字不动 |
| 段体第一行的说明行（`> ⚙️ 此段由 …`） | 整行 | 换成当前版式的说明；段体里别处的说明行归人写、原样保留 |
| 各索引表格行（requirements-index 等） | 每一列都有源 | 整行重写 |

**怎么用它回答新问题**：先逐 token 问「这一处有没有数据源」。以「requirements-index 某一列能不能留人写文字」为例——五列全部有源（**空值也是数据**，如 `原因` 列取 `meta.yaml.notes`、空则渲染 `-`），故答案是不能；想在那儿留人写内容只有两条出路：给它一个数据源（加字段），或把内容挪到 AUTO-INDEX 段外。

**新增行类型时的义务**：必须在上方取数表里显式写出该行的 token 边界，否则渲染方无从判断哪部分归机器。

**缺 PRD 处理**：若某 `<sub_req_slug>/` 目录存在但 `prd.md` 缺失（仅有 test-cases / prototypes 等），sub-requirements-index 段渲染该行时 status / 摘要列填 `_缺 prd.md_`，并在 Step 6 报告中追加 warning `⚠️ <req_slug>/<sub_req_slug>/ 缺 prd.md`。**不用父 meta 兜底**——父需求 status 无法代表子需求状态。

**摘要提取**：

- 优先取 overview.md positioning 段（H2 "定位" 后）首句
- 其次取 frontmatter `description` 或 `notes`
- 截断 ≤80 字符；超出加 `…`

### Step 3: 渲染索引段

渲染由脚本完成（Step 1.5），模型不渲染、不照下面的示例手写段体。产出形态示例（全局 overview 的段）：

**basic-modules-index**：

```markdown
<!-- WORKFRAME:AUTO-INDEX:START -->
> ⚙️ 此段由 `module-index-refresh` 自动维护，请勿手动编辑。

| 基础模块 | 状态 | 摘要 |
|---|---|---|
| [profile](./profile/overview.md) | active | 用户档案与个性化 |
| [payment](./payment/overview.md) | planning | 支付与结算 |

<!-- WORKFRAME:AUTO-INDEX:END -->
```

**全局 overview "摘要+下沉"原则**：只放基础模块摘要+链接，不展开子模块详情。

### Step 4: 安全替换索引段

对每个受影响 overview 文件：

1. 脚本读整个文件，按原字节定位段（BOM 与行尾风格原样保留）
2. **按 named 段配对**（只认独占一行的 START / END，代码围栏与行内代码里演示的标记不算）：
   - **匿名段**（单段文件，如 basic-module overview / requirement overview）：regex `<!-- WORKFRAME:AUTO-INDEX:START -->[\s\S]*?<!-- WORKFRAME:AUTO-INDEX:END -->`
   - **命名段**（多段文件，如 sub-module overview 同时含 `current-state-summary` + `requirements-index`）：regex `<!-- WORKFRAME:AUTO-INDEX:START:<name> -->[\s\S]*?<!-- WORKFRAME:AUTO-INDEX:END:<name> -->`，**必须用具名 END 配对**
   - **严禁用 `START.*?END -->` 通配 regex**：会跨段贪婪吞掉中间所有内容（包括另一个段的 START 标记 + 段外人写部分）
3. 用渲染结果替换段体；段结构有问题（缺段、不配对、重复、未知段）或取数源读不了的文件**整份不写**，只报诊断
4. **校验**：写前在锁内重读并比对规划时的文件摘要，被改过就报 `conflict`、该文件零写入；段外字节由「前段 + 新段 + 后段」的拼法逐字节保全
5. 写回走临时文件 ＋ `os.replace`，多段同文件一次写完。**模型不用 Edit / Write 改段体**——那条路绕过版式判定、确认码与冲突检测
6. 多 named 段并存时按 §3.3 语法各自独立替换（document-norms §3.3 命名段语法权威）

### Step 5: frontmatter 不更新

**重要**：本 skill **不更新** frontmatter `updated` 字段。理由：索引段刷新属于"系统维护操作"（详见 skill: `document-norms` §7.1），不应混淆"实质性产出"。`updated` 只在人工编辑或业务 skill 写入时刷新。

例外：如 `auto_sections` 列表本身有变更（如新增段），可同步更新 frontmatter `auto_sections` 字段，但不刷新 `updated`。

### Step 6: 报告

```
✅ 已刷新索引段：
  - projects/modules/overview.md (basic-modules-index)
  - projects/modules/profile/overview.md (submodules-index)
  - projects/modules/profile/edit/overview.md (current-state-summary, requirements-index)

📊 数据规模：
  - 基础模块：5
  - 子模块：12
  - 需求：8 active / 3 planning / 2 done

⚠️ 警告：
  - <basic>/<sub>/overview.md 缺 auto_sections 字段，已使用默认值
  - <sub>/requirements/<req_slug>/meta.yaml 缺 status 字段，已渲染为 _未知_
  - <req_slug>/<sub_req_slug>/ 缺 prd.md（详见 §Step 2 缺 PRD 处理）
```

## 输出

- 受影响 overview 文件的索引段已刷新
- 段外内容零变更
- 数据规模 + 警告报告

## 质量自检

- [ ] 段外 positioning + 中间内容字节数一致
- [ ] 同输入 → 同输出（运行两次 diff 应为空）
- [ ] 摘要 ≤80 字符
- [ ] 全局 overview 不展开子模块详情
- [ ] HTML 注释边界格式严格匹配（§3.3）
- [ ] frontmatter `updated` 字段未被本 skill 修改

## 反模式

- ❌ 写入边界外内容（positioning / 中间内容必须零接触）
- ❌ 全局 overview 展开子模块详情（必须只放基础模块摘要+链接）
- ❌ 修改 frontmatter `updated` 字段（系统维护操作不应触发"已修改"信号）
- ❌ 用 sed / awk 直接替换文本而不做边界校验（必须读全文 + regex match + 字节数校验）
- ❌ 跳过 `<!-- WORKFRAME:AUTO-INDEX:END -->` 配对检查（缺 END 时整文件被覆盖是灾难）

## 与其他 skill 的衔接

- **document-norms** §3 §4：边界格式 + 索引同步规则（前置）
- **module-init**：Step 2b 第 4 步调用本 skill 同步需求层索引（basic / sub 两层索引由脚本直接重建，不经本 skill）
- **migrate-to-modules**：完成迁移后调用本 skill 全量重建
- **obsidian-link-audit**：刷新后建议调用以查 broken-link

## 失败模式与降级

| 失败 | 降级 |
|---|---|
| 找不到 START/END 配对 | 脚本报 `problem`（原因码 `unpaired` / `missing-segment` 等）并整份跳过该文件，退出 2；提示用户检查边界格式 |
| 段外字节数前后不一致 | 回滚（不写入）+ 报错 |
| `auto_sections` 缺失 | 用默认 sections（按 overview_level 推断）|
| 数据源读不了（编码坏、读失败） | 脚本报 `unreadable-source`，整份跳过引用它的 overview，其他文件正常处理 |
