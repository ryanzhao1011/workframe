#!/usr/bin/env python3
"""
PostToolUse Hook — modules/ stale 检测 + 反向索引维护

触发条件（hooks.json PostToolUse 段配 matcher，当前 Edit|Write|NotebookEdit；Codex 门下它也命中 apply_patch）：
  - Edit/Write/NotebookEdit（Codex：apply_patch 补丁头里的每个路径）命中代码路径（任何在 code_paths glob 范围的文件）
  - 同上命中 **/submodule.yaml
  （`EDIT_TOOLS` 里的 MultiEdit 是历史工具残留，保留仅为向后兼容）

处理逻辑：
  - 代码改动 → 反向 lookup code-paths-index.json → 写 stale-modules.yaml
  - submodule.yaml 改动 → 全量重建索引
  - 索引过期（缺 built_at / 形态坏 / source_stamp 与声明的 stat 摘要不符）→ 读者经
    ensure_fresh_index 自动全量重建；任一份声明读不了时不覆盖旧缓存

子命令：
  rebuild-index              全量重建 code-paths-index（trigger=manual）；有声明读不了 → exit 1
  scan-git-diff              SessionStart 补扫 hook 缺席期间的改动（根仓 + code_paths
                             指到的嵌套 git 仓；tracked/staged/untracked 三类并集）
  init-submodule <basic/sub> 建子模块后刷新索引（内部即全量重建）；有声明读不了 → exit 1
  clear-stale <basic/sub>    清掉该子模块的 stale 标记（code-to-doc Step 7 调用；
                             锁内读改写，幂等，条目不存在也返回 0）

并发与原子写入：
  - 文件锁（POSIX fcntl / Windows msvcrt）排队；锁超时 5s 跳过本次更新
  - 写 .tmp → os.replace 原子替换原文件
  - 判过期在锁外读一次、锁内再判一次：并发时后进锁的进程看到已新鲜就不再重建

外部 API（被 skill 调用）：
  - ensure_fresh_index(project_dir)：有写权限的读者唯一入口（lookup / scan / 模块记忆投递）
  - declared_snapshot / build_index / validate_index：只读对账用（module_close_check 检查 1）
  - init_index_for_submodule(submodule_path)：module-init 调用；migrate-to-modules 经 CLI `init-submodule` 走同一条全量重建
  - rebuild_full_index()：保留给外部调用方的薄包装（仓内 CLI `rebuild-index` 直接走 `_rebuild_locked`）
  - scan_git_diff_for_stale()：SessionStart 段调用，扫 git diff 找未触发的改动

仅在 modules/ 体系项目（projects/modules/ 存在）下生效；否则立即静默退出。
"""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 锁与原子写在 _state_io 里共用一份实现——本文件是它们的出处，提取出去后
# 四个 activity-state hook 也复用同一套，不再各写各的（见 _state_io.py 抬头）。
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402
import _module_paths  # noqa: E402
from _harness import project_dir as _project_dir, strip_harness  # noqa: E402
from _state_io import (  # noqa: E402,F401
    FileLock, LOCK_TIMEOUT_SEC, append_line, atomic_write, event_json, state_dir_of,
)
from _git_scan import (  # noqa: E402
    collect_changed_files as _collect_changed_files,
    discover_nested_repos as _discover_nested_repos,
    flatten as _flatten_by_repo,
)

PROJECT_DIR = _project_dir()
STATE_DIR = state_dir_of(PROJECT_DIR)
INDEX_FILE = STATE_DIR / "code-paths-index.json"
STALE_FILE = STATE_DIR / "stale-modules.yaml"
LOCK_FILE = STATE_DIR / "modules-index.lock"
MODULES_DIR = PROJECT_DIR / "projects" / "modules"
EVENTS_FILE = STATE_DIR / "events.jsonl"

PERFORMANCE_BUDGET_MS = 100  # 单次 lookup 目标 ≤100ms


# ---------- 通用工具 ----------


def now_iso():
    # UTC + 秒级：与其余 producer 统一（见 event-schema 的 ts 口径）
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_event(event_type, **fields):
    """写一条事件到 events.jsonl；失败不阻塞 hook（加锁与 spill 见 _state_io.append_line）。"""
    event = {"ts": now_iso(), "type": event_type, **fields}
    append_line(EVENTS_FILE, event_json(event))


def is_modules_project():
    """仅在 modules/ 体系开启的项目下生效。"""
    return MODULES_DIR.exists() and MODULES_DIR.is_dir()


# 文件锁与原子写：见 _state_io（本文件顶部已导入）

# ---------- glob → regex ----------


_GLOB_TOKEN_RE = re.compile(r"\*\*|[*?\[\]]|[^*?\[\]]+")


def glob_to_regex(pat):
    """把 glob pattern 转成 regex string，支持 `**` `*` `?` 与字面 `[abc]` 字符类。

    与 pathlib.PurePath.match 不同，本实现对路径分隔符严格用 `/`，与项目根相对的 code_paths 一致。
    """
    out = ["^"]
    i = 0
    while i < len(pat):
        if pat.startswith("**", i):
            # `**` 匹配任意多段（含 0 段）
            if i + 2 < len(pat) and pat[i + 2] == "/":
                out.append("(?:.*/)?")
                i += 3
            else:
                out.append(".*")
                i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        elif pat[i] == "[":
            close = pat.find("]", i + 1)
            if close == -1:
                out.append(re.escape(pat[i]))
                i += 1
            else:
                out.append(pat[i : close + 1])
                i = close + 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    out.append("$")
    return "".join(out)


