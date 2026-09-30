#!/usr/bin/env python3
"""项目 skills 到达路径（`.agents/skills/` 真实源 ＋ `.claude/skills` 目录链接）的常驻单测。

被测：`_harness.project_skills_dir / skills_link_present / skills_link_dangling`、
`project_scaffold.ensure_project_skills_dir / ensure_project_skills_link / gitignore_required_entries`、
以及 `session-start-prep.py` 每次会话对链接的补建。爆炸半径档 4：它们决定「项目 skill 落在哪、
`.gitignore` 要不要忽略 `.claude/skills`」，判错时装机 exit 0、doctor 全绿，只有 CC 门看不到项目 skill。

守的形态，每格一个 fixture、各配自己的失败方式：
1. **全新**：建真实源 ＋ 链接；解析到中立目录；`.gitignore` 要求忽略 `.claude/skills`（无尾斜杠）；重跑幂等。
2. **悬空链接**（链接在、目标不在）：scaffold **不抛** `FileExistsError`、不建、不删、skipped 里报「悬空」；
   `.gitignore` 仍要求忽略（按链接判）**且** `project_skills_dir()` 仍解析到中立目录——两边同一口径。
3. **clone 形态**（只有真实源、`.claude/skills` 位置空）：补链接；经链接读得到真实源里的文件；再跑不变；
   `session-start-prep.py --harness cc` 在这一形态上建成并打一行。
4. **真目录**（已装项目）：一个字节不动、不建链接、不建 `.agents/`；解析到 `.claude/skills`；`.gitignore` 不要求
   忽略；`session-start-prep.py` 同样不动。
5. **两个真目录**：不动，报冲突。 6. **位置上是文件**：不建、不抛。
7. **`.claude/skills` 的七种形态**（`_harness.skills_cc_form`）：每种断言判定结果、SessionStart 的动作与输出、doctor
   `skills_link` 的级别与文案；目标不存在的链接（F4）由 SessionStart 重指、原目标与真实源逐文件不变；对真目录调
   解链接原语必然失败且内容不变；两个进程同时重指同一悬空链接都不报错、结果指向本项目。
8. **忽略写法**：四个产出点（必需条目 / managed 写入行 / 模板行 / doctor 必需条目）各一条**平台无关、整行相等**的
   字面断言（字面写死在本文件，不从被测模块取——从被测常量取的话常量改回旧写法时这里跟着变、恒绿）；旧写法判
   「legacy」不算覆盖、managed block 内就地升级（幂等、CRLF 与 BOM 保留、marker 外不动）；真目录 ＋ 无 `.gitignore`
   时模板那一行按整行剥掉、项目 skill 不被忽略。**POSIX 才红的两格**（旧写法盖不住链接本身）在 Windows 恒过，
   证据来自 Linux 容器跑同一文件。
9. **SessionStart 的 stdout 形态**：补建 / 重指的那一次 CC 门整次 stdout 是一个 JSON 对象（`reloadSkills: true`，
   `additionalContext` 与同一状态下纯文本模式的输出行尾归一后逐字相同）；没触发时不是 JSON；Codex 门结构不变、
   不带 `reloadSkills`、措辞是 Codex 版；JSON 串自检失败退回纯文本。
10. **SessionStart 补建 AGENTS.md 那句话**：项目根有 CLAUDE.md 时补半句（CLAUDE.md 里的项目内容不会自动搬过来 ＋
   「模型不代填」），没有 CLAUDE.md 时不带；AGENTS.md 已在时整句不出。与第 9 条共用同一个 SessionStart fixture。
链接用 `project_scaffold._make_dir_link` 建（Windows junction / POSIX symlink），与被测方同一条路径。
"""

import importlib.util
import json
import os
import shutil
import stat
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
sys.dont_write_bytecode = True

import _harness as H  # noqa: E402
import project_scaffold as ps  # noqa: E402
import workframe_doctor as doc  # noqa: E402

FAILURES = []
NEUTRAL = H.SKILLS_DIR_NEUTRAL
CC = H.SKILLS_DIR_CC
# 四个产出点的字面断言用的**写死**的期望值——不取 `ps.SKILLS_LINK_IGNORE_LINE`：被测常量改回带尾斜杠时，
# 取常量的断言会跟着变、恒绿
LINK_LINE = ".claude/skills"


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'FAIL'}] {label}: got={got!r}" + ("" if ok else f" want={want!r}"))
    if not ok:
        FAILURES.append(label)


def rm(d):
    def _onerr(f, p, e):
        try:
            os.chmod(p, stat.S_IWRITE)
            f(p)
        except Exception:
            pass
    shutil.rmtree(d, onerror=_onerr)


class Proj:
    """一次性 fixture 项目；退出时整树删除（含链接——`rmtree` 只解链接不穿透）。"""

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wf-skills-"))
        self.root = self.tmp / "proj"
        self.root.mkdir()
        return self.root

    def __exit__(self, *a):
        rm(self.tmp)


def mk_neutral(root):
    d = root / NEUTRAL / "prd-style"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("# probe\n", encoding="utf-8", newline="")


def mk_realdir(root):
    d = root / CC / "prd-style"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("# real\n", encoding="utf-8", newline="")


def link_ok(root):
    return H.is_dir_link(root / CC)


def test_fresh():
    print("fresh:")
    with Proj() as root:
        created, skipped = [], []
        got = ps.ensure_project_skills_dir(root, created, skipped)
        check("returns neutral", got, root / NEUTRAL)
        check("neutral is dir", (root / NEUTRAL).is_dir(), True)
        check("link built", link_ok(root), True)
        check("link_present", H.skills_link_present(root), True)
        check("dangling False", H.skills_link_dangling(root), False)
        check("resolve -> neutral", H.project_skills_dir(root), NEUTRAL)
        check("gitignore requires .claude/skills（整行、无尾斜杠）", LINK_LINE in ps.gitignore_required_entries(root), True)
        check("created 1 / skipped 0", (len(created), len(skipped)), (1, 0))
        # 经链接写入、经真实源读出——两条路径是同一份文件
        (root / CC / "x.txt").write_text("y", encoding="utf-8", newline="")
        check("through link == neutral", (root / NEUTRAL / "x.txt").read_text(encoding="utf-8"), "y")
        c2, s2 = [], []
        ps.ensure_project_skills_dir(root, c2, s2)
        check("rerun idempotent", (c2, len(s2)), ([], 1))


