#!/usr/bin/env python3
"""运行态状态的单一事实源：**目录位置**、锁、原子替换、损坏隔离、追加写只此一份实现。

三件事收在这里，各自都有「散着写必漂」的实证：
  - **目录位置**（`runtime_rel` / `state_dir_of` / `memory_dir_of` / `archive_dir_of`）
  - **读改写**（`load_activity` / `save_activity` / `update_activity`）
  - **追加写**（`append_line` / `merge_spills` / `probe_appendable`）


为什么单独成模块：`activity-state.json` 有四个 hook 在读改写，
四份 load 各自实现了**四种不同**的损坏降级——退出 1 / 恢复三字段 / 恢复完整默认并顺手
裁掉未知键 / 返回空字典。空字典写回就把 `session_counter` 与 `pending_maintenance` 抹平了；
同一份坏文件在不同 hook 手里结局不同，排查时无从复现。写入侧同样是四份非原子的
`write_text`，中途断电或并发写会留下半截 JSON。

锁与原子写本来就在 `check-stale-modules.py` 里跑了很久（含 Windows `msvcrt.locking`
需 `seek(0)` 才能对齐 POSIX whole-file 语义这类踩坑细节），本模块是**提取共用**而非新造。

被 hook 以同目录 import 方式使用，因此：
  - 模块 import 必须无副作用（不碰 stdout、不读环境、不建目录）。**唯一的例外**是把
    自己所在目录塞进 `sys.path`——本模块有一个同目录依赖（`_harness`，事件行要盖的
    `harness` 从那儿取），而调用方里有一路是 `spec_from_file_location` 直接按文件路径
    加载本模块的（`tools/validate.py` 的 `_state_io_module()`），那条路径下同目录不在
    `sys.path` 里，兄弟模块 import 不进来。同包脚本（`check-stale-modules.py` 等）用的
    是同一条惯例。
  - 文件名用下划线而非连字符——连字符的模块名 import 不进来
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402  —— 同目录兄弟模块；`_harness` 不 import 本模块，无循环

# 跨平台文件锁的底层依赖
if os.name == "nt":
    import msvcrt
else:
    import fcntl

LOCK_TIMEOUT_SEC = 5.0

ACTIVITY_FILE_NAME = "activity-state.json"
ACTIVITY_SCHEMA = "workframe.activity-state.v1"

# activity-state 的字段全集与出厂值。**这是唯一事实源**——此前它只长在
# session-start-prep.py 里，而那份 load 会把不在此表中的键裁掉，于是任何 hook 想加新字段
# 都必须同步改另一个文件的常量，否则字段被静默丢弃：一条既无文档也无机器闸的隐性契约。
# 现在默认值只用于补缺，不用于裁剪（见 load_activity）。
ACTIVITY_DEFAULTS = {
    "__schema__": ACTIVITY_SCHEMA,
    "session_counter": 0,
    "last_session_at": None,
    "active_sessions_30": 0,
    "weighted_events_since_last_iteration": 0.0,  # deprecated: trigger 实时重算
    "dormant": False,
    "wake_up_pending": False,
    "dormant_profile": "normal",
    "last_digest_at": None,
    "last_prompt_injected_session_id": None,
    "pending_maintenance": [],
    "recent_drift_repairs": [],
}


class StateUnavailable(Exception):
    """状态文件**存在但读不出来**（权限 / 路径是目录 / 被独占）。

    写路径必须靠它 fail-closed：读失败与「文件不存在」不是一回事，前者把默认值写回去
    等于拿空状态覆盖掉一份读不动但可能完好的文件。
    """


class FileLock:
    """跨平台 advisory file lock with timeout.

    POSIX: fcntl.flock + LOCK_NB 轮询
    Windows: msvcrt.locking + LK_NBLCK 非阻塞 + 自旋超时
    """

    def __init__(self, path, timeout=LOCK_TIMEOUT_SEC):
        self.path = Path(path)
        self.timeout = timeout
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+b")
        # Windows msvcrt.locking 锁定的是当前文件指针处的字节；"a+b" 模式下指针在 EOF
        # 显式 seek(0) 保证锁的是 byte 0，与 POSIX advisory whole-file 语义对齐
        self.fh.seek(0)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == "nt":
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except (OSError, BlockingIOError):
                if time.monotonic() > deadline:
                    self.fh.close()
                    self.fh = None
                    raise TimeoutError(f"file lock {self.path} timeout {self.timeout}s")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.fh is not None:
            try:
                if os.name == "nt":
                    self.fh.seek(0)
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            self.fh.close()
            self.fh = None


def atomic_write(path, content):
    """write `.tmp` → os.replace → atomic.

    跨平台：os.replace 在 Windows 与 POSIX 下都是原子替换。

    `newline=""` 不能省：Python 文本模式默认 `newline=None`，在 Windows 上把每个 `\n`
    翻译成 `\r\n`。这些 state 文件里有一部分是**进 git 的**（memory-index.json），
    于是 hook 写一次，整个文件从 LF 变 CRLF——git 判定每一行都改了，真实改动
    （通常只有一两个字段）被彻底淹没。实测：一次只改 1 个字段的写入，产出
    349 insertions / 349 deletions 的 diff。同样的坑也会让任何按内容 hash 对账的
    检查（按内容 hash 对账的那些）恒报不一致。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content, encoding="utf-8", newline="")
    os.replace(str(tmp), str(path))


