#!/usr/bin/env python3
"""「未编写」状态的常驻单测：`plugins/core/scripts/_doc_state.py` 判定 ＋ 读者与写入入口。

判定错了的两个方向都静默：判宽了（把正文当骨架）→ 查询层与 查询客户端 把真实正文藏成「未编写」、
prd-writer 不读它、归档检查跳过深检；判窄了 → 骨架被当成实现现状读。所以每组都看**多个读者**：

- 三组正交变化（只变字段 / 只变正文 / 只变 status 或 ref）：判定、查询布尔、`--doc-state`、归档深检
- 盖戳护栏：`true ＋ 真实 AC`、坏摘要、摘要不符、重复键必须拒；幂等
- 字节保全：BOM、CRLF、无尾换行、frontmatter 不闭合——有效状态与原文件字节同时断言；
  往返基线是「去掉该字段后的原字节」
- 归一契约每一步各有一条只靠它成立的断言（换行统一 / 行尾空白 / 首尾空行）
- 骨架比对是全文有序：只留 H1、重复末行、交换两行都不算骨架
- 归档：留着旧提示句「由 prd-writer skill 生成」并写非法 status 的 PRD 照样被深检
- 入口：脚本建子模块后现状四件已盖戳；复制 PRD 模板后 `--mark-unwritten`；写一次正文自动解除；`--unstamp`
- 存量盘点：六类各一例、确认码失配零写入、二次 apply 零差异、BOM / CRLF / 无尾换行插行后去掉该行与原字节一致
- 出厂模板契约：五份模板各有 `unwritten: true`，PRD 骨架的 AC 行是「未编写」
- 行级读数与 YAML 值对账：`H#typo`、带引号的同名键判 unstamped；多行字段插行 / 去戳后 YAML 仍合法、
  其余字段原样，写坏时复核拒绝；摘要有一条独立于实现的固定向量
- 历史骨架常量按 git 历史里模板的摘要锁定；每版纯壳认得、填一节不认
- 盘点边界：四件以外的现状文件进分母、摘要失配单列、无 frontmatter 的骨架报异常、`+++` 开头的正文行
  照列差异、只改 frontmatter 确认码也变、确认后字节变了整批零写入、几种模式同时给即拒
- 查询子需求布尔为真的一例；归档：保留旧摘要只改正文、`#typo`、带 BOM 的成稿

不在本套件：导航段与 close-check 的 `unstamped` 告警（它们的实现在后续步骤，届时各自的套件覆盖）。

执行：python tools/test_doc_state.py（退出 0 且打印 all passed = 通过）
"""

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
CORE = REPO_ROOT / "plugins" / "core"
SCRIPTS = CORE / "scripts"
TEMPLATES = CORE / "templates" / "modules-template"
MODULE_INIT = SCRIPTS / "module_init.py"
CHECK_ARCHIVE = CORE / "skills" / "requirement-archiving" / "scripts" / "check_archive.py"
sys.path.insert(0, str(SCRIPTS))

import _doc_state as ds  # noqa: E402

FAILURES = []
TMP = Path(tempfile.mkdtemp(prefix="wf-docstate-"))


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


def render(kind, name="订单"):
    path = TEMPLATES / ("requirement/main/prd.md" if kind == "prd" else f"sub-module/current-state/{kind}.md")
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    for k, v in {"SUB_NAME": name, "REQ_TITLE": name, "REQ_SLUG": "r1", "SUB_REQ_SLUG": "main",
                 "TODAY": "2026-11-22", "NOW_ISO": "2026-11-22T00:00:00+08:00", "MODULE_PATH": "甲/乙"}.items():
        text = text.replace("{{" + k + "}}", v)
    return text.encode("utf-8")


def drop_mark_line(raw):
    lines = raw.splitlines(keepends=True)
    return b"".join(l for l in lines if not l.startswith(b"unwritten"))


# ---------------------------------------------------------------- 判定


def test_states():
    print("[判定三态]")
    raw = render("code-map")
    check("模板字面 true → unstamped", ds.effective_state(raw), "unstamped")
    stamped = ds.stamp(raw, "code-map")
    check("盖戳后 → unwritten", ds.effective_state(stamped), "unwritten")
    check("值形如 sha256:<64hex>", bool(ds.DIGEST_RE.fullmatch(ds.mark_value(stamped))), True)
    check("无 frontmatter → written", ds.effective_state(b"# t\n\nbody\n"), "written")
    check("无该键 → written", ds.effective_state(drop_mark_line(stamped)), "written")
    other = stamped.replace(ds.mark_value(stamped).encode(), b"sha256:" + b"0" * 64)
    check("合法摘要但不等 → written", ds.effective_state(other), "written")
    for label, value in (("空值", b""), ("拼错", b"ture"), ("短摘要", b"sha256:abc"),
                         ("大写十六进制", ds.mark_value(stamped).upper().encode())):
        bad = stamped.replace(b'"' + ds.mark_value(stamped).encode() + b'"', value)
        check(f"{label} → unstamped", ds.effective_state(bad), "unstamped")
    dup = stamped.replace(b"status: draft\n", b"status: draft\n" + [l for l in stamped.split(b"\n")
                                                                     if l.startswith(b"unwritten")][0] + b"\n")
    check("重复键 → unstamped", ds.effective_state(dup), "unstamped")
    check("重复键 mark_value 为 DUPLICATE", ds.mark_value(dup) is ds.DUPLICATE, True)
    indented = stamped.replace(b"\nunwritten:", b"\n  unwritten:")
    check("缩进的同名键不是顶层字段 → written", ds.effective_state(indented), "written")
    unclosed = "---\nunwritten: true\ntitle: x\n# 正文\n".encode("utf-8")
    check("frontmatter 不闭合 → written", ds.effective_state(unclosed), "written")


