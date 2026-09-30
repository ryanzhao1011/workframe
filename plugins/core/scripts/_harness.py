#!/usr/bin/env python3
"""harness（CC / Codex）差异的唯一收口点：环境变量、项目根、会话号、插件根、门标识。

为什么单独成模块：`CLAUDE_PROJECT_DIR` / `CLAUDE_PLUGIN_ROOT` / `CLAUDE_CODE_SESSION_ID`
是 **Claude Code 专属**环境变量，Codex 一个都不设。散在各脚本里直接 `os.environ.get(...)`
的写法在 Codex 门下会**静默走兜底分支**——不报错、不告警，只是行为悄悄变了另一套。
本模块把这几个读取点收成一处，各脚本改为调用；validate 的 `no_direct_claude_env`
钉住「除本文件外没有第二处直读」。

**门由命令行参数声明，不由环境探测**（见 `harness()`）。

被 hook 以同目录 import 方式使用，因此：
  - 模块 import 必须无副作用（不碰 stdout、不建目录、不读磁盘）
  - 文件名用下划线而非连字符——连字符的模块名 import 不进来
"""

import os
import sys
from pathlib import Path

CC = "cc"
CODEX = "codex"
UNKNOWN = "unknown"
HARNESSES = (CC, CODEX)

# 项目根标记：scaffold 装机必落，且**只在项目根**存在（见 project_scaffold.write_workframe_config）
ROOT_MARKER = ".workframe-config.json"


def find_root(start=None):
    """从 `start`（默认 cwd）向上找 `ROOT_MARKER`；找不到返回 None。"""
    try:
        here = Path(start).resolve() if start else Path.cwd().resolve()
    except OSError:
        return None
    for cand in (here, *here.parents):
        try:
            if (cand / ROOT_MARKER).is_file():
                return cand
        except OSError:
            continue
    return None


def project_dir(payload=None):
    """项目根。

    **Codex 门（`harness() == "codex"`）**：不读 `CLAUDE_PROJECT_DIR`——Codex 从不设它，读到只可能是
    从外层会话泄漏进来的，而那会把状态写进另一个项目（实测：会话在项目 A、变量指向项目 B 时全部写进 B）。
    从载荷 `cwd` **向上找** `ROOT_MARKER`，找到即用根；没有载荷就从进程 cwd 向上找；都找不到时退回载荷
    `cwd`（或进程 cwd）——那是非 workframe 目录，`hook_outside_project()` 已先让 hook 退出。
    无载荷时按进程 cwd 找根（多数 hook 脚本在模块顶层这样调）——与守卫按载荷 `cwd` 判一致的前提是 Codex 的
    hook 工作目录契约，见 `hook_outside_project()`。
    向上找而不直接用载荷 `cwd`：子目录里起的会话若直接用它，带载荷的脚本写进子目录、只按进程 cwd 找根的
    脚本写到根，同一次会话劈成两棵状态树（实测清单 17 条命令里 5 条写子目录、7 条写根）。

    **其余（`cc` / `unknown`）**：CC 给 `CLAUDE_PROJECT_DIR`；否则 payload 的 `cwd`；都没有就找根标记。

    **兜底不用 `os.getcwd()`**：从子目录启动时那会在子目录下新建一整棵状态树，而
    hook 下一次在项目根启动又读回原来那棵——两棵树互不知情，事件与计数各记一半，
    且没有任何一处会报错（C09 实测）。向上找 `ROOT_MARKER` 才回答得了「哪个是项目根」。
    找不到标记时（非 workframe 目录）才退回 cwd，与改造前同值。
    """
    if harness() == CODEX:
        cwd = payload.get("cwd") if isinstance(payload, dict) else None
        if isinstance(cwd, str) and cwd.strip():
            root = find_root(cwd)
            return root if root is not None else Path(cwd).resolve()
        root = find_root()
        return root if root is not None else Path(".").resolve()
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env).resolve()
    if isinstance(payload, dict):
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            return Path(cwd).resolve()
    root = find_root()
    if root is not None:
        return root
    return Path(".").resolve()


def session_id(payload=None):
    """会话号：payload 优先（两个 harness 都给），环境变量兜底（仅 CC 有）。

    取不到返回空串——调用方按「没有会话号」处理，不要编一个。
    旧名 `$CLAUDE_SESSION_ID` 从来不是环境变量，不要读。
    """
    if isinstance(payload, dict):
        sid = payload.get("session_id")
        if isinstance(sid, str) and sid.strip():
            return sid
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or ""


# ---------- hook 载荷读取 ＋ 「不在 workframe 项目内就早退」的守卫 ----------

