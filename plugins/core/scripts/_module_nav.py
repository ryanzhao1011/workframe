#!/usr/bin/env python3
"""
_module_nav.py — modules/ 体系导航段（AUTO-INDEX）的规划器与写入口。

六类段都由这里渲染，`module_init.py` 只做 CLI，close-check 的 `[nav-index]` 拿同一个
`plan()` 做零写入比较——渲染规则只此一份。import 时零副作用（不包装流、不写盘）。

段与文件层级（相对 `projects/modules/`）：

| 文件 | 段 | 段类 |
|---|---|---|
| `overview.md` | 匿名 | basic-modules-index |
| `<basic>/overview.md` | 匿名 | submodules-index |
| `<basic>/<sub>/overview.md` | `current-state-summary`、`requirements-index` | 同名 |
| `<basic>/<sub>/requirements/overview.md` | 匿名 | requirements-by-status |
| `<basic>/<sub>/requirements/<req>/overview.md` | 匿名 | sub-requirements-index |

`requirements/_draft/` 下不检。段标记只认**独占一行**的 START / END（行内代码里演示的标记不算），
代码围栏内的也不算。

**版式**：前两类本来就由脚本生成，一直照常刷新；后四类按段体判 v2 / legacy——v2 照常刷新，
legacy 只报不写，等 `--convert-layout` 预览、确认码确认后才转换（转换会改写人写过的旧表与
旧行，所以单独确认）。判法见 `layout_of`：表格段比全部表头，`current-state-summary` 是列表
段、没有表头，比段体第一行说明（v2 的说明行写明「；」之后归人写）。不加不可见标记——Mortar
只隐藏大写 START/END 标记，别的 HTML 注释会当正文显示。

**`current-state-summary` 的所有权**：每行 `- **<标签>**：<机器 token>[；<人写尾注>]`，
机器只换 token（取对应现状文件的 `description`，见 `current_state_token`；机器 token 里不会
出现「；」，否则下次刷新会把它后半当尾注再拼一次），尾注原样保留；斜体占位 `_（…；注记）_`
整个是 token，占位里「；」之后的注记挪成尾注；「最近同步」行只换日期与 `基准 commit <ref>`
两个 token。段体第一行之外的说明行原样保留。旧版式转换时普通行「；」之后不当尾注——旧版式
没有这条约定，那半截是同一句旧摘要。

**整份不写的文件**：段结构有问题（缺段、不配对、重复、未知段）或取数源读不了的文件，本次一段
都不写，只报诊断——配对失败时任何一段的边界都不可信。

**确认码**（转换）= sha256（项目根、每个将写文件的摘要、每段新内容的摘要、该段读过的每个源
——PRD、meta、overview、现状文件、附件目录清单——的摘要、每个示例图选项）。源变了码就变，
哪怕新内容恰好不变。

**并发边界**：`apply` 写前在锁内重读、比对 plan 时的文件摘要，不等即记 conflict、该文件
零写入。锁只挡住同样走 `apply` 的写入方；不配合锁的外部编辑器在「比摘要」与 `os.replace`
之间保存，那一次改动仍会被覆盖。

**已知边界**：段外人写矛盾句的预览按状态词粗筛（`CONTRADICTION_WORDS`），只列不改，可能漏、
也可能误列；YAML 字段优先用 PyYAML 解析，没装时退回平铺读法（块标量与单引号转义照读，
其余复杂形态可能读不全，只影响显示）。
"""

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import _doc_state  # noqa: E402

# 最多 3 个空格缩进：4 个空格起是 Markdown 的缩进代码块，那里的标记是演示
MARKER_LINE_RE = re.compile(r"^ {0,3}<!-- WORKFRAME:AUTO-INDEX:(START|END)(?::([A-Za-z0-9_-]+))? -->[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
NOTICE = "> ⚙️ 此段由 `module-index-refresh` 自动维护，请勿手动编辑。"
NOTICE_PREFIX = "> ⚙️ 此段由"
CS_NOTICE_V2 = ("> ⚙️ 此段由 `module-index-refresh` 自动维护。每行「：」之后到第一个「；」由脚本按 "
                "`current-state/` 取数，「；」之后的文字由人写、刷新时原样保留。")
UNWRITTEN_TOKEN = "_（未编写）_"
NO_SUMMARY_TOKEN = "_（未填摘要）_"
UNSYNCED_TOKEN = "_（未同步）_"
PRD_UNWRITTEN_STATUS = "已立项、未开写"
MISSING_PRD = "_缺 prd.md_"
# 出厂旧模板「最近同步」行的占位全文（不是人写注记，转换时整体换掉）
OLD_SYNC_PLACEHOLDER = "last_synced_at；已同步后在日期后附「基准 commit <last_synced_ref>」括注"

ALWAYS_REFRESH = frozenset({"basic-modules-index", "submodules-index"})
STATUS_ORDER = ("active", "planning", "done", "dropped")
STATUS_LABELS = {"active": "进行中", "planning": "规划", "done": "已完成", "dropped": "已放弃"}
# 段外矛盾句的粗筛词：需求状态 → 与之矛盾的说法
CONTRADICTION_WORDS = {
    "not-started": ("尚未启动", "未启动", "尚未开始", "未开始", "待启动"),
    "shipped": ("已上线", "已发布", "已交付", "已完成"),
}
_CONTRADICTS = {"done": ("not-started",), "dropped": ("not-started", "shipped"),
                "active": ("not-started",), "planning": ("shipped",)}

# current-state 摘要行：标签 → 现状文件
CS_ROWS = (("架构", "architecture.md"), ("API", "api-surface.md"),
           ("数据模型", "data-model.md"), ("代码索引", "code-map.md"))
SYNC_LABEL = "最近同步"
_CS_ROW_RE = re.compile(r"^- \*\*(" + "|".join(re.escape(l) for l, _ in CS_ROWS) + r")\*\*：(.*)$")
_SYNC_ROW_RE = re.compile(r"^(- \*\*" + SYNC_LABEL + r"\*\*：)(.*)$")
_DATE_TOKEN_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:?\d{2}|Z)?)?")
# ref token 只认 hash 或 commit: / branch: / tag: 形态，括注里紧跟的人写文字（「、仅测主仓」）不算 ref
_REF_PHRASE_RE = re.compile(r"^[ \t]*（基准 commit[:：]?[ \t]*([0-9a-fA-F]{7,40}|(?:commit|branch|tag):[A-Za-z0-9._/-]+)")
_PLACEHOLDER_TOKEN_RE = re.compile(r"^_（([^）]*)）_(.*)$")

