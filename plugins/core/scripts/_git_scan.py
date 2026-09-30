#!/usr/bin/env python3
"""git 改动扫描面的唯一实现——「扫哪些仓 × 取哪几类改动 × 路径怎么拼」只在这里写一次。

出处是 `check-stale-modules.py` 的 `scan_git_diff_for_stale()`。提取成模块是因为出现了
第二个消费方（`signoff_tier_check.py` 的四段闸门判定吃同一份扫描面），而**两处手写必漂**：
根仓通常 ignore 嵌套仓（把插件仓 vendored 进项目根就是这个拓扑），漏掉嵌套仓那半的一方
会在嵌套仓侧恒读到「零改动」，**且不报错**——依赖它的尺寸上限与路径类判定当场变成假绿。

本模块只回答扫描面，**不定义失败时怎么留痕**：git 调用与错误策略由调用方注入
（stale 扫描要写事件，判定脚本纯读），两者对扫描面的口径因此完全相同。

契约（与 `_state_io` / `_harness` 同款）：
  - import 无副作用：不碰 stdout、不建目录、不读磁盘
  - 文件名用下划线而非连字符——连字符的模块名 import 不进来
"""

import re
from pathlib import Path


def discover_nested_repos(project_dir, patterns):
    """从 code_paths patterns 发现项目根**之内**的嵌套 git 仓，返回项目根相对路径列表。

    根仓通常 ignore 嵌套仓，`git diff` 对其整体失明——嵌套仓必须逐个单独问 git。
    做法：取每个 pattern 的静态目录前缀（到第一个含通配符的段为止），从项目根沿段
    逐级向下查 `.git`（目录或文件都算——worktree / submodule 形态是文件）。命中**全部**
    收集而非第一个：嵌套仓里再嵌套仓时，深层仓的改动对浅层仓的 git 同样不可见。
    只查 code_paths 指到的路径——指不到的仓与 stale 无关，无需发现。
    前缀末段若是 literal 文件名，`<file>/.git` 的存在性检查恒为 False，无需特判。

    拒绝的 pattern（「正向拼接不越出项目根」只对相对路径成立）：绝对路径 / 盘符路径
    （Windows 下 `Path / "C:/x"` 会整个替换成绝对路径）/ 以 / 或 \\ 开头 / 含 `..` 段。
    项目根自身恒不在候选中（从第一段之下才开始查），外层仓不会被误当嵌套仓。
    """
    project_dir = Path(project_dir)
    repos = set()
    for pat in patterns:
        norm = pat.replace("\\", "/")
        segs = [s for s in norm.split("/") if s]
        if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or ".." in segs:
            continue
        p = project_dir
        rel_parts = []
        for seg in segs:
            if any(c in seg for c in "*?["):
                break
            p = p / seg
            rel_parts.append(seg)
            try:
                if (p / ".git").exists():
                    repos.add("/".join(rel_parts))
            except OSError:
                break
    return sorted(repos)


def collect_changed_files(project_dir, patterns, git_ok, git_lines,
                          since_ref=None, on_note=None):
    """按仓收集改动文件名。返回 `(by_repo, notes)`。

    - `by_repo`：`{repo_rel: set(仓相对路径)}`，根仓的 key 是 `""`
    - `notes`：扫描面缺口清单，元素为 `(kind, repo_label, detail)`；
      `kind ∈ {"broken_nested_repo", "git_failed", "since_ref_unresolved"}`

    覆盖：根仓 + `patterns` 指到的嵌套 git 仓，每仓取未提交 tracked（`diff HEAD`）+
    staged（`diff --cached`）+ 未跟踪（`ls-files --others --exclude-standard`）三类并集。
    **默认不覆盖**已 commit 的改动——那不在上述三类差异里，这是 stale 扫描有意的验收边界；
    需要「上一个签发点到现在」这种含提交的切片时传 `since_ref`，本函数**追加**一条
    `diff <ref>..HEAD`，不改动原三类。

    注入点：
      - `git_ok(repo_abs, args) -> bool`：探测性命令，失败即 False（合法状态，不该留痕）
      - `git_lines(repo_abs, args, label) -> list|None`：取 stdout 行；失败返回 None
      - `on_note(kind, repo_label, detail)`：扫描面出缺口时回调；调用方自定留痕方式

    **缺口必须被调用方看见**：`git_lines` 返回 None 时本函数只能少收一批文件，静默下去
    就是「零改动」——判定类消费方据此报绿正是本模块要防的那种假绿。所以缺口既走
    `on_note` 回调，也进返回值的 `notes`，两条路任选其一都拿得到。
    """
    notes = []

    def note(kind, label, detail):
        notes.append((kind, label, detail))
        if on_note is not None:
            on_note(kind, label, detail)

    by_repo = {}
    for repo_rel in [""] + discover_nested_repos(project_dir, patterns):
        repo_abs = Path(project_dir) / repo_rel if repo_rel else Path(project_dir)
        label = repo_rel or "."
        # 探测失败的语义按仓分两种：根目录可以合法地不是 git 仓 → 静默跳过；
        # 嵌套仓是靠 .git 存在才被发现的，探测失败不是「合法非仓」而是坏仓——
        # 它的改动从此不可见，静默跳过等于把本模块要消灭的那类无声失明又留一个口子，
        # 必须留痕后再继续扫其余仓
        if not git_ok(repo_abs, ["rev-parse", "--git-dir"]):
            if repo_rel:
                note("broken_nested_repo", label,
                     "rev-parse --git-dir 失败（坏嵌套仓），本仓已跳过")
            continue
        cmds = [
            ["diff", "--name-only", "--cached"],
            ["ls-files", "--others", "--exclude-standard"],
        ]
        # 无 HEAD（git init 后尚无 commit）是合法状态：diff HEAD 必失败，跳过该条即可——
        # 此时一切改动都落在 staged / untracked 两条里，不漏
        has_head = git_ok(repo_abs, ["rev-parse", "--verify", "--quiet", "HEAD"])
        if has_head:
            cmds.insert(0, ["diff", "--name-only", "HEAD"])
        if since_ref:
            # ref 在哪个仓解析得到是逐仓的事：多仓场景下同一个 hash 通常只属于一个仓，
            # 解析不到既不是错误也不能当成「该仓无改动」——留痕交给调用方判。
            if not has_head:
                note("since_ref_unresolved", label, f"{since_ref}：本仓无 HEAD")
            elif not git_ok(repo_abs, ["rev-parse", "--verify", "--quiet",
                                       f"{since_ref}^{{commit}}"]):
                note("since_ref_unresolved", label, f"{since_ref}：本仓解析不到该 commit")
            else:
                cmds.append(["diff", "--name-only", f"{since_ref}..HEAD"])
        bucket = by_repo.setdefault(repo_rel, set())
        for args in cmds:
            lines = git_lines(repo_abs, args, label)
            if lines is None:
                note("git_failed", label, " ".join(args))
                continue
            for line in lines:
                line = line.strip()
                if not line or line.endswith("/"):
                    # 目录条目（根仓未 ignore 嵌套仓时 ls-files 会报 `<dir>/`）无从 lookup
                    continue
                bucket.add(line)
    return by_repo, notes


def flatten(by_repo):
    """`by_repo` → 项目根相对路径集合（`scan_git_diff_for_stale` 的原口径）。"""
    return {
        (f"{repo_rel}/{rel}" if repo_rel else rel)
        for repo_rel, rels in by_repo.items()
        for rel in rels
    }
