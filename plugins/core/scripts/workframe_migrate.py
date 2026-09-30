#!/usr/bin/env python3
"""已装项目迁移（`workframe-door --migrate` 的实现）：运行态三目录从旧位置搬到中立目录，并收掉同一批
已装项目升级项（项目 skills 真目录挪到中立目录 ＋ 建链接、`.gitignore` 条目、`@AGENTS.md` 导入、
上一版同步进来的纪律镜像目录、CLAUDE.md 里的框架段落旧副本）。

    workframe-door --migrate [--project P]        # 只算：写 plan，打印摘要 ＋ 确认码 ＋ apply 命令
    python "<插件根>/scripts/workframe_door.py" --migrate --apply --confirm <码> --project "<P>"   # 执行

**两阶段，第二阶段由用户本人跑**：plan 阶段零写入（只写 `<P>/logs/workframe-migrate/plan.json`），模型可以跑；
apply 含删除与改写，由用户在普通终端里跑（也允许在**别的项目**的 CC 会话里用 `!` 前缀）。

**apply 的防误跑**（全部在执行第一个动作之前）：
  ① **动作重算比对**：按项目当前状态重新计算动作列表，与 plan 文件里的逐条比较，不同即拒、提示重出 plan。
     参与计算的只有决策输入——各目录新旧存在态、跟踪文件清单、要删 / 要改写的文件的工作树 sha256、工具版本；
     运行态与记忆文件的内容、大小、mtime 不参与（关会话时 SessionEnd 会改写它们，参与了就永远对不上）。
  ② **确认码**：动作列表规范化后的 sha256 前 8 位；`--confirm` 必须等于按①重算出的码。
  ③ **执行环境提示（不拒绝）**：`_harness.not_plain_terminal_reasons()` 非空时打印醒目提示后照常继续。
  ④ **封闭枚举**：每条动作的类型 ∈ 固定集合、路径 ∈ 本迁移的对象清单，越界即拒。
  测试接缝只有 import 级的 `execute_plan(plan, project)`；没有任何放行开关。

**执行顺序**（`compute_plan` 按这个顺序拼动作表，apply 照表顺序执行）：skills 目录搬迁 → 建链接 → `.gitignore` → 运行态三类搬迁（state、memory、
archive）→ rollback-index 改写 → 建 AGENTS.md → 文本改写 → 候选文件 → 删纪律镜像目录。理由：
  - skills 搬迁最容易被编辑器占用而失败，放最前，失败时什么都还没动；
  - `.gitignore` 在 skills 之后（它要写的 skills 忽略行只在链接建成后才对）、运行态搬迁之前——
    运行态搬过去而忽略条目没写上的窗口因此不存在；反过来 `.gitignore` 先写、运行态后来搬失败，多出来的
    新目录忽略行管着一个不存在的目录，无害；
  - 文本改写在全部搬迁之后：改写把路径改成新形态，搬迁中途失败时不留下「文本指新、目录在旧」；
  - 候选文件在删镜像之前：rules local 那一份候选要读镜像里的文件名，删完再续做就算不出来了。

**能力边界（这几句要留）**：
  - **不搬 CLAUDE.md 里的项目内容**（项目目标 / 业务背景 / 项目特殊约束等）：AGENTS.md 不在时建出的是无参渲染的
    占位骨架。「这个项目是什么」只剩占位时 plan 出一条报告项、apply 收尾再说一次（`agents_goal_note`），不代搬。
  - **挡不住 AI 照 plan 打印的命令（含确认码）自己执行 apply**：CC 的 `!` 前缀与 AI 工具调用在本机的控制台判据
    与环境标记上不可分（实测），用户选择允许 `!` 前缀之后③只能提示。剩下的防线是：确认码必须显式传入、plan 之后
    项目一变即拒、越界动作即拒（拒绝文案不回显正确的码），以及纪律文本「模型不得传 `--apply`」（在 plan 的终端输出
    与 plan.json 的 `apply_by` 字段里，不在注入片里）。刻意绕过（直接 import 执行函数、自己 `mv`）同样挡不住。
  - **确认码依赖工具脚本的检出字节**：指纹里有本脚本的 sha256，同一提交在 LF 与 CRLF 两种检出下算出的码不同。
    plan 与 apply 必须用同一份插件检出；换检出、升级或合并过插件之后，盘上的 plan 作废，重出 plan。
  - **会话是否还开着检测不了**：只给弱信号（状态目录 10 分钟内有写入 / 有 Codex 会话标记 / 探到别的进程持锁）。
    只看状态目录顶层文件与已存在的 `*.lock`：会话闲置超过 10 分钟且没持锁时完全看不见。
    探锁只探已存在的锁文件、不建目录，探完即放。
  - **纪律镜像目录的删除不留备份**（用户 2026-09-17 拍板）：其中未提交的改动与未跟踪文件删掉就找不回来；
    已提交的版本可用回执里的逆操作从 git 找回。找回命令按 apply 那一刻逐文件试算的检出形态带 `-c core.autocrlf=…`，
    干净的已提交文件逐字节回到 apply 前；两种取值都对不上的（有未提交改动，或 `.gitattributes` 强制了行尾）只找回
    HEAD 版本，回执里逐文件记着。其中**干净的已提交文件**也算「会丢」：plan 阶段就用同一判定试算（只读），X-rules 摘要的
    计数与逐份标注含它们，apply 的逆操作说明再点名一次；回滚之前改过 git 的 `core.eol` 或属性，试算结果不再成立。
    其余改写与 `.gitignore` 先存原件到回执目录 `preimage/`；目录搬迁不存原件（逆操作是原样搬回）。
  - Windows 上目录里有文件被打开（编辑器 / Obsidian / 资源管理器），或有进程的当前目录停在里面时，搬迁会失败：
    停在已完成的那一步，回执如实记，处理掉占用后重出 plan 只剩未完成的条目（含建链接那一步失败的情形）。
  - **旧记忆目录没有框架出厂形态**（没有 `shared/`，也没有出厂角色的 `MEMORY.md` / `notes.md`；判据是 doctor
    `_legacy_memory_is_framework`，与体检同一份）时不搬、新目录在也不判冲突：那是 Claude Code 子 agent `memory: project`
    的落点，CC 只认原位置。**框架形态与这类子目录混在一起时整个目录照搬**——形态上分不出哪个子目录是 CC 的、哪个是
    项目级角色的，搬完那几个 CC 子目录要手工挪回原位置（plan 的报告项列出非出厂子目录）。
  - 被搬的目录里有**已暂存**的改动时那一类不搬（`git mv` 会带着暂存内容走、断开改名历史），先提交或移出索引。
  - **有类没做完时其余各类照做，退出码 8**（与中途停下同码），收尾块按类分措辞：`UNMIGRATED_ITEMS` 里的类 blocked / 冲突，
    以及中途停下时失败那一步与其后没执行的动作里属于这几类的 ⇒「未迁完：框架只读新位置，处理完之前别开会话」；其余
    blocked、中途停下时没做的其余动作、跳过的改写（要改写的文件有未提交改动或未跟踪，按「先提交」整份跳过；用户
    2026-09-18 拍板算没做完）⇒「补做之前别开会话」。已完成的步骤不列。分类是按条目 id 的固定表，不按项目
    实际状态判：`S-skills` 与 `G-gitignore` 整体归「未迁完」一侧（`G-gitignore` 没暂缓任何类时留的是忽略条目缺口；
    `S-skills` 因新旧 skills 目录并存而挡住时什么都没搬，也按这一侧报），认不出的条目 id 同样归这一侧，方向是多拦。
    工具不判断此刻开会话安不安全，只说别开。
  - 文本改写只换**项目相对的整段**旧运行态路径字面：前面紧挨路径字符（字母数字、`_`、`-`、`.`、`/`、`\\`、`~`、`$`）
    或后面紧挨字母数字、`_`、`-` 时不换——`~/…`、`$HOME/…`、绝对路径、别的项目的路径、URL 里的同段字面都原样留着；
    例外是前面紧挨 `$CLAUDE_PROJECT_DIR/` 一族（恒指本项目根，照换）。代价：`./` 开头的写法与 `<项目根>/`、`{project}/`
    这类占位写法（模板里指任意项目，不恒指本项目）不换；反斜杠形态与裸末段形态不换。要改写的文件按改写后的内容再扫一遍，
    没换掉的旧字面在 plan 里单列（只列）。只改 CLAUDE.md、AGENTS.md 与项目 skills 下的 md（用户 2026-09-17 拍板），
    其余文件只列。
  - 「要改写的文件先提交」按 git 判：项目嵌在上层仓的子目录里也成立（路径按 `git rev-parse --show-prefix` 归一）；
    索引里的纯改名（R100、工作树与索引相同）不算脏。
  - **项目 skills 链接的忽略行写无尾斜杠的 `.claude/skills`**：带尾斜杠的旧写法（`.claude/skills/` 等）匹配不到 POSIX
    的符号链接，只在 Windows junction 上生效，所以它不算「已有这一条」（判定在 `project_scaffold.skills_link_ignore_state`）：
    managed marker 之间是旧写法 ⇒ 重写 marker 之间；marker 之外的旧写法不动、marker 之间照常补新写法。提交建议里
    「别提交这一行链接」按 `check-ignore` 判、不按平台判——忽略行在位时不出现，只在 marker 外另有否定规则之类
    盖不住链接的情形下出现。
"""

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402
import _state_io as sio  # noqa: E402
import project_scaffold as ps  # noqa: E402
import workframe_doctor as doc  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CODE_PATHS_SOURCE = Path(__file__).resolve().parent / "check-stale-modules.py"    # `_code_paths_parser` 的取材
KINDS = ("state", "memory", "archive")
PLAN_SCHEMA = "workframe.migrate-plan.v1"
RECEIPT_SCHEMA = "workframe.migrate-receipt.v1"
OUT_PARTS = ("logs", "workframe-migrate")
PLAN_NAME = "plan.json"
ITEM_BY_KIND = {"state": "R-state", "memory": "R-memory", "archive": "R-archive"}
ITEM_RANK = {name: i for i, name in enumerate((
    "S-skills", "G-gitignore", "R-state", "R-memory", "R-archive", "R-conflict", "R-index", "I-agents-import",
    "L-literals", "W-w3-residue", "W-rules-local", "X-rules"))}
BOM = chr(0xFEFF)
UTF8_BOM = b"\xef\xbb\xbf"
AGENTS_IMPORT_LINE = "@AGENTS.md"

# ④ 封闭枚举：动作类型的全集
OPS = ("move_dir", "make_skills_link", "write_gitignore", "rewrite_file", "create_agents_md",
       "write_candidate", "remove_rules_mirror")

# 文本改写里恒指本项目根的前缀：旧运行态字面前面紧挨它们时照换（见 `rewrite_literals`）
PROJECT_ROOT_ANCHORS = ("$CLAUDE_PROJECT_DIR/", "${CLAUDE_PROJECT_DIR}/", '"$CLAUDE_PROJECT_DIR"/', '"${CLAUDE_PROJECT_DIR}"/')

SESSION_RECENT_SEC = 600

EXIT_REFUSED = 7        # 执行第一个动作之前拒绝：零写入
EXIT_PARTIAL = 8        # 没做完：执行中途停下、有类 blocked / conflict 没迁、或有改写被跳过（见输出末尾的块与回执）
UNFINISHED_STATUSES = ("blocked", "conflict")
# 没做完时那一类还留在旧位置（或新布局的忽略条目缺着）的条目：收尾块对它们说「框架只读新位置」，其余只说「补做前别开会话」
UNMIGRATED_ITEMS = ("R-state", "R-memory", "R-archive", "R-conflict", "S-skills", "G-gitignore")
# AGENTS.md「这个项目是什么」只剩占位的提示条目（只报不改，见 `agents_goal_note`）
AGENTS_GOAL_ITEM = "A-agents-goal"
# apply 开头重印的摘要：跳过的改写也要印——它和收尾块是用户在 apply 输出里仅有的两处能看见它的地方
APPLY_SUMMARY_STATUSES = ("planned", "blocked", "conflict", "skipped")


class MigrateRefused(Exception):
    """执行第一个动作之前的拒绝：零写入。"""


# ---------------------------------------------------------------- 小工具

def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _out_dir(project):
    return Path(project).joinpath(*OUT_PARTS)


def _mirror_rel():
    return "/".join(doc.RULES_MIRROR_PARTS)


def _local_rules_rel():
    return "/".join(doc.RULES_MIRROR_PARTS[:2] + ("local",))


