---
name: self-iteration
description: 系统自迭代能力，通过数据驱动的模式识别生成改进提案
user-invocable: false
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion]
---

# 自迭代技能

## 后台作业与待批处理

后台路径由控制器执行：采证 → 冻结 → 模型按主题给结论 → 控制器核对 → 作业日志。**后台作业只出建议**：任何档位下都不改项目文件、不建提案文件，结果在 `status`。每项目滚动七天最多一次、全机 UTC 每日最多一次（两门合计），默认单次超时十五分钟；不设费用限额。用户级 `self_iteration.mode` 为 off / propose / apply，是上限；项目没有自己写 `apply` 的一律按 propose。

采证：每条没人处理过的纠正、每条没处置的笔记、每个复测失败各算一条候选，不要求两条字面相同。满足任一就排队——单个 scope 待处理笔记 ≥3,000 字、待处理纠正 ≥3 条、有标明重复强调的、有复测失败的、最早一条等了 ≥14 天（没有日期的从首次见到算）。落点在主会话记忆的纠正不入选；归属对不上真实角色目录或模块的只诊断。没入选的原因、笔记里不成条目的字数由 `status` 与 doctor 给出。

模型作业说明见 `reference/job-instructions.md`。结论八种：该写、已覆盖、该修工具、该交框架、该进文档、已过时、有冲突、证据不足。每条证据至少要落到一个主题；判「已覆盖」须逐条给出规则原文，控制器在冻结的文件里找得到连续一致的文字才算；写操作各自列出所用证据，控制器核目标、章节、来源、限定词与容量，给出不能自动写的原因。引文与字面检查不能证明语义正确。

有结论的证据记为「看过」，不再单独触发；同 scope 有新候选或它涉及的文件变了才随包再看，最多再看两次。没有结论的（漏答、引文找不到、作业期间文件被改、操作不可用）算未处理，连续三次暂停，材料变了或 `resume` 后再试。没有任何结论会永久消费一条证据，后台从不移走、删除或改写原笔记。

输入上限内按等待时间从老到新装，复测失败与重复强调优先；放不下的留到下次并记数，不截断。单条连必要上下文都超总上限时，在状态目录 `selfiter/manual-review/` 保存原因、证据与所需文件全文转人工，跳过它继续装后面的；`status` 可查看。

新 CLI 程序先在隔离夹具中做实际能力自检，验证项目 hook、外部工具与文件写入的隔离；版本号本身不授予运行权。通过记录绑定平台、可执行程序与适配器指纹，任一变化重新验证；失败继续延期。自检与分析共享一次作业额度和总超时，自检用量单独记录。

用户在后续对话收到摘要后，选择查看或稍后；稍后不算驳回。有需要人看的主题（该写、该修工具、该交框架、该进文档、有冲突）或需处理的失败报告跨会话只提示一次；其余报告与自检延期只在 status 留存。旧待批、转人工和当前延期仍在新会话提示，同会话相同状态不重复。每个作业的 report-notice.json 保存报告提示回执，notices.json 只保留最近六十四个会话的待办提示指纹；已有历史报告不重播。用 `workframe-selfiter status --project <项目根>` 读取完整作业结果：建议逐条展示证据、原因、目标、拟写内容与不能自动写的原因，采纳后由主会话按各资产现有流程写，后台不代写。`projects/proposals/pending/` 下的旧 AUTO 提案仍可查看与决定，后台只读它们、用来不重提已驳回的证据；决定前逐项展示改前、改后、理由、影响、恢复依据与 `review_hash`，不得只展示数量就执行。

用户明确决定后，对仍在白名单中的局部变更调用 `workframe-selfiter decide --project <项目根> --proposal-id <AUTO-id> --decision accept --review-hash <用户所看版本的hash>`；驳回用 `--decision reject`。草稿有变化须重新展示，不复用旧批准。受保护资产、重组、删除等提案仍走各资产现有 owner/审批流程，此命令不新增越权入口。有新有效证据才重评驳回事项；相同待批问题补证据，不重复建单。

人工项先完整展示原因、当前证据、上下文、manual_id 与 review_hash。用户按对应资产的现有流程实际处理后，可确认 `workframe-selfiter manual-decide --project <项目根> --manual-id <id> --decision handled --review-hash <所看版本hash>`；明确忽略当前证据则用 `--decision ignore`。只消费该版证据并关闭提示，原记录与旧版历史保留；两个动作都不代写内容、不授予受保护资产写权。同主题新证据重开原记录，必须重新展示和确认。

