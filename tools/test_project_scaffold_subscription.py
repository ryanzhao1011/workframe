#!/usr/bin/env python3
"""`project_scaffold` 订阅那一层的**行为**单测——决定「装机成没成功」的就是这一层。

**为什么必须有这份文件**（它补的是一个被实证过的假绿）：`_settings_io` 那一层有几十条断言，
而**上层**（`preflight_subscription` / `write_subscription` / main 的退出码与记步）此前一条也没有。
独立验证方一次突变同时毁掉三处——成对校验的两个方向 ＋ 承重档的 `return True`——
之后 `validate.py` **215 条全绿**，而行为差是：

    退出码 1 → 0 ；`setup-state.subscribe` 从 `failed: …` → `ok`

即「装机失败」被整条改写成「装机成功、断点记录说订阅已完成」，且没有任何闸说话。
本文件的每一组都钉在**外部可观测的事实**上（退出码 / 文件在不在 / 文件字节 / setup-state 的值），
不钉源码字面——`check_scaffold_workframe_config_merge_mode` 那道闸已经管字面，
它挡得住「把某一半摘掉」，**挡不住「字面照旧、判定被架空」**，那正是本文件要挡的。

跑法：`python tools/test_project_scaffold_subscription.py`（末行打 `all passed`）。
由 validate 的 `check_scaffold_subscription_unit_tests_pass` 驱动。

**隔离**：每组自建临时项目目录，`HOME` / `USERPROFILE` 指向沙盒 ⇒ 用户级注册表落沙盒里，
真实用户目录零接触（末组另有一条对账断言）。不起 codex、不碰网络。

**一条曾经的空白，现在被 [5b] 顺带咬住了（记下来是因为它的成因会复发）**：
`write_subscription` 里 `flags.append("用户级市场注册表里这个名字已被别的源占用…")`
这一句此前**本套件与姊妹套件都不覆盖**——判据改恒假、两个套件全绿（独立验证实测）。
[5b] 加的「setup-state 两处冲突都记下了」那条断言**同时要求两个 flag 都在**，
于是它连带咬住了这一句（实测：只毁注册表那一条 flag ⇒ 本套件红、翻红的正是那条断言）。
**但这是连带、不是专门覆盖**：它依赖 [5b] 那个「项目侧与注册表侧同时冲突」的 fixture。
真正独立的那一格是「**只有**注册表侧冲突、项目侧不冲突」，**至今没有 fixture**——
哪天有人把 [5b] 的 fixture 简化成只冲突一侧，这一句会静默回到无人看守的状态。
"""

import json
import os
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
SCAFFOLD = REPO_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
sys.dont_write_bytecode = True

FAILURES = []

SRC = {"source": "directory", "path": "C:\\fw\\claude-workframe"}
OTHER = {"source": "github", "repo": "someone/else"}
SUB = {"marketplace_name": "workframe", "source": SRC,
       "install_location": "C:\\fw\\claude-workframe"}


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'FAIL'}] {label}: got={got!r}" + ("" if ok else f" want={want!r}"))
    if not ok:
        FAILURES.append(label)


