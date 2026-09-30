#!/usr/bin/env python3
"""`workframe_door.py` / `codex_roles.py` / `codex-trust-seed.py` 的常驻单测——纯函数与判定分支，
不起 codex（那半在沙盒里按侧效应验，见交付报告）。爆炸半径档 4：装机器的对账与 G 补种 hook 的
分支都是「判断别的东西对不对」的东西。

守的事按下列各条分组，每条各配它自己的失败方式：
1. **角色生成器确定性 ＋ 账本规则**：同输入两次逐字节相同；不在账本内的既有 role TOML 不动；账本内被
   手改的不覆盖（只差行尾不算被手改，见第 6 条）；managed block 段外字节不动；`.local.toml` 追加；inline 形态不产生。用户自写的
   `.codex/config.toml`（无 marker）→ 追加 managed 段并入账、段外逐字节原样（含 CRLF ＋ BOM）、重跑
   逐字节不变；有 marker 但不在账本 → 写任何文件之前拒绝；`verify()` 抓「role TOML 写了、注册段没写」
   与「整份 TOML 解析失败」两种半装态。
2. **装机对账的判定分支**：退出码 2 / 3 / 4 / 5 / 6 钉数值；条数 ≠ manifest → 3；command 为空 → 3；
   batchWrite 非 ok → 4；回读仍有非 trusted → 5；项目 trust 键形态；`own_entries` 只认 sourcePath 落在
   插件根内的条目；角色回读读不到时文案按 layers 有无项目层分说、不指向 trust；`describe()` 无残留引号。
3. **G 补种 hook 的分支**：N == 0 零输出只刷 last.json；N > 0 走 batchWrite 并明说「下次会话生效」；
   app-server 失败 → exit 0 ＋ 一行「待信任…自动补种失败」；`codex_home()` 三级定位。
4. **按项目启用**：managed 段段首启用表的位置与插件 id 取市场名；整份语义预检的拒绝矩阵（段外同名声明七种写法 /
   段后吸收三种 / 文件解析不了）与不拒的两种，拒绝时项目树逐字节不变；旧形态段（无启用表、未被手改）按原样替换；
   「用户已关」与「段内其他行手改」两种段状态。`do_codex` 打桩驱动：预检命中时 codex CLI 与 app-server **零调用**、
   回显的市场名不同时 plugin add 不跑、plugin add 之后失败的文案带用户层残留句。按层建模的假服务器（用户层
   enabled / 项目 trust / 段首启用表决定 `hooks/list` 条数）：首装 / 同 home 第二个项目 / `--upgrade` 三种 edits；
   回读三层（用户层 / 合成值 / 条数与信任）各自不符 → 5 且文案点名那一层；0 条的三种成因；`--check` 的用户层
   true / 缺表 / 已关。
5. **安装根守卫**：插件版本的读法（`.codex-plugin` 不在才退 `.claude-plugin`、坏了不往后退）；版本不一致 / 任一侧
   读不出都不放行，文案含两个版本、两个根与按方向给的出路；hook 冻结清单六种坏形态各一句成因不同的人话；
   `do_codex` 的安装根产生点各自在写项目文件之前停下（项目树逐字节不变）、`step_hooks_list` 自推的根同样要核、
   `--check` 坏形态进 problems 且其余照跑；失败文案里「写了 / 没写」与实际一致（`--upgrade` 先写后核那一格、
   app-server 回读途中失联、`--backfill` 先写 CC 那半）。版本号一律是 fixture 自己写的字面值。
6. **账本口径与行尾**：生成物按 LF 写、提交进 git，再用 `core.autocrlf=true` clone 出 CRLF 副本（真走 git，
   隔离调用方机器的 git 配置与 attributes，clone 后先断言文件里确有 CRLF）——`--check` / 重跑 / 预检都不把它判成
   被手改，一个字节不写（角色文件与 config.toml 的「无操作」各自断言）；真改一份只点名那一份；段内改一行时文案恰好
   点名那一行；用户已关（段首启用表 `enabled = false`，含带行尾注释的写法）照样认出；在 CRLF 副本上升级（经门的
   `step_roles`）与改 `.local.toml` 两条生产路径只写该写的、重跑零字节。git 造不出的形态用字节构造：隔行混合行尾
   容忍；孤立 CR / 文件头 BOM / 删末尾换行 / 行尾空白四个方向仍判被手改；`.local.toml` 值里带 CRLF 时记账与比对同口径。
7. **成功出口的 AGENTS.md 提示**：`!`（「这个项目是什么」只剩占位 / AGENTS.md 不存在）与 `i`（只有 CC 读得到的内容面）
   钉成 2×2，两行各自异常隔离；`--codex` / `--upgrade` / `--check` / 缺省 `--both` / `--backfill` 逐个出口成功时恰一次、
   失败时不说，`--cc` 不说；真 `do_codex` 走完那一格钉「恰一次」（`do_codex` 里也说就是两次）。`--backfill` 两扇门都接时
   收尾与 `[3/3]` 行都说「项目侧已接」、本机没核、给出 --check，Codex 门是本次装的才说「两扇门都已接上」；`do_backfill`
   的返回值（结束时 Codex 门在项目侧接没接着，main 据此决定打不打提示）四种结局各钉一格。
8. **验活表的会话计数**：会话前没有 `activity-state.json` 时按「建出且为 1」判（`session_counter_ticked` 矩阵 ＋ `check_live`
   用假进程只落侧效应跑一遍）。
9. **同名市场的出路不绕圈**：被拒时不再无条件说「已登记的那份没有被改动」，让人用 `marketplace list --json` 认出 git 登记、
   不拿克隆目录当 `--marketplace`；版本对不上时「分不清」那句在 git 登记、要本地源的那一态直接给 remove（删用户配置里的
   注册，门不代做）。
10. **装前核对**（`plugin add` 之前）：市场里将要装的那一份（`installed[]` ∪ `available[]` 里按市场名与插件名取 core 那一行的
   `source.path`，不读行上的 `version`）与门不同版本 → 2、`plugin add` 不跑、用户层没被写回 true；`--codex` 与 `--upgrade`
   本地源的两个重装点都经过它，停下时不说「写回 true」；按回显的市场名问；步 2 新登记 / 早已登记各自如实说；市场不比门新
   时出路先给「自己 remove 再带 `--marketplace`」、不给 `--upgrade`（新登记与早已登记都会把全机缓存换成旧版），早已登记与
   `--upgrade` 本地源另给「把本地目录更新到门这一版」；市场更新时出路点名市场目录里的门、按它重跑走完；读不出（六种形态）
   放行、打 `i`（各自说出是哪一种原因）、由装后核对兜底；判读句里的根不带扩展长度前缀；步号严格递增、`[3/11]` 只一次。
   helper 的封闭：`step_marketplace_add` 回显三格与 `market_core_row` 的形态循环、超时格传 `_SEAL` 下不存在的 exe
   （`_NO_CODEX`），漏打桩时起不来、不会按 PATH 找到 codex；本文件其余直接调 helper 的格（`check_static`、
   `marketplace_source_type`、`step_upgrade`、`locate_market`、`check_live` 等）仍传字面 `"codex"`，靠各自打的
   `run_codex` 桩挡住——那几格漏打桩时会按 PATH 解析，封闭不是结构性的；封闭自检另核 `run_codex` 没被留成桩。
"""

import atexit
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN = REPO_ROOT / "plugins" / "core"
SCRIPTS = PLUGIN / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.dont_write_bytecode = True

# ---- 封闭：本套件不起调用方机器上的 codex、不碰调用方的 Codex home ----
# 在 import 被测模块之前**强制覆盖**两个变量（不是 setdefault：调用方环境里的 `CODEX_HOME` 正是要挡的那一个）：
#   · `WF_CODEX_EXE` 指向一个不存在的路径 ⇒ `find_codex_exe` 返回 None，**不回退到 PATH**（它的第一个分支；
#     这条契约由 `test_codex_seal` 在 PATH 上放一份假 codex 钉住）；
#   · `CODEX_HOME` 指向本进程专属的临时目录。
# 于是被测代码里挡在真 codex 前面的守卫一旦回归（写坏反证、突变恰好在批量制造这种回归），走到的是「codex 未找到」，
# 而不是调用方机器上的 codex 与它的 home。放在测试模块里而不是 validate 的 runner 里：直接跑本文件（开发、写坏
# 反证、独立验证都这么跑）同样被封住。`test_workframe_migrate.py` 开头有同样的一段，两份各带自检。
_SEAL = Path(tempfile.mkdtemp(prefix="wf-door-test-seal-"))
(_SEAL / "codex-home").mkdir()
os.environ["CODEX_HOME"] = str(_SEAL / "codex-home")
os.environ["WF_CODEX_EXE"] = str(_SEAL / "no-such-codex" / "codex.exe")
atexit.register(shutil.rmtree, str(_SEAL), True)
# 传它的格漏打了 `run_codex` 桩时，起的是这个不存在的路径（FileNotFoundError），不会按 PATH 解析到调用方机器上的
# codex。只有部分 helper 单测传它（见抬头第 10 条）；其余仍传字面 `"codex"` 的格只靠各自的桩挡住
_NO_CODEX = str(_SEAL / "no-such-codex" / "codex.exe")

import codex_roles as cr  # noqa: E402
import workframe_door as wd  # noqa: E402
import _codex_appserver as cas  # noqa: E402

_FIND_CODEX_EXE = cas.find_codex_exe     # 封闭自检用：跑完时核它没有被哪一格打了桩留下
_RUN_CODEX = wd.run_codex                # 同上：各驱动都把它换成桩、靠手写的恢复元组还原——漏了复原，后面的格就在用别人的桩

seed = wd.seed
MKT = "workframe"          # 段首启用表的市场名（插件 id core@workframe）
FAILURES = []


def _code(err):
    """`DoorError` 的退出码；`None`（没抛）返回 `None`。

    **不要写 `err.code`**：被测分支被改坏而不再抛时那是一条 `AttributeError`，套件当场崩在
    这一行、**一个 ✗ 都不打**——而「崩了」与「这条断言没写对」在终端上同形。写坏反证实测踩过：
    5 组变异里全是这个形态，看起来像探针失效，其实断言是活的。
    """
    return getattr(err, "code", None)


def check(label, got, want):
    if got == want:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


def tmpdir():
    d = Path(tempfile.mkdtemp(prefix="wfdoor-"))
    return d


def rm(d):
    shutil.rmtree(d, ignore_errors=True)


# ---- 1. 角色生成器 ----

def test_roles():
    print("[roles] 确定性 ＋ 账本规则")
    d = tmpdir()
    try:
        r1, roles = cr.generate(d, PLUGIN, "1.1.0", write=False, marketplace=MKT)
        r2, _ = cr.generate(d, PLUGIN, "1.1.0", write=False, marketplace=MKT)
        check("两次渲染逐字节相同", r1, r2)
        check("角色数 == agents/*.md 数", len(roles), len(list((PLUGIN / "agents").glob("*.md"))))
        blk = r1[cr.CONFIG_REL.as_posix()][0]
        check("注册段每角色一段 [agents.<role>]", blk.count("[agents."), len(roles))
        check("注册段只有 config_file 指针、不内联正文", "developer_instructions" in blk, False)
        for role, _desc, _body in roles:
            check(f"config_file 相对 .codex/ 指向 roles/{role}.toml", f'config_file = "roles/{role}.toml"' in blk, True)
        report, _ = cr.generate(d, PLUGIN, "1.1.0", marketplace=MKT)
        check("首次全部 written", sorted({a for _r, a, _n in report}), ["written"])
        ledger = cr.load_ledger(d)
        check("账本登记 = 角色数 + 1（config.toml）", len(ledger["files"]), len(roles) + 1)
        check("verify 无问题", cr.verify(d), [])
        led_bytes = (d / cr.LEDGER_REL).read_bytes()
        report2, _ = cr.generate(d, PLUGIN, "1.1.0", marketplace=MKT)
        check("重跑全部 unchanged", sorted({a for _r, a, _n in report2}), ["unchanged"])
        check("无操作重跑账本逐字节不变", (d / cr.LEDGER_REL).read_bytes(), led_bytes)
        # 段外字节不动：用户在 config.toml 段外加内容
        cfg = d / cr.CONFIG_REL
        user = cfg.read_text(encoding="utf-8") + "\n[features]\nmulti_agent = true\n"
        cfg.write_bytes(user.encode("utf-8"))
        cr.generate(d, PLUGIN, "1.1.0", marketplace=MKT)
        check("段外用户内容保留", cfg.read_text(encoding="utf-8").endswith("[features]\nmulti_agent = true\n"), True)
        check("段外改动后 verify 仍过", cr.verify(d), [])
        # 手改账本内的 role 文件 → 跳过不覆盖
        pm = d / cr.ROLES_DIR_REL / f"{roles[0][0]}.toml"
        pm.write_bytes(pm.read_bytes() + b"\n# user edit\n")
        report3, _ = cr.generate(d, PLUGIN, "1.1.0", marketplace=MKT)
        acts = {r: a for r, a, _n in report3}
        check("手改的 role 文件被跳过", acts.get((cr.ROLES_DIR_REL / f"{roles[0][0]}.toml").as_posix()), "skipped")
        check("手改文件未被覆盖", pm.read_bytes().endswith(b"# user edit\n"), True)
        check("verify 报出手改", any("被手改" in p for p in cr.verify(d)), True)
        pm.write_bytes(pm.read_bytes().replace(b"\n# user edit\n", b""))
        # 不在账本内的既有文件不动
        d2 = tmpdir()
        try:
            foreign = d2 / cr.ROLES_DIR_REL / f"{roles[0][0]}.toml"
            foreign.parent.mkdir(parents=True)
            foreign.write_bytes(b"developer_instructions = 'mine'\n")
            report4, _ = cr.generate(d2, PLUGIN, "1.1.0", marketplace=MKT)
            acts4 = {r: a for r, a, _n in report4}
            check("不在账本内的既有文件跳过", acts4.get(foreign.relative_to(d2).as_posix()), "skipped")
            check("其内容未动", foreign.read_bytes(), b"developer_instructions = 'mine'\n")
        finally:
            rm(d2)
        # .local.toml 追加
        local = d / cr.ROLES_DIR_REL / f"{roles[1][0]}.local.toml"
        local.write_bytes(b'developer_instructions = "LOCAL_EXTRA_MARK"\n')
        cr.generate(d, PLUGIN, "1.1.0", marketplace=MKT)
        body = (d / cr.ROLES_DIR_REL / f"{roles[1][0]}.toml").read_text(encoding="utf-8")
        check(".local.toml 的 developer_instructions 追加在正文后", body.rstrip().endswith("LOCAL_EXTRA_MARK\n'''"), True)
        check(".local.toml 不进账本", any(r.endswith(".local.toml") for r in cr.load_ledger(d)["files"]), False)
        # 生成物可被 TOML 解析
        import tomllib
        for role, _d, _b in roles:
            data = tomllib.loads((d / cr.ROLES_DIR_REL / f"{role}.toml").read_text(encoding="utf-8"))
            check(f"{role}.toml 可解析且有 developer_instructions", "developer_instructions" in data, True)
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        check("config.toml 可解析、agents 段齐", sorted(data.get("agents", {})), sorted(r for r, _d, _b in roles))
        # marker 不成对 → 报错不猜
        cfg.write_bytes(cfg.read_bytes().replace(cr.BLOCK_END.encode(), b"# gone"))
        try:
            cr.generate(d, PLUGIN, "1.1.0", marketplace=MKT)
            check("marker 不成对 → RoleError", "no error", "RoleError")
        except cr.RoleError:
            check("marker 不成对 → RoleError", "RoleError", "RoleError")
    finally:
        rm(d)
    _test_user_config()


def _test_user_config():
    """用户预置 `.codex/config.toml`（无 marker、不在账本）的三种起点 ＋ verify 的两种半装态。"""
    print("[roles] 用户自写 config.toml")
    rel = cr.CONFIG_REL.as_posix()
    # (a) LF 用户文件：追加、入账、重跑逐字节不变、Codex 视角（tomllib）看得到用户键与 agents 段
    d = tmpdir()
    try:
        cfg = d / cr.CONFIG_REL
        cfg.parent.mkdir(parents=True)
        user = b'model = "gpt-5.5"\n[sandbox_workspace_write]\nnetwork_access = true\n'
        cfg.write_bytes(user)
        report, roles = cr.generate(d, PLUGIN, "1.1.0", marketplace=MKT)
        acts = {r: (a, n) for r, a, n in report}
        check("无 marker 的用户 config.toml → written 且说明只追加", acts[rel][0] == "written" and "追加" in acts[rel][1], True)
        out = cfg.read_bytes()
        check("用户内容是文件前缀、逐字节原样", out.startswith(user), True)
        check("追加的正是 managed 段", out.endswith(cr.render_block(roles, "1.1.0", MKT).encode("utf-8")), True)
        check("config.toml 入账（kind managed-block）", cr.load_ledger(d)["files"].get(rel, {}).get("kind"), "managed-block")
        check("verify 无问题", cr.verify(d), [])
        import tomllib
        data = tomllib.loads(out.decode("utf-8"))
        check("整份可解析：用户键与 agents 段并存", (data.get("model"), sorted(data.get("agents", {}))),
              ("gpt-5.5", sorted(r for r, _d, _b in roles)))
        led = (d / cr.LEDGER_REL).read_bytes()
        report2, _ = cr.generate(d, PLUGIN, "1.1.0", marketplace=MKT)
        check("重跑 config.toml unchanged", {r: a for r, a, _n in report2}[rel], "unchanged")
        check("重跑 config.toml 逐字节不变", cfg.read_bytes(), out)
        check("重跑账本逐字节不变", (d / cr.LEDGER_REL).read_bytes(), led)
    finally:
        rm(d)
    # (b) CRLF ＋ BOM 的用户文件：段外一个字节不动（不归一换行、不吃空行），重跑仍不变
    d = tmpdir()
    try:
        cfg = d / cr.CONFIG_REL
        cfg.parent.mkdir(parents=True)
        user = b'\xef\xbb\xbfmodel = "gpt-5.5"\r\n\r\n[features]\r\nmulti_agent = true\r\n'
        cfg.write_bytes(user)
        cr.generate(d, PLUGIN, "1.1.0", marketplace=MKT)
        out = cfg.read_bytes()
        check("CRLF+BOM 用户内容逐字节原样为前缀", out.startswith(user), True)
        check("CRLF 用户文件 verify 过（tomllib 接受 BOM 与混合行尾）", cr.verify(d), [])
        cr.generate(d, PLUGIN, "1.1.0", marketplace=MKT)
        check("CRLF 用户文件重跑逐字节不变", cfg.read_bytes(), out)
    finally:
        rm(d)
    # (c) 有 marker 但不在账本 → 生成前拒绝，role TOML 一个都不写、账本不建、config.toml 不动
    d = tmpdir()
    try:
        cfg = d / cr.CONFIG_REL
        cfg.parent.mkdir(parents=True)
        stale = b"model = 'x'\n" + cr.render_block([("ghost", "g", "body")], "0.0.1", MKT).encode("utf-8")
        cfg.write_bytes(stale)
        try:
            cr.generate(d, PLUGIN, "1.1.0", marketplace=MKT)
            check("有 marker 不在账本 → RoleError", "no error", "RoleError")
        except cr.RoleError as e:
            check("有 marker 不在账本 → RoleError", "RoleError", "RoleError")
            check("拒绝文案给出路（恢复账本 / 删段重跑）", "恢复账本" in str(e) and "重跑" in str(e), True)
        check("拒绝时未写任何 role TOML", (d / cr.ROLES_DIR_REL).exists(), False)
        check("拒绝时未建账本", (d / cr.LEDGER_REL).exists(), False)
        check("拒绝时 config.toml 逐字节不变", cfg.read_bytes(), stale)
    finally:
        rm(d)
    # (d) verify 抓半装态：账本有 role、config.toml 没注册段（即修复前 --codex 装到步 4 的形态）
    d = tmpdir()
    try:
        _report, roles = cr.generate(d, PLUGIN, "1.1.0", marketplace=MKT)
        ledger = cr.load_ledger(d)
        ledger["files"].pop(rel)
        cr.save_ledger(d, ledger)
        (d / cr.CONFIG_REL).write_bytes(b'model = "gpt-5.5"\n')
        probs = cr.verify(d)
        check("role 已生成但 config.toml 未注册 → 每个角色各报一条", sum("注册段没写" in p for p in probs), len(roles))
        # (e) verify 抓整份 TOML 解析失败（段外用户内容有语法错）
        cr.generate(d, PLUGIN, "1.1.0", marketplace=MKT)      # 无 marker → 追加回来
        check("追加后 verify 过", cr.verify(d), [])
        cfg = d / cr.CONFIG_REL
        cfg.write_bytes(b'broken = [\n' + cfg.read_bytes())
        check("段外语法错 → verify 报整份解析失败", any("整份 TOML 解析失败" in p for p in cr.verify(d)), True)
    finally:
        rm(d)


# ---- 2. 装机对账 ----

class FakeServer:
    def __init__(self, hooks, write_status="ok", projects=None, after=None):
        self.hooks, self.write_status, self.projects = hooks, write_status, projects or {}
        self.after = after
        self.edits = None

    def hooks_list(self, cwds):
        return {"data": [{"cwd": cwds[0] if cwds else "", "hooks": list(self.after if self.after and self.edits is not None else self.hooks),
                          "warnings": [], "errors": []}]}

    def config_batch_write(self, edits, **kw):
        self.edits = list(edits)
        if self.write_status == "raise":
            raise cas.AppServerError("configLayerReadonly")
        return {"status": self.write_status}

    agents = None
    layers = None           # config/read 的 layers；None = 只给 user / system 两层（没有项目层）
    user_enabled = False    # user 层 [plugins."core@workframe"].enabled；None = 无该表
    merged_enabled = True   # 带 cwd 读到的合成值

    def config_read(self, _layers=False, cwd=None):
        cfg = {"projects": dict(self.projects), "hooks": {"state": {}}}
        if cwd and self.agents is not None:
            cfg["agents"] = self.agents          # 项目层只在带 cwd 时可见（实测）
        if cwd and self.merged_enabled is not None:
            cfg["plugins"] = {"core@workframe": {"enabled": self.merged_enabled}}
        out = {"config": cfg}
        if self.layers is not None:
            out["layers"] = self.layers
        else:
            up = {} if self.user_enabled is None else {"core@workframe": {"enabled": self.user_enabled}}
            out["layers"] = [{"name": {"type": "user"}, "config": {"plugins": up}},
                             {"name": {"type": "system"}, "config": {}}]
        return out


def _hook(key, status="untrusted", cmd="x", plugin="core@workframe", root="R"):
    return {"key": f"{plugin}:hooks/hooks.codex.json:{key}", "trustStatus": status, "currentHash": "h" + key,
            "command": cmd, "eventName": key.split(":")[0], "pluginId": plugin,
            "sourcePath": f"{root}/hooks/hooks.codex.json"}


def test_door():
    print("[door] 对账分支与退出码")
    manifest = json.loads((PLUGIN / "hooks" / "hooks.codex.manifest.json").read_text(encoding="utf-8"))
    keys = [e["key"].split(":", 1)[1] for e in manifest["entries"]]
    d = tmpdir()
    try:
        # 退出码钉数值（不只钉符号）：117 §2.6 的 2–6 是对外契约，改常量必须在这里红
        for label, got, want in [("EXIT_ENV == 2", wd.EXIT_ENV, 2), ("EXIT_COUNT == 3", wd.EXIT_COUNT, 3),
                                 ("EXIT_WRITE == 4", wd.EXIT_WRITE, 4), ("EXIT_READBACK == 5", wd.EXIT_READBACK, 5),
                                 ("EXIT_LIVE == 6", wd.EXIT_LIVE, 6)]:
            check(label, got, want)
        proj = d / "proj"
        proj.mkdir()
        (proj / ".workframe-config.json").write_bytes(b'{"project_name":"t"}')
        root = str(PLUGIN)
        good = [_hook(k, root=root) for k in keys]
        s = FakeServer(good)
        mine, r = wd.step_hooks_list(s, proj, "core@workframe", PLUGIN)
        check("条数 == manifest 通过", len(mine), len(keys))
        for label, hooks, code in [
            ("少一条 → 3", good[:-1], wd.EXIT_COUNT),
            ("command 为空 → 3", [dict(h, command="") for h in good], wd.EXIT_COUNT),
            ("0 条（整份未解析）→ 3", [], wd.EXIT_COUNT),
        ]:
            try:
                wd.step_hooks_list(FakeServer(hooks), proj, "core@workframe", PLUGIN)
                check(label, "no error", code)
            except wd.DoorError as e:
                check(label, e.code, code)
        # 用户源条目不计入
        extra = good + [{"key": "C:/x/hooks.json:session_start:0:0", "trustStatus": "untrusted", "currentHash": "u",
                         "command": "y", "eventName": "sessionStart", "pluginId": None, "sourcePath": "C:/x/hooks.json"}]
        mine, _ = wd.step_hooks_list(FakeServer(extra), proj, "core@workframe", PLUGIN)
        check("用户源条目不计入过滤计数", len(mine), len(keys))
        # batchWrite 非 ok → 4
        key = wd.project_key(proj)
        for label, st, code in [("batchWrite 非 ok → 4", "error", wd.EXIT_WRITE), ("batchWrite 抛错 → 4", "raise", wd.EXIT_WRITE)]:
            try:
                wd.step_write(FakeServer(good, write_status=st), good, key, True)
                check(label, "no error", code)
            except wd.DoorError as e:
                check(label, e.code, code)
        s = FakeServer(good)
        wd.step_write(s, good, key, True)
        check("edits = N 条 hook + 1 条项目 trust", len(s.edits), len(good) + 1)
        check("项目 trust keyPath 形态（双引号 ＋ 反斜杠转义；单引号 / 裸反斜杠实测都写错键）",
              s.edits[-1]["keyPath"], 'projects."' + key.replace("\\", "\\\\") + '".trust_level')
        check("hash 只从 currentHash 取", all(e["value"].startswith("h") for e in s.edits[:-1]), True)
        # 回读仍有非 trusted → 5
        after = [dict(h, trustStatus="trusted") for h in good]
        after[3] = dict(after[3], trustStatus="modified")
        s = FakeServer(good, after=after, projects={key: {"trust_level": "trusted"}})
        s.edits = []
        try:
            wd.step_readback(s, proj, "core@workframe", key, True)
            check("回读仍有 modified → 5", "no error", wd.EXIT_READBACK)
        except wd.DoorError as e:
            check("回读仍有 modified → 5", e.code, wd.EXIT_READBACK)
            check("回读文案点名 key 与状态", "modified" in str(e) and keys[3] in str(e), True)
        s = FakeServer(good, after=[dict(h, trustStatus="trusted") for h in good], projects={})
        s.edits = []
        try:
            wd.step_readback(s, proj, "core@workframe", key, True)
            check("项目 trust 回读缺失 → 5", "no error", wd.EXIT_READBACK)
        except wd.DoorError as e:
            check("项目 trust 回读缺失 → 5", e.code, wd.EXIT_READBACK)
        check("project_key 是解析后的长路径" + ("（Windows 小写）" if os.name == "nt" else ""),
              key, str(proj.resolve()).lower() if os.name == "nt" else str(proj.resolve()))
        # own_entries：只认 sourcePath 落在插件根内
        outside = _hook("stop:0:0", root=str(d / "elsewhere"))
        check("own_entries 只认插件根内的 sourcePath", len(seed.own_entries(good + [outside], [PLUGIN])), len(good))
        # 角色注册回读：config/read(cwd) 的 agents 形态（config_file 由 Codex 解析成绝对路径）
        cr.generate(proj, PLUGIN, "1.1.0", marketplace=MKT)
        role_names = [r for r, _d, _b in cr.load_agents(PLUGIN / "agents")]
        ok_agents = {r: {"description": "x", "config_file": str(proj / cr.ROLES_DIR_REL / f"{r}.toml")} for r in role_names}
        s = FakeServer(good, projects={key: {"trust_level": "trusted"}}); s.agents = ok_agents
        check("角色注册回读通过", wd.roles_registered_problems(s, proj, role_names), [])
        s.agents = {k: v for k, v in ok_agents.items() if k != role_names[0]}
        check("少登记一个角色 → 报出", any(role_names[0] in p for p in wd.roles_registered_problems(s, proj, role_names)), True)
        bad = dict(ok_agents); bad[role_names[1]] = {"description": "x", "config_file": "roles/elsewhere.toml"}
        s.agents = bad
        check("config_file 指错 → 报出", any(role_names[1] in p for p in wd.roles_registered_problems(s, proj, role_names)), True)
        s.agents = None
        probs = wd.roles_registered_problems(s, proj, role_names)
        check("不带 cwd 的读法（agents 缺席）→ 每个角色都报", len(probs), len(role_names))
        check("agents 缺席且无项目层 → 文案说「没有项目层」、不指向 trust", all("没有项目层" in p and "trust 未生效" not in p for p in probs), True)
        s.layers = [{"name": {"type": "project", "dotCodexFolder": str(proj / ".codex")}, "config": {}}]
        probs = wd.roles_registered_problems(s, proj, role_names)
        # 钉「但其中没有这个角色」而不是「有项目层」——后者是 else 分支「没有项目层」的子串，两个分支都会真
        check("agents 缺席但有项目层 → 文案说「有项目层但其中没有这个角色」",
              all("但其中没有这个角色" in p and "没有项目层" not in p for p in probs), True)
        s.layers = None
        # describe()：命令字面 `"…/scripts/x.py" --args` 切掉前缀后不留闭合引号
        h = _hook("user_prompt_submit:0:0", cmd='"${CLAUDE_PLUGIN_ROOT}/bin/workframe-python" '
                                                '"${CLAUDE_PLUGIN_ROOT}/scripts/user-prompt-inject.py" --harness codex')
        check("describe() 无残留引号（键只去插件 id 前缀）", seed.describe(h),
              "hooks/hooks.codex.json:user_prompt_submit:0:0（user_prompt_submit）→ user-prompt-inject.py --harness codex")
        check("版本比较", wd.parse_version("codex-cli 0.153.4") >= wd.MIN_CODEX, True)
        check("版本过低判定", (wd.parse_version("codex-cli 0.149.0") or (0,)) < wd.MIN_CODEX, True)
    finally:
        rm(d)