AUTO 预览的完整 `after` 是批准后将落盘的 UTF-8 字节，包含来源注记、原有行尾与尾空行；执行前仍核对基线和来源。没有完整改后内容时不显示虚假的整文件替换 diff，也不直接批准；旧预览用 `workframe-selfiter preview --project <项目根> --proposal-id <AUTO-id>` 更新并重新展示，新 hash 须重新确认。无法安全渲染的建议只作为片段展示，交资产 owner 处理。

作业目录内的 evidence、transport、journal、result 与 `selfiter/history.md` 分别记录输入、真实执行器输出、逐操作恢复进度、结果及人类可读摘要；路径由状态目录配置推导。不得用模型的“完成”声明代替实际落盘结果。先用 `workframe-selfiter rollback --project <项目根> --job-id <id>` 展示精确回退预览；用户确认该版后加 `--review-hash` 才执行。后续编辑冲突时停止，不覆盖。未完成的 journal 用 `recover` 恢复，不能换新 job 重复追加。

复测分 pass / fail / inconclusive。缺少可归因的相关使用机会不能判 pass；最多延长两次十四天观察窗后保持 inconclusive 结案，保留恢复材料。普通自动记忆变更由作业台账跟踪，不逐份新建 QA 任务；仍需独立验证的资产照既有规则执行。控制器不可用或隔离能力未确立时延期并报告，不放宽权限重试。作业期间冻结的文件被改动或笔记被撤回，依赖它的结论不算数，涉及的证据下个额度窗口重评，报告提示一次。报告已存、记账没写完就中断的，下次运行先补账，不再调模型。

连续三次作业失败（含一条证据都没答上的作业）会熔断；复测失败只冻结那一条，下一轮作为修正项重新分析，不停整个项目。成功生成建议不会自行解除熔断。用户查看日志、解决原因并明确恢复后运行 `workframe-selfiter resume --project <项目根>`；该命令保留历史记录，也解除暂停的证据，不替你确认复测失败；配置中的 off / propose 仍然生效。只读 doctor 报出采证漏斗、熔断和未补齐的记账尾部。

以下五阶段保留作手动分析与既有提案处理参考；评分不赋予后台自动写入权。后台 AUTO 提案的状态、复测、记账以控制器为准，不重复执行旧阶段的建 QA 单、移动文件或关闭信号步骤。

## 5 阶段流程

### 阶段 1：数据收集 + 已应用提案闭环验证

**(a) 数据源收集**（主要在 `.workframe/state/`（另含 `.workframe/agent-memory/` 与 `projects/` 下若干路径，见下表） 下，由 hook / deterministic scripts / system skills 维护）：
- `.workframe/state/skill-metrics.yaml` — 技能/规则使用汇总（由 `recompute_skill_metrics.py` 从 events.jsonl 重算）
- `.workframe/state/events.jsonl` — 原始事件流（审计/追因时读，日常决策读 metrics 即可）
- `.workframe/state/activity-state.json` — 活跃度 + dormant 状态 + **`pending_maintenance`（status=open）**；若 `dormant=true` 或 `wake_up_pending=true` 则本次自迭代直接退出。`pending_maintenance` 里的 kind/details 是本次识别模式的重要线索，应与 notes/events 证据一起纳入阶段 2 分析。
- `.workframe/agent-memory/*/notes.md` — 各角色微反思
- `.workframe/agent-memory/shared/MEMORY.md` 和 `shared/notes.md` — 跨角色共识
- `projects/changelog.md` — 历史操作日志
- `projects/issues/` — 历史问题记录（若有结构化文件）

