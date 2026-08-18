#!/usr/bin/env python3
"""
module_init.py — modules/ 体系 basic / sub 两层骨架的确定性落盘（机械部分脚本化）。

设计动机（2026-08-11，源文档处理进装机流程）：
    接入存量项目时要在装机会话内建出**合规**的模块树，而 module-init 此前是纯 SKILL——
    建一个 sub 要落多类产物 + 回写两处索引段 + 反向索引，模型手搓必是半成品。
    本脚本承接其中的机械部分；语义部分（命名、证据、切分判断、requirement 层）仍归
    SKILL / 模型。与 `project_scaffold.py` 同族：模板渲染由脚本保证并断言占位符零残留。

用法：
    # 建树（幂等：已存在文件一律跳过；收尾自动重建两层索引段）
    python module_init.py --project "<dir>" --params "<tree.json>"

    # 仅重建索引段（global basic-modules-index + 各 basic 的 submodules-index）
    python module_init.py --project "<dir>" --refresh-index [--basic "<name>"]

params JSON（launcher setup / module-init SKILL 采集对话后写出）：
    {
      "basics": [
        { "name": "催收策略",
          "owner": "pm",                       # 可选，默认 pm
          "positioning": "一句话定位",          # 可选；写进 overview 定位段并成为索引摘要
          "subs": [
            { "name": "策略生成",
              "owner": "pm",                   # 可选
              "positioning": "一句话定位",      # 可选
              "code_paths": ["src/strategy/**"] # 可选；非空时初始化反向索引
            } ] } ] }

职责边界：
    - 只建 basic / sub 两层；requirement 资产包归 module-init SKILL（转写阶段的产物）
    - 索引段只管 global `basic-modules-index` 与 basic `submodules-index` 两类匿名段，
      生成规则与 module-index-refresh SKILL Step 2/3 对齐（该 SKILL 在这两层改调本脚本，
      需求层各段仍归 SKILL）——单一实现，防两处漂移
    - 反向索引不重新实现：subprocess 调 `check-stale-modules.py init-submodule`
      （复用其文件锁 + 原子写），经 CLAUDE_PROJECT_DIR 环境变量定位项目

退出码：0 成功；2 参数 / 校验错误（ParamError）；1 插件不完整（InstallError）或其他异常。
"""

import argparse
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "templates" / "modules-template"
CHECK_STALE = PLUGIN_ROOT / "scripts" / "check-stale-modules.py"

# 名称 3 条 OS 硬约束（与 module-init SKILL §输入 对齐）
FORBIDDEN_CHARS = set('/\\<>:"|?*')

# 模板「定位」段的引导占位行（positioning 提供时被替换；模板改文案须同步此处，
# 有 check_module_init_template_contract 闸对账）。
# 形态是 HTML 注释而非 blockquote：没人填写时它不渲染，不会以「这一段由人写」的面目
# 出现在交付文档正文里（引导语被当成正文留在 PRD 里，实测发生过）。
BASIC_POSITIONING_PLACEHOLDER = (
    "<!-- 基础模块的领域定位、核心责任、与其他基础模块的边界。**这一段由人写**，是稳定信息。 -->"
)
SUB_POSITIONING_PLACEHOLDER = (
    "<!-- 子模块的功能定位 / 用户价值 / 与父基础模块的关系。**这一段由人写**，反映稳定信息。 -->"
)

# 匿名机器维护段边界（global / basic overview 各只有一段；具名段不归本脚本管）
IDX_START = "<!-- WORKFRAME:AUTO-INDEX:START -->"
IDX_END = "<!-- WORKFRAME:AUTO-INDEX:END -->"
IDX_PATTERN = re.compile(
    re.escape(IDX_START) + r"[\s\S]*?" + re.escape(IDX_END)
)


class ParamError(Exception):
    """参数缺失 / 校验失败——必须让调用方当场看见，不静默降级。退出码 2。"""


class InstallError(Exception):
    """插件自身不完整（模板目录缺失等）——不是用户输入的问题。退出码 1，
    与 module-init SKILL 处置表的「确认 plugin-root.txt 指向的插件根有效」对齐。"""


# ---------------------------------------------------------------- 基础工具


def _now_iso():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _read_text(path: Path) -> str:
    # utf-8-sig：Windows 记事本与 PowerShell 5.1 的 `Out-File -Encoding utf8` 都会写 BOM，
    # 纯 utf-8 解码会把用户手搓的 params.json 当成语法错误顶回去。
    return path.read_bytes().decode("utf-8-sig")