def test_dangling():
    print("dangling:")
    with Proj() as root:
        ps.ensure_project_skills_dir(root, [], [])
        rm(root / NEUTRAL)          # fixture 自己的目标目录，制造悬空
        # 前置读数：这一形态在三个「存在」判定上全 False，只有 lexists / 链接判定看得见它
        check("pre: lexists", os.path.lexists(root / CC), True)
        check("pre: exists False", (root / CC).exists(), False)
        check("pre: dangling", H.skills_link_dangling(root), True)
        created, skipped = [], []
        try:
            got = ps.ensure_project_skills_dir(root, created, skipped)
            raised = None
        except Exception as e:      # 修复前：FileExistsError at cc.mkdir → 脚手架整个中断
            got, raised = None, f"{type(e).__name__}: {e}"
        check("no exception", raised, None)
        check("returns neutral path", got, root / NEUTRAL)
        check("nothing created", created, [])
        check("skipped reports 悬空", any("悬空" in s for s in skipped), True)
        check("link untouched (still lexists)", os.path.lexists(root / CC), True)
        check("neutral not rebuilt", (root / NEUTRAL).exists(), False)
        # 两个消费方同一口径：都按链接判
        check("gitignore still requires .claude/skills", LINK_LINE in ps.gitignore_required_entries(root), True)
        check("managed lines still write it", LINK_LINE in ps.gitignore_managed_lines(root), True)
        check("resolve -> neutral (按链接判)", H.project_skills_dir(root), NEUTRAL)
        check("link-only helper stays out", ps.ensure_project_skills_link(root, [], []), False)
        # 从模板落盘 .gitignore：链接形态那一行保留
        c3, s3 = [], []
        ps._ensure_gitignore(root, c3, s3)
        gi = (root / ".gitignore").read_text(encoding="utf-8")
        check("template keeps the .claude/skills line", LINK_LINE in [ln.strip() for ln in gi.splitlines()], True)
        # 目标一旦建回来，链接即恢复——两边都不用再改
        mk_neutral(root)
        check("revived: dangling False", H.skills_link_dangling(root), False)
        check("revived: readable through link", (root / CC / "prd-style" / "SKILL.md").is_file(), True)


def test_clone():
    print("clone:")
    with Proj() as root:
        mk_neutral(root)            # git clone 带来的只有真实源；.claude/ 整个不在
        check("pre: no .claude", (root / ".claude").exists(), False)
        created, skipped = [], []
        check("link built", ps.ensure_project_skills_link(root, created, skipped), True)
        check("is link", link_ok(root), True)
        check("created mentions 补建", any("补建" in c for c in created), True)
        check("skipped empty", skipped, [])
        check("file readable through link", (root / CC / "prd-style" / "SKILL.md").read_text(encoding="utf-8"), "# probe\n")
        c2, s2 = [], []
        check("second call no-op", ps.ensure_project_skills_link(root, c2, s2), False)
        check("second call silent", (c2, s2), ([], []))
        check("resolve -> neutral", H.project_skills_dir(root), NEUTRAL)
        check("conflict None", H.project_skills_conflict(root), None)
    with Proj() as root:
        mk_neutral(root)
        created, skipped = [], []
        got = ps.ensure_project_skills_dir(root, created, skipped)   # 整个 scaffold 步也走补链接
        check("scaffold step builds link too", (got == root / NEUTRAL, link_ok(root), any("补建" in c for c in created)),
              (True, True, True))


def test_realdir():
    print("realdir:")
    with Proj() as root:
        mk_realdir(root)
        before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
        created, skipped = [], []
        check("link helper stays out", ps.ensure_project_skills_link(root, created, skipped), False)
        check("helper silent", (created, skipped), ([], []))
        got = ps.ensure_project_skills_dir(root, created, skipped)
        check("returns .claude/skills", got, root / CC)
        check("not a link", link_ok(root), False)
        check("no .agents created", (root / ".agents").exists(), False)
        check("skipped names cc", skipped, [str(root / CC)])
        check("tree untouched", sorted(p.relative_to(root).as_posix() for p in root.rglob("*")), before)
        check("resolve -> cc", H.project_skills_dir(root), CC)
        check("gitignore requires no skills entry at all",
              [e for e in ps.gitignore_required_entries(root) if ".claude/skills" in e or ".agents/skills" in e], [])


def test_both_real_and_file():
    print("both-real / file-at-cc:")
    with Proj() as root:
        mk_realdir(root)
        mk_neutral(root)
        created, skipped = [], []
        check("link helper stays out (both real)", ps.ensure_project_skills_link(root, created, skipped), False)
        ps.ensure_project_skills_dir(root, created, skipped)
        check("conflict reported", any("两个不同的目录" in s for s in skipped), True)
        check("nothing created", created, [])
        check("cc still real dir", (root / CC).is_dir() and not link_ok(root), True)
    with Proj() as root:
        mk_neutral(root)
        (root / ".claude").mkdir()
        (root / CC).write_text("not a dir", encoding="utf-8", newline="")
        created, skipped = [], []
        check("file at cc: helper stays out", ps.ensure_project_skills_link(root, created, skipped), False)
        try:
            ps.ensure_project_skills_dir(root, created, skipped)
            raised = None
        except Exception as e:
            raised = f"{type(e).__name__}: {e}"
        check("file at cc: no exception", raised, None)
        check("file at cc: still a file", (root / CC).is_file(), True)


# ---------------------------------------------------------------- SessionStart 侧

PREP = SCRIPTS / "session-start-prep.py"


def _fixture(root, with_state=True):
    """照 validate 的 SessionStart fixture：最小骨架，够 session-start-prep 跑到底。"""
    (root / "projects" / "modules").mkdir(parents=True, exist_ok=True)
    if with_state:
        (root / ".claude" / "workframe-state").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "agent-memory" / "shared").mkdir(parents=True, exist_ok=True)
    (root / ".workframe-config.json").write_text('{"project_name":"skills-probe"}', encoding="utf-8", newline="")
    (root / "projects" / "board.yaml").write_text(
        "summary:\n  total: 0\n  pending: 0\n  in_progress: 0\n  pending_qa: 0\n"
        "  completed: 0\n  blocked: 0\n  cancelled: 0\n  last_updated: null\n\ntasks: []\n",
        encoding="utf-8", newline="")
    (root / ".claude" / "agent-memory" / "shared" / "MEMORY.md").write_text("# 共享记忆\n", encoding="utf-8", newline="")


def run_prep(root, harness="cc", extra=(), suppress_reload=False):
    """跑一次 SessionStart。`suppress_reload=True`：同一份脚本、只把 `request_skills_reload` 换成空操作——
    得到「同一状态下纯文本模式」的输出，用来和 JSON 模式的 `additionalContext` 逐字比。"""
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root), CLAUDE_PLUGIN_ROOT=str(PLUGIN),
               CLAUDE_CODE_SESSION_ID="skills-probe-sess", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup",
                          "session_id": "skills-probe-sess", "cwd": str(root)})
    args = ["--harness", harness, *extra]
    if suppress_reload:
        code = ("import runpy, sys; sys.path.insert(0, {s!r}); import _harness; "
                "_harness.request_skills_reload = lambda: None; sys.argv = [{p!r}] + sys.argv[1:]; "
                "runpy.run_path({p!r}, run_name='__main__')").format(s=str(SCRIPTS), p=str(PREP))
        cmd = [sys.executable, "-c", code, *args]
    else:
        cmd = [sys.executable, str(PREP), *args]
    return subprocess.run(cmd, input=payload, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=180, cwd=str(root), env=env)