**(b) 扫描 `projects/proposals/applied/*.yaml` 中 `verified: null` 的条目做闭合验证**：
- 对每条读取 `verify_by`（日期）和 `verify_signal`（需观察到的信号表达式）
- 若今天 ≥ `verify_by`：
  - 读取 skill-metrics.yaml / events.jsonl，判断 `verify_signal` 是否已达成
  - `signal_met=true` → append events.jsonl：`{"ts":"<ISO-8601>","type":"proposal_verified","proposal_id":"<id>","signal_met":true}`；将提案文件中 `verified: true`
  - `signal_met=false` → append events.jsonl：`{"ts":"<ISO-8601>","type":"proposal_verified","proposal_id":"<id>","signal_met":false}`，再 append `{"ts":"<ISO-8601>","type":"proposal_failed","proposal_id":"<id>"}`（供 audit / 下一轮 self-iteration 反思用；`recompute_skill_metrics.py` 实际统计的是 `proposal_verified.signal_met=false` 累加到 `proposal_failures_count`，不直接读 `proposal_failed`）；将提案文件中 `verified: false`

### 阶段 2：模式识别 + 置信度评分

从阶段 1 收集的数据中识别候选模式：
- **重复问题**：notes / changelog / events 中有明确证据显示同类问题重复出现。`occurrences` 是置信度计算的证据输入，**不是硬门槛**；低于 3 次仍可计算 confidence，但通常低于提案阈值。
- **低效流程**：仅当 notes / changelog / issues 中存在明确的耗时或阻塞记录时方可使用；系统无耗时事件，**不得凭感觉声称"平均耗时高于预期"**。
- **未覆盖场景**：仅当 notes / changelog 中有明确的"用户重复手工处理"记录时方可使用；**不得无证据臆造"手工处理"场景**。
- **技能低成功率**：近 30 天某 skill `success/invocations < 0.6`（来自 skill-metrics.yaml）。**仅供人工判读，不作自动触发信号**（`success` 为 agent 自评，实测从未产出 false，作触发条件永不满足）——用它时须结合 notes / user_correction 等独立证据，不得仅凭该比值提案。`invocations: 0`（只有 hook 记的 `invoked`、没有自报）的行是「无自报样本」，不进这一比值。
- **提案失败回路**：有 `proposal_failed` 事件的旧提案 → 反思当初假设，识别失败原因。

> **跨门计数前必须先说数据来源**（v3 起）：读 `skill-metrics.yaml` 的 `harness_breakdown` 段，在任何按事件计数的判断之前写一行「cc N 条 / codex M 条 / unknown K 条 / unstamped L 条；跨门缺口 <cross_door_gaps.codex>；两侧皆无 <neither_door>（**后者不算跨门缺口**，混进去会把缺口数读大）；判 full 但投递未确立 <full_but_delivery_unverified.codex>（这类的零计数同样可能是结构性的——先怀疑投递、再怀疑模型）」。**照抄那一段，别在这里另记一份缺口清单**——清单一变，副本立刻成为假话。
> 为什么这条压着模式识别：本阶段的四类信号全是**按事件计数**得出的，而一类事件为零可能是「真的没发生」，也可能是「那扇门根本产不出它」。`turn_failed` 在 Codex 侧恒为 0（无 `StopFailure` 对等 hook）⇒ **问题信号被系统性低估**；Codex 的 `PostToolUse` 对失败的工具调用不触发，两者同向叠加。这是已知的产品决策，**本 skill 只披露、不替代信号**——不得因为 codex 侧数小就断言「那边问题少」。

> "规则盲区"不作信号：CC 没有 rule 触发回调，`rule_triggered` 事件不可 deterministic 捕获（见 `.workframe-meta/event-schema.json`）。如怀疑某条 rule 定义不当，改由人工 review / `/core:audit` 主观判断，不作为自迭代自动信号。

**置信度公式**（每个候选模式必须显式计算并写进提案）：

```
confidence = min(1.0,
    0.35 × min(occurrences / 5, 1.0)
  + 0.30 × recency(last_seen, 30d)
  + 0.20 × cross_role_corroboration
  + 0.15 × user_confirmed
)

recency(d, max_days) = max(0, 1 - d / max_days)
cross_role_corroboration：该模式被多少个角色的数据源佐证
    0 角色 → 0.0；1 → 0.33；2 → 0.67；≥3 → 1.0
user_confirmed：用户显式确认过（如 [纠正] 标记 / 直接反馈）= 1.0，否则 0
```

**处置阈值**：
- `confidence < 0.5` → 只记 `.workframe/agent-memory/shared/notes.md`，**不生成提案**
- `0.5 ≤ confidence < 0.8` → 生成提案，进入阶段 3，需用户明确批准（L2 全部走这条）
- `confidence ≥ 0.8` → 生成提案，建议用户快速确认后执行（L1 可走这条，L2 仍需明确批准）
- **Core 文件变更**（`plugins/core/**`，以及项目侧 `AGENTS.md` / 项目 skills（`.claude/skills/**`、`.agents/skills/**`）这类系统定义资产）一律 **L2 + eval 覆盖**，阈值不降级

