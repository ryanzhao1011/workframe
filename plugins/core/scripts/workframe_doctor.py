#!/usr/bin/env python3
"""
workframe-doctor — 消费项目运行态体检（v0.4 G1#4）

与其他工具的分工：
  - tools/validate.py     检查框架仓自身的静态资产（contributor 工具）
  - workframe-doctor      检查消费项目的运行态数据健康（本脚本，代码确定性、按需跑）
  - /core:audit           模型读状态做人话汇总（展示层，不做健康判定）
  - hooks                 每轮/每会话的轻量信号巡逻

只诊断不治疗：报告归 doctor，动手归 /core:maintenance-review（读写分离，同 audit 边界）。
唯一例外——`--group install` 跑完会把本次验收结果记进 setup-state.json 的 acceptance 键
（那是本工具自己的运行记录，不是「治疗」；理由见 SETUP_SELF_RECORDED_STEPS 注释）。

两组检查：
  - `install`  安装/环境完整性——装没装对（launcher 落盘验收 + 首个会话运行时验收共用）
  - `runtime`  运行态数据健康——跑起来之后数据健不健康

用法：
    workframe-doctor                              # 全量体检，文本报告
    workframe-doctor --group install              # 只跑第 0 组
    workframe-doctor --project <path> --group install   # 从任意目录验收指定项目
    workframe-doctor --json                       # 机器可读输出
    workframe-doctor --list                       # 输出分组与保养项目录 + 阈值常量

依赖重启才成立的项（hook 活性）在重启前自降为 info，不设 --stage 参数：
同一条命令在重启前后跑出不同结果，与既有「空态不误报」原则一致。

退出码：存在 error 级发现 → 1；目标目录非法 → 2；否则 0。
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _force_utf8_io():
    """把 stdout 与 stderr 都包成 UTF-8——**只在 CLI 入口调用，不在 import 时**。

    模块级包装是个真陷阱：调用方（如 session-start-prep）自己也包了一层，两个 wrapper
    抢同一个 buffer，先被回收的那个把 buffer 关掉，另一个随即抛
    `I/O operation on closed file`。`maintenance_workorder.py` 当年正是为绕开它才改用子进程
    调 doctor；第 0 组要求可被 import 复用，于是从源头修掉——本模块 import 无副作用。
    """
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---- 阈值常量（v1 拍板：不建独立 config 文件，集中此处，--list 展示）----
NOTES_BACKLOG_LINES = 80      # 单个 notes.md 内容行超过此值视为积压（阈值与 check-iteration-trigger.py 一致）
NOTES_STALE_DAYS = 30         # 距上次 memory_promoted 超过此天数且 notes 内容行 > 5 视为陈化
# 陈化口径与 trigger **同阈值、不同取数**，这是有意的（勿"统一"）：
#   doctor  = 按角色取该角色的最近提升时间，且「从未提升过」也算陈化 —— 诊断工具，人主动跑，
#             宁可多报一条让人自己判断
#   trigger = 全局取最近一次提升，且「从未提升过」不报 —— 它产生 pending_maintenance 会打扰
#             用户，按角色分会因事件稀疏而失真（见该脚本内注释）
# 同一份数据两处结论不同属正常，别把其中一个改成另一个。
ROLE_MEMORY_MAX_CHARS = 8000     # 单角色 MEMORY.md 字符预算（≈5.6k tok；行数检查已退役——单行超长可钻空，字符才反映注入开销）
SHARED_MEMORY_MAX_CHARS = 4000   # shared MEMORY.md 字符预算（SubagentStart 注入所有 agent，预算更紧）
AUTO_MEMORY_ENTRY_MAX_CHARS = 3000   # auto-memory 单条字符预算（对应 CLAUDE.md 写入纪律 ≤2k token）
AUTO_MEMORY_INDEX_MAX_LINES = 80     # auto-memory MEMORY.md 索引行数上限（条数管索引成本）
TOKEN_PER_CHAR = 0.7          # CJK 文本 token 粗估系数（仅展示用）
PROTECTED_WINDOW_DAYS = 14    # 受保护资产对账回看窗口（天）

PLUGIN_ROOT = Path(__file__).resolve().parent.parent          # plugins/core
EVENT_SCHEMA_FILE = PLUGIN_ROOT / ".workframe-meta" / "event-schema.json"


class Paths:
    """一个体检目标的全部路径。

    2026-08-10：原为 import 时求值的模块级常量（目录写死 env/cwd）。launcher 要从**任意目录**
    对**指定项目**跑落盘验收，`session-start-prep` 又要 import 复用第 0 组——两者都要求
    目标目录是参数而不是全局。全部下沉到本对象，经 ctx 传给每个检查。
    """

    def __init__(self, project_dir):
        self.project = Path(project_dir).resolve()
        self.state = self.project / ".claude" / "workframe-state"
        self.events = self.state / "events.jsonl"
        self.sidecar = self.state / "memory-index.json"
        self.setup_state = self.state / "setup-state.json"
        self.memory = self.project / ".claude" / "agent-memory"
        self.proposals = self.project / "projects" / "proposals"
        self.changelog = self.project / "projects" / "changelog.md"
        self.config = self.project / ".workframe-config.json"
        self.claude_md = self.project / "CLAUDE.md"
        self.settings = self.project / ".claude" / "settings.json"
        self.rules_mirror = self.project / ".claude" / "rules" / "workframe" / "core"
        self.gitignore = self.project / ".gitignore"


def default_project_dir():
    return Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()


ACTIVE_TIERS = ("hook_deterministic", "protocol_expected", "model_mediated")
PROTECTED_PATHS = ["CLAUDE.md", ".claude/rules/local", ".claude/skills",
                   ".claude/settings.json", ".claude/settings.local.json"]

LEVEL_ORDER = {"ok": 0, "info": 1, "warn": 2, "error": 3}
LEVEL_MARK = {"ok": "✓", "info": "i", "warn": "!", "error": "✗"}


def _count_content_lines(text):
    """与 check-iteration-trigger.py 同口径：排除空行/标题/引用/分隔线/注释/占位符。"""
    n = 0
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith(("#", ">", "---", "<!--", "_（", "_(")):
            continue
        n += 1
    return n


def _discover_scopes(P):
    """动态发现合法 scope：agent-memory 子目录名（含 shared）。"""
    if not P.memory.is_dir():
        return []
    return sorted(d.name for d in P.memory.iterdir() if d.is_dir())


def _load_events(P):
    """单次解析事件流，供多个检查复用。返回 (events, malformed_lines, total_lines)。"""
    events, malformed, total = [], [], 0
    if not P.events.exists():
        return events, malformed, total
    for lineno, line in enumerate(
            P.events.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        total += 1
        try:
            ev = json.loads(s)
        except Exception:
            malformed.append(lineno)
            continue
        if not isinstance(ev, dict):
            # `[]` / `42` / `"x"` 是合法 JSON 但不是事件——此前既不计入 events 也不计入
            # malformed，于是 doctor 报「N 行全部可解析」，坏行凭空消失
            malformed.append(lineno)
            continue
        if not ev.get("__schema__"):
            events.append(ev)
    return events, malformed, total


def _import_scaffold():
    """按需 import 同目录的 project_scaffold（保持本模块 import 时无副作用）。"""
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import project_scaffold
    return project_scaffold


def _gitignore_covers(text, entry):
    """委托给 project_scaffold.gitignore_covers——规则解析只留一份实现。

    取不到（极端安装残缺）时退回子串匹配：宁可宽松，也不能让体检本身崩掉。
    """
    try:
        return _import_scaffold().gitignore_covers(text, entry)
    except Exception:
        return entry in text


def _parse_ts(value):
    """把事件 ts 解析成 aware datetime（UTC）；无法解析返回 None。

    **时间比较一律先解析再比，禁止字符串比较。** producer 侧虽已统一为 UTC+秒级，
    但那只管新事件：历史事件是混合格式（实测某项目并存 offset/秒、offset/微秒、
    Z/秒、Z/微秒 四种），模型手写的事件更不受控。字符串比较跨时区必然错——
    实测一个真实早于窗口 1 分钟、但写成 `+08:00` 的事件，被判成「在近 30 天窗口内」。
    顺带兜住脏数据：`ts: null` / 数字 / 缺字段时返回 None，不再抛
    `TypeError: '>' not supported between NoneType and str`。
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _days_since_date(date_str):
    try:
        then = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
        return (datetime.now().date() - then).days
    except Exception:
        return None


# ---------------------------------------------------------------- checks

