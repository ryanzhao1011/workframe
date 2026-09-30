#!/usr/bin/env python3
"""
project_scaffold.py — 在目标项目初始化 workframe 骨架（插件内自包含版）

项目骨架落盘的**唯一实现**。marketplace（GitHub / 目录）订阅的用户只有插件本体、没有
框架仓 tools/，所以这个能力必须长在插件里。2026-08-10 起仓根不再有任何安装入口——
launcher 调它，无人值守 / CI 场景也直接调它。

用法（launcher setup skill 调用 / 无人值守 / 用户手工）：
    python "<插件根>/scripts/project_scaffold.py" --project "/path/to/project"
    python "<插件根>/scripts/project_scaffold.py" --project "<新目录>" \
        --params params.json --create-missing
    python "<插件根>/scripts/project_scaffold.py" --project "<目标>" \
        --params params.json --write-subscription
    # 只读预演（确认页的动作清单取它的输出，逐字贴）：把上面任意一条原样加 --print-plan
    python "<插件根>/scripts/project_scaffold.py" --project "<目标>" \
        --params params.json --create-missing --require-empty --write-subscription --print-plan
    # 插件根见目标项目运行态状态目录下的 plugin-root.txt（SessionStart hook 维护）

行为：
    - merge 模式写 .workframe-config.json（框架字段刷新，用户字段保留，损坏不覆盖）
    - 初始化 projects/ + 运行态状态目录 + 角色记忆目录 + logs/ 骨架
    - 确保 .gitignore 含 Git 策略必需条目（非破坏性追加 managed block）
    - AGENTS.md 恒渲染（带 --params 时填对话参数，不带时填兜底文本），唯一写入点见 ensure_agents_md
    - 带 --params 时额外渲染 CLAUDE.md / modules 总图 / 周边资产层；两处渲染都断言占位符零残留
    - 已存在文件一律不覆盖；stdout 打印 created / skipped 摘要
    - `--print-plan` 只读预演：算出本次将要建 / 改的每一个文件（含项目外的用户级市场
      注册表）、外加已存在而本脚本一律跳过的那些（第四格，**逐项点名**），分四格打印，
      **一个字节都不写、目标目录也不创建**；清单与判据都取自真写
      那条路径（`scaffold_targets` / `project_docs_targets` / `_settings_io.would_change`）
    - plugin 订阅**只在带 `--write-subscription` 时做**（两扇门通用：写项目
      `.claude/settings.json` 的 extraKnownMarketplaces + enabledPlugins，再补用户级
      市场注册表），不带这个参数时一个字节都不写——实现见 `write_subscription`，
      调用顺序见 launcher setup skill §5
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

def _force_utf8_io():
    """把 stdout 与 stderr 都包成 UTF-8——**只在 CLI 入口调用，不在 import 时**。

    本模块会被 import（`workframe_doctor` 取 gitignore 规则解析、launcher 取
    `mark_setup_step`）。模块级包装会和调用方自己的那层抢同一个 buffer：先被回收的
    把 buffer 关掉，另一个随即抛 `I/O operation on closed file`——doctor 里已经为这个
    坑写过注释，给它加 import 时仍当场复现了一次。
    与 workframe_doctor / recompute_* 的做法保持一致：import 无副作用。
    """
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


# 同目录公共模块：运行态目录只有一份实现（见 _state_io.py 抬头）
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _settings_io  # noqa: E402
from _state_io import memory_dir_of, runtime_rel, state_dir_of  # noqa: E402
from _harness import (SKILLS_ABSENT, SKILLS_DIR_CC, SKILLS_DIR_NEUTRAL,  # noqa: E402
                      SKILLS_LINK_OK, SKILLS_LINK_TARGET_MISSING, project_skills_conflict,
                      project_skills_dir, skills_cc_form, skills_link_dangling)
from _harness import skills_link_present as _harness_skills_link_present  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[1]

# 模板根目录——scaffold 与 module-init / migrate-to-modules 共用同一组模板，避免漂移。
SCAFFOLD_TEMPLATES_DIR = PLUGIN_ROOT / "templates"


def read_framework_version():
    """从插件 plugin.json 读取版本号，避免字面量固化导致写入旧版本号。"""
    plugin_json = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    try:
        return json.loads(plugin_json.read_text(encoding="utf-8")).get("version", "unknown")
    except Exception:
        return "unknown"


def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


# 「近空」豁免：这些东西的存在不代表目录已被占用（与 launcher SKILL §1 的近空口径一致）
NEAR_EMPTY_ALLOWLIST = {
    ".git", ".gitignore", ".gitkeep", ".gitattributes",
    "readme.md", "readme", "readme.txt",
    ".ds_store", "thumbs.db", "desktop.ini",
    # 装机自己的中间产物：launcher 应当把它们写在目标目录之外（setup SKILL §5 已写明），
    # 这里兜一道——真写进来了也不该让目标目录看起来「已被占用」而把新建误导成接入。
    "params.json", "tree.json",
}


def _substantive_entries(project_dir: Path):
    """目录里有没有「实质内容」——返回不在近空白名单里的条目名。

    用于 `--require-empty`：「新建」路径下目标必须是空的或近空的。已有代码仓被当成新项目
    scaffold 时不会破坏文件（不覆盖原则），但会**静默跳过接入流程**（存量分析 + CLAUDE.md
    整合）。目标已有 CLAUDE.md 时 doctor 的 claude_md 会以 error 抓出缺 `@AGENTS.md` 导入，
    但**已经跳过的存量分析与整合补不回来**，仍要返工走接入流程；目标没有 CLAUDE.md 时
    scaffold 渲染的是模板版、导入行在位，验收看不出走错了路径。

    枚举失败时**抛 OSError 而不是返回空列表**：「读不出来」不等于「没有内容」，
    吞掉异常等于让 --require-empty 这道闸在权限或路径异常下自动失效
    。
    """
    return sorted(e.name for e in project_dir.iterdir()
                  if e.name.lower() not in NEAR_EMPTY_ALLOWLIST)


def _copy_if_absent(src: Path, dst: Path, created_list, skipped_list):
    """从模板复制到目标，仅当目标不存在时；返回是否真的写入。"""
    if dst.exists():
        skipped_list.append(str(dst))
        return False
    if not src.exists():
        skipped_list.append(f"{dst} (missing template: {src})")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8", newline="")
    created_list.append(str(dst))
    return True


def _render_if_absent(src: Path, dst: Path, mapping: dict, created_list, skipped_list):
    """带占位符渲染的 _copy_if_absent——模板含 {{XXX}} 时必须走本函数。

    走 _copy_if_absent 会把占位符原样落进项目文件（frontmatter 里就是一个非法
    时间戳，还会被 doc-graph-health 的 updated 异常维度反咬）。
    """
    if dst.exists():
        skipped_list.append(str(dst))
        return False
    if not src.exists():
        skipped_list.append(f"{dst} (missing template: {src})")
        return False
    rendered = render_placeholders(src.read_text(encoding="utf-8"), mapping, src.name)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(rendered, encoding="utf-8", newline="")
    created_list.append(str(dst))
    return True


def _write_if_absent(dst: Path, content: str, created_list, skipped_list):
    """无对应模板的小文件（.gitkeep 等）直接写内容。"""
    if dst.exists():
        skipped_list.append(str(dst))
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content, encoding="utf-8", newline="")
    created_list.append(str(dst))
    return True


# Baseline core roles — plugin 提供的 4 个通用角色，scaffold 必须为它们预创建角色记忆骨架
BASELINE_CORE_ROLES = ["pm", "dev", "qa", "prompt-eng"]


def _role_memory_placeholder(role: str) -> str:
    """role MEMORY.md 空骨架内容（高置信关键事实区；字符预算见 doctor ROLE_MEMORY_MAX_CHARS）"""
    return (
        f"# {role} MEMORY\n"
        f"\n"
        f"> 高置信关键事实区（先过归属分流：业务知识落 modules/ 文档、此处至多留指针，见 agent-protocols Step 2；再满足 D/U/R/A 准入），由 librarian 从 notes.md 提升。\n"
        f"> 总量 ≤ 8000 字符；超出由 librarian 生成容量候选，经用户确认后降级为 notes。\n"
        f"> [纠正] 标记的条目永不清理。\n"
    )


def _role_notes_placeholder(role: str) -> str:
    """role notes.md 空骨架内容（agent wrap-up Step 2 写入缓冲区）"""
    return (
        f"# {role} notes\n"
        f"\n"
        f"> 经验缓冲区，agent wrap-up Step 2 时按 D/U/R/A 评估写入。\n"
        f"> 由 librarian 周期性评估，达准入标准则提升到 MEMORY.md。\n"
    )


# ---------------------------------------------------------------- 项目 skills 目录的到达路径
#
# 真实源 `.agents/skills/`（两扇门共读的中立目录），`.claude/skills` 做成指向它的目录链接：
# Windows 是 junction（`mklink /J` 语义，不需要管理员），POSIX 是 symlink（**未验**——只在
# Windows 上实测过 CC 跟随 junction 发现 skill）。解析规则只有一份：`_harness.project_skills_dir`。

# `skills_link_present`：`.claude/skills` 是目录链接（junction / symlink，悬空也算）吗——只有这种形态才要
# .gitignore 忽略它。不忽略的后果按平台不同：Windows 上 git 把 junction 当目录，会穿过它把 `.agents/skills/`
# 下的每个文件再登记一次，clone 出来的 `.claude/skills` 就是一份真实拷贝（不是 reparse point），此后两份各改各的；
# POSIX 上它是一个链接对象，`git add -A` 会把它带进提交，Windows 同事 clone 出来是一个普通文件。
# 已装项目的 `.claude/skills/` 是真目录、本就该进 git——对它们**不**要求这一条。
# 判定式只有一份、在 `_harness`（`project_skills_dir()` 悬空时也靠它），这里只是同名再导出给 doctor 用。
skills_link_present = _harness_skills_link_present


def _make_dir_link(target: Path, link: Path):
    """`link` → `target` 的目录链接。Windows 走 junction（目标必须已存在，`_winapi` 对不存在的
    目标报 WinError 3）；其他平台走相对路径 symlink（项目整体搬家仍有效）。"""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(os.path.relpath(target, link.parent), link, target_is_directory=True)


def ensure_project_skills_link(project_dir: Path, created_list, skipped_list) -> bool:
    """只补链接：`.agents/skills/` 是目录、而 `.claude/skills` 位置上**什么都没有**（无目录、无文件、
    无链接——`os.path.lexists` 为 False）时建 `.claude/skills → .agents/skills`。建成返回 True，其余一律 False。

    这是 clone 侧的到达路径：链接被 `.gitignore` 忽略，`git clone` 出来的项目只有 `.agents/skills/`，
    CC 不读中立目录、项目 skill 在那一门下整个消失且 doctor 全绿。**纯新增**：位置上已有任何东西
    （真目录 / 文件 / 悬空链接）都不碰，中立目录不在也不碰——那两种各归 `ensure_project_skills_dir`
    与「不建、不删、报出来」。SessionStart 每次会话都调本函数（`session-start-prep.py`），所以它必须
    幂等且窄：不像整个 scaffold 那样重建骨架，只补这一根链接。
    链接建不成（平台 / 权限）只报出来，**不**退回真目录——clone 形态下再建一个空 `.claude/skills/`
    真目录会让它与中立目录从此成为两个不同的目录（`project_skills_conflict` 永久报警）。
    """
    neutral = project_dir / SKILLS_DIR_NEUTRAL
    cc = project_dir / SKILLS_DIR_CC
    if not neutral.is_dir() or skills_cc_form(project_dir) != SKILLS_ABSENT:
        return False
    cc.parent.mkdir(parents=True, exist_ok=True)
    try:
        _make_dir_link(neutral, cc)
    except Exception as e:
        skipped_list.append(f"{cc} (目录链接未建成: {type(e).__name__}: {e}——Claude Code 门看不到 "
                            f"{SKILLS_DIR_NEUTRAL.as_posix()}/ 下的项目 skill；手动建链接：Windows "
                            f"`mklink /J .claude\\skills .agents\\skills`，POSIX `ln -s ../.agents/skills .claude/skills`)")
        return False
    created_list.append(f"{cc} -> {SKILLS_DIR_NEUTRAL.as_posix()} 目录链接（补建）")
    return True


def _link_target_text(link: Path) -> str:
    """链接的读数，给人看：去掉 Windows 的 `\\\\?\\` 前缀。读不到时 `?`。"""
    try:
        t = os.readlink(link)
    except OSError:
        return "?"
    return t[4:] if t.startswith("\\\\?\\") else t


def _unlink_dir_link(link: Path):
    """只解链接本身、不碰目标的原语：Windows junction / 目录 symlink 用 `os.rmdir`（对非空真目录必然失败，
    WinError 145），POSIX symlink 用 `os.unlink`（对目录必然失败）。**不用 `rmtree` / `shutil` 系**。"""
    if os.name == "nt":
        os.rmdir(link)
    else:
        os.unlink(link)


def repoint_dangling_skills_link(project_dir: Path, created_list, skipped_list):
    """`.claude/skills` 是目标不存在的链接、而本项目 `.agents/skills` 是目录（`SKILLS_LINK_TARGET_MISSING`：
    Windows 上项目整体改名 / 剪切后，junction 存的绝对路径指向旧位置）时，把它重指到本项目 `.agents/skills`。
    成功返回原目标的读数（真值），其余一律返回 None。

    **这是写进 hook 的删除动作，范围只有「解这根悬空链接本身」**（用户 2026-09-18 授权）：解之前立即重判一次
    （判定与动作之间形态变了就不动）；解链接只用 `_unlink_dir_link` 的原语；其余形态（真目录 / 文件 / 指向别处 /
    真实源不在）一律不碰。两个会话同时重指：一边撞 `FileNotFoundError`（链接已被对方解掉）/ `FileExistsError`
    （对方已建好）⇒ 重判，已是 `link_ok` 即算成功。
    """
    cc = project_dir / SKILLS_DIR_CC
    neutral = project_dir / SKILLS_DIR_NEUTRAL
    if skills_cc_form(project_dir) != SKILLS_LINK_TARGET_MISSING:
        return None
    import time
    old = _link_target_text(cc)
    last_err = None
    for _ in range(8):
        form = skills_cc_form(project_dir)          # 紧贴动作的重判
        if form == SKILLS_LINK_OK:
            break
        if form == SKILLS_LINK_TARGET_MISSING:
            try:
                _unlink_dir_link(cc)
            except FileNotFoundError:
                pass
            except OSError as e:
                # 并发：对方刚解掉、Windows 上目录项处于「删除挂起」时这里是拒绝访问（实测 WinError 5）——稍等重判
                last_err = e
                time.sleep(0.05)
                continue
        elif form != SKILLS_ABSENT:
            # 别的形态（真目录 / 文件 / 指向别处）：一律不动手。但并发时判定本身不是原子的——对方恰在
            # `lexists` 与链接判定之间解掉链接，会瞬时读成「普通文件」；恰在解析途中解掉，会瞬时读成「指向别处」。
            # 所以只等、再判，不当场放弃；形态真被人改了，8 轮都读到它，最后按它收尾（不动手、不报）。
            time.sleep(0.05)
            continue
        try:
            _make_dir_link(neutral, cc)
        except FileExistsError:
            continue
        except OSError as e:
            last_err = e
            time.sleep(0.05)
            continue
    if skills_cc_form(project_dir) != SKILLS_LINK_OK:
        form = skills_cc_form(project_dir)
        why = f"{type(last_err).__name__}: {last_err}" if last_err else form
        if form == SKILLS_ABSENT:
            skipped_list.append(f"{cc} (悬空链接已解开，但新链接未建成: {why}——手动建：Windows "
                                f"`mklink /J .claude\\skills .agents\\skills`，POSIX `ln -s ../.agents/skills .claude/skills`)")
        elif form == SKILLS_LINK_TARGET_MISSING:
            skipped_list.append(f"{cc} (悬空链接未能解开: {why}——没动它；Windows `rmdir .claude\\skills`、"
                                f"POSIX `rm .claude/skills` 只解链接，解开后重开会话即补建)")
        return None
    created_list.append(f"{cc} -> {SKILLS_DIR_NEUTRAL.as_posix()} 目录链接（重指：原指向 {old}）")
    return old


def ensure_project_skills_dir(project_dir: Path, created_list, skipped_list) -> Path:
    """建项目 skills 的到达路径：`.agents/skills/` 真实源 ＋ `.claude/skills` 链接。返回真实源（绝对路径）。

    **只对两者都不存在的项目建**：`.claude/skills` 或 `.agents/skills` 任一已在（已装项目的真目录、
    用户自建的目录、上次装机留下的链接）都**不建、不删、报出来**——建链接对已存在路径直接失败，
    而删目录撞「删除权不给模型」。两者都在且不是同一目录时另报一句冲突。
    两个例外形态各自单独判、都排在通用「已在」判定之前，因为 `exists()` 对它们的回答会误导：
      - **悬空链接**（`.claude/skills` 是链接、目标 `.agents/skills/` 不在）：`exists()` / `is_symlink()`
        对 junction 都是 False，按「都不在」走新建会撞已存在的链接、再对它 `mkdir(exist_ok=True)` 抛
        `FileExistsError` 中断整个 scaffold。判定用 `skills_link_dangling`：不建、不删、报「悬空」＋出路。
        此时 `.gitignore` 仍忽略 `.claude/skills`、`project_skills_dir()` 仍解析到中立目录——两边都
        **按链接判**（目标随后一旦被建回来，链接即恢复，两边都不用再改）。
      - **只有中立目录**（clone 形态）：`neutral.exists()` 为真但链接不在——补链接，见
        `ensure_project_skills_link`。
    链接建不成（平台不支持 / 权限）时退回 `.claude/skills/` 真目录：CC 照常发现得到，只是没有中立
    到达路径；此时收回本函数刚 `mkdir` 出来的那个空中立目录——留着它会让 `project_skills_dir()`
    从此解析到一个空壳（`rmdir` 只删空目录，删不到任何用户内容）。
    """
    neutral = project_dir / SKILLS_DIR_NEUTRAL
    cc = project_dir / SKILLS_DIR_CC
    conflict = project_skills_conflict(project_dir)
    if conflict:
        skipped_list.append(f"{cc} WARNING: {conflict}")
    if skills_link_dangling(project_dir):
        try:
            target = os.readlink(cc)
        except OSError:
            target = "?"
        skipped_list.append(
            f"{cc} WARNING: 链接悬空——它指向 {target}，而 {SKILLS_DIR_NEUTRAL.as_posix()}/ 此刻不是目录。"
            f"不建、不删；两条出路：把真实源建回来（`mkdir {SKILLS_DIR_NEUTRAL.as_posix()}` 或从 git 恢复它，"
            f"链接随即恢复），或先解掉链接（Windows `rmdir .claude\\skills`、POSIX `rm .claude/skills`，"
            f"都只解链接不碰目标）再重跑让本步重建")
        return neutral
    if ensure_project_skills_link(project_dir, created_list, skipped_list):
        return neutral
    if neutral.exists() or cc.exists() or cc.is_symlink():
        skipped_list.append(str(project_dir / project_skills_dir(project_dir)))
        return project_dir / project_skills_dir(project_dir)
    neutral.mkdir(parents=True, exist_ok=True)
    cc.parent.mkdir(parents=True, exist_ok=True)
    try:
        _make_dir_link(neutral, cc)
    except Exception as e:
        try:
            neutral.rmdir()
        except OSError:
            pass
        cc.mkdir(parents=True, exist_ok=True)
        skipped_list.append(f"{cc} (目录链接未建成: {type(e).__name__}: {e}——退回真目录 "
                            f"{SKILLS_DIR_CC.as_posix()}/，无中立到达路径)")
        created_list.append(str(cc))
        return cc
    created_list.append(f"{neutral} (+ {SKILLS_DIR_CC.as_posix()} -> {SKILLS_DIR_NEUTRAL.as_posix()} 目录链接)")
    return neutral


# Git 策略要求 .gitignore 必须包含的条目；_ensure_gitignore 在 .gitignore 已存在时做缺失扫描。
# 详见 plugins/core/reference/project-architecture.md §Git 策略
#
# 这是**检测用**的语义清单，故意写成目录形态——gitignore_covers 会认 `dir/`、`/dir`、
# `dir/*` 等一切等价写法。要往 .gitignore 里**写**的字面内容见 gitignore_managed_lines，
# 两者不可互换：检测要宽（认等价形态），写入要精确（sidecar 例外必须成对出现）。
GITIGNORE_REQUIRED_ENTRIES_FIXED = [
    ".claude/settings.local.json",
    "logs/",
    "tmp/",
    ".tmp/",
]


def gitignore_required_entries(project_dir: Path):
    """检测用清单：固定四项 + 状态目录（问 `_state_io` 现算）
    + **链接形态时**的 `.claude/skills`（`SKILLS_LINK_IGNORE_LINE`，无尾斜杠）。

    状态目录那项不写死：目录位置只有 `_state_io` 一个事实源，这里写一份就是第二个源，挪目录那天
    漏改的话每次都被判「缺条目」，`_ensure_gitignore` 每跑一次就往文件尾追加一个管不着任何东西的
    managed block。`.claude/skills` 那项按项目现算（`skills_link_present`）：它是真目录的已装项目本就
    该让它进 git，一律要求会让每个已装项目凭空红一条。
    """
    return gitignore_required_entries_for(state_dir_rel(project_dir), skills_link_present(project_dir))


def gitignore_required_entries_for(state_rel: str, skills_link: bool):
    """检测用清单的纯函数形态：按**给定的**布局求值，与项目此刻在用哪个目录无关。

    迁移工具要的是「搬完之后该有哪些条目」——那时状态目录还没搬，按项目现算只会得出旧路径。
    """
    entries = GITIGNORE_REQUIRED_ENTRIES_FIXED + [state_rel + "/"]
    if skills_link:
        entries.append(SKILLS_LINK_IGNORE_LINE)
    return entries


# 项目 skills 链接的忽略行：**无尾斜杠**。git 官方 gitignore 语义：`foo/` 只匹配目录 `foo` 与其下路径，
# **不匹配**名为 `foo` 的普通文件或符号链接——POSIX 上 `.claude/skills` 是符号链接，带尾斜杠的写法（以及
# `/*`、`/**` 这类只管其下路径的写法）盖不住它，`git add -A` 会把链接对象带进提交；Windows 上 git 把
# junction 当目录，两种写法都生效。所以「这条算不算覆盖」只认两平台都生效的写法（`skills_link_ignore_state`），
# 其余必需条目的等价写法判定（`gitignore_covers`）不受影响。
SKILLS_LINK_IGNORE_LINE = SKILLS_DIR_CC.as_posix()
SKILLS_LINK_IGNORE_OK = (SKILLS_LINK_IGNORE_LINE, "/" + SKILLS_LINK_IGNORE_LINE)
SKILLS_LINK_IGNORE_LEGACY = tuple(p + SKILLS_LINK_IGNORE_LINE + s
                                  for p in ("", "/") for s in ("/", "/*", "/**"))


def skills_link_ignore_state(text):
    """`.gitignore` 对项目 skills 链接的忽略写法：`"ok"`（有两平台都生效的一行）/ `"legacy"`（没有，但有只在
    Windows junction 上生效的旧写法——`.claude/skills/` 等）/ `"missing"`（都没有）。

    非注释、非 `!` 的行，去首尾空白后逐行整行比较。祖先宽规则（`.claude/`、`.claude/*`）实际也能盖住链接，
    但**不认**：这里只认框架自己写的那一行，认宽规则就得再解一遍 gitignore 语义。代价是这类项目会被追加
    一次 managed block（一次性，追加后即 ok）。判定只有这一份——scaffold `_ensure_gitignore`、迁移工具
    `gitignore_bytes`、doctor `check_gitignore` 都经 `gitignore_covers` 或直接调本函数。
    """
    legacy = False
    for raw in text.splitlines():
        s = _strip_line(raw)
        if not s or s.startswith("#") or s.startswith("!"):
            continue
        if s in SKILLS_LINK_IGNORE_OK:
            return "ok"
        if s in SKILLS_LINK_IGNORE_LEGACY:
            legacy = True
    return "legacy" if legacy else "missing"

# managed block 实际写入的字面行。与上面的差别只在状态目录：必须写成
# `/*` + `!` 成对形态，否则 memory-index.json 这个 sidecar 会被连坐忽略——
# git 不会重新纳入被排除父目录下的文件，父目录整个被忽略时 `!` 静默失效。
# 曾经两处共用一个常量，结果「接入已有项目」路径追加的是整目录形态，sidecar 被忽略、
# 而缺失扫描又判「条目齐全」，于是永不自我纠正、用户全程无感（沙盒实证）。
def gitignore_managed_lines(project_dir: Path):
    """写入用清单（同样按项目现算，成对形态见上方注释；`.claude/skills` 只在链接形态时写）。"""
    return managed_lines_for(state_dir_rel(project_dir), skills_link_present(project_dir))


def managed_lines_for(state_rel: str, skills_link: bool):
    """写入用清单的纯函数形态（唯一实现）：按**给定的**布局求值。迁移工具按搬完之后的布局调它。"""
    state = state_rel
    lines = [
        ".claude/settings.local.json",
        f"{state}/*",
        f"!{state}/memory-index.json",
        "logs/",
        "tmp/",
        ".tmp/",
    ]
    if skills_link:
        lines.append(SKILLS_LINK_IGNORE_LINE)
    return lines


def gitignore_covers(text, entry):
    """.gitignore 里是否真的有这条规则。

    不能用子串匹配（`entry in text`）：`.tmp/` 存在就会让 `tmp/` 判为已有——两者恰好
    都在必需清单里，于是只写了 `.tmp/` 的项目被判齐全，`tmp/` 里的临时产物照样进 git；
    注释掉的 `# tmp/` 与否定规则 `!tmp/` 同样会被算数。
    按行取有效规则，并接受 `tmp` / `tmp/` / `/tmp` / `/tmp/` 这几种等价写法。

    `tmp/*` 形态同样算数：它忽略目录下全部内容，与 `tmp/` 的意图一致，差别只在
    父目录本身未被排除——而这正是要放行个别文件（`dir/*` + `!dir/keep.json`）时的
    唯一可行写法，因为 git 不会重新纳入被排除父目录下的文件。不认它会连环出事：
    doctor 对这类项目永久报「缺必需条目」，`_ensure_gitignore` 重跑时再把整目录
    忽略行追加回来，`!` 例外就被 git 静默无视了。
    本函数只回答「必需规则在不在」，不校验 `!` 例外行写得对不对——那由 validate.py
    的 gitignore-template 字面闸负责。

    **项目 skills 链接那一条单独判**（`skills_link_ignore_state` 为 `"ok"` 才算覆盖）：上面的等价写法对
    目录成立，对符号链接不成立（见 `SKILLS_LINK_IGNORE_LINE` 的注释），照上面的规则会在 POSIX 上假绿。
    """
    if entry.rstrip("/") == SKILLS_LINK_IGNORE_LINE:
        return skills_link_ignore_state(text) == "ok"
    core = entry.rstrip("/")
    variants = {
        entry,
        core,
        core + "/",
        "/" + core,
        "/" + core + "/",
        core + "/*",
        "/" + core + "/*",
    }
    for raw in text.splitlines():
        s = _strip_line(raw)          # 带 BOM 的首行也要能匹配
        if not s or s.startswith("#") or s.startswith("!"):
            continue
        if s in variants:
            return True
    return False


# marker 有两个字面量，**检测认两个、生成只用新的**。
#
# 历史：旧串写死了已退役安装器的脚本文件名（原文见下方 LEGACY 常量）——它却会被写进
# 每个用户项目的 .gitignore 并提交进对方 git 历史，成为一条指向不存在文件的永久死引用。
# 当初为幂等性把它冻结了（改串会让 `in text` 检测失配 → 给老用户追加出第二个 managed
# block），但冻结只保住了幂等，没保住正确性。
#
# 解法：检测时两个都认（老项目的旧块照样被识别，不会重复追加），生成时只用新串。
# 新增第三个变体前先想清楚——LEGACY 元组只增不减，删任何一个都会让对应年代的项目被追加重复块。
GITIGNORE_MANAGED_BEGIN = "# === Workframe managed (do not edit between markers) ==="
GITIGNORE_MANAGED_BEGIN_LEGACY = (
    "# === Workframe managed (auto-added by tools/install.py; "
    "do not edit between markers) ===",
)
GITIGNORE_MANAGED_END = "# === End Workframe managed ==="


def _has_managed_marker(text: str) -> bool:
    """认出任一代的 BEGIN marker——判「这份 .gitignore 已被 workframe 接管过」。"""
    return GITIGNORE_MANAGED_BEGIN in text or any(
        legacy in text for legacy in GITIGNORE_MANAGED_BEGIN_LEGACY
    )


def state_dir_rel(project_dir: Path) -> str:
    """项目实际在用的状态目录，项目根相对 POSIX 路径。唯一判定在 `_state_io`。"""
    return runtime_rel(project_dir, "state")


def sidecar_rel(project_dir: Path) -> str:
    """记忆 sidecar 的项目根相对路径——`git check-ignore` 要的就是相对路径。"""
    return state_dir_rel(project_dir) + "/memory-index.json"


def _strip_line(raw: str) -> str:
    """按行取有效内容。`lstrip("\\ufeff")` 不能省——Windows 上手工编辑过的 .gitignore
    常带 UTF-8 BOM，BOM 粘在首行会让 marker 比较与规则匹配全部失配：旧 managed block
    识别不出来 → 存量升级静默不执行（实测 upgraded=0、成对写法 0 行）。"""
    return raw.lstrip("﻿").strip()


def _git_says_sidecar_ignored(project_dir: Path):
    """问 git：sidecar 到底被不被忽略、被哪一行忽略。

    **git 才是最终裁决者**。自己枚举规则形态永远不全——状态目录本身、它的父目录、
    它的末段、各种 `**/` 前缀写法都能连坐吞掉 sidecar，穷举是打不完的地鼠。

    返回：`(行号, 规则原文)` 表示被该行忽略；`False` 表示确认未被忽略；
    `None` 表示 git 不可用 / 不在仓内，交给字面 fallback。
    """
    try:
        r = subprocess.run(
            ["git", "check-ignore", "-v", sidecar_rel(project_dir)],
            cwd=str(project_dir), capture_output=True, text=True, timeout=5,
            encoding="utf-8", errors="replace")
    except Exception:
        return None
    if r.returncode == 1:
        return False                      # 明确未被忽略
    if r.returncode != 0 or not r.stdout.strip():
        return None                       # 128=不在仓内，或输出异常 → fallback
    # 输出格式：<source>:<line>:<pattern>\t<pathname>
    head = r.stdout.strip().splitlines()[0].split("\t", 1)[0]
    parts = head.rsplit(":", 2)
    if len(parts) != 3:
        return None
    pattern = parts[2].strip()
    if pattern.startswith("!"):
        return False                      # 命中的是例外行 → 实际未被忽略
    try:
        return (int(parts[1]), pattern)
    except ValueError:
        return None


def find_sidecar_shadowing_rule(text: str, project_dir: Path = None):
    """找出 managed block **之外**、会连坐吞掉 sidecar 的整目录忽略规则。

    第四种失败形态（前三种见 gitignore_covers / gitignore_managed_lines /
    _upgrade_managed_sidecar_rule 的注释）：用户在接入前自己往 .gitignore 加过
    一条整目录忽略行（形如 `<状态目录>/`）。此时——

      - 缺失扫描认为该条目「已覆盖」，不报缺；
      - managed block 照样按正确的 `/*` + `!` 成对形态追加；
      - **但 git 里父目录已被前面那条规则排除，后面的 `!` 例外无效**，sidecar 仍被忽略；
      - doctor 的 gitignore 项报 ok。全程静默。

    前三种是「写错了」，这种是「**写对了但被前面的规则压制**」，更难发现。

    **只报告不修改**：marker 之外是用户的地盘。而且用户可能是有意忽略整个目录的
    （记忆含敏感内容时这是正当选择），框架没有判据替他决定，只能把冲突摆到他面前。

    返回 (行号, 原文) 或 None。
    """
    begins = (GITIGNORE_MANAGED_BEGIN,) + GITIGNORE_MANAGED_BEGIN_LEGACY
    lines = text.splitlines()

    def _in_managed_block(lineno):
        inside = False
        for i, raw in enumerate(lines, start=1):
            s = _strip_line(raw)
            if s in begins:
                inside = True
            elif s == GITIGNORE_MANAGED_END:
                inside = False
            if i == lineno:
                return inside
        return False

    # 主判据：git 说了算（任何形态的规则都逃不过它）
    if project_dir is not None:
        verdict = _git_says_sidecar_ignored(Path(project_dir))
        if verdict is False:
            return None
        if verdict:
            lineno, pattern = verdict
            # 命中行若在 managed block 内，说明是 block 自身写错了（不该发生，
            # 因为 block 里是 `/*` + `!`）——那属另一类问题，不报为 shadowing
            return None if _in_managed_block(lineno) else (lineno, pattern)

    # fallback（无 git / 不在仓内）：字面扫描。**覆盖不全是已知的**——
    # 这里只列常见形态，穷举不可能，所以能问 git 时一律以 git 为准。
    core = state_dir_rel(project_dir) if project_dir else state_dir_rel(Path("."))
    parent, _, leaf = core.rpartition("/")
    shadowing = set()
    for cand in (core, parent, leaf):          # 整路径 / 父目录 / 末段各三种写法
        if not cand:
            continue
        shadowing |= {cand, cand + "/", "/" + cand, "/" + cand + "/",
                      "**/" + cand, "**/" + cand + "/"}
    in_block = False
    for i, raw in enumerate(lines, start=1):
        s = _strip_line(raw)
        if s in begins:
            in_block = True
            continue
        if s == GITIGNORE_MANAGED_END:
            in_block = False
            continue
        if in_block or not s or s.startswith("#") or s.startswith("!"):
            continue
        if s in shadowing:          # 注意：`dir/*` 不在此集合内——它不排除父目录，无害
            return (i, s)
    return None


def upgrade_sidecar_rule_text(text: str, state_rel: str):
    """把 managed block 里的旧整目录写法 `<状态目录>/` 就地升级为成对形态。返回 `(新文本, 改没改)`。
    **纯函数，不读写磁盘**——落盘入口是 `_upgrade_managed_sidecar_rule`，只读预演直接调本函数。

    为什么必须自动升级：装过旧版的存量项目，其 managed block 里是整目录忽略行。
    新版 `gitignore_covers` 认这种形态为「条目已覆盖」→ 缺失扫描判齐全 → 走 skipped
    分支 → **文件纹丝不动**，sidecar 永远进不了 git，且全程零提示（沙盒实证）。
    不升级的话，「记忆 sidecar 进 git」这条对所有老项目等于没生效。

    只动 marker 之间——那是框架声明管理的区域（block 头就写着 do not edit between
    markers），块外一律不碰。幂等：已是新写法直接返回。
    """
    begins = (GITIGNORE_MANAGED_BEGIN,) + GITIGNORE_MANAGED_BEGIN_LEGACY
    lines = text.splitlines()
    begin_idx = end_idx = None
    for i, raw in enumerate(lines):
        s = _strip_line(raw)          # BOM 会让首行 marker 失配 → 存量升级静默不执行
        if begin_idx is None and s in begins:
            begin_idx = i
        elif begin_idx is not None and s == GITIGNORE_MANAGED_END:
            end_idx = i
            break
    if begin_idx is None or end_idx is None:
        return text, False

    block = lines[begin_idx + 1:end_idx]
    old_rule = f"{state_rel}/"
    new_rule = f"{state_rel}/*"
    exception = f"!{state_rel}/memory-index.json"
    stripped = [_strip_line(ln) for ln in block]
    if new_rule in stripped and exception in stripped:
        return text, False          # 已是新写法
    if old_rule not in stripped:
        return text, False          # 块内没有这条规则，不属本函数职责

    new_block = []
    for ln in block:
        if _strip_line(ln) == old_rule:
            new_block.append(new_rule)
            new_block.append(exception)
        elif _strip_line(ln) == exception:
            continue                # 去重：孤立的例外行由上面成对重建
        else:
            new_block.append(ln)
    new_lines = lines[:begin_idx + 1] + new_block + lines[end_idx:]
    new_text = "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")
    return new_text, True


def _upgrade_managed_sidecar_rule(gi: Path, text: str, created_list):
    """`upgrade_sidecar_rule_text` 的落盘入口（scaffold 路径）。返回 `(新文本, 改没改)`。"""
    state = state_dir_rel(gi.parent)
    new_text, changed = upgrade_sidecar_rule_text(text, state)
    if not changed:
        return text, False
    gi.write_text(new_text, encoding="utf-8", newline="")
    created_list.append(
        f"{gi} (upgraded managed block: {state}/ -> {state}/* + !{state}/memory-index.json; "
        f"记忆 sidecar 此前被连坐忽略)"
    )
    return new_text, True


def upgrade_skills_ignore_bytes(raw: bytes):
    """把 managed block 里项目 skills 链接的旧忽略写法（`SKILLS_LINK_IGNORE_LEGACY`，如 `.claude/skills/`）就地改成
    `SKILLS_LINK_IGNORE_LINE`。返回新字节；无可升级时返回 None。纯函数，不读写磁盘。

    形状照 `_upgrade_managed_sidecar_rule`：只动 marker 之间（block 头写着 do not edit between markers，那是
    框架声明管理的区域），块外的旧写法一律不碰——块外缺的由缺失扫描照常处理。幂等：块里已无旧写法即返回 None。
    **按字节保留原样**：BOM、每一行自己的行尾（CRLF 文件照 CRLF）都不变，被替换的只有旧写法那一行的内容——
    `_ensure_gitignore` 的其余写法按通用换行读写、会把 CRLF 整份改成 LF，这里不走那条。
    """
    bom = b"\xef\xbb\xbf"
    has_bom = raw.startswith(bom)
    try:
        text = raw[len(bom):].decode("utf-8") if has_bom else raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    lines = text.splitlines(keepends=True)
    begins = (GITIGNORE_MANAGED_BEGIN,) + GITIGNORE_MANAGED_BEGIN_LEGACY
    inside, changed, out = False, False, []
    for ln in lines:
        body = ln.rstrip("\r\n")
        eol = ln[len(body):]
        s = _strip_line(body)
        if not inside and s in begins:
            inside = True
        elif inside and s == GITIGNORE_MANAGED_END:
            inside = False
        elif inside and s in SKILLS_LINK_IGNORE_LEGACY:
            out.append(SKILLS_LINK_IGNORE_LINE + eol)
            changed = True
            continue
        out.append(ln)
    if not changed:
        return None
    return (bom if has_bom else b"") + "".join(out).encode("utf-8")


def _upgrade_managed_skills_rule(gi: Path, created_list) -> bool:
    """`upgrade_skills_ignore_bytes` 的落盘入口（scaffold 路径）。改了返回 True。"""
    raw = gi.read_bytes()
    new = upgrade_skills_ignore_bytes(raw)
    if new is None:
        return False
    gi.write_bytes(new)
    created_list.append(
        f"{gi} (upgraded managed block: {SKILLS_LINK_IGNORE_LINE}/ -> {SKILLS_LINK_IGNORE_LINE}; "
        f"带尾斜杠的写法只在 Windows junction 上生效，macOS / Linux 上链接会进 git)"
    )
    return True


def _ensure_gitignore(project_dir: Path, created_list, skipped_list):
    """确保项目 .gitignore 含 workframe Git 策略要求的关键条目。

    三种场景：
    1. **不存在** → 从 `templates/gitignore-template` 复制完整 gitignore
    2. **存在且已含全部必需条目** → 仅记录到 skipped（无操作）
    3. **存在但缺关键条目** → 在文件**末尾**非破坏性追加 Workframe managed block
       （带明确 begin/end marker；若 marker 已存在但条目仍缺，视作用户手工编辑过 marker
       内容，不破坏，改为报告供用户检查）
    """
    gi = project_dir / ".gitignore"
    template = SCAFFOLD_TEMPLATES_DIR / "gitignore-template"

    # Case 1: 不存在 → 整份从模板复制，**然后照常走一遍缺失扫描**
    #
    # 为什么复制完还要扫：模板里的状态目录是**手写的出厂形态**，缺失扫描的清单问 `_state_io`
    # 现算——两处理应一致，但分属两个事实源。漂开时模板那几行管不着真正的状态目录，运行态会
    # 直接漏进 git，且全程无提示；扫一遍就补上。一致时扫描判定「条目齐全」，走 Case 2 的无操作分支。
    just_created = False
    if not gi.exists():
        if not template.exists():
            skipped_list.append(f"{gi} (missing template: {template})")  # 前缀须与主流程识别一致
            return
        text = template.read_text(encoding="utf-8")
        # 模板里的 `.claude/skills` 忽略行只对链接形态成立（新建项目：真实源在 `.agents/skills/`）。
        # 真目录形态（已装 / 手工接入的项目）的 skills 本就该进 git——留着这一行会让此后新增的
        # skill 文件静默不进 git，而 doctor 的缺失扫描看不出「多了一条」。
        # **按整行相等剥**，不按子串：新写法 `.claude/skills` 是旧写法的前缀，也是注释里的常见字面。
        if not skills_link_present(project_dir):
            kept, dropped = [], False
            for ln in text.splitlines(keepends=True):
                if not dropped and ln.strip() == SKILLS_LINK_IGNORE_LINE:
                    dropped = True
                    continue
                kept.append(ln)
            text = "".join(kept)
        gi.write_text(text, encoding="utf-8", newline="")
        created_list.append(str(gi))
        just_created = True

    # Case 2 / 3: 已存在 → 先升级旧写法，再扫描必需条目
    # 项目 skills 链接的旧忽略写法先按字节就地升级（保留 BOM 与行尾），再走按文本的 sidecar 升级
    upgraded_skills = skills_link_present(project_dir) and _upgrade_managed_skills_rule(gi, created_list)
    text = gi.read_text(encoding="utf-8")
    text, upgraded = _upgrade_managed_sidecar_rule(gi, text, created_list)
    upgraded = upgraded or upgraded_skills

    # marker 外的整目录规则会压制 managed block 里的 `!` 例外——只报告不代改
    shadow = find_sidecar_shadowing_rule(text, project_dir)
    if shadow:
        skipped_list.append(
            f"{gi} WARNING: 第 {shadow[0]} 行 `{shadow[1]}` 在 Workframe managed block 之外，"
            f"会连坐忽略 {sidecar_rel(project_dir)}（记忆 sidecar）。"
            f"git 不会重新纳入被排除父目录下的文件，managed block 里的 `!` 例外对它无效。"
            f"要让 sidecar 进 git，请把该行改成 `{state_dir_rel(project_dir)}/*`；"
            f"若你有意忽略整个目录（如记忆含敏感内容），忽略本条提示即可"
        )
    missing = [
        entry for entry in gitignore_required_entries(project_dir)
        if not gitignore_covers(text, entry)
    ]
    if not missing:
        # 刚升级过 / 刚从模板建出来就不再记 skipped——同一文件既「已建/已改」又「跳过」
        # 会让调用方读不懂
        if not upgraded and not just_created:
            skipped_list.append(str(gi))
        return

    # Case 3a: 缺关键条目 + marker 已存在（用户改动过 marker 内）→ 不破坏，报告
    if _has_managed_marker(text):
        skipped_list.append(
            f"{gi} EXISTS with Workframe managed marker but missing entries: {missing} — "
            f"please re-add inside the managed block (reference: {template})"
        )
        return

    # Case 3b: 无 marker → 非破坏性追加 managed block 到文件末尾
    # 写 GITIGNORE_MANAGED_LINES 而非 GITIGNORE_REQUIRED_ENTRIES——后者是检测用的语义
    # 清单，直接拿来写会让 sidecar 被连坐忽略（见该常量注释）
    managed = gitignore_managed_lines(project_dir)
    block_lines = [
        "",  # 与上文留一空行
        GITIGNORE_MANAGED_BEGIN,
        *managed,
        GITIGNORE_MANAGED_END,
        "",  # 末尾换行
    ]
    if not text.endswith("\n"):
        text += "\n"
    new_text = text + "\n".join(block_lines)
    gi.write_text(new_text, encoding="utf-8", newline="")
    created_list.append(
        f"{gi} (appended Workframe managed block with required entries: "
        f"{managed})"
    )


SETUP_STATE_SCHEMA = "workframe.setup-state.v1"


def mark_setup_step(project_dir: Path, step: str, status: str = "ok", detail=None,
                    only_if_present: bool = False):
    """在运行态状态目录的 `setup-state.json` 里**增量**记一步。

    为什么由脚本写第一步、且必须增量：断点标记是**为中断准备的**，而早期设计把它放在
    launcher 全流程的最后一步一次性写——流程真在中途断了（CLI 卡住 / 会话崩 / 用户中止），
    就根本走不到那一步，文件压根不存在。为中断设计的机制反过来要求流程别中断，自我否定。
    现在改成：scaffold 一成功就落 `scaffold: ok`（代码保证，不靠模型记得），
    后续每步由 launcher 紧跟着追加。中途断在哪，文件里就停在哪。

    `only_if_present=True`：**已有一份可用记录才记这一步**，否则原样返回 `None` 不落任何字节。
    「可用」= 文件在 ＋ 解析出 dict ＋ `steps` 里已经有至少一步。最后一条不是多余的：
    `steps` 为空的文件与「没有记录」信息量相同（`mark_setup_step` 自己至少写一步，空 steps
    只可能来自手改或截断），往里补记一步得到的**正是本开关要消灭的那个形态**。

    给「每次会话启动都补记一次」那类调用方用（`session-start-prep.ensure_agents_md_present`）——
    那条路径在**整个状态目录不进 git** 的前提下会踩两个坑，两个都由本开关堵住：
      - **clone 出来的项目本就没有这份文件**，无条件记步会凭空造出一份只含该步的记录，
        而 `check_setup_state` 把它读成「初始化只走完 1/3 步」⇒ 恒 error 且永不自愈；
        文件不存在时 doctor 本来报 info 放行，是这次记步把合法状态改成了错误状态。
      - **文件在、但读不出 dict（损坏 / 零字节 / 带 BOM / 顶层不是对象）时，下面那段是「重建」语义**
        ——对带 BOM 的健康记录来说，一次会话启动就把 `scaffold` / `subscribe` 抹成了一步。
        默认路径保持重建（派生状态，scaffold 首步靠它落盘）；补记路径不重建。

    **判据必须落在本函数里，不能写在调用点**：调用点判完还要由本函数再读一次，两次读取的解码
    口径只要不同（实测过 `utf-8-sig` 守卫 ＋ `utf-8` 写入）就是守卫放行、写入方照样重建，
    守卫形同虚设。这里的「能不能用」与「拿它去写」共用同一次 `json.loads`。

    默认 `False` ⇒ scaffold 与 launcher 的记步行为一字不变。
    """
    f = state_dir_of(project_dir) / "setup-state.json"
    data = {"__schema__": SETUP_STATE_SCHEMA, "steps": {}}
    usable = False  # `only_if_present` 的判据，不是 `f.exists()`——见 docstring
    if f.exists():
        try:
            existing = json.loads(f.read_text(encoding="utf-8"))
            # **顶层与 `steps` 都得是 dict 才算「读出了一份记录」**。只判顶层的话，
            # `{"steps": [...]}` / `{"steps": null}` 会被接着当成可合并的记录，而下面那句
            # `data["steps"].get(step)` 当场抛 AttributeError——**默认路径本该「读不出就重建」，
            # 却偏偏在这一格崩掉**，崩在 launcher 重跑或 scaffold 落第一步的时候。
            # 与 doctor 读侧（`check_setup_state` 的两格结构兜底）同批取齐：两边对「这份文件
            # 还能不能用」必须给同一个答案，否则写入方重建了、读取方还在按原样报。
            if isinstance(existing, dict) and isinstance(existing.get("steps", {}), dict):
                data = existing
                data.setdefault("__schema__", SETUP_STATE_SCHEMA)
                data.setdefault("steps", {})
                usable = bool(data["steps"])   # 空 steps 等价于「没有记录」，见 docstring
        except Exception:
            pass  # 损坏就重建，这份文件是派生状态，不是用户数据
    if only_if_present and not usable:
        return None
    value = status if detail is None else f"{status}: {detail}"
    # **同值早退，不刷 `updated_at`**：`ensure_agents_md_present()` 每次会话启动都调本函数
    # 确认那一步在位，无条件重写会把 `updated_at` 变成「上次会话启动时间」——而 doctor 的
    # 文案写的是「初始化 N 步全部完成于 <updated_at>」，那行会每会话变一次、从装机日一路漂到今天。
    # 本字段的语义是**最后一次真正落步的时间**，没落步就不该动它。
    # 文件不存在或损坏时 `steps` 是空的，`.get()` 恒不等于 value，照常写——不必另判 `f.exists()`。
    if data["steps"].get(step) == value:
        return f
    data["steps"][step] = value
    data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="")
    return f


def mark_pending_work(project_dir: Path, batches, note: str = "", at_session: int = 0):
    """记下「初始化第二幕」的剩余批次，驱动重启后的主动接力（2026-08-11 取代 pending_intake）。

    batches: [{"name": "<批次名>", "files": ["<项目根相对路径>", ...],
               "target_skill": "requirement-archiving" | "migrate-to-modules" | "code-to-doc",
               "pace": "relay" | "paused" | "undecided"}]

    pace 语义（决定重启后的打扰强度，由用户在节奏闸拍板）：
      relay     = 用户拍了「重启后做」→ 首会话强接力，此后每会话一行轻提示（嫌烦可改 paused）
      paused    = 用户拍了「暂不做」→ 会话零打扰，仅 doctor / 看板可见，随时可恢复
      undecided = 用户没做处置决策 → 头 N 会话软提醒 + 文件搬走自清除（原 pending_intake 语义）

    写 setup-state.json 的**同级键**而非 `steps`——`check_setup_state` 把任何非 ok 的 step
    当失败报 error，而第二幕是流程态不是故障。批次完成由模型删除对应条目，
    全部销账后删除整个 `pending_work` 键（doctor 的 init_completeness 随之转 ok）。
    """
    f = state_dir_of(project_dir) / "setup-state.json"
    data = {"__schema__": SETUP_STATE_SCHEMA, "steps": {}}
    if f.exists():
        try:
            existing = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                data = existing
                data.setdefault("__schema__", SETUP_STATE_SCHEMA)
                data.setdefault("steps", {})
        except Exception:
            pass
    norm = []
    for b in batches:
        norm.append({
            "name": str(b.get("name", "")),
            "files": [str(p).replace("\\", "/") for p in (b.get("files") or [])],
            "target_skill": str(b.get("target_skill", "")),
            "pace": b.get("pace") if b.get("pace") in ("relay", "paused", "undecided") else "undecided",
        })
    data["pending_work"] = {
        "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "note": note,
        "recorded_at_session": at_session,
        "batches": norm,
    }
    data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="")
    return f


# 无对话参数时（显式重跑 / SessionStart 补建）AGENTS.md 项目段的兜底文本。前两条与模板
# 改版前那两句静态句同值——已装项目补建出来的内容与改版前一致，只是多了三段项目段。
# doctor `agents_md_goal_state` 据 GOAL 判「这个项目是什么」只剩占位（引用本常量，不抄字面）；BLANK 在带参新建而
# 判据留空时也会写，不是占位标记。
AGENTS_MD_FALLBACK_GOAL = "（待填）"
AGENTS_MD_FALLBACK_BLANK = "（暂无）"


def _configured_role_profile(project_dir: Path) -> str:
    """无对话参数时路由偏好取哪一档：读 `.workframe-config.json` 的 `role_profile`。

    **读不到**（文件不在 / 解析失败 / 不是对象 / 没这个键 / 不是非空字符串）→ 默认档。
    **读到了但 catalog 里没有**不在这里兜——交给 `extract_role_profile_routing` 当场抛
    ParamError：静默换成默认档会把另一档的路由写进一个只写一次的文件，改好配置之后也
    不会重渲染（catalog「渲染契约」段写的就是「找不到直接报错，不静默降级」）。
    """
    try:
        cfg = json.loads((project_dir / ".workframe-config.json").read_text(encoding="utf-8"))
    except Exception:
        return PARAM_DEFAULTS["role_profile"]
    val = cfg.get("role_profile") if isinstance(cfg, dict) else None
    return val if isinstance(val, str) and val.strip() else PARAM_DEFAULTS["role_profile"]


def agents_md_mapping(project_dir: Path, params=None) -> dict:
    """`agents-md-template.md` 六个占位符的取值——**键集合即该模板的占位符集合**。

    有 params（新建路径，`main()` 经 `ensure_project_scaffold` 传进来）用对话参数；没有时用
    兜底文本，路由偏好按 `_configured_role_profile` 取档。自由文本参数为空白时一律落「（暂无）」
    这类可见占位——模板注释要求段不许删，空段让下一个人不知道这里本该有东西。
    """
    def text(value, fallback):
        s = "" if value is None else str(value)
        return s if s.strip() else fallback

    if params:
        src, role_profile = params, params["role_profile"]
    else:
        src, role_profile = {}, _configured_role_profile(project_dir)
    return {
        "PROJECT_ONE_LINE_GOAL": text(src.get("one_line_goal"), AGENTS_MD_FALLBACK_GOAL),
        "PROJECT_BUSINESS_CONTEXT": text(src.get("business_context"), AGENTS_MD_FALLBACK_GOAL),
        "PROJECT_SPECIFIC_CONSTRAINTS": text(src.get("project_specific_constraints"),
                                             AGENTS_MD_FALLBACK_BLANK),
        "PROJECT_LEVEL_ROLES": text(src.get("project_level_roles"),
                                    PARAM_DEFAULTS["project_level_roles"]),
        "ROLE_PROFILE_ROUTING": extract_role_profile_routing(role_profile),
        "PROJECT_BUSINESS_DIRECTORIES": text(src.get("business_directories"),
                                             PARAM_DEFAULTS["business_directories"]),
    }


def ensure_agents_md(project_dir: Path, created_list=None, skipped_list=None, params=None):
    """写 `AGENTS.md` —— 两扇门共用的项目自有内容落点。**它的唯一写入点，三条路径都走这里。**

    三条路径：新建（`main()` 经 `ensure_project_scaffold(project_dir, params)` 传入对话参数）、
    显式重跑（无参数，填兜底文本）、SessionStart 侧的会话启动准备（已装项目补建，无参数）。
    **`render_project_docs` 里不许再加第二个写入点**：两个写入点时先跑的那个会写出一份文件、
    后跑的见文件已存在即跳过，对话参数就此被静默丢弃而退出码仍是 0。

    **为什么 SessionStart 只调本函数、不调整个 `ensure_project_scaffold`**：后者会重建一整
    套骨架，对用户有意删掉的东西是破坏性的；本函数只碰一个文件，且**只在它不存在时写**。
    **先判存在、再算 mapping**：SessionStart 每次会话都调本函数，文件在就不该去读 catalog。

    纯新增动作，不触碰任何删除；已存在一律跳过不覆盖。渲染失败（配置里的 `role_profile`
    在 catalog 里找不到）抛 ParamError，不写半成品。
    """
    created = [] if created_list is None else created_list
    skipped = [] if skipped_list is None else skipped_list
    dst = project_dir / "AGENTS.md"
    if dst.exists():
        skipped.append(str(dst))
        return False
    return _render_if_absent(SCAFFOLD_TEMPLATES_DIR / "agents-md-template.md", dst,
                             agents_md_mapping(project_dir, params), created, skipped)


# ---------------------------------------------------------------- 「本次会碰哪些文件」的唯一清单
#
# `ensure_project_scaffold` / `render_project_docs` 真写时按它逐项落盘，`--print-plan` 只读预演
# 按它逐项算「新建还是已存在」。**两处共用同一份清单，不许各写一份**——清单漂开的后果是确认页
# 给用户看的动作清单与实际落盘的文件对不上，而那一页正是用户在这条链路上唯一的事前授权点。


def scaffold_targets(project_dir: Path):
    """骨架层要写的每一项：(kind, dst, payload) 的有序清单，顺序即落盘顺序。

    kind 三种，各对应一个 `*_if_absent` 写入原语：
      - "copy"   payload = 模板绝对路径（`_copy_if_absent`）
      - "render" payload = (模板绝对路径, 占位符 mapping)（`_render_if_absent`）
      - "write"  payload = 要写的文本（`_write_if_absent`）

    **本函数不碰盘**（只拼路径、读时钟），所以只读预演可以直接调它。
    **不含**三项，它们各有自己的分支逻辑、由调用方分别处置（预演侧的对应处置见 `plan_entries`）：
    `AGENTS.md`（唯一写入点是 `ensure_agents_md`，SessionStart 侧也调它）、`.gitignore`
    （`_ensure_gitignore` 有整份复制 / 追加 managed block / 升级旧写法三条分支）、
    项目 skills 的到达路径（是目录与链接，不是文件）。
    """
    proj_dir = project_dir / "projects"
    now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
    today = datetime.now().strftime("%Y-%m-%d")
    out = []

    # projects/ — 模板复制
    out += [("copy", dst, src) for src, dst in [
        (SCAFFOLD_TEMPLATES_DIR / "board-template.yaml",
         proj_dir / "board.yaml"),
        (SCAFFOLD_TEMPLATES_DIR / "issues-templates-template.md",
         proj_dir / "issues" / "TEMPLATES.md"),
        (SCAFFOLD_TEMPLATES_DIR / "project-changelog-template.md",
         proj_dir / "changelog.md"),
        # evals/ 下只有 3 个空目录，不给说明用户不知道那是干什么的
        (SCAFFOLD_TEMPLATES_DIR / "evals-template" / "README.md",
         proj_dir / "evals" / "README.md"),
    ]]

    # projects/ — 含占位符的模板走渲染写入
    # taxonomy 是 doc-graph-health 概念热点维度与 check_archive tags 检查的正源，
    # 装机放入才有词表可维护（此前只有巡检报告里一句「模板见 core 插件」让用户手抄）
    # dev-log 是 PARAM_DEFAULTS['close_check']['ledger'] 指向的那个文件，两者必须同改。
    # 模板自带一条**真实首条**（不是 HTML 注释里的示例）：close_check 的账本检查会先剥
    # 注释再找条目，只给示例等于零条目——新项目 git init + 首次提交后跑 close-check
    # 立刻是红的，「开箱通电」就不成立了。首条日期即装机日，故走渲染而非 copy。
    out += [("render", dst, (SCAFFOLD_TEMPLATES_DIR / tpl_name, mapping))
            for tpl_name, dst, mapping in [
        ("specs-overview-template.md", proj_dir / "specs" / "overview.md",
         {"NOW_ISO": now_iso}),
        ("taxonomy-template.md", proj_dir / "specs" / "_meta" / "taxonomy.md",
         {"NOW_ISO": now_iso, "CREATED_DATE": today}),
        ("dev-log-template.md", proj_dir / "dev-log.md",
         {"CREATED_DATE": today}),
    ]]

    # projects/ — 空目录骨架（带 .gitkeep）
    # 下面这个变量名是 `check_scaffold_gitkeep_dirs_covered` 的对账锚点：那道闸按变量名 ＋ 紧跟的
    # 方括号清单取这几个目录，与 doctor 的骨架三档对账。改名要同批改那道闸。
    # （它取的是**第一个**匹配，所以这段注释里不能出现同样形态的字面，否则它解析出 0 个目录）
    #
    # **那道闸的方向是「本清单 ⊆ doctor 清单」，所以它抓不到「本清单少一项」**——清单变短时
    # 子集关系只会更容易成立，恒绿（实测：拿掉一整行后全量 validate 仍 218 全绿）。
    # 它抓的是反方向：这里多一项而 doctor 没查。少一项目前**没有提交时的闸**，
    # 兜底只有 `workframe_doctor.SCAFFOLD_OPTIONAL_FILES`，那是**装机落盘验收的 warn 档**。
    # 写在这里是因为「判过没有闸」与「没查过」在读者眼里同形。
    proj_gitkeep_dirs = [
        proj_dir / "evals" / "agents-md",
        proj_dir / "evals" / "skills",
        proj_dir / "evals" / "agents",
        proj_dir / "proposals" / "pending",
        proj_dir / "proposals" / "applied",
        proj_dir / "proposals" / "rejected",
        proj_dir / "archive",
    ]
    out += [("write", d / ".gitkeep", "") for d in proj_gitkeep_dirs]

    # logs/ — 仅 .gitkeep；运行期 hook 输出 / Librarian 快照按需追加
    out += [("write", project_dir / "logs" / ".gitkeep", "")]

    # 运行态状态目录 — 模板复制
    state_dir = state_dir_of(project_dir)
    out += [("copy", dst, src) for src, dst in [
        (SCAFFOLD_TEMPLATES_DIR / "activity-state-template.json",
         state_dir / "activity-state.json"),
        (SCAFFOLD_TEMPLATES_DIR / "events-template.jsonl",
         state_dir / "events.jsonl"),
        (SCAFFOLD_TEMPLATES_DIR / "memory-index-template.json",
         state_dir / "memory-index.json"),
        (SCAFFOLD_TEMPLATES_DIR / "skill-metrics-template.yaml",
         state_dir / "skill-metrics.yaml"),
    ]]

    # <角色记忆目录>/shared/ — 启动必读资产，由 agent-protocols Step 0 强依赖
    shared_dir = memory_dir_of(project_dir) / "shared"
    out += [("copy", dst, src) for src, dst in [
        (SCAFFOLD_TEMPLATES_DIR / "shared-memory-template.md",
         shared_dir / "MEMORY.md"),
        (SCAFFOLD_TEMPLATES_DIR / "shared-notes-template.md",
         shared_dir / "notes.md"),
    ]]

    # <角色记忆目录>/<role>/ — baseline 4 个 core role 骨架
    for role in BASELINE_CORE_ROLES:
        role_dir = memory_dir_of(project_dir) / role
        out.append(("write", role_dir / "MEMORY.md", _role_memory_placeholder(role)))
        out.append(("write", role_dir / "notes.md", _role_notes_placeholder(role)))

    return out


def ensure_project_scaffold(project_dir: Path, params=None):
    """初始化最小 projects/ + 运行态状态目录 + 角色记忆目录 + logs/ 骨架。

    设计原则：
      - 已存在文件一律不覆盖（跳过并记录到 skipped）
      - 不创建任何 project_type 业务目录（旧项目类型未知，旧项目可能已有自己的业务目录）
      - 复用 templates/ 下的模板，避免多处各写一份
      - core 4 角色的记忆骨架预创建（plugin baseline 承诺，消除对 Write 工具
        隐式 mkdir 行为的依赖）

    `params`：对话参数（`--params` 路径），只用于渲染 AGENTS.md；不带时 AGENTS.md 填兜底文本。

    Returns: (created_paths, skipped_paths)
    """
    created = []
    skipped = []

    ensure_agents_md(project_dir, created, skipped, params)

    # 清单只有一份（`scaffold_targets`），这里只按 kind 派发给对应的写入原语
    for kind, dst, payload in scaffold_targets(project_dir):
        if kind == "copy":
            _copy_if_absent(payload, dst, created, skipped)
        elif kind == "render":
            _render_if_absent(payload[0], dst, payload[1], created, skipped)
        else:
            _write_if_absent(dst, payload, created, skipped)

    # 项目 skills 的到达路径——**先于 .gitignore**：链接形态决定要不要忽略 `.claude/skills`
    ensure_project_skills_dir(project_dir, created, skipped)

    # .gitignore — 强制对齐 §Git 策略（运行态状态目录 + logs/** 必须默认不进 git；
    # 记忆与其 sidecar memory-index.json 反过来必须进 git，模板用 `/*` + `!` 成对写法放行）
    _ensure_gitignore(project_dir, created, skipped)

    return created, skipped


# ---------------------------------------------------------------- 参数化渲染
#
# 对话采集到的值由调用方（launcher setup skill）写成 JSON 参数文件传入，
# 模板填充与占位符零残留由本脚本保证——不依赖模型逐个手工替换。

PARAM_REQUIRED = ("project_name", "one_line_goal", "business_context")
PARAM_DEFAULTS = {
    "project_type": "product-work",
    "dormant_profile": "normal",
    "role_profile": "software-team",
    "project_level_roles": "_（暂无项目级角色；需要时按 `role-customization-guide.md` 新增）_",
    "project_specific_constraints": "",
    "business_directories": "_（业务层跟随实际脚手架，框架不预建）_",
    # 收口闸 `[ledger]` 项（账本条目字段）的项目级声明——**默认开启**，装机即写入。
    # `ledger` 指向下面 `ensure_project_scaffold` 一并落盘的 `projects/dev-log.md`，
    # 两者是同一个默认的两半：只写键不给文件，新项目首次跑 close-check 就是红的。
    # 想关掉的项目把值改成空壳 `{}`（**不是删键**：write_workframe_config 走
    # `setdefault`，删了键下次重跑 scaffold 会原样写回；空壳既不被覆盖，
    # `check_ledger` 也读不到 `ledger` 而走跳过分支）。
    "close_check": {
        "ledger": "projects/dev-log.md",
        "required_fields": ["模块", "背景与根因", "改动", "连带", "验证", "提交", "遗留"],
    },
}
CONFIG_USER_FIELDS = ("project_type", "dormant_profile", "role_profile", "close_check")

# 模块级自检：`CONFIG_USER_FIELDS` 的成员会在 main() 里被 `source[k]` 无兜底取值
# （`source` 是 params 或 PARAM_DEFAULTS，而 `load_params` 以 PARAM_DEFAULTS 打底——
# 用户 params 文件缺键永远兜得住）。**唯一的 KeyError 路径就是「进了元组、没进
# PARAM_DEFAULTS」**。实测那一炸的形态：`--create-missing` 已建出目标目录、
# preflight 已过，然后裸 KeyError traceback + exit 1，留下一个**空目录**——而空目录
# 正是下方 mkdir 处那段注释说的「失败痕迹被抹平」（下次重跑不再需要 --create-missing，
# --require-empty 也对它放行）。放在 import 期炸，让改错的人在跑任何东西之前就看见。
#
# 用 `⊆` 而不是 `==`：PARAM_DEFAULTS 合法地多出若干只用于模板渲染、不进 config 的键
# （project_level_roles 等），`==` 在健康基线上就是恒红。
# 写成 `raise` 而不是 `assert`：assert 会被 `python -O` 整条剥掉，那时它与「检查通过」
# 长得一模一样。
_CONFIG_USER_FIELDS_NO_DEFAULT = sorted(set(CONFIG_USER_FIELDS) - set(PARAM_DEFAULTS))
if _CONFIG_USER_FIELDS_NO_DEFAULT:
    raise RuntimeError(
        f"CONFIG_USER_FIELDS 有成员在 PARAM_DEFAULTS 里没有默认值："
        f"{_CONFIG_USER_FIELDS_NO_DEFAULT}"
        f"——装机会在 main() 组装 user_fields 时抛 KeyError，"
        f"目标目录已建出来但里面一个文件都没有"
    )

# 两个纯枚举字段的合法取值，与 `workframe_doctor.CONFIG_FIELD_ENUM` 同源
# （validate 的 `check_config_enum_single_source` 对账两处，防漂）。
# `role_profile` **不在这里硬编码**——它的权威定义是 reference/role-profile-catalog.md，
# 由 `extract_role_profile_routing` 直接向 catalog 求证，避免造出第三处事实源。
CONFIG_FIELD_ENUM = {
    "project_type": ("product-work",),
    "dormant_profile": ("high-frequency", "normal", "low-frequency", "archive"),
}


class ParamError(Exception):
    """参数缺失或模板渲染残留——必须让调用方当场看见，不静默降级。"""


# 原样渲染进 AGENTS.md 的用户自由文本参数（`agents_md_mapping` 里除路由块外的五个）。
# 路由块取自 catalog、由框架维护，不在此列。
AGENTS_MD_FREE_TEXT_PARAMS = ("one_line_goal", "business_context", "project_level_roles",
                              "project_specific_constraints", "business_directories")


def preflight_params(params: dict):
    """把所有「会导致失败」的参数校验集中到**写任何文件之前**。

    为什么必须前置：早期实现里 `role_profile` 的校验藏在 `render_project_docs` 内部
    （`extract_role_profile_routing` 找不到 profile 就抛），而那一步跑在
    `write_workframe_config` 与 `ensure_project_scaffold` **之后**——于是无效值会留下
    一个「配置已写坏、骨架已建一半」的半成品目录（2026-08-16 实测：exit=1，但目标目录
    已有 30 个文件、config 里躺着那个不存在的 profile 名）。
    `project_type` / `dormant_profile` 更糟：从来没有任何校验，坏值直接落盘且**退出码 0**
    ——脚手架自称成功，要等用户某天跑 doctor 才发现配置是坏的。

    校验失败即抛 ParamError，调用方在写盘前就退出，目标目录保持原样。
    """
    for field, allowed in CONFIG_FIELD_ENUM.items():
        val = params.get(field)
        if val not in allowed:
            raise ParamError(
                f"{field}={val!r} 非法（合法取值：{' / '.join(allowed)}）"
            )
    # 带 `@` 的值拒收、不改写：这几个值原样进 AGENTS.md，而 `@<路径>` 在那里是导入语法——
    # 有一扇门不展开它、字面串原样进上下文，安装自检（doctor agents_md）也按导入判 error。
    # 静默改写用户原文等于替用户改了口径，所以只报出来让用户改。**两条判据、两种成因**：
    #   1. 行首 `@`：比路径形宽（`@qa` / `@设计` 这类裸词也拦）。行首是整行被当成导入的形态，
    #      而裸词导入（官方的 `@README`）与角色名同形、事后分辨不了，所以在入口就一律不放。
    #   2. 行中路径形 `@`：判据与 doctor 同一份（`md_path_imports`）。少了这条，用户填
    #      「见 @docs/rules.md」会一路装完，然后每次 doctor 在他的项目上报 error。
    for field in AGENTS_MD_FREE_TEXT_PARAMS:
        val = params.get(field)
        if val is None:
            continue
        for n, line in enumerate(str(val).splitlines(), 1):
            if line.startswith("@"):
                raise ParamError(
                    f"{field} 第 {n} 行以 `@` 开头（{line[:40]!r}）——它会原样渲染进 AGENTS.md，"
                    f"行首的 `@<名字>` 在那里是导入语法：另一扇门不展开它、那行字面串原样进"
                    f"上下文，安装自检也会判 error。改写这一行，别让这个符号顶格"
                    f"（例如把它挪到句中，写成「设计角色 designer：负责视觉」）")
            hits = md_path_imports(line)
            if hits:
                raise ParamError(
                    f"{field} 第 {n} 行有路径形导入 {hits[:3]}——CC 把 `@<路径>` 当导入处理的位置"
                    f"是**行内任意处**，不只是行首（官方 memory.md：`reference them with @ syntax "
                    f"anywhere`）。它渲染进 AGENTS.md 后，一扇门展开、另一扇门原样读字面串，"
                    f"两边内容就此不同且都不报错。把路径写成不带前缀符号的形式（如「见 docs/rules.md」）")
    # role_profile 向 catalog 求证：不存在时在这里就抛，而不是等渲染 AGENTS.md 才炸
    extract_role_profile_routing(params["role_profile"])


# 订阅声明的参数块 schema（`params.json` 的 `subscription` 键）。三个字段都必填：
#   marketplace_name  市场名（`core@<这个名字>`）
#   source            市场源**对象**，写进 settings 的就是它本身
#                     （`{"source":"directory","path":"…"}` / `{"source":"github","repo":"owner/repo"}`）
#   install_location  市场本体在本机的落地目录，写进用户级注册表的 installLocation
# **source 不是 install_location**：后者对 github / git 源是本地缓存目录，写进 settings
# 等于把每台机器各不相同的缓存路径当成插件来源，协作者 clone 后解析不到。
SUBSCRIPTION_FIELDS = ("marketplace_name", "source", "install_location")


def preflight_subscription(params: dict, write_subscription: bool):
    """`--write-subscription` 与 params 的 `subscription` 块**必须成对**，缺一即拒。

    为什么两边都要、且不匹配就硬退出（而不是「有哪个用哪个」）：
      - 只给参数块、漏了命令行开关 ⇒ 静默不写，调用方以为订阅好了，产出一个没订阅的项目，
        而 doctor 要到落盘验收那一步才报——那时已经走过好几步；
      - 只给开关、漏了参数块 ⇒ 没有市场名与源可写，硬写只能瞎猜。
    两种都在**写任何文件之前**退出（本函数由 preflight 期调用），目标目录保持原样。

    返回归一后的 subscription dict（不写时返回 None）。
    """
    sub = params.get("subscription") if params else None
    if sub is not None and not isinstance(sub, dict):
        raise ParamError(f"params 的 subscription 必须是 JSON 对象，实际 {type(sub).__name__}")
    if write_subscription and not sub:
        raise ParamError("带了 --write-subscription 却没有 params.subscription 块——"
                         f"需要 {' / '.join(SUBSCRIPTION_FIELDS)} 三个字段")
    if sub and not write_subscription:
        raise ParamError("params 里有 subscription 块却没带 --write-subscription——"
                         "不加开关本脚本一个字节都不写 settings，"
                         "那会产出一个「以为订阅了、其实没有」的项目；"
                         "要写就加上开关，不写就把这个块去掉")
    if not sub:
        return None
    missing = [k for k in SUBSCRIPTION_FIELDS if not sub.get(k)]
    if missing:
        raise ParamError(f"params.subscription 缺字段: {', '.join(missing)}")
    if not isinstance(sub["source"], dict):
        raise ParamError(f"params.subscription.source 必须是市场源**对象**"
                         f"（实际 {type(sub['source']).__name__}）——"
                         f"形如 {{\"source\":\"directory\",\"path\":\"…\"}} 或 "
                         f"{{\"source\":\"github\",\"repo\":\"owner/repo\"}}")
    return sub


def write_subscription(project_dir: Path, sub: dict):
    """写两项订阅声明 ＋ 用户级市场注册表。**merge 不覆盖，备份 ＋ 回读 ＋ 失败回滚。**

    两处的失败后果不同，所以处置也不同（这是有意的不对称，不是漏写）：
      - **① 项目 `.claude/settings.json` 是承重的**——没有它 core 插件压根不加载，
        写不成即 `ok=False`，调用方非零退出；
      - **② 用户级 `~/.claude/plugins/known_marketplaces.json` 是补强**——① 里的
        `extraKnownMarketplaces` 已经让市场在项目一级解析得到（doctor 的判据就是这条），
        ② 补的是「这台机器上本来就有这个市场」那一格。写不成只出 warning ＋ 手动补丁，
        不拖垮装机。

    **不判断源解析不解析得到**（那是 doctor `subscription` 项的活），也**不装插件本体**：
    插件缓存与安装登记表交给各扇门自己的首个会话。

    **两处的备份都落项目的 `logs/`**，与框架既有的备份约定（`logs/CLAUDE.md.bak-<时间戳>`，
    见 `reference/claude-md-merge-guide.md`）合流——那个目录在 managed gitignore 块内。
    ② 的原文件在**项目外**（用户主目录），它的备份仍然落进本项目的 `logs/`，理由三条：
    ①`logs/` 在忽略面内，备份不会进任何仓；②它是**这一次装机**留下的审计痕迹，跟着这次
    装机走才找得回来（备份文件名保留原名 `known_marketplaces.json.bak-…`，不会与项目自己的
    文件混淆）；③落在 `~/.claude/plugins/` 旁边的话，那个目录不属于任何项目、没有任何清理
    机制，堆积起来无人负责。**落点会原样打给用户**，不靠他去猜。

    返回 `(ok, lines, detail)`：`ok` 只反映 ①；`lines` 是要原样打给用户的人话（含 note 与补丁）；
    **`detail` 是要落进 `setup-state` 的持久痕迹**——`ok=True` 但发生了「同名市场源冲突、
    保留了原声明」或「② 未写入」时，它非空。没有它的话，这两种情形唯一的痕迹只在 stdout，
    会话一结束就没了：装机自称成功，而项目实际订阅的可能是**用户刚才没选的那个框架副本**。

    **能力边界：写进 ① 的是市场**来源**（`SOURCE` 对象），不是本机落地路径——这一条
    「换台机器也解析得到」的语义本身从未跨机实测过。** 单机语义已验（写进去的确实是
    source 对象而不是 root / installLocation），跨机那一半只有推理：directory 绝对路径那一档
    由 doctor 的 `subscription` 分档报 `!` 提醒用户，git / github 源那一档连本机样本都没有。
    ⇒ 这句话写在这里，是因为它此前只活在一份按纪律要被删掉的过程档里；真机补法在发版检查单。
    """
    # 三个字段一律 `.get()` 取，**不用下标**：`preflight_subscription` 已经保证它们都在，
    # 但那是调用顺序上的保证，不是本函数自身的。用下标的话，preflight 哪天被绕过（直接调本
    # 函数、或那道校验被改松）就是一条裸 KeyError traceback；用 `.get()` 则交给 `_settings_io`
    # 的参数校验，得到的是人话 ＋ 本函数既定的分档处置（① 非零退出 / ② 只警告）。
    # 突变实测过：把 preflight 的必填校验毁掉后，下标写法当场退化成 traceback。
    lines = []
    flags = []          # 进 setup-state 的持久痕迹，见 docstring
    market = str(sub.get("marketplace_name") or "").strip()
    source = sub.get("source")
    settings_path = Path(project_dir) / ".claude" / "settings.json"
    backup_dir = Path(project_dir) / "logs"

    try:
        existing = _settings_io.read_settings(settings_path)
        merged, notes = _settings_io.merge_project_subscription(existing, market, source)
        changed, backup = _settings_io.write_settings(settings_path, merged,
                                                      backup_dir=backup_dir)
    except _settings_io.SettingsError as e:
        lines.append(f"ERROR: 订阅声明未写入——{e}")
        try:
            patch = {"extraKnownMarketplaces": {market: {"source": source}},
                     "enabledPlugins": {f"core@{market}": True}}
            lines.extend(_settings_io.manual_patch(settings_path, patch).splitlines())
        except Exception:
            pass
        return False, lines, "settings 写入失败"
    # 判据是**落盘的源到底是不是本次要写的那个**（结构比对，不是去匹配 note 的文案——
    # 文案会改，而「写进去的和要写的不一样」这件事不会）。不一致只可能来自
    # `merge_project_subscription` 的「同名市场、源不同 ⇒ 保留原声明」那一档。
    # 取值走 `_settings_io.landed_*`：只读预演要按同一个判据说「为什么不改」，两处各写一份必漂。
    landed = _settings_io.landed_project_source(merged, market)
    conflict = landed != source
    if conflict:
        flags.append(f"市场源与项目已有声明不一致，**保留了原声明**（落盘的是 {landed}，"
                     f"本次传入的是 {source}）——项目订阅的可能不是你这次选的那个框架副本")
    if not changed:
        # **「没变」有两个成因，文案必须分开**：幂等重跑（真的已经对了）与冲突
        # （同名市场源不同 ⇒ 保留原声明，而原声明恰好已经满足 merge 结果）。
        # 后者下说「已是目标状态」是**假安心**——市场源恰恰不是用户这次选的那个，
        # 而这正是本段代码后果最重的一格（项目订阅的是另一个框架副本）。
        # 触发面也不是边角：同名不同源是开发机上的常态，而补跑是被 SKILL 明确鼓励的动作、
        # 正是 `changed=False` 的高发路径。判据用 `conflict` 这个结构事实，不看文案。
        if conflict:
            lines.append(f"subscription unchanged → {settings_path}（一个字节没动，"
                         f"**但这不是「已经对了」**：落盘的仍是原来那个源 {landed}，"
                         f"本次传入的 {source} 没有被写入——详见下一行）")
        else:
            lines.append(f"subscription unchanged → {settings_path}（两项声明已是目标状态，"
                         f"一个字节没动、也没生成备份）")
    else:
        lines.append(f"wrote subscription → {settings_path}"
                     + (f"（原文件已备份到 {backup}）" if backup else ""))
    lines.extend(notes)

    # ② 用户级市场注册表
    try:
        home_reg = Path.home() / ".claude" / "plugins" / "known_marketplaces.json"
    except Exception as e:
        lines.append(f"WARNING: 定位不到用户主目录（{type(e).__name__}: {e}），"
                     f"用户级市场注册表未写——项目级声明已在，本机仍装得上")
        flags.append("用户级市场注册表未写入（定位不到用户主目录）")
        return True, lines, "；".join(flags) or None
    try:
        reg = _settings_io.read_settings(home_reg)
        reg_merged, reg_notes = _settings_io.merge_known_marketplaces(
            reg, market, source, sub.get("install_location"))
        reg_changed, reg_backup = _settings_io.write_settings(home_reg, reg_merged,
                                                              backup_dir=backup_dir)
        reg_landed = _settings_io.landed_registry_source(reg_merged, market)
        reg_conflict = reg_landed != source
        if reg_conflict:
            flags.append("用户级市场注册表里这个名字已被别的源占用，本次未覆盖")
        if not reg_changed:
            # 与项目侧同一处理：冲突态下「已注册为同一个源」与紧接着那行冲突提示直接互斥
            if reg_conflict:
                lines.append(f"marketplace registry unchanged → {home_reg}（一个字节没动，"
                             f"**但这不是「已经对了」**：这个名字仍注册为 {reg_landed}，"
                             f"不是本次的 {source}——详见下一行）")
            else:
                lines.append(f"marketplace registry unchanged → {home_reg}（本市场已注册为同一个源，"
                             f"一个字节没动）")
        else:
            lines.append(f"wrote marketplace registry → {home_reg}"
                         + (f"（原文件已备份到 {reg_backup}——它在项目外，备份按装机审计落本项目 "
                            f"logs/）" if reg_backup else ""))
        lines.extend(reg_notes)
    except _settings_io.SettingsError as e:
        lines.append(f"WARNING: 用户级市场注册表未写入——{e}")
        lines.append("         项目级声明已在，本机装得上；要补的话用下面的补丁：")
        patch = {market: {"source": source,
                          "installLocation": str(sub.get("install_location") or "<市场本体在本机的目录>")}}
        lines.extend(_settings_io.manual_patch(home_reg, patch).splitlines())
        flags.append("用户级市场注册表未写入")
    return True, lines, "；".join(flags) or None


def load_params(params_path: Path) -> dict:
    """读取并校验对话参数文件。"""
    try:
        # utf-8-sig：Windows 记事本与 PowerShell 5.1 `Out-File -Encoding utf8` 都会写 BOM，
        # 纯 utf-8 会把用户手搓的 params.json 顶成「Unexpected UTF-8 BOM」语法错误。
        # （曾只改了 module_init 的同款读取而漏掉本处）
        raw = json.loads(params_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ParamError(f"params file not found: {params_path}")
    except json.JSONDecodeError as e:
        raise ParamError(f"params JSON syntax error at line {e.lineno} col {e.colno}: {e.msg}")
    if not isinstance(raw, dict):
        raise ParamError(f"params must be a JSON object, got {type(raw).__name__}")
    missing = [k for k in PARAM_REQUIRED if not str(raw.get(k, "")).strip()]
    if missing:
        raise ParamError(f"params missing required field(s): {', '.join(missing)}")
    params = dict(PARAM_DEFAULTS)
    params.update({k: v for k, v in raw.items() if v is not None})
    return params


# `@<path>` 导入的**形态**判定——全仓唯一一份。doctor 的 `agents_md`、validate 的
# `agents_md_self_contained`、本文件的 `preflight_params` 三个消费方都问它，谁都别再抄一份。
# 官方语义（`code.claude.com/docs/en/memory.md` 第 97 / 99 / 101 / 104 / 107 行，2026-09-12 取）：
# 导入可写在**任意位置**（行首、行中、列表项里都算，原文 `reference them with @ syntax
# anywhere`），被导入的文件还能再导入（最多四跳）；只有 code span 与围栏代码块里的不算。
# **判据是「路径形」而不是「行首」**：按行首判会漏掉官方自己的示例
# `- git workflow @docs/git-instructions.md`；按「文中出现过 @」判又会把 `@dev`、
# `| @prompt-eng |`、`@角色名` 全判成导入，而假红最后会被「修」成永远绿。
# 路径形 = 带 `/`（含 `./` `../` `~/`）或以已知文件扩展名收尾。
# **已知漏报面**：官方的 `@README` 这种**不带斜杠也不带扩展名**的裸词导入认不出——它与
# `@dev` / `@qa` / `@角色名` 在文本上完全同形，无法只凭字面分辨。这一档只能靠人。
_MD_IMPORT_EXTS = frozenset((
    "md", "markdown", "mdx", "json", "jsonc", "yaml", "yml", "toml", "ini", "cfg", "conf",
    "txt", "py", "sh", "ps1", "bat", "js", "jsx", "ts", "tsx", "rs", "go", "java", "rb",
))
# 宽候选：`@` 后一串路径字符。`@` 前不能是字母数字 / `@` / 反斜杠（邮箱、转义）；
# 首字符收 `.` `/` `~`（`@./AGENTS.md`、`@../x.md`、`@/abs/x.md`、`@~/.claude/x.md` 都是合法
# 导入形态，只收 `[~\w]` 会把官方自己写的 `@./AGENTS.md` 整个漏掉——本条是自证时实测抓到的）；
# 结尾不收 `.`，免得把 `@AGENTS.md。` 里的句号吃进路径。
_MD_AT_TOKEN_RE = re.compile(r"(?<![\w@\\])@([~./\w][\w./\-]*[\w/\-]|[~\w])")


def md_path_imports(line: str):
    """一行文本里**路径形**的 `@<path>` 导入字面量，按出现顺序返回（没有则空表）。

    宽候选收 `@` 开头的整串，再逐个用「带 `/` 或已知扩展名」严证——一开始就用严正则
    枚举的话，形态怪一点的候选会直接从循环里消失，红灯连报都报不出来。
    只判**一行文本的形态**：这一行在文档里是不是落在 code span / 围栏 / 注释里，由调用方
    先剥（doctor 的 `_md_import_surface` 是那一层的唯一实现）。
    """
    hits = []
    for m in _MD_AT_TOKEN_RE.finditer(line):
        tok = m.group(1)
        if "/" in tok:
            hits.append(m.group(0))
        elif "." in tok and tok.rsplit(".", 1)[-1].lower() in _MD_IMPORT_EXTS:
            hits.append(m.group(0))
    return hits


def _outside_fence(lines):
    """逐行产出**代码围栏之外**的 `(下标, 行文本)`；围栏行本身不产出。

    catalog 里合法存在围栏内的示例标题与示例表（`### \\`x\\`` / `**角色优先级**：`），
    它们是文档内容、不是结构——不跟踪围栏就会把示例当成真章节 / 真表。

    这是 catalog 解析里「围栏外」这一判据的**唯一实现**：本模块的 `catalog_section`
    用它定位章节标题，框架仓 validate 的 `priority_table` import 它定位优先级表。
    两边同一份状态机，别在任何一侧另写一个 `in_fence` 循环。
    """
    in_fence = False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield i, ln


def catalog_section(text: str, profile: str):
    """切出 role-profile-catalog.md 里 `### `<profile>`` 章节的正文（不含标题行；找不到返回 None）。

    **这是 catalog 章节解析的唯一实现**：本模块的 `extract_role_profile_routing` 用它，
    框架仓 validate 的 `role_enum_single_source` / `role_profile_catalog_exists` 两闸也
    import 它求证。闸自己另写一套正则时只能比消费方更宽或更窄，两个方向都实证出过洞——
    闸宽的那次：标题行尾多一个空格，闸全绿而装机 preflight 抛 ParamError。

    两个方向的裸正则各自坏在相反的地方，所以必须带 fence 状态机：
      - 只认下一个**同级**标题（`(?=^### `|\\Z)`）→ 最后一个章节一路吞到文件末尾，把后面
        「渲染契约」段的示例块也算成本章节内容——删掉末章自己的块后不报错，静默渲染错块。
      - 收紧成也认 `^## ` → 基线直接全红：路由块正文第一行自己就是
        `## 路由偏好（profile: ...）`，那是**代码围栏内**的标题。
    围栏状态从第 0 行起就跟踪：围栏内出现的 `### `x`` 只是示例文本，不算章节标题。

    标题判定是**整行相等**（`ln == "### `<profile>`"`）：闭合反引号后必须紧跟行尾，
    行尾空格 / 中文后缀 / HTML 注释锚都视为「没有这个章节」。按 `splitlines()` 切行，
    CRLF 与 LF 原文都能命中（Windows 工作区检出即 CRLF）；别改回按 `\\n` 锚定的正则——
    那会让 `newline=""` 读进来的 CRLF 原文一个章节都找不到。

    **围栏外恰 1 处是契约**：0 处返回 None（调用方各自给措辞），≥2 处抛 ParamError、
    **不取第一个**——取第一个会让一份靠前的陈旧副本静默顶替真正的章节，渲染进用户项目的
    AGENTS.md 而没有任何报错（实测：真章节前插一份完整副本，仓侧两道闸全绿、
    `extract_role_profile_routing` 返回副本）。这与 `routing_block` 的「块数恰 1」
    同族：同一份 catalog 里「文件→章节」「章节→块」「章节→表」三层都做「恰好一个」，
    缺任一层就留一条「更靠前的东西顶替真内容」的静默通道。
    """
    lines = text.splitlines()
    heading = f"### `{profile}`"
    hits = [i for i, ln in _outside_fence(lines) if ln == heading]
    if not hits:
        return None
    if len(hits) > 1:
        # 行号列表**自己封顶**：runner 对异常消息按 `str(e)[:120]` 截断，让变长的那部分
        # 排最后并限长，文案长度才由构造保证有界——否则重复处数越多越容易把
        # 前面的动作指令挤掉（实测未封顶版 124 字符，被砍掉的正是「删到只剩一个」）。
        shown = ",".join(str(i + 1) for i in hits[:3])
        more = f"…共{len(hits)}" if len(hits) > 3 else ""
        raise ParamError(
            f"profile {profile!r} 章节出现 {len(hits)} 处——靠前那份会静默顶替真章节、"
            f"渲染进用户 AGENTS.md；删到只剩一个(行 {shown}{more})"
        )
    start = hits[0] + 1
    # 正文扫描**有意不复用** `_outside_fence`：那个迭代器把围栏行吞掉不产出，而正文
    # 必须**包含**围栏行（路由块本身就是围栏，`routing_block` 要在正文里再切一次）。
    # 两者的围栏语义相反——一个「跳过围栏内容」，一个「围栏内容是正文的一部分」，
    # 所以是两条判定式而不是一份被抄了两遍。
    in_fence = False        # 标题命中的前提就是围栏外，故正文一定从围栏外开始
    body = []
    for ln in lines[start:]:
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and re.match(r"#{1,3} ", ln):
            break
        body.append(ln)
    return "\n".join(body)


def routing_block(section: str, profile: str) -> str:
    """从章节正文里取出**恰好一个** ```markdown 路由渲染块（去围栏、去尾换行）。

    块数恰 1 是契约：0 个抛（多半是围栏语言标注 ```markdown 被去掉了）；≥2 个也抛、
    **不取第一个**——取第一个会让章节里一个更靠前的陈旧块静默顶替真正的路由文本，渲染进
    用户项目的 AGENTS.md 而没有任何报错。两种失败措辞分开，别把人引向错误的排查方向。
    validate 的 `role_enum_single_source` 断言 8 调的也是本函数：闸与消费方同一判定式。
    """
    blocks = re.findall(r"```markdown\n(.*?)```", section, re.S)
    if not blocks:
        raise ParamError(
            f"role_profile {profile!r} section has no ```markdown routing block"
            f"（检查围栏语言标注 ```markdown 是否被去掉）"
        )
    if len(blocks) > 1:
        raise ParamError(
            f"role_profile {profile!r} section has {len(blocks)} ```markdown blocks, "
            f"expected exactly 1（更靠前的块会顶替真正的路由文本——不猜哪个是对的，删到只剩一个）"
        )
    return blocks[0].rstrip("\n")


def extract_role_profile_routing(role_profile: str) -> str:
    """从 role-profile-catalog.md 抽取该 profile 的「AGENTS.md 路由偏好渲染文本」代码块。

    确定性派生，不由模型转述——转述会漂移。薄壳：读文件 → `catalog_section` 切章 →
    `routing_block` 取块；解析口径全在那两个纯函数里，validate 与本函数共用同一份。
    文本模式读（universal newlines），CRLF 检出的工作区同样命中。
    """
    catalog = PLUGIN_ROOT / "reference" / "role-profile-catalog.md"
    if not catalog.exists():
        raise ParamError(f"role-profile-catalog.md not found: {catalog}")
    section = catalog_section(catalog.read_text(encoding="utf-8"), role_profile)
    if section is None:
        raise ParamError(
            f"role_profile {role_profile!r} not found in role-profile-catalog.md"
            f"（章节标题须整行为 ### `{role_profile}`，闭合反引号后紧跟换行，且不在代码围栏内）"
        )
    return routing_block(section, role_profile)


def render_placeholders(text: str, mapping: dict, source: str) -> str:
    """替换 {{PLACEHOLDER}} 并断言零残留。"""
    for key, value in mapping.items():
        text = text.replace("{{" + key + "}}", str(value))
    leftover = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", text)))
    if leftover:
        raise ParamError(f"{source}: unresolved placeholder(s) {', '.join(leftover)}")
    return text


def _company_context_readme(kind: str) -> str:
    label = "公司资料" if kind == "company-context" else "个人产出"
    return (
        f"# {label}\n"
        f"\n"
        f"> ⚠️ **敏感内容提示**：本目录可能包含公司内部资料 / 个人材料。\n"
        f"> 框架**不预设** `.gitignore` 规则——哪些文件进 git 由你决定。\n"
        f"> 含客户信息、合同、薪酬、未公开数据的文件，提交前请自行确认。\n"
    )


def project_docs_targets(project_dir: Path, params: dict, skills_rel=None):
    """对话产物层要写的每一项：(kind, dst, payload) 的有序清单，顺序即落盘顺序。

    与 `scaffold_targets` 同一契约、同一用途（真写与只读预演共用一份清单），只是这一层
    要 params 才算得出来。kind 两种：
      - "render_strict" payload = (模板绝对路径, mapping, source 标签, 模板缺失时的名字)
        —— 模板缺失在这一层是 `ParamError`（骨架层那边是记进 skipped 由 main 统一报），
        两层的处置有意不同，不要合并。
      - "write" payload = 要写的文本（`_write_if_absent`）

    **本函数不碰盘**：`project_skills_dir()` 只 stat 不写。

    `skills_rel`（项目根下的相对 `Path`）不给时现问 `project_skills_dir()`。真写路径不给——
    它跑在 `ensure_project_skills_dir` **之后**，现问得到的就是终态。只读预演必须显式给：
    那时到达路径还没建出来，现问会把 prd-style 算到 `.claude/skills/` 下，而真跑落在
    `.agents/skills/` 下（往返一致性组实证过这一格）。

    **AGENTS.md 不在这里**——对话参数里的项目段全渲染进它，唯一写入点是 `ensure_agents_md`
    （`main()` 经 `ensure_project_scaffold` 把 params 传过去）。CLAUDE.md 只剩头部信息四项。
    """
    tpl_dir = SCAFFOLD_TEMPLATES_DIR
    now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
    today = datetime.now().strftime("%Y-%m-%d")
    return [
        ("render_strict", project_dir / "CLAUDE.md",
         (tpl_dir / "claude-md-template.md",
          {
              "PROJECT_NAME": params["project_name"],
              "FRAMEWORK_VERSION": read_framework_version(),
              "PROJECT_TYPE": params["project_type"],
              "CREATED_DATE": today,
          },
          "claude-md-template.md", "claude-md-template.md")),
        # projects/modules/overview.md —— modules/ 体系永远启用
        ("render_strict", project_dir / "projects" / "modules" / "overview.md",
         (tpl_dir / "modules-template" / "overview-template.md",
          {"PROJECT_NAME": params["project_name"], "NOW_ISO": now_iso, "TODAY": today},
          "modules-template/overview-template.md", "modules overview-template.md")),
        # 周边资产层
        ("write", project_dir / "company-context" / "README.md",
         _company_context_readme("company-context")),
        ("write", project_dir / "my-workspace" / "README.md",
         _company_context_readme("my-workspace")),
        # 项目 PRD 框架 skill —— 出厂默认版实例化到项目层；此后属于项目，框架不再覆盖。
        # 落点经 `project_skills_dir()`：新建项目是 `.agents/skills/`（`.claude/skills` 是指向
        # 它的链接），已装项目是 `.claude/skills/` 真目录——两种形态都解析到真实源，不按门硬写路径
        ("render_strict",
         project_dir / (skills_rel or project_skills_dir(project_dir)) / "prd-style" / "SKILL.md",
         (tpl_dir / "project-skills" / "prd-style" / "SKILL.md",
          {"PROJECT_NAME": params["project_name"]},
          "project-skills/prd-style/SKILL.md", "prd-style template")),
    ]


def _render_strict(dst: Path, payload, created_list, skipped_list):
    """`project_docs_targets` 的 "render_strict" 写入原语：已存在一律不覆盖，模板缺失抛 ParamError。"""
    if dst.exists():
        skipped_list.append(str(dst))
        return
    tpl, mapping, source, missing_label = payload
    if not tpl.exists():
        raise ParamError(f"{missing_label} missing: {tpl}")
    rendered = render_placeholders(tpl.read_text(encoding="utf-8"), mapping, source)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(rendered, encoding="utf-8", newline="")
    created_list.append(str(dst))


def render_project_docs(project_dir: Path, params: dict, created_list, skipped_list):
    """渲染对话产物：CLAUDE.md + modules/ 总图 + 周边资产层。已存在文件一律不覆盖。

    清单只有一份（`project_docs_targets`），这里只按 kind 派发给对应的写入原语。
    """
    for kind, dst, payload in project_docs_targets(project_dir, params):
        if kind == "render_strict":
            _render_strict(dst, payload, created_list, skipped_list)
        else:
            _write_if_absent(dst, payload, created_list, skipped_list)


def write_workframe_config(project_dir: Path, framework_path=None, user_fields=None,
                           project_name=None):
    """写 .workframe-config.json — 框架字段刷新 + 用户字段保留（merge 模式）。

    Merge 行为：
      - read existing → 仅刷新框架管理字段（project_name / framework_version /
        framework_path）→ 写回；用户配置字段（project_type / dormant_profile /
        role_profile 及任何其他用户加的字段）保留不动
      - 损坏 / 非 dict 时**不破坏用户文件**，返回 warning 让调用方处理

    framework_path 语义：框架仓根路径。marketplace 订阅场景没有仓根（插件缓存路径随
    版本变化，不持久化），传 None 时不写该字段——这是常态。读取方均有缺省兜底，
    见 project-architecture.md §.workframe-config.json。
    """
    config_path = project_dir / ".workframe-config.json"

    existing: dict = {}
    if config_path.exists():
        try:
            raw = config_path.read_text(encoding="utf-8")
        except OSError as e:
            warning = (
                f".workframe-config.json read failed: {e}. "
                f"Fix file permissions then re-run."
            )
            return config_path, warning
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            warning = (
                f".workframe-config.json JSON syntax error at line {e.lineno} col {e.colno}: {e.msg}. "
                f"Fix the JSON manually (do not delete the file — it holds your "
                f"project_type/dormant_profile/role_profile fields), then re-run."
            )
            return config_path, warning
        if not isinstance(parsed, dict):
            warning = (
                f".workframe-config.json must be a JSON object, got {type(parsed).__name__}. "
                f"Fix the file manually then re-run."
            )
            return config_path, warning
        existing = parsed

    # project_name 是**展示名**（启动横幅与报告用），目录名只是存放位置，两者可以不同。
    # 取值优先级：本次显式传入 > 项目里已有的 > 目录名兜底。
    # 中间那档不能省——没有它，一次不带 --params 的重跑（修骨架 / 无人值守流程）会把用户
    # 设过的展示名**静默冲回目录名**（2026-08-10 实测确认）。framework_version 则相反，
    # 它是框架自己的字段，每次都该刷新到最新。
    existing["project_name"] = project_name or existing.get("project_name") or project_dir.name
    existing["framework_version"] = read_framework_version()
    if framework_path:
        existing["framework_path"] = str(framework_path).replace("\\", "/")

    # 对话采集到的用户字段：仅在本地缺失时写入，已有值一律不覆盖
    # （接入已有项目时用户可能已手工配置过）
    for key in (user_fields or {}):
        existing.setdefault(key, user_fields[key])

    config_path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="",
    )
    return config_path, None


# ---------------------------------------------------------------- 只读预演（--print-plan）
#
# 确认页要对「会被修改的已存在文件」逐个点名，而此前那份清单靠模型凭记忆复述——真机上漏掉过
# 项目外那一处写入（用户级市场注册表）。本段把清单改成「跑一条命令、把输出逐字贴过去」。
#
# **能力边界，别把它当闸**：确认页是终端文本，**机器看不见它渲染了什么**。本段把「凭记忆
# 复述」换成「转述一条命令的输出」，这是降低失败概率，不是一道闸——模型仍然可以不跑、
# 或跑了不贴。真机那一层至今没有机器面。

# 「项目内·新建」与「已存在·本脚本不覆盖」两格里**必须逐项露面**的文件（两格共用这一份
# 判据）。判据是**用户要为它做决定**，不是「重要」：
#   ① 受保护资产里**人手改**的那几个（`CLAUDE.md` / `AGENTS.md` / `.claude/settings.json` /
#      `.workframe-config.json` / 项目 skills 目录）——它们是用户的东西，被碰到就该点名；
#   ② `.gitignore`——它改变的是用户自己的 git 行为。
# 其余（`projects/` 骨架、运行态、角色记忆、占位 `.gitkeep`）是机器维护的**纯新建**文件，
# 不覆盖任何已有内容、用户也没有可做的决定，折叠成一行计数。
#
# **折叠只发生在这一格与「已存在·本脚本不覆盖」那一格**，两格共用本判据——在一格里要你
# 过目的东西，不会在另一格里因为「这次恰好不动它」就退化成一个计数（用户自己写的
# `CLAUDE.md` 正是落在第四格的那一类）。「项目内·改已有」与「项目外·改已有」两格恒逐项
# 展开——那两格里每一行都是「会动你已有的东西」，一条都不许被计数吃掉。
#
# 为什么要折叠：A 路径实跑 40 项里有 34 项是骨架噪音，而真正要用户看的是受保护资产与
# 项目外那两行。确认页规范本身要求「**决策对象不因简洁缺席**」，而「逐字贴全部输出」
# 会把决策对象淹进噪音里——两条要求在这里正面相撞。**解法落在脚本侧**：由它分主次，
# 确认页照贴即可；靠确认页那层让模型自己挑，等于又回到「靠记性」，而那正是本次要修的东西。
# 键即「必须逐项露面」的清单，值是这一项**为什么要你过目**（该项自己没带说明时用它补上）。
# 两者写在一起，免得清单与理由各存一份、加了条目却没人写理由。
PLAN_ITEMIZED_WHY = {
    "CLAUDE.md": "项目入口（含 AGENTS.md 导入）；受保护资产",
    "AGENTS.md": "两扇门共用的项目自有判据；受保护资产",
    ".gitignore": "它改的是你自己的 git 行为",
    ".workframe-config.json": "项目身份与运行档位配置；受保护资产",
    ".claude/settings.json": "订阅声明落点；受保护资产",
}
PLAN_ITEMIZED_EXACT = tuple(PLAN_ITEMIZED_WHY)      # 单一事实源：清单就是上面那个字典的键
PLAN_ITEMIZED_PREFIX = (".agents/skills", ".claude/skills")
PLAN_ITEMIZED_PREFIX_WHY = "项目 skills（受保护资产：装机放入出厂默认版，此后属于你的项目、框架不再覆盖）"


def plan_itemized(rel_path: str) -> bool:
    """这一项要不要在「项目内·新建」格里逐项露面——判据见 `PLAN_ITEMIZED_WHY` 上方。

    **确定性判定，不交给模型挑**：路径字面命中即逐项，其余折叠。
    """
    p = str(rel_path).replace("\\", "/")
    return p in PLAN_ITEMIZED_EXACT or p.startswith(PLAN_ITEMIZED_PREFIX)


def plan_itemized_why(rel_path: str) -> str:
    """逐项露面的那几项各自「为什么要你过目」；该项自己已带说明时返回空串（不覆盖它）。"""
    p = str(rel_path).replace("\\", "/")
    if p in PLAN_ITEMIZED_WHY:
        return PLAN_ITEMIZED_WHY[p]
    return PLAN_ITEMIZED_PREFIX_WHY if p.startswith(PLAN_ITEMIZED_PREFIX) else ""


PLAN_BUCKET_NEW = "项目内·新建"
PLAN_BUCKET_MOD = "项目内·改已有（先落备份到 logs/）"
PLAN_BUCKET_OUT = "项目外·改已有（先落备份到 <目标>/logs/）"
PLAN_BUCKET_KEEP = "已存在·本脚本不覆盖"

# 第四格每一项的说明。**这一格只断言一件事：本脚本不动它。**
PLAN_KEEP_NOTE = "**已存在 ⇒ 本脚本跳过**：不覆盖、不备份、一个字节不动"

# 第四格的格级说明，整格打一次。**两句话必须分开说**：本脚本动不动它，预演算得出来；
# 「它跑完之后还有谁来改这个文件」，预演**看不到也无从断言**——那发生在本脚本之外、
# 本次运行之后，由调用方自己决定并自己讲。把两句合成一句（「会被按合并稿写回」）
# 就是让预演替调用方说它不知道的话，而这一族陈述正是本格要防的东西。
PLAN_KEEP_SCOPE = ("↑ 这一格只说一件事：**本脚本不动它们**。本次之后会不会有别的步骤"
                   "按合并稿改写其中某些（例如接入已有项目时对 CLAUDE.md / AGENTS.md 的整合），"
                   "由**调用方**决定、也由调用方向你说明——本脚本预演不到那一步，不替它回答。")


def plan_keep_note(rel_path: str) -> str:
    """「已存在·本脚本不覆盖」格里逐项露面的那几项，各自的说明。

    折叠掉的骨架项不给说明（它们不露面）；判据与「项目内·新建」格共用 `plan_itemized`，
    所以**两格的逐项面一致**：在一格里要你过目的东西，在另一格里不会因为「这次恰好不改」
    就退化成一个计数。
    """
    why = plan_itemized_why(rel_path)
    return f"{why}；{PLAN_KEEP_NOTE}" if why else ""


def plan_skills_reach(project_dir: Path):
    """预演 `ensure_project_skills_dir` 这一步：返回
    `(新建项 or None, 这步之后链接在不在, 这步之后 project_skills_dir() 会返回什么)`。

    **这是本模块唯一一处只读预演没能与真写共用判定的地方。** 真写那边建目录、建链接与分支
    判定交织在同一个函数里，抽成纯函数要动那段已被反复验证的分支；这里改为按它的**分支顺序
    镜写**（冲突 / 悬空 / 补链接 / 两者都不在则新建 / 其余不动）。
    **漂移被什么抓住**：`tools/test_project_scaffold_plan.py` 的往返一致性组——它在真夹具上
    先跑 plan、再跑真写，逐项比两边的「新建集合」；镜写与真写分叉时当场红。

    后两个返回值都是**这一步跑完之后**的读数，因为它们的两个消费方都排在本步后面：
    `_ensure_gitignore` 读 `skills_link_present()`（链接这次才建出来的项目，按旧形态算
    必需条目会算错一条），`render_project_docs` 读 `project_skills_dir()`（prd-style 的落点）。

    **能力边界**：链接建不成时真写会退回 `.claude/skills/` 真目录，预演看不见这一格——
    它要等真的去建才知道，而预演不建。三条建链接的分支都按「建得成」报。
    """
    neutral = project_dir / SKILLS_DIR_NEUTRAL
    cc = project_dir / SKILLS_DIR_CC
    if skills_link_dangling(project_dir):
        return None, True, SKILLS_DIR_NEUTRAL   # 悬空：不建、不删，链接对象仍在
    if neutral.is_dir() and skills_cc_form(project_dir) == SKILLS_ABSENT:
        return ((cc, f"目录链接 -> {SKILLS_DIR_NEUTRAL.as_posix()}（补建；clone 侧的到达路径）"),
                True, SKILLS_DIR_NEUTRAL)
    if neutral.exists() or cc.exists() or cc.is_symlink():
        return None, skills_link_present(project_dir), project_skills_dir(project_dir)
    return ((neutral, f"目录（项目 skills 真实源）＋ {SKILLS_DIR_CC.as_posix()} 目录链接指向它"
                      f"（链接建不成时退回 {SKILLS_DIR_CC.as_posix()}/ 真目录）"),
            True, SKILLS_DIR_NEUTRAL)


def plan_gitignore(project_dir: Path, link_after: bool):
    """预演 `_ensure_gitignore`：返回 `(bucket, 说明)`；`bucket` 为 None 表示本次不动它。

    三条写入分支各自调的判定与真写完全同源：旧写法升级问 `upgrade_skills_ignore_bytes`
    （纯函数）与 `upgrade_sidecar_rule_text`（纯函数），缺失扫描问 `gitignore_required_entries_for`
    ＋ `gitignore_covers`，marker 在不在问 `_has_managed_marker`。顺序也照真写：字节级升级 → 文本级升级 → 扫描。
    """
    gi = project_dir / ".gitignore"
    if not gi.exists():
        return PLAN_BUCKET_NEW, "从模板落一份完整的（含 workframe Git 策略必需条目）"
    try:
        raw = gi.read_bytes()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        return PLAN_BUCKET_MOD, f"读不出来（{type(e).__name__}）——真跑到这一步会报错，先确认这个文件"
    upgrades = []
    if link_after and upgrade_skills_ignore_bytes(raw) is not None:
        upgrades.append(f"managed block 里 {SKILLS_LINK_IGNORE_LINE}/ 的旧写法就地升级为 {SKILLS_LINK_IGNORE_LINE}")
        text = upgrade_skills_ignore_bytes(raw).decode("utf-8-sig")
    text, sidecar_upgraded = upgrade_sidecar_rule_text(text, state_dir_rel(project_dir))
    if sidecar_upgraded:
        upgrades.append("managed block 里状态目录的整目录忽略行升级为成对写法（记忆 sidecar 此前被连坐忽略）")
    missing = [e for e in gitignore_required_entries_for(state_dir_rel(project_dir), link_after)
               if not gitignore_covers(text, e)]
    if missing and not _has_managed_marker(text):
        upgrades.append(f"末尾追加 Workframe managed block（缺 {', '.join(missing)}）")
    elif missing:
        upgrades.append(f"已有 managed marker 却缺 {', '.join(missing)} ——**本脚本不改它**，只报告给你手工补")
    if not upgrades:
        return None, "条目齐全，本次不改"
    changing = [u for u in upgrades if "不改它" not in u]
    if not changing:
        return None, upgrades[0]
    return PLAN_BUCKET_MOD, "；".join(upgrades) + "。你原有的规则一个不动，**本项不落备份**"


def plan_entries(project_dir: Path, params, subscription, home_dir=None):
    """算出本次将要建 / 改 / 跳过的**每一个文件**，分四格返回。**零写入：只 stat 与读。**

    返回 `(buckets, untouched, warnings)`：
      - `buckets` = `{PLAN_BUCKET_NEW: [(显示路径, 说明)], PLAN_BUCKET_MOD: [...],
        PLAN_BUCKET_OUT: [...], PLAN_BUCKET_KEEP: [...]}`
      - `untouched` = 第四格的项数，**由该格现算**（`len(buckets[PLAN_BUCKET_KEEP])`）——
        名单与计数同源，不会漂
      - `warnings` = 要额外说一句的（模板缺失 / settings 读不出来这类）

    **第四格为什么必须点名**：用户自己写的 `CLAUDE.md` 落在这一格（本脚本对已存在的
    CLAUDE.md / AGENTS.md 一律跳过），它是确认页上「改写用户既有文件」的几项里标准最严的
    一项，却曾经连名字都不出现、只被算进一个计数。**这一格断言的仍然只有「本脚本不动它」**
    （见 `PLAN_KEEP_SCOPE`）。

    **清单不是本函数写的**：骨架层与对话产物层问 `scaffold_targets` / `project_docs_targets`
    （真写走的同一份），订阅那两处问 `_settings_io` 的 merge 与 `would_change`（`write_settings`
    落不落笔的同一个判据），`.gitignore` 与项目 skills 到达路径见各自的 `plan_*`。
    两处手写必漂，而漂开的后果是确认页点的名与真落盘的文件对不上。

    **「不改」也要露面**：`.claude/settings.json` 与用户级市场注册表恒在各自那一格，
    本次不需要改时写「本次不改」——真机上漏掉的正是项目外那一格，它不能因为「这次恰好
    不用改」就整条消失。

    **格名与实际是否落备份**：格名照确认页规范写死，**每一项的备份实情写在它自己的说明里**
    （`.gitignore` 与 `.workframe-config.json` 走的是非破坏性改写，不落备份）。
    """
    buckets = {PLAN_BUCKET_NEW: [], PLAN_BUCKET_MOD: [], PLAN_BUCKET_OUT: [],
               PLAN_BUCKET_KEEP: []}
    warnings = []

    def rel(path):
        try:
            return Path(path).relative_to(project_dir).as_posix()
        except ValueError:
            return str(path)

    def keep(path, note=None):
        """已存在、本脚本这一次一个字节都不动的：**进第四格并点名**，不是加一个计数。"""
        shown = path if isinstance(path, str) else rel(path)
        buckets[PLAN_BUCKET_KEEP].append((shown, plan_keep_note(shown) if note is None else note))

    # .workframe-config.json —— 恒写（merge），不是 *_if_absent
    cfg = project_dir / ".workframe-config.json"
    if cfg.exists():
        buckets[PLAN_BUCKET_MOD].append((rel(cfg), "merge：刷新框架字段（project_name / "
                                                   "framework_version），你已有的字段一个不动；本项不落备份"))
    else:
        buckets[PLAN_BUCKET_NEW].append((rel(cfg), "项目身份与运行档位配置"))

    # 运行态的断点标记 —— `mark_setup_step` 增量写，scaffold 与 subscribe 各记一步
    setup_state = state_dir_of(project_dir) / "setup-state.json"
    if setup_state.exists():
        buckets[PLAN_BUCKET_MOD].append((rel(setup_state), "增量记一步装机断点（scaffold / "
                                                           "subscribe），已有的步骤一个不动；本项不落备份"))
    else:
        buckets[PLAN_BUCKET_NEW].append((rel(setup_state), "装机断点标记（doctor 的 setup_state 读它）"))

    # AGENTS.md —— 唯一写入点 ensure_agents_md，判据同样是「存在即跳过」
    agents_md = project_dir / "AGENTS.md"
    if agents_md.exists():
        keep(agents_md)
    else:
        buckets[PLAN_BUCKET_NEW].append((rel(agents_md), "两扇门共用的项目自有判据"))

    # 骨架层
    for kind, dst, payload in scaffold_targets(project_dir):
        if dst.exists():
            keep(dst)
            continue
        note = ""
        if kind == "copy" and not Path(payload).exists():
            note = "（模板缺失，真跑到这一步会以 exit 1 停下——插件安装不完整）"
            warnings.append(f"模板缺失：{payload}")
        buckets[PLAN_BUCKET_NEW].append((rel(dst), note))

    # 项目 skills 的到达路径
    skills_entry, link_after, skills_rel = plan_skills_reach(project_dir)
    if skills_entry:
        buckets[PLAN_BUCKET_NEW].append((rel(skills_entry[0]), skills_entry[1]))

    # .gitignore
    gi_bucket, gi_note = plan_gitignore(project_dir, link_after)
    if gi_bucket:
        buckets[gi_bucket].append((".gitignore", gi_note))
    else:
        # 这一支的理由是 `plan_gitignore` 算出来的（条目齐全 / 有 marker 却缺条目只报告不改），
        # 比通用说明更具体，所以显式传它、不套 `plan_keep_note`
        keep(".gitignore", gi_note)

    # 对话产物层（只在带 --params 时写）
    if params:
        for kind, dst, payload in project_docs_targets(project_dir, params, skills_rel):
            if dst.exists():
                keep(dst)
                continue
            note = ""
            if kind == "render_strict" and not Path(payload[0]).exists():
                note = "（模板缺失，真跑到这一步会以 exit 1 停下——插件安装不完整）"
                warnings.append(f"模板缺失：{payload[0]}")
            buckets[PLAN_BUCKET_NEW].append((rel(dst), note))

    # 订阅两处 —— 不带 --write-subscription 时这两行一行都不出现（此时脚本一个字节都不写 settings）
    if subscription:
        market = str(subscription.get("marketplace_name") or "").strip()
        source = subscription.get("source")
        settings_path = project_dir / ".claude" / "settings.json"
        try:
            merged, _notes = _settings_io.merge_project_subscription(
                _settings_io.read_settings(settings_path), market, source)
            will_write, existed = _settings_io.would_change(settings_path, merged)
        except _settings_io.SettingsError as e:
            buckets[PLAN_BUCKET_MOD].append((rel(settings_path),
                                             f"**读 / merge 不过：{e}** —— 真跑到这一步会以 exit 1 停下"))
            warnings.append(f"项目 settings 不可用：{e}")
            will_write = existed = None
        if will_write is not None:
            # 「改不改」问 `would_change`，「**为什么**不改」问 `landed_project_source`——
            # 两个判据都与真写同源。少了后面那个，同名市场源不同的那一支会被说成
            # 「已是目标状态」，而真实语义是「这个名字已被另一个框架副本占用」。
            landed = _settings_io.landed_project_source(merged, market)
            conflict = landed != source
            what = (f"merge 两个键（extraKnownMarketplaces[{market}] / enabledPlugins[core@{market}]），"
                    f"你原有的键一个不动")
            conflict_tail = (f"；**但市场 {market} 已声明为 {landed}，与本次的 {source} 不同——"
                             f"保留原声明不覆盖**，本次传入的源不会被写入。"
                             f"**这不是「已经对了」**：项目订阅的可能不是你这次选的那个框架副本")
            if not existed:
                buckets[PLAN_BUCKET_NEW].append((rel(settings_path), what + "；受保护资产"))
            elif will_write:
                buckets[PLAN_BUCKET_MOD].append(
                    (rel(settings_path),
                     what + "；**受保护资产**，原文件先备份到 logs/"
                     + (conflict_tail if conflict else "")))
            elif conflict:
                buckets[PLAN_BUCKET_MOD].append(
                    (rel(settings_path),
                     f"**一个字节不动，但这不是「已经对了」**：落盘的仍是原来那个源 {landed}，"
                     f"本次传入的 {source} 没有被写入——项目订阅的可能不是你这次选的那个框架副本"))
            else:
                buckets[PLAN_BUCKET_MOD].append((rel(settings_path),
                                                 "两项声明已是目标状态，**本次不改**（零字节、无备份）"))

        # 用户级市场注册表 —— 项目外，恒在第三格，哪怕本次不需要改
        home = Path(home_dir) if home_dir else None
        try:
            home = home or Path.home()
        except Exception as e:
            buckets[PLAN_BUCKET_OUT].append(("~/.claude/plugins/known_marketplaces.json",
                                             f"定位不到用户主目录（{type(e).__name__}）——真跑时这一项只警告、不拖垮装机"))
            home = None
        if home is not None:
            reg = home / ".claude" / "plugins" / "known_marketplaces.json"
            try:
                reg_merged, _n = _settings_io.merge_known_marketplaces(
                    _settings_io.read_settings(reg), market, source,
                    subscription.get("install_location"))
                reg_write, reg_existed = _settings_io.would_change(reg, reg_merged)
                # 同上：「为什么不改」也问共用判据。注册表这一侧 `merge_known_marketplaces`
                # 在冲突时直接早退，所以冲突必然蕴含「不改」，但判据照样按结构取、不靠这个巧合。
                reg_landed = _settings_io.landed_registry_source(reg_merged, market)
                reg_conflict = reg_landed != source
                if not reg_existed:
                    note = f"补一条本市场（{market}）的注册条目——**这是项目外的写入**，新建文件"
                elif reg_write and not reg_conflict:
                    note = (f"merge 本市场（{market}）的注册条目，其余条目一个不动——"
                            f"**这是项目外、会改你既有文件的写入**；原文件先备份到 <目标>/logs/")
                elif reg_conflict:
                    note = (f"**这个市场名已被别的源占用**：本机上 {market} 仍注册为 {reg_landed}，"
                            f"不是本次的 {source}——**本次未覆盖**。"
                            f"**别读成「本机已经注册好了」**：这台机器上这个名字指向的是"
                            f"另一个框架副本；要换源请自行处理")
                else:
                    note = f"已有同源条目（{market}），**本次不改**（零字节、无备份）"
            except _settings_io.SettingsError as e:
                note = f"**读 / merge 不过：{e}** —— 真跑时这一项只警告并给手动补丁，不拖垮装机"
                warnings.append(f"用户级市场注册表不可用：{e}")
            buckets[PLAN_BUCKET_OUT].append((str(reg), note))

    # 计数由名单现算：名单与个数两处手写必漂，而漂开的后果正是「个数说 1、名字没人报」
    return buckets, len(buckets[PLAN_BUCKET_KEEP]), warnings


def print_plan(project_dir: Path, params, subscription, dir_existed: bool):
    """把 `plan_entries` 的结果打成确认页可以**逐字贴**的动作清单。零写入。

    **分档在这里做，不在确认页那层做**（判据见 `plan_itemized`）：「项目内·新建」与
    「已存在·本脚本不覆盖」两格里的纯骨架文件折叠成一行计数，受保护资产与「改已有」
    那两格恒逐项。**一项都不会消失**——折叠那一行自己带计数，格头的总数也仍是
    `plan_entries` 的真实项数，两者相加可对账。
    要逐项全量时读 `plan_entries` 的返回值（单测走的就是这条路）。

    **第四格逐项点名，不是只报个数**：用户自己写的 `CLAUDE.md` / `AGENTS.md` 落在那里。
    它断言的边界见 `PLAN_KEEP_SCOPE`——本脚本不动它们，谁在之后写回它们是调用方的事。
    """
    buckets, untouched, warnings = plan_entries(project_dir, params, subscription)
    print(f"[plan] 预演模式：下面是本次将要建 / 改的每一个文件。**本次运行一个字节都不写**，"
          f"目标目录也不会被创建。")
    print(f"[plan] 目标：{project_dir}"
          + ("（已存在）" if dir_existed else "（还不存在，真跑时会建出来）"))
    if not subscription:
        print("[plan] 本次不带 --write-subscription ⇒ 不写任何 settings、项目外零写入。")
    for bucket in (PLAN_BUCKET_NEW, PLAN_BUCKET_MOD, PLAN_BUCKET_OUT, PLAN_BUCKET_KEEP):
        items = buckets[bucket]
        print(f"[plan]")
        print(f"[plan] {bucket}（{len(items)} 项）")
        if not items:
            print("[plan]    （无）")
        # 分档只发生在「项目内·新建」与「已存在·本脚本不覆盖」两格，且两格共用同一份判据
        # （`plan_itemized`）——在一格里要你过目的东西，不会在另一格里退化成一个计数。
        # 「改已有」那两格每一行都动你已有的东西，恒逐项。
        folded = ([it for it in items if not plan_itemized(it[0])]
                  if bucket in (PLAN_BUCKET_NEW, PLAN_BUCKET_KEEP) else [])
        shown = [it for it in items if it not in folded]
        for path, note in shown:
            if not note and bucket == PLAN_BUCKET_NEW:
                note = plan_itemized_why(path)
            print(f"[plan]    {path}" + (f"  —— {note}" if note else ""))
        if folded:
            tops = sorted({(str(p).replace("\\", "/").split("/")[0] or p) for p, _n in folded})
            tail = (f"（{len(shown)} + {len(folded)} = {len(items)}，与上面格头的总数对得上）")
            if bucket == PLAN_BUCKET_NEW:
                print(f"[plan]    …… 另 {len(folded)} 个骨架文件（{' / '.join(tops)} 下）"
                      f"—— 全是新建，**不覆盖任何已有文件**，你没有要为它们做的决定，"
                      f"故不逐项列出{tail}")
            else:
                print(f"[plan]    …… 另 {len(folded)} 个已存在的骨架文件（{' / '.join(tops)} 下）"
                      f"—— 同样一律不覆盖，且它们是框架自己的骨架、不是你写的东西，"
                      f"故不逐项列出{tail}")
        if bucket == PLAN_BUCKET_KEEP and items:
            print(f"[plan]    {PLAN_KEEP_SCOPE}")
    print("[plan]")
    if untouched:
        print(f"[plan] 上面第四格那 {untouched} 个文件已存在，本脚本一律不覆盖；"
              f"前三格才是本次会建 / 会改的。")
    for w in warnings:
        print(f"[plan] ! {w}")
    print("[plan] 以上由 --print-plan 算出，与真跑走同一份清单与同一套判据；"
          "把它逐字贴进确认页的动作清单，不要凭记忆复述。")
    return 0


def main():
    _force_utf8_io()
    parser = argparse.ArgumentParser(
        description="Initialize workframe scaffold in an existing project"
    )
    parser.add_argument(
        "--project", default=".",
        help="Target project directory (default: current directory)",
    )
    parser.add_argument(
        "--params",
        help="JSON file with dialogue-collected values; enables CLAUDE.md / modules / "
             "peripheral-asset rendering (see PARAM_REQUIRED / PARAM_DEFAULTS)",
    )
    parser.add_argument(
        "--create-missing", action="store_true",
        help="Create the target directory when it does not exist",
    )
    parser.add_argument(
        "--require-empty", action="store_true",
        help="新建路径专用：目标必须不存在或近空，否则拒绝执行（防止把已有项目误当新项目）",
    )
    parser.add_argument(
        "--write-subscription", action="store_true",
        help="写两项订阅声明（项目 settings）＋ 用户级市场注册表；"
             "须与 params 的 subscription 块成对出现（见 preflight_subscription）",
    )
    parser.add_argument(
        "--print-plan", action="store_true",
        help="只读预演：按同一份 params ＋ 同一个目标目录算出本次将要建 / 改的每一个文件"
             "（含项目外的用户级市场注册表）并打印，**一个字节都不写**。"
             "把真跑那条命令原样加上本参数即可——两边走同一套 preflight，退出码对齐",
    )
    args = parser.parse_args()

    project_dir = Path(args.project).resolve()
    # 目录**到 preflight 之后才建**（见下方 mkdir 处）。这里只判存在性，不落任何盘。
    dir_existed = project_dir.exists()
    if not dir_existed:
        if not args.create_missing:
            print(f"[scaffold] ERROR: project directory does not exist: {project_dir}")
            print("[scaffold]        pass --create-missing to create it")
            return 1
    elif not project_dir.is_dir():
        # 只对**已存在**的路径问「是不是目录」：mkdir 挪后之后，新建路径在这一步还不存在，
        # 无守卫地问会让 --create-missing 整条正常路径当场退 1（实测报「path is not a
        # directory」，连坏参数那组的错误文案都被顶掉）。
        print(f"[scaffold] ERROR: path is not a directory: {project_dir}")
        return 1

    # `and dir_existed`：`_substantive_entries` 对不存在的目录抛 FileNotFoundError（OSError
    # 子类），会被下面的 except 判成「读不出来」退 3。目录不存在这一态本就是「空」，直接放行。
    if args.require_empty and dir_existed:
        try:
            leftover = _substantive_entries(project_dir)
        except OSError as e:
            print(f"[scaffold] ERROR: 无法枚举目标目录内容（{type(e).__name__}: {e}）: {project_dir}")
            print("[scaffold]        「读不出来」不等于「是空的」——按新建硬来可能正踩在一个已有")
            print("[scaffold]        项目上并跳过接入流程。请确认路径与权限后重试。")
            return 3
        if leftover:
            print(f"[scaffold] ERROR: 目标目录已有内容，拒绝按「新建」处理: {project_dir}")
            print(f"[scaffold]        实质内容: {', '.join(leftover[:8])}"
                  + (" …" if len(leftover) > 8 else ""))
            print("[scaffold]        这看起来是个已有项目。若要让它用上 workframe，走**接入**流程——")
            print("[scaffold]        接入会做存量资料分析与 CLAUDE.md 整合，按「新建」硬来会跳过这两步，")
            print("[scaffold]        结果是存量分析与 CLAUDE.md 整合被跳过——已有 CLAUDE.md 时")
            print("[scaffold]        落盘验收会报缺 `@AGENTS.md` 导入，但跳过的整合补不回来，仍需返工。")
            print("[scaffold]        确实要在这里建独立子项目的话，换一个不存在的子目录路径。")
            return 3

    params = None
    subscription = None
    try:
        if args.params:
            params = load_params(Path(args.params).resolve())
            preflight_params(params)   # 必须在写配置与建目录之前（见该函数 docstring）
        # 订阅参数同样前置校验：它写的是受保护资产，坏参数必须在建目录之前就退出
        subscription = preflight_subscription(params, args.write_subscription)
    except ParamError as e:
        print(f"[scaffold] ERROR: {e}")
        print("[scaffold]        参数无效，未写入任何文件——修正 params.json 后重跑。")
        return 1

    # 只读预演在这里出口：**preflight 之后、mkdir 之前**。位置不是随意选的——
    # 放在 preflight 之后，params 缺 subscription 块 / 目标非空这类真跑会拒的情况，预演
    # **同样拒、同样的退出码**（判据与文案都是上面那一份，不是复制来的第二份）；放在 mkdir
    # 之前，预演才真的连目标目录本身都不创建。
    if args.print_plan:
        return print_plan(project_dir, params, subscription, dir_existed)

    # 目标目录到这里才建：preflight 已过，参数确定合法。建在 preflight 之前会在坏参数
    # 退出后留下一个空目录，而后果不是「脏目录」是**失败痕迹被抹平**——下次重跑不再需要
    # `--create-missing`（命令形态与首次不同），`--require-empty` 对空目录放行（它本是用来
    # 分辨「新建 vs 已有项目」的），launcher 断点续做据此判目标目录来历时同样失明。
    # 顺序由 `check_scaffold_preflight_before_write` 断言 i_pre < i_mkdir 防回退。
    if not dir_existed:
        project_dir.mkdir(parents=True, exist_ok=True)
        print(f"[scaffold] created target directory {project_dir}")

    # CONFIG_USER_FIELDS 总是写（缺失才写，已有值绝不覆盖）。不带 --params 时取 PARAM_DEFAULTS——
    # 无人值守 / CI 场景没有对话可采集，但配置不能是残缺的：doctor 第 0 组把三个枚举字段缺失
    # 判为 error，留空等于让每个非对话安装出来就是红的。默认值本身也正是 launcher 推不出来时
    # 会填的。`source[k]` 无兜底取值的安全性由模块顶部的 CONFIG_USER_FIELDS ⊆ PARAM_DEFAULTS
    # 自检保证。
    source = params or PARAM_DEFAULTS
    user_fields = {k: source[k] for k in CONFIG_USER_FIELDS}
    config_path, config_warning = write_workframe_config(
        project_dir,
        user_fields=user_fields,
        project_name=params["project_name"] if params else None,
    )
    if config_warning:
        print(f"[scaffold] WARNING: {config_warning}")
    else:
        print(f"[scaffold] wrote {config_path}")

    # params 穿进去只为渲染 AGENTS.md（它的唯一写入点在 ensure_agents_md）。带 params 时
    # role_profile 已过 preflight；不带时取自配置，配置里写着 catalog 没有的档位会在这里抛。
    try:
        created, skipped = ensure_project_scaffold(project_dir, params)
    except ParamError as e:
        print(f"[scaffold] ERROR: {e}")
        print("[scaffold]        AGENTS.md 渲染失败，骨架未写——.workframe-config.json 的 role_profile"
              " 须是 role-profile-catalog.md 里的档位，改好后重跑。")
        mark_setup_step(project_dir, "scaffold", "failed", str(e)[:80])
        return 1

    if params:
        try:
            render_project_docs(project_dir, params, created, skipped)
        except ParamError as e:
            print(f"[scaffold] ERROR: {e}")
            return 1

    # 模板缺失 ≠ 目标已存在。两者此前都只进 skipped，主流程照样报 scaffold ok，
    # 用户拿到一个「成功」的半成品骨架。插件不完整属安装问题，
    # 记 failed 并非零退出，让 launcher 当场看见。
    missing_templates = [s for s in skipped if "(missing template:" in s]
    if missing_templates:
        print(f"[scaffold] ERROR: {len(missing_templates)} 个模板文件缺失——插件安装不完整，"
              f"骨架产物不完整：")
        for m in missing_templates[:8]:
            print(f"[scaffold]   {m}")
        if len(missing_templates) > 8:
            print(f"[scaffold]   …… 另 {len(missing_templates) - 8} 项")
        print("[scaffold] 重装 core 插件后重跑；不要在此基础上继续初始化。")
        mark_setup_step(project_dir, "scaffold", "failed",
                        f"{len(missing_templates)} 个模板缺失")
        return 1

    # 断点标记第一步由脚本落，不靠 launcher 记得——中途断了才有依据。
    # config 有 warning 时本步**不算成功**：末行退出码已是 1，setup-state 必须一致，
    # 否则 launcher 按非零退出码重试，doctor 的 setup_state 却认为这步早已完成。
    if config_warning:
        mark_setup_step(project_dir, "scaffold", "failed", str(config_warning)[:80])
    else:
        mark_setup_step(project_dir, "scaffold")

    print(f"[scaffold] created={len(created)} skipped={len(skipped)}")
    for p in created:
        print(f"[scaffold]   + {p}")

    # skipped 默认只报计数，但其中的 WARNING 是用户必须看见的冲突提示（如 marker 外的
    # 整目录忽略规则压制 sidecar）——藏在计数里等于没说
    warnings = [s for s in skipped if "WARNING:" in s]
    for w in warnings:
        print(f"[scaffold]   ! {w}")

    # 订阅放在骨架之后，两件事都靠这个顺序：①`logs/` 此刻已建出来，备份有地方落；
    # ②`.gitignore` 的 managed block 此刻已就位，而 `logs/` 在它的条目里 ⇒ 备份不进仓。
    # **managed 块里没有任何一条覆盖 `*.bak-*`**，所以备份落在原文件旁边是不行的
    # （实测 `git check-ignore` rc=1，会进用户的未跟踪面）——落点由 `backup_dir` 显式给。
    # 失败也要留痕——setup-state 里 `subscribe: failed` 正是 launcher 断点续做与
    # doctor 判「装到哪一步」的依据。
    subscription_ok = True
    if subscription:
        subscription_ok, sub_lines, sub_detail = write_subscription(project_dir, subscription)
        for ln in sub_lines:
            print(f"[scaffold] {ln}")
        # detail 非空时一起落盘：`ok: <detail>` 仍以 `ok` 开头，doctor 的失败判定
        # （`not str(v).startswith("ok")`）不受影响，但「装机成功**但是**…」这半句
        # 从此有了会话结束后还在的载体
        mark_setup_step(project_dir, "subscribe",
                        "ok" if subscription_ok else "failed", sub_detail)

    print("[scaffold] --- next step ---")
    print("[scaffold] Restart your Claude Code session to activate hooks and the injected discipline,")
    print("[scaffold] then run workframe-doctor to verify.")
    return 1 if (config_warning or not subscription_ok) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:
        # 意外异常的人话兜底：已知错误路径（参数校验/目录判定/params 解析）各自有
        # 人话消息，这里只兜没预料到的——先讲清楚发生了什么，再给完整堆栈供报 issue
        print(f"[scaffold] ERROR: 脚手架执行中断——{type(e).__name__}: {e}")
        print("[scaffold]        常见原因：目标目录无写权限 / 路径被占用或只读 / 磁盘满。")
        print("[scaffold]        已写入的文件不会回滚；排除原因后重跑即可（幂等，已有文件自动跳过）。")
        print("[scaffold]        若判断是框架 bug，请带下面的完整堆栈提 issue：")
        sys.stdout.flush()  # 管道场景下 stdout 有缓冲、stderr 无——不 flush 堆栈会插到人话前面
        import traceback
        traceback.print_exc()
        sys.exit(1)
