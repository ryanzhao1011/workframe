#!/usr/bin/env python3
"""
Setup Hook (matcher=maintenance) — 维护批处理工单聚合器（v0.4 G2#8）

`claude -p --maintenance` 会话启动前由 CC 触发（bin/workframe-maintenance 是标准入口），
把三层保养事项聚合成一份工单文件，模型照单执行。聚合 = 代码（确定性），执行 = 模型
（内容判断）——分工见框架收口计划总原则。

聚合来源（四路）：
  1. notes 积压（count_notes_entries，与 memory-ask.py 逐字一致，validate.py 锁定）
  2. promotion-candidates.md 未拍板候选（只计数提示，批处理会话不做 L2）
  3. 逾期提案验证（applied/*.yaml：verify_by 已过且 verified 非 true）
  4. pending_maintenance open 信号 + workframe-doctor 全量检查异常项

产物：
  - .claude/workframe-state/maintenance-workorder.md   工单（模型读取执行）
  - .claude/workframe-state/maintenance-run.flag       旗标（memory-ask.py 据此静默，
    30 分钟有效期，无需显式清理——2026-08-06 实测 --maintenance 会话中 SessionStart
    hook 照常触发，不静默会与工单重复询问）

两阶段提交（judge → commit，2026-08-06 深测后定型）：
  模型会话只做内容判断与 agent-memory 写盘，把全部记账诉求写成 manifest
  （logs/maintenance-commit.json，logs/ 非敏感可写）；wrapper 在会话结束后调本脚本
  `--commit` 用代码统一提交——sidecar entry + memory_promoted/skill_used/dismissed
  事件 + 关 PM 信号 + 工单打勾。记账=代码，与框架设计法则一致。

实测依据（2026-08-06 沙盒，勿回退）：
  - Setup hook 的 stdout（JSON additionalContext 与纯文本）均不注入模型上下文
    —— 工单必须落文件、由 -p prompt 引导模型读取
  - headless 会话对 `.claude/workframe-state/**` 的写入被 CC 判为敏感文件并硬拒，
    `Edit(path)` allow 规则也压不过（`Write(path)` 规则更是无效语法）——所以记账
    只能走会话外代码提交；`.claude/agent-memory/**` 的 md 实测可写
  - -p 会话下 Skill 工具不注入正文 → 工单指挥模型直接 Read librarian SKILL.md
    （wrapper 用 --add-dir 授予 plugin 目录读权限）
"""

import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

# 同目录公共模块：原子写与损坏隔离（见 _state_io.py 抬头）
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from _state_io import atomic_write, load_activity, quarantine, update_activity  # noqa: E402


PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
STATE_DIR = PROJECT_DIR / ".claude" / "workframe-state"
ACTIVITY_FILE = STATE_DIR / "activity-state.json"
MEMORY_DIR = PROJECT_DIR / ".claude" / "agent-memory"
CANDIDATES_FILE = STATE_DIR / "promotion-candidates.md"
PROPOSALS_APPLIED = PROJECT_DIR / "projects" / "proposals" / "applied"
WORKORDER_FILE = STATE_DIR / "maintenance-workorder.md"
MAINT_FLAG_FILE = STATE_DIR / "maintenance-run.flag"
EVENTS_FILE = STATE_DIR / "events.jsonl"
SIDECAR_FILE = STATE_DIR / "memory-index.json"
MANIFEST_FILE = PROJECT_DIR / "logs" / "maintenance-commit.json"
MANIFEST_APPLIED = PROJECT_DIR / "logs" / "maintenance-commit.applied.json"


