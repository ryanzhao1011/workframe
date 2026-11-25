# J9 跨会话状态流转

- 前置：activity-state.json 种子 `session_counter: 10`
- 动作：连续 N 次真实会话（J4/J1 等旅程会话自然累积，无需专门跑）
- 断言：
  1. counter 严格 +1/会话（种子 10 → 3 次会话后 13，与会话次数逐一对账）
  2. activity-state 的 `last_session_at` 每会话**启动时**刷新为本次开场时刻（它在 SessionStart 写，不是结束时）
  3. 每会话结束时本轮新增一条 `session_ended` 事件（结束时刻以它为准）
  4. SessionEnd 链事件（session_ended / summary_recomputed / skill_metrics_recomputed）
     每会话成组出现
- 上次实测 2026-08-07 用的是改版前的断言（含已撤的摘要与周月标记两项）；本版断言待重跑。
