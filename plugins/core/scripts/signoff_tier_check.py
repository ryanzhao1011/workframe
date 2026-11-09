#!/usr/bin/env python3
"""签发否决项判定 — 吃一段钉死两端的 diff，吐「否决项命中没有、要不要交用户拍板」。

签发分两类（必载片 §谁签发这次收口）：**主会话自理**与**需要独立验证**，类别由模型按
那一节的三条判据判，**本脚本不判类别**。它只算两类共用、且机器算得出来的那一半——
**否决项**：命中任一条（或产出了待裁决项）即交用户拍板，不论签发类别。算不出来的那几条
**显式标成「待裁决」或「恒有的模型裁量项」，而不是「未命中」**——静默未命中就是假绿，
而假绿与真绿在终端里长得一样。

用法：
    python signoff_tier_check.py [--project-dir DIR] --since REF[,LABEL=REF...]
                                 [--diff-ref REF[,LABEL=REF...]] [--size-cap N] [--json]

切片按仓给两端：单个 hash 指项目根那个仓；多仓写 `LABEL=hash`，LABEL 是仓相对项目根的
路径（根仓写 `.`），逗号分隔，例 `--since .=c302f74b,claude-workframe=ed230267`。
`--diff-ref` 缺省为各仓当前 HEAD。两端都只收**十六进制 commit**——符号引用（`HEAD`、
分支名）是可变的，钉不住「签发时判的是哪一段」。

退出码：
    0  判定已产出（可能带「待裁决」标注）
    1  拒绝判定——切片声明不合法、仓不存在或 hash 解析不到
    2  用法错误（argparse）

**本脚本自己就是「判断别的东西对不对」的东西**：改它属于「需要独立验证」，并且要先跑基线、
用它自己的失败方式打一遍、每条红灯分支各配一个真的走到它的探针。

## 能力边界（读输出前先读这段）

1. **签发类别（自理 / 独立验证）是模型判的**，本脚本与看板写入 hook 都不判：把一处该独立
   验证的改动判成自理，机器一无所知。用户 2026-11-01 拍板不加按路径的机器下限。
2. **六条否决项里只有部分是机器可判的**：V1 内容级可判、V2 只判得了版本号字段（推 main /
   打 tag / 对外 CHANGELOG 已发布段不在 diff 里）、V6 只判路径面（契约变更 vs 表述变更
   由模型判）、V3/V4/V5 纯模型。凡机器判不了的一律标「待裁决」或列进恒有裁量项。
3. **切片只含已提交的改动**：每个声明的仓取 `since..diff_ref`，未提交改动不在内——签发前先
   提交。**没声明的仓不在切片里**：改了两个仓却只给一个仓的两端，另一个仓的改动对本判定
   不可见，且不产生任何提示（声明由签发方写，漏写没有机器面）。
4. **尺寸上限过滤的是「大而浅」，漏的是「小而不可逆」**：行数与风险量纲正交，改 31 份
   文档里同一个术语只有 62 行却是 31 个表现层。这一格没有判据。
5. **不判「机器闸全绿」**：本脚本不跑 validate / doctor / close-check，别把它的绿读成那三道的绿。
6. **尺寸上限可被调用方自己抬掉**：`--size-cap` 无下限、放大后**不产生任何缺口或提示**，
   于是这条唯一的纯机器否决项生效与否取决于被约束者本人（看板写入 hook 不读这个参数，
   恒用默认值）。默认值是 2026-09-09 用户拍板的产品决策，用户同日就这条判「先不管」——
   **这是被知情接受的风险，不是被覆盖掉的风险**。
7. **共享对象的仓会让切片跑出跨树 diff**：`git clone --shared` / worktree / 配了
   `alternates` 的仓里，属于**外仓**的 commit 也解析得到，据此算出的 diff 会凭空造出
   该仓从未有过的改动（实测：一条整文件删除，并因此封顶到用户拍板），**且不产生任何提示**
   ——「解析得到」被当成「起点属于本仓」。两个独立仓、无 alternates 的拓扑不触发；
   vendored clone 与 worktree 会。
8. **diff 解析器认不出内容本身以 `-- ` / `++ ` 开头的行**：它们在 unified diff 里长成
   `--- xxx` / `+++ xxx`，与文件头同形，于是被当成文件头。两种后果：`-- ` 那条删除行被
   **整行吞掉（deletions 少算，方向是漏而不是多，正好是尺寸上限该看住的方向）**；
   `++ ` 那条新增行造出一个**幻影文件**，并且**其后的加减行都改挂在幻影名下**，真实文件
   那侧同时少算。SQL 注释（`-- comment`）是最常见的形态。真实资产命中 0 处，故只登记；
   修法是给 `---` / `+++` 的文件头判定加「必须紧跟 `diff --git` 之后」的状态约束。
   `tools/test_signoff_tier_check.py` 有一条**现状用例**钉住它，修好之后那条会报红——
   那是提醒去更新它，不是回归。
9. **V6 路径命中只进「待裁决」，不直接封顶**——这是**判定的中间态，不是终局**。必载段
   （§否决项 6 的清单与判法）已把方向钉死：路径命中后由模型判契约变更还是表述变更，
   **拿不准一律算契约变更**（⇒ 交用户拍板）。所以本脚本报「未命中」而 `pending` 里挂着
   V6 那条时，**那不是「可以签」，是「等人裁」**。会静默失效的形态：有人只读
   `needs_user` 不读 `pending`。看板写入 hook 两个都读：有待裁决项时，签注要么写
   「你拍板」，要么在 `ruling` 里逐条写出裁决。
10. **V1 在同一个 hunk 里把同键的新行当成原地改写**（`entry_key`）：同一处连续改动里删掉
   一条带键条目、又新增一条同键而内容无关的条目，判成改写、不命中。隔开的两处改动不配对；
   逐字匹配与同键配对都按个数一对一，净少掉的条目仍命中。被抵掉的逐条进 `v1_rewrites`
   并印在「按同键改写放行」那段里，**只提示、不封顶**，是不是同一条目由签发方核。
   不进 `pending` 是 2026-11-03 用户拍板（备选是进待裁决、签注逐条写 `ruling`）：
   **这是被知情接受的风险**，`tools/test_signoff_tier_check.py` 有一条锁定用例钉住它。
   反方向的假红：标签本身被改写（键变了）、改写无键的行，仍判删除。
"""

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

