#!/usr/bin/env python3
"""
SessionEnd Hook — 会话结束 flush + digest（v8.2）

触发：会话结束。stdin 字段名是 `reason`（写进 digest 时才叫 exit_reason），
      取值 ∈ {clear, resume, logout, prompt_input_exit, bypass_permissions_disabled, other}
职责：
  - 写 session-digest-latest.md（本次会话自动变更摘要；Claude 主体产出的变更摘要由 model 填，此处给 fallback）
  - flush events.jsonl 未落盘部分（当前 append 模式下实际不需要额外 flush，保留为占位兼容）
  - snapshot rollback 索引（配合 /core:rollback）
  - 重算 board.yaml 的 summary 块（调用 recompute_board_summary 模块）
  - 重算 skill-metrics.yaml（调用 recompute_skill_metrics 模块）

保持最小职责：hook 不做复杂文本生成，只做 deterministic 的 state 写入。
"""

import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 允许 import 同目录下的 recompute_board_summary 模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from recompute_board_summary import recompute_board_summary
except Exception:
    recompute_board_summary = None

try:
    from recompute_skill_metrics import recompute_skill_metrics
except Exception:
    recompute_skill_metrics = None

# 状态文件的锁 / 原子写 / 损坏隔离只有一份实现（见 _state_io.py 抬头）。
# 这个不做 None fallback——没有它就没有可用的退路，而它与本脚本同包分发。
from _state_io import load_activity, save_activity as _save_activity  # noqa: E402

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


# session_ended.reason 的合法枚举，与 .workframe-meta/event-schema.json 保持一致
# （validate 的 event_ts_and_reason_contract 对账两处）。
SESSION_END_REASONS = ("clear", "resume", "logout", "prompt_input_exit",
                       "bypass_permissions_disabled", "other")

PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
STATE_DIR = PROJECT_DIR / ".claude" / "workframe-state"
DIGEST_FILE = STATE_DIR / "session-digest-latest.md"
ROLLBACK_INDEX = STATE_DIR / "rollback-index.json"
ACTIVITY_FILE = STATE_DIR / "activity-state.json"
EVENTS_FILE = STATE_DIR / "events.jsonl"


def load_state():
    """字段全集与损坏处理见 `_state_io`（四个 hook 共用一份实现）。

    此前这里损坏时返回 `{}`——写回就把 session_counter / pending_maintenance 抹平了。
    """
    return load_activity(STATE_DIR)


def save_state(state):
    _save_activity(STATE_DIR, state)


