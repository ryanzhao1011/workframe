#!/usr/bin/env python3
"""
tools/validate.py — 深度语义校验框架仓库

用法：
    python tools/validate.py

检查维度（v7.1）：
    结构完整性：marketplace.json / plugin.json / hooks.json / agents / skills / rules / scripts / docs / root files
    引用边界：hooks 命令引用 ${CLAUDE_PLUGIN_ROOT}；出货资产不引用仓内私有路径；tools 无硬编码用户路径
    文档完整性：6 docs + 1 root README + CHANGELOG + LICENSE
    内容归属一致性：docs/ 不重复 reference/ 详细规范

退出码：
    0 — 全部通过
    1 — 至少一项失败
"""

import ast
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path


FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]

# Markers
OK = "  ✓"
FAIL = "  ✗"


def _ok(name, extra=""):
    print(f"{OK} {name}" + (f" {extra}" if extra else ""))
    return True, None


def _fail(name, msg):
    print(f"{FAIL} {name}: {msg}")
    return False, msg


# === Structure checks ===


def check_marketplace_json_parseable():
    path = FRAMEWORK_ROOT / ".claude-plugin" / "marketplace.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("name") != "workframe":
            return _fail("marketplace_json_parseable", f"name != 'workframe', got {data.get('name')!r}")
        plugins = data.get("plugins", [])
        if not any(p.get("name") == "core" for p in plugins):
            return _fail("marketplace_json_parseable", "no 'core' plugin listed")
        return _ok("marketplace_json_parseable")
    except Exception as e:
        return _fail("marketplace_json_parseable", str(e))


def check_plugin_json_parseable():
    path = FRAMEWORK_ROOT / "plugins" / "core" / ".claude-plugin" / "plugin.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("name") != "core":
            return _fail("plugin_json_parseable", f"name != 'core', got {data.get('name')!r}")
        return _ok("plugin_json_parseable")
    except Exception as e:
        return _fail("plugin_json_parseable", str(e))


def check_hooks_json_parseable():
    path = FRAMEWORK_ROOT / "plugins" / "core" / "hooks" / "hooks.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        hooks = data.get("hooks", {})
        if not {"SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SubagentStart", "SubagentStop", "StopFailure", "ConfigChange", "SessionEnd"}.issubset(hooks.keys()):
            return _fail("hooks_json_parseable", f"missing hook event, got {list(hooks.keys())}")
        return _ok("hooks_json_parseable")
    except Exception as e:
        return _fail("hooks_json_parseable", str(e))


def _check_frontmatter(file_path, required_fields):
    text = file_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return f"missing frontmatter in {file_path.name}"
    # extract YAML between first two --- lines
    parts = text.split("---", 2)
    if len(parts) < 3:
        return f"unterminated frontmatter in {file_path.name}"
    frontmatter = parts[1]
    for field in required_fields:
        pattern = rf"^{field}\s*:"
        if not re.search(pattern, frontmatter, re.MULTILINE):
            return f"missing '{field}' in {file_path.name} frontmatter"
    return None


def check_agents_count_and_frontmatter():
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    expected = {"pm.md", "dev.md", "qa.md", "prompt-eng.md"}
    actual = {f.name for f in agents_dir.glob("*.md")}
    if actual != expected:
        return _fail("agents_count_and_frontmatter", f"expected {expected}, got {actual}")
    for f in agents_dir.glob("*.md"):
        err = _check_frontmatter(f, ["name", "description"])
        if err:
            return _fail("agents_count_and_frontmatter", err)
    return _ok("agents_count_and_frontmatter", "(4 agents, frontmatter complete)")


REQUIRED_SKILLS = {
    "librarian", "self-iteration", "task-management",
    "requirement-analysis", "feature-breakdown", "acceptance-criteria",
    "competitive-analysis", "product-metrics-design", "user-feedback-analysis",
    "technical-design", "systematic-debugging",
    "test-case-design", "code-review",
    "prompt-design", "prompt-evaluation",
    # v8.2 Phase 5 system skills
    "session-digest", "audit", "rollback", "memory-log", "maintenance-review",
    # v0.3 docs/publishing skills (PRD writer + screenshot + obsidian-* CLI integration)
    "prd-writer", "screenshot",
    "obsidian-doc-structure", "obsidian-link-audit",
    "obsidian-safe-write", "obsidian-history-check",
    # v0.2.1 onboarding skill (Agent Teams default-skip + onboarded.json marker)
    "onboard",
    # v0.3.x M3 modules/ 体系：1 docs/publishing + 4 modules-system
    "document-norms",
    "module-init", "code-to-doc", "module-index-refresh", "migrate-to-modules",
    # v0.4.0-dev：仿真型 HTML 交互 demo（docs/publishing 第 8 个，skill 总数 33 → 34）
    "html-demo",
    # v0.4.0-dev 开源准备 W1：知识网巡检自 dogfood 项目上翻（modules-system 第 5 个，34 → 35）
    "doc-graph-health",
    # v0.4.0-dev 开源准备 W1：历史需求归档自 dogfood 项目上翻（modules-system 第 6 个，35 → 36）
    "requirement-archiving",
    # v0.4.0-dev 初始化链路重构：存量资料盘点/分流/结构推荐，launcher 接入路径与项目内补料共用
    "material-intake",
}

# v0.3.x M3：modules-system 类（仅 product-work / software-mvp 项目使用）
MODULES_SYSTEM_SKILLS = {
    "module-init", "code-to-doc", "module-index-refresh", "migrate-to-modules",
    "doc-graph-health", "requirement-archiving", "material-intake",
}

SYSTEM_SKILLS = {
    "librarian", "self-iteration",
    "session-digest", "audit", "rollback", "memory-log", "maintenance-review",
    # v0.2.1: onboard 是系统级"安装/配置"流程，非业务 skill，不预加载到任何 agent context
    "onboard",
}

# v0.2.1：含 onboard。集合名保持向后兼容（"maintenance"），语义扩展为"用户主动调用且
# 必须 disable-model-invocation 的 system skill"——含原 4 个维护命令 + onboard 配置流程
USER_INVOCABLE_MAINTENANCE_SKILLS = {
    "audit", "rollback", "memory-log", "maintenance-review",
    "onboard",
}


def check_skills_count_and_frontmatter():
    skills_dir = FRAMEWORK_ROOT / "plugins" / "core" / "skills"
    actual = {d.name for d in skills_dir.iterdir() if d.is_dir()}
    if actual != REQUIRED_SKILLS:
        missing = REQUIRED_SKILLS - actual
        extra = actual - REQUIRED_SKILLS
        return _fail("skills_count_and_frontmatter", f"missing={missing}, extra={extra}")
    for skill in REQUIRED_SKILLS:
        skill_md = skills_dir / skill / "SKILL.md"
        if not skill_md.exists():
            return _fail("skills_count_and_frontmatter", f"{skill}/SKILL.md missing")
        err = _check_frontmatter(skill_md, ["name", "description"])
        if err:
            return _fail("skills_count_and_frontmatter", err)
    return _ok("skills_count_and_frontmatter", f"({len(REQUIRED_SKILLS)} skills, frontmatter complete)")


def check_core_shared_assets_complete():
    """core 插件级共享资产完整性：`reference/`（规范文档）与 `templates/`（项目脚手架模板）。

    这两组资产的消费方遍布 core——agents、多个 skill 与 `scripts/project_scaffold.py`，
    因此归属插件根而非任何单个 skill 目录。
    """
    name = "core_shared_assets_complete"
    base = FRAMEWORK_ROOT / "plugins" / "core"
    required_ref = {
        "project-architecture.md",
        "role-customization-guide.md",
        "skill-customization-guide.md",
        "role-profile-catalog.md",
        # v0.3.x M3：modules/ 体系设计文档
        "module-architecture.md",
        # 接入已有项目时把框架约定与用户原文整合成一份（与 claude-md-template 强耦合，同插件放）
        "claude-md-merge-guide.md",
    }
    required_tpl = {
        "agent-template.md",
        "skill-template.md",
        "claude-md-template.md",
        "shared-memory-template.md",
        "shared-notes-template.md",
    }
    ref_actual = {f.name for f in (base / "reference").glob("*.md")}
    tpl_md_actual = {f.name for f in (base / "templates").glob("*.md")}
    if ref_actual != required_ref:
        return _fail(name, f"reference/ mismatch: want={required_ref}, got={ref_actual}")
    if not required_tpl.issubset(tpl_md_actual):
        return _fail(name, f"templates/ missing md: {required_tpl - tpl_md_actual}")
    return _ok(name, f"(reference/ {len(required_ref)} + templates/ ≥{len(required_tpl)} md)")


def check_rules_core_exists():
    rules_dir = FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core"
    expected = {"correction-detection.md", "response-output.md", "auto-update.md", "agent-protocols.md"}
    actual = {f.name for f in rules_dir.glob("*.md")}
    if actual != expected:
        return _fail("rules_core_exists", f"expected {expected}, got {actual}")
    return _ok("rules_core_exists", "(4 core rules)")


def check_scripts_syntax():
    scripts_dir = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    tools_dir = FRAMEWORK_ROOT / "tools"
    all_scripts = list(scripts_dir.glob("*.py")) + list(tools_dir.glob("*.py"))
    for script in all_scripts:
        try:
            ast.parse(script.read_text(encoding="utf-8"))
        except SyntaxError as e:
            return _fail("scripts_syntax", f"{script.name}: {e}")
    return _ok("scripts_syntax", f"({len(all_scripts)} scripts compile)")


# === Reference boundary checks ===


def check_hooks_commands_reference_plugin_root():
    path = FRAMEWORK_ROOT / "plugins" / "core" / "hooks" / "hooks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for event, hook_groups in data.get("hooks", {}).items():
        for hg in hook_groups:
            for h in hg.get("hooks", []):
                if h.get("type") != "command":
                    continue
                cmd = h.get("command", "")
                if "${CLAUDE_PLUGIN_ROOT}" not in cmd:
                    return _fail(
                        "hooks_commands_reference_plugin_root",
                        f"{event}: command does not reference ${{CLAUDE_PLUGIN_ROOT}}: {cmd!r}",
                    )
    return _ok("hooks_commands_reference_plugin_root")



# === 本机绝对路径检测 ===
# 与 plugins/*/scripts/workframe_doctor.py 的 LOCAL_PATH_PATTERNS 同源：那边扫用户项目、
# 这边扫本仓。通用形态，不写死任何维护者用户名——换个贡献者的路径一样拦得住。
#
# 捕获组刻意排除正则元字符：检测器自身（本文件、doctor）会把这些模式作为字符串字面量
# 持有，那种情况捕获段为空、直接跳过。**不给检测器开文件级白名单是有意的**——一旦按
# 文件豁免，「检测器里写了真实路径」这一类问题就永远查不出来了，而那正是此前的实际情况。
# 四种形态：带盘符的 Windows 路径、**无盘符的 `\Users\`**（相对当前盘的绝对路径，
# 在 Windows 上同样有效，早先只认带盘符的）、POSIX 的 /Users/ 与 /home/。
_HOME_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]+Users[\\/]+|(?<![A-Za-z0-9])\\{1,2}Users\\{1,2}|/Users/|/home/)"
    # 捕获组排除：路径分隔符、空白、引号、正则元字符，以及**中文标点**——
    # 中文文档里「路径 + 。」「路径 + ，」是常态，不排除的话句号会被当成用户名的一部分
    # （闸曾因此把自己注释里的 `/home/。` 报成本机路径）。
    r"([^\\/\s\"'`,;:)\]}\[\^*+?$|({。，、；：！？（）【】「」《》…]*)"
)

# 占位段：文档里拿本机路径作示例是合法的，这些形态一律放行。
# 刻意**移除了** name / user / you / me / foo / bar / example 这类短词——它们既可能是
# 占位符，也可能是真实存在的用户名，放行等于开一个口子。代价是文档里必须用**明确的**
# 占位形态（尖括号、省略号、${VAR}、%VAR%）而不能拿一个普通单词充当占位；
# 这对读者反而更清楚，也让「是不是占位」不再需要靠猜。
_PLACEHOLDER_SEG_RE = re.compile(
    r"^(?:<[^>]*>|\.{2,}|…+|\$\{?\w+\}?|%\w+%|~"       # `…` 是中文文档里常用的省略号
    r"|username|yourname|your_name|placeholder)$",
    re.IGNORECASE,
)

# URI 里的 /home/ 与 /Users/ 是 URI 路径段，不是本机绝对路径。若匹配点之前存在一个
# 未被空白打断的 URI 前缀就跳过——否则 `https://example.com/home/alice/docs` 会被误报。
# scheme 不能只写 https?：`file:///home/alice` 同样是 URI 形态（早先只覆盖 http/https，
# file:// 被误报）。这里收常见的几种，末尾 `//` 是硬要求——避免把 `C:/home/…` 之类误判。
_URL_PREFIX_RE = re.compile(r"(?:https?|file|ftps?|s3|gs)://\S*$", re.IGNORECASE)

# 不参与文本扫描的产物与二进制
_SCAN_EXCLUDED_SEGMENTS = {"__pycache__", ".git", "node_modules", ".venv", "venv"}
# 本机私有、明确不入库的文件。git 可用时它们本就不在 ls-files 里；**fallback 走目录遍历
# 时会扫到**，于是 CLAUDE.local.md（按设计含私仓路径与本机绝对路径）被当成出货文件误报。
_SCAN_EXCLUDED_NAMES = {"CLAUDE.local.md", "settings.local.json"}
_SCAN_EXCLUDED_SUFFIXES = {
    ".pyc", ".pyo", ".pyd", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".pdf", ".zip", ".ico", ".woff", ".woff2", ".ttf",
}


