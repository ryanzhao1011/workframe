# J11 librarian 关 memory_backlog 后的抑制闭环

> 补的是一个长期缺口：librarian 第 6 步**每次整理完 notes 都会 `--close-pm` 关掉
> `memory_backlog`**——这个关闭是常规工作流的一部分，不是偶发驳回。TASK-022 之前它
> 关了等于没关（下一轮 Stop 以新编号原样重开），而 J4/J5 都不断言这一段。

- 前置：沙盒 `.workframe/agent-memory/` 下 2 个角色的 `notes.md` 各 100 内容行
  （> `NOTES_BACKLOG_LINES`=80）；`pending_maintenance` 置空；`dormant` / `wake_up_pending`
  均为 false
- 工具：`CLAUDE_PROJECT_DIR=<沙盒> python check-iteration-trigger.py`（挂载点是 **Stop**）
  / `maintenance_workorder.py --close-pm <PM-ID> --reason <text>`
  / `session-start-prep.py`（喂 stdin `{}`，跑 GC）
- 断言：
  1. notes 积压 → `pending_maintenance` 出现 `dedup_key=memory_backlog` 的 open 条目，
     `details` 形如 `notes 待整理：dev(内容 100 行 > 80)；qa(内容 100 行 > 80)（调 librarian skill 处理）`
  2. `--close-pm <id>` 走**代码通道**关闭成功（exit 0，该条目 `status` 转 `closed`）
     ——librarian SKILL 第 6 步明写「不要自己改这个文件」，手改会绕过文件锁 / 原子替换 /
     三方合并，且模型整份重写 JSON 时漏字段不报错、只静默丢历史
  3. 同一次调用**配对写出** `pending_maintenance_dismissed` 事件，含 `pm_id` 与 `reason`
  4. **notes 一行未动，再跑 trigger → 不重开**（这正是 TASK-022 修掉的缺陷；改前版在此重开）
  5. **压制期内即使 notes 又长了也不重开**——纯压制期语义：不问 notes 当前状态。
     这与「notes 变了就重开」的 mtime 方案是**有意的不同选择**（mtime 跨机同步 /
     git checkout 会重置，且 librarian 把条目提升到 MEMORY 时 notes 也会变）
  6. `closed_at` 超过 `PM_CLOSED_RETENTION_DAYS` 后，SessionStart 的 GC **删除该条目**
  7. 载体消失 → 下一轮 trigger **重开**。这不是缺陷：其余四个 kind 的底层条件会随
     产出提案 / 跑迭代 / 事件滑出窗口自然归零，而 **notes 积压只有 librarian 会清**
     ——本 kind 的实际形态是「**每个保留期提醒一次，永远**」，不是「驳回一次就永远安静」
- **可脚本化的边界**：断言 1-7 全部可脚本直调，不需要真实 librarian 会话
  （关闭动作用代码通道模拟）。「librarian 是否**该**关这条」属内容判断，归 J4；
  本用例只验「关了之后信号侧的行为对不对」。
- 实测 2026-08-27：✅ **7/7**——TASK-025 落地时按上述前置与断言**逐条实跑**
  （该次实跑的基准 commit 未记在本文件内）。断言 4 在 TASK-022 的改前版上实测重开 = 缺陷复现。