def _split_lines(text):
    """按 `\\n` 切行、保留行尾（CRLF 行的 `\\r` 留在行尾）；不按裸 `\\r` 之类切。"""
    lines = re.split(r"(?<=\n)", text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _eol_of(raw):
    crlf = raw.count(b"\r\n")
    return "\r\n" if crlf and crlf * 2 >= raw.count(b"\n") else "\n"


def _walk_files(root):
    """`root` 下全部文件（不穿过目录链接），返回相对 `root` 的 POSIX 路径，排序。"""
    root = Path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not _harness.is_dir_link(Path(dirpath) / d))
        for f in filenames:
            out.append((Path(dirpath) / f).relative_to(root).as_posix())
    return sorted(out)


def _tool_version():
    try:
        version = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json")
                             .read_text(encoding="utf-8")).get("version") or "?"
    except Exception:
        version = "?"
    return {"plugin_version": version, "script_sha256": _sha(Path(__file__).read_bytes())[:16]}


# ---------------------------------------------------------------- git（读一律不抢可选锁）

def _git(project, *args, literal=True, raw=False):
    """返回 (rc, stdout, stderr)；`raw=True` 时 stdout 是原始字节（要按字节比对内容时用）。"""
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"        # `git status` 默认会刷新 index；plan 阶段不许写
    if literal:
        # 路径里的 `*?[` 不当通配。`check-ignore` 收的是路径名不是 pathspec，带着这个变量会以 128 拒绝执行
        env["GIT_LITERAL_PATHSPECS"] = "1"
    r = subprocess.run(["git", "-C", str(project), "-c", "core.longpaths=true", "-c", "core.quotepath=false",
                        *args], capture_output=True, stdin=subprocess.DEVNULL, env=env)
    out = r.stdout if raw else r.stdout.decode("utf-8", "replace")
    return r.returncode, out, r.stderr.decode("utf-8", "replace").strip()


def _z(out):
    return [x for x in out.split("\0") if x]


def is_git(project):
    try:
        rc, out, _ = _git(project, "rev-parse", "--is-inside-work-tree")
    except OSError:
        return False
    return rc == 0 and out.strip() == "true"


def _tracked(project, *rels):
    found = []
    for i in range(0, len(rels), 100):
        rc, out, _ = _git(project, "ls-files", "-z", "--", *rels[i:i + 100])
        if rc == 0:
            found += _z(out)
    return sorted(found)


def _deleted(project, rel):
    rc, out, _ = _git(project, "ls-files", "-z", "--deleted", "--", rel)
    return sorted(_z(out)) if rc == 0 else []


def _show_prefix(project):
    """项目根相对 git 仓根的前缀（`apps/proj/` 形态；项目根就是仓根时为空串）。"""
    rc, out, _ = _git(project, "rev-parse", "--show-prefix")
    return out.strip() if rc == 0 else ""


def _status_map(project, rels):
    """`git status --porcelain=v1 -z` 里各路径的 XY（键是**项目根相对**路径）；干净的路径不在结果里。

    porcelain 的路径恒相对**仓根**、不受 `status.relativePaths` 影响——项目嵌在上层仓的子目录里时，
    不剥掉 `--show-prefix` 给的前缀，按项目相对路径去查就永远查不到，脏文件全被当成干净的。"""
    out_map = {}
    rels = list(rels)
    prefix = _show_prefix(project) if rels else ""
    for i in range(0, len(rels), 100):
        _rc, out, _ = _git(project, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--",
                           *rels[i:i + 100])
        parts = out.split("\0")
        j = 0
        while j < len(parts):
            ent = parts[j]
            j += 1
            if len(ent) < 4:
                continue
            xy, path = ent[:2], ent[3:]
            if prefix and path.startswith(prefix):
                path = path[len(prefix):]
            out_map[path] = xy
            if xy[0] in "RC":
                j += 1                     # 改名 / 复制条目后面跟着原路径
    return out_map


def _staged_paths(project):
    """索引里相对 HEAD 有改动的路径（项目根相对，只含项目子树；改名拆成删除 ＋ 新增两条）。"""
    rc, out, _ = _git(project, "diff", "--cached", "--name-only", "--no-renames", "--relative", "-z")
    return sorted(_z(out)) if rc == 0 else []


def _content_dirty(project, rels):
    """`rels` 里有未提交**内容**改动的路径 → XY。与 `_status_map` 的差别只有一条：索引里的**纯改名**
    （`R100`、工作树与索引相同）不算脏——上一次 apply 停在半路时，已经 `git mv` 过去的文件就是这个形态，
    按 `git status` 字面判会把它们当成「有未提交改动」而跳过改写，续做永远收不拢。
    带路径的 `git status` 只看得见改名的新路径那一半，报成 `A `，所以 `A ` 与 `R ` 都要拿整份索引的
    改名检测结果核一遍。"""
    status = _status_map(project, rels)
    renames = [p for p, xy in status.items() if xy in ("R ", "A ")]
    if renames:
        _rc, out, _ = _git(project, "diff", "--cached", "-M", "--name-status", "--relative", "-z")
        parts, j, pure = out.split("\0"), 0, set()
        while j < len(parts):
            code = parts[j]
            if code.startswith(("R", "C")) and j + 2 < len(parts):
                if code == "R100":
                    pure.add(parts[j + 2])
                j += 3
            else:
                j += 2
        for p in renames:
            if p in pure:
                status.pop(p)
    return status


def _index_lock(project):
    rc, out, _ = _git(project, "rev-parse", "--git-path", "index.lock")
    if rc != 0 or not out.strip():
        return None
    p = Path(out.strip())
    return p if p.is_absolute() else Path(project) / p


# ---------------------------------------------------------------- 纯变换（plan 与 apply 共用，保证算出同一个结果）

def gitignore_bytes(raw, state_rel, skills_link):
    """按给定布局算 `.gitignore` 的新字节。返回 (新字节或 None, 模式, 缺的条目, 不能改的原因)。

    有 managed marker ⇒ 重写 marker 之间；没有 ⇒ 文件尾追加 managed block。**保留原文件的行尾与 BOM**
    （scaffold 的 `_ensure_gitignore` 按通用换行读写，会把 CRLF 文件整份改成 LF，这里不复用它的写法）。
    条目清单与写入行只问 scaffold 的两个纯函数，不另抄一份。"""
    raw = raw or b""
    bom = raw.startswith(UTF8_BOM)
    try:
        text = raw[len(UTF8_BOM):].decode("utf-8") if bom else raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, None, [], "不是 UTF-8"
    missing = [e for e in ps.gitignore_required_entries_for(state_rel, skills_link) if not ps.gitignore_covers(text, e)]
    if not missing:
        return None, None, [], ""
    eol = _eol_of(raw) if raw else "\n"
    managed = ps.managed_lines_for(state_rel, skills_link)
    lines = _split_lines(text)
    begins = (ps.GITIGNORE_MANAGED_BEGIN,) + ps.GITIGNORE_MANAGED_BEGIN_LEGACY
    b_idx = next((i for i, ln in enumerate(lines) if ps._strip_line(ln) in begins), None)
    if b_idx is not None:
        e_idx = next((i for i in range(b_idx + 1, len(lines))
                      if ps._strip_line(lines[i]) == ps.GITIGNORE_MANAGED_END), None)
        if e_idx is None:
            return None, None, missing, "有 managed 起始 marker 却没有结束 marker"
        new_text, mode = "".join(lines[:b_idx + 1] + [ln + eol for ln in managed] + lines[e_idx:]), "rewrite-block"
    else:
        block = eol.join(["", ps.GITIGNORE_MANAGED_BEGIN, *managed, ps.GITIGNORE_MANAGED_END, ""])
        if text:
            new_text = (text if text.endswith("\n") else text + eol) + block
        else:
            new_text = block[len(eol):]
        mode = "append-block"
    return (UTF8_BOM if bom else b"") + new_text.encode("utf-8"), mode, missing, ""


def json_prefix_rewrite(raw, kinds):
    """rollback-index.json 里以旧前缀开头的**字符串值**换新前缀。返回 (新字节, 替换处数)；
    没有要换的返回 (None, 0)；不能换返回 (None, 原因)。

    按文本替换（保住原文件的缩进与转义形态），再把新旧两份各自解析、逐个值比对：除了这几处前缀之外
    结构与值必须完全相同，否则不改。"""
    pairs = [(sio.legacy_rel(k), sio.new_rel(k)) for k in kinds]
    try:
        text = raw.decode("utf-8-sig")
        old_obj = json.loads(text)
    except Exception as e:
        return None, f"解析失败（{type(e).__name__}）"
    new_text, n = text, 0
    for old, new in pairs:
        needle = '"' + old + "/"
        n += new_text.count(needle)
        new_text = new_text.replace(needle, '"' + new + "/")
    if not n:
        return None, 0
    try:
        new_obj = json.loads(new_text)
    except Exception:
        return None, "替换后解析失败"

    def same(a, b):
        if isinstance(a, dict) and isinstance(b, dict):
            return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
        if isinstance(a, list) and isinstance(b, list):
            return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
        if isinstance(a, str) and isinstance(b, str):
            return a == b or any(a.startswith(o + "/") and b == nw + a[len(o):] for o, nw in pairs)
        return a == b

    if not same(old_obj, new_obj):
        return None, "替换碰到了路径值之外的内容"
    return (UTF8_BOM if raw.startswith(UTF8_BOM) else b"") + new_text.encode("utf-8"), n


def rewrite_literals(text, kinds):
    """把 `kinds` 这几类的旧运行态路径（整段字面）换成新形态；返回 (新文本, [(行号, 行摘录)])。

    **只换项目相对的写法**：字面前面紧挨着路径字符（字母数字、`_`、`-`、`.`、`/`、`\\`、`~`、`$`）时不换——
    `~/` 开头的是用户主目录下的同名目录（记忆那一类在那里是 Claude Code 官方 user 作用域的记忆目录），
    `$HOME/…`、`../别的项目/…`、绝对路径、URL 里的同段字面都不是本项目的运行态目录。
    **例外**：前面紧挨 `PROJECT_ROOT_ANCHORS` 里的写法时照换——`$CLAUDE_PROJECT_DIR` 由 Claude Code 设为会话的项目根，
    写在本项目自己的文件里恒指本项目根。`<项目根>/`、`{project}/` 这类占位写法不换：模板与说明文字里它们指任意项目，
    不恒指本项目。代价：`./` 开头的写法与占位写法都不换，改写之后仍含旧字面的行由 plan 的报告项单列。
    后面紧挨字母数字、`_`、`-` 时同样不换（`…/agent-memory-local/` 这类别的目录）。"""
    olds = [sio.legacy_rel(k) for k in kinds]
    if not olds:
        return text, []
    lead = "|".join([r"(?<![\w./\\~$-])"] + [f"(?<={re.escape(a)})" for a in PROJECT_ROOT_ANCHORS])
    pat = re.compile(r"(?:" + lead + r")(" + "|".join(re.escape(o) for o in olds) + r")(?![\w-])")
    to_new = {sio.legacy_rel(k): sio.new_rel(k) for k in kinds}
    hits, out = [], []
    for i, ln in enumerate(_split_lines(text), 1):
        if pat.search(ln):
            hits.append((i, ln.strip()[:100]))
            ln = pat.sub(lambda m: to_new[m.group(1)], ln)
        out.append(ln)
    return "".join(out), hits


def residual_literals(text):
    """仍含任一类旧运行态路径字面（子串）的行：[(行号, 行摘录)]。口径与 L-report 的 `git grep -F` 相同。"""
    olds = [sio.legacy_rel(k) for k in KINDS]
    return [(i, ln.strip()[:100]) for i, ln in enumerate(_split_lines(text), 1) if any(o in ln for o in olds)]


def append_agents_import(text, eol):
    return text + ("" if not text or text.endswith("\n") else eol) + AGENTS_IMPORT_LINE + eol


def apply_text_transforms(raw, transforms, kinds):
    """rewrite_file 的变换，plan 与 apply 都经这里。"""
    if transforms == ["json-prefix"]:
        new, _n = json_prefix_rewrite(raw, kinds)
        return new
    text = raw.decode("utf-8")
    if "literals" in transforms:
        text, _hits = rewrite_literals(text, kinds)
    if "agents-import" in transforms:
        text = append_agents_import(text, _eol_of(raw))
    return text.encode("utf-8")


# v1.0.0 模板里紧挨在项目特殊约束（`{{PROJECT_SPECIFIC_CONSTRAINTS}}`）前面的那一句框架正文的开头——「工种知识的
# 落点判据」小节的最后一行。v1.0.0 是唯一一个把这几段写进 CLAUDE.md 的已发布版本，已冻结，这一句不会再变
W3_TAIL_ANCHORS = ("主 Claude 恒为潜在消费者",)