# 用 `reconfigure` 而不是新建 `TextIOWrapper`：后者在「本脚本被另一个脚本 import」时
# 会有两层包装抢同一个 buffer，先被回收的那层关掉 buffer，之后任何 print 抛
# `ValueError: I/O operation on closed file`。本脚本注定要被 import（board 写入 hook 与
# 它的单测），原地改编码没有这个坑。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from _harness import project_dir as _project_dir  # noqa: E402
from _state_io import runtime_rel  # noqa: E402

GIT_TIMEOUT_SEC = 30

# 切片两端只收十六进制 commit。符号引用（`HEAD`、分支名、tag）是可变的：签注里写
# `since: HEAD`，下一次提交之后同一条签注就指向另一段 diff，而它在写入那一刻恰好让
# 已提交的工作对否决项整段不可见（2026-11-01 预审实测）。
HEX_REF_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")

# 尺寸上限：`additions + deletions`，**不是净变化**——净变化正是删除逃逸的那个量。
# 2000 是 2026-09-09 用户拍板的值，理由：框架仓的改动基线天然偏大（单个脚本就可能
# 七八百行），上限定在 1000 会让多数批次条条撞线、全部弹到用户拍板，分级当场失去
# 区分度。2000 让真正的大改动仍被拦住。**这个数字只在本行存在，别往别处抄第二份。**
DEFAULT_SIZE_CAP = 2000

# ---------- 否决项 6 的路径面 ----------
# **两张表，各管各的，不得互相代读**（必载片 §否决项 6 的清单与判法）：
#   VETO6_*       —— 管「签发档位何时封顶到用户」。入选判据两条同时满足：
#                    (a) 人手改的（机器生成或同步的派生物不算）
#                    (b) 改错了会静默扩散或难回收
#   AUTO_UPDATE_* —— 管「auto-update 规则自己不许直接写哪些文件」。**VETO6 是它的真子集**
#
# 第二张表**保留不删**：差集里的路径（`.claude/skills/**` / `.agents/skills/**` / `projects/proposals/**` /
# `README.md` / `LICENSE` / 运行态目录）**仍受 auto-update 约束**，只是不再自动封顶。
# 删掉它等于把「仍受那张表约束」这个事实从机器面抹掉，读输出的人无从知道自己动了
# auto-update 的受保护资产——`evaluate_vetoes` 因此把差集命中报成提示而不是丢掉。
#
# 形态只用三种（精确 / 目录前缀 / 目录内 basename 通配），因此不需要 glob 引擎——
# 少一套 `**` 语义的实现就少一处会与 `glob_to_regex` 漂开的地方。
VETO6_EXACT = (
    "CLAUDE.md",
    "AGENTS.md",
    ".workframe-config.json",
)
VETO6_PREFIX = (
    ".claude/agents/",
)
# 必载纪律由 hook 在启动时注入，项目侧不落文件，所以本表里承载它的那一格是 `AGENTS.md`
#（上面 `VETO6_EXACT`）：每会话必载、两扇门都读、人手改，(a)(b) 两条判据都命中。
# 注入源 `context/**` **不进**本表——它是框架仓资产、随插件分发而不由项目手改，不满足 (a)。
# (目录, basename 前缀, basename 后缀)：`.claude/settings*.json`
VETO6_BASENAME = ((".claude/", "settings", ".json"),)

AUTO_UPDATE_EXACT = VETO6_EXACT + (
    "README.md",
    "LICENSE",
)
AUTO_UPDATE_PREFIX = (
    ".claude/agents/",
    # 项目 skills 两个前缀都要：CC 里经链接 Edit 时 `file_path` 是 `.claude/skills/…`，而链接形态下 git 报的是
    # 真实源 `.agents/skills/…`（真目录形态的已装项目只有前者）。两个都**不进 VETO6**
    ".claude/skills/",
    ".agents/skills/",
    "projects/proposals/",
    # 另一扇门的项目内配置面。门没落地时匹配不到任何东西、纯 no-op；门落地后内部
    # 怎么排都被整目录前缀覆盖住——方向 fail-safe，所以现在就列。
    # **不进 VETO6**：它不满足「改错了会静默扩散或难回收」那半，且用户拍板把
    # 用户级 trust 段那一格留到门真落地时再判形态。
    ".codex/",
)
AUTO_UPDATE_BASENAME = VETO6_BASENAME

