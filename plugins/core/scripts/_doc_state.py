"""文档「未编写」状态的唯一判定——现状四件与 PRD 骨架。

一份文档是不是还停在骨架，由 frontmatter 顶层的 ``unwritten`` 字段与正文**一起**判：
字段值是建骨架时正文的摘要（``sha256:<64 位小写十六进制>``），当前正文摘要与它相同
才算未编写。正文改过一个字就自动视为已编写——谁写的、走没走 skill 都无所谓，不靠写入方
记得删字段。frontmatter 里的改动（``updated``、``description``、``status``）不算写正文。

三态：

- ``unwritten``：字段是合法摘要，且等于当前正文摘要
- ``written``：没有 frontmatter / frontmatter 不闭合 / 没有该字段 / 合法摘要但不等
- ``unstamped``：字段在，但值不是合法摘要（字面 ``true``、空、拼错、重复键），或行级读数与
  YAML 解析结果对不上（``H#typo`` 在 YAML 里是一整个字符串、带引号的键、多行值）。
  它证明不了正文仍是骨架，**读者一律按已编写处理**，只由收口闸提示去盖戳

字段按行读（写入要按行改），同时用真 YAML 解析器复核同一个值：两边不一致时取不利于
「隐藏正文」的一侧。行内写法本身也按 YAML 的规矩认：注释前必须有空白，带引号的同名键一律算
对不上。没装 PyYAML 时证明不了这一行真是顶层字段（它可能在跨行的流式集合里），所以**一律不判
``unwritten``**（字段在即 ``unstamped``），写入全部拒绝——唯一例外是建子模块时给刚渲染的出厂模板
盖戳（``stamp(..., trusted=True)``，模板的形态是框架自己定的）。

按原字节处理：BOM、行尾风格、文末换行都原样保留，``stamp`` / ``unstamp`` 只动字段那一行
（``unstamp`` 连同它的缩进续行）；写完重新解析，其余字段有任何变化即拒绝。
本模块 import 时零副作用，读者（查询层、导航、收口闸、归档检查）直接 import。
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

MARK_KEY = "unwritten"
BOM = b"\xef\xbb\xbf"
CURRENT_STATE_KINDS = ("architecture", "api-surface", "code-map", "data-model")
KINDS = CURRENT_STATE_KINDS + ("prd",)
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates" / "modules-template"
_TEMPLATE_FILES = {kind: TEMPLATES_DIR / "sub-module" / "current-state" / f"{kind}.md"
                   for kind in CURRENT_STATE_KINDS}
_TEMPLATE_FILES["prd"] = TEMPLATES_DIR / "requirement" / "main" / "prd.md"

DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_MARK_LINE_RE = re.compile(
    r"^(?P<pre>" + MARK_KEY + r"[ \t]*:(?=[ \t]|$)[ \t]*)"
    r"(?P<val>\"[^\"]*\"|'[^']*'|[^\s#][^#]*?|)"
    r"(?P<post>(?:[ \t]+#.*)?[ \t]*)$")
_QUOTED_KEY_RE = re.compile(r"[\"']" + MARK_KEY + r"[\"'][ \t]*:(?=[ \t]|$)")
_PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_]+\}\}")


class _Duplicate:
    def __repr__(self):
        return "DUPLICATE"


DUPLICATE = _Duplicate()


class _Sentinel:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


INCONSISTENT = _Sentinel("INCONSISTENT")   # 行级读数与 YAML 解析结果不一致
ABSENT = _Sentinel("ABSENT")               # 调用方已解析的元数据里没有该键（传给 ``mark``）
_NOT_GIVEN = _Sentinel("NOT_GIVEN")
_NO_YAML = _Sentinel("NO_YAML")
_BROKEN = _Sentinel("BROKEN")


class StampError(Exception):
    """盖戳被拒。``code`` 是机器可读的原因码，消息给人看。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Parts:
    bom: bytes
    fm_lines: tuple          # frontmatter 内各行在 raw 中的 (start, end)，end 含行尾；无 / 不闭合为 None
    fm_close: int            # 结束 `---` 行的起始偏移；无 frontmatter 时为 -1
    body: tuple              # 正文在 raw 中的 (start, end)
    newline: bytes