# 各类段 v2 的表头序列（表格段）；requirements-index 的 v2 没有表，靠计数行认
V2_HEADERS = {
    "requirements-by-status": (
        ("req_slug", "子需求数", "owner", "摘要"),
        ("req_slug", "owner", "摘要"),
        ("req_slug", "子需求数", "完成时间", "摘要"),
        ("req_slug", "放弃时间", "原因", "摘要"),
        ("req_slug", "实际取值", "owner", "摘要"),
    ),
    "sub-requirements-index": (("子需求", "状态", "摘要", "PRD", "评审", "用例"),),
    "requirements-index": (),
}
_COUNT_LINE_RE = re.compile(r"^进行中 \d+ · 规划 \d+ · 已完成 \d+ · 已放弃 \d+ · 状态未知 \d+$")
# 转换预览里「项目自加的列」= 不在出厂各版表头里的列
_KNOWN_COLUMNS = {
    "requirements-index": {"req_slug", "status", "子需求数", "owner", "摘要"},
    "requirements-by-status": {"req_slug", "子需求数", "owner", "摘要", "完成时间", "放弃时间", "原因", "实际取值"},
    "sub-requirements-index": {"子需求", "状态", "摘要", "PRD", "复盘", "评审", "用例"},
}
# 基础模块总览的出厂示例图（段外）：转换时按 --choice 逐份「换成说明」或保留
EXAMPLE_DIAGRAM = "```mermaid\ngraph LR\n    A[子模块 A] --> B[子模块 B]\n```"
EXAMPLE_NOTE = "<!-- 该基础模块的内部架构 / 子模块协作图 / 关键流程，建议用 mermaid。 -->"
# 出厂旧模板里紧挨在示例图上方的那行引导注释：两者一起换成 EXAMPLE_NOTE，免得留两行同义注释
EXAMPLE_LEAD = "<!-- 该基础模块的内部架构 / 子模块协作图 / 关键流程。建议用 mermaid。 -->"
CHOICES = ("replace-example", "keep")
STRUCTURAL = frozenset({"missing-segment", "unpaired", "duplicate", "unknown-segment",
                        "unreadable", "unreadable-source"})

SCHEMA = "workframe.nav.v1"


class ScopeError(Exception):
    """scope 解析失败（空、不存在、落在项目外、归不到基础模块、不在 --basic 内）。CLI 退出码 2。"""


# ---------------------------------------------------------------- 读取工具（module_init 共用）


def _scalar(raw: str) -> str:
    """YAML 平铺标量：带引号取引号内（引号内的 # 不是注释；单引号内 '' 是转义），否则去行尾注释；
    null / ~ 视为空。"""
    raw = raw.strip()
    if raw[:1] == '"':
        end = raw.find('"', 1)
        while end > 0 and raw[end - 1] == "\\":
            end = raw.find('"', end + 1)
        if end > 0:
            return raw[1:end].replace('\\"', '"')
    if raw[:1] == "'":
        i, out = 1, []
        while i < len(raw):
            if raw[i] == "'":
                if raw[i + 1:i + 2] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out)
            out.append(raw[i])
            i += 1
    val = re.sub(r"\s+#.*$", "", raw).strip()
    if val.startswith("#"):
        val = ""
    return "" if val in ("null", "~") else val


def flat_fields(text: str) -> dict:
    """顶层 `key: value` 平铺读法（没装 PyYAML 时的退路）：标量照读，块标量（`>` / `|` 族）把
    其后缩进行按折叠方式拼成一行。"""
    data = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line or line.startswith((" ", "\t", "#", "-")):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):(?:\s+(.*))?$", line)
        if not m or m.group(1) in data:
            continue
        val = (m.group(2) or "").strip()
        if re.fullmatch(r"[>|][+-]?\d?(\s+#.*)?", val):
            block = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
                block.append(lines[i].strip())
                i += 1
            joined = " ".join(b for b in block if b) if val.startswith(">") else "\n".join(block).strip("\n")
            data[m.group(1)] = joined
            continue
        data[m.group(1)] = _scalar(val)
    return data


def yaml_fields(text: str) -> dict:
    """顶层标量字段：PyYAML 在就用真解析（列表、映射跳过；None → ""），解析失败或没装退回 flat_fields。"""
    try:
        import yaml
        # BaseLoader：所有标量按原文字符串读——safe_load 会把 01234567 读成八进制、12e45678 读成浮点，
        # 基准 hash 就写错了；这里只要显示用的原文
        data = yaml.load(text, Loader=yaml.BaseLoader)
    except Exception:
        return flat_fields(text)
    if not isinstance(data, dict):
        return flat_fields(text)
    return {str(k): ("" if v is None or v in ("null", "Null", "NULL", "~") else str(v)) for k, v in data.items()
            if not isinstance(v, (list, dict))}


def _read(path: Path, rec=None):
    """读字节；rec 给出时记下（项目内路径, 摘要或 missing）供确认码用。读失败照常抛出。"""
    if not path.is_file():
        if rec is not None:
            rec.append((path.as_posix(), "missing"))
        return None
    raw = path.read_bytes()
    if rec is not None:
        rec.append((path.as_posix(), hashlib.sha256(raw).hexdigest()))
    return raw


def flat_yaml(path: Path, rec=None) -> dict:
    raw = _read(path, rec)
    if raw is None:
        return {}
    return yaml_fields(raw.decode("utf-8-sig"))


def frontmatter_fields(raw: bytes) -> dict:
    """Markdown frontmatter 的顶层标量字段；无 / 不闭合返回 {}。"""
    parts = _doc_state.split(raw)
    if parts.fm_lines is None:
        return {}
    text = "\n".join(raw[s:e].decode("utf-8").rstrip("\r\n") for s, e in parts.fm_lines)
    return yaml_fields(text)


