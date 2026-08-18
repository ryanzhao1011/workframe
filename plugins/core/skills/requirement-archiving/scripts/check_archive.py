# -*- coding: utf-8 -*-
"""归档产出自查：对一个 basic 模块目录做机器可查项的全量校验。

用法:
    python check_archive.py <projects/modules/<basic> 绝对或相对路径>

检查项（语义类自查仍走 document-norms §11.4 人工过）:
    1. prd.md frontmatter 必填字段 / description ≤200 字 / status 枚举
    2. tags 受控词表（自动向上定位 projects/specs/_meta/taxonomy.md）
    3. 模板占位符 {{...}} 残留
    4. lark-flavored 禁用标签（<text>/<callout>/<sheet>/<bitable>/{folded}...）
    5. AI 病灶短语（口号式收尾等）
    6. 表格行内 wikilink 未转义 |
    7. wikilink 断链（相对项目根解析）
    8. 图片引用断图 + assets/ 孤儿图
    9. overview 机器维护段 AUTO-INDEX START/END 配对

依赖: pyyaml（缺失时降级为字段名正则检查）
实战校准: 2026-07 两次大规模归档（16 份 PRD × 多轮批次自查零漏网）。
"""
import sys
import io
import os
import re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    import yaml
except ImportError:
    yaml = None

PRD_REQUIRED = ["type", "status", "owner_role", "updated", "module",
                "req_slug", "sub_req_slug", "description", "related", "tags"]
# markdown frontmatter 的 status 词表，与 document-norms §2.2 逐字对齐。
# `*.yaml` 实体（meta.yaml / submodule.yaml / module.yaml）另有一套
# planning|active|done|dropped，两套是**独立词表**（§2.2 明确声明）——此前这里把两套并成
# 一个集合，还混进了两边都没有的 `reviewing`，规范外的值因此能通过机器自查；
# 真正属于 markdown 的 `planning` 反而不在其中。
PRD_STATUS = {"planning", "draft", "in_progress", "approved",
              "implemented", "shipped", "deprecated", "superseded"}
# 存量文档里出现过的越界值 → 建议迁移目标。仍然计为问题（本脚本只有一档输出），
# 但报错信息直接给出该改成什么，不让人自己去翻规范对照。
PRD_STATUS_LEGACY = {"done": "shipped", "active": "in_progress", "reviewing": "approved"}
BANNED = re.compile(r"<(text|callout|sheet|bitable|/?font|/?span|/?div)\b|\{folded", re.I)
SMELL = re.compile(r"(满足[^。]{0,20}核心诉求|为[^。]{0,15}保驾护航|综上所述|总而言之|"
                   r"极大地?提升|全面提升[^。]{0,10}体验|奠定[^。]{0,10}基础)")


def find_project_root(start):
    d = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(d, "projects", "modules")):
            return d
        nd = os.path.dirname(d)
        if nd == d:
            return None
        d = nd


def load_vocab(root):
    p = os.path.join(root, "projects", "specs", "_meta", "taxonomy.md") if root else None
    if not p or not os.path.exists(p):
        return None
    t = io.open(p, encoding="utf-8").read()
    return set(re.findall(r"^\| `([^`]+)`", t, re.M))


