---
name: librarian
description: 记忆整理员：评估 notes.md 条目并按 D/U/R/A 提升到 MEMORY.md（含落点分层与同主题融合）、管理 MEMORY.md 容量候选、维护整理日志与 memory-index sidecar。
when_to_use: |
  SessionStart 询问式开场卡用户选「处理」后（主通道）、`workframe-maintenance`
  批处理工单的 notes 积压项；会话中出现 `memory_backlog` 维护信号（notes 积压待整理）时；
  MEMORY.md 接近容量上限需要评估降级候选时；用户说「整理一下记忆 / notes 太多了」时。
  边界：只看记忆变动流水 → memory-log；纠正类条目由 correction-detection 直写高置信区，不经本 skill。
user-invocable: false
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion]
---

# Librarian 记忆整理技能

## 6 步整理流程

### 第 1 步：读取状态（动态角色发现 + shared 特殊角色）

**先处理 shared/（第 0 号"特殊角色"）**：
```
Read .claude/agent-memory/shared/MEMORY.md（跨角色权威事实；不存在则跳过）
Read .claude/agent-memory/shared/notes.md（共享缓冲；不存在则跳过）
```
shared 层的整理规则与 role 层一致（D/U/R/A 评估、容量管理、快照保存），但写入条件更严：
- 提升到 `shared/MEMORY.md` 必须满足"影响 ≥2 个角色" + D/U/R/A ≥2 项（**主 Claude 不计入角色数**，理由见 2b 三问第 3 问）
- **单条压缩到 ≤200 字符**。shared 是**各 subagent 启动时全量注入**的——每加一句，此后每次委派都要付一次 token；判据是「这句删掉，另一个域的人会不会做错事」，答否就删。展开论证留在 `shared/notes.md` 或权威文档，MEMORY 只留结论
- **shared 变更在整理日志中单列**（与 role 层变更分开列，标明「本轮 shared 新增/修改 N 条」+ 逐条一行摘要），供用户事后复核。不逐条弹确认——已有「≥2 消费域 + D/U/R/A ≥2 + ≤200 字符」三道门槛，再加人工确认属于冗余打扰
- 冲突以 `shared/MEMORY.md` 为权威，role MEMORY 中的同类条目降级为 notes

**再动态发现项目中其他角色**：
```
用 Glob 扫描 .claude/agent-memory/*/  得到所有子目录，排除 shared
例如返回：[pm, dev, qa, prompt-eng]（core 默认）
          [pm, dev, qa, prompt-eng, ceo, designer, ...]（项目级扩展）
```

对发现的每个角色 `<role>`，读取其记忆文件：
```
.claude/agent-memory/<role>/MEMORY.md
.claude/agent-memory/<role>/notes.md
```

统计每个 MEMORY.md 的当前字符数（预算：role ≤8000 字符 / shared ≤4000 字符——字符才反映注入开销，行数可被单行超长钻空）。

**为什么动态发现**：core plugin 提供 4 通用角色（pm/dev/qa/prompt-eng），项目可手动在 `.claude/agents/` 和 `.claude/agent-memory/` 下新增项目级角色（如 ceo、designer、content-operator 等）。Librarian 必须覆盖所有角色，因此不能硬编码列表。

### 第 2 步：评估 notes.md + 决定落点

**2a. D/U/R/A 准入评估**

遍历每个角色的 notes.md 中的微反思条目，**跳过**下列两类已结案条目：
- 标注 `→ 已提升至 X` 的（本 SOP 自己提升过的）
- 标注 `→ 已被 {日期} 纠正取代` 的（`correction-detection` 第 3 步 supersede 时移进来的
  历史留档）——它们是**被推翻的旧口径**，重新提升等于把用户纠正过的错误再送回高置信区
- **D**urability：30 天后还重要？
- **U**niqueness：目标落点中尚未记录？
- **R**etrievable：未来需要回忆？
- **A**uthority：来源可靠？

满足 ≥2 项 → 标记为"待提升"。

> **单人/小团队项目的判定口径**：用户当面拍板的偏好，Authority 已满分，**不要因为"是否对所有人普适"而压低评分**——这类知识库只服务当前用户，普适性不是准入条件。历史上大量高价值偏好因这条误判滞留在 notes（实测某项目 181 行 notes 里全是可执行规则，却因自设"需多次验证"门槛从未提升）。