SPILL_SUFFIX = ".spill.jsonl"
MERGED_SUFFIX = ".merged"


def event_json(event, harness=None):
    """事件行的**唯一**序列化点：盖上 `harness` 后 dump 成一行 JSON。全框架只此一份实现。

    为什么必须收成一处：`harness` 要出现在**每一条** hook 产出的事件上，而事件字典
    今天由九个 producer 各自拼装。九处各加一行「顺手补个字段」，下一个新增的 producer
    必然漏——漏了不会报错，只是那条事件在两门混合的 events.jsonl 里**按缺省被读成 cc**，
    一条来自 Codex 的事件就此挂在 CC 名下，且没有任何一处看得出来。
    `event_line_serialization_single_source` 钉住「调 append_line / append_lines 时不得
    内联 `json.dumps`」，把这条从纪律变成闸。

    取值优先级：显式传参 > 事件字典里已有的 `harness` > `_harness.harness()`（读 argv 的
    `--harness`，没带就是 `unknown`）。**`unknown` 是显式写进去的，不省略**——省略在
    schema 里等于宣称 `cc`（存量兼容口径），把「不知道」写成「确定是 CC」是更坏的假话。

    **能力边界**：本函数不校验 `event` 里的其余字段，也不管 `ts`——ts 的口径归各
    producer 与 `event_ts_and_reason_contract`，这里只碰 `harness` 这一个键。
    """
    record = dict(event)
    record.setdefault(
        "harness", harness if harness is not None else _harness.harness()
    )
    return json.dumps(record, ensure_ascii=False)


def event_lines(events, harness=None):
    """一批事件的序列化，逐条走 `event_json`；配 `append_lines` 用（同生共死那批）。"""
    return [event_json(ev, harness=harness) for ev in events]


def append_line(path, line):
    """追加**一行**。多行一次性写（要求它们同生共死）用 `append_lines`。"""
    return append_lines(path, [line])


