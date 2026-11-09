#!/usr/bin/env python3
"""主会话角色记忆投递的共用加载器：开场、工作入口、压缩后补投三处共用这一份，两扇门共用。

## 为什么需要它

子 agent 启动时由 `subagent-memory-inject.py` 注入 shared 与本角色记忆；**主会话没有这个事件**。
此前主会话靠「直做前显式 Read」，实测十个会话里只有一次真的读了。本模块把读取改成 hook 在确定
时点投递：

  - 开场（SessionStart，`session-start-prep.py`）：shared 全文 ＋ 各角色条目目录
  - 工作入口：进入某域 skill 时投该域 MEMORY 全文——CC 门是调用 Skill 工具与用户直接输入 `/skill`
    （`log-skill-invoked.py`，PostToolUse(Skill) 与 UserPromptExpansion）；Codex 没有 Skill 工具，是 shell 读
    该 skill 的 `SKILL.md`（`signoff-guard.py`，它是 PostToolUse(Bash) 上现成的那条 hook）与用户在提示里写
    `$skill`（`user-prompt-inject.py`，UserPromptSubmit）。首次改到模块 `code_paths` 文件（`check-stale-modules.py`，
    CC 的编辑工具与 Codex 的 `apply_patch`）时**只投 dev**
  - 压缩后（SessionStart matcher `compact`，`role-memory-restore.py`）：补投本周期已投过的角色

**本模块不读门**：两门的入口形态不同（工具名、载荷字段），但识别规则对两门都成立——CC 的 Bash 读到
`SKILL.md`、提示里写 `$skill` 同样算进入该域。门差只在各入口脚本的挂载上。

## 输出协议

本模块只返回**正文文本**，不打印、不序列化。由入口脚本做唯一一次序列化：PostToolUse /
UserPromptExpansion 的纯文本 stdout 不进模型，必须输出 `hookSpecificOutput.additionalContext` JSON，
且那串 JSON 是该进程 stdout 的唯一内容（`hook_output()` 给出整串，`ensure_ascii=True`——纯 ASCII
不受 Windows 控制台代码页影响）；SessionStart 在 CC 门沿用纯文本，Codex 清单带 `--output hook-json` 时改投 JSON
（Codex 把以 `[` 起头的纯文本 stdout 当 JSON 解析、失败即整条丢弃）。

## 去重

键 = 会话 `session_id` × 压缩周期号；值 = 本周期已**生成过输出**的角色（全文或超预算时的条目目录都算）及其最近使用时刻
（去重命中也刷新——压缩补投按它排）。
读失败不记，下一次触发再试。压缩补投把周期号 +1，新周期只记补投时给了全文的角色。
状态的读改写在 1 秒短锁里（`_txn`）：不锁的话并行工具调用里投不同角色的两个进程会互相抹掉已投记录，压缩后漏补；
锁拿不到就不锁照做，不拦工具调用。`/clear` 换新 `session_id`，无需重置。
fork 线程不投正文、只给指针，指针的去重键是 `<session_id>#fork:<agent_id>`（见 `_fork_pointer`）。

## 事件

每次投递判定写一条 `role_memory_injected`（字段见 event-schema.json）。它看得出「触发了没投」「没映射」
「读失败」；**看不出「该触发却没有触发点」**（只读只分析、不调 skill 也不改文件）——那一格由开场条目目录兜底。

同目录 import 使用：import 期无副作用。
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402
from _state_io import (  # noqa: E402
    FileLock, append_line, atomic_write, event_json, memory_dir_of, read_json_object, state_dir_of,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MAP_FILE = PLUGIN_ROOT / ".workframe-meta" / "role-memory-map.json"
STATE_FILE_NAME = "role-memory-state.json"
STATE_SCHEMA = "workframe.role-memory-state.v1"
EVENT_TYPE = "role_memory_injected"
MAX_SESSIONS = 50

# CC 单条 additionalContext / stdout 上限 10,000 字符（超出落盘只给预览，模型会编造补缺）；留出余量。
# Codex 那一门的上限由清单的 `additionalContextLimit` 给（单位是 4 字节），派生规则按本预算取值，见 `_codex_hooks`
DELIVER_BUDGET = 9500
# 开场那条 stdout 里还有摘要、看板漂移、待办提醒等其他输出，记忆段只占一部分
OPENING_BUDGET = 7000

TRIGGER_SKILL_TOOL = "skill_tool"
TRIGGER_COMMAND = "command_expansion"
TRIGGER_SKILL_READ = "skill_read"
TRIGGER_SKILL_MENTION = "skill_mention"
TRIGGER_CODE_PATH = "code_path"
TRIGGER_SESSION_START = "session_start"
TRIGGER_COMPACT = "compact"

OUTCOMES = ("injected", "skipped_dedup", "skipped_subthread", "unmapped", "read_failed", "over_budget")

ROLE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s*[：:]?\s*")
_TAG_RE = re.compile(r"^\[[^\]\n]{0,40}\]\s*")
_TITLE_CHARS = 34


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- 映射表 ----------

def load_map():
    """`.workframe-meta/role-memory-map.json` → {skill 全名: 角色}。读不出返回空表（一切 skill 走 unmapped）。"""
    try:
        data = json.loads(MAP_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for role, skills in (data.get("roles") or {}).items():
        if not isinstance(skills, list) or not ROLE_NAME_RE.match(str(role)):
            continue
        for s in skills:
            if isinstance(s, str) and s:
                out[s] = role
    return out


def skill_full_name(name, plugin):
    """映射表的键是带插件前缀的全名；没有前缀的（项目 skill）就用裸名。"""
    return f"{plugin}:{name}" if plugin else name


# ---------- Codex 门的 skill 入口：shell 读 SKILL.md、提示里写 $skill ----------

PROJECT_SKILL_PARENTS = (".agents", ".claude")      # 项目 skill 的两个根（`.claude/skills` 可能是指向前者的链接）
# 不分大小写（Windows 上 `skill.md` 同样读得到）；目录名允许 `~`（8.3 短名，resolve 后还原成长名）
_SKILL_MD_RE = re.compile(r"(?<![A-Za-z0-9_~-])skills[\\/]+([A-Za-z0-9_~-]+)[\\/]+SKILL\.md(?![\w.\-])", re.I)
# shell 命令拆成「词」：相邻的引号段与裸段连成一个词（`'<含空格的根>'/skills/x/SKILL.md`），再去掉引号
_SHELL_WORD_RE = re.compile(r"(?:'[^']*'|\"[^\"]*\"|[^\s'\"])+")
_QUOTED_RE = re.compile(r"'([^']*)'|\"([^\"]*)\"")
_MENTION_RE = re.compile(r"(?<![\w$])\$([A-Za-z0-9_-]+(?::[A-Za-z0-9_-]+)?)(?![\w:-])")
_PLUGIN_NAME = {}


def plugin_name():
    """本插件名（映射表键的前缀）：读插件清单，读不出用 `core`。"""
    if "v" not in _PLUGIN_NAME:
        name = None
        for rel in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
            try:
                name = json.loads((PLUGIN_ROOT / rel).read_text(encoding="utf-8")).get("name")
            except (OSError, ValueError, AttributeError):
                continue
            if isinstance(name, str) and ROLE_NAME_RE.match(name):
                break
            name = None
        _PLUGIN_NAME["v"] = name or "core"
    return _PLUGIN_NAME["v"]


def _plugin_skill(name):
    return (PLUGIN_ROOT / "skills" / name / "SKILL.md").is_file()


def _project_skill(project_dir, name):
    return any((Path(project_dir) / d / "skills" / name / "SKILL.md").is_file() for d in PROJECT_SKILL_PARENTS)


def _under(path, root):
    """`path` 是 `root` 下的 `<名>/SKILL.md` 时返回 `<名>`（大小写按平台规则比），否则 None。两边都已 resolve。"""
    base = os.path.normcase(str(root)).rstrip("\\/") + os.sep
    p = os.path.normcase(str(path))
    if not p.startswith(base):
        return None
    rest = str(path)[len(base):].replace("\\", "/").split("/")
    return rest[0] if len(rest) == 2 and os.path.normcase(rest[1]) == os.path.normcase("SKILL.md") else None


def _skill_of_path(raw, project_dir, cwd):
    """命令里的一个 `…/skills/<名>/SKILL.md` 路径 → skill 全名，或 None。

    按**路径归属**认，不按名字认：解析成绝对路径（相对路径按载荷 `cwd`，没有就按项目根），落在本插件的
    `skills/` 下算本插件的 skill（`<插件名>:<名>`），落在项目 `.agents/skills` / `.claude/skills` 下算项目 skill
    （裸名），都不是（别的插件的同名 skill、随手写出的字面串）不认；文件得真存在。
    """
    try:
        p = Path(raw)
        if not p.is_absolute():
            p = Path(cwd if isinstance(cwd, str) and cwd.strip() else project_dir) / p
        p = p.resolve()
        if not p.is_file():
            return None
        name = _under(p, (PLUGIN_ROOT / "skills").resolve())
        if name:
            return skill_full_name(name, plugin_name())
        for d in PROJECT_SKILL_PARENTS:
            name = _under(p, (Path(project_dir) / d / "skills").resolve())
            if name:
                return name
    except (OSError, ValueError):
        return None
    return None


def skills_read_by_shell(command, project_dir, cwd=None):
    """shell 命令里读到的 `…/skills/<名>/SKILL.md` → skill 全名列表（按出现顺序、去重）。

    Codex 没有 Skill 工具，模型选用 skill 时用 shell 读它的 `SKILL.md`（实测 0.166.0：PostToolUse 的 `tool_name`
    是 `Bash`、`tool_input.command` 形如 `Get-Content -LiteralPath '<路径>/SKILL.md'`；插件 skill 的路径是插件
    缓存目录下的绝对路径）。归属判定见 `_skill_of_path`。**看不出命令是不是「读」**：`echo` 出一条真实存在的
    SKILL.md 路径也算——那种命令罕见，误判的代价是该域本周期多投一次。
    """
    out = []
    if not isinstance(command, str) or "skill.md" not in command.lower():
        return out
    for wm in _SHELL_WORD_RE.finditer(command):
        tok = _QUOTED_RE.sub(lambda q: q.group(1) if q.group(1) is not None else q.group(2), wm.group(0))
        for m in _SKILL_MD_RE.finditer(tok):
            full = _skill_of_path(tok[:m.end()], project_dir, cwd)
            if full and full not in out:
                out.append(full)
    return out


def skills_mentioned(prompt, project_dir):
    """提示里的 `$skill` → skill 全名列表（按出现顺序、去重）。

    Codex 里用户在提示中写 `$<skill>` 即显式选用该 skill，Codex 把 `SKILL.md` 全文直接附进上下文、不经 shell
    （实测 0.166.0：UserPromptSubmit 载荷的 `prompt` 是原文，附进的 skill 正文排在本 hook 的输出之后）。
    `$插件:名` 按全名认；裸名先认项目 skill、再认本插件的。只认磁盘上真有的 skill（`$PATH`、`$HOME` 之类不进来）。
    """
    out = []
    if not isinstance(prompt, str) or "$" not in prompt:
        return out
    for m in _MENTION_RE.finditer(prompt):
        tok = m.group(1)
        if ":" in tok:
            plugin, name = tok.split(":", 1)
            full = tok if plugin == plugin_name() and _plugin_skill(name) else None
        elif _project_skill(project_dir, tok):
            full = tok
        else:
            full = skill_full_name(tok, plugin_name()) if _plugin_skill(tok) else None
        if full and full not in out:
            out.append(full)
    return out


def deliver_for_skills(project_dir, payload, skills, trigger, budget=None):
    """按出现顺序对每个 skill 走一次 `deliver`，返回要投的正文（或 None）。

    **每次只投一个角色的正文**（单条上限放不下两个域的全文）：拿到第一段正文后，后面映射到**别的**角色的 skill
    不判定、不记事件，只在正文后各追加一行路径指针（「同一次还进入了 X 域，本次没投」）——指针的空间先从预算里预留，不让缺口静默；
    下一次进入那个域时照常投全文。没映射到角色的 skill 照记 `unmapped`（与 CC 两条入口同口径）。
    `budget`：本段正文可用的 UTF-16 码元数（与别的内容合并成一条输出时由调用方扣掉那部分），缺省 `DELIVER_BUDGET`。
    """
    budget = DELIVER_BUDGET if budget is None else budget
    mapping = load_map() if skills else {}
    for i, full in enumerate(skills):
        role = mapping.get(full)
        # 其余别的域的指针先算好、从这一域的预算里预留——第一个域占满预算时指针也不能丢
        seen, pointers = {role}, []
        for rest in skills[i + 1:]:
            other = mapping.get(rest)
            if not other or other in seen:
                continue
            seen.add(other)
            rel = _rel(project_dir, memory_dir_of(project_dir) / other / "MEMORY.md")
            pointers.append(f"[workframe] 同一次还进入了 {other} 域（skill `{rest}`）——一次只投一个域，本次没投；"
                            f"用到时 Read `{rel}`，下次进入该域时照常投递。")
        reserve = sum(u16len(p) + 1 for p in pointers)
        text = deliver(project_dir, payload, role, trigger, skill=full, budget=budget - reserve)
        if not text:
            continue
        return "\n".join([text] + pointers)
    return None


# ---------- 读记忆与条目目录 ----------

def read_memory(path):
    """不存在 / 为空 / 读失败 → None（坏字节替换成 U+FFFD，与 subagent-memory-inject 同口径）。"""
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8-sig", errors="replace").strip()
        return text or None
    except OSError:
        return None


def entry_titles(text):
    """MEMORY.md 里每条顶层列表项（行首 `- `）压成一行短标题：`MM-DD 前若干字…`，`[纠正]` 保留在前。"""
    out = []
    for line in (text or "").splitlines():
        if not line.startswith("- "):
            continue
        s = line[2:].strip()
        flag = ""
        if s.startswith("[纠正]"):
            flag, s = "[纠正]", s[len("[纠正]"):].strip()
        date = ""
        m = _DATE_RE.match(s)
        if m:
            date, s = f"{m.group(2)}-{m.group(3)}", s[m.end():]
        s = _TAG_RE.sub("", s).replace("**", "").strip()
        title = s if len(s) <= _TITLE_CHARS else s[:_TITLE_CHARS].rstrip() + "…"
        out.append(" ".join(x for x in (flag, date, title) if x))
    return out


def role_dirs(project_dir):
    """记忆目录下有 MEMORY.md 的角色（不含 shared），按名排序。"""
    mem = memory_dir_of(project_dir)
    try:
        return sorted(p.name for p in mem.iterdir()
                      if p.is_dir() and p.name != "shared" and ROLE_NAME_RE.match(p.name)
                      and (p / "MEMORY.md").is_file())
    except OSError:
        return []


def contract_path(project_dir, role):
    """该角色的契约文件：项目 `.claude/agents/<role>.md` 覆盖插件 `agents/<role>.md`；都没有返回 None。"""
    for p in (Path(project_dir) / ".claude" / "agents" / f"{role}.md", PLUGIN_ROOT / "agents" / f"{role}.md"):
        if p.is_file():
            return p.as_posix()
    return None


# ---------- 去重状态 ----------

def _state_path(project_dir):
    return state_dir_of(project_dir) / STATE_FILE_NAME


def _load_state(project_dir):
    data = read_json_object(_state_path(project_dir), {})
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        data = {}
    return {"schema": STATE_SCHEMA, "sessions": data.get("sessions", {})}


def _save_state(project_dir, state):
    sessions = state["sessions"]
    if len(sessions) > MAX_SESSIONS:
        keep = sorted(sessions, key=lambda k: str(sessions[k].get("touched", "")), reverse=True)[:MAX_SESSIONS]
        state["sessions"] = {k: sessions[k] for k in keep}
    try:
        atomic_write(_state_path(project_dir), json.dumps(state, ensure_ascii=False, indent=1) + "\n")
    except Exception:
        pass  # 写不进去：下一次同域触发会再投一次，代价只是重复


def _session(state, sid):
    rec = state["sessions"].get(sid)
    if not isinstance(rec, dict) or not isinstance(rec.get("delivered"), dict):
        rec = {"cycle": 0, "delivered": {}}
        state["sessions"][sid] = rec
    rec.setdefault("cycle", 0)
    return rec


def _txn(project_dir, mutate):
    """锁内「读状态 → mutate(state) → 写回」，返回 mutate 的返回值。

    不锁的话，同一批并行工具调用里投**不同**角色的两个 hook 进程各读一份旧快照、先后写回，后写的抹掉先写的
    那条已投记录——压缩后补投就漏了那个域。锁拿不到（1 秒）就不锁照做：退回「偶尔漏记一条」，不拦工具调用。
    """
    try:
        with FileLock(_state_path(project_dir).with_suffix(".lock"), timeout=1.0):
            state = _load_state(project_dir)
            result = mutate(state)
            _save_state(project_dir, state)
            return result
    except TimeoutError:
        state = _load_state(project_dir)
        result = mutate(state)
        _save_state(project_dir, state)
        return result


def _stamp():
    """去重状态里的使用时刻：微秒级，补投按它排「最近使用」——秒级的话同一秒里的先后分不出来。"""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def u16len(s):
    """CC 的注入上限按 JS 字符串长度（UTF-16 码元）计；BMP 外的字符（emoji 等）一个占两个，Python `len` 只算一个。

    与 `inject-context.py` 的 `u16len` 同一口径；不 import 它：那是连字符命名的 hook 入口脚本，模块级有副作用。
    """
    return len(s.encode("utf-16-le")) // 2


def _clip(s, budget, tail):
    """把 s 截到 UTF-16 码元数 ≤ budget（末尾接上 tail）。"""
    if u16len(s) <= budget:
        return s
    room = budget - u16len(tail)
    out, n = [], 0
    for ch in s:
        w = 2 if ord(ch) > 0xFFFF else 1
        if n + w > room:
            break
        out.append(ch)
        n += w
    return "".join(out).rstrip() + tail


# ---------- 事件 ----------

def _event(project_dir, sid, role, outcome, trigger, cycle=None, **extra):
    rec = {"ts": _now(), "type": EVENT_TYPE, "role": role, "outcome": outcome, "trigger": trigger}
    if sid:
        rec["session_id"] = sid
    if cycle is not None:
        rec["cycle"] = cycle
    rec.update({k: v for k, v in extra.items() if v is not None})
    append_line(state_dir_of(project_dir) / "events.jsonl", event_json(rec))


# ---------- 文本 ----------

_TRIGGER_DESC = {
    TRIGGER_SKILL_TOOL: "调用 skill `{skill}`",
    TRIGGER_COMMAND: "用户直接输入 `/{skill}`",
    TRIGGER_SKILL_READ: "读取 skill `{skill}` 的 SKILL.md",
    TRIGGER_SKILL_MENTION: "用户在提示里写 `${skill}`",
    TRIGGER_CODE_PATH: "首次改到模块代码路径 `{file}`",
    TRIGGER_COMPACT: "压缩后补投",
}


def _rel(project_dir, path):
    try:
        return path.relative_to(project_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _role_block(project_dir, role, text, desc, budget):
    """(正文, outcome)。放得下给全文，放不下给条目目录 ＋ 路径并标「未完整加载」。"""
    path = memory_dir_of(project_dir) / role / "MEMORY.md"
    rel = _rel(project_dir, path)
    cp = contract_path(project_dir, role)
    contract = f"该域角色契约（工程纪律／产物合同／状态流转）：`{cp}`，直做前读一遍。" if cp else ""
    head = f"[workframe] 角色记忆投递（{desc}）——{role} 域"
    full = (f"{head}\n本压缩周期内首次进入该域，以下是 `{rel}` 全文，本周期不再重复投递。{contract}\n"
            f"=== {rel} ===\n{text}\n=== 记忆投递结束 ===")
    if u16len(full) <= budget:
        return full, "injected"
    titles = "\n".join(f"  · {t}" for t in entry_titles(text)) or "  （无列表条目）"
    short = (f"{head} · **未完整加载**\n`{rel}` 共 {len(text)} 字，超过本次投递的预算，下面只有条目目录；"
             f"用到哪条就 Read 该文件取全文。{contract}\n{titles}\n=== 记忆投递结束 ===")
    return _clip(short, budget, "\n…（目录过长已截断）\n=== 记忆投递结束 ==="), "over_budget"


def _read_failed_text(project_dir, role, desc):
    rel = _rel(project_dir, memory_dir_of(project_dir) / role / "MEMORY.md")
    return (f"[workframe] 角色记忆投递（{desc}）——{role} 域 · **未加载**：`{rel}` 不存在、为空或读取失败，"
            "该域没有可用的已沉淀记忆。")


def _fork_pointer(project_dir, payload, sid, role, trigger, skill, file):
    """fork 线程首次进入某域时的一行指针；同一 fork 同域只给一次。

    不投正文：去重键是父会话的 `session_id`，fork 里投了就得记进父会话（父会话其实没收到，压缩补投会漏），
    不记又每次都投。指针的去重另用 `<session_id>#fork:<agent_id>` 这个键，与父会话的记录互不相干。
    """
    key = f"{sid}#fork:{payload.get('agent_id')}"

    def _mark(state):
        rec = _session(state, key)
        seen = role in rec["delivered"]
        rec["delivered"][role] = rec["touched"] = _stamp()
        return seen
    if _txn(project_dir, _mark):
        return None
    desc = _TRIGGER_DESC.get(trigger, trigger).format(skill=skill or "", file=file or "")
    rel = _rel(project_dir, memory_dir_of(project_dir) / role / "MEMORY.md")
    return (f"[workframe] 角色记忆投递（{desc}）——{role} 域 · **fork 线程不投正文**：父会话派生前投过的该域全文"
            f"在继承的上下文里；找不到就 Read `{rel}`。本 fork 内该域不再提示。")


# ---------- 三个时点 ----------

def _subthread(payload):
    """载荷来自子线程：带 `agent_id`；或不带它、但 transcript 是 fork 出来的 Codex 子线程（rollout 首行带
    `forked_from_id`）——Codex 子线程上 UserPromptSubmit / SessionStart 的载荷带不带 `agent_id` 没有实测，
    fork 的历史里已有父线程那套，不能按主线程投、也不能记进父会话的去重状态。"""
    return bool(payload.get("agent_id")) or _harness.rollout_forked(payload.get("transcript_path"))


def deliver(project_dir, payload, role, trigger, skill=None, file=None, budget=None):
    """工作入口：返回要投的正文，或 None（不投）。判定顺序：子线程 → 映射 → 去重 → 读 → 预算。

    `budget` 缺省 `DELIVER_BUDGET`；与别的内容合并成一条输出的调用方传扣掉那部分之后的值。

    `role` 为 None 表示动作没有映射到任何角色（记 unmapped）。调用方只在主线程工作入口调用；
    输入带 `agent_id`（子 agent，含 fork）一律不投正文——普通子 agent 启动时已由 SubagentStart 注入。fork 那里
    SubagentStart 不注入，它只有派生前父会话投过的那些，所以给一行指针（见 `_fork_pointer`），不让缺口静默。
    """
    payload = payload if isinstance(payload, dict) else {}
    sid = _harness.session_id(payload)
    if _subthread(payload):
        _event(project_dir, sid, role, "skipped_subthread", trigger, skill=skill, file=file,
               agent_type=payload.get("agent_type") if isinstance(payload.get("agent_type"), str) else None)
        if role and sid and payload.get("agent_id") and _harness.is_fork(payload):
            return _fork_pointer(project_dir, payload, sid, role, trigger, skill, file)
        return None
    if not role:
        _event(project_dir, sid, None, "unmapped", trigger, skill=skill)
        return None
    text = read_memory(memory_dir_of(project_dir) / role / "MEMORY.md")
    cycle, dup = None, False
    if sid:
        def _mark(state):
            rec = _session(state, sid)
            seen = role in rec["delivered"]
            if seen or text is not None:
                # 去重命中也刷新时刻：压缩补投按「最近使用」排，不按首次投递排
                rec["delivered"][role] = rec["touched"] = _stamp()
            return rec["cycle"], seen
        cycle, dup = _txn(project_dir, _mark)
    if dup:
        _event(project_dir, sid, role, "skipped_dedup", trigger, cycle, skill=skill, file=file)
        return None
    desc = _TRIGGER_DESC.get(trigger, trigger).format(skill=skill or "", file=file or "")
    if text is None:
        _event(project_dir, sid, role, "read_failed", trigger, cycle, skill=skill, file=file)
        return _read_failed_text(project_dir, role, desc)
    out, outcome = _role_block(project_dir, role, text, desc, DELIVER_BUDGET if budget is None else budget)
    _event(project_dir, sid, role, outcome, trigger, cycle, skill=skill, file=file, chars=u16len(out))
    return out


def opening_text(project_dir, payload):
    """开场：shared 全文 ＋ 各角色条目目录；超 `OPENING_BUDGET` 时 shared 也降为条目目录。返回正文或 None。"""
    payload = payload if isinstance(payload, dict) else {}
    sid = _harness.session_id(payload)
    mem = memory_dir_of(project_dir)
    shared_path = mem / "shared" / "MEMORY.md"
    shared_rel = _rel(project_dir, shared_path)
    shared = read_memory(shared_path)
    roles = role_dirs(project_dir)
    if shared is None and not roles and not mem.is_dir():
        return None  # 整个记忆目录都不在：不是装过记忆骨架的项目，装机验收与 doctor 会报，这里不每会话重复
    lines = ["[workframe] 角色记忆条目目录（正文不在此。进入某域 skill、或首次改到模块代码路径时"
             "由 hook 投递该域全文；没有触发而用得上时按路径 Read）"]
    for role in roles:
        p = mem / role / "MEMORY.md"
        titles = entry_titles(read_memory(p) or "")
        if not titles:
            lines.append(f"- {role}：无条目（`{_rel(project_dir, p)}`）")
            continue
        lines.append(f"- {role}（{len(titles)} 条，`{_rel(project_dir, p)}`）")
        lines.extend(f"  · {t}" for t in titles)
    directory = "\n".join(lines)
    if shared is None:
        _event(project_dir, sid, "shared", "read_failed", TRIGGER_SESSION_START)
        head = (f"[workframe] 跨角色权威事实 · **未加载**：`{shared_rel}` 不存在、为空或读取失败。")
        return head + "\n" + directory
    block = (f"[workframe] 跨角色权威事实（开场投递，`{shared_rel}` 全文；与单角色记忆冲突时以它为准）\n"
             f"=== {shared_rel} ===\n{shared}\n=== shared 结束 ===")
    outcome = "injected"
    # shared 全文是常驻主体、条目目录是兜底：超预算时先截目录；shared 自己就放不下（再留 200 给目录头）才降级它
    if u16len(block) + 200 > OPENING_BUDGET:
        titles = "\n".join(f"  · {t}" for t in entry_titles(shared))
        block = (f"[workframe] 跨角色权威事实 · **未完整加载**：`{shared_rel}` 共 {len(shared)} 字，超过开场投递预算，"
                 f"下面只有条目目录——开工前 Read 该文件取全文。\n{titles}")
        block = _clip(block, OPENING_BUDGET // 2, "\n…（目录过长已截断）")
        outcome = "over_budget"
    room = OPENING_BUDGET - u16len(block) - 1
    text = block + "\n" + _clip(directory, room, "\n…（目录过长已截断，各文件全文按路径 Read）")
    _event(project_dir, sid, "shared", outcome, TRIGGER_SESSION_START, chars=u16len(text))
    return text


def restore_after_compact(project_dir, payload):
    """压缩后：按最近使用顺序补投本周期已投过的角色，周期号 +1。返回正文或 None。

    合计超 `DELIVER_BUDGET` 时，放不下全文的角色给条目目录 ＋ 路径（outcome=over_budget），
    且不记进新周期——之后再触发该域时照常投全文。
    """
    payload = payload if isinstance(payload, dict) else {}
    sid = _harness.session_id(payload)
    if not sid:
        return None
    if _subthread(payload):
        # 子 agent 自动压缩后也会跑 SessionStart，`session_id` 是父会话的：不补投、也不动父会话的周期
        _event(project_dir, sid, None, "skipped_subthread", TRIGGER_COMPACT,
               agent_type=payload.get("agent_type") if isinstance(payload.get("agent_type"), str) else None)
        return None
    def _rotate(state):
        rec = _session(state, sid)
        delivered = dict(rec["delivered"])
        rec["cycle"] = int(rec.get("cycle") or 0) + 1
        rec["delivered"] = {}
        rec["touched"] = _stamp()
        return rec["cycle"], delivered
    new_cycle, delivered = _txn(project_dir, _rotate)
    order = sorted(delivered, key=lambda r: str(delivered[r]), reverse=True)
    kept, parts, used = {}, [], 0
    desc = _TRIGGER_DESC[TRIGGER_COMPACT]
    for role in order:
        if not ROLE_NAME_RE.match(role):
            continue
        room = DELIVER_BUDGET - used - (2 if parts else 0)
        rel = _rel(project_dir, memory_dir_of(project_dir) / role / "MEMORY.md")
        text = read_memory(memory_dir_of(project_dir) / role / "MEMORY.md")
        if text is None:
            out, outcome = _read_failed_text(project_dir, role, desc), "read_failed"
        else:
            out, outcome = _role_block(project_dir, role, text, desc, room)
            if u16len(out) > room:
                # 连条目目录都放不下：只留一行路径
                out = f"[workframe] {role} 域记忆压缩后**未补投**（超预算），用到时 Read `{rel}`。"
        placed = u16len(out) <= room
        if placed:
            parts.append(out)
            used += u16len(out) + (2 if len(parts) > 1 else 0)
        else:
            outcome = "over_budget" if outcome != "read_failed" else outcome  # 一行提示也放不下：只记事件
        if outcome == "injected":
            kept[role] = delivered[role]  # 沿用原时刻，新周期里的先后不变
        _event(project_dir, sid, role, outcome, TRIGGER_COMPACT, new_cycle,
               chars=u16len(out) if placed else 0)
    if kept:
        def _keep(state):
            rec = _session(state, sid)
            for role, ts in kept.items():
                rec["delivered"].setdefault(role, ts)  # 补投之后才投的（并发）以那次为准
        _txn(project_dir, _keep)
    return "\n\n".join(parts) if parts else None


def hook_output(event_name, text):
    """PostToolUse / UserPromptExpansion 的整串 stdout：`hookSpecificOutput.additionalContext` JSON，ASCII 转义。"""
    return json.dumps({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}},
                      ensure_ascii=True)
