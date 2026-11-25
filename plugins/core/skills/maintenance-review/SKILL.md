---
name: maintenance-review
description: 维护项关单与 dormant 项目唤醒后的手动维护入口，逐步执行 librarian 整理与 decay
user-invocable: true
disable-model-invocation: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion]
---

# /core:maintenance-review 唤醒后手动维护

后台作业与本手动入口分工：`workframe-selfiter status` 查看后台建议、旧待批提案与复测记录；用户选择查看后按 self-iteration 的后台作业段逐项确认，不能把“稍后”记成驳回。后台关闭或执行器不可用时保留本入口；`--dismiss` 不会替用户批准自动提案。旧提案仍按原验证信号人工核对，不从自由文本猜测机器验证方法。

## 用途

dormant 项目被重新打开后，**自动维护链路全部暂停**。用户主动 `/core:maintenance-review` 才恢复正常维护。

**Claude 不会自动调用**（`disable-model-invocation: true`）。

## 输入

两种调用模式：

1. **完整 wake-up review**（无参数）：按顺序走完 2 个检查点，Librarian 整理前用 `AskUserQuestion` 确认是否继续
2. **快速 dismiss 模式**（`--dismiss <PM-ID>`）：把指定 ID 的 `pending_maintenance` 条目关闭（经模式 A 的代码通道执行；audit 默认只读、不改任何状态，写操作归本 skill）。不触发 librarian

## 执行步骤

### 模式 A：`--dismiss <PM-ID>`（快速关闭单条）

1. Read `.workframe/state/activity-state.json`，确认 `id == <PM-ID>` 的条目存在且
   `status == "open"`，记下它的 `kind` / `severity`（用于第 3 步向用户复述）
2. 用代码通道关闭（**不要自己改这个文件**）——它一并写 `pending_maintenance_dismissed`
   事件（`ts` / `at` 由脚本填同一时刻，二者都是 schema 必填；`$WF_PLUGIN_ROOT` 换成会话开场注入的「本会话插件根」）：

   ```bash
   python "$WF_PLUGIN_ROOT/scripts/maintenance_workorder.py" \
       --close-pm <PM-ID>
   ```

   > `activity-state.json` 还装着 `session_counter` / `recent_drift_repairs` / 注入标记等
   > 攒出来的状态，整份重写漏字段不报错、只静默丢历史；脚本走文件锁 + 原子替换 + 三方合并，
   > 只动 `pending_maintenance` 一处。
3. **按命令实际回报**输出：命中的写 `已关闭 PM-ID=<PM-ID>，原 kind=<X> severity=<Y>`；命令报「未命中」的如实说没有关闭
4. **不触发 librarian**；该条目会在 SessionStart 的定期 GC 后从数组中
   清理（保留天数见 `session-start-prep.py` 的 `PM_CLOSED_RETENTION_DAYS`）

> **关掉一条信号不只是清提醒**：它会把**该 kind** 的起点推进到本次 `closed_at`。
> 按 kind 各管各的，关一条不影响另一条。所以第 1 步复述 `kind`
> 给用户时，要让他明白关掉它等于声明什么：
>
> | kind | 关掉它 = 声明 | 要重新报，需要 |
> |---|---|---|
> | `memory_backlog` | 这批 notes 现在不打算整理 | 静默一个保留期后重报（不问 notes 有没有变——它扫的是当前状态，没有可比的时间点） |
> | `close_check_due` | 这批 stale 标记现在不打算收口 | 驳回**之后**再有模块被标 stale（同一模块被再次触碰也算——标记时间会刷新） |
>
> 判断依据是那些证据本身是否真的处理完了，不是「这条提醒看着烦」。
> **抑制有期限**：它靠那条 closed 条目活着，条目被 GC 删掉后即失效，到期必然重报一次——
> notes 积压只有 librarian 会清、stale 清单只有 code-to-doc 会清，属预期行为不是缺陷。
> `memory_backlog` 的实际形态是「每个保留期提醒一次」；`close_check_due` 在保留期内仍有区别：
> 它按每条 stale 的标记时间过滤，**新标记照样报得出来**，被静音的只是驳回时已在清单里的那批。

### 模式 B：完整 wake-up review（无参数）

1. **上次停在哪**
   - Read `.workframe/state/activity-state.json` — 告知 dormant 多少天
   - 看板里 `in_progress` / `blocked` 的任务（及其 notes 指向的交接稿）— 上次活跃时手上的事
   - 问用户："继续执行 Librarian 整理吗？" (A: 是 / B: 跳过本步 / C: 取消全部)

2. **Librarian 整理（如用户同意）**
   - 以 `manual-maintenance=true` 上下文调用 librarian skill
   - 用户已确认继续整理时，允许处理 dormant / wake_up_pending 下被冻结的 promotion 候选和 decay / 容量候选
   - 输出整理摘要（整理了几条、提升几条、确认降级几条、仍待审查几条）

3. **结尾**
   - 关闭本次处理过的 pending_maintenance 条目——走模式 A 第 2 步的
     `--close-pm <PM-ID> ...`（可一次传多个 ID），不要手改 activity-state.json
   - 结束 wake-up 状态（`dormant=false` / `wake_up_pending=false`）同样走代码通道：

     ```bash
     python "$WF_PLUGIN_ROOT/scripts/maintenance_workorder.py" --wake-done
     ```

     > **别直接 Edit 这个文件**——即使这两个字段本身是幂等赋值，用 Edit 改也等于
     > **整份 JSON 重写**：你读到的那一份会被整体写回，期间别的 hook 写进来的
     > `session_counter` / `pending_maintenance` / drift 历史统统被盖掉。
     > 字段幂等 ≠ 写入方式幂等。
   - 追加 events.jsonl `{"ts":"<now ISO-8601>","type":"maintenance_review_completed"}`
   - 输出下次建议时间

## 约束

- Librarian 整理前必须等用户确认
- 任何写操作前做 snapshot / 备份（复用 librarian 自己的备份机制）
- 若用户随时取消，保留已完成步骤的结果，清理 pending_maintenance 中对应项
- `disable-model-invocation: true`（防止 Claude 自作主张把 dormant 项目"唤醒并整理"）

## 相邻入口：`workframe-maintenance` 批处理（不属本 skill，仅索引）

轻量维护有独立的**非交互批处理入口**：终端运行 `workframe-maintenance`（bin 包装；命令不在 PATH 时用 `python "<插件根>/bin/workframe-maintenance"`，插件根见 `.workframe/state/plugin-root.<门>.txt`，`<门>` 是 `cc` 或 `codex`）→
`claude -p --maintenance` → Setup hook（`scripts/maintenance_workorder.py`）把四路保养事项
（notes 积压 / 待拍板候选计数 / 逾期提案验证 / pending_maintenance open 信号 + doctor 异常项）
聚合成 `.workframe/state/maintenance-workorder.md` 工单，模型照单执行。

与本 skill 的分工：批处理只做 **L1 与记录性操作**（print 模式无交互卡，L2 一律攒到
promotion-candidates.md）；需要逐步确认、处理 L2 候选、或 dormant 唤醒的场景走本 skill。