def check_events_parse(ctx):
    """事件流可解析率：逐行 JSON 解析，坏行会污染所有严格解析的消费方。

    附带 main-led 观测（2026-08-16 增）：近 30 天 `skill_used` 按 role 的分布。
    **参考指标，不设阈值、不报 warn**——主 Claude 直做占比高低本身无所谓对错，
    它只回答「这个项目现在是 main-led 还是 role-led 在跑」，供人判断分流机制是否
    如预期生效。
    """
    P = ctx["paths"]
    out = []
    events, malformed, total = ctx["events"], ctx["malformed"], ctx["events_total"]
    if not P.events.exists():
        return [("info", "events.jsonl 不存在（新项目·尚无运行数据）")]
    # main-led 观测挂在 ok 消息尾部，**不单独成条**——它是参考信息不是判定，
    # 单列一条 info 会把整个健康检查项从 ok 降级成 info，读报告的人会以为出了事
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    roles = {}
    for ev in events:
        ts = _parse_ts(ev.get("ts"))
        if ev.get("type") != "skill_used" or ts is None or ts < cutoff:
            continue
        key = ev.get("role") if isinstance(ev.get("role"), str) and ev.get("role") else "unspecified"
        roles[key] = roles.get(key, 0) + 1
    # skill_invoked 是 hook_deterministic 层的「被调起」计数（代码保证），与 skill_used
    # 的「用了并产出了什么」（模型自评）**并存不相加**——同一次调用两条都会有。
    # 两个数差得多说明其中一侧在漏：invoked 远大于 used → agent 收尾常漏写 skill_used；
    # used 大于 invoked → 有不经 hook 的调用路径（或 hook 没装上）。
    invoked = sum(1 for ev in events
                  if ev.get("type") == "skill_invoked"
                  and (_parse_ts(ev.get("ts")) or cutoff - timedelta(days=1)) >= cutoff)
    trend = ""
    if roles:
        tot = sum(roles.values())
        dist = "、".join(f"{k}={v}" for k, v in sorted(roles.items(), key=lambda kv: -kv[1]))
        trend = (f"；近 30 天 skill 记账 {tot} 次、主 Claude 直做占 "
                 f"{roles.get('main', 0) * 100 // tot}%（{dist}，参考值不设阈值，"
                 f"直接 Read 不经 skill 的工作不在覆盖范围）")
    if invoked or roles:
        trend += f"；skill_invoked（代码保证的调起数，不与上一项相加）{invoked} 次"

    if malformed:
        head = ", ".join(str(n) for n in malformed[:5])
        out.append(("error", f"{len(malformed)}/{total} 行解析失败（行号: {head}{'…' if len(malformed) > 5 else ''}）"))
    else:
        out.append(("ok", f"{total} 行全部可解析（有效事件 {len(events)} 条）{trend}"))

    # 「可解析」≠「合规」：ts 为 null / type 不在 schema 里的事件能被 json.loads 解析，
    # 却会被所有按窗口过滤的消费方静默丢弃，而本项此前只报「N 行全部可解析」。
    # 单列一条 finding，不改上面那条的语义。
    bad_ts = sum(1 for ev in events if ev.get("ts") is not None and _parse_ts(ev.get("ts")) is None)
    bad_ts += sum(1 for ev in events if "ts" not in ev or ev.get("ts") is None)
    known = set()
    if EVENT_SCHEMA_FILE.exists():
        try:
            known = set(json.loads(EVENT_SCHEMA_FILE.read_text(encoding="utf-8")).get("events", {}))
        except Exception:
            known = set()
    unknown_types = sorted({ev.get("type") for ev in events
                            if known and ev.get("type") and ev.get("type") not in known})
    if bad_ts or unknown_types:
        bits = []
        if bad_ts:
            bits.append(f"{bad_ts} 条 ts 缺失/不可解析（会被窗口过滤静默丢弃）")
        if unknown_types:
            bits.append(f"{len(unknown_types)} 类事件不在 schema 中: "
                        + "、".join(unknown_types[:5]))
        out.append(("warn", "事件可解析但不合 schema：" + "；".join(bits)))
    return out


def check_sidecar_health(ctx):
    """sidecar schema + scope 枚举 + key/scope 一致性 + 疑似重复 key。"""
    P = ctx["paths"]
    if not P.sidecar.exists():
        return [("info", "memory-index.json 不存在（新项目·尚无运行数据）")]
    try:
        idx = json.loads(P.sidecar.read_text(encoding="utf-8"))
    except Exception as e:
        return [("error", f"memory-index.json 解析失败: {e}")]
    if not isinstance(idx, dict):
        return [("error", f"memory-index.json 顶层不是 object（实际 {type(idx).__name__}）——"
                          f"结构损坏，promotion 记账会失准")]
    entries = idx.get("entries", {})
    if not isinstance(entries, dict):
        # `entries: []` 此前会被 `if not entries` 当成「空 sidecar」报绿，
        # 非空 list 则要等到 .items() 抛异常才暴露
        return [("error", f"memory-index.json 的 entries 不是 object"
                          f"（实际 {type(entries).__name__}）——结构损坏，不是「空索引」")]
    out = []
    if idx.get("__schema__") != "workframe.memory-index.v2":
        out.append(("warn", f"__schema__={idx.get('__schema__')!r}（应为 workframe.memory-index.v2；"
                    "v1 含已废除的 confidence 数字打分字段，需迁移为 provenance 枚举）"))
    if not entries:
        return out or [("ok", "sidecar 为空（尚无提升记录）")]
    scopes = set(ctx["scopes"])
    required = ("scope", "created_at", "provenance", "protected", "source")
    # 来源类型四选一（归类不打分）：模糊值/数字残留在此被机器抓出
    provenance_enum = ("user-decree", "user-confirmed", "inferred", "external")
    # source 与 provenance 是两件事：前者答「这条从哪来」，后者答「可靠性怎么归类」。
    # provenance 一直有闸而 source 没有，于是同一字段混着两套语义在用——
    # 「谁做的」（librarian-promoted）与「从哪来」（notes.md / auto-memory）。
    # 枚举纳入**实际在用的全部值**：消费项目里 librarian-promoted 有 24 条、
    # auto-memory 13 条、[纠正] 11 条，都是合法历史，不能因为口径统一就把它们判违规
    # （同 entry_key 改序时「历史不回改」的先例）。新写入统一走「从哪来」口径，
    # librarian-promoted 只读不新写——真正的自造值（如 user-explicit-project-fact）
    # 仍会被本闸抓出来。
    source_enum = ("[纠正]", "notes.md", "shared/notes.md", "auto-memory",
                   "librarian-promoted", "manual backfill")
    bad_scope, mismatch, missing, bad_prov, bad_src = [], [], [], [], []
    for key, e in entries.items():
        if not isinstance(e, dict):
            missing.append(key)
            continue
        lack = [f for f in required if f not in e]
        if lack:
            missing.append(f"{key}（缺 {','.join(lack)}）")
        if "provenance" in e and e["provenance"] not in provenance_enum:
            bad_prov.append(f"{key} → provenance={e['provenance']!r}")
        src = e.get("source")
        # 搬迁来源写成 `<role>/MEMORY.md` 形态，按后缀放行
        if src is not None and src not in source_enum and not str(src).endswith("MEMORY.md"):
            bad_src.append(f"{key} → source={src!r}")
        sc = e.get("scope")
        if scopes and sc not in scopes:
            bad_scope.append(f"{key} → scope={sc!r}")
        prefix = key.split(":", 1)[0]
        if sc in scopes and prefix != sc:
            mismatch.append(f"{key}（key 前缀 {prefix!r} ≠ scope {sc!r}）")
    keys = sorted(entries)
    dups = [f"{a} ⊂ {b}" for a, b in zip(keys, keys[1:]) if b.startswith(a)]
    for label, items, level in (("scope 非法枚举", bad_scope, "warn"),
                                ("provenance 非法枚举", bad_prov, "warn"),
                                ("source 非法枚举（改成 notes.md / [纠正] / auto-memory "
                                 "/ manual backfill 之一即可消除）", bad_src, "warn"),
                                ("key/scope 不一致", mismatch, "warn"),
                                ("字段缺失", missing, "warn"),
                                ("疑似重复 key（前缀包含）", dups, "warn")):
        if items:
            out.append((level, f"{label} {len(items)} 条: " + "；".join(items[:4]) + ("…" if len(items) > 4 else "")))
    if not out:
        out.append(("ok", f"{len(entries)} 条 entry schema/scope 全部合规"))
    return out


def check_proposal_dates(ctx):
    """applied/rejected 提案日期字段——迭代日期代码派生（G1#2）的输入质量兜底。"""
    P = ctx["paths"]
    if not P.proposals.is_dir():
        return [("info", "proposals/ 不存在（新项目·尚无运行数据）")]
    out, total = [], 0
    for sub, field in (("applied", "applied_at"), ("rejected", "rejected_at")):
        d = P.proposals / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yaml")):
            total += 1
            text = f.read_text(encoding="utf-8", errors="replace")
            if not re.search(r"^\s*" + field + r"""\s*:\s*["']?\d{4}-\d{2}-\d{2}""", text, re.M):
                out.append(("warn", f"{sub}/{f.name} 缺 {field}（派生将回退文件名日期，请补字段）"))
    if not out:
        out.append(("ok", f"applied/rejected 共 {total} 份提案日期字段齐全" if total else "尚无 applied/rejected 提案"))
    return out


