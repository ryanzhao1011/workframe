#!/usr/bin/env python3
"""
module-close-check — 功能收口时的确定性检查命令（独立 CLI，不挂任何 hook）。

定位：把「hook 提醒 → 模型自行处理 → 模型自行声明完成」的收口链补上机器验收的
最后一环。判定全部是文件系统 + git 的确定性判断，无模型裁量；任一 error 则 exit 1
——红了就是没收干净。触发靠纪律：正确时点 = 模块文档与账本写完之后、外层仓收口
提交之前（代码仓应已提交——基准 hash 只有提交后才存在）。

九项检查：
  1. stale 清单为空（非空 → error 逐条列出）
  2. code_paths 形态与指向健康：非法形态（绝对路径 / `..` 逃逸，literal 与 glob
     同判）→ error；literal 指向不存在文件 → error；glob 展开为空（目录没了，
     或目录在但 git 视野内无文件）→ error。glob 判的是「还指不指向任何东西」这个
     终态，「展开集少了几个」由检查 4 覆盖——职责分工见该函数 docstring。
     （code-map 一侧的对账归检查 6，此处只判 code_paths 自身）
  3. 基准引用同值性：code-map `source_ref` / submodule `last_synced_ref` /
     overview「基准 commit」行（若有）不同值 → error
  4. 命中模块已刷新：source_ref..HEAD 之间存在触碰该模块 code_paths 的提交，
     或工作树/暂存区有命中改动 → error（=漏刷）；source_ref 解析不到 → error
  5. 嵌套代码仓提交状态：工作树脏 → warn（真命中模块清单的脏已由检查 4 升
     error）；领先 upstream → warn（中性提示，不催推送——何时推送是发版纪律）
  6. code-map 覆盖率对账：code-map frontmatter 的 `source_paths` 与 `code_paths`
     （`source_repo` 圈定仓内那部分）展开文件集**不相等** → error，「少」与「多」
     文案分开；跨仓路径显式跳过并报数；`code_map_coverage: exempt` 声明豁免走 info
  7. 账本条目字段：读 `.workframe-config.json` 的 `close_check` 键（scaffold 装机即
     写入，默认开启；改成空壳 `{}` 则读不到 `ledger`，本项跳过）；
     最新条目日期不在 {今天, 昨天} → error；必含字段头缺失 → error。
     「没记账」类判定按 git 活动条件化：**扫与检查 5 相同的仓集合**（根仓 + 已发现的
     嵌套仓），任一仓在参考日之后有提交或工作树有改动才 error，全都没有降 info，
     有仓判不了则回落无条件 error 并在文案里点名是哪个仓判不了
  8. YAML 可解析性：扫描面内每份文件过一次**真** YAML 解析器（PyYAML）。不合法 →
     error（分三种互不相同的文案：占位符残留 / 控制字符等 ReaderError / 其余语法错，
     红灯带行列与肇事字符 repr）；frontmatter 被 BOM 遮住 → 单列一条 error；
     PyYAML 不可用 → **error 而非跳过**（「没跑」不是「通过」），显式声明
     `"close_check": {"yaml_parse": false}` 才关闭。
     **只证明文件是合法 YAML，不证明各处正则读数与真解析器一致**——重复键这类
     「两边都成功但取值不同」的形态本项看不见。
     位置说明：语义上它该第一跑（本项失败时前 7 项对该文件的读数都不可信），
     工程上追加在账本之后——插进前面会让全仓十余处按序数的引用一起作废。
  9. 纪律产物痕迹（`[discipline-trace]`，**warn 档，永不产 error**）：窗口起点 = 本地
     「昨天 00:00」。适用前提 = 窗口内任一子模块的 `code_paths` 在所属仓有提交或工作树
     改动（按仓分组、git 查询写全时刻）。三条判据：a 窗口内项目里零 `skill_used` → 只出
     info 提示（协议允许「没用 skill 就不写」，判红即结构性假红）；b 映射同次运行的
     `[ledger]`（error → 红）；c 命中子模块的看板候选任务无一在今天 / 昨天更新 → 红。
     `main()` 每次运行追加一行计数日志并从日志现算升级计数，达标只打印建议、不自动升；
     标记「确实漏做 / 闸判错」走独立命令 `workframe-discipline-mark`。能力边界见
     `check_discipline_trace` 的 docstring。

序数的用法约定（本文件内**有意**保留序数，别当成漏改）：`检查 N` 这种写法只在
**本 docstring 与本文件的段落标题**里用，指的是紧邻上方那份编号清单，改一个就得
改整串，属于同一处定义的组成部分。**跨文件引用一律用 id**（`[stale-clean]` /
`[ledger]` / `[yaml-parse]` …），因为序数会随增删检查而作废，而 id 不会——命令的
输出本来打的就是 id。

退化行为：`projects/modules/` 不存在的项目，检查 1-6 整体跳过；检查 7（账本）照跑；
检查 8 扫描面为空，报 info 并明说「一份文件都没读到，不等于通过」；检查 9 无对象可查，
报不适用（info，不等于通过）。
写入面：除它自己的计数日志外（检查 9 同时读这份日志算升级计数），不改动任何被检对象
（体检/检查类工具不得改变被检对象）。计数日志只在 `main()` 里追加，`run_all()` 零写入
——后者是 validate smoke 与单测的入口，同一个临时目录会被连调多次。
为此刻意不走 check-stale-modules 的索引层（lookup 缺索引会触发重建写盘），只复用
其纯读函数（parse_code_paths / discover_nested_repos / load_stale）；git 查询自建
薄封装，失败一律降级为该项 warn，不炸整个命令。

本文件刻意不包 UTF-8 流（在 validate `check_utf8_stream_wrap_symmetric` 的豁免
白名单里）：main() 先 importlib 加载 check-stale-modules.py，那边是模块级包装，
加载那一刻两条流已经包好；自己再包就是两个 TextIOWrapper 抢同一个 buffer。
加载前的输出（argparse 报错/usage）只含 GBK 可编码字符，不带 ✓✗ 类符号。

已知盲区（陈述可证伪）：检查 7 无法分辨「最新账本段属于哪条工作线」——同日早前
收口写过账、本轮忘写时会假绿；确定性检查判不了语义归属，此项仍靠 QA 与纪律。
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

LEVEL_MARK = {"ok": "✓", "info": "i", "warn": "!", "error": "✗"}

# git 短 hash 的两种合法记法：裸 hash 或 commit: 前缀（document-norms 宽接口）。
# branch: / tag: 是可变引用，无法钉住「文档基于哪份代码」，检查 4 对其 warn 跳过。
_HEX_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")

# 子模块 overview「最近同步」行里的基准括注——基准引用的第三个落点。
# 模块级常量而非内联：产出方（module-index-refresh 的取数规程）与本提取式必须同形，
# validate 有一道闸 import 这个常量去验规程规定的行形态；两边各写一份正则就是它要防的分叉。
OVERVIEW_REF_RE = re.compile(r"基准 commit[:：]?\s*([0-9a-fA-F]{7,40})")


# 同目录公共模块：运行态目录与 harness 差异各只有一份实现
# （见 _state_io.py / _harness.py 抬头）。本文件刻意不包 UTF-8 流，而这两个模块
# import 时无副作用（不碰 stdout），所以放在这里不违反上面那条。
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from _harness import project_dir as _harness_project_dir  # noqa: E402
from _state_io import state_dir_of as _state_dir_of  # noqa: E402
from _state_io import SPILL_SUFFIX, append_line  # noqa: E402

_CSM = None


def _load_csm(project_dir):
    """importlib 加载同目录的 check-stale-modules.py（文件名带连字符无法直接 import）。

    必须在设好 CLAUDE_PROJECT_DIR **之后**加载：它的 PROJECT_DIR/STALE_FILE 等
    路径常量在 import 期冻结，加载前不设 env，从子目录跑 CLI 时全部检查静默空转。

    **只 exec 一次，之后换 project_dir 走属性重定向**：它的包流在 import 期执行，
    重复 exec_module 会把已包装的流再包一层——两个 TextIOWrapper 抢同一个 buffer，
    先被回收的关掉 buffer，另一个 `ValueError: I/O operation on closed file` +
    lost sys.stderr（本脚本首跑实测炸过，不是假想）。
    """
    global _CSM
    p = Path(project_dir).resolve()
    os.environ["CLAUDE_PROJECT_DIR"] = str(p)
    if _CSM is None:
        script = Path(__file__).resolve().parent / "check-stale-modules.py"
        spec = importlib.util.spec_from_file_location("check_stale_modules", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CSM = mod
    else:
        state = _state_dir_of(p)
        _CSM.PROJECT_DIR = p
        _CSM.STATE_DIR = state
        _CSM.INDEX_FILE = state / "code-paths-index.json"
        _CSM.STALE_FILE = state / "stale-modules.yaml"
        _CSM.LOCK_FILE = state / "modules-index.lock"
        _CSM.MODULES_DIR = p / "projects" / "modules"
        _CSM.EVENTS_FILE = state / "events.jsonl"
    return _CSM


# ---------- git 薄封装（自建，不复用 csm._git_lines——那个失败路径写事件，本命令不写事件） ----------


def _git(repo_dir, *args):
    """跑一条 git 命令。返回 (rc, stdout_lines, err_text)；进程级异常 rc=-1。

    `-c core.quotepath=off`：git 默认 `quotepath=true`，输出路径里的非 ASCII 字节会被
    转成八进制转义并整体加引号（实测 `projects/modules/装机与初始化/…` →
    `"projects/modules/\\350\\243\\205…"`）。检查 4 只数行数、拿首行做展示，转义仅损文案；
    但检查 2 要拿 `ls-files` 的输出路径与 code_paths 做匹配，转义路径匹配不上任何 pattern
    ——同一个开关在这里损的是判定。modules 体系明确允许中文模块名与中文路径
    （reference/module-architecture.md §5.1），所以这不是边角形态。
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_dir), "-c", "core.quotepath=off", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
        return r.returncode, r.stdout.splitlines(), r.stderr.strip()
    except Exception as e:  # git 缺失 / 超时：降级交给调用方，不炸整个命令
        return -1, [], f"{type(e).__name__}: {e}"


# ---------- 轻量解析（无第三方 yaml：与 parse_code_paths 同思路的标量字段版） ----------


def _scalar_field(text, key):
    """从 YAML / frontmatter 文本里取顶层 `key: value` 标量。

    实际文件形态是值带引号 + 行尾注释（`source_ref: "7c99076a"   # 生成基准…`），
    须剥引号与 `#` 尾注；找不到返回 None，值为空串返回 ""。
    """
    m = re.search(r"^%s:[ \t]*(.*)$" % re.escape(key), text, re.M)
    if not m:
        return None
    val = m.group(1).strip()
    if val.startswith('"'):
        end = val.find('"', 1)
        return val[1:end] if end > 0 else val.strip('"')
    if val.startswith("'"):
        end = val.find("'", 1)
        return val[1:end] if end > 0 else val.strip("'")
    return re.sub(r"\s+#.*$", "", val).strip()


def _inline_list(text, key):
    """从 frontmatter 文本取顶层 `key: [a, b]` **行内数组**。

    返回三态，**不能压成两态**：
      - `list`：正常解析（含合法的空数组 `[]`）
      - `None`：字段不存在
      - `False`：字段在、但值不是能解析的行内数组（写成块状列表、写坏了括号等）

    把 `None` / `False` 都退化成 `[]` 会让「有人把 `source_paths:` 整行删了」与
    「显式声明覆盖 0 个文件」长得一模一样。前者是静默失去看守，后者是合法状态；
    压成一种之后，红灯会说「缺 N 个文件」，把人引向 code-to-doc 漏解析，
    而真相是那个字段根本不在了。

    **为什么不复用 `csm.parse_code_paths`**：那个解析的是 submodule.yaml 的**块状列表**
    （逐行 `- item`，输入是文件路径）；这里是 code-map frontmatter 里的**行内数组**
    （出厂模板即 `[]`，产出方按此写）。输入形态与来源都不同，没有可复用的那一半。
    只认行内数组是**有意收窄**（见 code-to-doc/SKILL.md §source_paths 的语义与写法）：
    只认一种形态，才能给出一条点得准的红灯文案。
    """
    m = re.search(r"^%s:[ \t]*(.*)$" % re.escape(key), text, re.M)
    if not m:
        return None
    raw = re.sub(r"\s+#.*$", "", m.group(1)).strip()
    if not raw:
        return False  # 值为空（多半写成了块状列表，真正的空数组要写 `[]`）
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else False
    except Exception:
        pass
    # JSON 解析不了但形态像数组：YAML 允许单引号 / 裸标量，宽松再收一次
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
    return False