def _lines(raw, start):
    """从 start 起按行切 raw，返回 [(start, end_with_eol, content_bytes)]。整份没有 LF 而有 CR 时
    按 CR 切（旧式单 CR 换行）——否则字节入口认不出 frontmatter，而读文本的入口经通用换行认得，
    同一份文件两边结论分叉。"""
    term = b"\r" if b"\n" not in raw and b"\r" in raw else b"\n"
    out, pos, n = [], start, len(raw)
    while pos < n:
        nl = raw.find(term, pos)
        end = n if nl < 0 else nl + 1
        content = raw[pos:end].rstrip(b"\n").rstrip(b"\r")
        out.append((pos, end, content))
        pos = end
    return out


def split(raw: bytes) -> Parts:
    """按原字节切出 BOM / frontmatter 行 / 正文区间 / 换行风格。"""
    bom = BOM if raw.startswith(BOM) else b""
    start = len(bom)
    lines = _lines(raw, start)
    newline = (b"\r" if b"\n" not in raw and b"\r" in raw
               else b"\r\n" if lines and raw[lines[0][1] - 2:lines[0][1]] == b"\r\n" else b"\n")
    if lines and lines[0][2].strip() == b"---":
        for i in range(1, len(lines)):
            if lines[i][2].strip() == b"---":
                fm = tuple((s, e) for s, e, _ in lines[1:i])
                return Parts(bom, fm, lines[i][0], (lines[i][1], len(raw)), newline)
    return Parts(bom, None, -1, (start, len(raw)), newline)


def normalize_body(text: str) -> str:
    """正文归一契约（摘要与骨架比对共用，唯一定义）：换行统一为 LF → 每行去行尾空白 →
    去首尾空行 → 以 LF 连接。只改了行尾空白（含 Markdown 硬换行的两个空格）不算写正文。"""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _body_text(raw, parts=None):
    parts = parts or split(raw)
    return raw[parts.body[0]:parts.body[1]].decode("utf-8", errors="replace")


def body_digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(normalize_body(_body_text(raw)).encode("utf-8")).hexdigest()


def _mark_lines(raw, parts):
    """frontmatter 里顶层（无缩进）的该键行：[(start, end, decoded_line_without_eol)]。"""
    found = []
    for s, e in parts.fm_lines or ():
        line = raw[s:e].rstrip(b"\n").rstrip(b"\r").decode("utf-8", errors="replace")
        if re.match(MARK_KEY + r"[ \t]*:(?=[ \t]|$)", line):
            found.append((s, e, line))
    return found


def _unquote(val):
    if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
        return val[1:-1]
    return val.strip()


def _fm_text(raw, parts):
    if not parts.fm_lines:
        return ""
    return raw[parts.fm_lines[0][0]:parts.fm_close].decode("utf-8", errors="replace")


def _parsed_frontmatter(raw, parts):
    """frontmatter 的 YAML 解析结果：dict / _BROKEN（解析失败或不是映射）/ _NO_YAML（没装 PyYAML）。"""
    try:
        from _yaml_probe import safe_load
        data = safe_load(_fm_text(raw, parts))
    except ImportError:
        return _NO_YAML
    except Exception:
        return _BROKEN
    if data is None:
        return {}
    return data if isinstance(data, dict) else _BROKEN


def _parsed_mark(raw, parts):
    data = _parsed_frontmatter(raw, parts)
    if not isinstance(data, dict):
        return data
    return data[MARK_KEY] if MARK_KEY in data else ABSENT