def append_lines(path, lines):
    """向 append-only 文件（events.jsonl 等）追加若干**整行**；并发安全。全框架只此一份实现。

    返回 True=写进主文件 / False=锁抢不到，已落 spill / None=连 spill 都没落下。
    **任何情况都不抛**——调用方全是「写不进去也不能拦住用户」的 hook。
    一次调用里的多行在**同一把锁内**一次写完，不会被别的进程插进中间。

    为什么必须加锁：Windows CRT 的 `O_APPEND` 是「先 seek 到末尾、再 write」两步，
    不是内核级原子追加。两进程各追加 20,000 行的实测——每行重开追加句柄丢 10.5%，
    `O_APPEND` + 单次原始 write 丢 4.9%，加锁 0 丢。
    **暴露面比「两个 CLI 同开」宽得多**：单会话单事件内多条 hook 就已经并行。

    丢的形态比丢的条数更要紧：**真实事件行长不等，覆盖会在文件里留半行**，严格解析的
    消费方读到那一行直接抛异常。等长载荷时后写恰好盖满前写、总能拼回整行——所以
    「坏行 0」若来自等长载荷，那是载荷设计的产物，不是结论。

    锁超时**不静默丢弃**：落 `<stem>.<pid>.spill.jsonl`，下次 SessionStart 由
    `merge_spills()` 并回。理由是 stderr 不是可靠通道——CC 只在特定退出码时把它交给
    模型，Codex 侧根本无人看；写进 stderr 的「本行已丢」与什么都没发生同形。
    """
    path = Path(path)
    payload = "".join(ln if ln.endswith("\n") else ln + "\n" for ln in lines)
    if not payload:
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # 目录建不出来时下面的 open 会失败，统一走 spill 分支
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        with FileLock(lock_path):
            # 上一行没写完（历史遗留的半行 / 别的工具截断过）就先补一个换行，别把新记录
            # 接到残行尾巴上——那会让**两条**记录一起解析失败。判定放在锁内，
            # 否则「读末字节」与「追加」之间还是有窗口。
            with path.open("a", encoding="utf-8", newline="") as f:
                f.write(_newline_prefix(path) + payload)
        return True
    except TimeoutError:
        reason = f"锁超时（{LOCK_TIMEOUT_SEC}s）"
    except OSError as e:
        reason = f"写入失败（{type(e).__name__}: {e}）"
    except Exception as e:  # noqa: BLE001 —— hook 永不因记账失败非零退出
        reason = f"未预期错误（{type(e).__name__}: {e}）"
    return _spill(path, payload, reason)


def _newline_prefix(path):
    """文件已存在且最后一个字节不是换行 → 返回一个换行，否则空串。"""
    try:
        if path.exists() and path.stat().st_size:
            with path.open("rb") as fh:
                fh.seek(-1, os.SEEK_END)
                if fh.read(1) != b"\n":
                    return "\n"
    except OSError:
        pass
    return ""


def _spill(path, line, reason):
    """主文件写不进去时的落盘旁路。pid 进文件名，保证多进程各写各的、互不再抢锁。"""
    spill = path.with_name(f"{path.stem}.{os.getpid()}{SPILL_SUFFIX}")
    try:
        with spill.open("a", encoding="utf-8", newline="") as f:
            f.write(line)
        print(f"[warn] {path.name} {reason}，本行落 {spill.name}，下次 SessionStart 并回。",
              file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"[warn] {path.name} {reason}，且 spill 落盘也失败"
              f"（{type(e).__name__}: {e}）——本行丢失。", file=sys.stderr)
        return None