# ---- 3. G 补种 hook ----

def test_seed():
    print("[seed] G 的分支")
    d = tmpdir()
    try:
        home = d / "home"
        home.mkdir()
        (home / "config.toml").write_bytes(b"model = 'x'\n")
        proj = d / "proj"
        (proj / ".workframe" / "state").mkdir(parents=True)
        (proj / ".workframe-config.json").write_bytes(b'{"project_name":"t"}')
        env_backup = dict(os.environ)
        try:
            os.environ["CODEX_HOME"] = str(home)
            check("codex_home 认环境变量", seed.codex_home(), Path(str(home)))
            os.environ.pop("CODEX_HOME", None)
            # 上溯 5 级：<home>/plugins/cache/mkt/core/1.1.0
            fake_root = home / "plugins" / "cache" / "workframe" / "core" / "1.1.0"
            fake_root.mkdir(parents=True)
            orig = seed._harness.plugin_root_candidates
            seed._harness.plugin_root_candidates = lambda: [fake_root]
            try:
                check("codex_home 无环境变量时从插件根上溯 5 级（要求那一级有 config.toml）", seed.codex_home(), home.resolve())
                (home / "config.toml").unlink()
                check("上溯那一级没有 config.toml → 退到 ~/.codex", seed.codex_home(), Path.home() / ".codex")
                (home / "config.toml").write_bytes(b"model = 'x'\n")
            finally:
                seed._harness.plugin_root_candidates = orig
            os.environ["CODEX_HOME"] = str(home)
            manifest = json.loads((PLUGIN / "hooks" / "hooks.codex.manifest.json").read_text(encoding="utf-8"))
            keys = [e["key"].split(":", 1)[1] for e in manifest["entries"]]
            root = str(PLUGIN)
            payload = {"cwd": str(proj), "session_id": "s1", "hook_event_name": "SessionStart"}

            class FakeCas:
                AppServerError = cas.AppServerError
                hooks_of = staticmethod(cas.hooks_of)
                trust_edits = staticmethod(cas.trust_edits)
                plugin_hooks = staticmethod(cas.plugin_hooks)
                server = None

                class AppServer:
                    def __init__(self, cwd=None, timeout=None, **kw):
                        self.s = FakeCas.server
                        if self.s == "raise":
                            raise cas.AppServerError("app-server 已退出（exit 1）")

                    def __enter__(self):
                        return self.s

                    def __exit__(self, *a):
                        return False

            real_cas = sys.modules["_codex_appserver"]
            sys.modules["_codex_appserver"] = FakeCas
            seed._harness.plugin_root_candidates = lambda: [PLUGIN]
            try:
                # N == 0
                FakeCas.server = FakeServer([_hook(k, "trusted", root=root) for k in keys])
                text, last = seed.run(payload)
                check("N==0 零输出", text, None)
                check("N==0 last.json 记 checked/pending", (last.get("checked"), last.get("pending")), (len(keys), 0))
                # N > 0
                hooks = [_hook(k, "trusted", root=root) for k in keys]
                hooks[0] = dict(hooks[0], trustStatus="modified")
                hooks[5] = dict(hooks[5], trustStatus="untrusted")
                after = [dict(h, trustStatus="trusted") for h in hooks]
                FakeCas.server = FakeServer(hooks, after=after)
                text, last = seed.run(payload)
                check("N>0 写了恰好 N 条 upsert", len(FakeCas.server.edits), 2)
                check("N>0 文案明说条数与「下次会话生效」", "已代你信任 2 条" in text and "下次会话生效" in text, True)
                check("N>0 文案列出每条执行什么", keys[0] in text and keys[5] in text, True)
                check("N>0 写种信任记录", (home / ".workframe" / "codex-trust-seed.json").is_file(), True)
                rec = json.loads((home / ".workframe" / "codex-trust-seed.json").read_text(encoding="utf-8"))
                check("种信任记录字段集 = HOOK_HASH_FIELDS 全集", rec["fields"], list(wd.ch.GROUP_HASH_FIELDS + wd.ch.HOOK_HASH_FIELDS))
                check("种信任记录条数 = manifest", len(rec["entries"]), len(keys))
                check("last.json 记 seeded", last.get("seeded"), 2)
                # batchWrite 非 ok → 文案「自动补种失败」，不抛
                FakeCas.server = FakeServer(hooks, write_status="error")
                text, last = seed.run(payload)
                check("写失败 → 一行「待信任…自动补种失败」", "2 条 hook 待信任，自动补种失败" in text, True)
                # app-server 起不来
                FakeCas.server = "raise"
                text, last = seed.run(payload)
                check("app-server 起不来 → 文案「未能读取」且 last 记 error", "未能读取" in text and "error" in last, True)
                # 非本插件条目不写
                FakeCas.server = FakeServer([_hook("stop:0:0", "untrusted", plugin="other@m", root=str(d / "other"))])
                text, last = seed.run(payload)
                check("只有别的插件的条目 → 零输出、不写", (text, FakeCas.server.edits), (None, None))
            finally:
                sys.modules["_codex_appserver"] = real_cas
                seed._harness.plugin_root_candidates = orig
        finally:
            os.environ.clear()
            os.environ.update(env_backup)
    finally:
        rm(d)


# ---- 4. 按项目启用：段首启用表 ＋ 整份语义预检 ＋ 用户层 ----

PID = "core@workframe"


def _snap(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}


def _mk(prefix=None, gen=False, suffix=None):
    """(临时根, 项目)：`prefix` 先写进 .codex/config.toml；`gen` 跑一次生成器；`suffix` 追加在文件末尾。"""
    d = tmpdir()
    proj = d / "proj"
    proj.mkdir()
    (proj / ".workframe-config.json").write_bytes(b'{"project_name":"t"}')
    cfg = proj / cr.CONFIG_REL
    if prefix is not None:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_bytes(prefix.encode("utf-8"))
    if gen:
        cr.generate(proj, PLUGIN, "1.1.0", marketplace=MKT)
    if suffix is not None:
        cfg.write_bytes(cfg.read_bytes() + suffix.encode("utf-8"))
    return d, proj


# (标签, prefix, gen, suffix, 期望 kind)
PREFLIGHT_REJECT = [
    ("段外表头 enabled = false", 'model = "x"\n[plugins."core@workframe"]\nenabled = false\n', False, None, cr.KIND_OUTSIDE_DUP),
    ("段外表头 enabled = true", '[plugins."core@workframe"]\nenabled = true\n', False, None, cr.KIND_OUTSIDE_DUP),
    ("顶层点号键", 'plugins."core@workframe".enabled = true\n', False, None, cr.KIND_OUTSIDE_DUP),
    ("内联表", 'plugins = { "core@workframe" = { enabled = true } }\n', False, None, cr.KIND_OUTSIDE_DUP),
    ("[plugins] ＋ 点号键", '[plugins]\n"core@workframe".enabled = false\n', False, None, cr.KIND_OUTSIDE_DUP),
    ("字面量引号表头", "[plugins.'core@workframe']\nenabled = true\n", False, None, cr.KIND_OUTSIDE_DUP),
    ("段外子表（有意严格）", '[plugins."core@workframe".x]\ny = 1\n', False, None, cr.KIND_OUTSIDE_DUP),
    ("段后 enabled = false", None, True, "enabled = false\n", cr.KIND_ABSORBED),
    ("段后 model = …", None, True, 'model = "gpt-5"\n', cr.KIND_ABSORBED),
    ("[tui] 结尾、段后写点号键", "[tui]\nx = 1\n", True, 'plugins."core@workframe".enabled = false\n', cr.KIND_ABSORBED),
    ("当前文件解析不了", "broken = [\n", False, None, cr.KIND_UNPARSABLE),
]


def test_preflight():
    print("[preflight] 段首启用表 ＋ 整份语义判定 ＋ 段状态（写任何文件之前）")
    roles = cr.load_agents(PLUGIN / "agents")
    lines = cr.render_block(roles, "1.1.0", MKT).split("\n")
    first_agents = next(i for i, ln in enumerate(lines) if ln.startswith("[agents."))
    check("启用表在段首：头注释之后、第一个 [agents.*] 之前", (lines[2], lines[3], first_agents), (f'[plugins."{PID}"]', "enabled = true", 4))
    check("启用表的插件 id 取市场名、不写死", '[plugins."core@other-mkt"]' in cr.render_block(roles, "1.1.0", "other-mkt"), True)
    for label, prefix, gen, suffix, kind in PREFLIGHT_REJECT:
        d, proj = _mk(prefix, gen, suffix)
        try:
            before = _snap(proj)
            for fn_label, fn in (("preflight", lambda: cr.preflight(proj, PLUGIN, "1.1.0", MKT)),
                                 ("generate", lambda: cr.generate(proj, PLUGIN, "1.1.0", marketplace=MKT))):
                try:
                    fn()
                    check(f"{label}：{fn_label} 拒绝（kind {kind}）", "no error", kind)
                except cr.PreflightError as e:
                    check(f"{label}：{fn_label} 拒绝（kind {kind}）", e.kind, kind)
                check(f"{label}：{fn_label} 拒绝后项目树逐字节不变", _snap(proj) == before, True)
        finally:
            rm(d)
    # 文案三种各自点名出路
    for label, prefix, gen, suffix, want in [
        ("段外同名 → 文案说 Codex 拒绝加载整份、去掉段外那处", '[plugins."core@workframe"]\nenabled = true\n', False, None, ("拒绝加载整份", "去掉段外")),
        ("段后吸收 → 文案说加上所属表头", None, True, 'model = "gpt-5"\n', ("最后一张表", "表头")),
        ("解析不了 → 文案说先修文件", "broken = [\n", False, None, ("解析失败", "先修文件")),
    ]:
        d, proj = _mk(prefix, gen, suffix)
        try:
            try:
                cr.preflight(proj, PLUGIN, "1.1.0", MKT)
                check(label, "no error", "PreflightError")
            except cr.PreflightError as e:
                check(label, all(w in str(e) for w in want), True)
        finally:
            rm(d)
    # 不拒的
    for label, prefix, gen, suffix in [("只声明别的插件的表", '[plugins."other@mkt"]\nenabled = true\n', False, None),
                                       ("段后带表头的内容", None, True, "\n[features]\nmulti_agent = true\n")]:
        d, proj = _mk(prefix, gen, suffix)
        try:
            try:
                cr.preflight(proj, PLUGIN, "1.1.0", MKT)
                report, _ = cr.generate(proj, PLUGIN, "1.1.0", marketplace=MKT)
                check(f"{label}：不拒，生成后 verify 无问题", cr.verify(proj), [])
            except cr.RoleError as e:
                check(f"{label}：不拒", f"RoleError: {e}", "no error")
        finally:
            rm(d)
    # 旧形态段（无启用表、sha 与账本相符）→ 按未被手改替换为新段
    d, proj = _mk(gen=True)
    try:
        cfg = proj / cr.CONFIG_REL
        old_block = "\n".join(ln for ln in cr.render_block(roles, "1.1.0", MKT).split("\n")
                              if not ln.startswith("[plugins.") and ln != "enabled = true")
        cfg.write_bytes(old_block.encode("utf-8"))
        ledger = cr.load_ledger(proj)
        ledger["files"][cr.CONFIG_REL.as_posix()]["sha256"] = cr.sha256_bytes(old_block.encode("utf-8"))
        cr.save_ledger(proj, ledger)
        check("旧形态段：状态 pristine", cr.managed_block_state(proj), cr.BLOCK_PRISTINE)
        cr.generate(proj, PLUGIN, "1.1.0", marketplace=MKT)
        check("旧形态段重跑：替换成带启用表的新段", cr.block_enable_value(proj, PID), True)
        check("旧形态段重跑后 verify 无问题", cr.verify(proj), [])
    finally:
        rm(d)
    # 用户已关 / 段内其他行手改
    for label, old, new, state, kind in [
        ("段首启用表改成 enabled = false", "\nenabled = true\n", "\nenabled = false\n", cr.BLOCK_USER_CLOSED, cr.KIND_USER_CLOSED),
        ("改成 enabled=false（空白不同）", "\nenabled = true\n", "\nenabled=false\n", cr.BLOCK_USER_CLOSED, cr.KIND_USER_CLOSED),
        ("段内其他一行手改", "config_file = \"roles/dev.toml\"", "config_file = \"roles/dev.toml\"\nmodel = \"x\"", cr.BLOCK_EDITED, cr.KIND_BLOCK_EDITED),
        ("关了又改了别的", "\nenabled = true\n", "\nenabled = false\n# note\n", cr.BLOCK_EDITED, cr.KIND_BLOCK_EDITED),
    ]:
        d, proj = _mk(gen=True)
        try:
            cfg = proj / cr.CONFIG_REL
            b = cfg.read_bytes()
            assert b.count(old.encode()) == 1, label
            cfg.write_bytes(b.replace(old.encode(), new.encode(), 1))
            before = _snap(proj)
            check(f"{label}：段状态", cr.managed_block_state(proj), state)
            try:
                cr.preflight(proj, PLUGIN, "1.1.0", MKT)
                check(f"{label}：预检拒绝", "no error", kind)
            except cr.PreflightError as e:
                check(f"{label}：预检拒绝", e.kind, kind)
            check(f"{label}：拒绝后项目树逐字节不变", _snap(proj) == before, True)
            closed_ok = not any("被手改" in p for p in cr.verify(proj))
            check(f"{label}：verify {'不报' if state == cr.BLOCK_USER_CLOSED else '报'}「被手改」",
                  closed_ok, state == cr.BLOCK_USER_CLOSED)
            if state == cr.BLOCK_USER_CLOSED:
                check(f"{label}：block_enable_value 读到 False", cr.block_enable_value(proj, PID), False)
        finally:
            rm(d)
    _test_preflight_installed()


def _test_preflight_installed():
    """已装项目（段已在文件里）这一维：段外再写同名声明时成因与出路要与首装同形，不许退成「解析不了 / 先修文件」；
    行尾注释的「已关」写法仍判被手改（严格），但文案点名那一行、提示只改值不加注释。"""
    print("[preflight] 已装项目：段外同名声明的成因 ＋ 被手改文案点名那一行")
    for label, pre, post in [
        ("段前表头 enabled = false", '[plugins."core@workframe"]\nenabled = false\n', None),
        ("段前表头 enabled = true", '[plugins."core@workframe"]\nenabled = true\n', None),
        ("段前顶层点号键", 'plugins."core@workframe".enabled = true\n', None),
        ("段前内联表", 'plugins = { "core@workframe" = { enabled = true } }\n', None),
        ("END 之后带表头", None, '\n[plugins."core@workframe"]\nenabled = false\n'),
        ("段外子表", '[plugins."core@workframe".x]\ny = 1\n', None),
    ]:
        d, proj = _mk(gen=True)
        try:
            cfg = proj / cr.CONFIG_REL
            cfg.write_bytes((pre or "").encode("utf-8") + cfg.read_bytes() + (post or "").encode("utf-8"))
            before = _snap(proj)
            try:
                cr.preflight(proj, PLUGIN, "1.1.0", MKT)
                check(f"已装 {label}：预检拒绝为段外同名（不是解析不了）", "no error", cr.KIND_OUTSIDE_DUP)
            except cr.PreflightError as e:
                check(f"已装 {label}：预检拒绝为段外同名（不是解析不了）", e.kind, cr.KIND_OUTSIDE_DUP)
                check(f"已装 {label}：出路是去掉段外那处、不是先修文件", ("去掉段外" in str(e), "先修文件" in str(e)), (True, False))
                if "子表" in label:
                    check(f"已装 {label}：文案说多加了键或子表、不说声明两次", ("多加了键或子表" in str(e), "声明两次" in str(e)), (True, False))
                else:
                    check(f"已装 {label}：文案说同一张表声明两次", "声明两次" in str(e), True)
            check(f"已装 {label}：拒绝后项目树逐字节不变", _snap(proj) == before, True)
            probs = cr.verify(proj)
            check(f"已装 {label}：verify（--check）也按段外报、不说先修文件",
                  (any("段之外" in p for p in probs), any("先修文件" in p for p in probs)), (True, False))
        finally:
            rm(d)
    d, proj = _mk(gen=True)
    try:
        cfg = proj / cr.CONFIG_REL
        cfg.write_bytes(b"broken = [\n" + cfg.read_bytes())
        try:
            cr.preflight(proj, PLUGIN, "1.1.0", MKT)
            check("已装、段外内容本身坏了：仍判解析不了", "no error", cr.KIND_UNPARSABLE)
        except cr.PreflightError as e:
            check("已装、段外内容本身坏了：仍判解析不了", e.kind, cr.KIND_UNPARSABLE)
    finally:
        rm(d)
    # 行尾注释的「已关」写法：严格判被手改，文案点名那一行 ＋ 只改值不加注释
    d, proj = _mk(gen=True)
    try:
        cfg = proj / cr.CONFIG_REL
        cfg.write_bytes(cfg.read_bytes().replace(b"\nenabled = true\n", b"\nenabled = false  # off\n", 1))
        check("enabled = false 带行尾注释：段状态仍判被手改（严格）", cr.managed_block_state(proj), cr.BLOCK_EDITED)
        check("enabled = false 带行尾注释：block_enable_value 读到 False（Codex 按关闭处理）", cr.block_enable_value(proj, PID), False)
        check("enabled = false 带行尾注释：commented_close_line 认得出", cr.commented_close_line(proj) is not None, True)
        try:
            cr.preflight(proj, PLUGIN, "1.1.0", MKT)
            check("带注释关闭：预检拒绝", "no error", cr.KIND_BLOCK_EDITED)
        except cr.PreflightError as e:
            check("带注释关闭：预检拒绝为被手改", e.kind, cr.KIND_BLOCK_EDITED)
            check("带注释关闭：文案点名那一行 ＋ 只改值不加注释", ("enabled = false  # off" in str(e), "不加注释" in str(e)), (True, True))
        check("带注释关闭：verify 的被手改提示只改值不加注释", any("不加注释" in p for p in cr.verify(proj)), True)
    finally:
        rm(d)
    d, proj = _mk(gen=True)
    try:
        cfg = proj / cr.CONFIG_REL
        cfg.write_bytes(cfg.read_bytes().replace(b'config_file = "roles/dev.toml"', b'config_file = "roles/dev.toml"\nmodel = "x"', 1))
        try:
            cr.preflight(proj, PLUGIN, "1.1.0", MKT)
            check("段内加一行：预检拒绝", "no error", cr.KIND_BLOCK_EDITED)
        except cr.PreflightError as e:
            check("段内加一行：文案点名那一行、不带注释提示", ('model = "x"' in str(e), "不加注释" in str(e)), (True, False))
        check("段内加一行：commented_close_line 为 None", cr.commented_close_line(proj), None)
    finally:
        rm(d)


class _Args:
    marketplace = "src"
    marketplace_name = "workframe"


def _plugin_list_payload(source_type, market=MKT):
    """`codex plugin list --json` 的最小真形态（字段名取自真机 codex-cli 0.153.4 的输出）。

    **第一条恒是别的市场**：真机上 `installed` 有 8 条、分属 4 个市场，首条就不是我们要查的那个。
    只造一条自己的市场时，「按市场名过滤」那半被改坏也不会红——而它坏掉的后果是**把 git 源判成
    local 源、走上重装路径**，正是本批要修的那条故障的镜像。fixture 必须让那一半有对象。
    """
    # 别的市场的 `sourceType` **恒与被查的那个相反**：两者相同时，「去掉过滤」这个变异在
    # 这一格上恒真（取到谁都一样），于是它只能被另一格抓住。相反之后**每一格都能分辨**。
    other = {"pluginId": "documents@openai-primary-runtime",
             "marketplaceName": "openai-primary-runtime",
             "marketplaceSource": {"sourceType": "git" if source_type == "local" else "local",
                                   "source": "\\\\?\\C:\\other"}}
    mine = {"pluginId": f"core@{market}", "marketplaceName": market,
            "marketplaceSource": {"sourceType": source_type, "source": "\\\\?\\C:\\x"}}
    return json.dumps({"installed": [other, mine], "available": []})


def _drive(proj, echoed="workframe", upgrade=False, appserver_error=True, source_type=None):
    """打桩驱动 do_codex：run_codex 记录调用、AppServer 构造即记录并抛（不起 codex）。返回 (code, msg, calls, constructed)。

    `source_type=None` 时 `plugin list` 按失败返回（= 源形态分辨不出，`--upgrade` 走 git 那一路）；
    给了值就按该形态应答，用来驱动本地源那一路。"""
    calls, constructed = [], []

    def fake_run(exe, args, cwd=None, timeout=180, env=None):
        calls.append(list(args))
        if args[:1] == ["--version"]:
            return 0, "codex-cli 0.153.4", ""
        if args[:3] == ["plugin", "marketplace", "add"]:
            return 0, json.dumps({"marketplaceName": echoed}), ""
        if args[:3] == ["plugin", "list", "--json"]:
            return (0, _plugin_list_payload(source_type), "") if source_type else (1, "", "no")
        if args[:2] == ["plugin", "add"]:
            return 0, json.dumps({"installedPath": str(PLUGIN), "version": "1.1.0"}), ""
        if args[:3] == ["plugin", "marketplace", "upgrade"]:
            return 0, "{}", ""
        return 1, "", "unexpected"

    class NoServer:
        def __init__(self, *a, **k):
            constructed.append(1)
            raise cas.AppServerError("stub：本格不起 app-server")

    saved = (wd.run_codex, wd.codex_exe, wd.cas.AppServer)
    wd.run_codex, wd.codex_exe, wd.cas.AppServer = fake_run, (lambda: "codex"), NoServer
    try:
        try:
            wd.do_codex(_Args(), proj, upgrade=upgrade)
            return 0, "", calls, constructed
        except wd.DoorError as e:
            return e.code, str(e), calls, constructed
    finally:
        wd.run_codex, wd.codex_exe, wd.cas.AppServer = saved


def test_do_codex_order():
    print("[door] 预检在任何写动作之前：run_codex / app-server 零调用、项目树不变")
    cells = [(label, prefix, gen, suffix) for label, prefix, gen, suffix, _k in PREFLIGHT_REJECT]
    cells += [("用户已关", None, True, None), ("段内其他行手改", None, True, None)]
    for label, prefix, gen, suffix in cells:
        for upgrade in (False, True):
            d, proj = _mk(prefix, gen, suffix)
            try:
                if label in ("用户已关", "段内其他行手改"):
                    cfg = proj / cr.CONFIG_REL
                    b = cfg.read_bytes()
                    new = b"\nenabled = false\n" if label == "用户已关" else b"\nenabled = true\n# edit\n"
                    cfg.write_bytes(b.replace(b"\nenabled = true\n", new, 1))
                before = _snap(proj)
                code, msg, calls, constructed = _drive(proj, upgrade=upgrade)
                tag = "--upgrade" if upgrade else "--codex"
                check(f"{tag} {label}：exit 3", code, wd.EXIT_COUNT)
                check(f"{tag} {label}：codex CLI 零调用（marketplace add / plugin add / upgrade 都没跑）", calls, [])
                check(f"{tag} {label}：app-server 未构造", constructed, [])
                check(f"{tag} {label}：项目树逐字节不变", _snap(proj) == before, True)
                check(f"{tag} {label}：文案不带「用户层此刻为 true」（plugin add 没跑）", wd.USER_LAYER_STILL_TRUE in msg, False)
            finally:
                rm(d)
    # 回显市场名与参数不同：按回显名再预检，命中 → exit 3、plugin add 没跑、文案说市场已注册
    d, proj = _mk('[plugins."core@other"]\nenabled = true\n')
    try:
        before = _snap(proj)
        code, msg, calls, constructed = _drive(proj, echoed="other")
        check("回显名不同且按它预检命中：exit 3", code, wd.EXIT_COUNT)
        check("回显名不同：只跑了 --version 与 marketplace add，plugin add 未跑",
              [c[:3] for c in calls], [["--version"], ["plugin", "marketplace", "add"]])
        check("回显名不同：文案说市场已注册、插件未安装", "市场已注册" in msg and "插件未安装" in msg, True)
        check("回显名不同：文案不带「用户层此刻为 true」", wd.USER_LAYER_STILL_TRUE in msg, False)
        check("回显名不同：项目树逐字节不变", _snap(proj) == before, True)
    finally:
        rm(d)
    # plugin add 之后失败（app-server 起不来）→ 文案带用户层残留句；--upgrade 同形失败不带（没写回 true）
    d, proj = _mk()
    try:
        code, msg, calls, _c = _drive(proj)
        check("--codex：plugin add 之后失败 → exit 2 且文案明说用户层此刻为 true",
              (code, wd.USER_LAYER_STILL_TRUE in msg, ["plugin", "add"] in [c[:2] for c in calls]), (wd.EXIT_ENV, True, True))
        code, msg, calls, _c = _drive(proj, upgrade=True)
        check("--upgrade（git 源）：同形失败不带用户层残留句", (code, wd.USER_LAYER_STILL_TRUE in msg), (wd.EXIT_ENV, False))
        # 本地源那一路走 `plugin add`，用户层已被写回 true ⇒ 同一个失败点的文案必须**带**那句话。
        # 两格必须并排看：只有下面这格时，「带」可能来自别的分支；只有上面那格时，漏写这条代价不会红。
        code, msg, calls, _c = _drive(proj, upgrade=True, source_type="local")
        check("--upgrade（本地源）：同形失败带用户层残留句", (code, wd.USER_LAYER_STILL_TRUE in msg), (wd.EXIT_ENV, True))
        check("--upgrade（本地源）：走的是 plugin add，marketplace upgrade 一次没跑",
              (["plugin", "add"] in [c[:2] for c in calls],
               ["plugin", "marketplace", "upgrade"] in [c[:3] for c in calls]), (True, False))
    finally:
        rm(d)


class LayeredServer:
    """按层建模：用户层 enabled（True / False / None）、项目 trust、项目段首启用表。`hooks/list` 条数 =
    （用户层 true）或（项目已 trust 且项目表 true）时的全部条目，否则 0；batchWrite 把 edits 落进对应层
    （`ignore` 里点名的层不落，模拟「写了 ok 但没生效」）。"""

    def __init__(self, keys, key, user=True, trusted=False, table=True, trusted_hooks=(), ignore=()):
        self.keys, self.key = keys, key
        self.user, self.trusted, self.table = user, trusted, table
        self.hook_trust = set(trusted_hooks)
        self.ignore = set(ignore)
        self.edits = None

    def full(self, k):
        return f"{PID}:hooks/hooks.codex.json:{k}"

    def _visible(self):
        """本插件的 hook 此刻在项目里可不可见——与 `config_read(cwd)` 的合成值**同一条规则**：项目已 trust 且项目表有值时
        项目层优先，否则看用户层（真机 6b-2 D7；门自己的 `zero_hooks_cause` 也是这么判）。两处各写一份时，用户层
        true、项目表 false 那一格会一处说可见、一处说合成值 false，桩自己跟自己对不上。"""
        eff = self.table if (self.trusted and self.table is not None) else self.user
        return eff is True

    def hooks_list(self, cwds):
        loaded = self._visible()
        hooks = [_hook(k, "trusted" if self.full(k) in self.hook_trust else "untrusted", root=str(PLUGIN))
                 for k in self.keys] if loaded else []
        return {"data": [{"cwd": cwds[0] if cwds else "", "hooks": hooks, "warnings": [], "errors": []}]}

    def config_batch_write(self, edits, **kw):
        self.edits = list(edits)
        for e in edits:
            kp = e["keyPath"]
            if kp.startswith("hooks.state.") and "hooks" not in self.ignore:
                self.hook_trust.add(kp.split('"')[1])
            elif kp.startswith("projects.") and "project" not in self.ignore:
                self.trusted = True
            elif kp.startswith("plugins.") and "user" not in self.ignore:
                self.user = e["value"]
        return {"status": "ok"}

    def config_read(self, _layers=False, cwd=None):
        up = {} if self.user is None else {PID: {"enabled": self.user}}
        cfg = {"projects": {self.key: {"trust_level": "trusted"}} if self.trusted else {}, "hooks": {"state": {}}}
        if cwd:
            eff = self.table if (self.trusted and self.table is not None) else self.user   # 与 _visible 同一条规则
            cfg["plugins"] = {} if eff is None else {PID: {"enabled": eff}}
        return {"config": cfg, "layers": [{"name": {"type": "user"}, "config": {"plugins": up}},
                                          {"name": {"type": "system"}, "config": {}}]}