def first_static_segment(pat):
    """提取 glob 的第一段静态前缀作为 bucket key。

    miniprogram/pages/profile/edit/** → 'miniprogram'
    cloudfunctions/profile/**         → 'cloudfunctions'
    apps/web/app/profile/edit/**      → 'apps'
    *.js                              → ''（无静态前缀，进通配 bucket）
    """
    seg = pat.split("/", 1)[0]
    if any(c in seg for c in "*?["):
        return ""
    return seg


# ---------- 索引读写 ----------


INDEX_SCHEMA = "workframe.code-paths-index.v1"


def _index_paths(project_dir=None):
    """(modules_dir, index_file, lock_file, events_file)：`project_dir=None` 用模块常量，与其余函数同一约定。"""
    if project_dir is None:
        return MODULES_DIR, INDEX_FILE, LOCK_FILE, EVENTS_FILE
    p = Path(project_dir)
    state = state_dir_of(p)
    return (p / "projects" / "modules", state / "code-paths-index.json",
            state / "modules-index.lock", state / "events.jsonl")


def _event_to(events_file, event_type, **fields):
    append_line(events_file, event_json({"ts": now_iso(), "type": event_type, **fields}))


def validate_index(data):
    """缓存形态校验：顶层 dict、`buckets` 为 dict、每个桶为 dict、每个 pattern 的值为非空 `list[str]`。

    只验到顶层不够：`{"src": []}` 这类桶、或模块列表写成字符串，lookup 会逐字符 `extend`，
    命中一串单字母「子模块」而不报错。任一不满足即视为损坏。
    """
    if not isinstance(data, dict) or not isinstance(data.get("buckets"), dict):
        return False
    for key, bucket in data["buckets"].items():
        if not isinstance(key, str) or not isinstance(bucket, dict):
            return False
        for pat, subs in bucket.items():
            if (not isinstance(pat, str) or not isinstance(subs, list) or not subs
                    or not all(isinstance(s, str) for s in subs)):
                return False
    return True