def test_orthogonal():
    print("[三组正交变化：判定 / 查询布尔 / --doc-state / 归档深检]")
    import _query_modules
    base = ds.stamp(render("prd"), "prd")
    variants = {
        "只变字段（updated / description）": (base.replace(b'updated: "2026-11-22T00:00:00+08:00"',
                                                        b'updated: "2026-12-01T00:00:00+08:00"')
                                            .replace("（占位骨架".encode(), "（改过的摘要".encode()), "unwritten"),
        "只变 status": (base.replace(b"status: draft", b"status: approved"), "unwritten"),
        "只变正文": (base.replace("| AC-01 | _（未编写）_ |  |".encode(), "| AC-01 | 能下单 | 下单 |".encode()),
                 "written"),
    }
    proj = TMP / "orth"
    req = proj / "projects/modules/甲/乙/requirements"
    for i, (label, (raw, want)) in enumerate(variants.items()):
        check(f"{label}：判定", ds.effective_state(raw), want)
        doc = _query_modules.document_text(raw.decode("utf-8"), "x/prd.md", "other", [])
        check(f"{label}：查询 unwritten", doc["unwritten"], want == "unwritten")
        p = req / f"r{i}" / "main" / "prd.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        rc, out, _ = run("--doc-state", p, project=proj)
        check(f"{label}：--doc-state", (rc, out.split("\t")[0]), (0, want))
    rc, out, _ = run("--doc-state", req / "r0/main/prd.md", "--json", project=proj)
    check("--doc-state --json 是数组且带 state", json.loads(out)[0]["state"], "unwritten")
    unstamped = ds.unstamp(base).replace(b"status: draft\n", b"status: draft\nunwritten: true\n")
    doc = _query_modules.document_text(unstamped.decode("utf-8"), "x/prd.md", "other", [])
    check("字面 true：查询按已编写（unwritten=False）", doc["unwritten"], False)
    broken = base.replace(b"tags: []", b"tags: [")
    doc = _query_modules.document_text(broken.decode("utf-8"), "x/prd.md", "other", [])
    check("frontmatter YAML 坏：查询 unwritten=False 且留 meta_error",
          (doc["unwritten"], doc["meta_error"] is not None), (False, True))


def test_stamp_guard():
    print("[盖戳护栏]")
    raw = render("prd")
    real = raw.replace("| AC-01 | _（未编写）_ |  |".encode(), "| AC-01 | 能下单 | 下单 |".encode())

    def code_of(fn):
        try:
            fn()
            return None
        except ds.StampError as e:
            return e.code
    check("true ＋ 真实 AC → not-skeleton", code_of(lambda: ds.stamp(real, "prd")), "not-skeleton")
    stamped = ds.stamp(raw, "prd")
    check("已盖戳再盖 → 原样（幂等）", ds.stamp(stamped, "prd"), stamped)
    changed = stamped.replace("| AC-01 | _（未编写）_ |  |".encode(), "| AC-01 | 能下单 | 下单 |".encode())
    check("摘要不符 → digest-mismatch", code_of(lambda: ds.stamp(changed, "prd")), "digest-mismatch")
    bad = stamped.replace(ds.mark_value(stamped).encode(), b"sha256:zz")
    check("坏摘要 → bad-value", code_of(lambda: ds.stamp(bad, "prd")), "bad-value")
    dup = raw.replace(b"status: draft\n", b"status: draft\nunwritten: true\n")
    check("重复键 → duplicate", code_of(lambda: ds.stamp(dup, "prd")), "duplicate")
    check("无该键 → no-mark", code_of(lambda: ds.stamp(ds.unstamp(raw), "prd")), "no-mark")
    check("无 frontmatter → no-frontmatter", code_of(lambda: ds.stamp(b"# x\n", "prd")), "no-frontmatter")
    check("盖戳保留行尾注释", b"# \xe6\x9c\xaa\xe7\xbc\x96\xe5\x86\x99" in
          [l for l in stamped.split(b"\n") if l.startswith(b"unwritten")][0], True)


def test_bytes():
    print("[字节保全]")
    raw = render("architecture")
    forms = {
        "LF": raw,
        "BOM": ds.BOM + raw,
        "CRLF": raw.replace(b"\n", b"\r\n"),
        "无尾换行": raw.rstrip(b"\n"),
        "BOM＋CRLF＋无尾换行": ds.BOM + raw.replace(b"\n", b"\r\n").rstrip(b"\r\n"),
    }
    for label, form in forms.items():
        stamped = ds.stamp(form, "architecture")
        check(f"{label}：盖戳后 unwritten", ds.effective_state(stamped), "unwritten")
        check(f"{label}：只改了字段那一行（去掉该行与原文去掉该行一致）",
              drop_mark_line(stamped), drop_mark_line(form))
        check(f"{label}：unstamp 后 = 原字节去掉该字段", ds.unstamp(stamped), drop_mark_line(form))
        check(f"{label}：摘要与 LF 版相同", ds.body_digest(form), ds.body_digest(raw))
    crlf_stamped = ds.stamp(forms["CRLF"], "architecture")
    line = [l for l in crlf_stamped.split(b"\n") if l.startswith(b"unwritten")][0]
    check("CRLF 文件里改写后的字段行仍以 CR 结尾", line.endswith(b"\r"), True)


