#!/usr/bin/env python3
"""
SessionStart Hook — 会话启动准备（v0.2.1 修订版）

与 heartbeat-check.py 串联，职责：
  1. session_counter +1 并更新 last_session_at / active_sessions_30
  2. 读 activity-state.json 判 dormant / wake_up_pending（双字段语义）
  3. 读 session-digest-latest.md 打印简短上下文（非 dormant/wake_up 时）
  4. pending_maintenance GC：移除 status=closed 且 closed_at 早于
     PM_CLOSED_RETENTION_DAYS 天前的条目

v0.2.1 修订：
  - 修复状态机反转（v0.2.0：首次超阈值直接写 dormant=true 导致 wake-up 推迟一次）
    → 改为：首次超阈值时 wake_up_pending=true, dormant=false，本次 session 立即展示摘要
  - 修复 wake-up 打印"上次会话时间"被 now 覆盖的 bug（打印 last_ts 原值）
  - pending_maintenance 字段语义改为对象数组

Dormant Profiles 阈值（与 reference/project-architecture.md §Dormant profiles 对齐）：
  high-frequency=30d / normal=60d / low-frequency=90d / archive=立即
"""

import io
import json
import os
import re
import sys
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
    append_line, event_json, load_activity, memory_dir_of, merge_spills,
    save_activity,
    state_dir_of, update_activity,
)


def _stdin_payload():
    """hook 载荷（`session_id` / `cwd` / `transcript_path` …）；非管道 / 空 / 不合法一律当空字典。

    **bytes 读 + 显式 UTF-8 解码**：Windows 上 PowerShell 起的 python 其 stdin 默认按本机
    ANSI 代码页解码（`chcp 65001` 也不改变它），含中文的 `cwd` 会变成另一串字符。
    本脚本此前不读 stdin——项目根来自 `CLAUDE_PROJECT_DIR`；Codex 门下没有那个变量，
    `cwd` 与会话号只能从载荷取。
    """
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


PAYLOAD = _stdin_payload()
PROJECT_DIR = _project_dir(PAYLOAD)

# 会话标记文件：`<state>/current-session-<harness>-<session_id>.json`，给 Codex 门下的
# `workframe-event` 第三级兜底取会话号（那一门没有会话号环境变量）。**只有 Codex 门写**
# ——CC 门有 `CLAUDE_CODE_SESSION_ID`，不需要，也不能让 CC 的状态目录多出一个文件。
# 陈旧标记（SessionEnd 没跑到：进程被杀 / 崩溃）由下一次 Codex SessionStart 清掉，阈值 7 天：
# 一个 Codex 会话很少跨天，7 天给「TUI 开过周末」留余量，又让 `session_marker_id` 要求的
# 「恰好一个候选」在一周内自行恢复。**这是框架清理自己写的运行态文件，只作用于这个前缀。**
SESSION_MARKER_PREFIX = "current-session-"
SESSION_MARKER_STALE_DAYS = 7
_SESSION_ID_SAFE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")

# pending_work 里 undecided（未定处置）批次的软提醒静默窗：记录后头几个会话提，之后闭嘴。
# 3 是「够用户注意到、不至于变成每次开会话的噪音」的折中；信息不会丢（doctor 的
# init_completeness 常驻可见，material-intake 可随时重新盘点）。relay 批次不受此窗约束
# ——那是用户自己拍板要做的活，轻提示常驻、嫌烦可改 paused。
PENDING_WORK_WINDOW = 3
STATE_DIR = state_dir_of(PROJECT_DIR)
ACTIVITY_FILE = STATE_DIR / "activity-state.json"
DIGEST_FILE = STATE_DIR / "session-digest-latest.md"
EVENTS_FILE = STATE_DIR / "events.jsonl"
BOARD_FILE = PROJECT_DIR / "projects" / "board.yaml"

DORMANT_THRESHOLDS_DAYS = {
    "high-frequency": 30,
    "normal": 60,
    "low-frequency": 90,
    "archive": 0,
}

# 关闭后保留天数，到期由本脚本的 gc_pending_maintenance 清理。
# **不是自由参数**：那条 closed 条目同时是「驳回抑制」的唯一载体，被 GC 删掉抑制即失效，
# 所以本常量也就是抑制能维持多久。两条约束由 validate 的
# `pm_retention_matches_events_window` 对账（改这里之前先读那道闸的红灯文案）：
#   == check-iteration-trigger.EVENTS_WINDOW_DAYS —— 基线消失与旧事件出窗同刻，交接无缝
#   >= check-iteration-trigger.CADENCE_DAYS       —— 否则驳回 cadence 后条目先被删、
#                                                    天数还超标，提醒当场复活
PM_CLOSED_RETENTION_DAYS = 7

# C+ drift check（v0.2.2 起）
DRIFT_HISTORY_DAYS = 30        # recent_drift_repairs 只保留最近 30 天
DRIFT_ALERT_THRESHOLD = 3      # 30 天内 ≥3 次 drift 修复 → 启动摘要提示


def load_state():
    """字段全集与损坏处理见 `_state_io`（四个 hook 共用一份实现）。"""
    return load_activity(STATE_DIR)


def save_state(state):
    save_activity(STATE_DIR, state)


