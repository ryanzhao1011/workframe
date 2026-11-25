#!/usr/bin/env python3
"""装机边界单测：`.workframe-config.json` 的两个字段口径、初装完整度的报法、taxonomy 出厂模板。

三组都是「静默说错当前状态」的形态，红灯不会自己出现：

- `framework_version` 只作创建史：缺键时写当时的版本，已有值重跑不动（以前每次刷新，于是一个说不清来历的数字
  冒充当前版本）。
- `project_type` 可缺：缺省的正常新装成功且不写这个键；显式给合法值照写（兼容旧 launcher）；非法值在写任何
  文件之前拒绝；CLAUDE.md 不再渲染「项目类型」一行。doctor：缺这个键不报、非法只 warn；必填字段缺失仍 error。
- 初装完整度：只有「初装步骤齐全且无待接力批次」才报「已完整落地」；没有记录、只有 hook 补记的那一步、空 steps、
  装到一半、某步失败各自如实报，结构非法报无法判断，真实批次照列。
- taxonomy 出厂模板：能力域、平台两节渲染出来显示「未配置」、没有旧示例行、判为 unconfigured；填入第一个真词后
  能读出；三个特殊标记保留。**撤回模板修改时这组断言必须红**（旧模板靠过滤示例词也判 unconfigured，只看三态
  不够）。
- 本会话插件根（skill 命令里的 `$WF_PLUGIN_ROOT`）：那一行给的是正在执行的这一份；两扇门都**不**写进
  `CLAUDE_ENV_FILE`（嵌套启动会继承、静默跑错安装），分门指针照写；子 agent 纪律片（最空一片）与 Codex 子线程补投带这一行、
  记忆注入不带。没给这一行时配方展开为空，
  模型无值可换。

执行：python tools/test_install_boundary.py（退出 0 且打印 all passed = 通过）
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
CORE = REPO_ROOT / "plugins" / "core"
SCRIPTS = CORE / "scripts"
sys.path.insert(0, str(SCRIPTS))
import project_scaffold as sc  # noqa: E402
import workframe_doctor as doc  # noqa: E402
import _taxonomy as tx  # noqa: E402

FAILS = []


def check(label, got, want):
    if got == want:
        print(f"[ok] {label}")
    else:
        print(f"[FAIL] {label}: got {got!r}, want {want!r}")
        FAILS.append(label)


def tmpdir():
    return Path(tempfile.mkdtemp(prefix="wf-install-boundary-"))


def read_cfg(project):
    return json.loads((project / ".workframe-config.json").read_text(encoding="utf-8"))


def run_scaffold(project, params):
    pfile = project.parent / f"params-{project.name}.json"
    pfile.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8", newline="")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(SCRIPTS / "project_scaffold.py"), "--project", str(project),
                           "--params", str(pfile), "--create-missing"], input="",
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)


BASE = {"project_name": "probe", "one_line_goal": "虚构目标", "business_context": "虚构背景"}


def t_framework_version():
    d = tmpdir()
    try:
        cur = sc.read_framework_version()
        sc.write_workframe_config(d, user_fields={}, project_name="p")
        check("缺键时写入当前版本", read_cfg(d).get("framework_version"), cur)
        cfg = read_cfg(d)
        cfg["framework_version"] = "0.0.1"
        (d / ".workframe-config.json").write_text(json.dumps(cfg), encoding="utf-8", newline="")
        sc.write_workframe_config(d, user_fields={}, project_name="p")
        check("已有值重跑不刷新（创建史）", read_cfg(d).get("framework_version"), "0.0.1")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_project_type_scaffold():
    d = tmpdir()
    try:
        p1 = d / "fresh"
        r = run_scaffold(p1, BASE)
        check("缺省 project_type 的正常新装：exit 0", r.returncode, 0)
        if r.returncode == 0:
            check("缺省时不写 project_type", "project_type" in read_cfg(p1), False)
            check("缺省时必填字段照写", sorted(k for k in ("dormant_profile", "role_profile") if k in read_cfg(p1)),
                  ["dormant_profile", "role_profile"])
            claude = (p1 / "CLAUDE.md").read_text(encoding="utf-8")
            check("CLAUDE.md 不再渲染「项目类型」行", "项目类型" in claude, False)
            check("CLAUDE.md 无占位残留", "{{" in claude, False)
            check("新装不建 projects/evals/", (p1 / "projects" / "evals").exists(), False)
        p2 = d / "explicit"
        r = run_scaffold(p2, dict(BASE, project_type="product-work"))
        check("显式合法 project_type：exit 0", r.returncode, 0)
        if r.returncode == 0:
            check("显式合法值照写", read_cfg(p2).get("project_type"), "product-work")
        p4 = d / "null"
        r = run_scaffold(p4, dict(BASE, project_type=None))
        check("显式 null：写前拒绝、目标目录不存在", (r.returncode != 0, p4.exists()), (True, False))
        check("显式 null：文案点名字段", "project_type" in r.stdout + r.stderr, True)
        p5 = d / "bare"
        p5.mkdir()
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        r = subprocess.run([sys.executable, str(SCRIPTS / "project_scaffold.py"), "--project", str(p5)], input="",
                           capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
        check("裸 scaffold（无 --params）：exit 0", r.returncode, 0)
        manifest = json.loads((CORE / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
        got = read_cfg(p5) if (p5 / ".workframe-config.json").is_file() else {}
        check("裸 scaffold：不写 project_type、版本取自插件清单", ("project_type" in got, got.get("framework_version")), (False, manifest))
        tax = p5 / "projects" / "specs" / "_meta" / "taxonomy.md"
        tt = tax.read_text(encoding="utf-8") if tax.is_file() else ""
        check("裸 scaffold：落盘词表显示未配置、无旧示例", ("（未配置）" in tt, "示例能力" in tt or "示例平台" in tt), (True, False))
        check("裸 scaffold：不建 evals、照建 proposals 与 archive",
              ((p5 / "projects/evals").exists(), (p5 / "projects/proposals/pending").is_dir(), (p5 / "projects/archive").is_dir()),
              (False, True, True))
        p3 = d / "bad"
        r = run_scaffold(p3, dict(BASE, project_type="nonsense"))
        check("非法 project_type：非零退出", r.returncode != 0, True)
        check("非法 project_type：写前拒绝、目标目录不存在", p3.exists(), False)
        try:
            sc.preflight_params(dict(sc.PARAM_DEFAULTS, **BASE))
            check("preflight 对缺省 project_type 放行", True, True)
        except sc.ParamError as e:
            check("preflight 对缺省 project_type 放行", str(e), "no error")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def doctor_config(cfg):
    d = tmpdir()
    try:
        (d / ".workframe-config.json").write_text(json.dumps(cfg), encoding="utf-8", newline="")
        return doc.check_config({"paths": doc.Paths(d)})
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_project_type_doctor():
    good = {"dormant_profile": "normal", "role_profile": "software-team", "project_name": "p"}
    levels = lambda rows: sorted({lv for lv, _m in rows})
    check("doctor：缺 project_type 不报（ok）", levels(doctor_config(good)), ["ok"])
    rows = doctor_config(dict(good, project_type="nonsense"))
    check("doctor：project_type 非法只 warn", levels(rows), ["warn"])
    check("doctor：project_type 合法照常 ok", levels(doctor_config(dict(good, project_type="product-work"))), ["ok"])
    rows = doctor_config({k: v for k, v in good.items() if k != "dormant_profile"})
    check("doctor：必填 dormant_profile 缺失仍 error", "error" in levels(rows), True)


def completeness(setup, agents_md=True):
    d = tmpdir()
    try:
        P = doc.Paths(d)
        P.state.mkdir(parents=True, exist_ok=True)
        if agents_md:
            (d / "AGENTS.md").write_text("# x\n", encoding="utf-8", newline="")
        if setup is not None:
            P.setup_state.write_text(setup if isinstance(setup, str) else json.dumps(setup),
                                     encoding="utf-8", newline="")
        try:
            return doc.check_init_completeness({"paths": P})
        except Exception as e:                 # 抛错也落成一格红，不让整个套件崩在这一行
            return [("<raised>", f"{type(e).__name__}: {e}")]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def t_completeness():
    full = {"scaffold": "ok", "subscribe": "ok", "agents_md": "ok"}
    no_record = "未保留完整初装记录；当前无待接力批次"
    cases = [
        ("没有 setup-state.json", None, "info", no_record),
        ("只有 hook 补记的 agents_md", {"steps": {"agents_md": "ok"}}, "info", no_record),
        ("空 steps", {"steps": {}}, "info", "没走完"),
        ("只跑了 scaffold", {"steps": {"scaffold": "ok"}}, "info", "没走完"),
        ("subscribe 失败", {"steps": {"scaffold": "ok", "subscribe": "failed", "agents_md": "ok"}}, "info", "失败"),
        ("初装齐全、无批次", {"steps": full}, "ok", "已完整落地"),
        ("结构非法（batches 不是列表）", {"steps": full, "pending_work": {"batches": {}}}, "info", "无法判断"),
        ("解析失败", "{not json", "info", "无法判断"),
        ("批次 pace 不认识", {"steps": full, "pending_work": {"batches": [{"name": "a", "pace": "oops", "files": ["a"]}]}},
         "info", "无法判断"),
        ("批次 files 不是列表", {"steps": full, "pending_work": {"batches": [{"name": "a", "pace": "relay", "files": "a.md"}]}},
         "info", "无法判断"),
        ("批次 files 里有非字符串", {"steps": full, "pending_work": {"batches": [{"name": "a", "pace": "undecided", "files": [1]}]}},
         "info", "无法判断"),
        ("批次 files 是数字", {"steps": full, "pending_work": {"batches": [{"name": "a", "pace": "relay", "files": 1}]}},
         "info", "无法判断"),
    ]
    for label, setup, lv, needle in cases:
        rows = completeness(setup)
        check(f"完整度：{label} → {lv}", [r[0] for r in rows], [lv])
        check(f"完整度：{label} 文案含「{needle}」", needle in "；".join(m for _l, m in rows), True)
    rows = completeness({"steps": full, "pending_work": {"batches": [
        {"name": "b1", "pace": "relay", "files": ["a"], "target_skill": "x"}]}})
    check("完整度：真实待接力批次照列", any("待接力 1 批" in m for _l, m in rows), True)
    check("完整度：有批次时不报完整落地", any(lv == "ok" for lv, _m in rows), False)


def t_taxonomy_template():
    text = (CORE / "templates" / "taxonomy-template.md").read_text(encoding="utf-8")
    words = tx.parse(text)
    check("模板：能力域判 unconfigured", tx.section_state(words.get("能力域")), tx.UNCONFIGURED)
    check("模板：平台判 unconfigured", tx.section_state(words.get("平台")), tx.UNCONFIGURED)
    check("模板：特殊标记三个真词保留", words.get("特殊标记"), ["moc", "demo", "external-reference"])
    old = [w for w in tx.EXAMPLE_WORDS if w in text]
    check("模板：不再出现旧示例词", old, [])
    sect = lambda name: text.split(f"\n## {name}\n", 1)[1].split("\n## ", 1)[0]   # 节标题行，不是引言里的字面
    check("模板：能力域节写明「未配置」", "未配置" in sect("能力域"), True)
    check("模板：平台节写明「未配置」", "未配置" in sect("平台"), True)
    # 用户照提示加入第一个真词：在「未配置」行之后插一行表格数据
    lines = text.split("\n")
    i = next(k for k, ln in enumerate(lines) if ln.startswith("|") and "未配置" in ln
             and k > text[:text.index("\n## 能力域\n")].count("\n"))
    lines.insert(i + 1, "| `风控` | 首个真实能力词 |")
    got = tx.parse("\n".join(lines)).get("能力域")
    check("模板：加入第一个真词后能读出", got, ["风控"])


PROBE_ENV_EXPORT = r'''
import importlib.util, os, sys
sys.argv = ["session-start-prep.py", "--harness", sys.argv[2]]
spec = importlib.util.spec_from_file_location("ssp", os.environ["WF_TEST_SSP"])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.write_plugin_root()
'''


def t_plugin_root_line():
    import _harness
    root = (CORE).resolve().as_posix()
    line = _harness.plugin_root_line()
    check("本会话插件根一行给的是正在执行的这一份", f"本会话插件根：{root}" in line, True)
    check("那一行点名 $WF_PLUGIN_ROOT", "`$WF_PLUGIN_ROOT`" in line, True)
    d = tmpdir()
    try:
        (d / ".workframe-config.json").write_text('{"project_name": "p"}', encoding="utf-8", newline="")
        envf = d / "session-env.sh"
        for door in ("cc", "codex"):
            if envf.exists():
                envf.unlink()
            env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "CODEX"))}
            env.update(CLAUDE_ENV_FILE=str(envf), CLAUDE_PROJECT_DIR=str(d), PYTHONUTF8="1",
                       WF_TEST_SSP=str(SCRIPTS / "session-start-prep.py"))
            subprocess.run([sys.executable, "-c", PROBE_ENV_EXPORT, "x", door], cwd=str(d), env=env, input="",
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            check(f"{door} 门：不写 CLAUDE_ENV_FILE", envf.exists(), False)
            ptr = d / ".workframe" / "state" / f"plugin-root.{door}.txt"
            check(f"{door} 门：分门指针照写（前提：被测函数确实跑到了）",
                  ptr.read_text(encoding="utf-8").strip() if ptr.is_file() else None, root)
        # 子 agent：根行随纪律片走，记忆注入不带（它的正文由项目记忆决定、可能本来就贴着 CC 单条上限；
        # 双门对照工具也按「以记忆标记起头」认这份附件）
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "CODEX"))}
        env.update(CLAUDE_PROJECT_DIR=str(d), PYTHONUTF8="1")

        def hook(script, args, payload):
            r = subprocess.run([sys.executable, str(SCRIPTS / script), *args], input=json.dumps(dict(payload, cwd=str(d))),
                               cwd=str(d), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=60)
            try:
                return r.returncode, json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
            except Exception:
                return r.returncode, ""
        rc, ctx = hook("subagent-memory-inject.py", ["--harness", "cc"], {"agent_type": "core:dev"})
        check("子 agent 记忆注入：两份记忆都拿不出时零输出", (rc, ctx), (0, ""))
        mem = d / ".workframe" / "agent-memory" / "shared"
        mem.mkdir(parents=True)
        (mem / "MEMORY.md").write_text("# shared\n\n- 一条\n", encoding="utf-8", newline="")
        rc, ctx = hook("subagent-memory-inject.py", ["--harness", "cc"], {"agent_type": "core:dev"})
        check("子 agent 记忆注入：以记忆标记起头、不带根行",
              (rc, ctx.startswith("[workframe] 共享记忆注入"), "本会话插件根" in ctx), (0, True, False))
        listing = json.loads(subprocess.run(
            [sys.executable, str(SCRIPTS / "inject-context.py"), "--harness", "cc", "--scope", "sub", "--dry-run"],
            cwd=str(d), env=env, input="", capture_output=True, text=True, encoding="utf-8", timeout=60).stdout)
        n = len(listing["shards"])
        texts = [hook("inject-context.py", ["--harness", "cc", "--scope", "sub", "--shard", f"{i}/{n}"],
                      {"agent_type": "core:dev", "session_id": "s0"})[1] for i in range(1, n + 1)]
        check("CC 子 agent 各片：根行恰出现一次", sum(x.count(f"本会话插件根：{root}") for x in texts), 1)
        roomiest = min(range(n), key=lambda i: listing["shards"][i]["chars"])
        check("CC 子 agent：根行在最空的一片", f"本会话插件根：{root}" in texts[roomiest], True)
        check("CC 子 agent：每片仍以纪律片头起头", all("[workframe 必载纪律 · 子 agent · 第" in x[:300] for x in texts), True)
        rc, ctx = hook("inject-context.py", ["--harness", "codex", "--scope", "prompt"], {"session_id": "s1", "turn_id": "t1"})
        check("Codex 子线程补投：根行恰一次", (rc, ctx.count(f"本会话插件根：{root}")), (0, 1))
        rows = [json.loads(x) for x in (d / ".workframe" / "state" / "inject-log.jsonl").read_text(
            encoding="utf-8").splitlines()]
        u16 = lambda s: len(s.encode("utf-16-le")) // 2
        logged = {r["shard"]: (r["chars"], r["sha256"]) for r in rows if r.get("session_id") == "s0"}
        import hashlib
        want = {f"{i + 1}/{n}": (u16(x), hashlib.sha256(x.encode("utf-8")).hexdigest()) for i, x in enumerate(texts)}
        check("inject-log 记的字符数与摘要就是投出去的那段", {k: v[0] for k, v in logged.items()}, {k: v[0] for k, v in want.items()})
        check("inject-log 的摘要就是投出去那段的 sha256", {k: v[1] for k, v in logged.items()},
              {k: v[1] for k, v in want.items()})
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    for fn in (t_framework_version, t_project_type_scaffold, t_project_type_doctor, t_completeness,
               t_taxonomy_template, t_plugin_root_line):
        fn()
    if FAILS:
        print(f"{len(FAILS)} failed")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
