#!/usr/bin/env python3
"""导航段（AUTO-INDEX）规划器与 CLI 的常驻单测：`plugins/core/scripts/_module_nav.py` ＋ `module_init.py` 的
`--refresh-index` / `--check` / `--convert-layout`。

导航段错了的两个方向都静默：漏刷 → 读者看到旧状态（评审已加却显示 `-`、需求已上线仍「尚未启动」）；
误写 → 人写的旧表、行尾注记、段外文字被抹掉。所以每组都同时看渲染结果与「没被碰的字节」：

- v2 基线上评审、用例 0→1→2、3→4，断言链接到的具体文件与个数；只改 PRD 描述时唯一清单出现新描述
- 跨进程确认码：只改目标旧表 / 只改 PRD / 只新增附件，旧码一律整批拒绝、零写入
- 坏段与健康但漂移的段并存：两条诊断都在，健康那份照写
- 旧版式项目新建子模块：旧的自定义表一字不变，上级匿名表出现新子模块
- 缺段、围栏内的示例标记、`_draft`、缺 PRD、限定 scope、空 scope、幂等；`--check` 三种结果零写入；`apply` 冲突
- 「最近同步」四种情形（首次插 ref、改 ref、清 ref、占位日期＋人写括注）：OVERVIEW_REF_RE 能提取、人写文字一字不差
- 行尾与 BOM 保全；现状行按未编写状态取 token、尾注保留；未盖戳文档独立报出
- 出厂模板的段体与渲染器对空数据的产出逐字一致（新建模块后 `--check` 无差异）

执行：python tools/test_module_nav.py（退出 0 且打印 all passed = 通过）
"""

import json
import os
import re
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
CORE = REPO_ROOT / "plugins" / "core"
SCRIPTS = CORE / "scripts"
TEMPLATES = CORE / "templates" / "modules-template"
MODULE_INIT = SCRIPTS / "module_init.py"
sys.path.insert(0, str(SCRIPTS))

import _doc_state as ds  # noqa: E402
import _module_nav as nav  # noqa: E402
import module_init  # noqa: E402

FAILURES = []
TMP = Path(tempfile.mkdtemp(prefix="wf-modnav-")).resolve()   # 本机 TEMP 是 8.3 短名，不 resolve 则项目内判定对不上


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + ("" if ok else f": got={got!r} want={want!r}"))
    if not ok:
        FAILURES.append(label)


def run(*args, project):
    r = subprocess.run([sys.executable, str(MODULE_INIT), "--project", str(project), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, PYTHONUTF8="1"))
    return r.returncode, r.stdout, r.stderr


def run_json(*args, project):
    code, out, err = run(*args, "--json", project=project)
    try:
        return code, json.loads(out)
    except ValueError:
        return code, {"raw": out, "err": err}


_COUNTER = [0]


def new_project(subs=("乙",), basic="甲"):
    """脚本建树（与 module-init 同路径），返回项目根。"""
    _COUNTER[0] += 1
    proj = TMP / f"p{_COUNTER[0]}"
    proj.mkdir()
    params = proj / "params.json"
    params.write_text(json.dumps({"basics": [{"name": basic, "subs": [{"name": s} for s in subs]}]},
                                 ensure_ascii=False), encoding="utf-8", newline="")
    module_init.create_tree(proj, module_init.load_params(params))
    params.unlink()
    to_lf(proj)
    return proj


def to_lf(proj):
    """本机工作区模板是 CRLF（autocrlf），建出来的文件随之是 CRLF；夹具统一成 LF 与 CI 一致，
    CRLF 由 test_bytes_preserved 显式构造。"""
    for p in proj.rglob("*"):
        if p.is_file():
            p.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))


