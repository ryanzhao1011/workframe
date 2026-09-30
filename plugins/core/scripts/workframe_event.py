#!/usr/bin/env python3
"""模型手写事件的机械写法收口点（`bin/workframe-event` 的实现）。

**承接哪几类**：`skill_used` / `user_correction` / `task_blocked`——事件注册表里
`protocol_expected` 的那三个，由模型照注入片的收尾协议 / 纠正处理追加；外加
`discipline_spotcheck`（`model_mediated`），只在用户显式 `/core:audit --record` 时由模型追加。
手拼 JSON 有四个已实证的翻车点，本模块一次做掉（第 3、4 条只对应用到的那一类）：

  1. **ts 口径**：必须 UTC + 秒级。手写实测出过 `+08:00` 与微秒两种变体，而消费方按
     字符串排序与窗口过滤——一条真实早于 30 天窗口 1 分钟的事件被判成「窗口内」。
  2. **反斜杠转义**：字段值里的 `\\b` `\\s` 或 Windows 路径写进 JSON 必须写成 `\\\\`。
     `\\s` 这类会让**整行**解析失败，严格解析的消费方读到该行直接抛异常。
  3. **entry_key 归一**：`<scope>:<YYYY-MM-DD>:<正确信息前 20 字>`，且顺序是**先删
     空白再取前 20 字**（颠倒会算出另一个 key，跨 producer 的 supersede 会 miss）。
     实现**复用** `maintenance_workorder._normalize_key_fragment`，不另写一份——这条
     规则已经因为两处手写漂过一次。
  4. **task_blocked 去重**：schema 不含 session_id，dedup 只以 `task_id` 为准；协议要求
     append 前先扫 events.jsonl，扫漏就把 problem 加权分算两遍。

`harness` 由 `--harness` 给；**不给就显式写 `unknown`，不省略**——省略在 schema 里等于
宣称 `cc`（存量兼容口径），把「不知道」写成「确定是 CC」是更坏的假话。

**能力边界（现在就写清楚，别等它变成暗坑）**：

  - 本命令**不判断该不该写**（该不该记一条 skill_used、这条纠正是不是持久层认知），
    那是 agent-protocols / correction-detection 的判据，模型仍要自己过一遍。
  - `--session-id` 不给时按三级兜底取：显式参数 > `_harness.session_id()` > 会话标记文件。
    **中间那一级在 CC 门下可用**（`CLAUDE_CODE_SESSION_ID`），所以 CC 侧的 `skill_used`
    带得上 session_id；**Codex 门下那个变量不存在**，落到第三级——会话标记文件由
    `session-start-prep.py --harness codex` 写、`session-end-flush.py` 删（实测：不带
    `--session-id` 跑本命令，落行的 `session_id` 等于标记里的线程号）。**恰好一个标记才采信**：
    同一项目下有两个候选（上一个会话没跑到 SessionEnd、标记还没被 7 天清理）时省略
    `session_id`，消费方回退到按 ts 分桶、per-session cap 对那些行失效。
  - 本模块不写 memory-index sidecar，也不动 MEMORY.md：它只管事件流那一半。纠正类条目的
    sidecar 与 MEMORY 写入仍按 correction-detection 第 3、4 步由模型完成。
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# 同目录公共模块：harness、运行态目录、追加写各只有一份实现
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from _harness import UNKNOWN, harness as _harness_of  # noqa: E402
from _harness import project_dir as _project_dir  # noqa: E402
from _harness import session_id as _harness_session_id  # noqa: E402
from _state_io import append_line, event_json, state_dir_of  # noqa: E402

EVENT_TYPES = {
    "skill-used": "skill_used",
    "user-correction": "user_correction",
    "task-blocked": "task_blocked",
    "discipline-spotcheck": "discipline_spotcheck",
}

# `discipline-spotcheck` 收的判据 id。**判据表在 `skills/audit/SKILL.md`「纪律抽查」一节，本常量是它
# 的机器侧副本**：命令运行期不去解析 markdown（解析失败会让记录整个写不进去，且 skill 正文的表格形态
# 不该成为命令的运行期依赖），两处是否相等由 validate `discipline_spotcheck_contract` 对账。
SPOTCHECK_CRITERIA = ("S1", "S2")


def _now_iso():
    """事件 ts 的唯一形态：UTC + 秒级，与 event-schema 的 TS FORMAT 同口径。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def normalize_key_fragment(summary):
    """entry_key 第三段的归一，**复用** `maintenance_workorder` 里那一份，不另写。

    延迟 import 而不是模块顶部 import：那个模块在 import 期重绑 sys.stdout/stderr 并解析
    项目根（`bin/workframe-maintenance` 的注释里记着同一条理由）。只有真要算 key 时才付
    这个代价；算不出来就让 ImportError 抛出去——本命令的价值就是把 key 算对，静默退化成
    「自己拍一个」正是它要防的东西。

    **本函数被调用后 `sys.stdout` 会换成那个模块新建的 wrapper，这是有意不去还原的**：
    还原就意味着新 wrapper 无人引用、被 GC、`__del__` 关掉共用的底层 buffer，之后任何
    print 都是 `I/O operation on closed file`（本函数首版实测踩到）。不还原时旧的那个由
    `sys.__stdout__` 持着，两层都活着。`main()` 用 `reconfigure` 而不是再包一层，正是
    为了不在这里制造第三层。
    """
    from maintenance_workorder import _normalize_key_fragment
    return _normalize_key_fragment(summary)