def _frontmatter(md_path):
    """取 markdown 文件的 frontmatter 文本段（两条 `---` 之间）；无则空串。"""
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    return m.group(1) if m else ""


def _norm_ref(raw):
    """规范化基准引用：剥 commit: 前缀。返回 (kind, value)。

    kind ∈ {"commit", "mutable", "empty"}——branch:/tag: 是可变引用归 mutable；
    裸值必须长得像 hash 才算 commit，否则也归 mutable（防手写分支名混进来）。
    """
    if not raw:
        return "empty", ""
    if raw.startswith("commit:"):
        raw = raw[len("commit:"):]
    if raw.startswith("branch:") or raw.startswith("tag:"):
        return "mutable", raw
    return ("commit", raw) if _HEX_RE.match(raw) else ("mutable", raw)


def _same_commit(a, b):
    """hash 同值比对，带前缀容差：8 位与 10 位缩写指同一 commit 时不误报。"""
    a, b = a.lower(), b.lower()
    return a.startswith(b) or b.startswith(a)


def _collect_submodules(project_dir, csm):
    """遍历 projects/modules/<basic>/<sub>/，聚齐每个子模块的检查输入。"""
    subs = []
    modules_dir = project_dir / "projects" / "modules"
    for yaml_path in sorted(modules_dir.glob("*/*/submodule.yaml")):
        sub_dir = yaml_path.parent
        rel = f"{sub_dir.parent.name}/{sub_dir.name}"
        yaml_text = ""
        try:
            yaml_text = yaml_path.read_text(encoding="utf-8")
        except Exception:
            pass
        code_map = sub_dir / "current-state" / "code-map.md"
        fm = _frontmatter(code_map) if code_map.exists() else ""
        overview_ref = None
        overview = sub_dir / "overview.md"
        if overview.exists():
            try:
                m = OVERVIEW_REF_RE.search(overview.read_text(encoding="utf-8"))
                overview_ref = m.group(1) if m else None
            except Exception:
                pass
        subs.append({
            "rel": rel,
            # 三条路径原样带出，供检查 8 复用本次遍历的结果——它要的扫描面与这里
            # 完全同源，另写一份 glob 就是第二事实源。`sub_dir` 让它够得到本子模块
            # 下 current-state/ 的其余三份（architecture / api-surface / data-model）。
            "sub_dir": sub_dir,
            "yaml_path": yaml_path,
            "overview_path": overview,
            "code_paths": csm.parse_code_paths(yaml_path),
            "last_synced_ref": _scalar_field(yaml_text, "last_synced_ref"),
            "code_map_coverage": _scalar_field(yaml_text, "code_map_coverage"),
            # **两处回落，各堵一个静默跳过。** 基准与归属仓的首选事实源是 code-map
            # frontmatter，但**声明了 `code_map_coverage: exempt` 的子模块根本没有
            # code-map**，于是这两个字段恒为 None ——刷新判定把它读成「未同步过」
            # 整个跳过，而它的基准明明写在 `submodule.yaml` 里。实测：一个 exempt
            # 子模块的基准落后了 30 多个提交，本检查报绿、且它还被计进了「已检查」数。
            # `ref-consistency` 已经保证两处同值（都存在时），所以回落不引入第二口径。
            "source_ref": (_scalar_field(fm, "source_ref") if fm else "")
                          or _scalar_field(yaml_text, "last_synced_ref"),
            "source_repo": (_scalar_field(fm, "source_repo") if fm else "")
                           or _scalar_field(yaml_text, "source_repo"),
            # 三态（list / None=字段不存在 / False=解析不出数组），检查 6 分别处置
            "source_paths": _inline_list(fm, "source_paths") if fm else None,
            "has_code_map": code_map.exists(),
            "overview_ref": overview_ref,
        })
    return subs


# ---------- 检查 1：stale 清单 ----------


def check_stale_clean(project_dir, csm, subs):
    stale = csm.load_stale().get("submodules", {})
    if not stale:
        return [("ok", "stale 清单为空")]
    out = []
    for sub, info in sorted(stale.items()):
        reasons = "; ".join(info.get("reasons", [])[:3])
        out.append(("error", f"{sub} 仍挂 stale（{reasons}）——更新文档后走 "
                             f"clear-stale 清除（可带 --reason 留处置理由）"))
    return out


# ---------- 检查 2：code_paths 形态与指向健康 ----------


def _classify_path(pat):
    """把一条 code_paths 归类：('bad' | 'glob' | 'literal', 规范化路径)。

    非法形态判定必须发生在 literal / glob 分流**之前**。此前的写法是先
    `if any(c in pat for c in "*?["): continue` 跳过 glob、再判非法形态，于是
    `C:/x/**`、`../x/**` 这类**非法 glob** 从检查 2 溜走；而检查 4 的
    `_split_paths_by_repo` 同样 `continue` 跳过它们，注释还写着「非法形态已在检查 2
    报过」——那句话对 glob 并不成立，两边都不报，非法 glob 被静默丢弃。

    本函数是这个判定的唯一实现，检查 2 与检查 4 共用：两处各留一份副本时，
    收窄其中一份不会有任何东西报警（另一份照常红，终端上看仍有闸在拦）。
    """
    norm = pat.replace("\\", "/")
    segs = [x for x in norm.split("/") if x]
    if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or ".." in segs:
        return "bad", norm
    return ("glob" if any(c in norm for c in "*?[") else "literal"), norm


def _static_prefix(norm):
    """取 glob 的静态目录前缀（到第一个含通配符的路径段为止）。

    **只用于区分两种失败文案**（目录整个没了 vs 目录在但空），不参与匹配判定——
    命中与否一律由 `csm.glob_to_regex` 决定。不复用 csm.first_static_segment：
    那个只取**第一段**当索引 bucket key 用，对
    `claude-workframe/plugins/core/skills/x/**` 只会返回 `claude-workframe`。
    """
    segs = []
    for seg in norm.split("/"):
        if any(c in seg for c in "*?["):
            break
        segs.append(seg)
    return "/".join(segs)


def _owner_of(norm, nested):
    """判一条（已规范化的）路径属于哪个仓，取最长匹配（深层仓优先）。返回 "" = 根仓。

    检查 2 与检查 4 共用：前者按仓取 glob 展开面，后者按仓剥前缀做 git pathspec。
    两处各写一份最长匹配时，一旦有人只改其中一份，另一份照常工作，没有任何东西会报警。
    """
    owner = ""
    for repo in nested:
        if norm == repo or norm.startswith(repo + "/"):
            if len(repo) > len(owner):
                owner = repo
    return owner


def _repo_file_index(project_dir, nested):
    """按仓收集「git 视野内的文件」（项目根相对），作为 glob 的展开面。

    返回 (index:{owner: set(paths)}, unavailable:{owner: 'absent'|'failed'})。
    **按仓分治而不是合并成一个全集**：某个仓取不到时只让**指向该仓**的 glob 退出判定，
    其余仓照常判。合并成全集会让任意一个仓的失败把整个 glob 检查静默关掉。

    `unavailable` 分两种，判级不同（沿用 scan_git_diff_for_stale 的既有口径）：
      - `absent`：**根仓不是 git 仓**——这是合法状态（modules 体系不要求项目根是仓），
        报 info 不报 warn，但仍要说出「有几条 glob 因此没被检查」，否则是静默失效。
      - `failed`：嵌套仓查询失败——嵌套仓是靠 `.git` 存在才被发现的，失败即坏仓，报 warn。

    展开面取 git 而不是自己 walk 文件系统，两条理由：
    ①**与消费方同口径**：stale 扫描（check-stale-modules 的 `scan_git_diff_for_stale`）
      喂给 `lookup_submodules` 的正是 git 的输出（diff HEAD / diff --cached /
      ls-files --others）。闸自己走文件系统就成了第三套口径。
    ②**gitignore 的产物不该算进展开面**：`plugins/core/bin/` 实测文件系统 17 个文件、
      git 视野 12 个，差的 5 个是 `__pycache__` 里的 `.pyc`。若按文件系统判，某条 glob
      覆盖的 tracked 文件被删光、只剩 `.pyc` 时会判成非空而**假绿**。且项目纪律定
      「判 gitignore 一律问 git，不自己枚举规则形态」。

    `--cached --others --exclude-standard` = tracked + 未被 ignore 的 untracked，
    恰好等于 stale 扫描那三条命令的并集面。
    """
    index = {}
    unavailable = {}
    for owner in [""] + list(nested):
        repo_dir = project_dir / owner if owner else project_dir
        rc, lines, _err = _git(repo_dir, "ls-files", "--cached", "--others",
                               "--exclude-standard")
        if rc != 0:
            unavailable[owner] = "absent" if owner == "" else "failed"
            continue
        got = set()
        for ln in lines:
            ln = ln.strip()
            # 目录条目（根仓未 ignore 嵌套仓时 ls-files 会报 `<dir>/`）不是文件，跳过
            if not ln or ln.endswith("/"):
                continue
            got.add(f"{owner}/{ln}" if owner else ln)
        index[owner] = got
    return index, unavailable


def check_code_paths_health(project_dir, csm, subs):
    """literal 指向存在 + glob 可展开 + 形态合法。

    **职责边界（改检查 4 前先读这段）**：glob 一侧本检查只判**终态**——「这条清单
    还指不指向任何东西」。它**不判**「展开集里少了几个文件」，那一段由**检查 4**
    覆盖：删掉 glob 覆盖的任一文件都是一次 code_paths 命中，未提交时检查 4 报
    「有未提交改动」、已提交未刷基准时报「漏刷」。两层拼起来才没有漏网通道——
    实测四组：删 4/5 未提交→检查 4 红、删 4/5 已提交未刷→检查 4 红、删 4/5 已刷→
    全绿（正确，基准刷过即文档已随改动更新）、删光 5/5 已刷→**本检查红**。

    所以这里用「展开数 ≥1」的下限断言而不是集合相等：glob 的语义是「这片区域整体
    归我」而非名录，要求展开集与某份登记名录逐个相等等于把 glob 降级成 literal 清单。
    但下限断言是**容器级**的——`templates/**`（43 个文件）删到只剩 1 个，本检查照样
    绿；那个区间正是上面说的检查 4 的地盘。**若将来削弱或移除检查 4，本注释所依赖的
    前提就没了，这里必须补上展开面快照对账。**

    本段是**这一道检查**为什么这么选的记录。「任何断言该用 `==` 还是 `⊆`」的一般判据
    见 skill: `test-case-design` 的 `reference/test-scoping.md` 档 4。
    """
    out = []
    n_lit = n_glob = n_hit = 0
    skipped = {}  # owner -> 因该仓面取不到而未判的 glob 条数
    all_paths = sorted({p for s in subs for p in s["code_paths"]})
    nested = csm.discover_nested_repos(all_paths)
    index, unavailable = _repo_file_index(project_dir, nested)
    for s in subs:
        for pat in s["code_paths"]:
            kind, norm = _classify_path(pat)
            if kind == "bad":
                out.append(("error", f"{s['rel']} 的 code_paths 含非法形态路径 "
                                     f"（{pat}）——只接受项目根相对路径"))
                continue
            if kind == "literal":
                n_lit += 1
                if not (project_dir / norm).exists():
                    out.append(("error", f"{s['rel']} 的 code_paths 指向不存在的文件"
                                         f"（{pat}）——改名/移动/删除后清单未同步，"
                                         f"反向查找对它静默失明"))
                continue
            n_glob += 1
            owner = _owner_of(norm, nested)
            if owner in unavailable:
                # 该仓的展开面取不到，判了必是假红。逐仓计数，循环后统一报出——
                # 静默跳过会让「没检查」和「检查通过」长得一模一样。
                skipped[owner] = skipped.get(owner, 0) + 1
                continue
            files = index.get(owner, set())
            try:
                rx = re.compile(csm.glob_to_regex(norm))
            except re.error as e:
                out.append(("error", f"{s['rel']} 的 code_paths 含无法编译的 glob"
                                     f"（{pat}）：{e}"))
                continue
            hits = [f for f in files if rx.match(f)]
            if hits:
                n_hit += len(hits)
            elif _static_prefix(norm) and not (project_dir / _static_prefix(norm)).is_dir():
                out.append(("error", f"{s['rel']} 的 code_paths 里的 glob 指向不存在的"
                                     f"目录（{pat}）——目录被删或改名后清单未同步，"
                                     f"反向查找对整片区域静默失明"))
            else:
                out.append(("error", f"{s['rel']} 的 code_paths 里的 glob 展开为空"
                                     f"（{pat}）——目录在，但 git 视野内一个文件都没有，"
                                     f"该条已不指向任何东西"))
    for owner, n in sorted(skipped.items()):
        label = owner or "(项目根仓)"
        if unavailable.get(owner) == "absent":
            out.append(("info", f"{label} 不是 git 仓——指向它的 {n} 条 glob 未参与展开"
                                f"判定（literal 不受影响）"))
        else:
            out.append(("warn", f"{label}: git ls-files 失败——指向它的 {n} 条 glob 未参与"
                                f"展开判定（literal 不受影响）"))
    if not [x for x in out if x[0] == "error"]:
        judged = n_glob - sum(skipped.values())
        out.append(("ok", f"literal {n_lit} 条全部存在；glob {judged}/{n_glob} 条可展开"
                          f"（共命中 {n_hit} 处）"))
    return out


