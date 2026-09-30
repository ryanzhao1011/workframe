---
name: maintenance-review
description: Dormant 项目唤醒后的手动维护入口，逐步执行 librarian / self-iteration / decay
user-invocable: true
disable-model-invocation: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion]
---

# /core:maintenance-review 唤醒后手动维护

## 用途

dormant 项目被重新打开后，**自动维护链路全部暂停**。用户主动 `/core:maintenance-review` 才恢复正常维护。

**Claude 不会自动调用**（`disable-model-invocation: true`）。

## 输入

两种调用模式：

1. **完整 wake-up review**（无参数）：按顺序走完 3 个检查点，每步都用 `AskUserQuestion` 确认是否继续
2. **快速 dismiss 模式**（`--dismiss <PM-ID>`）：把指定 ID 的 `pending_maintenance` 条目关闭（经模式 A 的代码通道执行；audit 默认只读、不改任何状态，写操作归本 skill）。不触发 librarian / self-iteration

## 执行步骤

### 模式 A：`--dismiss <PM-ID>`（快速关闭单条）

1. Read `.workframe/state/activity-state.json`，确认 `id == <PM-ID>` 的条目存在且
   `status == "open"`，记下它的 `kind` / `severity`（用于第 3 步向用户复述）
2. 用代码通道关闭（**不要自己改这个文件**）——它一并写 `pending_maintenance_dismissed`
   事件（`ts` / `at` 由脚本填同一时刻，二者都是 schema 必填）：

   ```bash
   python "$(cat .workframe/state/plugin-root.txt)/scripts/maintenance_workorder.py" \
       --close-pm <PM-ID>
   ```

   > `activity-state.json` 还装着 `session_counter` / `recent_drift_repairs` / 注入标记等
   > 攒出来的状态，整份重写漏字段不报错、只静默丢历史；脚本走文件锁 + 原子替换 + 三方合并，
   > 只动 `pending_maintenance` 一处。
3. 在响应中输出：`已关闭 PM-ID=<PM-ID>，原 kind=<X> severity=<Y>`
4. **不触发 librarian / self-iteration**；该条目会在 SessionStart 的定期 GC 后从数组中
   清理（保留天数见 `session-start-prep.py` 的 `PM_CLOSED_RETENTION_DAYS`）

> **关掉一条信号不只是清提醒**：它会把**该 kind** 的计分起点推进到本次 `closed_at`，
> 此后只算更晚发生的证据。按 kind 各管各的，关一条不影响其余。所以第 1 步复述 `kind`
> 给用户时，要让他明白关掉它等于声明此刻之前的这类证据都已闭环：
>
> | kind | 关掉它 = 声明 | 要重新报，需要 |
> |---|---|---|
> | `problem_threshold` | 此刻之前的问题都已闭环 | 更晚的 `user_correction` / `task_blocked` 攒够分 |
> | `activity_threshold` | 这批活动量已经看过了 | 更晚的 `skill_used` 攒够分 |
> | `cadence_timeout` | 现在不打算迭代 | 从驳回那天起再过一个提醒周期 |
> | `completed_delta` | 这批完成量已经看过了 | 驳回**之后**再完成够数的任务 |
> | `memory_backlog` | 这批 notes 现在不打算整理 | 静默一个保留期后重报（不问 notes 有没有变——它扫的是当前状态，没有可比的时间点） |
> | `close_check_due` | 这批 stale 标记现在不打算收口 | 驳回**之后**再有模块被标 stale（同一模块被再次触碰也算——标记时间会刷新） |
>
> 判断依据是那些证据本身是否真的处理完了，不是「这条提醒看着烦」。
> **抑制有期限**：它靠那条 closed 条目活着，条目被 GC 删掉后即失效。对
> `completed_delta` 与 `close_check_due` 而言到期必然重报一次（前者的分子是累计值、
> 后者的 stale 清单只有 code-to-doc 会清，两边都没有滚动窗口），属预期行为不是缺陷。
> `memory_backlog` 同族且更明显：`cadence` / `problem` / `activity` / `completed_delta`
> 的条件会随产出提案 / 跑迭代 / 事件滑出窗口自然归零，**notes 积压只有 librarian 会清**
> ——所以它的实际形态是「每个保留期提醒一次」，不是「驳回一次就永远安静」。
> `close_check_due` 在保留期内仍有区别：它按每条 stale 的标记时间过滤，**新标记照样
> 报得出来**，被静音的只是驳回时已在清单里的那批。

### 模式 B：完整 wake-up review（无参数）

1. **会话摘要**
   - Read `.workframe/state/activity-state.json` — 告知 dormant 多少天
   - Read `.workframe/state/session-digest-latest.md` — 上次活跃时的摘要
   - 问用户："继续执行 Librarian 整理吗？" (A: 是 / B: 跳过本步 / C: 取消全部)

2. **Librarian 整理（如用户同意）**
   - 以 `manual-maintenance=true` 上下文调用 librarian skill
   - 用户已确认继续整理时，允许处理 dormant / wake_up_pending 下被冻结的 promotion 候选和 decay / 容量候选
   - 输出整理摘要（整理了几条、提升几条、确认降级几条、仍待审查几条）
   - 问："继续执行 self-iteration 吗？"

3. **Self-iteration 提案（如用户同意）**
   - 调用 self-iteration skill
   - 输出提案摘要（生成几个候选、阶段 4 审批清单）
   - 问："提案是否批准执行？（逐条选）"

4. **结尾**
   - 关闭本次处理过的 pending_maintenance 条目——走模式 A 第 2 步的
     `--close-pm <PM-ID> ...`（可一次传多个 ID），不要手改 activity-state.json
   - 结束 wake-up 状态（`dormant=false` / `wake_up_pending=false`）同样走代码通道：

     ```bash
     python "$(cat .workframe/state/plugin-root.txt)/scripts/maintenance_workorder.py" --wake-done
     ```

     > **别直接 Edit 这个文件**——即使这两个字段本身是幂等赋值，用 Edit 改也等于
     > **整份 JSON 重写**：你读到的那一份会被整体写回，期间别的 hook 写进来的
     > `session_counter` / `pending_maintenance` / drift 历史统统被盖掉。
     > 字段幂等 ≠ 写入方式幂等。
   - 追加 events.jsonl `{"ts":"<now ISO-8601>","type":"maintenance_review_completed"}`
   - 输出下次建议时间

## 约束

- 必须每步等用户确认
- 任何写操作前做 snapshot / 备份（复用 librarian + self-iteration 自己的备份机制）
- 若用户随时取消，保留已完成步骤的结果，清理 pending_maintenance 中对应项
- `disable-model-invocation: true`（防止 Claude 自作主张把 dormant 项目"唤醒并整理"）

## 相邻入口：`workframe-maintenance` 批处理（不属本 skill，仅索引）

轻量维护有独立的**非交互批处理入口**：终端运行 `workframe-maintenance`（bin 包装；命令不在 PATH 时用 `python "<插件根>/bin/workframe-maintenance"`，插件根见 `.workframe/state/plugin-root.txt`）→
`claude -p --maintenance` → Setup hook（`scripts/maintenance_workorder.py`）把四路保养事项
（notes 积压 / 待拍板候选计数 / 逾期提案验证 / pending_maintenance open 信号 + doctor 异常项）
聚合成 `.workframe/state/maintenance-workorder.md` 工单，模型照单执行。

与本 skill 的分工：批处理只做 **L1 与记录性操作**（print 模式无交互卡，L2 一律攒到
promotion-candidates.md）；需要逐步确认、处理 L2 候选、或 dormant 唤醒的场景走本 skill。
