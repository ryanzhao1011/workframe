#!/usr/bin/env python3
"""单元测试 — plugins/core/scripts/signoff_tier_check.py。

**这个脚本自己就是「判断别的东西对不对」的东西**，改它属于「需要独立验证」。本文件是
「用它自己的失败方式打一遍」的**常驻载体**：判定分支的复现步骤留在这里，而不是每轮重搭
一套跑完即清的沙盒探针（同一套东西已经被重建过两次，成本是实测的）。

覆盖：
  - `structured_kind()` 形态矩阵：无序 / **有序（`N.` `N)` 多位数）** / 表格行 / 各类
    不该命中的近似形态；裸 `-` `*` `+` 三态
  - **切片声明**（合成双仓 fixture：根仓 ignore 一个嵌套 git 仓）：只收十六进制 commit、
    按仓给两端、仓必须是仓根、两端仓集合一致、未提交改动不在切片内、缺省终点 = HEAD
  - V1 结构化条目删除：有序 / 无序 / 重排不命中 / 普通行不误报
  - V1 同键改写：`entry_key()` 形态矩阵；三种键原地改写不命中且逐条可见；删带键行、
    同键按个数抵、逐字挪动的那条不许拿来抵、同键新行在隔开的另一处、逐字相同的两条删掉
    只补回一条、标签被改、分隔行 / 纯序号首格 / 有序项 / 无标签项改写仍命中
  - `pending` / `conditional` 不得恒真；V6 路径命中必须给出可机读信号
  - 否决项 6 的两张表（封顶集 vs auto-update 集）
  - **三条「按用户拍板保持现状」的锁定用例**（`--size-cap` 可被抬掉、V6 路径命中不封顶、
    V1 同 hunk 同键替换只提示）
  - **一条「已登记未修」的现状用例**（diff 解析器吞掉 `-- ` 开头的删除行）

**锁定用例与现状用例报红时，先想「是不是有人改了一个当初由用户拍板或明确推迟的行为」**，
再想是不是缺陷——它们钉的是决定，不是正确性。改它们要回到那个决定本身。

执行：
    python tools/test_signoff_tier_check.py
退出 0 = 全部通过；非 0 = 有失败用例。
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 两条流成对包 UTF-8：中文红灯文案在 cp936 控制台上会让报错路径二次崩溃。
# 用 `reconfigure` 不新建 TextIOWrapper——下面 import 的被测模块自己也包一层，
# 两个 wrapper 抢同一个 buffer 会在回收时把 buffer 关掉。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "plugins" / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import signoff_tier_check as stc  # noqa: E402  文件名无连字符，可直接 import
from _state_io import new_rel  # noqa: E402

STATE = new_rel("state")

FAILURES = []


def check(name, got, want, note=""):
    if got == want:
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name}: got={got!r} want={want!r} {note}")
        FAILURES.append(name)


def check_in(name, needle, hay):
    if needle in (hay or ""):
        print(f"  ✓ {name}")
    else:
        print(f"  ✗ {name}: 文案不含 {needle!r}")
        FAILURES.append(name)


# ---------- fixture ----------


def _git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} @ {repo}: {r.stderr[:200]}")
    return r.stdout.strip()


def _w(path, text):
    """一律二进制写：文本模式在 Windows 上把 LF 翻成 CRLF，fixture 的行数会漂。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _onerr(func, path, _exc):
    try:
        os.chmod(path, 0o700)
        func(path)
    except Exception:
        pass


