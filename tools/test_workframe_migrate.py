#!/usr/bin/env python3
"""`workframe_migrate.py`（`workframe-door --migrate`）的常驻单测。爆炸半径档 4：它删文件、搬目录、改写受保护资产，
判错时迁移日 exit 0 而状态劈成两半、记忆注入为空。全部在临时 fixture 上跑，fixture 由本文件现造。

守的形态，各配自己的失败方式：
1. **plan 零写入**：除 `logs/workframe-migrate/plan.json` 与它的父目录外，文件集、目录集、`.git/index` 字节、
   各类 `_located_rel`（这一类此刻实际在哪）前后全同；连跑两次除 `generated_at` 外逐字节相同。
2. **apply 正路**：每类新目录的逐文件 sha256 == apply 前旧目录；旧路径不在；doctor `legacy_state_dir` 不报 error；
   `git check-ignore`（运行态文件被忽略、`memory-index.json` 不被忽略）；`.claude/skills` 是链接、`git status` 不出现它；
   rollback-index 前缀换新；CLAUDE.md 加了导入且 doctor 判「已导入」；纪律镜像目录没了、`rules/local` 字节不变；
   候选文件在回执目录里且 doctor 读候选判「无旧副本」；apply 后再出 plan 无搬迁条目；**空操作对照**：同一组断言
   对一个没执行任何动作的 fixture 必须报出失败。
3. **冲突与续做**：新旧并存 ⇒ 该类不动；plan 之后建出空目标目录 ⇒ 拒、零改动；（Windows）记忆目录里持句柄 ⇒ 停在
   state 已迁、memory 未迁，回执 failed；释放后重出 plan 只剩未完成条目，apply 收敛。
4. **防误跑**：缺 `--confirm` / 码不符 / plan 重出且动作不同 / plan 条目被篡改 ⇒ 拒（7）、零写入；越界动作 ⇒
   `validate_actions` 报出；`--apply` 不带 `--migrate`、`--migrate --apply` 不带 `--project` ⇒ 参数错（2）、零写入。
   **提示格**：`stdin_is_console` 注入 False 或环境有标记变量 ⇒ 提示出现且照常执行；注入 True 且只有
   `CODEX_HOME` / `CLAUDE_CODE_GIT_BASH_PATH` / `WT_SESSION` ⇒ 不出现提示。
5. **会话信号**：另一进程持锁 ⇒ 警告；状态目录刚写过 ⇒ 警告；探锁不建任何目录。
6. **边界**：零跟踪文件的目录走 rename；目标父目录不存在先建；有未暂存删除 ⇒ 该条 blocked；CRLF ＋ BOM 的
   `.gitignore` 改后行尾与 BOM 不变；带 marker 的 `.gitignore` 重写 marker 之间；非 git 仓；
   `.gitignore` 有未提交改动 ⇒ G 与依赖它的 state / skills 暂缓；要改写的文件有未提交改动 ⇒ 跳过。
7. **建链接失败后续做**：重出 plan 含建链接、`.gitignore` 按「将有链接」求值，续做后链接在、忽略行在。
8. **项目嵌在上层仓的子目录里**：脏的 CLAUDE.md 不改写、X-rules「会丢」计数正确、apply 照常完成。
9. **被搬目录里有已暂存改动** ⇒ 该类 blocked 并列路径；plan 之后才暂存 ⇒ apply 拒、零改动；暂存恰好发生在
   重算之后 ⇒ 前置检查拒、零改动；发生在前置检查之后 ⇒ 搬那个目录的一步失败、目录与暂存原样。
10. **报告项**：「按 `.claude/skills/` 圈文件的模块清单」只认 `code_paths` 列表项；`.gitignore` 整目录忽略新运行态根 ⇒ 提示。
11. **文本改写的前缀边界**：`~/…`、`$HOME/…`、`../别的项目/…`、绝对路径、URL、模板占位里的同段字面不换。
12. **打印出来的逆操作原样执行**（Windows 上 PowerShell、POSIX 上 sh，逐行、每行后查退出状态）⇒ 逐文件 sha256
    回到 apply 前，差异只剩镜像目录里未提交 / 未跟踪的那两份。三种夹具：仓内 `core.autocrlf=false` ＋ LF 文件；
    `core.autocrlf=true` ＋ LF 文件（git 找回要带 false）；`core.autocrlf=true` ＋ LF 与 CRLF 混着（分两条命令）。
13. **没做完**：其余照做、退出码 8、不打印「完成后重开会话」，收尾块按类分措辞——记忆目录 blocked / 冲突 ⇒「未迁完」
    ＋「框架只读新位置」「处理完之前别开」；导入加不上 ⇒「没做完」、不说「框架只读新位置」；要改写的文件有未提交改动
    （整份跳过 / 部分跳过）⇒ apply 摘要印出跳过的条目、块里列「跳过改写」、不说「框架只读新位置」；同时中途停下 ⇒ 块照样打印。
14. **改写残留**：`$CLAUDE_PROJECT_DIR/` 一族照换（只含这种写法的文件进改写集、不进报告项）；改写之后仍留着旧字面的行
    （占位写法之类）列进报告项。
15. **模块清单解析响亮失效**：取材里函数改名 / 函数体依赖别的名字 ⇒ plan 不崩、出「算不出来」报告项。
16. **镜像目录里 git 找回还原不了原字节的干净文件**（`.gitattributes` 强制 `eol=crlf`、工作树 LF）⇒ plan 的 X-rules
    「会丢」计数含它并逐份点名；apply 的逆操作说明再点名；没有这种文件时不出现这句。
17. **中途停下的收尾块**：（Windows）持句柄停在 R-memory ⇒「未迁完：R-memory——停在这一步」、其后没执行的 R-archive
    也列、说「框架只读新位置」、已完成的 S-skills / G-gitignore / R-state 不列；停在 L-literals（其后只剩不涉及运行态目录
    位置的）⇒「没做完」、不说「框架只读新位置」；全部执行完才发现旧路径仍在 ⇒ 那一类「未迁完」。
18. **跳过改写的 skills md 已随目录搬到中立目录** ⇒ 收尾块给搬迁后的路径（未跟踪、有未提交改动两种）。
19. **git status 里的 skills 链接**：链接自身与链接**下的文件**在两个平台都不许以未跟踪出现（忽略行写无尾斜杠的
    `.claude/skills`，两平台都盖得住）。判据是 `_untracked_link_problems`，另有一格拿合成输入自检；apply 后那一格
    （`post_apply_problems`）另用两条与平台无关的 git 问句判——**Windows 上恒过**（junction 被任一写法盖住），
    忽略写法退回带尾斜杠时只在 POSIX 上红。
20. **链接没被忽略时**（按 `git check-ignore --no-index` 判，不按平台判）两步提交建议多一句「别提交这一行链接」；
    新写法下正常迁移不出现这一句。
21. **`_pending_after_stop` 的两个来源**：回执里那一步的 op / item 与 plan 动作表对不上时，只按回执报、不顺着索引往后列；
    回执那条 log 缺 `item`（条目 id 成 `?`）⇒ 收尾块归「未迁完」一侧（分不清时多拦）。
22. **续做只剩改写 / 候选文件时的链接提醒**：链接没被忽略 ⇒ 没有两步提交建议也照样提醒「别提交它」。
23. **旧目录只在旧位置时迁移工具看的是旧目录**（运行期路径恒指新位置，迁移工具不能经它看）：state 被暂存改动挡住、
    memory 照搬 ⇒ R-index 仍出、`.gitignore` 的 managed 段按旧状态目录写；状态目录非标准内容与记忆目录非出厂子目录的
    报告项从旧目录报出；只有旧状态目录刚写过 ⇒ 会话信号报出。各配 `_located_rel` 改回运行期路径的突变对照。
24. **迁移的入口检查（doctor `legacy_state_dir`）**：按 kind 分「只有旧 / 新旧并存」两种 error、各带自己的出路关键词；
    只有新 / 都没有 ⇒ 不报；`memory` 只有 CC `memory: project` 形态（非出厂子目录）或空目录 ⇒ 不报 error、给 info；
    出厂角色目录下有 `MEMORY.md`（没有 `shared/`）⇒ 算旧布局。顺序钉在消费方：`run_all(…, "install")` 首位、
    首会话验收的摘要文本里它先于 `skeleton`；旧目录在场时 `skeleton` / `setup_state` / `gitignore` 的修法不再指
    `project_scaffold`、改指这一项，旧目录不在时照旧指 `project_scaffold`（对照，证明改指不是恒真）。旧位置是同名文件 /
    悬空链接也算旧布局（悬空链接格要本机能建符号链接，建不了如实跳过）。没迁移的项目上 runtime 组「不存在」几条不说「新项目」。
26. **旧记忆目录里的 CC 原生 `memory: project` 目录**（判据复用 doctor `_legacy_memory_is_framework`）：只有 CC 子目录 /
    空目录 ⇒ 不搬、R-memory 列一行说明、不出 move_dir；新目录在也不判冲突；apply 后 CC 目录逐字节原地、退出码 0、
    doctor 不报 error；纯框架形态（`shared/` 或只有出厂角色的 notes.md）照搬；混合态整个目录照搬、报告项列出非出厂
    子目录并写明挪回。
25. **auto-memory 里写着旧记忆目录的 scope 指针**（迁移工具只列不改）：doctor `auto_memory` 单列一条 warn、写明迁移后手改，
    不混进「死指针」那条、也不再打「目标齐全」ok；旧位置上不是框架 scope 的指针（CC 原生 `memory: project`）不单列、
    照旧按存在性判；新路径的指针照旧按存在性判死指针 / 齐全。
27. **AGENTS.md「这个项目是什么」只剩占位的提示**：plan 报告项三种状态（本次将建出 / 已是占位 / 不在且本次不建）各恰一条、
    已填不出、判定抛异常只降成「没判成」而 plan 照出；报告项不进 payload（有它没它确认码相同）；apply 收尾按盘面重判再说一次、
    在「完成后重开会话」之前；本该建、中途停下没建成时收尾说「没建成」，不说「本次 apply 不建它」。
28. **W3 候选不删 1.0.0 布局里夹在框架块后面的项目约束**：按「工种知识的落点判据」小节最后那句 1.0.0 原句再切一刀；原句被
    改写 / 只出现在围栏里时切不开、退回整块判（钉住能力边界）；plan 条目提醒对比时手工保留。
"""

import atexit
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "plugins" / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.dont_write_bytecode = True

# ---- 封闭：本套件不起调用方机器上的 codex、不碰调用方的 Codex home ----
# 与 `test_workframe_door.py` 开头那段同一个做法（理由写在那里）：import 被测模块之前强制覆盖 `WF_CODEX_EXE`（指向不存在的
# 路径 ⇒ `find_codex_exe` 返回 None、不回退到 PATH）与 `CODEX_HOME`（本进程专属的临时目录）。`run_door` 直接调 `wd.main`、
# 没打桩，挡在真 codex 前面的只有门的参数守卫——守卫一回归，走到的是「未探测到 codex」而不是调用方的 codex。
_SEAL = Path(tempfile.mkdtemp(prefix="wf-migrate-test-seal-"))
(_SEAL / "codex-home").mkdir()
os.environ["CODEX_HOME"] = str(_SEAL / "codex-home")
os.environ["WF_CODEX_EXE"] = str(_SEAL / "no-such-codex" / "codex.exe")
atexit.register(shutil.rmtree, str(_SEAL), True)

import _harness as H  # noqa: E402
import _state_io as sio  # noqa: E402
import workframe_doctor as doc  # noqa: E402
import workframe_migrate as wm  # noqa: E402
import workframe_door as wd  # noqa: E402

FAILURES = []
KINDS = wm.KINDS
MIRROR = "/".join(doc.RULES_MIRROR_PARTS)
LOCAL = "/".join(doc.RULES_MIRROR_PARTS[:2] + ("local",))
OLD = {k: sio.legacy_rel(k) for k in KINDS}
NEW = {k: sio.new_rel(k) for k in KINDS}


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'FAIL'}] {label}" + ("" if ok else f": got={got!r} want={want!r}"))
    if not ok:
        FAILURES.append(label)


def rm(d):
    def _onerr(f, p, e):
        try:
            os.chmod(p, stat.S_IWRITE)
            f(p)
        except Exception:
            pass
    shutil.rmtree(d, onerror=_onerr)


def git(p, *args):
    r = subprocess.run(["git", "-C", str(p), "-c", "core.longpaths=true", "-c", "core.autocrlf=false", *args],
                       capture_output=True)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def git_repo(p, *args):
    """不带 `-c core.autocrlf=false` 的 git：按仓内配置走（夹具建仓用）。"""
    r = subprocess.run(["git", "-C", str(p), "-c", "core.longpaths=true", *args], capture_output=True)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def put(p, text, raw=False):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text if raw else text.encode("utf-8"))


CLAUDE_MD = (
    "# demo\n\n## 项目目标\n\n做点什么\n\n## 角色体系\n\n### 通用角色（来自 core plugin）\n\n"
    "| 角色 | 职责 |\n|---|---|\n| @pm | 需求 |\n| @dev | 代码 |\n\n### 项目独有 skill\n\n- demo\n\n"
    "## 框架相关\n\n记忆见 `{mem}/dev/MEMORY.md`\n"
)


