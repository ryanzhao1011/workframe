#!/usr/bin/env python3
"""YAML 可解析性探针：`module-close-check` 与 `workframe-doctor` 共用的**唯一**判定式。

为什么单独成模块：两处闸问的是同一个问题（「这份文件是不是合法 YAML」），但喂进来的
是**不相交的两个文件集**——close-check 喂 `projects/modules/**`（模块收口面），
doctor 喂 `projects/board.yaml` + `projects/issues/` + `projects/proposals/`（项目级
运行态数据面）。文件集不相交、判定式只此一份，所以这不是双事实源；而判定式若两处
各写一份，宽严必漂。

**本模块证明什么、不证明什么**（写红灯文案与文档时照此措辞，别放宽）：
  证明：这段文本是**合法 YAML**，真解析器能读进去。
  不证明：框架各处的**正则读数与真解析器一致**。「两边都成功但取值不同」这一类
          （最典型是**重复键**——正则取第一个、PyYAML 取最后一个）本模块完全看不见。
          实测：同一个任务块里多写一行 `status:`，两条路径算出不同的 total，而两边
          都不报错。要覆盖那一类得另做正则/解析器分歧对账，不在本模块范围内。

**换行归一：谁做、在哪做**（三处喂的是同一个判定式，谁归一谁不归一必须一眼可见）：

| 调用路径 | 喂进来的文本怎么来的 | 进 `probe_yaml_text` 前是否已归一 |
|---|---|---|
| close-check `.yaml` 分支 | `raw.decode("utf-8")` | **否**（`decode` 不转换换行） |
| close-check frontmatter 分支 | `_frontmatter()` → `read_text()` 再正则切段 | 是（`read_text` 通用换行） |
| doctor `yaml_parse` | `read_text()` | 是（同上） |

三处**不一致是常态**，所以 `probe_yaml_text` **在自己入口统一归一**，调用方一律不必管。
别把归一挪回调用方——那等于要求三处各自记得做同一件事，实测已经漏过一次（见
`probe_yaml_text` docstring 里那段）。新增第四条调用路径时也不用改这张表：归一在被调方。

被 CLI 脚本以同目录 import 方式使用，因此：
  - 模块 import 必须无副作用（不碰 stdout、不读环境、不建目录、不 import PyYAML）
  - 文件名用下划线而非连字符——连字符的模块名 import 不进来
"""

import re

# CRLF 与单独 CR（老 Mac 形态）一并归一为 LF。两种在 YAML 里都是合法换行，
# 而 `re.MULTILINE` 的 `$` 只认 LF——不归一就会让按行锚定的正则整类失配。
_EOL_RE = re.compile(r"\r\n?")

# 未渲染的模板占位符，**只认「整个标量值就是一个占位符」这一形态**：
#     owner: {{OWNER_ROLE}}        ← 命中（未加引号）
#     updated: "{{NOW_ISO}}"       ← 命中（加了引号）
#     - "{{SOME_PLACEHOLDER}}"     ← 命中（列表项形态；模板里今天零实例，本分支只有构造探针走得到）
#     notes: "……scaffold `{{ROLE_PROFILE_ROUTING}}` 渲染进用户项目……"   ← **不**命中
#
# **为什么不能只扫「文中有没有 `{{`」**：真实项目里实测过一份 `projects/board.yaml`
# 含 4 处 `{{...}}`，全部是引号内**讲述**占位符机制的散文（任务 notes 里在解释 scaffold
# 怎么渲染），一处都不是没渲染的占位符。按「文中有 `{{` 就报」会把它们全判成红，而在
# 文档里讲模板系统是完全正常的事——闸对着一份正确的文件报红，比不装闸更坏。
#
# **为什么仍然排在解析之前**：这条分支要抓的是「渲染漏了一个字段」，它与解析成不成功
# 无关——模板里的占位符现在全部带引号（渲染后语义才对），所以漏渲染的占位符**照样是
# 合法 YAML**，解析这一关根本拦不住它。若把本判定放到 `except` 里，它就只在解析失败时
# 才有机会跑，等于永远抓不到最常见的那一种；而不可达的分支与不存在的分支长得一样。
_PLACEHOLDER_RE = re.compile(
    r'^[ \t]*(?:[\w.-]+[ \t]*:|-)[ \t]*"?(\{\{[A-Za-z0-9_]+\}\})"?[ \t]*(?:#.*)?$',
    re.MULTILINE,
)

# 判定结果的三种 kind（调用方按 kind 分派红灯文案，别只判「有没有问题」——
# 同一断言的不同失败模式措辞必须分开，否则把人引向错误的排查方向）。
OK = "ok"
PLACEHOLDER = "placeholder"
PARSE_ERROR = "parse-error"


def pyyaml_status():
    """PyYAML 可用性。返回 (available: bool, detail: str)。

    import 失败的原因原样带出（`ModuleNotFoundError` 与「装了但坏了」不是一回事，
    后者照抄「pip install pyyaml」的建议解决不了）。
    """
    try:
        import yaml
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    return True, getattr(yaml, "__version__", "unknown")


def _line_col(text, position):
    """把字符偏移换算成 1-based (行, 列)。

    **为 ReaderError 专设**：PyYAML 的 `MarkedYAMLError`（Scanner/Parser/Composer/
    Constructor）带 `problem_mark`，但 **`ReaderError` 没有**——它只有 `.character`
    与 `.position`（字符偏移）。而 ReaderError 正是 C0 控制字符走的那条路，也正是
    最需要报出位置的一条：那个字符肉眼完全不可见，只说「这个文件解析失败」，
    在一个几十份文件的项目里等于没报。
    """
    if not isinstance(position, int) or position < 0:
        return None, None
    head = text[:position]
    return head.count("\n") + 1, position - (head.rfind("\n") + 1) + 1


