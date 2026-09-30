# Self-iteration eval · 04 驳回基线：三类接法 × 六个 kind

**类型**：回归（跨 TASK-018 / 021 / 022 / 024 / 038 五批的核心机制，此前无任何 eval 覆盖）

用户驳回一条维护信号 = 认定此刻之前的证据都已闭环。该 kind 的计分起点推进到那条
pending 条目的 `closed_at`，**按 `dedup_key` 各管各的**。三类接法各覆盖不同 kind：

| 接法 | kind | 机制 |
|---|---|---|
| 证据过滤类 | `problem_threshold` / `activity_threshold` / `close_check_due` | 只计 ts **严格晚于** `closed_at` 的证据——前两者取 events.jsonl 的事件 ts，后者取每条 stale 记录的 `last_marked_at`（判定式共用 `event_after()`）|
| 日期基线类 | `cadence_timeout` / `completed_delta` | 推进起算日期，**只前移不后退** |
| 纯压制期类 | `memory_backlog` | 扫的是 notes 当前状态、无可比时间点 → 存在未被 GC 的 closed 条目就**整条跳过** |

> **「扫当前状态」不等于「只能做压制期」**：`memory_backlog` 与 `close_check_due` 扫的
> 都是一份当前清单，分野在**清单里有没有逐条的时间戳**。notes 只有内容行数，stale
> 每条带 `last_marked_at`——所以后者能落进证据过滤类，驳回只静音当下这批、新标记照报。
> 判一个新 kind 归哪一类，先问这一句，别按「扫的是清单还是事件流」分。

## 公共前置

- 直调 `CLAUDE_PROJECT_DIR=<沙盒> python check-iteration-trigger.py`（挂载点是 **Stop**）
- 读数：`activity-state.json` 的 `pending_maintenance` 中 `status=open` 条目的
  **`dedup_key` 集合**——必须比集合，只看「有没有条目」会被 fixture 预置的 closed 条目干扰
- `dormant` / `wake_up_pending` 均为 false；每组用独立沙盒目录

> **构造 fixture 的三个坑**（首跑各踩过一次）：
> 1. **`completed_delta` 的起算点是「驳回日 **+1**」**——把驳回日与任务完成日放在同一天，
>    则「驳回当天完成的任务不计入」（这是设计内的 fail-safe 少报），会看到静默而误以为
>    抑制坏了。要观测「驳回后新完成」必须让完成日**严格晚于**驳回日。
> 2. **`completed_delta` 是兜底信号**（`if not signals` 才评估）——测它时必须让其余五个
>    kind 都不命中，否则它根本不被评估。
> 3. **空 fixture 的默认态是 cadence 命中**：`proposals/{applied,rejected}` 两目录皆空时
>    `dsli is None`（从未自迭代），cadence 立即响。所以隔离测其他 kind 时必须先写一条
>    当天的 `applied/PROP-*.yaml`（含 `applied_at: <今天>`）让 cadence 闭嘴，否则每组的
>    期望集里都得挂一个 `cadence_timeout`，噪声掩盖真正要看的那一位。

## 断言矩阵（A-G 29 组 + H 12 组 + I 12 组 = 53 组）

### A. 事件流类 · problem_threshold

| # | fixture | 期望 |
|---|---|---|
| A1 | 2 条 3 天前的 problem 事件（共 5.0）+ closed@1 天前 | ∅ —— 驳回前的旧事件不再计分 |
| A2 | 同 A1 但无 closed 条目 | `{problem_threshold}` —— 证明 A1 的静默来自基线而非事件不够 |
| A3 | closed@1 天前 + 驳回后新增 5.0 | `{problem_threshold}` —— 新证据够阈值仍重开 |
| A4 | closed@1 天前 + 驳回后只新增一条 `user_correction`(3.0) | ∅ —— **判别性**：写成「抑制信号」式实现（关了就永久不报）会在此翻红 |

### B. 事件流类 · activity_threshold

| # | fixture | 期望 |
|---|---|---|
| B1 | 4 session × 20 条 3 天前的 `skill_used`（12.0）+ closed@1 天前 | ∅ |
| B2 | 同 B1 但无 closed 条目 | `{activity_threshold}` |

### C. 日期基线类 · cadence_timeout

| # | fixture | 期望 |
|---|---|---|
| C1 | 上次迭代 30 天前 + closed@6 天前 | ∅ —— 静默期内 |
| C2 | 同上但 closed@7 天前 | `{cadence_timeout}` —— 恰好一个提醒周期 |
| C3 | closed@7 天前，但**上次迭代在 2 天前**（驳回后真产出过提案）+ 另有 problem 开锁外闸 | 只有 `{problem_threshold}` —— 起算点取「上次自迭代」与「上次驳回」里**更晚**的那个 |
| C4 | **`proposals/{applied,rejected}` 两目录皆空**（从未自迭代）+ closed@2 天前 + problem 事件 | `{problem_threshold}` 且 **exit 0、stderr 无 Traceback** |