class Proj:
    """旧布局 fixture：state（跟踪 sidecar，其余被忽略）/ memory / archive / skills 真目录 / 纪律镜像（两份跟踪、
    一份改过、一份未跟踪）/ rules local 引用镜像文件名 / CLAUDE.md 无导入且带旧副本与旧路径。"""

    def __init__(self, use_git=True, gitignore=None, mirror=True, nest=False, leaf="proj", autocrlf="false",
                 mirror_eol="lf", gitattributes=None):
        self.use_git, self.gitignore, self.mirror, self.nest, self.leaf = use_git, gitignore, mirror, nest, leaf
        self.autocrlf, self.mirror_eol, self.gitattributes = autocrlf, mirror_eol, gitattributes

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wf-mig-"))
        # nest=True：项目嵌在上层仓的子目录里（git 仓根是 repo/，项目根是 repo/apps/proj/）
        P = self.root = self.tmp / "repo" / "apps" / self.leaf if self.nest else self.tmp / self.leaf
        top = self.tmp / "repo" if self.nest else P
        P.mkdir(parents=True)
        put(P / ".workframe-config.json", json.dumps({"project_type": "product-work", "role_profile": "software-team"}))
        put(P / "CLAUDE.md", CLAUDE_MD.format(mem=OLD["memory"]))
        put(P / ".gitignore", self.gitignore if self.gitignore is not None else
            f"{OLD['state']}/*\n!{OLD['state']}/memory-index.json\nlogs/\n", raw=isinstance(self.gitignore, bytes))
        put(P / OLD["state"] / "activity-state.json", json.dumps({"session_counter": 5}))
        put(P / OLD["state"] / "events.jsonl", '{"type":"x"}\n')
        put(P / OLD["state"] / "memory-index.json", '{"entries": {}}\n')
        put(P / OLD["state"] / "rollback-index.json",
            json.dumps({"entries": [{"targets": [f"{OLD['memory']}/shared/notes.md"],
                                     "backups": [f"{OLD['state']}/backups/RB-1/notes.md"]}]}, indent=2) + "\n")
        put(P / OLD["state"] / "backups" / "RB-1" / "notes.md", "bak\n")
        put(P / OLD["memory"] / "shared" / "MEMORY.md", "# shared\n")
        put(P / OLD["memory"] / "dev" / "notes.md", "# dev notes\n")
        put(P / OLD["archive"] / "old.md", "archived\n")
        put(P / ".claude" / "skills" / "demo" / "SKILL.md", f"读 `{OLD['state']}/plugin-root.txt`\n")
        if self.mirror:
            put(P / MIRROR / "alpha.md", "a\n")
            put(P / MIRROR / "beta.md", "b\n")
            if self.mirror_eol == "mixed":
                put(P / MIRROR / "delta.md", b"d\r\ne\r\n", raw=True)      # 工作树 CRLF、干净的已提交文件
            put(P / LOCAL / "ext.md", "---\nextends: workframe/core/alpha.md\n---\n保留这一行\n| `beta.md` | x |\n")
        put(P / "logs" / ".gitkeep", "")
        if self.gitattributes is not None:
            put(P / ".gitattributes", self.gitattributes)
        if self.use_git:
            # 仓内显式设 core.autocrlf（缺省 false），不依赖本机全局值；这几条 git 不带 `-c core.autocrlf=false`，
            # 让 autocrlf=true 的夹具按 true 入库（工作树 LF / CRLF 两种形态都是真实项目里的样子）
            git_repo(top, "init", "-q")
            git_repo(top, "config", "core.autocrlf", self.autocrlf)
            git_repo(top, "config", "user.email", "t@example.invalid")
            git_repo(top, "config", "user.name", "t")
            git_repo(top, "add", "-A")
            git_repo(top, "commit", "-q", "-m", "init")
            if self.mirror:
                put(P / MIRROR / "beta.md", "b-dirty\n")
                put(P / MIRROR / "gamma.md", "untracked\n")
        return P

    def __exit__(self, *a):
        rm(self.tmp)


def tree(P, skip_out=True):
    files, dirs = {}, set()
    for dirpath, dirnames, filenames in os.walk(P):
        here = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d != ".git" and not H.is_dir_link(here / d)]
        rel_dir = here.relative_to(P).as_posix()
        if skip_out and rel_dir.startswith("logs/workframe-migrate"):
            continue
        dirs.add(rel_dir)
        for f in filenames:
            rel = (here / f).relative_to(P).as_posix()
            files[rel] = (here / f).read_bytes()
    return files, dirs


