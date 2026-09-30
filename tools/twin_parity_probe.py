#!/usr/bin/env python3
"""两门动态对等探针：同一个项目在 Claude Code 与 Codex 各跑一段固定脚本后，比三样东西。

**这是框架仓根工具，不随插件分发；带凭据的那半由维护者跑。** 零凭据能自证的只有本脚本自身的
解析与比对逻辑（`--selftest`），以及对既有运行态记录的采集（`--collect`）。

三样比对与各自的取证面：
  ① **scope 级注入指纹**：两门 inject-log（`<state>/inject-log.jsonl`）同 scope（main / sub）最近一次
     记录的 `scope_sha256` 相等。它按源文件内容算、与分片无关（CC 主片切 n 片、Codex 一条不分片，
     逐片 `sha256` 按构造永远不等——比那个会恒红）。取证面现成。
  ② **角色记忆注入**：`subagent-memory-inject.py` 两门产出的 `additionalContext` 的 sha256 相等。
     **该脚本不写任何 log**，所以本脚本直接以两门形态（`--harness cc` / `--harness codex`）调用它、喂同一份
     载荷、比 stdout——这是**结构**证据（证脚本对两门产出相同），不是投递后到达相同。真实到达面要读
     Claude Code 的会话记录（`~/.claude/projects/<项目>/<会话>/subagents/*.jsonl` 里 SubagentStart 的
     hook 附件）与 Codex 子线程 rollout 的 `hooks.additional_context` 项，见 `--recipe`。
  ③ **事件类型集合**：两门各自写进 `events.jsonl` 的事件类型集合（按 `harness` 字段分门；无该字段的
     行按事件注册表口径读作 cc）。**不用「CC − Codex == 声明缺口集合」**——注册表里 `codex: none` 的
     集合含 `rule_triggered`，它两门都产不出（CC 侧永远不出现），等式左边永远缺它，`==` 恒假。拆成三条：
       (1) `Codex − CC == ∅`：Codex 不许多出 CC 没有的事件类型；
       (2) `CC − Codex ⊆ G`，G = 注册表 `codex: none` 减去两门都 none 的（现算），多出任一即红；
       (3) 本脚本自声明的确定性事件集 S ⊆ 两门各自的产出集。S 只放固定脚本**必然**产出的：SessionEnd
           链上的 `session_ended` / `summary_recomputed` / `skill_metrics_recomputed`（三者在看板缺失、
           重算失败时仍以 skipped / error 状态写同类型的行）。`skill_used` 是模型半、不进 S：零 `skill_used`
           可能是模型没调，也可能是投递没到（注册表 `__delivery_established__.codex` 仍 false）——先怀疑投递。

用法：
    python tools/twin_parity_probe.py --recipe
        打印两门各自要跑的固定脚本与取证步骤（带凭据、由维护者执行；**本文件的这一半未实测**）。
    python tools/twin_parity_probe.py --collect --door cc|codex --project <项目根> --since <ISO-8601> --out cc.json
        从该项目的运行态目录采集 ①③ 并现算 ②，写出一份门 JSON。只读项目，不写运行态。
        **`--since` 是取证窗口**：只取 `ts >= since` 的 inject-log / events 行（无 `ts` 或解析不了的行不进窗口、
        计入 notes）。不给它就是全历史——在一个用过 Claude Code 一段时间的项目上，CC 侧历史事件类型
        远多于一次会话能产出的，③(2) 会假 FAIL、③(1) 失去判别力；所以 `--recipe` 要求先记开跑时刻。
        不按 `session_id` 取窗口：它只在模型半事件（skill_used / skill_invoked 等）上有，SessionEnd 链
        写的三条确定性事件没有它，按它取会把 S 整个筛掉、③(3) 恒 FAIL。
        退出码：0 采集完成 / 2 输入读不到（`--project` 不是目录、或它的运行态目录不存在）。
    python tools/twin_parity_probe.py --compare cc.json codex.json
        逐项 PASS / FAIL / 未能判定。退出码：0 全 PASS / 1 有 FAIL / 2 输入读不到 / 3 无 FAIL 但有未能判定。
    python tools/twin_parity_probe.py --selftest
        零凭据自证：构造样本对（相等 / 只改一处）逐条证明每条判定各自会红；带历史噪声的 events 样本证
        `--since` 窗口把假红筛掉；`--collect` 对不存在的输入 exit 2。

失败方式（带凭据才能真打）：在 `plugins/core/context/sub/` 加一个文件、只让一门读到 → ① 的 sub 必不等；
在构造样本层面用「改一份 scope_sha256」代证分辨力（`--selftest`）。

能力边界：Codex 侧只在 Windows 上实测过；③ 依赖模型真的调了 skill / 写了纠正，那半不进 S。
② 的载荷 `agent_type` 固定为 `core:dev`：脚本按 `rsplit(":")` 取角色名，`core:dev` 与裸 `dev` 产出同一份
正文（实测同 sha）——Codex 真实 payload 里 `agent_type` 是哪种形态尚未实测，两种都命中同一角色。
G 按**本仓**的 `event-schema.json` 现算；被采集项目那门装的插件若是别的版本，其 schema 可能不同——
`--collect` 把该门 `plugin-root.<door>.txt` 指向的 schema sha 记进 JSON，`--compare` 发现与本仓不同时提示。
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "core"
SCRIPTS = PLUGIN_ROOT / "scripts"
DOORS = ("cc", "codex")
SCOPES = ("main", "sub")
# 固定脚本必然产出的事件类型（hook_deterministic，两门皆 full；见 session-end-flush.py 三个 _stage）
DETERMINISTIC_EVENTS = ("session_ended", "summary_recomputed", "skill_metrics_recomputed")
MODEL_HALF_EVENTS = ("skill_used", "user_correction")
MEMORY_PAYLOAD_AGENT = "core:dev"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import _harness  # noqa: E402  —— 事件注册表读取（可得性图）
import _state_io  # noqa: E402  —— 运行态目录定位（只此一份）


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_jsonl(path):
    """可解析的行；坏行跳过并计数（本脚本只回答「记过什么」，不修文件）。"""
    rows, bad = [], 0
    if not path.is_file():
        return rows, bad, False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            bad += 1
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows, bad, True


def gap_events():
    """G：注册表里 `codex: none` 且 `cc` 不为 none 的事件类型。**现算、不手抄**。"""
    none_codex = set(_harness.events_by_availability("codex", "none"))
    none_both = set(_harness.events_by_availability("cc", "none")) & none_codex
    return sorted(none_codex - none_both), sorted(none_both)


# ---- 采集 ----

class ProbeInputError(Exception):
    """`--collect` 的输入读不到（项目目录 / 运行态目录不存在）——main 转 exit 2，不写空 JSON。"""


def parse_ts(value):
    """ISO-8601 → aware datetime；无时区按 UTC（事件流的 ts 口径就是 UTC）。解析不了返回 None。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def window(rows, since):
    """取证窗口：since 为 None 时原样返回；否则只留 `ts >= since` 的行。返回 (留下的行, 无 ts / 解析不了而被筛掉的行数)。"""
    if since is None:
        return rows, 0
    kept, dropped = [], 0
    for r in rows:
        t = parse_ts(r.get("ts"))
        if t is None:
            dropped += 1
            continue
        if t >= since:
            kept.append(r)
    return kept, dropped


