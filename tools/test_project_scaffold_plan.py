#!/usr/bin/env python3
"""`project_scaffold.py --print-plan` 的行为单测——只读预演那一层。

**被测的是确认页动作清单的事实来源。** 确认页要对「会被修改的已存在文件」逐个点名，
真机上漏掉过项目外那一处写入（用户级市场注册表）；预演把「凭记忆复述」换成「跑一条命令、
逐字贴输出」。预演说错或说漏，用户按一份不实的清单授权，而**那一页是这条链路上唯一的
事前授权点**——所以这一层的断言按爆炸半径档 4 写。

分组：
  A 往返一致性 —— 同一个夹具上先问 `plan_entries` 拿完整清单、再跑真写，比它与真写在文件
    系统上留下的**实际增改**。比的是 sha256 整树快照的差集，**不解析 stdout**：
    这样「预演与真写各写一份清单」这类漂移不靠自觉，当场红。三种夹具：全新 / 接入已有 /
    **已装项目重跑**（后者专抓「多报」——前两种夹具里骨架文件本就不存在，多报那一族不可见）。
  B 零写入 —— 整树快照 ＋ 写入原语拦截，**三种夹具各跑一遍**。两条角度不同：快照证
    「跑完之后什么都没多」，拦截证「中间一次写都没发生」——只有后者分得出「没写」与
    「写了又删」；而只在 A 夹具上跑则 B 路径的「已有文件被写坏」那一族整族不可达。
  C 露面规则 —— 用户级注册表恒在第三格（哪怕本次不需要改）、那一行必须带「**这是项目外**」
    的披露语；不带 --write-subscription 时订阅那两行一行都不出现。
  D 退出码对齐 —— 真跑会拒的两类（目标非空 / 订阅参数不成对），预演同样拒、同样的码。
  E 冲突口径 —— **同名市场、源不同**那一支：plan 说的必须和真写说的是同一件事，
    不许把「这个名字已被另一个框架副本占用」说成「已有同源条目 / 已是目标状态」。
  F 渲染分档 —— 「项目内·新建」折叠骨架项之后，**一项都不能少**（逐项数 + 折叠计数 == 总数）。
  G 第四格逐项点名 —— 「已存在·本脚本不覆盖」那一格必须逐项报出名字（用户自己写的
    `CLAUDE.md` / `AGENTS.md` 就在这一格），不是只报个数；且它只断言「本脚本不动它」，
    不替调用方说「接着会被谁写回」——那发生在预演跑完之后、本脚本之外，预演看不到，
    断言它就是在说自己不知道的事。

**夹具全部在临时目录内，`HOME` / `USERPROFILE` 一律指向夹具里的假主目录**——真实的
`~/.claude/**` 在本套件里只读都不读。
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]
CORE = FRAMEWORK_ROOT / "plugins" / "core"
SCAFFOLD = CORE / "scripts" / "project_scaffold.py"
sys.path.insert(0, str(CORE / "scripts"))

import _harness                        # noqa: E402
import project_scaffold as ps          # noqa: E402

MARKET = "workframe"
SOURCE = {"source": "github", "repo": "owner/repo"}

_RESULTS = []


def _say(ok, label, detail=""):
    _RESULTS.append(ok)
    print(f"[{'ok' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))


# ---------------------------------------------------------------- 夹具

def tree_snapshot(root):
    """整树 sha256 快照：`{相对路径: 内容 sha256}`，目录与目录链接记成 `<dir>` / `<dirlink>`（键带尾斜杠）。

    **不穿过目录链接。** 新建项目里 `.claude/skills` 是指向 `.agents/skills` 的 junction，而
    `Path.rglob` 在 Windows 上会穿过它——同一份文件因此被数两遍（`.agents/skills/…` 与
    `.claude/skills/…`），往返比对当场出现一个并不存在的「真写多写了一个文件」。
    这一条是本套件自己的往返组实证出来的，不是推理。
    """
    root = Path(root)
    out = {}
    if not root.exists():
        return out

    def walk(d, prefix):
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            rel = f"{prefix}{e.name}"
            if _harness.is_dir_link(e.path):
                out[rel + "/"] = "<dirlink>"
                continue
            if e.is_dir():
                out[rel + "/"] = "<dir>"
                walk(e.path, rel + "/")
                continue
            try:
                out[rel] = hashlib.sha256(Path(e.path).read_bytes()).hexdigest()
            except OSError as ex:
                out[rel] = f"<unreadable {type(ex).__name__}>"

    walk(root, "")
    return out


def snapshot_delta(before, after):
    """增 ＋ 改的相对路径集合（目录条目与备份产物不计——备份是写入的副产品，不是被点名的对象）。"""
    out = set()
    for k, v in after.items():
        if k.endswith("/"):
            continue
        if ".bak-" in k:
            continue
        if before.get(k) != v:
            out.add(k)
    return out


def make_params(install_location, with_subscription=True):
    p = {
        "project_name": "demo-proj",
        "one_line_goal": "一句话目标",
        "business_context": "业务背景",
        "project_type": "product-work",
        "role_profile": "ai-product",
    }
    if with_subscription:
        p["subscription"] = {"marketplace_name": MARKET, "source": SOURCE,
                             "install_location": str(install_location)}
    return p


def run_scaffold(target, params_path, extra, home):
    env = dict(os.environ)
    home = str(home)
    env["USERPROFILE"] = home
    env["HOME"] = home
    env["HOMEDRIVE"] = os.path.splitdrive(home)[0]
    env["HOMEPATH"] = os.path.splitdrive(home)[1]
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, str(SCAFFOLD), "--project", str(target)]
    if params_path:
        cmd += ["--params", str(params_path)]
    return subprocess.run(cmd + extra, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)


PLAN_GRID_PREFIXES = ("项目内·", "项目外·", "已存在·")


def plan_sections(stdout, target):
    """按格解析预演输出：`{格名: {items, notes, folded, head, scope, fold_text}}`。

    **只有本函数解析 stdout**——它测的正是输出本身。三类非条目行各自单独识别，混进
    `items` 就等于让「逐项点名」这件事看着已经成立：`（无）`、折叠行（`……` 开头，
    计数收进 `folded`、**整行原文另收进 `fold_text`**）、格级说明行（`↑` 开头，收进 `scope`）。
    `head` 是格头自报的总数，用来与「逐项数 + 折叠计数」对账。

    **`fold_text` 为什么必须收**：折叠行也是一句会被逐字贴进确认页、用户会读到的文案，
    与格级句、逐项说明**同一可达性**（都是 `print_plan` 里的一句硬编码）。只抽数字、
    丢掉正文时，任何写进折叠行的越界措辞都没有检测面——实证：把肯定式写回承诺写进
    这一行，全套断言一条不红。
    """
    out, cur = {}, None
    for ln in stdout.splitlines():
        if not ln.startswith("[plan]"):
            continue
        body = ln[len("[plan]"):]
        stripped = body.strip()
        if not stripped:
            continue
        if stripped.startswith(PLAN_GRID_PREFIXES):
            cur = stripped.split("（")[0]
            m = re.search(r"（(\d+) 项）", stripped)
            out[cur] = {"items": set(), "notes": {}, "folded": 0,
                        "head": int(m.group(1)) if m else None, "scope": "", "fold_text": ""}
            continue
        if cur is None or not body.startswith("    ") or stripped == "（无）":
            continue
        if stripped.startswith("……"):
            m = re.search(r"另 (\d+) 个", stripped)
            out[cur]["folded"] += int(m.group(1)) if m else 0
            out[cur]["fold_text"] += stripped
            continue
        if stripped.startswith("↑"):
            out[cur]["scope"] += stripped
            continue
        path, _sep, note = stripped.partition("  —— ")
        path = path.strip()
        try:
            path = Path(path).relative_to(Path(target)).as_posix()
        except ValueError:
            pass
        out[cur]["items"].add(path)
        out[cur]["notes"][path] = note
    return out


def plan_paths(stdout, target):
    """从预演输出里取各格的相对路径集合（`plan_sections` 的薄封装，解析只有一份）。"""
    return {k: v["items"] for k, v in plan_sections(stdout, target).items()}


def plan_sets(fx):
    """**直接问 `plan_entries` 拿四格的完整清单**，不经渲染、不解析 stdout。

    往返比对要的是「本次会碰的每一个文件」，而渲染层对「项目内·新建」与
    「已存在·本脚本不覆盖」两格做了分档折叠（见 `project_scaffold.plan_itemized`）——
    拿 stdout 比会把被折叠的骨架项误判成漏报。渲染层有没有把某一项弄丢，由 F 组
    （`test_render_fold_loses_nothing`）与 G 组的对账断言分别管。
    """
    params = json.loads(fx.params_path.read_text(encoding="utf-8"))
    buckets, untouched, warnings = ps.plan_entries(
        fx.target, params, params.get("subscription"), home_dir=fx.home)
    sets = {k: {str(path) for path, _n in v} for k, v in buckets.items()}
    notes = {str(path): (n or "") for v in buckets.values() for path, n in v}
    return sets, notes, untouched


class Fixture:
    """一个隔离的沙盒：假 HOME ＋ 目标目录 ＋ params 文件（都在临时目录内）。"""

    def __init__(self, with_subscription=True):
        self.root = Path(tempfile.mkdtemp(prefix="wf-plan-"))
        self.home = self.root / "home"
        self.home.mkdir()
        self.target = self.root / "proj"
        self.params_path = self.root / "params.json"
        self.params_path.write_text(
            json.dumps(make_params(self.root / "market", with_subscription),
                       ensure_ascii=False, indent=2), encoding="utf-8", newline="")

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def install(self):
        """先真装一遍——「已装项目重跑」形态的夹具。返回真跑的 CompletedProcess。"""
        r = run_scaffold(self.target, self.params_path,
                         ["--create-missing", "--write-subscription"], self.home)
        assert r.returncode == 0, f"夹具预装失败 rc={r.returncode}: {r.stdout[-400:]}"
        return r

    def seed_registry(self, source=None):
        reg = self.home / ".claude" / "plugins" / "known_marketplaces.json"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text(json.dumps(
            {MARKET: {"source": source or SOURCE,
                      "installLocation": str(self.root / "market"),
                      "lastUpdated": "2026-01-01T00:00:00.000Z"}},
            ensure_ascii=False, indent=2), encoding="utf-8", newline="")
        return reg


# ---------------------------------------------------------------- A 往返一致性

# 「项目 skills 的到达路径」在 plan 里是**一个目录条目**，真写落的是它下面的文件；
# 目录不进文件系统差集（差集只收文件），故从比较面里剔掉它，另证其下的 prd-style 在。
SKILLS_REACH = ".agents/skills"


# 「恒写但常常幂等」的三项：脚本每次都会调用写入函数，而内容没变时落盘字节相同，
# 于是它们出现在 plan 的「会碰」清单里、却不出现在文件系统差集里。这**不是幻影**——
# 判据 3 要求「不改也要露面」，这三项正是那条规则的产物（`.claude/settings.json` 的说明
# 里就写着「已是目标状态，本次不改」）。把它们列成常量，是为了让「幻影」这一族的其余成员
# 仍然会被抓住：清单之外的任何多报照样红。
IDEMPOTENT_ALWAYS_LISTED = {".claude/settings.json", ".workframe-config.json",
                            ".workframe/state/setup-state.json"}


def _roundtrip(label, fx, real_extra, allow_idempotent_extras=False):
    """通用往返：先问 plan 拿完整清单，再真写，比 plan 的项目内两格与真写的实际增改。

    **漏报恒不许**（真写动了而 plan 没说 ⇒ 确认页点名不全，这正是本任务要修的形态）。
    多报按夹具判：全新 / 接入两种夹具下应当逐项相等；已装重跑那一格允许且仅允许
    `IDEMPOTENT_ALWAYS_LISTED` 里那三项（理由见该常量）。
    """
    sets, notes, _u = plan_sets(fx)
    before_t, before_h = tree_snapshot(fx.target), tree_snapshot(fx.home)
    r = run_scaffold(fx.target, fx.params_path, real_extra, fx.home)
    _say(r.returncode == 0, f"{label}/真写 退出 0", f"rc={r.returncode} {r.stdout[-300:]}")
    actual = snapshot_delta(before_t, tree_snapshot(fx.target))
    inside = (sets[ps.PLAN_BUCKET_NEW] | sets[ps.PLAN_BUCKET_MOD]) - {SKILLS_REACH}
    missing = actual - inside
    extras = inside - actual
    _say(not missing, f"{label}/往返无漏报：真写动的每一个文件 plan 都说了", f"漏报={sorted(missing)}")
    allowed = IDEMPOTENT_ALWAYS_LISTED if allow_idempotent_extras else set()
    _say(extras <= allowed, f"{label}/往返无多报（幂等恒列项除外）",
         f"多报={sorted(extras - allowed)}")
    # 第四格自称「本脚本不动它们」——拿真写的实际增改验它，不是只看它自己怎么说。
    # 「已装重跑」那一格里这一条非空（骨架 + CLAUDE.md + AGENTS.md 全在第四格）。
    keep_touched = sets[ps.PLAN_BUCKET_KEEP] & actual
    _say(not keep_touched, f"{label}/第四格说「不动」的文件，真写确实一个都没动",
         f"被动过={sorted(keep_touched)}")
    return sets, notes, snapshot_delta(before_h, tree_snapshot(fx.home))


def test_roundtrip_a_path():
    """A 路径（目标不存在）。"""
    f = Fixture()
    try:
        sets, notes, home_delta = _roundtrip(
            "A", f, ["--create-missing", "--require-empty", "--write-subscription"])
        outside = sets[ps.PLAN_BUCKET_OUT]
        _say(len(outside) == 1, "A/项目外恰一项", f"{outside}")
        _say(all("known_marketplaces.json" in o for o in outside),
             "A/项目外那一项就是用户级市场注册表", f"{outside}")
        _say(home_delta == {".claude/plugins/known_marketplaces.json"},
             "A/真写在假 HOME 里动的恰好是那一个文件", f"{home_delta}")
        # 第三格逐行必须带「这是项目外」的披露语——本任务的核心诉求，它可以被静默改平
        reg_note = "".join(notes[o] for o in outside)
        _say("这是项目外" in reg_note,
             "A/第三格那一行带「这是项目外」的披露语", f"{reg_note[:120]}")
    finally:
        f.close()


def test_roundtrip_b_path():
    """B 路径：目标已有 settings.json / .gitignore / CLAUDE.md，且用户级注册表同名不同源。"""
    f = Fixture()
    try:
        f.target.mkdir()
        (f.target / ".claude").mkdir()
        (f.target / ".claude" / "settings.json").write_text(
            json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}}, indent=2),
            encoding="utf-8", newline="")
        (f.target / ".gitignore").write_text("node_modules/\n", encoding="utf-8", newline="")
        (f.target / "CLAUDE.md").write_text("# 用户自己的\n", encoding="utf-8", newline="")
        f.seed_registry(source={"source": "directory", "path": "/somewhere/else"})

        sets, notes, home_delta = _roundtrip("B", f, ["--write-subscription"])
        mod = sets[ps.PLAN_BUCKET_MOD]
        _say(".claude/settings.json" in mod, "B/已有 settings.json 进「改已有」格", f"{sorted(mod)}")
        _say(".gitignore" in mod, "B/已有 .gitignore 进「改已有」格", f"{sorted(mod)}")
        _say("CLAUDE.md" not in (mod | sets[ps.PLAN_BUCKET_NEW]),
             "B/用户自己的 CLAUDE.md 不在任何「会碰」格里（本脚本不覆盖它）")
        _say(home_delta == set(), "B/同名不同源时真写不动用户级注册表", f"{home_delta}")
        # **这条断言此前断的是错的东西**：它只断子串「本次不改」，而同一行前半句
        # 「已有同源条目」在这个夹具里是假的（源根本不同）。改为断结构事实：
        # 那一行必须点出**对方那个源**，且不许出现「同源 / 已是目标状态」这两种说法。
        reg_note = "".join(notes[o] for o in sets[ps.PLAN_BUCKET_OUT])
        _say("somewhere/else" in reg_note,
             "B/冲突时第三格点出了占用这个名字的那个源", f"{reg_note[:160]}")
        _say("已有同源条目" not in reg_note and "已是目标状态" not in reg_note,
             "B/冲突时不许说成「已有同源条目 / 已是目标状态」", f"{reg_note[:160]}")
    finally:
        f.close()


def test_roundtrip_installed_rerun():
    """**已装项目重跑**：骨架文件都已存在，plan 不许把它们报成「新建」。

    前两个夹具里骨架文件本就不存在，所以「多报」这一族在那里**不可达**——
    突变「把骨架层的 `if dst.exists(): continue` 短路掉」在它们上面全绿。
    多报是假红方向、比漏报轻，但它会让确认页在**补跑**场景下声称要新建几十个文件。
    """
    f = Fixture()
    try:
        f.install()
        sets, notes, home_delta = _roundtrip("已装重跑", f, ["--write-subscription"],
                                            allow_idempotent_extras=True)
        _say(sets[ps.PLAN_BUCKET_NEW] == set(),
             "已装重跑/「项目内·新建」为空（骨架都在，一项都不该报新建）",
             f"{sorted(sets[ps.PLAN_BUCKET_NEW])[:6]}")
        _say(home_delta == set(), "已装重跑/真写不动用户级注册表（幂等）", f"{home_delta}")
        # 冲突组的**项目 settings 侧对照**：真的同源时不许说成冲突。
        # 没有这一条，`landed_project_source` 坏成恒 None（⇒ 恒判冲突）时，
        # 冲突那几条断言照样全绿——它们只证「冲突时说了实话」，证不了「不冲突时没瞎说」。
        s_note = notes.get(".claude/settings.json", "")
        _say("已是目标状态" in s_note and "不是「已经对了」" not in s_note,
             "已装重跑/对照：真的同源时项目 settings 说「已是目标状态」、不谎报冲突",
             f"{s_note[:160]}")
    finally:
        f.close()


def test_roundtrip_more_forms():
    """把往返一致铺到另外三种形态——每一种都会走到 plan 里不同的分支。

    单一形态的往返组是结构性盲区：分支只有在它被走到时才会被比对。
    """
    # 形态：已装项目的 `.claude/skills` 是**真目录**（不是链接）——prd-style 的落点随之不同
    f = Fixture()
    try:
        f.target.mkdir()
        (f.target / ".claude" / "skills").mkdir(parents=True)
        f.seed_registry()
        sets, _n, _h = _roundtrip("真目录 skills", f, ["--write-subscription"])
        _say(any(s.startswith(".claude/skills/") for s in sets[ps.PLAN_BUCKET_NEW]),
             "真目录 skills/prd-style 落在 .claude/skills 下（不是中立目录）",
             f"{sorted(x for x in sets[ps.PLAN_BUCKET_NEW] if 'skills' in x)}")
    finally:
        f.close()

    # 形态：不带 --write-subscription —— 订阅两段整段不执行
    f = Fixture(with_subscription=False)
    try:
        sets, _n, home_delta = _roundtrip("无订阅", f, ["--create-missing"])
        _say(sets[ps.PLAN_BUCKET_OUT] == set(), "无订阅/第三格为空", f"{sets[ps.PLAN_BUCKET_OUT]}")
        _say(home_delta == set(), "无订阅/真跑没碰假 HOME 一个字节", f"{home_delta}")
    finally:
        f.close()

    # 形态：.gitignore 已有 managed marker 却缺条目 —— plan 走「本脚本不改它，只报告」那一支
    f = Fixture()
    try:
        f.target.mkdir()
        (f.target / ".gitignore").write_text(
            "node_modules/\n" + ps.GITIGNORE_MANAGED_BEGIN + "\n"
            + "tmp/\n" + ps.GITIGNORE_MANAGED_END + "\n",
            encoding="utf-8", newline="")
        f.seed_registry()
        sets, _n, _h = _roundtrip("marker 缺条目", f, ["--write-subscription"])
        _say(".gitignore" not in (sets[ps.PLAN_BUCKET_NEW] | sets[ps.PLAN_BUCKET_MOD]),
             "marker 缺条目/plan 说不改 .gitignore（与真写一致：那一支只报告不改）")
    finally:
        f.close()


# ---------------------------------------------------------------- B 零写入

def test_zero_write_snapshot():
    """跑完之后：目标目录仍不存在、假 HOME 整树不变、params 文件字节不变。"""
    f = Fixture()
    try:
        reg = f.seed_registry()
        before_h = tree_snapshot(f.home)
        before_p = hashlib.sha256(f.params_path.read_bytes()).hexdigest()
        before_root = tree_snapshot(f.root)
        r = run_scaffold(f.target, f.params_path,
                         ["--create-missing", "--require-empty", "--write-subscription",
                          "--print-plan"], f.home)
        _say(r.returncode == 0, "零写入/退出 0", f"rc={r.returncode}")
        _say(not f.target.exists(), "零写入/目标目录本身没有被创建")
        _say(tree_snapshot(f.home) == before_h, "零写入/假 HOME 整树 sha256 不变")
        _say(hashlib.sha256(f.params_path.read_bytes()).hexdigest() == before_p,
             "零写入/params 文件字节不变")
        _say(tree_snapshot(f.root) == before_root,
             "零写入/参数文件所在目录整树不变（备份也没落进来）")
        _say(reg.exists(), "零写入/预置的用户级注册表还在")
    finally:
        f.close()


class WriteBarrier:
    """把写入原语全换成「一调就抛」——**这条才分得出「没写」与「写了又删」**。

    整树快照对「写了又删」是盲的（删干净了前后快照相同），而只读契约要的是「一次都没写」。
    覆盖面：`Path` 的写 / 建 / 删 / 改名 / 建链接、`shutil` 的拷贝、`os` 的建目录与建链接、
    以及 `builtins.open` 的一切非只读模式。
    """

    PATH_METHODS = ("write_text", "write_bytes", "mkdir", "touch", "unlink", "rmdir",
                    "rename", "replace", "symlink_to", "chmod")
    SHUTIL_FUNCS = ("copy", "copy2", "copyfile", "copytree", "move", "rmtree")
    OS_FUNCS = ("mkdir", "makedirs", "remove", "rename", "replace", "symlink", "rmdir", "unlink")

    def __init__(self):
        self.calls = []

    def __enter__(self):
        import builtins
        self._saved = []

        def trap(where):
            def _f(*a, **k):
                self.calls.append(f"{where}{a[:2]}")
                raise AssertionError(f"只读预演里调用了写入原语：{where}")
            return _f

        for name in self.PATH_METHODS:
            self._saved.append((Path, name, getattr(Path, name)))
            setattr(Path, name, trap(f"Path.{name}"))
        for name in self.SHUTIL_FUNCS:
            self._saved.append((shutil, name, getattr(shutil, name)))
            setattr(shutil, name, trap(f"shutil.{name}"))
        for name in self.OS_FUNCS:
            self._saved.append((os, name, getattr(os, name)))
            setattr(os, name, trap(f"os.{name}"))
        real_open = builtins.open

        def guarded_open(file, mode="r", *a, **k):
            if any(c in mode for c in "wax+"):
                self.calls.append(f"open({file!r}, {mode!r})")
                raise AssertionError(f"只读预演里以写模式打开了 {file!r}（mode={mode!r}）")
            return real_open(file, mode, *a, **k)

        self._saved.append((builtins, "open", real_open))
        builtins.open = guarded_open
        return self

    def __exit__(self, *exc):
        for obj, name, orig in reversed(self._saved):
            setattr(obj, name, orig)
        return False


def _b_path_fixture():
    """B 路径夹具：目标已有 settings.json / .gitignore / CLAUDE.md / .workframe-config.json。"""
    f = Fixture()
    f.target.mkdir()
    (f.target / ".claude").mkdir()
    (f.target / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"allow": ["Bash(ls:*)"]}}, indent=2),
        encoding="utf-8", newline="")
    (f.target / ".gitignore").write_text("node_modules/\n", encoding="utf-8", newline="")
    (f.target / "CLAUDE.md").write_text("# 用户自己的\n", encoding="utf-8", newline="")
    (f.target / ".workframe-config.json").write_text(
        json.dumps({"project_name": "old"}, indent=2), encoding="utf-8", newline="")
    f.seed_registry()
    return f


def _installed_fixture():
    f = Fixture()
    f.install()
    return f


ZERO_WRITE_FIXTURES = (
    ("A 全新", lambda: Fixture()),
    ("B 已有项目", _b_path_fixture),
    ("已装重跑", _installed_fixture),
)


def test_zero_write_barrier():
    """写入原语拦截：`plan_entries` 全程一次都没调过任何写入原语。**三种夹具各一遍。**

    只在 A 夹具上跑是不够的：那里 `.workframe-config.json` / `CLAUDE.md` / `settings.json`
    根本不存在，于是「对**已有**文件做读写」这一族分支整族不可达——
    突变「已存在时 `open(cfg, "a").close()`」（字节不变，快照天然盲）在 A 夹具上全绿。
    而 B 路径恰恰是「有既有文件可以被写坏」的那一侧。
    """
    for label, make in ZERO_WRITE_FIXTURES:
        f = make()
        try:
            params = json.loads(f.params_path.read_text(encoding="utf-8"))
            sub = params["subscription"]
            barrier = WriteBarrier()
            err = None
            with barrier:
                try:
                    buckets, untouched, warnings = ps.plan_entries(
                        f.target, params, sub, home_dir=f.home)
                except AssertionError as e:
                    err = e
            _say(err is None, f"零写入/拦截（{label}）：plan_entries 一次都没写",
                 f"{err} / 调用记录={barrier.calls[:3]}")
            if err is None:
                total = sum(len(v) for v in buckets.values())
                # 下限取「已装重跑」那一格的实际形态（骨架都在，只剩恒列的那几项），
                # 目的是证「不是提前崩掉才没写」，不是对账项数
                _say(total >= 3, f"零写入/拦截态下（{label}）仍算出了清单（不是提前崩掉才没写）",
                     f"{total} 项")
        finally:
            f.close()


def test_zero_write_snapshot_all_fixtures():
    """整树快照：三种夹具下跑 `--print-plan`（真 CLI），目标目录与假 HOME 逐字节不变。"""
    for label, make in ZERO_WRITE_FIXTURES:
        f = make()
        try:
            extra = ["--create-missing", "--write-subscription", "--print-plan"]
            before_t, before_h = tree_snapshot(f.target), tree_snapshot(f.home)
            r = run_scaffold(f.target, f.params_path, extra, f.home)
            _say(r.returncode == 0, f"零写入/CLI（{label}）退出 0", f"rc={r.returncode} {r.stdout[-300:]}")
            _say(tree_snapshot(f.target) == before_t, f"零写入/目标目录整树不变（{label}）")
            _say(tree_snapshot(f.home) == before_h, f"零写入/假 HOME 整树不变（{label}）")
        finally:
            f.close()


def test_barrier_itself_bites():
    """拦截器自证：同一个夹具上跑真写路径，拦截器必须当场抛——否则上一条是空真的。"""
    f = Fixture()
    try:
        f.target.mkdir()
        bit = False
        with WriteBarrier():
            try:
                ps.ensure_project_scaffold(f.target, None)
            except AssertionError:
                bit = True
            except Exception:
                bit = True
        _say(bit, "零写入/拦截器对真写路径确实咬得住（反证：它不是恒不触发）")
    finally:
        f.close()


# ---------------------------------------------------------------- C 露面规则

def test_registry_always_shows():
    """用户级注册表恒在第三格——本次需要改、与本次不需要改，两种都要露面。"""
    for label, seed in (("需要改（本机还没注册过）", False), ("不需要改（已有同源条目）", True)):
        f = Fixture()
        try:
            if seed:
                f.seed_registry()
            r = run_scaffold(f.target, f.params_path,
                             ["--create-missing", "--require-empty", "--write-subscription",
                              "--print-plan"], f.home)
            outside = plan_paths(r.stdout, f.target).get("项目外·改已有", set())
            hit = any("known_marketplaces.json" in p for p in outside)
            _say(hit, f"露面/用户级注册表在第三格（{label}）", f"{outside}")
            if seed:
                _say("本次不改" in r.stdout,
                     "露面/不需要改时写明「本次不改」而不是整条消失")
        finally:
            f.close()


def test_no_subscription_no_lines():
    """不带 --write-subscription：订阅那两行一行都不出现，第三格为空。"""
    f = Fixture(with_subscription=False)
    try:
        r = run_scaffold(f.target, f.params_path,
                         ["--create-missing", "--require-empty", "--print-plan"], f.home)
        _say(r.returncode == 0, "露面/无订阅时 plan 退出 0", f"rc={r.returncode} {r.stdout[-300:]}")
        buckets = plan_paths(r.stdout, f.target)
        inside = buckets.get("项目内·新建", set()) | buckets.get("项目内·改已有", set())
        _say(".claude/settings.json" not in inside,
             "露面/无订阅时项目 settings 那一行不出现", f"{sorted(inside)[:5]}")
        _say("known_marketplaces.json" not in r.stdout,
             "露面/无订阅时用户级注册表那一行不出现")
        _say(buckets.get("项目外·改已有", set()) == set(),
             "露面/无订阅时第三格为空", f"{buckets.get('项目外·改已有')}")
    finally:
        f.close()


# ---------------------------------------------------------------- D 退出码对齐

def test_exit_codes_aligned():
    """真跑会拒的情况，预演同样拒、同样的退出码——否则确认页给用户看的是一份跑不通的计划。"""
    # 目标非空 + --require-empty ⇒ 3
    f = Fixture()
    try:
        f.target.mkdir()
        (f.target / "src.py").write_text("x = 1\n", encoding="utf-8", newline="")
        a = run_scaffold(f.target, f.params_path,
                         ["--require-empty", "--write-subscription"], f.home)
        b = run_scaffold(f.target, f.params_path,
                         ["--require-empty", "--write-subscription", "--print-plan"], f.home)
        _say(a.returncode == 3 and b.returncode == 3,
             "退出码/目标非空：真跑与预演都退 3", f"real={a.returncode} plan={b.returncode}")
        _say(not (f.target / ".workframe-config.json").exists(),
             "退出码/被拒之后预演没在目标目录里留下任何骨架")
    finally:
        f.close()

    # params 有 subscription 块却没带 --write-subscription ⇒ 1
    f = Fixture()
    try:
        a = run_scaffold(f.target, f.params_path, ["--create-missing"], f.home)
        b = run_scaffold(f.target, f.params_path, ["--create-missing", "--print-plan"], f.home)
        _say(a.returncode == 1 and b.returncode == 1,
             "退出码/订阅参数不成对（有块无开关）：真跑与预演都退 1",
             f"real={a.returncode} plan={b.returncode}")
        _say(not f.target.exists(),
             "退出码/被拒之后预演没有创建目标目录")
    finally:
        f.close()

    # 带 --write-subscription 却没有 subscription 块 ⇒ 1
    f = Fixture(with_subscription=False)
    try:
        a = run_scaffold(f.target, f.params_path,
                         ["--create-missing", "--write-subscription"], f.home)
        b = run_scaffold(f.target, f.params_path,
                         ["--create-missing", "--write-subscription", "--print-plan"], f.home)
        _say(a.returncode == 1 and b.returncode == 1,
             "退出码/订阅参数不成对（有开关无块）：真跑与预演都退 1",
             f"real={a.returncode} plan={b.returncode}")
    finally:
        f.close()


# ---------------------------------------------------------------- E 冲突口径

def test_conflict_same_name_different_source():
    """**同名市场、源不同**：plan 说的必须和真写说的是同一件事。

    真写那条路径为这一格专门分了支，并在代码注释里写着它是「本段代码后果最重的一格」、
    说「已经对了」是**假安心**。预演此前把它说成「已有同源条目 / 已是目标状态」——
    成因是「改不改」共用了 `would_change`，而「**为什么**不改」这个派生结论被第二次手写。
    现在两者都问 `_settings_io.landed_*`。

    后果不是措辞问题：确认页是这条链路上唯一的事前授权点，用户读到「一切正常」而装完之后
    项目订阅的是**另一个框架副本**。
    """
    other = {"source": "github", "repo": "someone-else/other-workframe"}
    # 子形态 1：项目 settings 也已有冲突声明（两处都走「不改」档）
    f = Fixture()
    try:
        f.target.mkdir()
        (f.target / ".claude").mkdir()
        (f.target / ".claude" / "settings.json").write_text(
            json.dumps({"extraKnownMarketplaces": {MARKET: {"source": other}},
                        "enabledPlugins": {f"core@{MARKET}": True}}, indent=2),
            encoding="utf-8", newline="")
        f.seed_registry(source=other)
        sets, notes, _u = plan_sets(f)
        s_note = notes.get(".claude/settings.json", "")
        r_note = "".join(notes[o] for o in sets[ps.PLAN_BUCKET_OUT])
        for what, note in (("项目 settings", s_note), ("用户级注册表", r_note)):
            _say("other-workframe" in note,
                 f"冲突/{what} 那一行点出了占用它的那个源", f"{note[:160]}")
            _say("已有同源条目" not in note and "已是目标状态" not in note,
                 f"冲突/{what} 不许说成「已有同源条目 / 已是目标状态」", f"{note[:160]}")
            _say("占用" in note or "没有被写入" in note,
                 f"冲突/{what} 正面说出了「被占用 / 没写进去」", f"{note[:160]}")
        _say("不是「已经对了」" in s_note,
             "冲突/项目 settings 复用了真写那句「这不是「已经对了」」", f"{s_note[:160]}")
        # 与真写并排：同一夹具下真写也必须认为这是冲突
        r = run_scaffold(f.target, f.params_path, ["--write-subscription"], f.home)
        _say("保留原声明不覆盖" in r.stdout and "保留原注册不覆盖" in r.stdout,
             "冲突/真写在同一夹具下确认两处都是冲突（反证夹具造对了）", f"rc={r.returncode}")
        landed = json.loads((f.target / ".claude" / "settings.json").read_text(encoding="utf-8"))
        _say(landed["extraKnownMarketplaces"][MARKET]["source"] == other,
             "冲突/落盘核对：本次选的源确实没被写进去（plan 说的是真的）")
    finally:
        f.close()

    # 子形态 2：只有注册表冲突，项目 settings 要新建 —— 证两处判据各自独立、没有互相搭车
    f = Fixture()
    try:
        f.seed_registry(source=other)
        sets, notes, _u = plan_sets(f)
        r_note = "".join(notes[o] for o in sets[ps.PLAN_BUCKET_OUT])
        _say("other-workframe" in r_note and "已有同源条目" not in r_note,
             "冲突/只有注册表冲突时它照样说实话", f"{r_note[:160]}")
        _say(".claude/settings.json" in sets[ps.PLAN_BUCKET_NEW],
             "冲突/同一次里项目 settings 仍正常报「新建」（两处判据互不搭车）")
    finally:
        f.close()

    # 对照组：真的同源 —— 证上面那两条「不许出现」不是空真的
    f = Fixture()
    try:
        f.seed_registry()
        sets, notes, _u = plan_sets(f)
        r_note = "".join(notes[o] for o in sets[ps.PLAN_BUCKET_OUT])
        _say("已有同源条目" in r_note,
             "冲突/对照组：真的同源时它确实说「已有同源条目」（否则上面两条是空真的）",
             f"{r_note[:160]}")
    finally:
        f.close()


# ---------------------------------------------------------------- F 渲染分档

def test_render_fold_loses_nothing():
    """「项目内·新建」折叠骨架项之后，**一项都不能少**。

    往返组比的是 `plan_entries` 的返回值（完整清单），所以渲染层单独需要这一条：
    逐项打出来的行数 + 折叠那一行报的计数 == `plan_entries` 的真实项数。
    另证折叠**只发生在这一格**：另外两格每一行都动你已有的东西，一条都不许被计数吃掉。
    """
    f = Fixture()
    try:
        f.seed_registry()
        sets, _n, _u = plan_sets(f)
        r = run_scaffold(f.target, f.params_path,
                         ["--create-missing", "--require-empty", "--write-subscription",
                          "--print-plan"], f.home)
        _say(r.returncode == 0, "分档/plan 退出 0", f"rc={r.returncode}")
        rendered = plan_paths(r.stdout, f.target)
        shown = rendered.get("项目内·新建", set())
        folded = 0
        for ln in r.stdout.splitlines():
            if "个骨架文件" in ln:
                folded = int(re.search(r"另 (\d+) 个骨架文件", ln).group(1))
        total = len(sets[ps.PLAN_BUCKET_NEW])
        _say(len(shown) + folded == total,
             "分档/逐项数 + 折叠计数 == plan_entries 的真实项数",
             f"{len(shown)} + {folded} != {total}")
        _say(f"（{total} 项）" in r.stdout,
             "分档/格头报的仍是真实总数，不是折叠后的数", f"total={total}")
        _say(all(ps.plan_itemized(s) for s in shown),
             "分档/逐项露面的都是判据说该露面的那几类", f"{sorted(shown)}")
        _say({".claude/settings.json", ".gitignore", "CLAUDE.md", "AGENTS.md",
              ".workframe-config.json"} <= shown | rendered.get("项目内·改已有", set()),
             "分档/五个受保护资产一个都没被折叠掉", f"{sorted(shown)}")
        # 另外两格恒逐项：把注册表那一行折掉就等于本任务白做
        _say(len(rendered.get("项目外·改已有", set())) == len(sets[ps.PLAN_BUCKET_OUT]),
             "分档/项目外那一格逐项展开、没有被折叠")
    finally:
        f.close()


# ---------------------------------------------------------------- G 第四格逐项点名

# 渲染层的格头字面**写死在这里**，不从 `ps.PLAN_BUCKET_KEEP` 取：这一行是用户在授权页上
# 读到的文本，改名就该当场红一次，而拿被测常量当期望的写法对改名恒绿。
KEEP_HEAD = "已存在·本脚本不覆盖"
# 「必须逐项露面」的期望清单同理**另写一份**，不调 `ps.plan_itemized`——判据收缩时
# 拿它当期望的断言会跟着一起收缩，恒绿（同族写法在本套件里已踩过一次）。
KEEP_ITEMIZED_EXPECTED = {"CLAUDE.md", "AGENTS.md", ".gitignore",
                          ".workframe-config.json", ".claude/settings.json"}
KEEP_ITEMIZED_PREFIX_EXPECTED = (".agents/skills", ".claude/skills")

# 第四格里**不许**出现的肯定式承诺：写下「随后会按合并稿写回」这类话，就是预演替调用方
# 断言一件它看不到的事（那发生在本脚本之外、本次运行之后）——而那正是这一格设计要防的。
# **正向 token 的合取挡不住这一族**：把承诺插进句中时，`本脚本不动它们` 与 `调用方`
# 两个字面照样都在，断言照样绿（独立验证方的突变实证）。所以另配一条负向断言。
KEEP_PROMISE_WORDS = ("会写回", "会按合并稿", "随后会", "接着会", "一定会", "会覆盖", "会被改写")
# 非空真对照样本：检测器对这句（就是那组突变写进去的原文）必须响，否则上面那条是空真的
KEEP_PROMISE_SAMPLE = "↑ 这一格：**本脚本不动它们**，装机流程随后会按合并稿把它们写回。调用方"


def _expected_itemized(p):
    return p in KEEP_ITEMIZED_EXPECTED or p.startswith(KEEP_ITEMIZED_PREFIX_EXPECTED)


def keep_promise_hits(sec):
    """第四格里命中的肯定式承诺词。检测面 = **格级句 ＋ 逐项说明 ＋ 折叠行原文**。

    三者同一可达性（都是 `print_plan` 里的一句硬编码文案、都会被逐字贴进确认页），
    漏掉任何一处都是干净的假绿。

    **两个夹具各调一次，缺一不可**：B 路径夹具里第四格**没有折叠行**（两项都逐项露面，
    `fold_text` 恒为空），只在它上面断言时，折叠行那一半**结构性不可达**——实测把承诺
    写进折叠行，那个夹具上一条都不红；折叠行只在「已装重跑」夹具里存在（32 项被折叠）。

    **命中项带面名**（`词@面`）：三处合成一个字符串再匹配时，红灯文案说不出是哪一处越界，
    而改格级句与改折叠行的修法不在同一个地方。
    """
    faces = (("格级句", sec.get("scope", "")),
             ("逐项说明", "".join(sec.get("notes", {}).values())),
             ("折叠行", sec.get("fold_text", "")))
    return [f"{w}@{face}" for face, text in faces for w in KEEP_PROMISE_WORDS if w in text]


def _keep_grid_fixture():
    """目标已有用户自己写的 CLAUDE.md 与 AGENTS.md —— scaffold 对这两个一律跳过不覆盖。"""
    f = Fixture()
    f.target.mkdir()
    (f.target / "CLAUDE.md").write_text("# 我自己的项目入口\n\n- 我自己的约定\n",
                                        encoding="utf-8", newline="")
    (f.target / "AGENTS.md").write_text("# 我自己的判据\n", encoding="utf-8", newline="")
    (f.target / ".claude").mkdir()
    (f.target / ".claude" / "settings.json").write_text(
        json.dumps({"env": {"TZ": "Asia/Shanghai"}}, indent=2), encoding="utf-8", newline="")
    (f.target / ".gitignore").write_text("node_modules/\n", encoding="utf-8", newline="")
    f.seed_registry()
    return f


def test_keep_grid_names_every_file():
    """「已存在·本脚本不覆盖」这一格**逐项点名**，不是报一个计数。

    真机上它曾只打「另有 1 个文件已存在」——而那一个正是用户自己写的 `CLAUDE.md`：
    确认页里「改写用户既有文件」标准最严的一项，在机器清单里连名字都不出现。

    **这一格能断言的只有一句「本脚本不动它」。** 「它跑完之后还有谁来改这个文件」发生在
    **本脚本之外、本次运行之后**，预演看不到也无从断言——所以那一句必须由调用方说，
    plan 不替它回答。本组最后几条断言钉的就是这条边界：正向钉格级说明里「不动 ＋ 由调用方讲」
    两件事都在，反向钉这一格**不许出现肯定式的写回 / 备份承诺**（负向那两条各带一个
    非空真的对照：承诺检测器对样本确实会响、`备份到` 在「改已有」格里确实出现）。

    **承诺那一条的能力边界（读这段断言之前先读这句）**：它是一张**字面表**
    （`KEEP_PROMISE_WORDS`），**同义改写绕得过**——独立验证方实测只换两个词
    （「随后会」→「稍后」、「会按合并稿」→「依合并稿」）即可全绿。不扩词表是**有意的**：
    再宽就会把正常措辞判红，而假红会让人不敢信这一格。它**覆盖的面**是第四格的
    **格级句 ＋ 逐项说明 ＋ 折叠行原文**三处（判定式见 `keep_promise_hits`；早期版本漏掉
    折叠行，承诺写进那一行时一条都不红，已补）。**折叠行那一半在本夹具上不可达**——
    这里的第四格没有折叠行，它在「已装重跑」那个用例里断言。
    面之外与词表之外的越界措辞**这条断言看不见，只能靠人读**。
    """
    f = _keep_grid_fixture()
    try:
        sets, notes, untouched = plan_sets(f)
        keep = sets[ps.PLAN_BUCKET_KEEP]
        _say({"CLAUDE.md", "AGENTS.md"} <= keep,
             "第四格/数据层：用户自己的 CLAUDE.md 与 AGENTS.md 都在名单里", f"{sorted(keep)}")
        _say(untouched == len(keep),
             "第四格/返回的计数就是这份名单的长度（计数与名单同源，不会一个说 1 一个不报名）",
             f"untouched={untouched} len={len(keep)}")
        overlap = keep & (sets[ps.PLAN_BUCKET_NEW] | sets[ps.PLAN_BUCKET_MOD]
                          | sets[ps.PLAN_BUCKET_OUT])
        _say(not overlap, "第四格/与前三格无交集（同一个文件不会既说要动又说不动）",
             f"{sorted(overlap)}")

        r = run_scaffold(f.target, f.params_path, ["--write-subscription", "--print-plan"], f.home)
        _say(r.returncode == 0, "第四格/plan 退出 0", f"rc={r.returncode} {r.stdout[-300:]}")
        secs = plan_sections(r.stdout, f.target)
        _say(KEEP_HEAD in r.stdout, "第四格/格头字面在输出里", f"{r.stdout[-400:]}")
        sec = secs.get(KEEP_HEAD, {"items": set(), "notes": {}, "folded": 0,
                                   "head": None, "scope": "", "fold_text": ""})
        # ↓ 本条就是「点名退化回只报个数」那一格：折叠行与格级说明行都不算条目（见 plan_sections）
        _say({"CLAUDE.md", "AGENTS.md"} <= sec["items"],
             "第四格/渲染层逐项点名了 CLAUDE.md 与 AGENTS.md（不是「另有 N 个文件」）",
             f"逐项={sorted(sec['items'])} 折叠={sec['folded']}")
        _say(len(sec["items"]) + sec["folded"] == sec["head"] == len(keep),
             "第四格/逐项数 + 折叠计数 == 格头总数 == 数据层真实项数",
             f"{len(sec['items'])} + {sec['folded']} vs head={sec['head']} vs {len(keep)}")
        _say("本脚本跳过" in sec["notes"].get("CLAUDE.md", ""),
             "第四格/CLAUDE.md 那一行写明了本脚本拿它怎么办，不是一个裸路径",
             f"{sec['notes'].get('CLAUDE.md', '')[:160]}")
        _say("本脚本不动它们" in sec["scope"] and "调用方" in sec["scope"],
             "第四格/格级说明把「本脚本不动」与「之后谁写回由调用方说」分开讲",
             f"{sec['scope'][:200]}")
        # 反向：不许在这一格替调用方承诺备份 / 写回。**同一份输出里「改已有」那格确实有
        # 这个字面**，所以上一句不是空真的——两条一起才成立。
        keep_notes = "".join(sec["notes"].values())
        mod_notes = "".join(secs.get("项目内·改已有", {"notes": {}})["notes"].values())
        _say("备份到" not in keep_notes,
             "第四格/不替调用方承诺备份（本脚本压根不碰它们，谈不上备份）", f"{keep_notes[:160]}")
        _say("备份到" in mod_notes,
             "第四格/对照：同一次输出里「改已有」那格确实说了落备份（证上一条不是空真的）",
             f"{mod_notes[:160]}")
        # 负向：这一格不许肯定式地说「接着会被谁写回」。正向那条是两个 token 的合取，
        # 承诺句插进去时两个 token 都还在、它照样绿（实证），所以这一条不可省。
        # 本夹具的第四格**没有折叠行**（两项都逐项露面），所以这里只行使得到检测面的
        # 前两半；折叠行那一半在「已装重跑」夹具上断言（见 `keep_promise_hits` 的说明）。
        promised = keep_promise_hits(sec)
        _say(not promised,
             "第四格/不出现肯定式的写回承诺（那一步在预演之外、预演看不到）", f"命中={promised}")
        _say(any(w in KEEP_PROMISE_SAMPLE for w in KEEP_PROMISE_WORDS),
             "第四格/对照：承诺检测器对承诺式写法确实会响（证上一条不是空真的）")
    finally:
        f.close()


def test_keep_grid_folds_and_stays_empty_when_nothing_exists():
    """第四格的另外两种形态：**已装重跑**（几十项，折叠必须起作用）与 **A 路径**（恒空）。

    只在「已有几个文件」那一种形态上测，会同时漏掉两族：折叠在这一格从未被执行过；
    「这一格恒非空」这种退化也没有对照组抓得住。
    """
    f = _installed_fixture()
    try:
        sets, _n, _u = plan_sets(f)
        r = run_scaffold(f.target, f.params_path, ["--write-subscription", "--print-plan"], f.home)
        sec = plan_sections(r.stdout, f.target).get(KEEP_HEAD)
        _say(sec is not None, "第四格/已装重跑：这一格在", f"rc={r.returncode} {r.stdout[-300:]}")
        if sec:
            _say({"CLAUDE.md", "AGENTS.md", ".gitignore"} <= sec["items"],
                 "第四格/已装重跑：三个用户可感知的文件逐项露面，没被几十个骨架项淹掉",
                 f"{sorted(sec['items'])}")
            _say(sec["folded"] > 0,
                 "第四格/已装重跑：骨架项确实被折叠了（否则「折叠不丢项」那条是空真的）",
                 f"折叠={sec['folded']} 逐项={len(sec['items'])}")
            bad = sorted(p for p in sec["items"] if not _expected_itemized(p))
            _say(not bad, "第四格/已装重跑：逐项露面的都是期望清单里那几类（期望另写一份，不问被测判据）",
                 f"{bad}")
            _say(len(sec["items"]) + sec["folded"] == sec["head"]
                 == len(sets[ps.PLAN_BUCKET_KEEP]),
                 "第四格/已装重跑：逐项数 + 折叠计数 == 格头 == 数据层项数",
                 f"{len(sec['items'])} + {sec['folded']} vs {sec['head']} vs "
                 f"{len(sets[ps.PLAN_BUCKET_KEEP])}")
            # **外部锚点**：上面那组对账全是内部自洽式的——数据层少报时三者一起变小、
            # 恒成立；往返组也接不住（第四格按定义就是真写不碰的文件，少报一项在文件系统上
            # 不留痕）。实证：删掉骨架那一支的 `keep(dst)`（29 项整族消失）之后，
            # 本条之外的断言全绿。
            # 锚点取「已装项目里**必然全部已存在**」的两份清单 ＋ 两个单独分支：
            # `scaffold_targets` ＋ `project_docs_targets` ＋ `AGENTS.md` ＋ `.gitignore`。
            # 用 `==` 而不是 `>=`：这个夹具刚真装过，四族**一个不缺**，所以等号是可断言的，
            # 而 `>=` 放过「只少一项」那一档。多出来同样该红——那意味着有东西被算了两遍。
            skills_rel = ps.plan_skills_reach(f.target)[2]
            params = json.loads(f.params_path.read_text(encoding="utf-8"))
            anchor = (len(ps.scaffold_targets(f.target))
                      + len(ps.project_docs_targets(f.target, params, skills_rel))
                      + 2)
            _say(sec["head"] == anchor,
                 "第四格/已装重跑：项数与外部锚点逐项对齐（两份清单 + AGENTS.md + .gitignore）",
                 f"head={sec['head']} anchor={anchor}")
            # **折叠行那一半只有在这个夹具上才可达**（B 路径夹具的第四格没有折叠行）。
            # 折叠行同样是会被逐字贴进确认页的一句硬编码文案，同样不许替调用方承诺写回。
            _say(sec["fold_text"] != "",
                 "第四格/已装重跑：折叠行原文取到了（否则下一条是空真的）",
                 f"{sec['fold_text'][:80]}")
            promised = keep_promise_hits(sec)
            _say(not promised,
                 "第四格/已装重跑：三处文案（**含折叠行**）都不出现肯定式的写回承诺",
                 f"命中={promised}")
    finally:
        f.close()

    f = Fixture()
    try:
        f.seed_registry()
        sets, _n, untouched = plan_sets(f)
        _say(sets[ps.PLAN_BUCKET_KEEP] == set() and untouched == 0,
             "第四格/A 路径对照：目标不存在时这一格为空（证上面那些「含 CLAUDE.md」不是恒真）",
             f"{sorted(sets[ps.PLAN_BUCKET_KEEP])} untouched={untouched}")
        r = run_scaffold(f.target, f.params_path,
                         ["--create-missing", "--require-empty", "--write-subscription",
                          "--print-plan"], f.home)
        sec = plan_sections(r.stdout, f.target).get(KEEP_HEAD)
        _say(sec is not None and sec["items"] == set() and sec["head"] == 0,
             "第四格/A 路径对照：渲染出来也是空的一格（不是整格消失、也不是凭空多出条目）",
             f"{sec}")
    finally:
        f.close()


# ---------------------------------------------------------------- 共用判定来源

def test_would_change_is_the_single_predicate():
    """`_settings_io.would_change` 是 `write_settings` 落不落笔的判据，预演问的是同一个。"""
    import _settings_io as sio
    d = Path(tempfile.mkdtemp(prefix="wf-plan-wc-"))
    try:
        p = d / "settings.json"
        _say(sio.would_change(p, {"a": 1}) == (True, False),
             "共用判据/文件不存在 ⇒ (True, False)")
        changed, backup = sio.write_settings(p, {"a": 1})
        _say(changed is True and backup is None, "共用判据/首写 changed=True")
        _say(sio.would_change(p, {"a": 1}) == (False, True),
             "共用判据/内容相同 ⇒ (False, True)，与 write_settings 的短路同源")
        _say(sio.write_settings(p, {"a": 1}) == (False, None),
             "共用判据/内容相同时 write_settings 真的一个字节没动")
        _say(sio.would_change(p, {"a": 2}) == (True, True),
             "共用判据/内容不同 ⇒ (True, True)")
        p.write_text("{ 坏 JSON", encoding="utf-8", newline="")
        _say(sio.would_change(p, {"a": 1}) == (True, True),
             "共用判据/读不出来时不短路（前提没成立，不能当成相同放行）")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_targets_tables_are_shared():
    """清单只有一份：真写走的 `scaffold_targets` / `project_docs_targets` 就是预演读的那份。"""
    d = Path(tempfile.mkdtemp(prefix="wf-plan-tt-"))
    try:
        proj = d / "p"
        proj.mkdir()
        targets = ps.scaffold_targets(proj)
        _say(len(targets) >= 25, "共用清单/scaffold_targets 给出完整骨架清单", f"{len(targets)} 项")
        _say(all(k in ("copy", "render", "write") for k, _d, _p in targets),
             "共用清单/kind 只有三种，与三个写入原语一一对应")
        _say(all(Path(dst).is_absolute() for _k, dst, _p in targets),
             "共用清单/给的是绝对路径")
        before = tree_snapshot(proj)
        ps.scaffold_targets(proj)
        ps.project_docs_targets(proj, make_params(d / "market"), ps.SKILLS_DIR_NEUTRAL)
        _say(tree_snapshot(proj) == before, "共用清单/两个清单函数自身零写入")
        docs = ps.project_docs_targets(proj, make_params(d / "market"), ps.SKILLS_DIR_NEUTRAL)
        prd = [dst for _k, dst, _p in docs if dst.name == "SKILL.md"]
        _say(len(prd) == 1 and ".agents" in prd[0].parts,
             "共用清单/显式给 skills_rel 时 prd-style 落在中立目录下", f"{prd}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    for fn in (test_roundtrip_a_path, test_roundtrip_b_path, test_roundtrip_installed_rerun,
               test_roundtrip_more_forms,
               test_zero_write_snapshot, test_zero_write_barrier,
               test_zero_write_snapshot_all_fixtures, test_barrier_itself_bites,
               test_registry_always_shows, test_no_subscription_no_lines,
               test_exit_codes_aligned,
               test_conflict_same_name_different_source, test_render_fold_loses_nothing,
               test_keep_grid_names_every_file,
               test_keep_grid_folds_and_stays_empty_when_nothing_exists,
               test_would_change_is_the_single_predicate, test_targets_tables_are_shared):
        try:
            fn()
        except Exception as e:
            import traceback
            _say(False, f"{fn.__name__} 抛异常", f"{type(e).__name__}: {e}")
            traceback.print_exc()
    bad = _RESULTS.count(False)
    print()
    if bad:
        print(f"{bad} assertion(s) failed out of {len(_RESULTS)}")
        return 1
    print(f"all passed ({len(_RESULTS)} assertions)")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