# 有序（两种记法）与无序并存，用于 V1 的形态矩阵
STEPS = "# steps\n\n1. alpha\n2. beta\n3. gamma\n\n1) x\n2) y\n\n- keep\n- drop\n"
# 带键条目，用于 V1 的「改写 vs 删除」矩阵：表格首格、`**标签**`、`` `代码段` `` 三种键，
# 外加一对同键行（dup）——同键按个数抵、逐字挪动的那条不许被拿来抵
KEYED = ("# keyed\n\n"
         "| 文件 | 说明 |\n|---|---|\n"
         "| `a.py` | v1 |\n| `b.py` | v1 |\n| dup | 1 |\n| mid | m |\n| dup | 2 |\n\n"
         "- **最近同步**：2026-01-01（基准 commit aaaaaaaa）\n"
         "- `x.md`：v1\n\n"
         # 纯序号首格的表（分隔行换一种写法，免得与上一张表的被同一次 replace 改到）
         "| # | 步骤 |\n|--|--|\n| 1 | one |\n\n"
         # 两条逐字相同的行：逐字匹配必须按个数抵
         "| same | x |\n| same | x |\n")
BIG = "".join(f"line {i}\n" for i in range(1, 201))


def build_fixture(root):
    """根仓 + 一个被根仓 ignore 的嵌套 git 仓（本框架的真实拓扑）。"""
    _w(root / ".workframe-config.json", '{"project_name": "fixture"}\n')
    _w(root / ".gitignore", "nested/\n")
    _w(root / "CLAUDE.md", "# claude\n\ncontent v1\n")
    _w(root / "AGENTS.md", "# agents\n\ncontent v1\n")
    _w(root / ".claude/agents/dev.md", "# dev\n\ncontent v1\n")
    # 否决项 6 的**排除项**，各一份——排除是靠断言守住的，不是靠没人去改。
    # 运行态目录是「机器写的派生物不得进封顶集」这条性质的活体对象
    _w(root / STATE / "inject-log.jsonl", "{}\n")
    _w(root / ".claude/skills/s1/SKILL.md", "# s1\n\ncontent v1\n")
    _w(root / "projects/proposals/p1.md", "# p1\n\ncontent v1\n")
    _w(root / "docs/plain.md", "# plain\n\nline v1\n")
    nested = root / "nested"
    _w(nested / "steps.md", STEPS)
    _w(nested / "keyed.md", KEYED)
    _w(nested / "CHANGELOG.md", "# Changelog\n\n## [1.0.0] - 2026-01-01\n\nreleased\n")
    _w(nested / "big.txt", BIG)
    for repo in (nested, root):
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "fixture@example.invalid")
        _git(repo, "config", "user.name", "fixture")
        _git(repo, "config", "core.autocrlf", "false")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
    return {".": _git(root, "rev-parse", "HEAD"), "nested": _git(nested, "rev-parse", "HEAD")}


def commit_all(repo, msg="change"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg, "--allow-empty")
    return _git(repo, "rev-parse", "HEAD")


def reset(root, base):
    """把两个仓都退回基线：每个用例从同一个状态起跑，互不污染。"""
    for label, ref in base.items():
        repo = root if label == "." else root / label
        _git(repo, "reset", "-q", "--hard", ref)
        _git(repo, "clean", "-qfd")


def refs(d):
    return ",".join(f"{k}={v}" for k, v in d.items())


def run_cli(root, argv):
    """进程内跑一次判定，返回 (rc, stdout, stderr, parsed_json_or_None)。"""
    full = ["--project-dir", str(root)] + list(argv)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = stc.main(full)
    except SystemExit as e:          # argparse 用法错误
        rc = e.code if isinstance(e.code, int) else 2
    data = None
    if "--json" in full and rc == 0:
        try:
            data = json.loads(out.getvalue())
        except Exception:
            data = None
    return rc, out.getvalue(), err.getvalue(), data


def run_slice(root, base, *more):
    """以两仓基线为起点、当前 HEAD 为终点判一次。"""
    return run_cli(root, ["--json", "--since", refs(base)] + list(more))


# ---------- 单元：形态矩阵 ----------