def w3_candidate(text):
    """去掉框架段落旧副本后的 CLAUDE.md 候选。按标题切块（围栏内的 `#` 不算标题），块内命中 doctor
    `_w3_residue_blocks` 的锚点就整块去掉。返回 (去掉的行区间 [(起, 止)]（1 起、闭区间）, 各区间块名, 候选里仍认得出的块名)。

    **粒度是「相邻两个标题之间」**：命中的是二级标题下的一段时，它下面的三级小节不随之去掉（可能是项目自有内容）；
    锚点本来就宽（`document-norms` 在正文任何位置出现都算），所以候选可能去多——它是给你对比的候选，不直接生效。
    跨标题的 HTML 注释按块内判，与 doctor 整篇判可能不同；「仍认得出」那一格按整篇重判。

    **标题之外的切点（`W3_TAIL_ANCHORS`）**：v1.0.0 模板把项目特殊约束渲染在「工种知识的落点判据」小节正文之后、
    中间没有标题，只按标题切会让项目约束随那一块一起去掉——用户照候选采纳后，项目约束两扇门都读不到。所以在那段
    框架正文的最后一句（v1.0.0 起未变过的原句）之后再切一刀，其后的行另成一块、各自判。**能力边界**：那一句被改写
    或删掉时切不开，退回按标题整块判（块里夹着的项目内容照样随块去掉——plan 的条目文案提醒对比时手工保留）。"""
    lines = _split_lines(text)
    bare = [ln.rstrip("\r\n") for ln in lines]
    if bare:
        bare[0] = bare[0].lstrip(BOM)
    outside = list(ps._outside_fence(bare))
    heads = [i for i, ln in outside if re.match(r"#{1,6}\s", ln)]
    tails = [i + 1 for i, ln in outside if ln.strip().startswith(W3_TAIL_ANCHORS)]
    starts = sorted({0, *heads, *tails})
    drops, names = [], []
    for s, e in zip(starts, starts[1:] + [len(lines)]):
        if s >= e:
            continue
        hit = doc._w3_residue_blocks("".join(lines[s:e]))
        if hit:
            drops.append([s + 1, e])
            names.append(hit)
    remaining = doc._w3_residue_blocks(drop_line_ranges(text, drops))
    return drops, names, remaining


def lines_naming(text, names):
    """提到 `names` 里任一文件名的行号（1 起）。"""
    pat = re.compile(r"(?<![\w.-])(" + "|".join(re.escape(n) for n in names) + r")(?![\w-])")
    return [i for i, ln in enumerate(_split_lines(text), 1) if pat.search(ln)]


def drop_line_ranges(text, ranges):
    drop = set()
    for s, e in ranges:
        drop.update(range(s, e + 1))
    return "".join(ln for i, ln in enumerate(_split_lines(text), 1) if i not in drop)


# ---------------------------------------------------------------- 条目计算（纯读）

def _item(item_id, status, title, lines=None, data=None):
    return {"id": item_id, "status": status, "title": title, "lines": list(lines or []), "data": data or {}}


def _located_rel(project, kind):
    """这一类运行态目录此刻**实际在哪**：新目录在 → 新；否则旧目录在 → 旧；都没有 → 新。

    **不用 `sio.runtime_rel` / `*_dir_of`**：那一族恒指新位置（运行期不读旧目录），而迁移工具要看的
    正是还没搬的旧目录——plan 里的 rollback-index、搬不动时 `.gitignore` 该管哪个状态目录、只列不改的报告项、
    apply 前的会话信号，读错一侧都是静默的（条目消失 / 少报 / 不提醒）。全仓合法需要看旧目录的运行期消费方
    只有本工具与 doctor。"""
    new, old = project / sio.new_rel(kind), project / sio.legacy_rel(kind)
    if new.exists() or not old.exists():
        return sio.new_rel(kind)
    return sio.legacy_rel(kind)


def _located_dir(project, kind):
    """`_located_rel` 的绝对路径形态。"""
    return Path(project) / _located_rel(project, kind)


def _session_counter(state_dir):
    try:
        return json.loads((Path(state_dir) / sio.ACTIVITY_FILE_NAME).read_text(encoding="utf-8-sig")).get("session_counter")
    except Exception:
        return None


def _staged_inside(staged, src_rel):
    return [p for p in staged if p.startswith(src_rel + "/")]


def _move(project, git, item_id, src_rel, dst_rel, staged=()):
    """一次目录搬迁的条目与动作；返回 (item, action 或 None)。`staged` 是 `_staged_paths()` 的结果。"""
    src = project / src_rel
    if _harness.is_dir_link(src) or not src.is_dir():
        return _item(item_id, "blocked", f"{src_rel} 不是真目录，不搬"), None
    inside = _staged_inside(staged, src_rel)
    if inside:
        # git mv 会带着暂存内容一起搬，小文件在索引里断成删除 ＋ 新增、`git log --follow` 断开；
        # 搬完再照「移出索引」去做，新路径会变成未跟踪、旧路径的删除留在暂存里
        return _item(item_id, "blocked", f"{src_rel} 里有 {len(inside)} 个**已暂存**的改动——搬过去会断开改名历史；"
                                         f"先提交它们，或移出索引（git restore --staged -- <路径>），再重出 plan",
                     inside[:10]), None
    tracked = _tracked(project, src_rel) if git else []
    files = _walk_files(src)
    if git and tracked:
        deleted = _deleted(project, src_rel)
        if deleted:
            return _item(item_id, "blocked", f"{src_rel} 里有 {len(deleted)} 个已跟踪文件在工作树里被删了、没提交——"
                                             f"git mv 会拒绝；先 git restore 或提交这些删除再重出 plan",
                         deleted[:10]), None
        method = "git-mv"
    else:
        method = "rename"
    action = {"op": "move_dir", "item": item_id, "src": src_rel, "dst": dst_rel, "method": method, "tracked": tracked}
    how = (f"git mv（跟踪 {len(tracked)} 个）" if method == "git-mv"
           else ("os.rename（零跟踪文件）" if git else "os.rename（非 git 仓）"))
    return _item(item_id, "planned", f"搬 {src_rel} → {dst_rel}：{how}，目录内共 {len(files)} 个文件（未提交的改动随目录带走）",
                 data={"files": len(files), "tracked": len(tracked)}), action