def check_notes_backlog(ctx):
    """notes 积压：内容行数 + 距**该角色**上次 memory_promoted 天数。

    与 check-iteration-trigger 同阈值但取数口径不同（按角色 vs 全局、从未提升算不算陈化），
    差异与理由见文件顶部 NOTES_STALE_DAYS 旁注释——那不是漂移，是诊断与触发的定位差别。
    """
    P = ctx["paths"]
    if not P.memory.is_dir():
        return [("info", "agent-memory/ 不存在（新项目·尚无运行数据）")]
    last_promoted = {}
    for ev in ctx["events"]:
        if ev.get("type") == "memory_promoted":
            r = ev.get("role") or ev.get("scope")
            ts = _parse_ts(ev.get("ts"))
            if r and ts is not None and (r not in last_promoted or ts > last_promoted[r]):
                last_promoted[r] = ts
    out = []
    for scope in ctx["scopes"]:
        f = P.memory / scope / "notes.md"
        if not f.exists():
            continue
        n = _count_content_lines(f.read_text(encoding="utf-8", errors="replace"))
        days = ((datetime.now(timezone.utc) - last_promoted[scope]).days
                if scope in last_promoted else None)
        if n > NOTES_BACKLOG_LINES:
            out.append(("warn", f"{scope}/notes.md 内容 {n} 行 > {NOTES_BACKLOG_LINES}（积压，建议跑 librarian）"))
        elif n > 5 and (days is None or days > NOTES_STALE_DAYS):
            since = f"{days} 天前" if days is not None else "从未"
            out.append(("warn", f"{scope}/notes.md 内容 {n} 行且上次提升在 {since}（陈化 > {NOTES_STALE_DAYS} 天）"))
    if not out:
        out.append(("ok", "各角色 notes 无积压无陈化"))
    return out


def check_memory_capacity(ctx):
    """MEMORY.md 容量：字符预算（role/shared 分档）+ token 估算。"""
    P = ctx["paths"]
    if not P.memory.is_dir():
        return [("info", "agent-memory/ 不存在（新项目·尚无运行数据）")]
    out, sizes = [], []
    for scope in ctx["scopes"]:
        f = P.memory / scope / "MEMORY.md"
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        budget = SHARED_MEMORY_MAX_CHARS if scope == "shared" else ROLE_MEMORY_MAX_CHARS
        est = int(len(text) * TOKEN_PER_CHAR)
        sizes.append(f"{scope}={len(text)}字符(≈{est}tok)/预算{budget}")
        if len(text) > budget:
            out.append(("warn", f"{scope}/MEMORY.md {len(text)} 字符 > 预算 {budget}（容量整理候选，须用户确认后降级）"))
    out.append(("ok" if not any(l == "warn" for l, _ in out) else "info", "体量: " + "；".join(sizes)))
    return out


def _auto_memory_dir(P):
    """CC 官方 auto-memory 目录：~/.claude/projects/<munged-project-path>/memory。

    munge 规则实测（2026-08-06）：项目绝对路径中非 [A-Za-z0-9] 字符逐个替换为 '-'。
    """
    munged = re.sub(r"[^A-Za-z0-9]", "-", str(P.project))
    return Path.home() / ".claude" / "projects" / munged / "memory"


def check_auto_memory(ctx):
    """auto-memory 健康：体量（单条字符预算 + 索引行数上限）+ 索引与主题文件一一对应。

    对应检测（2026-08-16 增）解决两类静默失效：
      - **孤儿**：主题文件在、索引没它 —— 文件还在磁盘上，但没有任何入口能发现它，
        等同于失踪。本项目曾长期存在 4 个孤儿无人察觉。
      - **悬空**：索引有链接、文件没了 —— 点进去是空的。

    只对**带 markdown 链接**的索引行做对账，`- **[scope 指针] dev 域** — …` 这类纯文本
    指针行天然豁免（它们指向 `.claude/agent-memory/`，本就不对应 auto-memory 主题文件）。
    反过来说：若有人把 scope 指针写成 `[](../../…)` 链接形式，这里会判它悬空——那是对的，
    深层相对路径既脆弱又会误导，应改回纯文本形式。

    清理动作一律人工拍板，本检查只报警。
    """
    P = ctx["paths"]
    am = _auto_memory_dir(P)
    if not am.is_dir():
        return [("info", "auto-memory 目录不存在（未启用 CC 记忆或路径规则变更）")]
    out = []
    entries = [f for f in am.glob("*.md") if f.name != "MEMORY.md"]
    total = 0
    for f in sorted(entries):
        n = len(f.read_text(encoding="utf-8", errors="replace"))
        total += n
        if n > AUTO_MEMORY_ENTRY_MAX_CHARS:
            out.append(("warn", f"{f.name} {n} 字符 > 预算 {AUTO_MEMORY_ENTRY_MAX_CHARS}（超预算→先落文档再指回；瘦身候选，人工拍板）"))
    idx = am / "MEMORY.md"
    if idx.exists():
        idx_text = idx.read_text(encoding="utf-8", errors="replace")
        idx_lines = idx_text.count("\n")
        if idx_lines > AUTO_MEMORY_INDEX_MAX_LINES:
            out.append(("warn", f"MEMORY.md 索引 {idx_lines} 行 > {AUTO_MEMORY_INDEX_MAX_LINES}（条数治理候选：完结沉降/同模块聚合/归档，人工拍板）"))
        linked = {t.rsplit("/", 1)[-1] for t in re.findall(r"\]\(([^)]+\.md)\)", idx_text)}
        names = {f.name for f in entries}
        orphans = sorted(names - linked)
        dangling = sorted(linked - names)
        if orphans:
            out.append(("warn", f"{len(orphans)} 个孤儿主题文件（在磁盘但索引里没有入口，等同失踪）: "
                                f"{'、'.join(orphans[:6])}{' …' if len(orphans) > 6 else ''}"))
        if dangling:
            out.append(("warn", f"{len(dangling)} 条悬空索引（链接指向不存在的文件）: "
                                f"{'、'.join(dangling[:6])}{' …' if len(dangling) > 6 else ''}"))
        if not orphans and not dangling:
            out.append(("ok", f"索引与主题文件一一对应（{len(names)} 个）"))

        # scope 级指针的**文件级**存在性（6.2）：指针指向 .claude/agent-memory/<scope>/MEMORY.md，
        # 目标域被误删时指针就成了死指针，而上面的对账只管 auto-memory 内部文件、查不到它。
        # 只做文件级、不做条目级——[纠正] 条目永不清理，条目级 dangling 概率极低，
        # 为它扫全文不划算。
        ptr_targets = set(re.findall(r"`(\.claude/agent-memory/[^`]+MEMORY\.md)`", idx_text))
        dead = sorted(t for t in ptr_targets if not (P.project / t).exists())
        if dead:
            out.append(("warn", f"{len(dead)} 条 scope 指针指向不存在的记忆文件（死指针）: "
                                f"{'、'.join(dead)}"))
        elif ptr_targets:
            out.append(("ok", f"{len(ptr_targets)} 条 scope 指针目标齐全"))
    out.append(("ok" if not any(l == "warn" for l, _ in out) else "info",
                f"体量: {len(entries)} 条 / {total} 字符(≈{int(total * TOKEN_PER_CHAR)}tok)"))
    return out