# 会分发给所有项目的框架源。**不参与封顶**——否决项 6 的清单（上面 `VETO6_*`）里没有
# 框架源这一类，用户拍板缩窄清单时明确排除了它。列在这里只为把「本次动了会分发的
# 资产」这个事实说出来：这类改动由主对话实现、交新上下文验证；「谁实现」那条轴不是本脚本的判定对象。
# **别把这段读回成「清单 = auto-update 原表」**——那是缩窄之前的口径，与上面
# 「VETO6 是 AUTO_UPDATE 的真子集」直接矛盾。这两处在同一个文件里分叉过一次，
# 成因是改注释时只改了被搜到的那半。
FRAMEWORK_ASSET_PREFIX = (
    "plugins/",
    ".claude-plugin/",
    "tools/",
)

# ---------- 否决项 2 的机器面 ----------
VERSION_BEARING_BASENAMES = ("plugin.json", "marketplace.json", "package.json")
RELEASE_NOTE_BASENAMES = ("CHANGELOG.md",)


# ---------- git 薄封装（纯读，不写事件） ----------
# 不复用 csm 的 `_git_lines`：那个的失败路径会 append `modules_check_stale_error`，
# 而本命令是判定用的只读入口，往运行态里写噪声事件不合适（`module_close_check` 同款取舍：
# 它同样不写事件，写入只有它自己的计数日志及其锁文件）。
# 本脚本不走 `_git_scan` 的扫描面：切片由签注按仓声明两端，不需要发现嵌套仓。


def _git(repo_abs, args):
    """跑一条 git 命令，返回 (rc, stdout, stderr)；进程级异常 rc=-1。

    `-c core.quotepath=off`：git 默认把输出路径里的非 ASCII 字节转成八进制转义并给整行
    加引号，modules 体系明确允许中文路径，转义后的路径与任何清单都对不上。
    `-c` 必须在子命令之前——写在后面会被当成子命令自己的短选项静默吃掉。
    """
    cmd = ["git", "-C", str(repo_abs), "-c", "core.quotepath=off"] + list(args)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=GIT_TIMEOUT_SEC)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", f"{type(e).__name__}: {e}"


def git_ok(repo_abs, args):
    return _git(repo_abs, args)[0] == 0


# ---------- diff 解析 ----------


def _unquote(path):
    """剥掉 git 给特殊字符路径加的引号与 `a/` `b/` 前缀。"""
    p = path.strip()
    if len(p) >= 2 and p.startswith('"') and p.endswith('"'):
        try:
            p = json.loads(p)
        except Exception:
            p = p[1:-1]
    if p.startswith("a/") or p.startswith("b/"):
        p = p[2:]
    return p


class FileDelta:
    """一个文件在本次 delta 里的加减行。"""

    def __init__(self, path):
        self.path = path
        self.added = []
        self.removed = []
        # 与 added / removed 逐项对齐的 hunk 序号：V1 只在同一个 hunk 里认「同键改写」
        self.added_hunk = []
        self.removed_hunk = []
        self.hunk = 0
        self.binary = False
        self.deleted_whole = False

    @property
    def additions(self):
        return len(self.added)

    @property
    def deletions(self):
        return len(self.removed)


_BINARY_RE = re.compile(r"^Binary files (?:a/)?(.*?) and (?:b/)?(.*?) differ$")


def parse_unified_diff(text, into=None):
    """解析 `git diff --unified=0` 的输出为 {path: FileDelta}。

    只认**内容行**：`--- ` / `+++ ` 是文件头，`@@` 是 hunk 头，`\\ No newline` 是元信息，
    三者都不是加减行。不排除它们的话每个文件都会凭空多出一加一减——尺寸上限与删除识别
    会同时被这两行污染，而污染量恰好随文件数线性增长，看着像「改动确实变大了」。

    **整文件删除的路径只能取旧侧**：它的 `+++` 是 `/dev/null`。早先按新侧取路径，于是
    删掉的那个文件根本不进 `out`，它的每一条减行都被丢掉——删一个 500 行的文件，
    deletions 算出 0，尺寸上限当场被整份绕过。删除正是这道闸最该看住的方向。

    二进制文件既没有 `---` 也没有 `+++`，只有一行 `Binary files a/X and b/X differ`，
    路径必须从那一行取。不取的话它连出现在文件清单里都做不到。
    """
    out = {} if into is None else into
    cur = None
    old_path = None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            cur = None
            old_path = None
            continue
        if line.startswith("--- "):
            raw = line[4:].strip()
            old_path = None if raw == "/dev/null" else _unquote(raw)
            continue
        if line.startswith("+++ "):
            raw = line[4:].strip()
            whole_delete = raw == "/dev/null"
            path = old_path if whole_delete else _unquote(raw)
            if not path:
                cur = None
                continue
            cur = out.setdefault(path, FileDelta(path))
            if whole_delete:
                cur.deleted_whole = True
            continue
        m = _BINARY_RE.match(line)
        if m:
            path = _unquote(m.group(2)) or _unquote(m.group(1))
            if path and path != "/dev/null":
                cur = out.setdefault(path, FileDelta(path))
                cur.binary = True
            continue
        if line.startswith("GIT binary patch"):
            if cur is not None:
                cur.binary = True
            continue
        if cur is None or line.startswith("\\"):
            continue
        if line.startswith("@@"):
            cur.hunk += 1
            continue
        if line.startswith("+"):
            cur.added.append(line[1:])
            cur.added_hunk.append(cur.hunk)
        elif line.startswith("-"):
            cur.removed.append(line[1:])
            cur.removed_hunk.append(cur.hunk)
    return out


