# Self-iteration eval · 04 驳回基线：两类接法 × 两个 kind

**类型**：回归（跨 TASK-018 / 021 / 022 / 024 / 038 五批的核心机制）

用户驳回一条维护信号 = 认定此刻之前的都已处理。该 kind 的起点推进到那条
pending 条目的 `closed_at`，**按 `dedup_key` 各管各的**。两类接法：

| 接法 | kind | 机制 |
|---|---|---|
| 证据过滤类 | `close_check_due` | 只计 `last_marked_at` **严格晚于** `closed_at` 的 stale 记录（`event_after()`）|
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

## 断言矩阵

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
| F1 | closed `memory_backlog`@1 天前 + notes 积压 + stale 标记于今天 | `{close_check_due}` —— 关一条不影响另一条 |
| F2 | closed `close_check_due`@1 天前 + stale 标记于 3 天前 + notes 积压 | `{memory_backlog}` |

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
| H10 | closed `close_check_due`@1 天前 + stale 标记于 3 天前 + notes 积压 | `{memory_backlog}` —— 关本条不影响另一条 |
| H11 | closed `memory_backlog`@1 天前 + notes 积压 + stale 标记于今天 | `{close_check_due}` —— 关另一条不影响本条 |

**写坏反证（各只打断一个组成部分；每组先在无 stale 的 fixture 上自检仍静默）**：

| # | 变异 | 翻转 |
|---|---|---|
| W1 | 证据过滤整体失效（过滤条件写死为真） | H5 ∅ → `{close_check_due}` |
| W2 | 退化成 `memory_backlog` 式纯压制期（有基线就整条跳过） | H7 `{close_check_due}` → ∅ |
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

> 下面两条是旧口径（六个 kind）时的实跑；F、H10、H11 改写后的形态由框架的单测在 CI 里真跑，其余组未复跑。

- **2026-08-27**：A-G 29 组全过——TASK-025 落地时按本用例的前置与断言**逐行实跑**
  （不是照抄推理；该次实跑的基准 commit 未记在本文件内）。首跑 1 组 FAIL，查实为
  **fixture 写错**（D2 把驳回日与完成日放在同一天，命中上方坑 1），**被测方语义正确**；
  改正 fixture 后通过——该坑已写进上方前置，复跑时不必再踩一次。
- **2026-08-30**：H 12 组 + 6 组写坏反证（含各自的无注入自检）共 24 次运行全过，I 12 组另跑一轮全过（二检，换角度：往消费端多走一段 + 畸形输入矩阵）——
  TASK-038 落地时在整仓副本沙盒内实跑，子进程 + `CLAUDE_PROJECT_DIR` 指 fixture 项目，
  读数为 `activity-state.json` 中 `status=open` 条目的 `dedup_key` 集合。前后
  `sha256` 与整树 manifest 各对账一次，副本零残留。A-G 29 组本轮未复跑。