# ---------- 检查 3：基准引用同值性 ----------


def check_ref_consistency(project_dir, csm, subs):
    out = []
    compared = 0
    for s in subs:
        anchors = []  # (落点名, 原始值)
        if s["source_ref"] is not None:
            anchors.append(("code-map source_ref", s["source_ref"]))
        if s["last_synced_ref"] is not None:
            anchors.append(("submodule last_synced_ref", s["last_synced_ref"]))
        if s["overview_ref"] is not None:
            anchors.append(("overview 基准行", s["overview_ref"]))
        filled = [(n, v) for n, v in anchors if v]
        if len(filled) < 2:
            continue  # 尚未同步过（模板骨架 source_ref 为空）或落点不足，无从比对
        compared += 1
        kinds = {_norm_ref(v)[0] for _, v in filled}
        vals = [_norm_ref(v)[1] for _, v in filled]
        if kinds == {"commit"}:
            head_val = max(vals, key=len)
            if not all(_same_commit(v, head_val) for v in vals):
                detail = "、".join(f"{n}={v}" for n, v in filled)
                out.append(("error", f"{s['rel']} 基准引用不同值（{detail}）"
                                     f"——存在第二事实源，刷新时漏了落点"))
        else:
            if len(set(vals)) > 1:
                detail = "、".join(f"{n}={v}" for n, v in filled)
                out.append(("error", f"{s['rel']} 基准引用不同值（{detail}）"))
    if not out:
        out.append(("ok", f"{compared} 个已同步子模块的基准引用同值"))
    return out


# ---------- 检查 4：命中模块已刷新 ----------


def _split_paths_by_repo(code_paths, nested):
    """把项目根相对的 code_paths 按所在仓分组，剥掉仓前缀得到仓相对 pathspec。

    git pathspec 必须仓相对——带外层前缀的 pathspec 不报错、只静默匹配不到任何
    文件，检查会恒绿形同虚设。返回 {repo_rel or "": [仓相对路径, ...]}，"" = 根仓。
    """
    groups = {}
    for pat in code_paths:
        kind, norm = _classify_path(pat)
        if kind == "bad":
            continue  # 非法形态已由检查 2 报出（含非法 glob——两处共用 _classify_path）
        owner = _owner_of(norm, nested)  # 与检查 2 的展开面分仓共用同一份最长匹配
        rel = norm[len(owner) + 1:] if owner else norm
        groups.setdefault(owner, []).append(rel)
    return groups


def check_module_refreshed(project_dir, csm, subs):
    out = []
    checked = 0
    all_paths = sorted({p for s in subs for p in s["code_paths"]})
    nested = csm.discover_nested_repos(all_paths)
    for s in subs:
        kind, ref = _norm_ref(s["source_ref"] or "")
        if kind == "empty":
            continue  # 未同步过的模块没有基准，不参与刷新判定
        if kind == "mutable":
            out.append(("warn", f"{s['rel']} 的 source_ref 是可变引用（{s['source_ref']}）"
                                f"——branch/tag 无法精判是否已刷新，跳过"))
            continue
        groups = _split_paths_by_repo(s["code_paths"], nested)
        # source_repo 圈定基准 hash 的归属仓：值等于某个嵌套仓路径 → 该仓；空 → 根仓。
        # 一个子模块的 code_paths 可以横跨多仓（真实存在的形态）——基准精判只对
        # 归属仓内的路径做，其他仓的 hash 解析必然失败，报出来是纯误伤。
        src_repo = (s["source_repo"] or "").replace("\\", "/").strip("/")
        if src_repo and src_repo not in nested:
            out.append(("warn", f"{s['rel']} 的 source_repo（{src_repo}）不是已发现的"
                                f"嵌套仓——无法定位基准归属仓，跳过精判"))
            src_repo = None
        elif not src_repo:
            src_repo = ""  # 根仓
        groups_probe = _split_paths_by_repo(s["code_paths"], nested)
        if src_repo == "" and "" not in groups_probe and groups_probe:
            # **归属仓判空、而根仓一个路径都没有** ⇒ 基准不可能属于根仓。
            # 此时下面那句 `owner != src_repo` 会把**每一个** path group 都 continue 掉，
            # 基准精判整段不执行、且不留任何痕迹——报绿。把它变成显式信号。
            out.append(("error",
                        f"{s['rel']} 的 source_repo 为空（判为项目根仓），但它的 code_paths "
                        f"一条都不在根仓（都在 {sorted(groups_probe)}）——基准不可能属于根仓，"
                        f"此时基准精判会整段静默跳过。请在 submodule.yaml 或 code-map "
                        f"frontmatter 里显式写 source_repo"))
            continue
        checked += 1
        for owner, rels in sorted(groups.items()):
            repo_dir = project_dir / owner if owner else project_dir
            label = owner or "(项目根仓)"
            # 工作树/暂存区命中：tracked 改动、staged、untracked（??）一并覆盖
            rc, lines, err = _git(repo_dir, "status", "--porcelain", "--", *rels)
            if rc != 0:
                out.append(("warn", f"{s['rel']} @ {label}: git status 失败（{err[:80]}）"))
            elif lines:
                out.append(("error", f"{s['rel']} @ {label}: code_paths 有未提交改动 "
                                     f"{len(lines)} 处（如 {lines[0].strip()}）——"
                                     f"提交后才有可钉的基准 hash"))
            if src_repo is None or owner != src_repo:
                continue  # 非归属仓不做基准精判（该仓解析不到这个 hash 是正常的）
            rc, lines, err = _git(repo_dir, "rev-parse", "--verify", "--quiet",
                                  ref + "^{commit}")
            if rc != 0:
                out.append(("error", f"{s['rel']} 的 source_ref（{ref}）在 {label} "
                                     f"解析不到——死引用（仓历史被改写，或 hash 抄错）"))
                continue
            full = lines[0].strip() if lines else ref
            rc, _, _ = _git(repo_dir, "merge-base", "--is-ancestor", full, "HEAD")
            if rc == -1:
                out.append(("warn", f"{s['rel']} @ {label}: merge-base 执行失败，跳过"))
                continue
            if rc != 0:
                out.append(("error", f"{s['rel']} 的 source_ref（{ref}）不在 {label} "
                                     f"当前分支历史内——基准钉在别的分支上"))
                continue
            rc, lines, err = _git(repo_dir, "log", "--oneline", f"{full}..HEAD",
                                  "--", *rels)
            if rc != 0:
                out.append(("warn", f"{s['rel']} @ {label}: git log 失败（{err[:80]}）"))
            elif lines:
                out.append(("error", f"{s['rel']} 基准（{ref}）之后有 {len(lines)} 个"
                                     f"提交触碰其 code_paths（最近：{lines[0]}）——"
                                     f"漏刷，基准落点须刷至当前"))
    if not [x for x in out if x[0] == "error"]:
        out.append(("ok", f"{checked} 个有基准的子模块均已刷新到位"))
    return out


# ---------- 检查 5：代码仓提交状态 ----------


def check_repo_status(project_dir, csm, subs):
    out = []
    all_paths = sorted({p for s in subs for p in s["code_paths"]})
    nested = csm.discover_nested_repos(all_paths)
    for repo in nested:
        repo_dir = project_dir / repo
        rc, lines, err = _git(repo_dir, "status", "--porcelain")
        if rc != 0:
            out.append(("warn", f"{repo}: git status 失败（{err[:80]}）"))
        elif lines:
            # warn 不 error：真命中模块清单的脏已由检查 4 升 error，
            # 这里剩下的多是与本次收口无关的 WIP，不该把无关收口整个卡死
            out.append(("warn", f"{repo} 工作树有 {len(lines)} 处未提交改动"
                                f"（如 {lines[0].strip()}）"))
        rc, lines, _ = _git(repo_dir, "rev-parse", "--abbrev-ref", "@{u}")
        if rc != 0:
            out.append(("info", f"{repo} 当前分支未配置 upstream"))
        else:
            upstream = lines[0].strip() if lines else "@{u}"
            rc, lines, _ = _git(repo_dir, "rev-list", "--count", "@{u}..HEAD")
            if rc == 0 and lines and lines[0].strip().isdigit() and int(lines[0]) > 0:
                # 中性陈述，不写「请推送」——何时推送是各仓自己的发版/分发纪律
                out.append(("warn", f"{repo} 领先 {upstream} {lines[0].strip()} 个提交"))
    rc, lines, _ = _git(project_dir, "status", "--porcelain")
    if rc == 0 and lines:
        # 信息项不判级：收口闸的正确时点上，账本/看板本来就还没提交
        head = "、".join(x.strip() for x in lines[:5])
        more = f" 等共 {len(lines)} 处" if len(lines) > 5 else ""
        out.append(("info", f"项目根仓待提交：{head}{more}"))
    if not out:
        out.append(("ok", "代码仓提交状态干净"))
    return out


# ---------- 检查 6：code-map 覆盖率对账 ----------