def test_structured_kind():
    print("[structured_kind] 形态矩阵")
    ORDERED = "有序列表项"
    UNORDERED = "列表项/YAML 序列项"
    ROW = "表格行"
    cases = [
        ("1. x", ORDERED), ("1) x", ORDERED), ("12. x", ORDERED),
        ("0. zero", ORDERED), ("  3. 缩进", ORDERED),
        ("1.x", None), ("1.5 不是列表", None), ("1.", None), ("1. ", None),
        ("1、中文顿号", None), ("a. 字母序号", None), ("(1) 括号序号", None),
        ("①圈码", None), ("v1. 不是数字开头", None),
        ("- x", UNORDERED), ("* x", UNORDERED), ("+ x", UNORDERED),
        ("  - 缩进无序项", UNORDERED), ("- [ ] 任务项", UNORDERED),
        ("| a | b |", ROW), ("|---|---|", ROW), (" | a | b | ", ROW),
        ("-", None), ("*", None), ("+", None),
        ("", None), ("   ", None), ("text - 中间的横杠", None), ("*bold*", None),
    ]
    for src, want in cases:
        check(f"structured_kind({src!r})", stc.structured_kind(src), want)


def test_entry_key():
    print("[entry_key] 条目身份")
    ROW = "表格行"
    UNORDERED = "列表项/YAML 序列项"
    cases = [
        ("| `a.py` | 说明 |", (ROW, "`a.py`")), (" | k | v | ", (ROW, "k")),
        ("|---|---|", None), ("|:--|--:|", None), ("|  | 空首格 |", None),
        ("| 1 | 序号 |", None), ("| 12 | 序号 |", None), ("| # | 序号 |", None),
        ("| 1a | 不是纯序号 |", (ROW, "1a")),
        ("- **最近同步**：2026", (UNORDERED, "最近同步")), ("* **L** x", (UNORDERED, "L")),
        ("- `x.md`：v1", (UNORDERED, "x.md")), ("  - **缩进** y", (UNORDERED, "缩进")),
        ("- 没有标签", None), ("- 中间有 **加粗** 不算", None), ("- **", None),
        ("1. **有序项有标签也无键**", None), ("2) `x`", None),
        ("普通行", None), ("", None),
    ]
    for src, want in cases:
        check(f"entry_key({src!r})", stc.entry_key(src), want)


# ---------- 切片声明 ----------


def test_slice_declaration(root, base):
    """切片两端钉死、按仓给——这是整条重算链路的承重面。

    每条拒绝都要真的走到拒绝分支：钉退出码 ＋ 红灯点名的原因，不钉「有输出」这种宽形状。
    """
    print("[切片] 只收十六进制 commit、按仓、仓根、两端一致、只含已提交")
    rc, _, err, _ = run_cli(root, ["--json", "--since", "HEAD"])
    check("since=HEAD → 拒绝判定（符号引用）", rc, 1)
    check_in("红灯点名「十六进制」", "十六进制", err)
    rc, _, err, _ = run_cli(root, ["--json", "--since", base["."], "--diff-ref", "master"])
    check("diff-ref=分支名 → 拒绝判定", rc, 1)
    rc, _, err, _ = run_cli(root, ["--json", "--since", "deadbeef"])
    check("hash 解析不到 → 拒绝判定", rc, 1)
    check_in("红灯点名「解析不到」", "解析不到", err)
    rc, _, err, _ = run_cli(root, ["--json", "--since", f"nope={base['.']}"])
    check("声明的仓不存在 → 拒绝判定", rc, 1)
    rc, _, err, _ = run_cli(root, ["--json", "--since", f"docs={base['.']}"])
    check("声明的目录不是仓根（会被外层仓接管）→ 拒绝判定", rc, 1)
    check_in("红灯点名「不是一个 git 仓的根目录」", "根目录", err)
    rc, _, err, _ = run_cli(root, ["--json", "--since", refs(base),
                                   "--diff-ref", f".={base['.']}"])
    check("两端仓集合不一致 → 拒绝判定", rc, 1)
    check_in("红灯点名「不一致」", "不一致", err)

    rc, _, _, d = run_slice(root, base)
    check("零改动切片 → 判定产出", rc, 0)
    check("缺省终点 = 各仓 HEAD（嵌套仓也被取到）", d and sorted(d["slice"]), [".", "nested"])
    check("单个 hash = 根仓", stc._cli_refs(base["."]), base["."])
    check("LABEL=hash 解析", stc._cli_refs(refs(base)), base)

    # 未提交改动不在切片里：工作区删一条列表项而不提交，判定看不见它
    steps = root / "nested" / "steps.md"
    steps.write_bytes(STEPS.replace("- drop\n", "").encode("utf-8"))
    _, _, _, d = run_slice(root, base)
    check("未提交的删除不进切片（能力边界第 3 条）", d and d["v1_hits"], [])
    commit_all(root / "nested")
    _, _, _, d = run_slice(root, base)
    check("提交之后同一处删除进切片", bool(d and d["v1_hits"]), True)
    reset(root, base)


