#!/usr/bin/env python3
"""taxonomy 受控词表单测：`plugins/core/scripts/_taxonomy.py` 与它的两个读者（`doc-graph-health` 的概念热点、
`requirement-archiving` 的归档检查）。

词表判错的两个方向都静默：把出厂示例词当真词 → 概念热点拿「示例能力A」去扫、刚装的项目永远零热点却显示
「已启用」；把未配置的节当已配置 → 归档检查让刚装项目的每个业务 tag 都报违规。所以每组都同时看取词结果
与读者的输出：

- 纯示例、混合词、缺可选节、特殊标记缺失、带计数的节标题、只是前缀相同的说明节、围栏里的表、节内第二张表
  （归一化对照表里的旧词不得进词表）
- 图谱：配置覆盖优先；能力域未配置 →「未启用」三处都写（总表、详细节、终端摘要）；同一个真词出现在三份互不
  相链的正文里 → 热点真的产生
- 归档：已知词合法；全配置下的未知 tag → 违规、计入异常、exit 1；存在未配置节时的未知 tag → 提示行、
  不计入异常、exit 0；taxonomy 缺失照旧跳过

执行：python tools/test_taxonomy.py（退出 0 且打印 all passed = 通过）
"""

import json
import re
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
sys.path.insert(0, str(CORE / "scripts"))
import _taxonomy as tx  # noqa: E402

GRAPH = CORE / "skills" / "doc-graph-health" / "scripts" / "graph_health.py"
ARCHIVE = CORE / "skills" / "requirement-archiving" / "scripts" / "check_archive.py"
TEMPLATE = CORE / "templates" / "taxonomy-template.md"
FAILURES = []
TMP = Path(tempfile.mkdtemp(prefix="wf-taxonomy-")).resolve()
_COUNTER = [0]


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + ("" if ok else f": got={got!r} want={want!r}"))
    if not ok:
        FAILURES.append(label)


def table(*words):
    return "| tag | 含义 |\n|---|---|\n" + "".join(f"| `{w}` | 说明 |\n" for w in words)


def doc(sections, head="---\ntype: concept\nupdated: \"2026-11-22T00:00:00+08:00\"\n---\n\n# 词表\n\n"):
    return head + "".join(f"## {title}\n\n{body}\n" for title, body in sections)


def project(taxonomy=None, config=None):
    _COUNTER[0] += 1
    proj = TMP / f"p{_COUNTER[0]}"
    (proj / "projects" / "modules").mkdir(parents=True)
    if taxonomy is not None:
        p = proj / "projects" / "specs" / "_meta" / "taxonomy.md"
        p.parent.mkdir(parents=True)
        p.write_bytes(taxonomy.encode("utf-8"))
    if config is not None:
        (proj / ".workframe-config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8",
                                                    newline="")
    return proj


def run(script, *args):
    r = subprocess.run([sys.executable, "-B", str(script), *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONUTF8="1"))
    return r.returncode, r.stdout + r.stderr


# ---------------------------------------------------------------- 取词