def test_normalization_steps():
    print("[归一契约：每一步各一条只靠它成立的断言]")
    head = b"---\nunwritten: true\n---\n"
    a = head + b"# T\n\nline\n"
    check("换行统一：CRLF 正文与 LF 正文摘要相同", ds.body_digest(head + b"# T\r\n\r\nline\r\n"), ds.body_digest(a))
    check("换行统一：单独 CR 也归一", ds.body_digest(head + b"# T\r\rline\r"), ds.body_digest(a))
    check("行尾空白：去掉", ds.body_digest(head + b"# T  \n\t\nline \t\n"), ds.body_digest(a))
    check("首尾空行：去掉", ds.body_digest(head + b"\n\n# T\n\nline\n\n\n"), ds.body_digest(a))
    check("行首空白不去（缩进是正文）", ds.body_digest(head + b"# T\n\n  line\n") != ds.body_digest(a), True)
    check("中间空行不去", ds.body_digest(head + b"# T\nline\n") != ds.body_digest(a), True)
    check("frontmatter 不计入摘要", ds.body_digest(b"---\nx: 1\n---\n# T\n\nline\n"), ds.body_digest(a))


def test_skeleton_order():
    print("[骨架比对是全文有序]")
    raw = render("data-model")
    parts = ds.split(raw)
    fm, body = raw[:parts.body[0]], raw[parts.body[0]:]
    lines = body.split(b"\n")
    check("原样 → 骨架", ds.matches_skeleton(raw, "data-model"), True)
    check("只留 H1 → 不是", ds.matches_skeleton(fm + lines[0] + b"\n", "data-model"), False)
    nonblank = [i for i, l in enumerate(lines) if l.strip()]
    dup = lines + [lines[nonblank[-1]]]
    check("重复末行 → 不是", ds.matches_skeleton(fm + b"\n".join(dup), "data-model"), False)
    i, j = nonblank[2], nonblank[3]
    swapped = list(lines)
    swapped[i], swapped[j] = swapped[j], swapped[i]
    check("交换两行 → 不是", ds.matches_skeleton(fm + b"\n".join(swapped), "data-model"), False)
    check("渲染变量位通配（换子模块名仍是骨架）", ds.matches_skeleton(render("data-model", "别的名字"), "data-model"), True)
    check("类型不对 → 不是", ds.matches_skeleton(raw, "code-map"), False)
    import _skeletons
    rid, hist = _skeletons.HISTORICAL["prd"][0]
    old = ("---\nstatus: draft\n---\n" + hist.replace("{{REQ_TITLE}}", "T").replace("{{REQ_SLUG}}", "s")
           .replace("{{TODAY}}", "2026-01-01")).encode("utf-8")
    check("历史版 PRD 骨架认得", ds.match_skeleton(old, "prd"), f"history:{rid}")