def check_code_map_coverage(project_dir, csm, subs):
    """code-map frontmatter 的 `source_paths` 与 `code_paths` 展开文件集**相等**。

    契约：`source_paths` 与 `code_paths` 在 `source_repo` 那个仓内的那部分**同指一组文件**，
    坐标系为该仓相对（`source_repo` 空 = 项目根仓）。见 code-to-doc/SKILL.md 与
    reference/module-architecture.md §4.1。

    **为什么是 `==` 而不是 `⊇`**：契约语义是「全名录」，漏一个和多一个各自都是真实缺陷。
    `⊇` 只咬「少」，会把「多」静默吞掉——本仓落地时实测有一个子模块的 code-map 声称覆盖
    6 个归别的子模块的文件，`⊇` 对它全绿。**为什么不是 `⊆`**：那是恒绿方向，把 source_paths
    删空就全过。

    **为什么比的是展开文件集而不是字符串集**：同一片区域可以有多种等价写法
    （父目录 glob 与子目录 glob 在只有一个子目录时指同一组文件）。
    按字符串比会把这类等价写法判成红，那是假红。

    职责边界（改本检查或检查 2/4 前先读）：
      - 本检查只管**两张清单指的是不是同一片区域**，不管那片区域里的文件有没有被删。
      - 「glob 覆盖的文件被删了几个」→ **检查 4**（那是一次 code_paths 命中：未提交时报
        「有未提交改动」、已提交未刷基准时报「漏刷」）。两侧等量减少时本检查恒绿，
        因为 `==` 仍成立——这不是漏网，是分工。
      - 「整条清单已不指向任何东西」→ code_paths 一侧归**检查 2**，source_paths 一侧归本检查 L2。

    L2 的下限是**容器级**（每条 source_paths ≥1 个文件），落地前实核过分布：97 条里
    78 条恰覆盖 1 个文件（此时下限 ≡ 站点级），最宽的一条覆盖 43 个。宽容器上它确实退化
    （43 删到 1 仍绿），但如上所述那一格归检查 4，L2 的职责只是**咬死条目**——
    对「这条还指不指向东西」而言，「≥1」正是它的定义式，不存在退化。

    本段是**这一道检查**为什么这么选的记录。「`==` 还是 `⊆` 怎么定」与「写容器级下限断言
    前先实核每容器几处」的一般判据，见 skill: `test-case-design` 的
    `reference/test-scoping.md` 档 4。
    """
    out = []
    n_done = n_exempt = n_unsynced = n_skip_repo = 0
    cross_skipped = 0
    all_paths = sorted({p for s in subs for p in s["code_paths"]})
    nested = csm.discover_nested_repos(all_paths)
    index, unavailable = _repo_file_index(project_dir, nested)

    def _expand(pats, prefix):
        """把（仓相对的）pats 加上仓前缀展开成项目根相对文件集。返回 (文件集, 死条目)。"""
        hit, dead = set(), []
        for pat in pats:
            kind, norm = _classify_path(pat)
            if kind == "bad":
                dead.append((pat, "bad"))
                continue
            full = f"{prefix}/{norm}" if prefix else norm
            files = index.get(_owner_of(full, nested), set())
            if kind == "literal":
                got = {full} & files
            else:
                try:
                    rx = re.compile(csm.glob_to_regex(full))
                except re.error:
                    dead.append((pat, "bad"))
                    continue
                got = {f for f in files if rx.match(f)}
            if not got:
                dead.append((pat, "empty"))
            hit |= got
        return hit, dead

    for s in subs:
        rel = s["rel"]
        # L0 豁免声明：字段缺失（存量文件）与 required 同义；非法值必须报，不能当 required
        cov = s.get("code_map_coverage")
        if cov is not None and cov.strip() and cov.strip() not in ("required", "exempt"):
            out.append(("error", f"{rel} 的 submodule.yaml `code_map_coverage` 取值非法"
                                 f"（{cov.strip()!r}）——只接受 required | exempt。"
                                 f"写错的值不会被当成 required 放行：那样写错的人会以为"
                                 f"自己声明了豁免、而对账其实一直在跑"))
            continue
        if cov is not None and cov.strip() == "exempt":
            n_exempt += 1
            out.append(("info", f"{rel}: 已声明 code_map_coverage: exempt（有意不设 code-map），"
                                f"不参与覆盖率对账——理由见该子模块 overview"))
            continue
        sp = s.get("source_paths")
        if sp is None:
            if not s["has_code_map"]:
                out.append(("warn", f"{rel}: 没有 current-state/code-map.md，覆盖率无从对账"
                                    f"——跑 code-to-doc 生成，或在 submodule.yaml 标 "
                                    f"`code_map_coverage: exempt` 声明有意不设"))
            else:
                out.append(("error", f"{rel} 的 code-map frontmatter 里没有 `source_paths` 字段"
                                     f"——它是本对账的唯一输入，字段不在就整条静默失去看守"
                                     f"（不是「覆盖 0 个文件」，那要显式写 `source_paths: []`）"))
            continue
        if sp is False:
            out.append(("error", f"{rel} 的 code-map `source_paths` 不是可解析的行内数组"
                                 f"——本对账只认 `[\"a/**\", \"b.py\"]` 这一种形态"
                                 f"（出厂模板即如此）；写成块状 YAML 列表读不到，"
                                 f"对账会以为这个模块一个文件都没声明"))
            continue
        # L0b 未同步过：机械信号不是声明，给 warn 并指出两条出路
        kind, _ref = _norm_ref(s["source_ref"] or "")
        if kind == "empty":
            n_unsynced += 1
            out.append(("warn", f"{rel}: code-map 的 source_ref 为空（未同步过），覆盖率未对账"
                                f"——跑一次 code-to-doc 后本条自动转为对账；若是**有意**不设 "
                                f"code-map，在 submodule.yaml 标 `code_map_coverage: exempt`，"
                                f"别让它一直挂在这条上"))
            continue
        # 坐标系锚点：source_repo 三态（空=根仓 / 项目内嵌套仓 / 项目外脱敏标识）
        src_repo = (s["source_repo"] or "").replace("\\", "/").strip("/")
        if src_repo and src_repo not in nested:
            n_skip_repo += 1
            out.append(("info", f"{rel}: source_repo（{src_repo}）不是项目内的嵌套仓——按契约"
                                f"这是**项目外仓的脱敏标识**（dev-paste 形态）。本地没有那个仓的"
                                f"文件视野，硬对账只会报出「缺掉全部文件」的假红，整条跳过"))
            continue
        owner = src_repo or ""
        if owner in unavailable:
            lvl = "info" if unavailable[owner] == "absent" else "warn"
            label = owner or "(项目根仓)"
            why = "不是 git 仓" if unavailable[owner] == "absent" else "git ls-files 失败"
            out.append((lvl, f"{rel}: {label} {why}——取不到文件视野，本模块覆盖率未对账"))
            continue
        groups = _split_paths_by_repo(s["code_paths"], nested)
        cp_in = groups.get(owner, [])
        cross = {o: len(v) for o, v in groups.items() if o != owner}
        cross_label = "、".join(sorted(k or "(项目根仓)" for k in cross))
        # L1 对账面为空：source_repo 填错/漏填时，对账会「全部跨仓跳过」而输出全绿
        if not cp_in and s["code_paths"]:
            out.append(("error", f"{rel} 的 source_repo（{src_repo or '空=项目根仓'}）圈定的对账面里"
                                 f"一条 code_paths 都没有，而本模块共 {len(s['code_paths'])} 条"
                                 f"（全在 {cross_label}）——source_repo 填错或漏填，对账整条落空："
                                 f"输出看着是绿的，实际一个文件都没比过"))
            continue
        n_done += 1
        if cross:
            cross_skipped += sum(cross.values())
            out.append(("info", f"{rel}: {sum(cross.values())} 条 code_paths 在 {cross_label}"
                                f"（非 source_repo 圈定的仓），不在对账面内，已跳过"))
        sp_hit, sp_dead = _expand(sp, owner)
        cp_hit, _ = _expand(cp_in, owner)
        # L2 逐条：死条目（两种失败模式文案分开）
        for pat, why in sp_dead:
            if why == "bad":
                out.append(("error", f"{rel} 的 source_paths 含非法形态（{pat}）——只接受 "
                                     f"source_repo 那个仓内的相对路径，不接受绝对路径 / 盘符 / `..`"))
            elif pat.endswith("/"):
                out.append(("error", f"{rel} 的 source_paths 有一条尾斜杠目录形态（{pat}）——"
                                     f"它不是合法 glob：匹配时被当成一个**名字带斜杠的文件**去找，"
                                     f"命中 0 个；形态判定还会把它归成 literal。写成 "
                                     f"`{pat}**` 才是「这个目录整片」"))
            else:
                out.append(("error", f"{rel} 的 source_paths 有一条展开为 0 个文件（{pat}）——"
                                     f"目录被删/改名后清单没跟着改，该条已不指向任何东西"))
        # L3 集合对账：缺 / 多 两种失败模式，指向相反的排查方向
        missing = sorted(cp_hit - sp_hit)
        extra = sorted(sp_hit - cp_hit)
        if missing:
            out.append(("error", f"{rel} 的 code-map source_paths **少了** {len(missing)} 个文件"
                                 f"（如 {missing[0]}）——code_paths 认领了它、code-map 没跟平。"
                                 f"后果：stale 会因这些文件命中 code_paths 把本模块标脏，而顺着 "
                                 f"code-map 做影响分析的人看不到它们，两张清单指的不是同一片区域。"
                                 f"去查 code-to-doc 是不是漏解析、或这次改动只做了一半"))
        if extra:
            out.append(("error", f"{rel} 的 code-map source_paths **多了** {len(extra)} 个文件"
                                 f"（如 {extra[0]}）——本模块 code_paths 没认领它们，多半归别的模块。"
                                 f"后果：①改这些文件时 stale 只标到真正的归属模块，本模块 code-map "
                                 f"却声称覆盖它们，读的人以为本模块文档已随之更新（不会）"
                                 f"②同一文件被两份 code-map 声称覆盖时，描述分叉没有任何东西会报警。"
                                 f"去查这些文件到底归谁、是不是抄了别的模块的目录 glob"))
    if not [x for x in out if x[0] == "error"]:
        out.append(("ok", f"{n_done} 个子模块的 code-map 覆盖率已对账（跨仓跳过 {cross_skipped} 条 "
                          f"code_paths；{n_exempt} 个已声明豁免、{n_unsynced} 个未同步过、"
                          f"{n_skip_repo} 个跨仓 dev-paste）"))
    return out


# ---------- 检查 7：账本条目字段 ----------

# 账本模板与说明段里常有 `<!-- ... -->` 注释块，块内往往写着条目格式示例（含
# `## YYYY-MM-DD ·` 样例头）。不剥掉它，示例会被当成真实的「最新条目」，于是刚建好
# 还没记过账的项目反而报绿。
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
# 账本条目头。检查 7 取「最新条目」、检查 9 取「收口身份」都用它——同一条目头两处各写
# 一份正则，改格式时必有一处漏改。
_LEDGER_HEAD_RE = re.compile(r"^## (\d{4})-(\d{2})-(\d{2}) ·(.*)$", re.M)


def _repo_work_after(repo_dir, window):
    """单个仓在 `window`（date）当天及之后有没有干过活。True / False / None（判不了）。"""
    rc, lines, _ = _git(repo_dir, "rev-parse", "--is-inside-work-tree")
    if rc != 0 or not lines or lines[0].strip() != "true":
        return None
    rc, lines, _ = _git(repo_dir, "log", "--oneline",
                        f"--since={window.isoformat()}", "-1")
    if rc != 0:
        return None
    if lines and lines[0].strip():
        return True
    rc, lines, _ = _git(repo_dir, "status", "--porcelain")
    if rc != 0:
        return None
    return bool([x for x in lines if x.strip()])


def _work_after(project_dir, accounted_through, nested=None):
    """`accounted_through`（date）**之后**有没有干过活。返回 (判定, 窗口起始日, 出处)。

    判定 True（有提交或工作树有改动）/ False（都没有）/ None（判不了）。
    用途：账本类判定按「这段时间到底有没有活」条件化——没干活却因为没记账被判红，
    是纯噪声；而判不了时**不能默认无活**（那会静默放过真实漏记），故回落 None
    由调用方按无条件 error 处理并在文案里说明降级。

    **扫的仓集合必须与检查 5 相同：根仓 + 已发现的嵌套仓。** 只看根仓会在嵌套仓拓扑下
    把「真干活没记账」判成全绿——根仓通常 ignore 嵌套仓，`git status` 对其整体失明，
    而框架仓 / 子项目仓这类嵌套仓恰恰是大部分改动真正发生的地方。实测：根仓干净、
    被 ignore 的嵌套仓有未提交改动、账本停在几天前 → 旧实现输出 `error 0` 且 exit 0。

    任一仓有活即 True（保守方向）；无仓报有活但有仓判不了则 None，同样保守。

    **边界必须是「之后」而不是「当天起」**：`git log --since=<D>` 含 D 当天，而 D 当天
    的活正是那条账本条目记的东西，算进来会让「记了账且此后没再动」也判成有活。
    """
    window = accounted_through + timedelta(days=1)
    targets = [("(项目根仓)", project_dir)]
    targets += [(r, project_dir / r) for r in (nested or [])]
    unknown = []
    for label, repo in targets:
        v = _repo_work_after(repo, window)
        if v is True:
            return True, window, label
        if v is None:
            unknown.append(label)
    if unknown:
        return None, window, "、".join(unknown)
    return False, window, ""


def _ledger_verdict(project_dir, accounted_through, msg, no_git_note, nested=None):
    """把「该不该为没记账报红」交给 `_work_after` 判，统一三处分支的降级口径。"""
    worked, window, who = _work_after(project_dir, accounted_through, nested)
    n = len(nested or []) + 1
    if worked is None:
        return [("error", f"{msg}{no_git_note}（判不了的仓：{who}）")]
    if not worked:
        return [("info", f"{msg}——不过 {window.isoformat()} 以来{n} 个仓"
                         f"（根仓 + {n - 1} 个嵌套仓）既无提交、工作树也干净，"
                         f"这段时间本就没有要记的活，故只作提示")]
    return [("error", f"{msg}（{window.isoformat()} 以来 {who} 有提交或工作树有改动，"
                      f"确实干了活）")]


