#!/usr/bin/env python3
"""PostToolUse Hook — 看板签注的 schema 校验 ＋ **否决项重算**。

## 它把关什么

签发分两类（必载片 §谁签发这次收口）：**主会话自理**与**需要独立验证**，否决项命中则交用户
拍板。研发任务转 completed 的签注一律写成**映射形态**（字段见 skill `task-management`
§签发块 schema），本 hook 查四件事：

1. 格式齐不齐——类别、验了什么、没验什么、切片两端、`verified_ranges`
2. 独立验证类写没写验证方与报告指针——**只验有没有写，不判写得对不对**
3. 项目关了自签开关时不许写「自理」
4. 拿签注里钉死的两端 hash **自己重算否决项**（`signoff_tier_check`）：命中而没写
   「你拍板」即拒；有待裁决项时要么写「你拍板」，要么在 `ruling` 里逐条写出裁决

## 为什么必须是「重算」而不是「校验模型粘贴的文本」

模型可以把「否决项：未命中」直接敲进签注，**没有任何东西分辨得出那是算出来的还是敲出来的**。
本 hook 只吃签注声明的**输入**（切片两端），自己重跑一遍——不读写入内容里的任何结论。

**它仍然验不了的**（这条必须说出来，否则读者会把它的绿读成「签对了」）：
签发**类别**由模型判（该独立验证的判成自理，本 hook 一无所知）；验证方写了谁、报告指向哪，
本 hook 只看有没有写；**切片声明了哪几个仓也由签发方写**——漏写一个改过的仓，那个仓的
改动对否决项不可见（`signoff_tier_check` 能力边界第 3 条）。

## 查哪些签注

`status: completed`、`completed_at` 不早于 `SCHEMA_SINCE`，且**带签注、或带 `main-executed` /
`needs-qa-regression` 标签**的任务。之前完成的一律不追溯（它们是按旧口径写的）。两个挡不住的形态：
不带签注、也不带这两个标签的研发任务（与非研发任务同形，判不出该不该有签注）；
`completed_at` 缺失或写成早于截止日的值。

## 只拦本次写入改出来的问题（快照）

运行态目录里的 `signoff-guard-snapshot.json` 记每条在查范围内的签注的指纹与是否通过：
指纹没变且上次通过的直接跳过（两端都是钉死的 hash，重算结果确定，不必每次重跑）；
**新出现或改过**的签注出了问题才拦；**快照里已有、原样未动**的存量问题只报不拦——否则
升级前写下的一条旧签注会让此后每一次改看板都被拦，那种闸会被整个关掉。被拦下的那条
**不写进快照**，于是改回之前它一直算「本次改出来的」，每次写看板都会再拦。
首次运行（没有快照，或快照版本对不上）只建基线：今天完成的签注照常拦，更早的只报不拦——**留着一道窄缝**：
首次运行那一次写入里补写的、完成日早于今天的违规签注会被当成存量，此后只报不拦（每次写看板照样报出来，
不是静默；每个项目只在升级后第一次写看板时碰得上，按「罕见场景不做无谓处理」接受）。**快照写不进去时每次都按首次运行处理——今天之前完成的只报不拦**
（fail-open，stderr 会点名「快照写不进去」）。

## 整板缓存：看板没变就不解析

上一节的快照省的是**重算**，省不掉**解析**——它要先把整份看板解析出来才比得了指纹，而本 hook
挂在每一条 Bash 上，看板长到 MB 级之后纯 Python 解析一次就要大半秒。所以快照文件里另存一份
「上一次完整检查」：看板字节的 SHA-256、一个上下文摘要（快照版本与截止日、本脚本与
`signoff_tier_check.py` 的内容、`.workframe-config.json` 的内容、否决项 6 的运行态路径），以及那一次
的 problems 与存量 notes。两个摘要都相同 ⇒ 不解析，**原样报出上一次的结论**——上一次查出的问题
这一次照样报（Edit 侧照样拦），不会因为「查过了」就当成通过。

- **按内容不按 mtime**：同尺寸替换、保留时间戳的写入都骗不过它
- **不进缓存的结论**：首次建快照那一次（下一次的口径是「存量」不是「首次」）；任何一条签注重算
  跑不起来（hash 解析不到、仓不在——补拉一次提交结论就变，不是看板内容的函数）；快照写不进去
- **缓存读坏、形态不对、上下文摘要算不出来** ⇒ 按未命中走完整检查，不报错
- **它假定的前提**：签注两端钉的是十六进制 commit，同一对 hash 的重算结果不变——git 状态不在
  上下文摘要里。签注里声明的仓目录被整个换成另一个仓（hash 从「解析得到」变成「解析不到」）
  不在覆盖面内，直到看板再变一次

**规则文件变了只作废这份整板缓存，不让已通过的签注重判**：缓存失效后照样整份解析，但上一节的
签注快照里「通过、原样未动」的那几条仍然跳过——规则收紧不追溯已通过的签注（与关自签开关不追溯
同理）。要让一次规则改动重判全部签注，给 `SNAPSHOT_VERSION` 加一（快照整体按首次运行处理）。

解析本身走 `_yaml_probe.safe_load`：装了 libyaml 用 C 实现（同一份看板快一个数量级）；纯 Python
读得进的它都读得进，另有少数纯 Python 拒绝的形态它也读得进（见该函数），那时本 hook 照常检查
而不是「跳过」。

## 挂载点为什么是 PostToolUse

本项目已实测 `PostToolUse` 上 `exit 2` 能阻断该次工具调用；`PreToolUse` 在本项目**无实证**。
用已验证的机制。代价是**写入已经发生**，本 hook 是「拒绝并要求改回」而不是「拦下不让写」
——所以红灯文案必须说清要改的是哪一条、改成什么。

## 两种模式：拦截 vs 只报（**这是本 hook 最要紧的一条能力边界**）

| 工具 | 模式 | 为什么 |
|---|---|---|
| `Edit` / `Write` / `NotebookEdit`，Codex 的 `apply_patch` | **拦截**（exit 2） | 写入对象是具名文件（`apply_patch` 从补丁头取路径），判得出「这次动的就是看板」 |
| `Bash` | **只报**（exit 0 ＋ stderr） | 判不出这条命令动没动看板 |

**Bash 写文件是一条真实存在的旁路，不是理论缺口**——`python x.py`、原地改写类命令、heredoc、
`>` 重定向都能写 `board.yaml`，而 PostToolUse 给 Bash 的 `tool_input` 里**只有 `command`，
没有 `file_path`**。要从任意 shell 命令里判出「它写了哪个文件」是不可判定的
（变量展开、脚本间接写入、管道）。

所以 Bash 侧改成**不看命令、看结果**：PostToolUse 是事后触发的，此刻文件已经写完，
直接去查看板当前的状态即可。但**只报不拦**——拦的话，看板一旦处于违规状态，
**每一条与看板无关的 Bash 命令都会被拦**，那种闸会被整个关掉。Bash 侧还先过一道
便宜的前置过滤：看板里一个映射形态签注都没有时直接放行（那时散文签注的违规它看不见）；
有映射签注时，看板没变就走整板缓存、不重新解析（见上 §整板缓存）。

⇒ **Bash 仍然是一条能完成写入的通道，只是不再静默。** 这条差别必须留在这里：
「这道闸只覆盖 A、不覆盖 B」删掉就等于把一个已知缺口变成暗坑。

## 阻断策略（哪些情况 exit 2，哪些不）

| 情况 | 出口 | 为什么 |
|---|---|---|
| 本次新出现或改过的签注格式不对 / 缺验证方 / 缺批准记录 | **exit 2** | schema 违反 |
| 本次新出现或改过的签注命中否决项而未写「你拍板」 | **exit 2** | 正是本 hook 要挡的那件事 |
| 本次新出现或改过的签注重算跑不起来 | **exit 2** | 验不了的签发等于没验过。**「验不了」不许当成「通过」** |
| 自签开关关闭而写了「自理」 | **exit 2** | 项目主动做的决定 |
| 快照里已有、原样未动的存量问题 | exit 0 ＋ stderr | 见上一节 |
| 看板 YAML 解析不了 / 本 hook 自身出错 | exit 0 ＋ stderr 提示 | 为一个解析不了的看板阻断全部写入，代价远大于收益 |
| 经 `Bash` 写入（任何形态） | **exit 0 ＋ stderr 报出全部问题** | 判不出这条命令动没动看板，见上一节 |

**这张表本身是能力边界**：倒数第二行意味着**把看板写成 YAML 解析不了的样子就能绕过本
hook**。绕过是可观测的（doctor 会报看板不可解析），但本 hook 不负责发现它。

## 自签开关

`.workframe-config.json` 的 `signoff.self_signoff_enabled`，**默认 true**（缺键即开启）。
关闭 ⇒ 研发任务一律按「需要独立验证」签发，签注写「自理」即拒。

**默认值选 true 是一个被知情接受的取舍，不是「没风险」**：

- **收益**：一个要用户先发现某个配置键才生效的能力，对多数项目等于不存在。
- **代价一**：从默认关闭的旧版升级上来的存量项目会**在不做任何动作的情况下**获得自理签发权。
- **代价二**：Codex 门下两条 matcher 都命中（codex-cli 0.166.0 实测 `Bash` 命中 shell 命令、
  `Edit|Write|NotebookEdit` 命中 `apply_patch`）。`apply_patch` 的载荷没有 `file_path`，本 hook 从补丁头取
  被改路径（与 check-stale-modules 同一份解析 `_module_paths.edited_paths`），改到看板就按 Edit 同样判、
  同样 exit 2。**exit 2 在 Codex 下产生什么效果（模型看不看得到 stderr、会不会改回）未在真实会话里验过**
  ——别把它读成「两门把关一样可靠」。

上述取舍由用户 2026-09-10 明确拍板接受（当面提示过代价与更保守的两个选项）。
**别把这段删成一句「默认开启」**——被知情接受的风险要留在记录里才成立。

## 事件

签注通过校验时不写事件——只记成功的流水没有读者。只写 hook 自身出错的 `signoff_guard_error`。

## 顺带：Codex 门的 skill 入口（主会话角色记忆投递）

Bash 路径上若命令读到某个 skill 的 `SKILL.md`，投该 skill 映射到的角色记忆（见 `_deliver_skill_read`）。
它与签发把关互不相干：不改退出码、不碰 stderr，stdout 只放那一串 JSON。
"""