def check_onboarding_notice():
    """v0.2.1+ 只读检查：未 onboarded 的项目打印一行温和提示。

    设计原则：
    - 零写入副作用——hook 永不创建 marker、改 settings 或动 .gitignore
    - 所有 onboarding 状态变更走 /core:onboard 唯一入口
    - 用户主动 skip 也会写 onboarded.json，本函数随即静默
    """
    onboarded_file = STATE_DIR / "onboarded.json"
    if not onboarded_file.exists():
        print("[workframe] 可运行 /core:onboard 查看可选配置；选择跳过后将不再提示。")


def parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def append_event(event_type, **fields):
    """写一条事件到 events.jsonl；失败不阻塞 hook（加锁与 spill 见 _state_io.append_line）。"""
    event = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "type": event_type,
        **fields,
    }
    append_line(EVENTS_FILE, event_json(event))


def flush_event_spills():
    """把上次锁超时落下的 `events.<pid>.spill.jsonl` 并回主文件。

    为什么挂在 SessionStart：spill 是**锁抢不到**时产生的，而抢不到锁意味着当时正有别的
    进程在写——那一刻并不适合重试。会话启动是并发压力最低、且必定会跑到的一点。
    无 spill 时零输出、零副作用，所以它不影响任何既有会话的表现。
    """
    n = merge_spills(EVENTS_FILE)
    if n:
        print(f"[workframe] 并回 {n} 条上次锁超时落盘的事件。", file=sys.stderr)


