#!/usr/bin/env python3
"""
Stop Hook — 自迭代触发检测（v0.2.1）

触发逻辑 = Cadence ∨ WeightedEvents：
  - Cadence：距上次自迭代 ≥ 7 天
  - WeightedEvents：近 7 天 events.jsonl 加权积分超阈值
      - problem（user_correction=3.0, task_blocked=2.0）≥ 5
      - activity（skill_used=0.2, per-session cap=3）≥ 10
      - 注：proposal_failed 在 v0.2.2-fixup-2 移除（model_mediated 不能驱动核心触发）
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

替代信号：`user_correction`（problem 权重 3.0）。它由 correction-detection 规则按明确
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
from _state_io import (  # noqa: E402
    load_activity as _load_activity,
    save_activity as _save_activity,
    update_activity,
)


PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
BOARD_FILE = PROJECT_DIR / "projects" / "board.yaml"
STATE_DIR = PROJECT_DIR / ".claude" / "workframe-state"
PROPOSALS_DIR = PROJECT_DIR / "projects" / "proposals"
EVENTS_FILE = STATE_DIR / "events.jsonl"
METRICS_FILE = STATE_DIR / "skill-metrics.yaml"
ACTIVITY_FILE = STATE_DIR / "activity-state.json"
MEMORY_DIR = PROJECT_DIR / ".claude" / "agent-memory"

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
PROBLEM_WEIGHTS = {
    "user_correction": 3.0,    # protocol_expected: correction-detection rule + agent wrap-up 写入
    "task_blocked": 2.0,       # protocol_expected: 主 producer = test-case-design skill；fallback producer
                                # = 角色 wrap-up（仅限非 QA / 手动 blocked 转换，且必须做 dedup 检查）
                                # （详见 agent-protocols.md §Step 1 task_blocked producer 策略）
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


def count_completed_since(date_str):
    """现算 board.yaml 中 status=completed 且 updated_at ≥ date_str 的任务数（v0.4 G1#2）。

    date_str 为 None（从未迭代）时统计全部 completed。缺 updated_at 的 completed
    任务在 date_str 非 None 时保守不计入——老任务反复计入会制造假增量信号。
    轻量逐行解析（任务边界 = "- id:" 行），不引第三方 yaml 依赖；summary 段
    没有 "- id:" 行，天然不被计入。
    """
    if not BOARD_FILE.exists():
        return 0
    tasks = []
    cur = None
    try:
        for line in BOARD_FILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("- id:"):
                cur = {"status": None, "updated": None}
                tasks.append(cur)
            elif cur is not None and stripped.startswith("status:"):
                # 剥离行内注释（status: completed  # note）再取值
                cur["status"] = stripped.split(":", 1)[1].split("#", 1)[0].strip().strip("\"'")
            elif cur is not None and stripped.startswith("updated_at:"):
                m = re.search(r"(\d{4}-\d{2}-\d{2})", stripped)
                cur["updated"] = m.group(1) if m else None
    except Exception:
        return 0
    count = 0
    for t in tasks:
        if t["status"] != "completed":
            continue
        if date_str is None or (t["updated"] is not None and t["updated"] >= date_str):
            count += 1
    return count


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


def read_recent_events(days=7):
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
    """扫描 agent-memory/*/notes.md 的积压情况（纯确定性统计）。

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
    state = load_activity()
    if state.get("dormant") or state.get("wake_up_pending"):
        sys.exit(0)

    # dedup_key 不含时间维度（v0.4 M6 修正）：
    # pending_maintenance 是「当前待办信号板」而非事件流水（流水归 events.jsonl）。
    # 这些信号表达的都是**持续状态**（"一直没做维护" / "问题分仍然超标"），旧写法把日期/周
    # 拼进 dedup_key，导致状态未消解时每天/每周堆一条同义条目（实测某项目 cadence_timeout
    # 从 5 月堆到 7 月共 47 条，占 pending 总量 92%，制造虚假积压）。
    # 现在同一 kind 只保留一条 open 条目，upsert 命中时刷新 details/severity/created_at。

    signals = []  # (kind, severity, details, dedup_key)

    # 1. Cadence（迭代日期由 proposals/ 代码派生，v0.4 G1#2 起不再读手工 baseline）
    last_date = derive_last_iteration_date()
    dsli = days_since(last_date)
    if dsli is None or dsli >= CADENCE_DAYS:
        signals.append((
            "cadence_timeout", "info",
            f"距上次自迭代 {dsli} 天" if dsli is not None
            else "从未自迭代（proposals/ 无已处理提案）",
            "cadence_timeout",
        ))

    # 2. WeightedEvents
    events = read_recent_events(days=7)
    problem, activity = score_weighted_events(events)
    if problem >= PROBLEM_THRESHOLD:
        signals.append((
            "problem_threshold", "warn",
            f"问题类加权分 {problem:.1f} ≥ {PROBLEM_THRESHOLD}",
            "problem_threshold",
        ))
    if activity >= ACTIVITY_THRESHOLD:
        signals.append((
            "activity_threshold", "info",
            f"活动类加权分 {activity:.1f} ≥ {ACTIVITY_THRESHOLD}",
            "activity_threshold",
        ))

    # 3. 记忆积压（v0.4 M7）——notes.md 攒了但没整理进落点
    backlog = scan_memory_backlog()
    if backlog:
        detail = "；".join(f"{s}({r})" for s, _, r in backlog[:4])
        if len(backlog) > 4:
            detail += f" 等 {len(backlog)} 个"
        signals.append((
            "memory_backlog", "info",
            f"notes 待整理：{detail}（调 librarian skill 处理）",
            "memory_backlog",
        ))

    # 4. Completed delta fallback（独立 kind，避免与 cadence_timeout 混淆语义）
    if not signals:
        delta = count_completed_since(last_date)
        if delta >= COMPLETED_DELTA_FALLBACK:
            signals.append((
                "completed_delta", "info",
                f"completed 增量 {delta} ≥ {COMPLETED_DELTA_FALLBACK}",
                "completed_delta",
            ))

    if not signals:
        sys.exit(0)

    # 写入 pending_maintenance（幂等）——**在锁内对磁盘最新状态执行**：
    # upsert 是「读列表 → 按 dedup_key 查 → 改或追加」的读改写，在锁外算完再整体写回，
    # 两个并发 trigger 会各自基于旧列表计算，后写的把对方新增的条目抹掉
    # （pending_maintenance 与 counter 同属同键并发问题）。
    counts = {"new": 0, "touched": 0}

    def _apply_signals(s):
        counts["new"] = counts["touched"] = 0  # 锁内可能重放，计数重置
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
