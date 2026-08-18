#!/usr/bin/env python3
"""
SessionStart Hook — 会话启动准备（v0.2.1 修订版）

与 heartbeat-check.py 串联，职责：
  1. session_counter +1 并更新 last_session_at / active_sessions_30
  2. 读 activity-state.json 判 dormant / wake_up_pending（双字段语义）
  3. 读 session-digest-latest.md 打印简短上下文（非 dormant/wake_up 时）
  4. pending_maintenance GC：移除 status=closed 且 closed_at < now-7d 的条目

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
from _state_io import load_activity, save_activity, update_activity  # noqa: E402


PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()

# pending_work 里 undecided（未定处置）批次的软提醒静默窗：记录后头几个会话提，之后闭嘴。
# 3 是「够用户注意到、不至于变成每次开会话的噪音」的折中；信息不会丢（doctor 的
# init_completeness 常驻可见，material-intake 可随时重新盘点）。relay 批次不受此窗约束
# ——那是用户自己拍板要做的活，轻提示常驻、嫌烦可改 paused。
PENDING_WORK_WINDOW = 3
STATE_DIR = PROJECT_DIR / ".claude" / "workframe-state"
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

PM_CLOSED_RETENTION_DAYS = 7  # 关闭 7 天后 GC

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
    """写一条事件到 events.jsonl；失败不阻塞 hook。"""
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "type": event_type,
        **fields,
    }
    try:
        with EVENTS_FILE.open("a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


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
    落盘验收（launcher 在重启前跑）只能查文件；hooks 是否真的活着、rules 镜像有没有被
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
        print("[workframe] 安装验收通过 —— 骨架 / 配置 / 订阅 / rules 镜像 / hook 链路全部就位。")
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
    f = PROJECT_DIR / ".claude" / "workframe-state" / "setup-state.json"
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
    """移除 status=closed 且 closed_at < now - 7d 的条目。就地返回新列表。"""
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
    仍按 `agent-protocols.md` §1 由主 Claude 显式 Read。

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

    mem_dir = PROJECT_DIR / ".claude" / "agent-memory"
    if not roles and not mem_dir.is_dir():
        return

    print("[memory-map] 角色域一览——直做该域工作前，读其契约 + MEMORY.md（内容不在此注入）")
    print("  路径：契约 <plugin>/agents/<role>.md，项目侧角色在 .claude/agents/（同名则覆盖 core）"
          "｜记忆 .claude/agent-memory/<role>/MEMORY.md")
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
    """把当前插件根路径写进 workframe-state，供 skill / agent 在 Bash 中定位插件内脚本。

    背景：CC 官方的 plugin bin/ PATH 注入在部分环境不生效（Windows + directory 订阅
    实测 2.1.225 未注入），且模型的 Bash 环境没有 ${CLAUDE_PLUGIN_ROOT}。本脚本自定位
    （scripts/ 的上一级即插件根），每次 SessionStart 刷新——插件升级导致缓存路径变化时
    自动跟上（官方文档明确 PLUGIN_ROOT 随版本更新而变化，不能持久化假设不变）。

    消费方统一配方（Git Bash / macOS 通用）：
        python "$(cat .claude/workframe-state/plugin-root.txt)/bin/workframe-xxx"

    写 bytes 避免 Windows 文本模式把 LF 改写成 CRLF。失败不阻塞会话启动。
    """
    try:
        root = Path(__file__).resolve().parents[1]
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        (STATE_DIR / "plugin-root.txt").write_bytes(
            (str(root).replace("\\", "/") + "\n").encode("utf-8")
        )
    except Exception:
        pass


def main():
    write_plugin_root()
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
