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
    # 插件根见目标项目 .claude/workframe-state/plugin-root.txt（SessionStart hook 维护）

行为：
    - merge 模式写 .workframe-config.json（框架字段刷新，用户字段保留，损坏不覆盖）
    - 初始化 projects/ + .claude/workframe-state/ + .claude/agent-memory/ + logs/ 骨架
    - 确保 .gitignore 含 Git 策略必需条目（非破坏性追加 managed block）
    - 带 --params 时额外渲染 CLAUDE.md / modules 总图 / 周边资产层，并断言占位符零残留
    - 已存在文件一律不覆盖；stdout 打印 created / skipped 摘要
    - **不做** plugin 订阅与 rules 同步——那是两条 `claude plugin` 命令与
      `sync-rules.py --project` 的事，调用方按顺序自己跑（见 launcher setup skill §5）
"""

import argparse
import io
import json
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
    整合）。目标已有 CLAUDE.md 时 doctor 的 claude_md 会以 error 抓出契约段缺失，但**已经
    跳过的存量分析与整合补不回来**，仍要返工走接入流程；目标没有 CLAUDE.md 时 scaffold
    渲染的是模板版、四段齐全，验收看不出走错了路径。

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


# Baseline core roles — plugin 提供的 4 个通用角色，scaffold 必须为它们预创建 agent-memory 骨架
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


# Git 策略要求 .gitignore 必须包含的条目；_ensure_gitignore 在 .gitignore 已存在时做缺失扫描。
# 详见 plugins/core/reference/project-architecture.md §Git 策略
#
# 这是**检测用**的语义清单，故意写成目录形态——gitignore_covers 会认 `dir/`、`/dir`、
# `dir/*` 等一切等价写法。要往 .gitignore 里**写**的字面内容见 GITIGNORE_MANAGED_LINES，
# 两者不可互换：检测要宽（认等价形态），写入要精确（sidecar 例外必须成对出现）。
GITIGNORE_REQUIRED_ENTRIES = [
    ".claude/settings.local.json",
    ".claude/workframe-state/",
    "logs/",
    "tmp/",
    ".tmp/",
]

# managed block 实际写入的字面行。与上面的差别只在 workframe-state：必须写成
# `/*` + `!` 成对形态，否则 memory-index.json 这个 sidecar 会被连坐忽略——
# git 不会重新纳入被排除父目录下的文件，父目录整个被忽略时 `!` 静默失效。
# 曾经两处共用一个常量，结果「接入已有项目」路径追加的是整目录形态，sidecar 被忽略、
# 而缺失扫描又判「条目齐全」，于是永不自我纠正、用户全程无感（沙盒实证）。
GITIGNORE_MANAGED_LINES = [
    ".claude/settings.local.json",
    ".claude/workframe-state/*",
    "!.claude/workframe-state/memory-index.json",
    "logs/",
    "tmp/",
    ".tmp/",
]


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
    """
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


SIDECAR_REL = ".claude/workframe-state/memory-index.json"


def _strip_line(raw: str) -> str:
    """按行取有效内容。`lstrip("\\ufeff")` 不能省——Windows 上手工编辑过的 .gitignore
    常带 UTF-8 BOM，BOM 粘在首行会让 marker 比较与规则匹配全部失配：旧 managed block
    识别不出来 → 存量升级静默不执行（实测 upgraded=0、成对写法 0 行）。"""
    return raw.lstrip("﻿").strip()


