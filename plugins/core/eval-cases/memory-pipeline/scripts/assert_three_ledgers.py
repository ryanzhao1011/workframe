#!/usr/bin/env python3
"""三账对齐核验：sidecar(memory-index.json) ↔ MEMORY.md ↔ events.jsonl。

G7 全链路终验的收口断言脚本（J10），也可在任意时点做中途对账。
下列各项核对，输出违例清单，exit code = 违例数（0 = 三账对齐）：

  A. MEMORY 中每个「从 notes 提升」标注 → 按条目的 key 配一条 memory_promoted 事件
     （--since 之前的标注降级为 info，不计违例；判法见 `check_marks`）
  A'. 每个「从 X 迁移」标注 → 同法配一条 memory_migrated 事件
  B. MEMORY 中 [纠正] 条目 → 必须有同 scope 同日期的 protected sidecar entry
  C. sidecar entry → 对应 scope 的 MEMORY.md 必须存在同日期条目（防幽灵索引）
  D. 带 entry_key 的 user_correction / memory_promoted / memory_migrated 事件 → sidecar 必须有该 key；
     同来源 scope、同原 key、时序在后的完整 memory_moved_out / memory_decayed，或 from_entry_key
     指向它的更晚 memory_migrated，是合法去向证据

事件读主文件 ＋ 未并回的 spill（锁超时时落的 `events.<pid>.spill.jsonl`，下次会话启动才并回）。

用法：
  python assert_three_ledgers.py --project <项目根> [--since YYYY-MM-DD]
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# 冒号全角半角都认：skill `signal-intake` 与必载注入片（notes 格式硬要求）示范的条目格式是半角
# `- YYYY-MM-DD:`，而 correction-detection 的写入模板用全角 `：`。只认一种会让
# 另一种写法的条目**整条游离在三账本校验之外**（静默漏检，不是误报）。
ENTRY_RE = re.compile(r"^- (?:\[纠正\] )?(\d{4}-\d{2}-\d{2})[:：]")
# `notes` 与 `提升` 之间不许跨出本对括号：写成 `.*` 时，一行里两个标注会被吞成一次匹配，后一个没人核
PROMOTED_MARK_RE = re.compile(r"（(\d{4}-\d{2}-\d{2})[^）]*(?:从 notes 提升|notes[^）]*提升|融合提升)[^）]*）")
# 跨 scope 搬迁的出处标注（main-led 记忆分流改造 批次 5 起产生）。
# 单列而不并进 PROMOTED_MARK_RE：两者要求的事件类型不同（提升要 memory_promoted、
# 搬迁要 memory_migrated），合并后无法分辨该找哪种事件。
# 不加这条正则的后果不是误报而是**漏检**——搬迁条目不匹配任何标注正则，check A 直接
# 跳过，等于整批迁移条目游离在账本校验之外。
# 「从 X 迁移」中的 X 故意不限定：跨域搬迁的来源可能是 auto-memory，也可能是另一个角色域
# （改域）。收紧成只认 auto-memory 会让改域类搬迁重新漏检——那是更坏的失败方向。
# 代价是「从 notes 迁移」这种措辞错误也会被要求配 memory_migrated 事件；规格上「迁移」
# 字样只用于跨 scope（同 scope 的 notes → MEMORY 叫提升），写错字样本就该被挡下。
MIGRATED_MARK_RE = re.compile(r"（(\d{4}-\d{2}-\d{2})[^）]*(?:从 [^）]*迁移|迁移自)[^）]*）")
# 碰撞后缀从 -2 起分配（`maintenance_workorder._unique_key`），`-1` 恒是正文本身
KEY_SUFFIX_RE = re.compile(r"^(.*)-(?:[2-9]|[1-9]\d+)$")
# 不带 entry_key 的提升 / 搬迁事件只有早于这一天的才可能是「旧 producer 漏写 key」：框架仓最早可考的提交
# （v1.0.0，2026-08-18）的 event-schema 已为两类事件列出 entry_key，更早的规格不在本仓历史里。
# 此后不带 key 的 memory_promoted 是规格内的非 MEMORY 落点（skill / rule / 模块记忆），不能替 MEMORY 条目作证——
# 不靠 summary 里有没有路径来认落点：那是自由文本，写法一变就漏判
ENTRY_KEY_SPEC_SINCE = "2026-08-18"
# 日期级证据的窗口：标注按本地日期写、事件 ts 是 UTC，`maintenance --commit` 也可能隔天才跑
DATE_WINDOW_DAYS = 1


def key_fragment(text):
    """entry_key 第三段的取法：先删全部空白、再取前 20 字。

    与 `maintenance_workorder._normalize_key_fragment` 同一规则（`workframe_event` 也复用那份）。不 import 它：
    那个模块在 import 期重绑 stdout 并按 cwd 解析项目根，而本脚本跑在 `--project` 指定的任意项目上。
    两边一致性由单测钉住。"""
    return "".join(text.split())[:20]


def line_fragments(ln, body):
    """一行可能被取成 key 第三段的三种文本：日期后正文 / 去掉 `[纠正]` 的整行 / 整行。

    历史上三个 producer 取法不一（有的连行首日期一起取），从行文本还原 key 只能三种都试。"""
    whole = ln[2:]
    return {key_fragment(body), key_fragment(re.sub(r"^\[纠正\]\s*", "", whole)), key_fragment(whole)}


def fragment_matches(frag, cand):
    """key 片段与候选文本片段是否同一条。

    片段不足 20 字说明当初的摘要短于 20 字，候选（行文本的前 20 字）只能比前缀。前缀本身有歧义——`安装` 可能是整条
    正文，也可能是 `安装（2号方案）`、`安装器` 的开头，从行文本判不出来（按「前缀后跟什么算边界」收紧试过两版，
    要么漏掉正文里的括号，要么丢掉「摘要是正文前半句」的合法 key）。歧义由 `resolve_groups` 的最长匹配优先消解。"""
    if not frag:
        return False
    return frag == cand or (len(frag) < 20 and cand.startswith(frag))


def load_memory_entries(memory_dir):
    """返回 [{scope, date, is_correction, promoted_date, promoted_dates, migrated_date, migrated_dates,
    fragments, text}]。

    `promoted_date` 是第一个提升标注（C 用它对 sidecar 的 created_at——写进已有条目时原标注在前、沿用原 key），
    `promoted_dates` 是全部提升标注（A 逐个要事件），`migrated_dates` 同理（A' 逐个要事件）。
    `migrations_after` 与 `promoted_dates` 一一对应：该提升标注**之后**还写着几个搬迁标注（标注按操作先后追加，
    看行内位置不看日期——同一天先迁入再提升时，日期分不出先后）。它决定这次提升的事件带的是往回第几跳的 key。"""
    out = []
    for mem in sorted(memory_dir.glob("*/MEMORY.md")):
        scope = mem.parent.name
        for ln in mem.read_text(encoding="utf-8", errors="replace").splitlines():
            m = ENTRY_RE.match(ln)
            if not m:
                continue
            # 写进已有条目（并进 / 归纳）会在原标注后再追加一个提升标注：每个都要有事件，只取第一个会漏检后加的
            pm_hits = list(PROMOTED_MARK_RE.finditer(ln))
            mg_hits = list(MIGRATED_MARK_RE.finditer(ln))
            pms = [m_.group(1) for m_ in pm_hits]
            mgs = [m_.group(1) for m_ in mg_hits]
            out.append({
                "migrations_after": [sum(g_.start() > m_.start() for g_ in mg_hits) for m_ in pm_hits],
                "scope": scope,
                "date": m.group(1),
                "is_correction": ln.startswith("- [纠正]"),
                "promoted_date": pms[0] if pms else None,
                "promoted_dates": pms,
                "migrated_date": mgs[0] if mgs else None,
                "migrated_dates": mgs,
                "fragments": line_fragments(ln, ln[m.end():].strip()),
                "text": ln[:80],
            })
    return out


def family_base(key, live_keys):
    """碰撞族的基 key：`-N` 后缀只在剥掉后的 key 也在 sidecar 里时才算后缀。

    正文本身也可能以 `-数字` 结尾（`…TASK-1` 与它的碰撞后缀 `…TASK-1-2` 同形），按「末尾 -\\d+ 即后缀」剥会错。"""
    m = KEY_SUFFIX_RE.match(key)
    return m.group(1) if m and m.group(1) in live_keys else key


def resolve_groups(mem_entries, live_keys):
    """把 MEMORY 行解析到 sidecar 的碰撞族，返回 (groups, unresolved_lines)。

    行 → 族：同 scope，key 日期 ∈ {行首日期, 各提升 / 搬迁标注日期}，族基 key 的第三段与行的三种片段之一
    对上（`fragment_matches`）。一行对上几个族时**只留片段最长的那些**：`安装` 与 `安装（2号方案）` 两个 key 都在时，
    后一行只归后者，前一行只归前者（长 key 不是前一行的前缀）。剩下的仍对上几族、或几行认领同一族时并成一组——
    族内后缀按写入顺序分配，从行文本分不出哪行是哪个后缀，只能按组计数。

    已知限制：长条目自己的 key 不在 sidecar 时，这行会被它前缀上的短 key 认领、与短 key 那行并组；
    那时长条目的事件 key 悬空，由 D 报出。"""
    families = {}
    for k in live_keys:
        families.setdefault(family_base(k, live_keys), set()).add(k)
    parsed = {}
    for base in families:
        parts = base.split(":", 2)
        if len(parts) == 3:
            parsed[base] = parts
    parent = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    unresolved = []
    for i, me in enumerate(mem_entries):
        dates = {me["date"], *me["promoted_dates"], *me["migrated_dates"]}
        hits = [b for b, (sc, d, frag) in parsed.items()
                if sc == me["scope"] and d in dates and any(fragment_matches(frag, c) for c in me["fragments"])]
        if not hits:
            unresolved.append(me)
            continue
        longest = max(len(parsed[b][2]) for b in hits)
        hits = [b for b in hits if len(parsed[b][2]) == longest]
        for b in hits:
            parent[find(("line", i))] = find(("fam", b))
            find(("line", i))
    groups = {}
    for node in list(parent):
        root = find(node)
        g = groups.setdefault(root, {"lines": [], "keys": set()})
        if node[0] == "line":
            g["lines"].append(mem_entries[node[1]])
        else:
            g["keys"] |= families[node[1]]
    return [g for g in groups.values() if g["lines"]], unresolved


def _date_of(ev):
    ts = ev.get("ts")
    return ts[:10] if isinstance(ts, str) and DATE_RE.fullmatch(ts[:10] or "") else None


def _within(day, other):
    try:
        return abs((datetime.fromisoformat(day) - datetime.fromisoformat(other)).days) <= DATE_WINDOW_DAYS
    except (TypeError, ValueError):
        return False


def _max_matching(slots, pool):
    """slot ↔ 事件的最大一对一配对（增广路）。slot = (scope, 候选日期集合)；返回配上的 slot 下标集合。"""
    owner = {}

    def fits(slot, ev):
        scope, days = slot
        ev_scope = ev["entry_key"].split(":", 1)[0] if ev.get("entry_key") else ev.get("scope")
        d = _date_of(ev)
        return ev_scope == scope and d is not None and any(_within(d, x) for x in days)

    def augment(i, seen):
        for j, ev in enumerate(pool):
            if j in seen or not fits(slots[i], ev):
                continue
            seen.add(j)
            if j not in owner or augment(owner[j], seen):
                owner[j] = i
                return True
        return False

    for i in range(len(slots)):
        augment(i, set())
    return set(owner.values())


def trace_source_keys(keys, migrated_events):
    """沿 memory_migrated 的 `from_entry_key` 往回追：搬迁前在源域打的提升标注，事件带的是源域 key。"""
    out, frontier = set(keys), set(keys)
    while frontier:
        nxt = {ev["from_entry_key"] for ev in migrated_events
               if ev.get("entry_key") in frontier and isinstance(ev.get("from_entry_key"), str)
               and ev["from_entry_key"] and ev["from_entry_key"] not in out}
        out |= nxt
        frontier = nxt
    return out


def hops_traceable(keys, hops, migrated_events):
    """从现 key 沿 from_entry_key 往回走 `hops` 跳，每一跳都追得下去吗。"""
    frontier = set(keys)
    for _ in range(hops):
        frontier = {ev["from_entry_key"] for ev in migrated_events
                    if ev.get("entry_key") in frontier and isinstance(ev.get("from_entry_key"), str)
                    and ev["from_entry_key"]}
        if not frontier:
            return False
    return True


def summary_matches_key(summary, key):
    """事件摘要与 key 第三段是否同一条：摘要（及去掉行首日期后）的片段与 key 片段按 `fragment_matches` 对上。"""
    parts = key.split(":", 2) if isinstance(key, str) else []
    if len(parts) != 3 or not isinstance(summary, str) or not summary.strip():
        return False
    m = KEY_SUFFIX_RE.match(parts[2])
    frags = {parts[2]} | ({m.group(1)} if m else set())
    body = re.sub(r"^\s*(?:\[纠正\]\s*)?\d{4}-\d{2}-\d{2}[:：]\s*", "", summary)
    cands = {key_fragment(summary), key_fragment(body)}
    return any(fragment_matches(f, c) for f in frags for c in cands)


def check_marks(label, kind, mark_field, groups, unresolved, events, migrated_events, since):
    """A / A' 共用的判法，返回 (violations, infos)。

    1. **组内按 key 计数，不看日期**：组内各行的标注数 ≤ 事件 entry_key 落在组内各族（含沿 from_entry_key
       追到的源 key）的 `kind` 事件数 ⇒ 对上。不看日期，所以本地日期配 UTC 次日、隔天才跑的 `--commit` 都不误报；
       事件按 key 认领，所以同域同日别的条目的事件替不了它
    2. **不足的部分、以及解析不到族的行，只认日期级证据**：同 scope、日期差 ≤ DATE_WINDOW_DAYS、一对一整体最优配对，
       证据池 = entry_key 不属于任何已解析组的 `kind` 事件 ＋ ENTRY_KEY_SPEC_SINCE 之前不带 key 的旧事件。
       配上报 info「未解析」（不算按条目对上，只是有同期证据）；配不上报违例
    3. 写在搬迁标注之前的提升标注：它后面有 n 个搬迁标注，它的事件带的就是从现 key 往回第 n 跳的 key。
       往回这 n 跳里有一跳追不下去（那次搬迁缺 from_entry_key）时，缺口先记到它头上，报 info「未解析」。
       只看它自己要走的那几跳：更早一跳从 auto-memory 迁入、合法省略 from_entry_key，不影响之后的提升
    4. `--since`：组内标注分新旧两档（标注日期 < since 为旧）。按 key 计数不看日期，但事件日期早于
       since − DATE_WINDOW_DAYS 的不可能是新标注的事件——新档缺口 = 新标注数 − 新期事件数，旧档缺口用剩下的事件抵，
       旧档缺口降 info，与逐标注判时的契约一致

    已知限制：组内恰好一个标注漏记、另一个多记一条时，总数仍相等，判不出来。"""
    violations, infos = [], []
    claimed = set()
    for g in groups:
        g["traced"] = trace_source_keys(g["keys"], migrated_events)
        claimed |= g["traced"]
    slots = []  # (scope, 候选日期, 报告用文本, 是否早于 --since)
    cutoff = None
    if since:
        try:
            cutoff = (datetime.fromisoformat(since) - timedelta(days=DATE_WINDOW_DAYS)).date().isoformat()
        except ValueError:
            cutoff = since

    for g in groups:
        marks = [(me, d, me["migrations_after"][n] if mark_field == "promoted_dates" else 0)
                 for me in g["lines"] for n, d in enumerate(me[mark_field])]
        if not marks:
            continue
        group_events = [ev for ev in events if ev.get("entry_key") in g["traced"]]
        have = len(group_events)
        if len(marks) - have <= 0:
            continue
        old = [m for m in marks if since and m[1] < since]
        cur = [m for m in marks if not (since and m[1] < since)]
        cur_ev = sum(1 for ev in group_events if not cutoff or (_date_of(ev) or "") >= cutoff)
        short = {"cur": max(0, len(cur) - cur_ev),
                 "old": max(0, len(old) - (have - cur_ev) - max(0, cur_ev - len(cur)))}
        # 搬迁前的提升标注要走的那几跳里有一跳追不下去时，它按条目核不了：先把缺口记到它头上
        for bucket, pool_marks in (("cur", cur), ("old", old)):
            for me, d, hops in pool_marks:
                if hops and short[bucket] > 0 and not hops_traceable(g["keys"], hops, migrated_events):
                    short[bucket] -= 1
                    infos.append(f"{label}: 未解析 {me['scope']} 搬迁前的提升标注({d})：往回 {hops} 跳的搬迁里有一跳缺 "
                                 f"from_entry_key，追不到当时那个域的 key | {me['text']}")
        texts = " / ".join(sorted({me["text"] for me, _, _ in marks}))
        for bucket, pool_marks, early in (("cur", cur, False), ("old", old, True)):
            days = sorted({d for _, d, _ in pool_marks})
            for _ in range(short[bucket]):
                slots.append(("group", g["lines"][0]["scope"], days,
                              f"组内标注 {len(marks)} 个、按 key 认领的 {kind} 事件 {have} 条 | {texts}", early))
    for me in unresolved:
        for d in me[mark_field]:
            slots.append(("line", me["scope"], [d], f"行解析不到 sidecar key | {me['text']}",
                          bool(since and d < since)))

    pool = [ev for ev in events
            if (ev.get("entry_key") and ev["entry_key"] not in claimed)
            or (not ev.get("entry_key") and (_date_of(ev) or "9999") < ENTRY_KEY_SPEC_SINCE)]
    matched = _max_matching([(s[1], s[2]) for s in slots], pool)
    for i, (_, scope, days, text, early) in enumerate(slots):
        when = "/".join(days)
        if i in matched:
            infos.append(f"{label}: 未解析 {scope} 标注({when})只有同期日期级证据，未按条目对上 | {text}")
            continue
        msg = f"{label}: {scope} {'提升' if kind == 'memory_promoted' else '搬迁'}条目({when})无 {kind} 事件 | {text}"
        (infos if early else violations).append(msg + ("（早于 --since，仅提示）" if early else ""))
    return violations, infos


def read_events(events_file, spill_suffix, attempts=2):
    """主事件文件 ＋ 未并回的 spill，返回 (events, bad_lines, read_errors)。

    spill 不读的话，锁超时那一刻写下的事件在下次会话启动并回之前都「不存在」，A 会误报缺事件。
    spill 行排在主文件之后：D 的先后按 (ts, 行序) 判，同秒时它们算更晚写下的。
    读不了的事件文件不能当空文件：少了事件，A 顶多多报，D 却会因为「没有带 key 的事件」整片放行。
    主文件不存在是合法状态（项目刚装，或第一行就因锁超时落进了 spill）；**已经列出的 spill 读时消失**则是
    别的会话正在并回（先改名、再追加到主文件）——这一轮读到的不完整，整体重读一次，仍消失就报读不了。"""
    for attempt in range(attempts):
        events, bad_lines, read_errors, vanished = [], 0, [], False
        for f in [events_file] + sorted(events_file.parent.glob(f"{events_file.stem}.*{spill_suffix}")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError as e:
                if f == events_file:
                    continue
                vanished = True
                read_errors.append(f"E: spill 读时消失（疑似正被并回），三账无法判定：{f.name}（{e}）")
                continue
            except OSError as e:
                read_errors.append(f"E: 事件文件读不了，三账无法判定：{f.name}（{type(e).__name__}: {e}）")
                continue
            for ln in text.splitlines():
                if not ln.strip():
                    continue
                try:
                    ev = json.loads(ln)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                if isinstance(ev, dict):
                    events.append(ev)
                else:
                    bad_lines += 1
        if not vanished:
            break
    return events, bad_lines, read_errors


def event_position(event, row):
    """有效时戳加追加行序；同秒迁出以行序判先后，缺失/坏时戳不能成为迁出证据。"""
    try:
        ts = datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))
        if ts.tzinfo is not None:
            return ts.astimezone(timezone.utc), row
    except (KeyError, TypeError, ValueError, AttributeError):
        pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--since", default=None,
                    help="只对该日期（含）之后的提升条目严格要求事件；更早的降级 info")
    args = ap.parse_args()

    root = Path(args.project)
    # 运行态两个目录的位置只由 core 插件的 `_state_io` 决定——eval 跑在真实项目上，
    # 自己拼路径就是第二个源，挪目录那天这里漏改、读的是一个没人写的目录
    # __file__ = <core>/eval-cases/memory-pipeline/scripts/…  → parents[3] 即 <core>
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from _state_io import SPILL_SUFFIX, memory_dir_of, state_dir_of
    state = state_dir_of(root)
    memory_dir = memory_dir_of(root)

    sidecar = json.loads((state / "memory-index.json").read_text(encoding="utf-8"))
    entries = sidecar.get("entries", {})

    events, bad_lines, read_errors = read_events(state / "events.jsonl", SPILL_SUFFIX)
    promoted_events = [e for e in events if e.get("type") == "memory_promoted"]
    migrated_events = [e for e in events if e.get("type") == "memory_migrated"]
    keyed_events = [(row, e) for row, e in enumerate(events)
                    if e.get("type") in ("memory_promoted", "user_correction", "memory_migrated")
                    and e.get("entry_key")]
    moved_events = [(row, e) for row, e in enumerate(events) if e.get("type") == "memory_moved_out"]
    decayed_events = [(row, e) for row, e in enumerate(events) if e.get("type") == "memory_decayed"]

    mem_entries = load_memory_entries(memory_dir)
    violations, infos = list(read_errors), []

    # A. 提升标注 → memory_promoted 事件；A'. 搬迁标注 → memory_migrated 事件。判法见 check_marks。
    #    MEMORY 行不带 key，只能从行文本反推它在 sidecar 里的 key（碰撞族按组计数）。两种事件不可互换：
    #    用 memory_promoted 顶替搬迁会让消费方误以为该域刚消化过 notes 积压
    groups, unresolved = resolve_groups(mem_entries, set(entries))
    for label, kind, field, evs in (("A", "memory_promoted", "promoted_dates", promoted_events),
                                    ("A'", "memory_migrated", "migrated_dates", migrated_events)):
        v, i = check_marks(label, kind, field, [dict(g) for g in groups], unresolved, evs,
                           migrated_events, args.since)
        violations += v
        infos += i

    # B. [纠正] 条目 → protected sidecar
    for me in mem_entries:
        if not me["is_correction"]:
            continue
        hit = any(v.get("scope") == me["scope"] and v.get("created_at") == me["date"]
                  and v.get("protected") for v in entries.values())
        if not hit:
            violations.append(f"B: {me['scope']} [纠正]({me['date']})无 protected sidecar | {me['text']}")

    # C. sidecar → MEMORY 存在对应条目
    # sidecar created_at 是「提升日」；MEMORY 行首日期是「经验发生日」，提升日在
    # 行尾「（YYYY-MM-DD 从 notes 提升）」注记里——两个日期都参与匹配
    mem_index = {(me["scope"], me["date"]) for me in mem_entries}
    mem_index |= {(me["scope"], me["promoted_date"]) for me in mem_entries if me["promoted_date"]}
    # 搬迁条目的行首日期是**经验发生日**、sidecar created_at 可能是**迁移日**，两者天然不同。
    # 规格上 created_at 应承接原创建日（衰减时钟不该因搬家重置），但源条目未必有可考的
    # 创建日——auto-memory 的 frontmatter 只有 modified。拿不到时退用迁移日是允许的，
    # 于是这里必须同时认它，否则合规的迁移条目会被判成幽灵索引（沙盒实测确认）。
    mem_index |= {(me["scope"], me["migrated_date"]) for me in mem_entries if me["migrated_date"]}
    for key, v in entries.items():
        if (v.get("scope"), v.get("created_at")) not in mem_index:
            violations.append(f"C: sidecar 幽灵条目 {key}（{v.get('scope')}/{v.get('created_at')} 在 MEMORY 无对应）")

    # D. 带 key 的事件 → sidecar 有该 key。
    # supersede 例外：旧条目被纠正取代时 sidecar entry 删除但历史事件保留（append-only），
    # 悬空 key 若存在同 scope 更晚的 user_correction（取代证据）→ 降 info；否则违例
    corrections = [e for e in events if e.get("type") == "user_correction"]
    migration_rows = [(row, e) for row, e in enumerate(events) if e.get("type") == "memory_migrated"]
    for row, ev in keyed_events:
        if ev["entry_key"] in entries:
            continue
        # skill:/rule: 前缀 key 是 F5 规格定稿前的历史产物（现规格：skill/rule 落点
        # 不写 sidecar、事件省略 entry_key）——events append-only 不回改，降 info
        if ev["entry_key"].split(":", 1)[0] in ("skill", "rule"):
            infos.append(f"D: 历史 skill/rule 落点 key（规格前产物，仅提示）：{ev['entry_key']}"
                         f"（{ev.get('type')}@{ev.get('ts','')[:19]}）")
            continue
        # 取代证据按 entry_key 首段（落点命名空间）比较，不用事件 scope 字段——
        # 纠正落 skill 时事件 scope 记来源角色，粗比 scope 会把 skill 落点悬空误豁免
        ns = ev["entry_key"].split(":", 1)[0]
        position = event_position(ev, row)
        # 迁出只解释这一条原 key，不能像 supersede 启发式那样按同域任意事件放宽。
        # 原文/目标的存在性随之后的文档演进而变；这里只核事件保留了可追溯的完整字段。
        moved_out = any(
            m.get("entry_key") == ev["entry_key"]
            and m.get("scope") == (ev.get("scope") or ev.get("role") or ns) == ns
            and all(isinstance(m.get(f), str) and m[f].strip()
                    for f in ("archive_path", "target_path", "summary"))
            and position is not None and event_position(m, mr) is not None
            and event_position(m, mr) > position
            for mr, m in moved_events)
        # 降级与同族归纳都在删 sidecar entry 前记 memory_decayed（librarian 第 3 步）：
        # 与迁出同一套匹配——同来源 scope、原 key、排在原事件之后；它只解释这一条 key。
        # sidecar 删掉之后事件是唯一快照，快照字段不全的事件不算证据（同迁出那支核追溯字段）
        decayed = any(
            d.get("entry_key") == ev["entry_key"]
            and d.get("scope") == (ev.get("scope") or ev.get("role") or ns) == ns
            and all(isinstance(d.get(f), str) and d[f].strip() for f in ("summary", "source", "provenance"))
            and isinstance(d.get("age_days"), int) and not isinstance(d.get("age_days"), bool)
            and position is not None and event_position(d, dr) is not None
            and event_position(d, dr) > position
            for dr, d in decayed_events)
        superseded = any(c.get("entry_key", "").split(":", 1)[0] == ns
                         and c.get("ts", "") > ev.get("ts", "")
                         for c in corrections)
        # 改域搬迁：源 key 随源条目一起离开 sidecar，更晚的 memory_migrated 用 from_entry_key 指名它。
        # 缺 from_entry_key 的历史搬迁只能按来源域 ＋ 摘要片段认，那不是按 key 核过，报「未解析」
        # 没有目标 entry_key 的搬迁记录说明不了条目去了哪里：它自己又因没 key 进不了 D，认了它就是两头都不查
        later_migrations = [m for mr, m in migration_rows
                            if isinstance(m.get("entry_key"), str) and m["entry_key"].strip()
                            and position is not None and event_position(m, mr) is not None
                            and event_position(m, mr) > position]
        migrated_away = any(m.get("from_entry_key") == ev["entry_key"] for m in later_migrations)
        migrated_unkeyed = not migrated_away and any(
            not m.get("from_entry_key") and str(m.get("source") or "").split("/", 1)[0] == ns
            and summary_matches_key(m.get("summary"), ev["entry_key"])
            for m in later_migrations)
        msg = f"D: 事件 entry_key 不在 sidecar：{ev['entry_key']}（{ev.get('type')}@{ev.get('ts','')[:19]}）"
        if moved_out:
            infos.append(msg + "（同来源 scope、原 key 的更晚 memory_moved_out 已记录迁出，仅提示）")
        elif migrated_away:
            infos.append(msg + "（更晚的 memory_migrated 以 from_entry_key 记录了改域搬迁，仅提示）")
        elif migrated_unkeyed:
            infos.append(msg + "（未解析：更晚有来源同域、摘要相符的 memory_migrated，但它缺 from_entry_key）")
        elif decayed:
            infos.append(msg + "（同来源 scope、原 key 的更晚 memory_decayed 已记录降级或归纳，仅提示）")
        elif superseded:
            infos.append(msg + "（同 scope 存在更晚纠正，疑似 supersede 删除，仅提示）")
        else:
            violations.append(msg)

    print(f"[三账对齐] MEMORY 条目 {len(mem_entries)} / sidecar {len(entries)} / "
          f"events {len(events)}（坏行 {bad_lines}）")
    for v in violations:
        print("VIOLATION", v)
    for i in infos:
        print("INFO", i)
    print(f"[结果] 违例 {len(violations)}，info {len(infos)}" + ("" if violations else " — 三账对齐 ✓"))
    return min(len(violations), 120)


if __name__ == "__main__":
    sys.exit(main())