def compute_plan(project):
    """纯读：返回 `{"project", "git", "items", "payload": {"tool", "actions"}}`。**不写任何文件、不写 index。**"""
    project = Path(project).resolve()
    git = is_git(project)
    items, moves = [], {}
    staged = _staged_paths(project) if git else []

    # ---- R：运行态三类 ----
    for kind in KINDS:
        new, old = sio.new_rel(kind), sio.legacy_rel(kind)
        new_p, old_p = project / new, project / old
        if kind == "memory" and os.path.lexists(old_p):
            # 旧记忆目录同时是 Claude Code 子 agent `memory: project` 的落点，CC 只认原位置、搬走即读不到。
            # 判据与 doctor `legacy_state_dir` 同一份（`_legacy_memory_is_framework`）：没有框架出厂形态就不搬、也不判冲突
            is_fw, info = doc._legacy_memory_is_framework(old_p)
            if not is_fw:
                items.append(_item(ITEM_BY_KIND[kind], "report",
                                   f"不搬 {old}：里面没有框架出厂形态（`shared/` 或出厂角色的 MEMORY.md / notes.md），"
                                   "按 Claude Code 原生子 agent 记忆处理——CC 只认原位置，留在原地",
                                   [info] if info else []))
                continue
        if os.path.lexists(old_p) and os.path.lexists(new_p):
            data = {"kind": kind, "old_files": _walk_files(old_p) if old_p.is_dir() else [],
                    "new_files": _walk_files(new_p) if new_p.is_dir() else []}
            lines = [f"{old}：{len(data['old_files'])} 个文件",
                     f"{new}：{len(data['new_files'])} 个文件" + ("（空目录）" if not data["new_files"] else "")]
            if kind == "state":
                data["old_session_counter"] = _session_counter(old_p)
                data["new_session_counter"] = _session_counter(new_p)
                lines.append(f"session_counter：旧 {data['old_session_counter']} / 新 {data['new_session_counter']}")
            items.append(_item("R-conflict", "conflict", f"冲突（需人工判断）：{old} 与 {new} 同时存在，这一类不动",
                               lines, data))
        elif os.path.lexists(old_p):
            item, action = _move(project, git, ITEM_BY_KIND[kind], old, new, staged)
            items.append(item)
            if action:
                moves[kind] = action

    # ---- S：项目 skills 真目录 → 中立目录 ＋ 链接 ----
    cc, neutral = project / _harness.SKILLS_DIR_CC, project / _harness.SKILLS_DIR_NEUTRAL
    cc_rel, neu_rel = _harness.SKILLS_DIR_CC.as_posix(), _harness.SKILLS_DIR_NEUTRAL.as_posix()
    s_actions, s_item = [], None
    link_action = {"op": "make_skills_link", "item": "S-skills", "link": cc_rel, "target": neu_rel}
    if cc.is_dir() and not _harness.is_dir_link(cc):
        if os.path.lexists(neutral):
            s_item = _item("S-skills", "blocked", f"{cc_rel} 是真目录、{neu_rel} 也已存在——不搬，先人工合并成一处")
        else:
            s_item, act = _move(project, git, "S-skills", cc_rel, neu_rel, staged)
            if act:
                s_item["title"] += f"；随后建 {cc_rel} → {neu_rel} 目录链接，.gitignore 忽略 {cc_rel}"
                s_actions = [act, link_action]
        items.append(s_item)
    elif neutral.is_dir() and not _harness.is_dir_link(neutral) and not os.path.lexists(cc):
        # 上一次 apply 搬完 skills、建链接那一步失败时停在这个形态；clone 出来的项目也是它。
        # 只出建链接一条，`.gitignore` 按「将有链接」求值——否则续做回执报 ok 而 Claude Code 看不到项目 skill，
        # 之后会话启动补建链接时 `.claude/skills/**` 又以未跟踪文件冒出来
        s_actions = [link_action]
        s_item = _item("S-skills", "planned", f"建 {cc_rel} → {neu_rel} 目录链接（{neu_rel} 在、链接位置空着），"
                                              f".gitignore 忽略 {cc_rel}")
        items.append(s_item)

    # ---- G：搬完之后的布局要的 .gitignore 条目 ----
    def post_layout():
        state_rel = sio.new_rel("state") if "state" in moves else _located_rel(project, "state")
        return state_rel, bool(s_actions) or _harness.skills_link_present(project)

    gi = project / ".gitignore"
    gi_raw = gi.read_bytes() if gi.is_file() else b""
    state_rel, link = post_layout()
    new_gi, mode, missing, why = gitignore_bytes(gi_raw, state_rel, link)
    g_action = None
    if new_gi is not None or why:
        if not why and git and gi.is_file() and _status_map(project, [".gitignore"]):
            why = "有未提交的改动（要改写的文件先提交）"
        if why:
            held = []
            if "state" in moves:
                moves.pop("state")
                held.append(ITEM_BY_KIND["state"])
            if s_actions:
                s_actions = []
                held.append("S-skills")
            for it in items:
                if it["id"] in held:
                    it["status"], it["title"] = "blocked", it["title"] + "——暂缓：它要的 .gitignore 条目写不进去"
            items.append(_item("G-gitignore", "blocked", f".gitignore {why}，不改；缺 {missing}"
                               + (f"；因此暂缓 {'、'.join(held)}" if held else "")))
        else:
            g_action = {"op": "write_gitignore", "item": "G-gitignore", "path": ".gitignore", "mode": mode,
                        "state_rel": state_rel, "skills_link": link,
                        "pre_sha256": _sha(gi_raw) if gi.is_file() else None, "post_sha256": _sha(new_gi)}
            where = "重写 managed marker 之间" if mode == "rewrite-block" else "文件尾追加 managed block"
            eol_name = "CRLF" if _eol_of(new_gi) == "\r\n" else "LF"      # f-string 表达式里不放反斜杠：CI 跑 3.11
            items.append(_item("G-gitignore", "planned",
                               f".gitignore {where}（行尾 {eol_name}，"
                               f"BOM {'保留' if new_gi.startswith(UTF8_BOM) else '无'}）：补 {missing}"))

    actions = list(s_actions) + ([g_action] if g_action else []) + [moves[k] for k in KINDS if k in moves]

    # 搬完之后「只剩新目录」的类，才改写指向它的旧路径
    post_kinds = [k for k in KINDS if k in moves
                  or (os.path.lexists(project / sio.new_rel(k)) and not os.path.lexists(project / sio.legacy_rel(k)))]

    # ---- R-index：rollback-index.json 里的旧前缀路径 ----
    cur_state_rel = _located_rel(project, "state")
    ri = project / cur_state_rel / "rollback-index.json"
    if ri.is_file() and post_kinds:
        raw = ri.read_bytes()
        new_raw, n = json_prefix_rewrite(raw, post_kinds)
        exec_rel = (sio.new_rel("state") if "state" in moves else cur_state_rel) + "/rollback-index.json"
        if new_raw is not None:
            actions.append({"op": "rewrite_file", "item": "R-index", "path": exec_rel,
                            "plan_path": f"{cur_state_rel}/rollback-index.json", "kinds": post_kinds,
                            "transforms": ["json-prefix"], "pre_sha256": _sha(raw), "post_sha256": _sha(new_raw)})
            items.append(_item("R-index", "planned", f"改写 {exec_rel} 里 {n} 处旧前缀路径（先存原件）"))
        elif isinstance(n, str):
            items.append(_item("R-index", "blocked", f"rollback-index.json {n}，不改"))

    # ---- I-agents-import ＋ L-literals：按文件合并成一次改写 ----
    cm, am = project / "CLAUDE.md", project / "AGENTS.md"
    import_needed = False
    if cm.is_file():
        try:
            same_file = am.exists() and os.path.samefile(cm, am)
        except OSError:
            same_file = False
        import_needed = not same_file and not doc.claude_md_imports_agents_md(
            cm.read_bytes().decode("utf-8", "replace"))

    targets = [(rel, rel) for rel in ("CLAUDE.md", "AGENTS.md") if (project / rel).is_file()]
    skills_now = project / _harness.project_skills_dir(project)
    if skills_now.is_dir():
        now_rel = _harness.project_skills_dir(project).as_posix()
        post_rel = neu_rel if s_actions else now_rel
        targets += [(f"{now_rel}/{f}", f"{post_rel}/{f}") for f in _walk_files(skills_now) if f.endswith(".md")]
    tracked_set = set(_tracked(project, *[t[0] for t in targets])) if git and targets else set()
    status = _content_dirty(project, [t[0] for t in targets]) if git and targets else {}

    rewrites, lit_lines, lit_skips, import_item = [], [], [], None
    for plan_rel, exec_rel in targets:
        raw = (project / plan_rel).read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        _new, hits = rewrite_literals(text, post_kinds)
        want_import = plan_rel == "CLAUDE.md" and import_needed
        if not hits and not want_import:
            continue
        if git and (plan_rel not in tracked_set or plan_rel in status):
            why = "未跟踪" if plan_rel not in tracked_set else f"有未提交改动（{status[plan_rel]}）"
            # 跳过的文件记进条目的 data["skipped"]：apply 收尾按它列出来、判为没做完（退出码 8）
            if hits:
                lit_lines.append(f"跳过 {plan_rel}（{len(hits)} 行）：{why}——先提交它再重出 plan")
                lit_skips.append({"path": plan_rel, "post_path": exec_rel, "why": why,
                                  "what": f"旧运行态路径改写（{len(hits)} 行）"})
            if want_import:
                import_item = _item("I-agents-import", "skipped", f"CLAUDE.md {why}——`@AGENTS.md` 导入这次不加，先提交它再重出 plan",
                                    data={"skipped": [{"path": plan_rel, "why": why, "what": "末尾加 `@AGENTS.md` 导入"}]})
            continue
        transforms = ["literals"] if hits else []
        if want_import:
            probe = append_agents_import(_new, _eol_of(raw))
            if doc.claude_md_imports_agents_md(probe):
                transforms.append("agents-import")
            else:
                import_item = _item("I-agents-import", "blocked", "CLAUDE.md 末尾加 `@AGENTS.md` 之后 doctor 仍判「未导入」"
                                                                  "（前面有没闭合的 `<!--` 或 `~~~` 围栏之类）——不加，请手动顶格加一行")
        if not transforms:
            continue
        rewrites.append({"op": "rewrite_file", "item": "L-literals" if hits else "I-agents-import", "path": exec_rel,
                         "plan_path": plan_rel, "kinds": post_kinds if hits else [], "transforms": transforms,
                         "pre_sha256": _sha(raw), "post_sha256": _sha(apply_text_transforms(raw, transforms, post_kinds)),
                         "_hits": hits})

    cm_rewrite = next((r for r in rewrites if r["plan_path"] == "CLAUDE.md"), None)
    if cm_rewrite and "agents-import" in cm_rewrite["transforms"]:
        if am.exists():
            import_item = _item("I-agents-import", "planned", "CLAUDE.md 末尾加一行 `@AGENTS.md`（产物已用 doctor 的导入判定式读过）")
        else:
            try:
                tpl = (ps.SCAFFOLD_TEMPLATES_DIR / "agents-md-template.md").read_text(encoding="utf-8")
                rendered = ps.render_placeholders(tpl, ps.agents_md_mapping(project), "agents-md-template.md")
                actions.append({"op": "create_agents_md", "item": "I-agents-import", "path": "AGENTS.md",
                                "post_sha256": _sha(rendered.encode("utf-8"))})
                import_item = _item("I-agents-import", "planned",
                                    "AGENTS.md 不存在：先按模板建出（纯新增，与会话启动时补建同一个写入点）；"
                                    "再在 CLAUDE.md 末尾加一行 `@AGENTS.md`")
            except Exception as e:
                import_item = _item("I-agents-import", "blocked",
                                    f"AGENTS.md 渲染不出来（{type(e).__name__}: {e}）——导入这次不加")
                cm_rewrite["transforms"].remove("agents-import")
                if not cm_rewrite["transforms"]:
                    rewrites.remove(cm_rewrite)
                else:
                    raw = cm.read_bytes()
                    cm_rewrite["post_sha256"] = _sha(apply_text_transforms(raw, cm_rewrite["transforms"], post_kinds))
    if import_item:
        items.append(import_item)
    # 进了改写集的文件不再走 L-report 的 grep：按改写之后的内容再扫一遍，没换掉的旧字面（非项目相对写法、
    # 这次没搬的类）在这里列出来，否则 apply 前完全看不见、apply 之后重出 plan 才冒出来
    residue = []
    for r in rewrites:
        post = apply_text_transforms((project / r["plan_path"]).read_bytes(), r["transforms"], r["kinds"])
        residue += [f"{r['plan_path']} L{i}: {ex}" for i, ex in residual_literals(post.decode("utf-8"))]
    done_lines = []
    for r in rewrites:
        hits = r.pop("_hits")
        if hits:
            done_lines.append(f"{r['path']}：{len(hits)} 行（行 {', '.join(str(h[0]) for h in hits[:12])}"
                              + ("…" if len(hits) > 12 else "") + "）")
        actions.append(r)
    lit_lines = done_lines + lit_lines
    if lit_lines:
        n_files = sum(1 for r in rewrites if "literals" in r["transforms"])
        items.append(_item("L-literals", "planned" if n_files else "skipped",
                           f"改写 {n_files} 个文件里指向旧运行态目录的路径（先存原件）", lit_lines,
                           {"skipped": lit_skips} if lit_skips else None))

    # ---- W：候选文件（CLAUDE.md 旧副本 / rules local 里提到旧纪律文件名的行）----
    if cm.is_file():
        cm_bytes = cm.read_bytes()
        if cm_rewrite in rewrites:
            cm_bytes = apply_text_transforms(cm_bytes, cm_rewrite["transforms"], post_kinds)
        try:
            cm_text = cm_bytes.decode("utf-8")
        except UnicodeDecodeError:
            cm_text = ""
        blocks = doc._w3_residue_blocks(cm_text)
        if blocks:
            drops, names, remaining = w3_candidate(cm_text)
            cand = drop_line_ranges(cm_text, drops)
            actions.append({"op": "write_candidate", "item": "W-w3-residue", "source": "CLAUDE.md", "name": "CLAUDE.md",
                            "source_sha256": _sha(cm_bytes), "drop_lines": drops,
                            "post_sha256": _sha(cand.encode("utf-8"))})
            items.append(_item("W-w3-residue", "planned",
                               f"CLAUDE.md 还留着框架段落旧副本（{'、'.join(blocks)}）——生成去掉它们的候选文件；"
                               "CLAUDE.md 本体不动，你对比后自己替换（按块整块去掉：块里若夹着你自己的内容，对比时手工保留）",
                               [f"去掉第 {s}–{e} 行（{'、'.join(n)}）" for (s, e), n in zip(drops, names)]
                               + ([f"候选里仍认得出：{'、'.join(remaining)}（锚点不在可切的标题块里，需手删）"] if remaining else [])))

    mirror_rel = _mirror_rel()
    mirror = project / mirror_rel
    x_action = None
    if os.path.lexists(mirror):
        if _harness.is_dir_link(mirror) or not mirror.is_dir():
            items.append(_item("X-rules", "blocked", f"{mirror_rel} 不是真目录，不删"))
        else:
            files = _walk_files(mirror)
            names = sorted({Path(f).name for f in files if f.endswith(".md")})
            local = project / _local_rules_rel()
            if names and local.is_dir():
                for f in _walk_files(local):
                    if not f.endswith(".md"):
                        continue
                    src_rel = f"{_local_rules_rel()}/{f}"
                    raw = (project / src_rel).read_bytes()
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    hit = lines_naming(text, names)
                    if not hit:
                        continue
                    ranges = [[h, h] for h in hit]
                    actions.append({"op": "write_candidate", "item": "W-rules-local", "source": src_rel,
                                    "name": "rules-local/" + f, "source_sha256": _sha(raw), "drop_lines": ranges,
                                    "post_sha256": _sha(drop_line_ranges(text, ranges).encode("utf-8"))})
                    items.append(_item("W-rules-local", "planned",
                                       f"{src_rel} 有 {len(hit)} 行提到即将删除的旧纪律文件——生成去掉这些行的候选文件"
                                       "（整行去掉；行里若有你自己的内容，对比时请手工保留）；本体不动，你对比后自己替换",
                                       [f"行 {', '.join(map(str, hit))}"]))
            tracked = set(_tracked(project, mirror_rel)) if git else set()
            dirty = _status_map(project, [mirror_rel]) if git else {}
            entries = [{"path": f"{mirror_rel}/{f}", "sha256": _sha((mirror / f).read_bytes()),
                        "tracked": f"{mirror_rel}/{f}" in tracked} for f in files]
            n_dirty = sum(1 for e in entries if e["tracked"] and e["path"] in dirty)
            n_untracked = sum(1 for e in entries if not e["tracked"])
            # 干净的已提交文件也按回滚时的同一判定试算（只读）：git 找回还原不了原字节的（`.gitattributes` 强制了行尾
            # 之类）同样会丢，算进「会丢」并点名——不留备份这件事给用户的知情信息要如实
            raw_lost = set(_lost_bytes(project, [e for e in entries if e["tracked"]], dirty)) if git else set()
            x_action = {"op": "remove_rules_mirror", "item": "X-rules", "dir": mirror_rel, "files": entries}
            items.append(_item("X-rules", "planned",
                               f"删除上一版同步进来的纪律镜像目录 {mirror_rel}（{len(entries)} 份）：已跟踪 "
                               f"{len(entries) - n_untracked} 份 git rm -r -f（其中 {n_dirty + len(raw_lost)} 份会丢——"
                               f"{n_dirty} 份有未提交改动"
                               + (f"、{len(raw_lost)} 份已提交但 git 找回还原不了原字节" if raw_lost else "") + "）、"
                               f"未跟踪 {n_untracked} 份直接删；**不留备份**；{_local_rules_rel()} 不碰",
                               [e["path"] + ("（未跟踪）" if not e["tracked"] else "（有未提交改动）" if e["path"] in dirty
                                             else "（git 找回还原不了原字节）" if e["path"] in raw_lost else "")
                                for e in entries]))
    if x_action:
        actions.append(x_action)

    items.sort(key=lambda it: ITEM_RANK.get(it["id"], len(ITEM_RANK)))     # 摘要按执行顺序读（排序稳定）
    items.extend(_report_items(project, git, post_kinds, bool(s_actions),
                               {r["plan_path"] for r in rewrites}, mirror_rel, residue))
    goal = agents_goal_note(project, will_create=any(a["op"] == "create_agents_md" for a in actions))
    if goal:
        items.append(_item(AGENTS_GOAL_ITEM, "report", goal))
    return {"project": str(project), "git": git, "items": items,
            "payload": {"tool": _tool_version(), "actions": actions}}


