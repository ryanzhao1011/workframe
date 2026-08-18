# J5 maintenance 两阶段（judge → --commit）

- 前置：pm notes 有余量条目、promotion-candidates 有 1 条未拍板、proposals/applied
  有逾期未验证提案、pending_maintenance 有自迭代节奏信号（cadence/problem）
- 动作 phase 1（judge）：
  `claude -p "<工单 prompt>" --maintenance --permission-mode acceptEdits --add-dir <plugin根>`
  ——**prompt 必须放在 --add-dir 之前**（--add-dir nargs 贪婪，会把后置 prompt 吞成目录）
- 断言 phase 1：
  1. Setup hook（matcher=maintenance）自动生成工单，含执行规约五条（敏感闸/Read SKILL.md
     兜底/manifest 机制/close_pm 收紧判据/L2 隔离）
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
  workframe-state）。中途两次中断（session limit / API 断连）后 `--resume` 接续，评估
  结论无损落盘——judge 中断重入路径顺带验证。