def read_events(path, door, since=None):
    """③ 的取证：events.jsonl 里本门的行（无 harness 字段的行按注册表口径读作 cc；`unknown` 不归任何门）。
    返回 (事件类型排序列表, 本门行数, notes)。"""
    notes = []
    rows, bad, present = _read_jsonl(path)
    if not present:
        notes.append("events.jsonl 不存在")
    if bad:
        notes.append(f"events.jsonl 有 {bad} 行解析不了，已跳过")
    rows, dropped = window(rows, since)
    if dropped:
        notes.append(f"events.jsonl 有 {dropped} 行无 ts 或 ts 解析不了，不进取证窗口")
    types, n = set(), 0
    for r in rows:
        h = r.get("harness")
        if h == door or (door == "cc" and "harness" not in r):
            n += 1
            if isinstance(r.get("type"), str):
                types.add(r["type"])
    return sorted(types), n, notes


def schema_of_door(state, door):
    """该门 `plugin-root.<door>.txt` 指向的插件根，及其 event-schema.json 的 sha256（读不到的一律 None）。"""
    p = state / f"plugin-root.{door}.txt"
    if not p.is_file():
        return None, None
    root = p.read_text(encoding="utf-8", errors="replace").strip()
    schema = Path(root) / ".workframe-meta" / "event-schema.json"
    if not root or not schema.is_file():
        return root or None, None
    return root, hashlib.sha256(schema.read_bytes()).hexdigest()


