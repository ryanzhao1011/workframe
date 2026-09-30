#!/usr/bin/env python3
"""必载纪律的注入通道打包器：把 `plugins/core/context/` 下的源打成注入片。

用法：
    inject-context.py --harness cc|codex --scope main|sub [--shard i/n | --shard all] [--dry-run]
    inject-context.py --harness codex --scope prompt          # Codex 专用判定模式（见下）

读 `context/both/` + `context/<scope>/`，行尾归一、剥 frontmatter、按序确定性贪心
装箱，输出一片的 hook JSON；`--dry-run` 改为输出整个 (harness, scope) 的装箱账
（片数 / 每片余量 / 每份源的字符数），**那是片数与余量的唯一事实源**，别在任何文档
或断言里写死这些数。

## 三种投递模式

- **`--shard i/n`**（CC）：投第 i 片。CC 每条 hook 有物理上限，所以按片各挂一条。
- **`--shard all`**（Codex）：各片按序拼接、每片片头原样保留（片与片顺序无关，拼起来
  只是省进程），一条 hook 投全部。`--scope main` 时片头之前再加一行
  `本会话 harness=<门> session_id=<id>`——Codex 侧没有会话号环境变量，模型手写事件
  （`workframe-event`）要靠这一行知道自己在哪扇门、哪个会话。**那一行的门标识在
  `--harness` 缺失时落到 `codex`**：`--shard all` 只有 Codex 清单在用，而剥掉 `--harness`
  的行为探针（validate 的 `hook_scripts_tolerate_harness_arg`）要求 stdout 逐字节相同。
- **`--scope prompt`**（Codex，挂 `UserPromptSubmit`）：不装新片，按 stdin 载荷判「这是
  哪类线程」再决定投什么——
    thread_id := transcript_path 文件名里的 UUID（解析不出 ⇒ None）
    is_sub    := (thread_id is None) or (thread_id != session_id)
  子线程（review 型等 `SessionStart` / `SubagentStart` 都不触发的线程）：同 (thread_id,
  turn_id) 已由 `SubagentStart` 注过则零输出，否则投 sub 全片；主线程：记一行 turn，距上一次
  main 注入满 K 个 prompt 就重发 main 全片（compact 后的保险）。**缺失 `transcript_path`
  按子线程处理**——误判为子线程多投一片、inject-log 可见；误判为主线程则该线程整段无
  纪律、与模型犯错同形。K 读 `.workframe-config.json` 的 `codex.reinject_every_turns`
  （缺省 30），不进命令行——Codex 侧 hook 命令行一改即失去信任。
  **本模式永不非零退出**：`UserPromptSubmit` 上的 exit 2 会吞掉用户输入。

## 口径（每条都有它防的具体失效）

- **字符单位 = UTF-16 code unit**。物理上限（`PHYSICAL_CAP`）就是按这个单位实测出来
  的，用 `len(str)` 在 BMP 内恰好相等、遇到星平面字符（emoji）会少算一半，恰恰在
  「刚好卡上限」时算错。
- **策略上限 = 物理上限 × `POLICY_RATIO`，现算不写死**。两个数写死两处必漂；留出的
  余量是给片头与后续增补的。
- **行尾归一（CRLF / 裸 CR → LF）在计数之前做**。源侧混行尾时，raw 字符数与投出去的
  字符数不一致，预算会按错的那个算。归一只发生在打包这一侧，**不改源文件**——源文件
  自己不许有 CR，那条由 `context_sources_lf_only` 钉。
- **frontmatter 剥离**。frontmatter 装的是 `supersedes` 语义覆盖声明（给闸读的），
  不是给模型读的；不剥就等于把几十条段标题塞进每一片，且让「算余量的字符串」与
  「投出去的字符串」变成两个东西。
- **片头计入余量**。片头随每片各发一遍，n 片付 n 遍；不计入就会算出一个投不出去的余量。
- **装箱按文件边界，单文件不跨片**：片间顺序无关（同事件多 hook 并行，到达顺序不可控），
  一份文件被劈成两片就必然产生「接着上一片」的跨片依赖。
- **单文件超策略上限直接判红**，不做任何降级：一份装不下的源意味着该拆而没拆，静默
  截断的后果是模型拿到半份纪律而无从察觉。

## 能力边界

- `PHYSICAL_CAP` 是 **CC 侧 user/plugin 源注入路径的实测值**。**Codex 侧的上限至今未验**
  （只注入过 129 字符的探针）；本脚本对两门用同一个值，方向保守（Codex 若更宽则只是
  没用满，若更窄则本脚本挡不住）。别把它读成「两门都测过」。
- 本脚本只保证「片装得下、内容确定」，**不保证片里的文字是对的**——语义覆盖由
  `context_semantic_coverage` 钉「有没有落点声明」，而「落点里的文字是否忠实承接」
  没有机器判据，靠人工双检。
"""

