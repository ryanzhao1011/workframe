#!/usr/bin/env python3
"""`dispatch-name-guard.py`（PreToolUse:Agent）与 Codex 派生排除的常驻单测。

守三件事，每件都配它自己的失败方式：

1. **拦得住**：主会话派发 `core:*` 带非空 `name` → stdout 是 `permissionDecision: "deny"` 的 JSON、exit 0，
   理由里说清改法（去掉 name 重派、续派用 agentId 走 SendMessage）。
2. **不误伤**：不带 name / name 为空白 / 非 `core:*` / 触发在 subagent 内（载荷带 `agent_id`）/
   载荷坏掉——一律零 stdout、exit 0。这道闸挂在每一次 Agent 调用上，误拦的代价是每次派发都要重来。
3. **只挂 CC 门**：派生 Codex 清单时本脚本被去掉，去完为空的组与事件整段不出现，同组别的脚本照常派生；
   独立计数与派生结果条数一致。

> **测的是脚本的 stdin → stdout 通路（子进程实跑）与派生函数，不是 CC 是否按 deny 真的拦下这次调用**——
> 后者要真实会话才验得了。
"""

import json
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "plugins" / "core" / "scripts"
GUARD = SCRIPTS_DIR / "dispatch-name-guard.py"
sys.path.insert(0, str(SCRIPTS_DIR))
import _codex_hooks as ch  # noqa: E402

FAILURES = []


def check(label, got, want):
    if got == want:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


def run(stdin_text, args=("--harness", "cc")):
    r = subprocess.run([sys.executable, str(GUARD), *args], input=stdin_text.encode("utf-8"),
                       capture_output=True, timeout=60)
    return r.returncode, r.stdout.decode("utf-8", errors="replace"), r.stderr.decode("utf-8", errors="replace")


def payload(subagent_type="core:dev", name="dev-demo", **extra):
    ti = {"prompt": "做点事", "description": "派发", "subagent_type": subagent_type}
    if name is not None:
        ti["name"] = name
    p = {"session_id": "s1", "cwd": "C:/Users/<username>/项目", "hook_event_name": "PreToolUse",
         "tool_name": "Agent", "tool_input": ti, "tool_use_id": "toolu_1"}
    p.update(extra)
    return json.dumps(p, ensure_ascii=False)


def test_blocks():
    print("[拦截面]")
    for st in ("core:dev", "core:qa", "core:pm", "core:prompt-eng"):
        rc, out, _ = run(payload(subagent_type=st, name=f"{st[5:]}-x"))
        check(f"{st} 带 name → exit 0", rc, 0)
        try:
            hso = json.loads(out)["hookSpecificOutput"]
        except Exception:
            hso = {}
        check(f"{st} 带 name → permissionDecision=deny", hso.get("permissionDecision"), "deny")
        check(f"{st} 带 name → hookEventName=PreToolUse", hso.get("hookEventName"), "PreToolUse")
    rc, out, _ = run(payload(name="dev-demo-1"))
    reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"] if out.strip() else ""
    for needle in ("core:dev", "dev-demo-1", "去掉 `name`", "agentId", "SendMessage"):
        check(f"理由里点名 {needle!r}", needle in reason, True)
    # 不带 --harness 时行为相同（hook_scripts_tolerate_harness_arg 的同款要求，在这里就地钉）
    check("剥掉 --harness 后 stdout 逐字相同", run(payload(name="dev-demo-1"), args=())[1], out)
    # 非 ASCII 名字经 UTF-8 stdin 原样进理由
    rc, out, _ = run(payload(name="开发-甲"))
    check("中文 name 原样出现在理由里", "开发-甲" in out, True)


def test_passes():
    print("[放行面]")
    cases = [
        ("core:dev 不带 name", payload(name=None)),
        ("name 为空串", payload(name="")),
        ("name 为纯空白", payload(name="   ")),
        ("name 不是字符串", payload(name=123)),
        ("非 core:*（general-purpose）带 name", payload(subagent_type="general-purpose")),
        ("项目级裸名角色带 name（本闸不管）", payload(subagent_type="dev")),
        ("前缀只是相似（score:dev）", payload(subagent_type="score:dev")),
        ("subagent_type 缺失", json.dumps({"tool_input": {"name": "x"}})),
        ("触发在 subagent 内（载荷带 agent_id）", payload(agent_id="a1b2c3")),
        ("tool_input 不是对象", json.dumps({"tool_input": "core:dev"})),
        ("顶层是数组", "[]"),
        ("不是 JSON", "{oops"),
        ("空 stdin", ""),
    ]
    for label, text in cases:
        rc, out, err = run(text)
        check(f"{label} → exit 0", rc, 0)
        check(f"{label} → 零 stdout", out, "")
        check(f"{label} → 零 stderr", err, "")