def own_schema_sha():
    p = PLUGIN_ROOT / ".workframe-meta" / "event-schema.json"
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def collect(door, project, memory_agent=MEMORY_PAYLOAD_AGENT, since=None):
    if door not in DOORS:
        raise ValueError(f"door 须为 {DOORS}")
    project = Path(project).resolve()
    if not project.is_dir():
        raise ProbeInputError(f"--project 不是目录: {project}")
    state = _state_io.state_dir_of(project)
    if not state.is_dir():
        raise ProbeInputError(f"运行态目录不存在: {state}——该项目没跑过任何一门的会话，没有可采集的对象")
    plugin_root, schema_sha = schema_of_door(state, door)
    out = {"door": door, "project": str(project), "state_dir": state.relative_to(project).as_posix(),
           "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "since": since.isoformat(timespec="seconds") if since else None,
           "plugin_root": plugin_root, "schema_sha256": schema_sha,
           "scope_sha256": {}, "inject_rows": 0, "event_types": [], "event_rows": 0,
           "memory_sha256": None, "memory_chars": 0, "notes": []}
    if since is None:
        out["notes"].append("未给 --since：取的是全历史——有 Claude Code 历史的项目上 ③(2) 会假 FAIL")
    # ① inject-log：本门、窗口内、按 scope 取最后一条带 scope_sha256 的
    rows, bad, present = _read_jsonl(state / "inject-log.jsonl")
    if not present:
        out["notes"].append("inject-log.jsonl 不存在——该门在此项目尚未跑过注入 hook")
    if bad:
        out["notes"].append(f"inject-log.jsonl 有 {bad} 行解析不了，已跳过")
    rows, dropped = window(rows, since)
    if dropped:
        out["notes"].append(f"inject-log.jsonl 有 {dropped} 行无 ts 或 ts 解析不了，不进取证窗口")
    mine = [r for r in rows if r.get("harness") == door]
    out["inject_rows"] = len(mine)
    for scope in SCOPES:
        last = None
        for r in mine:
            if r.get("scope") == scope and isinstance(r.get("scope_sha256"), str):
                last = r
        out["scope_sha256"][scope] = last.get("scope_sha256") if last else None
    # ③ events.jsonl
    types, n, notes = read_events(state / "events.jsonl", door, since)
    out["event_types"], out["event_rows"] = types, n
    out["notes"] += notes
    # ② 记忆注入：直接调脚本两门形态，比 stdout（结构证据）
    sha, chars, note = memory_structural(door, project, memory_agent)
    out["memory_sha256"], out["memory_chars"] = sha, chars
    if note:
        out["notes"].append(note)
    return out


def memory_structural(door, project, agent_type):
    """`subagent-memory-inject.py --harness <door>` 喂固定载荷，返回 (additionalContext 的 sha256, 字符数, 备注)。
    环境里剥掉 `CLAUDE_PROJECT_DIR`，让项目根按载荷 `cwd`（Codex 形态）/ 根标记解析，两门走同一条取根路径。"""
    payload = {"agent_type": agent_type, "agent_id": "twin-parity-probe", "cwd": str(project),
               "session_id": "twin-parity-probe"}
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.pop("CLAUDE_PROJECT_DIR", None)
    try:
        r = subprocess.run([sys.executable, str(SCRIPTS / "subagent-memory-inject.py"), "--harness", door],
                           input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=str(project), env=env, timeout=60)
    except Exception as e:
        return None, 0, f"记忆注入脚本跑不起来: {e}"
    if r.returncode != 0:
        return None, 0, f"记忆注入脚本 exit={r.returncode}: {r.stderr.strip()[:200]}"
    text = r.stdout.strip()
    if not text:
        return None, 0, "记忆注入脚本零输出（该项目两份记忆都拿不出）——② 无对象"
    try:
        ctx = json.loads(text)["hookSpecificOutput"]["additionalContext"]
    except Exception:
        return None, 0, f"记忆注入脚本输出不是预期 JSON: {text[:120]!r}"
    return _sha(ctx), len(ctx), None