**2b. 落点分层决策**

`MEMORY.md` 不是唯一落点。按知识性质选：

| 落点 | 判据 | 加载时机 | 权限 |
|---|---|---|---|
| **项目 rules**（`.claude/rules/local/*.md`） | 跨场景稳定的纪律 / 口径，**主 Claude 也需遵守** | 每次会话必载 | **L2 用户审批** |
| **`CLAUDE.md`** | 项目级协作规则、角色边界、目录约定 | 每次会话必载 | **L2 用户审批** |
| **项目 skill**（`.claude/skills/<name>/`） | **场景触发且成套**的操作方法——特定场景才需要、一到场景就要整套用（SOP / 口径集 / 模板 / 清单） | 场景命中按需加载 | **L2 用户审批**（core plugin skill 不直接改，走 self-iteration 提案） |
| **`<role>/MEMORY.md`** | 角色特有的高置信事实 | 该 subagent 被调度时 | L1 自主 |
| **`shared/MEMORY.md`** | 影响 ≥2 角色的权威事实 | 各 subagent 启动时 | L1 自主（写入条件更严）；**agent / 主 Claude 不直写**——它们写 `shared/notes.md`，由本 skill 评估提升（对比上一行：role MEMORY 满足 D/U/R/A ≥2 可由角色直写，两行的「L1 自主」含义不同） |
| **auto-memory**（CC 官方主 Claude 记忆目录） | **主 Claude 层**内容：用户偏好 / 协作习惯 / 项目状态指针——消费者是主 Claude 而非某个角色 | 主会话启动（官方注入，subagent 不注入） | 主 Claude 自维护；**librarian 不写**——识别到此类条目时在结果中建议归 auto-memory |
| **保留 notes** | 未定型 / 场景太具体 / 待验证 | 不加载 | — |
| **移出记忆体系** | 属**业务知识**（竞品分析、方案论证、领域事实）→ 应进需求文档 / spec，不是记忆 | — | L2 |

**三问定落点**（顺序二元判断，逐问淘汰）：

1. **每次会话都必须遵守吗？** 是 → rules / CLAUDE.md（必载层）
2. 否——**特定场景触发、且是成套方法吗？**（≥3 条同主题相关做法，或含步骤/模板/清单结构）是 → 项目 skill。两形态：已有相关 skill → **增补该 skill**；没有 → 同主题攒满 3 条前**留 notes**，攒满后聚类新建（PRD 风格类沉淀直接增补项目 prd-style；先例：requirement-archiving 由实战沉淀）
3. 否——**一条就能说清的离散事实** → 先判**谁消费**：主 Claude（用户偏好 / 协作习惯 / 项目状态指针）→ 建议归 auto-memory（主 Claude 自维护，librarian 不代写）；单个角色 → `<role>/MEMORY.md`；跨 ≥2 角色 → `shared/MEMORY.md`
   > **主 Claude 不参与「≥2 角色」的计数**。它在 main-led 模式下可能直做任何域的活，恒为潜在读者——把它算进去，每条工种知识都自动凑够两个消费者，`shared/` 会被稀释成第二个大杂烩，而 shared 是**各 subagent 启动时全量注入**的，稀释的代价由每次委派承担。<br>计数只数**角色域**：这条工种知识，除了它自己那个域，还有哪个**角色**未来在具体决策／操作时要读它？答不出第二个 → 单角色域。<br>（主 Claude 的读取路径由 CLAUDE.md 直做纪律与 SessionStart 记忆地图保障，不靠挤进 shared。）

**关键判断**：`rules` / `CLAUDE.md` 是**必载**资产——放这里的知识对主 Claude 直接生效，可靠性最高，但每次会话都消耗 token；skill 行是它的**制度化泄压阀**——场景性成套内容不进必载层、不占每会话 token。`MEMORY.md` 由 SubagentStart hook 注入对应 subagent（代码保证，仅角色被调度时加载）。**定型的、跨场景的纪律往必载层搬；场景性的成套方法往 skill 搬；未定型的留 notes。**

L2 落点的提升**不自动执行**：把候选清单写入 `.claude/workframe-state/promotion-candidates.md`（覆盖式重写，头部带生成时间；每条一行，固定格式 `- [ ] <scope> | <YYYY-MM-DD> | <一句话摘要> | 出处: <notes 条目> | 建议落点: <文件+章节>`——`- [ ]` 前缀是 maintenance_workorder.py 计数依赖，勿改），然后按调用场景分流：

