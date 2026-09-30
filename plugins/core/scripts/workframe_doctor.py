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

# 同目录公共模块：运行态目录与 harness 差异各只有一份实现
# （见 _state_io.py / _harness.py 抬头）。两者 import 时均无副作用，与下面
# 「本模块 import 无副作用」的承诺不冲突。
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from _harness import project_dir as _harness_project_dir  # noqa: E402
from _harness import SKILLS_DIR_CC, project_skills_conflict, project_skills_dir  # noqa: E402
from _harness import (SKILLS_ABSENT, SKILLS_DIR_NEUTRAL, SKILLS_FILE, SKILLS_LINK_ELSEWHERE,  # noqa: E402
                      SKILLS_LINK_NEUTRAL_MISSING, SKILLS_LINK_OK, SKILLS_LINK_TARGET_MISSING,
                      SKILLS_REALDIR, skills_cc_form, skills_two_copies)
import _settings_io  # noqa: E402
from _state_io import legacy_present, legacy_rel, memory_dir_of, new_rel, state_dir_of  # noqa: E402
from _state_io import SPILL_SUFFIX  # noqa: E402


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
AUTO_MEMORY_ENTRY_MAX_CHARS = 3000   # auto-memory 单条字符预算（对应主会话注入片 auto-memory 写入纪律 ≤2k token）
AUTO_MEMORY_INDEX_MAX_LINES = 80     # auto-memory MEMORY.md 索引行数上限（条数管索引成本）
TOKEN_PER_CHAR = 0.7          # CJK 文本 token 粗估系数（仅展示用）
PROTECTED_WINDOW_DAYS = 14    # 受保护资产对账回看窗口（天）
INJECT_SHARD_WINDOW_DAYS = 14  # 注入分片到齐检查的回看窗口（天）；与上一项同值，各管各的

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
        # 运行态两个目录的位置只由 _state_io 决定，doctor 不自己拼
        self.state = state_dir_of(self.project)
        self.state_rel = self.state.relative_to(self.project).as_posix()
        self.events = self.state / "events.jsonl"
        self.sidecar = self.state / "memory-index.json"
        self.setup_state = self.state / "setup-state.json"
        self.memory = memory_dir_of(self.project)
        self.memory_rel = self.memory.relative_to(self.project).as_posix()
        self.proposals = self.project / "projects" / "proposals"
        self.board = self.project / "projects" / "board.yaml"
        self.issues = self.project / "projects" / "issues"
        self.changelog = self.project / "projects" / "changelog.md"
        self.config = self.project / ".workframe-config.json"
        self.claude_md = self.project / "CLAUDE.md"
        self.agents_md = self.project / "AGENTS.md"
        self.settings = self.project / ".claude" / "settings.json"
        self.gitignore = self.project / ".gitignore"


def default_project_dir():
    return _harness_project_dir()


ACTIVE_TIERS = ("hook_deterministic", "protocol_expected", "model_mediated")
# 项目 skills 两个前缀都要：链接形态（新建 / 迁移后的项目）git 只报真实源 `.agents/skills/…`，
# 真目录形态（已装未迁的项目）git 报 `.claude/skills/…`
PROTECTED_PATHS = ["CLAUDE.md", "AGENTS.md", ".claude/skills", ".agents/skills",
                   ".claude/settings.json", ".claude/settings.local.json"]

LEVEL_ORDER = {"ok": 0, "info": 1, "warn": 2, "error": 3}
LEVEL_MARK = {"ok": "✓", "info": "i", "warn": "!", "error": "✗"}


def _no_data_why(P):
    """运行态文件 / 目录「不存在」的定性：没迁移的项目里数据还在旧位置，说成「新项目」是误导。"""
    if _has_legacy(P):
        return "（数据还在旧布局目录里、本项目没迁移，见 [legacy_state_dir]）"
    return "（新项目·尚无运行数据）"


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
        return [("info", f"events.jsonl 不存在{_no_data_why(P)}")]
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
        return [("info", f"memory-index.json 不存在{_no_data_why(P)}")]
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


def _load_yaml_probe():
    """惰性 import 同目录的 `_yaml_probe`（保持本模块 import 时无副作用）。"""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import _yaml_probe
    return _yaml_probe


def check_data_yaml_parse(ctx):
    """项目级数据文件过一次**真** YAML 解析器：`board.yaml` + issues + proposals。

    与 `events_parse` 同形：拿真解析器过一遍框架自己的数据文件，坏了报 error。
    差别只在解析器与文件集（那边逐行 JSON 读 events.jsonl，这边整份 YAML）。

    **为什么 `board.yaml` 必须在这里**：它是全框架**唯一**一个真有真解析器消费方的
    数据文件（`recompute_board_summary.py` 用 `yaml.safe_load` 读它），而那条路径的
    失败处置是降级逐行解析。也就是说这类畸形在 board.yaml 上今天就会发生，且发生在
    唯一的真解析器消费方身上——最该被看住的恰恰是它。

    **扫描面与 `module-close-check` 的 `[yaml-parse]` 项严格不相交**：那边只扫
    `projects/modules/**`（模块收口面），这边只扫这三类项目级运行态数据。
    判定式共用 `_yaml_probe`，只有一份实现，故不构成双事实源。

    **本项证明什么**：这些文件是合法 YAML。
    **不证明什么**：各处正则读数与真解析器一致——重复键这类「两边都成功但取值不同」
    的形态本项看不见。
    """
    P = ctx["paths"]
    targets = []
    if P.board.exists():
        targets.append((P.board, "projects/board.yaml"))
    if P.issues.is_dir():
        for f in sorted(P.issues.glob("*.yaml")):
            targets.append((f, f"projects/issues/{f.name}"))
    if P.proposals.is_dir():
        for sub in ("applied", "rejected"):
            d = P.proposals / sub
            if d.is_dir():
                for f in sorted(d.glob("*.yaml")):
                    targets.append((f, f"projects/proposals/{sub}/{f.name}"))
    if not targets:
        return [("info", "board.yaml / issues / proposals 都还没有（新项目·尚无运行数据）")]

    # 与 close-check 的 `[yaml-parse]` 项**共用同一个开关**：同一件事在两个命令里的两半，
    # 一个声明关掉两边，免得用户以为关了一处就关了整项。
    disabled = False
    if P.config.exists():
        try:
            cfg = json.loads(P.config.read_text(encoding="utf-8"))
            disabled = ((cfg.get("close_check") or {}).get("yaml_parse") is False)
        except Exception:
            disabled = False      # config 坏了不静默关闸（fail-safe 方向）
    if disabled:
        return [("info", '`.workframe-config.json` 里显式声明了 `"close_check": '
                         '{"yaml_parse": false}`，本项已关闭——这是一次有记录的声明，'
                         "不是静默跳过")]

    probe = _load_yaml_probe()
    available, detail = probe.pyyaml_status()
    if not available:
        return [("error",
                 f"YAML 可解析性**未验证**：本机导入 PyYAML 失败（{detail}）——"
                 f"这一项**没有跑**，不是通过。{len(targets)} 份数据文件一份都没被真"
                 f"解析器读过。两条出路：①`pip install pyyaml`；②在 "
                 f'`.workframe-config.json` 里显式关掉——`"close_check": '
                 f'{{"yaml_parse": false}}`。关掉是一次有记录的声明，缺库静默报绿不是')]

    out, n_ok, n_skipped = [], 0, 0
    for path, label in targets:
        # 与 close-check 的 `[yaml-parse]` 项同口径：「读不出来」与「不是合法 YAML」是两条不同的红灯，
        # 措辞必须分开——前者说文件根本没被读进来，后者说文件内容写坏了，排查方向相反。
        # 静默跳过是第三种、也是最坏的一种：它让「没检查」长得像「检查通过了」。
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            n_skipped += 1
            out.append(("warn",
                        f"{label} **读不出来**——不是 UTF-8 文本或读取失败"
                        f"（{type(e).__name__}）。本项**没有**检查这份文件，"
                        f"这不是它通过了。用 UTF-8 重存该文件即可"))
            continue
        kind, info = probe.probe_yaml_text(text)
        if kind == probe.OK:
            n_ok += 1
        elif kind == probe.PLACEHOLDER:
            out.append(("error",
                        f"{label} 残留未渲染的模板占位符 `{info}`——这不是 YAML 语法问题，"
                        f"是生成这份文件时漏替换了一个占位符；替换成真实值即可"))
        else:
            hint = ("。控制字符多半是编辑时把分隔空格误写成了不可见字符，"
                    "删掉重打那一处即可"
                    if "ReaderError" in info else
                    "。常见成因：双引号标量里有未转义的裸双引号导致标量提前终止、"
                    "用 tab 缩进、或值里有未加引号的特殊字符")
            extra = ("。**board.yaml 尤其要紧**：`recompute_board_summary.py` 用真解析器"
                     "读它，解析失败会落进逐行降级路径，summary 数字可能与真解析器"
                     "的读数不一致" if label.endswith("board.yaml") else "")
            out.append(("error", f"{label} **不是合法 YAML**：{info}{hint}{extra}"))
    if not [x for x in out if x[0] == "error"]:
        # 有跳过时**不发 ok**：一条 `✓ 0/1 份可解析` 读起来像「都查过了」，
        # 而实际是「一份都没查成」。跳过的份数必须出现在结论里，不能只靠上面那条 warn。
        lvl = "warn" if n_skipped else "ok"
        skipped = (f"；**另有 {n_skipped} 份读不出来、未被检查**（见上）" if n_skipped else "")
        out.append((lvl, f"{n_ok}/{len(targets)} 份项目数据文件经真 YAML 解析器可解析"
                         f"（PyYAML {detail}）{skipped}。**只证明是合法 YAML，不证明正则读数"
                         f"与真解析器一致**——重复键这类形态本项看不见"))
    return out


def check_notes_backlog(ctx):
    """notes 积压：内容行数 + 距**该角色**上次 memory_promoted 天数。

    与 check-iteration-trigger 同阈值但取数口径不同（按角色 vs 全局、从未提升算不算陈化），
    差异与理由见文件顶部 NOTES_STALE_DAYS 旁注释——那不是漂移，是诊断与触发的定位差别。
    """
    P = ctx["paths"]
    if not P.memory.is_dir():
        return [("info", f"agent-memory/ 不存在{_no_data_why(P)}")]
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
        return [("info", f"agent-memory/ 不存在{_no_data_why(P)}")]
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
    指针行天然豁免（它们指向角色记忆目录，本就不对应 auto-memory 主题文件）。
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

        # scope 级指针的**文件级**存在性（6.2）：指针指向 .workframe/agent-memory/<scope>/MEMORY.md，
        # 目标域被误删时指针就成了死指针，而上面的对账只管 auto-memory 内部文件、查不到它。
        # 只做文件级、不做条目级——[纠正] 条目永不清理，条目级 dangling 概率极低，
        # 为它扫全文不划算。
        # 两种布局都认，且目录名**由 _state_io 现算**——写死字面就是又一个事实源。
        # 旧布局的**框架形态**指针（scope 是 shared 或出厂角色）单独报：框架只读新位置，迁移工具又不改
        # auto-memory（归主会话维护），这些指针迁移之后必然成死指针，只能手改——不单列的话它们混在死指针里，
        # 没人知道该怎么修。旧位置上别的 scope 是 Claude Code 子 agent `memory: project` 的正当位置
        # （`legacy_state_dir` 对它说「别搬」），照旧按存在性判。scope 口径与 `_legacy_memory_is_framework` 同一份
        # （`_is_framework_scope`）
        mem_new, mem_old = new_rel("memory"), legacy_rel("memory")
        _mem_dirs = "|".join(re.escape(r) for r in (mem_new, mem_old))
        ptr_targets = set(re.findall(
            rf"`((?:{_mem_dirs})/[^`]+MEMORY\.md)`", idx_text))
        old_ptrs = sorted(t for t in ptr_targets if t.startswith(mem_old + "/")
                          and _is_framework_scope(t[len(mem_old) + 1:].split("/", 1)[0]))
        dead = sorted(t for t in ptr_targets if t not in old_ptrs and not (P.project / t).exists())
        if old_ptrs:
            out.append(("warn", f"{len(old_ptrs)} 条 scope 指针还写着旧记忆目录 `{mem_old}/`（框架只读 `{mem_new}/`，"
                                f"迁移工具不改 auto-memory）: {'、'.join(old_ptrs)}——迁移之后手改：把指针里的 "
                                f"`{mem_old}/` 换成 `{mem_new}/`"))
        if dead:
            out.append(("warn", f"{len(dead)} 条 scope 指针指向不存在的记忆文件（死指针）: "
                                f"{'、'.join(dead)}"))
        elif ptr_targets and not old_ptrs:
            out.append(("ok", f"{len(ptr_targets)} 条 scope 指针目标齐全"))
    out.append(("ok" if not any(l == "warn" for l, _ in out) else "info",
                f"体量: {len(entries)} 条 / {total} 字符(≈{int(total * TOKEN_PER_CHAR)}tok)"))
    return out