# ---- 比对 ----

def compare(cc, codex):
    """返回 (results 列表 [(项, 状态, 说明)], 数据源说明行列表)。状态 ∈ PASS / FAIL / 未能判定。"""
    res, src = [], []
    gap, both_none = gap_events()
    src.append(f"数据源：事件可得性按 .workframe-meta/event-schema.json 现算——codex=none 且 cc≠none 的缺口集 G = {gap}；"
               f"两门皆 none（不算跨门缺口）= {both_none}；模型半事件 {list(MODEL_HALF_EVENTS)} 不进确定性集 S")
    flags = _harness.event_schema().get("availability_values", {}).get("__delivery_established__", {})
    src.append(f"__delivery_established__ = { {d: flags.get(d) for d in DOORS} }（codex 为 false 时该门零模型半事件先怀疑投递）")
    # 取证窗口：任一门是全历史，③ 的读数就不是「本轮」的
    for label, d in (("cc", cc), ("codex", codex)):
        s = d.get("since")
        src.append(f"取证窗口 {label}: " + (f"since {s}" if s else
                   "全历史（未给 --since）——③ 比的是该门在此项目的历史累计，不是本轮；有历史的项目上 ③(2) 会假 FAIL"))
    # G 的 schema 来源：本仓；被采集项目那门若装的是别的版本，G 可能漂
    mine = own_schema_sha()
    for label, d in (("cc", cc), ("codex", codex)):
        theirs = d.get("schema_sha256")
        if theirs and mine and theirs != mine:
            src.append(f"注意：{label} 侧插件根（{d.get('plugin_root')}）的 event-schema.json 与本仓不同"
                       f"（{theirs[:12]} ≠ {mine[:12]}）——G 按本仓算，对那门可能不准")
    # ①
    for scope in SCOPES:
        a, b = cc.get("scope_sha256", {}).get(scope), codex.get("scope_sha256", {}).get(scope)
        if a is None or b is None:
            res.append((f"① scope_sha256[{scope}]", "未能判定",
                        f"cc={'有' if a else '缺'} / codex={'有' if b else '缺'}——缺的那门在此项目没跑过该 scope 的注入"))
        elif a == b:
            res.append((f"① scope_sha256[{scope}]", "PASS", a[:12]))
        else:
            res.append((f"① scope_sha256[{scope}]", "FAIL",
                        f"cc={a[:12]} / codex={b[:12]}——两门读到的 {scope} 片源文件不同（一门多读或少读了 context/{scope}/ 下的文件）"))
    # ②
    a, b = cc.get("memory_sha256"), codex.get("memory_sha256")
    if a is None or b is None:
        res.append(("② 记忆注入 sha256（结构）", "未能判定", "至少一门无输出，见各自 notes"))
    elif a == b:
        res.append(("② 记忆注入 sha256（结构）", "PASS", f"{a[:12]}（证的是脚本对两门产出相同，不是投递后到达相同）"))
    else:
        res.append(("② 记忆注入 sha256（结构）", "FAIL", f"cc={a[:12]} / codex={b[:12]}——同一载荷下脚本按门分支产出了不同正文"))
    # ③
    sc, sx = set(cc.get("event_types") or []), set(codex.get("event_types") or [])
    if not sc or not sx:
        res.append(("③ 事件集合", "未能判定",
                    f"cc {len(sc)} 类 / codex {len(sx)} 类——有一门零事件，说明该门在此项目没跑过会话，差集没有对象"))
    else:
        extra = sorted(sx - sc)
        res.append(("③(1) Codex − CC == ∅", "PASS" if not extra else "FAIL",
                    "" if not extra else f"Codex 多出 {extra}——CC 侧产不出的类型出现在 Codex，producer 不对等"))
        leak = sorted((sc - sx) - set(gap))
        res.append(("③(2) CC − Codex ⊆ G", "PASS" if not leak else "FAIL",
                    f"CC − Codex = {sorted(sc - sx)}" if not leak else
                    f"CC 有而 Codex 没有、且不在缺口集 G 内：{leak}——这些类型在 Codex 应当能产出，缺了就是该门下 producer 没跑"))
        for label, s in (("cc", sc), ("codex", sx)):
            miss = sorted(set(DETERMINISTIC_EVENTS) - s)
            res.append((f"③(3) S ⊆ {label}", "PASS" if not miss else "FAIL",
                        "" if not miss else f"{label} 缺确定性事件 {miss}——SessionEnd 链在该门没跑完或没写事件"))
        for ev in MODEL_HALF_EVENTS:
            res.append((f"ⓘ 模型半 {ev}", "INFO", f"cc={'有' if ev in sc else '无'} / codex={'有' if ev in sx else '无'}"))
    return res, src