def _write_text(path: Path, content: str):
    """统一 LF 写盘（Windows 上 io.open('w') 会把 LF 转 CRLF 造成整文件重写）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


def _validate_name(name, label):
    if not isinstance(name, str) or not name:
        raise ParamError(f"{label} 的 name 缺失或为空")
    if name != name.strip():
        raise ParamError(f"{label} 「{name}」首尾不能有空格")
    if any(c.isspace() for c in name):
        # 内部空格此前一路放行到 stale 解析，那里按 \S+ 切分会把名字截断成半截，
        # 结果 code_paths 反查永远命中不了这个子模块（模板 README 的字符白名单
        # 本就不含空格）
        raise ParamError(f"{label} 「{name}」不能含空格（含中间）——"
                         f"stale 索引按空白切分，带空格的名字会被截断")
    bad = sorted(set(name) & FORBIDDEN_CHARS)
    if bad:
        raise ParamError(f"{label} 「{name}」含禁用字符：{' '.join(bad)}")
    if name in (".", ".."):
        raise ParamError(f"{label} 「{name}」不是合法目录名")


def _flat_yaml(path: Path) -> dict:
    """读顶层 `key: value` 平铺字段（本脚本只需 name/status/notes，不引第三方库）。"""
    data = {}
    if not path.exists():
        return data
    for line in _read_text(path).splitlines():
        if not line or line.startswith((" ", "\t", "#", "-")):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*?)\s*(?:#.*)?$", line)
        if m:
            val = m.group(2).strip().strip('"').strip("'")
            data[m.group(1)] = val
    return data


# ---------------------------------------------------------------- params 校验


def load_params(params_path: Path) -> list:
    """校验模块树参数。

    结构不合法一律转 ParamError（退出码 2 = 「改输入再重跑」）。此前只校验字段值，
    顶层或元素类型不对时会一路抛到 AttributeError——退出码 1，而处置表把 1 解释成
    「插件根无效」，等于把用户支到完全无关的方向去排查。
    """
    try:
        data = json.loads(_read_text(params_path))
    except Exception as e:
        raise ParamError(f"params 文件读取失败：{e}")
    if not isinstance(data, dict):
        raise ParamError(f"params 顶层必须是 JSON 对象，实际是 {type(data).__name__}")
    basics = data.get("basics")
    if not isinstance(basics, list) or not basics:
        raise ParamError("params 缺少非空的 basics 数组")
    seen_basic = set()
    for i, b in enumerate(basics):
        if not isinstance(b, dict):
            raise ParamError(f"basics[{i}] 必须是对象，实际是 {type(b).__name__}")
        _validate_name(b.get("name"), "基础模块")
        if b["name"] in seen_basic:
            raise ParamError(f"基础模块重名：「{b['name']}」")
        seen_basic.add(b["name"])
        seen_sub = set()
        subs = b.get("subs") or []
        if not isinstance(subs, list):
            raise ParamError(f"「{b['name']}」的 subs 必须是数组，实际是 {type(subs).__name__}")
        for j, s in enumerate(subs):
            if not isinstance(s, dict):
                raise ParamError(
                    f"「{b['name']}」的 subs[{j}] 必须是对象，实际是 {type(s).__name__}")
            _validate_name(s.get("name"), f"「{b['name']}」的子模块")
            if s["name"] in seen_sub:
                raise ParamError(f"「{b['name']}」下子模块重名：「{s['name']}」")
            seen_sub.add(s["name"])
            cp = s.get("code_paths", []) or []
            if not isinstance(cp, list) or any(not isinstance(x, str) or not x for x in cp):
                raise ParamError(f"「{b['name']}/{s['name']}」的 code_paths 必须是非空字符串数组")
    return basics


# ---------------------------------------------------------------- 模板渲染


def _render_tree_files(src_dir: Path, dst_dir: Path, mapping: dict,
                       positioning, placeholder_line,
                       created, skipped):
    """复制模板目录 → 目标目录，逐文件替换占位符并断言零残留。已存在文件跳过。

    先确认模板目录在：`rglob` 对不存在的目录返回空迭代器而不报错，于是模板缺失时
    这里一个文件都不复制、脚本照常刷索引并 exit 0——用户拿到「成功」，模块却没建出来
    。
    """
    if not src_dir.is_dir():
        raise InstallError(
            f"模板目录不存在：{src_dir}\n"
            f"  插件安装不完整或模板被删。确认 .claude/workframe-state/plugin-root.txt "
            f"指向的插件根有效（重装 core 插件可修复），再重跑。")
    for src in sorted(src_dir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(src_dir)
        dst = dst_dir / rel
        if dst.exists():
            skipped.append(str(dst))
            continue
        if src.name == ".gitkeep":
            _write_text(dst, "")
            created.append(str(dst))
            continue
        content = _read_text(src)
        for key, val in mapping.items():
            content = content.replace("{{" + key + "}}", val)
        residue = re.findall(r"\{\{[A-Z_]+\}\}", content)
        if residue:
            raise ParamError(
                f"模板渲染残留占位符 {sorted(set(residue))}：{src}（模板新增了占位符而本脚本不认识）"
            )
        if positioning and rel.name == "overview.md" and rel.parent == Path("."):
            if placeholder_line in content:
                content = content.replace(placeholder_line, positioning.strip(), 1)
            else:
                content = content.replace(
                    "## 定位\n", "## 定位\n\n" + positioning.strip() + "\n", 1)
        created.append(str(dst))
        _write_text(dst, content)


def _apply_code_paths(sub_yaml: Path, code_paths):
    """把 code_paths 写进 submodule.yaml。返回 'written' / 'same' / 'different'。

    仅在字段仍为空数组时写入；已有值时与目标值比对——同值静默（幂等重跑的正常态），
    异值才值得警告（用户改过配置，脚本不覆盖）。"""
    content = _read_text(sub_yaml)
    # 用 `\[\s*\]` 而不是 `\[\]`：用户把模板的 `code_paths: []` 手改成 `code_paths: [ ]`
    # （中间带空格）时原正则匹配不上，脚本会当成「已有值」去比对，报一条莫名其妙的
    # 「与 params 不一致」警告
    if re.search(r"^code_paths:\s*\[\s*\]", content, re.M) is None:
        existing = re.findall(r"^  - (.+?)\s*$", _code_paths_block(content), re.M)
        return "same" if existing == list(code_paths) else "different"
    rendered = "code_paths:\n" + "".join(f"  - {p}\n" for p in code_paths)
    content = re.sub(r"^code_paths:\s*\[\s*\]\s*$", rendered.rstrip("\n"), content, count=1, flags=re.M)
    _write_text(sub_yaml, content)
    return "written"


def _code_paths_block(content: str) -> str:
    """截取 submodule.yaml 中 code_paths 字段自身的列表行（到下一个顶层键为止）。"""
    m = re.search(r"^code_paths:\s*$([\s\S]*?)(?=^\S)", content, re.M)
    return m.group(1) if m else ""


def create_tree(project_dir: Path, basics: list):
    modules_dir = project_dir / "projects" / "modules"
    created, skipped, warnings = [], [], []
    now_iso, today = _now_iso(), _today()

    _ensure_global_overview(project_dir, created)

    for b in sorted(basics, key=lambda x: x["name"]):
        b_dir = modules_dir / b["name"]
        mapping = {
            "BASIC_NAME": b["name"],
            "OWNER_ROLE": b.get("owner") or "pm",
            "TODAY": today,
            "NOW_ISO": now_iso,
        }
        _render_tree_files(TEMPLATES_DIR / "basic-module", b_dir, mapping,
                           b.get("positioning"), BASIC_POSITIONING_PLACEHOLDER,
                           created, skipped)

        for s in sorted(b.get("subs", []) or [], key=lambda x: x["name"]):
            s_dir = b_dir / s["name"]
            s_mapping = {
                "BASIC_NAME": b["name"],
                "SUB_NAME": s["name"],
                "MODULE_PATH": f"{b['name']}/{s['name']}",
                "OWNER_ROLE": s.get("owner") or b.get("owner") or "pm",
                "TODAY": today,
                "NOW_ISO": now_iso,
            }
            _render_tree_files(TEMPLATES_DIR / "sub-module", s_dir, s_mapping,
                               s.get("positioning"), SUB_POSITIONING_PLACEHOLDER,
                               created, skipped)
            code_paths = s.get("code_paths", []) or []
            if code_paths:
                result = _apply_code_paths(s_dir / "submodule.yaml", code_paths)
                if result == "written":
                    ok, msg = _init_reverse_index(project_dir, f"{b['name']}/{s['name']}")
                    if not ok:
                        warnings.append(msg)
                elif result == "different":
                    warnings.append(
                        f"{b['name']}/{s['name']}: submodule.yaml 的 code_paths 与 params 不一致，"
                        f"以文件现值为准未覆盖")

    idx_report = refresh_index(project_dir, only_basic=None)
    return created, skipped, warnings, idx_report


def _ensure_global_overview(project_dir: Path, created):
    """global overview 缺失时（scaffold 未带 --params 的老项目）从模板补建。"""
    target = project_dir / "projects" / "modules" / "overview.md"
    if target.exists():
        return
    tpl = TEMPLATES_DIR.parent / "modules-template" / "overview-template.md"
    if not tpl.exists():
        raise ParamError(f"overview-template.md missing: {tpl}")
    cfg = {}
    cfg_path = project_dir / ".workframe-config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(_read_text(cfg_path))
        except Exception:
            pass
    content = _read_text(tpl)
    for key, val in {
        "PROJECT_NAME": cfg.get("project_name") or project_dir.name,
        "TODAY": _today(),
        "NOW_ISO": _now_iso(),
    }.items():
        content = content.replace("{{" + key + "}}", val)
    _write_text(target, content)
    created.append(str(target))


def _init_reverse_index(project_dir: Path, module_path: str):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project_dir))
    try:
        r = subprocess.run(
            [sys.executable, str(CHECK_STALE), "init-submodule", module_path],
            env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        if r.returncode != 0:
            return False, (f"{module_path}: 反向索引初始化失败（exit {r.returncode}）——"
                           f"事后可手动 `python check-stale-modules.py rebuild-index` 兜底")
        return True, ""
    except Exception as e:
        return False, f"{module_path}: 反向索引初始化异常 {e}——事后 rebuild-index 兜底"


# ---------------------------------------------------------------- 索引段重建


def _clean_summary(text: str) -> str:
    """摘要清洗：链接展开为显示文本、去粗体、竖线换全角——脏字符进表格会破列，
    截断切穿 wikilink 会坏渲染（2026-08-11 真实项目树沙盒实测抓出）。"""
    text = re.sub(r"\[\[([^\]|]+?)(?:\|([^\]]+?))?\]\]",
                  lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return text.replace("**", "").replace("|", "／").strip()


def _positioning_summary(overview_path: Path, yaml_fallback: dict) -> str:
    """摘要提取（与 module-index-refresh SKILL 对齐）：
    定位段首句 → frontmatter/description 或 yaml notes → '-'；清洗后 ≤80 字符。"""
    text = ""
    if overview_path.exists():
        lines = _read_text(overview_path).splitlines()
        try:
            i = next(n for n, l in enumerate(lines) if l.strip() == "## 定位")
            for l in lines[i + 1:]:
                s = l.strip()
                if not s:
                    continue
                if s.startswith((">", "#", "```")):
                    break  # 引导占位 blockquote = 尚无人写定位
                text = s
                break
        except StopIteration:
            pass
    if not text:
        text = yaml_fallback.get("notes") or yaml_fallback.get("description") or ""
    if not text:
        return "-"
    # 取首句
    for sep in ("。", "；", "\n"):
        if sep in text:
            text = text.split(sep)[0] + ("。" if sep == "。" else "")
            break
    text = _clean_summary(text)
    return text[:80] + ("…" if len(text) > 80 else "")


def _replace_anon_segment(path: Path, new_body: str) -> bool:
    """整段替换匿名 AUTO-INDEX 段，校验段外内容零变化。"""
    content = _read_text(path)
    m = IDX_PATTERN.search(content)
    if not m:
        raise ParamError(f"{path} 缺少匿名 AUTO-INDEX 段边界，无法重建索引")
    replacement = IDX_START + "\n" + new_body.rstrip("\n") + "\n" + IDX_END
    # 段外内容由构造方式天然保全：prefix + 新段 + suffix 逐字节来自原文，无需事后校验
    new_content = content[:m.start()] + replacement + content[m.end():]
    if new_content != content:
        _write_text(path, new_content)
        return True
    return False


def refresh_index(project_dir: Path, only_basic=None):
    """重建 global basic-modules-index +（各/指定）basic 的 submodules-index。"""
    modules_dir = project_dir / "projects" / "modules"
    report = []
    basics = sorted(
        [d for d in modules_dir.iterdir() if d.is_dir() and (d / "module.yaml").exists()],
        key=lambda d: d.name,
    ) if modules_dir.exists() else []

    # global 段
    g_path = modules_dir / "overview.md"
    if g_path.exists():
        rows = []
        for b_dir in basics:
            y = _flat_yaml(b_dir / "module.yaml")
            summary = _positioning_summary(b_dir / "overview.md", y)
            rows.append(f"| [{b_dir.name}](./{b_dir.name}/overview.md) "
                        f"| {y.get('status', '-')} | {summary} |")
        if not rows:
            rows = ["| _（暂无基础模块。运行 `/core:module-init` 创建第一个）_ | - | - |"]
        body = ("> ⚙️ 此段由 `module-index-refresh` 自动维护，请勿手动编辑。\n\n"
                "| 基础模块 | 状态 | 摘要 |\n|---|---|---|\n" + "\n".join(rows) + "\n")
        changed = _replace_anon_segment(g_path, body)
        report.append(f"global basic-modules-index：{'已重建' if changed else '无变化'}")

    # 各 basic 段
    for b_dir in basics:
        if only_basic and b_dir.name != only_basic:
            continue
        subs = sorted(
            [d for d in b_dir.iterdir() if d.is_dir() and (d / "submodule.yaml").exists()],
            key=lambda d: d.name,
        )
        rows = []
        for s_dir in subs:
            y = _flat_yaml(s_dir / "submodule.yaml")
            summary = _positioning_summary(s_dir / "overview.md", y)
            rows.append(f"| [{s_dir.name}](./{s_dir.name}/overview.md) "
                        f"| {y.get('status', '-')} | {summary} |")
        if not rows:
            rows = ["| _（暂无子模块。运行 `/core:module-init` 创建第一个）_ | - | - |"]
        body = ("> ⚙️ 此段由 `module-index-refresh` 自动维护，请勿手动编辑。\n\n"
                "| 子模块 | 状态 | 摘要 |\n|---|---|---|\n" + "\n".join(rows) + "\n")
        overview = b_dir / "overview.md"
        if overview.exists():
            changed = _replace_anon_segment(overview, body)
            report.append(f"{b_dir.name} submodules-index：{'已重建' if changed else '无变化'}")
        else:
            report.append(f"{b_dir.name}：缺 overview.md，跳过（异常状态，建议人工检查）")
    return report


# ---------------------------------------------------------------- CLI


def _force_utf8_io():
    """把 stdout 与 stderr 都包成 UTF-8——**只在 CLI 入口调用，不在 import 时**（同 workframe_doctor）。

    中文 Windows 的控制台默认 cp936，本脚本的建树 / 刷索引日志全是中文，不包会 mojibake。
    **stderr 与 stdout 同等重要**：报错行本身带中文（`⚠ {warning}`），且用户项目路径
    常含中文（`C:\\Users\\<中文名>\\...`），只包 stdout 时报错动作自己抛
    `UnicodeEncodeError`——真实错因被二次崩溃顶掉，正是最需要看清现场的时刻。
    对称性由 `check_utf8_stream_wrap_symmetric` 强制。
    """
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass


def main():
    _force_utf8_io()
    ap = argparse.ArgumentParser(
        description="modules/ 体系 basic/sub 两层骨架的确定性落盘 + 索引段重建")
    ap.add_argument("--project", required=True, help="目标项目目录")
    ap.add_argument("--params", help="模块树 JSON（建树模式）")
    ap.add_argument("--refresh-index", action="store_true", help="仅重建索引段")
    ap.add_argument("--basic", help="配合 --refresh-index：只刷新指定 basic 的段")
    args = ap.parse_args()

    project_dir = Path(args.project).resolve()
    if not project_dir.is_dir():
        print(f"[module-init] project not found: {project_dir}", file=sys.stderr)
        return 2

    try:
        if args.refresh_index:
            for line in refresh_index(project_dir, only_basic=args.basic):
                print(f"[module-init] {line}")
            return 0
        if not args.params:
            print("[module-init] 需要 --params（建树）或 --refresh-index（刷索引）之一",
                  file=sys.stderr)
            return 2
        basics = load_params(Path(args.params))
        created, skipped, warnings, idx_report = create_tree(project_dir, basics)
        print(f"[module-init] created={len(created)} skipped={len(skipped)}")
        for line in idx_report:
            print(f"[module-init] {line}")
        for w in warnings:
            print(f"[module-init] ⚠ {w}", file=sys.stderr)
        return 0
    except ParamError as e:
        print(f"[module-init] {e}", file=sys.stderr)
        return 2
    except InstallError as e:
        print(f"[module-init] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
