# X3 dormant 全链静默

- 前置：pending_maintenance 里有一条 open 的 `memory_backlog`、且从未提醒过（`maintenance_notified` 为空，保证唯一变量是 dormant）
- 动作：activity-state 置 `dormant: true` → 直调首问维护提醒（`echo '{"session_id":"x3","prompt":"hi"}' | python user-prompt-inject.py`）
- 断言：不出维护提醒（dormant 闸优先于提醒）；`wake_up_pending: true` 同理；复原后同一会话号再调能出提醒（证明前一步的静默来自 dormant，不是提醒本身坏了）
- 上次实测 2026-08-07 测的是改版前的入口（开场记忆巡检卡）；本版入口待重跑。测后 dormant 复原。