def check_promise_producer(ctx):
    """承诺-producer 对账 + 死信号三态标注（G1#3 规格）：
    normal（有发生）/ never-triggered starved（零发生但承诺在，上游饥饿）/
    broken promise（零发生且**插件内**找不到 producer 承诺文本）。

    注意扫描面是 **PLUGIN_ROOT**（插件自身的 skills / rules / scripts），不是用户项目——
    「有没有人会产生这个事件」是插件的属性。所以本项对不同项目只有 counts 部分会变。
    """
    if not EVENT_SCHEMA_FILE.exists():
        return [("error", f"event-schema.json 缺失: {EVENT_SCHEMA_FILE}")]
    schema = json.loads(EVENT_SCHEMA_FILE.read_text(encoding="utf-8"))
    counts = {}
    for ev in ctx["events"]:
        t = ev.get("type")
        counts[t] = counts.get(t, 0) + 1
    corpus = []
    for pattern, base in (("skills/**/SKILL.md", PLUGIN_ROOT), ("rules/**/*.md", PLUGIN_ROOT),
                          ("scripts/*.py", PLUGIN_ROOT)):
        for f in base.glob(pattern):
            corpus.append(f.read_text(encoding="utf-8", errors="replace"))
    corpus = "\n".join(corpus)
    out, starved, broken, normal, skipped = [], [], [], 0, 0
    for etype, spec in schema.get("events", {}).items():
        tier = spec.get("reliability")
        if tier not in ACTIVE_TIERS:
            skipped += 1
            continue
        c = counts.get(etype, 0)
        if c > 0:
            normal += 1
        elif etype in corpus:
            starved.append(f"{etype}({tier})")
        else:
            broken.append(f"{etype}({tier})")
    out.append(("ok", f"normal {normal} 类（历史有发生）；removed/非活跃层跳过 {skipped} 类"))
    if starved:
        out.append(("info", "never-triggered (starved) — 零发生但承诺存在（上游饥饿，非损坏；诊断见 event-schema caveats）: "
                    + "、".join(starved)))
    if broken:
        out.append(("error", "broken promise — 零发生且全仓无 producer 文本: " + "、".join(broken)))
    undocumented = sorted(t for t in counts if t and t not in schema.get("events", {}))
    if undocumented:
        out.append(("info", f"事件流中存在 schema 未收录的类型 {len(undocumented)} 类（覆盖缺口）: "
                    + "、".join(undocumented[:8]) + ("…" if len(undocumented) > 8 else "")))
    return out


def check_protected_assets(ctx):
    """受保护资产变更↔审批痕迹对账（启发式，仅提示级）：近 N 天 git 提交触及受保护路径，
    changelog 中找不到对应文件名痕迹的列出请人工核对。"""
    P = ctx["paths"]

    def _git(*args):
        return subprocess.run(["git", *args], cwd=str(P.project), capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30)

    try:
        # 排除**根提交**：项目诞生那一刻所有文件都是新增的，不是「改了受保护资产没走审批」。
        # 不排的话每个新项目一装完就被提示「CLAUDE.md / settings.json 未见审批痕迹」——
        # 而那正是 scaffold 刚给它建的（2026-08-10 走查实证）。
        root = _git("rev-list", "--max-parents=0", "HEAD")
        roots = [x.strip() for x in root.stdout.splitlines() if x.strip()] if root.returncode == 0 else []
        head = _git("rev-parse", "HEAD")
        if roots and head.returncode == 0 and head.stdout.strip() in roots:
            return [("ok", "仓库只有首个提交——项目刚建立，受保护资产尚无「变更」可对账")]
        rev_range = ["HEAD"] + [f"^{c}" for c in roots]
        r = _git("log", f"--since={PROTECTED_WINDOW_DAYS}.days",
                 "--name-only", "--pretty=format:", *rev_range, "--", *PROTECTED_PATHS)
    except Exception as e:
        return [("info", f"git 不可用，跳过（{e.__class__.__name__}）")]
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if "not a git repository" in err:
            return [("info", "非 git 仓库，跳过")]
        if "does not have any commits" in err:
            return [("info", "git 仓库尚无提交历史（新项目），跳过")]
        return [("info", f"git log 失败，跳过（{err[:60]}）")]
    changed = sorted({ln.strip() for ln in r.stdout.splitlines() if ln.strip()})
    if not changed:
        return [("ok", f"近 {PROTECTED_WINDOW_DAYS} 天受保护资产无提交")]
    changelog = P.changelog.read_text(encoding="utf-8", errors="replace") if P.changelog.exists() else ""
    unmatched = [p for p in changed if Path(p).name not in changelog and Path(p).stem not in changelog]
    out = [("ok", f"近 {PROTECTED_WINDOW_DAYS} 天受保护资产提交 {len(changed)} 个文件")]
    if unmatched:
        out.append(("info", f"changelog 未见痕迹的 {len(unmatched)} 个（启发式匹配，请人工核对是否经过审批）: "
                    + "；".join(unmatched[:6]) + ("…" if len(unmatched) > 6 else "")))
    return out


# ------------------------------------------------- 第 0 组：安装 / 环境完整性
#
# 与 runtime 组的区别：runtime 组查「跑起来之后数据健不健康」，本组查「装没装对」。
# 两个调用场景共用同一份实现：
#   1. launcher 落盘验收——重启前从任意目录 `--project <目标> --group install`
#   2. core 运行时验收——`session-start-prep` 判 session_counter == 1 时 import 直跑
# 依赖重启才成立的项（hook 活性）在重启前自降为 info「待首个会话后复查」，
# 不做 --stage 参数：同一条命令在重启前后跑出不同结果，符合既有「空态不误报」原则。

# 骨架分三档，判据是「缺了会不会当场坏事」以及「它本来就该不该在」。
#
# 硬骨架：进 git、hooks 与 agent 启动协议强依赖，缺了运行期直接出问题
SCAFFOLD_REQUIRED_FILES = [
    "projects/board.yaml",
    ".claude/agent-memory/shared/MEMORY.md", ".claude/agent-memory/shared/notes.md",
]
# 运行时骨架：**整个目录被 gitignore**（本地派生状态，设计上不进 git），所以
# clone 出来的项目必然没有它们——把这几项列成硬需求会让「同事加入」场景**必然报 error**。
# 四个文件都能自愈（各消费方写入前都 mkdir(parents=True) 并落初值），所以判据是：
#   hook 跑过（有 plugin-root.txt）却还缺 → 真问题 error
#   hook 没跑过 → info「重启后自动生成」
SCAFFOLD_RUNTIME_FILES = [
    ".claude/workframe-state/activity-state.json", ".claude/workframe-state/events.jsonl",
    ".claude/workframe-state/memory-index.json", ".claude/workframe-state/skill-metrics.yaml",
]
# 软骨架：缺了不影响跑，重跑 scaffold 可补
# 注意 `logs/.gitkeep` **不在此列**——`logs/` 整个被 gitignore，clone 后永远不会有它，
# 而写日志的 hook 自己会 mkdir。列进来等于给每个克隆项目挂一条永远消不掉的 warn。
SCAFFOLD_OPTIONAL_FILES = [
    "projects/specs/overview.md", "projects/specs/_meta/taxonomy.md",
    "projects/issues/TEMPLATES.md",
    "projects/changelog.md", "projects/evals/README.md",
    # scaffold 建的治理空目录（.gitkeep 占位）。消费方写入前都会 mkdir 兜底，所以缺了
    # 不致命——但「scaffold 产出什么」与「doctor 查什么」是两处手写，不列进来就没人
    # 发现漂移（check_scaffold_gitkeep_dirs_covered 已对账两边）。
    # 与 logs/.gitkeep 的区别：这些在 projects/ 下、随 git 分发，clone 后本就该有。
    "projects/evals/rules/.gitkeep", "projects/evals/skills/.gitkeep",
    "projects/evals/agents/.gitkeep",
    "projects/proposals/pending/.gitkeep", "projects/proposals/applied/.gitkeep",
    "projects/proposals/rejected/.gitkeep",
    "projects/archive/.gitkeep",
]
# 参数化渲染产物：仅 `--params` 路径（launcher 创建对话）才有
SCAFFOLD_PARAM_FILES = [
    "CLAUDE.md", "projects/modules/overview.md",
    "company-context/README.md", "my-workspace/README.md",
]
BASELINE_ROLES = ("pm", "dev", "qa", "prompt-eng")
CONFIG_FIELD_ENUM = {
    "project_type": ("product-work",),
    "dormant_profile": ("high-frequency", "normal", "low-frequency", "archive"),
    "role_profile": ("software-team", "solo-pm", "ai-product"),
}
REQUIRED_RULE_MIRRORS = ("agent-protocols.md", "auto-update.md",
                         "correction-detection.md", "response-output.md")
GITIGNORE_REQUIRED = (".claude/settings.local.json", ".claude/workframe-state/",
                      "logs/", "tmp/", ".tmp/")
# 本机绝对路径的通用模式——不写死任何维护者用户名，换个贡献者一样拦得住
LOCAL_PATH_PATTERNS = (r"[A-Za-z]:[\\/]Users[\\/]", r"/home/[^/\s]+/", r"/Users/[^/\s]+/")


