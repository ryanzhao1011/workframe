#!/usr/bin/env python3
"""
PostToolUse Hook — modules/ stale 检测 + 反向索引维护

触发条件（hooks.json PostToolUse 段配 matcher，当前 Edit|Write|NotebookEdit）：
  - Edit/Write/NotebookEdit 命中代码路径（任何在 code_paths glob 范围的文件）
  - 同上命中 **/submodule.yaml
  （下方白名单里的 MultiEdit 是历史工具残留，保留仅为向后兼容）

处理逻辑：
  - 代码改动 → 反向 lookup code-paths-index.json → 写 stale-modules.yaml
  - submodule.yaml 改动 → 重建该子模块的索引段
  - 索引文件损坏（JSON 解析失败）→ fallback 全量重建

子命令：
  rebuild-index              全量重建 code-paths-index（trigger=manual）
  scan-git-diff              SessionStart 补扫 hook 缺席期间的改动（根仓 + code_paths
                             指到的嵌套 git 仓；tracked/staged/untracked 三类并集）
  init-submodule <basic/sub> 初始化单个子模块的索引段
  clear-stale <basic/sub>    清掉该子模块的 stale 标记（code-to-doc Step 7 调用；
                             锁内读改写，幂等，条目不存在也返回 0）

并发与原子写入：
  - 文件锁（POSIX fcntl / Windows msvcrt）排队；锁超时 5s 跳过本次更新
  - 写 .tmp → os.rename 原子替换原文件
  - JSON validate 失败 → 触发 fallback 全量重建

外部 API（被 skill 调用）：
  - init_index_for_submodule(submodule_path)：module-init 调用
  - rebuild_full_index()：损坏 fallback / migrate-to-modules 调用
  - scan_git_diff_for_stale()：SessionStart 段调用，扫 git diff 找未触发的改动

仅在 modules/ 体系项目（projects/modules/ 存在）下生效；否则立即静默退出。
"""

import io
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

# 锁与原子写在 _state_io 里共用一份实现——本文件是它们的出处，提取出去后
# 四个 activity-state hook 也复用同一套，不再各写各的（见 _state_io.py 抬头）。
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402
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


def load_index():
    """读 code-paths-index.json；损坏返回 None 触发 rebuild。

    文件不存在时返回**不带 `built_at`** 的空骨架——该字段是"是否已真正构建过"的标记，
    lookup 用它区分「从未构建」与「构建过但结果确实为空」（见 lookup_submodules）。
    """
    if not INDEX_FILE.exists():
        return {"__schema__": "workframe.code-paths-index.v1", "buckets": {}}
    try:
        data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        # 只校验「顶层是 dict 且有 buckets 键」不够：`buckets: []` 能过这一关，
        # 后续 .get()/.items() 才在别处抛异常。buckets 的值也必须是
        # 映射，损坏就当 None 处理 → 触发全量重建，这是本来就设计好的兜底路径。
        if not isinstance(data, dict) or not isinstance(data.get("buckets"), dict):
            return None
        return data
    except Exception:
        return None


def save_index(index):
    # 任何一次成功写盘都视为"索引已构建"，避免无 code_paths 声明的项目被反复判定为未构建
    index.setdefault("built_at", now_iso())
    atomic_write(INDEX_FILE, json.dumps(index, indent=2, ensure_ascii=False))


def init_index_for_submodule(submodule_path):
    """被 module-init / migrate-to-modules 调用：初始化某子模块的索引段。

    submodule_path: 'profile/edit'（二段式）
    读 projects/modules/<basic>/<sub>/submodule.yaml 的 code_paths，写入索引。
    """
    if not is_modules_project():
        return
    sub_yaml = MODULES_DIR / submodule_path / "submodule.yaml"
    if not sub_yaml.exists():
        return
    code_paths = parse_code_paths(sub_yaml)
    with FileLock(LOCK_FILE):
        index = load_index()
        if index is None:
            index = rebuild_full_index_inner()
        # 删除该子模块的旧索引项
        for bucket in index["buckets"].values():
            for pat, subs in list(bucket.items()):
                if submodule_path in subs:
                    subs.remove(submodule_path)
                    if not subs:
                        del bucket[pat]
        # 写新索引项
        for pat in code_paths:
            bucket_key = first_static_segment(pat)
            bucket = index["buckets"].setdefault(bucket_key, {})
            bucket.setdefault(pat, []).append(submodule_path)
        save_index(index)