_PAYLOAD_CACHE = {}
# 被换下来的旧 `sys.stdin` 与新换上的 BytesIO / wrapper：模块级持引用。旧对象若无人引用被 GC，
# 它的 `__del__` 会关掉共用的底层 buffer，之后任何读写都是 `I/O operation on closed file`。
_STDIN_HOLD = []


def hook_payload_bytes(argv=None):
    """Codex 门下 hook 的 stdin 载荷原始字节，**全进程只读一次**；非 Codex 门返回 None 且**不碰 stdin**。

    读完把 `sys.stdin` 换成指向同一份字节的 `TextIOWrapper(BytesIO, utf-8)`：守卫在 `main()` 首行读过
    stdin 之后，脚本自己原有的读法（`sys.stdin.buffer.read()` / `json.load(sys.stdin)` / `sys.stdin.read()` /
    `isatty()` / 在 main 里重包 `sys.stdin.buffer`）一行不改都拿到同一份字节。模块顶层已把 `sys.stdin`
    重包成自己 wrapper 的脚本，读的是那层 wrapper 底下的 buffer（它此前未被读过），同样是原始字节。

    **bytes 读 ＋ 显式 UTF-8**：文本模式在 Windows 上按本机代码页解码，含中文的 `cwd` 会变成另一串字符，
    守卫随之把真实项目判成「不在项目内」、hook 全部静默早退。
    stdin 缺失 / 是终端 / 读失败 → `b""`，不替换。
    """
    if harness(argv) != CODEX:
        return None
    if "raw" in _PAYLOAD_CACHE:
        return _PAYLOAD_CACHE["raw"]
    raw = b""
    try:
        cur = sys.stdin
        if cur is not None and not cur.isatty():
            raw = sys.stdin.buffer.read()      # 字面写 sys.stdin：hook_stdin_utf8 的 AST 判定按这个形态认读点
            import io
            bio = io.BytesIO(raw)
            wrapper = io.TextIOWrapper(bio, encoding="utf-8", errors="replace")
            _STDIN_HOLD.extend([cur, bio, wrapper])
            sys.stdin = wrapper
    except Exception:
        raw = b""
    _PAYLOAD_CACHE["raw"] = raw
    return raw


def hook_payload(argv=None):
    """`hook_payload_bytes()` 解析成 dict；非 Codex 门返回 None；空 / 不合法 / 非对象 → `{}`。"""
    raw = hook_payload_bytes(argv)
    if raw is None:
        return None
    try:
        import json
        data = json.loads(raw.decode("utf-8", errors="replace")) if raw.strip() else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def hook_outside_project(payload=None, argv=None):
    """这次 hook 是否在 workframe 项目**之外**。是 ⇒ 调用方零写入、零 stdout、exit 0 早退。

    **门条件只写在这里**：只在 `harness() == "codex"` 时判；`cc` / `unknown` 恒返回 False（在项目内）。
    Codex 的插件启用面由装机器收窄到跑过 `--codex` 的项目，但用户层被写回 `true`（例如裸敲一次
    `codex plugin add`）之后任何目录的会话都会跑本插件 hook——这道守卫是那种状态下的兜底。
    CC 门不经过它：用户在 CC 用户作用域启用 core 时的同类缺口是已登记的能力边界。要改成两门都判，
    改这一处的门条件，并给 CC 那侧补测试与恒等格。

    会话目录 = 载荷 `cwd`（非空字符串）> 进程 cwd；从会话目录向上找 `ROOT_MARKER`，找不到即「在项目外」。
    **依赖 Codex 的 hook 工作目录契约**：官方 hooks 文档（Config shape）原文 "Commands run with the session `cwd`
    as their working directory."——守卫按载荷 `cwd` 判，多数脚本却在模块顶层按**进程** cwd 找根写状态，两者相等
    才一致；不相等时（契约外）守卫放行而状态会写进进程 cwd。本机 codex-cli 0.153.4 在 SessionStart 与
    UserPromptSubmit 两个事件实测二者逐字相同；其余事件零凭据触发不了、未实测。validate 的闸恒令二者相等，
    这条轴它看不见。
    **不读 `CLAUDE_PROJECT_DIR`**（与 `project_dir()` 的 Codex 分支同一口径）。
    `payload` 给 dict 时直接用它（在模块顶层已读过 stdin 的脚本传自己那份），否则经 `hook_payload()` 读。

    **只许在 `main()` 首行调用，不许放在模块顶层**：装机器与 validate / 单测会 import 这些脚本，import 期
    退出会误伤调用方。**能力边界**：向上找没有上限——祖先目录（例如家目录）带标记时，其下所有目录都判
    「在项目内」。
    """
    if harness(argv) != CODEX:
        return False
    if not isinstance(payload, dict):
        payload = hook_payload(argv) or {}
    cwd = payload.get("cwd")
    start = cwd if isinstance(cwd, str) and cwd.strip() else None
    return find_root(start) is None