def as_json(stdout):
    """整次 stdout 是一个 JSON 对象吗——是则返回解析结果，否则 None（真解析器解，不看子串）。"""
    s = stdout.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        return json.loads(s)
    except ValueError:
        return None


def _norm(text, root):
    """行尾归一、去尾换行、两份夹具各自的路径归一（长名 / `resolve()` 后的形态都换：Windows 8.3 短名、macOS
    `/var` → `/private/var`）。"""
    out = text.replace("\r\n", "\n").rstrip("\n")
    for form in sorted({str(root), str(root.resolve()), str(root).replace("\\", "/"),
                        str(root.resolve()).replace("\\", "/")}, key=len, reverse=True):
        out = out.replace(form, "<ROOT>")
    return out


def test_session_start():
    print("session-start-prep:")
    # clone 形态的首个会话：补建 ＋ 安装验收同框——JSON 路径的第一个真实用例
    with Proj() as root, Proj() as twin:
        for r_ in (root, twin):
            _fixture(r_)
            mk_neutral(r_)
        r = run_prep(root)
        plain = run_prep(twin, suppress_reload=True)
        check("clone: rc 0", (r.returncode, plain.returncode), (0, 0))
        check("clone: link built", (link_ok(root), link_ok(twin)), (True, True))
        obj = as_json(r.stdout)
        check("clone: 整次 stdout 是一个 JSON 对象", obj is not None, True)
        hso = (obj or {}).get("hookSpecificOutput") or {}
        check("clone: hookEventName == SessionStart", hso.get("hookEventName"), "SessionStart")
        check("clone: reloadSkills is True（在 hookSpecificOutput 内）", hso.get("reloadSkills") is True, True)
        check("clone: 顶层只有 hookSpecificOutput", sorted((obj or {}).keys()), ["hookSpecificOutput"])
        ctx = hso.get("additionalContext", "")
        check("clone: 补建句与安装验收同框", ("已补建 .claude/skills" in ctx, "安装验收" in ctx), (True, True))
        check("clone: 补建句给出 /reload-skills 兜底、不再说「下一次会话起可用」",
              ("/reload-skills" in ctx, "下一次会话起可用" in ctx), (True, False))
        check("clone: 对照组（同一状态、不请求重扫）是纯文本", as_json(plain.stdout) is None, True)
        check("clone: additionalContext == 纯文本模式输出（行尾归一、去尾换行）", _norm(ctx, root), _norm(plain.stdout, twin))
        r2 = run_prep(root)
        check("clone: second session silent & not JSON",
              ("已补建 .claude/skills" in r2.stdout, as_json(r2.stdout) is None), (False, True))
    # Codex 门补建：结构不变、不带 reloadSkills、措辞是 Codex 版
    with Proj() as root:
        _fixture(root)
        mk_neutral(root)
        r = run_prep(root, harness="codex", extra=("--output", "hook-json"))
        obj = as_json(r.stdout)
        hso = (obj or {}).get("hookSpecificOutput") or {}
        check("codex: rc 0 / link built", (r.returncode, link_ok(root)), (0, True))
        check("codex: 结构 {hookEventName, additionalContext}，无 reloadSkills", sorted(hso.keys()),
              ["additionalContext", "hookEventName"])
        ctx = hso.get("additionalContext", "")
        check("codex: 措辞是 Codex 版", ("已补建 .claude/skills" in ctx, "Codex 门直接读" in ctx,
                                      "reload-skills" in ctx), (True, True, False))
    # 没触发的各支：stdout 不是 JSON、不含 reloadSkills（与改动前逐字节相同的并排对照在交付报告里，需要改前版本）
    def no_trigger(label, build, extra_check=None):
        with Proj() as root:
            _fixture(root)
            build(root)
            r = run_prep(root)
            check(f"{label}: rc 0", r.returncode, 0)
            check(f"{label}: 不是 JSON、无 reloadSkills", (as_json(r.stdout) is None, "reloadSkills" in r.stdout),
                  (True, False))
            check(f"{label}: 无补建句", "已补建 .claude/skills" in r.stdout, False)
            if extra_check:
                extra_check(root, r)

    def _link_then(root):
        ps.ensure_project_skills_dir(root, [], [])

    no_trigger("link-present", _link_then)
    no_trigger("realdir", mk_realdir,
               lambda root, r: check("realdir: 未动", ((root / CC).is_dir() and not link_ok(root),
                                                      (root / ".agents").exists()), (True, False)))
    no_trigger("plain（两个目录都不在）", lambda root: None,
               lambda root, r: check("plain: nothing built", ((root / CC).exists(), (root / ".agents").exists()),
                                     (False, False)))

    def _file_at_cc(root):
        mk_neutral(root)
        (root / ".claude").mkdir(exist_ok=True)
        (root / CC).write_text("not a dir", encoding="utf-8", newline="")
    no_trigger("file-at-cc", _file_at_cc,
               lambda root, r: check("file-at-cc: 一行说明 ＋ 文件原样", ("是一个普通文件" in r.stdout,
                                     (root / CC).read_text(encoding="utf-8")), (True, "not a dir")))

    def _neutral_file(root):     # 中立位置上是文件、不是目录：不建
        (root / ".agents").mkdir()
        (root / NEUTRAL).write_text("x", encoding="utf-8", newline="")
    no_trigger("neutral-not-dir", _neutral_file,
               lambda root, r: check("neutral-not-dir: nothing built", os.path.lexists(root / CC), False))

    # 建链接失败：在进程里让建链接原语抛异常（包一层 runpy，把 `project_scaffold._make_dir_link` 换成抛错）——
    # stderr 报、stdout 不是 JSON
    with Proj() as root:
        _fixture(root)
        mk_neutral(root)
        env =dict(os.environ, CLAUDE_PROJECT_DIR=str(root), CLAUDE_PLUGIN_ROOT=str(PLUGIN),
                   CLAUDE_CODE_SESSION_ID="skills-probe-sess", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        code = ("import runpy, sys; sys.path.insert(0, {s!r}); import project_scaffold as ps\n"
                "def boom(*a, **k): raise OSError('probe: link refused')\n"
                "ps._make_dir_link = boom\n"
                "sys.argv = [{p!r}] + sys.argv[1:]; runpy.run_path({p!r}, run_name='__main__')").format(
                    s=str(SCRIPTS), p=str(PREP))
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup",
                              "session_id": "skills-probe-sess", "cwd": str(root)})
        r = subprocess.run([sys.executable, "-c", code, "--harness", "cc"], input=payload, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=180, cwd=str(root), env=env)
        check("link-fails: rc 0", r.returncode, 0)
        check("link-fails: 没建成、stderr 报了", (os.path.lexists(root / CC), "目录链接未建成" in r.stderr), (False, True))
        check("link-fails: stdout 不是 JSON、无 reloadSkills", (as_json(r.stdout) is None, "reloadSkills" in r.stdout),
              (True, False))


