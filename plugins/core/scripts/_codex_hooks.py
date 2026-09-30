#!/usr/bin/env python3
"""Codex hook 清单的**唯一事实源**：派生规则、hash 覆盖的字段集、清单条目与 manifest 形态。

为什么单独成模块（形态同 `_coverage_probe.py`：全仓唯一一份判定式，多个调用方 import）：
生成器（`gen_codex_hooks.py`）、validate 的几道 `check_codex_*` 闸、以及装机器 /
`codex-trust-seed.py` 要比对的种信任记录，问的都是同一个问题——「这份 Codex 清单每条
由哪些字段构成、条数应当是多少、哪一条被冻结」。三处各抄一份字段表，给某条加一个
`timeout` 时闸绿、manifest 不必改、CHANGELOG 不写「需重批」，而升级后该条在 Codex 侧
`modified` 静默停跑。字段集只在这里定义一次。

**派生规则**（CC `hooks/hooks.json` → `hooks/hooks.codex.json`，确定性、无平台分支）：
  1. 去掉 Codex 没有的事件（`CODEX_ABSENT_EVENTS`）；
  2. 每条命令的 `--harness cc` 换成 `--harness codex`（恰好一处，多一处少一处都报错）；
  3. 打包器的分片挂载（`inject-context.py … --shard i/n`）同一 (事件, scope) 合成**一条**
     `--shard all`，位置取第一片所在位置；
  4. 每条补 `commandWindows`（Windows + PowerShell 专用形态：`& "<launcher>.cmd" …`，
     正斜杠、`.cmd` 必加、不写解释器绝对路径——三条硬约束由 validate 钉）；
  5. 注入类事件（`CODEX_INJECTOR_EVENTS`）每条 `additionalContextLimit: 0`（不限 spill）；
     其他事件不带该键——`Stop` / `SubagentStop` / `SessionEnd` 带它实测只是 `hooks/list`
     报 warning 并忽略该字段（条目保留），`PostToolUse` 静默接受；本规则严于实测、方向 fail-safe；
  6. `UserPromptSubmit` 组末尾追加 Codex 专用纪律 hook（`--scope prompt`）；
     `SessionStart` 组末尾追加种信任 hook（`codex-trust-seed.py`，命令行冻结）。
  7. `SessionStart` 上两个**纯文本输出**的脚本（`HOOK_JSON_SCRIPTS`）命令末尾加固定参数
     `--output hook-json`：Codex 把以 `[` / `{` 起头的 stdout 按 JSON 对象解析、解析不到即
     整条丢弃并记 `failed`（实测），而这两个脚本的输出都以 `[…]` 标签行起头。参数只进
     Codex 清单，CC 命令不变；CC 门下 `_harness.hook_json_stdout` 同样接管 stdout，平时原样写回，只有本次
     补建 / 重指了 `.claude/skills` 时才改投带 `reloadSkills` 的 JSON。
     `check-stale-modules.py scan-git-diff` 不在内：它在 hook 路径上零 stdout。
  **新增只在组末尾**：Codex 的信任记录按 `<事件>:<组序>:<条序>` 定位，中插会让其后各条
  全部 `modified`，用户升级后整组静默停跑。

**hash 覆盖面**（`HOOK_HASH_FIELDS` ＋ 组级 `matcher`）来自实测：六个 handler 字段 ＋
组级 matcher ＋ handler `type` 全部计入 Codex 的 `currentHash`；`version` 不计入；
变量 `${CLAUDE_PLUGIN_ROOT}` 按**字面**计入而不是展开路径。**Windows 上 `commandWindows`
在场时 POSIX `command` 不入 hash**——所以 POSIX 那一行漂了 Codex 自己不会报，
manifest 冻结闸是它唯一的防线。

被 CLI 脚本与校验器以同目录 / sys.path import 方式使用，因此：
  - 模块 import 必须无副作用（不碰 stdout、不读环境、不建目录、不读磁盘）
  - 文件名用下划线而非连字符——连字符的模块名 import 不进来
"""

import json
import re

# ---- 字段集（hash 覆盖面）----
HOOK_HASH_FIELDS = ("type", "command", "commandWindows", "timeout",
                    "statusMessage", "additionalContextLimit", "async")
GROUP_HASH_FIELDS = ("matcher",)

# ---- 事件集 ----
CODEX_ABSENT_EVENTS = ("Setup", "StopFailure", "ConfigChange", "UserPromptExpansion")
CODEX_INJECTOR_EVENTS = ("SessionStart", "UserPromptSubmit", "SubagentStart")

# ---- 路径（插件根相对）----
CC_MANIFEST_REL = "hooks/hooks.json"
CODEX_MANIFEST_REL = "hooks/hooks.codex.json"
CODEX_BASELINE_REL = "hooks/hooks.codex.manifest.json"

