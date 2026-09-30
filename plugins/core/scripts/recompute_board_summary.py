#!/usr/bin/env python3
"""
重算 board.yaml 的 summary 块（可单独执行，也可被 session-end-flush.py import）

设计原则：
  - **写入侧不用 yaml.dump**，避免丢失注释和字段顺序——summary 块仍是逐行替换
  - **读计数用 yaml.safe_load**（软依赖，缺 PyYAML 时降级逐行 + 自检）：逐行判据
    对 YAML 的跨行标量续行失明（续行不要求缩进），会提前退出、静默少算并把错值
    写回，还返回 status=ok（BUG-005）。读取不涉及注释与字段顺序，故与上一条不冲突
  - 逐行扫描 summary 块，只替换已知字段（total / pending / in_progress / pending_qa /
    completed / blocked / cancelled / last_updated），未知字段原样保留
  - board.yaml 不存在或 summary 块格式异常时跳过 + 写 skipped 事件，不抛错

触发路径：
  - 自动：SessionEnd hook 结束时由 session-end-flush.py 调用
  - 手动兜底：用户要求立即重算 summary 时，由主 Claude 通过 Bash 调用本脚本

调用方式（单独执行）：
    python recompute_board_summary.py        # 使用 CLAUDE_PROJECT_DIR 或 cwd
"""

import io
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

# **只用于读计数**，写入侧仍走 rewrite_summary_block 的逐行替换——见上面 §设计原则
# 前两条：会丢注释与字段顺序的是 **dump**，读取不涉及，所以两条不冲突。
# 软依赖：缺 PyYAML 时降级到逐行解析，由 count_task_statuses 里的自检兜住
# （形态同 skills/requirement-archiving/scripts/check_archive.py）。
try:
    import yaml
except ImportError:
    yaml = None


class BoardParseMismatch(Exception):
    """逐行解析的结果与文件里的任务条目数对不上——拒绝写回（见下方自检）。"""


class BoardYamlUnparsable(Exception):
    """**PyYAML 在位却读不动 board.yaml**——文件本身是坏的，不是环境问题。

    与 `BoardParseMismatch` 是两回事，别合并：那条说的是「降级路径自己算得不对」，
    这条说的是「权威解析器根本读不进去」。两者的处置都是拒绝写回，但 reason 必须
    分开——否则排查的人不知道该去修 board.yaml 的语法，还是去查降级解析器的失明面。
    """


# 同目录公共模块：运行态目录、harness 差异、追加写各只有一份实现
# （见 _state_io.py / _harness.py 抬头）
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from _harness import project_dir as _project_dir  # noqa: E402
from _state_io import append_line, event_json, state_dir_of  # noqa: E402

PROJECT_DIR = _project_dir()
BOARD_FILE = PROJECT_DIR / "projects" / "board.yaml"
EVENTS_FILE = state_dir_of(PROJECT_DIR) / "events.jsonl"

KNOWN_FIELDS = (
    "total",
    "pending",
    "in_progress",
    "pending_qa",
    "completed",
    "blocked",
    "cancelled",
    "last_updated",
)

# status 值用 [^\s"'#]+ 捕获更宽容（含连字符 / 下划线 / 字母数字）；
# 之前用 \w+ 会让 'needs-review' 这类带 - 的 status 完全被漏掉（Codex P1-4 实测）
STATUS_LINE_RE = re.compile(r'^\s+status:\s*["\']?([^\s"\'#]+)["\']?\s*(?:#.*)?$')
# 任务条目起始行。两种缩进形态都要认——`  - id:`（缩进序列，模板正文用）与
# `- id:`（零缩进序列，YAML 同样合法，模板注释示例正是这种）。
TASK_ENTRY_RE = re.compile(r'^\s*-\s+id\s*:')
SUMMARY_HEADER_RE = re.compile(r'^summary:\s*$')
TASKS_HEADER_RE = re.compile(r'^tasks:\s*$')
FIELD_LINE_RE = re.compile(r'^(\s+)(\w+):\s*(.*?)(\s*#.*)?$')