def count_notes_entries(memory_dir):
    """数各角色 notes.md 的积压条目数——`### ` 章节头与顶层日期列表条目两种口径取 max。

    真实写入存在两种条目形态（rules 不约束 notes 格式）：`### ` 章节式与
    `- YYYY-MM-DD：` 顶层列表式（含 `- [纠正] …` 变体）。二者取 max：
    漏计 = 积压对真实数据失明（高危）；过计只是提早询问（fail-safe 方向）。
    返回 {scope: count}（仅收录 count>0）。纯前缀统计，无语义判断——
    条目够不够提升、是不是同主题，交给 librarian skill 判断。
    本函数在 memory-ask.py 与 maintenance_workorder.py 各有一份，validate.py 锁定逐字一致。
    """
    out = {}
    if not memory_dir.exists():
        return out
    for notes in sorted(memory_dir.glob("*/notes.md")):
        try:
            text = notes.read_text(encoding="utf-8")
        except Exception:
            continue
        n_hash = n_list = 0
        for ln in text.splitlines():
            if ln.startswith("### "):
                n_hash += 1
            elif re.match(r"^- (?:\[[^\]]+\] )?\d{4}-\d{2}-\d{2}", ln):
                n_list += 1
        n = max(n_hash, n_list)
        if n > 0:
            out[notes.parent.name] = n
    return out


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def count_pending_candidates():
    """promotion-candidates.md 中未拍板候选数（`- [ ]` 行）。文件不存在 → 0。"""
    if not CANDIDATES_FILE.exists():
        return 0
    try:
        text = CANDIDATES_FILE.read_text(encoding="utf-8")
    except Exception:
        return 0
    return sum(1 for ln in text.splitlines() if ln.strip().startswith("- [ ]"))


def find_overdue_proposals():
    """applied/*.yaml 中 verify_by 已过期且 verified 非 true 的提案。轻量正则，不引 yaml 依赖。"""
    out = []
    if not PROPOSALS_APPLIED.is_dir():
        return out
    today = datetime.now().date().isoformat()
    for f in sorted(PROPOSALS_APPLIED.glob("*.yaml")):
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        m_by = re.search(r"""^\s*verify_by\s*:\s*["']?(\d{4}-\d{2}-\d{2})""", text, re.M)
        # verified 有三态：null / 缺省 = 待验证；true = 验证通过；**false = 验证失败**。
        # true 和 false 都是**终态**（project-architecture.md §projects/proposals）——
        # 只排除 true 会让验证失败的提案永远挂在「逾期待验证」清单里，而它需要的是
        # 一个新提案，不是把同一份再核验一遍。
        m_done = re.search(r"^\s*verified\s*:\s*(?:true|false)\b", text, re.M)
        if m_by and not m_done and m_by.group(1) < today:
            out.append((f.stem, m_by.group(1)))
    return out