- **常规调用 / `/core:maintenance-review`**：用 AskUserQuestion 当场请用户确认；用户跳过时清单保留在该文件，下次运行时先读它恢复候选再继续。用户确认后执行写入并把对应行改为 `- [x]`。
- **询问式触发（SessionStart 开场卡）/ `--maintenance` 批处理**：**不当场出卡**——只写入候选文件攒卡，在结果摘要里提示用户「有 N 条 L2 候选待拍板」。开场卡场景保持轻量（用户是来干正事的），批处理场景是 print 模式（没有交互卡可用）。

### 第 2.5 步：融合整理 SOP（写入前必须执行）

> **禁止无脑 append。**往任何目标文档写入前，按顺序执行以下检查。这是本 skill 最容易被跳过、也最影响质量的一步——直接追加会让目标文档不断膨胀、同义内容重复、口径互相矛盾。
> 写入侧已前置：correction-detection / auto-update 的写入路径按本 SOP 第 1-4 步做**写时**同主题检查；本步在批处理/提升场景兜底复查漏网。

1. **先读目标文档全文**——不能只读插入位置附近。不了解全貌就无法判断归属与重复
2. **定位归属章节**——找语义最匹配的现有章节；只有确认现有章节都不合适时才新建，并说明理由
3. **查重合并**——目标位置已有同义/近义内容时，**改写合并成一条**，不是追加第二条。合并时保留信息量更大的表述，补充缺失的细节
4. **查冲突**——新内容与现有内容矛盾时**停止写入**，向用户报告冲突点，不自行裁决谁对
5. **压缩表述**——notes 里的场景描述与论证**不搬**（留原处作为出处），只搬可执行规则；粒度跟随目标文档现有风格（一条一句 vs 一条一段）
6. **格式对齐**——列表 / 表格 / 标题层级跟随目标文档现有约定
7. **标注出处**——写明「YYYY-MM-DD 从 `<role>` notes 提升」，便于回溯原始论证。**跨 scope 搬迁**（auto-memory → 角色域，或改域）写「YYYY-MM-DD 从 `<来源>` 迁移」并 append `memory_migrated` 事件——两种标注**不可混用**：三账本 check A/A' 按字样分别要求 `memory_promoted` / `memory_migrated`，写错字样等于报错了另一种事件
8. **体积预算**——写入 rules / CLAUDE.md 前统计目标文件行数。这是必载资产，**单文件超过约 150 行时先做一轮"哪些已内化成默认行为、可以精简"的复盘**，再写新内容；复盘时场景性成套内容优先迁往项目 skill（按 2b 三问定落点，泄压不丢内容）
9. **notes 侧处理（归档制）**——提升/处理完成后，把该条目**整段移动**到同目录 `notes-archive.md`（追加到文件尾，条目标题后加标注 `→ 已提升至 <目标文件>（YYYY-MM-DD）` 或 `→ 用户拍板不提升（YYYY-MM-DD）`），并从 notes.md 删除该段。
   - **为什么移动而不是原地标注**：notes.md 的语义是「待处理缓冲区」，`memory_backlog` 积压信号按它的内容量判定——已处理条目留在原地会让缓冲区只增不减、信号永不消解（与 M6 修复的 cadence 堆积同构）。archive 保留完整论证作为出处，不参与积压统计与后续评估
   - **混合段允许条目级拆分**：一个日期段里部分子条目已处理、部分未处理时，只移已处理的子条目，段内留一行注记 `（本段部分条目已处理，见 notes-archive.md 同日条目）`
   - `notes-archive.md` 首次创建时写文件头：`# <role> notes 归档区（已处理条目）` + 一行说明
   - 移动 = 删除 + 写入，但内容零删减，归类为 **L1**（不需要 L2 审批）
10. **跨 scope 顺带检测**——上面几步已把目标文档全文读了一遍，这是**唯一天然的跨域视野**，顺手做一次不额外读文件的检查：本轮处理的条目，是否与**其他域**已有条目讲同一件事？
   - 命中 → 出 **shared 候选**（进 `promotion-candidates.md`，走 L2 由用户拍板），**不自行搬**
   - 判据仍是「另一个域未来在什么决策／操作时要读它」——**两个域碰巧都提过某个工具不算**，工具重叠不构成消费证据
   - 这是**兜底**通道不是主通道：shared 候选的主发现路径是各角色收尾时的自反问（`agent-protocols` Step 2）。本步只捞那些当时没想到、事后从全文里才看出来的
   - 检测不到不算失职——**宁可漏报也不要凑数**：shared 每多一条，此后每次 subagent 启动都要付一次 token