# ---- 命令形态 ----
LAUNCHER_POSIX = '"${CLAUDE_PLUGIN_ROOT}/bin/workframe-python"'
LAUNCHER_WINDOWS = '& "${CLAUDE_PLUGIN_ROOT}/bin/workframe-python.cmd"'
SCRIPTS_PREFIX = '"${CLAUDE_PLUGIN_ROOT}/scripts/'
INJECTOR_SCRIPT = "inject-context.py"
SHARD_ALL_TOKEN = "--shard all"

# Codex 清单在 CC 清单之外**多出**的两条（追加在各自事件的组末尾）。
PROMPT_COMMAND = (f'{LAUNCHER_POSIX} {SCRIPTS_PREFIX}{INJECTOR_SCRIPT}" '
                  f"--harness codex --scope prompt")
SEED_SCRIPT = "codex-trust-seed.py"
SEED_COMMAND = f'{LAUNCHER_POSIX} {SCRIPTS_PREFIX}{SEED_SCRIPT}" --harness codex'
CODEX_EXTRA = (("UserPromptSubmit", PROMPT_COMMAND), ("SessionStart", SEED_COMMAND))
# 命令行冻结的条目：种信任 hook 自己的命令行一旦变了，它在用户机器上就变成 `modified`
# 而不再执行——此时没有任何东西能替用户补种。改它 = 改整套升级机制。
FROZEN_SCRIPTS = (SEED_SCRIPT,)
# `SessionStart` 上纯文本输出的脚本：Codex 清单里各带 `--output hook-json`（派生规则 7）。
# **只有这两个**：`check-stale-modules.py scan-git-diff` 在 hook 路径上零 stdout（它的每一处
# print 都在非 hook 子命令分支里），给它加参数只会无故动一条冻结命令行。
HOOK_JSON_EVENT = "SessionStart"
HOOK_JSON_SCRIPTS = ("heartbeat-check.py", "session-start-prep.py")
HOOK_JSON_TOKEN = "--output hook-json"

_HARNESS_CC_RE = re.compile(r"(?<!\S)--harness cc(?!\S)")
_SHARD_RE = re.compile(r"--shard \d+/\d+")
_SCOPE_RE = re.compile(r"--scope (\w+)")


class DeriveError(Exception):
    """派生失败：CC 清单里出现了派生规则认不出的形态。**报错而不是跳过**——跳过会让
    某条 hook 在 Codex 门下静默缺席，与正常同形。"""