def append_event(ev_type, **extra):
    if not EVENTS_FILE.parent.exists():
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ev = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "type": ev_type,
        **extra,
    }
    with EVENTS_FILE.open("a", encoding="utf-8", newline="") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def write_digest_skeleton(reason):
    """写 digest 骨架；真正的变更摘要由 Claude 在会话末尾或下次 SessionStart 主动调用 session-digest skill 填入。

    字段名 / section 名与 plugins/core/skills/session-digest/SKILL.md 输出格式严格对齐：
      - exit_reason（不是 reason）
      - session_counter（来自 activity-state.json）
      - Auto changes (T1/T2) / Pending maintenance (T3/T4) / Flags
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    state = load_state()
    session_counter = state.get("session_counter", 0)
    dormant_profile = state.get("dormant_profile", "normal")
    content = (
        "# Session Digest (latest)\n\n"
        f"- session_ended_at: {now_iso}\n"
        f"- session_counter: {session_counter}\n"
        f"- exit_reason: {reason}\n\n"
        "## Auto changes (T1/T2)\n"
        "- (填充：本次 session 内自动应用的 T1/T2 变更列表，由 session-digest skill 从 events.jsonl 重建)\n\n"
        "## Pending maintenance (T3/T4)\n"
        "- (填充：未处理的维护项；下次 session 启动展示)\n\n"
        "## Flags\n"
        f"- dormant_profile: {dormant_profile}\n"
    )
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    DIGEST_FILE.write_text(content, encoding="utf-8", newline="")


def _parse_iso_datetime(value):
    """容错解析 ISO-8601 / date-only 字符串为 timezone-aware datetime。

    支持：
      - "2026-04-15T10:30:00+00:00" / "...Z"   → aware
      - "2026-04-15T10:30:00"                  → 视为 UTC，补 tzinfo
      - "2026-04-15"                           → date-only，视为当日 00:00 UTC
      - 其他 / None / 解析失败                  → None
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _gc_rollback_index_inner():
    """GC 策略实现（被 snapshot_rollback_index 包裹 try/except）。

    - 保留所有未验证 entry（对应提案 verified=null）
    - 已验证且 applied_at < 30 天 → 保留
    - 已验证且 applied_at ≥ 30 天 → 丢弃
    - 已验证但 applied_at 缺失/非法 → **保留**，交给总量上限兜底
    - 总量上限 100 条，超出时**只丢最旧的已验证 entry**；已验证的丢光仍超限，
      才按时间截断未验证的

    末两条是 2026-08-18 外部复核查出的实现与声明不符：
    - 旧实现对 kept 无差别排序截断，超过 100 条时**最旧的未验证 entry 也会被删**，
      与本 docstring 第一行直接矛盾。未验证意味着「这次自动变更还没被确认是对的」，
      恰恰是最可能需要回滚的一批，不能因为总量超限就连坐丢掉。
    - 旧实现里 `applied_at` 解析失败的已验证 entry 会落进丢弃分支，即**时间戳一脏
      就立刻失去回滚能力**，而声明说的是「30 天后清理」。
    """
    if not ROLLBACK_INDEX.exists():
        skeleton = {
            "__schema__": "workframe.rollback-index.v1",
            "entries": [],
        }
        ROLLBACK_INDEX.write_text(json.dumps(skeleton, indent=2), encoding="utf-8", newline="")
        return

    try:
        data = json.loads(ROLLBACK_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return  # 索引损坏不强行重写，留给用户/audit 处理

    entries = data.get("entries") or []
    if not isinstance(entries, list):
        return

    applied_dir = PROJECT_DIR / "projects" / "proposals" / "applied"

    def _verified_status(proposal_id):
        if not applied_dir.exists() or not proposal_id:
            return None
        for ext in (".yaml", ".yml"):
            path = applied_dir / f"{proposal_id}{ext}"
            if not path.exists():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                return None
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("verified:"):
                    v = s.split(":", 1)[1].strip().lower()
                    if v in ("true", "yes"):
                        return True
                    if v in ("false", "no"):
                        return False
                    return None
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    # 带上 verified 一起留存：下面的总量截断要按它区分优先级，重算一次就要重读一遍磁盘
    kept = []                                     # [(entry, verified)]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        applied_at = _parse_iso_datetime(entry.get("applied_at"))
        verified = _verified_status(entry.get("proposal_id"))

        if verified is None:                      # 未验证：一律保留
            kept.append((entry, verified))
            continue
        # 已验证：时间戳缺失或非法时**保留**——数据脏不该直接等于失去回滚能力，
        # 交给下面的总量上限兜底
        if applied_at is None or applied_at >= cutoff:
            kept.append((entry, verified))
            continue
        # 已验证且确实超过 30 天 → 丢弃

    if len(kept) > 100:
        def _sort_key(item):
            ts = _parse_iso_datetime(item[0].get("applied_at"))
            # 时间戳不可知时当作**最新**而非最旧：上面刚决定「数据脏不该直接失去回滚
            # 能力」，若在这里把它们排到队首，总量一超限就首当其冲被丢，等于绕个弯
            # 又把能力拿走了。真正该先丢的是「确知很旧」的那些。
            return ts or datetime.max.replace(tzinfo=timezone.utc)

        kept.sort(key=_sort_key)                  # 时间正序，末尾最新
        over = len(kept) - 100
        pruned, dropped = [], 0
        for item in kept:
            # 只从**已验证**的里丢，且从最旧的开始
            if dropped < over and item[1] is not None:
                dropped += 1
                continue
            pruned.append(item)
        # 未验证条目本身就超过 100 条时，才按时间截断它们（保留最新的一批）
        if len(pruned) > 100:
            pruned = pruned[-100:]
        kept = pruned

    data["entries"] = [entry for entry, _ in kept]
    if "__schema__" not in data:
        data["__schema__"] = "workframe.rollback-index.v1"
    ROLLBACK_INDEX.write_text(json.dumps(data, indent=2), encoding="utf-8", newline="")


def snapshot_rollback_index():
    """生成 rollback 索引骨架 + GC；GC 失败不阻塞 SessionEnd 整体流程。

    任何异常都不能让 SessionEnd hook 失败——session_ended / summary_recomputed /
    skill_metrics_recomputed 等关键事件必须能写入。
    """
    try:
        _gc_rollback_index_inner()
    except Exception as e:
        print(
            f"[warn] rollback-index GC failed (non-fatal): {type(e).__name__}: {e}",
            file=sys.stderr,
        )


def _stage(name, fn, *args, **kwargs):
    """跑一个收尾阶段：失败只记一笔，绝不阻断后面的阶段。

    SessionEnd 是「最后一次把状态落下去」的机会。此前只有 rollback GC 做了隔离，
    digest 骨架与 session_ended 事件是裸奔的——状态目录被占用或不可写，第一步一抛，
    后面的结束事件、summary 重算、skill-metrics 重算、last_digest_at 全部陪葬，
    而整轮会话看起来只是「安静地结束了」。
    """
    try:
        fn(*args, **kwargs)
        return True
    except Exception as e:
        print(f"[warn] SessionEnd 阶段「{name}」失败（不阻断后续）: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return False


def main():
    # 官方 SessionEnd stdin JSON 的字段名是 `reason`（核实于 code.claude.com/docs/en/hooks.md，
    # 取值：clear / resume / logout / prompt_input_exit / bypass_permissions_disabled / other）
    # v0.2.0 误用 exit_reason 导致永远拿到 unknown，v0.2.1 修正。
    # 回退值必须落在 schema 枚举内：`unknown` 是自造值，按枚举精确匹配的消费方会漏掉它。
    # 官方枚举里 `other` 正是为「说不上是哪种」准备的兜底档。
    reason = "other"
    try:
        # Windows 下 sys.stdin 默认走 locale codec；强制 utf-8 解码跨平台一致
        raw = ""
        if not sys.stdin.isatty():
            raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        if raw:
            data = json.loads(raw)
            # **白名单校验**，不是只兜缺失：schema 的 reason 是固定枚举，而这里收的是
            # 外部输入。CC 将来新增退出原因、或 stdin 传了意外值时，原样写入就会造出
            # 枚举外的事件，严格按枚举匹配的消费方全部漏掉（实测 {"reason":"bogus"}
            # 会被原样写进 events.jsonl）。落在枚举外一律归 other——它正是为
            # 「说不上是哪种」准备的档。
            raw_reason = data.get("reason")
            reason = raw_reason if raw_reason in SESSION_END_REASONS else "other"
    except Exception:
        pass

    digest_ok = _stage("digest 骨架", write_digest_skeleton, reason)
    _stage("rollback 索引", snapshot_rollback_index)
    _stage("session_ended 事件", append_event, "session_ended", reason=reason)

    # 自动重算 board.yaml 的 summary 块（v0.2.2 起从手工重算改为 SessionEnd 自动 + drift check 兜底）
    if recompute_board_summary is not None:
        try:
            recompute_board_summary()
        except Exception as e:
            # recompute 内部对 board_not_found / summary_block_not_found 已用 status=skipped event 处理；
            # 这里捕获意料之外的异常（YAML 解析失败 / 磁盘满 / 权限问题 等），写 status=error 留痕，
            # 让用户/audit 能定位失败原因，而不是 silent 吞掉
            try:
                append_event(
                    "summary_recomputed",
                    status="error",
                    reason=f"exception during recompute: {type(e).__name__}: {str(e)[:200]}",
                )
            except Exception:
                # 二级兜底：事件写入也失败时不让 hook 整体失败影响 session 退出
                pass

    # 自动重算 skill-metrics.yaml（v0.2.x 起从 Librarian 文档步骤落为 deterministic 脚本）
    if recompute_skill_metrics is not None:
        try:
            recompute_skill_metrics()
        except Exception as e:
            try:
                append_event(
                    "skill_metrics_recomputed",
                    status="error",
                    reason=f"exception during recompute: {type(e).__name__}: {str(e)[:200]}",
                )
            except Exception:
                pass

    # 更新 activity-state.last_digest_at
    def _touch_digest_at():
        state = load_state()
        state["last_digest_at"] = datetime.now(timezone.utc).isoformat()
        save_state(state)

    # 只在 digest 真写成功后才推进 last_digest_at——否则这个字段会宣称「刚生成过摘要」
    # 而磁盘上根本没有那份摘要
    if digest_ok:
        _stage("last_digest_at", _touch_digest_at)
    else:
        print("[warn] digest 未写成，last_digest_at 保持原值", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
