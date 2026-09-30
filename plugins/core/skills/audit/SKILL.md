---
name: audit
description: 审计最近一段时间的自动维护活动（events / proposals / decay / digest），给用户汇总报告
user-invocable: true
disable-model-invocation: true
allowed-tools: [Read, Glob, Grep, Bash]
---

# /core:audit 审计维护活动

## 用途

用户显式 `/core:audit` 时执行。**Claude 不会自动调用**（`disable-model-invocation: true`）。

回答五类问题：
1. 最近 N 天自动发生了什么维护动作？（events.jsonl 聚合）
2. 有没有 T3/T4 待处理项？（activity-state.pending_maintenance + proposals/pending/）
3. 有没有失败的提案或低成功率的 skill？（skill-metrics.yaml）
4. **board summary 是否健康**？（current drift + 近 30 天修复历史）
5. **skill-metrics 是否刷新**？（`skill_metrics_recomputed` 事件 + 当前 `generated_at`）

## 输入

参数格式（自由文本）：
- 无参数 → 默认近 14 天
- `7d` / `30d` → 指定窗口
- `spotcheck=<n>` → 纪律抽查每扇门抽最近 n 场会话（默认 3）
- `--record` → 报告照常输出，**并**在末尾追加一条 `discipline_spotcheck` 事件（见「纪律抽查」一节最后一步）；不带就一个字节都不写

> **audit 默认只读；带 `--record` 时追加一条抽查事件，其余不写**——dismiss 等写操作迁到 `/core:maintenance-review --dismiss <PM-ID>`。这样 audit 与 maintenance-review 的边界清晰：audit 报告状态，maintenance-review 修改状态。

## 执行步骤

1. Read `.workframe/state/activity-state.json` — 拿 session_counter / dormant / pending_maintenance / **recent_drift_repairs**
2. Read `.workframe/state/skill-metrics.yaml` — 最近窗口的 skill/rule 汇总
3. 读取 `.workframe/state/events.jsonl` 近 N 天 — 按 type 分组统计（含 summary_drift_repaired / summary_drift_repair_skipped）
4. Glob `projects/proposals/pending/*.yaml` — 列出待审批提案
5. Glob `projects/proposals/applied/*.yaml` 过滤 `verified: null` — 列出待闭环验证
6. Read `.workframe/state/session-digest-latest.md` — 上次会话摘要
7. **Board summary drift check**（**只读不改**）：
   - 调用只读检查命令（插件根路径从 `plugin-root.txt` 取，不依赖 PATH）：
     ```bash
     python "$(cat .workframe/state/plugin-root.txt)/bin/workframe-audit-board-drift"
     ```
   - 输出文本格式：
     ```
     actual: total=N, counts={pending:..,...}, unknown=[..]
     summary: total=M, counts={pending:..,...}
     drift: <empty | field1=summary→actual, field2=...>
     ```
   - audit 只解析 `drift:` 行决定是否展示 drift 状态；不调用 `workframe-recompute-board-summary`（那个会写文件，违反 audit 默认只读的约束）。修复路径走 SessionStart drift check 自动 / `workframe-recompute-board-summary` 兜底命令
   - 调用方式说明：`${CLAUDE_PLUGIN_ROOT}` 在 agent Bash 上下文不可用，CC 官方的 plugin `bin/` PATH 注入在部分环境也不生效（Windows + directory 订阅实测未注入），故统一走 `plugin-root.txt` 配方——该文件由 SessionStart hook 每次会话刷新为当前插件根（正斜杠绝对路径）。若环境里 `workframe-audit-board-drift` 恰好已在 PATH，裸调等价
8. **纪律抽查**：按下方「纪律抽查」一节抽样、判定，结果进报告的「纪律抽查」段；**只有用户这次带了 `--record`**，才执行该节最后一步的写入

## 纪律抽查

收口检查只看得见「有落盘产物的纪律」。下面两条没有产物，只能从会话记录里抽查。它的定位是把失效率从「不可观测」变成「可估计」——**样本小时抓不到低频失效**，不能读成「有抽查所以安全」。

### 判据表（版本 1）

先看「触发」，触发了再看「违规」；命中「不适用」列的样本既不算适用、也不算违规。