def load_index(index_file=None):
    """读 code-paths-index.json；损坏（JSON 坏或 `validate_index` 不过）返回 None。

    文件不存在时返回**不带 `built_at`** 的空骨架——该字段是"是否已真正构建过"的标记，
    读者用它区分「从未构建」与「构建过但结果确实为空」（见 ensure_fresh_index）。
    """
    index_file = INDEX_FILE if index_file is None else Path(index_file)
    if not index_file.exists():
        return {"__schema__": INDEX_SCHEMA, "buckets": {}}
    try:
        data = json.loads(index_file.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if validate_index(data) else None


def save_index(index, index_file=None):
    # 任何一次成功写盘都视为"索引已构建"，避免无 code_paths 声明的项目被反复判定为未构建
    index.setdefault("built_at", now_iso())
    atomic_write(INDEX_FILE if index_file is None else index_file,
                 json.dumps(index, indent=2, ensure_ascii=False))


def _declared_files(modules_dir, errors=None):
    """(posix 相对路径, Path) 按相对路径排序——排序键用字符串，不用 Path（Windows 上 Path 比较不分大小写）。

    自己逐层 `os.scandir`，不用 `Path.glob("*/*/submodule.yaml")`：glob 遇到列不了的目录
    （权限、同步盘占位）**静默跳过**，那一片声明就被当成「不存在」——重建会把完整缓存覆盖成
    残缺缓存，只读对账也拿残缺的声明去比，两边一致、报绿。列不了的目录记进 `errors`
    （`(相对路径/, 异常类名)`），调用方按「读不了」处理。匹配 `submodule.yaml` 用 normcase，
    与原 glob 在 Windows 上不分大小写的行为一致；不判它是不是文件——是目录时由读取那一步报错。
    """
    def _ls(d, rel):
        try:
            with os.scandir(d) as it:
                return [(e.name, e) for e in it]
        except OSError as e:
            if errors is not None:
                errors.append((rel + "/", type(e).__name__))
            return []

    def _is_dir(entry, rel):
        try:
            return entry.is_dir()
        except OSError as e:
            if errors is not None:
                errors.append((rel, type(e).__name__))
            return False

    target = os.path.normcase("submodule.yaml")
    files = []
    for b_name, b in _ls(modules_dir, "."):
        if not _is_dir(b, b_name):
            continue
        for s_name, s in _ls(b.path, b_name):
            s_rel = f"{b_name}/{s_name}"
            if not _is_dir(s, s_rel):
                continue
            for f_name, _f in _ls(s.path, s_rel):
                if os.path.normcase(f_name) == target:
                    files.append((f"{s_rel}/{f_name}", Path(s.path) / f_name))
    return sorted(files, key=lambda x: x[0])


def _stamp_of(rows):
    return "v1:" + hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def _stat_row(rel, f):
    try:
        st = f.stat()
        return [rel, st.st_size, st.st_mtime_ns]
    except OSError:
        return [rel, None, None]


def declared_stamp(modules_dir):
    """全部 `*/*/submodule.yaml` 的（相对路径、字节数、mtime_ns）有序列表的摘要。只 stat、不读内容。

    用 stat 不用内容哈希：每次编辑都要算一次，stat 更便宜；git checkout、同步盘、shell 写入都会改 stat。
    同字节同 mtime 的改动会漏过——由 module_close_check 检查 1 的只读对账（按内容重算）兜底。
    """
    modules_dir = Path(modules_dir)
    if not modules_dir.is_dir():
        return "v1:empty"
    errors = []
    rows = [_stat_row(rel, f) for rel, f in _declared_files(modules_dir, errors)]
    # 列不了的目录也进戳：戳因此与「这片确实为空」不同，读者会去重建，重建那一步报 unreadable、保旧
    return _stamp_of(rows + [[rel, "unlistable", err] for rel, err in errors])


class _TextSource:
    """只提供 `read_text(encoding=…)` 的内存适配：把已读到的文本交给 `parse_code_paths`，不让它再开一次文件。"""

    def __init__(self, text):
        self._text = text

    def read_text(self, encoding=None, errors=None):
        return self._text


def declared_snapshot(modules_dir):
    """一轮读取全部声明：返回 `(stamp, pairs, errors)`。

    - `pairs`：`[(pattern, "<basic>/<sub>"), …]`
    - `errors`：`[(相对路径, 异常类名), …]`——读不了 / 不是 UTF-8 的那份**不当成空声明**
    - `stamp`：与 `declared_stamp` 同算法，取自同一轮 stat（先 stat 后读：两者之间文件变了，
      下一次判过期时戳必不符，只会多重建一次，不会漏）

    每份只读一次（`read_bytes` → `decode`），解析经 `_TextSource` 交给 `parse_code_paths`：
    让它自己再开文件的话，两次读之间文件消失或换版，它吞异常返回 `[]`，会被当成「成功读到空声明」。
    """
    modules_dir = Path(modules_dir)
    if not modules_dir.is_dir():
        return "v1:empty", [], []
    rows, pairs, errors = [], [], []
    for rel, f in _declared_files(modules_dir, errors):
        rows.append(_stat_row(rel, f))
        try:
            text = f.read_bytes().decode("utf-8")
        except Exception as e:
            errors.append((rel, type(e).__name__))
            continue
        sub = rel.rsplit("/", 1)[0]
        pairs.extend((pat, sub) for pat in parse_code_paths(_TextSource(text)))
    return _stamp_of(rows), pairs, errors


def index_triples(buckets):
    """`(桶, pattern, 子模块)` 三元组集合——索引的语义内容：空桶、重复项不影响结论。

    唯一实现：收口闸检查 1 的漂移对账与迁移计划的缓存新旧判断都按它比。"""
    return {(b, pat, sub) for b, bucket in buckets.items() for pat, subs in bucket.items() for sub in subs}


def build_index(stamp, pairs):
    """纯函数：按 glob 首段静态前缀分桶（同一 pattern 下子模块去重、保序）。"""
    index = {"__schema__": INDEX_SCHEMA, "built_at": now_iso(), "source_stamp": stamp, "buckets": {}}
    for pat, sub in pairs:
        subs = index["buckets"].setdefault(first_static_segment(pat), {}).setdefault(pat, [])
        if sub not in subs:
            subs.append(sub)
    return index


def rebuild_full_index_inner(modules_dir=None):
    """全量扫声明、在内存里建索引（不持锁、不写盘）。读不了的声明按缺席处理——要区分请用 `declared_snapshot`。"""
    modules_dir = MODULES_DIR if modules_dir is None else Path(modules_dir)
    stamp, pairs, _errors = declared_snapshot(modules_dir)
    return build_index(stamp, pairs)


def _empty_index():
    return {"__schema__": INDEX_SCHEMA, "buckets": {}}


def _rebuild_unlocked(project_dir, trigger):
    """按当前声明全量重建（**不拿锁**，由调用方持锁）。返回 `(index, status, errors)`。

    - 有声明读不了 → **不覆盖**磁盘缓存，返回旧缓存（旧缓存也不可用则返回空索引），
      `status="unreadable"`，写一条 `modules_check_stale_error`
    - 成功 → 写盘，`status="rebuilt"`。成功不写事件（只记成功的流水没有读者）；`trigger`
      只给调用方区分来源用
    """
    modules_dir, index_file, _lock, events_file = _index_paths(project_dir)
    stamp, pairs, errors = declared_snapshot(modules_dir)
    if errors:
        _event_to(events_file, "modules_check_stale_error",
                  error="index_rebuild_skipped_unreadable: " + "; ".join(f"{r} {e}" for r, e in errors[:3]))
        old = load_index(index_file)
        return (old if old is not None else _empty_index()), "unreadable", errors
    index = build_index(stamp, pairs)
    save_index(index, index_file)
    return index, "rebuilt", []


def _is_fresh(index, stamp):
    return index is not None and bool(index.get("built_at")) and index.get("source_stamp") == stamp


def ensure_fresh_index(project_dir=None):
    """有写权限的读者的唯一入口：返回 `(index, status)`，`status` ∈ fresh / rebuilt / unreadable。

    判过期：缺 `built_at`、`validate_index` 不过、或 `source_stamp` 与 `declared_stamp` 不符。
    无锁读一次判过期 → 拿锁 → **锁内重读缓存与当前戳再判一次**（并发时后进锁的进程看到已新鲜就不重建）
    → 调不拿锁的内部构建。拿不到锁照旧抛 `TimeoutError`，由调用方留痕降级。
    `projects/modules/` 不存在时零写入、返回空索引。
    """
    modules_dir, index_file, lock_file, _events = _index_paths(project_dir)
    if not modules_dir.is_dir():
        return _empty_index(), "fresh"
    index = load_index(index_file)
    if _is_fresh(index, declared_stamp(modules_dir)):
        return index, "fresh"
    with FileLock(lock_file):
        index = load_index(index_file)
        if _is_fresh(index, declared_stamp(modules_dir)):
            return index, "fresh"
        trigger = "drift" if index is not None and index.get("built_at") else "fallback"
        index, status, _errors = _rebuild_unlocked(project_dir, trigger)
        return index, status


def _rebuild_locked(project_dir, trigger):
    _modules, _index, lock_file, _events = _index_paths(project_dir)
    with FileLock(lock_file):
        return _rebuild_unlocked(project_dir, trigger)


def init_index_for_submodule(submodule_path):
    """被 module-init / migrate-to-modules 调用：建子模块后刷新索引。返回 status（见 `_rebuild_unlocked`）。

    submodule_path: 'profile/edit'（二段式）。签名保留给既有调用方；内部即全量重建——
    算 `source_stamp` 本来就要扫全部声明，只改一段省不了成本，还多一条写路径。
    重建成功不写事件。
    """
    if not is_modules_project():
        return None
    return _rebuild_locked(None, None)[1]


def rebuild_full_index(trigger="fallback", project_dir=None):
    """全量重建（持锁、原子写入）。返回 status；`projects/modules/` 不存在时返回 None、零写入。

    `trigger` 必须传实值：event-schema 给的枚举是 fallback | manual | migration | drift，
    audit 靠它分清「索引损坏自动重建」「声明变了自动重建」与「人手动跑 rebuild-index」。
    """
    modules_dir = _index_paths(project_dir)[0]
    if not modules_dir.is_dir():
        return None
    return _rebuild_locked(project_dir, trigger)[1]


def parse_code_paths(yaml_path):
    """轻量解析 submodule.yaml 的 code_paths（不引第三方 yaml lib）。

    只识别顶层 `code_paths:` 后的 `- xxx` 列表项，不支持嵌套 mapping 形式。
    支持值后的行尾注释 `# ...`，整行注释和空行会跳过。
    引号包裹的值会去引号。

    **另有一个调用方不 import 本模块**：`workframe_migrate._code_paths_parser` 只把本函数的源码抽出来单独执行，
    命名空间里只有 `re`，以避免引入入口脚本的项目解析和模块初始化依赖。所以本函数
    **只能依赖 `re`、名字与签名（一个带 `read_text(encoding=...)` 的路径参数）不能改**。改了之后那边的自检会在 plan 里
    报「算不出来」，不会静默漏列，但迁移工具的模块清单就此失效。
    """
    paths = []
    in_code_paths = False
    try:
        for raw in yaml_path.read_text(encoding="utf-8").splitlines():
            stripped = raw.rstrip()
            # 整行去注释后判定是否空行 / 是否新的顶层 key
            line_no_comment = re.sub(r"\s+#.*$", "", stripped)
            if not line_no_comment.strip():
                # 空行（或纯注释行）：保持当前 in_code_paths 状态
                continue
            if not stripped.startswith(" ") and not stripped.startswith("-") and not stripped.startswith("#"):
                # 顶层 key 行（如 'api_dependencies:'）：切换 section
                in_code_paths = stripped.startswith("code_paths:")
                continue
            if in_code_paths:
                # 列表项形如 `  - <val>` 或 `  - <val>  # comment`
                m = re.match(r"^\s*-\s*(?P<v>.*?)\s*(?:#.*)?$", stripped)
                if m:
                    val = m.group("v").strip()
                    if (val.startswith('"') and val.endswith('"')) or (
                        val.startswith("'") and val.endswith("'")
                    ):
                        val = val[1:-1]
                    if val:
                        paths.append(val)
                else:
                    in_code_paths = False
    except Exception:
        return []
    return paths


# ---------- stale 标记读写 ----------


def load_stale(stale_file=None):
    path = Path(stale_file) if stale_file is not None else STALE_FILE
    if not path.exists():
        return {"submodules": {}}
    text = path.read_text(encoding="utf-8")
    out = {"submodules": {}}
    current_sub = None
    for raw in text.splitlines():
        m = re.match(r"^\s{2}(\S+):\s*$", raw)
        if m:
            current_sub = m.group(1)
            out["submodules"][current_sub] = {"reasons": [], "first_marked_at": "", "last_marked_at": ""}
            continue
        if current_sub:
            m = re.match(r"^\s{4}(reasons|first_marked_at|last_marked_at):\s*(.*)$", raw)
            if m:
                k, v = m.group(1), m.group(2).strip()
                if k == "reasons":
                    pass  # 列表项接下来按 - 解析
                else:
                    out["submodules"][current_sub][k] = v
            m = re.match(r"^\s{6}-\s*(.+)$", raw)
            if m:
                out["submodules"][current_sub]["reasons"].append(m.group(1).strip())
    return out


def structured_stale(info):
    """Adapt recorded reasons for readers; never compare code or change marks."""
    reasons, files = [], []
    for reason in info.get("reasons", []):
        message = str(reason)
        kind, separator, value = message.partition(":")
        path = value.replace("\\", "/") if separator and kind in (
            "code_changed", "git_diff_at_session_start") and value else None
        reasons.append({"kind": kind, "path": path, "message": message})
        if path is not None and path not in files:
            files.append(path)
    return {"reasons": reasons, "files": files}


def save_stale(stale):
    """写 yaml 形式（手写，不引第三方 lib）。"""
    lines = [
        "# Workframe stale-modules.yaml — modules/ 反向同步过期标记",
        "# 由 PostToolUse hook 写入；code-to-doc skill 消费后清理对应条目",
        f"# 最近写入：{now_iso()}",
        "submodules:",
    ]
    for sub, info in sorted(stale.get("submodules", {}).items()):
        lines.append(f"  {sub}:")
        lines.append(f"    first_marked_at: {info.get('first_marked_at', '')}")
        lines.append(f"    last_marked_at: {info.get('last_marked_at', '')}")
        lines.append("    reasons:")
        for r in info.get("reasons", []):
            lines.append(f"      - {r}")
    atomic_write(STALE_FILE, "\n".join(lines) + "\n")


def mark_stale(submodule_path, reason):
    with FileLock(LOCK_FILE):
        stale = load_stale()
        info = stale["submodules"].setdefault(
            submodule_path,
            {"reasons": [], "first_marked_at": now_iso(), "last_marked_at": now_iso()},
        )
        info["last_marked_at"] = now_iso()
        if reason not in info["reasons"]:
            info["reasons"].append(reason)
        save_stale(stale)


def clear_stale(submodule_path, reason=None):
    """清掉一个子模块的 stale 标记，锁内读改写。返回是否真的清掉了。

    给 code-to-doc 用：它此前被要求「读 stale-modules.yaml、移除条目、写回（用
    fcntl/msvcrt 文件锁，参考 check-stale-modules.py）」——可 LLM 手里只有 Edit 工具，
    持不了 msvcrt 锁，那条指令按字面根本没法执行，并发安全的声明形同虚设；
    残留的 stale 还会让下次会话重复触发解析。

    真的清掉时 append 一条 `stale_cleared` 事件作处置留痕（reason 可选）——
    清除后条目即消失，「处理过才清」与「没处理直接清」在清单上长得一样，
    事件流是唯一能区分二者的地方。条目本来就不存在时不写事件（没清除任何东西）。
    """
    with FileLock(LOCK_FILE):
        stale = load_stale()
        existed = submodule_path in stale.get("submodules", {})
        if existed:
            del stale["submodules"][submodule_path]
            save_stale(stale)
    if existed:
        extra = {"reason": reason} if reason else {}
        append_event("stale_cleared", submodule=submodule_path, **extra)
    return existed


# ---------- 反向 lookup ----------


def lookup_submodules(file_path, project_dir=None, index=None):
    """给定 changed file（项目根相对路径），返回命中的子模块列表。

    `index` 不给时经 `ensure_fresh_index` 取（过期即全量重建）；**显式传入的索引照用、不触发重建**，
    合法的空索引也一样——批量 lookup 的调用方（scan-git-diff）先取一次再逐文件传入。
    过期判据是声明的 stat 摘要而不是「buckets 为空」：纯文档项目的空 buckets 是合法终态，
    用空判据会导致每次 lookup 都重建（实测某项目 2.5 个月累积 4774 次重建）。
    """
    rel = file_path.replace("\\", "/")
    # 不要用 lstrip("./") — char-set 删除会吃掉 .env / .github/... 等点开头文件首字符
    if rel.startswith("./"):
        rel = rel[2:]
    bucket_key = rel.split("/", 1)[0]
    if index is None:
        index, _status = ensure_fresh_index(project_dir)
    candidates = []
    # 主桶 + 通配桶（无静态前缀）
    for key in (bucket_key, ""):
        for pat, subs in index.get("buckets", {}).get(key, {}).items():
            try:
                if re.match(glob_to_regex(pat), rel):
                    candidates.extend(subs)
            except re.error:
                continue
    return sorted(set(candidates))


# ---------- 处理 hook input ----------


EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
PATCH_TOOL = "apply_patch"
# Codex 的 apply_patch 补丁头：四种形态各带一个路径，`Move to` 跟在 `Update File` 之后（移动 = 旧路径 ＋ 新路径）
_PATCH_HEAD_RE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)[ \t]*\r?$", re.M)


