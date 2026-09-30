#!/usr/bin/env python3
"""`maintenance_workorder.py --commit` 去重的常驻单测（冻结时钟）。爆炸半径档 4：判据错了要么重复记账
（三本账各多一条、librarian 用量翻倍），要么把一条真该记的 promotion 静默吞掉。

两个生产方会记同一条 promotion：`--commit`（按**本地日期**拼 key）与模型照 librarian SKILL 手写的 sidecar 条目
＋ `memory_promoted` 事件（日期口径没规定，可能是 UTC）。判据是**两半分开判**、日期取窗口 D（提交时刻与 manifest
mtime 各自的本地日期与 UTC 日期），见 `_promotion_decision`。

时钟冻在两个时刻各跑一遍：本地 00:00–08:00（+08:00 时本地日期与 UTC 日期不同——这一格不冻结时与另一格完全一样），
与 08:00 之后。每个时刻：
  sidecar ＋ 事件都已写（本地日期 key / UTC 日期 key）⇒ 两边都不重复、事件流零新增（含 skill_used）；
  只有 sidecar ⇒ 只补事件、沿用 key；只有事件 ⇒ 只补 sidecar、沿用 key；事件在 spill 里同样算；
  片段相同摘要不同 ⇒ 加后缀；同一份 manifest 里重复两条 ⇒ 只记一条；什么都没有 ⇒ 照常记（本地日期 key）；
  `l2_candidates` 已有同一行 ⇒ 跳过。另有跨零点一格（manifest 写于本地前一天 23:50、提交于次日 00:10）。
sidecar 保护四格：已有同 key 条目（created_at 不在 D）⇒ 不覆盖；同 scope、同日、同片段的 `[纠正]` 条目 ⇒ 不当成
这条 promotion 的 sidecar、另起带后缀的 key；判「是不是这条 promotion 的 sidecar」的两个合取条件各一格——来源不在
「从 notes 提升」白名单里、provenance 是 user-decree，各自单独成立时都另起带后缀的 key。
恒等格：`workframe-event user-correction` 的 entry_key 仍按「UTC 日期 ＋ 删空白后前 20 字」算。
"""

import atexit
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "plugins" / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.dont_write_bytecode = True

_IMPORT_HOME = Path(tempfile.mkdtemp(prefix="wf-dedup-import-"))
# 回收挂在 import 上、不挂在 main 上：探针与写坏反证只 import 本文件、直调测试函数，不走 main
atexit.register(shutil.rmtree, _IMPORT_HOME, True)
os.environ["CLAUDE_PROJECT_DIR"] = str(_IMPORT_HOME)       # 模块 import 期按它解析项目根；随后逐格改指向
import maintenance_workorder as mw  # noqa: E402
import workframe_event as we  # noqa: E402
from _state_io import state_dir_of  # noqa: E402

FAILURES = []
TZ8 = timezone(timedelta(hours=8))
SUMMARY = "发版前必须跑脱敏终检，扫描结果退出码 2 不是 0"


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + ("" if ok else f": got={got!r} want={want!r}"))
    if not ok:
        FAILURES.append(label)