def _resolve(raw, parts, mark, trust_line=False):
    """行级读数与 YAML 值对账后的字段值：None / DUPLICATE / INCONSISTENT / 去引号的字符串。"""
    found = _mark_lines(raw, parts)
    if len(found) > 1:
        return DUPLICATE
    if parts.fm_lines is None:
        return None
    line_val = None
    if found:
        m = _MARK_LINE_RE.match(found[0][2])
        line_val = _unquote(m.group("val")) if m else ""
    if any(_QUOTED_KEY_RE.match(raw[s:e].rstrip(b"\r\n").decode("utf-8", errors="replace"))
           for s, e in parts.fm_lines):
        return INCONSISTENT          # 带引号的同名键：与裸键谁先谁后，解析器只留最后一个
    if mark is _NOT_GIVEN:
        mark = _parsed_mark(raw, parts)
    if mark is _NO_YAML:
        return line_val if trust_line or line_val is None else INCONSISTENT
    if mark is _BROKEN or mark is ABSENT:
        return None if line_val is None else INCONSISTENT
    if line_val is None:
        return INCONSISTENT          # YAML 认得这个键、行扫描认不出（带引号的键、流式写法）
    canonical = "true" if mark is True else mark if isinstance(mark, str) else None
    return line_val if canonical == line_val else INCONSISTENT


def mark_value(raw: bytes, mark=_NOT_GIVEN, trusted=False):
    """该键的值（去引号、去行尾注释）；没有 frontmatter 或没有该键返回 None；重复键返回
    DUPLICATE；行级读数与 YAML 值不一致返回 INCONSISTENT。调用方已用 YAML 解析过 frontmatter
    时把该键的值传进 ``mark``（没有该键传 ``ABSENT``），同一文档不解析两遍。``trusted`` 同
    ``stamp``：只给刚渲染的出厂模板用，没装 PyYAML 时照信行级读数。"""
    return _resolve(raw, split(raw), mark, trust_line=trusted)


def effective_state(raw: bytes, mark=_NOT_GIVEN) -> str:
    """``"written"`` / ``"unwritten"`` / ``"unstamped"``，判法见模块 docstring；``mark`` 同 ``mark_value``。"""
    value = mark_value(raw, mark)
    if value is None:
        return "written"
    if value is DUPLICATE or value is INCONSISTENT or not DIGEST_RE.fullmatch(value):
        return "unstamped"
    return "unwritten" if value == body_digest(raw) else "written"


# ---------------------------------------------------------------- 骨架


def kind_of(path) -> "str | None":
    """按路径认文档类型：``current-state/<四件>.md`` → 件名；``prd.md`` → ``prd``；其余 None。"""
    path = Path(path)
    if path.name == "prd.md" and (path.parent.name != "current-state"
                                   or path.parent.parent.parent.name == "requirements"):
        return "prd"           # 子需求名恰为 current-state 时仍是 PRD；排除的只是子模块的 current-state/prd.md
    if path.parent.name == "current-state" and path.suffix == ".md" and path.stem in CURRENT_STATE_KINDS:
        return path.stem
    return None


def template_skeletons(kind):
    """当前出厂骨架正文（运行时现读模板）。模板缺失时返回空元组，由调用方决定怎么报。
    版本 id 带正文摘要前 8 位：模板一改，盘点确认码随之失效。"""
    path = _TEMPLATE_FILES[kind]
    if not path.is_file():
        return ()
    body = normalize_body(_body_text(path.read_bytes()))
    return ((f"template:{hashlib.sha256(body.encode('utf-8')).hexdigest()[:8]}", body),)


def known_skeletons(kind, extra=()):
    """[(版本 id, 归一后的骨架正文)]：当前模板 → 历史版本 → 调用方传入的 extra。"""
    from _skeletons import HISTORICAL
    out = list(template_skeletons(kind))
    out += [(f"history:{rid}", normalize_body(body)) for rid, body in HISTORICAL.get(kind, ())]
    out += [(sid, normalize_body(body)) for sid, body in extra]
    return out


def _skeleton_regex(body):
    pieces = _PLACEHOLDER_RE.split(body)
    return re.compile("[^\n]+?".join(re.escape(p) for p in pieces))