def _exit_code(results):
    states = {s for _n, s, _m in results}
    if "FAIL" in states:
        return 1
    if "未能判定" in states:
        return 3
    return 0


def _print(results, src):
    for line in src:
        print("  " + line)
    for name, state, msg in results:
        mark = {"PASS": "✓", "FAIL": "✗", "INFO": "i"}.get(state, "?")
        print(f"  {mark} {name}: {state}" + (f" — {msg}" if msg else ""))


# ---- 自证 ----

def selftest():
    fails = 0

    def expect(label, results, want):
        nonlocal fails
        got = {n: s for n, s, _m in results if s != "INFO"}
        bad = {n: (got.get(n), w) for n, w in want.items() if got.get(n) != w}
        others = [n for n, s in got.items() if n not in want and s == "FAIL"]
        ok = not bad and not others
        print(f"  [{'PASS' if ok else '*** MISMATCH ***'}] {label}" + ("" if ok else f" bad={bad} extra_fail={others}"))
        if not ok:
            fails += 1

    base_cc = {"scope_sha256": {"main": "A" * 64, "sub": "B" * 64}, "memory_sha256": "M" * 64,
               "event_types": sorted(set(DETERMINISTIC_EVENTS) | {"skill_used", "memory_promoted", "config_changed", "skill_invoked"})}
    base_cx = {"scope_sha256": {"main": "A" * 64, "sub": "B" * 64}, "memory_sha256": "M" * 64,
               "event_types": sorted(set(DETERMINISTIC_EVENTS) | {"skill_used", "memory_promoted"})}
    all_pass = {"① scope_sha256[main]": "PASS", "① scope_sha256[sub]": "PASS", "② 记忆注入 sha256（结构）": "PASS",
                "③(1) Codex − CC == ∅": "PASS", "③(2) CC − Codex ⊆ G": "PASS", "③(3) S ⊆ cc": "PASS", "③(3) S ⊆ codex": "PASS"}
    print("[selftest] 构造样本——P0 基线与逐项单点突变")
    r, _ = compare(base_cc, base_cx)
    expect("P0 相等样本 → 全 PASS", r, all_pass)
    m = json.loads(json.dumps(base_cx)); m["scope_sha256"]["sub"] = "C" * 64
    r, _ = compare(base_cc, m)
    expect("M1 只改 codex 的 sub scope_sha256 → ①[sub] FAIL、其余 PASS", r, dict(all_pass, **{"① scope_sha256[sub]": "FAIL"}))
    m = json.loads(json.dumps(base_cx)); m["scope_sha256"]["main"] = None
    r, _ = compare(base_cc, m)
    expect("M2 codex 缺 main 记录 → ①[main] 未能判定", r, dict(all_pass, **{"① scope_sha256[main]": "未能判定"}))
    m = json.loads(json.dumps(base_cx)); m["memory_sha256"] = "N" * 64
    r, _ = compare(base_cc, m)
    expect("M3 记忆 sha 不同 → ② FAIL", r, dict(all_pass, **{"② 记忆注入 sha256（结构）": "FAIL"}))
    m = json.loads(json.dumps(base_cx)); m["event_types"] = sorted(set(m["event_types"]) | {"turn_failed"})
    r, _ = compare(base_cc, m)
    expect("M4 codex 多出 CC 没有的类型 → ③(1) FAIL", r, dict(all_pass, **{"③(1) Codex − CC == ∅": "FAIL"}))
    m = json.loads(json.dumps(base_cx)); m["event_types"] = sorted(set(m["event_types"]) - {"memory_promoted"})
    r, _ = compare(base_cc, m)
    expect("M5 codex 少一个非缺口、非 S 的类型 → ③(2) FAIL（只它一条）", r, dict(all_pass, **{"③(2) CC − Codex ⊆ G": "FAIL"}))
    c = json.loads(json.dumps(base_cc)); m = json.loads(json.dumps(base_cx))
    for d in (c, m):
        d["event_types"] = sorted(set(d["event_types"]) - {"session_ended"})
    r, _ = compare(c, m)
    expect("M6 两门都缺 session_ended → 只 ③(3) 两条 FAIL（①②③(1)(2) 不动）", r,
           dict(all_pass, **{"③(3) S ⊆ cc": "FAIL", "③(3) S ⊆ codex": "FAIL"}))
    m = json.loads(json.dumps(base_cx)); m["event_types"] = []
    r, _ = compare(base_cc, m)
    expect("M7 codex 零事件 → ③ 整项未能判定、①② 不动", r,
           {"① scope_sha256[main]": "PASS", "① scope_sha256[sub]": "PASS", "② 记忆注入 sha256（结构）": "PASS", "③ 事件集合": "未能判定"})
    # 写坏反证：把 ③(2) 的缺口集换成「codex=none 全集」（含 rule_triggered）——M5 仍红、但真实 CC 集合
    # 若含 rule_triggered 会被吞；这里只证 G 确实剔除了两门皆 none 的那项
    gap, both = gap_events()
    print(f"  [{'PASS' if 'rule_triggered' in both and 'rule_triggered' not in gap else '*** MISMATCH ***'}] "
          f"G 现算剔除两门皆 none 的 {both}；G = {gap}")
    if not ("rule_triggered" in both and "rule_triggered" not in gap):
        fails += 1

    def check(label, cond, detail=""):
        nonlocal fails
        print(f"  [{'PASS' if cond else '*** MISMATCH ***'}] {label}" + (f" {detail}" if detail and not cond else ""))
        if not cond:
            fails += 1

    # 取证窗口：一份带 Claude Code 历史噪声的 events 样本——按 --since 取本轮后 ③(2) 绿、按全历史红
    import tempfile
    print("[selftest] 取证窗口——历史噪声样本")
    noise = ["memory_promoted", "task_blocked", "self_signoff", "stale_cleared", "user_correction"]
    this_round = list(DETERMINISTIC_EVENTS) + ["skill_used"]
    since = parse_ts("2026-09-13T09:00:00+00:00")
    with tempfile.TemporaryDirectory() as td:
        ev = Path(td) / "events.jsonl"
        lines = ['{"__schema__": "workframe.events.v2"}']                       # 头行：无 ts、无 type
        lines += [json.dumps({"ts": "2026-01-01T00:00:00+00:00", "type": t}) for t in noise]   # 历史、无 harness → cc
        lines += [json.dumps({"ts": "2026-09-13T10:00:00+00:00", "type": t, "harness": "cc"}) for t in this_round]
        lines += [json.dumps({"ts": "2026-09-13T10:05:00+00:00", "type": t, "harness": "codex"}) for t in this_round]
        ev.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
        cc_win, n_win, notes_win = read_events(ev, "cc", since)
        cc_all, n_all, _ = read_events(ev, "cc", None)
        cx_win, _n, _ = read_events(ev, "codex", since)
        check("W1 按 --since 取 cc：只剩本轮类型", cc_win == sorted(this_round), f"got={cc_win}")
        check("W1b 全历史取 cc：历史噪声混入", set(noise) <= set(cc_all), f"got={cc_all}")
        check("W1c 窗口把无 ts 的头行筛掉并记 notes", n_win == len(this_round) and any("无 ts" in x for x in notes_win),
              f"n={n_win} notes={notes_win}")
        check("W1d 全历史把头行也算进 cc 行数（无 harness 即 cc）", n_all == 1 + len(noise) + len(this_round), f"n={n_all}")
        base = {"scope_sha256": {"main": "A" * 64, "sub": "B" * 64}, "memory_sha256": "M" * 64}
        r, src = compare(dict(base, event_types=cc_win, since=since.isoformat()), dict(base, event_types=cx_win, since=since.isoformat()))
        expect("W1e 本轮窗口 → 全 PASS", r, all_pass)
        check("W1f 两门都给了 since：数据源行不报全历史", not any("全历史" in s for s in src))
        r, src = compare(dict(base, event_types=cc_all, since=None), dict(base, event_types=cx_win, since=None))
        expect("W1g 全历史 → 只 ③(2) FAIL（历史噪声不在 G）", r, dict(all_pass, **{"③(2) CC − Codex ⊆ G": "FAIL"}))
        check("W1h 未给 since：数据源行点名全历史", sum("全历史" in s for s in src) == 2)
        # ts 解析：Z 后缀、无时区按 UTC、坏值
        check("W1i parse_ts: Z 后缀 == +00:00", parse_ts("2026-09-13T09:00:00Z") == since)
        check("W1j parse_ts: 无时区按 UTC", parse_ts("2026-09-13T09:00:00") == since)
        check("W1k parse_ts: 坏值 → None", parse_ts("昨天") is None and parse_ts(None) is None)
    # --collect 对读不到的输入 exit 2，不写空 JSON
    print("[selftest] --collect 输入读不到")
    with tempfile.TemporaryDirectory() as td:
        missing = str(Path(td) / "no-such-project")
        out = str(Path(td) / "should-not-exist.json")
        check("W2 --project 不存在 → exit 2", main(["--collect", "--door", "cc", "--project", missing, "--out", out]) == 2)
        try:
            collect("cc", missing)
            why = "没抛"
        except ProbeInputError as e:
            why = str(e)
        check("W2a 拒绝理由点名的是项目目录，不是运行态目录", "不是目录" in why, f"why={why}")
        check("W2b 且不写 --out", not Path(out).exists())
        check("W2c 目录存在但无运行态目录 → exit 2", main(["--collect", "--door", "cc", "--project", td, "--out", out]) == 2)
        _state_io.state_dir_of(td).mkdir(parents=True)          # 有运行态目录了，下一格只剩 --since 一个拒绝理由
        check("W2d --since 解析不了 → exit 2", main(["--collect", "--door", "cc", "--project", td, "--since", "昨天"]) == 2)
    # G 的 schema 来源提示
    mine = own_schema_sha()
    if mine:
        same = dict(base, event_types=cc_win, since="x", schema_sha256=mine, plugin_root="/r")
        diff = dict(same, schema_sha256="f" * 64)
        _r, src = compare(same, diff)
        check("W3 采集侧 schema 与本仓不同 → 数据源行提示", any("与本仓不同" in s for s in src))
        _r, src = compare(same, same)
        check("W3b 相同 → 不提示", not any("与本仓不同" in s for s in src))
    else:
        check("W3 本仓 event-schema.json 读不到——G 现算依赖它", False)
    print(f"[selftest] {'all passed' if not fails else f'{fails} mismatch'}")
    return 0 if not fails else 1