def session_marker_id(state_dir, harness_name):
    """会话标记文件里的 session_id；没有（CC 门恒无——只有 Codex 门写它）返回空串。

    文件名形态 `current-session-<harness>-<session_id>.json`。**按 session_id 命名而不是
    按 harness 单文件**：同一扇门同时开两个会话（两个 CC 窗口是常态）时单文件会被后启动者
    覆盖，先启动那个会话的模型就会拿到别人的 session_id，per-session cap 归错桶且无从发现。
    所以这里只在**恰好一个**候选时采信；多于一个说明分不清是哪个会话，宁可省略 session_id
    （消费方回退到按 ts 分桶）也不猜。
    """
    prefix = f"current-session-{harness_name}-"
    try:
        cands = sorted(Path(state_dir).glob(prefix + "*.json"))
    except OSError:
        return ""
    if len(cands) != 1:
        return ""
    return cands[0].stem[len(prefix):]


def task_blocked_exists(events_file, task_id):
    """events.jsonl 里是否已有同 `task_id` 的 `task_blocked`。

    判定按「**解析出来的**那一行同时命中 type 与 task_id」，不是子串匹配——子串会把
    summary 里提到该任务号的别的事件误判成已存在，从而静默跳过一条真该记的阻塞。
    解析不了的行（历史半行）跳过：本函数只回答「有没有」，不负责修文件。
    """
    events_file = Path(events_file)
    if not events_file.exists():
        return False
    try:
        raw = events_file.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return False
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if (isinstance(ev, dict) and ev.get("type") == "task_blocked"
                and ev.get("task_id") == task_id):
            return True
    return False


def build_event(args):
    """按子命令拼出事件字典（不含 `harness`——那是 `event_json` 唯一的落笔处）。"""
    ev = {"ts": _now_iso(), "type": args.event_type}
    if args.event_type == "skill_used":
        ev["skill"] = args.skill
        ev["role"] = args.role
        ev["success"] = args.success
    elif args.event_type == "user_correction":
        ev["scope"] = args.scope
        # scope=main 是主 Claude 层纠正、落 auto-memory：无 sidecar ⇒ 省略 entry_key，
        # 且 correction-detection 明写该情形一并省略 role。这两条不是风格选择。
        if args.scope != "main":
            if args.role:
                ev["role"] = args.role
            key = args.entry_key
            if not key and args.summary:
                key = f"{args.scope}:{_today()}:{normalize_key_fragment(args.summary)}"
            if key:
                ev["entry_key"] = key
        ev["summary"] = (args.summary or "")[:80]
        ev["source"] = args.source
    elif args.event_type == "task_blocked":
        ev["task_id"] = args.task_id
        ev["role"] = args.role
    elif args.event_type == "discipline_spotcheck":
        ev["sessions_sampled"] = args.sessions_sampled
        ev["criteria_version"] = args.criteria_version
        ev["per_criterion"] = {cid: {"applicable": a, "violations": v}
                               for cid, a, v in args.criterion}
        ev["role"] = args.role
    else:  # pragma: no cover —— 子命令表与 EVENT_TYPES 同源，走不到
        raise ValueError(f"未知事件类型 {args.event_type!r}")
    return ev