# ---------- 项目 skills 目录：中立目录优先，按存在性解析 ----------

SKILLS_DIR_NEUTRAL = Path(".agents") / "skills"      # 两扇门共读的中立目录（新建项目的真实源）
SKILLS_DIR_CC = Path(".claude") / "skills"           # CC 的发现路径；新建项目里它是指向中立目录的 junction / symlink


def is_dir_link(path):
    """`path` 是目录链接（POSIX symlink / Windows junction）吗——**不看目标在不在**，悬空也算。

    junction 在 3.13 的 `Path.is_symlink()` 下是 False，只能靠 `os.path.isjunction`（3.12+）或
    lstat 的 reparse 位识别。`exists()` 对悬空链接也是 False，所以「链接在不在」必须单独问。
    """
    p = Path(path)
    try:
        if p.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)      # 3.12+
        if isjunction is not None:
            return bool(isjunction(str(p)))
        st = os.lstat(p)
        return bool(getattr(st, "st_file_attributes", 0) & 0x400)   # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return False


def skills_link_present(project_dir):
    """`.claude/skills` 是目录链接吗（悬空也算）——scaffold / doctor 据此决定要不要 .gitignore 忽略它，
    `project_skills_dir()` 据此在目标缺席时仍解析到中立目录。判定只有这一份。"""
    return is_dir_link(Path(project_dir) / SKILLS_DIR_CC)


# `.claude/skills` 位置上的形态（`skills_cc_form()` 的返回值）。判定只有这一份，scaffold / SessionStart / doctor 都问它。
SKILLS_ABSENT = "absent"                              # 位置上什么都没有（`lexists` 为 False）
SKILLS_LINK_OK = "link_ok"                            # 链接，解析到本项目 `.agents/skills`
SKILLS_LINK_TARGET_MISSING = "link_target_missing"    # 链接，目标不存在，而本项目 `.agents/skills` 是目录（项目整体改名 / 剪切后的 junction）
SKILLS_LINK_NEUTRAL_MISSING = "link_neutral_missing"  # 链接，本项目 `.agents/skills` 不是目录（真实源被删）
SKILLS_LINK_ELSEWHERE = "link_elsewhere"              # 链接，目标存在但不是本项目 `.agents/skills`
SKILLS_REALDIR = "realdir"                            # 真目录（本项目 `.agents/skills` 也在 ⇒ 两份各读各的；不在 ⇒ 已装项目的正常形态）
SKILLS_FILE = "file"                                  # 普通文件（POSIX 上误提交的链接对象在 Windows clone 出来的样子）


def skills_cc_form(project_dir):
    """`.claude/skills` 此刻是哪种形态——返回上面七个常量之一。

    判「链接指向哪」用 `Path.resolve()` 比，不比 `os.readlink` 的字符串：Windows 上 junction 的读数带
    `\\\\?\\` 前缀与长名，而项目路径常以 8.3 短名传进来，字符串比会把指向本项目的链接判成「指向别处」。
    目标不存在时 `exists()` 为 False（junction 与 symlink 都跟随链接判），据此分出「目标不存在」。
    """
    root = Path(project_dir)
    cc, neutral = root / SKILLS_DIR_CC, root / SKILLS_DIR_NEUTRAL
    if not os.path.lexists(cc):
        return SKILLS_ABSENT
    if is_dir_link(cc):
        if not neutral.is_dir():
            return SKILLS_LINK_NEUTRAL_MISSING
        if not cc.exists():
            return SKILLS_LINK_TARGET_MISSING
        try:
            same = cc.resolve() == neutral.resolve()
        except (OSError, RuntimeError):     # 3.11 及以下 `resolve()` 对符号链接环抛 RuntimeError（3.13 起是 OSError）
            same = False
        return SKILLS_LINK_OK if same else SKILLS_LINK_ELSEWHERE
    if cc.is_dir():
        return SKILLS_REALDIR
    return SKILLS_FILE


