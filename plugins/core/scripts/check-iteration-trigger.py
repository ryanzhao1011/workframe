#!/usr/bin/env python3
"""
Stop Hook — 维护提醒检测 ＋ Codex 门的后台自迭代排队

产出两种维护提醒，写进 `activity-state.json` 的 `pending_maintenance`，供首问提醒与 `/core:audit` 消费：
  - `memory_backlog`：notes.md 攒了没整理（见 `scan_memory_backlog()`）
  - `close_check_due`：有模块带 stale 标记未清（见 `scan_stale_modules()`）

另外调一次 `_selfiter_hooks.on_hook('Stop')`：Codex 门的后台自迭代在这里排队。

**驳回基线（两个 kind 共用一套语义，按 dedup_key 各管各的）**：用户驳回一条信号 = 认定此刻之前的
都已处理，该 kind 的起点推进到那条 pending 条目的 `closed_at`。关一条不影响另一条（取数与边界见
dismissal_baseline()）。两种接法：`close_check_due` 是**证据过滤类**（只认 `last_marked_at` 更晚的标记），
`memory_backlog` 是**纯压制期类**——它扫的是 notes 的**当前状态**，没有可与驳回时刻相比的证据时间点，
只能不问状态地静默一段；压制期不设独立常量，等于那条 closed 条目的 GC 保留期。close_check_due 扫的
同样是「当前清单」，但清单里每条自带 `last_marked_at`，故归证据过滤类而非压制期类。

只做**确定性统计**，不做语义判断；不按事件总数催人跑自迭代——后台自迭代按证据自己选题。

⚠️ `skill_used.success` 不能当触发条件：它要求 agent 对自己刚做完的工作做成败自评，实测**从未产出过 false**
（消费项目 2.5 个月 77 条 skill_used 全部 success=true）——这不意味着零失败，而是自评在收尾阶段天然偏向
正面。维持一个永不满足的触发条件比没有更糟（它会让人误以为质量退化能被自动发现）。字段本身保留：
recompute_skill_metrics.py 继续统计、`/core:audit` 继续展示，作为**人工观察**数据有价值。

dormant=true 或 wake_up_pending=true 时直接退出，不检测也不写 pending_maintenance。
"""

import io
import json
import os
import re
import sys
from datetime import datetime, timezone
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
from _notes_entries import BACKLOG_CHARS, open_entries  # noqa: E402
from _state_io import (  # noqa: E402
    load_activity as _load_activity,
    memory_dir_of,
    save_activity as _save_activity,
    state_dir_of,
    update_activity,
)


PROJECT_DIR = _project_dir()
STATE_DIR = state_dir_of(PROJECT_DIR)
EVENTS_FILE = STATE_DIR / "events.jsonl"
METRICS_FILE = STATE_DIR / "skill-metrics.yaml"
ACTIVITY_FILE = STATE_DIR / "activity-state.json"
MEMORY_DIR = memory_dir_of(PROJECT_DIR)
STALE_FILE = STATE_DIR / "stale-modules.yaml"

# 记忆积压阈值（v0.4 M7）
# 只做**确定性统计**（行数 / 待处理字数 / 距上次提升天数），不做语义聚类——
# "哪些条目是同一主题、够不够提升"需要判断力，交给 librarian skill 在被调起后做。
# 脚本硬做语义会产出不准的信号，维持一个不准的信号比没有更糟。
NOTES_BACKLOG_LINES = 80      # 单个 notes.md 内容行（见 _count_content_lines）超过此值视为积压
NOTES_STALE_DAYS = 30         # 距上次 memory_promoted 超过此天数且 notes 内容行 > 5


def load_activity():
    """字段全集与损坏处理见 `_state_io`（四个 hook 共用一份实现）。"""
    return _load_activity(STATE_DIR)


def save_activity(state):
    _save_activity(STATE_DIR, state)


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