def _bool(value):
    s = str(value).strip().lower()
    if s in ("true", "1", "yes", "y"):
        return True
    if s in ("false", "0", "no", "n"):
        return False
    raise argparse.ArgumentTypeError(f"要 true / false，收到 {value!r}")


def _door_counts(value):
    """`cc=3,codex=0` → `{"cc": 3, "codex": 0}`。只收这两扇门、非负整数；取不到样本的门不写。"""
    out = {}
    for part in str(value).split(","):
        door, sep, num = part.strip().partition("=")
        if not sep or door not in ("cc", "codex") or not num.isdigit() or door in out:
            raise argparse.ArgumentTypeError(f"要 cc=<n>,codex=<m> 形态，收到 {value!r}")
        out[door] = int(num)
    return out


def _criterion(value):
    """`S1=3/1` → `("S1", 3, 1)`：判据 id = 适用次数 / 违规次数，违规不得多于适用。"""
    cid, sep, rest = str(value).strip().partition("=")
    a, slash, v = rest.partition("/")
    if (not sep or not slash or not cid.strip() or not a.isdigit() or not v.isdigit()
            or int(v) > int(a)):
        raise argparse.ArgumentTypeError(f"要 <id>=<适用次数>/<违规次数>（违规 ≤ 适用），收到 {value!r}")
    if cid.strip() not in SPOTCHECK_CRITERIA:
        raise argparse.ArgumentTypeError(f"判据 id {cid.strip()!r} 不在判据表里（只收 "
                                         f"{' / '.join(SPOTCHECK_CRITERIA)}）")
    return cid.strip(), int(a), int(v)


def _add_common(sub):
    sub.add_argument("--session-id", dest="session_id", default=None,
                     help="会话号；不给则读会话标记文件，读不到就省略该字段")
    sub.add_argument("--project", default=None,
                     help="项目根；缺省按 _harness.project_dir 解析")
    sub.add_argument("--dry-run", dest="dry_run", action="store_true",
                     help="只打印将要写入的那一行，不落盘")
    # `--harness` 的值由 `_harness.harness()` 直接读 sys.argv；这里挂上只为 argparse 不报错
    sub.add_argument("--harness", default=None, help="当前是哪扇门（cc / codex）")
    return sub


def build_parser():
    ap = argparse.ArgumentParser(
        prog="workframe-event",
        description="把模型手写的三类事件按统一口径写进 events.jsonl",
    )
    subs = ap.add_subparsers(dest="cmd", required=True)

    p1 = _add_common(subs.add_parser("skill-used", help="agent wrap-up Step 1 的 skill_used"))
    p1.add_argument("--skill", required=True)
    p1.add_argument("--role", required=True, help="subagent 填角色名；主 Claude 直做填 main")
    p1.add_argument("--success", type=_bool, default=True,
                    help="产出了承诺的交付物 true；未产出 / 被用户当场否定 false")

    p2 = _add_common(subs.add_parser("user-correction",
                                     help="correction-detection 的 user_correction"))
    p2.add_argument("--scope", required=True, help="shared | <role> | main")
    p2.add_argument("--role", default=None, help="scope != shared 时的角色名；scope=main 时省略")
    p2.add_argument("--summary", default=None, help="正确信息摘要（入库截前 80 字）")
    p2.add_argument("--entry-key", dest="entry_key", default=None,
                    help="sidecar key；不给则由 scope + 日期 + summary 前 20 字现算")
    p2.add_argument("--source", default="[纠正]")

    p3 = _add_common(subs.add_parser("task-blocked", help="任务转 blocked 的 task_blocked"))
    p3.add_argument("--task-id", dest="task_id", required=True)
    p3.add_argument("--role", required=True)

    p4 = _add_common(subs.add_parser("discipline-spotcheck",
                                     help="/core:audit --record 的纪律抽查结果（不带 --record 不写）"))
    p4.add_argument("--sessions-sampled", dest="sessions_sampled", type=_door_counts,
                    required=True, help="各门抽到的会话数，如 cc=3,codex=0")
    p4.add_argument("--criteria-version", dest="criteria_version", required=True,
                    help="判据表版本（audit SKILL.md 纪律抽查段写的那个）")
    p4.add_argument("--criterion", action="append", type=_criterion, required=True,
                    help="每条判据一次：<id>=<适用次数>/<违规次数>，如 S1=4/1")
    p4.add_argument("--role", default="main", help="执行抽查的一方；/core:audit 在主会话跑，填 main")
    return ap