def skills_link_dangling(project_dir):
    """`.claude/skills` 是链接、而 `.agents/skills/` 此刻不是目录：上次装机留下的链接失去了真实源。
    scaffold 对这一形态不建、不删、报出来（对悬空链接 `mkdir(exist_ok=True)` 会抛 FileExistsError）。
    **不含**「目标不存在而本项目 `.agents/skills` 在」那一态（`SKILLS_LINK_TARGET_MISSING`，SessionStart 自动重指）。"""
    return skills_cc_form(project_dir) == SKILLS_LINK_NEUTRAL_MISSING


def project_skills_dir(project_dir):
    """项目 skills 目录（项目根下的相对 `Path`，调用方按需拼绝对路径）：
    `.agents/skills/` 存在则用它，否则 `.claude/skills/`。

    **按存在性解析，不按 harness 解析**：已装项目的 skills 是 `.claude/skills/` 真目录，
    按门硬解析会让它们在 Codex 门下找不到 prd-style；新建项目把真实源放在中立目录、
    `.claude/skills` 只是指向它的链接，两种形态用同一条规则都解析到真实源。
    两者都在且不是同一目录时仍返回中立目录（`project_skills_conflict()` 另给一句说明，
    由调用方决定要不要报）。两者都不在时返回 `.claude/skills`——那是「尚未装机」，
    由 scaffold 决定落在哪。

    **链接在而目标不在（悬空）也返回中立目录**——链接本身就说明真实源的位置是 `.agents/skills/`，
    此刻不在只是目标缺席；与 `.gitignore` 那一侧「链接在就忽略 `.claude/skills`」同一口径
    （`skills_link_present`）。返回 `.claude/skills` 会让调用方往悬空链接底下写文件而当场失败；
    返回中立目录则谁先在那里落文件、链接就随之恢复。
    """
    root = Path(project_dir)
    if (root / SKILLS_DIR_NEUTRAL).is_dir() or skills_link_present(root):
        return SKILLS_DIR_NEUTRAL
    return SKILLS_DIR_CC


def skills_two_copies(project_dir):
    """「`.claude/skills` 与本项目 `.agents/skills` 是两份不同的目录」——**判定只有这一份**，
    `project_skills_conflict()`、SessionStart 的每会话提示、doctor `skills_link` 都问它。

    返回 `skills_cc_form()` 的形态（`SKILLS_LINK_ELSEWHERE`：链接指向别处；`SKILLS_REALDIR`：真目录且本项目
    `.agents/skills` 也在、两者不是同一目录——复制出来的副本 / 空壳），否则 None。
    **反向链接**（`.claude/skills` 是真目录、`.agents/skills` 是指向它的链接）是同一份，返回 None：已装项目
    为让 Codex 读到项目 skill 最自然的做法，改前也不报；把它判成副本会让提示把人送去删掉唯一的那份真目录。
    """
    root = Path(project_dir)
    form = skills_cc_form(root)
    if form == SKILLS_LINK_ELSEWHERE:
        return form
    if form == SKILLS_REALDIR and (root / SKILLS_DIR_NEUTRAL).is_dir():
        try:
            if (root / SKILLS_DIR_NEUTRAL).resolve() == (root / SKILLS_DIR_CC).resolve():
                return None
        except OSError:
            pass
        return form
    return None


def project_skills_conflict(project_dir):
    """两份不是同一目录（`skills_two_copies()` 非 None）时返回一句说明；否则返回 None。"""
    if skills_two_copies(project_dir) is None:
        return None
    return (f"{SKILLS_DIR_NEUTRAL.as_posix()}/ 与 {SKILLS_DIR_CC.as_posix()}/ 是两个不同的目录——"
            f"两扇门各读一份：Claude Code 读 {SKILLS_DIR_CC.as_posix()}，框架脚本与 Codex 读 "
            f"{SKILLS_DIR_NEUTRAL.as_posix()}，内容会分叉")


def plugin_root_candidates():
    """插件根的候选，按可信度排序；调用方**逐个试**，不要只认第一个。

    `CLAUDE_PLUGIN_ROOT` 在某些 CC 版本 / 上下文不可用，Codex 侧只在插件源注入，
    所以 `__file__` 推导的那条必须常在（本文件位于 `<plugin_root>/scripts/`）。
    """
    out = []
    env = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        out.append(Path(env))
    out.append(Path(__file__).resolve().parents[1])
    return out


def plugin_root():
    """首选插件根。要遍历全部候选用 `plugin_root_candidates()`。"""
    return plugin_root_candidates()[0]


def entrypoint():
    """CC 的 `CLAUDE_CODE_ENTRYPOINT`（交互 / headless 判据）；Codex 侧恒为空串。

    收在这里而不是让 `memory-ask.py` 直读，理由与其余三个变量同：Codex 下取不到值时
    走的是「按交互处理」那条兜底分支，而那正是这个变量本该拦住的场景。
    """
    return os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")


