#!/usr/bin/env python3
"""PostToolUse Hook — 看板签发块的 schema 校验 ＋ **四段闸门重算比对**。

## 为什么必须是「重算」而不是「校验模型粘贴的文本」

判定权只是被建议移交，没有被实现——模型可以把「否决项：未命中」四个字直接敲进
`qa_signoff`，而**没有任何东西分辨得出那是算出来的还是敲出来的**。本 hook 拿走入内容
里声明的那几个输入（爆炸半径、切片起点、通行证），**自己调 `signoff_tier_check` 重跑
一遍**，把重算结果与写入的档位比。不符即拒。

**它仍然验不了的**（这条必须说出来，否则读者会把它的绿读成「档位判对了」）：
爆炸半径与通行证覆盖面**是模型给的输入**，不是本 hook 算出来的。模型把半径判低一档，
本 hook 一无所知——它只保证「给定这几个输入，档位算出来就是这个」。这一格没有判据。

## 挂载点为什么是 PostToolUse

本项目已实测 `PostToolUse` 上 `exit 2` 能阻断该次工具调用；`PreToolUse` 在本项目**无实证**。
用已验证的机制。代价是**写入已经发生**，本 hook 是「拒绝并要求改回」而不是「拦下不让写」
——所以红灯文案必须说清要改的是哪一条、改成什么。

## 两种模式：拦截 vs 只报（**这是本 hook 最要紧的一条能力边界**）

| 工具 | 模式 | 为什么 |
|---|---|---|
| `Edit` / `Write` / `NotebookEdit` | **拦截**（exit 2） | 写入对象是具名文件，判得出「这次动的就是看板」 |
| `Bash` | **只报**（exit 0 ＋ stderr） | 判不出这条命令动没动看板 |

**Bash 写文件是一条真实存在的旁路，不是理论缺口**——`python x.py`、原地改写类命令、heredoc、
`>` 重定向都能写 `board.yaml`，而 PostToolUse 给 Bash 的 `tool_input` 里**只有 `command`，
没有 `file_path`**。要从任意 shell 命令里判出「它写了哪个文件」是不可判定的
（变量展开、脚本间接写入、管道）。

所以 Bash 侧改成**不看命令、看结果**：PostToolUse 是事后触发的，此刻文件已经写完，
直接去查看板当前的状态即可。但**只报不拦**——拦的话，看板一旦处于违规状态，
**每一条与看板无关的 Bash 命令都会被拦**，那种闸会被整个关掉。

⇒ **Bash 仍然是一条能完成写入的通道，只是不再静默。** 这条差别必须留在这里：
「这道闸只覆盖 A、不覆盖 B」删掉就等于把一个已知缺口变成暗坑。

## 阻断策略（哪些情况 exit 2，哪些不）

| 情况 | 出口 | 为什么 |
|---|---|---|
| 有自签/轻签注声明，且重算档位比声明的严 | **exit 2** | 正是本 hook 要挡的那件事 |
| 有自签/轻签注声明，但重算跑不起来 | **exit 2** | 验不了的自签等于没验过。**「验不了」不许当成「通过」** |
| 有签发块但四段不齐 / `verified_ranges` 缺 | **exit 2** | schema 违反 |
| 自签档未启用而写了 `tier: 自签` | **exit 2** | 出口条件未满足前不得生效 |
| 看板 YAML 解析不了 / 没有任何签发块 / 本 hook 自身出错 | exit 0 ＋ stderr 提示 | 没有自签声明就没有要挡的东西；为一个解析不了的看板阻断全部写入，代价远大于收益 |
| 经 `Bash` 写入（任何形态） | **exit 0 ＋ stderr 报出全部问题** | 判不出这条命令动没动看板，见上一节 |
| `qa_signoff` 是**字符串**（散文签注） | exit 0；主 Claude 直做的那格额外报一句提示 | @qa 的散文签注是既有形态，不追溯、不强迫改写；但「主 Claude 直做 ＋ 散文签注」是成色最低的一格，值得说出来 |

**这张表本身是能力边界**：最后一行意味着**把看板写成 YAML 解析不了的样子就能绕过本
hook**。绕过是可观测的（doctor 会报看板不可解析），但本 hook 不负责发现它。

## 自签档的启用开关

`.workframe-config.json` 的 `signoff.self_signoff_enabled`，**默认 true**（缺键即开启）。
硬出口已满足：hook 绑定 ＋ 字段 schema ＋ 事件注册三者均已落地。

**默认值选 true 是一个被知情接受的取舍，不是「没风险」**——写在这里因为它与本文件
其余部分的取值方向相反，读代码的人有权知道它是被谁、按什么理由定的：

- **收益**：一个要用户先发现某个配置键才生效的能力，对多数项目等于不存在。
- **代价一**：升级到本版的存量项目会**在不做任何动作的情况下**获得自签权。
- **代价二**：**Codex 门下本 hook 是否触发未验**（`event-schema.json` 的
  `availability.codex = "unknown"`：`PostToolUse` 事件在，本 hook 的两条 matcher
  `Bash` / `Edit|Write|NotebookEdit` 按 `tool_name` 正则匹配，而 CC 的工具名在 Codex 是否
  命中要一次带凭据的工具调用才验得了），验明之前那一门下的把关**按不存在对待**：默认开启 ＝
  自签档生效而四段举证纯靠自觉。**「未验」不许写成「已有把关」。**

上述两条由用户 2026-09-10 明确拍板接受（当面提示过代价与更保守的两个选项）。
**别把这段删成一句「默认开启」**——被知情接受的风险要留在记录里才成立。
"""