def run_doctor():
    """子进程跑 workframe_doctor.py --json（G1#4 设计的对接口），返回非 ok 的 (check_id, level, msg) 列表。

    走子进程而非 import：`--json` 是 doctor 对外的稳定接口，子进程还能隔离它的退出码与
    异常。（2026-08-10 起 doctor import 已无副作用——原先模块级包 TextIOWrapper 会
    双重包装 → 旧包装 GC 时连带关闭底层 buffer → 本脚本后续 print 崩（2026-08-06 一检实证）。
    """
    doctor = Path(__file__).resolve().parent / "workframe_doctor.py"
    try:
        r = subprocess.run([sys.executable, str(doctor), "--json"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=120)
        results = json.loads(r.stdout).get("checks", [])
    except Exception as e:
        return [("doctor", "error", f"doctor 运行失败: {e.__class__.__name__}: {e}")]
    out = []
    for r in results:
        for f in r.get("findings", []):
            if f.get("level") != "ok":
                out.append((r.get("id", "?"), f.get("level", "?"), f.get("msg", "")))
    return out


def build_workorder():
    now = datetime.now(timezone.utc).astimezone().isoformat()
    backlog = count_notes_entries(MEMORY_DIR)
    candidates = count_pending_candidates()
    overdue = find_overdue_proposals()
    activity = load_activity(STATE_DIR)  # 走统一层：损坏时隔离留档 + 字段补齐
    pending = [it for it in (activity.get("pending_maintenance") or [])
               if isinstance(it, dict) and it.get("status") == "open"]
    doctor_findings = run_doctor()

    lines = [
        "# workframe 维护工单",
        "",
        f"> generated_at: {now}",
        "> 生成器: maintenance_workorder.py（Setup hook, matcher=maintenance）",
        "> 本文件由代码全量重写，勿手工长期维护；执行痕迹保留到下次生成前。",
        "",
        "## 执行规约（先读再动手）",
        "",
        "- 本会话为非交互批处理（print 模式）：只执行 **L1**（角色记忆整理/归档）与记录性操作。",
        "- **L2 一律不动**（rules / CLAUDE.md / skills / 删除 / 降级 / 受保护资产）——只在结果里提示用户。",
        "- 文件操作用 Read/Edit/Write 工具；**不要使用 Bash**（本会话无法批准权限）。",
        "- `.claude/workframe-state/` 本会话**不可写**（CC 敏感文件闸，实测）：sidecar / events /",
        "  关信号 / 本工单打勾一律**不要自己写**——把记账诉求写进 manifest（见文末格式），",
        "  会话结束后由 wrapper 代码统一提交。`.claude/agent-memory/` 下 MEMORY / notes /",
        "  notes-archive **可正常写**（实测）。",
        "- 全部处理完后：先 Write manifest 到 `logs/maintenance-commit.json`，再输出执行摘要",
        "  （做了什么 / 跳过什么及原因 / 需用户拍板什么）。",
        "",
        "## 1. notes 积压（按 librarian SOP 执行 L1 流程）",
        "",
    ]
    if backlog:
        skill_md = Path(__file__).resolve().parent.parent / "skills" / "librarian" / "SKILL.md"
        lines.append(f"先 Read `{skill_md}` 获取完整 SOP——**不要用 Skill 工具调用**"
                     f"（-p 会话下 Skill 工具不注入正文，2026-08-06 实测），严格按文中流程执行：")
        lines.append("D/U/R/A 评估 → 融合 SOP → **已处理条目整段移入同目录 notes-archive.md（归档制，勿直接删）** → "
                     "每条提升以 {scope, summary, provenance 来源归类} 记入 manifest `promotions`，对应的 memory_backlog 信号 ID 记入 `close_pm`"
                     "（sidecar 与事件由 --commit 代码生成，勿手写）。")
        for scope, n in sorted(backlog.items()):
            lines.append(f"- [ ] {scope}：{n} 条待评估（L1 提升/归档照常；L2 候选记入 manifest `l2_candidates`——"
                         f"promotion-candidates.md 也在 workframe-state 下本会话不可写，由 --commit 落盘）")
    else:
        lines.append("（无积压）")

    lines += ["", "## 2. 待拍板候选（仅提示，本会话不处理）", ""]
    if candidates:
        lines.append(f"promotion-candidates.md 现有 {candidates} 条未拍板候选——在执行摘要中提醒用户于交互会话拍板。")
    else:
        lines.append("（无待拍板候选）")

    lines += ["", "## 3. 逾期提案验证", ""]
    if overdue:
        for stem, by in overdue:
            lines.append(f"- [ ] {stem}：verify_by {by} 已逾期——读提案内 verify_signal 逐条核验，"
                         f"回写 verified 字段（projects/ 可写）+ 把 proposal_verified 事件对象（按 event-schema）"
                         f"记入 manifest `extra_events`")
    else:
        lines.append("（无逾期提案）")

    lines += ["", "## 4. pending_maintenance open 信号", ""]
    if pending:
        lines.append("close_pm 判据（仅此一条，勿自行扩展）：**本次工单已执行的动作实质消解了该信号的语义**"
                     "（例：memory_backlog 信号 + 本次已清完对应 notes 积压）→ ID 记入 manifest `close_pm`；"
                     "cadence_timeout / problem_threshold / activity_threshold 等自迭代节奏信号**不由批处理关闭**"
                     "（它们等的是自迭代评审，不是维护动作）——保留并在摘要提示用户。")
        for it in pending:
            lines.append(f"- [ ] {it.get('id', '?')}（{it.get('kind', '?')}, {it.get('severity', '?')}）"
                         f"{it.get('details', '')} —— 按上方判据处置（关闭与 dismissed 事件由 --commit 生成）")
    else:
        lines.append("（无 open 信号）")

    lines += ["", "## 5. doctor 异常项", ""]
    if doctor_findings:
        lines.append("处置判据：仅 `.claude/agent-memory/` 下可写文件的格式/内容问题可当场修复（数据修复类）；"
                     "涉及 schema / 脚本 / skills / 权限 / workframe-state 数据的一律只报告不动手（机制类）。")
        for cid, level, msg in doctor_findings:
            lines.append(f"- [ ] [{cid}] {level}: {msg} —— 按上方判据处置")
    else:
        lines.append("（doctor 全绿）")

    lines += ["", "## manifest 格式（Write 到 logs/maintenance-commit.json，所有键可省略）", "",
              "```json", "{",
              '  "promotions": [{"scope": "pm", "summary": "<提升条目一句话摘要>", "provenance": "<可选：user-confirmed|inferred|external；**缺省 inferred**——user-confirmed 在衰减规则里等同用户背书、享受豁免，只在用户当场确认过时才填>"}],',
              '  "l2_candidates": [{"scope": "pm", "summary": "<候选一句话>", "source": "<notes 条目>", "target": "<建议落点文件+章节>"}],',
              '  "close_pm": ["PM-YYYYMMDD-NNN"],',
              '  "extra_events": [{"type": "proposal_verified", "...": "按 event-schema 补全字段, ts 可省略"}],',
              '  "done": ["pm", "PM-YYYYMMDD-NNN", "<其他已完成条目的标识子串>"],',
              '  "notes": "<一句执行说明，记入工单执行记录>"', "}", "```"]

    return "\n".join(lines) + "\n"


def _normalize_key_fragment(summary):
    """entry_key 第三段：**先删全部空白、再取前 20 字**（Unicode）——与 librarian /
    correction-detection 的 key 规则同序（顺序颠倒时，前 20 字含空白的摘要会算出
    不同 key，跨 producer 按 key supersede 会 miss）。

    2026-08-16 改序：此前本函数是「先截断后去空白」，而实际落盘的条目用的是反序，
    两边对同一条目算出不同 key。以落盘行为为准统一——先去空白能拿到正好 20 个有效
    字符，信息量更大、碰撞率更低。历史 key 不回改（events 只追加不改写）。"""
    return "".join(summary.split())[:20]


def _unique_key(base, existing):
    key, n = base, 2
    while key in existing:
        key = f"{base}-{n}"
        n += 1
    return key


def _append_events(events):
    if not events:
        return
    prefix = ""
    if EVENTS_FILE.exists():
        try:
            tail = EVENTS_FILE.read_bytes()[-1:]
            if tail and tail != b"\n":
                prefix = "\n"
        except Exception:
            pass
    with open(EVENTS_FILE, "a", encoding="utf-8", newline="") as f:
        f.write(prefix + "".join(json.dumps(ev, ensure_ascii=False) + "\n" for ev in events))


def commit_manifest():
    """两阶段提交的 commit 端：读模型产出的 manifest，用代码完成全部记账。

    headless 会话写不了 .claude/workframe-state/**（CC 敏感文件闸，2026-08-06 实测），
    且记账本就该归代码（设计法则）——sidecar / 事件 / 关信号 / 工单打勾统一在此执行。
    """
    if not MANIFEST_FILE.exists():
        print("[maintenance --commit] 无 manifest（模型未产生记账诉求），跳过。")
        return 0
    try:
        mani = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[maintenance --commit] manifest 解析失败: {e}", file=sys.stderr)
        return 1

    ok, err = _events_writable()
    if not ok:
        print(f"[maintenance --commit] events.jsonl 不可写（{err}），未提交任何记账——"
              f"避免 sidecar 记了 promotion 而事件缺失。manifest 保留可重跑。",
              file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = datetime.now().date().isoformat()
    events_out = []
    summary_bits = []
    # 提交前的原始快照：事件写失败时据此把 sidecar 回滚，避免「sidecar 记了 promotion
    # 但没有 memory_promoted 事件」这种三账不一致（预检挡不住预检之后的失败）
    sidecar_backup = SIDECAR_FILE.read_bytes() if SIDECAR_FILE.exists() else None

    # 1. promotions → sidecar entry + memory_promoted 事件（+1 条 skill_used）
    promos = [p for p in (mani.get("promotions") or [])
              if isinstance(p, dict) and p.get("scope") and (p.get("summary") or "").strip()]
    if promos:
        # sidecar 损坏时 fail-closed：这份文件里有 [纠正] 条目的 protected 标记，
        # 拿一个空索引覆写等于把用户亲口纠正过的记忆保护静默抹掉——而同一份损坏
        # 在 workframe_doctor 那边是报 error 的，两个消费方不该一个报错一个照写。
        # 停下来让人看见，比"看起来跑成功了"强。
        idx = {"__schema__": "workframe.memory-index.v2", "entries": {}}
        if SIDECAR_FILE.exists():
            try:
                loaded = json.loads(SIDECAR_FILE.read_text(encoding="utf-8-sig"))
            except Exception as e:
                dest = quarantine(SIDECAR_FILE)
                print(f"[maintenance --commit] memory-index.json 解析失败: {e}\n"
                      f"  已隔离为 {dest.name if dest else '（隔离失败，原文件仍在）'}；"
                      f"未写入任何 promotion，manifest 保留可重跑。\n"
                      f"  修复 sidecar 后重跑 --commit；或跑 /core:audit 查看记忆流水。",
                      file=sys.stderr)
                return 1
            if not isinstance(loaded, dict) or not isinstance(loaded.get("entries", {}), dict):
                dest = quarantine(SIDECAR_FILE)
                print(f"[maintenance --commit] memory-index.json 结构非法"
                      f"（顶层或 entries 不是对象）\n"
                      f"  已隔离为 {dest.name if dest else '（隔离失败，原文件仍在）'}；"
                      f"未写入任何 promotion，manifest 保留可重跑。",
                      file=sys.stderr)
                return 1
            idx = loaded
        entries = idx.setdefault("entries", {})
        for p in promos:
            scope, summary = p["scope"], p["summary"].strip()
            prov = p.get("provenance")
            if prov not in ("user-confirmed", "inferred", "external"):
                # 缺省取 inferred 而非 user-confirmed：后者在衰减规则里等同「用户确认过」
                # 享受豁免，把「manifest 没写这个字段」悄悄升级成「用户背书过」。
                # 批处理是模型判断的产物，inferred 才是它的真实来源等级
                # （user-decree 仅 correction-detection 写，批处理不产）。
                prov = "inferred"
            key = _unique_key(f"{scope}:{today}:{_normalize_key_fragment(summary)}", entries)
            entries[key] = {"scope": scope, "created_at": today, "provenance": prov,
                            "protected": False, "source": "notes.md"}
            ev = {"ts": now, "type": "memory_promoted", "scope": scope,
                  "entry_key": key, "summary": summary[:80], "source": "notes.md",
                  "protected": False, "provenance": prov}
            if scope != "shared":
                ev["role"] = scope
            events_out.append(ev)
        atomic_write(SIDECAR_FILE, json.dumps(idx, ensure_ascii=False, indent=2))
        events_out.append({"ts": now, "type": "skill_used", "skill": "librarian",
                           "role": "main", "success": True})
        summary_bits.append(f"promotions {len(promos)} 条（sidecar+事件）")

    # 1.5 l2_candidates → 追加 promotion-candidates.md（行格式与 librarian SKILL 固定格式一致；
    #     批处理会话写不了 workframe-state，落盘归代码——2026-08-06 D2 实测抓出的缺口）
    cands = [c for c in (mani.get("l2_candidates") or [])
             if isinstance(c, dict) and (c.get("summary") or "").strip()]
    if cands:
        header = ""
        if not CANDIDATES_FILE.exists():
            header = "# L2 提升候选（攒卡待用户拍板）\n\n> 由 librarian / maintenance --commit 追加；拍板后把对应行改 `- [x]`。\n\n"
        rows = "".join(
            f"- [ ] {c.get('scope', '?')} | {today} | {c['summary'].strip()} | "
            f"出处: {(c.get('source') or '—').strip()} | 建议落点: {(c.get('target') or '待定').strip()}\n"
            for c in cands)
        with open(CANDIDATES_FILE, "a", encoding="utf-8", newline="") as f:
            f.write(header + rows)
        summary_bits.append(f"l2_candidates {len(cands)} 条")

    # 2. close_pm → 关信号 + dismissed 事件
    ids = [i for i in (mani.get("close_pm") or []) if isinstance(i, str)]
    closed = []
    if ids:
        # 走统一状态层：这是「读 pending 列表 → 改状态 → 写回」的读改写，此前直接
        # write_text 整份覆盖，既不持锁也非原子——与四个 hook 并发时互相抹改动。
        # 本文件是 activity-state 的**第五个写入方**，而 state_io_single_source 闸
        # 当时只扫四个 hook，所以一直报绿。
        def _close_pm(s):
            closed.clear()
            for it in s.get("pending_maintenance") or []:
                if isinstance(it, dict) and it.get("id") in ids and it.get("status") == "open":
                    it["status"] = "closed"
                    it["closed_at"] = now
                    closed.append(it["id"])

        if update_activity(STATE_DIR, _close_pm) is None:
            # 必须**中止并保留 manifest**：早先只 print 一句就继续往下走，最后照常
            # 归档 manifest 并 return 0——于是 close_pm 没执行、manifest 却已被改名成
            # .applied，用户既看不出失败也无法重跑（Codex 2026-08-16 实测
            # commit_exit=0 / manifest_applied=True）。此时 promotions 可能已写进
            # sidecar，一并回滚。
            if sidecar_backup is not None:
                try:
                    atomic_write(SIDECAR_FILE, sidecar_backup.decode("utf-8"))
                except Exception:
                    pass
            elif SIDECAR_FILE.exists():
                try:
                    SIDECAR_FILE.unlink()
                except Exception:
                    pass
            print("[maintenance --commit] activity-state 更新失败，close_pm 未落盘——"
                  "已回滚本次 sidecar 改动，manifest 保留可重跑；原因见上一条 [warn]",
                  file=sys.stderr)
            return 1
        elif closed:
            for pm_id in closed:
                events_out.append({"ts": now, "type": "pending_maintenance_dismissed",
                                   "pm_id": pm_id, "at": now})
            summary_bits.append(f"close_pm {len(closed)} 条")

    # 3. extra_events → 校验后追加
    # manifest 由**模型**写，不能原样放行：type 拼错（`proposal_verifed`）或写个
    # schema 里没有的名字时，早先照样入库并返回 0，事件流被污染而 manifest 已归档、
    # 无从追溯（Codex 2026-08-16 实测 event_types=['not_in_schema'] + exit=0）。
    known = _known_event_types()
    for ev in mani.get("extra_events") or []:
        if not (isinstance(ev, dict) and ev.get("type")):
            continue
        if known and ev["type"] not in known:
            print(f"[maintenance --commit] extra_events 含 schema 未定义的事件类型 "
                  f"{ev['type']!r}——未提交任何记账，manifest 保留。"
                  f"改用 event-schema.json 里已登记的类型后重跑。", file=sys.stderr)
            if sidecar_backup is not None:
                try:
                    atomic_write(SIDECAR_FILE, sidecar_backup.decode("utf-8"))
                except Exception:
                    pass
            return 1
        # ts 一律覆盖为提交时刻的 UTC 秒级：放行 manifest 自带的时间戳等于让本地时区/
        # 微秒精度重新混进 events.jsonl，破坏刚统一的口径。这些事件本就是"此刻提交"的。
        ev["ts"] = now
        events_out.append(ev)
        summary_bits.append(f"extra_event:{ev['type']}")

    ev_ok, ev_err = _append_events_rollbackable(events_out)
    if not ev_ok:
        # 回滚本次全部账面改动，manifest 保留可重跑
        if sidecar_backup is not None:
            try:
                atomic_write(SIDECAR_FILE, sidecar_backup.decode("utf-8"))
            except Exception:
                pass
        elif SIDECAR_FILE.exists():
            try:
                SIDECAR_FILE.unlink()
            except Exception:
                pass
        if closed:
            def _reopen(s):
                for it in s.get("pending_maintenance") or []:
                    if isinstance(it, dict) and it.get("id") in closed:
                        it["status"] = "open"
                        it["closed_at"] = None
            update_activity(STATE_DIR, _reopen)
        print(f"[maintenance --commit] 事件写入失败（{ev_err}）——已回滚本次 sidecar 与"
              f"PM 状态改动，manifest 保留可重跑。三账保持一致。", file=sys.stderr)
        return 1

    # 4. 工单打勾 + 执行记录
    done = [d for d in (mani.get("done") or []) if isinstance(d, str) and d.strip()]
    if WORKORDER_FILE.exists():
        text = WORKORDER_FILE.read_text(encoding="utf-8")
        wl = text.splitlines()
        for label in done:
            for i, ln in enumerate(wl):
                if "- [ ]" in ln and label in ln:
                    wl[i] = ln.replace("- [ ]", "- [x]", 1)
                    break
        wl += ["", f"## 执行记录（--commit @ {now}）", "",
               f"- 记账：{'；'.join(summary_bits) if summary_bits else '无'}",
               f"- 事件追加：{len(events_out)} 条",
               f"- 说明：{(mani.get('notes') or '').strip() or '—'}"]
        WORKORDER_FILE.write_text("\n".join(wl) + "\n", encoding="utf-8", newline="")

    try:
        MANIFEST_FILE.replace(MANIFEST_APPLIED)
    except Exception:
        pass
    print(f"[maintenance --commit] 完成：{'；'.join(summary_bits) if summary_bits else '无记账诉求'}"
          f"；事件 {len(events_out)} 条；勾选 {len(done)} 项。")
    return 0


def _append_events_rollbackable(events):
    """追加事件；失败时把文件截回写入前的长度。返回 (ok, err)。

    `_events_writable()` 只是预检——它挡得住「一开始就不可写」，挡不住预检通过之后
    磁盘满 / 权限变化 / 文件被替换。而本函数的调用点之前**已经改过 sidecar 或
    activity-state**，事件写不进去就留下三账不一致的半截状态
    （Codex 2026-08-16 用 monkeypatch 注入写失败实证）。
    events.jsonl 是 append-only，所以回滚极简：截回原长度即完整撤销本次追加。
    """
    if not events:
        return True, None
    try:
        size_before = EVENTS_FILE.stat().st_size if EVENTS_FILE.exists() else 0
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"
    try:
        _append_events(events)
        return True, None
    except Exception as e:
        try:
            if EVENTS_FILE.exists():
                with open(EVENTS_FILE, "r+b") as f:
                    f.truncate(size_before)
        except Exception as re_:
            return False, f"{type(e).__name__}: {e}（回滚也失败: {type(re_).__name__}: {re_}）"
        return False, f"{type(e).__name__}: {e}"


def _known_event_types():
    """从 event-schema.json 读合法事件类型；读不到返回空集（此时跳过校验，不误伤）。"""
    try:
        f = Path(__file__).resolve().parent.parent / ".workframe-meta" / "event-schema.json"
        return set(json.loads(f.read_text(encoding="utf-8")).get("events", {}))
    except Exception:
        return set()


def _events_writable():
    """提交前确认 events.jsonl 可追加——**跨账本写入的前置闸**。

    sidecar / activity-state 与 events 是三本互相印证的账（三账对齐是本框架的
    核心不变量）。此前的顺序是「先改账、后写事件」，事件写失败时留下的是
    **已记 promotion 但无 memory_promoted 事件**、或**已 closed 但无 dismissed 事件**
    的半截状态，而 manifest 也没标完成，重跑还可能产生重复条目
    （2026-08-16 实测：把 events.jsonl 换成目录即可稳定复现）。
    先探一次可写性，把「写不进去」挡在改账之前——比事后回滚简单，也更可靠。
    """
    try:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_FILE.open("a", encoding="utf-8", newline=""):
            pass
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def set_activity_flags(**flags):
    """幂等赋值型字段的代码通道（当前用于 dormant / wake_up_pending / dormant_profile）。

    为什么连"幂等赋值"也要走代码：模型手上只有 Edit/Write，改一个布尔字段等于
    **整份 JSON 重写**——它会把自己读到的那一份全部写回去，期间别的 hook 写进来的
    session_counter / pending_maintenance / drift 历史统统被盖掉。字段本身幂等，
    写入方式并不幂等。update_activity 只改指定键、其余键保持磁盘现值。
    """
    allowed = {"dormant", "wake_up_pending", "dormant_profile"}
    bad = [k for k in flags if k not in allowed]
    if bad:
        print(f"[maintenance --set-activity] 不支持的字段: {bad}（仅允许 {sorted(allowed)}）",
              file=sys.stderr)
        return 2

    def _apply(s):
        s.update(flags)

    if update_activity(STATE_DIR, _apply) is None:
        print("[maintenance --set-activity] activity-state 更新失败，未改动；原因见上一条 [warn]",
              file=sys.stderr)
        return 1
    print(f"[maintenance --set-activity] 已更新: {flags}")
    return 0


def close_pending(ids, reason=None):
    """关闭指定 pending_maintenance 条目 + 写 dismissed 事件（锁内读改写）。

    给 **skill 侧**用的代码通道。此前 librarian（第 6 步）/ maintenance-review
    （模式 A 与模式 B 收尾）/ self-iteration（阶段 5 第 8 步与无提案分支）都指挥模型
    直接 Read → 改 → Write `activity-state.json`，那绕过了本文件为这份状态建立的全部
    保护：文件锁、原子替换、三方合并、strict_read fail-closed。
    比并发更现实的风险是**模型整份重写 JSON 时漏字段**——activity-state 里装着
    session_counter / pending_maintenance / recent_drift_repairs 这些一点点攒出来的
    状态，漏掉任何一个都不会报错，只会静默丢掉一段历史。

    用法：
        python maintenance_workorder.py --close-pm PM-20260816-001 [PM-...] [--reason <text>]
    """
    ok, err = _events_writable()
    if not ok:
        print(f"[maintenance --close-pm] events.jsonl 不可写（{err}），未关闭任何条目——"
              f"避免留下「已 closed 但无 dismissed 事件」的半截状态。修好后重跑。",
              file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    closed = []

    def _close(s):
        closed.clear()
        for it in s.get("pending_maintenance") or []:
            if isinstance(it, dict) and it.get("id") in ids and it.get("status") == "open":
                it["status"] = "closed"
                it["closed_at"] = now
                closed.append(it["id"])

    if update_activity(STATE_DIR, _close) is None:
        print("[maintenance --close-pm] activity-state 更新失败，未关闭任何条目；"
              "原因见上一条 [warn]，可重跑", file=sys.stderr)
        return 1
    events = []
    for pm_id in closed:
        ev = {"ts": now, "type": "pending_maintenance_dismissed", "pm_id": pm_id, "at": now}
        if reason:
            ev["reason"] = reason
        events.append(ev)
    ev_ok, ev_err = _append_events_rollbackable(events)
    if not ev_ok:
        # 事件没写成就把状态改回去：否则 PM 显示 closed 却查不到关闭原因，且无法重跑
        def _reopen(s):
            for it in s.get("pending_maintenance") or []:
                if isinstance(it, dict) and it.get("id") in closed:
                    it["status"] = "open"
                    it["closed_at"] = None
        update_activity(STATE_DIR, _reopen)
        print(f"[maintenance --close-pm] 事件写入失败（{ev_err}）——已把 "
              f"{len(closed)} 条 PM 状态回滚为 open，可重跑。", file=sys.stderr)
        return 1
    print(f"[maintenance --close-pm] 已关闭 {len(closed)} 条：{'、'.join(closed) or '无'}")
    missed = [i for i in ids if i not in closed]
    if missed:
        print(f"[maintenance --close-pm] 未命中（不存在 / 已是 closed）：{'、'.join(missed)}")
    return 0


def main():
    argv = sys.argv[1:]
    if "--commit" in argv:
        return commit_manifest()
    if "--wake-done" in argv:
        # maintenance-review 模式 B 收尾：结束 wake-up 状态、恢复常规维护链路
        return set_activity_flags(dormant=False, wake_up_pending=False)
    if "--close-pm" in argv:
        rest = argv[argv.index("--close-pm") + 1:]
        reason = None
        if "--reason" in rest:
            j = rest.index("--reason")
            reason = rest[j + 1] if j + 1 < len(rest) else None
            rest = rest[:j]
        ids = [x for x in rest if x.startswith("PM-")]
        if not ids:
            print("用法：--close-pm PM-YYYYMMDD-NNN [PM-...] [--reason <text>]", file=sys.stderr)
            return 2
        return close_pending(ids, reason)

    # Setup hook stdin 可能带 JSON（matcher 等），当前不消费；robust 吞掉即可
    try:
        if not sys.stdin.isatty():
            sys.stdin.read()
    except Exception:
        pass

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    MAINT_FLAG_FILE.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8", newline="")
    WORKORDER_FILE.write_text(build_workorder(), encoding="utf-8", newline="")
    print(f"[maintenance] 工单已生成: {WORKORDER_FILE}")
    return 0


if __name__ == "__main__":
    # main() 的非零 return 只出现在 --commit / --close-pm / --set-activity 这些 CLI 子命令上；
    # exit-audited: Setup hook 走末尾默认分支，恒 return 0
    sys.exit(main())