# ---------- 「是不是在普通终端里跑」：只用于给出提示，不作拒绝判据 ----------

# AI 工具进程里才有的环境标记，**精确变量名**，不按前缀取：用户常驻环境里就有 `CODEX_HOME`、
# `CLAUDE_CODE_GIT_BASH_PATH`、`WT_SESSION`，按前缀取会在用户自己的终端里误报——这三个明确不算。
#   CC：Bash 工具子进程与 `!` 前缀命令里都实测在场（两者在这张表上不可分）。
#   Codex：取自 codex-cli 源码（0.153.4：`unified_exec` 给命令环境无条件插 `CODEX_THREAD_ID` /
#   `CODEX_SESSION_ID`；网络沙箱关时插 `CODEX_SANDBOX_NETWORK_DISABLED`、macOS seatbelt 下插
#   `CODEX_SANDBOX` 这两条只对过主干源码），**未在真实 Codex 工具调用里实测**；Codex 的用户 `!` 命令
#   同样带 `CODEX_THREAD_ID`。
AGENT_ENV_MARKERS = (
    "CLAUDECODE", "AI_AGENT", "CLAUDE_CODE_CHILD_SESSION",
    "CODEX_THREAD_ID", "CODEX_SESSION_ID", "CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED",
)


def stdin_is_console():
    """标准输入是不是一个真控制台。

    Windows 判 `GetConsoleMode(stdin)` 是否成功：AI 工具进程的 stdin 是 NUL 设备，`isatty()` 对它
    返回 True、`CONIN$` 也打得开，只有 `GetConsoleMode` 失败（实测），所以那两个都不能作判据。
    POSIX 判 `isatty()`。Git Bash（mintty）下 stdin 是管道，按本判据判「不是控制台」。
    """
    if sys.stdin is None:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.GetStdHandle.restype = wintypes.HANDLE
            handle = k32.GetStdHandle(-10)          # STD_INPUT_HANDLE
            mode = wintypes.DWORD()
            return bool(k32.GetConsoleMode(handle, ctypes.byref(mode)))
        except Exception:
            return False
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def not_plain_terminal_reasons():
    """当前进程**可能不是用户在普通终端里亲手跑**的依据清单；空表 = 看起来是普通终端。

    **只能用来提示，不能用来拒绝**：CC 的 `!` 前缀命令与 AI 工具调用在控制台判据与标记表上都
    分不开（实测），而用户选择允许 `!` 前缀执行；反过来环境变量可以被模型在命令行里改写，
    控制台也可以被刻意伪造。所以本函数挡不住任何东西，它只让「不在普通终端里」这件事被看见。
    """
    reasons = []
    if not stdin_is_console():
        reasons.append("标准输入不是控制台" + ("（GetConsoleMode 失败）" if os.name == "nt" else "（非 tty）"))
    hits = [name for name in AGENT_ENV_MARKERS if name in os.environ]
    if hits:
        reasons.append("环境里有 AI 工具的标记变量：" + "、".join(hits))
    return reasons