# 账本条目的格式说明**直接内嵌在红灯里**，不指向任何文件：用户侧插件缓存里没有仓根
# 文档，指过去就是死链。
_LEDGER_FORMAT = (
    "条目格式：一级项为 `## YYYY-MM-DD · <一句话工作线标题>`，其下每个必含字段写成 "
    "`- **字段名**：内容`（冒号全角半角都认）。必含字段清单由 "
    "`.workframe-config.json` 的 `close_check.required_fields` 声明"
)


def check_ledger(project_dir, cfg, nested=None):
    close_cfg = (cfg or {}).get("close_check") or {}
    ledger_rel = close_cfg.get("ledger")
    if not ledger_rel:
        return [("info", "`.workframe-config.json` 里读不到 close_check.ledger，账本检查"
                         "跳过。本项默认是开着的（脚手架装机即写入），读不到通常是两种"
                         "情况：本项目主动改成了空壳 `\"close_check\": {}` 把它关掉，"
                         "或者装机时框架版本还没有这个默认值。想开启就把该键写成 "
                         '`"close_check": {"ledger": "<相对路径>", '
                         '"required_fields": ["模块", "验证", "提交"]}`，'
                         "并按这份字段清单建好那个账本文件；跳过本项不影响其余八项"
                         "（空壳只关账本这一项，不连带关掉 YAML 可解析性检查）")]
    ledger = project_dir / ledger_rel
    # 还没有账本 / 还没有任何条目时的参考点：窗口取「昨天与今天」，与「条目必须在
    # 今天或昨天」同口径 ⇒ accounted_through = 前天
    before_window = date.today() - timedelta(days=2)
    if not ledger.exists():
        return _ledger_verdict(
            project_dir, before_window,
            f"配置声明的账本文件不存在：{ledger_rel}——新建它并写第一条即可。"
            f"{_LEDGER_FORMAT}",
            "。（本目录不是 git 仓或 git 不可用，无法判断这段时间有没有干活，"
            "故按有活处理——这是保守方向：宁可多问一次，不静默放过漏记）",
            nested=nested)
    try:
        text = ledger.read_text(encoding="utf-8")
    except Exception as e:
        return [("warn", f"账本读取失败（{type(e).__name__}），跳过——多为编码或权限问题；"
                         f"确认 {ledger_rel} 是 UTF-8 文本且当前用户可读")]
    # 先剥 HTML 注释块：模板里的格式示例常写在注释里，不剥会被当成真实条目
    text = _HTML_COMMENT_RE.sub("", text)
    m = _LEDGER_HEAD_RE.search(text)
    if not m:
        return _ledger_verdict(
            project_dir, before_window,
            f"{ledger_rel} 找不到任何 `## YYYY-MM-DD ·` 条目（HTML 注释块内的示例不算）"
            f"——追加一条即可。{_LEDGER_FORMAT}",
            "。（本目录不是 git 仓或 git 不可用，无法判断这段时间有没有干活，"
            "故按有活处理）",
            nested=nested)
    try:
        entry_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:  # BUG-001①：正则 \d{2} 能匹配 2026-08-32 这类手误，date() 会抛
        return [("error", f"账本最新条目日期非法（{m.group(1)}-{m.group(2)}-{m.group(3)}）"
                          f"——能匹配条目头格式但不是真实日期（月份 >12 / 日期超出该月天数 "
                          f"这类手误），把标题行的日期改成真实日期后重跑")]
    # 账本条目标题是本地日；这里破例不用 UTC——跨零点收口（本地凌晨）时账本段
    # 还是前一天日期，用 UTC 算「今天」会把当天写的账误报成「没记账」。
    # 昨天豁免同理。不要把这处统一成 events.jsonl 的 UTC 口径。
    today = date.today()
    if entry_date not in (today, today - timedelta(days=1)):
        return _ledger_verdict(
            project_dir, entry_date,
            f"账本最新条目是 {entry_date}（{m.group(4).strip()[:30]}）——不在今天/昨天，"
            f"这轮工作没记账。补一条以今天日期开头的新段即可。{_LEDGER_FORMAT}",
            "。（本目录不是 git 仓或 git 不可用，无法判断这段时间有没有干活，"
            "故按有活处理）",
            nested=nested)
    seg_start = m.start()
    nxt = re.search(r"^## ", text[m.end():], re.M)
    segment = text[seg_start: m.end() + nxt.start()] if nxt else text[seg_start:]
    out = []
    for field in close_cfg.get("required_fields", []):
        # 只验字段头存在——「改动」等字段内容常在嵌套子项里，头行可以无内容；
        # 冒号全角半角都认（账本惯用全角）
        if not re.search(r"^\s*-\s+\*\*%s\*\*[:：]" % re.escape(field), segment, re.M):
            out.append(("error", f"账本最新条目（{entry_date}）缺字段：**{field}**"
                                 f"——在该条目下补一行 `- **{field}**：<内容>`；"
                                 f"字段头存在即可，内容允许写在下级子项里"))
    if not out:
        n = len(close_cfg.get("required_fields", []))
        out.append(("ok", f"账本最新条目（{entry_date}）{n} 个必含字段齐全"))
    return out


# ---------- 检查 8：YAML 可解析性 ----------

# 扫描面 = **白名单式路径契约**，一条也别多。用户项目里可以有任意 `*.yaml`
# （docker-compose.yml / CI 配置 / k8s manifest），它们不是框架定义的 schema，
# 本闸对它们没有立场。挡在外面的三类各有理由：
#   - 运行态状态目录下的 `*.yaml`：机器全量重写的运行态产物。坏了说明产出方坏了，
#     该由那段代码的单测保证；本闸报「YAML 语法非法」只会把人引去手改一个下次
#     就被重写掉的文件。
#   - skills / agents 的 frontmatter：真消费方是 **Claude Code 本体**，不是框架。
#     它用什么解析器、对哪些形态宽容，框架并不知道；用 PyYAML 去替一个未知解析器
#     立标准，就是另写一个判定式。
#   - 项目里其余任意 `*.yaml`：同上，框架没有立场。
# `projects/board.yaml` / `projects/issues/` / `projects/proposals/` **也不在这里**——
# 它们是项目级运行态数据、与模块收口无关（改一行模块文档也要跑本命令，那时这三类
# 根本没被碰），归 `workframe-doctor` 的 runtime 组。两处喂给 `_yaml_probe` 的文件集
# 不相交，判定式只有一份，故不构成双事实源。


def _load_yaml_probe():
    """惰性 import 同目录的 `_yaml_probe`（保持本模块 import 时无副作用）。"""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import _yaml_probe
    return _yaml_probe


def _yaml_scan_targets(project_dir, subs):
    """本闸的扫描面，返回 `[(path, is_frontmatter, label)]`。

    子模块那三类**复用 `_collect_submodules` 已经走过的遍历**（`sub_dir` /
    `yaml_path` / `overview_path`），不新写 glob；basic 与 requirement 两层是它
    够不到的层级，另行枚举——那是新增覆盖面，不是同一事实的第二份实现。
    """
    targets = []
    modules_dir = project_dir / "projects" / "modules"
    for s in subs:
        sub_dir = s.get("sub_dir")
        if sub_dir is None:
            continue
        yp = s.get("yaml_path")
        if yp is not None and yp.exists():
            targets.append((yp, False, f"{s['rel']}/submodule.yaml"))
        ov = s.get("overview_path")
        if ov is not None and ov.exists():
            targets.append((ov, True, f"{s['rel']}/overview.md"))
        for cs in sorted((sub_dir / "current-state").glob("*.md")):
            targets.append((cs, True, f"{s['rel']}/current-state/{cs.name}"))
        for ov2 in sorted(sub_dir.glob("requirements/**/overview.md")):
            targets.append((ov2, True, ov2.relative_to(modules_dir).as_posix()))
        for meta in sorted(sub_dir.glob("requirements/**/meta.yaml")):
            targets.append((meta, False, meta.relative_to(modules_dir).as_posix()))
        for prd in sorted(sub_dir.glob("requirements/**/prd.md")):
            targets.append((prd, True, prd.relative_to(modules_dir).as_posix()))
    for basic_yaml in sorted(modules_dir.glob("*/module.yaml")):
        targets.append((basic_yaml, False, f"{basic_yaml.parent.name}/module.yaml"))
    for basic_ov in sorted(modules_dir.glob("*/overview.md")):
        targets.append((basic_ov, True, f"{basic_ov.parent.name}/overview.md"))
    root_ov = modules_dir / "overview.md"
    if root_ov.exists():
        targets.append((root_ov, True, "overview.md"))
    # 同一份文件可能被两条规则同时圈到（如 modules/overview.md）；按路径去重，
    # 保留先出现的 label。重复扫不会改判定，但会让同一份坏文件报两条红灯。
    seen, deduped = set(), []
    for path, is_fm, label in targets:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((path, is_fm, label))
    return deduped