def test_agents_md_backfill_note():
    print("session-start-prep · AGENTS.md 补建那句话：有 CLAUDE.md 才补半句")
    for label, claude_md, agents_md, want in (("有 CLAUDE.md、缺 AGENTS.md", True, False, (True, True)),
                                              ("没有 CLAUDE.md、缺 AGENTS.md", False, False, (True, False)),
                                              ("有 CLAUDE.md、AGENTS.md 已在", True, True, (False, False))):
        with Proj() as root:
            _fixture(root)
            if claude_md:
                (root / "CLAUDE.md").write_text("# p\n\n## 项目目标\n\n虚构\n", encoding="utf-8", newline="")
            if agents_md:
                (root / "AGENTS.md").write_text("# AGENTS.md\n", encoding="utf-8", newline="")
            r = run_prep(root)
            line = [ln for ln in r.stdout.splitlines() if "已补建 AGENTS.md" in ln]
            half = bool(line) and "不会自动搬过来" in line[0] and "模型不代填" in line[0]
            check(f"{label}：rc 0、(补建句出现, 带半句)", (r.returncode, bool(line), half), (0,) + want)


def test_render_self_check():
    print("render_hook_stdout 自检:")
    text = "[workframe] 已补建 .claude/skills\n第二行\n"
    good = H.render_hook_stdout(text, "SessionStart", H.OUTPUT_TEXT, H.CC, True)
    check("good: JSON 且带 reloadSkills", (json.loads(good)["hookSpecificOutput"]["reloadSkills"], good.endswith("\n")),
          (True, True))
    bad = H.render_hook_stdout(text, "SessionStart", H.OUTPUT_TEXT, H.CC, True, dumps=lambda o: '{"hookSpecificOutput": ')
    check("dumps 产出坏 JSON ⇒ 退回纯文本", bad, text)
    wrong = H.render_hook_stdout(text, "SessionStart", H.OUTPUT_TEXT, H.CC, True,
                                 dumps=lambda o: json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart"}}))
    check("解析得了但结构不对 ⇒ 退回纯文本", wrong, text)
    check("没请求重扫 ⇒ 原样", H.render_hook_stdout(text, "SessionStart", H.OUTPUT_TEXT, H.CC, False), text)
    check("门不是 cc ⇒ 原样", H.render_hook_stdout(text, "SessionStart", H.OUTPUT_TEXT, H.UNKNOWN, True), text)
    check("空缓冲 ⇒ 不写", H.render_hook_stdout("", "SessionStart", H.OUTPUT_TEXT, H.CC, False), None)
    cj = json.loads(H.render_hook_stdout(text, "SessionStart", H.OUTPUT_HOOK_JSON, H.CODEX, True))
    check("hook-json 模式即便请求了也不带 reloadSkills", sorted(cj["hookSpecificOutput"]), ["additionalContext", "hookEventName"])


# ---------------------------------------------------------------- `.claude/skills` 的七种形态

def mk_elsewhere(tmp, name="elsewhere"):
    d = tmp / name / "skills" / "other"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("# other\n", encoding="utf-8", newline="")
    return tmp / name / "skills"


def tree_bytes(d):
    d = Path(d)
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()} if d.is_dir() else None


def doctor_rows(root):
    return doc.check_skills_link({"paths": doc.Paths(root)})


def levels(rows):
    return sorted({lv for lv, _ in rows})


def build_f4(root):
    """F4：`.claude/skills` 是链接、目标不存在、本项目 `.agents/skills` 在。链接先指向 fixture 自建的旧真实源，
    再把旧真实源**改名**挪走（不删）——与「项目整体改名后 junction 仍指旧位置」同形。返回挪走后的旧目标路径。"""
    mk_neutral(root)
    old_src = root.parent / "old-home" / "skills"
    (old_src / "legacy").mkdir(parents=True)
    (old_src / "legacy" / "SKILL.md").write_text("# legacy\n", encoding="utf-8", newline="")
    (root / ".claude").mkdir(exist_ok=True)
    ps._make_dir_link(old_src, root / CC)
    moved = root.parent / "old-home-moved"
    os.rename(root.parent / "old-home", moved)
    return moved / "skills"