def check_skeleton(ctx):
    """骨架完整性 + 占位符零残留。

    上方三档清单与 `project_scaffold.py` 的写入点是**两处事实源**，改动 scaffold 的
    产物集合时必须同步这里——否则新产物落在验收面之外，缺失时静默放过（taxonomy
    接入装机线时即漏过一次）。
    """
    P = ctx["paths"]
    out = []
    missing_req = [f for f in SCAFFOLD_REQUIRED_FILES if not (P.project / f).is_file()]
    for role in BASELINE_ROLES:
        for fn in ("MEMORY.md", "notes.md"):
            if not (P.memory / role / fn).is_file():
                missing_req.append(f".claude/agent-memory/{role}/{fn}")
    if missing_req:
        out.append(("error", f"硬骨架缺 {len(missing_req)} 项（hooks / agent 启动协议强依赖）: "
                    + "、".join(missing_req[:6]) + ("…" if len(missing_req) > 6 else "")
                    + "（重跑 project_scaffold.py 可补齐）"))
    missing_opt = [f for f in SCAFFOLD_OPTIONAL_FILES if not (P.project / f).is_file()]
    if missing_opt:
        out.append(("warn", f"软骨架缺 {len(missing_opt)} 项（不影响运行）: " + "、".join(missing_opt)))
    # 运行时骨架：缺失是否算问题，取决于 hook 到底跑过没有
    missing_rt = [f for f in SCAFFOLD_RUNTIME_FILES if not (P.project / f).is_file()]
    if missing_rt:
        hooks_ran = (P.state / "plugin-root.txt").exists()
        if hooks_ran:
            out.append(("error", f"运行时骨架缺 {len(missing_rt)} 项，但 hook 已跑过（有 plugin-root.txt）"
                                 f"——说明写入失败而非尚未生成: " + "、".join(missing_rt)))
        else:
            out.append(("info", f"运行时骨架尚未生成 {len(missing_rt)} 项——该目录不进 git，"
                                "clone 下来没有属正常，首个会话由 hook 自动补齐"))
    missing_param = [f for f in SCAFFOLD_PARAM_FILES if not (P.project / f).exists()]
    if missing_param:
        # 接入已有项目时用户可能自带 CLAUDE.md 或不需要周边资产层，故为 warn 非 error
        out.append(("warn", "对话渲染产物缺 " + "、".join(missing_param)
                    + "（走 launcher 创建流程会生成；接入已有项目时可能是有意为之）"))
    # 占位符残留：scaffold 写入时已断言零残留，这里只兜底用户手改
    residue = []
    for f in SCAFFOLD_PARAM_FILES + ["projects/modules/overview.md"]:
        target = P.project / f
        if target.exists():
            left = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", target.read_text(encoding="utf-8", errors="replace"))))
            if left:
                residue.append(f"{f} → {', '.join(left[:3])}")
    if residue:
        out.append(("warn", "模板占位符未替换（scaffold 写入时会断言，出现说明是手改）: " + "；".join(residue)))
    if not out:
        out.append(("ok", f"骨架齐全：{len(SCAFFOLD_REQUIRED_FILES) + len(BASELINE_ROLES) * 2} 硬 / "
                    f"{len(SCAFFOLD_RUNTIME_FILES)} 运行时 / {len(SCAFFOLD_OPTIONAL_FILES)} 软 / "
                    f"{len(SCAFFOLD_PARAM_FILES)} 渲染产物，无占位符残留"))
    return out


def check_claude_md(ctx):
    """CLAUDE.md 的四个框架契约段必须在。

    接入已有项目时 scaffold 对已存在的 CLAUDE.md 一律跳过不覆盖，由 launcher 按
    `reference/claude-md-merge-guide.md` 整合写回。整合漏做或漏段时表面毫无异常——
    文件在、hooks 在、agent 能路由，但对应的行为约定静默失效。只查「文件存在」查不出来，
    所以这里查段。
    """
    P = ctx["paths"]
    if not P.claude_md.exists():
        return [("warn", "CLAUDE.md 不存在——角色路由与协作约定无处承载"
                         "（走 launcher 创建流程会生成；接入已有项目时应按整合规范写回）")]
    text = P.claude_md.read_text(encoding="utf-8", errors="replace")
    # 按语义标志判而非标题字面量：用户可能改标题措辞，但这些契约词没了就是真的没了。
    # 「文档与结构约定」曾用「文件放哪儿」当 token——那是纯标题措辞，恰恰踩了本函数
    # 自己声明要避开的坑（内容完备的项目照样报 error）。改钉 skill 名 document-norms：
    # 该段的全部价值就是把读者引向那个 skill，指针没了段落也就废了。
    required = {
        "角色体系": ("@dev", "@prompt-eng"),
        "路由规则": ("@角色名",),
        "任务状态流转与签发权限": ("pending_qa",),
        "文档与结构约定": ("document-norms",),
    }
    # 其中两段的 token 会被**别的段落借用**，全文检查挡不住「整段被删」，必须再做段内检查：
    #   - 角色体系：@dev / @prompt-eng 在业务背景、路由偏好、状态流转表里都会出现
    #   - 路由规则：@角色名 在「快速入门」的「指定 `@角色名` 则强制委派」、以及
    #     {{ROLE_PROFILE_ROUTING}} 渲染出的 `## 路由偏好` 段里都会出现
    #     （2026-08-16 实测：删掉整个 `## 路由规则` 段，本项仍报 ✓，主 Claude 由此
    #      静默失去「直做还是委派」的调度依据）
    # 另外两段不做段内检查是**有意的**：`pending_qa` 与 `document-norms` 只在各自段内
    # 出现，全文 token 消失即等价于段消失；而存量项目常把它们降级成三级标题挂在别的段下
    # （实测某项目把文档约定写成 `## 项目特殊约束` 下的 `### 文档与知识维护`），
    # 对它们钉二级段名会误伤合规项目。
    section_scoped = {
        "角色体系": (("角色体系", "角色定义"), ("@pm", "@dev", "@qa", "@prompt-eng"),
                     "该段是路由决策依据，角色行不全会让主 Claude 派错人"),
        "路由规则": (("路由规则",), ("@角色名",),
                     "该段是主 Claude「直做还是委派」的调度依据"),
    }
    # main-led 新机制：缺了不影响老项目运行，但新项目漏整合会让「主 Claude 直做」
    # 整套约定静默失效 → 报 warn 不报 error（拍板 13：原四段仍 error，新机制走 warn 档）。
    # token 一律选**契约标识符或路径字面量**，不选标题词：措辞可以改写，tag 值与落点
    # 路径是契约——改了它们机制本身就变了。
    recommended = {
        "主 Claude 代行流转": ("main-executed",),
        "自签 QA 禁令": ("签发权仍仅 @qa",),
        "直做前读契约与记忆": ("agent-memory/",),
        "工种知识分流判据": ("shared/notes.md",),
    }
    missing = [label for label, tokens in required.items()
               if not all(tok in text for tok in tokens)]
    if missing:
        return [("error", f"CLAUDE.md 缺 {len(missing)} 个框架契约段: {'、'.join(missing)}"
                          "——对应机制会静默失效。接入已有项目时按 core 的 "
                          "reference/claude-md-merge-guide.md 重做整合")]

    seg = re.split(r"^##\s+", text, flags=re.M)
    for label, (prefixes, tokens, why) in section_scoped.items():
        body = next((s for s in seg if s.startswith(prefixes)), None)
        if body is None:
            return [("error", f"CLAUDE.md 缺 `## {label}` 段标题——{tokens[0]} 等 token 虽在别处"
                              f"出现，但该段本身没了。{why}")]
        lack = [t for t in tokens if t not in body]
        if lack:
            return [("error", f"`## {label}` 段内缺 {'、'.join(lack)}"
                              f"（{len(tokens) - len(lack)}/{len(tokens)} 命中）——{why}")]
    lacking = [label for label, tokens in recommended.items()
               if not all(tok in text for tok in tokens)]
    if lacking:
        return [("warn", f"四个框架契约段齐全，但缺 {len(lacking)} 项 main-led 约定: "
                         f"{'、'.join(lacking)}——主 Claude 直做时无据可依"
                         "（模板已含，按 claude-md-merge-guide 补入即可）")]
    return [("ok", "四个框架契约段齐全 + main-led 四项约定在位")]


def check_prd_framework(ctx):
    """项目 PRD 框架 skill（.claude/skills/prd-style/）在位性。

    缺失不是故障——prd-writer 会 fallback 到框架默认模板照常工作，故仅 info；
    scaffold 装机会自动放入，老项目 / 手工接入项目缺失属正常。
    """
    P = ctx["paths"]
    f = P.project / ".claude" / "skills" / "prd-style" / "SKILL.md"
    if f.is_file():
        return [("ok", "项目 PRD 框架在位（.claude/skills/prd-style/——写 PRD 按本项目框架执行，可自由修改）")]
    return [("info", "未见项目 PRD 框架（.claude/skills/prd-style/）——写 PRD 将按框架默认执行；"
                     "需要本项目专属风格时，从 core 模板 templates/project-skills/prd-style/ 复制过来按需修改")]