def check_promise_producer(ctx):
    """承诺-producer 对账 + 死信号三态标注（G1#3 规格）：
    normal（有发生）/ never-triggered starved（零发生但承诺在，上游饥饿）/
    broken promise（零发生且**插件内**找不到 producer 承诺文本）。

    注意扫描面是 **PLUGIN_ROOT**（插件自身的 skills / scripts），不是用户项目——
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
    for pattern, base in (("skills/**/SKILL.md", PLUGIN_ROOT),
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
#
# 凡落在运行态两个目录下的条目，都写成**相对该目录的**片段（`_MEM_REQUIRED` /
# `_STATE_RUNTIME`），由 `check_skeleton` 接上 `P.memory_rel` / `P.state_rel`——
# 目录位置只留 `_state_io` 一个事实源，这里写死字面就是第二个。
SCAFFOLD_REQUIRED_FILES = ["projects/board.yaml"]
_MEM_REQUIRED = ["shared/MEMORY.md", "shared/notes.md"]
# 运行时骨架：**整个目录被 gitignore**（本地派生状态，设计上不进 git），所以
# clone 出来的项目必然没有它们——把这几项列成硬需求会让「同事加入」场景**必然报 error**。
# 四个文件都能自愈（各消费方写入前都 mkdir(parents=True) 并落初值），所以判据是：
#   hook 跑过（有 plugin-root.txt）却还缺 → 真问题 error
#   hook 没跑过 → info「重启后自动生成」
_STATE_RUNTIME = ["activity-state.json", "events.jsonl",
                  "memory-index.json", "skill-metrics.yaml"]
# 软骨架：缺了不影响跑，重跑 scaffold 可补
# 注意 `logs/.gitkeep` **不在此列**——`logs/` 整个被 gitignore，clone 后永远不会有它，
# 而写日志的 hook 自己会 mkdir。列进来等于给每个克隆项目挂一条永远消不掉的 warn。
# 注意 `projects/dev-log.md` **也不在此列**——它是 scaffold 的产出，但属于**条件性产物**：
# 该不该存在由 `.workframe-config.json` 的 `close_check.ledger` 决定，而那个值可以指向
# 任意路径（能配置的全部意义就在于账本可以放在别处）。本清单的消费方式是无条件查存在性，
# 容不下这种成员：把账本放在别处的项目一样都不缺，却会被报「缺账本」，而照那条 warn 去
# 重跑 scaffold 只会塞进一个永不被写的空账本——渲染循环不看配置值，无条件创建。
# 账本该不该有、有没有按时记，归 close-check `[ledger]` 项管（那里有完整的 git 活动条件化判定），
# 骨架完整性检查不该重复一遍口径更差的版本。
SCAFFOLD_OPTIONAL_FILES = [
    "projects/specs/overview.md", "projects/specs/_meta/taxonomy.md",
    "projects/issues/TEMPLATES.md",
    "projects/changelog.md", "projects/evals/README.md",
    # scaffold 建的治理空目录（.gitkeep 占位）。消费方写入前都会 mkdir 兜底，所以缺了
    # 不致命——但「scaffold 产出什么」与「doctor 查什么」是两处手写，不列进来就没人
    # 发现漂移（check_scaffold_gitkeep_dirs_covered 已对账两边）。
    # 与 logs/.gitkeep 的区别：这些在 projects/ 下、随 git 分发，clone 后本就该有。
    "projects/evals/agents-md/.gitkeep", "projects/evals/skills/.gitkeep",
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
GITIGNORE_REQUIRED_FIXED = (".claude/settings.local.json", "logs/", "tmp/", ".tmp/")


def gitignore_required(P):
    """必需的 .gitignore 条目：固定四项 + 状态目录 + **链接形态时**的 `.claude/skills`（无尾斜杠）。

    状态目录那项取 `P.state_rel`（`_state_io` 现算），不在这里写死字面。
    `.claude/skills` 那项同理：只有它是 junction / symlink（新建项目，真实源在 `.agents/skills/`）
    时才要求——Windows 上 git 把 junction 当目录、会把目标里的文件再登记一份，clone 出来就是第二份
    真实拷贝；POSIX 上它是一个链接对象，不忽略就会被 `git add -A` 带进提交。已装项目的
    `.claude/skills/` 是真目录、本就该进 git。形态判定与条目字面都委托 scaffold（规则只留一份），
    取不到时按「不要求」——宁可少判一条，不能让体检自己崩。
    """
    entries = GITIGNORE_REQUIRED_FIXED + (P.state_rel + "/",)
    try:
        ps = _import_scaffold()
        if ps.skills_link_present(P.project):
            entries += (ps.SKILLS_LINK_IGNORE_LINE,)
    except Exception:
        pass
    return entries
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
    legacy = _has_legacy(P)
    missing_req = [f for f in SCAFFOLD_REQUIRED_FILES if not (P.project / f).is_file()]
    missing_req += [f"{P.memory_rel}/{f}" for f in _MEM_REQUIRED
                    if not (P.memory / f).is_file()]
    for role in BASELINE_ROLES:
        for fn in ("MEMORY.md", "notes.md"):
            if not (P.memory / role / fn).is_file():
                missing_req.append(f"{P.memory_rel}/{role}/{fn}")
    if missing_req:
        out.append(("error", f"硬骨架缺 {len(missing_req)} 项（hooks / agent 启动协议强依赖）: "
                    + "、".join(missing_req[:6]) + ("…" if len(missing_req) > 6 else "")
                    + (f"（{_LEGACY_FIX_POINTER}）" if legacy else "（重跑 project_scaffold.py 可补齐）")))
    missing_opt = [f for f in SCAFFOLD_OPTIONAL_FILES if not (P.project / f).is_file()]
    if missing_opt:
        out.append(("warn", f"软骨架缺 {len(missing_opt)} 项（不影响运行）: " + "、".join(missing_opt)))
    # 运行时骨架：缺失是否算问题，取决于 hook 到底跑过没有
    missing_rt = [f"{P.state_rel}/{f}" for f in _STATE_RUNTIME
                  if not (P.state / f).is_file()]
    if missing_rt:
        hooks_ran = (P.state / "plugin-root.txt").exists()
        if hooks_ran:
            # **两种可能都列，别断言是哪一种。** `plugin-root.txt` 由 SessionStart 写，而这几项里
            # 有一部分要等**会话收尾**（SessionEnd）才落笔——首个会话跑到一半时「hook 跑过」与
            # 「这几项还没生成」同时成立，那一刻并没有任何东西写失败。判据没换（见下方 else 支
            # 的对照），只是不再把「尚未生成」这一半说成是写入失败。
            out.append(("error", f"运行时骨架缺 {len(missing_rt)} 项，但 hook 已跑过（有 plugin-root.txt）"
                                 f"——两种可能：① 其中一部分由会话收尾生成，本会话还没跑到那一步；"
                                 f"② 写入失败。完整跑完一次会话（含收尾）后仍缺即属后者: "
                                 + "、".join(missing_rt)
                                 + (f"（{_LEGACY_FIX_POINTER}）" if legacy else "")))
        else:
            out.append(("info", f"运行时骨架尚未生成 {len(missing_rt)} 项——该目录不进 git，"
                                "clone 下来没有属正常，首个会话由 hook 自动补齐"))
    missing_param = [f for f in SCAFFOLD_PARAM_FILES if not (P.project / f).exists()]
    if missing_param:
        # 接入已有项目时用户可能自带 CLAUDE.md 或不需要周边资产层，故为 warn 非 error
        out.append(("warn", "对话渲染产物缺 " + "、".join(missing_param)
                    + "（走 launcher 创建流程会生成；接入已有项目时可能是有意为之）"))
    # 占位符残留：scaffold 写入时已断言零残留，这里只兜底用户手改。AGENTS.md 每条装机路径都
    # 渲染（不止 --params），不在上面的渲染产物清单里，单独补进扫描面
    residue = []
    for f in SCAFFOLD_PARAM_FILES + ["projects/modules/overview.md", "AGENTS.md"]:
        target = P.project / f
        if target.exists():
            left = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", target.read_text(encoding="utf-8", errors="replace"))))
            if left:
                residue.append(f"{f} → {', '.join(left[:3])}")
    if residue:
        out.append(("warn", "模板占位符未替换（scaffold 写入时会断言，出现说明是手改）: " + "；".join(residue)))
    if not out:
        out.append(("ok", f"骨架齐全："
                    f"{len(SCAFFOLD_REQUIRED_FILES) + len(_MEM_REQUIRED) + len(BASELINE_ROLES) * 2} 硬 / "
                    f"{len(_STATE_RUNTIME)} 运行时 / {len(SCAFFOLD_OPTIONAL_FILES)} 软 / "
                    f"{len(SCAFFOLD_PARAM_FILES)} 渲染产物，无占位符残留"))
    return out


AGENTS_MD_WHAT = "这个项目是什么"
AGENTS_MD_SECTIONS = (AGENTS_MD_WHAT, "项目自有判据", "委派子 agent", "两扇门的差异")
# 「这个项目是什么」只剩占位时各处出路文案共用的几段（doctor / 门 / 迁移三个消费方，文案只在这里写一份）
MERGE_GUIDE = PLUGIN_ROOT / "reference" / "claude-md-merge-guide.md"
AGENTS_MD_NO_PROXY_EDIT = "改 AGENTS.md 要用户本人确认，模型不代填"
AGENTS_MD_NO_WHOLESALE = ("别把 CLAUDE.md 整段复制过去——那会把框架段落的旧副本与只对 Claude Code 成立的内容"
                          "一起带进 AGENTS.md")


def agents_md_goal_state(text):
    """AGENTS.md「这个项目是什么」段是不是只剩框架写下的占位：`"unfilled"` / `"filled"` / `"unparsed"`。

    **判据只认 `project_scaffold.AGENTS_MD_FALLBACK_GOAL`**（无参渲染时框架自己写下的占位，调用时现取、不写字面）：
    判定方手抄一份字面时，写入方一改常量，判定方就对每一份骨架报「已填」。**`AGENTS_MD_FALLBACK_BLANK` 不是占位
    标记**——带参新建而判据留空时它照样会写，拿它当标记每个新项目都误报。
    段范围：标题行到下一个 `#` / `##` 标题（`### 业务背景` 算在段内——只填了背景的项目，模型拿得到项目信息）。
    内容行：先剥 HTML 注释，去掉空行与围栏外的标题行，围栏里的行算内容；逐行 `strip()` 后与占位比 `==`。
    空段算 `unfilled`；同名段有多个时**全部**只剩占位才算 `unfilled`。
    标题只按整行（容行尾空白）、只在围栏外认；认不出返回 `"unparsed"`——`AGENTS_MD_SECTIONS` 那一判是子串，
    标题带后缀 / 缩进 / 降级时那边说「段在」、这边找不到，这一格不静默放过。
    **能力边界**（按 CC 口径判，都要求非常规手改，框架自己的写入方产不出）：
      - 只把项目描述写在段内 HTML 注释里 ⇒ `unfilled`（CC 注入前剥块级注释；Codex 不剥、看得见）；
      - 占位被改成别的无意义字样（`_（待填）_`、`- （待填）`、后接零宽空格）⇒ `filled`（漏报）；
      - `~~~` 围栏不认（`_outside_fence` 只认 ```）：前面 `~~~` 围栏里放同名标题与正文 ⇒ 可能 `filled`；
      - 下一段写成 setext 标题 ⇒ 段吞进下一段正文，`filled`；
      - 标题上方任一处没闭合的 `<!--`（含 code span 里的 `<!--` 字面与后面一条真注释配对）⇒ 整段被截，`unparsed`。
    判的是「占位还在不在」，不判写的内容对不对、全不全。
    """
    ps = _import_scaffold()
    lines = _strip_html_comments(_normalize_md(text)).split("\n")
    outside = list(ps._outside_fence(lines))
    heading = f"## {AGENTS_MD_WHAT}"
    starts = [i for i, ln in outside if ln.rstrip() == heading]
    if not starts:
        return "unparsed"
    bounds = [i for i, ln in outside if re.match(r"#{1,2}\s", ln)]
    heads = {i for i, ln in outside if re.match(r"#{1,6}\s", ln)}
    content = []
    for s in starts:
        e = next((i for i in bounds if i > s), len(lines))
        content += [lines[i].strip() for i in range(s + 1, e) if lines[i].strip() and i not in heads]
    return "unfilled" if all(t == ps.AGENTS_MD_FALLBACK_GOAL for t in content) else "filled"


def agents_md_goal_of(project):
    """项目根 AGENTS.md 的占位状态：`"absent"`（文件不在）或 `agents_md_goal_state` 的三种。异常原样抛，由调用方隔离。"""
    f = Path(project) / "AGENTS.md"
    if not f.exists():
        return "absent"
    return agents_md_goal_state(f.read_text(encoding="utf-8", errors="replace"))


def agents_md_goal_fact(state):
    """确定性那一半的事实陈述（只陈述，不断言模型知道什么）。`state` 只接 `"unfilled"` / `"absent"`。"""
    if state == "absent":
        return ("项目根没有 AGENTS.md——Codex 读 AGENTS.md、不读 CLAUDE.md 与 `.claude/rules/`，此刻本项目的 Codex 会话"
                "拿不到任何项目内容；下一个会话启动时会补建一份（会话启动每次都查、文件不在就建），补建出来的「这个项目是什么」"
                "是框架占位，项目内容仍要写进去")
    return (f"AGENTS.md「{AGENTS_MD_WHAT}」仍是框架补建时的占位（`{_import_scaffold().AGENTS_MD_FALLBACK_GOAL}`）——"
            f"两扇门的模型读到的项目介绍就是这一行（Codex 读 AGENTS.md，不读 CLAUDE.md 与 `.claude/rules/`）")


def agents_md_goal_outlet():
    """不看启发式结果时的通用出路（门的 `!` 行与迁移的报告项用；doctor 按启发式结果切换，见 `check_agents_md`）。"""
    return (f"项目内容在 CLAUDE.md 或 `.claude/rules/` 里的，按 {MERGE_GUIDE} 逐段归位（{AGENTS_MD_NO_WHOLESALE}）；"
            f"别处也没有的，直接写上这个项目做什么、给谁用。{AGENTS_MD_NO_PROXY_EDIT}")


def cc_only_surfaces(project):
    """只有 Claude Code 读得到的项目内容面：`(CLAUDE.md 里模板之外的 `## ` 标题, .claude/rules/ 下的 md 相对路径)`。

    **启发式，只报不判对错**：整合规范允许只对 CC 成立的内容留在 CLAUDE.md，只用 CC 的项目的 rules 也合法——
    所以它不单独成项（成项就是一条永远消不掉的提示），只给 doctor「这个项目是什么」warn 当「可能的来源」、
    给门的成功出口当一行 `i`。
    模板标题从 `templates/claude-md-template.md` 现算（剥注释、围栏外、整行 `## `），不写死清单。
    CLAUDE.md 里的标题按「所在段是不是框架段落旧副本」（`_w3_residue_blocks`）排序：非旧副本的排前——迁移过来的
    项目里 1.0.0 的框架段标题会混在列表里，列名只取前几个时不让它们占位。
    `.claude/rules/` 排除纪律镜像目录（`RULES_MIRROR_PARTS`，由 `rules_mirror_residue` 另报）。
    **能力边界**：只数这两处；`CLAUDE.local.md`、`.claude/CLAUDE.md` 也只有 CC 读，不列（前者通常是个人私有文件）。
    """
    project = Path(project)
    ps = _import_scaffold()

    def h2_sections(text):
        lines = _strip_html_comments(_normalize_md(text)).split("\n")
        outside = list(ps._outside_fence(lines))
        h2 = [(i, ln[2:].strip()) for i, ln in outside if re.match(r"##\s", ln)]
        bounds = [i for i, ln in outside if re.match(r"#{1,2}\s", ln)]
        out = []
        for i, title in h2:
            e = next((b for b in bounds if b > i), len(lines))
            out.append((title, "\n".join(lines[i:e])))
        return out

    tpl = PLUGIN_ROOT / "templates" / "claude-md-template.md"
    known = {t for t, _b in h2_sections(tpl.read_text(encoding="utf-8"))}
    heads = []
    cm = project / "CLAUDE.md"
    if cm.is_file():
        secs = [(t, b) for t, b in h2_sections(cm.read_text(encoding="utf-8", errors="replace")) if t not in known]
        heads = [t for t, b in sorted(secs, key=lambda tb: bool(_w3_residue_blocks(tb[1])))]
    rules = []
    rdir = project / ".claude" / "rules"
    mirror = project.joinpath(*RULES_MIRROR_PARTS)
    if rdir.is_dir():
        for f in sorted(rdir.rglob("*.md")):
            if f.is_file() and mirror not in f.parents:
                rules.append(f.relative_to(project).as_posix())
    return heads, rules


def cc_only_line(surfaces):
    """`cc_only_surfaces` 结果的一句人话；两处都空时返回 None。"""
    heads, rules = surfaces
    parts = []
    if heads:
        parts.append(f"CLAUDE.md 里模板之外的二级段 {len(heads)} 个（{'、'.join(heads[:3])}{'…' if len(heads) > 3 else ''}）")
    if rules:
        parts.append(f"`.claude/rules/` 下 {len(rules)} 份 md（{'、'.join(rules[:3])}{'…' if len(rules) > 3 else ''}）")
    return "、".join(parts) or None

# `@<file>` 导入的形态判定在 `project_scaffold.md_path_imports`（全仓唯一一份，三个消费方
# 共用）；「文档里哪些位置可能承载导入」在下面的 `_md_import_surface`。本文件不再留第二份正则。


def check_agents_md(ctx):
    """`AGENTS.md` 的四个框架契约段必须在，且正文不得出现 `@<file>` 导入。

    **为什么单列而不是并进 `claude_md`**：这一份是**两扇门共用**的项目自有判据落点，
    另一扇门原生读它、不读 CLAUDE.md。并进去的话，一个只跑另一扇门的项目会因为
    「CLAUDE.md 不存在」先走 warn 分支，本项的四段根本不被检查。

    **委派段单独点名**的理由是它的失效形态最隐蔽：另一扇门的模型按自己的 `spawn_agent`
    说明**不会主动 spawn**，缺了这一段它就一路自己做完——而「没派」与「派了但没用上」
    在终端里长得一模一样，没有任何一处会提醒。

    **`@<file>` 导入必须零**：有一扇门不展开它，那行字面串会原样进上下文；两扇门因此
    读到不同的内容，而两边都不报错。要引入的内容只有两条路——直接内联，或走注入片。
    CC 侧这一条是**递归**的：AGENTS.md 自己经 CLAUDE.md 那行导入进来，而被导入的文件还能
    再导入（官方 `memory.md:97`，最多四跳），所以 AGENTS.md 里的 `@<path>` 真的会被展开。

    **导入判据是「路径形在行内任意处」，不是「行首」**（官方 `memory.md:99/101/104/107`：
    `reference them with @ syntax anywhere`，示例里就有行中与列表项中的导入）。形态判定在
    `project_scaffold.md_path_imports`，位置剥离在 `_md_import_surface(text, "scan")`。
    **能力边界（两档，本项报 ok 时这两档都不在保证范围内）**：① 官方的 `@README` 这类
    **不带斜杠也不带扩展名**的裸词导入认不出——它与 `@dev` / `@qa` / `@角色名` 在文本上
    完全同形，硬报会让每个提到角色名的项目常年假红，而假红最后会被「修」成永远绿；
    ② 围栏代码块 / 行内 code span / 4 空格缩进块里出现 `<!--` **字面**时，它会与文档后面
    一条真注释的 `-->` 配成一对，中间的**普通正文**连同其中的导入一起被当注释剥掉（见
    `_md_import_surface` 末段、TASK-114）。两档都只能靠人。
    **不在这张表里的一项**：写在块级 HTML 注释里的导入认不出**不是**能力边界——CC 注入前
    就剥掉块级注释，那里的导入对 CC 不存在，不报它是正确行为。

    **「这个项目是什么」只剩框架占位报 warn**（判定在 `agents_md_goal_state`，能力边界见那里）：从上一版升级的
    项目，AGENTS.md 由会话启动补建或迁移建出，都是无参渲染的骨架，而项目内容还在 CLAUDE.md / `.claude/rules/`——
    Codex 不读那两处。四段齐全照样成立，只看标题就是假绿。文案只陈述事实，写明「要用户本人确认、模型不代填」：
    收口纪律要求 doctor 全绿，这条 warn 的读者多半是模型。标题认不出报 info（没判）；这一判出异常时降成一条
    info，四段与导入的结论不受影响；给出路用的启发式（`cc_only_surfaces`）出异常时 warn 照出、写明「没数成」、
    给不看来源的通用出路；「这个项目是什么」本身缺失时不判（已由 error 报出）。
    """
    P = ctx["paths"]
    if not P.agents_md.exists():
        return [("error", "AGENTS.md 不存在——两扇门都失去项目自有判据与委派授权；"
                          "CC 侧还会让 CLAUDE.md 里那行 `@AGENTS.md` 导入落空。"
                          "会话启动时的 scaffold 补建路径本应自动补上它，没补上说明那条路径没跑到")]
    text = P.agents_md.read_text(encoding="utf-8", errors="replace")
    out = []
    missing = [s for s in AGENTS_MD_SECTIONS if f"## {s}" not in text]
    if missing:
        why = ("；**其中「委派子 agent」是显式授权段**：缺了它，另一扇门的模型按自己的说明"
               "不会主动 spawn，失败与正常同形" if "委派子 agent" in missing else "")
        out.append(("error", f"AGENTS.md 缺契约段 {missing}{why}"))
    imports = agents_md_import_hits(text)
    if imports:
        out.append(("error", f"AGENTS.md 正文有 {len(imports)} 处 `@<path>` 导入（{imports[:3]}）"
                             f"——CC 会展开它（被导入的文件还能再导入，最多四跳），另一扇门不展开，"
                             f"那行字面串原样进上下文：两扇门读到的内容就此不同且都不报错。"
                             f"改为直接内联或走注入片；只是想提路径就用反引号包起来"))
    goal, extra = None, []
    if AGENTS_MD_WHAT not in missing:
        try:
            goal = agents_md_goal_state(text)
        except Exception as e:
            extra.append(("info", f"「{AGENTS_MD_WHAT}」是不是占位没判成（{type(e).__name__}: {e}）"
                                  f"——四段与导入的结论不受影响"))
    if goal == "unfilled":
        # 启发式那一半只决定出路怎么说；它出异常时**说没数成**、给不看来源的通用出路（与门那一侧的「i 没数成」同口径），
        # 不静默退回「写上」——那会把「内容其实在 CLAUDE.md 里」的项目引去另写一份
        try:
            heads, rules = cc_only_surfaces(P.project)
            src_err = None
        except Exception as e:
            heads, rules, src_err = [], [], e
        src = cc_only_line((heads, rules))
        if src_err is not None:
            advice = (f"只有 CC 读得到的内容面没数成（{type(src_err).__name__}: {src_err}），来源列不出。"
                      f"出路：{agents_md_goal_outlet()}")
        elif src:
            # CLAUDE.md 那两句只在 CLAUDE.md 里真有模板外的段时才说；只有 rules 时说 rules 自己的去留
            advice = (f"可能的来源：{src}——按 {MERGE_GUIDE} 逐段归位"
                      + (f"，{AGENTS_MD_NO_WHOLESALE}" if heads else "")
                      + "；归位后"
                      + "、".join((["CLAUDE.md 里那几段"] if heads else []) + (["那几份 rules"] if rules else []))
                      + f"删不删由你定，留着的话 CC 会读到两份。{AGENTS_MD_NO_PROXY_EDIT}")
        else:
            advice = f"出路：写上这个项目做什么、给谁用、处在什么阶段。{AGENTS_MD_NO_PROXY_EDIT}"
        extra.append(("warn", f"{agents_md_goal_fact('unfilled')}。{advice}"))
    elif goal == "unparsed":
        extra.append(("info", f"「{AGENTS_MD_WHAT}」的标题行认不出（标题带后缀 / 缩进 / 降级，或它前面有没闭合的 "
                              f"`<!--`）——本项没判这一段是不是框架占位"))
    if out:
        return out + extra
    return [("ok", f"AGENTS.md 四段齐全（{'/'.join(AGENTS_MD_SECTIONS)}），"
                   f"正文无路径形 `@<path>` 导入"
                   f"（裸词导入如 `@README`、以及围栏/code span/缩进块里的 `<!--` 字面之后"
                   f"到下一处 `-->` 之间的正文，本项认不出，见 docstring）"
                   + (f"；「{AGENTS_MD_WHAT}」已写" if goal == "filled" else ""))] + extra


# 任务状态流转表的行形 `| \`<左态> → <右态>\` | <允许操作者> |`。**全仓一份**：validate 的
# `signoff_table_alignment` 抽流转行、本文件认 CLAUDE.md 里的旧流转表，都用这一个——两处各写
# 一条正则时，一边认得出的行另一边认不出，症状是「断言全绿但它根本没读那一行」。
# 左态允许「任意」：`任意 → blocked` / `任意 → cancelled` 两行只认 `[a-z_]+` 就不进对账面。
FLOW_ROW_RE = re.compile(r"\|\s*`((?:[a-z_]+|任意) → [a-z_]+)`\s*\|(.+)\|")

# CLAUDE.md 里一处有效的 AGENTS.md 导入。`@` 前不能是字母数字 / `@` / 反斜杠（邮箱、转义）；
# `AGENTS.md` 后**只认空白或行尾**——CC 的路径在哪里截断官方没写，`@AGENTS.md。` 这种紧跟
# 标点的写法可能被读成另一个不存在的文件，拿不准就按「未导入」判。
_AGENTS_IMPORT_RE = re.compile(r"(?<![\w@\\])@(?:\./)?AGENTS\.md(?=\s|$)")
_CODE_SPAN_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")


def _normalize_md(text):
    """去 BOM、CRLF / 裸 CR → LF。本文件两个 CLAUDE.md 判定都先过这一步。"""
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def _strip_html_comments(text):
    """去掉全部 HTML 注释（块级与行内都去）；没闭合的 `<!--` 之后整段一并去掉。

    CC 注入前会剥掉块级注释，注释里的 `@AGENTS.md` 导不导入官方没写；行内注释连剥不剥都没写。
    两处都拿不准，一律当成不存在——对导入判定这是「按未导入判」，对残留判定这是「被注释掉的
    旧副本不算双份」（注入前就被剥了）。

    **未闭合 `<!--` 那条截断是残留识别（`_w3_residue_blocks`）唯一的假绿方向**：截断点之后
    的旧副本一律看不见，doctor 于是说「无旧副本」。触发条件不必是畸形文档——围栏代码块里
    出现一个孤立的 `<!--` 字面就够。其余各处的拿不准都往「多报」跑，只有这一处往「少报」跑。
    没有改成状态机是因为残留只出 warn；改的时候连 `_w3_residue_blocks` 的能力边界一起改。
    """
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    cut = text.find("<!--")
    return text if cut < 0 else text[:cut]


def _md_import_surface(text, undocumented):
    """逐行产出「CC 可能把 `@<path>` 当成导入」的文本区（已剥掉不算导入的部分）。

    **本文件里「导入可能落在哪儿」的唯一实现**，两个方向的判定都建在它上面；形态那一半
    （什么字面算路径）在 `project_scaffold.md_path_imports`。

    **剥块级 HTML 注释是对的，两个方向都剥，而且不是保守选择而是正确语义。** 官方
    `memory.md`：「Block-level HTML comments (`<!-- maintainer notes -->`) in CLAUDE.md files
    are **stripped before the content is injected into Claude's context**. … **Comments
    inside code blocks are preserved.**」⇒ 注释里的 `@<path>` CC 自己就看不到，报它才是
    误报。**被导入的文件同样剥**（官方那句主语是 CLAUDE.md，这一格由实测补上：一份 19,420
    字节的 `AGENTS.md` 经 `@AGENTS.md` 导入后，会话首轮 `instructions` 载荷恰为「磁盘字符数
    减去全部 HTML 注释」）。**剥离与展开的先后官方没写**，是仍未知的一格。

    官方另写明两种跳过：**围栏代码块与行内 code span**——这两种两个方向都跳。其余三种
    （`~~~` 围栏、行首缩进 ≥4、落单反引号之后）官方一个字都没写，由 `undocumented` 定，
    **而它要按「判错了哪一侧看得见」来选**：
      - `"skip"`：拿不准就当它不是导入。用在「有没有导入 AGENTS.md」那一问上——判错的方向
        是「判成未导入」⇒ 一条看得见的 error。
      - `"scan"`：拿不准就照扫。用在「AGENTS.md 里有没有**任何**导入」那一问上——判错的
        方向是「判成没有导入」⇒ 静默放过，两扇门从此读到不同内容而两边都不报错。
    两个取值都指向同一件事：宁可多报一条看得见的红。所以同一段文本在两个方向下的结论**本
    来就可以不同**，那不是不一致。

    **别把「剥注释」当成缺口去修。** 它被当成缺口修过两次，两次都引入真缺陷：不剥注释
    内容之后，注释里的围栏行会进 `_outside_fence` 的状态机，而那台状态机认的是
    `ln.lstrip().startswith("```")`——**任意缩进、任意 ≥3 个反引号**；任何窄于这个识别面的
    预处理（例如只认列 0 的、恰好 3 个反引号的）都会把原本成对的围栏剩成奇数，围栏从此
    永不闭合，丢掉那一点**之后的全部内容**。

    **真正的已知缺口在注释的边界识别上，不在剥不剥**：下面那条 `<!--.*?-->` 不认围栏、
    不认行内 code span、不认 4 空格缩进块，所以这些地方出现的 `<!--` **字面**会与文档后面
    任意一条真注释的 `-->` 配成一对，把中间的**普通正文**连同其中的导入一起吞掉——方向是
    假绿，半径是「那一点到下一处 `-->` 之间的全部内容」。已登记 TASK-114；修它要先给出
    「注释边界识别面 ⊇ 各种 `<!--` 字面宿主」的证明。
    """
    body = _normalize_md(text)
    body = (_strip_html_comments(body) if undocumented == "skip"
            else re.sub(r"<!--.*?-->", " ", body, flags=re.S))
    outside_fence = _import_scaffold()._outside_fence
    for _i, ln in outside_fence(body.split("\n")):
        if undocumented == "skip":
            # `_outside_fence` 只认 ```；`~~~` 围栏它不认，为了不写第二套状态机，第一个 `~~~`
            # 行之后的内容一律不算（宽于真实语义，在这个方向上是多报）
            if ln.lstrip().startswith("~~~"):
                return
            if ln.startswith(("    ", "\t")):
                continue
        ln = _CODE_SPAN_RE.sub(" ", ln)
        if undocumented == "skip" and "`" in ln:
            ln = ln[:ln.index("`")]     # 可能是跨行 code span
        yield ln


def claude_md_imports_agents_md(text):
    """CLAUDE.md 正文里有没有一处 CC 会展开的 `@AGENTS.md` / `@./AGENTS.md` 导入。

    按官方语义（导入写在代码 span 与围栏代码块之外、行内任意位置都算）判，**拿不准一律按
    「未导入」**（`_md_import_surface(..., "skip")`）：误报是一条看得见的 error，漏报是
    AGENTS.md 在 CC 里一行都读不到而没人知道。
    **目标专用的收尾判据**：`AGENTS.md` 之后只认空白或行尾——CC 的路径在哪里截断官方没写，
    `@AGENTS.md。` 可能被读成另一个不存在的文件。它比 `md_path_imports` 严，**有意不合并**：
    那一份答的是「有没有任何导入」（宁可多报），这一份答的是「有没有导入到 AGENTS.md」
    （宁可少认），两问的 fail-safe 方向相反。
    **能力边界**：只看这一份文本。`.claude/CLAUDE.md` 与 `CLAUDE.local.md` 里的导入不计，
    嵌套导入（导入的文件再导入 AGENTS.md）不认。
    """
    for ln in _md_import_surface(text, "skip"):
        if _AGENTS_IMPORT_RE.search(ln):
            return True
    return False


def agents_md_import_hits(text):
    """AGENTS.md 正文里**路径形** `@<path>` 导入的字面量清单（按出现顺序，没有则空表）。

    与 `claude_md_imports_agents_md` 共用位置剥离层、共用 `md_path_imports` 的形态判定，
    只在「拿不准怎么办」上取反（见 `_md_import_surface` 的 `undocumented`）：这一问漏报是
    静默的，所以 `~~~` 之后 / 缩进行 / 落单反引号之后一律照扫。
    **写在块级 HTML 注释里的 `@<path>` 本函数认不出，那是对的**——CC 注入前就把块级注释剥
    掉了（官方 `memory.md`），报它是误报。别把这一项当缺口修，它被当缺口修过两次。
    **已知缺口是另一件事**：注释的**边界识别**（`<!--.*?-->` 不认围栏 / 行内 code span /
    4 空格缩进块）会让这些宿主里的 `<!--` 字面与后面一条真注释的 `-->` 配对，吞掉中间的
    普通正文与其中的导入——方向是假绿，见 `_md_import_surface` 末段与 TASK-114。
    """
    md_path_imports = _import_scaffold().md_path_imports
    hits = []
    for ln in _md_import_surface(text, "scan"):
        hits.extend(md_path_imports(ln))
    return hits


# W3 残留识别用的锚点：只取**结构行**与出厂模板里自 v1.0.0 起未变过的原句，不取标题——
# 用户会改标题措辞；也不取留在新 CLAUDE.md 里的东西（快速入门的 `@dev 做技术方案`、
# 「指定 `@角色名` 则强制委派」都不是锚点）。
def _w3_residue_blocks(text):
    """CLAUDE.md 里还留着哪几块框架段落的旧副本（按固定顺序返回块名）。**只此一份**：
    旧副本的 warn 与缺导入 error 的旧 / 新形态文案都问它。

    四块 = 迁进注入片的那四块：通用角色表与路由规则 / 任务状态流转与签发权限 /
    两套记忆分工与落点判据 / 文档约定与收口清单。每块命中任一锚点即算：
      - 角色表：首格恰为 `@<出厂角色>` 的表行（名单从 `BASELINE_ROLES` 现算）；路由规则：
        「`@角色名` … 直接调度」那一句
      - 流转表：`FLOW_ROW_RE` 形态的行（与 validate 抽流转行同一条正则）
      - 记忆分工：首格以 `auto-memory` 或反引号包着的 `…agent-memory/…` 路径开头的表行；
        落点判据：首格以「工种知识」开头的表行（路径只认 `agent-memory/` 这一段：记忆目录的
        完整位置由 `_state_io` 现算，这里写死整段就是第二个路径源）
      - 文档约定：`document-norms`；收口清单：`close_check.required_fields`
    **最后那一块是例外，它用的是裸子串、不是结构行**：`document-norms` 与
    `close_check.required_fields` 在正文任何位置出现都算。于是**在自己 `CLAUDE.md` 里正当
    提到 `document-norms` 的项目会拿到一条永久 warn，而那里并没有旧副本**（实测：一份只是
    正当提到该 skill 的项目判据文件喂进来就报这一块；`check_claude_md` 只读项目根
    `CLAUDE.md`，所以这条路径上当前无害）。
    方向是假红、不是漏报，留着是因为这一块本来就没有稳定的结构行可锚。
    **漏报面**：项目把这几块改写过（换掉表行形态、改掉那几句原话）时认不出——只对上面这些
    锚点成立。HTML 注释里的不算（注入前就被剥掉，不构成双份）；**未闭合 `<!--` 之后的旧副本
    也看不见**，那是本块唯一的假绿方向，见 `_strip_html_comments`。
    """
    body = _strip_html_comments(_normalize_md(text))
    roles = "|".join(re.escape(r) for r in BASELINE_ROLES)
    blocks = []
    if (re.search(rf"^\s*\|\s*@(?:{roles})\s*\|", body, re.M)
            or re.search(r"`@角色名`[^\n]*直接调度", body)):
        blocks.append("通用角色表与路由规则")
    if any(FLOW_ROW_RE.match(ln) for ln in body.split("\n")):
        blocks.append("任务状态流转与签发权限")
    if re.search(r"^\s*\|\s*(?:auto-memory|`[^`|]*agent-memory/|工种知识)", body, re.M):
        blocks.append("两套记忆分工与落点判据")
    if "document-norms" in body or "close_check.required_fields" in body:
        blocks.append("文档约定与收口清单")
    return blocks


def check_claude_md(ctx):
    """CLAUDE.md 的契约只剩两项：**有一处有效的 `@AGENTS.md` 导入**；**没有框架段落的旧副本**。

    项目自有的内容全在 AGENTS.md（两扇门共用）；CC 只读 CLAUDE.md，那一行导入是 CC 侧读到
    AGENTS.md 的唯一入口。缺导入时文件在、hooks 在、agent 能路由，只是 AGENTS.md 在 CC 里
    一行都读不到——只查「文件存在」看不见，所以这里查导入。判级：
      - CLAUDE.md 存在但没有有效导入 → **一律 error**；只有修法文案按形态分（带旧副本的旧形态 /
        新形态），形态由 `_w3_residue_blocks` 判，**它只影响措辞、不影响判级**
      - CLAUDE.md 与 AGENTS.md 是同一个文件（官方认可的软链接接法）→ ok
      - CLAUDE.md 不存在 → warn（可能是只用另一扇门的项目）
      - 旧副本 → warn：那四块由会话启动时的注入片投递，副本与注入片形成双份
    **只看项目根的 CLAUDE.md**：`.claude/CLAUDE.md` 与 `CLAUDE.local.md` 里的导入不计。
    """
    P = ctx["paths"]
    if not P.claude_md.exists():
        return [("warn", "CLAUDE.md 不存在——只用另一扇门（它原生读 AGENTS.md）的项目可以没有它；"
                         "用 CC 的项目缺了它，AGENTS.md 一行都读不到。用 CC 就在项目根建 CLAUDE.md "
                         "并写一行 `@AGENTS.md`（`.claude/CLAUDE.md` 与 `CLAUDE.local.md` 本项不计）")]
    try:
        if P.agents_md.exists() and os.path.samefile(P.claude_md, P.agents_md):
            return [("ok", "CLAUDE.md 与 AGENTS.md 是同一个文件（软链接接法），CC 直接读到 AGENTS.md")]
    except OSError:
        pass
    text = P.claude_md.read_text(encoding="utf-8", errors="replace")
    residue = _w3_residue_blocks(text)
    out = []
    if not claude_md_imports_agents_md(text):
        hint = ""
        if "@AGENTS.md" in text or "@./AGENTS.md" in text:
            hint = ("（文件里有 `@AGENTS.md` 字样，但本项没把它算成导入。六种成因：在 ``` 代码块里 /"
                    " 在反引号里 / 在 HTML 注释里 / 行首缩进 ≥4 格 / 后面紧跟了标点 /"
                    " **它前面某处出现过 `~~~` 围栏行或没闭合的 `<!--`——那两种会让其后整段一并不算**，"
                    "后两种最难自己看出来，文件里搜一下这两个符号）")
        if residue:
            out.append(("error", "CLAUDE.md 没有有效的 `@AGENTS.md` 导入" + hint +
                        "——CC 只读 CLAUDE.md，AGENTS.md 里的内容（含今后按信号入账写进去的通用规则）"
                        "在 CC 会话里读不到。修法：在 CLAUDE.md 里加一行 `@AGENTS.md`，纯新增、不动原文；"
                        "项目目标 / 业务背景这类项目内容本来就不在框架段落里，加了导入也还留在 CLAUDE.md、Codex 读不到，"
                        f"要两扇门都读到的按 {MERGE_GUIDE} 归位进 AGENTS.md"))
        else:
            out.append(("error", "CLAUDE.md 没有有效的 `@AGENTS.md` 导入" + hint +
                        "——项目判据全在 AGENTS.md，CC 现在一行都读不到。修法：在 CLAUDE.md 里顶格"
                        "加一行 `@AGENTS.md`（别放进代码块、反引号或 HTML 注释）"))
    if residue:
        out.append(("warn", f"CLAUDE.md 还留着框架段落的旧副本：{'、'.join(residue)}——这几块现在由"
                            "会话启动时的注入片投递，与注入片形成双份：注入片随插件升级，这里的副本不会，"
                            "两份一分叉，模型按哪份做不可判。逐块确认后从 CLAUDE.md 删掉（夹在里面的"
                            "项目自有内容先挪进 AGENTS.md；不在旧副本里的项目目标 / 业务背景同样要归位，"
                            f"按 {MERGE_GUIDE}）；"
                            "删除要用户确认"))
    if out:
        return out
    return [("ok", "CLAUDE.md 有有效的 `@AGENTS.md` 导入，无框架段落的旧副本")]


def check_prd_framework(ctx):
    """项目 PRD 框架 skill（.claude/skills/prd-style/）在位性。

    缺失不是故障——prd-writer 会 fallback 到框架默认模板照常工作，故仅 info；
    scaffold 装机会自动放入，老项目 / 手工接入项目缺失属正常。
    """
    P = ctx["paths"]
    # 落点经 `project_skills_dir()`（与 scaffold / prd-writer 同一规则）：新建项目在 `.agents/skills/`
    # （`.claude/skills` 是指向它的链接），已装项目在 `.claude/skills/` 真目录
    # 两个目录不是同一份（副本 / 指向别处）由 `skills_link` 一项报，这里不重复
    skills = project_skills_dir(P.project)
    out = []
    f = P.project / skills / "prd-style" / "SKILL.md"
    if f.is_file():
        out.append(("ok", f"项目 PRD 框架在位（{skills.as_posix()}/prd-style/——写 PRD 按本项目框架执行，可自由修改）"))
    else:
        out.append(("info", f"未见项目 PRD 框架（{skills.as_posix()}/prd-style/）——写 PRD 将按框架默认执行；"
                            "需要本项目专属风格时，从 core 模板 templates/project-skills/prd-style/ 复制过来按需修改"))
    return out


def check_skills_link(ctx):
    """`.claude/skills` 的形态（Claude Code 读项目 skill 的唯一路径）。形态判定只在 `_harness.skills_cc_form`，
    本项只按形态定级别、给出路：

    | 形态 | 级别 | 说明 |
    |---|---|---|
    | 普通文件 | error | CC 读不到任何项目 skill |
    | 链接、目标不存在（本项目 `.agents/skills` 在） | error | 会话之外撞见；开一次会话 SessionStart 会自动重指 |
    | 链接指向别处（目标存在） | warn | CC 读别的目录的 skills |
    | 真目录、本项目 `.agents/skills` 也在 | warn | 两扇门各读一份；先比对再处理，不给删目录命令 |
    | 链接、本项目 `.agents/skills` 不在 | warn | 真实源没了 |
    另：`.claude/skills` 在 git 里以 `120000`（符号链接对象）被跟踪 ⇒ warn。
    """
    P = ctx["paths"]
    form = skills_cc_form(P.project)
    two = skills_two_copies(P.project)      # 「两份不是同一目录」只问这一处（反向链接不算）
    cc, neu = SKILLS_DIR_CC.as_posix(), SKILLS_DIR_NEUTRAL.as_posix()
    out = []
    try:
        target = os.readlink(P.project / SKILLS_DIR_CC)
        target = target[4:] if target.startswith("\\\\?\\") else target
    except (OSError, ValueError):
        target = "?"
    if form == SKILLS_FILE:
        out.append(("error", f"{cc} 是一个普通文件、不是指向 {neu} 的链接——Claude Code 读不到任何项目 skill"
                             f"（常见成因：macOS / Linux 上误提交的链接对象在 Windows 上 clone 出来）。确认它不是你的文件后"
                             f"移走它，下一次会话会补建链接"))
    elif form == SKILLS_LINK_TARGET_MISSING:
        out.append(("error", f"{cc} 链接的目标不存在（指向 {target}，多半是项目整体改名 / 移动后 junction 仍指旧位置）"
                             f"——此刻 Claude Code 读不到项目 skill。开一次会话即可：会话启动时会自动把它重指到本项目 {neu}"))
    elif two == SKILLS_LINK_ELSEWHERE:
        out.append(("warn", f"{cc} 指向 {target}，不是本项目的 {neu}——{project_skills_conflict(P.project)}。"
                            f"若不是有意共享，先比对两边，再把它换成指向 {neu} 的链接"))
    elif two == SKILLS_REALDIR:
        out.append(("warn", f"{project_skills_conflict(P.project)}（{cc} 是真目录，通常是复制项目时把链接展开成的副本）。"
                            f"先比对：`git diff --no-index --stat {cc} {neu}`——副本里可能有人改过；"
                            f"合并进 {neu} 后再把 {cc} 换成指向它的链接"))
    elif form == SKILLS_LINK_NEUTRAL_MISSING:
        out.append(("warn", f"{cc} 是链接，但本项目的 {neu} 不在——项目 skills 的真实源没了。从 git 恢复 {neu}"
                            f"（链接随即恢复），或解掉链接（Windows `rmdir .claude\\skills`、POSIX `rm .claude/skills`，"
                            f"都只解链接）"))
    try:
        r = subprocess.run(["git", "ls-files", "-s", "--", cc], cwd=str(P.project), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=30)
        tracked_link = r.returncode == 0 and any(ln.startswith("120000 ") and ln.endswith("\t" + cc)
                                                 for ln in r.stdout.splitlines())
    except Exception:
        tracked_link = False
    if tracked_link:
        out.append(("warn", f"{cc} 在 git 里被跟踪为符号链接对象（mode 120000，多半是 macOS / Linux 上带尾斜杠的忽略行"
                            f"没盖住它时被提交的）——Windows 同事 clone 出来是一个普通文件。`git rm --cached {cc}` 后提交，"
                            f"并确认 .gitignore 里有 `{cc}` 这一行（无尾斜杠）"))
    if out:
        return out
    if form == SKILLS_ABSENT and (P.project / SKILLS_DIR_NEUTRAL).is_dir():
        return [("info", f"{cc} 还不在（clone 形态）——下一次会话启动时会补建指向 {neu} 的链接")]
    if form == SKILLS_REALDIR and (P.project / SKILLS_DIR_NEUTRAL).is_dir():      # 两份判定已排除，只剩反向链接
        return [("ok", f"{cc} 是真目录，{neu} 是指向它的链接（同一份，已装项目的形态，随 git 走）")]
    say = {
        SKILLS_LINK_OK: f"{cc} 是指向本项目 {neu} 的链接",
        SKILLS_REALDIR: f"{cc} 是真目录（已装项目的形态，随 git 走）",
        SKILLS_ABSENT: f"未用项目 skills（{cc} 与 {neu} 都不在）",
    }
    return [("ok", say.get(form, form))]


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


def _user_level_market_names():
    """用户级注册的市场名集合——`~/.claude/` 下两处并集，读不到就当空集。

    doctor 其余检查一律只看项目目录，这里是唯一例外，因为「`core@X` 到底解析不解析
    得到」的答案有一半不在项目里：CLI 的 `marketplace add`（不带 `--scope project`）
    写 `plugins/known_marketplaces.json`，用户级 settings 也能声明 extraKnownMarketplaces。
    不看这一级，「项目级没声明」就只能笼统报一句「协作者装不上」，而实测本机 6 个项目
    里有 5 个正是这个状态、却都装得好好的——那条 error 对单人多机场景是纯误伤。

    **读失败一律降级成空集**，判定退回纯项目级口径：宁可少报一档可移植性，也不能让
    体检崩在一个可选文件上。目录用 `Path.home()`，与 `_auto_memory_dir` 同口径。

    `Path.home()` 自己也在 try 内——它不是纯计算，环境里 USERPROFILE / HOMEDRIVE /
    HOMEPATH / HOME 全缺时会抛 RuntimeError（精简容器 / service account / CI runner
    命中得到），漏在外面会让整项被兜成「检查自身异常」假红。旧版根本不读 home，这条
    失败面是随本函数一起新增的，降级承诺就得连它一起兜。
    """
    names = set()
    try:
        home = Path.home() / ".claude"
    except Exception:
        return names
    for path, key in ((home / "plugins" / "known_marketplaces.json", None),
                      (home / "settings.json", "extraKnownMarketplaces")):
        try:
            # 读路径与项目 settings 同一份实现（BOM 口径统一，见 check_subscription）；
            # 这里仍然整体兜底成空集——体检不能崩在一个可选文件上。
            data = _settings_io.read_settings(path)
            section = data if key is None else data.get(key)
            if isinstance(section, dict):
                names.update(str(k) for k in section)
        except Exception:
            continue
    return names


def _casefold_hit(market, project_markets, user_markets):
    """精确名不命中时，找只差大小写的声明。返回 (来源描述, 实际写法)，没有则 None。

    单独一档的理由见调用点：判死会在「CC 其实大小写不敏感」那半边造出假红。
    """
    target = market.casefold()
    for section, where in ((project_markets, "项目 settings "), (user_markets, "用户级注册 ")):
        for actual in section:
            if str(actual).casefold() == target:
                return where, str(actual)
    return None


def _directory_market_findings(project, name, raw_path):
    """本地目录源按路径形态分档——相对路径随仓可移植，绝对路径只在本机成立。

    分档前两者共用一句「该路径只在你这台机器上存在」，而框架仓嵌套在项目内的开发者
    形态（`"path": "./claude-workframe"`）恰恰是**推荐做法**：它进 git、换台机器
    clone 下来照样解析得到，却被自家 doctor 骂成不可移植（2026-08-22 实测）。

    存在性检查两档都做：声明指向的目录不在，插件就是装不上，与路径形态无关——实测
    有项目留着指向框架仓搬家前旧路径的死声明，靠人工比对才发现。

    **相对不等于可移植**：含 `..` 的相对路径**可能**指到项目目录外面，clone 下来那一级
    未必存在，不能给协作者打包票（同 scan-git-diff 拒绝 `..` pattern 的口径）——它单独
    一档，因为「只在你这台机器上存在」这句对它也不准确。判据按 `..` 出现即收紧、不做
    规范化后的落点计算：`a/../fw` 这种规范化后仍在项目内的形态会被一并收进本档，属**有意
    从严**（少数误收 vs 漏掉真越界，取前者），文案因此说的是「可能指到项目外」。

    **`C:fw` 这类盘符相对路径直接判死，不进任何一档**：`Path('C:fw').is_absolute()` 在
    Windows 是 False，按相对档走会被 pathlib 吞掉 `C:` 前缀（`项目根/C:fw` 实测得
    `<项目根>\\fw`）、目录在就报「随仓可移植」；而按绝对档走，`is_dir()` 又是相对**该盘符
    的当前目录**解析——同一份配置换个 cwd 结论就变。它压根没有确定基准，报「不存在」或
    「可移植」都是在替进程状态背书，所以单独一档报 error。UNC（`\\\\server\\share`）不在此列，
    它 `is_absolute()` 为真、基准确定，走绝对档。

    **两处已知边界（判 portable 但未必真可移植，暂不检测）**：目录 junction / 符号链接
    （git 带不走链接目标）；相对路径大小写与磁盘不符（`./FW` 对 `fw` 在 Windows 解析得到、
    Linux 协作者解析不到）。两者都要额外的平台相关探测，收益不抵复杂度，先记在这里。
    """
    raw = str(raw_path)
    # 盘符相对路径（有 drive 但不 is_absolute，如 `C:fw`）没有确定基准——先判死，
    # 免得后面两档谁接都是在替进程 cwd 背书。理由见 docstring
    if bool(Path(raw).drive) and not Path(raw).is_absolute():
        return [("error", f"市场 {name} 的路径是盘符相对路径（{raw}）——它的基准是"
                          "「该盘符的当前目录」，随进程工作目录变化，不是一个确定的位置；"
                          "改成完整绝对路径，或项目内的 ./<目录>")]
    # `~/x` 不是 is_absolute()，但它锚在本机主目录上，与绝对路径同性质（换人即失效）
    absolute = raw.startswith("~") or Path(raw).is_absolute()
    escapes = not absolute and any(p == ".." for p in Path(raw).parts)
    target = Path(raw).expanduser() if absolute else (Path(project) / raw)
    if not target.is_dir():
        where = "" if absolute else "，相对项目根解析"
        return [("error", f"市场 {name} 的本地目录源不存在（{raw}{where}）——"
                          "该声明解析不到，插件装不上；"
                          "目录搬走或改名后留下的死声明就是这个形态")]
    if absolute:
        return [("warn", f"市场 {name} 是本地目录源（{raw}）——"
                         "该路径只在你这台机器上存在，协作者不可自动安装；"
                         "开源 / 团队协作场景应换 git 源")]
    if escapes:
        return [("warn", f"市场 {name} 是相对路径目录源但含 `..`（{raw}）——"
                         "它可能指到项目目录外面，协作者 clone 后那一级未必存在，"
                         "不能算随仓可移植；框架仓放进项目内可写成 ./<目录>")]
    return []  # 相对路径、不含 ..、目录在 → 可移植，正面结论并进末尾的汇总 ok


def check_subscription(ctx):
    """项目 settings 的订阅声明——市场名解析得到吗、那个来源换台机器还成不成立。

    三条判据同一根轴：`core@<市场>` 的市场从**哪一级**解析得到（项目级 / 用户级 /
    都没有），以及来源形态**换台机器还成不成立**。旧版三条各管各的——只查 `core@`
    前缀不查市场名（拼错照样报绿）、只查 extraKnownMarketplaces 空不空（用户级注册
    够用的单人项目被误报 error）、directory 源不分路径形态（相对路径错套绝对路径文案）。

    **读路径走 `_settings_io.read_settings`**（与 `workframe_door.check_cc`、装机写入方
    同一份实现）：此前本处用 `utf-8`、那边用 `utf-8-sig`，同一份带 BOM 的 settings
    在一侧读得到、在这边被判「解析失败」报 error，而 BOM 正是 Windows 记事本与
    PowerShell 5.1 改过 settings 之后的默认产物。

    **修复文案不假设装了 `claude` CLI**：纯 Codex 机器上那两条命令不存在，照着敲只会
    得到「不是内部或外部命令」。两扇门通用的那条（装机脚本）写在前面。
    """
    P = ctx["paths"]
    if not P.settings.exists():
        return [("error", ".claude/settings.json 不存在——core 插件未订阅；"
                          "补两项声明（extraKnownMarketplaces ＋ enabledPlugins）："
                          "两扇门通用的走装机脚本 "
                          "`python <插件根>/scripts/project_scaffold.py --project <目标> "
                          "--params <params.json> --write-subscription`；"
                          "装了 Claude Code CLI 的机器也可以用 "
                          "`claude plugin marketplace add <源> --scope project` "
                          "＋ `claude plugin install core@workframe --scope project`")]
    try:
        st = _settings_io.read_settings(P.settings)
    except Exception as e:
        return [("error", f".claude/settings.json 解析失败: {e}")]
    out = []
    enabled = st.get("enabledPlugins") or {}
    markets = st.get("extraKnownMarketplaces") or {}
    core_markets = sorted({str(k).split("@", 1)[1] for k, v in enabled.items()
                           if v and str(k).startswith("core@") and str(k).split("@", 1)[1]})
    if not core_markets:
        out.append(("error", "enabledPlugins 未启用 core@<市场>——两扇门通用的补法是"
                             "带 `--write-subscription` 重跑装机脚本 project_scaffold.py；"
                             "装了 Claude Code CLI 的机器也可以跑 "
                             "`claude plugin install core@workframe --scope project`"))
        if not markets:
            # 全新项目（scaffold 完还没订阅）两样都缺，两条修复指引缺一不可。市场名
            # 交叉验证走的是「有 core@ 才逐个查」的路径，够不到这个场景——少写这条，
            # 用户按提示只跑 install 仍然装不上，等于把人送进第二次失败
            out.append(("error", "同时缺 extraKnownMarketplaces——只写启用位装不上，"
                                 "市场来源声明也要有；装机脚本的 `--write-subscription` "
                                 "一次写两项，用 CLI 的话另跑 "
                                 "`claude plugin marketplace add <源> --scope project`"))
    else:
        user_markets = _user_level_market_names()
        for market in core_markets:
            if market in markets:
                continue  # 项目级声明得到，可移植性交给下面的源形态分档
            if market in user_markets:
                # V2 实测：只跑 plugin install（或只在用户级 marketplace add）时就是这个状态
                out.append(("warn", f"core@{market} 靠用户级注册解析——项目 settings 的 "
                                    f"extraKnownMarketplaces 里没有 {market}，本机装得上，"
                                    "协作者 clone 后装不上；把市场来源落进项目（装机脚本的 "
                                    "`--write-subscription`，或 CLI 的 "
                                    "`claude plugin marketplace add <源> --scope project`）"))
                continue
            near = _casefold_hit(market, markets, user_markets)
            if near:
                # 精确不命中、只差大小写：CC 对市场名是否大小写敏感未经真机验证，两种
                # 可能下都不该判死——敏感则确实装不上（该报），不敏感则本机能装而大小写
                # 敏感的环境（Linux 协作者 / CI）未必（也该报）。报 warn 两头都不冤，
                # 判死才会在「不敏感」那半边造出假红
                where, actual = near
                out.append(("warn", f"core@{market} 与{where}声明的 {actual} 只差大小写——"
                                    "本机可能仍解析得到，但大小写敏感的环境（Linux 协作者 / CI）"
                                    "解析不到；把两边统一成同一种写法"))
            else:
                out.append(("error", f"core@{market} 解析不到市场 {market}——项目 settings 与"
                                     "用户级注册里都没有这个名字，插件装不上；"
                                     "检查市场名拼写，或补写市场来源声明（装机脚本的 "
                                     "`--write-subscription`，或 CLI 的 "
                                     "`claude plugin marketplace add <源> --scope project`）"))
    for name, meta in markets.items():
        src = (meta or {}).get("source") or {}
        kind = src.get("source") or ("directory" if src.get("path") else "?")
        if kind == "directory" or src.get("path"):
            path = src.get("path")
            if not path:
                out.append(("error", f"市场 {name} 声明为 directory 源却没写 path——解析不到，插件装不上"))
            else:
                out.extend(_directory_market_findings(P.project, name, path))
    if not out:
        # 走到这里的 directory 源必是「相对路径 + 不含 .. + 目录在」（其余形态上面都产了条目），
        # 所以这里可以直接标可移植——这串是给用户的确认，不是重新判定。
        # **没有 directory 源时这句逐字保持旧文案**：github / git 源的项目输出零变化，
        # 新增的正面结论只加在真正走了新档的项目上（AC-11 的零外溢边界就在这一行）
        portable = [n for n, m in markets.items()
                    if (((m or {}).get("source") or {}).get("source") == "directory"
                        or ((m or {}).get("source") or {}).get("path"))]
        detail = ("；其中 " + "、".join(portable) + " 是相对路径目录源，随仓可移植") if portable else ""
        out.append(("ok", f"订阅声明齐全（enabledPlugins + extraKnownMarketplaces "
                          f"{len(markets)} 个市场）{detail}"))
    return out


# 上一版同步进项目的纪律镜像目录（项目根相对，按段拼接）。**只此一份**：本项的残留检测与迁移工具
# 的删除条目都读它，两边各写一份就会一边改了另一边还在找旧位置。
RULES_MIRROR_PARTS = (".claude", "rules", "workframe", "core")


def check_rules_mirror_residue(ctx):
    """上一版留在项目里的纪律镜像目录必须已经不在了——它还在就说明这个项目卡在半升级态。

    必载纪律改由 hook 在会话启动时注入之后，项目侧不再有承载它的目录。但**升级插件不会
    帮你删掉上一版留下的镜像**：那些文件仍会被 Claude Code 的 rules 加载机制读进上下文，
    于是同一条纪律的**新旧两个措辞版本同时在场**，模型按哪一份执行不可判、也不可观测。

    **这是检测不是保留**：本项从不读镜像内容、不比对、不同步，只回答「它还在不在」。

    报 error 而不是 warn：warn 不阻断收口，而这个态下模型可能正按一份已被取代的口径干活。
    """
    P = ctx["paths"]
    legacy = P.project.joinpath(*RULES_MIRROR_PARTS)
    if not legacy.exists():
        return [("ok", "无旧镜像残留（必载纪律由会话启动时的注入通道投递，项目侧不落文件）")]
    try:
        leftovers = sorted(p.name for p in legacy.glob("*.md"))
    except OSError:
        leftovers = []
    detail = f"（{len(leftovers)} 份：{'、'.join(leftovers[:5])}）" if leftovers else "（空目录）"
    return [("error", f"上一版的纪律镜像目录仍在: "
                      f"{legacy.relative_to(P.project).as_posix()}{detail}"
                      "——这些文件仍会被读进上下文，与新的注入片构成同一条纪律的两个措辞版本，"
                      f"模型按哪份执行不可判。出路：迁移工具——{_migrate_how(P)}；它把这个目录列为 `X-rules`，"
                      "apply 时整个删掉，已提交的版本可按回执里的逆操作从 git 找回（未提交的改动删掉就找不回，"
                      "plan 里逐份标着）。该目录下从来只有框架同步进去的副本，项目自有内容不在这里")]


def _migrate_how(P):
    """迁移工具出 plan 的完整说法（体检文案共用）：终端命令按本插件的实际位置现算，不让人去猜插件根。"""
    door = (PLUGIN_ROOT / "scripts" / "workframe_door.py").as_posix()
    return (f"关掉这个项目的全部会话，在普通终端跑 `python \"{door}\" --migrate --project \"{P.project.as_posix()}\"` "
            "出 plan（在别的项目的 Claude Code 会话里跑 `workframe-door --migrate --project <本项目根>` 也行，"
            "**别在本项目自己的会话里出**），再按它打印的那行 apply")


def _is_framework_scope(name):
    """记忆目录下这个子目录名是不是框架出厂的 scope（`shared` 或出厂角色）。旧布局判定与 auto-memory 旧指针共用。"""
    return name == "shared" or name in BASELINE_ROLES


def _legacy_memory_is_framework(old):
    """旧记忆目录（`legacy_rel("memory")`）是不是框架的旧布局，返回 `(是否, info 文案或 None)`。

    这个位置同时是 Claude Code 官方子 agent `memory: project` 的落点（`<旧记忆目录>/<agent>/`），
    存在本身不说明框架的记忆还在那儿。所以只有出现**框架出厂形态**才算：`shared/` 子目录，或出厂角色
    （`BASELINE_ROLES`）目录下有 `MEMORY.md` / `notes.md`。同名文件 / 悬空链接不是 CC 的目录形态，按旧布局算。
    只有别的子目录时不算，给一条 info，两种来历都点名——形态上分不开。
    **迁移工具复用本函数**决定旧记忆目录搬不搬：两边一处判定，体检说「不算旧布局」的，迁移工具就不搬、不判冲突。"""
    if not old.is_dir():
        return True, None
    if (old / "shared").is_dir():
        return True, None
    subdirs = sorted(p.name for p in old.iterdir() if p.is_dir())
    if any(_is_framework_scope(r) and ((old / r / "MEMORY.md").is_file() or (old / r / "notes.md").is_file())
           for r in subdirs):
        return True, None
    rel = legacy_rel("memory")
    if not subdirs:
        return False, f"{rel}/ 存在但没有子目录——不是框架出厂形态，不算旧布局"
    return False, (f"{rel}/ 下的 {'、'.join(s + '/' for s in subdirs[:4])} 不是框架出厂角色，不算旧布局。"
                   "两种来历按实际判：①Claude Code 子 agent 的 `memory: project` 记忆（CC 从这里读）——**别搬**；"
                   f"②框架旧布局下**项目级角色**的记忆——迁移工具对这种形态不搬（与本项同一判据），确属框架的手工挪进 "
                   f"{new_rel('memory')}/")


def _legacy_layout(P):
    """旧布局运行态目录的现状，按 kind 分：`{"old": [kind…], "both": [kind…], "info": [文案…]}`。

    `old` = 框架旧目录在、新目录不在（没迁移）；`both` = 两个都在（多半是没迁移就开了会话，hook 在新位置
    另建了一套）。`memory` 的旧目录按 `_legacy_memory_is_framework` 判，`state` / `archive` 在即算。
    结果挂在 `P` 上只算一次：本项与 `skeleton` / `setup_state` / `gitignore` 的修法文案共用同一份判定。"""
    got = getattr(P, "_legacy_cache", None)
    if got is not None:
        return got
    got = {"old": [], "both": [], "info": []}
    for kind in ("state", "memory", "archive"):
        if not legacy_present(P.project, kind):
            continue
        if kind == "memory":
            is_fw, info = _legacy_memory_is_framework(P.project / legacy_rel(kind))
            if info:
                got["info"].append(info)
            if not is_fw:
                continue
        got["both" if os.path.lexists(P.project / new_rel(kind)) else "old"].append(kind)
    P._legacy_cache = got
    return got


def _has_legacy(P):
    lay = _legacy_layout(P)
    return bool(lay["old"] or lay["both"])


# 旧布局在场时，其余安装检查原本的修法（重跑装机脚本 / 按 launcher 步骤补做）会在新位置再建一套、把冲突扩到别的类
_LEGACY_FIX_POINTER = "本项目有旧布局运行态目录没迁移，这一条多半由它引起——先按 [legacy_state_dir] 迁移，别重跑装机脚本补齐"


def check_legacy_state_dir(ctx):
    """旧布局的运行态目录（`.claude/` 下的 state / 记忆 / 记忆归档）还在不在——在就说明这个项目没迁移或迁了一半。

    框架只读新位置（`_state_io.runtime_rel`），不回退到旧目录：没迁移的项目开会话，hook 在新位置从零建一套，
    旧目录里的状态与记忆从此没有读者。**排在安装组第一位**：首个会话的安装验收只跑安装组，而从旧版升级、
    没迁移就开会话的项目恰好在新位置得到 `session_counter == 1`，验收因此触发——这是它唯一一次不用人去跑
    就能被看见的机会，而同一次验收里 `skeleton` / `setup_state` 的缺项多半是它的连带，要让人先读到这一条。

    按 kind 分两态报（可同时出现）：只有旧 ⇒ 迁移；新旧并存 ⇒ 迁移工具判冲突不动，先人工处理新目录再迁移。
    `memory` 类只在出现框架出厂形态时算（见 `_legacy_memory_is_framework`），只有 CC 原生 agent 记忆时报 info。
    """
    P = ctx["paths"]
    lay = _legacy_layout(P)
    out = []
    if lay["old"]:
        olds = "、".join(legacy_rel(k) + "/" for k in lay["old"])
        news = "、".join(new_rel(k) + "/" for k in lay["old"])
        out.append(("error", f"没迁移：{olds} 还在旧位置，本版框架只读 {news}——旧目录里的状态与记忆没有读者"
                             f"（会话照常起，但计数从零、角色记忆注入为空）。出路：{_migrate_how(P)}。"
                             "迁移后 auto-memory 里指向旧记忆目录的指针要手改（plan 的报告项逐行列出）"))
    if lay["both"]:
        pairs = "；".join(f"{legacy_rel(k)}/ 与 {new_rel(k)}/" for k in lay["both"])
        out.append(("error", f"新旧并存：{pairs} 同时在——新目录多半是升级后没迁移就开会话时 hook 建的（只有那几次会话的"
                             "数据），旧目录里才是原来的状态与记忆；框架只读新目录。迁移工具对这一态判冲突不动：确认新目录里"
                             "没有要留的内容后把它挪出项目（或改名），再迁移。哪边是多余的看 plan 里冲突条目列出的两边"
                             "文件数与会话计数——计数小、文件少的多半是后来被会话另建的那一侧（反过来也可能：迁移之后仍开着的"
                             f"旧版会话把旧目录重新建了出来）——{_migrate_how(P)}"))
    out += [("info", m) for m in lay["info"]]
    if not lay["old"] and not lay["both"]:
        out.insert(0, ("ok", "没有旧布局的运行态目录（按框架出厂形态判）"))
    return out


def check_gitignore(ctx):
    """.gitignore 必需条目——运行时状态与日志默认不进 git。"""
    P = ctx["paths"]
    if not P.gitignore.exists():
        return [("warn", ".gitignore 不存在——运行时状态与日志会被误提交"
                         + (f"（{_LEGACY_FIX_POINTER}；迁移工具会写上新布局要的条目）" if _has_legacy(P)
                            else "（重跑 project_scaffold.py 会从模板补上）"))]
    text = P.gitignore.read_text(encoding="utf-8", errors="replace")
    # 与 project_scaffold.gitignore_covers 同口径：子串匹配会让 `.tmp/` 顶替 `tmp/`，
    # 也会把注释掉的 `# tmp/` 与否定规则 `!tmp/` 算成已有
    required = gitignore_required(P)
    missing = [e for e in required if not _gitignore_covers(text, e)]
    # 项目 skills 链接那一条：只有旧写法（`.claude/skills/` 等）时单独说——它在 Windows junction 上生效，
    # 在 macOS / Linux 的符号链接上不生效。**不按本机平台放行**：`.gitignore` 随仓走、不随机器走
    legacy = []
    try:
        ps = _import_scaffold()
        if ps.SKILLS_LINK_IGNORE_LINE in missing and ps.skills_link_ignore_state(text) == "legacy":
            missing.remove(ps.SKILLS_LINK_IGNORE_LINE)
            legacy = [("warn", f"`{ps.SKILLS_LINK_IGNORE_LINE}` 的忽略行是旧写法（带尾斜杠或 `/*`）——这条写法只在 "
                               f"Windows junction 上生效，macOS / Linux 上链接会以未跟踪出现、被 `git add -A` 带进提交。"
                               f"升级：跑 `workframe-door --migrate`（只算，打印确认码与 apply 命令），再按它打印的那行在"
                               f"普通终端执行 apply——对已迁移的项目它只改 `.gitignore` 的 managed block 这一行；"
                               f"或手改：把那一行改成 `{ps.SKILLS_LINK_IGNORE_LINE}`（去掉尾斜杠）")]
    except Exception:
        pass
    if missing:
        return [("warn", f"缺必需条目: {'、'.join(missing)}")] + legacy

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
                         f"改成 `{P.state_rel}/*` 即可；有意忽略整个目录则忽略本条")] + legacy
    if legacy:
        return legacy
    return [("ok", f"{len(required)} 项必需条目齐全")]


# launcher 初始化的必经步骤。setup-state 是**增量**写的（scaffold 一成功就落第一步），
# 所以「缺步骤」和「步骤失败」是两种不同的中断形态，得分开判——
# 早期版本只查有没有非 ok 的值，于是一份只有 `scaffold: ok` 的半截文件会被判成「全部完成」。
SETUP_REQUIRED_STEPS = ("scaffold", "subscribe", "agents_md")

# `acceptance` 不在必查清单里，也不参与 failed 判定——它由 doctor 自己在落盘验收跑完后落笔
# （见 main），是**本工具的运行记录**而不是 launcher 的前置步骤。
# 让 launcher 记它会自指：acceptance 的语义是「落盘验收做完了」，只能在验收之后写；而验收跑的
# 就是本组、组里又查 acceptance——于是第一次跑必然缺它、必然报 error，模型补记后再跑才过。
# 那条 error 是机制自造的，不是真问题。
SETUP_SELF_RECORDED_STEPS = ("acceptance",)

# 由 **hook 每会话补记**、而非 launcher 走流程时落笔的步骤。与上面那组同属「这一步在文件里
# 不证明 launcher 来过」，但成因不同，所以分成两组常量而不是合并：
#   - `SETUP_SELF_RECORDED_STEPS` 是 doctor 自己的运行记录，压根不在必查清单里；
#   - 本组**在**必查清单里（`agents_md` 是 launcher 的一步），只是另有一个非 launcher 的落笔人：
#     `session-start-prep.ensure_agents_md_present()` 每次会话启动确认文件在位就补记一次。
# 为什么要有这组：一批项目的 setup-state.json 已经被那条路径写成了只含 `agents_md` 的一步
# （状态目录不进 git ⇒ clone 侧无文件 ⇒ 当时的无条件记步凭空造出一份）。源头已由
# `mark_setup_step(..., only_if_present=True)` 堵住，但**已经造出来的文件还在人家仓里**，
# 而它恒报「初始化未走完 1/3 步」且永不自愈。列进本组 ⇒ 按「无有效 launcher 记录」放行。
SETUP_HOOK_RECORDED_STEPS = ("agents_md",)


def check_setup_state(ctx):
    """消费 setup-state.json 识别「上次装到哪一步」。"""
    P = ctx["paths"]
    if not P.setup_state.exists():
        return [("info", "无 setup-state.json（手工接入、克隆下来的项目、或早于该机制的项目，"
                         "不影响使用）")]
    try:
        st = json.loads(P.setup_state.read_text(encoding="utf-8"))
    except Exception as e:
        return [("warn", f"setup-state.json 解析失败: {e}")]
    # 结构兜底：合法 JSON 不等于本文件的形状。顶层非 dict ⇒ 下一行 `.get` 抛 AttributeError；
    # `steps` 非 dict ⇒ 后面的 `.items()` / `in` 抛。两处都被上层收成「检查自身异常」——
    # 看不出问题在文件结构上，还把一份读得出来的文件说成是体检自己坏了。
    # **这两格从本版起才够得着**：以前 SessionStart 的无条件记步会把它们整份重建掉，
    # 现在守卫保住原文件（那正是带 BOM 的健康记录不再被抹掉的同一个改动），于是它们会一直在。
    if not isinstance(st, dict):
        return [("warn", f"setup-state.json 结构非法：顶层不是对象（实际 {type(st).__name__}）"
                         "——该文件是派生状态，删掉后重跑 launcher 即可重建")]
    steps = st.get("steps") or {}
    if not isinstance(steps, dict):
        return [("warn", f"setup-state.json 结构非法：steps 不是对象（实际 "
                         f"{type(steps).__name__}）——该文件是派生状态，删掉后重跑 launcher 即可重建")]
    # 自愈：文件里一个 launcher 步骤都没有，只剩两类**不证明 launcher 来过**的步骤——
    # 本工具自己写的 acceptance（旧版 _record_acceptance 给手工接入项目凭空造的），
    # 以及会话启动 hook 补记的 agents_md（clone 侧无文件时旧版无条件记步凭空造的）。
    # 两者都已在源头堵掉，但已经被造出来的文件还在人家仓里，恒报「未走完」且永不自愈。
    # 按「无有效 launcher 记录」放行，等同于文件不存在。
    # **谓词要求 steps 非空**：空集上 `all(...)` 恒真，`{"steps": {}}` 会跟着变 info，
    # 而那一形态只能由手改 / 截断产生，不是本条要救的人群。
    if steps and all(k in SETUP_SELF_RECORDED_STEPS + SETUP_HOOK_RECORDED_STEPS for k in steps):
        return [("info", "setup-state.json 里没有任何 launcher 步骤——只有本工具的验收记录 / "
                         "会话启动 hook 补记的那一步，按手工接入或克隆下来的项目处理"
                         "（旧版会凭空建这份文件，可安全删除）")]
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
        if _has_legacy(P):
            # 挂在 error 行上而不是另起 info：首会话验收的摘要只印 error / warn，info 那行用户看不到
            why = "；这份 setup-state.json 在新状态目录里，只记着升级后的会话写下的步骤，不说明装机没走完"
            out = [(lv, f"{msg}（{_LEGACY_FIX_POINTER}{why}）") for lv, msg in out]
        else:
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
    out += _check_plugin_roots_same_version(P)
    return out


def _plugin_version_at(root):
    """`<插件根>/.claude-plugin/plugin.json`（Codex 安装缓存里也有这份——整个插件目录原样复制）的
    `version`；读不出来返回 None，由调用方按「对不上」处理，不按「没问题」。"""
    for rel in (".claude-plugin", ".codex-plugin"):
        p = Path(root) / rel / "plugin.json"
        if p.is_file():
            try:
                v = json.loads(p.read_text(encoding="utf-8-sig")).get("version")
                return v if isinstance(v, str) and v else None
            except Exception:
                return None
    return None


def _check_plugin_roots_same_version(P):
    """两扇门各自记录的插件根（`plugin-root.<门>.txt`，SessionStart 按 `--harness` 分文件写）
    版本必须相等。

    两扇门各有自己的安装缓存，升级是两个动作（`claude plugin update` / `workframe-door --upgrade`）；
    一边升了一边没升时，两门会各自执行**不同版本**的脚本、读不同版本的事件注册表，而会话里
    看不出任何差别——`plugin-root.txt` 兼容位只记「最后一个起会话的那扇门」，看不出这件事。
    只有一份分文件时（只用一扇门、或另一门还没起过会话）报 info，不是问题。
    """
    roots = {}
    for door in ("cc", "codex"):
        f = P.state / f"plugin-root.{door}.txt"
        if f.is_file():
            roots[door] = f.read_text(encoding="utf-8", errors="replace").strip()
    if not roots:
        return []
    if len(roots) == 1:
        door = next(iter(roots))
        return [("info", f"只有 {door} 门记录过插件根（plugin-root.{door}.txt）——另一扇门尚未在本项目起过会话，两根版本无从对账")]
    # 空串先判死：`Path("")` 解析到进程 cwd，doctor 恰好在某个插件根下跑时会把「没记录」读成「一致」
    vers = {d: (_plugin_version_at(r) if r else None) for d, r in roots.items()}
    if None in vers.values():
        bad = "、".join(f"{d}={roots[d]}" for d, v in vers.items() if v is None)
        return [("error", f"读不到插件根的版本（{bad}）——该门记录的安装根已不在或 plugin.json 损坏，"
                          f"该门下次会话会执行一个不存在的安装里的脚本；重装或升级那一门")]
    if vers["cc"] != vers["codex"]:
        return [("error", f"两扇门的插件根版本不等：cc={vers['cc']}（{roots['cc']}）/ codex={vers['codex']}"
                          f"（{roots['codex']}）——一边升级了一边没升，两门会各自执行不同版本的脚本、读不同"
                          f"版本的事件注册表，会话里看不出差别；把落后的那一门也升到同版本"
                          f"（CC：`claude plugin update core@<市场> -s project`；Codex：`workframe-door --upgrade`）")]
    return [("ok", f"两扇门的插件根版本一致（{vers['cc']}）")]


def check_inject_log(ctx):
    """注入记录一致性：`inject-log.jsonl` 里不得有 `scope == sub` 且 `thread_id == session_id` 的行。

    sub 片（子 agent 纪律）只该投给子线程：`SubagentStart` 上 `thread_id` 是子线程的 `agent_id`，
    `UserPromptSubmit --scope prompt` 上判子线程的依据正是「`transcript_path` 里的线程号 ≠ `session_id`」
    ——所以 sub 行的 `thread_id` 与 `session_id` 相等，只能来自判定式或载荷字段的错位：主会话被灌进了
    子 agent 的纪律片，模型按错的协议做事，而注入本身 exit 0、终端零信号。两个字段任一为空的行不判
    （缺字段是另一个问题，不在本检查内）；坏 JSON 行只计数不判。
    """
    P = ctx["paths"]
    f = P.state / "inject-log.jsonl"
    if not f.is_file():
        return [("info", "inject-log.jsonl 尚未生成——首个会话由注入 hook 写入")]
    rows, bad_json, offenders = 0, 0, []
    for lineno, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        try:
            r = json.loads(s)
        except Exception:
            bad_json += 1
            continue
        if not isinstance(r, dict):
            bad_json += 1
            continue
        rows += 1
        tid, sid = r.get("thread_id"), r.get("session_id")
        # 比对前两边都 `.lower()`：inject-context.py 判 is_sub 时把 session_id 小写后比，这里口径同它
        if (r.get("scope") == "sub" and isinstance(tid, str) and isinstance(sid, str) and tid
                and tid.lower() == sid.lower()):
            offenders.append(lineno)
    out = []
    if offenders:
        out.append(("error", f"{len(offenders)} 行 scope=sub 的注入记录 thread_id == session_id（第 "
                             f"{'、'.join(map(str, offenders[:6]))}{'…' if len(offenders) > 6 else ''} 行）——"
                             f"子 agent 纪律片投进了主线程：主会话按子 agent 的协议做事，而注入 hook 照样 exit 0；"
                             f"查 inject-context.py 的 is_sub 判定与该行载荷的 transcript_path / agent_id"))
    if bad_json:
        out.append(("warn", f"{bad_json} 行不是合法 JSON 对象，未参与判定"))
    if not out:
        out.append(("ok", f"{rows} 行注入记录，无 sub 片落主线程的行"))
    return out


def _shard_index(shard):
    """`"i/n"` → `(i, n)`；不是这个形态（`all` / None / 其它）返回 None。"""
    if not isinstance(shard, str):
        return None
    parts = shard.split("/")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


def check_inject_shards(ctx):
    """注入分片到齐：同一次投递里 `i/n` 各片的**出现次数必须相等**，不等 ⇒ warn。

    **取行**：只取 `shard` 形如 `i/n` 的行（按形态过滤，不按门过滤——Codex 行里也有 `i/n`）；
    排除 `kind == "turn"`（`--scope prompt` 的轮次记账行）；**只判带 `thread_id` 键的新格式行**，
    旧格式行只计数（窗口内的才报，窗口过后自然消失）。坏 JSON 行不在本项计数，`inject_log` 项已报。

    **判据**：组键 = (harness, session_id, thread_id, event, scope, n)；组内各片号出现次数
    c₁…cₙ（缺席计 0）；`min < max` ⇒ 有片没记上。**不先去重**——去重会把「33 次 1/2、32 次
    2/2」抹成两边都在。compact 重注让每个片号各 +1，不破坏相等，不假红。它比的是片号之间
    是否相等，不是「行数 == 设计片数」那种会被 compact 顶破的计数语义。
    **窗口**：组内**任一行**落在近 `INJECT_SHARD_WINDOW_DAYS` 天（或 ts 解析不了），就取该组
    **全部行**判定——按行截窗口会把一次投递劈成两半而假红。
    **spill**：状态目录有 `inject-log.*.spill.jsonl` 时降为 info——缺的那片可能正躺在里面。
    **只 warn 不 error**：doctor 有 error 就 exit 1，而收口要求 doctor 全绿；一条历史行不该
    卡住所有收口，且下面的第③种成因是已实见的假红来源。

    **能力边界**：
      - CC 侧「超限落盘」发生在记录**之后**，从 inject-log 读不出；打包器按构造保证每片不超
        上限，那一侧由 validate `context_shards_within_cap` 管。
      - 挂载表的 n 与现算片数不符时，每片在写记录**之前**就抛错 ⇒ 零行，本项看不见（与「整场
        hook 没跑」同形）；「整场没跑」本项同样看不见（CC 侧看 doctor `env` 的 hook 产物证据、
        Codex 侧看 `workframe-door --check --live`）。
      - 两次投递各缺不同片、恰好补平时漏报。
      - 分不出「片没投到」与「记录行丢了」。
    """
    P = ctx["paths"]
    f = P.state / "inject-log.jsonl"
    if not f.is_file():
        return [("info", "inject-log.jsonl 尚未生成——没有投递记录可查")]
    cutoff = datetime.now(timezone.utc) - timedelta(days=INJECT_SHARD_WINDOW_DAYS)
    groups = {}
    legacy_in_window = bad_shape = 0
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict) or r.get("kind") == "turn":
            continue
        idx = _shard_index(r.get("shard"))
        if idx is None:
            continue
        ts = _parse_ts(r.get("ts"))
        in_window = ts is None or ts >= cutoff
        if "thread_id" not in r:
            legacy_in_window += in_window
            continue
        i, n = idx
        if n < 1 or not 1 <= i <= n:
            bad_shape += 1
            continue
        key = (r.get("harness"), r.get("session_id"), r.get("thread_id"),
               r.get("event"), r.get("scope"), n)
        g = groups.setdefault(key, {"counts": {}, "in_window": False})
        g["counts"][i] = g["counts"].get(i, 0) + 1
        g["in_window"] = g["in_window"] or in_window
    judged = {k: g for k, g in groups.items() if g["in_window"]}
    offenders = []
    for k, g in judged.items():
        counts = [g["counts"].get(i, 0) for i in range(1, k[5] + 1)]
        if min(counts) < max(counts):
            offenders.append((k, counts))
    out = []
    if offenders:
        head = "；".join(f"{k[4]} n={k[5]}（{k[3]}，session {str(k[1])[:8]}，thread "
                        f"{str(k[2])[:8]}）各片次数 {counts}" for k, counts in offenders[:3])
        more = f" 等共 {len(offenders)} 组" if len(offenders) > 3 else ""
        spills = sorted(P.state.glob(f"inject-log.*{SPILL_SUFFIX}"))
        if spills:
            out.append(("info", f"分片次数不等：{head}{more}——但状态目录有 {len(spills)} 份未并回的 "
                                f"inject-log spill，缺的那片可能正躺在里面；下次会话启动并回后复查"))
        else:
            out.append(("warn", f"同一次投递里有片没记上：{head}{more}。三种成因：①该片的注入 hook "
                                f"没跑到写记录那一步（崩溃 / 超时）——那一片纪律没到模型手里；②记录行丢了"
                                f"（写记录失败被吞，片其实到了）；③有人手工运行过打包器（开发与探针环境里"
                                f"实见的假红来源，会带着继承的会话号写单片行）。本项分不出 ①②"))
    if legacy_in_window:
        out.append(("info", f"近 {INJECT_SHARD_WINDOW_DAYS} 天有 {legacy_in_window} 行旧格式分片记录"
                            f"（没有 thread_id 字段）只计数不判——窗口过后自然消失"))
    if bad_shape:
        out.append(("info", f"{bad_shape} 行分片号越界（i 不在 1..n），未参与判定"))
    if not offenders:
        out.append(("ok", f"近 {INJECT_SHARD_WINDOW_DAYS} 天 {len(judged)} 组分片投递各片次数相等"
                          f"（窗口外 {len(groups) - len(judged)} 组未判；本项看不见整场 hook 没跑）"))
    return out


INSTALL_CHECKS = [
    ("legacy_state_dir", "旧布局运行态目录", "`.claude/` 下的旧 state / 记忆 / 记忆归档目录不在（记忆按框架出厂形态判，只有 CC 原生 agent 记忆时报 info）；在即 error，按只有旧 / 新旧并存给出路。**必须排第一**：首会话验收里其余缺项多半是它的连带", check_legacy_state_dir),
    ("skeleton", "骨架完整性", "scaffold 基础骨架 + 渲染产物齐全；占位符零残留（兜底）", check_skeleton),
    ("claude_md", "CLAUDE.md 导入与旧副本", "有一处有效的 `@AGENTS.md` 导入（CC 侧读到 AGENTS.md 的唯一入口，缺即 error）；框架段落的旧副本报 warn（与注入片形成双份）", check_claude_md),
    ("agents_md", "AGENTS.md 契约段", "两扇门共用的项目自有判据落点：四段齐全、委派授权段在位、正文无 `@<file>` 导入；「这个项目是什么」只剩框架占位报 warn", check_agents_md),
    ("prd_framework", "项目 PRD 框架", "项目层 prd-style skill 在位性；缺失仅 info（prd-writer 走默认模板 fallback）", check_prd_framework),
    ("skills_link", "项目 skills 链接形态", "`.claude/skills` 是指向本项目 `.agents/skills` 的链接 / 真目录；普通文件与目标不存在的链接 error，两份各读各的 / 真实源不在 / 以符号链接对象被 git 跟踪 warn", check_skills_link),
    ("config", "config 三字段", "project_type / dormant_profile / role_profile 存在且取值合法", check_config),
    ("subscription", "订阅接线", "core@<市场> 在项目级 / 用户级解析得到；本地目录源按相对·绝对路径分档 + 存在性", check_subscription),
    ("rules_mirror_residue", "旧镜像残留", "上一版留在项目里的纪律镜像目录必须已删除；只判在不在，不读内容也不同步", check_rules_mirror_residue),
    ("gitignore", ".gitignore 条目", "运行时状态 / 日志 / 临时区默认不进 git", check_gitignore),
    ("setup_state", "初始化断点", "消费 setup-state.json 识别上次装到哪一步", check_setup_state),
    ("init_completeness", "初始化完整度", "源资料是否全部落地；pending_work 批次为流程态，只报 info", check_init_completeness),
    ("portability", "可移植性", "进 git 的文件不含本机绝对路径（通用模式，不写死用户名）", check_portability),
    ("env", "环境与 hook 活性", "python 可达；hook 层事件产物存在即链路存活（重启前降 info）；两扇门各自记录的插件根版本相等", check_env),
]

CHECKS = [
    ("events_parse", "事件流可解析率", "events.jsonl 逐行 JSON 解析；坏行=error", check_events_parse),
    ("sidecar_health", "sidecar 健康", "memory-index.json 字段 schema / scope 枚举 / key-scope 一致 / 疑似重复 key", check_sidecar_health),
    ("proposal_dates", "提案日期字段", "applied 须有 applied_at、rejected 须有 rejected_at（迭代日期派生的输入质量）", check_proposal_dates),
    ("yaml_parse", "数据文件 YAML 可解析性", "board.yaml / issues / proposals 过真 YAML 解析器；坏行=error（与 close-check `[yaml-parse]` 共用判定式、文件集不相交）", check_data_yaml_parse),
    ("notes_backlog", "notes 积压", f"内容行 > {NOTES_BACKLOG_LINES} 或 内容行>5 且距上次提升 > {NOTES_STALE_DAYS} 天（体积口径；开场卡问询按条目数另计，见 memory-ask.py）", check_notes_backlog),
    ("memory_capacity", "MEMORY 容量", f"字符预算 role ≤ {ROLE_MEMORY_MAX_CHARS} / shared ≤ {SHARED_MEMORY_MAX_CHARS}；附 token 估算（×{TOKEN_PER_CHAR}）", check_memory_capacity),
    ("auto_memory", "auto-memory 体量", f"单条 ≤ {AUTO_MEMORY_ENTRY_MAX_CHARS} 字符；索引 ≤ {AUTO_MEMORY_INDEX_MAX_LINES} 行；只报警不动手", check_auto_memory),
    ("promise_producer", "承诺-producer 对账与死信号三态", "schema 全事件类型 × (事件计数, producer 文本存在性) → normal / starved / broken-promise", check_promise_producer),
    ("protected_assets", "受保护资产对账", f"近 {PROTECTED_WINDOW_DAYS} 天 git 提交 vs changelog 痕迹（启发式，仅提示级）", check_protected_assets),
    ("inject_log", "注入记录一致性", "inject-log.jsonl 里不得有 scope=sub 且 thread_id == session_id 的行（子 agent 纪律片落进主线程）", check_inject_log),
    ("inject_shards", "注入分片到齐", f"近 {INJECT_SHARD_WINDOW_DAYS} 天同一次投递里 i/n 各片出现次数相等；不等=warn（看不见整场 hook 没跑与超限落盘）", check_inject_shards),
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
    state_file = state_dir_of(project_dir) / "setup-state.json"
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
              f"PROTECTED_WINDOW_DAYS={PROTECTED_WINDOW_DAYS} INJECT_SHARD_WINDOW_DAYS={INJECT_SHARD_WINDOW_DAYS}")
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