def edited_paths(payload):
    """一次编辑工具调用改到的文件（原样路径，可能是相对路径），按出现顺序去重。

    - CC 的编辑工具：`tool_input.file_path` / `notebook_path`，恰一个
    - Codex 的 `apply_patch`：载荷**没有** `file_path`，只有整段补丁文本（`tool_input.command`），一次可改多个文件；
      从补丁头取路径，移动保留前后两个（实测 0.166.0：框架实际 matcher `Edit|Write|NotebookEdit` 命中
      `apply_patch`；补丁头路径可能是绝对路径，也可能相对于会话目录）
    """
    return _module_paths.edited_paths(payload)


def _project_rel(path, cwd):
    """编辑路径 → 项目根相对路径；不在项目根内返回 None。相对路径按载荷 `cwd`（没有就按项目根）解析。"""
    try:
        p = Path(path)
        if not p.is_absolute():
            p = Path(cwd) / p if isinstance(cwd, str) and cwd.strip() else PROJECT_DIR / p
        return str(p.resolve().relative_to(PROJECT_DIR)).replace("\\", "/")
    except Exception:
        return None


def process_postool_use(tool_input):
    """入口：PostToolUse 收到 hook input 后调用。

    tool_input: dict，含 tool_name / tool_input（CC 编辑工具的 file_path，或 Codex apply_patch 的补丁文本）等。
    一次调用改到多个文件时逐个处理；dev 记忆按第一个命中 `code_paths` 的文件投（之后同周期去重）。
    """
    if not is_modules_project():
        return  # 静默
    first_hit = None
    for path in edited_paths(tool_input):
        rel = _project_rel(path, tool_input.get("cwd"))
        if rel is None:
            continue  # 不在项目根内 → 跳过
        if _process_file(rel) and first_hit is None:
            first_hit = rel
    if first_hit:
        _deliver_dev_memory(tool_input, first_hit)


