# -*- coding: utf-8 -*-
"""知识网健康巡检(doc-graph-health)——core skill 版

扫描 projects/modules/ + projects/specs/ + Home.md 的链接图,产出持久报告
projects/modules/graph-health.md(可 git diff、库内可读)。

五维检测:
  1. 断链   wikilink / markdown 链接 / frontmatter related 目标不存在
            (短名 wikilink 按 basename 解析,歧义单列)
  2. 孤儿   入链为 0 的文档(白名单豁免;wikilink + markdown 链接 + related 三源计入度)
  3. hub    被引最多的文档 top10(横向核心资产)
  4. stale  ①current-state 类超阈值未同步 ②同 req 内 overview.updated 落后 prd.updated
            超阈值的「姊妹时差」可疑对(语义矛盾的强信号,需人工/agent 抽读确认)
            ③frontmatter updated 缺失/格式错(单列,不静默跳过)
  5. 概念热点 词表词在正文高频提及但所在文件无对应链接(缺口候选)
            词表来源: .workframe-config.json graph_health.hotspot_words(优先)
            → projects/specs/_meta/taxonomy.md「## 能力域」表格
            → 两者皆无则跳过本维度并给出启用指引

项目级配置(.workframe-config.json, 均可缺省):
    "graph_health": {
        "hotspot_words":      ["词A", "词B"],   // 覆盖 taxonomy 来源
        "noise_line_markers": ["模板行特征"],    // 命中即整行不计热点(骨架模板噪声)
        "extra_exclude_dirs": ["目录名"]         // 追加排除目录
    }

用法(从项目根目录运行,或 --project 指定):
    python "<插件根>/skills/doc-graph-health/scripts/graph_health.py"          # 全量 + 写报告
    python "<插件根>/skills/doc-graph-health/scripts/graph_health.py" --dry    # 只打印摘要
"""
import argparse
import json
import re
import sys
import io
from pathlib import Path
from datetime import datetime, timezone, timedelta

# 中文 Windows 控制台是 cp936，本脚本报告与报错全是中文。**两条流都要包**：
# 只包 stdout 时，未捕获异常的 traceback（走 stderr，且印着含中文的用户路径）
# 自己会抛 UnicodeEncodeError，把真实错因顶掉。main() 里的 out 直接复用
# sys.stdout，不另建 wrapper——两个 TextIOWrapper 抢同一个 buffer 会在先被回收时
# 关掉 buffer，另一个当场 `I/O operation on closed file`。
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

EXCLUDE_DIRS_BASE = {"prototypes", "node_modules", ".claude", "tmp", "_draft", "assets"}
ORPHAN_WHITELIST = {"Home.md", "projects/modules/graph-health.md", "projects/modules/overview.md",
                    "projects/specs/overview.md", "projects/issues/TEMPLATES.md"}
STALE_CS_DAYS = 45          # current-state 超此天数未同步进 stale 候选
SIBLING_GAP_DAYS = 7        # req overview 落后同 req prd 超此天数 → 时差可疑对
HOTSPOT_MIN_FILES = 3       # 词表词至少在 N 个文件正文出现才列热点

TAXONOMY_REL = "projects/specs/_meta/taxonomy.md"
TAXONOMY_SECTION = "## 能力域"

NOW = datetime.now().astimezone()


def load_config(proj: Path) -> dict:
    """读 .workframe-config.json 的 graph_health 块;缺省一律空(读取方兜底,同框架契约)。"""
    cfg_path = proj / ".workframe-config.json"
    if not cfg_path.exists():
        return {}
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        block = cfg.get("graph_health", {})
        return block if isinstance(block, dict) else {}
    except Exception:
        return {}