import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402
from _harness import project_dir as _project_dir  # noqa: E402
from _state_io import append_line, event_json, state_dir_of  # noqa: E402

BLOCK = 2
PASS = 0

# 档位字面量**从判定脚本 import，不在这里手抄**——手抄一份就多一处会漂的表现层，
# 而漂的形态是「hook 认的档位名与脚本吐的不是同一套」，症状是每次都判不符、全被拒。
try:
    import signoff_tier_check as stc
    TIER_ORDER = stc.TIER_ORDER
    TIER_SELF, TIER_LIGHT = stc.TIER_SELF, stc.TIER_LIGHT
    _IMPORT_ERR = None
except Exception as e:  # pragma: no cover - 环境损坏路径
    stc = None
    TIER_ORDER = ("自签", "轻签注", "完整验证", "你拍板")
    TIER_SELF, TIER_LIGHT = TIER_ORDER[0], TIER_ORDER[1]
    _IMPORT_ERR = f"{type(e).__name__}: {e}"

# 需要重算把关的档：这两档意味着**没有第二个视角看过**或只看了签注。
# 完整验证与你拍板不需要本 hook 把关——它们本来就要人介入，写松了只会更严。
GUARDED_TIERS = (TIER_SELF, TIER_LIGHT)

REQUIRED_SEGMENTS = ("admission", "verified", "not_verified", "unreviewed")
ADMISSION_FIELDS = ("radius", "since", "diff_ref", "at")

# 这些工具的写入归因不到具体文件 → 只报不拦（见抬头 §两种模式）
ADVISORY_TOOLS = ("Bash",)

# **便宜的前置过滤。** 真实看板 2500+ 行，整份 PyYAML 解析实测 **339 ms**——那个代价
# 不能给每一条 Bash 命令都付。`qa_signoff:` 后面直接换行 = 块映射开头；
# `qa_signoff: "…"` 与 `qa_signoff: |` 都带值，不匹配。实测同一份看板：
# 前置过滤 11.7 ms vs 完整解析 339 ms（29×），且今天短路（零映射形态）。
# **方向 fail-safe**：它认的是「冒号后没有值」这个纯形态特征，宁可多放过去做一次解析，
# 不会漏掉映射形态。
_MAPPING_OPENER = re.compile(r"^[ \t]*qa_signoff:[ \t]*(?:#.*)?$", re.M)


def _cfg(project_dir):
    p = Path(project_dir) / ".workframe-config.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def board_path(project_dir):
    cfg = _cfg(project_dir)
    rel = ((cfg.get("close_check") or {}).get("board")
           or (cfg.get("paths") or {}).get("board")
           or "projects/board.yaml")
    return (Path(project_dir) / rel).resolve()


