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
  scan-git-diff              SessionStart 补扫 hook 缺席期间的改动
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
from _state_io import FileLock, LOCK_TIMEOUT_SEC, atomic_write  # noqa: E402,F401

PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
STATE_DIR = PROJECT_DIR / ".claude" / "workframe-state"
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
    """写一条事件到 events.jsonl；失败不阻塞 hook。"""
    EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    event = {"ts": now_iso(), "type": event_type, **fields}
    try:
        with EVENTS_FILE.open("a", encoding="utf-8", newline="") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


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


def clear_stale(submodule_path):
    """清掉一个子模块的 stale 标记，锁内读改写。返回是否真的清掉了。

    给 code-to-doc 用：它此前被要求「读 stale-modules.yaml、移除条目、写回（用
    fcntl/msvcrt 文件锁，参考 check-stale-modules.py）」——可 LLM 手里只有 Edit 工具，
    持不了 msvcrt 锁，那条指令按字面根本没法执行，并发安全的声明形同虚设；
    残留的 stale 还会让下次会话重复触发解析。
    """
    with FileLock(LOCK_FILE):
        stale = load_stale()
        existed = submodule_path in stale.get("submodules", {})
        if existed:
            del stale["submodules"][submodule_path]
            save_stale(stale)
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


def scan_git_diff_for_stale():
    """SessionStart 段调用：扫 git diff 找未通过 PostToolUse 触发的改动。

    用例：外部工具改代码（IDE / VS Code 直接改），未走 Claude Edit/Write，PostToolUse 未触发。
    """
    if not is_modules_project():
        return
    import subprocess

    try:
        # 取 unstaged + staged 改动并集——覆盖外部工具（IDE/Cursor/VS Code）改后未走 PostToolUse、
        # 以及用户已 git add 但未 commit 的场景
        # 显式 utf-8 + replace：路径含中文/CJK 时不会因系统默认 codec（cp936）解码失败崩溃
        files = set()
        for cmd in (
            ["git", "-C", str(PROJECT_DIR), "diff", "--name-only", "HEAD"],
            ["git", "-C", str(PROJECT_DIR), "diff", "--name-only", "--cached"],
        ):
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    files.add(line)
    except Exception:
        return

    for rel in sorted(files):
        try:
            hits = lookup_submodules(rel)
        except TimeoutError:
            continue
        for sub in hits:
            try:
                mark_stale(sub, f"git_diff_at_session_start:{rel}")
            except TimeoutError:
                pass


# ---------- main ----------


def main():
    if not is_modules_project():
        sys.exit(0)

    # 优先按 argv 子命令分派——SessionStart hook 调用形式是
    # `... check-stale-modules.py scan-git-diff` 但 hook 同样会通过 stdin 传入 JSON，
    # 因此不能先读 stdin（否则 stdin 有 JSON 时会走 PostToolUse 分支并退出，
    # 永远到不了 scan-git-diff）。改为先看 argv：
    #   - 有子命令 → 走子命令；stdin 不读，忽略
    #   - 无子命令 → 默认 PostToolUse 模式：读 stdin JSON 并 process_postool_use
    args = sys.argv[1:]
    if args:
        cmd = args[0]
        if cmd == "rebuild-index":
            # 人手动跑（或 migrate-to-modules 收尾调用）→ manual，与损坏自动重建区分开
            rebuild_full_index(trigger="manual")
            print("[modules] code-paths-index 已全量重建")
        elif cmd == "scan-git-diff":
            scan_git_diff_for_stale()
            # 静默：SessionStart hook stdout 会注入上下文，无操作时不污染
        elif cmd == "init-submodule" and len(args) >= 2:
            init_index_for_submodule(args[1])
            print(f"[modules] {args[1]} 索引段已初始化")
        elif cmd == "clear-stale" and len(args) >= 2:
            if clear_stale(args[1]):
                print(f"[modules] {args[1]} 的 stale 标记已清除")
            else:
                print(f"[modules] {args[1]} 本来就没有 stale 标记，无需清理")
        else:
            print(
                f"用法：{Path(sys.argv[0]).name} "
                f"[rebuild-index | scan-git-diff | init-submodule <basic/sub> "
                f"| clear-stale <basic/sub>]",
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