def test_enable_layers():
    print("[door] 用户层置 false：edits、三层回读、0 条成因、--check 的用户层判定")
    manifest = json.loads((PLUGIN / "hooks" / "hooks.codex.manifest.json").read_text(encoding="utf-8"))
    keys = [e["key"].split(":", 1)[1] for e in manifest["entries"]]
    d, proj = _mk(gen=True)
    try:
        key = wd.project_key(proj)
        # 首装：用户层 true（plugin add 写回）、项目未 trust
        s = LayeredServer(keys, key, user=True, trusted=False)
        mine = wd.cas.plugin_hooks(s.hooks_list([str(proj)]), PID)
        check("首装写入前：用户层 true 时 17 条可见", len(mine), len(keys))
        check("首装：user_layer_enabled 读 layers[user]", wd.user_layer_enabled(s, PID), True)
        wd.step_write(s, mine, key, True, PID, disable_user=True)
        check("首装 edits = N 条 hook ＋ 项目 trust ＋ 用户层 false", len(s.edits), len(keys) + 2)
        check("首装 edits 末条是用户层 enabled = false", s.edits[-1],
              {"keyPath": f'plugins."{PID}".enabled', "mergeStrategy": "upsert", "value": False})
        check("首装 edits 倒数第二条是项目 trust", s.edits[-2]["keyPath"].startswith("projects."), True)
        try:
            mine2, trust = wd.step_readback(s, proj, PID, key, True, expected=len(keys))
            got = (s.user, len(mine2), trust)
        except wd.DoorError as e:
            got = f"DoorError {e.code}: {str(e)[:80]}"
        check("首装回读通过：用户层 false、合成值 true、17/17 trusted", got, (False, len(keys), "trusted"))
        # 同一 home 的第二个项目：hook 早已受信
        s2 = LayeredServer(keys, key, user=True, trusted=False, trusted_hooks={s.full(k) for k in keys})
        pending = [h for h in wd.cas.plugin_hooks(s2.hooks_list([str(proj)]), PID) if h["trustStatus"] != "trusted"]
        wd.step_write(s2, pending, key, True, PID, disable_user=True)
        check("第二个项目首装 edits = 项目 trust ＋ 用户层 false", [e["keyPath"].split(".", 1)[0] for e in s2.edits], ["projects", "plugins"])
        # --upgrade：用户层已 false → 不重复写
        s3 = LayeredServer(keys, key, user=False, trusted=True, trusted_hooks={s.full(k) for k in keys[1:]})
        pending = [h for h in wd.cas.plugin_hooks(s3.hooks_list([str(proj)]), PID) if h["trustStatus"] != "trusted"]
        wd.step_write(s3, pending, key, False, PID, disable_user=wd.user_layer_enabled(s3, PID) is True)
        check("--upgrade 用户层已 false：edits 只有那 1 条 hook、不含 plugins.*",
              (len(s3.edits), any(e["keyPath"].startswith("plugins.") for e in s3.edits)), (1, False))
        # 回读三层各自不符
        for label, kw, ignore, want_words, expected in [
            ("用户层没被写成 false", dict(user=True, trusted=False), {"user"}, ("用户层", "true"), len(keys)),
            ("项目表不是 true（合成值 false）", dict(user=True, trusted=False, table=False), set(), ("合成值",), len(keys)),
            ("hook 信任没生效", dict(user=True, trusted=False), {"hooks"}, ("未信任",), len(keys)),
            ("条数与写入前不同", dict(user=True, trusted=False), set(), ("回读", "条"), len(keys) + 1),
        ]:
            sx = LayeredServer(keys, key, ignore=ignore, **kw)
            mx = wd.cas.plugin_hooks(sx.hooks_list([str(proj)]), PID)
            wd.step_write(sx, mx, key, True, PID, disable_user=True)
            try:
                wd.step_readback(sx, proj, PID, key, True, expected=expected)
                check(f"回读 {label} → 5", "no error", wd.EXIT_READBACK)
            except wd.DoorError as e:
                check(f"回读 {label} → 5 且文案点名那一层", (e.code, all(w in str(e) for w in want_words)), (wd.EXIT_READBACK, True))
        check("插件 id 进 keyPath 前转义双引号与反斜杠", wd.plugin_disable_edit('core@a"b\\c')["keyPath"],
              'plugins."core@a\\"b\\\\c".enabled')
        check("用户层缺表读成 None", wd.user_layer_enabled(LayeredServer(keys, key, user=None), PID), None)
        try:
            no_user = type("NoUserLayer", (), {"config_read": lambda self, *a, **k: {
                "config": {}, "layers": [{"name": {"type": "system"}, "config": {}}]}})()
            wd.user_layer_enabled(no_user, PID)
            check("layers 里没有 user 层 → 抛而不是当作未启用", "no error", "AppServerError")
        except cas.AppServerError:
            check("layers 里没有 user 层 → 抛而不是当作未启用", "AppServerError", "AppServerError")
        # 0 条成因
        c = wd.zero_hooks_cause(LayeredServer(keys, key, user=False, trusted=False), proj, PID)
        check("0 条：用户层 false ＋ 项目未 trust → 点名 trust、出路先跑 --codex", "未 trust" in (c or "") and "--codex" in (c or ""), True)
        d2, bare = _mk()
        try:
            c = wd.zero_hooks_cause(LayeredServer(keys, wd.project_key(bare), user=False, trusted=True), bare, PID)
            check("0 条：用户层 false ＋ 已 trust ＋ 段内无启用表 → 点名启用表", "启用表" in (c or ""), True)
        finally:
            rm(d2)
        cfg = proj / cr.CONFIG_REL
        cfg.write_bytes(cfg.read_bytes().replace(b"\nenabled = true\n", b"\nenabled = false\n", 1))
        c = wd.zero_hooks_cause(LayeredServer(keys, key, user=False, trusted=True, table=False), proj, PID)
        check("0 条：段首启用表被用户改成 false → 说已关闭", "已被你关闭" in (c or ""), True)
        c = wd.zero_hooks_cause(LayeredServer(keys, key, user=True), proj, PID)
        check("0 条：用户层 true 但段首表是 false → 说项目层优先、不归咎缺 command 键", "项目层优先" in (c or ""), True)
        cfg.write_bytes(cfg.read_bytes().replace(b"\nenabled = false\n", b"\nenabled = false  # off\n", 1))
        c = wd.zero_hooks_cause(LayeredServer(keys, key, user=True), proj, PID)
        check("0 条：用户层 true、段首表是带注释的 false → 提示只改值不加注释", "不加注释" in (c or ""), True)
        d3, fresh = _mk(gen=True)
        try:
            check("0 条：用户层 true 且段首表 true → 不归因（交给整份未解析那句）",
                  wd.zero_hooks_cause(LayeredServer(keys, wd.project_key(fresh), user=True), fresh, PID), None)
        finally:
            rm(d3)
        # --check 的用户层判定（算问题还是 info 收在一个常量里）
        check("USER_LAYER_TRUE_IS_PROBLEM（用户拍板：算问题）", wd.USER_LAYER_TRUE_IS_PROBLEM, True)
        is_p, line = wd.user_layer_finding(PID, True)
        check("--check 用户层 true → 问题，文案给现状 ＋ 出路 ＋ 成因", (is_p, all(w in line for w in ("enabled = true", "--codex", "plugin add"))), (True, True))
        check("--check 用户层缺表 → 放行", wd.user_layer_finding(PID, None)[0], False)
        check("--check 用户层 false → 放行", wd.user_layer_finding(PID, False)[0], False)
    finally:
        rm(d)
    # check_static 端到端（app-server 打桩）：用户层 true 进 problems；已关态进 info 且 0 条不算问题
    for label, user, close, want_problem, want_info in [
        ("用户层 true", True, False, "enabled = true", None),
        ("用户层缺表", None, False, None, "≡ 未启用"),
        ("本项目已关", False, True, None, "已被你关闭"),
    ]:
        d, proj = _mk(gen=True)
        home = d / "home"
        home.mkdir()
        env_backup = dict(os.environ)
        saved = (wd.cas.AppServer, wd.run_codex)
        try:
            os.environ["CODEX_HOME"] = str(home)
            if close:
                cfg = proj / cr.CONFIG_REL
                cfg.write_bytes(cfg.read_bytes().replace(b"\nenabled = true\n", b"\nenabled = false\n", 1))
            key = wd.project_key(proj)
            srv = LayeredServer(keys, key, user=user, trusted=True, table=not close,
                                trusted_hooks={f"{PID}:hooks/hooks.codex.json:{k}" for k in keys})

            class Ctx:
                def __init__(self, *a, **k):
                    pass

                def __enter__(self):
                    return srv

                def __exit__(self, *a):
                    return False
            wd.cas.AppServer = Ctx
            wd.run_codex = lambda *a, **k: (0, "multi_agent stable true", "")
            problems, info, _mine = wd.check_static("codex", proj, MKT)
            if want_problem:
                check(f"check_static {label}：进 problems", any(want_problem in p for p in problems), True)
            else:
                check(f"check_static {label}：用户层那句不进 problems", any("用户配置" in p or "用户层" in p for p in problems), False)
            if want_info:
                check(f"check_static {label}：进 info", any(want_info in i for i in info), True)
            if close:
                check("check_static 已关：0 条不算问题、verify 不报被手改",
                      any("hooks/list 里没有" in p or "被手改" in p for p in problems), False)
        finally:
            wd.cas.AppServer, wd.run_codex = saved
            os.environ.clear()
            os.environ.update(env_backup)
            rm(d)


def test_marketplace_add_name_taken():
    """同名不同源被 Codex 拒时给人话出路；**别的失败仍原样透传 stderr**。

    分岔对照是这条测试的一半：只断言「命中时给人话」证不出判别式真的在判别——
    再打一次形态不同的失败（stderr 无该句），要求它**不**走人话分支、原文照旧带出来。
    真实 stderr 取自隔离 CODEX_HOME 的实测（codex-cli 0.153.x）：rc=1、stdout 空。
    **被拒是有前提的**（已登记的那一份仍然有效，且不是「已登记本地源、这次加 git 源、克隆目录不在」——那一格 Codex
    不拒、直接换掉登记）：这里钉的是被拒那一态的文案，以及它不再无条件说「已登记的那份没有被改动」、出路不再绕回会被拒的那一步。
    """
    real = "Error: marketplace 'workframe' is already added from a different source; remove it before adding this source"

    def drive(rc, out, err):
        saved = wd.run_codex
        wd.run_codex = lambda exe, args, cwd=None, timeout=180, env=None: (rc, out, err)
        try:
            wd.step_marketplace_add("codex", "ryanzhao1011/workframe", None)
        except wd.DoorError as e:
            return e.code, str(e)
        finally:
            wd.run_codex = saved
        return None, ""

    code, msg = drive(1, "", real)
    check("同名不同源：exit 2", code, wd.EXIT_ENV)
    check("同名不同源：给的是人话出路，不是英文原话",
          (wd.MARKETPLACE_NAME_TAKEN in msg, "already added from a different source" in msg), (True, False))
    check("同名不同源：文案点名两条出路（--marketplace / 自己 remove）",
          ("--marketplace" in msg and "remove" in msg), True)
    check("同名不同源：给 remove 那条出路时明说它会删掉用户配置里的那条市场注册", "删掉那条市场注册" in msg, True)
    # 出路不绕圈：不再无条件断言「已登记的那份没有被改动」（它可能早被换成了 git 源）；让人看的是 `--json`（文本输出
    # 只有两列、看不出源形态）；root 是 Codex 克隆目录时不能拿它当 `--marketplace`（实测照样被拒）
    check("同名不同源：不再无条件说「已登记的那份没有被改动」，改说「这一次」没改动、它未必还是原来那份",
          ("已登记的那份没有被改动" in msg, "这一次没有改动已登记的那份" in msg, "未必还是你原来登记的" in msg),
          (False, True, True))
    check("同名不同源：让人跑 `marketplace list --json`、凭 marketplaceSource / 克隆目录认 git 登记、不拿克隆目录当源",
          ("`codex plugin marketplace list --json`" in msg, "marketplaceSource" in msg,
           "<CODEX_HOME>/.tmp/marketplaces/" in msg, "别拿克隆目录当 `--marketplace`" in msg), (True, True, True, True))
    # 版本对不上时的出路（`_door_outlets`）：「分不清」那句在 git 登记、要本地源的那一态直接给 remove（① ② 都走不通）
    outlets = wd._door_outlets()
    check("版本对不上的出路：「分不清」那句用 --json 看、点明 git 登记而要本地源时只能自己先 remove（删用户配置里的注册）",
          ("`codex plugin marketplace list --json`" in outlets, "只能你自己先跑 `codex plugin marketplace remove <名>`" in outlets,
           "删掉那条市场注册" in outlets, "本命令不代做" in outlets), (True, True, True, True))
    code, msg = drive(1, "", "boom: network unreachable")
    check("别的失败：不走人话分支、stderr 原样透传",
          (code, wd.MARKETPLACE_NAME_TAKEN in msg, "boom" in msg), (wd.EXIT_ENV, False, True))
    # 成功时把 alreadyAdded 一起交回：装前核对停下时要据此如实说步 2 是新登记还是早已登记（两格并排，缺一格时「恒真 / 恒假」都不红）
    for label, body, want in [("回报 alreadyAdded:true", {"marketplaceName": MKT, "alreadyAdded": True}, (MKT, True)),
                              ("回报 alreadyAdded:false", {"marketplaceName": MKT, "alreadyAdded": False}, (MKT, False)),
                              ("没有 alreadyAdded 键", {"marketplaceName": MKT}, (MKT, False))]:
        saved = wd.run_codex
        wd.run_codex = lambda exe, args, cwd=None, timeout=180, env=None, body=body: (0, json.dumps(body), "")
        try:
            got = wd.step_marketplace_add(_NO_CODEX, "ryanzhao1011/workframe", None)
        finally:
            wd.run_codex = saved
        check(f"marketplace add {label}：返回 (回显的市场名, 是否早已登记)", got, want)


def _run_codex_stub(handler):
    """把 `wd.run_codex` 换成按 argv 应答的 handler，返回 (calls, restore)。"""
    calls = []

    def fake(exe, args, cwd=None, timeout=180, env=None):
        calls.append(list(args))
        return handler(list(args))

    saved = wd.run_codex

    def restore():
        wd.run_codex = saved

    wd.run_codex = fake
    return calls, restore


def test_upgrade_source_routing():
    """`--upgrade` 按市场源形态选路：源形态从哪读、选哪条、判不出时怎么落。

    这一组钉的是 TASK-133 的故障形态——`codex plugin marketplace upgrade` 对本地目录源恒 rc=1
    且 **stdout 空**，于是 `_json_out` 返 None、门以 EXIT_ENV 停下，用户在开发机上永远升不了级。
    """
    print("[upgrade] 市场源形态识别与两条升级路径")
    PID = f"core@{MKT}"

    # ① 源形态只从 `plugin list` 读——`marketplace list` 只在 git 登记的那一行带源字段、本地登记的没有，问它认不出本地源
    for label, rc, out, want in [
        ("本地源 → local", 0, _plugin_list_payload("local"), "local"),
        ("git 源 → git", 0, _plugin_list_payload("git"), "git"),
        # ⚠️ 这一格的 stdout 必须是**合法 JSON**：给空串时 `_json_out` 本来就返 None，于是
        #    「rc != 0 就不解析」那道守卫被改坏也不会红。用一份合法且能命中的载荷，红绿只由 rc 决定。
        ("命令失败（stdout 仍是合法载荷）→ None", 1, _plugin_list_payload("local"), None),
        ("stdout 不是 JSON → None", 0, "not json", None),
        ("本市场没有已装插件 → None", 0, json.dumps({"installed": [], "available": []}), None),
        # 只有别的市场的条目 ⇒ 「按市场名过滤」那半有对象；它被改坏时会取到 other 的 local
        ("只有别的市场的条目 → None", 0,
         json.dumps({"installed": [{"pluginId": "documents@other", "marketplaceName": "other",
                                    "marketplaceSource": {"sourceType": "local", "source": "x"}}]}), None),
        ("条目缺 marketplaceSource → None", 0,
         json.dumps({"installed": [{"marketplaceName": MKT}]}), None),
    ]:
        calls, restore = _run_codex_stub(lambda a, rc=rc, out=out: (rc, out, ""))
        try:
            got = wd.marketplace_source_type("codex", MKT, None)
        finally:
            restore()
        check(f"source_type：{label}", got, want)
        check(f"source_type：{label} 问的是 plugin list 而不是 marketplace list",
              calls, [["plugin", "list", "--json"]])

    def answer(source_type=None, upgrade=(0, "{}", ""), add=(0, json.dumps(
            {"installedPath": str(PLUGIN), "version": "1.1.0"}), "")):
        def h(args):
            if args[:3] == ["plugin", "list", "--json"]:
                return (0, _plugin_list_payload(source_type), "") if source_type else (1, "", "no")
            if args[:3] == ["plugin", "marketplace", "upgrade"]:
                return upgrade
            if args[:2] == ["plugin", "add"]:
                return add
            return 1, "", "unexpected"
        return h

    def drive_upgrade(handler):
        calls, restore = _run_codex_stub(handler)
        try:
            return wd.step_upgrade("codex", MKT, PID, None), None, calls
        except wd.DoorError as e:
            return None, e, calls
        finally:
            restore()

    # ② 本地源：直接走 plugin add，**不先跑一条必然失败的 marketplace upgrade**
    got, err, calls = drive_upgrade(answer(source_type="local"))
    check("本地源：返回 (安装根, 用户层被写回 true)", got, (PLUGIN, True))
    # 第二条是装前核对（收在 step_plugin_add 入口）：这里的桩答不出 core 那一行，它放行、照常重装
    check("本地源：marketplace upgrade 一次没跑（先判源形态，再装前核对，再重装）",
          calls, [["plugin", "list", "--json"], ["plugin", "list", "--json", "--available", "-m", MKT],
                  ["plugin", "add", PID, "--json"]])

    # ③ git 源：走 marketplace upgrade，不碰 plugin add、不动用户层
    got, err, calls = drive_upgrade(answer(source_type="git"))
    check("git 源：返回 (None, 用户层未动)", got, (None, False))
    check("git 源：plugin add 没跑", ["plugin", "add"] in [c[:2] for c in calls], False)

    # ④ 分辨不出 ＋ upgrade 报「不是 Git 市场」→ 落到 plugin add（判不出 ≠ 升不了）
    got, err, calls = drive_upgrade(answer(
        source_type=None, upgrade=(1, "", f"marketplace '{MKT}' {wd.UPGRADE_NOT_GIT}; remove it")))
    check("分辨不出 ＋ 被拒：落到 plugin add 并报出用户层被写回", got, (PLUGIN, True))
    check("分辨不出 ＋ 被拒：先试过 marketplace upgrade 才落（落回的那一路同样先过装前核对）",
          calls, [["plugin", "list", "--json"], ["plugin", "marketplace", "upgrade", MKT, "--json"],
                  ["plugin", "list", "--json", "--available", "-m", MKT], ["plugin", "add", PID, "--json"]])

    # ④' 同一句拒绝落在 **stdout** 上时也要认出来（真机上它在 stderr，这一支是防御性冗余；
    #     没有这一格时「只看 stderr」被改坏也不会红）
    got, err, calls = drive_upgrade(answer(
        source_type=None, upgrade=(1, f"marketplace '{MKT}' {wd.UPGRADE_NOT_GIT}", "")))
    check("拒绝落在 stdout 上：同样落到 plugin add", got, (PLUGIN, True))

    # ⑤ 分辨不出 ＋ 别的失败：照常 EXIT_ENV，**不得**顺手去重装（那会把网络故障变成一次静默重装）
    got, err, calls = drive_upgrade(answer(source_type=None, upgrade=(1, "", "boom: network unreachable")))
    check("分辨不出 ＋ 别的失败：exit 2 且 stderr 原样透传",
          (got, _code(err), "boom" in str(err)), (None, wd.EXIT_ENV, True))
    check("分辨不出 ＋ 别的失败：plugin add 没跑", ["plugin", "add"] in [c[:2] for c in calls], False)

    # ⑥ git 源 upgrade 回了 errors：仍是 EXIT_ENV（这条分支本批没动，一并钉住防回归）
    got, err, calls = drive_upgrade(answer(source_type="git", upgrade=(0, json.dumps({"errors": ["x"]}), "")))
    check("git 源 upgrade 报 errors：exit 2", (got, _code(err)), (None, wd.EXIT_ENV))