def quiet(fn, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rv = fn(*a, **kw)
    return rv, buf.getvalue()


def plan_for(P):
    plan = wm.build_plan(P)
    wm.write_plan(P, plan)
    return plan


def manifest_under(P, rel):
    d = P / rel
    return {f: wm._sha((d / f).read_bytes()) for f in wm._walk_files(d)} if d.is_dir() else None


def untracked_of(status_text):
    return [ln[3:] for ln in status_text.splitlines() if ln.startswith("??")]


def _untracked_link_problems(untracked):
    """`git status` 的未跟踪项里，与项目 skills 链接有关、**不该出现**的那些。

    链接**下的文件**（`.claude/skills/<任何路径>`）与**链接自身**（`.claude/skills`）在两个平台都不该出现——出现了说明
    忽略条目没写上（或写成了带尾斜杠、在 POSIX 上盖不住符号链接的旧写法），或者那里此刻不是链接而是真目录。"""
    cc = H.SKILLS_DIR_CC.as_posix()
    return [u for u in untracked if u == cc or u.startswith(cc + "/")]


def post_apply_problems(P, before, rewrites=()):
    """apply 后应成立的断言；返回不成立的条目（空操作对照要求它非空）。

    `rewrites`：plan 里的 rewrite_file 动作——它们改写的文件不参与「新目录清单 == 旧目录清单」，改为核对
    改写后的 sha256 等于 plan 算出的 post_sha256。"""
    probs = []
    by_new = {a["path"]: a for a in rewrites}
    for k in KINDS:
        if before.get(k) is None:
            continue
        got = manifest_under(P, NEW[k]) or {}
        want = dict(before[k])
        for rel_in_dir in list(want):
            a = by_new.get(f"{NEW[k]}/{rel_in_dir}")
            if a:
                want[rel_in_dir] = a["post_sha256"]
        if got != want:
            probs.append(f"{k} 新目录清单 != apply 前旧目录（改写文件按 plan 的 post_sha256 核）")
        if os.path.lexists(P / OLD[k]):
            probs.append(f"{k} 旧路径仍在")
    legacy_errs = [m for lv, m in doc.check_legacy_state_dir({"paths": doc.Paths(P)}) if lv == "error"]
    if legacy_errs:
        probs.append(f"doctor legacy_state_dir 仍报 error：{legacy_errs[0][:60]}")
    if not H.is_dir_link(P / H.SKILLS_DIR_CC):
        probs.append("skills 链接不在")
    # `--no-index`：memory-index.json 在 fixture 里是已跟踪文件，不带它 `check-ignore` 对已跟踪路径恒答「不忽略」，
    # managed 段丢了 `!…/memory-index.json` 那一行这格也照样绿
    rc, _ = git(P, "check-ignore", "-q", "--no-index", f"{NEW['state']}/events.jsonl")
    if rc != 0:
        probs.append("events.jsonl 未被忽略")
    rc, _ = git(P, "check-ignore", "-q", "--no-index", f"{NEW['state']}/memory-index.json")
    if rc == 0:
        probs.append("memory-index.json 被忽略")
    # (g) 链接被忽略：两条与平台无关的 git 问句（porcelain 子串在 `.claude/` 下无已跟踪文件时会折叠成 `?? .claude/`，
    # 看不见链接这一行）。**Windows 上恒过**——junction 被带不带尾斜杠的写法都盖住；只在 POSIX 上分得开
    rc, _ = git(P, "check-ignore", "-q", "--no-index", "--", H.SKILLS_DIR_CC.as_posix())
    if rc != 0:
        probs.append("skills 链接本身未被忽略（check-ignore）")
    _rc, others = git(P, "ls-files", "-o", "--exclude-standard", "--", ".claude")
    leaked = [ln for ln in others.splitlines() if ln.startswith(H.SKILLS_DIR_CC.as_posix())]
    if leaked:
        probs.append(f"ls-files -o 出现 skills 链接相关未跟踪项：{leaked}")
    if not doc.claude_md_imports_agents_md((P / "CLAUDE.md").read_text(encoding="utf-8")):
        probs.append("CLAUDE.md 未导入 AGENTS.md")
    if os.path.lexists(P / MIRROR):
        probs.append("纪律镜像目录仍在")
    ri = P / NEW["state"] / "rollback-index.json"
    if not ri.is_file() or OLD["memory"] in ri.read_text(encoding="utf-8"):
        probs.append("rollback-index 未换前缀")
    return probs


# ---- 1. plan 零写入 ----

def test_plan_zero_write():
    print("[plan] 零写入 ＋ 连跑两次逐字节相同")
    with Proj() as P:
        # 让 index 里的 stat 信息过期（真实项目的常态）：否则 `git status` 本来就无须刷新 index，
        # plan 里某条 git 读命令漏了 GIT_OPTIONAL_LOCKS=0 时下面那格照样绿
        t = time.time() + 5
        os.utime(P / "CLAUDE.md", (t, t))
        f0, d0 = tree(P)
        idx0 = (P / ".git" / "index").read_bytes()
        rr0 = [wm._located_rel(P, k) for k in KINDS]
        p1 = plan_for(P)
        raw1 = (P / "logs" / "workframe-migrate" / "plan.json").read_bytes()
        p2 = plan_for(P)
        raw2 = (P / "logs" / "workframe-migrate" / "plan.json").read_bytes()
        f1, d1 = tree(P)
        check("文件集与字节不变（除 plan.json）", f1 == f0, True)
        check("目录集不变（除 plan 目录）", d1 == d0, True)
        check(".git/index 字节不变", (P / ".git" / "index").read_bytes() == idx0, True)
        check("_located_rel 不变（plan 没建出任何运行态目录）", [wm._located_rel(P, k) for k in KINDS], rr0)
        strip = lambda b: json.loads(b)  # noqa: E731
        a, b = strip(raw1), strip(raw2)
        a.pop("generated_at"), b.pop("generated_at")
        check("连跑两次除 generated_at 外相同", a == b, True)
        check("确认码稳定", p1["code"], p2["code"])
        ops = [(x["op"], x["item"]) for x in p1["payload"]["actions"]]
        check("动作顺序", ops, [("move_dir", "S-skills"), ("make_skills_link", "S-skills"),
                              ("write_gitignore", "G-gitignore"), ("move_dir", "R-state"), ("move_dir", "R-memory"),
                              ("move_dir", "R-archive"), ("rewrite_file", "R-index"),
                              ("create_agents_md", "I-agents-import"), ("rewrite_file", "L-literals"),
                              ("rewrite_file", "L-literals"), ("write_candidate", "W-w3-residue"),
                              ("write_candidate", "W-rules-local"), ("remove_rules_mirror", "X-rules")])
        check("封闭枚举自检通过", wm.validate_actions(p1["payload"]["actions"]), [])


# ---- 2. apply 正路 ＋ 空操作对照 ----

def test_apply_happy():
    print("[apply] 正路 ＋ 空操作对照")
    with Proj() as P:
        before = {k: manifest_under(P, OLD[k]) for k in KINDS}
        local0 = (P / LOCAL / "ext.md").read_bytes()
        plan = plan_for(P)
        rewrites = [a for a in plan["payload"]["actions"] if a["op"] == "rewrite_file"]
        check("空操作对照：未执行时断言报出失败", len(post_apply_problems(P, before, rewrites)) > 0, True)
        check("plan.json 写明 apply 由用户本人执行、模型不得代传",
              "模型不得" in json.loads((P / "logs" / "workframe-migrate" / "plan.json").read_bytes()).get("apply_by", ""),
              True)
        check("rules local 候选的摘要说明整行去掉、自有内容手工保留",
              any(i["id"] == "W-rules-local" and "整行去掉" in i["title"] and "手工保留" in i["title"] for i in plan["items"]),
              True)
        receipt, _out = quiet(wm.execute_plan, plan, P)
        check("回执 ok", receipt["status"], "ok")
        check("进度行不出现 None（每种动作都报出它动的是什么）",
              [ln for ln in _out.splitlines() if "✓" in ln and ln.rstrip().endswith("None")], [])
        check("apply 后断言全过", post_apply_problems(P, before, rewrites), [])
        check("rules local 字节不变", (P / LOCAL / "ext.md").read_bytes(), local0)
        rdir = Path(receipt["receipt_dir"])
        cand = rdir / "candidates" / "CLAUDE.md"
        check("CLAUDE.md 候选存在", cand.is_file(), True)
        check("doctor 读候选：无旧副本", doc._w3_residue_blocks(cand.read_text(encoding="utf-8")) if cand.is_file() else None, [])
        check("CLAUDE.md 本体仍有旧副本（不直接改）", bool(doc._w3_residue_blocks((P / "CLAUDE.md").read_text(encoding="utf-8"))), True)
        lc = rdir / "candidates" / "rules-local" / "ext.md"
        check("rules local 候选去掉两行", lc.read_text(encoding="utf-8") if lc.is_file() else None, "---\n---\n保留这一行\n")
        check("CLAUDE.md 旧路径已换新", OLD["memory"] in (P / "CLAUDE.md").read_text(encoding="utf-8"), False)
        check("skills md 经链接读到新路径", NEW["state"] in (P / H.SKILLS_DIR_CC / "demo" / "SKILL.md").read_text(encoding="utf-8"), True)
        check("preimage 存了 CLAUDE.md 原件", (rdir / "preimage" / "CLAUDE.md").read_bytes().decode("utf-8"),
              CLAUDE_MD.format(mem=OLD["memory"]))
        check("AGENTS.md 建出", (P / "AGENTS.md").is_file(), True)
        again = wm.compute_plan(P)
        check("apply 后再出 plan 无搬迁条目", [a for a in again["payload"]["actions"] if a["op"] == "move_dir"], [])
        _rc, cached = git(P, "diff", "--cached", "--name-status", "-M")
        check("索引里 memory 是纯改名", all(ln.startswith(("R100", "D")) for ln in cached.splitlines() if ln), True)


# ---- 3. 冲突与续做 ----

def test_conflict_and_resume():
    print("[conflict] 新旧并存 / 空目标目录 / 中途失败续做")
    with Proj() as P:
        (P / NEW["memory"]).mkdir(parents=True)
        plan = wm.compute_plan(P)
        check("新旧并存 ⇒ R-conflict", any(i["id"] == "R-conflict" and i["data"].get("kind") == "memory" for i in plan["items"]), True)
        check("新旧并存 ⇒ 该类无搬迁动作", [a for a in plan["payload"]["actions"] if a.get("item") == "R-memory"], [])
    with Proj() as P:
        plan = plan_for(P)
        (P / NEW["state"]).mkdir(parents=True)
        f0, d0 = tree(P)
        try:
            quiet(wm.execute_plan, plan, P)
            check("plan 后建出空目标目录 ⇒ 拒", "未拒", "拒")
        except wm.MigrateRefused:
            check("plan 后建出空目标目录 ⇒ 拒", "拒", "拒")
        f1, d1 = tree(P)
        check("拒后零改动（文件）", f1 == f0, True)
        check("拒后零改动（目录）", d1 == d0, True)
    with Proj(mirror=False) as P:
        # apply 开始时的重算看不见「执行到一半才出现的空目标目录」：在 R-memory 动手前一刻建出它
        plan = plan_for(P)
        real = wm._do_action

        def racing(project, a, *rest):
            if a["op"] == "move_dir" and a["item"] == "R-memory":
                (project / a["dst"]).mkdir(parents=True)
            return real(project, a, *rest)

        with mock.patch.object(wm, "_do_action", racing):
            receipt, _ = quiet(wm.execute_plan, plan, P)
        last = receipt["actions"][-1]
        check("执行途中目标目录出现 ⇒ 该步失败", (last["item"], last["status"], receipt["status"]),
              ("R-memory", "failed", "failed"))
        check("… 没有嵌套进去", os.path.lexists(P / NEW["memory"] / Path(OLD["memory"]).name), False)
        check("… 旧记忆目录原样在", (P / OLD["memory"] / "dev" / "notes.md").is_file(), True)
    if os.name != "nt":
        print("  [skip] 持句柄阻断搬迁只在 Windows 上成立（POSIX 的 rename 不受打开的文件影响）")
        return
    with Proj() as P:
        plan = plan_for(P)
        fh = open(P / OLD["memory"] / "dev" / "notes.md", "rb")
        try:
            receipt, _ = quiet(wm.execute_plan, plan, P)
        finally:
            fh.close()
        check("持句柄 ⇒ 回执 failed", receipt["status"], "failed")
        stopped = receipt["actions"][-1]
        check("停在 R-memory", (stopped["op"], stopped["item"], stopped["status"]), ("move_dir", "R-memory", "failed"))
        check("state 已迁", (P / NEW["state"]).is_dir() and not os.path.lexists(P / OLD["state"]), True)
        check("memory 未迁", (P / OLD["memory"]).is_dir() and not os.path.lexists(P / NEW["memory"]), True)
        again = plan_for(P)
        ops = [(a["op"], a["item"]) for a in again["payload"]["actions"]]
        check("重出 plan 不含已完成条目", any(i in ("S-skills", "R-state", "G-gitignore") for _o, i in ops), False)
        check("重出 plan 含 R-memory", ("move_dir", "R-memory") in ops, True)
        check("重出 plan 仍改写已 git mv 过去的 skills md（索引里的纯改名不算脏）",
              any(a["op"] == "rewrite_file" and a["path"].startswith(H.SKILLS_DIR_NEUTRAL.as_posix() + "/")
                  for a in again["payload"]["actions"]), True)
        receipt2, _ = quiet(wm.execute_plan, again, P)
        check("续做 apply 收敛", receipt2["status"], "ok")
        check("续做后旧路径全无", [k for k in KINDS if os.path.lexists(P / OLD[k])], [])
        check("续做后 skills md 里是新路径",
              NEW["state"] in (P / H.SKILLS_DIR_NEUTRAL / "demo" / "SKILL.md").read_text(encoding="utf-8"), True)


# ---- 4. 防误跑 ＋ 提示格 ----

def run_door(argv):
    buf = io.StringIO()
    code = None
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            code = wd.main(argv)
        except SystemExit as e:
            code = e.code
    return code, buf.getvalue()


def test_guards():
    print("[guards] 确认码 / 重算比对 / 篡改 / 越界 / 参数")
    with Proj() as P:
        plan = plan_for(P)
        f0, d0 = tree(P, skip_out=False)
        code, out = run_door(["--migrate", "--apply", "--project", str(P)])
        check("缺 --confirm ⇒ 7", code, 7)
        code, out = run_door(["--migrate", "--apply", "--confirm", "00000000", "--project", str(P)])
        check("码不符 ⇒ 7", code, 7)
        check("码不符的拒绝文案不回显正确的码", plan["code"] in out, False)
        check("码不符零写入", tree(P, skip_out=False) == (f0, d0), True)
        code, out = run_door(["--migrate", "--confirm", plan["code"], "--project", str(P)])
        check("--confirm 不带 --apply ⇒ 参数错 2（不静默重出 plan）", code, 2)
        check("--confirm 不带 --apply 零写入（plan.json 也没重写）", tree(P, skip_out=False) == (f0, d0), True)
        code, _ = run_door(["--apply", "--confirm", plan["code"], "--project", str(P)])
        check("--apply 不带 --migrate ⇒ 参数错 2", code, 2)
        code, _ = run_door(["--confirm", plan["code"]])
        check("--confirm 不带 --migrate ⇒ 参数错 2", code, 2)
        code, _ = run_door(["--migrate", "--apply", "--confirm", plan["code"]])
        check("--migrate --apply 不带 --project ⇒ 参数错 2", code, 2)
        check("参数错零写入", tree(P, skip_out=False) == (f0, d0), True)
        # plan 被篡改（加一条越界动作并重算码）
        bad = json.loads(json.dumps(plan))
        bad["payload"]["actions"].append({"op": "move_dir", "item": "R-state", "src": "projects", "dst": "x",
                                          "method": "rename", "tracked": []})
        bad["code"] = wm.confirm_code(bad["payload"])
        wm.write_plan(P, bad)
        f0, d0 = tree(P)
        code, out = run_door(["--migrate", "--apply", "--confirm", bad["code"], "--project", str(P)])
        check("篡改 plan 并重算码 ⇒ 7", code, 7)
        code, out = run_door(["--migrate", "--apply", "--confirm", plan["code"], "--project", str(P)])
        check("篡改 plan、给原码 ⇒ 7（重算比对）", code, 7)
        check("篡改拒后零改动", tree(P) == (f0, d0), True)
        check("越界动作被枚举抓到", len(wm.validate_actions(bad["payload"]["actions"])) > 0, True)
        for label, act in (
                ("改写越界路径", {"op": "rewrite_file", "item": "L-literals", "path": "projects/board.yaml",
                                  "plan_path": "projects/board.yaml", "kinds": [], "transforms": ["literals"]}),
                ("删除越界", {"op": "remove_rules_mirror", "item": "X-rules", "dir": MIRROR,
                              "files": [{"path": "CLAUDE.md", "sha256": "", "tracked": True}]}),
                ("爬出项目", {"op": "write_candidate", "item": "W-rules-local", "source": LOCAL + "/../../CLAUDE.md",
                              "name": "rules-local/x.md", "drop_lines": []}),
                ("未知类型", {"op": "shell", "item": "X"})):
            check(f"越界：{label}", len(wm.validate_actions([act])) > 0, True)
        # plan 重出且动作不同
        wm.write_plan(P, plan)
        (P / OLD["archive"]).rename(P / "archive-moved-away")
        code, out = run_door(["--migrate", "--apply", "--confirm", plan["code"], "--project", str(P)])
        check("plan 之后项目变了、给旧码 ⇒ 7", code, 7)
        plan2 = plan_for(P)
        check("重出 plan 码不同", plan2["code"] != plan["code"], True)
        code, out = run_door(["--migrate", "--apply", "--confirm", plan["code"], "--project", str(P)])
        check("plan 已重出且动作不同、给旧码 ⇒ 7", code, 7)


def test_terminal_hint():
    print("[hint] 非控制台 / 标记命中 ⇒ 提示且照常执行；真控制台无标记 ⇒ 无提示")
    marker = "当前不在普通终端里"
    quiet_env = {k: v for k, v in os.environ.items() if k not in H.AGENT_ENV_MARKERS}
    for label, console, extra, want_hint in (
            ("非控制台", False, {}, True),
            ("标记命中", True, {"CLAUDECODE": "1"}, True),
            ("Codex 标记命中", True, {"CODEX_THREAD_ID": "t"}, True),
            ("真控制台、只有常驻变量", True, {"CODEX_HOME": "x", "CLAUDE_CODE_GIT_BASH_PATH": "y", "WT_SESSION": "z"}, False)):
        with Proj(mirror=False) as P:
            plan = plan_for(P)
            env = dict(quiet_env, **extra)
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(H, "stdin_is_console", lambda: console):
                code, out = run_door(["--migrate", "--apply", "--confirm", plan["code"], "--project", str(P)])
            check(f"{label}：提示{'出现' if want_hint else '不出现'}", marker in out, want_hint)
            check(f"{label}：照常执行", (code, os.path.lexists(P / OLD["state"])), (0, False))


# ---- 5. 会话信号 ----

def test_session_signals():
    print("[session] 持锁 / 近期写入 / 探锁不建目录")
    with Proj(mirror=False) as P:
        state = P / OLD["state"]
        lock = state / "events.jsonl.lock"
        lock.write_bytes(b"")
        old = time.time() - 3600
        for f in state.iterdir():
            if f.is_file():
                os.utime(f, (old, old))
        check("无信号", wm.session_signals(P), [])
        holder = subprocess.Popen([sys.executable, "-c",
                                   "import sys,time; sys.path.insert(0, sys.argv[1]); import _state_io as s\n"
                                   "with s.FileLock(sys.argv[2]):\n print('held', flush=True); time.sleep(8)",
                                   str(SCRIPTS), str(lock)], stdout=subprocess.PIPE, text=True)
        try:
            holder.stdout.readline()
            msgs = wm.session_signals(P)
        finally:
            holder.kill()
            holder.wait()
        check("另一进程持锁 ⇒ 警告", any("持有" in m for m in msgs), True)
        os.utime(lock, (old, old))
        (state / "activity-state.json").write_bytes(b'{"session_counter": 6}')
        check("状态目录刚写过 ⇒ 警告", any("秒前" in m for m in wm.session_signals(P)), True)
    with Proj(mirror=False) as P:
        rm(P / OLD["state"])
        _f0, d0 = tree(P)
        wm.session_signals(P)
        check("状态目录不在时探锁不建目录", tree(P)[1], d0)


# ---- 6. 边界 ----

def test_boundaries():
    print("[boundary] 零跟踪 / 父目录 / 未暂存删除 / CRLF+BOM / marker / 非 git / 脏文件")
    gi = (b"\xef\xbb\xbf" + f"{OLD['state']}/\r\nlogs/\r\n".encode())
    with Proj(gitignore=gi, mirror=False) as P:
        plan = plan_for(P)
        st = next(a for a in plan["payload"]["actions"] if a.get("item") == "R-state")
        check("整目录被忽略、零跟踪文件 ⇒ rename", st["method"], "rename")
        check("目标父目录原本不存在", os.path.lexists(P / NEW["state"].split("/")[0]), False)
        receipt, _ = quiet(wm.execute_plan, plan, P)
        check("apply ok", receipt["status"], "ok")
        raw = (P / ".gitignore").read_bytes()
        check("BOM 保留", raw.startswith(b"\xef\xbb\xbf"), True)
        check("无裸 LF（行尾仍是 CRLF）", raw.count(b"\n") == raw.count(b"\r\n"), True)
        check("原两行原样在前", raw.startswith(gi), True)
    marker = (f"logs/\n# === Workframe managed (do not edit between markers) ===\n{OLD['state']}/*\n"
              f"!{OLD['state']}/memory-index.json\n# === End Workframe managed ===\ntail/\n")
    with Proj(gitignore=marker, mirror=False) as P:
        plan = plan_for(P)
        g = next(a for a in plan["payload"]["actions"] if a["op"] == "write_gitignore")
        check("带 marker ⇒ rewrite-block", g["mode"], "rewrite-block")
        quiet(wm.execute_plan, plan, P)
        text = (P / ".gitignore").read_text(encoding="utf-8")
        check("marker 外行不动", text.startswith("logs/\n") and text.endswith("tail/\n"), True)
        check("marker 内换成新布局", f"{NEW['state']}/*" in text and f"{OLD['state']}/*" not in text, True)
    with Proj(mirror=False) as P:
        (P / OLD["memory"] / "dev" / "notes.md").unlink()
        plan = wm.compute_plan(P)
        it = next(i for i in plan["items"] if i["id"] == "R-memory")
        check("有未暂存删除 ⇒ R-memory blocked", it["status"], "blocked")
        check("… 且无 R-memory 动作", [a for a in plan["payload"]["actions"] if a.get("item") == "R-memory"], [])
    with Proj(use_git=False) as P:
        plan = plan_for(P)
        check("非 git 仓 ⇒ 全部 rename", {a["method"] for a in plan["payload"]["actions"] if a["op"] == "move_dir"}, {"rename"})
        receipt, _ = quiet(wm.execute_plan, plan, P)
        check("非 git 仓 apply ok", receipt["status"], "ok")
        check("非 git 仓旧路径全无", [k for k in KINDS if os.path.lexists(P / OLD[k])], [])
    with Proj(mirror=False) as P:
        put(P / ".gitignore", "logs/\n# local edit\n" + (P / ".gitignore").read_text(encoding="utf-8"))
        put(P / "CLAUDE.md", (P / "CLAUDE.md").read_text(encoding="utf-8") + "dirty\n")
        plan = wm.compute_plan(P)
        items = {i["id"]: i for i in plan["items"]}
        items_all = plan["items"]
        check(".gitignore 脏 ⇒ G blocked", items["G-gitignore"]["status"], "blocked")
        check("… R-state 暂缓", next(i for i in items_all if i["id"] == "R-state")["status"], "blocked")
        check("… S-skills 暂缓", items["S-skills"]["status"], "blocked")
        check("… R-memory 照做", next(i for i in items_all if i["id"] == "R-memory")["status"], "planned")
        put(P / ".claude" / "skills" / "fresh" / "SKILL.md", f"新写的 `{OLD['memory']}/x`\n")
        git(P, "add", "--", ".claude/skills/fresh/SKILL.md")
        plan = wm.compute_plan(P)
        items = {i["id"]: i for i in plan["items"]}
        check("新暂存（非改名）的 skills md ⇒ 跳过改写",
              [a for a in plan["payload"]["actions"] if a["op"] == "rewrite_file" and a["path"].endswith("fresh/SKILL.md")], [])
        check("… 且在摘要里说明跳过", any("fresh/SKILL.md" in ln and "跳过" in ln for ln in items["L-literals"]["lines"]), True)
        check("CLAUDE.md 脏 ⇒ 导入跳过", items["I-agents-import"]["status"], "skipped")
        check("CLAUDE.md 脏 ⇒ 无 CLAUDE.md 改写动作",
              [a for a in plan["payload"]["actions"] if a["op"] == "rewrite_file" and a["path"] == "CLAUDE.md"], [])


# ---- 7. 修复轮补的形态 ----

def test_link_fail_resume():
    print("[link-fail] 搬完 skills、建链接失败 ⇒ 续做补上链接与忽略行")
    with Proj(mirror=False) as P:
        plan = plan_for(P)
        with mock.patch.object(wm.ps, "ensure_project_skills_link", lambda *a, **k: False):
            receipt, _ = quiet(wm.execute_plan, plan, P)
        last = receipt["actions"][-1]
        check("建链接失败 ⇒ 停在 make_skills_link", (receipt["status"], last["op"], last["status"]),
              ("failed", "make_skills_link", "failed"))
        again = plan_for(P)
        ops = [(a["op"], a["item"]) for a in again["payload"]["actions"]]
        check("重出 plan 含建链接", ("make_skills_link", "S-skills") in ops, True)
        check("重出 plan 不再搬 skills", ("move_dir", "S-skills") in ops, False)
        g = [a for a in again["payload"]["actions"] if a["op"] == "write_gitignore"]
        check("重出 plan 的 .gitignore 按「将有链接」求值", [x["skills_link"] for x in g], [True])
        receipt2, _ = quiet(wm.execute_plan, again, P)
        check("续做 ok", receipt2["status"], "ok")
        check("续做后链接在", H.is_dir_link(P / H.SKILLS_DIR_CC), True)
        check("续做后 .gitignore 忽略链接（两平台都生效的写法）",
              wm.ps.skills_link_ignore_state((P / ".gitignore").read_text(encoding="utf-8")), "ok")
        _rc, st = git(P, "status", "--porcelain", "-uall")
        cc = H.SKILLS_DIR_CC.as_posix()
        check("git status 不出现链接本身与链接下的未跟踪文件（两平台同判）",
              _untracked_link_problems(untracked_of(st)), [])
        check("判据自检：链接下的文件与链接自身一律算问题（不按平台放行）",
              (_untracked_link_problems([f"{cc}/demo/SKILL.md"]), _untracked_link_problems([cc])),
              ([f"{cc}/demo/SKILL.md"], [cc]))


def test_nested_project():
    print("[nested] 项目嵌在上层仓的子目录里：脏文件判定与 apply")
    with Proj(nest=True) as P:
        put(P / "CLAUDE.md", (P / "CLAUDE.md").read_text(encoding="utf-8") + "用户未提交的改动\n")
        plan = plan_for(P)
        items = {i["id"]: i for i in plan["items"]}
        check("CLAUDE.md 脏 ⇒ 无 CLAUDE.md 改写动作",
              [a for a in plan["payload"]["actions"] if a["op"] == "rewrite_file" and a["path"] == "CLAUDE.md"], [])
        check("CLAUDE.md 脏 ⇒ 导入跳过", items["I-agents-import"]["status"], "skipped")
        check("X-rules 摘要的「会丢」计数 = 有未提交改动的跟踪文件数", "其中 1 份会丢——1 份有未提交改动）" in items["X-rules"]["title"], True)
        check("X-rules 逐份标出脏的那份",
              any(ln.endswith("beta.md（有未提交改动）") for ln in items["X-rules"]["lines"]), True)
        receipt, _ = quiet(wm.execute_plan, plan, P)
        check("嵌套项目 apply ok", receipt["status"], "ok")
        check("嵌套项目旧路径全无", [k for k in KINDS if os.path.lexists(P / OLD[k])], [])
        check("嵌套项目链接在", H.is_dir_link(P / H.SKILLS_DIR_CC), True)
    with Proj(nest=True, mirror=False) as P:
        # 停在 skills 搬完之后：已 git mv 过去的 skills md 在索引里是纯改名，嵌套项目下也不能被当成脏的
        plan = plan_for(P)
        real = wm._do_action

        def stop_at_state(project, a, *rest):
            if a["op"] == "move_dir" and a["item"] == "R-state":
                raise RuntimeError("probe stop")
            return real(project, a, *rest)

        with mock.patch.object(wm, "_do_action", stop_at_state):
            quiet(wm.execute_plan, plan, P)
        again = wm.compute_plan(P)
        check("嵌套项目续做仍改写已 git mv 过去的 skills md",
              any(a["op"] == "rewrite_file" and a["path"].startswith(H.SKILLS_DIR_NEUTRAL.as_posix() + "/")
                  for a in again["payload"]["actions"]), True)


def test_staged_inside():
    print("[staged] 被搬目录里有已暂存改动 ⇒ 该类 blocked、列出路径")
    staged_rel = f"{OLD['memory']}/shared/MEMORY.md"
    with Proj(mirror=False) as P:
        put(P / staged_rel, "# shared\n- 2026-09-17: staged\n")
        git(P, "add", "--", staged_rel)
        plan = wm.compute_plan(P)
        it = next(i for i in plan["items"] if i["id"] == "R-memory")
        check("R-memory blocked", it["status"], "blocked")
        check("… 列出暂存的路径", staged_rel in it["lines"], True)
        check("… 无 R-memory 动作", [a for a in plan["payload"]["actions"] if a.get("item") == "R-memory"], [])
        check("… 其余类照做", ("move_dir", "R-state") in [(a["op"], a["item"]) for a in plan["payload"]["actions"]], True)
    with Proj(mirror=False) as P:
        plan = plan_for(P)                            # plan 时干净
        put(P / staged_rel, "# shared\n- 2026-09-17: staged later\n")
        git(P, "add", "--", staged_rel)
        f0, d0 = tree(P)
        try:
            quiet(wm.execute_plan, plan, P)
            check("plan 之后才暂存 ⇒ apply 拒", "未拒", "拒")
        except wm.MigrateRefused:
            check("plan 之后才暂存 ⇒ apply 拒", "拒", "拒")
        check("… 零改动", tree(P) == (f0, d0), True)
    # 上一格被重算比对先拦下（暂存让 R-memory 变 blocked、动作表不同），前置检查与动作前一刻那两道复核都没走到。
    # 这里让暂存恰好发生在重算之后 / 前置检查之后，两道各自钉住
    for after, label in (("compute_plan", "重算之后才暂存 ⇒ 前置检查拒"), ("_precheck_all", "前置检查之后才暂存 ⇒ 搬记忆目录那一步失败")):
        with Proj(mirror=False) as P:
            put(P / staged_rel, "# shared\n- 2026-09-17: staged in between\n")    # 未暂存的改动随目录走，plan 照常
            plan = plan_for(P)
            orig = getattr(wm, after)

            def stage_after(*a, _orig=orig, _P=P, **kw):
                rv = _orig(*a, **kw)
                git(_P, "add", "--", staged_rel)
                return rv

            f0, d0 = tree(P)
            with mock.patch.object(wm, after, stage_after):
                try:
                    receipt, _out = quiet(wm.execute_plan, plan, P)
                    refused = None
                except wm.MigrateRefused as e:
                    receipt, refused = None, str(e)
            if after == "compute_plan":
                check(label, refused is not None and "已暂存" in refused, True)
                check("… 零改动", tree(P) == (f0, d0), True)
            else:
                failed = [x for x in (receipt or {}).get("actions", []) if x.get("status") == "failed"]
                check(label, [(x["item"], "已暂存" in x.get("error", "")) for x in failed], [("R-memory", True)])
                check("… 旧记忆目录原样在、暂存仍在索引里",
                      ((P / OLD["memory"]).is_dir(), staged_rel in git(P, "diff", "--cached", "--name-only")[1].splitlines()),
                      (True, True))


def test_reports():
    print("[report] 模块清单只认 code_paths 列表项；.gitignore 整目录规则压过例外时提示")
    with Proj(mirror=False) as P:
        put(P / "projects" / "modules" / "a" / "b" / "submodule.yaml",
            "name: b\ncode_paths:\n  - \".claude/skills/demo/**\"   # 真正圈文件的一项\n  - src/x.py\n")
        put(P / "projects" / "modules" / "c" / "d" / "submodule.yaml",
            "name: d\nnotes: 以前在 .claude/skills/old 下\ncode_paths:\n  - src/y.py   # 旧版在 .claude/skills/y\n")
        git(P, "add", "-A")
        git(P, "commit", "-q", "-m", "modules")
        plan = wm.compute_plan(P)
        mod = [i for i in plan["items"] if i["id"] == "L-report" and "圈文件的模块清单" in i["title"]]
        lines = mod[0]["lines"] if mod else []
        check("code_paths 列表项圈到的模块列出", "projects/modules/a/b/submodule.yaml" in lines, True)
        check("只在注释 / 别的字段里提到的不列", "projects/modules/c/d/submodule.yaml" in lines, False)
        check("出厂解析函数 ⇒ 不报「算不出来」", [i["title"] for i in plan["items"] if "算不出来" in i["title"]], [])
        check("默认 .gitignore ⇒ 不出整目录忽略提示",
              [i for i in plan["items"] if i["id"] == "L-report" and "压过" in i["title"]], [])
    blanket = f"{OLD['state']}/*\n!{OLD['state']}/memory-index.json\nlogs/\n{NEW['memory'].split('/')[0]}/\n"
    with Proj(mirror=False, gitignore=blanket) as P:
        plan = wm.compute_plan(P)
        hint = [i for i in plan["items"] if i["id"] == "L-report" and "压过" in i["title"]]
        check("整目录忽略新运行态根 ⇒ 提示", len(hint), 1)
        check("… 列出记忆目录与 memory-index", sorted(ln.split(" ← ")[0] for ln in (hint[0]["lines"] if hint else [])),
              sorted([f"{NEW['memory']}/shared/MEMORY.md", f"{NEW['state']}/memory-index.json", f"{NEW['archive']}/x.md"]))


def test_literal_boundaries():
    print("[literals] 只换项目相对的整段字面")
    m, s = OLD["memory"], OLD["state"]
    cases = [
        (f"项目：`{m}/dev/MEMORY.md`", True),
        (f"{m}/dev 在行首", True),
        (f"导入 @{m}/shared/MEMORY.md", True),
        (f"用户级：`~/{m}/<agent>/`", False),
        (f"官方 local 作用域：`{m}-local/<agent>/`", False),
        (f"另一个项目：`../other-proj/{s}/events.jsonl`", False),
        (f"绝对路径：`D:/work/proj/{s}/x`", False),
        (f"HOME：`$HOME/{m}/x`", False),
        ("反斜杠：`" + m.replace("/", "\\") + "\\dev`", False),
        (f"URL：https://example.com/{m}/doc", False),
        (f"模板占位：`{{project}}/{OLD['archive']}/`", False),
        (f"`./{m}/x` 以点斜杠开头（已知代价：不换）", False),
        (f'锚定：`"$CLAUDE_PROJECT_DIR/{s}/plugin-root.txt"`', True),
        (f"锚定：`${{CLAUDE_PROJECT_DIR}}/{s}/x`", True),
        (f'锚定：`"$CLAUDE_PROJECT_DIR"/{m}/x`', True),
        (f'锚定：`"${{CLAUDE_PROJECT_DIR}}"/{m}/x`', True),
        (f"占位：`<项目根>/{m}/`（不恒指本项目：不换）", False),
    ]
    for text, want in cases:
        _new, hits = wm.rewrite_literals(text + "\n", list(KINDS))
        check(f"{'换' if want else '不换'}：{text[:40]}", bool(hits), want)
    check("锚定写法只换字面、锚点原样",
          wm.rewrite_literals(f"${{CLAUDE_PROJECT_DIR}}/{s}/x\n", list(KINDS))[0], f"${{CLAUDE_PROJECT_DIR}}/{NEW['state']}/x\n")


def _printed_inverse(out):
    lines, grab = [], False
    for ln in out.splitlines():
        if ln.startswith("逆操作——"):
            grab = True
            continue
        if grab:
            if not ln.startswith("  "):
                break
            lines.append(ln[2:])
    return lines


def test_printed_inverse_runs():
    print("[inverse] 打印出来的逆操作原样执行 ⇒ 回到 apply 前（镜像目录里未提交 / 未跟踪的除外）")
    # 第一种夹具的项目路径带空格、单引号与非 ASCII：逆操作里的路径引号要在真实 shell 里原样成立。
    # 后两种不钉 autocrlf=false：git 找回镜像目录时的行尾要按 apply 那一刻的工作树形态试算
    for autocrlf, eol, leaf, want_values in (("false", "lf", "my proj's 项目", ["false"]),
                                             ("true", "lf", "autocrlf-true lf", ["false"]),
                                             ("true", "mixed", "autocrlf-true mixed", ["false", "true"])):
        _printed_inverse_case(autocrlf, eol, leaf, want_values)


def _printed_inverse_case(autocrlf, eol, leaf, want_values):
    tag = f"[autocrlf={autocrlf} / {eol}]"
    with Proj(leaf=leaf, autocrlf=autocrlf, mirror_eol=eol) as P:
        plan = plan_for(P)
        with mock.patch.object(H, "stdin_is_console", lambda: True):
            code, out = run_door(["--migrate", "--apply", "--confirm", plan["code"], "--project", str(P)])
        check(f"{tag} apply rc 0", code, 0)
        check(f"{tag} 没有未迁完 ⇒ 无「未迁完」块、提示重开会话", ("未迁完" in out, "完成后重开会话" in out), (False, True))
        inv = _printed_inverse(out)
        if "'" in leaf:
            check("逆操作里的路径带着空格与单引号", any("my proj''s 项目" in ln or "my proj'\\''s 项目" in ln for ln in inv), True)
        check(f"{tag} 逆操作块非空", len(inv) > 0, True)
        restores = [ln for ln in inv if " restore " in ln]
        check(f"{tag} 镜像目录的找回命令按试算带 core.autocrlf",
              sorted(ln.split("core.autocrlf=", 1)[1].split(" ", 1)[0] for ln in restores if "core.autocrlf=" in ln),
              want_values)
        receipt_path = next(ln.split("回执：", 1)[1].strip() for ln in out.splitlines() if ln.startswith("回执："))
        receipt = json.loads(Path(receipt_path).read_bytes())
        script_dir = Path(tempfile.mkdtemp(prefix="wf-mig-inv-"))
        try:
            if os.name == "nt":
                body = ["$ErrorActionPreference = 'Continue'"]
                for i, ln in enumerate(inv, 1):
                    body.append(ln)
                    if not ln.startswith("#"):
                        body.append(f"if (-not $?) {{ Write-Output 'FAILED {i}'; exit 11 }}")
                script = script_dir / "inverse.ps1"
                script.write_bytes(b"\xef\xbb\xbf" + "\r\n".join(body).encode("utf-8") + b"\r\n")
                cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)]
            else:
                body = [ln if ln.startswith("#") else f"{ln} || {{ echo 'FAILED {i}'; exit 11; }}"
                        for i, ln in enumerate(inv, 1)]
                script = script_dir / "inverse.sh"
                script.write_bytes(("\n".join(body) + "\n").encode("utf-8"))
                cmd = ["sh", str(script)]
            r = subprocess.run(cmd, capture_output=True)
            check(f"{tag} 逆操作逐行执行全部成功", (r.returncode, r.stdout.decode("utf-8", "replace").strip()[-200:]), (0, ""))
        finally:
            rm(script_dir)
        now = wm._manifest(P, receipt["affected_paths"])
        pre = receipt["pre_manifest"]
        diff = sorted(k for k in set(pre) | set(now) if pre.get(k) != now.get(k))
        check(f"{tag} 逐文件 sha256 只差镜像目录里未提交 / 未跟踪的两份", diff, sorted([f"{MIRROR}/beta.md", f"{MIRROR}/gamma.md"]))
        check(f"{tag} 新建的 AGENTS.md 已按逆操作删掉", (P / "AGENTS.md").exists(), False)
        check(f"{tag} skills 回到真目录", (P / H.SKILLS_DIR_CC).is_dir() and not H.is_dir_link(P / H.SKILLS_DIR_CC), True)


