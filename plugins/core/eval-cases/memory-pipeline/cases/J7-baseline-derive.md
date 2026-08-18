# J7 自迭代 baseline 代码派生

- 前置：沙盒 `projects/proposals/applied/` 放真实提案文件（含 applied_at 字段）；
  board.yaml 真实副本
- 动作：任意真实会话触发 SessionStart（check-iteration-trigger 挂载执行）
- 断言：
  1. `pending_maintenance` 出现的 cadence_timeout 天数 = 今天 − max(applied_at/rejected_at)
     ——纯代码派生，**手工记账 baseline 文件已不存在**，假「84 天」类断账警报不可能再现
  2. completed 增量 = board.yaml 现算（status=completed 且 updated_at ≥ 派生日期）
  3. problem_threshold 从种子 events 现算加权分——证明读的是沙盒真实事件流
- 实测 2026-08-07：✅。cadence 报 11 天（applied 最新 2026-07-26，UTC 口径），
  与派生规则严格一致；主项目种子 events 的问题分被如实现算（15.0→18.0 随新事件累积）。
