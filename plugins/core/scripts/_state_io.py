#!/usr/bin/env python3
"""运行态状态文件的统一读写：锁、原子替换、损坏隔离只此一份实现。

为什么单独成模块：`activity-state.json` 有四个 hook 在读改写，
四份 load 各自实现了**四种不同**的损坏降级——退出 1 / 恢复三字段 / 恢复完整默认并顺手
裁掉未知键 / 返回空字典。空字典写回就把 `session_counter` 与 `pending_maintenance` 抹平了；
同一份坏文件在不同 hook 手里结局不同，排查时无从复现。写入侧同样是四份非原子的
`write_text`，中途断电或并发写会留下半截 JSON。

锁与原子写本来就在 `check-stale-modules.py` 里跑了很久（含 Windows `msvcrt.locking`
需 `seek(0)` 才能对齐 POSIX whole-file 语义这类踩坑细节），本模块是**提取共用**而非新造。

被 hook 以同目录 import 方式使用，因此：
  - 模块 import 必须无副作用（不碰 stdout、不读环境、不建目录）
  - 文件名用下划线而非连字符——连字符的模块名 import 不进来
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

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
    检查（rules 镜像比对等）恒报不一致。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(content, encoding="utf-8", newline="")
    os.replace(str(tmp), str(path))


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


def state_dir_of(project_dir):
    return Path(project_dir) / ".claude" / "workframe-state"


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