def test_parse():
    print("[取词：三节、示例词、三态]")
    t = TEMPLATE.read_bytes().decode("utf-8")
    words = tx.parse(t)
    check("出厂模板：能力域只有示例词 → 未配置", tx.section_state(words["能力域"]), tx.UNCONFIGURED)
    check("出厂模板：平台只有示例词 → 未配置", tx.section_state(words["平台"]), tx.UNCONFIGURED)
    check("出厂模板：特殊标记三个出厂词 → 已配置", words["特殊标记"], ["moc", "demo", "external-reference"])

    mixed = tx.parse(doc([("能力域", table("示例能力A", "支付", "示例能力B")), ("平台", table("示例平台"))]))
    check("混合：只有真词生效", mixed["能力域"], ["支付"])
    check("缺特殊标记节 → None（不适用）", mixed["特殊标记"], None)
    check("不适用的三态", tx.section_state(None), tx.NOT_APPLICABLE)
    check("空节 → 未配置", tx.parse(doc([("平台", "单平台，暂不维护。\n")]))["平台"], [])

    counted = tx.parse(doc([("能力域 (12)", table("风控")), ("平台（2）", table("ios"))]))
    check("节标题带计数括注照认", (counted["能力域"], counted["平台"]), (["风控"], ["ios"]))
    prefix = tx.parse(doc([("能力域说明", table("不是词")), ("能力域", table("风控"))]))
    check("只是前缀相同的说明节不算", prefix["能力域"], ["风控"])
    h3 = tx.parse(doc([("能力域", table("风控") + "\n### 能力域\n\n" + table("子节词"))]))
    check("H3 不切节、也不是第二张表之外的新表", h3["能力域"], ["风控"])

    second = doc([("能力域", table("风控") + "\n归一化对照：\n\n| 旧词 | 新词 |\n|---|---|\n| `旧催收` | 风控 |\n")])
    check("节内第二张表（归一化对照）里的旧词不进词表", tx.parse(second)["能力域"], ["风控"])
    fenced = doc([("能力域", "```markdown\n" + table("围栏词") + "```\n\n" + table("风控"))])
    check("围栏里的表不读", tx.parse(fenced)["能力域"], ["风控"])
    fenced_h2 = doc([("能力域", table("风控") + "\n```\n## 平台\n" + table("假平台") + "```\n")])
    check("围栏里的 ## 不切节", (tx.parse(fenced_h2)["能力域"], tx.parse(fenced_h2)["平台"]), (["风控"], None))
    crlf = tx.parse(doc([("能力域", table("风控"))]).replace("\n", "\r\n"))
    check("CRLF 照读", crlf["能力域"], ["风控"])
    dup = tx.parse(doc([("能力域", table("风控", "风控", "支付"))]))
    check("去重保序", dup["能力域"], ["风控", "支付"])

    # 第一轮复核反例：表格必须按「表头 ＋ 分隔行」认
    hdr = doc([("能力域", "| `tag` | 含义 |\n|---|---|\n| `风控` | 说明 |\n")])
    check("表头行即使带反引号也不读", tx.parse(hdr)["能力域"], ["风控"])
    loose = doc([("能力域", "| `旧词` | 不是表 |\n\n" + table("风控"))])
    check("没有分隔行的竖线文本不是表、不挡住后面的正式表", tx.parse(loose)["能力域"], ["风控"])
    loose2 = doc([("能力域", "| `旧词一` | 不是表 |\n| `旧词二` | 也不是 |\n\n" + table("风控"))])
    check("连续两行竖线文本（无分隔行）也不是表", tx.parse(loose2)["能力域"], ["风控"])
    glued = doc([("能力域", table("风控") + "```text\n示例\n```\n| 旧词 | 新词 |\n|---|---|\n| `旧催收` | 风控 |\n")])
    check("紧贴围栏的第二张表不进词表", tx.parse(glued)["能力域"], ["风控"])
    bare = doc([("能力域", "tag | 含义\n---|---\n`风控` | 说明\n")])
    check("省略外侧竖线的表照读", tx.parse(bare)["能力域"], ["风控"])
    info_close = doc([("能力域", "```markdown\n" + table("围栏词") + "```text\n" + table("仍在围栏") + "```\n\n" + table("风控"))])
    check("带信息串的反引号行不闭栏", tx.parse(info_close)["能力域"], ["风控"])
    indented = doc([("能力域", "    tag | 含义\n    ---|---\n    `旧词` | 已退役\n\n" + table("风控"))])
    check("缩进代码块里的表不读", tx.parse(indented)["能力域"], ["风控"])
    skew = doc([("能力域", "format | example\n---|---|---\n\n" + table("风控"))])
    check("表头与分隔行列数不符的不是表", tx.parse(skew)["能力域"], ["风控"])
    check("「##能力域」不加空格不是标题", tx.parse("##能力域\n\n" + table("风控"))["能力域"], None)