**自检**：写完后回读目标文档被修改的整个章节，确认 ① 没有同义重复 ② 没有前后矛盾 ③ 新旧内容的粒度和口吻一致。

### 第 3 步：容量管理 + memory-index sidecar 维护 + 量化衰减

**前置动作：快照保存（L1 变更前必须执行）**

执行任何 MEMORY.md 变更前，先将当前版本快照到：
```
logs/librarian-snapshots/{YYYY-MM-DD}/{HH-mm-ss}-{role}-MEMORY.md
```

文件名带秒级时间前缀（`HH-mm-ss`），避免同分钟内多次运行覆盖。每个角色最多保留 5 个快照（按时间排序），超出时删除最旧的快照。此快照机制确保 L1 变更可回滚。shared/ 也生成快照：`logs/librarian-snapshots/{YYYY-MM-DD}/{HH-mm-ss}-shared-MEMORY.md`。

**同时**为受影响的 sidecar entries 生成同目录快照（确保 rollback 时 MEMORY.md 与 memory-index.json 一致）：
```
logs/librarian-snapshots/{YYYY-MM-DD}/{HH-mm-ss}-{role}-memory-index-entries.json
```
内容是从 `.claude/workframe-state/memory-index.json` 中筛出 `scope=<role>`（或 `scope=shared` 处理 shared 角色时）的所有 entry 子集，格式：
```json
{"<entry-key>": {"scope":"...","created_at":"...","provenance":"...",...}}
```
若本次 librarian 运行未触及任何 sidecar entry（仅做 changelog 补漏 / metrics 重算），跳过 sidecar 快照。

**sidecar 维护**（`.claude/workframe-state/memory-index.json`）：

若 `memory-index.json` 不存在，先按 `templates/memory-index-template.json` 的最小结构初始化：
```json
{"__schema__":"workframe.memory-index.v2","entries":{}}
```

每条 MEMORY.md 写入/删除时同步维护 sidecar。**MEMORY.md 保持人类可读**，元数据放 sidecar：
```json
{
  "entries": {
    "shared:2026-04-10:研发任务签发仅由qa执行": {
      "scope": "shared",              // 实际角色名（pm/dev/…）或 shared——填具体值，不照抄占位符
      "created_at": "2026-04-10",
      "provenance": "user-decree",    // 来源类型四选一，见下表；不打分、不写数字
      "protected": true,              // [纠正] 条目自动 true
      "source": "[纠正]"               // 枚举见下表；与 memory_promoted 事件的 source 同口径
    }
  }
}
```

**provenance 来源类型（归类判据——只归类，不打分，禁止写数字）**：

| 值 | 判据 | 赋值方 |
|---|---|---|
| `user-decree` | 用户明确纠正/拍板（`[纠正]` 条目） | correction-detection 固定写入，librarian 不产 |
| `user-confirmed` | 用户当面确认过 / 亲测实证 | 提升默认档 |
| `inferred` | 推断、单次观察，未经用户确认 | librarian 归类 |
| `external` | 外部转述、时效性强的信息（竞品动态 / 行业消息 / 他人说法） | librarian 归类 |

归类只需回答两个二元问题：①用户确认过吗？是 → `user-confirmed`；②否的话，信息源在项目外且有时效性吗？是 → `external`，否 → `inferred`。

**source 来源枚举**（与 `memory_promoted` / `memory_migrated` 事件的 `source` 字段同一套；doctor 的 `sidecar_health` 按此校验，写枚举外的值会被报出来）：

| 值 | 含义 |
|---|---|
| `[纠正]` | correction-detection 写入的用户纠正条目 |
| `notes.md` | 从本域 notes.md 提升 |
| `shared/notes.md` | 从 shared 缓冲区提升 |
| `auto-memory` / `<role>/MEMORY.md` / `shared/MEMORY.md` | 跨 scope 搬迁的来源域 |
| `manual backfill` | 人工补录 |
| `librarian-promoted` | **历史值，只读不新写**——早期由 librarian 提升的条目用它。它答的是「谁做的」而非「从哪来」，与其余值不同轴；存量条目保持原样不回改，新写入一律用上面几种 |