def match_skeleton(raw: bytes, kind: str, extra=()):
    """正文与某一版已知骨架全文有序一致（渲染变量位通配）时返回该版 id，否则 None。"""
    if kind not in KINDS:
        return None
    body = normalize_body(_body_text(raw))
    for sid, skeleton in known_skeletons(kind, extra):
        if _skeleton_regex(skeleton).fullmatch(body):
            return sid
    return None


def matches_skeleton(raw: bytes, kind: str, extra=()) -> bool:
    return match_skeleton(raw, kind, extra) is not None


# ---------------------------------------------------------------- 写入


def _replace_value(line, value):
    m = _MARK_LINE_RE.match(line)
    post = m.group("post") if m else ""
    return f'{MARK_KEY}: "{value}"{post}'


def _verify_rewrite(old, new, expect, unverified_ok=False):
    """写完重新解析：其余字段必须原样，该键必须是 ``expect``（``ABSENT`` 即应已删掉）。
    没装 PyYAML 时复核不了：只换值的调用方（``unverified_ok``）照写，插行 / 删行的拒绝。"""
    before = _parsed_frontmatter(old, split(old))
    if before is _NO_YAML:
        if unverified_ok:
            return new
        raise StampError("no-yaml", "没装 PyYAML，无法复核插行 / 删行后其余字段不变，未写入")
    if before is _BROKEN:
        raise StampError("bad-yaml", "frontmatter 不是合法的 YAML 映射，先修好再改字段")
    expected = dict(before)
    expected.pop(MARK_KEY, None)
    if expect is not ABSENT:
        expected[MARK_KEY] = expect
    try:
        same = _parsed_frontmatter(new, split(new)) == expected
    except RecursionError:
        raise StampError("bad-yaml", "frontmatter 含自引用的别名，无法复核，请手工改")
    if not same:
        raise StampError("yaml-changed", "改写会连带改动其他字段或写坏 frontmatter，未写入；请手工改")
    return new


def stamp(raw: bytes, kind: str, extra=(), trusted=False) -> bytes:
    """把字面 ``true`` 换成当前正文摘要。只在正文仍与某一版已知骨架一致时才盖。

    已有相等的合法摘要 → 原样返回（幂等）。其余情形抛 StampError：
    ``no-frontmatter`` / ``no-mark`` / ``duplicate`` / ``digest-mismatch``（合法摘要但不等）/
    ``bad-value``（既非 true 也非合法摘要，或行级读数与 YAML 值不一致）/ ``not-skeleton``（``true``
    只说明「从模板来」，不说明「还没人写」——正文已不是骨架时删掉该字段即可）/
    ``bad-yaml`` / ``yaml-changed``（见 ``_verify_rewrite``）/ ``no-yaml``（没装 PyYAML；``trusted``
    只给刚渲染的出厂模板用，此时按行改、不复核）。
    """
    parts = split(raw)
    if parts.fm_lines is None:
        raise StampError("no-frontmatter", "没有闭合的 frontmatter，无处盖戳")
    found = _mark_lines(raw, parts)
    if not found:
        raise StampError("no-mark", f"frontmatter 里没有 {MARK_KEY} 字段")
    if len(found) > 1:
        raise StampError("duplicate", f"{MARK_KEY} 字段重复 {len(found)} 次，先手工留一条")
    s, e, line = found[0]
    if not trusted and _parsed_frontmatter(raw, parts) is _NO_YAML:
        raise StampError("no-yaml", "没装 PyYAML，无法确认该行是顶层字段，未写入")
    value = _resolve(raw, parts, _NOT_GIVEN, trust_line=trusted)
    if value is INCONSISTENT:
        raise StampError("bad-value", f"{MARK_KEY} 的行内写法与 YAML 解析出的值不一致，先手工改成 true")
    digest = body_digest(raw)
    if DIGEST_RE.fullmatch(value):
        if value == digest:
            return raw
        raise StampError("digest-mismatch", "已盖过戳且正文已改动（即已编写）；删掉该字段即可")
    if value != "true":
        raise StampError("bad-value", f"{MARK_KEY} 的值「{value}」既不是 true 也不是合法摘要")
    if not matches_skeleton(raw, kind, extra):
        raise StampError("not-skeleton", "正文已不是骨架，删掉该字段即可")
    eol = raw[s:e][len(raw[s:e].rstrip(b"\n").rstrip(b"\r")):]
    return _verify_rewrite(raw, raw[:s] + _replace_value(line, digest).encode("utf-8") + eol + raw[e:], digest,
                           unverified_ok=trusted)