# ---------------------------------------------------------------- 图谱


def _report(proj):
    return (proj / "projects" / "modules" / "graph-health.md").read_text(encoding="utf-8")


def _three_docs(proj, word):
    """同一个真词出现在三份互不相链、合格的正文里（各有 updated、不在排除目录）。"""
    d = proj / "projects" / "specs"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(3):
        (d / f"文档{i}.md").write_bytes(
            f"---\ntype: spec\nupdated: \"2026-11-22T00:00:00+08:00\"\n---\n\n# 文档{i}\n\n这里谈到{word}的做法。\n"
            .encode("utf-8"))


def test_graph():
    print("[图谱：未启用三处、真词产生热点、配置覆盖]")
    proj = project(TEMPLATE.read_bytes().decode("utf-8"))
    _three_docs(proj, "示例能力A")
    code, out = run(GRAPH, "--project", proj)
    rep = _report(proj)
    check("纯示例：命令成功", code, 0)
    check("纯示例：总表行写「未启用」", "| 概念热点(未链接高频提及) | 未启用 |" in rep, True)
    check("纯示例：详细节写「未启用」并说怎么启用", "本维度未启用" in rep and "能力域" in rep.split("## 5.")[1], True)
    check("纯示例：终端摘要写「未启用」", "热点 未启用" in out, True)
    check("纯示例：详细节说清是示例词、不是没找到词表", "本维度未启用:词表「能力域」节只有出厂示例词" in rep, True)
    check("纯示例：示例词不当热点", "**示例能力A**" in rep, False)

    proj = project(doc([("能力域", table("示例能力A", "风控引擎"))]))
    _three_docs(proj, "风控引擎")
    code, out = run(GRAPH, "--project", proj)
    rep = _report(proj)
    # 词表文件自己也提到这个词，所以至少 3 份（三份正文 ＋ 可能的词表本身）
    m = re.search(r"\*\*风控引擎\*\*:(\d+) 个文件", rep)
    check("真词：热点产生（≥3 份）", bool(m) and int(m.group(1)) >= 3, True)
    check("真词：总表计数 1", "| 概念热点(未链接高频提及) | 1 |" in rep, True)
    check("真词：终端摘要计数 1", "热点 1" in out and "未启用" not in out, True)

    proj = project(TEMPLATE.read_bytes().decode("utf-8"), {"graph_health": {"hotspot_words": ["风控引擎"]}})
    _three_docs(proj, "风控引擎")
    code, out = run(GRAPH, "--project", proj)
    rep = _report(proj)
    check("配置覆盖：taxonomy 未配置也照用配置词", ("**风控引擎**:3 个文件" in rep, "未启用" in rep), (True, False))

    proj = project(None)
    _three_docs(proj, "风控引擎")
    code, out = run(GRAPH, "--project", proj)
    rep = _report(proj)
    check("无 taxonomy：终端摘要写「未启用」", "热点 未启用" in out, True)
    check("无 taxonomy：详细节说「未找到词表」", "本维度未启用:未找到词表" in rep, True)

    proj = project(doc([("平台", table("ios"))]))
    _three_docs(proj, "风控引擎")
    code, out = run(GRAPH, "--project", proj)
    rep = _report(proj)
    check("只有平台节：详细节说缺能力域节", ("本维度未启用:词表里没有「能力域」节" in rep, "热点 未启用" in out), (True, True))


# ---------------------------------------------------------------- 归档


