"""逾期任务提醒：看板里 `deadline` 已过、状态未完结的任务，**只把真正列出来的记为已提醒**。

调用方是 session-start-prep（在判完 dormant / wake_up 之后调用，休眠、归档、唤醒会话不调）。放在那里而不放在
heartbeat-check：两个 SessionStart hook 的先后不定，心跳先跑时读到的是上次会话留下的唤醒标记，会在唤醒会话里
提前消耗提醒、或在已恢复的会话里错误跳过。

口径：
  - 列出（最多 SHOW_MAX 个）的任务号＋截止日记入 `heartbeat-state.json` 的 `overdue_notified`；没列出来的下次开场
    接着列，直到每一个都被列过一次。新出现的、截止日改过的排在前面。全部都列过 → 静默
  - 任务完结或不再逾期 → 从记录里出列（以后再逾期会重新提醒）
  - 看板**没能检查**（路径不在项目内、文件不存在、读不了、解析失败、PyYAML 不在且逐行读法不能证明读懂了整份看板）→ 每次都报，**不动记录**；
    「没有逾期」和「没能检查」必须分开
  - 读改写在锁内完成，两个会话同时开场同一项只会被列一次

被同目录 import：模块 import 无副作用（不碰 stdout、不读环境）。
"""

import hashlib
import json
from datetime import date, datetime
from pathlib import Path

from _state_io import FileLock, atomic_write, state_dir_of

SHOW_MAX = 5
STATE_FILE_NAME = "heartbeat-state.json"


class BoardUnavailable(Exception):
    """看板没能检查：调用方报出来，不当成「没有逾期」。"""


def _yaml_scalar(raw):
    """取 YAML 行尾标量：剥掉行尾注释与包裹引号。引号内的 `#` 是值的一部分，不剥。"""
    v = raw.strip()
    if v[:1] in ("'", '"'):
        end = v.find(v[0], 1)
        if end != -1:
            return v[1:end]
    return v.split("#", 1)[0].strip().strip('"').strip("'")


def tasks_by_line(text):
    """PyYAML 不可用时的降级读法，**只接受能证明完整读懂的那一种写法**，否则抛 `BoardUnavailable`：

      - 顶层必须有 `tasks:`；值为 `[]`（新装模板的写法）即空看板，其余行内值（流式写法）一律读不懂
      - `tasks:` 下每个条目都必须以 `- id:` 起头、缩进与首个条目相同；同缩进的其他 `- ` 起头行（字段顺序不同的条目）
        即读不懂——宁可报「没查成」，也不把字段挂到上一个任务上
      - 字段只认比条目深一层的 `deadline:` / `status:`；块状标量（notes 里的示例任务）缩进更深，不会被当成任务
      - 任务 ID 不得为空或重复

    宁可报「没查成」，不把一份读不懂的看板当成空看板或半份看板。
    **已知失明面**：不要求缩进的跨行引号续行会被误读（与 recompute_board_summary 的逐行降级同一类）。"""
    tasks, cur, item_indent, seen_tasks, empty_list = [], None, None, False, False
    in_tasks = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0 and stripped.startswith("tasks:"):
            seen_tasks = in_tasks = True
            # 行内值按原样判、不剥引号：`tasks: '[]'` 是字符串，不是空列表
            inline = stripped[len("tasks:"):].split("#", 1)[0].strip()
            if inline == "[]":
                in_tasks, empty_list = False, True
            elif inline:
                raise BoardUnavailable("PyYAML 不可用，逐行读法读不懂行内写法的 tasks")
            continue
        if not in_tasks:
            continue
        if indent == 0 and not stripped.startswith("- "):
            in_tasks = False
            continue
        if item_indent is None and stripped.startswith("- "):
            item_indent = indent
        if indent == item_indent and stripped.startswith("- "):
            if not stripped.startswith("- id:"):
                raise BoardUnavailable("PyYAML 不可用，逐行读法只认以 `- id:` 起头的任务条目")
            cur = {"id": _yaml_scalar(stripped[len("- id:"):])}
            tasks.append(cur)
            continue
        if cur is None:
            raise BoardUnavailable("PyYAML 不可用，逐行读法认不出 tasks 下的写法")
        if indent != item_indent + 2:
            continue
        for key in ("deadline", "status"):
            if stripped.startswith(key + ":"):
                cur[key] = _yaml_scalar(stripped.split(":", 1)[1])
    if not seen_tasks:
        raise BoardUnavailable("看板缺 `tasks` 列表")
    if not tasks and not empty_list:
        # 只有一行 `tasks:`、下面什么都没有：YAML 里那是 null 不是列表（截断的文件也长这样）
        raise BoardUnavailable("看板的 `tasks` 是空值、不是列表")
    ids = [t["id"] for t in tasks]
    if any(not i for i in ids) or len(set(ids)) != len(ids):
        raise BoardUnavailable("看板里有空的或重复的任务 ID")
    return tasks


def read_board_bytes(project_dir):
    """看板路径（先校验在项目内，**再**读）与它的字节；读不了抛 `BoardUnavailable`。"""
    import importlib
    path = importlib.import_module("signoff-guard").board_path(project_dir)
    try:
        path.resolve().relative_to(Path(project_dir).resolve())
    except ValueError as e:
        raise BoardUnavailable("配置的看板路径不在项目内") from e
    try:
        return path, path.read_bytes()
    except FileNotFoundError as e:
        raise BoardUnavailable("看板文件不存在") from e
    except OSError as e:
        raise BoardUnavailable(f"{type(e).__name__}: {e}") from e