def check_summary_drift_and_repair(state, now):
    """C+ drift check（v0.2.2 起）：检查 board.yaml 的 summary 数字 vs tasks 实际计数是否漂移。

    决策矩阵：
    - 无 board.yaml / 无 summary 块                     → return（无操作）
    - 无 drift                                          → return（无操作）
    - tasks 含未知 status（如 'completd' 拼写错误）    → 写 summary_drift_repair_skipped + stderr 提示，不修复
    - 有 drift + tasks 全是合法 status                → 调 recompute 修复 + 写 summary_drift_repaired

    C+ 三个保护条件：
    1. 只修 summary 段，不改 tasks（recompute_board_summary 本身保证）
    2. 本函数严格保持轻量（只读 board / 比对 / 调 recompute / 写事件，不做记忆/提案/重活）
    3. 30 天内 ≥3 次 drift 修复时启动摘要提示用户检查
    """
    if not BOARD_FILE.exists():
        return

    # 复用 recompute_board_summary 模块（与 session-end-flush 相同 import 路径）
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from recompute_board_summary import (  # noqa: E402
            count_task_statuses,
            parse_summary_counts,
            recompute_board_summary,
        )
    except Exception as e:
        append_event(
            "summary_drift_repair_skipped",
            reason="import_failed",
            detail=f"{type(e).__name__}: {str(e)[:100]}",
        )
        return

    try:
        # utf-8-sig 容错 Windows BOM（Codex P1-5 实测：PowerShell Set-Content -Encoding UTF8
        # 默认带 BOM，会让 SUMMARY_HEADER_RE 匹配失败误报 summary_block_not_found）
        text = BOARD_FILE.read_text(encoding="utf-8-sig")
    except Exception as e:
        append_event(
            "summary_drift_repair_skipped",
            reason="read_failed",
            detail=f"{type(e).__name__}: {str(e)[:100]}",
        )
        return

    try:
        actual_total, actual_counts, unknown_statuses, n_entries = count_task_statuses(text)
        summary_total, summary_counts = parse_summary_counts(text)
    except Exception as e:
        append_event(
            "summary_drift_repair_skipped",
            reason="parse_failed",
            detail=f"{type(e).__name__}: {str(e)[:100]}",
        )
        return

    if summary_total is None:
        append_event(
            "summary_drift_repair_skipped",
            reason="summary_block_not_found",
        )
        return  # 无 summary 块；recompute 当前只更新已有 summary，不自动创建

    # 找 drift 字段
    drift_fields = {}
    if summary_total != actual_total:
        drift_fields["total"] = {"summary": summary_total, "actual": actual_total}
    for key in actual_counts:
        if summary_counts.get(key) != actual_counts[key]:
            drift_fields[key] = {
                "summary": summary_counts.get(key),
                "actual": actual_counts[key],
            }

    if not drift_fields:
        return  # 无 drift

    # C+ 保护 1a：看到任务条目却一条 status 都没解析出来 → 不修复。
    # 否则 drift check 会把一块有内容的看板"修"成全 0（口径同 recompute 的 tasks_unparsed）。
    if n_entries and actual_total == 0:
        append_event(
            "summary_drift_repair_skipped",
            reason="tasks_unparsed",
            drift_fields=drift_fields,
        )
        print(f"[drift-check] board.yaml 有 {n_entries} 个任务条目但一条 status 都没解析出来；"
              "跳过自动 summary 修复。检查 tasks 块格式；详情见 /core:audit")
        return

    # C+ 保护 1b：tasks 块含未知 status → 不修复
    if unknown_statuses:
        append_event(
            "summary_drift_repair_skipped",
            reason="unknown_statuses_in_tasks",
            unknown_statuses=sorted(unknown_statuses),
            drift_fields=drift_fields,
        )
        print(
            f"[drift-check] board.yaml tasks 含未知 status: {sorted(unknown_statuses)}; "
            "跳过自动 summary 修复。检查 task 条目 status 拼写；详情见 /core:audit"
        )
        return

    # 修复 drift
    try:
        result = recompute_board_summary()
    except Exception as e:
        append_event(
            "summary_drift_repair_skipped",
            reason="recompute_failed",
            detail=f"{type(e).__name__}: {str(e)[:100]}",
            drift_fields=drift_fields,
        )
        return

    # recompute **正常返回但没修**（它自己的 strict 保护拦下了，如 parse_mismatch /
    # unknown_statuses_in_tasks）→ 必须走 skipped 事件。信息本来就没丢（repair_status
    # 一直是真值），丢的是**事件名**——而 audit 按事件名分组统计「自动修复 X 次 /
    # 跳过 Y 次」，写错名就把「跳过」算进「修复」。上面三支早退分支用的正是这个事件名，
    # 同形态分流即可。
    #
    # **这一支单次执行下不可达，保留它是防御性的**：上面 `:170` 已用**同一个**
    # `count_task_statuses` 解析过**同一份文件**，recompute 能返回的每种 skipped 成因
    # 都被各自的早退先拦下（解析失败 → `:174`；无 summary 块 → `:180`；有条目却一条
    # status 都没解析出来 → `:203`；未知 status → `:214`），而 `{"status": "error"}`
    # 只在 recompute 的 `main()` CLI 入口产生、本处直接调函数不经它。本支覆盖的是
    # ①将来 recompute 新增未被上面预过滤的 skipped 成因 ②两次读盘之间 board.yaml
    # 被并发改写（`:160` 读到的与 recompute 重读到的不是同一份）。
    # **别把它当热路径**：正常运行时它一次都不会命中。
    if result.get("status") != "ok":
        append_event(
            "summary_drift_repair_skipped",
            reason="recompute_skipped",
            # 本文件的 reason 只用自己那套词表，recompute 的原因塞 detail——与上面
            # `reason="recompute_failed"` + detail 那支同形态，recompute 将来加新 reason
            # 也不用回来改本文件与 schema。
            detail=str(result.get("reason") or "")[:100],
            repair_status=result.get("status"),
            drift_fields=drift_fields,
        )
        print(f"[drift-check] board.yaml 有 {len(drift_fields)} 项 summary drift，但重算被跳过"
              f"（{result.get('reason') or result.get('status')}）——**未修改任何字段**；"
              "详情见 /core:audit")
        # 不记 drift 修复历史：什么都没修，计入 30 天频次会把告警阈值攒虚（同上面三支
        # 早退分支——它们也都在 _record_repair 之前 return）。
        return

    append_event(
        "summary_drift_repaired",
        drift_fields=drift_fields,
        repair_status=result.get("status"),
        likely_cause="SessionEnd hook skipped or timed out in previous session",
    )

    # C+ 保护 3：维护 30 天 drift 修复历史 + 频繁 drift 提示。
    # 列表 GC + append 属「基于当前值计算」，按 `_state_io` 契约必须走 update_activity
    # 的锁内读改写——在锁外算完再靠 save_state 的三方合并写回，两个并发会话会各自基于
    # 旧列表计算，后写的把对方那次修复记录抹掉（与 session_counter 递增同型）。
    cutoff = now - timedelta(days=DRIFT_HISTORY_DAYS)
    kept = {"n": 0}

    def _record_repair(s):
        hist = s.get("recent_drift_repairs")
        if not isinstance(hist, list):
            hist = []
        hist = [ts for ts in hist if parse_iso(ts) and parse_iso(ts) > cutoff]
        hist.append(now.isoformat())
        s["recent_drift_repairs"] = hist
        # 同步调用方持有的 state：否则它仍是旧值，main() 收尾的 save_state 三方合并
        # 会把「我刚写进去的新值」当成调用方的显式改动给覆盖回去
        state["recent_drift_repairs"] = hist
        kept["n"] = len(hist)

    # 必须检查返回值：update_activity 在锁超时 / 状态读不出来 / 写失败时返回 None
    # 且**不落盘**。不检查就会出现「summary 确实修好了，但 recent_drift_repairs 没记上」
    # 而日志照样宣布成功——频繁 drift 的告警阈值（30 天 ≥3 次）因此永远攒不够数。
    if update_activity(STATE_DIR, _record_repair) is None:
        print("[drift-check] board summary 已修复，但 drift 历史未能落盘"
              "（activity-state 写入失败，原因见上一条 [warn]）——本次不计入 30 天频次统计。",
              file=sys.stderr)
        return

    print(f"[drift-check] 自动修复 board summary {len(drift_fields)} 项 drift。")
    if kept["n"] >= DRIFT_ALERT_THRESHOLD:
        print(
            f"  ⚠ 最近 {DRIFT_HISTORY_DAYS} 天内已触发 {kept['n']} 次 drift 修复——"
            "可能是 SessionEnd hook 频繁未执行或超时。"
        )
        print(
            "  大项目可设环境变量 CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS=5000（或更高）缓解。"
        )