### 阶段 3：多候选提案生成

每个通过阈值的模式生成**一份提案文件**，内含 **2-3 个候选方案**，按 `score = impact_int × confidence - risk_penalty` 降序排列：
- `impact_int`：`low=1 / medium=2 / high=3`
- `risk_penalty`：`low=0.25 / medium=0.5 / high=0.75`

> **凑候选时先看一眼 `new_gate`**：这个模式能不能用一道机器闸（断言 / 校验 / hook）盖掉？能就把它列进候选——纪律要靠模型每次记得住才生效，闸不用。落点分层判据见 skill: `librarian` §2b。

```yaml
# projects/proposals/pending/PROP-{YYYYMMDD}-{序号}.yaml
proposal:
  id: "PROP-20260424-001"
  created_at: "2026-04-24"
  pattern: "识别到的模式描述（≤80 字）"
  confidence: 0.75
  evidence:                     # 证据一律用**可复核的确定性信号**，别拿 success 比值当主证据
    - "events: user_correction ×4 近 7 天，同一主题（提升记忆时漏写 sidecar entry）"
    - "shared/notes.md 3 个角色提及同一现象"
    - "changelog: 近 30 天 3 次手工补 sidecar 的记录"
  source_pending_maintenance:
    - "PM-20260424-003"          # 触发本次自迭代的 pending_maintenance 条目 ID
  change_level: "L1 | L2"       # 触及系统定义 → L2
  eval_cases_required: false    # L2 + core 文件变更 → true
  eval_cases: []                # eval_cases_required=true 时填写 case 文件路径列表
  candidates:
    - option: "A"
      proposed_change:
        type: "new_gate | new_rule | update_rule | new_skill | update_skill | process_change"
        targets: ["目标文件路径"]   # 数组；多文件 L2 变更时列出全部
        description: "具体改进措施"
      impact: "medium"          # low / medium / high
      risk: "low"
      score: 1.25               # impact_int × confidence - risk_penalty = 2×0.75-0.25
    - option: "B"
      proposed_change:
        type: "update_skill"
        targets: ["目标文件路径1", "目标文件路径2"]
        description: "具体改进措施（涉及 ≥2 文件时建议提升 risk）"
      impact: "high"
      risk: "medium"
      score: 1.75               # 3×0.75-0.5
    - option: "C"
      proposed_change:
        type: "process_change"
        targets: ["目标文件路径"]
        description: "具体改进措施"
      impact: "low"
      risk: "low"
      score: 0.50               # 1×0.75-0.25
  recommended: "B"              # 按 score 排序推荐最高分
  verify_by: "2026-05-08"       # 下次自迭代闭环验证时间（默认 +14 天）
  verify_signal: |                # 必须是**能证伪**的确定性信号
    近 14 天内同主题（漏写 sidecar）的 user_correction 事件 = 0
    且期间每条 memory_promoted 事件都带非空 entry_key
  applied_at: null
  applied_option: null
  verified: null
  rejected_at: null
  rejection_reason: null
```

**L1 / L2 判定标准**：
- 仅涉及 `notes.md` / `MEMORY.md` / `projects/proposals/` / `projects/changelog.md` 的变更 → **L1**
- 任何触及以下文件的变更一律 **L2**：`CLAUDE.md` / `AGENTS.md` / `.claude/agents/**` / `.claude/skills/**` / `.agents/skills/**` / `.claude/settings*.json` / `.workframe-config.json` / `plugins/core/**`（含框架的注入源 `context/**`）
- **MEMORY 冲突 / `[纠正]` 条目冲突** → 强制 L2（不管其他条件）

- **触及框架注入源 `context/` 任一片 = L2，且提案必须带打包器 dry-run 的**前后余量**。必载层是**零和**的：一条提案可能悄悄把别的内容挤出上限，而挤出去的那部分**不会报错**