def _repo_text_files():
    """全仓被跟踪的文本文件。

    git 不可用时退回目录遍历——**两条路径都是真扫描**，不存在"环境不满足就跳过"的降级
    （那种降级在最需要它的环境里最可能触发，等于没有闸）。
    """
    rels = []
    try:
        r = subprocess.run(
            ["git", "ls-files"], cwd=FRAMEWORK_ROOT,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
        if r.returncode == 0:
            rels = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    except Exception:
        rels = []
    if not rels:
        # 目录清单要与「git 跟踪什么」大致对齐——早先漏了 .github/ 与 .claude-plugin/，
        # 于是 git 不可用时 workflow 与市场清单整批不被扫描。
        roots = [
            FRAMEWORK_ROOT / "plugins", FRAMEWORK_ROOT / "tools", FRAMEWORK_ROOT / "docs",
            FRAMEWORK_ROOT / ".github", FRAMEWORK_ROOT / ".claude-plugin",
        ]
        rels = [
            p.relative_to(FRAMEWORK_ROOT).as_posix()
            for root in roots if root.is_dir()
            for p in root.rglob("*") if p.is_file()
        ]
        rels += [p.name for p in FRAMEWORK_ROOT.glob("*.md")]
        rels += [".gitignore", ".gitattributes", "LICENSE"]
    out = []
    for rel in rels:
        p = FRAMEWORK_ROOT / rel
        if not p.is_file():
            continue
        if any(seg in _SCAN_EXCLUDED_SEGMENTS for seg in p.parts):
            continue
        if p.suffix.lower() in _SCAN_EXCLUDED_SUFFIXES:
            continue
        if p.name in _SCAN_EXCLUDED_NAMES or "dev-docs" in p.parts:
            continue
        out.append(rel)
    return sorted(set(out))


def check_no_hardcoded_user_paths():
    """任何被跟踪文件都不得含本机绝对路径（占位形态的文档示例除外）。

    旧实现有两处硬伤：

    1. 禁止模式**写死成维护者本人的用户名**，换一个贡献者的路径完全查不出来；
    2. **检测器自身把要检测的名字带进了对外分发的文件**——真实用户名与真实项目名
       作为字符串字面量长在公开仓库里，工作区"干净"只是因为没人扫检测器本身。

    扫描面也只有两个目录且 `glob` 不递归，`plugins/*/skills/*/scripts/*.py` 整批
    在扫描之外。现在改为通用形态 + 占位段放行 + 全仓被跟踪文本文件。
    """
    name = "no_hardcoded_user_paths"
    files = _repo_text_files()
    offenders = []
    for rel in files:
        try:
            text = (FRAMEWORK_ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            hit = None
            for m in _HOME_PATH_RE.finditer(line):
                seg = m.group(1)
                if not seg or _PLACEHOLDER_SEG_RE.match(seg):
                    continue
                if _URL_PREFIX_RE.search(line[:m.start()]):   # 落在 URL 里，不是本机路径
                    continue
                hit = m.group(0)
                break
            if hit:
                offenders.append(f"{rel}:{lineno}: {hit!r}")
                break
    if offenders:
        return _fail(name, "; ".join(offenders[:6])
                     + (f" …… 共 {len(offenders)} 处" if len(offenders) > 6 else ""))
    return _ok(name, f"扫描 {len(files)} 个被跟踪文本文件，无本机绝对路径")


# === Docs completeness ===


def check_docs_index_complete():
    """docs/ 与它的索引双向对齐：每份文档都挂在 README 上，README 指向的都存在。

    此前是「必须恰好是这 6 个文件名」的写死清单——加一份文档就撞闸，而撞闸时它说的是
    "extra=..."，读起来像"多了个不该有的文件"，而不是"你该把它挂到索引上"。
    改成双向断言后：新增文档只要挂进 `docs/README.md` 就自然通过，忘了挂才报红——
    **拦的是"文档孤儿"这个真问题，而不是"文件数变了"这个表象**。
    """
    name = "docs_index_complete"
    docs_dir = FRAMEWORK_ROOT / "docs"
    index = docs_dir / "README.md"
    if not index.exists():
        return _fail(name, "docs/README.md 缺失——索引本身没了")

    actual = {f.name for f in docs_dir.glob("*.md")} - {"README.md"}
    index_text = index.read_text(encoding="utf-8")
    # 接受这些合法写法：`./x.md`、`x.md`、带 `#anchor` / `?query`、以及 Markdown
    # link title（`[Doc](./x.md "标题")`）。
    # `(?!\.\./)` 排除 `../plugins/...` 这类指向仓内其他目录的链接——那些不属于 docs 索引，
    # 计进来会被误判成死链。子目录同样不计：`actual` 用的是 glob("*.md") 也不下钻，两边一致。
    linked = set(re.findall(
        r"\]\(\s*<?(?!\.\./)(?:\./)?([A-Za-z0-9_.-]+\.md)"   # 可选尖括号 <./x.md>
        r"(?:[#?][^\s)>]*)?>?"                               # 可选 #anchor / ?query
        r"(?:\s+[\"'][^\"']*[\"'])?"                         # 可选 link title
        r"\s*\)", index_text))
    # 引用式链接：`[Doc][ref]` 配 `[ref]: ./x.md`——定义行单独一条正则
    linked |= set(re.findall(
        r"^\s*\[[^\]]+\]:\s*<?(?!\.\./)(?:\./)?([A-Za-z0-9_.-]+\.md)",
        index_text, re.MULTILINE))

    errors = []
    for orphan in sorted(actual - linked):
        errors.append(f"{orphan}: 未挂在 docs/README.md 索引上（文档孤儿）")
    for dangling in sorted(linked - actual):
        errors.append(f"{dangling}: docs/README.md 指向它但文件不存在（死链）")
    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name, f"{len(actual)} 份文档与索引双向对齐")



def check_root_readme_changelog_license():
    required = ["README.md", "CHANGELOG.md", "LICENSE", ".gitignore"]
    missing = [r for r in required if not (FRAMEWORK_ROOT / r).exists()]
    if missing:
        return _fail("root_readme_changelog_license", f"missing: {missing}")
    return _ok("root_readme_changelog_license")



def check_no_reference_content_in_docs():
    """Ensure docs/ does not duplicate the full detailed spec from reference/.
    Heuristic: if docs/ contains the phrase "# 项目类型样例目录" (reference/project-types-catalog.md's
    own H1), it means docs is mirroring reference full text. Only link is allowed.
    """
    docs_dir = FRAMEWORK_ROOT / "docs"
    reference_h1s = [
        "# 项目目录结构规范",
        "# 角色扩展规范",
        "# 技能扩展规范",
        "# 项目类型样例目录",
    ]
    offenders = []
    for f in docs_dir.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        for h1 in reference_h1s:
            if h1 in text:
                offenders.append(f"{f.name} contains '{h1}' (should only link, not copy)")
    if offenders:
        return _fail("no_reference_content_in_docs", "; ".join(offenders))
    return _ok("no_reference_content_in_docs")


def check_no_dev_docs_content_leaked_to_user_docs():
    """dev-docs/ content markers should not appear in docs/."""
    docs_dir = FRAMEWORK_ROOT / "docs"
    markers = [
        "## 第一节：功能依赖矩阵",
        "## 第二节：内容归属矩阵",
        # 私有笔记 source-extraction-notes.md 的标题后半段。刻意不含来源项目名——
        # 检测器不该把它要检测的名字带进对外分发的文件。
        "抽取的资产清单与脱敏记录",
        "# 方案演进",
    ]
    offenders = []
    for f in docs_dir.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        for m in markers:
            if m in text:
                offenders.append(f"{f.name} contains dev-docs marker '{m}'")
    if offenders:
        return _fail("no_dev_docs_content_leaked_to_user_docs", "; ".join(offenders))
    return _ok("no_dev_docs_content_leaked_to_user_docs")


# === v8.2 checks (Phase 0-7) ===


def check_no_forbidden_frontmatter_fields():
    """禁用非官方 frontmatter 字段：usage_count / last_used / trigger_count / category（仅顶层 frontmatter）"""
    forbidden = {"usage_count", "last_used", "trigger_count"}
    scan_files = []
    for base in [FRAMEWORK_ROOT / "plugins" / "core" / "skills",
                 FRAMEWORK_ROOT / "plugins" / "core" / "rules",
                 FRAMEWORK_ROOT / "plugins" / "core" / "agents"]:
        scan_files.extend(base.rglob("*.md"))
    offenders = []
    for f in scan_files:
        if "eval-cases" in f.parts:
            continue
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        fm = parts[1]
        for field in forbidden:
            if re.search(rf"^\s*{field}\s*:", fm, re.MULTILINE):
                offenders.append(f"{f.relative_to(FRAMEWORK_ROOT)}:{field}")
        if re.search(r"^\s*category\s*:", fm, re.MULTILINE):
            offenders.append(f"{f.relative_to(FRAMEWORK_ROOT)}:category")
    if offenders:
        return _fail("no_forbidden_frontmatter_fields", "; ".join(offenders[:5]))
    return _ok("no_forbidden_frontmatter_fields")


def check_agents_no_hardcoded_model_id():
    """5 core agents 的 frontmatter 不应含硬编码 model ID（允许 inherit/opus/sonnet/haiku 别名）"""
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    offenders = []
    for f in agents_dir.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            continue
        fm = parts[1]
        m = re.search(r"^\s*model\s*:\s*(\S+)", fm, re.MULTILINE)
        if not m:
            continue
        val = m.group(1).strip()
        if val in {"inherit", "opus", "sonnet", "haiku"}:
            continue
        if re.match(r"^claude-[a-z]+-\d", val):
            offenders.append(f"{f.name}:model={val}")
    if offenders:
        return _fail("agents_no_hardcoded_model_id", "; ".join(offenders))
    return _ok("agents_no_hardcoded_model_id")


def check_system_skills_sidecar():
    """plugins/core/.workframe-meta/system-skills.yaml 存在且列出 8 个 system skill"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / ".workframe-meta" / "system-skills.yaml"
    if not path.exists():
        return _fail("system_skills_sidecar", "sidecar missing")
    text = path.read_text(encoding="utf-8")
    missing = [s for s in SYSTEM_SKILLS if f"- {s}" not in text]
    if missing:
        return _fail("system_skills_sidecar", f"missing in sidecar: {missing}")
    return _ok("system_skills_sidecar", f"({len(SYSTEM_SKILLS)} system skills registered)")


def check_maintenance_skills_disable_model_invocation():
    """audit/rollback/memory-log/maintenance-review 必须含 disable-model-invocation: true"""
    skills_dir = FRAMEWORK_ROOT / "plugins" / "core" / "skills"
    offenders = []
    for skill in USER_INVOCABLE_MAINTENANCE_SKILLS:
        skill_md = skills_dir / skill / "SKILL.md"
        if not skill_md.exists():
            offenders.append(f"{skill}/SKILL.md missing")
            continue
        text = skill_md.read_text(encoding="utf-8")
        if not re.search(r"^\s*disable-model-invocation\s*:\s*true", text, re.MULTILINE):
            offenders.append(f"{skill} missing disable-model-invocation: true")
    if offenders:
        return _fail("maintenance_skills_disable_model_invocation", "; ".join(offenders))
    return _ok("maintenance_skills_disable_model_invocation")


def check_hooks_complete_pipeline():
    """hooks.json 必须注册 11 段链路：SessionStart / Setup / UserPromptSubmit / UserPromptExpansion / PostToolUse / Stop / SubagentStart / SubagentStop / StopFailure / ConfigChange / SessionEnd

    UserPromptExpansion（2026-08-16 增）接的是「用户直敲 /skill-name」——命令展开成 prompt
    时触发，**不经过 Skill 工具**，此前所有消费方对这个最常见的 skill 入口都是盲的。
    """
    path = FRAMEWORK_ROOT / "plugins" / "core" / "hooks" / "hooks.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    hooks = data.get("hooks", {})
    required = {"SessionStart", "Setup", "UserPromptSubmit", "UserPromptExpansion", "PostToolUse", "Stop", "SubagentStart", "SubagentStop", "StopFailure", "ConfigChange", "SessionEnd"}
    missing = required - set(hooks.keys())
    if missing:
        return _fail("hooks_complete_pipeline", f"missing events: {missing}")

    # 只查事件名存在挡不住「stanza 被删」：PostToolUse 键因为还有 Edit|Write 那条而始终
    # 在场，matcher:Skill 那条被误删则悄无声息——skill 采集从此少一半入口，没有任何报错。
    logger = "log-skill-invoked.py"
    stanzas = []
    for ev in ("UserPromptExpansion", "PostToolUse"):
        for st in hooks.get(ev, []):
            for h in st.get("hooks", []):
                if logger in str(h.get("command", "")):
                    stanzas.append((ev, st.get("matcher", "")))
    if not any(ev == "UserPromptExpansion" for ev, _ in stanzas):
        return _fail("hooks_complete_pipeline",
                     f"UserPromptExpansion 未接 {logger}——用户直敲 /skill-name 的调用将不被记账")
    if not any(ev == "PostToolUse" and m == "Skill" for ev, m in stanzas):
        return _fail("hooks_complete_pipeline",
                     f"PostToolUse 缺 matcher:Skill 的 {logger} stanza——模型主动调 Skill 工具的调用将不被记账")
    if not (FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / logger).exists():
        return _fail("hooks_complete_pipeline", f"{logger} 不存在，两个 stanza 都会静默失败")
    return _ok("hooks_complete_pipeline", f"({len(required)} events registered + skill_invoked 双入口在位)")


def check_new_hook_scripts_exist():
    """新 hook 脚本存在且语法正确（scripts_syntax 已覆盖语法）"""
    scripts_dir = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    required = [
        "session-start-prep.py",
        "user-prompt-inject.py",
        "session-end-flush.py",
        # v0.2.2 新增 — SessionEnd hook 调用此脚本自动重算 board.yaml 的 summary 段
        "recompute_board_summary.py",
        # v0.2.x 一步到位 — SessionEnd hook 调用此脚本 deterministic 重算 skill-metrics.yaml
        "recompute_skill_metrics.py",
        # v0.4 G2#7 — SessionStart 记忆整理询问式触发（initialUserMessage）
        "memory-ask.py",
        # v0.4 G2#8 — Setup(matcher=maintenance) 维护批处理工单聚合器
        "maintenance_workorder.py",
        # v0.4 G3#12 — SubagentStart 角色记忆注入（启动协议必读升级为代码保证）
        "subagent-memory-inject.py",
        # 2026-08-16 — skill 调用双入口 logger（UserPromptExpansion + PostToolUse:Skill）
        "log-skill-invoked.py",
    ]
    missing = [s for s in required if not (scripts_dir / s).exists()]
    if missing:
        return _fail("new_hook_scripts_exist", f"missing: {missing}")
    # 记忆地图（A0a）：函数被删则 SessionStart 静默少一段、零报错，而主 Claude 直做时
    # 对角色记忆的暴露重新归零——它是「直做前该读哪份记忆」的唯一发现入口
    prep = (scripts_dir / "session-start-prep.py").read_text(encoding="utf-8")
    if "def print_memory_map(" not in prep or "print_memory_map()" not in prep:
        return _fail("new_hook_scripts_exist",
                     "session-start-prep.py 缺记忆地图（def print_memory_map 或其调用）"
                     "——SessionStart 会静默少一段，主 Claude 直做时无从知道有哪些角色域")
    return _ok("new_hook_scripts_exist", f"({len(required)} scripts)")


def check_skill_metrics_recompute_is_deterministic():
    """skill-metrics 必须由脚本实现，并由 SessionEnd hook 间接调用。"""
    scripts_dir = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    bin_dir = FRAMEWORK_ROOT / "plugins" / "core" / "bin"
    script = scripts_dir / "recompute_skill_metrics.py"
    session_end = scripts_dir / "session-end-flush.py"
    template = (
        FRAMEWORK_ROOT
        / "plugins"
        / "core"
        / "templates"
        / "skill-metrics-template.yaml"
    )

    # bin/ 入口（POSIX + Windows）
    posix_bin = bin_dir / "workframe-recompute-skill-metrics"
    cmd_bin = bin_dir / "workframe-recompute-skill-metrics.cmd"
    if not posix_bin.exists():
        return _fail("skill_metrics_recompute_is_deterministic", "bin/workframe-recompute-skill-metrics missing")
    if not cmd_bin.exists():
        return _fail("skill_metrics_recompute_is_deterministic", "bin/workframe-recompute-skill-metrics.cmd missing")
    cmd_data = cmd_bin.read_bytes()
    if any(b > 0x7F for b in cmd_data):
        return _fail("skill_metrics_recompute_is_deterministic", ".cmd contains non-ASCII bytes")
    if b"\r\n" not in cmd_data or cmd_data.replace(b"\r\n", b"").find(b"\n") != -1:
        return _fail("skill_metrics_recompute_is_deterministic", ".cmd must use CRLF line endings")

    if not script.exists():
        return _fail("skill_metrics_recompute_is_deterministic", "recompute_skill_metrics.py missing")

    script_text = script.read_text(encoding="utf-8")
    required_tokens = [
        "def recompute_skill_metrics",
        "type=skill_used",
        "proposal_failures_count",
        "rules:",
    ]
    missing = [token for token in required_tokens if token not in script_text]
    if missing:
        return _fail("skill_metrics_recompute_is_deterministic", f"script missing tokens: {missing}")

    session_text = session_end.read_text(encoding="utf-8")
    if "from recompute_skill_metrics import recompute_skill_metrics" not in session_text:
        return _fail("skill_metrics_recompute_is_deterministic", "session-end-flush.py does not import recompute_skill_metrics")
    if "recompute_skill_metrics()" not in session_text:
        return _fail("skill_metrics_recompute_is_deterministic", "session-end-flush.py does not call recompute_skill_metrics()")

    template_text = template.read_text(encoding="utf-8")
    stale_phrases = ["由 Librarian 从 events.jsonl 定期重算生成", "下次 Librarian 重算"]
    stale = [phrase for phrase in stale_phrases if phrase in template_text]
    if stale:
        return _fail("skill_metrics_recompute_is_deterministic", f"stale template phrasing: {stale}")

    return _ok("skill_metrics_recompute_is_deterministic")


def check_workframe_state_templates_exist():
    """create-project 模板目录要有遥测 / 记忆 / 活跃度相关骨架"""
    tpl_dir = FRAMEWORK_ROOT / "plugins" / "core" / "templates"
    required = [
        "events-template.jsonl",
        "skill-metrics-template.yaml",
        "memory-index-template.json",
        "activity-state-template.json",
        "shared-memory-template.md",
        "shared-notes-template.md",
    ]
    missing = [r for r in required if not (tpl_dir / r).exists()]
    if missing:
        return _fail("workframe_state_templates_exist", f"missing: {missing}")
    return _ok("workframe_state_templates_exist", f"({len(required)} templates)")


def check_activity_state_has_dormant_profile():
    """activity-state-template.json 必须含 dormant_profile 字段"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "activity-state-template.json"
    if not path.exists():
        return _fail("activity_state_has_dormant_profile", "template not found")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail("activity_state_has_dormant_profile", f"invalid JSON: {e}")
    if "dormant_profile" not in data:
        return _fail("activity_state_has_dormant_profile", "dormant_profile field missing")
    return _ok("activity_state_has_dormant_profile", f"(default={data.get('dormant_profile')!r})")


def check_self_iteration_confidence_formula():
    """self-iteration SKILL.md 必须含置信度公式关键词"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "self-iteration" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    required_tokens = [
        "confidence",
        "occurrences",
        "recency",
        "cross_role_corroboration",
        "user_confirmed",
        "verify_by",
        "verify_signal",
    ]
    missing = [t for t in required_tokens if t not in text]
    if missing:
        return _fail("self_iteration_confidence_formula", f"missing tokens: {missing}")
    return _ok("self_iteration_confidence_formula")


def check_eval_cases_exist():
    """3 组 eval-cases 存在（librarian / self-iteration 在 skill 下；auto-update 在顶层 eval-cases/ 下）"""
    cases = {
        "librarian": FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "librarian" / "eval-cases",
        "self-iteration": FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "self-iteration" / "eval-cases",
        "auto-update": FRAMEWORK_ROOT / "plugins" / "core" / "eval-cases" / "auto-update",
    }
    offenders = []
    for name, d in cases.items():
        if not d.exists():
            offenders.append(f"{name}: dir missing")
            continue
        md_files = list(d.glob("*.md"))
        if len(md_files) < 3:
            offenders.append(f"{name}: only {len(md_files)} cases (need ≥3)")
    if offenders:
        return _fail("eval_cases_exist", "; ".join(offenders))
    return _ok("eval_cases_exist", "(3 golden case sets)")


def check_shared_agent_contract():
    """shared memory 启动契约现位于 agent-protocols.md（v0.2.2 起从 agent body 抽离到 rule）"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core" / "agent-protocols.md"
    if not path.exists():
        return _fail("shared_agent_contract", "agent-protocols.md missing")
    text = path.read_text(encoding="utf-8")
    if "agent-memory/shared/MEMORY.md" not in text:
        return _fail("shared_agent_contract", "agent-protocols.md missing shared memory contract")
    return _ok("shared_agent_contract", "(in agent-protocols.md)")


# === v0.2.1 checks ===


def check_version_consistency():
    """两个插件的 plugin.json、各自的 marketplace 条目、market metadata、README status 版本号一致。

    **本仓采用锁步发版**——任何一次发布，两个插件一起 bump 到同一个版本号。

    为什么锁步而不是各自独立：V4 实测确认「用户装的是插件、不是市场，version 是**每个插件
    各自的**更新信号」——改了 core 却只 bump launcher，core 的用户会**静默**拿不到更新。
    独立版本号更精确，但要求每次都记得 bump 对的那个，而漏 bump 的失败是无声的。
    锁步让这个失败**在结构上不可能发生**：反正两个都 bump，就不存在「bump 错了」。
    代价是没改的那个插件也会推一次空更新——两个插件的仓库，这点噪音换一个不会翻的车，划算。

    所以这条检查要求**全部相等**，不是各自对齐即可。
    """
    name = "version_consistency"
    versions = {}
    try:
        mj = json.loads((FRAMEWORK_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    except Exception as e:
        return _fail(name, f"marketplace.json read: {e}")
    versions["marketplace.metadata"] = mj.get("metadata", {}).get("version")
    entries = {p.get("name"): p for p in mj.get("plugins", [])}

    for plugin_name, plugin_dir in (("core", "core"), ("workframe-launcher", "workframe-launcher")):
        pj_path = FRAMEWORK_ROOT / "plugins" / plugin_dir / ".claude-plugin" / "plugin.json"
        try:
            versions[f"{plugin_name}/plugin.json"] = json.loads(
                pj_path.read_text(encoding="utf-8")).get("version")
        except Exception as e:
            return _fail(name, f"{plugin_name} plugin.json read: {e}")
        entry = entries.get(plugin_name)
        if entry is None:
            return _fail(name, f"marketplace.json 未列出插件 {plugin_name}")
        versions[f"marketplace.{plugin_name}"] = entry.get("version")

    try:
        m = re.search(r"\*\*Status:\*\*\s*v([\d.]+(?:-\w+)?)",
                      (FRAMEWORK_ROOT / "README.md").read_text(encoding="utf-8"))
        versions["README.Status"] = m.group(1) if m else None
    except Exception as e:
        return _fail(name, f"README.md read: {e}")

    if None in versions.values():
        return _fail(name, f"missing: {versions}")
    unique = set(versions.values())
    if len(unique) > 1:
        return _fail(name, f"mismatch: {versions}")
    return _ok(name, f"(2 plugins + market + README all @ {unique.pop()})")


def check_event_registry_consistency():
    """event-schema.json 存在；removed_v* event 不应在 active producer 脚本里"""
    schema_path = FRAMEWORK_ROOT / "plugins" / "core" / ".workframe-meta" / "event-schema.json"
    if not schema_path.exists():
        return _fail("event_registry_consistency", "event-schema.json missing")
    try:
        data = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail("event_registry_consistency", f"invalid JSON: {e}")

    events = data.get("events", {})
    if not events:
        return _fail("event_registry_consistency", "no events defined")

    # v0.2.2 — SessionEnd hook 自动重算 summary 后写入此事件
    if "summary_recomputed" not in events:
        return _fail("event_registry_consistency", "summary_recomputed event missing (v0.2.2)")
    if "skill_metrics_recomputed" not in events:
        return _fail("event_registry_consistency", "skill_metrics_recomputed event missing")

    removed = [name for name, spec in events.items() if spec.get("reliability") == "removed_v0_2_1"]
    # 检查 removed event 不应在 check-iteration-trigger.py 里作为权重或触发条件出现
    trigger_script = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "check-iteration-trigger.py"
    if trigger_script.exists():
        text = trigger_script.read_text(encoding="utf-8")
        for name in removed:
            # 允许注释或 docstring 里提及（作为降级说明），但不应在代码层作为 key
            if re.search(rf'["\']({re.escape(name)})["\']\s*:\s*\d', text):
                return _fail("event_registry_consistency", f"removed event `{name}` still used as dict key in check-iteration-trigger.py")

    return _ok("event_registry_consistency", f"({len(events)} events, {len(removed)} removed)")


def check_slash_namespace_consistency():
    """docs/ 里所有 core skill slash 必须带 /core: 前缀"""
    docs_dir = FRAMEWORK_ROOT / "docs"
    # core skills that should be namespaced
    core_skills = ["audit", "rollback", "memory-log", "maintenance-review"]
    offenders = []
    for f in docs_dir.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        # 逐行扫描代码块（```）外的 slash 引用
        in_code = False
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            # 即使代码块里也检查，因为示例代码是给用户抄的，必须正确
            for skill in core_skills:
                # 匹配 slash 命令形式的 /skill：
                #   前面不能是 word / 连字符 / 点 / 斜杠（排除路径引用如 ./create-project-guide.md）
                #   后面必须是空白 / 行尾 / 代码标记 / 标点（排除 /create-project-guide 等连字符延续）
                pattern = rf"(?<![\w\-./])/{re.escape(skill)}(?=[\s`,.;:!?)\]'\"]|$)"
                if re.search(pattern, line):
                    # 例外：允许历史上下文引用 /skill 老名字
                    if any(w in line for w in ["曾", "历史", "v0.1", "历史版本", "legacy"]):
                        continue
                    offenders.append(f"{f.name}:{lineno}: /{skill} (should be /core:{skill})")
    if offenders:
        return _fail("slash_namespace_consistency", "; ".join(offenders[:5]))
    return _ok("slash_namespace_consistency")


def check_pending_maintenance_schema_documented():
    """pending_maintenance 字段契约必须随插件出货，且与真实写入方一致。

    契约载体 = `templates/activity-state-template.json` 的 `__pending_maintenance_schema__`
    （2026-08-10：原载体是维护者笔记 dev-docs/architecture-overview.md §17.8，随 dev-docs
    剥离而不再随包分发——留在模板里会让每个用户项目带一个够不着的指针）。

    检查同时升级为真对账：模板声明的字段集合 == `check-iteration-trigger.py` 的
    `upsert_pending()` 实际写入的字段集合。原检查只断言「某文档里出现 5 个字符串」，
    字段增删时不会红。
    """
    name = "pending_maintenance_schema_documented"
    tpl = FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "activity-state-template.json"
    producer = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "check-iteration-trigger.py"
    if not tpl.exists():
        return _fail(name, f"activity-state-template.json 缺失: {tpl}")
    if not producer.exists():
        return _fail(name, f"check-iteration-trigger.py 缺失: {producer}")
    try:
        data = json.loads(tpl.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return _fail(name, f"activity-state-template.json 解析失败: {e}")
    schema = data.get("__pending_maintenance_schema__")
    if not isinstance(schema, dict):
        return _fail(name, "模板缺 __pending_maintenance_schema__（字段契约必须随插件出货）")
    declared = {k for k in schema if not k.startswith("__")}

    # 从 upsert_pending() 的 new_item = { ... } 字面量抽真实写入字段
    text = producer.read_text(encoding="utf-8")
    m = re.search(r"new_item\s*=\s*\{(.*?)\n    \}", text, re.S)
    if not m:
        return _fail(name, "check-iteration-trigger.py 找不到 upsert_pending 的 new_item 字面量")
    actual = set(re.findall(r'"(\w+)"\s*:', m.group(1)))
    if declared != actual:
        return _fail(
            name,
            f"契约与实写不一致: 模板多={sorted(declared - actual)}, 模板缺={sorted(actual - declared)}",
        )
    return _ok(name, f"({len(declared)} 字段，模板契约 == upsert_pending 实写)")


def check_activity_state_wake_up_pending():
    """activity-state-template.json 必须含 wake_up_pending 字段"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "activity-state-template.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail("activity_state_wake_up_pending", f"invalid JSON: {e}")
    if "wake_up_pending" not in data:
        return _fail("activity_state_wake_up_pending", "field missing")
    if data["wake_up_pending"] is not False:
        return _fail("activity_state_wake_up_pending", f"should default to false, got {data['wake_up_pending']!r}")
    return _ok("activity_state_wake_up_pending")


# === v0.2.2 — Agent 边界与去耦合检查 ===

# 业务领域 skill 名（不应在 core agent body 出现，仅 frontmatter skills: 列表允许）
BUSINESS_SKILL_NAMES = {
    "requirement-analysis", "feature-breakdown", "acceptance-criteria",
    "competitive-analysis", "user-feedback-analysis", "product-metrics-design",
    "technical-design", "systematic-debugging",
    "test-case-design", "code-review",
    "prompt-design", "prompt-evaluation",
}


def _agent_body(text):
    """从 agent md 提取 frontmatter 之后的 body 文本"""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2] if len(parts) >= 3 else text


def _agent_skills(text):
    """从 agent md frontmatter 解析 skills: 列表，返回 set。

    支持 YAML 两种语法：
      skills:
        - skill-a
        - skill-b
      skills: [skill-a, skill-b]
    """
    if not text.startswith("---"):
        return set()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return set()
    fm = parts[1]
    skills = set()
    # 块式：skills: 后跟若干 - <name> 行
    block_match = re.search(r"^skills\s*:\s*$([\s\S]*?)(?=^\S|\Z)", fm, re.MULTILINE)
    if block_match:
        for line in block_match.group(1).splitlines():
            m = re.match(r"\s*-\s*([\w\-]+)\s*$", line)
            if m:
                skills.add(m.group(1))
    # 流式：skills: [a, b]
    inline_match = re.search(r"^skills\s*:\s*\[([^\]]*)\]\s*$", fm, re.MULTILINE)
    if inline_match:
        for item in inline_match.group(1).split(","):
            name = item.strip().strip("'\"")
            if name:
                skills.add(name)
    return skills


def check_agents_no_business_skill_in_body():
    """5 core agent body 不引用业务领域 skill 名（让 frontmatter `skills:` + skill description 自动匹配）

    两类例外允许：
    1. 路径引用（如 `plugins/core/skills/<skill>/reference/xxx.md`）— 文件路径而非 skill 调用引导
    2. 引用自己 frontmatter `skills:` 已绑定的 skill — 这是 agent 内部参考自己 preload 的能力，
       不会污染主 Claude 路由（routing 由 description 字段驱动，不读 body）。v0.2.2-fixup-5 起允许。
    """
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    PATH_REF_RE = re.compile(r"(?:plugins/core/)?skills/[\w\-]+/\S*")
    offenders = []
    for f in agents_dir.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        own_skills = _agent_skills(text)
        body = _agent_body(text)
        body_no_paths = PATH_REF_RE.sub("", body)
        for skill in BUSINESS_SKILL_NAMES:
            if skill in own_skills:
                continue  # 允许 agent 引用自己已绑定的 skill
            if skill in body_no_paths:
                offenders.append(f"{f.name}: body contains '{skill}' (not in own frontmatter skills)")
    if offenders:
        return _fail("agents_no_business_skill_in_body", "; ".join(offenders[:8]))
    return _ok("agents_no_business_skill_in_body")


def check_agents_no_dispatch_language():
    """5 core agent body 不出现 '通知 @X' 派发式语句（应改为响应文字标注 + 状态机）"""
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    offenders = []
    pat = re.compile(r"通知\s*@\w")
    for f in agents_dir.glob("*.md"):
        body = _agent_body(f.read_text(encoding="utf-8"))
        for lineno, line in enumerate(body.splitlines(), 1):
            if pat.search(line):
                offenders.append(f"{f.name}:body+{lineno}: {line.strip()[:60]}")
    if offenders:
        return _fail("agents_no_dispatch_language", "; ".join(offenders[:5]))
    return _ok("agents_no_dispatch_language")


def check_agents_no_inline_protocol():
    """5 core agent body 不内联 hook event payload 样板（应抽到 agent-protocols.md）

    注意：单纯描述"脚本会写 events.jsonl 事件"是合规的（属于副作用说明）；
    禁止的是 agent body 直接复制 hook payload 模板（如 `{"type": "skill_used", ...}`）
    或要求 agent 自己 append 事件流的样板（"为每个 skill 向 events.jsonl append"）。
    """
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    offenders = []
    patterns = [
        r'"type"\s*:\s*"skill_used"',           # hook payload JSON 字符串
        r"为每个.{0,30}events\.jsonl",          # 要求 agent 自己 append 事件流
        r"events\.jsonl.{0,15}append",          # append 动作样板
    ]
    for f in agents_dir.glob("*.md"):
        body = _agent_body(f.read_text(encoding="utf-8"))
        for pat in patterns:
            if re.search(pat, body):
                offenders.append(f"{f.name}: matched '{pat[:40]}'")
                break
    if offenders:
        return _fail("agents_no_inline_protocol", "; ".join(offenders))
    return _ok("agents_no_inline_protocol")


def check_agents_no_memory_frontmatter():
    """core agent frontmatter 不得含 `memory:` 字段。

    角色记忆由 SubagentStart hook（subagent-memory-inject.py）按框架 `<role>/` 布局注入；
    CC 官方 memory frontmatter 的目录键名带 plugin 前缀（core:pm → agent-memory/core-pm/），
    与框架布局和 D/U/R/A 维护协议不兼容，重新引入会造成两套记忆仓并行分叉。
    """
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    offenders = []
    for f in agents_dir.glob("*.md"):
        text = f.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        parts = text.split("---", 2)
        frontmatter = parts[1] if len(parts) >= 3 else ""
        if re.search(r"^memory\s*:", frontmatter, re.MULTILINE):
            offenders.append(f.name)
    if offenders:
        return _fail("agents_no_memory_frontmatter", "; ".join(offenders))
    return _ok("agents_no_memory_frontmatter")


def check_agents_no_response_output_duplication():
    """5 core agent body 不重复 response-output rule 的核心句"""
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    offenders = []
    patterns = [
        r"响应正文.{0,15}呈现",
        r"Step\s*-1.*前置检查",
        r"前置检查.*响应正文",
    ]
    for f in agents_dir.glob("*.md"):
        body = _agent_body(f.read_text(encoding="utf-8"))
        for pat in patterns:
            if re.search(pat, body):
                offenders.append(f"{f.name}: matched '{pat[:40]}'")
                break
    if offenders:
        return _fail("agents_no_response_output_duplication", "; ".join(offenders))
    return _ok("agents_no_response_output_duplication")


def check_agents_no_protected_assets_duplication():
    """5 core agent body 不重复 auto-update 受保护资产清单（≥3 个保护资产同时罗列且未引用 auto-update）"""
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    offenders = []
    indicators = [".claude/agents/", ".claude/rules/", ".claude/skills/", ".claude/settings"]
    for f in agents_dir.glob("*.md"):
        body = _agent_body(f.read_text(encoding="utf-8"))
        # 引用 auto-update.md 的 agent 视为合规（"清单见 auto-update.md"）
        if "auto-update" in body:
            continue
        match_count = sum(1 for i in indicators if i in body)
        if match_count >= 3:
            offenders.append(
                f"{f.name}: body lists {match_count}/4 protected paths inline "
                "(should reference auto-update.md instead)"
            )
    if offenders:
        return _fail("agents_no_protected_assets_duplication", "; ".join(offenders))
    return _ok("agents_no_protected_assets_duplication")


def check_agents_no_project_specific_path_hardcoding():
    """5 core agent body 不含项目特定路径硬编码（具体命名占位符 / 业务子目录假设）"""
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    offenders = []
    forbidden_patterns = [
        # 具体命名占位符（应由对应 skill 决定）
        r"REQ-\{",
        r"FEAT-\{",
        r"PROMPT-\{",
        r"METRICS-\{",
        # 业务子目录假设
        r"<模块>",
        r"<子模块>",
        r"<迭代>",
        # 具体项目特定示例
        r"\b\d{4}Q[1-4]-",
        r"projects/prompts/<",
        r"projects/evals/prompts/",
        r"board-archive-<",
    ]
    for f in agents_dir.glob("*.md"):
        body = _agent_body(f.read_text(encoding="utf-8"))
        for pat in forbidden_patterns:
            for m in re.finditer(pat, body):
                offenders.append(f"{f.name}: '{m.group(0)}'")
    if offenders:
        return _fail("agents_no_project_specific_path_hardcoding", "; ".join(offenders[:8]))
    return _ok("agents_no_project_specific_path_hardcoding")


def check_agents_no_deep_spec_path():
    """5 core agent body 不含 projects/specs/X/Y/Z/ 三层路径硬编码"""
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    offenders = []
    pat = re.compile(r"projects/specs/[^/\s]+/[^/\s]+/[^/\s]+/")
    for f in agents_dir.glob("*.md"):
        body = _agent_body(f.read_text(encoding="utf-8"))
        for m in pat.finditer(body):
            offenders.append(f"{f.name}: '{m.group(0)}'")
    if offenders:
        return _fail("agents_no_deep_spec_path", "; ".join(offenders))
    return _ok("agents_no_deep_spec_path")


DOMAIN_SKILLS = {
    "task-management",
    "requirement-analysis", "feature-breakdown", "acceptance-criteria",
    "competitive-analysis", "product-metrics-design", "user-feedback-analysis",
    "technical-design", "systematic-debugging",
    "test-case-design", "code-review",
    "prompt-design", "prompt-evaluation",
}


def check_domain_skills_have_description():
    """domain skill 必须同时有 description 和 when_to_use（v0.2.3 Phase A-lite 起 when_to_use 升级为 fail）。

    description 偏"是什么"，when_to_use 偏"什么时候调"——两者配合让主 Claude routing 更准。
    缺 when_to_use 时容易触发 routing 摇摆（多个 skill description 都覆盖同一关键词）。
    """
    skills_dir = FRAMEWORK_ROOT / "plugins" / "core" / "skills"
    missing_desc = []
    missing_when = []
    for skill in sorted(DOMAIN_SKILLS):
        skill_md = skills_dir / skill / "SKILL.md"
        if not skill_md.exists():
            missing_desc.append(f"{skill}/SKILL.md missing")
            continue
        text = skill_md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            missing_desc.append(f"{skill}: no frontmatter")
            continue
        parts = text.split("---", 2)
        if len(parts) < 3:
            missing_desc.append(f"{skill}: unterminated frontmatter")
            continue
        fm = parts[1]
        if not re.search(r"^\s*description\s*:", fm, re.MULTILINE):
            missing_desc.append(f"{skill}: no description field")
        if not re.search(r"^\s*when_to_use\s*:", fm, re.MULTILINE):
            missing_when.append(skill)

    if missing_desc:
        return _fail("domain_skills_have_description", "; ".join(missing_desc[:5]))

    if missing_when:
        return _fail(
            "domain_skills_have_description",
            f"{len(missing_when)} domain skills lack `when_to_use`: {sorted(missing_when)[:5]}",
        )

    return _ok("domain_skills_have_description", f"(all {len(DOMAIN_SKILLS)} domain skills have description + when_to_use)")

    return _ok("domain_skills_have_description", f"({len(DOMAIN_SKILLS)} domain skills with description + when_to_use)")


def check_event_types_registered():
    """所有 append_event() 调用 + skill SKILL.md 里 `{"type":"<name>"}` 模式提到的 event name
    必须在 event-schema.json registry 里注册。

    Codex 三轮 review P1-8：maintenance-review/SKILL.md 写 pending_maintenance_dismissed +
    maintenance_review_completed 事件但 schema 漏注册——本检查防止类似漏。
    """
    schema_path = FRAMEWORK_ROOT / "plugins" / "core" / ".workframe-meta" / "event-schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail("event_types_registered", f"event-schema.json read: {e}")
    registered = set(schema.get("events", {}).keys())

    used = set()

    # 扫描 plugin scripts 里 append_event(<name>, ...) / event_type=<name> / type=<name> 调用
    scripts_dir = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    py_patterns = [
        re.compile(r'append_event\(\s*["\']([a-z_]+)["\']'),
        re.compile(r'(?:event_type|ev_type)\s*=\s*["\']([a-z_]+)["\']'),
    ]
    for f in scripts_dir.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        for pat in py_patterns:
            used.update(pat.findall(text))

    # 扫描 SKILL.md / core rules 找 `{"type":"<name>"}` 模式
    skills_dir = FRAMEWORK_ROOT / "plugins" / "core" / "skills"
    md_pattern = re.compile(r'"type"\s*:\s*"([a-z_]+)"')
    for f in skills_dir.rglob("SKILL.md"):
        text = f.read_text(encoding="utf-8")
        used.update(md_pattern.findall(text))
    rules_dir = FRAMEWORK_ROOT / "plugins" / "core" / "rules"
    for f in rules_dir.rglob("*.md"):
        text = f.read_text(encoding="utf-8")
        used.update(md_pattern.findall(text))

    # 已知 false positive：documentation 里 "type" 字段指代 task type 等非 event 概念
    KNOWN_NON_EVENT_TYPES = {
        "command",       # hooks.json 里 "type": "command"
        "memory",        # subagent frontmatter "memory: project" 类似
    }
    used -= KNOWN_NON_EVENT_TYPES

    missing = used - registered
    if missing:
        return _fail(
            "event_types_registered",
            f"events used but not in schema registry: {sorted(missing)}"
        )
    return _ok("event_types_registered", f"({len(used)} event types referenced, all in registry)")


def check_model_mediated_events_have_append_samples():
    """model_mediated events depend on SKILL/rule prose, so require an explicit append sample.

    This intentionally does not parse natural language deeply. It only verifies that the
    producer document contains both `events.jsonl` and a JSON-like `"type":"<event>"`
    literal so consumer-only contracts cannot drift away from executable instructions.

    Exception — events that moved to a **code channel** (2026-08-16): once the skill tells
    the model to run a script instead of hand-writing the JSON, requiring a hand-write
    sample is actively harmful — it invites the model to bypass the script's lock /
    atomic-write / three-way-merge and rewrite `activity-state.json` wholesale (dropping
    `session_counter` and friends without any error). For those, the presence of the
    command itself is the stronger evidence, so the sample requirement is waived.
    """
    code_channel_marks = {
        # event name -> 该事件的代码通道命令标志（出现即视为已接线，免手写示例）
        "pending_maintenance_dismissed": "--close-pm",
    }
    schema_path = FRAMEWORK_ROOT / "plugins" / "core" / ".workframe-meta" / "event-schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail("model_mediated_events_have_append_samples", f"event-schema.json read: {e}")

    producer_docs = {
        "librarian": FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "librarian" / "SKILL.md",
        "self-iteration": FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "self-iteration" / "SKILL.md",
        "rollback": FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "rollback" / "SKILL.md",
        "maintenance-review": FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "maintenance-review" / "SKILL.md",
    }

    offenders = []
    for event_name, spec in schema.get("events", {}).items():
        if spec.get("reliability") != "model_mediated":
            continue
        producer = str(spec.get("producer", "")).lower()
        doc_path = None
        for key, path in producer_docs.items():
            if key in producer:
                doc_path = path
                break
        if doc_path is None:
            offenders.append(f"{event_name}: no producer doc mapping for {producer!r}")
            continue
        if not doc_path.exists():
            offenders.append(f"{event_name}: producer doc missing {doc_path.relative_to(FRAMEWORK_ROOT)}")
            continue
        text = doc_path.read_text(encoding="utf-8")
        mark = code_channel_marks.get(event_name)
        if mark:
            if mark in text:
                continue      # 已走代码通道，手写示例不再需要（见 docstring 例外条）
            offenders.append(f"{event_name}: 应走代码通道但 {doc_path.name} 里找不到 `{mark}`")
            continue
        has_type_literal = re.search(rf'"type"\s*:\s*"{re.escape(event_name)}"', text) is not None
        if "events.jsonl" not in text or not has_type_literal:
            offenders.append(f"{event_name}: missing append sample in {doc_path.relative_to(FRAMEWORK_ROOT)}")

    if offenders:
        return _fail("model_mediated_events_have_append_samples", "; ".join(offenders[:8]))
    return _ok("model_mediated_events_have_append_samples")


def check_memory_activity_events_have_snapshot_fields():
    """memory-log must be able to render history without relying on current sidecar state."""
    schema_path = FRAMEWORK_ROOT / "plugins" / "core" / ".workframe-meta" / "event-schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail("memory_activity_events_have_snapshot_fields", f"event-schema.json read: {e}")

    required = {
        "memory_promoted": {"ts", "type", "scope", "entry_key", "summary", "source"},
        "memory_decayed": {"ts", "type", "scope", "entry_key", "summary", "source", "age_days", "provenance"},
        "user_correction": {"ts", "type", "scope", "entry_key", "summary", "source"},
        # 迁移比提升多一个 from_ref：跨 scope 搬迁后，只看 scope 无法回答「它原来在哪」，
        # 而这恰恰是事后复核搬迁是否合理时第一个要问的
        "memory_migrated": {"ts", "type", "scope", "entry_key", "summary", "source", "from_ref"},
    }
    offenders = []
    events = schema.get("events", {})
    for event_name, required_fields in required.items():
        fields = set(events.get(event_name, {}).get("fields", {}).keys())
        missing = sorted(required_fields - fields)
        if missing:
            offenders.append(f"{event_name}: missing {missing}")

    if offenders:
        return _fail("memory_activity_events_have_snapshot_fields", "; ".join(offenders))
    return _ok("memory_activity_events_have_snapshot_fields")


def check_windows_cmd_wrapper_portable():
    """.cmd wrapper must be safe under Windows cmd.exe.

    Codex fixup-3: UTF-8 Chinese REM comments + LF-only newlines can be
    mis-parsed by cmd.exe, causing comment fragments to execute after the
    Python command. Keep the wrapper ASCII-only with CRLF line endings.
    """
    path = FRAMEWORK_ROOT / "plugins" / "core" / "bin" / "workframe-recompute-board-summary.cmd"
    if not path.exists():
        return _fail("windows_cmd_wrapper_portable", "missing bin/workframe-recompute-board-summary.cmd")
    data = path.read_bytes()
    if any(b > 0x7F for b in data):
        return _fail("windows_cmd_wrapper_portable", ".cmd contains non-ASCII bytes")
    if b"\r\n" not in data or data.replace(b"\r\n", b"").find(b"\n") != -1:
        return _fail("windows_cmd_wrapper_portable", ".cmd must use CRLF line endings")
    text = data.decode("ascii")
    required = [
        "@echo off",
        'python "%~dp0workframe-recompute-board-summary" %*',
        "exit /b %ERRORLEVEL%",
    ]
    missing = [s for s in required if s not in text]
    if missing:
        return _fail("windows_cmd_wrapper_portable", f"missing required lines: {missing}")
    return _ok("windows_cmd_wrapper_portable")


def check_no_proposal_failed_as_core_trigger():
    """proposal_failed is model_mediated and must not be documented as a core trigger."""
    scan_files = [
        FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "self-iteration" / "SKILL.md",
        FRAMEWORK_ROOT / "plugins" / "core" / ".workframe-meta" / "event-schema.json",
    ]
    forbidden = [
        "驱动下一轮触发信号",
        '"consumers": ["check-iteration-trigger.py (problem weight 3.0)"],',
        "`proposal_failed` | `proposal_failed:<proposal_id>`",
        "skill_low_success` / `proposal_failed`",
    ]
    offenders = []
    for f in scan_files:
        text = f.read_text(encoding="utf-8")
        for s in forbidden:
            if s in text:
                offenders.append(f"{f.relative_to(FRAMEWORK_ROOT)} contains {s!r}")
    if offenders:
        return _fail("no_proposal_failed_as_core_trigger", "; ".join(offenders[:5]))
    return _ok("no_proposal_failed_as_core_trigger")


def check_create_project_reference_current_counts():
    """create-project references must reflect the current 36 skills / 4 rules taxonomy."""
    scan_files = [
        FRAMEWORK_ROOT / "plugins" / "core" / "reference" / "project-architecture.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "reference" / "skill-customization-guide.md",
    ]
    forbidden = [
        "15 skill + 3 rules",
        "15 skills + 3 rules",
        "3 rules + hooks",
        "pending_maintenance 展示 / dismiss",
        "总数 5-7 视口径",
    ]
    offenders = []
    for f in scan_files:
        text = f.read_text(encoding="utf-8")
        for s in forbidden:
            if s in text:
                offenders.append(f"{f.relative_to(FRAMEWORK_ROOT)} contains {s!r}")
    if offenders:
        return _fail("create_project_reference_current_counts", "; ".join(offenders[:5]))
    return _ok("create_project_reference_current_counts")


def check_rules_sync_docs_current_rule_count():
    """User-facing rules-sync docs must mention 4 core rules after agent-protocols.md."""
    path = FRAMEWORK_ROOT / "docs" / "rules-sync.md"
    text = path.read_text(encoding="utf-8")
    forbidden = ["3 份通用 rules", "3 个 core rules"]
    offenders = [s for s in forbidden if s in text]
    if offenders:
        return _fail("rules_sync_docs_current_rule_count", f"legacy phrases: {offenders}")
    if "agent-protocols.md" not in text:
        return _fail("rules_sync_docs_current_rule_count", "agent-protocols.md missing from docs/rules-sync.md")
    return _ok("rules_sync_docs_current_rule_count")


def check_session_start_no_false_summary_creation_claim():
    """SessionStart must not claim SessionEnd creates a missing summary block."""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "session-start-prep.py"
    text = path.read_text(encoding="utf-8")
    forbidden = ["下次 SessionEnd hook 会建立", "SessionEnd hook 会建立"]
    offenders = [s for s in forbidden if s in text]
    if offenders:
        return _fail("session_start_no_false_summary_creation_claim", f"legacy phrases: {offenders}")
    if "summary_block_not_found" not in text:
        return _fail("session_start_no_false_summary_creation_claim", "summary_block_not_found skip path missing")
    schema = (FRAMEWORK_ROOT / "plugins" / "core" / ".workframe-meta" / "event-schema.json").read_text(encoding="utf-8")
    if "summary_block_not_found" not in schema:
        return _fail("session_start_no_false_summary_creation_claim", "event schema missing summary_block_not_found reason")
    return _ok("session_start_no_false_summary_creation_claim")


def check_agents_no_plugin_internal_path():
    """Agent files / template / customization guide must not reference plugin-internal source paths.

    After fixup-4 these files reference rules via "workframe core rule: <name>";
    fixup-5 extends to skills/<name>/reference/* paths (Codex 复测发现 dev.md 引用
    plugins/core/skills/technical-design/reference/engineering-discipline.md 同样
    在订阅项目里不可见——sync-rules 只同步 rules，不同步 skill reference）。
    """
    targets = [
        FRAMEWORK_ROOT / "plugins" / "core" / "agents" / "pm.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "agents" / "dev.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "agents" / "qa.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "agents" / "prompt-eng.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "agent-template.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "reference" / "role-customization-guide.md",
    ]
    # 兜底捕获 plugins/core/(rules|skills)/.../*.md 完整源码路径
    bad_path_pattern = re.compile(r"plugins/core/(?:rules|skills)/[\w\-/]+\.md")
    offenders = []
    for path in targets:
        if not path.exists():
            return _fail("agents_no_plugin_internal_path", f"missing {path.name}")
        text = path.read_text(encoding="utf-8")
        matches = bad_path_pattern.findall(text)
        if matches:
            offenders.append(f"{path.name}: {matches}")
    if offenders:
        return _fail(
            "agents_no_plugin_internal_path",
            f"plugin-internal path reference (use semantic name like 'workframe core rule: <name>' / "
            f"'<skill> skill 的 <ref> reference' instead): {offenders}",
        )
    return _ok("agents_no_plugin_internal_path", "(7 files)")


def check_role_profile_catalog_exists():
    """role-profile-catalog.md 存在且含 3 个 profile 章节标题（v0.2.3 Role Profile Lite）。

    最小检查：文件存在 + 3 个 profile 名作为 `### <name>` 标题出现 + 已退役档位零残留。
    不解析每个 profile 的具体路由文本内容（防止文档微调被校验卡住）。
    退役档位（client-delivery / content-ops / business-ops）随 project_type 六类型
    一并退出，正文再次出现即视为回潮。
    """
    path = FRAMEWORK_ROOT / "plugins" / "core" / "reference" / "role-profile-catalog.md"
    if not path.exists():
        return _fail("role_profile_catalog_exists", "role-profile-catalog.md missing")
    text = path.read_text(encoding="utf-8")
    expected_profiles = ["software-team", "solo-pm", "ai-product"]
    retired_profiles = ["client-delivery", "content-ops", "business-ops"]
    missing = [p for p in expected_profiles if f"### `{p}`" not in text]
    if missing:
        return _fail("role_profile_catalog_exists", f"missing profile sections: {missing}")
    revived = [p for p in retired_profiles if p in text]
    if revived:
        return _fail("role_profile_catalog_exists", f"已退役档位残留: {revived}")
    return _ok("role_profile_catalog_exists", "(3 profiles)")


def check_claude_md_template_has_role_profile_placeholder():
    """claude-md-template.md 含 {{ROLE_PROFILE_ROUTING}} 占位符（v0.2.3 Role Profile Lite）。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "claude-md-template.md"
    if not path.exists():
        return _fail("claude_md_template_has_role_profile_placeholder", "claude-md-template.md missing")
    text = path.read_text(encoding="utf-8")
    if "{{ROLE_PROFILE_ROUTING}}" not in text:
        return _fail(
            "claude_md_template_has_role_profile_placeholder",
            "{{ROLE_PROFILE_ROUTING}} placeholder missing in claude-md-template.md",
        )
    return _ok("claude_md_template_has_role_profile_placeholder")


def check_role_profile_field_documented():
    """关键文档说明了 role_profile 字段（v0.2.3 Role Profile Lite）。

    最小检查：project-architecture.md 含 role_profile 字段说明（且标注"可选"或类似语义）。
    不要求所有文档都提，只要权威 schema 文档（project-architecture.md）覆盖即可。
    """
    path = FRAMEWORK_ROOT / "plugins" / "core" / "reference" / "project-architecture.md"
    if not path.exists():
        return _fail("role_profile_field_documented", "project-architecture.md missing")
    text = path.read_text(encoding="utf-8")
    if "role_profile" not in text:
        return _fail(
            "role_profile_field_documented",
            "project-architecture.md does not mention role_profile field",
        )
    if "可缺省" not in text and "可选" not in text:
        return _fail(
            "role_profile_field_documented",
            "project-architecture.md must mark role_profile as optional (可缺省 / 可选)",
        )
    return _ok("role_profile_field_documented")


def check_no_stale_reference_count():
    """防止文档残留 'reference/ 4 份' 旧口径（v0.2.3-fixup-1：Codex 复测发现 4 处残留；
    reference/ 现为 6 份，随版本变化，检查只拦历史上实际漂过的「4 份」表述）。
    2026-08-11 文档重写后原目标文件已删，改扫全部 docs/。CHANGELOG 历史段不扫。"""
    target_files = sorted((FRAMEWORK_ROOT / "docs").glob("*.md"))
    # 匹配 'reference/' 后跟空格 + 数字 4 + 空格 + '份'（含中文/Markdown 转义场景）
    stale_pattern = re.compile(r"reference[/]?\s*[\\\\`]?\s*4\s*份")
    offenders = []
    for path in target_files:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if stale_pattern.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()[:80]}")
    if offenders:
        return _fail(
            "no_stale_reference_count",
            f"stale 'reference/ 4 份' (current is 6): {offenders[:3]}",
        )
    return _ok("no_stale_reference_count")


def check_no_stale_skill_count():
    """禁止旧版本 skill 数量口径残留在 onboarding / docs 描述里。

    当前 (v0.4.x): 13 domain + 8 system/maintenance + 8 docs/publishing + 7 modules-system = 36 skills。
    历史口径：v0.2.0 = 15+16 = 21，v0.3 = 13+7+1+6 = 27，v0.2.1 = 13+8+1+6 = 28，v0.3.x M3 ~ v0.4.0-dev 早期 = 13+8+1+7+4 = 33，v0.4.0-dev 中期 = 34（material-intake / doc-graph-health 补齐前）。
    Hook 段数：v0.2.x = 5 段，v0.3.x M3+ = 6 段（新增 PostToolUse on 代码/submodule.yaml），v0.4 M5+ = 8 段（新增 StopFailure / ConfigChange 审计），v0.4 G2#8 = 9 段（新增 Setup maintenance），v0.4 G3#12 = 10 段（新增 SubagentStart 记忆注入）。
    CHANGELOG / migration-decisions / source-extraction-notes 等历史描述段不扫
    （属于版本演进记录，含 stale 数字是合理的）。
    """
    target_files = [
        FRAMEWORK_ROOT / "docs" / "concepts.md",
        FRAMEWORK_ROOT / "docs" / "setup-guide.md",
        FRAMEWORK_ROOT / "docs" / "quickstart.md",
        FRAMEWORK_ROOT / "docs" / "rules-sync.md",
        FRAMEWORK_ROOT / "docs" / "README.md",
        FRAMEWORK_ROOT / "docs" / "onboarding.md",
        FRAMEWORK_ROOT / "README.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "reference" / "skill-customization-guide.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "reference" / "project-architecture.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "claude-md-template.md",
        FRAMEWORK_ROOT / ".claude-plugin" / "marketplace.json",
    ]
    stale_patterns = [
        # v0.2.0 旧口径（15/16 时代）
        r"15\s*个\s*domain",
        r"15\s*domain\s*skills",
        r"5\s*\+\s*16\s*skills",
        r"16\s*skills(?!\s*=)",  # "16 skills" 但允许 "16 skills = ..."（数学说明场景）
        # v0.2.x stable 旧口径（21 时代）
        r"\b21\s+skills\b",
        r"\b21\s+通用",
        # v0.3 旧口径（27 时代）
        r"\b27\s+skills\b",
        r"\b7\s+system/maintenance\b",
        # v0.2.1 旧口径（28 时代，v0.3.x M3 起改为 33）
        r"\b28\s+skills\b",
        r"\b28\s+通用",
        r"\b29\s+skills\b",  # 防 v5 计算错误（28→29 漏算 4 modules-system）残留
        r"13\+8\+1\+6",      # 旧合计公式
        # v0.3.x M3 旧口径（33 时代，v0.4.0-dev html-demo 加入 docs/publishing 后改为 34）
        r"\b33\s+skills\b",
        r"\b33\s+通用",
        r"33\s*个：",
        r"\b7\s+docs/publishing",
        r"文档/发布工具（7\s*个）",
        # v0.4.0-dev 中期旧口径（34 时代，material-intake / doc-graph-health 补齐后为 36）
        r"\b34\s+skills\b",
        r"34\s*通用\s*skill",
        r"\b35\s+skills\b",  # 防漏算 1 个的中间残留
        # hook 段数不再在这里枚举历史错值——改由 `check_hook_stage_count_consistent`
        # 从 hooks.json 现算后正向断言（枚举式每升一版都要手工补新值，漏补即失守：
        # `10-stage` 就是这么在 marketplace.json 里活过一版的）。
        # 仅保留「N 段式」这种不带 hook 字样、正向断言正则覆盖不到的历史措辞：
        r"5\s*段式",
        r"6\s*段式",
        r"8\s*段式",
        r"9\s*段式",
    ]
    offenders = []
    missing = [p.name for p in target_files if not p.exists()]
    if missing:
        # 清单式闸的静默失守防线：目标文件改名/删除不该让检查无声降级（I-028）
        return _fail("no_stale_skill_count", f"target files missing (rename/delete须同步本清单): {missing}")
    for path in target_files:
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            for pat in stale_patterns:
                if re.search(pat, line):
                    offenders.append(f"{path.name}:{lineno}: '{line.strip()[:80]}'")
                    break
    if offenders:
        return _fail(
            "no_stale_skill_count",
            f"stale skill count / hook stage phrasing (current is 13+8+8+7=36; 11-stage hook since 2026-08-16): {offenders[:5]}",
        )
    return _ok("no_stale_skill_count")


def check_role_count_wording_matches_agents():
    """全仓「N 角色 / N agents」表述必须与 agents 目录实数一致（I-053 pmo 退役的对账闸）。

    背景：pmo 退役时退役词闸只扫 `pmo` 字面，「5 角色」这类数字表述全部漏网——docs 3 处 +
    插件侧 4 处直到 2026-08-13 文档走查才被人工发现，其中 proposal-page.md 那处会直接
    进 launcher 确认页。教训同 §五「闸只认词不认数」：roster 数字口径必须由机器对账。

    判定：数字 ≥3 且 ≠ agents 目录实数才违规——「≥2 角色」「跨 2 角色」是记忆写入条件等
    合法语义，不属于 roster 口径。两类计数语义放行（二检实测的误伤面）：范围写法
    「2~3 个角色」（lookbehind 排范围符）与「N 个角色提及/参与」（lookahead 排动词）。
    已知错误家族三种写法全覆盖：「5 角色」「5 baseline agents」「5 个 agent」
    （第三种是 2026-08-13 launcher SKILL 走查抓到的中英混排盲区——量词「个」+ 英文单数，
    前两个模式都不命中）。CHANGELOG（历史账）与 tools/ 不在扫描面。
    """
    name = "role_count_wording_matches_agents"
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    agent_count = len(list(agents_dir.glob("*.md")))
    if agent_count == 0:
        return _fail(name, "agents dir empty/missing — 对账基准丢失")
    scan_roots = [
        FRAMEWORK_ROOT / "plugins",
        FRAMEWORK_ROOT / "docs",
        FRAMEWORK_ROOT / "README.md",
        FRAMEWORK_ROOT / ".claude-plugin" / "marketplace.json",
    ]
    pat_zh = re.compile(
        r"(?<![0-9~\-–—])([0-9]+)\s*(?:个\s*)?(?:baseline\s*|通用\s*|默认\s*)?角色"
        r"(?!提及|参与|使用|命中|贡献)")
    pat_en = re.compile(r"(?<![0-9])([0-9]+)\s+(?:baseline\s+|core\s+)?agents?\b(?!-)", re.IGNORECASE)
    pat_mixed = re.compile(
        r"(?<![0-9~\-–—])([0-9]+)\s*个\s*(?:core\s+)?(?:agents?|roles?)\b", re.IGNORECASE)
    offenders = []
    for root in scan_roots:
        if not root.exists():
            return _fail(name, f"scan root missing: {root.relative_to(FRAMEWORK_ROOT)}")
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for p in paths:
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            raw = p.read_bytes()
            if b"\x00" in raw:
                continue
            for lineno, line in enumerate(
                    raw.decode("utf-8", errors="replace").splitlines(), 1):
                for m in (list(pat_zh.finditer(line)) + list(pat_en.finditer(line))
                          + list(pat_mixed.finditer(line))):
                    n = int(m.group(1))
                    if n >= 3 and n != agent_count:
                        offenders.append(
                            f"{p.relative_to(FRAMEWORK_ROOT)}:{lineno}: '{line.strip()[:70]}'")
    if offenders:
        return _fail(name, f"roster 数字与 agents 实数（{agent_count}）不符: {offenders[:5]}")
    return _ok(name, f"(agents={agent_count}, wording aligned)")


def check_signoff_table_alignment():
    """签发权限的三处事实源对账——task-management（schema 权威）、claude-md-template
    （渲染进每个新项目）、四个 agent 文件的 Step 3 扩展段。

    2026-08-13 core skills 深审实锤：模板 `in_progress → completed` 行只列 @pm/@dev，
    漏了 task-management 与 qa.md 都承认的 @qa（非研发任务）/@prompt-eng（非研发类咨询）
    ——与「doctor 清单 8 项漏 claude_md」「模板 5 通用角色」同病：清单与权威两处事实源
    没有机器对账就会漂。表格间比对按**角色集合**（`@pm|@dev|@qa|@prompt-eng`），不比措辞；
    agent 文件是 prose 形态，按签发契约 token 断言（不解析散文），另加一条语义锚：
    权威表的 `pending_qa → completed` 必须恰为 @qa 独签。
    """
    name = "signoff_table_alignment"
    src_tm = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "task-management" / "SKILL.md"
    src_tpl = FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "claude-md-template.md"

    def extract_rows(path):
        rows = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            # 左态允许「任意」——补齐 blocked / cancelled 出边后，
            # 原正则只认 [a-z_]+，那四行等于没进对账面
            m = re.match(r"\|\s*`((?:[a-z_]+|任意) → [a-z_]+)`\s*\|(.+)\|", line)
            if m:
                rows[m.group(1)] = frozenset(
                    re.findall(r"@(pm|dev|qa|prompt-eng)", m.group(2)))
        return rows

    missing_files = [str(p.relative_to(FRAMEWORK_ROOT)) for p in (src_tm, src_tpl) if not p.exists()]
    if missing_files:
        return _fail(name, f"对账源缺失: {missing_files}")

    # main-led 代行的两个契约值（2026-08-16 增）。tags 无代码层枚举校验，`main-executed`
    # 一旦从词表消失，A1 要求打的这个 tag 就变成「词表外造词」而无人报错；
    # 「签发权仍仅 @qa」则是自签禁令的 token，doctor 的 CLAUDE.md warn 档也钉它。
    tm_text = src_tm.read_text(encoding="utf-8")
    for token, why in (("main-executed", "tags 词表缺代行标记值，A1 要求打的 tag 会变成词表外造词"),
                       ("签发权仍仅 @qa", "自签禁令 token 缺失，doctor CLAUDE.md warn 档也钉这句")):
        if token not in tm_text:
            return _fail(name, f"task-management 缺 `{token}`——{why}")

    tm, tpl = extract_rows(src_tm), extract_rows(src_tpl)
    # 两档：角色由 @枚举 表达的行严格对账集合；其余行（执行主体是「assigned_to 角色」
    # 「任何角色」这类自然语言）只保证两侧都在——把自然语言硬解析成角色集合只会误报。
    required = {
        "in_progress → pending_qa", "pending_qa → completed",
        "pending_qa → blocked", "in_progress → completed",
    }
    required_present_only = {
        "pending → in_progress", "任意 → blocked",
        "blocked → in_progress", "任意 → cancelled",
    }
    errors = []
    for key in sorted(required | required_present_only):
        if key not in tm:
            errors.append(f"task-management 缺流转行 `{key}`")
        if key not in tpl:
            errors.append(f"claude-md-template 缺流转行 `{key}`")
        if key in required and key in tm and key in tpl and tm[key] != tpl[key]:
            errors.append(
                f"`{key}` 角色集合不一致: task-management={sorted(tm[key])} "
                f"vs template={sorted(tpl[key])}")

    # 第三份复述：agent 文件 Step 3 的签发契约 token（prose 形态，不做散文解析）
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    agent_tokens = {
        "qa.md": ("`pending_qa → completed`", "唯一"),
        "dev.md": ("`pending_qa`", "不得直接 `completed`"),
        "prompt-eng.md": ("`pending_qa`", "不得直接 `completed`"),
        "pm.md": ("直接流转到 `completed`",),
    }
    for fname, tokens in agent_tokens.items():
        p = agents_dir / fname
        if not p.exists():
            errors.append(f"agents/{fname} 缺失")
            continue
        text = p.read_text(encoding="utf-8")
        for tok in tokens:
            if tok not in text:
                errors.append(f"agents/{fname} 缺签发契约 token {tok!r}")
    # 语义锚：签发权唯一性——权威表的 pending_qa → completed 必须恰为 @qa 独签
    if tm.get("pending_qa → completed") != frozenset({"qa"}):
        errors.append(
            f"`pending_qa → completed` 应仅 @qa 独签，"
            f"task-management 实为 {sorted(tm.get('pending_qa → completed', []))}")

    if errors:
        return _fail(name, "; ".join(errors[:4]))
    return _ok(name, f"({len(required)} 行角色集合对账 + {len(required_present_only)} 行存在性对账)")


def check_no_plugin_root_env_in_skills():
    """skills 与 templates 文档禁用 `${CLAUDE_PLUGIN_ROOT}` 写法（I-040 防回退闸）。

    官方 plugins-reference：该变量作为环境变量仅注入 hook / MCP / LSP 子进程，Bash 工具
    子进程不在承诺内；skill 内容的字符串替换只在 CC 的 skill 加载通道发生。本框架大量走
    Read 通道（subagent 直读 SKILL.md 原文照抄命令），替换不发生 → shell 对未设置变量
    静默展开为空，命令以「找不到 /scripts/xxx」的迷惑姿态翻车（audit:50 实测教义）。
    统一配方：`$(cat .claude/workframe-state/plugin-root.txt)`。

    豁免仅限「把该写法当反例/说明对象」的教义行（按 文件名+行内特征串 精确豁免）。

    扫描面含 templates/：模板里的命令同样被用户与 agent 照抄（modules-template/README
    的 check-stale-modules 命令即以此写法逃过本闸），且实测扩容零误伤。

    扫描面含 reference/（2026-08-16 补）：那里是 launcher 与 7 个 modules-system skill
    的运行时知识源（module-architecture.md 开篇即自述「7 个 skill 依此规范工作」），
    agent 会读它并照抄命令。此前该目录在闸外，module-architecture §9 的
    `${CLAUDE_PLUGIN_ROOT}/scripts/check-stale-modules.py` 就这么活了下来——实测在
    agent Bash 里展开成 `python "/scripts/..."`，报「No such file or directory」，
    与本 docstring 描述的翻车姿态逐字吻合。
    """
    name = "no_plugin_root_env_in_skills"
    exempt_line_marks = [
        ("audit", "在 agent Bash 上下文不可用"),                 # audit:50 教义行
        ("document-norms", "见 skill: `obsidian-doc-structure`"),  # §5.2 反模式表 ❌ 列
        ("document-norms", "物理路径出现在 skill 引用中"),          # §10 反模式行
        ("module-architecture", "在 agent 的 Bash 上下文不可用"),   # §9 教义行（同 audit:50）
    ]
    skill_roots = [
        FRAMEWORK_ROOT / "plugins" / "core" / "skills",
        FRAMEWORK_ROOT / "plugins" / "core" / "templates",
        FRAMEWORK_ROOT / "plugins" / "core" / "reference",
        FRAMEWORK_ROOT / "plugins" / "workframe-launcher" / "skills",
    ]
    missing = [str(r.relative_to(FRAMEWORK_ROOT)) for r in skill_roots if not r.exists()]
    if missing:
        return _fail(name, f"scan roots missing: {missing}")
    offenders = []
    for root in skill_roots:
        for md in sorted(root.rglob("*.md")):
            for lineno, line in enumerate(md.read_text(encoding="utf-8").splitlines(), 1):
                if "${CLAUDE_PLUGIN_ROOT}" not in line and "$CLAUDE_PLUGIN_ROOT" not in line:
                    continue
                if any(k in str(md) and mark in line for k, mark in exempt_line_marks):
                    continue
                offenders.append(f"{md.relative_to(FRAMEWORK_ROOT)}:{lineno}")
    if offenders:
        return _fail(
            name,
            "skills 内 ${CLAUDE_PLUGIN_ROOT} 已知失效写法（改 plugin-root.txt 配方）: "
            + "; ".join(offenders[:5]),
        )
    return _ok(name, "(skills + templates 零 ${CLAUDE_PLUGIN_ROOT} 依赖)")


def check_postool_use_hook_exists():
    """v0.3.x M3：hooks.json 必须含 PostToolUse 段（modules/ stale 检测）。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "hooks" / "hooks.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail("postool_use_hook_exists", f"hooks.json parse: {e}")
    hooks = data.get("hooks", {})
    if "PostToolUse" not in hooks:
        return _fail("postool_use_hook_exists", "PostToolUse section missing")
    found = False
    for hg in hooks["PostToolUse"]:
        for h in hg.get("hooks", []):
            if "check-stale-modules.py" in h.get("command", ""):
                found = True
                break
    if not found:
        return _fail("postool_use_hook_exists", "PostToolUse does not call check-stale-modules.py")
    return _ok("postool_use_hook_exists")


def check_postool_use_hook_script_exists():
    """v0.3.x M3：plugins/core/scripts/check-stale-modules.py 必须存在且可解析。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "check-stale-modules.py"
    if not path.exists():
        return _fail("postool_use_hook_script_exists", "check-stale-modules.py missing")
    try:
        ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        return _fail("postool_use_hook_script_exists", f"syntax: {e}")
    text = path.read_text(encoding="utf-8")
    required_funcs = [
        "init_index_for_submodule",
        "rebuild_full_index",
        "scan_git_diff_for_stale",
        "process_postool_use",
    ]
    missing = [f for f in required_funcs if f"def {f}(" not in text]
    if missing:
        return _fail("postool_use_hook_script_exists", f"missing functions: {missing}")
    return _ok("postool_use_hook_script_exists", "(4 required functions present)")


def check_modules_system_skills_registered():
    """v0.3.x M3：4 个 modules-system skill 必须在 REQUIRED_SKILLS 内且实际存在。"""
    expected = MODULES_SYSTEM_SKILLS
    if not expected.issubset(REQUIRED_SKILLS):
        return _fail(
            "modules_system_skills_registered",
            f"REQUIRED_SKILLS missing: {expected - REQUIRED_SKILLS}",
        )
    skills_dir = FRAMEWORK_ROOT / "plugins" / "core" / "skills"
    missing_dirs = [s for s in expected if not (skills_dir / s / "SKILL.md").exists()]
    if missing_dirs:
        return _fail(
            "modules_system_skills_registered",
            f"SKILL.md missing for: {missing_dirs}",
        )
    return _ok(
        "modules_system_skills_registered",
        f"({len(expected)} modules-system skills present)",
    )


def check_check_stale_modules_unit_tests_pass():
    """v0.3.x M3：跑 tools/test_check_stale_modules.py 单元测试 + scan-git-diff 子命令分派回归。

    覆盖 glob_to_regex / first_static_segment / parse_code_paths / lookup_submodules
    + 关键回归：scan-git-diff 在 stdin 含 hook JSON 时仍能跑（防 fix-1 倒退）。
    """
    test_path = FRAMEWORK_ROOT / "tools" / "test_check_stale_modules.py"
    if not test_path.exists():
        return _fail(
            "check_stale_modules_unit_tests_pass",
            "tools/test_check_stale_modules.py missing",
        )
    try:
        # 显式 utf-8 + replace：测试输出含中文/✓/✗ 时不会被系统默认 codec（cp936）误读
        result = subprocess.run(
            [sys.executable, str(test_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except Exception as e:
        return _fail("check_stale_modules_unit_tests_pass", f"run error: {e}")
    if result.returncode != 0:
        # 取 stdout 末尾几行作为错误概览
        last_lines = result.stdout.strip().splitlines()[-6:]
        summary = " | ".join(last_lines)[:400]
        return _fail(
            "check_stale_modules_unit_tests_pass",
            f"test exit={result.returncode}: {summary}",
        )
    return _ok("check_stale_modules_unit_tests_pass", "(5 test groups passed)")


def check_modules_no_legacy_display_name_or_slug():
    """v0.3.x 防回潮：basic / sub 模块层已合并 `slug` + `display_name` → 单字段 `name`。

    禁止以下回潮：
    - basic-module/module.yaml 顶层 `slug:` / `display_name:`
    - sub-module/submodule.yaml 顶层 `slug:` / `display_name:`
    - basic-module/overview.md frontmatter `basic_slug:`
    - modules-template/ 下任何 .md / .yaml 含已废占位符
      `{{BASIC_SLUG}}` / `{{BASIC_DISPLAY_NAME}}` / `{{SUB_SLUG}}` / `{{SUB_DISPLAY_NAME}}`

    显式保留（按方案 X，不在防回潮范围）：
    - requirement/meta.yaml 的 `slug:` 字段（需求层无重复痛点；`title` 是独立人读名）
    - `{{REQ_SLUG}}` 占位符
    - 散落 10+ 处的 frontmatter `req_slug:` 引用契约（board.yaml / issues / prd / test-cases ...）

    理由参见 reference/module-architecture.md §5.1（name 字段语义）+ §5.2（需求层差异说明）。
    """
    template_root = (
        FRAMEWORK_ROOT
        / "plugins" / "core" / "templates" / "modules-template"
    )
    if not template_root.exists():
        return _fail(
            "modules_no_legacy_display_name_or_slug",
            f"modules-template missing: {template_root}",
        )

    failures = []

    # 1) basic + sub yaml schema 顶层字段不能再有 slug: / display_name:
    for yaml_rel in ("basic-module/module.yaml", "sub-module/submodule.yaml"):
        path = template_root / yaml_rel
        if not path.exists():
            failures.append(f"{yaml_rel}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        for legacy in ("slug:", "display_name:"):
            if re.search(rf"^{re.escape(legacy)}", text, re.MULTILINE):
                failures.append(
                    f"{yaml_rel}: legacy field `{legacy}` (merge to `name:`)"
                )

    # 2) basic-module/overview.md frontmatter 不能再有 basic_slug:
    overview_path = template_root / "basic-module" / "overview.md"
    if overview_path.exists():
        text = overview_path.read_text(encoding="utf-8")
        if re.search(r"^basic_slug:", text, re.MULTILINE):
            failures.append(
                "basic-module/overview.md: legacy `basic_slug:` (rename to `basic_name:`)"
            )

    # 3) modules-template/ 下所有 .md / .yaml 不能含已废占位符
    legacy_placeholders = (
        "{{BASIC_SLUG}}",
        "{{BASIC_DISPLAY_NAME}}",
        "{{SUB_SLUG}}",
        "{{SUB_DISPLAY_NAME}}",
    )
    for ext in ("*.md", "*.yaml"):
        for f in template_root.rglob(ext):
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            for ph in legacy_placeholders:
                if ph in text:
                    rel = f.relative_to(template_root).as_posix()
                    failures.append(f"{rel}: legacy placeholder `{ph}`")

    if failures:
        return _fail(
            "modules_no_legacy_display_name_or_slug",
            "; ".join(failures[:8])
            + (f" (+{len(failures) - 8} more)" if len(failures) > 8 else ""),
        )
    return _ok(
        "modules_no_legacy_display_name_or_slug",
        "(basic/sub yaml + overview frontmatter + 4 legacy placeholders all clean)",
    )


def check_modules_template_user_input_fields_quoted():
    """v0.3.x 防 YAML 1.1 类型推断：modules-template/ 下所有用户输入字段占位符引用
    必须用双引号包裹。

    理由：YAML 1.1（PyYAML 默认）会把 `123` / `2026` / `yes` / `no` / `true` / `false` /
    `null` / `2026-05-09` 推断为 number / bool / null / date，破坏字符串语义。
    用户为模块取数字开头名（如季度模块 `2026q1`）或边缘命名（`yes`）时会触发。

    扫描的 user-input 占位符：
    - `{{BASIC_NAME}}` / `{{SUB_NAME}}`（模块名，用户输入）
    - `{{MODULE_PATH}}`（二段式 `<basic>/<sub>`）
    - `{{REQ_SLUG}}` / `{{REQ_TITLE}}`（需求 slug + 标题）
    - `{{PROJECT_NAME}}`（项目名）

    显式不扫的 framework-hardcoded 字段（无类型推断风险）：
    - `{{OWNER_ROLE}}`（枚举 pm / dev / qa / prompt-eng）
    - `{{REQ_TYPE}}`（枚举 iterative / one-shot）
    - `{{NOW_ISO}}` / `{{TODAY}}`（datetime 推断符合 frontmatter 语义；obsidian dataview 按 datetime 用）
    """
    template_root = (
        FRAMEWORK_ROOT
        / "plugins" / "core" / "templates" / "modules-template"
    )
    if not template_root.exists():
        return _fail(
            "modules_template_user_input_fields_quoted",
            f"modules-template missing: {template_root}",
        )

    user_input_placeholders = (
        "BASIC_NAME", "SUB_NAME", "MODULE_PATH",
        "REQ_SLUG", "REQ_TITLE", "SUB_REQ_SLUG", "PROJECT_NAME",
    )
    # 命中 `field: {{PLACEHOLDER}}` 不带引号（缺前导 `"`）
    unquoted_pattern = re.compile(
        r'^[^\n#]*?:\s*\{\{(' + "|".join(user_input_placeholders) + r')\}\}',
        re.MULTILINE,
    )

    failures = []
    for ext in ("*.md", "*.yaml"):
        for f in template_root.rglob(ext):
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            for m in unquoted_pattern.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                rel = f.relative_to(template_root).as_posix()
                placeholder = m.group(1)
                failures.append(f"{rel}:{line_no} `{{{{{placeholder}}}}}` not quoted")

    if failures:
        return _fail(
            "modules_template_user_input_fields_quoted",
            "; ".join(failures[:8])
            + (f" (+{len(failures) - 8} more)" if len(failures) > 8 else ""),
        )
    return _ok(
        "modules_template_user_input_fields_quoted",
        "(all user-input placeholders quoted with double-quotes)",
    )


def check_modules_no_legacy_v_dir_or_versions_timeline():
    """v0.4.x 防回潮：需求层去版本化后（v<N>/ 目录改为 <sub_req_slug>/，
    versions-timeline 索引段改为 sub-requirements-index），active 文档不能再
    出现旧版本路径或索引段名。

    扫描范围（active docs，不含历史归档与校验脚本自身）：
    - plugins/core/{skills,agents,rules}/**/*.{md,yaml}
    - docs/**/*.md
    - README.md

    显式排除：
    - CHANGELOG.md（不在范围内；历史记录保留原样）
    - tools/validate.py 自身（本函数模式列表必然命中）

    检测的旧结构模式：
    - `requirements/<slug>/v<N>`（文档占位符字面量）
    - `requirements/[^/]+/v\\d+/`（具体路径实例如 v1/ v2/）
    - `requirements/[^/]+/v\\*/`（glob 模式）
    - `v<N>/prd.md` / `v<N>/test-cases`（子目录引用）
    - `versions-timeline`（已改为 sub-requirements-index）
    - `^\\s*version:\\s*v\\d+\\b`（frontmatter 字段已废除）
    """
    patterns = [
        # 占位符字面量：通配所有 <xxx>/v<N> 形式（含 <slug> / <req_slug> 等）
        re.compile(r"requirements/<[^>]+>/v<N>"),
        # 具体路径实例：requirements/avatar-cropper/v1/ 等
        re.compile(r"requirements/[^/\s]+/v\d+/"),
        # glob 模式：requirements/foo/v*/
        re.compile(r"requirements/[^/\s]+/v\*/"),
        # 子目录引用扩展：v<N>/{prd,flowchart,test-cases,prototypes,reviews,assets}
        re.compile(r"v<N>/(prd|flowchart|test-cases|prototypes|reviews|assets)"),
        # 索引段名（已改为 sub-requirements-index）
        re.compile(r"versions-timeline"),
        # frontmatter version 字段：覆盖 v1 / "v1" / 'v1' / v<N> 三种变体
        re.compile(r"""^\s*version:\s*["']?v[\d<]""", re.MULTILINE),
    ]

    scan_roots = [
        FRAMEWORK_ROOT / "plugins" / "core" / "skills",
        FRAMEWORK_ROOT / "plugins" / "core" / "agents",
        FRAMEWORK_ROOT / "plugins" / "core" / "rules",
        FRAMEWORK_ROOT / "docs",
    ]
    single_files = [FRAMEWORK_ROOT / "README.md"]

    failures = []
    files_to_scan = list(single_files)
    for root in scan_roots:
        if not root.exists():
            continue
        for ext in ("*.md", "*.yaml"):
            files_to_scan.extend(root.rglob(ext))

    for f in files_to_scan:
        if not f.exists():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for pat in patterns:
            m = pat.search(text)
            if m:
                line_no = text.count("\n", 0, m.start()) + 1
                rel = f.relative_to(FRAMEWORK_ROOT).as_posix()
                failures.append(f"{rel}:{line_no} `{m.group(0)[:60]}`")
                break  # 每个文件报第一个命中即可

    # 路径级扫描：modules-template/ 下不能有 v\d+/ 目录残留（避免目录回潮）
    template_root = (
        FRAMEWORK_ROOT
        / "plugins" / "core" / "templates" / "modules-template"
    )
    path_failures = []
    if template_root.exists():
        legacy_dir_pat = re.compile(r"^v\d+$")
        for d in template_root.rglob("*"):
            if d.is_dir() and legacy_dir_pat.match(d.name):
                rel = d.relative_to(FRAMEWORK_ROOT).as_posix()
                path_failures.append(f"legacy version dir: {rel}/")

    if failures or path_failures:
        all_failures = failures + path_failures
        return _fail(
            "modules_no_legacy_v_dir_or_versions_timeline",
            "; ".join(all_failures[:8])
            + (f" (+{len(all_failures) - 8} more)" if len(all_failures) > 8 else ""),
        )
    return _ok(
        "modules_no_legacy_v_dir_or_versions_timeline",
        f"(scanned {len(files_to_scan)} active docs + modules-template paths; no legacy v<N>/ residue)",
    )


def check_modules_no_legacy_req_type():
    """v0.4.x 防回潮：需求资产包的 `type: iterative | one-shot` 字段和
    `{{REQ_TYPE}}` 占位符已废除（需求版本机制下沉到 prd.md「变更与决策记录」，
    目录结构不再按需求类型区分），modules-template 下不能再出现。

    扫描范围：
    - plugins/core/templates/modules-template/**/*.{md,yaml}

    检测：
    - yaml 顶层 `^type:\\s*(iterative|one-shot)\\b`
    - 占位符 `{{REQ_TYPE}}`
    """
    template_root = (
        FRAMEWORK_ROOT
        / "plugins" / "core" / "templates" / "modules-template"
    )
    if not template_root.exists():
        return _fail(
            "modules_no_legacy_req_type",
            f"modules-template missing: {template_root}",
        )

    type_pat = re.compile(r"^type:\s*(iterative|one-shot)\b", re.MULTILINE)
    placeholder_pat = re.compile(r"\{\{REQ_TYPE\}\}")

    failures = []
    for ext in ("*.md", "*.yaml"):
        for f in template_root.rglob(ext):
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            rel = f.relative_to(template_root).as_posix()
            for m in type_pat.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                failures.append(f"{rel}:{line_no} legacy `{m.group(0)}`")
            for m in placeholder_pat.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                failures.append(f"{rel}:{line_no} legacy placeholder `{{{{REQ_TYPE}}}}`")

    if failures:
        return _fail(
            "modules_no_legacy_req_type",
            "; ".join(failures[:8])
            + (f" (+{len(failures) - 8} more)" if len(failures) > 8 else ""),
        )
    return _ok(
        "modules_no_legacy_req_type",
        "(no legacy type: iterative/one-shot or {{REQ_TYPE}} placeholder)",
    )


def check_scaffold_framework_version_dynamic():
    """写进项目的 framework_version 必须动态读 plugin.json，不能是字面量。

    历史：曾经字面量写死 0.1.0，早就偏离 plugin 实际版本却没人发现——每次发版都要记得
    改字面量，必漏。守护对象随 2026-08-10 `tools/install.py` 退役从 install.py 转到
    `project_scaffold.py`（唯一实现），不变量本身一字未变。
    """
    name = "scaffold_framework_version_dynamic"
    scaffold = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    if not scaffold.exists():
        return _fail(name, "project_scaffold.py missing")
    text = scaffold.read_text(encoding="utf-8")
    if re.search(r'FRAMEWORK_VERSION\s*=\s*["\']\d+\.\d+\.\d+["\']', text):
        return _fail(name, "project_scaffold.py 含字面量版本号（应走 read_framework_version()）")
    if "def read_framework_version" not in text:
        return _fail(name, "project_scaffold.py 缺 read_framework_version()")
    m = re.search(r"def read_framework_version\(\):.*?(?=\ndef )", text, re.S)
    if not m:
        return _fail(name, "read_framework_version() 函数体解析失败")
    body = m.group(0)
    if "plugin.json" not in body:
        return _fail(name, "read_framework_version() 未从 plugin.json 取值")
    # 只查「函数体里提到 plugin.json」不够——二检实证：把 return 换成字面量、保留上面
    # 那行读取语句，检查照样放行。必须连返回值一起守。
    lit = re.search(r"""return\s+["']\d+\.\d+""", body)
    if lit:
        return _fail(name, f"read_framework_version() 直接返回字面量版本号: {lit.group(0)!r}")
    return _ok(name)


def check_scaffold_user_fields_never_overwritten():
    """scaffold 只写用户确认过的配置值，且**绝不覆盖**已有值。

    语义重定义（2026-08-10）：原检查断言「不写 role_profile」，是 create-project 时代的口径——
    那时 scaffold 无从推断该值。3c-1 引入 `--params` 后 scaffold **确实会写**（值来自 launcher
    对话中用户确认的结果），于是原检查的两个匹配点（`config = {...}` 字面量、
    `existing["role_profile"]` 硬编码）在新实现里都不存在，**它已空转、不保护任何东西**。

    真正要守的不变量变成了：三个用户字段一律走 `setdefault`（本地缺失才写），
    绝不用 `existing[key] = ...` 直接覆盖——接入已有项目时用户可能早就手工配过。
    """
    name = "scaffold_user_fields_never_overwritten"
    path = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    if not path.exists():
        return _fail(name, "project_scaffold.py missing")
    text = path.read_text(encoding="utf-8")
    func_match = re.search(r"def write_workframe_config\([^)]*\):.*?(?=\ndef |\Z)", text, re.DOTALL)
    if not func_match:
        return _fail(name, "write_workframe_config 未找到")
    body = func_match.group(0)
    if "CONFIG_USER_FIELDS" not in text:
        return _fail(name, "缺 CONFIG_USER_FIELDS 常量（用户字段清单应集中声明）")
    if "setdefault" not in body:
        return _fail(name, "write_workframe_config 未用 setdefault —— 用户已有配置会被覆盖")
    # 用户字段一律不得直接赋值覆盖
    for field in ("project_type", "dormant_profile", "role_profile"):
        if re.search(rf'existing\[\s*["\']{field}["\']\s*\]\s*=', body):
            return _fail(name, f"write_workframe_config 直接覆盖用户字段 {field}（应走 setdefault）")
    return _ok(name)



def check_release_consistency():
    """发布前一致性扫描：marketplace.json 的旧口径零残留。

    曾经还扫一类「真实来源项目名出现在运行时代码里」。那道扫描的**关键词本身就是
    真实项目名**——等于把它永久写进公开仓库，而工作区之所以显示"干净"，只是因为
    检测器自己在白名单里。该名字已从工作区全部资产清除，扫描随之移除。

    更早还扫过 install.py（"3 core rules"）与 dev-docs 两份文档，随 2026-08-10
    install.py 退役 / dev-docs 剥离为私有仓一并移除。
    """
    offenders = []

    # marketplace.json: 旧 skill 分组口径 / 旧 hook 段数
    mkt_path = FRAMEWORK_ROOT / ".claude-plugin" / "marketplace.json"
    if mkt_path.exists():
        mkt_text = mkt_path.read_text(encoding="utf-8")
        stale_mkt = {
            "15 domain": r"15\s+domain",
            "5 maintenance/system": r"5\s+maintenance",
            "4-stage hook pipeline": r"4-stage\s+hook",
        }
        for label, pat in stale_mkt.items():
            if re.search(pat, mkt_text, re.IGNORECASE):
                offenders.append(f"marketplace.json: found '{label}' (stale)")

    if offenders:
        return _fail("release_consistency", f"{len(offenders)} stale pattern(s): {offenders[:6]}")
    return _ok("release_consistency")


def check_self_iteration_allowed_tools_complete():
    """self-iteration/SKILL.md 的 allowed-tools 必须包含 AskUserQuestion（阶段 4 交互）和 Bash（阶段 5 移文件/备份）。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "self-iteration" / "SKILL.md"
    if not path.exists():
        return _fail("self_iteration_allowed_tools_complete", "SKILL.md missing")
    text = path.read_text(encoding="utf-8")
    # 找 frontmatter 的 allowed-tools 行
    for line in text.splitlines():
        if line.strip().startswith("allowed-tools:"):
            missing = [t for t in ("AskUserQuestion", "Bash") if t not in line]
            if missing:
                return _fail("self_iteration_allowed_tools_complete", f"allowed-tools missing: {missing}")
            return _ok("self_iteration_allowed_tools_complete")
    return _fail("self_iteration_allowed_tools_complete", "allowed-tools line not found in frontmatter")


def check_self_iteration_score_formula_uses_risk_penalty():
    """self-iteration/SKILL.md score 公式必须使用 risk_penalty（不得用旧的整数 risk 减法）。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "self-iteration" / "SKILL.md"
    if not path.exists():
        return _fail("self_iteration_score_formula_uses_risk_penalty", "SKILL.md missing")
    text = path.read_text(encoding="utf-8")
    if "risk_penalty" not in text:
        return _fail(
            "self_iteration_score_formula_uses_risk_penalty",
            "risk_penalty not found; score formula must be impact_int × confidence - risk_penalty",
        )
    return _ok("self_iteration_score_formula_uses_risk_penalty")


def check_self_iteration_proposal_schema_complete():
    """self-iteration/SKILL.md 的提案 YAML 示例必须包含 eval_cases 和 rejected_at 字段（P0-2 修复验证）。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "self-iteration" / "SKILL.md"
    if not path.exists():
        return _fail("self_iteration_proposal_schema_complete", "SKILL.md missing")
    text = path.read_text(encoding="utf-8")
    required = ["eval_cases:", "rejected_at:", "rejection_reason:", "source_pending_maintenance:"]
    missing = [f for f in required if f not in text]
    if missing:
        return _fail("self_iteration_proposal_schema_complete", f"proposal schema missing fields: {missing}")
    return _ok("self_iteration_proposal_schema_complete")


def check_eval_case_03_no_stale_problem_score():
    """eval case 03 不得声称 proposal_failed 会增加 check-iteration-trigger.py 的 problem 加权分。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "self-iteration" / "eval-cases" / "03-regress-proposal-failed-loop.md"
    if not path.exists():
        return _fail("eval_case_03_no_stale_problem_score", "eval case 03 file missing")
    text = path.read_text(encoding="utf-8")
    # 旧口径：声称 check-iteration-trigger 的 problem 分 +3.0
    stale_patterns = [
        r"check-iteration-trigger.*problem.*\+3\.0",
        r"problem\s*分\s*\+3\.0",
        r"\+3\.0\s*(?:problem|加权)",
    ]
    offenders = []
    for pat in stale_patterns:
        if re.search(pat, text):
            offenders.append(pat)
    if offenders:
        return _fail(
            "eval_case_03_no_stale_problem_score",
            "eval case 03 still claims proposal_failed adds +3.0 to check-iteration-trigger problem score (removed v0.2.2-fixup-2)",
        )
    return _ok("eval_case_03_no_stale_problem_score")


def check_proposal_applied_event_sample_complete():
    """self-iteration/SKILL.md 的 proposal_applied 事件样例必须包含 applied_option 字段。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "self-iteration" / "SKILL.md"
    if not path.exists():
        return _fail("proposal_applied_event_sample_complete", "SKILL.md missing")
    text = path.read_text(encoding="utf-8")
    # 找包含 "proposal_applied" 的 JSON 样例行，检查其是否含 applied_option
    for line in text.splitlines():
        if '"type":"proposal_applied"' in line or '"type": "proposal_applied"' in line:
            if "applied_option" not in line:
                return _fail(
                    "proposal_applied_event_sample_complete",
                    'proposal_applied JSON sample missing "applied_option" field',
                )
            return _ok("proposal_applied_event_sample_complete")
    return _fail("proposal_applied_event_sample_complete", "no proposal_applied JSON sample found in SKILL.md")


def check_proposal_verified_event_sample_complete():
    """self-iteration/SKILL.md 的 proposal_verified 事件样例必须包含 ts/proposal_id/signal_met 字段。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "self-iteration" / "SKILL.md"
    if not path.exists():
        return _fail("proposal_verified_event_sample_complete", "SKILL.md missing")
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if '"type":"proposal_verified"' in line or '"type": "proposal_verified"' in line:
            missing = [f for f in ('"ts"', '"proposal_id"', '"signal_met"') if f not in line]
            if missing:
                return _fail(
                    "proposal_verified_event_sample_complete",
                    f'proposal_verified JSON sample missing fields: {missing}',
                )
            return _ok("proposal_verified_event_sample_complete")
    return _fail("proposal_verified_event_sample_complete", "no proposal_verified JSON sample found in SKILL.md")


def check_proposal_failed_event_sample_complete():
    """self-iteration/SKILL.md 的 proposal_failed 事件样例必须包含 ts/proposal_id 字段。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "self-iteration" / "SKILL.md"
    if not path.exists():
        return _fail("proposal_failed_event_sample_complete", "SKILL.md missing")
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if '"type":"proposal_failed"' in line or '"type": "proposal_failed"' in line:
            missing = [f for f in ('"ts"', '"proposal_id"') if f not in line]
            if missing:
                return _fail(
                    "proposal_failed_event_sample_complete",
                    f'proposal_failed JSON sample missing fields: {missing}',
                )
            return _ok("proposal_failed_event_sample_complete")
    return _fail("proposal_failed_event_sample_complete", "no proposal_failed JSON sample found in SKILL.md")


def check_event_samples_have_required_schema_fields():
    """v0.2.x 事件链路审查：所有 SKILL.md 中的事件 JSON 样例必须含 schema 定义的所有 required 字段。

    扫描所有 producer SKILL.md，对每个 events.jsonl JSON 样例：
      1. 提取 "type":"<event_name>"
      2. 查 event-schema.json 的 fields 列表
      3. 'required' 字段判定：fields 描述中含 'required' 字符串
      4. 比对样例 JSON 中是否含这些字段名（按 "<field>" 字符串匹配，足够鲁棒）

    覆盖：rollback_applied / pending_maintenance_dismissed / maintenance_review_completed /
         memory_promoted / memory_decayed / proposal_applied / proposal_verified / proposal_failed /
         skill_used / user_correction / task_blocked
    """
    schema_path = FRAMEWORK_ROOT / "plugins" / "core" / ".workframe-meta" / "event-schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail("event_samples_have_required_schema_fields", f"schema read failed: {e}")

    producer_docs = [
        FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "librarian" / "SKILL.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "self-iteration" / "SKILL.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "rollback" / "SKILL.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "maintenance-review" / "SKILL.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "test-case-design" / "SKILL.md",
        FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core" / "agent-protocols.md",
    ]

    events_required = {}
    for ev_name, spec in schema.get("events", {}).items():
        if spec.get("reliability") == "removed_v0_2_1":
            continue
        required = []
        for fname, fdesc in (spec.get("fields") or {}).items():
            if isinstance(fdesc, str) and "required" in fdesc.lower():
                # 排除"only when status=ok"等条件式 required
                if "only when" in fdesc.lower():
                    continue
                required.append(fname)
        events_required[ev_name] = required

    offenders = []
    missing = [d.name for d in producer_docs if not d.exists()]
    if missing:
        # 清单式闸的静默失守防线（I-028）：producer 文档改名/删除须同步本清单
        return _fail("event_samples_have_required_schema_fields", f"producer docs missing: {missing}")
    for doc in producer_docs:
        text = doc.read_text(encoding="utf-8")
        for ev_name, req_fields in events_required.items():
            type_re = re.compile(rf'"type"\s*:\s*"{re.escape(ev_name)}"')
            for line in text.splitlines():
                if not type_re.search(line):
                    continue
                missing = [f for f in req_fields if f'"{f}"' not in line]
                if missing:
                    offenders.append(
                        f"{doc.relative_to(FRAMEWORK_ROOT)}: {ev_name} missing required {missing}"
                    )
                    break  # 同一 SKILL.md 同一事件只报一次

    if offenders:
        return _fail("event_samples_have_required_schema_fields", "; ".join(offenders[:10]))
    return _ok("event_samples_have_required_schema_fields")


def check_agent_protocols_documents_success_false_and_session_id():
    """agent-protocols.md Step 1 必须同时说明 success=true / success=false 触发条件，并提到 session_id 写入。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core" / "agent-protocols.md"
    if not path.exists():
        return _fail("agent_protocols_documents_success_false_and_session_id", "agent-protocols.md missing")
    text = path.read_text(encoding="utf-8")
    missing = []
    if "success" not in text or '"success":true' not in text.replace(" ", "").replace("'", '"'):
        missing.append("success:true sample")
    if "false" not in text.lower() or "success" not in text:
        missing.append("success:false guidance")
    # 更精确的检查：同时含 true / false 取值规则的描述
    if "success" in text:
        normalized = text.lower()
        has_false_rule = ("`false`" in text) or ('"success":false' in text.replace(" ", "")) or ("success=false" in normalized) or ("`false`" in normalized)
        if not has_false_rule:
            missing.append("explicit success=false rule")
    if "session_id" not in text:
        missing.append("session_id write guidance")
    if "task_blocked" not in text:
        missing.append("task_blocked event append guidance")
    if missing:
        return _fail("agent_protocols_documents_success_false_and_session_id", f"agent-protocols.md missing: {missing}")
    return _ok("agent_protocols_documents_success_false_and_session_id")


def check_test_case_design_appends_task_blocked():
    """test-case-design SKILL.md 第 4 步 blocked 状态变更必须 append task_blocked 事件。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "test-case-design" / "SKILL.md"
    if not path.exists():
        return _fail("test_case_design_appends_task_blocked", "SKILL.md missing")
    text = path.read_text(encoding="utf-8")
    has_event = re.search(r'"type"\s*:\s*"task_blocked"', text) is not None
    has_events_jsonl = "events.jsonl" in text
    if not (has_event and has_events_jsonl):
        return _fail(
            "test_case_design_appends_task_blocked",
            f"missing task_blocked append: type_literal={has_event}, events.jsonl_mention={has_events_jsonl}",
        )
    return _ok("test_case_design_appends_task_blocked")


def check_self_iteration_no_proposals_kinds_match_trigger_script():
    """self-iteration no-proposals 路径列出的 PM kind 必须覆盖 check-iteration-trigger.py 实际写入的全部 kind。"""
    skill_path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "self-iteration" / "SKILL.md"
    script_path = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "check-iteration-trigger.py"
    if not (skill_path.exists() and script_path.exists()):
        return _fail("self_iteration_no_proposals_kinds_match_trigger_script", "files missing")

    script_text = script_path.read_text(encoding="utf-8")
    # 提取 signals.append((  "<kind>", ... 中的 kind 字面量
    script_kinds = set(re.findall(r'signals\.append\(\(\s*"([^"]+)"', script_text))
    if not script_kinds:
        return _fail("self_iteration_no_proposals_kinds_match_trigger_script", "could not extract kinds from trigger script")

    skill_text = skill_path.read_text(encoding="utf-8")
    # 找 no-proposals 段：从"无任何提案生成"到"### 阶段 4"
    m = re.search(r"无任何提案生成.*?(?=### 阶段 4)", skill_text, re.DOTALL)
    if not m:
        return _fail("self_iteration_no_proposals_kinds_match_trigger_script", "could not find no-proposals section")
    section = m.group(0)
    missing = sorted(k for k in script_kinds if f"`{k}`" not in section)
    if missing:
        return _fail(
            "self_iteration_no_proposals_kinds_match_trigger_script",
            f"no-proposals path missing kinds {missing}; trigger script writes {sorted(script_kinds)}",
        )
    return _ok("self_iteration_no_proposals_kinds_match_trigger_script")



def check_text_writes_pin_newline():
    """所有文本写入必须显式 `newline=""`——否则 Windows 上 LF 静默变 CRLF。

    Python 文本模式默认 `newline=None`，写出时把每个换行翻成平台行尾。后果分两层：

    1. **进 git 的产物**（memory-index.json / board.yaml / CLAUDE.md / .gitignore …）
       被 hook 写一次就整文件变 CRLF，git 判定每行都改，真实改动淹没在噪声里。
       实测：只改 1 个字段的写入产出 349 insertions / 349 deletions。
    2. **按内容 hash 对账的检查**（doctor 的 rules 镜像比对等）恒报不一致——
       源是 LF、副本是 CRLF，字节不同但内容相同。

    这个坑框架里早认识过：`module_init.py` 有个「统一 LF 写盘」的辅助函数，注释写得
    明明白白——**但只修了那一处，没扫全仓**，`_state_io.atomic_write`（所有 state
    文件的公共写入路径）一直漏着。本闸把「扫全仓」变成机器保证。

    只查写入调用（`write_text` / `open` 的 w|a 模式），不碰读取。二进制写
    （`write_bytes` / `"wb"`）天然无此问题，不在范围内。
    """
    import re
    offenders = []
    re_wt = re.compile(r"[.]write_text[(]")
    re_open = re.compile("open[(][^)]*[\"'][wa][+]?[\"']")

    def _call_text(lines, idx):
        """从 lines[idx] 起累积到括号配平，返回整个调用的文本。

        必须按**完整调用**判断而不是按行：真实代码里 `newline=""` 常常落在续行上
        （`write_text(\n    payload,\n    encoding=..., newline="")`）。按行判断会把
        这些已经修好的写入全报成违规——初版就是这么误报了 11 处，差点让人以为
        全仓都没修。
        """
        buf, depth = [], 0
        for line in lines[idx:idx + 12]:
            buf.append(line)
            depth += line.count("(") - line.count(")")
            if depth <= 0 and len(buf) > 0:
                break
        return " ".join(buf)

    for root in ("plugins", "tools"):
        base = FRAMEWORK_ROOT / root
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in str(p):
                continue
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            for i, line in enumerate(lines):
                s = line.strip()
                # 跳过注释与 docstring 正文——那里出现的 open('w') 是在讲这个坑本身
                if s.startswith("#") or '"""' in s or s.startswith(">"):
                    continue
                binary = any(m in s for m in ('"wb"', "'wb'", '"ab"', "'ab'", '"rb"', "'rb'"))
                if not (re_wt.search(s) or (re_open.search(s) and not binary)):
                    continue
                if "newline" in _call_text(lines, i):
                    continue
                rel = p.relative_to(FRAMEWORK_ROOT).as_posix()
                offenders.append(rel + ":" + str(i + 1) + "  " + s[:70])
    if offenders:
        head = "\n    ".join(offenders[:8])
        more = ("\n    …另有 " + str(len(offenders) - 8) + " 处") if len(offenders) > 8 else ""
        return _fail("text_writes_pin_newline",
                     str(len(offenders)) + " 处文本写入未钉 newline（Windows 上 LF 会变 CRLF）：\n    "
                     + head + more)
    return _ok("text_writes_pin_newline", "全部文本写入均已钉 newline")


def check_utf8_stream_wrap_symmetric():
    """入口脚本必须把 stdout 与 stderr **成对**包成 UTF-8——只包一条比两条都不包更坏。

    中文 Windows 控制台是 cp936。只包 stdout 时，正常日志好好的，**报错路径却会自己崩**：

    1. 脚本的 warning / error 文案本身带中文与符号（`module_init` 的 `⚠ {warning}`、
       `_state_io` 的「状态未落盘，默认值覆盖」），`print(..., file=sys.stderr)` 当场
       抛 `UnicodeEncodeError`；
    2. 更普遍的是**未捕获异常的 traceback**——它无条件走 stderr，而里头印着文件路径。
       用户主目录含中文是常态（`C:\\Users\\<中文名>\\...`），于是 traceback 自己编码失败。

    两种情况下真实错因都被这层二次崩溃顶掉，你只拿到一句 `UnicodeEncodeError`——
    偏偏是最需要看清现场的时刻。这不是假想：2026-08-17 一次 PRD 落盘过程中就撞上了，
    当时 `plugins/core/scripts/` 的 20 个运行时脚本里 15 个只包 stdout、2 个只包 stderr，
    **没有一个是成对的**；skills 与 eval-cases 各自的 `scripts/` 还有 4 个同类。

    因此规则取「成对」而非「按需」：不去猜哪个脚本会输出中文（续行里的中文、第三方库
    抛出的异常消息都猜不到），凡入口脚本一律两条都包。判据是文件里有 `def main(`
    或 `__name__ == "__main__"`。

    两类豁免，同一个理由——**一个进程里只能有一层包装**：

    - 下划线开头的模块（`_state_io.py`）：被 import 的 library，docstring 里立了
      「import 无副作用（不碰 stdout）」的契约，输出在宿主进程里执行；
    - 用 `spec_from_file_location` 加载别的脚本的（`tools/sync-rules.py`、
      `tools/test_check_stale_modules.py`）：被加载的 `check-stale-modules.py` /
      `plugins/core/scripts/sync-rules.py` 是**模块级**包装，加载那一刻两条流已经包好。

    自己再包一层就是两个 TextIOWrapper 抢同一个 buffer：先被回收的把 buffer 关掉，
    另一个当场 `ValueError: I/O operation on closed file` + `lost sys.stderr`。
    落这道闸时给这两个文件加过包装，`tools/sync-rules.py` 当场全崩——**而那一轮
    validate 是全绿的**，因为没有闸会去跑它。豁免不是图省事，是被实测逼出来的；
    也是一处提醒：本闸只保证「包装成对」，保证不了「脚本还能跑」。
    """
    # 两种合法形态都认：替换成 TextIOWrapper（全仓主流）与原地 reconfigure
    # （`assert_three_ledgers.py` 用的写法，Python 3.7+；它不新建 wrapper，
    # 天然没有抢 buffer 的问题）。只认前者会把后者误报成「完全没包」。
    WRAP_OUT = ("sys.stdout = io.TextIOWrapper(sys.stdout.buffer", "sys.stdout.reconfigure(")
    WRAP_ERR = ("sys.stderr = io.TextIOWrapper(sys.stderr.buffer", "sys.stderr.reconfigure(")
    # 豁免用显式白名单而不是「含 spec_from_file_location 就跳过」那类特征判据——
    # 后者会连 validate.py 自己一起放行（它也用 importlib 加载脚本），闸当场被掏空。
    # 新增豁免必须在此写清它的流由谁负责。
    exempt = {
        "tools/sync-rules.py":
            "加载 plugins/core/scripts/sync-rules.py，那边是模块级包装",
        "tools/test_check_stale_modules.py":
            "加载 check-stale-modules.py，那边是模块级包装",
    }
    # 范围不能只有 plugins/core/scripts——skills 与 eval-cases 各自带 scripts/，
    # 那里的脚本同样由模型在中文 Windows 上直接 `python ...` 调起，中文输出更多
    # （`graph_health.py` 一千七百多个非 ASCII 字符）。初版闸漏了这 4 个，
    # 而 docstring 立的规则是「凡入口脚本一律两条都包」——规则比实现宽，等于没管。
    targets = [p for p in sorted((FRAMEWORK_ROOT / "tools").glob("*.py"))]
    core = FRAMEWORK_ROOT / "plugins" / "core"
    if core.is_dir():
        targets += [p for p in sorted(core.rglob("scripts/*.py"))
                    if "__pycache__" not in p.parts]
    offenders = []
    checked = 0
    for p in targets:
        if p.name.startswith("_"):
            continue
        if p.relative_to(FRAMEWORK_ROOT).as_posix() in exempt:
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        if "def main(" not in src and '__name__ == "__main__"' not in src:
            continue
        checked += 1
        has_out = any(w in src for w in WRAP_OUT)
        has_err = any(w in src for w in WRAP_ERR)
        if has_out and has_err:
            continue
        missing = "stderr" if has_out else ("stdout" if has_err else "stdout + stderr")
        offenders.append(p.relative_to(FRAMEWORK_ROOT).as_posix() + "  缺 " + missing)
    if offenders:
        head = "\n    ".join(offenders[:8])
        more = ("\n    …另有 " + str(len(offenders) - 8) + " 处") if len(offenders) > 8 else ""
        return _fail("utf8_stream_wrap_symmetric",
                     str(len(offenders)) + " 个入口脚本的 UTF-8 流包装不成对"
                     "（报错路径会在 cp936 控制台二次崩溃）：\n    " + head + more)
    return _ok("utf8_stream_wrap_symmetric",
               str(checked) + " 个入口脚本的 stdout/stderr 均已成对包 UTF-8")


def check_doctor_readonly_on_probe_project():
    """doctor 跑一遍**不得改变被检项目的状态**——体检工具不能把被检对象弄坏。

    2026-08-16 实证的观察者效应：`_record_acceptance` 无条件调 `mark_setup_step`，而它
    「文件不存在就新建」。于是给一个**手工接入的健康老项目**（合法地没有 setup-state.json，
    doctor 本来报 info 放行）跑一次 `--group install`，doctor 自己造出一份只含 `acceptance`
    的文件；第二次跑时 `check_setup_state` 看见文件存在却缺 scaffold/subscribe/sync_rules，
    报 error「初始化未走完 0/3 步」——项目其实装得好好的，是体检把它「检坏」的，而且不可逆
    （用户不会知道那份文件该删）。这类 bug 静态扫描永远看不见：单跑一次全绿，跑第二次才红。

    做法：最小沙盒项目上对 `.claude/` 做跑前跑后全量快照（相对路径 + 内容 hash），
    断言零差异。范围是整个目录而不只是 setup-state.json——将来任何检查顺手写点什么进
    被检项目，都会在这里当场暴露。
    """
    import hashlib
    import os
    import subprocess
    import tempfile
    doctor = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    if not doctor.exists():
        return _fail("doctor_readonly_on_probe_project", "workframe_doctor.py 不存在")
    src_rules = FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core"

    def snapshot(root):
        snap = {}
        for p in sorted(root.rglob("*")):
            if p.is_file():
                snap[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
        return snap

    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        state = proj / ".claude" / "workframe-state"
        mirror = proj / ".claude" / "rules" / "workframe" / "core"
        state.mkdir(parents=True)
        mirror.mkdir(parents=True)
        (state / "plugin-root.txt").write_text(str(FRAMEWORK_ROOT / "plugins" / "core"),
                                               encoding="utf-8", newline="")
        for f in src_rules.glob("*.md"):
            (mirror / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8", newline="")
        (proj / "projects").mkdir()
        (proj / "projects" / "board.yaml").write_text(
            "summary:\n  total: 0\ntasks: []\n", encoding="utf-8", newline="")

        before = snapshot(proj / ".claude")
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        for group in ("install", "runtime"):
            try:
                subprocess.run([sys.executable, str(doctor), "--project", str(proj),
                                "--group", group],
                               capture_output=True, text=True, timeout=90,
                               encoding="utf-8", errors="replace", env=env)
            except Exception as e:
                return _fail("doctor_readonly_on_probe_project",
                             f"--group {group} 无法执行: {type(e).__name__}: {e}")
        after = snapshot(proj / ".claude")

        created = sorted(set(after) - set(before))
        removed = sorted(set(before) - set(after))
        changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
        if created or removed or changed:
            bits = []
            if created:
                bits.append("新建 " + "、".join(created[:4]))
            if changed:
                bits.append("改写 " + "、".join(changed[:4]))
            if removed:
                bits.append("删除 " + "、".join(removed[:4]))
            return _fail("doctor_readonly_on_probe_project",
                         "doctor 跑一遍后被检项目 .claude/ 发生变化（体检工具必须只读）："
                         + "；".join(bits))
    return _ok("doctor_readonly_on_probe_project", "doctor 两个 group 跑完不改被检项目 .claude/")


def check_doctor_all_checks_run_clean():
    """doctor 的每个检查都必须能在**最小合规项目**上跑完，不抛自身异常。

    为什么需要单独一闸：doctor 的 19 个检查各自包在 try/except 里（单个检查炸了不该
    带崩整份体检）。代价是**检查彻底坏掉时也只显示一行 `✗ 检查自身异常`**——退出码
    照常、其余检查照常绿，静态闸更是一点感觉都没有。2026-08-16 实证：改
    `check_rules_mirror` 时误删了一行 `extra = ...` 赋值，NameError 让该检查全程失效，
    而此时 validate 的 148 项全绿。体检工具自己坏了却没人报警，是最坏的一种坏法。

    做法：起一个最小合规项目（rules 镜像齐全 + plugin-root 指向真插件），实跑两个 group，
    断言输出里不含 `检查自身异常`。这条闸对全部 19 个检查同时生效——将来任何一个检查
    写出 NameError / TypeError / 拼错字段名，都会在这里当场暴露。
    """
    import os
    import subprocess
    import tempfile
    doctor = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    if not doctor.exists():
        return _fail("doctor_all_checks_run_clean", "workframe_doctor.py 不存在")
    src_rules = FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core"
    plugin_root = FRAMEWORK_ROOT / "plugins" / "core"

    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        state = proj / ".claude" / "workframe-state"
        mirror = proj / ".claude" / "rules" / "workframe" / "core"
        state.mkdir(parents=True)
        mirror.mkdir(parents=True)
        (state / "plugin-root.txt").write_text(str(plugin_root), encoding="utf-8", newline="")
        for f in src_rules.glob("*.md"):
            (mirror / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8", newline="")
        (proj / "projects").mkdir()
        (proj / "projects" / "board.yaml").write_text(
            "summary:\n  total: 0\n  pending: 0\n  in_progress: 0\n  pending_qa: 0\n"
            "  completed: 0\n  blocked: 0\n  cancelled: 0\n  last_updated: null\n\ntasks: []\n",
            encoding="utf-8", newline="")
        (state / "events.jsonl").write_text("", encoding="utf-8", newline="")

        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        bad = []
        for group in ("install", "runtime"):
            try:
                r = subprocess.run([sys.executable, str(doctor), "--project", str(proj),
                                    "--group", group],
                                   capture_output=True, text=True, timeout=90,
                                   encoding="utf-8", errors="replace", env=env)
            except Exception as e:
                return _fail("doctor_all_checks_run_clean",
                             f"--group {group} 无法执行: {type(e).__name__}: {e}")
            blob = (r.stdout or "") + (r.stderr or "")
            for line in blob.splitlines():
                if "检查自身异常" in line:
                    bad.append(f"[{group}] {line.strip()}")
            if "Traceback (most recent call last)" in blob:
                bad.append(f"[{group}] doctor 整体抛栈（不是单检查隔离）")
        if bad:
            return _fail("doctor_all_checks_run_clean",
                         "doctor 检查在最小合规项目上自身报错：\n    " + "\n    ".join(bad[:6]))
    return _ok("doctor_all_checks_run_clean", "doctor 19 项检查在最小合规项目上均无自身异常")


def check_audit_board_drift_bin_exists_and_compiles():
    """workframe-audit-board-drift bin 入口必须存在（POSIX + .cmd）、可编译、**且实跑通得过**。

    为什么必须实跑：语法编译抓不到**跨文件签名漂移**。2026-08-16 实证——
    `count_task_statuses()` 从 3 元组改成 4 元组时，`.py` 后缀的调用方都同步了，
    唯独这个 bin 入口漏了（它**没有 .py 扩展名**，`grep --include="*.py"` 直接把它过滤掉）。
    结果：文件语法完全合法、本闸报绿，而每次真调用都抛
    `ValueError: too many values to unpack`，`/core:audit` 的看板 drift 检查恒失败。
    bin/ 下全是无扩展名的 Python 脚本，是按后缀搜索的天然盲区——只能靠实跑兜住。
    """
    bin_dir = FRAMEWORK_ROOT / "plugins" / "core" / "bin"
    posix_entry = bin_dir / "workframe-audit-board-drift"
    cmd_entry = bin_dir / "workframe-audit-board-drift.cmd"
    missing = []
    if not posix_entry.exists():
        missing.append("workframe-audit-board-drift (POSIX entry)")
    if not cmd_entry.exists():
        missing.append("workframe-audit-board-drift.cmd (Windows wrapper)")
    if missing:
        return _fail("audit_board_drift_bin_exists_and_compiles", f"missing: {missing}")

    # 语法检查 POSIX 入口
    try:
        import py_compile
        py_compile.compile(str(posix_entry), doraise=True)
    except py_compile.PyCompileError as e:
        return _fail("audit_board_drift_bin_exists_and_compiles", f"POSIX entry compile failed: {e}")
    except Exception as e:
        return _fail("audit_board_drift_bin_exists_and_compiles", f"compile probe failed: {type(e).__name__}: {e}")

    # 实跑冒烟：喂一个含 drift 的临时 board，断言它真的算出了 drift 而不是崩在解包上
    import os as _os
    import subprocess
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / "projects").mkdir()
        (proj / "projects" / "board.yaml").write_text(
            "summary:\n  total: 9\n  pending: 9\n  in_progress: 0\n  pending_qa: 0\n"
            "  completed: 0\n  blocked: 0\n  cancelled: 0\n  last_updated: null\n\n"
            "tasks:\n  - id: T1\n    status: pending\n  - id: T2\n    status: completed\n",
            encoding="utf-8", newline="")
        env = dict(_os.environ, CLAUDE_PROJECT_DIR=str(proj), PYTHONUTF8="1")
        try:
            r = subprocess.run([sys.executable, str(posix_entry)], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=60, env=env)
        except Exception as e:
            return _fail("audit_board_drift_bin_exists_and_compiles",
                         f"smoke run failed to start: {type(e).__name__}: {e}")
        if r.returncode != 0:
            return _fail("audit_board_drift_bin_exists_and_compiles",
                         f"smoke run exit={r.returncode}: {(r.stderr or r.stdout).strip()[:160]}")
        out = r.stdout
        if "actual: total=2" not in out:
            return _fail("audit_board_drift_bin_exists_and_compiles",
                         f"smoke run 未算出正确 actual（期望 total=2）: {out.strip()[:160]}")
        if "drift:" not in out or "total=9->2" not in out:
            return _fail("audit_board_drift_bin_exists_and_compiles",
                         f"smoke run 未报出预期 drift: {out.strip()[:160]}")

    return _ok("audit_board_drift_bin_exists_and_compiles", "(含实跑冒烟)")


def check_task_blocked_producer_policy_consistent():
    """task_blocked producer 策略必须前后一致：主 producer = test-case-design，fallback = wrap-up（限非 QA/手动 + dedup）。

    禁止三类反指标：
    1. test-case-design SKILL.md 缺失 task_blocked append 样例（主 producer 失效）
    2. agent-protocols.md 仍写"唯一 producer"（与 fallback 路径矛盾）
    3. agent-protocols.md 缺失 fallback dedup 检查规则
    """
    skill_path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "test-case-design" / "SKILL.md"
    proto_path = FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core" / "agent-protocols.md"

    if not (skill_path.exists() and proto_path.exists()):
        return _fail("task_blocked_producer_policy_consistent", "files missing")

    skill_text = skill_path.read_text(encoding="utf-8")
    proto_text = proto_path.read_text(encoding="utf-8")

    if not re.search(r'"type"\s*:\s*"task_blocked"', skill_text):
        return _fail(
            "task_blocked_producer_policy_consistent",
            "test-case-design SKILL.md missing task_blocked event sample (primary producer must append)",
        )

    # 反指标：仍保留"唯一 producer"措辞（与 fallback 路径冲突）
    if "唯一 producer" in proto_text:
        return _fail(
            "task_blocked_producer_policy_consistent",
            "agent-protocols.md still uses '唯一 producer' wording — conflicts with fallback path; rewrite as 主 producer + fallback",
        )

    # 必须明确"主 producer = test-case-design"
    has_primary = ("主 producer" in proto_text and "test-case-design" in proto_text)
    if not has_primary:
        return _fail(
            "task_blocked_producer_policy_consistent",
            "agent-protocols.md must declare 主 producer = test-case-design",
        )

    # 必须明确 fallback dedup 检查
    has_fallback_dedup = ("fallback" in proto_text.lower()) and any(
        kw in proto_text for kw in ["dedup", "去重", "扫描", "尚无"]
    )
    if not has_fallback_dedup:
        return _fail(
            "task_blocked_producer_policy_consistent",
            "agent-protocols.md must define fallback producer + mandatory dedup check on events.jsonl",
        )

    return _ok("task_blocked_producer_policy_consistent")


def check_iteration_baseline_code_derived():
    """v0.4 G1#2：迭代基线改代码派生（derive, don't store）——
    check-iteration-trigger.py 必须含 derive_last_iteration_date()/count_completed_since()
    且不再引用 iteration-baseline；self-iteration / maintenance-review SKILL 不得残留
    手工写 baseline 的义务文本（防回滚到"模型记账"层——实测 3 个月零记账产生假警报）。"""
    issues = []
    script = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "check-iteration-trigger.py"
    if not script.exists():
        return _fail("iteration_baseline_code_derived", "check-iteration-trigger.py missing")
    stext = script.read_text(encoding="utf-8")
    if "def derive_last_iteration_date" not in stext:
        issues.append("script missing derive_last_iteration_date()")
    if "def count_completed_since" not in stext:
        issues.append("script missing count_completed_since()")
    for token in ("BASELINE_FILE", "iteration-baseline.json"):
        # 允许 docstring 里作为"已退役"历史说明出现，但不允许作为路径常量/读写目标
        if re.search(r"^\s*BASELINE_FILE", stext, re.M) and token == "BASELINE_FILE":
            issues.append("script still defines BASELINE_FILE constant")
    for rel in ("self-iteration", "maintenance-review"):
        p = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / rel / "SKILL.md"
        if not p.exists():
            issues.append(f"{rel}/SKILL.md missing")
            continue
        t = p.read_text(encoding="utf-8")
        if re.search(r"写入[^\n]{0,40}iteration-baseline\.json", t) or \
           re.search(r"更新[^\n]{0,20}iteration-baseline\.json", t):
            issues.append(f"{rel}/SKILL.md still instructs writing iteration-baseline.json")
    if issues:
        return _fail("iteration_baseline_code_derived", "; ".join(issues))
    return _ok("iteration_baseline_code_derived")


def check_doctor_smoke():
    """workframe-doctor 存在、可编译、`--list` 列出两组全部检查 + bin 包装齐全。

    检查 ID 从 doctor 源码的 CHECKS（runtime）/ INSTALL_CHECKS 解析后与 `--list` 输出对账，
    不在本文件另写一份——手写清单漏过两次（先漏 auto_memory，后漏 prd_framework
    与 init_completeness）。
    """
    script = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    if not script.exists():
        return _fail("doctor_smoke", "workframe_doctor.py missing")
    issues = []
    r = subprocess.run([sys.executable, "-m", "py_compile", str(script)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        issues.append(f"py_compile failed: {r.stderr[:120]}")
    r = subprocess.run([sys.executable, str(script), "--list"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60)
    if r.returncode != 0:
        issues.append(f"--list exit {r.returncode}")
    else:
        # ID 清单**从 doctor 源码解析**，不再手写一份：手写那份先漏了 auto_memory，
        # 又漏了 prd_framework 与 init_completeness（install 组实为 11 项而闸只列 9 项）。
        # 两处手写清单必漂，让机器去数。
        dtext = script.read_text(encoding="utf-8")
        declared = []
        for const in ("CHECKS", "INSTALL_CHECKS"):  # CHECKS = runtime 组
            mm = re.search(rf"^{const}\s*=\s*\[(.*?)^\]", dtext, re.M | re.S)
            if not mm:
                issues.append(f"找不到 {const} 定义（无法与 --list 对账）")
                continue
            declared += re.findall(r'^\s*\("([a-z_]+)"', mm.group(1), re.M)
        if not declared:
            issues.append("两组检查 ID 一个都没解析出来（doctor 结构变了，闸需同步）")
        for cid in declared:
            if f"[{cid}]" not in r.stdout:
                issues.append(f"--list missing check id {cid}")
        for g in ("--group install", "--group runtime"):
            if g not in r.stdout:
                issues.append(f"--list 未展示分组 {g}")
    for b in ("workframe-doctor", "workframe-doctor.cmd"):
        if not (FRAMEWORK_ROOT / "plugins" / "core" / "bin" / b).exists():
            issues.append(f"bin/{b} missing")
    if issues:
        return _fail("doctor_smoke", "; ".join(issues))
    return _ok("doctor_smoke", "(19 checks in 2 groups)")


def check_validate_self_no_duplicates():
    """validate.py 自身不得有重名的 check 函数或重复注册——**这条闸守的是闸本身**。

    曾经踩到：给 event registry 加检查时没先查是否已有同名的，结果
    `check_event_types_registered` 被定义两次、在 CHECKS 里注册两次。后果有三层——
    Python 后定义覆盖先定义（原来那份覆盖面更广的实现**被悄悄替换掉了**）、
    同一个检查跑两遍、总项数虚增一项（对外宣称的「N 项检查」跟着虚高）。
    全绿输出里没有任何迹象，是我在给新闸找注册锚点、发现锚点匹配两次时才撞见的。
    """
    name = "validate_self_no_duplicates"
    src = Path(__file__).resolve()
    text = src.read_text(encoding="utf-8")
    errors = []

    defs = re.findall(r"^def (check_[a-z0-9_]+)\(", text, re.M)
    dup_defs = sorted({d for d in defs if defs.count(d) > 1})
    if dup_defs:
        errors.append(f"重名的 check 函数定义: {dup_defs}（后定义会静默覆盖先定义）")

    m = re.search(r"^CHECKS\s*=\s*\[(.*?)^\]", text, re.M | re.S)
    if not m:
        errors.append("找不到 CHECKS 列表")
    else:
        regs = re.findall(r"^\s*(check_[a-z0-9_]+),", m.group(1), re.M)
        dup_regs = sorted({r for r in regs if regs.count(r) > 1})
        if dup_regs:
            errors.append(f"CHECKS 里重复注册: {dup_regs}（同一检查跑两遍且项数虚增）")
        undefined = sorted(set(regs) - set(defs))
        if undefined:
            errors.append(f"注册了但没有定义: {undefined}")
    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name, f"{len(defs)} 个 check 函数无重名、无重复注册")


def check_event_reliability_enum():
    """每个事件的 `reliability` 必须是 reliability_tiers 里的裸枚举值。

    这条闸的存在是因为同一个错误犯了**两次**：先给 memory_promoted 写
    `"mixed — hook_deterministic via …"`，后给 pending_maintenance_dismissed 写
    `"mostly hook_deterministic …"`。两次动机都是「想说清楚它有多条 producer 路径」，
    但这个字段是被 `workframe_doctor.ACTIVE_TIERS` 消费的固定枚举——写进自由文本，
    该事件就被**静默**移出 producer 对账与 append 样本检查，没有任何报错。

    多来源要表达就写进 `producer`（那是自由文本字段），`reliability` 取**最弱**的那一档。
    """
    name = "event_reliability_enum"
    schema_file = (FRAMEWORK_ROOT / "plugins" / "core" / ".workframe-meta"
                   / "event-schema.json")
    if not schema_file.exists():
        return _fail(name, "event-schema.json 缺失")
    try:
        data = json.loads(schema_file.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail(name, f"event-schema.json 解析失败: {e}")
    tiers = set((data.get("reliability_tiers") or {}).keys())
    if not tiers:
        return _fail(name, "reliability_tiers 未定义，无法校验枚举")
    events = data.get("events") or data
    offenders = []
    for ev_type, spec in events.items():
        if not isinstance(spec, dict) or "reliability" not in spec:
            continue
        if spec["reliability"] not in tiers:
            offenders.append(f"{ev_type} = {str(spec['reliability'])[:48]!r}")
    if offenders:
        return _fail(name, f"reliability 非枚举值（合法: {sorted(tiers)}）: "
                           + "; ".join(offenders))
    return _ok(name, f"{len(events)} 个事件的 reliability 均为合法枚举")


def check_scaffold_gitkeep_dirs_covered():
    """scaffold 建的每个治理空目录都得在 doctor 的骨架清单里有一档。

    scaffold 建 7 个 .gitkeep 治理目录（evals×3 / proposals×3 / archive），
    doctor 三档清单一个都没查——目录被误删后零信号。两处都是手写清单，靠这条闸对账。
    `logs/.gitkeep` 是**有意**不查的（整个 logs/ 被 gitignore，clone 后必然没有，
    列进来等于给每个克隆项目挂一条永远消不掉的 warn），故在此显式豁免。
    """
    name = "scaffold_gitkeep_dirs_covered"
    scaffold = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    doctor = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    if not scaffold.exists() or not doctor.exists():
        return _fail(name, "project_scaffold.py 或 workframe_doctor.py 缺失")
    stext = scaffold.read_text(encoding="utf-8")
    dtext = doctor.read_text(encoding="utf-8")
    m = re.search(r"proj_gitkeep_dirs\s*=\s*\[(.*?)\]", stext, re.S)
    if not m:
        return _fail(name, "找不到 proj_gitkeep_dirs 列表（scaffold 结构变了，闸需同步）")
    # proj_dir / "evals" / "rules"  →  projects/evals/rules/.gitkeep
    dirs = []
    for line in m.group(1).splitlines():
        parts = re.findall(r'"([^"]+)"', line)
        if parts:
            dirs.append("projects/" + "/".join(parts) + "/.gitkeep")
    if not dirs:
        return _fail(name, "proj_gitkeep_dirs 解析出 0 个目录")
    exempt = {"logs/.gitkeep"}
    missing = [d for d in dirs if d not in dtext and d not in exempt]
    if missing:
        return _fail(name, f"scaffold 建了但 doctor 三档清单都没查: {missing}")
    return _ok(name, f"{len(dirs)} 个治理目录均已被 doctor 清单覆盖")


def check_activity_defaults_match_template():
    """代码里的 activity-state 出厂值不得比模板多出字段——模板才是权威副本。

    两处曾经漂过——模板带 `__doc__` 与
    `__pending_maintenance_schema__` 两段契约说明（自述「随插件分发、保持可机器校验」），
    代码常量里没有。文件一旦损坏重建就用代码常量，那两段说明再也回不来。
    现在 `_state_io.factory_activity_defaults()` 以模板为准、常量兜底，这条闸守住
    「常量不许比模板多字段」（少字段是允许的：模板里的纯文档键不必进代码）。
    """
    name = "activity_defaults_match_template"
    scripts = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    tpl = (FRAMEWORK_ROOT / "plugins" / "core" / "templates"
           / "activity-state-template.json")
    module = scripts / "_state_io.py"
    if not tpl.exists() or not module.exists():
        return _fail(name, "activity-state-template.json 或 _state_io.py 缺失")
    try:
        tpl_keys = set(json.loads(tpl.read_text(encoding="utf-8")).keys())
    except Exception as e:
        return _fail(name, f"模板解析失败: {e}")
    mtext = module.read_text(encoding="utf-8")
    m = re.search(r"^ACTIVITY_DEFAULTS\s*=\s*\{(.*?)^\}", mtext, re.M | re.S)
    if not m:
        return _fail(name, "找不到 ACTIVITY_DEFAULTS 定义")
    code_keys = set(re.findall(r'^\s*"([^"]+)":', m.group(1), re.M))
    extra = code_keys - tpl_keys
    if extra:
        return _fail(name, f"代码常量比模板多出字段（模板未同步）: {sorted(extra)}")
    if "factory_activity_defaults" not in mtext or str(tpl.name) not in mtext:
        return _fail(name, "_state_io 未以模板为出厂值来源")
    return _ok(name, f"模板 {len(tpl_keys)} 键 ⊇ 代码 {len(code_keys)} 键，出厂值以模板为准")


def check_no_drifted_literals():
    """两组「同事实多文件」的字面扫描——按字面追，不按发现编号追。

    这两条口径各自漂了 3-4 轮：每轮都在勾选「某编号已修」，实际只改了其中一两处。
    这类「同一事实散在多文件」的口径，只能用字面扫描当闸——按发现编号勾选必漏。

    1. 「落盘验收查不出来」——doctor 的 claude_md 早已查四个契约段（缺失=error），
       该断言在面向用户的文档里是撒谎。唯一豁免：doctor 自身解释「为什么要查段而不是
       查文件存在」的动机句。
    2. 「首尾空格」——module_init 入口拒绝**任何**空格（含中间），规范却说首尾。
       唯一豁免：解释「首尾/中间各自为什么不行」的教义行（含「中间空格」字样）。
    """
    name = "no_drifted_literals"
    scan_dirs = [FRAMEWORK_ROOT / "plugins", FRAMEWORK_ROOT / "docs"]
    rules = [
        ("查不出来",
         lambda p, ln: p.name == "workframe_doctor.py" and "只查「文件存在」" in ln,
         "doctor 已查 CLAUDE.md 四契约段，「验收查不出来」已失真"),
        ("首尾空格|首尾不带空格|首尾不空格",
         lambda p, ln: "中间空格" in ln,
         "module_init 拒绝任何空格（含中间），规范不应只写「首尾」"),
    ]
    offenders = []
    for pattern, is_exempt, why in rules:
        rx = re.compile(pattern)
        for d in scan_dirs:
            if not d.is_dir():
                continue
            for p in d.rglob("*"):
                if p.suffix not in (".md", ".py", ".yaml", ".json") or not p.is_file():
                    continue
                try:
                    lines = p.read_text(encoding="utf-8").splitlines()
                except Exception:
                    continue
                for i, ln in enumerate(lines, 1):
                    if rx.search(ln) and not is_exempt(p, ln):
                        rel = p.relative_to(FRAMEWORK_ROOT)
                        offenders.append(f"{rel}:{i}（{why}）")
    if offenders:
        return _fail(name, "; ".join(offenders[:6])
                     + (f" …… 共 {len(offenders)} 处" if len(offenders) > 6 else ""))
    return _ok(name, f"{len(rules)} 组已漂字面全仓零残留")


def check_state_io_concurrency():
    """_state_io 的同键并发**行为**测试：N 个进程各把 counter +1，最终必须等于 N。

    本仓第一个行为测试——其余检查全是静态的文本/结构比对，而这类缺陷只有真跑多进程
    才暴露：三方合并只保住「不同键」，同键并发仍后写覆盖（曾实测 2 个进程各 +1 得 1）。

    覆盖 `update_activity`（锁内读→改→写）。

    **本闸跑不动时报红，不降级为跳过。** 它是全仓唯一真正执行 POSIX `fcntl` 分支的
    检查（Windows 走 `msvcrt`），CI 的 macOS / Linux runner 靠它验证对侧分支。
    一道「健康时报绿、生病时也报绿」的闸，在最需要它的平台上最可能自动让路——
    而那正是本仓 macOS 侧唯一的自动化防线。宁可因环境问题误报，也不要假绿。
    """
    name = "state_io_concurrency"
    scripts = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    if not (scripts / "_state_io.py").exists():
        return _fail(name, "_state_io.py 缺失")
    n = 4
    worker = (
        "import sys,time;"
        "sys.path.insert(0, r'{sc}');"
        "from _state_io import update_activity;"
        "update_activity(r'{sd}', lambda s: (time.sleep(0.25), "
        "s.__setitem__('session_counter', int(s.get('session_counter',0))+1)))"
    )
    tmp = tempfile.mkdtemp(prefix="wf-conc-")
    try:
        sd = Path(tmp) / ".claude" / "workframe-state"
        sd.mkdir(parents=True, exist_ok=True)
        code = worker.format(sc=str(scripts), sd=str(sd))
        procs = [subprocess.Popen([sys.executable, "-c", code],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                 for _ in range(n)]
        for p in procs:
            try:
                p.wait(timeout=60)
            except Exception:
                p.kill()
                return _fail(name, "并发子进程 60s 未结束——本闸不降级：见 docstring")
        state_file = sd / "activity-state.json"
        if not state_file.exists():
            return _fail(name, "并发写后 activity-state.json 不存在")
        got = json.loads(state_file.read_text(encoding="utf-8")).get("session_counter")
        if got != n:
            return _fail(name, f"{n} 个进程各 +1，counter 实得 {got}——同键并发仍在丢更新")
        return _ok(name, f"{n} 进程并发累加收敛（counter={got}）")
    except Exception as e:
        return _fail(name, f"并发测试无法执行（{type(e).__name__}: {e}）——本闸不降级：见 docstring")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_state_io_fail_closed():
    """状态文件读不出来时，写路径必须 fail-closed —— **行为测试**。

    实测：把 activity-state.json 换成目录后 update_activity 抛未捕获的
    PermissionError，`session-start-prep` 直接 exit 1（SessionStart hook 被掀翻）；
    而「读失败返回默认值 + 照常 atomic_write」还会拿空状态覆盖掉一份读不动但可能完好的
    文件。两条都由本测试守：不抛异常、不覆盖、返回失败信号。

    **跑不动时报红，不降级为跳过**——理由同 `check_state_io_concurrency`：行为测试
    是本仓仅有的两道「真跑」检查，它们一旦学会在环境不顺时自动放行，剩下的 157 项
    静态比对没有一项能替它们发现问题。
    """
    name = "state_io_fail_closed"
    scripts = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    if not (scripts / "_state_io.py").exists():
        return _fail(name, "_state_io.py 缺失")
    probe = (
        "import sys, json, pathlib;"
        "sys.path.insert(0, r'{sc}');"
        "from _state_io import update_activity, save_activity;"
        "sd = pathlib.Path(r'{sd}');"
        "update_activity(str(sd), lambda s: s.update("
        "{{'session_counter': 42, 'future_unknown_key': 'keep-me'}}));"
        "before = json.loads((sd / 'activity-state.json').read_text(encoding='utf-8'))"
        "['session_counter'];"
        # 新进程视角：清掉本进程快照再 save，模拟「另一个 hook 起来直接写」
        "import _state_io; _state_io._LOAD_SNAPSHOTS.clear();"
        "save_activity(str(sd), {{'session_counter': 43}});"
        "kept = 'future_unknown_key' in json.loads("
        "(sd / 'activity-state.json').read_text(encoding='utf-8'));"
        "p = sd / 'activity-state.json';"
        "p.unlink(); p.mkdir();"                      # 变成目录 = 读不出来也写不进
        "r1 = update_activity(str(sd), lambda s: s.__setitem__('session_counter', 99));"
        "r2 = save_activity(str(sd), {{'session_counter': 99}});"
        "print(json.dumps({{'before': before, 'kept': kept, 'r1': r1, 'r2': r2}}))"
    )
    tmp = tempfile.mkdtemp(prefix="wf-failclosed-")
    try:
        sd = Path(tmp) / ".claude" / "workframe-state"
        sd.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([sys.executable, "-c", probe.format(sc=str(scripts), sd=str(sd))],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        if r.returncode != 0:
            return _fail(name, f"读/写失败时抛出未捕获异常（子进程 exit {r.returncode}）: "
                               f"{r.stderr.strip().splitlines()[-1][:120] if r.stderr.strip() else ''}")
        payload = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
        if not payload:
            return _fail(name, "fail-closed 探针无输出——本闸不降级：见 docstring")
        got = json.loads(payload[-1])
        if got.get("before") != 42:
            return _fail(name, f"前置写入未生效（counter={got.get('before')}）")
        if got.get("kept") is not True:
            return _fail(name, "无 load 快照时 save_activity 抹掉了磁盘上的未知键"
                               "（本模块承诺未知键原样保留）")
        if got.get("r1") is not None:
            return _fail(name, f"update_activity 在读不出来时应返回 None，实得 {got['r1']!r}")
        if got.get("r2") is not False:
            return _fail(name, f"save_activity 在读不出来时应返回 False，实得 {got['r2']!r}")
        return _ok(name, "读/写失败不抛异常、不覆盖、返回失败信号；无快照 save 保留未知键")
    except Exception as e:
        return _fail(name, f"fail-closed 测试无法执行（{type(e).__name__}: {e}）——本闸不降级：见 docstring")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_state_io_single_source():
    """activity-state 的读写只能有一份实现——四个 hook 必须走 `_state_io`。

    四个 hook 各写各的 load/save，损坏降级出现四种不同结局
    （退出 / 恢复三字段 / 恢复默认并裁掉未知键 / 返回空字典），写入侧则是四份非原子的
    write_text。裁剪那份还造成了实际损失：模板自带的 `__doc__` 与
    `__pending_maintenance_schema__` 两个契约键，在首个会话就被静默删掉——而它们的自述
    正是「随插件分发、保持可机器校验」。

    这条闸守的是「别再各写各的」：hook 里不得出现直接读写 activity-state 的字面操作。
    """
    name = "state_io_single_source"
    scripts = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    module = scripts / "_state_io.py"
    if not module.exists():
        return _fail(name, "_state_io.py 缺失——四个 hook 的状态读写都依赖它")
    # 五个写入方——maintenance_workorder 是第五个，曾不在名单里，
    # 于是它一直直接 write_text 整份覆盖 activity-state 而闸报绿。
    consumers = ["session-start-prep.py", "check-iteration-trigger.py",
                 "user-prompt-inject.py", "session-end-flush.py",
                 "maintenance_workorder.py"]
    errors = []
    for fname in consumers:
        p = scripts / fname
        if not p.exists():
            errors.append(f"{fname} 缺失")
            continue
        text = p.read_text(encoding="utf-8")
        if "from _state_io import" not in text:
            errors.append(f"{fname} 没有从 _state_io 导入（可能又自己实现了一份）")
        # 直接对 activity-state 文件做读写 = 绕过统一实现
        if re.search(r"ACTIVITY_FILE\.(write_text|read_text)", text):
            errors.append(f"{fname} 直接读写 ACTIVITY_FILE，未走 _state_io")
    # 锁与原子写的出处也必须复用，不能留两份
    stale = scripts / "check-stale-modules.py"
    if stale.exists():
        stext = stale.read_text(encoding="utf-8")
        if "from _state_io import" not in stext:
            errors.append("check-stale-modules.py 未复用 _state_io 的 FileLock / atomic_write")
        if re.search(r"^class FileLock", stext, re.M):
            errors.append("check-stale-modules.py 仍留有 FileLock 的第二份实现")
    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name, f"_state_io 为唯一实现，{len(consumers)} 个 hook + check-stale 均复用")


def check_setup_state_steps_wired():
    """setup-state 的每一步都得有人真的会去落笔——步骤清单与记账指引是两处手写事实源。

    doctor 把 scaffold/subscribe/sync_rules/acceptance 四步列为必查，
    launcher SKILL.md 却只给了 subscribe 一个可照抄的 mark_setup_step 示例——另两步只在
    「必记的四步」那句枚举里各出现一次、没有命令。结果是每个装机项目跑
    `doctor --group install` 都报「初始化未走完」，且没人知道怎么补记，重跑也消不掉。
    只验「step 名在 SKILL.md 里出现过」抓不住这个——当时它确实出现过。所以这条闸验的是
    **有没有可照抄的 mark_setup_step(..., '<step>') 调用**。

    现行分工：scaffold 由 project_scaffold 自动落；SETUP_SELF_RECORDED_STEPS（acceptance）
    由 doctor 自己落；其余每一步必须在 launcher SKILL.md 里有字面示例。
    """
    name = "setup_state_steps_wired"
    doctor = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    scaffold = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    skill = (FRAMEWORK_ROOT / "plugins" / "workframe-launcher" / "skills"
             / "setup" / "SKILL.md")
    for p in (doctor, scaffold, skill):
        if not p.exists():
            return _fail(name, f"{p.name} 缺失")
    dtext = doctor.read_text(encoding="utf-8")
    stext = scaffold.read_text(encoding="utf-8")
    ktext = skill.read_text(encoding="utf-8")

    def _steps(const):
        m = re.search(rf"^{const}\s*=\s*\((.*?)\)", dtext, re.M | re.S)
        return re.findall(r'"([^"]+)"', m.group(1)) if m else []

    def _has_call(text, step):
        # 不能用 [^)]* —— 实参里的 Path(r'<目标>') 自带一个右括号会把匹配截断
        return re.search(rf"mark_setup_step\([^\n]*['\"]{re.escape(step)}['\"]", text)

    required = _steps("SETUP_REQUIRED_STEPS")
    self_recorded = _steps("SETUP_SELF_RECORDED_STEPS")
    if not required:
        return _fail(name, "找不到 SETUP_REQUIRED_STEPS 定义（无法与记账指引对账）")

    errors = []
    for step in self_recorded:
        if not _has_call(dtext, step):
            errors.append(f"{step} 声明为 doctor 自记步，但 doctor 里没有对应的 "
                          f"mark_setup_step(..., '{step}') 调用")
        if step in required:
            errors.append(f"{step} 同时在 SETUP_REQUIRED_STEPS 与 SETUP_SELF_RECORDED_STEPS 里"
                          "——自记步不能参与必查判定，否则验收自指")
    for step in required:
        if _has_call(stext, step) or _has_call(dtext, step):
            continue  # 由脚本自动落笔
        if not _has_call(ktext, step):
            errors.append(f"{step} 是必查步，但没人落笔：launcher SKILL.md 里找不到可照抄的 "
                          f"mark_setup_step(..., '{step}')")
    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name, f"必查 {len(required)} 步 + 自记 {len(self_recorded)} 步均有落笔人")


def check_doctor_install_group_contract():
    """第 0 组的三条契约：CLI 参数、可 import 复用、被首个会话消费。

    三者任一断掉，落盘验收或运行时验收就会失效——而失效方式都是「什么都不报」，
    人不可能自己发现。这条闸守的是「验收机制本身还在不在」。
    """
    name = "doctor_install_group_contract"
    doctor = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    prep = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "session-start-prep.py"
    if not doctor.exists() or not prep.exists():
        return _fail(name, "workframe_doctor.py 或 session-start-prep.py 缺失")
    dtext, ptext = doctor.read_text(encoding="utf-8"), prep.read_text(encoding="utf-8")
    errors = []
    # 1. CLI surface —— launcher 的落盘验收命令直接依赖这两个参数
    for token in ('"--project"', '"--group"'):
        if token not in dtext:
            errors.append(f"doctor 缺 CLI 参数 {token}")
    if '"install"' not in dtext or "GROUPS" not in dtext:
        errors.append("doctor 缺 install 分组定义")
    # 2. 可 import 复用 —— 目标目录必须是参数，不能是 import 时求值的全局
    if "def run_all(project_dir" not in dtext:
        errors.append("run_all 未接收 project_dir 参数（无法对指定项目跑）")
    if re.search(r"^PROJECT_DIR\s*=", dtext, re.M):
        errors.append("doctor 仍有模块级 PROJECT_DIR（import 时求值，--project 会失效）")
    # import 无副作用：stdout 包装不得在模块级执行，否则调用方双重包装当场崩
    if re.search(r"^sys\.stdout\s*=", dtext, re.M):
        errors.append("doctor 在模块级改 sys.stdout（调用方 import 会撞 closed file）")
    # 3. 首个会话真的消费它
    if "from workframe_doctor import" not in ptext:
        errors.append("session-start-prep 未 import doctor（运行时验收缺失）")
    # 4. SKILL 里报给用户的项数必须与 INSTALL_CHECKS 实际条目数一致
    #    F2 实证：新增 claude_md 检查时没同步 SKILL 的「8 项覆盖」，模型照抄文档少报了一项——
    #    而漏报的恰是 B 路径最该被验的那个。两处事实源不对账就一定会漂。
    m_decl = re.search(r"^INSTALL_CHECKS\s*=\s*\[(.*?)^\]", dtext, re.M | re.S)
    if not m_decl:
        errors.append("找不到 INSTALL_CHECKS 列表定义（无法与 SKILL 项数对账）")
    else:
        # 条目形如 ("skeleton", "骨架完整性", "说明", check_skeleton),
        actual = len([
            ln for ln in m_decl.group(1).splitlines()
            if ln.strip().startswith('("') and "check_" in ln
        ])
        skill = LAUNCHER_DIR / "skills" / "setup" / "SKILL.md"
        if not skill.is_file():
            errors.append("找不到 launcher setup SKILL.md（无法对账项数）")
        else:
            m_skill = re.search(r"(\d+)\s*项覆盖", skill.read_text(encoding="utf-8"))
            if not m_skill:
                errors.append("SKILL.md 未声明「N 项覆盖」（用户看到的项数无据可查）")
            elif int(m_skill.group(1)) != actual:
                errors.append(
                    f"SKILL.md 写「{m_skill.group(1)} 项覆盖」但 INSTALL_CHECKS 实为 {actual} 项"
                )
    if 'session_counter"] == 1' not in ptext:
        errors.append("session-start-prep 未按 session_counter == 1 触发运行时验收")
    # 验收结论必须要求模型转述——hook stdout 只进模型上下文，用户在终端看不到。
    # 实测（走查 R5）：用户重启后面对空白屏，以为 hook 没跑，实际全部正常。
    if "请在本轮回复的开头" not in ptext:
        errors.append("运行时验收未要求模型转述给用户（用户在终端看不到 hook 输出）")
    # 4. 骨架分档不能把 gitignore 掉的东西列成硬需求
    #    实测（走查 R4）：workframe-state 四个文件曾列在硬骨架里，而该目录整个 gitignore——
    #    「同事 clone 已接入项目」这个主场景**必然报 error**，而那恰恰是设计要求的行为。
    if "SCAFFOLD_RUNTIME_FILES" not in dtext:
        errors.append("doctor 缺 SCAFFOLD_RUNTIME_FILES 档（gitignore 的运行时文件不能算硬骨架）")
    #    两处都只看引号里的**实际条目**：拿整块做子串匹配会把「为什么不列它」这类
    #    说明性注释当成条目误报（给软骨架补治理目录时踩到）。
    def _list_items(const):
        mm = re.search(rf"{const}\s*=\s*\[(.*?)\]", dtext, re.S)
        return re.findall(r'"([^"]+)"', mm.group(1)) if mm else []

    if any("workframe-state" in i for i in _list_items("SCAFFOLD_REQUIRED_FILES")):
        errors.append("workframe-state 被列进硬骨架——该目录整个 gitignore，clone 出来必然缺")
    if any(i.startswith("logs/") for i in _list_items("SCAFFOLD_OPTIONAL_FILES")):
        errors.append("logs/ 被列进软骨架——该目录整个 gitignore，clone 后永远缺，会挂永久 warn")
    # 5. 「什么都没验却报绿」是最坏的一种绿灯——本轮已在 validate 与 doctor 各抓到一次。
    #    portability 扫 0 个文件时必须降 info；protected_assets 必须排除根提交
    #    （项目诞生那一刻所有文件都是新增，不是「改了受保护资产没走审批」）。
    if "scanned == 0" not in dtext:
        errors.append("portability 未处理「扫了 0 个文件」——那会宣称验过其实什么都没验")
    if "--max-parents=0" not in dtext:
        errors.append("protected_assets 未排除根提交——每个新项目一装完就被误报无审批痕迹")
    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name)


def check_doctor_thresholds_match_trigger():
    """doctor 与 check-iteration-trigger 的 notes 积压判据常量必须一致（同判据两处定义，防漂移）。"""
    def _extract(path):
        text = path.read_text(encoding="utf-8")
        out = {}
        for name in ("NOTES_BACKLOG_LINES", "NOTES_STALE_DAYS"):
            m = re.search(r"^" + name + r"\s*=\s*(\d+)", text, re.M)
            out[name] = m.group(1) if m else None
        return out
    doctor = _extract(FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py")
    trigger = _extract(FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "check-iteration-trigger.py")
    issues = [f"{k}: doctor={doctor[k]} trigger={trigger[k]}"
              for k in doctor if doctor[k] is None or doctor[k] != trigger[k]]
    if issues:
        return _fail("doctor_thresholds_match_trigger", "; ".join(issues))
    return _ok("doctor_thresholds_match_trigger")


def check_memory_ask_smoke():
    """v0.4 G2#7：memory-ask.py 存在、可编译、频控拍板值锁定、hooks.json SessionStart 已接线。

    频控参数为 2026-08-06 用户拍板「更克制」：积压 ≥5 条 / 每日 ≤1 次 / 拒绝冷却 5 会话。
    锁定取值防止后续改动无痕漂移；调整参数须同步改此检查（即显式过一次审）。
    """
    name = "memory_ask_smoke"
    script = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "memory-ask.py"
    if not script.exists():
        return _fail(name, "memory-ask.py missing")
    issues = []
    r = subprocess.run([sys.executable, "-m", "py_compile", str(script)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        issues.append(f"py_compile failed: {r.stderr[:120]}")
    text = script.read_text(encoding="utf-8")
    for pat, what in ((r"^ASK_BACKLOG_MIN_ENTRIES\s*=\s*5\b", "ASK_BACKLOG_MIN_ENTRIES=5"),
                      (r"^ASK_REFUSAL_COOLDOWN_SESSIONS\s*=\s*5\b", "ASK_REFUSAL_COOLDOWN_SESSIONS=5")):
        if not re.search(pat, text, re.M):
            issues.append(f"频控拍板值缺失或被改: {what}")
    for token in ("initialUserMessage", "--record-refusal", "last_asked_date",
                  "maintenance-run.flag", "promotion-candidates.md"):
        if token not in text:
            issues.append(f"missing token: {token}")
    hooks_text = json.dumps(json.loads(
        (FRAMEWORK_ROOT / "plugins" / "core" / "hooks" / "hooks.json").read_text(encoding="utf-8")
    ).get("hooks", {}).get("SessionStart", []))
    if "memory-ask.py" not in hooks_text:
        issues.append("hooks.json SessionStart 未接线 memory-ask.py")
    if issues:
        return _fail(name, "; ".join(issues))
    return _ok(name)


def check_maintenance_workorder_smoke():
    """v0.4 G2#8：工单聚合器 + Setup(matcher=maintenance) 接线 + bin/workframe-maintenance 包装齐全。

    实测依据（2026-08-06）：--maintenance 仅 print 模式；Setup hook 输出不进上下文 →
    工单必须落文件；-p 无法批准权限 → 包装器须带 --permission-mode acceptEdits。
    """
    name = "maintenance_workorder_smoke"
    script = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "maintenance_workorder.py"
    if not script.exists():
        return _fail(name, "maintenance_workorder.py missing")
    issues = []
    r = subprocess.run([sys.executable, "-m", "py_compile", str(script)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        issues.append(f"py_compile failed: {r.stderr[:120]}")
    text = script.read_text(encoding="utf-8")
    for token in ("maintenance-workorder.md", "maintenance-run.flag", "workframe_doctor",
                  "promotion-candidates", "pending_maintenance",
                  # -p 下 Skill 工具不注入正文（2026-08-06 实测）：工单必须指挥模型 Read librarian SKILL.md
                  "librarian", "notes-archive.md",
                  # 两阶段提交：headless 写不了 .claude/workframe-state（敏感闸实测）→ 记账走代码
                  "--commit", "maintenance-commit.json", "l2_candidates"):
        if token not in text:
            issues.append(f"missing token: {token}")
    setup = json.loads(
        (FRAMEWORK_ROOT / "plugins" / "core" / "hooks" / "hooks.json").read_text(encoding="utf-8")
    ).get("hooks", {}).get("Setup", [])
    wired = any(g.get("matcher") == "maintenance"
                and any("maintenance_workorder.py" in h.get("command", "") for h in g.get("hooks", []))
                for g in setup if isinstance(g, dict))
    if not wired:
        issues.append("hooks.json 缺 Setup(matcher=maintenance) → maintenance_workorder.py 接线")
    wrapper = FRAMEWORK_ROOT / "plugins" / "core" / "bin" / "workframe-maintenance"
    if not wrapper.exists():
        issues.append("bin/workframe-maintenance missing")
    else:
        wtext = wrapper.read_text(encoding="utf-8")
        for token in ("--maintenance", "-p", "acceptEdits", "maintenance-workorder.md",
                      # 两阶段提交（2026-08-06 实测定型）：--add-dir 授 plugin 读权限；会话后 --commit 代码记账
                      "--add-dir", "--commit"):
            if token not in wtext:
                issues.append(f"wrapper missing token: {token}")
    if not (FRAMEWORK_ROOT / "plugins" / "core" / "bin" / "workframe-maintenance.cmd").exists():
        issues.append("bin/workframe-maintenance.cmd missing")
    if issues:
        return _fail(name, "; ".join(issues))
    return _ok(name)


def check_write_time_audn_present():
    """v0.4 G2#9：correction-detection / auto-update 写入路径必须含写时 A.U.N. 工序（防回退到直接 append）。"""
    name = "write_time_audn_present"
    rules_dir = FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core"
    issues = []
    cd = (rules_dir / "correction-detection.md").read_text(encoding="utf-8")
    for token in ("写前先查同主题", "supersede", "不重复写入"):
        if token not in cd:
            issues.append(f"correction-detection 缺: {token}")
    au = (rules_dir / "auto-update.md").read_text(encoding="utf-8")
    for token in ("A.U.N. 工序", "检索同主题条目", "同主题合并、异主题追加"):
        if token not in au:
            issues.append(f"auto-update 缺: {token}")
    lib = (FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "librarian" / "SKILL.md").read_text(encoding="utf-8")
    if "写时" not in lib or "兜底复查" not in lib:
        issues.append("librarian 融合 SOP 缺写时前置注记")
    if issues:
        return _fail(name, "; ".join(issues))
    return _ok(name)


def check_librarian_placement_has_skill_row():
    """v0.4 G2#11：librarian 落点表必须含项目 skill 行 + 三问判据（防回退到 rules/MEMORY 二元落点）。"""
    name = "librarian_placement_has_skill_row"
    text = (FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "librarian" / "SKILL.md").read_text(encoding="utf-8")
    issues = []
    for token in ("**项目 skill**（`.claude/skills/<name>/`）", "三问定落点",
                  "场景触发且成套", "聚类新建", "泄压阀"):
        if token not in text:
            issues.append(f"缺: {token}")
    if issues:
        return _fail(name, "; ".join(issues))
    return _ok(name)


def check_librarian_placement_has_automemory_row():
    """v0.4 G3#13：librarian 落点表必须含 auto-memory 行 + 三问第 3 问的消费者分流。

    两套记忆分工契约（auto-memory=主 Claude 层 / role=单角色 / shared=跨角色）依赖 librarian
    识别主 Claude 层条目并建议归 auto-memory（librarian 不代写）；缺行会导致用户偏好类内容
    被误提升进 role/shared。
    """
    name = "librarian_placement_has_automemory_row"
    text = (FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "librarian" / "SKILL.md").read_text(encoding="utf-8")
    issues = []
    for token in ("**auto-memory**", "librarian 不写", "先判**谁消费**", "librarian 不代写"):
        if token not in text:
            issues.append(f"缺: {token}")
    if issues:
        return _fail(name, "; ".join(issues))
    return _ok(name)


def check_notes_entry_count_sync():
    """memory-ask.py 与 maintenance_workorder.py 的 count_notes_entries 必须逐字一致（同判据两处定义，防漂移）。"""
    name = "notes_entry_count_sync"

    def _extract(path):
        text = path.read_text(encoding="utf-8")
        m = re.search(r"^def count_notes_entries\(.*?(?=^def |\Z)", text, re.M | re.S)
        return m.group(0).strip() if m else None

    scripts_dir = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    a = _extract(scripts_dir / "memory-ask.py")
    b = _extract(scripts_dir / "maintenance_workorder.py")
    if a is None or b is None:
        return _fail(name, "count_notes_entries 函数缺失（memory-ask 或 maintenance_workorder）")
    if a != b:
        return _fail(name, "两处 count_notes_entries 函数体不一致")
    return _ok(name)


def check_rollback_supports_v2_targets_array():
    """rollback/SKILL.md 必须支持 v2 entry 格式（targets[]/backups[]）且兼容 legacy single target。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "rollback" / "SKILL.md"
    if not path.exists():
        return _fail("rollback_supports_v2_targets_array", "SKILL.md missing")
    text = path.read_text(encoding="utf-8")
    issues = []
    if "targets" not in text or "backups" not in text:
        issues.append("missing v2 targets[]/backups[] support")
    if "legacy" not in text.lower() and "回退" not in text and "兼容" not in text:
        issues.append("missing legacy single-target compatibility note")
    if issues:
        return _fail("rollback_supports_v2_targets_array", "; ".join(issues))
    return _ok("rollback_supports_v2_targets_array")


def check_response_output_confirmation_rules_not_conflicting():
    """response-output.md 必须明确区分"补充事实/参数（视为确认）"和"否定方向/改写结构（需二次确认）"。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core" / "response-output.md"
    if not path.exists():
        return _fail("response_output_confirmation_rules_not_conflicting", "rule file missing")
    text = path.read_text(encoding="utf-8")

    issues = []
    # 必须含"补充事实"或"补充参数"侧的明确说明
    if not any(k in text for k in ["补充事实", "补充参数", "未否定当前草稿"]):
        issues.append("missing 补充事实/参数 path explanation")
    # 必须含"改写结构 / 否定方向 / 重做"任一关键词
    if not any(k in text for k in ["否定方向", "改写结构", "改写", "重做"]):
        issues.append("missing 否定方向/改写结构 path explanation")
    # 必须含 fallback / 退化 关键词（AskUserQuestion 不可用时的降级）
    if not any(k in text for k in ["fallback", "Fallback", "退化", "降级"]):
        issues.append("missing AskUserQuestion fallback rule")
    # 必须含"歧义"或"二次确认"语义保护
    if "二次确认" not in text:
        issues.append("missing 二次确认 boundary phrase")

    if issues:
        return _fail("response_output_confirmation_rules_not_conflicting", "; ".join(issues))
    return _ok("response_output_confirmation_rules_not_conflicting")


def check_auto_update_p0_example_confirm_before_write():
    """auto-update.md 示例必须先回显摘要+等确认，再写入；不允许"先写后确认"的旧序列。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core" / "auto-update.md"
    if not path.exists():
        return _fail("auto_update_p0_example_confirm_before_write", "rule file missing")
    text = path.read_text(encoding="utf-8")

    # 找示例段
    idx = text.find("## 示例")
    if idx < 0:
        return _fail("auto_update_p0_example_confirm_before_write", "no 示例 section")
    section = text[idx:]

    # 必须含"回显摘要 + 等待确认"
    has_confirm_first = any(k in section for k in ["回显摘要", "等待确认", "先于写入"])
    if not has_confirm_first:
        return _fail("auto_update_p0_example_confirm_before_write", "示例 must show 回显摘要+等待确认 before write")

    # 反指标：步骤序号 3 不能是"创建/更新 spec...追加任务"，4 才是"确认"
    if re.search(r"3\.\s*更新.*?spec.*?追加任务.*?4\.\s*确认", section, re.DOTALL):
        return _fail(
            "auto_update_p0_example_confirm_before_write",
            "示例 still has 'write before confirm' anti-pattern (step 3 writes, step 4 confirms)",
        )
    return _ok("auto_update_p0_example_confirm_before_write")


def check_auto_update_no_prompt_eng_skill_edit_claim():
    """auto-update.md 不应再有"由 @prompt-eng 评估后执行 skill 文件修改"或类似越权语句。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core" / "auto-update.md"
    if not path.exists():
        return _fail("auto_update_no_prompt_eng_skill_edit_claim", "rule file missing")
    text = path.read_text(encoding="utf-8")

    forbidden_patterns = [
        "由 @prompt-eng 评估后执行 skill 文件修改",
        "@prompt-eng 评估后执行 skill",
        "@prompt-eng 直接修改 skill",
    ]
    for phrase in forbidden_patterns:
        if phrase in text:
            return _fail(
                "auto_update_no_prompt_eng_skill_edit_claim",
                f"forbidden phrase reappeared: {phrase!r}",
            )
    # 必须含正确版本：评估和修改建议 / self-iteration L2
    has_correct_boundary = (
        "评估" in text
        and ("修改建议" in text or "self-iteration" in text or "/core:self-iteration" in text)
    )
    if not has_correct_boundary:
        return _fail(
            "auto_update_no_prompt_eng_skill_edit_claim",
            "missing prompt-eng correct boundary statement (评估/修改建议 + self-iteration path)",
        )
    return _ok("auto_update_no_prompt_eng_skill_edit_claim")


def check_task_blocked_producer_schema_matches_protocol():
    """event-schema.json 的 task_blocked.producer 必须使用主+fallback 策略表述：
    - 含 test-case-design（主 producer）
    - 含 fallback / dedup 表述（fallback 路径 + 强制去重）
    - 拒绝旧 'qa agent wrap-up' 单一口径
    - 拒绝 'sole producer = test-case-design' 单口径（与 fallback 路径冲突）
    """
    schema_path = FRAMEWORK_ROOT / "plugins" / "core" / ".workframe-meta" / "event-schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail("task_blocked_producer_schema_matches_protocol", f"schema read: {e}")

    spec = schema.get("events", {}).get("task_blocked", {})
    producer = spec.get("producer", "")

    if "test-case-design" not in producer:
        return _fail(
            "task_blocked_producer_schema_matches_protocol",
            f"task_blocked.producer must mention test-case-design as primary producer; got {producer!r}",
        )

    # 反指标 1：旧的 qa wrap-up 单一口径
    if producer.strip() == "qa agent wrap-up when marking task status=blocked":
        return _fail(
            "task_blocked_producer_schema_matches_protocol",
            "task_blocked.producer reverted to legacy single qa wrap-up wording",
        )

    # 反指标 2：'sole producer = test-case-design' 与 fallback 冲突
    if re.search(r"sole producer\s*=?\s*test-case-design", producer, re.IGNORECASE):
        return _fail(
            "task_blocked_producer_schema_matches_protocol",
            "task_blocked.producer wording 'sole producer = test-case-design' conflicts with fallback path; use 'Primary producer = ... Fallback producer = ...' instead",
        )

    # 必须含 fallback + dedup 表述
    has_fallback = "fallback" in producer.lower()
    has_dedup = ("dedup" in producer.lower()) or ("去重" in producer)
    if not (has_fallback and has_dedup):
        return _fail(
            "task_blocked_producer_schema_matches_protocol",
            "task_blocked.producer must describe fallback path AND mandatory dedup check",
        )

    return _ok("task_blocked_producer_schema_matches_protocol")


def check_auto_update_protected_assets_complete():
    """auto-update.md 受保护资产清单必须覆盖 .claude/workframe-state、projects/proposals、shared/MEMORY.md。"""
    path = FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core" / "auto-update.md"
    if not path.exists():
        return _fail("auto_update_protected_assets_complete", "rule file missing")
    text = path.read_text(encoding="utf-8")

    # 必须定位到 "## 受保护资产约束" 标题（不是表格内提到的"受保护资产"短语）
    idx = text.find("## 受保护资产约束")
    if idx < 0:
        return _fail("auto_update_protected_assets_complete", "no '## 受保护资产约束' section header")
    # 取这一段到下一个 ## 标题
    section_end = text.find("\n## ", idx + len("## 受保护资产约束"))
    section = text[idx:section_end] if section_end > 0 else text[idx:]

    required_assets = [
        ".claude/workframe-state",  # 含 events.jsonl 等
        "projects/proposals",
        "shared/MEMORY.md",
    ]
    missing = [a for a in required_assets if a not in section]
    if missing:
        return _fail(
            "auto_update_protected_assets_complete",
            f"protected assets missing in 受保护资产约束 section: {missing}",
        )
    return _ok("auto_update_protected_assets_complete")


def check_pm_skills_do_not_directly_write_board():
    """PM domain skills 不得指示直接写 board.yaml——落盘统一由用户 / 主 Claude 经
    task-management 执行，与 agents/pm.md「由用户确认后由主 Claude 落盘」边界对齐。
    """
    # 此前只扫两个文件、三个精确短语，而 user-feedback-analysis 写的是
    # 「→ 写入 board.yaml P2 任务」——既不在名单里、短语也不在黑名单里，双重盲区。
    # 现在扫全部 PM domain skills，并按行匹配「动词 + board.yaml」的直写措辞。
    PM_SKILLS = ("requirement-analysis", "feature-breakdown", "acceptance-criteria",
                 "competitive-analysis", "product-metrics-design", "user-feedback-analysis")
    targets = [FRAMEWORK_ROOT / "plugins" / "core" / "skills" / s / "SKILL.md"
               for s in PM_SKILLS]
    direct_write = re.compile(r"(创建|追加|写入|新增|落盘到)[^\n]{0,16}board\.yaml")
    offenders = []
    for path in targets:
        if not path.exists():
            offenders.append(f"{path.relative_to(FRAMEWORK_ROOT)}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            # 同一行里出现「草稿」或「主 Claude 落盘」即视为已限定边界
            if direct_write.search(line) and "草稿" not in line and "主 Claude" not in line:
                offenders.append(
                    f"{path.relative_to(FRAMEWORK_ROOT)}: 直写指令「{line.strip()[:44]}」")
        # 提到 board.yaml 的，全文必须有草稿 / 落盘边界声明；没提的不作要求
        if "board.yaml" in text and "草稿" not in text and "落盘" not in text:
            offenders.append(f"{path.relative_to(FRAMEWORK_ROOT)}: 提到 board.yaml 但无草稿/落盘边界声明")
    if offenders:
        return _fail("pm_skills_do_not_directly_write_board", "; ".join(offenders))
    return _ok("pm_skills_do_not_directly_write_board")


# === projects/ 框架契约修订（Codex 交叉评审落地） ===

def check_scaffold_has_ensure_project_scaffold():
    """scaffold 必须含 ensure_project_scaffold + ensure_gitignore + 关键常量。

    守的是接入路径的关键修复：避免 task-management / test-case-design / self-iteration
    撞到目录缺失，避免运行时状态意外进 git。
    2026-08-10 `tools/install.py` 完全退役后，原「install.py 薄壳接线」那半段随之移除
    ——薄壳都没了，接线自然无从谈起；scaffold 侧的断言一字未动。
    """
    name = "scaffold_has_ensure_project_scaffold"
    scaffold_py = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    if not scaffold_py.exists():
        return _fail(name, "plugins/core/scripts/project_scaffold.py 不存在")
    s_text = scaffold_py.read_text(encoding="utf-8")
    missing = []
    for token in (
        "def ensure_project_scaffold(",
        "def _ensure_gitignore(",
        "SCAFFOLD_TEMPLATES_DIR",
        "GITIGNORE_REQUIRED_ENTRIES",
        # 写入用清单必须与检测用清单分开：前者要精确成对，后者认等价形态。
        # 共用一个常量会让「接入已有项目」路径追加整目录形态，sidecar 被连坐忽略，
        # 而缺失扫描又判齐全 → 永不自我纠正
        "GITIGNORE_MANAGED_LINES",
        "!.claude/workframe-state/memory-index.json",
        # 存量项目的 managed block 里是旧的整目录写法，而缺失扫描认它「已覆盖」→
        # 静默跳过、sidecar 永远进不了 git。必须有就地升级这一步
        "def _upgrade_managed_sidecar_rule(",
        "GITIGNORE_MANAGED_BEGIN",
        "GITIGNORE_MANAGED_END",
        # 已存在 .gitignore 缺条目时必须主动追加 managed block，不能只 skip
        "appended Workframe managed block",
    ):
        if token not in s_text:
            missing.append(token)
    if missing:
        return _fail(name, f"project_scaffold.py 缺关键 token: {missing}")
    # 独立可执行：无人值守 / CI 场景直接调它，不再有仓根薄壳兜底
    if '__name__ == "__main__"' not in s_text or "def main(" not in s_text:
        return _fail(name, "project_scaffold.py 不可独立执行（无人值守场景直接调它）")
    return _ok(name)


def check_scaffold_templates_exist():
    """templates/ 必须含 scaffold 落盘依赖的 5 个模板。"""
    name = "scaffold_templates_exist"
    tdir = FRAMEWORK_ROOT / "plugins" / "core" / "templates"
    required = [
        "board-template.yaml",
        "specs-overview-template.md",
        "issues-templates-template.md",
        "project-changelog-template.md",
        "gitignore-template",
    ]
    missing = [n for n in required if not (tdir / n).exists()]
    if missing:
        return _fail(name, f"模板缺失: {missing}")
    return _ok(name, f"({len(required)} templates)")


def check_gitignore_template_has_required_entries():
    """gitignore-template 必须含 §Git 策略约定的 5 个必需条目（含 tmp/.tmp 截图临时区），
    且 memory-index.json 的放行必须写成 `workframe-state/*` + `!` 例外的成对形态。

    成对形态是硬契约，不是风格偏好：git 不会重新纳入被排除父目录下的文件，写成
    `.claude/workframe-state/` 时 `!` 例外被静默无视——已跟踪的项目看不出异样，
    新项目的 sidecar 则直接失踪且零报错。这条闸就是为了挡住"改回整目录写法"。
    """
    name = "gitignore_template_has_required_entries"
    f = (
        FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "gitignore-template"
    )
    if not f.exists():
        return _fail(name, "gitignore-template 不存在")
    text = f.read_text(encoding="utf-8")
    required = [
        ".claude/settings.local.json",
        ".claude/workframe-state/",
        "logs/",
        "tmp/",
        ".tmp/",
    ]
    missing = [r for r in required if r not in text]
    if missing:
        return _fail(name, f"gitignore-template 缺必需条目: {missing}")
    # 按行精确比对：注释掉的 `# .claude/workframe-state/*` 不算数
    lines = {ln.strip() for ln in text.splitlines()}
    pair = [
        ".claude/workframe-state/*",
        "!.claude/workframe-state/memory-index.json",
    ]
    missing_pair = [p for p in pair if p not in lines]
    if missing_pair:
        return _fail(
            name,
            f"sidecar 放行契约破损，缺行: {missing_pair}"
            "（必须 `workframe-state/*` + `!` 成对；写成整目录形态时 git 静默无视 `!`）",
        )
    return _ok(name, f"({len(required)} required entries + sidecar pair)")


def check_board_template_matches_task_management_schema():
    """board-template.yaml 必须与 task-management/SKILL.md schema 对齐：含 last_updated；priority 仅 P0|P1|P2（不含 P3）。"""
    name = "board_template_matches_task_management_schema"
    f = (
        FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "board-template.yaml"
    )
    if not f.exists():
        return _fail(name, "board-template.yaml 不存在")
    text = f.read_text(encoding="utf-8")
    if "last_updated" not in text:
        return _fail(name, "board-template summary 缺 last_updated")
    if "P3" in text:
        return _fail(name, "board-template 含 P3，与 task-management schema 不一致（仅允许 P0|P1|P2）")
    return _ok(name)


def check_issues_template_has_attribution_fields():
    """issues 模板必须含 6 个归属字段（area/module/component/spec_ref/related_task/source）+ 扁平结构原则。"""
    name = "issues_template_has_attribution_fields"
    f = (
        FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "issues-templates-template.md"
    )
    if not f.exists():
        return _fail(name, "issues-templates-template.md 不存在")
    text = f.read_text(encoding="utf-8")
    required = ["area", "module", "component", "spec_ref", "related_task", "source"]
    missing = [n for n in required if n not in text]
    if missing:
        return _fail(name, f"模板缺归属字段: {missing}")
    if "扁平" not in text:
        return _fail(name, "模板未声明扁平结构原则")
    return _ok(name, f"({len(required)} fields + flat principle)")


def check_session_digest_no_end_of_session_writeback():
    """session-digest/SKILL.md 不应再宣称"会话末尾预写覆盖 hook 骨架"——hook 在 SessionEnd 无条件覆盖该文件。

    检测时允许否定语境引用废弃说法（如"**不存在** XX 的有效路径"）——这是必要的反例说明。
    """
    name = "session_digest_no_end_of_session_writeback"
    f = (
        FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "session-digest" / "SKILL.md"
    )
    if not f.exists():
        return _fail(name, "session-digest SKILL.md 不存在")
    text = f.read_text(encoding="utf-8")

    forbidden_phrases = [
        "覆盖 hook 后续要写的骨架",
        "会话末尾 best-effort",
        "会话结束前由 Claude",
    ]
    # 否定语境标记——出现这些词的行视为反例引用，不报错
    negation_markers = ["不存在", "无效", "不要在会话末尾", "会被", "被 hook", "**不**"]

    offenders = []
    for line in text.splitlines():
        for phrase in forbidden_phrases:
            if phrase in line and not any(neg in line for neg in negation_markers):
                offenders.append(f"{phrase!r} in line: {line.strip()[:80]}")
    if offenders:
        return _fail(name, f"session-digest 仍含肯定语境的已废弃说法: {offenders}")

    if "无条件覆盖" not in text or "下次 SessionStart" not in text:
        return _fail(name, "session-digest 未明确 hook 无条件覆盖 + 唯一有效路径")
    return _ok(name)


def check_agent_protocols_has_rule_processing_order():
    """agent-protocols.md 必须含 §同消息内 rule 处理顺序 + task_blocked fallback dedup 检查段。"""
    name = "agent_protocols_has_rule_processing_order"
    f = (
        FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core" / "agent-protocols.md"
    )
    if not f.exists():
        return _fail(name, "agent-protocols.md 不存在")
    text = f.read_text(encoding="utf-8")
    required_sections = [
        "同消息内 rule 处理顺序",
        "task_blocked fallback dedup",
    ]
    missing = [s for s in required_sections if s not in text]
    if missing:
        return _fail(name, f"agent-protocols 缺章节: {missing}")
    return _ok(name)


# === Cross-platform launcher / bin mode / .cmd portability ===

# 6 个 POSIX 入口脚本（无后缀，shebang 执行，git index 必须 100755）
# workframe-doctor（G1#4）/ workframe-maintenance（G2#8）纳入清单——同享可移植性与 index mode 检查
BIN_POSIX_SCRIPTS = [
    "workframe-python",
    "workframe-audit-board-drift",
    "workframe-recompute-board-summary",
    "workframe-recompute-skill-metrics",
    "workframe-doctor",
    "workframe-maintenance",
]

# 6 个 Windows .cmd wrapper（git index 必须 100644）
BIN_CMD_WRAPPERS = [s + ".cmd" for s in BIN_POSIX_SCRIPTS]


def check_workframe_python_launcher_exists():
    """plugins/core/bin/workframe-python(.cmd) 必须存在并实现 python3 探测 + dispatch。

    Codex P0 fixup-1：每个候选解释器必须先通过 sys.version_info[0] == 3 探测才执行，
    避免误用 macOS 旧版 python (2.7) 或 Windows Microsoft Store alias。
    Codex P0 fixup-2：Windows .cmd 必须 chcp 65001 切 UTF-8 code page，
    否则中文输出在 cmd.exe 默认 cp936/cp1252 下显示为 mojibake。
    """
    name = "workframe_python_launcher_exists"
    bin_dir = FRAMEWORK_ROOT / "plugins" / "core" / "bin"
    posix = bin_dir / "workframe-python"
    cmd = bin_dir / "workframe-python.cmd"

    if not posix.exists():
        return _fail(name, "bin/workframe-python (POSIX launcher) 缺失")
    if not cmd.exists():
        return _fail(name, "bin/workframe-python.cmd (Windows launcher) 缺失")

    posix_text = posix.read_text(encoding="utf-8")
    posix_required = [
        "command -v python",
        "command -v python3",
        "exec python",
        "exec python3",
        "sys.version_info[0] == 3",  # Python 3 探测
    ]
    missing = [t for t in posix_required if t not in posix_text]
    if missing:
        return _fail(name, f"POSIX launcher 缺关键 dispatch / probe 逻辑: {missing}")

    cmd_text = cmd.read_text(encoding="utf-8")
    cmd_required = [
        "where python",
        "where py",
        "where python3",
        "py -3",
        "sys.version_info[0] == 3",  # Python 3 探测
        "chcp 65001",  # UTF-8 code page (中文输出防 mojibake)
    ]
    missing_cmd = [t for t in cmd_required if t not in cmd_text]
    if missing_cmd:
        return _fail(name, f"Windows launcher 缺关键 dispatch / probe / code page 逻辑: {missing_cmd}")

    return _ok(name)


def check_hooks_use_workframe_python_launcher():
    """hooks.json 不允许写死 `python "${CLAUDE_PLUGIN_ROOT}/...`，必须走 workframe-python launcher。

    Codex P0-1 落地：避免在仅有 python3 的 Linux 发行版下 hook 全失败。
    """
    name = "hooks_use_workframe_python_launcher"
    path = FRAMEWORK_ROOT / "plugins" / "core" / "hooks" / "hooks.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail(name, f"hooks.json 解析失败: {e}")

    bare_python_pattern = re.compile(r'(?:^|\s)python\s+"\$\{CLAUDE_PLUGIN_ROOT\}')
    launcher_pattern = re.compile(r'\$\{CLAUDE_PLUGIN_ROOT\}/bin/workframe-python')

    offenders = []
    missing_launcher = []
    for hook_event, groups in data.get("hooks", {}).items():
        for group in groups:
            for hook in group.get("hooks", []):
                cmd = hook.get("command", "")
                if bare_python_pattern.search(cmd):
                    offenders.append(f"{hook_event}: {cmd}")
                if not launcher_pattern.search(cmd):
                    missing_launcher.append(f"{hook_event}: {cmd}")

    if offenders:
        return _fail(name, f"hooks 仍写死 python: {offenders}")
    if missing_launcher:
        return _fail(name, f"hooks 未走 workframe-python launcher: {missing_launcher}")
    return _ok(name)


def _bin_indexed_modes():
    """git index 里 plugins/core/bin/ 的实际文件与 mode。

    用 index 而不是文件系统遍历：未跟踪的 __pycache__ 等产物天然不在其中，
    且 mode 位本来就只在 index 里有意义（工作区 mode 在 core.fileMode=false 下不可信）。

    返回 (modes: dict[文件名 -> mode], error: str|None)。
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--stage", "plugins/core/bin/"],
            cwd=FRAMEWORK_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
    except Exception as e:
        return {}, f"git ls-files 调用失败: {e}"
    if result.returncode != 0:
        return {}, f"git ls-files 退出码 {result.returncode}: {result.stderr}"
    modes = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            meta, path = line.split("\t", 1)          # `<mode> <sha> <stage>\t<path>`
            modes[Path(path).name] = meta.split()[0]
        except Exception:
            continue
    return modes, None


def check_bin_git_index_modes():
    """bin/ **目录里实际有什么就查什么**：无后缀入口 100755、.cmd 100644、两者一一配对。

    此前遍历的是写死的 6 元素名单。后果链是完整的：在 Windows 上新增第 7 个 bin 脚本
    → `core.fileMode=false` 让 git 读不出 exec 位、默认落成 100644 → 该文件不在名单里
    → 本检查根本不看它 → **validate 全绿，而 mac 用户 clone 下来直接 Permission denied**，
    且这个故障在 Windows 上永远复现不出来。改造前，全仓 155 项检查中没有任何一处
    glob 过这个目录。

    名单常量仍保留（别处按名引用它），但**事实源是目录**——两者不一致时本检查报红，
    避免常量悄悄落后于目录。
    """
    name = "bin_git_index_modes"
    modes, err = _bin_indexed_modes()
    if err:
        return _fail(name, err)
    if not modes:
        return _fail(name, "plugins/core/bin/ 在 git index 中为空——目录被整体漏跟踪？")

    errors = []
    posix_seen, cmd_seen = set(), set()
    for fname, mode in sorted(modes.items()):
        suffix = Path(fname).suffix
        if suffix == "":
            posix_seen.add(fname)
            if mode != "100755":
                errors.append(f"{fname} mode={mode}（应为 100755；Windows 上须 git update-index --chmod=+x）")
        elif suffix == ".cmd":
            cmd_seen.add(fname[:-4])
            if mode != "100644":
                errors.append(f"{fname} mode={mode}（应为 100644）")
        else:
            errors.append(f"{fname}: bin/ 只应有无后缀入口与 .cmd 包装，出现了 '{suffix}'")

    # 一一配对：POSIX 入口与 .cmd 包装同进同出，缺一边就有一个平台调不起来
    for miss in sorted(posix_seen - cmd_seen):
        errors.append(f"{miss}: 缺同名 .cmd 包装（Windows 上无法调起）")
    for miss in sorted(cmd_seen - posix_seen):
        errors.append(f"{miss}.cmd: 缺同名无后缀入口（POSIX 上无法调起）")

    # 常量与目录对齐：常量被别处按名引用，漂了那些检查会静默漏掉新文件
    if posix_seen != set(BIN_POSIX_SCRIPTS):
        errors.append(
            f"BIN_POSIX_SCRIPTS 与目录不一致：目录多 {sorted(posix_seen - set(BIN_POSIX_SCRIPTS))}、"
            f"常量多 {sorted(set(BIN_POSIX_SCRIPTS) - posix_seen)}"
        )

    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name, f"{len(posix_seen)} 个 POSIX 入口 + {len(cmd_seen)} 个 .cmd，mode 与配对均正确")


def check_scaffold_creates_baseline_role_memory():
    """ensure_project_scaffold 必须为 4 个 baseline core role 创建 agent-memory 骨架。

    历史修复：之前只创建 shared/，依赖 Write 工具自动 mkdir parents 的隐式契约。
    改为 scaffold 阶段强制预创建 4 个 role 的 MEMORY.md + notes.md。
    """
    name = "scaffold_creates_baseline_role_memory"
    scaffold_py = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    if not scaffold_py.exists():
        return _fail(name, "plugins/core/scripts/project_scaffold.py 不存在")
    text = scaffold_py.read_text(encoding="utf-8")
    required = [
        "BASELINE_CORE_ROLES",
        '"pm"',
        '"dev"',
        '"qa"',
        '"prompt-eng"',
        "_role_memory_placeholder",
        "_role_notes_placeholder",
        "for role in BASELINE_CORE_ROLES",
    ]
    missing = [t for t in required if t not in text]
    if missing:
        return _fail(name, f"project_scaffold.py 缺关键 token: {missing}")
    return _ok(name)


def check_scaffold_workframe_config_merge_mode():
    """write_workframe_config 必须 merge 模式 + 损坏检测（不破坏用户文件）。

    历史修复两轮：先是无条件 write_text 覆盖；后又发现损坏 / 非 dict 时也不能覆盖，
    必须返回 warning 让调用方处理，用户先手工修 JSON。
    2026-08-10 install.py 退役：原「install.py main() 收 warning 进 pending_items」那半段
    随之移除，改为断言 warning 确实被返回给调用方（scaffold main 打印并影响退出码）。
    """
    name = "scaffold_workframe_config_merge_mode"
    scaffold_py = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    if not scaffold_py.exists():
        return _fail(name, "plugins/core/scripts/project_scaffold.py 不存在")
    text = scaffold_py.read_text(encoding="utf-8")
    required = [
        "def write_workframe_config",
        "config_path.exists()",
        "json.loads",
        'existing["project_name"]',
        'existing["framework_version"]',
        'existing["framework_path"]',
        # 损坏检测三类：JSONDecodeError / OSError / 非 dict
        "json.JSONDecodeError",
        "isinstance(parsed, dict)",
        # 必须返回 warning 而不是写覆盖（return config_path, warning 形式）
        "return config_path, warning",
    ]
    missing = [t for t in required if t not in text]
    if missing:
        return _fail(name, f"write_workframe_config 缺 merge 模式或损坏检测逻辑: {missing}")
    # 调用方必须真的收 warning 并让它影响退出码，否则损坏检测形同虚设
    if "config_warning" not in text or "return 1 if config_warning else 0" not in text:
        return _fail(name, "scaffold main() 未消费 config warning（损坏时应非零退出）")
    return _ok(name)


def check_agent_protocols_step2_has_init_fallback():
    """agent-protocols.md 必须含 Step 2 空骨架兜底 + main-led 直做记账语义。

    M1 P0 修复：为项目级新增 role（手工创建场景）的 agent-memory 兜底。

    2026-08-16 增 `role` 取值锚点：主 Claude 直做时事件 role 填 `main`。这句被删则直做
    工作不进事件流——skill 使用率与问题信号只反映委派出去的那部分，self-iteration 的
    判断依据静默失真，且**没有任何报错**。属收口检查 #8 所说的病根类型。
    """
    name = "agent_protocols_step2_has_init_fallback"
    f = FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core" / "agent-protocols.md"
    if not f.exists():
        return _fail(name, "agent-protocols.md 不存在")
    text = f.read_text(encoding="utf-8")
    # 关键词组合：必须同时含"不存在"+"骨架"+"项目级新增 role"
    required = [
        "Step 2",
        "目录/文件不存在的兜底",
        "先创建空骨架",
        "项目级新增 role",
        # main-led 直做记账：填 main 这个取值本身是契约，措辞可改、取值不可改
        "**主 Claude 直做时填 `main`**",
    ]
    missing = [t for t in required if t not in text]
    if missing:
        return _fail(name, f"agent-protocols Step 2 缺兜底语义: {missing}")
    return _ok(name)


def check_log_subagent_activity_no_hardcoded_known_agents():
    """log-subagent-activity.py 不允许硬编码 KNOWN_AGENTS 列表，必须动态发现。

    M1 P0 修复：之前硬编码 5 个 core role + 5 个内置，项目级 custom role
    （ceo / finance 等）会被记成 "subagent" 通用名。
    """
    name = "log_subagent_activity_no_hardcoded_known_agents"
    f = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "log-subagent-activity.py"
    if not f.exists():
        return _fail(name, "log-subagent-activity.py 不存在")
    text = f.read_text(encoding="utf-8")
    # 必须含动态发现函数
    if "_discover_known_agents" not in text:
        return _fail(name, "缺 _discover_known_agents 动态发现函数")
    if "BUILTIN_AGENTS" not in text:
        return _fail(name, "缺 BUILTIN_AGENTS 内置名单常量")
    # 不允许出现旧的硬编码模式 KNOWN_AGENTS = [
    if re.search(r"^KNOWN_AGENTS\s*=\s*\[", text, re.MULTILINE):
        return _fail(name, "仍含硬编码 KNOWN_AGENTS 列表")
    # 必须扫两个来源
    required = [
        ".claude/agents",
        "CLAUDE_PLUGIN_ROOT",
    ]
    missing = [t for t in required if t not in text]
    if missing:
        return _fail(name, f"动态发现缺关键来源: {missing}")
    return _ok(name)


def check_role_customization_guide_has_protocol_contract_section():
    """role-customization-guide.md 必须含 §协议契约段，区分必备 vs 建议项。

    M1 P0 初版：要求 body 必须含 agent-protocols 引用（错误 — rule 是 runtime 自动加载）
    M1.1 fixup（Codex 三轮 F2）：修正口径。必备项仅 frontmatter `memory: project`；
    body 引用降为强烈建议（不是功能必需）；agent-protocols 自动加载机制必须明确说明。
    M1.3（2026-07-27）：官方 sub-agents §What loads at startup 已明确把 project rules
    列入 non-fork subagent 初始 context，此前"整体没有明确保证"的保守口径过时。
    必需串 "没有明确保证" 的语义随之收窄——现指 **path-scoped rules（带 paths: frontmatter）
    在 subagent 中的行为**仍未文档化，而非 rules 整体不可达。
    forbidden 列表保持不变：Explore / Plan 确实 omit CLAUDE.md 与 project rules
    （官方 "Explore and Plan are the only subagents that omit..."），
    故 "runtime 自动加载到所有 subagent" 一类措辞依然错误，不得复现。
    """
    name = "role_customization_guide_has_protocol_contract_section"
    f = (
        FRAMEWORK_ROOT / "plugins" / "core" / "reference" / "role-customization-guide.md"
    )
    if not f.exists():
        return _fail(name, "role-customization-guide.md 不存在")
    text = f.read_text(encoding="utf-8")
    required = [
        "协议契约",
        "memory: project",
        "agent-protocols",
        "必备 1 项",          # 区分必备 / 建议
        "强烈建议",
        "没有明确保证",       # 保守口径：subagent 是否自动加载 rules 没有官方文档保证
    ]
    missing = [t for t in required if t not in text]
    if missing:
        return _fail(name, f"缺协议契约段关键内容: {missing}")
    # 反向校验：禁止两类错误措辞
    forbidden = [
        # 1. 之前 M1 P0 的过强 "必须 body 显式 import" 措辞（已被 M1.1 纠正）
        "必须**显式声明协议入口",
        "body 必须含 agent-protocols 引用",
        # 2. M1.1 摆到另一极端的 "runtime 自动加载到所有 subagent" 措辞（M1.2 纠正）
        "runtime 自动加载到所有 subagent",
        "任何 agent 启动时都能",
        "都能拿到 Step 0/1/2/3 协议",
        # 3. 提前暴露未实现的 CLI flag (Codex F2)
        "--apply-onboarding",
    ]
    bad = [p for p in forbidden if p in text]
    if bad:
        return _fail(name, f"含错误措辞（M1.1/M1.2 已纠正的口径不应再现 / 未实现 CLI 不应暴露）: {bad}")
    return _ok(name)


def check_all_bin_cmd_wrappers_portable():
    """plugins/core/bin/*.cmd 必须 ASCII + CRLF + @echo off + exit /b。

    Codex P0-4：扩展原 check_windows_cmd_wrapper_portable 到所有 .cmd。
    """
    name = "all_bin_cmd_wrappers_portable"
    bin_dir = FRAMEWORK_ROOT / "plugins" / "core" / "bin"
    # 事实源是目录（经 git index）而非写死名单——新增的 .cmd 必须一起受这套约束，
    # 否则它在 cmd.exe 下的行为无人把关。名单不一致由 bin_git_index_modes 报。
    modes, err = _bin_indexed_modes()
    if err:
        return _fail(name, err)
    wrappers = sorted(f for f in modes if f.endswith(".cmd"))
    if not wrappers:
        return _fail(name, "plugins/core/bin/ 下没有任何 .cmd 包装——Windows 入口整体缺失？")
    errors = []
    for wrapper in wrappers:
        path = bin_dir / wrapper
        if not path.exists():
            errors.append(f"{wrapper}: 在 git index 但工作区缺失")
            continue
        data = path.read_bytes()
        if any(b > 0x7F for b in data):
            errors.append(f"{wrapper}: 含非 ASCII 字节")
            continue
        if b"\r\n" not in data or data.replace(b"\r\n", b"").find(b"\n") != -1:
            errors.append(f"{wrapper}: 必须使用 CRLF 行尾")
            continue
        text = data.decode("ascii")
        if "@echo off" not in text:
            errors.append(f"{wrapper}: 缺 @echo off")
        if "exit /b" not in text:
            errors.append(f"{wrapper}: 缺 exit /b")
    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name, f"{len(wrappers)} 个 .cmd 包装均为 ASCII + CRLF + @echo off + exit /b")


def check_readme_check_count_current():
    """README 里若写了 validate 的检查项数，必须等于实际值。

    `check_unreleased_changelog_numbers` 的 docstring 自己就点名过「136 项检查」是它要
    消灭的那类漂移——**但那道闸只读 CHANGELOG.md**，README 里的同一个数字不在任何闸的
    扫描范围内，于是它从 136 一路漂到实际值都无人察觉。

    两种写法都放行：不写具体数字（最省事，永不漂），或者写了就必须准。
    """
    name = "readme_check_count_current"
    readme = FRAMEWORK_ROOT / "README.md"
    if not readme.exists():
        return _fail(name, "README.md 缺失")
    text = readme.read_text(encoding="utf-8")
    actual = len(CHECKS)
    bad = []
    for m in re.finditer(r"(\d+)\s*项检查", text):
        if int(m.group(1)) != actual:
            lineno = text[:m.start()].count("\n") + 1
            bad.append(f"README.md:{lineno} 写 {m.group(1)} 项检查，实际 {actual}")
    if bad:
        return _fail(name, "; ".join(bad))
    return _ok(name, f"检查数表述与实际一致（{actual}）")


def check_no_case_only_filename_collisions():
    """全仓不得有仅大小写不同的同名文件。

    Windows 不敏感、macOS 默认不敏感（但可被格式化为敏感）、Linux 敏感。一对
    `Foo.md` / `foo.md` 在 Windows 上 clone 时会互相覆盖（后落盘的赢），
    **而本仓的开发与验证全在 Windows 上做，这类问题在这里永远发现不了**。
    """
    name = "no_case_only_filename_collisions"
    try:
        r = subprocess.run(
            ["git", "ls-files"], cwd=FRAMEWORK_ROOT, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=15,
        )
    except Exception as e:
        return _fail(name, f"git ls-files 调用失败: {e}")
    if r.returncode != 0:
        return _fail(name, f"git ls-files 退出码 {r.returncode}: {r.stderr}")

    seen, collisions = {}, []
    for rel in r.stdout.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        key = rel.lower()
        if key in seen and seen[key] != rel:
            collisions.append(f"{seen[key]} ↔ {rel}")
        else:
            seen.setdefault(key, rel)
    if collisions:
        return _fail(name, "; ".join(collisions))
    return _ok(name, f"{len(seen)} 个被跟踪文件无大小写冲突")


# hook 脚本里退出点的审查标注：写在同行或上一行，后面跟理由
_EXIT_AUDITED_MARK = "exit-audited"


def _explicit_exit_points(source):
    """用 AST 找出源码里所有显式退出点，返回 [(行号, 形态, 是否零退出)]。

    **不用正则**是因为正则修不好这三类：`sys.exit(1); return`（行内多语句）、
    跨行写法（`sys.exit(\\n 1 \\n)`）、`raise SystemExit(1);`（分号结尾）。
    AST 另有两个附带好处：注释与字符串里的同形文本天然不会误命中；参数是不是字面量 0
    由语法树直接判定，不必猜。

    **别名与间接调用**：先扫一遍 import 建立映射，因此下列写法都能识别——
    `import sys as s; s.exit(1)`、`from sys import exit; exit(1)`、
    `from sys import exit as bye; bye(1)`、`os._exit(1)`、`from os import _exit`。
    仍然识别不了的是运行期动态绑定（`f = sys.exit; f(1)`）——那需要数据流分析，
    本闸不做，也不声称做得到。

    语法错误时返回 None（由 check_scripts_syntax 负责报，本处不重复）。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    # 模块别名 → 真名；以及「直接被导入的退出函数」的本地名
    mod_alias = {}          # 本地名 -> "sys" / "os"
    direct_exit = {}        # 本地名 -> 展示用形态
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in ("sys", "os"):
                    mod_alias[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom):
            if node.module == "sys":
                for a in node.names:
                    if a.name == "exit":
                        direct_exit[a.asname or a.name] = "sys.exit(...)"
            elif node.module == "os":
                for a in node.names:
                    if a.name == "_exit":
                        direct_exit[a.asname or a.name] = "os._exit(...)"

    points = []
    for node in ast.walk(tree):
        args, label = None, None
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                mod = mod_alias.get(f.value.id)
                if mod == "sys" and f.attr == "exit":
                    args, label = node.args, "sys.exit(...)"
                elif mod == "os" and f.attr == "_exit":
                    args, label = node.args, "os._exit(...)"
            elif isinstance(f, ast.Name):
                if f.id == "SystemExit":
                    args, label = node.args, "SystemExit(...)"
                elif f.id in direct_exit:
                    args, label = node.args, direct_exit[f.id]
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Name) \
                and node.exc.id == "SystemExit":
            args, label = [], "raise SystemExit"      # 裸 raise，等价于 exit(None)=0
        if label is None:
            continue
        # os._exit 没有「0 即安全」的说法之外的特权，判定规则与 sys.exit 一致
        zero = (not args) or (
            len(args) == 1 and isinstance(args[0], ast.Constant)
            and args[0].value in (0, None)
        )
        points.append((node.lineno, label, zero))
    return points


def check_hook_scripts_no_blocking_exit():
    """hooks.json 引用的脚本里，非零退出点必须标注 `# exit-audited: <理由>`。

    官方语义（code.claude.com/docs/en/hooks）：exit 2 是唯一靠退出码本身就阻断的码，
    而 **UserPromptSubmit 的 exit 2 会「block prompt processing and erase the prompt」**
    ——用户敲的话会被直接吞掉。当前代码恰好避开了这个坑，但**没有任何东西钉住它**：
    新增一个 hook 脚本时随手写 `sys.exit(1)` 不会被任何闸拦下。

    **覆盖面**：走 AST，覆盖 `sys.exit(...)` / `SystemExit(...)` / `raise SystemExit`
    的所有语法形态——含行内多语句（`sys.exit(1); return`）、跨行调用、分号结尾。
    只有 `sys.exit(0)` / `sys.exit()` / 裸 `raise SystemExit` 自动放行。

    两次收紧的经过值得记：最初只匹配整数字面量，5 个真实退出点全部漏检；改成贪婪正则后
    仍要求语句位于行末，行内多语句与跨行写法照样漏，而 docstring 已经写成「全部形态」
    ——**声明比实现强，是比漏检本身更坏的问题**。现在换 AST 才配得上那句话。

    仍无法覆盖的是**语义**：`sys.exit(main())` 的返回值域静态证明不了。所以不去假装能
    证明，而是要求人审过并把理由写在标注里（`# exit-audited: <为什么这里安全>`）。
    """
    name = "hook_scripts_no_blocking_exit"
    hooks_path = FRAMEWORK_ROOT / "plugins" / "core" / "hooks" / "hooks.json"
    if not hooks_path.exists():
        return _fail(name, "hooks.json 缺失")
    try:
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail(name, f"hooks.json 解析失败: {e}")

    scripts = set()
    for groups in data.get("hooks", {}).values():
        for hg in groups:
            for h in hg.get("hooks", []):
                for m in re.finditer(r"scripts/([A-Za-z0-9_.-]+\.py)", h.get("command", "")):
                    scripts.add(m.group(1))
    if not scripts:
        return _fail(name, "未能从 hooks.json 解析出任何脚本名")

    scripts_dir = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    offenders, scanned = [], 0
    for fname in sorted(scripts):
        p = scripts_dir / fname
        if not p.exists():
            offenders.append(f"{fname}: hooks.json 引用了但文件不存在")
            continue
        scanned += 1
        source = p.read_text(encoding="utf-8")
        points = _explicit_exit_points(source)
        if points is None:
            offenders.append(f"{fname}: 语法错误，无法解析退出点")
            continue
        lines = source.splitlines()
        for lineno, label, zero in points:
            if zero:
                continue
            # 标注写在同行或紧邻上一行
            context = lines[lineno - 1] + (lines[lineno - 2] if lineno >= 2 else "")
            if _EXIT_AUDITED_MARK not in context:
                offenders.append(
                    f"{fname}:{lineno}: 退出点 `{label}` 未标注 # {_EXIT_AUDITED_MARK}"
                    f"（hook 路径上的非零退出会干扰会话；确认安全后写明理由）"
                )
    if offenders:
        return _fail(name, "; ".join(offenders))
    return _ok(name, f"{scanned} 个 hook 脚本无未标注的非零退出")


# macOS 自带 BSD 版 coreutils，以下语法是 GNU 专有——用了就在 mac 上静默坏。
# 长选项与短选项都要收：只拦 `grep -P` 会漏掉等价的 `grep --perl-regexp`。
# `_OPTS` = 命令名与目标选项之间可以隔着任意多个别的选项/参数，但不跨越命令边界
# （`|` `;` 与换行）。早先的写法要求目标选项紧跟命令，于是 `grep -i -P`、
# `grep --color=auto --perl-regexp`、`sed -n -i` 这类组合全部漏检。
_OPTS = r"(?:[^|;\n]*?\s)?"
_GNU_ONLY_PATTERNS = [
    # `-i` 后紧跟空串参数（`sed -i '' f` / `sed -i'' f`）是 BSD 兼容写法，要放行；
    # 用否定前瞻而不是「必须跟空格」——后者会漏掉 `sed -n -i f` 这类组合。
    (rf"\bsed\s+{_OPTS}(?:-[A-Za-z]*i\b(?!\s*(?:''|\"\"))|--in-place\b)",
     "sed -i（BSD 需要 `sed -i ''`）"),
    (rf"\breadlink\s+{_OPTS}(?:-[A-Za-z]*f\b|--canonicalize\b)", "readlink -f（BSD 无此选项）"),
    (rf"\bgrep\s+{_OPTS}(?:-[A-Za-z]*P\b|--perl-regexp\b)", "grep -P（BSD 无 PCRE）"),
    (rf"\bdate\s+{_OPTS}(?:-d\b|--date\b)", "date -d（BSD 用 -v / -j -f）"),
    (rf"\bstat\s+{_OPTS}(?:-[A-Za-z]*c\b|--format\b)", "stat -c（BSD 用 -f）"),
    (rf"\bmktemp\s+{_OPTS}(?:-[A-Za-z]*p\b|--tmpdir\b)", "mktemp -p / --tmpdir（BSD 无此选项）"),
    (rf"\bbase64\s+{_OPTS}(?:-[A-Za-z]*w\b|--wrap\b)", "base64 -w（BSD 无此选项）"),
    (r"\bdeclare\s+-[A-Za-z]*A\b", "declare -A（关联数组需 bash 4+，mac 自带 3.2）"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*(,,|\^\^)\}", "${var,,} / ${var^^}（需 bash 4+）"),
    (rf"\bxargs\s+{_OPTS}(?:-[A-Za-z]*r\b|--no-run-if-empty\b)", "xargs -r（BSD 无此选项）"),
]

# 否定语境：文档里「不要用 sed -i」是在**警告**这件事，不是在用它。
# 不排除的话，写一句提醒就会撞闸——本仓的 NOTICE / 跨平台说明都会踩到。
_CLAUSE_SPLIT_RE = re.compile(r"[;；。]")
_NEGATED_CONTEXT_RE = re.compile(
    r"不要用|不得用|不能用|别用|避免用|禁止用|不可用|不支持|不要写|"
    r"\bdo\s+not\s+use\b|\bdon'?t\s+use\b|\bavoid\b|\bnever\s+use\b|\bnot\s+portable\b|"
    r"BSD (?:无|不支持)|需要\s*`?sed -i ''",
    re.IGNORECASE,
)


def check_no_gnu_only_shell_syntax():
    """出货资产里不得出现 GNU coreutils 专有语法。

    macOS 自带的是 **BSD** 版本，且 `/bin/bash` 停在 **3.2**（GPLv3 之故）。
    当前全仓零命中——但零命中是**当下的事实，不是未来的保证**：将来某个 skill 里
    写一句 `sed -i` 教模型执行，就会在 mac 上静默坏掉，而作者在 Windows 上测不出来。
    这道闸把"当前恰好没有"变成"以后也不会有"。
    """
    name = "no_gnu_only_shell_syntax"
    offenders, scanned = [], 0
    for rel in _repo_text_files():
        if not rel.startswith("plugins/"):
            continue
        scanned += 1
        try:
            text = (FRAMEWORK_ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            # 按子句判定而非整行：整行跳过时，`# avoid sed -i; sed -i file` 里真正在用的
            # 那半句会跟着蒙混过关。分句后前半句带否定词被放行、后半句照常报红。
            hit = None
            for clause in _CLAUSE_SPLIT_RE.split(line):
                if _NEGATED_CONTEXT_RE.search(clause):
                    continue
                for pat, desc in _GNU_ONLY_PATTERNS:
                    if re.search(pat, clause):
                        hit = desc
                        break
                if hit:
                    break
            if hit:
                offenders.append(f"{rel}:{lineno}: {hit}")
    if offenders:
        return _fail(name, "; ".join(offenders[:6])
                     + (f" …… 共 {len(offenders)} 处" if len(offenders) > 6 else ""))
    return _ok(name, f"扫描 {scanned} 份出货资产，无 GNU 专有语法")


def check_bin_gitattributes_eol():
    """bin/ 下每个入口都必须被 .gitattributes 钉住行尾：无后缀 = lf，.cmd = crlf。

    为什么必须有这道闸：`.gitattributes` 是**仓库级**配置，它存在的全部意义就是覆盖各
    贡献者本机的 `core.autocrlf`。此前它逐文件枚举了 6 个入口——**当前恰好全覆盖纯属
    巧合**，新增第 7 个入口时不会有任何东西提醒你补一行；而一个 `autocrlf=false` 的
    Windows 贡献者（编辑器默认 CRLF 保存）会直接把 CRLF 提交进去，mac 上执行时报
    `bad interpreter: no such file or directory`，**报错信息里完全不提 CRLF**。

    改造前 155 项检查中没有任何一处读过 `.gitattributes`（grep 零命中）。
    """
    name = "bin_gitattributes_eol"
    modes, err = _bin_indexed_modes()
    if err:
        return _fail(name, err)
    if not modes:
        return _fail(name, "plugins/core/bin/ 在 git index 中为空")

    rels = [f"plugins/core/bin/{f}" for f in sorted(modes)]
    try:
        r = subprocess.run(
            ["git", "check-attr", "eol", "--"] + rels,
            cwd=FRAMEWORK_ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
    except Exception as e:
        return _fail(name, f"git check-attr 调用失败: {e}")
    if r.returncode != 0:
        return _fail(name, f"git check-attr 退出码 {r.returncode}: {r.stderr}")

    # 输出形如 `<path>: eol: <value>`；路径本身可能含冒号，故从右侧切
    got = {}
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        head, _, value = line.rpartition(": ")
        path = head.rpartition(": ")[0]
        if path:
            got[Path(path).name] = value.strip()

    errors = []
    for fname in sorted(modes):
        want = "crlf" if fname.endswith(".cmd") else "lf"
        actual = got.get(fname, "(未取到)")
        if actual != want:
            errors.append(f"{fname}: eol={actual}，应为 {want}（.gitattributes 漏了这个文件？）")
    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name, f"{len(modes)} 个 bin 入口的行尾均已被 .gitattributes 钉死")


def check_sync_rules_project_arg_and_single_impl():
    """sync-rules 的 --project 契约 + 双入口单一实现。

    历史陷阱：插件侧曾无参数解析，`--project` 被静默忽略、rules 同步到调用方 cwd
    却照常打印 success。launcher 从任意目录发起，靠这个参数指定目标——必须同时
    保证「参数存在」与「未知参数报错」，只加参数不加严格解析等于换个形式留坑。
    """
    name = "sync_rules_project_arg_and_single_impl"
    plugin_side = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "sync-rules.py"
    tools_side = FRAMEWORK_ROOT / "tools" / "sync-rules.py"
    errors = []
    if not plugin_side.exists():
        return _fail(name, f"插件侧 sync-rules.py 缺失: {plugin_side}")
    ptext = plugin_side.read_text(encoding="utf-8")
    if "argparse" not in ptext or '"--project"' not in ptext:
        errors.append("插件侧缺 argparse/--project（未知参数会被静默吞掉）")
    if "def sync_rules(" not in ptext:
        errors.append("插件侧缺可复用的 sync_rules() —— 双入口无法共用实现")
    if tools_side.exists():
        ttext = tools_side.read_text(encoding="utf-8")
        if "shutil.copy2" in ttext:
            errors.append("tools/sync-rules.py 自带复制实现（应薄壳复用插件侧，防漂移）")
        if "sync_rules" not in ttext:
            errors.append("tools/sync-rules.py 未复用插件侧 sync_rules()")
    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name)


def check_core_reference_links_resolve():
    """`plugins/core/reference/` 内的相对链接必须解析得到。

    这批规范文档被 agents / 多个 skill / scaffold 共同引用，位置变动时相对深度容易失配。
    只扫 reference/：skills/ 与 templates/ 正文含大量示意用的假路径，全仓扫会淹在误报里。
    """
    name = "core_reference_links_resolve"
    ref_dir = FRAMEWORK_ROOT / "plugins" / "core" / "reference"
    if not ref_dir.is_dir():
        return _fail(name, f"reference/ 缺失: {ref_dir}")
    bad, total = [], 0
    for f in sorted(ref_dir.glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"\]\((\.{1,2}/[^)#\s]+)", text):
            total += 1
            if not (f.parent / m.group(1)).resolve().exists():
                bad.append(f"{f.name} → {m.group(1)}")
    if bad:
        return _fail(name, f"{len(bad)} 处断链: " + "；".join(bad[:5]))
    return _ok(name, f"({total} 条相对链接全部可解析)")


def check_scaffold_params_interface():
    """scaffold 的 --params 渲染契约：launcher 靠它把对话产物确定性落盘。

    守三件事：参数入口存在、必填字段校验存在、占位符零残留断言存在——
    最后一条是「不靠模型自觉替换占位符」这一设计的兜底，掉了就退回旧的手工模式。
    """
    name = "scaffold_params_interface"
    script = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    if not script.exists():
        return _fail(name, f"project_scaffold.py 缺失: {script}")
    text = script.read_text(encoding="utf-8")
    required = {
        '"--params"': "参数入口",
        '"--require-empty"': "新建路径的非空拒绝闸",
        "def _substantive_entries(": "近空判定的函数定义",
        "def mark_setup_step(": "断点增量记账函数",
        'mark_setup_step(project_dir, "scaffold")': "scaffold 成功即落第一步",
        "_substantive_entries(project_dir)": "近空判定被真正调用",
        "PARAM_REQUIRED": "必填字段清单",
        "unresolved placeholder": "占位符零残留断言",
        "render_project_docs": "对话产物渲染入口",
        "extract_role_profile_routing": "role_profile 路由段确定性派生",
    }
    missing = [f"{desc}({token})" for token, desc in required.items() if token not in text]
    # 速查表模板已于 2026-08-15 退役：它是 document-norms §1 归属矩阵的有损副本
    # （§1 有 28 行含 type 字段与反模式警告，表只有 12 行且丢字段），却无条件渲染进
    # 每个新项目的必载上下文。副本与 §1 一分叉，读到副本的人就照错的做。
    if missing:
        return _fail(name, "缺: " + "；".join(missing))
    return _ok(name)


def check_core_assets_no_repo_internal_path():
    """出货资产不得引用框架仓内部路径（`check_launcher_no_cross_plugin_relative_path` 的对偶闸）。

    背景：3b 建了三道闸守「launcher 不许引用 core 之外」，但反方向一直没人守——
    2026-08-10 实测发现 core 侧有 8 处引用 `dev-docs/`，其中 rules 镜像里那条在
    dogfood 项目实测已是死链，`activity-state-template.json` 那条更会被 scaffold
    复制进**每个用户项目**。

    扫描面 = 会离开框架仓到达用户侧的三类：
      - `rules/`     → 被 sync-rules 镜像进 <项目>/.claude/rules/workframe/core/
      - `templates/` → 被 project_scaffold 渲染成用户项目文件
      - `skills/`    → 随插件安装到用户机器的插件缓存

    禁止的是**仓库结构路径**（用户侧不存在）：`dev-docs/`（已剥离为私有仓）、
    `plugins/core/`（用户侧插件根不长这样）、`tools/`（仓根工具，不随插件分发）。
    插件内相对引用（`.workframe-meta/` / `templates/` 等）不在此列。
    """
    name = "core_assets_no_repo_internal_path"
    base = FRAMEWORK_ROOT / "plugins" / "core"
    scan_dirs = [base / "rules", base / "templates", base / "skills"]
    # 两类合法写法必须放行，否则会误伤（同 launcher 闸二检的教训：别见字符串就拦）：
    #   `**/plugins/core/x` —— Glob 搜索模式，正是为了在任意安装布局下找到文件
    #   `plugins/core/**`   —— 作用域通配，用于点名 L2 保护域，不是让人去打开的路径
    # `tools/` 太常见于自然语句（"工具"、npm tools/ 等），只在指向 .py 脚本时判定。
    forbidden = {
        "dev-docs/": r"(?<!\*\*/)dev-docs/",
        "plugins/core/": r"(?<!\*\*/)plugins/core/(?!\*\*)",
        "tools/xxx.py": r"\btools/[\w-]+\.py",
    }
    # 扫全部文本文件，不用后缀白名单——初版白名单漏掉了无后缀的 `gitignore-template`
    # 与 `.jsonl`，而两者恰好都带违规引用（实跑 scaffold 才发现，静态审查没看出来）。
    # 二进制按内容判（含 NUL 字节），比维护后缀名单可靠。
    offenders = []
    for d in scan_dirs:
        if not d.is_dir():
            continue
        for f in sorted(d.rglob("*")):
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            raw = f.read_bytes()
            if b"\x00" in raw:
                continue
            for lineno, line in enumerate(
                    raw.decode("utf-8", errors="replace").splitlines(), 1):
                for label, pat in forbidden.items():
                    if re.search(pat, line):
                        offenders.append(
                            f"{f.relative_to(FRAMEWORK_ROOT)}:{lineno} → {label}")
                        break
    if offenders:
        return _fail(
            name,
            f"{len(offenders)} 处出货资产引用了仓内路径（用户侧不存在）: "
            + "；".join(offenders[:5]) + ("…" if len(offenders) > 5 else ""),
        )
    return _ok(name, f"(scanned {len(scan_dirs)} shipping asset dirs)")


def check_plugins_no_user_docs_path():
    """插件资产不得引用仓根用户文档路径（docs/<篇名>.md）——用户侧插件缓存里没有 docs/。

    2026-08-13 文档走查实锤三处死指针：onboard/SKILL.md 相对链接向上爬出插件指向
    docs/onboarding.md、skill-customization-guide 与 log-subagent-activity.py 的
    docs/ 字面引用。对偶闸 `check_core_assets_no_repo_internal_path` 拦不住：扫描面
    （rules/templates/skills）漏掉 reference/ 与 scripts/，禁止清单也没有 docs/。
    该闸扫描面若直接扩容会误伤 4 处合法 contributor 注释（tools/validate.py 提及、
    LEGACY marker 字面量），故单立本闸：全 plugins/ 文本文件，只禁六篇用户文档的
    路径字面量。指路的正确写法是「框架仓用户文档 <篇名>」，不落具体路径。
    """
    name = "plugins_no_user_docs_path"
    pat = re.compile(
        r"docs/(?:quickstart|setup-guide|concepts|onboarding|rules-sync|README)\.md")
    offenders = []
    root = FRAMEWORK_ROOT / "plugins"
    for p in sorted(root.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        raw = p.read_bytes()
        if b"\x00" in raw:
            continue
        for lineno, line in enumerate(
                raw.decode("utf-8", errors="replace").splitlines(), 1):
            if pat.search(line):
                offenders.append(
                    f"{p.relative_to(FRAMEWORK_ROOT)}:{lineno}: '{line.strip()[:70]}'")
    if offenders:
        return _fail(
            name,
            f"{len(offenders)} 处插件资产指向仓根用户文档（用户侧不存在）: {offenders[:5]}")
    return _ok(name, "(plugins/ clean of user-docs paths)")


def check_marketplace_lists_both_plugins():
    """市场清单必须同时列出两个插件，且 source 指向真实存在的目录。

    launcher 是用户装的第一个（多数情况下也是唯一一个）插件——它没上架，整条上手链路的
    起点就不存在，README 那两条安装命令会直接失败。版本一致性由 `check_version_consistency`
    守，这里守「在不在、指得对不对、顺序合不合理」。
    """
    name = "marketplace_lists_both_plugins"
    mp = FRAMEWORK_ROOT / ".claude-plugin" / "marketplace.json"
    try:
        mk = json.loads(mp.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail(name, f"marketplace.json 解析失败: {e}")
    entries = mk.get("plugins", [])
    by_name = {e.get("name"): e for e in entries}
    errors = []
    for plugin in ("workframe-launcher", "core"):
        e = by_name.get(plugin)
        if e is None:
            errors.append(f"未列出 {plugin}")
            continue
        src = e.get("source", "")
        if not src:
            errors.append(f"{plugin} 缺 source")
        elif not (FRAMEWORK_ROOT / src.lstrip("./")).is_dir():
            errors.append(f"{plugin} 的 source 指向不存在的目录: {src}")
        if not (e.get("description") or "").strip():
            errors.append(f"{plugin} 缺 description（市场列表里用户看到的第一句话）")
    # launcher 排前面：用户先装它，列表顺序即推荐顺序
    order = [e.get("name") for e in entries]
    if "workframe-launcher" in order and "core" in order:
        if order.index("workframe-launcher") > order.index("core"):
            errors.append("launcher 应排在 core 之前（用户先装 launcher）")
    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name, f"({len(entries)} plugins listed)")


def check_retired_terms_absent():
    """退役概念零残留（「退役不留墓碑」纪律的机器闸）。

    初始化链路重构退役了一批概念，但退役常常只删实现、忘删描述它的文字——
    2026-08-10 实测：create-project 目录早在 3e-2 就删了，46 处引用仍在，其中两处模板
    会把「`/core:create-project`」渲染进**每个用户项目**的 CLAUDE.md，用户照着敲会撞空。

    扫描面：`plugins/` + 市场清单 + README + `docs/`（2026-08-11 文档全量重写时纳入——
    此前 quickstart 与 create-project-guide 整篇讲旧链路，13 处 `tools/install.py`
    死引用因扫描面不含 docs/ 一直没人报）。CHANGELOG 是历史账，永久豁免。
    """
    name = "retired_terms_absent"
    retired = {
        "create-project": r"create-project",
        "六类型枚举": r"client-delivery|content-studio|business-ops|content-ops",
        "software-mvp": r"software-mvp",
        "project-types-catalog": r"project-types-catalog",
        "起步项目": r"起步项目",
        "install-report": r"install-report",
        "tools/install.py": r"tools[/\\]install\.py|install\.py",
        # 旧 specs/ 需求落盘体系（modules/ 体系恒启用后退役；I-012 清扫的防回潮词。
        # REQ-{序号} 本身不入表——role-customization-guide 有「别写这种占位符」的反例句属合法提及）
        "旧specs需求落盘": r"projects/specs/REQ-|specs/<模块>/REQ-|旧\s*specs/?\s*体系",
        # pmo 角色 2026-08-12 整体退役（baseline 5→4：职责已被 hook 链路 / 主 Claude / @qa
        # 全量接管，签发权收归 @qa、流程信号落 shared/notes.md）。不用 \b——中文正文里
        # 「含pmo」这类汉字紧邻场景 \b 会漏（CJK 属 \w），宁可全匹配后人工裁定。
        "pmo角色": r"(?i)pmo",
        # requirement-archiving 是九段流水线（Phase 0-8），「八阶段/eight-phase」是把
        # Phase 0-8 数成 8 的作废口径——2026-08-13 走查在英文 README 与 launcher SKILL
        # 各抓到一批（README 已中文化时顺修，SKILL 4 处由本词条跑红后清零）。
        "八阶段口径": r"八阶段|eight-phase",
    }
    scan_targets = [
        FRAMEWORK_ROOT / "plugins",
        FRAMEWORK_ROOT / ".claude-plugin" / "marketplace.json",
        FRAMEWORK_ROOT / "README.md",
        FRAMEWORK_ROOT / "docs",
    ]
    # 豁免（**已收窄到单文件**）：只放行 project_scaffold.py 里的 LEGACY marker 常量。
    #
    # 原豁免全仓生效：旧 marker 字面量含 "auto-added by tools/install.py"，改它会让老项目检测
    # 失配、被追加第二个 managed block，于是整串被放行。但那是「暂时做不到」——双匹配（认旧串、
    # 只生成新串）本来就能解，只是没人做。豁免把临时妥协固化成了永久死引用，而它会写进**每个**
    # 用户项目的 .gitignore 并提交进对方 git 历史（2026-08-10 B 场景实测发现）。
    #
    # 现在旧串只允许作为 LEGACY 常量存在于 scaffold 脚本内，且必须配套 `_has_managed_marker`
    # 双匹配——后者由 check_gitignore_marker_dual_match 断言，豁免不再等于放任。
    exempt_substrings = ("Workframe managed (auto-added by",)
    exempt_only_in = "project_scaffold.py"
    offenders = []
    missing = [str(t.relative_to(FRAMEWORK_ROOT)) for t in scan_targets if not t.exists()]
    if missing:
        # 清单式闸的静默失守防线（I-028）：扫描面塌了必须报，不许无声缩小覆盖
        return _fail(name, f"scan targets missing: {missing}")
    for target in scan_targets:
        paths = [target] if target.is_file() else target.rglob("*")
        for f in paths:
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            raw = f.read_bytes()
            if b"\x00" in raw:
                continue
            for lineno, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                if f.name == exempt_only_in and any(x in line for x in exempt_substrings):
                    continue
                for label, pat in retired.items():
                    if re.search(pat, line):
                        offenders.append(f"{f.relative_to(FRAMEWORK_ROOT)}:{lineno} → {label}")
                        break
    if offenders:
        return _fail(
            name,
            f"{len(offenders)} 处退役概念残留: " + "；".join(offenders[:5])
            + ("…" if len(offenders) > 5 else ""),
        )
    return _ok(name, f"({len(retired)} 类退役概念零残留)")


def check_gitignore_marker_dual_match():
    """`.gitignore` managed marker 必须「认两代、只生成新的」。

    背景：旧 marker 写死了 `tools/install.py`（该文件已随安装器退役被删）。它会被写进每个
    用户项目的 .gitignore 并提交进对方 git 历史，成为永久死引用。当初为幂等性冻结了这行字
    并给 check_retired_terms_absent 加了全仓豁免——幂等保住了，正确性没有。

    正解是双匹配：检测认新旧两串（老项目的旧块照样识别，不会被追加第二个 block），
    生成只用新串。本闸守住这个契约的三个失效方式：

      1. 生成串又混进 `install.py`（改回去了）
      2. LEGACY 串被删（老项目的旧块认不出来 → 被追加重复 block）
      3. **检测点绕过 `_has_managed_marker`**（写回 `GITIGNORE_MANAGED_BEGIN in text`）
         ——这条最隐蔽：新项目一切正常，只有老项目才炸，而老项目不在测试面里

    第 3 条是本闸的重点。前两条改坏了还容易看出来，第 3 条改坏了长得完全正常。
    """
    name = "gitignore marker 双匹配契约"
    scaffold = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    if not scaffold.is_file():
        return _fail(name, f"缺 {scaffold.relative_to(FRAMEWORK_ROOT)}")
    text = scaffold.read_text(encoding="utf-8")

    problems = []

    m = re.search(r'^GITIGNORE_MANAGED_BEGIN\s*=\s*(.+)$', text, re.M)
    if not m:
        problems.append("找不到 GITIGNORE_MANAGED_BEGIN 定义")
    elif "install.py" in m.group(1):
        problems.append("生成用的 GITIGNORE_MANAGED_BEGIN 又含 install.py（死引用会进用户项目）")

    if "GITIGNORE_MANAGED_BEGIN_LEGACY" not in text:
        problems.append("缺 GITIGNORE_MANAGED_BEGIN_LEGACY——老项目的旧 marker 将认不出来，被追加重复 block")
    elif "auto-added by tools/install.py" not in text:
        problems.append("LEGACY 里没有旧串本体，等于没有兼容")

    if "def _has_managed_marker" not in text:
        problems.append("缺 _has_managed_marker——双匹配没有实现")
    else:
        # 检测点必须走 _has_managed_marker；裸 `GITIGNORE_MANAGED_BEGIN in text` 会漏掉老项目
        for lineno, line in enumerate(text.splitlines(), 1):
            if re.search(r'\bGITIGNORE_MANAGED_BEGIN\s+in\s+\w', line) and "def _has_managed_marker" not in line:
                # 允许出现在 _has_managed_marker 函数体内（那正是双匹配的实现）
                before = "\n".join(text.splitlines()[:lineno])
                last_def = before.rfind("\ndef ")
                if last_def == -1 or "_has_managed_marker" not in before[last_def:last_def + 60]:
                    problems.append(
                        f"L{lineno} 绕过双匹配直接用 `GITIGNORE_MANAGED_BEGIN in ...`——老项目会被追加重复 block"
                    )

    if problems:
        return _fail(name, "；".join(problems))
    return _ok(name, "(认两代 marker、只生成新串、检测走 _has_managed_marker)")


def check_pending_work_wired():
    """初始化第二幕接力链的四环必须都在：记批次 → 存状态 → 启动接力 → doctor 可见。

    这条链断在哪一环都**不报错，只是再也没人提起**——而它本来就是为「用户装完以为
    结束了」设计的，静默失效等于回到原点：推迟的转写批次永远搁置，项目长期停在
    「结构建好了、内容没落地」的半初始化状态。
    """
    name = "pending_work_wired"
    scaffold = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    prep = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "session-start-prep.py"
    doctor = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    skill = FRAMEWORK_ROOT / "plugins" / "workframe-launcher" / "skills" / "setup" / "SKILL.md"
    missing = [p.name for p in (scaffold, prep, doctor, skill) if not p.is_file()]
    if missing:
        return _fail(name, f"缺文件: {'、'.join(missing)}")

    errors = []
    if "def mark_pending_work" not in scaffold.read_text(encoding="utf-8"):
        errors.append("project_scaffold 缺 mark_pending_work（无处记批次）")
    ptext = prep.read_text(encoding="utf-8")
    if "def check_pending_work" not in ptext:
        errors.append("session-start-prep 缺 check_pending_work（会话启动不会接力）")
    elif not re.search(r"^\s+check_pending_work\(", ptext, re.M):
        errors.append("check_pending_work 定义了但没在 main 里调用（定义即死代码）")
    if "pending_work" not in ptext:
        errors.append("session-start-prep 未读 pending_work 键")
    if "主动向用户提出继续" not in ptext:
        errors.append("session-start-prep 缺首会话强接力指令（relay 批次没人接）")
    dtext = doctor.read_text(encoding="utf-8")
    if "def check_init_completeness" not in dtext or "init_completeness" not in dtext:
        errors.append("workframe_doctor 缺 init_completeness（挂起批次失去常驻可见性）")
    if "mark_pending_work" not in skill.read_text(encoding="utf-8"):
        errors.append("launcher SKILL 未要求记批次（链条起点缺失，后三环永远等不到数据）")

    if errors:
        return _fail(name, "；".join(errors))
    return _ok(name, "(记批次 → 存状态 → 启动接力 → doctor 可见，四环在位)")


def check_module_init_template_contract():
    """module_init.py 与 modules-template 的双向契约（防模板与渲染器漂移）。

    1. basic-module / sub-module 模板的占位符集合 ⊆ 脚本替换字典键集——模板新增占位符
       而脚本不认识时，运行期渲染出 `{{XXX}}` 残留（脚本自身有断言，但那要等到有人跑，
       本闸把失败提前到 validate）
    2. 脚本内写死的两条「定位段引导行」必须与对应模板 overview 逐字一致——它们是
       positioning 替换的锚点，模板改文案而脚本没跟上时替换会静默退化为追加
    """
    name = "module_init_template_contract"
    script = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "module_init.py"
    tpl_root = FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "modules-template"
    if not script.is_file():
        return _fail(name, f"module_init.py 缺失: {script}")
    stext = script.read_text(encoding="utf-8")

    tpl_placeholders = set()
    for sub in ("basic-module", "sub-module"):
        d = tpl_root / sub
        if not d.is_dir():
            return _fail(name, f"模板目录缺失: {d}")
        for f in d.rglob("*"):
            if f.is_file():
                tpl_placeholders |= set(
                    re.findall(r"\{\{([A-Z_]+)\}\}", f.read_text(encoding="utf-8")))
    script_keys = set(re.findall(r'"([A-Z_]+)":', stext))
    unknown = tpl_placeholders - script_keys
    if unknown:
        return _fail(name, f"模板含脚本不认识的占位符: {sorted(unknown)}")

    anchor_specs = [
        ("BASIC_POSITIONING_PLACEHOLDER", tpl_root / "basic-module" / "overview.md"),
        ("SUB_POSITIONING_PLACEHOLDER", tpl_root / "sub-module" / "overview.md"),
    ]
    for const, tpl_file in anchor_specs:
        m = re.search(const + r'\s*=\s*\(\s*"([^"]+)"\s*\)', stext)
        if not m:
            return _fail(name, f"脚本缺少常量 {const}（或写法变了，本闸解析不到）")
        if m.group(1) not in tpl_file.read_text(encoding="utf-8"):
            return _fail(
                name,
                f"{const} 与 {tpl_file.name} 不一致——模板定位段文案改了，脚本锚点没跟上")
    return _ok(name, f"({len(tpl_placeholders)} 个占位符全覆盖 + 2 条定位锚点逐字一致)")


def check_prd_style_template_contract():
    """prd-style 项目框架模板契约（PRD 框架四层拆分的 ④ 层出厂件）。

    1. 模板存在且 frontmatter 有 name=prd-style + when_to_use——前者是 scaffold 装机
       实例化与 prd-writer fallback 的取件路径，后者决定装机后的路由准确性（装机产物
       是 CC 注册的可路由 skill，只有 description 时 routing 摇摆）
    2. 占位符集合 ⊆ 渲染已知键——模板新增占位符而渲染器不认识时，装机产物出现
       `{{XXX}}` 残留
    3. 结构锚点在位（§0 机器契约红线 / §1 工序形态声明 / §3 章节总览 / 变更与决策
       记录契约行）——prd-writer 六段骨架按节名读取工序形态，锚点改名即静默失联
    """
    name = "prd_style_template_contract"
    tpl = (FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "project-skills"
           / "prd-style" / "SKILL.md")
    if not tpl.is_file():
        return _fail(name, f"prd-style 模板缺失: {tpl}")
    text = tpl.read_text(encoding="utf-8")
    if not re.search(r"^name:\s*prd-style\s*$", text, re.M):
        return _fail(name, "模板 frontmatter 缺 `name: prd-style`")
    if not re.search(r"^when_to_use\s*:", text, re.M):
        return _fail(name, "模板 frontmatter 缺 `when_to_use`（装机后是可路由 skill，缺则 routing 摇摆）")
    allowed = {"PROJECT_NAME", "TODAY", "NOW_ISO"}
    found = set(re.findall(r"\{\{([A-Z_]+)\}\}", text))
    unknown = found - allowed
    if unknown:
        return _fail(name, f"模板含渲染不认识的占位符: {sorted(unknown)}")
    anchors = ["## §0 机器契约红线", "## §1 工序形态声明", "## §3 章节总览",
               "变更与决策记录"]
    missing = [a for a in anchors if a not in text]
    if missing:
        return _fail(name, f"模板结构锚点缺失: {missing}")
    # 4. 消费端接线对账——模板存在但没人取件，就是又一个「写完了从没被执行过」
    scaffold = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    stext = scaffold.read_text(encoding="utf-8") if scaffold.is_file() else ""
    if '"project-skills"' not in stext or '"prd-style"' not in stext:
        return _fail(name, "project_scaffold.py 未接线 prd-style 模板渲染（装机不会放入项目层）")
    writer = (FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "prd-writer" / "SKILL.md")
    wtext = writer.read_text(encoding="utf-8") if writer.is_file() else ""
    for ref in (".claude/skills/prd-style/SKILL.md",
                "templates/project-skills/prd-style/SKILL.md"):
        if ref not in wtext:
            return _fail(name, f"prd-writer SKILL 缺引用 {ref}（项目框架读取/fallback 断线）")
    return _ok(name, f"(frontmatter name+when_to_use + {len(found)} 个占位符合规 + "
                     f"{len(anchors)} 个结构锚点在位 + scaffold/prd-writer 两端接线)")


LAUNCHER_DIR = FRAMEWORK_ROOT / "plugins" / "workframe-launcher"
LAUNCHER_TEXT_SUFFIXES = {".md", ".json", ".py", ".yaml", ".yml"}


def _launcher_text_files():
    """launcher 内全部文本资产（机器闸的共同扫描面）。"""
    if not LAUNCHER_DIR.is_dir():
        return []
    return sorted(
        p for p in LAUNCHER_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in LAUNCHER_TEXT_SUFFIXES
    )


def check_launcher_plugin_structure():
    """workframe-launcher 插件骨架：plugin.json 合规 + setup skill frontmatter 完整。"""
    name = "launcher_plugin_structure"
    pj = LAUNCHER_DIR / ".claude-plugin" / "plugin.json"
    if not pj.exists():
        return _fail(name, f"plugin.json 缺失: {pj}")
    try:
        meta = json.loads(pj.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return _fail(name, f"plugin.json 解析失败: {e}")
    errors = []
    if meta.get("name") != "workframe-launcher":
        errors.append(f"plugin.json name={meta.get('name')!r}，应为 'workframe-launcher'")
    # kebab-case 是官方硬要求：非 kebab 会被 claude.ai 市场同步拒绝
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", str(meta.get("name", ""))):
        errors.append(f"plugin name 非 kebab-case: {meta.get('name')!r}")
    if not meta.get("version"):
        errors.append("plugin.json 缺 version（version 是用户获得更新的唯一信号）")

    skill = LAUNCHER_DIR / "skills" / "setup" / "SKILL.md"
    if not skill.exists():
        errors.append(f"setup skill 缺失: {skill}")
    else:
        text = skill.read_text(encoding="utf-8")
        fm = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not fm:
            errors.append("setup/SKILL.md 缺 frontmatter")
        else:
            block = fm.group(1)
            for field in ("name", "description", "when_to_use", "allowed-tools", "user-invocable"):
                if not re.search(rf"^{re.escape(field)}\s*:", block, re.M):
                    errors.append(f"setup/SKILL.md frontmatter 缺 {field}")
            if not re.search(r"^allowed-tools\s*:\s*\[", block, re.M):
                errors.append("setup/SKILL.md allowed-tools 必须是 YAML list")
    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name, f"(plugin {meta.get('version')} + setup skill)")


def check_launcher_no_cross_plugin_relative_path():
    """launcher 不得用 `../` 向上引用（含跨插件找 core）。

    官方契约：安装时插件按目录逐个复制到 `cache/<市场>/<插件>/<版本>/`，
    插件目录之外的文件不会被复制。launcher 旁边不存在 core——目录源（开发期）
    碰巧有兄弟目录、GitHub 源（用户侧）没有，该写法开发期能跑、发布后必坏。
    定位 core 只允许走 known_marketplaces.json 的 installLocation。
    """
    name = "launcher_no_cross_plugin_relative_path"
    hits = []
    launcher_root = LAUNCHER_DIR.resolve()

    def _judge(raw, f, lineno):
        """向上相对路径的两种失败：爬出插件根（发布后必坏）/ 插件内断链（引用了不存在的文件）。
        真正落在插件内且存在的相对引用（如 skills/setup 内的 ./reference/）放行。"""
        where = f"{f.relative_to(FRAMEWORK_ROOT)}:{lineno} → {raw}"
        if "$" in raw or "{" in raw:
            return f"{where}（运行时路径表达式含向上跳）"
        try:
            target = (f.parent / raw).resolve()
        except Exception:
            return f"{where}（路径无法解析）"
        if target != launcher_root and launcher_root not in target.parents:
            return f"{where}（爬出插件根）"
        if not target.exists():
            return f"{where}（插件内断链）"
        return None

    for f in _launcher_text_files():
        # Python 侧的等价逃逸写法：parents[N] 爬到插件根之外。
        # depth = 文件与插件根之间的目录层数；parents[depth] 恰为插件根（合法），
        # parents[depth+1] 起才算爬出。
        depth = len(f.relative_to(LAUNCHER_DIR).parts) - 1
        for lineno, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            # 只抓被反引号/引号/括号包裹、且 `../` 后确有路径内容的写法——
            # 裸 `../`（讲解禁令本身时会出现）不算违规。
            for m in re.finditer(r"[`'\"(]([^`'\"()\s]*\.\./[^`'\"()\s]+)", line):
                verdict = _judge(m.group(1), f, lineno)
                if verdict:
                    hits.append(verdict)
            if f.suffix.lower() == ".py":
                for m in re.finditer(r"parents\[(\d+)\]", line):
                    if int(m.group(1)) > depth:
                        hits.append(f"{f.relative_to(FRAMEWORK_ROOT)}:{lineno} → {m.group(0)}（爬出插件根）")
    if hits:
        return _fail(
            name,
            f"{len(hits)} 处向上相对路径（改走 known_marketplaces.installLocation）: "
            + "；".join(hits[:4]) + ("…" if len(hits) > 4 else ""),
        )
    return _ok(name, f"(scanned {len(_launcher_text_files())} files)")


def check_claude_md_merge_wired():
    """CLAUDE.md 整合链路三处必须都在，缺一整条链就断。

    这条链的失效方式极隐蔽：接入已有项目后文件在、hooks 在、agent 能路由，但框架的行为
    约定一条都没进去，而「文件存在」的检查发现不了。三处分别是——规范本体、launcher 在
    B 路径与执行步骤里引用它、doctor 查四个契约段。
    """
    name = "claude_md_merge_wired"
    guide = FRAMEWORK_ROOT / "plugins" / "core" / "reference" / "claude-md-merge-guide.md"
    skill = LAUNCHER_DIR / "skills" / "setup" / "SKILL.md"
    page = LAUNCHER_DIR / "skills" / "setup" / "reference" / "proposal-page.md"
    doctor = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    errors = []
    if not guide.exists():
        return _fail(name, f"整合规范缺失: {guide.relative_to(FRAMEWORK_ROOT)}")
    gtext = guide.read_text(encoding="utf-8")
    # 规范本体的四个不可省要素
    for token, desc in (("git ls-files", "备份判定的第一条命令"),
                        ("git status --porcelain", "备份判定的第二条命令"),
                        ("覆盖率必须 100%", "原文去向对账纪律"),
                        ("默认框架优先", "冲突裁决口径")):
        if token not in gtext:
            errors.append(f"整合规范缺{desc}（{token}）")
    if skill.exists():
        stext = skill.read_text(encoding="utf-8")
        if "claude-md-merge-guide" not in stext:
            errors.append("setup SKILL 未引用整合规范")
        if "CLAUDE.md.bak-" not in stext:
            errors.append("setup SKILL 执行步骤缺备份落点")
    else:
        errors.append("setup SKILL.md 缺失")
    if page.exists():
        if "原文去向对照表" not in page.read_text(encoding="utf-8"):
            errors.append("确认页规范未要求展示原文去向对照表")
    else:
        errors.append("proposal-page.md 缺失")
    if doctor.exists() and "def check_claude_md(" not in doctor.read_text(encoding="utf-8"):
        errors.append("doctor 无 check_claude_md（整合漏段查不出来）")
    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name)


def check_launcher_cli_contract():
    """launcher SKILL 里写死的脚本参数，必须在目标脚本的 argparse 里真实存在。

    setup SKILL 正文把命令行逐字写给了模型（`--params` / `--create-missing` / `--project` /
    `--group install`）。这些参数一旦被改名或删掉，现有的引用完整性检查只校验**路径**、
    不校验**参数**，于是要等 E2E 当天才炸。这里用 AST 取每个脚本 argparse 声明的参数集合对账。
    """
    name = "launcher_cli_contract"
    skill = LAUNCHER_DIR / "skills" / "setup" / "SKILL.md"
    if not skill.exists():
        return _fail(name, f"setup SKILL.md 缺失: {skill}")
    text = skill.read_text(encoding="utf-8")
    core = FRAMEWORK_ROOT / "plugins" / "core"

    def declared_args(script_path):
        """AST 取该脚本 add_argument 声明的全部长参数名。"""
        try:
            tree = ast.parse(script_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        found = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add_argument"):
                for a in node.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                            and a.value.startswith("--"):
                        found.add(a.value)
        return found

    errors, checked = [], 0
    for m in re.finditer(r"<CORE>/scripts/([\w.-]+\.py)([^\n`]*)", text):
        script = core / "scripts" / m.group(1)
        used = set(re.findall(r"(--[a-z][a-z-]*)", m.group(2)))
        if not used:
            continue
        if not script.exists():
            errors.append(f"SKILL 引用的脚本不存在: scripts/{m.group(1)}")
            continue
        declared = declared_args(script)
        if declared is None:
            errors.append(f"scripts/{m.group(1)} 解析失败")
            continue
        checked += len(used)
        for arg in sorted(used - declared):
            errors.append(f"scripts/{m.group(1)} 无参数 {arg}（SKILL 正文在用）")
    if errors:
        return _fail(name, "; ".join(errors[:5]))
    return _ok(name, f"({checked} 个参数与 argparse 声明对账一致)")


def check_launcher_entry_flow_guards():
    """入口分流的三条护栏——都是 E2E 实测撞出来的，没闸会悄悄退回去。

    1. **容器目录**这一档存在，且根级 `.git` / 项目清单一票否决它。
       早期草案用「子项有 ≥2 个独立项目标志」判容器，会把 monorepo 判成容器——
       而 monorepo 恰恰最需要「接入」，那不是不便是功能没了。
    2. 容器目录下**不出现「接入当前」**：把桌面本身变成 workframe 项目，
       会在容器里散出 projects/ logs/ .claude/ 一堆东西。
    3. **候选不从 cwd 推断业务**：实测在桌面发起时 Q2 三个候选全是从桌面某个代码目录
       推断的变体，用户只能自填才逃得出去——推断变成了默认值。
    """
    name = "launcher_entry_flow_guards"
    skill = LAUNCHER_DIR / "skills" / "setup" / "SKILL.md"
    if not skill.exists():
        return _fail(name, f"setup SKILL.md 缺失: {skill}")
    text = skill.read_text(encoding="utf-8")
    required = {
        "容器目录档位": "容器目录",
        "根级项目标志一票否决": "一票否决",
        "拿不准按项目处理兜底": "拿不准",
        "就地建 vs 在此处建的区分": "在此处建",
        "用户意图高于自动判定": "用户意图永远高于自动判定",
        "候选不从 cwd 推断业务": "不是从当前目录的内容派生",
        "cwd 候选须标注依据": "标注推断依据",
        "cwd 候选不作推荐项": "不作为推荐项",
        "Q3 候选须查路径占用": "存在且有实质内容",
        "A 路径带 --require-empty": "--require-empty",
        "必填字段来源写明": "必填字段从哪来",
        "one_line_goal 必须问用户": "必须问用户",
        "Q1 必须单独问": "Q1 必须单独问",
        "A 路径默认 git init": "git init + 首提交",
        "不写「看启动横幅」的错指引": "不要写「看启动横幅是否出现",
        "断点标记须增量记": "每步紧跟着记",
        "失败当场记不等末尾": "而不是等流程末尾统一写",
        # 市场源必须有确定的来源（2026-08-16 补）：定位脚本此前只输出 installLocation，
        # 而步骤 2 的 `<市场源>` 无任何取值说明——模型手上唯一现成的变量就是那个本地
        # 缓存路径，填进去就产出协作者装不上的项目，正是 README/quickstart/doctor
        # 三处反复告警的失败形态。
        "定位脚本输出市场源": "SOURCE=",
        "市场源不得用 installLocation 顶替": "不要用\n`CORE` 或 `installLocation` 顶替",
    }
    page = LAUNCHER_DIR / "skills" / "setup" / "reference" / "proposal-page.md"
    if page.exists():
        ptext = page.read_text(encoding="utf-8")
        for desc, token in (("确认页禁 HTML 标签", "禁止任何 HTML 标签"),
                            ("确认页一格一件事", "一个单元格只放一件事"),
                            ("动作清单含 git init 可选项", "`git init` 是可勾选项")):
            if token not in ptext:
                return _fail(name, f"proposal-page 缺{desc}")
    missing = [desc for desc, token in required.items() if token not in text]
    if missing:
        return _fail(name, "SKILL 缺护栏: " + "、".join(missing))
    return _ok(name, f"({len(required)} 条护栏在位)")


def check_launcher_reference_integrity():
    """launcher 的对外与对内引用都必须解析得到。

    两类：跨插件的 `plugins/core/...` 路径（防 core 侧搬家/改名漏改）、
    自身文档里的相对 markdown 链接（防插件内断链——安装后用户点不开）。
    """
    name = "launcher_reference_integrity"
    core_refs, missing = set(), []
    for f in _launcher_text_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"plugins/core/[\w./-]+", text):
            core_refs.add(m.group(0).rstrip("./-"))
        # launcher 正文用 <CORE>/xxx 占位表示 core 根（运行时由 known_marketplaces 解析）。
        # 不一并校验的话，core 侧改名搬家时这些引用会静默失效。
        for m in re.finditer(r"<CORE>/([\w./-]+)", text):
            core_refs.add("plugins/core/" + m.group(1).rstrip("./-"))
        # 相对 markdown 链接：排除 URL 与纯锚点
        for m in re.finditer(r"\]\((\.{1,2}/[^)#\s]+)", text):
            target = (f.parent / m.group(1)).resolve()
            if not target.exists():
                missing.append(f"{f.relative_to(FRAMEWORK_ROOT)} → {m.group(1)}（断链）")
    for ref in sorted(core_refs):
        if not (FRAMEWORK_ROOT / ref).exists():
            missing.append(f"{ref}（core 引用不存在）")
    if missing:
        return _fail(name, f"{len(missing)} 处引用解析失败: " + "；".join(missing[:5]))
    return _ok(name, f"({len(core_refs)} core refs + 相对链接全部可解析)")


def check_extract_assets_tag_unique():
    """extract_assets 的产物名必须逐文件唯一，且 inventory 要能账实对账。

    防回退对象（2026-08-16 实测）：tag 曾只取 basename 的 stem，`需求A/导出.docx` 与
    `需求B/导出.docx` 算出同一个 tag，后写的静默覆盖先写的——3 个源文件只落 1 份产物，
    而脚本仍打印「✅ 盘点完成：3 文件」、inventory 逐行列出全部 3 个。本工具用于**删源前**
    归档，账面说全量无损、磁盘已经丢了三分之二。
    """
    name = "extract_assets_tag_unique"
    p = (FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "requirement-archiving"
         / "scripts" / "extract_assets.py")
    if not p.exists():
        return _fail(name, "extract_assets.py 不存在")
    text = p.read_text(encoding="utf-8")
    if re.search(r"tag\s*=\s*safe_name\(os\.path\.splitext\(fn\)\[0\]\)", text):
        return _fail(name, "tag 又退回 basename stem（不含目录），同名文件会互相覆盖")
    if "os.path.splitext(rel)[0]" not in text:
        return _fail(name, "tag 未基于相对路径 rel 生成（跨目录同名会撞）")
    if "used_tags" not in text or "collisions" not in text:
        return _fail(name, "缺碰撞检测/告警（safe_name 有长度截断，深目录仍可能撞）")
    if "产物" not in text:
        return _fail(name, "inventory 缺「产物」列——账实无法对账，覆盖发生时看不出来")
    return _ok(name)


def check_hook_stage_count_consistent():
    """全仓「N 段 hook / N-stage hook」必须等于 hooks.json 的实际段数（正向断言）。

    取代「枚举历史错值」式的防漏。后者的结构性缺陷是**每升一版都要手工补一个新的历史值**，
    漏补即静默失守：`check_no_stale_skill_count` 当时枚举了 5/6/8/9 段与 six-stage/6-stage，
    唯独没有 `10-stage`，于是 marketplace.json 的 "a 10-stage hook pipeline" 在实际已经
    是 11 段之后又活了一版（2026-08-16 三方 review 才发现）。

    正向断言不需要预知错值：段数从 hooks.json 现算，文档里写几就得是几。
    CHANGELOG 是历史账，永久豁免。
    """
    name = "hook_stage_count_consistent"
    hooks_path = FRAMEWORK_ROOT / "plugins" / "core" / "hooks" / "hooks.json"
    try:
        n = len(json.loads(hooks_path.read_text(encoding="utf-8")).get("hooks", {}))
    except Exception as e:
        return _fail(name, f"读 hooks.json 失败: {e}")
    if n == 0:
        return _fail(name, "hooks.json 未解析出任何 hook 段")

    targets = [FRAMEWORK_ROOT / "README.md", FRAMEWORK_ROOT / ".claude-plugin" / "marketplace.json"]
    targets += sorted((FRAMEWORK_ROOT / "docs").glob("*.md"))
    targets += sorted((FRAMEWORK_ROOT / "plugins").rglob("*.md"))
    targets += sorted((FRAMEWORK_ROOT / "plugins").rglob("plugin.json"))
    pat = re.compile(r"(\d+)\s*段\s*hook|(\d+)\s*[-\s]\s*stage\s+hook|hook\s*(?:链路|pipeline)[^\n]{0,10}?(\d+)\s*段", re.I)
    offenders = []
    for p in targets:
        if p.name == "CHANGELOG.md" or not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in pat.finditer(line):
                val = next((g for g in m.groups() if g), None)
                if val and int(val) != n:
                    offenders.append(f"{p.relative_to(FRAMEWORK_ROOT)}:{i} 写 {val} 段（实际 {n}）")
    if offenders:
        return _fail(name, "; ".join(offenders[:5]))
    return _ok(name, f"(hooks.json 实际 {n} 段，全仓表述一致)")


def check_doc_model_id_not_recommended():
    """reference / templates 的文档不得把完整 model ID 当推荐写法。

    `check_agents_no_hardcoded_model_id` 只看 agents/*.md 的 frontmatter，管不到「教用户
    怎么写」的文档。2026-08-16 实测：role-customization-guide 同一份文件里，§Frontmatter
    示例注释写「或完整 ID 如 `model: claude-opus-4-8`」，§编写约束第 5 条却写「**不硬编码
    具体 model ID**（如 `model: claude-opus-4-8`）」——同一个例子一允许一禁止，而
    agent-template.md 采用的是被禁的那半，用户照模板建 agent 就会写出违规 frontmatter。
    """
    name = "doc_model_id_not_recommended"
    roots = [FRAMEWORK_ROOT / "plugins" / "core" / "reference",
             FRAMEWORK_ROOT / "plugins" / "core" / "templates"]
    # 只拦「推荐/允许」语境：`model: claude-xxx` 出现在同一行且不带否定词
    neg = ("不要", "不得", "禁止", "不硬编码", "别写", "随模型换代失效")
    offenders = []
    for root in roots:
        for p in sorted(root.rglob("*.md")):
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if not re.search(r"model:\s*`?claude-[a-z]+-[\d.]", line):
                    continue
                if any(k in line for k in neg):
                    continue
                offenders.append(f"{p.relative_to(FRAMEWORK_ROOT)}:{i}")
    if offenders:
        return _fail(name, "把完整 model ID 当推荐写法（应只用 opus/sonnet/haiku 别名）: "
                     + "; ".join(offenders[:5]))
    return _ok(name)


def check_event_ts_and_reason_contract():
    """事件 ts 必须 UTC+秒级；枚举型 reason 不得拼接详情。

    防回退对象（2026-08-16 实测）：
    - 同一份 events.jsonl 里并存 **4 种 ts 格式**（本地/UTC × 秒/微秒），同一事件类型
      跨 3 种。消费方（doctor 的 30 天窗口、audit 分组、memory-log 时间线）按**字符串**
      比较与排序，跨时区必然错序。
    - `session_ended.reason` 的回退值曾是 schema 枚举外的 `unknown`。
    - `summary_drift_repair_skipped.reason` 曾拼成 `import_failed: ImportError: ...`，
      而 schema 声明的是裸枚举——按等值匹配的消费方会把这些事件全部漏掉。

    本闸只钉**时区**这一条硬规则：跨时区的字符串比较必然错序，是真正的破坏源。
    精度（秒 vs 微秒）在同为 UTC 时只影响同一秒内的相对顺序，危害小得多，且
    `timespec` 是否出现难以静态断定（`_now()` 这类间接调用会假阳性），交由实跑与 review 保证。
    """
    name = "event_ts_and_reason_contract"
    scripts = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    offenders = []
    for py in sorted(scripts.glob("*.py")):
        text = py.read_text(encoding="utf-8")
        if "EVENTS_FILE" not in text:
            continue          # 不写事件流的脚本不受本闸约束
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            # 只在这一行确实是在造事件 ts 时才判；generated_at 之类人读时间戳不管
            if not (re.search(r'"ts"\s*:', line) or re.match(r"\s*ts\s*=", line)):
                continue
            if "datetime.now().astimezone()" in line:
                offenders.append(f"{py.name}:{i} 事件 ts 用了本地时区（须 timezone.utc）")
            # naive 时间戳同样致命：没有 tzinfo 的 isoformat() 产出 `2026-08-16T15:24:04`，
            # 消费方按 UTC 解释就整整差一个时区。
            # 这两条必须**并列判**——早先把 naive 检测写在「不含 astimezone 就 continue」
            # 之后，于是只有本地时区行才走得到它，而 naive 恰恰不含 astimezone：
            # 注入 `datetime.now().isoformat()` 反例时闸照样报绿（自查时实测）。
            elif re.search(r"datetime\.now\(\)\.isoformat\(", line):
                offenders.append(f"{py.name}:{i} 事件 ts 用了 naive 时间戳（须 timezone.utc）")
    ssp = (scripts / "session-start-prep.py").read_text(encoding="utf-8")
    if re.search(r'reason=f"[a-z_]+:', ssp):
        offenders.append("session-start-prep: reason 又拼接了详情（应拆成 reason + detail）")
    sef = (scripts / "session-end-flush.py").read_text(encoding="utf-8")
    if 'reason = "unknown"' in sef:
        offenders.append("session-end-flush: reason 回退值又用了 schema 枚举外的 unknown")
    # 光有 fallback 不够——外部传进来的值必须过白名单，否则 {"reason":"bogus"} 原样入库
    if "SESSION_END_REASONS" not in sef or "in SESSION_END_REASONS" not in sef:
        offenders.append("session-end-flush: reason 缺白名单校验（只兜缺失挡不住枚举外的输入值）")
    # 消费方一律解析后比较：字符串比 ts 在跨时区数据上必然错序
    doc = (scripts / "workframe_doctor.py").read_text(encoding="utf-8")
    if re.search(r'str\(ev\.get\("ts"[^)]*\)\)\s*[<>]', doc):
        offenders.append("workframe_doctor: 又出现按字符串比较事件 ts（须先 _parse_ts）")
    # 模型手写的事件占位符 `<ISO-8601>` 无法逐处静态断言格式，但必须有**一处**权威定义，
    # 否则模型只能凭默认习惯写（实测项目里模型写的迁移事件就是 +08:00）
    ap = (FRAMEWORK_ROOT / "plugins" / "core" / "rules" / "core"
          / "agent-protocols.md").read_text(encoding="utf-8")
    if "UTC + 秒级" not in ap or "timespec='seconds'" not in ap:
        offenders.append("agent-protocols: 缺 `<ISO-8601>` 的 UTC+秒级口径定义"
                         "（模型手写事件的唯一格式来源）")
    # 实跑探针：静态扫描只看得见**字面量所在行**，看不见 helper 间接生成。
    # check-stale-modules 的事件 ts 来自 now_iso()，把那个 helper 改回本地时间后，
    # 逐行正则完全无感——149 道闸照样全绿而事件重新写成 +08:00（Codex 2026-08-16 注入实证）。
    # 与其继续堆正则特例，不如直接调用 helper 看它真正产出什么。
    import subprocess
    helper_probes = [("check-stale-modules.py", "now_iso()")]
    for fname, call in helper_probes:
        f = scripts / fname
        if not f.exists():
            continue
        probe = (
            "import importlib.util,sys;"
            f"spec=importlib.util.spec_from_file_location('m',r'{f}');"
            "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
            f"print(m.{call})"
        )
        try:
            r = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=60)
        except Exception as e:
            offenders.append(f"{fname} 的 {call} 探针无法运行: {type(e).__name__}")
            continue
        got = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", got):
            offenders.append(f"{fname} 的 {call} 产出 {got!r}——须 UTC+秒级"
                             f"（形如 2026-08-16T07:24:04+00:00）")
    if offenders:
        return _fail(name, "; ".join(offenders[:6]))
    return _ok(name, "(UTC+秒级 ts / 裸枚举 reason / helper 实跑探针)")


def check_events_template_no_inline_registry():
    """events 模板不得内嵌事件注册表副本——`event-schema.json` 是唯一事实源。

    防回退对象（2026-08-16 三方 review 共同命中）：模板头部曾内嵌一份 `reliability`
    分层映射，落后 schema **10 个事件类型**（缺 skill_invoked / memory_migrated /
    turn_failed / config_changed / 6 个 modules_index_*）。它随 scaffold 复制成每个新项目
    events.jsonl 的第一行并长期留存，人工审计与未来消费者会读到错误元数据。
    模板 description 自己就写着 schema 是事实源，却又带一份会漂的副本。
    """
    name = "events_template_no_inline_registry"
    p = FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "events-template.jsonl"
    if not p.exists():
        return _fail(name, "events-template.jsonl 缺失")
    try:
        header = json.loads(p.read_text(encoding="utf-8").strip().splitlines()[0])
    except Exception as e:
        return _fail(name, f"模板首行不是合法 JSON: {e}")
    for key in ("reliability", "events", "reliability_tiers"):
        if key in header:
            return _fail(name, f"模板又内嵌了 `{key}` 注册表副本——改为只留 event-schema.json 指针")
    if "event-schema.json" not in header.get("description", ""):
        return _fail(name, "模板 description 未指向 event-schema.json")
    return _ok(name)


def check_board_summary_indent_agnostic():
    """board summary 重算必须认两种 YAML 序列缩进，且解析失败不得静默写 0。

    防回退对象（2026-08-16 实测）：tasks 块结束判据曾是「非空且无缩进」，而 YAML 允许
    序列项与父键同级（`tasks:` 换行后直接 `- id:`）——于是第一个任务条目就被当成块结束，
    total 算成 0 并把全 0 写回 summary，返回 status=**ok** 零告警，SessionStart 的
    drift check 因与它自洽也不报。board-template 的注释示例正是这种零缩进写法。

    真伪判据用实跑而非读代码：直接喂三种形态的 board.yaml 给函数。
    """
    name = "board_summary_indent_agnostic"
    scripts = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        import importlib
        mod = importlib.import_module("recompute_board_summary")
        importlib.reload(mod)
    except Exception as e:
        return _fail(name, f"import recompute_board_summary 失败: {e}")

    head = ("summary:\n  total: 0\n  pending: 0\n  in_progress: 0\n  pending_qa: 0\n"
            "  completed: 0\n  blocked: 0\n  cancelled: 0\n  last_updated: null\n\n")
    cases = {
        "zero-indent": head + "tasks:\n- id: T1\n  status: pending\n- id: T2\n  status: completed\n",
        "two-indent": head + "tasks:\n  - id: T1\n    status: pending\n  - id: T2\n    status: completed\n",
    }
    for label, text in cases.items():
        total, counts, unknown, n_entries = mod.count_task_statuses(text)
        if total != 2 or counts.get("pending") != 1 or counts.get("completed") != 1:
            return _fail(name, f"{label} 形态计数错误: total={total} counts={counts}")
        if n_entries != 2:
            return _fail(name, f"{label} 条目数错误: n_entries={n_entries}")
    # 空看板必须仍是合法的 0（不能误判成解析失败）
    t0, _, _, n0 = mod.count_task_statuses(head + "tasks: []\n")
    if t0 != 0 or n0 != 0:
        return _fail(name, f"空看板误判: total={t0} n_entries={n0}")
    # 有条目但 status 解析不出 → 必须能被上层识别为异常
    t1, _, _, n1 = mod.count_task_statuses(head + "tasks:\n- id: T1\n  stat_us: pending\n")
    if not (n1 == 1 and t1 == 0):
        return _fail(name, f"解析失败场景未被区分: total={t1} n_entries={n1}")
    return _ok(name, "(零缩进/两格缩进/空看板/解析失败 四形态实跑)")


def check_doctor_claude_md_section_scoped():
    """doctor 的 claude_md 必须对「角色体系」「路由规则」两段做**段内**检查。

    防回退对象（2026-08-16 实测）：这两段的 token 会被别的段落借用——`@角色名` 出现在
    「快速入门」与 `## 路由偏好` 段，`@dev` 出现在业务背景与流转表。只做全文 token 时，
    删掉整个 `## 路由规则` 段，doctor 仍报「四个框架契约段齐全」，主 Claude 由此静默
    失去「直做还是委派」的调度依据。

    另两段（状态流转 / 文档约定）**有意不做段内检查**：其 token 只在各自段内出现，
    且存量项目常把它们降级为三级标题挂在别的段下，钉二级段名会误伤合规项目。
    """
    name = "doctor_claude_md_section_scoped"
    p = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    text = p.read_text(encoding="utf-8")
    if "section_scoped" not in text:
        return _fail(name, "check_claude_md 缺 section_scoped 段内检查表")
    seg = text.split("section_scoped", 1)[1][:900]
    for label in ("角色体系", "路由规则"):
        if label not in seg:
            return _fail(name, f"section_scoped 未覆盖「{label}」段")
    if "@角色名" not in seg:
        return _fail(name, "路由规则段未钉 @角色名 token")
    if not re.search(r'startswith\(prefixes\)', text):
        return _fail(name, "段定位未使用前缀元组（存量项目改写标题会误伤）")
    return _ok(name, "(角色体系 + 路由规则 双段内检查在位)")


def check_scaffold_preflight_before_write():
    """scaffold 的参数校验必须发生在**写任何文件之前**。

    防回退对象（2026-08-16 实测）：`role_profile` 校验曾藏在 render_project_docs 内部，
    跑在 write_workframe_config 与 ensure_project_scaffold 之后——坏值留下 30 个文件的
    半成品 + 一份写坏的 config；`project_type` / `dormant_profile` 更是全无校验，
    坏值落盘且**退出码 0**（脚手架自称成功）。
    """
    name = "scaffold_preflight_before_write"
    p = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    text = p.read_text(encoding="utf-8")
    if "def preflight_params" not in text:
        return _fail(name, "缺 preflight_params()")
    # 只在 main() 函数体内比较**真实调用顺序**。
    # 早期写法是「preflight 之后还能找到写盘调用」——那只证明写盘发生在某个 preflight
    # 之后，挡不住有人在 preflight **之前**再插一次 write_workframe_config()：
    # 两个调用都在，闸照样绿（Codex 2026-08-16 注入反例实证）。
    m = re.search(r"\ndef main\(\):\n(.*?)(?=\ndef |\Z)", text, re.S)
    if not m:
        return _fail(name, "找不到 main() 函数体")
    body = m.group(1)
    i_pre = body.find("preflight_params(params)")
    i_cfg = body.find("write_workframe_config(")
    i_sc = body.find("ensure_project_scaffold(")
    if i_pre < 0:
        return _fail(name, "main() 未调用 preflight_params(params)")
    if i_cfg < 0 or i_sc < 0:
        return _fail(name, "main() 内未见 write_workframe_config / ensure_project_scaffold")
    if not (i_pre < i_cfg < i_sc):
        return _fail(name, f"main() 内调用顺序不对（preflight@{i_pre} / config@{i_cfg} / "
                           f"scaffold@{i_sc}）——参数校验必须早于任何写盘")
    return _ok(name, "(main() 内 preflight < config < scaffold 顺序断言)")


def check_config_enum_single_source():
    """scaffold 与 doctor 的 config 枚举必须一致（两处事实源的对账闸）。

    scaffold 写盘前校验、doctor 事后体检，判据必须同一套；漂了就会出现
    「scaffold 放行、doctor 报 error」或反之的撕裂。
    role_profile 不参与对账——它的权威在 role-profile-catalog.md，两边都向 catalog 求证。
    """
    name = "config_enum_single_source"
    sc = (FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py").read_text(encoding="utf-8")
    dc = (FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py").read_text(encoding="utf-8")

    def grab(text, field):
        m = re.search(rf'"{field}":\s*\(([^)]*)\)', text)
        return tuple(sorted(re.findall(r'"([^"]+)"', m.group(1)))) if m else None

    for field in ("project_type", "dormant_profile"):
        a, b = grab(sc, field), grab(dc, field)
        if a is None:
            return _fail(name, f"project_scaffold 缺 {field} 枚举")
        if b is None:
            return _fail(name, f"workframe_doctor 缺 {field} 枚举")
        if a != b:
            return _fail(name, f"{field} 枚举漂移: scaffold={a} vs doctor={b}")
    return _ok(name, "(project_type / dormant_profile 两处一致)")


def check_unreleased_changelog_numbers():
    """CHANGELOG 未发布段里的口径数字必须等于代码实际值。

    已发布段是历史账，写的是当时的状态，永久豁免——改它等于篡改历史。
    但**未发布段不是历史账**：它是下一版要交给用户的说明，数字应当随代码走。
    此前整个文件被豁免，于是「10 段 hook」「136 项检查」在实际已是 11 段 /
    152 项之后仍留在待发布内容里，靠人工比对才发现。

    未发布判据 = 段标题不含 ISO 日期（`## [Unreleased]` / `## [1.0.0] — 未发布`）。
    打 tag 当天把日期填上，该段自动转为历史账、不再受本闸约束——不需要改本函数。

    引号内的数字按引述处理不校验：讲「模板里删掉了『36 skills』这个数字」时，
    说的是被删掉的旧内容，不是对当前状态的断言。
    """
    name = "unreleased_changelog_numbers"
    path = FRAMEWORK_ROOT / "CHANGELOG.md"
    if not path.exists():
        return _fail(name, "CHANGELOG.md 不存在")

    hooks_path = FRAMEWORK_ROOT / "plugins" / "core" / "hooks" / "hooks.json"
    try:
        n_hooks = len(json.loads(hooks_path.read_text(encoding="utf-8")).get("hooks", {}))
    except Exception as e:
        return _fail(name, f"读 hooks.json 失败: {e}")
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    actual = {
        "hook 段数": n_hooks,
        "skills 数": len(REQUIRED_SKILLS),
        "validate 检查数": len(CHECKS),
        "agents 数": len(list(agents_dir.glob("*.md"))),
    }
    rules = [
        ("hook 段数", re.compile(r"(\d+)\s*段\s*hook\s*链路|(\d+)\s*[-\s]\s*stage\s+hook", re.I)),
        ("skills 数", re.compile(r"(\d+)\s*个\s*skills?\b|(\d+)\s+skills\b", re.I)),
        ("validate 检查数", re.compile(r"(\d+)\s*项[^。\n]{0,30}?检查")),
        ("agents 数", re.compile(r"(\d+)\s*个\s*(?:baseline\s*)?(?:角色|agents?)\b", re.I)),
    ]
    quoted = re.compile(r"「[^」]*」|『[^』]*』|`[^`]*`|\"[^\"]*\"")

    offenders = []
    unreleased_titles = []
    in_unreleased = False
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("## ["):
            in_unreleased = not re.search(r"\d{4}-\d{2}-\d{2}", line)
            if in_unreleased:
                unreleased_titles.append(line.strip())
            continue
        if not in_unreleased:
            continue
        bare = quoted.sub(" ", line)
        for label, pat in rules:
            for m in pat.finditer(bare):
                got = int(next(g for g in m.groups() if g))
                if got != actual[label]:
                    offenders.append(f"{path.name}:{i} {label} 写 {got}，实际 {actual[label]}")
    if offenders:
        return _fail(name, "; ".join(offenders))
    if not unreleased_titles:
        return _ok(name, "(无未发布段——全部已打日期，按历史账豁免)")
    return _ok(name, f"({len(unreleased_titles)} 个未发布段的数字与代码一致)")


def check_frontmatter_block_scalar_indent():
    """frontmatter 里的块标量（`key: |`）内容行必须保持缩进，不得断裂。

    2026-08-17 实例：`competitive-analysis/SKILL.md` 的 `when_to_use: |` 块里有一行
    缩进为 0。YAML 解析器认为块标量到此终止，再把该行当新 key 解析 → 整份 frontmatter
    解析失败。官方 `claude plugin validate` 的原话是「At runtime this skill loads with
    empty metadata (all frontmatter fields silently dropped)」——**name / description /
    when_to_use / allowed-tools 全部静默丢弃**，该 skill 在模型侧彻底不可见（实测当日
    会话的 skill 列表里确实没有它），而本仓当时 153 项检查全绿。

    这类缺陷的特征是「文件看着完全正常、功能整个消失、任何自有检查都不报」，
    正是收口检查里最该堵的形状。

    **覆盖边界**（不做完整 YAML 解析——本仓零依赖，不引 PyYAML）：只检块标量缩进断裂
    这一类，因为它最易踩、后果最重（整份元数据丢弃）。其他 YAML 语法错误（重复 key、
    错误的引号嵌套、非法 tag）不在覆盖内，发布前仍应跑一次 `claude plugin validate`
    作为官方兜底（已列入 dev-docs 的发布前人工终检清单）。
    """
    name = "frontmatter_block_scalar_indent"
    targets = sorted((FRAMEWORK_ROOT / "plugins").rglob("SKILL.md"))
    targets += sorted((FRAMEWORK_ROOT / "plugins" / "core" / "agents").glob("*.md"))
    key_re = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*([|>][-+\d]*)?\s*$")
    plain_key_re = re.compile(r"^[A-Za-z_][\w-]*\s*:")
    offenders = []
    for p in targets:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            offenders.append(f"{p.name}: 读取失败 {e}")
            continue
        if not lines or lines[0].strip() != "---":
            continue
        try:
            end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        except StopIteration:
            offenders.append(f"{p.relative_to(FRAMEWORK_ROOT)}: frontmatter 未闭合")
            continue
        in_block, block_key, block_line = False, None, 0
        for i in range(1, end):
            line = lines[i]
            if in_block:
                if not line.strip():
                    continue
                if len(line) - len(line.lstrip()) == 0:
                    if plain_key_re.match(line):
                        in_block = False  # 块标量正常结束于下一个顶层 key
                    else:
                        offenders.append(
                            f"{p.relative_to(FRAMEWORK_ROOT)}:{i + 1} 块标量 `{block_key}:`"
                            f"（第 {block_line} 行起）的内容行缩进为 0，YAML 会在此断开并把它当新 key"
                        )
                        in_block = False
                else:
                    continue
            if not in_block:
                m = key_re.match(line)
                if m and m.group(2):
                    in_block, block_key, block_line = True, m.group(1), i + 1
    if offenders:
        return _fail(name, "; ".join(offenders[:5]))
    return _ok(name, f"扫描 {len(targets)} 份 frontmatter，块标量缩进完好")


CHECKS = [
    # Structure
    check_marketplace_json_parseable,
    check_marketplace_lists_both_plugins,
    check_plugin_json_parseable,
    check_hooks_json_parseable,
    check_agents_count_and_frontmatter,
    check_skills_count_and_frontmatter,
    check_core_shared_assets_complete,
    check_rules_core_exists,
    check_scripts_syntax,
    # Reference boundary
    check_hooks_commands_reference_plugin_root,
    check_no_hardcoded_user_paths,
    check_core_assets_no_repo_internal_path,
    check_plugins_no_user_docs_path,
    check_retired_terms_absent,
    check_gitignore_marker_dual_match,
    check_pending_work_wired,
    check_module_init_template_contract,
    check_prd_style_template_contract,
    # Docs completeness
    check_docs_index_complete,
    check_root_readme_changelog_license,
    # Content ownership
    check_no_reference_content_in_docs,
    check_no_dev_docs_content_leaked_to_user_docs,
    # v8.2 — Compliance + v8.2 infrastructure
    check_no_forbidden_frontmatter_fields,
    check_agents_no_hardcoded_model_id,
    check_system_skills_sidecar,
    check_maintenance_skills_disable_model_invocation,
    check_hooks_complete_pipeline,
    check_new_hook_scripts_exist,
    check_skill_metrics_recompute_is_deterministic,
    check_workframe_state_templates_exist,
    check_activity_state_has_dormant_profile,
    check_self_iteration_confidence_formula,
    check_eval_cases_exist,
    check_shared_agent_contract,
    # v0.2.1
    check_version_consistency,
    check_event_registry_consistency,
    check_slash_namespace_consistency,
    check_pending_maintenance_schema_documented,
    check_activity_state_wake_up_pending,
    # v0.2.2 — Agent 边界与去耦合
    check_agents_no_business_skill_in_body,
    check_agents_no_dispatch_language,
    check_agents_no_memory_frontmatter,
    check_agents_no_inline_protocol,
    check_agents_no_response_output_duplication,
    check_agents_no_protected_assets_duplication,
    check_agents_no_project_specific_path_hardcoding,
    check_agents_no_deep_spec_path,
    # v0.2.2-fixup — Codex 二轮 review
    check_domain_skills_have_description,
    # v0.2.2-fixup-2 — Codex 三轮 review
    check_model_mediated_events_have_append_samples,
    check_memory_activity_events_have_snapshot_fields,
    # v0.2.2-fixup-3 — Codex 四轮 review
    check_windows_cmd_wrapper_portable,
    check_no_proposal_failed_as_core_trigger,
    check_create_project_reference_current_counts,
    check_rules_sync_docs_current_rule_count,
    check_session_start_no_false_summary_creation_claim,
    # v0.2.2-fixup-4 — agent 多项目适配
    check_agents_no_plugin_internal_path,
    # v0.2.3 — Role Profile Lite
    check_role_profile_catalog_exists,
    check_claude_md_template_has_role_profile_placeholder,
    check_role_profile_field_documented,
    check_scaffold_user_fields_never_overwritten,
    # v0.2.3-fixup-1 — Codex Role Profile Lite review 发现的口径残留
    check_no_stale_reference_count,
    check_scaffold_framework_version_dynamic,
    # v0.2.3 Phase A-lite — skill gap routing 准确性
    check_no_stale_skill_count,
    check_role_count_wording_matches_agents,
    check_signoff_table_alignment,
    # v1.0.0 审查修复批次 — I-040 防回退（skills 禁 ${CLAUDE_PLUGIN_ROOT}）
    check_no_plugin_root_env_in_skills,
    # v0.2.x 发布前一致性扫描
    check_release_consistency,
    # v0.2.x self-iteration fixes
    check_self_iteration_allowed_tools_complete,
    check_self_iteration_score_formula_uses_risk_penalty,
    check_self_iteration_proposal_schema_complete,
    check_eval_case_03_no_stale_problem_score,
    check_proposal_applied_event_sample_complete,
    check_proposal_verified_event_sample_complete,
    check_proposal_failed_event_sample_complete,

    # v0.2.x event chain & boundary fixes
    check_event_samples_have_required_schema_fields,
    check_agent_protocols_documents_success_false_and_session_id,
    check_test_case_design_appends_task_blocked,
    check_self_iteration_no_proposals_kinds_match_trigger_script,
    check_pm_skills_do_not_directly_write_board,

    # Codex-round-N follow-ups
    check_audit_board_drift_bin_exists_and_compiles,
    check_doctor_all_checks_run_clean,
    check_doctor_readonly_on_probe_project,
    check_text_writes_pin_newline,
    check_utf8_stream_wrap_symmetric,
    check_task_blocked_producer_policy_consistent,
    check_iteration_baseline_code_derived,
    check_doctor_smoke,
    check_validate_self_no_duplicates,
    check_event_reliability_enum,
    check_event_types_registered,
    check_scaffold_gitkeep_dirs_covered,
    check_activity_defaults_match_template,
    check_no_drifted_literals,
    check_state_io_concurrency,
    check_state_io_fail_closed,
    check_state_io_single_source,
    check_setup_state_steps_wired,
    check_doctor_install_group_contract,
    check_doctor_thresholds_match_trigger,
    check_memory_ask_smoke,
    check_maintenance_workorder_smoke,
    check_notes_entry_count_sync,
    check_write_time_audn_present,
    check_librarian_placement_has_skill_row,
    check_librarian_placement_has_automemory_row,
    check_rollback_supports_v2_targets_array,

    # Rules deep-audit follow-ups
    check_response_output_confirmation_rules_not_conflicting,
    check_auto_update_p0_example_confirm_before_write,
    check_auto_update_no_prompt_eng_skill_edit_claim,
    check_task_blocked_producer_schema_matches_protocol,
    check_auto_update_protected_assets_complete,

    # projects/ 框架契约修订（Codex 交叉评审落地）
    check_scaffold_has_ensure_project_scaffold,
    check_scaffold_templates_exist,
    check_issues_template_has_attribution_fields,
    check_session_digest_no_end_of_session_writeback,
    check_agent_protocols_has_rule_processing_order,
    check_gitignore_template_has_required_entries,
    check_board_template_matches_task_management_schema,

    # 跨平台兼容性（Codex P0/P1 落地）
    check_workframe_python_launcher_exists,
    check_hooks_use_workframe_python_launcher,
    check_bin_git_index_modes,
    check_all_bin_cmd_wrappers_portable,
    check_bin_gitattributes_eol,
    check_readme_check_count_current,
    check_no_case_only_filename_collisions,
    check_hook_scripts_no_blocking_exit,
    check_no_gnu_only_shell_syntax,

    # M1 P0 兜底（Codex 二轮 + 项目级 custom role 完整支持）
    check_scaffold_creates_baseline_role_memory,
    check_scaffold_workframe_config_merge_mode,
    check_agent_protocols_step2_has_init_fallback,
    check_log_subagent_activity_no_hardcoded_known_agents,
    check_role_customization_guide_has_protocol_contract_section,

    # M3 modules/ 体系（Codex 4 轮 + D1/D2 决策 + 5 轮 fix-1~10）
    check_postool_use_hook_exists,
    check_postool_use_hook_script_exists,
    check_modules_system_skills_registered,
    check_check_stale_modules_unit_tests_pass,
    check_modules_no_legacy_display_name_or_slug,
    check_modules_template_user_input_fields_quoted,
    # v0.4.x 需求层去版本化 + 子需求结构
    check_modules_no_legacy_v_dir_or_versions_timeline,
    check_modules_no_legacy_req_type,
    # 初始化链路重构：scaffold 参数化契约 + workframe-launcher 骨架与依赖方向机器闸
    check_core_reference_links_resolve,
    check_sync_rules_project_arg_and_single_impl,
    check_scaffold_params_interface,
    check_launcher_plugin_structure,
    check_launcher_no_cross_plugin_relative_path,
    check_launcher_reference_integrity,
    check_launcher_cli_contract,
    check_launcher_entry_flow_guards,
    check_claude_md_merge_wired,
    # 三方 review 收口（2026-08-16）：静默丢数据 / 静默放行 / 假成功三类的防回退闸
    check_extract_assets_tag_unique,
    check_hook_stage_count_consistent,
    check_doc_model_id_not_recommended,
    check_event_ts_and_reason_contract,
    check_events_template_no_inline_registry,
    check_board_summary_indent_agnostic,
    check_doctor_claude_md_section_scoped,
    check_scaffold_preflight_before_write,
    check_config_enum_single_source,
    check_unreleased_changelog_numbers,
    check_frontmatter_block_scalar_indent,
]


def main():
    # Windows UTF-8 stdout/stderr
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

    print(f"Validating framework at: {FRAMEWORK_ROOT}")
    print()

    failures = []
    for check in CHECKS:
        try:
            ok, msg = check()
        except Exception as e:
            # 检查自身抛异常必须降级为该项失败，不能中断整轮：资产被移动或删除时，
            # 首个异常会吞掉其后所有检查，恰在改动最大时失去全部质量反馈。
            # 定位取本文件内最深的一帧：末帧通常落在标准库（如 pathlib 的 open），
            # 对排查无用——要的是哪个检查的哪一行触发。
            tb = traceback.extract_tb(e.__traceback__)
            own = [f for f in tb if Path(f.filename).name == Path(__file__).name]
            frame = (own or tb)[-1] if tb else None
            where = f"{Path(frame.filename).name}:{frame.lineno}" if frame else "?"
            ok = False
            msg = f"EXCEPTION {type(e).__name__} at {where}: {str(e)[:120]}"
            print(f"{FAIL} {check.__name__.replace('check_', '', 1)}: {msg}")
        if not ok:
            failures.append((check.__name__, msg))

    print()
    if failures:
        print(f"{len(failures)} check(s) failed out of {len(CHECKS)}.")
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    print(f"All {len(CHECKS)} checks passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