def snake_event(event):
    """`SessionStart` → `session_start`（Codex 信任记录 key 里事件名的形态）。"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()


def windows_command(command):
    """POSIX 形态 → Windows/PowerShell 形态：只换 launcher 那一段，其余逐字保留。"""
    if not command.startswith(LAUNCHER_POSIX + " "):
        raise DeriveError(f"命令不以 POSIX launcher 开头，无法派生 commandWindows: {command!r}")
    return LAUNCHER_WINDOWS + command[len(LAUNCHER_POSIX):]


def codex_command(command):
    """`--harness cc` → `--harness codex`，恰好一处。"""
    n = len(_HARNESS_CC_RE.findall(command))
    if n != 1:
        raise DeriveError(f"命令里 `--harness cc` 出现 {n} 次（应恰 1 次）: {command!r}")
    return _HARNESS_CC_RE.sub("--harness codex", command)


def needs_hook_json(event, command):
    """派生规则 7 的判定：SessionStart 上、脚本在 `HOOK_JSON_SCRIPTS` 内、且尚未带该参数。"""
    if event != HOOK_JSON_EVENT or HOOK_JSON_TOKEN in command:
        return False
    return any(f"{SCRIPTS_PREFIX}{s}\"" in command for s in HOOK_JSON_SCRIPTS)


def hook_entry(event, command):
    """一条 Codex hook 的 JSON 形态（键序即输出序）。"""
    if needs_hook_json(event, command):
        command = f"{command} {HOOK_JSON_TOKEN}"
    entry = {"type": "command", "command": command,
             "commandWindows": windows_command(command)}
    if event in CODEX_INJECTOR_EVENTS:
        entry["additionalContextLimit"] = 0
    return entry


def derive(cc_data):
    """CC 清单（已解析的 dict）→ Codex 清单（dict）。确定性：同输入同输出。"""
    hooks = cc_data.get("hooks") if isinstance(cc_data, dict) else None
    if not isinstance(hooks, dict) or not hooks:
        raise DeriveError("CC 清单没有 `hooks` 段或为空")
    out = {}
    for event, groups in hooks.items():
        if event in CODEX_ABSENT_EVENTS:
            continue
        seen_scopes = set()
        new_groups = []
        for grp in groups or []:
            new_hooks = []
            for h in grp.get("hooks") or []:
                if h.get("type") != "command":
                    raise DeriveError(f"{event}: 非 command 类型的 hook 不在派生规则内: {h!r}")
                cmd = h.get("command") or ""
                if INJECTOR_SCRIPT in cmd and _SHARD_RE.search(cmd):
                    m = _SCOPE_RE.search(cmd)
                    scope = m.group(1) if m else None
                    if scope is None:
                        raise DeriveError(f"{event}: 分片挂载缺 `--scope`: {cmd!r}")
                    if scope in seen_scopes:
                        continue                      # 同 (事件, scope) 只留第一片的位置
                    seen_scopes.add(scope)
                    cmd = _SHARD_RE.sub(SHARD_ALL_TOKEN, cmd, count=1)
                new_hooks.append(hook_entry(event, codex_command(cmd)))
            new_groups.append({"matcher": grp.get("matcher", ""), "hooks": new_hooks})
        out[event] = new_groups
    for event, command in CODEX_EXTRA:
        if event not in out or not out[event]:
            raise DeriveError(f"要追加到 `{event}` 组末尾，但 Codex 清单里没有这个事件")
        out[event][-1]["hooks"].append(hook_entry(event, command))
    return {"hooks": out}


def render(data):
    """清单 / manifest 的唯一序列化形态：2 空格缩进、保留非 ASCII、LF、末尾换行。"""
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def entries(codex_data):
    """Codex 清单 → 逐条 `{key, event, matcher, <HOOK_HASH_FIELDS>...}`。

    key 形态 `hooks/hooks.codex.json:<snake_event>:<组序>:<条序>`——与 Codex 信任记录
    key 的尾段同形（其前缀 `<plugin>@<marketplace>:` 由用户装机时的市场名决定，仓里不知道）。
    """
    out = []
    for event, groups in (codex_data.get("hooks") or {}).items():
        for gi, grp in enumerate(groups or []):
            for hi, h in enumerate(grp.get("hooks") or []):
                row = {"key": f"{CODEX_MANIFEST_REL}:{snake_event(event)}:{gi}:{hi}",
                       "event": event, "matcher": grp.get("matcher", "")}
                for f in HOOK_HASH_FIELDS:
                    row[f] = h.get(f)
                out.append(row)
    return out


def hash_tuple(row):
    """一条的 hash 覆盖面取值（组级 matcher ＋ HOOK_HASH_FIELDS），供逐字段比对。"""
    return tuple([row.get("matcher")] + [row.get(f) for f in HOOK_HASH_FIELDS])


def is_frozen(row):
    return any(s in (row.get("command") or "") for s in FROZEN_SCRIPTS)


def build_manifest(codex_data):
    """人维护的冻结基线的**初始 / 重新接受**形态。只经 `gen_codex_hooks.py --accept-manifest`
    写出；平时它不随生成器变——那正是它能抓「改了 hooks.json、重生成、没人 accept」的原因。"""
    rows = []
    for row in entries(codex_data):
        item = {"key": row["key"], "event": row["event"], "matcher": row["matcher"]}
        for f in HOOK_HASH_FIELDS:
            item[f] = row[f]
        if is_frozen(row):
            item["frozen"] = True
        rows.append(item)
    return {
        "__doc__": ("Codex hook 清单的冻结基线（人维护）。每条的 hash 覆盖面字段与 "
                    "hooks.codex.json 逐字段相等、key 序为其前缀（只许末尾追加），由 validate "
                    "的 check_codex_hook_lines_frozen 钉住。有意改动：跑 gen_codex_hooks.py "
                    "--accept-manifest 重新接受，并在对外 CHANGELOG 未发布段写「升级后需重批 N 条」。"
                    "frozen: true 的条目是种信任 hook 本身，其命令行任何变化都要单独审。"),
        "fields": list(GROUP_HASH_FIELDS + HOOK_HASH_FIELDS),
        "entries": rows,
    }


def count_hooks(data, events=None):
    """一份清单（dict）里的 hook 条数；`events` 给定时只数这些事件。"""
    n = 0
    for event, groups in (data.get("hooks") or {}).items():
        if events is not None and event not in events:
            continue
        for grp in groups or []:
            n += len(grp.get("hooks") or [])
    return n


def independent_count(cc_data, shard_count_of):
    """**不经生成器**、直接从 CC 清单算出的 Codex 条数：

        CC 总条数 − 缺口事件的条数 − Σ_scope (n_scope − 1) ＋ 追加的条数

    `shard_count_of(scope)` 由调用方给（打包器现算的片数），这样这个数与生成器的
    合成逻辑不同源——生成器少走一步（比如漏合成某个 scope）时两个数对不上。
    """
    total = count_hooks(cc_data)
    absent = count_hooks(cc_data, CODEX_ABSENT_EVENTS)
    scopes = []
    for event, groups in (cc_data.get("hooks") or {}).items():
        if event in CODEX_ABSENT_EVENTS:
            continue
        for grp in groups or []:
            for h in grp.get("hooks") or []:
                cmd = h.get("command") or ""
                if INJECTOR_SCRIPT in cmd and _SHARD_RE.search(cmd):
                    m = _SCOPE_RE.search(cmd)
                    if m and m.group(1) not in scopes:
                        scopes.append(m.group(1))
    collapsed = sum(max(int(shard_count_of(s)), 1) - 1 for s in scopes)
    return total - absent - collapsed + len(CODEX_EXTRA)
