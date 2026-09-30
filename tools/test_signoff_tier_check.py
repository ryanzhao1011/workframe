#!/usr/bin/env python3
"""单元测试 — plugins/core/scripts/signoff_tier_check.py。

**这个脚本自己就是「判断别的东西对不对」的东西**，改它按爆炸半径档 4 处置。本文件是
那一档要求的「用它自己的失败方式打一遍」的**常驻载体**：判定分支的复现步骤留在这里，
而不是每轮重搭一套跑完即清的沙盒探针（同一套东西已经被重建过两次，成本是实测的）。

覆盖：
  - `structured_kind()` 形态矩阵：无序 / **有序（`N.` `N)` 多位数）** / 表格行 / 各类
    不该命中的近似形态；裸 `-` `*` `+` 三态（同时是「删掉那条死分支没改变行为」的证据）
  - 四段闸门端到端（合成双仓 fixture：根仓 ignore 一个嵌套 git 仓）
      * 通行证 × `--task` 五形态 —— **省略 `--task` 不得放行跨任务通行证**
      * V1 结构化条目删除：有序 / 无序 / 重排不命中 / 普通行不误报
      * `pending` / `conditional` 不得恒真；V6 路径命中必须给出可机读信号
  - **承重语义组**：地板表四格逐格（**档 4 降不下去**）+ 默认起点——不是某一轮修的缺陷，
    是整套四段闸门赖以成立的东西；前一版套件只瞄准「这次改对了没有」，这两条被改掉全绿
  - **两条「按用户拍板保持现状」的锁定用例**（`--size-cap` 可被抬掉、V6 路径命中不封顶）
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

# 夹具的状态目录由 `_state_io` 现算、不写死目录名：被测脚本只读新位置，夹具写旧布局的话
# code-paths-index 读不到，半径一律算不出、档位全落「完整验证」
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


INDEX = {"__schema__": "workframe.code-paths-index",
         "built_at": "2026-01-01T00:00:00+00:00",
         "buckets": {"nested": {"nested/**": ["m/s"]}, "docs": {"docs/**": ["m/s"]}}}

# 有序（两种记法）与无序并存，用于 V1 的形态矩阵
STEPS = "# steps\n\n1. alpha\n2. beta\n3. gamma\n\n1) x\n2) y\n\n- keep\n- drop\n"
BIG = "".join(f"line {i}\n" for i in range(1, 201))


def build_fixture(root):
    """根仓 + 一个被根仓 ignore 的嵌套 git 仓（本框架的真实拓扑）。返回根仓 HEAD。"""
    _w(root / ".workframe-config.json", '{"project_name": "fixture"}\n')
    _w(root / ".gitignore", "nested/\n")
    _w(root / "CLAUDE.md", "# claude\n\ncontent v1\n")
    _w(root / "AGENTS.md", "# agents\n\ncontent v1\n")
    _w(root / ".claude/agents/dev.md", "# dev\n\ncontent v1\n")
    # 否决项 6 的**排除项**，各一份——排除是靠断言守住的，不是靠没人去改。
    # 运行态那棵（下面的状态目录）是「机器写的派生物不得进封顶集」这条性质在
    # 活体对象：机器写的派生物里挑一个**没有任何真解析路径消费**的（选错的失效形态是症状出现在三层之外）。
    # 用 inject-log 而不是 code-paths-index：后者会被嵌套仓发现面真解析，
    # 往里写非 JSON 会让整个发现面塌掉、把尺寸与路径类判定连带打成 0（实测踩过）
    _w(root / STATE / "inject-log.jsonl", "{}\n")
    _w(root / ".claude/skills/s1/SKILL.md", "# s1\n\ncontent v1\n")
    _w(root / "projects/proposals/p1.md", "# p1\n\ncontent v1\n")
    _w(root / "docs/plain.md", "# plain\n\nline v1\n")
    _w(root / STATE / "code-paths-index.json",
       json.dumps(INDEX, ensure_ascii=False, indent=2) + "\n")
    nested = root / "nested"
    _w(nested / "steps.md", STEPS)
    _w(nested / "CHANGELOG.md", "# Changelog\n\n## [1.0.0] - 2026-01-01\n\nreleased\n")
    _w(nested / "big.txt", BIG)
    for repo in (nested, root):
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "fixture@example.invalid")
        _git(repo, "config", "user.name", "fixture")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "base")
    return _git(root, "rev-parse", "HEAD")


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


def write_passport(path, task, ref, cross=None):
    body = {"task": task, "round": "第三轮", "covered": "全量", "ref": ref}
    if cross is not None:
        body["cross_task"] = cross
    _w(path, json.dumps(body, ensure_ascii=False))
    return ["--passport", str(path)]


# ---------- 单元：形态矩阵 ----------


def test_structured_kind():
    print("[structured_kind] 形态矩阵")
    ORDERED = "有序列表项"
    UNORDERED = "列表项/YAML 序列项"
    ROW = "表格行"
    cases = [
        # 有序列表：否决项 1 的「列表项」是语义词，两种记法与多位数都要命中
        ("1. x", ORDERED), ("1) x", ORDERED), ("12. x", ORDERED),
        ("0. zero", ORDERED), ("  3. 缩进", ORDERED),
        # 点后没有空白 ⇒ 不是列表项。这一条与上面四条一起，才证明新分支的边界是活的
        ("1.x", None), ("1.5 不是列表", None), ("1.", None), ("1. ", None),
        # 近似形态一律不认
        ("1、中文顿号", None), ("a. 字母序号", None), ("(1) 括号序号", None),
        ("①圈码", None), ("v1. 不是数字开头", None),
        # 无序列表 / YAML 序列项（同形，不分开）
        ("- x", UNORDERED), ("* x", UNORDERED), ("+ x", UNORDERED),
        ("  - 缩进无序项", UNORDERED), ("- [ ] 任务项", UNORDERED),
        # 表格行
        ("| a | b |", ROW), ("|---|---|", ROW), (" | a | b | ", ROW),
        # 裸符号三态：删掉那条 `if s in ("-","*","+"): return None` 死分支后行为不变
        ("-", None), ("*", None), ("+", None),
        # 其余
        ("", None), ("   ", None), ("text - 中间的横杠", None), ("*bold*", None),
    ]
    for src, want in cases:
        check(f"structured_kind({src!r})", stc.structured_kind(src), want)


# ---------- 端到端 ----------


def test_passport_task_matrix(root, tmp):
    """F1：省略 `--task` 时判不出是不是跨任务继承，通行证必须不成立。

    这道门被真实绕过过一次（引用了别的任务的独立结论），流程的目的正是把暗门改明路。
    实现若写成 `if current_task and ...`，少传一个可选参数就把整段校验关掉了，
    **而省略这个参数的人正是降档的受益人**。
    """
    print("[F1] 通行证 × --task 五形态")
    pp = tmp / "pp.json"
    head = _git(root, "rev-parse", "HEAD")
    down = ["--json", "--radius", "1"]

    _, _, _, d = run_cli(root, write_passport(pp, "T-A", head)
                         + down + ["--task", "T-B"])
    check("异任务未声明 cross_task → 不得降档", d and d["tier"], "完整验证")

    _, _, _, d = run_cli(root, write_passport(pp, "T-A", head) + down)
    check("省略 --task → 不得降档", d and d["tier"], "完整验证")
    check_in("省略 --task 的文案点名缺参数", "缺 `--task`",
             " ".join(d["segments"]["passport"]["lines"]) if d else "")

    _, _, _, d = run_cli(root, write_passport(pp, "T-A", head)
                         + down + ["--task", "T-A"])
    check("同任务正向仍可降档", d and d["tier"], "自签")

    _, _, _, d = run_cli(root, write_passport(pp, "T-A", head, cross=True)
                         + down + ["--task", "T-B"])
    check("跨任务已显式声明 → 可降档", d and d["tier"], "自签")

    _, _, _, d = run_cli(root, write_passport(pp, "T-A", head, cross=True) + down)
    check("省略 --task 时写了 cross_task 也不成立", d and d["tier"], "完整验证")


def test_v1_ordered_list(root, tmp):
    """F3：有序列表项属于方案原文的「列表项」，删掉编号步骤中的一步必须命中 V1。"""
    print("[F3] V1 结构化条目删除")
    pp = tmp / "pp.json"
    head = _git(root, "rev-parse", "HEAD")
    down = ["--json", "--radius", "1", "--task", "T"]
    steps = root / "nested" / "steps.md"

    for label, mutated, want_kind in [
        ("删有序项 `2. beta`", STEPS.replace("2. beta\n", ""), "有序列表项"),
        ("删有序项 `2) y`", STEPS.replace("2) y\n", ""), "有序列表项"),
        ("删无序项 `- drop`", STEPS.replace("- drop\n", ""), "列表项/YAML 序列项"),
    ]:
        steps.write_bytes(mutated.encode("utf-8"))
        _, _, _, d = run_cli(root, write_passport(pp, "T", head) + down)
        check(f"{label} → 封顶", d and d["tier"], "你拍板")
        check(f"{label} 的类别", d and d["v1_hits"] and d["v1_hits"][0]["kind"], want_kind)

    # 反面两条：同文件内原样重排、以及普通正文行改动，都不该命中
    steps.write_bytes(STEPS.replace("1. alpha\n2. beta\n",
                                    "2. beta\n1. alpha\n").encode("utf-8"))
    _, _, _, d = run_cli(root, write_passport(pp, "T", head) + down)
    check("有序项原样重排不命中", d and d["v1_hits"], [])

    steps.write_bytes(STEPS.encode("utf-8"))
    (root / "docs" / "plain.md").write_bytes(b"# plain\n\nline v2\n")
    _, _, _, d = run_cli(root, write_passport(pp, "T", head) + down)
    check("普通正文行改动不误报", d and d["v1_hits"], [])
    (root / "docs" / "plain.md").write_bytes(b"# plain\n\nline v1\n")


def test_pending_not_always_true(root, tmp):
    """F4b：`pending` / `conditional` 不得恒真——恒真的标注不携带信息，下游也没法消费。

    与 F4a 有因果：V6 路径命中**不封顶**（用户拍板维持现状），所以待裁决通道是它
    唯一的出口；出口恒真就等于没有出口。
    """
    print("[F4b] 待裁决通道可证伪 + V6 的可机读信号")
    pp = tmp / "pp.json"
    head = _git(root, "rev-parse", "HEAD")
    down = ["--json", "--radius", "1", "--task", "T"]

    _, out, _, d = run_cli(root, ["--json"])
    check("零改动基线 pending 为空", d and d["pending"], [])
    check("零改动基线 conditional 为假", d and d["conditional"], False)
    check("恒有的模型裁量项仍在（没被顺手删掉）", d and len(d["model_review"]), 5)
    _, out_text, _, _ = run_cli(root, [])
    check("零改动基线的结论行不带「暂定」", "暂定" in out_text.split("\n")[2], False)
    check_in("恒有裁量项仍整段印给人看", "恒有的模型裁量项", out_text)

    (root / "AGENTS.md").write_bytes(b"# agents\n\ncontent v2\n")
    (root / ".claude/agents/dev.md").write_bytes(b"# dev\n\ncontent v2\n")
    (root / "CLAUDE.md").write_bytes(b"# claude\n\ncontent v2\n")
    _, _, _, d = run_cli(root, write_passport(pp, "T", head) + down)
    check("V6 路径命中 → conditional 为真", d and d["conditional"], True)
    check("V6 路径命中 → pending 恰一条", d and len(d["pending"]), 1)
    check_in("pending 那条点名路径命中数", "路径命中 3 处", " ".join(d["pending"]) if d else "")
    # 下游（hook）要拿得到结构化的那份，不能只有散文
    check("V6 命中的路径清单可机读", d and sorted(d["segments"]["veto"]["protected"]),
          [".claude/agents/dev.md", "AGENTS.md", "CLAUDE.md"])
    for p in ("AGENTS.md", ".claude/agents/dev.md", "CLAUDE.md"):
        (root / p).write_bytes(b"# x\n\ncontent v1\n")
    _w(root / "CLAUDE.md", "# claude\n\ncontent v1\n")
    _w(root / "AGENTS.md", "# agents\n\ncontent v1\n")
    _w(root / ".claude/agents/dev.md", "# dev\n\ncontent v1\n")


def test_load_bearing_semantics(root, tmp):
    """**承重语义组**：地板表四格逐格 + 默认起点。

    这两条不是哪一轮修的缺陷，是整套四段闸门赖以成立的东西：

    - **地板表**：爆炸半径只当地板，往上抬、不发通行证。**档 4 ＝ 改的东西自己就是判据
      → 降不下去**——被测脚本自己正是档 4 资产，这一格一旦被悄悄改成可降档，
      它就能给自己签发。
    - **默认起点**：没有任何独立视角看过的东西，默认就该被独立看一次。

    **本组的由来**：套件的前一版只瞄准「这次改对了没有」（本轮修的四条缺陷 + 两条被拍板
    保持现状的行为），验证方实测把地板表档 4 那一格改掉、把默认起点那份副本改掉，
    **套件全绿**——「这次改对了没有」不等于「这套语义还在不在」。

    断言一律钉**端到端产出的那个档位**，不钉 `RADIUS_FLOOR` 这个零件本身：
    零件在货架上完好，不等于它装到了判定链路里。
    """
    print("[承重语义] 地板表四格 + 默认起点")
    pp = tmp / "pp.json"
    head = _git(root, "rev-parse", "HEAD")

    # 地板表四格：通行证成立时，各档位能降到哪里
    for radius, want in [("1", "自签"), ("2", "自签"), ("3", "轻签注"), ("4", "完整验证")]:
        _, _, _, d = run_cli(root, write_passport(pp, "T", head)
                             + ["--json", "--radius", radius, "--task", "T"])
        check(f"地板 档{radius} → {want}", d and d["tier"], want)

    # 档 4 单独再说一遍：它是「降不下去」，不是「刚好等于默认档」——
    # 通行证成立、其余条件全部允许降档时，它仍须停在完整验证
    _, _, _, d = run_cli(root, write_passport(pp, "T", head)
                         + ["--json", "--radius", "4", "--task", "T"])
    check("档 4 即使通行证成立也降不下去", d and d["tier"], "完整验证")
    check("档 4 时通行证确实是成立的（否则上一条会因别的原因而绿）",
          d and d["segments"]["passport"]["ok"], True)

    # 默认起点：无通行证时停在默认档，且**报出来的那个值**与实际用的是同一个
    _, _, _, d = run_cli(root, ["--json"])
    check("默认起点 无通行证 → 完整验证", d and d["tier"], "完整验证")
    check("默认起点 报出的段值同值", d and d["segments"]["default_start"], "完整验证")

    # 地板只往上抬、不发通行证：给了最低的半径但没有通行证，仍停在默认档
    _, _, _, d = run_cli(root, ["--json", "--radius", "1"])
    check("地板不发通行证（radius 1 无通行证仍停默认档）", d and d["tier"], "完整验证")

    # 缺口挡降档：扫描面有缺口时，那半的改动没被看见，据此降档就是拿看不见的东西换权限。
    # 这条路上唯一的合成动作是 `stricter()`，而它全文只有一个调用点——把它从「取严」
    # 反成「取松」，缺口那行照常打印「缺口存在时禁止降档」，档位却降到自签，
    # **输出自相矛盾而套件全绿**。验证方实测出来的，补在这里。
    # 取 `--since <根仓 HEAD>`：该 ref 在根仓解析得到、在嵌套仓解析不到，正是双仓拓扑
    # 下的真实缺口形态（`since_ref_unresolved`）。
    _, _, _, d = run_cli(root, write_passport(pp, "T", head)
                         + ["--json", "--radius", "1", "--task", "T", "--since", head])
    check("缺口存在时禁止降档（radius 1 + 通行证成立，仍停完整验证）",
          d and d["tier"], "完整验证")
    check("上一条确实产生了缺口（否则它会因为压根没缺口而绿）",
          bool(d and d["gaps"]), True)
    check("上一条通行证确实成立（否则它会因为通行证没过而绿）",
          d and d["segments"]["passport"]["ok"], True)


def test_accepted_risks_unchanged(root, tmp):
    """**锁定用例**：两条由用户 2026-09-09 拍板「不修 / 维持现状」的行为。

    报红不代表发现了缺陷，代表**有人改了一个当初由用户拍板的决定**——回到那个决定，
    别就地改断言。两条都在被测脚本的能力边界里有对应登记。
    """
    print("[F2/F4a 锁定] 被知情接受的风险必须保持原状")
    pp = tmp / "pp.json"
    head = _git(root, "rev-parse", "HEAD")
    big = root / "nested" / "big.txt"

    # F2：`--size-cap` 无下限，调用方可以把唯一的纯机器否决项抬掉，且不产生任何信号
    big.write_bytes((BIG + "".join(f"extra {i}\n" for i in range(3000))).encode("utf-8"))
    args = write_passport(pp, "T", head) + ["--json", "--radius", "1", "--task", "T"]
    _, _, _, d = run_cli(root, args)
    check("F2 默认上限下大改动仍封顶", d and d["tier"], "你拍板")
    _, _, _, d = run_cli(root, args + ["--size-cap", "999999999"])
    check("F2 上限被抬高后放行（用户拍板：先不管）", d and d["tier"], "自签")
    check("F2 抬高上限不产生任何缺口（登记在案的风险）", d and d["gaps"], [])
    big.write_bytes(BIG.encode("utf-8"))

    # F4a：V6 路径命中只进待裁决，不封顶
    (root / "CLAUDE.md").write_bytes(b"# claude\n\ncontent v2\n")
    _, _, _, d = run_cli(root, write_passport(pp, "T", head)
                         + ["--json", "--radius", "1", "--task", "T"])
    check("F4a V6 路径命中不封顶（用户拍板：维持现状）", d and d["tier"], "自签")
    check("F4a 但路径清单仍可机读", d and d["segments"]["veto"]["protected"], ["CLAUDE.md"])
    _w(root / "CLAUDE.md", "# claude\n\ncontent v1\n")


def test_known_limitation_dashdash_swallowed():
    """**现状用例（F8，已登记未修）**：内容本身以 `-- ` 开头的删除行会被当成文件头吞掉。

    它在 unified diff 里长成 `--- xxx`，与文件头同形。后果是 **deletions 少算**
    （方向是漏不是多，正好是尺寸上限该看住的方向）。真实资产命中 0 处，修法推后续批次：
    给 `---` / `+++` 的文件头判定加「必须紧跟在 `diff --git` 之后」的状态约束。

    **修好之后本用例会报红**——那时把它改成断言修复后的行为，别把断言删掉。
    """
    print("[F8 现状] diff 解析器对 `-- ` / `++ ` 开头内容行的已知缺陷")
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
    check("F8 两条删除行只算到 1 条（少算，登记未修）", fd and fd.deletions, 1)
    check("F8 被吞掉的是 `-- ` 那条", fd and fd.removed, ["SELECT 1;"])


def test_veto6_two_tables(root, tmp):
    """**否决项 6 的清单是它自己的一张表，不是 `auto-update` 那张。**

    这一组守两件会静默失效的事：

    1. **派生物后门**：机器写的派生物若留在封顶集里，它一变就把否决项 6 从后门触发，等于把
       「机器生成的东西不算人手改的受保护资产」那条排除**原样撤销**。**这条性质没有退役，
       退役的只是它当时的对象**——现在由运行态目录担这个角色。失效方式是**假红**
       （天天封顶到用户），而假红的下场是这道闸被人绕着走。
    2. **差集不许静默丢掉**：`.claude/skills/**` / `projects/proposals/**` 不封顶，
       但**仍受 `auto-update` 约束**。丢掉它们，读输出的人无从知道自己动了那张表——
       两张表就此混为一谈，而这正是必载段花一整段去分开的东西。

    断言钉的是**两个集合各自的成员**，不是「protected 非空」这种宽形状：宽形状下
    把镜像放回封顶集，`protected` 仍非空、仍报绿。
    """
    print("[V6-两张表] 封顶集 vs auto-update 集")
    pp = tmp / "pp2.json"
    head = _git(root, "rev-parse", "HEAD")
    down = ["--json", "--radius", "1", "--task", "T"]

    # 恢复用的原文**逐文件记**：一律写同一份会把别的用例的 fixture 改掉，
    # 而症状是「后面某个用例莫名其妙地多出一处改动」，查起来离成因很远
    touched = {
        f"{STATE}/inject-log.jsonl": "{}",
        ".claude/skills/s1/SKILL.md": "# s1",
        "projects/proposals/p1.md": "# p1",
        "AGENTS.md": "# agents",
    }
    for rel in touched:
        (root / rel).write_bytes(b"# t\n\ncontent v2\n")
    _, _, _, d = run_cli(root, write_passport(pp, "T", head) + down)
    veto = (d or {}).get("segments", {}).get("veto", {})

    check("封顶集恰为项目 rule 那一条", sorted(veto.get("protected", [])),
          ["AGENTS.md"])
    check("差集三条都被报出来（不是静默丢掉）", sorted(veto.get("auto_update_only", [])),
          sorted([".claude/skills/s1/SKILL.md", f"{STATE}/inject-log.jsonl",
                  "projects/proposals/p1.md"]))
    _, out_text, _, _ = run_cli(root, write_passport(pp, "T", head)
                                + ["--radius", "1", "--task", "T"])
    check_in("差集在人读的那份里也说明了两表关系", "真子集", out_text)

    # 单元层：判定函数本身，绕开 CLI 直接钉两张表的边界
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
        (".claude/skills/s/SKILL.md", False, True, "skills：当轮可见（CC 里经链接 Edit 的路径 / 真目录形态）"),
        (".agents/skills/s/SKILL.md", False, True, "skills 真实源：链接形态下 git 报的路径"),
        ("projects/proposals/p.md", False, True, "proposals：本身就是待审对象"),
        ("README.md", False, True, "不影响运行"),
        ("LICENSE", False, True, "不影响运行"),
        ("docs/plain.md", False, False, "普通文档，两张表都不在"),
    ):
        check(f"veto6 {path}（{why}）", stc.is_veto6_path(path, v6), in_v6)
        check(f"auto-update {path}", stc.is_auto_update_protected(path, au), in_au)

    for rel, head_line in touched.items():
        (root / rel).write_bytes((head_line + chr(10) * 2 + "content v1" + chr(10)).encode("utf-8"))


def main():
    print(f"[test] signoff_tier_check.py @ {SCRIPTS_DIR / 'signoff_tier_check.py'}")
    if shutil.which("git") is None:
        print("  ! 环境无 git，跳过端到端用例（单元用例照跑）")
        test_structured_kind()
        test_known_limitation_dashdash_swallowed()
    else:
        test_structured_kind()
        test_known_limitation_dashdash_swallowed()
        tmp = Path(tempfile.mkdtemp(prefix="wf-signoff-test-"))
        try:
            root = tmp / "proj"
            root.mkdir()
            build_fixture(root)
            test_passport_task_matrix(root, tmp)
            test_v1_ordered_list(root, tmp)
            test_pending_not_always_true(root, tmp)
            test_veto6_two_tables(root, tmp)
            test_load_bearing_semantics(root, tmp)
            test_accepted_risks_unchanged(root, tmp)
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