def agents_goal_note(project, will_create=False, absent_why="本次 apply 不建它"):
    """AGENTS.md「这个项目是什么」只剩框架占位 / AGENTS.md 不存在时的一句提示；都不是时返回 None。
    plan 的报告项与 apply 收尾共用这一句（判定式与文案在 doctor，这里只拼迁移侧的那半句）。

    三种状态：**本次 apply 将建出**（`will_create`：建出来的恒是无参渲染的占位，不必再判）／**此刻已是占位**／
    **不存在、本次也不建**（CLAUDE.md 未提交、`@AGENTS.md` 导入这次不加之类——下一个会话会补建一份占位）。
    判定出异常时降成一句「没判成」，不让 plan 出不来。`absent_why`：不存在时括号里那半句——plan 阶段是「本次 apply
    不建它」；apply 收尾时若动作表里有建它的那一步、却没建成（中途停下），调用方改说「没建成」。
    **只报不改**：它不是动作，不进 payload，确认码不因它变；条目 id 单列（`AGENTS_GOAL_ITEM`），不借 `L-report`
    （那个 id 的语义是旧运行态路径的字面残留），也不在 `UNMIGRATED_ITEMS` 里——收尾块不会把它算成没做完。"""
    not_moved = "迁移不搬 CLAUDE.md 里的项目内容（项目目标 / 业务背景 / 项目特殊约束等）"
    try:
        if will_create:
            return (f"本次 apply 将按模板建出 AGENTS.md，其中「{doc.AGENTS_MD_WHAT}」是框架占位"
                    f"（`{ps.AGENTS_MD_FALLBACK_GOAL}`）——{not_moved}，而 Codex 读 AGENTS.md、不读 CLAUDE.md 与 "
                    f"`.claude/rules/`。出路：{doc.agents_md_goal_outlet()}")
        state = doc.agents_md_goal_of(project)
        if state == "unfilled":
            return f"{doc.agents_md_goal_fact(state)}；{not_moved}。出路：{doc.agents_md_goal_outlet()}"
        if state == "absent":
            return f"{doc.agents_md_goal_fact(state)}（{absent_why}）。出路：{doc.agents_md_goal_outlet()}"
        return None
    except Exception as e:
        return (f"AGENTS.md「这个项目是什么」是不是占位没判成（{type(e).__name__}: {e}）——不影响迁移本身；"
                f"迁移后跑 workframe-doctor --group install 看 agents_md 项")


def _report_items(project, git, post_kinds, skills_moving, rewrite_plan_paths, mirror_rel, residue=()):
    """只列不改的几类（L-report）。`residue`：改写集里的文件按改写后的内容扫出的旧字面行。"""
    out = []
    olds = [sio.legacy_rel(k) for k in KINDS]
    runtime_rels = {sio.legacy_rel(k) for k in KINDS} | {sio.new_rel(k) for k in KINDS}
    if git:
        rc, text, _ = _git(project, "grep", "-l", "-z", "-F", *sum((["-e", o] for o in olds), []))
        files = sorted(_z(text)) if rc == 0 else []
    else:
        files = []
        skip_names = {".git", "node_modules"}
        for dirpath, dirnames, filenames in os.walk(project):
            here = Path(dirpath)
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in skip_names and not _harness.is_dir_link(here / d)
                and (here / d).relative_to(project).as_posix() not in runtime_rels | {mirror_rel, "logs", "tmp", ".tmp"}
                and not (here / d / ".git").exists())
            for fn in filenames:
                p = here / fn
                try:
                    if p.stat().st_size > 2_000_000:
                        continue
                    t = p.read_bytes().decode("utf-8")
                except Exception:
                    continue
                if any(o in t for o in olds):
                    files.append(p.relative_to(project).as_posix())
    skip_prefixes = tuple(o + "/" for o in olds) + (mirror_rel + "/",)
    rest = [f for f in files if not f.startswith(skip_prefixes) and f not in rewrite_plan_paths
            and f != ".gitignore"]                 # .gitignore 的旧规则行下面单列
    if rest:
        tagged = []
        for f in rest:
            note = ""
            if f.startswith(".codex/"):
                note = "（由 Codex 门 --upgrade 重生成，工具不碰）"
            elif f.startswith("projects/proposals/applied/"):
                note = "（提案 targets）"
            tagged.append(f + note)
        out.append(_item("L-report", "report",
                         f"其余 {len(rest)} 个{'跟踪' if git else ''}文件里有旧运行态路径（只列，不改）", tagged))
    if residue:
        out.append(_item("L-report", "report",
                         "要改写的文件在改写之后仍留着旧运行态路径（不是项目相对写法——`./`、`~/`、`$HOME/`、`<项目根>/` 之类——"
                         "或属于这次没搬的类；只列，不改）", list(residue)))
    gi = project / ".gitignore"
    if gi.is_file() and post_kinds:
        try:
            gi_lines = gi.read_bytes().decode("utf-8-sig").split("\n")
        except UnicodeDecodeError:
            gi_lines = []
        mine = [sio.legacy_rel(k) for k in post_kinds]
        hits = [f"L{i} {ln.strip()}" for i, ln in enumerate(gi_lines, 1)
                if any(o in ln for o in mine) and not ln.strip().startswith("#")]
        if hits:
            out.append(_item("L-report", "report", ".gitignore 里指向旧运行态目录的规则行（只列，不删）", hits))
    try:
        am_dir = doc._auto_memory_dir(doc.Paths(project))
    except Exception:
        am_dir = None
    if am_dir and Path(am_dir).is_dir():
        mem_old = sio.legacy_rel("memory")
        hits = []
        for f in sorted(Path(am_dir).glob("*.md")):
            try:
                lines = f.read_bytes().decode("utf-8").split("\n")
            except Exception:
                continue
            hits += [f"{f.name} L{i}: {ln.strip()[:90]}" for i, ln in enumerate(lines, 1) if mem_old in ln]
        if hits:
            out.append(_item("L-report", "report", f"auto-memory（{am_dir}）里指向旧记忆目录的行（只列，不改；归主会话维护）",
                             hits))
    state = _located_dir(project, "state")
    if state.is_dir():
        odd = sorted(p.name + ("/" if p.is_dir() else "") for p in state.iterdir()
                     if p.is_dir() or ".bak" in p.name or ".tmp" in p.name or ".corrupt." in p.name)
        if odd:
            out.append(_item("L-report", "report", "状态目录里的非标准内容（随目录一起搬，只列）", odd))
    mem = _located_dir(project, "memory")
    # 不搬的旧记忆目录（没有框架出厂形态）在 R-memory 那条里已经说明，这里不再按「随目录搬」列它的子目录
    if mem.is_dir() and (mem != project / sio.legacy_rel("memory") or doc._legacy_memory_is_framework(mem)[0]):
        baseline = set(ps.BASELINE_CORE_ROLES) | {"shared"}
        extra = sorted(p.name + "/" for p in mem.iterdir() if p.is_dir() and p.name not in baseline)
        if extra:
            out.append(_item("L-report", "report",
                             "记忆目录里的非出厂角色子目录（项目级角色属正常；其中若有 Claude Code 子 agent `memory: project` "
                             "的目录——有 agent 声明了它——搬完要手工挪回旧位置，CC 只认原位置）", extra))
    if skills_moving:
        prefix = _harness.SKILLS_DIR_CC.as_posix() + "/"
        lines = []
        idx = state / "code-paths-index.json"
        if idx.is_file():
            try:
                n = sum(1 for k in (json.loads(idx.read_bytes().decode("utf-8-sig")) or {}) if prefix in str(k))
            except Exception:
                n = 0
            if n:
                lines.append(f"{idx.name} 有 {n} 个含 {prefix} 的键")
        if git:
            rc, text, _ = _git(project, "grep", "-l", "-z", "-F", "-e", prefix, "--", "projects/modules")
            try:
                parse = _code_paths_parser()
            except RuntimeError as e:
                parse = None
                out.append(_item("L-report", "report",
                                 f"按 {prefix} 圈文件的模块清单**算不出来**：{e}——手工查 projects/modules 下各 submodule.yaml 的 code_paths"))
            for f in (_z(text) if rc == 0 and parse else []):
                if f.endswith("submodule.yaml") and any(str(p).startswith(prefix) for p in parse(project / f)):
                    lines.append(f)                  # 只认 code_paths 列表项；注释与别的字段里提到的不算
        if lines:
            out.append(_item("L-report", "report",
                             f"按 {prefix} 圈文件的模块清单（搬到中立目录后 git 只报新路径，这些要改口）", lines))
    if git and post_kinds:
        probes = {"memory": "shared/MEMORY.md", "state": "memory-index.json", "archive": "x.md"}
        paths = [f"{sio.new_rel(k)}/{probes[k]}" for k in post_kinds]
        rc, text, _ = _git(project, "check-ignore", "-v", "--no-index", "--", *paths, literal=False)
        hits = []
        for ln in text.splitlines():
            head, _tab, path = ln.partition("\t")
            pattern = head.rsplit(":", 1)[-1] if head else ""
            if path and not pattern.startswith("!"):
                hits.append(f"{path} ← {head}")
        if hits:
            out.append(_item("L-report", "report",
                             ".gitignore 会把迁移后该进 git 的文件也忽略掉（整目录规则压过了 managed 段里的例外；只列，不改）",
                             hits))
    return out


def _code_paths_parser():
    """`submodule.yaml` 的 `code_paths` 解析，复用 `check-stale-modules.py` 的 `parse_code_paths`（唯一实现）。

    **不 import 那个模块**：它在 import 期把 `sys.stdout` 换成新的 `TextIOWrapper`，而 door 已经包过一层——
    先前那层失去引用被回收时会关掉共用的底层 buffer，之后任何输出都抛 `I/O operation on closed file`。
    所以只把那一个纯函数的源码取出来单独执行；它只依赖 `re`。

    **失效要响亮**：取不出（改名 / 删除）、执行不了，或对一份已知输入给不出已知结果，都抛 `RuntimeError`，
    由调用方在 plan 里出一条「算不出来」的报告项。最后一种要靠自检：函数体开始依赖那个模块里的别的名字时，
    `NameError` 会被它自己的 `except Exception: return []` 吞成空清单——不自检就是静默漏列。"""
    import ast
    try:
        tree = ast.parse(CODE_PATHS_SOURCE.read_text(encoding="utf-8"))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "parse_code_paths")
        ns = {"re": re}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(CODE_PATHS_SOURCE), "exec"), ns)
        parse = ns["parse_code_paths"]
    except Exception as e:
        raise RuntimeError(f"取不出 {CODE_PATHS_SOURCE.name} 的 parse_code_paths（{type(e).__name__}: {e}）")

    class _Probe:                                # 自检输入走内存，plan 阶段不写文件
        def read_text(self, encoding=None, errors=None):
            return 'name: x\ncode_paths:\n  - ".claude/skills/a/**"   # c\n  - b.py\nother: 1\n'

    try:
        got = parse(_Probe())
    except Exception as e:
        got = f"{type(e).__name__}: {e}"
    if got != [".claude/skills/a/**", "b.py"]:
        raise RuntimeError(f"{CODE_PATHS_SOURCE.name} 的 parse_code_paths 自检不过（已知输入得到 {got!r}）"
                           "——函数体是否依赖了那个模块里的别的名字")
    return parse


# ---------------------------------------------------------------- plan 文件与摘要

def confirm_code(payload):
    return _sha(_canon(payload).encode("utf-8"))[:8]


def apply_command(project, code):
    script = PLUGIN_ROOT / "scripts" / "workframe_door.py"
    return f'python "{script}" --migrate --apply --confirm {code} --project "{project}"'


def build_plan(project):
    computed = compute_plan(project)
    code = confirm_code(computed["payload"])
    return {"__schema__": PLAN_SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "project": computed["project"], "git": computed["git"], "code": code,
            "apply_command": apply_command(computed["project"], code),
            "apply_by": "用户本人在普通终端里执行 apply_command；模型不得代为传 --apply",
            "items": computed["items"], "payload": computed["payload"]}