def test_unfinished_exit():
    print("[unfinished] 没做完 ⇒ 其余照做、退出码 8、收尾块按类分措辞（留在旧位置的才说「框架只读新位置」）")
    staged_rel = f"{OLD['memory']}/shared/MEMORY.md"
    for label, setup, marker, window in (
            ("blocked（记忆目录里有已暂存改动）", "stage", "未迁完：R-memory", True),
            ("冲突（新记忆目录已在）", "conflict", "未迁完：R-conflict", True),
            ("blocked 且中途停下", "stage+stop", "未迁完：R-memory", True),
            ("导入加不上（CLAUDE.md 末尾有未闭合的 <!--）", "import-blocked", "没做完：I-agents-import", False),
            ("整份跳过改写（CLAUDE.md 有未提交改动、skills md 无旧路径）", "skip-all", "跳过改写：CLAUDE.md", False),
            ("部分跳过改写（skills md 照改、CLAUDE.md 有未提交改动）", "skip-part", "跳过改写：CLAUDE.md", False)):
        with Proj(mirror=False) as P:
            if setup.startswith("stage"):
                put(P / staged_rel, "# shared\n- 2026-09-17: staged\n")
                git(P, "add", "--", staged_rel)
            elif setup == "conflict":
                (P / NEW["memory"]).mkdir(parents=True)
            elif setup == "import-blocked":
                put(P / "CLAUDE.md", (P / "CLAUDE.md").read_text(encoding="utf-8") + "\n<!-- 未闭合的注释\n")
                git(P, "commit", "-q", "-am", "unclosed comment")
            else:
                if setup == "skip-all":
                    put(P / H.SKILLS_DIR_CC / "demo" / "SKILL.md", "demo\n")
                else:                                   # 已有导入：只剩旧路径改写这一件被跳过
                    put(P / "CLAUDE.md", (P / "CLAUDE.md").read_text(encoding="utf-8") + "@AGENTS.md\n")
                git(P, "commit", "-q", "-am", "prep")
                put(P / "CLAUDE.md", (P / "CLAUDE.md").read_text(encoding="utf-8") + "用户未提交的一行\n")
            plan = plan_for(P)
            real = wm._do_action

            def stop_at_archive(project, a, *rest):
                if setup == "stage+stop" and a["op"] == "move_dir" and a["item"] == "R-archive":
                    raise RuntimeError("probe stop")
                return real(project, a, *rest)

            with mock.patch.object(H, "stdin_is_console", lambda: True), mock.patch.object(wm, "_do_action", stop_at_archive):
                code, out = run_door(["--migrate", "--apply", "--confirm", plan["code"], "--project", str(P)])
            check(f"{label} ⇒ 退出码 8", code, 8)
            check(f"{label} ⇒ 块里点名「{marker}」", f"! {marker}" in out, True)
            check(f"{label} ⇒ 不打印「完成后重开会话」", "完成后重开会话" in out, False)
            check(f"{label} ⇒ 其余类照做（state 已搬）", os.path.lexists(P / OLD["state"]), False)
            if window:
                check(f"{label} ⇒ 说「框架只读新位置」「处理完之前别开这个项目的会话」",
                      ("框架只读新位置" in out, "处理完之前别开这个项目的会话" in out), (True, True))
            else:
                check(f"{label} ⇒ 不说「框架只读新位置」、说「不涉及运行态目录的位置」「补做之前别开这个项目的会话」",
                      ("框架只读新位置" in out, "不涉及运行态目录的位置" in out, "补做之前别开这个项目的会话" in out),
                      (False, True, True))
            if setup == "stage+stop":
                check(f"{label} ⇒ 停下的原因也在", "停下了" in out, True)
            if setup.startswith("skip"):
                check(f"{label} ⇒ 块里写明跳过的是旧路径改写、原因与「先提交」",
                      any(ln.startswith("! 跳过改写：CLAUDE.md（有未提交改动") and "旧运行态路径改写" in ln for ln in out.splitlines())
                      and "先提交那个文件" in out, True)
                check(f"{label} ⇒ CLAUDE.md 仍是旧路径（确实没改）", OLD["memory"] in (P / "CLAUDE.md").read_text(encoding="utf-8"), True)
            if setup == "skip-all":
                check(f"{label} ⇒ apply 摘要印出整条跳过的 L-literals 与 I-agents-import",
                      ("- [L-literals]" in out, "- [I-agents-import]" in out), (True, True))


