# J5 maintenance 两阶段（judge → --commit）

- 前置：pm notes 有余量条目、promotion-candidates 有 1 条未拍板、proposals/applied
  有逾期未验证提案、pending_maintenance 有自迭代节奏信号（cadence/problem）
- 动作 phase 1（judge）：
  `claude -p "<工单 prompt>" --maintenance --permission-mode acceptEdits --add-dir <plugin根>`
  ——**prompt 必须放在 --add-dir 之前**（--add-dir nargs 贪婪，会把后置 prompt 吞成目录）
- 断言 phase 1：
  1. Setup hook（matcher=maintenance）自动生成工单：「执行规约」段五条——只做 L1 与记录性操作 /
     L2 一律不动 / 只用 Read·Edit·Write、不用 Bash / 运行态目录下的记账（sidecar·events·关信号·
     工单打勾）不自己写、记进 manifest 由代码统一落盘（角色记忆目录由会话直接写）/ 先 Write
     manifest 再输出执行摘要；另有 §1 的 Read SKILL.md 兜底（有 notes 积压时出现）与 §4 的
     close_pm 收紧判据（有 open 信号时出现）。条数与措辞以 `maintenance_workorder.py` 的
     `build_workorder()` 为准
  2. notes 评估落盘 + 快照 `logs/librarian-snapshots/` + 运行日志 `logs/librarian/`
  3. 逾期提案 verified 回写，「无法验证」与「验证失败」明确区分
  4. **close_pm 为空**——cadence_timeout / problem_threshold 不由批处理关闭
  5. manifest `logs/maintenance-commit.json` 结构合法（promotions/extra_events/close_pm/done）
- 动作 phase 2：`CLAUDE_PROJECT_DIR=<沙盒> python maintenance_workorder.py --commit`
- 断言 phase 2：
  6. sidecar/events 由代码落盘（memory_promoted + skill_used + proposal_verified 按 schema）
  7. 工单全部打勾、manifest 归档 `.applied.json`
  8. flag 不删除属设计内（30 分钟 mtime 自动过期）
- 实测 2026-08-07：✅ 8/8。全程 0 permission denials（工单规约让模型根本不去碰
  状态目录）。中途两次中断（session limit / API 断连）后 `--resume` 接续，评估
  结论无损落盘——judge 中断重入路径顺带验证。