def dismissal_baseline(state, dedup_key, now=None):
    """某个 kind 的起点 = 同 dedup_key 已 closed 条目里**最新**的 closed_at。

    语义：用户驳回一次该信号，等于认定此刻之前的都已处理，从驳回时刻重新起算。
    **按 dedup_key 各管各的**，关一条不影响另一条。

    没有它时的形态：驳回只是把条目标成 closed，而 closed 条目不参与 upsert 的 dedup，
    于是下一次 Stop 拿同一批已处理的东西重新算出同一个结论、以新编号原样重开——
    关闭对该 kind 实质无效。

    **不新存基线**：读的是 pending 生命周期本来就有的 `closed_at`（由 `maintenance_workorder.py`
    经 `update_activity` 的锁写入）。基线陈旧的方向是抑制失效、照常提醒（fail-safe）。

    **抑制是有期限的**：载体就是那条 closed 条目，它在 closed_at + PM_CLOSED_RETENTION_DAYS
    天后被 SessionStart GC 删除，基线随之消失，**GC 后必然重开**——notes 积压只有 librarian 会清、
    stale 清单只有 code-to-doc 会清，两边都没有滚动窗口把旧的推出视野。那不是缺陷：要让抑制活过 GC
    只能新存一个不受 GC 管的状态，而那个状态陈旧时的方向是永久静音。

    **memory_backlog 只用本函数的「有没有基线」，不用基线的值**（纯压制期，见 `_apply_signals()`）：
    它没有可与 closed_at 相比的证据时间点，所以压制期只能由载体自己的寿命定义——有未被 GC 的 closed
    条目就静默，条目一没就重开。这样压制期恒等于 PM_CLOSED_RETENTION_DAYS，**不需要也不应该另设常量**：
    另设的话它的有效值恒为 `min(常量, 保留期)`（实测设 14 天时第 8 天条目被 GC、提醒照常重开，
    多出的 7 天完全无效），而没有任何闸会说破这条脱钩。实际形态是「每个保留期提醒一次」。
    close_check_due 清单里每条自带 `last_marked_at`，能与 closed_at 相比，故走证据过滤而非压制期
    ——保留期内新标记照样报得出来，只有旧标记被静音。

    返回 aware datetime；无可用基线返回 None（= 不抑制）。
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
        # **必须按 dedup_key 筛**：不筛的话关掉一种会顺带抑制另一种（跨 kind 串扰，静默失明）。
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
            # 未来时刻的 closed_at（系统时钟回拨 / 状态文件被手改）会让所有标记永远
            # 落在基线之前 = 该信号被永久抑制。这是本机制唯一的 fail-dangerous 方向，
            # 就地丢弃这一条（其余合法条目仍可提供基线）。
            continue
        if latest is None or ts > latest:
            # 多条 closed 取**最新**：GC 前同一 kind 可以被关过不止一次，取较早的
            # 那条等于让抑制从一个已经作废的时点起算，中间那段旧标记会重新报出来。
            latest = ts
    return latest


def event_after(ev, baseline):
    """带 `ts` 字段的一条证据是否发生在基线**之后**。

    调用方是 `scan_stale_modules()` 的 stale 标记（`last_marked_at` 为 UTC 秒级 ISO，由
    check-stale-modules 的 `now_iso()` 写），取 `ev["ts"]`；解析不了即判 False（保守静音）。

    用 `>` 而非 `>=`：驳回动作发生在被它判定为「已处理」的那批标记之后，与 closed_at
    同秒的标记属于被驳回的那一批。两侧都是秒级精度（`closed_at` 由
    `maintenance_workorder.py` 以 `isoformat(timespec="seconds")` 写），撞秒是现实可能而非理论边界。
    """
    ts = parse_iso_ts(ev.get("ts"))
    if ts is None:
        return False
    # tz 兜底不能省：遇到 naive 的 `last_marked_at` 会抛 TypeError。
    # 实测后果不是崩溃而是更隐蔽的静默失效：本函数跑在 `update_activity` 的锁内回调里，
    # 异常冒泡到 main 的 `except Exception` 被兜住、进程照常 exit 0，但 mutator 抛出时
    # `atomic_write` 还没执行——**本轮全部信号一起没落盘**（backlog 陪葬），stderr 只留一行 `[warn] failed to save activity-state`。
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
    判据三选一：
      - notes.md 内容行 > NOTES_BACKLOG_LINES
      - 还没处置的条目合计 ≥ BACKLOG_CHARS 字（条目定义与后台采证同一份）
      - 距最近一次 memory_promoted 事件 > NOTES_STALE_DAYS 且内容行 > 5；从没提升过的项目
        改从该文件最早一条未处置条目的日期算起
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
            text = notes.read_text(encoding="utf-8")
        except Exception:
            continue
        content, waiting = _count_content_lines(text), open_entries(text)
        chars = sum(len(entry["text"]) for entry in waiting)
        days = days_stale
        if last_promoted is None:
            dates = [entry["date"] for entry in waiting if entry["date"]]
            days = days_since(min(dates).isoformat()) if dates else None
        if content > NOTES_BACKLOG_LINES:
            backlog.append((scope, content, f"内容 {content} 行 > {NOTES_BACKLOG_LINES}"))
        elif chars >= BACKLOG_CHARS:
            backlog.append((scope, content, f"待处理 {chars} 字 ≥ {BACKLOG_CHARS}"))
        elif content > 5 and days is not None and days > NOTES_STALE_DAYS:
            backlog.append((scope, content, f"{days} 天未整理"))
    return backlog


def scan_stale_modules():
    """扫 `stale-modules.yaml`，返回 `[{"sub": "<basic/sub>", "ts": "<last_marked_at>"}, ...]`。

    stale 标记由 PostToolUse hook（check-stale-modules.py）在改动命中某子模块
    `code_paths` 时写入，语义 = 「代码动了、该模块的文档还没跟上」——正是
    `module_close_check.py` 检查 1 的判定对象，也是「该跑收口检查了」最便宜的确定性证据。

    **为什么不 import check-stale-modules.py 的 `load_stale()`**（不复用
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
    from _selfiter_policy import in_job
    if in_job():
        return 0
    state = load_activity()
    if state.get("dormant") or state.get("wake_up_pending"):
        sys.exit(0)
    from _selfiter_hooks import on_hook
    on_hook(PROJECT_DIR, _harness.harness(), 'Stop')

    # dedup_key 不含时间维度（v0.4 M6 修正）：
    # pending_maintenance 是「当前待办信号板」而非事件流水（流水归 events.jsonl）。
    # 这些信号表达的都是**持续状态**（"notes 一直没整理" / "stale 一直没清"），旧写法把日期/周
    # 拼进 dedup_key，导致状态未消解时每天/每周堆一条同义条目，制造虚假积压。
    # 现在同一 kind 只保留一条 open 条目，upsert 命中时刷新 details/severity/created_at。

    # ---- 取数（锁外，IO 重）----
    backlog = scan_memory_backlog()
    stale_marks = scan_stale_modules()

    # 锁外快速短路：两项都空 → 本次必定无信号，直接退出，不为「什么都不写」去争一次锁
    # （Stop 每轮都跑，多数轮次无信号）。两项非空时拿不到抑制的好处——抑制要读
    # pending_maintenance，而那必须在锁内（锁外快照读不到别的会话刚跑完的 `--close-pm`）。
    # 所以 notes 积压 / stale 未清期间每轮都会进锁一次，哪怕结论一定是「被抑制」。
    if not (backlog or stale_marks):
        sys.exit(0)

    # ---- 判定 + 写入（锁内，对磁盘最新状态执行）----
    # upsert 是「读列表 → 按 dedup_key 查 → 改或追加」的读改写，在锁外算完再整体写回，
    # 两个并发 trigger 会各自基于旧列表计算，后写的把对方新增的条目抹掉
    # （pending_maintenance 与 counter 同属同键并发问题）。
    #
    # **信号判定也在这里，不在锁外**：各 kind 的起点要读 pending_maintenance 里 closed 条目的
    # closed_at，而 `load_activity()` 的锁外快照与锁内磁盘态是两次独立读取。在锁外判定时，
    # 另一个会话刚跑完的 `--close-pm` 还看不见，于是抑制被绕过、照常新建一条 open（可稳定复现）。
    counts = {"new": 0, "touched": 0}

    def _apply_signals(s):
        counts["new"] = counts["touched"] = 0  # 锁内可能重放，计数重置
        signals = []  # (kind, severity, details, dedup_key)

        # 锁内取一次「现在」，两个 kind 的基线判定共用同一时刻
        now = datetime.now(timezone.utc)

        # 1. 记忆积压——notes.md 攒了但没整理进落点
        #    驳回抑制走**纯压制期**：本 kind 扫的是 notes 的当前状态，
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

        # 2. 收口检查待跑——stale 清单非空 = 改动命中了某模块的 code_paths 而文档没跟上，
        #    正是 module-close-check 检查 1 的判定对象。信号只回答「该跑了」，**不重算它
        #    的任何一项**：那些检查各有自己的判据（含账本条目、基准同值、覆盖率对账），在
        #    这里再实现一遍就是同契约的第二套口径。
        #
        #    **驳回抑制走证据过滤类**，不是 memory_backlog 那种纯压制期：每条 stale 记录自带
        #    `last_marked_at`（由 check-stale-modules 的 `now_iso()` 以 UTC 秒级 ISO 写，与
        #    `closed_at` 同口径），所以有可比的证据时间点，能只计**严格晚于**基线的那些——
        #    判定式直接复用 `event_after()`，不另写一份比较。
        #    落到用户身上的差别：驳回只静音「当下这批 stale」，此后任何新标记都能重新报
        #    出来；同一个模块被再次触碰也算新证据（`mark_stale()` 每次都 bump
        #    `last_marked_at`），而那恰恰是又干了一轮活、确实该再跑一次收口检查的时刻。
        #    backlog 做不到这一点只是因为它扫的是 notes 的当前状态、没有时间点可比。
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
        print(f"[维护提醒] 新增 {new_count} 条维护项（另 touch {touched_count} 条）；/core:audit 查看。")

    sys.exit(0)


if __name__ == "__main__":
    main()
