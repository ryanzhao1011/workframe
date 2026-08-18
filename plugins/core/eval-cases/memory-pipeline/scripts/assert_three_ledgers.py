#!/usr/bin/env python3
"""三账对齐核验：sidecar(memory-index.json) ↔ MEMORY.md ↔ events.jsonl。

G7 全链路终验的收口断言脚本（J10），也可在任意时点做中途对账。
四个核对方向，输出违例清单，exit code = 违例数（0 = 三账对齐）：

  A. MEMORY 中标注「从 notes 提升」的当期条目 → 必须有同 scope 同日期的
     memory_promoted 事件（--since 之前的历史条目降级为 info，不计违例）
  B. MEMORY 中 [纠正] 条目 → 必须有同 scope 同日期的 protected sidecar entry
  C. sidecar entry → 对应 scope 的 MEMORY.md 必须存在同日期条目（防幽灵索引）
  D. 带 entry_key 的 user_correction / memory_promoted 事件 → sidecar 必须有该 key

用法：
  python assert_three_ledgers.py --project <项目根> [--since YYYY-MM-DD]
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
# 冒号全角半角都认：`agent-protocols.md` 与 CLAUDE.md 模板示范的条目格式是半角
# `- YYYY-MM-DD:`，而 correction-detection 的写入模板用全角 `：`。只认一种会让
# 另一种写法的条目**整条游离在三账本校验之外**（静默漏检，不是误报）。
ENTRY_RE = re.compile(r"^- (?:\[纠正\] )?(\d{4}-\d{2}-\d{2})[:：]")
PROMOTED_MARK_RE = re.compile(r"（(\d{4}-\d{2}-\d{2})[^）]*(?:从 notes 提升|notes.*提升|融合提升)[^）]*）")
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


def load_memory_entries(memory_dir):
    """返回 [{scope, date, is_correction, promoted_date, text}]。"""
    out = []
    for mem in sorted(memory_dir.glob("*/MEMORY.md")):
        scope = mem.parent.name
        for ln in mem.read_text(encoding="utf-8", errors="replace").splitlines():
            m = ENTRY_RE.match(ln)
            if not m:
                continue
            pm = PROMOTED_MARK_RE.search(ln)
            mg = MIGRATED_MARK_RE.search(ln)
            out.append({
                "scope": scope,
                "date": m.group(1),
                "is_correction": ln.startswith("- [纠正]"),
                "promoted_date": pm.group(1) if pm else None,
                "migrated_date": mg.group(1) if mg else None,
                "text": ln[:80],
            })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--since", default=None,
                    help="只对该日期（含）之后的提升条目严格要求事件；更早的降级 info")
    args = ap.parse_args()

    root = Path(args.project)
    state = root / ".claude" / "workframe-state"
    memory_dir = root / ".claude" / "agent-memory"

    sidecar = json.loads((state / "memory-index.json").read_text(encoding="utf-8"))
    entries = sidecar.get("entries", {})

    events, bad_lines = [], 0
    for ln in (state / "events.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
        if not ln.strip():
            continue
        try:
            events.append(json.loads(ln))
        except json.JSONDecodeError:
            bad_lines += 1
    promoted_events = [e for e in events if e.get("type") == "memory_promoted"]
    migrated_events = [e for e in events if e.get("type") == "memory_migrated"]
    keyed_events = [e for e in events
                    if e.get("type") in ("memory_promoted", "user_correction", "memory_migrated")
                    and e.get("entry_key")]

    mem_entries = load_memory_entries(memory_dir)
    violations, infos = [], []

    # A. 提升条目 → memory_promoted 事件
    for me in mem_entries:
        if not me["promoted_date"]:
            continue
        hit = any(
            (ev.get("scope") == me["scope"] or ev.get("role") == me["scope"])
            and ev.get("ts", "").startswith(me["promoted_date"])
            for ev in promoted_events
        ) or any(  # 融合提升可能记在来源角色名下，放宽到仅日期
            ev.get("ts", "").startswith(me["promoted_date"]) for ev in promoted_events
        ) and me["scope"] == "shared"
        if not hit:
            msg = f"A: {me['scope']} 提升条目({me['promoted_date']})无 memory_promoted 事件 | {me['text']}"
            if args.since and me["promoted_date"] < args.since:
                infos.append(msg + "（早于 --since，仅提示）")
            else:
                violations.append(msg)

    # A'. 搬迁条目 → memory_migrated 事件（与 A 同构，但事件类型不可互换：
    #     用 memory_promoted 顶替会让消费方误以为该域刚消化过 notes 积压）
    for me in mem_entries:
        if not me["migrated_date"]:
            continue
        hit = any(
            (ev.get("scope") == me["scope"] or ev.get("role") == me["scope"])
            and ev.get("ts", "").startswith(me["migrated_date"])
            for ev in migrated_events
        )
        if not hit:
            msg = (f"A': {me['scope']} 搬迁条目({me['migrated_date']})无 memory_migrated 事件"
                   f" | {me['text']}")
            if args.since and me["migrated_date"] < args.since:
                infos.append(msg + "（早于 --since，仅提示）")
            else:
                violations.append(msg)

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
    for ev in keyed_events:
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
        superseded = any(c.get("entry_key", "").split(":", 1)[0] == ns
                         and c.get("ts", "") > ev.get("ts", "")
                         for c in corrections)
        msg = f"D: 事件 entry_key 不在 sidecar：{ev['entry_key']}（{ev.get('type')}@{ev.get('ts','')[:19]}）"
        if superseded:
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