import argparse
import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# 同目录公共模块：运行态目录与 harness 差异各只有一份实现（见 _state_io.py / _harness.py 抬头）
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402
from _state_io import append_line, state_dir_of  # noqa: E402

CONTEXT_DIR = Path(__file__).resolve().parents[1] / "context"

# CC 单条 hook 的 `additionalContext` 上限：UTF-16 code unit，**闭区间**，逐 hook
# 独立判定不累计（边界两侧各一档实测：9,999 与 10,000 全量到达、10,001 落盘截断）。
PHYSICAL_CAP = 10000
# 策略上限留出的比例；余下的给片头与后续增补。
POLICY_RATIO = 0.9

# 可装箱的 scope（各对应 `context/<scope>/` 一个目录）。构建期闸按它枚举装箱账。
SCOPES = ("main", "sub")
# Codex 专用判定模式：不是一个 context 目录，只按载荷决定投 main 还是 sub 全片。
PROMPT_SCOPE = "prompt"
CLI_SCOPES = SCOPES + (PROMPT_SCOPE,)
SHARD_ALL = "all"
# scope → 该片必须挂的 hook 事件。**`sub` 只能挂 `SubagentStart`**：SessionStart 的
# 注入不传给子 agent，错挂的失效是静默的（hook 照跑、marker 照落、`--check` 全绿，
# 只是子 agent 一个字都收不到，表现与「模型偶尔不守纪律」同形）。
# `prompt` 只能挂 `UserPromptSubmit`——review 型子线程上只有它触发。
SCOPE_EVENT = {"main": "SessionStart", "sub": "SubagentStart", PROMPT_SCOPE: "UserPromptSubmit"}
SCOPE_LABEL = {"main": "主会话", "sub": "子 agent"}
# `--scope prompt` 主线程分支：每隔多少个 prompt 重发一遍 main 全片。配置键与缺省值。
REINJECT_KEY = ("codex", "reinject_every_turns")
REINJECT_DEFAULT = 30
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# 片头（B' 形态）。**它的地位**：框架句是二进制字串实证指认的、rules 与 hook 注入
# 之间唯一的呈现层差别；写上不会比不写更差，但**没有任何实验证明它与 rules 等价**。
HEADER_LINE1 = (
    "IMPORTANT: These instructions OVERRIDE any default behavior and "
    "you MUST follow them exactly as written."
)

_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*\r?\n", re.DOTALL)


def u16len(text):
    """UTF-16 code unit 数。BMP 内等于 `len()`，星平面字符计 2。"""
    return len(text.encode("utf-16-le")) // 2