def check_yaml_parse(project_dir, cfg, subs):
    """扫描面内每份文件过一次**真** YAML 解析器。

    **本项证明什么**：这些文件是合法 YAML，真解析器读得进去。
    **本项不证明什么**：框架各处的正则读数与真解析器一致。「两边都成功但取值不同」
    这一类（最典型是重复键：正则取第一个、PyYAML 取最后一个）本项完全看不见。

    为什么需要它：框架读 `submodule.yaml` 与 current-state frontmatter 的路径全部
    走自己的正则，**没有一处用真解析器**，于是语法上非法的 YAML 能全程报绿。这些
    文件是框架发布的 schema，必须是合法 YAML——因为它会被编辑器、`yq`、GitHub
    渲染、以及将来任何一个改用真解析器的框架版本读到。当前全靠正则读只是实现细节，
    不是许可证。

    本项失败时，**检查 1-7 对该文件的读数不可信**：它们的正则可能读到脏值、也可能
    读到 None 而报成「字段不存在」，那是假归因。
    """
    close_cfg = (cfg or {}).get("close_check") or {}
    # 与账本检查的开关语义**有意不同**，别照搬：账本要的是一个路径，读不到就无从检查，
    # 故「缺省 = 跳过」；本项要的是一个布尔，缺省必须是**开**——「这台机器碰巧没装库」
    # 不能等于「这一项通过」。所以只有显式写 `"yaml_parse": false` 才关掉；
    # `"close_check": {}` 这个空壳（文档里关账本检查的写法）**不**连带关掉本项，
    # 否则关一项会静默连坐关掉另一项。
    if close_cfg.get("yaml_parse") is False:
        return [("info", "`.workframe-config.json` 里显式声明了 "
                         '`"close_check": {"yaml_parse": false}`，YAML 可解析性检查已关闭'
                         "——这是一次有记录的声明，不是静默跳过。想重新打开就删掉该键")]

    targets = _yaml_scan_targets(project_dir, subs)
    if not targets:
        return [("info", "扫描面内没有可查的文件（`projects/modules/` 下还没有模块文档）"
                         "——**这不等于检查通过**，本项一份文件都没读到")]

    probe = _load_yaml_probe()
    available, detail = probe.pyyaml_status()
    if not available:
        # 「缺库」与「文件坏了」必须是两条互不相同的红灯：前者是环境问题、后者正是
        # 本项要抓的缺陷。合成一条 `except Exception` 吞掉，就是本任务要修的那个形状。
        return [("error",
                 f"YAML 可解析性**未验证**：本机导入 PyYAML 失败（{detail}）——"
                 f"这一项**没有跑**，不是通过。扫描面内 {len(targets)} 份文件"
                 f"一份都没被真解析器读过。两条出路：①装上——`pip install pyyaml`；"
                 f"②在 `.workframe-config.json` 里显式关掉——"
                 f'`"close_check": {{"yaml_parse": false}}`。'
                 f"关掉是一次有记录的声明，缺库静默报绿不是")]

    out = []
    n_ok = 0
    n_skipped = 0
    for path, is_fm, label in targets:
        try:
            raw = path.read_bytes()
        except Exception as e:
            n_skipped += 1
            out.append(("warn", f"{label} 读取失败（{type(e).__name__}），跳过"
                                f"——本项**没有**检查这份文件，这不是它通过了"))
            continue
        # 解码是**两条分支的共同前置**，统一在这里做一次，分支内不再各自 decode。
        # 这样「读不出来」的处置由**一份实现**给出，而不是两处措辞碰巧一致。
        # 此前 frontmatter 那半走 `_frontmatter()`，而它内部是 `except: return ""`，
        # 空串又是合法 YAML（`safe_load("") is None`）——于是任何**非 UTF-8 可解码**的
        # `.md` 被静默剔出扫描面：**无 finding 行、状态报绿**，只在「N 份（扫描面 M 份）」
        # 的数字差里留一道没人会去看的痕。实测两例：`code-map.md` 存成 UTF-16 时本项报绿，
        # 而检查 6 同时报「没有 source_paths 字段」（字段就在文件里，**假归因**）；
        # `overview.md` 存成 GBK 时整轮 rc=0 全绿。这与 BOM 那条是同一个矛盾的两个入口
        # ——BOM 堵住一个，任何非 UTF-8 编码这条还开着。目标用户是中文 Windows 用户，
        # 记事本另存为「ANSI / Unicode」即命中。
        #
        # **不动 `_frontmatter` 自己的 `except: return ""`**：它服务于检查 3/6，改它是
        # 跨检查的行为改动；在本 checker 里区分「读不出来」与「没有 frontmatter」即可。
        try:
            decoded = raw.decode("utf-8")
        except Exception as e:
            n_skipped += 1
            out.append(("warn",
                        f"{label} **读不出来**——不是 UTF-8 文本（{type(e).__name__}）。"
                        f"本项**没有**检查这份文件，这不是它通过了。"
                        f"注意这与「不是合法 YAML」是两回事：那个说文件内容写坏了，"
                        f"这个说文件根本没被读进来。后果：检查 3/6 会把它的字段全部读成"
                        f"「不存在」（假归因红灯）。用 UTF-8 重存该文件即可"))
            continue
        if is_fm:
            # BOM 会让 `_frontmatter` 的 `^---` 失配而返回空串，而空串是**合法 YAML**
            # ——不单独判的话本项会对这份文件报绿，而检查 3/6 同时报「字段不存在」，
            # 两个结论矛盾且都错。
            if probe.bom_blinds_frontmatter(raw):
                out.append(("error",
                            f"{label} 以 **UTF-8 BOM** 开头——BOM 让 frontmatter 抽取的 "
                            f"`^---` 失配，本命令的 `_frontmatter()` 会返回空串，于是"
                            f"检查 3/6 把该文件的字段全部读成「不存在」（假归因红灯），"
                            f"而本项若只看那段空串反而会报绿。用不带 BOM 的 UTF-8 重存该文件"))
                continue
            text = _frontmatter(path)
            if not text:
                # 走到这里说明文件**解码成功但没有 frontmatter**——正文型 markdown，
                # 不是错误，跳过不计入。上面那道解码闸保证了这一支不会再混进
                # 「读不出来」的文件。
                continue
            line_offset = 1          # 抽取时丢掉了开头那行 `---`，行号平移回原文件坐标系
        else:
            text = decoded      # 复用上面那次解码，不再 decode 第二遍
            line_offset = 0
        kind, info = probe.probe_yaml_text(text, line_offset=line_offset)
        if kind == probe.OK:
            n_ok += 1
        elif kind == probe.PLACEHOLDER:
            # 单列一条文案：占位符残留报成 `ConstructorError: found unhashable key`
            # 会把人引向「YAML 语法」，而真相是「模板渲染漏了一个字段」。
            out.append(("error",
                        f"{label} 残留未渲染的模板占位符 `{info}`——这不是 YAML 语法问题，"
                        f"是 module-init / code-to-doc 生成这份文件时漏替换了一个占位符。"
                        f"把它替换成真实值即可（未加引号的 `{{{{X}}}}` 同时也让整份文件"
                        f"解析不了，所以本项一并拦下）"))
        else:
            hint = ("。控制字符多半是编辑时把「值 → `#` 注释」之间的分隔空格误写成了"
                    "不可见字符，删掉重打那一处即可"
                    if "ReaderError" in info else
                    "。常见成因：双引号标量里有未转义的裸双引号导致标量提前终止、"
                    "用 tab 缩进、或值里有未加引号的特殊字符")
            out.append(("error",
                        f"{label} **不是合法 YAML**：{info}{hint}。"
                        f"注意：本项失败时，检查 1-7 对该文件的读数全部不可信"
                        f"——它们走正则，可能读到脏值，也可能读成「字段不存在」"))
    if not [x for x in out if x[0] == "error"]:
        # 有跳过时**不发 ok**：一条 `✓ N 份可解析（扫描面 M 份）` 里的差值没人会去做减法，
        # 而那个差值正是「有文件根本没被读进来」。把它抬到结论里并降级成 warn。
        lvl = "warn" if n_skipped else "ok"
        skipped = (f"；**另有 {n_skipped} 份读不出来、未被检查**（见上）" if n_skipped else "")
        out.append((lvl, f"{n_ok} 份模块文档经真 YAML 解析器可解析"
                         f"（PyYAML {detail}；扫描面 {len(targets)} 份）{skipped}。"
                         f"**本项只证明文件是合法 YAML，不证明各处正则读数与真解析器一致**"
                         f"——重复键这类「两边都成功但取值不同」的形态本项看不见"))
    return out


# ---------- 检查 9：纪律产物痕迹 ----------

# 档位与升级门槛是**代码常量**，不进 config：改它们就是改闸口径，走用户审批。
# 降级建议（升 error 之后确认的假红累计 → 建议降回 warn）在 warn 档不可达，本档不实现，
# 升 error 的那一批同时实现。
DISCIPLINE_TRACE_LEVEL = "warn"
DISCIPLINE_UPGRADE_MIN_CLOSEOUTS = 10
DISCIPLINE_UPGRADE_MIN_LOCAL_DAYS = 7
# 计数日志文件名。validate 的 `NON_EVENT_APPEND_LINE_OWNERS` 靠「追加调用第一个实参里的文件名
# 字面（含本文件里这个常量的赋值）」认出它不是事件流——改名或改成拼接须同步那张表。
_TRACE_LOG = "discipline-trace-log.jsonl"
TRACE_VERDICTS = ("missed", "false-positive")
FINGERPRINT_SHOW = 12


def _status_of(findings):
    return ("error" if any(l == "error" for l, _ in findings)
            else "warn" if any(l == "warn" for l, _ in findings) else "ok")


def trace_window_start(today=None):
    """窗口起点：本地「昨天 00:00」，带时区偏移（aware）。与 `[ledger]` 的 {今天, 昨天} 同口径。

    **git 侧必须拿这个带时刻与偏移的值去查**：只给日期的 `--since=<YYYY-MM-DD>` 会被 git
    用**当前钟点**补上时分，窗口首日早于此刻的提交被静默截掉（实测：晚上 22 点查
    `--since=<昨天>`，昨天 00:30 的提交不在结果里）。
    """
    today = today or date.today()
    return datetime.combine(today - timedelta(days=1), dtime.min).astimezone()


def _parse_event_ts(value):
    """事件 ts → aware UTC datetime；解析不了返回 None。**复用** doctor 的 `_parse_ts`。

    时间比较一律先解析再比，禁止字符串比较（跨时区的误判实证写在那个函数的 docstring 里）。
    延迟 import：doctor 模块 import 无副作用，但只在本项真要比时间时才付加载代价。
    """
    from workframe_doctor import _parse_ts
    return _parse_ts(value)


def _repo_touched_since(repo_dir, rels, since, known_repo=False):
    """`rels`（仓相对 pathspec）在 `since` 之后有提交，或工作树 / 暂存区有改动。

    返回 True / False / None（判不了：不是 git 仓或 git 失败）。

    **不复用 `_repo_work_after`**：那个查的是整仓（不带 pathspec），且 `--since` 只给日期
    ——谓词比本项要的「code_paths 被改动」弱，时刻还会被 git 截断。
    `rels` 为空时直接 False：空 pathspec 等于整仓，会把「没有路径可判」读成「整仓有活」。
    """
    if not rels:
        return False
    if not known_repo:
        rc, lines, _ = _git(repo_dir, "rev-parse", "--is-inside-work-tree")
        if rc != 0 or not lines or lines[0].strip() != "true":
            return None
    rc, lines, _ = _git(repo_dir, "log", "-1", "--format=%H",
                        f"--since={since.isoformat()}", "--", *rels)
    if rc != 0:
        return None
    if any(x.strip() for x in lines):
        return True
    rc, lines, _ = _git(repo_dir, "status", "--porcelain", "--", *rels)
    if rc != 0:
        return None
    return any(x.strip() for x in lines)


def trace_premise(project_dir, csm, subs, since):
    """适用前提：窗口内任一子模块的 `code_paths` 在其所属仓有提交或工作树改动。

    返回 `(applicable, hit_subs, unknown_repos, nested)`，applicable ∈ True / False / "unknown"：
    任一仓 True ⇒ True；无 True 而有仓判不了 ⇒ "unknown"（与 False 分开——「判不了」与
    「确实没改」在日志里同形，事后就数不出闸有几次是瞎的）；全 False ⇒ False。
    仓集合 = 根仓 ＋ `discover_nested_repos`，分仓与剥前缀复用 `_split_paths_by_repo`。
    """
    all_paths = sorted({p for s in subs for p in s["code_paths"]})
    nested = csm.discover_nested_repos(all_paths)
    per_repo = {}
    for s in subs:
        for owner, rels in _split_paths_by_repo(s["code_paths"], nested).items():
            per_repo.setdefault(owner, {}).setdefault(s["rel"], []).extend(rels)
    hit, unknown = set(), []
    for owner, by_sub in sorted(per_repo.items()):
        repo_dir = project_dir / owner if owner else project_dir
        union = sorted({r for rels in by_sub.values() for r in rels})
        touched = _repo_touched_since(repo_dir, union, since)
        if touched is None:
            unknown.append(owner or "(项目根仓)")
            continue
        if not touched:
            continue
        for sub_rel, rels in sorted(by_sub.items()):
            if len(by_sub) == 1 or _repo_touched_since(repo_dir, sorted(set(rels)), since,
                                                       known_repo=True):
                hit.add(sub_rel)
    if hit:
        return True, hit, unknown, nested
    return ("unknown" if unknown else False), hit, unknown, nested


def _count_skill_used_since(project_dir, since):
    """窗口内项目里的 `skill_used` 条数：主文件 ＋ 未并回的 spill，坏行跳过。"""
    state = _state_dir_of(project_dir)
    files = [state / "events.jsonl"] + sorted(state.glob("events.*" + SPILL_SUFFIX))
    n = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if '"skill_used"' not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict) or ev.get("type") != "skill_used":
                continue
            ts = _parse_event_ts(ev.get("ts"))
            if ts is not None and ts >= since:
                n += 1
    return n


def trace_criterion_b(ledger_findings):
    """判据 b：映射同次运行的 `[ledger]` 结论。返回 `(state, 不适用原因或 None)`。

    error ⇒ 红；ok ⇒ 绿；只有 info（没配账本 / 这段时间本就没活）⇒ 不适用；
    warn（读失败 / config 解析失败 / 检查自身异常）⇒ 不适用 ＋ 原因。
    **b 红时同次运行必然 exit 1**，按计数口径不进升级计数——b 的作用是让本项输出自足，
    不是独立信号。
    """
    levels = {lv for lv, _ in ledger_findings}
    if "error" in levels:
        return "red", None
    if "warn" in levels:
        return "na", "`[ledger]` 自身没判成（读失败 / 配置解析失败 / 检查异常）"
    if "ok" in levels:
        return "green", None
    return "na", "没配账本，或这段时间本就没有要记的活"


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _norm_module(value):
    return value.replace("\\", "/").strip().strip("/") if isinstance(value, str) else None