def append_event(**fields):
    """写一条 summary_recomputed 事件到 events.jsonl（加锁与 spill 见 _state_io.append_line）。"""
    event = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "type": "summary_recomputed",
        **fields,
    }
    append_line(EVENTS_FILE, event_json(event))


def _count_via_yaml(text):
    """用 safe_load 取计数。**只读不写**——写入侧仍是逐行替换，注释与字段顺序不受影响。

    比逐行解析多覆盖的是**跨行标量**的全部形态：双引号 / 单引号续行（YAML 不要求
    续行缩进）、块状标量 `|` `>`。逐行判据对这些一律失明，且方向还不一致——实测
    双引号续行少算、块状标量里的 `status:` 正文多算（BUG-005）。

    返回签名与 _count_task_statuses_by_line 一致，便于两条路径互换。
    """
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("board.yaml 顶层不是映射")
    tasks = data.get("tasks") or []
    if not isinstance(tasks, list):
        raise ValueError("board.yaml 的 tasks 不是序列")
    counts = {k: 0 for k in KNOWN_FIELDS if k not in ("total", "last_updated")}
    total = 0
    unknown_statuses = set()
    for t in tasks:
        if not isinstance(t, dict):
            continue
        status = t.get("status")
        if status in counts:
            counts[status] += 1
            total += 1
        elif status is not None:
            unknown_statuses.add(str(status))
    return total, counts, unknown_statuses, len(tasks)


def count_task_statuses(text):
    """统计 tasks 的 status 分布。**yaml 优先；只有 PyYAML 缺失才降级逐行 + 自检**。

    返回 (total, counts dict, unknown_statuses set, n_entries)——两条路径签名一致。

    **两种「读不到」分流，不共用一条路径**：

    - **PyYAML 缺失**（环境问题）→ 降级逐行 + 自检。逐行路径的已知失明面写在
      `_count_task_statuses_by_line` 的 docstring 里。
    - **PyYAML 在位但解析失败**（文件坏了）→ 抛 `BoardYamlUnparsable`，调用方转
      skipped，**不写回、不报 ok**。这一支曾与上一支共用 `except Exception: pass`，
      后果是 board.yaml 里的 C0 控制字符 / 未转义裸双引号被静默吞掉，summary 照写、
      事件照报 `status:"ok"`——「真解析器读不了这个文件」这件事一点痕迹都不留。

    降级路径抛 `BoardParseMismatch` 时调用方**必须拒绝写回**：本函数的老实现对跨行
    标量续行失明，会静默少算并把错值写进 summary、还返回 status=ok（BUG-005）。
    """
    if yaml is not None:
        try:
            return _count_via_yaml(text)
        except Exception as e:
            # **「缺库」与「文件坏了」必须分流，不能共用一条降级路径。**
            # 走到这里说明 PyYAML 在位、却读不动 board.yaml ⇒ 文件本身是坏的。
            # 旧实现在这里 `except Exception: pass` 落进逐行路径，而逐行路径对
            # C0 控制字符、未转义裸双引号这类畸形**完全失明**（它只按行找
            # `status:`），于是照常算出一组数、写进 summary、事件写 `status:"ok"`
            # ——真解析器读不了这个文件这件事**不留任何痕迹**。实测：把 U+0001
            # 写进 board.yaml，两条路径都报 total=3，事件 ok，零告警。
            # 现在改为抛出，由调用方转成 skipped + 可查的 reason，绝不静默报 ok。
            # （逐行降级仍然保留，但**只服务于 PyYAML 缺失**这一种情形——见下方。）
            raise BoardYamlUnparsable(f"{type(e).__name__}: {e}") from e

    # 只有 PyYAML 不可用时才走到这里：降级逐行 + 下面的自检。
    total, counts, unknown_statuses, n_entries = _count_task_statuses_by_line(text)

    # **自检的参照必须不共享块内循环的退出条件。**
    # `n_entries` 与 `total` 都在那个循环里递增，遇到提前退出会**一起**少算——
    # 实测 BUG-005 现场两者同为 23（真值 27），拿它俩互比是**恒绿假闸**。
    # 全文件 `- id:` 计数与该循环无关，同一现场得 27 vs 23，正确报红。
    declared = sum(1 for ln in text.splitlines() if TASK_ENTRY_RE.match(ln))
    if declared != n_entries:
        raise BoardParseMismatch(
            f"全文件 `- id:` {declared} 条，tasks 块内只解析到 {n_entries} 条"
            f"——逐行解析对某种跨行标量失明，拒绝写回（见 BUG-005）"
        )
    return total, counts, unknown_statuses, n_entries