def _process_file(rel):
    """单个改动文件（项目根相对路径）：submodule.yaml 重建索引段；代码文件反查 stale。返回是否命中 `code_paths`。"""
    start_t = time.monotonic()

    # 触发条件 1：submodule.yaml 改动 → 全量重建索引
    if rel.endswith("/submodule.yaml") and rel.startswith("projects/modules/"):
        sub_path = "/".join(rel.split("/")[2:4])  # projects/modules/<basic>/<sub>/submodule.yaml
        try:
            # 有声明读不了：旧缓存保留、error 事件已由重建函数写过。重建成功不写事件
            init_index_for_submodule(sub_path)
        except TimeoutError:
            mark_stale(sub_path, "lock_timeout_during_index_rebuild")
            append_event("modules_index_rebuild_skipped_lock_timeout", submodule=sub_path)
        return False

    # 触发条件 2：代码改动 → 反向 lookup → 写 stale
    try:
        hits = lookup_submodules(rel)
    except TimeoutError:
        # 拿不到索引锁：这次改动的 stale 标记丢了，且连「哪个子模块受影响」都不知道，
        # 没法退而求其次去 mark_stale。至少留一条事件——此前是直接 return，
        # current-state 悄悄过期而没有任何信号。
        append_event("modules_stale_write_skipped_lock_timeout", file=rel,
                     reason="index_lookup_lock_timeout")
        return False
    duration_ms = int((time.monotonic() - start_t) * 1000)
    if duration_ms > PERFORMANCE_BUDGET_MS:
        append_event("modules_index_lookup_slow", file=rel, duration_ms=duration_ms)
    if not hits:
        return False
    for sub in hits:
        try:
            mark_stale(sub, f"code_changed:{rel}")
        except TimeoutError:
            append_event("modules_stale_write_skipped_lock_timeout", submodule=sub, file=rel)
    return True