> **入口分流提示**：项目自有判据（`AGENTS.md` 的判据段 + 项目 skill）也有"用户显式确认"入口（用户纠正被判为通用规则时的那条），与本 skill 的 L2 提案路径**互不冲突**——前者由用户在纠正回显时直接落盘，后者由 self-iteration 自动提案审批。本 skill 自动提议时一律走 L2，不直接写。

**若阶段 2/3 结束后无任何提案生成**（所有候选模式 confidence < 0.5）：

1. 在 `.workframe/agent-memory/shared/notes.md` 补记本次自迭代无提案原因（简短，≤ 2 行）
2. 关闭所有触发本次自迭代的 open 条目——**用代码通道**，它会连同 `reason` 一起写
   `pending_maintenance_dismissed` 事件（条目会被 SessionStart 定期 GC 清掉，保留天数
   见 `session-start-prep.py` 的 `PM_CLOSED_RETENTION_DAYS`；没有事件就查不到
   「它当时为什么关的」）：

   ```bash
   python "$WF_PLUGIN_ROOT/scripts/maintenance_workorder.py" \
       --close-pm <PM-ID> [<PM-ID> ...] --reason self_iteration_no_proposal
   ```

   待关闭条目的 kind 为 `memory_backlog` / `close_check_due` 之一——与
   `check-iteration-trigger.py` 实际写入的 kind 集合一致；`skill_low_success` 已移除，
   存量条目一并关闭。
3. 退出，不进入阶段 4。让这批信号安静下来的是第 2 步给每个 kind 装上的驳回基线：
   **第 2 步漏关的条目会原样重开**——dedup upsert 只对 `status=open` 的条目去重，
   closed 条目不参与，同一个 kind 会以新 ID 再冒出来。

### 阶段 4：用户审批

展示提案摘要（用 `AskUserQuestion` 选择题式交互）：
```
[PROP-20260424-001] confidence=0.75 | L2 | 推荐候选 B (score=1.75)

模式：librarian 提升记忆时漏写 sidecar entry，近 7 天 4 次被用户纠正

候选：
  A. 在第 3 步补 sidecar 写入自检清单 (impact=medium, risk=low, score=1.25)
  B. 提升与 sidecar 写入合并为一步 + 加 2 个 eval case (impact=high, risk=medium, score=1.75) ← 推荐
  C. 仅在整理日志里记录漏写次数 (impact=low, risk=low, score=0.50)

Verify by: 2026-05-08 | Signal: 同主题 user_correction = 0 且 memory_promoted 均带 entry_key
```

- **L1 + confidence ≥ 0.8**：作为手动分析参考展示推荐候选；评分不授予后台自动写入权，后台按上方控制器白名单和融合条件执行，待批内容须明确确认（用户可事后 `/core:rollback`）
- **L1 + confidence 0.5~0.79**：等用户明确选哪个候选
- **L2（含 core 文件变更）**：必须等用户明确选哪个候选 + 若 `eval_cases_required=true` 则须先补 eval cases

### 阶段 5：执行变更 + 写入闭环验证标记

**用户批准后**按顺序执行：

1. **前置备份**（L2 必须）：仅对**用户选定候选**（即将执行的 `applied_option`）的 `proposed_change.targets[]` 中**每个** target 备份到 `{target-dir}/versions/{YYYYMMDD-HHmmss}-{target-basename}.bak`
   - **关键约束**：未被选中的候选（如用户选 B 时的 A / C）的 targets **不**备份、**不**进入后续步骤的 rollback-index entry——避免 rollback 时误回滚未变更的文件
   - 示例：`.claude/skills/my-skill/SKILL.md` → `.claude/skills/my-skill/versions/20260427-193000-SKILL.md.bak`
   - 用 Bash 创建 versions/ 目录，每个 target basename 最多保留 3 个备份，删除最旧的同 target 备份
   - 多 target 时：所有备份失败任一即整批回退，不执行后续步骤
2. **eval 门禁**（若 `eval_cases_required=true`）：
   - 遍历提案的 `eval_cases` 路径列表，确认每个路径存在，且至少覆盖：必载片 ≥2 正 +1 负；core skill 1 成功 +1 失败；agent 路由 ≥3 样例
   - 未满足则拒绝执行，回退到阶段 4 提示用户补 case