| id | 判据 | 触发（字面可判） | 违规 | 不适用 | 抽样面 |
|---|---|---|---|---|---|
| S1 | 陈述「已验证」须说明范围 | 助手消息含「已验证」「验证通过」「已测过」「验证完成」任一 | 同一条消息里「验了」「覆盖」「不含」「没验」「未验」「范围」一个都没有 | 无触发字面 | 主会话 ＋ 子 agent（这条纪律两边都投） |
| S2 | 选择题式澄清须用 `AskUserQuestion` | 助手发给用户的消息里连续 ≥2 行以 `A.` / `A、` / `(A)` / `1.` 这类选项标记起头，且消息以问号结尾或含「选哪」「哪个」 | 触发，且同一轮里没有 `AskUserQuestion` 调用；转述子 agent 的选择题同样算 | 该会话是非交互运行：transcript 行的 `entrypoint` 为 `sdk-cli`（工具不可用；没有这个字段的记录按交互会话判）；开放式问题与要用户填具体值的问题（每个选项行本身都以问号结尾）；Codex 门 | 只主会话（子 agent 拿不到这个工具） |

判据表之外的纪律不进抽查：凡是要模型二次判断才能定违规的，一律不收。

**S2 的「不适用」只认看得见的受限信号**（`entrypoint` 为 `sdk-cli`、Codex 门）。`AskUserQuestion` 是常驻工具，transcript 不记它可不可用，所以「整场没调用过它、也没有 `ToolSearch` 记录」**不算**工具不可用——整场都用纯文本选项、从不调工具，正是 S2 最要抓的形态。

### 从哪抽

- **CC**：读步骤 3 所在的同一个状态目录下的 `inject-log.jsonl`，取 `harness` 为 `cc` 的行里最近出现的不同 `session_id`。主会话记录 glob `~/.claude/projects/*/<session_id>.jsonl`，子 agent 记录 glob `~/.claude/projects/*/<session_id>/subagents/*.jsonl`，**只保留行内 `cwd` 字段落在本项目根之内的**。不要自己拼项目目录名：非 ASCII 字符与空格都会被替换成 `-`，不同路径可能撞成同一个名字。
- **Codex**：只 glob `<CODEX_HOME>/sessions/**/rollout-*.jsonl`（`CODEX_HOME` 未设时是 `~/.codex`），按首条 `session_meta` 的 `cwd` 落在本项目根之内过滤，按文件修改时间取最近几场。**不读 `CODEX_HOME` 根目录下的任何文件，`auth.json` 一个字节都不碰。**
- 每门默认最近 3 场，`spotcheck=<n>` 可调。一门取不到样本就写「本门无样本」，**不写「0 违规」**。

### 带 `--record` 时的写入（唯一的写入）

报告输出完之后执行一次（插件根的取法与步骤 7 相同）：

```bash
python "<插件根>/bin/workframe-event" discipline-spotcheck --sessions-sampled cc=<场数>,codex=<场数> --criterion S1=<适用次数>/<违规次数> --criterion S2=<适用次数>/<违规次数> --criteria-version 1 --role main --harness <当前门 cc|codex>
```

- 取不到样本的门不写进 `--sessions-sampled`（如只写 `cc=3`）；**两扇门都取不到样本时不执行这一步**：不记事件，报告里写「两门均无样本」
- `--criterion` 每个判据 id 写且只写一次，只收判据表里的 id；重复或表外的 id 命令直接拒绝（exit 2，不写）
- 它往事件流追加**一条** `discipline_spotcheck`，本 skill 其余一个字节不写；不带 `--record` 时这一步不执行

## 输出格式

**报告首行必须是数据来源分列**，在任何计数之前。取自 `skill-metrics.yaml` 的
`harness_breakdown` 段（由 `recompute_skill_metrics.py` 现算于 `event-schema.json` 的
`availability`）——**照抄那一段，不要在这里另记一份缺口清单**：清单一变，副本立刻成为假话。

没有该段时（旧版 skill-metrics 尚未重算）说明「本次未取到分列」，**不要用「都是 cc」顶替**。
只有 `gaps` 而没有 `cross_door_gaps` 的，是本次改造之前的旧格式——那份 `gaps` 里**混着两侧皆无的类型**，
照抄会把跨门缺口数报大；这种情况说明「分列格式过期，请重算」，不要照抄。

**`full` 不等于「已验过」**：模型侧事件判 `full` 是因为「模型写一行」本身与门无关，但**「模型会被告知
去写」**是另一条链，可能还没接到那扇门上。这一格**已经是一张现算的表**——`full_but_delivery_unverified`，
和上面三张一样照抄即可，**不要在这里另写一段说明**：手写的 caveat 是副本，schema 一改它就漂，而这正是
本节自己禁止的事。判据全文见 `event-schema.json` 的 `availability_values.__how_to_judge__`。

为什么这一行不能省：一类事件为零，可能是「真的没发生」，也可能是「那扇门根本产不出它」。
不分开说，读者会把 `turn_failed` 在 Codex 侧恒为 0 读成「Codex 会话失败更少」；同理，
Codex 的 `PostToolUse` 对**失败的**工具调用不触发，任何以它为输入的计数在那一侧系统性偏低。

