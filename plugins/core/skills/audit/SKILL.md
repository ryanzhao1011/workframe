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

> **audit 保持纯只读**——dismiss 等写操作迁到 `/core:maintenance-review --dismiss <PM-ID>`。这样 audit 与 maintenance-review 的边界清晰：audit 报告状态，maintenance-review 修改状态。

## 执行步骤

1. Read `.claude/workframe-state/activity-state.json` — 拿 session_counter / dormant / pending_maintenance / **recent_drift_repairs**
2. Read `.claude/workframe-state/skill-metrics.yaml` — 最近窗口的 skill/rule 汇总
3. 读取 `.claude/workframe-state/events.jsonl` 近 N 天 — 按 type 分组统计（含 summary_drift_repaired / summary_drift_repair_skipped）
4. Glob `projects/proposals/pending/*.yaml` — 列出待审批提案
5. Glob `projects/proposals/applied/*.yaml` 过滤 `verified: null` — 列出待闭环验证
6. Read `.claude/workframe-state/session-digest-latest.md` — 上次会话摘要
7. **Board summary drift check**（**只读不改**）：
   - 调用只读检查命令（插件根路径从 `plugin-root.txt` 取，不依赖 PATH）：
     ```bash
     python "$(cat .claude/workframe-state/plugin-root.txt)/bin/workframe-audit-board-drift"
     ```
   - 输出文本格式：
     ```
     actual: total=N, counts={pending:..,...}, unknown=[..]
     summary: total=M, counts={pending:..,...}
     drift: <empty | field1=summary→actual, field2=...>
     ```
   - audit 只解析 `drift:` 行决定是否展示 drift 状态；不调用 `workframe-recompute-board-summary`（那个会写文件，违反 audit 只读约束）。修复路径走 SessionStart drift check 自动 / `workframe-recompute-board-summary` 兜底命令
   - 调用方式说明：`${CLAUDE_PLUGIN_ROOT}` 在 agent Bash 上下文不可用，CC 官方的 plugin `bin/` PATH 注入在部分环境也不生效（Windows + directory 订阅实测未注入），故统一走 `plugin-root.txt` 配方——该文件由 SessionStart hook 每次会话刷新为当前插件根（正斜杠绝对路径）。若环境里 `workframe-audit-board-drift` 恰好已在 PATH，裸调等价

## 输出格式

```markdown
## 🔍 维护审计报告（近 <N> 天）

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

> 关闭指定条目请使用 `/core:maintenance-review --dismiss <PM-ID>`（audit 保持只读，写操作归 maintenance-review）。

### 📐 Board Summary Drift 健康度

- **当前状态**：✅ summary 与 tasks 实际计数一致 / ⚠️ 发现 N 项 drift（current snapshot）
- **近 30 天修复历史**（来自 activity-state.recent_drift_repairs + events.jsonl）：
  - 自动修复（summary_drift_repaired）：X 次
  - 跳过修复（summary_drift_repair_skipped）：Y 次
    - unknown_statuses_in_tasks: <list>
    - summary_block_not_found / recompute_failed / read_failed / parse_failed: <count>

如近 30 天 ≥3 次自动修复 → 提示：
> ⚠️ 频繁触发 drift 修复可能是 SessionEnd hook 经常未执行或超时。
> 大项目可设环境变量 `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS=5000` 缓解。

如出现 unknown_statuses_in_tasks → 提示：
> ⚠️ 检查 `projects/board.yaml` 中 task status 拼写：<具体值>
> SessionStart drift check 不会自动修复含未知 status 的 board.yaml（C+ 第一保护条件）。

### ⚠️ 其他注意项
- 低成功率 skill: <list>
```

## 约束

- **纯只读**——本 skill 不写任何文件（dismiss 等写操作在 `/core:maintenance-review`）
- `disable-model-invocation: true` 防止 Claude 主动触发"审计"动作造成噪音
- 输出用 Markdown，不贴原始 JSON
- `allowed-tools` 不含 `Write` / `Edit`，从工具层强制只读