def _cc_manifest(groups_by_event):
    def cmd(script):
        return f'{ch.LAUNCHER_POSIX} {ch.SCRIPTS_PREFIX}{script}" --harness cc'
    return {"hooks": {ev: [{"matcher": m, "hooks": [{"type": "command", "command": cmd(s)} for s in scripts]}
                           for m, scripts in groups]
                      for ev, groups in groups_by_event.items()}}


def test_codex_derive():
    print("[Codex 派生排除]")
    guard = ch.CC_ONLY_SCRIPTS[0]
    check("CC_ONLY_SCRIPTS 含 dispatch-name-guard.py", guard, "dispatch-name-guard.py")
    base = {
        "SessionStart": [("", ["heartbeat-check.py"])],
        "UserPromptSubmit": [("", ["user-prompt-inject.py"])],
        "Stop": [("", ["check-iteration-trigger.py"])],
    }
    # ① 只挂本脚本的事件 → 整段不出现
    cc = _cc_manifest(dict(base, PreToolUse=[("Agent", [guard])]))
    out = ch.derive(cc)["hooks"]
    check("只挂 CC 专有脚本的事件整段不派生", "PreToolUse" in out, False)
    check("其余事件照常派生", sorted(out), ["SessionStart", "Stop", "UserPromptSubmit"])
    check("独立计数 == 派生条数（事件整段去掉）",
          ch.independent_count(cc, lambda s: 1), ch.count_hooks({"hooks": out}))
    # ② 同组还有别的脚本 → 只去掉本脚本那一条
    cc = _cc_manifest(dict(base, PreToolUse=[("Agent", [guard, "log-skill-invoked.py"])]))
    out = ch.derive(cc)["hooks"]
    pre = out.get("PreToolUse") or []
    cmds = [h["command"] for g in pre for h in g["hooks"]]
    check("同组别的脚本照常派生（组保留）", len(pre), 1)
    check("组内只剩那一条", len(cmds), 1)
    check("去掉的是 CC 专有脚本", any(guard in c for c in cmds), False)
    check("独立计数 == 派生条数（组内去一条）",
          ch.independent_count(cc, lambda s: 1), ch.count_hooks({"hooks": out}))
    # ③ 同事件另一组没有本脚本 → 那一组保留、本脚本那组去掉
    cc = _cc_manifest(dict(base, PreToolUse=[("Agent", [guard]), ("Bash", ["signoff-guard.py"])]))
    out = ch.derive(cc)["hooks"]
    check("同事件别的组保留、CC 专有组去掉", [g["matcher"] for g in out.get("PreToolUse") or []], ["Bash"])
    # ④ 不含本脚本时派生结果与旧规则一致：空组照旧保留（排除只作用于因它变空的组）
    cc = _cc_manifest(dict(base, PostToolUse=[("Skill", [])]))
    out = ch.derive(cc)["hooks"]
    check("与本脚本无关的空组照旧保留", out.get("PostToolUse"), [{"matcher": "Skill", "hooks": []}])
    # ⑤ 仓内真实清单：CC 有 PreToolUse、Codex 产物里没有
    real_cc = json.loads((REPO_ROOT / "plugins" / "core" / ch.CC_MANIFEST_REL).read_text(encoding="utf-8"))
    real_cx = json.loads((REPO_ROOT / "plugins" / "core" / ch.CODEX_MANIFEST_REL).read_text(encoding="utf-8"))
    check("仓内 CC 清单挂了 PreToolUse", "PreToolUse" in real_cc["hooks"], True)
    check("仓内 Codex 清单没有 PreToolUse", "PreToolUse" in real_cx["hooks"], False)
    check("仓内 Codex 清单无一条引用本脚本",
          any(guard in json.dumps(v) for v in real_cx["hooks"].values()), False)


def main():
    print(f"[test] dispatch-name-guard.py @ {GUARD}")
    test_blocks()
    test_passes()
    test_codex_derive()
    print()
    if FAILURES:
        print(f"✗ {len(FAILURES)} 个用例失败：")
        for f in FAILURES:
            print(f"    - {f}")
        return 1
    print("✓ All dispatch-name-guard.py tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