def write_plan(project, plan):
    path = _out_dir(project) / PLAN_NAME
    sio.atomic_write(path, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    return path


def render_items(items, limit=8):
    out = []
    mark = {"planned": "→", "blocked": "✗", "conflict": "!", "skipped": "-", "report": "i"}
    for it in items:
        out.append(f"  {mark.get(it['status'], '?')} [{it['id']}] {it['title']}")
        for ln in it["lines"][:limit]:
            out.append(f"        {ln}")
        if len(it["lines"]) > limit:
            out.append(f"        …另 {len(it['lines']) - limit} 行（全文见 plan.json）")
    return out


def load_plan(project):
    path = _out_dir(project) / PLAN_NAME
    if not path.is_file():
        raise MigrateRefused(f"没有 plan 文件（{path}）——先出 plan")
    try:
        plan = json.loads(path.read_bytes().decode("utf-8"))
    except Exception as e:
        raise MigrateRefused(f"plan 文件读不出来（{type(e).__name__}）——重出 plan")
    if not isinstance(plan, dict) or plan.get("__schema__") != PLAN_SCHEMA or not isinstance(plan.get("payload"), dict):
        raise MigrateRefused("plan 文件形态不对——重出 plan")
    return plan


# ---------------------------------------------------------------- ④ 封闭枚举

def _safe_rel(rel):
    if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        return False
    return all(part not in ("", ".", "..") for part in rel.split("/"))


def validate_actions(actions):
    """越界即拒：返回问题清单（空 = 通过）。"""
    problems = []
    moves = {(sio.legacy_rel(k), sio.new_rel(k), ITEM_BY_KIND[k]) for k in KINDS}
    cc_rel, neu_rel = _harness.SKILLS_DIR_CC.as_posix(), _harness.SKILLS_DIR_NEUTRAL.as_posix()
    moves.add((cc_rel, neu_rel, "S-skills"))
    skill_prefixes = (cc_rel + "/", neu_rel + "/")
    local_prefix = _local_rules_rel() + "/"
    index_paths = {f"{sio.new_rel('state')}/rollback-index.json", f"{sio.legacy_rel('state')}/rollback-index.json"}
    for i, a in enumerate(actions if isinstance(actions, list) else [None]):
        op = a.get("op") if isinstance(a, dict) else None
        where = f"动作 {i + 1}（{op}）"
        if op not in OPS:
            problems.append(f"{where}：类型不在固定集合内")
            continue
        if op == "move_dir":
            if (a.get("src"), a.get("dst"), a.get("item")) not in moves or a.get("method") not in ("git-mv", "rename"):
                problems.append(f"{where}：{a.get('src')} → {a.get('dst')} 不是本迁移的搬迁对象")
        elif op == "make_skills_link":
            if (a.get("link"), a.get("target")) != (cc_rel, neu_rel):
                problems.append(f"{where}：链接路径越界")
        elif op == "write_gitignore":
            if (a.get("path") != ".gitignore" or not isinstance(a.get("skills_link"), bool)
                    or a.get("state_rel") not in (sio.new_rel("state"), sio.legacy_rel("state"))):
                problems.append(f"{where}：只许按两种状态目录之一写 .gitignore")
        elif op == "create_agents_md":
            if a.get("path") != "AGENTS.md":
                problems.append(f"{where}：只许建 AGENTS.md")
        elif op == "rewrite_file":
            path, plan_path, tf = a.get("path"), a.get("plan_path"), a.get("transforms")
            kinds_ok = isinstance(a.get("kinds"), list) and all(k in KINDS for k in a["kinds"])
            ok = _safe_rel(path) and _safe_rel(plan_path) and kinds_ok and isinstance(tf, list)
            if ok and a.get("item") == "R-index":
                ok = path in index_paths and plan_path in index_paths and tf == ["json-prefix"]
            elif ok:
                ok = (set(tf) <= {"literals", "agents-import"} and tf
                      and ((path in ("CLAUDE.md", "AGENTS.md") and path == plan_path
                            and ("agents-import" not in tf or path == "CLAUDE.md"))
                           or (path.endswith(".md") and path.startswith(skill_prefixes)
                               and plan_path.startswith(skill_prefixes) and "agents-import" not in tf
                               and path.split("/", 2)[2] == plan_path.split("/", 2)[2])))
            if not ok:
                problems.append(f"{where}：{path} 不在允许改写的文件范围内")
        elif op == "write_candidate":
            src, name, ranges = a.get("source"), a.get("name"), a.get("drop_lines")
            ok = (_safe_rel(src) and _safe_rel(name) and isinstance(ranges, list)
                  and all(isinstance(r, list) and len(r) == 2 and all(isinstance(x, int) for x in r) for r in ranges)
                  and ((src == "CLAUDE.md" and name == "CLAUDE.md")
                       or (src.startswith(local_prefix) and src.endswith(".md")
                           and name == "rules-local/" + src[len(local_prefix):])))
            if not ok:
                problems.append(f"{where}：候选文件的来源 / 落点越界")
        elif op == "remove_rules_mirror":
            files = a.get("files")
            mirror = _mirror_rel()
            if (a.get("dir") != mirror or not isinstance(files, list)
                    or not all(isinstance(f, dict) and _safe_rel(f.get("path")) and f["path"].startswith(mirror + "/")
                               for f in files)):
                problems.append(f"{where}：删除范围越出 {mirror}")
    return problems


# ---------------------------------------------------------------- 会话信号（弱）

def session_signals(project, now=None):
    project = Path(project)
    msgs = []
    state = _located_dir(project, "state")
    if not state.is_dir():
        return msgs
    now = now if now is not None else datetime.now().timestamp()
    try:
        latest = max((p.stat().st_mtime for p in state.iterdir() if p.is_file()), default=None)
    except OSError:
        latest = None
    if latest is not None and now - latest < SESSION_RECENT_SEC:
        msgs.append(f"状态目录 {int(now - latest)} 秒前还有写入——可能有会话没关（刚关掉的会话也会这样）")
    markers = sorted(p.name for p in state.glob("current-session-codex-*.json"))
    if markers:
        msgs.append(f"有 Codex 会话标记文件：{', '.join(markers)}")
    for lock in sorted(state.glob("*.lock")):
        if not lock.is_file():
            continue
        try:
            with sio.FileLock(lock, timeout=0.3):
                pass
        except TimeoutError:
            msgs.append(f"别的进程正持有 {lock.name}")
        except OSError:
            pass
    return msgs


# ---------------------------------------------------------------- 执行

def _write_bytes_atomic(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + f".migrate-tmp.{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(str(tmp), str(path))


def _receipt_dir(project):
    base = _out_dir(project)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d, n = base / stamp, 2
    while d.exists():
        d = base / f"{stamp}-{n}"
        n += 1
    return d


INVERSE_SHELL = "PowerShell" if os.name == "nt" else "sh"


def _inverse(a, project, receipt_dir, log=None):
    """一条动作的逆操作：`cmd` **只放命令本身**（在 `INVERSE_SHELL` 里原样粘贴就能跑；多条时以换行分隔、逐行执行），
    说明放 `note`，打印时说明单独成一行 `#` 注释。命令里的路径一律单引号（PowerShell 与 sh 里单引号内都不做变量展开）。
    `log`：这条动作的回执记录（镜像目录的找回命令按其中 `_restore_groups` 的结果带 `core.autocrlf`）。"""
    op = a["op"]
    project = Path(project)
    nt = os.name == "nt"

    def q(p):
        s = str(p)
        return "'" + (s.replace("'", "''") if nt else s.replace("'", "'\\''")) + "'"

    if op == "move_dir":
        if a["method"] == "git-mv":
            return {"do": "git-mv", "src": a["dst"], "dst": a["src"], "note": None,
                    "cmd": f"git -C {q(project)} mv -- {q(a['dst'])} {q(a['src'])}"}
        cmd = (f"Move-Item -LiteralPath {q(project / a['dst'])} -Destination {q(project / a['src'])}" if nt
               else f"mv {q(project / a['dst'])} {q(project / a['src'])}")
        return {"do": "rename", "src": a["dst"], "dst": a["src"], "note": None, "cmd": cmd}
    if op == "make_skills_link":
        cmd = f"cmd /c rmdir {q(project / a['link'])}" if nt else f"rm {q(project / a['link'])}"
        return {"do": "unlink-dir", "path": a["link"], "note": "只解目录链接，不碰它指向的目录", "cmd": cmd}
    if op in ("write_gitignore", "rewrite_file", "create_agents_md") and a.get("pre_sha256") is None:
        cmd = f"Remove-Item -LiteralPath {q(project / a['path'])}" if nt else f"rm {q(project / a['path'])}"
        return {"do": "remove-new-file", "path": a["path"], "note": f"{a['path']} 是 apply 新建的；要完全回到 apply 前才删，你决定",
                "cmd": cmd}
    if op in ("write_gitignore", "rewrite_file"):
        pre = receipt_dir / "preimage" / a["path"]
        cmd = (f"Copy-Item -LiteralPath {q(pre)} -Destination {q(project / a['path'])} -Force" if nt
               else f"cp {q(pre)} {q(project / a['path'])}")
        return {"do": "copy-back", "from": str(pre), "path": a["path"], "note": None, "cmd": cmd}
    if op == "remove_rules_mirror":
        groups = (log or {}).get("restore_groups") or []
        if not groups:
            return None                          # 没有 HEAD 里的已跟踪文件：git 找不回任何东西
        cmds = [f"git -C {q(project)} -c core.autocrlf={g['autocrlf']} restore --source=HEAD --staged --worktree -- "
                + (q(a["dir"]) if g["paths"] is None else " ".join(q(p) for p in g["paths"])) for g in groups]
        lost = (log or {}).get("restore_lost_bytes") or []
        return {"do": "git-restore", "path": a["dir"],
                "note": "只找回已提交的版本（行尾按 apply 那一刻逐文件试算过）；未提交的改动与未跟踪文件找不回"
                        + (f"；这几份已提交但 git 找回也还原不了原字节：{'、'.join(lost)}" if lost else ""),
                "cmd": "\n".join(cmds)}
    return None


def _manifest(project, rels):
    out = {}
    for rel in rels:
        p = Path(project) / rel
        if _harness.is_dir_link(p):
            continue
        if p.is_file():
            out[rel] = _sha(p.read_bytes())
        elif p.is_dir():
            for f in _walk_files(p):
                out[f"{rel}/{f}"] = _sha((p / f).read_bytes())
    return out


def affected_paths(actions):
    """回滚对账面：apply 前各动作动到的路径（按 apply 前的位置）。"""
    paths = set()
    for a in actions:
        if a["op"] == "move_dir":
            paths.add(a["src"])
        elif a["op"] in ("rewrite_file", "write_gitignore"):
            paths.add(a.get("plan_path") or a["path"])
        elif a["op"] == "remove_rules_mirror":
            paths.add(a["dir"])
        elif a["op"] == "create_agents_md":
            paths.add(a["path"])
    return sorted(paths)


def _precheck_all(project, actions, git):
    """执行第一个动作之前的全部前置检查（针对 apply 开始时的项目状态）。"""
    problems = []
    staged = _staged_paths(project) if git else []
    if git:
        lock = _index_lock(project)
        if lock is not None and lock.exists():
            problems.append(f"{lock} 存在——git 正在运行或上次异常退出；确认没有 git 进程后再处理它")
    for a in actions:
        op = a["op"]
        if op == "move_dir":
            src, dst = project / a["src"], project / a["dst"]
            if os.path.lexists(dst):
                problems.append(f"{a['dst']} 已存在（空目录也算）——搬过去会嵌套")
            if not src.is_dir() or _harness.is_dir_link(src):
                problems.append(f"{a['src']} 不是真目录")
            elif git and a["method"] == "git-mv" and (_tracked(project, a["src"]) != a["tracked"]
                                                      or _deleted(project, a["src"])):
                problems.append(f"{a['src']} 的跟踪状态与 plan 不同")
            if _staged_inside(staged, a["src"]):
                problems.append(f"{a['src']} 里有已暂存的改动")
        elif op == "make_skills_link" and not any(x["op"] == "move_dir" and x["dst"] == a["target"] for x in actions):
            if not (project / a["target"]).is_dir() or os.path.lexists(project / a["link"]):
                problems.append(f"{a['link']} 位置上已有东西，或 {a['target']} 不是目录")
        elif op in ("rewrite_file", "write_gitignore"):
            p = project / (a.get("plan_path") or a["path"])
            if (_sha(p.read_bytes()) if p.is_file() else None) != a.get("pre_sha256"):
                problems.append(f"{a.get('plan_path') or a['path']} 与 plan 时的内容不同")
        elif op == "create_agents_md":
            if os.path.lexists(project / a["path"]):
                problems.append("AGENTS.md 已存在")
        elif op == "remove_rules_mirror":
            d = project / a["dir"]
            now = sorted(f"{a['dir']}/{f}" for f in _walk_files(d)) if d.is_dir() else []
            if now != sorted(f["path"] for f in a["files"]):
                problems.append(f"{a['dir']} 的文件清单与 plan 不同")
    return problems


def _do_action(project, a, receipt_dir, git, log):
    op = a["op"]
    if op == "move_dir":
        src, dst = project / a["src"], project / a["dst"]
        if os.path.lexists(dst):
            raise RuntimeError(f"{a['dst']} 此刻已存在——不搬（会嵌套）")
        if not src.is_dir() or _harness.is_dir_link(src):
            raise RuntimeError(f"{a['src']} 此刻不是真目录")
        if git:
            lock = _index_lock(project)
            if lock is not None and lock.exists():
                raise RuntimeError(f"{lock} 此刻存在")
        if git and _staged_inside(_staged_paths(project), a["src"]):
            raise RuntimeError(f"{a['src']} 此刻有已暂存的改动——搬过去会断开改名历史")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if a["method"] == "git-mv":
            if _tracked(project, a["src"]) != a["tracked"] or _deleted(project, a["src"]):
                raise RuntimeError(f"{a['src']} 的跟踪状态与 plan 不同")
            rc, _out, err = _git(project, "mv", "--", a["src"], a["dst"])
            log["git"] = f"git mv -- {a['src']} {a['dst']} → rc {rc}" + (f"：{err}" if err else "")
            if rc != 0:
                raise RuntimeError(f"git mv 失败（rc {rc}）：{err}")
        else:
            os.rename(src, dst)
        if os.path.lexists(src) or not dst.is_dir():
            raise RuntimeError(f"搬完核对不过：{a['src']} 还在或 {a['dst']} 不是目录")
    elif op == "make_skills_link":
        created, skipped = [], []
        if not ps.ensure_project_skills_link(project, created, skipped):
            raise RuntimeError("目录链接没建成：" + "；".join(skipped or ["链接位置上已有东西或中立目录不在"]))
        if not _harness.is_dir_link(project / a["link"]):
            raise RuntimeError("建完核对不过：链接不在")
    elif op in ("write_gitignore", "rewrite_file"):
        p = project / a["path"]
        cur = p.read_bytes() if p.is_file() else None
        if (_sha(cur) if cur is not None else None) != a.get("pre_sha256"):
            raise RuntimeError(f"{a['path']} 此刻的内容与 plan 不同")
        if op == "write_gitignore":
            new = gitignore_bytes(cur, a["state_rel"], a["skills_link"])[0]
        else:
            new = apply_text_transforms(cur, a["transforms"], a["kinds"])
        if new is None or _sha(new) != a["post_sha256"]:
            raise RuntimeError(f"{a['path']} 改写结果与 plan 算出的不同——不写")
        _write_bytes_atomic(p, new)
        log["pre_sha256"], log["post_sha256"] = a.get("pre_sha256"), a["post_sha256"]
    elif op == "create_agents_md":
        ps.ensure_agents_md(project, [], [])
        p = project / a["path"]
        if not p.is_file() or _sha(p.read_bytes()) != a["post_sha256"]:
            raise RuntimeError("AGENTS.md 建出来的内容与 plan 算出的不同")
    elif op == "write_candidate":
        src = project / a["source"]
        raw = src.read_bytes()
        if _sha(raw) != a["source_sha256"]:
            raise RuntimeError(f"{a['source']} 此刻的内容与 plan 预期不同")
        cand = drop_line_ranges(raw.decode("utf-8"), a["drop_lines"]).encode("utf-8")
        if _sha(cand) != a["post_sha256"]:
            raise RuntimeError("候选文件与 plan 算出的不同")
        out = receipt_dir / "candidates" / a["name"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(cand)
        log["candidate"] = str(out)
        log["diff"] = f'git diff --no-index -- "{src}" "{out}"'
    elif op == "remove_rules_mirror":
        d = project / a["dir"]
        now = sorted(f"{a['dir']}/{f}" for f in _walk_files(d)) if d.is_dir() else []
        if now != sorted(f["path"] for f in a["files"]):
            raise RuntimeError(f"{a['dir']} 此刻的文件清单与 plan 不同")
        for f in a["files"]:
            if _sha((project / f["path"]).read_bytes()) != f["sha256"]:
                raise RuntimeError(f"{f['path']} 此刻的内容与 plan 不同")
        tracked = sorted(f["path"] for f in a["files"] if f["tracked"])
        log["restore_groups"], log["restore_unmatched"], log["restore_not_in_head"] = [], [], []
        log["restore_lost_bytes"] = []
        if tracked:
            if not git or _tracked(project, a["dir"]) != tracked:
                raise RuntimeError(f"{a['dir']} 的跟踪状态与 plan 不同")
            (log["restore_groups"], log["restore_unmatched"],
             log["restore_not_in_head"]) = _restore_groups(project, [f for f in a["files"] if f["tracked"]])
            dirty = _status_map(project, [a["dir"]])
            log["restore_lost_bytes"] = sorted(p for p in log["restore_unmatched"] if p not in dirty)
            rc, _out, err = _git(project, "rm", "-r", "-f", "-q", "--", a["dir"])
            log["git"] = f"git rm -r -f -q -- {a['dir']} → rc {rc}" + (f"：{err}" if err else "")
            if rc != 0:
                raise RuntimeError(f"git rm 失败（rc {rc}）：{err}")
        for f in a["files"]:
            p = project / f["path"]
            if p.is_file():
                p.unlink()
        if d.is_dir():
            for dirpath, _dirnames, filenames in os.walk(d, topdown=False):
                if filenames:
                    raise RuntimeError(f"{dirpath} 里还有 plan 之外的文件")
                os.rmdir(dirpath)
        parent = d.parent                       # 纪律镜像目录的上一层只为它而建；空了一并去掉
        if parent.is_dir() and not any(parent.iterdir()):
            os.rmdir(parent)
        if os.path.lexists(d):
            raise RuntimeError(f"{a['dir']} 删完核对不过：还在")


def _restore_groups(project, files):
    """删镜像目录之前，按每个已跟踪文件**此刻的工作树字节**试算：从 git 找回时带哪个 `core.autocrlf` 才逐字节一样。

    `git restore` 按回滚那一刻的 git 配置与属性做行尾转换：全局 `core.autocrlf=true` 的项目里，工作树原本是 LF 的
    已提交文件会被找回成 CRLF——内容没丢，但对不上 `pre_manifest`，按文档判回滚完整性的人分不清是丢了还是只差行尾。
    试算用 `git cat-file --filters HEAD:./<路径>`（检出时会做的转换），只读。

    返回 (组, 两种都对不上的, 不在 HEAD 里的)。组是 `[{"autocrlf": "false"|"true", "paths": [...] 或 None}]`：
    一种取值对所有对得上的文件都成立 ⇒ 一组、`paths=None`（整目录一条命令）；否则按取值分两组、各列文件
    （两组不相交，每个文件只由一条命令写出，结果不依赖两条命令的先后）。
    两种都对不上的（多半有未提交改动）归第一组，只能找回 HEAD 版本；不在 HEAD 里的找不回，不进任何组。"""
    matches, not_in_head = {}, []
    for f in files:
        shas = {}
        for v in ("false", "true"):
            rc, data, _err = _git(project, "-c", f"core.autocrlf={v}", "cat-file", "--filters", f"HEAD:./{f['path']}",
                                  raw=True)
            if rc != 0:
                break
            shas[v] = _sha(data)
        if len(shas) < 2:
            not_in_head.append(f["path"])
            continue
        matches[f["path"]] = {v for v, s in shas.items() if s == f["sha256"]}
    unmatched = sorted(p for p, m in matches.items() if not m)
    if not matches:
        return [], unmatched, not_in_head
    reproducible = {p for p, m in matches.items() if m}
    for v in ("false", "true"):
        if all(v in matches[p] for p in reproducible):
            return [{"autocrlf": v, "paths": None}], unmatched, not_in_head
    first = sorted(p for p in reproducible if "false" in matches[p]) + unmatched
    second = sorted(p for p in reproducible if "false" not in matches[p])
    return [{"autocrlf": "false", "paths": first}, {"autocrlf": "true", "paths": second}], unmatched, not_in_head


def _lost_bytes(project, files, dirty):
    """已跟踪、没有未提交改动（不在 `dirty` 里），按 `_restore_groups` 的试算两种 `core.autocrlf` 都还原不了原字节的文件。
    plan 的 X-rules「会丢」计数用它；apply 在 `_do_action` 里用同一次试算的结果记 `restore_lost_bytes`。"""
    _groups, unmatched, _not_in_head = _restore_groups(project, files)
    return sorted(p for p in unmatched if p not in dirty)


def _action_target(a):
    """进度行里「动的是什么」。每种动作各自的路径键不同，逐个列出——漏一种就会打印成 None。"""
    op = a["op"]
    if op == "move_dir":
        return f"{a['src']} → {a['dst']}"
    if op == "make_skills_link":
        return f"{a['link']} → {a['target']}"
    if op == "remove_rules_mirror":
        return a["dir"]
    if op == "write_candidate":
        return a["source"]
    return a["path"]          # write_gitignore / rewrite_file / create_agents_md


def execute_plan(plan, project, out=print):
    """**唯一的测试接缝**：按 plan 执行。重算比对（①）与封闭枚举（④）在这里；确认码与环境提示在 CLI 层。

    返回回执字典（`status` 为 `ok` / `failed`）；执行第一个动作之前的拒绝抛 `MigrateRefused`（零写入）。"""
    project = Path(project).resolve()
    current = compute_plan(project)
    plan_payload = plan.get("payload") if isinstance(plan, dict) else None
    if _canon(plan_payload) != _canon(current["payload"]):
        cur_actions = current["payload"]["actions"]
        plan_actions = plan_payload.get("actions") if isinstance(plan_payload, dict) else None
        if not isinstance(plan_payload, dict) or plan_payload.get("tool") != current["payload"]["tool"]:
            diff = "工具版本不同（插件升级过，或脚本改过）"
        else:
            diff = f"动作条数不同（plan {len(plan_actions or [])} 条，现在 {len(cur_actions)} 条）"
            for i, (x, y) in enumerate(zip(plan_actions or [], cur_actions)):
                if _canon(x) != _canon(y):
                    diff = (f"第 {i + 1} 条动作不同（plan：{x.get('op') if isinstance(x, dict) else x} "
                            f"{x.get('item') if isinstance(x, dict) else ''}；现在：{y.get('op')} {y.get('item')}）")
                    break
        raise MigrateRefused(f"按项目当前状态重算的动作与 plan 不一致：{diff}——重出 plan、核对后再 apply")
    actions = current["payload"]["actions"]
    problems = validate_actions(actions)
    if problems:
        raise MigrateRefused("动作越界：" + "；".join(problems))
    if not actions:
        raise MigrateRefused("plan 里没有要执行的动作")
    git = current["git"]
    problems = _precheck_all(project, actions, git)
    if problems:
        raise MigrateRefused("前置检查不过：" + "；".join(problems))

    receipt_dir = _receipt_dir(project)
    pre_manifest = _manifest(project, affected_paths(actions))
    for a in actions:
        if a["op"] in ("rewrite_file", "write_gitignore") and a.get("pre_sha256") is not None:
            dst = receipt_dir / "preimage" / a["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes((project / (a.get("plan_path") or a["path"])).read_bytes())
    staged_before = []
    if git:
        rc, text, _ = _git(project, "diff", "--cached", "--name-only", "-z")
        staged_before = _z(text) if rc == 0 else []
    receipt = {"__schema__": RECEIPT_SCHEMA, "project": str(project), "code": plan.get("code"),
               "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": "running",
               "tool": current["payload"]["tool"], "receipt_dir": str(receipt_dir), "staged_before": staged_before,
               "affected_paths": affected_paths(actions), "pre_manifest": pre_manifest, "actions": []}
    receipt_path = receipt_dir / "receipt.json"

    def flush():
        sio.atomic_write(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")

    flush()
    for i, a in enumerate(actions, 1):
        log = {"n": i, "op": a["op"], "item": a["item"]}
        receipt["actions"].append(log)
        try:
            _do_action(project, a, receipt_dir, git, log)
        except Exception as e:
            log["status"] = "failed"
            log["error"] = f"{type(e).__name__}: {e}"
            receipt["status"], receipt["stopped_at"], receipt["error"] = "failed", i, log["error"]
            out(f"  ✗ [{i}/{len(actions)}] {a['op']} {a['item']}：{log['error']}")
            break
        log["status"] = "done"
        log["inverse"] = _inverse(a, project, receipt_dir, log)
        out(f"  ✓ [{i}/{len(actions)}] {a['op']} {a['item']}  {_action_target(a)}")
        flush()
    if receipt["status"] == "running":
        leftovers = [x["src"] for x in actions if x["op"] == "move_dir" and os.path.lexists(project / x["src"])
                     and not _harness.is_dir_link(project / x["src"])]
        if leftovers:
            receipt["status"], receipt["error"] = "failed", f"执行完旧路径仍在：{leftovers}"
            receipt["leftovers"] = leftovers
        else:
            receipt["status"] = "ok"
    receipt["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    flush()
    return receipt


# ---------------------------------------------------------------- CLI（door 调）

def cli_plan(project, out=print):
    plan = build_plan(project)
    path = write_plan(project, plan)
    actions = plan["payload"]["actions"]
    out(f"迁移 plan：{plan['project']}（{'git 仓' if plan['git'] else '非 git 仓'}）")
    for ln in render_items(plan["items"]):
        out(ln)
    out(f"plan 文件：{path}")
    if not actions:
        out("没有要执行的动作（上面列的只是报告项）。")
        return 0
    out(f"共 {len(actions)} 条动作；确认码：{plan['code']}")
    out("执行前关掉这个项目的全部 Claude Code / Codex 会话，以及打开着它的编辑器、Obsidian。")
    out("执行由你本人跑：普通终端（PowerShell / Windows Terminal）里粘贴下面这行；也可以在**别的项目**的 Claude Code"
        " 会话里加 `!` 前缀跑（别用被迁移项目自己的会话）。模型不得替你传 --apply。")
    out(f"  {plan['apply_command']}")
    return 0


def _skills_link_untracked_note(project, git):
    """项目 skills 的目录链接没被 `.gitignore` 盖住时，提醒别把它当改动提交（返回要打印的行，否则空）。

    本工具写的是无尾斜杠的 `.claude/skills`，两平台都盖得住，正常迁移完不会走到这里；带尾斜杠的旧写法（POSIX 上
    匹配不到符号链接）或 marker 外的否定规则盖不住时才会。**按 `git check-ignore` 判，不按平台判**。"""
    cc_rel = _harness.SKILLS_DIR_CC.as_posix()
    if not git or not _harness.is_dir_link(Path(project) / cc_rel):
        return []
    rc, _out, _err = _git(project, "check-ignore", "-q", "--no-index", "--", cc_rel, literal=False)
    if rc == 0:
        return []
    return [f"     注意：`{cc_rel}` 是指向 {_harness.SKILLS_DIR_NEUTRAL.as_posix()} 的目录链接，此刻会以未跟踪出现在 "
            "git status 里——**别提交它**，也别 `git add -A`（当前的忽略写法盖不住这种链接）。"]


def _pending_after_stop(receipt, actions):
    """中途停下时没做完的动作，按条目去重：[(条目 id, 说明)]。已完成的步骤不在内。

    停在某一步 ⇒ 那一步与它之后没执行的全部动作；全部执行完才发现旧路径仍在 ⇒ 那几条搬迁动作。

    **两个来源的耦合**：`stopped_at` 的序号出自 `execute_plan` 重算的动作表，这里拿的是 plan 文件里的那份。
    两者此刻必然相同——重算比对不等即拒、连码都对不上。真要放宽了那道比对，这里就会按错位的动作报错步；
    所以先核一遍回执里那一步的 `op` / `item` 对不对得上，对不上就只按回执报、不再顺着索引往后列。"""
    n = receipt.get("stopped_at")
    if n:
        logs = receipt.get("actions") or []
        log = logs[n - 1] if len(logs) >= n else {}
        a = actions[n - 1] if n <= len(actions) else None
        if a is None or (log.get("op"), log.get("item")) != (a["op"], a["item"]):
            return [(log.get("item") or "?", f"停在第 {n} 步（{log.get('op')}，{receipt.get('error')}）"
                                             "——plan 文件与回执对不上，后面还有哪些没做请看回执")]
        rows = [(a, f"停在这一步（{receipt.get('error')}）")] + [(x, "还没执行") for x in actions[n:]]
    else:
        left = set(receipt.get("leftovers") or [])
        rows = [(a, "执行完旧路径仍在") for a in actions if a["op"] == "move_dir" and a["src"] in left]
    seen, out = set(), []
    for a, note in rows:
        if a["item"] not in seen:
            seen.add(a["item"])
            out.append((a["item"], f"{note}：{a['op']} {_action_target(a)}"))
    return out


def _unfinished_block(items, pending=(), project=None):
    """apply 收尾打印的「没做完」醒目块（什么都做完了时为空；非空即退出码 8）。四种来源、两种措辞：

    - **未迁完**（`UNMIGRATED_ITEMS` 里的类 blocked / conflict，或中途停下时 `pending` 里属于这几类的、以及认不出的
      条目 id）：那一类还留在旧位置，或新布局的忽略条目缺着——框架只读新位置，开会话会在新位置另建一套、与旧目录冲突
      （记忆注入为空之类）⇒「框架只读新位置……处理完之前别开这个项目的会话」；
    - **没做完**（其余 blocked，如导入加不上、rollback-index 改不了；中途停下时 `pending` 里的其余条目）与**跳过改写**
      （条目 `data["skipped"]`：要改写的文件有未提交改动或未跟踪，按「先提交」整份跳过）：只是某个文件没改、不涉及运行态
      目录的位置 ⇒「补做之前别开这个项目的会话」——CLAUDE.md / AGENTS.md 每个会话都加载、skills 调用时读到，跳过的那几份
      仍指着已经搬走的旧目录。
    `pending`：`_pending_after_stop` 的结果（中途停下时才有；回执对不上时条目 id 可能是 `?`）。`project`：给跳过改写的
    文件取**此刻**的位置——skills md 已随目录搬到中立目录时，要提交的是新位置。
    回执与退出码若报「完成」，没人会回头看 plan 里那几行。"""
    window = [(it["id"], it["title"]) for it in items
              if it["status"] in UNFINISHED_STATUSES and it["id"] in UNMIGRATED_ITEMS]
    other = [(it["id"], it["title"]) for it in items
             if it["status"] in UNFINISHED_STATUSES and it["id"] not in UNMIGRATED_ITEMS]
    for item_id, note in pending:
        # 认不出的 id（回执那条 log 缺 `item` 时是 `?`）归「未迁完」：分不清时往多拦的一侧报
        (window if item_id in UNMIGRATED_ITEMS or item_id not in ITEM_RANK else other).append((item_id, note))
    skipped = [s for it in items for s in (it.get("data") or {}).get("skipped", [])]
    if not (window or other or skipped):
        return []

    def now_at(s):
        post = s.get("post_path")
        return post if post and project is not None and (Path(project) / post).is_file() else s["path"]

    out = ["!" * 60]
    out += [f"! 未迁完：{i}——{t}" for i, t in window]
    out += [f"! 没做完：{i}——{t}" for i, t in other]
    out += [f"! 跳过改写：{now_at(s)}（{s['why']}）——{s['what']}没做" for s in skipped]
    if window:
        out.append("! 「未迁完」的那几类还留在旧位置，而框架只读新位置——开这个项目的会话会在新位置另建一套、与旧目录冲突。"
                   "先处理原因，再重出 plan、apply 剩下的；处理完之前别开这个项目的会话。")
    if other or skipped:
        out.append("! 「没做完」「跳过改写」的只是这几份文件没改、不涉及运行态目录的位置：先处理原因（跳过改写的先提交那个文件），"
                   "再重出 plan、apply 补做；补做之前别开这个项目的会话。")
    out.append("!" * 60)
    return out


def cli_apply(project, confirm, out=print):
    """返回退出码：0 完成 / 7 执行前拒绝（零写入）/ 8 没做完——执行中途停下（见回执）、有类 blocked / conflict 没迁，
    或有要改写的文件被跳过（见 `_unfinished_block`）。"""
    project = Path(project).resolve()
    try:
        if not confirm:
            raise MigrateRefused("缺 --confirm <确认码>（码在 plan 输出里）")
        plan = load_plan(project)
        current = compute_plan(project)
        code = confirm_code(current["payload"])
        if confirm != code:
            # 不回显按当前状态算出的码：回显了，随便传一个错码就能从报错里拿到正确的码，「必须显式传入」形同虚设
            raise MigrateRefused(f"确认码不符（你给的是 {confirm}）——出 plan 之后项目变了，或码抄错了；"
                                 "重出 plan、核对摘要后再来")
        out(f"迁移 apply：{project}（确认码 {code}）")
        for ln in render_items([it for it in current["items"] if it["status"] in APPLY_SUMMARY_STATUSES]):
            out(ln)
        reasons = _harness.not_plain_terminal_reasons()
        if reasons:
            out("!" * 60)
            out("! 当前不在普通终端里（可能是 Claude Code 的 `!` 前缀，也可能是 AI 在调用）：" + "；".join(reasons))
            out("! 删除与改写即将执行——确认是你本人在操作。")
            out("!" * 60)
        for msg in session_signals(project):
            out(f"! 会话信号：{msg}——确认这个项目的会话都已关掉")
        receipt = execute_plan(plan, project, out=out)
    except MigrateRefused as e:
        out(f"迁移 apply 拒绝执行（零写入）：{e}")
        return EXIT_REFUSED
    out(f"回执：{Path(receipt['receipt_dir']) / 'receipt.json'}")
    done = [x for x in receipt["actions"] if x.get("status") == "done"]
    for x in done:
        if x.get("diff"):
            out(f"  候选文件对比：{x['diff']}")
    if done:
        out(f"逆操作——在 {INVERSE_SHELL} 里从上到下逐行执行（已按倒序排好；`#` 开头的是说明，一起粘进去也无妨）。"
            "回滚是否完整以回执里 pre_manifest 的逐文件 sha256 为准，不以 git status 为准：")
        for x in reversed(done):
            inv = x.get("inverse")
            if inv:
                if inv.get("note"):
                    out(f"  # {inv['note']}")
                for ln in inv["cmd"].split("\n"):
                    out(f"  {ln}")
    # plan 里那条报告项 apply 摘要不重印（只印 APPLY_SUMMARY_STATUSES），而先迁移再开会话的路径上首会话验收不会跑
    # （计数随状态目录搬过来，不是 1）——apply 收尾是这批人能自动看到它的最后一处。按此刻的盘面重判，三种结局都打
    goal = agents_goal_note(project, absent_why=(
        "本次 apply 没建成，见下面的收尾块" if any(a["op"] == "create_agents_md" for a in plan["payload"]["actions"])
        else "本次 apply 不建它"))
    if goal:
        out(f"i {goal}")
    actions = plan["payload"]["actions"]
    pending = _pending_after_stop(receipt, actions) if receipt["status"] != "ok" else []
    unfinished = _unfinished_block(current["items"], pending, project)
    if receipt["status"] != "ok":
        out(f"停下了：{receipt.get('error')}")
        out("  已完成的步骤保持原样（见回执）。处理完原因（例如关掉占用文件的程序）后重出 plan，只会剩下没做完的条目。")
        if (any(x["op"] == "make_skills_link" for x in done) and not any(x["op"] == "write_gitignore" for x in done)
                and any(a["op"] == "write_gitignore" for a in actions)):
            out("  注意：.gitignore 条目还没写上，链接那边的文件此刻会出现在 git status 里——续做之前别 git add -A。")
        for ln in unfinished:
            out(ln)
        return EXIT_PARTIAL
    if receipt.get("staged_before"):
        out(f"注意：apply 之前索引里已有你暂存的 {len(receipt['staged_before'])} 个改动，第一步提交前先把它们移出索引"
            "（git restore --staged <路径>）。")
    specs = []
    for x, a in zip(receipt["actions"], actions):
        if x.get("status") == "done" and a["op"] == "move_dir":
            specs += [a["src"], a["dst"]]
        elif x.get("status") == "done" and a["op"] == "remove_rules_mirror" and any(f["tracked"] for f in a["files"]):
            specs.append(a["dir"])
    if specs and plan.get("git"):
        spec = " ".join(f'"{s}"' for s in specs)
        out("提交建议（分两步，保住 git log --follow）：")
        out(f"  1) 核对索引里只有迁移的改名与删除：git -C \"{project}\" diff --cached --name-status -M -- {spec}")
        out(f"     与整份索引对比：git -C \"{project}\" diff --cached --name-status -M   （两者应相同）")
        out(f"     提交索引：git -C \"{project}\" commit -m \"chore(workframe): 运行态目录迁移（纯改名）\"")
        out("     **不要**写成 git commit -- <路径>：带路径的 commit 取工作树内容，会把随目录带过去的未提交改动一并提交，"
            "改名随之断成删除 ＋ 新增（实测）。")
        out("  2) 其余改动（带过去的未提交修改、.gitignore / CLAUDE.md / AGENTS.md / skills 的改写）用 git status 看，自行分批提交。")
    # 链接提醒不挂在提交建议那块里：续做那次若只剩改写 / 候选文件，提交建议整块不打印，而链接此刻照样没被忽略
    for ln in _skills_link_untracked_note(project, plan.get("git")):
        out(ln)
    if unfinished:
        for ln in unfinished:
            out(ln)
        return EXIT_PARTIAL
    out("完成后重开会话。")
    return 0