def _deliver_dev_memory(payload, rel):
    """首次改到模块代码路径 → 投 dev 记忆（主线程；判定与去重在 `_role_memory.deliver`）。

    **路径触发只投 dev**：文件归属不等于正在承担的职责——改一个被 qa 用例覆盖的文件，干的仍是实现。
    PostToolUse 的纯文本 stdout 不进模型，这里输出 `hookSpecificOutput` JSON，且它是本进程 stdout 的唯一内容
    （默认 PostToolUse 路径此前零 stdout）。投递失败一律吞掉，不影响 stale 标记。两门同一路径。
    """
    try:
        import _role_memory
        text = _role_memory.deliver(PROJECT_DIR, payload, "dev", _role_memory.TRIGGER_CODE_PATH, file=rel)
        if text:
            print(_role_memory.hook_output("PostToolUse", text))
    except Exception:
        pass


# ---------- SessionStart 扫 git diff ----------

GIT_SCAN_TIMEOUT_SEC = 10


def _git_ok(repo_abs, args):
    """探测性 git 命令：只关心 rc 是否为 0，失败不留痕。

    探测的是**合法状态**（目录不是 git 仓 / 仓尚无 HEAD），失败不算故障；
    每次 SessionStart 都为这类状态记事件只会刷出噪声。
    """
    try:
        return (
            subprocess.run(
                ["git", "-C", str(repo_abs)] + args,
                capture_output=True,
                timeout=GIT_SCAN_TIMEOUT_SEC,
            ).returncode
            == 0
        )
    except Exception:
        return False


def _git_lines(repo_abs, args, repo_label):
    """跑一条扫描用 git 命令，返回 stdout 行列表；失败（异常/超时/rc 非零）记事件返回 None。

    逐命令、逐仓独立失败：多仓扫描后「一仓坏、全体停」不可接受——任何一条命令的
    故障只损失它自己那份结果，其余仓与命令照常。
    显式 utf-8 + replace：路径含中文/CJK 时不会因系统默认 codec（cp936）解码失败崩溃。

    `-c core.quotepath=off`：只解码正确还不够。git 默认 `quotepath=true`，会把输出路径
    里的非 ASCII **字节**转成八进制转义并给整行加引号（实测 `.../装机与初始化/...` →
    `"projects/modules/\\350\\243\\205..."`）。这类路径喂给 `lookup_submodules` 匹配不上
    任何 code_paths pattern，于是**中文路径的改动被静默漏标 stale**——没有报错、没有
    事件，只是那次改动从此不存在。modules 体系明确允许中文模块名与中文路径
    （reference/module-architecture.md §5.1「name 允许中文」），这不是边角形态。
    **`-c` 必须放在子命令之前**：写成 `ls-files -c core.quotepath=off` 不会报错——
    `-c` 是 `ls-files` 自己的 `--cached` 短选项，于是开关被静默吃掉、转义照旧。
    """
    cmd = ["git", "-C", str(repo_abs), "-c", "core.quotepath=off"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_SCAN_TIMEOUT_SEC,
        )
    except Exception as e:
        append_event(
            "modules_check_stale_error",
            error=f"scan_git_failed: {repo_label} {' '.join(args)}: "
            f"{type(e).__name__}: {str(e)[:120]}",
        )
        return None
    if result.returncode != 0:
        append_event(
            "modules_check_stale_error",
            error=f"scan_git_failed: {repo_label} {' '.join(args)}: "
            f"rc={result.returncode}: {result.stderr.strip()[:120]}",
        )
        return None
    return result.stdout.splitlines()


