#!/usr/bin/env python3
"""语义覆盖判定式：「退役前那批源的每一段都有落点声明」的**唯一**实现。

为什么单独成模块（形态同 `_yaml_probe.py`）：会有**不止一个**消费方问同一个问题
（「这份段全集里，哪些段没有落点声明」），而它们喂进来的是**互不相交**的三元组
——各自的源目录、各自的落点文件、各自的台账。文件集不相交、判定式只此一份，
所以这不是双事实源；而判定式若各写一份，宽严必漂（同一个断言的两个实现只能比对方
更宽或更窄，两个方向都会造出洞）。

**本模块不认识任何具体项目，也不认识调用它的是谁**：三个输入全部由调用方给，路径
解析由调用方注入的 `resolve` 回调做。它不 import 调用方、不读环境变量、不猜目录。
这既是可复用的前提，也是**引用方向**的要求——被分发的资产不得反向引用调用它的仓。

**本模块证明什么、不证明什么**（写红灯文案与文档时照此措辞，别放宽）：
  证明：段全集里的每一段，**有没有**一条落点声明；台账快照与现场切段**是否逐段一致**。
  不证明：落点里的文字**是否忠实承接了**那一段。后者没有机器判据，靠人工双检。
          `elsewhere` 同理只是一条声明——落点文件此刻存不存在都不影响判定，
          存不存在只由 `pending_lands` 如实报出来，交调用方决定怎么说。

**射程（能力边界，删掉这段等于把一个已知缺口变成暗坑）**：切段只认**代码围栏之外的
ATX 标题 `##`–`######`**。两类不在射程：
  - **H1**（各文件标题）——有意排除，理由见 `HEADING_RE` 旁注；
  - **setext 形式的标题**（正文行 + 下一行 `===` / `---`）——源里一旦出现，那一段对本
    模块**根本不存在**，它整块消失也不会报。
这条必须写出来，因为本模块产出的红灯文案（「第 N 段无人认领」「原本有落点、现在没有
了」）把自己描述成一个**段级完整性判定**，读者据此会推出「一段没了它会说」——射程之外
不会。

被 CLI 脚本与校验器以同目录 / sys.path import 方式使用，因此：
  - 模块 import 必须无副作用（不碰 stdout、不读环境、不建目录、不 import 兄弟模块）
  - 文件名用下划线而非连字符——连字符的模块名 import 不进来
"""

import re
import sys
from pathlib import Path


def _fence_walker():
    """围栏状态机——**import `project_scaffold` 复用，本模块不自留副本**。

    围栏内的 `## ` 是示例文本不是章节；两处各写一份状态机必漂。那边的实现是本仓内
    唯一经双向反证的一份（两个反面版本在无注入基线上实测都坏），复用它而不是照抄。

    惰性 import（保持本模块 import 时无副作用）；同目录不在 `sys.path` 时自己补上，
    这样调用方只需把本目录挂进 `sys.path` 就够，不必额外知道还有个兄弟依赖。
    """
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import project_scaffold
    return project_scaffold._outside_fence


def frontmatter_block(text):
    """开头 frontmatter 的原始文本（不含两条 `---` 行）；没有返回 None。

    **不复用通用的 frontmatter 字段检查**：那类实现通常只判字段在不在、取不出列表，
    且用 `text.split("---", 2)` 定位——正文里任何一处 `---`（水平线、表格分隔、乃至
    `a---b` 中间那段）都会被当成结束标记。本函数按**整行相等**找结束行，形态更窄。
    """
    # 去 BOM：带 BOM 的文件首行是 `﻿---`，不等于 `---`，本函数会返回 None，
    # 于是覆盖断言报「没有 frontmatter」——**红对了但把人指向错的地方**（真因是 BOM）。
    text = text.lstrip("﻿")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i])
    return None


# 双引号标量，内层 `"` 必须转义成 `\"`（YAML 本来就这么要求）。段标题里真的有带
# ASCII 双引号的一条，裸写会造出一个**不合法的 YAML**，而它同时还能被一条贪心正则
# 读对——两边都"成功"、取值却由谁先解析决定，正是重复键那一族的形状。
SUPERSEDES_ITEM_RE = re.compile(r'^  - "((?:[^"\\]|\\.)*)"$')