def test_backfill():
    """`--backfill`：检测哪扇门未接 → 只补缺的那扇 → 重跑零改动 → 不碰用户关掉的门。

    两条承重断言互为对照，缺一不可：**已接的那扇门根本不调写入方**（不是「调了但没写」），
    以及**拿不到市场来源时零写入**（宁可停下，也不拿猜的值写一份会进 git 的声明）。
    """
    print("[backfill] 只补缺的那扇门 / 幂等 / 不猜市场源")
    PID = f"{wd.PLUGIN_NAME}@{MKT}"

    def _mk_args(marketplace=None):
        class _A:
            pass
        a = _A()
        a.marketplace = wd.DEFAULT_MARKETPLACE if marketplace is None else marketplace
        a.marketplace_name = MKT
        return a

    def drive(proj, cc_settings=None, codex_ok=False, located=(None, None, "stub：没有注册面"),
              exe="codex", cap=False, marketplace=None):
        """打桩跑 do_backfill：locate_market / do_codex / write_subscription 全部记录调用。
        `cap=True` 时把 `say` 的输出也收进 `seen["out"]`（验收尾那句话的措辞）。"""
        (proj / ".claude").mkdir(parents=True, exist_ok=True)
        if cc_settings is not None:
            (proj / ".claude" / "settings.json").write_bytes(
                json.dumps(cc_settings, ensure_ascii=False).encode("utf-8"))
        seen = {"codex": 0, "sub": [], "located": 0, "out": "", "codex_wrote": "没调过 do_codex", "rv": "没返回"}
        import project_scaffold as ps
        saved = (wd.locate_market, wd.do_codex, ps.write_subscription, wd.cas.find_codex_exe,
                 wd.codex_door_state)

        def fake_locate(mkt, exe=None, project=None):
            seen["located"] += 1
            return located

        def fake_sub(project_dir, sub):
            # 像真的那样落盘：项目 settings ＋ 原文件在时往 logs/ 放一份备份（真实实现的写入面同此）——
            # 回填按写入面前后比对得出「本次写过哪些项目文件」，桩不落盘的话那份清单就恒为空
            seen["sub"].append(dict(sub))
            sp = Path(project_dir) / ".claude" / "settings.json"
            if sp.is_file():
                (Path(project_dir) / "logs").mkdir(parents=True, exist_ok=True)
                shutil.copyfile(sp, Path(project_dir) / "logs" / "settings.json.bak-stub")
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_bytes(b'{"stub": "subscription"}')
            return True, ["stub: wrote subscription"], None

        def fake_codex(a, p, upgrade=False, wrote=None):
            # 收下 `wrote`：do_backfill 把它自己写过的项目文件交给 do_codex，后者的失败文案据此说写没写
            seen["codex"] += 1
            seen["codex_wrote"] = None if wrote is None else list(wrote)

        wd.locate_market = fake_locate
        wd.do_codex = fake_codex
        ps.write_subscription = fake_sub
        wd.cas.find_codex_exe = lambda: exe
        wd.codex_door_state = lambda p, m: (("ok", "stub 已接") if codex_ok is True else
                                            codex_ok if isinstance(codex_ok, tuple) else
                                            ("missing", "stub 未接"))
        saved_say = wd.say
        if cap:
            def cap_say(msg=""):
                seen["out"] += str(msg) + "\n"
            wd.say = cap_say
        a = _mk_args(marketplace)
        try:
            seen["rv"] = wd.do_backfill(a, proj)
            return None, seen, a
        except wd.DoorError as e:
            return e, seen, a
        finally:
            wd.say = saved_say
            (wd.locate_market, wd.do_codex, ps.write_subscription, wd.cas.find_codex_exe,
             wd.codex_door_state) = saved

    SUBSCRIBED = {"extraKnownMarketplaces": {MKT: {"source": {"source": "directory", "path": "x"}}},
                  "enabledPlugins": {PID: True}}
    SRC = ({"source": "directory", "path": "D:/fw"}, "D:/fw", "stub 注册面")

    # ① 两扇门都接 → 零写入、连注册面都不去问
    d = Path(tempfile.mkdtemp())
    try:
        err, seen, _a = drive(d, cc_settings=SUBSCRIBED, codex_ok=True, cap=True)
        check("两扇门都接：不抛、零写入", (err, seen["codex"], seen["sub"]), (None, 0, []))
        check("两扇门都接：连市场注册面都没去问", seen["located"], 0)
        # Codex 门的「接上」只看了项目文件（clone 到第二台机器时照样是 ok）⇒ 收尾不许说「两扇门都已接上」，
        # 要说项目侧已接、本机未核、跑 --check；与下面「Codex 门是本次装的」那格并排看
        check("两扇门都接：收尾说「项目侧已接；本机未核，跑 --check」、不说「两扇门都已接上」",
              ("项目侧已接" in seen["out"], "本机未核" in seen["out"], "workframe-door --check" in seen["out"],
               "两扇门都已接上" in seen["out"]), (True, True, True, False))
        # [3/3] 那一行自己也要说「项目侧」「本机没核」「跑 --check」——只看收尾那句的话，这一行退回旧文案照样全绿
        step3 = [ln for ln in seen["out"].splitlines() if ln.startswith("[3/3]")]
        check("两扇门都接：[3/3] 行说「项目侧已接」、本机那一侧没核、给出 --check",
              (len(step3), bool(step3) and "项目侧已接" in step3[0], bool(step3) and "本机那一侧" in step3[0],
               bool(step3) and "workframe-door --check" in step3[0]), (1, True, True, True))
        check("两扇门都接：do_backfill 返回 True（结束时 Codex 门在项目侧接着 ⇒ main 打 AGENTS.md 提示）", seen["rv"], True)
    finally:
        rm(d)

    # ② 只缺 CC → 写订阅声明；Codex 那边一次都不调
    d = Path(tempfile.mkdtemp())
    try:
        err, seen, _a = drive(d, cc_settings={}, codex_ok=True, located=SRC)
        check("只缺 CC：写了订阅声明、do_codex 没跑", (err, seen["codex"], len(seen["sub"])), (None, 0, 1))
        check("只缺 CC：三个字段都来自注册面（source 是对象、不是落地路径）",
              seen["sub"][0], {"marketplace_name": MKT, "source": SRC[0], "install_location": SRC[1]})
    finally:
        rm(d)

    # ③ 只缺 Codex → 走 do_codex；**不碰 settings**，且市场源换成项目已在用的那个
    d = Path(tempfile.mkdtemp())
    try:
        err, seen, a = drive(d, cc_settings=SUBSCRIBED, codex_ok=False, located=SRC, cap=True)
        check("只缺 Codex：do_codex 跑了一次、settings 没被写", (err, seen["codex"], seen["sub"]), (None, 1, []))
        check("只缺 Codex、本次装上：收尾说「两扇门都已接上」（do_codex 已回读核过本机那一侧）、不说「本机未核」",
              ("两扇门都已接上" in seen["out"], "本机未核" in seen["out"]), (True, False))
        check("只缺 Codex、本次装上：do_backfill 返回 True", seen["rv"], True)
        check("只缺 Codex：市场源取项目已在用的来源，不用默认公开仓", a.marketplace, "D:/fw")
    finally:
        rm(d)

    # ④ 缺 CC 而拿不到市场来源 → 停下且零写入（不猜）
    d = Path(tempfile.mkdtemp())
    try:
        err, seen, _a = drive(d, cc_settings={}, codex_ok=True, located=(None, None, "拿不到"))
        check("拿不到市场来源：exit 2 且一个字节没写",
              (_code(err), seen["sub"], seen["codex"]), (wd.EXIT_ENV, [], 0))
    finally:
        rm(d)

    # ④' 缺 CC 但只拿到 source、拿不到 installLocation（第 ③ 来源＝本项目 settings 的形态）
    #     → 停下：用户级注册表要 installLocation，猜一个写进去等于给这台机器编了一条注册
    d = Path(tempfile.mkdtemp())
    try:
        err, seen, _a = drive(d, cc_settings={}, codex_ok=True,
                              located=(SRC[0], None, "本项目 settings（没有 installLocation）"))
        check("只有 source、没有 installLocation：CC 那半停下且零写入",
              (_code(err), seen["sub"]), (wd.EXIT_ENV, []))
    finally:
        rm(d)

    # ④'' **A-1 的那一格**：CC 已接 ＋ Codex 缺 ＋ 注册面落空 ⇒ 必须停下。
    #      早先这里不抛：`source_to_cli(None)` 返 None ⇒ 市场源覆盖被跳过 ⇒ do_codex 拿
    #      DEFAULT_MARKETPLACE（公开仓）去注册，把本项目悄悄接到另一个框架副本上。
    #      可达性不是理论的：clone 到第二台机器正是这个组合。
    d = Path(tempfile.mkdtemp())
    try:
        err, seen, a = drive(d, cc_settings=SUBSCRIBED, codex_ok=False, located=(None, None, "拿不到"))
        check("CC 已接 + Codex 缺 + 拿不到来源：exit 2、do_codex 不跑",
              (_code(err), seen["codex"]), (wd.EXIT_ENV, 0))
        check("CC 已接 + Codex 缺 + 拿不到来源：**没有退回默认公开仓源**",
              a.marketplace, wd.DEFAULT_MARKETPLACE)
        # 两道守卫串在一起（`source is None` 与 `source_to_cli(...) is None`），**任一道单独在场
        # 都能把 do_codex 挡住** ⇒ 只看「有没有停下」分不出是哪一道在起作用。第一道的独立价值
        # 在**文案**：它说得出「拿不到来源、出处是哪」，第二道只说得出「认不出形态 None」。
        check("CC 已接 + Codex 缺 + 拿不到来源：文案点名「拿不到来源」而不是「认不出形态」",
              ("拿不到市场来源" in str(err), "认不出市场源形态" in str(err)), (True, False))
    finally:
        rm(d)

    # ④''' 认不出的 source 形态同样停下（不瞎拼一个串去注册市场）
    d = Path(tempfile.mkdtemp())
    try:
        err, seen, _a = drive(d, cc_settings=SUBSCRIBED, codex_ok=False,
                              located=({"source": "weird"}, "D:/fw", "stub 注册面"))
        check("认不出的 source 形态：exit 2、do_codex 不跑", (_code(err), seen["codex"]), (wd.EXIT_ENV, 0))
    finally:
        rm(d)

    # ④'''' 用户显式给了 --marketplace ⇒ 以他的为准：不顶替、连注册面都不去问
    d = Path(tempfile.mkdtemp())
    try:
        err, seen, a = drive(d, cc_settings=SUBSCRIBED, codex_ok=False, located=SRC,
                             marketplace="D:/user-given")
        check("显式 --marketplace：不被代码顶替", a.marketplace, "D:/user-given")
        check("显式 --marketplace：do_codex 跑了、且连注册面都没去问",
              (seen["codex"], seen["located"]), (1, 0))
    finally:
        rm(d)

    # ⑤ 用户自己关掉的 Codex 门 → 不替他改回来
    d = Path(tempfile.mkdtemp())
    try:
        err, seen, _a = drive(d, cc_settings=SUBSCRIBED, codex_ok=("closed", "已被你关闭"), cap=True)
        check("用户已关的 Codex 门：不抛、do_codex 不跑", (err, seen["codex"]), (None, 0))
        check("用户已关的 Codex 门：do_backfill 返回 False（main 不打 AGENTS.md 提示）", seen["rv"], False)
        check("用户已关的 Codex 门：收尾不说「两扇门都已接上」，点名仍未接的那扇",
              ("仍未接上" in seen["out"], "两扇门都已接上" in seen["out"]), (True, False))
    finally:
        rm(d)

    # ⑥ managed 段被手改 → 写任何文件之前停下
    d = Path(tempfile.mkdtemp())
    try:
        err, seen, _a = drive(d, cc_settings={}, codex_ok=("edited", "被手改"), located=SRC)
        check("managed 段被手改：exit 3 且零写入（CC 那半也不写）",
              (_code(err), seen["sub"], seen["codex"]), (wd.EXIT_COUNT, [], 0))
    finally:
        rm(d)

    # ⑦ settings 解析不了 → 不拿写入去盖坏文件
    d = Path(tempfile.mkdtemp())
    try:
        (d / ".claude").mkdir(parents=True, exist_ok=True)
        (d / ".claude" / "settings.json").write_bytes(b"{ not json")
        err, seen, _a = drive(d, cc_settings=None, codex_ok=True, located=SRC)
        check("settings 坏了：exit 3 且零写入", (_code(err), seen["sub"]), (wd.EXIT_COUNT, []))
    finally:
        rm(d)

    # ⑧ 缺 Codex 但本机没有 codex → 跳过，不当失败
    d = Path(tempfile.mkdtemp())
    try:
        err, seen, _a = drive(d, cc_settings=SUBSCRIBED, codex_ok=False, exe=None)
        check("没装 codex：不抛、do_codex 不跑", (err, seen["codex"]), (None, 0))
        check("没装 codex：do_backfill 返回 False（Codex 门仍未接）", seen["rv"], False)
    finally:
        rm(d)

    # ⑨ cc_door_state / codex_door_state 各自的真实判定（上面那些格把它们打桩掉了）
    d = Path(tempfile.mkdtemp())
    try:
        check("cc_door_state：没有 settings → missing", wd.cc_door_state(d, MKT), "missing")
        (d / ".claude").mkdir(parents=True, exist_ok=True)
        (d / ".claude" / "settings.json").write_bytes(
            json.dumps(SUBSCRIBED, ensure_ascii=False).encode("utf-8"))
        check("cc_door_state：两项声明齐全 → ok", wd.cc_door_state(d, MKT), "ok")
        (d / ".claude" / "settings.json").write_bytes(
            json.dumps({"enabledPlugins": {PID: False}}, ensure_ascii=False).encode("utf-8"))
        check("cc_door_state：enabledPlugins 是 false → missing", wd.cc_door_state(d, MKT), "missing")
        (d / ".claude" / "settings.json").write_bytes(b"\xef\xbb\xbf{ not json")
        check("cc_door_state：坏 settings → broken（不是 missing）", wd.cc_door_state(d, MKT), "broken")
        check("codex_door_state：没有 .codex/config.toml → missing", wd.codex_door_state(d, MKT)[0], "missing")
    finally:
        rm(d)

    # ⑩ source_to_cli：三种形态各取对，认不出返回 None（宁可退回默认源，也不瞎拼一个）
    for src, want in [({"source": "directory", "path": "D:/fw"}, "D:/fw"),
                      ({"source": "github", "repo": "owner/repo"}, "owner/repo"),
                      ({"source": "git", "url": "https://x/y.git"}, "https://x/y.git"),
                      ({"source": "weird"}, None), ("not-a-dict", None), ({}, None)]:
        check(f"source_to_cli({src!r})", wd.source_to_cli(src), want)

    # ⑪ 「写了 / 没写」如实：CC 那半写过订阅声明、Codex 那半再停下 ⇒ 文案点名写过的文件，不说「未写任何文件」
    d = Path(tempfile.mkdtemp())
    try:
        err, seen, _a = drive(d, cc_settings={}, codex_ok=False,
                              located=({"source": "weird"}, "D:/fw", "stub 注册面"))
        check("CC 已写、Codex 认不出源形态而停下：exit 2、点名 .claude/settings.json、不说未写任何文件",
              (_code(err), ".claude/settings.json" in str(err), "未写任何文件" in str(err)),
              (wd.EXIT_ENV, True, False))
        check("CC 已写：写入清单列全——logs/ 下的备份也在（写入面前后比对得出，不是手写一条路径）",
              "logs/settings.json.bak-stub" in str(err), True)
    finally:
        rm(d)
    # ⑪' 交给 do_codex 的写入记录：CC 写过就带着它，没写就是空表（两格并排，缺一格时「永远带 / 永远空」都不红）
    for label, cc, want in [("CC 已写", {}, [".claude/settings.json", "logs/settings.json.bak-stub"]),
                            ("CC 原本就接着", SUBSCRIBED, [])]:
        d = Path(tempfile.mkdtemp())
        try:
            err, seen, _a = drive(d, cc_settings=cc, codex_ok=False, located=SRC)
            check(f"{label}：交给 do_codex 的写入记录", (err, seen["codex_wrote"]), (None, want))
        finally:
            rm(d)
    # ⑫ 门自己的插件根读不出版本、而这次要装 Codex 门 ⇒ 在写 CC 那半之前就停（装后核对必停，而那时 CC 已经写了）；
    #    对照：本机没有 codex、不装 Codex 门时，门自己的版本与这次无关，CC 那半照补（两格并排，缺一格时「恒停」
    #    或「恒不停」都不红）
    blind_root = Path(tempfile.mkdtemp(prefix="blinddoor-"))
    try:
        _plugin_root_fixture(blind_root / "core", codex=False, claude=False)
        for label, exe, want in [("要装 Codex 门", "codex", (wd.EXIT_ENV, 0, True)),
                                 ("本机没有 codex", None, (None, 1, False))]:
            d = Path(tempfile.mkdtemp())
            try:
                with _DoorAt(blind_root / "core"):
                    err, seen, _a = drive(d, cc_settings={}, codex_ok=False, located=SRC, exe=exe)
                check(f"门自己读不出版本、{label}：退出码 / CC 那半写没写 / 文案点名门自己的根",
                      (_code(err), len(seen["sub"]), "本命令自己所在的插件根" in str(err)), want)
            finally:
                rm(d)
    finally:
        rm(blind_root)


def test_locate_market():
    """`locate_market` / `_unc_strip` / `_project_declared_source` / `_cc_registry_entry` 的真实执行。

    **为什么单列一组**：`test_backfill` 把 `locate_market` 整个打桩掉了，于是这个函数——**唯一决定
    「往进 git 的 settings 里写什么值」的那个**——此前一次都没被执行过；三条安全属性（① 要 source ＋
    installLocation 成对、② 非 `local` 的 Codex 源不代填、③ `\\\\?\\` 前缀两路都剥）被改坏时没有任何
    断言说话。

    隔离：`Path.home()` 走 `os.path.expanduser("~")`，Windows 每次调用现读 `USERPROFILE`
    ⇒ 指向临时假 home，真实的 `~/.claude/plugins/known_marketplaces.json` **全程零接触**。
    """
    print("[locate] 市场来源三级定位（真实执行，假 home）")

    # _unc_strip：剥 / 不剥 / 空值 / **UNC 网络路径不许被误剥**
    for raw, want in [("\\\\?\\C:\\dev\\fw", "C:\\dev\\fw"), ("C:\\dev\\fw", "C:\\dev\\fw"),
                      (None, ""), ("", ""), ("\\\\server\\share", "\\\\server\\share")]:
        check(f"_unc_strip({raw!r})", wd._unc_strip(raw), want)

    home = Path(tempfile.mkdtemp(prefix="home-"))
    proj = Path(tempfile.mkdtemp(prefix="proj-"))
    old_home = (os.environ.get("USERPROFILE"), os.environ.get("HOME"))
    reg = home / ".claude" / "plugins" / "known_marketplaces.json"

    def put_reg(obj):
        reg.parent.mkdir(parents=True, exist_ok=True)
        if obj is None:
            if reg.is_file():
                reg.unlink()
            return
        reg.write_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def put_settings(obj):
        (proj / ".claude").mkdir(parents=True, exist_ok=True)
        p = proj / ".claude" / "settings.json"
        if obj is None:
            if p.is_file():
                p.unlink()
            return
        p.write_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    DIR_SRC = {"source": "directory", "path": "D:/fw"}
    try:
        os.environ["USERPROFILE"] = str(home)
        os.environ["HOME"] = str(home)

        # ① CC 用户级注册表：成对才算命中；缺 installLocation / source 非对象 / 文件不在都要落空
        put_reg({MKT: {"source": DIR_SRC, "installLocation": "D:/fw"}})
        check("① 命中：返回 (source 对象, installLocation, 出处)",
              wd.locate_market(MKT), (DIR_SRC, "D:/fw", "CC 用户级市场注册表"))
        put_reg({MKT: {"source": DIR_SRC, "installLocation": "\\\\?\\D:\\fw"}})
        check("① installLocation 的 \\\\?\\ 前缀也被剥（与 source 同一口径）",
              wd.locate_market(MKT)[1], "D:\\fw")
        put_reg({MKT: {"source": DIR_SRC}})
        check("① 缺 installLocation → 不算命中", wd.locate_market(MKT)[0], None)
        put_reg({MKT: {"source": "D:/fw", "installLocation": "D:/fw"}})
        check("① source 不是对象 → 不算命中", wd.locate_market(MKT)[0], None)
        put_reg(None)
        check("① 注册表文件不在 → 不算命中", wd.locate_market(MKT)[0], None)
        check("_cc_registry_entry：注册表不在时返回 None", wd._cc_registry_entry(MKT), None)

        # ③ 本项目 settings：只有 source、没有 installLocation，够补 Codex 那扇门
        put_settings({"extraKnownMarketplaces": {MKT: {"source": DIR_SRC}}})
        src, loc, via = wd.locate_market(MKT, exe=None, project=proj)
        check("③ 项目 settings 兜底：拿到 source、installLocation 是 None", (src, loc), (DIR_SRC, None))
        check("③ 出处点名这是项目 settings", "settings" in via, True)
        check("_project_declared_source 直判", wd._project_declared_source(proj, DIR_SRC and MKT), DIR_SRC)
        check("_project_declared_source：市场名不同 → None", wd._project_declared_source(proj, "other"), None)
        put_settings({"extraKnownMarketplaces": {MKT: {"source": "not-an-object"}}})
        check("③ source 不是对象 → None", wd._project_declared_source(proj, MKT), None)
        put_settings(None)
        check("③ 没有 settings 且没有 codex → 全落空",
              wd.locate_market(MKT, exe=None, project=proj)[0], None)

        # ② Codex 注册面：只认 source_type = local；非 local 且项目自己没声明 ⇒ 不代填
        cfg_home = Path(tempfile.mkdtemp(prefix="codexhome-"))
        saved_codex_home = os.environ.get("CODEX_HOME")
        saved_run = wd.run_codex
        try:
            os.environ["CODEX_HOME"] = str(cfg_home)

            def fake_run(exe, args, cwd=None, timeout=180, env=None):
                if args[:3] == ["plugin", "marketplace", "list"]:
                    return 0, json.dumps({"marketplaces": [
                        {"name": "other", "root": "C:\\other"},
                        {"name": MKT, "root": "\\\\?\\C:\\dev\\fw"}]}), ""
                return 1, "", "unexpected"
            wd.run_codex = fake_run

            def put_toml(body):
                (cfg_home / "config.toml").write_bytes(body.encode("utf-8"))

            put_toml(f'[marketplaces.{MKT}]\nsource = "\\\\\\\\?\\\\C:\\\\dev\\\\fw"\n'
                     f'source_type = "local"\n')
            src, loc, via = wd.locate_market(MKT, exe="codex", project=proj)
            check("② local 源：source 对象剥了前缀", src, {"source": "directory", "path": "C:\\dev\\fw"})
            check("② local 源：root（installLocation）**也**剥了前缀", loc, "C:\\dev\\fw")
            check("② 出处点名 Codex", "Codex" in via, True)

            put_toml(f'[marketplaces.{MKT}]\nsource = "owner/repo"\nsource_type = "github"\n')
            src, loc, via = wd.locate_market(MKT, exe="codex", project=proj)
            check("② 非 local 且项目没声明：不代填（返回 None）", src, None)
            check("② 非 local：把原值念出来让用户自己定", "owner/repo" in via, True)

            # 非 local 但项目自己声明过 ⇒ 用项目自己那份（不是我替它猜的）
            put_settings({"extraKnownMarketplaces": {MKT: {"source": DIR_SRC}}})
            src, loc, via = wd.locate_market(MKT, exe="codex", project=proj)
            check("② 非 local ＋ 项目有声明：用项目自己那份", (src, loc), (DIR_SRC, None))
        finally:
            wd.run_codex = saved_run
            if saved_codex_home is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = saved_codex_home
            rm(cfg_home)
    finally:
        for k, v in zip(("USERPROFILE", "HOME"), old_home):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        rm(home)
        rm(proj)


# ---- 5. 安装根守卫：版本核对 / 清单形态 / 「写了 / 没写」如实 ----

# fixture 自己写的版本字面值。期望值只拿它们比，**不拿被测函数现算的结果当期望**——取值函数坏掉时两边一起坏、断言恒绿
DOOR_V, OLD_V, NEW_V = "9.8.7", "9.8.6", "9.8.8"


def _manifest_keys():
    manifest = json.loads((PLUGIN / "hooks" / "hooks.codex.manifest.json").read_text(encoding="utf-8"))
    return [e["key"].split(":", 1)[1] for e in manifest["entries"]]


def _plugin_root_fixture(root, version=None, codex=True, claude=True, manifest="copy", agents=True):
    """在 `root` 造一个插件根。`codex` / `claude` 为 True 时写一份带 `version` 的 plugin.json，给 bytes 就原样写
    （造坏清单），False 不写。`manifest`：`"copy"` 复制本仓的 hook 冻结清单、None 不写、`"dir"` 在那个路径建目录
    （读不出）、bytes 原样写。`agents/` 与 `hooks/hooks.codex.json` 复制本仓真件（生成角色、种信任记录要读）。"""
    root.mkdir(parents=True, exist_ok=True)
    if agents:
        shutil.copytree(PLUGIN / "agents", root / "agents")
    hooks = root / "hooks"
    hooks.mkdir(exist_ok=True)
    shutil.copyfile(PLUGIN / "hooks" / "hooks.codex.json", hooks / "hooks.codex.json")
    mf = hooks / "hooks.codex.manifest.json"
    if manifest == "copy":
        shutil.copyfile(PLUGIN / "hooks" / "hooks.codex.manifest.json", mf)
    elif manifest == "dir":
        mf.mkdir()
    elif isinstance(manifest, bytes):
        mf.write_bytes(manifest)
    for rel, how in ((".codex-plugin", codex), (".claude-plugin", claude)):
        if how is False:
            continue
        (root / rel).mkdir(exist_ok=True)
        body = how if isinstance(how, bytes) else json.dumps({"name": "core", "version": version}).encode("utf-8")
        (root / rel / "plugin.json").write_bytes(body)
    return root


class _DoorAt:
    """把 `wd.PLUGIN_ROOT`（本命令所在的插件根）临时指到 fixture：两边的版本都是测试自己写的字面值。"""

    def __init__(self, root):
        self.root = root

    def __enter__(self):
        self.saved = wd.PLUGIN_ROOT
        wd.PLUGIN_ROOT = self.root
        return self.root

    def __exit__(self, *a):
        wd.PLUGIN_ROOT = self.saved
        return False


class _EnvVar:
    """临时设一个环境变量（CODEX_HOME 指到临时目录：种信任记录只会写进它，用户的 ~/.codex 零接触）。"""

    def __init__(self, name, value):
        self.name, self.value = name, value

    def __enter__(self):
        self.saved = os.environ.get(self.name)
        os.environ[self.name] = self.value

    def __exit__(self, *a):
        if self.saved is None:
            os.environ.pop(self.name, None)
        else:
            os.environ[self.name] = self.saved
        return False


class RootServer(LayeredServer):
    """在 LayeredServer 上改三处，给安装根守卫用：hook 的 `sourcePath` 落在 `root`（被测的安装根，不再写死本仓
    插件根）；段首启用表**现读项目文件**（还没有段的项目在段落盘之前 `hooks/list` 为 0 条、落盘之后才可见——
    `--upgrade` 先写后核那一格靠它）；带 cwd 的 `config/read` 按项目 `.codex/roles/` 里现有的文件回显角色注册。
    `fail_read_after_write=True` 时 batchWrite 之后的 `config/read` 抛 AppServerError（回读途中失联）。
    修复轮加的三个开关：`reject_write`（batchWrite 答 status=error、不落任何一层）/ `fail_hooks_list_at=N`（第 N 次
    `hooks/list` 抛 AppServerError，模拟途中失联）/ `no_source_path`（条目不带 `sourcePath`）。"""

    def __init__(self, keys, key, project, root, fail_read_after_write=False, reject_write=False,
                 fail_hooks_list_at=None, no_source_path=False, write_timeout_after_apply=False, **kw):
        self.project, self.root, self.fail_read_after_write = project, root, fail_read_after_write
        self.reject_write, self.fail_hooks_list_at, self.no_source_path = reject_write, fail_hooks_list_at, no_source_path
        self.write_timeout_after_apply = write_timeout_after_apply
        self.hooks_list_calls = 0
        super().__init__(keys, key, **kw)

    def config_batch_write(self, edits, **kw):
        if self.reject_write:
            self.edits = list(edits)
            return {"status": "error"}
        res = super().config_batch_write(edits, **kw)
        if self.write_timeout_after_apply:      # 写入已落进各层，答复却没回来（超时）
            raise cas.AppServerError("config/batchWrite 超时（stub：已写入、答复未回）")
        return res

    @property
    def table(self):
        return cr.block_enable_value(self.project, PID)

    @table.setter
    def table(self, _v):
        pass            # LayeredServer.__init__ 会给它赋值；本类的启用表只认项目文件

    def hooks_list(self, cwds):
        self.hooks_list_calls += 1
        if self.fail_hooks_list_at is not None and self.hooks_list_calls >= self.fail_hooks_list_at:
            raise cas.AppServerError("stub：hooks/list 途中 app-server 退出")
        loaded = self._visible()
        hooks = [_hook(k, "trusted" if self.full(k) in self.hook_trust else "untrusted", root=str(self.root))
                 for k in self.keys] if loaded else []
        if self.no_source_path:
            hooks = [{k: v for k, v in h.items() if k != "sourcePath"} for h in hooks]
        return {"data": [{"cwd": cwds[0] if cwds else "", "hooks": hooks, "warnings": [], "errors": []}]}

    def config_read(self, _layers=False, cwd=None):
        if self.fail_read_after_write and self.edits is not None:
            raise cas.AppServerError("stub：回读途中 app-server 退出")
        out = super().config_read(_layers, cwd)
        if cwd:
            roles_dir = Path(self.project) / cr.ROLES_DIR_REL
            out["config"]["agents"] = {p.stem: {"config_file": str(p)} for p in roles_dir.glob("*.toml")
                                       if not p.name.endswith(".local.toml")}
        return out


def _ctx(server):
    class Ctx:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return server

        def __exit__(self, *a):
            return False
    return Ctx


def _drive_root(proj, server, installed_path, upgrade=False, source_type=None, cap=None,
                add_sets_user=True, add_path=True, version="codex-cli 0.153.4", wrote=None, on_upgrade=None,
                avail=None, echoed=MKT, already=False, upgrade_resp=(0, "{}", "")):
    """打桩跑 do_codex：codex CLI 按 argv 应答（`plugin add` 回显 `installed_path`，并把用户层写回 true——实测
    如此），app-server 换成 `server`。返回 (退出码, 文案, codex 调用列表)；没抛时退出码 0。
    `cap` 给一个列表时，把 `say` 打出的每一行按顺序收进去（验「某句话说没说、在哪一行之后说」）。
    `add_sets_user=False` / `add_path=False` 造「回显形态与现有样本不同」的那几格；`version` 换 `--version` 的回显；
    `wrote` 原样交给 do_codex（`--backfill` 先写过 CC 那半的形态）。
    `on_upgrade` 给了就在 `marketplace upgrade` 那一刻调它，模拟真机的副作用（见 `_CodexLayout.upgrade`：建新版本目录、
    删旧版本目录、原地刷新 git 快照）；不给时那条命令什么都不改（旧行为）；`upgrade_resp` 换它的应答。
    `avail`：装前核对那条 `plugin list --json --available -m` 的应答——字符串按 rc=0 的 stdout 给、三元组原样给、可调用的
    拿 argv 调它（造超时之类抛异常的形态）；不给时
    rc=1（装前核对读不出、放行：此前各格的形态不变）。`echoed` / `already` 换 `marketplace add` 回显的市场名与
    `alreadyAdded`。"""
    calls = []

    def fake_run(exe, args, cwd=None, timeout=180, env=None):
        calls.append(list(args))
        if args[:1] == ["--version"]:
            return 0, version, ""
        if args[:3] == ["plugin", "marketplace", "add"]:
            return 0, json.dumps({"marketplaceName": echoed, "alreadyAdded": already}), ""
        if args[:4] == ["plugin", "list", "--json", "--available"]:
            if avail is None:
                return 1, "", "no"
            if callable(avail):
                return avail(list(args))
            return avail if isinstance(avail, tuple) else (0, avail, "")
        if args[:3] == ["plugin", "list", "--json"]:
            return (0, _plugin_list_payload(source_type), "") if source_type else (1, "", "no")
        if args[:2] == ["plugin", "add"]:
            if add_sets_user:
                server.user = True
            body = {"version": "x"}
            if add_path:
                body["installedPath"] = str(installed_path)
            return 0, json.dumps(body), ""
        if args[:3] == ["plugin", "marketplace", "upgrade"]:
            if on_upgrade is not None:
                on_upgrade()
            return upgrade_resp
        return 1, "", "unexpected"

    saved = (wd.run_codex, wd.codex_exe, wd.cas.AppServer, wd.say)
    wd.run_codex, wd.codex_exe, wd.cas.AppServer = fake_run, (lambda: "codex"), _ctx(server)
    if cap is not None:
        wd.say = lambda msg="": cap.append(str(msg))
    try:
        try:
            wd.do_codex(_Args(), proj, upgrade=upgrade, wrote=wrote)
            return 0, "", calls
        except wd.DoorError as e:
            return e.code, str(e), calls
    finally:
        wd.run_codex, wd.codex_exe, wd.cas.AppServer, wd.say = saved


def test_plugin_version():
    print("[guard] 插件版本怎么读：先 .codex-plugin，这份不在才退 .claude-plugin，坏了不往后退")
    d = tmpdir()
    try:
        cases = [
            ("只有 .codex-plugin", dict(version=DOOR_V, claude=False), DOOR_V),
            ("只有 .claude-plugin（1.1.0 之前的插件就是这个形态）", dict(version=OLD_V, codex=False), OLD_V),
            ("两份都在、版本不同 → 取 .codex-plugin",
             dict(version=DOOR_V, claude=json.dumps({"version": OLD_V}).encode("utf-8")), DOOR_V),
            ("两份都不在", dict(codex=False, claude=False), None),
            (".codex-plugin 解析不了 → 读不出，不退到 .claude-plugin", dict(version=OLD_V, codex=b"{not json"), None),
            # `.claude-plugin` 在且有版本：只有这样「没有 version 串就往后退」被改出来时这一格才会红
            (".codex-plugin 没有 version 串 → 读不出，不退到 .claude-plugin",
             dict(version=None, claude=json.dumps({"version": OLD_V}).encode("utf-8")), None),
            (".codex-plugin 带 BOM", dict(codex=b"\xef\xbb\xbf" + json.dumps({"version": DOOR_V}).encode("utf-8"),
                                        claude=False), DOOR_V),
        ]
        for i, (label, kw, want) in enumerate(cases):
            root = _plugin_root_fixture(d / f"r{i}", agents=False, manifest=None, **kw)
            v, why = wd.plugin_version(root)
            check(f"plugin_version：{label}", v, want)
            check(f"plugin_version：{label}——读不出才给原因", why is not None, want is None)
        check("installed_version：读不出时显示 ?", wd.installed_version(d / "r3"), "?")
        # 排前面那份「在」按 exists 判：它是个目录也算在、却读不出，不往后退到 .claude-plugin
        dirc = _plugin_root_fixture(d / "r-dir", agents=False, manifest=None, version=OLD_V, codex=False)
        (dirc / ".codex-plugin" / "plugin.json").mkdir(parents=True)
        v, why = wd.plugin_version(dirc)
        check("plugin_version：.codex-plugin/plugin.json 是个目录 → 读不出，不退到 .claude-plugin", (v, why is not None),
              (None, True))
    finally:
        rm(d)


