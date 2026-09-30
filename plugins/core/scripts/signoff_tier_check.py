#!/usr/bin/env python3
"""签发档判定 — 吃 diff，吐「本次签发档 = X」及其四段依据。

四段闸门（默认起点 → 通行证 → 地板 → 封顶）中三段机器可算，通行证那段机器只验
**声明格式与 hash 可解析**。本脚本把机器能算的那部分算出来，把算不出来的**显式标成
「待裁决」而不是「未命中」**——静默未命中就是假绿，而假绿与真绿在终端里长得一样。

用法：
    python signoff_tier_check.py [--project-dir DIR] [--radius {1,2,3,4}]
                                 [--since REF] [--passport FILE|-] [--task ID]
                                 [--size-cap N] [--json]

退出码（**不承载档位**，档位在输出里；挂 hook 时的阻断策略由调用方定）：
    0  判定已产出（可能带「待裁决」标注）
    1  拒绝判定——输入或扫描面不足以给出可信结论
    2  用法错误（argparse）

**本脚本自己就是「判断别的东西对不对」的东西**，改它按爆炸半径档 4 处置：先跑基线、
用它自己的失败方式打一遍、每条红灯分支各配一个真的走到它的探针。

## 能力边界（读输出前先读这段）

1. **通行证只验格式与 hash 可解析，验不了「这份独立结论真的覆盖了本轮 delta」**——那是
   模型判的，且它出过错。这是本流程最软的一环。
2. **六条否决项里只有部分是机器可判的**：V1 内容级可判、V2 只判得了版本号字段（推 main /
   打 tag / 对外 CHANGELOG 已发布段不在 diff 里）、V6 只判路径面（契约变更 vs 表述变更
   由模型判）、V3/V4/V5 纯模型。凡机器判不了的一律标「待裁决」。
3. **爆炸半径是模型判的**，本脚本只读 `--radius` 的取值；未声明时按最严地板处理。
4. **切片起点**：给了 `--since` 就用它，没给就退回「未提交改动」。签注记录的 hash 是切片
   起点的最终形态，那个字段还不存在，所以此刻的起点由调用方自划——输出里会写明。
   **多仓项目下 `--since` 是单个 ref，寻址不了两个仓**：起点在某个仓解析不到时该仓只贡献
   未提交改动，这被归为**扫描面缺口**（因而禁止降档），不是「照常判定＋一行提示」。
5. **尺寸上限过滤的是「大而浅」，漏的是「小而不可逆」**：行数与风险量纲正交，改 31 份
   文档里同一个术语只有 62 行却是 31 个表现层。这一格没有判据。
6. **不判「机器闸全绿」**：本脚本不跑 validate / doctor / close-check，别把它的绿读成那三道的绿。
7. **尺寸上限可被调用方自己抬掉**：`--size-cap` 无下限、放大后**不产生任何缺口或提示**，
   于是这条唯一的纯机器否决项生效与否取决于被约束者本人（实测 5001 行改动：默认判
   `你拍板`，加 `--size-cap 999999999` 判 `自签`）。默认值是 2026-09-09 用户拍板的产品
   决策，用户同日就这条判「先不管」——**这是被知情接受的风险，不是被覆盖掉的风险**。
8. **共享对象的仓会让 `--since` 跑出跨树 diff**：`git clone --shared` / worktree / 配了
   `alternates` 的仓里，属于**外仓**的 commit 也解析得到，脚本据此算出的 diff 会凭空造出
   该仓从未有过的改动（实测：一条整文件删除，并因此封顶到 `你拍板`），**且不产生任何缺口**
   ——「解析得到」被当成「起点属于本仓」。两个独立仓、无 alternates 的拓扑不触发；
   vendored clone 与 worktree 会。
9. **diff 解析器认不出内容本身以 `-- ` / `++ ` 开头的行**：它们在 unified diff 里长成
   `--- xxx` / `+++ xxx`，与文件头同形，于是被当成文件头。两种后果：`-- ` 那条删除行被
   **整行吞掉（deletions 少算，方向是漏而不是多，正好是尺寸上限该看住的方向）**；
   `++ ` 那条新增行造出一个**幻影文件**，并且**其后的加减行都改挂在幻影名下**，真实文件
   那侧同时少算。SQL 注释（`-- comment`）是最常见的形态。真实资产命中 0 处，故本批只登记；
   修法（给 `---` / `+++` 的文件头判定加「必须紧跟 `diff --git` 之后」的状态约束）与其余
   寻址/解析类问题同批处理。`tools/test_signoff_tier_check.py` 有一条**现状用例**钉住它，
   修好之后那条会报红——那是提醒去更新它，不是回归。
10. **V6 路径命中只进「待裁决」，不封顶**——这是**判定的中间态，不是终局**。必载段
   （`closeout-discipline` §否决项 6 的清单与判法）已把方向钉死：路径命中后由模型判
   契约变更还是表述变更，**拿不准一律算契约变更**（⇒ 封顶到用户）。所以本脚本报
   `自签` 而 `pending` 里挂着 V6 那条时，**那不是「可以自签」，是「等人裁」**。
   会静默失效的形态：有人只读 `tier` 不读 `pending`。消费方（board 写入 hook）必须两个都读。
"""