def declared_supersedes(label, text):
    """取 frontmatter 里 `supersedes:` 的条目；返回 `(条目列表, 形态问题列表)`。

    **只认 `  - "<段键>"` 一种形态，别的形态报红而不是静默跳过**：一条写错缩进或漏了
    引号的声明被静默跳过之后，与「压根没声明」在终端里同形，而两者的处置相反——前者
    要修声明，后者要判这段该不该搬。

    `label` 只进文案（调用方拿什么当标识就传什么，通常是落点文件的相对路径）。
    """
    fm = frontmatter_block(text)
    if fm is None:
        return [], [f"{label}: 没有 frontmatter——覆盖声明无处可放，它承接的段会被判成无人认领"]
    items, bad, inside = [], [], False
    for ln in fm.split("\n"):
        if not ln.strip():
            continue
        if re.match(r"^\S", ln):
            inside = ln.startswith("supersedes:")
            if inside and ln.strip() != "supersedes:":
                bad.append(f"{label}: `supersedes:` 只支持块列表形态，收到 `{ln.strip()[:40]}`")
            continue
        if not inside:
            continue
        hit = SUPERSEDES_ITEM_RE.match(ln)
        if hit:
            items.append(re.sub(r"\\(.)", r"\1", hit.group(1)))   # `\"` → `"`、`\\` → `\`
        else:
            bad.append(f"{label}: `supersedes` 条目形态不合规 `{ln.strip()[:40]}`——"
                       f'须写成 `  - "<文件名>#<段标题>"`（两空格缩进 + 双引号）')
    return items, bad


# **切到 H6，不是「刚好覆盖今天的内容」**：本模块最初的那个调用点上，源当时最深只到
# `####`，写 `#{2,4}` 与写 `#{2,6}` 同为 58 段；但前者留一条 latent 回归——将来任一份源
# 写出 `#####`，它会再一次静默消失，而那时没人记得这里曾调过一次粒度。
# **H1 排除是有意的**：那些是各文件的标题（`# 收口纪律` 之类），随头注一起退役，
# 不是内容单元；把它们算进段全集会造出一批永远无人认领的段。
# **这条粒度是跨调用点的同一事实**：各调用点的台账快照都按它冻结，任一侧另调一个粒度，
# 两边的段全集会在同一份源上给出不同的数——所以它长在这里，调用方不许自带正则。
HEADING_RE = re.compile(r"^#{2,6} (.+?)\s*$")


def live_sections(source_dir):
    """现场把 `source_dir` 下的 `*.md` 按 `##`–`######` 切成 `<文件名>#<段标题>` 清单。

    源目录不存在时返回 `None`：那时段全集的唯一载体是台账里的快照。**`None` 与 `[]`
    必须分开**——前者是「源已退役、以快照为准」，后者是「源还在但一段都切不出来」，
    后者是真问题（多半是标题形态变了），两者的处置相反。

    **不复用同目录里那个「恰好一个章节」的切章函数**：它的契约是命中 0 返 `None`、
    ≥2 抛异常，且标题判定要求整行相等且带反引号；这里要的是「枚举全部章节」，而这类
    源的标题形态（`## <数字>. <标题>` / `### 第 N 步：…`）不带反引号，它一个都命中不了。
    """
    if not source_dir.is_dir():
        return None
    outside_fence = _fence_walker()
    out = []
    for p in sorted(source_dir.glob("*.md")):
        text = p.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        for _i, ln in outside_fence(text.split("\n")):
            hit = HEADING_RE.match(ln)
            if hit:
                out.append(f"{p.name}#{hit.group(1)}")
    return out


def multiset_diff(left, right):
    """`left` 比 `right` 多出来的条目（含重复次数）。"""
    pool = {}
    for x in right:
        pool[x] = pool.get(x, 0) + 1
    extra = []
    for x in left:
        if pool.get(x, 0) > 0:
            pool[x] -= 1
        else:
            extra.append(x)
    return extra


