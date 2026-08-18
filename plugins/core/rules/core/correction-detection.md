---
name: correction-detection
created_at: 2026-04-23
scope: project
action: auto-correct
---

# 用户纠正检测规则

## 检测模式

用户消息出现以下任一类信号即触发本规则：

- **否定**（不是 / 不对 / 错了 / 不应该）
- **修正**（应该是 / 改成 / 用 X 而不是 Y）
- **重复强调**（我说过 / 之前告诉你 / 记住 / 注意）——这类尤其要接住：它意味着同一事实此前已纠正过至少一次而未被记住

## 排除条件（不触发本规则）

纠正的对象是**持久层认知**——跨轮次、跨需求仍然成立的事实 / 口径 / 工作方式 / 偏好。以下不触发：

- **对当前草稿 / 产出的一次性修改意见**（改标题、换措辞、调本篇结构、本方案内的取舍）——这是正常迭代反馈，按 `response-output.md` 的「补充 vs 改写」判定直接修改产出即可，**不写记忆、不发 `user_correction` 事件**
- **判据一句话**：这句话在下一个不相关的任务里还成立吗？成立 → 纠正；只对当前这份产出成立 → 修改意见
- **歧义裁决**：含「以后 / 都 / 每次 / 记住」等持久化信号词 → 按纠正处理；没有 → 按修改意见处理。同一修改意见被用户再次重申时，自然命中上方「重复强调」信号，升级为纠正

## 5 步处理流程

### 第 1 步：识别

提取两个要素：被纠正的**错误认知** + **正确信息**（第 3 步的写入模板需要这两项）。

### 第 2 步：确认回显
向用户确认理解：
> "收到纠正：[错误认知] → [正确信息]。已记录。"

### 第 3 步：写入记忆高置信区（先分流落点，写前先查同主题）

**落点分流**（判据 = 谁消费，同 CLAUDE.md 两套记忆分工契约与 `agent-protocols.md` Step 2 归属判据）：

> **主 Claude 直做时怎么对号入座**：下面四个分支的主语写的是「角色」，但判据从来是**谁消费**、不是谁挨的批评。主 Claude 直做 dev 域的活被纠正了一个工程习惯——消费者是未来做该域活的人（含 @dev subagent），走**第三分支**落 `dev/MEMORY.md`，不因为「这次是我被纠正」就往 auto-memory 塞。auto-memory 只收**协作行为**类（配合方式 / 输出形态 / 工作流程）。<br>拿不准时问一句：*换成 @dev 被派去做同一件事，它需要知道这条吗？* 需要 → 角色域。

- **业务事实 / 需求口径**（消费者是查该模块的人，不是某个角色）→ 正确信息落对应 `projects/modules/` 文档为权威（shared 事实源 / PRD「变更与决策记录」等，归属查 skill: `document-norms` §1）；MEMORY 高置信区只写一行指针条目 `- [纠正] {日期}：{一行结论}（权威口径见 [[<文档路径>]]）`——第 4 步 sidecar 与事件照走，正文不复制
- **主 Claude 协作行为**（配合方式 / 输出形态 / 工作流程——消费者是主 Claude 而非某个角色）→ 写 **auto-memory**（CC 官方记忆，type: feedback；同样先查同主题、supersede 语义）；不写 role/shared，**跳过第 4 步 sidecar**（auto-memory 无 sidecar），事件流仍写（scope 填 `main`、省略 entry_key）
- **角色执行 / 跨角色口径** → 按下文原路径写 role/shared MEMORY 高置信区
- **双消费**（主 Claude 和角色都要遵守）→ `shared/MEMORY.md` 存全文为权威 + auto-memory 只留一行指针（指向 shared 条目，不复制全文）

**写入前必做**：Read 目标记忆文件，检索同主题旧条目（同一事实/口径/对象——表述可能不同，按语义判断），三选一：

- **无同主题** → 正常新增
- **有同主题**（旧 `[纠正]` 或普通条目均算）→ **supersede**：新条目写入高置信区；旧条目整段移入同角色 `notes.md` 作历史记录（标注 `→ 已被 {日期} 纠正取代`）；同步删除旧条目的 memory-index entry（防 stale protected entry 继续参与容量判断）
- **本次纠正与已有条目等价**（无新信息）→ 不重复写入，第 2 步回显时改说「该口径已有记录」

写入格式：
```
- [纠正] {日期}：{正确信息}（用户纠正，原错误认知：{错误认知}）
```

**注意**：用户纠正直接写入高置信区，**跳过 D/U/R/A 准入检查**（查重合并判据同 `librarian/SKILL.md` §融合整理 SOP（第 2.5 步），写时执行）。

### 第 4 步：同步 sidecar 与事件流

**落点为 skill / rule 文件时的例外**：若第 3 步（经第 5 步用户确认）把纠正内容写进了 skill 或 rule 文件而非 MEMORY——**跳过 sidecar**（sidecar 只索引 MEMORY 条目；skill/rule 文件本身是受保护资产（self-iteration 的 L2 变更级别，见 `self-iteration` SKILL.md §L1/L2 判定），保护已由审批机制承担），`user_correction` 事件仍写但**省略 entry_key**，落点文件路径写进 summary。

写入 MEMORY.md 后，同步维护 `.claude/workframe-state/memory-index.json`。若文件不存在，先初始化：
```json
{"__schema__":"workframe.memory-index.v2","entries":{}}
```