RECIPE = """两门固定脚本（带凭据；本节未在零凭据下实测，按已知命令形态写）
========================================================
前提：同一个已装两门的项目 <P>；两门插件根版本相等（doctor 绿）。
取证面必须是「本轮」：③ 比的是事件类型**集合**，一个用过 Claude Code 一段时间的项目，CC 侧历史里的类型远多于
  一次会话能产出的（skill_used / task_blocked / self_signoff / memory_promoted …），按全历史比 ③(2) 必 FAIL。
  两条路任选其一（都做也行）：
  (a) 开跑前记下 UTC 时刻 T0（如 `date -u +%Y-%m-%dT%H:%M:%S+00:00`），采集时带 `--since T0`；
  (b) 用一个**全新的 fixture 项目**（两门装完、零会话），每门**恰跑一次**会话。
每门各跑一次会话，提示词相同（一段就够）：
  「1) 派一个 dev 子 agent 回答 1+1；2) 调用 core:task-management 读一遍看板并按收尾协议写 skill_used 事件；
    3) 我纠正你一次：时间戳一律现取——请按纠正流程写一条 user_correction 事件；4) 结束会话。」
  Claude Code：  cd <P> && claude -p --plugin-dir <本仓>/plugins/core "<提示词>"
  Codex：        cd <P> && codex exec "<提示词>"
跑完各采集一份（`--since` 两门用同一个 T0；走 (b) 时可省，JSON 里会记「全历史」）：
  python tools/twin_parity_probe.py --collect --door cc    --project <P> --since T0 --out cc.json
  python tools/twin_parity_probe.py --collect --door codex --project <P> --since T0 --out codex.json
  python tools/twin_parity_probe.py --compare cc.json codex.json
② 的真实到达面（脚本比的是结构，这一步比投递后的到达）：
  CC：   <用户主目录>/.claude/projects/<项目键>/<会话>/subagents/*.jsonl，找 SubagentStart 的 hook 附件里
         以「[workframe] 角色记忆注入」起头的那段，sha256 与 cc.json 的 memory_sha256 比
  Codex：<CODEX_HOME>/sessions/<日期>/rollout-*.jsonl，子线程里 content_item_kinds 含
         hooks.additional_context 的项，取其正文同法比
失败方式打一遍：往 plugins/core/context/sub/ 加一个只让一门读到的文件（例如只在一门的插件根副本里加），
  重跑两门 → ①[sub] 必 FAIL。
"""


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="两门动态对等探针（见文件头）")
    ap.add_argument("--recipe", action="store_true")
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--door", choices=DOORS)
    ap.add_argument("--project")
    ap.add_argument("--out")
    ap.add_argument("--memory-agent", default=MEMORY_PAYLOAD_AGENT)
    ap.add_argument("--since", metavar="ISO-8601", help="取证窗口起点（UTC；无时区按 UTC）。不给 = 全历史，见文件头")
    ap.add_argument("--compare", nargs=2, metavar=("CC_JSON", "CODEX_JSON"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.recipe:
        print(RECIPE)
        return 0
    if a.selftest:
        return selftest()
    if a.collect:
        if not (a.door and a.project):
            ap.error("--collect 需要 --door 与 --project")
        since = None
        if a.since:
            since = parse_ts(a.since)
            if since is None:
                print(f"--since 解析不了（要 ISO-8601，如 2026-09-13T09:00:00+00:00）: {a.since!r}")
                return 2
        try:
            data = collect(a.door, a.project, a.memory_agent, since)
        except ProbeInputError as e:
            print(f"输入读不到: {e}")
            return 2
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        if a.out:
            Path(a.out).write_bytes(text.encode("utf-8"))
            print(f"written {a.out}: scope_sha256={ {k: (v or '')[:12] for k, v in data['scope_sha256'].items()} } "
                  f"events={len(data['event_types'])} memory={(data['memory_sha256'] or '')[:12] or '-'}")
        else:
            print(text)
        for n in data["notes"]:
            print("  note:", n)
        return 0
    if a.compare:
        try:
            cc = json.loads(Path(a.compare[0]).read_text(encoding="utf-8"))
            cx = json.loads(Path(a.compare[1]).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"输入读不到: {e}")
            return 2
        if cc.get("door") not in (None, "cc") or cx.get("door") not in (None, "codex"):
            print(f"两份文件的 door 字段与位置不符：第一份应为 cc（{cc.get('door')!r}），第二份应为 codex（{cx.get('door')!r}）")
            return 2
        results, src = compare(cc, cx)
        _print(results, src)
        code = _exit_code(results)
        print(f"twin-parity: {'all PASS' if code == 0 else 'FAIL' if code == 1 else '有未能判定项'} (exit {code})")
        return code
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