# ---------- 结构化条目识别（否决项 1） ----------


_ORDERED_ITEM_RE = re.compile(r"^\d+[.)]\s")


def structured_kind(content):
    """这一行是不是「结构化条目」？是则返回类别名，否则 None。

    否决项 1 的原文是**列表项** / 表格行 / YAML 序列项——「列表项」是语义词，
    **有序列表也是列表项**。早先按三个字面前缀（`- ` / `* ` / `+ `）枚举，把语义词
    收窄成了字面表，于是 `1. foo` 判 None：删掉编号步骤中的一步不命中 V1。这个形态
    在真实资产里是主力（必载片 §改动闭环那 5 条编号步骤、
    `auto-update` §执行规范 8 条，都是它）。**「别删，改 supersede」是一条无条件的硬边界**
    ——对一切删除动作生效，不限文件、目录、条目、规则、清单项；V1 正是它的机器面。

    无序那三种与 YAML 序列项在字面上同形，不必也不该分开——分开需要知道文件是 md
    还是 yaml，而 diff 里同一个 `- foo` 在两种文件里要防的是同一件事。有序那一支
    单独命名，是为了让红灯文案说得出「删掉的是第几步」这类线索。

    **不认的形态**（判 None，回套实测）：`1.5 不是列表`（数字后不是 `.`/`)` 加空白）、
    `1、中文顿号`、`a. 字母序号`、`(1) 括号序号`、`①圈码`、裸 `-` / `*` / `+`。
    `2026. 年份开头` **认**——markdown 里它就是一个有序列表项，宁可过判。
    """
    s = content.strip()
    if not s:
        return None
    if s.startswith("|") and s.rstrip().endswith("|") and s.count("|") >= 2:
        return "表格行"
    if s.startswith(("- ", "* ", "+ ")):
        return "列表项/YAML 序列项"
    if _ORDERED_ITEM_RE.match(s):
        return "有序列表项"
    return None


def normalize_entry(content):
    """归一化：去首尾空白 + 内部连续空白折叠成一个空格。

    「有没有对应的 `+` 行」先按归一化后**逐字相等**判，判不上再按 `entry_key` 的键判。
    判据故意不做模糊匹配——相似度阈值是拍脑袋的数，而这条判定的下游是「要不要惊动用户」。
    """
    return " ".join(content.split())


# 无序列表项正文开头的标签：`**加粗**` 或 `` `代码段` ``
_ENTRY_LABEL_RE = re.compile(r"^(?:\*\*(.+?)\*\*|`([^`]+)`)")
# 表格首格是纯序号（`3` / `#`）时它是位置不是身份
_ORDINAL_CELL_RE = re.compile(r"^(?:#|\d+)$")


def entry_key(content):
    """条目的身份：`(类别, 键)`；拿不出键返回 None。

    V1 防的是**条目消失**，不是**行变了**（删除判据：条目还在不在）。只比行，原地改写
    一个表格行或一条带标签的列表项就被判成删除——模块文档每次同步都要改写 code-map
    的行与 overview 的「最近同步」行，于是凡同步过模块文档的签发 V1 恒命中，红灯失去
    区分度。键让「同一个条目换了内容」与「条目没了」分得开，且是逐字相等、没有阈值。

    - 表格行：第一格（code-map 是文件路径）；分隔行（只含 `-` `:` 空格）、空首格与
      纯序号首格（`1` / `#`）无键
    - 无序列表项 / YAML 序列项：正文开头的 `**标签**` 或 `` `代码段` ``；没有标签的无键
    - **有序列表项无键**：删掉一步后重新编号，编号就不再指原来那一条——纯序号的表格
      首格同理，编号是位置不是身份

    无键的行照旧只按逐字相等判，即改写它仍判删除（假红，方向 fail-safe）。键只在**同一个
    hunk** 里配对（见 `evaluate_vetoes`）：同键不等于同一条目，原地改写才在同一处。
    """
    kind = structured_kind(content)
    if kind is None or kind == "有序列表项":
        return None
    s = content.strip()
    if kind == "表格行":
        first = s.strip("|").split("|", 1)[0].strip()
        if not first or set(first) <= set("-: ") or _ORDINAL_CELL_RE.match(first):
            return None
        return (kind, first)
    m = _ENTRY_LABEL_RE.match(s[2:].lstrip())
    if not m:
        return None
    return (kind, (m.group(1) or m.group(2)).strip())


