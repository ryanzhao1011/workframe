#!/usr/bin/env python3
"""
tools/validate.py — 深度语义校验框架仓库

用法：
    python tools/validate.py

检查族以文件末尾 CHECKS 注册表为准（总数从注册表现算，运行输出自报；README 的
检查数由 check_readme_check_count_current 对账）。大类涵盖：结构完整性 / 引用边界 /
行为契约（agents·skills·rules 编写约束）/ 事件 registry 与 producer 对账 / 平台与
并发（Windows 行尾·UTF-8·锁）/ 退役词与计数防漂 / 装机与验收接线。

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
import tokenize
import traceback
from pathlib import Path


FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]

# Markers
OK = "  ✓"
FAIL = "  ✗"


# ---- 运行态目录名的**唯一事实源**：本文件不自己写字面，一律问 `_state_io` ----
_STATE_IO_CACHE = {}


def _state_io_module():
    """import 框架自己的 `_state_io`，用来问「运行态目录叫什么」。

    闸不许自己抄一份目录名——抄了就是第二个事实源，改一边不改另一边时闸会替错误的那份
    背书（`paths_single_source` 恰恰是为防这件事而设，它自己更不能违反）。
    """
    if "mod" not in _STATE_IO_CACHE:
        import importlib.util
        src = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "_state_io.py"
        spec = importlib.util.spec_from_file_location("_wf_state_io", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _STATE_IO_CACHE["mod"] = mod
    return _STATE_IO_CACHE["mod"]


def _rt(kind, legacy=False):
    """运行态目录的项目根相对路径；`legacy=True` 取迁移前的旧形态。"""
    m = _state_io_module()
    return m.legacy_rel(kind) if legacy else m.new_rel(kind)


def _ok(name, extra=""):
    print(f"{OK} {name}" + (f" {extra}" if extra else ""))
    return True, None


def _fail(name, msg):
    print(f"{FAIL} {name}: {msg}")
    return False, msg

def _suite_failure_summary(output):
    """单测套件失败时转述给 validate 文案的那一段——**所有「跑一个 tools/test_*.py 再转述失败」的检查都走这里**。

    优先转述套件输出里**全部**失败行（行首 `[FAIL]` 或 `✗ `，两种是本仓单测的两种失败前缀），总长上限 2000 字；
    没有失败行（套件半途抛异常、导入失败等）才退回「末 6 行、截 400 字」。
    只取末几行的话，失败行里带的诊断（如并发格的子进程异常）会被末行汇总挤出去——CI 只跑 validate，
    那等于下次再红仍然定不了因。

    有失败行时**另外带上**两样，放在失败行之后、但先于截断保住：输出末行（套件汇总），以及输出含 `Traceback`
    时最后一段 traceback 的异常行（其后第一条不缩进的行）。只转述失败行的话，套件里另有测试函数抛了异常
    （混合崩溃）会完全看不见——那比「末 6 行」还少信息。"""
    lines = (output or "").strip().splitlines()
    fails = [ln.strip() for ln in lines if ln.strip().startswith(("[FAIL]", "✗ "))]
    if not fails:
        return " | ".join(lines[-6:])[:400]
    extras = []
    tb = [i for i, ln in enumerate(lines) if ln.startswith("Traceback")]
    if tb:
        exc = next((ln.strip() for ln in lines[tb[-1] + 1:] if ln.strip() and not ln[:1].isspace()), "")
        if exc:
            extras.append(f"traceback: {exc[:300]}")
    last = lines[-1].strip()[:300]
    if last and last not in fails and all(last not in x for x in extras):
        extras.append(last)
    tail = (" | " + " | ".join(extras)) if extras else ""
    return " | ".join(fails)[:max(0, 2000 - len(tail))] + tail



def _symbol_use_sites(src, symbol, func=None, paired_with=None, mode="any"):
    """「某符号在消费方真实路径上被引用」的**共用**判定式（全仓只此一份，别再抄第二份）。

    为什么不能用存在性断言：被检对象是**代码符号**（常量 / 函数 / 字段名 / 正则）时，
    「它在源码里出现过」不等于「消费方在用它」——定义 ≠ 被调用，零件在货架上完好不
    等于装到机器里了。两种假绿都实测过：把消费方的调用点换成内联副本、常量留成孤儿，
    `getattr(模块, 常量)` 式的闸全绿；把调用点换成恒返回 None 的分叉实现、同时保留
    共享函数的定义与那个字段名字面量，「源码里含该字面量」式的闸也全绿。两种情况下
    消费方读到的都已经是另一套口径，而闸对账的是个没人用的零件。

    **只用于标识符类符号**（函数名 / 常量名 / 类名）。**字面量类（字符串取值域等）
    不得走这条路径**——它们在源码里根本不是 NAME token，本函数对它们恒返回空 = 恒红。
    判「某个取值域判定还在不在」用 `_literal_compare_shapes`。实测：`exempt` 走这条
    路径命中 0（9 个出现点全在字符串与文案里），套上去会从假绿直接翻成假红。

    三步（宽收候选 / 严判剔除 / 差集即真实引用点）：
      1. **宽收**：`func` 给定时先把搜索面收窄到该函数体（顶层 `def` / `async def`
         到下一个顶层 `def` / `async def` / `class` 之前）；
      2. **严判**：交给 stdlib `tokenize`，只认 **NAME token 精确相等**，并额外剔除
         三类诱饵。**不是逐类加规则**——「哪些字节是注释 / 字符串」是词法器已解决的
         封闭问题，而 docstring / 行尾注释 / 输出文案 / 更长标识符子串是开放集合，
         逐类加规则永远追不完：
           · 注释与字符串（含 docstring、多行字符串里的示例代码）——词法器天然剥掉；
           · **f-string 插值区**——PEP 701（Py3.12+）之后 `f"{X}"` 里的 `X` 是**真
             NAME token**，词法器剥不掉它。文案插值不是承重引用，必须显式排除，
             否则一句 `f"…{CONST})"` 就能替真正的写入路径挡箭（实测：某常量
             tokenize 后 2 处，剥掉插值才降到 1 处那个真承重点）；
           · **`def` / `async def` 头部**（含默认值）——`def f(days=CONST)` 会满足
             任何「出现过该写法」式的探测，而它一直都在、不是别人插进来的诱饵，
             靠肉眼复查抓不到。
      3. **差集非空即真实引用点**。

    `paired_with` 非 None 时判「**这一次引用的上下文里**出现了该串」：引用点后面紧跟
    `(` 时取**平衡括号切片**（该次调用自己的实参文本，跨行安全、不会串到同一个字典
    字面量里的兄弟条目上）；否则退化为该引用所在物理行。

    `mode` 决定 `==` 还是 `⊆`，**由被检对象语义定、不一刀切**（写机器闸六问第 2 条）：
      · `"any"`（⊆）——只要有一处引用带上 `paired_with` 即可。用于「同一个取值函数被
        多个字段各调一次」这类场景：要求每处都带同一个字段名是无意义的；
      · `"all"`（==）——每一处引用都必须带上 `paired_with`，漏一处即红。用于「这个
        调用点只要有一处没走常量，那条路径就脱离对账」这类场景。
      **调用方必须在自己的注释里写明选了哪个、为什么。**

    **两种已知假红**（宽严方向都是 fail-safe，登记在此不另立闸）：
      · 实参走变量——`k = "last_synced_ref"; _scalar_field(t, k)` 配不上 `paired_with`；
      · 实参含嵌套括号且括号内恰好出现同名字串——平衡切片会把它算作命中（偏宽），
        此时 `paired_with` 的判定力下降，但不会把真缺陷放过去。

    返回 `(sites, bad, err)`：
      · `err` 非空 = 定位失败（函数改名 / 移走 / 无法 tokenize），调用方**必须据此
        报红**——提取器扫 0 时闸会「看起来很绿」，而那正是它彻底失明的样子；
      · `sites` 为空 = 该符号在这段范围内是孤儿，调用方报红；
      · `bad` 非空（仅 `mode="all"`）= 有引用点没带上 `paired_with`，调用方报红。
    """
    scope = src
    if func is not None:
        m = re.search(
            rf"^(?:async +)?def {re.escape(func)}\(.*?"
            rf"(?=^(?:async +)?def |^class |\Z)", src, re.S | re.M)
        if not m:
            return [], [], f"找不到 `def {func}(` ——实现改名或移走后本判定式静默失明"
        scope = m.group(0)
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(scope).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError) as e:
        return [], [], f"tokenize `{func or '<module>'}` 失败（源码形态变了）: {e}"

    lines = scope.splitlines()
    sites, fstr, in_hdr, hdr_paren = [], 0, False, 0
    for i, t in enumerate(toks):
        tn = tokenize.tok_name.get(t.type, "")
        if tn == "FSTRING_START":
            fstr += 1
        elif tn == "FSTRING_END":
            fstr = max(0, fstr - 1)
        if t.type == tokenize.NAME and t.string == "def":
            in_hdr, hdr_paren = True, 0
        elif in_hdr and t.type == tokenize.OP:
            if t.string in "([{":
                hdr_paren += 1
            elif t.string in ")]}":
                hdr_paren -= 1
            elif t.string == ":" and hdr_paren == 0:
                in_hdr = False
        if t.type != tokenize.NAME or t.string != symbol or fstr or in_hdr:
            continue
        sites.append(_use_context(toks, i, lines))

    if paired_with is None:
        return sites, [], ""
    if mode == "all":
        return sites, [s for s in sites if paired_with not in s], ""
    return [s for s in sites if paired_with in s], [], ""


def _use_context(toks, i, lines):
    """引用点的上下文文本：跟着 `(` 就取平衡括号内的实参切片，否则取该物理行。"""
    j = i + 1
    skip = (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT,
            tokenize.INDENT, tokenize.DEDENT)
    while j < len(toks) and toks[j].type in skip:
        j += 1
    if j < len(toks) and toks[j].type == tokenize.OP and toks[j].string == "(":
        depth, k = 0, j
        while k < len(toks):
            if toks[k].type == tokenize.OP and toks[k].string in "([{":
                depth += 1
            elif toks[k].type == tokenize.OP and toks[k].string in ")]}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        if k < len(toks):
            (r1, c1), (r2, c2) = toks[j].end, toks[k].start
            if r1 == r2:
                return lines[r1 - 1][c1:c2].strip()
            chunk = [lines[r1 - 1][c1:]] + lines[r1:r2 - 1] + [lines[r2 - 1][:c2]]
            return " ".join(x.strip() for x in chunk).strip()
    return (toks[i].line or "").strip()


def _literal_compare_shapes(src, func, literal):
    """判「以某个**字符串字面量**为对象的比较判定还在不在」——字面量类符号的专用路径。

    为什么不能复用 `_symbol_use_sites`：字符串字面量在源码里不是 NAME token，那条路径
    对它恒返回空（恒红）。而「整份源码/整个函数体里出现过这个字符串」又是恒绿——实测
    某取值域字面量在其判定函数体内出现 9 处，其中只有 2 处承重，另外 7 处是变量名子串
    与输出文案；把两处真实判定全漂成别的取值、只留那 7 处，存在性断言照样报绿。

    **正解是换断言形状**：不问「这个字符串被引用了吗」，问「**那两处比较判定本身还在
    吗**」。用 `ast` 求证，文案与注释天然不参与——它们是 `Constant` / `JoinedStr`，
    不是 `Compare` 的比较对象。

    返回 `(ops, domains, err)`：
      · `ops` = 以该字面量为直接比较对象的运算符名集合（`Eq` / `In` / `NotIn` …）；
      · `domains` = 包含该字面量的**成员测试容器**的完整字面量集合列表，用于断言取值域
        闭合（少一个取值 / 多一个取值都看得见）；
      · `err` 非空 = 定位失败或解析失败，调用方必须报红。
    """
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return set(), [], f"解析失败: {e}"
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            target = node
            break
    if target is None:
        return set(), [], f"找不到函数 `{func}` ——实现改名或移走后本判定式静默失明"
    ops, domains = set(), []
    for node in ast.walk(target):
        if not isinstance(node, ast.Compare):
            continue
        for op, comp in zip(node.ops, node.comparators):
            if isinstance(comp, ast.Constant) and comp.value == literal:
                ops.add(type(op).__name__)
            elif isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
                vals = {e.value for e in comp.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                if literal in vals:
                    ops.add(type(op).__name__)
                    domains.append(vals)
    return ops, domains, ""


AGENTS_DIR = FRAMEWORK_ROOT / "plugins" / "core" / "agents"


def _baseline_roles():
    """core baseline 角色集的**唯一事实源** = agents/ 下的 .md 文件名（去后缀）。

    隐含契约：该目录只放角色文件。放进任何非角色 .md，依赖本函数的闸会集体报错——
    这是有意的，目录即名录。下游枚举一律向本函数求证，不再各自手写角色名。
    """
    return {p.stem for p in AGENTS_DIR.glob("*.md")}


def _role_alternation(roles):
    """把角色集渲染成正则 alternation，**按长度降序**排列。

    正则 alternation 是最左匹配优先，短名排前面会把长名切一半——`dev|devops` 对
    `@devops` 只吃到 `dev`，剩下 `ops` 被丢弃且不报错。当前 4 个角色没有互为前缀的，
    但这条排序是本函数存在的全部理由：它保证将来加了 `devops` 这类名字也不会静默切断。
    """
    return "|".join(re.escape(r) for r in sorted(roles, key=lambda r: (-len(r), r)))


def _scaffold():
    """惰性 import `plugins/core/scripts/project_scaffold`——role-profile-catalog 章节解析的唯一实现方。

    闸解析 catalog 一律调它的 `catalog_section` / `routing_block`，validate 内不再自留一份
    切章逻辑：闸另写一套只能比消费方更宽或更窄，两个方向都实证出过洞（闸宽：标题行尾多一个
    空格，闸全绿而装机 preflight 抛 ParamError；闸镜像消费方实现：把它的 bug 一起抄，两边同错
    而闸报绿）。
    只在检查函数体内调用、异常由调用方转 `_fail`，**不放模块顶层**：scaffold 语法坏时应是
    复用它的那几项红，而不是 validate 整体起不来。依赖 scaffold「import 无副作用」这条装机链路
    硬约束（doctor / launcher 也 import 它）。
    """
    scripts = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import importlib
    return importlib.import_module("project_scaffold")


def _doctor():
    """惰性 import `plugins/core/scripts/workframe_doctor`——CLAUDE.md 导入判定、W3 残留识别与
    流转表行形（`FLOW_ROW_RE`）的唯一实现方。闸直接调它，不另写第二份判定式；与 `_scaffold`
    同理只在检查函数体内调用。doctor 承诺 import 无副作用（stdout 包装只在 CLI 入口）。
    """
    scripts = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import importlib
    return importlib.import_module("workframe_doctor")


def _catalog_profiles(text):
    """role-profile-catalog 里的 profile 候选名单——**宽探测器，全仓唯一实现**。

    故意宽（闭合反引号后不锚行尾）：若在这里就用消费方的严格判定式，行尾多了空格的
    标题会直接从循环里消失，只剩「doctor 枚举多 X」把人引向错误方向。宽收候选、再逐个
    用消费方判定式（`catalog_section`）求证，红灯才能点名真正的坏处。

    名字部分收的是**反引号之间的任意内容**，不是某个字符集：消费方 `catalog_section`
    对 profile 名零字符集约束，探测器一旦更窄，窄掉的那些档整档从候选里消失——闸与
    doctor 枚举两边同时看不见它，于是「catalog 新增档位而 doctor 枚举没跟」这个唯一
    要防的场景反而判绿（实测：字符集写成 `[a-z-]+` 时漏掉带数字的 `### `solo-pm2``，
    相关两闸全绿而 scaffold 照常抽取到它）。收宽的代价是形态坏掉的标题（`### `x` 后缀`）
    也会进候选，由调用方的「切不出章节」分支点名——那正是它该报的话。

    **两个消费本函数的检查必须共用这一份**：各自抄一份正则时，收紧其中一份就只让那一个
    闸失明，另一个照常红，看上去「还有闸拦着」，实际已经开出一个只在特定档位形态下触发
    的洞。
    """
    return re.findall(r"^### `([^`\n]+)`", text, re.M)


def priority_table(section, profile):
    """从 catalog 章节正文里取出**围栏外恰 1 张**「**角色优先级**：」表，返回 `(表头, 首数据行)`。

    **两个消费者共用这一份**（`role_profile_lead_copies` 取事实源主力列、
    `role_enum_single_source` 断言 7 取三列并集）。此前是两份逐字相同的正则副本，
    收紧其中一份只会让那一个闸失明、另一个照常红，看上去「还有闸拦着」。

    **围栏外恰 1 张是契约**，与 scaffold `routing_block` 的「块数恰 1」同族：
      · 取第一张 → 章节里一张更靠前的诱饵表静默顶替事实源。实测：真表之前放一份
        ```text 围栏内的同名表、再把真表主力列改坏 → 两闸全绿，事实源与两份副本
        已分叉而无一闸说话（```markdown 围栏时只有块数断言碰巧兜住一闸，覆盖是偶然的）。
      · 围栏内的表按「示例」跳过、**不计数**——那是文档里合法存在的形态。只数围栏外的，
        「真表被删、只剩围栏内示例」才会判 0 张；若把围栏内也计数，那一注入会读到示例并判绿。
    表结构判定与被它取代的旧正则**同宽**：标签行 → 表头 → 分隔行 → 首数据行四行紧邻，
    缺一不算（别为了让文案好看放宽成允许空行，那会把别处的表认成本章节的）。
    围栏判定 import scaffold 的 `_outside_fence`，validate 侧不另写 `in_fence` 循环。

    **表头策略不进本函数**：两个消费者对表头的要求不同（一个冻结断言三列完整划分、
    一个只要有「主力」列），那是合法的策略差异；本函数只负责「取出哪一张」。
    """
    sc = _scaffold()
    lines = section.splitlines()
    outside = {i for i, _ln in sc._outside_fence(lines)}
    hits = []
    for i in sorted(outside):
        if lines[i].rstrip() != "**角色优先级**：":
            continue
        if any((i + k) not in outside for k in (1, 2, 3)):
            continue
        hdr_ln, sep_ln, data_ln = (lines[i + k].rstrip() for k in (1, 2, 3))
        if not (hdr_ln.startswith("|") and hdr_ln.endswith("|")):
            continue
        if not re.fullmatch(r"\|[-:| ]+\|", sep_ln):
            continue
        if not (data_ln.startswith("|") and data_ln.endswith("|")):
            continue
        hits.append((i, [c.strip() for c in hdr_ln[1:-1].split("|")],
                     [c.strip() for c in data_ln[1:-1].split("|")]))
    if not hits:
        raise ValueError(
            f"catalog {profile} 围栏外的「**角色优先级**：」表 0 张——表被删、挪进了代码围栏、"
            f"或标签/表头/分隔行四行紧邻的结构变了；两处主力列对账从此失去事实源")
    if len(hits) > 1:
        rows = ", ".join(f"第 {h[0] + 1} 行" for h in hits[:3])
        more = f"（共 {len(hits)} 张）" if len(hits) > 3 else ""
        raise ValueError(
            f"catalog {profile} 围栏外的「**角色优先级**：」表 {len(hits)} 张（{rows}{more}）"
            f"——靠前那张会顶替事实源，真表改坏了两处副本对账仍报绿；删到只剩一张")
    return hits[0][1], hits[0][2]


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


# 两份 hook 清单：CC 与 Codex 各一份，都随插件分发。**凡「每条 hook 命令都要 X」的闸都要
# 枚举两份**——只扫 CC 那份时 Codex 清单零闸覆盖，而 Codex 运行时对坏 hook 零告警（双盲）。
HOOK_MANIFEST_RELS = {
    "cc": "plugins/core/hooks/hooks.json",
    "codex": "plugins/core/hooks/hooks.codex.json",
}
_CODEX_HOOKS_CACHE = {}


def _codex_hooks():
    """import `plugins/core/scripts/_codex_hooks.py`——Codex 清单派生规则、hash 字段集、条目形态的
    **唯一实现**。闸不自留一份字段表：字段表若各抄一份，给某条加 `timeout` 时闸绿、manifest 不必改，
    而升级后该条在 Codex 侧 `modified` 静默停跑。"""
    if "mod" not in _CODEX_HOOKS_CACHE:
        import importlib.util
        src = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "_codex_hooks.py"
        spec = importlib.util.spec_from_file_location("_wf_codex_hooks", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CODEX_HOOKS_CACHE["mod"] = mod
    return _CODEX_HOOKS_CACHE["mod"]


def _hook_manifest_path(door):
    return FRAMEWORK_ROOT / HOOK_MANIFEST_RELS[door]


def _hook_manifest(door):
    """一份清单（已解析）。缺失 / 不可解析直接抛——调用方转 `_fail`，不把「读不到」当「0 条」。"""
    return json.loads(_hook_manifest_path(door).read_text(encoding="utf-8"))


def _hook_field_entries(door):
    """`door` 那份清单的全部**命令字段**，现算：[(事件, matcher, 字段名, 命令串)]。

    `command` 恒有；`commandWindows` 在场时也进来（Codex 清单每条都有）。两条 launcher
    断言的迭代面必须是这个——只换「读哪份文件」不换「读哪个字段」，`commandWindows`
    依旧零覆盖：在它上面写死一条带版本号的绝对路径，三条形状检查全过，而插件根带版本号、
    升级即变，那正是「升级后静默执行另一个安装里的旧脚本」那条通道。
    """
    out = []
    for event, entries in (_hook_manifest(door).get("hooks") or {}).items():
        for entry in entries or []:
            for h in entry.get("hooks") or []:
                out.append((event, entry.get("matcher", ""), "command", h.get("command", "")))
                if "commandWindows" in h:
                    out.append((event, entry.get("matcher", ""), "commandWindows",
                                h.get("commandWindows", "")))
    return out


def check_hooks_json_parseable():
    """两份 hook 清单都可解析：CC 那份含全部 CC 事件；Codex 那份的事件集 **==** CC 事件集 −
    Codex 没有的那几个（`_codex_hooks.CODEX_ABSENT_EVENTS`）。

    Codex 那份是派生物，事件集不另列名单——从 CC 清单现算：少一个事件 = 生成器漏了一段链路
    （该事件在 Codex 门下整段缺席且零告警），多一个 = 手改了产物。
    """
    name = "hooks_json_parseable"
    try:
        hooks = _hook_manifest("cc").get("hooks", {})
        if not {"SessionStart", "UserPromptSubmit", "PostToolUse", "Stop", "SubagentStart",
                "SubagentStop", "StopFailure", "ConfigChange", "SessionEnd"}.issubset(hooks.keys()):
            return _fail(name, f"CC 清单缺 hook 事件, got {list(hooks.keys())}")
        want = set(hooks.keys()) - set(_codex_hooks().CODEX_ABSENT_EVENTS)
        got = set((_hook_manifest("codex").get("hooks") or {}).keys())
        if got != want:
            return _fail(name, f"Codex 清单事件集 ≠ 「CC 事件 − Codex 缺口事件」：少 {sorted(want - got) or '无'}、"
                               f"多 {sorted(got - want) or '无'}——少的事件在 Codex 门下整段链路缺席且零告警，"
                               f"多的说明产物被手改过（重跑 gen_codex_hooks.py）")
        return _ok(name, f"(CC {len(hooks)} 事件 / Codex {len(got)} 事件，两份都可解析)")
    except Exception as e:
        return _fail(name, str(e))


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
    # 信号入账的落盘工序：判据留在必载片，工序在这个 skill
    "signal-intake",
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
    "signal-intake",
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
        # 接入已有项目时把用户原文与两份模板（CLAUDE.md / AGENTS.md）整合（与两份模板强耦合，同插件放）
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


# rules 分发机制的字面残留模式。**别在本文件的散文里复述这些字面**——本文件在扫描面内，
# 复述一次就是一处自命中；清单只此一份，文案与 docstring 里一律按 `len()` 现算。
RULES_RESIDUE_PATTERNS = (
    "sync-rules",
    # 与上一条模式**词序相反**，那条抓不到这个已删 docs 的文件名，故单列。
    # 当前跟踪文件内零命中——补它是把覆盖面补对称，不是修现存缺陷。
    # （本注释刻意不写出那两个字面：裸字符串列表元素有自豁免，注释没有，
    #   写进来会被本闸当成真残留——补这条模式时它第一时间抓的就是这段注释。）
    "rules-sync.md",
    "rules/workframe/core",
    "rules/core/",
    "core rule",
    "rules 镜像",
    "agent-protocols.md",
    "auto-update.md",
    "correction-detection.md",
    "response-output.md",
    "closeout-discipline.md",
)
# 台账键：被退役那批源的**段落标识符**，是冻结对账的基准，不是活指针。按**行形**排除而不是
# 整文件豁免——同一个文件里写一句散文指针照样进判定面。两种形：注入片 frontmatter 的
# `- "<name>.md#<段名>"`，与台账里的 `"section": "<name>.md#<段名>"`。
# 段名里可能带转义的 ASCII 双引号（真的有一条），所以内层不能写成 `[^"]*`。
RULES_RESIDUE_LEDGER_KEY = re.compile(
    r'^\s*(?:-\s+|"section":\s*)?'
    r'"(?:agent-protocols|auto-update|correction-detection|response-output'
    r'|closeout-discipline)\.md#(?:[^"\\]|\\.)*",?\s*$')
# 白名单按**文件 + 段**给，不按整个文件给。值是 `(文件文本) -> 豁免行号集合`。
RULES_RESIDUE_ALLOW_FILES = ("CHANGELOG.md",)


def _changelog_released_lines(text):
    """CHANGELOG 里**已发布段**的行号集合（1-based）。

    已发布段按账本纪律不可改，必然写过退役前的机制；**未发布段不豁免**——那一段本就该
    随本次改动更新。段边界按 `## [x.y.z]` 标题切，标题里含「未发布」的那一段不进集合。
    """
    released, current_released = set(), False
    for i, line in enumerate(text.splitlines(), 1):
        if re.match(r"^##\s+\[", line):
            current_released = "未发布" not in line
            if current_released:
                released.add(i)
            continue
        if current_released:
            released.add(i)
    return released


def _residue_scan_files():
    """零残留断言的扫描面。

    方案点名的 `templates/` 不单列——它是 `plugins/core/templates/`，已被 `plugins/` 覆盖；
    单列一个不存在的仓根目录只会让本闸永久红在「扫描面缺失」上。
    仓根 `CHANGELOG.md` 在面内（它是框架自有资产），靠上面的段级白名单处理已发布段。
    """
    roots = (FRAMEWORK_ROOT / "plugins", FRAMEWORK_ROOT / "tools",
             FRAMEWORK_ROOT / "docs", FRAMEWORK_ROOT / ".claude-plugin",
             FRAMEWORK_ROOT / ".github", FRAMEWORK_ROOT / "README.md",
             FRAMEWORK_ROOT / "CLAUDE.md", FRAMEWORK_ROOT / "CHANGELOG.md")
    for root in roots:
        if not root.exists():
            raise FileNotFoundError(root.relative_to(FRAMEWORK_ROOT).as_posix())
        for p in ([root] if root.is_file() else sorted(root.rglob("*"))):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            raw = p.read_bytes()
            if b"\x00" in raw:
                continue
            yield p.relative_to(FRAMEWORK_ROOT).as_posix(), raw.decode("utf-8", errors="replace")


def check_no_rules_mechanism_residue():
    """rules 分发机制在框架自有资产里零残留。

    **判据不是「那个目录删掉了」，是「机制在框架自有资产里一个字面都不剩」**——保留一份
    镜像 / 副本 / 兼容读取「以防万一」，或「先留着下版再删」，都不算删干净。留下的每一处
    都会把读它的人送去找一个不存在的东西。

    **能力边界（这句要留，它是已知缺口的公开记录，不是墓碑）**：本闸只覆盖清单里那几条
    **字面**模式，**不覆盖中文散文形态的同义指代**（把同一件事换个说法写出来，本闸看不
    见）；也不覆盖用户项目自己要不要用 Claude Code 原生的那套能力——那是用户的事，扫描面
    因此只含框架自有资产。

    **台账键不算残留**：注入片 frontmatter 的 `supersedes` 与落点台账里的段落标识符，记的是
    被退役那批源**当时有哪些段**，是对账基准而非活指针。按**行形**排除（整行就是一个
    `"<文件名>.md#<段名>"` 字面量），不按整文件豁免——同一个文件里写一句散文指针照样红。

    **本文件自身在扫描面内**：模式清单那几行必然自命中，故只在本文件内豁免——但**豁免钉的
    是「引号里装的恰好是清单里的那一条」，不是行的形状**。按行形豁免会连同任何字符串列表
    元素一起放行，而**一份路径清单正是残留最可能的形状**（退役前的 `REQUIRED_RULE_MIRRORS`
    就是它）：实测往本文件塞一个把两条退役路径当列表元素写的常量赋值，整轮照绿、该项 OK 文案
    连「零命中」都照说。钉内容之后，模式清单增删时豁免面自动跟着走。
    **举例不写真字面**——本文件在扫描面内，举一次就造一处残留（本条改动本身就被它抓过一次）。
    """
    name = "no_rules_mechanism_residue"
    allow_by_file = {"CHANGELOG.md": _changelog_released_lines}
    datum_line = re.compile(r'^\s*"([^"]*)",\s*(?:#.*)?$')
    offenders, scanned, waived, ledger_keys = [], 0, 0, 0
    try:
        files = list(_residue_scan_files())
    except FileNotFoundError as e:
        return _fail(name, f"扫描面缺失: {e}——面整个消失时的「零残留」是空转，判红而非放行")
    if not files:
        return _fail(name, "扫描面一个文件都没取到——取材为空时的「零残留」是空转")
    for rel, text in files:
        scanned += 1
        self_scan = rel == "tools/validate.py"
        # **台账键的豁免必须同时限定位置**：那个行形只在注入片 frontmatter 的 `supersedes`
        # 与段落台账里合法。不限位置的话，任何文件里放一行同形字符串都会被静默豁免——
        # 与 `datum_line` 那处「钉了形状没钉内容」是同一个洞的另一半（实证：往
        # `workframe_doctor.py` 放一行台账键形态的真残留，整轮报绿，只有计数 +1）。
        ledger_face = rel.startswith("plugins/core/context/")
        allow_lines = allow_by_file[rel](text) if rel in allow_by_file else ()
        for lineno, line in enumerate(text.splitlines(), 1):
            m_datum = datum_line.match(line) if self_scan else None
            if m_datum and m_datum.group(1) in RULES_RESIDUE_PATTERNS:
                continue
            hits = [pat for pat in RULES_RESIDUE_PATTERNS if pat in line]
            if not hits:
                continue
            if ledger_face and RULES_RESIDUE_LEDGER_KEY.match(line):
                ledger_keys += 1
                continue
            if lineno in allow_lines:
                waived += 1
                continue
            offenders.append(f"{rel}:{lineno} 命中 {hits}: {line.strip()[:70]}")
    if offenders:
        return _fail(name, f"{len(offenders)} 处 rules 机制残留（扫了 {scanned} 份文件，"
                           f"{len(RULES_RESIDUE_PATTERNS)} 条字面模式）:\n    "
                           + "\n    ".join(offenders[:12])
                           + (f"\n    …另有 {len(offenders) - 12} 处" if len(offenders) > 12 else ""))
    return _ok(name, f"({scanned} 份文件 × {len(RULES_RESIDUE_PATTERNS)} 条字面模式零命中；"
                     f"已发布段段级豁免 {waived} 处、台账键按行形排除 {ledger_keys} 处；"
                     f"**不覆盖中文散文形态的同义指代**)")


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
    """两份清单、两个命令字段（`command` / `commandWindows`）都必须引用 `${CLAUDE_PLUGIN_ROOT}`。

    迭代面是 `_hook_field_entries`（为什么必须扩到 `commandWindows` 见其 docstring）。
    下限：字段数 ≥ 10——清单被清空时「全都引用了」是假绿。
    """
    name = "hooks_commands_reference_plugin_root"
    try:
        rows = [(door, *r) for door in HOOK_MANIFEST_RELS for r in _hook_field_entries(door)]
    except Exception as e:
        return _fail(name, f"hook 清单读取/解析失败: {e}")
    if len(rows) < 10:
        return _fail(name, f"两份清单只解析出 {len(rows)} 个命令字段（应 ≥10）——清单被清空时本闸假绿")
    for door, event, matcher, field, cmd in rows:
        if "${CLAUDE_PLUGIN_ROOT}" not in cmd:
            return _fail(name, f"[{door}] {event}({matcher or '*'}).{field} 不引用 ${{CLAUDE_PLUGIN_ROOT}}: "
                               f"{cmd!r}——插件根带版本号、升级即变，写死的路径会让会话静默执行"
                               f"另一个安装里的旧脚本")
    return _ok(name, f"({len(rows)} 个命令字段全部引用插件根变量，两份清单、两个字段)")



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
    """禁用非官方 frontmatter 字段：usage_count / last_used / trigger_count / category / when_to_use（仅顶层 frontmatter）。

    `when_to_use` 在列，是因为 Agent Skills spec 认的字段里，**描述「什么时候该调它」
    的只有 `description` 一个落点**（spec 字段为 name / description / license /
    compatibility / metadata / allowed-tools）。写进自定义字段的触发条件与边界反例，
    对只读 spec 字段的 runtime 不可见。四类信息一律并进 `description`。

    **覆盖边界**（写在这里免得它看起来比实际管得宽）：本闸只扫 `core/skills`、
    `core/rules`、`core/agents` 三个目录，**不扫 `templates/` 与 launcher**。所以它防得住
    「新写的 core skill 又长出 when_to_use」，防不住模板与 launcher 侧的同类回退——那两处
    目前靠 `check_launcher_plugin_structure` 的字段元组与出厂模板本身的形态兜。
    """
    forbidden = {"usage_count", "last_used", "trigger_count", "when_to_use"}
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
    """出厂 core agent 的 frontmatter 不应含硬编码 model ID（允许 inherit/opus/sonnet/haiku 别名）"""
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

    **只检 CC 那份清单，有意不扩到 Codex 清单**：本闸断言的是 11 段链路齐全，而 Codex 与 CC 的
    事件交集只有 7 个（`Setup` / `StopFailure` / `ConfigChange` / `UserPromptExpansion` 那边没有），
    扩过去恒红。Codex 清单的完整性由 `hooks_json_parseable`（事件集 == CC 事件 − 缺口事件）
    与 `codex_hooks_count` / `codex_hooks_generated_in_sync` 管——它是派生物，链路齐不齐取决于
    这一份。
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
    """shared 记忆的启动契约现位于**子 agent 注入片**——它是这条契约唯一的消费方。

    契约本身没变（两份记忆由 hook 注入、有标记段就别再 Read、无标记段按清单兜底），
    变的是它送到谁手里。**钉在 sub 片而不是 both 片是有意的**：主会话那半是「恒走
    显式 Read」，与本条相反，钉错片会让两条相反的契约互相顶替而闸照样绿。
    """
    path = CONTEXT_DIR / "sub" / "10-sub-protocol.md"
    if not path.exists():
        return _fail("shared_agent_contract", "sub/10-sub-protocol.md 不存在——子 agent 侧的启动契约无处承载")
    text = path.read_text(encoding="utf-8")
    if "shared/MEMORY.md" not in text:
        return _fail("shared_agent_contract", "sub 片里没有 shared 记忆的启动契约——子 agent 会不知道那两份"
                     "记忆已由 hook 注入，要么重复 Read、要么以为没有而跳过")
    return _ok("shared_agent_contract", "(in context/both/20-protocol-common.md)")


# === v0.2.1 checks ===


# 一个插件可能同时有几份 manifest，**每一份都是一个版本号落点**。顺序 = 对账时的枚举顺序，
# 第 0 项是 CC 那份（本仓每个插件都必须有）。加一种新形态只改这里，别在各闸里各写一份。
#   · `.claude-plugin/plugin.json`  Claude Code 清单
#   · `plugin.json`                 Agent Plugins 规范的根清单（Codex 当前读它）
#   · `.codex-plugin/plugin.json`   Codex 的 compatibility fallback
PLUGIN_MANIFEST_RELS = (".claude-plugin/plugin.json", "plugin.json", ".codex-plugin/plugin.json")

# Agent Plugins 根 manifest 的**闭集**顶层字段（规范 §5.2：其余顶层字段客户端「报告并忽略」）。
AGENT_PLUGIN_MANIFEST_KEYS = frozenset({
    "$schema", "name", "version", "description", "author", "homepage", "repository",
    "license", "keywords", "extensions"})

# `$schema` 的取值**不是装饰**：规范 §5.2 要求客户端按它挑选本地的校验与解释规则，不认识就拒绝。
# 本机实测（codex-cli 0.153.4，隔离 CODEX_HOME、独立市场副本）：
#   · 声明 1.0.0 ⇒ `codex plugin list --available` 报出 manifest 里的 version，`plugin add` 装得上；
#   · 声明 1.1.0 ⇒ **同一份 manifest 的 version 变成 `null`**，不报错、不拒绝，只是静默丢读数。
# ⇒ 只钉本机验过能用的那个版本；要升到 1.1.0 得先在真机上重跑这组探针。
AGENT_PLUGIN_SCHEMA_ID = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"

# **必须带根 manifest 的插件**（其余插件仍然是选配）。用户 2026-09-22 拍板只钉 launcher 一个。
# 为什么要钉：实测 codex-cli 0.153.4 下，根 manifest 在场时 Codex 报的 version **取自它**
# （根清单写 `9.9.9`、marketplace 条目写 `1.1.0` ⇒ Codex 报 `9.9.9`），插件缓存目录也按它分版本。
# 而不钉的话它可以**静默消失**：删掉之后本闸报「无对象」放行、`check_version_consistency` 的落点
# 从 8 降到 7 且剩下的仍全等 —— 两道闸全绿，而 launcher 在 Codex 侧的版本号读数已经换了来源。
REQUIRED_ROOT_MANIFEST_PLUGINS = ("workframe-launcher",)


def check_root_plugin_manifest():
    """现扫 `plugins/*/plugin.json`（Agent Plugins 规范的根清单）：`$schema` 是本机验过的那个、
    顶层字段在闭集内、`name` 与同目录 `.claude-plugin/plugin.json` 相等、`skills/` 在。

    **`REQUIRED_ROOT_MANIFEST_PLUGINS` 里的插件必须有这份清单，其余插件有则查**（形状与
    `codex_plugin_manifest_twin` 里 core 的 `.codex-plugin` 那一支相同）。一个插件都没有根清单
    也不是「没有对象」时的合法态——那一支只在硬要求清单为空时才可达。

    四条各自防什么（都是**静默**失效，没有一条会自己报错）：
      · **`$schema` 取值**：见 `AGENT_PLUGIN_SCHEMA_ID` 上面那段实测——写错版本号不会被拒绝，
        只是 Codex 从此读不到 version。而版本号读不到 ⇒ 缓存目录分版本失效、升级对账失去依据。
      · **顶层字段闭集**：规范把根清单定成闭集，未知字段客户端「报告并忽略」。所以在这里写
        `"skills": "./skills/"` 或 `"hooks": …`（`.codex-plugin` 那份的写法）**不会红、也不会生效**，
        而写的人会以为配好了。客户端专属数据的正确落点是 `extensions.<反向域名>`。
      · **`name` 与 CC 清单相等**：不等 ⇒ 两扇门把同一份代码认成两个插件。
      · **`skills/` 目录在**：规范按**固定位置**发现组件，根清单里没有也不该有指向 skills 的字段
        ⇒ 目录不在就是零 skill，而清单本身完全合法。

    **不覆盖**：`version` 的锁步对账在 `check_version_consistency`（它现扫全部 manifest 形态）；
    `extensions.<命名空间>` 的内容由各客户端自己定义、规范明说不赋予语义，本闸不碰。
    """
    name = "root_plugin_manifest"
    for pname in REQUIRED_ROOT_MANIFEST_PLUGINS:
        req = FRAMEWORK_ROOT / "plugins" / pname / "plugin.json"
        if not req.is_file():
            return _fail(name, f"{_rel(req)} 不存在，而 {pname} 被列为**必须带根 manifest**——"
                               f"Codex 报的 version 就是从这份清单读的（实测：它与 marketplace 条目"
                               f"不一致时以它为准），没有它时 launcher 在 Codex 侧的版本号换了来源，"
                               f"而**两道版本闸都不会红**（本闸放行、锁步闸少一个落点且剩下的仍全等）。"
                               f"⚠️ **如果你是有意拿掉这个形态**（判定它选错了），那就先把 "
                               f"`REQUIRED_ROOT_MANIFEST_PLUGINS` 里的 {pname!r} 去掉——这道红不是 bug，"
                               f"是这条硬要求被知情接受时一起接受的代价")
    found = []
    for pdir in sorted(p for p in (FRAMEWORK_ROOT / "plugins").iterdir() if p.is_dir()):
        mf = pdir / "plugin.json"
        if not mf.is_file():
            continue
        try:
            data = json.loads(mf.read_text(encoding="utf-8"))
        except Exception as e:
            return _fail(name, f"{_rel(mf)} 解析失败: {e}")
        if not isinstance(data, dict):
            return _fail(name, f"{_rel(mf)} 顶层不是 JSON 对象")
        got_schema = data.get("$schema")
        if got_schema != AGENT_PLUGIN_SCHEMA_ID:
            return _fail(name, f"{_rel(mf)} 的 `$schema` 是 {got_schema!r}，应为 {AGENT_PLUGIN_SCHEMA_ID!r}"
                               f"——实测写成别的版本号时 Codex **不报错也不拒绝**，只是从此把这个插件的"
                               f"version 读成 null（缓存分版本与升级对账一起失效）")
        extra = sorted(set(data) - AGENT_PLUGIN_MANIFEST_KEYS)
        if extra:
            return _fail(name, f"{_rel(mf)} 有闭集之外的顶层字段 {extra}——规范要求客户端**报告并忽略**"
                               f"它们，所以写在这里既不会红也不会生效；客户端专属数据放 "
                               f"`extensions.<反向域名>` 下")
        cc_path = pdir / PLUGIN_MANIFEST_RELS[0]
        try:
            cc = json.loads(cc_path.read_text(encoding="utf-8"))
        except Exception as e:
            return _fail(name, f"{_rel(cc_path)} 读不出来（根 manifest 在就必须有它对账）: {e}")
        if cc.get("name") != data.get("name"):
            return _fail(name, f"{pdir.name} 的根 manifest 与 CC 清单 `name` 不等："
                               f"claude={cc.get('name')!r} / root={data.get('name')!r}"
                               f"——两扇门会把同一份代码认成两个插件")
        if not (pdir / "skills").is_dir():
            return _fail(name, f"{_rel(pdir)} 有根 manifest 却没有 `skills/`——规范按固定位置发现组件，"
                               f"清单里没有也不该有指向 skills 的字段，所以目录不在就是零 skill，"
                               f"而清单本身完全合法、不会有任何报错")
        found.append(f"{pdir.name} v{data.get('version')}")
    if not found:
        return _ok(name, "(plugins/ 下没有任何根 plugin.json，本闸无对象——这个形态对 "
                         "`REQUIRED_ROOT_MANIFEST_PLUGINS` 之外的插件是选配的)")
    return _ok(name, f"({len(found)} 份根 manifest：" + "；".join(found)
                     + f"；$schema 全部为 {AGENT_PLUGIN_SCHEMA_ID.rsplit('/', 2)[-2]}"
                     + f"；硬要求 {list(REQUIRED_ROOT_MANIFEST_PLUGINS)}，其余插件选配)")


def check_version_consistency():
    """两个插件的 plugin.json、各自的 marketplace 条目、market metadata、README status 版本号一致。

    **本仓采用锁步发版**——任何一次发布，两个插件一起 bump 到同一个版本号。

    为什么锁步而不是各自独立：V4 实测确认「用户装的是插件、不是市场，version 是**每个插件
    各自的**更新信号」——改了 core 却只 bump launcher，core 的用户会**静默**拿不到更新。
    独立版本号更精确，但要求每次都记得 bump 对的那个，而漏 bump 的失败是无声的。
    锁步让这个失败**在结构上不可能发生**：反正两个都 bump，就不存在「bump 错了」。
    代价是没改的那个插件也会推一次空更新——两个插件的仓库，这点噪音换一个不会翻的车，划算。

    所以这条检查要求**全部相等**，不是各自对齐即可。

    **插件清单与各自的 manifest 形态都现扫，不写死**（`PLUGIN_MANIFEST_RELS`）：
      · 插件目录来自 `plugins/*`——写死成两条时，**第三个插件的版本号不进锁步链**（它的两份
        manifest 可以自洽而版本 ≠ core，两条闸全绿）；
      · 每个插件的 manifest 形态来自 `PLUGIN_MANIFEST_RELS`——`.claude-plugin/plugin.json`（CC，必需）、
        根 `plugin.json`（Agent Plugins 形态，在则收）、`.codex-plugin/plugin.json`（Codex 兼容回退，在则收）。
        **根 manifest 那一份是承重的**：实测 codex-cli 0.153.4 下，它在场时 `codex plugin list` 报的
        version **取自它、不取 marketplace 条目**（把它改成 `9.9.9` 而 marketplace 仍是 `1.1.0` ⇒ 列出 `9.9.9`），
        插件缓存目录也按它分版本。⇒ 它漏 bump 就是「用户那边版本号原地不动、而代码已经换了」。

    形态清单现扫之后，**加一种新的 manifest 形态只需改 `PLUGIN_MANIFEST_RELS` 一处**。

    **本闸只管 version**，各形态自己的形态规则在别处：`.codex-plugin` 的 `name` / `hooks` 在
    `codex_plugin_manifest_twin`（它对 `version` 另有一条从 `.claude-plugin` 独立推出的断言，
    那是**有意的交叉冗余**）；根 manifest 的 `$schema` / 闭集字段 / `name` / `skills/` 在
    `check_root_plugin_manifest`。
    """
    name = "version_consistency"
    versions = {}
    try:
        mj = json.loads((FRAMEWORK_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    except Exception as e:
        return _fail(name, f"marketplace.json read: {e}")
    versions["marketplace.metadata"] = mj.get("metadata", {}).get("version")
    entries = {p.get("name"): p for p in mj.get("plugins", [])}

    plugin_dirs = sorted(p for p in (FRAMEWORK_ROOT / "plugins").iterdir() if p.is_dir())
    if not plugin_dirs:
        return _fail(name, "plugins/ 下一个插件目录都没有——本断言没有对象等于恒绿")
    for pdir in plugin_dirs:
        cc_rel = PLUGIN_MANIFEST_RELS[0]
        if not (pdir / cc_rel).is_file():
            return _fail(name, f"{_rel(pdir)} 没有 {cc_rel}——它在 plugins/ 下却不是一个 CC 插件；"
                               f"是插件就补上清单，不是插件就别放在这里（本闸按目录现扫）")
        for rel in PLUGIN_MANIFEST_RELS:
            pj_path = pdir / rel
            if not pj_path.is_file():
                continue
            try:
                versions[f"{pdir.name}/{rel}"] = json.loads(
                    pj_path.read_text(encoding="utf-8")).get("version")
            except Exception as e:
                return _fail(name, f"{_rel(pj_path)} read: {e}")
        entry = entries.get(pdir.name)
        if entry is None:
            return _fail(name, f"marketplace.json 未列出插件 {pdir.name}——它不会被分发，"
                               f"而本仓每个 plugins/ 下的目录都该是一个被分发的插件")
        versions[f"marketplace.{pdir.name}"] = entry.get("version")

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
    return _ok(name, f"({len(versions)} 个版本号落点现扫全等 @ {unique.pop()}："
                     + "、".join(sorted(versions)) + ")")


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


def check_pm_retention_matches_events_window():
    """pending 条目的 GC 保留期同时锚住两个触发常量，且两侧都真的在用那个常量。

    为什么必须锚住：任何 kind 被驳回后不再重开，靠的都是「同 dedup_key 的 closed 条目
    的 closed_at」当计分基线（`check-iteration-trigger.dismissal_baseline()`）。而那条
    closed 条目在 `closed_at + PM_CLOSED_RETENTION_DAYS` 天后被 SessionStart GC 删除
    ——**保留期就是抑制能维持多久**。两条约束由此而来：

      == EVENTS_WINDOW_DAYS （事件流类 problem / activity）
         相等时，条目被删的同一时刻 closed_at 之前的事件也恰好全部滑出窗口——抑制
         记忆与它要抑制的对象同时消失，交接无缝。撕开缝的后果不对称：
           窗口 > 保留期 → 基线先没、旧事件还在窗口里 → 已驳回的账重新计分，次日复活
           窗口 < 保留期 → 基线多留几天，那时窗口内已无旧事件 → 只是冗余，无害
      >= CADENCE_DAYS （日期基线类 cadence_timeout）
         cadence 驳回后的静默期同样只有保留期那么长，而它一到期就按老的 proposals
         日期重算。保留期 < 周期时，条目被删的那天天数早已超标，提醒当场复活——
         缺陷只是被推迟，没被修掉。反向（保留期更长）只是多静默几天，无害，故用
         `>=` 不用 `==`：漏判它不构成真实功能缺陷。

    四层断言，缺任一层都留一条完整的漏网通道：
      ① 三个常量都抽得到——抽不到就报红，不静默跳过。提取器扫 0 时闸会「看起来很绿」，
         而那正是它彻底失明的样子
      ② 窗口 == 保留期（`==` 而非任何包含关系：这是两个标量，漏判就是上面那个后果）
      ③ 两个**消费点**确实引用常量而非字面量——少了这层，把 `days=EVENTS_WINDOW_DAYS`
         改回 `days=14` 时前两层照样全绿，闸对账的成了一个没人用的数字
      ④ 保留期 >= cadence 周期

    **明写一处不做的**：③ 没有 cadence 侧的对应层（不查 `>= CADENCE_DAYS` 的比较点是否
    写成了字面量）。窗口侧能做是因为它有唯一且形态固定的消费入口
    `read_recent_events(days=…)`；cadence 是裸比较 `x >= CADENCE_DAYS`，写成 `x >= 7`
    与文件里其他任何数字比较无法区分——那层判定要么假红要么恒绿，装上去只会让人以为
    这一侧被咬住了。这条缺口靠常量旁注释与 code review 兜底，不靠本闸。
    """
    name = "pm_retention_matches_events_window"
    scripts = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    trig = scripts / "check-iteration-trigger.py"
    prep = scripts / "session-start-prep.py"
    for p in (trig, prep):
        if not p.exists():
            return _fail(name, f"脚本缺失: {p}")
    trig_src = trig.read_text(encoding="utf-8")
    prep_src = prep.read_text(encoding="utf-8")

    # ① 常量抽取（抽不到 = 闸失明，必须红）
    m_win = re.search(r"^EVENTS_WINDOW_DAYS\s*=\s*(\d+)", trig_src, re.M)
    m_ret = re.search(r"^PM_CLOSED_RETENTION_DAYS\s*=\s*(\d+)", prep_src, re.M)
    m_cad = re.search(r"^CADENCE_DAYS\s*=\s*(\d+)", trig_src, re.M)
    if not m_win:
        return _fail(name, "check-iteration-trigger.py 抽不到 EVENTS_WINDOW_DAYS 赋值——"
                           "常量被改名/改形态则本闸静默失明，先修提取或恢复常量")
    if not m_ret:
        return _fail(name, "session-start-prep.py 抽不到 PM_CLOSED_RETENTION_DAYS 赋值——"
                           "常量被改名/改形态则本闸静默失明，先修提取或恢复常量")
    if not m_cad:
        return _fail(name, "check-iteration-trigger.py 抽不到 CADENCE_DAYS 赋值——"
                           "常量被改名/改形态则本闸的 cadence 一侧静默失明，"
                           "先修提取或恢复常量")
    win, ret, cad = int(m_win.group(1)), int(m_ret.group(1)), int(m_cad.group(1))

    # ② 两值相等
    if win != ret:
        worse = ("窗口 > 保留期：驳回基线会先被 GC 删掉，而旧事件还在窗口里，"
                 "已驳回的 problem_threshold / activity_threshold 次日原样复活"
                 "（两者同走 read_recent_events + event_after 这条事件流路径）"
                 if win > ret else
                 "窗口 < 保留期：驳回基线多留几天，窗口内已无旧事件，仅冗余无害")
        return _fail(name, f"EVENTS_WINDOW_DAYS={win} != PM_CLOSED_RETENTION_DAYS={ret}；{worse}")

    # ③ 消费点确实用常量（否则常量成摆设，本闸对账一个没人用的数字）
    #
    # 判的是**每一个调用点**而非「存在一个」：漏掉任一处就等于那条路径的窗口脱离对账，
    # 而这正是本闸要防的形态。定义行必须排除——`def read_recent_events(days=EVENTS_WINDOW_DAYS)`
    # 的默认值会满足任何「文件里出现过该写法」式的探测，于是把调用点改成
    # `read_recent_events(days=14)` 时闸照样绿（实测过这个假绿，故改成逐调用点判定）。
    # 走共用件 `_symbol_use_sites`，本函数不再自留第二份实现（此前这里是全仓第二份
    # 同契约实现：它知道「排除 def 行」而共用件当时不知道，共用件知道「函数级 scope」
    # 而它不知道——两份是交叉关系，已取并集合并到共用件里）。
    # mode="all"：这里每一处调用都必须走常量，漏一处就等于那条路径的窗口脱离对账；
    # 与「同一取值函数被 4 个字段各调一次」那类 ⊆ 场景不同。
    # 分布实核：全文 3 处出现，def 头部 1 处 + 注释里 1 处 + 真实调用 1 处 ⇒ 1 处承重。
    calls, bad, err = _symbol_use_sites(trig_src, "read_recent_events",
                                        paired_with="days=EVENTS_WINDOW_DAYS", mode="all")
    if err:
        return _fail(name, f"check-iteration-trigger.py: {err}")
    if not calls:
        return _fail(name, "check-iteration-trigger.py 找不到 read_recent_events 的调用点——"
                           "提取器扫 0 时本闸会静默变绿，先确认调用形态")
    if bad:
        return _fail(name, f"read_recent_events 有 {len(bad)}/{len(calls)} 处调用没走 "
                           f"EVENTS_WINDOW_DAYS（窗口写成字面量时本闸对账的是个摆设常量）: "
                           f"{bad[0][:80]}")

    # GC 侧同理走共用件，函数级 scope 由它承担（session-start-prep 里 timedelta(days=…)
    # 不止一处，drift 历史等）。分布实核：该常量在 gc_pending_maintenance 内 2 处出现，
    # 其中 1 处在 docstring ⇒ 1 处承重。mode="all" 理由同上。
    gc_sites, gc_bad, gc_err = _symbol_use_sites(
        prep_src, "PM_CLOSED_RETENTION_DAYS", func="gc_pending_maintenance",
        paired_with="timedelta(days=", mode="all")
    if gc_err:
        return _fail(name, f"session-start-prep.py: {gc_err}——GC 实现改名/移走则本闸的"
                           f"保留期一侧失明")
    if not gc_sites:
        return _fail(name, "gc_pending_maintenance 里没有一处引用 PM_CLOSED_RETENTION_DAYS"
                           "——保留期写成字面量时本闸对账的是个摆设常量")
    if gc_bad:
        return _fail(name, f"gc_pending_maintenance 的 cutoff 没走 timedelta(days="
                           f"PM_CLOSED_RETENTION_DAYS)（{gc_bad[0][:80]}）——保留期写成"
                           f"字面量时本闸对账的是个摆设常量")

    # ④ 保留期 >= cadence 周期（驳回 cadence 后的静默期不能短于提醒周期本身）
    if ret < cad:
        return _fail(name, f"PM_CLOSED_RETENTION_DAYS={ret} < CADENCE_DAYS={cad}；"
                           f"驳回 cadence_timeout 后第 {ret + 1} 天条目就被 GC 删掉，"
                           f"而那时距上次提案还不足 {cad} 天的条件早已满足——提醒当场"
                           f"复活，dismissal_baseline() 的抑制等于没做（缺陷只被推迟，"
                           f"没被修掉）。要么调大保留期，要么调小 cadence 周期")
    return _ok(name, f"(窗口 == 保留期 == {win} 天 >= cadence {cad} 天；"
                     f"{len(calls)} 处窗口调用与 GC cutoff 均引用常量)")


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

# 业务领域 skill 名。**语义随 `skills:` 预载退役而变**：从前它是「core agent body 里的
# 违禁词（仅 frontmatter `skills:` 列表允许）」，现在它是
# `check_agents_no_business_skill_in_body` 第三层差集的**宽候选面**——正文里出现这些名字
# 却没用可识别的点名形态写，就是漂出前两层射程的那一格。
BUSINESS_SKILL_NAMES = {
    "requirement-analysis", "feature-breakdown", "acceptance-criteria",
    "competitive-analysis", "user-feedback-analysis", "product-metrics-design",
    "technical-design", "systematic-debugging",
    "test-case-design", "code-review",
    "prompt-design", "prompt-evaluation",
}

# 签发契约的消费方 skill。唯一消费方是 `check_signoff_consumers_carry_pointer`（判「它们身上
# 有没有判据指针」，并反向对账「谁身上带着锚点」与本清单相等）。
# **「有没有人来读」这一层没有机器面**：七个里只有 `task-management` 由子 agent 必载片固定
# 植入（`check_sub_protocol_pins_task_management` 钉那句规矩），其余六个靠 skill 清单的
# description 触发——这是知情接受的能力边界，不是遗漏。
SIGNOFF_CONSUMER_SKILLS = (
    "task-management", "test-case-design", "technical-design", "code-review",
    "prompt-design", "systematic-debugging", "feature-breakdown",
)

# 签发判据落点的小节锚点。它是「契约标识符」而不是普通字符串：抽成常量，是为了改节名时
# 只有一处要改——调用方各写一份字面的话，改了节名只会改到一处，另一处对着旧锚点报绿。
SIGNOFF_PTR_ANCHOR = "谁签发这次收口"

# 「什么时候调哪个 skill」的触发句式词表。触发条件的唯一源是各 skill 自己的 description
# （经会话的 skill 清单到达模型），出厂 agent 正文里出现这批句式 = 触发条件长出了第二个源：
# 两者矛盾时子 agent 按 description 判，正文那句要么多余、要么把人引向与实际行为相反的方向。
#
# **扫描面严格限定在 `agents/*.md`，这是自伤路径，别顺手扩面。** 同一批句式在**注入片与
# skill 正文里是规范**（必载片写「动看板之前先调 skill …」正是它们的本职），在**出厂 agent
# 正文里是 offender**——两处作用域相反且是刻意的。扩面到「所有 md」等于把规范判成违规，
# 且注入片一直在仓里，**扩面后当场恒红**。
#
# `时调` / `前调` 是裸子串，会连「何时调用」「动手前调研」一起命中。**方向有意 fail-safe
# 偏多报**：漏掉一条真映射的代价是两个源静默分叉，多红一次的代价是把那句话换个说法。
# 反引号里的裸 token。**单独一条、不与 `_POINT_SKILL_RES` 合并**：那批认的是「带标记的点名」，
# 这条认的是「反引号里的一个词」，两者是宽严关系而不是同类。宽面还要与**文件系统现算的真实
# skill 名**取交集，所以它不会把普通术语卷进来，也不需要手写词表——加了新 skill 自动进宽面。
# **反引号内的前缀形态必须与 `_POINT_SKILL_RES` 的反引号那几条同步**（斜杠写法 `/core:x` 不在
# 宽面内，那一条严判被删时本层兜不住——已知边界）：严判认 `core:` 前缀而宽面不认时，全名写法
# 整个落在宽面之外，「严判被改窄」这件事宽面就兜不住了（实测：正文写 `skill \`core:x\``、
# 再把严判的前缀收回去，闸照绿）。捕获组同样只取前缀之后的名字。
_BACKTICK_TOKEN_RE = re.compile(r"`(?:core:)?([a-z0-9][a-z0-9-]*)`")

_BODY_TRIGGER_RES = (
    re.compile(r"时调"),
    re.compile(r"前调"),
    re.compile(r"→\s*调\s*skill"),
    re.compile(r"调用\s*skill\s*`"),
)


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
            m = re.match(r"\s*-\s*([\w\-:]+)\s*$", line)   # 含 `core:` 全名写法
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


def _tools_declarations(text):
    """扫出一份 md 里**全部** `tools:` 声明，返回 `(声明, 解析不了的候选)`。

    - 声明：`[(行号, 工具名 set)]`，工具名 set **恒非空**
    - 解析不了的候选：`[(行号, 成因)]`，成因取 `"inline"`（冒号后的内容不认）或
      `"block"`（冒号后为空但底下没有一条缩进条目）

    与 `_agent_skills` 分工不同、别合并：那个只取 frontmatter 第一段、只返回一个 set，
    因为一份 agent 只有一个 `skills:`；而 `tools:` 样板在 reference 文档里是**多处并存**的
    （role-customization-guide 有 3 处），只取第一处等于放过其余两处。

    块式与单行流式都认——两种写法在扫描面里都真实存在（agents 与模板用块式、指南的最小
    示例用单行流式）。只认其一的解析器会静默漏掉另一半，而漏掉与合格在终端里长得一样。

    **行首 `tools:` 是宽候选集，认得出的形态只是它的严格子集，两者的差就是第二个返回值。**
    这个差以前被 `continue` 静默丢弃：在期望恰 1 处的扫描面上，丢弃会让计数变化、由计数
    断言兜成红（fail-safe）；但在只判「≥1」的扫描面上，丢弃不改变成立与否，**闸继续报绿**
    ——`tools: [` 换行续写与 `tools: Read, Write` 标量式都从这里漏过去过。所以不再丢，
    交调用方点名报红；本函数**有意不去支持**这些写法，写法收敛比解析器变宽更省事。

    块内允许注释行与行尾注释（模板用它写「若需联网：加 WebSearch / WebFetch」）；遇到第一行
    既非条目也非缩进注释即收束，所以顶格的 `# …` 与 `---` 都能正确断开。`allowed-tools:`
    是 skill 层的另一套机制，行首锚定天然不命中，不在本函数管辖内。
    """
    lines = text.splitlines()
    out, unparsed = [], []
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)tools\s*:(.*)$", line)
        if not m:
            continue
        rest = m.group(2)
        inline = re.match(r"\s*\[([^\]]*)\]", rest)
        if inline:
            names = {t.strip().strip("'\"") for t in inline.group(1).split(",")}
            names = {n for n in names if n}
            if names:
                out.append((i + 1, names))
            else:
                unparsed.append((i + 1, "inline"))   # `tools: []` 空清单同样无可断言
            continue
        if rest.strip() and not rest.strip().startswith("#"):
            unparsed.append((i + 1, "inline"))   # 多行流式 / 标量式 / 散句，一律不认
            continue
        names = set()
        for follow in lines[i + 1:]:
            if not follow.strip():
                break
            entry = re.match(r"^\s+-\s*[\"']?([\w\-{}]+)[\"']?\s*(?:#.*)?$", follow)
            if entry:
                names.add(entry.group(1))
                continue
            if re.match(r"^\s+#", follow):
                continue      # 块内缩进注释
            break
        if names:
            out.append((i + 1, names))
        else:
            unparsed.append((i + 1, "block"))
    return out, unparsed


def check_agents_no_business_skill_in_body():
    """出厂 agent **不承载 skill 触发映射**（正文与 frontmatter 都不），且正文提到的 skill 名指得到真东西。

    **闸 id 保留不改，判据已整体换掉。** 旧判据是「正文不得出现业务 skill 名，除非它在自己
    frontmatter `skills:` 里」——那条豁免随预载退役而失效，改口后它会对着正确的正文报红。
    处置是换判据不是加白名单。id 之所以留着：账本与既往签注按 id 索引本闸，改了那些引用
    就成死引用；**但名字本身此刻已不完全描述判据**（正文现在允许出现业务 skill 名，只要
    形态可识别且解析得到），所以判据以本 docstring 与红灯 / OK 文案为准，别按名字反推。

    **触发条件的唯一源是各 skill 的 description**（经 skill 清单到达模型）；出厂角色没有按角色
    的 skill 映射表，唯一固定植入的 `core:task-management` 那句在子 agent 必载片里、对所有
    子 agent 一样。所以 agent 文件里任何「什么时候调哪个 skill」都是第二个源。

    **五层，各管一段，缺任一层都留一条完整的漏网通道**：

    | 层 | 抓什么 | 缺了它会怎样 |
    |---|---|---|
    | ① 存在性 | 正文里**认得出的**点名，每个都解析到真实 skill 目录 | 正文把人指向一个不存在的 skill，模型多半挑个名字最近的调，失败与「模型选错」同形 |
    | ② 触发句式 | 正文不得含 `_BODY_TRIGGER_RES` 那批句式 | 「正文里塞一条与 description 不同的触发条件」恒绿，触发条件长出第二个源并静默分叉（v1 单条判据实证过这个洞） |
    | ③ 宽收严判差集 | 业务 skill 名出现在正文、却不在①的射程内 | ①②共同够不着的那一格：不带任何标记地写「需求澄清用 requirement-analysis」——没有触发词、没有反引号，两条判据都看不见它 |
    | ④ 反引号差集 | 反引号里的真实 skill 名不在①的射程内 | 射程本身被改窄时①的命中数净减少而①②③都不响 |
    | ⑤ 无预载 | 出厂 agent frontmatter 不得含 `skills:` | 预载把列出的 skill 全文每次派发整段装进上下文，且与 skill 清单叠成两套导向；出厂角色不用它 |

    **①的射程比「每个点名」窄，这句不能省**：它复用 `_POINT_SKILL_RES`（与注入片那条闸
    同一份正则，**有意不另写也不照抄**），只认那几种带标记的形态；**裸反引号**提到的名字
    逃出射程。③把这条缝补上了一半——**只补到 `BUSINESS_SKILL_NAMES` 这个手写宽面为止**，
    宽面之外的 skill 名（`prd-writer` / `module-init` 等）裸着写仍然三层都够不着。这是当前
    的能力边界，不是「已覆盖」。

    **这条边界句本身被订正过一次，形态值得记住**：早先它写的是「裸反引号逃出射程」，而
    实际连 `skill: \\`x\\`` 这种带标记的冒号形态也在射程外（`skill\\s+` 卡在冒号上），于是
    `qa.md` / `prompt-eng.md` 的射程命中数曾是 0 而闸照绿——**陈述比实际射程宽，比射程窄
    本身更危险**：读的人据此以为带标记就被看住了。

    路径引用（`plugins/core/skills/<skill>/reference/xxx.md`）在三层之前一律剥掉——那是
    文件路径不是调用引导，且另有 `check_agents_no_plugin_internal_path` 专管它。
    """
    name = "agents_no_business_skill_in_body"
    # **扫描面严格限定在出厂 agent**：扩面即自伤，成因见 `_BODY_TRIGGER_RES` 旁注
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    skills_dir = FRAMEWORK_ROOT / "plugins" / "core" / "skills"
    files = sorted(agents_dir.glob("*.md"))
    if not files:
        return _fail(name, "agents/ 目录空或缺失——无对象可查，这不等于检查通过")
    PATH_REF_RE = re.compile(r"(?:plugins/core/)?skills/[\w\-]+/\S*")
    # ④ 的宽面**从文件系统现算**，不手写：加了新 skill 它自动进宽面，删了自动出
    real_skills = {p.name for p in skills_dir.iterdir() if (p / "SKILL.md").is_file()} \
        if skills_dir.is_dir() else set()
    problems, n_named = [], 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        body = _agent_body(text)
        body_no_paths = PATH_REF_RE.sub("", body)

        # ⑤ 无预载（先判：它看 frontmatter，与后面几层看正文互不依赖）
        parts = text.split("---", 2) if text.startswith("---") else []
        fm = parts[1] if len(parts) >= 3 else ""
        if re.search(r"^skills\s*:", fm, re.MULTILINE):
            still = sorted(_agent_skills(text))
            if still:
                problems.append(f"⑤ {f.name} frontmatter 预载着 {still}——出厂角色不用 `skills:`："
                                f"它每次派发都把列出的 skill 全文整段装进上下文，且与 skill 清单"
                                f"叠成两套导向。把 `skills:` 整段移除")
            else:
                problems.append(f"⑤ {f.name} frontmatter 里 `skills:` 键还在但解析不出条目"
                                f"（`skills: []` 或本解析器不认的写法）——键在就有预载的入口，整段移除")

        # ① 存在性
        named = set()
        for rx in _POINT_SKILL_RES:
            named |= set(rx.findall(body_no_paths))
        for nm in sorted(named):
            n_named += 1
            if not (skills_dir / nm / "SKILL.md").is_file():
                problems.append(
                    f"{f.name}: 正文点名 skill `{nm}`，但 `plugins/core/skills/{nm}/SKILL.md` "
                    f"不存在——subagent 会去调一个没有的 skill，多半改挑一个名字最近的，"
                    f"失败与「模型选错 skill」同形")

        # ② 触发句式
        for lineno, line in enumerate(body.splitlines(), 1):
            for rx in _BODY_TRIGGER_RES:
                if rx.search(line):
                    problems.append(
                        f"{f.name}:body+{lineno}: 触发句式 `{rx.pattern}` 命中 —— "
                        f"「什么时候调哪个 skill」的唯一源是该 skill 的 description，正文写一份"
                        f"就是第二个源，两者矛盾时子 agent 按 description 判、正文那句失真："
                        f"{line.strip()[:60]}")

        # ③ 宽收严判差集（宽面 = 手写的业务 skill 名，抓**不带反引号**地裸写）
        stray = sorted({s for s in BUSINESS_SKILL_NAMES if s in body_no_paths} - named)
        for nm in stray:
            problems.append(
                f"{f.name}: 正文提到 `{nm}` 却不在①的射程内 —— 改写成 "
                f"「`{nm}` skill」或「skill `{nm}`」这类可识别形态，否则它改名 / 删除时"
                f"本闸不会响，而正文照旧把人指过去")

        # ④ 第二个差集，宽面**从文件系统现算**——抓「净减少」，这是③接不住的那一格
        backticked = {n for n in _BACKTICK_TOKEN_RE.findall(body_no_paths) if n in real_skills}
        for nm in sorted(backticked - named):
            problems.append(
                f"{f.name}: 反引号里的 `{nm}` 是一个真实 skill 名，却不在①的射程内 —— "
                f"补成「skill `{nm}`」或「`{nm}` skill」。**这一层专抓射程本身被改窄**："
                f"把 `_POINT_SKILL_RES` 收回去（例如去掉冒号形态）时，①的命中数会净减少而"
                f"①②③三层都不会响——严面缩了、宽面没缩，只有这个差集会红")

    if problems:
        return _fail(name, f"{len(problems)} 项：\n    " + "\n    ".join(problems[:8]))
    if n_named == 0:
        # 取材面非空却一个点名都没认出来：正则失明与「正文真的一个 skill 都没提」同形，
        # 而前者会让①③两层从此恒绿（③的差集也会因 named 恒空而全落进 stray，所以这条
        # 下限实际只在「正文真的零提及」时才走得到——它是那一格的显式声明，不是兜底）
        return _ok(name, f"（{len(files)} 份出厂 agent 正文零 skill 点名；"
                         f"②触发句式与③差集仍已跑过，两者对零提及恒成立；⑤零 `skills:` 预载）")
    return _ok(name, f"（{len(files)} 份出厂 agent：①{n_named} 处可识别点名全部解析得到、"
                     f"②零触发句式、③④ skill 名零漂出射程、⑤零 `skills:` 预载）")


def check_agents_no_dispatch_language():
    """出厂 core agent body 不出现 '通知 @X' 派发式语句（应改为响应文字标注 + 状态机）"""
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
    """出厂 core agent body 不内联 hook event payload 样板（应抽到注入片）

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


def check_agent_tools_include_skill():
    """agent 的 `tools:` 白名单必须含 `Skill`——出厂角色 / 模板基线 / 指南样板三面都要。

    立案实测（2026-09-02）：4 个 core agent 的 tools 列了 9 个工具、独缺 `Skill`。CC 的
    `tools` 是**白名单**，`Skill` 不在其中则该 subagent 完全无法调用任何 skill。危害不止
    「预载之外的 skill 用不上」：rules 每会话必载且对所有 agent 生效，于是 `agent-protocols`
    的「归属查 skill: document-norms §1」、`auto-update` 的「落点分层见 skill: librarian §2b」
    这类点名指令在 subagent 内全部空转——**空转与执行在终端里长得一模一样**，无人会察觉。

    `Skill`（授权：能不能调）与触发（什么条件下该调哪个）是**两套机制**。本闸只钉
    前者；后者的唯一源是各 skill 的 description，出厂 agent 不得另写一份由
    `check_agents_no_business_skill_in_body` 管——那道闸同时钉死出厂 agent frontmatter 不得出现
    `skills:` 预载键。

    三个扫描面各是一条独立的传播路径，少扫一条就漏一条：出厂角色自己 / 项目照模板新建与
    override / 手写角色照抄指南样板。各类红灯文案分开写，指向的排查方向互不相同。

    **三层各管一段，缺任一层都留一条完整的漏网通道**：

    1. **值对账**——逐处声明必须含 `Skill`、出厂角色不得低于模板基线。抓「认得出的形态里
       写错了内容」。
    2. **差集**——行首 `tools:` 是宽候选集，解析器认得出的是它的严格子集，差非空即红。抓
       「漂出严格集但仍在宽集内」的写法（多行流式 / 标量式 / 条目没缩进）。缺这层时，那些
       写法在期望恰 1 处的两面上还有计数断言兜成红，在只判「≥1」的指南面上则无人兜底、
       **闸报绿**。
    3. **下限**——指南里每处角色样板都得有一处解析得出的 `tools:`，锚点取行首 `name:`
       （CC agent frontmatter 的必填字段）。抓「宽严两个正则一起够不着且净减少」，例如某处
       样板整个不写 `tools:`：那时宽集里根本没有它，差集看不见。**下限有意不钉死处数**
       ——样板数量本就随文档演进增减，钉死会造高频假红，所以下限跟着锚点数走。

    **覆盖边界**：项目级 `.claude/agents/**` 不在扫描面内（validate 是框架仓工具、不随插件
    分发），那一侧只能靠模板与指南把正确形态带过去，没有第二道闸兜底。
    """
    name = "agent_tools_include_skill"
    tpl_rel = "plugins/core/templates/agent-template.md"
    guide_rel = "plugins/core/reference/role-customization-guide.md"

    agent_files = sorted(AGENTS_DIR.glob("*.md"))
    if not agent_files:
        return _fail(name, "agents/ 目录空或缺失——无对象可查，这不等于检查通过")
    agent_rels = [f"plugins/core/agents/{p.name}" for p in agent_files]

    # 两种成因的排查方向不同：一个改声明行本身，一个改它底下几行的缩进。别合并措辞
    unparsed_hint = {
        "inline": "冒号后面的内容不是认得出的形态（`tools: [` 换行续写、"
                  "`tools: Read, Write` 标量式都在此列）——改写成单行流式 "
                  "`tools: [Read, Write]` 或块式；若这行只是散文里提到 tools，"
                  "用反引号包起来，行首就不再命中",
        "block": "冒号后是空的（块式），但紧跟的几行里没有一条缩进条目——`- Read` 顶格"
                 "不算条目。查这处声明**底下**几行的缩进，别去改 `tools:` 那一行",
    }

    # (相对路径, 期望声明数)；None = 至少 1 处（指南的样板不止一处，逐处判）
    targets = [(rel, 1) for rel in agent_rels] + [(tpl_rel, 1), (guide_rel, None)]
    texts, decls, blind, unparsed = {}, {}, [], []
    for rel, expect in targets:
        p = FRAMEWORK_ROOT / rel
        if not p.exists():
            blind.append(f"{rel} 不存在")
            continue
        texts[rel] = p.read_text(encoding="utf-8")
        found, bad_lines = _tools_declarations(texts[rel])
        for ln, why in bad_lines:
            unparsed.append(f"{rel}:{ln} {unparsed_hint[why]}")
        bad = (len(found) != expect) if expect is not None else (not found)
        if bad:
            blind.append(f"{rel} 解析到 {len(found)} 处 `tools:`"
                         f"（期望 {expect if expect is not None else '≥1'}）")
            continue
        decls[rel] = found
    if unparsed:
        # 比下面的失明分支更靠前、也更具体：能点到行号与成因时就别退回「计数对不上」
        return _fail(name, "这些 `tools:` 本闸解析不出工具清单——解析不出就断言不了它含不含 "
                           "`Skill`，而缺 `Skill` 的角色一个 skill 都调不起来: "
                           + "; ".join(unparsed))
    if blind:
        # 解析失效必须报红：此时「没有 offender」只说明本闸瞎了，不说明合规
        return _fail(name, "本闸在这些扫描面上已失明（解析口径与文件形态对不上，"
                           "此时全绿不代表合规）: " + "; ".join(blind))

    tpl_line, tpl_tools = decls[tpl_rel][0]
    baseline = tpl_tools - {"Skill"}     # Skill 由下面的专用分支管，不混进基线差集
    offenders = []

    if "Skill" not in tpl_tools:
        offenders.append(
            f"{tpl_rel}:{tpl_line} 模板基线缺 `Skill`——出厂角色可能仍是好的，红灯不在它们"
            f"身上：项目照本模板新建 / override 的角色会继承这个缺口，而项目级 agent 不在"
            f"任何闸的扫描面内")

    for rel in agent_rels:
        ln, tools = decls[rel][0]
        role = rel.rsplit("/", 1)[-1][:-3]
        if "Skill" not in tools:
            offenders.append(
                f"{rel}:{ln} 出厂角色 @{role} 的 tools 缺 `Skill`——tools 是白名单，缺它则这个 "
                f"subagent 一个 skill 都调不起来，rules 里「调 skill: X」的点名指令随之空转")
        short = baseline - tools
        if short:
            offenders.append(
                f"{rel}:{ln} 出厂角色 @{role} 的 tools 低于 {tpl_rel} 的基线，缺 {sorted(short)}"
                f"——照模板建的项目级角色反而比出厂角色能力更全，两边形态已分叉")

    for ln, tools in decls[guide_rel]:
        if "Skill" not in tools:
            offenders.append(
                f"{guide_rel}:{ln} 指南的 tools 样板缺 `Skill`——照抄该样板手写出来的角色"
                f"调不了任何 skill，且这条路径上没有任何机器闸")

    anchors = len(re.findall(r"(?m)^name\s*:", texts[guide_rel]))
    if len(decls[guide_rel]) < anchors:
        offenders.append(
            f"{guide_rel} 有 {anchors} 处角色样板（行首 `name:`）却只有 "
            f"{len(decls[guide_rel])} 处解析得出的 `tools:`——至少一处样板整个没写工具白名单，"
            f"照抄它手写出来的角色连 Read 都调不了。这一层专抓「宽严两个正则一起够不着」的"
            f"删减：没写的声明不在宽候选集里，差集看不见它")

    # 不截断：几族红灯的排查方向互不相同，截掉哪一族都会让人反复跑闸才凑齐线索
    if offenders:
        return _fail(name, " | ".join(offenders))
    return _ok(name, f"({len(agent_rels)} 个出厂角色 + 模板 + 指南 {len(decls[guide_rel])} 处样板"
                     f"的 tools 均含 Skill，不低于模板基线 {sorted(baseline)}，"
                     f"指南样板数 ≥ {anchors} 处 `name:` 锚点，且无解析不了的 `tools:`)")


def check_agents_no_response_output_duplication():
    """出厂 core agent body 不重复 response-output rule 的核心句"""
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
    """出厂 core agent body 不重复受保护资产清单（≥3 个保护资产同时罗列且未指向必载片）。

    豁免条件钉的是**小节锚点** `受保护资产清单`。早先钉的是退役前那份 rule 的文件名，
    agent 头注改口之后那个子串在正文里一个都不剩——**豁免分支从此不可达**，而不可达的
    分支与不存在的分支长得一样：本闸会开始对着「已经正确指过去了」的 agent 报红。
    """
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    offenders = []
    # 指示器跟着受保护资产表走：`.claude/rules/` 已不在那张表内，`AGENTS.md` 是它的继任者。
    indicators = [".claude/agents/", "AGENTS.md", ".claude/skills/", ".claude/settings"]
    for f in agents_dir.glob("*.md"):
        body = _agent_body(f.read_text(encoding="utf-8"))
        # 指向必载片那一节的 agent 视为合规（「清单见必载片 §受保护资产清单」）
        if "受保护资产清单" in body:
            continue
        match_count = sum(1 for i in indicators if i in body)
        if match_count >= 3:
            offenders.append(
                f"{f.name}: body lists {match_count}/4 protected paths inline "
                "(should point at 必载片 §受保护资产清单 instead)"
            )
    if offenders:
        return _fail("agents_no_protected_assets_duplication", "; ".join(offenders))
    return _ok("agents_no_protected_assets_duplication")


def check_agents_no_project_specific_path_hardcoding():
    """出厂 core agent body 不含项目特定路径硬编码（具体命名占位符 / 业务子目录假设）"""
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
    """出厂 core agent body 不含 projects/specs/X/Y/Z/ 三层路径硬编码"""
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
    """domain skill 必须有 description——它是 routing 的唯一依据。

    Agent Skills spec 的字段里，描述「什么时候该调它」的只有 `description` 一个落点，
    所以「是什么 / 什么时候调 / 典型触发词 / 不用于」四类信息全部写在它这一段单行标量里；
    本闸挡的就是「某个 domain skill 连 description 都没有 → 它在任何
    harness 上都不可路由」。**只判存在性**——标量形态是否合法由
    `check_frontmatter_scalar_forms` 判，长度上限也在那里。
    """
    skills_dir = FRAMEWORK_ROOT / "plugins" / "core" / "skills"
    missing_desc = []
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

    if missing_desc:
        return _fail("domain_skills_have_description", "; ".join(missing_desc[:5]))

    return _ok("domain_skills_have_description", f"(all {len(DOMAIN_SKILLS)} domain skills have description)")


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

    # 扫描 SKILL.md / 注入片 找 `{"type":"<name>"}` 模式
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
        # audit 的纪律抽查结果只经 `workframe-event` 子命令写，SKILL.md 里摆手拼 JSON 等于邀请绕开它
        "discipline_spotcheck": "workframe-event\" discipline-spotcheck",
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
        # producer 不一定是 skill：`self_signoff` 的可执行指令写在必载片里
        # （签发档判定本身就在那儿定义，把示例放到某个 skill 里等于让指令与判据分家）。
        # 这张表按 producer 文本里出现的关键字匹配，**新增落点要与 producer 措辞对齐**——
        # 对不上时本闸报的是 "no producer doc mapping"，而那读起来像事件写错了。
        "closeout-discipline": CONTEXT_DIR / "both" / "10-closeout.md",
        # 放在最后：按插入顺序匹配，别的 producer 措辞里若顺带提到 audit，先命中的仍是它自己的键
        "audit": FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "audit" / "SKILL.md",
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
    """create-project references must reflect the current 36 skills / 5 rules taxonomy."""
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

    通用协议由 `SubagentStart` 注入，agent 正文不再指向任何插件内源文件；本闸兜的是
    「又有人把插件内路径写回 agent 正文」。扫描面含 skills/<name>/reference/* ——
    那类路径在订阅项目里同样不可见（Codex 复测发现 dev.md 曾引用
    plugins/core/skills/technical-design/reference/engineering-discipline.md）。
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
            f"plugin-internal path reference (say it by name — 注入片的小节名 / "
            f"'<skill> skill 的 <ref> reference' instead): {offenders}",
        )
    return _ok("agents_no_plugin_internal_path", "(7 files)")


def check_role_profile_catalog_exists():
    """role-profile-catalog.md 存在且含 3 个 profile 章节标题（v0.2.3 Role Profile Lite）。

    最小检查：文件存在 + 3 个 profile 章节能被 scaffold 的 `catalog_section` 切出 + 已退役
    档位零残留。不解析每个 profile 的具体路由文本内容（防止文档微调被校验卡住）。

    章节判定**不自写子串匹配**：`f"### `{p}`" in text` 连 `####` 与围栏内的示例都认，比
    消费方宽——标题行尾多一个空格时这里绿、scaffold 的 preflight 抛 ParamError（BUG-002）。
    改为调消费方自己的判定式（整行相等、围栏外），闸与消费方同宽。

    **不要因为「与 role_enum_single_source 重复」而删掉这里的 expected_profiles**：
    两者互为承重、方向相反。本检查是 ⊇ 冻结断言（这三个档位必须在，不禁止第四个），
    对方是「catalog 现算章节集 == doctor 枚举」的正向对账。本检查还是 scaffold 默认档
    的间接保护。删掉任一边都会开出一个静默的洞。
    退役档位（client-delivery / content-ops / business-ops）随 project_type 六类型
    一并退出，正文再次出现即视为回潮。
    """
    path = FRAMEWORK_ROOT / "plugins" / "core" / "reference" / "role-profile-catalog.md"
    if not path.exists():
        return _fail("role_profile_catalog_exists", "role-profile-catalog.md missing")
    text = path.read_text(encoding="utf-8")
    try:
        sc = _scaffold()
    except Exception as e:
        return _fail("role_profile_catalog_exists",
                     f"import project_scaffold 失败（章节判定式在它那里）: {e}")
    expected_profiles = ["software-team", "solo-pm", "ai-product"]
    retired_profiles = ["client-delivery", "content-ops", "business-ops"]
    missing = [p for p in expected_profiles if sc.catalog_section(text, p) is None]
    if missing:
        return _fail(
            "role_profile_catalog_exists",
            f"missing profile sections: {missing}（按 scaffold 判定式：标题须整行为 ### `<name>`、"
            f"闭合反引号后紧跟换行、不在代码围栏内——否则该档装机 preflight 抛 ParamError）",
        )
    revived = [p for p in retired_profiles if p in text]
    if revived:
        return _fail("role_profile_catalog_exists", f"已退役档位残留: {revived}")
    return _ok("role_profile_catalog_exists", "(3 profiles)")


def check_agents_md_template_has_role_profile_placeholder():
    """agents-md-template.md 含 {{ROLE_PROFILE_ROUTING}} 占位符——路由偏好渲染进项目的 AGENTS.md。

    缺了它 `render_placeholders` 不会报（它只查模板里剩没剩占位符，不查参数用没用上），
    role_profile 选的那一档路由就被静默丢掉。
    """
    name = "agents_md_template_has_role_profile_placeholder"
    path = FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "agents-md-template.md"
    if not path.exists():
        return _fail(name, "agents-md-template.md missing")
    text = path.read_text(encoding="utf-8")
    if "{{ROLE_PROFILE_ROUTING}}" not in text:
        return _fail(name, "{{ROLE_PROFILE_ROUTING}} placeholder missing in agents-md-template.md"
                           "——装机时 role_profile 那一档的路由偏好无处可落，且不报错")
    return _ok(name)


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
        FRAMEWORK_ROOT / "docs" / "context-injection.md",
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
    # 注入源按目录现扫（每会话必载，正是最该扫的地方），不逐个列文件；一份都扫不到时报红，
    # 否则目录被改名后本闸对注入源静默失明
    context_files = sorted((FRAMEWORK_ROOT / "plugins" / "core" / "context").rglob("*.md"))
    if not context_files:
        return _fail("no_stale_skill_count", "plugins/core/context/ 下一份 .md 都没扫到——注入源脱离扫描面")
    target_files += context_files
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


# ── 角色计数短语：严格集（值对账用）与宽集（扫描面自检用），**全仓各一份** ──
# 数字收阿拉伯与**单字符**中文数字；复合中文数（十 / 二十 / 三十五）**有意不收进严格集**，
# 让它们落进 `role_count_scan_surface_intact` 的差集报红——凭空造一套复合数换算去解析一个
# 全仓不存在的形态，只会多一个错面，而「解析不了就报红」本身是 fail-safe 的方向。
_CN_DIGIT = "一两二三四五六七八九"
_COUNT_NUM = rf"(?:[0-9]+|[{_CN_DIGIT}])"
_COUNT_NUM_WIDE = rf"(?:[0-9]+|[{_CN_DIGIT}十百]+)"      # 宽集收复合数，好让它进差集
# 修饰词允许**叠加**（`*` 而非 `?`）：仓内 `4 个 baseline core role` 是两个修饰词叠加，
# `?` 只允许一个，那一处于是整个脱离对账面。同一份仓里 `_ROLE_COUNT`（名录闸用）的注释
# 早就点名了这个形态并用了 `*`——两处实现写于不同时间，后写的那份知道更多却没回填前一份。
_COUNT_MOD = r"(?:baseline\s*|core\s+|通用\s*|默认\s*|内置\s*|基础\s*)*"
# 「N 个角色提及 / 参与 / …」是「本例涉及几个角色」的语义，不是 roster 口径
_COUNT_EXCL = r"(?!提及|参与|使用|命中|贡献)"
# 角色计数短语的**唯一实现，两族闸共用**：值对账闸（数字对不对）、扫描面自检闸
# （还看得见吗）、名录闸（名字对不对，在它后面再接括号 / 冒号名录）三处同一份。
#
# **它是 union，不是「挑现成的那份」**——这一点值得记住：写新判定式前先 grep 同契约的
# 既有实现、存在则复用，是对的；但**「复用」不总是拿现成的那份替换掉另一份**。
# 此前仓内两份实现是**交叉关系而非包含关系**——
#   · 名录闸那份知道「修饰词能叠加」（`*`）且中心词收 `roles?`；
#   · 计数闸那三条知道「要收 `agents`」且要排除「N 个角色提及 / 参与」。
# 实测直接拿名录闸那份去替换计数闸：**造 1 处假红**（`self-iteration/SKILL.md` 的
# 「3 个角色提及同一现象」——它没有排除 lookahead）**并丢 6 处覆盖**（全部 agents 形态，
# 因为它的中心词里没有 `agents`）；命中数 23 vs 28，谁都不包含谁。
# 挑错方向的代价正是「消了副本，同时造出假红、丢了覆盖」——所以两份各自携带对方没有的
# 知识时，正解是**取并集**，合并前先把两侧各自知道什么列清楚。
#
# 只含**一个**捕获组（数字）；名录闸在其后接 `([^）)\n]*)` 取名录，靠 group(2) 定位，
# 这里多加一个捕获组会把它错位。
_ROLE_COUNT = (rf"(?<![0-9~\-–—.])({_COUNT_NUM})\s*(?:个\s*)?{_COUNT_MOD}"
               rf"(?:角色{_COUNT_EXCL}|(?:\bagents?|\broles?)\b(?!-))")
_ROLE_COUNT_RE = re.compile(_ROLE_COUNT, re.I)
# 宽集：量词与修饰词都放开一档（修饰词槽 2 字——实测放到 4 字会把
# 「三个层级加载 agents」这类非角色计数收进来制造假红，2 字则差集恰为 0）。
# 排除 lookahead 与中心词与严格集**共用同一份字面**，否则差集会混进两边语义本就不同的东西。
_ROLE_COUNT_WIDE = re.compile(
    rf"(?<![0-9~\-–—.])({_COUNT_NUM_WIDE})\s*(?:个|名|位|种|类|款|条)?\s*"
    rf"(?:[一-鿿A-Za-z]{{0,2}}\s*)?"
    rf"(?:角色{_COUNT_EXCL}|(?:\bagents?|\broles?)\b)", re.I)
# 已知的角色计数短语处数下限。差集只能发现「宽看得见而严看不见」的漂移，发现不了**两边
# 都够不着**的形态（实测 `4 类核心业务角色`：修饰词 4 字，宽集槽 2 字也够不着，两边一起掉）。
# 本条兜的就是那个残余：净减少即红。**代价诚实记这里**——计数短语是散文，任何文档增删一句
# 「N 角色」都会动这个数，它是本仓 churn 最高的常量；不留 slack 是有意的，留了就退化成弱闸。
ROLE_COUNT_SITES_FLOOR = 28


def _role_scan_lines():
    """产出角色计数两族闸的**共同扫描面**里的 `(rel, lineno, line)`。

    值对账闸与扫描面自检闸必须扫同一批文件——否则 `ROLE_COUNT_SITES_FLOOR` 那个数字
    失去意义（一边扫得多一边扫得少，下限就成了两套口径的差）。
    `tools/` 不在面内：本文件自己的实现与文案里就要举计数与名录的例子。
    root 缺失时抛 FileNotFoundError 由调用方转 `_fail`——扫描面整个消失必须响亮失败，
    不能安静地扫 0 行然后报绿。
    """
    for root in (FRAMEWORK_ROOT / "plugins", FRAMEWORK_ROOT / "docs",
                 FRAMEWORK_ROOT / "README.md",
                 FRAMEWORK_ROOT / ".claude-plugin" / "marketplace.json"):
        if not root.exists():
            raise FileNotFoundError(root.relative_to(FRAMEWORK_ROOT).as_posix())
        for p in ([root] if root.is_file() else sorted(root.rglob("*"))):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            raw = p.read_bytes()
            if b"\x00" in raw:
                continue
            rel = p.relative_to(FRAMEWORK_ROOT).as_posix()
            for lineno, line in enumerate(
                    raw.decode("utf-8", errors="replace").splitlines(), 1):
                yield rel, lineno, line


def _cn_int(tok):
    """计数短语里的数字 token → int；阿拉伯数字与**单字符**中文数字，其余返回 None。"""
    if tok.isdigit():
        return int(tok)
    return {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}.get(tok)


def _role_count_hits(line):
    """产出该行被**严格集**识别的角色计数短语 `(start, end, n, 原文)`；n < 3 的已滤掉。

    值对账闸与扫描面自检闸共用这一份。各抄一份的话，收紧其中一份只会让那一个闸失明，
    另一个照常红——看上去「还有闸拦着」，实际已开出一个只在特定形态下触发的洞。
    """
    for m in _ROLE_COUNT_RE.finditer(line):
        n = _cn_int(m.group(1))
        if n is None or n < 3:
            continue
        yield m.start(), m.end(), n, m.group(0).strip()


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
    offenders = []
    try:
        for rel, lineno, line in _role_scan_lines():
            for _s, _e, n, _txt in _role_count_hits(line):
                if n != agent_count:
                    offenders.append(f"{rel}:{lineno}: '{line.strip()[:70]}'")
    except FileNotFoundError as e:
        return _fail(name, f"scan root missing: {e}")
    if offenders:
        return _fail(name, f"roster 数字与 agents 实数（{agent_count}）不符: {offenders[:5]}")
    return _ok(name, f"(agents={agent_count}, wording aligned)")


def check_role_count_scan_surface_intact():
    """计数闸的**扫描面自身**是否完好——两条断言，答的都不是「数字写对没有」。

    背景：`check_role_count_wording_matches_agents` 只收集「数字 ≠ 实数」的 offender，
    **没有任何计数器**。全仓计数短语一旦全部改成它识别不了的形态，它就安静地报绿——
    实测把 25 处全改成中文数字（25 句语义全部写错）→ 全绿；半失明（只改一处）与全失明
    在终端里长得**一模一样**，都是一句 `(agents=4, wording aligned)`。

    两条断言正交，缺任一条都留一条完整的漏网通道：

      1. **差集**（宽集 ⊆ 严格集）——抓「宽看得见而严看不见」，即形态漂出了值对账面。
         方向是**非空断言**：宽集里漏一个元素 = 那处计数表述永久脱离对账，角色增删时
         不会红。这与「挑代表性 token 做存在性探测」那种恒绿 `⊆` 是反方向。
      2. **处数下限**——抓「两边都够不着」。差集只能发现两个正则的**能力差**，发现不了
         它们**共用的**修饰词表够不着的形态（实测 `4 类核心业务角色`：修饰词 4 字，
         宽集的 2 字槽也够不着，严格与宽一起掉，差集为空）。净减少即红。

    两条的红灯文案必须分开：一条要人「把表述改回受支持形态」，一条要人「查哪处漂了，
    或确认是有意删除后改常量」——指向的动作不同。
    """
    name = "role_count_scan_surface_intact"
    strict_total = 0
    drifted = []
    try:
        for rel, lineno, line in _role_scan_lines():
            starts = {h[0] for h in _role_count_hits(line)}
            strict_total += len(starts)
            for m in _ROLE_COUNT_WIDE.finditer(line):
                if m.start() in starts:
                    continue
                n = _cn_int(m.group(1))
                # 解析不出来（复合中文数）**也算漂移**：宽集看得见而严格集读不懂，
                # 正是要报的那件事。只有确定 < 3 的才跳过（「仅 2 个角色」不是名录口径）。
                if n is not None and n < 3:
                    continue
                drifted.append(f"{rel}:{lineno} {m.group(0).strip()!r}")
    except FileNotFoundError as e:
        return _fail(name, f"scan root missing: {e}")

    problems = []
    if drifted:
        problems.append(
            f"{len(drifted)} 处角色计数表述用了值对账闸识别不了的形态: {drifted[:4]}"
            f"——这些句子说的是 core 角色数，但计数闸看不见它们，角色增删时不会红。"
            f"改成受支持形态（阿拉伯数字或单字符中文数 + 常见量词/修饰词），"
            f"或确认它讲的不是 core 角色数")
    if strict_total < ROLE_COUNT_SITES_FLOOR:
        problems.append(
            f"全仓角色计数短语实扫 {strict_total} 处，低于已知的 {ROLE_COUNT_SITES_FLOOR} 处"
            f"——有表述漂成了**宽严两边都识别不了**的形态（差集也够不着它）。"
            f"若确实增删了角色计数表述，把 ROLE_COUNT_SITES_FLOOR 改成新值；"
            f"若不是你改的，去查哪一处漂了形态")
    if problems:
        return _fail(name, " || ".join(problems))
    return _ok(name, f"({strict_total} 处计数短语在对账面内，差集为空)")


# 名录段内允许出现的字符（冒号形态用）：拉丁名、反引号、常见分隔符与空白。
# 出现任何别的字符即认为「这不是一段裸名名录」，见下方识别条件 1。
_ROSTER_RUN = r"[A-Za-z`][A-Za-z0-9`\-_/,、，\s]*"
# 角色计数短语用上方的 `_ROLE_COUNT`（两族闸共用的 union，见其注释）。
# 本处曾另有一份只认阿拉伯数字、中心词不含 `agents` 的副本——两份各自携带对方没有的
# 知识，合并时取的是并集而不是挑一份。


def _in_quote_span(line, start, end):
    """命中片段 `[start, end)` 是否**整个**落在该行的某个 `『…』` 之内。

    豁免粒度必须是**片段级**、不是整行：一行里既有引述的旧写法、又有一处真写错的表述时，
    整行豁免会把真错一起遮掉（实测「旧写法『5 角色』已废弃；现为 4 个 baseline 角色
    （pm / dev / qa / pmo）。」整行跳过、报绿——pmo 这个已不存在的角色没人说话）。

    只认 `『』` 一种引号。**别复用 CHANGELOG 那支四合一的 quoted 正则**（它还含反引号与
    双引号两支）：`docs/concepts.md` 的角色名录恰是反引号形态，把反引号也算成引述会让那处
    名录整个脱离扫描面，反倒制造新的失明。
    未配对的单个 `『` 不构成引述区（正则要求成对），此时不豁免——方向 fail-safe。

    两个消费者共用这一份：`role_roster_wording_matches_agents` 与
    `skill_count_consistent`（两处此前是逐字相同的 `if "『" in line: continue`）。
    """
    return any(m.start() <= start and end <= m.end()
               for m in re.finditer(r"『[^』]*』", line))


# 已知承载「N 角色（<裸名名录>）」散句的文件——每个至少要贡献 1 处**被扫到**的名录。
# 单处名录改写成不受支持的包裹符 / 分隔符 / 行尾形态时会**静默脱离扫描面**，而其余各处
# 仍绿、整闸报绿（实测：把一处改成 `【pm / dev / qa / pmo】`，scanned 由 8 降到 7，
# 闸照常绿，那个已不存在的 pmo 没人说话）。全局 `scanned == 0` 只在**全部**脱离时说话，
# 挡不住半失明——半失明与全绿在终端里长得一模一样。
# 断言方向是 `本清单 ⊆ 实际有命中的文件`：不禁止别处新增名录，但清单里的每个文件都必须
# 还在对账面内。增删名录点是**有意动作**：真要删掉某处名录，把该文件从本清单一并删掉。
ROSTER_BEARING_FILES = (
    ".claude-plugin/marketplace.json",
    "docs/concepts.md",
    "docs/setup-guide.md",
    "plugins/core/.claude-plugin/plugin.json",
    "plugins/core/reference/role-customization-guide.md",
    "plugins/core/reference/role-profile-catalog.md",
    "plugins/core/context/both/20-protocol-common.md",
    "plugins/core/skills/librarian/SKILL.md",
)


def check_role_roster_wording_matches_agents():
    """全仓「N 角色（<裸名名录>）」散句里的名录必须等于 agents/ 目录现算的角色集。

    与相邻的 `check_role_count_wording_matches_agents` 分工明确，**两者互为承重、别合并**：
    那道闸只对账**数字**，本闸只对账**名字**。数字对而名字错的整条缺陷带在它下面是盲区，
    最典型的是**改名**——把一个角色文件改个名字，角色总数仍然不变，全部计数表述照样绿，
    而散句里写的旧名全部指向一个已不存在的角色，没有任何闸会说话。增删角色时两闸会同时
    红（数字与名字一起错），那是重叠不是冗余：两条文案不同、指向的修法也不同。

    扫描面与计数闸严格一致（plugins/ + docs/ + README + marketplace.json；tools/ 与
    CHANGELOG 不在面内），两闸看同一批文件才能互相解释。tools/ 必须排除——本闸自己的
    实现与文案里就要举名录的例子。含『』的行按引述豁免（引号内是被谈论的旧内容）。

    识别一处「名录」要三个条件同时成立，每条都是被仓内真实行逼出来的：

      1. 紧跟在角色计数短语之后，两种形态都收——`（…）` 括号形态与 `：…` 冒号形态。
         包裹符与分隔符在仓内**已经三种并存**（括号+斜杠+首字母大写 / 括号+无空格斜杠 /
         冒号+反引号+逗号），按固定分隔符解析必漏。冒号形态额外要求名录一直延伸到行尾，
         否则「…：pm、dev 是主力」这种半截句会被当成名录而误红。
      2. 计数数字 ≥3 —— 与计数闸同一门槛。「仅 1 个角色（dev）」这类是「本例涉及几个
         角色」的语义，不是名录。
      3. 名录段内无 CJK —— 「4 通用角色（来自 core plugin）」括号里是说明文字不是名录。
         裸名名录按定义只含拉丁名与分隔符。

    比较大小写不敏感：面向用户的描述字段写的是 `PM / Dev / QA / …` 展示形态，而 agents/
    的文件名是小写，两侧一律折成小写后比。用 **== 而非 ⊆**：这类句子自称「baseline 的
    N 个角色就是这些」，漏一个就是把一个真实存在的角色从名录里抹掉——⊆ 在这里等于恒绿。

    另断言**句内自洽**（数字 == 名录长度）。看似与计数闸重叠，实则补的是它的形态盲区：
    那道闸的英文形态只认 `N agents`，不认没有量词「个」的 `N roles`，而本闸的计数短语
    收得更宽——两边一叠，`the 7 roles (pm / dev / qa / prompt-eng)` 这种写法会两闸全绿。
    """
    name = "role_roster_wording_matches_agents"
    baseline = {r.lower() for r in _baseline_roles()}
    if not baseline:
        return _fail(name, "agents/ 目录空或缺失——角色名录对账基准丢失")
    scan_roots = [
        FRAMEWORK_ROOT / "plugins",
        FRAMEWORK_ROOT / "docs",
        FRAMEWORK_ROOT / "README.md",
        FRAMEWORK_ROOT / ".claude-plugin" / "marketplace.json",
    ]
    pat_paren = re.compile(_ROLE_COUNT + r"\s*[（(]([^）)\n]*)[）)]", re.I)
    pat_colon = re.compile(_ROLE_COUNT + r"\s*[：:]\s*(" + _ROSTER_RUN + r")$", re.I)
    cjk = re.compile(r"[一-鿿㐀-䶿]")
    offenders = []
    scanned = 0
    hit_files = set()
    for root in scan_roots:
        if not root.exists():
            return _fail(name, f"scan root missing: {root.relative_to(FRAMEWORK_ROOT)}")
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for p in paths:
            if not p.is_file() or "__pycache__" in p.parts or p.name == "CHANGELOG.md":
                continue
            raw = p.read_bytes()
            if b"\x00" in raw:
                continue
            for lineno, line in enumerate(
                    raw.decode("utf-8", errors="replace").splitlines(), 1):
                for m in list(pat_paren.finditer(line)) + list(pat_colon.finditer(line)):
                    inner = m.group(2)
                    # `_cn_int` 而非 `int`：共用的 `_ROLE_COUNT` 收单字符中文数，
                    # `int("四")` 会当场抛 ValueError 把整闸炸成 runner 层的 EXCEPTION
                    n = _cn_int(m.group(1))
                    if n is None or n < 3 or cjk.search(inner):
                        continue
                    # 引述豁免收到**片段级**：整行豁免会让「引述 + 真写错」同行时漏检
                    if _in_quote_span(line, m.start(), m.end()):
                        continue
                    scanned += 1
                    hit_files.add(p.relative_to(FRAMEWORK_ROOT).as_posix())
                    got = {t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_-]*", inner)}
                    bad = []
                    if got != baseline:
                        miss, extra = sorted(baseline - got), sorted(got - baseline)
                        if miss and extra:
                            why = "名录双向不符（多半是角色被改名）——旧名指向已不存在的角色"
                        elif miss:
                            why = ("名录漏了真实存在的角色——照它派活的读者（含读到每会话"
                                   "注入片的模型）会少一个可调度的角色")
                        else:
                            why = "名录写了 agents/ 里没有的角色——按它 `@` 调用会落空"
                        bad.append(f"名录 {sorted(got)}（少 {miss}、多 {extra}）：{why}")
                    if n != len(got):
                        # 句内自洽。相邻计数闸的英文形态只认 `N agents`、不认 `N roles`，
                        # 而本闸的计数短语更宽——「the 7 roles (pm / dev / qa / prompt-eng)」
                        # 那道闸看不见、名录又恰好正确时，错的数字会一路绿到用户眼前。
                        # 这条只比「这句话自己」，与那道闸的「全仓计数 == agents 实数」不重叠。
                        bad.append(f"句内自相矛盾：写「{n}」却列了 {len(got)} 个名字")
                    if bad:
                        offenders.append(
                            f"{p.relative_to(FRAMEWORK_ROOT)}:{lineno} " + "；".join(bad))
    problems = []
    if offenders:
        # 前缀保持中立：本闸有两类失败（名录不符 / 句内自相矛盾），措辞由每条 offender
        # 自己给，外层再断言一次「名录不符」会在计数出错时把人指向错误的排查方向
        problems.append(f"角色名录散句不合格（agents/ 现算 {sorted(baseline)}）: "
                        + "; ".join(offenders[:4]))
    # 扫描面下限：清单里每个文件都必须还贡献至少 1 处被扫到的名录（`清单 ⊆ 实扫文件集`）。
    # 这一条与上面的内容对账**正交**——它答的不是「名录写对没有」，是「我还看得见它吗」。
    missing = [f for f in ROSTER_BEARING_FILES if f not in hit_files]
    if missing:
        problems.append(
            f"这些文件不再贡献任何被扫到的裸名名录: {missing[:4]}——要么那处名录被改成了"
            f"不受支持的形态（包裹符 / 分隔符 / 冒号形态未延伸到行尾）而**静默脱离对账**，"
            f"从此改坏了也不会红；要么它是被有意删掉的，那就把该文件从 "
            f"ROSTER_BEARING_FILES 里一并删掉")
    if problems:
        return _fail(name, " || ".join(problems))
    if scanned == 0:
        return _fail(name, "全仓一处裸名名录都没扫到——名录写法变了（包裹符 / 分隔符 / "
                           "计数短语形态），本闸已失去对账能力，不是「仓里干净」")
    return _ok(name, f"({scanned} 处裸名名录与 agents/ {len(baseline)} 角色一致)")


def check_role_profile_lead_copies():
    """「Profile → 主力」表的副本必须与 role-profile-catalog 角色优先级表的**主力列**一致。

    catalog 的角色优先级表是事实源，仓内另有两份面向不同读者的副本（一份在安装指南、
    一份在架构参考），此前全程无闸——副本与事实源分叉时，读者按副本挑档位，拿到的默认
    路由与实际渲染进 AGENTS.md 的不是一回事。

    **断言是「副本 == 事实源同 profile 的主力集」，不是「主力集 == baseline 名录」。**
    这个区别是本闸的全部要害：主力列按定义只列该档的主力角色，`software-team` 的主力
    天然就比全名录少一个，套 `== baseline` 会让三份表全年红着。反过来，副本内部若用 ⊆
    比事实源（「副本 ⊆ catalog」）则是恒绿假闸——从副本里删掉一个主力角色仍然是子集。
    正确口径只有一个：同 profile 的主力集合两边逐个相等。

    profile 覆盖面也断言 ==：副本表少列一档，读者就不知道那一档存在。顺序**不断言**——
    主力列内部的先后是排版，不是契约；把它写进断言会在纯粹的重排上制造假红。

    表的识别靠**内容**而非路径、也不只靠表头：有 `Profile` 列、且数据行里**真的出现过
    catalog 现算的档位名**，才算一份副本。三层理由：
      · 事实源自己那三张表没有 Profile 列，天然不进副本集，不必按文件名排除（按路径排除
        的写法在文件改名时会静默失效）；
      · 同一份架构文档里还有一张 `dormant_profile` 表，表头同样是「Profile | … 」而行是
        休眠档位——按行内容认表才不会把它误当角色 profile 表；
      · 反过来，一张已被认作角色 profile 表的表若**没有**「主力」列，判红而不是跳过。
        只按表头识别时，把列名从「主力」改成别的词就能让这份副本静默脱离对账面，而
        `copies == 0` 只在**两份都**脱离时才说话——半失明与全绿长得一模一样。
    切章一律调 scaffold 的 `catalog_section`，闸不自写第二套 catalog 解析。
    """
    name = "role_profile_lead_copies"
    catalog_md = FRAMEWORK_ROOT / "plugins" / "core" / "reference" / "role-profile-catalog.md"
    if not catalog_md.exists():
        return _fail(name, "role-profile-catalog.md 缺失——主力列副本失去对账基准")
    cat = catalog_md.read_text(encoding="utf-8")
    try:
        sc = _scaffold()
    except Exception as e:
        return _fail(name, f"import project_scaffold 失败（catalog 切章的唯一实现在它那里）: {e}")
    # 去重：同名章节重复时 `catalog_section` 会抛，去重只是防同一条错误被记两遍
    profiles = list(dict.fromkeys(_catalog_profiles(cat)))   # 与 role_enum_single_source 共用的宽探测器
    if not profiles:
        return _fail(name, "catalog 未提取到任何 ### `<profile>` 章节——标题形态变了")

    src = {}
    for prof in profiles:
        sec = sc.catalog_section(cat, prof)
        if sec is None:
            return _fail(name, f"catalog {prof!r} 宽探测有标题、消费方判定式却切不出章节"
                               f"——主力列事实源读不到（该档装机 preflight 同样会抛 ParamError）")
        # 取表走 `priority_table`（围栏外恰 1 张），闸不自写第二套表正则
        try:
            hdr, row = priority_table(sec, prof)
        except ValueError as e:
            return _fail(name, f"{e}——本闸失去对账基准")
        if "主力" not in hdr or len(row) <= hdr.index("主力"):
            return _fail(name, f"catalog {prof} 优先级表无「主力」列或数据行列数不足: {hdr}")
        src[prof] = set(re.findall(r"[a-z][a-z-]*", row[hdr.index("主力")]))

    targets = sorted((FRAMEWORK_ROOT / "docs").glob("*.md"))
    targets += sorted((FRAMEWORK_ROOT / "plugins").rglob("*.md"))
    errors = []
    copies = 0
    for p in targets:
        if p.name == "CHANGELOG.md" or not p.exists():
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        rel = p.relative_to(FRAMEWORK_ROOT)
        for i, line in enumerate(lines):
            if not line.startswith("|"):
                continue
            hdr = [c.strip() for c in line.strip().strip("|").split("|")]
            pi = next((k for k, c in enumerate(hdr) if c.lower() == "profile"), None)
            if pi is None:
                continue
            if i + 1 >= len(lines) or not re.match(r"^\|[-:| ]+\|\s*$", lines[i + 1]):
                continue
            rows = []
            for j in range(i + 2, len(lines)):
                if not lines[j].startswith("|"):
                    break
                rows.append((j + 1, [c.strip() for c in lines[j].strip().strip("|").split("|")]))

            def _pname(cells):
                if len(cells) <= pi:
                    return None
                mp = re.search(r"`([^`\n]+)`", cells[pi])
                return mp.group(1) if mp else None

            hit = sorted({x for x in (_pname(c) for _, c in rows) if x in src})
            if not hit:
                continue        # 有 Profile 列但行里没有角色档位名（如休眠档位表），不是副本
            li = next((k for k, c in enumerate(hdr) if c == "主力"), None)
            if li is None:
                errors.append(
                    f"{rel}:{i+1} 这张表的行里出现了角色档位 {sorted(set(hit))}、却没有「主力」列"
                    f"（表头 {hdr}）——要么列名被改（这份副本从此静默脱离对账，改坏了也不会红），"
                    f"要么它本就不该列主力（那就把 Profile 表头改成别的列名，别让它被认成副本）")
                continue
            copies += 1
            seen = set()
            for lineno, cells in rows:
                if len(cells) <= max(pi, li):
                    errors.append(f"{rel}:{lineno} 数据行列数 {len(cells)} 少于表头 {len(hdr)}")
                    continue
                prof = _pname(cells)
                if prof is None:
                    errors.append(f"{rel}:{lineno} Profile 列取不到 `<profile>`: {cells[pi]!r}")
                    continue
                if prof not in src:
                    errors.append(f"{rel}:{lineno} profile {prof!r} 不在 catalog 章节名单 "
                                  f"{profiles} 内——档位名写错或 catalog 已改名")
                    continue
                seen.add(prof)
                got = set(re.findall(r"[a-z][a-z-]*", cells[li]))
                if got != src[prof]:
                    errors.append(
                        f"{rel}:{lineno} {prof} 主力列 {sorted(got)} ≠ catalog {sorted(src[prof])}"
                        f"（少 {sorted(src[prof] - got)}、多 {sorted(got - src[prof])}）"
                        f"——读者按副本挑档，拿到的主力与实际渲染进 AGENTS.md 的路由不一致")
            absent = [x for x in profiles if x not in seen]
            if absent:
                errors.append(f"{rel}:{i+1} 表缺 profile 行 {absent}——读者不会知道这些档存在")
    if errors:
        return _fail(name, "; ".join(errors[:4]))
    if copies == 0:
        return _fail(name, "一份「Profile + 主力」副本表都没扫到——表结构变了（表头列名 / "
                           "分隔行形态），本闸已失去对账能力，不是「没有副本」")
    return _ok(name, f"({copies} 份主力列副本 × {len(profiles)} profile 与 catalog 一致)")


def check_signoff_table_alignment():
    """签发权限的三处事实源对账——task-management（schema 权威）、注入片
    `context/both/50-roles-and-flow.md`（每会话投给主会话与子 agent）、四个 agent 文件的
    Step 3 扩展段。

    2026-08-13 core skills 深审实锤：当时承载流转表的模板 `in_progress → completed` 行只列
    @pm/@dev，漏了 task-management 与 qa.md 都承认的 @qa（非研发任务）/@prompt-eng（非研发类
    咨询）——清单与权威两处事实源没有机器对账就会漂。表格间比对按**角色集合**
    （`@pm|@dev|@qa|@prompt-eng`），不比措辞；agent 文件是 prose 形态，按签发契约 token 断言
    （不解析散文），另加一条语义锚：权威表的 `pending_qa → completed` 必须恰为 @qa 独签。
    """
    name = "signoff_table_alignment"
    src_tm = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "task-management" / "SKILL.md"
    src_tpl = CONTEXT_DIR / "both" / "50-roles-and-flow.md"

    # 角色 alternation 从 agents/ 现算，不手写字面量。手写版（曾是 `@(pm|dev|qa|prompt-eng)`）
    # 的失效方式是**假绿而非漏检**：新增角色后两份文档就算都正确写了 `@新角色`，正则两边
    # 都抠不出来 → 比较的是两个空集 → 判定「一致」。闸会说没问题，而它根本没看那一行。
    baseline = _baseline_roles()
    if not baseline:
        return _fail(name, "agents/ 目录空或缺失——角色对账基准丢失")
    role_alt = _role_alternation(baseline)

    def extract_cells(path):
        """流转行 → 该行「允许操作者」单元格的**原文**。

        A2/A3 要看的是措辞（有没有写自签、有没有指到判据落点），角色集合对账要看的是
        角色。两者从同一份解析产出——**别写第二套行提取**：两处手写的正则一旦漂开，
        一边看得见的行另一边看不见，而症状是「断言全绿但它根本没读那一行」。
        行形取 doctor 的 `FLOW_ROW_RE`：doctor 认 CLAUDE.md 里的旧流转表用的也是它。
        """
        flow_row = _doctor().FLOW_ROW_RE
        cells = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            m = flow_row.match(line)
            if m:
                cells[m.group(1)] = m.group(2)
        return cells

    def extract_rows(path):
        return {k: frozenset(re.findall(rf"@({role_alt})", v))
                for k, v in extract_cells(path).items()}

    missing_files = [str(p.relative_to(FRAMEWORK_ROOT)) for p in (src_tm, src_tpl) if not p.exists()]
    if missing_files:
        return _fail(name, f"对账源缺失: {missing_files}")

    # main-led 代行的契约值（2026-08-16 增）。tags 无代码层枚举校验，`main-executed`
    # 一旦从词表消失，A1 要求打的这个 tag 就变成「词表外造词」而无人报错。
    tm_text = src_tm.read_text(encoding="utf-8")
    if "main-executed" not in tm_text:
        return _fail(name, "task-management 缺 `main-executed`——tags 词表缺代行标记值，"
                           "A1 要求打的 tag 会变成词表外造词")

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
            errors.append(f"context/both/50-roles-and-flow.md 缺流转行 `{key}`——每会话注入的"
                          f"流转表少了这一行，模型读不到谁能做这次变更")
        if key in required and key in tm and key in tpl and tm[key] != tpl[key]:
            errors.append(
                f"`{key}` 角色集合不一致: task-management={sorted(tm[key])} "
                f"vs both/50={sorted(tpl[key])}")

    # 第三份复述：agent 文件 Step 3 的签发契约 token（prose 形态，不做散文解析）
    agent_tokens = {
        "qa.md": ("`pending_qa → completed`", "唯一"),
        "dev.md": ("`pending_qa`", "不得直接 `completed`"),
        "prompt-eng.md": ("`pending_qa`", "不得直接 `completed`"),
        "pm.md": ("直接流转到 `completed`",),
    }
    # 反向覆盖面：下面的循环只遍历本字典自己的 key，agents/ 里多出来的角色文件一行都不会
    # 被检查（纯漏检，不报错也不报警）。新增 baseline 角色时它的签发契约必须同批进这张表。
    uncovered = sorted({p.name for p in AGENTS_DIR.glob("*.md")} - set(agent_tokens))
    if uncovered:
        errors.append(f"agents/ 中 {uncovered} 未纳入签发契约 token 表——其签发约定无闸看管")
    for fname, tokens in agent_tokens.items():
        p = AGENTS_DIR / fname
        if not p.exists():
            errors.append(f"agents/{fname} 缺失")
            continue
        text = p.read_text(encoding="utf-8")
        for tok in tokens:
            if tok not in text:
                errors.append(f"agents/{fname} 缺签发契约 token {tok!r}")
    # 语义锚：签发权唯一性——权威表的 pending_qa → completed 必须恰为签发角色独签。
    # 具名化不是洁癖：裸写 frozenset({"qa"}) 时，qa 若改名，这条断言会静默比对一个
    # 不存在的角色名而永远为真侧失败/假侧通过，先断言它仍在名录内才有锚点。
    signoff_role = "qa"
    if signoff_role not in baseline:
        errors.append(f"签发角色 {signoff_role!r} 不在 agents/ 名录内——签发权断言失去锚点")
    if tm.get("pending_qa → completed") != frozenset({signoff_role}):
        errors.append(
            f"`pending_qa → completed` 应仅 @{signoff_role} 独签，"
            f"task-management 实为 {sorted(tm.get('pending_qa → completed', []))}")

    # ---- A2 / A3：签发行的自签契约 ----
    # 上面那条角色集合锚**一个字不动**：它说的是「baseline 角色里只有 qa 能签」，
    # 主 Claude 不是 baseline 角色，这句话在开了自签通道之后仍然正确。
    # 它挡不住的是**新增非 baseline 签发者**——沙盒实测：把该行改写成
    # 「@qa；主 Claude 自签」报绿，改成 `@main` 也报绿（`role_alt` 由 agents/ 现算，
    # `main` 不在字母表内，两侧都抠不出 → 比较的是两个空集 → 恒真）。A2/A3 补这一格。
    SIGNOFF_ROW = "pending_qa → completed"
    # **钉契约标识符，不钉术语。** 早先的版本钉过档位代号，那是术语——术语一改名
    # 断言当场变空而无人报错。这里钉的是**小节锚点**：`signoff_consumers_carry_pointer`
    # 钉的也是它，两条闸共用同一个标识符，锚点改名会在两处同时炸。
    # （更早钉的是承载该小节的文件名，理由是「它同时被别处的清单精确比较钉住」——那些
    # 钉子随机制退役后理由不再成立，文件名就退化成一个谁都不校验的字符串。）
    A2_TOKENS = ("自签", "谁签发这次收口")
    # 出现下列任一字样 = 声称开了一条非 @qa 的签发通道
    SELF_SIGN_MARKS = ("自签", "主 Claude", "@main")
    tm_cells, tpl_cells = extract_cells(src_tm), extract_cells(src_tpl)
    for label, cells in (("task-management", tm_cells), ("context/both/50", tpl_cells)):
        cell = cells.get(SIGNOFF_ROW)
        if cell is None:
            # 行本身缺失已由上面的 required 循环报过，这里不重复报
            continue
        # A2：既要写出这条通道叫什么，也要指得到判据落在哪
        lack = [t for t in A2_TOKENS if t not in cell]
        if lack:
            errors.append(
                f"[A2] {label} 的 `{SIGNOFF_ROW}` 行缺 {lack}"
                "——自签通道要么没写出来（读者按『仅 @qa』理解，与实际行为不符），"
                "要么写了却没指到判据落点（读者判不出什么情况下能自签，只能各自发挥）")
        # A3-1：该行内除签发角色外**不得出现任何 `@xxx`**——不是「不得出现别的 baseline
        # 角色」。差别就是这道断言的全部价值所在：`role_alt` 由 agents/ 现算，`@main`
        # 根本不在那张字母表里，按 baseline 判等于**对它恒真**。变异实测：判据引用写齐、
        # 只把该行改成「@qa 与 @main」，按 baseline 的写法**照样报绿**——而那正是
        # 闸 A 原本的假绿形态，本组断言的存在理由就是补它。
        others = sorted(set(re.findall(r"@([A-Za-z][\w-]*)", cell)) - {signoff_role})
        if others:
            errors.append(
                f"[A3] {label} 的 `{SIGNOFF_ROW}` 行出现了 @{'、@'.join(others)}"
                f"——签发权只对 @{signoff_role} 开。**主 Claude 的自签通道不写成 `@角色`**"
                "（它不是角色），所以这一行里任何别的 `@xxx` 都是一条谁都没审过的签发通道；"
                "`@main` 这种尤其危险：它不在 agents/ 现算的字母表内，按 baseline 判会恒真")
        # A3-2：写了自签字样就必须同时命中判据引用
        marks = [m for m in SELF_SIGN_MARKS if m in cell]
        if marks and lack:
            errors.append(
                f"[A3] {label} 的 `{SIGNOFF_ROW}` 行写了 {marks} 却没有判据引用"
                f"（缺 {lack}）——这正是角色集合锚看不见的那一格：`@main` 与「自签」都不在"
                "baseline 字母表内，抠不出来，语义锚对它恒真")

    if errors:
        return _fail(name, "; ".join(errors[:4]))
    return _ok(name, f"({len(required)} 行角色集合对账 + {len(required_present_only)} 行存在性对账)")


class _ConstParseError(Exception):
    """`signoff_tier_check.py` 的常量抠不出来。**与「对方没有这几条」是两件事。**"""


def signoff_const_tuple(src, var):
    """从 `signoff_tier_check.py` 源码里抠一个字符串元组常量。**两条闸共用这一份。**

    **解析不出来时抛异常，不返回空集**——空集会被下游当成「对方那张表没有这几条」，
    于是红灯指向完全相反的方向（该改闸的时候你去改常量）。实测踩过一次：
    `[A-Z_]*` 吃不下 `VETO6_EXACT` 里的数字 `6`，`AUTO_UPDATE_EXACT = VETO6_EXACT + (…)`
    整条抠空，闸报「注入片有、脚本没有 README.md / LICENSE」，而两条其实都在且都生效。

    认两种写法：`VAR = (…)` 与 `VAR = OTHER + (…)`（后者是继承写法，`AUTO_UPDATE_EXACT`
    就是）。**别在别处再写一个**——本仓已经因为「两个 helper 各写一份」而分叉过：
    一份吃得下继承写法、另一份不行，今天没出事只是因为调用方恰好只问了平写的那两个常量。
    """
    mm = re.search(rf"^{var} = [A-Z0-9_]*\s*\+?\s*\((.*?)^\)", src, re.M | re.S)
    if not mm:
        raise _ConstParseError(f"{var}：源码里找不到 `{var} = (…)` 或 `{var} = X + (…)` 形态")
    vals = set(re.findall(r'"([^"]+)"', mm.group(1)))
    if not vals:
        raise _ConstParseError(f"{var}：抠到了常量块但里面一个字符串都没有")
    return vals


def check_veto_list_alignment():
    """否决项 6 的路径清单：必载段那份 vs 判定脚本那份，两处手写必须逐条同值。

    这张清单有两个表现层——`closeout-discipline` §否决项 6 的清单与判法（人读的，
    渲染进每会话必载面）与 `signoff_tier_check.py` 的常量（机器算的）。**两处手写必漂**，
    而漂的后果是不对称的：

    - 脚本那份**少**一条 → 那个路径被改时不再进「待裁决」，**静默放行**（假绿）
    - 脚本那份**多**一条 → 天天封顶到用户（假红），闸被人绕着走

    收不成唯一实现的原因是形态不同：一边是 markdown 表格，一边要参与路径匹配。
    所以退而求其次——**同一批改完 + 加这道闸对账**，这是 `closeout-discipline`
    §同一事实的多个表现层 给的第二条路。

    **对账的是路径字面量集合，不是措辞**：表格里「为什么在」那一列随便怎么写都行。
    """
    name = "veto_list_alignment"
    rule = CONTEXT_DIR / "both" / "10-closeout.md"
    script = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "signoff_tier_check.py"
    missing = [str(p.relative_to(FRAMEWORK_ROOT)) for p in (rule, script) if not p.exists()]
    if missing:
        return _fail(name, f"对账源缺失: {missing}")

    text = rule.read_text(encoding="utf-8")
    # 只取「否决项 6 的清单与判法」那一节的表格；不限定节的话，正文别处的反引号路径
    # （比如排除项那段里的 `.claude/skills/**`）会被一起抠进来，对账面当场变成两倍
    m = re.search(r"^###\s+否决项 6 的清单与判法\s*$(.*?)^(?=###\s|##\s)",
                  text, re.M | re.S)
    if not m:
        return _fail(name, "closeout-discipline 里找不到 `### 否决项 6 的清单与判法` 段"
                           "——必载段的清单没了，读者会退回去读 auto-update 那张 10 条表，"
                           "两张表就此混为一谈")
    section = m.group(1)
    doc_paths = set()
    n_rows = 0
    for line in section.splitlines():
        s = line.strip()
        if not (s.startswith("|") and s.count("|") >= 3):
            continue
        first = s.split("|")[1].strip()
        found = set(re.findall(r"`([^`]+)`", first))
        if found:
            n_rows += 1
        doc_paths |= found
    if not doc_paths:
        return _fail(name, "`### 否决项 6 的清单与判法` 段里一条路径都抠不出来"
                           "——表格结构变了（或路径没用反引号包），此时对账面为空、"
                           "本闸对任何漂移都恒绿")

    src = script.read_text(encoding="utf-8")

    # **两侧先归一到同一形态再比**：文档用 glob 记法（`**` / `*`），脚本用
    # 前缀与 (目录, basename 前缀, 后缀) 三元组。不归一就是拿两种写法互相比，
    # 必然逐条报差，而那种红灯不携带任何信息——它只说明两边的记法不同。
    def canon(path):
        return path[:-2] if path.endswith("/**") else path

    code_basenames = set()
    mb = re.search(r"^VETO6_BASENAME = \((.*?)^\)", src, re.M | re.S)
    if mb:
        for d, pre, suf in re.findall(r'\("([^"]*)",\s*"([^"]*)",\s*"([^"]*)"\)', mb.group(1)):
            code_basenames.add(f"{d}{pre}*{suf}")
    try:
        code_paths = (signoff_const_tuple(src, "VETO6_EXACT")
                      | signoff_const_tuple(src, "VETO6_PREFIX") | code_basenames)
    except _ConstParseError as e:
        return _fail(name, f"脚本侧常量解析失败：{e}——**本闸此刻对任何漂移都恒绿**。这是闸自己坏了，不是对方缺条目；先修抽取，别照着红灯去改常量")
    doc_paths = {canon(p) for p in doc_paths}
    # **两侧直接比**：`.claude/rules/` 那一支退役后，文档与脚本之间**不再有形态差**
    # 需要折算。此前那套 `doc_rules` / `doc_rest` / `code_rest` 三分拆存在的唯一理由
    # 就是折算它，现在每次都恒等于「全集」——留着它区分不出任何东西，而下一个人会
    # 以为这里还有个待处理的形态差、照着它的形状继续加东西。
    # shared 记忆那条在脚本里由 `runtime_protected()` 现算（目录位置只有 `_state_io` 一个事实源，
    # 写死一份就是第二个源，漂了之后静默失明），不在常量里——单独按 basename 核
    # 记法两态都认：必载段用抽象记号 `shared/MEMORY.md`（同理不写死目录名，
    # 写死 `.workframe/agent-memory/...` 那天挪目录就得再改一次），老项目里可能仍是
    # 带前缀的全路径。钉住的是「那条在不在」，不是它写成哪一种。
    shared_doc = {p for p in doc_paths
                  if p == "shared/MEMORY.md" or p.endswith("/shared/MEMORY.md")}
    # **二元减，产出新 set**：`doc_paths` 必须保持全集——下面的 OK 文案要报它的大小。
    # 写成 `doc_rest = doc_paths` 再 `doc_rest -= shared_doc`，两个名字指同一个对象，
    # 原地减把原件一起减掉、绿灯上的数字少 1，而**没有任何闸会抓绿灯文案里的数**。
    # 判据可复用：删掉一个中间变量之前，先查下游有没有依赖「它是新对象」的原地修改
    # （`-=` / `|=` / `.update()` / `.append()`）——中间变量常兼着「隔离副本」这个
    # 在名字上完全看不出来的职责。
    doc_rest = doc_paths - shared_doc

    errors = []
    if not shared_doc:
        errors.append("必载段清单里没有 shared 记忆那条，"
                      "而它是应用层最高权威 ＋ 永不清理，漏了等于最该封顶的那个不封顶")
    elif not re.search(r'shared = \(memory \+ "/shared/MEMORY\.md",\)', src):
        # **钉的是那条赋值语句，不是「源码里出现过这个字符串」。** 先前的写法钉后者，
        # 实测把赋值改成 `shared = ()` 之后本闸照样报绿——`shared/MEMORY.md` 还在
        # 同一函数的 docstring 里躺着。零件在货架上完好，不等于装到机器里了。
        # 行为侧的断言在 `tools/test_signoff_tier_check.py` 的「V6-两张表」组里
        # （它 import 脚本、真调 `runtime_protected()` 读产出值）；本条是静态兜底，
        # 覆盖「单测被跳过 / 环境无 git」那条路径。
        errors.append("signoff_tier_check.py 的 runtime_protected 不再把 "
                      "`shared/MEMORY.md` 装进 veto6 那一侧——应用层最高权威 ＋ 永不清理"
                      "的那个文件静默退出封顶集，而现象只是「少了一条待裁决」")
    only_doc = sorted(doc_rest - code_paths)
    only_code = sorted(code_paths - doc_rest)
    if only_doc:
        errors.append(f"必载段有、脚本没有：{only_doc}"
                      "——这几条被改时不再进「待裁决」，**静默放行**（假绿）")
    if only_code:
        errors.append(f"脚本有、必载段没有：{only_code}"
                      "——闸比文档严，改这几条会被封顶而文档里查不到理由（假红，"
                      "下场是这道闸被人绕着走）")
    if errors:
        return _fail(name, "; ".join(errors[:4]))
    # 报「路径字面量」不报「条目」——两者不等（第 2 个条目一行里写了两个路径），
    # 而这个差把实施方自己绕进去过一次：报告里把 6 条目的清单说成了「7 条」。
    return _ok(name, f"(必载段 {len(doc_paths)} 个路径字面量 / {n_rows} 个条目，"
                     f"与 signoff_tier_check 常量同值；shared 记忆那条由 runtime_protected 现算)")


def check_no_plugin_root_env_in_skills():
    """skills 与 templates 文档禁用 `${CLAUDE_PLUGIN_ROOT}` 写法（I-040 防回退闸）。

    官方 plugins-reference：该变量作为环境变量仅注入 hook / MCP / LSP 子进程，Bash 工具
    子进程不在承诺内；skill 内容的字符串替换只在 CC 的 skill 加载通道发生。本框架大量走
    Read 通道（subagent 直读 SKILL.md 原文照抄命令），替换不发生 → shell 对未设置变量
    静默展开为空，命令以「找不到 /scripts/xxx」的迷惑姿态翻车（audit:50 实测教义）。
    统一配方：`$(cat .workframe/state/plugin-root.txt)`。

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
    + 关键回归：scan-git-diff 在 stdin 含 hook JSON 时仍能跑（防 fix-1 倒退）
    + 跨仓扫描（嵌套仓发现与端到端矩阵 / 未跟踪文件 / 锁超时事件留痕）。
    具体组数与断言以测试文件自身为准，此处不复述计数。
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
        summary = _suite_failure_summary(result.stdout)
        return _fail(
            "check_stale_modules_unit_tests_pass",
            f"test exit={result.returncode}: {summary}",
        )
    return _ok("check_stale_modules_unit_tests_pass", "(test suite passed)")


def check_module_close_check_unit_tests_pass():
    """收口闸单测：跑 tools/test_module_close_check.py。

    覆盖 module_close_check.py 九项检查与计数 / 标记命令的正反例（每个判定分支用它自己的失败方式
    打过一遍）+ _load_csm 双重包流回归（首跑实测炸过）+ run_all 结构与非 modules
    项目退化。具体组数与断言以测试文件自身为准，此处不复述计数。
    """
    test_path = FRAMEWORK_ROOT / "tools" / "test_module_close_check.py"
    if not test_path.exists():
        return _fail(
            "module_close_check_unit_tests_pass",
            "tools/test_module_close_check.py missing",
        )
    try:
        # 显式 utf-8 + replace：测试输出含中文/✓/✗ 时不会被系统默认 codec（cp936）误读
        result = subprocess.run(
            [sys.executable, str(test_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except Exception as e:
        return _fail("module_close_check_unit_tests_pass", f"run error: {e}")
    if result.returncode != 0:
        summary = _suite_failure_summary(result.stdout)
        return _fail(
            "module_close_check_unit_tests_pass",
            f"test exit={result.returncode}: {summary}",
        )
    return _ok("module_close_check_unit_tests_pass", "(test suite passed)")


def check_project_scaffold_catalog_unit_tests_pass():
    """catalog 解析单测：跑 tools/test_project_scaffold_catalog.py。

    覆盖 catalog 三层「恰好一个」契约（文件→章节 / 章节→块 / 章节→表）与标题形态族。
    **固化的理由是它此前被独立重建过 3 次**：同一套形态探针在 scratchpad 里搭完即弃，
    下一个人无从知道哪些形态已经验过，于是要么重搭要么漏掉。具体组数与断言以测试文件
    自身为准，此处不复述计数。
    """
    test_path = FRAMEWORK_ROOT / "tools" / "test_project_scaffold_catalog.py"
    if not test_path.exists():
        return _fail(
            "project_scaffold_catalog_unit_tests_pass",
            "tools/test_project_scaffold_catalog.py missing",
        )
    try:
        # 显式 utf-8 + replace：测试输出含中文/✓/✗ 时不会被系统默认 codec（cp936）误读
        result = subprocess.run(
            [sys.executable, str(test_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except Exception as e:
        return _fail("project_scaffold_catalog_unit_tests_pass", f"run error: {e}")
    if result.returncode != 0:
        summary = _suite_failure_summary(result.stdout)
        return _fail(
            "project_scaffold_catalog_unit_tests_pass",
            f"test exit={result.returncode}: {summary}",
        )
    return _ok("project_scaffold_catalog_unit_tests_pass", "(test suite passed)")


def check_signoff_tier_check_unit_tests_pass():
    """签发档判定单测：跑 tools/test_signoff_tier_check.py。

    被测对象 `plugins/core/scripts/signoff_tier_check.py` 的职责就是「判断别的东西对不对」，
    它自己判错时**静默不报**——这一档必须有常驻的失败方式验证，而不是每轮在临时目录里
    重搭一套跑完即弃的探针。**固化的理由是同一套探针已被重建两次**，且在它被挂上运行时
    之前，没有任何别的闸看得住它的回归。

    套件覆盖 `structured_kind()` 的形态矩阵、四段闸门端到端，以及两类**钉决定而非钉正确性**
    的用例（被用户拍板保持现状的行为、已登记未修的现状）——那两类报红时先查「是不是有人
    改了一个当初拍板或明确推迟的行为」。具体组数与断言以测试文件自身为准，此处不复述计数。
    """
    test_path = FRAMEWORK_ROOT / "tools" / "test_signoff_tier_check.py"
    if not test_path.exists():
        return _fail(
            "signoff_tier_check_unit_tests_pass",
            "tools/test_signoff_tier_check.py missing",
        )
    try:
        # 显式 utf-8 + replace：测试输出含中文/✓/✗ 时不会被系统默认 codec（cp936）误读
        result = subprocess.run(
            [sys.executable, str(test_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except Exception as e:
        return _fail("signoff_tier_check_unit_tests_pass", f"run error: {e}")
    if result.returncode != 0:
        summary = _suite_failure_summary(result.stdout)
        return _fail(
            "signoff_tier_check_unit_tests_pass",
            f"test exit={result.returncode}: {summary}",
        )
    return _ok("signoff_tier_check_unit_tests_pass", "(test suite passed)")


def check_signoff_guard_unit_tests_pass():
    """看板签发闸单测：跑 tools/test_signoff_guard.py。

    被测对象 `plugins/core/scripts/signoff-guard.py` 同样是「判断别的东西对不对」的东西，
    且它比判定脚本更危险一格——**它挂在 PostToolUse 上，非零退出会阻断该次工具调用**。
    两个方向的失效各有代价：把关面收窄了（假绿）就是自签失去把关；放宽了（假红）
    就是每次改看板都被拦，而那种闸的下场是被人整个关掉。

    套件分三层：schema（四段 / same_party / verified_ranges / 时间戳形态）、
    阻断面（散文形态签注、无签发块、看板解析不了 → 一律放行且留痕）、
    重算（档位写松必拒、结论字样敲进正文不改变判定、待裁决必拒、重算跑不起来必拒）。
    组数与断言以测试文件自身为准，此处不复述计数。

    **它不覆盖的**：hook 的 stdin 解析与 `sys.exit(2)` 是否真的阻断了该次工具调用——
    那要真实会话才验得了，套件抬头已写明为不含项。
    """
    name = "signoff_guard_unit_tests_pass"
    test_path = FRAMEWORK_ROOT / "tools" / "test_signoff_guard.py"
    if not test_path.exists():
        return _fail(name, "tools/test_signoff_guard.py missing")
    try:
        result = subprocess.run(
            [sys.executable, str(test_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except Exception as e:
        return _fail(name, f"run error: {e}")
    if result.returncode != 0:
        return _fail(name, f"test exit={result.returncode}: {_suite_failure_summary(result.stdout)}")
    # 环境缺 git 或缺 PyYAML 时套件整体跳过并 exit 0——**那不是通过**，必须报出来，
    # 否则 CI 上「跳过」与「全绿」在这一行里长得一模一样。
    if "整套跳过" in result.stdout:
        # 报红而不是报绿：本仓的既有政策就是「没装 PyYAML 那一组整组报红」
        # （贡献者前置依赖，不是用户装框架的前置依赖）。`_warn` 这个 helper 本仓没有，
        # 早先误用它写在这条分支上——那是一条**只在缺依赖时才会走到、走到就 NameError**
        # 的路径，平时全绿看不出来。这类分支正是「不可达与不存在长得一样」的反面：
        # 它可达，只是本机跑不到。
        return _fail(name, "套件因环境缺依赖整体跳过（git / PyYAML）——"
                           "**跳过不是通过**；装上 `pyyaml==6.0.2` 后重跑")
    return _ok(name, "(test suite passed)")


def check_signoff_consumers_carry_pointer():
    """签发契约的消费方 skill 必须指得到判据落点——**四层，各管一段**。

    起因是枚举面自身的盲区：按裸 `pending_qa` 枚举漏掉 `code-review` 与 `feature-breakdown`
    两个消费方（前者通篇不含这个字面量），而它们陈述的是同一个契约。**一个检索面之外
    还有它够不着的地方**，所以这道闸做成三层——**宽收严判的差集只发现得了两个正则的
    能力差，发现不了它们共同够不着的地方**，所以差集之外还得配一条下限断言：

    | 层 | 抓什么 | 失效方式 |
    |---|---|---|
    | ① 下限（具名七个） | 方案定下的**产出**：七个消费方每个都要指得到判据 | 有人删掉某个 skill 里的指针 |
    | ② 宽收严判差集 | 宽面**现算**的候选里有谁没指针 | **新增**消费方没人接线 |
    | ③ 盲区对账 | **`NAMED` 里**宽面看不见的恰好是已知那一个 | 宽面被改窄，或出现新盲区 |
    | ④ 反向对账 | 带着锚点却不在 `NAMED` 里的 skill | 有人把具名清单改小，①③随之一起静默变窄 |

    **本闸只管「指针在不在」，不管「有没有人来读」**：七个里只有 `task-management` 由子
    agent 必载片固定植入（`check_sub_protocol_pins_task_management`），其余六个靠 skill 清单
    的 description 触发，那一层没有机器面。

    **③ 的射程只到 `NAMED` 内**：它对账「具名七个里谁掉出了宽面」，`NAMED` 之外的
    skill 不在其射程——那一片由②（宽面现算 ＋ 下限）管。写出来是因为它容易被读成
    「所有盲区都被对账了」，而那不成立。

    **② 是为什么不能只写①**：具名清单是手写的，新增第八个消费方时它不会自己长大。
    宽面现算让新消费方自动进候选，不接线就红。
    **③ 是为什么不能只写①②**：`NAMED` 里有成员**按设计落在宽面之外**（`code-review`），
    只有①②的话，这个事实只能写进注释——而注释不会报错，宽面哪天把它捎带进来了、或者
    又多掉出去一个，没有任何东西会说话。把它写成等式断言，盲区一变就红。

    **注意③的射程与它挡不住的**：③ 只遍历 `NAMED`，所以「不在 `NAMED` ＋ 宽面也看不见」
    的 skill 只要身上也不带锚点，**四层都够不着**（④只抓带着锚点的；当前 36 个 skill 里有 29 个属此类；独立词表逐个扫过，
    命中的都是「验收标准」「文档 status 流转」这类同形异义，**今天没有活体漏网**，
    但那是当前事实不是机制保证）。另外③ 的两侧都是手写的，单独看它可被
    「改窄宽面 ＋ 把红灯打印出的实际盲区原样粘进期望侧」消音——挡这一手的是②的
    `WIDE_FLOOR` 下限，不是③自己。

    **不做的**：不判指针指向的那段说得对不对（文本资产的正确性机器读不了），
    也不判宽面是不是**完备**的检索面（它按定义不是——③ 登记的就是这件事）。
    """
    name = "signoff_consumers_carry_pointer"
    skills_dir = FRAMEWORK_ROOT / "plugins" / "core" / "skills"
    if not skills_dir.is_dir():
        return _fail(name, "skills/ 目录缺失")

    # 方案定下的七个消费方（下限层；**钉产出不钉零件**）。清单是模块级常量，别在本函数里
    # 另抄一份：抄了之后改一处漏一处，另一处会对着一个过期清单报绿。
    NAMED = SIGNOFF_CONSUMER_SKILLS
    # 宽面：陈述「研发任务走什么流程 / 谁验收签发」的几种字面。**故意不含裸 `@qa`**——
    # 那会把 prompt-evaluation、role-profile-catalog 这类只是提到角色的文件全卷进来，
    # 候选面一宽，②那层就退化成天天报红的假红机器。
    WIDE = re.compile(r"pending_qa|QA 验收|测试\s*/\s*签发|签发\s*→\s*qa|需 @qa 介入")
    # **钉小节锚点，不钉裸的 rule 文件名。** 早先钉 `closeout-discipline`，实测是假绿：
    # `code-review` 里那个字面出现 5 次，其中 4 次早于本次改动（双检口径 / 双事实源 /
    # 契约-验证覆盖缝 / 墓碑判定各引一次），删掉真正的签发指针本闸照样绿。
    # `test-case-design` 更直接——它有 4 处 `closeout-discipline`、**0 处签发指针**，
    # 按裸文件名判会把一个真实缺口读成通过。
    # 小节名在这里就是契约标识符：指针的价值全在「指得到那一节」，节名换了指针即失效。
    PTR = SIGNOFF_PTR_ANCHOR

    def carries(skill):
        """指针可以落在 SKILL.md 或它的 reference/ 里——判据分档细则本来就常放 reference。"""
        d = skills_dir / skill
        for p in [d / "SKILL.md"] + sorted(d.glob("reference/*.md")):
            if p.is_file() and PTR in p.read_text(encoding="utf-8"):
                return True
        return False

    def wide_hit(skill):
        d = skills_dir / skill
        for p in [d / "SKILL.md"] + sorted(d.glob("reference/*.md")):
            if p.is_file() and WIDE.search(p.read_text(encoding="utf-8")):
                return True
        return False

    errors = []

    # ① 下限：七个具名的一个都不能少，且各自指得到判据
    missing_dir = [s for s in NAMED if not (skills_dir / s / "SKILL.md").is_file()]
    if missing_dir:
        errors.append(f"消费方 skill 不存在：{missing_dir}"
                      "——它可能被改名或删了，而签发契约在那一侧就此无人承载")
    no_ptr = [s for s in NAMED if s not in missing_dir and not carries(s)]
    if no_ptr:
        errors.append(f"[下限] 这几个消费方指不到判据落点（缺 `{PTR}`）：{no_ptr}"
                      "——读者在那个 skill 里读到流转规则，却查不到什么情况下能降档，"
                      "只能各自发挥")

    # ② 宽收严判：宽面现算的候选里，差集非空即红（**新增消费方自动进面**）
    all_skills = sorted(p.parent.name for p in skills_dir.glob("*/SKILL.md"))
    wide = [s for s in all_skills if wide_hit(s)]
    # **下限**：三层里唯独宽面自己没有下限，于是「把 WIDE 改窄 ＋ 把红灯打印出来的实际
    # 盲区集合原样粘进 KNOWN_BLIND」这一步能把闸整个消音——③挡得住「宽面漂了」，
    # 挡不住「宽面漂了 ＋ 同时改了期望侧」。加下限后，改窄宽面必然先撞这里。
    # N = 6 的由来：`NAMED` 七个里 `code-review` 按设计在宽面之外（见③），其余六个
    # 当前全被宽面看见。**这是下限不是等式**——新增消费方只会让它变大。
    WIDE_FLOOR = 6
    if not wide:
        errors.append("[差集] 宽面一个 skill 都没命中——检索面塌了，"
                      "此时②这一层对任何漏网都恒绿")
    elif len(wide) < WIDE_FLOOR:
        errors.append(
            f"[下限] 宽面只命中 {len(wide)} 个 skill（下限 {WIDE_FLOOR}）：{wide}"
            "——检索面被改窄了。**别靠改 KNOWN_BLIND 让它转绿**：那会同时把③消音，"
            "两层一起失效而绿灯照打。要么把宽面改回去，要么说明为什么真的少了一个消费方")
    gap = [s for s in wide if not carries(s)]
    if gap:
        errors.append(f"[差集] 宽面命中但指不到判据：{gap}"
                      "——多半是新增了一个陈述签发流程的 skill 而没人接线")

    # ③ 盲区对账：宽面看不见的具名消费方，必须恰好是已知的那一个
    KNOWN_BLIND = {"code-review"}
    blind = {s for s in NAMED if s not in missing_dir and not wide_hit(s)}
    if blind != KNOWN_BLIND:
        errors.append(
            f"[盲区] 宽面看不见的具名消费方实为 {sorted(blind)}，登记在案的是 "
            f"{sorted(KNOWN_BLIND)}——两者不等意味着检索面的能力变了：多出来的是**新盲区**"
            "（它此后只靠①那层手写清单兜着），少掉的说明宽面变宽了、该把它从盲区清单里划掉")

    # ④ 反向对账：**具名清单自己不许静默变小。** 实证——把 `SIGNOFF_CONSUMER_SKILLS` 由 7 个
    # 改成 6 个，①②③一起报绿：①③只**遍历**这个清单，清单短一截就是少查一项；②的差集
    # 看的是「宽面命中却没指针」，被划出清单的那个仍带着指针，不进差集。
    # 地面事实取「谁身上带着锚点」（从文件系统现算），它与清单的差 = 清单漏列的。①钉
    # 「清单 → 指针」，本层钉「指针 → 清单」，两个方向合起来才闭合。
    unlisted = sorted(s for s in all_skills if s not in NAMED and carries(s))
    if unlisted:
        errors.append(
            f"[反向] 这几个 skill 身上带着签发判据锚点 `{PTR}`，却不在 "
            f"`SIGNOFF_CONSUMER_SKILLS` 里：{unlisted}——要么是新增的消费方没接线，"
            "要么是**有人把清单改小了**；①③只遍历那个清单，改小它会让它们一起静默变窄")

    if errors:
        return _fail(name, "; ".join(errors[:5]))
    return _ok(name, f"({len(NAMED)} 个具名消费方全部指得到判据；宽面现算 {len(wide)} 个"
                     f"零漏网；已知盲区 {sorted(KNOWN_BLIND)} 对账一致；带锚点者全部在清单内)")


def check_modules_no_legacy_display_name_or_slug():
    """v0.3.x 防回潮：basic / sub 模块层已合并 `slug` + `display_name` → 单字段 `name`。

    禁止以下回潮：
    - basic-module/module.yaml 顶层 `slug:` / `display_name:`
    - sub-module/submodule.yaml 顶层 `slug:` / `display_name:`
    - basic-module/overview.md frontmatter `basic_slug:`
    - modules-template/ 下任何 .md / .yaml 含已废占位符
      `{{BASIC_SLUG}}` / `{{BASIC_DISPLAY_NAME}}` / `{{SUB_SLUG}}` / `{{SUB_DISPLAY_NAME}}`

    显式保留（以下三类不在防回潮范围，理由各列于行内）：
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


def check_modules_template_placeholders_quoted():
    """modules-template/ 下**任何** `field: {{PLACEHOLDER}}` 都必须用双引号包裹。

    两条独立理由，缺一条都会让白名单收窄到错的地方：

    1. **硬解析错**（更严重，本闸的主要理由）：未加引号的 `{{X}}` 在 YAML 里是「一个
       flow mapping，其唯一的键是另一个 flow mapping」，而 mapping 不可 hash ——
       `yaml.safe_load` 直接抛 `ConstructorError: found unhashable key`，**整份文件
       解析不了**，与值里装的是什么类型无关。
    2. **YAML 1.1 类型推断**：PyYAML 会把 `123` / `2026` / `yes` / `no` / `true` /
       `false` / `null` / `2026-05-09` 推断成 number / bool / null / date，破坏字符串
       语义。用户为模块取数字开头名（季度模块 `2026q1`）或边缘命名（`yes`）时触发。

    **为什么白名单是「任意占位符」而不是只扫用户输入字段**：此前本闸只扫
    `{{BASIC_NAME}}` 等 7 个 user-input 占位符，显式豁免 `{{OWNER_ROLE}}` /
    `{{NOW_ISO}}` / `{{TODAY}}` / `{{REQ_TYPE}}`，理由写的是「framework-hardcoded，
    无类型推断风险」。那个理由**对第 2 条成立，对第 1 条失明**——实测框架自己的
    16 份模板 `safe_load` 失败，其中 13 份的肇事者正是被豁免的 `{{OWNER_ROLE}}`（×6）
    与 `{{NOW_ISO}}`（×11）。类型推断只毁一个字段的语义，硬解析错毁整份文件，
    后者严重一档却不在闸的视野里。

    扫描面刻意保持整文件（不只 frontmatter）：实测 modules-template 下正文里的占位符
    （`- {{TODAY}} 初次解析`、表格 `| {{TODAY}} |`、图片路径 `流程图-{{REQ_SLUG}}-…`）
    没有一处长成 `key: {{X}}`，本模式对它们零命中；收窄到 frontmatter 反而要在
    validate 里再写一份 frontmatter 抽取式。
    """
    name = "modules_template_placeholders_quoted"
    template_root = (
        FRAMEWORK_ROOT
        / "plugins" / "core" / "templates" / "modules-template"
    )
    if not template_root.exists():
        return _fail(name, f"modules-template missing: {template_root}")

    # 命中 `field: {{ANY_PLACEHOLDER}}` 不带引号（缺前导 `"`）。
    # `\s*` 刻意跨行——`skills:` 换行后顶格写 `{{X}}` 同样是非法 YAML（实测
    # ScannerError），收成 `[ \t]*` 就漏掉这一形态。行号取占位符自身的位置
    # （`m.start(1)`）而不是匹配起点，否则跨行命中会把人指到上一行的 key 上。
    unquoted_pattern = re.compile(r'^[^\n#]*?:\s*\{\{([A-Za-z0-9_]+)\}\}', re.MULTILINE)

    failures = []
    for ext in ("*.md", "*.yaml"):
        for f in template_root.rglob(ext):
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            for m in unquoted_pattern.finditer(text):
                line_no = text.count("\n", 0, m.start(1)) + 1
                rel = f.relative_to(template_root).as_posix()
                failures.append(f"{rel}:{line_no} `{{{{{m.group(1)}}}}}` not quoted")

    if failures:
        return _fail(
            name,
            "占位符未加引号——渲染前整份文件 safe_load 会抛 ConstructorError"
            "（found unhashable key），渲染后还有 YAML 1.1 类型推断风险: "
            + "; ".join(failures[:8])
            + (f" (+{len(failures) - 8} more)" if len(failures) > 8 else ""),
        )
    return _ok(name, "(all placeholders quoted with double-quotes)")


def _close_check():
    """惰性 import `plugins/core/scripts/module_close_check`——基准落点字段的消费方。

    与 `_scaffold()` 同款理由与同款位置纪律：只在检查函数体内调用、异常由调用方转
    `_fail`，不放模块顶层。import 本身无副作用（该脚本模块级只有常量，加载
    check-stale-modules 的 `_load_csm` 是惰性的）。
    """
    scripts = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import importlib
    return importlib.import_module("module_close_check")


# close-check 组装全部 anchor 的那一个函数——下面几道闸判「消费方真的在用这个符号」时
# 都把搜索面收窄到它，避免在整份源码里撞上定义行 / 注释 / docstring 里的同名字样。
_ANCHOR_FUNC = "_collect_submodules"
# 检查 6（code-map 覆盖率对账）里真正判 `code_map_coverage` 取值域的那个函数。
_COVERAGE_FUNC = "check_code_map_coverage"
# 子模块 overview 里「最近同步」行的行首字面量——出厂模板与渲染方共用同一个锚。
_SYNC_LINE_PREFIX = "- **最近同步**："


def check_modules_template_sync_ref_field():
    """出厂模板必须产出 close-check 检查 3 对账所需的 `last_synced_ref` 落点。

    背景：检查 3 对账三个基准落点——current-state frontmatter 的 `source_ref`、
    submodule.yaml 的 `last_synced_ref`、overview 的「基准 commit」行；**任意两个不同值
    即 error**。三个落点里只有 `last_synced_ref` 有本闸这样的「模板必须产出它」的
    validate 闸——`source_ref` 只被 close-check 消费，没有对应的产出方闸。
    但检查 3 只在 `filled >= 2` 时才比对，**落点不足直接整条跳过**。而出厂模板
    此前只有 `last_synced_at`、根本没有 ref 字段，于是按模板新建的子模块天生少一个落点
    ——闸依赖的字段，产出方不产出，缺陷形态是**静默失去看守**而非报错。

    三层断言：
      ①字段取得到（用消费方自己的 `_scalar_field` 求证，不另写一套 YAML 解析）
      ②出厂值必须为空——预填任何 hash 都会给新项目一个假基准，且空值恰好被检查 3 的
        `filled` 过滤掉，模板骨架不会误伤
      ③消费方确实按这个字段名建 anchor——防的是「消费方把字段改名、模板还留旧名」
        这种两边分叉；此时模板与闸都各自自洽，没有任何东西会报警。
        **判的是 `_collect_submodules` 里那一处调用，不是「源码里出现过这个字面量」**：
        后者实测有洞——把取值调用换成恒返回 `None` 的分叉实现、同时保留 `_scalar_field`
        的定义与该字面量，本闸与①层一起报绿，而消费方读到的已是另一套口径
    """
    name = "modules_template_sync_ref_field"
    field = "last_synced_ref"
    tpl = (
        FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "modules-template"
        / "sub-module" / "submodule.yaml"
    )
    if not tpl.exists():
        return _fail(name, f"modules-template sub-module/submodule.yaml missing: {tpl}")
    try:
        mcc = _close_check()
    except Exception as e:
        return _fail(name, f"import module_close_check 失败（字段判定式在它那里）: {e}")

    text = tpl.read_text(encoding="utf-8")
    # ①消费方判定式求证
    val = mcc._scalar_field(text, field)
    if val is None:
        return _fail(
            name,
            f"模板 sub-module/submodule.yaml 取不到 `{field}`——按模板新建的子模块只有 "
            f"2 个基准落点，module-close-check 检查 3 需要 >=2 个**已填**落点才比对，"
            f"于是那条对账对新项目静默整条跳过（不报错，只是没人看着了）",
        )
    # ②出厂值必须为空
    if val.strip():
        return _fail(
            name,
            f"模板 `{field}` 出厂值不为空（{val!r}）——会给每个新建子模块一个假基准；"
            f"留空才会被检查 3 的 filled 过滤掉，骨架不参与对账",
        )
    # ③消费方按同一字段名建 anchor
    src_path = (
        FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "module_close_check.py"
    )
    try:
        src = src_path.read_text(encoding="utf-8")
    except Exception as e:
        return _fail(name, f"读 module_close_check.py 失败: {e}")
    # mode="any"：`_collect_submodules` 里 `_scalar_field` 有 4 处调用（实核分布：4 处
    # 全承重，四个字段各一处），要求每处都带同一个字段名是无意义的——本闸只需求证
    # 「这个字段仍由那个取值函数取」，故用 ⊆ 而非 ==。
    sites, _bad, err = _symbol_use_sites(src, "_scalar_field", func=_ANCHOR_FUNC,
                                         paired_with=f'"{field}"', mode="any")
    if err:
        return _fail(name, f"module_close_check.py: {err}——检查 3 的 anchor 就在那里组装")
    if not sites:
        return _fail(
            name,
            f"`{_ANCHOR_FUNC}` 里没有一处 `_scalar_field(…, \"{field}\")` 调用——消费方"
            f"要么把字段改了名、要么换了另一套取值实现，而模板还留着旧名字：两边各自"
            f"自洽、谁也不会报警，检查 3 却少一个落点。**注意本层判的是调用点不是字面量"
            f"存在性**——只查字面量时，把调用换成分叉实现、保留 `_scalar_field` 定义与该"
            f"字面量即可骗过它（实测全绿）",
        )
    return _ok(name, f"(template ships empty `{field}`; consumer calls it at "
                     f"{len(sites)} site(s))")


def check_index_refresh_emits_overview_ref():
    """索引渲染规程规定的「最近同步」行形态，必须能被 close-check 的基准提取式认出。

    背景：基准引用有三个落点——current-state frontmatter 的 `source_ref`、submodule.yaml
    的 `last_synced_ref`、子模块 overview「最近同步」行的「基准 commit」括注。**三个都由
    close-check 检查 3 对账，差别在产出方**：前两个是 code-to-doc 直接写的 yaml /
    frontmatter 字段，第三个落在 AUTO-INDEX 机器重写段**内**——只能由
    `module-index-refresh` 的取数规程渲染出来，规程不写它就没有任何东西会写它。规程不
    产出它（或括注措辞漂了）时两件事同时发生、且都不报错：三方对账静默降为两方，段内
    已写的括注被下次刷新抹掉。

    四层断言（形态由实测的假绿反推，不是照同族抄的）：
      ①**定位取数表那一行**再取形态，不在整份 SKILL.md 里裸搜——文件级探测挡不住
        「改成『不再产出该括注（历史形态为…已废弃）』」与「靠前塞诱饵 span」两种写法
      ②行内形态 **≥1 条且每条都要能过**——`⊆`（有一条能过即算过）会放行「行内并存
        一条坏形态」；`==1` 又会在「同时示出新写形态与就地替换后形态」上假红
      ③用**消费方自己的正则**求证提得出 ref（正则从 module_close_check import，不在此
        处另写一份），并断言替换后不残留闸不认识的占位（占位改名时本闸会静默失明）
      ④出厂模板**肯定 + 否定两半**：段内 `- **最近同步**：` 恰 1 行（整行被删时新建
        子模块既看不到目标形态、也没有可被就地替换的锚行），且该行不得被同一正则提出
        ref（假基准在未同步期间静默存在）

    **本闸不保证什么**（写进这里而不是只写在账本里）：它验的是**规程文本**，不是渲染
    行为，也不是落点的存在性——括注在运行期被抹掉时，检查 3 在 `filled==2` 下照常比对
    并报 ✓，没有任何东西会说话。
    """
    name = "index_refresh_emits_overview_ref"
    sample_ref = "a1b2c3d4"
    skill = (
        FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "module-index-refresh"
        / "SKILL.md"
    )
    tpl = (
        FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "modules-template"
        / "sub-module" / "overview.md"
    )
    if not skill.exists():
        return _fail(name, f"module-index-refresh/SKILL.md missing: {skill}")
    if not tpl.exists():
        return _fail(name, f"modules-template sub-module/overview.md missing: {tpl}")
    try:
        mcc = _close_check()
    except Exception as e:
        return _fail(name, f"import module_close_check 失败（提取式在它那里）: {e}")
    ref_re = getattr(mcc, "OVERVIEW_REF_RE", None)
    if ref_re is None:
        return _fail(
            name,
            "module_close_check 里没有 OVERVIEW_REF_RE——基准提取式大概率被改回内联写法。"
            "本闸就只能自己抄一份正则，而两份正则各自演进正是它要防的分叉",
        )
    # 常量**存在**不等于消费方在用它：把提取调用换回内联正则、常量留成孤儿时，上面那句
    # `getattr` 照样取得到（实测全绿），而 close-check 读的已是另一份正则。所以还要判
    # 它在真实取值路径上被引用。
    mcc_path = (
        FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "module_close_check.py"
    )
    try:
        mcc_src = mcc_path.read_text(encoding="utf-8")
    except Exception as e:
        return _fail(name, f"读 module_close_check.py 失败: {e}")
    # mode 默认 any + 无 paired_with：实核分布该常量在此函数体内恰 1 处，无需配对；
    # 「有一处真实引用」即证明提取式没被换成内联副本。
    ref_sites, _rb, ref_err = _symbol_use_sites(mcc_src, "OVERVIEW_REF_RE",
                                                func=_ANCHOR_FUNC)
    if ref_err:
        return _fail(name, f"module_close_check.py: {ref_err}——overview 基准就在那里提取")
    if not ref_sites:
        return _fail(
            name,
            f"`{_ANCHOR_FUNC}` 里没有一处引用 OVERVIEW_REF_RE——提取式已被换成内联副本，"
            f"常量退化成孤儿。此时本闸拿着这个没人用的常量去验规程，规程与真正的提取式"
            f"从此各自演进，不会被任何东西发现（实测：换成内联后 validate 与 close-check "
            f"单测双双全绿；再把内联口径漂开，第三个基准落点从每个项目里静默消失，仍全绿）",
        )

    # ①定位取数表那一行——**不在整份 SKILL.md 里裸搜**。整份文件级的 code span 存在性
    #   探测有两个洞（都实测过）：把取数表那句改成「不再产出该括注（历史形态为 …，已
    #   废弃）」照样绿；在文件靠前处塞一个合法诱饵 span 也照样绿。收窄到那一行即两洞同消。
    #   锚点用**宽形态**（以 `|` 开头 + 含段名）而不是整串表格首列字面量：首列改成带
    #   链接的写法是合法排版改动，窄锚点会在它上面假红（实测）。
    skill_lines = skill.read_text(encoding="utf-8").splitlines()
    rows = [l for l in skill_lines
            if l.startswith("|") and "current-state-summary" in l]
    if len(rows) != 1:
        return _fail(
            name,
            f"module-index-refresh/SKILL.md 的 current-state-summary 取数表行定位到 "
            f"{len(rows)} 行（应恰 1 行）——整条被删、被拆成多行、或段名改了。定位不到就"
            f"没有可照抄的行形态，渲染方不会产出这个括注",
        )
    row = rows[0]

    # ②该行内的形态字面量：**≥1 条且每一条都要能过**（不是「有一条能过即算过」）。
    #   `⊆` 语义下，行内并存一条坏形态时渲染方可能照抄那条坏的；`==1` 又会在「同时示出
    #   新写形态与就地替换后形态」这类合法写法上假红（两种都实测过），故取「≥1 且全解析」。
    forms = [s for s in re.findall(r"`([^`\n]*基准 commit[^`\n]*)`", row)
             if "最近同步" in s]
    if not forms:
        return _fail(
            name,
            f"取数表 current-state-summary 行内取不到「最近同步 + 基准 commit」的行形态"
            f"字面量——要么形态子句被删、要么括注措辞漂到认不出。不产出这个括注时：基准"
            f"三方对账静默降为两方，且 overview 段内已写的括注会被下次刷新抹掉，两种失效"
            f"都不报错。当前行内容: {row[:160]}",
        )

    # ③每条形态都必须被消费方正则认出，且替换后不得残留闸不认识的占位。
    for form in forms:
        if "<ref>" not in form:
            return _fail(
                name,
                f"行形态 {form!r} 里没有 `<ref>` 占位——占位名改了而本闸没跟上。本闸只会"
                f"填 `<ref>` 与 `<日期>` 两个占位，改名后它填不进真值，等于对这条形态失明",
            )
        probe = form.replace("<ref>", sample_ref).replace("<日期>", "2026-01-01")
        m = ref_re.search(probe)
        if not (m and m.group(1) == sample_ref):
            return _fail(
                name,
                f"行形态 {form!r} 提取不出基准 ref（消费方正则 {ref_re.pattern!r}）"
                f"——照它渲染出来的括注 close-check 认不出，第三个落点等于没有",
            )
        leftover = re.search(r"<[^>]+>", probe)
        if leftover:
            return _fail(
                name,
                f"行形态 {form!r} 在替换后仍残留占位 {leftover.group(0)!r}——本闸只认识 "
                f"`<ref>` 与 `<日期>`，出现第三个占位说明形态改了而闸没跟上：那部分不会被"
                f"填成真值，本闸对它是失明的",
            )

    # ④出厂模板：**肯定 + 否定两半**。只有否定断言时，把占位行整行删掉照样绿（实测）
    #   ——而那正是新建子模块看不到目标形态、也没有可被就地替换的锚行的状态。
    tpl_lines = tpl.read_text(encoding="utf-8").splitlines()
    seg = [i for i, l in enumerate(tpl_lines) if "AUTO-INDEX:START:current-state-summary" in l]
    seg_end = [i for i, l in enumerate(tpl_lines) if "AUTO-INDEX:END:current-state-summary" in l]
    if len(seg) != 1 or len(seg_end) != 1 or seg_end[0] <= seg[0]:
        return _fail(
            name,
            f"出厂模板 sub-module/overview.md 的 current-state-summary 段边界不成对"
            f"（START {len(seg)} 个 / END {len(seg_end)} 个）——段都没了，渲染方无处落笔",
        )
    ph = [l for l in tpl_lines[seg[0]:seg_end[0]] if l.startswith(_SYNC_LINE_PREFIX)]
    if len(ph) != 1:
        return _fail(
            name,
            f"出厂模板 current-state-summary 段内 `{_SYNC_LINE_PREFIX}` 开头的行有 "
            f"{len(ph)} 条（应恰 1 条）——整行被删时新建子模块既看不到目标形态、也没有"
            f"可被就地替换的锚行；重复时渲染方不知道该换哪一条",
        )
    tm = ref_re.search(ph[0])
    if tm:
        return _fail(
            name,
            f"出厂模板的占位行提得出基准 ref（{tm.group(1)!r}）——每个新建子模块天生带一个"
            f"假基准。**它不会当场报红**：骨架的 `source_ref` 与 `last_synced_ref` 都是空串，"
            f"被检查 3 的 filled 过滤后只剩这一个落点、不足两处而整条跳过。危害是这个假值"
            f"在未同步期间**静默存在**，等 code-to-doc 跑完补上另两个落点才炸",
        )
    return _ok(name, f"(spec row emits {len(forms)} parseable form(s); "
                     f"template ships exactly 1 placeholder line, no ref)")


def check_modules_template_code_map_coverage_field():
    """出厂模板必须产出 close-check 检查 6 用的豁免声明字段 `code_map_coverage`。

    背景：检查 6（code-map 覆盖率对账）要求 `source_paths` 与 `code_paths` 展开文件集相等。
    有些子模块**有意不设 code-map**（纯文档档等裁量）——那是合法状态，但机器读不出「有意」
    与「忘了做」的区别。字段就是把这条区分从散文提到 schema 上。

    **为什么不能靠 `source_ref` 为空当豁免信号**：那是机械副作用不是声明。同一个空值
    既可能是「有意不设」也可能是「新建了还没跑 code-to-doc」，两者该走的分支完全不同
    （前者永久 info、后者是 warn 加一句「跑 code-to-doc」）。靠它区分等于把两种状态
    压成一种，且这正是「启发式假绿」的教科书形态。

    三层断言（与 `check_modules_template_sync_ref_field` 同形，但③层多一条）：
      ①字段取得到——用消费方自己的 `_scalar_field` 求证，不另写一套 YAML 解析
      ②出厂值必须恰为 "required"——预填 exempt 会给每个新建子模块一个**假豁免**，
        而假豁免是静默的（检查 6 直接跳过，不报错、只是没人看着了）
      ③消费方确实按这个字段名**和这套取值域**判定——字段名一侧判 `_collect_submodules`
        里那处取值调用，取值域一侧判 `check_code_map_coverage` 函数体里认不认 "exempt"。
        只查字段名挡不住「模板留 required/exempt、消费方改判 true/false」这种两边各自
        自洽的分叉；`last_synced_ref` 没有取值域，所以那道闸不需要这半条。
        **两侧都判「在哪个函数体里被用到」而不是「整份源码里出现过」**：后者实测有洞
        ——docstring / 注释 / 另一条分支里残留的同名字样会替真正的判定分支挡箭
    """
    name = "modules_template_code_map_coverage_field"
    field = "code_map_coverage"
    default = "required"
    exempt = "exempt"
    tpl = (
        FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "modules-template"
        / "sub-module" / "submodule.yaml"
    )
    if not tpl.exists():
        return _fail(name, f"modules-template sub-module/submodule.yaml missing: {tpl}")
    try:
        mcc = _close_check()
    except Exception as e:
        return _fail(name, f"import module_close_check 失败（字段判定式在它那里）: {e}")

    text = tpl.read_text(encoding="utf-8")
    # ①消费方判定式求证
    val = mcc._scalar_field(text, field)
    if val is None:
        return _fail(
            name,
            f"模板 sub-module/submodule.yaml 取不到 `{field}`——按模板新建的子模块没有豁免声明位，"
            f"于是「有意不设 code-map」只能靠 source_ref 空之类的机械副作用去撞；"
            f"撞对了没人知道是撞的，撞错了就是假绿",
        )
    # ②出厂值必须恰为 required
    if val.strip() != default:
        return _fail(
            name,
            f"模板 `{field}` 出厂值是 {val.strip()!r} 而非 {default!r}——"
            + (f"预填 {exempt!r} 会让每个新建子模块天生豁免覆盖率对账，"
               f"而豁免是静默跳过：检查 6 不报错，只是不再有人看着"
               if val.strip() == exempt else
               f"出厂值必须是闭合取值域里的 {default!r}，否则新建子模块一上来就撞检查 6 的取值域断言"),
        )
    # ③消费方按同一字段名 + 同一取值域判定
    src_path = (
        FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "module_close_check.py"
    )
    try:
        src = src_path.read_text(encoding="utf-8")
    except Exception as e:
        return _fail(name, f"读 module_close_check.py 失败: {e}")
    # mode="any"：理由同 `modules_template_sync_ref_field`（4 处调用各取一个字段）。
    name_sites, _nb, name_err = _symbol_use_sites(src, "_scalar_field", func=_ANCHOR_FUNC,
                                                  paired_with=f'"{field}"', mode="any")
    if name_err:
        return _fail(name, f"module_close_check.py: {name_err}——豁免声明就在那里取值")
    if not name_sites:
        return _fail(
            name,
            f"`{_ANCHOR_FUNC}` 里没有一处 `_scalar_field(…, \"{field}\")` 调用——消费方"
            f"要么把字段改了名、要么换了另一套取值实现，而模板还留着旧名字：两边各自"
            f"自洽、谁也不会报警，而检查 6 从此对每个子模块都读到 None、把「有意豁免」"
            f"的那个也拉进对账报红",
        )
    # 取值域一侧**换断言形状**：不问「exempt 被引用了吗」，问「那两处比较判定本身还在
    # 吗」。分布实核：`exempt` 在 `check_code_map_coverage` 内出现 9 处，**只有 2 处承重**
    # （取值域成员测试 + 豁免等值判定），另 7 处是变量名 `n_exempt` 与输出文案——把两处
    # 真判定全漂成别的取值、只留那 7 处，任何存在性断言都照样报绿（实测）。
    ops, domains, cov_err = _literal_compare_shapes(src, _COVERAGE_FUNC, exempt)
    if cov_err:
        return _fail(name, f"module_close_check.py `{_COVERAGE_FUNC}`: {cov_err}"
                           f"——检查 6 的取值域判定在那里")
    if "Eq" not in ops:
        return _fail(
            name,
            f"`{_COVERAGE_FUNC}` 里没有以 {exempt!r} 为对象的等值判定——豁免分支已改判"
            f"别的取值（true/false 之类）：模板教用户写 {exempt!r}，消费方不认，用户"
            f"以为声明了豁免、实际每轮照样报红。**判的是比较判定本身，不是这个词有没有"
            f"出现过**——它在同一个函数体里另有 7 处非承重出现（变量名 + 输出文案），"
            f"存在性断言会被它们替真正的判定分支挡箭",
        )
    if not any(d == {default, exempt} for d in domains):
        got = " / ".join(sorted("{" + ", ".join(sorted(d)) + "}" for d in domains)) or "无"
        return _fail(
            name,
            f"`{_COVERAGE_FUNC}` 的取值域成员测试不再恰为 {{{default!r}, {exempt!r}}}"
            f"（实际: {got}）——取值域两边分叉时，写错的人以为自己声明了豁免而对账其实"
            f"一直在跑；多一个取值则模板没教、用户写不出来",
        )
    return _ok(name, f"(template ships `{field}: {default}`; consumer reads it at "
                     f"{len(name_sites)} site(s); {_COVERAGE_FUNC} still judges "
                     f"`{exempt}` via {sorted(ops)} on domain {{{default}, {exempt}}})")


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

    真正要守的不变量变成了：`CONFIG_USER_FIELDS` 的每个成员一律走 `setdefault`（本地
    缺失才写），绝不用 `existing[key] = ...` 直接覆盖——接入已有项目时用户可能早就手工
    配过。`close_check` 尤其依赖这一条：**关闭账本检查的方式是把它改成空壳 `{}`**，
    一旦哪天改成直接赋值，用户的关闭意图会在每次重跑 scaffold 时被静默覆盖回默认值。
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
    # 用户字段一律不得直接赋值覆盖（清单与 scaffold 的 CONFIG_USER_FIELDS 一一对应）
    for field in ("project_type", "dormant_profile", "role_profile", "close_check"):
        if re.search(rf'existing\[\s*["\']{field}["\']\s*\]\s*=', body):
            return _fail(name, f"write_workframe_config 直接覆盖用户字段 {field}（应走 setdefault）")
    return _ok(name)



def check_release_consistency():
    """发布前一致性扫描：marketplace.json 的旧口径零残留。

    曾经还扫一类「真实来源项目名出现在运行时代码里」。那道扫描的**关键词本身就是
    真实项目名**——等于把它永久写进公开仓库，而工作区之所以显示"干净"，只是因为
    检测器自己在白名单里。该名字已从工作区全部资产清除，扫描随之移除。

    更早还扫过 install.py（『3 条通用纪律』）与 dev-docs 两份文档，随 2026-08-10
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
        FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "audit" / "SKILL.md",
        CONTEXT_DIR / "both" / "20-protocol-common.md",
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
    """通用协议片的事件流段：`success` 两侧取值规则 + `session_id` 归属 + `task_blocked` 策略。

    **不再要求片里出现手写 JSON 示例**（原断言要 `"success":true` 字面量）。机械写法已改由
    `workframe-event` 承担，要求片里摆一个可照抄的 JSON 等于邀请模型绕开命令手拼——与
    `model_mediated_events_have_append_samples` docstring 里那条代码通道例外同一个理由。

    **改成段内判而不是全文判**：全文判时「文里别处有个 `false`」就能把它糊过去，而这段要
    钉的恰恰是「同一处同时说清 true 与 false 两侧」。`success` 那条规则被砍掉半边时，全文
    判照绿——它是本闸唯一真正想防的那种漂移。
    """
    name = "agent_protocols_documents_success_false_and_session_id"
    path = CONTEXT_DIR / "both" / "20-protocol-common.md"
    if not path.exists():
        return _fail(name, f"注入片缺失: {path.relative_to(FRAMEWORK_ROOT)}")
    text = path.read_text(encoding="utf-8")
    missing = []
    # 段 = 空行分隔的块；列表项各自成行，故按行也一并当候选段（一条 bullet 讲完一个字段）
    blocks = [b for b in re.split(r"\n\s*\n", text)] + text.splitlines()
    success_blocks = [b for b in blocks if "success" in b]
    if not success_blocks:
        missing.append("success 取值规则整段缺失")
    elif not any("`true`" in b and "`false`" in b for b in success_blocks):
        missing.append("success 的 true / false 两侧规则不在同一段内"
                       "（半边被砍掉时全文判会照绿，故按段判）")
    if "session_id" not in text:
        missing.append("session_id 归属说明（谁写、取不到时什么后果）")
    if "task_blocked" not in text:
        missing.append("task_blocked 的 producer 策略")
    if missing:
        return _fail(name, f"{path.relative_to(FRAMEWORK_ROOT)} 缺: {missing}")
    return _ok(name, "(success 两侧规则同段 + session_id 归属 + task_blocked 策略齐备)")


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
    2. **按内容 hash 对账的检查**恒报不一致——
       源是 LF、副本是 CRLF，字节不同但内容相同。

    这个坑框架里早认识过：`module_init.py` 有个「统一 LF 写盘」的辅助函数，注释写得
    明明白白——**但只修了那一处，没扫全仓**，`_state_io.atomic_write`（所有 state
    文件的公共写入路径）一直漏着。本闸把「扫全仓」变成机器保证。

    只查写入调用（`write_text` / 以 `w` `a` 模式打开），不碰读取。二进制写
    （`write_bytes` / mode 带 `b`）天然无此问题，不在范围内。`os.open` 返回裸 fd、
    没有文本层，谈不上 `newline`，不在范围内；**`os.fdopen` 在范围内**——它是 `io.open`
    在 fd 上的薄包装，接受并遵守 `newline=`，不给就照样在 Windows 上把 LF 翻成 CRLF
    （实测：`os.fdopen(fd,"w",newline="")` 写出 `b"a\nb\n"`，不给 `newline=` 写出
    `b"a\r\nb\r\n"`）。

    **判定走 AST**（打开调用的枚举与 mode 解析复用 `_open_call_modes`，全仓唯一实现）。
    前身是逐行正则 + 括号配平的续行拼接，两个已实证的毛病：docstring 正文里写着
    `mode="a"` 的那一行会被判成一处未钉 newline 的写入（真实假红，逼人改文案），
    而 `open(p, "a+b")` 这类拼法它又整个漏掉。AST 下注释与 docstring 天然不是 Call 节点，
    跨行调用也不需要额外拼接。

    **能力边界**：扫描面 = `plugins/**` 与 `tools/**` 的全部 Python（含无扩展名的
    shebang 入口，见 `_python_sources`）。**模块风格与低层 `os.fdopen` 两类**下 mode
    静态解析不出的打开调用按未钉 newline 报出（fail-safe）——`os.fdopen` 在
    `_open_call_modes` 的分类里属第 4 类「低层」而非第 2 类「模块风格」，此前这句只写了
    模块风格，行为严于陈述、方向 fail-safe，但陈述覆盖不全；
    `<obj>.open(<解析不出的位置实参>)` 例外，那一类与 `pdfplumber.open(path)` 同形
    （见 `_append_open_calls` 的同一条边界），报了就是假红。
    `newline` 由关键字实参判定——它本来就传不了位置参数。

    **排除的只有 `os.open`**（返回裸 fd、没有文本层，传 `newline=` 直接抛 TypeError）。
    **`os.fdopen` 不排除**：它是 `io.open` 在 fd 上的薄包装，接受并遵守 `newline=`，
    不给就照样把 LF 翻成 CRLF。按 `os.` 前缀一刀切会放宽本闸的口径（BUG-008 即此），
    而共用实现 `_open_call_modes` 的另一个消费方 `_append_open_calls` 反过来**要收**
    `os.open(... O_APPEND)`——**两个消费方的口径各判各的，不因共用实现而合并**。
    """
    offenders = []
    for root in ("plugins", "tools"):
        base = FRAMEWORK_ROOT / root
        if not base.is_dir():
            continue
        for p in _python_sources(base):
            src = p.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(src)
            except SyntaxError as e:
                offenders.append(p.relative_to(FRAMEWORK_ROOT).as_posix() + f"  解析失败: {e}")
                continue
            lines = src.splitlines()
            bad_calls = []
            for call, mode, style, ambiguous_positional in _open_call_modes(tree):
                if style == "os.open + O_APPEND":
                    continue                      # 裸 fd，没有文本层，谈不上 newline
                if mode is None and ambiguous_positional:
                    continue                      # `pdfplumber.open(path)` 那一类，见下方能力边界
                if mode is not None and ("b" in mode or not set(mode) & set("wa")):
                    continue                      # 二进制写与只读不在范围内
                bad_calls.append(call)
            for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
                if isinstance(call.func, ast.Attribute) and call.func.attr == "write_text":
                    bad_calls.append(call)
            for call in sorted(bad_calls, key=lambda c: c.lineno):
                if any(k.arg == "newline" for k in call.keywords):
                    continue
                rel = p.relative_to(FRAMEWORK_ROOT).as_posix()
                snippet = lines[call.lineno - 1].strip()[:70] if call.lineno <= len(lines) else ""
                offenders.append(rel + ":" + str(call.lineno) + "  " + snippet)
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
    - 用 `spec_from_file_location` 加载别的脚本的（`tools/test_check_stale_modules.py`
      这一类）：被加载的 `check-stale-modules.py` 是**模块级**包装，加载那一刻两条流
      已经包好。

    自己再包一层就是两个 TextIOWrapper 抢同一个 buffer：先被回收的把 buffer 关掉，
    另一个当场 `ValueError: I/O operation on closed file` + `lost sys.stderr`。
    落这道闸时给这类文件加过包装，其中一个当场全崩——**而那一轮
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
        "tools/test_check_stale_modules.py":
            "加载 check-stale-modules.py，那边是模块级包装",
        "plugins/core/scripts/module_close_check.py":
            "main() 先加载 check-stale-modules.py，那边是模块级包装（重复包装实测炸）",
        "tools/test_module_close_check.py":
            "加载 module_close_check.py → check-stale-modules.py，包装在最深那层",
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
    的文件；第二次跑时 `check_setup_state` 看见文件存在却缺当时的其余必查步，
    报 error「初始化未走完 0/3 步」——项目其实装得好好的，是体检把它「检坏」的，而且不可逆
    （用户不会知道那份文件该删）。这类 bug 静态扫描永远看不见：单跑一次全绿，跑第二次才红。

    做法：最小沙盒项目上对**整个项目目录**做跑前跑后全量快照（相对路径 + 内容 hash），
    断言零差异。范围是整个目录而不只是 setup-state.json——将来任何检查顺手写点什么进
    被检项目，都会在这里当场暴露。

    **快照范围一度钉在 `proj / ".claude"`，而运行态目录在 2026-08 之后已搬到 `.workframe/`**
    ——于是这道闸对它自己守的那个 bug 完全失明：把 `_record_acceptance` 的存在性早退删掉
    原样重放，它仍然报绿（写出来的 `.workframe/state/setup-state.json` 落在快照范围之外）。
    范围现在取 `proj` 本身，**不再写任何具体子目录**：运行态目录位置的唯一事实源是
    `_state_io`，这里复述一次就是第二个事实源，而它上一次分叉整整两个月没人发现。

    **能力边界——本闸的绿依赖探针项目「没有 `setup-state.json`」这一个属性。**
    doctor 的 `_record_acceptance` 对**已存在**的那份文件按设计会写一步 `acceptance`
    （它只在文件不存在时才什么都不做）。三形态实测：不带该文件 ⇒ 零改动；带一份健康的
    ⇒ 该文件被改写；真 scaffold 铺的完整项目 ⇒ 同样被改写。所以**「doctor 跑一遍不改被检
    项目任何文件」在一般情况下并不成立**，本闸证明的是它更窄的那个版本。
    ⇒ 谁要给探针补上 `setup-state.json`（想顺带覆盖 `setup_state` 分支，是很自然的下一步），
    本闸会当场红、并喊「体检工具必须只读」——**那不是回归，是本边界被踩到**；
    届时要么给 `acceptance` 那一处写入开豁免，要么把探针拆成两个。
    """
    import hashlib
    import os
    import subprocess
    import tempfile
    doctor = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    if not doctor.exists():
        return _fail("doctor_readonly_on_probe_project", "workframe_doctor.py 不存在")
    agents_tpl = (FRAMEWORK_ROOT / "plugins" / "core" / "templates"
                  / "agents-md-template.md")
    if not agents_tpl.exists():
        return _fail("doctor_readonly_on_probe_project",
                     "agents-md-template.md 不存在——最小合规项目铺不出 AGENTS.md，"
                     "此时的「零差异」是探针没建起来，不是 doctor 只读")

    def snapshot(root):
        snap = {}
        for p in sorted(root.rglob("*")):
            if p.is_file():
                snap[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
        return snap

    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        state = proj / ".claude" / "workframe-state"
        state.mkdir(parents=True)
        (state / "plugin-root.txt").write_text(str(FRAMEWORK_ROOT / "plugins" / "core"),
                                               encoding="utf-8", newline="")
        # 走 scaffold 的唯一写入点渲染，不原样复制模板：模板带占位符，复制出来的 AGENTS.md
        # 会被 doctor 的占位符残留扫描报 warn，探针项目就不再是「最小合规」
        try:
            _scaffold().ensure_agents_md(proj)
        except Exception as e:
            return _fail("doctor_readonly_on_probe_project",
                         f"最小合规项目铺不出 AGENTS.md（渲染失败 {type(e).__name__}: {e}）")
        (proj / "projects").mkdir()
        (proj / "projects" / "board.yaml").write_text(
            "summary:\n  total: 0\ntasks: []\n", encoding="utf-8", newline="")

        before = snapshot(proj)
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
        after = snapshot(proj)

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
                         "doctor 跑一遍后被检项目目录发生变化（体检工具必须只读）："
                         + "；".join(bits))
    return _ok("doctor_readonly_on_probe_project",
               "doctor 两个 group 跑完不改探针项目任何文件"
               "（探针不带 setup-state.json；带了的话 acceptance 记账会按设计写它，见 docstring 边界）")


def check_session_start_no_phantom_setup_state():
    """SessionStart 的 `agents_md` 记步：**没有记录时不许凭空造，有记录时必须补记**。

    **三棵树写在一道闸里**，因为它们是同一个判据的三个面——拆成三处必然只改一边，而
    「只改一边」正是这几条各自的历史成因：

      - **缺席树**（`git clone` 下来的项目）：运行态状态目录整个被 gitignore，所以 clone
        侧必然**没有** `setup-state.json`。记步若无条件落笔，就给它凭空造出一份只含
        `agents_md` 的记录，`check_setup_state` 随即读成「初始化未走完，已完成 1/3 步」。
        那是加入项目的同事在**首个会话**里被念出来的第一句话，且此后每个会话原样重复、
        永不自愈——而文件不存在时 doctor 本来报 info 放行。
      - **在场树**（装好了、只是这一步没记的项目）：文件在、`agents_md` 那一步没记。记步若挪回「刚创建
        AGENTS.md」那一支，升级到本版的存量项目就落进「文件有了、步没记」，同样恒 error。
      - **不可读树**（文件在、但读不出一份可用记录）：**判据是「一个字节都没变」**。
        守卫若退成 `.exists()`，这一类会被当成「有记录」而走进重建分支，于是
        **一份带 BOM 的、内容完好的八步装机记录会被一次会话启动抹成一步**。
        这一格必须单列的理由是它的失效**无声**：自愈谓词会把重建出来的那一步认成
        「没有 launcher 记录」而报 `info` ⇒ **装机记录被销毁，而体检报一切正常**。
        三个 fixture 分别瞄准 `mark_setup_step` 里判「可不可用」的一行（**第 2 格是例外，
        见下方能力边界**）：带 BOM ⇒ `json.loads` 抛（`except` 那支）；顶层是数组 ⇒ 顶层
        `isinstance` 那支；`steps` 为空 ⇒ 「steps 非空」那支（也就是用户 2026-09-20
        确认保留的那条加严）。

    **为什么要有这道闸**：三个方向此前都只由 docstring 守着，全量闸对它们完全无感——
    实测两个**单行**回改（判据退成 `.exists()`；去掉「steps 非空」那半）都能过全量 214。
    也就是说，改回去不会有任何一处说话。

    探针形态与 `check_doctor_readonly_on_probe_project` 同族：临时目录铺最小合规项目、
    `subprocess` 跑真脚本、读真实落盘结果。**两个前置条件各有一条断言兜底**（AGENTS.md
    在位、脚本真的跑到了写入路径）——缺了它们，「文件没被造出来」既可能是守卫在起作用，
    也可能是探针根本没跑起来，两者在终端里同形。

    **能力边界（本闸不覆盖什么）**：
      - 只验 `agents_md` 这一个记步调用点，不验 scaffold / launcher 走的默认路径
        （那条路径按设计**就该**重建损坏文件，与本闸的判据相反）。
      - 只验**一次** SessionStart；「连跑 N 次会不会漂」不在内。
      - 不可读树只断言**字节未变**，不断言 doctor 对这几种文件报什么级别——
        那归 `check_setup_state` 自己，本闸不重复一遍它的口径。
      - 探针经 `CLAUDE_PROJECT_DIR` 把项目根钉死；该变量被绕开时（脚本改成只认载荷 `cwd`）
        本闸的红灯文案会指向「没跑到写入路径」，而真实成因是**跑到了别的项目上**。
      - **不可读树第 2 格（顶层是数组）不可独立反证**，上面那句「分别瞄准一行」对它不成立：
        单独削弱顶层 `isinstance` 那一行时本闸**仍绿**——`existing.get` 会在 `try` 里对 list
        抛、被 `except` 接住，`usable` 仍为 False。也就是说该格 **fail-safe by construction，
        不可能单独失效**；它在判据整体退化时（如退成 `.exists()`）才参与报红，是串联兜底的
        一环，不是可独立反证的断言。**别据此省掉对它的复核**——不可独立反证 ≠ 无效断言，
        而是「任何能让它红的回改，同时会让排在它前面的 BOM 格先红」。
    """
    import json as _json
    import os
    import subprocess
    import tempfile
    name = "session_start_no_phantom_setup_state"
    prep = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "session-start-prep.py"
    if not prep.exists():
        return _fail(name, "session-start-prep.py 不存在")
    state_rel = _rt("state")
    try:
        schema = _scaffold().SETUP_STATE_SCHEMA
    except Exception as e:
        return _fail(name, f"取不到 SETUP_STATE_SCHEMA（{type(e).__name__}: {e}）")
    seeded = {"scaffold": "ok", "subscribe": "ok"}

    def blob(steps, bom=False):
        """一份 setup-state.json 的字节。`bom=True` 时前置 U+FEFF（写入方按 utf-8 读 ⇒ 解析失败）。"""
        text = _json.dumps({"__schema__": schema, "steps": steps}, ensure_ascii=False) + "\n"
        return (("﻿" + text) if bom else text).encode("utf-8")

    # 不可读树的三个 fixture，各钉「可不可用」判定里的**一行**；值 = (期望原样保住的字节, 踩到时会怎样)
    # **故意不含 `agents_md`**：含了的话「同值早退」会替守卫挡住写入，于是解码口径一旦放宽
    # （`utf-8` → `utf-8-sig`）这一格照样绿——那条断言就成了摆设。不含它时，任何一种
    # 「把这份文件读成可用记录」的回改都会当场写进来、被本格抓住。
    healthy7 = {k: "ok" for k in ("scaffold", "subscribe", "sync_rules", "git_init",
                                  "module_tree", "placement", "acceptance")}
    unreadable = {
        "带 BOM 的健康多步记录": (
            blob(healthy7, bom=True),
            "这一格钉的是「写入方按什么口径判这份文件可不可用」，两种回改都会踩到它："
            "①判据退成「文件在」⇒ 这份内容完好的记录被一次会话启动抹成只剩 agents_md 一步，"
            "而重建出来的那一步随后被自愈谓词判成 info ⇒ **装机记录静默销毁、体检报一切正常**；"
            "②写入侧解码放宽成 `utf-8-sig` ⇒ 数据不丢，但与 doctor 读侧（仍按 `utf-8` 读、"
            "报解析失败）当场分叉。所以本格的判据是**字节相等**，不是「数据有没有丢」"),
        "顶层不是对象": (
            b"[1, 2, 3]\n",
            "钉的是顶层 `isinstance(existing, dict)` 那一支——放过它，下一句 `.get(\"steps\")` "
            "会在 doctor 侧抛 AttributeError，而写入侧会把文件整份重建掉"),
        "steps 为空": (
            blob({}),
            "钉的是「`steps` 非空」那一条加严。去掉它，`{\"steps\": {}}` 会被补记成 "
            "`{agents_md}` ⇒ doctor 对它从 error 变 info。而空 steps 恰恰是"
            "「装机断在最开头」最该被看见的形态：空集上「没有失败的步骤」恒真，"
            "不构成任何「装完了」的证据，放它过去等于拿空真换绿灯"),
    }

    def build(root, content):
        """最小合规项目；`content=None` ⇒ 不铺 setup-state.json（缺席树）。"""
        root.mkdir(parents=True)
        try:
            _scaffold().ensure_agents_md(root)
        except Exception as e:
            return f"最小项目铺不出 AGENTS.md（渲染失败 {type(e).__name__}: {e}）"
        if not (root / "AGENTS.md").is_file():
            return ("最小项目没有 AGENTS.md——记步的前置条件不成立，"
                    "此时三棵树的结论都是空真，不是守卫在起作用")
        (root / "projects").mkdir()
        (root / "projects" / "board.yaml").write_text(
            "summary:\n  total: 0\ntasks: []\n", encoding="utf-8", newline="")
        if content is not None:
            f = root / state_rel / "setup-state.json"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(content)   # 字节直写：BOM 那一格的意义全在字节上
        return None

    def run(root):
        payload = _json.dumps({"session_id": "wf-validate-probe", "cwd": str(root),
                               "hook_event_name": "SessionStart"}).encode("utf-8")
        # CLAUDE_PROJECT_DIR 必须显式覆盖：validate 常在一个真实 workframe 会话里跑，
        # 继承下来的那个值会让探针把状态写进**真实项目**
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                   PYTHONDONTWRITEBYTECODE="1", CLAUDE_PROJECT_DIR=str(root))
        try:
            subprocess.run([sys.executable, str(prep)], input=payload,
                           capture_output=True, timeout=180, env=env, cwd=str(root))
        except Exception as e:
            return f"session-start-prep.py 无法执行: {type(e).__name__}: {e}"
        # 跑没跑到写入路径，不看 rc（脚本吞异常、恒 0），看它自己的落盘痕迹
        if not (root / state_rel / "plugin-root.txt").is_file():
            return ("session-start-prep.py 没写出 plugin-root.txt——它没跑到写入路径，"
                    "此时「文件没被造出来」证明不了守卫在起作用")
        return None

    with tempfile.TemporaryDirectory() as td:
        absent, present = Path(td) / "absent", Path(td) / "present"
        trees = [(absent, None), (present, blob(seeded))]
        trees += [(Path(td) / f"unreadable{i}", c)
                  for i, (c, _why) in enumerate(unreadable.values())]
        for root, content in trees:
            err = build(root, content)
            if err:
                return _fail(name, err)
            err = run(root)
            if err:
                return _fail(name, err)

        # 不可读树：一个字节都不许动。**排在两棵结构树之前**——它是本批的头号修复，
        # 且它的失效是无声的（重建出来的一步会被自愈谓词判成 info），先报它。
        for i, (label, (want, why)) in enumerate(unreadable.items()):
            f = Path(td) / f"unreadable{i}" / state_rel / "setup-state.json"
            if not f.exists():
                return _fail(name, f"SessionStart 把「{label}」那份 setup-state.json 删掉了"
                                   f"（读不出内容时守卫应当原样不动，不是清理）。{why}")
            if f.read_bytes() != want:
                return _fail(name, f"SessionStart 改写了「{label}」那份读不出内容的 "
                                   f"setup-state.json（{len(want)} B → {len(f.read_bytes())} B）"
                                   f"——守卫的判据必须是「解析得出一份可用记录」而不是「文件在」。{why}")

        # 缺席树
        f = absent / state_rel / "setup-state.json"
        if f.exists():
            try:
                got = (_json.loads(f.read_text(encoding="utf-8")) or {}).get("steps")
            except Exception:
                got = "<不可解析>"
            return _fail(name, f"SessionStart 在没有 setup-state.json 的项目里凭空造出了一份"
                               f"（steps={got}）——clone 下来的项目会因此恒报「初始化未走完」")
        # 在场树
        f = present / state_rel / "setup-state.json"
        if not f.exists():
            return _fail(name, "SessionStart 之后已存在的 setup-state.json 不见了"
                               "（守卫不该动文件，只该决定写不写）")
        try:
            got = _json.loads(f.read_text(encoding="utf-8")).get("steps") or {}
        except Exception as e:
            return _fail(name, f"SessionStart 之后 setup-state.json 读不出来了: "
                               f"{type(e).__name__}: {e}")
        if "agents_md" not in got:
            return _fail(name, f"AGENTS.md 在位却没给已有的 setup-state.json 补记 agents_md 步"
                               f"（steps={sorted(got)}）——装好了只是这一步没记的项目"
                               f"会因此恒报「初始化未走完」")
        lost = sorted(k for k in seeded if k not in got)
        if lost:
            return _fail(name, f"补记 agents_md 时把已有的步骤弄丢了（缺 {'、'.join(lost)}，"
                               f"实得 steps={sorted(got)}）——记步必须增量，不是整份重建")
    return _ok(name, f"缺席树不凭空造、在场树补记 agents_md 且不丢既有步骤、"
                     f"{len(unreadable)} 种读不出内容的记录逐字节未动")


def _doctor_declared_check_ids(group=None):
    r"""从 doctor 源码解析检查 ID —— **doctor 项数与名录的唯一事实源**。

    `group=None` 返回 INSTALL_CHECKS + CHECKS 合并后的全部 id；
    `group="INSTALL_CHECKS"` / `"CHECKS"` 只返回该组（CHECKS = runtime 组）。
    返回 `(ids, problems)`；`problems` 非空时调用方须报红，**不得当成「零个检查」
    静默通过**——提取器失明与「真的一个都没有」在结果上长得一样。

    为什么提成共用件：这个数字此前在仓里被**九处**手写（两道闸的成功文案、一处闸的
    docstring 两句、eval 用例集五处断言）。给 doctor 加一项 `yaml_parse` 之后，
    那九处一夜之间全变成假话——其中最坏的一处是**闸报绿时输出的数字**。
    `closeout-discipline` §同一事实的多个表现层：找出唯一实现，其余改成引用。

    **`group=` 这个参数就是为了让「唯一」名副其实**：`check_launcher_setup_contract`
    要按**分组**数（拿 install 组项数与 launcher SKILL 的「N 项覆盖」对账），此前它
    自己另写了一份解析——正则逐字相同、**计数规则却不同**（那边按「行首 `("` 且行内
    含 `check_`」数，这边按 `^\s*\("([a-z_]+)"` 抓）。两份当前同为 11，属于「还没漂
    但迟早漂」，现已合并到本函数。
    """
    script = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    if not script.exists():
        return [], ["workframe_doctor.py missing"]
    dtext = script.read_text(encoding="utf-8")
    wanted = (group,) if group else ("INSTALL_CHECKS", "CHECKS")
    ids, problems = [], []
    for const in wanted:
        mm = re.search(rf"^{const}\s*=\s*\[(.*?)^\]", dtext, re.M | re.S)
        if not mm:
            problems.append(f"找不到 {const} 定义（doctor 结构变了，闸需同步）")
            continue
        ids += re.findall(r'^\s*\("([a-z_]+)"', mm.group(1), re.M)
    if not ids and not problems:
        problems.append(f"{'/'.join(wanted)} 的检查 ID 一个都没解析出来"
                        f"（提取器失明时本闸会静默变绿）")
    return ids, problems


def check_doctor_all_checks_run_clean():
    r"""doctor 的每个检查都必须能在**最小合规项目**上跑完，不抛自身异常。

    为什么需要单独一闸：doctor 的每个检查各自包在 try/except 里（单个检查炸了不该
    带崩整份体检）。代价是**检查彻底坏掉时也只显示一行 `✗ 检查自身异常`**——退出码
    照常、其余检查照常绿，静态闸更是一点感觉都没有。2026-08-16 实证：改
    `check_rules_mirror` 时误删了一行 `extra = ...` 赋值，NameError 让该检查全程失效，
    而此时 validate 全绿。体检工具自己坏了却没人报警，是最坏的一种坏法。

    做法：起一个最小合规项目（`AGENTS.md` 四段齐全 + plugin-root 指向真插件），实跑两个 group，
    断言输出里不含 `检查自身异常`。这条闸对**每一个**检查同时生效——将来任何一个检查
    写出 NameError / TypeError / 拼错字段名，都会在这里当场暴露。

    **另一半断言：真跑起来的项数 == 源码声明的项数。** 「不抛异常」在**一个检查都没跑**
    时同样成立——扫 0 报绿是最坏的一种绿。所以逐组数实际输出的检查行，与
    `_doctor_declared_check_ids()` 现算的声明数对账，缺项即红。

    **这条对账的能力边界**：`ran` 靠「行首（可选状态符）+ `[id]`」认检查行。实测当前
    命中集与检查头行集**完全相等**（多收 0、漏收 0），且 doctor 源码里含换行的 finding
    文案 **0 条**，所以 finding 正文产生不了第二个行首。**但它认的是行首形态、不是
    「这是一行检查头」**——将来若有人写多行 finding 且第二行以 `[某 id]` 开头，那一行
    会被算进 `ran`，本对账就可能被顶替（方向是漏报缺项，不是假红）。真出现那种文案时，
    把提取器锚到状态符上（`^[✓i!✗] \[`）即可。
    """
    import os
    import subprocess
    import tempfile
    doctor = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    if not doctor.exists():
        return _fail("doctor_all_checks_run_clean", "workframe_doctor.py 不存在")
    plugin_root = FRAMEWORK_ROOT / "plugins" / "core"
    agents_tpl = plugin_root / "templates" / "agents-md-template.md"
    if not agents_tpl.exists():
        return _fail("doctor_all_checks_run_clean",
                     "agents-md-template.md 不存在——最小合规项目铺不出 AGENTS.md，"
                     "探针没建起来时的「无异常」是空转")

    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        state = proj / ".claude" / "workframe-state"
        state.mkdir(parents=True)
        (state / "plugin-root.txt").write_text(str(plugin_root), encoding="utf-8", newline="")
        # 走 scaffold 的唯一写入点渲染（模板带占位符，原样复制会被残留扫描报 warn）
        try:
            _scaffold().ensure_agents_md(proj)
        except Exception as e:
            return _fail("doctor_all_checks_run_clean",
                         f"最小合规项目铺不出 AGENTS.md（渲染失败 {type(e).__name__}: {e}）")
        (proj / "projects").mkdir()
        (proj / "projects" / "board.yaml").write_text(
            "summary:\n  total: 0\n  pending: 0\n  in_progress: 0\n  pending_qa: 0\n"
            "  completed: 0\n  blocked: 0\n  cancelled: 0\n  last_updated: null\n\ntasks: []\n",
            encoding="utf-8", newline="")
        (state / "events.jsonl").write_text("", encoding="utf-8", newline="")

        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        bad = []
        ran = set()
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
            ran.update(re.findall(r"^[^\S\n]*\S?\s*\[([a-z_]+)\]", blob, re.M))
        if bad:
            return _fail("doctor_all_checks_run_clean",
                         "doctor 检查在最小合规项目上自身报错：\n    " + "\n    ".join(bad[:6]))
    # 「一个都没跑」也满足「没抛异常」——项数对账把这条堵上
    declared, problems = _doctor_declared_check_ids()
    if problems:
        return _fail("doctor_all_checks_run_clean", "; ".join(problems))
    missing = [cid for cid in declared if cid not in ran]
    if missing:
        return _fail("doctor_all_checks_run_clean",
                     f"源码声明 {len(declared)} 项，实跑输出里缺 {len(missing)} 项："
                     f"{'、'.join(missing[:6])}——「没抛异常」在一个检查都没跑时同样成立，"
                     f"这一条正是为了不让扫 0 报绿")
    return _ok("doctor_all_checks_run_clean",
               f"doctor {len(declared)} 项检查在最小合规项目上均无自身异常（实跑输出逐项对上）")


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
    2. 通用协议片仍写"唯一 producer"（与 fallback 路径矛盾）
    3. 通用协议片缺失 fallback dedup 检查规则
    """
    skill_path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "test-case-design" / "SKILL.md"
    proto_path = CONTEXT_DIR / "both" / "20-protocol-common.md"

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
            "通用协议片 still uses '唯一 producer' wording — conflicts with fallback path; rewrite as 主 producer + fallback",
        )

    # 必须明确"主 producer = test-case-design"
    has_primary = ("主 producer" in proto_text and "test-case-design" in proto_text)
    if not has_primary:
        return _fail(
            "task_blocked_producer_policy_consistent",
            "通用协议片 must declare 主 producer = test-case-design",
        )

    # 必须明确 fallback dedup 检查
    has_fallback_dedup = ("fallback" in proto_text.lower()) and any(
        kw in proto_text for kw in ["dedup", "去重", "扫描", "尚无"]
    )
    if not has_fallback_dedup:
        return _fail(
            "task_blocked_producer_policy_consistent",
            "通用协议片 must define fallback producer + mandatory dedup check on events.jsonl",
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
        # 两处手写清单必漂，让机器去数。解析式与 `check_doctor_all_checks_run_clean`
        # **共用 `_doctor_declared_check_ids()`**，不在本函数再写一份。
        declared, problems = _doctor_declared_check_ids()
        issues += problems
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
    # 项数**现算**，不写死：ID 清单已经从源码解析了，成功文案再手写一个数字就是
    # 同一事实的第二处手写，加一项检查它就变成假话（实测：加 yaml_parse 之后
    # 这句仍写着 19）。
    return _ok("doctor_smoke", f"({len(declared)} checks in 2 groups)")


def check_validate_self_no_duplicates():
    """validate.py 自身的四向断言——**这条闸守的是闸本身**：重名定义 / 重复注册 /
    注册未定义 / 定义未注册（孤儿——「永不执行的检查与通过长得一模一样」，第四向
    2026-08-20 外审抓出补齐）。

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
        # 反向：定义了但没进 CHECKS——「写完了但永不执行」的检查与通过长得一模一样，
        # 且此前三向断言（重名/重复注册/注册未定义）恰恰漏了这一向（2026-08-20 外审抓出）
        orphans = sorted(set(defs) - set(regs))
        if orphans:
            errors.append(f"定义了但未注册进 CHECKS（永不执行）: {orphans}")
    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name, f"{len(defs)} 个 check 函数无重名、无重复注册、无未注册孤儿")


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
    """「同事实多文件」的字面扫描——按字面追，不按发现编号追。

    这类口径曾各自漂了 3-4 轮：每轮都在勾选「某编号已修」，实际只改了其中一两处。
    「同一事实散在多文件」的口径，只能用字面扫描当闸——按发现编号勾选必漏。

    - 「首尾空格」——module_init 入口拒绝**任何**空格（含中间），规范却说首尾。
      唯一豁免：解释「首尾/中间各自为什么不行」的教义行（含「中间空格」字样）。
    """
    name = "no_drifted_literals"
    scan_dirs = [FRAMEWORK_ROOT / "plugins", FRAMEWORK_ROOT / "docs"]
    rules = [
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


# ---- 单源三条：运行态路径单源 / 环境变量中立 / 事件追加单源 ----

# 扫描面**由 glob 现算**，不列文件名：新加的脚本自动进扫描面，这是这三条与旧的
# 名单式闸的根本区别（名单漏一个 = 那个文件永远合规，且闸报绿）。
def _python_sources(base):
    """`base` 下的全部 Python 源：`*.py` **加上**首行 shebang 指向 python 的无后缀入口。

    **为什么不能只按后缀**：`plugins/core/bin/` 下的入口脚本一律没有扩展名（靠 shebang
    执行），按后缀搜索对它们天然失明。这个盲区本仓 2026-08-16 已经实证过一次并写在本文件里
    （`check_audit_board_drift_bin_exists_and_compiles` 的 docstring：`count_task_statuses()`
    从 3 元组改 4 元组时唯独那个 bin 入口漏改，因为 `grep --include="*.py"` 把它过滤掉了）。
    判据因此改成**读第一行**而不是看后缀：`#!` 开头且含 `python`。全仓待判文件 <200 份，
    每份只读 200 字节，代价可忽略。

    **能力边界**（两个方向都写，今天都是零命中）：

      - **漏收**：只认首行 shebang。没有 shebang、又不叫 `.py` 的 Python（例如被别的脚本
        以 `runpy` 加载的数据文件）看不见。另有一类**命中逃生口后静默掉出扫描面**——
        判据是「`#!` 开头且含 `python`」，于是 `#!/usr/bin/env pypy3`、大写 `PYTHON`
        这类既进不来、又会被下限当成「明说不是 python」放过，两头都不报。
      - **误收**：`plugins/**` 下**任何**首行是 python shebang 的文件都会进来，哪怕它
        不是 Python（如一份以 shebang 开头的示例文本）——那时四道闸会把它判成「解析失败」，
        是假红。假红比漏收响亮，但也会被「修」成永远绿，所以一并写明。

    下限 `_shipped_py_scan_face_problems()` 钉住 `plugins/*/bin/` 这一片不会悄悄缩水，
    但它护不住上面第一条的逃生口那一格——两者判据同源（都看 shebang 含不含 `python`）。
    """
    base = Path(base)
    if not base.is_dir():
        return []
    out = []
    for p in base.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts or "node_modules" in p.parts:
            continue
        if p.suffix == ".py":
            out.append(p)
            continue
        try:
            with p.open("rb") as fh:
                first = fh.readline(200)
        except OSError:
            continue
        if first.startswith(b"#!") and b"python" in first:
            out.append(p)
    return sorted(out)


def _shipped_py():
    """随插件分发的全部 Python（`tools/` 是构建期工具，不随插件走，另论）。

    **含 `plugins/core/bin/` 下无扩展名的 shebang 入口**——它们随插件分发、且
    `plugins/core/bin/**` 就写在模块清单的 `code_paths` 里。枚举口径见 `_python_sources`。
    """
    return _python_sources(FRAMEWORK_ROOT / "plugins")


def _shipped_py_scan_face_problems():
    """扫描面的下限：`bin/` 里每个非 `.cmd` 文件，要么进扫描面，要么首行 shebang
    明说它不是 python（`workframe-python` 是 bash）。两者皆非 = 扫描面在悄悄缩水。

    没有这一条时，`_python_sources` 的 shebang 判据一旦被改严一个字节，三条闸会在一个更小的
    集合上继续报「零命中」——正是它们要防的那种假绿，且没有任何一处会报错。

    **事实源是目录本身**，与 `check_bin_git_index_modes` 那条实证同一条教训（它此前遍历
    写死的 6 元素名单，于是 Windows 上新增的第 7 个 bin 脚本没有任何检查看得见）。
    那条用的是 git index，因为它要的是**mode 位**（工作区 mode 在 `core.fileMode=false`
    下不可信）；本函数只要**文件名**，所以直接读目录——三条闸就不必依赖仓里有 `.git`，
    在整仓副本沙盒（探针一律不复制 `.git`）与源码分发包里同样成立。
    这不是循环论证：两边各自独立地枚举一次，`_python_sources` 用 shebang 判据筛，
    本函数不筛；判据坏掉时两个集合就对不上。

    **`bin/` 目录现算而不写死**：此前只看 `plugins/core/bin/`，将来出现
    `plugins/workframe-launcher/bin/` 时取材层（`_python_sources` 扫 `plugins/**`）
    扫得到、下限却护不住——那正好是本函数要防的「扫描面悄悄缩水」换个位置重现。
    """
    plugins_root = FRAMEWORK_ROOT / "plugins"
    bin_dirs = sorted(p for p in plugins_root.glob("*/bin") if p.is_dir()) \
        if plugins_root.is_dir() else []
    if not bin_dirs:
        return ["plugins/*/bin/ 一个都不存在——入口脚本整体缺失，扫描面无从谈起"]
    entries = [p for d in bin_dirs for p in sorted(d.iterdir())
               if p.is_file() and p.suffix != ".cmd" and p.name != "__pycache__"]
    if not entries:
        return ["plugins/*/bin/ 下没有任何非 .cmd 入口——扫描面下限失去参照"]
    covered = {p.resolve() for p in _shipped_py()}
    problems = []
    for p in entries:
        if p.resolve() in covered:
            continue
        first = b""
        try:
            with p.open("rb") as fh:
                first = fh.readline(200)
        except OSError:
            pass
        if first.startswith(b"#!") and b"python" not in first:
            continue                      # 明说不是 python
        problems.append(_rel(p))          # 多个 bin/ 时裸文件名会撞，点名到相对路径
    return problems


def _scan_face_fail(name):
    """三条闸共用的扫描面自检；有问题时返回 `_fail(...)`，否则返回 None。"""
    problems = _shipped_py_scan_face_problems()
    if not problems:
        return None
    return _fail(name, f"扫描面漏掉 {len(problems)} 个 bin/ 入口（" + "、".join(problems[:6])
                       + "）——它们随插件分发却不在本闸视野内，本闸的「零命中」在这些文件上"
                         "不成立。按后缀搜索是 bin/ 的天然盲区（本仓 2026-08-16 已实证一次）")


def _shipped_js():
    return sorted(p for p in (FRAMEWORK_ROOT / "plugins").rglob("*.js")
                  if "node_modules" not in p.parts)


def _rel(p):
    return p.relative_to(FRAMEWORK_ROOT).as_posix()


# 运行态目录的三个末段 + 中立父目录。取自 `_state_io`，本文件不抄字面。
def _runtime_segments():
    segs = set()
    joined = set()
    for kind in ("state", "memory", "archive"):
        for rel in (_rt(kind), _rt(kind, legacy=True)):
            head, _, tail = rel.partition("/")
            segs.add(tail)
            joined.add(rel)
        joined.add(_rt(kind))
    segs.add(_rt("state").split("/")[0])      # 中立父目录 `.workframe`
    return segs, joined


def _docstring_nodes(tree):
    """全部 docstring 的 Constant 节点（模块 / 类 / 函数的首条 Expr）。

    docstring 是**说明性文字**，不是路径源。把它判红是假红，而假红最后会被「修」成
    永远绿——比漏更坏。散文里出现旧目录名该由文档纪律管，不该由路径闸管。
    """
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(n, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _path_building_strings(tree):
    """AST 里**参与路径构造**的字符串字面量（值, 行号）。

    三种形态：`a / "x" / "y"` 的 `/` 链、`Path("x", "y")` / `os.path.join(...)` 的实参、
    `.joinpath("x", "y")`。**不含**普通字符串——文案里出现目录名不是「第二个路径源」，
    而 `("agent-memory/",)` 这类文档契约令牌也不是；把它们一起判红就是造假红，
    而假红最后会被「修」成永远绿。跨越 `def` 边界不成立的问题在这里天然不存在：
    AST 覆盖全部作用域，**函数体内的局部拼接与模块级常量一视同仁**。
    """
    out = []

    def _consts(node):
        for n in ast.walk(node):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                out.append((n.value, n.lineno))

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            _consts(node)
        elif isinstance(node, ast.Call):
            f = node.func
            fname = getattr(f, "attr", None) or getattr(f, "id", None)
            if fname in ("Path", "joinpath", "join"):
                for a in node.args:
                    _consts(a)
    return out


def check_paths_single_source():
    """运行态目录（state / agent-memory / memory-archive）的位置只能有一个实现。

    被它挡住的是两类漏改，**后果不同、文案必须分开**：
      - **字面旧路径**：框架只读写新位置，写死旧路径的那一处单独去读写旧目录——同一个项目的状态劈成两半；
      - **函数体内的局部拼接**：模块级常量扫描抓不到它，漏改后新装项目仍在旧目录建骨架，
        doctor 报「待迁移」，`--migrate` 在一个刚装好的项目上又要跑一次迁移。

    判据只钉**路径构造**（`/` 链 / `Path(...)` / `joinpath` / `os.path.join` 的字符串实参）
    与**已拼好的整串**，不钉普通文案——文案里出现目录名不构成第二个路径源，把它判红
    是假红，而假红最终会被「修」成永远绿。

    **能力边界**：① 扫描面 = 随插件分发的 `plugins/**`（`.py`——含 `bin/` 下无扩展名的
    shebang 入口，见 `_python_sources`——与 `.js`）；`tools/**` 是构建期工具、不随插件走，
    它引用两种形态是本职。② 从别处**接收**路径参数、自己不出现目录名的脚本，本闸看不见
    ——那类由「调用方在扫描面内」间接兜住。③ JS 侧是**逐行**匹配，跨行拼出来的目录名
    （`join(\n ".claude",\n "workframe" + "-state")`）够不着；py 侧走 AST，无此边界。

    **与 `runtime_dir_legacy_literals` 分工**：本闸管 `plugins/**` 下 py / js 的代码字符串、新旧两种
    形态都管；那道闸管发布面全部文本（含 docstring 与注释）里的旧形态。py 代码字符串里的旧字面
    两道都红，是有意的重叠。
    """
    name = "paths_single_source"
    bad = _scan_face_fail(name)
    if bad:
        return bad
    segs, joined = _runtime_segments()
    py_owner = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "_state_io.py"
    js_owner = (FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "screenshot"
                / "scripts" / "screenshot.js")
    if not py_owner.exists():
        return _fail(name, "_state_io.py 缺失——运行态目录的唯一实现没了")

    literal_hits, concat_hits = [], []
    for p in _shipped_py():
        if p == py_owner:
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            return _fail(name, f"{_rel(p)} 解析失败: {e}")
        for val, lineno in _path_building_strings(tree):
            if val in segs or any(j in val for j in joined):
                concat_hits.append(f"{_rel(p)}:{lineno} `{val}`")
        docstrings = _docstring_nodes(tree)
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                    and id(n) not in docstrings:
                if any(j in n.value for j in joined):
                    literal_hits.append(f"{_rel(p)}:{n.lineno} `{n.value[:60]}`")
    # JS 侧同一事实的第二个表现层——Node 进程 import 不了 Python，无法合并成一份实现，
    # 所以按 `closeout-discipline` §同一事实的多个表现层：两处同批改 + 闸把两处一起圈住。
    for p in _shipped_js():
        if p == js_owner:
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        for i, ln in enumerate(src.splitlines(), 1):
            if any(j in ln for j in joined) or any(f"'{s}'" in ln or f'"{s}"' in ln
                                                   for s in segs):
                literal_hits.append(f"{_rel(p)}:{i}")

    if literal_hits:
        return _fail(name, f"{len(literal_hits)} 处**字面旧路径**（路径出现第二个源，"
                           f"那一处单独读写旧目录，同一项目的状态劈成两半）: "
                           + "、".join(literal_hits[:6]))
    if concat_hits:
        return _fail(name, f"{len(concat_hits)} 处**函数内/局部路径拼接**（模块级常量扫描"
                           f"抓不到它——漏改后新装项目仍在旧目录建骨架，doctor 报「待迁移」，"
                           f"`--migrate` 在刚装好的项目上又要跑一次迁移）: "
                           + "、".join(concat_hits[:6]))

    # 下限一：零命中也可能是「机制被整个删了」。**在四种目录形态上真跑一遍入口函数**，不看字面——
    # `_state_io` 是按目录段拼的，任何「找字符串」式的下限对它恒假（写过一版，当场假红）。
    # 钉两件事：①运行期入口（`*_dir_of`，调用方实际经过的那一层）在任何形态下都指新位置——旧目录在场
    # 也不回退（回退就是读旧写新的另一种形态：doctor 与迁移工具按「框架只读新位置」给的出路全部失真）；
    # ②`legacy_present` 如实报旧目录在不在——doctor `legacy_state_dir` 与迁移工具靠它看旧目录，它恒假时
    # 没迁移的项目在体检里永远是绿的。
    m = _state_io_module()
    dir_of = {"state": m.state_dir_of, "memory": m.memory_dir_of, "archive": m.archive_dir_of}
    with tempfile.TemporaryDirectory() as td:
        for kind in ("state", "memory", "archive"):
            new_r, old_r = m.new_rel(kind), m.legacy_rel(kind)
            if new_r == old_r:
                return _fail(name, f"`{kind}` 的新旧形态相同——旧布局与新布局分不开，"
                                   f"没迁移的项目在体检与迁移工具里都认不出来")
            base = Path(td) / kind
            for label, pres, legacy in (("fresh", (), False), ("legacy-only", (old_r,), True),
                                        ("migrated", (new_r,), False), ("both", (old_r, new_r), True)):
                proj = base / label
                proj.mkdir(parents=True, exist_ok=True)
                for pre in pres:
                    (proj / pre).mkdir(parents=True, exist_ok=True)
                got = dir_of[kind](proj).relative_to(proj).as_posix()
                if got != new_r:
                    return _fail(name, f"`{kind}/{label}` 的运行期目录判成 `{got}`（应恒为 `{new_r}`）——"
                                       + ("旧目录在场时回退去读写它：doctor 报「没迁移」、迁移工具要搬的正是它，"
                                          "两边给的出路都会失真" if old_r in pres else
                                          "新装项目会建在一个不该用的目录下"))
                if bool(m.legacy_present(proj, kind)) != legacy:
                    return _fail(name, f"`legacy_present({kind})` 在 `{label}` 形态下答 "
                                       f"{not legacy}——doctor `legacy_state_dir` 与迁移工具靠它看旧目录，"
                                       + ("没迁移的项目在体检里会是绿的" if legacy else "新装项目会被报「没迁移」"))
    js_src = js_owner.read_text(encoding="utf-8") if js_owner.exists() else ""
    missing_seg = [s for s in _rt("state").split("/") if f"'{s}'" not in js_src and f'"{s}"' not in js_src]
    if missing_seg:
        return _fail(name, f"screenshot.js 的状态目录解析缺 `{_rt('state')}` 的段 {missing_seg}——"
                           f"截图日志会写进一个该项目根本不用的目录")
    if _rt("state", legacy=True).split("/")[-1] in js_src:
        return _fail(name, f"screenshot.js 里还有旧状态目录 `{_rt('state', legacy=True)}` 的末段——JS 侧仍在回退到"
                           f"旧目录，与 `_state_io`（只读新位置）分叉：没迁移的项目截图日志写进旧目录、没有读者")
    callers = 0
    for p in _shipped_py():
        if p == py_owner:
            continue
        s = p.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\b(state_dir_of|memory_dir_of|runtime_rel)\s*\(", s):
            callers += 1
    if callers < 10:
        return _fail(name, f"只有 {callers} 个脚本在调 state_dir_of / memory_dir_of / "
                           f"runtime_rel（应 ≥10）——多数消费方已不走单源，闸的零命中是假的")
    return _ok(name, f"运行态路径单源：{len(_shipped_py())} 份 py + {len(_shipped_js())} 份 js "
                     f"零字面、零局部拼接，{callers} 个消费方走单源")


# ---- 发布面里运行态目录的旧字面 ----

# 豁免表：(仓相对路径〔全等匹配〕, 行内特征串〔None = 文件级〕, 类, 理由)。
# 类：M 迁移对照句 / E eval 断言依赖布局 / O 单源所有者。**过期判定**见 `_legacy_literal_scan`。
_LEGACY_LITERAL_EXEMPT = (
    ("docs/setup-guide.md", "上一版装的项目运行态还在", "M",
     "迁移对照句：点名旧布局在哪、框架不再读它，旧字面是这句要说的内容本身"),
    ("docs/setup-guide.md", "| `.workframe/state/` |", "M",
     "迁移工具一节的新旧位置对照表：旧位置是这一行要说的内容本身（迁移工具那一节随之新增，本条与上一条同批登记）"),
    ("docs/setup-guide.md", "| `.workframe/agent-memory/` |", "M",
     "同上：对照表记忆目录行"),
    ("docs/setup-guide.md", "| `.workframe/memory-archive/` |", "M",
     "同上：对照表记忆归档行"),
    ("plugins/core/scripts/_state_io.py", None, "O",
     "运行态目录的唯一实现，新旧两种形态都必须写在这里"),
)

_LEGACY_FACE_PREFIXES = ("plugins/", "docs/", ".claude-plugin/")
_LEGACY_FACE_FILES = ("README.md",)
_LEGACY_CHANGELOG = "CHANGELOG.md"
_LEGACY_MSG_RECIPE = "模型照这条配方执行：新布局项目读不到文件"
_LEGACY_MSG_PROSE = "模型照这句读写：框架只读写新位置，照旧路径写下的内容没有读者（新布局项目里那是不存在的目录）"


def _legacy_literal_forms():
    """运行态目录旧形态的字面集，**由 `_runtime_segments()` 现算**，本文件不抄目录名。

    返回 `(literals, problem)`：`literals` 按长度降序（一行同时含带前缀与裸段时报更具体的那个）；
    `problem` 非 None 表示字面集现算不出完整形状——那时闸对缺掉的形态恒绿，必须报红而不是照扫。
    形状：每个 kind 的旧形态 `<父>/<末段>` 各出 `/`、`\\`、`\\\\` 三种分隔；另加**只属于旧形态**
    的末段裸写（新旧同名的末段——如 `agent-memory`——两种布局同义，不算旧字面）。
    """
    kinds = ("state", "memory", "archive")
    _segs, joined = _runtime_segments()
    new = {_rt(k) for k in kinds}
    legacy = sorted(joined - new)
    if len(legacy) != len(kinds) or any("/" not in r for r in legacy):
        return [], (f"旧形态现算得 {legacy}（应为 {len(kinds)} 个 `<父>/<末段>`）——`_state_io` 的形态表"
                    f"或 `_runtime_segments` 漂了，本闸扫的字面集已不完整")
    new_tails = {r.split("/", 1)[1] for r in new}
    literals, bare = set(), set()
    for rel in legacy:
        head, tail = rel.split("/", 1)
        literals.update({f"{head}/{tail}", f"{head}\\{tail}", f"{head}\\\\{tail}"})
        if tail not in new_tails:
            bare.add(tail)
    if not bare:
        return [], "旧形态没有任何只属于它的末段——裸写形态（目录树里的 `workframe-state/` 一类）从此无人看守"
    return sorted(literals | bare, key=lambda s: (-len(s), s)), None


def _legacy_literal_scan(root, rels, literals, exempt):
    """扫描发布面里的旧字面。纯判定，不打印；`check_runtime_dir_legacy_literals` 与单测共用。

    `rels` 是候选清单（取材 `_repo_text_files()`），本函数按扫描面过滤。返回 dict：
      floor      扫描面下限缺的组成（非空即红）
      unreadable 读不出或不是 UTF-8 的文件（非空即红——读不出就不算扫过）
      hits       [(rel, 行号, 命中字面, 是否配方)]，已扣掉豁免
      expired    [(路径, 特征串, 类)]：找不到「同时含特征串与旧字面」的行
      scanned    实际扫过的文件数
    """
    root = Path(root)
    face = sorted(r for r in set(rels)
                  if r.startswith(_LEGACY_FACE_PREFIXES) or r in _LEGACY_FACE_FILES
                  or r == _LEGACY_CHANGELOG)
    floor = []
    if not any(r.startswith("plugins/") and Path(r).parent.name == "bin" and Path(r).suffix == ""
               for r in face):
        floor.append("plugins/*/bin/ 下无扩展名的入口")
    if "plugins/core/templates/gitignore-template" not in face:
        floor.append("plugins/core/templates/gitignore-template")
    if not any(r.startswith("docs/") for r in face):
        floor.append("docs/ 下的文件")
    if "README.md" not in face:
        floor.append("根 README.md")

    unreadable, hits = [], []
    armed = {i: False for i in range(len(exempt))}
    by_path = {}
    for i, (path, feat, _cls, _why) in enumerate(exempt):
        by_path.setdefault(path, []).append((i, feat))
    scanned = 0
    for rel in face:
        try:
            text = (root / rel).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            unreadable.append(f"{rel}（{type(e).__name__}）")
            continue
        scanned += 1
        if rel == _LEGACY_CHANGELOG:
            # 分段判据与 `check_unreleased_changelog_numbers` 同一份（`_changelog_segments`）：
            # 已发布段是历史账，只扫未发布段（含其段标题行）
            lines = [(i, ln) for i, ln, _h, unrel in _changelog_segments(text) if unrel]
        else:
            lines = [(i, ln.rstrip("\r")) for i, ln in enumerate(text.split("\n"), 1)]
        rules = by_path.get(rel, [])
        for i, ln in lines:
            found = [lit for lit in literals if lit in ln]
            if not found:
                continue
            covered = False
            for idx, feat in rules:
                if feat is None or feat in ln:
                    armed[idx] = True
                    covered = True
            if not covered:
                hits.append((rel, i, found[0], "plugin-root.txt" in ln))
    expired = [(path, feat, cls) for i, (path, feat, cls, _why) in enumerate(exempt) if not armed[i]]
    return {"floor": floor, "unreadable": unreadable, "hits": hits, "expired": expired,
            "scanned": scanned}


def check_runtime_dir_legacy_literals():
    """发布面里运行态目录的**旧字面**零残留（豁免逐条枚举，且每条必须仍命中）。

    **为什么要这道闸**：skill / reference / 注入片 / 模板里的路径是模型据以执行的指令。旧字面
    `.claude/<旧末段>` 留在一处，新布局项目里模型就照它去读一个不存在的目录（配方类当场读不到
    `plugin-root.txt`）；照旧字面写下的记忆与状态落进旧目录，而框架只读写新位置（`_state_io.runtime_rel`），
    写下的东西没有读者。

    **扫描面**：`_repo_text_files()` 里的 `plugins/**`、`docs/**`、根 `README.md`、`.claude-plugin/**`，
    以及 `CHANGELOG.md` 的**未发布段**（分段判据复用 `_changelog_segments`，与
    `check_unreleased_changelog_numbers` 同一份）。**本闸自己不按后缀白名单筛**——无扩展名的 `bin/`
    入口、`.jsonl` / `.html` 都在；py / js 的 docstring 与注释在内。但取材层 `_repo_text_files()`
    自带二进制后缀黑名单（`_SCAN_EXCLUDED_SUFFIXES`，含 `.svg` / `.png` / `.pdf` 等），命中黑名单的文件
    不进扫描面（见能力边界 ⑧）。读失败或不是 UTF-8 ⇒ 红。
    下限：扫描面里至少有无扩展名的 `bin/` 入口、`templates/gitignore-template`、`docs/` 下的文件、
    根 `README.md`，少一样即红。

    **字面**：由 `_legacy_literal_forms()` 从 `_runtime_segments()` 现算——三个旧形态各出 `/`、`\\`、
    `\\\\` 三种分隔，外加只属于旧形态的末段裸写。

    **豁免** `_LEGACY_LITERAL_EXEMPT`：路径全等 ＋ 行内特征串（O 类可文件级）＋ 类 ＋ 理由。
    **过期**：必须仍有一行**同时**含特征串与旧字面，否则红——只看特征串的话，把那行的字面改掉
    之后豁免永不过期，却仍让人以为那里有一处需要豁免。

    **与 `paths_single_source` 分工**：那道闸管 `plugins/**` 下 py / js 的**代码字符串**、新旧两种
    形态都管；本闸管发布面**全部文本**里的**旧形态**。py 代码字符串里的旧字面两道都红，是有意的重叠。

    **既有实现复用了什么、为什么不整个复用**：取材复用 `_repo_text_files()`，字面复用
    `_runtime_segments()`，CHANGELOG 分段复用 `_changelog_segments()`。`check_no_drifted_literals`
    不扩一条规则进去——它只看 `.md/.py/.yaml/.json`、读失败 `continue`，无扩展名的 `bin/` 入口、
    `.jsonl` 与读不出的文件都会静默出扫描面，而这三种正是本闸必须红的形态。
    `check_no_plugin_root_env_in_skills` 只借「文件 ＋ 行内特征串」的豁免表形——它按绝对路径子串
    匹配，路径写错时仍可能命中别的文件、豁免永不落空；本闸路径全等，写错即过期红。

    **红灯文案**按机器判据分两种：行内含 `plugin-root.txt` 算配方，其余算说明。

    **能力边界**：① 目录树里 `.claude/` 下单独一行 `agent-memory/` 抓不到——那一末段新旧同名，
    只靠同一棵树里的 `workframe-state/` 行连带；② 分拼形态（`path.join('.claude', 'agent-memory')`、
    跨行拼接）抓不到；③ 将来出现的 CC 官方 `memory: project` 目录若写成带 `.claude/` 前缀的全路径，
    字面与框架自己的旧目录完全相同，只能靠豁免表区分，**豁免表写错本闸看不出**；④ 本闸只证「旧字面
    没了」，不证「新句子是真话」，也不证配方目标存在——不含字面、却把记忆或运行状态说成在 `.claude/`
    下的类别级描述（「`.claude/` = 运行时层（…记忆 / 运行状态）」一类）本闸看不见；⑤ `CHANGELOG.md`
    里第一个以 `## [` 开头的段标题之前的全部内容不属于任何段、不在扫描面内——未发布段标题若没写成
    `## [` 开头（如 `## 1.1.0 — 未发布`），整段都被当作文件头不扫，而 `check_unreleased_changelog_numbers`
    共用同一份分段判据，会同时报「无未发布段」、两道一起静默；⑥ O 类文件级豁免覆盖该文件的**全部**
    行——单源所有者里新写进一处旧字面（例如 docstring 里多提一句），本闸看不见；⑦ 扫描面只认 git 跟踪的
    文件（git 不可用时退回目录遍历），未 `git add` 的新文件在提交前扫不到；⑧ 取材层的二进制后缀黑名单
    （`.svg` / `.png` / `.pdf` 等）挡在扫描面外的文件本闸看不见，其中 `.svg` 是文本、可能含路径；
    ⑨ 字面是逐字子串匹配，以下形态抓不到：JSON 转义斜杠（`.claude\\/agent-memory`）、花括号展开
    （`.claude/{agent-memory,memory-archive}/`）、大小写变体（`.Claude/Workframe-State`）、同一行拆成两段
    反引号（`` `.claude/` 下的 `agent-memory/` ``，与 ① ② 同族）；⑩ 行级豁免按「这一行含特征串」放行
    整行——被豁免那一行上另写的任何旧字面会一并放过。
    """
    name = "runtime_dir_legacy_literals"
    literals, problem = _legacy_literal_forms()
    if problem:
        return _fail(name, problem)
    r = _legacy_literal_scan(FRAMEWORK_ROOT, _repo_text_files(), literals, _LEGACY_LITERAL_EXEMPT)
    # 四族红灯各带各的处置——统一挂一句「改成新形态」会把扫描面缩水、读失败、豁免过期的人
    # 送去改一个根本不存在的字面
    problems = []
    if r["floor"]:
        problems.append(f"扫描面缺 {'、'.join(r['floor'])}——取材层缩水，本闸对缺掉的那一片恒绿"
                        f"（查 `_repo_text_files()` 为什么没取到它；有意改名或移除时同批改本闸的下限）")
    if r["unreadable"]:
        problems.append(f"{len(r['unreadable'])} 个文件读不出或不是 UTF-8（{'、'.join(r['unreadable'][:6])}）"
                        f"——其中的旧字面本闸看不见，读不出就不算扫过（把文件存成 UTF-8）")
    groups = []
    for is_recipe, msg in ((True, _LEGACY_MSG_RECIPE), (False, _LEGACY_MSG_PROSE)):
        group = [f"{rel}:{i} `{lit}`" for rel, i, lit, rec in r["hits"] if rec is is_recipe]
        if group:
            groups.append(f"{'配方' if is_recipe else '说明'} {len(group)} 处旧字面（{msg}）: "
                          + "、".join(group[:8]) + (f" …共 {len(group)} 处" if len(group) > 8 else ""))
    if groups:
        problems.append("；".join(groups)
                        + "——改成 `_state_io.new_rel` 给出的新形态；确属迁移对照 / eval 断言 / 单源实现的，"
                          "进 `_LEGACY_LITERAL_EXEMPT` 并写类与理由")
    if r["expired"]:
        problems.append("豁免过期（找不到同时含特征串与旧字面的行——原句已改或路径写错，这条豁免"
                        "什么都不再豁免）: "
                        + "、".join(f"{p}〔{c}〕`{f if f is not None else '<文件级>'}`"
                                   for p, f, c in r["expired"])
                        + "——对着原句现状改这条豁免的路径或特征串；原句已不含旧字面时，这条豁免已无对象")
    if problems:
        return _fail(name, "；".join(problems))
    return _ok(name, f"（{r['scanned']} 个发布面文件、{len(literals)} 种旧字面零残留；"
                     f"{len(_LEGACY_LITERAL_EXEMPT)} 条豁免均仍命中）")


def check_runtime_dir_legacy_literals_unit_tests_pass():
    """旧字面闸的单测：跑 tools/test_runtime_legacy_literals.py。

    被测的是 `_legacy_literal_forms` / `_legacy_literal_scan` / `_changelog_segments` 三件的判定面——
    字面形状、扫描面过滤与下限、CHANGELOG 分段边界、豁免的命中与过期、读失败、两种红灯的分派。
    组数与断言以测试文件自身为准，此处不复述计数。
    """
    name = "runtime_dir_legacy_literals_unit_tests_pass"
    import os
    test_path = FRAMEWORK_ROOT / "tools" / "test_runtime_legacy_literals.py"
    if not test_path.exists():
        return _fail(name, "tools/test_runtime_legacy_literals.py missing")
    try:
        result = subprocess.run([sys.executable, str(test_path)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=180,
                                env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    except Exception as e:
        return _fail(name, f"run error: {e}")
    if result.returncode != 0:
        return _fail(name, f"test exit={result.returncode}: {_suite_failure_summary(result.stdout + result.stderr)}")
    if "all passed" not in result.stdout:
        return _fail(name, "套件退出 0 但没有打出 all passed——半途退出与通过同形，按未通过处理")
    return _ok(name, "(test suite passed)")


def _claude_env_reads(src):
    """源码里对 `CLAUDE_*` 环境变量的**读取点**，返回 [(变量名, 行号)]。

    只认读：`os.environ["X"]`（`ctx=Load`）/ `os.environ.get("X")` / `os.getenv("X")`。
    **写不算**——`os.environ["X"] = …` 与 `dict(os.environ, X=…)` 是给子进程铺 CC 门的
    环境，那是 harness 中立化要改成传 `--harness` 的东西，不是本闸的靶子。
    """
    def _is_claude(node):
        return (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value.startswith("CLAUDE_"))

    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Load) and _is_claude(n.slice):
            out.append((n.slice.value, n.lineno))
        elif isinstance(n, ast.Call):
            fname = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if fname in ("get", "getenv") and n.args and _is_claude(n.args[0]):
                out.append((n.args[0].value, n.lineno))
    return out


def check_no_direct_claude_env():
    """`CLAUDE_*` 环境变量只能由 `_harness.py` 读。

    Codex 门下这些变量一个都不存在，直读的脚本会**静默走兜底分支**——不报错、不告警，
    只是行为悄悄变成另一套（项目根变成 cwd、会话号变成空串、插件根退到 `__file__` 推导）。
    收在一处之后，Codex 侧要改的是一个文件而不是十四个。

    **能力边界**：① 只钉**读**（`os.environ.get(...)` / `os.environ[...]` / `os.getenv(...)`）。
    **写**不在扫描面内——`dict(os.environ, CLAUDE_PROJECT_DIR=…)` 这类是给子进程模拟
    CC 门的测试脚手架（`module_close_check` / `module_init` / validate 各一处），
    它们在 Codex 门下要改成传 `--harness`，那是 harness 中立化的活，本闸不替它表态。
    ② 变量名本身是拼出来的（`_V = "CLAUDE_" + "PROJECT_DIR"; os.environ.get(_V)`）看不见
    ——判据钉的是实参那个字符串常量。这一条比①刁钻得多，未见于本仓任何脚本。
    ③ 扫描面 = 随插件分发的全部 Python，**含 `bin/` 下无扩展名的 shebang 入口**
    （见 `_python_sources`；漏了它们正是 2026-08-16 那个盲区的复发）。
    """
    name = "no_direct_claude_env"
    bad = _scan_face_fail(name)
    if bad:
        return bad
    owner = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "_harness.py"
    if not owner.exists():
        return _fail(name, "_harness.py 缺失——CLAUDE_* 的唯一读取点没了")
    hits = []
    for p in _shipped_py():
        if p == owner:
            continue
        try:
            reads = _claude_env_reads(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as e:
            return _fail(name, f"{_rel(p)} 解析失败: {e}")
        hits += [f"{_rel(p)}:{lineno}" for _v, lineno in reads]
    if hits:
        return _fail(name, f"{len(hits)} 处直读 CLAUDE_* 环境变量（Codex 门下该变量不存在，"
                           f"会静默走兜底分支：项目根变 cwd、会话号变空串、插件根退到 "
                           f"__file__ 推导，全程零告警）: " + "、".join(hits[:8]))
    # 下限：`_harness` 自己必须还**真的在读**那几个变量，否则「零直读」只是因为没人读了。
    # 判据必须是「读」而不是「字面出现过」——这三个名字在本文件 docstring 里就写着，
    # 用子串判会把「改成读另一个变量名」判成通过（突变实测：当场假绿）。
    read_names = {v for v, _ in _claude_env_reads(owner.read_text(encoding="utf-8"))}
    for var in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE_SESSION_ID"):
        if var not in read_names:
            return _fail(name, f"_harness.py 已不再读 `{var}`（散文里提到不算）——CC 门下"
                               f"这一项会恒走兜底分支，且与 Codex 门表现相同，"
                               f"不再有任何一处能看出差别")
    return _ok(name, f"{len(_shipped_py()) - 1} 份脚本零直读，CLAUDE_* 全部经 _harness")

# ---- 事件层：harness 标注的四条 ----

# 事件 producer 的取材：**闭包扫描，不列名单**。名单式取材漏一个 producer，那个文件就
# 永远合规而闸报绿——本文件里 `bin/` 盲区与 `check_bin_git_index_modes` 的写死名单各栽过
# 一次，两次都是「扫描面窄了」而不是「判定写错了」。
_EVENT_PRODUCER_TOKENS = ("events.jsonl", "EVENTS_FILE", "event_json", "event_lines")


# **已声明的非 events 追加点**：调 `append_line` 但目标不是事件流的文件，`(源文件, 目标文件名)`。
# 事件层的三条闸（取材下限 / 序列化单源）据此放行这一格；**未声明的新增追加点仍会变红**，
# 方向过严不过松。与 `events_append_single_source` 里的 `NON_EVENT_APPENDS` 同一形状、同一
# 判定式（`_call_writes_target`，按文件名判），**放行的是那一次调用而不是整个文件**——
# 整文件放行的话，同一文件里后来真加了一条 events 追加也一并静默通过。
# **粒度写实**：判据看的是该次调用的**第一个位置参数**（承载路径的那个）与接收者，
# payload 不在扫描面内。BUG-015 之前是「实参子树里出现该文件名字面的那一次调用」，
# 那个粒度能被 payload 里的一句字面串骗过。
NON_EVENT_APPEND_LINE_OWNERS = {
    # 注入投递流水：一次注入投了哪一片、多少字符、内容 sha256。它不是事件流，不进
    # events schema，行里的 `harness` 是打包器按 `--harness` 自己写的，不经 `event_json`。
    ("plugins/core/scripts/inject-context.py", "inject-log.jsonl"),
    # 收口检查 `[discipline-trace]` 的计数日志（运行行与标记行共用这一个写入点）。不是事件流，
    # 不进 events schema；文件名在该文件里是模块常量 `_TRACE_LOG`，靠常量的赋值被认出来。
    ("plugins/core/scripts/module_close_check.py", "discipline-trace-log.jsonl"),
}


def _event_producer_sources():
    """随插件分发的、**会碰 events.jsonl** 的 Python 源。取材层是 `_shipped_py()`
    （`*.py` ∪ shebang 指向 python 的无后缀入口）。两条判据取并集：

      ① 文本里出现 `events.jsonl` / `EVENTS_FILE` / `event_json` / `event_lines` 任一；
      ② **`plugins/*/bin/` 下的入口一律进面，不看内容**。

    ②不是冗余，是补①的一个真缺口：`bin/` 入口一律是薄壳（`sys.path.insert` 后 import
    `scripts/` 里的 `main`），四个 token 一个都不含——`bin/workframe-event` 是事件命令的
    入口却按①判不进来。这一格自证过：给它注入一行本地时区事件 ts 时，**只有当注入内容
    自己带上 `EVENTS_FILE` 字样才会被抓到**，不带就两边都看不见。判定层只在「这一行确实
    在造事件 ts」时才开火，所以把惰性薄壳收进取材面零代价、也不会造假红。

    **能力边界**：①那半仍是文本命中，`scripts/` 下一个把文件名拼出来的 producer
    （`"events" + ".jsonl"`）看不见。下限 `_event_producer_scan_face_problems()` 用
    **AST 独立枚举一次**（谁在调 `append_line` / `append_lines`）来抓这一格——两边判据
    不同源，判据坏掉时两个集合就对不上。
    """
    bin_dirs = {d.resolve() for d in (FRAMEWORK_ROOT / "plugins").glob("*/bin") if d.is_dir()}
    out = []
    for p in _shipped_py():
        if p.resolve().parent in bin_dirs:
            out.append(p)
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(tok in text for tok in _EVENT_PRODUCER_TOKENS):
            out.append(p)
    return out


def _event_producer_scan_face_problems():
    """取材下限：**AST 里调了 `append_line` / `append_lines` 的文件**必须都在取材面内。

    与 `_event_producer_sources` 的文本判据不同源，所以文本判据被改窄一个 token 时，
    这里立刻对不上——而不是让三条闸在一个更小的集合上继续报「零命中」。
    """
    covered = {p.resolve() for p in _event_producer_sources()}
    problems = []
    for p in _shipped_py():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                fname = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
                if fname not in ("append_line", "append_lines") or p.resolve() in covered:
                    continue
                if any(_rel(p) == owner and _call_writes_target(tree, n, target)
                       for owner, target in NON_EVENT_APPEND_LINE_OWNERS):
                    continue          # 已声明的非 events 追加点，本就不该进事件取材面
                problems.append(_rel(p))
                break
    return sorted(set(problems))


def _hook_command_entries(door="cc"):
    """`door` 那份清单里的全部 POSIX `command`，**现算**：[(事件, matcher, 命令串)]。

    读的是文件本身而不是任何名单——新增一条 hook 自动进本闸视野。两份清单的调用方各自
    按 `door` 枚举（`HOOK_MANIFEST_RELS`）；`commandWindows` 不在这里（它由 `_hook_field_entries`
    与 `codex_hook_command_windows_shape` 管）。
    """
    return [(event, matcher, cmd) for event, matcher, field, cmd in _hook_field_entries(door)
            if field == "command"]


def check_event_schema_v3_availability():
    """事件注册表必须是 v3，且**每一类**事件都声明了两扇门各自的可得性。

    为什么这条要存在：两门混合的 events.jsonl 里，一类事件为零可能是「真的没发生」，
    也可能是「那扇门根本产不出它」。不把这两件事在**数据**里分开，消费方就只能靠散文
    记住——而实测的失效形态正是把 `turn_failed` 在 Codex 侧恒为 0 读成「Codex 会话
    失败更少」。所以 availability 是每类事件的必填项，不是可选注释。

    三条判据：
      ① `__schema_version__` 是 v3，且 `harness_field` / `availability_values` 两段在位；
      ② 每类事件都有 `availability`，`cc` / `codex` 取值在 `availability_values` 声明的
         枚举内（枚举本身也从文件里读，闸不自带副本）；
      ③ **任一侧不是 `full` 就必须有非空 `note`**——「这里有个缺口」而不说是哪一路缺、
         读者会怎么误读，等于把已知缺口写成了暗坑。

    **能力边界**：本闸判的是「有没有声明、声明合不合法」，**判不了声明得对不对**。
    「`turn_failed` 在 Codex 侧到底能不能捕获」是探针结论，机器读不出来；写错了只能靠
    review 与 Codex 门真正落地时的实机对等探针抓。

    **这条边界的具体形状，说到能复现为止**——机器验得了「声明在不在、合不合法」，验不了
    「声明说的是不是真的」，于是下面这两族改动本闸一律**不红**：

    1. **任何把某一格放宽到 `full` 并同时去掉 `note`**：`full` + 无 note 本就是合法形态。
       `unknown → full`、`partial → full`、`none → full` 三种实测全部报绿；其中
       `none → full` 那次，绿灯摘要里「跨门缺口」从 3 类变成 2 类，**闸看得见这个变化
       却不报红**。只举其中一个实例会让读者以为另外两种被覆盖了，所以写的是整族。
    2. **把 `__delivery_established__` 的某扇门翻成 `true`**：第四张表随之清空，闸看到的是
       「声明已确立、表为空」这个自洽组合，无从分辨投递是真接上了还是只是标志被改了。

    两族的共同形状是：**它们改的都是「对世界的断言」，而闸只看得见文件内部的自洽性。**
    这一格只能靠 review 与 Codex 门真正落地时的实机对等探针。
    """
    name = "event_schema_v3_availability"
    path = (FRAMEWORK_ROOT / "plugins" / "core" / ".workframe-meta" / "event-schema.json")
    if not path.exists():
        return _fail(name, "event-schema.json 缺失")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return _fail(name, f"event-schema.json 解析失败: {e}")

    problems = []
    version = data.get("__schema_version__", "")
    if version != "workframe.event-schema.v3":
        problems.append(f"__schema_version__ = {version!r}（应为 workframe.event-schema.v3）")
    for seg in ("harness_field", "availability_values"):
        if not data.get(seg):
            problems.append(f"缺 `{seg}` 段（`harness` 字段与 availability 取值的唯一定义处）")
    if "harness" not in str(data.get("harness_field", "")):
        problems.append("`harness_field` 段没提到 harness 字段本身——那段是它的唯一定义处")

    values = data.get("availability_values") or {}
    allowed = {k for k in values if not k.startswith("__") and k != "note"}
    if not allowed:
        problems.append("`availability_values` 没声明任何取值，枚举无从校验")
    # 分档判据必须留在文件里：第一版把它留成隐规则，结果三个 SessionEnd 驱动的事件
    # 被判得过宽（BUG-009）。判据本身机器验不了对错，但**它在不在**验得了。
    how = str(values.get("__how_to_judge__", ""))
    if not how.strip():
        problems.append("`availability_values.__how_to_judge__` 缺失或为空——分档判据一旦"
                        "退回隐规则，下一个人就会按「事件名在不在枚举里」而不是「有没有"
                        "被探针实测」来判，那正是 BUG-009 的成因")
    else:
        for token in ("SessionStart", "SubagentStart", "PostToolUse", "Stop"):
            if token not in how:
                problems.append(f"`__how_to_judge__` 没点名已实测的 hook `{token}`——"
                                f"判据里那份名单是「已确立」的唯一依据，缺项就判不了")
                break
        # 判据里的三层缺一层都会让它推不出自己的既有档位——这三条是逐条实证补上去的：
        # 缺 matcher 层 → PostToolUse-only 那三类被推成 full；缺发射点粒度 → 多挂载点脚本
        # 的每个事件都按脚本的全部挂载算；缺「已证伪 vs 未判定」→ none 与 unknown 分不开。
        # 机器判不了判据写得**对不对**，但判得了这三层**在不在**。
        for token, why in (
            ("MATCHER-SCOPED", "缺 matcher 那一层——hook 事件已观察 ≠ 该 matcher 会为目标"
                               "工具触发，少了它 PostToolUse-only 的事件会被推回 full"),
            ("EMISSION POINT", "缺发射点粒度——路径的单位是「发射点能被哪些挂载点走到」，"
                               "按脚本的全部挂载算会把多挂载点脚本里的事件判宽"),
            ("DISPROVEN", "缺「已证伪 vs 未判定」的分界——`none` 与 `unknown` 都是"
                          "「没观察到」，不写清楚就分不开"),
        ):
            if token not in how:
                problems.append(f"`__how_to_judge__` 缺 `{token}` 那一层：{why}")
    events = data.get("events") or {}
    if not events:
        problems.append("events 段为空")
    for ev_type, spec in sorted(events.items()):
        avail = spec.get("availability") if isinstance(spec, dict) else None
        if not isinstance(avail, dict):
            problems.append(f"{ev_type}: 缺 availability 段")
            continue
        for door in ("cc", "codex"):
            val = avail.get(door)
            if val not in allowed:
                problems.append(f"{ev_type}.availability.{door} = {val!r}（合法值 {sorted(allowed)}）")
        if any(avail.get(d) != "full" for d in ("cc", "codex")):
            if not str(avail.get("note", "")).strip():
                problems.append(f"{ev_type}: 有一侧不是 full 却没写 note（缺口不许只标不说）")
    # 消费端的不变量**实跑**一次：`harness_breakdown` 必须把「跨门缺口」与「两侧皆无」
    # 分成两张表。合成一张时 codex 那格会多出一个两侧皆无的类型，而 audit 要求逐字照抄进
    # 报告首行——读者据此把跨门缺口数读大（实际发生过：报 4，真值 3）。
    if not problems:
        try:
            import importlib.util
            src = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "_harness.py"
            spec = importlib.util.spec_from_file_location("_wf_harness_probe", src)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            bd = mod.harness_breakdown([])
        except Exception as e:
            problems.append(f"harness_breakdown 探针跑不起来: {type(e).__name__}: {e}")
        else:
            cross = bd.get("cross_door_gaps")
            neither = bd.get("neither_door")
            if not isinstance(cross, dict) or not isinstance(neither, list):
                problems.append("harness_breakdown 缺 `cross_door_gaps` / `neither_door` 两张表"
                                "——合成一张会把两侧皆无的类型算进跨门缺口")
            else:
                doors = tuple(mod.HARNESSES)
                for door, names in cross.items():
                    for ev_type in names:
                        a = events.get(ev_type, {}).get("availability", {})
                        if a.get(door) != "none":
                            problems.append(f"cross_door_gaps[{door}] 混进了非 none 的 {ev_type}")
                        elif all(a.get(h) == "none" for h in doors):
                            problems.append(f"cross_door_gaps[{door}] 混进了**两侧皆无**的 "
                                            f"{ev_type}——它与门无关，算进去会把跨门缺口读大")
                for ev_type in neither:
                    a = events.get(ev_type, {}).get("availability", {})
                    if not all(a.get(h) == "none" for h in doors):
                        problems.append(f"neither_door 混进了并非两侧皆无的 {ev_type}")
            # 第四张表：模型侧事件判 full，但「告知模型去写」那条链在该门未确立。
            # 它替掉了原先写在 audit 散文里的一段手写 caveat——散文是副本、会漂，
            # 而本节自己的法则就是「清单现算、不许另抄」。
            deliv = bd.get("full_but_delivery_unverified")
            flags = (data.get("availability_values") or {}).get("__delivery_established__")
            if not isinstance(deliv, dict):
                problems.append("harness_breakdown 缺 `full_but_delivery_unverified` 表——"
                                "模型侧事件的投递缺口会退回散文 caveat，而散文是会漂的副本")
            elif not isinstance(flags, dict):
                problems.append("`availability_values.__delivery_established__` 缺失——"
                                "第四张表没有事实源，只能靠手写名单")
            else:
                for door, names in deliv.items():
                    if flags.get(door) is False and not names:
                        problems.append(f"{door} 的投递未确立，`full_but_delivery_unverified` "
                                        f"却是空的——这张表白设了")
                    if flags.get(door) is True and names:
                        problems.append(f"{door} 的投递已确立，表里却还列着 {len(names)} 类")
                    for ev_type in names:
                        spec = events.get(ev_type, {})
                        if spec.get("reliability") not in ("model_mediated", "protocol_expected"):
                            problems.append(f"full_but_delivery_unverified[{door}] 混进了"
                                            f"非模型侧的 {ev_type}")
                        elif (spec.get("availability") or {}).get(door) != "full":
                            problems.append(f"full_but_delivery_unverified[{door}] 混进了"
                                            f"并非 full 的 {ev_type}——那类该进缺口表，不是这张")

    if problems:
        return _fail(name, f"{len(problems)} 处：" + "；".join(problems[:6]))
    # 报「跨门缺口」时把两侧同为 none 的排除掉：那是「谁都产不出来」（`rule_triggered`），
    # 不是 Codex 缺一块。摘要里混着说，读的人会把跨门缺口数记大一个。
    gaps = sorted(k for k, v in events.items()
                  if v["availability"].get("codex") == "none"
                  and v["availability"].get("cc") != "none")
    both_none = sorted(k for k, v in events.items()
                       if v["availability"].get("codex") == "none"
                       and v["availability"].get("cc") == "none")
    return _ok(name, f"v3；{len(events)} 类事件各有 availability（枚举 {sorted(allowed)}），"
                     f"跨门缺口（codex none / cc 可得）{len(gaps)} 类：{gaps}"
                     f"；两侧皆无 producer {both_none}")


def check_hook_commands_declare_harness():
    """`hooks.json` 的**每一条**命令都要带 `--harness cc`，且不许混进别的门。

    为什么是参数而不是探测：两份 hook 表本来就是分开的，各自把自己的门写进命令行，
    确定性优于探测。探测法在嵌套启动（在 CC 的 Bash 里跑 `codex exec`）时 `CLAUDE_*`
    会泄漏进 Codex 的 hook 环境、恒判 `cc`。

    为什么**每一条**都要带、包括那四条 Codex 无对等物的（`Setup` / `StopFailure` /
    `ConfigChange` / `UserPromptExpansion`）：将来两扇门的 hook 表做孪生对账时，单位是「(事件, matcher,
    脚本, 规范化后的 argv)」四元组，两表要逐条可比；给缺口 hook 留一条没有 `--harness`
    的命令，等于在对账里留一个恒不相等的格子。

    扫描面 = 两份清单 **现算**的全部命令（新增一条 hook 自动进视野，不列名单）；
    另配下限：每份命令数不得低于 10——「全都带了」在一份被清空的清单上同样成立。

    **口径（两份清单并存后先定义再判）**：期望值按清单文件定——CC 清单每条 `--harness cc`、
    Codex 清单每条 `--harness codex`。混进对方的门标识 = 这条 hook 产出的事件会被归到错误的
    门下，且 `_harness.harness()` 据此选的分支（会话标记、片头会话行）在错的门上执行。
    """
    name = "hook_commands_declare_harness"
    total = 0
    for door in HOOK_MANIFEST_RELS:
        try:
            cmds = _hook_command_entries(door)
        except Exception as e:
            return _fail(name, f"[{door}] 清单读取/解析失败: {e}")
        if len(cmds) < 10:
            return _fail(name, f"[{door}] 清单只解析出 {len(cmds)} 条命令（应 ≥10）——"
                               f"清单被清空 / 结构变了时「全都带了」是假绿")
        missing, wrong = [], []
        for event, matcher, cmd in cmds:
            where = f"{event}({matcher or '*'})"
            if "--harness" not in cmd:
                missing.append(where)
            elif not re.search(rf"--harness(=|\s+){door}(\s|$)", cmd):
                wrong.append(f"{where}: {cmd[-40:]!r}")
        if missing:
            return _fail(name, f"[{door}] {len(missing)} 条 hook 命令没带 `--harness`（这些 hook 产出的"
                               f"事件会落成 harness=unknown，两门混流后分不出来）: " + "、".join(missing[:6]))
        if wrong:
            return _fail(name, f"[{door}] {len(wrong)} 条 hook 命令的门标识不是 `{door}`——这份清单里"
                               f"串进了别的门，事件会被归到错误的门下、按门分支的脚本在错的门上执行: "
                               + "、".join(wrong[:4]))
        total += len(cmds)
    return _ok(name, f"{total} 条 hook 命令各自声明本清单的门（cc 清单 `cc`、codex 清单 `codex`）")


def check_event_line_serialization_single_source():
    """事件行的序列化只能有一份实现：调 `append_line` / `append_lines` 时不得内联
    `json.dumps`，一律经 `_state_io.event_json` / `event_lines`。

    为什么必须收成一处：`harness` 要出现在**每一条** hook 产出的事件上，而事件字典由
    九个 producer 各自拼装。九处各加一行「顺手补个字段」，下一个新增的 producer 必然
    漏——漏了不会报错，只是那条事件在混流的 events.jsonl 里**按缺省被读成 cc**，一条来自
    Codex 的事件就此挂在 CC 名下，且没有任何一处看得出来。把它变成闸，纪律就不必每次
    被记住。

    三层，各管一段：
      宽   = `append_line` / `append_lines` 的**全部**调用点（AST，取材闭包）
      offender = 其中实参子树里含 `json.dumps` 的
      下限 = `event_json` / `event_lines` 的调用点数不得低于 9——offender 为零也可能是
             因为**没人再写事件了**（把调用点整个删掉同样零 offender）。

    **能力边界**：① 判据钉的是「调用点里有没有 `json.dumps`」，先把 JSON 串存进变量、
    再把变量传进来的写法看不见；下限抓不到这一格（它只数 event_json 用了几次），要靠
    review。② 将来若有**非 events** 的 jsonl 也走 `append_line`，本闸会要求它同样经
    `event_json`（于是被盖上 harness）——方向是过严而不是过松，届时按需补一份声明豁免，
    与 `events_append_single_source` 的 `NON_EVENT_APPENDS` 同一形状。
    """
    name = "event_line_serialization_single_source"
    face = _event_producer_scan_face_problems()
    if face:
        return _fail(name, f"取材面漏掉 {len(face)} 个在调 append_line/append_lines 的文件"
                           f"（{'、'.join(face[:5])}）——本闸的「零 offender」在它们上不成立")
    owner_rel = "plugins/core/scripts/_state_io.py"
    offenders, event_json_sites = [], 0
    for p in _shipped_py():
        rel = _rel(p)
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except SyntaxError as e:
            return _fail(name, f"{rel} 解析失败，序列化点无法判定: {e}")
        if rel == owner_rel:
            continue                      # 唯一实现自己就该直接 dumps
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            fname = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if fname in ("event_json", "event_lines"):
                event_json_sites += 1
            if fname not in ("append_line", "append_lines"):
                continue
            if any(rel == owner and _call_writes_target(tree, n, target)
                   for owner, target in NON_EVENT_APPEND_LINE_OWNERS):
                continue              # 非 events 目标：不该被盖 harness，也不进 event schema
            for arg in list(n.args) + [kw.value for kw in n.keywords]:
                for sub in ast.walk(arg):
                    if (isinstance(sub, ast.Call)
                            and getattr(sub.func, "attr", None) == "dumps"
                            and getattr(getattr(sub.func, "value", None), "id", None) == "json"):
                        offenders.append(f"{rel}:{n.lineno}")
                        break
    if offenders:
        return _fail(name, f"{len(offenders)} 处在追加事件时内联 json.dumps（未经 event_json，"
                           f"该行不会带 harness，混流后被按缺省读成 cc）: "
                           + "、".join(sorted(set(offenders))[:6]))
    if event_json_sites < 9:
        return _fail(name, f"只有 {event_json_sites} 处在调 event_json / event_lines（应 ≥9）——"
                           f"零 offender 是因为**没人再写事件了**，不是因为都走了单源")
    return _ok(name, f"{event_json_sites} 处经 event_json / event_lines 序列化，零内联 dumps"
                     f"（AST 判定，取材面 {len(_event_producer_sources())} 份 py）")


def check_hook_scripts_tolerate_harness_arg():
    """**实跑**：hooks.json 的**每一条**命令逐条跑两遍——带 `--harness cc` 与剥掉它各一次——
    断言 **exit code 相同且 stdout 逐字节相同**。

    这条不是形式检查，是行为检查。它防的是两个已实测的具体后果：
      - `check-stale-modules.py` 的 PostToolUse 那条**没有子命令**，`--harness` 会变成
        `args[0]` 落进「用法错误」分支并 `sys.exit(2)`，按 CC 官方语义那是**阻断
        PostToolUse**（该脚本自己的注释里就写着这句预言）；
      - 用 argparse `parse_args()` 的脚本对未知参数直接 exit 2，SessionStart
        那条整条失效。
    两者在改造前实测均为 exit 2，改造后 exit 0。

    **为什么是「逐条实跑」而不是「静态钉 + 抽两个跑」**：本闸的**断言面**是「给
    hooks.json 的每一条命令加这个参数都安全」，探针面若只覆盖已知会坏的那两个、其余靠
    静态判据（「读 argv 的脚本必须经 helper」），那静态判据恰恰判不出 argparse 之外的自
    定义 argv 读法——**探针面窄于断言面**。本仓在这个形状上栽过：一条闸按文件后缀取材、
    漏掉 `bin/` 下无扩展名的入口，而那里当时就有两处真实违例，闸照样报绿。所以口径改成
    逐条实跑，扫描面 = hooks.json **现算**的全部命令（新增一条 hook 自动进来）。

    **为什么 stdout 也要比、而不只比 exit code**：hook 的 stdout 会注入模型上下文，
    多一个参数把某条命令的输出改掉（哪怕仍 exit 0）同样是行为变更，而本批承诺的是
    「行为变更 = 事件行加一个字段」。实测**零差异、无需任何归一**——两趟跑在同一个
    路径上（跑完擦掉重新播种），路径串因此逐字相同，时间戳一类也没有出现在任何一条的
    stdout 里。哪天真出现了会漂的轴，正确做法是把那条轴登记出来，不是把断言降级为只比
    exit code。

    **能力边界**：① 探针喂的是各 hook 的 payload **形态**（`hook_event_name` + 常见字段）
    与一个临时项目根，走的是各脚本在该输入下的真实路径；它证的是「参数加进去不改变
    这条命令的行为」，**不是**「这条 hook 的业务逻辑仍然正确」——后者归各自的单测与恒等
    对账。② `plugins/*/bin/` 下的入口**不在本闸射程内**（它们不在 hooks.json 里）：其中
    `workframe-maintenance` 会真起 `claude -p` 会话、`workframe-python` 是 bash 启动器，
    两者都不适合在闸里实跑。

    **bin 侧只有写事件的那三个（`workframe-event` / `workframe-recompute-board-summary` /
    `workframe-recompute-skill-metrics`）接受 `--harness`**，那也正是 event-schema 的
    `harness_field` PARTITION 段点名承诺的三个，人工探针跑过。**其余 bin 入口不承诺、
    也不需要**——它们不挂 hook、不写事件；实测 `workframe-doctor` 与
    `workframe-module-close-check` 传 `--harness` 会被 argparse 判未知参数而 exit 2，
    `workframe-door` 同样走 argparse、同样不承诺（它是装机入口，不写事件），
    `workframe-audit-board-drift` 则是因为不解析 argv 才碰巧不报错，**不是声明过的口径**。
    `workframe-python` 又是另一种：它是启动器，`"$@"` / `%*` **透明转发**一切参数、自己一个都
    不消费——**本闸逐条实跑的那些命令能工作，靠的正是它这个转发**。把它归进「不承诺」侧是因为它不
    消费 `--harness`，不是因为它会拒绝。
    这一段此前写成「bin 侧的容纳性由各自 docstring 与人工探针覆盖」，把七个入口一起兜住了——
    那是假的（两个不容纳、三个 docstring 里根本没提），已按实测收窄到点名面。

    **两份清单都逐条实跑**（Codex 清单跑的是其 POSIX `command`，`commandWindows` 的运行时
    语义由 Codex 端到端探针验，本闸够不着 PowerShell）。Codex 清单里 `--shard all` 主线程片的
    片头会话行、`session-start-prep.py --harness codex` 写的会话标记，都设计成剥掉 `--harness`
    后 stdout 逐字节不变（标记只落状态目录、不进 stdout；会话行的门标识在缺省时落到 `codex`）。
    """
    name = "hook_scripts_tolerate_harness_arg"
    import os
    import shlex
    import shutil
    import subprocess
    import tempfile

    cmds = []
    for door in HOOK_MANIFEST_RELS:
        try:
            door_cmds = _hook_command_entries(door)
        except Exception as e:
            return _fail(name, f"[{door}] 清单读取/解析失败: {e}")
        if len(door_cmds) < 10:
            return _fail(name, f"[{door}] 清单只解析出 {len(door_cmds)} 条命令（应 ≥10）——"
                               f"清单被清空 / 结构变了时「逐条都过」是假绿")
        cmds += [(door, event, matcher, cmd) for event, matcher, cmd in door_cmds]

    plugin_root = FRAMEWORK_ROOT / "plugins" / "core"
    payloads = _HOOK_PROBE_PAYLOADS      # 与 codex_hooks_exit_outside_project 共用一份

    def seed(proj):
        if proj.exists():
            shutil.rmtree(proj, ignore_errors=True)
        (proj / "projects" / "modules").mkdir(parents=True, exist_ok=True)
        (proj / ".claude" / "workframe-state").mkdir(parents=True, exist_ok=True)
        (proj / ".claude" / "agent-memory" / "shared").mkdir(parents=True, exist_ok=True)
        (proj / ".workframe-config.json").write_text('{"project_name":"harness-probe"}',
                                                     encoding="utf-8", newline="")
        (proj / "projects" / "board.yaml").write_text(
            "summary:\n  total: 9\n  pending: 9\n  in_progress: 0\n  pending_qa: 0\n"
            "  completed: 0\n  blocked: 0\n  cancelled: 0\n  last_updated: null\n\n"
            "tasks:\n  - id: T1\n    status: pending\n  - id: T2\n    status: completed\n",
            encoding="utf-8", newline="")
        (proj / ".claude" / "agent-memory" / "shared" / "MEMORY.md").write_text(
            "# 共享记忆\n", encoding="utf-8", newline="")

    def one_pass(proj, keep_flag):
        """按清单顺序跑完全部命令；返回 [(标签, exit, stdout)]。

        **每换一扇门重新播种 fixture**：两门的会话序列在现实里各自独立，共用一份状态时
        第二扇门的 `session-start-prep` 会读到第一扇门 `session-end-flush` 写下的 digest 并把
        其中的时间戳打进 stdout——两趟相隔几十秒，同长不同字节，与「`--harness` 改变了行为」
        同形（实测撞到）。这条轴由播种隔离掉，而不是把断言降级为只比 exit code。
        """
        # Codex 清单里有 `codex-trust-seed.py`，它会起 codex app-server：不覆盖这两个变量时，
        # 子进程继承调用方的 `CODEX_HOME` 并从 PATH 找到 codex，本闸每跑一次就在调用方真实的
        # Codex home 里写一批运行态文件（实测隔离 home 上一次新增 82 个）。指向不存在的可执行
        # 文件时 trust-seed 不起进程、带与不带 `--harness` 两趟输出同一句「未能读取」。
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(proj),
                   CLAUDE_PLUGIN_ROOT=str(plugin_root),
                   CLAUDE_CODE_SESSION_ID="harness-probe-sess",
                   CODEX_HOME=str(proj.parent / "codex-home"),
                   WF_CODEX_EXE=str(proj.parent / "nope" / "codex.exe"))
        rows = []
        seeded_for = None
        for door, event, matcher, cmd in cmds:
            if door != seeded_for:
                seed(proj)
                seeded_for = door
            toks = shlex.split(
                cmd.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root).replace("\\", "/")),
                posix=True)
            # 双包装器 `bin/workframe-python` 是 bash 脚本，Windows 上起不来；用当前解释器
            # 顶替它那一格。本闸要验的是**命令串里的参数怎么传到脚本**，不是 launcher 自身
            # （它的契约由 workframe_python_launcher_exists 等三条闸各管一段）。
            if toks and toks[0].endswith("workframe-python"):
                toks[0] = sys.executable
            if not keep_flag:
                keep, skip = [], False
                for t in toks:
                    if skip:
                        skip = False
                        continue
                    if t == "--harness":
                        skip = True
                        continue
                    if t.startswith("--harness="):
                        continue
                    keep.append(t)
                toks = keep
            script = next((t for t in toks if t.endswith(".py")), None)
            if script is None:
                return None, f"{event} 的命令里找不到 .py 脚本: {cmd!r}"
            if not Path(script).exists():
                return None, f"{event} 指向的脚本不存在: {script}"
            pl = dict(payloads.get(event, {}))
            pl.update({"session_id": "harness-probe-sess", "cwd": str(proj),
                       "hook_event_name": event})
            if event == "PostToolUse":
                pl["tool_name"] = "Skill" if matcher == "Skill" else "Edit"
            try:
                r = subprocess.run(toks, input=json.dumps(pl, ensure_ascii=False),
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=180, cwd=str(proj), env=env)
            except Exception as e:
                return None, (f"{event}/{Path(script).name} 探针跑不起来: "
                              f"{type(e).__name__}: {e}")
            rows.append((f"[{door}] {event}({matcher or '*'})/{Path(script).name}",
                         r.returncode, r.stdout, (r.stderr or "").strip()[:160]))
        return rows, None

    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"          # 两趟同一路径：stdout 里的路径串才逐字可比
        with_flag, err = one_pass(proj, True)
        if err:
            return _fail(name, err)
        without_flag, err = one_pass(proj, False)
        if err:
            return _fail(name, err)

    bad_exit = [(k, c, e) for k, c, _o, e in with_flag if c != 0]
    if bad_exit:
        return _fail(name, f"{len(bad_exit)} 条 hook 命令带上 `--harness <门>` 后非零退出"
                           f"（PostToolUse 上 exit 2 = 阻断工具调用）: "
                           + "；".join(f"{k} exit={c} stderr={e!r}" for k, c, e in bad_exit[:4]))
    drift = []
    for (k1, c1, o1, _), (k2, c2, o2, _) in zip(with_flag, without_flag):
        if c1 != c2:
            drift.append(f"{k1}: exit {c1}（带）vs {c2}（不带）")
        elif o1 != o2:
            drift.append(f"{k1}: stdout 不同（带 {len(o1)} 字符 / 不带 {len(o2)} 字符）")
    if drift:
        return _fail(name, f"{len(drift)} 条命令的行为被 `--harness` 改变了——本批承诺的是"
                           f"「行为变更 = 事件行加一个字段」，stdout 与退出码都不该动: "
                           + "；".join(drift[:4]))
    # 件 D 的回归：trust-seed 在本闸里必须输出「未能读取」——那一行只在它没能对任何 Codex home 起 app-server 时出现。
    # 子进程 env 不再把 CODEX_HOME / WF_CODEX_EXE 指向闸自己的临时目录时，本机有 codex 就会对调用方的 home 起真
    # app-server 并写运行态文件，而上面的 stdout 比对照样逐字节相同、看不见。（本机没有 codex 时这一条恒绿——那时也写不了。）
    seed_out = [o for k, _c, o, _e in with_flag if k.startswith("[codex]") and k.endswith("/codex-trust-seed.py")]
    if not seed_out:
        return _fail(name, "Codex 清单里找不到 codex-trust-seed.py——本闸的 home 隔离断言无对象")
    if any("未能读取" not in o for o in seed_out):
        return _fail(name, "codex-trust-seed 在本闸里没有输出「未能读取」——它对某个 Codex home 起了真 app-server：子进程 env 没把 "
                           "CODEX_HOME / WF_CODEX_EXE 指向闸自己的临时目录，本闸每跑一次都会在调用方的 Codex home 里写运行态文件")
    per_door = {door: sum(1 for d, *_ in cmds if d == door) for door in HOOK_MANIFEST_RELS}
    return _ok(name, f"{len(cmds)} 条 hook 命令（{'、'.join(f'{d} {n}' for d, n in per_door.items())}）"
                     f"逐条实跑两遍（带 / 不带 `--harness`），退出码与 stdout 逐字节相同、零归一；"
                     f"Codex 清单只跑 POSIX `command`、`commandWindows` 归端到端探针；"
                     f"bin/ 入口不在射程内（只有写事件的三个接受 `--harness`，"
                     f"其余不挂 hook、不写事件、也不承诺），已登记为不含项")


# 逐条实跑 hook 命令时喂的载荷形态（`hook_event_name` / `session_id` / `cwd` 由调用方补）。
# 两道实跑闸共用这一份：`hook_scripts_tolerate_harness_arg` 与 `codex_hooks_exit_outside_project`。
_HOOK_PROBE_PAYLOADS = {
    "SessionStart": {"source": "startup"},
    "Setup": {"matcher": "maintenance"},
    "UserPromptSubmit": {"prompt": "hi"},
    "PostToolUse": {"tool_input": {"skill": "librarian"}},
    "SubagentStart": {"agent_type": "dev"},
    "SubagentStop": {"agent_type": "dev"},
    "StopFailure": {"error": "rate_limit"},
    "ConfigChange": {"files": []},
    "SessionEnd": {"reason": "clear"},
    "UserPromptExpansion": {"command": "/core:librarian"},
}

_OUTSIDE_GUARD_FN = "hook_outside_project"


def _main_guard_problem(src):
    """一份 hook 脚本的源码：`main()` 的首条语句（或 `if __name__ == "__main__":` 块的首条语句）是不是
    早退守卫。合规返回 None，否则返回一句说明。

    守卫的形态只认两种（**不认 `not`、不认 `or`**——取反的守卫会让脚本在项目**内**早退）：
      `if hook_outside_project(…): return | return 0 | sys.exit(0)`
      `if <前置条件> and hook_outside_project(…): …`（调用须是 `and` 的最后一项；`inject-context.py` 的
       `--dry-run` 豁免用这一形）
    调用写成 `_harness.hook_outside_project(…)` 或裸名都认。**能力边界**：前置条件恒假（`if False and …`）
    这种形态静态半看不出，归行为半——行为半只对「剥掉 `--harness` 后本来就有产出」的命令看得见。
    """
    tree = ast.parse(src)

    def is_guard_call(expr):
        return (isinstance(expr, ast.Call)
                and (getattr(expr.func, "attr", None) or getattr(expr.func, "id", None)) == _OUTSIDE_GUARD_FN)

    def exits_zero(stmt):
        if isinstance(stmt, ast.Return):
            return stmt.value is None or (isinstance(stmt.value, ast.Constant) and stmt.value.value in (0, None))
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) \
                and getattr(stmt.value.func, "attr", None) == "exit" \
                and getattr(getattr(stmt.value.func, "value", None), "id", None) == "sys":
            a = stmt.value.args
            return not a or (isinstance(a[0], ast.Constant) and a[0].value in (0, None))
        return False

    def first_is_guard(body):
        body = [s for s in body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                                        and isinstance(s.value.value, str))]
        if not body or not isinstance(body[0], ast.If):
            return False
        s = body[0]
        test_ok = is_guard_call(s.test) or (isinstance(s.test, ast.BoolOp) and isinstance(s.test.op, ast.And)
                                            and is_guard_call(s.test.values[-1]))
        return test_ok and bool(s.body) and exits_zero(s.body[0]) and not s.orelse

    mains = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"]
    dunder = [n for n in tree.body if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
              and getattr(n.test.left, "id", None) == "__name__"]
    if any(first_is_guard(m.body) for m in mains) or any(first_is_guard(d.body) for d in dunder):
        return None
    where = "main()" if mains else ("`if __name__ == \"__main__\":` 块" if dunder else "模块（既无 main() 也无 __main__ 块）")
    return f"{where} 的首条语句不是 `if …{_OUTSIDE_GUARD_FN}(…): return`"


def check_codex_hooks_exit_outside_project():
    """Codex 清单挂载的每个脚本：会话不在 workframe 项目内时**零写入、零 stdout、exit 0**；在项目根里照常跑；
    子目录里起的会话写到项目根。

    **为什么要有**：Codex 的插件启用是用户层的。装机器把用户层置 `false`、只在装过的项目里开，但用户层
    一旦被写回 `true`（裸敲一次 `codex plugin add` 就会），任何目录的 Codex 会话都会跑本插件全部 hook——
    新建 `.workframe/state/`、补建 `AGENTS.md`、把纪律片注入一个与框架无关的会话。守卫是那个状态的兜底，
    门条件（只管 Codex）收在 `_harness.hook_outside_project()` 一处。

    **静态半**：从 `hooks.codex.json` 现算脚本集合，AST 判每个脚本 `main()`（或 `__main__` 块）首条语句是守卫
    （形态见 `_main_guard_problem`）。行为半对一部分脚本看不见（下述），静态半是它们唯一的闸。

    **行为半**：逐条实跑清单全部命令，env 按 Codex 形态——剥掉全部 `CLAUDE_*` 后只回填 `CLAUDE_PLUGIN_ROOT`、
    `CODEX_HOME` 指向临时目录、`WF_CODEX_EXE` 指向不存在的路径、**不设 `PYTHONUTF8`**；进程 cwd = 载荷 cwd；
    临时目录名带非 ASCII 字符（守卫若以文本模式读 stdin，在这里把真实项目误判为「在项目外」）。
      0 前提：临时根向上找不到根标记，否则整道闸「fixture 不可信」报红；
      1 无标记空目录：每条 exit 0、stdout 空、该目录与 `CODEX_HOME` 文件内容不变。**正向对照逐条自算**：
        同一条命令剥掉 `--harness`（`unknown` 下守卫不起作用）在另一个空目录里跑，有产出的记「守卫被证实」，
        本来就零产出的在绿灯里点名「行为面不可见，由静态半覆盖」；
      1b 无标记 cwd ＋ `CLAUDE_PROJECT_DIR` 指向带标记的目录：两个目录都零写入（守卫不读该变量）；
      1c CC 形态（`--harness cc`）、进程 cwd 无标记、`CLAUDE_PROJECT_DIR` 指向带标记项目：`heartbeat-check` 有产出
        ——门条件只管 Codex（用户拍板）；守卫对 CC 也生效时，CC 用户在非项目 cwd 起的 hook 会静默早退；
      2 带标记的项目根：对照 fixture（同形态、剥掉 `--harness`）里有产出的命令这里都有产出；
        `log-skill-invoked` 的事件行带载荷里的 skill 名（证 `main()` 自己的 stdin 读点拿到了守卫读过的那份字节）；
        `codex-trust-seed` 输出「未能读取」那一行（它在本 fixture 下的正向信号）；
      2b 带标记项目 A 里的会话 ＋ `CLAUDE_PROJECT_DIR` 泄漏指向带标记项目 B：B 零写入、A 有写入（`project_dir` 的
        Codex 分支不读该变量——嵌套启动时它会从外层会话泄漏进来）；
      3 带标记项目的子目录：子目录下零新文件，项目根有写入。
    **能力边界**：跑的是 POSIX `command`，`commandWindows` 由 Codex 端到端探针验；载荷只是各事件的**形态**，
    证的是「守卫与定位判得对」，不是各 hook 业务逻辑对；零凭据、不起 codex。所有 fixture 都令进程 cwd = 载荷
    cwd——守卫按载荷 cwd 判、多数脚本按进程 cwd 写，二者不等时的行为依赖 Codex 的 hook 工作目录契约（见
    `_harness.hook_outside_project`），本闸看不见这条轴。
    """
    name = "codex_hooks_exit_outside_project"
    import importlib.util
    import os
    import shlex
    import subprocess
    import tempfile

    try:
        cmds = _hook_command_entries("codex")
    except Exception as e:
        return _fail(name, f"Codex 清单读取/解析失败: {e}")
    if len(cmds) < 10:
        return _fail(name, f"Codex 清单只解析出 {len(cmds)} 条命令（应 ≥10）——清单被清空时「逐条都过」是假绿")
    scripts_dir = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    names = sorted({m.group(1) for _e, _m, c in cmds for m in [re.search(r"scripts/([A-Za-z0-9_.-]+\.py)", c)] if m})

    # ---- 静态半 ----
    static_bad = []
    for fname in names:
        p = scripts_dir / fname
        if not p.exists():
            static_bad.append(f"{fname}: 清单引用了但文件不存在")
            continue
        try:
            prob = _main_guard_problem(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            static_bad.append(f"{fname}: 语法错误，守卫位置无法判定: {e}")
            continue
        if prob:
            static_bad.append(f"{fname}: {prob}")
    if static_bad:
        return _fail(name, f"{len(static_bad)} 个 Codex 清单脚本没有在 main() 首行早退守卫——用户层启用被写回 true 时，"
                           f"任何目录的 Codex 会话都会跑它、在与框架无关的目录里写状态 / 注入内容: "
                           + "；".join(static_bad[:5]))

    # ---- 行为半 ----
    try:
        spec = importlib.util.spec_from_file_location("_wf_harness_outside_gate", scripts_dir / "_harness.py")
        hmod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hmod)
    except Exception as e:
        return _fail(name, f"_harness.py 加载失败: {type(e).__name__}: {e}")
    plugin_root = FRAMEWORK_ROOT / "plugins" / "core"
    root_posix = str(plugin_root).replace("\\", "/")

    def snap(root):
        root = Path(root)
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
                if p.is_file() and "__pycache__" not in p.parts} if root.exists() else {}

    def label(c):
        event, matcher, cmd = c
        return f"{event}({matcher or '*'})/" + cmd.split("scripts/")[-1].replace('"', "")

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "守卫探针-ü"
        base.mkdir()
        anc = hmod.find_root(base)
        if anc is not None:
            return _fail(name, f"fixture 不可信：临时目录的祖先 {anc} 带 {hmod.ROOT_MARKER}——守卫会把整棵临时树判成"
                               f"「在项目内」，fixture 1 的零写入断言无从成立（这不是被测脚本的问题，是跑闸环境的问题）")
        home = base / "codex-home"
        home.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")
               and k not in ("PYTHONUTF8", "PYTHONIOENCODING", "CODEX_HOME", "WF_CODEX_EXE")}
        env.update({"CLAUDE_PLUGIN_ROOT": str(plugin_root), "CODEX_HOME": str(home),
                    "WF_CODEX_EXE": str(base / "nope" / "codex.exe")})

        def seed_project(proj):
            (proj / "projects" / "modules").mkdir(parents=True)
            (proj / ".claude" / "workframe-state").mkdir(parents=True)
            (proj / ".claude" / "agent-memory" / "shared").mkdir(parents=True)
            (proj / hmod.ROOT_MARKER).write_bytes(b'{"project_name":"outside-guard-probe"}')
            (proj / "projects" / "board.yaml").write_bytes(
                b"summary:\n  total: 1\n  pending: 1\n  in_progress: 0\n  pending_qa: 0\n"
                b"  completed: 0\n  blocked: 0\n  cancelled: 0\n  last_updated: null\n\n"
                b"tasks:\n  - id: T1\n    status: pending\n")
            (proj / ".claude" / "agent-memory" / "shared" / "MEMORY.md").write_bytes("# 共享记忆\n".encode("utf-8"))
            return proj

        def run(c, keep_flag, cwd, extra_env=None):
            event, matcher, cmd = c
            toks = shlex.split(cmd.replace("${CLAUDE_PLUGIN_ROOT}", root_posix), posix=True)
            if toks and toks[0].endswith("workframe-python"):
                toks[0] = sys.executable
            if not keep_flag:
                toks = hmod.strip_harness(toks)
            pl = dict(_HOOK_PROBE_PAYLOADS.get(event, {}))
            pl.update({"session_id": "outside-guard-probe", "cwd": str(cwd), "hook_event_name": event})
            if event == "PostToolUse":
                pl["tool_name"] = "Skill" if matcher == "Skill" else ("Bash" if matcher == "Bash" else "Edit")
            e = dict(env, **(extra_env or {}))
            return subprocess.run(toks, input=json.dumps(pl, ensure_ascii=False).encode("utf-8"),
                                  capture_output=True, cwd=str(cwd), env=e, timeout=180)

        try:
            # fixture 1 ＋ 逐条正向对照
            proven, invisible, bad1 = [], [], []
            for i, c in enumerate(cmds):
                d = base / f"f1-{i:02d}-空目录"
                d.mkdir()
                h0 = snap(home)
                r = run(c, True, d)
                if r.returncode != 0 or r.stdout.strip() or snap(d) or snap(home) != h0:
                    bad1.append(f"{label(c)}: exit={r.returncode} stdout {len(r.stdout)} 字节、目录新增 "
                                f"{sorted(snap(d))[:3]}、CODEX_HOME {'变了' if snap(home) != h0 else '未变'}")
                u = base / f"f1c-{i:02d}-空目录"
                u.mkdir()
                rc = run(c, False, u)
                (proven if (rc.stdout.strip() or snap(u)) else invisible).append(label(c))
            if bad1:
                return _fail(name, f"{len(bad1)} 条命令在无标记目录里没有早退——Codex 门下与框架无关的目录会被建出 "
                                   f".workframe/state/ 与 AGENTS.md、会话被注入纪律片: " + "；".join(bad1[:4]))
            if not proven:
                return _fail(name, "正向对照里剥掉 --harness 后没有一条命令有产出——要么 fixture 1 的「零写入」对全部命令都"
                                   "看不见（行为半失明），要么守卫对 `--harness codex` 以外的门也生效了（"
                                   "`_harness.hook_outside_project` 的门条件被放宽或去掉，剥掉 --harness 的 unknown 形态也早退）")
            # fixture 1b
            u1b, m1b = base / "f1b-无标记-ü", seed_project(base / "f1b-带标记-ü")
            u1b.mkdir()
            s_u, s_m = snap(u1b), snap(m1b)
            bad1b = []
            for c in cmds:
                r = run(c, True, u1b, {"CLAUDE_PROJECT_DIR": str(m1b)})
                if r.stdout.strip():
                    bad1b.append(f"{label(c)}: stdout {len(r.stdout)} 字节")
            if snap(u1b) != s_u or snap(m1b) != s_m:
                bad1b.append(f"无标记目录{'被写' if snap(u1b) != s_u else '未写'}、`CLAUDE_PROJECT_DIR` 指向的项目"
                             f"{'被写' if snap(m1b) != s_m else '未写'}")
            if bad1b:
                return _fail(name, f"会话在无标记目录、`CLAUDE_PROJECT_DIR` 指向另一个项目时没有零写入——守卫或写入位置读了"
                                   f"这个变量：嵌套启动时它会从外层会话泄漏，Codex 会话的状态被写进别的项目: "
                                   + "；".join(bad1b[:4]))
            # fixture 1c（D11：门条件只管 Codex——CC 形态下同一守卫不许早退）
            cc_hb = [c for c in _hook_command_entries("cc") if "heartbeat-check.py" in c[2]]
            if not cc_hb:
                return _fail(name, "CC 清单里找不到 heartbeat-check.py——fixture 1c（门条件只管 Codex）无对象，不能报绿")
            u1c, m1c = base / "f1cc-无标记-ü", seed_project(base / "f1cc-带标记-ü")
            u1c.mkdir()
            s_m1c = snap(m1c)
            r = run(cc_hb[0], True, u1c, {"CLAUDE_PROJECT_DIR": str(m1c), "CLAUDE_CODE_SESSION_ID": "outside-guard-probe"})
            if not (r.stdout.strip() or snap(m1c) != s_m1c):
                return _fail(name, "CC 形态（--harness cc）下 heartbeat-check 在进程 cwd 无标记、CLAUDE_PROJECT_DIR 指向项目时零产出"
                                   "——守卫对 CC 也生效了：门条件只管 Codex（用户拍板），放宽后 CC 用户在非项目 cwd 起的 hook 会静默早退")
            # fixture 2（带标记项目根）与它的对照
            p2, p2c = seed_project(base / "f2-项目-ü"), seed_project(base / "f2c-项目-ü")
            if hmod.find_root(p2) != p2.resolve():
                return _fail(name, f"fixture 2 的根标记没落到 {p2}——「在项目内不早退」断言无对象，不能报绿")
            bad2, seed_line = [], False
            for c in cmds:
                before, before_c = snap(p2), snap(p2c)
                r = run(c, True, p2)
                rc = run(c, False, p2c)
                got = bool(r.stdout.strip()) or snap(p2) != before
                want = bool(rc.stdout.strip()) or snap(p2c) != before_c
                if want and not got:
                    bad2.append(label(c))
                if "codex-trust-seed.py" in c[2] and "未能读取".encode("utf-8") in r.stdout:
                    seed_line = True
            events = [p for p in p2.rglob("events.jsonl")]
            skill_seen = any('"skill": "librarian"' in p.read_text(encoding="utf-8", errors="replace")
                             or '"skill":"librarian"' in p.read_text(encoding="utf-8", errors="replace")
                             for p in events)
            # 只有 log-skill-invoked 零产出而其余都照常：成因是它在 main() 里读到了空载荷，不是守卫误早退——
            # 两种成因的修法不同，文案分开
            if bad2 and not (not skill_seen and all("log-skill-invoked.py" in b for b in bad2)):
                return _fail(name, f"{len(bad2)} 条命令在带标记的项目根里零产出（剥掉 --harness 时有）——守卫把真实项目"
                                   f"误判成「在项目外」，该项目的 Codex 会话静默拿不到纪律片与事件，exit 0 零告警"
                                   f"（常见成因：读取器以文本模式读 stdin，非 ASCII 路径被按本机代码页解码）: "
                                   + "；".join(bad2[:4]))
            if not skill_seen:
                return _fail(name, "log-skill-invoked 在项目根里没有写出载荷里那个 skill 的事件行——守卫读过 stdin 之后，"
                                   "脚本在 main() 里自己读到的不是同一份字节：skill_invoked 事件在 Codex 门下静默消失")
            if not seed_line:
                return _fail(name, "codex-trust-seed 在项目根里没有输出「未能读取」那一行（本 fixture 下 codex 可执行文件不存在，"
                                   "这一行是它跑到了补种逻辑的唯一信号）——它被误早退，升级后的自动补种在该项目里不会发生")
            # fixture 2b（K-F：Codex 门下写入位置不读 CLAUDE_PROJECT_DIR）
            a2b, b2b = seed_project(base / "f2b-A-ü"), seed_project(base / "f2b-B-ü")
            s_a, s_b = snap(a2b), snap(b2b)
            for c in cmds:
                run(c, True, a2b, {"CLAUDE_PROJECT_DIR": str(b2b)})
            b_changed = sorted(k for k, v in snap(b2b).items() if s_b.get(k) != v)
            if b_changed:
                return _fail(name, f"会话在带标记项目 A、`CLAUDE_PROJECT_DIR` 泄漏指向带标记项目 B 时，{len(b_changed)} 个文件写进了 B"
                                   f"（{b_changed[:3]}）——project_dir 的 Codex 分支读了这个变量：嵌套启动的 Codex 会话整体写进"
                                   f"另一个项目，两边都零告警")
            if snap(a2b) == s_a:
                return _fail(name, "fixture 2b 里项目 A 也零写入——「B 零写入」没有对照，不能报绿（hook 在 A 里被误判成在项目外？）")
            # fixture 3（子目录会话）
            q = seed_project(base / "f3-项目-ü")
            sub = q / "projects" / "sub"
            sub.mkdir(parents=True)
            s_q = snap(q)
            for c in cmds:
                run(c, True, sub)
            after = snap(q)
            changed = {k for k in after if s_q.get(k) != after[k]}
            in_sub = sorted(k for k in changed if k.startswith("projects/sub/"))
            if in_sub:
                return _fail(name, f"子目录里起的会话把 {len(in_sub)} 个文件写进了子目录（{in_sub[:3]}）——同一次会话劈成两棵"
                                   f"状态树，事件与计数各记一半，零告警")
            if not changed:
                return _fail(name, "子目录会话在项目根也零写入——hook 全被判成「在项目外」，子目录里的 Codex 会话一条都不跑")
        except subprocess.TimeoutExpired as e:
            return _fail(name, f"探针超时: {e}")
    return _ok(name, f"({len(names)} 个脚本 main() 首行守卫；{len(cmds)} 条命令 × 5 个 fixture ＋ 1 条 CC 对照实跑：无标记目录"
                     f"零写入零输出、泄漏的 CLAUDE_PROJECT_DIR 既不让无标记目录的会话写进项目也不把项目 A 的会话写进项目 B、"
                     f"项目根照常、子目录写到根、CC 形态不经守卫；行为面证实 {len(proven)} 条，"
                     f"行为面不可见、由静态半覆盖 {len(invisible)} 条：{'、'.join(invisible)})")


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
    bad = _scan_face_fail(name)
    if bad:
        return bad
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
    # ---- events.jsonl 的追加也只能有一份实现（`events_append_single_source`）----
    #
    # **闭包扫描，不是名单**：上面 activity-state 那半是名单式，它自述曾因名单漏一个
    # producer 而假绿（maintenance_workorder 当年不在名单里，一直整份 write_text 覆盖
    # 而闸报绿）。事件追加的 producer 有十处、还会增加，按名单扩等于把同一个坑再挖一遍。
    #
    # 三层，各管一段（缺任一层都留一条完整漏网通道）：
    #   宽：`plugins/**` 全部 py 里**任何追加模式的文件打开**（glob 现算，新文件自动进）
    #   严：目标解析得出、且**已声明**为非 events 的追加点
    #   差集：宽 - 严 = 没走 `_state_io` 的 events 追加 → 红
    # 外加下限：`append_line` / `append_lines` 的调用点数不得低于已知 producer 数——
    # 只有差集时，把调用点整个删掉（不再写事件）同样是零差集。
    #
    # **文件内的判定走 AST，不逐行正则**：文件枚举闭包、文件内判定不闭包同样留一条完整
    # 漏网通道。逐行正则实测漏三条——跨行的 `open(` 调用（格式化器对长行的常规产物，
    # 不是刁钻写法）、mode 串来自变量、`os.open` 经模块别名调用；而下限钉的是「还有没有人
    # 走单源」，**新增**一个绕开者不会让调用点数下降，两层同时失明。
    # **已声明的非 events 追加点**：每条都写清目标与理由。新增一条非 events 的追加会
    # 落进差集变红——那是 fail-safe 方向（过严不过松），补声明即可；而**新增一条 events
    # 追加**同样变红，正是本闸要抓的。
    NON_EVENT_APPENDS = {
        # 目标写**文件名**而不是整路径：定义点多是 `X / "logs" / "y.log"` 的分段拼接，
        # 整路径串在源码里根本不存在（写过一版，当场把已声明的那条判成漏网）
        ("plugins/core/scripts/log-subagent-activity.py", "subagent-activity.log"),
        ("plugins/core/scripts/maintenance_workorder.py", "promotion-candidates.md"),
    }
    io_owner_rel = "plugins/core/scripts/_state_io.py"
    wide, strict = [], set()
    for p in _shipped_py():
        rel = _rel(p)
        if rel == io_owner_rel:
            continue                      # 唯一实现自己就该直接追加
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            errors.append(f"{rel} 解析失败，追加点无法判定: {e}")
            continue
        for call, shape in _append_open_calls(tree):
            wide.append((rel, call.lineno, shape))
            for owner, target in NON_EVENT_APPENDS:
                if rel == owner and _call_writes_target(tree, call, target):
                    strict.add((rel, call.lineno))
    leaks = [w for w in wide if (w[0], w[1]) not in strict]
    if leaks:
        errors.append(
            f"{len(leaks)} 处直接以追加模式打开事件流（未走 _state_io.append_line）："
            + "、".join(f"{r}:{i}（{shape}）" for r, i, shape in leaks[:6])
            + "——并发下会丢行，且真实事件行长不等，覆盖会在文件里留**半行**，"
              "严格解析的消费方读到那一行直接抛异常（不是「少了几条」那种形态）"
        )
    call_sites = 0
    for p in _shipped_py():
        if _rel(p) == io_owner_rel:
            continue
        if re.search(r"\bappend_lines?\s*\(", p.read_text(encoding="utf-8", errors="replace")):
            call_sites += 1
    if not leaks and call_sites < 8:
        errors.append(
            f"只有 {call_sites} 个脚本在调 append_line / append_lines（应 ≥8）——"
            "差集为空是因为**没人再写事件了**，不是因为都走了单源"
        )

    if errors:
        return _fail(name, "; ".join(errors))
    return _ok(name, f"_state_io 为唯一实现：{len(consumers)} 个 hook + check-stale 复用状态读写；"
                     f"{call_sites} 个脚本经 append_line 写事件，"
                     f"另有 {len(strict)} 处已声明的非 events 追加，零漏网"
                     f"（AST 判定，闭包扫描 {len(_shipped_py())} 份 py）")


# 合法 mode 串的形态判据（用于把 `p.open(x)` 里的 `x` 与真正的 mode 区分开）。
# 不用「名单式」枚举全部拼法：`<obj>.open(...)` 的第 1 个位置实参**可能是 mode
# （`Path.open`）也可能是路径（`pdfplumber.open`）**，只能按形态判——
# 长度 ≤4、首字符是 r/w/x/a、字符集不出 mode 字母表。
_MODE_CHARS = frozenset("rwxabt+U")
# 「模块风格」的打开函数：第 1 个位置实参是路径、第 2 个是 mode。
# 对象方法（`Path.open` / 文件对象）则是第 1 个位置实参就是 mode——两者必须分开判，
# 否则 `p.open("a")` 会被当成「路径 = "a"、没给 mode」而放过。
_OPEN_MODULES = frozenset(("io", "codecs", "builtins", "gzip", "bz2", "lzma", "tokenize"))


def _looks_like_mode(s):
    return isinstance(s, str) and 0 < len(s) <= 4 and s[0] in "rwxa" and set(s) <= _MODE_CHARS


def _open_call_modes(tree):
    """AST 里全部「打开文件」的调用 → [(Call 节点, mode, 形态, 是否对象方法)]。

    **全仓判断「某个调用以什么模式打开文件」只有这一份实现**（`closeout-discipline`
    §同一事实的多个表现层）。此前有两份：本闸的前身逐行正则，与 `text_writes_pin_newline`
    的另一条逐行正则——两条各自漏各自的：前者漏跨行 `open(`、变量 mode、`os.open` 别名；
    后者把 docstring 里写着 `mode="a"` 的那行判成一处未钉 newline 的写入（真实假红，
    逼人改文案）。逐行正则看不见括号内的换行，这是同一个成因。

    `mode` 取值：解析出的 mode 串；`None` = 实参在但静态解析不出。**没给 mode 的调用
    不在返回值里**（默认只读）。`os.open(..., O_APPEND)` 没有 mode 串，归一成 `"a"`。

    识别四类：
      1. 内建 `open(path, mode)`；2. 模块风格 `io.open(path, mode)`（含 `import io as _x`
         与 `from io import open as _x`）；3. 对象方法 `p.open(mode)` / 关键字 mode 形态；
      4. 低层 `os.open(path, ... O_APPEND)` 与 `os.fdopen(fd, mode)`——`import os as _x`
         的模块别名、`from os import fdopen` / `from os import open as _oo` 的 from-import
         形态，以及 `flags=` 关键字形态，都在识别面内。

    **能力边界**（写全，不写等于把已知缺口变成暗坑——`closeout-discipline`）：

      - mode 只做同文件内的**单次赋值**常量传播（`_M = "a"` 这一层）；再间接（函数返回值 /
        形参 / 字典取值）时 mode 为 `None`，由各调用方决定怎么处置。
      - `getattr(os, "open")` 这类动态取函数看不见。
      - **O_APPEND 标志的传播只做同文件内的直接赋值**（`_FL = os.O_WRONLY | os.O_APPEND`
        后 `os.open(pth, _FL)` 能认出来）；标志由函数返回值 / 形参 / 字典取值间接得来时
        仍看不见。这一侧取**并集**而非「多次赋不同值即视为解析不出」——**方向与 mode 相反
        是有意的**：mode 解析不出会让调用方按可疑报出（fail-safe 落在「报」那侧），而标志
        这里解析不出就是静默失明（fail-safe 落在「收」那侧），故名字**曾经**持有过 O_APPEND
        就算，宁可多报。
      - 以上三条都是**静默失明**（返回值里没有这个调用），不是报红。消费方拿不到的东西
        无从判断，所以缺口写在这里而不是靠调用方兜。
    """
    os_aliases, module_aliases, module_open_names = {"os"}, set(_OPEN_MODULES), set()
    # `from os import open as _oo` / `from os import fdopen`：绑定到裸名字，没有 owner。
    # 少这两个集合时它们从低层分支整个溜走（都落在 events 漏网面上）。
    os_open_names, os_fdopen_names = set(), set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "os":
                    os_aliases.add(a.asname or "os")
                if a.name in _OPEN_MODULES:
                    module_aliases.add(a.asname or a.name)
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                if a.name == "open" and n.module in _OPEN_MODULES:
                    module_open_names.add(a.asname or "open")
                if n.module == "os" and a.name == "open":
                    os_open_names.add(a.asname or "open")
                if n.module == "os" and a.name == "fdopen":
                    os_fdopen_names.add(a.asname or "fdopen")

    # 同文件内的字符串常量赋值；同名多次赋不同值 → 视为解析不出
    const_str = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = n.value.value if isinstance(n.value, ast.Constant)                 and isinstance(n.value.value, str) else None
            key = n.targets[0].id
            const_str[key] = v if key not in const_str or const_str[key] == v else None

    def _as_str(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return const_str.get(node.id)
        return None

    def _mentions_o_append(node):
        return any(isinstance(n, (ast.Name, ast.Attribute))
                   and (getattr(n, "id", None) or getattr(n, "attr", None)) == "O_APPEND"
                   for n in ast.walk(node))

    # 标志藏在变量里：`_FL = os.O_WRONLY | os.O_APPEND` 后 `os.open(p, _FL)`。
    # 取**并集**而不是像 mode 那样「多次赋不同值即视为解析不出」——方向相反是有意的：
    # mode 解析不出会让调用方按可疑报出（fail-safe 在「报」那侧），而标志这里解析不出
    # 就是静默失明（fail-safe 在「收」那侧）。名字**曾经**持有过 O_APPEND 就算，宁可多报。
    o_append_names = {n.targets[0].id
                      for n in ast.walk(tree)
                      if isinstance(n, ast.Assign) and len(n.targets) == 1
                      and isinstance(n.targets[0], ast.Name) and _mentions_o_append(n.value)}

    out = []
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call)):
        func = call.func
        fname = getattr(func, "attr", None) or getattr(func, "id", None)
        owner = func.value if isinstance(func, ast.Attribute) else None
        owner_id = owner.id if isinstance(owner, ast.Name) else None
        kw_mode = next((k.value for k in call.keywords if k.arg == "mode"), None)
        method_style = False

        bare = isinstance(func, ast.Name)
        is_os_open = (fname == "open" and owner_id in os_aliases) or (bare and fname in os_open_names)
        is_os_fdopen = (fname == "fdopen" and owner_id in os_aliases) or (bare and fname in os_fdopen_names)

        if is_os_open or is_os_fdopen:
            if is_os_open:
                # `flags` 是 positional-or-keyword，只扫 args 会漏 `os.open(p, flags=…O_APPEND)`
                flag_nodes = list(call.args) + [k.value for k in call.keywords]
                hit = any(_mentions_o_append(a) for a in flag_nodes) or any(
                    isinstance(n, ast.Name) and n.id in o_append_names
                    for a in flag_nodes for n in ast.walk(a))
                if hit:
                    out.append((call, "a", "os.open + O_APPEND", False))
                continue
            mode_node = kw_mode or (call.args[1] if len(call.args) > 1 else None)
            style = "os.fdopen(fd, mode)"
        elif fname == "open" or fname in module_open_names:
            if isinstance(func, ast.Name) or owner_id in module_aliases:
                mode_node = kw_mode or (call.args[1] if len(call.args) > 1 else None)
                style = "open(path, mode)"
            else:
                method_style = True
                mode_node = kw_mode or (call.args[0] if call.args else None)
                style = "<obj>.open(mode)"
        else:
            continue

        if mode_node is None:
            continue                       # 没给 mode → 默认只读
        mode = _as_str(mode_node)
        if mode is not None and not _looks_like_mode(mode):
            # 解析出来了但不是 mode 串：`<obj>.open(...)` 的第 1 个位置实参可能是路径
            # （`pdfplumber.open("x.pdf")`），那不是一次带 mode 的打开
            continue
        out.append((call, mode, style, method_style and kw_mode is None))
    return out


def _append_open_calls(tree):
    """以**追加**模式打开文件的调用点 → [(Call 节点, 形态描述)]。

    **为什么不能逐行正则**：`open(` 的实参跨行是格式化器对长行的常规产物，逐行正则一律
    看不见（实测绿）。同一成因下漏网的还有「mode 串来自变量」与「`os.open` 经模块别名调用」。
    枚举与 mode 解析复用 `_open_call_modes`，本函数只做取舍。

    **静态解析不出 mode 时的两种处置，方向相反、都是有意的**：
      - 模块风格（`open(path, <算出来的 mode>)`）**按可疑报出而不是放过**——fail-safe，
        把 mode 写成字面量即可消掉；
      - 对象方法风格的第 1 个位置实参**按「不是 mode」处理**，不报可疑：
        `pdfplumber.open(path)` 与 `events_file.open(m)` 在 AST 上完全同形，一律报可疑会
        造出真实假红（本仓 `extract_assets.py:86` 就是前者），而假红最后会被「修」成永远绿。
        代价是「对象方法 + 算出来的 mode」这一种写法本闸看不见——比跨行 `open(` 刁钻得多，
        且 `mode=` 关键字形态仍然钉得住（关键字形态不走这条豁免）。
    """
    out = []
    for call, mode, style, ambiguous_positional in _open_call_modes(tree):
        if mode is None:
            if not ambiguous_positional:
                out.append((call, f"{style}，mode 实参静态解析不出（按可疑报出）"))
        elif "a" in mode:
            out.append((call, f"{style} mode={mode!r}"))
    return out


def _call_writes_target(tree, call, target):
    """这个追加调用写的是不是 `target`（按**文件名**判，不按整路径）。

    目标的定义点多是 `X / "logs" / "y.log"` 的分段拼接，整路径串在源码里根本不存在。
    先看**承载路径的那个实参**与接收者子树，再回溯同文件里那些变量名的赋值。

    **扫描面只取 `call.args[0]` ＋ 接收者，payload 参数不进（BUG-015）**：原实现遍历
    全部实参子树，于是**任意一个实参**（含 payload）里出现被白名单登记的文件名字面串，
    这次调用就被判成「写的是那个非 events 文件」而放行——哪怕第一个实参明明白白写着
    `events.jsonl`。方向是**假绿**（该拦没拦）。实测：payload 里塞一句
    `"source=inject-log.jsonl"` 即可骗过白名单。
    收窄不造假红：现有三个 owner 的路径**都在第一个位置参数上**（`append_line(path, line)`
    与 `open(path, mode)` 同形），变量回溯那半的 `names` 同样只从 `args[0]` 取。
    """
    def _has(node):
        return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and target in n.value
                   for n in ast.walk(node))

    subtrees = list(call.args[:1])
    if isinstance(call.func, ast.Attribute):
        subtrees.append(call.func.value)
    if any(_has(t) for t in subtrees):
        return True
    names = {n.id for t in subtrees for n in ast.walk(t) if isinstance(n, ast.Name)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in names for t in node.targets) and _has(node.value):
            return True
    return False


def check_setup_state_steps_wired():
    """setup-state 的每一步都得有人真的会去落笔——步骤清单与记账指引是两处手写事实源。

    doctor 把若干步列为必查（清单当前值以 `SETUP_REQUIRED_STEPS` 为准，本闸也从那里读），
    launcher SKILL.md 却只给了 subscribe 一个可照抄的 mark_setup_step 示例——另两步只在
    「必记的四步」那句枚举里各出现一次、没有命令。结果是每个装机项目跑
    `doctor --group install` 都报「初始化未走完」，且没人知道怎么补记，重跑也消不掉。
    只验「step 名在 SKILL.md 里出现过」抓不住这个——当时它确实出现过。所以这条闸验的是
    **有没有可照抄的 mark_setup_step(..., '<step>') 调用**。

    现行分工：scaffold 由 project_scaffold 自动落；SETUP_SELF_RECORDED_STEPS（acceptance）
    由 doctor 自己落；其余每一步必须在 launcher SKILL.md 里有字面示例。

    **能力边界**：`session-start-prep.py` 也会补记 `agents_md`，本闸的扫描面**不含它**，
    判过不纳入：纳入等于放宽——`agents_md` 会改从脚本侧过闸，此后 launcher SKILL.md 丢掉
    那个字面示例也不再报红，而 launcher 流程需要它作为独立的落笔人。那条记步路径本身由
    `check_session_start_no_phantom_setup_state` 按**真实运行结果**守着，不靠字面再扫一遍。
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
    #    项数**走共用件** `_doctor_declared_check_ids("INSTALL_CHECKS")`，不在本函数
    #    另写一份解析——此前这里自己抓一遍，正则与共用件逐字相同但**计数规则不同**
    #    （那边按「行首 `("` 且行内含 `check_`」数）。两份当时同为 11，是「还没漂但
    #    迟早漂」的形态，正是本轮要消掉的那一类。
    install_ids, install_problems = _doctor_declared_check_ids("INSTALL_CHECKS")
    if install_problems:
        errors += install_problems
    else:
        actual = len(install_ids)
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
    if "_STATE_RUNTIME" not in dtext:
        errors.append("doctor 缺 _STATE_RUNTIME 档（gitignore 的运行时文件不能算硬骨架）")
    #    两处都只看引号里的**实际条目**：拿整块做子串匹配会把「为什么不列它」这类
    #    说明性注释当成条目误报（给软骨架补治理目录时踩到）。
    def _list_items(const):
        mm = re.search(rf"{const}\s*=\s*\[(.*?)\]", dtext, re.S)
        return re.findall(r'"([^"]+)"', mm.group(1)) if mm else []

    # 运行态两个目录的**位置**已收进 `_state_io`，所以硬骨架清单里连片段都不该带目录名：
    # 带了就说明有人又把位置写死回来了（`_MEM_REQUIRED` 存的是**相对记忆目录**的片段，
    # 由 `check_skeleton` 接前缀，故不在此判）。
    if any(("workframe-state" in i) or (".workframe/" in i)
           for i in _list_items("SCAFFOLD_REQUIRED_FILES")):
        errors.append("状态目录被列进硬骨架——该目录整个 gitignore，clone 出来必然缺")
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
                  # 两阶段提交：记账由代码统一落盘（--commit）
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
    """写时 A.U.N. 工序不得回退成「直接 append」——判据在注入片、工序在 skill，两侧都验。

    落点随机制迁移：判据那半进 `context/main/20-signal-intake.md`，工序那半进
    `skills/signal-intake/SKILL.md`。**两侧都要验**——只验 skill 的话，判据片被砍成
    「命中了就写」时闸照绿；只验片的话，工序细节（查重 / supersede 落盘动作）丢了也照绿。
    """
    name = "write_time_audn_present"
    shard = CONTEXT_DIR / "main" / "20-signal-intake.md"
    skill = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "signal-intake" / "SKILL.md"
    issues = []
    for label, p in (("判据片 main/20-signal-intake", shard), ("工序 skill signal-intake", skill)):
        if not p.exists():
            issues.append(f"{label} 缺失: {_rel(p)}")
    if issues:
        return _fail(name, "; ".join(issues))
    shard_text = shard.read_text(encoding="utf-8")
    skill_text = skill.read_text(encoding="utf-8")
    # 判据侧：写之前必须先查同主题，等价条目不重复写
    for token in ("同主题", "supersede"):
        if token not in shard_text:
            issues.append(f"判据片缺: {token}")
    # 工序侧：A.U.N. 三步与「同主题合并、异主题追加」的落盘动作
    for token in ("A.U.N.", "检索同主题", "同主题合并、异主题追加", "supersede"):
        if token not in skill_text:
            issues.append(f"工序 skill 缺: {token}")
    lib = (FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "librarian" / "SKILL.md").read_text(encoding="utf-8")
    if "写时" not in lib or "兜底复查" not in lib:
        issues.append("librarian 融合 SOP 缺写时前置注记")
    if issues:
        return _fail(name, "; ".join(issues))
    return _ok(name, "(判据片 2 token + 工序 skill 4 token + librarian 前置注记)")

def check_librarian_placement_has_skill_row():
    """librarian 落点表必须含项目 skill 行 + 四问判据（防回退到二元落点）。

    「三问」→「四问」是必载层裂成四片之后的必然：第一问只判「要不要进必载层」，
    **落哪一片**是第二个独立判断。钉「四问定落点」而不是「三问」——退回三问就是
    退回「必载层只有一个落点」那个已经不成立的世界，而那时表面上仍然是有判据的。
    """
    name = "librarian_placement_has_skill_row"
    text = (FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "librarian" / "SKILL.md").read_text(encoding="utf-8")
    issues = []
    for token in ("**项目 skill**（`.claude/skills/<name>/`）", "四问定落点",
                  "场景触发且成套", "聚类新建", "泄压阀",
                  # 必载层裂成四片之后，落哪一片是独立一问；缺它等于退回二元落点
                  "谁要遵守？", "第 0 步「先算预算」"):
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
    """响应输出片必须明确区分"补充事实/参数（视为确认）"和"否定方向/改写结构（需二次确认）"。"""
    path = CONTEXT_DIR / "main" / "30-response-main.md"
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
    """信号入账工序的端到端示例：确认步必须排在写入步之前。

    落点随机制迁移到 `skills/signal-intake/SKILL.md` §8（判据留在注入片，工序进 skill）。

    **判定式换了形状，不是换了路径**：原断言钉的是一句写死的反模式正则（步骤 3 写 spec、
    步骤 4 确认）。示例重写之后 `spec` 这个词整段消失，那条正则**永远匹配不到**——不可达
    的分支与不存在的分支长得一样，留着它只会让人以为反序仍被盯着。改成按**步骤序号**比
    先后：同一个示例块里，确认步的序号不得大于任何写入步的序号。反序换一种措辞重现时，
    写死正则那条路照绿，本形状照红。

    **能力边界**：判的是**跨步反序**（写在前、确认在后）。同一步里既出现确认措辞又出现
    写入措辞时本闸**放行**——现文步骤 2 正是这一形态（「回显摘要 + 等待确认（P0 必须先于
    写入）」），把它判红就是假红。代价是「一步里既写又确认」这种形态本闸看不见。
    """
    name = "auto_update_p0_example_confirm_before_write"
    path = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "signal-intake" / "SKILL.md"
    if not path.exists():
        return _fail(name, "工序落点缺失: plugins/core/skills/signal-intake/SKILL.md")
    text = path.read_text(encoding="utf-8")

    m_head = re.search(r"^##\s.*端到端示例.*$", text, re.M)
    if not m_head:
        return _fail(name, "找不到「端到端示例」段头——段被改名或删掉时本闸不许静默降级")
    sec_end = re.search(r"^##\s", text[m_head.end():], re.M)
    section = text[m_head.end():m_head.end() + sec_end.start()] if sec_end else text[m_head.end():]

    CONFIRM = ("回显摘要", "等待确认", "先于写入")
    WRITE = ("落盘", "写入", "append", "创建")
    # 示例块 = 以「**<标题>** — 用户：」开头的一段；逐块判，不跨块比序号
    blocks = re.split(r"\n(?=\*\*[^*\n]+\*\*\s*—)", section)
    checked = 0
    for blk in blocks:
        steps = re.findall(r"^(\d+)\.\s+(.*)$", blk, re.M)
        if not steps:
            continue
        confirm_no = next((int(n) for n, body in steps
                           if any(k in body for k in CONFIRM)), None)
        write_no = next((int(n) for n, body in steps
                         if any(k in body for k in WRITE)), None)
        if confirm_no is None or write_no is None:
            continue
        checked += 1
        if confirm_no > write_no:
            title = blk.strip().splitlines()[0][:40]
            return _fail(name, f"示例「{title}」里第 {write_no} 步已经写入，"
                               f"确认却排在第 {confirm_no} 步——P0 的「回显 → 等确认 → 写入」"
                               f"顺序在示例里被演示反了，照抄的人就会先写后确认")
    if checked == 0:
        return _fail(name, "段内没有任何一个示例块同时含确认步与写入步——"
                           "取材为空时的「顺序正确」是空转，判红而非放行")
    return _ok(name, f"({checked} 个示例块的确认步均早于写入步)")

def check_auto_update_no_prompt_eng_skill_edit_claim():
    """信号入账片不应再有"由 @prompt-eng 评估后执行 skill 文件修改"或类似越权语句。"""
    path = CONTEXT_DIR / "main" / "20-signal-intake.md"
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
    """受保护资产注入片必须覆盖运行态整棵、proposals、shared/MEMORY.md 三项。

    落点迁到 `context/both/40-protected-assets.md`，**整片就是那一段**，所以不再在文内找
    段头切片——原来那句 `## 受保护资产约束` 是旧 rule 里的二级段头，在独立成片之后不存在。
    改判 H1 标题在位（片被换成别的内容时不许静默放行），断言面为全片。

    这三项与 `auto_update_list_alignment` 的分工：那条钉的是**两个表现层彼此同值**（片与
    `signoff_tier_check` 的常量），两边一起漂时它绿；本条钉的是**绝对下限**，一起漂也红。
    """
    name = "auto_update_protected_assets_complete"
    path = CONTEXT_DIR / "both" / "40-protected-assets.md"
    if not path.exists():
        return _fail(name, "注入片缺失: context/both/40-protected-assets.md")
    text = path.read_text(encoding="utf-8")
    if not re.search(r"^#\s.*受保护资产", text, re.M):
        return _fail(name, "片里没有「受保护资产」H1 标题——落点被换掉时不许静默放行")

    required_assets = [
        "<state>/**",           # 运行态整棵，记法抽象（目录名只在 _state_io 一处）
        "projects/proposals",
        "shared/MEMORY.md",
    ]
    missing = [a for a in required_assets if a not in text]
    if missing:
        return _fail(name, f"受保护资产片缺: {missing}")
    return _ok(name, f"({len(required_assets)} 项绝对下限在位)")

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

    **两层，缺一不可**：token 清单是存在性断言（对 `SCAFFOLD_TEMPLATES_DIR`、输出文案
    这类「在不在」就是全部语义的对象成立）；三条调用链 + 两个清单常量走
    `_symbol_use_sites` 判「真的在上游被调用 / 被引用」——对函数与清单常量，**定义 ≠
    被调用**，只有存在性断言时把 `main()` 里那句 `ensure_project_scaffold(project_dir)`
    删掉，token 一个不少、闸照样绿，而装机路径整个静默空转。
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
        "gitignore_required_entries(",
        # 写入用清单必须与检测用清单分开：前者要精确成对，后者认等价形态。
        # 共用一个常量会让「接入已有项目」路径追加整目录形态，sidecar 被连坐忽略，
        # 而缺失扫描又判齐全 → 永不自我纠正
        "gitignore_managed_lines(",
        # 成对形态里的 `!` 例外行：目录名由 `_state_io` 现算，这里只钉「例外行还在」
        f'f"!{{state}}/memory-index.json"',
        # 目录名不得写死在 scaffold 里——写死就绕过了 `_state_io` 这个唯一事实源，挪目录那天清单漏改
        "state_dir_rel(",
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

    # 上面那组是**存在性**断言。对 `def x(` / 常量这类代码符号它不成立——定义 ≠ 被调用。
    # 三条调用链与两个清单常量逐个判「真的在上游被用到」，否则 token 全在、闸全绿，而
    # 装机路径实际什么都没做。
    for symbol, caller, why in (
        ("ensure_project_scaffold", "main",
         "装机入口不再调它，骨架目录与运行态文件一个都不会建，后续 task-management / "
         "test-case-design / self-iteration 全部撞目录缺失"),
        ("_ensure_gitignore", "ensure_project_scaffold",
         "运行态文件不再进 .gitignore，events.jsonl 之类会被误提交进用户仓"),
        ("_upgrade_managed_sidecar_rule", "_ensure_gitignore",
         "存量项目 managed block 里的旧整目录写法不再就地升级，缺失扫描判它「已覆盖」，"
         "memory-index sidecar 永远进不了 git"),
    ):
        # mode 默认 any + 无 paired_with：实核分布五条各自在其容器内恰 1 处承重引用，
        # 「有一处真实引用」即等价于「这条链没断」。
        sites, _b, err = _symbol_use_sites(s_text, symbol, func=caller)
        if err:
            return _fail(name, f"project_scaffold.py: {err}（要在它体内找 `{symbol}` 的调用）")
        if not sites:
            return _fail(name, f"`{symbol}` 有定义，但 `{caller}` 里没有一处调用它——{why}")
    # 两张清单是**问 `_state_io` 现算的函数**（不是常量），判据：
    # 它们必须在 `_ensure_gitignore` 体内被真的调用一次，否则清单成了摆设。
    # `_symbol_use_sites` 剥 f-string 插值区的能力仍然必要——`f"…{managed})"`
    # 那句输出文案里的 NAME token 会替真正的写入路径挡箭（PEP 701 之后它是真 token）。
    for const, user, why in (
        ("gitignore_required_entries", "_ensure_gitignore",
         "缺失扫描改读别处的字面量，清单增删不再影响判定"),
        ("gitignore_managed_lines", "_ensure_gitignore",
         "managed block 改写死字面量，写入内容与检测清单从此各自演进而无人发现"),
        ("state_dir_rel", "gitignore_managed_lines",
         "写入清单不再问 _state_io 要目录名，挪目录那天会写出一条管不着新状态目录的规则"),
    ):
        sites, _b, err = _symbol_use_sites(s_text, const, func=user)
        if err:
            return _fail(name, f"project_scaffold.py: {err}（`{const}` 的消费点在它体内）")
        if not sites:
            return _fail(
                name,
                f"`{const}` 有定义，但 `{user}` 里没有一处**承重**引用（输出文案里的"
                f"f-string 插值不算）——清单成了摆设：{why}",
            )

    # 独立可执行：无人值守 / CI 场景直接调它，不再有仓根薄壳兜底
    if '__name__ == "__main__"' not in s_text or "def main(" not in s_text:
        return _fail(name, "project_scaffold.py 不可独立执行（无人值守场景直接调它）")
    return _ok(name)


def check_scaffold_templates_exist():
    """templates/ 必须含 scaffold 落盘依赖的模板（清单即下方 `required`）。

    `dev-log-template.md` 是 `PARAM_DEFAULTS['close_check']['ledger']` 指向的那个文件的
    模板：配置键默认开启而模板不在，装机会写出一个「声明了账本、账本却不存在」的项目，
    首次跑 close-check 直接红。两者必须同增同减。
    """
    name = "scaffold_templates_exist"
    tdir = FRAMEWORK_ROOT / "plugins" / "core" / "templates"
    required = [
        "board-template.yaml",
        "specs-overview-template.md",
        "issues-templates-template.md",
        "project-changelog-template.md",
        "dev-log-template.md",
        "gitignore-template",
    ]
    missing = [n for n in required if not (tdir / n).exists()]
    if missing:
        return _fail(name, f"模板缺失: {missing}")
    return _ok(name, f"({len(required)} templates)")


def check_gitignore_template_has_required_entries():
    """gitignore-template 必须含 §Git 策略约定的 5 个必需条目（含 tmp/.tmp 截图临时区），
    且 memory-index.json 的放行必须写成 `<状态目录>/*` + `!` 例外的成对形态，
    且项目 skills 链接的忽略行恰为整行 `.claude/skills`（无尾斜杠；按项目形态剥不剥由 scaffold 定）。

    成对形态是硬契约，不是风格偏好：git 不会重新纳入被排除父目录下的文件，写成
    `.workframe/state/` 时 `!` 例外被静默无视——已跟踪的项目看不出异样，
    新项目的 sidecar 则直接失踪且零报错。这条闸就是为了挡住"改回整目录写法"。
    """
    name = "gitignore_template_has_required_entries"
    f = (
        FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "gitignore-template"
    )
    if not f.exists():
        return _fail(name, "gitignore-template 不存在")
    text = f.read_text(encoding="utf-8")
    state = _rt("state")          # 目录名问 _state_io，不在本文件抄第二份
    required = [
        ".claude/settings.local.json",
        state + "/",
        "logs/",
        "tmp/",
        ".tmp/",
    ]
    missing = [r for r in required if r not in text]
    if missing:
        return _fail(name, f"gitignore-template 缺必需条目: {missing}")
    # 模板是**出厂形态**，不该再出现迁移前的旧目录名——留着会让新项目 ignore 一个
    # 它根本不会用到的目录，而真正的状态目录裸奔进 git
    legacy = _rt("state", legacy=True)
    if legacy in text:
        return _fail(name, f"gitignore-template 仍含迁移前的旧状态目录 `{legacy}`——"
                           f"新项目的状态在 `{state}/`，旧行管不着它，运行态会直接进 git")
    # 按行精确比对：注释掉的 `# <state>/*` 不算数
    lines = {ln.strip() for ln in text.splitlines()}
    pair = [
        state + "/*",
        f"!{state}/memory-index.json",
    ]
    missing_pair = [p for p in pair if p not in lines]
    if missing_pair:
        return _fail(
            name,
            f"sidecar 放行契约破损，缺行: {missing_pair}"
            f"（必须 `{state}/*` + `!` 成对；写成整目录形态时 git 静默无视 `!`）",
        )
    # 项目 skills 链接的忽略行：**整行相等**、无尾斜杠。不能用上面的子串判——新写法 `.claude/skills` 是旧写法
    # `.claude/skills/` 的前缀，子串判对旧写法同样成立，退回旧写法时这里红不了
    skills_line = ".claude/skills"
    rules = [ln for ln in lines if ln and not ln.startswith("#")]
    skills_rules = [ln for ln in rules if ln.lstrip("/").startswith(skills_line)]
    if skills_rules != [skills_line]:
        return _fail(name, f"项目 skills 链接的忽略行应恰为一行 `{skills_line}`（无尾斜杠），实为 {skills_rules}——"
                           f"带尾斜杠 / `/*` / `/**` 的写法在 macOS / Linux 上盖不住符号链接，新建项目的 "
                           f"`.claude/skills` 会被 `git add -A` 带进提交；缺这一行则 Windows 上 git 把 junction 里的"
                           f"文件再登记一份")
    return _ok(name, f"({len(required)} required entries + sidecar pair + skills link line)")


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
    """主会话片必须含处理顺序段，通用协议片必须含 task_blocked fallback dedup 检查段。"""
    name = "agent_protocols_has_rule_processing_order"
    f = (
        CONTEXT_DIR / "main" / "10-main-protocol.md"
    )
    if not f.exists():
        return _fail(name, "承接片不存在")
    text = f.read_text(encoding="utf-8")
    required_sections = [
        "一条消息触发多条纪律时的处理顺序",
        "task_blocked` fallback",
    ]
    missing = [s for s in required_sections if s not in text]
    if missing:
        return _fail(name, f"主会话片缺章节: {missing}——同一条消息命中多条纪律时没有位序就会双写")
    return _ok(name)


# === Cross-platform launcher / bin mode / .cmd portability ===

# POSIX 入口脚本（无后缀，shebang 执行，git index 必须 100755）。
# **事实源是目录**——本常量只是给别处按名引用，与目录不一致时 `check_bin_git_index_modes` 报红。
BIN_POSIX_SCRIPTS = [
    "workframe-python",
    "workframe-audit-board-drift",
    "workframe-recompute-board-summary",
    "workframe-recompute-skill-metrics",
    "workframe-doctor",
    "workframe-maintenance",
    "workframe-module-close-check",
    "workframe-event",
    "workframe-door",
    "workframe-discipline-mark",
]

# Windows .cmd wrapper（git index 必须 100644），与上表一一配对
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

    **`workframe-python` 的覆盖边界（已知缺口，写明而非留暗坑）**：本闸与
    `check_bin_posix_scripts_exist` / `check_bin_git_index_modes` 三条都是**契约层**
    ——文件在不在、mode 对不对、hooks.json 有没有绕过它。**行为层为零**：恒等对账类探针
    起 hook 一律用 `sys.executable` 直调脚本，**不经这个 launcher**，所以 hooks.json 里那些
    命令在真机上实际走的入口从未被端到端跑过。它又不是 Python（bash），因此也不在
    `_python_sources` 的四道闸视野内。今天这个缺口无代价（该文件与其 `.cmd` 兄弟在
    TASK-086 三版对照下逐字节相同 ⇒ 没跑到的那层与跑到的那层等价），但**等价靠的是
    「它没变」，不是「它被验过」**——它一旦被改，现有任何一道闸都不会告诉你行为变了。

    **迭代面 = 两份清单 × 两个命令字段**（`_hook_field_entries`）：`commandWindows` 上写 `py -3`
    或写死解释器绝对路径，与 `command` 上写死 `python` 是同一个失效（发行版 / 用户机器上找不到
    那个解释器，hook 整条静默不跑）。
    """
    name = "hooks_use_workframe_python_launcher"
    try:
        rows = [(door, *r) for door in HOOK_MANIFEST_RELS for r in _hook_field_entries(door)]
    except Exception as e:
        return _fail(name, f"hook 清单读取/解析失败: {e}")
    if len(rows) < 10:
        return _fail(name, f"两份清单只解析出 {len(rows)} 个命令字段（应 ≥10）——清单被清空时本闸假绿")

    bare_python_pattern = re.compile(r'(?:^|\s)python\s+"\$\{CLAUDE_PLUGIN_ROOT\}')
    bare_py_pattern = re.compile(r'(?:^|\s|&\s*)py\s+-3\b')
    launcher_pattern = re.compile(r'\$\{CLAUDE_PLUGIN_ROOT\}/bin/workframe-python')

    offenders = []
    missing_launcher = []
    for door, event, matcher, field, cmd in rows:
        where = f"[{door}] {event}({matcher or '*'}).{field}"
        if bare_python_pattern.search(cmd) or bare_py_pattern.search(cmd):
            offenders.append(f"{where}: {cmd}")
        if not launcher_pattern.search(cmd):
            missing_launcher.append(f"{where}: {cmd}")

    if offenders:
        return _fail(name, f"hooks 仍写死解释器（python / py -3）——发行版只有 python3 或用户机器"
                           f"没有 py 启动器时该条命令找不到解释器、静默不跑: {offenders[:4]}")
    if missing_launcher:
        return _fail(name, f"hooks 未走 workframe-python launcher: {missing_launcher[:4]}")
    return _ok(name, f"({len(rows)} 个命令字段全部经 launcher，两份清单、两个字段)")


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
    """ensure_project_scaffold 必须为每个 baseline core role 创建 agent-memory 骨架。

    历史修复：之前只创建 shared/，依赖 Write 工具自动 mkdir parents 的隐式契约。
    改为 scaffold 阶段强制预创建各 role 的 MEMORY.md + notes.md。

    本检查只管**结构**（枚举常量在、占位符生成函数在、遍历写法在）。枚举的**内容**
    由 check_role_enum_single_source 对着 agents/ 目录精确对账——原先在下面列
    `"pm"` / `"dev"` 等字面 token 是弱断言（全文 in 检查，两字符串在文件任何角落
    命中都算过），且构成又一份手写角色名副本。
    """
    name = "scaffold_creates_baseline_role_memory"
    scaffold_py = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    if not scaffold_py.exists():
        return _fail(name, "plugins/core/scripts/project_scaffold.py 不存在")
    text = scaffold_py.read_text(encoding="utf-8")
    required = [
        "BASELINE_CORE_ROLES",
        "_role_memory_placeholder",
        "_role_notes_placeholder",
        "for role in BASELINE_CORE_ROLES",
    ]
    missing = [t for t in required if t not in text]
    if missing:
        return _fail(name, f"project_scaffold.py 缺关键 token: {missing}")
    return _ok(name)


def check_role_enum_single_source():
    """agents/ 目录是 core baseline 角色集的唯一事实源，下游枚举必须跟随。

    盲区背景：改 agents/ 只会让 check_agents_count_and_frontmatter 变红，而那道断言
    比的是它自己手写的 expected——改完它就绿了，下游没跟上时没有任何闸会说话。
    pmo 退役那轮是靠退役词闸一次性兜住的（它只认 `pmo` 字面），常态无闸。

    分层判据（== 还是 ⊆ 由该处**语义**决定，不一刀切）——这里只剩 == 这一档：
      == 驱动遍历 / 要求列全名录的，漏一个是真实功能缺陷
        · scaffold BASELINE_CORE_ROLES —— `for role in ...` 建记忆骨架，漏一个即少建
        · doctor BASELINE_ROLES —— 同样驱动遍历（CLAUDE.md 旧副本识别的角色表锚点也从它现算）
        · 主会话注入片 `main/10` 的通用角色表 —— 每会话投给主会话，它就是路由名录
      子 agent 那侧唯一的名录是 `both/20` 那句「出厂 4 角色」，由 `role_roster_wording_matches_agents`
      经 `ROSTER_BEARING_FILES` 看着；两处投递面不同（`main/10` 只进主会话），各有一道闸。

    提取不到一律判失败、不静默跳过：被解析的源码形态变了时静默跳过，会让本闸悄悄退化
    成恒绿的假闸。函数签名或字典写法重构会让本闸红——这是有意的，改完顺手更新正则即可。
    """
    name = "role_enum_single_source"
    baseline = _baseline_roles()
    if not baseline:
        return _fail(name, "agents/ 目录空或缺失——角色对账基准丢失")

    doctor_py = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    scaffold_py = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
    main_md = CONTEXT_DIR / "main" / "10-main-protocol.md"
    for p in (doctor_py, scaffold_py, main_md):
        if not p.exists():
            return _fail(name, f"对账源缺失: {p.relative_to(FRAMEWORK_ROOT)}")

    errors = []

    def roles_in(s):
        # `@角色名` 这类中文占位不会被 [a-z] 起头的字符类命中
        return set(re.findall(r"@([a-z][a-z-]*)", s))

    # —— 1/2：两处驱动遍历的枚举常量（顶层赋值，形态稳定，同 config_enum_single_source 手法）
    for label, path, const in (
        ("project_scaffold", scaffold_py, "BASELINE_CORE_ROLES"),
        ("workframe_doctor", doctor_py, "BASELINE_ROLES"),
    ):
        m = re.search(rf"^{const}\s*=\s*[\[(]([^\])]*)[\])]",
                      path.read_text(encoding="utf-8"), re.M)
        if not m:
            errors.append(f"{label}.{const} 未提取到——形态变了，本闸失去对账能力")
            continue
        got = set(re.findall(r'"([^"]+)"', m.group(1)))
        if got != baseline:
            errors.append(f"{label}.{const} 与 agents/ 不一致: "
                          f"少 {sorted(baseline - got)}、多 {sorted(got - baseline)}")

    doc = doctor_py.read_text(encoding="utf-8")     # 9a 对 doctor 的 role_profile 枚举还要用

    # 组号不连续是有意的：别处按「9a」这类编号引用后面几组，不重排。
    # —— 6：主会话注入片 main/10 的通用角色表（每会话投给主会话的路由名录）——
    # 段按 `## 通用角色与路由` 圈（止于下一个 `## `），表行按「首格恰为 @<角色>」抽
    m_sec = re.search(r"^##\s*通用角色与路由\n(.*?)(?=^##\s|\Z)",
                      main_md.read_text(encoding="utf-8"), re.S | re.M)
    if not m_sec:
        errors.append("context/main/10 通用角色段未提取到——段标题变了，主会话路由名录脱离对账")
    else:
        got = set(re.findall(r"^\|\s*@([a-z][a-z-]*)\s*\|", m_sec.group(1), re.M))
        if got != baseline:
            errors.append(f"context/main/10 通用角色表与 agents/ 不一致: "
                          f"少 {sorted(baseline - got)}、多 {sorted(got - baseline)}"
                          f"——主会话按这张表派活，少一行就少一个可调度的角色")

    # —— 7/8/9：role-profile-catalog 的角色名录（每 profile 两处）+ profile 名单对账 ——
    profiles = []
    catalog_md = FRAMEWORK_ROOT / "plugins" / "core" / "reference" / "role-profile-catalog.md"
    if not catalog_md.exists():
        errors.append("role-profile-catalog.md 缺失——profile 侧名录失去对账基准")
    else:
        cat = catalog_md.read_text(encoding="utf-8")
        # 候选枚举走 `_catalog_profiles` 宽探测器（宽严分工与字符集口径见其 docstring），
        # 再逐个用消费方判定式求证——本函数与 role_profile_lead_copies 共用那一份，别各抄一份
        # 去重：同名章节重复时 `catalog_section` 会抛，去重防同一条错误被记两遍
        profiles = list(dict.fromkeys(_catalog_profiles(cat)))
        if not profiles:
            errors.append("catalog 未提取到任何 ### `<profile>` 章节——标题形态变了")
        # 切章与取块一律调 scaffold 自己的判定式（catalog_section / routing_block），闸不另写
        # 正则：闸比消费方宽（BUG-002：标题行尾空格，两闸绿而 preflight 炸）或盲目镜像其实现
        # （把 bug 一起抄，两边同错而闸报绿）两个方向都实证出过洞，正解是同一判定式。
        try:
            sc = _scaffold()
        except Exception as e:
            errors.append(f"import project_scaffold 失败（catalog 解析的唯一实现在它那里）: {e}")
            sc = None
        for prof in (profiles if sc else []):
            # 本函数是**错误累加型**：`errors` 跨各组断言累积、末尾一次性 _fail。
            # 若让 `catalog_section` 的 ParamError（同名章节重复）直接冒到 runner，
            # runner 虽会把它降级为本项失败，但**同轮已累加的其他错误全部丢失**。
            # 另两个调用点（catalog_exists / lead_copies）不累加，故不包、由 runner 接。
            try:
                sec = sc.catalog_section(cat, prof)
            except sc.ParamError as e:
                errors.append(f"{e}——scaffold 渲染 / preflight 抛同一 ParamError，装机当场失败")
                continue
            if sec is None:
                errors.append(
                    f"catalog {prof} 宽探测有标题、消费方判定式却切不出章节：标题行闭合反引号后"
                    f"须紧跟换行（行尾空格 / 中文后缀 / HTML 注释锚都不行），且不能只出现在代码"
                    f"围栏内——scaffold 的 extract_role_profile_routing 会抛 ParamError，"
                    f"装机 preflight 当场失败")
                continue
            # 7：优先级表三列并集 == 名录。**表头做冻结断言**——用 == 的合法性全部来自
            # 「三列是完整划分」，而该语义只由「几乎不用」这个兜底桶承载；表头一变（比如
            # 改成两列）== 就可能不再成立，必须让人回来重审，而不是长期红着或被弱化成 ⊆。
            try:
                hdr_cells, row_cells = priority_table(sec, prof)   # 围栏外恰 1 张，与 lead_copies 共用
            except ValueError as e:
                errors.append(str(e))
            else:
                hdr = tuple(hdr_cells)
                if hdr != ("主力", "按需", "几乎不用"):
                    errors.append(f"catalog {prof} 优先级表头变为 {hdr}"
                                  f"——三列完整划分语义须重审，== 断言可能不再成立")
                else:
                    # 直接收割 token，**不 split 分隔符**：仓内实际已存在 ` / ` 与 `/` 两种
                    # 写法，空桶占位也有 `—`/`-`/`无` 多种，逐一枚举必漏。收割法对两者无感。
                    # 代价写进标准（别靠弱化正则消红）：格内只允许角色名与非拉丁占位符，
                    # 任何小写拉丁词都会被判成越界角色名。
                    got = set(re.findall(r"[a-z][a-z-]*", " ".join(row_cells)))
                    if got != baseline:
                        errors.append(f"catalog {prof} 优先级表与 agents/ 不一致: "
                                      f"少 {sorted(baseline - got)}、多 {sorted(got - baseline)}")
            # 8：渲染块 @角色 == 名录，且块数**恰为 1**——块数由 scaffold 的 routing_block 判定
            # （0 个 / ≥2 个抛措辞不同的 ParamError；scaffold 渲染时走的也是它，≥2 个不再取
            # 第一个）。此前闸与消费方各写一套时，「章节里多出一个更靠前的 markdown 块」是
            # 消费方静默渲染错块、只有仓侧闸拦得住的场景；现在两边同一判定式，用户侧同红。
            try:
                block = sc.routing_block(sec, prof)
            except sc.ParamError as e:
                # 两种失败模式的措辞由 routing_block 分开给出，这里只补消费方会怎么炸
                errors.append(f"catalog {e}——scaffold 渲染 / preflight 抛同一 ParamError，装机当场失败")
            else:
                got = roles_in(block)
                if got != baseline:
                    errors.append(f"catalog {prof} 路由渲染块与 agents/ 不一致: "
                                  f"少 {sorted(baseline - got)}、多 {sorted(got - baseline)}")
            # 文件路径层实调：纯函数在切片上绿，不等于消费方从真实文件读出来也绿——
            # 守的是 PLUGIN_ROOT 解析、读取模式（universal newlines）与薄壳装配三样
            try:
                sc.extract_role_profile_routing(prof)
            except sc.ParamError as e:
                errors.append(f"消费方实调 extract_role_profile_routing({prof!r}) 抛 ParamError: {e}")
        # 9a：profile 名单现算 == doctor 枚举。少了这条，上面整套章节发现能力会静默失效
        # ——`### `solo-pm`` 掉个反引号，该 profile 根本不进循环，7/8 照常全绿、整档脱闸。
        # 危害可证：doctor 对非法 role_profile 报的是 error 不是 warn，catalog 新增档位
        # 而 doctor 枚举没跟时，一个完全正确的项目会被体检报错。
        m_dp = re.search(r'"role_profile":\s*\(([^)]*)\)', doc)
        if not m_dp:
            errors.append("doctor CONFIG_FIELD_ENUM['role_profile'] 未提取到")
        elif profiles:
            dp = set(re.findall(r'"([^"]+)"', m_dp.group(1)))
            if dp != set(profiles):
                errors.append(f"doctor role_profile 枚举与 catalog 章节不一致: "
                              f"少 {sorted(set(profiles) - dp)}、多 {sorted(dp - set(profiles))}"
                              f"（或 catalog 标题形态坏了：少反引号 / 标题级别变化）")
        # 9b：scaffold 的默认档必须是 catalog 里真实存在的 profile，否则 catalog 一改档位名，
        # 每次建新项目的 preflight（extract_role_profile_routing）都会抛 ParamError。
        m_sd = re.search(r'"role_profile":\s*"([^"]+)"',
                         scaffold_py.read_text(encoding="utf-8"))
        if not m_sd:
            errors.append("scaffold PARAM_DEFAULTS['role_profile'] 未提取到")
        elif profiles and m_sd.group(1) not in profiles:
            errors.append(f"scaffold 默认 profile {m_sd.group(1)!r} 不在 catalog 名单 {profiles} 内")

    if errors:
        return _fail(name, "; ".join(errors[:4]))
    return _ok(name, f"(agents={len(baseline)} 名录；下游 3 处〔scaffold / doctor 枚举 + main/10 角色表〕+ "
                     f"{len(profiles)} profile×2 处〔切章/取块复用 scaffold 判定式〕+ profile 名单 2 处对齐)")


def check_scaffold_workframe_config_merge_mode():
    """write_workframe_config 必须 merge 模式 + 损坏检测（不破坏用户文件）。

    历史修复两轮：先是无条件 write_text 覆盖；后又发现损坏 / 非 dict 时也不能覆盖，
    必须返回 warning 让调用方处理，用户先手工修 JSON。
    2026-08-10 install.py 退役：原「install.py main() 收 warning 进 pending_items」那半段
    随之移除，改为断言 warning 确实被返回给调用方（scaffold main 打印并影响退出码）。

    退出码那行的字面随 `--write-subscription` 扩了一项：订阅声明（受保护资产）写不成时
    同样必须非零退出——写成功与写失败都 exit 0 的话，launcher 会带着一个没订阅的项目
    往下走，而那正是 doctor 要到落盘验收才报的那条 error。**两个条件都在字面里**，
    任一被摘掉即红。
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
    if "config_warning" not in text or \
            "return 1 if (config_warning or not subscription_ok) else 0" not in text:
        return _fail(name, "scaffold main() 未消费 config warning 或订阅写入结果"
                           "（两者任一失败都应非零退出）")
    return _ok(name)


def check_agent_protocols_step2_has_init_fallback():
    """通用协议片必须含 Step 2 空骨架兜底 + 主会话直做记账语义。

    M1 P0 修复：为项目级新增 role（手工创建场景）的 agent-memory 兜底。

    2026-08-16 增 `role` 取值锚点：主 Claude 直做时事件 role 填 `main`。这句被删则直做
    工作不进事件流——skill 使用率与问题信号只反映委派出去的那部分，self-iteration 的
    判断依据静默失真，且**没有任何报错**。属收口检查 #8 所说的病根类型。
    """
    name = "agent_protocols_step2_has_init_fallback"
    f = CONTEXT_DIR / "both" / "20-protocol-common.md"
    if not f.exists():
        return _fail(name, "承接片不存在")
    text = f.read_text(encoding="utf-8")
    # 关键词组合：必须同时含"不存在"+"骨架"+"项目级新增 role"
    required = [
        "Step 2",
        "目录/文件不存在的兜底",
        "先创建空骨架",
        "项目级新增角色",
        # main-led 直做记账：填 main 这个取值本身是契约，措辞可改、取值不可改
        "**主会话直做时填 `main`**",
    ]
    missing = [t for t in required if t not in text]
    if missing:
        return _fail(name, f"通用协议片 Step 2 缺兜底语义: {missing}")
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
    scripts = set()
    try:
        for door in HOOK_MANIFEST_RELS:
            for _event, _matcher, _field, cmd in _hook_field_entries(door):
                for m in re.finditer(r"scripts/([A-Za-z0-9_.-]+\.py)", cmd):
                    scripts.add(m.group(1))
    except Exception as e:
        return _fail(name, f"hook 清单读取/解析失败: {e}")
    if not scripts:
        return _fail(name, "未能从两份 hook 清单解析出任何脚本名")

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
    2026-08-10 实测发现 core 侧有 8 处引用 `dev-docs/`，其中一条在 dogfood 项目实测
    已是死链，`activity-state-template.json` 那条更会被 scaffold 复制进**每个用户项目**。

    扫描面 = 会离开框架仓到达用户侧的四类：
      - `templates/` → 被 project_scaffold 渲染成用户项目文件
      - `skills/`    → 随插件安装到用户机器的插件缓存
      - `context/`   → 随插件装到用户机器，且被打包器投进**每会话必载面**。
        **爆炸半径比前三类都大**：前三类还要用户去打开或渲染才生效，注入片是每次
        会话自动进上下文。当前 9 份源实测零违规，属「当前无对象、将来会有」——
        早一批接进守卫是纯赚，等它长出第一处再补就是在补债。

    禁止的是**仓库结构路径**（用户侧不存在）：`dev-docs/`（已剥离为私有仓）、
    `plugins/core/`（用户侧插件根不长这样）、`tools/`（仓根工具，不随插件分发）。
    插件内相对引用（`.workframe-meta/` / `templates/` 等）不在此列。
    """
    name = "core_assets_no_repo_internal_path"
    base = FRAMEWORK_ROOT / "plugins" / "core"
    scan_dirs = [base / "templates", base / "skills", base / "context"]
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
    # **缺一格要响亮失败，不许 `continue`。** 原写法在目录消失时静默少扫，而下方 OK 文案
    # 报的是 `len(scan_dirs)`（清单长度，不是实扫数）——少扫与扫全在终端里长得一模一样。
    missing = [d.relative_to(FRAMEWORK_ROOT).as_posix() for d in scan_dirs if not d.is_dir()]
    if missing:
        return _fail(name, f"出货资产扫描面缺 {missing}——目录消失时的「零违规」是空转，"
                           f"判红而非放行；确实退役了就把它从 scan_dirs 里一并删掉")
    for d in scan_dirs:
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
    # 清单与 `docs/` 下的实际篇目一一对应——新增一篇用户文档必须同步加进来，
    # 否则插件资产可以指向它而无人拦（本闸靠字面量清单，不扫目录）。
    pat = re.compile(
        r"docs/(?:quickstart|setup-guide|concepts|onboarding|context-injection|one-round"
        r"|README)\.md")
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


# 「项目 skills 目录**两个候选路径都要写出来**」这条口径活在哪些随插件分发的文件里。
# 路径相对 `plugins/core/`。**这是一份按「最可能被照着去取件」排序的发现式清单，不是
# 「满足某条判据的全集」**——它没有可反推的入选判据，所以拿任何一条语义判据去扫全仓、
# 再按差额报「清单漏了 N 处」都是误用；缺口是已知且被判为可接受的，登记在下面。
# 已知在清单外、同样写了双候选路径的至少 6 处（2026-09-22 独立验证方全仓扫描所得）：
#   agents/pm.md
#   reference/skill-customization-guide.md
#   reference/role-customization-guide.md
#   skills/requirement-analysis/SKILL.md
#   skills/requirement-archiving/SKILL.md
#   skills/obsidian-safe-write/SKILL.md
# 它们优先级更低而有意不入闸：都是旁述一句「项目 PRD 框架在那个目录下」，读者不会照着
# 它去取件，漂了的后果是措辞不准，而不是取不到文件。同族措辞在 doctor docstring 等处
# 也还有，同理不入闸；上面这 6 条也只是当次扫描所见，不保证是清单外的全部。
# 要扩清单是可以的（把某处判为「会被照着取件」即可加进来），但**不扩也不是缺陷**——
# 这份清单的完备性从来不由判据保证。
PRD_STYLE_DUAL_PATH_FILES = (
    "skills/prd-writer/SKILL.md",
    "skills/prd-writer/diagram-guide.md",
    "skills/prd-writer/html-prototype.md",
    "skills/prd-writer/writing-guide.md",
    "templates/claude-md-template.md",
    "templates/modules-template/requirement/main/prd.md",
)


def check_prd_style_template_contract():
    """prd-style 项目框架模板契约（PRD 框架四层拆分的 ④ 层出厂件）。

    1. 模板存在且 frontmatter 有 name=prd-style——它是 scaffold 装机实例化与
       prd-writer fallback 的取件路径；缺文件或改名即两端同时断线
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
    # 5. prd-writer 取件路径的口径：项目框架在「项目 skills 目录」下的 `prd-style/SKILL.md`，而
    #    该目录按存在性解析（`.agents/skills/` 在则用它，否则 `.claude/skills/`——`_harness.project_skills_dir`
    #    同一规则）。SKILL 正文必须把两个候选都写出来：只写 `.claude/skills/` 会让新建项目（真实源在
    #    `.agents/skills/`、`.claude/skills` 只是链接）在链接失效 / 非 Windows 平台上取不到项目框架、
    #    静默退到默认模板；只写 `.agents/skills/` 则已装项目（真目录）永远 fallback。
    for ref in ("prd-style/SKILL.md", ".agents/skills/", ".claude/skills/",
                "templates/project-skills/prd-style/SKILL.md"):
        if ref not in wtext:
            return _fail(name, f"prd-writer SKILL 缺引用 {ref}（项目框架读取/fallback 断线：项目 skills 目录"
                               f"按存在性解析，两个候选路径都要写出来）")
    if "project_skills_dir" not in wtext:
        return _fail(name, "prd-writer SKILL 未指明「项目 skills 目录」的解析规则来源（_harness.project_skills_dir）"
                           "——两处各写一份规则必漂")
    # 6. **同一口径还活在另外五份随插件分发的文件里**（清单见 `PRD_STYLE_DUAL_PATH_FILES`）：
    #    两份出厂模板（一份渲染进每个新装项目的 `CLAUDE.md`、一份渲染进每份 PRD）与 prd-writer 的
    #    三份 reference 兄弟文件。失效形态与上面那条完全相同（链接建不成 / 非 Windows 平台上只写
    #    一个候选 ⇒ 读者按它去找、找不到、静默退回默认），只是模板那两份发生在**用户项目里**。
    #    **只要求两个候选路径字面都在**，不要求每份都复述解析规则的来源：那个规则由 prd-writer 的
    #    工序执行（上面第 5 条已钉住 `SKILL.md` 必须写明来源），别的文件只是告诉读者去哪儿找，
    #    把规则来源抄进每份 PRD 是噪音。
    #    **文件不提 prd-style 就跳过**（一份文件可以合法地不再引用项目框架），但**跳过是可见的**：
    #    ok 文案里打出「清单 N 份 / 其中 M 份带该口径」，M 掉下来时读输出的人看得见。
    #    **能力边界**：本条只证「两个候选目录的字面都出现在这份文件里」，**证不了它们就写在讲
    #    prd-style 的那一句旁边**——一份长文件里两个字面各自服务于别的话题时它同样绿。
    #    文件短、口径集中时这个差可忽略；清单里将来若进来长文件，判据要跟着收紧。
    checked = 0
    for rel in PRD_STYLE_DUAL_PATH_FILES:
        tp = FRAMEWORK_ROOT / "plugins" / "core" / rel
        if not tp.is_file():
            return _fail(name, f"清单里的文件不存在: {_rel(tp)}——改名 / 搬家之后要同批改 "
                               f"`PRD_STYLE_DUAL_PATH_FILES`，否则这道闸从此少扫一份")
        ttext = tp.read_text(encoding="utf-8")
        if "prd-style" not in ttext:
            continue
        checked += 1
        # 判据是**两个候选目录的字面都在这份文件里**，不锁死拼法：合起来写
        # （`.agents/skills/prd-style/`）与拆开写（先说目录候选、再说 `prd-style/`，
        # `prd-writer/SKILL.md` 就是后者，而且拆开写更好——它把解析规则也讲出来了）都算数。
        # 与上面第 5 条用的是**同一组字面**，两处口径只剩一份。
        missing = [c for c in (".agents/skills/", ".claude/skills/") if c not in ttext]
        if missing:
            return _fail(name, f"{_rel(tp)} 提到了 prd-style 却只写了一个候选路径（缺 {missing}）"
                               f"——读者照着单一路径去找：新建项目的真实源在 `.agents/skills/`"
                               f"（`.claude/skills` 只是链接，链接建不成或非 Windows 平台上取不到），"
                               f"已装项目才是 `.claude/skills/` 真目录。少一个候选就有一类项目**静默**"
                               f"读不到项目 PRD 框架、退回默认模板")
    if checked == 0:
        return _fail(name, f"清单 {len(PRD_STYLE_DUAL_PATH_FILES)} 份文件里一份都不提 prd-style"
                           f"——本断言没有对象等于恒绿")
    return _ok(name, f"(frontmatter name + {len(found)} 个占位符合规 + "
                     f"{len(anchors)} 个结构锚点在位 + scaffold/prd-writer 两端接线 + "
                     f"项目 skills 目录双候选：清单 {len(PRD_STYLE_DUAL_PATH_FILES)} 份、"
                     f"其中 {checked} 份带该口径)")


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
            for field in ("name", "description", "allowed-tools", "user-invocable"):
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

    这条链的失效方式极隐蔽：接入已有项目后文件在、hooks 在、agent 能路由，但 CLAUDE.md
    没导入 AGENTS.md（项目内容在 CC 里一行都读不到），而「文件存在」的检查发现不了。三处
    分别是——规范本体、launcher 在 B 路径与执行步骤里引用它、doctor 查导入行与旧副本。
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
        errors.append("doctor 无 check_claude_md（整合漏写 `@AGENTS.md` 导入没人报）")
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
        # 装机链路开始写受保护资产之后补的三条（TASK-140）。必载注入片把「写入前由确认页对
        # 『会被修改的已存在文件』逐个点名」写成了这条豁免的**授权前提**——前提落在 launcher
        # 这一侧，而它只是几句散文，没有任何机器面。独立验证方实测过它落空的形态：
        # 枚举里只有 CLAUDE.md 与 .gitignore 两类，模型照着执行点名出来的就是两类，
        # 于是「框架对用户的承诺」与「框架实际做的事」不一致，且全绿。
        # **这三条只证「话在不在」，证不了模型真的照做了**——真机那一半在发版检查单第 7 组。
        "确认页枚举含项目 settings": "已存在的 `.claude/settings.json`",
        "用户级注册表单独点名": "还有一处写在项目外，同样必须点名",
        # 确认页的文件面改成「跑一条命令、逐字贴输出」之后补的两条（TASK-151）。真机实证：
        # 上面那三条只证「话在文件里」，而真机那一页上用户级注册表那一行根本没出现——
        # 话在文件里、页面上没有，是同一条缺口的两半。这两条守的是新链路的两个端点：
        # 确认页前面那条命令在不在，以及 Codex 门的触发口径有没有退回「单问一层」。
        # **它们同样只证「话在不在」**，证不了模型真的跑了、真的贴了。
        "确认页前先跑只读预演": "--print-plan",
        "Codex 门不再单问一层": "不再单问一层",
        # 第四格（TASK-158）。此前 §3 把已存在的 `CLAUDE.md` 归进「脚本算得到的」那几类，
        # 而脚本对它一律跳过、合并稿由模型在 §5 步骤 1.5 写回——**一句假陈述**，
        # 真机上那一格只打「另有 1 个文件已存在」，连名字都没有。脚本侧已改成逐项点名，
        # 这两条守的是文案的两半：这一格在不在，以及「写回是调用方的事」有没有被写平成
        # 「脚本会覆盖它」。**同样只证「话在不在」**，证不了模型贴没贴。
        "确认页第四格逐项点名脚本不覆盖的文件": "`已存在·本脚本不覆盖`",
        "第四格的合并稿写回归调用方": "改写的人是你",
        # **登记一条已知缺口（本表全是正向 `token in text`，没有负向断言的位置）**：
        # `proposal-page.md` 里那句已成假的「唯一会改写用户既有文件」是被**改掉**的，
        # 而上面这几条守的都是「新话在不在」，**没有任何一条防那句旧话回来**。
        # 要补的话是一条 `"唯一会改写用户既有文件" not in ptext` 的负向断言——
        # 它会把本表从纯正向变成正负混合，那是本表形态的改动，留给专门的一轮。
    }
    page = LAUNCHER_DIR / "skills" / "setup" / "reference" / "proposal-page.md"
    if page.exists():
        ptext = page.read_text(encoding="utf-8")
        for desc, token in (("确认页禁 HTML 标签", "禁止任何 HTML 标签"),
                            ("确认页一格一件事", "一个单元格只放一件事"),
                            ("动作清单含 git init 可选项", "`git init` 是可勾选项"),
                            # 同上（TASK-140）：动作清单是用户级注册表**唯一的露面机会**，
                            # 它不是项目级文件、够不到「只拍项目级三件事」那个框架。
                            # TASK-151 把这一条的锚句**改写**了（原句是「『订阅 core』这一步要
                            # 单独展开成两行」，现在那两行的身份变成「脚本输出必须包含的内容」），
                            # 锚点随之改到那段里唯一没变的那句；要求本身一条没少。
                            ("动作清单把订阅两处写入都点到名", "动作清单是它唯一的露面机会"),
                            # 文件面的来源从「凭记忆复述」换成「跑一条命令、逐字贴输出」（TASK-151）
                            ("动作清单的文件面取自脚本输出", "文件面取自脚本输出，逐字贴，不手写"),
                            # 「免除『要不要装 Codex 门』那层确认、改为直接装」这条拍板落地
                            # 之后（TASK-151），本页成了这批 hook 信任唯一的事前授权点——
                            # 那一行与它的披露都不能省
                            ("动作清单单列 Codex 门那一行", "装 Codex 门这一步同样单独列一行"),
                            ("Codex 门那一行披露脱离沙盒", "受信 hook 脱离沙盒执行"),
                            ("确认页不写死 hook 条数", "别在本页写死 hook 条数"),
                            # 第四格那一条（TASK-158）：本页要贴的是四格，且第四格里
                            # `CLAUDE.md` / `AGENTS.md` 的合并稿写回**不在脚本射程内**，
                            # 要模型自己接一句。少了这句，第四格会被读成「脚本会处理它们」。
                            ("动作清单贴四格", "`已存在·本脚本不覆盖`"),
                            ("第四格的写回由模型自己补一句", "由你写回")):
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


def check_skill_count_consistent():
    """全仓「N skills / N 个 skills」（数字紧邻 skills 的总数形态）必须等于 skills/ 目录
    实际个数（正向断言）。

    2026-08-20 第 10 批摸排：总数手写点 7 处（marketplace ×2 / core plugin.json /
    README / docs/concepts / docs/setup-guide / skill-customization-guide）全部无正向闸
    ——加一个 skill 七处全漂零报警；此前只有黑名单（release_consistency 查 15 domain 等
    旧值）。分组明细（「13 domain」等，数字不紧邻 skills）无机器可读分组源，暂靠人工。
    CHANGELOG 历史账豁免；含『』的行按引述豁免（引号内是被谈论的旧内容，非现状断言）。
    """
    name = "skill_count_consistent"
    n = len([d for d in (FRAMEWORK_ROOT / "plugins" / "core" / "skills").iterdir() if d.is_dir()])
    if n == 0:
        return _fail(name, "skills/ 目录为空（现算基准缺失）")
    targets = [FRAMEWORK_ROOT / "README.md", FRAMEWORK_ROOT / ".claude-plugin" / "marketplace.json"]
    targets += sorted((FRAMEWORK_ROOT / "docs").glob("*.md"))
    targets += sorted((FRAMEWORK_ROOT / "plugins").rglob("*.md"))
    targets += sorted((FRAMEWORK_ROOT / "plugins").rglob("plugin.json"))
    pat = re.compile(r"(\d+)\s*(?:个\s*)?skills\b", re.I)
    offenders = []
    for p in targets:
        if p.name == "CHANGELOG.md" or not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in pat.finditer(line):
                # 引述豁免收到**片段级**（与 role_roster_wording 同一 helper）：整行豁免时
                # 「旧写法『36 skills』已废弃，现在是 99 skills」整行跳过，真错被遮蔽
                if _in_quote_span(line, m.start(), m.end()):
                    continue
                if int(m.group(1)) != n:
                    offenders.append(f"{p.relative_to(FRAMEWORK_ROOT)}:{i} 写 {m.group(1)} skills（实际 {n}）")
    if offenders:
        return _fail(name, "; ".join(offenders[:5]))
    return _ok(name, f"(skills/ 实际 {n} 个，全仓总数表述一致)")


def check_hook_stage_count_consistent():
    """全仓「N 段 hook / N-stage hook」必须等于 hooks.json 的实际段数（正向断言）。

    取代「枚举历史错值」式的防漏。后者的结构性缺陷是**每升一版都要手工补一个新的历史值**，
    漏补即静默失守：`check_no_stale_skill_count` 当时枚举了 5/6/8/9 段与 six-stage/6-stage，
    唯独没有 `10-stage`，于是 marketplace.json 的 "a 10-stage hook pipeline" 在实际已经
    是 11 段之后又活了一版（2026-08-16 三方 review 才发现）。

    正向断言不需要预知错值：段数从 hooks.json 现算，文档里写几就得是几。
    CHANGELOG 是历史账，永久豁免。

    **两份清单并存后的口径（先定义再判）**：「N 段 hook 链路 / N-stage hook」**指 CC 清单的事件
    段数**（`hooks/hooks.json`）；Codex 清单**另有名字、另有计数单位**——写作「Codex 清单 N 条」，
    N = `hooks/hooks.codex.json` 的 hook 条数（事件段数对它没有意义：它是 CC 的派生物、事件集由
    派生规则定）。两个数各自现算、各自对账，不混用。
    """
    name = "hook_stage_count_consistent"
    try:
        n = len(_hook_manifest("cc").get("hooks", {}))
        n_codex = _codex_hooks().count_hooks(_hook_manifest("codex"))
    except Exception as e:
        return _fail(name, f"读 hook 清单失败: {e}")
    if n == 0:
        return _fail(name, "hooks.json 未解析出任何 hook 段")
    if n_codex == 0:
        return _fail(name, "hooks.codex.json 未解析出任何 hook 条目")

    targets = [FRAMEWORK_ROOT / "README.md", FRAMEWORK_ROOT / ".claude-plugin" / "marketplace.json"]
    targets += sorted((FRAMEWORK_ROOT / "docs").glob("*.md"))
    targets += sorted((FRAMEWORK_ROOT / "plugins").rglob("*.md"))
    targets += sorted((FRAMEWORK_ROOT / "plugins").rglob("plugin.json"))
    pat = re.compile(r"(\d+)\s*段\s*hook|(\d+)\s*[-\s]\s*stage\s+hook|hook\s*(?:链路|pipeline)[^\n]{0,10}?(\d+)\s*段", re.I)
    pat_codex = re.compile(r"Codex\s*(?:hook\s*)?清单\s*(?:共\s*)?(\d+)\s*条", re.I)
    offenders = []
    for p in targets:
        if p.name == "CHANGELOG.md" or not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in pat.finditer(line):
                val = next((g for g in m.groups() if g), None)
                if val and int(val) != n:
                    offenders.append(f"{p.relative_to(FRAMEWORK_ROOT)}:{i} 写 {val} 段（CC 清单实际 {n}）")
            for m in pat_codex.finditer(line):
                if int(m.group(1)) != n_codex:
                    offenders.append(f"{p.relative_to(FRAMEWORK_ROOT)}:{i} 写 Codex 清单 {m.group(1)} 条"
                                     f"（实际 {n_codex}）")
    if offenders:
        return _fail(name, "; ".join(offenders[:5]))
    return _ok(name, f"(CC 清单实际 {n} 段、Codex 清单 {n_codex} 条，全仓表述一致)")


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

    **扫描面（2026-09-09 扩，v3 harness 同批）**：原口径是 `scripts/*.py` ∩「文本含
    `EVENTS_FILE`」，两处都窄——`plugins/*/bin/` 下无后缀的入口按后缀搜索天然看不见
    （本文件已实证过一次同形盲区），而把事件文件放进小写局部变量的 producer 连
    `EVENTS_FILE` 这个判据也躲得开。现改为 `_event_producer_sources()`：取材是
    `_shipped_py()`（`*.py` ∪ shebang 指向 python 的无后缀入口），判据是四个 token 任一命中。
    **新面严格包含旧面**（旧面的每个文件都在 `plugins/**` 下、且都含 `EVENTS_FILE`），
    逐行判定一个字未动，所以这次扩面只可能多抓、不可能放过原先抓得住的。

    **同批新增的 harness 那半**：`harness` 是 v3 加的字段，取值只有 `cc` / `codex` /
    `unknown` 三个。写死一个枚举外的值不会报错，只会让消费方的分列多出一个谁也不认识的桶，
    因此这里既钉**静态字面**，也用一次**实跑**确认 `_harness.harness()` 真的把非法值归一
    成 `unknown`（而不是原样透传）。
    """
    name = "event_ts_and_reason_contract"
    scripts = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    offenders = []
    for py in _event_producer_sources():
        text = py.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            # 只在这一行确实是在造事件 ts 时才判；generated_at 之类人读时间戳不管
            if not (re.search(r'"ts"\s*:', line) or re.match(r"\s*ts\s*=", line)):
                continue
            if "datetime.now().astimezone()" in line:
                offenders.append(f"{_rel(py)}:{i} 事件 ts 用了本地时区（须 timezone.utc）")
            # naive 时间戳同样致命：没有 tzinfo 的 isoformat() 产出 `2026-08-16T15:24:04`，
            # 消费方按 UTC 解释就整整差一个时区。
            # 这两条必须**并列判**——早先把 naive 检测写在「不含 astimezone 就 continue」
            # 之后，于是只有本地时区行才走得到它，而 naive 恰恰不含 astimezone：
            # 注入 `datetime.now().isoformat()` 反例时闸照样报绿（自查时实测）。
            elif re.search(r"datetime\.now\(\)\.isoformat\(", line):
                offenders.append(f"{_rel(py)}:{i} 事件 ts 用了 naive 时间戳（须 timezone.utc）")
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
    # `<ISO-8601>` 的权威口径：**只钉散文那一条**。原断言还要片里出现可照抄的
    # `timespec='seconds'` 命令行——机械写法改由 `workframe-event` 承担之后，摆一条
    # 手拼命令等于邀请模型绕开它，与本文件 model_mediated 那条的代码通道例外同理。
    # 命令自己那半改由下方 helper_probes 实跑证明，比字面断言强。
    ap_shard = CONTEXT_DIR / "both" / "20-protocol-common.md"
    if not ap_shard.exists():
        offenders.append("通用协议片缺失: context/both/20-protocol-common.md")
    elif "UTC + 秒级" not in ap_shard.read_text(encoding="utf-8"):
        offenders.append("通用协议片: 缺 `<ISO-8601>` 的 UTC+秒级口径定义"
                         "（模型侧的唯一格式来源）")
    # 实跑探针：静态扫描只看得见**字面量所在行**，看不见 helper 间接生成。
    # check-stale-modules 的事件 ts 来自 now_iso()，把那个 helper 改回本地时间后，
    # 逐行正则完全无感——149 道闸照样全绿而事件重新写成 +08:00（Codex 2026-08-16 注入实证）。
    # 与其继续堆正则特例，不如直接调用 helper 看它真正产出什么。
    import subprocess
    # **新增一个 helper 生成 ts 的 producer，就要在这里加一行**，否则这道闸对它无感。
    # `signoff-guard.py` 是第二个：它的 `_now_iso()` 改成 `datetime.now().astimezone()`
    # （写出 `+08:00`）后，**validate 185 项全绿、`test_signoff_guard.py` 也全过**——
    # 那条常驻探针断言的是事件里 `ts` **在不在**，不判它的形态，同样钉不住。
    helper_probes = [
        ("check-stale-modules.py", "now_iso()"),
        ("signoff-guard.py", "_now_iso()"),
        # 第三个：模型手写事件的收口命令。它是**唯一**由模型日常触发的 ts 生成点，
        # 口径散文那半退掉照抄命令之后，这条实跑就是它剩下的全部证明。
        ("workframe_event.py", "_now_iso()"),
    ]
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

    # ---- harness 枚举（v3）----
    # 枚举从 `_harness` 现读，本闸不自带副本；schema 侧的取值由
    # `check_event_schema_v3_availability` 各管一段。
    harness_src = (scripts / "_harness.py").read_text(encoding="utf-8")
    legal = set(re.findall(r'^(?:CC|CODEX|UNKNOWN)\s*=\s*"([a-z]+)"', harness_src, re.M))
    if legal != {"cc", "codex", "unknown"}:
        offenders.append(f"_harness 的门枚举变成了 {sorted(legal)}——本闸按它判字面，"
                         f"枚举读不出来时下面那半是假绿")
    else:
        for py in _event_producer_sources():
            if _rel(py).endswith("scripts/_harness.py"):
                continue
            text = py.read_text(encoding="utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                for val in re.findall(r'"harness"\s*:\s*"([^"]*)"', line):
                    if val not in legal:
                        offenders.append(f"{_rel(py)}:{i} 写死了枚举外的 harness={val!r}"
                                         f"（合法 {sorted(legal)}）——消费方分列会多出一个"
                                         f"谁也不认识的桶")
    # 实跑：非法值必须被归一成 unknown，而不是原样透传。静态判据只看得见字面量，
    # 看不见 `--harness $(...)` 这类运行期取值；归一这一层坏掉时字面扫描全程无感。
    probe = (
        "import importlib.util,sys;"
        f"spec=importlib.util.spec_from_file_location('h',r'{scripts / '_harness.py'}');"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "print(m.harness(['--harness','BOGUS']),m.harness(['--harness=cc']),m.harness([]))"
    )
    try:
        r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        got = (r.stdout or "").strip().splitlines()[-1] if r.stdout.strip() else ""
    except Exception as e:
        got = f"<探针无法运行: {type(e).__name__}: {e}>"
    if got != "unknown cc unknown":
        offenders.append(f"_harness.harness() 归一探针产出 {got!r}——期望 "
                         f"'unknown cc unknown'（非法值须归一为 unknown、`--harness=x` 形态"
                         f"须认得、无参须为 unknown）")

    if offenders:
        return _fail(name, "; ".join(offenders[:6]))
    return _ok(name, f"(UTC+秒级 ts / 裸枚举 reason / helper 实跑探针 / harness 枚举归一；"
                     f"取材面 {len(_event_producer_sources())} 份 py，含 bin/ 无后缀入口)")


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


# W3 旧副本的最小样本：四块各给一处出厂模板里的锚点行（表行形态 / 原句），不带导入。
# 只用于 `doctor_claude_md_import_contract`，按块切开是为了能逐块单留。
_W3_FIXTURE_BLOCKS = {
    "通用角色表与路由规则": ("## 角色体系\n\n| 角色 | 核心能力 |\n|------|---------|\n"
                           "| @pm | 需求分析 |\n| @dev | 代码实施 |\n\n## 路由规则\n\n"
                           "- 消息以 `@角色名` 开头 → 直接调度对应角色\n"),
    "任务状态流转与签发权限": ("## 任务状态流转与签发权限\n\n| 状态变更 | 允许操作者 |\n"
                             "|---------|-----------|\n| `pending_qa → completed` | @qa（签发） |\n"),
    "两套记忆分工与落点判据": ("### 两套记忆分工\n\n| 记忆 | 消费者 | 装什么 | 维护 |\n|---|---|---|---|\n"
                             "| auto-memory（CC 官方记忆目录） | 主 Claude | 指针 | 主 Claude |\n"),
    "文档约定与收口清单": ("## 文档与结构约定\n\n文档归属等请参考 `document-norms` skill。\n"),
}


def check_doctor_claude_md_import_contract():
    """doctor `claude_md` 的行为级 fixture 闸：一组 CLAUDE.md 形态各跑一次真 `check_claude_md`，
    断言级别集合与文案要点。

    **直接调 doctor 的检查函数**，闸里不写导入判定——导入正则全仓只有 doctor 那一份，这里再
    写一份就是第三份。「新模板」那格用真模板现渲染（占位符全填成探针值），模板丢了导入行或
    带回了框架段落，都在这一格上红。

    每格断言三件：级别集合 == 期望（`ok` / `error` / `warn` 的组合，不取最高级）、期望文案
    片段都在、禁止文案片段都不在（逐块单留时别的块名不许出现）。软链接格建不了符号链接时
    改用硬链接（同一个文件，`samefile` 同样成立）；两者都建不了才记为没探，并写进 OK 文案。
    """
    import os
    import tempfile
    name = "doctor_claude_md_import_contract"
    try:
        doc, sc = _doctor(), _scaffold()
    except Exception as e:
        return _fail(name, f"import doctor / scaffold 失败（判定式与模板渲染的唯一实现在它们那里）: {e}")
    tpl = FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "claude-md-template.md"
    if not tpl.is_file():
        return _fail(name, "claude-md-template.md 缺失——「新模板」那格失去样本")
    raw = tpl.read_text(encoding="utf-8")
    new = sc.render_placeholders(raw, {k: "probe" for k in re.findall(r"\{\{([A-Z_]+)\}\}", raw)},
                                 "claude-md-template.md")
    if "\n@AGENTS.md\n" not in new:
        return _fail(name, "出厂 CLAUDE.md 模板里没有顶格的 `@AGENTS.md` 一行——新装项目的 AGENTS.md "
                           "在 CC 里一行都读不到，而 doctor 会在每个新项目上报 error")
    no_imp = new.replace("\n@AGENTS.md\n", "\n")
    fat = "# probe\n\n" + "\n".join(_W3_FIXTURE_BLOCKS.values())
    blocks = list(_W3_FIXTURE_BLOCKS)
    old_msg, new_msg, dup = "纯新增、不动原文", "项目判据全在 AGENTS.md", "与注入片形成双份"
    absent_msg, same_msg = "CLAUDE.md 不存在", "同一个文件"

    # (格名, {相对路径: 内容 | "@link"}, 期望级别集合, 必须出现, 禁止出现)
    rows = [
        ("新模板渲染产物", {"CLAUDE.md": new}, {"ok"}, [], [dup]),
        ("新模板删掉导入行", {"CLAUDE.md": no_imp}, {"error"}, [new_msg], [old_msg, dup]),
        ("旧形态整份肥副本", {"CLAUDE.md": fat}, {"error", "warn"}, [old_msg, dup] + blocks, [new_msg]),
        ("导入行在 ``` 围栏里", {"CLAUDE.md": no_imp + "\n```\n@AGENTS.md\n```\n"},
         {"error"}, [new_msg, "字样"], []),
        ("导入写成行内代码", {"CLAUDE.md": no_imp + "\n见 `@AGENTS.md`\n"}, {"error"}, [new_msg], []),
        # 下面两格各只靠一道保护：前者靠「去掉闭合的 code span」才认得出行尾那处真导入，
        # 后者靠「落单反引号之后不算」才不把跨行 code span 里的字样当导入
        ("code span 之后的行中导入", {"CLAUDE.md": no_imp + "\n见 `x` 与 @AGENTS.md\n"}, {"ok"}, [], []),
        ("跨行 code span 里的导入", {"CLAUDE.md": no_imp + "\n见 `@AGENTS.md\n续行`\n"}, {"error"}, [new_msg], []),
        ("行中导入", {"CLAUDE.md": no_imp + "\n项目判据见 @AGENTS.md 与本文件\n"}, {"ok"}, [], []),
        ("@./AGENTS.md", {"CLAUDE.md": no_imp + "\n@./AGENTS.md\n"}, {"ok"}, [], []),
        ("软链接接法", {"CLAUDE.md": "@link"}, {"ok"}, [same_msg], []),
        ("只在 .claude/CLAUDE.md 导入", {".claude/CLAUDE.md": "@AGENTS.md\n"}, {"warn"}, [absent_msg], []),
        ("只在 CLAUDE.local.md 导入", {"CLAUDE.md": no_imp, "CLAUDE.local.md": "@AGENTS.md\n"},
         {"error"}, [new_msg], []),
        ("导入在 HTML 块注释里", {"CLAUDE.md": no_imp + "\n<!--\n@AGENTS.md\n-->\n"}, {"error"}, [new_msg], []),
        ("CRLF + BOM + 行尾空格", {"CLAUDE.md": "﻿" + new.replace("\n@AGENTS.md\n", "\n@AGENTS.md   \n")
                                  .replace("\n", "\r\n")}, {"ok"}, [], []),
        ("CLAUDE.md 不存在", {}, {"warn"}, [absent_msg], []),
        ("导入在 ~~~ 围栏里", {"CLAUDE.md": no_imp + "\n~~~\n@AGENTS.md\n~~~\n"}, {"error"}, [new_msg], []),
        ("导入行缩进 4 格", {"CLAUDE.md": no_imp + "\n    @AGENTS.md\n"}, {"error"}, [new_msg], []),
        ("导入后紧跟标点", {"CLAUDE.md": no_imp + "\n见 @AGENTS.md。\n"}, {"error"}, [new_msg], []),
        ("旧形态带导入", {"CLAUDE.md": fat + "\n@AGENTS.md\n"}, {"warn"}, [dup] + blocks, [old_msg, new_msg]),
        # 被注释掉的旧副本注入前就被剥掉，不构成双份——残留识别侧也得先去注释
        ("旧副本整段被 HTML 注释包住", {"CLAUDE.md": new + "\n<!--\n" + fat + "\n-->\n"}, {"ok"}, [], [dup]),
    ]
    # 逐块单留（带导入）：只报那一块，别的块名不许出现
    for b in blocks:
        rows.append((f"单留「{b}」", {"CLAUDE.md": "# probe\n\n@AGENTS.md\n\n" + _W3_FIXTURE_BLOCKS[b]},
                     {"warn"}, [dup, b], [x for x in blocks if x != b]))

    agents_text = "# AGENTS.md\n\n## 这个项目是什么\n\nprobe\n"
    bad, unprobed = [], []
    with tempfile.TemporaryDirectory() as td:
        for i, (label, files, want_levels, must, must_not) in enumerate(rows):
            proj = Path(td) / f"p{i}"
            proj.mkdir()
            (proj / "AGENTS.md").write_text(agents_text, encoding="utf-8", newline="")
            for rel, content in files.items():
                dst = proj / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if content == "@link":
                    try:
                        os.symlink(proj / "AGENTS.md", dst)
                    except (OSError, NotImplementedError):
                        try:
                            os.link(proj / "AGENTS.md", dst)
                        except OSError:
                            unprobed.append(label)
                            break
                else:
                    dst.write_bytes(content.encode("utf-8"))
            else:
                out = doc.check_claude_md({"paths": doc.Paths(proj)})
                levels = {lvl for lvl, _m in out}
                text = " ".join(m for _l, m in out)
                miss = [s for s in must if s not in text]
                extra = [s for s in must_not if s in text]
                if levels != want_levels or miss or extra:
                    bad.append(f"「{label}」期望 {sorted(want_levels)} 实得 {sorted(levels)}"
                               + (f"，缺文案 {miss}" if miss else "")
                               + (f"，多出 {extra}" if extra else ""))
    if bad:
        return _fail(name, f"{len(bad)} 格不符（doctor claude_md 判错了形态：error 格判成 ok 是 AGENTS.md "
                           f"在 CC 里读不到却没人报，ok 格判成 error 是每个合规项目天天红）: "
                           + "；".join(bad[:5]))
    tail = f"；没探：{unprobed}（平台建不了软/硬链接）" if unprobed else ""
    return _ok(name, f"({len(rows) - len(unprobed)} 格形态逐格对上{tail})")


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
    # `body.find` 取**首个** mkdir 是有意的：将来有人在 preflight 之前再插一次 mkdir，
    # 命中的就是靠前那个、断言失败报红——fail-safe 方向，不是误报。
    i_mkdir = body.find("project_dir.mkdir(")
    if i_pre < 0:
        return _fail(name, "main() 未调用 preflight_params(params)")
    if i_cfg < 0 or i_sc < 0:
        return _fail(name, "main() 内未见 write_workframe_config / ensure_project_scaffold")
    if i_mkdir < 0:
        return _fail(name, "main() 内未见 project_dir.mkdir( ——`--create-missing` 的建目录"
                           "调用形态变了，本闸失去对「目录也不能早于 preflight」的判定能力")
    if not (i_pre < i_cfg < i_sc):
        return _fail(name, f"main() 内调用顺序不对（preflight@{i_pre} / config@{i_cfg} / "
                           f"scaffold@{i_sc}）——参数校验必须早于任何写盘")
    if not i_pre < i_mkdir:
        return _fail(name, f"main() 内 mkdir@{i_mkdir} 早于 preflight@{i_pre}——坏参数退出后会"
                           f"留下一个空目录，后果不是「脏目录」是**失败痕迹被抹平**：下次重跑"
                           f"不再需要 --create-missing，且 --require-empty 对空目录放行，"
                           f"分辨不出这目录是用户自己建的还是上次装机失败留的")
    return _ok(name, "(main() 内 preflight < mkdir / config / scaffold 顺序断言)")


def check_config_enum_single_source():
    """scaffold 与 doctor 的 config 枚举必须一致（两处事实源的对账闸）。

    scaffold 写盘前校验、doctor 事后体检，判据必须同一套；漂了就会出现
    「scaffold 放行、doctor 报 error」或反之的撕裂。
    role_profile 不在**本检查**内对账——它的权威在 role-profile-catalog.md：scaffold 侧
    确实向 catalog 求证（`extract_role_profile_routing`，preflight 即抛），doctor 侧则是
    一份手写枚举，由 `check_role_enum_single_source` 的 9a 对着 catalog 现算的章节名单
    收口。别因为这里写着「不参与对账」就再造一道重复闸。
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


def _changelog_segments(text):
    """CHANGELOG 逐行分段：`[(行号, 行, 是否段标题, 所属段是否未发布)]`。**分段判据全仓只此一份**，
    `check_unreleased_changelog_numbers` 与 `check_runtime_dir_legacy_literals` 共用。

    段标题 = 以 `## [` 开头的行；未发布 = 标题不含 ISO 日期。段标题行标它自己开启的那一段；
    第一个段标题之前的文件头不属于任何段，标为非未发布。
    """
    out = []
    in_unreleased = False
    for i, line in enumerate(text.splitlines(), 1):
        heading = line.startswith("## [")
        if heading:
            in_unreleased = not re.search(r"\d{4}-\d{2}-\d{2}", line)
        out.append((i, line, heading, in_unreleased))
    return out


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

    # 口径同 `check_hook_stage_count_consistent`：「N 段 hook 链路」指 CC 清单的事件段数；
    # Codex 清单写作「Codex 清单 N 条」，N 为其 hook 条数。两个数各自现算。
    try:
        n_hooks = len(_hook_manifest("cc").get("hooks", {}))
        n_codex = _codex_hooks().count_hooks(_hook_manifest("codex"))
    except Exception as e:
        return _fail(name, f"读 hook 清单失败: {e}")
    agents_dir = FRAMEWORK_ROOT / "plugins" / "core" / "agents"
    actual = {
        "hook 段数": n_hooks,
        "Codex 清单条数": n_codex,
        "skills 数": len(REQUIRED_SKILLS),
        "validate 检查数": len(CHECKS),
        "agents 数": len(list(agents_dir.glob("*.md"))),
    }
    rules = [
        ("hook 段数", re.compile(r"(\d+)\s*段\s*hook\s*链路|(\d+)\s*[-\s]\s*stage\s+hook", re.I)),
        ("Codex 清单条数", re.compile(r"Codex\s*(?:hook\s*)?清单\s*(?:共\s*)?(\d+)\s*条", re.I)),
        ("skills 数", re.compile(r"(\d+)\s*个\s*skills?\b|(\d+)\s+skills\b", re.I)),
        ("validate 检查数", re.compile(r"(\d+)\s*项[^。\n]{0,30}?检查")),
        ("agents 数", re.compile(r"(\d+)\s*个\s*(?:baseline\s*)?(?:角色|agents?)\b", re.I)),
    ]
    quoted = re.compile(r"「[^」]*」|『[^』]*』|`[^`]*`|\"[^\"]*\"")

    offenders = []
    unreleased_titles = []
    for i, line, heading, in_unreleased in _changelog_segments(path.read_text(encoding="utf-8")):
        if heading:
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


# core agents 的 4 份 `description: |` 是结构上恒存在的块标量，作块标量闸的覆盖下限。
_FM_BLOCK_SCALAR_FLOOR = 4
# Agent Skills 开放标准对 description 的字符上限。
_FM_DESC_MAX = 1024
# frontmatter 单行标量的三种安全形态（白名单，fail-closed）
_FM_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*)\s*:(.*)$")
_FM_DQ_RE = re.compile(r'^"(?:[^"\\]|\\.)*"$')      # 双引号：内部 " 必须转义
_FM_SQ_RE = re.compile(r"^'(?:[^']|'')*'$")         # 单引号：内部 ' 必须写成两个
_FM_BLOCK_RE = re.compile(r"^[|>][-+]?\d*$")
# 裸标量不得以之开头的 YAML 指示符
_FM_INDICATORS = set("-?:,[]{}#&*!|>'\"%@`")


def _fm_split_scalar(val):
    """把标量值与行尾注释切开（**引号感知**），返回 `(标量, 注释, 闭合引号后的垃圾)`。

    行尾注释在 YAML 里到处都合法（`user-invocable: true  # 可选`）。不先切开就判形态，
    会把注释算进标量：引号标量被判成「引号未闭合」——**红得对不上，还把人送去改一对
    本来就没问题的引号**。裸标量侧则相反：` #` 确实起注释、值确实被截断，那是要报的。
    """
    if val[:1] in ("'", '"'):
        q, i = val[0], 1
        while i < len(val):
            c = val[i]
            if c == "\\" and q == '"':
                i += 2
                continue
            if c == q:
                if q == "'" and val[i + 1:i + 2] == "'":   # 单引号内的 '' 是转义
                    i += 2
                    continue
                break
            i += 1
        if i >= len(val):
            return val, "", ""                     # 未闭合，交给形态分支报
        scalar, rest = val[:i + 1], val[i + 1:]
        if not rest.strip() or rest.lstrip().startswith("#"):
            return scalar, rest, ""
        return scalar, "", rest                    # 闭合引号之后还有非注释内容
    m = re.search(r"\s#", val)
    if m:
        return val[:m.start()].rstrip(), val[m.start():], ""
    return val, "", ""


def _frontmatter_targets(include_skill_template=False):
    """frontmatter 系列闸的扫描面。两个消费方要的面不同，故只此一份实现、用参数区分。

    `include_skill_template=True` 时补上 `templates/skill-template.md`——它不叫
    `SKILL.md`，`rglob("SKILL.md")` 扫不到，而它是每个新 skill 的种子：模板里写坏一个
    标量形态会被照抄进此后所有新建 skill。块标量缩进闸维持原有 42 份的面不变（改它的
    扫描面不在本次授权内），形态白名单闸取 43 份。
    """
    targets = sorted((FRAMEWORK_ROOT / "plugins").rglob("SKILL.md"))
    targets += sorted((FRAMEWORK_ROOT / "plugins" / "core" / "agents").glob("*.md"))
    if include_skill_template:
        tpl = FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "skill-template.md"
        if tpl.is_file():
            targets.append(tpl)
    return targets


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

    **覆盖边界**（不做完整 YAML 解析——本闸不引 PyYAML，`validate.py` 本体也不引：
    缺库时只有以子进程跑的收口闸单测那一项会红，其余检查照常跑完）：只检块标量缩进断裂
    这一类，因为它最易踩、后果最重（整份元数据丢弃）。其他 YAML 语法错误（重复 key、
    错误的引号嵌套、非法 tag）不在覆盖内，发布前仍应跑一次 `claude plugin validate`
    作为官方兜底（在发版前的人工终检里执行）。
    """
    name = "frontmatter_block_scalar_indent"
    targets = _frontmatter_targets()
    n_blocks = 0            # 实际检到的块标量数——绿灯文案报它，不报扫描面（见下方下限断言）
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
                    n_blocks += 1
    if offenders:
        return _fail(name, "; ".join(offenders[:5]))
    # 下限断言：本闸只在「有块标量可检」时才有意义。扫描面恒为 42 份而实际命中数可以
    # 掉到 0——那时绿灯文案与真绿一字不差，正是本文件 `check_ci_actions_pinned_sha`
    # docstring 记的「扫描面为空的闸与恒绿假闸长得一样」。故绿灯报**实际命中数**，并对
    # 它设下限：4 = core agents 的 4 份 `description: |`，是结构上恒存在的那批。
    if n_blocks < _FM_BLOCK_SCALAR_FLOOR:
        return _fail(
            name,
            f"扫描面 {len(targets)} 份里只检到 {n_blocks} 个块标量（下限 {_FM_BLOCK_SCALAR_FLOOR}）"
            f"——本闸已退化为恒绿：它对不含块标量的文件不做任何判断，"
            f"而绿灯文案看不出覆盖面塌了。要么 agents 的 `description: |` 被改成了单行"
            f"（那要同步下调本下限并说明），要么扫描面被改窄了",
        )
    return _ok(name, f"扫描 {len(targets)} 份 frontmatter，实际检到 {n_blocks} 个块标量，缩进完好")


def check_frontmatter_scalar_forms():
    """frontmatter 的每个顶层标量必须落在四种已知安全形态之一（**白名单，fail-closed**）。

    块标量闸只管 `key: |` 的缩进。单行标量有它看不见的一整族静默失败——而
    `when_to_use` 折进 `description` 之后，SKILL.md 侧只剩单行长标量，正好全落在这族里：

    - 裸标量含 `: `（冒号+空白）→ YAML 当嵌套映射，解析失败，**整份 frontmatter 静默丢弃**
    - 裸标量含 ` #`（空白+井号）→ 井号之后**被当注释静默丢掉**，字段值被悄悄截短。
      **这一类最阴**：文件能解析、检查全绿、skill 照常注册，只是 routing 文本少了一截。
      实测 PyYAML 对它返回截断后的短串且不抛任何异常——所以「能不能解析」这个判据
      对它恒绿，只有形态判定抓得住。**只对 `description` 判**：行尾注释在 YAML 里
      到处都合法（`user-invocable: true  # 可选`），对布尔 / 枚举字段截断无害，
      全局判会造一片假红；而 description 是自由文本，截断正是那条灾难
    - 引号不配对 / 双引号内 `"` 未转义 → 解析失败，同样整份丢弃
    - 裸标量以 `- ? [ & *` 等指示符开头 → 被解析成别的类型，值不是你写的那串
    - 单行标量后跟缩进续行 → YAML 折行拼接，routing 文本与肉眼所见不一致

    后果与块标量闸 docstring 记的 2026-08-17 事故同型：文件看着正常、skill 在模型侧
    彻底消失、而自有检查全绿。

    **为什么是白名单而不是黑名单**：黑名单只挡想得到的坏形态；白名单让**没想到的新形态
    默认报红**。偏了会红不会静默放行——fail-safe 方向。

    **覆盖边界**（不做完整 YAML 解析；本闸不引 PyYAML，`validate.py` 本体也不引——
    缺库时只有以子进程跑的收口闸单测那一项会红，本闸照常判）：
      覆盖：顶层键的单行标量形态、`description` 字符上限、顶层键重复。
      **行尾注释合法**：判形态前先引号感知地切掉它（`key: 'x'  # 注释` 不报）；
      闭合引号之后出现非注释内容则报。
      **不覆盖**：行内流式集合 `[...]` `{...}` 的内部语法、非法 tag、多文档分隔、
      嵌套映射内部的值形态。双引号内的转义只判「`"` 是否成对转义」，不校验
      `\\q` 这类非法转义序列（真解析器会拒、本闸放行）——本仓编码约定是
      **单引号包裹**，单引号标量没有转义语义，该缺口在约定层已关闭。
      发布前仍应跑一次官方 `claude plugin validate` 作为兜底。
    """
    name = "frontmatter_scalar_forms"
    targets = _frontmatter_targets(include_skill_template=True)
    offenders = []
    for p in targets:
        rel = p.relative_to(FRAMEWORK_ROOT)
        is_skill = p.name == "SKILL.md" or p.name == "skill-template.md"
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except Exception as e:
            offenders.append(f"{rel}: 读取失败 {e}")
            continue
        if not lines or lines[0].strip() != "---":
            continue
        try:
            end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        except StopIteration:
            offenders.append(f"{rel}: frontmatter 未闭合 → 整份被当正文，该 skill 不注册")
            continue
        seen_keys = {}
        i = 1
        while i < end:
            raw = lines[i]
            if not raw.strip() or raw.lstrip().startswith("#"):
                i += 1
                continue
            m = _FM_KEY_RE.match(raw)
            if not m:
                offenders.append(
                    f"{rel}:{i + 1} 顶层出现非 `key:` 行 `{raw.strip()[:40]}` "
                    f"→ YAML 报错，整份 frontmatter 静默丢弃，该 skill 在模型侧消失")
                i += 1
                continue
            key, val = m.group(1), m.group(2).strip()
            if key in seen_keys:
                offenders.append(
                    f"{rel}:{i + 1} 顶层键 `{key}` 重复（首次在第 {seen_keys[key]} 行）"
                    f"→ YAML 取最后一个、本仓各处正则取第一个，两条路径读出不同的值而都不报错")
            else:
                seen_keys[key] = i + 1

            # 块标量：缩进由 check_frontmatter_block_scalar_indent 判，本闸只负责跳过它，
            # 不重复报同一个缺陷（两条闸对同一行各报一次，会把人引向两个排查方向）。
            if _FM_BLOCK_RE.match(val):
                j = i + 1
                while j < end and not (
                        lines[j].strip()
                        and len(lines[j]) - len(lines[j].lstrip()) == 0
                        and _FM_KEY_RE.match(lines[j])):
                    j += 1
                i = j
                continue
            # 空值（下挂列表 / 映射）与行内流式集合：不在本闸覆盖内，跳过其块体
            if val == "" or val[0] in "[{":
                j = i + 1
                while j < end and (not lines[j].strip() or lines[j].startswith((" ", "\t"))):
                    j += 1
                i = j
                continue

            # 先把行尾注释切开再判形态（引号感知），否则合法的 `key: 'x'  # 注释`
            # 会被判成「引号未闭合」——假红，且文案指向一对本来没问题的引号。
            val, _comment, _junk = _fm_split_scalar(val)

            # 单行标量：三种安全形态之一，否则红。分支顺序=从具体到宽泛，
            # 具体分支命中后不再落到宽泛分支（不可达的分支与不存在的分支长得一样）。
            if _junk:
                offenders.append(
                    f"{rel}:{i + 1} `{key}:` 闭合引号之后还有非注释内容 `{_junk.strip()[:30]}` "
                    f"→ YAML 解析失败，整份 frontmatter 静默丢弃，该 skill 在模型侧消失")
            elif _FM_DQ_RE.match(val) or _FM_SQ_RE.match(val):
                pass
            elif val[0] == '"':
                offenders.append(
                    f'{rel}:{i + 1} `{key}:` 双引号未闭合，或内部 `"` 未转义 '
                    f"→ YAML 解析失败，整份 frontmatter 静默丢弃，该 skill 在模型侧消失")
            elif val[0] == "'":
                offenders.append(
                    f"{rel}:{i + 1} `{key}:` 单引号未闭合（内部单引号须写成两个连续单引号）"
                    f"→ YAML 解析失败，整份 frontmatter 静默丢弃，该 skill 在模型侧消失")
            elif val[0] in _FM_INDICATORS:
                offenders.append(
                    f"{rel}:{i + 1} `{key}:` 裸标量以 YAML 指示符 `{val[0]}` 开头 "
                    f"→ 被解析成序列 / 锚点 / 别名等别的类型，该字段的值不是你写的那串")
            elif re.search(r":(?:\s|$)", val):
                offenders.append(
                    f"{rel}:{i + 1} `{key}:` 裸标量含冒号加空白 "
                    f"→ YAML 当成嵌套映射，解析失败、整份 frontmatter 丢弃；"
                    f"用单引号包裹整串即可")
            elif _comment and key == "description":
                offenders.append(
                    f"{rel}:{i + 1} `description` 是裸标量且含空白加井号，"
                    f"YAML 会把 `{_comment.strip()[:24]}` 当注释**静默丢弃** "
                    f"→ 字段值被悄悄截短：不报错、能解析、skill 照常注册，"
                    f"只是 routing 文本少了一截；把整串用单引号包起来即可。"
                    f"（**若那确实是注释**：description 按本仓约定本就该单引号包裹，"
                    f"包起来之后行尾注释合法、本条不再报）")

            # description 字符上限（只对 skill 面判；agents 不受 Agent Skills 上限约束）
            if is_skill and key == "description":
                if _FM_DQ_RE.match(val):
                    body = re.sub(r"\\(.)", r"\1", val[1:-1])
                elif _FM_SQ_RE.match(val):
                    body = val[1:-1].replace("''", "'")
                else:
                    body = val
                if len(body) > _FM_DESC_MAX:
                    offenders.append(
                        f"{rel}:{i + 1} `description` {len(body)} 字符，超过 Agent Skills 上限 "
                        f"{_FM_DESC_MAX} → 合规 runtime 可能截断或拒绝该 skill 的元数据")

            # 单行性：下一非空行不得是缩进续行。报完就把续行吃掉，避免它再被当成
            # 「顶层非 key 行」二次报红。
            j = i + 1
            while j < end and not lines[j].strip():
                j += 1
            if j < end and lines[j].startswith((" ", "\t")):
                offenders.append(
                    f"{rel}:{j + 1} `{key}:` 是单行标量却跟了缩进续行 "
                    f"→ YAML 会折行拼接成一个长串，routing 文本与你在文件里看到的不一致")
                while j < end and (not lines[j].strip() or lines[j].startswith((" ", "\t"))):
                    j += 1
                i = j
                continue
            i += 1
    if offenders:
        return _fail(name, "; ".join(offenders[:5]))
    return _ok(name, f"扫描 {len(targets)} 份 frontmatter，顶层标量形态全部合规（含 skill-template）")


def check_ci_actions_pinned_sha():
    """CI workflow 里每个 `uses:` 必须是 `owner/repo@<40 位小写 SHA> # vX.Y.Z`。

    tag 是可变引用：上游 actions 仓被攻破即可把 `v7` 重指到恶意 commit，我们的 CI
    下次运行就执行它——钉 40 位 commit SHA 是 GitHub 官方文档里唯一的不可变用法。
    行尾 `# vX.Y.Z` 注释是 Dependabot 识别并维护的版本锚，也是人工核版的唯一线索：
    缺了它，没人能从 40 位 SHA 看出钉的是哪一版、该不该升。两样缺一即红。

    `==` 语义：每一行都要过，漏一行即红；解析不到任何 `uses:` 行也红——扫描面为空
    的闸与恒绿假闸长得一样。只跳过整行注释（runner 同样不消费）。不同失败模式的
    文案分开写、各自点名消费方会怎么坏，别把人引向错误的排查方向。
    """
    name = "ci_actions_pinned_sha"
    wf_dir = FRAMEWORK_ROOT / ".github" / "workflows"
    files = (sorted(wf_dir.glob("*.yml")) + sorted(wf_dir.glob("*.yaml"))) if wf_dir.is_dir() else []
    if not files:
        return _fail(name, ".github/workflows/ 下没有 workflow 文件——闸没有扫描对象，等于恒绿")
    re_uses = re.compile(r"^\s*(?:-\s+)?uses:(.*)$")
    re_ref = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[^@\s]+)?)@(\S+)$")
    re_ver = re.compile(r"^#\s*v\d+\.\d+\.\d+$")

    def _split(value):
        """把 `uses:` 后面的值拆成（引用, 注释）。YAML 只把前面有空白的 `#` 当注释。"""
        value = value.strip()
        ref, comment = value, ""
        m = re.search(r"\s#", value)
        if m:
            ref, comment = value[:m.start()].strip(), value[m.start():].strip()
        if len(ref) >= 2 and ref[0] == ref[-1] and ref[0] in "\"'":
            ref = ref[1:-1]
        return ref, comment

    total, bad = 0, []
    for p in files:
        rel = p.relative_to(FRAMEWORK_ROOT).as_posix()
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            m = re_uses.match(line)
            if not m:
                continue
            total += 1
            where = f"{rel}:{i}"
            ref, comment = _split(m.group(1))
            if not ref:
                bad.append(f"{where} `uses:` 后面没有单行引用（块标量 / 换行形态）——"
                           f"本闸只认单行 `owner/repo@sha # vX.Y.Z`，改成单行")
                continue
            if "#" in ref:
                # 必须先于形态匹配判：`sha# v7.0.1` 的引用段含空格，re_ref 会先把它拦成
                # 「不是 owner/repo 形态」，文案指向本地/docker action——探针实证过的误导
                bad.append(f"{where} `{ref}` 里的 `#` 前缺空格——YAML 只把前面有空白的 `#` 当注释，"
                           f"这里它成了引用的一部分，runner 会解析不到这个 action")
                continue
            mr = re_ref.match(ref)
            if not mr:
                bad.append(f"{where} `{ref}` 不是 `owner/repo@<ref>` 形态——本地 `./` 与 "
                           f"`docker://` action 不在本仓纪律内，确需引入先扩闸再引入")
                continue
            sha = mr.group(2)
            if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
                if re.fullmatch(r"[0-9a-fA-F]{40}", sha):
                    bad.append(f"{where} SHA 含大写 `{sha[:12]}…`——git 对象名一律小写、Dependabot 写回的"
                               f"也是小写，混用会让人工比对与 grep 失锚")
                elif re.fullmatch(r"[0-9a-fA-F]{4,39}", sha):
                    bad.append(f"{where} `@{sha}` 是短 SHA——GitHub 只把完整 40 位 SHA 当不可变引用，"
                               f"短前缀可能歧义且 Dependabot 不维护")
                else:
                    bad.append(f"{where} `@{sha}` 是浮动 tag / 分支——可变引用，上游 actions 仓被攻破即可"
                               f"把它重指到恶意 commit，CI 下次运行就执行它；改钉 40 位 commit SHA")
                continue
            if not comment:
                bad.append(f"{where} 缺行尾 `# vX.Y.Z` 注释——Dependabot 靠它识别版本、人工核版靠它知道"
                           f"钉的是哪一版；40 位 SHA 本身说不出该不该升")
                continue
            if re_ver.match(comment) is None:
                bad.append(f"{where} 行尾注释 `{comment[:24]}` 不是完整 `# vX.Y.Z`——缺 minor/patch 的"
                           f"注释说不出钉的是哪个版本，核版时无法与 release 对照")
    if total == 0:
        return _fail(name, f"扫描 {len(files)} 个 workflow 文件未找到任何 `uses:` 行——"
                           f"闸没有对象，等于恒绿；解析规则或文件形态变了")
    if bad:
        return _fail(name, f"{len(bad)}/{total} 个 uses 行不合规：\n    " + "\n    ".join(bad[:8]))
    return _ok(name, f"{total} 个 uses 行（{len(files)} 个 workflow）全部钉 40 位 SHA + `# vX.Y.Z` 注释")


# === 注入通道（`plugins/core/context/`）===
#
# 五条断言共用下面这组取数件。**闸一律不自己抄一份装箱逻辑**：片数、每片字符数、两个上限
# 都问 `inject-context.py` 本体要——抄一份就是第二个事实源，宽严必漂。

CONTEXT_DIR = FRAMEWORK_ROOT / "plugins" / "core" / "context"

_INJECT_CACHE = {}


def _inject_context():
    """import 打包器本体。文件名带连字符，只能按路径加载，不能 `import_module`。"""
    if "mod" not in _INJECT_CACHE:
        import importlib.util
        src = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "inject-context.py"
        spec = importlib.util.spec_from_file_location("_wf_inject_context", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _INJECT_CACHE["mod"] = mod
    return _INJECT_CACHE["mod"]


def _coverage_ledger():
    """`context/coverage.json`——语义覆盖对账的台账。"""
    return json.loads((CONTEXT_DIR / "coverage.json").read_text(encoding="utf-8"))


def _plugin_rel(value):
    """台账里的路径字段是**插件根相对**（`rules/core`、`skills/signal-intake/`），在这里
    补上仓内前缀。台账不写 `plugins/core/…` 是因为它随插件装到用户机器，仓内结构路径
    在那边不存在——`core_assets_no_repo_internal_path` 钉这一条。
    """
    return FRAMEWORK_ROOT / "plugins" / "core" / value


def _load_coverage_probe():
    """惰性 import `plugins/core/scripts/_coverage_probe`——语义覆盖判定式的**唯一实现方**。

    闸不自留一份切段 / 声明解析：闸另写一套只能比消费方更宽或更窄，两个方向都实证出过洞。
    只在检查函数体内调用、异常由调用方转 `_fail`，**不放模块顶层**：那边语法坏时应是
    复用它的那一项红，而不是 validate 整体起不来。

    路径解析（`legacy_source_dir` / `lands_in` → 仓内 Path）留在本文件的 `_plugin_rel`，
    以回调形式喂进去——**判定式不许知道框架仓的目录层级**，那样它就只能服务一个消费方了。
    """
    scripts = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import importlib
    return importlib.import_module("_coverage_probe")


def _context_md_files():
    """打包器真正会读的那批源，`(相对路径, Path)`，顺序同打包器。

    **扫描面与消费方对齐**：`context/` 根下的 `coverage.json` 不在内——打包器不读它，
    把它算进「注入源」等于让断言替一个永远投不出去的文件背书。
    """
    ic = _inject_context()
    out = []
    for group in ("both",) + ic.SCOPES:
        d = CONTEXT_DIR / group
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md"), key=lambda p: p.name):
            out.append((f"{group}/{f.name}", f))
    return out


def _context_face_problem():
    """取材下限：`_context_md_files()` 扫 0 时，靠它取材的闸会**恒绿**——与「真的没问题」同形。

    三条闸共用这一份判定（`sources_lf_only` / `no_duplicate_text` / `pointer_targets_exist`），
    各写各的必漂，而这一格的失效方向恰恰是静默放行。**另两条有意不用它**：`within_cap`
    取材走打包器自己的 `collect_sources`（扫 0 即 `PackError`），`semantic_coverage` 的空
    取材会让 47 段全部落进「回归方向」——两条本就 fail-safe，再叠一层只会多一条恒不触发
    的分支。
    """
    if not _context_md_files():
        return ("注入源取材面为空——`context/{both,main,sub}/` 下一个 .md 都没扫到。"
                "本闸此刻**恒绿**，与「真的没问题」在终端里同形；先查目录是不是被改名或删了")
    return None


def _hooks_shard_declarations():
    """hooks.json 里挂到打包器的条目：`{scope: [(事件名, i, n), ...]}`。

    只扫 CC 那张表——Codex 表是另一份文件、另一批次的事。
    """
    data = json.loads((FRAMEWORK_ROOT / "plugins" / "core" / "hooks" / "hooks.json")
                      .read_text(encoding="utf-8"))
    out = {}
    for event, groups in (data.get("hooks") or {}).items():
        for grp in groups or []:
            for h in grp.get("hooks") or []:
                cmd = h.get("command") or ""
                if "inject-context.py" not in cmd:
                    continue
                scope = re.search(r"--scope\s+(\w+)", cmd)
                shard = re.search(r"--shard\s+(\d+)/(\d+)", cmd)
                key = scope.group(1) if scope else "?"
                out.setdefault(key, []).append(
                    (event, int(shard.group(1)) if shard else None,
                     int(shard.group(2)) if shard else None))
    return out


def check_context_shards_within_cap():
    """每片都装得下，且挂载表声明的片数与打包器现算的一致。

    **两条上限分支是对打包器输出的独立复核，不是重述它自己的判据。** 打包器按策略上限
    装箱，所以它**没坏**的时候这两条恒不触发——它们抓的是打包器改坏了（装箱把上限读成
    另一个值、片头忘了计入、后处理重算片号时溢出）。探针据此打在打包器上，不是打在源上：
    源长到装不下时走的是「单文件超策略上限」那条 `PackError`，另一条路径。

    三条分支各点名不同的消费方后果，措辞不许合并：超策略上限 = 还到得了但没余量；
    超物理上限 = 静默落盘 + 模型编内容填平；声明数不符 = 有源永远不会被注入。

    另两条是**往「小」的方向**的复核，与上面三条方向相反：
      - **取材对账**：打包器读到的源清单 == 本文件独立枚举到的清单。打包器少读一份源时
        每片仍然合法、余量看着更宽松，**与正常同形**，上面三条一条都不会响。
      - **片长恒等式**：`Σ片长 == Σ片头长 + Σ源长 + 2×文件数`（每片头与正文之间、每两份
        源之间各一个空行）。它钉的是**打包器内部账目自洽**：报出来的片长，恰好等于它
        自己声称装进去的那些东西。抓「片头没计入片长」「分隔符数目对不上」「某份源进了
        `files` 清单却没进任何片」。<br>**它抓不到「两边一起错」**——`strip_frontmatter`
        剥过头时，`files` 的字符数与片正文出自**同一批已剥好的 body**，等式照样成立。
        这一格与下面 `u16len` 那格同源：内部恒等式对共用输入的错误天然失明。

    **抓不住的那一格（写在这里，别让读者高估它）**：本闸的 `chars` 与打包器的装箱判据
    共用同一个 `u16len`。**度量口径本身写错时两边一起错**——实测把 `u16len` 减半，
    上面那三条上限分支**一条都不响**（每片看着都变小了，永远够不着上限）。
    片长恒等式那条**碰巧会响**，但**那是巧合不是设计**：等式里 `2×文件数` 那一项是**字面
    常数**、不经 `u16len` 量，尺子缩水时只有它不跟着缩，于是差出一个小余数（实测 cc/main
    差 −5、cc/sub 差 −4）。换一种让常数项也一起错的口径错误，它同样不响。
    **守这一格的不是本闸**，是 `u16len` 只此一份实现、且它的口径由 `PHYSICAL_CAP` 的实测
    出处钉着。
    """
    name = "context_shards_within_cap"
    ic = _inject_context()
    problems, counts = [], {}
    for harness in ("cc", "codex"):
        for scope in ic.SCOPES:
            try:
                rep = ic.report(harness, scope)
            except ic.PackError as e:
                problems.append(f"[{harness}/{scope}] 装箱失败：{e}")
                continue
            counts[(harness, scope)] = rep["shard_count"]
            for s in rep["shards"]:
                if s["chars"] > rep["physical_cap"]:
                    problems.append(
                        f"[{harness}/{scope}] 第 {s['index']} 片 {s['chars']} > 物理上限 "
                        f"{rep['physical_cap']}：超出的部分**不会进上下文而是静默落盘**，"
                        f"模型只拿到预览、会用编造内容填平缺口，且没有任何一处报错")
                elif s["chars"] > rep["policy_cap"]:
                    problems.append(
                        f"[{harness}/{scope}] 第 {s['index']} 片 {s['chars']} > 策略上限 "
                        f"{rep['policy_cap']}（物理上限 {rep['physical_cap']} 之内，这一轮到得了）："
                        f"余量已不足以容纳片头之外的增补，下一次往这片加内容就撞物理上限")

            # (a) 取材对账：打包器读到的 == 闸独立枚举到的
            packed = [f["path"] for f in rep["files"]]
            enumerated = [rel for rel, _p in _context_md_files()
                          if rel.startswith("both/") or rel.startswith(scope + "/")]
            if packed != enumerated:
                miss = [x for x in enumerated if x not in packed]
                extra = [x for x in packed if x not in enumerated]
                problems.append(
                    f"[{harness}/{scope}] 打包器读到的源与独立枚举不一致"
                    f"（打包器少读 {miss or '无'}，多读 {extra or '无'}）："
                    f"少读的那份**一个字都不会到达模型**，而每片仍然合法、余量看着更宽松，"
                    f"与正常同形——上限那几条一条都不会响")

            # (b) 片长恒等式：每片「片头 + 空行 + 正文」，正文里每两份源之间也是一个空行
            head_sum = sum(ic.u16len(ic.shard_header(scope, s["index"], rep["shard_count"]))
                           for s in rep["shards"])
            lhs = sum(s["chars"] for s in rep["shards"])
            rhs = head_sum + sum(f["chars"] for f in rep["files"]) + 2 * len(rep["files"])
            if lhs != rhs:
                problems.append(
                    f"[{harness}/{scope}] 片长恒等式不成立：Σ片长 {lhs} ≠ Σ片头 {head_sum} + "
                    f"Σ源长 {rhs - head_sum - 2 * len(rep['files'])} + 2×{len(rep['files'])} 份 "
                    f"= {rhs}（差 {lhs - rhs:+}）：打包器报的片长与它自己声称装进去的东西对不上"
                    f"——片头没计入片长、分隔符数目变了、或有源进了清单却没进任何片。"
                    f"三种都让余量算得比真实值宽，而每片看着仍在上限内")

    declared = _hooks_shard_declarations()
    if not declared:
        note = "挂载表未声明任何注入片（通道已建、尚未挂载），声明数对账本次不参与判定"
    else:
        note_bits = []
        for scope, entries in sorted(declared.items()):
            real = counts.get(("cc", scope))
            ns = {n for _e, _i, n in entries if n is not None}
            idx = sorted(i for _e, i, _n in entries if i is not None)
            if real is None:
                problems.append(f"挂载表声明了 `--scope {scope}`，但打包器不认识这个 scope")
                continue
            if ns != {real} or idx != list(range(1, real + 1)):
                problems.append(
                    f"[{scope}] 挂载表声明 {sorted(ns)} 片 / 片号 {idx}，打包器现算 {real} 片："
                    f"少声明一片就有源**永远不会被注入**，hook 照跑、片头照发、`--check` 全绿，"
                    f"与正常同形；多声明一片则那条 hook 每次启动都失败")
            note_bits.append(f"{scope}={real}")
        note = "声明数与现算片数一致（" + " ".join(note_bits) + "）" if note_bits else ""

    if problems:
        return _fail(name, f"{len(problems)} 项：\n    " + "\n    ".join(problems[:8]))
    shown = " ".join(f"{h}/{s}={n}片" for (h, s), n in sorted(counts.items()))
    return _ok(name, f"({shown}；上限 {ic.PHYSICAL_CAP} 物理 / {ic.policy_cap()} 策略；{note})")


def check_context_sources_lf_only():
    """注入源里不许有 CR。两种形态分开报——它们坏在不同的地方。"""
    name = "context_sources_lf_only"
    face = _context_face_problem()
    if face:
        return _fail(name, face)
    files = _context_md_files()
    problems = []
    for rel, path in files:
        raw = path.read_bytes()
        crlf = raw.count(b"\r\n")
        bare = len(re.findall(rb"\r(?!\n)", raw))
        if crlf:
            problems.append(
                f"{rel}: {crlf} 处 CRLF——该文件的 raw 字符数比打包器归一后**投出去**的多 "
                f"{crlf}，余量按多出来的那个算，越贴上限越容易把一片算成「还装得下」")
        if bare:
            problems.append(
                f"{rel}: {bare} 处裸 CR——归一时被当成换行，行边界随之错位，"
                f"按行锚定的判定式（标题、列表、围栏）整类失配")
    if problems:
        return _fail(name, f"{len(problems)} 项：\n    " + "\n    ".join(problems[:8]))
    return _ok(name, f"({len(files)} 份注入源全为 LF)")


# 段落级重复的下限，UTF-16 单元。短段落（表头、单行提示）天然会重合，钉它们只会
# 制造假红；这个值只钉「整段被复制过去」这一形态。
DUP_MIN_CHARS = 100


def check_context_no_duplicate_text():
    """同一段长文本不许出现在两处。**两种重复坏在不同的地方，措辞分开**：

      - 会同片系投递的两份（`both/` + 同一 scope）→ 同一次会话里把同一段发两遍，白付额度
      - `main/` 与 `sub/` 各一份 → 两处手写同一事实，改一处漏一处之后，两扇注入下发的
        纪律就此不一致，而**没有任何一处会报错**

    **归一后再比**：一份 LF 一份 CRLF 的复制粘贴，不归一时这条断言恰好对它要防的那种
    重复失明；归一后相同的会在文案里点出来，免得排查者去比字节。
    """
    name = "context_no_duplicate_text"
    face = _context_face_problem()
    if face:
        return _fail(name, face)
    ic = _inject_context()
    seen = {}
    for rel, path in _context_md_files():
        full = path.read_bytes().decode("utf-8")
        text = ic.strip_frontmatter(full)
        # 行号要能直接跳到文件里那一行：frontmatter 被剥掉了，得把它的行数补回来，
        # 否则红灯指向的行与真实位置差着整个 frontmatter，排查时打开的是另一段。
        line_no = ic.normalize_eol(full[:len(full) - len(text)]).count("\n") + 1
        for para in re.split(r"\r?\n[ \t]*\r?\n", text):
            span = ic.normalize_eol(para).count("\n") + 1
            norm = ic.normalize_eol(para).strip()
            # `raw` 存**去掉首尾空白但不归一行尾**的形态：与 `norm` 的差只剩行尾。
            # 存原样 `para` 会把「切段带来的首尾空白差」也算成差异，那条「归一后才相同」
            # 的提示就恒真——恒真的提示读起来像结论，会把人支去比字节。
            if norm and ic.u16len(norm) >= DUP_MIN_CHARS:
                seen.setdefault(norm, []).append((rel, line_no, para.strip()))
            line_no += span + 1
    problems = []
    for norm, hits in seen.items():
        if len(hits) < 2:
            continue
        where = "、".join(f"{r}:{n}" for r, n, _p in hits)
        groups = {r.split("/", 1)[0] for r, _n, _p in hits}
        if groups <= {"main", "sub"} and len(groups) > 1:
            why = ("这两份分属 `main/` 与 `sub/`，不会同片系投递，但**是同一事实的两处手写**"
                   "——改一处漏一处之后两扇注入下发的纪律就此不一致，且没有任何一处会报错")
        else:
            why = ("这几份会在同一个 scope 里一起投出去，同一段在一次会话里发两遍，"
                   "白付一份额度，而额度正是本通道最紧的资源")
        tail = ""
        if len({p for _r, _n, p in hits}) > 1:
            tail = ("；另注：这几处**归一后才相同**（行尾差异），"
                    "不做归一的话这条断言恰好对它要防的那种复制粘贴失明")
        problems.append(f"{len(hits)} 处相同段落（{ic.u16len(norm)} 单元）：{where}——{why}{tail}"
                        f"\n      首行：{norm.splitlines()[0][:48]}")
    if problems:
        return _fail(name, f"{len(problems)} 组：\n    " + "\n    ".join(problems[:5]))
    return _ok(name, f"(按段落比对，无 ≥{DUP_MIN_CHARS} 单元的重复)")


# 点名的两种承载形态。**只钉与「skill」「命令名」相邻的那些**：一个不带这类标记的
# 反引号词无从判断它是不是在点名，钉它会把普通术语一起判红。
_POINT_SKILL_RES = (
    # 冒号形态（`skill: \`x\``）是本仓的主流文风，早先漏在射程外：`skill\s+` 卡在冒号上，
    # 于是 `qa.md` 与 `prompt-eng.md` 的射程命中数一度为 0，而 docstring 声明的边界写的是
    # 「裸反引号逃出射程」——**陈述比实际射程宽**，读的人不会想到冒号形态也在外面。
    # 反引号内可带插件前缀 `core:`（全名写法，裸名会撞同名内置 skill）；捕获组只取前缀之后
    # 的名字，下游按 `plugins/core/skills/<name>/` 求证，前缀在哪种写法下都不改变指向。
    re.compile(r"skill\s*[:：]?\s*`(?:core:)?([a-z0-9][a-z0-9-]*)`"),
    re.compile(r"`(?:core:)?([a-z0-9][a-z0-9-]*)`\s*skill"),
    # `\`x\` SKILL.md`：指的是那个 skill 的文件，语义上仍是点名。上一条不加 IGNORECASE
    # 而单列一条，是为了别把 `\`x\` Skill 工具` 这类散句一并卷进来。
    re.compile(r"`(?:core:)?([a-z0-9][a-z0-9-]*)`\s*SKILL\.md"),
    re.compile(r"/core:([a-z0-9][a-z0-9-]*)"),
)
_POINT_CMD_RE = re.compile(r"`(workframe-[a-z0-9-]+)`")


def check_context_pointer_targets_exist():
    """注入片里**被本闸认出来的**那些 skill / 命令点名，都解析得到目录。

    **射程比「每个点名」窄，这句不能省。** 认的只有 `_POINT_SKILL_RES` 那几种形态：
    `skill \\`x\\``（含 `skill: \\`x\\`` 冒号形态）、`\\`x\\` skill`、`\\`x\\` SKILL.md`、
    `/core:x`（命令另认 `\\`workframe-*\\``）。**裸反引号提到的 skill 名
    逃出射程**——一个不带这类标记的反引号词无从判断它是不是在点名，钉它会把普通术语
    一起判红。当前注入源里**就有多处这样的提及不在射程内**（例如 `both/40` 的「调
    `module-init`」、几处裸反引号的 `\\`librarian\\`` / `\\`test-case-design\\``）；此处不列清单、
    不写个数——两者都随注入源增删而漂，手写即过期（实测曾写「另有 9 处被认出」而实际已是 13 处）。
    它们今天都指向真实存在的 skill，**但改名或删除时本闸不会响**。
    **下限断言接不住这一格**：它只在「一个都没认出来」时才响，部分逃逸而其余仍在时不触发。
    补形态断言（把裸反引号词收进宽候选、再用严判据求证差集）是另一件事，不在本闸。
    """
    name = "context_pointer_targets_exist"
    face = _context_face_problem()
    if face:
        return _fail(name, face)
    skills_dir = FRAMEWORK_ROOT / "plugins" / "core" / "skills"
    bin_dir = FRAMEWORK_ROOT / "plugins" / "core" / "bin"
    problems, n_skill, n_cmd = [], 0, 0
    for rel, path in _context_md_files():
        text = path.read_bytes().decode("utf-8")
        names = set()
        for rx in _POINT_SKILL_RES:
            names |= set(rx.findall(text))
        for nm in sorted(names):
            n_skill += 1
            if not (skills_dir / nm / "SKILL.md").is_file():
                problems.append(
                    f"{rel}: 点名 skill `{nm}`，但 `plugins/core/skills/{nm}/SKILL.md` 不存在"
                    f"——注入片会让模型去调一个没有的 skill；它多半会挑一个名字最近的调，"
                    f"失败与「模型选错 skill」同形")
        cmds = sorted(set(_POINT_CMD_RE.findall(text)))
        for nm in cmds:
            n_cmd += 1
            if not (bin_dir / nm).is_file():
                problems.append(
                    f"{rel}: 点名命令 `{nm}`，但 `plugins/core/bin/{nm}` 不存在"
                    f"——模型会去跑一条不存在的命令；Windows 侧还会落进 PATHEXT 隐式解析，"
                    f"同一句在不同机器上结果相反")
    if problems:
        return _fail(name, f"{len(problems)} 项：\n    " + "\n    ".join(problems[:8]))
    if n_skill + n_cmd == 0:
        # 取材面非空却一个点名都没提到：两条正则失明与「片里真的一个点名都没有」同形，
        # 而前者会让本闸从此恒绿。方向过严（真要把点名全删光时会多红一次，红灯已写明
        # 怎么办），比静默失明便宜。
        return _fail(name, f"{len(_context_md_files())} 份注入源里一个 skill / 命令点名都没提取到"
                           f"——两条点名正则失明与「片里真的没有点名」在终端里同形，而前者"
                           f"会让本闸从此恒绿。若确实有意去掉全部点名，把本条下限一并改掉")
    return _ok(name, f"({n_skill} 处 skill 点名 + {n_cmd} 处命令点名全部解析得到)")


# 子 agent 动看板前必须调的 skill（全名）与那句规矩必须写出的触发时刻。
# 触发时刻逐个钉：少写一个，子 agent 在那个时刻就不会想到去调，而其余几个照样在，读起来完整。
PINNED_BOARD_SKILL = "core:task-management"
PINNED_BOARD_TRIGGERS = ("建任务", "改任务状态", "pending_qa", "签发", "打回")
PINNED_BOARD_NEGATIONS = ("不必", "不用", "无需", "不要", "跳过", "可选")


def check_sub_protocol_pins_task_management():
    """子 agent 必载片里必须有一句「动看板之前先调 `core:task-management`」的固定规矩。

    子 agent 没有按角色的 skill 映射；其余 skill 靠 skill 清单的 description 触发，唯独
    `task-management` 装着看板字段约束、流转规则与签发权限，**必须保证每个子 agent 都拿得到**，
    所以它由必载片固定植入。本闸钉的就是那一句还在、还完整、还投得到子 agent。

    **扫描面 = 打包器 `collect_sources("sub")` 的产出**（`both/` ＋ `sub/`，已剥 frontmatter）——
    与实际投给子 agent 的字节同源，不另列文件：规矩被挪进 `main/` 时它就投不到子 agent 了，
    本闸按投递面判，挪走即红。以段落（空行分隔）为单位找那句规矩，触发时刻只在该段
    **指向之前**的那半句里找。

    **五个失败方式，文案分开**（成因不同、改的地方不同）：
    ① 写成了裸名 `task-management`——裸名遇到同名的内置 skill 会解析到那一个；
    ② 规矩整句不见了（含被挪进 `main/`：那里挂 SessionStart，子 agent 收不到）；
    ③ 句子在，但缺了某个触发时刻；
    ④ 全名不在 `_POINT_SKILL_RES` 射程内——`context_pointer_targets_exist` 看不见它，
       skill 改名 / 删除时没有闸会响；
    ⑤ 动作被改掉：指向所在那一小句没有「先调」，或带否定词（「不必调」「可以跳过」）。
       只查那一小句、只认一张否定词表——措辞换成表外的否定说法仍会漏，这是字面判据的边界。

    **不判什么**：不判子 agent 实际有没有照做（运行期行为），也不判规矩措辞之外的
    task-management 正文内容。到达面（片是否真的挂在 SubagentStart 上）由
    `sub_shard_event_binding` 管。
    """
    name = "sub_protocol_pins_task_management"
    face = _context_face_problem()
    if face:
        return _fail(name, face)
    ic = _inject_context()
    literal = f"`{PINNED_BOARD_SKILL}`"
    bare = PINNED_BOARD_SKILL.split(":", 1)[1]

    def paragraphs(scope):
        out = []
        for rel, body in ic.collect_sources(scope, CONTEXT_DIR):
            for para in re.split(r"\n\s*\n", body):
                out.append((rel, para))
        return out

    def n_triggers(para):
        return sum(1 for t in PINNED_BOARD_TRIGGERS if t in para)

    sub_paras = paragraphs("sub")
    # **标题段不算「指向」**：节标题也写全名（`## 动看板之前先调 \`core:task-management\``）后，
    # 规矩段一旦出事（写成裸名 / 整句删掉 / 挪进 main），标题段就成了唯一命中，于是三种情况
    # 都被报成「缺触发时刻 [全部]」，把人引向错误的排查方向（实测）。规矩只在正文段里找。
    def is_heading(p):
        return p.lstrip().startswith("#")

    hits = [(rel, p) for rel, p in sub_paras if literal in p and not is_heading(p)]
    if not hits:
        # 「像那句规矩」的段落：触发时刻命中过半。用来分辨是写成了裸名，还是整句没了
        need = len(PINNED_BOARD_TRIGGERS) // 2 + 1
        lookalike = [(rel, p) for rel, p in sub_paras
                     if n_triggers(p) >= need and f"`{bare}`" in p]
        if lookalike:
            return _fail(name, f"{lookalike[0][0]} 里那句规矩写成了裸名 `{bare}`——裸名遇到同名的"
                               f"内置 skill 会解析到那一个，子 agent 调到的不是本插件的看板 skill。"
                               f"改成 {literal}")
        return _fail(name, f"子 agent 必载片（both/ ＋ sub/）里没有指向 {literal} 的规矩——子 agent "
                           f"动看板前不再被要求调它，流转规则与签发权限只能凭印象；"
                           f"其余 skill 靠 description 触发，这一个是唯一的固定植入。"
                           f"若是挪进了 `context/main/`：主会话片挂 SessionStart，子 agent 收不到")

    # 触发时刻只在**指向之前**那半句里找：句式是「X 之前先调 …」，而同段后半会解释
    # 理由（「签发权限都装在它里面」）——按整段找时删掉触发时刻里的「签发」照样绿（实测）
    rel, para = max(hits, key=lambda h: n_triggers(h[1][:h[1].index(literal)]))
    head = para[:para.index(literal)]
    missing = [t for t in PINNED_BOARD_TRIGGERS if t not in head]
    if missing:
        return _fail(name, f"{rel} 里指向 {literal} 的那句规矩缺触发时刻 {missing}——子 agent "
                           f"在这些时刻不会想到先调它，而句子其余部分读起来仍完整")
    # ⑤ 动作：指向所在的那一小句（上一个句号之后）必须是「先调」，且不带否定——触发时刻与
    # 全名都在、只把「之前，先调」改成「之后，不必调」时，前四条全绿而规矩的意思已经反了（实测）
    # 分号也是小句边界：前一小句里正当地写着「不要…」时，不能算到指向那一小句头上（实测误报）
    clause = re.split(r"[。！？；;\n]", head)[-1]
    negations = [w for w in PINNED_BOARD_NEGATIONS if w in clause]
    if "先调" not in clause or negations:
        why = f"带否定词 {negations}" if negations else "没有「先调」"
        return _fail(name, f"{rel} 里指向 {literal} 的那一小句{why}——触发时刻与全名都在，"
                           f"但规矩的动作已经不是「动看板之前先调它」：{clause.strip()[-60:]!r}")
    recognized = set()
    for rx in _POINT_SKILL_RES:
        recognized |= set(rx.findall(para))
    if bare not in recognized:
        return _fail(name, f"{rel} 里的 {literal} 不在 `_POINT_SKILL_RES` 射程内（写成「skill "
                           f"{literal}」或「{literal} skill」）——否则 `context_pointer_targets_exist`"
                           f"看不见它，那个 skill 改名 / 删除时没有闸会响")
    return _ok(name, f"({rel}：{literal} 全名、{len(PINNED_BOARD_TRIGGERS)} 个触发时刻齐全、"
                     f"在点名射程内；扫描面 = 打包器 sub scope 投递面)")


def check_context_semantic_coverage():
    """退役前那批 rules 的**每一段都有落点声明**：旧段全集 ⊆ 各落点声明的并集。

    **能力边界一（删掉这句等于把一个已知缺口变成暗坑）**：它钉的是「这一段有没有落点
    声明」，**不是**「落点里的文字忠实承接了那一段」。后者没有机器判据，靠人工双检。
    `coverage.json` 的 `elsewhere` 同理只是一条声明——落点文件此刻存不存在都不影响判定，
    存不存在只在下面的 OK 文案里如实报出来。

    **能力边界二（射程）**：切段只认**围栏外的 ATX 标题 `##`–`######`**。两类不在射程：
      - **H1**（各文件标题）——有意排除，理由见 `_coverage_probe.HEADING_RE` 旁注；
      - **setext 形式的标题**（正文行 + 下一行 `===` / `---`）——这批源里今天为 0，但源里
        一旦出现，那一段对本闸**根本不存在**，它整块消失也不会红。
    这条必须写出来，因为本闸的红灯文案（「第 N 段无人认领」「原本有落点、现在没有了」）
    把自己描述成一个**段级完整性闸**，读者据此会推出「一段没了它会说」——射程之外不会。

    **判定式本身不在本文件**，在 `plugins/core/scripts/_coverage_probe.py`（全仓唯一实现）。
    本闸只负责填框架自己的三个参数——源目录 / 落点文件清单 / 台账——再把结果翻成一行终端
    文案。判定逻辑别往回抄：抄回来就有了第二个实现，而两个实现只能比对方更宽或更窄。
    """
    name = "context_semantic_coverage"
    led = _coverage_ledger()
    probe = _load_coverage_probe()
    lands = [(rel, path.read_bytes().decode("utf-8")) for rel, path in _context_md_files()]
    res = probe.evaluate(_plugin_rel(led["legacy_source_dir"]), lands, led,
                         resolve=_plugin_rel)

    if res["problems"]:
        return _fail(name, f"{len(res['problems'])} 项：\n    "
                           + "\n    ".join(res["problems"][:8]))
    pend = res["pending_lands"]
    tail = f"；{len(pend)} 条 elsewhere 声明的落点尚未落地（{sorted(set(pend))}）" if pend else ""
    split = res["split"]
    both = f"，其中 {split} 段被拆成两半、两个落点都登记了" if split else ""
    return _ok(name, f"({len(res['frozen'])} 段全部有落点声明：context 内 {len(res['in_lands'])} "
                     f"+ elsewhere {len(res['elsewhere'])} + retired {len(res['retired'])}{both}；"
                     f"{res['snapshot_note']}{tail})")


def check_auto_update_list_alignment():
    """受保护资产清单：必载片那份 vs 判定脚本那份，两处手写必须逐条同值。

    **这是 `veto_list_alignment` 的孪生闸，形状照搬它**（section 圈段 → 抠反引号字面 →
    两侧归一 → 双向差集），不新写 parser。两张表在同一个文件里分叉过一次，脚本注释自己
    写着；而本批之内**这张表又漂了两次**——先是删 `.claude/rules/` 只删了文档面（碰巧
    无害），再是加 `.codex/**` 只加了文档面（文档说保护、机器不认）。**两次都发生在
    没有闸的这一侧。**

    抠字面时**只取每条首个全角括号或破折号之前的那一段**：括注里常有别的反引号
    （实测会多抠出 `config.toml` / `librarian` / `/core:self-iteration` 三个），
    它们是解释文字不是清单项。这与 veto6 那条「只取表格第一列」是同一个理由。

    **段必须圈准**：本片有两个列表——受保护清单 ＋「可直接写入的资产」，后者是**反向
    条目**（`projects/issues/` / 看板 / PRD）。抠错段会把它们当成受保护项，对账面当场翻倍。

    **运行态两条走 veto6 已有的解法**：`<state>/**` 与 `shared/MEMORY.md` 在脚本侧由
    `runtime_protected()` 现算、不在常量里（目录位置只有 `_state_io` 一个事实源，写死一份就是第二个源，
    漂了之后静默失明）。这里同样把它们从差集里摘出去单独核，**不为它们放宽差集**。
    """
    name = "auto_update_list_alignment"
    doc = CONTEXT_DIR / "both" / "40-protected-assets.md"
    script = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "signoff_tier_check.py"
    missing = [str(p) for p in (doc, script) if not p.exists()]
    if missing:
        return _fail(name, f"对账源缺失: {missing}")

    text = doc.read_text(encoding="utf-8")
    marker = "**可直接写入的资产**"
    if marker not in text:
        return _fail(name, f"注入片里找不到 `{marker}` 分隔——受保护清单与「可直接写入」"
                           f"两个列表的边界没了，对账面会把反向条目（issues / 看板 / PRD）"
                           f"当成受保护项，当场翻倍")
    section = text[:text.index(marker)]
    doc_paths, n_rows = set(), 0
    for line in section.splitlines():
        if not line.startswith("- "):
            continue                      # 缩进的子条目（例外说明）不是清单项
        head = re.split(r"（|——", line)[0]
        found = set(re.findall(r"`([^`]+)`", head))
        if found:
            n_rows += 1
            doc_paths |= found
    if not doc_paths:
        return _fail(name, "受保护清单段里一条路径都抠不出来——列表结构变了（或路径没用"
                           "反引号包），此时对账面为空、本闸对任何漂移都恒绿")

    src = script.read_text(encoding="utf-8")

    def canon(p):
        return p[:-2] if p.endswith("/**") else p

    code_basenames = set()
    mb = re.search(r"^AUTO_UPDATE_BASENAME = (\w+)", src, re.M)
    if mb and mb.group(1) == "VETO6_BASENAME":
        m2 = re.search(r"^VETO6_BASENAME = \((.*?)^\)", src, re.M | re.S)
        if m2:
            for d, pre, suf in re.findall(r'\("([^"]*)",\s*"([^"]*)",\s*"([^"]*)"\)', m2.group(1)):
                code_basenames.add(f"{d}{pre}*{suf}")
    try:
        code_paths = (signoff_const_tuple(src, "AUTO_UPDATE_EXACT")
                      | signoff_const_tuple(src, "VETO6_EXACT")
                      | signoff_const_tuple(src, "AUTO_UPDATE_PREFIX") | code_basenames)
    except _ConstParseError as e:
        return _fail(name, f"脚本侧常量解析失败：{e}——**本闸此刻对任何漂移都恒绿**。这是闸自己坏了，不是对方缺条目；先修抽取，别照着红灯去改常量")

    doc_paths = {canon(p) for p in doc_paths}
    runtime_doc = {p for p in doc_paths
                   if p in ("<state>/", "shared/MEMORY.md")
                   or p.endswith("/shared/MEMORY.md")}
    doc_rest = doc_paths - runtime_doc          # 二元减：doc_paths 保持全集供文案报数

    errors = []
    if len(runtime_doc) != 2:
        errors.append(f"运行态那两条（`<state>/**` 与 shared 记忆）在清单里只找到 "
                      f"{sorted(runtime_doc)}——它们由 runtime_protected() 现算，"
                      f"清单里漏写不会让脚本失效，但读清单的人会以为它们不受保护")
    only_doc = sorted(doc_rest - code_paths)
    only_code = sorted(code_paths - doc_rest)
    if only_doc:
        errors.append(f"注入片有、脚本没有：{only_doc}——文档说它是受保护资产而机器不认，"
                      f"信号入账会直接写它（假绿）")
    if only_code:
        errors.append(f"脚本有、注入片没有：{only_code}——机器拦着而清单里查不到理由，"
                      f"读者只会觉得这道闸莫名其妙（假红，下场是被绕着走）")
    # 真子集不变量：它在同一个文件里分叉过一次，两张表都有闸之后这条是免费的
    veto = (signoff_const_tuple(src, "VETO6_EXACT")
            | signoff_const_tuple(src, "VETO6_PREFIX"))
    if not veto <= code_paths:
        errors.append(f"`VETO6_* ⊆ AUTO_UPDATE_*` 被破坏，差集 {sorted(veto - code_paths)}"
                      f"——两张表的关系是「封顶集是受保护集的真子集」，破了它意味着有路径"
                      f"会封顶到用户、却不在受保护清单里")
    if errors:
        return _fail(name, "; ".join(errors[:4]))
    return _ok(name, f"(注入片 {len(doc_paths)} 个路径字面量 / {n_rows} 个条目，与 "
                     f"signoff_tier_check 的 AUTO_UPDATE_* 同值；运行态 2 条由 "
                     f"runtime_protected 现算；VETO6 ⊆ AUTO_UPDATE 成立)")


# 导入判定**不在本文件里写**：形态在 `project_scaffold.md_path_imports`，位置剥离与两个
# 方向在 `workframe_doctor.agents_md_import_hits`。这里曾手抄过一份行首 `^@` 的副本，
# 与 doctor 那份同时错、同时没人对账（判据错成「只有行首才算导入」，而官方语义是行内任意处）。
# 本闸问的是「模板与生成物里有没有导入」，与 doctor `agents_md` 同一问，直接调它。

# 形态判定的自检夹具：(文本, 期望命中与否)。它钉的是**判据本身**，不是某份文件的现状——
# 现存文件全过是有偏样本（它们按旧规则写成），判据被改窄回「行首」时唯有这一格会红。
_IMPORT_SHAPE_FIXTURES = (
    ("@AGENTS.md", True),                                   # 行首
    ("项目判据见 @AGENTS.md 与本文件", True),                 # 行中——旧的行首判据在这里假绿
    ("- git workflow @docs/git-instructions.md", True),      # 官方示例：列表项中
    ("| 约定 | 见 @docs/rules.md |", True),                  # 表格单元里
    ("见 @./AGENTS.md", True),                               # 显式相对
    ("`@AGENTS.md`", False),                                 # code span：官方明写跳过
    ("```\n@AGENTS.md\n```", False),                         # 围栏：官方明写跳过
    ("| @prompt-eng | 主力 |", False),                       # 角色名，不是路径
    ("@dev 做技术方案", False),                               # 行首角色名——按「凡 @ 都算」会假红
    ("指定 `@角色名` 则强制委派", False),
    ("联系 a@b.com", False),                                 # 邮箱
)


# 带参数那条路径的哨兵：每个渲染进 AGENTS.md 的对话参数一个唯一值（都不以 `@` 开头，免得撞 preflight）
_AGENTS_MD_SENTINELS = {
    "one_line_goal": "哨兵GOAL7f3a", "business_context": "哨兵BIZ7f3a",
    "project_level_roles": "哨兵ROLES7f3a", "project_specific_constraints": "哨兵CONS7f3a",
    "business_directories": "哨兵DIRS7f3a",
}


def _goal_shape_fixtures(skel, goal, blank):
    """「这个项目是什么」占位判定（doctor `agents_md_goal_state`）的形态夹具：(标签, 文本, 期望)。

    底子是**真 scaffold 无参渲染的骨架** `skel`，一次只动一个变量——夹具钉的是判据的各个组成部分（只认 GOAL 常量 /
    剥注释 / 同名段全算 / 段止于 `#` `##` 而 `###` 在段内 / 标题只在围栏外认 / 逐行 `==` / 标题容行尾空白 / 空段算
    占位），每一部分被改坏都至少有一格翻。`goal` / `blank` 是 scaffold 的两个兜底常量，调用时现取。"""
    head = "## 这个项目是什么"

    def nth(text, old, new, n):
        i = -1
        for _ in range(n):
            i = text.find(old, i + 1)
        return text[:i] + new + text[i + len(old):]

    return (
        ("骨架原样", skel, "unfilled"),
        ("只填目标行", nth(skel, goal, "虚构目标", 1), "filled"),
        ("只填 ### 业务背景 行", nth(skel, goal, "虚构背景", 2), "filled"),
        ("两行占位都删掉（段里只剩注释与小标题）", skel.replace(goal, ""), "unfilled"),
        ("CRLF", skel.replace("\n", "\r\n"), "unfilled"),
        ("BOM", "﻿" + skel, "unfilled"),
        ("标题行尾两个空格", skel.replace(head + "\n", head + "  \n", 1), "unfilled"),
        ("标题带后缀", skel.replace(head, head + "（草稿）", 1), "unparsed"),
        ("前面围栏里有同名标题与正文（诱饵）", "```\n" + head + "\n\n虚构目标\n```\n\n" + skel, "unfilled"),
        ("文末追加一份有内容的同名段", skel + "\n" + head + "\n\n虚构目标\n", "filled"),
        ("目标只写在段内 HTML 注释里", nth(skel, goal, "<!-- 虚构目标 -->", 1), "unfilled"),
        ("标题前有没闭合的 <!--", skel.replace(head, "<!--\n" + head, 1), "unparsed"),
        ("占位两侧带空白", skel.replace(goal, "  " + goal + "  "), "unfilled"),
        ("占位后同一行接正文", nth(skel, goal, goal + " 虚构目标", 1), "filled"),
        ("只填项目自有判据、目标段仍是占位", skel.replace(blank, "虚构判据"), "unfilled"),
        ("目标写在围栏代码块里", nth(skel, goal, "```\n虚构目标\n```", 1), "filled"),
    )


def _agents_md_goal_contract_problems():
    """`AGENTS.md`「这个项目是什么」占位判定的**写入方 ↔ 判定方契约**（判定式在 doctor `agents_md_goal_state`，
    本闸不另写）：真 scaffold 无参渲染 ⇒ `unfilled`；带参渲染 ⇒ `filled`——带参分两样，五个参数全填的，与
    **判据字段留空**的（launcher 新建的默认形态，写出 `AGENTS_MD_FALLBACK_BLANK`「（暂无）」）；前一样里（暂无）出现
    0 次、打不死「把（暂无）当占位标记」这个回归，后一样打得死（以无参骨架为底的形态夹具与 doctor「已填」格里也有
    （暂无），同样会红；无参骨架那一格自身本来就判 `unfilled`、不翻——所以它不是唯一的一格，留着是因为它是 launcher
    新建路径的真实形态）。
    另两组：①形态夹具 `_goal_shape_fixtures`（判据各组成部分的活性证明）；②**改常量**：进程内把
    `AGENTS_MD_FALLBACK_GOAL` 换一个值、写入方与判定方同时生效，无参渲染仍须判 `unfilled`——判定方手抄字面时
    这一格翻成 `filled`（每份骨架都假绿）。改完在 `finally` 里复原：validate 单进程跑全部检查。
    模板改成「本项目：{{PROJECT_ONE_LINE_GOAL}}」这类写法时无参那一格当场红——不红的话每个补建项目都静默漏报。
    另钉 doctor `check_agents_md` 的消费面（warn / info / ok「已写」/ 判定异常降级 / 启发式异常时 warn 说「没数成」/
    出路按来源在 CLAUDE.md 还是只在 rules 分说 /「这个项目是什么」缺失时不判）与启发式 `cc_only_surfaces` 的四种失败方式。
    并进 `agents_md_self_contained`、不单列一项：同一份模板、同一个写入点，拆成两项只会让两边各自漂。返回问题清单。
    """
    try:
        doc = _doctor()
        ps = _scaffold()
        judge = doc.agents_md_goal_state
    except Exception as e:
        return [f"import doctor / scaffold 失败（占位判定与兜底常量在它们那里）: {e}"]
    problems = []
    tpl_path = FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "agents-md-template.md"
    tpl = tpl_path.read_text(encoding="utf-8")

    def render(proj, params=None):
        return ps.render_placeholders(tpl, ps.agents_md_mapping(proj, params), "agents-md-template.md")

    with tempfile.TemporaryDirectory() as td:
        proj = Path(td)
        (proj / ".workframe-config.json").write_text("{}", encoding="utf-8", newline="")
        try:
            skel = render(proj)
            full = render(proj, dict(ps.PARAM_DEFAULTS, **_AGENTS_MD_SENTINELS, project_name="probe"))
            blank_params = dict(ps.PARAM_DEFAULTS, **_AGENTS_MD_SENTINELS, project_name="probe")
            blank_params["project_specific_constraints"] = ""
            blank = render(proj, blank_params)
        except Exception as e:
            return [f"scaffold 渲染 AGENTS.md 失败：{type(e).__name__}: {e}"]
        for label, text, want in (("无参（补建 / 迁移 / 无参重跑）", skel, "unfilled"),
                                  ("带参、五个参数全填", full, "filled"),
                                  ("带参、判据字段留空", blank, "filled")):
            got = judge(text)
            if got != want:
                problems.append(f"[{label}] 判成 {got}、应为 {want}"
                                + ("——无参生成物不再被认作占位，每个补建项目都会静默漏报" if want == "unfilled"
                                   else "——每个新装项目的 doctor 都会误报一条 warn"))
        if ps.AGENTS_MD_FALLBACK_BLANK not in blank:
            problems.append("[带参、判据字段留空] 生成物里没有「（暂无）」——这一样本本来要用它打「把（暂无）也当占位」"
                            "的回归，没有它这一格就打不死那个回归")
        wrong = [f"{lbl}：期望 {w}、实得 {judge(t)}"
                 for lbl, t, w in _goal_shape_fixtures(skel, ps.AGENTS_MD_FALLBACK_GOAL, ps.AGENTS_MD_FALLBACK_BLANK)
                 if judge(t) != w]
        if wrong:
            problems.append(f"形态夹具 {len(wrong)} 格不符：{wrong[:3]}")
        saved = ps.AGENTS_MD_FALLBACK_GOAL
        try:
            ps.AGENTS_MD_FALLBACK_GOAL = saved + "改"
            got = judge(render(proj))
        finally:
            ps.AGENTS_MD_FALLBACK_GOAL = saved
        if got != "unfilled":
            problems.append(f"兜底常量换一个值后无参渲染判成 {got}——判定方没有引用 "
                            f"`project_scaffold.AGENTS_MD_FALLBACK_GOAL`（手抄了字面），写入方一改常量就对每份骨架假绿")
        # doctor `agents_md` 这一项的消费面：骨架 ⇒ warn（写明模型不代填；有 CC 专属面时给「可能的来源」、没有时给
        # 「写上」）；已填 ⇒ 无 warn、ok 行说「已写」；标题认不出 ⇒ info；判定抛异常 ⇒ info 且四段的 ok 结论照在
        (proj / "AGENTS.md").write_text(skel, encoding="utf-8", newline="")
        ctx = {"paths": doc.Paths(proj)}

        def levels(rows):
            return sorted({lv for lv, _m in rows})

        rows = doc.check_agents_md(ctx)
        warn = [m for lv, m in rows if lv == "warn"]
        if len(warn) != 1 or doc.AGENTS_MD_NO_PROXY_EDIT not in warn[0] or "写上这个项目" not in warn[0]:
            problems.append(f"[doctor 骨架、无 CC 专属面] 应恰一条 warn、写明「模型不代填」与「写上」出路，实得 {rows}")
        (proj / ".claude" / "rules" / "local").mkdir(parents=True)
        (proj / ".claude" / "rules" / "local" / "x.md").write_text("x\n", encoding="utf-8", newline="")
        warn = [m for lv, m in doc.check_agents_md(ctx) if lv == "warn"]
        if len(warn) != 1 or "可能的来源" not in warn[0] or ".claude/rules/local/x.md" not in warn[0]:
            problems.append(f"[doctor 骨架、有 CC 专属面] warn 应给「可能的来源」并点名那份 rules，实得 {warn}")
        # 来源只有 rules、项目根没有 CLAUDE.md：出路不许接 CLAUDE.md 那两句（它不存在）
        if warn and ("CLAUDE.md 整段" in warn[0] or "CLAUDE.md 里那几段" in warn[0]):
            problems.append(f"[doctor 来源只有 rules、没有 CLAUDE.md] warn 仍在说 CLAUDE.md 的整段复制与去留：{warn[0]}")
        if warn and "那几份 rules" not in warn[0]:
            problems.append(f"[doctor 来源只有 rules] warn 没说那几份 rules 的去留：{warn[0]}")
        (proj / "CLAUDE.md").write_text("# p\n\n## 项目目标\n\n虚构\n", encoding="utf-8", newline="")
        warn = [m for lv, m in doc.check_agents_md(ctx) if lv == "warn"]
        if not warn or "CLAUDE.md 整段" not in warn[0] or "CLAUDE.md 里那几段" not in warn[0]:
            problems.append(f"[doctor 来源含 CLAUDE.md 模板外段] warn 应带「别把 CLAUDE.md 整段复制」与那几段的去留，实得 {warn}")
        (proj / "CLAUDE.md").unlink()
        # 启发式出异常：warn 照出、写明「没数成」、给通用出路；不静默退回「写上」，也不让整项变成「检查自身异常」
        saved_src = doc.cc_only_surfaces
        try:
            doc.cc_only_surfaces = lambda _p: (_ for _ in ()).throw(OSError("stub 读不出"))
            rows = doc.check_agents_md(ctx)
        except Exception as e:
            rows = [("error", f"<外抛 {type(e).__name__}>")]
        finally:
            doc.cc_only_surfaces = saved_src
        warn = [m for lv, m in rows if lv == "warn"]
        if levels(rows) != ["ok", "warn"] or len(warn) != 1:
            problems.append(f"[doctor 启发式抛异常] 应为 ok ＋ 恰一条 warn（异常不外抛、整项不变成检查自身异常），实得 {rows}")
        elif "没数成" not in warn[0] or "写上这个项目做什么、给谁用、处在什么阶段" in warn[0]:
            problems.append(f"[doctor 启发式抛异常] warn 应写明「没数成」、不静默退回「写上」，实得 {warn[0]}")
        # 「这个项目是什么」本身缺失：已由 error 报出，占位这一判不再跑（不多出一条 info / warn）
        (proj / "AGENTS.md").write_text(skel.replace("## 这个项目是什么", "## 别的标题", 1), encoding="utf-8", newline="")
        if levels(doc.check_agents_md(ctx)) != ["error"]:
            problems.append(f"[doctor「这个项目是什么」缺失] 应只有缺段 error、不判占位，实得 {doc.check_agents_md(ctx)}")
        (proj / "AGENTS.md").write_text(skel.replace(ps.AGENTS_MD_FALLBACK_GOAL, "虚构目标", 1),
                                        encoding="utf-8", newline="")
        rows = doc.check_agents_md(ctx)
        if levels(rows) != ["ok"] or "已写" not in rows[0][1]:
            problems.append(f"[doctor 已填] 应只有 ok 且说「已写」，实得 {rows}")
        (proj / "AGENTS.md").write_text(skel.replace("## 这个项目是什么", "## 这个项目是什么（草稿）", 1),
                                        encoding="utf-8", newline="")
        if levels(doc.check_agents_md(ctx)) != ["info", "ok"]:
            problems.append(f"[doctor 标题认不出] 应为 ok ＋ info（没判），实得 {doc.check_agents_md(ctx)}")
        (proj / "AGENTS.md").write_text(skel, encoding="utf-8", newline="")
        saved_judge = doc.agents_md_goal_state
        try:
            doc.agents_md_goal_state = lambda _t: (_ for _ in ()).throw(ValueError("stub"))
            rows = doc.check_agents_md(ctx)
        finally:
            doc.agents_md_goal_state = saved_judge
        if levels(rows) != ["info", "ok"]:
            problems.append(f"[doctor 判定抛异常] 应降成 info、四段的 ok 结论照在，实得 {rows}")
    # 启发式那一半（doctor `cc_only_surfaces`，doctor warn 的「可能的来源」与门的 i 行共用）的四种失败方式：
    # 模板标题现算而非写死 / 纪律镜像目录不算 / 没有 CLAUDE.md 不崩 / 框架段落旧副本排在项目标题之后
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        fake_plugin = root / "plugin"
        (fake_plugin / "templates").mkdir(parents=True)
        real_tpl = (FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "claude-md-template.md").read_text(encoding="utf-8")
        grown = real_tpl + "\n## 模板新加的一段\n\n框架样板\n"
        (fake_plugin / "templates" / "claude-md-template.md").write_text(grown, encoding="utf-8", newline="")
        proj = root / "proj"
        proj.mkdir()
        try:
            got = doc.cc_only_surfaces(proj)
            if got != ([], []):
                problems.append(f"[启发式 没有 CLAUDE.md、没有 rules] 应为 ([], [])，实得 {got}")
            (proj / "CLAUDE.md").write_text(grown, encoding="utf-8", newline="")
            saved_root = doc.PLUGIN_ROOT
            try:
                doc.PLUGIN_ROOT = fake_plugin
                got = doc.cc_only_surfaces(proj)[0]
            finally:
                doc.PLUGIN_ROOT = saved_root
            if got:
                problems.append(f"[启发式 模板加了一段] 照模板写的 CLAUDE.md 仍报模板外二级段 {got}——模板标题不是现算的，"
                                f"模板一改每个新项目都报「只有 CC 读得到的内容」")
            (proj / ".claude" / "rules" / "workframe" / "core").mkdir(parents=True)
            (proj / ".claude" / "rules" / "workframe" / "core" / "a.md").write_text("x\n", encoding="utf-8", newline="")
            if doc.cc_only_surfaces(proj)[1]:
                problems.append("[启发式 只有纪律镜像目录] 镜像目录里的 md 被当成项目的 CC 专属内容（它由 rules_mirror_residue 另报）")
            (proj / "CLAUDE.md").write_text("# p\n\n## 角色体系\n\n| @pm | 需求 |\n\n## 项目目标\n\n虚构\n",
                                            encoding="utf-8", newline="")
            got = doc.cc_only_surfaces(proj)[0]
            if got != ["项目目标", "角色体系"]:
                problems.append(f"[启发式 排序] 框架段落旧副本应排在项目标题之后，实得 {got}")
        except Exception as e:
            problems.append(f"[启发式] cc_only_surfaces 抛异常：{type(e).__name__}: {e}")
    return problems


def check_agents_md_self_contained():
    """`AGENTS.md` 模板与 scaffold 真写出来的那一份：正文零 `@<file>` 导入、四个契约段齐全、
    **零 `{{[A-Z_]+}}` 残留**；带对话参数时**每个参数值都真的落进了 AGENTS.md**。

    **两个扫描面缺一不可**：只扫模板的话，生成路径里任何一处拼接都能把导入行加回去；
    只扫生成物的话，模板改坏了要等下一次装机才暴露。所以这里跑真 scaffold 到临时目录。
    **两条路径各跑一次**：无参数（显式重跑 / SessionStart 补建走的那条）直调
    `ensure_project_scaffold`；带参数（新建）走真 `main()`（子进程跑 `--params`）——AGENTS.md
    的写入点只有一个，但「main 把 params 传没传到它」只有跑 main 才看得见。只查零 `{{` 不够：
    写入点被改回原样复制时占位符会留下，而在无参数兜底渲染路径上占位符全消失、**参数却被静默
    丢掉**，所以另逐个断言哨兵在位。
    **`CLAUDE.md` 有意不在导入扫描面内**——那一行 `@AGENTS.md` 是 CC 侧的正确用法。
    **判定式不在本文件**：调 `workframe_doctor.agents_md_import_hits`（形态那一半再往下是
    `project_scaffold.md_path_imports`），与 doctor 的 `agents_md` 同一份。判据是「路径形在
    行内任意处」——官方 `memory.md:101` 写的是 `@ syntax anywhere`，按行首判会漏掉行中导入。
    另跑一组 `_IMPORT_SHAPE_FIXTURES` 自检：模板与生成物恰好干净时，那条判定式即使被改坏
    也不会有任何一格红，夹具是它唯一的活性证明。
    """
    import os
    import subprocess
    name = "agents_md_self_contained"
    tpl = FRAMEWORK_ROOT / "plugins" / "core" / "templates" / "agents-md-template.md"
    if not tpl.is_file():
        return _fail(name, "agents-md-template.md 缺失——scaffold 的补建路径无源可渲染")
    try:
        import_hits = _doctor().agents_md_import_hits
    except Exception as e:
        return _fail(name, f"import doctor 失败（导入判定的唯一实现在它那里）: {e}")
    problems = []
    wrong = [f"{t!r} 期望{'命中' if w else '不命中'}、实得 {import_hits(t)}"
             for t, w in _IMPORT_SHAPE_FIXTURES if bool(import_hits(t)) != w]
    if wrong:
        problems.append(f"导入形态判定有 {len(wrong)} 格不符：{wrong[:3]}——判错成不命中是"
                        f"「AGENTS.md 里真有导入却报绿」，判错成命中是每个提到角色名的项目常年假红")
    hits = import_hits(tpl.read_text(encoding="utf-8"))
    if hits:
        problems.append(f"模板正文有 {len(hits)} 处 `@<path>` 导入（{hits[:3]}）")

    def judge(label, gen, sentinels=()):
        hits2 = import_hits(gen)
        if hits2:
            problems.append(f"[{label}] 生成物正文有 {len(hits2)} 处 `@<path>` 导入（{hits2[:3]}）")
        miss = [s for s in ("这个项目是什么", "项目自有判据", "委派子 agent", "两扇门的差异")
                if f"## {s}" not in gen]
        if miss:
            problems.append(f"[{label}] 生成物缺契约段 {miss}——doctor 的 agents_md 会在每个新装"
                            f"项目上报 error，而根因在框架侧的模板")
        left = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", gen)))
        if left:
            problems.append(f"[{label}] 生成物留着占位符 {left}——字面 `{{{{…}}}}` 随每个新项目的 "
                            f"AGENTS.md 两扇门每会话都载（写入点多半被改回了原样复制模板）")
        lost = [s for s in sentinels if s not in gen]
        if lost:
            problems.append(f"[{label}] 对话参数没落进 AGENTS.md：{lost}——用户在装机对话里填的"
                            f"内容被静默丢掉、退出码仍是 0（写入点没拿到 params，或出现了第二个写入点）")

    scaffold = _scaffold()
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "probe"
        proj.mkdir()
        (proj / ".workframe-config.json").write_text("{}", encoding="utf-8", newline="")
        try:
            scaffold.ensure_project_scaffold(proj)
        except Exception as e:
            return _fail(name, f"scaffold 补建路径跑不起来：{type(e).__name__}: {e}")
        out = proj / "AGENTS.md"
        if not out.is_file():
            problems.append("[无参数] scaffold 没有写出 AGENTS.md——补建路径没接上，"
                            "新建项目与已装项目都拿不到它，而 doctor 的 agents_md 会在"
                            "**用户项目**里才报出来")
        else:
            judge("无参数", out.read_text(encoding="utf-8"))

        params = dict(_AGENTS_MD_SENTINELS, project_name="probe", role_profile="solo-pm")
        pf = Path(td) / "params.json"
        pf.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8", newline="")
        proj2 = Path(td) / "probe-params"
        script = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        try:
            r = subprocess.run([sys.executable, str(script), "--project", str(proj2),
                                "--params", str(pf), "--create-missing"],
                               capture_output=True, text=True, timeout=90,
                               encoding="utf-8", errors="replace", env=env)
        except Exception as e:
            return _fail(name, f"scaffold --params 路径无法执行: {type(e).__name__}: {e}")
        out2 = proj2 / "AGENTS.md"
        if r.returncode != 0 or not out2.is_file():
            problems.append(f"[带参数] scaffold --params 退出码 {r.returncode}、AGENTS.md "
                            f"{'在' if out2.is_file() else '没写出来'}：{r.stdout.strip()[-160:]}")
        else:
            judge("带参数", out2.read_text(encoding="utf-8"),
                  list(_AGENTS_MD_SENTINELS.values()) + ["profile: solo-pm"])
    goal_problems = _agents_md_goal_contract_problems()
    if problems:
        return _fail(name, "；".join(problems) + "。有一扇门不展开 `@<path>`，那行字面串会"
                                                "原样进上下文，两扇门读到的内容就此不同且都不报错"
                     + ("；另「这个项目是什么」占位契约：" + "；".join(goal_problems) if goal_problems else ""))
    if goal_problems:
        return _fail(name, "「这个项目是什么」占位契约：" + "；".join(goal_problems))
    return _ok(name, f"(导入形态判定 {len(_IMPORT_SHAPE_FIXTURES)} 格自检通过；模板 + 无参数 / "
                     f"带参数两条 scaffold 路径的生成物均无 `@<path>` 导入、四段齐全、"
                     f"零占位符残留，带参数时 5 个哨兵与所选路由档全部落进 AGENTS.md；"
                     f"「这个项目是什么」占位判定：无参 ⇒ unfilled、带参两样 ⇒ filled、形态夹具 "
                     f"{len(_goal_shape_fixtures('', '', ''))} 格、改常量后仍认得出骨架)")


def check_sub_shard_event_binding():
    """每条注入挂载的事件必须与它的 scope 对得上——**两个方向都钉，不只钉 sub**。

    **错挂的失效是静默的**：hook 照常执行、marker 照常落地、`--check` 全绿。
    实测依据：同一次会话里 `SessionStart` 注的令牌在父 transcript 各 4 命中、**子 agent
    transcript 各 0 命中**；`SubagentStart` 注的令牌父 6 / 子 4。两层注入各管各的。

    **为什么原来只钉一半是漏**：两侧是同一个契约（scope ↔ event）的两半。实测把
    `--scope main --shard 2/3` 整条挪到 `SubagentStart`，整轮 validate 仍全绿，而
    `context_shards_within_cap` 的 OK 文案照旧写「声明数与现算片数一致」——
    `_hooks_shard_declarations()` 明明收了事件名，那条闸却 `for _e, _i, n` 当场丢弃。

    **映射表不在本文件里另写一张**：直接 import 打包器的 `SCOPE_EVENT`，它是唯一事实源；
    另写一张就是第二个手写副本，而本批交付的正是「两处手写必漂」那条纪律。

    **两侧红灯文案分开写**：错挂的后果不同，合并措辞会把人引向错误的排查方向——读到红灯的人
    会照着文案去查，文案说错了半边，他就去查了错的那半。

    **扫描面 = 两份清单**。Codex 清单多一种 scope：`--scope prompt` 只能挂 `UserPromptSubmit`
    ——review 型子线程上 `SessionStart` / `SubagentStart` 都不触发、只有它触发；挂错则那类线程
    整段无纪律。映射同样取自打包器的 `SCOPE_EVENT`。"""
    name = "sub_shard_event_binding"
    ic = _inject_context()
    problems, seen, by = [], 0, {}
    for door in HOOK_MANIFEST_RELS:
        try:
            data = _hook_manifest(door)
        except Exception as e:
            return _fail(name, f"[{door}] 清单读取/解析失败: {e}")
        for event, groups in (data.get("hooks") or {}).items():
            for grp in groups or []:
                for h in grp.get("hooks") or []:
                    cmd = h.get("command") or ""
                    m_scope = (re.search(r"--scope\s+(\w+)", cmd)
                               if "inject-context.py" in cmd else None)
                    is_memory = "subagent-memory-inject.py" in cmd
                    if not (m_scope or is_memory):
                        continue
                    seen += 1
                    key = m_scope.group(1) if m_scope else "memory"
                    by[f"{door}/{key}"] = by.get(f"{door}/{key}", 0) + 1
                    if is_memory:
                        want, scope = "SubagentStart", None
                    else:
                        scope = m_scope.group(1)
                        want = ic.SCOPE_EVENT.get(scope)
                        if want is None:
                            problems.append(
                                f"[{door}] 挂载表声明了 `--scope {scope}`，但打包器不认识这个 scope"
                                f"——它只认 {sorted(ic.SCOPE_EVENT)}；这条 hook 每次启动都会失败")
                            continue
                    if event == want:
                        continue
                    if scope == "main":
                        problems.append(
                            f"[{door}] 主会话片 `--scope main` 挂在 `{event}` 上，必须是 `{want}`："
                            f"**主会话少收一整片纪律**，而片头仍写着 `i/n`、没有任何机器面读它；"
                            f"子 agent 那边则多收一片不属于它的内容。hook 照跑、片头照发、"
                            f"整轮 validate 全绿——失效与「模型偶尔不守纪律」同形")
                        continue
                    if scope == ic.PROMPT_SCOPE:
                        problems.append(
                            f"[{door}] Codex 纪律判定 hook `--scope prompt` 挂在 `{event}` 上，必须是 "
                            f"`{want}`：review 型子线程上只有 `{want}` 触发，挂别处则那类线程"
                            f"**整段无纪律**、与模型犯错同形")
                        continue
                    what = "sub 纪律片" if scope == "sub" else "角色记忆注入"
                    problems.append(
                        f"[{door}] {what} 挂在 `{event}` 上，必须是 `{want}`：**子 agent 一个字"
                        f"都收不到**——SessionStart 的注入不传给子 agent，而 hook 会照常执行、"
                        f"marker 照常落地、`--check` 全绿，失效与「模型偶尔不守纪律」同形")
    if problems:
        return _fail(name, f"{len(problems)} 项：\n    " + "\n    ".join(problems))
    if seen == 0:
        return _fail(name, "两份清单里一条注入挂载都没有（既无 `--scope` 的纪律片、"
                           "也无角色记忆注入）——本断言没有对象等于恒绿，而此刻两侧都是零注入")
    shown = "、".join(f"{k}×{v}" for k, v in sorted(by.items()))
    return _ok(name, f"({seen} 条注入挂载的事件与 scope 全部对得上（{shown}）；"
                     f"映射表取自打包器的 SCOPE_EVENT，本文件不另写一张)")


# ---- Codex 清单：静态面九条（派生 / 冻结 / 形态 / stdin / SessionStart 输出量）----

def _codex_entries_or_fail(name):
    """(codex 清单 dict, 条目列表) 或 (None, _fail)。"""
    try:
        data = _hook_manifest("codex")
        return (data, _codex_hooks().entries(data)), None
    except Exception as e:
        return None, _fail(name, f"hooks.codex.json 读取/解析失败: {e}")


def check_codex_plugin_manifest_twin():
    """每个插件的 `.codex-plugin/plugin.json`：`name` / `version` 与同目录 `.claude-plugin/plugin.json`
    相等，`hooks` 与该插件自己的两份 hook 清单对得上。**core 那一份是硬要求，其余插件有则查。**

    **只有 `.claude-plugin` 时 Codex 照读 CC 那份清单并执行它**（实测）：CC 清单没有 `commandWindows`，
    每条都是「引号路径开头」，Windows 下全部 PowerShell parse error，而 Codex 对 hook 执行失败零告警
    ——所以 Codex 清单一旦进仓，这份孪生清单必须同在、且指向它。版本不等 = 两扇门各报一个版本号，
    升级对账（`plugin-root` 两根版本相等）从一开始就对不上。

    **落点现扫，不写死 `plugins/core`**：路径写死时，给第二个插件加一份 `.codex-plugin/plugin.json`
    就凭空多出一个**没有任何闸读它的 `name`** 的落点——两扇门会把同一份代码认成两个插件，而不会红。
    现扫之后，新插件一加进来就自动进对账面。
    **`version` 那一半今天是交叉冗余**：`check_version_consistency` 已现扫 `PLUGIN_MANIFEST_RELS`
    的全部形态，本闸仍从 `.claude-plugin` **独立**推出期望值——两处手写是有意的，任一处被改松时
    另一处仍会红。

    **`hooks` 按该插件有没有 CC 清单分档**，不是无条件必填：
      · `hooks/hooks.json` 在 ⇒ `hooks` 必须指向 `hooks/hooks.codex.json` 且该文件在（否则 Codex
        退回去读 CC 那份，Windows 下全部 parse error 且零告警）；
      · CC 清单不在（零 hook 的插件，launcher 今天就是）⇒ `hooks` 必须缺席。写着 `hooks` 却没有
        源清单时，Codex 按一份没有生成源的清单加载它，而 `codex_hooks_generated_in_sync` 只对账
        core 那一份、看不见它。

    **不覆盖**：某个插件该不该有 `.codex-plugin`（core 之外没有这条判据——加不加是形态选型），
    以及 Codex 真机是否按这份 manifest 认出了插件（那要真机，在发版检查单第 7 组）。
    """
    name = "codex_plugin_manifest_twin"
    plugins_dir = FRAMEWORK_ROOT / "plugins"
    core_cx = plugins_dir / "core" / ".codex-plugin" / "plugin.json"
    if not core_cx.exists():
        return _fail(name, f"{_rel(core_cx)} 不存在——Codex 会退回去读 CC 的 .claude-plugin 与 hooks.json，"
                           f"其命令在 Windows 下全部 parse error 且零告警")
    ch = _codex_hooks()
    want = "./" + ch.CODEX_MANIFEST_REL
    checked = []
    for pdir in sorted(p for p in plugins_dir.iterdir() if p.is_dir()):
        cx_p = pdir / ".codex-plugin" / "plugin.json"
        if not cx_p.exists():
            continue
        cc_p = pdir / ".claude-plugin" / "plugin.json"
        if not cc_p.exists():
            return _fail(name, f"{_rel(cx_p)} 在而 {_rel(cc_p)} 不在——这个插件只在 Codex 一侧有身份，"
                               f"CC 门下它不是插件，两扇门的版本号也就无从对账")
        try:
            cc = json.loads(cc_p.read_text(encoding="utf-8"))
            cx = json.loads(cx_p.read_text(encoding="utf-8"))
        except Exception as e:
            return _fail(name, f"{pdir.name} 的两份 plugin.json 解析失败: {e}")
        # 两个字段的后果不同，红灯文案就分开写——共用一条时，`name` 不等会打出一段讲 version 的话，
        # 读的人按它去查发版锁步，而真正坏掉的是插件身份。
        if cc.get("name") != cx.get("name"):
            return _fail(name, f"{pdir.name} 两份 plugin.json 的 `name` 不等："
                               f"claude={cc.get('name')!r} / codex={cx.get('name')!r}"
                               f"——**两扇门会把同一份代码认成两个插件**：项目里的启用表、用户层的启用键、"
                               f"插件缓存目录都按这个 id 分叉")
        if cc.get("version") != cx.get("version"):
            return _fail(name, f"{pdir.name} 两份 plugin.json 的 `version` 不等："
                               f"claude={cc.get('version')!r} / codex={cx.get('version')!r}"
                               f"——**两扇门各报一个版本号**，升级对账（两根 `plugin-root.<门>.txt` 的版本相等）"
                               f"从一开始就对不上；它同时是锁步发版的落点")
        cc_manifest = pdir / ch.CC_MANIFEST_REL
        cx_manifest = pdir / ch.CODEX_MANIFEST_REL
        hooks_ref = cx.get("hooks")
        if cc_manifest.exists():
            if hooks_ref != want:
                return _fail(name, f"{pdir.name} 的 .codex-plugin/plugin.json 的 `hooks` 是 {hooks_ref!r}，"
                                   f"应为 {want!r}——指错 / 缺失时 Codex 读到的不是派生清单")
            if not cx_manifest.exists():
                return _fail(name, f"`hooks` 指向的 {_rel(cx_manifest)} 不存在——插件在 Codex 下零 hook")
            note = f"hooks → {want}"
        else:
            if hooks_ref is not None:
                return _fail(name, f"{pdir.name} 没有 {ch.CC_MANIFEST_REL}，而 .codex-plugin/plugin.json 里"
                                   f"仍写着 `hooks` = {hooks_ref!r}——Codex 会按一份没有生成源的清单加载它，"
                                   f"而派生对账闸只看 core 那一份、看不见这里")
            note = f"无 hook（{ch.CC_MANIFEST_REL} 也不在）"
        checked.append(f"{pdir.name} v{cx.get('version')}（{note}）")
    return _ok(name, f"({len(checked)} 份 .codex-plugin 清单现扫对账：" + "；".join(checked) + ")")


def check_codex_hooks_generated_in_sync():
    """`hooks.codex.json` 必须等于「此刻从 `hooks.json` 重跑生成器」的产物（行尾归一后逐字节相同）。

    **只管清单，不管 manifest**：manifest 是人维护的冻结基线，由 `codex_hook_lines_frozen` 对账。
    抓两类：手改了产物（产物是派生物，手改会在下次重生成时被覆盖）；改了 `hooks.json` 没重生成
    （Codex 门下那条改动整段缺席）。
    """
    name = "codex_hooks_generated_in_sync"
    ch = _codex_hooks()
    try:
        want = ch.render(ch.derive(_hook_manifest("cc"))).encode("utf-8")
    except Exception as e:
        return _fail(name, f"从 hooks.json 重跑派生失败: {e}")
    p = _hook_manifest_path("codex")
    if not p.exists():
        return _fail(name, f"{HOOK_MANIFEST_RELS['codex']} 不存在——跑 gen_codex_hooks.py 生成并提交")
    got = p.read_bytes().replace(b"\r\n", b"\n")
    if got != want:
        return _fail(name, f"{HOOK_MANIFEST_RELS['codex']} 与从 hooks.json 重跑生成器的产物不同"
                           f"（{len(got)} vs {len(want)} 字节）——要么产物被手改（会在重生成时被覆盖），"
                           f"要么 hooks.json 改了没重生成（那条改动在 Codex 门下整段缺席）；"
                           f"跑 gen_codex_hooks.py 后提交")
    return _ok(name, f"(重跑生成器逐字节相同，{ch.count_hooks(json.loads(want))} 条)")


def check_codex_hooks_count():
    """manifest 条数 == 清单条数 == **直接从 hooks.json 算出的独立值**。

    第三个数不经生成器（`_codex_hooks.independent_count`：CC 总条数 − 缺口事件条数 − 各 scope
    合成掉的片数 ＋ 追加条数，片数由打包器现算），才能抓「生成器少走一步」——生成器与它自己比恒等。
    """
    name = "codex_hooks_count"
    ch = _codex_hooks()
    ic = _inject_context()
    res, fail = _codex_entries_or_fail(name)
    if fail:
        return fail
    codex, rows = res
    try:
        manifest = json.loads((FRAMEWORK_ROOT / "plugins" / "core" / ch.CODEX_BASELINE_REL)
                              .read_text(encoding="utf-8"))
        n_manifest = len(manifest.get("entries") or [])
    except Exception as e:
        return _fail(name, f"读 {ch.CODEX_BASELINE_REL} 失败: {e}")
    try:
        n_expected = ch.independent_count(
            _hook_manifest("cc"), lambda scope: len(ic.pack(scope)[0]))
    except Exception as e:
        return _fail(name, f"独立计数失败: {e}")
    n_list = len(rows)
    if not (n_manifest == n_list == n_expected):
        return _fail(name, f"三个数不等：manifest {n_manifest} / 清单 {n_list} / 从 hooks.json 独立算出 "
                           f"{n_expected}——清单 ≠ 独立值是生成器少走或多走了一步（某条 hook 在 Codex 门下"
                           f"缺席或重复）；manifest ≠ 清单是有条目未经 --accept-manifest 进基线")
    if n_list < 10:
        return _fail(name, f"三个数相等但只有 {n_list} 条（应 ≥10）——清单与基线一起被清空时本闸假绿")
    return _ok(name, f"(manifest = 清单 = 独立计数 = {n_list})")


def check_codex_hook_lines_frozen():
    """现清单每条的 hash 覆盖面（组级 `matcher` ＋ `HOOK_HASH_FIELDS`）与 manifest 逐字段相等；
    key 序为 manifest 的前缀（只许末尾追加）；`frozen: true` 那条任何字段变化单独点名。

    **这道闸的地位**：Codex 按这些字段算信任 hash，任一字段变了用户升级后该条 `modified`、
    静默停跑，直到有人重批。有意改动的正道是 `gen_codex_hooks.py --accept-manifest` 并在对外
    CHANGELOG 未发布段写「升级后需重批 N 条」——本闸把「改了 hooks.json、重生成、没 accept」拦下。
    **Windows 上 `commandWindows` 在场时 POSIX `command` 不入 hash**，那一行漂了 Codex 自己不会报，
    本闸是唯一防线；所以 `command` 与 `commandWindows` 各自逐字比、不合并。
    """
    name = "codex_hook_lines_frozen"
    ch = _codex_hooks()
    res, fail = _codex_entries_or_fail(name)
    if fail:
        return fail
    _codex, rows = res
    try:
        manifest = json.loads((FRAMEWORK_ROOT / "plugins" / "core" / ch.CODEX_BASELINE_REL)
                              .read_text(encoding="utf-8"))
    except Exception as e:
        return _fail(name, f"读 {ch.CODEX_BASELINE_REL} 失败: {e}")
    base = manifest.get("entries") or []
    if not base:
        return _fail(name, "manifest 没有任何条目——基线为空时本闸没有对象，恒绿")
    if list(manifest.get("fields") or []) != list(ch.GROUP_HASH_FIELDS + ch.HOOK_HASH_FIELDS):
        return _fail(name, f"manifest 的 fields 与 _codex_hooks 的字段集不同：{manifest.get('fields')!r} "
                           f"vs {list(ch.GROUP_HASH_FIELDS + ch.HOOK_HASH_FIELDS)!r}——字段集变了要重新 accept")
    cur_keys = [r["key"] for r in rows]
    base_keys = [r.get("key") for r in base]
    if cur_keys[:len(base_keys)] != base_keys:
        first = next((i for i, (a, b) in enumerate(zip(cur_keys, base_keys)) if a != b), min(len(cur_keys), len(base_keys)))
        return _fail(name, f"现清单的 key 序不以 manifest 为前缀（第 {first} 处起：清单 "
                           f"{cur_keys[first] if first < len(cur_keys) else '<缺>'} / manifest "
                           f"{base_keys[first] if first < len(base_keys) else '<缺>'}）——中插、交换或删除会让"
                           f"其后每条的 key 撞上别人的信任记录，用户升级后整组 `modified` 静默停跑；"
                           f"新增只许追加末尾")
    by_key = {r["key"]: r for r in rows}
    # `frozen: true` 集合必须等于按 `_codex_hooks.is_frozen()` 现算的集合：manifest 里的标记被
    # 去掉后，改种信任 hook 的命令行仍会红，但文案退化成普通「1 条…」——冻结条目的特殊文案
    # 正是读者判「这一改动是在改整套升级机制」的唯一提示（@qa Q-2）。
    marked = {b.get("key") for b in base if b.get("frozen")}
    computed = {r["key"] for r in rows if ch.is_frozen(r)} | {b.get("key") for b in base
                                                               if ch.is_frozen(b)}
    if marked != computed:
        return _fail(name, f"manifest 的 frozen 集合 {sorted(marked)} ≠ 按 _codex_hooks.FROZEN_SCRIPTS 现算的 "
                           f"{sorted(computed)}——标记丢了，改种信任 hook 命令行时本闸只报普通字段差异，"
                           f"读者看不出那是在改整套升级机制；跑 --accept-manifest 重新接受")
    changed, frozen_hit = [], []

    def _differs(x, y):
        # 类型也比：`False == 0`，`additionalContextLimit: false` 会穿过 `==`（@qa Q-3）
        return x != y or type(x) is not type(y)

    for b in base:
        cur = by_key.get(b.get("key"))
        diff = [f for f in ch.GROUP_HASH_FIELDS + ch.HOOK_HASH_FIELDS if _differs(cur.get(f), b.get(f))]
        if not diff:
            continue
        label = f"{b.get('key')}: {'/'.join(diff)}"
        (frozen_hit if b.get("frozen") else changed).append(label)
    if frozen_hit:
        return _fail(name, f"**命令行冻结的种信任 hook 被改了**（{'; '.join(frozen_hit)}）——它在用户机器上"
                           f"会变成 `modified` 而不再执行，此时没有任何东西能替用户补种其余 hook 的信任；"
                           f"改它 = 改整套升级机制，须单独审")
    if changed:
        return _fail(name, f"{len(changed)} 条的 hash 覆盖面字段与 manifest 不同（{'; '.join(changed[:4])}）"
                           f"——该条升级后在用户侧 `modified` 静默停跑，Codex 自己不报（POSIX `command` 在 "
                           f"Windows 上甚至不入 hash）；有意改动跑 gen_codex_hooks.py --accept-manifest 并在"
                           f"对外 CHANGELOG 未发布段写「升级后需重批 N 条」")
    extra = cur_keys[len(base_keys):]
    if extra:
        return _fail(name, f"{len(extra)} 条追加在清单末尾但未进 manifest（{'、'.join(extra[:3])}）——追加合法，"
                           f"但要 --accept-manifest 并写「升级后需重批 {len(extra)} 条」")
    return _ok(name, f"({len(base)} 条与冻结基线逐字段相等，key 序为其前缀；frozen 条目 "
                     f"{sum(1 for b in base if b.get('frozen'))} 条未变)")


def check_codex_hook_command_windows_shape():
    """Codex 清单每条：⓪ `command` 非空（**第一条子断言**）；`commandWindows` ① 以
    `& "${CLAUDE_PLUGIN_ROOT}/bin/workframe-python.cmd"` 开头 ② 正斜杠、无反斜杠 ③ 无 `py -3`、
    无解释器绝对路径。

    ⓪ 防的失效比其余大一个量级：`command` 是 Codex 侧必填字段，整份清单一次性反序列化，一条缺它
    **整份清单静默蒸发**——不是少这一条，是同文件全部 hook 一起消失且零告警（实测）。
    ① 不以 `& "` 开头 = PowerShell 把引号路径当表达式，parse error、该条启动失败零告警；
    ② 反斜杠版会让 launcher 正则当场红、且在 JSON 里要双写；③ `py -3` 依赖 py 启动器，
    解释器绝对路径带版本 / 机器差异，两者都是「在作者机器上能跑」。
    """
    name = "codex_hook_command_windows_shape"
    ch = _codex_hooks()
    res, fail = _codex_entries_or_fail(name)
    if fail:
        return fail
    _codex, rows = res
    if len(rows) < 10:
        return _fail(name, f"Codex 清单只有 {len(rows)} 条（应 ≥10）——被清空时本闸假绿")
    for r in rows:
        where = r["key"]
        cmd = r.get("command")
        if not isinstance(cmd, str) or not cmd.strip():
            return _fail(name, f"{where}: `command` 缺失或为空——Codex 侧它是必填字段，整份清单会被"
                               f"**静默丢弃**：不是少这一条，是同文件里全部 hook 一起消失，且运行时零告警")
        win = r.get("commandWindows")
        if not isinstance(win, str) or not win.startswith(ch.LAUNCHER_WINDOWS + " "):
            return _fail(name, f"{where}: `commandWindows` 不以 `{ch.LAUNCHER_WINDOWS}` 开头（{win!r}）"
                               f"——PowerShell 下引号路径开头是 parse error，该条启动失败且零告警；"
                               f"缺 `.cmd` 则落进隐式 PATHEXT 解析，同一命令在不同机器上结果相反")
        if "\\" in win:
            return _fail(name, f"{where}: `commandWindows` 含反斜杠（{win!r}）——PowerShell 接受正斜杠，"
                               f"反斜杠在 JSON 里要双写、launcher 正则也不认")
        if re.search(r"(?:^|\s|&\s*)py\s+-3\b", win) or re.search(r"(?:^|\s|&\s*)\"?[A-Za-z]:/", win) \
                or re.search(r"(?:^|\s|&\s*)\"?/usr/", win):
            return _fail(name, f"{where}: `commandWindows` 用了 `py -3` 或解释器绝对路径（{win!r}）"
                               f"——只在作者机器上能跑；一律经 workframe-python.cmd")
    return _ok(name, f"({len(rows)} 条 command 非空、commandWindows 形态合规)")


def check_codex_limit_zero_on_injectors():
    """注入类事件（`SessionStart` / `UserPromptSubmit` / `SubagentStart`）每条 `additionalContextLimit == 0`；
    其他事件**不得带**该键。

    前半：不关 spill，超阈值的纪律片会整条换成一句 warning ＋ 落盘路径、头部 0 字节保留（实测），
    模型一个字都拿不到。后半**严于实测**：`Stop` / `SubagentStop` / `SessionEnd` 带它实测是 `hooks/list`
    报 `ignoring additionalContextLimit … this event cannot emit additionalContext` 并忽略该字段（条目保留、
    limit 回显 null），`PostToolUse` 静默接受（limit 回显 0）——都不是「被拒」；本闸仍一律禁，因为该字段在
    非注入事件上没有对象、只会制造一条随版本可能变义的 warning——方向 fail-safe（多禁一格，代价是将来
    真要给别的事件开 spill 时改本闸），别把它读成「带了会炸」。
    """
    name = "codex_limit_zero_on_injectors"
    ch = _codex_hooks()
    res, fail = _codex_entries_or_fail(name)
    if fail:
        return fail
    _codex, rows = res
    injectors = 0
    for r in rows:
        lim = r.get("additionalContextLimit")
        if r["event"] in ch.CODEX_INJECTOR_EVENTS:
            injectors += 1
            if lim != 0 or type(lim) is not int:      # `False == 0`：布尔会穿过 `==`（@qa Q-3）
                return _fail(name, f"{r['key']}: 注入类事件的 additionalContextLimit 是 {lim!r}，必须为 0"
                                   f"——不关 spill 时超阈值的纪律片整条换成 warning ＋ 落盘路径，模型拿到 0 字节")
        elif lim is not None:
            return _fail(name, f"{r['key']}: 非注入事件 `{r['event']}` 带了 additionalContextLimit"
                               f"（非注入事件上该字段无对象：`Stop` / `SubagentStop` / `SessionEnd` 实测被 Codex 忽略并报 warning，"
                               f"`PostToolUse` 静默接受；本闸按 fail-safe 一律禁）")
    if injectors < 3:
        return _fail(name, f"只有 {injectors} 条注入类条目（应 ≥3：main / prompt / sub 各至少一条）")
    return _ok(name, f"({injectors} 条注入类 limit=0，其余 {len(rows) - injectors} 条不带该键)")


def check_codex_injector_shard_all():
    """Codex 清单里凡 `inject-context.py` 且 scope 为 `main` / `sub` 的条目必须 `--shard all`，不许 `i/n`。

    替代「`context_shards_within_cap` 扩到 Codex」——那条恒真（两门六片 sha256 逐片相同，harness
    值不影响装箱）。这里有真对象：Codex 一条 hook 投全片，写成 `i/n` 就只投一片、其余源静默不到达。
    """
    name = "codex_injector_shard_all"
    ch = _codex_hooks()
    res, fail = _codex_entries_or_fail(name)
    if fail:
        return fail
    _codex, rows = res
    seen = 0
    for r in rows:
        cmd = r.get("command") or ""
        if ch.INJECTOR_SCRIPT not in cmd:
            continue
        m = re.search(r"--scope\s+(\w+)", cmd)
        if not m or m.group(1) not in ("main", "sub"):
            continue
        seen += 1
        if re.search(r"--shard\s+\d+/\d+", cmd) or ch.SHARD_ALL_TOKEN not in cmd:
            return _fail(name, f"{r['key']}: Codex 清单的 `--scope {m.group(1)}` 条目不是 `--shard all`"
                               f"（{cmd[-40:]!r}）——只投得出一片，其余源静默不到达；Codex 一条 hook 投全片")
    if seen < 2:
        return _fail(name, f"只找到 {seen} 条 main/sub 注入挂载（应 ≥2）——本断言没有对象等于恒绿")
    return _ok(name, f"({seen} 条 main/sub 注入挂载全部 `--shard all`)")


def _stdin_usage(src):
    """一份脚本对 stdin 的用法（AST）：返回 (text_reads, bytes_reads, wrapped, decoded)。

    text_reads：`sys.stdin.read*()` / `json.load(sys.stdin)` / `for … in sys.stdin` 的行号；
    bytes_reads：`sys.stdin.buffer.read*()` 的行号；
    wrapped：有没有 `sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8"…)` 或
             `sys.stdin.reconfigure(encoding="utf-8"…)`；
    decoded：有没有任何 `.decode("utf-8"…)` 调用（bytes 读的解码事实）。
    """
    tree = ast.parse(src)

    def is_sys_stdin(n):
        return (isinstance(n, ast.Attribute) and n.attr == "stdin"
                and isinstance(n.value, ast.Name) and n.value.id == "sys")

    def is_stdin_buffer(n):
        return isinstance(n, ast.Attribute) and n.attr == "buffer" and is_sys_stdin(n.value)

    def utf8_kw(call):
        for kw in call.keywords:
            if kw.arg == "encoding" and isinstance(kw.value, ast.Constant) \
                    and str(kw.value.value).lower().replace("_", "-").startswith("utf-8"):
                return True
        return False

    text_reads, bytes_reads, wrapped, decoded = [], [], False, False
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr in ("read", "readline", "readlines"):
                if is_sys_stdin(f.value):
                    text_reads.append(n.lineno)
                elif is_stdin_buffer(f.value):
                    bytes_reads.append(n.lineno)
            if isinstance(f, ast.Attribute) and f.attr == "load" and isinstance(f.value, ast.Name) \
                    and f.value.id == "json" and n.args and is_sys_stdin(n.args[0]):
                text_reads.append(n.lineno)
            if isinstance(f, ast.Attribute) and f.attr == "reconfigure" and is_sys_stdin(f.value) \
                    and utf8_kw(n):
                wrapped = True
            if isinstance(f, ast.Attribute) and f.attr == "decode" and n.args \
                    and isinstance(n.args[0], ast.Constant) \
                    and str(n.args[0].value).lower().replace("_", "-").startswith("utf-8"):
                decoded = True
        elif isinstance(n, (ast.For, ast.AsyncFor)) and is_sys_stdin(n.iter):
            text_reads.append(n.lineno)
        elif isinstance(n, ast.Assign) and any(is_sys_stdin(t) for t in n.targets) \
                and isinstance(n.value, ast.Call):
            fn = n.value.func
            fname = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if fname == "TextIOWrapper" and n.value.args and is_stdin_buffer(n.value.args[0]) \
                    and utf8_kw(n.value):
                wrapped = True
    return text_reads, bytes_reads, wrapped, decoded


# 读了就扔、不解析内容的 hook 脚本：stdin 解码与否不影响任何行为，显式豁免。
_STDIN_UTF8_EXEMPT = {"maintenance_workorder.py"}
# hook 载荷读取器所在模块：不在任何清单里，但 Codex 门下 13 个脚本的守卫经它读 stdin（`hook_payload_bytes`），
# 它解码错了，整条守卫把真实项目判成「在项目外」——所以显式并进扫描面。
_STDIN_READER_MODULE = "_harness.py"


def check_hook_stdin_utf8():
    """凡读 stdin 的 hook 脚本，其 stdin **要么已被 UTF-8 包装**（`sys.stdin = io.TextIOWrapper(
    sys.stdin.buffer, encoding="utf-8"…)` / `sys.stdin.reconfigure(encoding="utf-8"…)`），
    **要么以 bytes 读入后显式 `.decode("utf-8"…)`**。钉的是解码事实，不钉调用语法。

    为什么：Windows 上 PowerShell 起的 python 其 stdin 默认按本机 ANSI 代码页解码（`chcp 65001`
    不改变它，launcher 的 `PYTHONUTF8=1` 是第二层、不是脚本的替身），而 hook 载荷是 UTF-8——
    含中文的 `cwd` 会变成另一串字符：状态目录建到一棵错的树下、`--scope prompt` 的整条判定式读到
    错的 `transcript_path` / `session_id`、review 型子线程一个字都收不到，且 hook exit 0 零告警。
    CC 侧有 `CLAUDE_PROJECT_DIR` 掩盖 `cwd` 那一半，Codex 侧没有。

    **扫描面 = 两份 hook 清单引用到的全部脚本（闭包，不列名单）＋ 载荷读取器所在模块 `_harness.py`**
    （Codex 门下守卫经它读 stdin；它必须被判出读点，否则红）；`maintenance_workorder.py`
    显式豁免（读了就扔）；`signoff_tier_check.py` 不在面内（CLI 面，不挂 hook）。
    **能力边界**：bytes 读那半只看「文件里有没有 `.decode("utf-8")`」，不证明解码的是那次读到的
    字节；text 读那半只看「文件里有没有包装语句」，不证明包装先于读。两者都是形态判定，
    比它们更严的写法会在 `check-stale-modules.py`（读与解码分两行）上造假红。
    """
    name = "hook_stdin_utf8"
    scripts = set()
    try:
        for door in HOOK_MANIFEST_RELS:
            for _e, _m, _f, cmd in _hook_field_entries(door):
                for m in re.finditer(r"scripts/([A-Za-z0-9_.-]+\.py)", cmd):
                    scripts.add(m.group(1))
    except Exception as e:
        return _fail(name, f"hook 清单读取/解析失败: {e}")
    if len(scripts) < 10:
        return _fail(name, f"两份清单只引用到 {len(scripts)} 个脚本（应 ≥10）——扫描面缩水时本闸假绿")
    scripts.add(_STDIN_READER_MODULE)
    scripts_dir = FRAMEWORK_ROOT / "plugins" / "core" / "scripts"
    offenders, readers = [], 0
    for fname in sorted(scripts):
        if fname in _STDIN_UTF8_EXEMPT:
            continue
        p = scripts_dir / fname
        if not p.exists():
            offenders.append(f"{fname}: 清单引用了但文件不存在")
            continue
        try:
            text_reads, bytes_reads, wrapped, decoded = _stdin_usage(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            offenders.append(f"{fname}: 语法错误，stdin 用法无法判定: {e}")
            continue
        if not text_reads and not bytes_reads:
            if fname == _STDIN_READER_MODULE:
                offenders.append(f"{fname}: 读取器所在模块里找不到 stdin 读点——Codex 门下守卫经它读载荷，"
                                 f"扫描面扩到它却判不到读点，等于没扩")
            continue
        readers += 1
        if text_reads and not wrapped:
            offenders.append(f"{fname}:{text_reads[0]} 文本读 stdin 而未做 UTF-8 包装")
        if bytes_reads and not decoded:
            offenders.append(f"{fname}:{bytes_reads[0]} bytes 读 stdin 而全文无 .decode('utf-8')")
    if offenders:
        return _fail(name, f"{len(offenders)} 处 stdin 解码不成立——Codex 门下（PowerShell 起进程）含中文路径"
                           f"的载荷会 mojibake，状态目录建错树、prompt 判定式读错线程号，hook 照样 exit 0: "
                           + "；".join(offenders[:5]))
    if readers < 3:
        return _fail(name, f"扫描面里只有 {readers} 个脚本在读 stdin（应 ≥3）——判定式失明时本闸假绿")
    return _ok(name, f"({readers} 个读 stdin 的 hook 脚本全部 UTF-8 解码；扫描面 {len(scripts)} 个脚本，"
                     f"豁免 {sorted(_STDIN_UTF8_EXEMPT)})")


def check_codex_door_complete():
    """`.codex-plugin/plugin.json` 在 ⇒ `bin/workframe-door` ＋ `.cmd` 在、`scripts/workframe_door.py`
    可编译且被入口引用。

    守的是一个曾经真实存在过的中间态：仓里已有 `.codex-plugin` 与 17 条 Codex 清单、却没有装机门
    ——Codex 用户装上就是 17/17 `untrusted`、一条不跑、非交互通道零信号，而对外看不出任何异常。
    `.codex-plugin` 是 Codex 认插件的入口，它在而装机门不在，就是「能装、装了不跑」。

    **本闸只判 `plugins/core`，这是判过的、不是漏泛化**（隔壁 `codex_plugin_manifest_twin` 已改成
    现扫全部插件）：本闸的命题是「带 hook 的插件必须同时带上那道装机门」，而装机门**全仓只该有
    一个**——它写的是用户配置与项目配置，不是某个插件的私产。零 hook 的插件（launcher 今天就是）
    根本没有「装了不跑」这一态，把本闸泛化过去只会要求每个插件各带一份 `bin/workframe-door`。
    """
    name = "codex_door_complete"
    core = FRAMEWORK_ROOT / "plugins" / "core"
    if not (core / ".codex-plugin" / "plugin.json").exists():
        return _ok(name, "(无 .codex-plugin，本闸无对象)")
    missing = [p for p in ("bin/workframe-door", "bin/workframe-door.cmd", "scripts/workframe_door.py")
               if not (core / p).exists()]
    if missing:
        return _fail(name, f".codex-plugin/plugin.json 在而 {missing} 不在——Codex 用户装上本插件后全部 hook "
                           f"`untrusted`、一条不跑，且非交互通道零信号；装机门与 .codex-plugin 必须同在")
    entry = (core / "bin" / "workframe-door").read_text(encoding="utf-8")
    if "from workframe_door import main" not in entry:
        return _fail(name, "bin/workframe-door 不再引用 scripts/workframe_door.py 的 main——入口与实现脱钩")
    return _ok(name, "(.codex-plugin 与 workframe-door 同在)")


def check_codex_door_unit_tests_pass():
    """装机器 / 角色生成器 / 种信任 hook 单测：跑 tools/test_workframe_door.py。

    被测对象都是「判断别的东西对不对」的东西（对账、退出码、账本规则、补种分支），按档 4 处置。
    套件不起 codex——app-server 那半用假服务器喂 `hooks/list` 形态，真 Codex 上的行为归沙盒端到端。这一点是结构性的：
    套件在 import 时把 `WF_CODEX_EXE` 指向不存在的路径、`CODEX_HOME` 指向临时目录（`test_workframe_migrate.py` 同样），
    被测代码里的守卫回归时走到的是「codex 未找到」，而不是调用方机器上的 codex；所以本闸不另给子进程注入这两个变量。
    组数与断言以测试文件自身为准，此处不复述计数。
    """
    name = "codex_door_unit_tests_pass"
    import os
    test_path = FRAMEWORK_ROOT / "tools" / "test_workframe_door.py"
    if not test_path.exists():
        return _fail(name, "tools/test_workframe_door.py missing")
    try:
        result = subprocess.run([sys.executable, str(test_path)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=180,
                                env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    except Exception as e:
        return _fail(name, f"run error: {e}")
    if result.returncode != 0:
        return _fail(name, f"test exit={result.returncode}: {_suite_failure_summary(result.stdout + result.stderr)}")
    if "all passed" not in result.stdout:
        return _fail(name, "套件退出 0 但没有打出 all passed——半途退出与通过同形，按未通过处理")
    return _ok(name, "(test suite passed)")


# ---- 两门孪生对账（hooks / roles）：**不经生成器**的第二个独立视角 ----
#
# Codex 清单在 CC 清单之外**应当**多出的两条。显式列举、**不从 `_codex_hooks.CODEX_EXTRA` 取**：
# 本闸的价值在于它从 CC 清单独立推出期望集合——生成器规则改错时（比如 CODEX_EXTRA 被多加一条），
# `codex_hooks_generated_in_sync` 只会证明「产物与生成器一致」，唯一还能红的是这里。两处手写是有意的
# 交叉冗余；改一处必须改另一处，这条闸红就是提醒。
_TWIN_CODEX_ONLY = (
    ("UserPromptSubmit", "", "scripts/inject-context.py", "--scope prompt"),
    ("SessionStart", "", "scripts/codex-trust-seed.py", ""),
)
_TWIN_LAUNCHER = "${CLAUDE_PLUGIN_ROOT}/bin/workframe-python"     # shlex 去引号后的 argv[0]
_TWIN_ROOT_PREFIX = "${CLAUDE_PLUGIN_ROOT}/"


def _twin_normalize(command):
    """一条 hook 命令 → (脚本的插件根相对路径, 规范化固定参数, 观测到的 `--harness` 值列表, `--output hook-json` 次数)。

    规范化规则（与派生规则 2 / 3 / 7 一一对应，但**独立实现**、不 import `derive()`）：
      · 去掉 launcher（须恰为 `_TWIN_LAUNCHER`，否则「认不出」）与脚本路径；脚本以**插件根相对路径**
        保留（`scripts/x.py`），不是 basename——换目录的同名脚本才不会静默对上；
      · `--harness <x>` / `--harness=<x>` 全部剥掉并把值记下来，由调用方断言「恰一次且是本门」；
      · `--shard i/n` 与 `--shard all` 都折叠成 `--shard *`（CC 多片、Codex 一条，折叠后才可比）；
      · `--output hook-json` 剥掉并计数，由调用方断言它只落在该落的那两条上。
    认不出的形态抛 ValueError——报「解析不了」而不是把它当成一条空四元组。
    """
    import shlex
    argv = shlex.split(command)
    if len(argv) < 2 or argv[0] != _TWIN_LAUNCHER or not argv[1].startswith(_TWIN_ROOT_PREFIX):
        raise ValueError(f"命令形态认不出（须以 launcher ＋ 插件根相对脚本路径开头）: {command!r}")
    script = argv[1][len(_TWIN_ROOT_PREFIX):]
    rest, out, harness_vals, hook_json = argv[2:], [], [], 0
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--harness":
            harness_vals.append(rest[i + 1] if i + 1 < len(rest) else "")
            i += 2
            continue
        if a.startswith("--harness="):
            harness_vals.append(a.split("=", 1)[1])
            i += 1
            continue
        if a == "--shard":
            out += ["--shard", "*"]
            i += 2
            continue
        if a == "--output" and i + 1 < len(rest) and rest[i + 1] == "hook-json":
            hook_json += 1
            i += 2
            continue
        out.append(a)
        i += 1
    return script, " ".join(out), harness_vals, hook_json


def _twin_rows(data, door):
    """一份清单 → 逐条 dict（event / matcher / script / args / harness / hook_json / where / key）。
    事件名不是 PascalCase、`command` 不是字符串、命令认不出——三种都抛，调用方转 `_fail`。"""
    rows = []
    for event, groups in (data.get("hooks") or {}).items():
        if not re.fullmatch(r"[A-Z][A-Za-z]*", event):
            raise ValueError(f"[{door}] 事件名 {event!r} 不是 PascalCase——Codex / CC 都按精确字面匹配事件名，"
                             f"写错大小写的那一组静默不挂到任何事件上")
        for gi, grp in enumerate(groups or []):
            matcher = grp.get("matcher", "")
            for hi, h in enumerate(grp.get("hooks") or []):
                cmd = h.get("command")
                if not isinstance(cmd, str):
                    raise ValueError(f"[{door}] {event}({matcher or '*'})#{gi}.{hi}: `command` 不是字符串"
                                     f"（{cmd!r}）")
                script, args, hv, hj = _twin_normalize(cmd)
                rows.append({"event": event, "matcher": matcher, "script": script, "args": args,
                             "harness": hv, "hook_json": hj,
                             "key": (event, matcher, script, args),
                             "where": f"[{door}] {event}({matcher or '*'})#{gi}.{hi}"})
    return rows


def _twin_label(key):
    event, matcher, script, args = key
    return f"{event}/{matcher or '*'}/{script}" + (f" {args}" if args else "")


def check_hooks_twin_parity():
    """两门 hook 清单在「事件 × matcher × 脚本 × 固定参数」这个**语义面**上对等（四元组多重集 `==`）。

    **对账单位是四元组，不是脚本集合**：`signoff-guard.py` 同时挂在 `PostToolUse(Bash)` 与
    `PostToolUse(Edit|Write|NotebookEdit)`，按脚本集合对账时删掉其中一条仍绿——那条 hook 在 Codex 门下
    就永不执行，与正常同形。

      expected := multiset{ tuple(e) | e ∈ CC 清单, e.event ∉ CODEX_ABSENT_EVENTS }，
                  其中分片挂载（`--shard *`）折叠为 1；⊎ `_TWIN_CODEX_ONLY`
      actual   := multiset{ tuple(e) | e ∈ Codex 清单 }
      断言 actual == expected —— Counter 不是 set：复制一条 Codex 条目必须红（Codex 会跑两遍）。

    子断言：Codex 每条 `--harness` 恰一次且为 `codex`、零次 `cc`；CC 每条恰一次 `cc`（值被剥掉再比，
    不断言的话 Codex 清单里留一条 `--harness cc` 会被放行）；`--output hook-json` 只出现在 `SessionStart`
    上 `HOOK_JSON_SCRIPTS` 那两条、各恰一次；事件名 PascalCase；`command` 为字符串。

    **与既有闸的分工**：`codex_hooks_generated_in_sync` 证「产物 == 生成器现跑」——生成器规则改错时它跟着
    错；本闸从 CC 清单**独立**推期望（`CODEX_ABSENT_EVENTS` / `HOOK_JSON_SCRIPTS` 只读常量，`derive()` 不
    import，CODEX_ONLY 自己列举），是第二个视角。`codex_hooks_count` 只比条数；`hook_commands_declare_harness`
    已管两份清单的 `--harness` 值——本闸的同款子断言与它双覆盖，留着是因为本闸自称独立视角、自己得看得见。
    `command` 非空那半由 `codex_hook_command_windows_shape` ⓪ 管，这里只判类型。

    **本闸看不见的三处，闸绿不改变它们的披露**：① launcher 与 `commandWindows` 那半的形态（由
    `codex_hook_command_windows_shape` 与 launcher 闸管；本闸把脚本按插件根相对路径比，换目录会红，
    但 launcher 只判「认不认得出」）；② 组内顺序（Counter 不比序；由 `codex_hook_lines_frozen` 的
    key 序前缀管）；③ **matcher 字面相等 ≠ 命中面相等**——`PostToolUse` 四条的 matcher 是 CC 工具名
    （`Bash` / `Edit|Write|NotebookEdit` / `Skill`），在 Codex 是否命中未验（要一次带凭据的工具调用才验得了）。对等的是声明面。
    """
    name = "hooks_twin_parity"
    from collections import Counter
    ch = _codex_hooks()
    try:
        cc_rows = _twin_rows(_hook_manifest("cc"), "cc")
        cx_rows = _twin_rows(_hook_manifest("codex"), "codex")
    except Exception as e:
        return _fail(name, f"清单读取 / 解析失败: {e}")
    if len(cc_rows) < 10 or len(cx_rows) < 10:
        return _fail(name, f"CC {len(cc_rows)} 条 / Codex {len(cx_rows)} 条（各应 ≥10）——CC 清单被清空时期望集只剩 "
                           f"_TWIN_CODEX_ONLY 那 {len(_TWIN_CODEX_ONLY)} 条，Codex 清单恰好也只剩它们就能「多重集相等」；"
                           f"两份一起清空的形态即便没有这道下限也会被子断言 B 或 missing 族接住，这道下限兜的是前一种")
    # 子断言 A：门标识——值已被剥掉，不在这里断言就等于放行 `--harness cc` 混进 Codex 清单
    for door, rows in (("cc", cc_rows), ("codex", cx_rows)):
        for r in rows:
            if r["harness"] != [door]:
                return _fail(name, f"{r['where']}: `--harness` 观测到 {r['harness']!r}，应恰一次且为 `{door}`"
                                   f"——规范化剥掉门标识后四元组照样对得上，这条 hook 产出的事件却会归到错误的门下"
                                   f"（或落成 unknown），按门分支的脚本在错的门上执行")
    # 子断言 B：`--output hook-json` 的落点——非纯文本脚本加它会把本来的 JSON 输出再包一层
    want_json = {(ch.HOOK_JSON_EVENT, f"scripts/{s}") for s in ch.HOOK_JSON_SCRIPTS}
    seen_json = Counter()
    for r in cx_rows:
        slot = (r["event"], r["script"])
        if r["hook_json"] and slot not in want_json:
            return _fail(name, f"{r['where']}: 带了 `--output hook-json`，但它不是 SessionStart 上的纯文本脚本"
                               f"（HOOK_JSON_SCRIPTS = {list(ch.HOOK_JSON_SCRIPTS)}）——把纯文本脚本以外的输出"
                               f"包成 hook-json 会吞掉它本来的 JSON（hookSpecificOutput 整段丢失）")
        if r["hook_json"]:
            seen_json[slot] += r["hook_json"]
    for slot in sorted(want_json):
        if seen_json.get(slot, 0) != 1:
            return _fail(name, f"Codex 清单里 {slot[0]}/{slot[1]} 的 `--output hook-json` 出现 {seen_json.get(slot, 0)} 次"
                               f"（应恰 1）——缺了：Codex 把它以 `[` 起头的纯文本 stdout 当 JSON 解析失败、整条丢弃并记"
                               f" failed；多了：参数重复，argparse 取最后一个，看不出但形态已漂")
    # 主断言：四元组多重集
    expected = Counter()
    for r in cc_rows:
        if r["event"] in ch.CODEX_ABSENT_EVENTS:
            continue
        if "--shard *" in r["args"]:
            expected[r["key"]] = 1          # 分片折叠：CC 多片 → Codex 一条
        else:
            expected[r["key"]] += 1
    for k in _TWIN_CODEX_ONLY:
        expected[k] += 1
    actual = Counter(r["key"] for r in cx_rows)
    missing = [k for k in expected if actual.get(k, 0) < expected[k]]
    extra = [k for k in actual if k not in expected]
    dup = [k for k in actual if k in expected and actual[k] > expected[k]]
    if missing:
        k = sorted(missing)[0]
        return _fail(name, f"Codex 表少了 {len(missing)} 条交集内的挂载，首条 `{_twin_label(k)}`（CC 侧 {expected[k]} 条 / "
                           f"Codex 侧 {actual.get(k, 0)} 条）——该 hook 在 Codex 门下永不执行，而 `hooks/list` 全 trusted、"
                           f"--check 全绿，失效与正常同形")
    if extra:
        k = sorted(extra)[0]
        return _fail(name, f"Codex 表多出 {len(extra)} 条 CC 侧没有、也不在 CODEX_ONLY 里的挂载，首条 "
                           f"`{_twin_label(k)}`——两门行为不对等：这条只在 Codex 门下跑，CC 用户永远看不到它的效果；"
                           f"若它是新的 Codex 专有 hook，同时把 _TWIN_CODEX_ONLY 与 _codex_hooks.CODEX_EXTRA 各加一条")
    if dup:
        k = sorted(dup)[0]
        return _fail(name, f"同一挂载 `{_twin_label(k)}` 在 Codex 表出现 {actual[k]} 次（应 {expected[k]}）——Codex 会跑 "
                           f"{actual[k]} 遍：注入类重复投递同一片、写事件的重复记账、种信任的重复批")
    return _ok(name, f"({len(cx_rows)} 条四元组与从 hooks.json 独立推出的期望多重集相等，含 {len(_TWIN_CODEX_ONLY)} 条 "
                     f"Codex 专有；对等的是声明面（事件 × matcher × 脚本 × 固定参数）——PostToolUse 四条 CC matcher "
                     f"在 Codex 的命中面未验，本闸绿不改变那条披露)")


_ROLE_SAMPLE_CACHE = {}


def _gen_codex_roles_sample():
    """import `tools/gen_codex_roles_sample.py`——样本渲染契约的**唯一实现**（重出脚本与本闸共用）。
    闸不另写一份渲染调用：两处各传一遍 `(version, local_extra)`，改一边不改另一边时闸会替错的那份背书。"""
    if "mod" not in _ROLE_SAMPLE_CACHE:
        import importlib.util
        src = FRAMEWORK_ROOT / "tools" / "gen_codex_roles_sample.py"
        spec = importlib.util.spec_from_file_location("_wf_gen_codex_roles_sample", src)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _ROLE_SAMPLE_CACHE["mod"] = mod
    return _ROLE_SAMPLE_CACHE["mod"]


def check_role_twin_parity():
    """角色生成器对 `agents/*.md` 现渲染（`version="SAMPLE"`、无定制、LF）== `tools/fixtures/codex-roles-sample/`
    逐字节（**行尾归一后**）；角色集合 == 样本集合。

    防的是**生成器漂移**：`codex_roles.py` 的渲染改了（头标措辞、分隔符、转义规则），或 `agents/<role>.md`
    改了没重出样本——两者在用户侧的表现相同：下次 `workframe-door --upgrade` 重生成 `.codex/roles/<role>.toml`，
    Codex 门的角色正文静默变了，而 CC 门读的还是 `agents/*.md`，两门角色不再是同一份。
    `test_workframe_door.test_roles` 证的是「同输入两次渲染相同」（确定性），本闸证的是「与基线一致」（防漂）——
    两件事，分工不重叠。

    **行尾归一是契约的一部分，不是可报的差异**：Windows `autocrlf` 检出的样本是 CRLF、CI 的 Linux runner 是
    LF，不归一同一份样本会一边红一边绿；归一后相同即绿、零输出。
    唯一重出入口 `tools/gen_codex_roles_sample.py`（`scripts/**` 冻结，入口只能落 `tools/`）。
    """
    name = "role_twin_parity"
    try:
        gen = _gen_codex_roles_sample()
        rendered = gen.render_sample(FRAMEWORK_ROOT / "plugins" / "core")
    except Exception as e:
        return _fail(name, f"渲染当前 agents/*.md 失败: {e}")
    sample_dir = FRAMEWORK_ROOT / "tools" / "fixtures" / "codex-roles-sample"
    if not sample_dir.is_dir():
        return _fail(name, f"{_rel(sample_dir)} 不存在——没有基线本闸无对象；跑 tools/gen_codex_roles_sample.py 生成并提交")
    role_files = {k for k in rendered if k != gen.BLOCK_FILE}
    sample_files = {p.name for p in sample_dir.glob("*.toml")}
    if len(role_files) < 1:
        return _fail(name, "渲染出 0 个角色——agents/ 为空时本闸无对象")
    if gen.BLOCK_FILE not in sample_files:
        return _fail(name, f"样本缺 {gen.BLOCK_FILE}（config.toml managed 段的基线）——跑 tools/gen_codex_roles_sample.py")
    missing = sorted(role_files - sample_files)
    extra = sorted(sample_files - role_files - {gen.BLOCK_FILE})
    if missing:
        return _fail(name, f"样本少了角色 {missing}——agents/ 新增了角色但没重出样本，该角色在基线里无对照、"
                           f"渲染漂了也没人看见；跑 tools/gen_codex_roles_sample.py 并提交")
    if extra:
        return _fail(name, f"样本多出角色 {extra}——agents/ 里已没有它（被删或改名），旧样本会让人以为 Codex 门仍生成"
                           f"这个角色；重出样本后自行删掉旧文件")
    for fname in sorted(rendered):
        want = rendered[fname].encode("utf-8")
        got = (sample_dir / fname).read_bytes().replace(b"\r\n", b"\n")
        if got == want:
            continue
        w_lines, g_lines = want.split(b"\n"), got.split(b"\n")
        first = next((i + 1 for i, (a, b) in enumerate(zip(w_lines, g_lines)) if a != b),
                     min(len(w_lines), len(g_lines)) + 1)
        if fname == gen.BLOCK_FILE:
            cause = "某个 agents/<role>.md 的 description（或角色集合）改了没重出样本"
            role = "config-block"
        else:
            role = fname[:-len(".toml")]
            cause = f"agents/{role}.md 改了没重出样本"
        return _fail(name, f"生成物与仓内样本不同（{role}，首个差异行 {first}）——要么 {cause}"
                           f"（跑 tools/gen_codex_roles_sample.py），要么渲染器 codex_roles.py 漂了；有意改动请重出样本"
                           f"并在对外 CHANGELOG 未发布段写「升级后 Codex 角色配置变化」")
    return _ok(name, f"({len(role_files)} 个角色 ＋ {gen.BLOCK_FILE} 与样本逐字节相同（行尾归一后），version=SAMPLE)")


def check_project_skills_path_unit_tests_pass():
    """项目 skills 到达路径单测：跑 tools/test_project_skills_path.py。

    被测的是「项目 skill 落在哪、`.gitignore` 怎么忽略 `.claude/skills`」的判定与 scaffold / SessionStart 两条
    补建路径、悬空链接的自动重指、SessionStart 在补建 / 重指那一次改投的 JSON 形态、doctor `skills_link` 的分级，
    以及忽略写法四个产出点的整行字面，按档 4 处置：判错时装机 exit 0、doctor 全绿，只有 Claude Code 门看不到
    项目 skill。形态与格子以测试文件自身为准，此处不复述计数。
    """
    name = "project_skills_path_unit_tests_pass"
    import os
    test_path = FRAMEWORK_ROOT / "tools" / "test_project_skills_path.py"
    if not test_path.exists():
        return _fail(name, "tools/test_project_skills_path.py missing")
    try:
        result = subprocess.run([sys.executable, str(test_path)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=180,
                                env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    except Exception as e:
        return _fail(name, f"run error: {e}")
    if result.returncode != 0:
        return _fail(name, f"test exit={result.returncode}: {_suite_failure_summary(result.stdout + result.stderr)}")
    if "all passed" not in result.stdout:
        return _fail(name, "套件退出 0 但没有打出 all passed——半途退出与通过同形，按未通过处理")
    return _ok(name, "(test suite passed)")


def _run_suite_with_floor(name, rel, floor, extra=""):
    """跑一个 `tools/test_*.py` 套件，并**要求它至少打出 `floor` 条断言行**。

    **为什么要下限**：`*_unit_tests_pass` 这个族此前只看「退出码 0 ＋ 打了 all passed」——
    把套件 `main()` 里的调用全换成 `pass`，它照样打 `✓ …(test suite passed)`（独立验证方实证）。
    一个被掏空的套件与一个真在干活的套件在闸这一层长得一模一样，而这两个套件守的是
    **爆炸半径档 4** 的新代码（写受保护资产）——它们是那段代码唯一的常驻保护。

    **下限是下限，不是计数**：取一个远低于当前实际值的数，日常加断言不用动它，
    只有「套件被整段摘掉」这种量级的变化才会撞线。所以它**不承诺**「断言一条没少」，
    只承诺「套件没有被掏空」——这条能力边界写在这里，免得读者把它当成计数对账。

    **能力边界之二：本仓只有走本函数的那几个套件有下限，其余的没有。** `*_unit_tests_pass`
    这个族里**其余 10 条仍是「退出 0 ＋ all passed」形态**，掏空它们照样绿。
    **族级改造不是把这个函数套上去就行**——把本函数的计数口径（行首 `[ok]` / `[FAIL]`）
    施用到全族实测过：**12 条里有 6 条根本不打这个形态的行**（`test_check_stale_modules` /
    `test_module_close_check` / `test_project_scaffold_catalog` / `test_signoff_guard` /
    `test_signoff_tier_check` / `test_workframe_door` 的读数都是 0）。⇒ 要么改那些套件的输出形态，
    要么给它们另配计数谓词，两条路都不是十行复制粘贴。这组读数是「只给新套件加下限、
    不动整族」那个取舍的依据，写在这里是因为**它此前只活在一份按纪律要被删掉的过程档里**。
    """
    import os
    test_path = FRAMEWORK_ROOT / rel
    if not test_path.exists():
        return _fail(name, f"{rel} missing")
    try:
        result = subprocess.run([sys.executable, str(test_path)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=300,
                                env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    except Exception as e:
        return _fail(name, f"run error: {e}")
    if result.returncode != 0:
        return _fail(name, f"test exit={result.returncode}: {_suite_failure_summary(result.stdout + result.stderr)}")
    if "all passed" not in result.stdout:
        return _fail(name, "套件退出 0 但没有打出 all passed——半途退出与通过同形，按未通过处理")
    n = sum(1 for ln in result.stdout.splitlines() if ln.strip().startswith(("[ok]", "[FAIL]")))
    if n < floor:
        return _fail(name, f"套件只打出 {n} 条断言行，低于下限 {floor}——套件被掏空时"
                           f"「退出 0 ＋ all passed」与真的跑过一模一样，本条下限就是为这个形态设的")
    return _ok(name, f"(test suite passed, {n} 条断言行 ≥ 下限 {floor}{extra})")


def check_settings_io_unit_tests_pass():
    """settings 读 / merge / 写单测：跑 tools/test_settings_io.py。

    被测的 `_settings_io` 是**框架第一次用代码写 settings** 的那份实现（装机链路的订阅声明
    ＋ 用户级市场注册表），按档 4 处置：merge 退化成覆盖时装机照样 exit 0、doctor 也未必红，
    而用户 `.claude/settings.json` 里原有的 `hooks` / `permissions.allow` 已经没了——那正是
    受保护资产表里「改错框架整个不加载」的同一格。读路径还是 doctor 与 `workframe-door --cc`
    共用的 BOM 口径，两边曾各写各的。形态与格子以测试文件自身为准，此处不复述计数
    （下限的语义见 `_run_suite_with_floor`：它挡的是「套件被掏空」，不是「断言一条没少」）。
    """
    return _run_suite_with_floor("settings_io_unit_tests_pass",
                                 Path("tools") / "test_settings_io.py", 60)


def check_scaffold_subscription_unit_tests_pass():
    """订阅那一层的**行为**单测：跑 tools/test_project_scaffold_subscription.py。

    被测的是 `preflight_subscription` / `write_subscription` / main 的退出码与记步——
    **决定「装机成没成功」的就是这一层**，而它此前一条常驻断言都没有。实证过的假绿形态：
    一次突变同时毁掉成对校验的两个方向与承重档的返回值之后，全量 validate **仍然全绿**，
    而行为差是「退出码 1→0、`setup-state.subscribe` 从 failed 变 ok」——装机自称成功、
    断点记录说订阅已完成、闸一声不吭。

    **与 `check_scaffold_workframe_config_merge_mode` 的分工**：那道闸比的是**源码字面**，
    挡得住「把退出码表达式的某一半摘掉」；本闸跑的是**外部可观测行为**（退出码 / 文件字节 /
    setup-state 的值），挡的是「字面照旧、判定被架空」。两者都要，缺一个就留着上面那个形态。
    """
    return _run_suite_with_floor("scaffold_subscription_unit_tests_pass",
                                 Path("tools") / "test_project_scaffold_subscription.py", 30)


def check_scaffold_plan_unit_tests_pass():
    """只读预演那一层的**行为**单测：跑 tools/test_project_scaffold_plan.py。

    被测的 `project_scaffold.py --print-plan` 是**确认页动作清单的事实来源**——装机链路能写
    受保护资产（项目 `.claude/settings.json`）与项目外的用户级市场注册表，其授权论证就是
    「写入前由确认页对会被修改的已存在文件逐个点名」。预演说漏一处，用户按一份不实的清单
    授权，而那一页是这条链路上唯一的事前授权点 ⇒ 按档 4 处置。

    **两条只有这里管得住的形态**：①「预演与真写各写一份清单」——套件在真夹具上先跑 plan、
    再跑真写，比的是 sha256 整树快照的差集，不解析任何一方的 stdout；②「写了又删」——
    整树快照对它是盲的（删干净了前后相同），套件另用写入原语拦截器证「中间一次写都没发生」，
    并配一条反证（对真写路径跑同一个拦截器，必须当场咬住）。

    **能力边界**：本闸管的是预演算得对不对、写不写盘，**管不到「模型在真实确认页上贴没贴」**
    ——那一层是终端文本，机器看不见它渲染了什么，至今没有机器面。
    格子与断言以测试文件自身为准，此处不复述计数（下限语义见 `_run_suite_with_floor`）。
    """
    return _run_suite_with_floor("scaffold_plan_unit_tests_pass",
                                 Path("tools") / "test_project_scaffold_plan.py", 25)


def check_migrate_unit_tests_pass():
    """已装项目迁移工具单测：跑 tools/test_workframe_migrate.py。

    被测的 `workframe_migrate.py` 会删文件、搬目录、改写 CLAUDE.md / AGENTS.md / `.gitignore`，按档 4 处置：
    判错时迁移日 apply exit 0，而运行态劈成两半（counter 从 1 起、子 agent 记忆注入为空）或 `.gitignore`
    整份改了行尾，且 doctor 看不出来。套件在临时 fixture 上跑 plan 零写入 / apply 正路（含空操作对照）/
    冲突与续做 / 防误跑与提示 / 会话信号 / 边界，以及建链接失败续做、嵌套项目、被搬目录里的暂存改动、报告项、
    改写前缀边界、打印出来的逆操作原样执行（含 `core.autocrlf=true` 的夹具）、没做完时的退出码与收尾块措辞（含跳过的
    改写、中途停下，以及跳过改写的文件搬迁后的路径）、改写残留、模块清单解析的响亮失效、镜像目录里 git 找回还原不了
    原字节的文件的点名、`git status` 里的 skills 链接（链接自身与链接下的文件两平台都不许有；apply 后那一格在 Windows
    恒过、只在 POSIX 上分得开）、链接没被忽略时的提交提醒、回执与 plan 动作表对不上时的降级；组数与断言以测试文件
    自身为准，此处不复述计数。
    """
    name = "migrate_unit_tests_pass"
    import os
    test_path = FRAMEWORK_ROOT / "tools" / "test_workframe_migrate.py"
    if not test_path.exists():
        return _fail(name, "tools/test_workframe_migrate.py missing")
    try:
        result = subprocess.run([sys.executable, str(test_path)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=300,
                                env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    except Exception as e:
        return _fail(name, f"run error: {e}")
    if result.returncode != 0:
        return _fail(name, f"test exit={result.returncode}: {_suite_failure_summary(result.stdout + result.stderr)}")
    if "all passed" not in result.stdout:
        return _fail(name, "套件退出 0 但没有打出 all passed——半途退出与通过同形，按未通过处理")
    return _ok(name, "(test suite passed)")


def check_maintenance_dedup_unit_tests_pass():
    """`maintenance_workorder.py --commit` 去重单测：跑 tools/test_maintenance_dedup.py。

    判据是「两半分开判、日期取提交时刻与 manifest mtime 各自的本地与 UTC 日期」，按档 4 处置：判宽了会把
    一条真该记的 promotion 静默吞掉，判窄了三本账各多一条且 librarian 用量翻倍。套件冻结时钟在本地 00:00–08:00
    与之后各跑一遍（不冻结时 UTC 格与本地格构造出来完全一样），另有跨零点一格、sidecar 保护格（同 key 不覆盖、
    「是不是这条 promotion 的 sidecar」的两个合取条件各一格）与 `workframe-event` 的 key 恒等格。
    """
    name = "maintenance_dedup_unit_tests_pass"
    import os
    test_path = FRAMEWORK_ROOT / "tools" / "test_maintenance_dedup.py"
    if not test_path.exists():
        return _fail(name, "tools/test_maintenance_dedup.py missing")
    try:
        result = subprocess.run([sys.executable, str(test_path)], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=180,
                                env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    except Exception as e:
        return _fail(name, f"run error: {e}")
    if result.returncode != 0:
        return _fail(name, f"test exit={result.returncode}: {_suite_failure_summary(result.stdout + result.stderr)}")
    if "all passed" not in result.stdout:
        return _fail(name, "套件退出 0 但没有打出 all passed——半途退出与通过同形，按未通过处理")
    return _ok(name, "(test suite passed)")


def check_session_start_output_within_cap():
    """CC 清单 `SessionStart` 上除打包器之外的每条 hook，在 fixture 状态下各自 stdout ≤ 物理上限；
    合计超限只提示。

    这几条（`heartbeat-check.py` / `session-start-prep.py` / `check-stale-modules.py scan-git-diff` /
    `memory-ask.py`）的输出直接进上下文、直接受同一个上限约束，而 `context_shards_within_cap`
    的被检对象是打包器的片，够不着它们。逐 hook 独立判定上限，所以合计超限只在合并成一条 hook
    时才是真风险——分开挂时它是提示不是错误。

    **它钉的是 fixture 状态下的输出，不是上界**：真实项目里首会话验收 / drift 修复 / 待办提示
    同时触发时输出更长，本闸看不见；这一格靠人工在真实项目上抽查。stdout 是 `hookSpecificOutput` JSON 时
    量的是 `additionalContext`：fixture 取 clone 形态（只有 `.agents/skills/`），`session-start-prep` 在这一形态
    补建链接、CC 门改投带 `reloadSkills` 的 JSON，走的正是这一支。
    """
    name = "session_start_output_within_cap"
    import os
    import shlex
    import shutil
    import subprocess
    import tempfile
    ic = _inject_context()
    try:
        cmds = [(e, m, c) for e, m, c in _hook_command_entries("cc")
                if e == "SessionStart" and "inject-context.py" not in c]
    except Exception as e:
        return _fail(name, f"读 CC 清单失败: {e}")
    if len(cmds) < 3:
        return _fail(name, f"SessionStart 上只有 {len(cmds)} 条非打包器 hook（应 ≥3）——本闸对象缩水")
    plugin_root = FRAMEWORK_ROOT / "plugins" / "core"
    with tempfile.TemporaryDirectory() as tmp:
        proj = Path(tmp) / "proj"
        (proj / "projects" / "modules").mkdir(parents=True, exist_ok=True)
        (proj / ".claude" / "workframe-state").mkdir(parents=True, exist_ok=True)
        (proj / ".claude" / "agent-memory" / "shared").mkdir(parents=True, exist_ok=True)
        (proj / ".workframe-config.json").write_text('{"project_name":"cap-probe"}', encoding="utf-8", newline="")
        (proj / ".agents" / "skills" / "probe").mkdir(parents=True, exist_ok=True)   # clone 形态：走补建 ＋ JSON 那一支
        (proj / "projects" / "board.yaml").write_text(
            "summary:\n  total: 1\n  pending: 1\n  in_progress: 0\n  pending_qa: 0\n"
            "  completed: 0\n  blocked: 0\n  cancelled: 0\n  last_updated: null\n\n"
            "tasks:\n  - id: T1\n    status: pending\n", encoding="utf-8", newline="")
        (proj / ".claude" / "agent-memory" / "shared" / "MEMORY.md").write_text("# 共享记忆\n", encoding="utf-8", newline="")
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(proj), CLAUDE_PLUGIN_ROOT=str(plugin_root),
                   CLAUDE_CODE_SESSION_ID="cap-probe-sess", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        sizes, total = [], 0
        for event, matcher, cmd in cmds:
            toks = shlex.split(cmd.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root).replace("\\", "/")), posix=True)
            if toks and toks[0].endswith("workframe-python"):
                toks[0] = sys.executable
            script = next((t for t in toks if t.endswith(".py")), "?")
            payload = json.dumps({"hook_event_name": event, "source": "startup",
                                  "session_id": "cap-probe-sess", "cwd": str(proj)})
            try:
                r = subprocess.run(toks, input=payload, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=180, cwd=str(proj), env=env)
            except Exception as e:
                return _fail(name, f"{Path(script).name} 探针跑不起来: {type(e).__name__}: {e}")
            # 整次 stdout 是 hookSpecificOutput JSON 时（补建 / 重指了 `.claude/skills` 的那一次，CC 门改投 JSON），
            # 进上下文的是 `additionalContext` 那一串，量它；解析不了就按原文量（不放宽）
            out = r.stdout or ""
            if out.strip().startswith("{"):
                try:
                    out = json.loads(out)["hookSpecificOutput"]["additionalContext"]
                except Exception:
                    pass
            n = ic.u16len(out)
            sizes.append((Path(script).name, n))
            total += n
            if n > ic.PHYSICAL_CAP:
                return _fail(name, f"{Path(script).name} 在 fixture 状态下 stdout {n} > 物理上限 {ic.PHYSICAL_CAP}"
                                   f"（UTF-16 单元）——该 hook 的输出会被静默落盘，模型只拿到预览并用编造"
                                   f"内容填平缺口")
    shown = "、".join(f"{s} {n}" for s, n in sizes)
    note = ""
    if total > ic.PHYSICAL_CAP:
        note = (f"；合计 {total} > {ic.PHYSICAL_CAP}，**只是提示**：逐 hook 独立判定上限，合计只在"
                f"这些脚本被合并成一条 hook 时才成风险")
    return _ok(name, f"(fixture 状态下 {shown}，各 ≤ {ic.PHYSICAL_CAP}{note})")


def check_plugin_root_per_door_contract():
    """`plugin-root.<门>.txt` 的两端契约：SessionStart 按 `--harness` 分文件写，doctor 读两份对账版本。

    单文件 `plugin-root.txt` 被两扇门轮流覆盖写各自的安装缓存根，一边升级一边没升时另一门的 skill
    配方会静默执行另一个安装里的旧脚本——分文件是让 doctor 看得见这件事的唯一办法。写方与读方分属
    两个脚本，任一方改了文件名另一方不会红，所以本闸**实跑写方**（fixture 项目 ＋ 三种 `--harness`
    形态）并**静态核读方**读的是同一组文件名：
      - `--harness cc` ⇒ 多出 `plugin-root.cc.txt`，内容与 `plugin-root.txt` 逐字节相同；
      - `--harness codex` ⇒ 多出 `plugin-root.codex.txt`，同上；
      - 不带 `--harness` ⇒ **不**产生任何 `plugin-root.<门>.txt`（门不明就不猜）；
      - 三种形态下 `plugin-root.txt` 都在——兼容位，存量 `$(cat …/plugin-root.txt)` 配方零改动。
    读方：doctor 对 `cc` / `codex` 两门各读 `plugin-root.<门>.txt`、比较各根的 `plugin.json` 版本，且这条
    判定接在 `check_env` 里（挂在 install 组，装机验收就会跑到）。
    **能力边界**：只钉文件名与内容相等，不钉「两门真的指向两个不同的缓存根」——fixture 里两门跑的是
    同一份插件，两根本就相同；不同根的对账逻辑由 doctor 自己的判定负责。
    """
    name = "plugin_root_per_door_contract"
    import os
    import subprocess
    import tempfile
    prep = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "session-start-prep.py"
    doctor = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    if not prep.is_file() or not doctor.is_file():
        return _fail(name, "session-start-prep.py 或 workframe_doctor.py 缺失")
    dtext = doctor.read_text(encoding="utf-8")
    problems = []
    # 读方静态核：两门都读、比版本、接进 check_env
    if 'f"plugin-root.{door}.txt"' not in dtext or 'for door in ("cc", "codex")' not in dtext:
        problems.append("doctor 未对 cc / codex 两门各读 plugin-root.<门>.txt")
    if "_plugin_version_at(" not in dtext or 'vers["cc"] != vers["codex"]' not in dtext:
        problems.append("doctor 未比较两根的 plugin.json 版本")
    # 走 AST 找 `check_env` 函数体内的 Call：文本匹配把注释掉的调用与 docstring 里的字面都算成「在调」
    # （本闸的突变探针先后撞到这两种），AST 里注释与字符串都不是 Call 节点
    try:
        tree = ast.parse(dtext)
    except SyntaxError as e:
        return _fail(name, f"workframe_doctor.py 解析失败: {e}")
    env_fns = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "check_env"]
    if len(env_fns) != 1:
        problems.append(f"doctor 里 check_env 定义 {len(env_fns)} 处（须恰 1）")
    elif not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "_check_plugin_roots_same_version" for n in ast.walk(env_fns[0])):
        problems.append("check_env 未调用两根版本对账（install 组验收跑不到它）")
    plugin_root = FRAMEWORK_ROOT / "plugins" / "core"
    arms = (("cc", ["--harness", "cc"], {"plugin-root.cc.txt"}),
            ("codex", ["--harness", "codex"], {"plugin-root.codex.txt"}),
            ("none", [], set()))
    for label, args, expect_extra in arms:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj"
            state = proj / _rt("state")
            (proj / "projects" / "modules").mkdir(parents=True, exist_ok=True)
            state.mkdir(parents=True, exist_ok=True)
            (proj / _rt("memory") / "shared").mkdir(parents=True, exist_ok=True)
            (proj / ".workframe-config.json").write_text('{"project_name":"door-probe"}', encoding="utf-8", newline="")
            (proj / "projects" / "board.yaml").write_text(
                "summary:\n  total: 0\n  pending: 0\n  in_progress: 0\n  pending_qa: 0\n"
                "  completed: 0\n  blocked: 0\n  cancelled: 0\n  last_updated: null\n\ntasks: []\n",
                encoding="utf-8", newline="")
            (proj / _rt("memory") / "shared" / "MEMORY.md").write_text("# 共享记忆\n", encoding="utf-8", newline="")
            env = dict(os.environ, CLAUDE_PROJECT_DIR=str(proj), CLAUDE_PLUGIN_ROOT=str(plugin_root),
                       CLAUDE_CODE_SESSION_ID="door-probe-sess", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup",
                                  "session_id": "door-probe-sess", "cwd": str(proj)})
            try:
                r = subprocess.run([sys.executable, str(prep)] + args, input=payload, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=180,
                                   cwd=str(proj), env=env)
            except Exception as e:
                return _fail(name, f"[{label}] session-start-prep 跑不起来: {type(e).__name__}: {e}")
            if r.returncode != 0:
                problems.append(f"[{label}] session-start-prep exit {r.returncode}")
                continue
            compat = state / "plugin-root.txt"
            if not compat.is_file():
                problems.append(f"[{label}] 兼容位 plugin-root.txt 未写——存量 $(cat …/plugin-root.txt) 配方当场断")
                continue
            got_extra = {p.name for p in state.glob("plugin-root.*.txt")}
            if got_extra != expect_extra:
                problems.append(f"[{label}] 分文件集合 {sorted(got_extra)} ≠ 期望 {sorted(expect_extra)}"
                                + ("——门不明时不该猜一个门写" if label == "none" else "——doctor 读不到该门的根，两门版本对账失明"))
                continue
            for extra in expect_extra:
                if (state / extra).read_bytes() != compat.read_bytes():
                    problems.append(f"[{label}] {extra} 与 plugin-root.txt 内容不同——两份「本门的根」互相矛盾")
    if problems:
        return _fail(name, "; ".join(problems))
    return _ok(name, "(写方三种 --harness 形态实跑 + 读方两门对账接线)")


def check_doctor_inject_shards_behavior():
    """doctor `inject_shards`（注入分片到齐）的行为单测：临时项目里喂合成 inject-log，逐格断言。

    doctor 的行为检查都长在本文件里（`tools/` 下没有 doctor 单测文件），本闸就是该项的单测。
    格：①各片到齐不报 ②缺一片报 warn（含重注时只到一片的 2/1/1）③compact 重注（各片 +1）不报 ④旧格式行只计数不判
    ⑤`kind: turn` 与 `shard: all` 行不参与 ⑥窗口外整组不判，跨窗口边界的组取全部行（一次投递
    被窗口劈开时不假红、缺片照样红）⑦状态目录有 inject-log 的 spill 时降 info。
    每格比文案特征而不只比等级：「缺片 warn」与「spill 降 info」若只比等级，互换文案也照样绿。
    """
    name = "doctor_inject_shards_behavior"
    import importlib.util
    import tempfile
    from datetime import datetime, timedelta, timezone
    script = FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_doctor.py"
    spec = importlib.util.spec_from_file_location("_wf_doctor_inject_shards_probe", script)
    doc = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(doc)
    except Exception as e:
        return _fail(name, f"加载 workframe_doctor 失败: {type(e).__name__}: {e}")
    fn = getattr(doc, "check_inject_shards", None)
    window = getattr(doc, "INJECT_SHARD_WINDOW_DAYS", None)
    if fn is None or not isinstance(window, int):
        return _fail(name, "workframe_doctor 里找不到 check_inject_shards / INJECT_SHARD_WINDOW_DAYS"
                           "——该项被删或改名，本单测失去对象，doctor 缺片检查从此无人验")
    now = datetime.now(timezone.utc)

    def ts(days=0, sec=0):
        return (now - timedelta(days=days) + timedelta(seconds=sec)).isoformat(timespec="seconds")

    def row(i, n, t, **kw):
        r = {"ts": t, "harness": "cc", "scope": "main", "shard": f"{i}/{n}", "sha256": None,
             "scope_sha256": None, "chars": 100, "session_id": "s-1", "thread_id": "s-1",
             "turn_id": None, "event": "SessionStart"}
        r.update(kw)
        return r

    def run(rows, spill=False):
        with tempfile.TemporaryDirectory() as td:
            P = doc.Paths(td)
            P.state.mkdir(parents=True, exist_ok=True)
            (P.state / "inject-log.jsonl").write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                encoding="utf-8", newline="")
            if spill:
                (P.state / "inject-log.4242.spill.jsonl").write_text("{}\n", encoding="utf-8",
                                                                    newline="")
            return fn({"paths": P})

    def has(f, lv, kw):
        return any(l == lv and kw in m for l, m in f)

    def warns(f):
        return [m for l, m in f if l == "warn"]

    problems = []
    full = [row(1, 3, ts()), row(2, 3, ts()), row(3, 3, ts())]
    f = run(full)
    if warns(f) or not has(f, "ok", "各片次数相等"):
        problems.append(f"① 各片到齐应 ok 且不报 warn: {f}")
    f = run(full[:2])
    if not has(f, "warn", "有片没记上"):
        problems.append(f"② 缺一片应 warn（有片没记上）: {f}")
    f = run(full + [row(1, 3, ts(sec=90))])
    if not has(f, "warn", "有片没记上"):
        problems.append(f"②b 重注时只到了一片（各片次数 2/1/1）应 warn——先去重再比会把它抹平: {f}")
    f = run(full + [row(i, 3, ts(sec=90)) for i in (1, 2, 3)])
    if warns(f):
        problems.append(f"③ compact 重注让各片各 +1，不应报: {f}")
    legacy = [{k: v for k, v in r.items() if k not in ("thread_id", "turn_id", "event")}
              for r in full[:2]]
    f = run(legacy)
    if warns(f) or not has(f, "info", "2 行旧格式"):
        problems.append(f"④ 旧格式行（缺 thread_id）只计数不判: {f}")
    f = run(full + [row(1, 3, ts(), kind="turn"), row(1, 3, ts(), shard="all"),
                    row(1, 3, ts(), shard=None, scope="prompt", kind="turn")])
    if warns(f):
        problems.append(f"⑤ kind=turn 与 shard=all 的行不应参与计数: {f}")
    f = run([row(1, 3, ts(days=window + 10)), row(2, 3, ts(days=window + 10))])
    if warns(f) or not has(f, "ok", "窗口外 1 组"):
        problems.append(f"⑥a 整组都在窗口外时不判: {f}")
    split = [row(1, 3, ts(days=window, sec=-30)), row(2, 3, ts(days=window, sec=30)),
             row(3, 3, ts(days=window, sec=30))]
    f = run(split)
    if warns(f):
        problems.append(f"⑥b 一次投递被窗口劈开时应取组内全部行（否则假红）: {f}")
    f = run(split[:2])
    if not has(f, "warn", "有片没记上"):
        problems.append(f"⑥c 跨窗口边界的组缺片仍应 warn: {f}")
    f = run(full[:2], spill=True)
    if warns(f) or not has(f, "info", "spill"):
        problems.append(f"⑦ 有未并回的 spill 时应降 info: {f}")
    if problems:
        return _fail(name, f"{len(problems)} 格不符：" + "；".join(problems[:6]))
    return _ok(name, "(缺片 / compact / 旧格式 / 窗口 / spill 各格均符)")


def check_discipline_spotcheck_contract():
    """`/core:audit` 纪律抽查的两处契约。

    ① **判据表在位**：audit SKILL.md 里有一张带「不适用」列的表，S1 / S2 两行都在、各自的「不适用」
       格非空。它是模型执行抽查的唯一判据；被删成散文、或「不适用」列没了，受限环境、开放式问题、
       Codex 门都会被判成违规——结构性假红，而终端里什么都不报。
    ② **写入命令的产出**：实跑 `workframe-event discipline-spotcheck --dry-run`，那一行的字段集
       （扣掉统一盖章的 `harness`）== event-schema 里 `discipline_spotcheck.fields` 的键集，
       `per_criterion` / `sessions_sampled` 形态符合，ts 为 UTC 秒级；dry-run 零写入；
       违规多于适用的输入被拒（exit 2）。钉产出不钉零件：schema 与命令任一边改了字段，这里对不上。
    ③ **S2 的「不适用」只认可观测的受限信号**：格里要点名 `sdk-cli`（transcript `entrypoint`），且不得
       出现「从未 / ToolSearch」——`AskUserQuestion` 是常驻工具，transcript 不记可不可用，按「整场没
       调用过、也没有 ToolSearch 记录」判不适用，会把「整场纯文本选项、从不调工具」这一 S2 最要抓的
       形态结构性排除（真实主会话实测 104 场里 71 场会被这样排掉）。两半各管一段：正向那半防受限
       信号被删，反向那半防「从未调用」被加回去（加回去时正向那半仍在，只有反向那半会红）。
    ④ **判据 id 两处相等**：判据表的 id 集合 == `workframe_event.SPOTCHECK_CRITERIA`（命令运行期不解析
       markdown，所以是两处手写、由这里对账）；命令对重复 id 与表外 id 都拒绝（exit 2、零写入）。
    """
    name = "discipline_spotcheck_contract"
    import importlib.util
    import os
    import tempfile
    skill = FRAMEWORK_ROOT / "plugins" / "core" / "skills" / "audit" / "SKILL.md"
    bin_event = FRAMEWORK_ROOT / "plugins" / "core" / "bin" / "workframe-event"
    schema_path = FRAMEWORK_ROOT / "plugins" / "core" / ".workframe-meta" / "event-schema.json"
    problems = []
    lines = skill.read_text(encoding="utf-8").splitlines() if skill.exists() else []
    rows, na_idx = {}, None
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and "id" in cells and "不适用" in cells:
            na_idx, id_idx = cells.index("不适用"), cells.index("id")
            for body in lines[i + 2:]:
                if not body.lstrip().startswith("|"):
                    break
                bc = [c.strip() for c in body.strip().strip("|").split("|")]
                if len(bc) > max(na_idx, id_idx):
                    rows[bc[id_idx]] = bc[na_idx]
            break
    if na_idx is None:
        problems.append("audit SKILL.md 里找不到带「id」与「不适用」两列的判据表——判据被删成散文或"
                        "「不适用」列没了，抽查会把受限环境、开放式问题、Codex 门判成违规")
    else:
        for cid in ("S1", "S2"):
            if cid not in rows:
                problems.append(f"判据表缺 {cid} 行——抽查少一条判据，报告里那一格没有判据可依")
            elif not rows[cid]:
                problems.append(f"判据表 {cid} 行的「不适用」格为空——协议允许缺席的情形会被判成违规")
        s2_na = rows.get("S2") or ""
        if "S2" in rows and "sdk-cli" not in s2_na:
            problems.append("S2 的「不适用」格没点名 `entrypoint` 为 `sdk-cli` 这一可观测的受限信号——"
                            "非交互会话里工具本就不可用，删掉它会把这类会话判成违规（假红）")
        if "S2" in rows and re.search(r"从未|ToolSearch", s2_na):
            problems.append("S2 的「不适用」格退回按「整场没调用过 AskUserQuestion / 没有 ToolSearch 记录」判"
                            "——常驻工具在 transcript 里没有可用性观测面，这样写会把「整场纯文本选项、从不"
                            "调工具」这一 S2 最要抓的形态结构性判成不适用（违规率被压向 0，假绿）")
        try:
            ev_spec = importlib.util.spec_from_file_location(
                "_wf_event_spotcheck_probe",
                FRAMEWORK_ROOT / "plugins" / "core" / "scripts" / "workframe_event.py")
            ev_mod = importlib.util.module_from_spec(ev_spec)
            ev_spec.loader.exec_module(ev_mod)
            accepted = set(getattr(ev_mod, "SPOTCHECK_CRITERIA", ()))
        except Exception as e:
            accepted = None
            problems.append(f"加载 workframe_event 失败，判据 id 无从对账: {type(e).__name__}: {e}")
        if accepted is not None and set(rows) != accepted:
            problems.append(f"判据表的 id {sorted(rows)} 与 workframe-event 收的 id {sorted(accepted)} 不等"
                            f"——表里有而命令不收的判据记不进事件，命令收而表里没有的 id 没有判据可依")
    try:
        fields = set(json.loads(schema_path.read_text(encoding="utf-8"))["events"]
                     ["discipline_spotcheck"]["fields"])
    except Exception as e:
        fields = None
        problems.append(f"event-schema 里读不到 discipline_spotcheck.fields: {type(e).__name__}: {e}")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    with tempfile.TemporaryDirectory() as td:
        base = [sys.executable, str(bin_event), "discipline-spotcheck", "--dry-run", "--project", td,
                "--sessions-sampled", "cc=3,codex=0", "--criteria-version", "1", "--harness", "cc"]
        r = subprocess.run(base + ["--criterion", "S1=4/1", "--criterion", "S2=2/0"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=60)
        try:
            ev = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            ev = None
            problems.append(f"dry-run 没打出一行 JSON（exit {r.returncode}）: "
                            f"{(r.stdout + r.stderr).strip()[:200]}")
        if ev is not None:
            if fields is not None and set(ev) - {"harness"} != fields:
                problems.append(f"命令产出的字段 {sorted(set(ev) - {'harness'})} 与 schema 声明的 "
                                f"{sorted(fields)} 不等——消费方按 schema 读会缺字段或读到未声明字段")
            if ev.get("type") != "discipline_spotcheck":
                problems.append(f"type 写成了 {ev.get('type')!r}")
            if ev.get("per_criterion") != {"S1": {"applicable": 4, "violations": 1},
                                           "S2": {"applicable": 2, "violations": 0}}:
                problems.append(f"per_criterion 形态不符: {ev.get('per_criterion')!r}")
            if ev.get("sessions_sampled") != {"cc": 3, "codex": 0}:
                problems.append(f"sessions_sampled 形态不符: {ev.get('sessions_sampled')!r}")
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", str(ev.get("ts"))):
                problems.append(f"ts 不是 UTC 秒级: {ev.get('ts')!r}")
        written = [p for p in Path(td).rglob("*") if p.is_file()]
        if written:
            problems.append(f"--dry-run 写了文件: {[str(p.relative_to(td)) for p in written][:3]}")
        r2 = subprocess.run(base + ["--criterion", "S1=1/2"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env, timeout=60)
        if r2.returncode != 2:
            problems.append(f"违规多于适用（S1=1/2）应被拒 exit 2，实得 exit {r2.returncode}")
        # 每一格用自己的临时项目：共用一个目录时，前一格没拒绝而写下的文件会让后一格也报「写了文件」，
        # 红灯把人引向那个其实拒绝得好好的分支（本闸写坏反证时实见）。
        for label, crit in (("重复 id", ["S1=1/0", "S1=2/0"]), ("表外 id", ["S9=1/0"])):
            with tempfile.TemporaryDirectory() as case_td:
                argv = [sys.executable, str(bin_event), "discipline-spotcheck", "--project", case_td,
                        "--sessions-sampled", "cc=1", "--criteria-version", "1", "--harness", "cc"]
                argv += [x for c in crit for x in ("--criterion", c)]
                r3 = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", env=env, timeout=60)
                left = [p for p in Path(case_td).rglob("*") if p.is_file()]
            if r3.returncode != 2 or left:
                problems.append(f"{label}（{' '.join(crit)}）应被拒 exit 2 且零写入，实得 exit "
                                f"{r3.returncode}、写了 {len(left)} 个文件——后一次静默覆盖前一次或"
                                f"记下一条没有判据的数")
    if problems:
        return _fail(name, "；".join(problems[:6]))
    return _ok(name, "(判据表 S1/S2 与不适用列在位、S2 不适用只认 sdk-cli / Codex 门；判据 id 两处相等、"
                     "重复与表外 id 被拒；dry-run 字段集 == schema)")


CHECKS = [
    # Structure
    check_marketplace_json_parseable,
    check_marketplace_lists_both_plugins,
    check_plugin_json_parseable,
    check_hooks_json_parseable,
    check_agents_count_and_frontmatter,
    check_skills_count_and_frontmatter,
    check_core_shared_assets_complete,
    check_scripts_syntax,
    check_no_rules_mechanism_residue,
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
    check_root_plugin_manifest,
    check_version_consistency,
    check_event_registry_consistency,
    check_slash_namespace_consistency,
    check_pending_maintenance_schema_documented,
    check_pm_retention_matches_events_window,
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
    check_session_start_no_false_summary_creation_claim,
    # v0.2.2-fixup-4 — agent 多项目适配
    check_agents_no_plugin_internal_path,
    # v0.2.3 — Role Profile Lite
    check_role_profile_catalog_exists,
    check_agents_md_template_has_role_profile_placeholder,
    check_role_profile_field_documented,
    check_scaffold_user_fields_never_overwritten,
    # v0.2.3-fixup-1 — Codex Role Profile Lite review 发现的口径残留
    check_no_stale_reference_count,
    check_scaffold_framework_version_dynamic,
    # v0.2.3 Phase A-lite — skill gap routing 准确性
    check_no_stale_skill_count,
    check_role_count_wording_matches_agents,
    # 计数闸自身没有计数器，全仓形态一起漂就安静报绿——这条只管「我还看得见吗」
    check_role_count_scan_surface_intact,
    # TASK-019 — 角色名录散句与主力列副本入闸（计数闸只对数字，这两条对名字）
    check_role_roster_wording_matches_agents,
    check_role_profile_lead_copies,
    check_signoff_table_alignment,
    check_veto_list_alignment,
    # TASK-010 — 角色集正向对账：agents/ 目录为唯一事实源，下游枚举跟随
    check_role_enum_single_source,
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
    check_session_start_no_phantom_setup_state,
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
    check_paths_single_source,
    # 发布面里运行态目录的旧字面（与 paths_single_source 分工：那道管代码字符串两形态，这道管全文本旧形态）
    check_runtime_dir_legacy_literals,
    check_runtime_dir_legacy_literals_unit_tests_pass,
    check_no_direct_claude_env,
    check_event_schema_v3_availability,
    check_hook_commands_declare_harness,
    check_event_line_serialization_single_source,
    check_hook_scripts_tolerate_harness_arg,
    check_codex_hooks_exit_outside_project,
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
    check_module_close_check_unit_tests_pass,
    check_project_scaffold_catalog_unit_tests_pass,
    check_signoff_tier_check_unit_tests_pass,
    check_signoff_guard_unit_tests_pass,
    check_signoff_consumers_carry_pointer,
    check_modules_no_legacy_display_name_or_slug,
    check_modules_template_placeholders_quoted,
    check_modules_template_sync_ref_field,
    check_index_refresh_emits_overview_ref,
    check_modules_template_code_map_coverage_field,
    # v0.4.x 需求层去版本化 + 子需求结构
    check_modules_no_legacy_v_dir_or_versions_timeline,
    check_modules_no_legacy_req_type,
    # 初始化链路重构：scaffold 参数化契约 + workframe-launcher 骨架与依赖方向机器闸
    check_core_reference_links_resolve,
    check_scaffold_params_interface,
    check_launcher_plugin_structure,
    check_launcher_no_cross_plugin_relative_path,
    check_launcher_reference_integrity,
    check_launcher_cli_contract,
    check_launcher_entry_flow_guards,
    check_claude_md_merge_wired,
    # 三方 review 收口（2026-08-16）：静默丢数据 / 静默放行 / 假成功三类的防回退闸
    check_extract_assets_tag_unique,
    check_skill_count_consistent,
    check_hook_stage_count_consistent,
    check_doc_model_id_not_recommended,
    check_event_ts_and_reason_contract,
    check_events_template_no_inline_registry,
    check_board_summary_indent_agnostic,
    check_doctor_claude_md_import_contract,
    check_scaffold_preflight_before_write,
    check_config_enum_single_source,
    check_unreleased_changelog_numbers,
    check_frontmatter_block_scalar_indent,
    # frontmatter 单行标量形态白名单——块标量闸看不见的那一族静默失败（`: ` / ` #` /
    # 引号不配对 / 指示符开头 / 缩进续行）；` #` 那一类真解析器也是绿的，只有形态判定抓得住
    check_frontmatter_scalar_forms,
    # CI actions 钉 SHA 形态闸——防手滑写回浮动 tag / 丢版本注释；升级本身由发版前人工核版驱动
    check_ci_actions_pinned_sha,
    # agent tools 白名单必须含 Skill——缺它 subagent 一个 skill 都调不了，rules 的点名指令随之空转
    check_agent_tools_include_skill,
    # 注入通道：源装得下 / 源无 CR / 无重复段 / 点名解析得到 / 子 agent 看板规矩在 / 旧段全有落点声明
    check_context_shards_within_cap,
    check_context_sources_lf_only,
    check_context_no_duplicate_text,
    check_context_pointer_targets_exist,
    check_sub_protocol_pins_task_management,
    check_context_semantic_coverage,
    # 受保护资产两侧同值 / AGENTS.md 自包含 / 子 agent 侧挂载事件绑定
    check_auto_update_list_alignment,
    check_agents_md_self_contained,
    check_sub_shard_event_binding,
    # Codex 清单静态面：孪生 plugin.json / 派生同步 / 三数相等 / 冻结基线 / commandWindows 形态 /
    # 注入类 limit=0 / 全片投递 / stdin UTF-8 解码事实 / SessionStart 纯文本输出量
    check_codex_plugin_manifest_twin,
    check_codex_hooks_generated_in_sync,
    check_codex_hooks_count,
    check_codex_hook_lines_frozen,
    check_codex_hook_command_windows_shape,
    check_codex_limit_zero_on_injectors,
    check_codex_injector_shard_all,
    check_hook_stdin_utf8,
    check_session_start_output_within_cap,
    check_plugin_root_per_door_contract,
    check_codex_door_complete,
    check_codex_door_unit_tests_pass,
    # 两门孪生对账（不经生成器的第二视角）：hook 四元组多重集 / 角色渲染 vs 仓内样本
    check_hooks_twin_parity,
    check_role_twin_parity,
    check_project_skills_path_unit_tests_pass,
    # settings 读 / merge / 写（装机链路写订阅声明的那份实现）＋ 订阅那一层的行为
    check_settings_io_unit_tests_pass,
    check_scaffold_subscription_unit_tests_pass,
    # 只读预演（确认页动作清单的事实来源）：往返一致 / 零写入 / 露面规则 / 退出码对齐
    check_scaffold_plan_unit_tests_pass,
    # 纪律可观测层：doctor 注入分片到齐的行为单测 / audit 纪律抽查的判据表与写入命令
    check_doctor_inject_shards_behavior,
    check_discipline_spotcheck_contract,
    # 已装项目迁移工具 / --commit 记账去重
    check_migrate_unit_tests_pass,
    check_maintenance_dedup_unit_tests_pass,
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