> C4 断言的是「**其他 kind 的信号照常落盘**」而非「cadence 不响」。只断言后者测不出
> 真正危险的失败形态：判定跑在 `update_activity` 的锁内回调里，一旦抛异常，
> `atomic_write` 不执行 → **本轮全部信号一起不落盘**，而进程仍 exit 0、只留一行 stderr warn。
> 该状态正是**每个新装机项目的开局**（scaffold 建的 proposals 两目录只有 `.gitkeep`）。

### D. 日期基线类 · completed_delta

（前置：让其余五 kind 都不命中——`cadence_timeout` 也置一条 closed@2 天前）

| # | fixture | 期望 |
|---|---|---|
| D1 | closed@2 天前 + 10 个任务完成于 5 天前（**驳回之前**） | ∅ —— 驳回前的账不再计入 |
| D2 | closed@2 天前 + 10 个任务完成于**今天** | `{completed_delta}` |
| D3 | 同 D2 但只完成 9 个（< `COMPLETED_DELTA_FALLBACK`=10） | ∅ —— **判别性** |

### E. 纯压制期类 · memory_backlog

| # | fixture | 期望 |
|---|---|---|
| E1 | notes 100 内容行 + closed@1 天前 | ∅ —— 有未被 GC 的 closed 条目就整条跳过，**不问 notes 状态** |
| E2 | 同 E1 但无 closed 条目 | `{memory_backlog}` |

> 压制期**不设独立常量**：载体就是那条 closed 条目，GC 删掉它压制即失效，故压制期恒
> 等于 `PM_CLOSED_RETENTION_DAYS`。到期后的重开路径由 memory-pipeline J11 覆盖。

### F. 跨 kind 不串扰

| # | fixture | 期望 |
|---|---|---|
| F1 | closed `memory_backlog`@1 天前 + cadence 超期 + problem 够分 | `{cadence_timeout, problem_threshold}` —— 关一条不影响其余 |
| F2 | closed `cadence_timeout`@1 天前 + notes 积压 | `{memory_backlog}` |

### G. fail-safe 边界（坏基线一律**退回不抑制**，方向是多报不是静音）

`closed_at` 取 **null / 非法串 / 数字 / 列表 / 空串 / bool / dict / 未来时刻** 八形态，
外加**缺 `dedup_key`**、**条目非 dict 混排（不含任何合法 closed 条目）**、**多条 closed 取最新**、**dormant 全链静默**
——共 12 组，一律 exit 0，前十组均照常产出信号（不因坏数据获得抑制力）。

> 「未来时刻的 `closed_at`」是本机制**唯一的 fail-dangerous 方向**（会让所有事件永远落在
> 基线之前 = 该信号被永久抑制），故就地丢弃该条、其余合法条目仍可提供基线。

### H. 证据过滤类 · close_check_due（TASK-038 新增）

证据源 = `.workframe/state/stale-modules.yaml`，逐条的时间戳 = `last_marked_at`。

| # | fixture | 期望 |
|---|---|---|
| H1 | 无 `stale-modules.yaml` | ∅ |
| H2 | 文件在但 `submodules:` 下无条目 | ∅ —— **判别性**：文件存在 ≠ 有积压 |
| H3 | 1 条 stale，标记于今天 | `{close_check_due}` |
| H4 | 3 条 stale，均标记于今天 | `{close_check_due}` —— 多条合并为一条信号，不逐模块堆条目 |
| H5 | stale 标记于 3 天前 + closed@1 天前 | ∅ —— 驳回前的旧标记不再计入 |
| H6 | 同 H5 但无 closed 条目 | `{close_check_due}` —— 证明 H5 的静默来自基线而非清单读不到 |
| H7 | closed@1 天前 + stale 标记于**今天** | `{close_check_due}` —— **判别性**：写成压制期式实现（有基线就整条跳过）会在此翻绿；这正是本 kind 与 `memory_backlog` 的分界 |
| H8 | closed@1 天前 + stale 条目**缺 `last_marked_at`** | ∅ —— 时间戳解析不了即判「不晚于基线」（`event_after` 的既有口径，保守静音）|
| H9 | 同 H8 但无 closed 条目 | `{close_check_due}` —— 无基线时不过滤，缺时间戳不影响报出 |
| H10 | closed `close_check_due`@1 天前 + cadence 超期 + problem 够分 | `{cadence_timeout, problem_threshold}` —— 关本条不影响其余 |
| H11 | closed `cadence_timeout`@1 天前 + stale 标记于今天 | `{close_check_due}` —— 关其余不影响本条 |
| H12 | stale 标记于今天 + 10 个任务完成于今天 | `{completed_delta, close_check_due}` —— **判别性**：本 kind 的信号追加在 `completed_delta` 的 `if not signals` 判定**之后**，不吞掉兜底信号 |