def test_v1_ordered_list(root, base):
    """有序列表项属于方案原文的「列表项」，删掉编号步骤中的一步必须命中 V1。"""
    print("[V1] 结构化条目删除")
    steps = root / "nested" / "steps.md"
    for label, mutated, want_kind in [
        ("删有序项 `2. beta`", STEPS.replace("2. beta\n", ""), "有序列表项"),
        ("删有序项 `2) y`", STEPS.replace("2) y\n", ""), "有序列表项"),
        ("删无序项 `- drop`", STEPS.replace("- drop\n", ""), "列表项/YAML 序列项"),
    ]:
        steps.write_bytes(mutated.encode("utf-8"))
        commit_all(root / "nested")
        _, _, _, d = run_slice(root, base)
        check(f"{label} → 交用户拍板", d and d["needs_user"], True)
        check(f"{label} 的类别", d and d["v1_hits"] and d["v1_hits"][0]["kind"], want_kind)
        reset(root, base)

    steps.write_bytes(STEPS.replace("1. alpha\n2. beta\n", "2. beta\n1. alpha\n").encode("utf-8"))
    commit_all(root / "nested")
    _, _, _, d = run_slice(root, base)
    check("有序项原样重排不命中", d and d["v1_hits"], [])
    check("重排不交用户", d and d["needs_user"], False)
    reset(root, base)

    (root / "docs" / "plain.md").write_bytes(b"# plain\n\nline v2\n")
    commit_all(root)
    _, _, _, d = run_slice(root, base)
    check("普通正文行改动不误报", d and d["v1_hits"], [])
    reset(root, base)


