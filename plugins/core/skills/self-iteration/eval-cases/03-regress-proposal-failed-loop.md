# Self-iteration eval · 03 Applied proposal fails verify → proposal_failed signal

**类型**：回归（闭环验证失败）

## 输入状态
- `proposals/applied/PROP-20260401-001.yaml`:
  - `verify_by: 2026-04-15`
  - `verify_signal: librarian success ≥ 0.7`
  - `verified: null`
  - `applied_option: B`
- 今天 = 2026-04-20（已过 verify_by）
- skill-metrics.yaml `librarian.successes/invocations = 0.5`

## 期望行为
- 阶段 1(b) 扫描到 `verified: null` 且今天 >= verify_by
- 判定 signal_met=false → 提案文件 `verified: false`
- events.jsonl append `{"ts":"...","type":"proposal_verified","proposal_id":"PROP-20260401-001","signal_met":false}`
- events.jsonl append `{"ts":"...","type":"proposal_failed","proposal_id":"PROP-20260401-001"}`
- recompute_skill_metrics.py 下次重算后 `proposal_failures_count` +1
- 下一轮 self-iteration 阶段 2 可把该失败作为**反思证据**（"提案失败回路"候选模式）
- **`check-iteration-trigger.py` problem 分不因 `proposal_failed` 增加**（`proposal_failed` 是 model_mediated，已从 PROBLEM_WEIGHTS 移除，见 v0.2.2-fixup-2）