def harness(argv=None):
    """当前是哪扇门：`cc` / `codex` / `unknown`。判据是**命令行 `--harness`**。

    **参数而不是探测**：两份 hook 表本来就是分开的，各自把自己的门写进命令行，确定性
    优于探测。探测法在嵌套启动（在 CC 的 Bash 里跑 `codex exec`）时 `CLAUDE_*` 会泄漏
    进 Codex 的 hook 环境、恒判 `cc`；而 `CODEX_HOME` 默认不设，反向也判不出来。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    for i, a in enumerate(args):
        if a == "--harness" and i + 1 < len(args):
            return _norm(args[i + 1])
        if a.startswith("--harness="):
            return _norm(a.split("=", 1)[1])
    return detect()


def _norm(value):
    v = (value or "").strip().lower()
    return v if v in HARNESSES else UNKNOWN


def detect():
    """无参兜底：**恒为 `unknown`**，不做环境探测。

    这不是「还没实现」——是 `harness()` docstring 里那条判断的结论：能探到的信号
    （`CLAUDE_*` 泄漏 / `CODEX_HOME` 缺省）在嵌套启动下会给出**确信而错误**的答案，
    比「不知道」更坏。没带 `--harness` 就如实说不知道，消费方按缺省口径处理。
    """
    return UNKNOWN


def strip_harness(args):
    """把 `--harness <v>` / `--harness=<v>` 从参数表里剥掉，返回其余参数。

    **手写 argv 分派的脚本必须用它。** hooks.json 给每条命令都带了 `--harness`，而
    `check-stale-modules.py` 的 PostToolUse 那条**没有子命令**——不剥掉的话 `args[0]`
    变成 `--harness`，落进「用法错误」分支并 `sys.exit(2)`，按 CC 官方语义那是
    **阻断 PostToolUse**（本仓实测）。值本身仍由 `harness()` 直接从 `sys.argv` 读，
    与剥不剥无关。

    `--harness` 在末尾缺值时连同它一起吃掉（`i += 2` 越界即结束循环），不抛。
    """
    args = list(args)
    out, i = [], 0
    while i < len(args):
        a = args[i]
        if a == "--harness":
            i += 2
            continue
        if a.startswith("--harness="):
            i += 1
            continue
        out.append(a)
        i += 1
    return out


# ---------- hook stdout 的输出形态：纯文本 vs hookSpecificOutput JSON ----------

OUTPUT_HOOK_JSON = "hook-json"
OUTPUT_TEXT = "text"


def output_mode(argv=None):
    """hook 的 stdout 形态：`--output hook-json` ⇒ `hook-json`，否则 `text`。

    **为什么有这个开关**：Codex 对 hook 的 stdout 先去空白看首字符，`[` 或 `{` 开头就按
    JSON 对象解析、解析不到期望形态即**整条丢弃**并记 `status: failed`（实测）；而本仓三个
    纯文本 SessionStart 脚本的输出都以 `[<项目名>] …` / `[workframe] …` / `[modules] …` 标签行
    起头——CC 下照常进上下文，Codex 下一个字都到不了模型。CC 那份清单**不带**本参数，
    所以 CC 的 stdout 逐字节不变；只有 Codex 清单给这三条命令带上它（派生规则见
    `_codex_hooks.py`）。判据与 `harness()` 同款：读命令行，不探测环境。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    for i, a in enumerate(args):
        if a == "--output" and i + 1 < len(args):
            return OUTPUT_HOOK_JSON if args[i + 1].strip().lower() == OUTPUT_HOOK_JSON else OUTPUT_TEXT
        if a.startswith("--output="):
            return OUTPUT_HOOK_JSON if a.split("=", 1)[1].strip().lower() == OUTPUT_HOOK_JSON else OUTPUT_TEXT
    return OUTPUT_TEXT


_SKILLS_RELOAD = {"requested": False}


def request_skills_reload():
    """本次 SessionStart 改变了 `.claude/skills` 的解析目标（补建了链接 / 重指了悬空链接）——请 CC 门在 hook
    结束后重扫 skill 清单。只在 `hook_json_stdout()` 已接管 stdout 的进程里有效，由它在退出时决定投什么。"""
    _SKILLS_RELOAD["requested"] = True


def render_hook_stdout(text, event, mode, harness_name, reload_skills, dumps=None):
    """进程退出时 stdout 该写出的整串（`None` = 什么都不写）。纯函数，`hook_json_stdout()` 的唯一决策点。

    - `hook-json`（Codex 清单）：缓冲非空 ⇒ `{"hookSpecificOutput": {hookEventName, additionalContext}}`，
      **不加 `reloadSkills`**（Codex 对未知键的容忍没人验过，而 Codex 本就直接读 `.agents/skills`）；空 ⇒ 不写。
    - 纯文本且 CC 门且 `reload_skills` ⇒ 同形 JSON，`hookSpecificOutput` 里多一个 `reloadSkills: true`
      （CC 官方：SessionStart 可要求 hook 结束后重扫 skill；skill 发现通常在 SessionStart hook 跑完之前）。
      **写出前自检**：整串 `json.loads` 一次、结构对得上才投；否则退回纯文本——退回只是「下一次会话才可见」，
      而以 `{` 开头却解析失败的 stdout 会让这一整段启动上下文消失。
    - 其余 ⇒ 原样写回缓冲（与不接管 stdout 时逐字节相同；空缓冲不写）。
    """
    import json
    dumps = dumps or (lambda obj: json.dumps(obj, ensure_ascii=False))
    if mode == OUTPUT_HOOK_JSON:
        if not text.strip():
            return None
        return dumps({"hookSpecificOutput": {
            "hookEventName": event, "additionalContext": text.rstrip("\n"),
        }}) + "\n"
    if reload_skills and harness_name == CC and text.strip():
        ctx = text.rstrip("\n")
        try:
            out = dumps({"hookSpecificOutput": {
                "hookEventName": event, "additionalContext": ctx, "reloadSkills": True,
            }})
            back = json.loads(out)
            hso = back["hookSpecificOutput"]
            if (out.lstrip().startswith("{") and hso.get("hookEventName") == event
                    and hso.get("additionalContext") == ctx and hso.get("reloadSkills") is True):
                return out + "\n"
        except Exception:
            pass
    return text or None