Key 规则：`{scope}:{YYYY-MM-DD}:{条目前 20 字规范化}`。规范化三条**缺一不可**（与 `correction-detection.md` 第 4 步同源，否则两边为同一条目算出不同 key、sidecar 出现重复项）：**先删除全部空白字符**（空格 / 制表符 / 换行）**→ 再取前 20 字**（Unicode 字符数，非字节）→ 同日同前 20 字碰撞时追加序号后缀（`-2`、`-3`…）。

**顺序不可颠倒**（2026-08-16 统一，同 `correction-detection.md`）：颠倒会让同一条目在两处算出不同 key。改序前的历史 key 不回改。

**事件写入（供 `/core:memory-log` 展示历史流水）**：

1. notes.md → MEMORY.md 提升成功后，先写 MEMORY.md 和 sidecar entry，再 append `.claude/workframe-state/events.jsonl`：
   ```json
   {"ts":"<ISO-8601>","type":"memory_promoted","scope":"<角色名或shared，填实际值>","role":"<role-if-any>","entry_key":"<memory-index-key>","summary":"<条目摘要/原文前80字>","source":"notes.md","protected":<sidecar.protected>,"provenance":"<sidecar.provenance>"}
   ```
   落点为 skill / rule 文件时（L2 经用户批准后执行）：**不写 sidecar**（sidecar 只索引 MEMORY 条目），事件仍写但**省略 entry_key**，落点文件路径写进 summary——与 correction-detection 第 4 步同规格。
2. MEMORY.md → notes.md 降级或容量腾挪经用户确认后，**必须在删除 sidecar entry 前** append `.claude/workframe-state/events.jsonl`，把即将删除的元数据快照写入事件：
   ```json
   {"ts":"<ISO-8601>","type":"memory_decayed","scope":"<角色名或shared，填实际值>","role":"<role-if-any>","entry_key":"<memory-index-key>","summary":"<降级条目摘要/原文前80字>","source":"<sidecar.source>","age_days":<按 created_at 现算的整数>,"provenance":"<sidecar.provenance>"}
   ```

3. **跨 scope 搬迁**（auto-memory → role/shared，或域判错后改域）——先在目标域写好条目与 sidecar entry，再 append `.claude/workframe-state/events.jsonl`：
   ```json
   {"ts":"<ISO-8601>","type":"memory_migrated","scope":"<迁入的目标域：角色名或shared>","role":"<role-if-any>","entry_key":"<目标域新建的 memory-index-key>","summary":"<条目摘要/原文前80字>","source":"<迁出方：auto-memory 或 <role>/MEMORY.md>","from_ref":"<源文件名或条目标识>","protected":<sidecar.protected>,"provenance":"<原样承接，不重新判定>"}
   ```
   **与 `memory_promoted` 的分工**：提升是**同一 scope 内** notes → MEMORY（过 D/U/R/A）；搬迁是**已合格条目跨 scope 移动**（不重判准入）。混用会污染 notes 陈化信号——消费方靠 `memory_promoted` 的时间戳判"这个域多久没消化 notes 了"，而搬迁并没有消化任何 notes。
   `provenance` **原样承接不重新判定**：搬迁不是新的信息来源，把 `user-decree` 在搬家途中降成 `inferred` 会让该条目失去衰减豁免。
   **三个日期各有出处，别混**：

   | 位置 | 取值 | 理由 |
   |---|---|---|
   | MEMORY 条目行首 | **经验发生日**（原条目的日期，搬迁不改） | 它回答「这条知识是什么时候的」，与搬家无关 |
   | 出处标注 `（YYYY-MM-DD 从 … 迁移）` | **迁移日** | 它回答「什么时候搬的」 |
   | sidecar `created_at` 与 `entry_key` 里的日期 | **优先承接原创建日**；源头查不到（如 auto-memory 只有 `modified`）时退用迁移日 | 衰减时钟按 `created_at` 走——重置它等于让一条两年前的老知识搬个家就「变年轻」，与 `provenance` 原样承接同理 |

   两种取值三账本都认（`check C` 同时收行首日期与迁移日），但**同一条目内三处必须自洽**：`entry_key` 的日期段要与 `created_at` 相同。