def _git_says_sidecar_ignored(project_dir: Path):
    """问 git：sidecar 到底被不被忽略、被哪一行忽略。

    **git 才是最终裁决者**。自己枚举规则形态永远不全——`.claude/`、`workframe-state/`、
    `**/.claude/workframe-state/` 都能连坐吞掉 sidecar，穷举是打不完的地鼠。

    返回：`(行号, 规则原文)` 表示被该行忽略；`False` 表示确认未被忽略；
    `None` 表示 git 不可用 / 不在仓内，交给字面 fallback。
    """
    try:
        r = subprocess.run(
            ["git", "check-ignore", "-v", SIDECAR_REL],
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

    第四种失败形态（前三种见 gitignore_covers / GITIGNORE_MANAGED_LINES /
    _upgrade_managed_sidecar_rule 的注释）：用户在接入前自己往 .gitignore 加过
    `.claude/workframe-state/`。此时——

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
    core = ".claude/workframe-state"
    shadowing = {core + "/", core, "/" + core + "/", "/" + core,
                 ".claude/", ".claude", "/.claude/", "/.claude",
                 "workframe-state/", "workframe-state",
                 "**/.claude/workframe-state/", "**/.claude/"}
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


def _upgrade_managed_sidecar_rule(gi: Path, text: str, created_list):
    """把 managed block 里的旧写法 `.claude/workframe-state/` 就地升级为成对形态。

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
    old_rule = ".claude/workframe-state/"
    new_rule = ".claude/workframe-state/*"
    exception = "!.claude/workframe-state/memory-index.json"
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
    gi.write_text(new_text, encoding="utf-8", newline="")
    created_list.append(
        f"{gi} (upgraded managed block: {old_rule} -> {new_rule} + {exception}; "
        f"记忆 sidecar 此前被连坐忽略)"
    )
    return new_text, True


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

    # Case 1: 不存在 → 整份从模板复制
    if not gi.exists():
        if not template.exists():
            skipped_list.append(f"{gi} (missing template: {template})")  # 前缀须与主流程识别一致
            return
        gi.write_text(template.read_text(encoding="utf-8"), encoding="utf-8", newline="")
        created_list.append(str(gi))
        return

    # Case 2 / 3: 已存在 → 先升级旧写法，再扫描必需条目
    text = gi.read_text(encoding="utf-8")
    text, upgraded = _upgrade_managed_sidecar_rule(gi, text, created_list)

    # marker 外的整目录规则会压制 managed block 里的 `!` 例外——只报告不代改
    shadow = find_sidecar_shadowing_rule(text, project_dir)
    if shadow:
        skipped_list.append(
            f"{gi} WARNING: 第 {shadow[0]} 行 `{shadow[1]}` 在 Workframe managed block 之外，"
            f"会连坐忽略 .claude/workframe-state/memory-index.json（记忆 sidecar）。"
            f"git 不会重新纳入被排除父目录下的文件，managed block 里的 `!` 例外对它无效。"
            f"要让 sidecar 进 git，请把该行改成 `.claude/workframe-state/*`；"
            f"若你有意忽略整个目录（如记忆含敏感内容），忽略本条提示即可"
        )
    missing = [
        entry for entry in GITIGNORE_REQUIRED_ENTRIES
        if not gitignore_covers(text, entry)
    ]
    if not missing:
        # 刚升级过就不再记 skipped——同一文件既「已改」又「跳过」会让调用方读不懂
        if not upgraded:
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
    block_lines = [
        "",  # 与上文留一空行
        GITIGNORE_MANAGED_BEGIN,
        *GITIGNORE_MANAGED_LINES,
        GITIGNORE_MANAGED_END,
        "",  # 末尾换行
    ]
    if not text.endswith("\n"):
        text += "\n"
    new_text = text + "\n".join(block_lines)
    gi.write_text(new_text, encoding="utf-8", newline="")
    created_list.append(
        f"{gi} (appended Workframe managed block with required entries: "
        f"{GITIGNORE_MANAGED_LINES})"
    )


SETUP_STATE_SCHEMA = "workframe.setup-state.v1"


def mark_setup_step(project_dir: Path, step: str, status: str = "ok", detail=None):
    """在 `.claude/workframe-state/setup-state.json` 里**增量**记一步。

    为什么由脚本写第一步、且必须增量：断点标记是**为中断准备的**，而早期设计把它放在
    launcher 全流程的最后一步一次性写——流程真在中途断了（CLI 卡住 / 会话崩 / 用户中止），
    就根本走不到那一步，文件压根不存在。为中断设计的机制反过来要求流程别中断，自我否定。
    现在改成：scaffold 一成功就落 `scaffold: ok`（代码保证，不靠模型记得），
    后续每步由 launcher 紧跟着追加。中途断在哪，文件里就停在哪。
    """
    f = project_dir / ".claude" / "workframe-state" / "setup-state.json"
    data = {"__schema__": SETUP_STATE_SCHEMA, "steps": {}}
    if f.exists():
        try:
            existing = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                data = existing
                data.setdefault("__schema__", SETUP_STATE_SCHEMA)
                data.setdefault("steps", {})
        except Exception:
            pass  # 损坏就重建，这份文件是派生状态，不是用户数据
    data["steps"][step] = status if detail is None else f"{status}: {detail}"
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
    f = project_dir / ".claude" / "workframe-state" / "setup-state.json"
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


def ensure_project_scaffold(project_dir: Path):
    """初始化最小 projects/ + .claude/workframe-state/ + .claude/agent-memory/ + logs/ 骨架。

    设计原则：
      - 已存在文件一律不覆盖（跳过并记录到 skipped）
      - 不创建任何 project_type 业务目录（旧项目类型未知，旧项目可能已有自己的业务目录）
      - 复用 templates/ 下的模板，避免多处各写一份
      - core 4 角色的 agent-memory 骨架预创建（plugin baseline 承诺，消除对 Write 工具
        隐式 mkdir 行为的依赖）

    Returns: (created_paths, skipped_paths)
    """
    created = []
    skipped = []

    # projects/ — 模板复制
    proj_dir = project_dir / "projects"
    proj_template_map = [
        (SCAFFOLD_TEMPLATES_DIR / "board-template.yaml",
         proj_dir / "board.yaml"),
        (SCAFFOLD_TEMPLATES_DIR / "issues-templates-template.md",
         proj_dir / "issues" / "TEMPLATES.md"),
        (SCAFFOLD_TEMPLATES_DIR / "project-changelog-template.md",
         proj_dir / "changelog.md"),
        # evals/ 下只有 3 个空目录，不给说明用户不知道那是干什么的
        (SCAFFOLD_TEMPLATES_DIR / "evals-template" / "README.md",
         proj_dir / "evals" / "README.md"),
    ]
    for src, dst in proj_template_map:
        _copy_if_absent(src, dst, created, skipped)

    # projects/ — 含占位符的模板走渲染写入
    # taxonomy 是 doc-graph-health 概念热点维度与 check_archive tags 检查的正源，
    # 装机放入才有词表可维护（此前只有巡检报告里一句「模板见 core 插件」让用户手抄）
    now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
    today = datetime.now().strftime("%Y-%m-%d")
    for tpl_name, dst, mapping in [
        ("specs-overview-template.md", proj_dir / "specs" / "overview.md",
         {"NOW_ISO": now_iso}),
        ("taxonomy-template.md", proj_dir / "specs" / "_meta" / "taxonomy.md",
         {"NOW_ISO": now_iso, "CREATED_DATE": today}),
    ]:
        _render_if_absent(SCAFFOLD_TEMPLATES_DIR / tpl_name, dst, mapping,
                          created, skipped)

    # projects/ — 空目录骨架（带 .gitkeep）
    proj_gitkeep_dirs = [
        proj_dir / "evals" / "rules",
        proj_dir / "evals" / "skills",
        proj_dir / "evals" / "agents",
        proj_dir / "proposals" / "pending",
        proj_dir / "proposals" / "applied",
        proj_dir / "proposals" / "rejected",
        proj_dir / "archive",
    ]
    for d in proj_gitkeep_dirs:
        _write_if_absent(d / ".gitkeep", "", created, skipped)

    # logs/ — 仅 .gitkeep；运行期 hook 输出 / Librarian 快照按需追加
    _write_if_absent(project_dir / "logs" / ".gitkeep", "", created, skipped)

    # .claude/workframe-state/ — 模板复制
    state_dir = project_dir / ".claude" / "workframe-state"
    state_template_map = [
        (SCAFFOLD_TEMPLATES_DIR / "activity-state-template.json",
         state_dir / "activity-state.json"),
        (SCAFFOLD_TEMPLATES_DIR / "events-template.jsonl",
         state_dir / "events.jsonl"),
        (SCAFFOLD_TEMPLATES_DIR / "memory-index-template.json",
         state_dir / "memory-index.json"),
        (SCAFFOLD_TEMPLATES_DIR / "skill-metrics-template.yaml",
         state_dir / "skill-metrics.yaml"),
    ]
    for src, dst in state_template_map:
        _copy_if_absent(src, dst, created, skipped)

    # .claude/agent-memory/shared/ — 启动必读资产，由 agent-protocols Step 0 强依赖
    shared_dir = project_dir / ".claude" / "agent-memory" / "shared"
    shared_template_map = [
        (SCAFFOLD_TEMPLATES_DIR / "shared-memory-template.md",
         shared_dir / "MEMORY.md"),
        (SCAFFOLD_TEMPLATES_DIR / "shared-notes-template.md",
         shared_dir / "notes.md"),
    ]
    for src, dst in shared_template_map:
        _copy_if_absent(src, dst, created, skipped)

    # .claude/agent-memory/<role>/ — baseline 4 个 core role 骨架
    for role in BASELINE_CORE_ROLES:
        role_dir = project_dir / ".claude" / "agent-memory" / role
        _write_if_absent(
            role_dir / "MEMORY.md",
            _role_memory_placeholder(role),
            created, skipped,
        )
        _write_if_absent(
            role_dir / "notes.md",
            _role_notes_placeholder(role),
            created, skipped,
        )

    # .gitignore — 强制对齐 §Git 策略（workframe-state/** + logs/** 必须默认不进 git；
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
}
CONFIG_USER_FIELDS = ("project_type", "dormant_profile", "role_profile")

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
    # role_profile 向 catalog 求证：不存在时在这里就抛，而不是等渲染 CLAUDE.md 才炸
    extract_role_profile_routing(params["role_profile"])


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


def extract_role_profile_routing(role_profile: str) -> str:
    """从 role-profile-catalog.md 抽取该 profile 的「CLAUDE.md 路由偏好渲染文本」代码块。

    确定性派生，不由模型转述——转述会漂移。
    """
    catalog = PLUGIN_ROOT / "reference" / "role-profile-catalog.md"
    if not catalog.exists():
        raise ParamError(f"role-profile-catalog.md not found: {catalog}")
    text = catalog.read_text(encoding="utf-8")
    section = re.search(
        rf"^### `{re.escape(role_profile)}`\n(.*?)(?=^### `|\Z)",
        text, re.S | re.M,
    )
    if not section:
        raise ParamError(f"role_profile {role_profile!r} not found in role-profile-catalog.md")
    block = re.search(r"```markdown\n(.*?)```", section.group(1), re.S)
    if not block:
        raise ParamError(f"role_profile {role_profile!r} section has no ```markdown routing block")
    return block.group(1).rstrip("\n")


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


def render_project_docs(project_dir: Path, params: dict, created_list, skipped_list):
    """渲染对话产物：CLAUDE.md + modules/ 总图 + 周边资产层。已存在文件一律不覆盖。"""
    tpl_dir = SCAFFOLD_TEMPLATES_DIR
    now_iso = datetime.now().astimezone().isoformat(timespec="seconds")
    today = datetime.now().strftime("%Y-%m-%d")

    # CLAUDE.md
    claude_md = project_dir / "CLAUDE.md"
    if claude_md.exists():
        skipped_list.append(str(claude_md))
    else:
        tpl = tpl_dir / "claude-md-template.md"
        if not tpl.exists():
            raise ParamError(f"claude-md-template.md missing: {tpl}")
        rendered = render_placeholders(
            tpl.read_text(encoding="utf-8"),
            {
                "PROJECT_NAME": params["project_name"],
                "FRAMEWORK_VERSION": read_framework_version(),
                "PROJECT_TYPE": params["project_type"],
                "CREATED_DATE": today,
                "PROJECT_ONE_LINE_GOAL": params["one_line_goal"],
                "PROJECT_BUSINESS_CONTEXT": params["business_context"],
                "PROJECT_LEVEL_ROLES": params["project_level_roles"],
                "ROLE_PROFILE_ROUTING": extract_role_profile_routing(params["role_profile"]),
                "PROJECT_SPECIFIC_CONSTRAINTS": params["project_specific_constraints"],
                "PROJECT_BUSINESS_DIRECTORIES": params["business_directories"],
            },
            "claude-md-template.md",
        )
        claude_md.write_text(rendered, encoding="utf-8", newline="")
        created_list.append(str(claude_md))

    # projects/modules/overview.md —— modules/ 体系永远启用
    overview = project_dir / "projects" / "modules" / "overview.md"
    if overview.exists():
        skipped_list.append(str(overview))
    else:
        tpl = tpl_dir / "modules-template" / "overview-template.md"
        if not tpl.exists():
            raise ParamError(f"modules overview-template.md missing: {tpl}")
        rendered = render_placeholders(
            tpl.read_text(encoding="utf-8"),
            {"PROJECT_NAME": params["project_name"], "NOW_ISO": now_iso, "TODAY": today},
            "modules-template/overview-template.md",
        )
        overview.parent.mkdir(parents=True, exist_ok=True)
        overview.write_text(rendered, encoding="utf-8", newline="")
        created_list.append(str(overview))

    # 周边资产层
    for kind in ("company-context", "my-workspace"):
        _write_if_absent(
            project_dir / kind / "README.md",
            _company_context_readme(kind),
            created_list, skipped_list,
        )

    # 项目 PRD 框架 skill —— 出厂默认版实例化到项目层；此后属于项目，框架不再覆盖
    prd_style = project_dir / ".claude" / "skills" / "prd-style" / "SKILL.md"
    if prd_style.exists():
        skipped_list.append(str(prd_style))
    else:
        tpl = tpl_dir / "project-skills" / "prd-style" / "SKILL.md"
        if not tpl.exists():
            raise ParamError(f"prd-style template missing: {tpl}")
        rendered = render_placeholders(
            tpl.read_text(encoding="utf-8"),
            {"PROJECT_NAME": params["project_name"]},
            "project-skills/prd-style/SKILL.md",
        )
        prd_style.parent.mkdir(parents=True, exist_ok=True)
        prd_style.write_text(rendered, encoding="utf-8", newline="")
        created_list.append(str(prd_style))


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
    args = parser.parse_args()

    project_dir = Path(args.project).resolve()
    if not project_dir.exists():
        if not args.create_missing:
            print(f"[scaffold] ERROR: project directory does not exist: {project_dir}")
            print("[scaffold]        pass --create-missing to create it")
            return 1
        project_dir.mkdir(parents=True, exist_ok=True)
        print(f"[scaffold] created target directory {project_dir}")
    if not project_dir.is_dir():
        print(f"[scaffold] ERROR: path is not a directory: {project_dir}")
        return 1

    if args.require_empty:
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
            print("[scaffold]        落盘验收会报契约段缺失，但跳过的整合补不回来，仍需返工。")
            print("[scaffold]        确实要在这里建独立子项目的话，换一个不存在的子目录路径。")
            return 3

    params = None
    if args.params:
        try:
            params = load_params(Path(args.params).resolve())
            preflight_params(params)   # 必须在写配置与建目录之前（见该函数 docstring）
        except ParamError as e:
            print(f"[scaffold] ERROR: {e}")
            print("[scaffold]        参数无效，未写入任何文件——修正 params.json 后重跑。")
            return 1

    # 三个用户字段总是写（缺失才写，已有值绝不覆盖）。不带 --params 时取 PARAM_DEFAULTS——
    # 无人值守 / CI 场景没有对话可采集，但配置不能是残缺的：doctor 第 0 组把三字段缺失判为
    # error，留空等于让每个非对话安装出来就是红的。默认值本身也正是 launcher 推不出来时会填的。
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

    created, skipped = ensure_project_scaffold(project_dir)

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

    print("[scaffold] --- next step ---")
    print("[scaffold] Restart your Claude Code session to activate hooks and rules,")
    print("[scaffold] then run workframe-doctor to verify.")
    return 1 if config_warning else 0


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
