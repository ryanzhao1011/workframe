#!/usr/bin/env python3
"""主会话角色记忆投递（`_role_memory.py` 与它的各个入口）的常驻单测。

入口全部子进程实跑（临时项目，`CLAUDE_PROJECT_DIR` 指过去；Codex 门的载荷带 `cwd`），看 stdout 与事件流：

1. **CC 的 skill 入口**（`log-skill-invoked.py`）：模型调 Skill 与用户直敲 `/skill` 都投、hookEventName 跟着事件走、
   stdout 是唯一一串纯 ASCII 的 JSON；同周期再调不投（skipped_dedup）；**投递判定在分钟级去重之前**；
   未映射不投（unmapped）；子线程不投（skipped_subthread），fork 只给一次指针、不记进父会话；记忆缺失可见「未加载」且不记已投；
   超预算给条目目录、可见「未完整加载」、长度在上限内；`--harness codex` 下同一载荷同样投（本模块不读门）。
2. **Codex 的 skill 入口**：shell 读 `SKILL.md`（`signoff-guard.py` 的 Bash 路径）与提示里的 `$skill`
   （`user-prompt-inject.py`）——投、去重、未映射、项目 skill、同形字面串不认、一条命令多个 skill 只投第一个可投的、
   子线程不投、`$skill` 与维护提醒合并成一次输出且不受「本会话已提醒」挡。
3. **路径入口**（`check-stale-modules.py`）：CC 编辑工具与 Codex `apply_patch`（四种补丁头、移动的前后两个路径、
   相对 / 绝对 / 中文空格路径、项目外路径）；未命中不投、子线程不投。
4. **压缩补投**（`role-memory-restore.py`）：按最近使用顺序补投（去重命中也算使用）、周期号 +1、补过全文的在新周期算已投；
   子线程的压缩不补投也不动父会话周期；非 compact 来源零输出；Codex 清单形态（`--output hook-json`）投 JSON。
5. **开场**（`session-start-prep.py`）：shared 全文＋条目目录，dormant（archive）态照样有；Codex 清单形态投 JSON。
6. 映射表与派生：键都是存在的 `core:<skill>`、一个 skill 只归一个角色；Codex 派生清单含补投（compact 组、带
   `--output hook-json`）、SessionStart 不再挂载自动种信任 hook、工具事件带正整数上限。
7. **SubagentStart 对 fork 跳过**（`subagent-memory-inject.py` 与 `inject-context.py --scope sub`）：CC 的 `agent_type=fork`
   与 Codex 的 rollout 首行 `forked_from_id` 两种认法；fork 载荷零 stdout、sub 片在 inject-log 记一行 `kind=skipped_fork`；
   普通子 agent 照投（对照组，证明探针走得到投递路径）；rollout 读不到时按普通子 agent 处理。

> **测的是脚本 stdin → stdout 与事件流，不是 harness 是否把 additionalContext 送进模型**——那要真实会话看
> transcript 的 hook 附件 / Codex rollout 才验得了。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "core"
SCRIPTS = PLUGIN / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _codex_hooks as ch  # noqa: E402
import _role_memory as rm  # noqa: E402

FAILURES = []
SENTINEL_QA = "哨兵-QA-测试方法论"
SENTINEL_DEV = "哨兵-DEV-环境坑"
SENTINEL_PM = "哨兵-PM-需求口径"
SENTINEL_SHARED = "哨兵-SHARED-跨角色纪律"


def check(label, cond, detail=""):
    if cond:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}{': ' + detail if detail else ''}")
        FAILURES.append(label)


def make_project():
    proj = Path(tempfile.mkdtemp(prefix="role-memory-test-"))
    (proj / ".workframe-config.json").write_text('{"project_name":"rm-test"}', encoding="utf-8", newline="")
    sub = proj / "projects" / "modules" / "engine" / "core"
    sub.mkdir(parents=True)
    (sub / "submodule.yaml").write_text("parent_module: engine\nname: core\ncode_paths:\n  - src/**\n",
                                        encoding="utf-8", newline="")
    (proj / "projects" / "board.yaml").write_text("summary:\n  total: 0\ntasks: []\n", encoding="utf-8", newline="")
    mem = proj / ".workframe" / "agent-memory"
    for role, body in (("qa", f"- 2026-09-01：{SENTINEL_QA} 第一条\n- [纠正] 2026-09-02：**纠正条目**" + "长" * 40 + "尾部正文哨兵\n"),
                       ("dev", f"- 2026-09-03：[亲测 X] {SENTINEL_DEV}\n"),
                       ("pm", f"- 2026-09-04：{SENTINEL_PM}\n"),
                       ("shared", f"- 2026-09-05：{SENTINEL_SHARED}\n")):
        (mem / role).mkdir(parents=True)
        (mem / role / "MEMORY.md").write_text(f"# {role} MEMORY\n\n## 区\n\n{body}", encoding="utf-8", newline="")
    (proj / ".workframe" / "state").mkdir(parents=True)
    (proj / "src").mkdir()
    (proj / "src" / "a.py").write_text("x = 1\n", encoding="utf-8", newline="")
    return proj


def run(proj, script, payload, args=("--harness", "cc")):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(proj), CLAUDE_PLUGIN_ROOT=str(PLUGIN))
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    r = subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                       input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       capture_output=True, timeout=120, cwd=str(proj), env=env)
    return r.returncode, r.stdout.decode("utf-8", errors="replace"), r.stderr.decode("utf-8", errors="replace")


def events(proj, sid=None):
    p = proj / ".workframe" / "state" / "events.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == rm.EVENT_TYPE and (sid is None or e.get("session_id") == sid):
            out.append(e)
    return out


def skill_payload(sid, skill, proj, **extra):
    p = {"session_id": sid, "cwd": str(proj), "hook_event_name": "PostToolUse", "tool_name": "Skill",
         "tool_input": {"skill": skill}, "tool_response": {}}
    p.update(extra)
    return p


def safe_ctx(stdout):
    """同 `ctx_of`，但 stdout 不是那串 JSON 时返回空值——对照组断言要具名失败，不能让整个套件崩掉、后面的断言不跑。"""
    try:
        return ctx_of(stdout)
    except (ValueError, KeyError, TypeError):
        return "", ""


def ctx_of(stdout):
    obj = json.loads(stdout)
    hso = obj["hookSpecificOutput"]
    return hso["hookEventName"], hso["additionalContext"]


def test_skill_entry(proj):
    print("[skill 入口]")
    rc, out, err = run(proj, "log-skill-invoked.py", skill_payload("s1", "core:test-case-design", proj))
    check("Skill 调用 exit 0", rc == 0, err[:200])
    check("stdout 是纯 ASCII", out.isascii(), out[:80])
    try:
        ev, ctx = ctx_of(out)
    except Exception as e:
        check("stdout 是唯一一串 hookSpecificOutput JSON", False, f"{type(e).__name__}: {out[:120]!r}")
        return
    check("hookEventName=PostToolUse", ev == "PostToolUse", ev)
    check("投的是 qa 记忆全文", SENTINEL_QA in ctx and "qa 域" in ctx, ctx[:120])
    check("附契约路径指针", "agents/qa.md" in ctx, ctx[:200])
    e = events(proj, "s1")
    check("事件 injected / skill_tool / 全名", [(x["outcome"], x["trigger"], x.get("skill")) for x in e]
          == [("injected", "skill_tool", "core:test-case-design")], str(e))

    rc, out, _ = run(proj, "log-skill-invoked.py", skill_payload("s1", "core:code-review", proj))
    check("同周期同域再调零 stdout", rc == 0 and out == "", out[:80])
    check("事件 skipped_dedup", events(proj, "s1")[-1]["outcome"] == "skipped_dedup")

    # 投递判定在分钟级去重之前：同分钟已有同 skill 的 skill_invoked，新会话照样投
    import datetime
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with (proj / ".workframe" / "state" / "events.jsonl").open("a", encoding="utf-8", newline="") as f:
        # 本分钟与下一分钟各写一条：只写本分钟的话，测试恰好跨过分钟边界时错误实现也会被放过
        nxt = (datetime.datetime.fromisoformat(ts) + datetime.timedelta(minutes=1)).isoformat(timespec="seconds")
        for t in (ts, nxt):
            f.write(json.dumps({"ts": t, "type": "skill_invoked", "skill": "code-review", "session_id": "s2"}) + "\n")
    rc, out, _ = run(proj, "log-skill-invoked.py", skill_payload("s2", "core:code-review", proj))
    check("分钟级去重早退挡不住投递", SENTINEL_QA in (out and ctx_of(out)[1] or ""), out[:80])

    rc, out, _ = run(proj, "log-skill-invoked.py",
                     {"session_id": "s1", "cwd": str(proj), "hook_event_name": "UserPromptExpansion",
                      "command_name": "core:prd-writer", "command_source": "plugin", "prompt": "/core:prd-writer"})
    ev, ctx = ctx_of(out) if out else ("", "")
    check("用户直敲 /skill 投、hookEventName=UserPromptExpansion",
          ev == "UserPromptExpansion" and SENTINEL_PM in ctx, f"{ev} {ctx[:80]}")
    check("事件 trigger=command_expansion", events(proj, "s1")[-1]["trigger"] == "command_expansion")

    rc, out, _ = run(proj, "log-skill-invoked.py", skill_payload("s1", "core:document-norms", proj))
    check("未映射零 stdout、事件 unmapped", out == "" and events(proj, "s1")[-1]["outcome"] == "unmapped"
          and events(proj, "s1")[-1]["role"] is None)

    rc, out, _ = run(proj, "log-skill-invoked.py",
                     skill_payload("s3", "core:technical-design", proj, agent_id="a0", agent_type="core:qa"))
    last = events(proj, "s3")[-1]
    check("普通子 agent 零 stdout、事件 skipped_subthread 带 agent_type",
          out == "" and last["outcome"] == "skipped_subthread" and last.get("agent_type") == "core:qa", str(last))
    rc, out, _ = run(proj, "log-skill-invoked.py",
                     skill_payload("s3", "core:technical-design", proj, agent_id="a1", agent_type="fork"))
    last = events(proj, "s3")[-1]
    ctx = ctx_of(out)[1] if out else ""
    check("fork：不投正文、给 dev 域一行指针（含路径）、事件 skipped_subthread 带 agent_type",
          "fork 线程不投正文" in ctx and "dev/MEMORY.md" in ctx and SENTINEL_DEV not in ctx
          and last["outcome"] == "skipped_subthread" and last.get("agent_type") == "fork", f"{ctx[:160]} {last}")
    rc, out, _ = run(proj, "log-skill-invoked.py",
                     skill_payload("s3", "core:technical-design", proj, agent_id="a1", agent_type="fork"))
    check("同一 fork 同域第二次零 stdout", out == "", out[:80])
    rc, out, _ = run(proj, "log-skill-invoked.py",
                     skill_payload("s3", "core:technical-design", proj, agent_id="a2", agent_type="fork"))
    check("另一个 fork 同域照样给指针", "fork 线程不投正文" in (ctx_of(out)[1] if out else ""), out[:80])
    rc, out, _ = run(proj, "log-skill-invoked.py", skill_payload("s3", "core:technical-design", proj))
    check("fork 的指针不记进父会话：父会话首次调该域照投全文", SENTINEL_DEV in (ctx_of(out)[1] if out else ""), out[:80])

    rc, out, _ = run(proj, "log-skill-invoked.py", skill_payload("s4", "core:prompt-design", proj))
    ev, ctx = ctx_of(out) if out else ("", "")
    check("记忆缺失：可见「未加载」、事件 read_failed", "未加载" in ctx and events(proj, "s4")[-1]["outcome"] == "read_failed",
          ctx[:120])
    rc, out, _ = run(proj, "log-skill-invoked.py", skill_payload("s4", "core:prompt-evaluation", proj))
    check("读失败不记已投：下次照样再报", events(proj, "s4")[-1]["outcome"] == "read_failed")

    rc, out, _ = run(proj, "log-skill-invoked.py", skill_payload("s5", "core:test-case-design", proj),
                     args=("--harness", "codex"))
    check("--harness codex 下同一载荷同样投（不读门）", rc == 0 and SENTINEL_QA in safe_ctx(out)[1], f"rc={rc} {out[:80]}")


def bash_payload(sid, command, proj, **extra):
    p = {"session_id": sid, "cwd": str(proj), "hook_event_name": "PostToolUse", "tool_name": "Bash",
         "tool_input": {"command": command}, "tool_response": {}}
    p.update(extra)
    return p


CODEX = ("--harness", "codex")


def test_codex_skill_entries(proj):
    print("[Codex 的 skill 入口：shell 读 SKILL.md、提示里的 $skill]")
    md = lambda name: (PLUGIN / "skills" / name / "SKILL.md").as_posix()
    rc, out, err = run(proj, "signoff-guard.py", bash_payload("k1", f"Get-Content -LiteralPath '{md('test-case-design')}'", proj),
                       args=CODEX)
    ev, ctx = safe_ctx(out)
    check("shell 读 SKILL.md：exit 0、PostToolUse JSON、投 qa 全文", rc == 0 and ev == "PostToolUse" and SENTINEL_QA in ctx,
          f"rc={rc} {(out or err)[:160]}")
    check("stdout 纯 ASCII", out.isascii())
    check("事件 injected / skill_read / 全名",
          [(x["outcome"], x["trigger"], x.get("skill")) for x in events(proj, "k1")]
          == [("injected", "skill_read", "core:test-case-design")], str(events(proj, "k1")))
    rc, out, _ = run(proj, "signoff-guard.py", bash_payload("k1", f"cat '{md('code-review')}'", proj), args=CODEX)
    check("同周期同域再读零 stdout、skipped_dedup", out == "" and events(proj, "k1")[-1]["outcome"] == "skipped_dedup")
    rc, out, _ = run(proj, "signoff-guard.py", bash_payload("k1", "git status --short", proj), args=CODEX)
    check("没有 SKILL.md 的命令零 stdout、零事件", rc == 0 and out == "" and len(events(proj, "k1")) == 2)
    rc, out, _ = run(proj, "signoff-guard.py",
                     bash_payload("k1", "cat skills/not-a-skill/SKILL.md myskills/code-review/SKILL.md", proj), args=CODEX)
    check("磁盘上没有的 skill、同形字面串不认", out == "" and len(events(proj, "k1")) == 2, str(events(proj, "k1")[-1:]))
    rc, out, _ = run(proj, "signoff-guard.py",
                     bash_payload("k2", f"cat '{md('document-norms')}' '{md('prd-writer')}' '{md('technical-design')}'", proj),
                     args=CODEX)
    e = events(proj, "k2")
    ctx = safe_ctx(out)[1]
    check("一条命令多个 skill：未映射的记 unmapped，投第一个可投的（pm）全文，后面别的域只给一行指针、不判定不记事件",
          SENTINEL_PM in ctx and SENTINEL_DEV not in ctx and "同一次还进入了 dev 域" in ctx and "dev/MEMORY.md" in ctx
          and [(x["outcome"], x.get("skill")) for x in e] == [("unmapped", "core:document-norms"), ("injected", "core:prd-writer")],
          f"{ctx[-200:]} {e}")
    other = Path(tempfile.mkdtemp(prefix="other-plugin-")) / "skills" / "code-review"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("x\n", encoding="utf-8", newline="")
    bak = PLUGIN / "skills" / "code-review" / "SKILL.md.bak"
    rc, out, _ = run(proj, "signoff-guard.py",
                     bash_payload("k6", f"cat '{(other / 'SKILL.md').as_posix()}' '{bak.as_posix()}'", proj), args=CODEX)
    check("别的插件里同名 skill、SKILL.md.bak 不认", out == "" and events(proj, "k6") == [], str(events(proj, "k6")))
    shutil.rmtree(other.parent.parent, ignore_errors=True)
    rc, out, _ = run(proj, "signoff-guard.py",
                     bash_payload("k9", f"cat '{PLUGIN.parent.as_posix()}'/core/skills/code-review/SKILL.md", proj), args=CODEX)
    check("引号段与裸段相邻的路径按一个词认（shell 拼接写法）", [x.get("skill") for x in events(proj, "k9")] == ["core:code-review"],
          str(events(proj, "k9")))
    if os.name == "nt":
        rc, out, _ = run(proj, "signoff-guard.py",
                         bash_payload("k10", f"Get-Content -LiteralPath '{PLUGIN.as_posix()}/SKILLS/code-review/skill.md'", proj),
                         args=CODEX)
        check("Windows：大小写不同的同一路径照认", [x.get("skill") for x in events(proj, "k10")] == ["core:code-review"],
              str(events(proj, "k10")))
    sk = proj / ".agents" / "skills" / "local-skill"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: local-skill\n---\n", encoding="utf-8", newline="")
    # 反斜杠只在 Windows 是路径分隔符：POSIX 上它是文件名里的普通字符，那串路径不指向夹具里的文件，不认才对。
    # 两个平台各测本平台的写法，「项目 skill 按裸名认、未映射记 unmapped」在哪儿跑都有覆盖。
    k3_cmd, k3_form = (("type .agents\\skills\\local-skill\\SKILL.md", "反斜杠路径") if os.name == "nt"
                       else ("cat .agents/skills/local-skill/SKILL.md", "正斜杠路径"))
    rc, out, _ = run(proj, "signoff-guard.py", bash_payload("k3", k3_cmd, proj), args=CODEX)
    check(f"项目 skill（{k3_form}）按裸名认、未映射记 unmapped",
          out == "" and [(x["outcome"], x.get("skill")) for x in events(proj, "k3")] == [("unmapped", "local-skill")],
          str(events(proj, "k3")))
    rc, out, _ = run(proj, "signoff-guard.py",
                     dict(bash_payload("k7", "Get-Content -LiteralPath skills/local-skill/SKILL.md", proj), cwd=str(proj / ".agents")),
                     args=CODEX)
    check("相对路径按载荷 cwd 解析（cwd 在 .agents 下）", [x.get("skill") for x in events(proj, "k7")] == ["local-skill"],
          str(events(proj, "k7")))
    rc, out, _ = run(proj, "signoff-guard.py",
                     bash_payload("k4", f"cat \"{md('technical-design')}\"", proj, agent_id="a5", agent_type="default"), args=CODEX)
    check("子线程里读不投、skipped_subthread", out == "" and events(proj, "k4")[-1]["outcome"] == "skipped_subthread")

    act = proj / ".workframe" / "state" / "activity-state.json"
    saved = act.read_text(encoding="utf-8") if act.exists() else None
    data = json.loads(saved) if saved else {}
    data.update({"session_counter": 7, "pending_maintenance": [
        {"id": "PM-1", "kind": "k", "severity": "warn", "status": "open", "details": "维护提醒哨兵"}]})
    data.pop("last_prompt_injected_session_id", None)
    data.pop("dormant", None)
    act.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="")
    try:
        ups = lambda sid, prompt: {"session_id": sid, "cwd": str(proj), "hook_event_name": "UserPromptSubmit", "prompt": prompt}
        rc, out, err = run(proj, "user-prompt-inject.py", ups("k5", "$core:prd-writer 写一份需求"), args=CODEX)
        ev, ctx = safe_ctx(out)
        check("$插件:skill：UserPromptSubmit JSON，维护提醒与 pm 记忆合并成一次输出",
              rc == 0 and ev == "UserPromptSubmit" and "维护提醒哨兵" in ctx and SENTINEL_PM in ctx, f"rc={rc} {(out or err)[:160]}")
        check("事件 skill_mention", events(proj, "k5")[-1]["trigger"] == "skill_mention")
        rc, out, _ = run(proj, "user-prompt-inject.py", ups("k5", "用 $technical-design 做方案"), args=CODEX)
        ev, ctx = safe_ctx(out)
        check("裸名 $skill 认本插件的；本会话已提醒过也照投", SENTINEL_DEV in ctx and "维护提醒哨兵" not in ctx, ctx[:120])
        rc, out, _ = run(proj, "user-prompt-inject.py", ups("k5", "echo $PATH and $HOME, cost $5, a$code-review"), args=CODEX)
        check("$PATH 之类、词中间的 $ 不认：零 stdout、零新事件", out == "" and len(events(proj, "k5")) == 2,
              str(events(proj, "k5")[-1:]))

        pm = proj / ".workframe" / "agent-memory" / "pm" / "MEMORY.md"
        pm_saved = pm.read_text(encoding="utf-8")
        data.update({"session_counter": 8, "pending_maintenance": [
            {"id": "PM-2", "kind": "k", "severity": "warn", "status": "open", "details": "维护提醒哨兵" + "长" * 300}]})
        act.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="")
        try:
            pm.write_text("# pm\n\n" + "".join(f"- 2026-09-{i % 28 + 1:02d}：pm 第{i}条" + "长" * 80 + "\n" for i in range(100))[:9050]
                          + "\n", encoding="utf-8", newline="")
            alone = rm.deliver(proj, {"session_id": "k8-probe"}, "pm", rm.TRIGGER_SKILL_MENTION, skill="core:prd-writer")
            check("前提：这份 pm 记忆单独投放得下全文", "未完整加载" not in (alone or "") and SENTINEL_PM not in (alone or "")
                  and 0 < rm.u16len(alone or "") <= rm.DELIVER_BUDGET, str(rm.u16len(alone or "")))
            rc, out, _ = run(proj, "user-prompt-inject.py", ups("k8", "$core:prd-writer"), args=CODEX)
            ctx = safe_ctx(out)[1]
            check(f"与维护提醒合并放不下全文：记忆降为条目目录，整条 ≤ {rm.DELIVER_BUDGET}",
                  "维护提醒哨兵" in ctx and "未完整加载" in ctx and rm.u16len(ctx) <= rm.DELIVER_BUDGET,
                  f"u16={rm.u16len(ctx)}")
            # 第一个域贴近满预算时，其余域的指针仍在（指针空间先预留）
            pm.write_text("# pm\n\n- x\n", encoding="utf-8", newline="")
            tiny = rm.deliver(proj, {"session_id": "k9-probe"}, "pm", rm.TRIGGER_SKILL_MENTION, skill="core:prd-writer")
            head = rm.u16len(tiny) - rm.u16len("# pm\n\n- x")
            pm.write_text("# pm\n\n" + ("- 2026-09-01：" + "长" * 60 + "\n") * 200, encoding="utf-8", newline="")
            body = pm.read_text(encoding="utf-8")[: rm.DELIVER_BUDGET - head - 10]
            pm.write_text(body, encoding="utf-8", newline="")
            alone = rm.deliver(proj, {"session_id": "k9-probe2"}, "pm", rm.TRIGGER_SKILL_MENTION, skill="core:prd-writer")
            check("前提：这份 pm 记忆单独投恰好放得下全文", "未完整加载" not in alone and rm.u16len(alone) > rm.DELIVER_BUDGET - 100,
                  str(rm.u16len(alone)))
            rc, out, _ = run(proj, "user-prompt-inject.py", ups("k11", "$core:prd-writer $core:technical-design"), args=CODEX)
            ctx = safe_ctx(out)[1]
            check(f"第一个域贴满预算：指针仍在、第一个域让出空间、整条 ≤ {rm.DELIVER_BUDGET}",
                  "同一次还进入了 dev 域" in ctx and "未完整加载" in ctx and rm.u16len(ctx) <= rm.DELIVER_BUDGET,
                  f"u16={rm.u16len(ctx)} {ctx[-120:]}")
            data.update({"session_counter": 9, "pending_maintenance": [
                {"id": f"PM-{i}", "kind": "k", "severity": "warn", "status": "open", "details": "长" * 900} for i in range(5)]})
            act.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="")
            rc, out, _ = run(proj, "user-prompt-inject.py", ups("k12", "$core:code-review"), args=CODEX)
            ctx = safe_ctx(out)[1]
            check(f"维护提醒超长时先截断，记忆仍投得进去、整条 ≤ {rm.DELIVER_BUDGET}",
                  "已截断" in ctx and SENTINEL_QA in ctx and rm.u16len(ctx) <= rm.DELIVER_BUDGET, f"u16={rm.u16len(ctx)}")
        finally:
            pm.write_text(pm_saved, encoding="utf-8", newline="")
    finally:
        if saved is None:
            act.unlink(missing_ok=True)
        else:
            act.write_text(saved, encoding="utf-8", newline="")


def test_over_budget(proj):
    print("[超预算]")
    big = proj / ".workframe" / "agent-memory" / "pm" / "MEMORY.md"
    original = big.read_text(encoding="utf-8")
    big.write_text(original + "".join(f"- 2026-09-{i % 28 + 1:02d}：第{i}条" + "长" * 200 + "\n" for i in range(60)),
                   encoding="utf-8", newline="")
    try:
        rc, out, _ = run(proj, "log-skill-invoked.py", skill_payload("s6", "core:prd-writer", proj))
        ev, ctx = ctx_of(out) if out else ("", "")
        check("超预算可见「未完整加载」", "未完整加载" in ctx, ctx[:120])
        check(f"超预算投递长度 ≤ {rm.DELIVER_BUDGET}", 0 < len(ctx) <= rm.DELIVER_BUDGET, str(len(ctx)))
        check("超预算给的是条目目录（含第 59 条标题）", "第59条" in ctx, ctx[-200:])
        check("事件 over_budget", events(proj, "s6")[-1]["outcome"] == "over_budget")
    finally:
        big.write_text(original, encoding="utf-8", newline="")


def test_code_path_entry(proj):
    print("[路径入口]")
    edit = lambda sid, f, **kw: dict({"session_id": sid, "cwd": str(proj), "hook_event_name": "PostToolUse",
                                      "tool_name": "Edit", "tool_input": {"file_path": str(f)},
                                      "tool_response": {}}, **kw)
    rc, out, err = run(proj, "check-stale-modules.py", edit("p1", proj / "src" / "a.py"))
    ev, ctx = ctx_of(out) if out else ("", "")
    check("改 code_paths 内文件投 dev", rc == 0 and SENTINEL_DEV in ctx and "src/a.py" in ctx, (out or err)[:160])
    check("事件 trigger=code_path、file 为相对路径",
          [(e["trigger"], e.get("file")) for e in events(proj, "p1")] == [("code_path", "src/a.py")])
    rc, out, _ = run(proj, "check-stale-modules.py", edit("p1", proj / "src" / "a.py"))
    check("同周期再改零 stdout", out == "", out[:80])
    rc, out, _ = run(proj, "check-stale-modules.py", edit("p2", proj / "README.md"))
    check("未命中 code_paths 零 stdout、零事件", out == "" and events(proj, "p2") == [])
    rc, out, _ = run(proj, "check-stale-modules.py", edit("p4", proj / "src" / "a.py", agent_id="a9", agent_type="core:dev"))
    check("子 agent 里改不投", out == "" and events(proj, "p4")[-1]["outcome"] == "skipped_subthread")

    patch = lambda sid, body, **kw: dict({"session_id": sid, "cwd": str(proj), "hook_event_name": "PostToolUse",
                                          "tool_name": "apply_patch", "tool_input": {"command": body},
                                          "tool_response": {}}, **kw)
    stale = proj / ".workframe" / "state" / "stale-modules.yaml"

    def code_files(sid):
        return [e.get("file") for e in events(proj, sid) if e["trigger"] == "code_path"]
    rc, out, err = run(proj, "check-stale-modules.py",
                       patch("p3", "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x = 1\n+x = 2\n*** End Patch"), args=CODEX)
    check("apply_patch（相对路径 Update File）投 dev、exit 0", rc == 0 and SENTINEL_DEV in safe_ctx(out)[1] and code_files("p3") == ["src/a.py"],
          f"rc={rc} {(out or err)[:160]}")
    stale.unlink(missing_ok=True)
    rc, out, _ = run(proj, "check-stale-modules.py",
                     patch("p5", "*** Begin Patch\n*** Update File: README.md\n*** Move to: src/moved.py\n@@\n*** End Patch"), args=CODEX)
    check("移动：新路径命中即投（文件记新路径）、标 stale", code_files("p5") == ["src/moved.py"] and stale.is_file()
          and "src/moved.py" in stale.read_text(encoding="utf-8"), str(code_files("p5")))
    stale.unlink(missing_ok=True)
    rc, out, _ = run(proj, "check-stale-modules.py",
                     patch("p6", "*** Begin Patch\r\n*** Update File: src/a.py\r\n*** Move to: docs/a.md\r\n*** End Patch\r\n"), args=CODEX)
    check("移动：旧路径命中同样算（CRLF 补丁）", code_files("p6") == ["src/a.py"] and "src/a.py" in stale.read_text(encoding="utf-8"),
          str(code_files("p6")))
    outside = Path(tempfile.gettempdir()) / "rm-outside.py"
    rc, out, _ = run(proj, "check-stale-modules.py",
                     patch("p7", f"*** Begin Patch\n*** Add File: {outside.as_posix()}\n+x\n"
                                 f"*** Delete File: {(proj / 'src' / '中文 目录' / 'b.py').as_posix()}\n*** End Patch"), args=CODEX)
    check("多文件：项目外路径跳过、绝对路径（中文空格）的 Delete File 命中", code_files("p7") == ["src/中文 目录/b.py"],
          str(code_files("p7")))
    rc, out, _ = run(proj, "check-stale-modules.py", patch("p8", "*** Begin Patch\n*** Add File: notes.txt\n+x\n*** End Patch"),
                     args=CODEX)
    check("apply_patch 未命中 code_paths 零 stdout、零事件", out == "" and events(proj, "p8") == [])
    rc, out, _ = run(proj, "check-stale-modules.py", patch("p9", "*** Begin Patch\n*** Add File: src/c.py\n+x\n*** End Patch",
                                                           agent_id="a8", agent_type="default"), args=CODEX)
    check("Codex 子线程里 apply_patch 不投", out == "" and events(proj, "p9")[-1]["outcome"] == "skipped_subthread")
    rc, out, _ = run(proj, "check-stale-modules.py", dict(patch("p10", ""), tool_name="Read", tool_input={"file_path": "src/a.py"}),
                     args=CODEX)
    check("其他工具名零 stdout、零事件", rc == 0 and out == "" and events(proj, "p10") == [])


def test_restore(proj):
    print("[压缩补投]")
    sid = "c1"
    run(proj, "log-skill-invoked.py", skill_payload(sid, "core:code-review", proj))
    import time
    time.sleep(1.1)  # 投递时间戳是秒级：拉开先后，才验得了「最近的在前」
    run(proj, "log-skill-invoked.py", skill_payload(sid, "core:prd-writer", proj))
    compact = {"session_id": sid, "cwd": str(proj), "hook_event_name": "SessionStart", "source": "compact"}
    rc, out, err = run(proj, "role-memory-restore.py", compact)
    check("补投两域、纯文本", rc == 0 and SENTINEL_QA in out and SENTINEL_PM in out and not out.lstrip().startswith("{"),
          (out or err)[:160])
    check("最近使用的在前（此处即后投的 pm）", 0 <= out.find(SENTINEL_PM) < out.find(SENTINEL_QA), out[:80])
    st = json.loads((proj / ".workframe" / "state" / rm.STATE_FILE_NAME).read_text(encoding="utf-8"))["sessions"][sid]
    check("周期号 +1、补过全文的在新周期算已投", st["cycle"] == 1 and set(st["delivered"]) == {"qa", "pm"}, str(st))
    rc, out, _ = run(proj, "log-skill-invoked.py", skill_payload(sid, "core:test-case-design", proj))
    check("补投后同域再调不重复", out == "" and events(proj, sid)[-1]["outcome"] == "skipped_dedup")
    rc, out, _ = run(proj, "role-memory-restore.py", dict(compact, agent_id="a2", agent_type="core:qa"))
    st2 = json.loads((proj / ".workframe" / "state" / rm.STATE_FILE_NAME).read_text(encoding="utf-8"))["sessions"][sid]
    check("子线程压缩：零 stdout、不动父会话周期", out == "" and st2["cycle"] == 1, str(st2))
    rc, out, _ = run(proj, "role-memory-restore.py", dict(compact, source="startup"))
    check("非 compact 来源零输出、exit 0", rc == 0 and out == "", f"rc={rc}")
    rc, out, _ = run(proj, "role-memory-restore.py", compact, args=("--harness", "codex", "--output", "hook-json"))
    ev, ctx = safe_ctx(out)
    check("Codex 清单形态：SessionStart JSON（纯 ASCII）、补投正文在内", rc == 0 and out.isascii() and ev == "SessionStart"
          and SENTINEL_QA in ctx and SENTINEL_PM in ctx, f"rc={rc} {out[:120]}")


def test_restore_order_and_budget(proj):
    print("[补投：最近使用顺序与总预算]")
    import time
    sid = "c2"
    run(proj, "log-skill-invoked.py", skill_payload(sid, "core:code-review", proj))       # qa
    time.sleep(0.05)
    run(proj, "log-skill-invoked.py", skill_payload(sid, "core:prd-writer", proj))        # pm
    time.sleep(0.05)
    run(proj, "log-skill-invoked.py", skill_payload(sid, "core:test-case-design", proj))  # qa 去重命中
    compact = {"session_id": sid, "cwd": str(proj), "hook_event_name": "SessionStart", "source": "compact"}
    rc, out, _ = run(proj, "role-memory-restore.py", compact)
    check("去重命中也算使用：最近用过的 qa 排在 pm 前", 0 <= out.find(SENTINEL_QA) < out.find(SENTINEL_PM), out[:120])

    mem = proj / ".workframe" / "agent-memory"
    saved = {r: (mem / r / "MEMORY.md").read_text(encoding="utf-8") for r in ("dev", "qa", "pm")}
    (mem / "prompt-eng").mkdir(exist_ok=True)
    try:
        for r in ("dev", "qa", "pm", "prompt-eng"):
            (mem / r / "MEMORY.md").write_text(
                "# m\n\n" + "".join(f"- 2026-09-{i % 28 + 1:02d}：{r} 第{i}条" + "长" * 70 + "\n" for i in range(100)),
                encoding="utf-8", newline="")
        sid = "c3"
        for sk in ("core:technical-design", "core:code-review", "core:prd-writer", "core:prompt-design"):
            run(proj, "log-skill-invoked.py", skill_payload(sid, sk, proj))
        rc, out, _ = run(proj, "role-memory-restore.py", dict(compact, session_id=sid))
        u16 = rm.u16len(out.rstrip("\n"))
        check(f"四域都超大时补投总量 ≤ {rm.DELIVER_BUDGET}（UTF-16）", 0 < u16 <= rm.DELIVER_BUDGET, str(u16))
        e = [x for x in events(proj, sid) if x["trigger"] == "compact"]
        check("四域各有一条补投事件", sorted(x["role"] for x in e) == ["dev", "pm", "prompt-eng", "qa"], str(e))
        st = json.loads((proj / ".workframe" / "state" / rm.STATE_FILE_NAME).read_text(encoding="utf-8"))["sessions"][sid]
        full = {x["role"] for x in e if x["outcome"] == "injected"}
        check("新周期只记补投时给了全文的域（超预算的不记）",
              set(st["delivered"]) == full and any(x["outcome"] == "over_budget" for x in e), f"{st['delivered']} {full}")
    finally:
        for r, t in saved.items():
            (mem / r / "MEMORY.md").write_text(t, encoding="utf-8", newline="")
        shutil.rmtree(mem / "prompt-eng", ignore_errors=True)


def test_utf16_budget(proj):
    print("[UTF-16 预算]")
    p = proj / ".workframe" / "agent-memory" / "dev" / "MEMORY.md"
    original = p.read_text(encoding="utf-8")
    p.write_text("# dev\n\n- 2026-09-01：" + "😀" * 6000 + "\n", encoding="utf-8", newline="")
    try:
        rc, out, _ = run(proj, "log-skill-invoked.py", skill_payload("u1", "core:technical-design", proj))
        ev, ctx = ctx_of(out) if out else ("", "")
        check("Python 字符数在预算内、UTF-16 超限 → 按超预算处理",
              "未完整加载" in ctx and rm.u16len(ctx) <= rm.DELIVER_BUDGET and events(proj, "u1")[-1]["outcome"] == "over_budget",
              f"u16={rm.u16len(ctx)} {events(proj, 'u1')[-1:]}")
    finally:
        p.write_text(original, encoding="utf-8", newline="")


def test_parallel(proj):
    print("[并行投递不同角色]")
    import threading
    lost = []
    for i in range(6):
        sid = f"par{i}"
        ts = [threading.Thread(target=run, args=(proj, "log-skill-invoked.py", skill_payload(sid, sk, proj)))
              for sk in ("core:code-review", "core:prd-writer", "core:technical-design")]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        st = json.loads((proj / ".workframe" / "state" / rm.STATE_FILE_NAME).read_text(encoding="utf-8"))
        got = set(st["sessions"].get(sid, {}).get("delivered", {}))
        if got != {"qa", "pm", "dev"}:
            lost.append((sid, sorted(got)))
    check("6 轮三路并行，每轮三个角色都记成已投（不被后写的抹掉）", not lost, str(lost))


def test_opening(proj):
    print("[开场]")
    start = {"session_id": "o1", "cwd": str(proj), "hook_event_name": "SessionStart", "source": "startup"}
    rc, out, err = run(proj, "session-start-prep.py", start)
    check("开场有 shared 全文", rc == 0 and SENTINEL_SHARED in out, (out or err)[-200:])
    check("开场有条目目录（dev / qa 标题，[纠正] 保留）",
          "角色记忆条目目录" in out and SENTINEL_DEV in out and "[纠正] 09-02 纠正条目" in out, out[-400:])
    check("开场不带角色正文", "第一条" in out and "尾部正文哨兵" not in out)
    act = proj / ".workframe" / "state" / "activity-state.json"
    data = json.loads(act.read_text(encoding="utf-8"))
    data["dormant_profile"] = "archive"
    act.write_text(json.dumps(data), encoding="utf-8", newline="")
    rc, out, _ = run(proj, "session-start-prep.py", dict(start, session_id="o2"))
    check("archive（dormant）态照样有 shared", "[archive]" in out and SENTINEL_SHARED in out, out[:200])
    data["dormant_profile"] = "normal"
    act.write_text(json.dumps(data), encoding="utf-8", newline="")
    rc, out, _ = run(proj, "session-start-prep.py", dict(start, session_id="o3"),
                     args=("--harness", "codex", "--output", "hook-json"))
    ev, ctx = safe_ctx(out)
    check("Codex 清单形态：SessionStart JSON、shared 全文与条目目录在内",
          rc == 0 and ev == "SessionStart" and SENTINEL_SHARED in ctx and "角色记忆条目目录" in ctx, f"rc={rc} {out[:200]}")
    e = [x for x in events(proj) if x.get("trigger") == "session_start"]
    check("开场事件 role=shared outcome=injected（两门各记）",
          len(e) == 3 and all(x["role"] == "shared" and x["outcome"] == "injected" for x in e)
          and [x.get("harness") for x in e] == ["cc", "cc", "codex"], str(e))


def test_opening_over_budget():
    print("[开场：超预算]")
    proj = make_project()
    try:
        mem = proj / ".workframe" / "agent-memory"
        shared = "# s\n\n" + "".join(f"- 2026-09-{i % 28 + 1:02d}：共享第{i}条" + "长" * 30 + "\n" for i in range(100)) \
            + f"- 2026-09-30：{SENTINEL_SHARED} 末条\n"
        (mem / "shared" / "MEMORY.md").write_text(shared, encoding="utf-8", newline="")   # 约 4200 字，在 doctor 上限附近
        for r in ("dev", "qa", "pm", "ops"):
            (mem / r).mkdir(exist_ok=True)
            (mem / r / "MEMORY.md").write_text(
                "# m\n\n" + "".join(f"- 2026-09-{i % 28 + 1:02d}：{r} 第{i}条" + "长" * 60 + "\n" for i in range(100)),
                encoding="utf-8", newline="")
        t = rm.opening_text(proj, {"session_id": "b1"})
        check(f"目录过长时先截目录、shared 全文保住，总长 ≤ {rm.OPENING_BUDGET}",
              SENTINEL_SHARED in t and "目录过长已截断" in t and rm.u16len(t) <= rm.OPENING_BUDGET
              and events(proj, "b1")[-1]["outcome"] == "injected", f"u16={rm.u16len(t)}")
        (mem / "shared" / "MEMORY.md").write_text(shared * 2, encoding="utf-8", newline="")  # shared 自己就放不下
        t = rm.opening_text(proj, {"session_id": "b2"})
        check("shared 自己放不下才降级：可见「未完整加载」、记 over_budget、总长在预算内",
              "未完整加载" in t and events(proj, "b2")[-1]["outcome"] == "over_budget"
              and rm.u16len(t) <= rm.OPENING_BUDGET, f"u16={rm.u16len(t)}")
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_opening_missing():
    print("[开场：记忆缺失]")
    proj = make_project()
    try:
        mem = proj / ".workframe" / "agent-memory"
        for r in ("shared", "dev", "qa", "pm"):
            shutil.rmtree(mem / r)
        t = rm.opening_text(proj, {"session_id": "m1"})
        check("记忆目录在、一份记忆都没有：可见 shared「未加载」并记 read_failed",
              t is not None and "未加载" in t and events(proj, "m1")[-1]["outcome"] == "read_failed", str(t)[:120])
        shutil.rmtree(mem)
        check("整个记忆目录都不在：零输出", rm.opening_text(proj, {"session_id": "m2"}) is None)
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def test_static():
    print("[映射表与派生]")
    main_protocol = (PLUGIN / "context" / "main" / "10-main-protocol.md").read_text(encoding="utf-8")
    check("Codex 角色派发明确使用 fork_turns=none", 'fork_turns="none"' in main_protocol)
    m = rm.load_map()
    check("映射表非空", len(m) >= 8, str(len(m)))
    missing = [k for k in m if not (k.startswith("core:") and (PLUGIN / "skills" / k.split(":", 1)[1] / "SKILL.md").is_file())]
    check("映射键都是存在的 core:<skill>", not missing, str(missing))
    data = json.loads(rm.MAP_FILE.read_text(encoding="utf-8"))
    flat = [s for v in data["roles"].values() for s in v]
    check("一个 skill 只归一个角色", len(flat) == len(set(flat)))
    titles = rm.entry_titles("- [纠正] 2026-08-20：**一切时间戳**必须取系统实际时间\n- 2026-09-07：[亲测 x] 短\n非条目行\n")
    check("条目标题形态", titles == ["[纠正] 08-20 一切时间戳必须取系统实际时间", "09-07 短"], str(titles))
    out = rm.hook_output("PostToolUse", "中文")
    check("hook_output ASCII 且可还原", out.isascii() and ctx_of(out) == ("PostToolUse", "中文"))
    cc = json.loads((PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    cx = ch.derive(cc)
    ss = cx["hooks"]["SessionStart"]
    check("Codex 派生清单：补投挂在 SessionStart matcher=compact 的组、带 --output hook-json",
          any(g["matcher"] == "compact" and any("role-memory-restore.py" in h["command"] and ch.HOOK_JSON_TOKEN in h["command"]
                                                for h in g["hooks"]) for g in ss), json.dumps(ss, ensure_ascii=False)[:200])
    check("SessionStart 不再挂载自动种信任 hook",
          all("codex-trust-seed.py" not in h["command"] for g in ss for h in g["hooks"]))
    post = [h for g in cx["hooks"]["PostToolUse"] for h in g["hooks"]]
    check("PostToolUse 每条带正整数上限、不低于投递预算的中文最坏情形（× 3 ÷ 4）",
          post and all(type(h.get("additionalContextLimit")) is int and h["additionalContextLimit"] * 4 >= rm.DELIVER_BUDGET * 3
                       for h in post), str([h.get("additionalContextLimit") for h in post]))
    groups = cc["hooks"]["SessionStart"]
    check("CC 清单把补投挂在 SessionStart matcher=compact 的组",
          any(g.get("matcher") == "compact" and any("role-memory-restore.py" in h["command"] for h in g["hooks"])
              for g in groups))


def test_subagent_start_fork(proj):
    print("[SubagentStart：fork 跳过 sub 片与记忆注入]")
    sub = lambda agent_type: {"session_id": "f1", "cwd": str(proj), "hook_event_name": "SubagentStart",
                              "agent_id": f"agent-{agent_type.replace(':', '-')}", "agent_type": agent_type,
                              "transcript_path": ""}
    rc, out, err = run(proj, "subagent-memory-inject.py", sub("core:qa"))
    ctx = safe_ctx(out)[1]
    check("对照：普通子 agent 收到 shared 与本角色记忆、exit 0", rc == 0 and SENTINEL_SHARED in ctx and SENTINEL_QA in ctx,
          f"rc={rc} {(out or err)[:160]}")
    rc, out, err = run(proj, "subagent-memory-inject.py", sub("fork"))
    check("fork 记忆注入零 stdout、exit 0", rc == 0 and out == "", f"rc={rc} {(out or err)[:160]}")

    rc, out, err = run(proj, "inject-context.py", {}, args=("--harness", "cc", "--scope", "sub", "--dry-run"))
    n = json.loads(out)["shard_count"] if rc == 0 else 0
    check("sub 片装箱账可读", n >= 1, (out or err)[:160])
    log = proj / ".workframe" / "state" / "inject-log.jsonl"
    rows = lambda: [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    shard = ("--harness", "cc", "--scope", "sub", "--shard", f"1/{n}")
    before = len(rows())
    rc, out, err = run(proj, "inject-context.py", sub("core:qa"), args=shard)
    new = rows()[before:]
    check("对照：普通子 agent 收到 sub 片、记一行 chars>0",
          rc == 0 and safe_ctx(out)[0] == "SubagentStart" and len(new) == 1 and new[0]["chars"] > 0
          and new[0].get("kind") is None, f"rc={rc} {new} {err[:120]}")
    before = len(rows())
    rc, out, err = run(proj, "inject-context.py", sub("fork"), args=shard)
    new = rows()[before:]
    check("fork sub 片零 stdout、exit 0", rc == 0 and out == "", f"rc={rc} {(out or err)[:160]}")
    check("fork 记一行 kind=skipped_fork、片号与线程号照记、chars=0",
          len(new) == 1 and new[0].get("kind") == "skipped_fork" and new[0]["shard"] == f"1/{n}"
          and new[0]["thread_id"] == "agent-fork" and new[0]["chars"] == 0 and new[0]["sha256"] is None, str(new))
    before = len(rows())
    rc, out, err = run(proj, "inject-context.py", sub("fork"), args=("--harness", "codex", "--scope", "sub", "--shard", "all"))
    new = rows()[before:]
    check("全片形态（Codex 清单的 --shard all）：fork 零 stdout、记一行 shard=all kind=skipped_fork",
          rc == 0 and out == "" and len(new) == 1 and new[0].get("kind") == "skipped_fork" and new[0]["shard"] == "all",
          f"rc={rc} {(out or err)[:120]} {new}")

    # Codex：显式指定角色时 fork 与新子线程都可带角色名，以 rollout 首行区分。
    roll = proj / "rollouts"
    roll.mkdir(exist_ok=True)

    def rollout(name, meta):
        p = roll / f"{name}.jsonl"
        p.write_text(json.dumps({"timestamp": "t", "type": "session_meta", "payload": meta}) + "\n"
                     + json.dumps({"type": "response_item", "payload": {}}) + "\n", encoding="utf-8", newline="")
        return str(p)
    cx_sub = lambda tp, aid: {"session_id": "f2", "cwd": str(proj), "hook_event_name": "SubagentStart",
                              "agent_id": aid, "agent_type": "default", "transcript_path": tp}
    forked = rollout("forked", {"id": "c1", "forked_from_id": "p1", "base_instructions": "长" * 30000})
    fresh = rollout("fresh", {"id": "c2"})
    bad_json = str(roll / "bad.jsonl")
    Path(bad_json).write_text('not json {"forked_from_id": "p1"}\n', encoding="utf-8", newline="")
    full = ("--harness", "codex", "--scope", "sub", "--shard", "all")
    for tp, expected in ((fresh, True), (forked, False)):
        named = {**cx_sub(tp, "cx-role"), "agent_type": "qa"}
        rc, out, err = run(proj, "subagent-memory-inject.py", named, args=CODEX)
        check(f"Codex 指定角色：{'新线程投本角色' if expected else 'fork 跳过记忆'}",
              rc == 0 and ((SENTINEL_QA in safe_ctx(out)[1]) if expected else out == ""), (out or err)[:160])
    for label, tp, want_fork in (("rollout 首行带 forked_from_id（首行超 64KB）", forked, True),
                                 ("对照：首行不带 forked_from_id", fresh, False),
                                 ("rollout 读不到：按普通子 agent", str(roll / "missing.jsonl"), False),
                                 ("rollout 首行不是 JSON：按普通子 agent", bad_json, False),
                                 ("transcript_path 是目录：按普通子 agent", str(roll), False)):
        rc1, out1, _ = run(proj, "subagent-memory-inject.py", cx_sub(tp, "cx-a"), args=CODEX)
        before = len(rows())
        rc2, out2, _ = run(proj, "inject-context.py", cx_sub(tp, "cx-a"), args=full)
        new = rows()[before:]
        skipped = out1 == "" and out2 == "" and len(new) == 1 and new[0].get("kind") == "skipped_fork"
        injected = SENTINEL_SHARED in safe_ctx(out1)[1] and safe_ctx(out2)[0] == "SubagentStart" and new and new[0].get("kind") is None
        check(f"Codex fork 认法 · {label}：{'跳过' if want_fork else '照投'}",
              rc1 == 0 and rc2 == 0 and (skipped if want_fork else injected), f"{out1[:80]!r} {out2[:80]!r} {new}")
    # Codex 的 `--scope prompt`（UserPromptSubmit）：子线程分支对 fork 同样跳过；载荷不带 agent_id 也认（看 rollout）
    ups_sub = lambda tp: {"session_id": "f4", "cwd": str(proj), "hook_event_name": "UserPromptSubmit",
                          "turn_id": f"t-{Path(tp).stem}", "transcript_path": tp, "prompt": "继续"}
    prompt_args = ("--harness", "codex", "--scope", "prompt")
    for label, tp, want_fork in (("fork 子线程", forked, True), ("对照：新派生子线程", fresh, False)):
        before = len(rows())
        rc, out, _ = run(proj, "inject-context.py", ups_sub(tp), args=prompt_args)
        new = rows()[before:]
        ok = (out == "" and len(new) == 1 and new[0].get("kind") == "skipped_fork") if want_fork else \
            (safe_ctx(out)[0] == "UserPromptSubmit" and len(new) == 1 and new[0].get("kind") is None and new[0]["chars"] > 0)
        check(f"--scope prompt · {label}：{'跳过、记 skipped_fork' if want_fork else '补投 sub 片'}", rc == 0 and ok,
              f"{out[:80]!r} {new}")
    # 不带 agent_id、但 transcript 是 fork 的子线程：$skill 与压缩补投都不按主线程处理
    rc, out, _ = run(proj, "user-prompt-inject.py",
                     {"session_id": "f5", "cwd": str(proj), "hook_event_name": "UserPromptSubmit", "transcript_path": forked,
                      "prompt": "$core:technical-design"}, args=CODEX)
    e = events(proj, "f5")
    check("无 agent_id 的 fork 写 $skill：不投正文、记 skipped_subthread",
          SENTINEL_DEV not in safe_ctx(out)[1] and e and e[-1]["outcome"] == "skipped_subthread", f"{out[:80]!r} {e}")
    rc, out, _ = run(proj, "role-memory-restore.py",
                     {"session_id": "f5", "cwd": str(proj), "hook_event_name": "SessionStart", "source": "compact",
                      "transcript_path": forked}, args=CODEX)
    check("无 agent_id 的 fork 压缩：零输出、记 skipped_subthread", out == "" and events(proj, "f5")[-1]["trigger"] == "compact"
          and events(proj, "f5")[-1]["outcome"] == "skipped_subthread", str(events(proj, "f5")[-1:]))
    md = (PLUGIN / "skills" / "technical-design" / "SKILL.md").as_posix()
    rc, out, _ = run(proj, "signoff-guard.py", bash_payload("f3", f"cat '{md}'", proj, agent_id="cx-b", agent_type="default",
                                                            transcript_path=forked), args=CODEX)
    ctx = safe_ctx(out)[1]
    check("Codex fork 线程里读 SKILL.md：不投正文、给一行指针", "fork 线程不投正文" in ctx and SENTINEL_DEV not in ctx, ctx[:120])


def main():
    test_static()
    proj = make_project()
    try:
        test_skill_entry(proj)
        test_codex_skill_entries(proj)
        test_over_budget(proj)
        test_code_path_entry(proj)
        test_restore(proj)
        test_restore_order_and_budget(proj)
        test_utf16_budget(proj)
        test_parallel(proj)
        test_opening(proj)
        test_opening_over_budget()
        test_opening_missing()
        test_subagent_start_fork(proj)
    finally:
        shutil.rmtree(proj, ignore_errors=True)
    print(f"\n{'全部通过' if not FAILURES else f'{len(FAILURES)} 项失败'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