import argparse
import json
import re
import subprocess
import sys
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
from _git_scan import collect_changed_files  # noqa: E402
from _harness import project_dir as _project_dir  # noqa: E402
from _state_io import runtime_rel, state_dir_of  # noqa: E402

GIT_TIMEOUT_SEC = 30

# ---------- 档位格 ----------
# 排序即严格程度，索引越大越严；一切合成用 `stricter()`，不许在别处再写一遍比较。
TIER_SELF = "自签"
TIER_LIGHT = "轻签注"
TIER_FULL = "完整验证"
TIER_USER = "你拍板"
TIER_ORDER = (TIER_SELF, TIER_LIGHT, TIER_FULL, TIER_USER)

# 爆炸半径只当地板（往上抬，不发通行证）：档 4 降不下去，档 3 最低到轻签注，档 1-2 可到自签。
# **档 4 那一格是整套闸门的承重面**——「改的东西自己就是判据」，本脚本自己正是这类资产；
# 它被改成可降档的话，档 4 资产就能自签，而这件事没有别的东西看得住。四格逐格由
# `tools/test_signoff_tier_check.py` 的承重语义组守着（每格各配一条变异反证）。
RADIUS_FLOOR = {1: TIER_SELF, 2: TIER_SELF, 3: TIER_LIGHT, 4: TIER_FULL}

# 默认起点：没有任何独立视角看过的东西，默认就该被独立看一次。
# **收成一个常量**——这个事实此前在 `evaluate()` 里被手写了三份（`result["tier"]` 初值、
# `segments.default_start`、局部变量 `tier`），改其中一份另外两份不跟着动、也没有任何东西
# 会报红（实测：把 `default_start` 那份改掉，整套单测仍绿）。同一事实不留第二份。
DEFAULT_START = TIER_FULL

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
# 资产」这个事实说出来：「谁实现」那条轴据此定为恒 @dev，而那条轴不是本脚本的判定对象。
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


def stricter(a, b):
    return TIER_ORDER[max(TIER_ORDER.index(a), TIER_ORDER.index(b))]


# ---------- git 薄封装（纯读，不写事件） ----------
# 不复用 csm 的 `_git_lines`：那个的失败路径会 append `modules_check_stale_error`，
# 而本命令是判定用的只读入口，往运行态里写噪声事件不合适（`module_close_check` 同款取舍：
# 它同样不写事件，写入只有它自己的计数日志及其锁文件）。
# 扫描面本身仍复用 `_git_scan`，两者是不同的东西：口径共用，留痕策略各自负责。


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


