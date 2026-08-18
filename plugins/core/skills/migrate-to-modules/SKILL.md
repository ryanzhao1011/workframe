---
name: migrate-to-modules
description: 将老项目（无 modules/ 体系）一次性迁移到 modules/ 体系。四步流程：模块树设计（人工+AI 推荐）→ dry-run 输出 file→target 映射 → 用户 review + 调整 → 用户确认后自动搬 + 写元数据 + 全量索引重建。前置必须做 grep + 文件清单 + 引用替换 SOP。触发词：迁移到 modules、迁移老项目、migrate-to-modules、modules 体系迁移。
when_to_use: |
  存量项目（散落 PRD / spec / decisions）首次启用 modules/ 体系时；
  现有 docs/ 或散落需求资产需统一收编到 modules/ 时；
  用户主动调用 `/core:migrate-to-modules` 时。
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# Migrate to Modules Skill

## 前置依赖

调用本 skill 前需读 skill: `document-norms` §1（归属矩阵）/ §3（三段制 overview）/ §4（索引层级）/ §8（修改 SOP，含删除前 grep）。

参考插件根 `reference/module-architecture.md`（modules/ 体系完整设计）。

## 定位

老项目一次性迁移到 modules/ 体系的统一入口。**强约束**：必须 dry-run 用户 review 后才执行实际搬运。

**适用范围**：

- 存量项目（典型场景：含散落 PRD / spec / decisions / plans / obsidian 归属规范文件等）
- 已有代码 + 散落需求文档的工程仓库
- 项目首次启用 modules/ 体系（一次性，不重复执行）

**不适用**：

- 创建单个新模块/需求 → 调 `module-init`
- 修改已有 modules/ 结构 → 直接 Edit + 调 `module-index-refresh`
- 仅刷新索引 → 调 `module-index-refresh`

## 输入

| 输入 | 说明 |
|---|---|
| 老资产位置（必需）| 列出 `docs/` / `projects/specs/` / `obsidian-knowledge.md` 等待迁源。**逐条标注项目内 / 项目外**——决定搬还是拷，见下 |
| 目标 modules/ 路径根 | 默认 `projects/modules/` |
| 模块树初稿（可选）| 用户给定基础模块/子模块列表；不给则 AI 推荐 |
| 是否保留 `_legacy/` 兜底 | 默认是；保留 `projects/_legacy/<old-path>/` 备份 |

**跨项目导入 = 只拷不删（硬约束）**：待迁源在当前项目**之外**时（典型场景：用户新建项目、
把老项目资料喂进来），源目录一律**只读**——禁止移动、删除、写入，`_legacy/` 备份与
「删除老项目顶层 `docs/`」等清源动作**全部不适用**。那是用户仍在使用的仓库，动它一个字都是越界。
本 skill 默认的「搬」只对项目内的源成立。

## 工作流（4 步 + 强 SOP）

### Step 1: 模块树设计（人工 + AI 推荐）

#### 1.1 老资产盘点

- 全量列出待迁源文件（递归 .md / .yaml / asset / png / pdf）
- 按目录分组统计数量
- 报告：

  ```
  待迁源盘点：
    docs/                 (43 .md, 12 png, 3 pdf)
    docs/decisions/       (8 .md)
    docs/plans/           (15 .md)
    .claude/rules/local/obsidian-knowledge.md
    projects/specs/auth/  (5 .md)
  ```

#### 1.2 AI 推荐基础模块树

读全部 `.md` 的 H1 + frontmatter `module` / `tags` / `area`，聚类推荐基础模块：

```
推荐基础模块（10 个，按文档密度排序）：
  1. profile      （档案与个性化，14 文档）
  2. messaging    （消息与通知，10 文档）
  3. payment      （支付与结算，8 文档）
  ...
```

#### 1.3 用户 review + 调整

用户可：

- 重命名基础模块
- 合并/拆分基础模块
- 提供子模块拆分建议（按需）
- 标记某些资产"不进 modules/，留在 specs/plans/" 或 "归档到 _legacy/"

输出：用户确认的模块树 + 子模块拆分（如果用户给）。

### Step 2: dry-run 映射（绝对禁止跳过）

#### 2.1 生成 file→target 映射

对每个待迁源文件，根据：

- 模块树
- frontmatter `module` / `tags` / `area`
- 文件路径关键词
- H1 标题语义

推断目标路径：