def test_v1_rewrite_by_key(root, base):
    """V1 防的是条目消失：同键改写不命中、条目真没了照样命中，两边各打到每条分支。"""
    print("[V1] 同键改写 vs 删除")
    keyed = root / "nested" / "keyed.md"

    def mutate(text):
        keyed.write_bytes(text.encode("utf-8"))
        commit_all(root / "nested")
        return run_slice(root, base)[3]

    # 放行面：三种键各改写一处，同一切片
    d = mutate(KEYED.replace("| `a.py` | v1 |", "| `a.py` | v2 改写 |")
               .replace("aaaaaaaa）", "bbbbbbbb）——附注")
               .replace("- `x.md`：v1", "- `x.md`：v2"))
    check("三种键原地改写 → V1 不命中", d and d["v1_hits"], [])
    check("改写不交用户", d and d["needs_user"], False)
    check("改写不产生待裁决", d and d["pending"], [])
    check("被抵掉的逐条可机读（键）", d and sorted(r["key"] for r in d["v1_rewrites"]),
          ["`a.py`", "x.md", "最近同步"])
    _, text, _, _ = run_cli(root, ["--since", refs(base)])
    check_in("人读那份说明了放行处数", "按同键改写放行 3 处", text)
    reset(root, base)

    # 拦截面：每一格都必须仍然命中，且命中的是那一条
    for label, mutated, want_sample in [
        ("删掉带键表格行", KEYED.replace("| `b.py` | v1 |\n", ""), "| `b.py` | v1 |"),
        # 两条同键行相邻（同一个 hunk）才打得到「按个数抵」：隔开时 hunk 先把它挡住了
        ("同一 hunk 里同键两条删掉、只改写回一条 → 按个数仍报一处",
         KEYED.replace("| same | x |\n| same | x |\n", "| same | y |\n"), "| same | x |"),
        ("同键一条逐字挪走、一条删掉 → 挪动那条不许拿来抵",
         KEYED.replace("| dup | 1 |\n| mid | m |\n| dup | 2 |\n", "| mid | m |\n| dup | 1 |\n"),
         "| dup | 2 |"),
        ("标签本身被改写（键变了）",
         KEYED.replace("- **最近同步**：", "- **上次同步**："), None),
        # 首格不变才分辨得出「分隔行无键」：首格一变，键本来就对不上
        ("分隔行无键、改写照旧判删除", KEYED.replace("|---|---|", "|---|:--|"), "|---|---|"),
        ("纯序号首格无键、原地改写照旧判删除", KEYED.replace("| 1 | one |", "| 1 | ONE |"),
         "| 1 | one |"),
        # 同键不等于同一条目：删在一处、同键新行补在隔开的另一处，不算原地改写
        ("隔开的两处：删带键行、文件尾补一条同键无关行",
         KEYED.replace("| `a.py` | v1 |\n", "") + "| `a.py` | 无关 |\n", "| `a.py` | v1 |"),
        # 补回的那条放到文件头，diff 才会是「删两条、加一条」而不是「只删一条」
        ("两条逐字相同的删掉、只补回一条 → 净少一条仍命中",
         KEYED.replace("| same | x |\n| same | x |\n", "")
              .replace("# keyed\n\n", "# keyed\n\n| same | x |\n"), "| same | x |"),
    ]:
        d = mutate(mutated)
        hits = (d or {}).get("v1_hits", [])
        check(f"{label} → 命中恰一处", len(hits), 1)
        check(f"{label} → 交用户拍板", d and d["needs_user"], True)
        if want_sample:
            check(f"{label} → 命中的是那一条", hits and hits[0]["sample"], want_sample)
        reset(root, base)

    steps = root / "nested" / "steps.md"
    for label, mutated in [
        ("有序项无键：原地改写仍判删除", STEPS.replace("2. beta\n", "2. BETA\n")),
        ("无标签列表项：原地改写仍判删除", STEPS.replace("- drop\n", "- dropped\n")),
    ]:
        steps.write_bytes(mutated.encode("utf-8"))
        commit_all(root / "nested")
        _, _, _, d = run_slice(root, base)
        check(label, d and len(d["v1_hits"]), 1)
        reset(root, base)


def test_pending_not_always_true(root, base):
    """`pending` / `conditional` 不得恒真——恒真的标注不携带信息，下游也没法消费。"""
    print("[待裁决] 通道可证伪 + V6 的可机读信号")
    _, _, _, d = run_slice(root, base)
    check("零改动基线 pending 为空", d and d["pending"], [])
    check("零改动基线 conditional 为假", d and d["conditional"], False)
    check("恒有的模型裁量项仍在（没被顺手删掉）", d and len(d["model_review"]), 5)
    _, out_text, _, _ = run_cli(root, ["--since", refs(base)])
    check_in("零改动基线结论为未命中", "机器可判的未命中", out_text.split("\n")[2])
    check_in("恒有裁量项仍整段印给人看", "恒有的模型裁量项", out_text)

    for p in ("AGENTS.md", ".claude/agents/dev.md", "CLAUDE.md"):
        (root / p).write_bytes(b"# x\n\ncontent v2\n")
    commit_all(root)
    _, _, _, d = run_slice(root, base)
    check("V6 路径命中 → conditional 为真", d and d["conditional"], True)
    check("V6 路径命中 → pending 恰一条", d and len(d["pending"]), 1)
    check_in("pending 那条点名路径命中数", "路径命中 3 处", " ".join(d["pending"]) if d else "")
    check("V6 命中的路径清单可机读", d and sorted(d["veto"]["protected"]),
          [".claude/agents/dev.md", "AGENTS.md", "CLAUDE.md"])
    reset(root, base)