class Fixture:
    def __init__(self, now_utc, mtime_utc=None):
        self.now, self.mtime = now_utc, mtime_utc or now_utc

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wf-dedup-"))
        P = self.tmp / "proj"
        (P / "logs").mkdir(parents=True)
        state = state_dir_of(P)
        state.mkdir(parents=True)
        (state / "events.jsonl").write_bytes(b"")
        (state / "memory-index.json").write_bytes(
            json.dumps({"__schema__": "workframe.memory-index.v2", "entries": {}}).encode())
        (state / "activity-state.json").write_bytes(b'{"session_counter": 1}')
        for name, val in (("PROJECT_DIR", P), ("STATE_DIR", state), ("ACTIVITY_FILE", state / "activity-state.json"),
                          ("CANDIDATES_FILE", state / "promotion-candidates.md"),
                          ("WORKORDER_FILE", state / "maintenance-workorder.md"),
                          ("MAINT_FLAG_FILE", state / "maintenance-run.flag"), ("EVENTS_FILE", state / "events.jsonl"),
                          ("SIDECAR_FILE", state / "memory-index.json"),
                          ("MANIFEST_FILE", P / "logs" / "maintenance-commit.json"),
                          ("MANIFEST_APPLIED", P / "logs" / "maintenance-commit.applied.json")):
            setattr(mw, name, val)
        mw._utcnow = lambda: self.now
        mw._local_tz = lambda: TZ8
        self.P, self.state = P, state
        return self

    def manifest(self, promotions=(), l2=()):
        f = mw.MANIFEST_FILE
        f.write_bytes(json.dumps({"promotions": list(promotions), "l2_candidates": list(l2)}, ensure_ascii=False).encode())
        ts = self.mtime.timestamp()
        os.utime(f, (ts, ts))

    def sidecar(self, entries):
        mw.SIDECAR_FILE.write_bytes(json.dumps({"__schema__": "workframe.memory-index.v2", "entries": entries},
                                               ensure_ascii=False).encode())

    def event(self, key, summary=SUMMARY, spill=False):
        line = json.dumps({"ts": "2026-09-16T12:00:00+00:00", "type": "memory_promoted", "scope": "dev",
                           "entry_key": key, "summary": summary[:80], "source": "notes.md", "protected": False,
                           "provenance": "inferred", "harness": "cc"}, ensure_ascii=False) + "\n"
        target = self.state / "events.4242.spill.jsonl" if spill else mw.EVENTS_FILE
        with open(target, "ab") as fh:
            fh.write(line.encode("utf-8"))

    def commit(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = mw.commit_manifest()
        return rc, buf.getvalue()

    def entries(self):
        return json.loads(mw.SIDECAR_FILE.read_bytes().decode("utf-8"))["entries"]

    def events(self, typ=None):
        out = []
        for ln in mw.EVENTS_FILE.read_bytes().decode("utf-8").splitlines():
            if ln.strip():
                ev = json.loads(ln)
                if typ is None or ev.get("type") == typ:
                    out.append(ev)
        return out

    def __exit__(self, *a):
        shutil.rmtree(self.tmp, ignore_errors=True)


FRAG = "".join(SUMMARY.split())[:20]          # 独立按规则算，不调被测函数
PROMO = {"scope": "dev", "summary": SUMMARY}


def run_clock(label, now_utc):
    local, utc = now_utc.astimezone(TZ8).date().isoformat(), now_utc.date().isoformat()
    print(f"[{label}] now={now_utc.isoformat()} 本地日期 {local} / UTC 日期 {utc}")
    k_local, k_utc = f"dev:{local}:{FRAG}", f"dev:{utc}:{FRAG}"

    with Fixture(now_utc) as fx:
        fx.manifest([PROMO])
        rc, _ = fx.commit()
        check("什么都没有 ⇒ 照常记，key 用本地日期", (rc, list(fx.entries()), [e["entry_key"] for e in fx.events("memory_promoted")]),
              (0, [k_local], [k_local]))
        check("… 写了 skill_used", len(fx.events("skill_used")), 1)

    for key_label, key in (("本地日期 key", k_local), ("UTC 日期 key", k_utc)):
        with Fixture(now_utc) as fx:
            fx.sidecar({key: {"scope": "dev", "created_at": key.split(":")[1], "provenance": "inferred",
                              "protected": False, "source": "notes.md"}})
            fx.event(key)
            fx.manifest([PROMO])
            side0, ev0 = mw.SIDECAR_FILE.read_bytes(), mw.EVENTS_FILE.read_bytes()
            rc, out = fx.commit()
            check(f"sidecar＋事件都已写（{key_label}）⇒ sidecar 字节不变", (rc, mw.SIDECAR_FILE.read_bytes() == side0), (0, True))
            check(f"… 事件流零新增（含 skill_used）", mw.EVENTS_FILE.read_bytes() == ev0, True)
            check(f"… 输出计数跳过 1 条", "跳过 1 条已存在" in out, True)

        with Fixture(now_utc) as fx:
            fx.sidecar({key: {"scope": "dev", "created_at": key.split(":")[1], "provenance": "inferred",
                              "protected": False, "source": "notes.md"}})
            fx.manifest([PROMO])
            fx.commit()
            check(f"只有 sidecar（{key_label}）⇒ 只补事件、沿用 key",
                  (list(fx.entries()), [e["entry_key"] for e in fx.events("memory_promoted")]), ([key], [key]))

        for spill in (False, True):
            with Fixture(now_utc) as fx:
                fx.event(key, spill=spill)
                fx.manifest([PROMO])
                fx.commit()
                check(f"只有事件{'（在 spill 里）' if spill else ''}（{key_label}）⇒ 只补 sidecar、沿用 key、不补事件",
                      (list(fx.entries()), len(fx.events("memory_promoted")), len(fx.events("skill_used"))),
                      ([key], 0 if spill else 1, 0))

    with Fixture(now_utc) as fx:
        fx.sidecar({k_local: {"scope": "dev", "created_at": local, "provenance": "inferred", "protected": False,
                              "source": "notes.md"}})
        fx.event(k_local, summary=SUMMARY[:20] + "但后半句完全是另一件事")
        fx.manifest([PROMO])
        fx.commit()
        keys = sorted(fx.entries())
        check("片段相同、摘要不同 ⇒ 加后缀", keys, sorted([k_local, k_local + "-2"]))
        check("… 新事件用带后缀的 key", [e["entry_key"] for e in fx.events("memory_promoted")][-1], k_local + "-2")

    with Fixture(now_utc) as fx:
        fx.manifest([PROMO, dict(PROMO)])
        fx.commit()
        check("同一份 manifest 里重复两条 ⇒ 只记一条",
              (list(fx.entries()), len(fx.events("memory_promoted"))), ([k_local], 1))

    with Fixture(now_utc) as fx:
        cand = {"scope": "dev", "summary": "候选一句话", "source": "notes 第 3 条", "target": "AGENTS.md §x"}
        for day in (local, utc):
            mw.CANDIDATES_FILE.write_bytes(
                f"# L2\n\n- [x] dev | {day} | 候选一句话 | 出处: notes 第 3 条 | 建议落点: AGENTS.md §x\n".encode())
            before = mw.CANDIDATES_FILE.read_bytes()
            fx.manifest(l2=[cand])
            rc, _ = fx.commit()
            check(f"l2_candidates 已有同一行（日期 {day}）⇒ 跳过", (rc, mw.CANDIDATES_FILE.read_bytes() == before), (0, True))
        mw.CANDIDATES_FILE.write_bytes(b"# L2\n\n")
        fx.manifest(l2=[cand])
        fx.commit()
        check("l2_candidates 没有 ⇒ 追加一行", mw.CANDIDATES_FILE.read_bytes().decode("utf-8").count("候选一句话"), 1)


def test_cross_midnight():
    print("[跨零点] manifest 写于本地 09-16 23:50，--commit 跑于本地 09-17 00:10")
    mtime = datetime(2026, 9, 16, 23, 50, tzinfo=TZ8).astimezone(timezone.utc)
    now = datetime(2026, 9, 17, 0, 10, tzinfo=TZ8).astimezone(timezone.utc)
    key = f"dev:2026-09-16:{FRAG}"           # 模型在会话里按本地前一天拼的 key
    with Fixture(now, mtime) as fx:
        fx.sidecar({key: {"scope": "dev", "created_at": "2026-09-16", "provenance": "inferred", "protected": False,
                          "source": "notes.md"}})
        fx.event(key)
        fx.manifest([PROMO])
        ev0 = mw.EVENTS_FILE.read_bytes()
        fx.commit()
        check("跨零点 ⇒ 不重复", (list(fx.entries()), mw.EVENTS_FILE.read_bytes() == ev0), ([key], True))
    print("[跨 UTC 零点] manifest 写于本地 09-16 07:50（UTC 09-15），--commit 跑于本地 09-16 08:10（UTC 09-16）")
    # 模型按 UTC 拼的 key 日期 09-15 只落在 manifest mtime 的 UTC 日期里——提交时刻的两个日期都是 09-16，
    # 这一格因此只靠 mtime 那一半撑着（上一格被提交时刻的 UTC 日期顺带覆盖，分不出 mtime 那一半死没死）
    mtime = datetime(2026, 9, 16, 7, 50, tzinfo=TZ8).astimezone(timezone.utc)
    now = datetime(2026, 9, 16, 8, 10, tzinfo=TZ8).astimezone(timezone.utc)
    key = f"dev:2026-09-15:{FRAG}"
    with Fixture(now, mtime) as fx:
        fx.sidecar({key: {"scope": "dev", "created_at": "2026-09-15", "provenance": "inferred", "protected": False,
                          "source": "notes.md"}})
        fx.event(key)
        fx.manifest([PROMO])
        ev0 = mw.EVENTS_FILE.read_bytes()
        fx.commit()
        check("跨 UTC 零点（只有 mtime 的日期覆盖到）⇒ 不重复", (list(fx.entries()), mw.EVENTS_FILE.read_bytes() == ev0),
              ([key], True))


def test_sidecar_guards():
    print("[sidecar 保护] 已有同 key 条目不覆盖；[纠正] 条目不当成这条 promotion 写过的 sidecar")
    now = datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)
    key = f"dev:2026-09-17:{FRAG}"
    # 同 key 条目已在但 created_at 不在 D（sidecar 半边判「没有」），事件半边判「有」⇒ 该补 sidecar，而 key 被占
    protected = {"scope": "dev", "created_at": "2026-08-01", "provenance": "user-confirmed", "protected": True,
                 "source": "notes.md"}
    with Fixture(now) as fx:
        fx.sidecar({key: dict(protected)})
        fx.event(key)
        fx.manifest([PROMO])
        side0 = mw.SIDECAR_FILE.read_bytes()
        fx.commit()
        check("同 key 条目已在 ⇒ 不覆盖（protected / provenance 原样）", fx.entries().get(key), protected)
        check("… sidecar 字节不变", mw.SIDECAR_FILE.read_bytes() == side0, True)
    # 同 scope、同日、同片段的 [纠正] 条目：不是这条 promotion 的 sidecar
    correction = {"scope": "dev", "created_at": "2026-09-17", "provenance": "user-decree", "protected": True,
                  "source": "[纠正]"}
    with Fixture(now) as fx:
        fx.sidecar({key: dict(correction)})
        fx.manifest([PROMO])
        fx.commit()
        entries = fx.entries()
        check("[纠正] 条目原样", entries.get(key), correction)
        check("… 这条 promotion 另起带后缀的 key", sorted(entries), sorted([key, key + "-2"]))
        check("… 事件用带后缀的 key", [e["entry_key"] for e in fx.events("memory_promoted")], [key + "-2"])
    # 两个合取条件各自钉住：上一格的 [纠正] 条目两个条件同时不满足，只去掉其中一个它照样绿
    for label, entry in (
            ("来源不是从 notes 提升（provenance 不是 user-decree）",
             {"scope": "dev", "created_at": "2026-09-17", "provenance": "user-confirmed", "protected": False,
              "source": "auto-memory"}),
            ("provenance 是 user-decree（来源在白名单里）",
             {"scope": "dev", "created_at": "2026-09-17", "provenance": "user-decree", "protected": True,
              "source": "notes.md"})):
        with Fixture(now) as fx:
            fx.sidecar({key: dict(entry)})
            fx.manifest([PROMO])
            fx.commit()
            entries = fx.entries()
            check(f"{label} ⇒ 条目原样", entries.get(key), entry)
            check(f"{label} ⇒ promotion 另起带后缀的 key、事件同", (sorted(entries), [e["entry_key"] for e in fx.events("memory_promoted")]),
                  (sorted([key, key + "-2"]), [key + "-2"]))


def test_event_key_identity():
    print("[恒等] workframe-event user-correction 的 entry_key")
    with Fixture(datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)) as fx:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            we.main(["user-correction", "--scope", "dev", "--role", "dev", "--summary", SUMMARY,
                     "--project", str(fx.P), "--dry-run"])
        line = json.loads(buf.getvalue().strip().splitlines()[-1])
        want = f"dev:{datetime.now(timezone.utc).date().isoformat()}:{FRAG}"
        check("entry_key = scope:UTC 日期:删空白后前 20 字", line.get("entry_key"), want)


def main():
    try:
        run_clock("本地 00:00–08:00", datetime(2026, 9, 16, 20, 30, tzinfo=timezone.utc))
        run_clock("本地 08:00 之后", datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc))
        test_cross_midnight()
        test_sidecar_guards()
        test_event_key_identity()
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        FAILURES.append(f"抛异常: {type(e).__name__}: {e}")
    if FAILURES:
        print(f"\n{len(FAILURES)} failed: {FAILURES}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
