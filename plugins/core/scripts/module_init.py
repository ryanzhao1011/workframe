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

    # 导航段（六类 AUTO-INDEX 段；规则唯一在 _module_nav.py）
    python module_init.py --project "<dir>" --refresh-index [--basic "<name>"] [--scope <路径>…] [--json]
    python module_init.py --project "<dir>" --check [--scope <路径>…] [--json]            # 零写入
    python module_init.py --project "<dir>" --convert-layout [--scope …] [--choice <路径>=<选项>…]
        [--apply --confirm <码>] [--json]                                           # 旧版式转换，默认只预览

    # 「未编写」状态（判定唯一在 _doc_state.py）
    python module_init.py --project "<dir>" --doc-state <文件…> [--json]        # 零写入
    python module_init.py --project "<dir>" --mark-unwritten <文件…>            # 复制模板后盖戳
    python module_init.py --project "<dir>" --unstamp <文件…>                   # 正文写入成功后删字段
    python module_init.py --project "<dir>" --mark-unwritten --scan [--extra-skeleton [类型=]<文件>…]
        [--apply --confirm <码>] [--json]                                      # 存量盘点，默认只预览

params JSON（launcher setup / module-init SKILL 采集对话后写出）：
    {
      "basics": [
        { "name": "订单管理",
          "owner": "pm",                       # 可选，默认 pm
          "positioning": "一句话定位",          # 可选；写进 overview 定位段并成为索引摘要
          "subs": [
            { "name": "订单创建",
              "owner": "pm",                   # 可选
              "positioning": "一句话定位",      # 可选
              "code_paths": ["src/order/**"]    # 可选；非空时初始化反向索引
            } ] } ] }

职责边界：
    - 只建 basic / sub 两层；requirement 资产包归 module-init SKILL（转写阶段的产物）
    - 导航段六类都由 `_module_nav.py` 渲染与写入，本脚本只做 CLI；module-index-refresh SKILL
      调本脚本、只把取数表留作规格，close-check 拿同一个规划器做零写入比较——单一实现，防两处漂移
    - 反向索引不重新实现：subprocess 调 `check-stale-modules.py init-submodule`
      （复用其文件锁 + 原子写），经 CLAUDE_PROJECT_DIR 环境变量定位项目

退出码：0 成功；2 参数 / 校验错误（ParamError，含几种模式同时给）；1 插件不完整（InstallError）或其他异常。
    导航三条命令另有：--refresh-index 3 = 有冲突（其余已写）、2 = 段问题；--check 1 = 有差异或旧版式、2 = 无法检查；
    --convert-layout --apply 确认码不符是 2、零写入。
    「未编写」几条命令：--doc-state 逐份报状态、读不到的也只报不改退出码（看输出）；
    --unstamp / --mark-unwritten 有任一份 missing / refused / error 即 1；--scan --apply 整批要么全写、要么零写入，
    零写入时 1（确认码不符是 2）。