3. **执行变更**：按选中的候选 `proposed_change.description` 修改目标文件
4. **记录 changelog / MEMORY / board tracking**：
   - 在 `projects/changelog.md` 追加变更摘要
   - 变更影响角色行为时，影响说明进上面那条 changelog 摘要，**不往 MEMORY.md 抄一份**——变更已落在目标文件里，记忆里再写一遍就是第二份手写口径。只有目标文件没承载、角色执行时又要知道的增量，才按必载片 §Step 2 的归属判据另行沉淀
   - 追加 board tracking 任务前先检查 `projects/board.yaml` 是否已有 `PROP-{id}` 标签的 open 任务；有则跳过；无则追加：
     ```yaml
     - id: "TASK-<next-id>"
       title: "验证自迭代提案 PROP-<id>"
       description: "跟踪 verify_by 和 verify_signal 达成情况"
       status: pending
       priority: P2
       assigned_to: qa
       created_at: "<today>"
       updated_at: "<today>"
       deadline: "<proposal.verify_by>"
       depends_on: []
       tags: [iteration-tracking, "PROP-<id>"]
       estimate_hours: 1
       notes: ""
     ```
5. **归档提案 + 写入验证标记**：
   - 提案文件从 `proposals/pending/` 移至 `proposals/applied/`（用 Bash mv）
   - 在提案文件内写入：`applied_at: <today>`、`applied_option: <A|B|C>`、`verified: null`（留给下次阶段 1b 闭合）
6. **append proposal_applied 事件**（若多 target，`change_target` 写第一个，详细列表存在 rollback-index entry）：
   ```json
   {"ts":"<ISO-8601>","type":"proposal_applied","proposal_id":"<id>","change_target":"<targets[0]>","applied_option":"<A|B|C>"}
   ```
7. **写 rollback-index entry**（一条 entry 涵盖**用户选定候选**的所有 target，**不含**未执行候选的 target）：向 `.workframe/state/rollback-index.json` 的 `entries` 数组追加：
   ```json
   {"id":"RB-<YYYYMMDD-NNN>","proposal_id":"<id>","targets":["<target1>","<target2>"],"backups":["<backup1>","<backup2>"],"applied_at":"<ISO-8601>","applied_option":"<A|B|C>"}
   ```
   - `targets[]` 与 `backups[]` 一一对应，长度相同
   - `targets[]` 严格 = `selected_candidate.proposed_change.targets[]`（选定的那一个 candidate）
   - 多候选场景下未被执行候选的 target **不得**写入此 entry，避免 `/core:rollback` 时误回滚未变更文件
8. **关闭相关 pending_maintenance 条目**：读提案的 `source_pending_maintenance` 字段取到
   ID 列表，用代码通道关闭（**不要手改 `activity-state.json`**——那份文件还装着
   session_counter / drift 历史等攒出来的状态，整份重写漏字段不报错只丢历史）：

   ```bash
   python "$WF_PLUGIN_ROOT/scripts/maintenance_workorder.py" \
       --close-pm <PM-ID> [<PM-ID> ...]
   ```

   该命令一并写 `pending_maintenance_dismissed` 事件，无需再手写事件行。

   > 这一步不只是清提醒：关掉一条会把**该 kind** 的起点推进到本次 `closed_at`
   > （`close_check_due` 只认驳回之后新打上的 stale 标记；`memory_backlog` 没有可比的
   > 证据时间点，改为静默一个保留期后重报）。按 kind 各管各的，关一条不影响另一条。
   > 所以只关**本次自迭代真的覆盖到**的那些条目——把没处理的信号一并关掉，等于宣称
   > 它们也已处理，就此静音。

**若所有候选均被拒绝**：

1. 提案文件从 `proposals/pending/` 移至 `proposals/rejected/`（用 Bash mv）
2. 在提案文件内写入：`rejected_at: <today>`、`rejection_reason: "<用户给出的拒绝原因>"`
3. 关闭相关 pending_maintenance 条目（同上第 8 步）
4. 不写 proposal_applied 事件，不写验证标记

## 与其他 skill 的协作

- **librarian**：本 skill 消费 `recompute_skill_metrics.py` 维护的 skill-metrics.yaml；若模式涉及记忆层，非冲突 L1 提升可由 librarian 执行，降级/容量候选需 `/core:maintenance-review` 确认
- **/core:rollback**：用户不满意本 skill 生成的变更时，通过 rollback skill 回退；阶段 5 第 7 步写入 rollback-index entry，`/core:rollback` 优先读该索引