def check_config(ctx):
    """.workframe-config.json 三字段存在且取值合法。"""
    P = ctx["paths"]
    if not P.config.exists():
        return [("error", f"{P.config.name} 不存在——该目录尚未接入 Workframe")]
    try:
        cfg = json.loads(P.config.read_text(encoding="utf-8"))
    except Exception as e:
        return [("error", f"{P.config.name} 解析失败: {e}（手工修 JSON，别删文件——它存着你的项目配置）")]
    if not isinstance(cfg, dict):
        return [("error", f"{P.config.name} 顶层不是 JSON 对象")]
    out = []
    for field, allowed in CONFIG_FIELD_ENUM.items():
        if field not in cfg:
            out.append(("error", f"缺字段 {field}（合法取值：{' / '.join(allowed)}）"))
        elif cfg[field] not in allowed:
            out.append(("error", f"{field}={cfg[field]!r} 非法（合法取值：{' / '.join(allowed)}）"))
    if not cfg.get("project_name"):
        out.append(("warn", "缺 project_name（启动横幅与报告的展示名）"))
    if cfg.get("framework_path"):
        out.append(("warn", "存在 framework_path——它是本机绝对路径，进 git 后协作者拿到的是无效路径；"
                            "marketplace 订阅场景不需要该字段，建议删除"))
    if not out:
        out.append(("ok", f"三字段齐全合法（{', '.join(f'{k}={cfg[k]}' for k in CONFIG_FIELD_ENUM)}）"))
    return out


def check_subscription(ctx):
    """项目 settings 的订阅声明——两项缺一不可，且 marketplace 来源形态影响可移植性。"""
    P = ctx["paths"]
    if not P.settings.exists():
        return [("error", ".claude/settings.json 不存在——core 插件未订阅，"
                          "需在项目目录跑 `claude plugin marketplace add <源> --scope project` "
                          "与 `claude plugin install core@workframe --scope project`")]
    try:
        st = json.loads(P.settings.read_text(encoding="utf-8"))
    except Exception as e:
        return [("error", f".claude/settings.json 解析失败: {e}")]
    out = []
    enabled = st.get("enabledPlugins") or {}
    if not any(str(k).startswith("core@") and v for k, v in enabled.items()):
        out.append(("error", "enabledPlugins 未启用 core@<市场>——跑 "
                             "`claude plugin install core@workframe --scope project`"))
    markets = st.get("extraKnownMarketplaces") or {}
    if not markets:
        # V2 实测：只跑 plugin install 时就是这个状态，协作者 clone 后解析不到插件来源
        out.append(("error", "缺 extraKnownMarketplaces——协作者 clone 后无法解析插件从哪装。"
                             "跑 `claude plugin marketplace add <源> --scope project` 补上"))
    for name, meta in markets.items():
        src = (meta or {}).get("source") or {}
        kind = src.get("source") or ("directory" if src.get("path") else "?")
        if kind == "directory" or src.get("path"):
            out.append(("warn", f"市场 {name} 是本地目录源（{src.get('path')}）——"
                                "该路径只在你这台机器上存在，协作者不可自动安装；"
                                "开源 / 团队协作场景应换 git 源"))
    if not out:
        out.append(("ok", f"订阅声明齐全（enabledPlugins + extraKnownMarketplaces {len(markets)} 个市场）"))
    return out


def check_rules_mirror(ctx):
    """core rules 镜像 4 份——SessionStart hook 自愈同步的产物。"""
    P = ctx["paths"]
    if not P.rules_mirror.is_dir():
        return [("error", f"rules 镜像目录不存在: {P.rules_mirror.relative_to(P.project)}"
                          "（重启会话由 SessionStart hook 自动同步；或手动跑 sync-rules.py --project）")]
    actual = {f.name for f in P.rules_mirror.glob("*.md")}
    missing = [r for r in REQUIRED_RULE_MIRRORS if r not in actual]
    if missing:
        return [("error", f"rules 镜像缺 {len(missing)} 份: {'、'.join(missing)}")]

    # 只查文件名挡不住**内容漂移**：框架升级后 rules 改了内容，SessionStart 的自愈同步
    # 尚未跑过（或跑失败了）时，镜像还是旧版而本项照样报绿——项目里的模型继续按旧规则
    # 干活，没有任何信号（实测：框架 agent-protocols 加了 ts 口径，项目镜像仍是旧的，
    # 两文件 hash 不同，doctor 却说「齐全」）。逐文件比 hash 才看得见。
    # 插件根从 plugin-root.txt 取（SessionStart 写）；取不到就只能退回文件名检查。
    stale = []
    src_dir = None
    root_txt = P.state / "plugin-root.txt"
    if root_txt.exists():
        try:
            src_dir = Path(root_txt.read_text(encoding="utf-8").strip()) / "rules" / "core"
        except Exception:
            src_dir = None
    if src_dir and src_dir.is_dir():
        import hashlib
        for name in REQUIRED_RULE_MIRRORS:
            src, dst = src_dir / name, P.rules_mirror / name
            if not src.exists():
                continue
            try:
                h1 = hashlib.sha256(src.read_bytes()).hexdigest()
                h2 = hashlib.sha256(dst.read_bytes()).hexdigest()
            except OSError:
                continue
            if h1 != h2:
                stale.append(name)
    extra = actual - set(REQUIRED_RULE_MIRRORS)
    if stale:
        out = [("warn", f"rules 镜像有 {len(stale)} 份与插件源不一致: {'、'.join(stale)}"
                        "——重启会话由 SessionStart 自愈同步；急用可手动跑 "
                        "`python \"$(cat .claude/workframe-state/plugin-root.txt)/scripts/sync-rules.py\" "
                        "--project .`。在同步前，项目里的模型读到的仍是旧规则")]
    else:
        out = [("ok", f"4 份 core rules 镜像齐全"
                      f"{'（内容与插件源一致）' if src_dir and src_dir.is_dir() else '（未比对内容：plugin-root.txt 不可用）'}")]
    if extra:
        out.append(("info", f"镜像目录另有 {len(extra)} 个非 core 文件: {'、'.join(sorted(extra)[:4])}"
                            "（该目录由 sync-rules 就地覆盖 + prune 维护，多余的 .md 会被清掉）"))
    return out


def check_gitignore(ctx):
    """.gitignore 必需条目——运行时状态与日志默认不进 git。"""
    P = ctx["paths"]
    if not P.gitignore.exists():
        return [("warn", ".gitignore 不存在——运行时状态与日志会被误提交"
                         "（重跑 project_scaffold.py 会从模板补上）")]
    text = P.gitignore.read_text(encoding="utf-8", errors="replace")
    # 与 project_scaffold.gitignore_covers 同口径：子串匹配会让 `.tmp/` 顶替 `tmp/`，
    # 也会把注释掉的 `# tmp/` 与否定规则 `!tmp/` 算成已有
    missing = [e for e in GITIGNORE_REQUIRED if not _gitignore_covers(text, e)]
    if missing:
        return [("warn", f"缺必需条目: {'、'.join(missing)}")]

    # 条目齐全 ≠ sidecar 真能进 git：managed block 之外若有整目录忽略规则，git 里父目录
    # 已被排除，block 内的 `!` 例外无效，memory-index.json 照样被吞——而缺失扫描看不出来。
    # 只报不改（marker 外是用户地盘，且有意忽略整个目录是正当选择）。
    try:
        # 传 project——检测优先问 git check-ignore（任何形态的宽规则都逃不掉），
        # 不传则退化为字面扫描，`.claude/` 这类祖先目录规则会漏
        shadow = _import_scaffold().find_sidecar_shadowing_rule(text, P.project)
    except Exception:
        shadow = None
    if shadow:
        return [("warn", f"条目齐全，但第 {shadow[0]} 行 `{shadow[1]}` 在 managed block 之外，"
                         f"会连坐忽略记忆 sidecar（memory-index.json）——block 里的 `!` 例外对它无效。"
                         f"改成 `.claude/workframe-state/*` 即可；有意忽略整个目录则忽略本条")]
    return [("ok", f"{len(GITIGNORE_REQUIRED)} 项必需条目齐全")]


# launcher 初始化的必经步骤。setup-state 是**增量**写的（scaffold 一成功就落第一步），
# 所以「缺步骤」和「步骤失败」是两种不同的中断形态，得分开判——
# 早期版本只查有没有非 ok 的值，于是一份只有 `scaffold: ok` 的半截文件会被判成「全部完成」。
SETUP_REQUIRED_STEPS = ("scaffold", "subscribe", "sync_rules")

# `acceptance` 不在必查清单里，也不参与 failed 判定——它由 doctor 自己在落盘验收跑完后落笔
# （见 main），是**本工具的运行记录**而不是 launcher 的前置步骤。
# 让 launcher 记它会自指：acceptance 的语义是「落盘验收做完了」，只能在验收之后写；而验收跑的
# 就是本组、组里又查 acceptance——于是第一次跑必然缺它、必然报 error，模型补记后再跑才过。
# 那条 error 是机制自造的，不是真问题。
SETUP_SELF_RECORDED_STEPS = ("acceptance",)