def add_requirement(proj, slug, basic="甲", sub="乙", status="planning", subreqs=("main",)):
    """照 module-init SKILL 的做法复制需求包模板、替换变量、给 PRD 盖戳。"""
    r_dir = proj / "projects" / "modules" / basic / sub / "requirements" / slug
    mapping = {"REQ_SLUG": slug, "REQ_TITLE": slug, "OWNER_ROLE": "pm", "MODULE_PATH": f"{basic}/{sub}",
               "TODAY": "2026-11-22", "NOW_ISO": "2026-11-22T00:00:00+08:00"}
    for src in sorted((TEMPLATES / "requirement").rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(TEMPLATES / "requirement")
        for name in (subreqs if rel.parts[0] == "main" else (None,)):
            dst = r_dir / (Path(name, *rel.parts[1:]) if name else rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            text = src.read_bytes().decode("utf-8").replace("\r\n", "\n")
            for k, v in dict(mapping, SUB_REQ_SLUG=name or "main").items():
                text = text.replace("{{" + k + "}}", v)
            if rel.name == "meta.yaml":
                text = text.replace("status: planning", f"status: {status}", 1)
            dst.write_bytes(text.encode("utf-8"))
    for name in subreqs:
        module_init.mark_unwritten(proj, [str(r_dir / name / "prd.md")])
    return r_dir


def write_prd(r_dir, sub="main", description="真实摘要", status="review", body="## 一、背景\n\n真实正文。\n"):
    p = r_dir / sub / "prd.md"
    p.write_bytes(f"---\ntype: prd\nstatus: {status}\ndescription: \"{description}\"\n---\n\n{body}".encode("utf-8"))


def snapshot(proj):
    return {p.relative_to(proj).as_posix(): p.read_bytes()
            for p in sorted(proj.rglob("*")) if p.is_file() and ".workframe" not in p.parts}


def seg_body(path, name=None):
    text = path.read_bytes().decode("utf-8-sig")
    tag = f":{name}" if name else ""
    a = text.index(f"<!-- WORKFRAME:AUTO-INDEX:START{tag} -->")
    b = text.index(f"<!-- WORKFRAME:AUTO-INDEX:END{tag} -->")
    return text[a:b]


def sub_dir(proj, basic="甲", sub="乙"):
    return proj / "projects" / "modules" / basic / sub


# ---------------------------------------------------------------- 组


def test_fresh_tree_matches_templates():
    print("[新建模块：模板段体 = 渲染产出]")
    proj = new_project()
    code, out, err = run("--check", project=proj)
    check("脚本新建子模块后 --check 无差异", (code, out.strip()), (0, "[module-init] 导航段无差异"))
    r = add_requirement(proj, "需求一")
    code, out, err = run("--check", project=proj)
    check("复制需求包后：模板段体本身无差异，只有计数与子需求行待刷", code, 1)
    check("子需求行显示「已立项、未开写」",
          "| main | 已立项、未开写 | - | [prd](<./main/prd.md>) | - | - |" in seg_body(r / "overview.md"), False)
    code, out, _ = run("--refresh-index", project=proj)
    check("刷新后子需求行显示「已立项、未开写」",
          "| main | 已立项、未开写 | - | [prd](<./main/prd.md>) | - | - |" in seg_body(r / "overview.md"), True)
    check("刷新后计数行：规划 1", "进行中 0 · 规划 1 · 已完成 0 · 已放弃 0 · 状态未知 0"
          in seg_body(sub_dir(proj) / "overview.md", "requirements-index"), True)
    check("现状行为「未编写」", seg_body(sub_dir(proj) / "overview.md", "current-state-summary").count(nav.UNWRITTEN_TOKEN), 4)
    before = snapshot(proj)
    check("第二次刷新：退出 0", run("--refresh-index", project=proj)[0], 0)
    check("两次运行幂等（字节零差异）", snapshot(proj) == before, True)


def test_attachments_and_summary():
    print("[评审 / 用例逐个链接；PRD 描述进唯一清单]")
    proj = new_project()
    r = add_requirement(proj, "需求一")
    run("--refresh-index", project=proj)
    main = r / "main"
    seq = [("reviews", "2026-11-01-评审甲.md"), ("reviews", "2026-11-02-评审乙.md"),
           ("test-cases", "用例甲.md"), ("test-cases", "子目录/用例乙.md")]
    for i, (d, f) in enumerate(seq, 1):
        (main / d / f).parent.mkdir(parents=True, exist_ok=True)
        (main / d / f).write_bytes(b"# x\n")
        run("--refresh-index", "--scope", str(main / d / f), project=proj)
        body = seg_body(r / "overview.md")
        row = next(l for l in body.splitlines() if l.startswith("| main |"))
        reviews = re.findall(r"\(<\./main/reviews/([^>]+)>\)", row)
        cases = re.findall(r"\(<\./main/test-cases/([^>]+)>\)", row)
        want_r = sorted(f2 for d2, f2 in seq[:i] if d2 == "reviews")
        want_c = sorted(f2 for d2, f2 in seq[:i] if d2 == "test-cases")
        check(f"第 {i} 步评审链接 = {want_r}", reviews, want_r)
        check(f"第 {i} 步用例链接 = {want_c}", cases, want_c)
    check(".gitkeep 不算评审", ".gitkeep" in seg_body(r / "overview.md"), False)
    write_prd(r, description="订单提交前校验库存")
    run("--refresh-index", "--scope", str(r / "main" / "prd.md"), project=proj)
    listing = seg_body(sub_dir(proj) / "requirements" / "overview.md")
    check("唯一清单（requirements/overview.md）出现新描述", "| 需求一 | pm | 订单提交前校验库存 |" in listing, True)
    check("子模块页只有计数、不复制清单", "订单提交前校验库存" in seg_body(sub_dir(proj) / "overview.md", "requirements-index"), False)
    check("子需求行状态为 PRD 原值", "| main | review | 订单提交前校验库存 |" in seg_body(r / "overview.md"), True)


def test_status_tables_and_missing_prd():
    print("[按状态五张表；缺 PRD；多子需求]")
    proj = new_project()
    add_requirement(proj, "进行", status="active")
    done = add_requirement(proj, "完成", status="done")
    add_requirement(proj, "怪", status="shipped")
    multi = add_requirement(proj, "多子", subreqs=("main", "拆分"))
    (multi / "拆分" / "prd.md").unlink()
    write_prd(done, description="已上线的那个")
    run("--refresh-index", project=proj)
    listing = seg_body(sub_dir(proj) / "requirements" / "overview.md")
    check("done 表带摘要列", "| 完成 | 1 | 2026-11-22 | 已上线的那个 |" in listing, True)
    check("非法状态进未知表、写原值", "| 怪 | shipped | pm |" in listing, True)
    check("计数行", "进行中 1 · 规划 1 · 已完成 1 · 已放弃 0 · 状态未知 1"
          in seg_body(sub_dir(proj) / "overview.md", "requirements-index"), True)
    body = seg_body(multi / "overview.md")
    check("缺 prd.md 的子需求两列写「缺 prd.md」", "| 拆分 | _缺 prd.md_ | _缺 prd.md_ | - |" in body, True)
    check("多子需求：main 行在", "| main | 已立项、未开写 |" in body, True)


def test_convert_and_confirm_code():
    print("[版式转换：预览零写入、确认码跨进程失效、零写入拒绝]")
    proj = new_project()
    r = add_requirement(proj, "需求一")
    run("--refresh-index", project=proj)
    legacy = ("<!-- WORKFRAME:AUTO-INDEX:START -->\n> 旧说明\n\n| 子需求 | 状态 | 摘要 | PRD | 复盘 | 上线 |\n"
              "|---|---|---|---|---|---|\n| main | draft | 老摘要 | [prd](./main/prd.md) | - | 2026-10-01 |\n\n"
              "<!-- WORKFRAME:AUTO-INDEX:END -->")
    ov = r / "overview.md"
    text = ov.read_bytes().decode("utf-8")
    a, b = text.index("<!-- WORKFRAME:AUTO-INDEX:START -->"), text.index("<!-- WORKFRAME:AUTO-INDEX:END -->")
    ov.write_bytes((text[:a] + legacy + text[b + len("<!-- WORKFRAME:AUTO-INDEX:END -->"):]).encode("utf-8"))
    before = snapshot(proj)
    code, out, _ = run("--refresh-index", project=proj)
    check("旧版式段：--refresh-index 只报不写", ("legacy" in out, snapshot(proj) == before), (True, True))
    code, prev = run_json("--convert-layout", project=proj)
    check("预览退出 0 且零写入", (code, snapshot(proj) == before), (0, True))
    det = next(d for d in prev["details"] if d["path"].endswith("需求一/overview.md"))
    check("预览列出项目自加列「上线」及其有值单元格", det["extra_columns"], [{"column": "上线", "cells": [["main", "2026-10-01"]]}])
    check("预览列出将消失的旧行（含老摘要那行）",
          any("老摘要" in g for g in det["gone"]), True)

    def stale_code_rejected(label, mutate):
        _, p1 = run_json("--convert-layout", project=proj)
        mutate()
        snap = snapshot(proj)
        code, out, err = run("--convert-layout", "--apply", "--confirm", p1["confirm"], project=proj)
        check(f"{label}：旧码 exit 2", code, 2)
        check(f"{label}：零写入", snapshot(proj) == snap, True)

    stale_code_rejected("只改目标旧表", lambda: ov.write_bytes(ov.read_bytes().replace("老摘要".encode(), "改过".encode())))
    stale_code_rejected("只改 PRD", lambda: write_prd(r, description="新描述"))
    stale_code_rejected("只新增附件", lambda: (r / "main" / "reviews" / "新评审.md").write_bytes(b"# r\n"))
    _, p2 = run_json("--convert-layout", project=proj)
    code, out, _ = run("--convert-layout", "--apply", "--confirm", p2["confirm"], project=proj)
    check("当前码执行：exit 0", code, 0)
    body = seg_body(ov)
    check("转换后是 v2 表头", "| 子需求 | 状态 | 摘要 | PRD | 评审 | 用例 |" in body, True)
    check("转换后评审链接到文件", "(<./main/reviews/新评审.md>)" in body, True)
    check("转换后 --check 无差异", run("--check", project=proj)[0], 0)


def test_example_diagram_choice():
    print("[基础模块总览示例图：按 --choice 换，缺省保留]")
    proj = new_project()
    bov = proj / "projects" / "modules" / "甲" / "overview.md"
    text = bov.read_bytes().decode("utf-8")
    text = text.replace(nav.EXAMPLE_NOTE, nav.EXAMPLE_LEAD + "\n\n" + nav.EXAMPLE_DIAGRAM)
    bov.write_bytes(text.encode("utf-8"))
    _, prev = run_json("--convert-layout", project=proj)
    check("预览列出示例图", [e["path"] for e in prev["examples"]], ["projects/modules/甲/overview.md"])
    run("--convert-layout", "--apply", "--confirm", prev["confirm"], project=proj)
    check("不带 --choice：示例图保留", nav.EXAMPLE_DIAGRAM in bov.read_bytes().decode("utf-8"), True)
    choice = "projects/modules/甲/overview.md=replace-example"
    code, out, _ = run("--convert-layout", "--apply", "--confirm", prev["confirm"], "--choice", choice, project=proj)
    check("带 --choice 而码来自不带选项的预览：拒绝", code, 2)
    _, prev2 = run_json("--convert-layout", "--choice", choice, project=proj)
    code, _, _ = run("--convert-layout", "--apply", "--confirm", prev2["confirm"], "--choice", choice, project=proj)
    after = bov.read_bytes().decode("utf-8")
    check("带同样选项预览后执行：exit 0", code, 0)
    check("示例图与引导注释一起换成说明", (nav.EXAMPLE_DIAGRAM in after, after.count(nav.EXAMPLE_NOTE),
                                         nav.EXAMPLE_LEAD in after), (False, 1, False))


def test_problems_isolated():
    print("[坏段与漂移段并存：两条诊断都在，健康文件照写]")
    proj = new_project(subs=("乙", "丙"))
    bad = sub_dir(proj, sub="乙") / "overview.md"
    bad.write_bytes(bad.read_bytes().replace(b"<!-- WORKFRAME:AUTO-INDEX:END:requirements-index -->", b""))
    good = sub_dir(proj, sub="丙")
    add_requirement(proj, "需求一", sub="丙")      # 让丙的计数行漂移
    code, res = run_json("--check", project=proj)
    probs = [(r["path"].split("/")[-2], r["reason"].split("：")[0]) for r in res["results"] if r["status"] == "problem"]
    drift = [r for r in res["results"] if r["status"] == "update" and r["path"].endswith("丙/overview.md")]
    check("--check 无法检查 → exit 2", code, 2)
    check("坏段诊断在（乙：不配对）", ("乙", "unpaired") in probs, True)
    check("漂移诊断在（丙 requirements-index）", [d["segment"] for d in drift], ["requirements-index"])
    before_bad = bad.read_bytes()
    code, _, _ = run("--refresh-index", project=proj)
    check("--refresh-index 有段问题 → exit 2", code, 2)
    check("健康文件照写", "规划 1" in seg_body(good / "overview.md", "requirements-index"), True)
    check("坏文件零写入", bad.read_bytes() == before_bad, True)


def test_legacy_project_new_sub():
    print("[旧版式项目新建子模块：旧表不动，上级匿名表出现新子模块]")
    proj = new_project()
    ov = sub_dir(proj) / "overview.md"
    custom = ("> ⚙️ 旧说明\n\n| req_slug | status | 子需求数 | owner | 摘要 |\n|---|---|---|---|---|\n"
              "| 手写 | done | 1 | pm | 人写的旧行 |\n\n")
    text = ov.read_bytes().decode("utf-8")
    a = text.index("<!-- WORKFRAME:AUTO-INDEX:START:requirements-index -->\n") + len("<!-- WORKFRAME:AUTO-INDEX:START:requirements-index -->\n")
    b = text.index("<!-- WORKFRAME:AUTO-INDEX:END:requirements-index -->")
    ov.write_bytes((text[:a] + custom + text[b:]).encode("utf-8"))
    old = ov.read_bytes()
    params = proj / "p2.json"
    params.write_text(json.dumps({"basics": [{"name": "甲", "subs": [{"name": "丁"}]}]}, ensure_ascii=False),
                      encoding="utf-8", newline="")
    module_init.create_tree(proj, module_init.load_params(params))
    params.unlink()
    check("旧的自定义表一字不变", ov.read_bytes() == old, True)
    check("上级匿名表出现新子模块",
          "[丁](./丁/overview.md)" in seg_body(proj / "projects" / "modules" / "甲" / "overview.md"), True)


def test_segments_edge_cases():
    print("[缺段 / 围栏内标记 / _draft / scope]")
    proj = new_project()
    ov = sub_dir(proj) / "overview.md"
    text = ov.read_bytes().decode("utf-8")
    fenced = "\n```markdown\n<!-- WORKFRAME:AUTO-INDEX:START:bogus -->\n<!-- WORKFRAME:AUTO-INDEX:END:bogus -->\n```\n"
    ov.write_bytes((text + fenced).encode("utf-8"))
    code, res = run_json("--check", project=proj)
    check("围栏内的示例标记不算段（无差异）", code, 0)
    draft = sub_dir(proj) / "requirements" / "_draft" / "草稿"
    draft.mkdir(parents=True)
    (draft / "overview.md").write_bytes(b"# draft without segments\n")
    check("_draft 下不检", run("--check", project=proj)[0], 0)
    ov.write_bytes(text.replace("<!-- WORKFRAME:AUTO-INDEX:START:requirements-index -->", "").replace(
        "<!-- WORKFRAME:AUTO-INDEX:END:requirements-index -->", "").encode("utf-8"))
    code, res = run_json("--check", project=proj)
    check("缺段 → exit 2 且原因码 missing-segment",
          (code, [r["reason"].split("：")[0] for r in res["results"] if r["status"] == "problem"]),
          (2, ["missing-segment"]))
    ov.write_bytes(text.encode("utf-8"))
    for label, args in (("空 scope", ("--scope",)), ("scope 在项目外", ("--scope", str(TMP))),
                        ("scope 归不到基础模块", ("--scope", "projects/modules/overview.md")),
                        ("scope 不在 --basic 内", ("--basic", "甲", "--scope", "不存在/乙"))):
        before = snapshot(proj)
        code, out, err = run("--refresh-index", *args, project=proj)
        check(f"{label} → exit 2 且零写入", (code, snapshot(proj) == before), (2, True))
    proj2 = new_project(subs=("乙", "丙"))
    add_requirement(proj2, "需求一", sub="乙")
    add_requirement(proj2, "需求二", sub="丙")
    run("--refresh-index", "--scope", "甲/乙", project=proj2)
    check("限定 scope：乙被刷新", "规划 1" in seg_body(sub_dir(proj2, sub="乙") / "overview.md", "requirements-index"), True)
    check("限定 scope：丙未动", "规划 0" in seg_body(sub_dir(proj2, sub="丙") / "overview.md", "requirements-index"), True)


def test_check_zero_write_and_conflict():
    print("[--check 三种结果零写入；apply 冲突]")
    proj = new_project()
    for label, prep, want in (("无差异", lambda: None, 0),
                              ("有差异", lambda: add_requirement(proj, "需求一"), 1)):
        prep()
        before = snapshot(proj)
        code, _, _ = run("--check", project=proj)
        check(f"--check {label} → exit {want}，零写入", (code, snapshot(proj) == before), (want, True))
    ov = sub_dir(proj) / "overview.md"
    ov.write_bytes(ov.read_bytes().replace(b"<!-- WORKFRAME:AUTO-INDEX:END:current-state-summary -->", b""))
    before = snapshot(proj)
    check("--check 无法检查 → exit 2，零写入", (run("--check", project=proj)[0], snapshot(proj) == before), (2, True))
    proj = new_project()
    add_requirement(proj, "需求一")
    p = nav.plan(proj)
    target = next(c.path for c in p.changes if c.kind == "update")
    (proj / target).write_bytes((proj / target).read_bytes() + b"\n")
    changed = (proj / target).read_bytes()
    results = {r.path: r.status for r in nav.apply(p)}
    check("plan 之后改了一个字节 → conflict", results[target], "conflict")
    check("冲突文件零写入", (proj / target).read_bytes() == changed, True)
    check("其余文件照写", "written" in results.values(), True)


def _sync(rest, date, ref):
    return nav._sync_line(rest, date, ref)


def test_sync_line():
    print("[最近同步：只换日期与 ref，人写文字一字不差]")
    ref_re = re.compile(r"基准 commit[:：]?\s*([0-9a-fA-F]{7,40})")
    try:
        import module_close_check as mcc
        ref_re = mcc.OVERVIEW_REF_RE
    except Exception:
        pass
    human = "：TASK-1 人写说明（含括号）；尾注"
    cases = [
        ("首次插 ref", "2026-11-01" + human, "2026-11-17", "abc12345", "2026-11-17（基准 commit abc12345）" + human),
        ("改 ref", "2026-11-01（基准 commit 11111111）" + human, "2026-11-17", "abc12345",
         "2026-11-17（基准 commit abc12345）" + human),
        ("清 ref", "2026-11-01（基准 commit 11111111——人写附注）" + human, "2026-11-17", "",
         "2026-11-17（人写附注）" + human),
        ("占位日期＋人写括注", "_（未同步）_（人写括注）", "2026-11-17", "abc12345",
         "2026-11-17（基准 commit abc12345）（人写括注）"),
    ]
    for label, rest, date, ref, want in cases:
        got = _sync(rest, date, ref)
        check(f"{label}：整行", got, want)
        m = ref_re.search("- **最近同步**：" + got)
        check(f"{label}：OVERVIEW_REF_RE 提取", m.group(1) if m else None, ref or None)
    check("清 ref 且括注只剩 ref：括注整个去掉", _sync("2026-11-01（基准 commit 11111111）" + human, "2026-11-17", ""),
          "2026-11-17" + human)
    check("出厂旧占位整体是日期 token", _sync("_（last_synced_at；已同步后在日期后附「基准 commit <last_synced_ref>」括注）_",
                                          nav.UNSYNCED_TOKEN, ""), nav.UNSYNCED_TOKEN)


def test_current_state_rows():
    print("[现状行：按未编写状态取 token，尾注保留；读的是 last_synced_*]")
    proj = new_project()
    s = sub_dir(proj)
    ov = s / "overview.md"
    ov.write_bytes(ov.read_bytes().replace("- **代码索引**：_（未编写）_".encode(),
                                           "- **代码索引**：_（未编写）_；普通代码档按需".encode()))
    arch = s / "current-state" / "architecture.md"
    arch.write_bytes("---\ntype: current-state\ndescription: 订单子模块架构：三层（接入 / 编排 / 存储）\n---\n\n"
                     "# A\n\n## 总览\n\n<!-- c -->\n入口在 handler.py:12。第二句。\n".encode("utf-8"))
    dm = s / "current-state" / "data-model.md"
    dm.write_bytes("---\ntype: current-state\ndescription: \"\"\n---\n\n# D\n\n## 表\n\n订单表 orders。\n".encode("utf-8"))
    y = s / "submodule.yaml"
    y.write_bytes(y.read_bytes() + b'last_synced_ref: "abc12345"\n'
                  if b"last_synced_ref" not in y.read_bytes() else y.read_bytes())
    text = y.read_bytes().decode("utf-8")
    text = re.sub(r"(?m)^last_synced_at:.*$", 'last_synced_at: "2026-11-17T10:00:00+08:00"  # note', text)
    text = re.sub(r"(?m)^last_synced_ref:.*$", 'last_synced_ref: "abc12345"', text)
    y.write_bytes(text.encode("utf-8"))
    run("--refresh-index", project=proj)
    body = seg_body(ov, "current-state-summary")
    old_style = nav.render("current-state-summary", {
        "old_body": "- **API**：_（待 code-to-doc 生成 api-surface.md 后摘要；高风险档按需）_\n",
        "docs": {"API": nav.UNWRITTEN_TOKEN}, "sync_date": nav.UNSYNCED_TOKEN, "sync_ref": ""})
    check("斜体占位里的人写注记挪成尾注、不切穿斜体",
          f"- **API**：{nav.UNWRITTEN_TOKEN}；高风险档按需" in old_style.splitlines(), True)
    check("已编写的架构取 description，不取正文首句", "- **架构**：订单子模块架构：三层（接入 / 编排 / 存储）" in body, True)
    check("已编写却没填 description：显示「未填摘要」", f"- **数据模型**：{nav.NO_SUMMARY_TOKEN}" in body, True)
    check("未编写的 API 显示「未编写」", f"- **API**：{nav.UNWRITTEN_TOKEN}" in body, True)
    check("尾注原样保留", f"- **代码索引**：{nav.UNWRITTEN_TOKEN}；普通代码档按需" in body, True)
    check("最近同步取 last_synced_at 日期与 ref", "- **最近同步**：2026-11-17（基准 commit abc12345）" in body, True)
    arch.write_bytes(module_init._read_text(TEMPLATES / "sub-module" / "current-state" / "architecture.md").replace(
        "{{SUB_NAME}}", "乙").replace("{{MODULE_PATH}}", "甲/乙").replace("{{TODAY}}", "2026-11-22").replace(
        "{{NOW_ISO}}", "2026-11-22T00:00:00+08:00").replace("{{OWNER_ROLE}}", "pm").encode("utf-8"))
    module_init.mark_unwritten(proj, [str(arch)])
    run("--refresh-index", project=proj)
    check("文档退回骨架：不保留旧摘要", f"- **架构**：{nav.UNWRITTEN_TOKEN}" in seg_body(ov, "current-state-summary"), True)


def test_bytes_preserved():
    print("[行尾与 BOM 保全；段外零接触]")
    proj = new_project()
    add_requirement(proj, "需求一")
    ov = sub_dir(proj) / "overview.md"
    crlf = ds.BOM + ov.read_bytes().replace(b"\n", b"\r\n")
    ov.write_bytes(crlf)
    run("--refresh-index", project=proj)
    after = ov.read_bytes()
    check("BOM 保留", after.startswith(ds.BOM), True)
    check("全文仍是 CRLF（无裸 LF）", after.count(b"\n") == after.count(b"\r\n"), True)
    seg = re.compile(rb"<!-- WORKFRAME:AUTO-INDEX:START:requirements-index -->[\s\S]*?<!-- WORKFRAME:AUTO-INDEX:END:requirements-index -->")
    check("段外字节零变化", seg.sub(b"", after) == seg.sub(b"", crlf), True)
    check("段内已更新", "规划 1".encode() in after, True)


def test_unstamped_reported():
    print("[未盖戳文档独立报出]")
    proj = new_project()
    r = add_requirement(proj, "需求一")
    prd = r / "main" / "prd.md"
    prd.write_bytes(re.sub(rb"(?m)^unwritten: .*$", b"unwritten: true", prd.read_bytes()))
    code, res = run_json("--check", project=proj)
    un = [x["path"] for x in res["results"] if x["status"] == "unstamped"]
    check("unstamped 的 PRD 被列出", un, [prd.relative_to(proj).as_posix()])
    check("unstamped 不算「无法检查」", code in (0, 1), True)


def _set_sync(proj, at, ref, sub="乙"):
    y = sub_dir(proj, sub=sub) / "submodule.yaml"
    text = y.read_bytes().decode("utf-8")
    text = re.sub(r"(?m)^last_synced_at:.*$", f'last_synced_at: "{at}"', text)
    if re.search(r"(?m)^last_synced_ref:", text):
        text = re.sub(r"(?m)^last_synced_ref:.*$", f'last_synced_ref: "{ref}"', text)
    else:
        text += f'last_synced_ref: "{ref}"\n'
    y.write_bytes(text.encode("utf-8"))


def _replace_cs_row(ov, label, new_line):
    text = ov.read_bytes().decode("utf-8")
    text = re.sub(r"(?m)^- \*\*" + re.escape(label) + r"\*\*：.*$", lambda m: new_line, text, count=1)
    ov.write_bytes(text.encode("utf-8"))


def test_review_round1():
    print("[第一轮复核的复现形态]")
    # ① 行内代码里演示的标记不是段
    proj = new_project()
    bov = proj / "projects" / "modules" / "甲" / "overview.md"
    demo = ("\n说明：`<!-- WORKFRAME:AUTO-INDEX:START -->`\n人工迁移说明，须保留\n"
            "`<!-- WORKFRAME:AUTO-INDEX:END -->`\n")
    bov.write_bytes(bov.read_bytes() + demo.encode("utf-8"))
    code, res = run_json("--check", project=proj)
    check("行内代码里的标记：无诊断、无差异", (code, [r for r in res["results"] if r["status"] != "unchanged"]), (0, []))
    run("--refresh-index", project=proj)
    check("行内代码里的标记：人写说明保留", "人工迁移说明，须保留" in bov.read_bytes().decode("utf-8"), True)

    # ② 占位里的人写注记、第二条说明行
    proj = new_project()
    ov = sub_dir(proj) / "overview.md"
    _replace_cs_row(ov, "最近同步", "- **最近同步**：_（未同步；迁移前需复核）_")
    text = ov.read_bytes().decode("utf-8").replace("- **架构**：", "> ⚙️ 仅在周五更新此表\n- **架构**：", 1)
    ov.write_bytes(text.encode("utf-8"))
    _set_sync(proj, "2026-11-22T10:00:00+08:00", "abc12345")
    run("--refresh-index", project=proj)
    body = seg_body(ov, "current-state-summary")
    check("占位里的注记挪成尾注", "- **最近同步**：2026-11-22（基准 commit abc12345）；迁移前需复核" in body, True)
    check("段体第一行以外的说明行原样保留", "> ⚙️ 仅在周五更新此表" in body, True)

    # ③ description 含「；」：反复刷新不增长
    proj = new_project()
    arch = sub_dir(proj) / "current-state" / "architecture.md"
    arch.write_bytes("---\ntype: current-state\ndescription: 可跨门解除；CLI 特定失败只由同门解除\n---\n\n## 总览\n\n正文。\n"
                     .encode("utf-8"))
    ov = sub_dir(proj) / "overview.md"
    bodies = []
    for _ in range(3):
        run("--refresh-index", project=proj)
        bodies.append(seg_body(ov, "current-state-summary"))
    row = next(l for l in bodies[0].splitlines() if l.startswith("- **架构**："))
    check("三次刷新结果相同", bodies[0] == bodies[1] == bodies[2], True)
    check("机器 token 不含「；」", "；" in row, False)
    check("上两层定位摘要沿用既有切句：有「。」就切到「。」，前面的「；」保留",
          nav._first_sentence("内嵌终端；会话占满。第二句"), "内嵌终端；会话占满。")

    # ⑤ ref 前有空格时清空；branch: 形态连刷不重复
    for label, rest, want_ref in (("日期后有空格", "2026-11-20 （基准 commit abcd1234）；说明", ""),
                                  ("branch 形态", "2026-11-20（基准 commit branch:main）；说明", "branch:dev")):
        got = nav._sync_line(nav._sync_line(rest, "2026-11-21", want_ref), "2026-11-21", want_ref)
        check(f"{label}：连刷两次只有一份括注", got.count("基准 commit"), 1 if want_ref else 0)
        check(f"{label}：说明保留", got.endswith("；说明"), True)

    # 第二轮：括注里紧跟 ref 的人写文字；裸 hash 不被 YAML 当数字；完成时间原值；缩进代码块里的标记
    check("括注里 ref 后的人写文字保留",
          nav._sync_line("2026-11-01（基准 commit abcd1234、仅测主仓）", "2026-11-22", "fedcba98"),
          "2026-11-22（基准 commit fedcba98、仅测主仓）")
    for ref in ("01234567", "12e45678"):
        check(f"未加引号的 last_synced_ref: {ref} 原样读出",
              nav.yaml_fields(f"last_synced_ref: {ref}\nlast_synced_at: null\n"), {"last_synced_ref": ref, "last_synced_at": ""})
    tproj = new_project()
    done = add_requirement(tproj, "终态", status="done")
    m = done / "meta.yaml"
    m.write_bytes(re.sub(rb'(?m)^updated_at:.*$', b'updated_at: "2026-11-22T10:12:45+08:00"', m.read_bytes()))
    run("--refresh-index", project=tproj)
    check("完成时间按 updated_at 原值显示", "| 终态 | 1 | 2026-11-22T10:12:45+08:00 |"
          in seg_body(sub_dir(tproj) / "requirements" / "overview.md"), True)
    check("缩进 4 空格的标记是代码块", nav.MARKER_LINE_RE.match("    <!-- WORKFRAME:AUTO-INDEX:START -->"), None)
    check("缩进 3 空格的标记仍是段", bool(nav.MARKER_LINE_RE.match("   <!-- WORKFRAME:AUTO-INDEX:START -->")), True)

    # ⑥ YAML 折叠标量、单引号转义；附件名带 #；点文件不按前缀过滤
    proj = new_project()
    r = add_requirement(proj, "需求一")
    prd = r / "main" / "prd.md"
    prd.write_bytes("---\ntype: prd\nstatus: review\ndescription: >-\n  折叠的\n  摘要\n---\n\n## 一\n\n正文。\n".encode("utf-8"))
    (r / "main" / "reviews" / "review #12.md").write_bytes(b"# r\n")
    (r / "main" / "reviews" / ".review.md").write_bytes(b"# r\n")
    run("--refresh-index", project=proj)
    body = seg_body(r / "overview.md")
    check("折叠标量 description 读成正文", "| main | review | 折叠的 摘要 |" in body, True)
    check("附件名里的 # 编码成 %23", "(<./main/reviews/review %2312.md>)" in body, True)
    check("只排除 .gitkeep，不按点前缀过滤", "(<./main/reviews/.review.md>)" in body, True)
    check("单引号转义", nav.flat_fields("d: 'customer''s note'")["d"], "customer's note")

    # ⑦ 建树：上两层段坏了以参数错误退出
    proj = new_project()
    g = proj / "projects" / "modules" / "overview.md"
    g.write_bytes(g.read_bytes().replace(b"<!-- WORKFRAME:AUTO-INDEX:END -->", b""))
    params = proj / "p.json"
    params.write_text(json.dumps({"basics": [{"name": "甲", "subs": [{"name": "新"}]}]}, ensure_ascii=False),
                      encoding="utf-8", newline="")
    code, out, err = run("--params", params, project=proj)
    check("全局 overview 缺 END：建树退出 2", code, 2)

    # ⑧ 同一份文件里一段坏、另一段漂移：整份不写
    proj = new_project()
    ov = sub_dir(proj) / "overview.md"
    ov.write_bytes(ov.read_bytes().replace(b"<!-- WORKFRAME:AUTO-INDEX:END:requirements-index -->",
                                           b"<!-- WORKFRAME:AUTO-INDEX:END:typo -->"))
    _set_sync(proj, "2026-11-22T10:00:00+08:00", "abc12345")
    before = ov.read_bytes()
    code, _, _ = run("--refresh-index", project=proj)
    check("坏文件里健康段的漂移也不写：exit 2 且字节不变", (code, ov.read_bytes() == before), (2, True))

    # ⑨ --check 的差异行、缺 modules 目录、scope 拼错
    proj = new_project()
    add_requirement(proj, "需求一")
    code, res = run_json("--check", project=proj)
    upd = next(x for x in res["results"] if x["status"] == "update" and x["segment"] == "requirements-index")
    check("--check 给出旧行与新行", ("进行中 0 · 规划 1 · 已完成 0 · 已放弃 0 · 状态未知 0" in upd["new_lines"],
                                    "进行中 0 · 规划 0 · 已完成 0 · 已放弃 0 · 状态未知 0" in upd["old_lines"]), (True, True))
    check("--check 的行号指向差异行而不是 START 行", upd["line"] > 0 and (sub_dir(proj) / "overview.md").read_bytes()
          .decode("utf-8").splitlines()[upd["line"] - 1].startswith("进行中"), True)
    empty = TMP / "empty-proj"
    empty.mkdir()
    check("没有 projects/modules：--check 无法检查", run("--check", project=empty)[0], 2)
    check("scope 拼错：exit 2", run("--check", "--scope", "甲/错字", project=proj)[0], 2)

    # ⑩ 预览：段外矛盾句、转义竖线
    proj = new_project()
    r = add_requirement(proj, "需求一", status="done")
    write_prd(r, description="已写的摘要", status="review")   # 已编写：之后只改正文时新段内容不变
    ov = r / "overview.md"
    legacy = ("<!-- WORKFRAME:AUTO-INDEX:START -->\n| 子需求 | 状态 | 摘要 | PRD | 复盘 | 上线 |\n|---|---|---|---|---|---|\n"
              "| main | draft | 功能 A\\|B | [prd](./main/prd.md) | - | 暂缓 |\n<!-- WORKFRAME:AUTO-INDEX:END -->")
    text = ov.read_bytes().decode("utf-8")
    a, b = text.index("<!-- WORKFRAME:AUTO-INDEX:START -->"), text.index("<!-- WORKFRAME:AUTO-INDEX:END -->")
    text = text[:a] + legacy + text[b + len("<!-- WORKFRAME:AUTO-INDEX:END -->"):] + "\n本需求尚未启动。\n"
    ov.write_bytes(text.encode("utf-8"))
    _, prev = run_json("--convert-layout", project=proj)
    det = next(d for d in prev["details"] if d["path"].endswith("需求一/overview.md"))
    check("转义竖线不切列：自加列取到「暂缓」", det["extra_columns"], [{"column": "上线", "cells": [["main", "暂缓"]]}])
    check("段外矛盾句列出（done 却写尚未启动）",
          [(c["text"], c["status"]) for c in prev["contradictions"]], [("本需求尚未启动。", "done")])
    # ④ 只改 PRD 正文（新段内容不变）也让旧码失效
    prd = r / "main" / "prd.md"
    p_before = nav.plan(proj, convert=True)
    prd.write_bytes(prd.read_bytes() + "\n| AC-09 | 新增条件 |  |\n".encode("utf-8"))
    p_after = nav.plan(proj, convert=True)
    check("前提：只改 PRD 正文时各段新内容不变",
          [c.new for c in p_before.changes] == [c.new for c in p_after.changes], True)
    snap = snapshot(proj)
    code, out, _ = run("--convert-layout", "--apply", "--confirm", prev["confirm"], project=proj)
    check("只改 PRD 正文：旧码拒绝且零写入", (code, snapshot(proj) == snap), (2, True))
    code_before = nav.confirm_code(nav.plan(proj, convert=True))
    meta = r / "meta.yaml"
    meta.write_bytes(meta.read_bytes().replace(b"status: done", b"status: planning"))
    check("只改矛盾句读的 meta：确认码也变", nav.confirm_code(nav.plan(proj, convert=True)) != code_before, True)

    # ⑪ JSON 失败回执；示例图冲突回执
    proj = new_project()
    for label, args in (("空 scope", ("--check", "--scope")), ("错误确认码", ("--convert-layout", "--apply", "--confirm", "x")),
                        ("--check --apply", ("--check", "--apply"))):
        code, res = run_json(*args, project=proj)
        check(f"{label}：exit 2 且 stdout 有 JSON error", (code, "error" in res and res.get("schema") == nav.SCHEMA), (2, True))
    bov = proj / "projects" / "modules" / "甲" / "overview.md"
    bov.write_bytes(bov.read_bytes().decode("utf-8").replace(nav.EXAMPLE_NOTE, nav.EXAMPLE_LEAD + "\n\n" + nav.EXAMPLE_DIAGRAM)
                    .encode("utf-8"))
    choice = "projects/modules/甲/overview.md=replace-example"
    p = nav.plan(proj, convert=True)
    bov.write_bytes(bov.read_bytes() + b"\n")
    results = {r.path: r.status for r in nav.apply(p, {"projects/modules/甲/overview.md": "replace-example"})}
    rows = module_init._nav_rows(p, {k: nav.Result(k, v) for k, v in results.items()})
    ex = [x for x in rows if x["segment"] == "example-diagram"]
    check("只有示例图要写且冲突：回执报 conflict", [x["status"] for x in ex], ["conflict"])
    del choice

    # ⑫ 坏源文件不吞掉同批健康文件
    proj = new_project(subs=("乙", "丙"))
    r = add_requirement(proj, "坏", sub="乙")
    (r / "meta.yaml").write_bytes(b"status: \xff\xfe\n")
    add_requirement(proj, "好", sub="丙")
    code, res = run_json("--check", project=proj)
    probs = [x["reason"].split("：")[0] for x in res["results"] if x["status"] == "problem"]
    drift = [x for x in res["results"] if x["status"] == "update" and "/丙/" in x["path"]]
    check("坏 meta.yaml：报 unreadable-source、不抛异常", ("unreadable-source" in probs, code), (True, 2))
    check("同批健康文件的漂移照样返回", bool(drift), True)

    # ⑬ 旧版式转换：普通行「；」后半是同一句旧摘要，不当尾注；v2 刷新照常保留尾注
    docs = {"架构": "新摘要", "API": "新 API", "数据模型": nav.UNWRITTEN_TOKEN, "代码索引": nav.NO_SUMMARY_TOKEN}
    legacy_body = (nav.NOTICE + "基于 `current-state/` 4 个文件提炼摘要。\n\n"
                   "- **架构**：两条线；本地只做解析（confidence=high）\n"
                   "- **API**：_（待 code-to-doc 生成 api-surface.md 后摘要；高风险档按需）_\n")
    rows = nav.render("current-state-summary", {"old_body": legacy_body, "docs": docs,
                                                "sync_date": nav.UNSYNCED_TOKEN, "sync_ref": ""}).splitlines()
    check("旧版式转换：旧行「；」后半随旧摘要一起换掉", "- **架构**：新摘要" in rows, True)
    check("旧版式转换：斜体占位里的注记仍挪成尾注", "- **API**：新 API；高风险档按需" in rows, True)
    v2_body = nav.CS_NOTICE_V2 + "\n\n- **架构**：旧 token；人写尾注\n"
    rows = nav.render("current-state-summary", {"old_body": v2_body, "docs": docs,
                                                "sync_date": nav.UNSYNCED_TOKEN, "sync_ref": ""}).splitlines()
    check("v2 刷新：尾注原样保留", "- **架构**：新摘要；人写尾注" in rows, True)
    check("description 超 80 字截断带省略号",
          nav.current_state_token("written", {"description": "长" * 90}), "长" * 80 + "…")


def main():
    try:
        for fn in (test_fresh_tree_matches_templates, test_attachments_and_summary, test_status_tables_and_missing_prd,
                   test_convert_and_confirm_code, test_example_diagram_choice, test_problems_isolated,
                   test_legacy_project_new_sub, test_segments_edge_cases, test_check_zero_write_and_conflict,
                   test_sync_line, test_current_state_rows, test_bytes_preserved, test_unstamped_reported,
                   test_review_round1):
            try:
                fn()
            except Exception as e:
                import traceback
                traceback.print_exc()
                FAILURES.append(f"{fn.__name__} 抛异常: {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    if FAILURES:
        print(f"\n{len(FAILURES)} failed: {FAILURES}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