def discover_nested_repos(patterns):
    """嵌套 git 仓发现——实现在 `_git_scan`，这里只绑定本模块当前的 PROJECT_DIR。

    保留本函数是因为它有第二批调用方（`module_close_check` 的检查 2/4/5/7 与单测按
    `csm.discover_nested_repos(patterns)` 调用），签名不能变；实现移出去是为了让扫描面
    只有一份实现，两处手写必漂（见 `_git_scan` 抬头）。
    """
    return _discover_nested_repos(PROJECT_DIR, patterns)


def scan_git_diff_for_stale():
    """SessionStart 段调用：扫 git 改动找未通过 PostToolUse 触发的改动。

    用例：外部工具改代码（IDE / VS Code 直接改），未走 Claude Edit/Write，PostToolUse 未触发。
    覆盖：根仓 + code_paths 指到的嵌套 git 仓，每仓取未提交 tracked（diff HEAD）+
    staged（diff --cached）+ 未跟踪（ls-files --others --exclude-standard）三类并集。
    **不覆盖**跨会话已 commit 改动的基线比较——已 commit 的改动不在上述三类差异里，
    这是有意的验收边界。

    扫描面本身实现在 `_git_scan.collect_changed_files`；
    本函数负责索引保障、留痕策略与 stale 落盘。
    """
    if not is_modules_project():
        return

    # 入口索引保障：嵌套仓发现依赖索引里的 patterns，过期的索引会漏扫嵌套仓。
    # 一次补扫只取一次快照（ensure_fresh_index），嵌套仓发现与逐文件 lookup 都用这一份，
    # 不对每个脏文件重新 stat 全树。锁超时留痕后按磁盘现状降级（本次可能漏发现嵌套仓，
    # 但不能让 SessionStart hook 因此崩掉）。
    try:
        index, _status = ensure_fresh_index()
    except TimeoutError:
        append_event("modules_check_stale_error", error="scan_ensure_index_lock_timeout")
        index = load_index()
    if not isinstance(index, dict) or not validate_index(index):
        index = _empty_index()
    patterns = [pat for bucket in index.get("buckets", {}).values() for pat in bucket]

    # 扫描面（仓集合 × 三类改动 × 路径拼接）在 `_git_scan` 里，本函数只注入 git 调用与
    # 留痕策略：坏嵌套仓照旧写 `modules_check_stale_error`，逐命令失败的事件仍由
    # `_git_lines` 自己写，因此这里的 on_note 只接坏仓那一类，不重复记。
    def _note(kind, label, detail):
        if kind == "broken_nested_repo":
            append_event(
                "modules_check_stale_error",
                error=f"scan_git_failed: {label} rev-parse --git-dir: "
                "broken nested repo (probe failed), repo skipped",
            )

    by_repo, _notes = _collect_changed_files(
        PROJECT_DIR, patterns, _git_ok, _git_lines, on_note=_note
    )

    for rel in sorted(_flatten_by_repo(by_repo)):
        try:
            hits = lookup_submodules(rel, index=index)
        except TimeoutError:
            # 与 PostToolUse 路径对齐留痕：此前是静默 continue，stale 悄悄丢失无信号
            append_event(
                "modules_stale_write_skipped_lock_timeout",
                file=rel,
                reason="index_lookup_lock_timeout",
            )
            continue
        for sub in hits:
            try:
                mark_stale(sub, f"git_diff_at_session_start:{rel}")
            except TimeoutError:
                append_event("modules_stale_write_skipped_lock_timeout", submodule=sub, file=rel)


# ---------- main ----------


