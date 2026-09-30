#!/usr/bin/env python3
"""
Stop Hook — 自迭代触发检测（v0.2.1）

触发逻辑 = Cadence ∨ WeightedEvents：
  - Cadence：距上次自迭代 ≥ CADENCE_DAYS 天（基线从 proposals/ 派生，见
    derive_last_iteration_date）
  - WeightedEvents：近 EVENTS_WINDOW_DAYS 天 events.jsonl 加权积分超阈值
      - problem（user_correction=3.0, task_blocked=2.0）≥ 5
      - activity（skill_used=0.2, per-session cap=3）≥ 10
      - 注：proposal_failed 在 v0.2.2-fixup-2 移除（model_mediated 不能驱动核心触发）

**驳回基线（各 kind 共用一套语义，按 dedup_key 各管各的）**：用户驳回一条信号 =
认定此刻之前的证据都已闭环，该 kind 的计分起点推进到那条 pending 条目的 `closed_at`，
此后只算更晚的证据。关一条不影响其余 kind（取数与边界见 dismissal_baseline()）。
三类接法：证据过滤类（problem / activity / close_check_due）过滤证据 ts，日期基线类
（cadence / completed_delta）推进起算日期，**纯压制期类（memory_backlog）**在压制期内
整条跳过——它扫的是 notes 的**当前状态**，没有可与驳回时刻相比的证据时间点，只能不问
状态地静默一段；压制期不设独立常量，等于那条 closed 条目的 GC 保留期（见
dismissal_baseline()）。close_check_due 扫的同样是「当前清单」，但清单里每条自带
`last_marked_at`，故归证据过滤类而非压制期类。
  - 注：SkillLowSuccess 在 v0.4 M6 移除——见下方"关于 success 字段"

v0.2.1 变更（相对 v0.2.0）：
  - 彻底移除 rule_triggered / idle_rules：CC 没有 rule 触发回调，
    rule_triggered 不可 deterministic 捕获（见 event-schema.json reliability=removed_v0_2_1）
  - 触发时 append pending_maintenance 对象（按 architecture-overview §17.8 schema + dedup_key 去重），
    供 UserPromptSubmit / /core:audit 消费；不再仅 print 到 stdout

v0.4 G1#2 变更（derive, don't store）：
  - Cadence 与 CompletedDelta 不再读 iteration-baseline.json（该文件退役，模型手工
    记账义务同步从 self-iteration / maintenance-review SKILL 删除——实测 3 个月零记账，
    stale 基线产生假 cadence 警报）
  - 迭代日期改为代码派生：projects/proposals/applied/*.yaml 的 applied_at 与
    rejected/*.yaml 的 rejected_at 取最大值（拒绝也算一次迭代审阅活动，2026-08-05
    用户拍板），字段缺失回退文件名 PROP-YYYYMMDD
  - completed 增量改为 board.yaml 现算：status=completed 且 updated_at ≥ 派生日期

⚠️ 关于事件可靠性（v0.2.2 起）：
本脚本依赖的 weighted events（user_correction / task_blocked / skill_used）
全部来自 `protocol_expected` 可靠性层——agent 收尾协议写入，model 遵循
（分层定义见 `.workframe-meta/event-schema.json`）。这意味着：
  - 计数本身是 best-effort，可能因模型 skip / duplicate / misattribute 失真
  - 阈值是工程经验值，不是 deterministic 严格判定
  - 触发的 pending_maintenance 仅是"建议触发自迭代"信号，最终是否真跑 self-iteration
    skill 由用户在 UserPromptSubmit 注入后的对话轮决定
真正 hook_deterministic 的事件目前只有 session_ended 和 summary_recomputed，它们不参与
weighted events 计算。

⚠️ 关于 success 字段与 SkillLowSuccess 的移除（v0.4 M6）：
`skill_used.success` 要求 agent 对自己刚做完的工作做成败自评，实测**从未产出过 false**
（消费项目 2.5 个月 77 条 skill_used 全部 success=true）——这不意味着零失败，而是自评
在收尾阶段天然偏向正面。据此 SkillLowSuccess 触发条件被移除：维持一个永不满足的触发
条件比没有更糟（它会让人误以为质量退化能被自动发现）。

替代信号：`user_correction`（problem 权重 3.0）。它由 correction-detection 流程按明确
信号词触发，判据不依赖自我评价，是当前架构下最可靠的"哪里做得不好"信号。

`success` 字段本身保留：recompute_skill_metrics.py 继续统计、`/core:audit` 继续展示，
作为**人工观察**数据有价值，只是不再驱动自动触发。

dormant=true 或 wake_up_pending=true 时直接退出，不检测也不写 pending_maintenance。
"""

import io
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path


try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


# 同目录公共模块：状态文件的锁 / 原子写 / 损坏隔离只有一份实现（见 _state_io.py 抬头）
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402
from _harness import project_dir as _project_dir  # noqa: E402
from _state_io import (  # noqa: E402
    load_activity as _load_activity,
    memory_dir_of,
    save_activity as _save_activity,
    state_dir_of,
    update_activity,
)


PROJECT_DIR = _project_dir()
BOARD_FILE = PROJECT_DIR / "projects" / "board.yaml"
STATE_DIR = state_dir_of(PROJECT_DIR)
PROPOSALS_DIR = PROJECT_DIR / "projects" / "proposals"
EVENTS_FILE = STATE_DIR / "events.jsonl"
METRICS_FILE = STATE_DIR / "skill-metrics.yaml"
ACTIVITY_FILE = STATE_DIR / "activity-state.json"
MEMORY_DIR = memory_dir_of(PROJECT_DIR)
STALE_FILE = STATE_DIR / "stale-modules.yaml"