def test_version_mismatch():
    print("[guard] 版本核对：同版本放行；不同 / 任一侧读不出都给人话，含两个版本、两个根、按方向给的出路")
    d = tmpdir()
    try:
        door = _plugin_root_fixture(d / "mkt" / "plugins" / "core", version=DOOR_V)
        same = _plugin_root_fixture(d / "same", version=DOOR_V)
        old = _plugin_root_fixture(d / "old", version=OLD_V, codex=False)     # 1.1.0 之前的形态：只有 .claude-plugin
        new = _plugin_root_fixture(d / "new", version=NEW_V)
        blind = _plugin_root_fixture(d / "blind", codex=False, claude=False)
        with _DoorAt(door):
            check("同版本 → None（放行）", wd.version_mismatch(same), None)
            m = wd.version_mismatch(old)
            check("安装根更旧 → 给人话（不是 None）", isinstance(m, str), True)
            m = m or ""
            check("安装根更旧：含两个版本（fixture 字面值）", (f"v{OLD_V}" in m, f"v{DOOR_V}" in m), (True, True))
            check("安装根更旧：含两个根", (str(old) in m, str(door) in m), (True, True))
            check("安装根更旧：判读句说「不是同一版本」、不说读不出", ("不是同一版本" in m, "读不出版本" in m), (True, False))
            check("安装根更旧：两类读者各有出路（--marketplace / --upgrade），不给「升本命令这一侧」那条",
                  ("--marketplace" in m, "workframe-door --upgrade" in m, "claude plugin update" in m), (True, True, False))
            check("安装根更旧：本命令不在市场目录里时出路给占位、不编路径", ("例如本地框架仓" in m, "市场目录" in m),
                  (True, False))
            m = wd.version_mismatch(new) or ""
            check("安装根更新：出路是升本命令这一侧，不让人去 --upgrade Codex 侧",
                  ("claude plugin update" in m, f"v{NEW_V}" in m, "workframe-door --upgrade" in m), (True, True, False))
            # 只装了 Codex 的读者做不了 `claude plugin update`：那份新版自带门脚本时，出路要点名用它重跑；
            # 不带时不编一条不存在的路径（两格并排：只有前一格时「恒点名」也绿，只有后一格时「恒不点名」也绿）
            check("安装根更新、那份没带门脚本：不编「用它自带的门」那条", "自带的门" in m, False)
            # 既有消费方（步 3'、--check）的措辞：装后那一态说「Codex 侧那份」，不说装前核对的「登记的市场里那份」
            check("安装根更新（非装前）：出路说「Codex 侧那份更新」、不说「Codex 登记的市场里那份」",
                  ("出路：Codex 侧那份更新" in m, "Codex 登记的市场里" in m), (True, False))
            newd = _plugin_root_fixture(d / "new-door", version=NEW_V)
            (newd / "scripts").mkdir()
            (newd / "scripts" / "workframe_door.py").write_bytes(b"# stub\n")
            m = wd.version_mismatch(newd) or ""
            check("安装根更新、那份带门脚本：点名用新版本目录下的门重跑（Codex-only 读者照做得了）",
                  f'python "{newd / "scripts" / "workframe_door.py"}"' in m, True)
            # 预发布串：`9.8.7-dev` 按语义化版本比 `9.8.7` 旧 ⇒ 给 ①②，不给「升本命令这一侧」。
            # 反方向（门是预发布、安装根是正式版）现在判错，已登记为后续条目，**这里不钉**
            pre = _plugin_root_fixture(d / "pre", version=DOOR_V + "-dev")
            m = wd.version_mismatch(pre) or ""
            check("安装根是本版的预发布串：出路给 ①②、不给「升本命令这一侧」",
                  ("workframe-door --upgrade" in m, "claude plugin update" in m), (True, False))
            m = wd.version_mismatch(blind)
            check("安装根读不出版本 → 不放行（不是 None）", isinstance(m, str), True)
            check("安装根读不出版本：说读不出、给原因、不按一致放行",
                  all(w in (m or "") for w in ("读不出版本", "都不在", "不按一致放行")), True)
        with _DoorAt(blind):
            check("本命令这一侧读不出版本 → 同样不放行", isinstance(wd.version_mismatch(same), str), True)
            check("两侧都读不出 → 仍不放行（两个「不知道」不算一致）", isinstance(wd.version_mismatch(blind), str), True)
        (d / "mkt" / ".claude-plugin").mkdir()
        (d / "mkt" / ".claude-plugin" / "marketplace.json").write_bytes(b"{}")
        with _DoorAt(door):
            check("本命令来自市场目录时，出路 ① 点名那个目录", f"市场目录 {d / 'mkt'}" in (wd.version_mismatch(old) or ""),
                  True)
    finally:
        rm(d)


def test_manifest_shapes():
    print("[guard] hook 冻结清单形态坏：每种一句人话、成因互不相同，不抛裸异常")
    d = tmpdir()
    try:
        real = json.loads((PLUGIN / "hooks" / "hooks.codex.manifest.json").read_text(encoding="utf-8"))
        n, data = wd.manifest_count(PLUGIN)
        check("正常态：条数 == 清单 entries 条数（现读、不写死）、第二元是 dict",
              (n, isinstance(data, dict)), (len(real["entries"]), True))
        shapes = [
            ("文件不存在", None, "文件不存在"),
            ("路径是个目录（读不出）", "dir", "读不出"),
            ("不是合法 JSON", b"{not json", "不是合法 JSON"),
            ("entries 是对象", b'{"entries": {"a": 1, "b": 2}}', "不是列表"),
            ("entries 是字符串", b'{"entries": "abc"}', "不是列表"),
            ("没有 entries 键", b'{"x": 1}', "不是列表"),
            ("顶层是数组", b"[]", "不是列表"),
            ("entries 是空列表", b'{"entries": []}', "空列表"),
            ("条目不是带 key 的对象", b'{"entries": [{"key": "a"}, 1]}', "第 2 条"),
        ]
        words = sorted({w for _l, _m, w in shapes})
        causes = {}
        for i, (label, mf, word) in enumerate(shapes):
            root = _plugin_root_fixture(d / f"m{i}", version=DOOR_V, manifest=mf, agents=False)
            try:
                wd.manifest_count(root)
                err = None
            except Exception as e:           # 裸异常也收下：本组要钉死的正是「这一格不再是 traceback」
                err = e
            msg = str(err) if err is not None else ""
            # 钉子类名而不是「是 DoorError」：do_codex 的收尾靠这个类认出安装根这一族、补说此后加载哪一版
            check(f"清单{label}：抛 InstalledRootError（DoorError 的子类，不是裸异常）且 exit 2",
                  (type(err).__name__, _code(err)), ("InstalledRootError", wd.EXIT_ENV))
            check(f"清单{label}：文案含自己的成因「{word}」、不含别的成因",
                  (word in msg, [w for w in words if w != word and w in msg]), (True, []))
            check(f"清单{label}：文案点名安装根与清单路径", (str(root) in msg, "hooks.codex.manifest.json" in msg), (True, True))
            causes.setdefault(word, msg.replace(str(root), "<根>"))
        check("各成因的文案两两不同（去掉根路径后）", len(set(causes.values())), len(words))
        bare = d / "m0"
        with _DoorAt(bare):
            try:
                wd.manifest_count(bare)
                msg = ""
            except wd.DoorError as e:
                msg = str(e)
        check("清单坏在本命令自己的插件根：说「本命令自己所在的插件根」、出路不是去 --upgrade Codex 侧",
              ("本命令自己所在的插件根" in msg, "--upgrade" in msg), (True, False))
        # 同一处目录、换一种写法（带 `..` 段）：仍要认成本命令自己的根——同根判断比的是归一后的路径，不是字符串
        with _DoorAt(bare):
            try:
                wd.manifest_count(bare / "hooks" / "..")
                msg = ""
            except wd.DoorError as e:
                msg = str(e)
        check("同根的另一种写法（带 ..）：仍认成本命令自己所在的插件根", "本命令自己所在的插件根" in msg, True)
        # BOM：与版本读法同一口径（utf-8-sig），带 BOM 的清单照常读得出
        bom = _plugin_root_fixture(d / "bom", version=DOOR_V, agents=False,
                                   manifest=b"\xef\xbb\xbf" + (PLUGIN / "hooks" / "hooks.codex.manifest.json").read_bytes())
        try:
            got = wd.manifest_count(bom)[0]
        except Exception as e:
            got = f"{type(e).__name__}: {str(e)[:80]}"
        check("清单带 BOM：照常读出（与 plugin_version 同一口径），条数 == entries 条数", got, len(real["entries"]))
        try:
            wd.manifest_count(bare)
            msg = ""
        except wd.DoorError as e:
            msg = str(e)
        check("清单坏在 Codex 侧的安装根：说「Codex 侧的安装根」、出路是 --upgrade",
              ("Codex 侧的安装根" in msg, "--upgrade" in msg), (True, True))
    finally:
        rm(d)


def test_guard_hooks_list():
    print("[guard] step_hooks_list：自己反推出的安装根同样要核；清单坏给人话、不是 FileNotFoundError")
    keys = _manifest_keys()
    d = tmpdir()
    try:
        door = _plugin_root_fixture(d / "door", version=DOOR_V)
        old = _plugin_root_fixture(d / "old", version=OLD_V)
        same = _plugin_root_fixture(d / "same", version=DOOR_V)
        bare = _plugin_root_fixture(d / "bare", version=DOOR_V, manifest=None)
        proj = d / "proj"
        proj.mkdir()
        with _DoorAt(door):
            try:     # 包住：守卫误拦正常态时要报成一条 ✗，而不是让整个套件崩在这里
                mine, root = wd.step_hooks_list(FakeServer([_hook(k, root=str(same)) for k in keys]), proj, PID)
                got = (len(mine), Path(root).resolve())
            except Exception as e:
                got = f"{type(e).__name__}: {str(e)[:120]}"
            check("自推出的安装根同版本：放行，返回的就是它", got, (len(keys), same.resolve()))
            for label, inst, given, word in (("自推出的安装根更旧", old, None, "不是同一版本"),
                                             ("调用方给的安装根缺清单", bare, bare, "文件不存在")):
                try:
                    wd.step_hooks_list(FakeServer([_hook(k, root=str(inst)) for k in keys]), proj, PID, given)
                    got = ("no error", None, False)
                except wd.DoorError as e:
                    got = ("DoorError", e.code, word in str(e))
                except Exception as e:
                    got = (type(e).__name__, None, False)
                check(f"step_hooks_list {label}：DoorError、exit 2、文案说「{word}」", got, ("DoorError", wd.EXIT_ENV, True))
    finally:
        rm(d)


def test_guard_do_codex():
    print("[guard] do_codex：安装根不是本命令这一份 → 在写任何项目文件之前停下（exit 2、项目树逐字节不变）")
    keys = _manifest_keys()
    d = tmpdir()
    try:
        home = d / "codexhome"
        home.mkdir()
        door = _plugin_root_fixture(d / "mkt" / "plugins" / "core", version=DOOR_V)
        old_bare = _plugin_root_fixture(d / "old-bare", version=OLD_V, codex=False, manifest=None)   # 1.0.0 缓存的形态
        old_full = _plugin_root_fixture(d / "old-full", version=OLD_V)                              # 完整、只是旧
        same = _plugin_root_fixture(d / "same", version=DOOR_V)
        with _DoorAt(door), _EnvVar("CODEX_HOME", str(home)):
            # 产生点：--codex 的 `plugin add` 回显 / --upgrade 本地源重装的回显
            for tag, upgrade, st in (("--codex", False, None), ("--upgrade（本地源）", True, "local")):
                for label, inst in (("缺清单的旧版缓存", old_bare), ("完整的旧版缓存", old_full)):
                    dd, proj = _mk()
                    try:
                        srv = RootServer(keys, wd.project_key(proj), proj, inst, user=False, trusted=False)
                        before = _snap(proj)
                        code, msg, _calls = _drive_root(proj, srv, inst, upgrade=upgrade, source_type=st)
                        check(f"{tag} {label}：exit 2", code, wd.EXIT_ENV)
                        check(f"{tag} {label}：项目树逐字节不变", _snap(proj) == before, True)
                        check(f"{tag} {label}：没走到写信任（batchWrite 零调用）", srv.edits, None)
                        # 先被拦下的是「Codex 实际加载的那一份」（由 sourcePath 反推，经 resolve）：它在文案里是长路径
                        # 写法，夹具给的可能是 8.3 短名——同一处的两种写法任认其一
                        check(f"{tag} {label}：文案含两个版本（fixture 字面值）与两个根",
                              all(w in msg for w in (f"v{OLD_V}", f"v{DOOR_V}", str(door)))
                              and (str(inst) in msg or str(inst.resolve()) in msg), True)
                        check(f"{tag} {label}：文案说未写任何项目文件、带用户层残留句（plugin add 已把它写回 true）",
                              ("未写任何项目文件" in msg, wd.USER_LAYER_STILL_TRUE in msg), (True, True))
                        check(f"{tag} {label}：没写启用表，不说「此后本项目会加载 Codex 侧那一份」",
                              "此后在本项目起的" in msg, False)
                    finally:
                        rm(dd)
            # 同版本、只是缺清单：同样要在写之前停（守卫的「清单」那一半也在写之前，不只「版本」那一半）
            same_bare = _plugin_root_fixture(d / "same-bare", version=DOOR_V, manifest=None)
            dd, proj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(proj), proj, same_bare, user=False, trusted=False)
                before = _snap(proj)
                code, msg, _calls = _drive_root(proj, srv, same_bare)
                check("--codex 同版本缺清单：exit 2、文案说「文件不存在」", (code, "文件不存在" in msg), (wd.EXIT_ENV, True))
                check("--codex 同版本缺清单：项目树逐字节不变（清单那一半也在写之前核）", _snap(proj) == before, True)
            finally:
                rm(dd)
            # 产生点：--upgrade git 源从 hooks/list 反推（段已在、启用表 true ⇒ 一开始就可见）
            dd, proj = _mk(gen=True)
            try:
                srv = RootServer(keys, wd.project_key(proj), proj, old_full, user=False, trusted=True)
                before = _snap(proj)
                code, msg, _calls = _drive_root(proj, srv, None, upgrade=True)
                check("--upgrade（git 源）反推出的安装根更旧：exit 2", code, wd.EXIT_ENV)
                check("--upgrade（git 源）反推出的安装根更旧：项目树逐字节不变", _snap(proj) == before, True)
                check("--upgrade（git 源）：文案说未写任何项目文件、不带用户层残留句（git 源那一路不动用户层）",
                      ("未写任何项目文件" in msg, wd.USER_LAYER_STILL_TRUE in msg), (True, False))
                check("--upgrade（git 源）写之前就停：不说「此后本项目会加载 Codex 侧那一份」", "此后在本项目起的" in msg, False)
            finally:
                rm(dd)
            # 对照：同版本 ⇒ 守卫放行、整条走完（守卫不误伤正常态）
            dd, proj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(proj), proj, same, user=False, trusted=False)
                code, msg, _calls = _drive_root(proj, srv, same)
                check("同版本对照：装机走完（exit 0）", (code, msg[:300]), (0, ""))
            finally:
                rm(dd)
    finally:
        rm(d)


def test_guard_check_static():
    print("[guard] --check：版本不一致 / 清单坏进 problems，其余检查照跑、不整段崩")
    keys = _manifest_keys()
    lone = keys[0]           # 这一条故意留成未受信：它报没报出来，证明清单那一段之后的逐条检查照跑了
    d = tmpdir()
    try:
        home = d / "codexhome"
        home.mkdir()
        door = _plugin_root_fixture(d / "door", version=DOOR_V)
        cells = [
            ("安装根更旧", _plugin_root_fixture(d / "old", version=OLD_V), ("不是同一版本",)),
            ("安装根缺清单（同版本）", _plugin_root_fixture(d / "bare", version=DOOR_V, manifest=None), ("文件不存在",)),
            ("1.0.0 形态（旧、且缺清单）", _plugin_root_fixture(d / "v1", version=OLD_V, codex=False, manifest=None),
             ("不是同一版本", "文件不存在")),
            ("对照：同版本、清单完好", _plugin_root_fixture(d / "same", version=DOOR_V), ()),
        ]
        with _DoorAt(door), _EnvVar("CODEX_HOME", str(home)):
            for label, inst, want_words in cells:
                dd, proj = _mk(gen=True)
                saved = (wd.cas.AppServer, wd.run_codex)
                try:
                    srv = RootServer(keys, wd.project_key(proj), proj, inst, user=False, trusted=True,
                                     trusted_hooks={f"{PID}:hooks/hooks.codex.json:{k}" for k in keys[1:]})
                    wd.cas.AppServer = _ctx(srv)
                    wd.run_codex = lambda *a, **k: (0, "multi_agent stable true", "")
                    try:
                        problems, info, _m = wd.check_static("codex", proj, MKT)
                        crashed = None
                    except Exception as e:
                        problems, info, crashed = [], [], f"{type(e).__name__}: {e}"
                    check(f"check_static {label}：不整段崩", crashed, None)
                    for w in want_words:
                        check(f"check_static {label}：「{w}」进 problems", any(w in p for p in problems), True)
                    if not want_words:
                        check(f"check_static {label}：版本与清单都不报", any(
                            "不是同一版本" in p or "hook 冻结清单" in p for p in problems), False)
                    check(f"check_static {label}：其余照跑——那条未受信 hook 照样报出",
                          any(lone in p and "untrusted" in p for p in problems), True)
                    check(f"check_static {label}：其余照跑——项目 trust 与 features 两项都在 info",
                          (any("项目 trust" in i for i in info), any(i.startswith("features:") for i in info)), (True, True))
                    check(f"check_static {label}：不拿坏清单比条数", any("≠ manifest" in p for p in problems), False)
                finally:
                    wd.cas.AppServer, wd.run_codex = saved
                    rm(dd)
    finally:
        rm(d)


def test_wrote_truthful():
    print("[wrote] 失败文案里「写了 / 没写」与实际一致：--upgrade 先写后核 / 回读途中失联 / 带着 --backfill 的写入记录")
    keys = _manifest_keys()
    d = tmpdir()
    try:
        home = d / "codexhome"
        home.mkdir()
        door = _plugin_root_fixture(d / "mkt" / "plugins" / "core", version=DOOR_V)
        old = _plugin_root_fixture(d / "old", version=OLD_V)
        same = _plugin_root_fixture(d / "same", version=DOOR_V)
        with _DoorAt(door), _EnvVar("CODEX_HOME", str(home)):
            # ① --upgrade（git 源）＋ 项目里还没有段：段落盘之前 hooks/list 0 条、安装根解析不出 ⇒ 角色先按本命令的
            #    插件根写；落段后反推出安装根、核对不过 ⇒ 停下。文案列出的文件必须 == 实际写过的文件
            dd, proj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(proj), proj, old, user=False, trusted=True)
                before = _snap(proj)
                out = []
                code, msg, _calls = _drive_root(proj, srv, None, upgrade=True, cap=out)
                after = _snap(proj)
                check("先写后核：核对不过时终端上不出现「重新生成」（不抢在守卫前面说）",
                      [ln for ln in out if "重新生成" in ln], [])
                changed = sorted(r for r in after if before.get(r) != after[r])
                m = re.search(r"本次已写入的项目文件：(.*?)（门不代删", msg)
                listed = sorted(m.group(1).split("、")) if m else []
                check("先写后核：exit 2（落段后反推出的安装根核对不过）", code, wd.EXIT_ENV)
                check("先写后核：确实写过项目文件（本格的前提）", bool(changed), True)
                check("先写后核：文案不说「未写任何项目文件」", "未写任何项目文件" in msg, False)
                check("先写后核：文案列出的文件 == 实际写过的文件", listed, changed)
                check("先写后核：说明此后本项目会加载 Codex 侧那一份（带它的版本）、以及怎么重跑",
                      # 版本号要钉在提示句自己那一截里——前面版本不一致那句本来就含 v{OLD_V}，只查「含」是空真
                      ("此后在本项目起的 Codex 会话会加载 Codex 侧装着的那一份" in msg,
                       f"，v{OLD_V}），不是本命令这一份" in msg,
                       "重跑 `workframe-door --upgrade`" in msg), (True, True, True))
                heads = [p.read_text(encoding="utf-8").splitlines()[0] for p in (proj / cr.ROLES_DIR_REL).glob("*.toml")]
                check("先写后核：核对不过就没按旧安装根重渲染（角色头标仍是本命令的版本）",
                      (bool(heads), all(f"v{DOOR_V}" in h for h in heads), any(f"v{OLD_V}" in h for h in heads)),
                      (True, True, False))
            finally:
                rm(dd)
            # ①'' 同一条路，Codex 实际加载的那一份同版本、但缺 hook 冻结清单 ⇒ 第二轮 step_roles 入口就停：不多生成一轮、
            #      不说「核对通过」。这一格是入口守卫「清单」那一半在 P4 路径上唯一的一次核对——只核版本时它会多生成一轮、
            #      先打一句假的「核对通过」，最后才在步 5 以「文件不存在」停下
            same_bare = _plugin_root_fixture(d / "same-bare", version=DOOR_V, manifest=None)
            dd, proj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(proj), proj, same_bare, user=False, trusted=True)
                out = []
                code, msg, _calls = _drive_root(proj, srv, None, upgrade=True, cap=out)
                check("先写后核、实际加载的那一份同版本但缺清单：exit 2、说「文件不存在」",
                      (code, "文件不存在" in msg), (wd.EXIT_ENV, True))
                check("先写后核、实际加载的那一份同版本但缺清单：终端上没有「核对通过」、[4/11] 恰好一次（第二轮生成之前就停）",
                      ([ln for ln in out if "核对通过" in ln], sum(1 for ln in out if ln.startswith("[4/11]"))), ([], 1))
            finally:
                rm(dd)
            # ①' 对照：同一条路、安装根与本命令同版本（只是另一处目录）⇒ 核对通过、真的重新生成，而且那句话说在
            #    第二次 [4/11] 之后。与 ① 并排：只有 ① 时「永远不说」也绿，只有这一格时「抢跑着说」也绿
            dd, proj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(proj), proj, same, user=False, trusted=True)
                out = []
                code, msg, _calls = _drive_root(proj, srv, None, upgrade=True, cap=out)
                roles4 = [i for i, ln in enumerate(out) if ln.startswith("[4/11]")]
                said = [i for i, ln in enumerate(out) if "已按它重新生成" in ln]
                check("先写后核对照（同版本、另一处目录）：装机走完（exit 0）", (code, msg[:300]), (0, ""))
                check("先写后核对照：角色生成了两轮、「已按它重新生成」恰好说一次、说在第二轮 [4/11] 之后",
                      (len(roles4), len(said), bool(said and roles4 and said[0] > roles4[-1])), (2, 1, True))
            finally:
                rm(dd)
            # ② app-server 在 [7/11] 写完之后、回读途中失联：不许再说「未写」
            dd, proj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(proj), proj, same, user=False, trusted=False,
                                 fail_read_after_write=True)
                code, msg, _calls = _drive_root(proj, srv, same)
                check("回读途中失联：exit 2", code, wd.EXIT_ENV)
                check("回读途中失联：说 batchWrite 已写入、不说未写入 hook 信任",
                      ("已写入你的 Codex 用户配置" in msg, "未写入任何 hook 信任" in msg), (True, False))
                check("回读途中失联：点名已写的项目文件、不说未写",
                      ("本次已写入的项目文件" in msg, "未写任何项目文件" in msg), (True, False))
            finally:
                rm(dd)
        # ②' 对照：写之前就失联（app-server 起不来）⇒ 两件都说没写
        dd, proj = _mk()
        try:
            _code_, msg, _c, _k = _drive(proj)
            check("app-server 起不来：说未写入 hook 信任、未写任何项目文件",
                  ("未写入任何 hook 信任" in msg, "未写任何项目文件" in msg, "本次已写入" in msg), (True, True, False))
        finally:
            rm(dd)
        # ③ do_codex 带着 --backfill 已写过的记录、预检不过：点名那个文件；不带记录时仍说「未写任何文件」（两格并排）
        for label, wrote, want in [("带着 --backfill 的写入记录", [".claude/settings.json"], (True, False)),
                                   ("不带写入记录", None, (False, True))]:
            dd, proj = _mk('[plugins."core@workframe"]\nenabled = true\n')
            try:
                try:
                    wd.do_codex(_Args(), proj, wrote=wrote)
                    msg = ""
                except wd.DoorError as e:
                    msg = str(e)
                check(f"do_codex {label}、预检不过：点名 .claude/settings.json / 说未写任何文件",
                      (".claude/settings.json" in msg, "未写任何文件" in msg), want)
            finally:
                rm(dd)
    finally:
        rm(d)


def test_hook_source_guard():
    print("[guard] Codex 实际加载的那一份：由 sourcePath 反推（反推不出按读不出）、与回显的那一份各自独立核，"
          "不要求两个路径写法相等（看得见 hook 时在写之前）")
    keys = _manifest_keys()
    d = tmpdir()
    try:
        home = d / "codexhome"
        home.mkdir()
        door = _plugin_root_fixture(d / "door", version=DOOR_V)
        same = _plugin_root_fixture(d / "same", version=DOOR_V)
        twin = _plugin_root_fixture(d / "twin", version=DOOR_V)       # 另一处、同版本、清单完好：路径归一也对不上
        old = _plugin_root_fixture(d / "old", version=OLD_V)
        proj = d / "proj"
        proj.mkdir()

        def hooks_at(root, drop_sp=False):
            hs = [_hook(k, root=str(root)) for k in keys]
            return [{k: v for k, v in h.items() if k != "sourcePath"} for h in hs] if drop_sp else hs

        with _DoorAt(door), _EnvVar("CODEX_HOME", str(home)):
            r, p = wd.loaded_root(hooks_at(same, drop_sp=True))
            check("实际加载的那一份：条目都没有 sourcePath → 读不出（不拿本命令自己的根顶上）",
                  (r, "一条都没有 sourcePath" in (p or "")), (None, True))
            r, p = wd.loaded_root(hooks_at(same))
            check("实际加载的那一份：各条来源一致 → 得出那一处、不报问题",
                  (Path(r).resolve() if r else None, p), (same.resolve(), None))
            mixed = hooks_at(same)
            mixed[0] = hooks_at(same, drop_sp=True)[0]
            r, p = wd.loaded_root(mixed)
            check("实际加载的那一份：有一条没 sourcePath → 读不出、点名「1 条没有 sourcePath」",
                  (r, "1 条没有 sourcePath" in (p or "")), (None, True))
            two = hooks_at(same)
            two[0] = hooks_at(old)[0]
            r, p = wd.loaded_root(two)
            check("实际加载的那一份：各条反推出两处 → 读不出、点名「2 个不同的根」",
                  (r, "反推出 2 个不同的根" in (p or "")), (None, True))
            if os.name == "nt":     # 扩展长度前缀只是 Windows 的写法；别的平台上它就是普通字符，这一格没有意义
                ext = hooks_at(same)
                ext[0] = dict(ext[0], sourcePath="\\\\?\\" + str((same / "hooks" / "hooks.codex.json").resolve()))
                r, p = wd.loaded_root(ext)
                check("实际加载的那一份：同一输出里一条带扩展长度前缀、实为同一处 → 仍认成一处（不误报红）",
                      (r is not None, p), (True, None))
            # sourcePath 在盘符根（或文件系统根）下：往上没有两级，反推不出——给人话，不抛裸 IndexError
            drive_root = [dict(h, sourcePath=os.path.join(os.path.abspath(os.sep), "hooks.codex.json"))
                          for h in hooks_at(same)]
            try:
                r, p = wd.loaded_root(drive_root)
                got = (r, "反推不出插件根" in (p or ""))
            except Exception as e:
                got = f"{type(e).__name__}: {e}"
            check("实际加载的那一份：sourcePath 在盘符根下 → 读不出、点名「反推不出插件根」，不抛裸异常",
                  got, (None, True))
            check("实际加载的那一份：0 条 → 不在这里判（交给 0 条成因那几句）", wd.loaded_root([]), (None, None))
            for label, hooks, given, word in [("条目都没有 sourcePath、没给根", hooks_at(same, drop_sp=True), None,
                                               "一条都没有 sourcePath"),
                                              ("给了同版本的根、hook 来自旧版那份", hooks_at(old), same, "不是同一版本")]:
                try:
                    wd.step_hooks_list(FakeServer(hooks), proj, PID, given)
                    got = ("no error", None, False)
                except wd.DoorError as e:
                    got = (type(e).__name__, e.code, word in str(e))
                except Exception as e:
                    got = (type(e).__name__, None, False)
                check(f"step_hooks_list {label}：InstalledRootError、exit 2、文案说「{word}」",
                      got, ("InstalledRootError", wd.EXIT_ENV, True))
            # 回显的那一份与实际加载的那一份不是同一处（归一也对不上），但两份版本与清单都对 ⇒ 放行，返回实际加载的那一份
            try:
                mine, root = wd.step_hooks_list(FakeServer(hooks_at(same)), proj, PID, twin)
                got = (len(mine), Path(root).resolve())
            except Exception as e:
                got = f"{type(e).__name__}: {str(e)[:120]}"
            check("step_hooks_list 回显与实际加载的不是同一处、两份都对：放行，对账与种信任用实际加载的那一份",
                  got, (len(keys), same.resolve()))
            # 反过来：hook 来自本版那一份，回显的却是旧版 ⇒ 回显那一份的独立核对拦下（角色会按回显那份渲染）
            try:
                wd.step_hooks_list(FakeServer(hooks_at(same)), proj, PID, old)
                got = ("no error", None, False)
            except wd.DoorError as e:
                got = (type(e).__name__, e.code, "不是同一版本" in str(e) and str(old) in str(e))
            except Exception as e:
                got = (type(e).__name__, None, False)
            check("step_hooks_list 实际加载的是本版、回显的是旧版：回显那一份独立核对拦下、文案点名它",
                  got, ("InstalledRootError", wd.EXIT_ENV, True))
            # do_codex 端到端：两种该停的都在写任何项目文件之前停（此刻看得见 hook：plugin add 已把用户层写回 true）
            for label, inst_echo, server_root, drop, word in [
                    ("plugin add 不回显路径、hook 也不带 sourcePath", None, old, True, "一条都没有 sourcePath"),
                    ("回显同版本的根、sourcePath 指向旧版那份", same, old, False, "不是同一版本")]:
                dd, pj = _mk()
                try:
                    srv = RootServer(keys, wd.project_key(pj), pj, server_root, user=False, trusted=False,
                                     no_source_path=drop)
                    before = _snap(pj)
                    code, msg, _calls = _drive_root(pj, srv, inst_echo, add_path=inst_echo is not None)
                    check(f"--codex {label}：exit 2、文案说「{word}」", (code, word in msg), (wd.EXIT_ENV, True))
                    check(f"--codex {label}：项目树逐字节不变、batchWrite 零调用", (_snap(pj) == before, srv.edits),
                          (True, None))
                finally:
                    rm(dd)
            # 该放行的那一格端到端：回显 twin、hook 来自 same（两处路径归一也不相等），两份都是本版且清单完好 ⇒ 装完
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=False)
                code, msg, _calls = _drive_root(pj, srv, twin)
                check("--codex 回显与实际加载的不是同一处、两份版本与清单都对：装机走完（exit 0）", (code, msg[:300]), (0, ""))
            finally:
                rm(dd)
        # --check：反推不出安装根进 problems，不拿本命令的根顶上；其余照跑
        dd, pj = _mk(gen=True)
        saved = (wd.cas.AppServer, wd.run_codex)
        try:
            with _DoorAt(door), _EnvVar("CODEX_HOME", str(home)):
                srv = RootServer(keys, wd.project_key(pj), pj, old, user=False, trusted=True, no_source_path=True,
                                 trusted_hooks={f"{PID}:hooks/hooks.codex.json:{k}" for k in keys[1:]})
                wd.cas.AppServer = _ctx(srv)
                wd.run_codex = lambda *a, **k: (0, "multi_agent stable true", "")
                try:
                    problems, info, _m = wd.check_static("codex", pj, MKT)
                    crashed = None
                except Exception as e:
                    problems, info, crashed = [], [], f"{type(e).__name__}: {e}"
                check("check_static hook 都没有 sourcePath：不整段崩、「反推不出」进 problems",
                      (crashed, any("一条都没有 sourcePath" in p for p in problems)), (None, True))
                check("check_static hook 都没有 sourcePath：其余照跑（未受信那条照报、features 在 info）",
                      (any(keys[0] in p and "untrusted" in p for p in problems),
                       any(i.startswith("features:") for i in info)), (True, True))
        finally:
            wd.cas.AppServer, wd.run_codex = saved
            rm(dd)
    finally:
        rm(d)