import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    # Read-only consumers import load_board; importing must preserve their
    # streams. Configure the CLI in place without creating another wrapper.
    if __name__ == "__main__":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402
import _module_paths  # noqa: E402  编辑工具载荷 → 被改路径（含 Codex apply_patch 补丁头），与 check-stale-modules 同一份
import _yaml_probe  # noqa: E402  共用解析入口（libyaml 优先）；import 无副作用、不 import PyYAML
from _harness import project_dir as _project_dir  # noqa: E402
from _state_io import append_line, atomic_write, event_json, state_dir_of  # noqa: E402

BLOCK = 2
PASS = 0

try:
    import signoff_tier_check as stc
    _IMPORT_ERR = None
except Exception as e:  # pragma: no cover - 环境损坏路径
    stc = None
    _IMPORT_ERR = f"{type(e).__name__}: {e}"

MODE_SELF = "自理"
MODE_INDEP = "独立验证"
MODE_USER = "你拍板"
MODES = (MODE_SELF, MODE_INDEP, MODE_USER)

# 从这一天起完成的任务按本 schema 查；之前的签注按旧口径写成，不追溯。
# 比较按 ISO 日期字符串字典序（`completed_at` 写成 "YYYY-MM-DD"，YAML 解析成 date 也先转 str）。
SCHEMA_SINCE = "2026-11-01"