# 记忆积压阈值（v0.4 M7）
# 只做**确定性统计**（行数 / 距上次提升天数），不做语义聚类——
# "哪些条目是同一主题、够不够提升"需要判断力，交给 librarian skill 在被调起后做。
# 脚本硬做语义会产出不准的信号，重蹈 skill_low_success 覆辙。
NOTES_BACKLOG_LINES = 80      # 单个 notes.md 内容行（见 _count_content_lines）超过此值视为积压
NOTES_STALE_DAYS = 30         # 距上次 memory_promoted 超过此天数且 notes 内容行 > 5


# 权重表（v0.2.2-fixup-2 起）
# 所有计入触发判定的 event 类型 reliability ≥ protocol_expected。
# model_mediated 事件（如 proposal_failed）不能作为 core triggering logic
# （详见 event-schema.json reliability_tiers 定义 + Codex 三轮 review P1）
#
# **跨门口径（event-schema v3 起）**：本表按事件类型加权，不看事件来自哪扇门。所以两门
# 混流的 events.jsonl 上，积分本身仍然可加——但**问题信号在 Codex 侧被系统性低估**：
# `turn_failed` 在那边没有 producer（无 `StopFailure` 对等 hook），`PostToolUse` 又对失败
# 的工具调用不触发，两者同向叠加。这是已知的产品决策，**本脚本只按已有权重算，不做补偿**
# ——补偿等于凭猜给一扇门加分。要看缺口清单去 `event-schema.json` 的 `availability`，
# 或读 `skill-metrics.yaml` 的 `harness_breakdown`（现算，此处不复制一份会漂的副本）。
PROBLEM_WEIGHTS = {
    "user_correction": 3.0,    # protocol_expected: correction-detection 流程 + agent wrap-up 写入
    "task_blocked": 2.0,       # protocol_expected: 主 producer = test-case-design skill；fallback producer
                                # = 角色 wrap-up（仅限非 QA / 手动 blocked 转换，且必须做 dedup 检查）
                                # （详见必载片 §Step 1 — 事件流 的 task_blocked producer 策略）
    # proposal_failed: 已从 PROBLEM_WEIGHTS 移除（v0.2.2-fixup-2）。
    # 它是 self-iteration stage 1b 的 model_mediated 产物，不能基于它做 core 触发；
    # self-iteration 失败反馈循环留给第二步 skill gap 分析时重新设计（如让 stage 1b 写
    # protocol_expected 级别的"verify failed"事件，或专门 hook 捕获）
}
ACTIVITY_WEIGHTS = {
    "skill_used": 0.2,         # protocol_expected: agent wrap-up Step 1 写入
}
ACTIVITY_PER_SESSION_CAP = {
    "skill_used": 3.0,
}

PROBLEM_THRESHOLD = 5.0
ACTIVITY_THRESHOLD = 10.0
CADENCE_DAYS = 7
COMPLETED_DELTA_FALLBACK = 10

# 加权事件的滚动窗口（天）。**必须与 session-start-prep.py 的 PM_CLOSED_RETENTION_DAYS
# 相等**，由 validate 的 `pm_retention_matches_events_window` 对账——两者相等不是巧合，
# 是 problem_threshold 驳回抑制能够自洽收场的前提：
#   「用户驳回过」这件事的唯一载体是那条 status=closed 的 pending 条目，而它在
#   closed_at + PM_CLOSED_RETENTION_DAYS 天后被 SessionStart GC 删除。两个常量相等时，
#   条目被删的同一时刻，closed_at 之前的事件也恰好全部滑出本窗口——抑制记忆与它要
#   抑制的对象同时消失，交接无缝。
# 任一常量单独改动都会撕开一道缝：
#   窗口 > 保留期 → 基线先没、旧事件还在窗口里 → 已驳回的账重新计分（本次修的就是这个形态）
#   窗口 < 保留期 → 基线多留几天，但那时窗口内已无旧事件，只是冗余，无害
EVENTS_WINDOW_DAYS = 7


def load_activity():
    """字段全集与损坏处理见 `_state_io`（四个 hook 共用一份实现）。"""
    return _load_activity(STATE_DIR)


def save_activity(state):
    _save_activity(STATE_DIR, state)


def derive_last_iteration_date():
    """从 proposals/{applied,rejected}/ 派生最近一次自迭代活动日期（v0.4 G1#2）。

    替代 iteration-baseline.json 手工记账：档案文件是干活时自然留下的，
    不依赖模型收尾自觉。applied 取 applied_at，rejected 取 rejected_at
    （拒绝 = 用户完成了一次迭代审阅，同样重置 cadence）；字段缺失/解析失败
    回退文件名 PROP-YYYYMMDD 的日期段；两目录均无文件 → None（从未迭代）。
    """
    latest = None
    for sub, field in (("applied", "applied_at"), ("rejected", "rejected_at")):
        d = PROPOSALS_DIR / sub
        if not d.is_dir():
            continue
        for f in d.glob("*.yaml"):
            date = None
            try:
                m = re.search(
                    r"^\s*" + field + r"""\s*:\s*["']?(\d{4}-\d{2}-\d{2})""",
                    f.read_text(encoding="utf-8"), re.M)
                if m:
                    date = m.group(1)
            except Exception:
                pass
            if date is None:
                m = re.match(r"PROP-(\d{4})(\d{2})(\d{2})", f.stem)
                if m:
                    date = "-".join(m.groups())
            if date and (latest is None or date > latest):
                latest = date
    return latest