def run_first_session_acceptance():
    """首个会话的运行时验收：内联跑 doctor 第 0 组，结果打进启动上下文。

    为什么放这里而不是让模型自觉跑：安装是否成功必须由**代码**确定性地回答一次。
    落盘验收（launcher 在重启前跑）只能查文件；hooks 是否真的活着、必载纪律有没有被
    SessionStart 同步出来，只有重启后才有答案——这就是那一半。

    只在 `session_counter == 1` 触发（装完的第一个会话），之后不再打扰；
    doctor 侧对依赖重启的项在重启前自降 info，所以这里跑到的是完整结果。
    失败一律不阻塞会话启动。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from workframe_doctor import run_all, summarize  # noqa: E402
    # 「跳过」这个词此前用在这两条降级上——用户几乎一定会把它读成「没问题所以跳过」，
    # 而真实情况是验收根本没跑起来，装没装对无人回答。
    except Exception as e:
        print(f"[workframe] 安装验收**没能执行**（doctor 不可用: {type(e).__name__}: {e}）。"
              f"这不等于验收通过——请转述给用户，并建议手动跑 "
              f"`workframe-doctor --group install` 复查。", file=sys.stderr)
        return
    try:
        results = run_all(PROJECT_DIR, "install")
    except Exception as e:
        print(f"[workframe] 安装验收**没能执行**（{type(e).__name__}: {e}）。"
              f"这不等于验收通过——请转述给用户，并建议手动跑 "
              f"`workframe-doctor --group install` 复查。", file=sys.stderr)
        return
    problems = summarize(results)
    if problems is None:
        print("[workframe] 安装验收通过 —— 骨架 / 配置 / 订阅 / hook 链路全部就位。")
    else:
        n_err = sum(1 for r in results if r["status"] == "error")
        print(f"[workframe] 安装验收发现 {n_err} 个错误、"
              f"{sum(1 for r in results if r['status'] == 'warn')} 个提醒：")
        print(problems)
        print("  复查：workframe-doctor --group install")
    # hook 的 stdout 进的是**模型上下文**，不是用户的终端显示——用户重启后看到的是空白屏。
    # 这是本项目首个会话，也是他唯一一次自然获知「到底装好没有」的时机，所以这里显式
    # 要求模型转述。不这么写的话，验收跑了、结论只有模型知道，用户永远等不到。
    print("[workframe] ↑ 以上是本项目**首个会话**的安装验收结果。用户在终端看不到这段"
          "（hook 输出只进模型上下文），请在本轮回复的开头用一两句话把结论转述给他，"
          "有错误则一并给出处理建议。")


def check_pending_work(session_counter=None):
    """初始化第二幕的接力驱动（2026-08-11 取代 pending_intake 提醒）。

    装机流程已能当场建树 + 安置资料（module_init.py），剩到重启后的只有用户**知情拍板**
    推迟的批次（pending_work.batches，pace 由用户在装机节奏闸选定）。本段按 pace 分流
    打扰强度——强度是用户自己选的，不是系统强加的：

      relay     首会话（session_counter==1）注入**强接力**：本轮回复必须主动提出继续；
                此后每会话一行轻提示（用户嫌烦可让模型改 paused）
      paused    零打扰——只在 doctor / 看板可见，用户说「继续初始化」时恢复
      undecided 未定处置的资料：沿用软提醒 + 静默窗 + 文件搬走自清除
                （「留原位」是合法终态，决定落定即删批次——记的是「还没决定」不是「还没搬」）

    批次完成 → 模型删除对应条目；全部销账 → 删除整个 `pending_work` 键。
    """
    f = STATE_DIR / "setup-state.json"
    if not f.exists():
        return
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        pw = data.get("pending_work") or {}
        batches = pw.get("batches") or []
    except Exception:
        return
    if not batches:
        return

    relay, undecided = [], []
    for b in batches:
        pace = b.get("pace", "undecided")
        if pace == "paused":
            continue
        if pace == "undecided":
            remaining = [p for p in (b.get("files") or []) if (PROJECT_DIR / p).exists()]
            if not remaining:
                continue  # 自清除：文件已被搬走 / 归档 / 删除
            undecided.append(dict(b, files=remaining))
        else:
            relay.append(b)

    def _fmt(bs):
        return "；".join(
            f"「{b.get('name', '?')}」{len(b.get('files') or [])} 项 → {b.get('target_skill', '?')}"
            for b in bs
        )

    if relay:
        if session_counter == 1:
            print(f"[workframe] 初始化第二幕待接力（用户装机时拍板「重启后做」）：{_fmt(relay)}")
            if pw.get("note"):
                print(f"  装机时的安排：{pw['note']}")
            print("  **请在本轮回复中主动向用户提出继续**：报出批次与装机时拍的节奏，问从哪批开始；"
                  "用户同意后调对应 skill 当场执行（工具链此刻已完整加载）。")
            print("  用户说搁置 → 把对应批次的 pace 改为 paused（编辑 setup-state.json，之后零打扰），"
                  "并视作当前阶段完成、主动提议把已产出内容提交（若拍的策略是完成后一次提交）；"
                  "批次完成 → 删除对应条目；全部完成 → 删除整个 pending_work 键。")
        else:
            print(f"[workframe] 初始化第二幕仍有 {len(relay)} 批待接力：{_fmt(relay)}"
                  f"——用户提到或有空档时接续；嫌打扰可改 pace=paused。")
    if undecided:
        since = pw.get("recorded_at_session")
        in_window = True
        if session_counter is not None and isinstance(since, int):
            in_window = session_counter - since <= PENDING_WORK_WINDOW
        if in_window:
            print(f"[workframe] 有 {len(undecided)} 批存量资料未定处置：{_fmt(undecided)}")
            print("  用户没提起时也主动问一次；处置选项与边界见 material-intake skill。"
                  "决定落定（含「留原位不动」）即删除对应批次——只说「待会再说」不算决定，"
                  "本轮别再追、下次照常提。")


def gc_pending_maintenance(items, now):
    """移除 status=closed 且 closed_at 早于 PM_CLOSED_RETENTION_DAYS 天前的条目。

    就地返回新列表。
    """
    if not isinstance(items, list):
        return []
    cutoff = now - timedelta(days=PM_CLOSED_RETENTION_DAYS)
    kept = []
    for item in items:
        if not isinstance(item, dict):
            continue  # 旧版字符串条目直接丢弃（v0.2.0 → v0.2.1 自清理）
        if item.get("status") == "closed":
            closed_at = parse_iso(item.get("closed_at"))
            if closed_at and closed_at < cutoff:
                continue
        kept.append(item)
    return kept


def _agent_tagline(md_path):
    """取 agent frontmatter 里 description 的首行，作为该域的定位语。

    两种形态都认：`description: |` 的 block scalar（core 四个 agent 都是这个）与
    `description: 单行文本`（项目自定义角色常见）。解析失败返回 None——记忆地图是
    锦上添花的上下文，缺一行定位语不值得让 SessionStart 报错。
    """
    try:
        lines = md_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for i, raw in enumerate(lines[1:81], start=1):
        s = raw.strip()
        if s == "---":
            break
        if not s.startswith("description:"):
            continue
        inline = s[len("description:"):].strip()
        if inline and inline not in ("|", ">", "|-", ">-"):
            return inline.strip("\"'")
        # block scalar：取下一个非空行
        for follow in lines[i + 1:i + 6]:
            if follow.strip():
                return follow.strip()
        return None
    return None


def print_memory_map():
    """打印角色域记忆地图（A0a）。

    为什么需要：SessionStart 的其余脚本无一注入 role/shared MEMORY，SubagentStart
    的注入又只在委派时发生——主 Claude 直做时对角色记忆的暴露为零，连"有哪些域、
    该读哪份"都无从得知。这里每个 scope 打一行定位语，承担**领域发现**；具体内容
    仍按主会话必载片 §直做前恒走显式 Read 由主 Claude 显式 Read。

    扫两处 agents 目录：plugin 内置 + 项目 `.claude/agents/`（自定义角色），
    **同名以项目为准**（与 Claude Code 官方的同名覆盖优先级一致）。
    """
    plugin_agents = Path(__file__).resolve().parents[1] / "agents"
    project_agents = PROJECT_DIR / ".claude" / "agents"

    roles = {}  # role -> (agent 文件, 来源标记)
    for d, is_project in ((plugin_agents, False), (project_agents, True)):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            if is_project:
                # 覆盖 core 同名 vs 纯新增，对用户含义不同：前者意味着 core 版本
                # 已被替换（该域的行为不再是出厂默认），值得在地图上看得见
                mark = "（项目覆盖）" if f.stem in roles else "（项目自定义）"
            else:
                mark = ""
            roles[f.stem] = (f, mark)

    mem_dir = memory_dir_of(PROJECT_DIR)
    if not roles and not mem_dir.is_dir():
        return

    # 记忆目录问 `_state_io` 现算，不写死字面——目录位置只有那一个事实源
    mem_rel = mem_dir.relative_to(PROJECT_DIR).as_posix()
    print("[memory-map] 角色域一览——直做该域工作前，读其契约 + MEMORY.md（内容不在此注入）")
    print("  路径：契约 <plugin>/agents/<role>.md，项目侧角色在 .claude/agents/（同名则覆盖 core）"
          f"｜记忆 {mem_rel}/<role>/MEMORY.md")
    for role in sorted(roles):
        path, mark = roles[role]
        tag = _agent_tagline(path) or "（该 agent 未声明 description）"
        has_mem = (mem_dir / role / "MEMORY.md").exists()
        miss = "" if has_mem else " ⟨无 MEMORY.md⟩"
        print(f"  - {role}{mark}：{tag}{miss}")
    shared_mem = mem_dir / "shared" / "MEMORY.md"
    if shared_mem.exists():
        print("  - shared：跨角色权威事实（≥2 角色共用的口径与纪律）"
              "，与单角色记忆冲突时**以 shared 为准**")


def write_plugin_root():
    """把当前插件根路径写进运行态状态目录，供 skill / agent 在 Bash 中定位插件内脚本。

    背景：CC 官方的 plugin bin/ PATH 注入在部分环境不生效（Windows + directory 订阅
    实测 2.1.225 未注入），且模型的 Bash 环境没有 ${CLAUDE_PLUGIN_ROOT}。本脚本自定位
    （scripts/ 的上一级即插件根），每次 SessionStart 刷新——插件升级导致缓存路径变化时
    自动跟上（官方文档明确 PLUGIN_ROOT 随版本更新而变化，不能持久化假设不变）。

    消费方统一配方（Git Bash / macOS 通用）：
        python "$(cat <状态目录>/plugin-root.txt)/bin/workframe-xxx"
    `<状态目录>` 即 `_state_io.state_dir_of(项目根)`；skill / reference 里的字面配方
    写的是出厂形态的状态目录（`_state_io.new_rel("state")`）。

    **按门分文件 ＋ 兼容位**：两扇门各有自己的安装缓存根（CC 在 `~/.claude/plugins/cache/…`，
    Codex 在 `<CODEX_HOME>/plugins/cache/…`），同一个项目同时挂两扇门时，单文件会被轮流覆盖
    写成「最后一个起会话的那扇门」的根——一边升级一边没升时，另一门的 skill 配方会静默执行
    另一个安装里的旧脚本。所以本门的根另写一份 `plugin-root.<门>.txt`（门标识来自命令行
    `--harness`，取不到就不写这份）；`plugin-root.txt` 保留为「最后一个写的」兼容位，存量
    `$(cat …/plugin-root.txt)` 配方零改动。doctor 读两份分文件、断言两根的插件版本相等。

    写 bytes 避免 Windows 文本模式把 LF 改写成 CRLF。失败不阻塞会话启动。
    """
    try:
        root = Path(__file__).resolve().parents[1]
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        line = (str(root).replace("\\", "/") + "\n").encode("utf-8")
        (STATE_DIR / "plugin-root.txt").write_bytes(line)
        door = _harness.harness()
        if door in _harness.HARNESSES:
            (STATE_DIR / f"plugin-root.{door}.txt").write_bytes(line)
    except Exception:
        pass


def write_session_marker(payload):
    """Codex 门：写本会话的标记文件，并清掉过期的旧标记。失败不阻塞会话启动。"""
    if _harness.harness() != _harness.CODEX:
        return
    sid = _harness.session_id(payload)
    if not sid or not _SESSION_ID_SAFE.match(sid):
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / f"{SESSION_MARKER_PREFIX}{_harness.CODEX}-{sid}.json").write_bytes(
            json.dumps({"harness": _harness.CODEX, "session_id": sid,
                        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                       ).encode("utf-8"))
    except Exception:
        return
    cutoff = datetime.now(timezone.utc).timestamp() - SESSION_MARKER_STALE_DAYS * 86400
    for p in STATE_DIR.glob(f"{SESSION_MARKER_PREFIX}*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            continue


def ensure_agents_md_present():
    """已装项目的 `AGENTS.md` 补建入口——**这一条不接，模板就等于没落地**。

    注入片从本版起把「项目自有判据」的落点指向项目根 `AGENTS.md`。新建项目由装机脚本铺，
    **已装项目不会再跑一次装机**，所以补建必须挂在每次会话启动这条路径上。判定式只有一份
    （`project_scaffold.ensure_agents_md`），这里只是第二个调用点。

    **记步的触发条件是「确认文件在位」，不是「刚创建了它」。** 补建每次会话都跑，但文件
    已在就跳过——只在创建那一支记步的话，升级到本版的存量项目会落进「文件有了、步没记」，
    而 `check_setup_state` 把 `agents_md` 列为必查步 ⇒ **恒 error 且永不自愈**
    （下一次会话仍然跳过创建，仍然不记）。幂等赋值，重复写同一个值无副作用。

    **但记步只在已经有一份可用的 `setup-state.json` 时落笔**（`only_if_present=True`）：
    运行态状态目录整个不进 git，所以 `git clone` 下来的项目必然**没有**这份文件——无条件
    记步会给它凭空造出一份只含 `agents_md` 的记录，`check_setup_state` 随即把它读成
    「初始化未走完，已完成 1/3 步」。那是**加入项目的同事在首个会话里被念出来的**第一句话，
    且此后每个会话原样重复；而文件不存在时 doctor 本来报 info 放行。判据（能不能解析出
    dict）与写入共用同一次读取，所以落在 `mark_setup_step` 里而不是这里，理由见那边。

    延迟 import：`project_scaffold` 不是本脚本的常规依赖，模块级 import 会让它的任何
    导入期问题都变成 SessionStart 的问题。失败不阻塞会话启动。
    """
    try:
        from project_scaffold import ensure_agents_md, mark_setup_step  # noqa: E402
        created = []
        if ensure_agents_md(PROJECT_DIR, created, []):
            print("[workframe] 已补建 AGENTS.md（项目自有判据落点，两扇门共用）"
                  "——按你的项目改写它，框架不覆盖已存在的内容。"
                  + ("项目根 CLAUDE.md 里已有的项目内容（项目目标 / 业务背景 / 项目特殊约束等）不会自动搬过来："
                     "按 core 插件 reference/claude-md-merge-guide.md 逐段归位，别把 CLAUDE.md 整段复制过去；"
                     "改 AGENTS.md 要用户本人确认，模型不代填。"
                     if (PROJECT_DIR / "CLAUDE.md").is_file() else ""))
        # 在位即记步——**不放进上面的 if 里**，见 docstring
        # `only_if_present=True`：没有可用的 setup-state.json 就不记、也不建，见 docstring
        if (PROJECT_DIR / "AGENTS.md").is_file():
            mark_setup_step(PROJECT_DIR, "agents_md", only_if_present=True)
    except Exception as e:
        print(f"[warn] AGENTS.md 补建跳过: {type(e).__name__}: {e}", file=sys.stderr)


def ensure_project_skills_link_present():
    """clone 侧的 `.claude/skills` 链接补建——**没有这一条，clone 出来的项目在 Claude Code 门下看不到项目 skill**。

    新建项目的项目 skills 真实源在 `.agents/skills/`，`.claude/skills` 是指向它的目录链接且被
    `.gitignore` 忽略，所以 `git clone` 带来的只有真实源；CC 只读 `.claude/skills`，而 doctor 按
    真实源解析、全绿。形态判定只有一份（`_harness.skills_cc_form`），两个动作都在 `project_scaffold`：
      - 位置上什么都没有、`.agents/skills/` 是目录 ⇒ 补建（`ensure_project_skills_link`）；
      - 链接的目标不存在、本项目 `.agents/skills/` 是目录（Windows 上项目整体改名 / 移动后 junction 仍指旧位置）
        ⇒ 解掉这根悬空链接、重指到本项目（`repoint_dangling_skills_link`，用户 2026-09-18 授权的唯一删除动作）；
      - 其余只报不动的形态（普通文件 / 指向别处 / 真目录副本 / 真实源不在）每会话打一行（`skills_form_note`）。
    每次会话都跑，所以它必须窄且幂等。两扇门都跑：链接是仓形态的一部分，不按门分（Codex 会话里补上它，
    下一个用 CC 打开的人就直接可用）。补建或重指之后 CC 门请求 `reloadSkills`（见 `_harness.hook_json_stdout`）：
    CC 的 skill 发现通常在 SessionStart hook 跑完之前，不请求的话本会话看不到项目 skill。
    延迟 import 与失败不阻塞会话启动，同 `ensure_agents_md_present`。
    """
    try:
        from project_scaffold import ensure_project_skills_link, repoint_dangling_skills_link  # noqa: E402
        codex = _harness.harness() == _harness.CODEX
        # 改变了 `.claude/skills` 的解析目标之后怎么说，按门分：Codex 直接读 `.agents/skills`，这根链接对它无影响；
        # CC 门由 hook_json_stdout 在退出时改投带 reloadSkills 的 JSON，让本会话重扫 skill 清单
        after = ("（Codex 门直接读 .agents/skills，不受这根链接影响）" if codex else
                 "已请 Claude Code 在本次启动后重扫 skill 清单；若本会话仍看不到项目 skill，"
                 "执行 /reload-skills 或重开会话。")
        created, skipped = [], []
        old = repoint_dangling_skills_link(PROJECT_DIR, created, skipped)
        if old:
            print(f"[workframe] 已把悬空的 .claude/skills 链接（原指向 {old}）重指到本项目 .agents/skills"
                  f"（项目整体改名 / 移动后，Windows junction 仍指着旧位置）。{after}")
            _harness.request_skills_reload()
        elif ensure_project_skills_link(PROJECT_DIR, created, skipped):
            print("[workframe] 已补建 .claude/skills → .agents/skills 目录链接"
                  f"（项目 skills 的真实源随仓走，链接不进 git；clone 后首个会话补建）。{after}")
            _harness.request_skills_reload()
        else:
            note = skills_form_note(PROJECT_DIR, codex)
            if note:
                print(note)
        for s in skipped:
            print(f"[warn] {s}", file=sys.stderr)
    except Exception as e:
        print(f"[warn] .claude/skills 链接补建跳过: {type(e).__name__}: {e}", file=sys.stderr)


def skills_form_note(project, codex):
    """`.claude/skills` 处于只报不动的形态时每会话一行（形态判定只在 `_harness.skills_cc_form`）；其余返回 None。

    复制出来的项目通常带着 counter>1 的运行态，首会话安装验收不会触发——所以这一行不等 doctor，每会话都打。
    """
    form = _harness.skills_cc_form(project)
    cc, neu = _harness.SKILLS_DIR_CC.as_posix(), _harness.SKILLS_DIR_NEUTRAL.as_posix()
    tail = "（Codex 门直接读 .agents/skills，不受影响）" if codex else ""
    if form == _harness.SKILLS_FILE:
        return (f"[workframe] {cc} 是一个普通文件、不是指向 {neu} 的链接——Claude Code 读不到任何项目 skill。"
                f"常见成因：macOS / Linux 上误提交的链接对象在 Windows 上 clone 出来。确认它不是你的文件后移走它"
                f"（它若被 git 跟踪，先 `git rm --cached {cc}`），下一次会话会补建链接。{tail}")
    two = _harness.skills_two_copies(project)      # 「两份不是同一目录」只问这一处（反向链接不算）
    if two is not None:
        what = "是指向别处的链接" if two == _harness.SKILLS_LINK_ELSEWHERE else "是一个真目录（通常是复制项目时展开的副本）"
        return (f"[workframe] {cc} {what}，与 {neu} 不是同一个目录——Claude Code 读前者，框架脚本与 Codex 读后者，"
                f"内容会分叉。先比对两边（副本里可能有人改过），合并进 {neu} 后再把 {cc} 换成指向它的链接；"
                f"细节见 workframe-doctor --group install。{tail}")
    if form == _harness.SKILLS_LINK_NEUTRAL_MISSING:
        return (f"[workframe] {cc} 是链接，但本项目的 {neu} 不在——项目 skills 的真实源没了。"
                f"从 git 恢复 {neu}（链接随即恢复），或解掉链接（Windows `rmdir .claude\\skills`、"
                f"POSIX `rm .claude/skills`，都只解链接）。{tail}")
    return None


def main():
    # Codex 门下会话不在 workframe 项目内：零写入、零输出退出（判定与门条件只在 _harness 一处）
    if _harness.hook_outside_project(PAYLOAD):  # 载荷已在模块顶层读过
        return
    # 接管 stdout，退出时一次写出。Codex 清单带 `--output hook-json`：本脚本的 stdout 以 `[workframe]` /
    # `[memory-map]` 起头，Codex 按 JSON 解析失败后整条丢弃；该模式下把全部输出收成一条 hookSpecificOutput。
    # CC 不带此参数：原样写回，只有本次补建 / 重指了 `.claude/skills` 时改投带 reloadSkills 的 JSON。
    _harness.hook_json_stdout("SessionStart")
    write_plugin_root()
    write_session_marker(PAYLOAD)
    ensure_agents_md_present()
    ensure_project_skills_link_present()
    try:
        flush_event_spills()
    except Exception as e:
        print(f"[warn] event spill flush failed: {type(e).__name__}: {e}", file=sys.stderr)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # 累加型更新走**锁内事务**：counter / active_sessions_30 是「读当前值再 +1」，
    # pending GC 是列表读改写——这三样在锁外算完再写，两个并发会话就会各少算一次
    # （实测：counter 期望 2 实得 1）。幂等赋值型字段（dormant / 各种标记）
    # 仍走后面的 save_state 三方合并即可。
    tick = {}

    def _session_tick(s):
        tick["was_dormant"] = bool(s.get("dormant", False))
        # 上次会话的真实时间（用于 wake-up 摘要展示，避免被 now 覆盖）
        tick["last_ts"] = parse_iso(s.get("last_session_at"))
        s["session_counter"] = int(s.get("session_counter", 0)) + 1
        s["last_session_at"] = now_iso
        # active_sessions_30（粗算：连续活跃链长度；真滚动窗口见 backlog）
        if tick["last_ts"] and (now - tick["last_ts"]).days <= 30:
            s["active_sessions_30"] = int(s.get("active_sessions_30", 0)) + 1
        else:
            s["active_sessions_30"] = 1
        s["pending_maintenance"] = gc_pending_maintenance(
            s.get("pending_maintenance", []), now
        )

    state = update_activity(STATE_DIR, _session_tick)
    if state is None:
        # 更新失败（锁超时 / 状态读不出来 / 写入异常）：本轮状态不落盘，但会话照常跑完
        # ——提示已由 _state_io 打到 stderr
        state = load_state()
        tick.setdefault("was_dormant", bool(state.get("dormant", False)))
        tick.setdefault("last_ts", parse_iso(state.get("last_session_at")))
    was_dormant = tick["was_dormant"]
    last_ts = tick["last_ts"]

    # dormant / wake_up 判定
    profile = state.get("dormant_profile", "normal")
    threshold = DORMANT_THRESHOLDS_DAYS.get(profile, 60)
    over_threshold = last_ts is not None and (now - last_ts).days > threshold

    if profile == "archive":
        # 场景 D：永久 dormant
        state["dormant"] = True
        state["wake_up_pending"] = False
        print(f"[archive] 项目 profile=archive，处于只读归档态。跳过周期检查。")
    elif over_threshold and not was_dormant:
        # 场景 A：首次超阈值唤醒 —— 展示摘要但不标 dormant
        state["dormant"] = False
        state["wake_up_pending"] = True
        last_ts_str = last_ts.isoformat() if last_ts else "未知"
        print(f"[wake-up] 项目超过 {profile} 阈值（{threshold}d）自动唤醒。")
        print(f"  - 上次会话：{last_ts_str}")
        print(f"  - 当前 session_counter={state['session_counter']}")
        print("  - 本次跳过自动维护；执行 /core:maintenance-review 可手动恢复。")
    elif was_dormant and over_threshold:
        # 场景 B 的延续：已 dormant 且仍超阈值（极少出现，只在 archive 外的人为标记）
        state["dormant"] = True
        state["wake_up_pending"] = False
        print(f"[dormant] 项目仍在 dormant（profile={profile}），跳过周期检查。")
    else:
        # 场景 C：正常激活
        state["dormant"] = False
        state["wake_up_pending"] = False
        if DIGEST_FILE.exists():
            try:
                content = DIGEST_FILE.read_text(encoding="utf-8").strip()
                if content:
                    first_lines = "\n".join(content.splitlines()[:5])
                    print("[last-session-digest]")
                    print(first_lines)
            except Exception:
                pass

    # C+ drift check：仅在非 dormant 非 wake_up_pending 状态下执行
    # （archive / wake-up / still-dormant 时不动 board.yaml）
    if not state.get("dormant") and not state.get("wake_up_pending"):
        try:
            check_summary_drift_and_repair(state, now)
        except Exception as e:
            # drift check 失败不阻塞 SessionStart 整体流程
            print(f"[warn] drift check failed: {type(e).__name__}: {e}", file=sys.stderr)

    # 角色域记忆地图（A0a）——与 drift check 同守卫：dormant / wake_up_pending /
    # archive 时不打印（归档态项目没人直做，纯噪音）
    if not state.get("dormant") and not state.get("wake_up_pending"):
        try:
            print_memory_map()
        except Exception as e:
            print(f"[warn] memory map failed: {type(e).__name__}: {e}", file=sys.stderr)

    # 首个会话的运行时验收（安装是否真的成功，由代码回答一次）
    if state["session_counter"] == 1:
        try:
            run_first_session_acceptance()
        except Exception as e:
            print(f"[warn] first-session acceptance failed: {type(e).__name__}: {e}", file=sys.stderr)

    # 存量资料待归档提醒（**每个会话**都查，不只首个——只在首会话提一次的话，
    # 用户当时说「先不弄」就等于永远不弄了。文件搬走 / 删掉后自动消失。）
    try:
        check_pending_work(state.get("session_counter"))
    except Exception as e:
        print(f"[warn] pending intake check failed: {type(e).__name__}: {e}", file=sys.stderr)

    # v0.2.1+: onboarding 提示（零副作用——只检查文件存在性 + 一行打印）
    try:
        check_onboarding_notice()
    except Exception as e:
        # onboarding check 失败不阻塞 SessionStart 整体流程
        print(f"[warn] onboarding check failed: {type(e).__name__}: {e}", file=sys.stderr)

    try:
        save_state(state)
    except Exception as e:
        print(f"[warn] failed to save activity-state: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