def hook_json_stdout(event, argv=None):
    """把本进程之后写到 stdout 的一切收进缓冲，进程退出时按 `render_hook_stdout()` 一次性写出——两扇门共用这一套缓冲：
    `--output hook-json`（Codex 清单）⇒ 投 `hookSpecificOutput` JSON；纯文本（CC 清单）⇒ 原样写回，
    只有本进程调过 `request_skills_reload()` 时才改投带 `reloadSkills` 的 JSON。决策放在退出时，是因为
    要不要重扫 skill 在打印了一部分输出之后才知道，而 JSON 必须是整次 stdout 的唯一内容。

    **只替换 `sys.stdout` 这一个对象、并且持住被替换的那个**：本仓每个脚本在 import 期
    都把 `sys.stdout` 换成了一层 `TextIOWrapper`，若把它丢掉，它被 GC 时会关掉共用的底层
    buffer，之后任何写入都是 `I/O operation on closed file`（本仓实测踩过）。所以旧对象
    由闭包持有，退出时用它写出。走 `atexit`：脚本里有在半路 `sys.exit(0)` 的，
    `SystemExit` 同样触发 atexit。stderr 不动（纯文本模式下 stdout 因此不再与 stderr 交错，字节不变）。
    """
    import atexit
    import io
    mode = output_mode(argv)
    door = harness(argv)
    real = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf

    def _flush():
        text = buf.getvalue()
        sys.stdout = real
        try:
            out = render_hook_stdout(text, event, mode, door, _SKILLS_RELOAD["requested"])
        except Exception:
            out = text if mode != OUTPUT_HOOK_JSON else None
        try:
            if out:
                real.write(out)
                real.flush()
        except Exception:
            pass
    atexit.register(_flush)


def add_harness_arg(parser):
    """给 argparse 脚本挂上 `--harness`，只为**不报错**，不为取值。

    argparse 的 `parse_args()` 对未知参数是 **exit 2**：不挂这一个参数，走 argparse
    的 hook 脚本在 hooks.json 带上 `--harness` 后会整条失效（退役前的镜像同步脚本上实测过）。

    **不设 `choices`** 是有意的：设了之后一个拼错的门名会让脚本 exit 2、hook 当场死，
    而不走 argparse 的那十几个脚本对同一个拼错值只是退成 `unknown`。同一个输入在两
    类脚本上给出两种结局，比「都退成 unknown」更难排查——归一交给 `harness()`。
    """
    parser.add_argument(
        "--harness", default=None,
        help="当前是哪扇门（cc / codex）；由 hook 表写入，人工调用可省",
    )
    return parser


# ---------- 事件注册表：哪类事件在哪扇门上产得出来 ----------

_SCHEMA_CACHE = {}


def event_schema():
    """`.workframe-meta/event-schema.json` 的内容；读不出来返回 `{}`（调用方按「不校验」处理）。

    **按 `__file__` 解析，不走 `plugin_root()`**：后者优先认 `CLAUDE_PLUGIN_ROOT`，而
    那个变量可能指向**另一份**安装（插件根带版本号、升级即变）。本函数要回答的是
    「**正在执行的这份代码**配套的注册表长什么样」——跨安装读会给出确信而错误的答案。
    这一条不是理论风险：`plugin-root.txt` 被两扇门轮流覆盖写各自安装缓存的根，一边
    升级一边没升时就会踩到。

    读盘发生在**调用时**，不在 import 期（本模块的 import 无副作用约定）。
    """
    if "data" not in _SCHEMA_CACHE:
        import json
        path = (Path(__file__).resolve().parents[1]
                / ".workframe-meta" / "event-schema.json")
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}
        _SCHEMA_CACHE["data"] = data if isinstance(data, dict) else {}
    return _SCHEMA_CACHE["data"]


def registered_event_types():
    """注册表里的全部事件类型；读不到返回空集（调用方据此**跳过**校验，不误伤）。"""
    return set((event_schema().get("events") or {}).keys())


def event_availability(ev_type):
    """某事件类型的 `availability` 段（`{"cc": …, "codex": …, "note": …}`）；缺失返回 `{}`。"""
    spec = (event_schema().get("events") or {}).get(ev_type)
    avail = spec.get("availability") if isinstance(spec, dict) else None
    return dict(avail) if isinstance(avail, dict) else {}


