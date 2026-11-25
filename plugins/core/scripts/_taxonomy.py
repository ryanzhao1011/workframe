"""taxonomy 受控词表的唯一取词函数——`doc-graph-health` 的概念热点与 `requirement-archiving` 的归档
检查共用。import 时零副作用。

只读三节表格：能力域、平台、特殊标记。节标题按 `## <节名>` 认，后面允许跟一个计数括注（如
「## 能力域 (12)」）；只是前缀相同的说明节（「## 能力域说明」）不算。节内只读**第一张表**的数据行首列
反引号词，遇下一个 H2 停——归一化对照表这类别的表格不进词表。

「表」按 GFM 认：表头行之后紧跟分隔行（`---|---`）才算一张表，表头行本身不读；外侧竖线可省；
数据行到空行、非表格行或围栏为止。没有分隔行的竖线文本不是表。代码围栏里的内容不读，闭栏行
不带信息串。

出厂示例词逐词过滤（`EXAMPLE_WORDS`）：真词与示例词混在一节时只有真词生效。

节的三态：节不存在 = 不适用（单平台项目合法地删了平台节）；节在但去掉示例词后为空 = 未配置；
有真词 = 已配置。特殊标记节同样按此判，不写死为已配置。
"""

import re

# 出厂模板各版出现过的示例词（git 历史各版只有这三个）。新的「未配置」标识定下后加在这里。
EXAMPLE_WORDS = frozenset({"示例能力A", "示例能力B", "示例平台"})
SECTIONS = ("能力域", "平台", "特殊标记")

NOT_APPLICABLE = "not_applicable"
UNCONFIGURED = "unconfigured"
CONFIGURED = "configured"

_H2 = re.compile(r"^ {0,3}##[ \t]+(.*?)[ \t#]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_SEP_CELL = re.compile(r"^\s*:?-+:?\s*$")
_WORD = re.compile(r"^`([^`]+)`$")


def _title_of(heading):
    """`能力域 (12)` / `能力域（12）` / `能力域` → 节名；其余返回 None。"""
    for name in SECTIONS:
        if re.fullmatch(re.escape(name) + r"\s*(?:[（(]\d+[)）])?", heading):
            return name
    return None


def _cells(line):
    """表格行 → 单元格列表；不含竖线的行返回 None。"""
    s = line.strip()
    if "|" not in s:
        return None
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_sep(line):
    cells = _cells(line)
    return bool(cells) and all(_SEP_CELL.match(c) for c in cells)


def parse(text):
    """节名 → 真词列表（已滤示例词、去重保序）；None = 节不存在。"""
    out = {name: None for name in SECTIONS}
    lines = text.replace("\r\n", "\n").split("\n")
    current = None      # 当前所在的受认节
    table = "before"    # before / in / done：只读节内第一张表
    fence = None
    for i, line in enumerate(lines):
        m = _FENCE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not m.group(2).strip():
                fence = None
            continue
        if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            fence = m.group(1)
            if table == "in":
                table = "done"
            continue
        h = _H2.match(line)
        if h:
            current = _title_of(h.group(1))
            if current is not None and out[current] is None:
                out[current] = []
            table = "before"
            continue
        if current is None or table == "done":
            continue
        if table == "before":
            # 表头行（本身不读）后紧跟分隔行才进表
            # 缩进 4 个空格起是缩进代码块，不是表；表头与分隔行列数必须相同
            if (not line.startswith("    ") and _cells(line) is not None and i + 1 < len(lines)
                    and _is_sep(lines[i + 1]) and len(_cells(line)) == len(_cells(lines[i + 1]))):
                table = "header"
            continue
        if table == "header":   # 这一行是分隔行
            table = "in"
            continue
        cells = _cells(line) if line.strip() else None
        if cells is None:
            table = "done"
            continue
        w = _WORD.match(cells[0])
        if w:
            word = w.group(1).strip()
            if word and word not in EXAMPLE_WORDS and word not in out[current]:
                out[current].append(word)
    return out


def section_state(words):
    if words is None:
        return NOT_APPLICABLE
    return CONFIGURED if words else UNCONFIGURED