**写坏反证（6 组，各只打断一个组成部分；每组先在无 stale 的 fixture 上自检仍静默）**：

| # | 变异 | 翻转 |
|---|---|---|
| W1 | 证据过滤整体失效（过滤条件写死为真） | H5 ∅ → `{close_check_due}` |
| W2 | 退化成 `memory_backlog` 式纯压制期（有基线就整条跳过） | H7 `{close_check_due}` → ∅ |
| W3 | 把本 kind 的信号块移到 `completed_delta` 之前 | H12 `{completed_delta, close_check_due}` → `{close_check_due}` |
| W4 | stale 顶层键正则缩进收窄一格（扫不到任何条目） | H3 `{close_check_due}` → ∅ |
| W5 | `last_marked_at` 提取正则失效 | H7 `{close_check_due}` → ∅ |
| W6 | 锁外短路条件里去掉 `or stale_marks` | H3 `{close_check_due}` → ∅ |

> W4 与 W6 都让 H3 翻转，但打的不是同一个零件：W4 毁解析、W6 毁锁外准入。两条都要，
> 否则「H3 绿」只证明这条链路整体通了，证不出其中**每一段**各自是活的。

### I. 端到端到消费端 + stale 文件的边界形态（TASK-038 新增）

A-H 都止步于「trigger 写没写进 `activity-state.json`」。**写进状态文件而渲染不出来等于没做**
——注入才是这条管道的交付面，故本组往下再走一段；`scan_stale_modules()` 是一份手写解析，
它吃到畸形输入时的方向也必须钉住。

| # | fixture | 期望 |
|---|---|---|
| I1 | stale 非空 → 跑 trigger → 跑 `user-prompt-inject.py` | 注入文本含 kind 名与 `module-close-check` 命令，exit 0 |
| I2 | 同 session_counter 再跑一次注入 | stdout 空（防抖生效，不重复刷屏）|
| I3 | 同 fixture 跑 `maintenance_workorder.py` | 工单文件（**不是 stdout**）的「pending_maintenance open 信号」段列出该条，且 close_pm 判据段写明本 kind 批处理不关 |
| I4 | `stale-modules.yaml` 为**空文件** | ∅、exit 0 |
| I5 | 只有注释行 | ∅、exit 0 |
| I6 | BOM 开头 + 正常内容 | 照常报出 |
| I7 | 全文 CRLF | 照常报出 |
| I8 | 含**非法 UTF-8 字节** | ∅、exit 0 —— 读取异常吞掉 = 少报（fail-safe），不炸 hook |
| I9 | 文件在时间戳行之后被**截断** | 照常报出 —— 顶层键行完整即确有一条 stale 记录 |
| I10 | 没有 `submodules:` 键（换成别的顶层结构）| ∅、exit 0 |
| I11 | 顶层键缩进成 3 格（偏离 `save_stale()` 的形态）| ∅、exit 0 —— **与 `load_stale()` 同口径**（它也只认 2 格）；`save_stale()` 一旦改缩进或字段名，这份手写解析会**静默扫 0**——不报错、不翻红，功能死了没人知道，改产出方时务必连它一起改 |
| I12 | 关本 kind@1 天前 + stale 标记于 3 天前 + notes 积压 120 行 | `{memory_backlog}` —— 反向补测：关本条不妨碍**其余 kind 各自**照报 |

> **首跑 4 组 FAIL、查实全是探针自己写错**（被测方语义正确），三种错法各记一笔：
> ①I3 取错输出通道（工单写文件、探针读 stdout）②I9 期望写反（截断后顶层键仍在 =
> 确有记录）③I12 的 fixture 用了「今天」的 stale 标记，那本就该报。**首跑 MISMATCH 过半时
> 先怀疑探针**，这一轮又验了一次。

## 实跑记录

- **2026-08-27**：A-G 29 组全过——TASK-025 落地时按本用例的前置与断言**逐行实跑**
  （不是照抄推理；该次实跑的基准 commit 未记在本文件内）。首跑 1 组 FAIL，查实为
  **fixture 写错**（D2 把驳回日与完成日放在同一天，命中上方坑 1），**被测方语义正确**；
  改正 fixture 后通过——该坑已写进上方前置，复跑时不必再踩一次。
- **2026-08-30**：H 12 组 + 6 组写坏反证（含各自的无注入自检）共 24 次运行全过，I 12 组另跑一轮全过（二检，换角度：往消费端多走一段 + 畸形输入矩阵）——
  TASK-038 落地时在整仓副本沙盒内实跑，子进程 + `CLAUDE_PROJECT_DIR` 指 fixture 项目，
  读数为 `activity-state.json` 中 `status=open` 条目的 `dedup_key` 集合。前后
  `sha256` 与整树 manifest 各对账一次，副本零残留。A-G 29 组本轮未复跑。
