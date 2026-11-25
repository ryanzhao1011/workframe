#!/usr/bin/env python3
"""`signoff-guard.py` 的常驻单测 —— 它是**在判断别的东西对不对**的东西，改它属于「需要独立验证」。

本套件守六件事，每件都配一个**它自己的失败方式**：

1. **schema 缺一即拒**：类别、验了什么、没验什么、切片两端、`verified_ranges`；独立验证缺
   验证方、你拍板缺批准记录、散文形态、旧 schema、打了 `main-executed` 却没签注，各自报红。
2. **否决项靠重算，不读结论**：切片里删了列表项而没写「你拍板」必拒；把「否决项：未命中」
   敲进任何一段不改变判定；符号引用 / 解析不到的 hash 走「验不了」而不是放行。
3. **只拦本次改出来的问题**：首次建快照只报不拦；存量问题只报；新出现或改过的才拦，
   且被拦的那条改回之前一直拦；通过的签注进快照后不再重算。
4. **查哪些签注**：截止日之前完成的、未完成的、不带签注也不打 `main-executed` 的，不查。
5. **Bash 只报不拦**、自签开关关闭时「自理」必拒、内部出错留痕。
6. **整板缓存**：看板字节没变不解析，但上一次查出的问题照报（Edit 侧照拦）；内容、配置、规则文件
   任一变化即失效（按内容不按 mtime）；首次建快照与重算跑不起来的结论不进缓存；坏缓存按未命中。

> **本套件测的是 `guard()` / `judge()` / `check_schema()` 与 `main()` 的分流，不是真实会话里
> exit 2 能否阻断工具调用**——那条已在别处实测，本套件不重复证明。
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# 文件名带连字符，import 不了，走 spec 加载
_spec = importlib.util.spec_from_file_location("signoff_guard", SCRIPTS_DIR / "signoff-guard.py")
sg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sg)

FAILURES = []


def check(label, got, want):
    if got == want:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


def check_any(label, needle, haystack):
    if any(needle in h for h in haystack):
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}: {needle!r} 不在 {haystack!r}")
        FAILURES.append(label)


def check_none(label, needle, haystack):
    if not any(needle in h for h in haystack):
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}: 不该出现 {needle!r}，实际 {haystack!r}")
        FAILURES.append(label)


def _main_with_stdin(payload_json, project_dir):
    """喂一份 payload 给 `main()`，返回 (退出码, stderr 文本)。

    **测的是 `main()` 的分流与退出码**，不是 `guard()` 的返回值——Bash 只报不拦那条边界
    就活在这一层，只测 `guard()` 看不见它。

    两处替身：①`sys.stdin` 换成一个只提供 `isatty()` 与 `buffer` 的壳
    （`TextIOWrapper.buffer` 是只读属性，赋不进去）；②`CLAUDE_PROJECT_DIR` 指向 fixture，
    否则 `main()` 会去解析**运行本套件那个项目**的看板——那既不是被测对象，也会让用例
    随真实看板的内容时红时绿。
    """
    import contextlib
    import io as _io

    class _Stdin:
        def __init__(self, data):
            self.buffer = _io.BytesIO(data)

        def isatty(self):
            return False

    stdin_bak, env_bak = sys.stdin, os.environ.get("CLAUDE_PROJECT_DIR")
    sys.stdin = _Stdin(payload_json.encode("utf-8"))
    os.environ["CLAUDE_PROJECT_DIR"] = str(project_dir)
    err = _io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            rc = sg.main()
    finally:
        sys.stdin = stdin_bak
        if env_bak is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = env_bak
    return rc, err.getvalue()


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout.strip()


def _w(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


NL = chr(10)   # 用常量而不是换行转义字面量：经 shell heredoc 生成代码时它会被吃成真换行
DONE = "2026-11-05"   # 截止日之后的完成日


def _commit(root, msg):
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", msg)
    return _git(root, "rev-parse", "HEAD")


def build_project(root, enabled=True):
    """一个仓，三个提交：base → clean（只加一行正文）→ veto（删一条列表项）。返回三个 hash。"""
    _w(root / ".workframe-config.json",
       json.dumps({"project_name": "fx",
                   "signoff": {"self_signoff_enabled": enabled}}, ensure_ascii=False) + NL)
    _w(root / "docs/plain.md", "# plain" + NL + NL + "- 第一条" + NL + "- 第二条" + NL)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fx@example.invalid")
    _git(root, "config", "user.name", "fx")
    _git(root, "config", "core.autocrlf", "false")
    base = _commit(root, "base")
    _w(root / "docs/plain.md", "# plain" + NL + NL + "- 第一条" + NL + "- 第二条" + NL + NL + "正文" + NL)
    clean = _commit(root, "clean")
    _w(root / "docs/plain.md", "# plain" + NL + NL + "- 第一条" + NL + NL + "正文" + NL)
    veto = _commit(root, "veto")
    return {"base": base, "clean": clean, "veto": veto}


def write_board(root, tasks):
    import yaml
    if isinstance(tasks, dict):
        tasks = [tasks]
    _w(root / "projects/board.yaml",
       yaml.safe_dump({"summary": {"total": len(tasks)}, "tasks": tasks},
                      allow_unicode=True, sort_keys=False))
    return (root / "projects/board.yaml").resolve()


def mk_signoff(since, diff_ref, mode="自理", **over):
    blk = {"mode": mode, "since": since, "diff_ref": diff_ref,
           "verified": "验了 A、B", "not_verified": "没验 C，因为它不在本次改动面内"}
    if mode == "独立验证":
        blk["verifier"] = {"who": "@qa", "report": "scratchpad/qa-report.md"}
    if mode == "你拍板":
        blk["approval"] = "用户 11-05 当面批准删除第二条"
    blk.update(over)
    return blk


def mk_task(blk, tid="TASK-001", **over):
    t = {"id": tid, "status": "completed", "completed_at": DONE,
         "tags": ["main-executed"], "qa_signoff": blk, "verified_ranges": "覆盖 docs/ 全部"}
    t.update(over)
    return t


def _reset_snapshot(root):
    p = sg.state_dir_of(root) / sg.SNAPSHOT_NAME
    if p.exists():
        p.unlink()


def _seed_snapshot(root):
    """建一个空快照，让后面的写入都算「本次新出现」（否则首次运行只报不拦）。"""
    _reset_snapshot(root)
    sg.save_snapshot(root, {})


def _snap_doc(root):
    return json.loads((sg.state_dir_of(root) / sg.SNAPSHOT_NAME).read_text(encoding="utf-8"))


def _drop_board_cache(root):
    """只移开整板缓存、签注快照原样保留：要测的是快照那一层时，先让整板缓存让路。"""
    data = _snap_doc(root)
    data.pop("board", None)
    _w(sg.state_dir_of(root) / sg.SNAPSHOT_NAME, json.dumps(data, ensure_ascii=False) + NL)


class _CountParse:
    """数 `load_board` 被调了几次——整板缓存命中时一次都不该调。包一层、照常解析，不改结果。"""

    def __enter__(self):
        self.n, self._real = 0, sg.load_board

        def wrapped(*a, **k):
            self.n += 1
            return self._real(*a, **k)
        sg.load_board = wrapped
        return self

    def __exit__(self, *exc):
        sg.load_board = self._real
        return False


# ---------- 1. schema ----------


def test_schema_layer(h):
    print("[schema] 缺一即拒，三种类别各有必填")
    for mode in sg.MODES:
        blk = mk_signoff(h["base"], h["clean"], mode=mode)
        check(f"{mode} 完整形态零问题", sg.check_schema("T", mk_task(blk), blk), [])

    ok = mk_signoff(h["base"], h["clean"])
    for field in ("mode", "verified", "not_verified", "since", "diff_ref"):
        blk = dict(ok)
        blk.pop(field)
        check_any(f"缺 {field} → 报", field, sg.check_schema("T", mk_task(blk), blk))
    blk = dict(ok, verified="   ")
    check_any("verified 只有空白 → 报", "verified", sg.check_schema("T", mk_task(blk), blk))
    blk = dict(ok, mode="自签")
    check_any("类别写成旧档位名 → 报", "mode", sg.check_schema("T", mk_task(blk), blk))

    ind = mk_signoff(h["base"], h["clean"], mode="独立验证")
    for broken in ({"verifier": None}, {"verifier": {"who": "@qa"}},
                   {"verifier": {"who": "", "report": "x"}}, {"verifier": "codex 验过"}):
        blk = dict(ind, **broken)
        check_any(f"独立验证 verifier={broken['verifier']!r} → 报", "verifier",
                  sg.check_schema("T", mk_task(blk), blk))
    usr = dict(mk_signoff(h["base"], h["clean"], mode="你拍板"), approval="")
    check_any("你拍板缺 approval → 报", "approval", sg.check_schema("T", mk_task(usr), usr))
    # 自理不要求 verifier / approval：要求了就是把自理变成另一种独立验证
    check("自理不要求 verifier / approval", sg.check_schema("T", mk_task(ok), ok), [])

    t = mk_task(ok)
    t.pop("verified_ranges")
    check_any("缺 verified_ranges → 报", "verified_ranges", sg.check_schema("T", t, ok))
    check_any("散文形态 → 报（须映射）", "映射形态",
              sg.check_schema("T", mk_task("验过了"), "验过了"))
    check_any("打了 main-executed 却没签注 → 报", "没有 `qa_signoff`",
              sg.check_schema("T", mk_task(None), None))
    old = {"tier": "自签", "same_party": True, "admission": {"radius": 1},
           "verified": "x", "not_verified": "y"}
    check_any("旧 schema → 报并点名现行字段", "旧 schema", sg.check_schema("T", mk_task(old), old))


# ---------- 2. 查哪些签注 ----------


def test_scope(h):
    print("[范围] 截止日、状态、签注 / 标签")
    blk = mk_signoff(h["base"], h["clean"])
    check("截止日之后完成 ＋ 带签注 → 查", sg.in_scope(mk_task(blk)), True)
    check("截止日当天 → 查", sg.in_scope(mk_task(blk, completed_at=sg.SCHEMA_SINCE)), True)
    check("截止日前一天 → 不查", sg.in_scope(mk_task(blk, completed_at="2026-10-31")), False)
    import datetime
    check("completed_at 被 YAML 解析成 date 也认", sg.in_scope(
        mk_task(blk, completed_at=datetime.date(2026, 11, 5))), True)
    check("未完成 → 不查", sg.in_scope(mk_task(blk, status="pending_qa")), False)
    check("缺 completed_at → 不查（已知挡不住的形态）",
          sg.in_scope({k: v for k, v in mk_task(blk).items() if k != "completed_at"}), False)
    check("无签注、有 main-executed → 查", sg.in_scope(mk_task(None)), True)
    check("无签注、无 main-executed → 不查（与非研发任务同形）",
          sg.in_scope(mk_task(None, tags=[])), False)
    check("无签注、带 needs-qa-regression → 查（看板约定里它恒为研发类）",
          sg.in_scope(mk_task(None, tags=["needs-qa-regression"])), True)
    check_any("且缺签注 → 报", "没有 `qa_signoff`",
              sg.check_schema("T", mk_task(None, tags=["needs-qa-regression"]), None))
    check("散文签注、无标签 → 查（@qa 签的也要映射）",
          sg.in_scope(mk_task("散文", tags=[])), True)


# ---------- 3. 否决项重算 ----------


def test_recompute(root, h):
    print("[重算] 只吃切片两端，不读结论")
    j = lambda blk, **kw: sg.judge(root, "T", mk_task(blk, **kw), True)  # noqa: E731

    check("干净切片 ＋ 自理 → 通过", j(mk_signoff(h["base"], h["clean"])), [])
    check("干净切片 ＋ 独立验证 → 通过", j(mk_signoff(h["base"], h["clean"], mode="独立验证")), [])
    check_any("删了列表项 ＋ 自理 → 拒（V1）", "V1",
              j(mk_signoff(h["clean"], h["veto"])))
    check_any("删了列表项 ＋ 独立验证 → 同样拒（否决项不论类别，走的是命中那条而不是待裁决）", "必须交用户拍板",
              j(mk_signoff(h["clean"], h["veto"], mode="独立验证")))
    check("删了列表项 ＋ 你拍板 ＋ approval → 通过", j(mk_signoff(h["clean"], h["veto"], mode="你拍板")), [])
    check_any("你拍板也要切片可重算（hash 解析不到 → 拒）", "解析不到",
              j(mk_signoff("deadbeef", h["veto"], mode="你拍板")))
    # 结论敲进签注不改变判定
    lie = mk_signoff(h["clean"], h["veto"], verified="否决项：未命中", not_verified="否决项：未命中",
                     ruling="V1：未命中")
    check_any("把「未命中」敲进各段 → 照样拒", "V1", j(lie))

    # 「验不了」不当成「通过」
    check_any("since 写 HEAD → 拒（符号引用）", "十六进制",
              j(mk_signoff("HEAD", h["clean"])))
    check_any("diff_ref 写分支名 → 拒", "十六进制",
              j(mk_signoff(h["base"], "master")))
    check_any("hash 解析不到 → 拒", "解析不到",
              j(mk_signoff("deadbeef", h["clean"])))
    check_any("两端仓不一致 → 拒", "不一致",
              j(mk_signoff({".": h["base"], "nested": h["base"]}, {".": h["clean"]})))
    check_any("声明的仓不存在 → 拒", "不是一个 git 仓",
              j(mk_signoff({"nope": h["base"]}, {"nope": h["clean"]})))
    check_any("声明的目录在仓里但自己不是仓根 → 拒（不许静默对外层仓取 diff）",
              "不是一个 git 仓", j(mk_signoff({"docs": h["base"]}, {"docs": h["clean"]})))


def test_pending_and_multirepo(tmp):
    print("[重算] 待裁决项与多仓")
    root = tmp / "multi"
    root.mkdir()
    h = build_project(root)
    nested = root / "nested"
    nested.mkdir()
    _w(root / ".gitignore", "nested/" + NL)
    _commit(root, "ignore nested")
    _w(nested / "a.txt", "a" + NL)
    _git(nested, "init", "-q")
    _git(nested, "config", "user.email", "fx@example.invalid")
    _git(nested, "config", "user.name", "fx")
    n0 = _commit(nested, "n0")
    _w(nested / "list.md", "- 甲" + NL + "- 乙" + NL)
    n1 = _commit(nested, "n1")
    _w(nested / "list.md", "- 甲" + NL)
    n2 = _commit(nested, "n2")
    r_head = _git(root, "rev-parse", "HEAD")

    j = lambda blk: sg.judge(root, "T", mk_task(blk), True)  # noqa: E731
    check("两仓都干净 → 通过",
          j(mk_signoff({".": h["base"], "nested": n0}, {".": h["clean"], "nested": n1})), [])
    check_any("嵌套仓那侧删了列表项 → 拒（按仓取到了嵌套仓）", "V1",
              j(mk_signoff({".": h["base"], "nested": n1}, {".": h["clean"], "nested": n2})))
    # 能力边界第 3 条的现状：没声明的仓不在切片里
    check("只声明根仓 → 嵌套仓的删除看不见（已登记的能力边界，不是期望行为）",
          j(mk_signoff({".": h["base"]}, {".": h["clean"]})), [])

    # V6：改 AGENTS.md → 待裁决
    _w(root / "AGENTS.md", "# agents" + NL)
    v6 = _commit(root, "agents")
    blk = mk_signoff(r_head, v6)
    check_any("触及 AGENTS.md 无裁决 → 拒（待裁决）", "待裁决", j(blk))
    check("触及 AGENTS.md 写了 ruling → 通过", j(dict(blk, ruling="V6：只加了标题，表述变更")), [])
    check("触及 AGENTS.md 交你拍板 → 通过", j(mk_signoff(r_head, v6, mode="你拍板")), [])


# ---------- 4. 快照：只拦本次改出来的 ----------


def test_snapshot(root, h):
    print("[快照] 首次只报 / 存量只报 / 新改的才拦 / 拦下的一直拦 / 通过的不重算")
    good = mk_task(mk_signoff(h["base"], h["clean"]), tid="TASK-001")
    bad = mk_task(mk_signoff(h["clean"], h["veto"]), tid="TASK-002")
    real_today = sg._today
    sg._today = lambda: "2026-12-31"   # 夹具的完成日都早于「今天」→ 首次运行时都算存量
    try:
        _snapshot_body(root, h, good, bad)
    finally:
        sg._today = real_today

    # 首次运行时，今天完成的违规签注照样拦（否则当次写进来的会被当成存量永久豁免）
    sg._today = lambda: DONE
    try:
        _reset_snapshot(root)
        probs, notes, _ = sg.guard(root, write_board(root, [good, bad]))
        check_any("首次建快照：今天完成的违规签注照拦", "TASK-002", probs)
        probs, _, _ = sg.guard(root, write_board(root, [good, bad]))
        check_any("且下一次写入仍拦（没被写进基线）", "TASK-002", probs)
    finally:
        sg._today = real_today


def _snapshot_body(root, h, good, bad):
    _reset_snapshot(root)
    probs, notes, passed = sg.guard(root, write_board(root, [good, bad]))
    check("首次建快照：不拦", probs, [])
    check_any("首次建快照：存量问题照样报出来", "首次建快照", notes)
    check("首次建快照：不算「本次通过」（不写事件）", passed, [])

    probs, notes, _ = sg.guard(root, write_board(root, [good, bad]))
    check("存量问题原样未动 → 不拦", probs, [])
    check_any("存量问题原样未动 → 仍报", "存量", notes)

    new_bad = mk_task(mk_signoff(h["clean"], h["veto"]), tid="TASK-003")
    probs, _, _ = sg.guard(root, write_board(root, [good, bad, new_bad]))
    check_any("新出现的问题签注 → 拦", "TASK-003", probs)
    probs, _, _ = sg.guard(root, write_board(root, [good, bad, new_bad]))
    check_any("被拦的那条没改回 → 下一次写入仍拦", "TASK-003", probs)

    fixed = mk_task(mk_signoff(h["clean"], h["veto"], mode="你拍板"), tid="TASK-003")
    probs, _, passed = sg.guard(root, write_board(root, [good, bad, fixed]))
    check("改回之后 → 不拦", probs, [])
    check("改回之后 → 算本次通过（写事件）", [p[0] for p in passed], ["TASK-003"])

    # 改过一条原本通过的签注，使之违规 → 拦
    broken_good = mk_task(mk_signoff(h["base"], h["veto"]), tid="TASK-001")
    probs, _, _ = sg.guard(root, write_board(root, [broken_good, bad, fixed]))
    check_any("改坏一条已通过的签注 → 拦", "TASK-001", probs)
    sg.guard(root, write_board(root, [good, bad, fixed]))   # 改回

    # 通过的进快照后不再重算：把重算换成必炸的替身，原样未动的那几条仍通过。
    # 这一格测的是**签注快照**那一层：同一份看板上一次刚查过，不先移开整板缓存的话这次连解析都不做，
    # 替身一次都不会被调用，下面的断言就证不出「通过的那几条没被重算」
    real = sg.recompute
    sg.recompute = lambda *_a, **_k: (None, "替身：不该被调用")
    try:
        _drop_board_cache(root)
        probs, notes, _ = sg.guard(root, write_board(root, [good, bad, fixed]))
        check("通过的签注原样未动 → 不再重算", probs, [])
        # 存量违规那条（TASK-002）不进缓存、每次照样重算——替身只该在它身上被调用
        check("且通过的两条确实没被重算（替身只出现在存量违规那条上）",
              sorted({n.split(":")[0].split("）")[-1] for n in notes if "替身" in n}), ["TASK-002"])
        newer = mk_task(mk_signoff(h["base"], h["clean"]), tid="TASK-004")
        probs, _, _ = sg.guard(root, write_board(root, [good, bad, fixed, newer]))
        check_any("新签注照样重算（替身被调用 → 验不了 → 拦）", "TASK-004", probs)
    finally:
        sg.recompute = real

    # 快照版本对不上（指纹口径变过）→ 按首次运行处理：更早完成的存量只报不拦，不被误判成「本次改过」
    snap_p = sg.state_dir_of(root) / sg.SNAPSHOT_NAME
    sg.save_snapshot(root, {"TASK-002": {"h": "旧口径的指纹", "ok": False}})
    data = json.loads(snap_p.read_text(encoding="utf-8"))
    data["version"] = sg.SNAPSHOT_VERSION - 1
    snap_p.write_text(json.dumps(data), encoding="utf-8", newline="")
    probs, notes, _ = sg.guard(root, write_board(root, [good, bad]))
    check("快照版本对不上 → 不误拦原样未动的存量", probs, [])
    check_any("且按首次建快照报出", "首次建快照", notes)

    # 快照写不进去 → 每次都按首次处理，并点名
    real_save = sg.save_snapshot
    sg.save_snapshot = lambda *_a, **_k: "PermissionError: 替身"
    try:
        _reset_snapshot(root)
        probs, notes, _ = sg.guard(root, write_board(root, [good, new_bad]))
        check("快照写不进去 → 只报不拦（已登记的 fail-open）", probs, [])
        check_any("快照写不进去 → stderr 点名", "快照写不进去", notes)
    finally:
        sg.save_snapshot = real_save


# ---------- 4b. 整板缓存：看板没变就不解析，但查出的问题照报 ----------


def test_board_cache(tmp):
    """整板缓存的命中 / 失效 / 不进缓存三类形态。每格同时钉两件事：解析调没调（`_CountParse`）、
    结论对不对——只钉前者的话，「命中后一律返回空」也能全绿。"""
    print("[整板缓存] 未变不解析 / 问题照报 / 按内容失效 / 不缓存首次与验不了的结论")
    root = tmp / "cache"
    root.mkdir()
    h = build_project(root)
    good = mk_task(mk_signoff(h["base"], h["clean"]), tid="TASK-001")
    bad = mk_task(mk_signoff(h["clean"], h["veto"]), tid="TASK-002")
    snap_p = sg.state_dir_of(root) / sg.SNAPSHOT_NAME
    real_today = sg._today
    sg._today = lambda: "2026-12-31"   # 夹具完成日早于「今天」→ 首次运行时都算存量
    try:
        _reset_snapshot(root)
        with _CountParse() as c:
            board = write_board(root, [good, bad])
            sg.guard(root, board)
            check("首次建快照：不写整板缓存（下一次的口径是「存量」不是「首次」）",
                  "board" in _snap_doc(root), False)
            p1, n1, _ = sg.guard(root, board)
            check("第二次完整检查之后写入整板缓存", "board" in _snap_doc(root), True)
            check("两次都解析了", c.n, 2)
            p2, n2, s2 = sg.guard(root, write_board(root, [good, bad]))   # 同内容重写
            check("看板字节没变 → 不解析", c.n, 2)
            check("命中时存量问题照报（与完整检查逐字相同）", (p2, n2), (p1, n1))
            check_any("且确实报的是那条存量违规", "TASK-002", n2)
            check("命中不算「本次通过」（不重复写事件）", s2, [])
    finally:
        sg._today = real_today

    # 本次新出现的问题：命中缓存也照报、Edit 侧照拦
    new_bad = mk_task(mk_signoff(h["clean"], h["veto"]), tid="TASK-003")
    with _CountParse() as c:
        board = write_board(root, [good, bad, new_bad])
        p1, _, _ = sg.guard(root, board)
        p2, _, _ = sg.guard(root, board)
        check("新问题：第一次解析、第二次命中", c.n, 1)
        check_any("命中时「本次改出来的」问题照报——不把查过但没过的当通过", "TASK-003", p2)
        check("且与完整检查逐字相同", p2, p1)
        edit = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(board)}})
        bash = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        rc, err = _main_with_stdin(edit, root)
        check("命中时 Edit 侧照拦（exit 2）", rc, 2)
        rc, err = _main_with_stdin(bash, root)
        check("命中时 Bash 侧照报不拦", (rc, "本次未阻断" in err), (0, True))
        check("main 两次都走缓存", c.n, 1)

    # 按内容失效，不按 mtime：同尺寸改一个字节、把 mtime 拨回去，照样重新解析
    with _CountParse() as c:
        text = board.read_text(encoding="utf-8")
        _w(board, text + "# aa" + NL)
        sg.guard(root, board)
        st = board.stat()
        _w(board, text + "# ab" + NL)
        os.utime(board, ns=(st.st_atime_ns, st.st_mtime_ns))
        check("前提：两版同尺寸、同 mtime", (board.stat().st_size, board.stat().st_mtime_ns),
              (st.st_size, st.st_mtime_ns))
        sg.guard(root, board)
        check("同尺寸同 mtime、内容不同 → 重新解析", c.n, 2)

    # 配置变 → 失效（自签开关、看板位置都在配置里）
    with _CountParse() as c:
        sg.guard(root, board)
        check("前提：未变时命中", c.n, 0)
        cfg = root / ".workframe-config.json"
        cfg_raw = cfg.read_text(encoding="utf-8")
        _w(cfg, cfg_raw.rstrip() + " " + NL)
        sg.guard(root, board)
        check("`.workframe-config.json` 内容变了 → 重新解析", c.n, 1)

    # 规则文件变 → 失效：把规则目录换成一份副本，改副本的一个字节
    rules = tmp / "rules"
    rules.mkdir()
    for n in sg._RULE_FILES:
        shutil.copy2(SCRIPTS_DIR / n, rules / n)
    check("规则摘要可复现（同内容同摘要）", sg._context_key(root, rules), sg._context_key(root))
    real_dir = sg._SCRIPTS_DIR
    try:
        with _CountParse() as c:
            sg._SCRIPTS_DIR = str(rules)
            sg.guard(root, board)
            check("前提：规则未变时命中", c.n, 0)
            for n in sg._RULE_FILES:
                with open(rules / n, "ab") as f:
                    f.write(b"\n# probe\n")
                sg.guard(root, board)
                sg.guard(root, board)
            check(f"{len(sg._RULE_FILES)} 份规则文件各改一次 → 各重新解析一次（之后又命中）",
                  c.n, len(sg._RULE_FILES))
            # 规则变了只作废整板缓存：已通过、原样未动的签注不重判（不追溯，抬头 §整板缓存）
            real_rc = sg.recompute
            called = []
            sg.recompute = lambda _pd, blk: (called.append(blk.get("since")), real_rc(_pd, blk))[1]
            try:
                with open(rules / sg._RULE_FILES[0], "ab") as f:
                    f.write(b"# probe 2\n")
                sg.guard(root, board)
            finally:
                sg.recompute = real_rc
            check("（前提：这次确实做了完整检查——没通过的那几条被重算了）", h["clean"] in called, True)
            check("规则变后重新解析，但已通过的签注（TASK-001）不重算", h["base"] in called, False)
    finally:
        sg._SCRIPTS_DIR = real_dir

    # 验不了的结论不进缓存：hash 解析不到，补拉之后结论会变
    with _CountParse() as c:
        sg.guard(root, write_board(root, [good, mk_task(mk_signoff("deadbeef", h["clean"]), tid="TASK-004")]))
        check("重算跑不起来 → 不写整板缓存", "board" in _snap_doc(root), False)
        p, _, _ = sg.guard(root, board)
        p, _, _ = sg.guard(root, write_board(root, [good, mk_task(mk_signoff("deadbeef", h["clean"]), tid="TASK-004")]))
        check_any("且下一次照样完整检查、照样报", "TASK-004", p)
        check("三次都解析了", c.n, 3)

    # 坏缓存：形态不对一律按未命中，不抛
    board = write_board(root, [good, bad])
    ref = sg.guard(root, board)[:2]      # 完整检查的结论，坏缓存之后必须逐字回到它
    sg.guard(root, board)
    for label, junk in (("不是映射", "garbage"), ("problems 不是列表", {"problems": "x"}),
                        ("条目不是字符串", {"problems": [1], "notes": []})):
        data = _snap_doc(root)
        data["board"] = dict(data["board"], **junk) if isinstance(junk, dict) else junk
        _w(snap_p, json.dumps(data, ensure_ascii=False) + NL)
        with _CountParse() as c:
            try:
                p, n, _ = sg.guard(root, board)
                got = (c.n, (p, n))
            except Exception as e:
                got = f"抛 {type(e).__name__}"
            check(f"坏缓存（{label}）→ 重新解析、结论与完整检查相同", got, (1, ref))
    check_any("（前提：参照结论非空，上面比的不是两个空列表）", "TASK-002", ref[0] + ref[1])

    # 看板解析不了：照旧只报「跳过」，也不写缓存
    _w(board, "tasks: [" + NL)
    with _CountParse() as c:
        _, n, _ = sg.guard(root, board)
        _, n, _ = sg.guard(root, board)
        check_any("坏看板 → 跳过并点名", "跳过", n)
        check("坏看板不进缓存（每次都重新解析）", c.n, 2)


# ---------- 5. 开关 / Bash / 事件 / 内部错误 ----------


def test_enable_switch(tmp):
    """**缺键即开启**（默认 true）；显式 false 才关，关了写「自理」必拒，「独立验证」照常。"""
    print("[开关] 缺键即开启 / 显式 false 才关")
    root = tmp / "off"
    root.mkdir()
    h = build_project(root, enabled=False)
    cfg = root / ".workframe-config.json"
    base = json.loads(cfg.read_text(encoding="utf-8"))
    base.pop("signoff", None)
    _w(cfg, json.dumps(base, ensure_ascii=False) + NL)
    check("缺 signoff 键 → 开启（默认 true）", sg.self_signoff_enabled(root), True)
    check("默认值常量与实现同源（不是两处手写）",
          sg.self_signoff_enabled(root), sg.SELF_SIGNOFF_DEFAULT)

    _w(cfg, json.dumps({**base, "signoff": {"self_signoff_enabled": False}},
                       ensure_ascii=False) + NL)
    check("显式 false → 关闭", sg.self_signoff_enabled(root), False)
    probs = sg.judge(root, "T", mk_task(mk_signoff(h["base"], h["clean"])), False)
    check_any("关闭时写自理 → 拒", "显式关闭", probs)
    check_any("红灯指向配置，不是让人改类别", "不要改这一行类别", probs)
    check("关闭时写独立验证 → 照常通过",
          sg.judge(root, "T", mk_task(mk_signoff(h["base"], h["clean"], mode="独立验证")), False), [])
    check_any("关闭时写你拍板但没写验证方 → 拒（拍板不免除独立验证）", "verifier",
              sg.judge(root, "T", mk_task(mk_signoff(h["base"], h["clean"], mode="你拍板")), False))
    check("关闭时你拍板 ＋ 验证方 → 通过", sg.judge(root, "T", mk_task(mk_signoff(
        h["base"], h["clean"], mode="你拍板",
        verifier={"who": "@qa", "report": "r.md"})), False), [])
    check("开启时你拍板不要求验证方", sg.judge(root, "T", mk_task(mk_signoff(
        h["base"], h["clean"], mode="你拍板")), True), [])

    # 关开关不追溯：开启时通过并进快照的自理签注，关闭之后原样未动 → 不重判
    _w(cfg, json.dumps({**base, "signoff": {"self_signoff_enabled": True}}, ensure_ascii=False) + NL)
    _reset_snapshot(root)
    sg.save_snapshot(root, {})
    self_task = mk_task(mk_signoff(h["base"], h["clean"]))
    probs, _, _ = sg.guard(root, write_board(root, self_task))
    check("开启时自理签注通过", probs, [])
    _w(cfg, json.dumps({**base, "signoff": {"self_signoff_enabled": False}}, ensure_ascii=False) + NL)
    probs, _, _ = sg.guard(root, write_board(root, self_task))
    check("关掉开关后，原样未动的旧自理签注不被追溯拦截", probs, [])
    probs, _, _ = sg.guard(root, write_board(root, mk_task(mk_signoff(h["base"], h["clean"]), tid="TASK-009")))
    check_any("关掉开关后，新写的自理签注照拦", "显式关闭", probs)


def test_enable_switch_non_mapping(tmp):
    """`signoff` 非映射时：**不抛、不 fail-open，落回默认值并在 stderr 点名**。"""
    print("[开关] signoff 非映射 → 落回默认并点名，不抛不 fail-open")
    root = tmp / "shape"
    root.mkdir()
    build_project(root)
    cfg = root / ".workframe-config.json"
    base = json.loads(cfg.read_text(encoding="utf-8"))
    import contextlib
    import io as _io
    for label, val in (("字符串", "yes"), ("数组", ["a"]), ("数字", 1)):
        base["signoff"] = val
        _w(cfg, json.dumps(base, ensure_ascii=False) + NL)
        err = _io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                got = sg.self_signoff_enabled(root)
        except Exception as e:
            got = f"抛 {type(e).__name__} → main() 会 fail-open 放行"
        check(f"signoff 是{label} → 落回默认（不抛、不 fail-open）", got, sg.SELF_SIGNOFF_DEFAULT)
        check_any(f"signoff 是{label} → stderr 点名 misconfiguration", "不是映射", [err.getvalue()])
    base["signoff"] = {"self_signoff_enabled": False}
    _w(cfg, json.dumps(base, ensure_ascii=False) + NL)
    check("正常映射读得出 false（上面三条不是把开关判死）", sg.self_signoff_enabled(root), False)


def test_main_paths(root, h):
    print("[main] Edit 拦 / Bash 只报 / 通过不写事件 / 非看板放行")
    board = write_board(root, [])
    edit = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(board)}})
    bash = json.dumps({"tool_name": "Bash", "tool_input": {"command": "python x.py"}})

    _seed_snapshot(root)
    write_board(root, mk_task(mk_signoff(h["clean"], h["veto"])))
    rc, err = _main_with_stdin(edit, root)
    check("Edit 侧：本次新出现的违规 → exit 2（拦）", rc, 2)
    check_any("且红灯点名否决项", "V1", [err])
    rc, err = _main_with_stdin(bash, root)
    check("Bash 侧：同一份违规看板 → exit 0（**只报不拦**）", rc, 0)
    check_any("且 stderr 报出", "本次未阻断", [err])

    # Codex 的 apply_patch：载荷没有 file_path，被改路径只在补丁头里
    def patch(head, cwd=None):
        p = {"tool_name": "apply_patch",
             "tool_input": {"command": "*** Begin Patch" + NL + head + NL + "@@" + NL + "*** End Patch" + NL}}
        if cwd is not None:
            p["cwd"] = str(cwd)
        return json.dumps(p)
    rc, err = _main_with_stdin(patch(f"*** Update File: {board}"), root)
    check("apply_patch 补丁头是看板绝对路径 → exit 2（与 Edit 同样拦）", rc, 2)
    check_any("且红灯点名否决项", "V1", [err])
    # cwd 取子目录才分辨得出「按 cwd 解析」与「忽略 cwd、按项目根解析」：cwd 等于项目根时两者同形
    sub = root / "docs"
    sub.mkdir(exist_ok=True)
    rel = patch("*** Update File: ../projects/board.yaml", cwd=sub)
    check("apply_patch 相对路径按载荷 cwd 解析（cwd=子目录、../ 指回看板）→ exit 2", _main_with_stdin(rel, root)[0], 2)
    rel_wrong = patch("*** Update File: projects/board.yaml", cwd=sub)
    check("同一相对路径在子目录 cwd 下指的是别的文件 → 放行（没按项目根解析）", _main_with_stdin(rel_wrong, root)[0], 0)
    rel_nocwd = patch("*** Update File: projects/board.yaml")
    check("载荷没有 cwd → 按项目根解析 → exit 2", _main_with_stdin(rel_nocwd, root)[0], 2)
    multi = patch(f"*** Add File: {root / 'docs/x.md'}" + NL + "+x" + NL + f"*** Update File: {board}")
    check("一次补丁改多个文件、看板不是第一个 → exit 2", _main_with_stdin(multi, root)[0], 2)
    moved = patch(f"*** Update File: {root / 'docs/a.md'}" + NL + f"*** Move to: {board}")
    check("移动的目标是看板 → exit 2（Move to 也算被改路径）", _main_with_stdin(moved, root)[0], 2)
    elsewhere = patch(f"*** Update File: {root / 'docs/plain.md'}")
    check("apply_patch 没改到看板 → 放行", _main_with_stdin(elsewhere, root)[0], 0)
    lookalike = json.dumps({"tool_name": "apply_patch", "tool_input": {"file_path": str(board)}})
    check("apply_patch 载荷里塞 file_path 而补丁头为空 → 放行（只认补丁头）", _main_with_stdin(lookalike, root)[0], 0)

    ev = sg.state_dir_of(root) / "events.jsonl"
    before = ev.read_text(encoding="utf-8").count('"self_signoff"') if ev.exists() else 0
    write_board(root, mk_task(mk_signoff(h["base"], h["clean"], mode="独立验证")))
    rc, err = _main_with_stdin(edit, root)
    check("Edit 侧：改成合规 → exit 0", rc, 0)
    after = ev.read_text(encoding="utf-8") if ev.exists() else ""
    check("通过时不写 self_signoff 事件（只记成功的流水已停写）", after.count('"self_signoff"') - before, 0)
    # 正向对照：同一份签注换一个类别再写，守卫确实判了「通过」（而不是根本没判）——passed 里有它
    write_board(root, mk_task(mk_signoff(h["base"], h["clean"], mode="你拍板")))
    problems, _notes, passed = sg.guard(root, board)
    check("同形签注改过一次 → guard 判通过并在 passed 里点名", ([p[1] for p in passed], problems), (["你拍板"], []))
    rc, _ = _main_with_stdin(edit, root)
    check("原样未动再写一次 → 仍不写事件",
          (ev.read_text(encoding="utf-8") if ev.exists() else "").count('"self_signoff"') - before, 0)

    other = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": str(root / "docs/plain.md")}})
    check("写入对象不是看板 → 放行", _main_with_stdin(other, root)[0], 0)

    bp = root / "projects/board.yaml"
    _w(bp, "tasks:" + NL + "  - id: T" + NL + "    qa_signoff: \"散文\"" + NL)
    check("前置过滤：散文形态 → 不触发完整解析（Bash 侧看不见散文违规，已登记）",
          sg.board_may_have_mapping_signoff(bp), False)
    _w(bp, "tasks:" + NL + "  - id: T" + NL + "    qa_signoff:" + NL + "      mode: 自理" + NL)
    check("前置过滤：映射形态 → 放行到完整解析", sg.board_may_have_mapping_signoff(bp), True)


def test_internal_error_leaves_trace(tmp):
    """hook 内部出错时，`signoff_guard_error` 必须真的落盘——钉计数，不钉 stderr。"""
    print("[内部错误] 出错要留痕，不能静默")
    root = tmp / "err"
    root.mkdir()
    build_project(root)
    ev = sg.state_dir_of(root) / "events.jsonl"
    ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text("", encoding="utf-8", newline="")
    sg.append_event(root, "signoff_guard_error", error="RuntimeError: probe")
    lines = [l for l in ev.read_text(encoding="utf-8").splitlines() if "signoff_guard_error" in l]
    check("内部错误事件真的落盘（钉计数，不钉 stderr）", len(lines), 1)
    rec = json.loads(lines[-1]) if lines else {}
    check("事件含 schema 标 required 的 ts", "ts" in rec, True)
    check("事件 type 正确", rec.get("type"), "signoff_guard_error")
    check("事件带 harness（由 event_json 统一盖章）", "harness" in rec, True)


def main():
    print(f"[test] signoff-guard.py @ {SCRIPTS_DIR / 'signoff-guard.py'}")
    if shutil.which("git") is None:
        print("  ! 环境无 git，整套跳过（本套件全部用例都要真实 git 仓）")
        return 0
    try:
        import yaml  # noqa: F401
    except Exception:
        print("  ! 环境无 PyYAML，整套跳过——**这不是通过**")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="wf-guard-test-"))
    try:
        root = tmp / "proj"
        root.mkdir()
        h = build_project(root)
        test_schema_layer(h)
        test_scope(h)
        test_recompute(root, h)
        test_pending_and_multirepo(tmp)
        test_snapshot(root, h)
        test_board_cache(tmp)
        test_main_paths(root, h)
        test_enable_switch(tmp)
        test_enable_switch_non_mapping(tmp)
        test_internal_error_leaves_trace(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"✗ {len(FAILURES)} 个用例失败：")
        for f in FAILURES:
            print(f"    - {f}")
        return 1
    print("✓ All signoff-guard.py tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