def test_fix_round_wording():
    print("[wrote] 修复轮：「说写了但没写」这一方向 / batchWrite 没等到答复 / 版本过低 / 门自己的根坏了 / 先写后核 (c) 格")
    keys = _manifest_keys()
    d = tmpdir()
    try:
        home = d / "codexhome"
        home.mkdir()
        door = _plugin_root_fixture(d / "door", version=DOOR_V)
        same = _plugin_root_fixture(d / "same", version=DOOR_V)
        old = _plugin_root_fixture(d / "old", version=OLD_V)
        blind = _plugin_root_fixture(d / "blind", codex=False, claude=False)
        with _DoorAt(door), _EnvVar("CODEX_HOME", str(home)):
            # (a) 重跑、文件全未变、随后 batchWrite 被拒 ⇒ 必须说「未写任何项目文件」（不许把没动过的文件说成写了）
            dd, pj = _mk()
            try:
                first, _m, _c = _drive_root(pj, RootServer(keys, wd.project_key(pj), pj, same, user=False,
                                                           trusted=False), same)
                before = _snap(pj)
                srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=True, reject_write=True)
                code, msg, _c = _drive_root(pj, srv, same)
                check("重跑未变更 ＋ batchWrite 被拒：前提是首跑装成功", first, 0)
                check("重跑未变更 ＋ batchWrite 被拒：exit 4、项目树逐字节不变", (code, _snap(pj) == before),
                      (wd.EXIT_WRITE, True))
                check("重跑未变更 ＋ batchWrite 被拒：说未写任何项目文件、不说本次已写入",
                      ("未写任何项目文件" in msg, "本次已写入" in msg), (True, False))
                check("batchWrite 被服务端拒绝：照说用户层此刻为 true（被拒的写入没把它改成 false）",
                      wd.USER_LAYER_STILL_TRUE in msg, True)
            finally:
                rm(dd)
            # batchWrite 已生效、答复却超时：说未确认，且**不再断言**「用户层此刻为 true」——那一键也在这次写入里
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=False,
                                 write_timeout_after_apply=True)
                code, msg, _c = _drive_root(pj, srv, same)
                check("batchWrite 答复超时：exit 4、说未确认是否写入、不说未信任任何 hook",
                      (code, "未确认是否写入" in msg, "未信任任何 hook" in msg), (wd.EXIT_WRITE, True, False))
                check("batchWrite 答复超时：不说「用户层此刻为 true」（服务端其实已把它写成 false）",
                      (wd.USER_LAYER_STILL_TRUE in msg, srv.user), (False, False))
            finally:
                rm(dd)
            # (b) app-server 在步 5 的 hooks/list 途中失联（角色已写、batchWrite 从未调用）⇒ 说未写入 hook 信任
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=False, fail_hooks_list_at=2)
                code, msg, _c = _drive_root(pj, srv, same)
                check("步 5 途中失联：exit 2、batchWrite 零调用", (code, srv.edits), (wd.EXIT_ENV, None))
                check("步 5 途中失联：说未写入任何 hook 信任、不说 batchWrite 已写入、点名已写的项目文件",
                      ("未写入任何 hook 信任" in msg, "已写入你的 Codex 用户配置" in msg, "本次已写入的项目文件" in msg),
                      (True, False, True))
            finally:
                rm(dd)
            # 先写后核 (c) 格：plugin add 不回显路径、也没把用户层写回 true、项目已 trust ⇒ 先按本命令的根写，
            # 反推出的安装根核对不过而停；文案点名写过的文件，并说明此后本项目会加载 Codex 侧那一份、怎么重跑
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, old, user=False, trusted=True)
                before = _snap(pj)
                code, msg, _c = _drive_root(pj, srv, None, add_sets_user=False, add_path=False)
                after = _snap(pj)
                changed = sorted(r for r in after if before.get(r) != after[r])
                m = re.search(r"本次已写入的项目文件：(.*?)（门不代删", msg)
                check("先写后核 (c) 格：exit 2、确实先写了、文案列出的文件 == 实际",
                      (code, bool(changed), sorted(m.group(1).split("、")) if m else []), (wd.EXIT_ENV, True, changed))
                check("先写后核 (c) 格：说此后本项目会加载 Codex 侧那一份、重跑用 --codex",
                      ("此后在本项目起的 Codex 会话会加载" in msg, "重跑 `workframe-door --codex`" in msg), (True, True))
            finally:
                rm(dd)
            # 先写后核 (d) 格：同 (c) 的形态，但**回显了** installedPath（另一处同版本、写之前核过）；Codex 实际加载的那一份
            # 落段后才看得见、是旧版 ⇒ 先写、再停；「此后会加载」那句点名的是实际加载的那一份
            twin = _plugin_root_fixture(d / "twin", version=DOOR_V)
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, old, user=False, trusted=True)
                before = _snap(pj)
                lines = []
                code, msg, _c = _drive_root(pj, srv, twin, add_sets_user=False, cap=lines)
                after = _snap(pj)
                changed = sorted(r for r in after if before.get(r) != after[r])
                m = re.search(r"本次已写入的项目文件：(.*?)（门不代删", msg)
                check("先写后核 (d) 格：exit 2、确实先写了、文案列出的文件 == 实际",
                      (code, bool(changed), sorted(m.group(1).split("、")) if m else []), (wd.EXIT_ENV, True, changed))
                roles_at = [ln for ln in lines if ln.startswith("[4/11]")]
                check("先写后核 (d) 格：角色按回显的那一份渲染（首个 [4/11] 点名的插件根是它，不是本命令自己的根）",
                      (len(roles_at), bool(roles_at) and f"插件根 {twin} " in roles_at[0]), (1, True))
                check("先写后核 (d) 格：「此后会加载」那句点名的是 Codex 实际加载的旧版那一份",
                      ("此后在本项目起的 Codex 会话会加载" in msg, f"，v{OLD_V}），不是本命令这一份" in msg), (True, True))
            finally:
                rm(dd)
            # (d') 同一形态，但落段后看得见的 hook 没有 sourcePath ⇒ 此刻并不知道 Codex 加载的是哪一份：那句说「安装根反推不出」，
            # 不点名回显的那一份
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, old, user=False, trusted=True, no_source_path=True)
                code, msg, _c = _drive_root(pj, srv, twin, add_sets_user=False)
                check("先写后核 (d') 格：实际加载的反推不出时，「此后会加载」那句说「安装根反推不出」、不点名回显的那一份",
                      (code, "Codex 侧装着的那一份（安装根反推不出）" in msg, f"（安装根 {twin}" in msg), (wd.EXIT_ENV, True, False))
            finally:
                rm(dd)
            # 先写后核 (e) 格：`--upgrade` 走本地源重装那一路，其余同 (d)（回显另一处同版本、用户层没写回 true、已 trust、段不在）
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, old, user=False, trusted=True)
                before = _snap(pj)
                lines = []
                code, msg, calls = _drive_root(pj, srv, twin, upgrade=True, source_type="local", add_sets_user=False,
                                               cap=lines)
                after = _snap(pj)
                changed = sorted(r for r in after if before.get(r) != after[r])
                m = re.search(r"本次已写入的项目文件：(.*?)（门不代删", msg)
                check("先写后核 (e) 格：走的是本地源重装那一路（plugin add）、exit 2、确实先写了、文案列出的文件 == 实际",
                      (any(c[:2] == ["plugin", "add"] for c in calls), code, bool(changed),
                       sorted(m.group(1).split("、")) if m else []), (True, wd.EXIT_ENV, True, changed))
                check("先写后核 (e) 格：「此后会加载」那句点名实际加载的旧版那一份、重跑用 --upgrade",
                      ("此后在本项目起的 Codex 会话会加载" in msg, f"，v{OLD_V}），不是本命令这一份" in msg,
                       "重跑 `workframe-door --upgrade`" in msg), (True, True, True))
                roles_at = [ln for ln in lines if ln.startswith("[4/11]")]
                check("先写后核 (e) 格：角色按回显的那一份渲染（首个 [4/11] 点名的插件根是它，不是本命令自己的根）",
                      (len(roles_at), bool(roles_at) and f"插件根 {twin} " in roles_at[0]), (1, True))
            finally:
                rm(dd)
            # 回显的 installedPath 接进了 do_codex——钉住「回显的那一份在写之前核」这一环：回显的是旧版、Codex 实际加载的
            # 是本版（`plugin add` 把用户层写回 true，现有样本的形态）⇒ 只有回显那一份核对不过，必须在写之前停、零写入。
            # 回显在 do_codex / step_upgrade 里被丢掉时，这两格会照装（安装根取实际加载的那一份）
            for label, kw in (("--codex", {}), ("--upgrade 本地源重装", {"upgrade": True, "source_type": "local"})):
                dd, pj = _mk()
                try:
                    srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=True)
                    before = _snap(pj)
                    code, msg, _c = _drive_root(pj, srv, old, **kw)
                    check(f"回显接进 do_codex（{label}）：回显的是旧版、实际加载的是本版 ⇒ exit 2、写之前停（项目树逐字节不变、"
                          f"batchWrite 零调用）、文案点名回显的那一份",
                          (code, _snap(pj) == before, srv.edits, f"Codex 侧装上的插件在 {old}（v{OLD_V}）" in msg),
                          (wd.EXIT_ENV, True, None, True))
                finally:
                    rm(dd)
            # 先写后停 (f) 格：用户层是 true、Codex 却列不出本插件的任何 hook ⇒ 写之前看不见实际加载的那一份，角色照写，
            # 随后在步 5 以 3 停下；文案点名写过的文件（没有「此后会加载」那句：不是因安装根停下）
            dd, pj = _mk()
            try:
                srv = RootServer([], wd.project_key(pj), pj, same, user=None, trusted=False)
                before = _snap(pj)
                code, msg, _c = _drive_root(pj, srv, same)
                after = _snap(pj)
                changed = sorted(r for r in after if before.get(r) != after[r])
                m = re.search(r"本次已写入的项目文件：(.*?)（门不代删", msg)
                check("先写后停 (f) 格：用户层 true 但列不出 hook ⇒ exit 3、确实先写了、文案列出的文件 == 实际、不说「此后会加载」",
                      (srv.user, code, bool(changed), sorted(m.group(1).split("、")) if m else [],
                       "此后在本项目起的 Codex 会话会加载" in msg), (True, wd.EXIT_COUNT, True, changed, False))
            finally:
                rm(dd)
            # 版本过低发生在 --backfill 先写过 CC 那半之后：失败文案同样说写过什么
            dd, pj = _mk()
            try:
                code, msg, calls = _drive_root(pj, RootServer(keys, wd.project_key(pj), pj, same), same,
                                               version="codex-cli 0.150.0", wrote=[".claude/settings.json"])
                check("codex 版本过低、此前已写 CC 那半：exit 2、文案点名写过的文件",
                      (code, ".claude/settings.json" in msg, "0.153.4" in msg), (wd.EXIT_ENV, True, True))
            finally:
                rm(dd)
        # 门自己的插件根读不出版本 ⇒ 开跑即停：codex CLI 一次不调（市场注册 / 插件安装 / 用户层都不动）
        with _DoorAt(blind), _EnvVar("CODEX_HOME", str(home)):
            dd, pj = _mk()
            try:
                code, msg, calls = _drive_root(pj, RootServer(keys, wd.project_key(pj), pj, same), same)
                check("门自己读不出版本：exit 2、codex CLI 零调用、文案点名门自己的根与未写",
                      (code, calls, "本命令自己所在的插件根" in msg and "读不出版本" in msg, "未写任何文件" in msg),
                      (wd.EXIT_ENV, [], True, True))
            finally:
                rm(dd)
    finally:
        rm(d)
    # batchWrite 没等到答复（超时 / 退出）≠ 被拒：前者不许说「未信任任何 hook」
    for label, exc, want in [("没等到答复（超时）", cas.AppServerError("config/batchWrite 超时（30.0s 无响应）"),
                              (wd.EXIT_WRITE, True, False)),
                             ("服务端明确拒绝", cas.AppServerRpcError("config/batchWrite 返回错误: -32000 readonly"),
                              (wd.EXIT_WRITE, False, True))]:
        class Raiser:
            def config_batch_write(self, edits, exc=exc, **kw):
                raise exc
        try:
            wd.step_write(Raiser(), [_hook("stop:0:0")], "k", False)
            got = ("no error", None, None)
        except wd.DoorError as e:
            got = (e.code, "未确认是否写入" in str(e), "未信任任何 hook" in str(e))
        check(f"batchWrite {label}：exit 4 / 说未确认 / 说未信任任何 hook", got, want)
    # 客户端那一侧：只有「服务端回了 error 对象」抛 AppServerRpcError；超时仍是普通 AppServerError
    class _Pipe:
        def write(self, _b):
            pass

        def flush(self):
            pass

    class _Proc:
        stdin = _Pipe()
        returncode = None

        def poll(self):
            return None

    for label, pending, want in [("服务端回了 error 对象", {1: {"id": 1, "error": {"code": -32000, "message": "nope"}}},
                                  "AppServerRpcError"),
                                 ("超时", {}, "AppServerError")]:
        srv = cas.AppServer(exe="codex-stub")
        srv.proc = _Proc()
        srv._pending = dict(pending)
        try:
            srv.request("config/batchWrite", {}, timeout=0.05)
            got = "no error"
        except cas.AppServerError as e:
            got = type(e).__name__
        check(f"app-server 客户端 {label} → {want}", got, want)


def _with_door(root):
    """给 fixture 插件根补一份门脚本（占位）：真机的新版本目录里带着 `scripts/workframe_door.py`，出路才点得了名。"""
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "workframe_door.py").write_bytes(b"# stub door\n")
    return root


class _CodexLayout:
    """真机的 Codex 目录布局 ＋ `marketplace upgrade` 的真机副作用，门可以放在三处之一。

    前三条是真 codex（0.153.4）的读数：升级在 `<CODEX_HOME>/plugins/cache/<市场>/core/<新版本>/`
    建新目录、**删掉旧版本目录**，`hooks/list` 的 `sourcePath` 随之指向新目录；第四条是按那条命令「刷快照」的
    语义推定、没有单独的目录级读数：git 源的市场快照 `<CODEX_HOME>/.tmp/marketplaces/<市场>/` 被**原地刷新**。门在：
      `cc`（对照）——CC 的插件缓存里，Codex 碰不到；`install-cache`——Codex 安装缓存的旧版本目录，升级会删掉它；
      `snapshot`——git 源的市场快照里（launcher 的 Codex 定位器给的 `CORE` 就在这里），升级会原地改写它。"""

    def __init__(self, d, door_where, old=DOOR_V, new=NEW_V):
        self.home = d / "codexhome"
        self.cache = self.home / "plugins" / "cache" / MKT / "core"
        self.old, self.new = old, new
        self.old_dir, self.new_dir = self.cache / old, self.cache / new
        self.snap_root = self.home / ".tmp" / "marketplaces" / MKT
        self.snap_core = self.snap_root / "plugins" / "core"
        _with_door(_plugin_root_fixture(self.old_dir, version=old))
        if door_where == "cc":
            self.door = _with_door(_plugin_root_fixture(d / "claudehome" / "plugins" / "cache" / MKT / "core" / old,
                                                        version=old))
        elif door_where == "install-cache":
            self.door = self.old_dir
        else:
            self.door = _with_door(_plugin_root_fixture(self.snap_core, version=old))
            (self.snap_root / ".claude-plugin").mkdir(parents=True, exist_ok=True)
            (self.snap_root / ".claude-plugin" / "marketplace.json").write_bytes(b"{}")
        self.server = None

    def upgrade(self):
        _with_door(_plugin_root_fixture(self.new_dir, version=self.new))
        shutil.rmtree(self.old_dir)
        if self.snap_core.exists():
            for rel in (".codex-plugin", ".claude-plugin"):
                (self.snap_core / rel / "plugin.json").write_bytes(
                    json.dumps({"name": "core", "version": self.new}).encode("utf-8"))
        if self.server is not None:
            self.server.root = self.new_dir


def test_upgrade_door_layouts():
    print("[upgrade] 门落后于刷新后的 Codex 侧：门在 CC 缓存 / Codex 安装缓存 / git 快照三处，都停在「落后的是本命令」、"
          "点名新目录下的门；按出路用新门重跑能走完")
    keys = _manifest_keys()
    notes = {"cc": None, "install-cache": "这个目录此刻已经不在了", "snapshot": f"这个目录此刻已被刷新成 v{NEW_V}"}
    for where in ("cc", "install-cache", "snapshot"):
        d = tmpdir()
        try:
            lay = _CodexLayout(d, where)
            with _DoorAt(lay.door), _EnvVar("CODEX_HOME", str(lay.home)):
                dd, pj = _mk(gen=True)
                try:
                    srv = RootServer(keys, wd.project_key(pj), pj, lay.old_dir, user=False, trusted=True)
                    lay.server = srv
                    before = _snap(pj)
                    code, msg, _calls = _drive_root(pj, srv, None, upgrade=True, on_upgrade=lay.upgrade)
                    new_door = lay.new_dir / "scripts" / "workframe_door.py"
                    names = f'python "{new_door}"' in msg or f'python "{new_door.resolve()}"' in msg
                    check(f"--upgrade 门在 {where}：前提——刷新确实发生（旧版本目录已删、新目录在）",
                          (lay.old_dir.exists(), lay.new_dir.exists()), (False, True))
                    check(f"--upgrade 门在 {where}：exit 2、停在「落后的是本命令」、出路点名新目录下的门",
                          (code, "落后的是本命令" in msg, names), (wd.EXIT_ENV, True, True))
                    check(f"--upgrade 门在 {where}：写之前停（项目树逐字节不变、batchWrite 零调用）",
                          (_snap(pj) == before, srv.edits), (True, None))
                    want = notes[where]
                    check(f"--upgrade 门在 {where}：「本命令来自 X」如实说明 X 此刻的样子",
                          (want in msg) if want else ("这个目录此刻" not in msg), True)
                finally:
                    rm(dd)
                # 按出路用新目录下的门重跑（同一个项目）：这次两边同版本，装完
                dd, pj = _mk(gen=True)
                try:
                    with _DoorAt(lay.new_dir):
                        srv2 = RootServer(keys, wd.project_key(pj), pj, lay.new_dir, user=False, trusted=True)
                        code2, msg2, _c2 = _drive_root(pj, srv2, None, upgrade=True)
                    check(f"--upgrade 门在 {where}：按出路改用新目录下的门重跑，装完（exit 0）", (code2, msg2[:300]), (0, ""))
                finally:
                    rm(dd)
        finally:
            rm(d)
    # (a) 格在 Codex-only 布局下：项目里还没有段、落段前看不见 hook ⇒ 首轮要按「本命令这一份」生成角色，而它所在的目录
    # 已被这次刷新删掉 / 改写 ⇒ 不按别人的文件写，在写任何项目文件之前停，出路点名缓存里的新门；对照（门在 CC 缓存）照旧先写后核
    for where in ("install-cache", "snapshot", "cc"):
        d = tmpdir()
        try:
            lay = _CodexLayout(d, where)
            with _DoorAt(lay.door), _EnvVar("CODEX_HOME", str(lay.home)):
                dd, pj = _mk()
                try:
                    srv = RootServer(keys, wd.project_key(pj), pj, lay.old_dir, user=False, trusted=True)
                    lay.server = srv
                    before = _snap(pj)
                    code, msg, _calls = _drive_root(pj, srv, None, upgrade=True, on_upgrade=lay.upgrade)
                    new_door = lay.new_dir / "scripts" / "workframe_door.py"
                    names = str(new_door) in msg or str(new_door.resolve()) in msg
                    drift = "要按本命令自己的插件根生成角色" in msg
                    if where == "cc":
                        check("(a) 格、门在 CC 缓存（对照）：照旧先写后核（exit 2、写过文件、不走「按本命令生成」那句）",
                              (code, _snap(pj) != before, drift), (wd.EXIT_ENV, True, False))
                    else:
                        check(f"(a) 格、门在 {where}：写任何项目文件之前停、说明本命令所在目录已变、出路点名缓存里的新门",
                              (code, _snap(pj) == before, drift, names), (wd.EXIT_ENV, True, True, True))
                finally:
                    rm(dd)
        finally:
            rm(d)
    # 守卫文案自己不说写没写，交给收尾的 `_wrote_note`：此前已写过文件时（`--backfill` 先写了 CC 那半的形态），同一条
    # 文案里不能一边说「在写任何项目文件之前」、一边列出写过的文件；目录没了的原因也不只说「升级」（`plugin add` 同样会删）
    d = tmpdir()
    try:
        lay = _CodexLayout(d, "install-cache")
        with _DoorAt(lay.door), _EnvVar("CODEX_HOME", str(lay.home)):
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, lay.old_dir, user=False, trusted=True)
                lay.server = srv
                code, msg, _calls = _drive_root(pj, srv, None, upgrade=True, on_upgrade=lay.upgrade,
                                                wrote=[".claude/settings.json"])
                check("(a) 格守卫、此前已写过文件：停在「在按它生成角色之前」、列出写过的文件、不自带「在写任何项目文件之前」、"
                      "目录没了的原因点名 plugin add 与 marketplace upgrade",
                      (code, "要按本命令自己的插件根生成角色" in msg, "在按它生成角色之前停下" in msg,
                       "本次已写入的项目文件：.claude/settings.json" in msg, "在写任何项目文件之前" in msg,
                       "`plugin add` / `marketplace upgrade`" in msg),
                      (wd.EXIT_ENV, True, True, True, False, True))
            finally:
                rm(dd)
    finally:
        rm(d)
    # 门在 git 快照里时，出路 ① 不把 Codex 自己的快照目录当成「本命令所属的那份市场源」点名（照抄只会撞同名不同源）
    d = tmpdir()
    try:
        lay = _CodexLayout(d, "snapshot")
        older = _plugin_root_fixture(d / "older", version=OLD_V)
        with _DoorAt(lay.door), _EnvVar("CODEX_HOME", str(lay.home)):
            m = wd.version_mismatch(older) or ""
        check("门在 git 快照里：出路 ① 不点名 Codex 自己的快照目录（给占位）", ("市场目录" in m, "例如本地框架仓" in m), (False, True))
    finally:
        rm(d)


# ---- 装前核对（步 3 之前）：Codex 登记的市场里将要装的那一份 ----

def _avail(core, where="available", row_version=None, market=MKT, decoy=None, core_path=True, prefix=False):
    """`codex plugin list --json --available -m <市场>` 的真形态（字段取自 codex-cli 0.153.4 的真机输出）。

    core 那一行放进 `where` 指的数组（真机：没装时只在 `available[]`、装上后只在 `installed[]`），另一个数组为空；行上的
    `version` 取 `row_version`（真机上已装时它是已装缓存的版本，与市场里那一份无关）。**同一数组里排在它前面的是两条
    诱饵**：同市场的 `workframe-launcher`（真机就排在 core 前面、且与 core 锁步同版本——这里故意让它指向另一个版本）与
    别的市场里同名的 `core`，两者的 `source.path` 都指向 `decoy`：给一个与 core 那一份结论相反的插件根，去掉「按插件名」
    或「按市场名」任一个过滤条件都会取错、结论翻过来。`core=None` 不造 core 那一行；`core_path=False` 时它没有
    `source.path`；`prefix=True` 时 `source.path` 带扩展长度前缀。"""
    def row(name, mkt, path, ver):
        r = {"pluginId": f"{name}@{mkt}", "name": name, "marketplaceName": mkt, "version": ver,
             "installed": where == "installed", "enabled": False,
             "marketplaceSource": {"sourceType": "local", "source": "\\\\?\\C:\\mkt"}}
        if path is not None:
            r["source"] = {"source": "local", "path": ("\\\\?\\" if prefix else "") + str(path)}
        return r
    rows = []
    if decoy is not None:
        rows += [row("workframe-launcher", market, decoy, "0.0.1"), row(wd.PLUGIN_NAME, "elsewhere", decoy, "0.0.1")]
    if core is not None:
        rows.append(row(wd.PLUGIN_NAME, market, core if core_path else None, row_version or "0.0.1"))
    return json.dumps({where: rows, ("available" if where == "installed" else "installed"): []})


