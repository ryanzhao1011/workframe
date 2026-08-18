# Self-iteration eval · 02 Weighted problem events trigger self-iteration

**类型**：正例（触发）

## 输入状态
- events.jsonl 近 7 天：
  - `user_correction` ×1（权重 3.0）
  - `task_blocked` ×1（权重 2.0）
- 累计 problem 分 = 5.0，≥ 阈值 5

## 期望行为
- `check-iteration-trigger.py` 输出包含 "问题类加权分 5.0 ≥ 5"
- 提示用户执行 self-iteration skill
- 若 dormant=true 则完全静默（优先级高于触发逻辑）