def rebuild_full_index_inner():
    """全量扫 projects/modules/**/submodule.yaml 重建索引（不持锁，由调用方持锁）。"""
    index = {
        "__schema__": "workframe.code-paths-index.v1",
        "built_at": now_iso(),
        "buckets": {},
    }
    if not MODULES_DIR.exists():
        return index
    for sub_yaml in MODULES_DIR.glob("*/*/submodule.yaml"):
        sub_path = "/".join(sub_yaml.parent.relative_to(MODULES_DIR).parts)
        for pat in parse_code_paths(sub_yaml):
            bucket_key = first_static_segment(pat)
            bucket = index["buckets"].setdefault(bucket_key, {})
            bucket.setdefault(pat, []).append(sub_path)
    return index


def rebuild_full_index(trigger="fallback"):
    """全量重建（持锁、原子写入）。

    `trigger` 必须传实值：event-schema 给的枚举是 fallback | manual | migration，
    而所有路径都走本函数，此前一律记 fallback——audit 里分不出「索引损坏自动重建」
    和「人手动跑 rebuild-index」。
    """
    if not is_modules_project():
        return
    with FileLock(LOCK_FILE):
        index = rebuild_full_index_inner()
        save_index(index)
        append_event("modules_index_rebuilt", trigger=trigger, buckets=len(index["buckets"]))


def parse_code_paths(yaml_path):
    """轻量解析 submodule.yaml 的 code_paths（不引第三方 yaml lib）。

    只识别顶层 `code_paths:` 后的 `- xxx` 列表项，不支持嵌套 mapping 形式。
    支持值后的行尾注释 `# ...`，整行注释和空行会跳过。
    引号包裹的值会去引号。

    **另有一个调用方不 import 本模块**：`workframe_migrate._code_paths_parser` 只把本函数的源码抽出来单独执行，
    命名空间里只有 `re`（本模块在 import 期替换 `sys.stdout`，被 door 进程二次包装会关掉底层 buffer）。所以本函数
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


def load_stale():
    if not STALE_FILE.exists():
        return {"submodules": {}}
    text = STALE_FILE.read_text(encoding="utf-8")
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


def lookup_submodules(file_path):
    """给定 changed file（项目根相对路径），返回命中的子模块列表。

    若索引文件不存在（首次启用 modules/ 体系还没跑过 init/PostToolUse）或损坏，
    自动触发 fallback 全量重建后再 lookup——避免 SessionStart scan-git-diff 在新项目下永远空命中。
    """
    rel = file_path.replace("\\", "/")
    # 不要用 lstrip("./") — char-set 删除会吃掉 .env / .github/... 等点开头文件首字符
    if rel.startswith("./"):
        rel = rel[2:]
    bucket_key = rel.split("/", 1)[0]
    index = load_index()
    # 仅在「损坏」或「从未构建过」时 rebuild。
    # 不能用 `not index.get("buckets")` 作判据——纯文档项目（submodule.yaml 均未声明
    # code_paths）的空 buckets 是合法终态，用空判据会导致每次 lookup 都重建、重建结果仍为空的
    # 死循环（实测某项目 2.5 个月累积 4774 条 modules_index_rebuilt，占事件流 90%）。
    if index is None or not index.get("built_at"):
        rebuild_full_index()
        index = load_index() or {"buckets": {}}
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


def process_postool_use(tool_input):
    """入口：PostToolUse 收到 hook input 后调用。

    tool_input: dict，含 tool_name / tool_input.file_path 等
    """
    if not is_modules_project():
        return  # 静默

    tool_name = tool_input.get("tool_name", "")
    if tool_name not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return

    raw = tool_input.get("tool_input", {}) or {}
    file_path = raw.get("file_path") or raw.get("notebook_path") or ""
    if not file_path:
        return

    # 转项目根相对路径
    try:
        rel = str(Path(file_path).resolve().relative_to(PROJECT_DIR)).replace("\\", "/")
    except Exception:
        # 不在项目根内 → 跳过
        return

    start_t = time.monotonic()

    # 触发条件 1：submodule.yaml 改动 → 重建该子模块索引段
    if rel.endswith("/submodule.yaml") and rel.startswith("projects/modules/"):
        sub_path = "/".join(rel.split("/")[2:4])  # projects/modules/<basic>/<sub>/submodule.yaml
        try:
            init_index_for_submodule(sub_path)
            append_event(
                "modules_index_segment_rebuilt",
                submodule=sub_path,
                duration_ms=int((time.monotonic() - start_t) * 1000),
            )
        except TimeoutError:
            mark_stale(sub_path, "lock_timeout_during_index_rebuild")
            append_event("modules_index_rebuild_skipped_lock_timeout", submodule=sub_path)
        return

    # 触发条件 2：代码改动 → 反向 lookup → 写 stale
    try:
        hits = lookup_submodules(rel)
    except TimeoutError:
        # 拿不到索引锁：这次改动的 stale 标记丢了，且连「哪个子模块受影响」都不知道，
        # 没法退而求其次去 mark_stale。至少留一条事件——此前是直接 return，
        # current-state 悄悄过期而没有任何信号。
        append_event("modules_stale_write_skipped_lock_timeout", file=rel,
                     reason="index_lookup_lock_timeout")
        return
    duration_ms = int((time.monotonic() - start_t) * 1000)
    if duration_ms > PERFORMANCE_BUDGET_MS:
        append_event("modules_index_lookup_slow", file=rel, duration_ms=duration_ms)
    if not hits:
        return
    for sub in hits:
        try:
            mark_stale(sub, f"code_changed:{rel}")
        except TimeoutError:
            append_event("modules_stale_write_skipped_lock_timeout", submodule=sub, file=rel)


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
    `csm.discover_nested_repos(patterns)` 调用），签名不能变；实现移出去是因为判定类
    脚本要吃同一份扫描面，两处手写必漂（见 `_git_scan` 抬头）。
    """
    return _discover_nested_repos(PROJECT_DIR, patterns)