def test_preinstall_check():
    print("[pending] 装前核对：市场里将要装的那一份与本命令不同版本 → plugin add 之前停下；读不出放行；三个重装点都经过它")
    keys = _manifest_keys()
    AV = ["plugin", "list", "--json", "--available"]

    def m_args(calls):
        return [c[c.index("-m") + 1] for c in calls if c[:4] == AV and "-m" in c]

    def added(calls):
        return ["plugin", "add"] in [c[:2] for c in calls]

    # ① helper 本身：两个数组都扫、问的就是那一条命令、超时不沿用 180 s、形态坏不崩
    d = tmpdir()
    try:
        m_old = _plugin_root_fixture(d / "mkt-old" / "plugins" / "core", version=OLD_V, agents=False)
        m_same = _plugin_root_fixture(d / "mkt-same" / "plugins" / "core", version=DOOR_V, agents=False)
        for label, out, want in [("没装：core 只在 available[]", _avail(m_old, decoy=m_same), str(m_old)),
                                 ("已装：core 只在 installed[]", _avail(m_old, where="installed", decoy=m_same), str(m_old)),
                                 ("只有两条诱饵、没有本市场的 core", _avail(None, decoy=m_same), None),
                                 ("installed 不是列表、available 是 null", json.dumps({"installed": "x", "available": None}), None),
                                 ("行不是对象", json.dumps({"installed": [1, "a"], "available": [None]}), None),
                                 # 顶层合法 JSON 却不是对象：只判「解析得出」的话，下一步 `.get` 抛 AttributeError（docstring 说不抛）
                                 ("顶层是 JSON 数组", "[]", None),
                                 ("顶层是 JSON 标量", "3", None)]:
            calls, restore = _run_codex_stub(lambda a, out=out: (0, out, ""))
            try:
                try:
                    row, why = wd.market_core_row(_NO_CODEX, MKT, None)
                    got = ((row or {}).get("source") or {}).get("path")
                except Exception as e:      # 形态坏时崩了记成一格红，不让整组停下
                    got, why = f"<{type(e).__name__}>", None
            finally:
                restore()
            check(f"market_core_row {label}：取到的 source.path", got, want)
            check(f"market_core_row {label}：拿不到时给原因", (got is None) == bool(why), True)
            check(f"market_core_row {label}：问的恰是 plugin list --json --available -m <市场>", calls,
                  [["plugin", "list", "--json", "--available", "-m", MKT]])
        seen = {}
        saved = wd.run_codex
        wd.run_codex = lambda exe, args, cwd=None, timeout=180, env=None: (seen.update(t=timeout), (1, "", ""))[1]
        try:
            wd.market_core_row(_NO_CODEX, MKT, None)
        finally:
            wd.run_codex = saved
        check("装前核对那条命令的超时不沿用 run_codex 的 180 s", (seen.get("t"), (seen.get("t") or 999) < 180),
              (wd.PENDING_LIST_TIMEOUT, True))
        # 结构性封闭：helper 显式收 exe——拿 _SEAL 下一个不存在的路径、用真的 run_codex 跑一次：起不来就放行，跑不到任何 codex
        check("封闭：这一格用的是被测模块自己的 run_codex（下一格真去起进程）", wd.run_codex is _RUN_CODEX, True)
        try:
            row, why = wd.market_core_row(str(_SEAL / "no-such-codex" / "codex.exe"), MKT, None)
        except Exception as e:          # 没兜住异常时记成一格红，不让整组崩掉
            row, why = f"<外抛 {type(e).__name__}>", None
        check("封闭：helper 拿 _SEAL 下不存在的 exe 真跑 → 起不来（FileNotFoundError）、返回原因放行", (row, "FileNotFoundError" in (why or "")),
              (None, True))
    finally:
        rm(d)

    # ② 端到端（do_codex）：三个重装点、停与不停、写没写如实
    d = tmpdir()
    try:
        home = d / "codexhome"
        home.mkdir()
        door = _plugin_root_fixture(d / "door", version=DOOR_V)
        same = _plugin_root_fixture(d / "same", version=DOOR_V)          # plugin add 回显 / hook 来源：与门同版本
        old = _plugin_root_fixture(d / "old", version=OLD_V)
        m_old = _plugin_root_fixture(d / "mkt-old" / "plugins" / "core", version=OLD_V)
        m_same = _plugin_root_fixture(d / "mkt-same" / "plugins" / "core", version=DOOR_V)
        m_new = _with_door(_plugin_root_fixture(d / "mkt-new" / "plugins" / "core", version=NEW_V))
        m_blind = _plugin_root_fixture(d / "mkt-blind" / "plugins" / "core", codex=False, claude=False)
        remove_line = "出路：先自己跑 `codex plugin marketplace remove workframe`"
        stopped = "装前核对在 `plugin add` 之前停下"
        old_second = "先跑 `workframe-door --upgrade` 刷新"       # `_door_outlets` 的 ②：装前那一态照它走会把全机缓存换成旧版

        def passed(ln):         # 「装前核对通过」那一行：步 3 之前的子行，不占步号
            return "装前核对：" in ln and "与本命令同版本" in ln

        def steps(out):         # 终端上的步号序列（2' 记作 2.5）：步号应当严格递增、每个只出现一次
            got = []
            for ln in out:
                m = re.match(r"\[(\d+)('?)/11\]", ln)
                if m:
                    got.append(int(m.group(1)) + (0.5 if m.group(2) else 0))
            return got

        with _DoorAt(door), _EnvVar("CODEX_HOME", str(home)):
            # B1 --codex、步 2 是新登记、市场里那一份更旧
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=False)
                before = _snap(pj)
                out = []
                code, msg, calls = _drive_root(pj, srv, same, avail=_avail(m_old, decoy=m_same), cap=out)
                check("B1 --codex 市场里将要装的更旧：exit 2、plugin add 没跑、用户层没被写回 true、batchWrite 零调用、项目树不变",
                      (code, added(calls), srv.user, srv.edits, _snap(pj) == before), (wd.EXIT_ENV, False, False, None, True))
                check("B1：装前核对按本市场问、恰一次", m_args(calls), [MKT])
                check("B1：文案说「将要装」、两个版本、市场里那一份的根，不说「装上的」",
                      ("Codex 登记的市场里将要装的插件与本命令不是同一版本" in msg, f"v{OLD_V}" in msg, f"v{DOOR_V}" in msg,
                       str(m_old) in msg, "Codex 侧装上的插件" in msg), (True, True, True, True, False))
                check("B1：说在 plugin add 之前停下、缓存与 enabled 没动，不带「用户层此刻为 true」",
                      ("在 `plugin add` 之前停下" in msg, "enabled 都没动" in msg, wd.USER_LAYER_STILL_TRUE in msg),
                      (True, True, False))
                check("B1：步 2 是新登记——说登记成了什么、可能换掉了本地登记，出路直接给自己先 remove",
                      ("第 2 步把市场 'workframe' 登记成了 src" in msg, "那条登记已被它换掉" in msg, remove_line in msg),
                      (True, True, True))
                # 市场那份更旧时不借 `_door_outlets`：它的 ② 照着走会按这条登记把全机缓存刷成旧版（真机实测）；
                # 明说别拿 --upgrade 来补、为什么，以及本机其他已装项目里 --check 看不出这一态
                check("B1：不给 `--upgrade` 那条出路，明说别拿它来补、它会把全机缓存换成这一版、其他已装项目里 --check 看不出",
                      (old_second in msg, "别拿 `workframe-door --upgrade` 来补" in msg, f"第 2 步刚按它取回的就是 v{OLD_V}" in msg,
                       "会把本机已装的插件缓存" in msg, "`--check` 此刻照样报通过" in msg), (False, True, True, True, True))
                # 先给眼下走得通的那条，不先列 ①② 再在末尾改口
                check("B1：出路（remove 再带 --marketplace）先于事实与「别走」那段，不出现「照上面 ①」式的改口",
                      (0 <= msg.find(remove_line) < msg.find(stopped) < msg.find("别拿 `workframe-door --upgrade`"),
                       "workframe-door --codex --marketplace <本命令所属的那份源>" in msg, "照上面 ①" in msg, "① " in msg),
                      (True, True, False, False))
                check("B1：新登记时不给「把本地目录更新」那条（登记是第 2 步刚写的，不是你的本地目录）",
                      "也可以把那个目录更新到" in msg, False)
                check("B1：门不在市场目录里 ⇒ 出路给占位、不编路径（点名那一半见 B-h）",
                      ("（例如本地框架仓）" in msg, "本命令就来自市场目录" in msg), (True, False))
                check("B1：终端上没有「装前核对通过」那一行", any(passed(ln) for ln in out), False)
            finally:
                rm(dd)
            # B1' 同形，但步 2 回报早已登记
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=False)
                code, msg, calls = _drive_root(pj, srv, same, avail=_avail(m_old, decoy=m_same), already=True)
                check("B1' 早已登记：exit 2、说本次之前就已按同一个源登记，不说新登记",
                      (code, added(calls), "在本次之前就已按同一个源登记" in msg, "第 2 步把市场" in msg),
                      (wd.EXIT_ENV, False, True, False))
                # 早已登记的那条若是 git 源（本地登记早被换掉、缓存还是新版），`--upgrade` 同样会降级缓存、带别的
                # --marketplace 同样被拒 ⇒ 出路同 B1（remove 再带 --marketplace），另给「本地目录落后就更新它」
                check("B1' 早已登记：出路给 remove、另给「更新本地目录」、不给 `--upgrade` 那条、说「这个源此刻若仍是」",
                      (remove_line in msg, f"更新到 v{DOOR_V} 后原样重跑本命令" in msg, old_second in msg,
                       f"这个源此刻若仍是 v{OLD_V}" in msg), (True, True, False, True))
            finally:
                rm(dd)
            # B2 同版本 ⇒ 放行、整条走完（诱饵指向旧版：过滤条件坏了这一格会误停）
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=False)
                out = []
                code, msg, calls = _drive_root(pj, srv, same, avail=_avail(m_same, decoy=m_old), cap=out)
                check("B2 市场里将要装的与本命令同版本：装机走完、plugin add 跑了、说了装前核对通过",
                      (code, msg[:300], added(calls), any(passed(ln) for ln in out)), (0, "", True, True))
                st = steps(out)
                ok_at = [i for i, ln in enumerate(out) if passed(ln)]
                add_at = [i for i, ln in enumerate(out) if ln.startswith("[3/11] 插件")]
                check("B2 步号：[3/11] 只出现一次（打在 plugin add 那一行）、全程严格递增、装前核对那一行在它之前",
                      (st.count(3), all(a < b for a, b in zip(st, st[1:])), bool(ok_at and add_at and ok_at[0] < add_at[0])),
                      (1, True, True))
            finally:
                rm(dd)
            # B3 已装形态：core 只在 installed[]、行上的 version 是本命令的版本（已装缓存），市场里那一份却更旧 ⇒ 停；
            #    同一形态、source.path 带扩展长度前缀 ⇒ 剥掉后照样读出、照样停
            for label, kw in (("行上 version 与门相同、市场里那份更旧", {}), ("source.path 带扩展长度前缀", {"prefix": True})):
                dd, pj = _mk()
                try:
                    srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=False)
                    code, msg, calls = _drive_root(pj, srv, same, avail=_avail(
                        m_old, where="installed", row_version=DOOR_V, decoy=m_same, **kw))
                    check(f"B3 已装形态（core 只在 installed[]）、{label}：读 source.path 那一份的版本 ⇒ exit 2、plugin add 没跑",
                          (code, added(calls), str(m_old) in msg), (wd.EXIT_ENV, False, True))
                    # 前缀剥没剥，Windows 上读版本分辨不出（`Path` 认得 `\\?\`），只在文案里看得出：带前缀的串同样包含
                    # `str(m_old)`，所以另核判读句里那个根是**不带前缀**的写法（POSIX 上没剥就读不出、上一格已红）
                    check(f"B3 {label}：判读句里市场那一份的根不带扩展长度前缀", ("\\\\?\\" in msg, f"在 {m_old}（" in msg),
                          (False, True))
                finally:
                    rm(dd)
            # B4 读不出就放行、打一行 i，装后核对（3'）照常起作用：这里 plugin add 回显的是旧版，由 3' 拦下
            def timeout(_args):
                raise subprocess.TimeoutExpired("codex", wd.PENDING_LIST_TIMEOUT)
            # 每种形态另核 `i` 行说的原因：只核「放行了」分辨不出是哪一道守卫在起作用——例如去掉「没有 source.path」
            # 那道守卫时，`Path("")` 落到门进程的 cwd、读不出版本，照样放行，只有原因变了
            for label, av, why in [("命令失败（stdout 仍是能命中的合法载荷）", (1, _avail(m_old, decoy=m_same), ""),
                                    "命令失败或输出不是 JSON（rc=1）"),
                                   ("输出不是 JSON", "not json", "命令失败或输出不是 JSON（rc=0）"),
                                   ("没有本市场的 core 那一行（只有诱饵）", _avail(None, decoy=m_old),
                                    f"输出里没有市场 {MKT!r} 的 core 那一行"),
                                   ("core 那一行没有 source.path", _avail(m_old, core_path=False, decoy=m_old),
                                    "core 那一行没有 source.path"),
                                   ("source.path 那一份读不出版本", _avail(m_blind, decoy=m_old), f"那一份 {m_blind} 读不出版本"),
                                   ("命令超时", timeout, "命令没跑成（TimeoutExpired）")]:
                dd, pj = _mk()
                try:
                    srv = RootServer(keys, wd.project_key(pj), pj, old, user=False, trusted=False)
                    out = []
                    try:
                        code, msg, calls = _drive_root(pj, srv, old, avail=av, cap=out)
                    except Exception as e:      # 读不出时异常外抛（没兜住）：记成一格红，不让整组崩掉
                        code, msg, calls = f"<外抛 {type(e).__name__}>", "", []
                    check(f"B4 {label}：放行（plugin add 跑了）、打一行「装前核对没核成」、随后由装后核对以 2 停下",
                          (added(calls), any(ln.startswith("   i 装前核对没核成") for ln in out), code,
                           "Codex 侧装上的插件在" in msg, "将要装" in msg), (True, True, wd.EXIT_ENV, True, False))
                    check(f"B4 {label}：`i` 行说的是这一种原因", any(ln.startswith("   i 装前核对没核成") and why in ln
                                                                  for ln in out), True)
                finally:
                    rm(dd)
            # B5 --upgrade 本地源的两个重装点：源形态判得出（local）/ 判不出、marketplace upgrade 被拒后落回。
            #    停下时那句「会把 enabled 写回 true」不说（没写）；过了时它说在「装前核对通过」之后、plugin add 之前
            for label, kw in (("源形态判得出（local）", {"source_type": "local"}),
                              ("源形态判不出、upgrade 被拒后落回", {"source_type": None, "upgrade_resp": (
                                  1, "", f"marketplace '{MKT}' {wd.UPGRADE_NOT_GIT}")})):
                dd, pj = _mk()
                try:
                    srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=True)
                    before = _snap(pj)
                    out = []
                    code, msg, calls = _drive_root(pj, srv, same, upgrade=True, avail=_avail(m_old, decoy=m_same),
                                                   cap=out, **kw)
                    check(f"B5 --upgrade {label}、市场里那份更旧：exit 2、plugin add 没跑、项目树不变、用户层没被写回 true",
                          (code, added(calls), _snap(pj) == before, srv.user), (wd.EXIT_ENV, False, True, False))
                    check(f"B5 --upgrade {label}：没说「写回 true」、不带用户层残留句、说「将要装」、不说第 2 步",
                          (any("写回 true" in ln for ln in out), wd.USER_LAYER_STILL_TRUE in msg, "将要装" in msg,
                           "第 2 步" in msg), (False, False, True, False))
                    check(f"B5 --upgrade {label}：装前核对按本市场问", m_args(calls), [MKT])
                    # 这一路登记的是本地目录、早就在：出路给 remove 再 `--codex --marketplace`，另给「更新那个目录」；
                    # 不给 `_door_outlets` 的 ②（照它重跑 --upgrade 只会在同一处再停一次），也不说「别拿 --upgrade 来补」那段
                    #（那段是给有步 2 的 --codex 的）
                    check(f"B5 --upgrade {label}：出路给 remove 与「更新本地目录」、不给 ②、不带步 2 那段「别拿 --upgrade 来补」",
                          (remove_line in msg, f"更新到 v{DOOR_V} 后原样重跑本命令" in msg, old_second in msg,
                           "别拿 `workframe-door --upgrade`" in msg), (True, True, False, False))
                    check(f"B5 --upgrade {label}：出路先于「在 plugin add 之前停下」那段事实",
                          0 <= msg.find(remove_line) < msg.find(stopped), True)
                finally:
                    rm(dd)
                dd, pj = _mk()
                try:
                    srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=True)
                    out = []
                    code, msg, calls = _drive_root(pj, srv, same, upgrade=True, avail=_avail(m_same, decoy=m_old),
                                                   cap=out, **kw)
                    ok_at = [i for i, ln in enumerate(out) if passed(ln)]
                    true_at = [i for i, ln in enumerate(out) if "写回 true" in ln]
                    check(f"B5 --upgrade {label}、同版本：走完、plugin add 跑了、「写回 true」说在装前核对通过之后",
                          (code, msg[:300], added(calls), bool(ok_at and true_at and ok_at[0] < true_at[0])), (0, "", True, True))
                    # 选路那一行（[2'/11]）先于装前核对说；步号严格递增、[3/11] 只一次（打在 plugin add 那一行）
                    route_at = [i for i, ln in enumerate(out) if ln.startswith("[2'/11]") and "改走 `plugin add" in ln]
                    add_at = [i for i, ln in enumerate(out) if ln.startswith("[3/11] 插件")]
                    st = steps(out)
                    check(f"B5 --upgrade {label}、同版本：[2'/11] 选路 → 装前核对 → 写回 true → [3/11] 插件，步号不倒序、[3/11] 只一次",
                          (bool(route_at and ok_at and true_at and add_at and route_at[0] < ok_at[0] < true_at[0] < add_at[0]),
                           all(a < b for a, b in zip(st, st[1:])), st.count(3)), (True, True, 1))
                finally:
                    rm(dd)
            # B6 市场里那份比门新（本地仓已 bump、门还是旧的）：--upgrade 本地源停下，出路点名**市场目录里自带的门**；
            #    按出路改用那份门重跑（同版本）走完
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=True)
                code, msg, calls = _drive_root(pj, srv, same, upgrade=True, source_type="local",
                                               avail=_avail(m_new, decoy=m_same))
                new_door = m_new / "scripts" / "workframe_door.py"
                check("B6 --upgrade 市场里那份更新：exit 2、plugin add 没跑、落后的是本命令、出路点名市场目录里的门",
                      (code, added(calls), "落后的是本命令" in msg, "Codex 登记的市场里那份更新" in msg,
                       f'python "{new_door}"' in msg), (wd.EXIT_ENV, False, True, True, True))
            finally:
                rm(dd)
            with _DoorAt(m_new):
                dd, pj = _mk()
                try:
                    srv = RootServer(keys, wd.project_key(pj), pj, m_new, user=False, trusted=True)
                    code, msg, calls = _drive_root(pj, srv, m_new, upgrade=True, source_type="local",
                                                   avail=_avail(m_new, decoy=m_old))
                    check("B6 按出路改用市场目录里的门重跑：装完（exit 0）", (code, msg[:300]), (0, ""))
                finally:
                    rm(dd)
            # B6' --codex、步 2 新登记、市场里那份更新：说新登记，但出路是换用市场里的门（那时步 2 回 alreadyAdded），不给 remove
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=False)
                code, msg, calls = _drive_root(pj, srv, same, avail=_avail(m_new, decoy=m_same))
                check("B6' --codex 新登记 ＋ 市场里那份更新：exit 2、说新登记、出路点名市场里的门、不给 remove 那句",
                      (code, "第 2 步把市场" in msg, f'python "{m_new / "scripts" / "workframe_door.py"}"' in msg,
                       remove_line in msg), (wd.EXIT_ENV, True, True, False))
            finally:
                rm(dd)
            # B-m 回显的市场名与参数不同、按回显名重新预检过了 ⇒ 装前核对按回显名问（-m other），不按参数名
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=False)
                code, msg, calls = _drive_root(pj, srv, same, echoed="other",
                                               avail=_avail(m_old, market="other", decoy=m_same))
                check("B-m 回显名是 other：装前核对问 -m other、停下、plugin add 没跑",
                      (m_args(calls), code, added(calls)), (["other"], wd.EXIT_ENV, False))
                # 出路里要 remove 的是本次回显的那个市场名：照抄默认名删的是另一条登记（或者什么都没删）
                check("B-m：出路里 remove 的是回显名 other、不是默认名",
                      ("`codex plugin marketplace remove other`" in msg, "`codex plugin marketplace remove workframe`" in msg),
                      (True, False))
            finally:
                rm(dd)
            # B-h 门本身住在一个市场目录里（本地框架仓就是这样）⇒ 出路点名那个目录，照抄 `--marketplace` 有具体路径可填；
            # 与 B1（门不在市场目录里 ⇒ 给占位）并排：只有一格时「恒点名」或「恒给占位」都会绿
            mkt_door = d / "mkt-door"
            door_in_mkt = _plugin_root_fixture(mkt_door / "plugins" / "core", version=DOOR_V)
            (mkt_door / ".claude-plugin").mkdir()
            (mkt_door / ".claude-plugin" / "marketplace.json").write_bytes(b"{}")
            dd, pj = _mk()
            try:
                with _DoorAt(door_in_mkt):
                    srv = RootServer(keys, wd.project_key(pj), pj, same, user=False, trusted=False)
                    code, msg, calls = _drive_root(pj, srv, same, avail=_avail(m_old, decoy=m_same))
                check("B-h 门在市场目录里：exit 2、出路点名门所在的市场目录、不给占位",
                      (code, f"（本命令就来自市场目录 {mkt_door}）" in msg, "例如本地框架仓" in msg),
                      (wd.EXIT_ENV, True, False))
            finally:
                rm(dd)
    finally:
        rm(d)


def test_stub_consistency():
    print("[stub] LayeredServer：hook 可见当且仅当从项目看的合成值是 true（与 config_read(cwd) 同一条规则）")
    for user in (True, False, None):
        for trusted in (True, False):
            for table in (True, False, None):
                s = LayeredServer(["session_start:0:0"], "key", user=user, trusted=trusted, table=table)
                vis = bool(s.hooks_list(["p"])["data"][0]["hooks"])
                merged = ((s.config_read(False, cwd="p")["config"].get("plugins") or {}).get(PID) or {}).get("enabled")
                check(f"桩自洽 user={user} trusted={trusted} table={table}：可见 == 合成值为 true", vis, merged is True)


def test_p4_same_place_norm():
    print("[upgrade] (a) 格落段后反推出的根与首轮所用的根是同一处、只是写法带扩展长度前缀 ⇒ 不多生成一轮")
    if os.name != "nt":      # 扩展长度前缀只是 Windows 的写法
        return
    keys = _manifest_keys()
    d = tmpdir()
    try:
        home = d / "codexhome"
        home.mkdir()
        door = _plugin_root_fixture(d / "door", version=DOOR_V)
        with _DoorAt(door), _EnvVar("CODEX_HOME", str(home)):
            dd, pj = _mk()
            try:
                srv = RootServer(keys, wd.project_key(pj), pj, "\\\\?\\" + str(door.resolve()), user=False, trusted=True)
                out = []
                code, msg, _c = _drive_root(pj, srv, None, upgrade=True, cap=out)
                check("落段后反推出的就是本命令自己的根（写法带前缀）：装完、[4/11] 只一次、不说「已按它重新生成」",
                      (code, sum(1 for ln in out if ln.startswith("[4/11]")), any("已按它重新生成" in ln for ln in out)),
                      (0, 1, False))
            finally:
                rm(dd)
    finally:
        rm(d)


def _agents_fixture(proj, agents, cc_only):
    """在项目里造 AGENTS.md 与 CC 专属面。`agents`：`"skeleton"`（真 scaffold 无参渲染）/ `"filled"`（同一份骨架
    把目标行换成正文）/ `None`（不建）。`cc_only`：True 时写一份 `.claude/rules/local/x.md`。"""
    import project_scaffold as ps
    if agents is not None:
        ps.ensure_agents_md(proj)
        if agents == "filled":
            f = proj / "AGENTS.md"
            f.write_bytes(f.read_bytes().replace(ps.AGENTS_MD_FALLBACK_GOAL.encode("utf-8"),
                                                 "一个虚构的演示项目".encode("utf-8"), 1))
    if cc_only:
        (proj / ".claude" / "rules" / "local").mkdir(parents=True, exist_ok=True)
        (proj / ".claude" / "rules" / "local" / "x.md").write_bytes(b"# x\n")


def _notes_marks(lines):
    """(确定性 `!` 行条数, 启发式 `i` 行条数)——只数 `agents_md_notes` 那两种行。"""
    bang = sum(1 for ln in lines if ln.startswith("! ") and "AGENTS.md" in ln)
    info = sum(1 for ln in lines if ln.startswith("i 只有 Claude Code 读得到的项目内容面："))
    return bang, info


def test_agents_md_notes():
    """门的成功出口附带的 AGENTS.md 提示（TASK-161）：两行各自独立（2×2）、各自异常隔离；逐个成功出口都说且只说
    一次，失败出口不说。判定式在 doctor，本组只钉门这一侧的接线。"""
    print("[agents] 成功出口的 AGENTS.md 提示：2×2 / 不存在 / 异常隔离 / 每个成功出口恰一次、失败不说")
    import workframe_doctor as doc
    # ① 2×2 ＋ 不存在：两行各自独立判
    for agents, cc_only, want in [("skeleton", False, (1, 0)), ("skeleton", True, (1, 1)),
                                  ("filled", True, (0, 1)), ("filled", False, (0, 0)),
                                  (None, False, (1, 0))]:
        d, proj = _mk()
        try:
            _agents_fixture(proj, agents, cc_only)
            lines = wd.agents_md_notes(proj)
            check(f"AGENTS.md {agents or '不存在'}、CC 专属面{'有' if cc_only else '无'}：(! 行, i 行)",
                  _notes_marks(lines), want)
            first = lines[0] if lines else ""
            if agents is None:
                check("AGENTS.md 不存在：! 行说的是「没有 AGENTS.md」，不是「仍是占位」",
                      ("没有 AGENTS.md" in first, "仍是框架补建时的占位" in first), (True, False))
                check("AGENTS.md 不存在：说「下一个会话启动时会补建」，不说「首个会话」（补建每个会话都查）",
                      ("下一个会话启动时会补建" in first, "首个会话" in first), (True, False))
            if want[0]:
                check(f"AGENTS.md {agents or '不存在'}：! 行写明「模型不代填」", doc.AGENTS_MD_NO_PROXY_EDIT in first, True)
        finally:
            rm(d)
    # ② 异常隔离：一行判不成只降成一行 i、另一行照判
    d, proj = _mk()
    try:
        _agents_fixture(proj, "skeleton", True)
        for target, want in (("agents_md_goal_of", (0, 1)), ("cc_only_surfaces", (1, 0))):
            saved = getattr(doc, target)
            setattr(doc, target, lambda *_a, **_k: (_ for _ in ()).throw(OSError("stub 读不出")))
            try:
                lines = wd.agents_md_notes(proj)
            except Exception as e:      # 隔离被改坏时异常外抛：记成一格红，不让整组崩掉
                lines = [f"<外抛 {type(e).__name__}>"]
            finally:
                setattr(doc, target, saved)
            check(f"{target} 抛异常：不外抛、另一行照判", _notes_marks(lines), want)
            check(f"{target} 抛异常：降成一行说明「没判成 / 没数成」的 i",
                  sum(1 for ln in lines if ln.startswith("i ") and ("没判成" in ln or "没数成" in ln)), 1)
    finally:
        rm(d)

    # ③ 每个成功出口恰一次、失败不说。门的主流程打桩成 no-op / 抛错，只看 main 的接线
    def run_main(argv, proj, stubs):
        out = []
        names = list(stubs) + ["say"]
        saved = {n: getattr(wd, n) for n in names if n != "find_codex_exe"}
        saved_find = wd.cas.find_codex_exe
        try:
            for n, v in stubs.items():
                if n == "find_codex_exe":
                    wd.cas.find_codex_exe = v
                else:
                    setattr(wd, n, v)
            wd.say = lambda msg="": out.append(str(msg))
            code = wd.main(argv + ["--project", str(proj)])
        finally:
            for n, v in saved.items():
                setattr(wd, n, v)
            wd.cas.find_codex_exe = saved_find
        return code, out

    def boom(*_a, **_k):
        raise wd.DoorError(wd.EXIT_COUNT, "stub 失败")

    ok = lambda *_a, **_k: None  # noqa: E731
    cells = [
        ("--codex 成功", ["--codex"], {"do_codex": ok}, (0, 1)),
        ("--codex 失败", ["--codex"], {"do_codex": boom}, (wd.EXIT_COUNT, 0)),
        ("--upgrade 成功", ["--upgrade"], {"do_codex": ok}, (0, 1)),
        ("--upgrade 失败", ["--upgrade"], {"do_codex": boom}, (wd.EXIT_COUNT, 0)),
        ("--check 通过", ["--check"], {"do_check": ok}, (0, 1)),
        ("--check 不过", ["--check"], {"do_check": boom}, (wd.EXIT_COUNT, 0)),
        # 缺省 --both：Codex 那半装上了，CC 那半有问题（没有 settings）⇒ 退出码 3 照旧，提示照说
        ("--both Codex 装上、CC 有问题", [], {"do_codex": ok, "find_codex_exe": lambda: "codex"}, (wd.EXIT_COUNT, 1)),
        ("--both 没有 codex", [], {"do_codex": ok, "find_codex_exe": lambda: None}, (wd.EXIT_COUNT, 0)),
        ("--both Codex 那半失败", [], {"do_codex": boom, "find_codex_exe": lambda: "codex"}, (wd.EXIT_COUNT, 0)),
        ("--backfill 结束时 Codex 门接着", ["--backfill"], {"do_backfill": lambda *_a: True}, (0, 1)),
        ("--backfill 结束时 Codex 门仍未接", ["--backfill"], {"do_backfill": lambda *_a: False}, (0, 0)),
        ("--cc（只核 CC 门）", ["--cc"], {}, (wd.EXIT_COUNT, 0)),
    ]
    for label, argv, stubs, want in cells:
        d, proj = _mk()
        try:
            _agents_fixture(proj, "skeleton", False)
            code, out = run_main(argv, proj, stubs)
            check(f"{label}：(退出码, ! 行条数)", (code, _notes_marks(out)[0]), want)
        finally:
            rm(d)

    # ④ 真 do_codex 走完（不打桩主流程）：提示恰一次——`do_codex` 里若也说了，这一格是 2
    keys = _manifest_keys()
    root = tmpdir()
    try:
        home = root / "codexhome"
        home.mkdir()
        same = _plugin_root_fixture(root / "same", version=DOOR_V)
        door = _plugin_root_fixture(root / "door", version=DOOR_V)
        with _DoorAt(door), _EnvVar("CODEX_HOME", str(home)):
            d, proj = _mk()
            try:
                _agents_fixture(proj, "skeleton", False)
                srv = RootServer(keys, wd.project_key(proj), proj, same, user=False, trusted=False)

                def fake_run(exe, args, cwd=None, timeout=180, env=None):
                    if args[:1] == ["--version"]:
                        return 0, "codex-cli 0.153.4", ""
                    if args[:3] == ["plugin", "marketplace", "add"]:
                        return 0, json.dumps({"marketplaceName": MKT}), ""
                    if args[:2] == ["plugin", "add"]:
                        srv.user = True
                        return 0, json.dumps({"version": "x", "installedPath": str(same)}), ""
                    return 1, "", "unexpected"

                saved_as = wd.cas.AppServer
                wd.cas.AppServer = _ctx(srv)
                try:
                    code, out = run_main(["--codex"], proj, {"run_codex": fake_run, "codex_exe": lambda: "codex"})
                finally:
                    wd.cas.AppServer = saved_as
                check("真 do_codex 走完：exit 0、「就绪」在、AGENTS.md 提示恰一次且在「就绪」之后",
                      (code, any(ln.startswith("Codex 门就绪") for ln in out), _notes_marks(out)[0],
                       [i for i, ln in enumerate(out) if ln.startswith("Codex 门就绪")]
                       < [i for i, ln in enumerate(out) if ln.startswith("! ") and "AGENTS.md" in ln]),
                      (0, True, 1, True))
            finally:
                rm(d)
    finally:
        rm(root)


