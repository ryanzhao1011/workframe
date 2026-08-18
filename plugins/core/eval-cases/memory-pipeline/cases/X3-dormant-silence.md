# X3 dormant 全链静默

- 前置：notes 积压 ≥5、无 ask-state（保证唯一变量是 dormant）
- 动作：activity-state 置 `dormant: true, dormant_profile: "deep"` → 直调 memory-ask
- 断言：静默（dormant 闸优先于 backlog 闸）；`wake_up_pending: true` 同理
- 实测 2026-08-07：✅。测后 dormant 复原 normal。