`summary` 是历史快照字段，memory-log 优先展示事件里的 summary；不要依赖当前 sidecar 仍能查到已降级条目。

**量化衰减**（基于 sidecar 的 provenance 来源类型 + age 判断——归类不打分）：

- `protected = false` 且 `provenance = external` → 生成**待审查降级候选**（外部转述/时效性信息，不论年龄；不自动执行——在下次 `/core:maintenance-review` 或维护批处理时由用户确认后执行；执行时按上面的时序先 append `memory_decayed`，再删除 sidecar entry）
- `protected = false` 且 `provenance = inferred` 且 `(today - created_at) > 180 天` → 同上生成待审查降级候选（长期未被重申的未确认推断）
- `user-confirmed` / `user-decree` 条目**永不自动进候选**——用户确认过的事实不因年龄过期，要变只能被新的确认/纠正 supersede
- **dormant 项目**：所有衰减动作**和** notes→MEMORY promotion **均冻结**（读 `activity-state.json` 的 `dormant` 字段，true 则本步全部跳过；`wake_up_pending=true` 时同等处理）。除非用户通过 `/core:maintenance-review` 明确确认，否则不改 MEMORY / notes

**容量管理流程**：

如果 MEMORY.md 已接近字符预算（role 8000 / shared 4000）：
1. 识别低价值条目（从 sidecar 衰减信号识别：provenance=external / provenance=inferred 且 age>180 天 / protected=false 且非 [纠正]）
2. 生成**容量整理候选**，写入本次 Librarian 日志和 `/core:maintenance-review` 待用户确认
3. 用户未确认前，不移动 MEMORY 条目、不删除 sidecar entry、不为了腾空间写入新条目
4. 用户确认后，才将候选条目移至 notes.md 底部（标注"已从 MEMORY.md 降级，source=<原 source>"），同步删除 sidecar entry，再写入待提升条目

**绝对禁止清理的内容**：
- 带 `[纠正]` 标记的条目 — **永不清理**（sidecar `protected=true`）
- 用户明确要求保留的条目（`protected=true`）

### 第 4 步：日志补漏

检查 `projects/changelog.md`（如存在）是否有缺失的记录：
- 对比 MEMORY.md / notes.md 的最近写入与 changelog 条目
- 补齐缺失的 changelog 记录
- 若项目未维护 changelog.md，跳过此步

### 第 5 步：刷新 `skill-metrics.yaml`（调用 deterministic 脚本）

**默认路径**：SessionEnd hook 会调用 `recompute_skill_metrics.py` 自动重算 `.claude/workframe-state/skill-metrics.yaml`。如果本次 Librarian 需要立即刷新，调用插件内兜底命令（插件根路径从 `plugin-root.txt` 取，不依赖 PATH）：

```bash
python "$(cat .claude/workframe-state/plugin-root.txt)/bin/workframe-recompute-skill-metrics"
```

`plugin-root.txt` 由 SessionStart hook 每次会话刷新为当前插件根。若环境里 `workframe-recompute-skill-metrics` 恰好已在 PATH（CC plugin `bin/` 注入生效的环境），裸调等价。

**脚本职责**：

1. 读取 `.claude/workframe-state/events.jsonl`（逐行 JSON 解析；跳过 `__schema__` 描述行和 malformed 行）
2. 按默认窗口 30 天过滤（`generated_at - ts ≤ 30d`），也可由调用方指定
3. 分类聚合：
   - `skills[<name>]`: 按 `type=skill_used` 汇总 `invocations`（计数）、`successes`（`success=true` 计数）、`last_used`（最新 ts 的日期）
   - `rules`: **整段空对象**——`recompute_skill_metrics.py` 直接写 `rules: {}`（`rule_triggered` 无 deterministic producer，不生成占位 entry；保留 `rules` 顶层 key 仅为 schema 结构兼容）。下游（check-iteration-trigger.py）不再消费此字段；见 `.workframe-meta/event-schema.json` 中 `reliability: removed_v0_2_1`
   - `corrections_count`: `type=user_correction` 事件总数
   - `blocks_count`: `type=task_blocked` 事件总数
   - `proposal_failures_count`: `type=proposal_verified` 且 `signal_met=false` 的事件总数
