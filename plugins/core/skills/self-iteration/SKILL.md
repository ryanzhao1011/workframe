---
name: self-iteration
description: 系统自迭代能力，通过数据驱动的模式识别生成改进提案
user-invocable: false
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion]
---

# 自迭代技能

## 5 阶段流程

### 阶段 1：数据收集 + 已应用提案闭环验证

**(a) 数据源收集**（主要在 `.claude/workframe-state/`（另含 `.claude/agent-memory/` 与 `projects/` 下若干路径，见下表） 下，由 hook / deterministic scripts / system skills 维护）：
- `.claude/workframe-state/skill-metrics.yaml` — 技能/规则使用汇总（由 `recompute_skill_metrics.py` 从 events.jsonl 重算）
- `.claude/workframe-state/events.jsonl` — 原始事件流（审计/追因时读，日常决策读 metrics 即可）
- `.claude/workframe-state/activity-state.json` — 活跃度 + dormant 状态 + **`pending_maintenance`（status=open）**；若 `dormant=true` 或 `wake_up_pending=true` 则本次自迭代直接退出（除非由 `/core:maintenance-review` 显式触发）。`pending_maintenance` 里的 kind/details 是本次识别模式的重要线索，应与 notes/events 证据一起纳入阶段 2 分析。
- `.claude/agent-memory/*/notes.md` — 各角色微反思
- `.claude/agent-memory/shared/MEMORY.md` 和 `shared/notes.md` — 跨角色共识
- `projects/changelog.md` — 历史操作日志
- `projects/issues/` — 历史问题记录（若有结构化文件）

**(b) 扫描 `projects/proposals/applied/*.yaml` 中 `verified: null` 的条目做闭合验证**：
- 对每条读取 `verify_by`（日期）和 `verify_signal`（需观察到的信号表达式）
- 若今天 ≥ `verify_by`：
  - 读取 skill-metrics.yaml / events.jsonl，判断 `verify_signal` 是否已达成
  - `signal_met=true` → append events.jsonl：`{"ts":"<ISO-8601>","type":"proposal_verified","proposal_id":"<id>","signal_met":true}`；将提案文件中 `verified: true`
  - `signal_met=false` → append events.jsonl：`{"ts":"<ISO-8601>","type":"proposal_verified","proposal_id":"<id>","signal_met":false}`，再 append `{"ts":"<ISO-8601>","type":"proposal_failed","proposal_id":"<id>"}`（供 audit / 下一轮 self-iteration 反思用；**`proposal_failed` 不计入 `check-iteration-trigger.py` 的 problem 加权分**；`recompute_skill_metrics.py` 实际统计的是 `proposal_verified.signal_met=false` 累加到 `proposal_failures_count`，不直接读 `proposal_failed`）；将提案文件中 `verified: false`

### 阶段 2：模式识别 + 置信度评分

从阶段 1 收集的数据中识别候选模式：
- **重复问题**：notes / changelog / events 中有明确证据显示同类问题重复出现。`occurrences` 是置信度计算的证据输入，**不是硬门槛**；低于 3 次仍可计算 confidence，但通常低于提案阈值。
- **低效流程**：仅当 notes / changelog / issues 中存在明确的耗时或阻塞记录时方可使用；系统无耗时事件，**不得凭感觉声称"平均耗时高于预期"**。
- **未覆盖场景**：仅当 notes / changelog 中有明确的"用户重复手工处理"记录时方可使用；**不得无证据臆造"手工处理"场景**。
- **技能低成功率**：近 30 天某 skill `success/invocations < 0.6`（来自 skill-metrics.yaml）。**仅供人工判读，不作自动触发信号**（`success` 为 agent 自评，实测从未产出 false，作触发条件永不满足）——用它时须结合 notes / user_correction 等独立证据，不得仅凭该比值提案。
- **提案失败回路**：有 `proposal_failed` 事件的旧提案 → 反思当初假设，识别失败原因。

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
- `confidence < 0.5` → 只记 `.claude/agent-memory/shared/notes.md`，**不生成提案**
- `0.5 ≤ confidence < 0.8` → 生成提案，进入阶段 3，需用户明确批准（L2 全部走这条）
- `confidence ≥ 0.8` → 生成提案，建议用户快速确认后执行（L1 可走这条，L2 仍需明确批准）
- **Core 文件变更**（`plugins/core/**`、本仓库 `.claude/rules/**` 以外的系统定义资产）一律 **L2 + eval 覆盖**，阈值不降级

### 阶段 3：多候选提案生成