def check_setup_state(ctx):
    """消费 setup-state.json 识别「上次装到哪一步」。"""
    P = ctx["paths"]
    if not P.setup_state.exists():
        return [("info", "无 setup-state.json（手工接入或早于该机制的项目，不影响使用）")]
    try:
        st = json.loads(P.setup_state.read_text(encoding="utf-8"))
    except Exception as e:
        return [("warn", f"setup-state.json 解析失败: {e}")]
    steps = st.get("steps") or {}
    # 自愈：文件里一个 launcher 步骤都没有、只剩本工具自己写的 acceptance——说明它不是
    # launcher 建的，而是旧版 _record_acceptance 给手工接入项目凭空造出来的（那个 bug 已修，
    # 但已经被造出来的文件还在人家仓里）。按「无有效初始化记录」放行，等同于文件不存在。
    if steps and all(k in SETUP_SELF_RECORDED_STEPS for k in steps):
        return [("info", "setup-state.json 里只有本工具的验收记录、无 launcher 步骤"
                         "——按手工接入项目处理（旧版 doctor 会凭空建这份文件，可安全删除）")]
    failed = [k for k, v in steps.items()
              if k not in SETUP_SELF_RECORDED_STEPS and not str(v).startswith("ok")]
    missing = [k for k in SETUP_REQUIRED_STEPS if k not in steps]
    out = []
    if failed:
        out.append(("error", f"初始化有步骤失败: "
                             + "、".join(f"{k}({steps[k]})" for k in failed)))
    if missing:
        done = [k for k in SETUP_REQUIRED_STEPS if k in steps]
        out.append(("error", f"初始化未走完——已完成 {len(done)}/{len(SETUP_REQUIRED_STEPS)} 步"
                             f"（{'、'.join(done) or '无'}），未做: {'、'.join(missing)}"))
    if out:
        out.append(("info", "补做未完成的步骤后重跑本检查；各步命令见 launcher setup skill §5"))
        return out
    when = st.get("completed_at") or st.get("updated_at") or "未知时间"
    acc = steps.get("acceptance")
    tail = f"；最近一次落盘验收: {acc}" if acc else "（尚未跑过落盘验收）"
    return [("ok", f"初始化 {len(SETUP_REQUIRED_STEPS)} 步全部完成于 {when}{tail}")]


def check_init_completeness(ctx):
    """初始化完整度：源资料是否全部落地（「第二幕」状态）。

    pending_work 是**流程状态不是故障**——待接力 / 挂起都是用户知情拍板的合法态，
    一律 info。全部批次销账（键删除）才转 ok「完整落地」。
    """
    P = ctx["paths"]
    if not P.setup_state.exists():
        return [("info", "无 setup-state.json，无法判断（手工接入项目不影响使用）")]
    try:
        st = json.loads(P.setup_state.read_text(encoding="utf-8"))
    except Exception:
        return [("info", "setup-state.json 解析失败（断点检查项已报），跳过")]
    batches = (st.get("pending_work") or {}).get("batches") or []
    if not batches:
        return [("ok", "初始化已完整落地（无待接力批次）")]

    def _fmt(bs):
        return "、".join(
            f"{b.get('name', '?')}（{len(b.get('files') or [])} 项 → {b.get('target_skill', '?')}）"
            for b in bs[:3]
        ) + ("…" if len(bs) > 3 else "")

    relay = [b for b in batches if b.get("pace") == "relay"]
    paused = [b for b in batches if b.get("pace") == "paused"]
    undecided = [b for b in batches if b.get("pace", "undecided") == "undecided"]
    out = []
    if relay:
        out.append(("info", f"初始化第二幕待接力 {len(relay)} 批：{_fmt(relay)}"
                            f"——会话里说「继续初始化」即开工"))
    if undecided:
        out.append(("info", f"{len(undecided)} 批资料未定处置：{_fmt(undecided)}"))
    if paused:
        out.append(("info", f"第二幕挂起 {len(paused)} 批（用户拍板暂不做，随时可恢复）：{_fmt(paused)}"))
    return out