```
file → target 映射 dry-run（103 文件）：

✅ 自动推断高置信（72 文件）：
  docs/auth/login-flow.md  →  modules/auth/login/requirements/login-flow/main/prd.md
  docs/decisions/0042-jwt-vs-session.md  →  modules/auth/login/decisions/2024-12-08-jwt-vs-session.md
  ...

⚠️ 自动推断低置信（18 文件）：
  docs/misc/bot-policy.md  →  modules/messaging/bot/research/bot-policy.md ?
  ...

❓ 无法推断（8 文件）：
  docs/old-architecture-2023.md  →  ???
  ...

📦 建议归档 _legacy/（5 文件）：
  docs/2022-prd-discarded.md  →  _legacy/docs/2022-prd-discarded.md
```

#### 2.2 引用扫描（强 SOP）

参考 `document-norms` §8.3 删除/重命名 SOP，**对每个待删除路径**执行：

```bash
grep -rln "<old-path-or-keyword>" .claude/ projects/ company-context/ my-workspace/
```

报告所有引用：

```
docs/auth/login-flow.md 被以下文件引用（4 处）：
  - docs/auth/overview.md:12
  - .claude/rules/local/obsidian-knowledge.md:67
  - projects/board.yaml:tasks[15].notes
  - Home.md:23
```

#### 2.3 输出 dry-run 报告

完整 dry-run 报告写到 `projects/_migration-dryrun-{{TODAY}}.md`，含：

- 模块树（最终版）
- file→target 映射（高置信 / 低置信 / 无法推断 / 归档）
- 每个待删源的引用扫描结果
- 引用替换 plan：旧路径 → 新路径

### Step 3: 用户 review + 调整

**强约束**：用户必须确认 dry-run 后才进入 Step 4。

用户可：

- 调整 file→target 映射（特别是低置信和无法推断的）
- 修改归档清单
- 修改引用替换 plan

确认机制：

- 用户回复 "✅ 确认执行" 才继续
- 任何"不确定"/"我再想想" → 暂停，等用户重新触发

### Step 4: 自动搬运 + 元数据 + 索引重建

按用户确认的 plan 顺序执行：

#### 4.1 创建目标骨架

调 `module-init` 子例程为每个新基础模块/子模块/需求创建骨架（不复制内容，只建空骨架）。

#### 4.2 文件搬运

对每个 file→target 映射：

1. Read 源文件
2. 解析现有 frontmatter（保留语义字段如 `status` / `updated`）
3. 补全/调整 frontmatter（按 `document-norms` §2 标准）：
   - 加 `module: <basic>/<sub>`
   - 加 `req_slug`（仅需求级文档）
   - 加 `sub_req_slug: main`（仅需求级文档；需求层用 `req_slug` + `sub_req_slug` 二字段引用契约，详见 `document-norms` §2.6）
   - 调整 `type` 到合规取值
4. Write 到 target 路径
5. **不删除源文件**（Step 4.4 统一删）

#### 4.3 引用替换

按 dry-run 引用替换 plan，逐文件 Edit：

- wikilink `[[old-path]]` → `[[new-path]]`
- markdown link `(old-path)` → `(new-path)`
- yaml `path: old-path` → `path: new-path`
- 普通文本路径 → 新路径（仅当语义明确）

替换策略：用 `Grep` 找全引用 + `Edit` 改每处；改完后再次 `Grep` 验证 0 残留。

#### 4.4 删除源文件 + 归档

按用户 plan：

- 标记"删除"的 → 移到 `projects/_legacy/<old-path>/`（保留兜底，不直接 rm）
- 标记"归档"的 → 直接到 `_legacy/`
- 整目录删除（如老 `docs/`）：先 `_legacy/<dir>/` + 二次 grep 验证 0 引用 → 才 rm 原目录

**老项目顶层 `docs/` 删除特殊约束**：必须按 skill: `document-norms` §8.3 删除/重命名 SOP 走完（含 grep / 备份到 `_legacy/` / 引用替换 0 残留 / 二次 grep 验证）。

#### 4.5 全量索引重建

调 `module-index-refresh`（全量模式）重建所有层级 overview 索引段。

#### 4.6 反向索引初始化

对每个新建子模块且 `code_paths` 非空，跑 CLI：
```bash
python "$(cat .claude/workframe-state/plugin-root.txt)/scripts/check-stale-modules.py" init-submodule <basic>/<sub>
```
（与 module-init Step 2a 中脚本内部的反向索引初始化语义一致；不要直接 `from check_stale_modules import ...`，本 skill `allowed-tools` 不含 Python import 能力）

#### 4.7 巡检收尾

迁移是断链最高发的操作（批量挪位 + 引用改写），索引重建后必须全库巡检验收：

```bash
python "$(cat .claude/workframe-state/plugin-root.txt)/skills/doc-graph-health/scripts/graph_health.py"
```