SELF_SIGNOFF_DEFAULT = True


def self_signoff_enabled(project_dir):
    """读 `signoff.self_signoff_enabled`，**缺键即 `SELF_SIGNOFF_DEFAULT`**。

    `signoff` 被写成字符串／数组／数字时 `.get` 会抛 `AttributeError`，而 `main()`
    对内部异常按 fail-open 处理——那条路径必须堵死，所以这里显式判类型，不让异常冒出去。

    **非映射时落回默认值而不是硬判关闭**：默认为 true 之后，「非映射 ⇒ 关闭」会让一个
    手误（`"signoff": "true"`）**静默产生与缺键相反的行为**，而用户看不出差别。落回默认
    ＋ 在 stderr 点名，misconfiguration 就是可见的而不是可猜的。**这一格没有 fail-safe
    方向可选**——默认值本身是 true，两个方向都不比对方更保守，所以选可观测的那个。
    """
    sec = _cfg(project_dir).get("signoff")
    if sec is not None and not isinstance(sec, dict):
        print(f"[signoff-guard] `.workframe-config.json` 的 `signoff` 不是映射"
              f"（实为 {type(sec).__name__}），已按默认值 "
              f"{'启用' if SELF_SIGNOFF_DEFAULT else '关闭'} 处理。"
              f"要显式关闭请写 `\"signoff\": {{\"self_signoff_enabled\": false}}`",
              file=sys.stderr)
        return SELF_SIGNOFF_DEFAULT
    if sec is None:
        return SELF_SIGNOFF_DEFAULT
    return bool(sec.get("self_signoff_enabled", SELF_SIGNOFF_DEFAULT))