# ---------- 受保护资产路径判定 ----------


def runtime_protected(project_dir):
    """两张表里的运行态路径在**本项目**的实际取值，问 `_state_io` 现算。

    位置**不写死**：目录位置只有 `_state_io` 一个事实源，这里再写一份就是第二个源，漂了之后
    否决项 6 静默失明——失明是假绿。只看新位置：旧布局目录框架不读写（迁移工具搬），没迁移的
    项目里旧位置的 `shared/MEMORY.md` 不在封顶集内（用户 2026-09-19 拍板，不额外纳入保护）。

    返回 `{"veto6": (exact, prefixes), "auto_update": (exact, prefixes)}`。
    两者的差：`shared/MEMORY.md`（应用层最高权威 ＋ 永不清理）**两张表都在**；
    运行态目录整棵**只在 auto-update 那张**——hooks 与 system skill 写它，不满足「人手改的」。

    判定对象是本项目根下的目录形态：嵌套仓里同名的路径也会被匹上，那是过判
    （fail-safe 方向），不是漏判。
    """
    state = runtime_rel(project_dir, "state").rstrip("/")
    memory = runtime_rel(project_dir, "memory").rstrip("/")
    shared = (memory + "/shared/MEMORY.md",)
    return {"veto6": (shared, ()),
            "auto_update": (shared, (state + "/",))}


def _match_table(p, exact, prefixes, basenames, runtime):
    extra_exact, extra_prefix = runtime
    if p in exact or p in extra_exact:
        return True
    if any(p.startswith(pre) for pre in tuple(prefixes) + tuple(extra_prefix)):
        return True
    for d, pre, suf in basenames:
        if p.startswith(d):
            base = p[len(d):]
            if "/" not in base and base.startswith(pre) and base.endswith(suf):
                return True
    return False


def is_veto6_path(rel, runtime=((), ())):
    """封顶用的那张表：命中即进「待裁决」，由模型判契约还是表述，**拿不准一律算契约变更**。"""
    p = rel.replace("\\", "/")
    return _match_table(p, VETO6_EXACT, VETO6_PREFIX, VETO6_BASENAME, runtime)


def is_auto_update_protected(rel, runtime=((), ())):
    """auto-update 那张表：**不参与封顶**，只报出来——差集里的路径仍受那条规则约束。"""
    return _match_table(rel.replace("\\", "/"), AUTO_UPDATE_EXACT,
                        AUTO_UPDATE_PREFIX, AUTO_UPDATE_BASENAME, runtime)


def is_framework_asset(rel):
    p = rel.replace("\\", "/")
    return any(p.startswith(pre) for pre in FRAMEWORK_ASSET_PREFIX)


# ---------- 切片：按仓钉死两端 ----------


ROOT_LABEL = "."


def parse_refs(raw, field):
    """把签注 / 命令行里的一端解析成 `{仓标签: hash}`。返回 (dict, errors)。

    两种形态：单个字符串 = 项目根那个仓；映射 = `{仓相对项目根的路径: hash}`（根仓写 `.`）。
    命令行的 `LABEL=hash,LABEL=hash` 在 `main()` 里先转成映射再进这里，两条入口共用一份校验。
    """
    if isinstance(raw, str):
        raw = {ROOT_LABEL: raw}
    if not isinstance(raw, dict) or not raw:
        return {}, [f"`{field}` 必须是一个 commit hash，或 `{{仓路径: hash}}` 映射（根仓写 `.`），"
                    f"实为 {raw!r}"]
    out, errs = {}, []
    for label, ref in raw.items():
        lab = str(label).replace("\\", "/").strip().rstrip("/") or ROOT_LABEL
        val = str(ref).strip() if ref is not None else ""
        if not HEX_REF_RE.match(val):
            errs.append(f"`{field}` 里仓 `{lab}` 的值 {ref!r} 不是十六进制 commit"
                        "——符号引用（HEAD / 分支名 / tag）会随提交移动，钉不住签发时判的是哪一段")
            continue
        out[lab] = val
    return out, errs


def _repo_abs(project_dir, label):
    return Path(project_dir) if label == ROOT_LABEL else Path(project_dir) / label


def _is_repo_root(repo_abs):
    """这个目录**自己**是不是一个仓的根。

    只问 `git -C dir rev-parse` 不够：普通子目录会被它往上找到外层仓，于是「声明了一个不是仓的
    目录」静默变成「对外层仓取 diff」。比对 `--show-toplevel` 与目录本身，两者一致才算。
    """
    rc, out, _ = _git(repo_abs, ["rev-parse", "--show-toplevel"])
    if rc != 0 or not out.strip():
        return False
    try:
        return Path(out.strip()).resolve() == Path(repo_abs).resolve()
    except OSError:
        return False