def test_residual_literals():
    print("[residue] $CLAUDE_PROJECT_DIR 一族照换；改写之后仍留着的旧字面列进报告项")
    m, s = OLD["memory"], OLD["state"]
    anchored = f"{H.SKILLS_DIR_CC.as_posix()}/anchored/SKILL.md"
    with Proj(mirror=False) as P:
        put(P / "CLAUDE.md", (P / "CLAUDE.md").read_text(encoding="utf-8") + f"模板里写作 `<项目根>/{m}/shared/MEMORY.md`\n")
        put(P / anchored, f'cat "${{CLAUDE_PROJECT_DIR}}/{s}/plugin-root.txt"\n')
        git(P, "add", "-A")
        git(P, "commit", "-q", "-m", "residue")
        plan = plan_for(P)
        reports = [i for i in plan["items"] if i["id"] == "L-report"]
        residue = [ln for i in reports if "改写之后仍留着" in i["title"] for ln in i["lines"]]
        check("混合文件（一处照换、一处占位写法）：残留那一行列进报告项",
              [ln.split(":", 1)[0] for ln in residue if "<项目根>" in ln], ["CLAUDE.md L" + str(
                  (P / "CLAUDE.md").read_text(encoding="utf-8").count("\n"))])
        check("只含锚定写法的 skills md 进改写集",
              any(a["op"] == "rewrite_file" and a.get("plan_path") == anchored for a in plan["payload"]["actions"]), True)
        check("… 且不出现在任何报告项里", [ln for i in reports for ln in i["lines"] if "anchored/SKILL.md" in ln], [])
        receipt, _ = quiet(wm.execute_plan, plan, P)
        check("apply ok", receipt["status"], "ok")
        check("… 锚定写法换成新路径、锚点原样",
              (P / H.SKILLS_DIR_NEUTRAL / "anchored" / "SKILL.md").read_text(encoding="utf-8"),
              f'cat "${{CLAUDE_PROJECT_DIR}}/{NEW["state"]}/plugin-root.txt"\n')


def test_code_paths_parser_loud():
    print("[parser] 模块清单解析取不出 / 自检不过 ⇒ plan 不崩、出「算不出来」报告项")
    src = wm.CODE_PATHS_SOURCE.read_text(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="wf-mig-parser-"))
    try:
        for n, (label, old, new) in enumerate((("函数改名", "def parse_code_paths(yaml_path):", "def parse_code_paths_v2(yaml_path):"),
                                               ("函数体依赖模块里的别的名字", "paths.append(val)", "paths.append(_norm(val))"))):
            bad = tmp / f"variant{n}.py"
            check(f"{label}：取材锚点唯一", src.count(old), 1)
            bad.write_bytes(src.replace(old, new).encode("utf-8"))
            with Proj(mirror=False) as P:
                put(P / "projects" / "modules" / "a" / "b" / "submodule.yaml", 'name: b\ncode_paths:\n  - ".claude/skills/demo/**"\n')
                git(P, "add", "-A")
                git(P, "commit", "-q", "-m", "modules")
                with mock.patch.object(wm, "CODE_PATHS_SOURCE", bad):
                    try:
                        plan, err = wm.compute_plan(P), None
                    except Exception as e:  # noqa: BLE001
                        plan, err = None, f"{type(e).__name__}: {e}"
                check(f"{label} ⇒ plan 不崩", err, None)
                check(f"{label} ⇒ 出「算不出来」报告项",
                      any(i["id"] == "L-report" and "算不出来" in i["title"] for i in (plan or {}).get("items", [])), True)
    finally:
        rm(tmp)


def test_mirror_unrestorable():
    print("[mirror-lost] 镜像目录里 git 找回还原不了原字节的干净文件 ⇒ plan 算进「会丢」并点名，apply 逆操作说明再点名")
    attrs = f"{MIRROR}/alpha.md text eol=crlf\n"       # 检出恒为 CRLF，而工作树是 LF、git status 干净
    with Proj(gitattributes=attrs) as P:
        _rc, st = git(P, "status", "--porcelain", "--", f"{MIRROR}/alpha.md")
        check("夹具：alpha.md 干净", st.strip(), "")
        plan = plan_for(P)
        x = next(i for i in plan["items"] if i["id"] == "X-rules")
        check("plan：「会丢」计数含还原不了原字节的干净文件",
              "其中 2 份会丢——1 份有未提交改动、1 份已提交但 git 找回还原不了原字节" in x["title"], True)
        check("plan：逐份点名", f"{MIRROR}/alpha.md（git 找回还原不了原字节）" in x["lines"], True)
        with mock.patch.object(H, "stdin_is_console", lambda: True):
            code, out = run_door(["--migrate", "--apply", "--confirm", plan["code"], "--project", str(P)])
        check("apply rc 0", code, 0)
        check("apply：逆操作说明点名",
              any(ln.startswith("  # ") and "还原不了原字节" in ln and f"{MIRROR}/alpha.md" in ln for ln in out.splitlines()), True)
    with Proj() as P:
        x = next(i for i in wm.compute_plan(P)["items"] if i["id"] == "X-rules")
        check("对照：没有这种文件 ⇒ 计数只含有未提交改动的、不出现「还原不了」",
              ("其中 1 份会丢——1 份有未提交改动）" in x["title"], "还原不了" in x["title"]), (True, False))


def _apply_out(P, plan, do_action=None):
    patches = [mock.patch.object(H, "stdin_is_console", lambda: True)]
    if do_action is not None:
        patches.append(mock.patch.object(wm, "_do_action", do_action))
    with contextlib.ExitStack() as stack:
        for pt in patches:
            stack.enter_context(pt)
        return run_door(["--migrate", "--apply", "--confirm", plan["code"], "--project", str(P)])