- 断链 / 孤儿指向**本次迁移涉及的文件** → 当场修复（引用漏改 / 映射错位），修完重跑确认
- 与本次迁移无关的存量问题 → 不在本 skill 内扩大处置，列入报告「待人工跟进」由用户排期
- 报告解读与处置口径见 core skill `doc-graph-health`

#### 4.8 报告

```
✅ 迁移完成

📦 已搬运：97 文件
  modules/profile/       (14 文件)
  modules/payment/       (8 文件)
  ...

🗄️ 已归档到 _legacy/：6 文件

🗑️ 待删除（已 _legacy/ 备份，确认无误后可 rm）：
  - docs/ 整目录（移到 _legacy/docs/）
  - .claude/rules/local/obsidian-knowledge.md（已替换引用 0 残留）

🔗 引用替换：143 处替换，0 残留

📊 索引重建：12 个 overview 已刷新

🩺 巡检收尾：断链 0 / 孤儿 0（迁移相关）；存量遗留 N 项见待人工跟进

⚠️ 待人工跟进：
  - <basic>/<sub>/submodule.yaml 的 code_paths 待用户填充
  - 8 个文件标记 confidence=low，需 PM/Dev 复核

💾 完整 dry-run 记录：projects/_migration-dryrun-{{TODAY}}.md
💾 引用替换日志：projects/_migration-replacements-{{TODAY}}.md
```

## 输出

- 完成迁移的 modules/ 树
- `_legacy/` 备份兜底
- 全量重建的索引段
- 引用替换日志
- 待人工跟进清单

## 质量自检

- [ ] dry-run 报告已生成且用户确认
- [ ] 每个待删源都做了引用扫描
- [ ] 引用替换后 grep 验证 0 残留
- [ ] 删除整目录前先 `_legacy/` 备份
- [ ] frontmatter 补全合规（§2）
- [ ] 全量索引段已重建
- [ ] 反向索引段已初始化（子模块 code_paths 非空）
- [ ] 巡检收尾已跑：迁移涉及文件零断链零孤儿（存量遗留另列跟进）

## 反模式

- ❌ **跳过 dry-run** 直接搬（绝对禁止；这是数据安全底线）
- ❌ 整目录直接 rm（必须 `_legacy/` 备份 + 二次 grep）
- ❌ 跳过引用替换（grep 0 引用前不能删）
- ❌ 不刷新索引（`module-index-refresh` 全量必调）
- ❌ 重复执行 migrate-to-modules（一次性工具；二次需要专用迁移 plan）
- ❌ 删除老项目内 `.claude/rules/local/obsidian-knowledge.md` 类归属规范文件不先 grep 引用
- ❌ 删除老项目顶层 `docs/` 整目录不做完整迁移校验（必须按 skill: `document-norms` §8.3 删除/重命名 SOP 全走完——grep / 备份 `_legacy/` / 替换 / 二次 grep 4 子步）

完整反模式见 skill: `document-norms` §10。

## 与其他 skill 的衔接

- **document-norms** §1 §2 §3 §4 §8：归属 / frontmatter / overview / 索引 / 修改 SOP（前置）
- **module-init**：Step 4.1 子例程（创建空骨架）
- **module-index-refresh**：Step 4.5 全量重建
- **code-to-doc**：迁移完成后建议调用，从代码反向生成 current-state/
- **obsidian-link-audit**：Step 4.3 引用替换前后建议调用查 broken-link
- **check-stale-modules.py**：Step 4.6 反向索引初始化
- **doc-graph-health**：Step 4.7 巡检收尾（全库级验收，与 4.3 的单点 link-audit 互补）

## 失败模式与降级

| 失败 | 降级 |
|---|---|
| 用户未确认 Step 3 | 暂停在 Step 2 输出 dry-run；不进入 Step 4 |
| Step 4.2 部分文件搬运失败 | 已搬部分保留 + 报告未搬清单；用户决定补搬或回滚 |
| Step 4.3 引用替换 grep 残留 > 0 | 报告残留 + 暂停 Step 4.4 删除（必须 0 残留才删）|
| Step 4.5 索引重建失败 | 报告失败 overview，提示用户手动 `/core:module-index-refresh` |
| `_legacy/` 已存在同名 | 加时间戳后缀避免覆盖（`<old-path>-{{TODAY}}/`）|

## 一次性约束

本 skill 设计为**一次性**工具：项目首次启用 modules/ 体系时执行。**重复执行**应慎重——结构已存在时必须重新设计 dry-run，避免产生混乱目录结构。