def completed_task_dates():
    """扫 board.yaml，返回全部 status=completed 任务的**完成日期**（IO，锁外调）。

    取 `completed_at`，缺该字段才回退 `updated_at`。**`updated_at` 不作首选**：它在
    任务完成之后仍会被 bump——收口后往 completed 条目里追加实测记录、补 QA 签发段
    都会动它，只要一次追加跨了天，那条老任务就重新落进增量窗口，制造一个没有任何
    新工作对应的假 delta（方向 fail-dangerous，而本脚本处处防的正是假信号）。此前
    的实现只堵了「缺字段」那个口，没堵「字段被 bump」这个口。

    两个字段都缺的 completed 任务以 None 入列，由 count_since() 按基线有无决定计不计。
    轻量逐行解析（任务边界 = "- id:" 行），不引第三方 yaml 依赖；summary 段没有
    "- id:" 行，天然不被计入。
    """
    if not BOARD_FILE.exists():
        return []
    tasks = []
    cur = None
    try:
        for line in BOARD_FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("- id:"):
                cur = {"status": None, "completed": None, "updated": None}
                tasks.append(cur)
            elif cur is None:
                continue
            elif stripped.startswith("status:"):
                # 剥离行内注释（status: completed  # note）再取值
                cur["status"] = stripped.split(":", 1)[1].split("#", 1)[0].strip().strip("\"'")
            elif stripped.startswith("completed_at:"):
                m = re.search(r"(\d{4}-\d{2}-\d{2})", stripped)
                cur["completed"] = m.group(1) if m else None
            elif stripped.startswith("updated_at:"):
                m = re.search(r"(\d{4}-\d{2}-\d{2})", stripped)
                cur["updated"] = m.group(1) if m else None
    except Exception:
        return []
    return [t["completed"] or t["updated"] for t in tasks if t["status"] == "completed"]


def count_since(dates, date_str):
    """数 completed_task_dates() 结果里 ≥ date_str 的条数（纯计算，锁内可反复调）。

    拆成纯函数是为了让锁内能按**各 kind 自己的驳回基线**重复计数而不重复读盘——
    board.yaml 的解析留在锁外（IO 重），锁内只做比较。

    date_str 为 None（从未迭代）时统计全部。无日期的条目在 date_str 非 None 时
    保守不计入——老任务反复计入会制造假增量信号。
    """
    if date_str is None:
        return len(dates)
    return sum(1 for d in dates if d is not None and d >= date_str)


def count_completed_since(date_str):
    """现算 board.yaml 中 status=completed 且完成日期 ≥ date_str 的任务数（v0.4 G1#2）。

    保留为 completed_task_dates() + count_since() 的单次入口（读一次数一次）；
    需要对同一份数据按多个基线反复计数的调用点走那两个函数。
    """
    return count_since(completed_task_dates(), date_str)


def days_since(date_str):
    if not date_str:
        return None
    try:
        then = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now().date()
        return (today - then).days
    except Exception:
        return None