def test_midstop_block():
    print("[midstop] 中途停下 ⇒ 收尾块列失败那一步与其后没执行的类、按类分措辞，已完成的不列")
    if os.name == "nt":
        with Proj(mirror=False) as P:
            plan = plan_for(P)
            fh = open(P / OLD["memory"] / "dev" / "notes.md", "rb")
            try:
                code, out = _apply_out(P, plan)
            finally:
                fh.close()
            block = [ln for ln in out.splitlines() if ln.startswith("! ")]
            check("持句柄停在 R-memory ⇒ 退出码 8", code, 8)
            check("… 块里「未迁完：R-memory——停在这一步」", any(ln.startswith("! 未迁完：R-memory——停在这一步") for ln in block), True)
            check("… 其后没执行的窗口类也列（R-archive 还没执行）", any(ln.startswith("! 未迁完：R-archive——还没执行") for ln in block), True)
            check("… 说「框架只读新位置」「处理完之前别开这个项目的会话」",
                  ("框架只读新位置" in out, "处理完之前别开这个项目的会话" in out), (True, True))
            check("… 已完成的步骤不列（S-skills / G-gitignore / R-state）",
                  [ln for ln in block if any(f"：{i}——" in ln for i in ("S-skills", "G-gitignore", "R-state"))], [])
    else:
        print("  [skip] 持句柄阻断搬迁只在 Windows 上成立（POSIX 的 rename 不受打开的文件影响）")
    with Proj(mirror=False) as P:
        plan = plan_for(P)
        real = wm._do_action

        def stop_at_literals(project, a, *rest):
            if a["op"] == "rewrite_file" and a["item"] == "L-literals":
                raise RuntimeError("probe stop")
            return real(project, a, *rest)

        code, out = _apply_out(P, plan, stop_at_literals)
        block = [ln for ln in out.splitlines() if ln.startswith("! ")]
        check("停在 L-literals ⇒ 退出码 8", code, 8)
        check("… 块里「没做完：L-literals——停在这一步」", any(ln.startswith("! 没做完：L-literals——停在这一步") for ln in block), True)
        check("… 不说「框架只读新位置」、说「不涉及运行态目录的位置」「补做之前别开这个项目的会话」",
              ("框架只读新位置" in out, "不涉及运行态目录的位置" in out, "补做之前别开这个项目的会话" in out),
              (False, True, True))
        check("… 已完成的窗口类不列", [ln for ln in block if ln.startswith("! 未迁完")], [])
    with Proj(mirror=False) as P:
        plan = plan_for(P)
        real = wm._do_action

        def archive_not_moved(project, a, *rest):
            if a["op"] == "move_dir" and a["item"] == "R-archive":
                return None
            return real(project, a, *rest)

        code, out = _apply_out(P, plan, archive_not_moved)
        block = [ln for ln in out.splitlines() if ln.startswith("! ")]
        check("执行完旧路径仍在 ⇒ 退出码 8", code, 8)
        check("… 块里「未迁完：R-archive——执行完旧路径仍在」",
              any(ln.startswith("! 未迁完：R-archive——执行完旧路径仍在") for ln in block), True)
        check("… 说「框架只读新位置」「处理完之前别开这个项目的会话」",
              ("框架只读新位置" in out, "处理完之前别开这个项目的会话" in out), (True, True))


def test_skipped_moved_path():
    print("[skip-path] 跳过改写的 skills md 已随目录搬到中立目录 ⇒ 收尾块给搬迁后的路径")
    cc, neu = H.SKILLS_DIR_CC.as_posix(), H.SKILLS_DIR_NEUTRAL.as_posix()
    with Proj(mirror=False) as P:
        put(P / cc / "fresh" / "SKILL.md", f"新写、未跟踪的 `{OLD['memory']}/x`\n")
        put(P / cc / "demo" / "SKILL.md", (P / cc / "demo" / "SKILL.md").read_text(encoding="utf-8") + "未提交的一行\n")
        plan = plan_for(P)
        code, out = _apply_out(P, plan)
        block = sorted(ln.split("——")[0] for ln in out.splitlines() if ln.startswith("! 跳过改写："))
        check("退出码 8", code, 8)
        check("块里给搬迁后的路径（未跟踪 / 有未提交改动）", block,
              sorted([f"! 跳过改写：{neu}/demo/SKILL.md（有未提交改动（ M））", f"! 跳过改写：{neu}/fresh/SKILL.md（未跟踪）"]))
        check("… 两个文件确实在搬迁后的真目录里",
              ((P / neu / "fresh" / "SKILL.md").is_file(), (P / neu / "demo" / "SKILL.md").is_file(), H.is_dir_link(P / neu)),
              (True, True, False))


def test_link_commit_hint():
    print("[link-hint] 新写法下链接被忽略、不提示；盖不住时（否定规则）两步提交建议多一句「别提交这一行链接」")
    with Proj(mirror=False) as P:
        plan = plan_for(P)
        code, out = _apply_out(P, plan)
        cc = H.SKILLS_DIR_CC.as_posix()
        rc, _ = git(P, "check-ignore", "-q", "--no-index", cc)
        ignored = rc == 0
        check("apply rc 0", code, 0)
        check("新写法下链接被忽略（两平台）", ignored, True)
        check(f"链接{'被忽略 ⇒ 不提示' if ignored else '没被忽略 ⇒ 提示别提交'}", "别提交它" in out, not ignored)
        check("… 提示与 git status 里有没有这一行一致",
              (cc in untracked_of(git(P, "status", "--porcelain", "-uall")[1]), "别提交它" in out),
              (not ignored, not ignored))
        # 盖不住的情形照样要提示：marker 外补一条否定规则，让链接本身不再被忽略
        with open(P / ".gitignore", "ab") as f:
            f.write(b"!.claude/skills\n")
        check("否定规则盖掉忽略行 ⇒ 提示出现", any("别提交它" in ln for ln in wm._skills_link_untracked_note(P, True)), True)


def test_pending_source_mismatch():
    print("[pending] 回执与 plan 动作表对不上 ⇒ 只按回执报，不顺着索引往后列")
    actions = [{"op": "move_dir", "item": "R-state", "src": OLD["state"], "dst": NEW["state"], "method": "rename"},
               {"op": "move_dir", "item": "R-memory", "src": OLD["memory"], "dst": NEW["memory"], "method": "rename"},
               {"op": "move_dir", "item": "R-archive", "src": OLD["archive"], "dst": NEW["archive"], "method": "rename"}]
    logs = [{"n": 1, "op": "move_dir", "item": "R-state", "status": "done"},
            {"n": 2, "op": "move_dir", "item": "R-memory", "status": "failed"}]
    ok = wm._pending_after_stop({"stopped_at": 2, "error": "boom", "actions": logs}, actions)
    check("对得上 ⇒ 失败那一步 ＋ 其后没执行的", [i for i, _n in ok], ["R-memory", "R-archive"])
    bad_logs = [dict(logs[0]), {"n": 2, "op": "remove_rules_mirror", "item": "X-rules", "status": "failed"}]
    bad = wm._pending_after_stop({"stopped_at": 2, "error": "boom", "actions": bad_logs}, actions)
    check("对不上 ⇒ 只报回执里那一步、不往后列", [(i, "对不上" in n) for i, n in bad], [("X-rules", True)])


def _runtime_path_mutant(project, kind):
    """突变体：迁移工具改回经运行期路径看目录（`sio.runtime_rel` 恒指新位置）——下面每格的反面对照都用它。"""
    return sio.runtime_rel(project, kind)


def test_located_rel_consumers():
    print("[located] 旧目录只在旧位置：R-index / .gitignore 的状态目录 / 报告项 / 会话信号都看旧目录（各配突变对照）")
    # 判定式本身四种形态逐格钉：新旧并存时取新，与关窗前 `runtime_rel` 同值——plan 对冲突项目的读数因此不变
    for label, pres, want in (("都没有", (), "new"), ("只有旧", ("old",), "old"),
                              ("只有新", ("new",), "new"), ("新旧并存", ("old", "new"), "new")):
        tmp = Path(tempfile.mkdtemp(prefix="wf-loc-"))
        try:
            for k in KINDS:
                for side in pres:
                    (tmp / (OLD[k] if side == "old" else NEW[k])).mkdir(parents=True)
            check(f"_located_rel {label} ⇒ {want}", [wm._located_rel(tmp, k) for k in KINDS],
                  [(OLD[k] if want == "old" else NEW[k]) for k in KINDS])
        finally:
            rm(tmp)
    staged_rel = f"{OLD['state']}/memory-index.json"
    with Proj(mirror=False) as P:
        # state 被暂存改动挡住、memory 照搬：R-index 与 `.gitignore` 都要按「搬完之后 state 仍在旧位置」算
        put(P / staged_rel, '{"entries": {"k": 1}}\n')
        git(P, "add", "--", staged_rel)
        put(P / OLD["memory"] / "custom-agent" / "notes.txt", "x\n")

        def facts(plan):
            items = {i["id"]: i for i in plan["items"] if i["id"] != "L-report"}
            reports = [i["title"] for i in plan["items"] if i["id"] == "L-report"]
            g = next((a for a in plan["payload"]["actions"] if a["op"] == "write_gitignore"), None)
            ri = next((a for a in plan["payload"]["actions"] if a.get("item") == "R-index"), None)
            return {"state": items.get("R-state", {}).get("status"), "memory": items.get("R-memory", {}).get("status"),
                    "ri_path": ri and ri["path"], "g_state": g and g["state_rel"],
                    "odd": any("状态目录里的非标准内容" in t for t in reports),
                    "extra_mem": any("记忆目录里的非出厂角色子目录" in t for t in reports)}

        got = facts(wm.compute_plan(P))
        check("前提：state blocked、memory planned", (got["state"], got["memory"]), ("blocked", "planned"))
        check("R-index 出、改写旧状态目录里的 rollback-index", got["ri_path"], f"{OLD['state']}/rollback-index.json")
        check("G-gitignore 的 managed 段按旧状态目录写", got["g_state"], OLD["state"])
        check("L-report：旧状态目录的非标准内容（backups/）报出", got["odd"], True)
        check("L-report：旧记忆目录的非出厂子目录（custom-agent/）报出", got["extra_mem"], True)
        with mock.patch.object(wm, "_located_rel", _runtime_path_mutant):
            bad = facts(wm.compute_plan(P))
        check("突变对照：改回运行期路径 ⇒ R-index 消失", bad["ri_path"], None)
        check("突变对照：改回运行期路径 ⇒ .gitignore 按新状态目录写", bad["g_state"], NEW["state"])
        check("突变对照：改回运行期路径 ⇒ 两条报告项都消失", (bad["odd"], bad["extra_mem"]), (False, False))
    with Proj(mirror=False) as P:
        (P / OLD["state"] / "activity-state.json").write_bytes(b'{"session_counter": 6}')
        check("只有旧状态目录、刚写过 ⇒ 会话信号报出", any("秒前" in m for m in wm.session_signals(P)), True)
        with mock.patch.object(wm, "_located_rel", _runtime_path_mutant):
            check("突变对照：改回运行期路径 ⇒ 会话信号全盲", wm.session_signals(P), [])


def test_unknown_pending_item():
    print("[pending-?] 回执那条 log 缺 item ⇒ 收尾块归「未迁完」、说「框架只读新位置」")
    actions = [{"op": "move_dir", "item": "R-state", "src": OLD["state"], "dst": NEW["state"], "method": "rename"}]
    pend = wm._pending_after_stop({"stopped_at": 1, "error": "boom", "actions": [{"n": 1, "op": "move_dir"}]}, actions)
    check("前提：条目 id 退成 `?`", [i for i, _n in pend], ["?"])
    block = wm._unfinished_block([], pend)
    check("… 归「未迁完」一侧", any(ln.startswith("! 未迁完：?——") for ln in block), True)
    check("… 说「框架只读新位置」「处理完之前别开这个项目的会话」",
          (any("框架只读新位置" in ln for ln in block), any("处理完之前别开这个项目的会话" in ln for ln in block)), (True, True))
    known = wm._unfinished_block([], [("L-literals", "停在这一步")])
    check("对照：认得出的非搬迁条目仍归「没做完」", any(ln.startswith("! 没做完：L-literals——") for ln in known), True)


def test_link_hint_on_resume():
    print("[link-resume] 续做只剩改写 / 候选文件（没有两步提交建议）⇒ 链接没被忽略的提醒照样打印")
    with Proj(mirror=False) as P:
        put(P / "CLAUDE.md", (P / "CLAUDE.md").read_text(encoding="utf-8") + "用户未提交的一行\n")
        code, _out = _apply_out(P, plan_for(P))
        check("前提：第一次 apply 跳过 CLAUDE.md 改写、退出码 8", code, 8)
        git(P, "commit", "-q", "-m", "user line", "--", "CLAUDE.md")
        plan = plan_for(P)
        check("前提：续做只剩改写 / 建 AGENTS.md，没有搬迁与删镜像",
              sorted({a["op"] for a in plan["payload"]["actions"]} - {"rewrite_file", "create_agents_md", "write_candidate"}),
              [])
        sentinel = "     注意：SENTINEL-LINK-NOTE"
        with mock.patch.object(wm, "_skills_link_untracked_note", lambda project, g: [sentinel]):
            code, out = _apply_out(P, plan)
        check("续做 apply rc 0", code, 0)
        check("… 没有两步提交建议", "提交建议（分两步" in out, False)
        check("… 链接提醒照样打印", sentinel in out, True)


def _legacy_errors(P):
    out = doc.check_legacy_state_dir({"paths": doc.Paths(P)})
    return [m for lv, m in out if lv == "error"], [m for lv, m in out if lv == "info"]