def git_lines(repo_abs, args, label):
    """`_git_scan` 的注入点：成功返回行列表，失败返回 None（由它记 note）。"""
    rc, out, _err = _git(repo_abs, args)
    if rc != 0:
        return None
    return out.splitlines()


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
        if cur is None or line.startswith("@@") or line.startswith("\\"):
            continue
        if line.startswith("+"):
            cur.added.append(line[1:])
        elif line.startswith("-"):
            cur.removed.append(line[1:])
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

    「有没有对应的 `+` 行」按归一化后**逐字相等**判。改写措辞会被判成删除（假红，
    方向 fail-safe，按假红处置分档登记不阻断）；判据故意不做模糊匹配——相似度阈值
    是拍脑袋的数，而这条判定的下游是「要不要惊动用户」。
    """
    return " ".join(content.split())


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


# ---------- 输入模型 ----------


def load_index_patterns(project_dir):
    """读 code-paths-index 的 patterns。返回 (patterns, gap_reason|None)。

    patterns 是嵌套仓发现的**唯一输入**：读不到它，被主仓 ignore 的嵌套仓就一个都发现
    不了，而那正是尺寸上限与路径类判定在框架仓侧恒为 0 的成因。所以读不到不是「没有
    嵌套仓」，是**扫描面有缺口**，必须让调用方看见。
    """
    idx = state_dir_of(Path(project_dir)) / "code-paths-index.json"
    if not idx.is_file():
        return [], f"code-paths-index 不存在（{idx.name}）"
    try:
        data = json.loads(idx.read_text(encoding="utf-8"))
    except Exception as e:
        return [], f"code-paths-index 解析失败：{type(e).__name__}"
    buckets = data.get("buckets")
    if not isinstance(buckets, dict) or not buckets:
        return [], "code-paths-index 的 buckets 为空"
    # bucket 的值是 `{pattern: [submodule,...]}`，遍历它取到的是 pattern——与
    # `scan_git_diff_for_stale` 取 patterns 的写法逐字相同。**别加 `isinstance(b, list)`
    # 这类守卫**：值是 dict 不是 list，守卫会把 104 条 pattern 全过滤掉，然后嵌套仓
    # 一个都发现不了，而现象只是「扫描面缺口」这条提示——首跑实测就撞了这一下。
    try:
        pats = [p for b in buckets.values() for p in b]
    except TypeError:
        return [], "code-paths-index 的 buckets 结构异常（值不可遍历）"
    if not pats:
        return [], "code-paths-index 的 buckets 里没有任何 pattern"
    return pats, None


def collect_delta(project_dir, patterns, since_ref):
    """把扫描面转成内容级 delta。返回 dict。

    扫描面（哪些仓 × 哪几类改动）来自 `_git_scan.collect_changed_files`——与 stale 扫描
    **同一份实现**。本函数只做它不管的那半：把文件名换成加减行。
    """
    by_repo, notes = collect_changed_files(
        project_dir, patterns, git_ok, git_lines, since_ref=since_ref
    )
    unresolved = {label for kind, label, _ in notes if kind == "since_ref_unresolved"}

    repos = {}
    for repo_rel, names in by_repo.items():
        repo_abs = Path(project_dir) / repo_rel if repo_rel else Path(project_dir)
        label = repo_rel or "."
        files = {}
        errs = []
        has_head = git_ok(repo_abs, ["rev-parse", "--verify", "--quiet", "HEAD"])
        # 与 `_git_scan` 的三类一一对应，只是把 `--name-only` 换成内容：
        #   有 HEAD → `diff HEAD` 已含 staged + unstaged，不能再叠 `--cached`（会重复计数）
        #   无 HEAD → 一切改动落在 staged 与 untracked 两条里
        base = ["diff", "--unified=0", "--no-color", "--no-ext-diff"]
        cmds = [base + ["HEAD"]] if has_head else [base + ["--cached"]]
        if since_ref and has_head and label not in unresolved:
            cmds.append(base + [f"{since_ref}..HEAD"])
        for args in cmds:
            rc, out, err = _git(repo_abs, args)
            if rc != 0:
                errs.append(f"{' '.join(args)}: rc={rc} {err.strip()[:120]}")
                continue
            parse_unified_diff(out, into=files)

        # 未跟踪文件没有 diff：整份都是新增。二进制不按行算，只登记。
        tracked = set(files)
        for name in sorted(names):
            if name in tracked:
                continue
            f = repo_abs / name
            if not f.is_file():
                continue
            fd = FileDelta(name)
            try:
                raw = f.read_bytes()
            except OSError as e:
                errs.append(f"读未跟踪文件失败 {name}: {type(e).__name__}")
                continue
            if b"\x00" in raw[:8192]:
                fd.binary = True
            else:
                fd.added = raw.decode("utf-8", errors="replace").splitlines()
            files[name] = fd
        repos[label] = {"repo_rel": repo_rel, "files": files, "errors": errs}
    return {"repos": repos, "notes": notes, "since_unresolved": sorted(unresolved)}


# ---------- 通行证 ----------

PASSPORT_FIELDS = ("task", "round", "covered", "ref")


def check_passport(raw, delta, current_task):
    """验通行证的**声明格式与 hash 对应**。返回 (ok, lines)。

    **验不了的那件事必须说出来**：这份独立结论有没有真的覆盖本轮 delta 是模型判的，
    机器在这里一个字都判不了（脱敏一族就是「上轮结论以为覆盖了、实际没有」）。
    """
    lines = []
    if not isinstance(raw, dict):
        return False, ["通行证不是 JSON 对象 → 声明不合法，不得降档"]
    missing = [k for k in PASSPORT_FIELDS
               if not isinstance(raw.get(k), str) or not raw[k].strip()]
    if missing:
        return False, [f"通行证缺字段或为空：{'、'.join(missing)}"
                       f"（四段必填：{'、'.join(PASSPORT_FIELDS)}）→ 不得降档"]

    ref = raw["ref"].strip()
    hit = []
    # ref 逐仓解析：多仓下同一个 hash 通常只属于一个仓，只要有一个仓认得它就算可解析
    for label, repo_abs in delta["_repo_abs"].items():
        if git_ok(repo_abs, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]):
            hit.append(label)
    if not hit:
        return False, [f"通行证 ref `{ref}` 在扫描到的任何一个仓里都解析不到 commit"
                       " → 声明的被验对象不存在，不得降档"]
    lines.append(f"ref `{ref}` 可解析（仓：{'、'.join(hit)}）")

    declared_task = raw["task"].strip()
    if not current_task:
        # **`--task` 缺失时不许静默放行。** 本轮任务未知 ⇒ 判不出这份通行证是不是跨任务
        # 继承，而跨任务那条门被真实绕过过一次，本判定的目的正是把暗门改明路。
        # 早先写作 `if current_task and ...`，于是少传一个可选参数就把整段校验关掉了——
        # 而**省略这个参数的人正是降档的受益人**，与 §5.3「切片的人正是受益的人」同构。
        # 处置复用既有的「通行证不成立 ⇒ 停在默认档」，不另造机制。
        return False, lines + [
            f"本轮任务未声明（缺 `--task`），无从判断这份通行证（声明 {declared_task}）"
            "是不是跨任务继承 → **通行证不成立，不得降档**。"
            "跨任务继承本身不禁止，但必须写明哪个任务、哪一轮、覆盖了什么，且可核"]
    if declared_task != current_task:
        if raw.get("cross_task") is not True:
            return False, lines + [
                f"通行证来自别的任务（声明 {declared_task}，本轮 {current_task}）却未写"
                " `\"cross_task\": true` → 跨任务继承必须显式声明，不得降档"]
        lines.append(f"跨任务继承已显式声明：{declared_task} / {raw['round'].strip()}")
    else:
        lines.append(f"任务与轮次：{declared_task} / {raw['round'].strip()}")
    lines.append(f"声明覆盖了：{raw['covered'].strip()[:160]}")
    lines.append("⚠ 机器只验到这里——「这份独立结论是否真的覆盖本轮 delta」验不了（§12-07）")
    return True, lines


# ---------- 判定 ----------


def evaluate(delta, radius, size_cap, passport_raw, current_task, scan_gaps, runtime):
    # ① 默认起点——三处同值全部取自 `DEFAULT_START`，不再各写一份
    tier = DEFAULT_START
    result = {
        "tier": tier,
        "segments": {"default_start": tier},
        "pending": [],
        "gaps": list(scan_gaps),
    }

    # ② 通行证
    if passport_raw is None:
        passport_ok, passport_lines = False, ["未提供通行证（--passport）→ 停在默认档"]
    else:
        passport_ok, passport_lines = check_passport(passport_raw, delta, current_task)
    result["segments"]["passport"] = {"ok": passport_ok, "lines": passport_lines}

    # ③ 地板
    if radius is None:
        floor = TIER_FULL
        floor_note = "爆炸半径未声明（--radius）→ 按最严处理，地板 = 完整验证"
    else:
        floor = RADIUS_FLOOR[radius]
        floor_note = f"爆炸半径档 {radius} → 地板 = {floor}"
    result["segments"]["floor"] = {"tier": floor, "note": floor_note}

    if passport_ok:
        tier = floor
    # 通行证不成立时 tier 保持默认档——地板只往上抬，不发通行证。

    # 扫描面有缺口时不许降档：缺口那半的改动没被看见，据此降档就是拿看不见的东西换权限。
    if scan_gaps:
        tier = stricter(tier, TIER_FULL)

    # ④ 封顶
    veto = evaluate_vetoes(delta, size_cap, runtime)
    result["segments"]["veto"] = veto
    if veto["hit"]:
        tier = TIER_USER
    result["pending"] = veto["pending"]

    result["tier"] = tier
    return result


def evaluate_vetoes(delta, size_cap, runtime):
    adds = dels = 0
    binaries = []
    protected = []
    auto_update_only = []
    framework = []
    v1_hits = []
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
            # V1：结构化条目的 `-` 行，同文件内无归一化等价的 `+` 行即命中。
            added_norm = {normalize_entry(x) for x in fd.added}
            for line in fd.removed:
                kind = structured_kind(line)
                if kind and normalize_entry(line) not in added_norm:
                    v1_hits.append((prefix + path, kind, line.strip()[:100]))

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
    }


# ---------- 输出 ----------


def render(result, delta, slice_note, scan_summary):
    out = []
    w = out.append
    w("签发档判定（四段闸门）")
    w("=" * 60)
    tail = ""
    if result["pending"]:
        tail = f"   ⚠ 暂定——含 {len(result['pending'])} 条待裁决项，裁决前不是终局"
    w(f"判定结论：{result['tier']}{tail}")
    w("")
    w("扫描面")
    for line in scan_summary:
        w(f"  {line}")
    w(f"  切片锚定：{slice_note[0]}")
    for extra in slice_note[1:]:
        w(f"     {extra}")
    if result["gaps"]:
        w("  ⚠ 缺口（缺口存在时禁止降档——降档等于拿看不见的东西换权限）：")
        for g in result["gaps"]:
            w(f"     - {g}")
    else:
        w("  缺口：无")
    w("")
    w(f"① 默认起点：@qa {result['segments']['default_start']}")
    ps = result["segments"]["passport"]
    w(f"② 通行证：{'成立' if ps['ok'] else '不成立'}")
    for line in ps["lines"]:
        w(f"     {line}")
    w(f"③ 地板：{result['segments']['floor']['note']}")
    v = result["segments"]["veto"]
    w("④ 封顶：")
    w(f"     尺寸：additions {v['additions']} + deletions {v['deletions']} = "
      f"{v['size_total']}（上限 {v['size_cap']}，口径是加减之和不是净变化）")
    if v["binaries"]:
        w(f"       二进制文件 {len(v['binaries'])} 个不计行数：{'、'.join(v['binaries'][:5])}")
    if v["hit"]:
        w("     已命中（任一条即到用户）：")
        for h in v["hit"]:
            w(f"       ✗ {h}")
    else:
        w("     机器可判的那几条：未命中")
    if v["v1_hits"]:
        w(f"     V1 明细（前 10 条，共 {len(v['v1_hits'])}）：")
        for path, kind, sample in v["v1_hits"][:10]:
            w(f"       - {path} [{kind}] {sample}")
    if v["protected"]:
        w(f"     V6 路径命中：{'、'.join(v['protected'][:10])}")
    if v["auto_update_only"]:
        w(f"     （提示，不参与封顶）触及 `auto-update` §受保护资产 但不在否决项 6 清单内"
          f" {len(v['auto_update_only'])} 处：{'、'.join(v['auto_update_only'][:5])}"
          "——两张表是真子集关系，差集里的路径仍受 auto-update 约束，只是不自动封顶")
    if v["framework_assets"]:
        w(f"     （提示，不参与封顶）触及会分发的框架资产 {len(v['framework_assets'])} 处"
          "——「谁实现」那条轴据此定为恒 @dev，不由本脚本判")
    if result["pending"]:
        w("     待裁决（**本轮真的产出了机器信号**，裁决为未命中之前本判定不是终局）：")
        for p in result["pending"]:
            w(f"       ? {p}")
    else:
        w("     待裁决：无——本轮 diff 没有产出需要人裁的机器信号")
    w("     恒有的模型裁量项（**与本次 diff 无关，每次都要人判**，机器永远算不了）：")
    for p in v["model_review"]:
        w(f"       · {p}")
    w("")
    w("本脚本没验的：机器闸是否全绿（不跑 validate / doctor / close-check）；"
      "通行证覆盖的真实性；爆炸半径判得对不对；上面那五条恒有的模型裁量项。")
    return "\n".join(out)


def to_json(result, delta, slice_note, scan_summary):
    v = result["segments"]["veto"]
    return {
        "tier": result["tier"],
        "conditional": bool(result["pending"]),
        "segments": {
            "default_start": result["segments"]["default_start"],
            "passport": {"ok": result["segments"]["passport"]["ok"],
                         "lines": result["segments"]["passport"]["lines"]},
            "floor": result["segments"]["floor"],
            "veto": {k: v[k] for k in ("hit", "additions", "deletions", "size_total",
                                       "size_cap", "binaries", "protected",
                                       "auto_update_only", "framework_assets")},
        },
        "pending": result["pending"],
        "model_review": v["model_review"],
        "gaps": result["gaps"],
        "slice": slice_note,
        "scan": scan_summary,
        "v1_hits": [{"path": p, "kind": k, "sample": s} for p, k, s in v["v1_hits"]],
    }


# ---------- main ----------


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="signoff_tier_check.py",
        description="吃 diff，吐「本次签发档 = X」及四段依据（默认起点 / 通行证 / 地板 / 封顶）",
    )
    ap.add_argument("--project-dir", default=None, help="项目根；默认按 harness 解析")
    ap.add_argument("--radius", type=int, choices=[1, 2, 3, 4], default=None,
                    help="爆炸半径档位（模型判出的结果）；不给则按最严地板处理")
    ap.add_argument("--since", default=None,
                    help="切片起点 ref；不给则只看未提交改动")
    ap.add_argument("--passport", default=None,
                    help="通行证 JSON 文件路径，`-` 表示从 stdin 读")
    ap.add_argument("--task", default=None, help="本轮任务 ID，用于判通行证是否跨任务")
    ap.add_argument("--size-cap", type=int, default=DEFAULT_SIZE_CAP,
                    help=f"尺寸上限（additions+deletions），默认 {DEFAULT_SIZE_CAP}")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)

    project_dir = Path(args.project_dir).resolve() if args.project_dir else _project_dir()
    if not project_dir.is_dir():
        print(f"[拒绝判定] 项目根不存在：{project_dir}", file=sys.stderr)
        return 1

    passport_raw = None
    if args.passport is not None:
        try:
            text = sys.stdin.read() if args.passport == "-" \
                else Path(args.passport).read_text(encoding="utf-8")
            passport_raw = json.loads(text)
        except FileNotFoundError:
            print(f"[拒绝判定] 通行证文件不存在：{args.passport}"
                  "——声明了通行证却读不到，按「无通行证」处理会把不成立读成没声明，"
                  "两者的下游动作不同", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"[拒绝判定] 通行证解析失败（{type(e).__name__}）：{args.passport}",
                  file=sys.stderr)
            return 1

    patterns, index_gap = load_index_patterns(project_dir)
    scan_gaps = []
    if index_gap:
        scan_gaps.append(
            f"{index_gap} → 嵌套仓发现面为空。被主仓 ignore 的嵌套仓（框架仓是典型）"
            "的改动不在本次判定内，尺寸与路径类判定在那一侧恒为 0")

    delta = collect_delta(project_dir, patterns, args.since)

    # 通行证 ref 要逐仓解析，把仓的绝对路径带给 check_passport
    delta["_repo_abs"] = {
        label: (project_dir / r["repo_rel"] if r["repo_rel"] else project_dir)
        for label, r in delta["repos"].items()
    }

    if not delta["repos"]:
        print("[拒绝判定] 一个 git 仓都没扫到——项目根不是 git 仓且未发现嵌套仓。"
              "此时「零改动」与「看不见改动」无法区分，据此报绿就是假绿", file=sys.stderr)
        return 1

    for kind, label, detail in delta["notes"]:
        if kind == "broken_nested_repo":
            scan_gaps.append(f"嵌套仓 {label} 探测失败：{detail}")
        elif kind == "git_failed":
            scan_gaps.append(f"仓 {label} 的 `git {detail}` 失败——该类改动本次未收集")
        elif kind == "since_ref_unresolved":
            # **切片起点解析不到必须归到缺口，不能只印一行散文。** `--since` 是单个 ref
            # 而项目可能是多仓：起点在仓 A 有效、在仓 B 无效时，B 只贡献未提交改动，
            # 它在起点之后的**已提交**改动整段不进 delta——尺寸上限与否决项在 B 那侧
            # 被少算，而「缺口：无」照样打出来。实测同一个任务用两个合法起点算出
            # 1016（命中上限）与 96（不命中）两个相反答案。归到缺口即走「有缺口不得
            # 降档」那条既有通道，方向 fail-safe，不另造一套并行机制。
            scan_gaps.append(
                f"切片起点在仓 {label} 解析不到（{detail}）——**该仓只贡献未提交改动**，"
                "它在起点之后的已提交改动整段不在本次 delta 里，"
                "尺寸上限与否决项在那一侧被少算")
    for label, r in delta["repos"].items():
        for e in r["errors"]:
            scan_gaps.append(f"仓 {label} 取 diff 内容失败：{e}")

    if args.since:
        resolved = [l for l in delta["repos"] if l not in delta["since_unresolved"]]
        if not resolved:
            print(f"[拒绝判定] --since `{args.since}` 在扫描到的任何一个仓里都解析不到。"
                  "切片起点无效时整个 delta 失去锚定，此时算出的档位是对一个未定义区间"
                  "的判定", file=sys.stderr)
            return 1
        slice_note = [
            f"`{args.since}`..HEAD ＋ 未提交改动（仓：{'、'.join(resolved)}）",
            "起点由调用方给定；board 签注记录字段落地后应改由它记录的 hash 提供",
        ]
        # 「哪些仓解析不到」不在这里重说一遍——它已经是上面的缺口条目，且那条带后果。
        # 同一事实两个表现层必漂，留带后果的那个。
    else:
        slice_note = [
            "未提交改动（HEAD → 工作区，含 staged 与未跟踪）",
            "**未给 --since**：本批尚无签注记录字段，切片起点由调用方自划（§12-08）。"
            "已提交的改动不在本次判定内",
        ]

    nfiles = sum(len(r["files"]) for r in delta["repos"].values())
    scan_summary = [
        f"项目根：{project_dir}",
        f"仓：{'、'.join(sorted(delta['repos']))}",
        f"文件：{nfiles}",
    ]

    result = evaluate(delta, args.radius, args.size_cap, passport_raw, args.task,
                      scan_gaps, runtime_protected(project_dir))

    if args.json:
        print(json.dumps(to_json(result, delta, slice_note, scan_summary),
                         ensure_ascii=False, indent=2))
    else:
        print(render(result, delta, slice_note, scan_summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
