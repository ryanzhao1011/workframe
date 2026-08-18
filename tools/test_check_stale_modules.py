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
    mod.STATE_DIR = project / ".claude" / "workframe-state"
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
    stale_file = project / ".claude" / "workframe-state" / "stale-modules.yaml"
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


# ---------- main ----------


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

    if not all_failures:
        print("✓ All check-stale-modules.py tests passed")
        return 0

    print(f"✗ {len(all_failures)} failure(s):")
    for name, msg in all_failures:
        print(f"  [{name}] {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