def test_doctor_legacy_state_dir():
    print("[doctor-legacy] legacy_state_dir：按 kind 两态、memory 按出厂形态判、排在安装组第一、改指修法")
    cases = (
        # (标签, 建哪些目录 / 文件, 期望「没迁移」里点名的 kind, 期望「新旧并存」里点名的 kind, 期望有 info)
        ("都没有", [], [], [], False),
        ("只有新", [NEW["state"] + "/", NEW["memory"] + "/shared/"], [], [], False),
        ("只有旧 state", [OLD["state"] + "/activity-state.json"], ["state"], [], False),
        ("只有旧 memory（shared/）", [OLD["memory"] + "/shared/MEMORY.md"], ["memory"], [], False),
        ("只有旧 archive", [OLD["archive"] + "/x.md"], ["archive"], [], False),
        ("旧 memory 只有出厂角色的 MEMORY.md", [OLD["memory"] + "/dev/MEMORY.md"], ["memory"], [], False),
        ("新旧并存 state", [OLD["state"] + "/a.json", NEW["state"] + "/b.json"], [], ["state"], False),
        ("混合：state 并存 ＋ memory 只有旧", [OLD["state"] + "/a.json", NEW["state"] + "/b.json",
                                             OLD["memory"] + "/shared/MEMORY.md"], ["memory"], ["state"], False),
        ("只有 CC memory: project 形态", [OLD["memory"] + "/code-reviewer/MEMORY.md"], [], [], True),
        ("旧记忆目录是空目录", [OLD["memory"] + "/"], [], [], True),
        # 同名文件（不是目录）也算旧布局：`legacy_present` 用 lexists、`_legacy_memory_is_framework` 对非目录判框架
        ("旧 state 位置是同名文件", [OLD["state"]], ["state"], [], False),
        ("旧 memory 位置是同名文件", [OLD["memory"]], ["memory"], [], False),
    )
    tmp = Path(tempfile.mkdtemp(prefix="wf-legacy-"))
    try:
        # 悬空链接也算旧布局（lexists）；本机建不了符号链接时如实跳过、不算过
        (tmp / OLD["state"]).parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(str(tmp / "nowhere"), str(tmp / OLD["state"]), target_is_directory=True)
            linked = True
        except OSError:
            linked = False
        if linked:
            errs, _i = _legacy_errors(tmp)
            check("旧 state 位置是悬空链接 ⇒ 算没迁移", any(m.startswith("没迁移：") and OLD["state"] + "/" in m for m in errs), True)
        else:
            print("  [skip] 本机建不了符号链接，悬空链接格没跑")
    finally:
        rm(tmp)
    for label, paths, want_old, want_both, want_info in cases:
        tmp = Path(tempfile.mkdtemp(prefix="wf-legacy-"))
        try:
            for rel in paths:
                if rel.endswith("/"):
                    (tmp / rel).mkdir(parents=True, exist_ok=True)
                else:
                    put(tmp / rel, "x\n")
            errs, infos = _legacy_errors(tmp)
            if paths and not any(p.endswith("/") for p in paths) and len(paths) == 1 and paths[0] in OLD.values():
                check(f"{label} ⇒ 前提：那个位置确实是文件", (tmp / paths[0]).is_file(), True)
            old_msg = next((m for m in errs if m.startswith("没迁移：")), "")
            both_msg = next((m for m in errs if m.startswith("新旧并存：")), "")
            check(f"{label} ⇒「没迁移」点名的 kind", [k for k in KINDS if OLD[k] + "/" in old_msg], want_old)
            check(f"{label} ⇒「新旧并存」点名的 kind", [k for k in KINDS if OLD[k] + "/" in both_msg], want_both)
            check(f"{label} ⇒ error 条数", len(errs), bool(want_old) + bool(want_both))
            check(f"{label} ⇒ 有 info", bool(infos), want_info)
            if old_msg:
                check(f"{label} ⇒「没迁移」给迁移命令、提醒别在本项目自己的会话里出、提醒 auto-memory 手改",
                      ("--migrate" in old_msg, "别在本项目自己的会话里出" in old_msg, "auto-memory" in old_msg),
                      (True, True, True))
            if both_msg:
                check(f"{label} ⇒「新旧并存」说迁移工具判冲突、给「挪出项目」的出路",
                      ("判冲突" in both_msg, "挪出项目" in both_msg, "--migrate" in both_msg), (True, True, True))
        finally:
            rm(tmp)

    # 顺序与改指：旧 state 在、新 state 被会话建了一套（首会话验收实际碰到的形态）
    tmp = Path(tempfile.mkdtemp(prefix="wf-legacy-"))
    try:
        put(tmp / "projects" / "board.yaml", "tasks: []\n")
        put(tmp / OLD["state"] / "activity-state.json", '{"session_counter": 42}')
        put(tmp / OLD["memory"] / "shared" / "MEMORY.md", "# shared\n")
        put(tmp / NEW["state"] / "plugin-root.txt", "x\n")
        # `scaffold` 与 `agents_md` 两步都要有：只有 `agents_md` 的文件现在被 doctor 判成
        # 「没有任何 launcher 步骤」（那是会话启动 hook 自己补记的一步，不证明 launcher 来过）
        # 而走自愈 info，于是下面那条按条数判的前提断言会**绿着失真**——它自称
        # 「setup_state 的行在」，实测那一行已经不在了，靠另外三行凑够 3 条照样过。
        put(tmp / NEW["state"] / "setup-state.json",
            json.dumps({"steps": {"scaffold": "ok", "agents_md": "ok"}}))
        results = doc.run_all(tmp, "install")
        check("legacy_state_dir 排在安装组结果首位（放进 runtime 组或排后都红）", results[0]["id"], "legacy_state_dir")
        text = doc.summarize(results) or ""
        lines = text.splitlines()
        first_legacy = next((i for i, ln in enumerate(lines) if "[legacy_state_dir]" in ln), None)
        first_skel = next((i for i, ln in enumerate(lines) if "[skeleton]" in ln), None)
        check("摘要文本里 legacy 行先于 skeleton 行",
              first_legacy is not None and first_skel is not None and first_legacy < first_skel, True)
        fix_lines = [ln for ln in lines if any(f"[{c}]" in ln for c in ("skeleton", "setup_state", "gitignore"))
                     and ("硬骨架缺" in ln or "运行时骨架缺" in ln or "初始化" in ln or ".gitignore 不存在" in ln)]
        check("旧目录在场：skeleton / setup_state / gitignore 的 error / warn 行都在（前提）", len(fix_lines) >= 3, True)
        # 上一条按**条数**判，而 skeleton 一家就贡献 2 行——少掉 setup_state 那行仍然够 3 条，
        # 断言照绿、它自称验着的前提却已不成立（实测过一次：余量从 1 掉到 0 而没人看见）。
        # 逐个点名才对得上那句话。
        check("… 且三个 check id 各自至少贡献一行（上一条按条数判，凑得出假绿）",
              sorted({c for c in ("skeleton", "setup_state", "gitignore")
                      if any(f"[{c}]" in ln for ln in fix_lines)}),
              ["gitignore", "setup_state", "skeleton"])
        check("… 这些行都不再指 project_scaffold", [ln for ln in fix_lines if "project_scaffold" in ln], [])
        check("… 这些行都改指 [legacy_state_dir]", all("[legacy_state_dir]" in ln for ln in fix_lines), True)
    finally:
        rm(tmp)
    for label, legacy, want in (("没迁移", True, "legacy_state_dir"), ("新项目", False, "新项目")):
        tmp = Path(tempfile.mkdtemp(prefix="wf-legacy-"))
        try:
            if legacy:
                put(tmp / OLD["memory"] / "shared" / "MEMORY.md", "# s\n")
            P = doc.Paths(tmp)
            ctx = {"paths": P, "scopes": [], "events": [], "malformed": [], "events_total": 0}
            msgs = [m for fn in (doc.check_notes_backlog, doc.check_memory_capacity, doc.check_events_parse,
                                 doc.check_sidecar_health) for _lv, m in fn(ctx)]
            check(f"{label} ⇒ runtime 组「不存在」四条的定性", all(want in m for m in msgs) and len(msgs) == 4, True)
            if legacy:
                check("没迁移 ⇒ 不再说「新项目」", [m for m in msgs if "新项目" in m], [])
        finally:
            rm(tmp)
    tmp = Path(tempfile.mkdtemp(prefix="wf-legacy-"))
    try:
        put(tmp / "projects" / "board.yaml", "tasks: []\n")
        put(tmp / NEW["state"] / "plugin-root.txt", "x\n")
        # 与上面那格同形（见那边的理由）：只含 `agents_md` 的文件已不再代表「装过的项目」
        put(tmp / NEW["state"] / "setup-state.json",
            json.dumps({"steps": {"scaffold": "ok", "agents_md": "ok"}}))
        text = doc.summarize(doc.run_all(tmp, "install")) or ""
        check("对照：旧目录不在时 skeleton 的修法照旧指 project_scaffold", any(
            "[skeleton]" in ln and "硬骨架缺" in ln and "project_scaffold" in ln for ln in text.splitlines()), True)
        check("对照：旧目录不在时不出 [legacy_state_dir] 行", "[legacy_state_dir]" in text, False)
    finally:
        rm(tmp)


def test_doctor_auto_memory_old_pointer():
    print("[doctor-am] auto-memory 的 scope 指针写着旧记忆目录 ⇒ 单列、写明手改；新路径照旧判死指针")
    tmp = Path(tempfile.mkdtemp(prefix="wf-am-"))
    try:
        proj, am = tmp / "proj", tmp / "am"
        put(proj / NEW["memory"] / "qa" / "MEMORY.md", "# qa\n")
        put(am / "MEMORY.md", "# idx\n\n"
            f"- **[scope 指针] dev 域** — `{OLD['memory']}/dev/MEMORY.md`\n"
            f"- **[scope 指针] pm 域** — `{NEW['memory']}/pm/MEMORY.md`\n"
            f"- **[scope 指针] qa 域** — `{NEW['memory']}/qa/MEMORY.md`\n")
        with mock.patch.object(doc, "_auto_memory_dir", lambda P: am):
            out = doc.check_auto_memory({"paths": doc.Paths(proj)})
        old_msg = next((m for lv, m in out if lv == "warn" and "旧记忆目录" in m), "")
        dead_msg = next((m for lv, m in out if lv == "warn" and "死指针" in m), "")
        check("旧记忆目录的指针单列一条 warn、点名它、写明迁移后手改",
              (f"{OLD['memory']}/dev/MEMORY.md" in old_msg, "手改" in old_msg), (True, True))
        check("… 它不混进死指针那条", f"{OLD['memory']}/dev/MEMORY.md" in dead_msg, False)
        check("新路径、文件不在 ⇒ 仍报死指针", f"{NEW['memory']}/pm/MEMORY.md" in dead_msg, True)
        check("新路径、文件在 ⇒ 不报", f"{NEW['memory']}/qa/MEMORY.md" in old_msg + dead_msg, False)
    finally:
        rm(tmp)
    # F2：旧位置上不是框架 scope 的指针是 CC 原生 `memory: project` 的正当位置——不单列、照旧按存在性判
    tmp = Path(tempfile.mkdtemp(prefix="wf-am-"))
    try:
        proj, am = tmp / "proj", tmp / "am"
        put(proj / NEW["memory"] / "qa" / "MEMORY.md", "# qa\n")
        put(proj / OLD["memory"] / "cc-reviewer" / "MEMORY.md", "# cc\n")
        put(am / "MEMORY.md", "# idx\n\n"
            f"- **[scope 指针] cc-reviewer** — `{OLD['memory']}/cc-reviewer/MEMORY.md`\n"
            f"- **[scope 指针] cc-gone** — `{OLD['memory']}/cc-gone/MEMORY.md`\n"
            f"- **[scope 指针] qa 域** — `{NEW['memory']}/qa/MEMORY.md`\n")
        with mock.patch.object(doc, "_auto_memory_dir", lambda P: am):
            out = doc.check_auto_memory({"paths": doc.Paths(proj)})
        warns = [m for lv, m in out if lv == "warn"]
        check("CC 原生指针 ⇒ 不单列「旧记忆目录」", [m for m in warns if "旧记忆目录" in m], [])
        check("CC 原生指针、文件在 ⇒ 不报死指针", any(f"{OLD['memory']}/cc-reviewer/" in m for m in warns), False)
        check("CC 原生指针、文件不在 ⇒ 照旧报死指针",
              any("死指针" in m and f"{OLD['memory']}/cc-gone/" in m for m in warns), True)
    finally:
        rm(tmp)
    # D4：有框架旧指针、其余指针都在 ⇒ 不再打「目标齐全」ok（那条 ok 会和旧指针 warn 自相矛盾）
    tmp = Path(tempfile.mkdtemp(prefix="wf-am-"))
    try:
        proj, am = tmp / "proj", tmp / "am"
        put(proj / NEW["memory"] / "qa" / "MEMORY.md", "# qa\n")
        put(am / "MEMORY.md", "# idx\n\n"
            f"- **[scope 指针] dev 域** — `{OLD['memory']}/dev/MEMORY.md`\n"
            f"- **[scope 指针] qa 域** — `{NEW['memory']}/qa/MEMORY.md`\n")
        with mock.patch.object(doc, "_auto_memory_dir", lambda P: am):
            out = doc.check_auto_memory({"paths": doc.Paths(proj)})
        check("有框架旧指针 ⇒ 单列 warn 在、不打「目标齐全」ok",
              (any(lv == "warn" and "旧记忆目录" in m for lv, m in out), any("目标齐全" in m for _lv, m in out)),
              (True, False))
    finally:
        rm(tmp)


def _mem_fixture(P, layout):
    """把 Proj 的旧记忆目录换成指定形态：`{相对旧记忆目录的文件: 内容}`；None = 空目录。"""
    rm(P / OLD["memory"])
    (P / OLD["memory"]).mkdir(parents=True)
    for rel, text in (layout or {}).items():
        put(P / OLD["memory"] / rel, text)
    git(P, "add", "-A")
    git(P, "commit", "-q", "-m", "memory layout")