def check_portability(ctx):
    """可移植性：进 git 的文件里不该出现本机绝对路径（E7；只报 warn，不阻断）。"""
    P = ctx["paths"]
    try:
        r = subprocess.run(["git", "ls-files"], cwd=str(P.project), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
    except Exception as e:
        return [("info", f"git 不可用，跳过（{e.__class__.__name__}）")]
    if r.returncode != 0:
        return [("info", "非 git 仓库，跳过可移植性检查")]
    hits, scanned = [], 0
    for rel in r.stdout.splitlines():
        rel = rel.strip()
        if not rel or rel.startswith(("logs/", "tmp/", "projects/archive/")):
            continue
        f = P.project / rel
        if not f.is_file() or f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip"}:
            continue
        try:
            raw = f.read_bytes()
        except Exception:
            continue
        if b"\x00" in raw:
            continue
        scanned += 1
        for lineno, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            # 文档里拿本机路径作示例是合法的，只在明显是配置/引用的行上判
            if any(re.search(pat, line) for pat in LOCAL_PATH_PATTERNS):
                hits.append(f"{rel}:{lineno}")
                break
    if hits:
        return [("warn", f"{len(hits)} 个进 git 的文件含本机绝对路径（协作者拿到的是无效路径）: "
                 + "、".join(hits[:5]) + ("…" if len(hits) > 5 else ""))]
    if scanned == 0:
        # 「扫了 0 个还报绿」是最坏的一种绿灯——它宣称验过，其实什么都没验。
        # 真实触发场景：git init 成功但首提交失败（没配 user.name），此时 ls-files 为空。
        return [("info", "git 仓库里还没有已跟踪文件，没有可扫的内容"
                         "——提交一次后再跑本检查才有意义")]
    return [("ok", f"已扫 {scanned} 个进 git 的文本文件，无本机绝对路径")]


def check_env(ctx):
    """python 可达性 + hook 活性证据（后者依赖重启，重启前自降 info）。"""
    P = ctx["paths"]
    out = [("ok", f"python {sys.version.split()[0]} 可达（当前解释器）")]
    # 这三个事件**全部由 SessionEnd 产生**——所以「开过会话」不够，得「关过会话」。
    # 用 plugin-root.txt（SessionStart 的产物）区分两种未命中，否则会对着已经重启过的用户
    # 说「若刚装完还没重启属正常」，把人绕进去（2026-08-10 走查 R5 实证）。
    hook_events = {"session_ended", "summary_recomputed", "skill_metrics_recomputed"}
    seen = {e.get("type") for e in ctx["events"]}
    session_start_ran = (P.state / "plugin-root.txt").exists()
    if hook_events & seen:
        out.append(("ok", f"hook 层产物已出现（{'、'.join(sorted(hook_events & seen))}）——hook 链路存活自证"))
    elif session_start_ran:
        out.append(("info", "SessionStart 已跑过（plugin-root.txt 在），但这三个事件由 **SessionEnd** 产生"
                            "——**关掉一次会话**后才会出现，不是没装好"))
    else:
        out.append(("info", "尚无任何 hook 产物——用 Claude Code 打开本项目开一个会话即可激活"))
    if session_start_ran:
        out.append(("ok", "plugin-root.txt 已写入（SessionStart hook 跑过）"))
    else:
        out.append(("info", "plugin-root.txt 未生成——重启后由 SessionStart hook 写入"))
    return out


INSTALL_CHECKS = [
    ("skeleton", "骨架完整性", "scaffold 基础骨架 + 渲染产物齐全；占位符零残留（兜底）", check_skeleton),
    ("claude_md", "CLAUDE.md 契约段", "角色体系 / 路由规则 / 状态流转 / 文档约定四段齐全（接入已有项目时靠整合写回）", check_claude_md),
    ("prd_framework", "项目 PRD 框架", "项目层 prd-style skill 在位性；缺失仅 info（prd-writer 走默认模板 fallback）", check_prd_framework),
    ("config", "config 三字段", "project_type / dormant_profile / role_profile 存在且取值合法", check_config),
    ("subscription", "订阅接线", "enabledPlugins + extraKnownMarketplaces 两项齐全；本地目录源告警", check_subscription),
    ("rules_mirror", "rules 镜像", "4 份 core rules 已同步到 .claude/rules/workframe/core/", check_rules_mirror),
    ("gitignore", ".gitignore 条目", "运行时状态 / 日志 / 临时区默认不进 git", check_gitignore),
    ("setup_state", "初始化断点", "消费 setup-state.json 识别上次装到哪一步", check_setup_state),
    ("init_completeness", "初始化完整度", "源资料是否全部落地；pending_work 批次为流程态，只报 info", check_init_completeness),
    ("portability", "可移植性", "进 git 的文件不含本机绝对路径（通用模式，不写死用户名）", check_portability),
    ("env", "环境与 hook 活性", "python 可达；hook 层事件产物存在即链路存活（重启前降 info）", check_env),
]

CHECKS = [
    ("events_parse", "事件流可解析率", "events.jsonl 逐行 JSON 解析；坏行=error", check_events_parse),
    ("sidecar_health", "sidecar 健康", "memory-index.json 字段 schema / scope 枚举 / key-scope 一致 / 疑似重复 key", check_sidecar_health),
    ("proposal_dates", "提案日期字段", "applied 须有 applied_at、rejected 须有 rejected_at（迭代日期派生的输入质量）", check_proposal_dates),
    ("notes_backlog", "notes 积压", f"内容行 > {NOTES_BACKLOG_LINES} 或 内容行>5 且距上次提升 > {NOTES_STALE_DAYS} 天（体积口径；开场卡问询按条目数另计，见 memory-ask.py）", check_notes_backlog),
    ("memory_capacity", "MEMORY 容量", f"字符预算 role ≤ {ROLE_MEMORY_MAX_CHARS} / shared ≤ {SHARED_MEMORY_MAX_CHARS}；附 token 估算（×{TOKEN_PER_CHAR}）", check_memory_capacity),
    ("auto_memory", "auto-memory 体量", f"单条 ≤ {AUTO_MEMORY_ENTRY_MAX_CHARS} 字符；索引 ≤ {AUTO_MEMORY_INDEX_MAX_LINES} 行；只报警不动手", check_auto_memory),
    ("promise_producer", "承诺-producer 对账与死信号三态", "schema 全事件类型 × (事件计数, producer 文本存在性) → normal / starved / broken-promise", check_promise_producer),
    ("protected_assets", "受保护资产对账", f"近 {PROTECTED_WINDOW_DAYS} 天 git 提交 vs changelog 痕迹（启发式，仅提示级）", check_protected_assets),
]


GROUPS = {
    "install": ("安装 / 环境完整性", INSTALL_CHECKS),
    "runtime": ("运行态数据健康", CHECKS),
}


def run_all(project_dir=None, group=None):
    """跑体检。`group` 为 None 时跑全部组。

    可被 import 复用——`session-start-prep` 在首个会话内联调用 `run_all(dir, "install")`
    做运行时验收，不必起子进程、也不依赖模型自觉。
    """
    P = Paths(project_dir or default_project_dir())
    # 预检（事件流预读 + 记忆目录枚举）此前在逐项隔离之外：事件文件是个目录、权限不足、
    # 记忆目录枚举失败，都会让整个 run_all 抛出去，调用方只好报「验收跳过」——用户很难
    # 不把「跳过」读成「没问题」。现在预检失败降级成一条 error 级检查项，验收照跑完。
    preflight = []
    try:
        events, malformed, total = _load_events(P)
    except Exception as e:
        events, malformed, total = [], [], 0
        preflight.append(f"事件流预读失败（{type(e).__name__}: {e}）——"
                         f"事件相关检查的结论不可信")
    try:
        scopes = _discover_scopes(P)
    except Exception as e:
        scopes = []
        preflight.append(f"记忆目录枚举失败（{type(e).__name__}: {e}）——"
                         f"记忆相关检查的结论不可信")
    ctx = {"paths": P, "scopes": scopes, "events": events,
           "malformed": malformed, "events_total": total}
    results = []
    if preflight:
        results.append({"id": "preflight", "group": group or "install",
                        "name": "体检前置读取", "criteria": "事件流与记忆目录可读",
                        "status": "error",
                        "findings": [{"level": "error", "msg": m} for m in preflight]})
    for gname, (_, checks) in GROUPS.items():
        if group and gname != group:
            continue
        for cid, name, crit, fn in checks:
            try:
                findings = fn(ctx)
            except Exception as e:
                findings = [("error", f"检查自身异常: {e.__class__.__name__}: {e}")]
            worst = max((LEVEL_ORDER[l] for l, _ in findings), default=0)
            status = [k for k, v in LEVEL_ORDER.items() if v == worst][0]
            results.append({"id": cid, "group": gname, "name": name, "criteria": crit,
                            "status": status,
                            "findings": [{"level": l, "msg": m} for l, m in findings]})
    return results


def summarize(results):
    """把结果压成一段可直接打进启动上下文的短文本；全绿时返回 None。"""
    bad = [r for r in results if r["status"] in ("error", "warn")]
    if not bad:
        return None
    lines = []
    for r in bad:
        for f in r["findings"]:
            if f["level"] in ("error", "warn"):
                lines.append(f"  {LEVEL_MARK[f['level']]} [{r['id']}] {f['msg']}")
    return "\n".join(lines)


def _record_acceptance(project_dir: Path, error_count: int):
    """把本次落盘验收的结果记进 setup-state.json 的 `acceptance` 键。

    由代码写而不是让 launcher 模型记，理由见 SETUP_SELF_RECORDED_STEPS 上方注释。
    **只在命令行 `--group install` 路径落笔**：session-start-prep 内联调的
    `run_all(dir, "install")` 是重启后的运行时验收，与 launcher 的落盘验收不是同一件事，
    不该覆盖这条记录。记账失败不影响验收结论本身——这份文件是派生状态。

    **文件不存在时不创建**（2026-08-16）：「没有 setup-state.json」是手工接入 / 早于该机制
    的老项目的**合法状态**，check_setup_state 对它报 info 放行。若这里凭空建一份只含
    `acceptance` 的文件，那些项目的状态就从「没记录」变成「记录显示 0/3 步没做」——
    从此每次体检都多一条 error，而项目其实装得好好的。实测踩过：给一个健康老项目跑一次
    `--group install`，第二次跑就红了，且再也回不去。体检工具不该给被检对象凭空造状态；
    真正走 launcher 装的项目，scaffold 一成功就落了 `scaffold: ok`，文件必然已存在。
    """
    state_file = project_dir / ".claude" / "workframe-state" / "setup-state.json"
    if not state_file.exists():
        return
    try:
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from project_scaffold import mark_setup_step
        if error_count:
            mark_setup_step(project_dir, "acceptance", "failed", f"{error_count} 项 error")
        else:
            mark_setup_step(project_dir, "acceptance")
    except Exception as e:
        print(f"[warn] acceptance 记账失败: {type(e).__name__}: {e}", file=sys.stderr)


def main(argv=None):
    _force_utf8_io()
    ap = argparse.ArgumentParser(prog="workframe-doctor", add_help=True)
    ap.add_argument("--list", action="store_true", help="输出保养项目录与阈值常量（单一事实源）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--project", help="目标项目目录；缺省用 $CLAUDE_PROJECT_DIR，再缺省用当前目录")
    ap.add_argument("--group", choices=sorted(GROUPS), help="只跑指定组；缺省跑全部")
    args = ap.parse_args(argv)

    if args.list:
        print("workframe-doctor 保养项目录（阈值常量集中于脚本顶部）")
        print(f"  阈值: NOTES_BACKLOG_LINES={NOTES_BACKLOG_LINES} NOTES_STALE_DAYS={NOTES_STALE_DAYS} "
              f"ROLE_MEMORY_MAX_CHARS={ROLE_MEMORY_MAX_CHARS} SHARED_MEMORY_MAX_CHARS={SHARED_MEMORY_MAX_CHARS} "
              f"AUTO_MEMORY_ENTRY_MAX_CHARS={AUTO_MEMORY_ENTRY_MAX_CHARS} AUTO_MEMORY_INDEX_MAX_LINES={AUTO_MEMORY_INDEX_MAX_LINES} "
              f"PROTECTED_WINDOW_DAYS={PROTECTED_WINDOW_DAYS}")
        for gname, (gdesc, checks) in GROUPS.items():
            print(f"  --group {gname} — {gdesc}（{len(checks)} 项）")
            for cid, name, crit, _ in checks:
                print(f"    [{cid}] {name} — {crit}")
        return 0

    project_dir = Path(args.project).resolve() if args.project else default_project_dir()
    if not project_dir.is_dir():
        print(f"workframe-doctor: 目标不是目录: {project_dir}", file=sys.stderr)
        return 2

    results = run_all(project_dir, args.group)
    counts = {"ok": 0, "info": 0, "warn": 0, "error": 0}
    for r in results:
        counts[r["status"]] += 1
    if args.json:
        print(json.dumps({"generated_at": datetime.now(timezone.utc).astimezone().isoformat(),
                          "project": str(project_dir), "group": args.group or "all",
                          "summary": counts, "checks": results},
                         ensure_ascii=False, indent=2))
    else:
        scope = f"（组: {args.group}）" if args.group else ""
        print(f"workframe-doctor — {project_dir}{scope}")
        for r in results:
            print(f"{LEVEL_MARK[r['status']]} [{r['id']}] {r['name']}")
            for f in r["findings"]:
                print(f"    {LEVEL_MARK[f['level']]} {f['msg']}")
        print(f"结果: ok {counts['ok']} / info {counts['info']} / warn {counts['warn']} / error {counts['error']}")
    if args.group == "install":
        _record_acceptance(project_dir, counts["error"])
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