def _prd(proj, tags):
    req = proj / "projects" / "modules" / "甲" / "乙" / "requirements" / "r1" / "main"
    req.mkdir(parents=True, exist_ok=True)
    tag_list = ", ".join(f'"{t}"' for t in tags)
    (req / "prd.md").write_bytes(
        ("---\ntype: prd\nstatus: draft\nowner_role: pm\nupdated: \"2026-11-22T00:00:00+08:00\"\nmodule: 甲/乙\n"
         "req_slug: r1\nsub_req_slug: main\ndescription: \"真实摘要\"\nrelated: []\n"
         f"tags: [{tag_list}]\n---\n\n# r1\n\n真实正文。\n").encode("utf-8"))
    return proj / "projects" / "modules" / "甲"


def test_archive():
    print("[归档：已知词、违规、提示、跳过]")
    full = doc([("能力域", table("风控")), ("平台", table("ios")), ("特殊标记", table("moc"))])
    basic = _prd(project(full), ["风控", "moc"])
    code, out = run(ARCHIVE, basic)
    check("全配置：已知词合法", (code, "不在受控词表" in out), (0, False))

    basic = _prd(project(full), ["风控", "陌生词"])
    code, out = run(ARCHIVE, basic)
    check("全配置：未知 tag 违规、exit 1", (code, "tag 不在受控词表: 陌生词" in out), (1, True))
    check("全配置：异常计入合计", "异常合计 1 项" in out, True)

    nosec = doc([("能力域", table("风控")), ("特殊标记", table("moc"))])   # 平台节删了 = 不适用
    basic = _prd(project(nosec), ["陌生词"])
    code, out = run(ARCHIVE, basic)
    check("缺可选节（不适用）不放宽：未知 tag 仍违规", (code, "tag 不在受控词表: 陌生词" in out), (1, True))

    basic = _prd(project(TEMPLATE.read_bytes().decode("utf-8")), ["业务词", "moc"])
    code, out = run(ARCHIVE, basic)
    check("存在未配置节：未知 tag 只提示、exit 0", (code, "提示" in out and "业务词" in out), (0, True))
    check("存在未配置节：不计入异常", ("异常合计 0 项" in out, "tag 不在受控词表" in out), (True, False))
    check("提示行点名未配置的节", "能力域" in out.split("业务词")[1].split("\n")[0], True)

    basic = _prd(project(TEMPLATE.read_bytes().decode("utf-8")), ["示例能力A"])
    code, out = run(ARCHIVE, basic)
    check("示例词本身不是合法 tag（只提示）", ("示例能力A" in out, "异常合计 0 项" in out), (True, True))

    old = doc([("能力域", table("风控") + "\n| 旧词 | 新词 |\n|---|---|\n| `旧催收` | 风控 |\n"),
               ("平台", table("ios")), ("特殊标记", table("moc"))])
    basic = _prd(project(old), ["旧催收"])
    code, out = run(ARCHIVE, basic)
    check("归一化对照表里的旧词不是合法 tag", (code, "tag 不在受控词表: 旧催收" in out), (1, True))

    glued = doc([("能力域", table("风控") + "```text\n示例\n```\n| 旧词 | 新词 |\n|---|---|\n| `旧催收` | 风控 |\n"),
                 ("平台", table("ios")), ("特殊标记", table("moc"))])
    basic = _prd(project(glued), ["旧催收"])
    code, out = run(ARCHIVE, basic)
    check("紧贴围栏的对照表里的旧词仍违规", (code, "tag 不在受控词表: 旧催收" in out), (1, True))
    bare = doc([("能力域", "tag | 含义\n---|---\n`风控` | 说明\n"), ("平台", table("ios")), ("特殊标记", table("moc"))])
    basic = _prd(project(bare), ["陌生词"])
    code, out = run(ARCHIVE, basic)
    check("省略外侧竖线的已配置节不被当未配置：未知 tag 仍违规", (code, "tag 不在受控词表: 陌生词" in out), (1, True))

    basic = _prd(project(None), ["随便"])
    code, out = run(ARCHIVE, basic)
    check("无 taxonomy：照旧跳过词表检查", (code, "跳过 tags 词表检查" in out), (0, True))


def main():
    try:
        for fn in (test_parse, test_graph, test_archive):
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