def test_accepted_risks_unchanged(root, base):
    """**锁定用例**：两条由用户 2026-09-09 拍板「不修 / 维持现状」的行为，外加一条
    2026-11-03 拍板的（V1 同 hunk 同键替换只提示、不进待裁决）。

    报红不代表发现了缺陷，代表**有人改了一个当初由用户拍板的决定**——回到那个决定，
    别就地改断言。两条都在被测脚本的能力边界里有对应登记。
    """
    print("[锁定] 被知情接受的风险必须保持原状")
    big = root / "nested" / "big.txt"
    big.write_bytes((BIG + "".join(f"extra {i}\n" for i in range(3000))).encode("utf-8"))
    commit_all(root / "nested")
    _, _, _, d = run_slice(root, base)
    check("默认上限下大改动交用户", d and d["needs_user"], True)
    check_in("命中的是尺寸上限", "尺寸上限", " ".join(d["veto"]["hit"]) if d else "")
    _, _, _, d = run_slice(root, base, "--size-cap", "999999999")
    check("上限被抬高后放行（用户拍板：先不管）", d and d["needs_user"], False)
    reset(root, base)

    (root / "CLAUDE.md").write_bytes(b"# claude\n\ncontent v2\n")
    commit_all(root)
    _, _, _, d = run_slice(root, base)
    check("V6 路径命中不直接封顶（进待裁决）", d and d["needs_user"], False)
    check("但路径清单仍可机读", d and d["veto"]["protected"], ["CLAUDE.md"])
    reset(root, base)

    # 2026-11-03 用户拍板：同一 hunk 里同键而内容无关的替换只提示、不进待裁决
    keyed = root / "nested" / "keyed.md"
    keyed.write_bytes(KEYED.replace("| `b.py` | v1 |", "| `b.py` | 毫不相干的新事项 |").encode("utf-8"))
    commit_all(root / "nested")
    _, _, _, d = run_slice(root, base)
    check("同 hunk 同键无关替换：不命中 V1（用户拍板只提示）", d and d["v1_hits"], [])
    check("同 hunk 同键无关替换：不进待裁决", d and d["pending"], [])
    check("同 hunk 同键无关替换：放行明细仍可机读", d and [r["key"] for r in d["v1_rewrites"]],
          ["`b.py`"])
    reset(root, base)


def test_known_limitation_dashdash_swallowed():
    """**现状用例（已登记未修）**：内容本身以 `-- ` 开头的删除行会被当成文件头吞掉。

    **修好之后本用例会报红**——那时把它改成断言修复后的行为，别把断言删掉。
    """
    print("[现状] diff 解析器对 `-- ` / `++ ` 开头内容行的已知缺陷")
    diff = ("diff --git a/q.sql b/q.sql\n"
            "index 111..222 100644\n"
            "--- a/q.sql\n"
            "+++ b/q.sql\n"
            "@@ -1,2 +1,1 @@\n"
            "--- 旧注释\n"
            "-SELECT 1;\n"
            "+SELECT 2;\n")
    files = stc.parse_unified_diff(diff)
    fd = files.get("q.sql")
    check("两条删除行只算到 1 条（少算，登记未修）", fd and fd.deletions, 1)
    check("被吞掉的是 `-- ` 那条", fd and fd.removed, ["SELECT 1;"])