4. 整个重写 `.claude/workframe-state/skill-metrics.yaml`（不是 append），更新 `generated_at` 和 `window_days`
5. 不写入任何 skill/rule frontmatter — frontmatter 不再承担统计职责（Phase 0 已清理非官方字段 `usage_count` / `last_used` / `trigger_count`）

> 为什么 L1：脚本重算是 deterministic 的派生文件操作，不改动用户可读资产（MEMORY.md / notes.md / rules / skills 本体），失败影响面仅限 skill-metrics.yaml 自身，旧版本可随时从 events.jsonl 重新生成。

**下游消费者**：
- `self-iteration` 日常决策读 `skill-metrics.yaml`（无需解析全量 events）
- 审计/追因需要时读 `events.jsonl`
- `/core:audit` 等维护命令汇总展示

### 第 6 步：关闭积压信号 + 写整理日志

**关闭 `memory_backlog` 信号**（本次整理覆盖了触发该信号的 notes 时）：

1. Read `.claude/workframe-state/activity-state.json`，找 `pending_maintenance` 中
   `kind == "memory_backlog"` 且 `status == "open"` 的条目，记下它们的 `id`
2. 用代码通道关闭（**不要自己改这个文件**）——它一并写 `pending_maintenance_dismissed` 事件：

   ```bash
   python "$(cat .claude/workframe-state/plugin-root.txt)/scripts/maintenance_workorder.py" \
       --close-pm <PM-ID> [<PM-ID> ...]
   ```

> **为什么不自己写这份文件**：`activity-state.json` 同时装着 `session_counter` /
> `recent_drift_repairs` / 注入标记等一点点攒出来的状态。整份重写时漏掉任何一个都不会
> 报错，只会静默丢掉一段历史；而上面那个命令走文件锁 + 原子替换 + 三方合并，只动
> `pending_maintenance` 一处。同理，`--commit` 路径的记账也一律由代码提交。

> 归档制（第 2.5 步第 9 条）保证已处理条目移出 notes.md——若整理后 notes 仍超阈值（还有大量未处理条目），下次 Stop hook 会重新产生信号，这是**正确行为**，不是重复报警。

在 `logs/librarian/{YYYY-MM}/{YYYY-MM-DD}.md` 追加本次 Librarian 运行摘要：
```
## {日期} Librarian 整理

- 覆盖角色：{第 1 步发现的角色列表}
- 评估 notes.md 条目：{n} 条（跳过已标注已提升：{n} 条）
- 提升到 MEMORY.md：{n} 条
- 提升到 rules / CLAUDE.md（L2，已确认）：{n} 条；待确认清单：{n} 条
- 融合方式统计：新增章节 {n} / 合并进现有条目 {n} / 因冲突暂停 {n}
- 从 MEMORY.md 降级：{n} 条（仅统计用户已确认执行的降级；候选另列）
- 待审查降级/容量候选：{n} 条
- 快照保存：{n} 个角色
- skill-metrics.yaml 重算：window_days={N}，events 条数={n}，统计了 {m} 个 skill / {k} 个 rule
```

重大结构变更（如清理过期条目）同步追加到 `projects/changelog.md`（如维护）。

## 变更权限

- **L1 变更（自主执行）**：非冲突 notes.md → `MEMORY.md` / `shared/MEMORY.md` 条目提升、已处理条目整段移入同目录 `notes-archive.md`（归档制，见第 2.5 步第 9 条——**不是**原地加标注，那是被它取代的旧做法）、changelog 补漏、快照保存、`skill-metrics.yaml` 脚本重算
- **L1 候选（须用户在 `/core:maintenance-review` 确认后执行）**：MEMORY.md → notes.md 降级、容量不足时的腾挪整理
- **L2 变更（须用户审批）**：删除任何内容、**提升/增补到 `.claude/rules/local/*.md`、`CLAUDE.md` 或 `.claude/skills/**`（项目 skill）**、把业务知识移出记忆体系到需求文档、修改 `board.yaml` 任务结构、改 core plugin 内源文件（含 core skill——走 self-iteration 提案）

> L2 落点（rules / CLAUDE.md / skills）是主 Claude 直接消费的资产（前两者必载、skill 场景加载），误写影响面大，因此**一律先出清单等用户确认**，不自主执行。清单形式：每条给「一句话摘要 + 出处 + 建议落点 + 建议插入的章节」，让用户一次过目即可决策。