def scan_git_diff_for_stale():
    """SessionStart 段调用：扫 git 改动找未通过 PostToolUse 触发的改动。

    用例：外部工具改代码（IDE / VS Code 直接改），未走 Claude Edit/Write，PostToolUse 未触发。
    覆盖：根仓 + code_paths 指到的嵌套 git 仓，每仓取未提交 tracked（diff HEAD）+
    staged（diff --cached）+ 未跟踪（ls-files --others --exclude-standard）三类并集。
    **不覆盖**跨会话已 commit 改动的基线比较——已 commit 的改动不在上述三类差异里，
    这是有意的验收边界。

    扫描面本身实现在 `_git_scan.collect_changed_files`（另一个消费方是签发档判定脚本）；
    本函数负责索引保障、留痕策略与 stale 落盘。
    """
    if not is_modules_project():
        return

    # 入口索引保障：嵌套仓发现依赖索引里的 patterns，首次会话索引未构建时 buckets 为空，
    # 不先构建就会漏扫嵌套仓（rebuild 原本只在第一次 lookup 时才触发，晚于发现步）。
    # 判据与 lookup_submodules 同款；锁超时留痕后按现状降级（本次可能漏发现嵌套仓，
    # 但不能让 SessionStart hook 因此崩掉）。
    try:
        index = load_index()
        if index is None or not index.get("built_at"):
            rebuild_full_index()
            index = load_index()
    except TimeoutError:
        append_event("modules_check_stale_error", error="scan_ensure_index_lock_timeout")
        index = load_index()
    if not isinstance(index, dict):
        index = {"buckets": {}}
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
            hits = lookup_submodules(rel)
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
    if not is_modules_project():
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
    args = strip_harness(sys.argv[1:])
    if args:
        cmd = args[0]
        if cmd == "rebuild-index":
            # 人手动跑（或 migrate-to-modules 收尾调用）→ manual，与损坏自动重建区分开
            rebuild_full_index(trigger="manual")
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
            init_index_for_submodule(args[1])
            print(f"[modules] {args[1]} 索引段已初始化")
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
                f"| clear-stale <basic/sub> [--reason <一句话>]]",
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