```markdown
## 🔍 维护审计报告（近 <N> 天）

> 数据来源：cc <N> 条 / codex <M> 条 / unknown <K> 条 / unstamped <L> 条（无 harness 字段的
> 存量行，按 schema 缺省读作 cc）。**跨门缺口**（对门产得出、本门产不出）：
> <cross_door_gaps.codex 逐字照抄>；一路缺失或未判定：<partial.codex>；
> 一条已确立路径都没有：<unverified.codex>。
> 两扇门都没有 producer 的（与门无关，**不算跨门缺口**）：<neither_door>。
> 判 `full` 但投递未确立（该门零计数同样可能是结构性的）：<full_but_delivery_unverified.codex>。

### 📊 事件统计
- skill_used: X 次（top: librarian ×5 / task-management ×3）
- rule_triggered: N/A（无 deterministic producer，字段保留仅为兼容；见 event-schema.json）
- user_correction: Z 次（problem 信号）
- task_blocked: W 次
- skill_metrics_recomputed: M 次（最近 generated_at: <ISO>）
- proposal_applied: A 次，proposal_verified: B 次（signal_met=true），proposal_failed: C 次（best_effort；依赖 self-iteration 执行）

### 🗂️ 提案状态
- pending 待审批：N 条
  - [PROP-...] confidence=0.75 | L2 | pattern=...
- applied 待验证：M 条（verify_by 到期前不闭合）

### 🏁 活动指标
- session_counter: xxx
- active_sessions_30: yy
- dormant: false (profile=normal)
- wake_up_pending: false
- last_digest_at: <ISO>

### ⚠️ pending_maintenance（按 severity 排序）
- 总数：N 条（open: N1 / closed: N2）
- critical: ...
- warn: ...
  - [PM-20260424-003] cadence_timeout — 距上次自迭代 10 天
- info: ...

> 关闭指定条目请使用 `/core:maintenance-review --dismiss <PM-ID>`（audit 默认只读，唯一的写入是 `--record` 追加的那条抽查事件；修改状态的写操作归 maintenance-review）。

### 📐 Board Summary Drift 健康度

- **当前状态**：✅ summary 与 tasks 实际计数一致 / ⚠️ 发现 N 项 drift（current snapshot）
- **近 30 天修复历史**（来自 activity-state.recent_drift_repairs + events.jsonl）：
  - 自动修复（summary_drift_repaired）：X 次
  - 跳过修复（summary_drift_repair_skipped）：Y 次
    - unknown_statuses_in_tasks: <list>
    - summary_block_not_found / recompute_failed / **recompute_skipped** / read_failed / parse_failed: <count>
      （`recompute_skipped` = 重算被它自己的 strict 保护拦下、**一个字段都没改**；
      `detail` 里是 recompute 返回的原因。**别把它算进「自动修复」那一行**）

如近 30 天 ≥3 次自动修复 → 提示：
> ⚠️ 频繁触发 drift 修复可能是 SessionEnd hook 经常未执行或超时。
> 大项目可设环境变量 `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS=5000` 缓解。

如出现 unknown_statuses_in_tasks → 提示：
> ⚠️ 检查 `projects/board.yaml` 中 task status 拼写：<具体值>
> SessionStart drift check 不会自动修复含未知 status 的 board.yaml（C+ 第一保护条件）。

### 纪律抽查（判据表版本 1）
- 样本：cc <n> 场（主会话 <a> / 子 agent <b>）；codex <m> 场（取不到写「本门无样本」）
- S1「已验证」须说明范围：适用 <x> 次，违规 <y> 次（违规例：<会话号前 8 位>「<原句>」）
- S2 选择题须用 AskUserQuestion：适用 <x> 次，违规 <y> 次（Codex 门恒不适用）
- 灵敏度：样本小时抓不到低频失效；本段把失效率从「不可观测」变成「可估计」，不是「保证不失效」
- 记录：本次带了 `--record` → 已追加一条 `discipline_spotcheck` / 未带 → 未写入

### ⚠️ 其他注意项
- 低成功率 skill: <list>
```

## 约束

- **默认只读**——不带 `--record` 时本 skill 不写任何文件；带 `--record` 时只经 `workframe-event` 追加一条 `discipline_spotcheck` 事件，其余一个字节不写（dismiss 等写操作在 `/core:maintenance-review`）
- `disable-model-invocation: true` 防止 Claude 主动触发"审计"动作造成噪音
- 输出用 Markdown，不贴原始 JSON
- `allowed-tools` 含 `Bash`，工具层**并不**强制只读——只读靠本 skill 的约定