def test_live_counter():
    """`--check --live` 验活表 session-start-prep 那一行（TASK-172）：会话前还没有 activity-state.json 时按「建出且为 1」判。
    判定式 `session_counter_ticked` 的矩阵 ＋ `check_live` 真用它（`subprocess.Popen` 换成只落侧效应的假进程）。"""
    print("[live] 验活：会话前没有计数的第一次跑不误报")
    for c0, c1, want in [(None, 1, True), (None, None, False), (None, 2, False), (None, 0, False),
                         (3, 4, True), (3, 3, False), (3, None, False), (0, 1, True)]:
        check(f"session_counter_ticked({c0!r}, {c1!r})", wd.session_counter_ticked(c0, c1), want)

    from _state_io import state_dir_of

    class FakePopen:
        """假 `codex exec`：按 `after` 落受信 hook 会落的侧效应，然后退出。"""
        after = None

        def __init__(self, argv, cwd=None, **_k):
            self.returncode = 1
            FakePopen.after(Path(cwd))

        def communicate(self, timeout=None):
            return b"", b"stub exit\n"

    def side_effects(counter):
        def run(proj):
            st = state_dir_of(proj)
            st.mkdir(parents=True, exist_ok=True)
            if counter is not None:
                (st / "activity-state.json").write_text(json.dumps({"session_counter": counter}), encoding="utf-8", newline="")
            with open(st / "inject-log.jsonl", "a", encoding="utf-8", newline="") as f:
                f.write(json.dumps({"scope": "main", "shard": "all", "harness": "codex"}) + "\n")
                f.write(json.dumps({"scope": "prompt", "harness": "codex"}) + "\n")
            with open(st / "events.jsonl", "a", encoding="utf-8", newline="") as f:
                f.write(json.dumps({"type": "session_ended", "harness": "codex"}) + "\n")
            (st / seed.LAST_FILE).write_text(json.dumps({"n": os.urandom(4).hex()}), encoding="utf-8", newline="")
        return run

    for label, before, counter, want in [("会话前没有 activity-state.json、会话后为 1", None, 1, None),
                                         ("会话前没有、会话后仍没有", None, None, wd.EXIT_LIVE),
                                         ("会话前 5、会话后 6", 5, 6, None),
                                         ("会话前 5、会话后仍 5", 5, 5, wd.EXIT_LIVE)]:
        d, proj = _mk()
        try:
            if before is not None:
                st = state_dir_of(proj)
                st.mkdir(parents=True, exist_ok=True)
                (st / "activity-state.json").write_text(json.dumps({"session_counter": before}), encoding="utf-8", newline="")
            FakePopen.after = side_effects(counter)
            saved = (wd.subprocess.Popen, wd.say)
            out = []
            wd.subprocess.Popen, wd.say = FakePopen, (lambda msg="": out.append(str(msg)))
            try:
                try:
                    wd.check_live("codex", proj)
                    err = None
                except wd.DoorError as e:
                    err = e
            finally:
                wd.subprocess.Popen, wd.say = saved
            check(f"check_live {label}：退出码", _code(err), want)
            check(f"check_live {label}：session-start-prep 那一行判{'过' if want is None else '不过'}",
                  any(ln.strip().startswith("ok") and "session-start-prep.py" in ln for ln in out), want is None)
        finally:
            rm(d)


def _seal_state():
    """本进程此刻是否仍封着：`WF_CODEX_EXE` 指向本套件临时目录下一个不存在的路径、`CODEX_HOME` 在本套件临时目录下。"""
    exe, home = os.environ.get("WF_CODEX_EXE") or "", os.environ.get("CODEX_HOME") or ""
    return (bool(exe) and Path(exe).is_relative_to(_SEAL) and not Path(exe).exists(),
            bool(home) and Path(home).is_relative_to(_SEAL))


def test_codex_seal(when):
    print(f"[seal] 套件封闭（{when}）：PATH 上有 codex 也解析不到；CODEX_HOME 是本进程的临时目录")
    check(f"封闭（{when}）：WF_CODEX_EXE 指向本套件临时目录下不存在的路径、CODEX_HOME 在本套件临时目录下",
          _seal_state(), (True, True))
    # 封闭的另一条腿是 `find_codex_exe` 本身：套件里有驱动把它换成替身、靠手写的恢复元组还原——漏了复原，只看环境变量
    # 看不出来。所以两次都核它仍是原函数，并照样做一遍 PATH 解析探测（替身不收参数时调用会抛，按「解析结果不对」报红）
    check(f"封闭（{when}）：find_codex_exe 仍是被测模块自己的函数（没有哪一格打了桩没复原）",
          cas.find_codex_exe is _FIND_CODEX_EXE, True)
    check(f"封闭（{when}）：run_codex 仍是被测模块自己的函数（没有哪一格打了桩没复原）", wd.run_codex is _RUN_CODEX, True)

    def resolve(env):
        try:
            return cas.find_codex_exe(env)
        except Exception as e:  # noqa: BLE001
            return f"<{type(e).__name__}>"

    tag = "" if when == "开跑前" else f"（{when}）"
    d = tmpdir()
    try:
        fake = d / ("codex.exe" if os.name == "nt" else "codex")
        fake.write_bytes(b"")
        if os.name != "nt":
            fake.chmod(0o755)
        env = dict(os.environ, PATH=str(d))
        bare = {k: v for k, v in env.items() if k != "WF_CODEX_EXE"}
        found = resolve(bare)
        check(f"封闭对照{tag}：不设 WF_CODEX_EXE 时，PATH 上那份假 codex 解析得到（下一格的前提）",
              isinstance(found, str) and not found.startswith("<") and Path(found).name.lower() == fake.name.lower(), True)
        check(f"封闭{tag}：WF_CODEX_EXE 指向不存在的路径 ⇒ 解析不到，不回退到 PATH 上那份", resolve(env), None)
    finally:
        rm(d)


# ---- 6. 账本口径与行尾：git 检出成 CRLF ----

EOL_V = "0.0.1"     # 夹具生成时写的版本：与门自己的插件版本不同，「升级」那一格才真有内容要写


def _git_env(home):
    """git 子进程的隔离环境。只在命令行写 `-c` 挡不住两样东西：system / global 的配置与 attributes。全局 attributes
    （`$XDG_CONFIG_HOME/git/attributes`、`~/.config/git/attributes`）写了 `* -text`、`* text eol=lf` 或 `binary` 时，
    attribute 优先于 `core.autocrlf`，clone 出来仍是 LF——修复前的代码照样通过，这组测试在那台机器上恒绿。继承下来的
    `GIT_*`（在 git hook 里跑时有 `GIT_DIR` / `GIT_INDEX_FILE`）一并去掉。只改传给子进程的 env，不改本进程的环境。"""
    home = Path(home)
    (home / "xdg").mkdir(parents=True, exist_ok=True)
    empty = home / "empty.gitconfig"
    empty.write_bytes(b"")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(empty), GIT_ATTR_NOSYSTEM="1",
               HOME=str(home), XDG_CONFIG_HOME=str(home / "xdg"))
    return env


def _git(env, *args):
    r = subprocess.run(["git", *args], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:4])} … rc={r.returncode}: {r.stderr.strip()[:200]}")
    return r.stdout


def _rm_git(d):
    """git 的对象文件是只读的，Windows 上 `rmtree` 删到它们就失败——先去掉只读再删。"""
    def fix(func, path, *_):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(d, onexc=fix)
    else:
        shutil.rmtree(d, onerror=fix)


def _tree(root):
    """项目树的 路径 → 字节，不含 `.git/`（`git status` 会刷新索引）。"""
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts}


def _changed(before, after):
    """字节变了的文件（含新增、消失）。判「写没写」一律看它与动作表，不看账本的 `written_at`——同一秒内跑完它不变。"""
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def _acts(report, action):
    return sorted(rel for rel, a, _n in report if a == action)


def _acts_of(report, rels):
    """report 里 rels 这几份各自的动作 → [(rel, 动作)]。角色文件与 config.toml 分开断言：两处「无操作」判定
    各自回归时，红的是不同的格。"""
    return sorted((rel, a) for rel, a, _n in report if rel in rels)


def _gen(proj, version):
    """跑一次生成器，返回 report。生成器抛错（被测判定回归成「生成前拒绝」）时返回一条 `<raised>` 记录，
    让红落在这一格的断言上、后面各格照跑——不让整个套件崩在这一行。"""
    try:
        return cr.generate(proj, PLUGIN, version, marketplace=MKT)[0]
    except cr.RoleError as e:
        return [("<raised>", type(e).__name__, str(e)[:120])]


def _named(problems):
    """verify 的问题 → [(被点名的文件, 是不是「被手改」)]。"""
    return sorted((p.split(":", 1)[0], "被手改" in p) for p in problems)


def _eol_origin(d, env):
    """机器 A：`.codex/config.toml` 段前、段后各有一处用户自写的内容；生成（LF）后以 `core.autocrlf=false` 提交。
    提交身份、签名、默认分支都显式给——CI runner 没有 `user.name` 时 `git commit` 直接失败，开了签名的机器会去调签名。"""
    a = d / "A"
    cfg = a / cr.CONFIG_REL
    cfg.parent.mkdir(parents=True)
    (a / ".workframe-config.json").write_bytes(b'{"project_name":"t"}\n')
    cfg.write_bytes(b'model = "gpt-5.5"\n')
    cr.generate(a, PLUGIN, EOL_V, marketplace=MKT)
    cfg.write_bytes(cfg.read_bytes() + b"\n[features]\nmulti_agent = true\n")
    _git(env, "-c", "init.defaultBranch=main", "init", "-q", str(a))
    _git(env, "-C", str(a), "-c", "core.autocrlf=false", "add", "-A")
    _git(env, "-C", str(a), "-c", "core.autocrlf=false", "-c", "user.name=wf-test",
         "-c", "user.email=wf-test@example.invalid", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "fixture")
    return a


def _eol_clone(a, env, name):
    """机器 B：`clone --config core.autocrlf=true`（写进副本自己的配置，检出与之后的 `git status` 都按它）。
    **前提断言**：账本登记的每个文件与账本自己都确有 CRLF、`git status` 为空。不成立时按红报、不跳过——观测面静默
    失效时，「修复前红、修复后绿」的格会一起变绿，与修好了同形。返回 (副本, 前提是否成立)。"""
    b = a.parent / name
    _git(env, "clone", "-q", "--config", "core.autocrlf=true", str(a), str(b))
    rels = sorted(cr.load_ledger(b)["files"]) + [cr.LEDGER_REL.as_posix()]
    crlf = [rel for rel in rels if b"\r\n" in (b / rel).read_bytes()]
    ok = (len(rels), crlf, _git(env, "-C", str(b), "status", "--porcelain")) == \
        (len(cr.load_agents(PLUGIN / "agents")) + 2, rels, "")
    check(f"[{name}] 夹具前提：clone 出来的角色文件、config.toml、账本都含 CRLF，git status 为空"
          f"（不成立时本格其余断言无意义，按红报）", ok, True)
    return b, ok


def test_eol_checkout():
    print("[eol] 账本口径：git 检出成 CRLF 不算被手改、一个字节不写；真改照样抓；四个放宽方向仍判被手改")
    roles = [r for r, _d, _b in cr.load_agents(PLUGIN / "agents")]
    role_rel = {r: (cr.ROLES_DIR_REL / f"{r}.toml").as_posix() for r in roles}
    cfg_rel, led_rel = cr.CONFIG_REL.as_posix(), cr.LEDGER_REL.as_posix()
    role_rels = sorted(role_rel.values())
    every = sorted(role_rels + [cfg_rel, led_rel])
    d = tmpdir()
    try:
        env = _git_env(d / "home")
        try:
            a = _eol_origin(d, env)
        except (OSError, RuntimeError, cr.RoleError) as e:
            check("git 夹具建得起来（git 不在或失败时按红报，不跳过）", f"{type(e).__name__}: {e}", "ok")
            return
        # (a) 只差行尾：预检、段状态、verify（--check）、重跑都不报，一个字节不写
        b, ok = _eol_clone(a, env, "B-same")
        if ok:
            before = _tree(b)
            try:
                cr.preflight(b, PLUGIN, EOL_V, MKT)
                pre = "pass"
            except cr.RoleError as e:
                pre = f"{type(e).__name__}: {e}"
            check("CRLF 检出：预检通过", pre, "pass")
            check("CRLF 检出：managed 段状态 pristine", cr.managed_block_state(b), cr.BLOCK_PRISTINE)
            check("CRLF 检出：verify（--check）零问题", cr.verify(b), [])
            report = _gen(b, EOL_V)
            check("CRLF 检出重跑：角色文件全是 unchanged（不跳过、不写）",
                  _acts_of(report, role_rels), [(r, "unchanged") for r in role_rels])
            check("CRLF 检出重跑：config.toml 是 unchanged（不重写段）", _acts_of(report, [cfg_rel]), [(cfg_rel, "unchanged")])
            check("CRLF 检出重跑：项目树一个字节不变（含 config.toml 与账本）", _changed(before, _tree(b)), [])
            check("CRLF 检出重跑：git status 仍为空", _git(env, "-C", str(b), "status", "--porcelain"), "")
        # (b) 真改一份：只点名那一份
        b, ok = _eol_clone(a, env, "B-edit")
        if ok:
            victim = role_rel[roles[0]]
            (b / victim).write_bytes((b / victim).read_bytes() + b"# my real edit\r\n")
            before = _tree(b)
            check("CRLF 检出＋真改一份：verify 只点名那一份", _named(cr.verify(b)), [(victim, True)])
            report = _gen(b, EOL_V)
            check("CRLF 检出＋真改一份：角色文件里只跳过那一份、其余不写",
                  _acts_of(report, role_rels), sorted((r, "skipped" if r == victim else "unchanged") for r in role_rels))
            check("CRLF 检出＋真改一份：config.toml 是 unchanged", _acts_of(report, [cfg_rel]), [(cfg_rel, "unchanged")])
            check("CRLF 检出＋真改一份：项目树一个字节不变（被改那份原样保留）", _changed(before, _tree(b)), [])
        # (c) 段内只改一行：预检拒绝，文案恰好点名那一行——`_extract_block` 的行尾归一在这里承重
        b, ok = _eol_clone(a, env, "B-block")
        if ok:
            cfg = b / cr.CONFIG_REL
            lines = cfg.read_bytes().split(b"\r\n")
            i = next(k for k, ln in enumerate(lines) if ln.startswith(b"description = "))
            assert lines.count(lines[i]) == 1, "改的那一行在文件里须唯一"
            lines[i] = b'description = "mine"'
            cfg.write_bytes(b"\r\n".join(lines))
            before = _tree(b)
            check("CRLF 检出＋段内改一行：段状态 edited", cr.managed_block_state(b), cr.BLOCK_EDITED)
            try:
                cr.preflight(b, PLUGIN, EOL_V, MKT)
                named = "no error"
            except cr.PreflightError as e:
                s = str(e)
                named = s.split("不同的行：", 1)[1].split("）——", 1)[0] if "不同的行：" in s else f"<{e.kind}> {s[:80]}"
            check("CRLF 检出＋段内改一行：预检拒绝，文案恰好点名改了的那一行（不把整段没改的行列出来）",
                  named, '`description = "mine"`')
            check("CRLF 检出＋段内改一行：拒绝后项目树一个字节不变", _changed(before, _tree(b)), [])
        # (d) 生产路径：CRLF 检出上升级，经门的 step_roles（--codex / --upgrade 生成角色那一步）
        b, ok = _eol_clone(a, env, "B-upgrade")
        if ok:
            new_v = wd.installed_version(wd.PLUGIN_ROOT)
            check("升级格前提：门自己的插件版本读得出、且与夹具版本不同", new_v not in ("?", EOL_V), True)
            said, saved = [], wd.say
            wd.say = lambda msg="": said.append(msg)
            try:
                for rnd in (1, 2):
                    before, wrote = _tree(b), []
                    try:
                        wd.step_roles(b, None, MKT, wrote)
                        err = None
                    except wd.DoorError as e:
                        err = e
                    changed = _changed(before, _tree(b))
                    if rnd == 1:
                        check("CRLF 检出上升级：对账过；全部生成物＋账本都写了；门报的「写了哪些」与字节一致",
                              (_code(err), sorted(wrote), changed), (None, every, every))
                        check("CRLF 检出上升级：之后 verify 零问题", cr.verify(b), [])
                        raw = (b / cr.CONFIG_REL).read_bytes()
                        check("重跑前提：升级后 config.toml 是混合行尾（段外 CRLF、段内 LF）",
                              (b"\r\n" in raw, raw.count(b"\n") > raw.count(b"\r\n")), (True, True))
                    else:
                        check("升级后重跑：门报零写入、项目树一个字节不变", (_code(err), wrote, changed), (None, [], []))
            finally:
                wd.say = saved
        # (e) 生产路径：第二台机器上改 `.local.toml`（CRLF 写的）——只写被影响的那一份
        b, ok = _eol_clone(a, env, "B-local")
        if ok:
            who = roles[-1]
            (b / cr.ROLES_DIR_REL / f"{who}.local.toml").write_bytes(b'developer_instructions = "LOCAL_EXTRA_MARK"\r\n')
            before = _tree(b)
            report = _gen(b, EOL_V)
            check("CRLF 检出上改 .local.toml：角色文件里只写那一份，其余 unchanged、不跳过",
                  _acts_of(report, role_rels), sorted((r, "written" if r == role_rel[who] else "unchanged") for r in role_rels))
            check("CRLF 检出上改 .local.toml：config.toml 是 unchanged", _acts_of(report, [cfg_rel]), [(cfg_rel, "unchanged")])
            check("CRLF 检出上改 .local.toml：字节变了的恰是那一份＋账本",
                  _changed(before, _tree(b)), sorted([role_rel[who], led_rel]))
            check("CRLF 检出上改 .local.toml：之后 verify 零问题", cr.verify(b), [])
            before = _tree(b)
            report = _gen(b, EOL_V)
            check("CRLF 检出上改 .local.toml 后重跑：全 unchanged、项目树一个字节不变",
                  (sorted({act for _r, act, _n in report}), _changed(before, _tree(b))), (["unchanged"], []))
        # (f) CRLF 检出上用户已关（段首启用表 enabled = false）：照样认作「已关」、不判成被手改；带行尾注释的写法
        #     照样读出那一行、给提示。段状态、启用值、带注释那一行三处都经 `read_text` 读段；只有预检的提示读原始字节
        b, ok = _eol_clone(a, env, "B-closed")
        if ok:
            cfg = b / cr.CONFIG_REL
            raw, on = cfg.read_bytes(), b"\r\nenabled = true\r\n"
            assert raw.count(on) == 1, "段首启用表那一行须唯一、且是 CRLF"
            cfg.write_bytes(raw.replace(on, b"\r\nenabled = false\r\n"))
            check("CRLF 检出＋用户已关：段状态 user-closed（不判成被手改）", cr.managed_block_state(b), cr.BLOCK_USER_CLOSED)
            check("CRLF 检出＋用户已关：段首启用表读出 false", cr.block_enable_value(b, cr.plugin_id_for(MKT)), False)
            try:
                cr.preflight(b, PLUGIN, EOL_V, MKT)
                kind = "no error"
            except cr.PreflightError as e:
                kind = e.kind
            check("CRLF 检出＋用户已关：预检按「已关」拒绝", kind, cr.KIND_USER_CLOSED)
            check("CRLF 检出＋用户已关：verify 零问题（已关不算问题）", cr.verify(b), [])
            closed = "enabled = false  # 先关掉"
            cfg.write_bytes(raw.replace(on, ("\r\n" + closed + "\r\n").encode("utf-8")))
            check("CRLF 检出＋带注释的 enabled = false：读出的恰是那一行", cr.commented_close_line(b), closed)
            try:
                cr.preflight(b, PLUGIN, EOL_V, MKT)
                hint = "no error"
            except cr.PreflightError as e:
                hint = (e.kind, "只改值、不加注释" in str(e))
            check("CRLF 检出＋带注释的 enabled = false：预检判被手改，文案带「只改值、不加注释」的提示",
                  hint, (cr.KIND_BLOCK_EDITED, True))
    finally:
        _rm_git(d)
    _test_eol_bytes(roles, role_rel, led_rel)


def _test_eol_bytes(roles, role_rel, led_rel):
    """git 造不出的形态，用字节构造（LF 夹具上直接改）。"""
    print("[eol] 字节构造：混合行尾容忍；四个放宽方向仍判被手改；值里带 CRLF 时记账与比对同口径")
    # 隔行混合行尾：容忍、零写入
    dd, proj = _mk(gen=True)
    try:
        rels = sorted(cr.load_ledger(proj)["files"]) + [led_rel]
        for rel in rels:
            ls = (proj / rel).read_bytes().split(b"\n")
            (proj / rel).write_bytes(b"".join(ln + (b"\r\n" if k % 2 == 0 else b"\n") for k, ln in enumerate(ls[:-1])) + ls[-1])
        raws = [(proj / rel).read_bytes() for rel in rels]
        check("混合行尾前提：每份都同时有 CRLF 与裸 LF", all(b"\r\n" in r and r.count(b"\n") > r.count(b"\r\n") for r in raws), True)
        before = _tree(proj)
        check("隔行混合行尾：verify 零问题、段状态 pristine", (cr.verify(proj), cr.managed_block_state(proj)), ([], cr.BLOCK_PRISTINE))
        report = _gen(proj, "1.1.0")
        roles_ = sorted(role_rel.values())
        check("隔行混合行尾：重跑时角色文件全是 unchanged", _acts_of(report, roles_), [(r, "unchanged") for r in roles_])
        check("隔行混合行尾：重跑时 config.toml 是 unchanged",
              _acts_of(report, [cr.CONFIG_REL.as_posix()]), [(cr.CONFIG_REL.as_posix(), "unchanged")])
        check("隔行混合行尾：重跑后项目树一个字节不变", _changed(before, _tree(proj)), [])
    finally:
        rm(dd)
    # 四个放宽方向：各自仍判被手改，只点名被改那一份、不覆盖。每个方向对应一种把归一写宽的写法——
    # 删掉全部 `\r` / 去 BOM / 忽略末尾换行 / 每行去尾空白——写宽了哪一种，恰好是它那一格变绿
    def lone_cr(b):
        m = b.index(b"DO NOT") + 2          # 首行的 ASCII 段里：不落在多字节字符中间
        assert b[m - 1:m] not in (b"\r", b"\n") and b[m:m + 1] not in (b"\r", b"\n"), "孤立 CR 的两侧都不能是行尾"
        return b[:m] + b"\r" + b[m:]

    def no_final_newline(b):
        assert b.endswith(b"\n"), "生成物应以换行结尾"
        return b[:-1]

    def trailing_space(b):
        i = b.index(b"\n")
        return b[:i] + b" " + b[i:]

    victim = role_rel[roles[0]]
    for label, tamper in [("行中间插一个孤立 CR", lone_cr), ("文件头加 BOM", lambda b: b"\xef\xbb\xbf" + b),
                          ("删掉末尾换行", no_final_newline), ("首行行尾加一个空格", trailing_space)]:
        dd, proj = _mk(gen=True)
        try:
            p = proj / victim
            orig = p.read_bytes()
            new = tamper(orig)
            assert new != orig, label
            p.write_bytes(new)
            before = _tree(proj)
            check(f"{label}：verify 仍判被手改、只点名那一份", _named(cr.verify(proj)), [(victim, True)])
            report = _gen(proj, "1.1.0")
            check(f"{label}：重跑只跳过那一份、不写", (_acts(report, "skipped"), _acts(report, "written")), ([victim], []))
            check(f"{label}：项目树一个字节不变", _changed(before, _tree(proj)), [])
        finally:
            rm(dd)
    # 值里带 CRLF（`.local.toml` 用基本字符串写 `\r\n` 转义，唯一能让记账内容带 CR 的来路）：记账与比对同口径
    dd, proj = _mk(gen=True)
    try:
        who = roles[1]
        (proj / cr.ROLES_DIR_REL / f"{who}.local.toml").write_bytes(b'developer_instructions = "x\\r\\ny"\n')
        report = _gen(proj, "1.1.0")
        check("值里带 CRLF：前提——生成的角色文件内容里确有 CRLF，且这一份被写",
              (b"x\r\ny" in (proj / role_rel[who]).read_bytes(), _acts(report, "written")), (True, [role_rel[who]]))
        check("值里带 CRLF：生成后 verify 零问题（记账与比对同口径）", cr.verify(proj), [])
        before = _tree(proj)
        report = _gen(proj, "1.1.0")
        check("值里带 CRLF：重跑全 unchanged、项目树一个字节不变",
              (sorted({act for _r, act, _n in report}), _changed(before, _tree(proj))), (["unchanged"], []))
    finally:
        rm(dd)
    # 内容不含 CR 时，账本口径与直接对原始字节算 sha256 相同——只证这个范围：按原始字节记下的账，
    # 记账内容不含 CR 的照旧有效；内容带 CR 的（上一格那种）不在此列
    dd = tmpdir()
    try:
        rendered, _ = cr.generate(dd, PLUGIN, "1.1.0", write=False, marketplace=MKT)
        blobs = [c.encode("utf-8") for c, _k in rendered.values()]
        check("不含 CR 的内容：账本口径 == 原始字节 sha256（前提：渲染结果确实不含 CR）",
              [(b"\r" in c, cr.sha256_bytes(c) == hashlib.sha256(c).hexdigest()) for c in blobs],
              [(False, True)] * len(blobs))
    finally:
        rm(dd)


def main():
    test_codex_seal("开跑前")
    test_roles()
    test_door()
    test_seed()
    test_preflight()
    test_eol_checkout()
    test_do_codex_order()
    test_enable_layers()
    test_marketplace_add_name_taken()
    test_upgrade_source_routing()
    test_backfill()
    test_locate_market()
    test_plugin_version()
    test_version_mismatch()
    test_manifest_shapes()
    test_guard_hooks_list()
    test_guard_do_codex()
    test_guard_check_static()
    test_wrote_truthful()
    test_hook_source_guard()
    test_fix_round_wording()
    test_upgrade_door_layouts()
    test_preinstall_check()
    test_stub_consistency()
    test_p4_same_place_norm()
    test_agents_md_notes()
    test_live_counter()
    test_codex_seal("跑完")     # 中途有用例改了环境变量没复原，后面的用例就不再封闭——在这里说出来
    if FAILURES:
        print(f"\n{len(FAILURES)} 条失败: {FAILURES}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
