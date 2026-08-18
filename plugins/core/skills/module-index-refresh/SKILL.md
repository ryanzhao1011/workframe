---
name: module-index-refresh
description: 递归刷新 modules/ 体系各层 overview.md 的机器维护索引段（基于 HTML 注释边界 `<!-- WORKFRAME:AUTO-INDEX:START/END -->`）。严格只重写 START/END 之间内容；段外人写部分（positioning + 中间内容）零接触。支持限定路径增量刷新与全量递归两种模式。触发词：刷新模块索引、重建 overview、module-index-refresh、同步 overview 索引。
when_to_use: |
  modules/ 体系下：
  - 创建/修改/删除子模块或需求后（module-init 内部已自动调用）；
  - 修改 submodule.yaml.status / meta.yaml.status 后手动同步上级；
  - 大规模迁移完成后全量重建（migrate-to-modules 内部已自动调用）；
  - 用户主动调用 `/core:module-index-refresh` 手动兜底。
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

### Step 1.5: 顶部两层段先交给脚本（单一实现，防漂移）

`modules/overview.md` 的 basic-modules-index 与 `<basic>/overview.md` 的 submodules-index
这两类**匿名段**由 `module_init.py` 承包（它建树时也重建同两段，生成规则只此一份）：

```bash
# 全量：重建 global + 所有 basic 的段；限定：加 --basic "<name>"
# 插件根从 plugin-root.txt 取（SessionStart hook 每会话刷新），不依赖环境变量
python "$(cat .claude/workframe-state/plugin-root.txt)/scripts/module_init.py" --project "<项目根>" --refresh-index [--basic "<name>"]
```

跑完把输出转述（「已重建 / 无变化」逐行），然后 Step 2-4 **只处理其余层级的段**：
sub overview 的具名段（current-state-summary / requirements-index）、requirements overview
的 requirements-by-status、`<req_slug>/overview.md` 的 sub-requirements-index。
脚本不可达时按 Step 2-4 模型兜底处理全部段（含顶部两层），并在报告中注明降级。

### Step 2: 数据收集

按层级读取数据源：

| overview 层级 | 数据源 |
|---|---|
| `modules/overview.md` basic-modules-index | 所有 `<basic>/module.yaml` 的 `name` / `status` / 摘要（取自 overview.md positioning 段首句）|
| `<basic>/overview.md` submodules-index | 该 basic 下所有 `<sub>/submodule.yaml` 的 `name` / `status` / 摘要 |
| `<sub>/overview.md` current-state-summary | 4 个 `current-state/*.md` 的 frontmatter（updated / confidence / source_paths）+ 第一个 H2 段首句。逐行取数：**架构 / API / 数据模型 / 代码索引**=对应文件第一个 H2 段首句，文件缺失则保留 `_（待 code-to-doc 生成 ...）_` 占位；**最近同步**=`submodule.yaml.last_synced_at`（不是 4 个 current-state 文件的 `updated`——那是文档改动时间，前者才是代码反解时间），键缺失或为空则 `_（未同步）_` |
| `<sub>/overview.md` requirements-index | 该 sub 下所有 `requirements/<req_slug>/meta.yaml` 的关键字段 |
| `<sub>/requirements/overview.md` requirements-by-status | 同上，按 status 分成**五张表**（active / planning / done / dropped + `_未知_`）。`_未知_` 表收 `meta.yaml` 缺 status 或取值不在四态内的需求，列为 req_slug / 实际取值 / owner——它和四张状态表一样在 AUTO-INDEX 段**内**渲染（曾把它放在 END 之外，而本 skill 的硬纪律是段外零接触，那张表于是永远不会被填）。四张状态表逐列取数：**子需求数**=该 `<req_slug>/` 下 `<sub_req_slug>/` 目录数；**owner**=`meta.yaml.owner`；**摘要**=按下方摘要提取链；**完成时间 / 放弃时间**=`meta.yaml.updated_at`（纪律见 skill: `document-norms` §2.7「status 终态翻转」；只改 status 不动 updated_at 时这一列会显示创建日期，属已知失真）；**原因**=`meta.yaml.notes`，空则 `-` |
| `<req_slug>/overview.md` sub-requirements-index | 子需求目录列表（含 `main/` + 拆出的其他子需求）+ 各 `<sub_req_slug>/prd.md` frontmatter。逐列取数：**子需求**=目录名；**状态**=`status`；**摘要**=`description`（按下方摘要提取链）；**PRD**=`prd.md` 存在则链接该文件，缺则 `_缺 prd.md_`；**复盘**=`reviews/` 下有 `.gitkeep` 以外的文件才链接该目录，否则 `-`（骨架自带 `.gitkeep` 占位，把它算成「有复盘」会让每个新需求都显示已复盘） |

**缺 PRD 处理**：若某 `<sub_req_slug>/` 目录存在但 `prd.md` 缺失（仅有 test-cases / prototypes 等），sub-requirements-index 段渲染该行时 status / 摘要列填 `_缺 prd.md_`，并在 Step 6 报告中追加 warning `⚠️ <req_slug>/<sub_req_slug>/ 缺 prd.md`。**不用父 meta 兜底**——父需求 status 无法代表子需求状态。

**摘要提取**：

- 优先取 overview.md positioning 段（H2 "定位" 后）首句
- 其次取 frontmatter `description` 或 `notes`
- 截断 ≤80 字符；超出加 `…`

### Step 3: 渲染索引段

用模板渲染受影响 overview 文件的索引段。模板示例：

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

1. Read 整个文件
2. **按 named 段配对替换**（避免贪婪匹配跨段吞数据）：
   - **匿名段**（单段文件，如 basic-module overview / requirement overview）：regex `<!-- WORKFRAME:AUTO-INDEX:START -->[\s\S]*?<!-- WORKFRAME:AUTO-INDEX:END -->`
   - **命名段**（多段文件，如 sub-module overview 同时含 `current-state-summary` + `requirements-index`）：regex `<!-- WORKFRAME:AUTO-INDEX:START:<name> -->[\s\S]*?<!-- WORKFRAME:AUTO-INDEX:END:<name> -->`，**必须用具名 END 配对**
   - **严禁用 `START.*?END -->` 通配 regex**：会跨段贪婪吞掉中间所有内容（包括另一个段的 START 标记 + 段外人写部分）
3. 替换为 Step 3 渲染结果
4. **校验**：替换前后段外字节数应一致（前段 + 后段相同）；不一致时报错回滚
5. 写回（推荐 Edit 工具替换该段；多段同文件时合并一次 Write）
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
| 找不到 START/END 配对 | 报错并跳过该文件（不破坏文件）+ 提示用户检查边界格式 |
| 段外字节数前后不一致 | 回滚（不写入）+ 报错 |
| `auto_sections` 缺失 | 用默认 sections（按 overview_level 推断）|
| 数据源 yaml 解析失败 | 跳过该模块 + 报告警告，其他正常处理 |