def test_archive():
    print("[归档深检]")
    proj = TMP / "arch"
    basic = proj / "projects/modules/甲"
    req = basic / "乙/requirements/r1/main"
    req.mkdir(parents=True)
    legacy = render("prd").replace(b"status: draft", b"status: bogus")
    legacy = ds.unstamp(legacy)
    assert "由 `prd-writer` skill 生成".encode() in legacy
    (req / "prd.md").write_bytes(legacy)

    def archive():
        r = subprocess.run([sys.executable, str(CHECK_ARCHIVE), str(basic)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        return r.returncode, r.stdout
    rc, out = archive()
    check("留旧提示句 ＋ 非法 status：深检照报", ("status 取值异常 bogus" in out, rc), (True, 1))
    check("深检计数 1 份", "检查 PRD 1 份" in out, True)
    (req / "prd.md").write_bytes(ds.stamp(render("prd"), "prd").replace(b"status: draft", b"status: bogus"))
    rc, out = archive()
    check("盖过戳的骨架：跳过深检（0 份、不报 status）", ("检查 PRD 0 份" in out, "status 取值异常" in out),
          (True, False))
    (req / "prd.md").write_bytes(render("prd").replace(b"status: draft", b"status: bogus"))
    rc, out = archive()
    check("字面 true（未盖戳）：照检", "status 取值异常 bogus" in out, True)


def test_entrypoints():
    print("[入口：建子模块 / 复制模板盖戳 / 写正文自动解除 / --unstamp]")
    proj = TMP / "entry"
    (proj / "projects").mkdir(parents=True)
    params = TMP / "tree.json"
    params.write_bytes(json.dumps({"basics": [{"name": "甲", "subs": [{"name": "乙"}]}]},
                                  ensure_ascii=False).encode("utf-8"))
    rc, _, err = run("--params", params, project=proj)
    check("建树 exit 0", (rc, err), (0, ""))
    cs = proj / "projects/modules/甲/乙/current-state"
    for kind in ds.CURRENT_STATE_KINDS:
        check(f"脚本建出的 {kind} 已盖戳", ds.effective_state((cs / f"{kind}.md").read_bytes()), "unwritten")
    prd = proj / "projects/modules/甲/乙/requirements/r1/main/prd.md"
    prd.parent.mkdir(parents=True)
    prd.write_bytes(render("prd"))
    rc, out, _ = run("--mark-unwritten", prd, project=proj)
    check("复制模板后 --mark-unwritten：stamped", (rc, out.split()[1]), (0, "stamped"))
    rc, out, _ = run("--mark-unwritten", prd, project=proj)
    check("再跑：already", (rc, out.split()[1]), (0, "already"))
    prd.write_bytes(prd.read_bytes().replace("| AC-01 | _（未编写）_ |  |".encode(), "| AC-01 | 能下单 | 下单 |".encode()))
    rc, out, _ = run("--doc-state", prd, project=proj)
    check("写一次正文自动转 written（没删字段）", out.split("\t")[0], "written")
    filled = proj / "projects/modules/甲/乙/requirements/r2/main/prd.md"
    filled.parent.mkdir(parents=True)
    filled.write_bytes(render("prd").replace("| AC-01 | _（未编写）_ |  |".encode(), "| AC-01 | 真 | x |".encode()))
    before = filled.read_bytes()
    rc, out, _ = run("--mark-unwritten", filled, project=proj)
    check("已填内容再盖戳：拒绝 exit 1、文件不动", (rc, "not-skeleton" in out, filled.read_bytes() == before),
          (1, True, True))
    rc, out, _ = run("--unstamp", prd, project=proj)
    check("--unstamp：removed、字段消失", (rc, out.split()[1], ds.mark_value(prd.read_bytes())), (0, "removed", None))
    rc, out, _ = run("--unstamp", prd, project=proj)
    check("--unstamp 再跑：absent", out.split()[1], "absent")
    rc, _, err = run("--mark-unwritten", TMP / "tree.json", project=proj)
    check("项目外路径：拒绝 exit 2", rc, 2)


def test_scan():
    print("[存量盘点]")
    proj = TMP / "scan"
    m = proj / "projects/modules/甲/乙"
    cs = m / "current-state"
    cs.mkdir(parents=True)
    pure = ds.unstamp(render("architecture"))
    files = {
        cs / "architecture.md": pure,                                                       # 纯骨架（无字段）
        cs / "api-surface.md": ds.BOM + ds.unstamp(render("api-surface")).replace(b"\n", b"\r\n"),  # 纯骨架 BOM＋CRLF
        cs / "code-map.md": ds.unstamp(render("code-map")).rstrip(b"\n"),                   # 纯骨架 无尾换行
        cs / "data-model.md": ds.unstamp(render("data-model")) + "- 2026-11-01 真实维护记录\n".encode(),  # 疑似
    }
    req = m / "requirements"
    files[req / "a/main/prd.md"] = b"---\ntype: prd\n---\n# A\n\n" + "真实需求正文\n".encode()   # 已编写
    files[req / "b/main/prd.md"] = b"---\ntype: prd\n---\n"                                   # 无信号
    files[req / "c/main/prd.md"] = ds.stamp(render("prd"), "prd")                            # 已盖戳
    files[req / "d/main/prd.md"] = render("prd").replace(b"status: draft\n", b"status: draft\nunwritten: x\n")  # 异常
    files[req / "e/main/prd.md"] = render("prd")                                              # 纯骨架（字面 true）
    for p, raw in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    rc, out, _ = run("--mark-unwritten", "--scan", "--json", project=proj)
    data = json.loads(out)
    cls = {f["path"].split("modules/")[1]: f["class"] for f in data["files"]}
    check("分母完整：每份都在输出里", len(data["files"]), len(files))
    want = {"甲/乙/current-state/architecture.md": "纯骨架", "甲/乙/current-state/api-surface.md": "纯骨架",
            "甲/乙/current-state/code-map.md": "纯骨架", "甲/乙/current-state/data-model.md": "疑似",
            "甲/乙/requirements/a/main/prd.md": "已编写", "甲/乙/requirements/b/main/prd.md": "无信号",
            "甲/乙/requirements/c/main/prd.md": "已盖戳", "甲/乙/requirements/d/main/prd.md": "异常",
            "甲/乙/requirements/e/main/prd.md": "纯骨架"}
    check("六类各就其位", cls, want)
    suspect = next(f for f in data["files"] if f["class"] == "疑似")
    check("疑似列出差异行", suspect["diff"], ["+- 2026-11-01 真实维护记录"])
    snapshot = {p: p.read_bytes() for p in files}
    rc, _, err = run("--mark-unwritten", "--scan", "--apply", "--confirm", "0" * 16, project=proj)
    check("确认码不符：exit 2、零写入", (rc, all(p.read_bytes() == b for p, b in snapshot.items())), (2, True))
    rc, _, _ = run("--mark-unwritten", "--scan", "--apply", "--confirm", data["confirm"], project=proj)
    check("按确认码 apply：exit 0", rc, 0)
    for p in (cs / "architecture.md", cs / "api-surface.md", cs / "code-map.md", req / "e/main/prd.md"):
        check(f"{p.name}（{p.parent.parent.name}）盖戳后 unwritten", ds.effective_state(p.read_bytes()), "unwritten")
        check(f"{p.name}（{p.parent.parent.name}）去掉新增行 = 原字节去掉字段", drop_mark_line(p.read_bytes()),
              drop_mark_line(snapshot[p]))
    line = [l for l in (cs / "api-surface.md").read_bytes().splitlines(keepends=True) if l.startswith(b"unwritten")]
    check("CRLF 文件插入的字段行以 CRLF 结尾", [l.endswith(b"\r\n") for l in line], [True])
    for p in (cs / "data-model.md", req / "a/main/prd.md", req / "b/main/prd.md", req / "c/main/prd.md",
              req / "d/main/prd.md"):
        check(f"非候选 {p.parent.parent.name}/{p.name} 一字不动", p.read_bytes(), snapshot[p])
    rc, out, _ = run("--mark-unwritten", "--scan", "--json", project=proj)
    again = json.loads(out)
    after = {p: p.read_bytes() for p in files}
    rc2, _, _ = run("--mark-unwritten", "--scan", "--apply", "--confirm", again["confirm"], project=proj)
    check("二次 apply 零差异", (rc2, all(p.read_bytes() == b for p, b in after.items())), (0, True))
    check("二次预览零候选", sum(1 for f in again["files"] if f["class"] == "纯骨架"), 0)
    sample = TMP / "old-data-model.md"
    sample.write_bytes(snapshot[cs / "data-model.md"])
    rc, out, _ = run("--mark-unwritten", "--scan", "--json", "--extra-skeleton", f"data-model={sample}", project=proj)
    data = json.loads(out)
    check("--extra-skeleton 样本：疑似转纯骨架、预览带样本全文",
          ([f["class"] for f in data["files"] if f["path"].endswith("data-model.md")], len(data["extras"])),
          (["纯骨架"], 1))
    check("换样本后旧确认码失效", data["confirm"] != again["confirm"], True)


def test_yaml_agreement():
    print("[行级读数与 YAML 值对账]")
    import _query_modules
    raw = render("prd")
    stamped = ds.stamp(raw, "prd")
    h = ds.mark_value(stamped).encode()
    check("固定向量：正文 abc 的摘要（独立于实现的 sha256('abc')）",
          ds.body_digest(b"---\nx: 1\n---\n\nabc  \r\n"),
          "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    typo = stamped.replace(b'"' + h + b'"', h + b"#typo")
    check("摘要后紧贴 #typo（YAML 里是一整个字符串）→ unstamped", ds.effective_state(typo), "unstamped")
    quoted_dup = stamped.replace(b"tags: []", b"tags: []\n'unwritten': false")
    check("后面再写一个带引号的同名键 → unstamped", ds.effective_state(quoted_dup), "unstamped")
    for label, v in (("#typo", typo), ("带引号的重复键", quoted_dup)):
        doc = _query_modules.document_text(v.decode("utf-8"), "x/prd.md", "other", [])
        check(f"{label}：查询 unwritten=False", doc["unwritten"], False)
    check("YAML 值与行内一致时照常 unwritten", ds.effective_state(stamped, mark=h.decode()), "unwritten")
    check("调用方传入 ABSENT 而行里有字段 → unstamped", ds.effective_state(stamped, mark=ds.ABSENT), "unstamped")

    def code_of(fn):
        try:
            fn()
            return None
        except ds.StampError as e:
            return e.code
    check("true#c（YAML 里是字符串）盖戳 → bad-value",
          code_of(lambda: ds.stamp(raw.replace(b"unwritten: true", b"unwritten: true#c"), "prd")), "bad-value")
    import yaml

    def fm(b):
        p = ds.split(b)
        return yaml.safe_load(b[p.fm_lines[0][0]:p.fm_close].decode("utf-8"))
    folded = ds.unstamp(raw).replace(b"status: draft", b"status: >-\n  draft")
    ins = ds.insert_stamp(folded, "prd")
    check("status 是两行折叠值：插行后 YAML 仍合法、status 仍是 draft、已盖戳",
          (fm(ins)["status"], ds.effective_state(ins)), ("draft", "unwritten"))
    flow = ds.unstamp(raw).replace(b"status: draft", b"status: [draft,\n]")
    check("status 是跨行的流式序列（续行不缩进）：插行会写坏 → 复核拒绝 yaml-changed",
          code_of(lambda: ds.insert_stamp(flow, "prd")), "yaml-changed")
    nested = ds.unstamp(raw).replace(b"status: draft", b"status: draft\nunwritten:\n  nested: x")
    out = ds.unstamp(nested)
    check("字段带缩进子行：unstamp 连子行一起删、其余字段原样",
          fm(out), {k: v for k, v in fm(nested).items() if k != "unwritten"})
    check("字段带缩进子行：unstamp 只删了那几行", out, ds.unstamp(raw))
    whole_cr = stamped.replace(b"\n", b"\r")
    doc = _query_modules.document_text(whole_cr.decode("utf-8"), "x/prd.md", "other", [])
    check("整份单 CR 换行：查询与字节判定一致（都认出 frontmatter，都是 unwritten）",
          (doc["unwritten"], ds.effective_state(whole_cr)), (True, "unwritten"))


def test_historical_locked():
    print("[历史骨架常量锁定（期望值取自 git 历史里的模板，不取自常量本身）]")
    import hashlib
    import _skeletons
    want = {"7e47334": "acee8444d614351719a25cd87d65ce6394a8e629deb65eec09b328eed923b072",
            "0bee31f": "cef195f41d729035f53bc0bc8422061744e36567befda938cb260dbb48504ac2"}
    got = {rid: hashlib.sha256(ds.normalize_body(body).encode("utf-8")).hexdigest()
           for rid, body in _skeletons.HISTORICAL["prd"]}
    check("PRD 历史两版正文逐字未变", got, want)
    check("五类都有骨架可比（类型集合固定）", tuple(ds.KINDS),
          ("architecture", "api-surface", "code-map", "data-model", "prd"))
    for rid, body in _skeletons.HISTORICAL["prd"]:
        filled = body.replace("{{REQ_TITLE}}", "T").replace("{{REQ_SLUG}}", "s").replace("{{TODAY}}", "2026-01-01")
        pure = ("---\nstatus: draft\n---\n" + filled).encode("utf-8")
        one = pure + "\n用户补写的一段真实需求正文。\n".encode("utf-8")
        check(f"history:{rid} 纯壳 → 认得", ds.match_skeleton(pure, "prd"), f"history:{rid}")
        check(f"history:{rid} 填了一节 → 不是骨架", ds.match_skeleton(one, "prd"), None)


def test_scan_review_round():
    print("[存量盘点：边界]")
    proj = TMP / "scan2"
    m = proj / "projects/modules/甲/乙"
    cs = m / "current-state"
    cs.mkdir(parents=True)
    stamped = ds.stamp(render("architecture"), "architecture")
    files = {
        cs / "deployment.md": "---\ntype: x\n---\n# 部署\n\n真实内容\n".encode("utf-8"),
        cs / "architecture.md": stamped.replace(b"\n# ", "\n# 改过 ".encode(), 1),       # 摘要失配
        cs / "api-surface.md": ds.unstamp(render("api-surface")).split(b"---\n", 2)[2],     # 骨架无 frontmatter
        cs / "data-model.md": ds.unstamp(render("data-model")) + b"+++ customer line\n",    # 疑似
        cs / "code-map.md": ds.unstamp(render("code-map")),                                 # 纯骨架
    }
    req = m / "requirements"
    files[req / "a/main/prd.md"] = ds.unstamp(render("prd"))                               # 纯骨架
    for p, raw in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
    rc, out, _ = run("--mark-unwritten", "--scan", "--json", project=proj)
    data = json.loads(out)
    cls = {f["path"].split("current-state/")[-1].split("requirements/")[-1]: f["class"] for f in data["files"]}
    check("分母含四件以外的 current-state 文件；摘要失配单列；无 frontmatter 的骨架是异常",
          cls, {"deployment.md": "已编写", "architecture.md": "摘要失配", "api-surface.md": "异常",
                "data-model.md": "疑似", "code-map.md": "纯骨架", "a/main/prd.md": "纯骨架"})
    suspect = next(f for f in data["files"] if f["class"] == "疑似")
    check("疑似：以 +++ 开头的真实正文行照样列为差异", suspect["diff"], ["++++ customer line"])
    snapshot = {p: p.read_bytes() for p in files}
    cm = cs / "code-map.md"
    cm.write_bytes(cm.read_bytes().replace(b"status:", b"# preview changed\nstatus:", 1))
    rc, out2, _ = run("--mark-unwritten", "--scan", "--json", project=proj)
    check("只改候选的 frontmatter：确认码随之改变", json.loads(out2)["confirm"] != data["confirm"], True)
    rc, _, _ = run("--mark-unwritten", "--scan", "--apply", "--confirm", data["confirm"], project=proj)
    check("旧码 apply：exit 2、另一份候选也没写", (rc, (req / "a/main/prd.md").read_bytes()),
          (2, snapshot[req / "a/main/prd.md"]))
    import module_init
    proj = proj.resolve()                              # CLI 入口同样先 resolve 项目根
    fresh = module_init.scan_unwritten(proj, {})
    cm.write_bytes(snapshot[cm])                       # 预览之后、写入之前文件变了
    results = module_init.apply_scan(proj, fresh, {})
    check("确认后候选字节变了：整批零写入",
          (sorted(r["status"] for r in results), (req / "a/main/prd.md").read_bytes() == snapshot[req / "a/main/prd.md"]),
          (["refused", "skipped"], True))
    sample = TMP / "sample-data-model.md"
    sample.write_bytes(snapshot[cs / "data-model.md"])
    rc, out, _ = run("--mark-unwritten", "--scan", "--json", "--extra-skeleton", f"data-model={sample}", project=proj)
    check("预览里的额外样本带文件原文（含 frontmatter）", json.loads(out)["extras"][0]["text"],
          snapshot[cs / "data-model.md"].decode("utf-8"))
    (req / "a/main/prd.md").write_bytes(ds.stamp(render("prd"), "prd"))     # 带有效戳：写端一被调就看得出
    before = (req / "a/main/prd.md").read_bytes()
    for combo in (("--unstamp", req / "a/main/prd.md", "--mark-unwritten", "--scan"),
                  ("--unstamp", req / "a/main/prd.md", "--scan"),
                  ("--refresh-index", "--scan")):
        rc, _, _ = run(*combo, project=proj)
        check(f"参数组合 {' '.join(str(c).split('modules/')[-1] for c in combo)}：exit 2、零写入",
              (rc, (req / "a/main/prd.md").read_bytes()), (2, before))
    rc, out, _ = run("--unstamp", req / "nope/main/prd.md", project=proj)
    check("--unstamp 指向不存在的文件：missing 且 exit 1", (rc, out.split()[1]), (1, "missing"))


def test_query_subs_and_archive_round():
    print("[查询子需求布尔 ＋ 归档边界]")
    import _query_modules
    proj = TMP / "qa2"
    basic = proj / "projects/modules/甲"
    sub = basic / "乙"
    good = sub / "requirements/r1/main/prd.md"
    good.parent.mkdir(parents=True)
    good.write_bytes(ds.stamp(render("prd"), "prd"))

    class Err(Exception):
        pass
    reqs = _query_modules._requirements(sub, proj, [], Err)
    check("子需求是盖过戳的骨架：subs[].unwritten 为 True", reqs[0]["subs"][0]["unwritten"], True)

    def archive():
        r = subprocess.run([sys.executable, str(CHECK_ARCHIVE), str(basic)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        return r.returncode, r.stdout
    kept = ds.stamp(render("prd"), "prd").replace("| AC-01 | _（未编写）_ |  |".encode(),
                                                   "| AC-01 | 能下单 | 下单 |".encode())
    good.write_bytes(kept.replace(b"status: draft", b"status: bogus"))
    rc, out = archive()
    check("保留旧摘要只改正文 ＋ 非法 status：深检照报", "status 取值异常 bogus" in out, True)
    h = ds.mark_value(kept).encode()
    good.write_bytes(ds.stamp(render("prd"), "prd").replace(b'"' + ds.mark_value(ds.stamp(render("prd"), "prd")).encode()
                                                           + b'"', h + b"#typo").replace(b"status: draft", b"status: bogus"))
    rc, out = archive()
    check("摘要后紧贴 #typo ＋ 非法 status：深检照报", "status 取值异常 bogus" in out, True)
    good.write_bytes(ds.unstamp(kept))
    plain = archive()
    good.write_bytes(ds.BOM + ds.unstamp(kept))
    rc, out = archive()
    check("带 BOM 的完整成稿：深检 1 份，结果与不带 BOM 的同一份逐字相同",
          ("检查 PRD 1 份" in out, (rc, out)), (True, plain))
    good.write_bytes(ds.BOM + ds.unstamp(kept).replace(b"status: draft", b"status: bogus"))
    rc, out = archive()
    check("带 BOM 的成稿 ＋ 非法 status：深检照报", ("status 取值异常 bogus" in out, rc), (True, 1))
    s0 = ds.stamp(render("prd"), "prd")
    h0 = ds.mark_value(s0)
    rev = s0.replace(f'unwritten: "{h0}"'.encode(), f"'unwritten': false\nunwritten: \"{h0}\"".encode())
    good.write_bytes(rev.replace(b"status: draft", b"status: bogus"))
    rc, out = archive()
    check("先写带引号的同名键、再写裸键 ＋ 非法 status：深检照报", ("status 取值异常 bogus" in out, rc), (True, 1))
    good.write_bytes(s0.replace(b"\n", b"\r"))
    doc, _ = _query_modules._document_source(good, proj, "other", [])
    rc, out, _ = run("--doc-state", good, project=proj)
    check("整份单 CR 换行：文件入口的查询与 --doc-state 一致（都是 unwritten）",
          (doc["unwritten"], out.split("\t")[0]), (True, "unwritten"))
    check("整份单 CR 换行：unstamp 往返只删戳那一行", ds.unstamp(s0.replace(b"\n", b"\r")),
          ds.unstamp(s0).replace(b"\n", b"\r"))


def test_third_round():
    print("[第三轮：注释保全 / 无 PyYAML / 引号键 / 执行中失败 / 不可执行候选 / 路径认类型]")
    import module_init
    raw = render("prd")
    s0 = ds.stamp(raw, "prd")
    h0 = ds.mark_value(s0)

    def code_of(fn):
        try:
            fn()
            return None
        except ds.StampError as e:
            return e.code
    eq = s0.replace(f'unwritten: "{h0}"'.encode(), f"unwritten: \"{h0}\"\n'unwritten': \"{h0}\"".encode())
    check("裸键与带引号键同值 → unstamped", ds.effective_state(eq), "unstamped")
    rev = s0.replace(f'unwritten: "{h0}"'.encode(), f"'unwritten': false\nunwritten: \"{h0}\"".encode())
    check("先引号键后裸键 → unstamped", ds.effective_state(rev), "unstamped")
    bare_quoted = s0.replace(f'unwritten: "{h0}"'.encode(), f'"unwritten":\nunwritten: "{h0}"'.encode())
    check("CRLF 文件里冒号后直接换行的引号键 → unstamped",
          ds.effective_state(bare_quoted.replace(b"\n", b"\r\n")), "unstamped")
    mark_line = [l for l in s0.split(b"\n") if l.startswith(b"unwritten")][0]
    with_note = s0.replace(mark_line + b"\n", mark_line + "\n  # owner_role 的维护说明\n".encode("utf-8"))
    want = with_note.replace(mark_line + b"\n", b"")          # 独立基线：只去掉戳那一行
    check("单行戳后跟独立缩进注释：unstamp 只删戳那一行", ds.unstamp(with_note), want)
    real = ds._parsed_frontmatter
    ds._parsed_frontmatter = lambda raw, parts: ds._NO_YAML
    try:
        check("无 PyYAML：H#typo → unstamped（行内写法按 YAML 规矩认）",
              ds.effective_state(s0.replace(f'"{h0}"'.encode(), f"{h0}#typo".encode())), "unstamped")
        check("无 PyYAML：插行拒绝 no-yaml", code_of(lambda: ds.insert_stamp(ds.unstamp(s0), "prd")), "no-yaml")
        check("无 PyYAML：去戳拒绝 no-yaml", code_of(lambda: ds.unstamp(s0)), "no-yaml")
        check("无 PyYAML：已盖戳的骨架也不判 unwritten（证明不了是顶层字段）", ds.effective_state(s0), "unstamped")
        check("无 PyYAML：普通盖戳拒绝 no-yaml", code_of(lambda: ds.stamp(raw, "prd")), "no-yaml")
        check("无 PyYAML：渲染出厂模板的受信盖戳照做（建子模块不因缺库失败）",
              f'unwritten: "{ds.body_digest(raw)}"'.encode() in ds.stamp(raw, "prd", trusted=True), True)
        import module_init
        tree = (TMP / "noyaml").resolve()
        (tree / "projects").mkdir(parents=True)
        params_file = TMP / "noyaml.json"
        params_file.write_bytes(json.dumps({"basics": [{"name": "甲", "subs": [{"name": "乙"}]}]},
                                           ensure_ascii=False).encode("utf-8"))
        module_init.create_tree(tree, module_init.load_params(params_file))
        arch_raw = (tree / "projects/modules/甲/乙/current-state/architecture.md").read_bytes()
        check("无 PyYAML：脚本建子模块照样把现状四件盖上戳",
              f'unwritten: "{ds.body_digest(arch_raw)}"'.encode() in arch_raw, True)
    finally:
        ds._parsed_frontmatter = real
    alias =raw.replace(b"tags: []", b"tags: []\nloop: &a [*a]")
    check("自引用别名：拒绝而不是抛 RecursionError", code_of(lambda: ds.stamp(alias, "prd")) in ("bad-yaml", "yaml-changed"),
          True)
    check("子模块的 current-state/prd.md 不按 PRD 认", ds.kind_of("甲/乙/current-state/prd.md"), None)
    check("子需求名恰为 current-state 的 PRD 仍按 PRD 认",
          ds.kind_of("甲/乙/requirements/r/current-state/prd.md"), "prd")
    check("unwritten:H（冒号后无空白，YAML 里不是键）→ 不算字段",
          ds.mark_value(raw.replace(b"unwritten: true", b"unwritten:" + h0.encode())), None)
    gap = ds.unstamp(raw).replace(b"status: draft", "status: draft\nunwritten:\n\n  # keep with title".encode("utf-8"))
    check("空值字段后隔空行的缩进注释：unstamp 保留空行与注释",
          ds.unstamp(gap), gap.replace(b"unwritten:\n", b"", 1))
    gap2 = gap.replace("# keep with title".encode("utf-8"), "# keep with title\n  ".encode("utf-8"))
    check("注释后再跟一行纯空格：unstamp 仍保留注释",
          ds.unstamp(gap2), gap2.replace(b"unwritten:\n", b"", 1))

    proj = (TMP / "r3").resolve()
    req = proj / "projects/modules/甲/乙/requirements"
    paths = [req / f"{c}/main/prd.md" for c in "abc"]
    for p in paths:
        p.parent.mkdir(parents=True)
        p.write_bytes(ds.unstamp(raw))
    calls = {"n": 0}
    real_write = module_init._atomic_write_bytes

    def flaky(path, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise PermissionError("被占用")
        real_write(path, data)
    module_init._atomic_write_bytes = flaky
    try:
        res = module_init.apply_scan(proj, module_init.scan_unwritten(proj, {}), {})
    finally:
        module_init._atomic_write_bytes = real_write
    check("第二份替换失败：逐份报 stamped / error / skipped",
          [r["status"] for r in res], ["stamped", "error", "skipped"])
    check("第二份替换失败：只有第一份变了",
          [ds.effective_state(p.read_bytes()) for p in paths], ["unwritten", "written", "written"])
    for p in paths:
        p.write_bytes(ds.unstamp(raw))
    files = module_init.scan_unwritten(proj, {})
    real_plan = module_init._planned_stamp

    def save_a_while_planning_b(raw_b, kind, extra):
        if calls.setdefault("plan", 0) == 1:
            paths[0].write_bytes(ds.unstamp(raw) + "\n用户刚保存的正文。\n".encode("utf-8"))
        calls["plan"] += 1
        return real_plan(raw_b, kind, extra)
    module_init._planned_stamp = save_a_while_planning_b
    try:
        res = module_init.apply_scan(proj, files, {})
    finally:
        module_init._planned_stamp = real_plan
    check("预检 B 时用户保存了 A：A 不被覆盖、其余不写",
          ([r["status"] for r in res], "用户刚保存的正文".encode("utf-8") in paths[0].read_bytes()),
          (["error", "skipped", "skipped"], True))
    multi = ds.unstamp(raw).replace(b"status: draft", b'status: "dra\\\nft"')
    paths[1].write_bytes(multi)
    cls = {f["path"].split("requirements/")[1]: f["class"] for f in module_init.scan_unwritten(proj, {})}
    check("status 是跨行引号标量：预览就报异常、不进候选", cls["b/main/prd.md"], "异常")
    cs = proj / "projects/modules/甲/乙/current-state"
    cs.mkdir(parents=True)
    (cs / "prd.md").write_bytes(ds.unstamp(raw))
    cls = {f["path"].split("乙/")[1]: f["class"] for f in module_init.scan_unwritten(proj, {})}
    check("current-state/prd.md 恰与 PRD 骨架相同：不进自动候选", cls["current-state/prd.md"] != "纯骨架", True)
    arch = ds.stamp(render("architecture"), "architecture")
    check("只变 source_ref → 仍 unwritten",
          ds.effective_state(arch.replace(b'source_ref: ""', b'source_ref: "abc12345"')), "unwritten")


def test_template_contract():
    print("[出厂模板契约]")
    for kind in ds.KINDS:
        raw = (TEMPLATES / ("requirement/main/prd.md" if kind == "prd"
                            else f"sub-module/current-state/{kind}.md")).read_bytes()
        check(f"{kind} 模板 frontmatter 有 unwritten: true", ds.mark_value(raw.replace(b"\r\n", b"\n")), "true")
        check(f"{kind} 模板渲染后正文是骨架", ds.matches_skeleton(render(kind), kind), True)
    prd = (TEMPLATES / "requirement/main/prd.md").read_bytes().decode("utf-8")
    check("PRD 骨架 AC 行是「未编写」", "| AC-01 | _（未编写）_ |  |" in prd, True)


def main():
    # 每组独立捕获：一组抛异常不吞掉其余组的断言（突变探针要看见具体哪条红）
    try:
        for fn in (test_states, test_orthogonal, test_stamp_guard, test_bytes, test_normalization_steps,
                   test_skeleton_order, test_archive, test_entrypoints, test_scan, test_yaml_agreement,
                   test_historical_locked, test_scan_review_round, test_query_subs_and_archive_round,
                   test_third_round, test_template_contract):
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
