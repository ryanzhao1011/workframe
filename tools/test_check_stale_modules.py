#!/usr/bin/env python3
"""
单元测试 — plugins/core/scripts/check-stale-modules.py 关键函数 + 回归。

覆盖：
  - glob_to_regex：glob → regex 转换正确性
  - first_static_segment：bucket key 提取
  - parse_code_paths：submodule.yaml code_paths 解析
  - lookup_submodules：反向 lookup 命中正确子模块
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

# 本文件**刻意不包 UTF-8 流**（在 `check_utf8_stream_wrap_symmetric` 的豁免白名单里）：
# 下面 import 的 check-stale-modules.py 是模块级包装，import 那一刻就把两条流包好了。
# 再包一层就是两个 TextIOWrapper 抢同一个 buffer——先被回收的把 buffer 关掉，
# 另一个当场 `ValueError: I/O operation on closed file` + `lost sys.stderr`。
# 加过一次，测试立刻红；本进程的中文输出由被测模块那层覆盖，无需自己再包。

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

    if not all_failures:
        print("✓ All check-stale-modules.py tests passed")
        return 0

    print(f"✗ {len(all_failures)} failure(s):")
    for name, msg in all_failures:
        print(f"  [{name}] {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
