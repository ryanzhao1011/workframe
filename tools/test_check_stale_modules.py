#!/usr/bin/env python3
"""
单元测试 — plugins/core/scripts/check-stale-modules.py 关键函数 + 回归。

覆盖：
  - glob_to_regex：glob → regex 转换正确性
  - first_static_segment：bucket key 提取
  - parse_code_paths：submodule.yaml code_paths 解析
  - lookup_submodules：反向 lookup 命中正确子模块
  - 索引新鲜度：显式传入索引不重建 / 入口按声明重建再判新鲜 / project_dir 落点 / 两进程并发只保存一次 /
    声明读不了时 CLI exit 1、缓存不覆盖、hook 不报段已重建（对账矩阵在 test_module_close_check）
  - main 子命令分派回归（防 v0.3.x M3 fix-1 倒退）：
    * scan-git-diff 子命令在 stdin 含 SessionStart JSON 时仍能跑
    * 无 argv 时走 PostToolUse 路径
  - discover_nested_repos：嵌套 git 仓发现（深层仓 / 嵌套套嵌套全收集 / .git 文件形态 /
    绝对路径·盘符·../ 开头 pattern 拒绝 / 项目根不误报）
  - scan-git-diff 端到端矩阵：根仓 tracked/staged/untracked + 嵌套仓 tracked/staged/
    untracked 同扫 + ignored 不误报 + 干净仓不误报 + 坏嵌套仓留痕且不影响其余仓
  - 锁超时故障注入：入口索引保障超时 / mark_stale 超时均留事件且字段完整、hook 不崩
  - 运行态状态目录的落点：新项目 / 只有旧目录（没迁移）/ 迁完的项目一律落新目录；前后两种断言另一侧目录
    **没被建出来**，只有旧目录那种断言旧目录的文件集与字节**原样不动**

执行：
  python tools/test_check_stale_modules.py
退出 0 = 全部通过；非 0 = 有失败用例。
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# 本文件**不新建 TextIOWrapper**（在 `check_utf8_stream_wrap_symmetric` 的豁免白名单里），
# 只在 `__main__` 里对两条流原地 reconfigure：被加载的 check-stale-modules.py import 时
# 不再改宿主的流，不自己设的话 ✓ 在中文 Windows 的管道输出上当场 UnicodeEncodeError。
# 别改成 TextIOWrapper 替换——两个 wrapper 抢同一个 buffer，先被回收的会把它关掉。

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "plugins" / "core" / "scripts" / "check-stale-modules.py"

# 运行态目录名问 `_state_io`，测试里**不抄第二份字面**——抄了就是又一个事实源，
# 而这些用例正是用来证明脚本按单源解析路径的。
sys.path.insert(0, str(SCRIPT_PATH.parent))
from _state_io import legacy_rel, new_rel, state_dir_of  # noqa: E402

STATE_NEW = new_rel("state")
STATE_LEGACY = legacy_rel("state")


def _load_module():
    spec = importlib.util.spec_from_file_location("check_stale_modules", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 单元测试 ----------


def test_glob_to_regex(mod):
    cases = [
        ("miniprogram/pages/profile/edit/**", "miniprogram/pages/profile/edit/index.js", True),
        ("miniprogram/pages/profile/edit/**", "miniprogram/pages/profile/edit/sub/foo.js", True),
        ("miniprogram/pages/profile/edit/**", "miniprogram/pages/payment/index.js", False),
        ("cloudfunctions/profile/**", "cloudfunctions/profile/get/index.js", True),
        ("cloudfunctions/profile/**", "cloudfunctions/auth/get/index.js", False),
        ("miniprogram/services/profile.js", "miniprogram/services/profile.js", True),
        ("miniprogram/services/profile.js", "miniprogram/services/auth.js", False),
        ("apps/web/app/**/*.tsx", "apps/web/app/profile/page.tsx", True),
        ("apps/web/app/**/*.tsx", "apps/web/app/profile/sub/edit.tsx", True),
        ("apps/web/app/**/*.tsx", "apps/web/app/profile/page.ts", False),
    ]
    failures = []
    for pat, path, expected in cases:
        actual = bool(re.match(mod.glob_to_regex(pat), path))
        if actual != expected:
            failures.append(f"glob_to_regex({pat!r}, {path!r}) = {actual}, want {expected}")
    return failures


def test_first_static_segment(mod):
    cases = [
        ("miniprogram/pages/**", "miniprogram"),
        ("apps/web/**", "apps"),
        ("*.js", ""),
        ("cloudfunctions/profile/get/**", "cloudfunctions"),
        ("**/auth/**", ""),
    ]
    failures = []
    for pat, expected in cases:
        actual = mod.first_static_segment(pat)
        if actual != expected:
            failures.append(f"first_static_segment({pat!r}) = {actual!r}, want {expected!r}")
    return failures


def test_parse_code_paths(mod, tmpdir):
    sub_yaml = Path(tmpdir) / "submodule.yaml"
    sub_yaml.write_text(
        "parent_module: profile\n"
        "name: edit\n"
        "code_paths:\n"
        "  - miniprogram/pages/profile/edit/**\n"
        "  - cloudfunctions/profile/**       # 行尾注释\n"
        '  - "miniprogram/services/profile.js"\n'
        "  - \n"  # 空项
        "api_dependencies: []\n",
        encoding="utf-8", newline="",
    )
    paths = mod.parse_code_paths(sub_yaml)
    expected = [
        "miniprogram/pages/profile/edit/**",
        "cloudfunctions/profile/**",
        "miniprogram/services/profile.js",
    ]
    if paths != expected:
        return [f"parse_code_paths got {paths!r}, want {expected!r}"]
    return []


def test_lookup_submodules_with_dummy_project(mod, tmpdir):
    """跑一个 dummy modules 项目，跑 rebuild_full_index → 验证 lookup 命中正确。"""
    project = Path(tmpdir)
    (project / "projects" / "modules" / "profile" / "edit").mkdir(parents=True)
    (project / "projects" / "modules" / "profile" / "edit" / "submodule.yaml").write_text(
        "parent_module: profile\n"
        "name: edit\n"
        "code_paths:\n"
        "  - miniprogram/pages/profile/edit/**\n"
        "  - cloudfunctions/profile/**\n",
        encoding="utf-8", newline="",
    )
    (project / "projects" / "modules" / "payment" / "checkout").mkdir(parents=True)
    (project / "projects" / "modules" / "payment" / "checkout" / "submodule.yaml").write_text(
        "parent_module: payment\n"
        "name: checkout\n"
        "code_paths:\n"
        "  - miniprogram/pages/payment/**\n",
        encoding="utf-8", newline="",
    )
    # 重定向脚本的 PROJECT_DIR
    mod.PROJECT_DIR = project.resolve()
    mod.STATE_DIR = state_dir_of(project)
    mod.INDEX_FILE = mod.STATE_DIR / "code-paths-index.json"
    mod.STALE_FILE = mod.STATE_DIR / "stale-modules.yaml"
    mod.LOCK_FILE = mod.STATE_DIR / "modules-index.lock"
    mod.MODULES_DIR = project / "projects" / "modules"
    mod.EVENTS_FILE = mod.STATE_DIR / "events.jsonl"

    mod.rebuild_full_index()
    failures = []
    cases = [
        ("miniprogram/pages/profile/edit/index.js", ["profile/edit"]),
        ("miniprogram/pages/payment/checkout.js", ["payment/checkout"]),
        ("cloudfunctions/profile/get/index.js", ["profile/edit"]),
        ("miniprogram/pages/random/foo.js", []),
        ("unrelated/path/file.js", []),
    ]
    for path, expected in cases:
        actual = mod.lookup_submodules(path)
        if actual != expected:
            failures.append(f"lookup_submodules({path!r}) = {actual!r}, want {expected!r}")
    return failures


# ---------- 子命令分派回归（防 fix-1 倒退） ----------


def test_main_dispatch_argv_priority(tmpdir):
    """关键回归：argv 子命令 + stdin JSON 同时存在时，必须走 argv（hook 真实场景）。

    SessionStart hook 命令是 `... check-stale-modules.py scan-git-diff`，hook 同时通过 stdin 传 JSON。
    若 main 先读 stdin 走 PostToolUse 分支会直接退出，scan-git-diff 永不执行。

    硬断言策略（防 v0.3.x M3 fix-1 倒退）：建临时 git repo → 提交基线 → 改 code → 跑
    `scan-git-diff` + 同时塞 SessionStart JSON stdin → 必须出现 stale-modules.yaml
    含 `git_diff_at_session_start:<rel-path>` 才算通过。
    旧 bug 版（先读 stdin）只会 exit 0 但不写 stale；该断言能精确捕捉到。
    """
    project = Path(tmpdir).resolve()
    (project / "projects" / "modules" / "profile" / "edit").mkdir(parents=True)
    (project / "projects" / "modules" / "profile" / "edit" / "submodule.yaml").write_text(
        "parent_module: profile\n"
        "name: edit\n"
        "code_paths:\n"
        "  - miniprogram/pages/profile/edit/**\n",
        encoding="utf-8", newline="",
    )
    code_dir = project / "miniprogram" / "pages" / "profile" / "edit"
    code_dir.mkdir(parents=True)
    code_file = code_dir / "index.js"
    code_file.write_text("// initial baseline\n", encoding="utf-8", newline="")

    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(project)
    # 屏蔽全局 git config / 钩子，避免污染
    env["GIT_AUTHOR_NAME"] = "test"
    env["GIT_AUTHOR_EMAIL"] = "test@example.com"
    env["GIT_COMMITTER_NAME"] = "test"
    env["GIT_COMMITTER_EMAIL"] = "test@example.com"

    # 建 git 基线
    def _git(*args):
        return subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=15,
        )

    init = _git("init", "-q")
    if init.returncode != 0:
        return [f"git init failed: {init.stderr[:200]}"]
    _git("add", ".")
    commit = _git("commit", "-q", "-m", "baseline")
    if commit.returncode != 0:
        return [f"git commit failed: {commit.stderr[:200]}"]

    # 改一处代码（产生 git diff vs HEAD 的命中条目）
    code_file.write_text("// modified after baseline\n", encoding="utf-8", newline="")

    failures = []

    # Test 1（关键回归）：argv + stdin JSON 同时存在时必须真正执行 scan-git-diff
    stale_file = state_dir_of(project) / "stale-modules.yaml"
    if stale_file.exists():
        stale_file.unlink()
    payload = json.dumps({"hook_event_name": "SessionStart", "cwd": str(project)})
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "scan-git-diff"],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=20,
    )
    if result.returncode != 0:
        failures.append(
            f"argv+stdin exit={result.returncode}, stderr={result.stderr.strip()[:200]}"
        )
    if not stale_file.exists():
        failures.append(
            "argv+stdin: scan-git-diff did NOT write stale-modules.yaml "
            "(regression: stdin 吞了 argv 子命令，主链路不工作)"
        )
    else:
        text = stale_file.read_text(encoding="utf-8")
        expected_marker = "git_diff_at_session_start:miniprogram/pages/profile/edit/index.js"
        if expected_marker not in text:
            failures.append(
                f"argv+stdin: stale-modules.yaml missing expected marker '{expected_marker}' "
                f"(content head: {text[:300]})"
            )
        if "profile/edit:" not in text:
            failures.append(
                "argv+stdin: stale-modules.yaml missing submodule entry 'profile/edit:'"
            )

    # Test 2: 无 argv 时走 PostToolUse；空 stdin 直接退 0
    result2 = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=15,
    )
    if result2.returncode != 0:
        failures.append(f"no argv + empty stdin exit={result2.returncode}")

    # Test 3: 无 argv + PostToolUse JSON 也能命中 stale（正向覆盖路径）
    if stale_file.exists():
        stale_file.unlink()
    payload3 = json.dumps(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(code_file)},
        }
    )
    result3 = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        input=payload3,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=15,
    )
    if result3.returncode != 0:
        failures.append(f"PostToolUse via stdin exit={result3.returncode}")
    if not stale_file.exists():
        failures.append("PostToolUse via stdin: stale-modules.yaml NOT written")
    else:
        text3 = stale_file.read_text(encoding="utf-8")
        if "code_changed:miniprogram/pages/profile/edit/index.js" not in text3:
            failures.append(
                f"PostToolUse via stdin: stale missing 'code_changed:...' marker "
                f"(content head: {text3[:200]})"
            )

    return failures


# ---------- TASK-004 跨仓扫描（嵌套仓 / 未跟踪 / 超时留痕） ----------


def _repoint(mod, project):
    """把被测模块的路径全局量重定向到临时项目（in-process 测试共用）。"""
    project = Path(project).resolve()
    mod.PROJECT_DIR = project
    mod.STATE_DIR = state_dir_of(project)
    mod.INDEX_FILE = mod.STATE_DIR / "code-paths-index.json"
    mod.STALE_FILE = mod.STATE_DIR / "stale-modules.yaml"
    mod.LOCK_FILE = mod.STATE_DIR / "modules-index.lock"
    mod.MODULES_DIR = project / "projects" / "modules"
    mod.EVENTS_FILE = mod.STATE_DIR / "events.jsonl"
    return project


def test_discover_nested_repos(mod, tmpdir):
    project = _repoint(mod, tmpdir)
    (project / "projects" / "modules").mkdir(parents=True)
    # 项目根自己是 git 仓（必须不被当成嵌套仓）
    (project / ".git").mkdir()
    # 一层嵌套仓（.git 目录形态）
    (project / "fw" / "plugins").mkdir(parents=True)
    (project / "fw" / ".git").mkdir()
    # 深层嵌套仓（第一段 vendor 不是仓，仓在 vendor/foo；.git 文件形态 = worktree/submodule）
    (project / "vendor" / "foo" / "src").mkdir(parents=True)
    (project / "vendor" / "foo" / ".git").write_text("gitdir: ../elsewhere\n", encoding="utf-8", newline="")
    # 嵌套仓里再嵌套仓（两层都要收集，浅层不遮蔽深层）
    (project / "fw" / "inner" / "deep").mkdir(parents=True)
    (project / "fw" / "inner" / ".git").mkdir()
    # literal 文件路径（末段是文件，不能当目录崩掉）
    (project / "fw" / "tools").mkdir()
    (project / "fw" / "tools" / "validate.py").write_text("# x\n", encoding="utf-8", newline="")

    cases = [
        (["fw/plugins/**"], ["fw"]),
        (["vendor/foo/src/**"], ["vendor/foo"]),                # 深层仓：第一段查不到，逐段下行查得到
        (["fw/inner/deep/**"], ["fw", "fw/inner"]),             # 嵌套套嵌套：全部收集
        (["fw/tools/validate.py"], ["fw"]),                     # literal 文件路径
        (["miniprogram/pages/**"], []),                         # 路径不存在 → 无仓
        (["../outside/**"], []),                                # .. 段拒绝
        (["/abs/path/**"], []),                                 # 绝对路径拒绝
        (["C:/abs/path/**"], []),                               # 盘符路径拒绝（Windows Path 拼接会整体替换）
        (["\\\\server\\share\\**"], []),                        # UNC / 反斜杠开头拒绝
        (["**"], []),                                           # 无静态前缀 → 不查（项目根不误报为嵌套仓）
        (["fw/plugins/**", "vendor/foo/src/**", "fw/tools/validate.py"], ["fw", "vendor/foo"]),  # 去重
    ]
    failures = []
    for patterns, expected in cases:
        actual = mod.discover_nested_repos(patterns)
        if actual != expected:
            failures.append(f"discover_nested_repos({patterns!r}) = {actual!r}, want {expected!r}")
    return failures


def _read_events(events_file):
    events = []
    if Path(events_file).exists():
        for line in Path(events_file).read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def test_scan_cross_repo_matrix(tmpdir):
    """端到端矩阵：根仓 + 被根仓 ignore 的嵌套仓，一次 scan-git-diff 全覆盖。

    嵌套仓是本检查的主场景：根仓 git 对它整体失明，改动必须靠进入嵌套仓单独问 git。
    """
    project = Path(tmpdir).resolve()
    # 两个子模块：一个管根仓代码，一个管嵌套仓代码
    (project / "projects" / "modules" / "profile" / "edit").mkdir(parents=True)
    (project / "projects" / "modules" / "profile" / "edit" / "submodule.yaml").write_text(
        "parent_module: profile\nname: edit\ncode_paths:\n  - miniprogram/pages/profile/edit/**\n",
        encoding="utf-8", newline="",
    )
    (project / "projects" / "modules" / "framework" / "engine").mkdir(parents=True)
    (project / "projects" / "modules" / "framework" / "engine" / "submodule.yaml").write_text(
        "parent_module: framework\nname: engine\ncode_paths:\n  - nestedfw/plugins/**\n",
        encoding="utf-8", newline="",
    )
    # 坏嵌套仓：.git 是内容非法的 gitfile → 会被发现、rev-parse 必失败 → 应留痕后跳过，
    # 不影响其余仓扫描
    (project / "projects" / "modules" / "framework" / "broken").mkdir(parents=True)
    (project / "projects" / "modules" / "framework" / "broken" / "submodule.yaml").write_text(
        "parent_module: framework\nname: broken\ncode_paths:\n  - brokenfw/**\n",
        encoding="utf-8", newline="",
    )
    code_dir = project / "miniprogram" / "pages" / "profile" / "edit"
    code_dir.mkdir(parents=True)
    (code_dir / "index.js").write_text("// base\n", encoding="utf-8", newline="")
    (code_dir / "staged.js").write_text("// base\n", encoding="utf-8", newline="")
    (project / ".gitignore").write_text("nestedfw/\nbrokenfw/\n.claude/\n*.log\n", encoding="utf-8", newline="")
    nested_dir = project / "nestedfw" / "plugins"
    nested_dir.mkdir(parents=True)
    (nested_dir / "core.py").write_text("# base\n", encoding="utf-8", newline="")
    (nested_dir / "staged.py").write_text("# base\n", encoding="utf-8", newline="")
    broken_dir = project / "brokenfw"
    broken_dir.mkdir()
    (broken_dir / ".git").write_text("not a valid gitfile\n", encoding="utf-8", newline="")
    (broken_dir / "x.py").write_text("# unreachable\n", encoding="utf-8", newline="")

    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(project)
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        env[k] = "test"
    for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        env[k] = "test@example.com"

    def _git(repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=15,
        )

    for repo in (project, project / "nestedfw"):
        r = _git(repo, "init", "-q")
        if r.returncode != 0:
            return [f"git init {repo} failed: {r.stderr[:200]}"]
        _git(repo, "add", ".")
        r = _git(repo, "commit", "-q", "-m", "baseline")
        if r.returncode != 0:
            return [f"git commit {repo} failed: {r.stderr[:200]}"]

    stale_file = state_dir_of(project) / "stale-modules.yaml"
    events_file = state_dir_of(project) / "events.jsonl"

    def _scan():
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "scan-git-diff"],
            input="{}", capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, timeout=30,
        )

    failures = []

    # 负对照：干净双仓 → 不应产生任何 stale
    r = _scan()
    if r.returncode != 0:
        failures.append(f"clean scan exit={r.returncode}, stderr={r.stderr.strip()[:200]}")
    if stale_file.exists() and ":" in stale_file.read_text(encoding="utf-8").split("submodules:")[-1].strip():
        failures.append(f"clean scan wrote stale entries: {stale_file.read_text(encoding='utf-8')[:300]}")

    # 矩阵：根仓 tracked 修改 / staged 修改 / untracked 新文件；嵌套仓 tracked 修改 /
    # staged 修改 / untracked 新文件；ignored 文件（*.log）不得误报；坏嵌套仓留痕不阻他仓
    (code_dir / "index.js").write_text("// changed\n", encoding="utf-8", newline="")
    (code_dir / "staged.js").write_text("// staged change\n", encoding="utf-8", newline="")
    _git(project, "add", "miniprogram/pages/profile/edit/staged.js")
    (code_dir / "brand-new.js").write_text("// untracked\n", encoding="utf-8", newline="")
    (code_dir / "debug.log").write_text("ignored\n", encoding="utf-8", newline="")
    (nested_dir / "core.py").write_text("# changed\n", encoding="utf-8", newline="")
    (nested_dir / "staged.py").write_text("# staged change\n", encoding="utf-8", newline="")
    _git(project / "nestedfw", "add", "plugins/staged.py")
    (nested_dir / "newtool.py").write_text("# untracked\n", encoding="utf-8", newline="")

    r = _scan()
    if r.returncode != 0:
        failures.append(f"matrix scan exit={r.returncode}, stderr={r.stderr.strip()[:200]}")
    if not stale_file.exists():
        return failures + ["matrix scan did NOT write stale-modules.yaml"]
    text = stale_file.read_text(encoding="utf-8")
    expectations = [
        ("根仓 tracked", "git_diff_at_session_start:miniprogram/pages/profile/edit/index.js"),
        ("根仓 staged", "git_diff_at_session_start:miniprogram/pages/profile/edit/staged.js"),
        ("根仓 untracked", "git_diff_at_session_start:miniprogram/pages/profile/edit/brand-new.js"),
        ("嵌套仓 tracked", "git_diff_at_session_start:nestedfw/plugins/core.py"),
        ("嵌套仓 staged", "git_diff_at_session_start:nestedfw/plugins/staged.py"),
        ("嵌套仓 untracked", "git_diff_at_session_start:nestedfw/plugins/newtool.py"),
        ("根仓模块条目", "profile/edit:"),
        ("嵌套仓模块条目", "framework/engine:"),
    ]
    for label, marker in expectations:
        if marker not in text:
            failures.append(f"matrix: missing {label} marker {marker!r} (content: {text[:400]})")
    if "debug.log" in text:
        failures.append("matrix: ignored file (*.log) was wrongly marked stale")
    if "framework/broken:" in text:
        failures.append("matrix: broken nested repo wrongly produced stale entries")
    # 事件断言：坏嵌套仓必须留痕（含 ts + 仓名 + 探测命令），且除它之外无其他故障事件
    errs = [e for e in _read_events(events_file) if e.get("type") == "modules_check_stale_error"]
    broken_errs = [e for e in errs if "brokenfw" in e.get("error", "")]
    other_errs = [e for e in errs if "brokenfw" not in e.get("error", "")]
    if not broken_errs:
        failures.append(f"matrix: broken nested repo left NO error event (events tail: {errs[-2:]})")
    else:
        evt = broken_errs[-1]
        if not evt.get("ts"):
            failures.append(f"matrix: broken-repo event missing ts: {evt}")
        if "rev-parse" not in evt.get("error", ""):
            failures.append(f"matrix: broken-repo event lacks probe command in error: {evt}")
    if other_errs:
        failures.append(f"matrix: unexpected non-broken-repo error events: {other_errs[:2]}")
    return failures


def test_scan_lock_timeout_events(mod, tmpdir):
    """故障注入：锁被占时，入口索引保障与 mark_stale 都必须留事件、不崩、不写半截 stale。"""
    project = _repoint(mod, tmpdir)
    (project / "projects" / "modules" / "profile" / "edit").mkdir(parents=True)
    (project / "projects" / "modules" / "profile" / "edit" / "submodule.yaml").write_text(
        "parent_module: profile\nname: edit\ncode_paths:\n  - miniprogram/**\n",
        encoding="utf-8", newline="",
    )
    env = os.environ.copy()
    for k in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        env[k] = "test"
    for k in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        env[k] = "test@example.com"

    def _git(*args):
        return subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=15,
        )

    (project / "miniprogram").mkdir()
    (project / "miniprogram" / "app.js").write_text("// base\n", encoding="utf-8", newline="")
    _git("init", "-q")
    _git("add", ".")
    r = _git("commit", "-q", "-m", "baseline")
    if r.returncode != 0:
        return [f"git commit failed: {r.stderr[:200]}"]
    (project / "miniprogram" / "app.js").write_text("// changed\n", encoding="utf-8", newline="")

    RealLock = mod.FileLock

    class ShortLock(RealLock):
        def __init__(self, path, timeout=None):
            super().__init__(path, timeout=0.3)

    failures = []
    try:
        mod.FileLock = ShortLock

        # 场景 A：索引未构建 + 锁被占 → 入口保障超时留痕，hook 函数不抛
        holder = RealLock(mod.LOCK_FILE)
        holder.__enter__()
        try:
            mod.scan_git_diff_for_stale()
        except Exception as e:
            failures.append(f"scenario A: scan raised {type(e).__name__}: {e}")
        finally:
            holder.__exit__(None, None, None)
        events = _read_events(mod.EVENTS_FILE)
        ensure_evts = [e for e in events if e.get("type") == "modules_check_stale_error"
                       and e.get("error") == "scan_ensure_index_lock_timeout"]
        if not ensure_evts:
            failures.append(f"scenario A: no scan_ensure_index_lock_timeout event (events: {events[-3:]})")
        elif not ensure_evts[0].get("ts"):
            failures.append("scenario A: event missing ts field")

        # 场景 B：索引已构建（lookup 无需锁）+ 锁被占 → mark_stale 超时留痕（含 submodule+file 字段）
        mod.rebuild_full_index()
        if mod.STALE_FILE.exists():
            mod.STALE_FILE.unlink()
        holder = RealLock(mod.LOCK_FILE)
        holder.__enter__()
        try:
            mod.scan_git_diff_for_stale()
        except Exception as e:
            failures.append(f"scenario B: scan raised {type(e).__name__}: {e}")
        finally:
            holder.__exit__(None, None, None)
        skip_evts = [e for e in _read_events(mod.EVENTS_FILE)
                     if e.get("type") == "modules_stale_write_skipped_lock_timeout"]
        if not skip_evts:
            failures.append("scenario B: no modules_stale_write_skipped_lock_timeout event")
        else:
            evt = skip_evts[-1]
            for field in ("ts", "submodule", "file"):
                if not evt.get(field):
                    failures.append(f"scenario B: event missing field {field!r}: {evt}")
        if mod.STALE_FILE.exists():
            failures.append("scenario B: stale file written despite lock timeout")
    finally:
        mod.FileLock = RealLock
    return failures


# ---------- main ----------


def test_state_dir_placement(tmpdir):
    """状态目录落点：三种项目形态各走一遍，**在真实脚本上**验证写到哪儿——一律写新目录。

    框架只读写新位置（`_state_io.runtime_rel`），旧目录由迁移工具搬、hook 不碰。所以「只有旧目录」那格
    断言的是两件事：写进了新目录，且旧目录的文件集与字节原样不动（它是夹具预先建好的，「不该出现」对它恒红，
    改成「没被改」才分得出「hook 又往旧目录写了」）。另外两格逐条断言**另一侧目录没被创建**（只断言「写对了」
    抓不到「同时也在另一边建了一棵」）。
    """
    failures = []
    base = Path(tmpdir)

    cases = [
        # (标签, 预先建哪个目录, 期望落点, 期望**不该**出现的目录（None = 预先建的旧目录，改为断言原样不动）)
        ("fresh", None, STATE_NEW, STATE_LEGACY),
        ("legacy-only", STATE_LEGACY, STATE_NEW, None),
        ("migrated", STATE_NEW, STATE_NEW, STATE_LEGACY),
    ]
    for label, pre, expect, forbid in cases:
        project = base / label
        (project / "projects" / "modules" / "profile" / "edit").mkdir(parents=True)
        (project / "projects" / "modules" / "profile" / "edit" / "submodule.yaml").write_text(
            "parent_module: profile\nname: edit\ncode_paths:\n  - src/**\n",
            encoding="utf-8", newline="")
        (project / "src").mkdir()
        (project / "src" / "a.js").write_text("//x\n", encoding="utf-8", newline="")
        if pre:
            (project / pre).mkdir(parents=True)
        legacy_before = None
        if pre == STATE_LEGACY:
            (project / pre / "activity-state.json").write_bytes(b'{"session_counter": 7}\n')
            legacy_before = {p.relative_to(project / pre).as_posix(): p.read_bytes()
                             for p in (project / pre).rglob("*") if p.is_file()}

        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(project)
        env["PYTHONUTF8"] = "1"
        payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                              "tool_input": {"file_path": str(project / "src" / "a.js")}})
        subprocess.run([sys.executable, str(SCRIPT_PATH)], input=payload,
                       capture_output=True, text=True, encoding="utf-8", env=env,
                       cwd=str(project), timeout=60)

        stale = project / expect / "stale-modules.yaml"
        if not stale.exists():
            failures.append(f"{label}: 期望写进 {expect}/，实际没有 stale-modules.yaml"
                            f"（状态目录落点错位 → 框架读新目录、这条标记没有读者）")
        if forbid and (project / forbid).exists():
            failures.append(f"{label}: 不该出现的目录 {forbid}/ 被建了出来"
                            f"（漏改的定义点仍在按写死的字面拼路径）")
        if legacy_before is not None:
            after = {p.relative_to(project / pre).as_posix(): p.read_bytes()
                     for p in (project / pre).rglob("*") if p.is_file()}
            if after != legacy_before:
                failures.append(f"{label}: 旧目录 {pre}/ 被改动了（文件集或字节变了）——hook 仍在往旧目录写，"
                                f"迁移工具随后会把它当成冲突")
    return failures


# ---------- 反向索引新鲜度：入口、并发、读不了 ----------


def _fresh_project(base):
    project = Path(base)
    for sub, pat in (("a/x", "src/a/**"), ("b/y", "src/b/**")):
        d = project / "projects" / "modules" / sub
        d.mkdir(parents=True)
        (d / "submodule.yaml").write_text(f"name: {sub}\ncode_paths:\n  - {pat}\n", encoding="utf-8", newline="")
    return project


def test_explicit_index_and_entry(mod, tmpdir):
    """显式传入的合法空索引照用、不触发重建；不传时经入口按声明重建，第二次判新鲜。"""
    project = _repoint(mod, _fresh_project(tmpdir))
    failures = []
    if mod.lookup_submodules("src/a/f.py", index={"buckets": {}}) != []:
        failures.append("显式空索引应照用（命中应为空）")
    if mod.INDEX_FILE.exists():
        failures.append("显式传入索引时不应触发重建写盘")
    _index, status = mod.ensure_fresh_index()
    if status != "rebuilt":
        failures.append(f"首次入口应重建，实际 {status}")
    _index, status = mod.ensure_fresh_index()
    if status != "fresh":
        failures.append(f"声明未变时应判新鲜，实际 {status}")
    if mod.lookup_submodules("src/b/f.py") != ["b/y"]:
        failures.append("入口重建后 lookup 应命中 b/y")
    # project_dir 显式给出：锁、缓存、事件都落在该项目
    other = _fresh_project(Path(tmpdir) / "other")
    if mod.lookup_submodules("src/a/f.py", project_dir=other) != ["a/x"]:
        failures.append("project_dir 显式给出时应按该项目的声明回答")
    if not (state_dir_of(other) / "code-paths-index.json").exists():
        failures.append("project_dir 显式给出时缓存应落在该项目")
    return failures


_RACE_CHILD = r'''
import importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("csm_child", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
counter = Path(sys.argv[2])
real = mod.save_index
def counted(index, index_file=None):
    with open(counter, "a", encoding="utf-8", newline="") as fh:
        fh.write("save\n")
    return real(index, index_file)
mod.save_index = counted
ready = Path(sys.argv[3])
ready.write_text("1", encoding="utf-8", newline="")
_i, status = mod.ensure_fresh_index()
print(status)
'''


def test_concurrent_rebuild_once(mod, tmpdir):
    """两个进程都先读到旧缓存、再排队进锁：缓存**实际保存次数**为 1（锁内复判），最终内容正确。"""
    import time as _time
    base = Path(tmpdir)
    project = _repoint(mod, _fresh_project(base / "p"))
    child = base / "child.py"
    child.write_text(_RACE_CHILD, encoding="utf-8", newline="")
    counter = base / "saves.txt"
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project), PYTHONUTF8="1")
    failures = []
    holder = mod.FileLock(mod.LOCK_FILE)
    holder.__enter__()
    procs = []
    try:
        for i in range(2):
            procs.append(subprocess.Popen([sys.executable, str(child), str(SCRIPT_PATH), str(counter),
                                           str(base / f"ready{i}")], env=env, stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True, encoding="utf-8"))
        deadline = _time.monotonic() + 20
        while not all((base / f"ready{i}").exists() for i in range(2)) and _time.monotonic() < deadline:
            _time.sleep(0.05)
        _time.sleep(0.8)  # 就绪后各自做完锁外判过期、停在锁上
    finally:
        holder.__exit__(None, None, None)
    outs = [p.communicate(timeout=60) for p in procs]
    statuses = sorted(o[0].strip() for o in outs)
    if statuses != ["fresh", "rebuilt"]:
        failures.append(f"两进程应一重建一判新鲜，实际 {statuses} / stderr {[o[1][-200:] for o in outs]}")
    saves = counter.read_text(encoding="utf-8").splitlines() if counter.exists() else []
    if len(saves) != 1:
        failures.append(f"缓存应只保存 1 次，实际 {len(saves)} 次")
    if mod.lookup_submodules("src/a/f.py", index=mod.load_index()) != ["a/x"]:
        failures.append("并发重建后缓存内容不对")
    return failures


def test_unreadable_cli_and_hook(tmpdir):
    """有声明读不了：`rebuild-index` / `init-submodule` exit 1 且点名文件、缓存字节不变；
    PostToolUse 改 submodule.yaml 时不写「段已重建」。"""
    project = _fresh_project(tmpdir)
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project), PYTHONUTF8="1")
    failures = []

    def cli(*args, stdin=None):
        return subprocess.run([sys.executable, str(SCRIPT_PATH), *args], input=stdin, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    r = cli("rebuild-index")
    if r.returncode != 0:
        failures.append(f"声明正常时 rebuild-index 应 exit 0: {r.returncode} {r.stderr[-200:]}")
    index_file = state_dir_of(project) / "code-paths-index.json"
    if not index_file.exists():
        failures.append("声明正常时 rebuild-index 应写出索引")
    if any(e.get("type") in ("modules_index_rebuilt", "modules_index_segment_rebuilt")
           for e in _read_events(state_dir_of(project) / "events.jsonl")):
        failures.append("重建成功不应再写成功流水事件")
    cache = index_file.read_bytes()
    # PostToolUse 改一份**正常**的 submodule.yaml：分段路径真的重建了（索引里出现新声明），但不写成功流水
    good = project / "projects" / "modules" / "b" / "y" / "submodule.yaml"
    good_saved = good.read_bytes()
    good.write_bytes(good_saved.rstrip(b"\n") + b"\n  - src/segment-probe/**\n")
    r = cli(stdin=json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                              "tool_input": {"file_path": str(good)}}))
    if r.returncode != 0:
        failures.append(f"PostToolUse 改正常 submodule.yaml 应 exit 0: {r.returncode} {r.stderr[-200:]}")
    if b"segment-probe" not in index_file.read_bytes():
        failures.append("PostToolUse 改正常 submodule.yaml 后索引没重建（新声明不在索引里）——下面「不写成功事件」无对象")
    if any(e.get("type") in ("modules_index_rebuilt", "modules_index_segment_rebuilt")
           for e in _read_events(state_dir_of(project) / "events.jsonl")):
        failures.append("分段重建成功不应再写成功流水事件")
    good.write_bytes(good_saved)
    cli("rebuild-index")
    cache = index_file.read_bytes()
    bad = project / "projects" / "modules" / "b" / "y" / "submodule.yaml"
    bad.write_bytes(b"name: y\ncode_paths:\n  - src/\xff/**\n")
    for args in (("rebuild-index",), ("init-submodule", "b/y")):
        r = cli(*args)
        if r.returncode != 1 or "b/y/submodule.yaml" not in r.stderr:
            failures.append(f"{args[0]}: 读不了时应 exit 1 并点名文件，实际 rc={r.returncode} {r.stderr[-200:]}")
    if index_file.read_bytes() != cache:
        failures.append("读不了时缓存被覆盖")
    payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                          "tool_input": {"file_path": str(bad)}})
    r = cli(stdin=payload)
    events = _read_events(state_dir_of(project) / "events.jsonl")
    if any(e.get("type") == "modules_index_segment_rebuilt" for e in events):
        failures.append("读不了时 PostToolUse 不应写 modules_index_segment_rebuilt")
    if r.returncode != 0:
        failures.append(f"PostToolUse 不应因读不了而非零退出: {r.returncode}")
    return failures


def test_unlistable_dir(mod, tmpdir):
    """某个模块目录列不了（权限）：不当成「这片为空」——快照记错、入口返回 unreadable、缓存字节不变。

    修复前用 `Path.glob` 枚举，列不了的目录被静默跳过：快照 errors 为空、入口把完整缓存覆盖成残缺的。"""
    from unittest import mock
    project = _repoint(mod, _fresh_project(tmpdir))
    failures = []
    _i, status = mod.ensure_fresh_index()
    cache = mod.INDEX_FILE.read_bytes()
    good_stamp = mod.declared_stamp(mod.MODULES_DIR)
    blocked = (mod.MODULES_DIR / "b").resolve()
    real = os.scandir

    def scandir(path="."):
        if Path(path).resolve() == blocked:
            raise PermissionError(13, "denied", str(path))
        return real(path)

    with mock.patch.object(mod.os, "scandir", scandir):
        _stamp, pairs, errors = mod.declared_snapshot(mod.MODULES_DIR)
        if errors != [("b/", "PermissionError")]:
            failures.append(f"列不了的目录应记进 errors: {errors}")
        if pairs != [("src/a/**", "a/x")]:
            failures.append(f"列得了的那片照常读出: {pairs}")
        if mod.declared_stamp(mod.MODULES_DIR) == good_stamp:
            failures.append("列不了时戳应与正常时不同（否则读者判新鲜、看不见这个故障）")
        _i, status = mod.ensure_fresh_index()
        if status != "unreadable":
            failures.append(f"入口应返回 unreadable，实际 {status}")
    if mod.INDEX_FILE.read_bytes() != cache:
        failures.append("列不了时完整缓存被覆盖")
    if mod.lookup_submodules("src/b/f.py") != ["b/y"]:
        failures.append("故障解除后 b/y 的命中应还在")
    # 缓存建好之后新加的模块目录恰好列不了：已知声明一条不少，戳只能靠「列不了」那几行变——
    # 不把它们算进戳，入口判新鲜、新声明静默缺席，且不留任何事件
    new = mod.MODULES_DIR / "c" / "z"
    new.mkdir(parents=True)
    (new / "submodule.yaml").write_text("name: c/z\ncode_paths:\n  - src/c/**\n", encoding="utf-8", newline="")
    blocked = (mod.MODULES_DIR / "c").resolve()
    with mock.patch.object(mod.os, "scandir", scandir):
        _i, status = mod.ensure_fresh_index()
    if status != "unreadable":
        failures.append(f"新加的目录列不了时入口应返回 unreadable（不是判新鲜），实际 {status}")
    del project
    return failures


def test_read_once(mod, tmpdir):
    """每份声明只读一次：第一次读成功后文件再被打开就失败，映射照样完整、不记错。

    反例：把路径（而不是已读文本）交给 parse_code_paths 时，它第二次读失败、吞异常返回 []，
    得到 pairs 缺一片而 errors 为空——被当成「成功读到空声明」。"""
    from unittest import mock
    _repoint(mod, _fresh_project(tmpdir))

    def gone(self, *a, **k):
        raise FileNotFoundError(2, "gone", str(self))

    opened = []
    real_rb = Path.read_bytes

    def counted(self):
        opened.append(self.name if self.name != "submodule.yaml" else "/".join(self.parts[-3:-1]))
        return real_rb(self)

    with mock.patch.object(Path, "read_text", gone), mock.patch.object(Path, "read_bytes", counted):
        _stamp, pairs, errors = mod.declared_snapshot(mod.MODULES_DIR)
    want = [("src/a/**", "a/x"), ("src/b/**", "b/y")]
    if pairs != want or errors != []:
        return [f"第二次打开失败不应影响结果: pairs={pairs} errors={errors}"]
    # 逐文件计数：成功路径连读两次 read_bytes 也能骗过上面那条（它只禁 read_text）
    if sorted(opened) != ["a/x", "b/y"]:
        return [f"每份声明应恰读一次: {sorted(opened)}"]
    return []


def test_locked_recheck(mod, tmpdir):
    """确定性屏障：本进程锁外判旧之后、进锁之前，「另一个进程」已完成重建——锁内复判应看到新鲜、不再保存。

    子进程版（`concurrent_rebuild_once`）靠 sleep 让两进程停在锁上，第二个晚到时删掉锁内复判也可能绿；
    这里把「另一个进程」塞进进锁那一刻，两者首读都旧是构造出来的，不靠时序。"""
    _repoint(mod, _fresh_project(tmpdir))
    failures = []
    mod.ensure_fresh_index()
    y = mod.MODULES_DIR / "b" / "y" / "submodule.yaml"
    y.write_text("name: b/y\ncode_paths:\n  - src/b/**\n  - src/c/**\n", encoding="utf-8", newline="")
    real_lock, real_save = mod.FileLock, mod.save_index
    saves, fired = [], []

    def counted(index, index_file=None):
        saves.append(1)
        return real_save(index, index_file)

    class Racing:
        def __init__(self, lock_file):
            self._lock_file, self._inner = lock_file, real_lock(lock_file)

        def __enter__(self):
            if not fired:
                fired.append(1)
                with real_lock(self._lock_file):
                    mod._rebuild_unlocked(None, "drift")   # 另一个进程：判旧 → 进锁 → 重建 → 出锁
            return self._inner.__enter__()

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

    mod.FileLock, mod.save_index = Racing, counted
    try:
        _i, status = mod.ensure_fresh_index()
    finally:
        mod.FileLock, mod.save_index = real_lock, real_save
    if not fired:
        failures.append("屏障没触发：本进程锁外判成了新鲜（夹具没把缓存弄旧）")
    if status != "fresh":
        failures.append(f"锁内复判应看到另一进程的重建、返回 fresh，实际 {status}")
    if len(saves) != 1:
        failures.append(f"缓存应只保存 1 次（另一进程那次），实际 {len(saves)} 次")
    if mod.lookup_submodules("src/c/f.py", index=mod.load_index()) != ["b/y"]:
        failures.append("最终缓存应含新声明的 src/c/**")
    return failures


def test_stamp_saved(mod, tmpdir):
    """只改 mtime（内容不变）：入口重建一次并**保存新戳**，下一次判新鲜——不保存的话每次都重建。"""
    _repoint(mod, _fresh_project(tmpdir))
    failures = []
    mod.ensure_fresh_index()
    y = mod.MODULES_DIR / "a" / "x" / "submodule.yaml"
    st = y.stat()
    os.utime(y, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    _i, status = mod.ensure_fresh_index()
    if status != "rebuilt":
        failures.append(f"mtime 变了应重建，实际 {status}")
    if mod.load_index().get("source_stamp") != mod.declared_stamp(mod.MODULES_DIR):
        failures.append("重建后缓存里的 source_stamp 应等于当前戳")
    _i, status = mod.ensure_fresh_index()
    if status != "fresh":
        failures.append(f"保存新戳后应判新鲜，实际 {status}")
    return failures


def test_cli_project_flag(tmpdir):
    """`--project <目录>`：rebuild-index / init-submodule 重建的是该项目，不是环境解析出的项目。"""
    base = Path(tmpdir)
    here, there = _fresh_project(base / "here"), _fresh_project(base / "there")
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(here), PYTHONUTF8="1")
    failures = []

    def cli(*args):
        return subprocess.run([sys.executable, str(SCRIPT_PATH), *args], env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    for args in (("rebuild-index",), ("init-submodule", "a/x")):
        idx = state_dir_of(there) / "code-paths-index.json"
        if idx.exists():
            idx.unlink()
        r = cli(*args, "--project", str(there))
        if r.returncode != 0 or not idx.exists():
            failures.append(f"{args[0]} --project 应重建目标项目: rc={r.returncode} {r.stderr[-200:]}")
        if (state_dir_of(here) / "code-paths-index.json").exists():
            failures.append(f"{args[0]} --project 不应碰环境解析出的项目")
    r = cli("rebuild-index", "--project")
    if r.returncode != 2:
        failures.append(f"--project 缺参数应 exit 2: {r.returncode}")
    # 不认 --project 的子命令、等号写法、多余参数：一律写前 exit 2，默认项目（here）的状态一个字节不动
    stale = state_dir_of(here) / "stale-modules.yaml"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("submodules:\n  a/x:\n    reasons:\n      - code_changed:src/a/f.py\n",
                     encoding="utf-8", newline="")
    snap = stale.read_bytes()
    there_idx = state_dir_of(there) / "code-paths-index.json"
    there_snap = there_idx.read_bytes()
    for args in (("clear-stale", "a/x", "--project", str(there)), ("scan-git-diff", "--project", str(there)),
                 ("rebuild-index", f"--project={there}"), ("rebuild-index", "--project", str(there), "extra"),
                 ("rebuild-index", "--project", str(there), "--project", str(there)),
                 ("rebuild-index", "--project", ""), ("rebuild-index", "--projec", str(there)),
                 ("rebuild-index", str(there)), ("init-submodule", "--project", str(there)),
                 ("init-submodule", "a/x", "b/y"), ("init-submodule", "--verbose"), ("init-submodule", "")):
        r = cli(*args)
        if r.returncode != 2:
            failures.append(f"{args} 应在写前 exit 2，实际 {r.returncode}")
    if stale.read_bytes() != snap or (state_dir_of(here) / "code-paths-index.json").exists():
        failures.append("被拒的调用不应改动默认项目的 stale 清单或缓存")
    if there_idx.read_bytes() != there_snap:
        failures.append("被拒的调用也不应改动 --project 指向的项目")
    plain = base / "plain"
    plain.mkdir()
    r = cli("rebuild-index", "--project", str(plain))
    if r.returncode != 1 or (state_dir_of(plain) / "code-paths-index.json").exists():
        failures.append(f"--project 指向非 modules 项目应 exit 1 且零写入: {r.returncode}")
    return failures


def main():
    mod = _load_module()
    all_failures = []

    print(f"[test] check-stale-modules.py @ {SCRIPT_PATH}")
    print()

    # 单元测试
    all_failures += [("glob_to_regex", f) for f in test_glob_to_regex(mod)]
    all_failures += [("first_static_segment", f) for f in test_first_static_segment(mod)]
    with tempfile.TemporaryDirectory() as tmp:
        all_failures += [("parse_code_paths", f) for f in test_parse_code_paths(mod, tmp)]
    with tempfile.TemporaryDirectory() as tmp:
        all_failures += [
            ("lookup_submodules", f) for f in test_lookup_submodules_with_dummy_project(mod, tmp)
        ]
    # 子命令分派回归（用 subprocess，独立进程更接近 hook 真实场景）
    with tempfile.TemporaryDirectory() as tmp:
        all_failures += [("main_dispatch", f) for f in test_main_dispatch_argv_priority(tmp)]
    # TASK-004 跨仓扫描
    with tempfile.TemporaryDirectory() as tmp:
        all_failures += [("discover_nested_repos", f) for f in test_discover_nested_repos(mod, tmp)]
    with tempfile.TemporaryDirectory() as tmp:
        all_failures += [("scan_cross_repo", f) for f in test_scan_cross_repo_matrix(tmp)]
    with tempfile.TemporaryDirectory() as tmp:
        all_failures += [("scan_lock_timeout", f) for f in test_scan_lock_timeout_events(mod, tmp)]
    with tempfile.TemporaryDirectory() as tmp:
        all_failures += [("state_dir_placement", f) for f in test_state_dir_placement(tmp)]
    with tempfile.TemporaryDirectory() as tmp:
        all_failures += [("explicit_index_and_entry", f) for f in test_explicit_index_and_entry(mod, tmp)]
    with tempfile.TemporaryDirectory() as tmp:
        all_failures += [("concurrent_rebuild_once", f) for f in test_concurrent_rebuild_once(mod, tmp)]
    with tempfile.TemporaryDirectory() as tmp:
        all_failures += [("unreadable_cli_and_hook", f) for f in test_unreadable_cli_and_hook(tmp)]
    for name, fn in (("unlistable_dir", test_unlistable_dir), ("read_once", test_read_once),
                     ("locked_recheck", test_locked_recheck), ("stamp_saved", test_stamp_saved)):
        with tempfile.TemporaryDirectory() as tmp:
            all_failures += [(name, f) for f in fn(mod, tmp)]
    with tempfile.TemporaryDirectory() as tmp:
        all_failures += [("cli_project_flag", f) for f in test_cli_project_flag(tmp)]

    if not all_failures:
        print("✓ All check-stale-modules.py tests passed")
        return 0

    print(f"✗ {len(all_failures)} failure(s):")
    for name, msg in all_failures:
        print(f"  [{name}] {msg}")
    return 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