def frontmatter(t):
    m = re.match(r"^---\n(.*?)\n---\n", t, re.S)
    if not m:
        return None
    if yaml:
        try:
            return yaml.safe_load(m.group(1))
        except Exception:
            return {"__parse_error__": True}
    return {k: True for k in re.findall(r"^(\w[\w-]*):", m.group(1), re.M)}


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    M = os.path.abspath(sys.argv[1])
    root = find_project_root(M)
    vocab = load_vocab(root)
    if vocab is None:
        print("⚠ 未找到 taxonomy.md，跳过 tags 词表检查")

    bad = 0
    n_prd = 0
    used_imgs = set()
    img_refs = 0

    for r, _, files in os.walk(M):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            p = os.path.join(r, fn)
            t = io.open(p, encoding="utf-8", errors="replace").read()
            rel = os.path.relpath(p, M)
            errs = []

            # 图片引用（所有 md 统一收集）
            for link in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", t):
                img_refs += 1
                tgt = os.path.normpath(os.path.join(r, link))
                used_imgs.add(tgt)
                if not os.path.exists(tgt):
                    errs.append("断图 " + link)

            # wikilink 断链 + 表格行内转义
            for i, line in enumerate(t.split("\n"), 1):
                for wl in re.findall(r"\[\[([^\]]+)\]\]", line):
                    # 先剥别名（`|`）再剥章节锚点（`#`）：`[[设计稿#交互细节|设计稿]]`
                    # 两者都可能出现。不剥 `#` 会去找名为「设计稿#交互细节.md」的文件，
                    # 于是所有带锚点的 wikilink 恒判断链，Phase 4 门禁「自查零异常」虚警。
                    tgt = wl.split("|")[0].split("#")[0].replace("\\", "").strip()
                    if root and tgt:      # 纯锚点 `[[#本文小节]]` 剥完为空，指向本文档，不查
                        c = os.path.join(root, tgt.replace("/", os.sep))
                        if not (os.path.exists(c) or os.path.exists(c + ".md")):
                            errs.append("wikilink 断链 L%d %s" % (i, tgt))
                if line.strip().startswith("|"):
                    for wl in re.findall(r"\[\[[^\]]*\]\]", line):
                        if "|" in wl and "\\|" not in wl:
                            errs.append("L%d 表格内 wikilink 未转义" % i)

            if "{{" in t:
                errs.append("占位符残留")
            for bm in BANNED.finditer(t):
                errs.append("禁用标签 " + bm.group(0))
            for sm in SMELL.finditer(t):
                errs.append("AI 病灶「%s」" % sm.group(0))

            # overview 机器维护段配对
            if fn == "overview.md":
                ns = len(re.findall(r"WORKFRAME:AUTO-INDEX:START", t))
                ne = len(re.findall(r"WORKFRAME:AUTO-INDEX:END", t))
                if ns != ne or ns == 0:
                    errs.append("AUTO-INDEX 段不配对 START=%d END=%d" % (ns, ne))

            # prd.md frontmatter 深检
            if fn == "prd.md" and "由 `prd-writer` skill 生成" not in t:
                n_prd += 1
                fm = frontmatter(t)
                if not fm:
                    errs.append("无 frontmatter")
                elif fm.get("__parse_error__"):
                    errs.append("frontmatter YAML 解析失败")
                else:
                    for k in PRD_REQUIRED:
                        if k not in fm:
                            errs.append("缺字段 " + k)
                    if yaml:
                        d = fm.get("description") or ""
                        if len(d) > 200:
                            errs.append("description %d 字 >200" % len(d))
                        st = fm.get("status")
                        if st in PRD_STATUS_LEGACY:
                            errs.append("status «%s» 是 yaml 实体词表的值，markdown 应改用 «%s»"
                                        "（两套词表独立，见 document-norms §2.2）"
                                        % (st, PRD_STATUS_LEGACY[st]))
                        elif st not in PRD_STATUS:
                            errs.append("status 取值异常 %s" % st)
                        if vocab is not None:
                            for tg in (fm.get("tags") or []):
                                if tg not in vocab:
                                    errs.append("tag 不在受控词表: " + tg)

            if errs:
                bad += len(errs)
                print("!! %-50s %s" % (rel, " / ".join(errs)))

    # 孤儿图
    orph = []
    for r, _, files in os.walk(M):
        if os.path.basename(r) != "assets":
            continue
        for f in files:
            if not f.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                continue
            fp = os.path.normpath(os.path.join(r, f))
            if fp not in used_imgs:
                orph.append(os.path.relpath(fp, M))
    for o in orph:
        print("!! 孤儿图（assets 内无任何 md 引用）:", o)
    bad += len(orph)

    print("\n检查 PRD %d 份 | 图片引用 %d 条 | 异常合计 %d 项 %s"
          % (n_prd, img_refs, bad, "✅" if bad == 0 else "❌"))
    sys.exit(0 if bad == 0 else 1)


if __name__ == "__main__":
    main()
