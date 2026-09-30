# J7 自迭代 baseline 代码派生

> **三条断言不能一次跑齐**：断言 1/3 要求对应信号命中，而断言 2 的 `completed_delta`
> 是**兜底信号**（`if not signals` 才评估），只在其余信号都不命中时才产生条目。
> 故分 A / B 两个 fixture 跑。

- 公共前置：
  - 沙盒 `projects/proposals/{applied,rejected}/` 放真实提案文件（`applied_at` /
    `rejected_at`）——**两个目录都要**，派生取两者最大值
  - `board.yaml` 真实副本，completed 任务带 `completed_at` 字段
  - **`activity-state.json` 的 `pending_maintenance` 置空**：同 `dedup_key` 的 closed
    条目会成为驳回基线，把下述三条的取数**全部改道**（见 `dismissal_baseline()`）；
    不置空则本用例无确定期望。驳回基线那一族由 self-iteration eval 04 单独覆盖
  - `dormant` / `wake_up_pending` 均为 false
- 动作：任意真实会话**触发 Stop**（check-iteration-trigger 挂在 **Stop**，不是
  SessionStart），或直调 `CLAUDE_PROJECT_DIR=<沙盒> python check-iteration-trigger.py`
- 读数位置：`activity-state.json` 的 `pending_maintenance[].details`
  ——stdout 只有一行 `[自迭代提醒] 新增 N 条维护项…`，**不含任何数值**
- 断言（fixture A · 令 cadence 与 problem 命中）：
  1. `cadence_timeout` 天数 = **本地今天** − max(applied_at, rejected_at)
     ——纯代码派生（`derive_last_iteration_date()` + `days_since()`，后者取本地日期），
     **手工记账 baseline 文件已不存在**，假「84 天」类断账警报不可能再现
  3. `problem_threshold` 加权分 = **近 `EVENTS_WINDOW_DAYS`(7) 天**种子 events 现算
     ——证明读的是沙盒真实事件流
- 断言（fixture B · 令其余信号均不命中，只留兜底）：
  2. `completed_delta` 增量 = board.yaml 现算：status=completed 的任务，完成日期取
     `completed_at`、**缺该字段才回退 `updated_at`**，计 ≥ 派生日期的条数
- 断言依据：TASK-018 / TASK-021 / TASK-024 交付时的探针实测（2026-08-26、
  2026-08-27 两段）
- 历史实测 2026-08-07（**口径已变，不作当前凭证**）：✅。cadence 报 11 天（applied
  最新 2026-07-26），与当时的派生规则一致；种子 events 的问题分被如实现算
  （15.0→18.0）。该记录早于 TASK-018/021/024 三批基线改动；原文标注的「UTC 口径」
  亦有误——`days_since()` 一直用本地日期。
- 待重测：J7 本身的端到端重跑未安排（断言依据见上一条）