def parse_iso_ts(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def read_recent_events(days=EVENTS_WINDOW_DAYS):
    if not EVENTS_FILE.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    try:
        for line in EVENTS_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if not isinstance(ev, dict):
                # 合法 JSON 但不是对象（`[]` / `42`）：跳过这行继续读。此前 ev.get() 会抛
                # AttributeError 并被**外层** except 吞掉，函数当场返回已读的那部分——
                # 一行坏数据就让它之后的所有事件从自迭代计数里凭空消失
                continue
            if ev.get("__schema__"):
                continue
            ts = parse_iso_ts(ev.get("ts"))
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                continue
            out.append(ev)
    except Exception:
        pass
    return out


def score_weighted_events(events):
    problem = 0.0
    session_activity = defaultdict(lambda: defaultdict(float))
    for ev in events:
        etype = ev.get("type")
        if etype in PROBLEM_WEIGHTS:
            problem += PROBLEM_WEIGHTS[etype]
        elif etype in ACTIVITY_WEIGHTS:
            sid = ev.get("session_id") or ev.get("ts") or "no-session"
            session_activity[sid][etype] += ACTIVITY_WEIGHTS[etype]
    activity = 0.0
    for sid, bucket in session_activity.items():
        for etype, val in bucket.items():
            activity += min(val, ACTIVITY_PER_SESSION_CAP.get(etype, val))
    return problem, activity


def dismissal_baseline(state, dedup_key, now=None):
    """某个 kind 的计分起点 = 同 dedup_key 已 closed 条目里**最新**的 closed_at。

    语义：用户驳回一次该信号，等于认定此刻之前的证据都已闭环，计分从驳回时刻重新
    起算（baseline reset）。**按 dedup_key 各管各的**，关一条不影响其余 kind。

    没有它时的形态：驳回只是把条目标成 closed，而 closed 条目不参与 upsert 的 dedup，
    于是下一次 Stop 拿同一批已闭环的证据重新算出同一个结论、以新编号原样重开——
    关闭对该 kind 实质无效。

    **为什么不把驳回记录并进 `derive_last_iteration_date()`**（同一个 `main()` 里、
    隔 20 行，做的正是同一件事「上次处理过的时点之后才算」，且它的 docstring 已经
    认可「拒绝提案也算一次迭代审阅」——看起来「直接驳回」顺理成章也该算）。四条理由
    不并：
      1. 它是 cadence 与 completed_delta 的**共用**取数点，动它天然跨 kind——驳回一条
         cadence 提醒会顺手把 completed_delta 的计数清零。跨 kind 串扰正是下面那段
         「必须按 dedup_key 筛」防的东西，不该换个位置重新引入。
      2. 类型对不上：它返回 `YYYY-MM-DD`，而 `closed_at` 是秒级 ISO。截断到日期后，
         精度损失落在 `count_since` 的 `>=` 上——驳回当天完成的任务全部仍计入，把
         「撞秒」放大成「撞天」。
      3. 它的立论是「档案文件是干活时自然留下的，不依赖模型自觉」（derive, don't
         store）。proposals/ 是永久档案；驳回记录是 PM_CLOSED_RETENTION_DAYS 天后
         就被 GC 删掉的运行态，把它提升为「迭代日期」的来源会让基线在 GC 后**回弹**。
      4. `iteration_baseline_code_derived` 闸只断言这两个函数名存在，改它们的函数体
         不翻红——正因为不翻红，改坏了也没人告诉你。
    故两者并存：`derive_last_iteration_date()` 回答「上次真的做过迭代是什么时候」，
    本函数回答「这条提醒上次被驳回是什么时候」，cadence 两者都问。

    **与 `weighted_events_since_last_iteration` 退役（derive, don't store）不冲突**：
    本函数不新存基线，读的是 pending 生命周期本来就有的 `closed_at`（由
    `maintenance_workorder.py` 经 `update_activity` 的锁写入）。退役那个字段的失败
    方向是「基线陈旧 → 假警报」（fail-dangerous）；本基线陈旧的方向恰好相反——抑制
    失效、退回未改前的行为（fail-safe）。

    **抑制是有期限的**：载体就是那条 closed 条目，它在 closed_at +
    PM_CLOSED_RETENTION_DAYS 天后被 SessionStart GC 删除，基线随之消失。对
    problem / activity 而言那一刻旧事件也恰好滑出 EVENTS_WINDOW_DAYS 窗口（两常量
    由 validate 对账），交接无缝；对 cadence 而言等价于「提醒周期重新起算」（保留期
    ≥ CADENCE_DAYS 由同一道闸对账）；对 completed_delta 与 close_check_due 而言
    **GC 后必然重开**——前者的分子是累计值、后者的 stale 清单只有 code-to-doc 会清，
    两边都没有滚动窗口把旧证据推出视野。那不是缺陷：要让它们的抑制活过 GC 只能新存
    一个不受 GC 管的状态，即上面第 3 条判死的那条路。

    **memory_backlog 只用本函数的「有没有基线」，不用基线的值**（纯压制期，见
    `_apply_signals()` 第 3 段）：它没有可与 closed_at 相比的证据时间点，所以压制期
    只能由载体自己的寿命定义——有未被 GC 的 closed 条目就静默，条目一没就重开。
    这样压制期恒等于 PM_CLOSED_RETENTION_DAYS，**不需要也不应该另设常量**：另设的话
    它的有效值恒为 `min(常量, 保留期)`（实测设 14 天时第 8 天条目被 GC、提醒照常重开，
    多出的 7 天完全无效），而没有任何闸会说破这条脱钩。它同样是 GC 后必然重开的那一族
    ——且它的底层条件**不会自己消解**（cadence / problem / activity / completed_delta 会随
    产出提案 / 跑迭代 / 事件出窗自然归零，notes 积压只有 librarian 会清），故实际形态是
    「每个保留期提醒一次」。**close_check_due 不落进这一族**：它扫的虽也是「当前清单」，
    但清单里每条自带 `last_marked_at`，能与 closed_at 相比，故走证据过滤而非压制期
    ——保留期内新标记照样报得出来，只有旧标记被静音。

    返回 aware datetime；无可用基线返回 None（= 不抑制，等同改动前行为）。
    """
    items = state.get("pending_maintenance") or []
    if not isinstance(items, list):
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    latest = None
    for item in items:
        if not isinstance(item, dict):
            continue
        # **必须按 dedup_key 筛**：不筛的话关掉 cadence_timeout 会顺带抑制
        # problem_threshold（跨 kind 串扰，实测能让 problem 静默失明）。
        # 缺该字段视为不匹配——手改过或旧版留下的条目不该获得抑制力。
        if item.get("dedup_key") != dedup_key:
            continue
        if item.get("status") != "closed":
            continue
        ts = parse_iso_ts(item.get("closed_at"))
        if ts is None:
            continue                      # closed_at 为 null / 格式非法 → 该条不提供基线
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts > now:
            # 未来时刻的 closed_at（系统时钟回拨 / 状态文件被手改）会让所有事件永远
            # 落在基线之前 = 该信号被永久抑制。这是本机制唯一的 fail-dangerous 方向，
            # 就地丢弃这一条（其余合法条目仍可提供基线）。
            continue
        if latest is None or ts > latest:
            # 多条 closed 取**最新**：GC 前同一 kind 可以被关过不止一次，取较早的
            # 那条等于让抑制从一个已经作废的时点起算，中间那段旧账会重新计分。
            latest = ts
    return latest


def event_after(ev, baseline):
    """带 `ts` 字段的一条证据是否发生在基线**之后**。

    两类调用方共用这一个判定式，不各写一份：`score_weighted_events` 侧的 events.jsonl
    事件，与 `scan_stale_modules()` 侧的 stale 标记（`last_marked_at` 同为 UTC 秒级 ISO，
    由 check-stale-modules 的 `now_iso()` 写）。取的都是 `ev["ts"]`，`>` 语义、tz 兜底、
    解析不了即判 False（保守静音）三条一并复用。

    用 `>` 而非 `>=`：驳回动作发生在被它判定为「已闭环」的那批事件之后，与 closed_at
    同秒的事件属于被驳回的那一批。两侧都是秒级精度（`closed_at` 由
    `maintenance_workorder.py` 以 `isoformat(timespec="seconds")` 写，事件 ts 按
    agent-protocols 也是 UTC 秒级），撞秒是现实可能而非理论边界。
    """
    ts = parse_iso_ts(ev.get("ts"))
    if ts is None:
        return False
    # tz 兜底不能省：`read_recent_events` 内部虽然补过 tzinfo，但那只用于窗口过滤，
    # **没有写回 ev**——这里重新解析拿到的仍是 naive。漏掉这一步会在遇到 naive ts
    # （模型手写事件时漏时区，protocol_expected 层的现实风险）时抛 TypeError。
    # 实测后果不是崩溃而是更隐蔽的静默失效：本函数跑在 `update_activity` 的锁内回调里，
    # 异常冒泡到 main 的 `except Exception` 被兜住、进程照常 exit 0，但 mutator 抛出时
    # `atomic_write` 还没执行——**本轮全部信号一起没落盘**（cadence / activity /
    # backlog 陪葬），stderr 只留一行 `[warn] failed to save activity-state`。
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts > baseline


def _count_content_lines(text):
    """数 notes.md 的**内容行**：排除空行 / 标题 / 引用说明 / 分隔线 / 注释 / 占位符。

    只数内容行是为了避免"文件头 + 归档说明占了 20 行但实质为空"的空壳误报；
    判据仍是纯字符串前缀匹配，无语义判断。
    """
    n = 0
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith(("#", ">", "---", "<!--", "_（", "_(")):
            continue
        n += 1
    return n


def scan_memory_backlog():
    """扫描角色记忆目录下 `*/notes.md` 的积压情况（纯确定性统计）。

    返回 [(scope, content_lines, reason), ...]；无积压返回 []。
    判据二选一：
      - notes.md 内容行 > NOTES_BACKLOG_LINES
      - 距最近一次 memory_promoted 事件 > NOTES_STALE_DAYS 且内容行 > 5
    已处理条目由 librarian 移入 notes-archive.md（归档制，见 librarian SKILL 第 2.5 步），
    archive 文件不匹配本 glob，天然不计入积压。
    """
    if not MEMORY_DIR.exists():
        return []

    # 最近一次提升时间（全局，不分角色——按角色分会因事件稀疏而失真）
    last_promoted = None
    if EVENTS_FILE.exists():
        try:
            for line in EVENTS_FILE.read_text(encoding="utf-8").splitlines():
                if '"memory_promoted"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                # 先解析成 aware datetime 再比：`ev.get("ts","")[:10]` 对
                # `ts: null` 会抛 TypeError（None 不可切片），跨时区的日期前缀
                # 在当日边界也会比错。取 UTC 日期作为「最近提升日」。
                dt = parse_iso_ts(ev.get("ts"))
                if dt is None:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = dt.astimezone(timezone.utc).date().isoformat()
                if last_promoted is None or ts > last_promoted:
                    last_promoted = ts
        except Exception:
            pass
    days_stale = days_since(last_promoted)

    backlog = []
    for notes in sorted(MEMORY_DIR.glob("*/notes.md")):
        scope = notes.parent.name
        try:
            content = _count_content_lines(notes.read_text(encoding="utf-8"))
        except Exception:
            continue
        if content > NOTES_BACKLOG_LINES:
            backlog.append((scope, content, f"内容 {content} 行 > {NOTES_BACKLOG_LINES}"))
        elif content > 5 and days_stale is not None and days_stale > NOTES_STALE_DAYS:
            backlog.append((scope, content, f"{days_stale} 天未整理"))
    return backlog


def scan_stale_modules():
    """扫 `stale-modules.yaml`，返回 `[{"sub": "<basic/sub>", "ts": "<last_marked_at>"}, ...]`。

    stale 标记由 PostToolUse hook（check-stale-modules.py）在改动命中某子模块
    `code_paths` 时写入，语义 = 「代码动了、该模块的文档还没跟上」——正是
    `module_close_check.py` 检查 1 的判定对象，也是「该跑收口检查了」最便宜的确定性证据。

    **为什么不 import check-stale-modules.py 的 `load_stale()`**（六问第 5 条要求：不复用
    同契约的既有实现时写明理由）：那个脚本在**模块级**包 UTF-8 流，而本脚本抬头也包过
    一次——两个 TextIOWrapper 抢同一个 buffer，先被回收的那个关掉 buffer，另一个当场
    `ValueError: I/O operation on closed file`（`module_close_check._load_csm` 的 docstring
    记着这次实测崩溃，它为此把加载做成「只 exec 一次」）。本脚本挂在 Stop 上、每轮都跑，
    不值得为读两个字段引入那条路径。

    代价是这里有第二份解析。把它压到最小：只认 `save_stale()` 产出的两行形态（两空格
    缩进的顶层键、四空格缩进的 `last_marked_at`），是 `load_stale()` 文法的**真子集**
    （`reasons` / `first_marked_at` 一概不取）。该文件由 `save_stale()` 代码生成而非手写，
    形态不会自由漂移；真漂了这里只会少读到条目（少报，fail-safe），不会误判成有积压。
    """
    if not STALE_FILE.exists():
        return []
    try:
        text = STALE_FILE.read_text(encoding="utf-8")
    except Exception:
        return []
    out = []
    cur = None
    for raw in text.splitlines():
        m = re.match(r"^\s{2}(\S+):\s*$", raw)
        if m:
            cur = {"sub": m.group(1), "ts": ""}
            out.append(cur)
            continue
        if cur is not None:
            m = re.match(r"^\s{4}last_marked_at:\s*(.*)$", raw)
            if m:
                cur["ts"] = m.group(1).strip()
    return out


def _next_pm_id(existing, today_str):
    """PM-<YYYYMMDD>-<3 位序号>；按当日已有条目累加。"""
    prefix = f"PM-{today_str.replace('-', '')}-"
    max_n = 0
    for item in existing:
        if isinstance(item, dict):
            pid = item.get("id", "")
            if pid.startswith(prefix):
                try:
                    n = int(pid.split("-")[-1])
                    max_n = max(max_n, n)
                except Exception:
                    pass
    return f"{prefix}{max_n + 1:03d}"


def upsert_pending(state, kind, severity, details, dedup_key, source="check-iteration-trigger.py"):
    """按 dedup_key 幂等 upsert pending_maintenance 条目。"""
    items = state.get("pending_maintenance") or []
    if not isinstance(items, list):
        items = []
    now_iso = datetime.now(timezone.utc).isoformat()
    today = datetime.now().date().isoformat()

    # 命中 dedup → touch
    for item in items:
        if isinstance(item, dict) and item.get("dedup_key") == dedup_key and item.get("status") == "open":
            item["created_at"] = now_iso
            item["details"] = details
            item["severity"] = severity
            state["pending_maintenance"] = items
            return item["id"], False  # False = 未新增

    # 未命中 → append
    new_item = {
        "id": _next_pm_id(items, today),
        "kind": kind,
        "severity": severity,
        "source": source,
        "created_at": now_iso,
        "dedup_key": dedup_key,
        "status": "open",
        "closed_at": None,
        "details": details,
    }
    items.append(new_item)
    state["pending_maintenance"] = items
    return new_item["id"], True  # True = 新增


def main():
    # Codex 门下会话不在 workframe 项目内：零写入、零输出退出（判定与门条件只在 _harness 一处）
    if _harness.hook_outside_project():
        return 0
    state = load_activity()
    if state.get("dormant") or state.get("wake_up_pending"):
        sys.exit(0)

    # dedup_key 不含时间维度（v0.4 M6 修正）：
    # pending_maintenance 是「当前待办信号板」而非事件流水（流水归 events.jsonl）。
    # 这些信号表达的都是**持续状态**（"一直没做维护" / "问题分仍然超标"），旧写法把日期/周
    # 拼进 dedup_key，导致状态未消解时每天/每周堆一条同义条目（实测某项目 cadence_timeout
    # 从 5 月堆到 7 月共 47 条，占 pending 总量 92%，制造虚假积压）。
    # 现在同一 kind 只保留一条 open 条目，upsert 命中时刷新 details/severity/created_at。

    # ---- 取数（锁外，IO 重）----
    # 六个 kind 在这里拿到的都是**上界**（未经驳回基线过滤的值）：基线只会让计入的
    # 证据变少、起算日期变晚、或整条被压制期跳过，不可能把结论放大。锁内按各自的
    # 基线重算。cadence 一侧这条不变式靠 `_apply_signals()` 里那句「基线只前移不后退」
    # 维持——去掉它 cad_days 就能超过这里的 dsli（曾经如此，见该段注释）。
    last_date = derive_last_iteration_date()
    dsli = days_since(last_date)
    events = read_recent_events(days=EVENTS_WINDOW_DAYS)
    problem_upper, activity_upper = score_weighted_events(events)
    backlog = scan_memory_backlog()
    stale_marks = scan_stale_modules()
    # completed 日期表在锁外解析一次，锁内按基线用 count_since() 反复数——board.yaml
    # 的逐行扫描是 IO，不进临界区。
    # completed_delta 从「仅在无主信号时惰性查」改为无条件预算：它的兜底条件依赖
    # 其余信号是否产生，而那要到锁内才知道；若沿用惰性、在锁外按上界判定要不要算，
    # 上界命中而锁内被基线抑制的那些轮次就会拿不到 delta，兜底信号被持续漏掉。
    completed_dates = completed_task_dates()
    completed_upper = count_since(completed_dates, last_date)

    # 锁外快速短路：上界都不达标 + 其余信号也不达标 → 本次必定无信号，直接退出，
    # 不为「什么都不写」去争一次锁（Stop 每轮都跑，多数轮次无信号）。
    if not (dsli is None or dsli >= CADENCE_DAYS
            or problem_upper >= PROBLEM_THRESHOLD
            or activity_upper >= ACTIVITY_THRESHOLD
            or backlog
            or stale_marks
            or completed_upper >= COMPLETED_DELTA_FALLBACK):
        sys.exit(0)
    # 注：`backlog` 与 `stale_marks` 这两项拿不到抑制的好处——抑制要读 pending_maintenance，
    # 而那必须在锁内（锁外快照读不到别的会话刚跑完的 `--close-pm`）。所以 notes 积压 /
    # stale 未清期间每轮都会进锁一次，哪怕结论一定是「被抑制」。是正确性换来的开销，
    # 不是零开销。

    # ---- 判定 + 写入（锁内，对磁盘最新状态执行）----
    # upsert 是「读列表 → 按 dedup_key 查 → 改或追加」的读改写，在锁外算完再整体写回，
    # 两个并发 trigger 会各自基于旧列表计算，后写的把对方新增的条目抹掉
    # （pending_maintenance 与 counter 同属同键并发问题）。
    #
    # **信号判定也在这里，不在锁外**：各 kind 的计分起点要读 pending_maintenance 里
    # closed 条目的 closed_at，而 `load_activity()` 的锁外快照与锁内磁盘态是两次独立
    # 读取。在锁外判定时，另一个会话刚跑完的 `--close-pm` 还看不见，于是抑制被绕过、
    # 照常新建一条 open（可稳定复现）——而那正是这套机制要修的缺陷的并发版本。
    # completed_delta 的兜底条件依赖其余信号是否产生，一并入锁。
    counts = {"new": 0, "touched": 0}

    def _apply_signals(s):
        counts["new"] = counts["touched"] = 0  # 锁内可能重放，计数重置
        signals = []  # (kind, severity, details, dedup_key)

        # 锁内取一次「现在」，六个 kind 的基线判定共用同一时刻——各取各的会让同一轮
        # 里的几条信号基于不同的 now，边界上可能自相矛盾。
        now = datetime.now(timezone.utc)

        # 1. Cadence（迭代日期由 proposals/ 代码派生，v0.4 G1#2 起不再读手工 baseline；
        #    驳回过则改从驳回时刻重新计时，见 dismissal_baseline()）
        cad_base = dismissal_baseline(s, "cadence_timeout", now)
        if cad_base is None:
            cad_days = dsli
            cad_detail = (f"距上次自迭代 {dsli} 天" if dsli is not None
                          else "从未自迭代（proposals/ 无已处理提案）")
        else:
            # 两条路各自内部一致、互不混用：无基线走 days_since()（本地日期口径，
            # proposals 里的日期本就是本地语义），有基线两侧都取 UTC 日期
            # （closed_at 是 UTC 写入的）。混着相减会差一天。
            cad_days = (now.date() - cad_base.date()).days
            # **基线只前移不后退**——与 completed_delta 的 `max(last_date, 驳回日+1)`
            # 是同一条约束的对偶写法（起算点取 max ⟺ 时长取 min）：真正的起算点是
            # 「上次自迭代」与「上次驳回」里更晚的那个。缺了这一句，一个很久以前驳回、
            # 之后又真的迭代过的项目会算出 cad_days > dsli，而 dsli 正是锁外短路给
            # cadence 用的上界（见 main() 取数段），于是**报不报取决于另一个不相干信号
            # 有没有把锁外闸打开**，details 还会写出「自 X 驳回以来 N 天未产出提案」
            # 这句假话（实测：7 天前驳回 + 2 天前真产出提案 + 另有 problem 信号开闸）。
            # `dsli is None`（proposals 两目录皆空 = 从未自迭代）语义上是 +∞，**不参与
            # 比较**：写成 `min(cad_days, dsli)` 会在那里抛 TypeError，而本函数跑在
            # update_activity 的锁内回调里——异常冒泡后 atomic_write 不执行，**本轮全部
            # 信号一起不落盘**、进程仍 exit 0（与 event_after() 的 tz 兜底防的是同一个
            # 静默失效形态）。而那恰是每个新装机项目的开局状态（scaffold 建的
            # proposals/{applied,rejected} 只有 .gitkeep），cadence 在其中立即命中，
            # 用户驳回一次即中招。
            # 两侧口径差：dsli 用本地日期、cad_days 用 UTC 日期，跨时区最多差 1 天。
            # 这里是**取较小值的钳制**、不是相减（上一段注释禁的是相减）。差的那 1 天
            # **方向随时区正负偏移而定**：UTC+ 侧本地日期跑在 UTC 前面，dsli 偏大、钳制
            # 偏松，提醒早一天（多报）；UTC− 侧反过来，dsli 偏小、钳制偏紧，提醒晚一天
            # （少报）。**两向都不破坏 `cad_days <= dsli`**——钳制用的就是锁外短路那个
            # 同一个 dsli，不变式**由构造保证**、与时区无关；口径差只影响数值（≤1 天），
            # 不影响不变式。与 completed_delta 那侧直接拿本地 last_date 与 UTC closed_at
            # 比大小同源（那处注释同样只写了 UTC+ 的方向，已一并改准）。
            if dsli is not None and dsli < cad_days:
                # 驳回之后又有过迭代活动 → 起算点是那次迭代，不是驳回时刻。
                # 文案必须跟着换：沿用「自 X 驳回以来 N 天」会让日期与天数互相矛盾。
                cad_days = dsli
                cad_detail = (f"距上次自迭代 {dsli} 天"
                              f"（{cad_base.date().isoformat()} 驳回后又有迭代活动，"
                              f"按更晚的起算点计）")
            else:
                cad_detail = (f"自 {cad_base.date().isoformat()} 驳回以来 {cad_days} 天"
                              f"未产出提案")
        if cad_days is None or cad_days >= CADENCE_DAYS:
            signals.append(("cadence_timeout", "info", cad_detail, "cadence_timeout"))

        # 2. WeightedEvents
        #    problem 与 activity 各只计**自己上次被驳回之后**发生的事件（各按自己的
        #    dedup_key 取基线，互不串扰；取数与理由见 `dismissal_baseline()`）。
        #    无基线时直接用锁外算好的上界，省一次重算。
        prob_base = dismissal_baseline(s, "problem_threshold", now)
        problem = (problem_upper if prob_base is None else score_weighted_events(
            [e for e in events if event_after(e, prob_base)])[0])
        if problem >= PROBLEM_THRESHOLD:
            signals.append((
                "problem_threshold", "warn",
                # 文案带上基线：不带的话这个分数与用户在 events.jsonl 里能数到的
                # 条数对不上（"我明明看到三条，为什么说 2 分"），而分数正是他判断
                # 要不要跑自迭代的依据。
                f"问题类加权分 {problem:.1f} ≥ {PROBLEM_THRESHOLD}" if prob_base is None
                else (f"自 {prob_base.date().isoformat()} 驳回以来问题类加权分 "
                      f"{problem:.1f} ≥ {PROBLEM_THRESHOLD}"),
                "problem_threshold",
            ))
        act_base = dismissal_baseline(s, "activity_threshold", now)
        activity = (activity_upper if act_base is None else score_weighted_events(
            [e for e in events if event_after(e, act_base)])[1])
        if activity >= ACTIVITY_THRESHOLD:
            signals.append((
                "activity_threshold", "info",
                f"活动类加权分 {activity:.1f} ≥ {ACTIVITY_THRESHOLD}" if act_base is None
                else (f"自 {act_base.date().isoformat()} 驳回以来活动类加权分 "
                      f"{activity:.1f} ≥ {ACTIVITY_THRESHOLD}"),
                "activity_threshold",
            ))

        # 3. 记忆积压（v0.4 M7）——notes.md 攒了但没整理进落点
        #    驳回抑制走**纯压制期**（第三类接法）：本 kind 扫的是 notes 的当前状态，
        #    没有可与 closed_at 相比的证据时间点，过滤不了任何东西，只能在压制期内
        #    整条跳过。**只取「有没有基线」、不取基线的值**——压制期由载体自己的寿命
        #    定义，故恒等于 PM_CLOSED_RETENTION_DAYS，理由与「为什么不另设常量」见
        #    dismissal_baseline() 末段。复用它而不自写「有没有 closed 条目」，是为了
        #    白拿它的 dedup_key 筛选 / status 筛选 / 未来时钟防御 / naive ts 兜底
        #    （自写一份就是同契约的第二套实现，收窄其中一份不会有东西报警）。
        #    这条信号的关闭是**常规工作流的一部分**而非偶发驳回：librarian 第 6 步
        #    每次整理完 notes 就 `--close-pm` 关它，此前关了等于没关、下一轮原样重开。
        bl_base = dismissal_baseline(s, "memory_backlog", now)
        if backlog and bl_base is None:
            detail = "；".join(f"{sc}({r})" for sc, _, r in backlog[:4])
            if len(backlog) > 4:
                detail += f" 等 {len(backlog)} 个"
            signals.append((
                "memory_backlog", "info",
                f"notes 待整理：{detail}（调 librarian skill 处理）",
                "memory_backlog",
            ))

        # 4. Completed delta fallback（独立 kind，避免与 cadence_timeout 混淆语义）
        cd_base = dismissal_baseline(s, "completed_delta", now)
        if cd_base is None:
            completed_delta = completed_upper
        else:
            # 基线日 **+1 天**：count_since 用 `>=`，加一天等价于「严格晚于驳回日」，
            # 与事件侧 event_after 的 `>` 同口径——驳回动作发生在它判定为已闭环的
            # 那批任务之后。代价是驳回当天完成的任务不计入本 kind（fail-safe，少报）。
            # closed_at 是 UTC 而 board 的日期由模型按本地写，跨时区时**方向随时区正负
            # 偏移而定**：UTC+ 侧 +1 后可能刚好等于本地当天、多计几条（多报，不静音）；
            # UTC− 侧 UTC 日期跑在本地前面，+1 落到本地后天、漏计次日完成的任务（少计，
            # 而本 kind 是兜底信号，少计即可能静音）。两向影响都 ≤1 天，与 cadence 侧那处
            # 钳制的口径差同源。
            cd_since = (cd_base.date() + timedelta(days=1)).isoformat()
            if last_date is not None and last_date > cd_since:
                # 基线只前移不后退。**这一句还是锁外短路正确性的前提**：短路用的
                # `completed_upper` 是按 last_date 算的，只有 cd_since >= last_date
                # 时锁内的 delta 才不会超过它。去掉这句，一个很久以前被驳回、之后
                # 又迭代过的项目会让锁内算出比上界更大的值——而短路早已在锁外把这
                # 一轮判成「必定无信号」退出了，真信号被吞掉且不留痕迹。
                cd_since = last_date
            completed_delta = count_since(completed_dates, cd_since)
        if not signals and completed_delta >= COMPLETED_DELTA_FALLBACK:
            signals.append((
                "completed_delta", "info",
                f"completed 增量 {completed_delta} ≥ {COMPLETED_DELTA_FALLBACK}"
                if cd_base is None
                else (f"自 {cd_base.date().isoformat()} 驳回以来 completed 增量 "
                      f"{completed_delta} ≥ {COMPLETED_DELTA_FALLBACK}"),
                "completed_delta",
            ))

        # 5. 收口检查待跑——stale 清单非空 = 改动命中了某模块的 code_paths 而文档没跟上，
        #    正是 module-close-check 检查 1 的判定对象。信号只回答「该跑了」，**不重算它
        #    的任何一项**：那九项各有自己的判据（含账本条目、基准同值、覆盖率对账），在
        #    这里再实现一遍就是同契约的第二套口径，正是六问第 1 / 第 5 条要防的东西。
        #
        #    **驳回抑制走证据过滤类（第一类接法，与 problem / activity 同族）**，不是
        #    memory_backlog 那种纯压制期：每条 stale 记录自带 `last_marked_at`（由
        #    check-stale-modules 的 `now_iso()` 以 UTC 秒级 ISO 写，与 `closed_at` 同口径），
        #    所以有可比的证据时间点，能只计**严格晚于**基线的那些——判定式直接复用
        #    `event_after()`，不另写一份比较。
        #    落到用户身上的差别：驳回只静音「当下这批 stale」，此后任何新标记都能重新报
        #    出来；同一个模块被再次触碰也算新证据（`mark_stale()` 每次都 bump
        #    `last_marked_at`），而那恰恰是又干了一轮活、确实该再跑一次收口检查的时刻。
        #    backlog 做不到这一点只是因为它扫的是 notes 的当前状态、没有时间点可比。
        #
        #    位置在 completed_delta **之后**：那条是 `if not signals` 的兜底信号，把新
        #    kind 插到它前面会让本 kind 一命中就把它压掉——那是对既有 kind 行为的改动。
        st_base = dismissal_baseline(s, "close_check_due", now)
        fresh = [m for m in stale_marks
                 if st_base is None or event_after(m, st_base)]
        if fresh:
            # 计数在前、名字在后：模块路径可以很长（中文 basic 名 + 子模块名），名字
            # 打头时「一共几个」会被挤到末尾甚至被消费方截断掉。
            names = "、".join(m["sub"] for m in fresh[:3]) + (" 等" if len(fresh) > 3 else "")
            signals.append((
                "close_check_due", "info",
                f"{len(fresh)} 个模块有 stale 标记未清：{names}"
                + ("" if st_base is None
                   else f"（{st_base.date().isoformat()} 驳回之后新标记的）")
                + "——收口前跑 `workframe-module-close-check`"
                  "（不在 PATH 上时跑插件目录下的 scripts/module_close_check.py）",
                "close_check_due",
            ))

        for kind, severity, details, dedup_key in signals:
            _, is_new = upsert_pending(s, kind, severity, details, dedup_key)
            if is_new:
                counts["new"] += 1
            else:
                counts["touched"] += 1

    try:
        if update_activity(STATE_DIR, _apply_signals) is None:
            sys.exit(0)  # 更新失败（锁超时 / 读写异常，_state_io 已告警）；下次会话重算同样的信号
    except Exception as e:
        print(f"[warn] failed to save activity-state: {e}", file=sys.stderr)
    new_count, touched_count = counts["new"], counts["touched"]

    # stdout 也保留一行摘要（非侵入）
    if new_count > 0:
        print(f"[自迭代提醒] 新增 {new_count} 条维护项（另 touch {touched_count} 条）；/core:audit 查看。")

    sys.exit(0)


if __name__ == "__main__":
    main()