def main():
    # Codex 门下会话不在 workframe 项目内：零写入、零输出退出（判定与门条件只在 _harness 一处）
    if _harness.hook_outside_project():
        return 0
    # `--project <目录>`：只给 rebuild-index / init-submodule 用。close-check 的修复提示带它——
    # 检查的是 `--project` 指定的项目，提示命令不绑项目的话，照抄会按当前 shell 的项目重建另一份缓存
    # 只有这两个子命令认它；别的子命令带上它（含 `--project=<目录>` 写法）一律在任何写入前拒绝——
    # 静默摘掉再按默认项目执行，会在「以为操作了 B」时实际清掉 / 写进 A 的状态
    # 这两个会写缓存的子命令**整串参数**都在写前校验：未知选项、空目录、多余位置参数一律 exit 2——
    # 只在认出 `--project` 之后才查的话，`--projec B`、`rebuild-index B` 会被当成无参、按默认项目重建
    argv_all = strip_harness(sys.argv[1:])
    project = None

    def _usage_error(msg):
        print(f"[modules] {msg}", file=sys.stderr)
        sys.exit(2)  # exit-audited: 用法错误，hook 只走 scan-git-diff 与默认 stdin 两条路径

    if argv_all and argv_all[0] in ("rebuild-index", "init-submodule"):
        positional, rest, i = [], argv_all[1:], 0
        while i < len(rest):
            a = rest[i]
            if a == "--project":
                if project is not None:
                    _usage_error("--project 只给一次")
                if i + 1 >= len(rest) or not rest[i + 1].strip() or rest[i + 1].startswith("-"):
                    _usage_error("--project 缺目录参数")
                project = Path(rest[i + 1]).resolve()
                i += 2
                continue
            if a.startswith("-"):
                _usage_error(f"不认识的选项 {a}（目录写成 `--project <目录>`）")
            if not a.strip():
                _usage_error("位置参数不能为空（init-submodule 要 `<basic>/<sub>`）")
            positional.append(a)
            i += 1
        want = 0 if argv_all[0] == "rebuild-index" else 1
        if len(positional) != want:
            _usage_error(f"{argv_all[0]} 需要 {want} 个位置参数，实际 {positional}")
        argv_all = [argv_all[0], *positional]
    elif any(a == "--project" or a.startswith("--project=") for a in argv_all):
        _usage_error("--project 只用于 rebuild-index / init-submodule")
    if not _index_paths(project)[0].is_dir():
        if project is not None:
            print(f"[modules] {project} 下没有 projects/modules/，未重建", file=sys.stderr)
            sys.exit(1)  # exit-audited: 显式指定的项目不是 modules 项目，hook 不带 --project
        sys.exit(0)

    # 优先按 argv 子命令分派——SessionStart hook 调用形式是
    # `... check-stale-modules.py scan-git-diff` 但 hook 同样会通过 stdin 传入 JSON，
    # 因此不能先读 stdin（否则 stdin 有 JSON 时会走 PostToolUse 分支并退出，
    # 永远到不了 scan-git-diff）。改为先看 argv：
    #   - 有子命令 → 走子命令；stdin 不读，忽略
    #   - 无子命令 → 默认 PostToolUse 模式：读 stdin JSON 并 process_postool_use
    # `--harness <门>` 先剥掉再分派：hooks.json 给**每条**命令都带了它，而本脚本的
    # PostToolUse 那条没有子命令——不剥的话 `args[0]` 就是 `--harness`，落进下面的
    # 「用法错误」分支并 exit 2，按 CC 官方语义那是**阻断 PostToolUse**。
    # 值本身由 `_harness.harness()` 直接读 sys.argv，与这里剥不剥无关。
    args = argv_all
    if args:
        cmd = args[0]
        if cmd == "rebuild-index":
            # 人手动跑 → manual，与损坏自动重建区分开
            _index, status, errors = _rebuild_locked(project, "manual")
            if status == "unreadable":
                for rel_f, err in errors:
                    print(f"[modules] 未重建：projects/modules/{rel_f} 读不了（{err}）——旧缓存保留", file=sys.stderr)
                sys.exit(1)  # exit-audited: 手动重建失败，hook 不走此子命令
            print("[modules] code-paths-index 已全量重建")
        elif cmd == "scan-git-diff":
            try:
                scan_git_diff_for_stale()
            except Exception as e:
                # SessionStart hook 不能因扫描故障非零退出（入口 rebuild 的锁超时已在
                # 函数内留痕降级，这里兜的是未预期异常）——留痕后照常 exit 0
                append_event(
                    "modules_check_stale_error",
                    error=f"scan_git_diff_crashed: {type(e).__name__}: {str(e)[:200]}",
                )
            # 静默：SessionStart hook stdout 会注入上下文，无操作时不污染
        elif cmd == "init-submodule" and len(args) >= 2:
            _index, status, errors = _rebuild_locked(project, None)
            if status == "unreadable":
                for rel_f, err in errors:
                    print(f"[modules] 未重建：projects/modules/{rel_f} 读不了（{err}）——旧缓存保留", file=sys.stderr)
                sys.exit(1)  # exit-audited: module-init 据退出码报 warn，hook 不走此子命令
            print(f"[modules] {args[1]} 已纳入索引（全量重建）")
        elif cmd == "clear-stale" and len(args) >= 2:
            # 可选 --reason "<一句话>"：处置理由随 stale_cleared 事件留痕
            reason = None
            if "--reason" in args[2:]:
                ri = args.index("--reason")
                reason = args[ri + 1] if ri + 1 < len(args) else None
            if clear_stale(args[1], reason=reason):
                print(f"[modules] {args[1]} 的 stale 标记已清除")
            else:
                print(f"[modules] {args[1]} 本来就没有 stale 标记，无需清理")
        else:
            print(
                f"用法：{Path(sys.argv[0]).name} "
                f"[rebuild-index | scan-git-diff | init-submodule <basic/sub> "
                f"| clear-stale <basic/sub> [--reason <一句话>]] "
                f"[--project <目录>（rebuild-index / init-submodule 用）]",
                file=sys.stderr,
            )
            # hook 只走 scan-git-diff 与默认 stdin 两条路径，到不了这里；
            # 若哪天 hook 能走到，exit 2 会阻断 PostToolUse（官方语义）。
            sys.exit(2)  # exit-audited: 子命令用法错误，hook 只走 scan-git-diff 与默认 stdin 两条路径
        sys.exit(0)

    # 默认：PostToolUse hook 通过 stdin 接收 JSON
    # Windows 下 sys.stdin 默认 cp936 解码，含中文路径的 hook payload 会 mojibake；
    # 强制走 stdin.buffer 二进制读 + utf-8 decode 兜住跨平台
    raw_input_text = ""
    try:
        if not sys.stdin.isatty():
            raw_bytes = sys.stdin.buffer.read()
            raw_input_text = raw_bytes.decode("utf-8", errors="replace")
    except Exception as e:
        # stdin 异常本身要可观测，否则只能靠 PostToolUse 全静默 debug
        append_event("modules_check_stale_error", error=f"stdin_read_failed: {type(e).__name__}: {str(e)[:200]}")
        sys.exit(0)

    if not raw_input_text:
        sys.exit(0)

    try:
        payload = json.loads(raw_input_text)
    except Exception as e:
        append_event(
            "modules_check_stale_error",
            error=f"json_parse_failed: {type(e).__name__}: {str(e)[:120]} | head: {raw_input_text[:120]!r}",
        )
        sys.exit(0)
    try:
        process_postool_use(payload)
    except Exception as e:
        append_event("modules_check_stale_error", error=f"{type(e).__name__}: {str(e)[:200]}")
    sys.exit(0)


if __name__ == "__main__":
    main()