def _describe(exc, text, line_offset):
    """把 PyYAML 异常转成一句「点得准」的人读描述：类型 + 位置 + 肇事字符 repr。"""
    kind = type(exc).__name__
    line = col = None
    extra = ""
    mark = getattr(exc, "problem_mark", None)
    if mark is not None:
        line, col = mark.line + 1, mark.column + 1
        problem = (getattr(exc, "problem", None) or "").strip()
        if problem:
            extra = f"；解析器原话：{problem}"
    else:
        # ReaderError 分支：自己换算位置，并把不可见字符 repr 出来
        pos = getattr(exc, "position", None)
        line, col = _line_col(text, pos)
        # `ReaderError.character` **两种类型都可能**：走 `check_printable` 那条路时
        # PyYAML 传的是 `ord(character)`（int），走字节解码失败那条路时传的是原字符
        # （str）。只按 str 处理会在**最需要它的那条分支**（C0 控制字符）上抛
        # `TypeError: ord() expected string of length 1`——而调用方的 `except Exception`
        # 会把它降级成「检查自身异常」的 warn，于是这道闸恰好在它唯一要抓的形态上
        # 静默失效。这里两种都收。
        ch = getattr(exc, "character", None)
        code = ch if isinstance(ch, int) else (ord(ch) if isinstance(ch, str) and len(ch) == 1 else None)
        if code is not None:
            extra = f"；肇事字符 {chr(code)!r}（U+{code:04X}，肉眼不可见）"
        reason = (getattr(exc, "reason", None) or "").strip()
        if reason:
            extra += f"；解析器原话：{reason}"
    where = (f"第 {line + line_offset} 行第 {col} 列"
             if line is not None else "位置未知")
    return f"{kind} @ {where}{extra}"


def probe_yaml_text(text, line_offset=0):
    """判定一段 YAML 文本。返回 `(kind, detail)`。

    - `(OK, "")`                 —— 合法 YAML（空串也算合法，`safe_load("")` 返回 None）
    - `(PLACEHOLDER, "{{X}}")`   —— 含未渲染占位符，**先于解析判**（见 `_PLACEHOLDER_RE`）
    - `(PARSE_ERROR, "…")`       —— 解析失败，detail 已含类型 / 行列 / 肇事字符 repr

    `line_offset`：把行号平移到**原文件**的坐标系。markdown frontmatter 段被抽出来时
    丢掉了开头那行 `---`，不平移会让所有行号少 1，指到上一行去。

    **换行形态在本函数入口统一归一（CRLF / 单独 CR → LF），三条调用路径都不必自己管。**
    归一放在这里而不是各调用方，是因为对换行敏感的东西就在本模块里：`_PLACEHOLDER_RE`
    以 `[ \t]*(?:#.*)?$` 收尾，`re.MULTILINE` 下 `$` 落在 LF 之前，**CR 卡在 `}}` 与 `$`
    之间且不被 `[ \t]*` 吃掉** ⇒ CRLF 的行永远匹配不上占位符。放在调用方就成了「三处
    各自记得归一」，实测已经漏过一次：`.yaml` 分支由 `read_text`（通用换行，自动归一）
    改成 `raw.decode()`（不转换）后，CRLF 文件上「占位符残留」分支整类不可达，红灯从
    「漏渲染占位符」变成「不是合法 YAML……裸双引号 / tab 缩进」，**把人送去改完全不同
    的地方**。归一后行数不变（`\r\n`→`\n` 是一对一），故 `_describe` 的行列换算不受影响
    （已实测：同一畸形在 LF 与 CRLF 下报出的行列完全相同）。

    调用方须自行保证 PyYAML 可用（先问 `pyyaml_status()`）——本函数里 import 失败会
    原样抛出，不做静默降级：「没装库」与「文件坏了」必须分成两条红灯，混成一条
    `except Exception` 正是本任务要修的那个缺陷本身。
    """
    # CRLF 与单独 CR 一并归一。`safe_load` 本来就吃得下两种，这一步只为喂饱上面那条正则。
    text = _EOL_RE.sub("\n", text)
    m = _PLACEHOLDER_RE.search(text)
    if m:
        return PLACEHOLDER, m.group(1)
    import yaml
    try:
        yaml.safe_load(text)
    except Exception as e:
        return PARSE_ERROR, _describe(e, text, line_offset)
    return OK, ""


def bom_blinds_frontmatter(raw_bytes):
    """文件是否「剥掉 BOM 后以 `---` 开头，但带 BOM 时 `^---` 匹配不上」。

    这是既有实现的一处**静默失明**，新闸不单独报会与它给出矛盾结论：
    `module_close_check._frontmatter` 用 `encoding="utf-8"`（不是 `utf-8-sig`）读，
    BOM 让 `re.match(r"^---")` 失配 → 返回空串 → 下游 `_scalar_field` 全部读成
    「字段不存在」。而空串是**合法 YAML**（`safe_load("") is None`），所以本闸若只看
    抽出来的那段，会对同一份文件报绿——一边说「YAML 没问题」，一边说「字段全没了」，
    两个结论矛盾且都错。故对这一形态单独报一条 error 并点名 BOM。
    """
    return raw_bytes.startswith(b"\xef\xbb\xbf") and raw_bytes[3:].startswith(b"---")