def _count_task_statuses_by_line(text):
    """**只扫描 tasks: 块内**的 `status: <value>` 行，遇到下一个顶层 key 停止。

    降级实现：仅在 PyYAML 不可用或 safe_load 失败时使用，且结果必须过
    count_task_statuses 的自检才作数。**已知失明面**：YAML 的跨行标量续行不要求
    缩进，本函数会把这类续行判成下一个顶层 key 而提前退出（BUG-005）。

    返回 (total, counts dict, unknown_statuses set, n_entries)。

    - total: 合法状态的 task 总数
    - counts: 各合法状态计数（pending / in_progress / pending_qa / completed / blocked / cancelled）
    - unknown_statuses: 在 tasks 块内遇到但不在 KNOWN_FIELDS 里的 status 值集合
      （如 'completd' 拼写错误、'review' 未定义状态等）。SessionStart drift check
      看到非空集合时会**跳过自动修复**（C+ 第一个保护条件），改写
      summary_drift_repair_skipped 事件让用户检查。
    - n_entries: tasks 块内看到的任务条目数（`- id:` 行）。用于区分「看板真的是空的」
      与「有任务但一条都没解析出来」——后者必须报 skipped 而不是把 0 写进 summary。

    v0.2.2 起改为只扫 tasks 块（之前扫整个文件，未来 board.yaml 扩展
    `projects:` / `milestones:` / `review.status:` 等顶层 key 时会误计数）。
    """
    counts = {k: 0 for k in KNOWN_FIELDS if k not in ("total", "last_updated")}
    total = 0
    unknown_statuses = set()
    n_entries = 0

    in_tasks = False
    for line in text.splitlines():
        if TASKS_HEADER_RE.match(line):
            in_tasks = True
            continue
        if not in_tasks:
            continue
        # 块结束：遇到非空、无缩进、**且不是列表项**的行（下一个顶层 key）。
        # `and not stripped.startswith("- ")` 这一条不能省——YAML 允许序列项与父键同级：
        #     tasks:
        #     - id: TASK-001
        #       status: pending
        # 这种写法完全合法，而早期判据会在第一个 `- id:` 处就认为 tasks 块结束，
        # 于是 total 算成 0 并把全 0 写回 summary，且返回 status=ok 零告警
        # （2026-08-16 实测：2 个任务算成 0；drift check 因与它自洽也不报）。
        # board-template.yaml 的注释示例恰好是这种零缩进写法，照抄即中招。
        stripped = line.strip()
        if stripped and not line.startswith((" ", "\t")) and not stripped.startswith("- "):
            in_tasks = False
            continue
        if TASK_ENTRY_RE.match(line):
            n_entries += 1
        m = STATUS_LINE_RE.match(line)
        if m:
            status = m.group(1)
            if status in counts:
                counts[status] += 1
                total += 1
            else:
                unknown_statuses.add(status)
    return total, counts, unknown_statuses, n_entries