def trace_criterion_c(project_dir, hit_subs, today=None):
    """判据 c：命中子模块的看板候选任务里，有没有今天 / 昨天更新的。返回 `(state, 原因, 候选数)`。

    候选 = `tasks` 里 `module` ∈ 命中子模块，或 `affected_modules` 与之有交集（按二段式
    `<basic>/<sub>` 比），排除 `cancelled`。候选为空 ⇒ 不适用；**任一**候选 `updated_at` 在
    {今天, 昨天} ⇒ 绿；否则红。看板不存在 / 读不了 ⇒ 不适用 ＋ 原因。
    **不收窄到进行中状态**：那样「今天刚完成的任务」会判红、要人去标假红。代价是分辨力低
    （一个模块挂几十条任务时近乎恒绿），写在能力边界里。
    """
    today = today or date.today()
    board = project_dir / "projects" / "board.yaml"
    if not board.is_file():
        return "na", "看板 projects/board.yaml 不存在", 0
    available, detail = _load_yaml_probe().pyyaml_status()
    if not available:
        return "na", f"PyYAML 不可用（{detail}），看板读不了", 0
    try:
        import yaml
        data = yaml.safe_load(board.read_text(encoding="utf-8"))
    except Exception as e:
        return "na", f"看板解析失败（{type(e).__name__}）", 0
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        return "na", "看板里没有 tasks 列表", 0
    cands = []
    for t in tasks:
        if not isinstance(t, dict) or t.get("status") == "cancelled":
            continue
        mods = {_norm_module(t.get("module"))}
        if isinstance(t.get("affected_modules"), list):
            mods |= {_norm_module(x) for x in t["affected_modules"]}
        if mods & hit_subs:
            cands.append(t)
    if not cands:
        return "na", "命中子模块在看板上没有候选任务", 0
    recent = {today, today - timedelta(days=1)}
    if any(_as_date(t.get("updated_at")) in recent for t in cands):
        return "green", None, len(cands)
    return "red", None, len(cands)


def check_discipline_trace(project_dir, csm, subs, ledger_findings, verdict=None, today=None):
    """检查 9：纪律产物痕迹——「该留的痕迹留了没有」。**warn 档：只产 ok / info / warn。**

    判定式各段：适用前提 `trace_premise`；判据 a 只提示（`_count_skill_used_since`）；
    b `trace_criterion_b`；c `trace_criterion_c`。项级：前提不成立 ⇒ 不适用；前提成立时
    b 或 c 红 ⇒ 红；b、c 都不适用 ⇒ 不适用；其余 ⇒ 绿。
    `verdict`（dict）传入时被填上 `{applicable, result, criteria, repos}`，供 `main()` 记账；
    本函数自身零写入。

    **能力边界**（陈述可证伪，也是红绿灯怎么读的前提）：
      - 绿是**项目级**痕迹：`events.jsonl` 与看板不分会话、不分工作线，别的会话或子 agent
        留下的痕迹同样让它绿；两日窗口还会遮住「两日内较早那次收口留下的痕迹」。高频多会话
        项目里 b / c 近乎恒绿，本项主要在低频单人项目里有分辨力。
      - 判据 a 不判红：协议允许「没用 skill 就不写 `skill_used`」，判红即结构性假红。
      - 判据 b 与 `[ledger]` 完全重合；b 红的运行 exit 1，按定义不进升级计数。
      - 判据 c 的候选集不收窄，一个模块挂很多任务时近乎恒绿。
      - `code_paths` 没覆盖的改动不触发前提；非 git 项目永远不适用。
      - 升级计数：只有整体 exit 0 的运行进计数；「收口身份」取账本最新条目头，同一身份取
        最后一次运行，无账本时退回指纹。指纹是各仓 HEAD ＋ porcelain，porcelain 对内容不敏感
        ——同一批脏文件、内容不同的两个状态合成同一个指纹。
      - 标记的 `marked_by` 验证不了是谁敲的命令（与 `self_signoff` 事件同构）。
      - 状态目录没被 git 忽略的项目，首次写计数日志会改变 porcelain，从而改变指纹——新增的
        不止日志本身，还有它的锁文件 `discipline-trace-log.jsonl.lock`（锁超时时另有 spill 文件）。
      - 计数不切割起点：装上本版之后的真实收口运行都计入。
      - 降级建议在 warn 档不可达，本档不实现；升 error 的那一批同时实现。
    """
    today = today or date.today()
    since = trace_window_start(today)
    win = since.strftime("%Y-%m-%d %H:%M")
    v = {"applicable": False, "result": "na",
         "criteria": {"a": "na", "b": "na", "c": "na"}, "repos": []}

    def done(findings):
        if verdict is not None:
            verdict.update(v)
        return findings

    if csm is None or not subs:
        return done([("info", "不适用：没有模块树或一个子模块都没有，适用前提无对象可查"
                              "——这不等于检查通过")])
    applicable, hit, unknown, nested = trace_premise(project_dir, csm, subs, since)
    v["repos"], v["applicable"] = list(nested), applicable
    if applicable == "unknown":
        return done([("info", f"不适用：{'、'.join(unknown)} 判不了（不是 git 仓或 git 失败），"
                              f"其余仓在窗口（{win} 起）内 code_paths 无改动——前提判不了，"
                              f"本次既不计入也不清零")])
    if not applicable:
        return done([("ok", f"不适用：窗口（{win} 起）内没有任何子模块的 code_paths 被改动，"
                            f"三条判据都不判")])
    n_skill = _count_skill_used_since(project_dir, since)
    b, b_why = trace_criterion_b(ledger_findings)
    c, c_why, n_cands = trace_criterion_c(project_dir, hit, today)
    v["criteria"] = {"a": "green" if n_skill else "info", "b": b, "c": c}
    v["result"] = "red" if "red" in (b, c) else ("na" if (b, c) == ("na", "na") else "green")
    label = {"red": "红", "green": "绿", "na": "不适用"}

    def state(x, why):
        return label[x] + (f"：{why}" if x == "na" and why else "")

    head = (f"{label[v['result']]}（b {state(b, b_why)}；c {state(c, c_why)}）"
            f"——命中子模块 {len(hit)} 个，窗口 {win} 起")
    out = []
    if v["result"] == "green":
        out.append(("ok", head + "；窗口内项目里有痕迹（项目级：别的会话或子 agent 留下的同样"
                                 "算数，不代表本轮收口一定留了）"))
    elif v["result"] == "na":
        out.append(("info", head + "；b、c 都判不了，本次既不计入也不清零"))
    else:
        out.append((DISCIPLINE_TRACE_LEVEL, head + "；warn 档，不阻断收口"))
    if b == "red":
        out.append((DISCIPLINE_TRACE_LEVEL,
                    "b 红：`[ledger]` 判了 error——收口未记账。同次运行必然 exit 1，"
                    "本次不进升级计数；补账本后重跑"))
    if c == "red":
        out.append((DISCIPLINE_TRACE_LEVEL,
                    f"c 红：命中子模块的 {n_cands} 个看板候选任务（已排除 cancelled）没有一个在"
                    f"今天 / 昨天更新。两种成因：①看板没更新；②本轮还没走到看板流转——先走完再"
                    f"重跑本命令，这种红不算闸判错"))
    if not n_skill:
        out.append(("info", "窗口内项目里没有任何 `skill_used`——若本轮用过 skill，走收尾 "
                            "Step 1 补记（只提示，不算红、不进计数）"))
    return done(out)


def trace_fingerprint(project_dir, nested):
    """本次运行的指纹：各仓 `HEAD` ＋ `git status --porcelain` 的 sha256，仓集合同适用前提。

    porcelain 对内容不敏感：同一批脏文件、内容不同的两个状态会合成同一个指纹。
    """
    h = hashlib.sha256()
    for owner in [""] + sorted(nested or []):
        repo = project_dir / owner if owner else project_dir
        rc1, head, _ = _git(repo, "rev-parse", "HEAD")
        rc2, status, _ = _git(repo, "status", "--porcelain")
        h.update(f"{owner}|{rc1}|{head[0].strip() if head else ''}|{rc2}\n".encode("utf-8"))
        h.update("\n".join(sorted(x.rstrip() for x in status)).encode("utf-8") + b"\n")
    return h.hexdigest()


def closeout_identity(project_dir, cfg):
    """收口身份：账本最新条目头（`## 日期 · 标题`）；读不到返回 None，由调用方退回指纹。"""
    ledger_rel = ((cfg or {}).get("close_check") or {}).get("ledger")
    if not ledger_rel:
        return None
    try:
        text = (project_dir / ledger_rel).read_text(encoding="utf-8")
    except Exception:
        return None
    m = _LEDGER_HEAD_RE.search(_HTML_COMMENT_RE.sub("", text))
    return f"ledger:{m.group(0).strip()}" if m else None


def append_trace_row(project_dir, row):
    """计数日志的**唯一**写入点：运行行（本命令 `main()`）与标记行（标记命令）都走这里。

    返回 `_state_io.append_line` 的三态：True 写进主文件 / False 锁超时、已落 spill /
    None 主文件与 spill 都没写成。
    """
    return append_line(_state_dir_of(project_dir) / _TRACE_LOG,
                       json.dumps(row, ensure_ascii=False))


def read_trace_rows(project_dir):
    """计数日志全部可解析的行：主文件 ＋ spill，按 ts 稳定排序，坏行跳过。

    **spill 必须一起读**：`append_line` 锁超时落的 `<stem>.<pid>.spill.jsonl` 永不并回
    ——全仓唯一的并回调用只管 `events.jsonl`。
    """
    state = _state_dir_of(project_dir)
    files = [state / _TRACE_LOG] + sorted(
        state.glob(f"{Path(_TRACE_LOG).stem}.*{SPILL_SUFFIX}"))
    rows = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    far = datetime.max.replace(tzinfo=timezone.utc)
    rows.sort(key=lambda r: _parse_event_ts(r.get("ts")) or far)
    return rows


def count_trace(rows):
    """从计数日志现算升级计数（纯函数，吃任意合成日志；计数本身不单独存）。

    规则：
      - 只看整体 exit 0、结果为绿 / 红的运行（有 error 按本命令的契约就不是收口；不适用既不
        计入也不清零）。同一收口身份只取**最后一次**这样的运行。
      - 标记挂到排在它之前、指纹相同的最后一次运行上，同一次运行取最后一条标记，只作用于红的运行。
      - 绿 +1；红且标「确实漏做」+1；红且标「闸判错」**清零**（落在跨度内任一运行上都清零，
        即便它不是该身份被计入的那一次）；红未标记 → 挂起：不加、不清零，记进待确认。
      - 升级建议 = 计数 ≥ 门槛次数，且计入的收口分布在 ≥ 门槛天数个不同本地日，且跨度内没有
        待确认的红。
    """
    runs = [r for r in rows if r.get("kind") == "run"]
    # 标记挂到「日志里排在它之前、指纹相同的最后一次运行」上（`rows` 须已按 ts 排好，
    # `read_trace_rows` 即如此），**不**套到标记之后同指纹的另一次运行：两次收口之间各仓 HEAD
    # 不变、脏文件集合相同时指纹会重合，按指纹全局挂会把一次确认自动套到另一次红上，压掉一条
    # 本该待确认的红。与标记命令取目标的口径相同（该指纹在标记那一刻的最后一次运行）。
    marks, last_run = {}, {}
    for r in rows:
        fp = r.get("fingerprint")
        if not isinstance(fp, str):
            continue
        if r.get("kind") == "run":
            last_run[fp] = r
        elif r.get("kind") == "mark" and r.get("verdict") in TRACE_VERDICTS and fp in last_run:
            marks[id(last_run[fp])] = r["verdict"]
    eligible = [r for r in runs if r.get("exit_code") == 0 and r.get("result") in ("green", "red")]
    last_of = {}
    for i, r in enumerate(eligible):
        last_of[r.get("identity") or f"fingerprint:{r.get('fingerprint')}"] = i
    reps = set(last_of.values())
    count, days, pending, b_app, c_app = 0, set(), [], 0, 0
    for i, r in enumerate(eligible):
        mark = marks.get(id(r)) if r.get("result") == "red" else None
        if mark == "false-positive":
            count, days, pending, b_app, c_app = 0, set(), [], 0, 0
            continue
        if i not in reps:
            continue
        if r.get("result") == "green" or mark == "missed":
            count += 1
            ts = _parse_event_ts(r.get("ts"))
            if ts is not None:
                days.add(ts.astimezone().date())
            crit = r.get("criteria") if isinstance(r.get("criteria"), dict) else {}
            b_app += crit.get("b") in ("green", "red")
            c_app += crit.get("c") in ("green", "red")
        else:
            pending.append(str(r.get("fingerprint") or ""))
    return {"count": count, "days": len(days), "pending": pending,
            "b_applicable": int(b_app), "c_applicable": int(c_app),
            "suggest": (count >= DISCIPLINE_UPGRADE_MIN_CLOSEOUTS
                        and len(days) >= DISCIPLINE_UPGRADE_MIN_LOCAL_DAYS and not pending)}