def test_cc_native_memory():
    print("[cc-memory] 旧记忆目录只有 CC 原生 memory: project 目录 ⇒ 不搬、不判冲突；框架形态照搬；混合整搬并提示挪回")
    cc = {"code-reviewer/MEMORY.md": "# cc agent memory\n"}
    for label, layout, new_too, want_move in (
            ("只有 CC 子目录", cc, False, False),
            ("空目录", None, False, False),
            ("只有 CC 子目录、新记忆目录已在（迁移后 CC 又建回来）", cc, True, False),
            ("纯框架（shared/ ＋ 出厂角色）", {"shared/MEMORY.md": "# s\n", "dev/notes.md": "# n\n"}, False, True),
            ("只有出厂角色的 notes.md（没有 shared/）", {"dev/notes.md": "# n\n"}, False, True),
            ("混合（shared/ ＋ CC 子目录）", dict(cc, **{"shared/MEMORY.md": "# s\n"}), False, True)):
        with Proj(mirror=False) as P:
            _mem_fixture(P, layout)
            if new_too:
                put(P / NEW["memory"] / "shared" / "MEMORY.md", "# s\n")
            plan = plan_for(P)
            mem_items = [i for i in plan["items"] if i["id"] in ("R-memory", "R-conflict")]
            moved = any(a["op"] == "move_dir" and a["item"] == "R-memory" for a in plan["payload"]["actions"])
            check(f"{label} ⇒ 搬不搬", moved, want_move)
            if not want_move:
                check(f"{label} ⇒ R-memory 列一行「不搬」说明、没有冲突条目",
                      [(i["id"], i["status"], "不搬" in i["title"]) for i in mem_items], [("R-memory", "report", True)])
                info_lines = " ".join(ln for i in mem_items for ln in i["lines"])
                check(f"{label} ⇒ 说明条带 doctor 同一份 info（点名来历）",
                      ("memory: project" in info_lines) if layout else ("没有子目录" in info_lines), True)
                check(f"{label} ⇒ 报告项不再按「随目录搬」列它的子目录",
                      [i for i in plan["items"] if i["id"] == "L-report" and "非出厂角色子目录" in i["title"]], [])
                before = manifest_under(P, OLD["memory"])
                code, _out = _apply_out(P, plan)
                check(f"{label} ⇒ apply 退出码 0（不搬不算没做完）", code, 0)
                check(f"{label} ⇒ 旧记忆目录逐字节原地", manifest_under(P, OLD["memory"]), before)
                errs, _infos = _legacy_errors(P)
                check(f"{label} ⇒ apply 后 doctor legacy_state_dir 不报 error", errs, [])
            else:
                check(f"{label} ⇒ R-memory planned", [(i["id"], i["status"]) for i in mem_items], [("R-memory", "planned")])
            if label.startswith("混合"):
                rep = [i for i in plan["items"] if i["id"] == "L-report" and "非出厂角色子目录" in i["title"]]
                check("混合 ⇒ 报告项列出 CC 子目录并写明搬完挪回",
                      (bool(rep) and "code-reviewer/" in rep[0]["lines"], bool(rep) and "挪回" in rep[0]["title"]), (True, True))


# ---- 27. AGENTS.md「这个项目是什么」只剩占位的提示（plan 报告项 ＋ apply 收尾）----

def _goal_items(plan):
    return [i for i in plan["items"] if i["id"] == wm.AGENTS_GOAL_ITEM]


def test_agents_goal_note():
    print("[agents-goal] plan 报告项三种状态 ＋ 已填不报 ＋ 异常不挡 plan ＋ 确认码不变 ＋ apply 收尾再说一次")
    import project_scaffold as ps
    # ① 本次 apply 将建出：AGENTS.md 不在、CLAUDE.md 已提交且干净
    with Proj(mirror=False) as P:
        plan = wm.compute_plan(P)
        items = _goal_items(plan)
        check("将建出 AGENTS.md ⇒ 恰一条报告项、状态 report、说「本次 apply 将按模板建出」「迁移不搬」",
              (len(items), items[0]["status"] if items else None,
               "本次 apply 将按模板建出" in (items[0]["title"] if items else ""),
               "迁移不搬 CLAUDE.md 里的项目内容" in (items[0]["title"] if items else "")), (1, "report", True, True))
        check("将建出 ⇒ 写明「模型不代填」", doc.AGENTS_MD_NO_PROXY_EDIT in (items[0]["title"] if items else ""), True)
        with mock.patch.object(wm, "agents_goal_note", lambda *_a, **_k: None):
            silent = wm.compute_plan(P)
        check("报告项不进 payload：有它与没它的确认码相同",
              wm.confirm_code(plan["payload"]) == wm.confirm_code(silent["payload"]), True)
        check("（对照）没它时确实没有这一条", _goal_items(silent), [])
        code, out = run_door(["--migrate", "--project", str(P)])
        check("plan 输出里印出这一条（i 标记）", (code, f"i [{wm.AGENTS_GOAL_ITEM}]" in out), (0, True))
        # apply 收尾：apply 摘要不重印 report，收尾按盘面重判再说一次（AGENTS.md 此刻已是建出来的占位）
        plan2 = plan_for(P)
        code, out = _apply_out(P, plan2)
        lines = out.splitlines()
        tail = [i for i, ln in enumerate(lines) if ln.startswith("i ") and "仍是框架补建时的占位" in ln]
        done = [i for i, ln in enumerate(lines) if ln.startswith("完成后重开会话")]
        check("apply 收尾：退出码 0、占位提示恰一次、在「完成后重开会话」之前",
              (code, len(tail), bool(tail and done and tail[0] < done[0])), (0, 1, True))
    # ② AGENTS.md 已在且是骨架 / 已填 / 异常
    for label, setup, want in (("已在且是骨架", "skeleton", "仍是框架补建时的占位"),
                               ("已在且已填", "filled", None),
                               ("判定抛异常", "boom", "没判成")):
        with Proj(mirror=False) as P:
            ps.ensure_agents_md(P)
            if setup == "filled":
                f = P / "AGENTS.md"
                f.write_bytes(f.read_bytes().replace(ps.AGENTS_MD_FALLBACK_GOAL.encode("utf-8"), "演示项目".encode("utf-8"), 1))
            git(P, "add", "-A")
            git(P, "commit", "-q", "-m", "agents")
            if setup == "boom":
                with mock.patch.object(doc, "agents_md_goal_of", lambda *_a: (_ for _ in ()).throw(OSError("stub"))):
                    plan = wm.compute_plan(P)
            else:
                plan = wm.compute_plan(P)
            items = _goal_items(plan)
            if want is None:
                check(f"{label} ⇒ 不出这一条", items, [])
            else:
                check(f"{label} ⇒ 恰一条、含「{want}」、plan 照常出（有动作）",
                      (len(items), want in (items[0]["title"] if items else ""), bool(plan["payload"]["actions"])),
                      (1, True, True))
    # ③ AGENTS.md 不在、本次也不建（CLAUDE.md 有未提交改动 ⇒ 导入这次不加）
    with Proj(mirror=False) as P:
        put(P / "CLAUDE.md", (P / "CLAUDE.md").read_text(encoding="utf-8") + "用户未提交的一行\n")
        plan = wm.compute_plan(P)
        items = _goal_items(plan)
        check("不在且本次不建 ⇒ 恰一条、说「没有 AGENTS.md」「本次 apply 不建它」",
              (len(items), "没有 AGENTS.md" in (items[0]["title"] if items else ""),
               "本次 apply 不建它" in (items[0]["title"] if items else ""),
               any(a["op"] == "create_agents_md" for a in plan["payload"]["actions"])), (1, True, True, False))
    # ④ 本该建、中途停下没建成（停在建 AGENTS.md 那一步）⇒ apply 收尾说「没建成」，不说「本次 apply 不建它」
    with Proj(mirror=False) as P:
        plan = plan_for(P)
        real = wm._do_action

        def stop_at_agents(project, a, *rest):
            if a["op"] == "create_agents_md":
                raise RuntimeError("probe stop")
            return real(project, a, *rest)

        code, out = _apply_out(P, plan, stop_at_agents)
        tail = [ln for ln in out.splitlines() if ln.startswith("i ") and "没有 AGENTS.md" in ln]
        check("停在建 AGENTS.md 那一步 ⇒ 退出码 8、收尾恰一条、说「没建成」、不说「本次 apply 不建它」",
              (code, len(tail), bool(tail) and "没建成" in tail[0], bool(tail) and "本次 apply 不建它" in tail[0]),
              (8, 1, True, False))


# ---- 28. W3 候选不删 1.0.0 布局里夹在框架块后面的项目约束（TASK-167）----

# 1.0.0 原句的前半截（后半截点名了已退役的纪律文件名，本仓的退役残留闸不许它出现；判据只认前缀，不影响本组）
_V100_TAIL = "主 Claude 恒为潜在消费者，**不参与落点计数**。"
_V100_CONSTRAINTS = (
    "# demo\n\n## 项目目标\n\n做点什么\n\n## 项目特殊约束\n\n### 两套记忆分工\n\n"
    "| 记忆 | 消费者 | 装什么 | 维护 |\n|---|---|---|---|\n"
    "| auto-memory（CC 官方记忆目录，主会话注入） | 主 Claude | 用户偏好 | 主 Claude 自维护 |\n\n"
    "### 工种知识的落点判据（主 Claude 与 subagent 收尾时共用）\n\n"
    "落点由「**未来谁要读它**」决定。\n\n| 判据 | 落点 |\n|---|---|\n"
    "| 工种知识，未来场景可点名**恰 1 个角色域** | 该角色 `notes.md` |\n\n"
    "{tail}\n\n- 项目约束哨兵 PRV-CONS-167：改 src/ 必须同批更新 CHANGES\n\n## 业务目录速查\n\n- src/\n"
)


def test_w3_keeps_project_constraints():
    print("[w3] 1.0.0 布局：落点判据小节后面紧跟的项目约束不随框架块去掉；那一句被改写时退回整块判（能力边界）")
    sentinel = "项目约束哨兵 PRV-CONS-167"
    text = _V100_CONSTRAINTS.format(tail=_V100_TAIL)
    drops, names, remaining = wm.w3_candidate(text)
    cand = wm.drop_line_ranges(text, drops)
    check("1.0.0 原句在 ⇒ 候选保留项目约束", sentinel in cand, True)
    check("… 框架块照样去掉（记忆分工表行与落点判据表行都不在）、原句本身也去掉",
          ("| auto-memory" in cand, "| 工种知识" in cand, "主 Claude 恒为潜在消费者" in cand), (False, False, False))
    check("… 项目约束留在「## 项目特殊约束」标题之下、没有旧副本剩下", (sentinel in cand and cand.index("## 项目特殊约束") < cand.index(sentinel), remaining),
          (True, []))
    check("… 候选按 doctor 判「无旧副本」", doc._w3_residue_blocks(cand), [])
    # 能力边界：原句被改写 ⇒ 切不开，退回整块判（项目约束随块去掉——plan 条目提醒对比时手工保留）
    rewritten = _V100_CONSTRAINTS.format(tail="主会话不参与落点计数。")
    cand2 = wm.drop_line_ranges(rewritten, wm.w3_candidate(rewritten)[0])
    check("原句被改写 ⇒ 退回整块判：项目约束随块去掉（钉住能力边界）", sentinel in cand2, False)
    # 围栏里的原句不算切点（切点与标题同一个「围栏外」判据）
    fenced = _V100_CONSTRAINTS.format(tail="```\n" + _V100_TAIL + "\n```")
    cand3 = wm.drop_line_ranges(fenced, wm.w3_candidate(fenced)[0])
    check("原句只出现在围栏里 ⇒ 不切：项目约束随块去掉", sentinel in cand3, False)
    # plan 条目提醒：按块整块去掉，块里夹着自己的内容要对比时手工保留
    with Proj(mirror=False) as P:
        put(P / "CLAUDE.md", text)
        git(P, "commit", "-q", "-am", "v1.0.0 layout")
        items = [i for i in wm.compute_plan(P)["items"] if i["id"] == "W-w3-residue"]
        check("plan 的 W-w3-residue 条目提醒「对比时手工保留」", bool(items) and "对比时手工保留" in items[0]["title"], True)


def test_codex_seal(when):
    print(f"[seal] 套件封闭（{when}）：PATH 上有 codex 也解析不到；CODEX_HOME 是本进程的临时目录")
    exe, home = os.environ.get("WF_CODEX_EXE") or "", os.environ.get("CODEX_HOME") or ""
    check(f"封闭（{when}）：WF_CODEX_EXE 指向本套件临时目录下不存在的路径、CODEX_HOME 在本套件临时目录下",
          (bool(exe) and Path(exe).is_relative_to(_SEAL) and not Path(exe).exists(),
           bool(home) and Path(home).is_relative_to(_SEAL)), (True, True))
    d = Path(tempfile.mkdtemp(prefix="wf-migrate-fakepath-"))
    try:
        fake = d / ("codex.exe" if os.name == "nt" else "codex")
        fake.write_bytes(b"")
        if os.name != "nt":
            fake.chmod(0o755)
        env = dict(os.environ, PATH=str(d))
        bare = {k: v for k, v in env.items() if k != "WF_CODEX_EXE"}
        check(f"封闭对照（{when}）：不设 WF_CODEX_EXE 时那份假 codex 解析得到（下一格的前提）",
              wd.cas.find_codex_exe(bare) is not None, True)
        check(f"封闭（{when}）：PATH 上放一份假 codex，门照样解析不到它", wd.cas.find_codex_exe(env), None)
    finally:
        rm(d)


def main():
    test_codex_seal("开跑前")
    for t in (test_plan_zero_write, test_apply_happy, test_conflict_and_resume, test_guards, test_terminal_hint,
              test_session_signals, test_boundaries, test_link_fail_resume, test_nested_project, test_staged_inside,
              test_reports, test_literal_boundaries, test_printed_inverse_runs, test_unfinished_exit,
              test_residual_literals, test_code_paths_parser_loud, test_mirror_unrestorable, test_midstop_block,
              test_skipped_moved_path, test_link_commit_hint, test_pending_source_mismatch,
              test_located_rel_consumers, test_unknown_pending_item, test_link_hint_on_resume,
              test_doctor_legacy_state_dir, test_doctor_auto_memory_old_pointer, test_cc_native_memory,
              test_agents_goal_note, test_w3_keeps_project_constraints):
        try:
            t()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            FAILURES.append(f"{t.__name__} 抛异常: {type(e).__name__}: {e}")
    test_codex_seal("跑完")     # 中途有用例改了环境变量没复原，后面的用例就不再封闭——在这里说出来
    if FAILURES:
        print(f"\n{len(FAILURES)} failed: {FAILURES}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