def parse_summary_counts(text):
    """读出 board.yaml 里 summary 块当前的数字字段值。

    返回 (summary_total, summary_counts dict)：
    - summary_total: int 或 None（无 summary 块 / 字段缺失）
    - summary_counts: {pending: int|None, in_progress: int|None, ...}

    供 session-start-prep.py 的 drift check 对比 actual vs summary 用。
    """
    parsed = {}
    in_summary = False
    for raw in text.splitlines():
        body = raw.rstrip("\r\n")
        if SUMMARY_HEADER_RE.match(body):
            in_summary = True
            continue
        if not in_summary:
            continue
        if body == "" or not body.startswith((" ", "\t")):
            in_summary = False
            continue
        m = FIELD_LINE_RE.match(body)
        if m:
            _indent, key, value, _comment = m.groups()
            value = value.strip().strip('"').strip("'")
            try:
                parsed[key] = int(value)
            except (ValueError, TypeError):
                pass  # last_updated 等非数字字段跳过

    if not parsed:
        return None, {}
    summary_total = parsed.get("total")
    summary_counts = {k: parsed.get(k) for k in KNOWN_FIELDS if k not in ("total", "last_updated")}
    return summary_total, summary_counts


def rewrite_summary_block(text, total, counts, today):
    """逐行扫描 text，在 summary 块内就地替换已知字段的值。

    若 summary 块缺失 KNOWN_FIELDS 中的字段（如 pending_qa 从未写过），在块结束前
    按 canonical 顺序补齐——否则 SessionStart drift check 会反复把 None vs 0 视为
    drift 并写假 summary_drift_repaired 事件（v0.2.2-fixup-5 修复 Codex 复测发现）。

    返回 (新文本, 是否找到 summary 块)。
    """
    target_values = {
        "total": str(total),
        "pending": str(counts["pending"]),
        "in_progress": str(counts["in_progress"]),
        "pending_qa": str(counts["pending_qa"]),
        "completed": str(counts["completed"]),
        "blocked": str(counts["blocked"]),
        "cancelled": str(counts["cancelled"]),
        "last_updated": f'"{today}"',
    }
    # canonical 顺序：补齐缺失字段时按此顺序追加到 summary 块尾部
    canonical_order = list(KNOWN_FIELDS)

    lines = text.splitlines(keepends=True)
    out = []
    in_summary = False
    summary_seen = False
    seen_keys = set()
    summary_indent = "  "  # 默认 2 空格；首次见到字段时学习实际缩进
    summary_eol = "\n"     # 默认 LF；首次见到字段时学习实际 EOL

    def flush_missing_fields():
        """在 summary 块结束前按 canonical 顺序补齐缺失的 KNOWN_FIELDS 字段。"""
        for key in canonical_order:
            if key not in seen_keys:
                out.append(f"{summary_indent}{key}: {target_values[key]}{summary_eol}")

    for raw in lines:
        body = raw.rstrip("\r\n")
        eol = raw[len(body):]

        if not in_summary:
            if SUMMARY_HEADER_RE.match(body):
                in_summary = True
                summary_seen = True
                seen_keys = set()
                summary_indent = "  "
                summary_eol = "\n"
            out.append(raw)
            continue

        # 在 summary 块内。空行或零缩进行即块结束——结束前补齐缺失字段。
        if body == "" or not body.startswith((" ", "\t")):
            flush_missing_fields()
            in_summary = False
            out.append(raw)
            continue

        m = FIELD_LINE_RE.match(body)
        if m:
            indent, key, _old_value, comment = m.groups()
            comment = comment or ""
            # 首个字段时学习实际缩进 / EOL，后续补齐字段沿用同一格式
            if not seen_keys:
                summary_indent = indent
                summary_eol = eol
            seen_keys.add(key)
            if key in target_values:
                out.append(f"{indent}{key}: {target_values[key]}{comment}{eol}")
                continue

        out.append(raw)

    # 文件结尾仍在 summary 块内（罕见：board.yaml 只有 summary 段）
    if in_summary:
        flush_missing_fields()

    return "".join(out), summary_seen