def board_may_have_mapping_signoff(path):
    """看板里**可能**有映射形态签发块吗？读不出来时返回 True（宁可多解析一次）。"""
    try:
        return bool(_MAPPING_OPENER.search(path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return True


def load_board(path):
    """返回 (tasks, err)。**没有 PyYAML 时返回 err 而不是空列表**——
    「解析不出任务」与「没有任务」是两回事，后者报绿是假绿。"""
    try:
        import yaml
    except Exception:
        return None, "PyYAML 不可用，看板解析不了"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"看板 YAML 解析失败（{type(e).__name__}）"
    if not isinstance(data, dict):
        return None, "看板顶层不是映射"
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return None, "看板缺 `tasks` 列表"
    return tasks, None


def signoff_claims(tasks):
    """挑出**结构化形态**的签发块。

    `qa_signoff` 写成字符串是既有形态（散文签注），**照旧放行**——本 hook 不追溯
    已有条目，也不强迫 @qa 的散文签注改写成 schema。只有写成映射的那种才进把关面，
    因为那正是自签通道要求的形态。
    """
    out = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        blk = t.get("qa_signoff")
        if isinstance(blk, dict):
            out.append((str(t.get("id", "<无 id>")), t, blk))
    return out


def check_schema(task_id, task, blk):
    """四段齐全 ＋ `verified_ranges` ＋ `same_party`。返回问题列表。"""
    problems = []
    for seg in REQUIRED_SEGMENTS:
        v = blk.get(seg)
        if v is None or (isinstance(v, str) and not v.strip()) or \
           (isinstance(v, (list, dict)) and not v):
            problems.append(
                f"{task_id}: `qa_signoff.{seg}` 缺失或为空。四段缺一即不是完整举证——"
                f"② 验了什么 / ③ 没验什么与为什么 / ④ 未独立复核的部分，"
                f"少哪一段读者就在哪一段上无从判断这次签发覆盖到哪儿")
    if not isinstance(blk.get("tier"), str) or blk["tier"] not in TIER_ORDER:
        problems.append(
            f"{task_id}: `qa_signoff.tier` 必须是 {list(TIER_ORDER)} 之一，"
            f"实为 {blk.get('tier')!r}——档位不可解析时本 hook 无从重算，"
            f"而无从重算的自签与没验过等价")
    if not isinstance(blk.get("same_party"), bool):
        problems.append(
            f"{task_id}: `qa_signoff.same_party` 必须显式写 true/false。"
            f"它是「实现方与签发方是否同一方」的机器字段，缺了就无法把两种成色分开统计——"
            f"而这正是允许同一方时唯一的补偿控制")
    adm = blk.get("admission")
    if not isinstance(adm, dict):
        problems.append(
            f"{task_id}: `qa_signoff.admission` 必须是映射，含 {list(ADMISSION_FIELDS)}"
            f"——① 准入凭据是四段里唯一由机器重算的那段，它不可解析时本 hook 只能拒绝")
    else:
        for f in ADMISSION_FIELDS:
            if adm.get(f) in (None, ""):
                problems.append(f"{task_id}: `qa_signoff.admission.{f}` 缺失"
                                f"——重算需要它，缺了算出来的是另一个区间的档位")
        if adm.get("radius") not in (1, 2, 3, 4):
            problems.append(f"{task_id}: `admission.radius` 必须是 1-4 的整数，"
                            f"实为 {adm.get('radius')!r}")
        at = adm.get("at")
        if isinstance(at, str) and not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$", at):
            problems.append(f"{task_id}: `admission.at` 必须是 UTC ＋ 秒级"
                            f"（如 2026-09-10T01:02:03+00:00），实为 {at!r}")
    if task.get("verified_ranges") in (None, "") or \
       (isinstance(task.get("verified_ranges"), str) and not task["verified_ranges"].strip()):
        problems.append(
            f"{task_id}: 缺 `verified_ranges`——增量复核的前提是**上一轮的签发范围被记录**；"
            f"不写它，下一轮就没有可锚定的起点，只能退回全量复核或凭印象划范围")
    return problems


def recompute(project_dir, blk):
    """按声明的输入重跑四段闸门。返回 (tier, err)。

    **不吃写入内容里的任何结论**，只吃它声明的输入（radius / since / passport）。
    """
    if stc is None:
        return None, f"判定脚本 import 失败：{_IMPORT_ERR}"
    adm = blk.get("admission") or {}
    try:
        patterns, index_gap = stc.load_index_patterns(project_dir)
        scan_gaps = []
        if index_gap:
            scan_gaps.append(index_gap)
        since = str(adm.get("since") or "") or None
        delta = stc.collect_delta(project_dir, patterns, since)
        if not delta["repos"]:
            return None, "一个 git 仓都没扫到——此时零改动与看不见改动无法区分"
        delta["_repo_abs"] = {
            label: (Path(project_dir) / r["repo_rel"] if r["repo_rel"] else Path(project_dir))
            for label, r in delta["repos"].items()
        }
        for kind, label, detail in delta["notes"]:
            if kind == "broken_nested_repo":
                scan_gaps.append(f"嵌套仓 {label} 探测失败：{detail}")
            elif kind == "git_failed":
                scan_gaps.append(f"仓 {label} 的 `git {detail}` 失败")
            elif kind == "since_ref_unresolved":
                scan_gaps.append(f"切片起点在仓 {label} 解析不到（{detail}）")
        for label, r in delta["repos"].items():
            for e in r["errors"]:
                scan_gaps.append(f"仓 {label} 取 diff 失败：{e}")
        res = stc.evaluate(delta, adm.get("radius"), stc.DEFAULT_SIZE_CAP,
                           adm.get("passport"), str(blk.get("task") or adm.get("task") or ""),
                           scan_gaps, stc.runtime_protected(project_dir))
        return res, None
    except Exception as e:
        return None, f"重算抛异常：{type(e).__name__}: {e}"


def stricter_of(a, b):
    return TIER_ORDER[max(TIER_ORDER.index(a), TIER_ORDER.index(b))]


def prose_signoff_hints(tasks):
    """「主 Claude 直做 ＋ 散文签注」那一格的提示。**只报不拦。**

    散文形态是既有形态，不追溯也不强迫改写——@qa 的签注本来就不需要这道闸把关，
    它把关的是自签。但打了 `main-executed`（主 Claude 实际完成了研发交付）又用散文签注
    的那一格，正是「实现与签发同一方且无结构化举证」，成色最低。

    **它同时是这道闸最大的豁免**：散文里没有 `tier` 字段 ⇒ 没有「声称走了哪一档」这件事
    可查 ⇒ 只要一直写散文就永远不进把关面。这不是格式问题，是档位声明缺失。
    """
    out = []
    for t in tasks:
        if not isinstance(t, dict):
            continue
        if not isinstance(t.get("qa_signoff"), str):
            continue
        tags = t.get("tags") or []
        if not (isinstance(tags, list) and "main-executed" in tags):
            continue
        if t.get("status") != "completed":
            continue
        out.append(
            f"[signoff-guard] {t.get('id', '<无 id>')}: 打了 `main-executed` 又用散文形态 "
            "`qa_signoff` 签发——实现与签发同一方，且没有结构化举证可核。**本 hook 不拦它**"
            "（散文形态是既有形态），但这一格是成色最低的那种；走自签档请改用映射形态，"
            "它才进重算把关面")
    return out


def guard(project_dir, board):
    """返回 (problems, notes)。problems 非空 ⇒ 调用方决定拦还是只报。"""
    problems, notes = [], []
    tasks, err = load_board(board)
    if err:
        # 没有任务清单就没有签发声明可挡。为一个解析不了的看板阻断全部写入，
        # 代价远大于收益——但**必须说出来**，否则「本 hook 什么都没查」看着像通过。
        notes.append(f"[signoff-guard] 跳过：{err}。本次未做任何签发校验")
        return problems, notes

    notes.extend(prose_signoff_hints(tasks))

    claims = signoff_claims(tasks)
    if not claims:
        return problems, notes

    enabled = self_signoff_enabled(project_dir)
    for task_id, task, blk in claims:
        problems.extend(check_schema(task_id, task, blk))
        tier = blk.get("tier")
        if tier not in GUARDED_TIERS:
            continue
        if not enabled:
            problems.append(
                f"{task_id}: 写了 `tier: {tier}`，但本项目**显式关闭**了自签档"
                f"（`.workframe-config.json` 写了 `signoff.self_signoff_enabled: false`；"
                f"本框架的默认值是 true，所以这是该项目主动做的决定）。"
                f"在改回之前 `pending_qa → completed` 仍须实际调度 @qa；"
                f"要改请动那个配置，**不要改这一行档位**")
            continue
        res, rerr = recompute(project_dir, blk)
        if rerr:
            problems.append(
                f"{task_id}: 声明了 `tier: {tier}` 但本 hook 重算不起来（{rerr}）。"
                f"**验不了的自签不等于验过了**——先把重算跑通（多半是 `admission.since` "
                f"指不到、或运行环境缺 git），再写这个档位")
            continue
        computed = res["tier"]
        if res.get("pending"):
            problems.append(
                f"{task_id}: 重算得出 {len(res['pending'])} 条待裁决项，"
                f"裁决之前判定不是终局，不能落 `{tier}`：{'；'.join(res['pending'])[:300]}")
            continue
        if TIER_ORDER.index(tier) < TIER_ORDER.index(computed):
            problems.append(
                f"{task_id}: 写入的档位 `{tier}` **比重算结果 `{computed}` 松**。"
                f"重算依据——① 默认起点 {res['segments']['default_start']}；"
                f"② 通行证{'成立' if res['segments']['passport']['ok'] else '不成立'}；"
                f"③ {res['segments']['floor']['note']}；"
                f"④ 封顶命中 {res['segments']['veto']['hit'] or '无'}"
                + (f"；扫描面缺口 {res['gaps']}" if res.get("gaps") else "")
                + "。本 hook 只吃你声明的输入（radius / since / passport）自己重跑，"
                  "不读你写的结论——改档位改不动这个结果，要改就改输入或去把缺口补上")
        else:
            notes.append(f"[signoff-guard] {task_id}: 重算 `{computed}`，"
                         f"写入 `{tier}`（不比重算松），通过")
    return problems, notes


def _now_iso():
    """与其余八个 producer 逐字同形（`check-stale-modules.py:81` 等）：UTC ＋ 秒级。
    别改成本地时区或带微秒——events.jsonl 是多方共写的单一文件，消费方按窗口过滤。
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_event(project_dir, event_type, **fields):
    """事件构造照既有惯例：`{"ts", "type", **fields}` 一个**位置 dict** 传给 `event_json`。

    `event_json(event, harness=None)` 的首参是**位置参数**。此处此前写的是
    `event_json(**fields)` —— 全框架九个 producer 里唯一的异类，且**恒抛
    `TypeError: unexpected keyword argument 'type'`**，再被下面的 except 吞掉。

    后果不是「少一条日志」：`event-schema.json` 给这条事件的定语是「静默失败与通过在
    终端里同形，**这条事件是唯一的区分处**」。它自己处于静默失败状态，等于把那道区分
    整个抹掉——一个专为消除静默失败而注册的事件，自身静默失败。

    `ts` 由本函数补齐（schema 标 required），不靠调用方记得传。
    """
    try:
        p = state_dir_of(Path(project_dir)) / "events.jsonl"
        append_line(p, event_json({"ts": _now_iso(), "type": event_type, **fields}))
    except Exception as e:
        # 写不进去也**不能静默**——静默正是这条事件要消灭的东西。这里再吞一次，
        # 就回到了修它之前的状态。
        print(f"[signoff-guard] 事件写入失败，本次内部错误未留痕："
              f"{type(e).__name__}: {e}", file=sys.stderr)


def main():
    # Codex 门下会话不在 workframe 项目内：零写入、零输出退出（判定与门条件只在 _harness 一处）
    if _harness.hook_outside_project():
        return 0
    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    except Exception:
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    tool_name = payload.get("tool_name") or ""
    advisory = tool_name in ADVISORY_TOOLS
    project_dir = _project_dir()

    if advisory:
        # Bash 侧：判不出这条命令动没动看板，所以**不看命令看结果**。
        # 先过便宜的前置过滤，避免给每条 Bash 命令都付一次整份 YAML 解析。
        try:
            board = board_path(project_dir)
            if not board.is_file() or not board_may_have_mapping_signoff(board):
                return PASS
        except Exception:
            return PASS
    else:
        tool_in = payload.get("tool_input") or {}
        fpath = tool_in.get("file_path") or tool_in.get("notebook_path") or ""
        if not fpath:
            return PASS
        try:
            board = board_path(project_dir)
            if Path(fpath).resolve() != board:
                return PASS
        except Exception:
            return PASS

    try:
        problems, notes = guard(project_dir, board)
    except Exception as e:
        # 本 hook 自身出错**不阻断**，但要留痕——静默失败与通过在终端里同形
        print(f"[signoff-guard] 内部错误，本次未做签发校验：{type(e).__name__}: {e}",
              file=sys.stderr)
        append_event(project_dir, "signoff_guard_error",
                     error=f"{type(e).__name__}: {str(e)[:200]}")
        return PASS

    for n in notes:
        print(n, file=sys.stderr)
    if problems:
        if advisory:
            # **只报不拦**：经 Bash 写入时判不出这条命令动没动看板，拦它就会误伤
            # 每一条与看板无关的命令。缺口因此仍在——但不再静默。
            print("[signoff-guard] ⚠ 看板签发块有问题，**本次未阻断**"
                  f"（经 {tool_name} 写入，判不出这条命令动没动看板）。"
                  "看板当前处于违规状态，请修：", file=sys.stderr)
            for p in problems:
                print(f"  ! {p}", file=sys.stderr)
            return PASS
        print("[signoff-guard] 看板签发块未通过校验，本次写入被拒："
              "（本 hook 自己重算四段闸门，不读你写的结论）", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        return BLOCK
    return PASS


if __name__ == "__main__":
    # 本 hook 的非零退出**就是它的功能**——PostToolUse 上 exit 2 阻断该次工具调用，
    # 正是「看板签发块未通过校验时拒绝写入」的实现方式。阻断面收得很窄：只有写入对象
    # 恰是本项目看板、且看板里存在**映射形态**的 `qa_signoff` 声明时才可能返回 2；
    # 本 hook 自身出错、看板解析不了、没有任何签发声明，一律 exit 0（见抬头的阻断策略
    # 表）。散文形态的 `qa_signoff`（既有形态）不进把关面，不会被它拦。
    # exit-audited: 阻断是本 hook 的功能本身，阻断面与豁免面见上方五行与抬头策略表
    sys.exit(main())