def collect_slice(project_dir, since, diff_ref=None):
    """取每个声明仓的 `since..diff_ref` 内容级 delta。返回 (delta, errors)。

    `diff_ref` 缺省为各仓当前 HEAD（解析成 hash 后写进 delta，输出里看得到实际用的是哪个）。
    两端的仓集合必须一致；任一仓不是仓根、任一端解析不到 commit 都进 errors——**调用方据此
    拒绝判定，不降级成「少看一个仓照常判」**：少看的那个仓里的删除与尺寸会被静默漏算。
    """
    errors = []
    s_map, e1 = parse_refs(since, "since")
    errors.extend(e1)
    if diff_ref is None:
        d_map, e2 = {}, []
    else:
        d_map, e2 = parse_refs(diff_ref, "diff_ref")
        errors.extend(e2)
        if not e1 and not e2 and set(s_map) != set(d_map):
            errors.append(f"`since` 与 `diff_ref` 声明的仓不一致（since：{sorted(s_map)}；"
                          f"diff_ref：{sorted(d_map)}）——每个仓都要两端齐全")
    if errors:
        return None, errors

    repos = {}
    for label in sorted(s_map):
        repo_abs = _repo_abs(project_dir, label)
        if not repo_abs.is_dir() or not _is_repo_root(repo_abs):
            errors.append(f"仓 `{label}` 不存在或不是一个 git 仓的根目录（{repo_abs}）")
            continue
        ends = {}
        for end, ref in (("since", s_map[label]), ("diff_ref", d_map.get(label, "HEAD"))):
            rc, out, _ = _git(repo_abs, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
            if rc != 0 or not out.strip():
                errors.append(f"仓 `{label}` 里 `{end}` = `{ref}` 解析不到 commit")
            else:
                ends[end] = out.strip()
        if len(ends) < 2:
            continue
        files, errs = {}, []
        args = ["diff", "--unified=0", "--no-color", "--no-ext-diff",
                f"{ends['since']}..{ends['diff_ref']}"]
        rc, out, err = _git(repo_abs, args)
        if rc != 0:
            errs.append(f"{' '.join(args)}: rc={rc} {err.strip()[:120]}")
        else:
            parse_unified_diff(out, into=files)
        repos[label] = {"repo_rel": "" if label == ROOT_LABEL else label,
                        "since": ends["since"], "diff_ref": ends["diff_ref"],
                        "files": files, "errors": errs}
    if errors:
        return None, errors
    # diff 本身失败与「解析不到」同级：那个仓的改动没被看见，据此报「未命中」就是假绿
    for label, r in repos.items():
        errors.extend(f"仓 `{label}` 取 diff 失败：{e}" for e in r["errors"])
    if errors:
        return None, errors
    return {"repos": repos}, []


# ---------- 判定 ----------


def evaluate(delta, size_cap, runtime):
    """否决项判定。`needs_user` = 机器可判的命中任一条；`pending` 非空时判定不是终局（等人裁）。"""
    veto = evaluate_vetoes(delta, size_cap, runtime)
    return {"needs_user": bool(veto["hit"]), "veto": veto, "pending": veto["pending"]}


def evaluate_vetoes(delta, size_cap, runtime):
    adds = dels = 0
    binaries = []
    protected = []
    auto_update_only = []
    framework = []
    v1_hits = []
    v1_rewrites = []
    v2_version = []
    v2_release_notes = []

    for label, r in delta["repos"].items():
        prefix = (r["repo_rel"] + "/") if r["repo_rel"] else ""
        for path, fd in sorted(r["files"].items()):
            adds += fd.additions
            dels += fd.deletions
            if fd.binary:
                binaries.append(prefix + path)
            if is_veto6_path(path, runtime["veto6"]):
                protected.append(prefix + path)
            elif is_auto_update_protected(path, runtime["auto_update"]):
                # 差集：不封顶、不进待裁决，但要说出来——`auto-update` 规则自己仍然
                # 不许直接写它们。静默丢掉的话，读输出的人无从知道自己动了那张表。
                auto_update_only.append(prefix + path)
            if is_framework_asset(path):
                framework.append(prefix + path)

            base = path.rsplit("/", 1)[-1]
            if base in VERSION_BEARING_BASENAMES:
                if any('"version"' in ln for ln in fd.removed) and \
                   any('"version"' in ln for ln in fd.added):
                    v2_version.append(prefix + path)
            if base in RELEASE_NOTE_BASENAMES:
                v2_release_notes.append(prefix + path)

            if fd.deleted_whole:
                # 整文件删除是 `--diff-filter=D` 那一层的事，但它同样是删除，必须封顶；
                # 只登记一条，不把文件里每一行都算成一处条目删除（那会把明细刷爆）。
                v1_hits.append((prefix + path, "整文件删除",
                                f"（{fd.deletions} 行）"))
                continue
            # V1：结构化条目的 `-` 行，先在同文件内找归一化等价的 `+` 行（挪动），再在同一个
            # hunk 里找同键的新内容 `+` 行（原地改写），都找不到即命中。两步都**按个数一对一**
            # 抵：删两条只补回一条，净少的那条照样命中。被逐字匹配用掉的 `+` 行不再参与同键
            # 配对，否则「挪了一条、删了一条同键的」会被整个放过。
            removed = [(ln, h) for ln, h in zip(fd.removed, fd.removed_hunk)
                       if structured_kind(ln)]
            exact = Counter(normalize_entry(x) for x in fd.added)
            unmatched = []
            for ln, h in removed:
                n = normalize_entry(ln)
                if exact[n] > 0:
                    exact[n] -= 1
                else:
                    unmatched.append((ln, h))
            new_keys = Counter()
            for ln, h in zip(fd.added, fd.added_hunk):
                n = normalize_entry(ln)
                if exact[n] > 0:            # 没被逐字匹配用掉的那几条才是新内容
                    exact[n] -= 1
                    key = entry_key(ln)
                    if key:
                        new_keys[(h, key)] += 1
            for ln, h in unmatched:
                kind = structured_kind(ln)
                key = entry_key(ln)
                if key and new_keys[(h, key)] > 0:
                    new_keys[(h, key)] -= 1
                    v1_rewrites.append((prefix + path, kind, key[1][:100]))
                    continue
                v1_hits.append((prefix + path, kind, ln.strip()[:100]))

    size_total = adds + dels
    hit = []
    pending = []

    if size_total > size_cap:
        hit.append(f"尺寸上限：additions {adds} + deletions {dels} = {size_total} "
                   f"> 上限 {size_cap}")
    if v1_hits:
        hit.append(f"V1 结构化条目删除：{len(v1_hits)} 处")
    if v2_version:
        hit.append(f"V2 发版动作：版本号字段变更（{'、'.join(sorted(set(v2_version)))}）")

    # 机器判不了的一律说出来，**不许静默算作未命中**——静默未命中就是假绿。但要说在
    # 哪儿分两种，早先混在一处，后果是 `pending` 恒 ≥5 条、`conditional` 恒为 True：
    # 零改动的基线也打「⚠ 暂定——含 5 条待裁决项」，于是这个标注不携带任何信息
    # （违反 `response-output` §陈述可证伪与举证纪律自己写的「恒真即删」），
    # 而调用它的 hook 因此拿不到可机读的信号、只能读 `tier`。现在分成两个字段：
    #   model_review —— **本流程恒有**的模型裁量项，与本次 diff 无关，每次都要人判
    #   pending      —— **本轮 diff 真的产出了机器信号**、需要人裁的那几条
    # `conditional` 只看后者，因而可证伪：干净基线上它是 False。
    model_review = [
        "V2 发版动作：推 main / 打 tag 是动作不是 diff，机器永远看不见",
        "V3 判定命题被推翻：纯模型判定",
        "V4 产品决策（影响所有下游的 schema、warn/error 取舍）：纯模型判定",
        "V5 两个独立结论冲突：纯模型判定",
        "V6 受保护资产的「契约变更 vs 表述变更」：机器只判得了路径那一维",
    ]
    if v2_release_notes:
        pending.append(
            f"V2：本次触及对外 CHANGELOG（{'、'.join(sorted(set(v2_release_notes)))}），"
            "改的是不是已发布段须人判")
    if protected:
        pending.append(f"V6 受保护资产：路径命中 {len(sorted(set(protected)))} 处 → "
                       "「契约变更 vs 表述变更」由模型判，**拿不准一律算契约变更**"
                       "（裁为契约变更即封顶到用户）")
    if v1_hits:
        pending.append("V1 的 supersede 出口：原纪律第一顺位是「别删，改 supersede」，"
                       "机器判不了这次删除是不是走了 supersede")

    return {
        "hit": hit,
        "pending": pending,
        "model_review": model_review,
        "additions": adds,
        "deletions": dels,
        "size_total": size_total,
        "size_cap": size_cap,
        "binaries": sorted(set(binaries)),
        "protected": sorted(set(protected)),
        "auto_update_only": sorted(set(auto_update_only)),
        "framework_assets": sorted(set(framework)),
        "v1_hits": v1_hits,
        "v1_rewrites": v1_rewrites,
    }


# ---------- 输出 ----------


def _slice_lines(delta):
    return [f"仓 `{label}`：{r['since'][:12]}..{r['diff_ref'][:12]}（文件 {len(r['files'])}）"
            for label, r in sorted(delta["repos"].items())]


def render(result, delta):
    out = []
    w = out.append
    w("签发否决项判定")
    w("=" * 60)
    v = result["veto"]
    if v["hit"]:
        verdict = "命中否决项 → 交用户拍板（不论签发类别）"
    elif result["pending"]:
        verdict = (f"机器可判的未命中，但有 {len(result['pending'])} 条待裁决——"
                   "裁决之前不是终局：要么交用户拍板，要么逐条写出裁决")
    else:
        verdict = "机器可判的未命中"
    w(f"判定结论：{verdict}")
    w("")
    w("切片（只含已提交改动）")
    for line in _slice_lines(delta):
        w(f"  {line}")
    w("")
    w(f"尺寸：additions {v['additions']} + deletions {v['deletions']} = "
      f"{v['size_total']}（上限 {v['size_cap']}，口径是加减之和不是净变化）")
    if v["binaries"]:
        w(f"  二进制文件 {len(v['binaries'])} 个不计行数：{'、'.join(v['binaries'][:5])}")
    if v["hit"]:
        w("已命中（任一条即到用户）：")
        for h in v["hit"]:
            w(f"  ✗ {h}")
    else:
        w("机器可判的那几条：未命中")
    if v["v1_hits"]:
        w(f"V1 明细（前 10 条，共 {len(v['v1_hits'])}）：")
        for path, kind, sample in v["v1_hits"][:10]:
            w(f"  - {path} [{kind}] {sample}")
    if v["v1_rewrites"]:
        w(f"（提示，不参与封顶）V1 按同键改写放行 {len(v['v1_rewrites'])} 处——同一个 hunk 里"
          "有类别与键都相同的新行，按原地改写算；键是表格首格或列表项开头的标签。机器只证明"
          "了这一点，是不是同一条目请签发方核（前 10 条，全部见 --json 的 v1_rewrites）：")
        for path, kind, key in v["v1_rewrites"][:10]:
            w(f"  · {path} [{kind}] 键 {key}")
    if v["protected"]:
        w(f"V6 路径命中：{'、'.join(v['protected'][:10])}")
    if v["auto_update_only"]:
        w(f"（提示，不参与封顶）触及 `auto-update` §受保护资产 但不在否决项 6 清单内"
          f" {len(v['auto_update_only'])} 处：{'、'.join(v['auto_update_only'][:5])}"
          "——两张表是真子集关系，差集里的路径仍受 auto-update 约束，只是不自动封顶")
    if v["framework_assets"]:
        w(f"（提示，不参与封顶）触及会分发的框架资产 {len(v['framework_assets'])} 处"
          "——要不要独立验证由签发方按 §谁签发这次收口 的判据定，不由本脚本判")
    if result["pending"]:
        w("待裁决（**本轮真的产出了机器信号**，裁决为未命中之前本判定不是终局）：")
        for p in result["pending"]:
            w(f"  ? {p}")
    else:
        w("待裁决：无——本轮 diff 没有产出需要人裁的机器信号")
    w("恒有的模型裁量项（**与本次 diff 无关，每次都要人判**，机器永远算不了）：")
    for p in v["model_review"]:
        w(f"  · {p}")
    w("")
    w("本脚本没验的：签发类别判得对不对；机器闸是否全绿（不跑 validate / doctor / close-check）；"
      "未提交改动与未声明的仓；上面那几条恒有的模型裁量项。")
    return "\n".join(out)


def to_json(result, delta):
    v = result["veto"]
    return {
        "needs_user": result["needs_user"],
        "conditional": bool(result["pending"]),
        "veto": {k: v[k] for k in ("hit", "additions", "deletions", "size_total",
                                   "size_cap", "binaries", "protected",
                                   "auto_update_only", "framework_assets")},
        "pending": result["pending"],
        "model_review": v["model_review"],
        "slice": {label: {"since": r["since"], "diff_ref": r["diff_ref"],
                          "files": len(r["files"])}
                  for label, r in delta["repos"].items()},
        "v1_hits": [{"path": p, "kind": k, "sample": s} for p, k, s in v["v1_hits"]],
        "v1_rewrites": [{"path": p, "kind": k, "key": s} for p, k, s in v["v1_rewrites"]],
    }


# ---------- main ----------


def _cli_refs(text):
    """`hash` 或 `LABEL=hash,LABEL=hash` → parse_refs 吃的形态。"""
    if text is None:
        return None
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) == 1 and "=" not in parts[0]:
        return parts[0]
    out = {}
    for p in parts:
        label, sep, ref = p.partition("=")
        out[label.strip() if sep else ROOT_LABEL] = ref.strip() if sep else label.strip()
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="signoff_tier_check.py",
        description="吃一段钉死两端的 diff，吐否决项判定：命中即交用户拍板",
    )
    ap.add_argument("--project-dir", default=None, help="项目根；默认按 harness 解析")
    ap.add_argument("--since", required=True,
                    help="切片起点：一个 hash（项目根那个仓），或 LABEL=hash,LABEL=hash（根仓写 .）")
    ap.add_argument("--diff-ref", default=None,
                    help="切片终点，形态同 --since；缺省为各声明仓的当前 HEAD")
    ap.add_argument("--size-cap", type=int, default=DEFAULT_SIZE_CAP,
                    help=f"尺寸上限（additions+deletions），默认 {DEFAULT_SIZE_CAP}")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    project_dir = Path(args.project_dir).resolve() if args.project_dir else _project_dir()
    if not project_dir.is_dir():
        print(f"[拒绝判定] 项目根不存在：{project_dir}", file=sys.stderr)
        return 1

    delta, errors = collect_slice(project_dir, _cli_refs(args.since), _cli_refs(args.diff_ref))
    if errors:
        print("[拒绝判定] 切片声明不成立——看不全的切片据此报「未命中」就是假绿：", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        return 1

    result = evaluate(delta, args.size_cap, runtime_protected(project_dir))
    if args.json:
        print(json.dumps(to_json(result, delta), ensure_ascii=False, indent=2))
    else:
        print(render(result, delta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