def merge_spills(path):
    """把 `<stem>.<pid>.spill.jsonl` 并回主文件，返回并回的行数。

    **并回后不删 spill**，改名为 `<name>.merged.<ts>` 留档：删除权不给代码同样成立，
    而留着原名会在下次会话被重复并回、把事件翻倍。改名两者都躲开。
    改名失败时**不写主文件**（宁可这一轮不并，也不要并两遍）。
    """
    path = Path(path)
    parent = path.parent
    if not parent.is_dir():
        return 0
    merged = 0
    for spill in sorted(parent.glob(f"{path.stem}.*{SPILL_SUFFIX}")):
        try:
            lines = [ln for ln in spill.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            continue
        stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
        dest, n = spill.with_name(f"{spill.name}{MERGED_SUFFIX}.{stamp}"), 2
        while dest.exists():
            dest = spill.with_name(f"{spill.name}{MERGED_SUFFIX}.{stamp}-{n}")
            n += 1
        try:
            spill.replace(dest)          # 先摘牌再并回：并到一半崩了也不会并第二遍
        except OSError:
            continue
        for ln in lines:
            if append_line(path, ln) is True:
                merged += 1
    return merged


def probe_appendable(path):
    """探一次「这个文件现在能不能追加」，**不写任何内容**；返回 (ok, detail)。

    跨账本写入的前置闸用它：sidecar / activity-state 与 events 是三本互相印证的账，
    先探可写性能把「写不进去」挡在改账之前——比事后回滚简单也更可靠。
    与 `append_line` 同在本模块，是因为「怎么碰 events.jsonl」是同一件事，
    分开写迟早漂（`events_append_single_source` 把两者一起圈进扫描面）。
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline=""):
            pass
        return True, None
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def quarantine(path):
    """把损坏文件挪成 `<name>.corrupt.<ts>`，返回新路径；失败返回 None。

    为什么隔离而不是就地覆盖：这些文件里装的是攒出来的运行状态（会话计数、待办维护项），
    直接用默认值盖掉等于静默抹掉历史，事后无从追查。留一份副本，代价只是一个文件。
    """
    path = Path(path)
    try:
        base = path.with_suffix(path.suffix + f".corrupt.{datetime.now():%Y%m%d-%H%M%S}")
        dest, n = base, 2
        while dest.exists():  # 时间戳只到秒，同一秒内连坏两次不能让后一份盖掉前一份
            dest = Path(f"{base}-{n}")
            n += 1
        path.replace(dest)
        return dest
    except Exception:
        return None


def read_json_object(path, default, *, isolate_corrupt=True, strict_read=False):
    """读一份 JSON 对象；不存在返回 default 副本，损坏则隔离原文件后返回 default 副本。

    「损坏」含两种：解析失败，以及解析出来不是 object（`[]` / `42` / `"x"` 都算）——
    后者此前会被当成合法值往下传，直到某处 `.get()` 抛异常才暴露。
    读取用 utf-8-sig：Windows 记事本与 PowerShell 5.1 的 `Out-File -Encoding utf8`
    都会带 BOM，纯 utf-8 解析会当场报「Unexpected UTF-8 BOM」。
    """
    path = Path(path)
    if not path.exists():
        return dict(default)
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        # 文件真的坏了（非法字节）——与 JSON / 类型损坏同等对待，隔离留档。
        # 此前这里跟权限错误共用一个 except 直接返回默认值，下一次成功保存就把
        # 原文件覆盖了，与「损坏副本可追查」的承诺不符。
        if isolate_corrupt:
            quarantine(path)
        return dict(default)
    except OSError as e:
        # 权限 / 被占用 / 路径类型错——**文件内容本身可能是好的**，隔离（重命名）
        # 反而会破坏用户数据，也多半同样会失败。只降级读，不动文件。
        # strict_read=True 时抛给调用方：写路径不能把「读不出来」当成「空状态」，
        # 否则默认值会覆盖掉一份读不动但可能完好的文件。
        if strict_read:
            raise StateUnavailable(f"{path} 读取失败: {type(e).__name__}: {e}")
        return dict(default)
    except Exception as e:
        if strict_read:
            raise StateUnavailable(f"{path} 读取失败: {type(e).__name__}: {e}")
        return dict(default)
    try:
        data = json.loads(raw)
    except Exception:
        if isolate_corrupt:
            quarantine(path)
        return dict(default)
    if not isinstance(data, dict):
        if isolate_corrupt:
            quarantine(path)
        return dict(default)
    return data


def dump_json(data):
    """状态文件的统一序列化形态：缩进 2、不转义非 ASCII、结尾带换行。

    ensure_ascii=False 是有意的——pending_maintenance 的 details 是中文，
    转义成 \\uXXXX 后人工排查得先解码一遍。
    """
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


# ---------- 运行态目录的单一事实源 ----------
#
# **本文件是全框架唯一允许出现这些字面路径的地方。** 其余脚本一律经下面三个函数取，
# validate 的 `paths_single_source` 钉住这一条。
#
# 为什么要搬：`.claude/` 是 Claude Code 的挂载点，而 `workframe-state` / `agent-memory` /
# `memory-archive` 三样没有一处是 CC 强制的——它们只是**借住**在那儿。借住的后果是
# 换一个 harness（Codex）就无处安身。中立目录 `.workframe/` 两扇门通用。
RUNTIME_DIR = ".workframe"
LEGACY_RUNTIME_DIR = ".claude"

# kind -> (新形态相对段, 旧形态相对段)
_RUNTIME_DIRS = {
    "state":   ((RUNTIME_DIR, "state"),          (LEGACY_RUNTIME_DIR, "workframe-state")),
    "memory":  ((RUNTIME_DIR, "agent-memory"),   (LEGACY_RUNTIME_DIR, "agent-memory")),
    "archive": ((RUNTIME_DIR, "memory-archive"), (LEGACY_RUNTIME_DIR, "memory-archive")),
}


def runtime_rel(project_dir, kind):
    """该项目的运行态目录，**项目根相对 POSIX 路径**——恒为新形态，与旧目录在不在无关。

    **旧形态一律不读不写**：旧布局项目的运行态由 `workframe-door --migrate` 搬过来（含删除动作，
    apply 须用户本人执行），本函数不搬、也不回退到旧目录。没迁移就开会话，hook 会在新位置从零建一套，
    旧目录里的状态与记忆此后没有读者；doctor 安装组第一项 `legacy_state_dir` 报这一态，迁移工具对
    「新旧同时存在」判冲突不动。**要看旧目录此刻在不在的只有 doctor 与迁移工具**，它们用 `legacy_rel` /
    `legacy_present`，不经本函数。

    保留 `project_dir` 参数：`state_dir_of` 一族拿它拼绝对路径，调用方一律经这一个入口取位置。
    """
    return new_rel(kind)


def new_rel(kind):
    """新形态（出厂形态）的项目根相对路径，与项目现状无关。

    给**构建期**的闸与模板对账用：它们要判的是「出厂默认写对没有」，不是某个项目
    此刻在用哪个目录。运行期一律用 `runtime_rel`。
    """
    return "/".join(_RUNTIME_DIRS[kind][0])


def legacy_rel(kind):
    """旧形态的项目根相对路径——只给「有没有残留」这类检查与迁移工具用，运行期读写一律不用它。"""
    return "/".join(_RUNTIME_DIRS[kind][1])


def legacy_present(project_dir, kind):
    """旧形态路径此刻在不在（`os.path.lexists`：同名文件与悬空链接也算，与迁移工具判冲突同口径）。

    只回答「路径在不在」，**不判它是不是框架的旧布局**：`memory` 类的旧位置同时是 Claude Code 官方子 agent
    `memory: project` 的落点，那层区分在 doctor `legacy_state_dir`（按框架出厂形态判），不在这里。
    """
    return os.path.lexists(Path(project_dir).joinpath(*_RUNTIME_DIRS[kind][1]))


def state_dir_of(project_dir):
    """运行态状态目录（events / activity-state / sidecar / 各类 marker）。"""
    return Path(project_dir) / runtime_rel(project_dir, "state")


def memory_dir_of(project_dir):
    """角色记忆目录（`<role>/MEMORY.md` + `notes.md` + `shared/`）。"""
    return Path(project_dir) / runtime_rel(project_dir, "memory")


def archive_dir_of(project_dir):
    """记忆归档区。

    **当前没有代码消费方**——归档动作全部由模型手工执行（auto-memory 清理判据），
    路径只出现在主会话注入片（auto-memory 清理判据）与 reference 里。放在这里是为了让日后改那些字面时
    有一个可指的单源，而不是各处再手写一遍；`paths_single_source` 同时钉住这一点。
    """
    return Path(project_dir) / runtime_rel(project_dir, "archive")


# 出厂值的权威副本是模板文件，上面的 ACTIVITY_DEFAULTS 只作兜底。
# 两者曾经漂过：模板里还有 __doc__ 与 __pending_maintenance_schema__ 两段契约说明
# （自述「随插件分发、保持可机器校验」），代码常量里没有——于是文件一旦损坏重建，
# 这两段说明就再也回不来了。以模板为准就不用在代码里再抄一份字段表。
_TEMPLATE_FILE = (Path(__file__).resolve().parent.parent
                  / "templates" / "activity-state-template.json")
_FACTORY_CACHE = None


def factory_activity_defaults():
    """出厂 activity-state：模板文件优先，读不到时退回模块常量。"""
    global _FACTORY_CACHE
    if _FACTORY_CACHE is None:
        merged = dict(ACTIVITY_DEFAULTS)
        try:
            data = json.loads(_TEMPLATE_FILE.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict) and data:
                merged.update(data)
        except Exception:
            pass
        _FACTORY_CACHE = merged
    return dict(_FACTORY_CACHE)


# load 时留一份快照，save 时用它区分「我改过的字段」和「我只是读过的字段」。
# 进程级即可——hook 都是短命进程，一轮一进一出。
_LOAD_SNAPSHOTS = {}


def load_activity(state_dir):
    """读 activity-state.json：缺失键用出厂值补齐，**未知键原样保留**。

    默认值只用来补缺、不用来裁剪——裁剪正是那条隐性契约的来源（见 ACTIVITY_DEFAULTS）。
    """
    path = Path(state_dir) / ACTIVITY_FILE_NAME
    factory = factory_activity_defaults()
    data = read_json_object(path, factory)
    merged = dict(factory)
    merged.update(data)
    merged["__schema__"] = data.get("__schema__") or ACTIVITY_SCHEMA
    try:
        _LOAD_SNAPSHOTS[str(path)] = json.loads(json.dumps(merged))
    except Exception:
        # 存不下快照不算失败：save 走「磁盘为底 + 传入值盖上」的无 base 分支，未知键仍保留
        _LOAD_SNAPSHOTS.pop(str(path), None)
    return merged


def _read_activity_nolock(path, *, strict_read=False):
    """锁内/内部用的读取：不碰 _LOAD_SNAPSHOTS。

    `strict_read=True` 用于**写前读**：读不出来就抛 StateUnavailable，让调用方放弃写入。
    """
    factory = factory_activity_defaults()
    data = read_json_object(path, factory, strict_read=strict_read)
    merged = dict(factory)
    merged.update(data)
    merged["__schema__"] = data.get("__schema__") or ACTIVITY_SCHEMA
    return merged


def update_activity(state_dir, mutator):
    """**锁内**读→改→写，返回落盘后的 state；**任何安全失败返回 None**（本次不落盘，
    具体原因已打到 stderr）——锁超时 / 状态读不出来 / 原子写失败都走这条路。

    这是唯一能保证**同键并发**正确的路径。`save_activity` 的三方合并只解决「不同键
    互相覆盖」——两个进程都把 `session_counter` 从 N 算成 N+1，合并后仍是 N+1，少一次。
    凡是「基于当前值计算」的更新（计数器递增、列表 upsert / GC）都必须走这里：
    读取与计算一起在临界区内，别的进程插不进来。

    此前只有 save 在锁内，load 和增量计算在锁外，等于没锁住真正的临界区。
    """
    path = Path(state_dir) / ACTIVITY_FILE_NAME
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        with FileLock(lock_path):
            state = _read_activity_nolock(path, strict_read=True)
            mutator(state)
            atomic_write(path, dump_json(state))
            # 落盘版本即后续 save_activity 三方合并的 base
            try:
                _LOAD_SNAPSHOTS[str(path)] = json.loads(json.dumps(state))
            except Exception:
                _LOAD_SNAPSHOTS.pop(str(path), None)
            return state
    except TimeoutError:
        print(f"[warn] activity-state 锁超时（{LOCK_TIMEOUT_SEC}s），本次状态更新未落盘；"
              f"下次会话正常写入。", file=sys.stderr)
        return None
    except StateUnavailable as e:
        # 读不出来就不写：拿默认值覆盖掉一份读不动的文件，比丢一次更新严重得多
        print(f"[warn] {e}；本次状态更新未落盘（不用默认值覆盖）。", file=sys.stderr)
        return None
    except OSError as e:
        # 写入侧失败（目标是目录 / 磁盘满 / 锁文件建不出来）。hook 不该因此非零退出——
        # 此前这里没有兜底，把 activity-state.json 换成目录就能让 SessionStart exit 1。
        print(f"[warn] activity-state 写入失败: {type(e).__name__}: {e}；本次更新未落盘。",
              file=sys.stderr)
        return None


def save_activity(state_dir, state):
    """锁内三方合并后原子写回：只覆盖本进程真正改过的字段。

    为什么不是「整份写回」：四个 hook 都是读—改—写，两个会话并发时后写的会把先写的
    整份盖掉（会话计数回退、别人刚 upsert 的 pending_maintenance 消失）。
    为什么不是「从 load 到 save 全程持锁」：session-start-prep 在这中间要跑 doctor 验收
    等重活，全程持锁会让其它 hook 干等到 5s 超时——把会话启动拖慢，代价大于收益。

    所以按 base / ours / theirs 三方合并：
      base   = 本进程 load 时的快照
      ours   = 传进来的 state
      theirs = 锁内重读的磁盘现状
    与 base 不同的键才是「我改的」，只有这些覆盖 theirs；我没碰过的键一律保留磁盘版本。

    **适用范围**：幂等赋值型字段（dormant / 各种标记 / 时间戳）。
    「基于当前值计算」的更新走 `update_activity`——本函数解决不了同键并发。

    **调用契约：`state` 必须是 `load_activity()` 拿到的完整字典改出来的**，不能只传
    你关心的那几个键。有 base 快照时，「base 里有、state 里没有」会被判成「调用方显式
    删除了这个键」而真的删掉——传部分字典就会误删其余字段（写行为测试时踩到）。
    """
    path = Path(state_dir) / ACTIVITY_FILE_NAME
    base = _LOAD_SNAPSHOTS.get(str(path))
    lock_path = path.with_suffix(path.suffix + ".lock")
    try:
        with FileLock(lock_path):
            _merge_and_write(path, state, base)
            return True
    except TimeoutError:
        # fail-closed：本次不落盘，但 hook 照常 exit 0。
        # 曾经是「抢不到锁也无锁写下去」，理由是「丢一次更新比丢整轮会话状态轻」——
        # 这个理由站不住：fail-closed 只丢这一次更新（下次拿到锁照常写），并不阻断会话；
        # 而无锁写在「持锁进程恰好卡在读—写之间」时会把对方刚写的字段按旧值盖回去。
        # 丢一次的代价很轻（counter 少 1 / pending 下次 dedup 重新产生 / 注入标记多一次），
        # 覆盖别人的代价更重。
        print(f"[warn] activity-state 锁超时（{LOCK_TIMEOUT_SEC}s），本次状态更新未落盘；"
              f"下次会话正常写入。", file=sys.stderr)
        return False
    except StateUnavailable as e:
        print(f"[warn] {e}；本次状态更新未落盘（不用默认值覆盖）。", file=sys.stderr)
        return False
    except OSError as e:
        print(f"[warn] activity-state 写入失败: {type(e).__name__}: {e}；本次更新未落盘。",
              file=sys.stderr)
        return False


def _merge_and_write(path, state, base):
    if base is None:
        # 没有 load 快照（调用方没先 load，或快照存失败）：判断不出「我改了哪些键」，
        # 但也**不能整份覆盖**——那会连磁盘上的未知键一起删掉，破坏本模块承诺的
        # 「未知键原样保留」（实测：future_unknown_key 被抹）。
        # 退化为「磁盘为底 + 传入值盖上去」。锁仍然罩着整个读—改—写，所以不是没有并发
        # 保护；真正失去的是**分辨力**：没有 base 就分不清「我改过的键」和「我只是读过的键」，
        # 于是我持有的旧值也会盖到磁盘上（有 base 时只覆盖差异键），并且认不出我显式删掉了谁。
        disk0 = read_json_object(path, factory_activity_defaults(),
                                 isolate_corrupt=False, strict_read=True)
        merged0 = dict(disk0)
        merged0.update(state)
        atomic_write(path, dump_json(merged0))
        return
    # strict_read：读不出来就不写（异常由 save_activity 捕获），别拿默认值当 theirs
    disk = read_json_object(path, factory_activity_defaults(),
                            isolate_corrupt=False, strict_read=True)
    merged = dict(disk)
    for k, v in state.items():
        if k not in base or base[k] != v:
            merged[k] = v
    for k in base:
        if k not in state and k in merged:
            del merged[k]  # 本进程显式删掉的键，别让磁盘版本把它带回来
    atomic_write(path, dump_json(merged))