SNAPSHOT_NAME = "signoff-guard-snapshot.json"
# 指纹口径一变就要加一：版本对不上的快照按首次运行处理，否则旧指纹全部失配，
# 原样未动的签注会被当成「本次改过」重判、误拦
SNAPSHOT_VERSION = 2

# 这些工具的写入归因不到具体文件 → 只报不拦（见抬头 §两种模式）
ADVISORY_TOOLS = ("Bash",)

# **便宜的前置过滤**：看板里一个映射形态签注都没有时，Bash 侧连摘要都不必算。
# `qa_signoff:` 后面直接换行 = 块映射开头；`qa_signoff: "…"` 与 `qa_signoff: |` 都带值，不匹配。
# **方向 fail-safe**：它认的是「冒号后没有值」这个纯形态特征，宁可多放过去做一次检查，
# 不会漏掉映射形态。**它只挡得住还没写过映射签注的看板**——签注一律写映射之后它恒放行，
# 挡「看板没变却每条 Bash 都整份解析」的是下面的整板缓存（见抬头 §整板缓存）。
_MAPPING_OPENER = re.compile(r"^[ \t]*qa_signoff:[ \t]*(?:#.*)?$", re.M)

# 整板缓存的「规则」那一半：这两份文件的**内容**变了（升级、本地改动）⇒ 整板缓存作废、重新解析。
# 按内容而不是版本号判，升级时不靠人记得给哪个常量加一。已通过的签注不因此重判（见抬头 §整板缓存）。
_RULE_FILES = ("signoff-guard.py", "signoff_tier_check.py")