def main(argv=None):
    # **`reconfigure` 而不是本仓别处那种「再包一层 TextIOWrapper」**：本模块可能在同一
    # 进程里再 import `maintenance_workorder`，而那个模块 import 期会拿 `sys.stdout.buffer`
    # 新建一层。两层包装里先被顶掉的那层一旦无人引用就会被 GC，其 `__del__` 关掉共用的底层
    # buffer，之后任何 print 直接抛 `I/O operation on closed file`（实测）。
    # `reconfigure` 原地改编码、不新建对象，从源头上没有这个竞争。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = build_parser()
    args = ap.parse_args(argv)
    args.event_type = EVENT_TYPES[args.cmd]
    if args.event_type == "discipline_spotcheck":
        ids = [cid for cid, _, _ in args.criterion]
        dup = sorted({cid for cid in ids if ids.count(cid) > 1})
        if dup:
            # 后一次会静默覆盖前一次，等于替用户挑了一个数；在任何写入之前拒绝（argparse 的 exit 2）
            ap.error(f"--criterion 的判据 id 重复：{', '.join(dup)}——每个 id 只写一次")

    harness_name = _harness_of() or UNKNOWN   # 读 sys.argv 的 --harness；没带就是 unknown
    project = Path(args.project).resolve() if args.project else _project_dir()
    events_file = state_dir_of(project) / "events.jsonl"

    if args.event_type == "task_blocked" and task_blocked_exists(events_file, args.task_id):
        print(json.dumps({"status": "skipped", "reason": "duplicate_task_blocked",
                          "task_id": args.task_id}, ensure_ascii=False))
        return 0

    event = build_event(args)
    # session_id 只对 skill_used 有消费方（check-iteration-trigger 的 per-session cap）；
    # 另两类的 schema 里没有这个字段，塞进去只会造出 schema 外的键。
    if args.event_type == "skill_used":
        # 三级兜底，顺序即优先级：显式参数 > harness 自己的会话号 > 会话标记文件。
        # 中间那一级是 CC 门下**今天就能拿到**的（`CLAUDE_CODE_SESSION_ID` 注进 Bash
        # 子进程，与 hooks stdin 的 `session_id` 同值）；漏接它等于让 per-session cap
        # 在唯一有数据的那扇门上也失效。最后一级见 `session_marker_id` 的能力边界。
        sid = (args.session_id or _harness_session_id() or
               session_marker_id(state_dir_of(project), harness_name))
        if sid:
            event["session_id"] = sid

    line = event_json(event, harness=harness_name)
    if args.dry_run:
        print(line)
        return 0

    written = append_line(events_file, line)
    print(json.dumps({"status": "ok" if written is True else "spilled",
                      "file": str(events_file), "line": json.loads(line)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # exit-audited: 正常路径恒 0；用法错误由 argparse 自己 exit 2——这是 CLI 命令不是 hook，
    # 非零不会阻断任何会话
    sys.exit(main())