def recompute_board_summary():
    """入口：读 board.yaml，重算并就地更新 summary 块，写事件。
    返回 dict：{status: ok|skipped|error, reason?: str, total?: int, counts?: dict, unknown_statuses?: list}

    **strict 默认行为（v0.2.2-fixup-2 起）**：
    扫描 tasks: 块时遇到任何未定义 status 值（如 'completd' / 'needs-review' 拼写错误或未声明状态）
    → 不重算，写 status=skipped 事件（reason=unknown_statuses_in_tasks）。
    避免把"错误数据静默排除在 total 外"——summary 数字应反映 source of truth tasks 的真实状态。
    用户/SessionStart drift check 看到 skipped 后明确知道要先修 task 拼写。
    """
    if not BOARD_FILE.exists():
        result = {"status": "skipped", "reason": "board_not_found"}
        append_event(**result)
        return result

    # utf-8-sig 容错 Windows BOM（PowerShell Set-Content -Encoding UTF8 默认带 BOM；普通 utf-8 文件不受影响）
    text = BOARD_FILE.read_text(encoding="utf-8-sig")
    # strict 保护 0：逐行降级路径的自检不通过 → 不重算。
    # 与下面两道同一形态（skipped + 不写回）。本缺陷的伤害主要来自「写错还报 ok」，
    # 所以宁可什么都不写、留一条可查的 reason，也不把少算的值盖上去。
    try:
        total, counts, unknown_statuses, n_entries = count_task_statuses(text)
    except BoardParseMismatch as e:
        result = {"status": "skipped", "reason": "parse_mismatch", "detail": str(e)}
        append_event(**result)
        return result
    except BoardYamlUnparsable as e:
        # strict 保护 0b：真解析器读不动 board.yaml → 不重算、留痕。
        # 与 parse_mismatch 分成两个 reason：那条指向降级解析器的失明面，
        # 这条指向 board.yaml 自己的语法。跑 `workframe-doctor --group runtime`
        # 的 `yaml_parse` 项可以拿到具体行列与肇事字符。
        result = {"status": "skipped", "reason": "board_yaml_unparsable", "detail": str(e)}
        append_event(**result)
        return result
    today = date.today().isoformat()

    # strict 保护：发现未知 status 时不重算（避免静默忽略错误数据）
    if unknown_statuses:
        result = {
            "status": "skipped",
            "reason": "unknown_statuses_in_tasks",
            "unknown_statuses": sorted(unknown_statuses),
        }
        append_event(**result)
        return result

    # strict 保护 2：看到任务条目却一条 status 都没解析出来 → 不重算。
    # 「看板真的是空的」(tasks: [] → n_entries=0) 与「有任务但解析失败」必须分开：
    # 后者若照常写 0 进 summary，用户看到的是一块凭空清零的看板，且 status=ok 零告警。
    if n_entries and total == 0:
        result = {
            "status": "skipped",
            "reason": "tasks_unparsed",
            "entries_seen": n_entries,
        }
        append_event(**result)
        return result

    new_text, summary_seen = rewrite_summary_block(text, total, counts, today)

    if not summary_seen:
        result = {"status": "skipped", "reason": "summary_block_not_found"}
        append_event(**result)
        return result

    if new_text != text:
        BOARD_FILE.write_text(new_text, encoding="utf-8", newline="")

    result = {"status": "ok", "total": total, "counts": counts}
    append_event(**result)
    return result


def main():
    # stdout/stderr wrap 仅在 CLI 直接调用时生效（避免 import 时副作用关闭外层流）
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
    # bin/workframe-recompute-board-summary 的 docstring 对外承诺三种 status
    # （ok / skipped / error），异常时也必须给 JSON 而不是抛 traceback——
    # 调用方（主 Claude 的 Bash、CI）按 JSON 解析结果。
    try:
        result = recompute_board_summary()
    except Exception as e:
        result = {"status": "error", "reason": f"{type(e).__name__}: {str(e)[:200]}"}
        try:
            append_event(**result)
        except Exception:
            pass
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