def run(target, params_obj, home, extra_args=()):
    """真跑脚本（子进程），返回 CompletedProcess。不 import——退出码是被测对象之一。"""
    Path(home, ".claude", "plugins").mkdir(parents=True, exist_ok=True)
    params = Path(target).parent / (Path(target).name + ".params.json")
    params.write_text(json.dumps({"project_name": "Sandbox", "one_line_goal": "g",
                                  "business_context": "b", **params_obj},
                                 ensure_ascii=False), encoding="utf-8", newline="")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               HOME=str(home), USERPROFILE=str(home))
    env.pop("HOMEDRIVE", None)
    env.pop("HOMEPATH", None)
    return subprocess.run([sys.executable, str(SCAFFOLD), "--project", str(target),
                           "--params", str(params), "--create-missing", *extra_args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=300)


def steps_of(target):
    f = Path(target) / ".workframe" / "state" / "setup-state.json"
    if not f.is_file():
        return {}
    try:
        return (json.loads(f.read_text(encoding="utf-8")) or {}).get("steps") or {}
    except Exception:
        return {"__unreadable__": True}


def settings_of(target):
    f = Path(target) / ".claude" / "settings.json"
    return json.loads(f.read_bytes().decode("utf-8-sig")) if f.is_file() else None


def registry_of(home):
    f = Path(home) / ".claude" / "plugins" / "known_marketplaces.json"
    return json.loads(f.read_bytes().decode("utf-8-sig")) if f.is_file() else None


# ---- 1. 成对校验：两个方向各自独立，且在写任何文件之前退出 ----

def test_pairing(root):
    print("[1] 开关与参数块必须成对（写任何文件之前退出）")
    for tag, params_obj, args in (("只给开关不给块", {}, ["--write-subscription"]),
                                  ("只给块不给开关", {"subscription": SUB}, [])):
        home = root / f"home_{tag}"
        target = root / f"proj_{tag}"
        r = run(target, params_obj, home, args)
        check(f"{tag} ⇒ 退出码 1", r.returncode, 1)
        check(f"{tag} ⇒ 目标目录未被建出来", target.exists(), False)
        check(f"{tag} ⇒ 用户级注册表未被创建", registry_of(home), None)
        check(f"{tag} ⇒ 报错点名缺的是哪一半",
              ("--write-subscription" in r.stdout), True)


def test_missing_fields(root):
    print("[2] subscription 块字段必填")
    for tag, sub in (("缺 marketplace_name", {k: v for k, v in SUB.items() if k != "marketplace_name"}),
                     ("缺 source", {k: v for k, v in SUB.items() if k != "source"}),
                     ("缺 install_location", {k: v for k, v in SUB.items() if k != "install_location"}),
                     ("source 不是对象", {**SUB, "source": "owner/repo"})):
        home = root / f"home_f_{tag}"
        target = root / f"proj_f_{tag}"
        r = run(target, {"subscription": sub}, home, ["--write-subscription"])
        check(f"{tag} ⇒ 退出码 1", r.returncode, 1)
        check(f"{tag} ⇒ 零写入（目标目录未建）", target.exists(), False)


# ---- 3. 承重档：① 写不成 ⇒ 非零退出 ＋ 原文件零改动 ＋ setup-state 记 failed ----

def test_load_bearing(root):
    print("[3] ① 项目 settings 是承重的")
    home = root / "home_lb"
    target = root / "proj_lb"
    (target / ".claude").mkdir(parents=True)
    broken = b'{"permissions": {"allow": [ }'
    (target / ".claude" / "settings.json").write_bytes(broken)
    r = run(target, {"subscription": SUB}, home, ["--write-subscription"])
    # 这四条是那次「一次突变同时毁三处」的行为差全集——任一条松掉，那个突变体就通过
    check("坏 settings ⇒ 退出码 1", r.returncode, 1)
    check("坏 settings ⇒ 原文件一个字节没动", (target / ".claude" / "settings.json").read_bytes(), broken)
    check("坏 settings ⇒ setup-state 的 subscribe 记 failed",
          str(steps_of(target).get("subscribe", "")).startswith("failed"), True)
    check("坏 settings ⇒ 打出手动补丁", "手动补丁" in r.stdout, True)
    check("坏 settings ⇒ 没有打出「订阅写好了」那类成功行", "wrote subscription" in r.stdout, False)


# ---- 4. 补强档：② 写不成 ⇒ 仍然 rc 0，但痕迹必须留下 ----

def test_supplementary(root):
    print("[4] ② 用户级注册表是补强（失败不拖垮装机，但要留痕）")
    home = root / "home_sup"
    (home / ".claude" / "plugins").mkdir(parents=True)
    (home / ".claude" / "plugins" / "known_marketplaces.json").write_bytes(b'{oops')
    target = root / "proj_sup"
    r = run(target, {"subscription": SUB}, home, ["--write-subscription"])
    check("用户级坏 ⇒ 退出码 0（不拖垮装机）", r.returncode, 0)
    check("用户级坏 ⇒ 项目 settings 照样写成",
          (settings_of(target) or {}).get("enabledPlugins", {}).get("core@workframe"), True)
    check("用户级坏 ⇒ 原文件未被覆盖",
          (home / ".claude" / "plugins" / "known_marketplaces.json").read_bytes(), b'{oops')
    check("用户级坏 ⇒ 打 WARNING 而不是 ERROR", "WARNING" in r.stdout, True)
    # 痕迹：只在 stdout 里的话，会话一结束就没了
    check("用户级坏 ⇒ setup-state 仍记 ok（它不是承重的）",
          str(steps_of(target).get("subscribe", "")).startswith("ok"), True)
    check("用户级坏 ⇒ setup-state 的 detail 说出了这件事",
          "用户级市场注册表未写入" in str(steps_of(target).get("subscribe", "")), True)


# ---- 5. 冲突态：rc 0，但「落盘的不是你这次选的那个源」必须留持久痕迹 ----

def test_conflict(root):
    print("[5] 同名市场、源不同 ⇒ 不覆盖，且痕迹进 setup-state")
    home = root / "home_cf"
    target = root / "proj_cf"
    (target / ".claude").mkdir(parents=True)
    (target / ".claude" / "settings.json").write_text(
        json.dumps({"extraKnownMarketplaces": {"workframe": {"source": OTHER}}},
                   ensure_ascii=False, indent=2), encoding="utf-8", newline="")
    r = run(target, {"subscription": SUB}, home, ["--write-subscription"])
    check("冲突 ⇒ 退出码 0", r.returncode, 0)
    check("冲突 ⇒ 原市场源未被覆盖",
          settings_of(target)["extraKnownMarketplaces"]["workframe"]["source"], OTHER)
    check("冲突 ⇒ setup-state 的 detail 点名了「保留了原声明」",
          "保留了原声明" in str(steps_of(target).get("subscribe", "")), True)
    check("冲突 ⇒ 仍以 ok 开头（doctor 的失败判定不受影响）",
          str(steps_of(target).get("subscribe", "")).startswith("ok"), True)


# ---- 5b. 冲突 ＋ changed=False：终端上那句话不许说「已经对了」 ----

def test_conflict_unchanged(root):
    """「没变」有两个成因，文案必须分得开。

    上一组（`test_conflict`）的 fixture 只写了 `extraKnownMarketplaces`、没写 `enabledPlugins`，
    所以 merge 必然产生变化、走的是 `changed=True` 那条分支——**它覆盖不到本组**。
    本组把两个键都按冲突源写满，于是 merge 结果与现状完全相同 ⇒ `changed=False`，
    而落盘的源**不是**本次传入的那个。此时若照常打「已是目标状态」，就是一句假安心，
    且它紧挨着的下一行正好在说「与本次的 … 不同」——两行直接互斥。

    **本组与「纯幂等重跑」那组（test_happy_and_idempotent）必须给出可分辨的读数**：
    那边 stdout 要有「已是目标状态」，这边一定不能有。两条断言各在各的组里，合起来才成立。
    """
    print("[5b] 冲突 ＋ changed=False ⇒ 不许说「已经对了」")
    home = root / "home_cfu"
    target = root / "proj_cfu"
    (target / ".claude").mkdir(parents=True)
    # 两个键都写满 ⇒ merge 结果 == 现状 ⇒ changed=False
    (target / ".claude" / "settings.json").write_text(
        json.dumps({"extraKnownMarketplaces": {"workframe": {"source": OTHER}},
                    "enabledPlugins": {"core@workframe": True}},
                   ensure_ascii=False, indent=2), encoding="utf-8", newline="")
    # 用户级注册表同样预置成别的源 ⇒ 注册表侧也走 changed=False 的冲突分支
    reg_dir = home / ".claude" / "plugins"
    reg_dir.mkdir(parents=True, exist_ok=True)
    (reg_dir / "known_marketplaces.json").write_text(
        json.dumps({"workframe": {"source": OTHER, "installLocation": "C:\\elsewhere",
                                  "lastUpdated": "2026-01-01T00:00:00.000Z"}},
                   ensure_ascii=False, indent=2), encoding="utf-8", newline="")
    before = (target / ".claude" / "settings.json").read_bytes()
    reg_before = (reg_dir / "known_marketplaces.json").read_bytes()

    r = run(target, {"subscription": SUB}, home, ["--write-subscription"])
    check("冲突+重跑 ⇒ 退出码 0", r.returncode, 0)
    # 先坐实这一格真的走了 changed=False 那条分支（否则下面的文案断言是空转）
    check("冲突+重跑 ⇒ 项目 settings 字节未变（确实是 changed=False 那条分支）",
          (target / ".claude" / "settings.json").read_bytes(), before)
    check("冲突+重跑 ⇒ 用户级注册表字节未变（同上）",
          (reg_dir / "known_marketplaces.json").read_bytes(), reg_before)
    check("冲突+重跑 ⇒ 没有生成备份（changed=False 的旁证）",
          sorted(x.name for x in (target / "logs").glob("*.bak-*")), [])

    check("冲突+重跑 ⇒ stdout 不出现「已是目标状态」", "已是目标状态" in r.stdout, False)
    check("冲突+重跑 ⇒ stdout 不出现「已注册为同一个源」", "已注册为同一个源" in r.stdout, False)
    check("冲突+重跑 ⇒ 项目侧点名「落盘的仍是原来那个源」",
          "落盘的仍是原来那个源" in r.stdout, True)
    check("冲突+重跑 ⇒ 注册表侧点名「这个名字仍注册为」",
          "这个名字仍注册为" in r.stdout, True)
    check("冲突+重跑 ⇒ 两处都明说「这不是『已经对了』」",
          r.stdout.count("这不是「已经对了」"), 2)
    check("冲突+重跑 ⇒ setup-state 两处冲突都记下了",
          ("保留了原声明" in str(steps_of(target).get("subscribe", ""))
           and "已被别的源占用" in str(steps_of(target).get("subscribe", ""))), True)


# ---- 6. 正路 + 幂等：重跑零改动、不堆备份 ----

def test_happy_and_idempotent(root):
    print("[6] 正路与重跑幂等")
    home = root / "home_ok"
    target = root / "proj_ok"
    r1 = run(target, {"subscription": SUB}, home, ["--write-subscription"])
    check("首跑 ⇒ 退出码 0", r1.returncode, 0)
    st = settings_of(target)
    check("写的是 SOURCE 对象，不是 root / installLocation",
          st["extraKnownMarketplaces"]["workframe"], {"source": SRC})
    check("启用位写好", st["enabledPlugins"]["core@workframe"], True)
    reg = registry_of(home) or {}
    check("用户级注册三字段齐全", sorted(reg.get("workframe", {})),
          ["installLocation", "lastUpdated", "source"])
    check("首跑 ⇒ setup-state 记 ok 且无附注", steps_of(target).get("subscribe"), "ok")

    before = (target / ".claude" / "settings.json").read_bytes()
    reg_before = (home / ".claude" / "plugins" / "known_marketplaces.json").read_bytes()
    r2 = run(target, {"subscription": SUB}, home, ["--write-subscription"])
    check("重跑 ⇒ 退出码 0", r2.returncode, 0)
    # 与 [5b]（冲突 ＋ changed=False）成对：**纯幂等重跑这一格必须说「已是目标状态」**，
    # 那一格必须不说。两条断言分处两组，缺任一条都分辨不出「没变」的两个成因
    check("重跑（无冲突）⇒ stdout 说「已是目标状态」", "已是目标状态" in r2.stdout, True)
    check("重跑（无冲突）⇒ stdout 不出现冲突话术", "这不是「已经对了」" in r2.stdout, False)
    check("重跑 ⇒ 项目 settings 字节未变", (target / ".claude" / "settings.json").read_bytes(), before)
    check("重跑 ⇒ 用户级注册字节未变",
          (home / ".claude" / "plugins" / "known_marketplaces.json").read_bytes(), reg_before)
    baks = sorted(x.name for x in (target / "logs").glob("*.bak-*"))
    check("重跑 ⇒ 一份备份都没多出来（首跑也没有：两处原文件本来都不存在）", baks, [])
    check("重跑 ⇒ 原文件旁边没有备份（备份落 logs/，不落 .claude/）",
          sorted(x.name for x in (target / ".claude").glob("*.bak-*")), [])


# ---- 7. 备份落点：logs/ 而不是原文件旁边 ----

def test_backup_location(root):
    print("[7] 备份落 logs/（managed gitignore 块覆盖它；`*.bak-*` 不被任何条目覆盖）")
    home = root / "home_bk"
    target = root / "proj_bk"
    run(target, {"subscription": SUB}, home, ["--write-subscription"])      # 首跑，建出两份文件
    other_sub = {**SUB, "marketplace_name": "second"}
    run(target, {"subscription": other_sub}, home, ["--write-subscription"])  # 二跑，内容真变 ⇒ 备份
    proj_baks = sorted(x.name for x in (target / "logs").glob("settings.json.bak-*"))
    reg_baks = sorted(x.name for x in (target / "logs").glob("known_marketplaces.json.bak-*"))
    check("项目 settings 的备份落在 logs/", len(proj_baks), 1)
    check("用户级注册表的备份也落在本项目 logs/（它在项目外，按装机审计跟着这次装机走）",
          len(reg_baks), 1)
    check("`.claude/` 下没有备份", sorted(x.name for x in (target / ".claude").glob("*.bak-*")), [])
    check("用户主目录下没有备份",
          sorted(x.name for x in (home / ".claude" / "plugins").glob("*.bak-*")), [])
    check("两个市场都在了（第二次是追加不是覆盖）",
          sorted(settings_of(target)["extraKnownMarketplaces"]), ["second", "workframe"])


# ---- 8. 不带开关的老调用：行为一字不变 ----

def test_no_flag(root):
    print("[8] 不带开关的老调用零回归")
    home = root / "home_nf"
    target = root / "proj_nf"
    r = run(target, {}, home)
    check("退出码 0", r.returncode, 0)
    check("没写项目 settings", (target / ".claude" / "settings.json").exists(), False)
    check("没写用户级注册表", registry_of(home), None)
    check("setup-state 里没有 subscribe 这一步", "subscribe" in steps_of(target), False)


REAL_REG = Path.home() / ".claude" / "plugins" / "known_marketplaces.json"
REAL_BYTES = REAL_REG.read_bytes() if REAL_REG.is_file() else None


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        test_pairing(root)
        test_missing_fields(root)
        test_load_bearing(root)
        test_supplementary(root)
        test_conflict(root)
        test_conflict_unchanged(root)
        test_happy_and_idempotent(root)
        test_backup_location(root)
        test_no_flag(root)
    print("[9] 真实用户级注册表零接触")
    check("真实 known_marketplaces.json 字节未变",
          REAL_REG.read_bytes() if REAL_REG.is_file() else None, REAL_BYTES)
    if FAILURES:
        print(f"\n{len(FAILURES)} 条失败: {FAILURES}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