def insert_stamp(raw: bytes, kind: str, extra=()) -> bytes:
    """存量盘点用：给没有该字段、正文是纯骨架的文件插一行已盖好的字段。

    插在 ``status:`` 字段之后（越过它的多行值，如 ``status: >-`` 的折叠值；没有该字段则插在
    结束 ``---`` 之前），用文件自己的换行风格；其余字节原样。不满足前提抛 StampError
    （同 ``stamp`` 的原因码）。
    """
    parts = split(raw)
    if parts.fm_lines is None:
        raise StampError("no-frontmatter", "没有闭合的 frontmatter，无处盖戳")
    if _mark_lines(raw, parts) or _resolve(raw, parts, _NOT_GIVEN) is not None:
        raise StampError("has-mark", f"已有 {MARK_KEY} 字段，走 stamp")
    if not matches_skeleton(raw, kind, extra):
        raise StampError("not-skeleton", "正文已不是骨架")
    at = parts.fm_close
    fm = parts.fm_lines
    for i, (s, e) in enumerate(fm):
        if re.match(rb"status[ \t]*:", raw[s:e]):
            j = _continuation_end(raw, fm, i)
            at = fm[j - 1][1]
            break
    # frontmatter 内的行后面恒有结束 `---`，所以 `at` 处之前必有行尾
    digest = body_digest(raw)
    line = f'{MARK_KEY}: "{digest}"'.encode("utf-8") + parts.newline
    return _verify_rewrite(raw, raw[:at] + line + raw[at:], digest)


def _continuation_end(raw, fm, i):
    """第 i 行字段的多行值结束于哪一行（不含）。只有值在下一行开始（冒号后为空，或是 ``|`` / ``>``
    块标量）才有续行：缩进的非注释行，以及夹在它们之间的空行与缩进注释。单行值后面、或多行值之后
    的空行与缩进注释不属于它，原样保留；其余跨行写法（引号标量、流式集合）不在这里认，交给写后
    复核拒绝。"""
    head = raw[fm[i][0]:fm[i][1]].rstrip(b"\r\n")
    value = re.sub(rb"(^|[ \t])#.*$", b"", head.split(b":", 1)[1] if b":" in head else b"").strip()
    if value and value[:1] not in (b"|", b">"):
        return i + 1
    j = k = i + 1
    while k < len(fm):
        line = raw[fm[k][0]:fm[k][1]]
        if not line.strip() or (line[:1] in (b" ", b"\t") and line.lstrip().startswith(b"#")):
            k += 1           # 空白行与缩进注释：只有后面还有缩进内容行时才算进去
        elif line[:1] in (b" ", b"\t"):
            k += 1
            j = k
        else:
            break
    return j


def unstamp(raw: bytes) -> bytes:
    """删掉该字段（所有出现，连同缩进续行）。写入方在正文写入成功之后调；没有该字段原样返回。
    删完会连带改动其他字段时抛 StampError——不删也不影响判定（正文一改字段即失效）。"""
    parts = split(raw)
    found = _mark_lines(raw, parts)
    if not found:
        if _resolve(raw, parts, _NOT_GIVEN) is not None:
            raise StampError("bad-value", f"{MARK_KEY} 不是按行可删的写法，请手工删")
        return raw
    fm = parts.fm_lines
    starts = {s for s, _, _ in found}
    out, pos, i = [], 0, 0
    while i < len(fm):
        s = fm[i][0]
        if s in starts:
            j = _continuation_end(raw, fm, i)
            out.append(raw[pos:s])
            pos = fm[j - 1][1]
            i = j
        else:
            i += 1
    out.append(raw[pos:])
    return _verify_rewrite(raw, b"".join(out), ABSENT)