def load_taxonomy_words(proj: Path):
    """解析 taxonomy.md「## 能力域」表格首列反引号 tag → 热点词表。

    Returns: (words, taxonomy_updated_str) — 文件/段落不存在返回 ([], None)。
    """
    tax = proj / TAXONOMY_REL
    if not tax.exists():
        return [], None
    text = tax.read_text(encoding="utf-8", errors="replace")
    updated = None
    m = re.search(r"^updated:\s*(.+)$", text, re.M)
    if m:
        updated = m.group(1).strip().strip('"')
    sec = re.search(rf"^{re.escape(TAXONOMY_SECTION)}.*?$(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not sec:
        return [], updated
    words = re.findall(r"^\|\s*`([^`]+)`\s*\|", sec.group(1), re.M)
    return [w.strip() for w in words if w.strip()], updated


def frontmatter_block(text):
    """返回 frontmatter 原文块(不含 --- 围栏),无则空串。"""
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    return m.group(1) if m else ""


def frontmatter(text):
    fm = {}
    for line in frontmatter_block(text).splitlines():
        kv = re.match(r"^(\w+):\s*(.*)$", line)
        if kv:
            fm[kv.group(1)] = kv.group(2).strip().strip('"')
    return fm


def parse_related(fm_text):
    """从 frontmatter 原文块提取 related 条目(两种 YAML 形态)。

    行内: related: ["[[path|alias]]", "../x.md"]
    块式: related:
            - "[[path|alias]]"
            - "../x.md"
    返回原始条目字符串列表(未解 wikilink)。
    """
    entries = []
    inline = re.search(r"^related:\s*\[(.*)\]\s*$", fm_text, re.M)
    if inline:
        entries += re.findall(r'"([^"]+)"', inline.group(1))
        entries += re.findall(r"'([^']+)'", inline.group(1))
        return entries
    block = re.search(r"^related:\s*$(.*?)(?=^\w|\Z)", fm_text, re.S | re.M)
    if block:
        for line in block.group(1).splitlines():
            m = re.match(r"^\s*-\s*(.+)$", line)
            if m:
                entries.append(m.group(1).strip().strip('"').strip("'"))
    return entries


def _safe_resolvable(base: Path, t: str) -> bool:
    """路径拼接是否可安全 resolve(Windows 非法字符/越界均视为不可)。"""
    try:
        (base / t).resolve()
        return True
    except (ValueError, OSError):
        return False


def parse_dt(s):
    """解析 frontmatter 的 updated 时间戳。

    先规范化再解析：原先按 `s[:25]` 定长切片，遇到带小数秒的合法 ISO-8601
    （`datetime.isoformat()` 默认输出、外部工具常见）会切出 `…12.12345` 这种畸形串——
    连时区一起丢掉，三种格式全部解析失败，该文件被误报「updated 缺失/格式错」
    并被排除出 stale 与姊妹时差判定。
    """
    if not s:
        return None
    s = str(s).strip().strip('"')
    s = re.sub(r"^(\d{4}-\d{2}-\d{2}) ", r"\1T", s)          # 空格分隔 → T
    s = re.sub(r"(T\d{2}:\d{2}:\d{2})\.\d+", r"\1", s)       # 去掉小数秒
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=NOW.tzinfo)
            return dt
        except ValueError:
            continue
    return None