def load_tasks(project_dir):
    """`(任务列表, 这份看板字节的摘要)`；没能检查时抛 `BoardUnavailable`（原因写在消息里）。

    只读一次：解析的就是算摘要的那份字节（认领前在锁内拿它与当前看板比）。PyYAML 在位时用签发守卫那份真解析
    （与查询层 `read_board` 同一个 `load_board`），并同样拒绝缺失或重复的任务 ID；不在位才逐行降级。"""
    import importlib
    path, raw = read_board_bytes(project_dir)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        import yaml  # noqa: F401
        have_yaml = True
    except Exception:
        have_yaml = False
    if have_yaml:
        try:
            tasks, problem = importlib.import_module("signoff-guard").load_board(path, raw=raw)
        except Exception as e:
            raise BoardUnavailable(f"{type(e).__name__}: {e}") from e
        if problem:
            raise BoardUnavailable(problem)
        ids = [t.get("id") if isinstance(t, dict) else None for t in tasks]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise BoardUnavailable("看板里有空的或重复的任务 ID")
        return tasks, digest
    try:
        return tasks_by_line(raw.decode("utf-8-sig")), digest
    except UnicodeDecodeError as e:
        raise BoardUnavailable(f"看板不是 UTF-8：{e}") from e


def overdue(tasks, today=None):
    """`[(task_id, deadline)]`，按截止日、任务号排序。"""
    today = (today or date.today()).isoformat()
    out = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        dl = t.get("deadline")
        if isinstance(dl, (date, datetime)):
            dl = dl.isoformat()[:10]
        dl = str(dl).strip() if dl is not None else ""
        status = str(t.get("status") or "").strip()
        if dl and dl.lower() not in ("null", "none", "~") and dl < today \
                and status not in ("completed", "cancelled"):
            out.append((str(t.get("id") or "?"), dl))
    return sorted(out, key=lambda item: (item[1], item[0]))


def _key(item):
    return f"{item[0]}@{item[1]}"


def board_digest(project_dir):
    """当前看板字节的摘要（路径不在项目内或读不了时为 None）：认领前在锁内再核一次，证明「据以认领的观测」还是当前的看板。"""
    try:
        return hashlib.sha256(read_board_bytes(project_dir)[1]).hexdigest()
    except BoardUnavailable:
        return None


def claim(project_dir, items, seen_digest=None):
    """锁内：挑出要列的项（没列过的优先，最多 SHOW_MAX 个）、把它们记为已提醒，返回 `(shown, total, notified_before)`。

    没有没列过的项时 `shown` 为空。`seen_digest` 是解析时那份看板字节的摘要；锁内重算当前看板的摘要，不同（读完之后
    别的会话改过看板）或任一边拿不到就**整次不认领**、返回空——用旧观测删掉别的会话刚记下的项，会让那一项在下一个
    会话重播。
    锁超时 / 写不进去时照样返回要列的项（宁可下次重复，不吞掉这一次）。"""
    state_dir = state_dir_of(project_dir)
    path = state_dir / STATE_FILE_NAME
    current = {_key(i) for i in items}
    box = {"shown": [], "before": set()}

    def _pick(before):
        fresh = [i for i in items if _key(i) not in before]
        return fresh[:SHOW_MAX]

    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        with FileLock(path.with_suffix(path.suffix + ".lock")):
            if seen_digest is None or board_digest(project_dir) != seen_digest:
                return [], len(items), set()
            try:
                state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            except Exception:
                state = {}
            if not isinstance(state, dict):
                state = {}
            before = state.get("overdue_notified")
            before = set(before) if isinstance(before, list) else set()
            shown = _pick(before)
            after = sorted((before & current) | {_key(i) for i in shown})
            if after != sorted(before):
                state["overdue_notified"] = after
                atomic_write(path, json.dumps(state, indent=2))
            box["shown"], box["before"] = shown, before
    except Exception as e:
        import sys
        print(f"[warn] 逾期提醒记录未能落盘（{type(e).__name__}: {e}），下次可能重复提醒", file=sys.stderr)
        box["shown"] = _pick(set())
    return box["shown"], len(items), box["before"]


def reminder_line(project_dir):
    """开场要打印的那一行（逾期提醒或「没查成」），或 None。"""
    try:
        tasks, seen = load_tasks(project_dir)
        items = overdue(tasks)
    except BoardUnavailable as e:
        return f"[注意] 看板读不出来（{e}）——逾期任务这次没查成，不代表没有逾期"
    if not items:
        claim(project_dir, items, seen)   # 让已完结的项出列
        return None
    shown, total, before = claim(project_dir, items, seen)
    if not shown:
        return None
    body = "、".join(f"{tid}（截止 {dl}）" for tid, dl in shown)
    rest = total - len(shown)
    tail = ""
    if rest:
        later = sum(1 for i in items if _key(i) not in before) - len(shown)
        tail = f"；另有 {rest} 个" + (f"（其中 {later} 个下次开场再列）" if later > 0 else "（之前提醒过）")
    return f"[注意] 逾期任务 {total} 个，本次列出：{body}{tail}——在看板里核对或改 deadline"
