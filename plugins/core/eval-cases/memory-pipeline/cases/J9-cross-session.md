# J9 跨会话状态流转

- 前置：activity-state.json 种子 `session_counter: 10`
- 动作：连续 N 次真实会话（J3a/J4/J1 等旅程会话自然累积，无需专门跑）
- 断言：
  1. counter 严格 +1/会话（种子 10 → 3 次会话后 13，与会话次数逐一对账）
  2. session-digest-latest.md 的 counter/ended_at 与 activity-state 同步
  3. heartbeat-state.json 周/月标记被 HEARTBEAT hook 真实刷新（非空值）
  4. SessionEnd 链事件（session_ended / summary_recomputed / skill_metrics_recomputed）
     每会话成组出现
- 实测 2026-08-07：✅ 4/4（heartbeat weekly_friday=2026-W32、monthly=2026-08）。