def test_veto6_two_tables(root, base):
    """**否决项 6 的清单是它自己的一张表，不是 `auto-update` 那张。**

    1. **派生物后门**：机器写的派生物若留在封顶集里，它一变就把否决项 6 从后门触发——
       失效方式是**假红**（天天交用户），而假红的下场是这道闸被人绕着走。
    2. **差集不许静默丢掉**：`.claude/skills/**` / `projects/proposals/**` 不封顶，
       但**仍受 `auto-update` 约束**。丢掉它们，读输出的人无从知道自己动了那张表。

    断言钉的是**两个集合各自的成员**，不是「protected 非空」这种宽形状。
    """
    print("[V6-两张表] 封顶集 vs auto-update 集")
    for rel in (f"{STATE}/inject-log.jsonl", ".claude/skills/s1/SKILL.md",
                "projects/proposals/p1.md", "AGENTS.md"):
        (root / rel).write_bytes(b"# t\n\ncontent v2\n")
    commit_all(root)
    _, _, _, d = run_slice(root, base)
    veto = (d or {}).get("veto", {})
    check("封顶集恰为 AGENTS.md 那一条", sorted(veto.get("protected", [])), ["AGENTS.md"])
    check("差集三条都被报出来（不是静默丢掉）", sorted(veto.get("auto_update_only", [])),
          sorted([".claude/skills/s1/SKILL.md", f"{STATE}/inject-log.jsonl",
                  "projects/proposals/p1.md"]))
    _, out_text, _, _ = run_cli(root, ["--since", refs(base)])
    check_in("差集在人读的那份里也说明了两表关系", "真子集", out_text)
    reset(root, base)

    rt = stc.runtime_protected(root)
    v6, au = rt["veto6"], rt["auto_update"]
    for path, in_v6, in_au, why in (
        ("AGENTS.md", True, True, "两扇门共用的项目自有判据落点：人手改 + 每会话必载"),
        (".codex/config.toml", False, True, "另一扇门的项目内配置面：受保护但不封顶"),
        (".claude/agents/x.md", True, True, "角色契约"),
        (".claude/settings.local.json", True, True, "settings*.json"),
        ("CLAUDE.md", True, True, "每会话必载"),
        (".workframe-config.json", True, True, "项目身份与运行档位"),
        (stc.runtime_rel(root, "memory").rstrip("/") + "/shared/MEMORY.md",
         True, True, "最高权威 + 永不清理（路径问 _state_io 现算，不写死）"),
        (stc.runtime_rel(root, "state").rstrip("/") + "/events.jsonl",
         False, True, "运行态：hooks 写的，路径同样现算"),
        (".claude/skills/s/SKILL.md", False, True, "skills：当轮可见"),
        (".agents/skills/s/SKILL.md", False, True, "skills 真实源：链接形态下 git 报的路径"),
        ("projects/proposals/p.md", False, True, "proposals：本身就是待审对象"),
        ("README.md", False, True, "不影响运行"),
        ("LICENSE", False, True, "不影响运行"),
        ("docs/plain.md", False, False, "普通文档，两张表都不在"),
    ):
        check(f"veto6 {path}（{why}）", stc.is_veto6_path(path, v6), in_v6)
        check(f"auto-update {path}", stc.is_auto_update_protected(path, au), in_au)


def main():
    print(f"[test] signoff_tier_check.py @ {SCRIPTS_DIR / 'signoff_tier_check.py'}")
    test_structured_kind()
    test_entry_key()
    test_known_limitation_dashdash_swallowed()
    if shutil.which("git") is None:
        print("  ! 环境无 git，跳过端到端用例（单元用例照跑）")
    else:
        tmp = Path(tempfile.mkdtemp(prefix="wf-signoff-test-"))
        try:
            root = tmp / "proj"
            root.mkdir()
            base = build_fixture(root)
            test_slice_declaration(root, base)
            test_v1_ordered_list(root, base)
            test_v1_rewrite_by_key(root, base)
            test_pending_not_always_true(root, base)
            test_veto6_two_tables(root, base)
            test_accepted_risks_unchanged(root, base)
        finally:
            for _ in range(3):
                if not tmp.exists():
                    break
                shutil.rmtree(tmp, onerror=_onerr)

    print()
    if FAILURES:
        print(f"✗ {len(FAILURES)} 个用例失败：")
        for f in FAILURES:
            print(f"    - {f}")
        return 1
    print("✓ All signoff_tier_check.py tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