def evaluate(source_dir, land_texts, ledger, resolve=None):
    """判定一次语义覆盖。**纯函数**：除了读 `source_dir` 下的 `*.md`，不碰别的。

    参数
      `source_dir`   —— 退役前那批源所在目录（`Path`）。不存在即走「快照为准」。
      `land_texts`   —— 落点文件清单，`[(标识, 全文文本), …]`。**由调用方决定谁算落点**，
                        本模块不去猜目录：不同消费方的落点面本就不同，猜一次就把某个
                        消费方的扫描面写死在这里了。
      `ledger`       —— 台账 dict，键：`legacy_source_dir` / `legacy_sections` /
                        `elsewhere`（每条含 `section` 与 `lands_in`）/ `retired`（含 `section`）。
      `resolve`      —— 可选回调 `str -> Path`，把 `lands_in` 解析成可判存在性的路径。
                        **不给就不做这项检查**，此时 `pending_lands` 返回 `None` 而不是
                        `[]`：「没查」与「查过、都在」是两件事，混成一个空列表之后，
                        调用方会替一个根本没跑的检查报绿。

    返回一个 dict：
      `problems`      —— 人读的问题清单，**顺序稳定**（形态问题 → 现场缺段 → 现场新增
                         未认领 → 快照段无人认领 → 声明指向不存在的段）。空 = 无问题。
      `problem_kinds` —— 与 `problems` **等长、下标对齐**的机器可读种类：`form` /
                         `gone` / `added` / `unclaimed` / `bogus`。**给调用方分派处置用**
                         ——`unclaimed`（快照里那一段还没有任何落点声明）在某些消费方那里
                         是**进度读数**而不是失效，别的四种一律是失效；调用方要区分时
                         **读这个字段，不许拿 `problems` 的文案去正则匹配**（那等于把
                         红灯文案变成机器契约，改一个字就悄悄改变分派）。
      `unclaimed_sections` —— 快照里**没有任何落点声明**的那些段（结构化段名，顺序同
                         `frozen`）。给调用方**自己渲染**用：`unclaimed` 那条 problem 文案
                         按「原本有落点、现在没有了」写，**那只对「台账冻结时段段都有声明」
                         的消费方成立**；对「台账在承接开始之前就冻结」的消费方，同一件事
                         是「还没做」而不是「掉出来了」，照抄那句会把人送去 git 历史里找一个
                         从来不存在的声明。⇒ 措辞归调用方，段名从这里拿，**不要去正则抠文案**。
      `frozen`        —— 台账快照的段全集（原样，含重复）。
      `live`          —— 现场切段结果，或 `None`（源目录不存在）。
      `universe`      —— 判定用的段全集 = 快照 ＋ 现场新增。
      `in_lands`      —— 落点文件里声明承接的段（**集合，已去重**）。
      `elsewhere`     —— 台账 `elsewhere` 声明的段（集合）。
      `retired`       —— 台账 `retired` 声明的段（集合）。
      `split`         —— 同时出现在 `in_lands` 与 `elsewhere` 的段数（一段被拆成两半，
                         两个落点都登记了）。
      `snapshot_note` —— 快照与现场关系的一句话（调用方可原样并进 OK 文案）。
      `pending_lands` —— `elsewhere` 里落点尚未落地的 `lands_in` 值（含重复），或 `None`。

    **三个集合可以相交**：一段被拆成判据 / 工序两半时，两个落点都要登记。所以上面的
    计数各报各的**实际大小**，调用方不要用「总数减两个」倒推——倒推在有交集时会把
    `in_lands` 那格少报，而那正是被拆的段最多的一类。
    """
    frozen = list(ledger["legacy_sections"])
    live = live_sections(source_dir)

    problems, kinds = [], []

    def report(kind, text):
        """记一条问题 ＋ 它的机器可读种类。**两个列表严格等长、下标对齐。**

        `kinds` 是给调用方**分派处置**用的：不同种类的问题后果完全不同（`unclaimed`
        是进度读数，`gone` / `added` / `bogus` / `form` 是真失效），而调用方**不许拿
        `problems` 的文案去正则匹配来区分**——那是把红灯文案变成机器契约，改一个字
        就悄悄改变分派。
        """
        problems.append(text)
        kinds.append(kind)

    in_lands, form_bad = set(), []
    for label, text in land_texts:
        items, bad = declared_supersedes(label, text)
        in_lands |= set(items)
        form_bad += bad
    for text in form_bad:
        report("form", text)
    elsewhere = {e["section"] for e in ledger.get("elsewhere", [])}
    retired = {e["section"] for e in ledger.get("retired", [])}
    declared = in_lands | elsewhere | retired

    if live is None:
        snap_note = f"源目录 `{ledger['legacy_source_dir']}` 已退役，段全集以台账快照为准"
        universe = frozen
    else:
        snap_note = f"台账快照与现场切段逐段一致（{len(live)} 段）"
        added = multiset_diff(live, frozen)
        gone = multiset_diff(frozen, live)
        for s in gone:
            report(
                "gone",
                f"快照有、现场无：`{s}`——源里这一段被删了或改了标题，"
                f"针对它的落点声明会变成指向一个不存在的段；先确认是改名还是真删，两者处置不同")
        universe = frozen + added
        for s in added:
            if s in declared:
                continue
            report(
                "added",
                f"**新增方向**：`{s}` 无人认领——它是新写进旧 rules 的，还是被漏搬的？"
                f"两种处理相反：新写的要判该不该进注入片，漏搬的要补落点。先确认再动手")
        if added or gone:
            snap_note = f"快照 {len(frozen)} 段 / 现场 {len(live)} 段，有差异（见上）"

    unclaimed_sections = []
    for s in frozen:
        if s not in declared:
            unclaimed_sections.append(s)
            report(
                "unclaimed",
                f"**回归方向**：`{s}` 原本有落点、现在没有了——查是不是拆分时删了段却没同步"
                f"删声明，或声明被误删。它不是新问题，是掉出来的")
    bogus = sorted(d for d in declared if d not in set(universe))
    for s in bogus:
        report(
            "bogus",
            f"声明指向一个不存在的段：`{s}`——多半是段标题写错或复制串行了；"
            f"这条声明不承接任何东西，而它盖住的那一段会显示为「已覆盖」")

    pending = None
    if resolve is not None:
        pending = [e["lands_in"] for e in ledger.get("elsewhere", [])
                   if not resolve(e["lands_in"]).exists()]

    return {
        "problems": problems,
        "problem_kinds": kinds,
        "unclaimed_sections": unclaimed_sections,
        "frozen": frozen,
        "live": live,
        "universe": universe,
        "in_lands": in_lands,
        "elsewhere": elsewhere,
        "retired": retired,
        "split": len(in_lands & elsewhere),
        "snapshot_note": snap_note,
        "pending_lands": pending,
    }