每个通过阈值的模式生成**一份提案文件**，内含 **2-3 个候选方案**，按 `score = impact_int × confidence - risk_penalty` 降序排列：
- `impact_int`：`low=1 / medium=2 / high=3`
- `risk_penalty`：`low=0.25 / medium=0.5 / high=0.75`

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
        type: "new_rule | update_rule | new_skill | update_skill | process_change"
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
- 任何触及以下文件的变更一律 **L2**：`CLAUDE.md` / `.claude/agents/**` / `.claude/rules/**`（含 `local/` 与 `workframe/core/` 两层）/ `.claude/skills/**` / `.claude/settings*.json` / `.workframe-config.json` / `plugins/core/**`
- **MEMORY 冲突 / `[纠正]` 条目冲突** → 强制 L2（不管其他条件）

> **入口分流提示**：`.claude/rules/local/**` 也有"用户显式确认"入口（见 `correction-detection.md` §入口分流），与本 skill 的 L2 提案路径**互不冲突**——前者由用户在纠正回显时直接落盘，后者由 self-iteration 自动提案审批。本 skill 自动提议时一律走 L2，不直接写。

**若阶段 2/3 结束后无任何提案生成**（所有候选模式 confidence < 0.5）：

1. 在 `.claude/agent-memory/shared/notes.md` 补记本次自迭代无提案原因（简短，≤ 2 行）
2. 关闭所有触发本次自迭代的 open 条目——**用代码通道**，它会连同 `reason` 一起写
   `pending_maintenance_dismissed` 事件（条目 7 天后被 GC 清掉，没有事件就查不到
   「它当时为什么关的」）：

   ```bash
   python "$(cat .claude/workframe-state/plugin-root.txt)/scripts/maintenance_workorder.py" \
       --close-pm <PM-ID> [<PM-ID> ...] --reason self_iteration_no_proposal
   ```

   待关闭条目的 kind 为 `cadence_timeout` / `problem_threshold` / `activity_threshold` /
   `memory_backlog` / `completed_delta` 之一——与 `check-iteration-trigger.py` 实际写入的
   kind 集合一致；`skill_low_success` 已移除，存量条目一并关闭。
3. 退出，不进入阶段 4（迭代日期由 `check-iteration-trigger.py` 从 proposals/ 的
   `applied_at` / `rejected_at` 派生，无提案时日期自然不前移；同 kind 信号由
   pending_maintenance 的 dedup upsert 保证只保留一条 open 条目，不会重复堆积）

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

- **L1 + confidence ≥ 0.8**：展示摘要后默认执行推荐候选（用户可事后 `/core:rollback`）
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
   - 遍历提案的 `eval_cases` 路径列表，确认每个路径存在，且至少覆盖：core rule ≥2 正 +1 负；core skill 1 成功 +1 失败；agent 路由 ≥3 样例
   - 未满足则拒绝执行，回退到阶段 4 提示用户补 case
3. **执行变更**：按选中的候选 `proposed_change.description` 修改目标文件
4. **记录 changelog / MEMORY / board tracking**：
   - 在 `projects/changelog.md` 追加变更摘要
   - 若变更影响角色行为，在对应 MEMORY.md 添加说明
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
7. **写 rollback-index entry**（一条 entry 涵盖**用户选定候选**的所有 target，**不含**未执行候选的 target）：向 `.claude/workframe-state/rollback-index.json` 的 `entries` 数组追加：
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
   python "$(cat .claude/workframe-state/plugin-root.txt)/scripts/maintenance_workorder.py" \
       --close-pm <PM-ID> [<PM-ID> ...]
   ```

   该命令一并写 `pending_maintenance_dismissed` 事件，无需再手写事件行。
**若所有候选均被拒绝**：

1. 提案文件从 `proposals/pending/` 移至 `proposals/rejected/`（用 Bash mv）
2. 在提案文件内写入：`rejected_at: <today>`、`rejection_reason: "<用户给出的拒绝原因>"`
3. 关闭相关 pending_maintenance 条目（同上第 8 步）
4. 不写 proposal_applied 事件，不写验证标记

## 与其他 skill 的协作

- **librarian**：本 skill 消费 `recompute_skill_metrics.py` 维护的 skill-metrics.yaml；若模式涉及记忆层，非冲突 L1 提升可由 librarian 执行，降级/容量候选需 `/core:maintenance-review` 确认
- **session-digest**：SessionEnd hook 只写 session-digest-latest.md 的**骨架**（时间 / 计数 / exit_reason）；本 skill 的变更摘要由 `session-digest` skill 从 events.jsonl 重建后填入，通常发生在下一个会话
- **/core:rollback**：用户不满意本 skill 生成的变更时，通过 rollback skill 回退；阶段 5 第 7 步写入 rollback-index entry，`/core:rollback` 优先读该索引