def clean_summary(text: str) -> str:
    """摘要清洗：链接展开为显示文本、去粗体、竖线换全角——脏字符进表格会破列，
    截断切穿 wikilink 会坏渲染（2026-08-11 真实项目树沙盒实测抓出）。"""
    text = re.sub(r"\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]",
                  lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return text.replace("**", "").replace("|", "／").strip()


def _clip(text: str) -> str:
    return text[:80] + ("…" if len(text) > 80 else "")


def _trim(text: str) -> str:
    """description 类取数源本身就是一句话摘要：只清洗、截断到 80 字，不切句。"""
    return _clip(clean_summary(" ".join(text.split())))


def _first_sentence(text: str) -> str:
    """首句：有「。」切到第一个「。」（保留），否则「；」，否则换行；清洗后截断。顺序是既有口径——
    上两层导航一直这样取定位段摘要，改顺序会改写每个项目的总览。"""
    for sep in ("。", "；", "\n"):
        if sep in text:
            text = text.split(sep)[0] + ("。" if sep == "。" else "")
            break
    return _clip(clean_summary(text))


def positioning_summary(overview_path: Path, yaml_fallback: dict, rec=None) -> str:
    """定位段首句 → yaml notes / description → '-'；清洗后 ≤80 字符。"""
    text = ""
    raw = _read(overview_path, rec)
    if raw is not None:
        lines = raw.decode("utf-8-sig").splitlines()
        try:
            i = next(n for n, l in enumerate(lines) if l.strip() == "## 定位")
            for l in lines[i + 1:]:
                s = l.strip()
                if not s:
                    continue
                if s.startswith((">", "#", "```", "<!--")):
                    break  # 引导占位 = 尚无人写定位
                text = s
                break
        except StopIteration:
            pass
    if not text:
        text = yaml_fallback.get("notes") or yaml_fallback.get("description") or ""
    return _first_sentence(text) if text else "-"


def _unfenced(lines):
    """[(下标, 行)]，跳过代码围栏内（含围栏行本身）。"""
    out, fence = [], None
    for i, l in enumerate(lines):
        fm = _FENCE_RE.match(l)
        if fence:
            if fm and fm.group(1)[0] == fence[0] and len(fm.group(1)) >= len(fence):
                fence = None
            continue
        if fm:
            fence = fm.group(1)
            continue
        out.append((i, l))
    return out


def current_state_token(state: str, fm: dict) -> str:
    """现状摘要行的机器 token：未编写 / 缺文件 → 未编写；否则取 frontmatter `description`
    （现状模板里它是必填的一句话摘要），清洗后 ≤80 字，「；」换成「，」（它是行内尾注分隔符）；
    已编写却没填 description → 未填摘要。不回落到正文：正文首句常是一条细节，不是摘要。"""
    if state in ("unwritten", "missing"):
        return UNWRITTEN_TOKEN
    desc = fm.get("description") or ""
    return _trim(desc).replace("；", "，") if desc.strip() else NO_SUMMARY_TOKEN


# ---------------------------------------------------------------- 段的定位


@dataclass
class Segment:
    name: "str | None"
    kind: str
    line: int            # START 所在行（1 起）
    body_start: int      # 文本偏移：START 行之后
    body_end: int        # 文本偏移：END 行开头

    def body(self, text):
        return text[self.body_start:self.body_end]


@dataclass
class Diagnostic:
    path: str
    line: int
    code: str            # missing-modules-dir / missing-overview / missing-segment / unpaired / duplicate /
    #                      unknown-segment / unreadable / unreadable-source / unstamped
    detail: str = ""
    segment: "str | None" = None


def modules_dir(project: Path) -> Path:
    return project / "projects" / "modules"


def expected_segments(project: Path, path: Path):
    """按文件层级给出应有的段 [(段名或 None, 段类)]；不归本规划器管的文件返回 None。"""
    try:
        parts = path.relative_to(modules_dir(project)).parts
    except ValueError:
        return None
    if parts[-1:] != ("overview.md",):
        return None
    mdir = modules_dir(project)
    n = len(parts)
    if n == 1:
        return [(None, "basic-modules-index")]
    if not (mdir / parts[0] / "module.yaml").is_file():
        return None
    if n == 2:
        return [(None, "submodules-index")]
    if not (mdir / parts[0] / parts[1] / "submodule.yaml").is_file():
        return None
    if n == 3:
        return [("current-state-summary", "current-state-summary"), ("requirements-index", "requirements-index")]
    if parts[2] != "requirements":
        return None
    if n == 4:
        return [(None, "requirements-by-status")]
    if n == 5 and parts[3] != "_draft":
        return [(None, "sub-requirements-index")]
    return None


def _line_spans(text):
    """[(start, end_with_eol, content)]，按 LF 切；CR 留在行尾、不进 content 比较。"""
    out, pos = [], 0
    for chunk in text.splitlines(keepends=True):
        out.append((pos, pos + len(chunk), chunk.rstrip("\r\n")))
        pos += len(chunk)
    return out


def _outside_fences(text):
    """[(行号, start, end, content)]，代码围栏外的行。"""
    spans = _line_spans(text)
    keep = {i for i, _ in _unfenced([c for _, _, c in spans])}
    return [(i + 1, s, e, c) for i, (s, e, c) in enumerate(spans) if i in keep]


def find_segments(text: str, expected, rel: str):
    """配对独占一行的 START/END（围栏内的不算），与 expected 核对。返回 ({段名: Segment}, [Diagnostic])；
    问题收集后返回、不抛出。"""
    markers = []
    for line, start, end, content in _outside_fences(text):
        m = MARKER_LINE_RE.match(content)
        if m:
            markers.append((m.group(1), m.group(2), line, start, end))
    names = {name: kind for name, kind in expected}
    found, problems, open_ = {}, [], None
    pairs = []
    for typ, name, line, start, end in markers:
        if typ == "START":
            if open_:
                problems.append(Diagnostic(rel, open_[2], "unpaired", f"START{_tag(open_[1])} 没有配对的 END", open_[1]))
            open_ = (typ, name, line, start, end)
        else:
            if open_ and open_[1] == name:
                pairs.append((name, open_[2], open_[4], start))
            else:
                problems.append(Diagnostic(rel, line, "unpaired", f"END{_tag(name)} 前没有配对的 START", name))
            open_ = None
    if open_:
        problems.append(Diagnostic(rel, open_[2], "unpaired", f"START{_tag(open_[1])} 没有配对的 END", open_[1]))
    seen = {}
    for name, line, body_start, body_end in pairs:
        seen.setdefault(name, []).append((line, body_start, body_end))
    for name, occ in seen.items():
        if name not in names:
            problems.append(Diagnostic(rel, occ[0][0], "unknown-segment", f"多出未知段{_tag(name)}", name))
        elif len(occ) > 1:
            problems.append(Diagnostic(rel, occ[1][0], "duplicate", f"段{_tag(name)}出现 {len(occ)} 次", name))
        else:
            line, b0, b1 = occ[0]
            found[name] = Segment(name, names[name], line, b0, b1)
    unpaired_names = {p.segment for p in problems if p.code == "unpaired"}
    for name in names:
        if name not in seen and name not in unpaired_names:
            problems.append(Diagnostic(rel, 0, "missing-segment", f"缺段{_tag(name)}", name))
    for name in unpaired_names:
        found.pop(name, None)
    return found, problems


def _tag(name):
    return f":{name}" if name else "（匿名）"


# ---------------------------------------------------------------- 版式


def _split_row(line: str):
    """表格行按未转义的竖线切列。"""
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _is_sep(line: str) -> bool:
    return bool(re.fullmatch(r"\|(\s*:?-{3,}:?\s*\|)+", line.strip()))


def _table_headers(body: str):
    lines = body.splitlines()
    return [tuple(_split_row(lines[i])) for i in range(len(lines) - 1)
            if lines[i].strip().startswith("|") and _is_sep(lines[i + 1])]


def _first_line(body: str) -> str:
    return next((l.rstrip("\r") for l in body.splitlines() if l.strip()), "")


def layout_of(kind: str, body: str) -> str:
    if kind in ALWAYS_REFRESH:
        return "v2"
    if kind == "current-state-summary":
        return "v2" if _first_line(body) == CS_NOTICE_V2 else "legacy"
    heads = tuple(_table_headers(body))
    if heads != V2_HEADERS[kind]:
        return "legacy"
    if kind == "requirements-index" and not any(_COUNT_LINE_RE.match(l.strip()) for l in body.splitlines()):
        return "legacy"
    return "v2"


# ---------------------------------------------------------------- 取数


def _rel_link(target: str) -> str:
    """尖括号链接目标：百分号编码 `%` `#` `<` `>` `|`——读者（Mortar）先按 `#` 切锚点再解码。"""
    for ch, enc in (("%", "%25"), ("#", "%23"), ("<", "%3C"), (">", "%3E"), ("|", "%7C")):
        target = target.replace(ch, enc)
    return "<./" + target + ">"


def _cell(text: str) -> str:
    return (text or "-").replace("|", "／").replace("\r", " ").replace("\n", " ")


def _attachments(d: Path, rec=None):
    """`.gitkeep` 之外的文件，按相对路径排序；清单本身计入确认码。"""
    if not d.is_dir():
        if rec is not None:
            rec.append((d.as_posix() + "/", "missing"))
        return []
    files = sorted(p.relative_to(d).as_posix() for p in d.rglob("*") if p.is_file() and p.name != ".gitkeep")
    if rec is not None:
        rec.append((d.as_posix() + "/", hashlib.sha256("\n".join(files).encode("utf-8")).hexdigest()))
    return files


def _doc_info(path: Path, rec=None):
    """(state, frontmatter 字段)；state ∈ written / unwritten / unstamped / missing。读失败照常抛出。"""
    raw = _read(path, rec)
    if raw is None:
        return "missing", {}
    return _doc_state.effective_state(raw), frontmatter_fields(raw)


def _req_dirs(s_dir: Path):
    rdir = s_dir / "requirements"
    if not rdir.is_dir():
        return []
    return sorted(d for d in rdir.iterdir()
                  if d.is_dir() and d.name != "_draft" and not d.name.startswith(".")
                  and ((d / "meta.yaml").is_file() or (d / "overview.md").is_file()))


def _sub_req_dirs(r_dir: Path):
    return sorted(d for d in r_dir.iterdir() if d.is_dir() and not d.name.startswith("."))


def _req_record(r_dir: Path, rec=None):
    meta = flat_yaml(r_dir / "meta.yaml", rec)
    summary = ""
    state, fm = _doc_info(r_dir / "main" / "prd.md", rec)
    if state in ("written", "unstamped") and fm.get("description"):
        summary = fm["description"]
    if not summary:
        raw = _read(r_dir / "overview.md", rec)
        if raw is not None:
            summary = frontmatter_fields(raw).get("description", "")
    subs = _sub_req_dirs(r_dir)
    if rec is not None:
        rec.append((r_dir.as_posix() + "/*", hashlib.sha256("\n".join(d.name for d in subs).encode("utf-8")).hexdigest()))
    return {
        "slug": r_dir.name,
        "status": meta.get("status", ""),
        "owner": meta.get("owner") or "-",
        "updated_at": meta.get("updated_at") or "-",
        "notes": meta.get("notes") or "-",
        "subs": len(subs),
        "summary": _trim(summary) if summary else "-",
    }


# ---------------------------------------------------------------- 渲染（纯函数：只吃 ctx）


def _table(header, rows, empty):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += rows or [empty]
    return out


def render(kind: str, ctx: dict) -> str:
    """段体（START 行之后、END 行之前的全部文本，LF、以换行结尾）。"""
    lines = _RENDERERS[kind](ctx)
    return "\n".join(lines) + "\n"


def _render_top(ctx, label, empty):
    rows = [f"| [{r['name']}](./{r['name']}/overview.md) | {r['status']} | {r['summary']} |" for r in ctx["rows"]]
    if not rows:
        rows = [f"| _（{empty}运行 `/core:module-init` 创建第一个）_ | - | - |"]
    return [NOTICE, "", f"| {label} | 状态 | 摘要 |", "|---|---|---|"] + rows


def _render_basic_modules(ctx):
    return _render_top(ctx, "基础模块", "暂无基础模块。")


def _render_submodules(ctx):
    return _render_top(ctx, "子模块", "暂无子模块。")


def _placeholder_annotation(rest: str):
    """rest 以斜体占位开头时返回 (占位里「；」之后的人写注记, 占位之后的文字)；否则 None。
    出厂旧模板的「最近同步」占位全文不是人写注记。"""
    pm = _PLACEHOLDER_TOKEN_RE.match(rest)
    if not pm:
        return None
    inner, after = pm.groups()
    if inner == OLD_SYNC_PLACEHOLDER or "；" not in inner:
        return "", after
    return inner.split("；", 1)[1], after


def _sync_line(rest: str, date: str, ref: str) -> str:
    ph = _placeholder_annotation(rest)
    if ph is not None:
        note, after = ph
        tail = ("；" + note if note else "") + after
    else:
        m = _DATE_TOKEN_RE.match(rest)
        tail = rest[m.end():] if m else (("；" + rest) if rest else "")
    rm = _REF_PHRASE_RE.match(tail)
    if rm:
        if ref:
            tail = tail[:rm.start(1)] + ref + tail[rm.end(1):]
        else:
            after = tail[rm.end():]
            for sep in ("；", "——", "，", " "):
                if after.startswith(sep):
                    after = after[len(sep):]
                    break
            tail = after[1:] if after.startswith("）") else "（" + after
    elif ref:
        tail = f"（基准 commit {ref}）" + tail
    return date + tail


def _render_current_state(ctx):
    old = ctx.get("old_body", "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while old and not old[-1].strip():
        old.pop()
    docs, date, ref = ctx["docs"], ctx["sync_date"], ctx["sync_ref"]
    # 旧版式里「；」不是尾注分隔符：后半截是同一句旧摘要，转换时随旧 token 一起换掉，否则与新 token 重复
    legacy = layout_of("current-state-summary", ctx.get("old_body", "")) == "legacy"
    out, seen = [], set()
    first = next((i for i, l in enumerate(old) if l.strip()), None)
    for i, line in enumerate(old):
        if i == first and line.startswith(NOTICE_PREFIX):
            out.append(CS_NOTICE_V2)          # 只换段体第一行的说明；别处的说明行归人写
            seen.add("notice")
            continue
        m = _CS_ROW_RE.match(line)
        if m:
            label, rest = m.group(1), m.group(2)
            ph = _placeholder_annotation(rest)
            if ph is not None:   # 斜体占位整个是 token；占位里「；」之后的人写注记挪成尾注，不切穿斜体
                note, after = ph
                tail = ("；" + note if note else "") + after
            else:
                tail = rest[rest.index("；"):] if "；" in rest and not legacy else ""
            token = docs.get(label)
            out.append(line if token is None else f"- **{label}**：{token}{tail}")
            seen.add(label)
            continue
        m = _SYNC_ROW_RE.match(line)
        if m:
            out.append(m.group(1) + _sync_line(m.group(2), date, ref))
            seen.add(SYNC_LABEL)
            continue
        out.append(line)
    if "notice" not in seen:
        out[:0] = [CS_NOTICE_V2, ""]
    for label, _ in CS_ROWS:
        if label not in seen:
            out.append(f"- **{label}**：{docs.get(label) or UNWRITTEN_TOKEN}")
    if SYNC_LABEL not in seen:
        out.append(f"- **{SYNC_LABEL}**：" + _sync_line("", date, ref))
    return out + [""]


def _render_requirements_index(ctx):
    c = ctx["counts"]
    count = " · ".join(f"{STATUS_LABELS[s]} {c.get(s, 0)}" for s in STATUS_ORDER) + f" · 状态未知 {c.get('unknown', 0)}"
    return [NOTICE + "基于 `requirements/<req_slug>/meta.yaml` 计数。", "", count, "",
            "完整清单见 [requirements/overview.md](<./requirements/overview.md>)。", ""]


def _render_by_status(ctx):
    by = {s: [r for r in ctx["reqs"] if r["status"] == s] for s in STATUS_ORDER}
    unknown = [r for r in ctx["reqs"] if r["status"] not in STATUS_ORDER]
    h = V2_HEADERS["requirements-by-status"]
    out = [NOTICE + "基于 `<req_slug>/meta.yaml` 提炼。", "", "## active 进行中", ""]
    out += _table(h[0], [f"| {r['slug']} | {r['subs']} | {_cell(r['owner'])} | {r['summary']} |" for r in by["active"]],
                  "| _（暂无）_ | - | - | - |")
    out += ["", "## planning 规划中", ""]
    out += _table(h[1], [f"| {r['slug']} | {_cell(r['owner'])} | {r['summary']} |" for r in by["planning"]],
                  "| _（暂无）_ | - | - |")
    out += ["", "## done 已完成", ""]
    out += _table(h[2], [f"| {r['slug']} | {r['subs']} | {_cell(r['updated_at'])} | {r['summary']} |" for r in by["done"]],
                  "| _（暂无）_ | - | - | - |")
    out += ["", "## dropped 已放弃", ""]
    out += _table(h[3], [f"| {r['slug']} | {_cell(r['updated_at'])} | {_cell(clean_summary(r['notes']))} | {r['summary']} |"
                         for r in by["dropped"]], "| _（暂无）_ | - | - | - |")
    out += ["", "## _未知_ 状态缺失 / 非法", "",
            "> `meta.yaml` 缺 `status` 或取值不在四态内的需求渲染到这张表。",
            "> 不是长期归置区——补上 `meta.yaml.status` 后重跑索引即归位。", ""]
    out += _table(h[4], [f"| {r['slug']} | {_cell(r['status'] or '_缺失_')} | {_cell(r['owner'])} | {r['summary']} |"
                         for r in unknown], "| _（暂无）_ | - | - | - |")
    return out + [""]


def _links(prefix, files):
    if not files:
        return "-"
    out = []
    for f in files:
        label = _cell(re.sub(r"\.md$", "", f)).replace("[", "［").replace("]", "］")
        out.append("[" + label + "](" + _rel_link(prefix + f) + ")")
    return " · ".join(out)


def _render_sub_requirements(ctx):
    rows = []
    for s in ctx["subs"]:
        name = s["name"]
        reviews = _links(f"{name}/reviews/", s["reviews"])
        cases = _links(f"{name}/test-cases/", s["cases"])
        if s["state"] == "missing":
            rows.append(f"| {name} | {MISSING_PRD} | {MISSING_PRD} | - | {reviews} | {cases} |")
            continue
        status = PRD_UNWRITTEN_STATUS if s["state"] == "unwritten" else _cell(s["status"])
        summary = "-" if s["state"] == "unwritten" or not s["description"] else _trim(s["description"])
        rows.append(f"| {name} | {status} | {summary} | [prd]({_rel_link(name + '/prd.md')}) | {reviews} | {cases} |")
    out = [NOTICE + "基于 `<sub_req_slug>/prd.md` 与其 `reviews/`、`test-cases/` 提炼。", ""]
    out += _table(V2_HEADERS["sub-requirements-index"][0], rows, "| _（暂无子需求）_ | - | - | - | - | - |")
    return out + [""]


_RENDERERS = {
    "basic-modules-index": _render_basic_modules,
    "submodules-index": _render_submodules,
    "current-state-summary": _render_current_state,
    "requirements-index": _render_requirements_index,
    "requirements-by-status": _render_by_status,
    "sub-requirements-index": _render_sub_requirements,
}


def _top_rows(parent: Path, marker: str, rec=None):
    rows = []
    if parent.is_dir():
        for d in sorted((d for d in parent.iterdir() if d.is_dir() and (d / marker).is_file()), key=lambda d: d.name):
            y = flat_yaml(d / marker, rec)
            rows.append({"name": d.name, "status": y.get("status") or "-",
                         "summary": positioning_summary(d / "overview.md", y, rec)})
    return rows


def build_ctx(project: Path, path: Path, kind: str, old_body: str, rec=None) -> dict:
    """从实物取数。path 是该段所在的 overview；rec 收集读过的源（确认码用）。读失败照常抛出。"""
    if kind == "basic-modules-index":
        return {"rows": _top_rows(path.parent, "module.yaml", rec)}
    if kind == "submodules-index":
        return {"rows": _top_rows(path.parent, "submodule.yaml", rec)}
    if kind == "current-state-summary":
        s_dir = path.parent
        docs = {}
        for label, fname in CS_ROWS:
            p = s_dir / "current-state" / fname
            docs[label] = current_state_token(*_doc_info(p, rec))
        y = flat_yaml(s_dir / "submodule.yaml", rec)
        at = y.get("last_synced_at", "")
        date = at[:10] if re.match(r"\d{4}-\d{2}-\d{2}", at) else (at or UNSYNCED_TOKEN)
        return {"old_body": old_body, "docs": docs, "sync_date": date, "sync_ref": y.get("last_synced_ref", "")}
    if kind == "requirements-index":
        counts = {}
        for r in _req_dirs(path.parent):
            st = flat_yaml(r / "meta.yaml", rec).get("status", "")
            key = st if st in STATUS_ORDER else "unknown"
            counts[key] = counts.get(key, 0) + 1
        return {"counts": counts}
    if kind == "requirements-by-status":
        return {"reqs": [_req_record(r, rec) for r in _req_dirs(path.parent.parent)]}
    if kind == "sub-requirements-index":
        subs = []
        for d in _sub_req_dirs(path.parent):
            state, fm = _doc_info(d / "prd.md", rec)
            subs.append({"name": d.name, "state": state, "status": fm.get("status", ""),
                         "description": fm.get("description", ""),
                         "reviews": _attachments(d / "reviews", rec), "cases": _attachments(d / "test-cases", rec)})
        return {"subs": subs}
    raise ValueError(kind)


# ---------------------------------------------------------------- 规划


@dataclass
class Change:
    path: str            # 项目相对路径
    segment: "str | None"
    seg_kind: str
    kind: str            # unchanged / update / legacy / convert / blocked
    old: str
    new: str
    file_sha256: str
    line: int
    body_start: int = 0
    body_end: int = 0
    sources: tuple = ()  # ((项目相对路径, 摘要), …)

    def diff(self):
        """(首个差异行在文件中的行号, 旧行, 新行)：旧行 = 只在旧段体里的行，新行同理。"""
        old = self.old.replace("\r\n", "\n").split("\n")
        new = self.new.split("\n")
        k = next((i for i, (a, b) in enumerate(zip(old, new)) if a != b), min(len(old), len(new)))
        new_set, old_set = set(new), set(old)
        return (self.line + 1 + k, [l for l in old if l.strip() and l not in new_set],
                [l for l in new if l.strip() and l not in old_set])


@dataclass
class Plan:
    project: Path
    changes: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)
    examples: list = field(default_factory=list)        # 段外的出厂示例图：[(path, file_sha256, line)]
    contradictions: list = field(default_factory=list)  # 段外人写矛盾句：[{path, line, text, status}]
    extra_sources: list = field(default_factory=list)   # 预览里除段内容外读过的源（矛盾句的 meta）
    convert: bool = False


@dataclass
class Result:
    path: str
    status: str          # written / unchanged / conflict / error
    reason: str = ""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def target_overviews(project: Path):
    """全量的导航文件：[(path, expected)]；应有而缺的 overview 记成 Diagnostic。"""
    mdir = modules_dir(project)
    out, missing = [], []

    def want(p: Path):
        exp = expected_segments(project, p)
        if exp is None:
            return
        if p.is_file():
            out.append((p, exp))
        else:
            missing.append(Diagnostic(_rel(project, p), 0, "missing-overview", "缺 overview.md"))

    if not mdir.is_dir():
        return out, missing
    want(mdir / "overview.md")
    for b in sorted(d for d in mdir.iterdir() if d.is_dir() and (d / "module.yaml").is_file()):
        want(b / "overview.md")
        for s in sorted(d for d in b.iterdir() if d.is_dir() and (d / "submodule.yaml").is_file()):
            want(s / "overview.md")
            want(s / "requirements" / "overview.md")
            for r in _req_dirs(s):
                want(r / "overview.md")
    return out, missing


def _rel(project: Path, path: Path) -> str:
    return path.relative_to(project).as_posix()


def _rel_src(project: Path, recorded: str) -> str:
    """rec 里记的是绝对路径，目录清单带 `/` 或 `/*` 后缀；转成项目相对路径（后缀保留）。"""
    base = recorded.rstrip("/*")
    try:
        return Path(base).relative_to(project).as_posix() + recorded[len(base):]
    except ValueError:
        return recorded


def resolve_scope(project: Path, scope, basic=None):
    """scope 列表 → 受影响 overview 的集合；None = 全量（或只限 --basic）。非法即 ScopeError。"""
    mdir = modules_dir(project).resolve()
    if basic is not None:
        if not (mdir / basic / "module.yaml").is_file():
            raise ScopeError(f"--basic {basic}：不是基础模块")
    if scope is None:
        if basic is None:
            return None
        scope = [str(mdir / basic)]
    if not scope:
        raise ScopeError("--scope 不能为空（空 scope 不退化成全量）")
    wanted = set()
    for raw in scope:
        if not raw or not raw.strip():
            raise ScopeError("--scope 含空路径")
        p = Path(raw)
        if not p.is_absolute():
            p = (project / p) if str(p).replace("\\", "/").startswith("projects/") else mdir / p
        p = p.resolve()
        if not p.exists():
            raise ScopeError(f"--scope {raw}：路径不存在")
        try:
            parts = p.relative_to(mdir).parts
        except ValueError:
            raise ScopeError(f"--scope {raw}：不在 projects/modules/ 内")
        if not parts or not (mdir / parts[0] / "module.yaml").is_file():
            raise ScopeError(f"--scope {raw}：归不到任何基础模块")
        if basic is not None and parts[0] != basic:
            raise ScopeError(f"--scope {raw}：不在 --basic {basic} 内")
        b = mdir / parts[0]
        wanted |= {mdir / "overview.md", b / "overview.md"}
        if len(parts) >= 2 and (b / parts[1] / "submodule.yaml").is_file():
            s = b / parts[1]
            wanted |= {s / "overview.md", s / "requirements" / "overview.md"}
            if len(parts) >= 4 and parts[2] == "requirements" and parts[3] != "_draft" and (s / "requirements" / parts[3]).is_dir():
                wanted.add(s / "requirements" / parts[3] / "overview.md")
        if basic is not None and len(parts) == 1:
            for s in (d for d in b.iterdir() if d.is_dir() and (d / "submodule.yaml").is_file()):
                wanted |= {s / "overview.md", s / "requirements" / "overview.md"}
                wanted |= {r / "overview.md" for r in _req_dirs(s)}
    return {w.resolve() for w in wanted}


def _contradictions(text, segs, status, rel):
    """段外、代码围栏外的行里与需求状态矛盾的说法（粗筛，只列不改）。"""
    kinds = _CONTRADICTS.get(status, ())
    if not kinds:
        return []
    inside = [(s.body_start, s.body_end) for s in segs.values()]
    out = []
    for line, start, _, content in _outside_fences(text):
        if any(a <= start < b for a, b in inside) or MARKER_LINE_RE.match(content):
            continue
        hit = [w for k in kinds for w in CONTRADICTION_WORDS[k] if w in content]
        if hit:
            out.append({"path": rel, "line": line, "text": content.strip(), "status": status, "words": hit})
    return out


def plan(project: Path, scope=None, convert=False, basic=None) -> Plan:
    """不写盘。scope 同 resolve_scope；非法 scope 抛 ScopeError。"""
    project = Path(project).resolve()
    wanted = resolve_scope(project, scope, basic)
    result = Plan(project, convert=convert)
    if not modules_dir(project).is_dir():
        result.diagnostics.append(Diagnostic("projects/modules", 0, "missing-modules-dir", "没有 projects/modules/"))
        return result
    targets, missing = target_overviews(project)
    for d in missing:
        if wanted is None or (project / d.path).resolve() in wanted:
            result.diagnostics.append(d)
    for path, expected in targets:
        if wanted is not None and path.resolve() not in wanted:
            continue
        rel = _rel(project, path)
        try:
            raw = path.read_bytes()
            bom = raw.startswith(_doc_state.BOM)
            text = raw[3 if bom else 0:].decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            result.diagnostics.append(Diagnostic(rel, 0, "unreadable", f"{type(e).__name__}: {e}"))
            continue
        sha = _sha(raw)
        segs, problems = find_segments(text, expected, rel)
        file_changes = []
        for name, seg in sorted(segs.items(), key=lambda kv: kv[1].line):
            old = seg.body(text)
            rec = []
            try:
                new = render(seg.kind, build_ctx(project, path, seg.kind, old, rec))
            except (OSError, UnicodeDecodeError, ValueError) as e:
                problems.append(Diagnostic(rel, seg.line, "unreadable-source",
                                           f"取数源读不了：{type(e).__name__}: {e}", name))
                continue
            sources = tuple(sorted({(_rel_src(project, p), h) for p, h in rec}))
            same = old.replace("\r\n", "\n") == new
            layout = layout_of(seg.kind, old)
            if same:
                kind = "unchanged"
            elif layout == "legacy":
                kind = "convert" if convert else "legacy"
            else:
                kind = "update"
            file_changes.append(Change(rel, name, seg.kind, kind, old, new, sha, seg.line,
                                       seg.body_start, seg.body_end, sources))
        if any(p.code in STRUCTURAL for p in problems):
            for c in file_changes:   # 段结构或取数有问题：整份不写，只报
                if c.kind in ("update", "convert", "legacy"):
                    c.kind = "blocked"
        result.diagnostics.extend(problems)
        result.changes.extend(file_changes)
        if convert and expected[0][1] == "submodules-index" and not any(p.code in STRUCTURAL for p in problems):
            lf = text.replace("\r\n", "\n")
            if EXAMPLE_DIAGRAM in lf:
                result.examples.append((rel, sha, lf[:lf.index(EXAMPLE_DIAGRAM)].count("\n") + 1))
        if convert and expected[0][1] == "sub-requirements-index":
            try:
                crec = []   # 矛盾句清单也是预览内容：它读的 meta 一并进确认码
                status = flat_yaml(path.parent / "meta.yaml", crec).get("status", "")
                result.extra_sources.extend((_rel_src(project, s), h) for s, h in crec)
            except (OSError, UnicodeDecodeError):
                status = ""
            result.contradictions.extend(_contradictions(text, segs, status, rel))
    result.diagnostics.extend(unstamped_docs(project, wanted))
    return result


def unstamped_docs(project: Path, wanted=None):
    """独立遍历全部现状四件与 prd.md，报 `unstamped`（不靠导航渲染顺带——legacy 段不渲染，靠顺带会漏）。"""
    out = []
    mdir = modules_dir(project)
    if not mdir.is_dir():
        return out
    sub_dirs = None
    if wanted is not None:   # 限定 scope 时只看 scope 覆盖到的子模块
        sub_dirs = {w.parent for w in wanted if (w.parent / "submodule.yaml").is_file()}
    for p in sorted(mdir.rglob("*.md")):
        kind = _doc_state.kind_of(p)
        if kind is None or (kind == "prd" and p.parent.parent.parent.name != "requirements"):
            continue
        if "_draft" in p.relative_to(mdir).parts:
            continue
        if sub_dirs is not None and not any(s in p.resolve().parents for s in sub_dirs):
            continue
        try:
            state = _doc_state.effective_state(p.read_bytes())
        except OSError:
            continue
        if state == "unstamped":
            out.append(Diagnostic(_rel(project, p), 0, "unstamped", "未盖未编写戳（按已编写展示）"))
    return out


def has_problems(p: Plan) -> bool:
    return any(d.code != "unstamped" for d in p.diagnostics)


# ---------------------------------------------------------------- 转换预览


def convert_details(change: Change):
    """转换预览：将消失的旧行、项目自加的列及其有值单元格。"""
    _, gone, _ = change.diff()
    extra = []
    known = _KNOWN_COLUMNS.get(change.seg_kind)
    if known:
        lines = change.old.replace("\r\n", "\n").splitlines()
        i = 0
        while i < len(lines) - 1:
            if lines[i].strip().startswith("|") and _is_sep(lines[i + 1]):
                cols = _split_row(lines[i])
                j = i + 2
                rows = []
                while j < len(lines) and lines[j].strip().startswith("|"):
                    rows.append(_split_row(lines[j]))
                    j += 1
                for ci, col in enumerate(cols):
                    if col not in known:
                        cells = [(r[0], r[ci]) for r in rows if ci < len(r) and r[ci] not in ("", "-")]
                        extra.append({"column": col, "cells": cells})
                i = j
            else:
                i += 1
    return {"gone": gone, "extra_columns": extra}


def confirm_code(p: Plan, choices=None) -> str:
    """sha256（项目根、每个将写文件的摘要、段的新内容摘要、该段读过的每个源的摘要、示例图与选项）。"""
    items = sorted((c.path, c.segment or "", c.file_sha256, _sha(c.new.encode("utf-8")), list(c.sources))
                   for c in p.changes if c.kind in ("update", "convert"))
    ex = sorted((path, sha) for path, sha, _ in p.examples)
    payload = json.dumps({"project": str(p.project), "changes": items, "examples": ex,
                          "extra_sources": sorted(set(p.extra_sources)),
                          "choices": sorted((choices or {}).items())}, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- 写入


def _lock_path(project: Path) -> Path:
    """锁放运行态目录，不放 overview 同目录：那里是项目文档区，锁文件会出现在用户的 git status 里。"""
    import _state_io
    return _state_io.state_dir_of(project) / "module-nav.lock"


def _newline_of(text: str) -> str:
    i = text.find("\n")
    return "\r\n" if i > 0 and text[i - 1] == "\r" else "\n"


def apply(p: Plan, choices=None) -> list:
    """逐文件写 update（convert 计划里再加 convert 与选中的示例图替换）。返回 [Result]。"""
    import _state_io
    writable = ("update", "convert") if p.convert else ("update",)
    by_file = {}
    for c in p.changes:
        by_file.setdefault(c.path, []).append(c)
    ex_files = {path: sha for path, sha, _ in p.examples if (choices or {}).get(path) == "replace-example"}
    results = []
    with _state_io.FileLock(_lock_path(p.project)):
        for rel in sorted(set(by_file) | set(ex_files)):
            todo = [c for c in by_file.get(rel, []) if c.kind in writable]
            if not todo and rel not in ex_files:
                results.append(Result(rel, "unchanged"))
                continue
            path = p.project / rel
            expect = todo[0].file_sha256 if todo else ex_files[rel]
            try:
                raw = path.read_bytes()
                if _sha(raw) != expect:
                    results.append(Result(rel, "conflict", "规划之后文件被改过，本文件未写"))
                    continue
                bom = raw.startswith(_doc_state.BOM)
                text = raw[3 if bom else 0:].decode("utf-8")
                nl = _newline_of(text)
                for c in sorted(todo, key=lambda c: c.body_start, reverse=True):
                    text = text[:c.body_start] + c.new.replace("\n", nl) + text[c.body_end:]
                if rel in ex_files:
                    pair = (EXAMPLE_LEAD + "\n\n" + EXAMPLE_DIAGRAM).replace("\n", nl)
                    if pair in text:
                        text = text.replace(pair, EXAMPLE_NOTE, 1)
                    else:
                        text = text.replace(EXAMPLE_DIAGRAM.replace("\n", nl), EXAMPLE_NOTE, 1)
                data = (_doc_state.BOM if bom else b"") + text.encode("utf-8")
                if data == raw:
                    results.append(Result(rel, "unchanged"))
                    continue
                tmp = path.with_name(f".{path.name}.{os.getpid()}.navtmp")
                tmp.write_bytes(data)
                os.replace(tmp, path)
                results.append(Result(rel, "written"))
            except (OSError, UnicodeDecodeError) as e:
                results.append(Result(rel, "error", f"{type(e).__name__}: {e}"))
    return results