def main():
    parser = argparse.ArgumentParser(description="知识网健康巡检(doc-graph-health)")
    parser.add_argument("--project", default=".", help="项目根目录(默认当前目录)")
    parser.add_argument("--dry", action="store_true", help="只打印摘要,不写报告")
    args = parser.parse_args()

    out = sys.stdout  # 已在模块级包成 UTF-8；不再另建 wrapper（见顶部注释）

    proj = Path(args.project).resolve()
    scopes = [proj / "projects" / "modules", proj / "projects" / "specs"]
    extra_files = [proj / "Home.md"]
    report_path = proj / "projects" / "modules" / "graph-health.md"

    gh_cfg = load_config(proj)
    # 配置项写成字符串是常见笔误，必须容错：set("文档附件") 会拆成
    # {'文','档','附','件'} 四个"目录名"，列表推导同样逐字符——行为诡异且零报错。
    def _as_list(v):
        if v is None:
            return []
        return [v] if isinstance(v, str) else [x for x in v if x]

    exclude_dirs = EXCLUDE_DIRS_BASE | set(_as_list(gh_cfg.get("extra_exclude_dirs")))
    noise_markers = _as_list(gh_cfg.get("noise_line_markers"))

    # 热点词表: config 覆盖 > taxonomy 能力域 > 跳过
    tax_words, tax_updated = load_taxonomy_words(proj)
    cfg_words = [w for w in (gh_cfg.get("hotspot_words", []) or []) if w]
    if cfg_words:
        hotspot_words, hotspot_source = cfg_words, "config(graph_health.hotspot_words)"
    elif tax_words:
        hotspot_words = tax_words
        hotspot_source = f"taxonomy 能力域({TAXONOMY_REL}, updated {tax_updated or '未知'})"
    else:
        hotspot_words, hotspot_source = [], None

    def excluded(p: Path) -> bool:
        return any(part in exclude_dirs for part in p.parts)

    def rel(p: Path) -> str:
        return p.relative_to(proj).as_posix()

    files = []
    for scope in scopes:
        if scope.is_dir():
            files += [p for p in scope.rglob("*.md") if not excluded(p.relative_to(proj))]
    files += [p for p in extra_files if p.exists()]
    files = sorted(set(files))

    # 空态友好: 新项目/无 modules 体系时不误报、不留副作用
    if not files:
        out.write("尚无文档数据: projects/modules 与 projects/specs 下未发现 md 文档。\n")
        out.write("(新项目属正常;建立文档后再跑巡检。未写报告。)\n")
        out.flush()
        return 0

    file_set = {rel(p) for p in files}
    basename_map = {}
    for f in file_set:
        basename_map.setdefault(Path(f).stem, []).append(f)

    inlinks = {f: 0 for f in file_set}
    broken, ambiguous = [], []

    wiki_re = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
    mdlink_re = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")

    texts = {}
    for p in files:
        texts[rel(p)] = p.read_text(encoding="utf-8", errors="replace")

    def exists_outside(t, src):
        """目标在**扫描范围外但真实存在** → 合法引用，不算断链。

        扫描面只有 projects/modules + projects/specs（+ Home.md），而指向 CLAUDE.md、
        projects/issues/、company-context/ 的链接完全合法。判据必须是「文件存不存在」，
        不是「在不在扫描集合里」——后者把每个跨区引用都记成断链，真断链被淹没，
        报告口径也与 SKILL 自述的「目标不存在」不符。
        """
        names = (t,) if t.endswith(".md") else (t + ".md", t)
        for base in (proj, proj / Path(src).parent):
            for name in names:
                try:
                    if (base / name).resolve().exists():
                        return True
                except (ValueError, OSError):
                    continue
        return False

    def link_wiki_target(src, t, origin):
        """解析一个 wikilink 目标(全路径/相对路径/短名),计入度或记断链/歧义。

        含 "/" 的目标先按 vault 根解析,失败再按所在文件目录相对解析
        (Obsidian 两种写法都合法;只按根解析会把相对写法误报断链)。
        两个分支最后都要过 exists_outside——范围外的合法引用不是断链。
        """
        if "/" in t:
            tgt = t + ".md" if not t.endswith(".md") else t
            if tgt in file_set:
                inlinks[tgt] += 1
                return
            try:
                cand = (proj / Path(src).parent / tgt).resolve().relative_to(proj).as_posix()
            except (ValueError, OSError):
                cand = None
            if cand and cand in file_set:
                inlinks[cand] += 1
            elif not exists_outside(t, src):
                broken.append((src, origin))
        else:                                       # 短名解析
            hits = basename_map.get(t, [])
            if len(hits) == 1:
                inlinks[hits[0]] += 1
            elif len(hits) == 0:
                if not exists_outside(t, src):
                    broken.append((src, origin))
            else:
                ambiguous.append((src, origin, len(hits)))

    fm_strip_re = re.compile(r"^---\r?\n.*?\r?\n---\r?\n", re.S)

    for src, text in texts.items():
        src_dir = Path(src).parent
        # 正文与 frontmatter 分开扫:正文走 wikilink/md 链接,frontmatter 走 related 通道。
        # (旧版全文扫描会让 related 里的 wikilink 被计两次,断链虚高)
        body = fm_strip_re.sub("", text, count=1)
        for m in wiki_re.finditer(body):
            t = m.group(1).strip().rstrip("\\")   # 表格内 [[path\|alias]] 的转义管道
            link_wiki_target(src, t, f"[[{t}]]")
        for m in mdlink_re.finditer(body):
            t = m.group(1).strip()
            if t.startswith(("http://", "https://", "mailto:")):
                continue
            if not t.endswith(".md"):
                # 目录链接(如 AUTO-INDEX 的 [main](main/)):入度记给该目录下 prd.md
                if t.endswith("/"):
                    cand = (proj / src_dir / t / "prd.md")
                    try:
                        cand_rel = cand.resolve().relative_to(proj).as_posix()
                        if cand_rel in file_set:
                            inlinks[cand_rel] += 1
                    except ValueError:
                        pass
                continue
            tgt = (proj / src_dir / t).resolve()
            try:
                tgt_rel = tgt.relative_to(proj).as_posix()
            except ValueError:
                broken.append((src, t))
                continue
            if tgt_rel in file_set:
                inlinks[tgt_rel] += 1
            elif excluded(Path(tgt_rel)):
                pass                      # 排除目录（prototypes/ 等）：不计入度也不算断链
            elif not tgt.exists():
                broken.append((src, t))
            # else: 扫描范围外但**文件真实存在**（CLAUDE.md / projects/issues/ 等）——
            # 那是合法引用，不是断链。判据必须是「目标存不存在」而不是「在不在扫描集合里」，
            # 否则每个指向范围外的正常链接都算一条断链，真断链被淹没在误报里
            # （报告口径也与 SKILL 自述的「目标不存在」不符）。

        # frontmatter related: 弱关联通道同样计入度 + 查断链(wikilink / 相对路径双格式)
        for entry in parse_related(frontmatter_block(text)):
            wm = wiki_re.fullmatch(entry) or wiki_re.match(entry)
            if wm:
                t = wm.group(1).strip()
                link_wiki_target(src, t, f"related: [[{t}]]")
                continue
            t = entry.strip()
            if t.startswith(("http://", "https://", "mailto:")) or not t:
                continue
            if not t.endswith(".md"):
                suffix = Path(t).suffix
                if suffix and suffix != ".md":
                    # 指向非 md 资产(html/png 等):只查存在性,存在即合理引用(不入图),不存在才报
                    exists = any((base / t).resolve().exists()
                                 for base in (proj, proj / src_dir)
                                 if _safe_resolvable(base, t))
                    if not exists:
                        broken.append((src, f"related: {entry}"))
                    continue
                t += ".md"
            # 纯路径条目双解析:先按 vault 根相对,再按所在文件相对,命中即计
            hit = None
            for base in (proj, proj / src_dir):
                try:
                    cand = (base / t).resolve().relative_to(proj).as_posix()
                except (ValueError, OSError):
                    continue
                if cand in file_set:
                    hit = cand
                    break
            if hit:
                inlinks[hit] += 1
            else:
                broken.append((src, f"related: {entry}"))

    # 孤儿(豁免结构性文件:current-state 四件套、requirements 清单页——位置约定即入口)
    def structural(f):
        return "/current-state/" in f or f.endswith("requirements/overview.md")
    orphans = sorted(f for f, n in inlinks.items()
                     if n == 0 and f not in ORPHAN_WHITELIST and not structural(f))
    # hub
    hubs = sorted(((n, f) for f, n in inlinks.items() if n > 0), reverse=True)[:10]

    # stale ① current-state
    stale_cs = []
    for f, text in texts.items():
        if "/current-state/" not in f:
            continue
        fm = frontmatter(text)
        dt = parse_dt(fm.get("updated"))
        if dt and (NOW - dt).days > STALE_CS_DAYS:
            stub = "待 code-to-doc" in text
            stale_cs.append((f, dt.date().isoformat(), (NOW - dt).days, "stub" if stub else "有内容"))

    # stale ② 姊妹时差:req overview vs 同 req 各 prd
    siblings = []
    for f, text in texts.items():
        m = re.match(r"(.*/requirements/[^/]+)/overview\.md$", f)
        if not m:
            continue
        ov_dt = parse_dt(frontmatter(text).get("updated"))
        if not ov_dt:
            continue
        req_dir = m.group(1)
        for g, t2 in texts.items():
            if g.startswith(req_dir + "/") and g.endswith("/prd.md"):
                if "本骨架仅占位" in t2:
                    continue
                prd_dt = parse_dt(frontmatter(t2).get("updated"))
                if prd_dt and (prd_dt - ov_dt).days > SIBLING_GAP_DAYS:
                    siblings.append((f, ov_dt.date().isoformat(), g, prd_dt.date().isoformat(),
                                     (prd_dt - ov_dt).days))

    # stale ③ updated 缺失/格式错(有 frontmatter 但 updated 解析失败——静默跳过=漏检不可见)
    missing_updated = []
    for f, text in texts.items():
        fmb = frontmatter_block(text)
        if not fmb:
            continue        # 无 frontmatter 的文件(如 Home.md)不要求
        fm = frontmatter(text)
        raw = fm.get("updated")
        if not raw or parse_dt(raw) is None:
            missing_updated.append((f, raw or "(缺失)"))

    # 概念热点(正文提及但该文件无对应链接)
    hotspots = []
    for word in hotspot_words:
        hit_files = []
        for f, text in texts.items():
            body = re.sub(r"^---\r?\n.*?\r?\n---\r?\n", "", text, flags=re.S)
            lines = [ln for ln in body.splitlines()
                     if word in ln and "[[" not in ln
                     and not any(marker in ln for marker in noise_markers)]
            if lines and word not in f:
                hit_files.append((f, len(lines)))
        if len(hit_files) >= HOTSPOT_MIN_FILES:
            hit_files.sort(key=lambda x: -x[1])
            hotspots.append((word, len(hit_files), hit_files[:5]))

    # ---------- 渲染报告 ----------
    ts = NOW.isoformat(timespec="seconds")
    L = []
    L.append("---")
    L.append("type: report")
    L.append("status: in_progress")
    L.append("owner_role: qa")
    L.append(f"updated: {ts}")
    L.append('module: ""')
    L.append('description: "知识网健康巡检持久报告(断链/孤儿/hub/stale时差/概念热点),由 core skill doc-graph-health 生成,可 git diff 追踪收敛趋势。"')
    L.append("related: []")
    L.append("tags: [moc]")
    L.append("---")
    L.append("")
    L.append("# 知识网健康巡检报告(graph-health)")
    L.append("")
    L.append(f"> 生成:{ts} · 由 core skill `doc-graph-health` 的 graph_health.py 重算,请勿手改正文。")
    L.append(f"> 扫描范围:projects/modules + projects/specs + Home.md,共 {len(files)} 份;排除 {'/'.join(sorted(exclude_dirs))} 噪声目录。")
    L.append("")
    L.append("## 总览")
    L.append("")
    L.append("| 维度 | 数量 |")
    L.append("|---|---|")
    L.append(f"| 断链 | {len(broken)} |")
    L.append(f"| 短名歧义 | {len(ambiguous)} |")
    L.append(f"| 孤儿文档 | {len(orphans)} |")
    L.append(f"| stale current-state(>{STALE_CS_DAYS}天) | {len(stale_cs)} |")
    L.append(f"| 姊妹时差可疑对(overview 落后 prd >{SIBLING_GAP_DAYS}天) | {len(siblings)} |")
    L.append(f"| updated 缺失/格式错 | {len(missing_updated)} |")
    L.append(f"| 概念热点(未链接高频提及) | {len(hotspots)} |")
    L.append("")
    L.append("## 1. 断链(含 frontmatter related)")
    L.append("")
    if broken:
        L.append("| 所在文件 | 链接 |")
        L.append("|---|---|")
        for src, t in broken[:50]:
            L.append(f"| {src} | `{t}` |")
    else:
        L.append("_无断链。_")
    L.append("")
    L.append("## 2. 孤儿文档(入链=0;wikilink/md链接/related 三源计数)")
    L.append("")
    if orphans:
        L.append("| 文件 | 建议 |")
        L.append("|---|---|")
        for f in orphans[:60]:
            L.append(f"| {f} | 补上级索引/related 或确认归档 |")
    else:
        L.append("_无孤儿。_")
    L.append("")
    L.append("## 3. Hub(被引 top10 = 横向核心资产)")
    L.append("")
    L.append("| 入链数 | 文件 |")
    L.append("|---|---|")
    for n, f in hubs:
        L.append(f"| {n} | {f} |")
    L.append("")
    L.append("## 4. Stale 候选")
    L.append("")
    L.append(f"### 4.1 current-state 超 {STALE_CS_DAYS} 天未同步")
    L.append("")
    if stale_cs:
        L.append("| 文件 | updated | 天数 | 形态 |")
        L.append("|---|---|---|---|")
        for f, d, days, kind in sorted(stale_cs, key=lambda x: -x[2])[:30]:
            L.append(f"| {f} | {d} | {days} | {kind} |")
    else:
        L.append("_无。_")
    L.append("")
    L.append(f"### 4.2 姊妹时差(overview 落后同 req prd > {SIBLING_GAP_DAYS} 天 → 需抽读确认 overview 是否过期)")
    L.append("")
    if siblings:
        L.append("| overview | ov.updated | prd | prd.updated | 时差(天) |")
        L.append("|---|---|---|---|---|")
        for ov, od, prd, pd, gap in sorted(siblings, key=lambda x: -x[4]):
            L.append(f"| {ov} | {od} | {prd} | {pd} | {gap} |")
    else:
        L.append("_无。_")
    L.append("")
    L.append("### 4.3 updated 缺失/格式错(stale 判定无法覆盖这些文件)")
    L.append("")
    if missing_updated:
        L.append("| 文件 | updated 现值 |")
        L.append("|---|---|")
        for f, raw in missing_updated[:30]:
            L.append(f"| {f} | `{raw}` |")
    else:
        L.append("_无。_")
    L.append("")
    L.append("## 5. 概念热点(高频提及但未链接 → 织网/建 spec 候选)")
    L.append("")
    if hotspot_source:
        L.append(f"> 词表来源:{hotspot_source},共 {len(hotspot_words)} 词。词表停更会让本维度漏检新概念——留意来源的 updated 时间。")
        L.append("")
        if hotspots:
            for word, nf, tops in hotspots:
                L.append(f"- **{word}**:{nf} 个文件未链接提及;top:" +
                         ";".join(f"{Path(f).name}×{c}" for f, c in tops))
        else:
            L.append("_无。_")
    else:
        L.append(f"> 本维度已跳过:未找到词表。启用方式二选一:①建 `{TAXONOMY_REL}`(模板见 core 插件 templates/taxonomy-template.md),在「{TAXONOMY_SECTION}」表格维护业务概念词;②在 `.workframe-config.json` 配 `graph_health.hotspot_words`。")
    L.append("")
    L.append("## 处置指引")
    L.append("")
    L.append("- 断链/孤儿:小修直接改;批量走任务。")
    L.append("- 姊妹时差对:**必须人工/agent 抽读确认**是否真矛盾(如列数/口径不一致),确认后修 overview 正文(bump updated)。")
    L.append("- updated 缺失/格式错:按 document-norms §2.7 补齐(实时 ISO-8601)。")
    L.append("- 概念热点:反复提及且无专属文档的概念 → 评估补 wikilink 织网,或立跨模块 spec。")
    L.append("- 巡检口径与排除纪律见 core skill `doc-graph-health` SKILL.md。")
    L.append("")

    report_text = "\n".join(L)
    out.write(f"扫描 {len(files)} 份 | 断链 {len(broken)} | 歧义 {len(ambiguous)} | 孤儿 {len(orphans)}"
              f" | staleCS {len(stale_cs)} | 时差对 {len(siblings)} | updated异常 {len(missing_updated)}"
              f" | 热点 {len(hotspots)}{'' if hotspot_source else '(维度跳过:无词表)'}\n")
    if args.dry:
        out.write("(dry-run,未写报告)\n")
    else:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text, encoding="utf-8", newline="")
        out.write(f"报告已写入: {rel(report_path)}\n")
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