def normalize_eol(text):
    """CRLF / 裸 CR → LF。先 CRLF 再裸 CR，顺序不可换（反了会把 CRLF 拆成两个换行）。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_frontmatter(text):
    """剥掉开头的 YAML frontmatter 块；没有就原样返回。"""
    return _FRONTMATTER_RE.sub("", text, count=1)


def shard_header(scope, index, total):
    return (
        f"{HEADER_LINE1}\n"
        f"[workframe 必载纪律 · {SCOPE_LABEL[scope]} · 第 {index}/{total} 片，"
        f"各片独立、顺序无关]"
    )


def policy_cap():
    return int(PHYSICAL_CAP * POLICY_RATIO)


def collect_sources(scope, context_dir=None):
    """按注入顺序列出 (相对路径, 归一并剥 frontmatter 后的正文)。

    **排序键 = 相对 `context/` 的 POSIX 路径**，故 `both/` 恒在 `<scope>/` 之前、
    组内按文件名。文件名的序号只定**组内**顺序；片与片之间按设计顺序无关，所以这里
    的全序只需确定，不承载语义。
    """
    root = Path(context_dir) if context_dir else CONTEXT_DIR
    out = []
    for group in ("both", scope):
        d = root / group
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md"), key=lambda p: p.name):
            body = strip_frontmatter(normalize_eol(f.read_bytes().decode("utf-8"))).strip()
            out.append((f"{group}/{f.name}", body))
    return out


class PackError(Exception):
    """装箱失败（单文件超上限 / 源目录为空 / `--shard` 与现算片数不符）。"""


def pack(scope, context_dir=None):
    """确定性贪心装箱，返回 (片列表, 源清单)。

    片列表每项 `{"index", "total", "files", "body", "text", "chars"}`；`text` 是**真正投
    出去的那个字符串**（片头 + 正文），`chars` 按它算，两者不是两份。
    """
    sources = collect_sources(scope, context_dir)
    if not sources:
        raise PackError(f"context/both 与 context/{scope} 下没有任何 .md 源")

    cap = policy_cap()
    for rel, body in sources:
        n = u16len(body)
        if n > cap:
            raise PackError(
                f"单文件超策略上限：{rel} = {n} > {cap} UTF-16 单元。"
                f"单文件不跨片，装不下即无片可投；拆分该文件，不要放宽上限"
            )

    # 片头里带 `第 i/n 片`，而 n 又取决于装箱结果 —— 定点迭代。
    #
    # **收敛有结构性理由，不是「跑了几个值都一样」**：片头长度对 `total` 单调不减（只在
    # n 跨 9→10、99→100 时多一个字符）⇒ 每片可用容量单调不增 ⇒ 片数 `f(n)` 单调不减；
    # 从 n=1 起迭代 `n ← f(n)` 得单调不减序列，而 `f` 的上界是文件数（单文件超上限已在
    # 上面判红，故每份至少能独占一片）⇒ 有限步必达不动点，**不会在两个值之间震荡**。
    #
    # 上界取 `max(16, len(sources) + 1)`：理论步数不超过文件数，`+1` 留一步确认不动点；
    # 保底 16 是不降低既有阈值。**别写回常数**——常数会随 `context/` 长大而在一个本该
    # 收敛的场景下误抛（方向 fail-safe，但那是个会自己过期的数）。
    total = 1
    for _ in range(max(16, len(sources) + 1)):
        shards = _greedy(scope, sources, total, cap)
        if len(shards) == total:
            return shards, sources
        total = len(shards)
    raise PackError(f"片数定点迭代未收敛（scope={scope}）")


def _greedy(scope, sources, total_assumed, cap):
    shards, cur, cur_files = [], None, []

    def flush():
        if cur_files:
            idx = len(shards) + 1
            head = shard_header(scope, idx, total_assumed)
            text = head + "\n\n" + cur
            shards.append({
                "index": idx, "total": total_assumed, "files": list(cur_files),
                "body": cur, "text": text, "chars": u16len(text),
            })

    for rel, body in sources:
        idx = len(shards) + 1
        head = shard_header(scope, idx, total_assumed)
        cand = body if cur is None else cur + "\n\n" + body
        if u16len(head + "\n\n" + cand) <= cap:
            cur, cur_files = cand, cur_files + [rel]
            continue
        flush()
        cur, cur_files = body, [rel]
    flush()
    # 用真实片数重新渲染片头并重算字符数。
    #
    # **这一轮重算在不动点上到不了任何新结果，写清楚为什么**：`pack()` 的迭代只在
    # `len(shards) == total_assumed` 时返回，所以此处渲染出的片头与装箱时用的**逐字相同**，
    # 字符数不可能因此上涨、更不可能溢出。留着它是为了让「投出去的字符串」这一件事只有
    # 一个生成点（装箱期与输出期不各拼一次），不是为了兜住溢出。
    # 真正会让片头变长的情形——`total_assumed` 比最终片数少一个数量级——已被迭代本身排除。
    for s in shards:
        head = shard_header(scope, s["index"], len(shards))
        s["text"] = head + "\n\n" + s["body"]
        s["chars"] = u16len(s["text"])
        s["total"] = len(shards)
    return shards


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _scope_sha(sources):
    """整个 (scope) 的内容指纹，**与分片无关**：片怎么切都不变，切法变了它也不变。

    只此一份实现——`--dry-run` 的账与 inject-log 的行都问它要。两处各拼一遍字符串在今天
    的形态下恰好等值（片内已用同一个分隔符连过），改一次分隔符就会分叉，而分叉后两边
    都不报错、只是同一份内容算出两个指纹。
    """
    return _sha("\n\n".join(body for _rel, body in sources))


def report(harness, scope, context_dir=None):
    """`--dry-run` 的机器可读装箱账。**片数与余量的唯一事实源。**"""
    shards, sources = pack(scope, context_dir)
    where = {rel: s["index"] for s in shards for rel in s["files"]}
    cap = policy_cap()
    return {
        "harness": harness,
        "scope": scope,
        "hook_event": SCOPE_EVENT[scope],
        "physical_cap": PHYSICAL_CAP,
        "policy_cap": cap,
        "shard_count": len(shards),
        "files": [
            {"path": rel, "chars": u16len(body), "shard": where[rel]}
            for rel, body in sources
        ],
        "shards": [
            {
                "index": s["index"], "chars": s["chars"],
                "headroom_policy": cap - s["chars"],
                "headroom_physical": PHYSICAL_CAP - s["chars"],
                "files": s["files"], "sha256": _sha(s["text"]),
            }
            for s in shards
        ],
        "scope_sha256": _scope_sha(sources),
    }


def _parse_shard(spec, total):
    """`i/n` → i；`all` → None（全片拼接投递）。"""
    if spec.strip() == SHARD_ALL:
        return None
    m = re.fullmatch(r"(\d+)/(\d+)", spec.strip())
    if not m:
        raise PackError(f"--shard 形态须为 `i/n` 或 `all`，收到 {spec!r}")
    i, n = int(m.group(1)), int(m.group(2))
    if n != total:
        raise PackError(
            f"--shard 声明 n={n}，打包器现算片数为 {total}："
            f"挂载表少声明一片就有源永远不会被注入，与正常同形。"
            f"改挂载表的 `--shard i/n`，不要改本脚本"
        )
    if not 1 <= i <= n:
        raise PackError(f"--shard i={i} 越界（n={n}）")
    return i


def _stdin_payload():
    """hook 给的 JSON payload；非管道 / 空 / 不合法一律当空字典，不抛。

    **bytes 读 + 显式 UTF-8 解码**：Windows 上 PowerShell 起的 python 其 stdin 默认按本机
    ANSI 代码页解码（`chcp 65001` 也不改变它），而载荷是 UTF-8——含中文的 `cwd` 会变成
    另一串字符，状态目录随之建到一棵错的树下，且 `--scope prompt` 的整条判定式都从这份
    载荷来。CC 侧有 `CLAUDE_PROJECT_DIR` 环境变量掩盖这一点，Codex 侧没有。
    """
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def thread_id_of(payload):
    """载荷 `transcript_path` 文件名末段的 UUID；解析不出返回 None（调用方按「缺失」处理）。

    Codex 的 rollout 文件名形如 `rollout-<时间>-<线程 UUID>.jsonl`，取**最后一个** UUID；
    CC 的 transcript 是 `<会话 UUID>.jsonl`，同一规则得到会话号。
    """
    if not isinstance(payload, dict):
        return None
    tp = payload.get("transcript_path")
    if not isinstance(tp, str) or not tp.strip():
        return None
    found = _UUID_RE.findall(Path(tp).name)
    return found[-1].lower() if found else None


def session_line(harness, sid):
    """`--shard all` 主线程片头之前那一行。门标识在 `--harness` 缺失时落到 `codex`（见文件头）。"""
    door = harness if harness in _harness.HARNESSES else _harness.CODEX
    return f"本会话 harness={door} session_id={sid or ''}"


def all_text(scope, shards, harness=None, sid=None):
    """全片拼接的投递文本；`main` 带会话行。只此一个生成点，inject-log 的 `chars` / `sha256`
    与投出去的字符串出自同一个值。"""
    body = "\n\n".join(s["text"] for s in shards)
    if scope == "main":
        return session_line(harness, sid) + "\n\n" + body
    return body


def _emit(event, text):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": event,
        "additionalContext": text,
    }}, ensure_ascii=False))


def _log(project_dir, row):
    """inject-log 追加一行；写不进去也不能拦住注入——注入是主职责，log 是旁证。"""
    try:
        append_line(state_dir_of(project_dir) / "inject-log.jsonl",
                    json.dumps(row, ensure_ascii=False))
    except Exception:
        pass


def _row(harness, scope, payload, event, **fields):
    """inject-log 行的公共字段。`thread_id`：`agent_id`（SubagentStart 载荷带子线程号）优先，
    否则取 transcript_path 的 UUID——让 `--scope prompt` 在子线程上能按同 (thread_id, turn_id)
    找到 `SubagentStart` 已注过的那一行。"""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "harness": harness, "scope": scope, "shard": None, "sha256": None,
        "scope_sha256": None, "chars": 0,
        "session_id": _harness.session_id(payload),
        "thread_id": (payload.get("agent_id") if isinstance(payload, dict) else None)
        or thread_id_of(payload),
        "turn_id": payload.get("turn_id") if isinstance(payload, dict) else None,
        "event": event,
    }
    row.update(fields)
    return row


def read_inject_log(project_dir):
    """inject-log 全部可解析的行（坏行跳过——本函数只回答「记过什么」，不修文件）。"""
    path = state_dir_of(project_dir) / "inject-log.jsonl"
    rows = []
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    obj = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except OSError:
        pass
    return rows


def reinject_every_turns(project_dir):
    """K：`.workframe-config.json` 的 `codex.reinject_every_turns`，缺省 / 非法一律缺省值。"""
    try:
        cfg = json.loads((Path(project_dir) / _harness.ROOT_MARKER).read_text(encoding="utf-8-sig"))
        val = (cfg.get(REINJECT_KEY[0]) or {}).get(REINJECT_KEY[1])
        if isinstance(val, bool) or not isinstance(val, int) or val < 1:
            return REINJECT_DEFAULT
        return val
    except Exception:
        return REINJECT_DEFAULT


def prompt_mode(harness, payload):
    """`--scope prompt` 的判定式（文件头「三种投递模式」第三条）。返回 0，永不抛。"""
    event = SCOPE_EVENT[PROMPT_SCOPE]
    project_dir = _harness.project_dir(payload)
    sid = _harness.session_id(payload)
    thread_id = thread_id_of(payload)
    turn_id = payload.get("turn_id") if isinstance(payload, dict) else None
    is_sub = (thread_id is None) or (thread_id != (sid or "").lower())
    rows = read_inject_log(project_dir)

    if is_sub:
        if turn_id and any(r.get("scope") == "sub" and r.get("thread_id") == thread_id
                           and r.get("turn_id") == turn_id for r in rows):
            return 0                      # 同轮已由 SubagentStart 注过
        shards, sources = pack("sub")
        text = all_text("sub", shards)
        _log(project_dir, _row(harness, "sub", payload, event, shard=SHARD_ALL,
                               sha256=_sha(text), scope_sha256=_scope_sha(sources),
                               chars=u16len(text), thread_id=thread_id))
        _emit(event, text)
        return 0

    _log(project_dir, _row(harness, PROMPT_SCOPE, payload, event, kind="turn"))
    since = 0
    for r in reversed(rows):
        if r.get("session_id") != sid:
            continue
        if r.get("scope") == "main":
            break
        if r.get("scope") == PROMPT_SCOPE and r.get("kind") == "turn":
            since += 1
    since += 1                            # 刚记的这一轮
    if since < reinject_every_turns(project_dir):
        return 0
    shards, sources = pack("main")
    text = all_text("main", shards, harness, sid)
    _log(project_dir, _row(harness, "main", payload, event, shard=SHARD_ALL,
                           sha256=_sha(text), scope_sha256=_scope_sha(sources),
                           chars=u16len(text), kind="reinject"))
    _emit(event, text)
    return 0


def main():
    # Codex 门下会话不在 workframe 项目内：零写入、零输出退出（判定与门条件只在 _harness 一处）
    if "--dry-run" not in sys.argv[1:] and _harness.hook_outside_project():  # --dry-run 只由人手敲、不写盘，不经守卫
        return 0
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(add_help=True)
    _harness.add_harness_arg(parser)
    parser.add_argument("--scope", required=True, choices=CLI_SCOPES)
    parser.add_argument("--shard", default=None, help="第几片，形如 `2/3`；或 `all`")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    harness = _harness.harness()
    scope = args.scope

    if scope == PROMPT_SCOPE:
        if args.dry_run:
            # 只有 CLI 会这样敲（hook 清单里没有这种组合）：prompt 模式没有「装箱账」可报，
            # 照常走下去会真判定、真发片、真写 inject-log 一行——拒绝比假装 dry-run 诚实
            print("inject-context: `--scope prompt` 没有 --dry-run 形态（它不装箱，只按载荷判定并写 "
                  "inject-log）；要看装箱账用 --scope main|sub --dry-run", file=sys.stderr)
            return 2
        # 挂在 UserPromptSubmit 上：任何失败都只到 stderr，exit 恒 0（非零会吞掉用户输入）
        try:
            return prompt_mode(harness, _stdin_payload())
        except Exception as e:
            print(f"inject-context: prompt 模式失败（{type(e).__name__}: {e}），本轮不注入",
                  file=sys.stderr)
            return 0

    try:
        if args.dry_run:
            print(json.dumps(report(harness, scope), ensure_ascii=False, indent=2))
            return 0
        shards, sources = pack(scope)
        idx = _parse_shard(args.shard, len(shards)) if args.shard else 1
        if not args.shard and len(shards) > 1:
            raise PackError(
                f"scope={scope} 现算 {len(shards)} 片，必须带 `--shard i/n` 指明投哪一片"
                f"（或 `--shard all` 全片投递）：不带就只投得出第 1 片，其余源静默不到达"
            )
    except PackError as e:
        print(f"inject-context: {e}", file=sys.stderr)
        return 2

    payload = _stdin_payload()
    project_dir = _harness.project_dir(payload)
    event = SCOPE_EVENT[scope]
    if idx is None:
        text = all_text(scope, shards, harness, _harness.session_id(payload))
        _log(project_dir, _row(harness, scope, payload, event, shard=SHARD_ALL,
                               sha256=_sha(text), scope_sha256=_scope_sha(sources),
                               chars=u16len(text)))
        _emit(event, text)
        return 0

    shard = shards[idx - 1]
    _log(project_dir, _row(harness, scope, payload, event,
                           shard=f"{shard['index']}/{shard['total']}",
                           sha256=_sha(shard["text"]), scope_sha256=_scope_sha(sources),
                           chars=shard["chars"]))
    _emit(event, shard["text"])
    return 0


if __name__ == "__main__":
    # 非零只有一个异常类型 `PackError`，但它有 **7 个 raise 点**：源目录为空 / 单份源超策略
    # 上限 / 片数定点迭代未收敛 / `--shard` 形态不合法 / `i` 越界 / `i-n` 与现算片数不符 /
    # 多片却不带 `--shard`。**构建期闸只覆盖「发布物形态」的那几条**——
    # `context_shards_within_cap` 看的是仓里的源；**用户机器上安装残缺导致的空源目录
    # 只在运行期可见**，构建期那道闸永远看不到它。到得了的那一刻，模型这一片纪律
    # **一个字都没拿到**，此时最不该做的是安静退 0。
    # **边界**：`SessionStart` / `SubagentStart` 上 exit 2 的确切效果**本项目无实证**
    # （signoff-guard 实证过的是 `PostToolUse`）。这里选的是「响亮失败」而不是「确知安全」；
    # 真要改成收敛为 0，先补这两个事件的实测，别凭推断改。
    # 分片 / 全片模式只挂 SessionStart / SubagentStart；挂在 UserPromptSubmit 上的是
    # `--scope prompt` 模式，它自己把一切异常收成 exit 0（非零会吞掉用户输入）。
    # exit-audited: 非零路径仅 PackError（7 个 raise 点，构建期闸只覆盖其中发布物形态的几条）与 CLI 专属的 `--scope prompt --dry-run` 拒绝（hook 清单里没有这种组合）；hook 上的 prompt 模式恒 exit 0（见上方）
    sys.exit(main())