"""

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import _doc_state  # noqa: E402
import _module_nav  # noqa: E402

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

class ParamError(Exception):
    """参数缺失 / 校验失败——必须让调用方当场看见，不静默降级。退出码 2。"""


class InstallError(Exception):
    """插件自身不完整（模板目录缺失等）——不是用户输入的问题。退出码 1，
    与 module-init SKILL 处置表的「确认 `$WF_PLUGIN_ROOT` 指向的插件根有效」对齐。"""


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


_flat_yaml = _module_nav.flat_yaml   # 唯一实现在 _module_nav（导航取数与这里共用）


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
            f"  插件安装不完整或模板被删。确认会话开场注入的「本会话插件根」"
            f"有效（重装 core 插件可修复），再重跑。")
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
        kind = _doc_state.kind_of(rel)
        if kind and _doc_state.mark_value(content.encode("utf-8"), trusted=True) == "true":
            # 模板里的 `unwritten: true` 只说明「从模板来」；刚渲染的正文必然是骨架，
            # 在这里就换成正文摘要，免得新建的现状文档停在 unstamped
            try:
                content = _doc_state.stamp(content.encode("utf-8"), kind, trusted=True).decode("utf-8")
            except _doc_state.StampError as e:
                raise InstallError(f"模板 {src} 渲染后无法盖未编写戳（{e.code}：{e}）——"
                                   f"模板正文与骨架判定不一致") from e
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

    idx_report, nav_plan, nav_results = refresh_index(project_dir, only_basic=None)
    top = lambda path: len(Path(path).parts) <= 4   # projects/modules/overview.md 与 <basic>/overview.md
    broken = [d for d in nav_plan.diagnostics if d.code in _module_nav.STRUCTURAL and top(d.path)]
    broken += [_module_nav.Diagnostic(r.path, 0, r.status, r.reason) for r in nav_results
               if r.status in ("error", "conflict") and top(r.path)]
    if broken:
        # 与旧实现同口径：上两层导航段坏了就以参数错误退出，不把「上级导航没更新」报成成功
        raise ParamError("上级导航段无法刷新：" + "；".join(f"{d.path}：{d.code}（{d.detail}）" for d in broken))
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
            detail = (r.stderr or "").strip().splitlines()
            return False, (f"{module_path}: 反向索引未刷新（exit {r.returncode}"
                           f"{'：' + detail[0] if detail else ''}）——修好后跑 "
                           f"`python check-stale-modules.py rebuild-index`")
        return True, ""
    except Exception as e:
        return False, f"{module_path}: 反向索引初始化异常 {e}——事后 rebuild-index 兜底"


# ---------------------------------------------------------------- 索引段重建


def refresh_index(project_dir: Path, only_basic=None, scope=None):
    """刷新导航段（规则唯一在 _module_nav）。返回 (report_lines, plan, results)。

    只写 update：前两层匿名段一直照常刷新；后四类段遇旧版式只报不写（等 --convert-layout）。
    建树（create_tree）调的也是它，所以建一个无关模块不会改动存量旧表。"""
    p = _module_nav.plan(project_dir, scope=scope, basic=only_basic)
    results = _module_nav.apply(p)
    written = {r.path for r in results if r.status == "written"}
    report = []
    for c in p.changes:
        seg = c.seg_kind if c.segment is None else c.segment
        if c.kind == "legacy":
            report.append(f"{c.path} {seg}：旧版式，待转换（--convert-layout 预览）")
        elif c.kind == "update":
            report.append(f"{c.path} {seg}：{'已重建' if c.path in written else '未写'}")
        else:
            report.append(f"{c.path} {seg}：无变化")
    for r in results:
        if r.status in ("conflict", "error"):
            report.append(f"{r.path}：{r.status}（{r.reason}）")
    for d in p.diagnostics:
        if d.code != "unstamped":
            report.append(f"{d.path}：{d.code}（{d.detail}）")
    return report, p, results


# ---------------------------------------------------------------- 未编写状态（判定在 _doc_state）


SCAN_CLASSES = ("纯骨架", "疑似", "已编写", "无信号", "已盖戳", "摘要失配", "异常")


def _project_path(project_dir: Path, raw_path: str) -> Path:
    """命令行给的文档路径 → 项目内绝对路径；落在项目外拒绝（这几条命令会写文件）。"""
    path = Path(raw_path)
    path = (path if path.is_absolute() else project_dir / path).resolve()
    try:
        path.relative_to(project_dir)
    except ValueError:
        raise ParamError(f"{raw_path} 不在项目 {project_dir} 内")
    return path


def _rel(project_dir: Path, path: Path) -> str:
    return path.relative_to(project_dir).as_posix()


def _atomic_write_bytes(path: Path, data: bytes):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def doc_states(project_dir: Path, paths):
    """[{path, kind, state}]；state 多出 missing / error 两种读不到的情形。"""
    out = []
    for raw_path in paths:
        path = _project_path(project_dir, raw_path)
        item = {"path": _rel(project_dir, path), "kind": _doc_state.kind_of(path)}
        if not path.is_file():
            item["state"] = "missing"
        else:
            try:
                item["state"] = _doc_state.effective_state(path.read_bytes())
            except OSError as e:
                item["state"], item["reason"] = "error", f"{type(e).__name__}: {e}"
        out.append(item)
    return out


def mark_unwritten(project_dir: Path, paths, extra=()):
    """把各文件的 `unwritten: true` 换成正文摘要。[{path, status, reason}]，
    status ∈ stamped / already / refused / error。"""
    out = []
    for raw_path in paths:
        path = _project_path(project_dir, raw_path)
        item = {"path": _rel(project_dir, path)}
        kind = _doc_state.kind_of(path)
        try:
            if kind is None:
                raise _doc_state.StampError("unknown-kind", "只认 current-state 四件与 prd.md")
            raw = path.read_bytes()
            new = _doc_state.stamp(raw, kind, extra)
            if new == raw:
                item["status"] = "already"
            else:
                _atomic_write_bytes(path, new)
                item["status"] = "stamped"
        except _doc_state.StampError as e:
            item["status"], item["reason"] = "refused", f"{e.code}：{e}"
        except OSError as e:
            item["status"], item["reason"] = "error", f"{type(e).__name__}: {e}"
        out.append(item)
    return out


def unstamp_docs(project_dir: Path, paths):
    """删掉各文件的 unwritten 字段。[{path, status}]，status ∈ removed / absent / missing / refused / error。"""
    out = []
    for raw_path in paths:
        path = _project_path(project_dir, raw_path)
        item = {"path": _rel(project_dir, path)}
        try:
            if not path.is_file():
                item["status"] = "missing"
            else:
                raw = path.read_bytes()
                new = _doc_state.unstamp(raw)
                if new == raw:
                    item["status"] = "absent"
                else:
                    _atomic_write_bytes(path, new)
                    item["status"] = "removed"
        except _doc_state.StampError as e:
            item["status"], item["reason"] = "refused", f"{e.code}：{e}"
        except OSError as e:
            item["status"], item["reason"] = "error", f"{type(e).__name__}: {e}"
        out.append(item)
    return out


def load_extra_skeletons(project_dir: Path, specs):
    """`--extra-skeleton [<类型>=]<文件>` → {类型: [(id, 正文)]}；类型缺省按文件名认。

    返回的 id 带样本正文摘要，进确认码——换了样本，旧确认码即失效。"""
    extras = {}
    for spec in specs or ():
        kind, _, file_part = spec.rpartition("=") if "=" in spec else ("", "", spec)
        path = Path(file_part)
        path = path if path.is_absolute() else project_dir / path
        kind = kind or _doc_state.kind_of(path)
        if kind not in _doc_state.KINDS:
            raise ParamError(f"--extra-skeleton {spec}：认不出类型，写成 <类型>=<文件>，"
                             f"类型取 {' / '.join(_doc_state.KINDS)}")
        if not path.is_file():
            raise ParamError(f"--extra-skeleton {spec}：文件不存在")
        raw = path.read_bytes()
        body = _doc_state.normalize_body(_doc_state._body_text(raw))
        sid = f"extra:{path.name}:{_doc_state.body_digest(raw)[7:15]}"
        extras.setdefault(kind, []).append((sid, body))
        EXTRA_TEXTS[sid] = raw.decode("utf-8", errors="replace")
    return extras


EXTRA_TEXTS = {}   # 额外样本 id → 文件原文（预览给人核准用；比对只用归一正文）


def _scan_targets(project_dir: Path):
    """全部 `current-state/*.md` 与两层子需求的 `prd.md`；解析后落到项目外的（经目录链接）不收——
    盘点会写这些文件，项目边界与显式路径的 `_project_path` 同一口径。"""
    modules = project_dir / "projects" / "modules"
    found = set(modules.glob("*/*/current-state/*.md"))
    found.update(modules.glob("*/*/requirements/*/*/prd.md"))
    out = []
    for p in sorted(found):
        if not p.is_file():
            continue
        try:
            p.resolve().relative_to(project_dir)
        except ValueError:
            continue
        out.append(p)
    return out


def _nearest_diff(body, skeletons):
    import difflib
    if not skeletons:
        return None, []
    body_lines = body.split("\n")

    def resolved(skeleton):
        # 带渲染变量位的骨架行，换成正文里第一条能被它通配匹配上的行——否则每份疑似件都会把
        # 「# {{SUB_NAME}} …」「- {{TODAY}} 初次解析」报成差异，真正的正文增量被淹没
        used, out = set(), []
        for line in skeleton.split("\n"):
            if "{{" in line:
                rx = _doc_state._skeleton_regex(line)
                hit = next((i for i, b in enumerate(body_lines) if i not in used and rx.fullmatch(b)), None)
                if hit is not None:
                    used.add(hit)
                    line = body_lines[hit]
            out.append(line)
        return out

    candidates = [(sid, resolved(skel)) for sid, skel in skeletons]
    sid, skel_lines = max(candidates, key=lambda s: difflib.SequenceMatcher(
        None, s[1], body_lines, autojunk=False).ratio())
    # 只去掉两行文件头与 @@ 段头；内容行恒以 + / - / 空格开头，「+++ 正文」这类真实增量照留
    lines = [line for line in list(difflib.unified_diff(skel_lines, body_lines, lineterm="", n=0))[2:]
             if not line.startswith("@@")]
    return sid, lines


def scan_unwritten(project_dir: Path, extras):
    """存量盘点：每份 current-state/*.md 与 prd.md 恰落一类（SCAN_CLASSES），只有「纯骨架」进候选。
    不是四件之一的现状文件没有骨架可比，按有无内容落「已编写」或「无信号」。"""
    from _skeletons import SIGNATURES
    files = []
    for path in _scan_targets(project_dir):
        kind = _doc_state.kind_of(path)
        item = {"path": _rel(project_dir, path), "kind": kind}
        files.append(item)
        try:
            raw = path.read_bytes()
        except OSError as e:
            item.update({"class": "异常", "reason": f"{type(e).__name__}: {e}"})
            continue
        item["sha256"] = hashlib.sha256(raw).hexdigest()
        body = _doc_state.normalize_body(_doc_state._body_text(raw))
        value = _doc_state.mark_value(raw)
        state = _doc_state.effective_state(raw)
        if state == "unwritten":
            item["class"] = "已盖戳"
        elif value is _doc_state.DUPLICATE:
            item.update({"class": "异常", "reason": "unwritten 字段重复"})
        elif value is _doc_state.INCONSISTENT:
            item.update({"class": "异常", "reason": "unwritten 的行内写法与 YAML 解析出的值不一致"})
        elif value is not None and value != "true" and not _doc_state.DIGEST_RE.fullmatch(value):
            item.update({"class": "异常", "reason": f"unwritten 的值「{value}」异常"})
        elif value is not None and value != "true":
            item.update({"class": "摘要失配", "reason": "盖过戳，正文已改（即已编写）；可删掉该字段"})
        elif not body:
            item["class"] = "无信号"
        elif _doc_state.split(raw).fm_lines is None and \
                _doc_state.match_skeleton(raw, kind, extras.get(kind, ())):
            item.update({"class": "异常", "reason": "正文是骨架但没有闭合的 frontmatter，无处盖戳"})
        else:
            sid = None if value not in (None, "true") else \
                _doc_state.match_skeleton(raw, kind, extras.get(kind, ()))
            if sid:
                try:
                    _planned_stamp(raw, kind, extras.get(kind, ()))
                    item.update({"class": "纯骨架", "skeleton": sid})
                except _doc_state.StampError as e:
                    item.update({"class": "异常", "reason": f"正文是骨架但盖不了戳：{e.code}：{e}"})
            else:
                hits = [s for s in SIGNATURES.get(kind, ()) if s in body]
                if hits:
                    near, diff = _nearest_diff(body, _doc_state.known_skeletons(kind, extras.get(kind, ())))
                    item.update({"class": "疑似", "signatures": hits, "nearest": near, "diff": diff})
                else:
                    item["class"] = "已编写"
    return files


def scan_confirm_code(project_dir: Path, files, extras):
    payload = {
        "project": str(project_dir),
        "candidates": [[f["path"], f["sha256"], f["skeleton"]] for f in files if f["class"] == "纯骨架"],
        "extras": sorted([k, sid] for k, items in extras.items() for sid, _ in items),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _planned_stamp(raw, kind, extra):
    """盘点与执行共用的改写：没有字段的插一行，字面 true 的换值。"""
    if _doc_state.mark_value(raw) is None:
        return _doc_state.insert_stamp(raw, kind, extra)
    return _doc_state.stamp(raw, kind, extra)


def apply_scan(project_dir: Path, files, extras):
    """给纯骨架盖戳：没有字段的插一行，字面 true 的换值。

    先在内存里把整批算完：任一候选的字节与预览时不同（确认码绑的是预览那一刻）、或盖戳被拒，
    整批不写，返回各自原因；全部通过才逐份写盘（临时文件 + `os.replace`），每份替换前重读一次、
    重核仍在项目内。中途某份写失败或被改动即停：已写的报 stamped、出事的报原因、其余报 skipped，
    调用方据此知道哪些文件变了。重读与替换之间仍有一个很短的窗口，期间若有人保存该文件，那次
    保存会被覆盖——这是本命令的已知边界。"""
    planned, results = [], []
    for f in files:
        if f["class"] != "纯骨架":
            continue
        path = project_dir / f["path"]
        extra = extras.get(f["kind"], ())
        try:
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != f["sha256"]:
                raise _doc_state.StampError("changed", "预览之后文件变了，确认作废；重新预览")
            new = _planned_stamp(raw, f["kind"], extra)
            planned.append((path, raw, new, len(results)))
            results.append({"path": f["path"], "status": "stamped"})
        except _doc_state.StampError as e:
            results.append({"path": f["path"], "status": "refused", "reason": f"{e.code}：{e}"})
        except OSError as e:
            results.append({"path": f["path"], "status": "error", "reason": f"{type(e).__name__}: {e}"})
    if any(r["status"] != "stamped" for r in results):
        for r in results:
            if r["status"] == "stamped":
                r.update({"status": "skipped", "reason": "同批有候选未通过，整批未写"})
        return results
    for n, (path, raw, new, idx) in enumerate(planned):
        try:
            path.resolve().relative_to(project_dir)
            if path.read_bytes() != raw:
                raise _doc_state.StampError("changed", "执行中文件被改动，未覆盖")
            _atomic_write_bytes(path, new)
        except (ValueError, OSError, _doc_state.StampError) as e:
            code = e.code if isinstance(e, _doc_state.StampError) else type(e).__name__
            results[idx].update({"status": "error", "reason": f"{code}：{e}" if str(e) else code})
            for _, _, _, rest in planned[n + 1:]:
                results[rest].update({"status": "skipped", "reason": "前一份写入失败，其余未写"})
            break
    return results


def _print_scan(files, extras, code, diff_cap=30):
    counts = {c: sum(1 for f in files if f["class"] == c) for c in SCAN_CLASSES}
    print(f"[module-init] 盘点 {len(files)} 份：" + " · ".join(f"{c} {n}" for c, n in counts.items()))
    for kind, items in sorted(extras.items()):
        for sid, body in items:
            print(f"\n[module-init] 传入的额外骨架 {sid}（类型 {kind}）全文——请核准它确实是没人写过的骨架：")
            print(EXTRA_TEXTS.get(sid, body))
    for f in files:
        line = f"  {f['class']}  {f['path']}"
        if f["class"] == "纯骨架":
            line += f"（{f['skeleton']}）"
        elif f.get("reason"):
            line += f"：{f['reason']}"
        print(line)
        if f["class"] == "疑似":
            print(f"      命中占位：{'、'.join(f['signatures'])}；与 {f['nearest']} 的差异行：")
            for d in f["diff"][:diff_cap]:
                print(f"        {d}")
            if len(f["diff"]) > diff_cap:
                print(f"        …另有 {len(f['diff']) - diff_cap} 行，--json 看全部")
    print(f"\n[module-init] 确认码 {code}——核对清单后跑 --mark-unwritten --scan --apply --confirm {code}"
          f"（带同样的 --extra-skeleton）")


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


NAV_MODES = ("--refresh-index", "--check", "--convert-layout")


def _nav_rows(p, file_status=None):
    """导航命令的逐段结果行；file_status 为 apply 结果（{path: Result}），只在写过的命令里给。"""
    writable = ("update", "convert") if p.convert else ("update",)
    rows = []
    for c in p.changes:
        status, reason = c.kind, ""
        if file_status is not None and c.kind in writable:
            r = file_status.get(c.path)
            status, reason = (r.status, r.reason) if r else ("unchanged", "")
        elif c.kind == "legacy":
            reason = "旧版式，待转换（--convert-layout 预览）"
        elif c.kind == "blocked":
            reason = "该文件段结构或取数有问题，整份不写"
        row = {"path": c.path, "segment": c.segment or c.seg_kind, "status": status, "reason": reason, "line": c.line}
        if c.kind in ("update", "legacy", "convert", "blocked"):
            line, old, new = c.diff()
            row.update(line=line, old_lines=old, new_lines=new)
        rows.append(row)
    if file_status is not None:
        covered = {c.path for c in p.changes if c.kind in writable}
        for path, _, line in p.examples:
            r = file_status.get(path)
            if r and path not in covered:
                rows.append({"path": path, "segment": "example-diagram", "status": r.status, "reason": r.reason,
                             "line": line})
    for d in p.diagnostics:
        rows.append({"path": d.path, "segment": d.segment, "status": "unstamped" if d.code == "unstamped" else "problem",
                     "reason": d.code + (f"：{d.detail}" if d.detail else ""), "line": d.line})
    return rows


def _nav_error(command, message, as_json):
    """导航命令的失败回执：--json 时 stdout 照样给一份结构化结果。退出码 2。"""
    if as_json:
        print(json.dumps({"schema": _module_nav.SCHEMA, "command": command, "results": [],
                          "error": {"code": "bad_usage", "message": message}}, ensure_ascii=False))
    print(f"[module-init] {message}", file=sys.stderr)
    return 2


def _nav_command(project_dir: Path, args):
    command = "refresh-index" if args.refresh_index else "check" if args.check else "convert-layout"
    try:
        return _nav_run(project_dir, args, command)
    except ParamError as e:
        return _nav_error(command, str(e), args.json)


def _nav_run(project_dir: Path, args, command):
    if args.convert_layout and args.confirm and not args.apply:
        raise ParamError("--confirm 只配合 --apply 使用")
    if args.convert_layout and args.apply and not args.confirm:
        raise ParamError("--apply 需要预览给出的 --confirm <码>")
    try:
        p = _module_nav.plan(project_dir, scope=args.scope, convert=args.convert_layout, basic=args.basic)
    except _module_nav.ScopeError as e:
        raise ParamError(str(e))
    choices = {}
    for spec in args.choice or []:
        raw, sep, opt = spec.rpartition("=")
        if not sep or opt not in _module_nav.CHOICES:
            raise ParamError(f"--choice {spec}：形如 <路径>=<{'|'.join(_module_nav.CHOICES)}>")
        rel = _rel(project_dir, _project_path(project_dir, raw))
        if rel not in {path for path, _, _ in p.examples}:
            raise ParamError(f"--choice {spec}：该文件不在本次预览的示例图清单里")
        choices[rel] = opt
    problems = _module_nav.has_problems(p)
    out = {"schema": _module_nav.SCHEMA, "command": command}
    if args.check:
        out["results"] = _nav_rows(p)
        code = 2 if problems else 1 if any(c.kind in ("update", "legacy") for c in p.changes) else 0
    elif args.refresh_index or args.apply:
        if args.convert_layout and args.confirm != _module_nav.confirm_code(p, choices):
            raise ParamError("确认码不符（预览之后有目标文件、PRD、meta、附件或 --choice 变了，或码抄错）"
                             "——整批拒绝、零写入；重新预览")
        results = _module_nav.apply(p, choices)
        out["results"] = _nav_rows(p, {r.path: r for r in results})
        code = 2 if problems else 3 if any(r.status in ("conflict", "error") for r in results) else 0
    else:
        out["results"] = _nav_rows(p)
        out["confirm"] = _module_nav.confirm_code(p, choices)
        out["details"] = [dict(path=c.path, segment=c.segment or c.seg_kind, **_module_nav.convert_details(c))
                          for c in p.changes if c.kind == "convert"]
        out["examples"] = [{"path": path, "line": line} for path, _, line in p.examples]
        out["contradictions"] = p.contradictions
        code = 2 if problems else 0
    if args.json:
        print(json.dumps(out, ensure_ascii=False))
        return code
    for r in out["results"]:
        if r["status"] == "unchanged":
            continue
        print(f"[module-init] {r['status']}\t{r['path']}:{r['line']}\t{r['segment'] or '-'}"
              + (f"\t{r['reason']}" if r["reason"] else ""))
        if command == "check":
            for line in r.get("old_lines", [])[:20]:
                print(f"    - {line}")
            for line in r.get("new_lines", [])[:20]:
                print(f"    + {line}")
    if command == "convert-layout" and not args.apply:
        for d in out["details"]:
            print(f"\n== {d['path']}（{d['segment']}）将消失的旧行 {len(d['gone'])} 条：")
            for line in d["gone"]:
                print(f"  - {line}")
            for col in d["extra_columns"]:
                print(f"  项目自加列「{col['column']}」有值单元格 {len(col['cells'])} 个（转换后丢弃）：")
                for key, val in col["cells"]:
                    print(f"    {key}：{val}")
        for ex in out["examples"]:
            print(f"\n== {ex['path']}:{ex['line']} 段外有出厂示例图：--choice {ex['path']}=replace-example 换成说明，缺省保留")
        if out["contradictions"]:
            print("\n== 段外人写句与需求状态可能矛盾（按状态词粗筛，只列不改，可能漏也可能误列）：")
            for c in out["contradictions"]:
                print(f"  {c['path']}:{c['line']}（meta.yaml status: {c['status']}）{c['text']}")
        print(f"\n确认码：{out['confirm']}（执行时带上同样的 --choice：--convert-layout --apply "
              f"--confirm {out['confirm']}；确认码随选项变，改选项先重新预览）")
    if not out["results"] or all(r["status"] == "unchanged" for r in out["results"]):
        print("[module-init] 导航段无差异")
    return code


def main():
    _force_utf8_io()
    ap = argparse.ArgumentParser(
        description="modules/ 体系 basic/sub 两层骨架的确定性落盘 + 索引段重建")
    ap.add_argument("--project", required=True, help="目标项目目录")
    ap.add_argument("--params", help="模块树 JSON（建树模式）")
    ap.add_argument("--refresh-index", action="store_true",
                    help="刷新导航段：写有差异的段；旧版式段只报不写。退出码 0 完成 / 3 有冲突（其余已写）/ 2 参数或段问题")
    ap.add_argument("--check", action="store_true",
                    help="零写入比较导航段。退出码 0 无差异 / 1 有差异或旧版式 / 2 无法检查")
    ap.add_argument("--convert-layout", action="store_true",
                    help="旧版式导航段转新版式：默认零写入预览并给确认码；--apply --confirm <码> 才写。"
                         "锁只挡同样走本命令的写入方，不配合锁的编辑器恰在比对与替换之间保存仍会被覆盖")
    ap.add_argument("--basic", help="配合 --refresh-index：只刷新指定 basic 的段")
    ap.add_argument("--scope", nargs="*", metavar="PATH",
                    help="配合导航三命令：限定到 <basic> / <basic>/<sub> / 需求目录 / projects/modules 下任一文件；"
                         "空或非法即退出 2，不退化成全量")
    ap.add_argument("--choice", action="append", metavar="PATH=OPTION",
                    help="配合 --convert-layout（预览与 --apply 带同样的选项）：基础模块总览的示例图 "
                         "replace-example / keep（缺省 keep）")
    ap.add_argument("--doc-state", nargs="+", metavar="PATH",
                    help="零写入：逐份输出 <state>\\t<path>（written / unwritten / unstamped / missing / error）")
    ap.add_argument("--mark-unwritten", nargs="*", metavar="PATH",
                    help="把 unwritten: true 换成正文摘要；正文已不是骨架则拒绝。配 --scan 做存量盘点")
    ap.add_argument("--unstamp", nargs="+", metavar="PATH", help="删掉 unwritten 字段（正文写入成功之后调）")
    ap.add_argument("--scan", action="store_true", help="配合 --mark-unwritten：存量盘点，默认只预览")
    ap.add_argument("--extra-skeleton", action="append", metavar="[KIND=]FILE",
                    help="配合 --scan：额外认得的骨架样本（全文会显示在预览里）")
    ap.add_argument("--apply", action="store_true", help="配合 --scan / --convert-layout：按确认码执行")
    ap.add_argument("--confirm", help="配合 --apply：预览给出的确认码")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    modes = [name for name, on in (("--doc-state", args.doc_state), ("--unstamp", args.unstamp),
                                   ("--mark-unwritten", args.mark_unwritten is not None),
                                   ("--refresh-index", args.refresh_index), ("--check", args.check),
                                   ("--convert-layout", args.convert_layout), ("--params", args.params)) if on]
    nav = NAV_MODES
    nav_json = args.json and bool(modes) and modes[0] in nav
    command = modes[0].lstrip("-") if nav_json else None
    if len(modes) > 1:
        msg = f"{' 与 '.join(modes)} 不能同时用，一次只做一件事"
        return _nav_error(command, msg, True) if nav_json else _nav_error(None, msg, False)
    owners = {"--scan": (args.scan, ("--mark-unwritten",)),
              "--apply": (args.apply, ("--mark-unwritten", "--convert-layout")),
              "--confirm": (args.confirm, ("--mark-unwritten", "--convert-layout")),
              "--extra-skeleton": (args.extra_skeleton, ("--mark-unwritten",)),
              "--basic": (args.basic, ("--refresh-index",)),
              "--scope": (args.scope is not None, nav),
              "--choice": (args.choice, ("--convert-layout",))}
    stray = [flag for flag, (on, allowed) in owners.items() if on and not (modes and modes[0] in allowed)]
    if args.json and modes and modes[0] not in ("--doc-state", "--unstamp", "--mark-unwritten") + nav:
        stray.append("--json")
    if stray:
        return _nav_error(command, f"{' / '.join(stray)} 不属于 {modes[0] if modes else '当前模式'}", nav_json)

    project_dir = Path(args.project).resolve()
    if not project_dir.is_dir():
        return _nav_error(command, f"project not found: {project_dir}", nav_json)

    try:
        if args.doc_state:
            items = doc_states(project_dir, args.doc_state)
            if args.json:
                print(json.dumps(items, ensure_ascii=False))
            else:
                for it in items:
                    print(f"{it['state']}\t{it['path']}")
            return 0
        if args.unstamp:
            items = unstamp_docs(project_dir, args.unstamp)
            if args.json:
                print(json.dumps(items, ensure_ascii=False))
            else:
                for it in items:
                    print(f"[module-init] {it['status']}\t{it['path']}" + (f"\t{it['reason']}" if it.get("reason") else ""))
            return 1 if any(it["status"] in ("missing", "refused", "error") for it in items) else 0
        if args.mark_unwritten is not None:
            if args.scan:
                if args.mark_unwritten:
                    raise ParamError("--scan 扫全项目，不接受文件参数")
                extras = load_extra_skeletons(project_dir, args.extra_skeleton)
                files = scan_unwritten(project_dir, extras)
                code = scan_confirm_code(project_dir, files, extras)
                if not args.apply:
                    if args.json:
                        print(json.dumps({"schema": "workframe.doc-state-scan.v1", "files": files,
                                          "extras": [{"kind": k, "id": sid, "body": body,
                                                      "text": EXTRA_TEXTS.get(sid)}
                                                     for k, items in sorted(extras.items()) for sid, body in items],
                                          "confirm": code}, ensure_ascii=False))
                    else:
                        _print_scan(files, extras, code)
                    return 0
                if args.confirm != code:
                    raise ParamError("确认码不符（预览之后有文件或骨架样本变了，或码抄错）——整批拒绝、零写入；重新预览")
                results = apply_scan(project_dir, files, extras)
                if args.json:
                    print(json.dumps({"schema": "workframe.doc-state-scan.v1", "applied": results}, ensure_ascii=False))
                else:
                    for r in results:
                        print(f"[module-init] {r['status']}\t{r['path']}" + (f"\t{r['reason']}" if r.get("reason") else ""))
                return 1 if any(r["status"] != "stamped" for r in results) else 0
            if args.apply or args.confirm or args.extra_skeleton:
                raise ParamError("--apply / --confirm / --extra-skeleton 只配合 --scan 使用")
            if not args.mark_unwritten:
                raise ParamError("--mark-unwritten 需要文件参数，或配 --scan")
            items = mark_unwritten(project_dir, args.mark_unwritten)
            if args.json:
                print(json.dumps(items, ensure_ascii=False))
            else:
                for it in items:
                    print(f"[module-init] {it['status']}\t{it['path']}" + (f"\t{it['reason']}" if it.get("reason") else ""))
            return 1 if any(it["status"] in ("refused", "error") for it in items) else 0
        if args.refresh_index or args.check or args.convert_layout:
            return _nav_command(project_dir, args)
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