def trace_count_findings(s):
    """把 `count_trace` 的结论写成 findings（全部 info——升级只报建议，不自动升）。"""
    n, d, pend = s["count"], s["days"], s["pending"]
    fps = "、".join(p[:FINGERPRINT_SHOW] for p in pend[:5]) + ("…" if len(pend) > 5 else "")
    need_n, need_d = DISCIPLINE_UPGRADE_MIN_CLOSEOUTS, DISCIPLINE_UPGRADE_MIN_LOCAL_DAYS
    if s["suggest"]:
        return [("info", f"已满足升级条件：自上次清零以来 {n} 次合格收口、跨 {d} 个本地日、无待确认"
                         f"的红——建议把 `[discipline-trace]` 升为 error（改闸口径须用户审批，本命令"
                         f"不自动升）。本段计数中 b 适用 {s['b_applicable']} 次、"
                         f"c 适用 {s['c_applicable']} 次")]
    if n >= need_n and d >= need_d and pend:
        return [("info", f"计数 {n}（跨 {d} 天），其中 {len(pend)} 次红待确认：{fps}——每一次红都经"
                         f"`workframe-discipline-mark` 确认之后才建议升级")]
    line = f"升级计数 {n}/{need_n}（跨 {d}/{need_d} 个本地日）"
    if pend:
        line += f"；{len(pend)} 次红待确认：{fps}"
    return [("info", line)]


def record_trace_run(project_dir, trace, exit_code, cfg):
    """`main()` 专用：追加本次运行行，再从日志现算计数。返回要追加到检查 9 输出里的 findings。"""
    fp = trace_fingerprint(project_dir, trace.get("repos"))
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": "run",
           "fingerprint": fp, "exit_code": exit_code,
           "identity": closeout_identity(project_dir, cfg) or f"fingerprint:{fp}",
           "applicable": trace.get("applicable"), "result": trace.get("result"),
           "criteria": trace.get("criteria")}
    written = append_trace_row(project_dir, row)
    hint = f"本次运行指纹 {fp[:FINGERPRINT_SHOW]}"
    if row["result"] == "red":
        hint += (f"——确认这次红：`workframe-discipline-mark --fingerprint {fp[:FINGERPRINT_SHOW]} "
                 f"--verdict missed|false-positive --by qa|user|self`（闸判错只收 qa / user）")
        if exit_code:
            hint += "；本次 exit 1，不进计数"
    out = [("info", hint)]
    if written is False:
        out.append(("warn", "计数日志锁超时，本行落到了 spill 文件——判据结果见上；计数照读 spill，"
                            "本次仍计入（spill 不会被并回主文件）"))
    elif written is None:
        out.append(("warn", "计数日志没写成（主文件与 spill 都失败）——判据结果见上，本次未计入计数"))
    return out + trace_count_findings(count_trace(read_trace_rows(project_dir)))


def _read_cfg_quiet(project_dir):
    try:
        return json.loads((project_dir / ".workframe-config.json").read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------- 汇总与入口 ----------

CHECKS = [
    ("stale-clean", "stale 清单已清", check_stale_clean),
    ("code-paths", "code_paths 形态与指向健康", check_code_paths_health),
    ("ref-consistency", "基准引用同值", check_ref_consistency),
    ("module-refreshed", "命中模块已刷新", check_module_refreshed),
    ("repo-status", "代码仓提交状态", check_repo_status),
    ("code-map-coverage", "code-map 覆盖率对账", check_code_map_coverage),
]


def run_all(project_dir, trace=None):
    """跑全部检查，返回 [{id, name, status, findings}]。可被 validate smoke import。

    **零写入**：validate smoke 与单测在同一个临时目录上连调多次，写盘会让结果依赖调用顺序。
    `trace`（dict）传入时被填上检查 9 的结构化结论，由 `main()` 据此追加计数日志。
    """
    results = []
    modules_ok = (project_dir / "projects" / "modules").is_dir()
    subs = []
    if modules_ok:
        csm = _load_csm(project_dir)
        subs = _collect_submodules(project_dir, csm)
    if modules_ok and not subs:
        # 「扫 0 报 ok」：`projects/modules/` 在、但一个子模块都没有时，六项检查各自
        # 对空集判绿，输出一排绿勾——看起来像「全都查过且没问题」，实际一项都没查到
        # 东西。合并成一条 info，并说清怎么让它真正生效（否则用户以为已经在被看着）。
        results.append({
            "id": "modules", "name": "modules 体系", "status": "ok",
            "findings": [{"level": "info", "msg":
                          "projects/modules/ 在，但还没有任何子模块（`<basic>/<sub>/"
                          "submodule.yaml`），检查 1-6 无对象可查——**这不等于检查通过**。"
                          "想让它们生效：先用 module-init 建基础模块与子模块，在子模块的 "
                          "submodule.yaml 里把 `code_paths` 填成本模块涉及的代码路径，"
                          "再跑 code-to-doc 生成 current-state/（基准 commit 由它写入）。"
                          "这两步做完，六项才开始真正对账"}]})
    elif modules_ok:
        for cid, name, fn in CHECKS:
            try:
                findings = fn(project_dir, csm, subs)
            except Exception as e:  # 检查自身异常降级为该项 warn，整轮继续
                findings = [("warn", f"检查自身异常：{type(e).__name__}: {str(e)[:120]}")]
            status = ("error" if any(l == "error" for l, _ in findings)
                      else "warn" if any(l == "warn" for l, _ in findings) else "ok")
            results.append({"id": cid, "name": name, "status": status,
                            "findings": [{"level": l, "msg": m} for l, m in findings]})
    else:
        results.append({"id": "modules", "name": "modules 体系", "status": "ok",
                        "findings": [{"level": "info",
                                      "msg": "projects/modules/ 不存在，检查 1-6 跳过"
                                             "——本项目没建模块树，六项无对象可查。"
                                             "想启用：跑 module-init 建基础模块与子模块，"
                                             "在 submodule.yaml 填 `code_paths`，再跑 "
                                             "code-to-doc 生成 current-state/"}]})
    cfg = None
    cfg_error = None
    cfg_path = project_dir / ".workframe-config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as e:
            # BUG-001②：config 存在但解析失败 ≠ 未配置——静默当「未配置」跳过
            # 等于无声禁用账本检查（fail-open），必须出 warn 点明
            cfg_error = type(e).__name__
    if cfg_error:
        findings = [("warn", f".workframe-config.json 存在但解析失败（{cfg_error}）——"
                             f"账本检查无法判定；修复 config 后重跑")]
    else:
        try:
            # 账本的 git 活动判定必须扫**与检查 5 相同的仓集合**（根仓 + 已发现的嵌套
            # 仓）。只看根仓会在嵌套仓拓扑下把「真干活没记账」判成全绿——根仓通常
            # ignore 嵌套仓，对其整体失明。发现逻辑复用 csm.discover_nested_repos，
            # 不另写第二份。
            nested = []
            if modules_ok and subs:
                try:
                    nested = csm.discover_nested_repos(
                        sorted({p for s in subs for p in s["code_paths"]}))
                except Exception:
                    nested = []
            findings = check_ledger(project_dir, cfg, nested=nested)
        except Exception as e:  # 与 CHECKS 循环同款降级：检查自身异常不炸整个命令
            findings = [("warn", f"检查自身异常：{type(e).__name__}: {str(e)[:120]}")]
    status = ("error" if any(l == "error" for l, _ in findings)
              else "warn" if any(l == "warn" for l, _ in findings) else "ok")
    results.append({"id": "ledger", "name": "账本条目字段", "status": status,
                    "findings": [{"level": l, "msg": m} for l, m in findings]})
    ledger_findings = findings
    # 检查 8 追加在账本之后，**不进 CHECKS 循环**。语义上它该第一跑（文件解析不了，
    # 前七项对它的正则读数全都不可信），工程上只有追加到末尾代价可控：插进 CHECKS
    # 会把账本从第 7 挤到第 8，全仓十余处「第 7 项 / 检查 7」的序数引用当场作废；
    # 插到 CHECKS[0] 更会让四十余处「检查 3/4/5/6」一起失效。红灯文案里点明了
    # 「本项失败时检查 1-7 的读数不可信」，把顺序代价补在文案上。
    try:
        findings = check_yaml_parse(project_dir, cfg, subs)
    except Exception as e:  # 与上面两处同款降级：检查自身异常不炸整个命令
        findings = [("warn", f"检查自身异常：{type(e).__name__}: {str(e)[:120]}")]
    status = ("error" if any(l == "error" for l, _ in findings)
              else "warn" if any(l == "warn" for l, _ in findings) else "ok")
    results.append({"id": "yaml-parse", "name": "YAML 可解析性", "status": status,
                    "findings": [{"level": l, "msg": m} for l, m in findings]})
    # 检查 9 追加在检查 8 之后（同样不进 CHECKS 循环）：它要读账本那一项的结论（判据 b）。
    try:
        findings = check_discipline_trace(project_dir, csm if modules_ok else None, subs,
                                          ledger_findings, verdict=trace)
    except Exception as e:  # 与上面几处同款降级：检查自身异常不炸整个命令
        findings = [("warn", f"检查自身异常：{type(e).__name__}: {str(e)[:120]}")]
    results.append({"id": "discipline-trace", "name": "纪律产物痕迹", "status": _status_of(findings),
                    "findings": [{"level": l, "msg": m} for l, m in findings]})
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="module-close-check",
        description="功能收口确定性检查：stale/路径存在性/基准同值与刷新/仓状态/code-map 覆盖率/"
                    "账本/YAML 可解析性/纪律产物痕迹（warn 档）。"
                    "正确时点 = 模块文档与账本写完之后、外层仓收口提交之前。"
                    "每次运行追加一行计数日志；标记红灯用 workframe-discipline-mark。")
    ap.add_argument("--project", help="目标项目目录；缺省按 _harness.project_dir() 解析"
                                      "（环境变量 → 向上找 .workframe-config.json → 当前目录）")
    args = ap.parse_args(argv)
    project_dir = Path(args.project).resolve() if args.project else _harness_project_dir()
    if not project_dir.is_dir():
        print(f"module-close-check: 目标不是目录: {project_dir}", file=sys.stderr)
        return 2
    # 先加载一次 csm 让它完成模块级 UTF-8 包流（run_all 内的加载幂等）
    _load_csm(project_dir)
    trace = {}
    results = run_all(project_dir, trace=trace)
    # 计数日志只在这里写（run_all 零写入）。本项永不产 error，所以退出码先算后记不改变结论。
    exit_code = 1 if any(r["status"] == "error" for r in results) else 0
    tr = next((r for r in results if r["id"] == "discipline-trace"), None)
    if tr is not None:
        if trace.get("result"):
            try:
                extra = record_trace_run(project_dir, trace, exit_code,
                                         _read_cfg_quiet(project_dir))
            except Exception as e:
                extra = [("warn", f"计数记账自身异常：{type(e).__name__}: {str(e)[:120]}"
                                  f"——判据结果见上，本次未计入计数")]
        else:
            # 检查自身抛异常时 run_all 已降级成一条 warn，但没有判定结果可记——与写入失败同口径说清
            extra = [("warn", "本项没有得出判定（检查自身异常，见上），没写运行行——本次未计入计数")]
        tr["findings"] += [{"level": l, "msg": m} for l, m in extra]
        tr["status"] = _status_of([(f["level"], f["msg"]) for f in tr["findings"]])
    counts = {"ok": 0, "warn": 0, "error": 0}
    print(f"module-close-check — {project_dir}")
    for r in results:
        counts[r["status"]] += 1
        print(f"{LEVEL_MARK[r['status']]} [{r['id']}] {r['name']}")
        for f in r["findings"]:
            print(f"    {LEVEL_MARK[f['level']]} {f['msg']}")
    print(f"结果: ok {counts['ok']} / warn {counts['warn']} / error {counts['error']}"
          + ("　→ 未收干净，不得声明完成" if counts["error"] else ""))
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