def _cfg(project_dir):
    p = Path(project_dir) / ".workframe-config.json"
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
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
    """看板里**可能**有映射形态签注吗？读不出来时返回 True（宁可多解析一次）。"""
    try:
        return bool(_MAPPING_OPENER.search(path.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        return True


def load_board(path, raw=None):
    """返回 (tasks, err)。**没有 PyYAML 时返回 err 而不是空列表**——
    「解析不出任务」与「没有任务」是两回事，后者报绿是假绿。

    `raw`：调用方已读出的字节（算摘要用的就是这一份，解析的也必须是这一份，否则摘要与结论
    对不上同一个版本）。按 `read_text` 的口径解码：UTF-8 ＋ 通用换行。"""
    try:
        import yaml  # noqa: F401
    except Exception:
        return None, "PyYAML 不可用，看板解析不了"
    try:
        if raw is None:
            raw = path.read_bytes()
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        data = _yaml_probe.safe_load(text)
    except Exception as e:
        return None, f"看板 YAML 解析失败（{type(e).__name__}）"
    if not isinstance(data, dict):
        return None, "看板顶层不是映射"
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return None, "看板缺 `tasks` 列表"
    return tasks, None


def _has_tag(task, tag):
    tags = task.get("tags") or []
    return isinstance(tags, list) and tag in tags


def _main_executed(task):
    return _has_tag(task, "main-executed")


def _surely_dev_task(task):
    """不带签注时也判得出「这是研发任务」的形态：主会话直做打的 `main-executed`，或
    `needs-qa-regression`（看板约定里它恒为研发类）。`assigned_to: dev` 判不出——@dev 也做咨询类。"""
    return _main_executed(task) or _has_tag(task, "needs-qa-regression")


def _today():
    """本地日期（ISO）。单独成函数，单测要钉住它。"""
    return datetime.now().date().isoformat()


def in_scope(task):
    """这条任务的签注归本 hook 查吗？判据见抬头 §查哪些签注。"""
    if not isinstance(task, dict) or task.get("status") != "completed":
        return False
    ca = task.get("completed_at")
    if ca is None or str(ca).strip()[:10] < SCHEMA_SINCE:
        return False
    return task.get("qa_signoff") is not None or _surely_dev_task(task)


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip()) or \
        (isinstance(v, (list, dict)) and not v)


def check_schema(task_id, task, blk):
    """签注的格式。返回问题列表；不重算否决项。"""
    if blk is None:
        return [f"{task_id}: 研发任务（`main-executed` / `needs-qa-regression`）已完成，却没有 `qa_signoff`——研发任务转 "
                f"completed 必须写签注（映射形态，字段见 skill `task-management` §签发块 schema）"]
    if not isinstance(blk, dict):
        return [f"{task_id}: `qa_signoff` 必须是映射形态（实为 {type(blk).__name__}）。"
                f"{SCHEMA_SINCE} 起完成的研发任务不论谁签发都写映射——散文里没有类别与切片，"
                f"本 hook 无从重算否决项，等于这条签发从没进过把关面"]
    problems = []
    if "tier" in blk or "admission" in blk:
        problems.append(f"{task_id}: 签注用的是旧 schema（`tier` / `admission`）。现行字段是 "
                        f"`mode` / `since` / `diff_ref` / `verified` / `not_verified`"
                        f"（独立验证另需 `verifier`，你拍板另需 `approval`）")
    mode = blk.get("mode")
    if mode not in MODES:
        problems.append(f"{task_id}: `qa_signoff.mode` 必须是 {list(MODES)} 之一，实为 {mode!r}")
    for seg, why in (("verified", "验了什么"), ("not_verified", "没验什么、为什么")):
        if _blank(blk.get(seg)):
            problems.append(f"{task_id}: `qa_signoff.{seg}` 缺失或为空——少了「{why}」，"
                            f"读者判断不了这次签发覆盖到哪儿")
    for end in ("since", "diff_ref"):
        if _blank(blk.get(end)):
            problems.append(f"{task_id}: `qa_signoff.{end}` 缺失——否决项按 since..diff_ref 重算，"
                            f"缺一端就没有可重算的切片")
    if mode == MODE_INDEP:
        ver = blk.get("verifier")
        if not isinstance(ver, dict) or _blank(ver.get("who")) or _blank(ver.get("report")):
            problems.append(f"{task_id}: 独立验证类必须写 `verifier: {{who: …, report: …}}`"
                            f"——谁验的、报告在哪（路径或会话 id）。本 hook 只查有没有写")
    if mode == MODE_USER and _blank(blk.get("approval")):
        problems.append(f"{task_id}: 「你拍板」必须写 `approval`——用户批准的原话或时间与范围")
    if _blank(task.get("verified_ranges")):
        problems.append(f"{task_id}: 缺 `verified_ranges`——增量复核的前提是上一轮的签发范围被"
                        f"记录；不写它，下一轮只能退回全量复核或凭印象划范围")
    return problems


def recompute(project_dir, blk):
    """按签注声明的切片两端重算否决项。返回 (result, err)。**不吃写入内容里的任何结论。**"""
    if stc is None:
        return None, f"判定脚本 import 失败：{_IMPORT_ERR}"
    try:
        delta, errors = stc.collect_slice(project_dir, blk.get("since"), blk.get("diff_ref"))
        if errors:
            return None, "；".join(errors)
        return stc.evaluate(delta, stc.DEFAULT_SIZE_CAP, stc.runtime_protected(project_dir)), None
    except Exception as e:
        return None, f"重算抛异常：{type(e).__name__}: {e}"


def judge(project_dir, task_id, task, enabled, volatile=None):
    """一条在查范围内的任务。返回问题列表，空 = 通过。

    `volatile`：给一个列表时，重算**跑不起来**（hash 解析不到、仓不在、git 出错……）就把 task_id
    记进去。那类结论随环境变（补拉一次提交就好了），不是看板内容的函数，整板缓存不许存它。"""
    blk = task.get("qa_signoff")
    problems = check_schema(task_id, task, blk)
    if problems:
        return problems
    mode = blk["mode"]
    if mode == MODE_SELF and not enabled:
        return [f"{task_id}: 写了 `mode: {MODE_SELF}`，但本项目**显式关闭**了自签开关"
                f"（`.workframe-config.json` 的 `signoff.self_signoff_enabled: false`）——研发任务"
                f"一律按「{MODE_INDEP}」签发。要改请动那个配置，**不要改这一行类别**"]
    if mode == MODE_USER and not enabled:
        ver = blk.get("verifier")
        if not isinstance(ver, dict) or _blank(ver.get("who")) or _blank(ver.get("report")):
            return [f"{task_id}: 本项目关闭了自签开关，研发任务一律要经独立验证——交用户拍板"
                    f"不免除这一条，`{MODE_USER}` 也要写 `verifier: {{who: …, report: …}}`"]
    res, err = recompute(project_dir, blk)
    if err:
        if volatile is not None:
            volatile.append(task_id)
        return [f"{task_id}: 否决项重算不起来（{err}）。**验不了的签发不等于验过了**——"
                f"两端要写成十六进制 commit、按仓给全（根仓写 `.`），且都已提交"]
    if mode == MODE_USER:
        return []
    veto = res["veto"]
    if veto["hit"]:
        return [f"{task_id}: 重算命中否决项 {veto['hit']} → 必须交用户拍板（`mode: {MODE_USER}` "
                f"＋ `approval`）。本 hook 只吃切片两端自己重跑，改类别改不动这个结果"]
    if res["pending"] and _blank(blk.get("ruling")):
        return [f"{task_id}: 重算得出 {len(res['pending'])} 条待裁决项——裁决之前不是终局："
                f"要么交用户拍板，要么在 `ruling` 里逐条写出裁决（拿不准一律算命中）："
                f"{'；'.join(res['pending'])[:300]}"]
    return []


def _fingerprint(task):
    """签注这一条自己的指纹。**不含自签开关**：开关改了不等于签注改了，算进来的话关一次开关，
    此前通过、原样未动的自理签注会在下一次写看板时全部重判、全部被拦。"""
    body = {"s": task.get("qa_signoff"), "st": task.get("status"),
            "ca": str(task.get("completed_at")), "vr": task.get("verified_ranges"),
            "dev": _surely_dev_task(task)}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _snapshot_doc(project_dir):
    """快照文件原样读成 dict；不存在、读坏、顶层不是映射一律返回 None。"""
    p = state_dir_of(Path(project_dir)) / SNAPSHOT_NAME
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _snapshot_tasks(doc):
    """返回 (tasks_map, first_run)。读坏了按首次运行处理（只建基线、不拦）。"""
    if isinstance(doc, dict) and isinstance(doc.get("tasks"), dict) \
            and doc.get("version") == SNAPSHOT_VERSION:
        return doc["tasks"], False
    return {}, True


def load_snapshot(project_dir):
    """返回 (tasks_map, first_run)。读坏了按首次运行处理（只建基线、不拦）。"""
    return _snapshot_tasks(_snapshot_doc(project_dir))


def save_snapshot(project_dir, tasks_map, board_check=None):
    """`board_check` 是整板缓存（见 `guard`），与签注快照写在同一个文件、同一次原子写里——
    两者永远出自同一次完整检查；不给就不写，下一次按「无缓存」走完整检查。"""
    doc = {"version": SNAPSHOT_VERSION, "schema_since": SCHEMA_SINCE, "tasks": tasks_map}
    if board_check is not None:
        doc["board"] = board_check
    try:
        atomic_write(state_dir_of(Path(project_dir)) / SNAPSHOT_NAME,
                     json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        return None
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def _context_key(project_dir, rule_dir=None):
    """除看板本身以外、会改变检查结论的输入，压成一个摘要：快照版本与截止日、两份规则文件的
    内容、`.workframe-config.json` 的内容（看板位置、自签开关）、否决项 6 的运行态路径（问
    `signoff_tier_check` 现算）。任何一项读不出来就抛——调用方按「缓存不可用」走完整检查。

    **不在内的输入**：git 里的提交。签注两端钉的是十六进制 commit，同一对 hash 的重算结果不变；
    重算跑不起来的那类结论（随环境变）由 `guard` 拒绝缓存，不靠这里。"""
    rule_dir = Path(rule_dir or _SCRIPTS_DIR)
    h = hashlib.sha256(f"{SNAPSHOT_VERSION}|{SCHEMA_SINCE}".encode("utf-8"))
    for name in _RULE_FILES:
        h.update(b"\0rule\0" + name.encode("utf-8") + b"\0")
        h.update((rule_dir / name).read_bytes())
    cfg = Path(project_dir) / ".workframe-config.json"
    h.update(b"\0cfg\0")
    h.update(cfg.read_bytes() if cfg.is_file() else b"<absent>")
    h.update(b"\0runtime\0")
    h.update(json.dumps(stc.runtime_protected(project_dir) if stc is not None else None,
                        sort_keys=True, default=str).encode("utf-8"))
    return h.hexdigest()


def _cached_check(doc, digest, ctx):
    """整板缓存命中则返回 (problems, notes)，否则 None。任何一处形态不对都按未命中处理。"""
    if ctx is None:
        return None
    _, first_run = _snapshot_tasks(doc)
    if first_run:
        return None
    c = doc.get("board")
    if not isinstance(c, dict) or c.get("sha256") != digest or c.get("ctx") != ctx:
        return None
    probs, notes = c.get("problems"), c.get("notes")
    if not (isinstance(probs, list) and isinstance(notes, list)
            and all(isinstance(x, str) for x in probs + notes)):
        return None
    return list(probs), list(notes)


def guard(project_dir, board):
    """返回 (problems, notes, passed)。

    problems —— 本次新出现或改过的签注的问题，非空 ⇒ 调用方决定拦还是只报
    notes    —— 存量问题与运行提示，只报
    passed   —— 本次新出现或改过且通过的 [(task_id, mode, verifier_who)]（不写事件，供单测断言）

    **整板缓存**（抬头 §整板缓存）：看板字节的 SHA-256 与 `_context_key` 都与上一次完整检查
    相同 ⇒ 不解析，原样返回上一次的 problems 与存量 notes（**查过但没过的照报**，不当通过）；
    passed 为空——「签注通过」只在那条签注新出现或改过的那一次发生。
    """
    problems, notes, passed = [], [], []
    try:
        raw = board.read_bytes()
    except OSError as e:
        notes.append(f"[signoff-guard] 跳过：看板读不出来（{type(e).__name__}）。本次未做任何签发校验")
        return problems, notes, passed
    digest = hashlib.sha256(raw).hexdigest()
    try:
        ctx = _context_key(project_dir)
    except Exception:
        ctx = None      # 算不出来 ⇒ 这一次不读也不写缓存，走完整检查
    doc = _snapshot_doc(project_dir)
    hit = _cached_check(doc, digest, ctx)
    if hit is not None:
        # 开关写成非映射时的 stderr 点名不能因为命中缓存就消失（它不进 notes，是直接打印的）
        self_signoff_enabled(project_dir)
        return hit[0], hit[1], passed

    tasks, err = load_board(board, raw)
    if err:
        # 没有任务清单就没有签注可挡。为一个解析不了的看板阻断全部写入，
        # 代价远大于收益——但**必须说出来**，否则「本 hook 什么都没查」看着像通过。
        notes.append(f"[signoff-guard] 跳过：{err}。本次未做任何签发校验")
        return problems, notes, passed

    enabled = self_signoff_enabled(project_dir)
    snap, first_run = _snapshot_tasks(doc)
    new_snap = {}
    volatile = []      # 重算跑不起来的任务——有一条，这次结论就不进缓存
    stale = []         # 存量问题的 notes：下一次同一份看板仍会原样报出的那部分
    for t in tasks:
        if not in_scope(t):
            continue
        task_id = str(t.get("id", "<无 id>"))
        fp = _fingerprint(t)
        prev = snap.get(task_id) if isinstance(snap.get(task_id), dict) else None
        if prev and prev.get("h") == fp and prev.get("ok") is True:
            new_snap[task_id] = prev
            continue
        probs = judge(project_dir, task_id, t, enabled, volatile)
        if first_run:
            # 首次建快照时分不出存量与本次写入，只有「今天完成的」认作本次——否则当次写进来的
            # 违规签注会被当成存量，从此只报不拦
            changed = str(t.get("completed_at")).strip()[:10] >= _today()
        else:
            changed = prev is None or prev.get("h") != fp
        if not probs:
            new_snap[task_id] = {"h": fp, "ok": True}
            if changed:
                blk = t.get("qa_signoff") or {}
                ver = blk.get("verifier") if isinstance(blk.get("verifier"), dict) else {}
                passed.append((task_id, blk.get("mode"), ver.get("who")))
                notes.append(f"[signoff-guard] {task_id}: 签注通过（{blk.get('mode')}）")
        elif changed:
            problems.extend(probs)
            # 不写进快照：改回之前它一直算「本次改出来的」
            if prev:
                new_snap[task_id] = prev
        else:
            new_snap[task_id] = {"h": fp, "ok": False}
            tag = "首次建快照" if first_run else "存量，原样未动"
            stale.extend(f"[signoff-guard] （{tag}，未阻断）{p}" for p in probs)
    notes.extend(stale)

    # 只缓存「同一份看板、同一套规则与配置下，再查一遍必然得到同样结果」的结论：
    # 首次建快照那次不算（下一次的口径是「存量」而不是「首次」），重算跑不起来的不算（随环境变）
    cache = None
    if ctx is not None and not first_run and not volatile:
        cache = {"sha256": digest, "ctx": ctx, "problems": problems, "notes": stale}
    serr = save_snapshot(project_dir, new_snap, cache)
    if serr:
        notes.append(f"[signoff-guard] 快照写不进去（{serr}）——下次仍会全量重算，"
                     f"存量问题与新问题分不开")
    return problems, notes, passed

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


def _deliver_skill_read(payload, project_dir):
    """顺带：shell 读到某个 skill 的 `SKILL.md` → 投该 skill 映射到的角色记忆（主会话角色记忆投递的 Codex 门入口）。

    **为什么在这里**：Codex 没有 Skill 工具，模型选用 skill 是用 shell 读 `SKILL.md`；PostToolUse(Bash) 上现成的
    hook 只有本脚本，并进来不新起进程（新挂一条 Bash hook，每条 shell 命令都多一个进程）。识别与判定全在
    `_role_memory`（`skills_read_by_shell` / `deliver_for_skills`），本函数只做取命令与输出。
    **与签发把关互不相干**：本脚本的 stderr 与退出码照旧，stdout 此前为空、现在只放这一串 JSON；命令里没有
    `SKILL.md` 字面时不 import 加载器、零开销。任何异常都吞掉——投递失败不能改变把关的结果。
    """
    try:
        cmd = (payload.get("tool_input") or {}).get("command")
        if not isinstance(cmd, str) or "skill.md" not in cmd.lower():
            return
        import _role_memory
        skills = _role_memory.skills_read_by_shell(cmd, project_dir, payload.get("cwd"))
        text = _role_memory.deliver_for_skills(project_dir, payload, skills, _role_memory.TRIGGER_SKILL_READ)
        if text:
            print(_role_memory.hook_output("PostToolUse", text))
    except Exception:
        pass


def _edits_board(payload, project_dir, board):
    """这次编辑工具调用改到的文件里有没有看板。

    - CC 的编辑工具：`tool_input.file_path` / `notebook_path`，恰一个（不看工具名，matcher 已经筛过）
    - Codex 的 `apply_patch`：载荷**没有** `file_path`，只有补丁文本；从补丁头取路径，一次可改多个文件，
      解析与 check-stale-modules 共用 `_module_paths.edited_paths`（移动取前后两个路径）
    相对路径按载荷 `cwd` 解析（补丁头可能相对会话目录），没有 `cwd` 按项目根。
    """
    tool_in = payload.get("tool_input") or {}
    if payload.get("tool_name") == _module_paths.PATCH_TOOL:
        paths = _module_paths.edited_paths(payload)
    else:
        fpath = (tool_in.get("file_path") or tool_in.get("notebook_path")) if isinstance(tool_in, dict) else None
        paths = [fpath] if isinstance(fpath, str) and fpath else []
    cwd = payload.get("cwd")
    base = Path(cwd) if isinstance(cwd, str) and cwd.strip() else Path(project_dir)
    for p in paths:
        q = Path(p)
        if not q.is_absolute():
            q = base / q
        if q.resolve() == board:
            return True
    return False


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
        _deliver_skill_read(payload, project_dir)

    if advisory:
        # Bash 侧：判不出这条命令动没动看板，所以**不看命令看结果**。
        # 先过便宜的前置过滤；过了之后看板没变就走 guard 里的整板缓存，不重新解析。
        try:
            board = board_path(project_dir)
            if not board.is_file() or not board_may_have_mapping_signoff(board):
                return PASS
        except Exception:
            return PASS
    else:
        try:
            board = board_path(project_dir)
            if not _edits_board(payload, project_dir, board):
                return PASS
        except Exception:
            return PASS

    try:
        problems, notes, passed = guard(project_dir, board)
    except Exception as e:
        # 本 hook 自身出错**不阻断**，但要留痕——静默失败与通过在终端里同形
        print(f"[signoff-guard] 内部错误，本次未做签发校验：{type(e).__name__}: {e}",
              file=sys.stderr)
        append_event(project_dir, "signoff_guard_error",
                     error=f"{type(e).__name__}: {str(e)[:200]}")
        return PASS

    for n in notes:
        print(n, file=sys.stderr)
    # 通过的签注不写事件（只记成功的流水没有读者）；`passed` 仍由 guard 返回，供单测与将来的消费方
    if problems:
        if advisory:
            # **只报不拦**：经 Bash 写入时判不出这条命令动没动看板，拦它就会误伤
            # 每一条与看板无关的命令。缺口因此仍在——但不再静默。
            print("[signoff-guard] ⚠ 看板签注有问题，**本次未阻断**"
                  f"（经 {tool_name} 写入，判不出这条命令动没动看板）。"
                  "看板当前处于违规状态，请修：", file=sys.stderr)
            for p in problems:
                print(f"  ! {p}", file=sys.stderr)
            return PASS
        print("[signoff-guard] 看板签注未通过校验，本次写入被拒："
              "（本 hook 按签注的切片两端自己重算否决项，不读你写的结论）", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        return BLOCK
    return PASS


if __name__ == "__main__":
    # 本 hook 的非零退出**就是它的功能**——PostToolUse 上 exit 2 阻断该次工具调用，
    # 正是「看板签注未通过校验时拒绝写入」的实现方式。阻断面收得很窄：只有写入对象
    # 恰是本项目看板、且有**本次新出现或改过**的在查签注出了问题时才可能返回 2；
    # 本 hook 自身出错、看板解析不了、存量问题、首次建快照时今天之前完成的，一律 exit 0（见抬头的阻断策略
    # 表）。截止日之前完成的签注不在查范围内，不会被它拦。
    # exit-audited: 阻断是本 hook 的功能本身，阻断面与豁免面见上方五行与抬头策略表
    sys.exit(main())