为 `[纠正]` 条目写入 sidecar entry。**entry_key 生成规则**（与 `librarian/SKILL.md` 和 `memory-index-template.json` 对齐）：
```
<scope>:<YYYY-MM-DD>:<正确信息前 20 字规范化>
```

规范化规则（三条缺一不可，否则与 librarian 生成的 key 对不上）：**先删除全部空白字符 → 再取前 20 字**（Unicode 字符数，非字节）→ 同日同前 20 字碰撞时追加 `-2` / `-3`。

**顺序不可颠倒**（2026-08-16 统一）：先截断会让空白落在前 20 字内的摘要少算有效字符，两种顺序对同一条目算出不同 key，跨 producer 的 supersede / 去重会 miss。此前规范写的是「先截断后去空白」，与 `maintenance_workorder.py` 及已落盘条目的实际行为不符，现以实际行为为准；**改序前生成的历史 key 保持不变，不回改**。

**20 字是硬截断，不是「大约」**——基线实测有条目整句原文入 key，导致同一事实被 librarian 与本规则算出两个不同 key、sidecar 出现重复项：

| | 正确信息原文 | entry_key 尾段 |
|---|---|---|
| ✅ | 提交必须用 `git commit --only`，该仓禁 amend | `提交必须用gitcommit--only，该仓` ← 数到第 20 个字符即停 |
| ❌ | 同上 | `提交必须用gitcommit--only，该仓禁amend` ← 贪心到语义完整，超长 |

数的是**规范化后**的 Unicode 字符：先删空白，再数 20 个，不管断在哪儿——断在词中间是正常的，key 是标识符不是摘要。

**scope 必须填实际值**（角色名 pm/dev/… 或 shared；主 Claude 层纠正填 `main`）。**基线实测有条目把 `"role"`、`"<role>"` 这类占位符原样写进了 sidecar**——那样的 entry 谁都查不到、也无法参与容量与保护判断，等于白写。下例中的 `"pm"` 是**示例值**，照抄它同样是错的：

```json
{
  "scope": "pm",
  "created_at": "2026-04-19",
  "provenance": "user-decree",
  "protected": true,
  "source": "[纠正]"
}
```

然后 append `.claude/workframe-state/events.jsonl`：
```json
{"ts":"<ISO-8601>","type":"user_correction","scope":"<角色名或shared，填实际值>","role":"<role-if-any>","entry_key":"<memory-index-key>","summary":"<正确信息摘要/前80字>","source":"[纠正]"}
```

主 Claude 层纠正（第 3 步分流进 auto-memory 的）：`scope` 填 `main`、省略 `entry_key` 与 `role`（无 sidecar），其余字段同上——问题权重信号不因落点不同而丢失。事件写入的去重口径统一见下方 §执行位序。

### 第 5 步：通用规则判断（受保护资产边界）

评估该纠正是否具有通用性（不仅限于当前场景）：

- **具有通用性** → 这是 **"用户显式确认"入口**，可创建 / 更新项目级 rule 文件，**严格遵循以下边界**：
  - **允许目标**：`.claude/rules/local/*.md` 或项目专有的 rule 文件
  - **禁止目标**：`.claude/rules/workframe/core/**`（core plugin 同步资产；core 文件变更走 self-iteration L2 提案路径——这是另一个独立入口，与本规则不冲突）
  - **必须先回显摘要并等待用户明确确认才落盘**——受保护资产强约束（与 `auto-update.md` §受保护资产约束一致）
  - 若用户未明确确认 → **不**直接落盘 local rule；建议用户走 `/core:self-iteration` 提案审批流程作为更受控的入口
- **仅限特定场景** → 仅写入 MEMORY.md（已在第 3 步完成）

#### 入口分流（避免与 self-iteration 冲突）

`.claude/rules/local/**` 修改有两条**独立**入口，**不冲突**：

| 入口 | 触发 | 流程 |
|---|---|---|
| 本规则（correction-detection）| 用户消息中的纠正信号 + 用户**明确确认**这是通用规则 | 直接写 local rule（受本规则的强约束保护）|
| `self-iteration` L2 提案 | 模式识别自动提议（含触及 `.claude/rules/**` 的任何变更）| 走 `proposals/pending` → `applied` 提案审批，**不**直接写 |

`.claude/rules/workframe/core/**` 只能由 plugin 源同步，两条入口都不能写。

## 重要原则

- **[纠正] 标记的内容永不清理**：Librarian 整理 MEMORY.md 时，带有 `[纠正]` 标记的条目永远保留。**指针化不算清理**——业务事实类条目把正文移交权威文档、压缩为一行指针后，条目连同标记、日期与 sidecar 保护依然在位
- **[纠正] 条目必须有 sidecar 保护**：`protected=true`、`provenance=user-decree`、`source=[纠正]`
- 用户纠正具有最高权威性，优先级高于任何其他信息源
- 同一主题被多次纠正时，保留最新纠正，旧纠正移入 notes.md 作为历史记录；旧条目移出 MEMORY.md 后，同步删除旧的 memory-index entry，避免 stale protected entry 继续参与容量/衰减判断

## 执行位序

本规则位于 `agent-protocols.md` §同消息内 rule 处理顺序的 **step 1**，最先执行。含否定词 + 修正词的消息一律由本规则消费；写入 `user_correction` event 后，auto-update（step 2）与 agent wrap-up（step 5）均不再为同一条纠正补写，避免重复计数。