def events_by_availability(harness_name, status):
    """在 `harness_name` 这扇门上可得性等于 `status` 的事件类型，排序返回。"""
    out = []
    for ev_type, spec in (event_schema().get("events") or {}).items():
        avail = spec.get("availability") if isinstance(spec, dict) else None
        if isinstance(avail, dict) and avail.get(harness_name) == status:
            out.append(ev_type)
    return sorted(out)


def harness_breakdown(events):
    """一批事件按门分列 + 各门的结构性缺口。**消费方报告首行的唯一实现。**

    返回 `{"counts", "cross_door_gaps", "neither_door", "partial", "unverified",
    "full_but_delivery_unverified"}`。

    - `counts`：按事件行的 `harness` 字段分桶。**没有该字段的行归 `unstamped`，
      不并进 `cc`**——schema 说缺省读作 `cc`，但那是「怎么解释」，不是「数出来是几」；
      合并会让「存量行有多少」这个可观测量当场消失，而它正是判断改造铺开到哪一步的依据。
    - **`cross_door_gaps` 与 `neither_door` 是分开的两张表，不许合成一张**：
      前者是「对门产得出、本门产不出」——真正的跨门缺口，读者要据此把本门的零计数
      当结构性缺失；后者是「两扇门都没有 producer」（如 `rule_triggered`），它跟门
      无关，混进前者会让**跨门缺口数被读大**。这不是假想：合并成一张 `gaps` 时，
      Codex 那格是四项，而真实跨门缺口是三项，第四项 `rule_triggered` 两侧皆无——
      消费方按要求逐字照抄进报告首行，读者据此把缺口记成 4。
      `neither_door` 因此是**一张表而不是按门分**：两侧皆无本就与门无关，按门分会
      把同一份清单印两遍、又给人一种「每扇门各缺一批」的错觉。
    - **`full_but_delivery_unverified`**：模型侧事件（`protocol_expected` / `model_mediated`）
      判 `full` 是因为「模型写一行」这个动作本身与门无关；但**「模型会不会被告知去写」**
      是另一条链，那条链在某扇门上没接好时，该门的零计数同样是结构性的。这一格因此不
      降档、而是单列一张表——降档会把「机制可能不存在」和「机制存在但还没接线」混成一档
      （两者的排查方向完全不同），单列则两个事实都留住。
      门的投递状态取自 schema 的 `availability_values.__delivery_established__`，**现算**；
      哪天某扇门接上了，把那个布尔改掉，这张表自己就空了。
    - 四张表都现算于 `availability`，**清单只此一处**。别在报告模板 / skill 正文里
      另抄一份——抄一份就会在下次改 schema 时变成假话。这一条对本函数自己同样成立：
      投递那句 caveat 曾经写在 audit 的散文里，那就是一份会漂的手工副本。
    """
    counts = {}
    for ev in events:
        h = ev.get("harness") if isinstance(ev, dict) else None
        if not isinstance(h, str) or not h.strip():
            h = "unstamped"
        counts[h] = counts.get(h, 0) + 1

    cross, neither = {h: [] for h in HARNESSES}, []
    for ev_type, spec in sorted((event_schema().get("events") or {}).items()):
        avail = spec.get("availability") if isinstance(spec, dict) else None
        if not isinstance(avail, dict):
            continue
        dead = [h for h in HARNESSES if avail.get(h) == "none"]
        if not dead:
            continue
        if len(dead) == len(HARNESSES):
            neither.append(ev_type)      # 与门无关，不进任何一门的缺口表
        else:
            for h in dead:
                cross[h].append(ev_type)
    # 模型侧事件 × 投递未确立的门。两个输入都从 schema 现读，本函数不自带名单。
    values = event_schema().get("availability_values") or {}
    delivered = values.get("__delivery_established__") or {}
    model_side = sorted(
        ev_type for ev_type, spec in (event_schema().get("events") or {}).items()
        if isinstance(spec, dict)
        and spec.get("reliability") in ("model_mediated", "protocol_expected")
        and (spec.get("availability") or {}).get("codex") == "full"
    )
    delivery = {h: (list(model_side) if delivered.get(h) is False else [])
                for h in HARNESSES}

    return {
        "counts": counts,
        "cross_door_gaps": cross,
        "neither_door": neither,
        "partial": {h: events_by_availability(h, "partial") for h in HARNESSES},
        "unverified": {h: events_by_availability(h, "unknown") for h in HARNESSES},
        "full_but_delivery_unverified": delivery,
    }