def test_forms():
    print("forms（七种形态 × 判定 / SessionStart / doctor）:")
    # absent / link_ok / realdir（正常形态）
    with Proj() as root:
        check("absent: form", H.skills_cc_form(root), H.SKILLS_ABSENT)
        check("absent: doctor ok", levels(doctor_rows(root)), ["ok"])
        ps.ensure_project_skills_dir(root, [], [])
        check("link_ok: form", H.skills_cc_form(root), H.SKILLS_LINK_OK)
        check("link_ok: doctor ok", levels(doctor_rows(root)), ["ok"])
    with Proj() as root:
        mk_realdir(root)
        check("realdir（无中立目录）: form", H.skills_cc_form(root), H.SKILLS_REALDIR)
        check("realdir（无中立目录）: doctor ok、conflict None", (levels(doctor_rows(root)), H.project_skills_conflict(root)),
              (["ok"], None))
    # F1 真目录副本 / F2 空真目录（本项目 `.agents/skills` 也在）
    for label, fill in (("F1 副本", True), ("F2 空壳", False)):
        with Proj() as root:
            mk_neutral(root)
            (root / CC).mkdir(parents=True)
            if fill:
                (root / CC / "prd-style").mkdir()
                (root / CC / "prd-style" / "SKILL.md").write_text("# copy, edited\n", encoding="utf-8", newline="")
            before = tree_bytes(root / CC)
            _fixture(root)
            r = run_prep(root)
            rows = doctor_rows(root)
            check(f"{label}: form realdir", H.skills_cc_form(root), H.SKILLS_REALDIR)
            check(f"{label}: SessionStart 一行、不动、不是 JSON",
                  ("是一个真目录" in r.stdout, tree_bytes(root / CC) == before, as_json(r.stdout) is None), (True, True, True))
            check(f"{label}: doctor warn、先比对、不给删目录命令",
                  (levels(rows), "git diff --no-index" in rows[0][1], "rmdir" in rows[0][1] or "rm -r" in rows[0][1]),
                  (["warn"], True, False))
            check(f"{label}: 文案方向（CC 读 .claude/skills，Codex 读 .agents/skills）",
                  "Claude Code 读 .claude/skills，框架脚本与 Codex 读 .agents/skills" in rows[0][1], True)
    # F3 链接指向别处（目标存在）
    with Proj() as root:
        mk_neutral(root)
        other = mk_elsewhere(root.parent)
        (root / ".claude").mkdir()
        ps._make_dir_link(other, root / CC)
        _fixture(root)
        before = tree_bytes(other)
        r = run_prep(root)
        rows = doctor_rows(root)
        check("F3: form link_elsewhere", H.skills_cc_form(root), H.SKILLS_LINK_ELSEWHERE)
        check("F3: SessionStart 一行、链接不动、目标不动",
              ("指向别处的链接" in r.stdout, H.skills_cc_form(root), tree_bytes(other) == before),
              (True, H.SKILLS_LINK_ELSEWHERE, True))
        check("F3: doctor warn", levels(rows), ["warn"])
    # F5 普通文件
    with Proj() as root:
        mk_neutral(root)
        (root / ".claude").mkdir()
        (root / CC).write_bytes(b"../.agents/skills")      # POSIX 链接对象在 core.symlinks=false 下 checkout 的样子
        check("F5: form file", H.skills_cc_form(root), H.SKILLS_FILE)
        rows = doctor_rows(root)
        check("F5: doctor error", levels(rows), ["error"])
        _fixture(root)
        r = run_prep(root)
        check("F5: SessionStart 一行、文件原样", ("是一个普通文件" in r.stdout, (root / CC).read_bytes()),
              (True, b"../.agents/skills"))
        rc = run_prep(root, harness="codex", extra=("--output", "hook-json"))
        ctx = ((as_json(rc.stdout) or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("F5: Codex 门那一行写明本门不受影响", ("是一个普通文件" in ctx, "Codex 门直接读 .agents/skills，不受影响" in ctx),
              (True, True))
    # link_neutral_missing：链接在、真实源不在（fixture 自己的真实源改名挪走，不删）
    with Proj() as root:
        ps.ensure_project_skills_dir(root, [], [])
        os.rename(root / NEUTRAL, root / ".agents" / "skills-moved")
        check("neutral_missing: form", H.skills_cc_form(root), H.SKILLS_LINK_NEUTRAL_MISSING)
        check("neutral_missing: dangling helper True", H.skills_link_dangling(root), True)
        _fixture(root)
        r = run_prep(root)
        check("neutral_missing: SessionStart 一行、不建不删",
              ("真实源没了" in r.stdout, H.skills_cc_form(root)), (True, H.SKILLS_LINK_NEUTRAL_MISSING))
        check("neutral_missing: doctor warn", levels(doctor_rows(root)), ["warn"])
    # F4 目标不存在：doctor 在会话之外 error；SessionStart 重指、两边内容逐文件不变、这次 stdout 是 JSON
    with Proj() as root:
        old = build_f4(root)
        check("F4: form link_target_missing", H.skills_cc_form(root), H.SKILLS_LINK_TARGET_MISSING)
        check("F4: 不是 dangling（那是真实源不在的另一态）", H.skills_link_dangling(root), False)
        rows = doctor_rows(root)
        check("F4: doctor error、说开一次会话会自动重指", (levels(rows), "自动把它重指" in rows[0][1]), (["error"], True))
        neu_before, old_before = tree_bytes(root / NEUTRAL), tree_bytes(old)
        _fixture(root)
        r = run_prep(root)
        obj = as_json(r.stdout)
        ctx = ((obj or {}).get("hookSpecificOutput") or {}).get("additionalContext", "")
        check("F4: 重指后 link_ok", H.skills_cc_form(root), H.SKILLS_LINK_OK)
        check("F4: 本项目 .agents/skills 与原目标逐文件不变",
              (tree_bytes(root / NEUTRAL) == neu_before, tree_bytes(old) == old_before), (True, True))
        check("F4: 这一次 stdout 是 JSON、reloadSkills、说了原指向",
              (obj is not None, (obj or {}).get("hookSpecificOutput", {}).get("reloadSkills"),
               "已把悬空的 .claude/skills 链接" in ctx, "old-home" in ctx), (True, True, True, True))
        check("F4: 经链接读得到本项目 skill", (root / CC / "prd-style" / "SKILL.md").read_text(encoding="utf-8"), "# probe\n")
        r2 = run_prep(root)
        check("F4: 下一次会话安静", ("悬空" in r2.stdout, as_json(r2.stdout) is None), (False, True))
    # 项目整体搬家：POSIX 相对符号链接随项目走、仍 link_ok；Windows junction 存绝对路径 ⇒ F4、会话重指
    with Proj() as root:
        mk_neutral(root)
        ps.ensure_project_skills_dir(root, [], [])
        moved = root.parent / "proj-moved"
        os.rename(root, moved)
        want = H.SKILLS_LINK_TARGET_MISSING if os.name == "nt" else H.SKILLS_LINK_OK
        check(f"整体搬家后 form == {want}", H.skills_cc_form(moved), want)
        _fixture(moved)
        run_prep(moved)
        check("整体搬家后开一次会话 ⇒ link_ok、经链接读得到", (H.skills_cc_form(moved),
              (moved / CC / "prd-style" / "SKILL.md").is_file()), (H.SKILLS_LINK_OK, True))
    # fail-safe 的直接证据：解链接原语对非空真目录必然失败、内容不变
    with Proj() as root:
        mk_realdir(root)
        before = tree_bytes(root / CC)
        try:
            ps._unlink_dir_link(root / CC)
            raised = None
        except OSError as e:
            raised = type(e).__name__
        check("unlink 原语对非空真目录失败", raised is not None, True)
        check("… 内容不变", tree_bytes(root / CC) == before, True)
        check("repoint 对真目录不动手", ps.repoint_dangling_skills_link(root, [], []), None)
    # Windows：8.3 短名路径下，指向本项目的 junction 仍判 link_ok（字符串比较会判成 link_elsewhere）
    if os.name == "nt":
        with Proj() as root:
            ps.ensure_project_skills_dir(root, [], [])
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            n = ctypes.windll.kernel32.GetShortPathNameW(str(root), buf, 1024)
            short = Path(buf.value) if n else root
            raw = os.readlink(root / CC)
            print(f"  (short={short!s}; readlink={raw!s})")
            check("short-name root: link_ok", H.skills_cc_form(short), H.SKILLS_LINK_OK)
            check("readlink 带 \\\\?\\ 前缀（字符串比较必然判错的前提）", raw.startswith("\\\\?\\"), True)


def test_reverse_link():
    """反向链接：`.claude/skills` 是真目录、`.agents/skills` 是指向它的链接——同一份，不是副本。
    判成副本的话，每会话提示与 doctor 都会让人「合并后把 .claude/skills 换成链接」，照做即删掉唯一的真目录。"""
    print("reverse link（.agents/skills → .claude/skills 真目录）:")
    with Proj() as root:
        mk_realdir(root)
        (root / ".agents").mkdir()
        ps._make_dir_link(root / CC, root / NEUTRAL)
        before = tree_bytes(root / CC)
        check("form realdir", H.skills_cc_form(root), H.SKILLS_REALDIR)
        check("两份判定 None、conflict None", (H.skills_two_copies(root), H.project_skills_conflict(root)), (None, None))
        _fixture(root)
        r = run_prep(root)
        check("SessionStart 不打副本那一行、不是 JSON",
              ("不是同一个目录" in r.stdout, "真目录" in r.stdout, as_json(r.stdout) is None), (False, False, True))
        rows = doctor_rows(root)
        check("doctor ok、文案里没有字面 None", (levels(rows), any("None" in m for _, m in rows)), (["ok"], False))
        check("scaffold 步不动、不报冲突", (ps.repoint_dangling_skills_link(root, [], []), tree_bytes(root / CC) == before,
              H.is_dir_link(root / NEUTRAL)), (None, True, True))


def test_recheck_before_unlink():
    """解链接前「紧贴动作的重判」确定性一格：让形态判定只在第一次说谎（返回目标不存在），解链接 / 建链接原语换成
    只记录调用。现实现在真目录（空 / 非空）、普通文件、指向别处四种形态下调用 0 次；去掉重判的变体会调用。"""
    print("recheck before unlink（判定首次说谎 × 原语记录器）:")
    real_form = ps.skills_cc_form
    real_unlink, real_make = ps._unlink_dir_link, ps._make_dir_link

    def build_empty(root):
        (root / CC).mkdir(parents=True)

    def build_full(root):
        mk_realdir(root)

    def build_file(root):
        (root / ".claude").mkdir()
        (root / CC).write_text("x", encoding="utf-8", newline="")

    def build_elsewhere(root):
        (root / ".claude").mkdir()
        real_make(mk_elsewhere(root.parent), root / CC)

    for label, build in (("空真目录", build_empty), ("非空真目录", build_full), ("普通文件", build_file),
                         ("指向别处", build_elsewhere)):
        with Proj() as root:
            mk_neutral(root)
            build(root)
            want_form = real_form(root)
            calls, told = [], {"n": 0}

            def liar(p, _told=told):
                _told["n"] += 1
                return H.SKILLS_LINK_TARGET_MISSING if _told["n"] == 1 else real_form(p)
            try:
                ps.skills_cc_form = liar
                ps._unlink_dir_link = lambda p: calls.append(("unlink", str(p)))
                ps._make_dir_link = lambda t, p: calls.append(("make", str(p)))
                got = ps.repoint_dangling_skills_link(root, [], [])
            finally:
                ps.skills_cc_form, ps._unlink_dir_link, ps._make_dir_link = real_form, real_unlink, real_make
            check(f"{label}: 判定确实说过谎、原语调用 0 次、返回 None、形态不变",
                  (told["n"] >= 2, calls, got, real_form(root)), (True, [], None, want_form))


def test_repoint_race_simulated():
    """并发重指的确定性替身（两平台同一套，macOS 真机上的时序靠它钉住）：把原语换成「第一次调用时对方恰好刚做完」——
    ①解链接：对方已解掉并建好新链接，本方撞 `FileNotFoundError`；②建链接：对方已建好，本方撞 `FileExistsError`；
    ③判定：对方恰在读形态时解掉链接，本方瞬时读到「普通文件」/「指向别处」一次。三格都断言：不抛异常、返回原指向、
    不报失败、结果 `link_ok`。原语以外一律走真实实现。"""
    print("repoint race（原语替身：对方刚做完）:")
    real_unlink, real_make, real_form = ps._unlink_dir_link, ps._make_dir_link, ps.skills_cc_form

    def run_case(label, patch):
        with Proj() as root:
            build_f4(root)
            neutral, cc = root / NEUTRAL, root / CC
            state = {"n": 0}
            patch(state, neutral, cc)
            created, skipped = [], []
            try:
                got, err = ps.repoint_dangling_skills_link(root, created, skipped), ""
            except Exception as e:      # 替身模拟的是竞态；被测方把它抛出来就是子进程里的 traceback
                got, err = None, f"{type(e).__name__}: {e}"
            finally:
                ps._unlink_dir_link, ps._make_dir_link, ps.skills_cc_form = real_unlink, real_make, real_form
            check(f"{label}: 替身确实被调用、不抛异常、返回原指向、不报失败、link_ok",
                  (state["n"] >= 1, err, bool(got), skipped, H.skills_cc_form(root)),
                  (True, "", True, [], H.SKILLS_LINK_OK))

    def unlink_race(state, neutral, cc):
        def fake(p):
            state["n"] += 1
            if state["n"] == 1:
                real_unlink(p)
                real_make(neutral, p)       # 对方解掉并建好了
                raise FileNotFoundError(2, "simulated: peer already unlinked", str(p))
            real_unlink(p)
        ps._unlink_dir_link = fake

    def make_race(state, neutral, cc):
        def fake(t, p):
            state["n"] += 1
            if state["n"] == 1:
                real_make(t, p)             # 对方抢先建好了
                raise FileExistsError(17, "simulated: peer already linked", str(p))
            real_make(t, p)
        ps._make_dir_link = fake

    def judge_flicker(flicker):
        def patch(state, neutral, cc):
            calls = {"k": 0}

            def fake(p):
                calls["k"] += 1
                if calls["k"] == 2:         # 紧贴动作的那次重判恰好撞上对方解链接
                    state["n"] += 1
                    return flicker
                return real_form(p)
            ps.skills_cc_form = fake
        return patch

    run_case("解链接撞 FileNotFoundError", unlink_race)
    run_case("建链接撞 FileExistsError", make_race)
    run_case("重判瞬时读成普通文件", judge_flicker(H.SKILLS_FILE))
    run_case("重判瞬时读成指向别处", judge_flicker(H.SKILLS_LINK_ELSEWHERE))


def test_concurrent_repoint():
    print("concurrent repoint（两个进程同时重指同一悬空链接）:")
    code = ("import sys, time; sys.path.insert(0, {s!r}); import project_scaffold as ps\n"
            "from pathlib import Path\n"
            "t = float(sys.argv[2])\n"
            "while time.time() < t: pass\n"
            "c, s = [], []\n"
            "old = ps.repoint_dangling_skills_link(Path(sys.argv[1]), c, s)\n"
            "print('OLD' if old else 'NONE', '|'.join(s))\n").format(s=str(SCRIPTS))
    import time
    for rnd in range(3):
        with Proj() as root:
            build_f4(root)
            go = time.time() + 1.5
            procs = [subprocess.Popen([sys.executable, "-c", code, str(root), str(go)], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
                     for _ in range(2)]
            outs = [p.communicate(timeout=60) for p in procs]
            rcs = [p.returncode for p in procs]
            # 失败时把每个子进程的诊断带进读数：CI 只跑 validate，而 validate 转述套件失败时优先取全部 `[FAIL]` 行
            # （`validate._suite_failure_summary`），诊断同时进失败标签与 got，两处都在那一行里
            # 每个子进程一串：**异常类型与消息那一行在最前**（traceback 末尾那行不缩进的），再附 rc 与 stderr 末 3 行——
            # 被上游截断时最先保住的是最关键的那句
            def _diag(rc, e):
                if not (rc or "Traceback" in e):
                    return ""
                lines = [ln for ln in e.strip().splitlines() if ln.strip()]
                exc = next((ln for ln in reversed(lines) if not ln[:1].isspace()), "")
                tail = " | ".join(ln.strip()[:160] for ln in lines[-3:])
                return f"{exc[:200]} ‖ rc={rc} ‖ {tail}"
            diag = [_diag(rc, e) for rc, (_, e) in zip(rcs, outs)]
            want = ["", ""]
            check(f"round {rnd}: 两边 rc 0、无 traceback" + ("" if diag == want else f" {diag}"), diag, want)
            check(f"round {rnd}: 两边都没报失败", [o.strip().split(" ", 1)[1:] for o, _ in outs], [[], []])
            check(f"round {rnd}: 结果 link_ok", H.skills_cc_form(root), H.SKILLS_LINK_OK)


# ---------------------------------------------------------------- 忽略写法

def git_ok():
    try:
        return subprocess.run(["git", "--version"], capture_output=True).returncode == 0
    except OSError:
        return False


def gitc(root, *args):
    r = subprocess.run(["git", "-c", "core.autocrlf=false", *args], cwd=str(root), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout


def test_ignore_literals():
    print("ignore literals（四个产出点，整行相等、平台无关）:")
    req = ps.gitignore_required_entries_for(".workframe/state", True)
    check("① 必需条目含整行 .claude/skills、无别的 skills 写法",
          [e for e in req if e.startswith(".claude/skills") or e.startswith("/.claude/skills")], [LINK_LINE])
    ml = ps.managed_lines_for(".workframe/state", True)
    check("② managed 写入行含整行 .claude/skills、无别的 skills 写法",
          [e for e in ml if e.startswith(".claude/skills") or e.startswith("/.claude/skills")], [LINK_LINE])
    tpl = (PLUGIN / "templates" / "gitignore-template").read_text(encoding="utf-8")
    rules = [ln.strip() for ln in tpl.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    check("③ 模板规则行含整行 .claude/skills、无别的 skills 写法",
          [ln for ln in rules if ln.lstrip("/").startswith(".claude/skills")], [LINK_LINE])
    with Proj() as root:
        ps.ensure_project_skills_dir(root, [], [])
        dreq = doc.gitignore_required(doc.Paths(root))
        check("④ doctor 必需条目含整行 .claude/skills、无别的 skills 写法",
              [e for e in dreq if e.lstrip("/").startswith(".claude/skills")], [LINK_LINE])
    check("realdir 形态必需条目 / 写入行无任何 skills 条目",
          ([e for e in ps.gitignore_required_entries_for(".workframe/state", False) if "skills" in e],
           [e for e in ps.managed_lines_for(".workframe/state", False) if "skills" in e]), ([], []))


def test_ignore_state_and_upgrade():
    print("ignore state / upgrade:")
    S = ps.skills_link_ignore_state
    cases = [(".claude/skills\n", "ok"), ("/.claude/skills\n", "ok"), ("  .claude/skills  \n", "ok"),
             (".claude/skills/\n", "legacy"), ("/.claude/skills/\n", "legacy"), (".claude/skills/*\n", "legacy"),
             ("/.claude/skills/*\n", "legacy"), (".claude/skills/**\n", "legacy"),
             ("# .claude/skills\n", "missing"), ("!.claude/skills\n", "missing"), (".claude/\n", "missing"),
             (".claude/*\n", "missing"), ("", "missing"), ("﻿.claude/skills\n", "ok"),
             (".claude/skills/\n.claude/skills\n", "ok")]
    check("skills_link_ignore_state 各写法", [S(t) for t, _ in cases], [w for _, w in cases])
    check("gitignore_covers：旧写法不算覆盖（带不带尾斜杠的 entry 同判）",
          (ps.gitignore_covers(".claude/skills/\n", LINK_LINE), ps.gitignore_covers(".claude/skills/\n", LINK_LINE + "/")),
          (False, False))
    check("gitignore_covers：其余条目语义不动（tmp/* 仍算 tmp/）", ps.gitignore_covers("tmp/*\n", "tmp/"), True)
    B, E = ps.GITIGNORE_MANAGED_BEGIN, ps.GITIGNORE_MANAGED_END
    crlf = ("node_modules/\r\n.claude/skills/\r\n" + B + "\r\n.claude/settings.local.json\r\n.claude/skills/\r\n"
            + E + "\r\n").encode("utf-8")
    bom_crlf = b"\xef\xbb\xbf" + crlf
    up = ps.upgrade_skills_ignore_bytes(bom_crlf)
    want = b"\xef\xbb\xbf" + ("node_modules/\r\n.claude/skills/\r\n" + B + "\r\n.claude/settings.local.json\r\n"
                              ".claude/skills\r\n" + E + "\r\n").encode("utf-8")
    check("upgrade：块内旧写法 → 新写法、块外不动、BOM ＋ CRLF 保留", up, want)
    check("upgrade：幂等（第二次返回 None）", ps.upgrade_skills_ignore_bytes(up), None)
    check("upgrade：块内没有旧写法 ⇒ None", ps.upgrade_skills_ignore_bytes(b".claude/skills/\n"), None)
    lf = ("x\n" + B + "\n/.claude/skills/*\n" + E + "\nlast").encode("utf-8")
    check("upgrade：LF、末行无换行也照原样", ps.upgrade_skills_ignore_bytes(lf),
          ("x\n" + B + "\n.claude/skills\n" + E + "\nlast").encode("utf-8"))


def test_ensure_gitignore_paths():
    print("_ensure_gitignore（链接形态升级 / 块外旧写法 / 真目录无 .gitignore）:")
    B, E = ps.GITIGNORE_MANAGED_BEGIN, ps.GITIGNORE_MANAGED_END
    # managed block 内旧写法（10a 迁移工具写出的形态）⇒ 升级；doctor 转 ok；再跑零改动
    with Proj() as root:
        ps.ensure_project_skills_dir(root, [], [])
        managed = ps.managed_lines_for(ps.state_dir_rel(root), True)
        old_lines = [ln if ln != LINK_LINE else LINK_LINE + "/" for ln in managed]
        raw = ("\r\n".join(["", B, *old_lines, E, ""])).encode("utf-8")[2:]
        (root / ".gitignore").write_bytes(raw)
        rows = doc.check_gitignore({"paths": doc.Paths(root)})
        check("旧写法：doctor warn 且点明只在 Windows junction 上生效",
              (levels(rows), any("只在 Windows junction 上生效" in m for _, m in rows)), (["warn"], True))
        check("… 文案给出升级命令与手改一行", any("workframe-door --migrate" in m and "去掉尾斜杠" in m for _, m in rows), True)
        c, s = [], []
        ps._ensure_gitignore(root, c, s)
        after = (root / ".gitignore").read_bytes()
        check("升级后块内是新写法、CRLF 行数不变",
              (LINK_LINE in after.decode("utf-8").split("\r\n"), after.count(b"\r\n"), after.count(b"\n")),
              (True, raw.count(b"\r\n"), raw.count(b"\n")))
        check("升级后 doctor ok", levels(doc.check_gitignore({"paths": doc.Paths(root)})), ["ok"])
        ps._ensure_gitignore(root, [], [])
        check("再跑零改动", (root / ".gitignore").read_bytes(), after)
    # marker 之外的旧写法、没有 marker ⇒ 旧行不动、追加一个 managed block（带新写法）
    with Proj() as root:
        ps.ensure_project_skills_dir(root, [], [])
        state = ps.state_dir_rel(root)
        user = f".claude/settings.local.json\n{state}/*\n!{state}/memory-index.json\nlogs/\ntmp/\n.tmp/\n.claude/skills/\n"
        (root / ".gitignore").write_text(user, encoding="utf-8", newline="")
        ps._ensure_gitignore(root, [], [])
        t = (root / ".gitignore").read_text(encoding="utf-8")
        check("块外旧写法：原行在、追加了一个 managed block、块里是新写法",
              (t.startswith(user), t.count(B), ps.skills_link_ignore_state(t)), (True, 1, "ok"))
        ps._ensure_gitignore(root, [], [])
        check("… 再跑不再追加第二个 block", (root / ".gitignore").read_text(encoding="utf-8").count(B), 1)
    # 真目录 ＋ 无 .gitignore：模板那一行按整行剥掉、项目 skill 不被忽略
    with Proj() as root:
        mk_realdir(root)
        ps._ensure_gitignore(root, [], [])
        t = (root / ".gitignore").read_text(encoding="utf-8")
        check("realdir：模板里的 skills 忽略行被剥掉（注释段保留）",
              ([ln for ln in t.splitlines() if ln.strip() and not ln.strip().startswith("#") and "skills" in ln],
               "# 项目 skills 的真实源" in t), ([], True))
        if git_ok():
            gitc(root, "init", "-q")
            rc, _ = gitc(root, "check-ignore", "-q", "--no-index", ".claude/skills/prd-style/SKILL.md")
            check("realdir：check-ignore 项目 skill 文件 rc 1（不被忽略）", rc, 1)
    # 链接形态 ＋ git：新写法下链接本身被忽略、ls-files -o 不出现它；旧写法在 POSIX 上盖不住链接（Windows 恒过）
    if git_ok():
        for form_line, want_ignored in ((LINK_LINE, True), (LINK_LINE + "/", os.name == "nt")):
            with Proj() as root:
                ps.ensure_project_skills_dir(root, [], [])
                (root / ".gitignore").write_text(form_line + "\n", encoding="utf-8", newline="")
                gitc(root, "init", "-q")
                rc, _ = gitc(root, "check-ignore", "-q", "--no-index", "--", LINK_LINE)
                _, others = gitc(root, "ls-files", "-o", "--exclude-standard", "--", ".claude")
                leaked = [ln for ln in others.splitlines() if ln.startswith(LINK_LINE)]
                tag = "（Windows 恒过，POSIX 才分得开）" if form_line.endswith("/") else ""
                check(f"`{form_line}`：链接本身被忽略 == {want_ignored}{tag}", (rc == 0, not leaked),
                      (want_ignored, want_ignored))


def test_protected_paths():
    print("doctor protected_assets（链接形态下 git 报 .agents/skills/…）:")
    if not git_ok():
        print("  [skip] 没有 git")
        return
    with Proj() as root:
        gitc(root, "init", "-q")
        gitc(root, "config", "user.email", "t@example.invalid")
        gitc(root, "config", "user.name", "t")
        (root / "README.md").write_text("r\n", encoding="utf-8", newline="")
        gitc(root, "add", "-A")
        gitc(root, "commit", "-q", "-m", "root")                     # 根提交被排除，第二个提交才是被测改动
        ps.ensure_project_skills_dir(root, [], [])
        (root / NEUTRAL / "x").mkdir()
        (root / NEUTRAL / "x" / "SKILL.md").write_text("# x\n", encoding="utf-8", newline="")
        (root / ".gitignore").write_text(LINK_LINE + "\n", encoding="utf-8", newline="")
        gitc(root, "add", "-A")
        gitc(root, "commit", "-q", "-m", "edit project skill")      # 当下时间，落在 14 天窗口内
        _, names = gitc(root, "log", "--name-only", "--pretty=format:", "-1")
        check("前提：第二个提交里是 .agents/skills/x/SKILL.md、没有 .claude/skills 路径",
              (".agents/skills/x/SKILL.md" in names.split(), any(n.startswith(".claude/skills") for n in names.split())),
              (True, False))
        rows = doc.check_protected_assets({"paths": doc.Paths(root)})
        check("changed 清单计入这个文件（ok 行报 1 个）", [m for lv, m in rows if lv == "ok"],
              [f"近 {doc.PROTECTED_WINDOW_DAYS} 天受保护资产提交 1 个文件"])
        check("… 未见审批痕迹那行列的正是它", [m.split(": ", 1)[-1] for lv, m in rows if lv == "info"],
              [".agents/skills/x/SKILL.md"])
        saved = list(doc.PROTECTED_PATHS)
        try:
            doc.PROTECTED_PATHS[:] = [p for p in saved if p != ".agents/skills"]
            rows2 = doc.check_protected_assets({"paths": doc.Paths(root)})
        finally:
            doc.PROTECTED_PATHS[:] = saved
        check("对照：去掉 .agents/skills 前缀 ⇒ 看不见这次改动", [m for _, m in rows2],
              [f"近 {doc.PROTECTED_WINDOW_DAYS} 天受保护资产无提交"])


def main():
    test_fresh()
    test_dangling()
    test_clone()
    test_realdir()
    test_both_real_and_file()
    test_session_start()
    test_agents_md_backfill_note()
    test_render_self_check()
    test_forms()
    test_reverse_link()
    test_recheck_before_unlink()
    test_repoint_race_simulated()
    test_concurrent_repoint()
    test_ignore_literals()
    test_ignore_state_and_upgrade()
    test_ensure_gitignore_paths()
    test_protected_paths()
    if FAILURES:
        print(f"\n{len(FAILURES)} 条失败: {FAILURES}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
