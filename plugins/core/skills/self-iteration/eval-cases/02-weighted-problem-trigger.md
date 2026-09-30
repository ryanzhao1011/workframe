# Self-iteration eval · 02 Weighted problem events trigger self-iteration

**类型**：正例（触发）

## 输入状态
- events.jsonl 近 `EVENTS_WINDOW_DAYS`(7) 天：
  - `user_correction` ×1（权重 3.0）
  - `task_blocked` ×1（权重 2.0）
- 累计 problem 分 = 5.0，≥ 阈值 `PROBLEM_THRESHOLD`(5.0)
- **`activity-state.json` 的 `pending_maintenance` 置空**——同 `dedup_key`
  （`problem_threshold`）的 closed 条目会成为驳回基线，只计 ts 严格晚于 `closed_at`
  的事件，本例期望值随即不成立（驳回基线族见 eval 04）
- `dormant` / `wake_up_pending` 均为 false

## 期望行为
- `activity-state.json` 的 `pending_maintenance` 新增一条 `dedup_key=problem_threshold`
  的 open 条目，其 **`details` 为 `问题类加权分 5.0 ≥ 5.0`**
  （阈值按 `PROBLEM_THRESHOLD` 原样渲染，是 `5.0` 不是 `5`）
- **数值只在 `details` 里**：`check-iteration-trigger.py` 的 stdout 只有一行
  `[自迭代提醒] 新增 N 条维护项（另 touch M 条）；/core:audit 查看。`，**不含分数**
- 该条目经 UserPromptSubmit 注入提示用户执行 self-iteration skill
- 若 `dormant=true` 则完全静默（优先级高于触发逻辑，`main()` 首两行即退出）
