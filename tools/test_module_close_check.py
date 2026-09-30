#!/usr/bin/env python3
"""
单元测试 — plugins/core/scripts/module_close_check.py 九项检查 + 升级计数 + 标记命令 + 关键回归。

覆盖（每个判定分支用它自己的失败方式打一遍——「从没真正执行过的检查」与通过
长得一模一样）：
  - 检查 1 stale：非空报 error / 清除后 ok；clear_stale --reason 的 stale_cleared
    事件留痕（含 reason 字段、条目不存在不写事件）
  - 检查 2 literal 存在性：缺失文件报 error / 非法形态（`..`）报 error / 全存在 ok
  - 检查 3 同值性：三落点不同值报 error / 同值（含 8 位 vs 12 位前缀容差）不报
  - 检查 4 刷新：基准后有命中提交报漏刷 error / 刷平后 ok / 死引用 error /
    工作树命中改动 error / branch: 可变引用 warn 跳过
  - 检查 5 仓状态：嵌套仓脏报 warn（不 error——无关 WIP 不卡收口）
  - 检查 6 覆盖率对账：缺/多两种失败文案分开 / 尾斜杠与非法形态 / 字段缺失 ≠ 空数组 /
    块状列表 / exempt 与拼错 / 未同步过 warn / 外部 source_repo 跳过 / 对账面落空 /
    单仓项目 / 非 git 仓不假红
  - `[ledger]` 账本：无配置跳过 / 日期过老 error / 缺字段 error / 今天日期+字段齐 ok
  - `[yaml-parse]` YAML 可解析性：占位符残留 / C0 控制字符 / 标量提前终止 /
    frontmatter BOM 失明 / 显式 config 豁免 / 合法文件不误伤。**本组需要 PyYAML**
    （`pip install pyyaml==6.0.2`，CI 装的就是这一版）；没装时整组由前置守卫拦下并
    报一条点名环境的失败——是报红不是跳过，「没跑」不等于「通过」
  - 回归：_load_csm 同进程多次调用不炸（首跑实测双重包流炸过）+ 换 project_dir
    后 csm 路径常量正确重定向
  - run_all：完整 fixture 结构完整；非 modules 项目检查 1-6 整体跳过；连调多次零写入
  - `[discipline-trace]`：判据 a 只出 info / b 红（无当日账本段，exit 1、运行行
    exit_code 1、不进计数）/ c 红（看板未更新，warn、exit 0、挂起）/ b 与 c 各自独立会红 /
    任意红组合永不产 error / 前提：窗口首日早于此刻的提交判适用、窗口前的提交判不适用、
    判不了的仓记 unknown
  - `[discipline-trace]` 会放行或假绿的分支：前提看工作树改动 / c 排除 cancelled / a 只数窗口内 /
    同仓多子模块只算真被改的那个 / 窗口起点本身 = 本地昨天 00:00（独立计算，不取被测函数的返回值）/
    b、c 都不适用时项级不适用 / 看板不存在时 c 不适用 / 第九项自身异常时写明「本次未计入计数」
  - 升级计数（纯函数，合成日志）：10 个绿身份跨 7 个本地日才建议 / 只跨 6 天不建议 /
    有待确认的红压住建议 / 假红清零（含标在非计入运行上）/ 确实漏做 +1 / 不适用不计入不清零 /
    同一身份合并取最后一次 / exit 1 不计入 / 标记只挂在它之前同指纹的最后一次运行上、不套到之后
    同指纹的另一次红；并经 `main()` 端到端走到「建议打印」与「清零」
  - 日志写入返回 False（spill 行照样计数）/ None（warn、未计入、退出码不变）
  - 标记命令（经 bin 入口实跑）：前缀歧义 / 前缀过短 / 零命中 / 非红目标 /
    `false-positive --by self` / 取值域外 → exit 2 且日志字节不变；合法标记 exit 0 追加一行

执行：
  python tools/test_module_close_check.py
退出 0 = 全部通过；非 0 = 有失败用例。
"""

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

# 本文件刻意不包 UTF-8 流（在 `check_utf8_stream_wrap_symmetric` 豁免白名单里）：
# 加载链 module_close_check.py → check-stale-modules.py，包装在最深那层完成。

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "plugins" / "core" / "scripts" / "module_close_check.py"


def _state_rel():
    """夹具的状态目录（项目根相对）——由 `_state_io` 现算，不在本文件写死目录名。框架只读写新位置，
    夹具建旧布局目录的话被测脚本读的是另一棵树（事件文件「不存在」、计数日志读不到）。"""
    spec = importlib.util.spec_from_file_location("_state_io", SCRIPT_PATH.parent / "_state_io.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return Path(*mod.new_rel("state").split("/"))


STATE_REL = _state_rel()


def _load_mcc():
    spec = importlib.util.spec_from_file_location("module_close_check", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t.local", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace")


def _short_head(repo):
    return _git(repo, "rev-parse", "--short=8", "HEAD").stdout.strip()


def _findings(result_list):
    return [(lv, msg) for lv, msg in result_list]


def _has(findings, level, keyword):
    return any(lv == level and keyword in msg for lv, msg in findings)


def make_fixture(tmp, ref_map=None, source_repo="fw", overview_tail=""):
    """建一个含嵌套 git 仓的最小 modules 项目。返回嵌套仓首个 commit 短 hash。"""
    tmp = Path(tmp)
    fw = tmp / "fw"
    (fw / "src").mkdir(parents=True)
    (fw / "src" / "app.py").write_text("v1\n", encoding="utf-8", newline="\n")
    _git(tmp, "init", "fw")
    _git(fw, "add", "-A")
    _git(fw, "commit", "-m", "c1")
    h1 = _short_head(fw)
    refs = ref_map or {"code_map": h1, "submodule": h1, "overview": h1}
    sub = tmp / "projects" / "modules" / "basicA" / "subA"
    (sub / "current-state").mkdir(parents=True)
    (sub / "submodule.yaml").write_text(
        "module: basicA/subA\n"
        "code_paths:\n"
        "  - fw/src/app.py\n"
        '  - "fw/src/**"      # 注释\n'
        f'last_synced_ref: "{refs["submodule"]}"   # 基准\n',
        encoding="utf-8", newline="\n")
    (sub / "current-state" / "code-map.md").write_text(
        "---\ntype: current-state\n"
        f'source_repo: "{source_repo}"\n'
        f'source_ref: "{refs["code_map"]}"   # 基准\n'
        # 仓相对，与 code_paths 在 source_repo 那个仓内的部分同指一组文件（检查 6）
        'source_paths: ["src/app.py", "src/**"]\n'
        "---\n\n# code map\n", encoding="utf-8", newline="\n")
    (sub / "overview.md").write_text(
        "# subA\n\n"
        # overview_tail 默认空 = 新写形态；传入时模拟**存量形态**（括注里 ref 后跟着
        # 人写现状标注）。真实项目 12/12 都是带尾巴的那种，此前 fixture 一条都没覆盖。
        f"- **最近同步**：2026-01-01（基准 commit {refs['overview']}{overview_tail}）\n",
        encoding="utf-8", newline="\n")
    (tmp / STATE_REL).mkdir(parents=True)
    return h1


# ---------- 用例 ----------


def test_load_csm_regression(mcc):
    """回归：同进程多次 _load_csm 不炸 + 换 project_dir 后路径常量重定向。"""
    failures = []
    with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
        make_fixture(t1)
        make_fixture(t2)
        csm1 = mcc._load_csm(t1)
        csm2 = mcc._load_csm(t2)  # 首跑实测：这里第二次 exec 会双重包流当场炸
        if csm1 is not csm2:
            failures.append("_load_csm 未复用缓存模块（二次 exec 有双重包流风险）")
        if Path(csm2.STALE_FILE).parts[:len(Path(t2).resolve().parts)] != Path(t2).resolve().parts:
            failures.append(f"换 project_dir 后 STALE_FILE 未重定向: {csm2.STALE_FILE}")
        print("test stdout 存活探针 ✓")  # 双重包流的典型死法是流被关掉——真打印一下
    return failures


def test_stale_and_reason_event(mcc):
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        csm = mcc._load_csm(tmp)
        subs = mcc._collect_submodules(Path(tmp), csm)
        f = _findings(mcc.check_stale_clean(Path(tmp), csm, subs))
        if not _has(f, "ok", "为空"):
            failures.append(f"stale 空应 ok: {f}")
        csm.mark_stale("basicA/subA", "code_path_hit:fw/src/app.py")
        f = _findings(mcc.check_stale_clean(Path(tmp), csm, subs))
        if not _has(f, "error", "basicA/subA"):
            failures.append(f"stale 非空应 error: {f}")
        csm.clear_stale("basicA/subA", reason="已更新 code-map")
        f = _findings(mcc.check_stale_clean(Path(tmp), csm, subs))
        if not _has(f, "ok", "为空"):
            failures.append(f"清除后应 ok: {f}")
        ev_file = Path(tmp) / STATE_REL / "events.jsonl"
        evs = [json.loads(x) for x in ev_file.read_text(encoding="utf-8").splitlines()
               if '"stale_cleared"' in x] if ev_file.exists() else []
        if not evs or evs[-1].get("reason") != "已更新 code-map":
            failures.append(f"clear_stale --reason 未留 stale_cleared 事件: {evs}")
        n = len(evs)
        csm.clear_stale("basicA/subA")  # 条目已不存在：不得再写事件
        evs2 = [x for x in ev_file.read_text(encoding="utf-8").splitlines()
                if '"stale_cleared"' in x]
        if len(evs2) != n:
            failures.append("条目不存在时 clear_stale 不应写 stale_cleared 事件")
    return failures


def test_code_paths_health(mcc):
    """检查 2：literal 存在性 + glob 可展开 + 形态合法（每条判定分支各一组）。

    非法形态的 literal / glob 两版都要打：此前非法判定写在「含通配符就 continue」
    之后，非法 glob 从检查 2 溜走、检查 4 也跳过，两边都不报。
    """
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        fw = Path(tmp) / "fw"
        (fw / "solo").mkdir()                       # D6：恰好 1 个文件
        (fw / "solo" / "only.md").write_text("x\n", encoding="utf-8", newline="\n")
        (fw / "empty").mkdir()                      # D7：目录在，无任何文件
        (fw / "ignored").mkdir()                    # D8：只有被 gitignore 的文件
        (fw / "ignored" / "junk.pyc").write_text("x\n", encoding="utf-8", newline="\n")
        (fw / ".gitignore").write_text("*.pyc\n", encoding="utf-8", newline="\n")
        _git(fw, "add", "-A")
        _git(fw, "commit", "-m", "code-paths fixture")

        csm = mcc._load_csm(tmp)

        def run(paths):
            subs = mcc._collect_submodules(Path(tmp), csm)
            subs[0]["code_paths"] = list(paths)
            return _findings(mcc.check_code_paths_health(Path(tmp), csm, subs))

        cases = [
            # (标签, code_paths, 期望等级, 期望文案关键词)
            ("N1 基线：literal+glob 都在", ["fw/src/app.py", "fw/src/**"], "ok", "可展开"),
            ("D1 glob 指向不存在的目录", ["fw/src/app.py", "fw/gone/**"], "error", "不存在的目录"),
            ("D2 非法 glob：盘符绝对路径", ["fw/src/app.py", "C:/somewhere/**"], "error", "非法形态"),
            ("D3 非法 glob：父级逃逸", ["fw/src/app.py", "../outside/**"], "error", "非法形态"),
            ("D4 literal 不存在", ["fw/src/gone.py"], "error", "gone.py"),
            ("D5 非法 literal：父级逃逸", ["../outside.py"], "error", "非法形态"),
            ("D6 glob 恰展开 1 个文件", ["fw/solo/**"], "ok", "可展开"),
            ("D7 glob 目录在但无文件", ["fw/empty/**"], "error", "展开为空"),
            ("D8 glob 只剩被 ignore 的文件", ["fw/ignored/**"], "error", "展开为空"),
        ]
        for label, paths, level, kw in cases:
            f = run(paths)
            if not _has(f, level, kw):
                failures.append(f"{label} 应 {level}/{kw}: {f}")

        # D9 根仓不是 git 仓时，指向根仓的 glob 退出判定但必须说出来（info，非静默）
        f = run(["fw/src/app.py", "topdir/**"])
        if not _has(f, "info", "不是 git 仓"):
            failures.append(f"D9 根仓非 git 仓应 info 点名: {f}")
        if _has(f, "error", "展开为空"):
            failures.append(f"D9 不该把「取不到展开面」判成「展开为空」（假红）: {f}")
    return failures


def test_ref_consistency(mcc):
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        csm = mcc._load_csm(tmp)
        subs = mcc._collect_submodules(Path(tmp), csm)
        f = _findings(mcc.check_ref_consistency(Path(tmp), csm, subs))
        if not _has(f, "ok", "同值"):
            failures.append(f"三落点同值应 ok: {f}")
        # 前缀容差：8 位 vs 12 位缩写指同一 commit，不得误报
        subs[0]["source_ref"] = subs[0]["source_ref"] + "abcd"
        f = _findings(mcc.check_ref_consistency(Path(tmp), csm, subs))
        if any(lv == "error" for lv, _ in f):
            failures.append(f"前缀容差不应误报: {f}")
        subs[0]["source_ref"] = "1234abcd"
        f = _findings(mcc.check_ref_consistency(Path(tmp), csm, subs))
        if not _has(f, "error", "不同值"):
            failures.append(f"不同值应 error: {f}")
        subs[0]["source_ref"] = ""  # 模板骨架（未同步）：落点不足两处有值时跳过
        subs[0]["overview_ref"] = None
        f = _findings(mcc.check_ref_consistency(Path(tmp), csm, subs))
        if any(lv == "error" for lv, _ in f):
            failures.append(f"骨架空值不应参与比对: {f}")
    return failures


def test_overview_ref_extraction(mcc):
    """overview 基准提取式必须与规程行形态同形——走 `_collect_submodules` 真实路径。

    **独立成用例，不塞进 `test_ref_consistency`**：那条的首句断言 `_has(f, "ok", "同值")`
    在 `filled >= 2` 时就成立（code-map 与 submodule 两个锚点已经够），overview 提取整个
    失效照样绿；它后面几步又直接改 `subs[0][...]` 字典、绕过了 `_collect_submodules`，
    等于从没验过提取式。实测过这个假绿：把提取调用换成内联副本并让口径漂开，第三个落点
    从每个项目静默消失，而 validate 与本套单测双双全绿。
    """
    failures = []
    # 正面①新写形态（无人写尾巴）
    with tempfile.TemporaryDirectory() as tmp:
        h1 = make_fixture(tmp)
        subs = mcc._collect_submodules(Path(tmp), mcc._load_csm(tmp))
        if subs[0]["overview_ref"] != h1:
            failures.append(f"新写形态提取不出 ref: {subs[0]['overview_ref']!r} != {h1!r}")

    # 正面②存量形态（括注带人写尾巴）——真实项目全是这一种
    with tempfile.TemporaryDirectory() as tmp:
        h1 = make_fixture(tmp, overview_tail="，当前仅 code-map")
        subs = mcc._collect_submodules(Path(tmp), mcc._load_csm(tmp))
        if subs[0]["overview_ref"] != h1:
            failures.append(f"带人写尾巴的形态提取不出 ref: {subs[0]['overview_ref']!r}")

    # ★反面：只让 overview 那一处漂移，必须 error 且点名该落点。
    #   提取式一旦坏掉 → overview_ref=None → filled 退回 2 且两者同值 → 拿不到 error
    #   → 本断言失败。正面断言可能被「三值恰好相同」的巧合满足，反面不会。
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp, ref_map={"code_map": "a" * 8, "submodule": "a" * 8,
                                   "overview": "b" * 8})
        csm = mcc._load_csm(tmp)
        subs = mcc._collect_submodules(Path(tmp), csm)
        f = _findings(mcc.check_ref_consistency(Path(tmp), csm, subs))
        if not _has(f, "error", "overview 基准行"):
            failures.append(f"overview 单独漂移应报 error 且点名该落点: {f}")
    return failures


def test_module_refreshed(mcc):
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        h1 = make_fixture(tmp)
        fw = Path(tmp) / "fw"
        csm = mcc._load_csm(tmp)
        subs = mcc._collect_submodules(Path(tmp), csm)
        f = _findings(mcc.check_module_refreshed(Path(tmp), csm, subs))
        if not _has(f, "ok", "刷新到位"):
            failures.append(f"基准=HEAD 应 ok: {f}")
        # 基准后再提交一次命中 code_paths → 漏刷
        (fw / "src" / "app.py").write_text("v2\n", encoding="utf-8", newline="\n")
        _git(fw, "add", "-A")
        _git(fw, "commit", "-m", "c2")
        f = _findings(mcc.check_module_refreshed(Path(tmp), csm, subs))
        if not _has(f, "error", "漏刷"):
            failures.append(f"基准后命中提交应报漏刷: {f}")
        subs[0]["source_ref"] = _short_head(fw)  # 刷平
        f = _findings(mcc.check_module_refreshed(Path(tmp), csm, subs))
        if not _has(f, "ok", "刷新到位"):
            failures.append(f"刷平后应 ok: {f}")
        # 工作树命中改动
        (fw / "src" / "app.py").write_text("v3-dirty\n", encoding="utf-8", newline="\n")
        f = _findings(mcc.check_module_refreshed(Path(tmp), csm, subs))
        if not _has(f, "error", "未提交改动"):
            failures.append(f"工作树命中应 error: {f}")
        _git(fw, "checkout", "--", ".")
        # 死引用
        subs[0]["source_ref"] = "deadbeef00"
        f = _findings(mcc.check_module_refreshed(Path(tmp), csm, subs))
        if not _has(f, "error", "死引用"):
            failures.append(f"解析不到应报死引用: {f}")
        # 可变引用跳过
        subs[0]["source_ref"] = "branch:main"
        f = _findings(mcc.check_module_refreshed(Path(tmp), csm, subs))
        if not _has(f, "warn", "可变引用"):
            failures.append(f"branch: 形态应 warn 跳过: {f}")
    return failures


def test_repo_status(mcc):
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        fw = Path(tmp) / "fw"
        csm = mcc._load_csm(tmp)
        subs = mcc._collect_submodules(Path(tmp), csm)
        (fw / "wip.txt").write_text("unrelated wip\n", encoding="utf-8", newline="\n")
        f = _findings(mcc.check_repo_status(Path(tmp), csm, subs))
        if not _has(f, "warn", "未提交改动"):
            failures.append(f"嵌套仓脏应 warn: {f}")
        if any(lv == "error" for lv, _ in f):
            failures.append(f"无关 WIP 不得 error（会卡死无关收口）: {f}")
    return failures


def _yaml_findings(mcc, tmp, cfg=None):
    """跑一次 `[yaml-parse]` 项，返回 findings。走 `_collect_submodules` 真实路径。"""
    p = Path(tmp)
    subs = mcc._collect_submodules(p, mcc._load_csm(tmp))
    return _findings(mcc.check_yaml_parse(p, cfg, subs))


def test_yaml_parse(mcc):
    """`[yaml-parse]` 项的四条红灯分支各自独立走一遍 + 反向探针不误伤。

    **两类畸形分在不同 fixture 文件里**，不合并到一份：一份同时含 C0 和裸引号的文件
    翻红只证明「这一整块有问题」，证不出两条判定各自是活的（突变粒度必须细于断言粒度）。
    每条分支比对的是**文案特征子串**，不是 `status == "error"`——只判等级的话，四条
    分支合并成一条也照样全绿。

    **本组以「PyYAML 可用」为前提，故有前置守卫**：缺库时 `check_yaml_parse` 对任何
    输入都只返回同一条「未验证」finding，于是下面每一条断言都会各报一次失败——一堆
    把人送去查 fixture 与判定式的假线索，而真正要改的是环境。守卫返回**一条普通
    failure**（不是「跳过」）：它走的是和其余任何失败完全相同的那条路，`main()` 打 ✗、
    退出码 1、`validate.py` 判红。**缺库时改前改后的红绿判定完全一致（都是红），变的
    只有诊断质量**——没有新增 skip 状态、没有新增退出码分支，也就没有新的退化面。
    """
    failures = []

    # 前置守卫。判定式复用 `_yaml_probe.pyyaml_status()`——它就是为回答「有没有装、
    # 装了但坏了是哪种」而单独成模块的，`probe_yaml_text` 的 docstring 也把「调用方须
    # 先问 pyyaml_status()」写成了契约，生产侧 `check_yaml_parse` 正是这么调的。这里
    # 不另写 `try: import yaml`——同一契约两份实现必漂。
    available, detail = mcc._load_yaml_probe().pyyaml_status()
    if not available:
        return [
            f"[yaml-parse] 组整组未跑：本机导入 PyYAML 失败（{detail}）。本组测的是"
            f"「PyYAML 可用时 check_yaml_parse 各分支的行为」——缺库时每条断言都只会读到"
            f"同一条『未验证』finding，逐条报失败会把人送去查 fixture 和判定式，而真正要"
            f"改的是环境。两条出路：①`pip install pyyaml==6.0.2`（CI 钉的就是这一版）；"
            f"②接受这一组不跑——但 `tools/validate.py` 仍会红，「没跑」不等于「通过」。"
            f"（`close_check.yaml_parse: false` 是**用户侧**关闭收口检查的声明，关不掉也"
            f"不该关掉本单测：那是两个层级。）"
        ]

    # ---- P0 基线：合法 fixture 不得误伤 ----
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        f = _yaml_findings(mcc, tmp)
        if not _has(f, "ok", "可解析"):
            failures.append(f"合法 fixture 应 ok: {f}")
        if any(lv == "error" for lv, _ in f):
            failures.append(f"合法 fixture 不得 error: {f}")

    # ---- 分支 1：占位符残留（**必须先于解析判**，否则该分支不可达）----
    # 反证「可达」：占位符**加了引号**——所以这份文件是**合法 YAML**，解析这一关
    # 根本拦不住它。若把占位符判定挪进解析失败的 except 分支，本组会变绿。
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        sub = Path(tmp) / "projects" / "modules" / "basicA" / "subA"
        raw = (sub / "submodule.yaml").read_text(encoding="utf-8")
        (sub / "submodule.yaml").write_text(
            raw + 'owner: "{{OWNER_ROLE}}"\n', encoding="utf-8", newline="\n")
        # 先自证这份文件确实是合法 YAML（否则本组证的是解析失败，不是占位符分支）
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "_yp_probe_selftest",
            Path(mcc.__file__).parent / "_yaml_probe.py")
        _yp = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_yp)
        # `import` 与 `safe_load` 分开 try：两者的排查方向相反，合成一个 `except Exception`
        # 会把**环境缺库**报成**fixture 坏了**，把人送去改一份完全没问题的文件。
        # ImportError 那半在正常路径上不可达（函数头部的守卫已拦下），它是一条**守卫完整性
        # 断言**——只有守卫自己失效时才会走到，所以文案指的是守卫而不是 fixture。
        try:
            import yaml as _y
        except Exception as e:
            failures.append(
                f"探针自身失效：本组头部的前置守卫本应已拦下「缺 PyYAML」，却仍走到这里 {e!r}"
                f"——去查守卫（`pyyaml_status()` 那一段），fixture 没问题")
        else:
            try:
                _y.safe_load((sub / "submodule.yaml").read_text(encoding="utf-8"))
            except Exception as e:
                failures.append(
                    f"探针自身失效：带引号占位符的 fixture 本应是合法 YAML，却 {e!r}"
                    f"——去查 `make_fixture()` 与本组注入的那一行，环境没问题")
        f = _yaml_findings(mcc, tmp)
        if not _has(f, "error", "占位符"):
            failures.append(f"占位符残留应 error 且点名占位符: {f}")
        if _has(f, "error", "不是合法 YAML"):
            failures.append(f"占位符残留不得走「语法非法」文案（会把人引向 YAML 语法）: {f}")

    # ---- 分支 2：C0 控制字符（ReaderError）----
    # ReaderError **没有 problem_mark**，位置要自己从 `.position` 换算；
    # `.character` 是 int（`ord()` 后的值）不是 str。这两条错一条，本分支就会在
    # 它唯一要抓的形态上抛异常、被调用方降级成 warn，等于静默失效。
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        sub = Path(tmp) / "projects" / "modules" / "basicA" / "subA"
        raw = (sub / "submodule.yaml").read_text(encoding="utf-8")
        (sub / "submodule.yaml").write_text(
            raw.replace('"   # 基准', '"\x01# 基准'), encoding="utf-8", newline="\n")
        f = _yaml_findings(mcc, tmp)
        if not _has(f, "error", "不是合法 YAML"):
            failures.append(f"C0 控制字符应 error: {f}")
        for kw in ("ReaderError", "\\x01", "U+0001", "行", "列"):
            if not _has(f, "error", kw):
                failures.append(f"C0 红灯必须给出 {kw}（那个字符肉眼不可见）: {f}")
        if not _has(f, "error", "控制字符"):
            failures.append(f"C0 红灯须给该形态专属的处置提示: {f}")

    # ---- CRLF 回归：换行形态不得改变任何一条分支的归属 ----
    # 曾经的回归：`.yaml` 分支由 `read_text`（通用换行，自动归一）改成 `raw.decode()`
    # （不转换）后，`_PLACEHOLDER_RE` 结尾的 `$` 在 `re.MULTILINE` 下只认 LF，CR 卡在
    # `}}` 与 `$` 之间且不被 `[ \t]*` 吃掉 ⇒ **CRLF 文件上「占位符残留」整类不可达**，
    # 红灯从「漏渲染占位符」变成「不是合法 YAML……裸双引号 / tab 缩进」，把人送去改
    # 完全不同的地方。当时 validate 与本单测**全绿**——因为 fixture 与 dogfood 的
    # `.yaml` 恰好全是 LF。这一组就是补上那个反证：**fixture 必须真的是 CRLF**
    # （落盘用 write_bytes 显式拼 `\r\n`，别让工具悄悄归一），落盘后回读断言。
    for eol_label, eol in (("LF", b"\n"), ("CRLF", b"\r\n")):
        # (a) 漏渲染占位符 → 必须走「占位符残留」，不得走「不是合法 YAML」
        with tempfile.TemporaryDirectory() as tmp:
            make_fixture(tmp)
            sy = Path(tmp) / "projects" / "modules" / "basicA" / "subA" / "submodule.yaml"
            lines = sy.read_text(encoding="utf-8").splitlines()
            lines.append('owner: {{OWNER_ROLE}}')
            sy.write_bytes(eol.join(l.encode("utf-8") for l in lines) + eol)
            got = sy.read_bytes()
            if (b"\r\n" in got) != (eol == b"\r\n"):
                failures.append(f"探针自身失效：{eol_label} fixture 落盘后行尾不符预期")
            f = _yaml_findings(mcc, tmp)
            if not _has(f, "error", "占位符"):
                failures.append(f"{eol_label} + 漏渲染占位符应走「占位符残留」文案: {f}")
            if _has(f, "error", "不是合法 YAML"):
                failures.append(
                    f"{eol_label} + 漏渲染占位符走了「语法非法」文案——会把人送去查裸双引号 / "
                    f"tab 缩进，而真实修法是补上漏渲染的占位符: {f}")
        # (b) 正常文件 → 不许因换行形态假红
        with tempfile.TemporaryDirectory() as tmp:
            make_fixture(tmp)
            sy = Path(tmp) / "projects" / "modules" / "basicA" / "subA" / "submodule.yaml"
            lines = sy.read_text(encoding="utf-8").splitlines()
            sy.write_bytes(eol.join(l.encode("utf-8") for l in lines) + eol)
            f = _yaml_findings(mcc, tmp)
            if any(lv == "error" for lv, _ in f):
                failures.append(f"{eol_label} 正常文件不得 error（假红）: {f}")
        # (c) C0 控制字符 → 行列与文案不因换行形态改变
        with tempfile.TemporaryDirectory() as tmp:
            make_fixture(tmp)
            sy = Path(tmp) / "projects" / "modules" / "basicA" / "subA" / "submodule.yaml"
            lines = sy.read_text(encoding="utf-8").splitlines()
            lines = [l.replace('"   # 基准', '"\x01# 基准') for l in lines]
            sy.write_bytes(eol.join(l.encode("utf-8") for l in lines) + eol)
            f = _yaml_findings(mcc, tmp)
            for kw in ("ReaderError", "\\x01", "U+0001"):
                if not _has(f, "error", kw):
                    failures.append(f"{eol_label} + C0 红灯缺 {kw}: {f}")

    # ---- 分支 3：标量提前终止（未转义裸双引号 → ParserError）----
    # 与分支 2 是**不同的文件、不同的畸形**，各自独立走到。
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        cm = (Path(tmp) / "projects" / "modules" / "basicA" / "subA"
              / "current-state" / "code-map.md")
        raw = cm.read_text(encoding="utf-8")
        cm.write_text(
            raw.replace("---\n\n# code map",
                        'verifier: "read_text(encoding="utf-8-sig")"\n---\n\n# code map'),
            encoding="utf-8", newline="\n")
        f = _yaml_findings(mcc, tmp)
        if not _has(f, "error", "不是合法 YAML"):
            failures.append(f"标量提前终止应 error: {f}")
        if not _has(f, "error", "ParserError"):
            failures.append(f"标量提前终止须点名 ParserError（与 C0 的措辞分开）: {f}")
        if _has(f, "error", "控制字符"):
            failures.append(f"标量提前终止不得复用 C0 的处置提示（指错方向）: {f}")

    # ---- 分支 4：frontmatter 被 BOM 遮住 ----
    # `_frontmatter` 用 utf-8 读，BOM 让 `^---` 失配 → 返回空串，而空串是**合法 YAML**。
    # 不单独判的话本项会对这份文件报绿，而检查 3/6 同时报「字段不存在」——两个结论
    # 矛盾且都错。
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        cm = (Path(tmp) / "projects" / "modules" / "basicA" / "subA"
              / "current-state" / "code-map.md")
        cm.write_bytes(b"\xef\xbb\xbf" + cm.read_bytes())
        f = _yaml_findings(mcc, tmp)
        if not _has(f, "error", "BOM"):
            failures.append(f"BOM 遮住 frontmatter 应 error 且点名 BOM: {f}")

    # ---- 分支 5：文件读不出来（非 UTF-8）—— 两条分支必须同处置 ----
    # 此前 frontmatter 那半走 `_frontmatter()` 的 `except: return ""`，而空串是合法
    # YAML ⇒ 非 UTF-8 的 .md 被**静默剔出扫描面**：无 finding、状态报绿，只在
    # 「N 份（扫描面 M 份）」的数字差里留痕。而 `.yaml` 那半一直有 warn。
    # 同一个 checker 对同一根因给两种处置，是本组要钉住的东西。
    for label, rel, enc in (
        ("code-map.md UTF-16", ("current-state", "code-map.md"), "utf-16"),
        ("overview.md GBK", ("overview.md",), "gbk"),
        ("submodule.yaml GBK", ("submodule.yaml",), "gbk"),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            make_fixture(tmp)
            target = Path(tmp) / "projects" / "modules" / "basicA" / "subA"
            for seg in rel:
                target = target / seg
            target.write_bytes(("# 中文标题\n" + target.read_text(encoding="utf-8")).encode(enc))
            f = _yaml_findings(mcc, tmp)
            if not _has(f, "warn", "读不出来"):
                failures.append(f"「{label}」必须 warn 且说「读不出来」，不得静默跳过: {f}")
            if not _has(f, "warn", label.split()[0]):
                failures.append(f"「{label}」的 warn 必须点名文件: {f}")
            # 措辞必须与「不是合法 YAML」分开——两者排查方向相反
            if _has(f, "error", "不是合法 YAML"):
                failures.append(f"「{label}」是读不出来，不得走「不是合法 YAML」文案: {f}")
    # 对称性的机器证明：同一根因下，frontmatter 分支与 .yaml 分支给出**同一个** level
    levels = []
    for rel, enc in ((("current-state", "code-map.md"), "utf-16"),
                     (("submodule.yaml",), "gbk")):
        with tempfile.TemporaryDirectory() as tmp:
            make_fixture(tmp)
            target = Path(tmp) / "projects" / "modules" / "basicA" / "subA"
            for seg in rel:
                target = target / seg
            target.write_bytes(("# 中文\n" + target.read_text(encoding="utf-8")).encode(enc))
            f = _yaml_findings(mcc, tmp)
            levels.append(sorted({lv for lv, msg in f if "读不出来" in msg}))
    if levels[0] != levels[1] or not levels[0]:
        failures.append(f"frontmatter 分支与 .yaml 分支对「读不出来」处置不一致: {levels}")

    # ---- 显式 config 豁免：info，不是 error，且说清「是声明不是跳过」----
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        f = _yaml_findings(mcc, tmp, {"close_check": {"yaml_parse": False}})
        if not _has(f, "info", "显式声明"):
            failures.append(f"显式豁免应 info 并说明是有记录的声明: {f}")
        if any(lv == "error" for lv, _ in f):
            failures.append(f"显式豁免不得 error: {f}")
        # 空壳 `{}`（文档里关账本检查的写法）**不得**连带关掉本项——
        # 关一项静默连坐关另一项是 fail-open 方向。
        f = _yaml_findings(mcc, tmp, {"close_check": {}})
        if not _has(f, "ok", "可解析"):
            failures.append(f"空壳 close_check 不得连带关闭本项: {f}")

    # ---- 反向探针：合法但「看着可疑」的形态一个都不许误伤 ----
    # 全绿本身不构成证据（现状本来就绿），所以这些是**构造**出来的形态，
    # 每一条都是真实会出现在 frontmatter / submodule.yaml 里的写法。
    reverse = {
        "引号内的井号":   'a: "x # not a comment"\n',
        "引号内的花括号": 'a: "{not a mapping}"\n',
        "块标量竖线":     "a: |\n  line1\n  line2\n",
        "折叠标量大于号": "a: >\n  line1\n  line2\n",
        "中文键与中文值": "模块: 装机与初始化\n",
        "空数组":         "a: []\n",
        "null":           "a: null\n",
        "CRLF 行尾":      'a: 1\r\nb: "x"\r\n',
        "行尾多空格":     'a: 1   \nb: 2  \n',
        "全角冒号在值里": 'a: "键：值"\n',
        "emoji":          'a: "✓ 完成 🎉"\n',
        # **实测过的真实假红**：文档里**讲述**占位符机制的散文。真实项目的
        # `projects/board.yaml` 出现过 4 处这种写法（任务 notes 里解释 scaffold 怎么渲染），
        # 按「文中有 `{{` 就报」会把它们全判红。占位符判定必须收窄到
        # 「整个标量值就是一个占位符」，不能扫全文。
        "散文里提占位符": 'notes: "scaffold `{{ROLE_PROFILE_ROUTING}}` 渲染进用户项目"\n',
        "值里占位符带后文": 'notes: "{{FOO}} 后面还有别的字"\n',
    }
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        sub = Path(tmp) / "projects" / "modules" / "basicA" / "subA"
        base = (sub / "submodule.yaml").read_text(encoding="utf-8")
        for label, snippet in reverse.items():
            (sub / "submodule.yaml").write_text(base + snippet,
                                                encoding="utf-8", newline="")
            f = _yaml_findings(mcc, tmp)
            if any(lv == "error" for lv, _ in f):
                failures.append(f"反向探针「{label}」被误伤: {f}")
        (sub / "submodule.yaml").write_text(base, encoding="utf-8", newline="\n")

    # ---- PyYAML 缺失：必须 error（「没跑」不等于「通过」），且**不是**解析失败那条文案 ----
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        probe_path = Path(mcc.__file__).parent / "_yaml_probe.py"
        import importlib.util
        spec = importlib.util.spec_from_file_location("_yaml_probe_absent", probe_path)
        fake = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fake)
        fake.pyyaml_status = lambda: (False, "ModuleNotFoundError: No module named 'yaml'")
        real_loader = mcc._load_yaml_probe
        mcc._load_yaml_probe = lambda: fake
        try:
            f = _yaml_findings(mcc, tmp)
        finally:
            mcc._load_yaml_probe = real_loader
        if not _has(f, "error", "未验证"):
            failures.append(f"缺 PyYAML 必须 error 并说明「没跑」不是「通过」: {f}")
        for kw in ("pip install pyyaml", "yaml_parse"):
            if not _has(f, "error", kw):
                failures.append(f"缺 PyYAML 的红灯须给出两条出路（缺 {kw}）: {f}")
        if _has(f, "error", "不是合法 YAML"):
            failures.append(f"缺 PyYAML 不得复用「文件坏了」的文案: {f}")
        # 恢复真实 loader 后必须回到绿——证明上面那组红来自注入而非污染
        if any(lv == "error" for lv, _ in _yaml_findings(mcc, tmp)):
            failures.append("恢复 loader 后应回绿（上一组红须来自注入）")

    return failures


def test_ledger(mcc):
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        f = _findings(mcc.check_ledger(p, {}))
        if not _has(f, "info", "跳过"):
            failures.append(f"无配置应跳过: {f}")
        cfg = {"close_check": {"ledger": "LEDGER.md",
                               "required_fields": ["模块", "验证", "提交"]}}
        f = _findings(mcc.check_ledger(p, cfg))
        if not _has(f, "error", "不存在"):
            failures.append(f"账本文件缺失应 error: {f}")
        today = date.today().isoformat()
        (p / "LEDGER.md").write_text(
            f"# 账本\n\n## {today} · 测试工作线\n"
            "- **模块**：a/b\n- **验证**：ok\n- **提交**：abc12345\n\n"
            "## 2026-01-01 · 旧条目\n- **模块**：x\n",
            encoding="utf-8", newline="\n")
        f = _findings(mcc.check_ledger(p, cfg))
        if not _has(f, "ok", "齐全"):
            failures.append(f"当日字段齐全应 ok: {f}")
        cfg["close_check"]["required_fields"].append("连带")
        f = _findings(mcc.check_ledger(p, cfg))
        if not _has(f, "error", "连带"):
            failures.append(f"缺字段应点名 error: {f}")
        old = (date.today() - timedelta(days=3)).isoformat()
        (p / "LEDGER.md").write_text(
            f"# 账本\n\n## {old} · 陈年条目\n- **模块**：a/b\n",
            encoding="utf-8", newline="\n")
        f = _findings(mcc.check_ledger(p, cfg))
        if not _has(f, "error", "没记账"):
            failures.append(f"最新条目过老应 error: {f}")
        # 非 git 目录 = `_work_since` 判不了 → 必须回落无条件 error 并在文案说明降级，
        # 不得静默当「没干活」放过（那会放走真实漏记）
        if not _has(f, "error", "无法判断"):
            failures.append(f"git 不可用时须在文案里说明降级: {f}")

    # git 条件化：账本条目过老，但自那天起既无提交、工作树也干净 ⇒ 本就没活可记，降 info
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        _git(p, "init", ".")
        old = (date.today() - timedelta(days=3)).isoformat()
        (p / "LEDGER.md").write_text(
            f"# 账本\n\n## {old} · 陈年条目\n- **模块**：a/b\n",
            encoding="utf-8", newline="\n")
        _git(p, "add", "-A")
        # `git log --since` 按**提交日**（committer date）过滤，不是作者日——只设
        # `--date` 的话提交日仍是「现在」，`--since=<那天>` 照样命中，探针会失效
        prev = os.environ.get("GIT_COMMITTER_DATE")
        os.environ["GIT_COMMITTER_DATE"] = f"{old}T10:00:00"
        try:
            _git(p, "commit", "--date", f"{old}T10:00:00", "-m", "old")
        finally:
            if prev is None:
                os.environ.pop("GIT_COMMITTER_DATE", None)
            else:
                os.environ["GIT_COMMITTER_DATE"] = prev
        cfg2 = {"close_check": {"ledger": "LEDGER.md", "required_fields": ["模块"]}}
        f = _findings(mcc.check_ledger(p, cfg2))
        if any(lv == "error" for lv, _ in f):
            failures.append(f"自条目日起无提交且工作树干净时不应 error: {f}")
        if not _has(f, "info", "本就没有要记的活"):
            failures.append(f"降级 info 需说明理由: {f}")
        # 造出「有活」：工作树弄脏 → 必须回到 error
        (p / "new.txt").write_text("x", encoding="utf-8", newline="\n")
        f = _findings(mcc.check_ledger(p, cfg2))
        if not _has(f, "error", "没记账"):
            failures.append(f"工作树有改动时应 error: {f}")

    # 嵌套仓拓扑：根仓干净、被 gitignore 的嵌套仓有未提交改动、账本过期。
    # 只扫根仓时这里会判成「没活」→ info + exit 0，而嵌套仓恰是大部分改动真正发生的
    # 地方（框架仓 / 子项目仓被外层 ignore，`git status` 对其整体失明）。
    # 走 run_all 而不是直调 check_ledger：发现逻辑（csm.discover_nested_repos）在那条
    # 路径上，直调传不进 nested，等于没验发现这一半。
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        old = (date.today() - timedelta(days=3)).isoformat()
        # 嵌套仓的提交日也要压到窗口之前——否则它自己那条「今天的提交」就构成有活，
        # 假红面这一半验的就不是「两仓皆干净」了（首跑正是栽在这个前置没造对）
        prev0 = os.environ.get("GIT_COMMITTER_DATE")
        os.environ["GIT_COMMITTER_DATE"] = f"{old}T09:00:00"
        try:
            make_fixture(tmp)  # 建 fw 嵌套仓 + basicA/subA（code_paths 指向 fw/）
        finally:
            os.environ.pop("GIT_COMMITTER_DATE", None) if prev0 is None \
                else os.environ.__setitem__("GIT_COMMITTER_DATE", prev0)
        _git(p, "init", ".")
        (p / "LEDGER.md").write_text(f"# 账本\n\n## {old} · 旧\n- **模块**：basicA/subA\n",
                                     encoding="utf-8", newline="\n")
        (p / ".workframe-config.json").write_text(
            json.dumps({"close_check": {"ledger": "LEDGER.md",
                                        "required_fields": ["模块"]}}),
            encoding="utf-8", newline="\n")
        (p / ".gitignore").write_text("fw/\n", encoding="utf-8", newline="\n")
        _git(p, "add", "-A")
        prev = os.environ.get("GIT_COMMITTER_DATE")
        os.environ["GIT_COMMITTER_DATE"] = f"{old}T10:00:00"
        try:
            _git(p, "commit", "--date", f"{old}T10:00:00", "-m", "base")
        finally:
            os.environ.pop("GIT_COMMITTER_DATE", None) if prev is None \
                else os.environ.__setitem__("GIT_COMMITTER_DATE", prev)
        # 先确认根仓确实干净——否则这条用例验的是别的东西
        if [x for x in _git(p, "status", "--porcelain").stdout.splitlines() if x.strip()]:
            failures.append("嵌套仓用例前置不成立：根仓应干净")
        led = [r for r in mcc.run_all(p) if r["id"] == "ledger"][0]
        f = [(x["level"], x["msg"]) for x in led["findings"]]
        if not _has(f, "info", "本就没有要记的活"):
            failures.append(f"两仓皆干净时应降 info（假红面）: {f}")
        # 嵌套仓弄脏（根仓 ignore 它，status 看不见）→ 必须翻 error
        (p / "fw" / "src" / "app.py").write_text("v2\n", encoding="utf-8", newline="\n")
        led = [r for r in mcc.run_all(p) if r["id"] == "ledger"][0]
        f = [(x["level"], x["msg"]) for x in led["findings"]]
        if not _has(f, "error", "没记账"):
            failures.append(f"嵌套仓有未提交改动时应 error（只扫根仓会漏判）: {f}")
        if not _has(f, "error", "fw"):
            failures.append(f"红灯应点名是哪个仓有活: {f}")

    # HTML 注释块里的条目示例不得被当成真实条目
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        today = date.today().isoformat()
        (p / "LEDGER.md").write_text(
            f"# 账本\n\n<!-- 模板示例：\n## {today} · 示例工作线\n- **模块**：x/y\n-->\n",
            encoding="utf-8", newline="\n")
        cfg3 = {"close_check": {"ledger": "LEDGER.md", "required_fields": ["模块"]}}
        f = _findings(mcc.check_ledger(p, cfg3))
        if not _has(f, "error", "找不到任何"):
            failures.append(f"注释块内的示例不得算作真实条目: {f}")
    return failures


def test_bug001_input_robustness(mcc):
    """BUG-001 回归：①畸形日期结构化 error 不裸崩 ②config 损坏出 warn 不静默跳过。"""
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        cfg = {"close_check": {"ledger": "LEDGER.md", "required_fields": ["模块"]}}
        # ①日期越界（正则 \d{2} 可匹配、date() 抛 ValueError）：应返回结构化 error
        (p / "LEDGER.md").write_text(
            "# 账本\n\n## 2026-08-32 · 手误日期\n- **模块**：a/b\n",
            encoding="utf-8", newline="\n")
        try:
            f = _findings(mcc.check_ledger(p, cfg))
        except Exception as e:
            f = None
            failures.append(f"畸形日期不应抛异常裸崩: {type(e).__name__}: {e}")
        if f is not None and not _has(f, "error", "日期非法"):
            failures.append(f"畸形日期应报结构化 error: {f}")
    with tempfile.TemporaryDirectory() as tmp:
        # ②config JSON 损坏：run_all 的 ledger 项应 warn 点明解析失败，而非 info 跳过
        p = Path(tmp)
        (p / ".workframe-config.json").write_text(
            '{"close_check": broken', encoding="utf-8", newline="\n")
        results = mcc.run_all(p)
        ledger = [r for r in results if r["id"] == "ledger"][0]
        msgs = " | ".join(x["msg"] for x in ledger["findings"])
        if ledger["status"] != "warn" or "解析失败" not in msgs:
            failures.append(f"config 损坏应 warn 点明解析失败: {ledger}")
    return failures


def _cov(mcc, tmp):
    """跑检查 6 并返回 findings。"""
    csm = mcc._load_csm(Path(tmp))
    subs = mcc._collect_submodules(Path(tmp), csm)
    return _findings(mcc.check_code_map_coverage(Path(tmp), csm, subs))


def _patch_cm(tmp, old, new):
    """改 fixture 的 code-map.md（字节直写，保持 LF）。"""
    p = Path(tmp) / "projects/modules/basicA/subA/current-state/code-map.md"
    raw = p.read_text(encoding="utf-8")
    assert raw.count(old) == 1, f"锚点命中 {raw.count(old)} 次: {old!r}"
    p.write_text(raw.replace(old, new), encoding="utf-8", newline="\n")


def _patch_yaml(tmp, extra):
    p = Path(tmp) / "projects/modules/basicA/subA/submodule.yaml"
    p.write_text(p.read_text(encoding="utf-8") + extra, encoding="utf-8", newline="\n")


def test_code_map_coverage(mcc):
    """检查 6：source_paths 与 code_paths 展开文件集相等（含六层各自的失败方式）。"""
    f = []
    SP = 'source_paths: ["src/app.py", "src/**"]'

    # ① 基线：两侧同指一组文件 → ok
    with tempfile.TemporaryDirectory() as tmp:
        r = _cov(mcc, tmp)
        if any(lv == "error" for lv, _ in r):
            f.append(f"基线不应报 error: {r}")
        if not _has(r, "ok", "覆盖率已对账"):
            f.append(f"基线应有 ok 汇总行: {r}")

    # ② L3「少」：仓里新增文件被 code_paths 的 glob 覆盖，而 source_paths 只列了单文件
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        (Path(tmp) / "fw" / "src" / "util.py").write_text("v\n", encoding="utf-8", newline="\n")
        _patch_cm(tmp, SP, 'source_paths: ["src/app.py"]')
        r = _cov(mcc, tmp)
        if not _has(r, "error", "**少了**"):
            f.append(f"应报「少了」: {r}")

    # ③ L3「多」：source_paths 认领了 code_paths 没认领的文件（⊇ 判据会静默吞掉这一类）
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        (Path(tmp) / "fw" / "extra").mkdir()
        (Path(tmp) / "fw" / "extra" / "x.py").write_text("v\n", encoding="utf-8", newline="\n")
        _git(Path(tmp) / "fw", "add", "-A")
        _patch_cm(tmp, SP, 'source_paths: ["src/app.py", "src/**", "extra/**"]')
        r = _cov(mcc, tmp)
        if not _has(r, "error", "**多了**"):
            f.append(f"应报「多了」: {r}")

    # ④ L2 尾斜杠：不是合法 glob，文案须点名它而不是笼统说「少了」
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        _patch_cm(tmp, SP, 'source_paths: ["src/app.py", "src/"]')
        r = _cov(mcc, tmp)
        if not _has(r, "error", "尾斜杠目录形态"):
            f.append(f"应报尾斜杠形态: {r}")

    # ⑤ L2 非法形态
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        _patch_cm(tmp, SP, 'source_paths: ["src/app.py", "src/**", "../oops/**"]')
        r = _cov(mcc, tmp)
        if not _has(r, "error", "非法形态"):
            f.append(f"应报非法形态: {r}")

    # ⑥ 字段整行删掉 ≠ 空数组：两者红灯文案必须分开（三态不可压两态）
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        _patch_cm(tmp, SP + "\n", "")
        r = _cov(mcc, tmp)
        if not _has(r, "error", "没有 `source_paths` 字段"):
            f.append(f"字段缺失应单独报: {r}")
        if _has(r, "error", "**少了**"):
            f.append(f"字段缺失不该退化成「少了」: {r}")
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        _patch_cm(tmp, SP, "source_paths: []")
        r = _cov(mcc, tmp)
        if not _has(r, "error", "**少了**"):
            f.append(f"显式空数组应走「少了」: {r}")

    # ⑦ 块状 YAML 列表（本对账只认行内数组）
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        _patch_cm(tmp, SP, "source_paths:\n  - src/app.py\n  - src/**")
        r = _cov(mcc, tmp)
        if not _has(r, "error", "不是可解析的行内数组"):
            f.append(f"块状列表应点名形态: {r}")

    # ⑧ L0 豁免：exempt 走 info；拼错的值报 error 而不是被当成 required 放行
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        _patch_cm(tmp, SP, "source_paths: []")   # 本会红，验豁免确实让它安静
        _patch_yaml(tmp, "code_map_coverage: exempt\n")
        r = _cov(mcc, tmp)
        if any(lv == "error" for lv, _ in r) or not _has(r, "info", "exempt"):
            f.append(f"exempt 应走 info 且不报 error: {r}")
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        _patch_yaml(tmp, "code_map_coverage: exmept\n")
        r = _cov(mcc, tmp)
        if not _has(r, "error", "取值非法"):
            f.append(f"非法取值应报 error: {r}")

    # ⑨ L0b 未同步过：warn 而非 error（module_init 新建的子模块天生是这个状态）
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp, ref_map={"code_map": "", "submodule": "", "overview": "0" * 8})
        r = _cov(mcc, tmp)
        if any(lv == "error" for lv, _ in r) or not _has(r, "warn", "未同步过"):
            f.append(f"source_ref 空应 warn 不 error: {r}")

    # ⑩ L0c 项目外仓的脱敏标识：整条跳过报 info（本地无该仓文件视野，硬对账必是假红）
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp, source_repo="内部仓/某项目")
        r = _cov(mcc, tmp)
        if any(lv == "error" for lv, _ in r) or not _has(r, "info", "整条跳过"):
            f.append(f"外部 source_repo 应整条跳过: {r}")

    # ⑪ L1 对账面落空：source_repo 漏填成根仓，而 code_paths 全在嵌套仓
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp, source_repo="")
        _git(tmp, "init")          # 让根仓可查，隔离掉「不是 git 仓」那条分支
        r = _cov(mcc, tmp)
        if not _has(r, "error", "对账整条落空"):
            f.append(f"对账面落空应报 error: {r}")

    # ⑫ 单仓项目（无嵌套仓，source_repo 空）：正常对账，不落 L1
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "src").mkdir()
        (tmp / "src" / "a.py").write_text("v\n", encoding="utf-8", newline="\n")
        _git(tmp, "init")
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-m", "c1")
        h = _short_head(tmp)
        sub = tmp / "projects" / "modules" / "b" / "s"
        (sub / "current-state").mkdir(parents=True)
        (sub / "submodule.yaml").write_text(
            "code_paths:\n  - src/**\n", encoding="utf-8", newline="\n")
        (sub / "current-state" / "code-map.md").write_text(
            f'---\nsource_repo: ""\nsource_ref: "{h}"\nsource_paths: ["src/**"]\n---\n',
            encoding="utf-8", newline="\n")
        r = _cov(mcc, tmp)
        if any(lv == "error" for lv, _ in r):
            f.append(f"单仓项目不应报 error: {r}")

    # ⑬ 项目根不是 git 仓：报 info 且说出「本模块覆盖率未对账」，不假红
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "src").mkdir()
        (tmp / "src" / "a.py").write_text("v\n", encoding="utf-8", newline="\n")
        sub = tmp / "projects" / "modules" / "b" / "s"
        (sub / "current-state").mkdir(parents=True)
        (sub / "submodule.yaml").write_text(
            "code_paths:\n  - src/**\n", encoding="utf-8", newline="\n")
        (sub / "current-state" / "code-map.md").write_text(
            '---\nsource_repo: ""\nsource_ref: "abcdef12"\nsource_paths: ["src/**"]\n---\n',
            encoding="utf-8", newline="\n")
        r = _cov(mcc, tmp)
        if any(lv == "error" for lv, _ in r):
            f.append(f"非 git 仓不应假红: {r}")
        if not _has(r, "info", "未对账"):
            f.append(f"非 git 仓应说出未对账: {r}")
    return f


def test_run_all_shapes(mcc):
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        make_fixture(tmp)
        results = mcc.run_all(Path(tmp))
        ids = [r["id"] for r in results]
        expected = ["stale-clean", "code-paths", "ref-consistency",
                    "module-refreshed", "repo-status", "code-map-coverage", "ledger",
                    "yaml-parse", "discipline-trace"]
        if ids != expected:
            failures.append(f"run_all 检查项不符: {ids}")
        if any(set(r) != {"id", "name", "status", "findings"} for r in results):
            failures.append("run_all 返回结构字段缺失")
    with tempfile.TemporaryDirectory() as tmp:  # 非 modules 项目：1-6 整体跳过
        results = mcc.run_all(Path(tmp))
        ids = [r["id"] for r in results]
        if ids != ["modules", "ledger", "yaml-parse", "discipline-trace"]:
            failures.append(f"非 modules 项目应整体跳过 1-6: {ids}")
        if results[0]["status"] != "ok":
            failures.append(f"跳过不应报非绿: {results[0]}")
    # 第三种形态：`projects/modules/` **在但为空**。此前它落进「有子模块」那条路径，
    # 六项各自对空集判绿 → 输出一排绿勾，看着像全查过且没问题，实际一项都没查到东西
    # （空项目实测 `ok 7 / warn 0 / error 0`）。现在合并成一条 info 并说明怎么让它生效。
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "projects" / "modules").mkdir(parents=True)
        results = mcc.run_all(Path(tmp))
        ids = [r["id"] for r in results]
        if ids != ["modules", "ledger", "yaml-parse", "discipline-trace"]:
            failures.append(f"空 modules 目录应合并成一条，不得输出六项绿勾: {ids}")
        f = [(x["level"], x["msg"]) for x in results[0]["findings"]]
        if not _has(f, "info", "这不等于检查通过"):
            failures.append(f"空 modules 需明说「不等于检查通过」: {f}")
        for kw in ("module-init", "code_paths", "code-to-doc"):
            if not _has(f, "info", kw):
                failures.append(f"空 modules 的 info 缺「怎么让它生效」里的 {kw}: {f}")
    # ⑮ run_all 零写入：validate smoke 与本套单测都在同一个临时目录上连调，写盘会让结果依赖顺序
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp)
        before = _tree_bytes(p)
        for _ in range(3):
            mcc.run_all(p)
            mcc.run_all(p, trace={})
        after = _tree_bytes(p)
        if before != after:
            changed = sorted(set(before) ^ set(after)) or sorted(
                k for k in before if before[k] != after.get(k))
            failures.append(f"run_all 连调改动了项目文件（应零写入）: {changed[:5]}")
    return failures


# ---------- [discipline-trace] 纪律产物痕迹 ----------


def _tree_bytes(root):
    """项目树（不含嵌套仓的 .git）路径 → 字节，用于零写入对账。"""
    return {x.relative_to(root).as_posix(): x.read_bytes() for x in Path(root).rglob("*")
            if x.is_file() and ".git" not in x.parts}


def _state(p):
    return Path(p) / STATE_REL


def _trace_fixture(tmp, board_updated=None, ledger_today=True, skill_used=False, commit_at=None,
                   with_config=True, with_board=True):
    """检查 9 的最小适用场景：嵌套仓 fw 在窗口内有提交（前提成立）＋ 账本 ＋ 看板一条命中任务。

    `commit_at`：嵌套仓那次提交的时刻（aware datetime），不给就是「现在」。
    `with_config=False` 不写 `.workframe-config.json`（`[ledger]` 读不到账本 ⇒ 判据 b 不适用）；
    `with_board=False` 不写看板（判据 c 不适用）。
    """
    p = Path(tmp)
    saved = {k: os.environ.get(k) for k in ("GIT_COMMITTER_DATE", "GIT_AUTHOR_DATE")}
    if commit_at is not None:
        os.environ["GIT_COMMITTER_DATE"] = os.environ["GIT_AUTHOR_DATE"] = commit_at.isoformat()
    try:
        make_fixture(tmp)
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    today = date.today()
    head = today if ledger_today else today - timedelta(days=5)
    (p / "LEDGER.md").write_text(f"# 账本\n\n## {head.isoformat()} · 纪律痕迹用例\n"
                                 "- **模块**：basicA/subA\n", encoding="utf-8", newline="\n")
    if with_config:
        (p / ".workframe-config.json").write_text(
            json.dumps({"close_check": {"ledger": "LEDGER.md", "required_fields": ["模块"]}}),
            encoding="utf-8", newline="\n")
    if with_board:
        (p / "projects" / "board.yaml").write_text(
            "tasks:\n  - id: T-1\n    status: in_progress\n    module: basicA/subA\n"
            f"    updated_at: '{board_updated or today.isoformat()}'\n", encoding="utf-8",
            newline="\n")
    if skill_used:
        ev = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "type": "skill_used", "skill": "x", "role": "dev", "success": True}
        (_state(p) / "events.jsonl").write_text(json.dumps(ev) + "\n", encoding="utf-8",
                                                newline="\n")
    return p


def _trace_item(mcc, p):
    trace = {}
    item = [r for r in mcc.run_all(Path(p), trace=trace) if r["id"] == "discipline-trace"][0]
    return item, [(x["level"], x["msg"]) for x in item["findings"]], trace


def _run_main(mcc, p):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mcc.main(["--project", str(p)])
    return rc, buf.getvalue()


def _log_rows(p):
    f = _state(p) / "discipline-trace-log.jsonl"
    return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def test_discipline_trace_criteria(mcc):
    """三条判据各自的失败方式 + 前提查询的时刻口径。b 与 c 各毁一条、另一条保持绿。"""
    failures = []
    # 基线：b 绿、c 绿、有 skill_used → 项级绿，没有 a 的提示
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp, skill_used=True)
        item, f, trace = _trace_item(mcc, p)
        if trace.get("result") != "green" or item["status"] != "ok":
            failures.append(f"基线应项级绿: {trace} {f}")
        if _has(f, "info", "skill_used"):
            failures.append(f"有 skill_used 时不应出 a 的提示（判据 a 没读事件流？）: {f}")
    # ① 零 skill_used：a 只出 info，项级不因 a 变红
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp)
        item, f, trace = _trace_item(mcc, p)
        if not _has(f, "info", "没有任何 `skill_used`"):
            failures.append(f"① 零 skill_used 应出 info 提示: {f}")
        if trace.get("result") != "green" or item["status"] != "ok":
            failures.append(f"① 判据 a 不得让项级变红: {trace} status={item['status']}")
    # ② 无当日账本段：b 红 ＋ exit 1 ＋ 运行行 exit_code 1、不进计数（c 保持绿）
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp, ledger_today=False, skill_used=True)
        rc, out = _run_main(mcc, p)
        rows = _log_rows(p)
        if rc != 1:
            failures.append(f"② 无当日账本段时整体应 exit 1，实得 {rc}")
        if not rows or rows[-1].get("exit_code") != 1 or rows[-1].get("result") != "red" \
                or (rows[-1].get("criteria") or {}).get("b") != "red" \
                or (rows[-1].get("criteria") or {}).get("c") != "green":
            failures.append(f"② 运行行应 result=red、b=red、c=green、exit_code=1: {rows[-1:]}")
        s = mcc.count_trace(rows)
        if s["count"] or s["pending"]:
            failures.append(f"② exit 1 的红不得进计数（不加、不挂起）: {s}")
        if "b 红" not in out:
            failures.append("② 输出应点名 b 红")
    # ③ 看板未更新：c 红、warn、exit 0、进计数为「未标记红」（b 保持绿）
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp, board_updated="2020-01-01", skill_used=True)
        item, f, trace = _trace_item(mcc, p)
        if item["status"] != "warn" or trace.get("criteria", {}).get("c") != "red" \
                or trace.get("criteria", {}).get("b") != "green":
            failures.append(f"③ 看板未更新应 c 红 / b 绿 / 项级 warn: {trace} {f}")
        if not _has(f, "warn", "本轮还没走到看板流转"):
            failures.append(f"③ c 的红灯应列出两条成因（含「还没走到看板流转」）: {f}")
        rc, _ = _run_main(mcc, p)
        s = mcc.count_trace(_log_rows(p))
        if rc != 0 or s["count"] != 0 or len(s["pending"]) != 1:
            failures.append(f"③ 应 exit 0 且挂起 1 次红: rc={rc} {s}")
    # ⑭ 任意红组合永不产 error
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp, board_updated="2020-01-01", ledger_today=False)
        item, f, _ = _trace_item(mcc, p)
        if item["status"] == "error" or any(lv == "error" for lv, _ in f):
            failures.append(f"⑭ b、c 同红时本项也不得产 error: {f}")
        if item["status"] != "warn":
            failures.append(f"⑭ b、c 同红时项级应 warn: {item['status']}")
    # ⑪ 窗口起点当天、早于此刻的提交：前提判适用（钉住「git 裸日期 --since 补当前钟点」）
    since = _local_yesterday_midnight()
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp, commit_at=datetime.combine(since.date(), dtime(0, 5)).astimezone())
        csm = mcc._load_csm(p)
        subs = mcc._collect_submodules(p, csm)
        got = mcc.trace_premise(p, csm, subs, since)[0]
        if got is not True:
            failures.append(f"⑪ 窗口首日 00:05 的提交应判适用，实得 {got!r}"
                            f"（--since 若只给日期，git 会用当前钟点补时分而截掉它）")
    # 窗口起点之前的提交、工作树干净：前提不成立（且不是 unknown）
    with tempfile.TemporaryDirectory() as tmp:
        before = datetime.combine(since.date() - timedelta(days=1), dtime(23, 0)).astimezone()
        p = _trace_fixture(tmp, commit_at=before)
        csm = mcc._load_csm(p)
        subs = mcc._collect_submodules(p, csm)
        got = mcc.trace_premise(p, csm, subs, since)[0]
        if got is not False:
            failures.append(f"窗口前的提交且工作树干净应判不适用（False），实得 {got!r}")
        _, f, trace = _trace_item(mcc, p)
        if trace.get("result") != "na" or not _has(f, "ok", "不适用"):
            failures.append(f"前提不成立时项级应不适用: {trace} {f}")
        # 判不了的仓：code_paths 指向非 git 的项目根 → unknown，与 False 分开
        subs[0]["code_paths"] = ["topdir/x.py"]
        got = mcc.trace_premise(p, csm, subs, since)
        if got[0] != "unknown" or "(项目根仓)" not in got[2]:
            failures.append(f"判不了的仓应记 unknown 并点名: {got[:3]}")
    return failures


def _local_yesterday_midnight():
    """独立算的窗口起点：本地「昨天 00:00」（aware）。**不取 `trace_window_start()` 的返回值当期望**
    ——拿被测函数自己的输出当期望，窗口起点被改成别的时刻也照样相等。"""
    now = datetime.now().astimezone()
    return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def _commit_all_at(repo, when, msg):
    """在 `repo` 里把工作树全部提交，提交日与作者日都钉在 `when`（aware datetime）。"""
    env = dict(os.environ, GIT_COMMITTER_DATE=when.isoformat(), GIT_AUTHOR_DATE=when.isoformat())
    for args in (["add", "-A"], ["commit", "-q", "-m", msg]):
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.local",
                        *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=env)


def test_discipline_trace_branches(mcc):
    """第九项里「回归时会放行或假绿」的分支，各一组（每组对应一条已打过的突变）。"""
    failures = []
    since = _local_yesterday_midnight()
    before = datetime.combine(since.date() - timedelta(days=1), dtime(23, 0)).astimezone()

    # Q01 前提要看工作树：提交在窗口之前，但工作树有未提交改动 → 适用
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp, commit_at=before, skill_used=True)
        (p / "fw" / "src" / "app.py").write_text("dirty\n", encoding="utf-8", newline="\n")
        _, f, trace = _trace_item(mcc, p)
        if trace.get("applicable") is not True:
            failures.append(f"Q01 提交在窗口前、工作树有改动时前提应成立（只看提交会漏判 → 放行）: "
                            f"{trace} {f}")

    # Q02 c 排除 cancelled：今天取消的任务不能让 c 绿
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp, skill_used=True)
        (p / "projects" / "board.yaml").write_text(
            "tasks:\n"
            "  - id: T-C\n    status: cancelled\n    module: basicA/subA\n"
            f"    updated_at: '{date.today().isoformat()}'\n"
            "  - id: T-1\n    status: in_progress\n    module: basicA/subA\n"
            "    updated_at: '2020-01-01'\n", encoding="utf-8", newline="\n")
        _, f, trace = _trace_item(mcc, p)
        if (trace.get("criteria") or {}).get("c") != "red":
            failures.append(f"Q02 只有已取消任务今天更新过时 c 应红（不排除 cancelled 会假绿）: {trace}")

    # Q05 a 只数窗口内：窗口外的旧 skill_used 不能压掉提示
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp)
        old = (since - timedelta(days=2)).astimezone(timezone.utc).isoformat(timespec="seconds")
        (_state(p) / "events.jsonl").write_text(
            json.dumps({"ts": old, "type": "skill_used", "skill": "x", "role": "dev",
                        "success": True}) + "\n", encoding="utf-8", newline="\n")
        _, f, _ = _trace_item(mcc, p)
        if not _has(f, "info", "没有任何 `skill_used`"):
            failures.append(f"Q05 事件流里只有窗口外的 skill_used 时仍应出提示（不看窗口会放行）: {f}")

    # Q08 同仓多子模块只算真被改的那个：subB 的任务今天更新过，不能替 subA 顶掉 c 的红
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp, commit_at=before, board_updated="2020-01-01", skill_used=True)
        fw = p / "fw"
        (fw / "other").mkdir()
        (fw / "other" / "b.py").write_text("b\n", encoding="utf-8", newline="\n")
        _commit_all_at(fw, before, "subB 的文件，窗口前")
        (fw / "src" / "app.py").write_text("v2\n", encoding="utf-8", newline="\n")
        _commit_all_at(fw, datetime.now().astimezone(), "subA 的改动，窗口内")
        sub_b = p / "projects" / "modules" / "basicA" / "subB"
        sub_b.mkdir(parents=True)
        (sub_b / "submodule.yaml").write_text("module: basicA/subB\ncode_paths:\n  - fw/other/**\n",
                                               encoding="utf-8", newline="\n")
        (p / "projects" / "board.yaml").write_text(
            "tasks:\n"
            "  - id: T-A\n    status: in_progress\n    module: basicA/subA\n"
            "    updated_at: '2020-01-01'\n"
            "  - id: T-B\n    status: in_progress\n    module: basicA/subB\n"
            f"    updated_at: '{date.today().isoformat()}'\n", encoding="utf-8", newline="\n")
        csm = mcc._load_csm(p)
        subs = mcc._collect_submodules(p, csm)
        hit = mcc.trace_premise(p, csm, subs, since)[1]
        _, f, trace = _trace_item(mcc, p)
        if hit != {"basicA/subA"} or (trace.get("criteria") or {}).get("c") != "red":
            failures.append(f"Q08 同仓两个子模块只有 subA 被改时命中集应为 {{subA}}、c 应红"
                            f"（全算命中会让 subB 的任务替它变绿）: hit={hit} {trace}")

    # Q09 窗口起点本身 = 本地昨天 00:00：独立计算期望，且昨天 00:05 的提交真的落在窗口内
    got = mcc.trace_window_start()
    if got != since:
        failures.append(f"Q09 窗口起点应为本地昨天 00:00（{since.isoformat()}），实得 {got!r}")
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp, commit_at=since + timedelta(minutes=5), skill_used=True)
        _, f, trace = _trace_item(mcc, p)
        if trace.get("applicable") is not True \
                or not any(f"窗口 {since:%Y-%m-%d} 00:00 起" in m for _, m in f):
            failures.append(f"Q09 昨天 00:05 的提交应判适用、输出应写「窗口 {since:%Y-%m-%d} 00:00 起」"
                            f"（窗口起点收窄会放行）: {trace} {f}")

    # Q10 b、c 都不适用 → 项级不适用，不是绿（不适用被计入会让计数假涨）。
    # c 走「看板在、但没有命中子模块的候选任务」这条不适用路，**不走看板不存在**——后者是 Q12 那一格
    # 的判定点，两格共用同一条路时，Q12 的突变会连带 Q10 一起红，分不出是哪一格在起作用。
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp, skill_used=True, with_config=False, with_board=False)
        (p / "projects" / "board.yaml").write_text(
            "tasks:\n  - id: T-X\n    status: in_progress\n    module: otherB/otherS\n"
            "    updated_at: '2020-01-01'\n", encoding="utf-8", newline="\n")
        _, f, trace = _trace_item(mcc, p)
        crit = trace.get("criteria") or {}
        if (crit.get("b"), crit.get("c")) != ("na", "na") or trace.get("result") != "na" \
                or not any("没有候选任务" in m for _, m in f):
            failures.append(f"Q10 b、c 都不适用时项级应不适用: {trace} {f}")

    # Q12 看板不存在 → c 不适用（不是绿）
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp, skill_used=True, with_board=False)
        _, f, trace = _trace_item(mcc, p)
        if (trace.get("criteria") or {}).get("c") != "na" \
                or not any("不存在" in m for _, m in f):
            failures.append(f"Q12 看板不存在时 c 应不适用并说明原因（判绿即假绿）: {trace} {f}")

    # J4 第九项自身抛异常 → 输出写明「本次未计入计数」、不写运行行、退出码不受影响
    real = mcc.trace_premise
    try:
        def boom(*a, **k):
            raise RuntimeError("probe")
        mcc.trace_premise = boom
        with tempfile.TemporaryDirectory() as tmp:
            p = _trace_fixture(tmp, skill_used=True)
            rc, out = _run_main(mcc, p)
            if rc != 0 or "检查自身异常" not in out or "本次未计入计数" not in out \
                    or (_state(p) / "discipline-trace-log.jsonl").exists():
                failures.append(f"J4 第九项自身异常时应 exit 0、写明「本次未计入计数」、不写运行行"
                                f"（rc={rc}）")
    finally:
        mcc.trace_premise = real
    return failures


def _synth_run(day_offset, ident, result="green", exit_code=0, fp=None, b="green", c="green"):
    """合成运行行：ts 取「今天往前 day_offset 天」的本地正午（避免跨时区换日）。"""
    ts = datetime.combine(date.today() - timedelta(days=day_offset), dtime(12, 0)).astimezone()
    return {"ts": ts.astimezone(timezone.utc).isoformat(timespec="seconds"), "kind": "run",
            "fingerprint": fp or ("f%063d" % abs(hash((day_offset, ident, result)) % 10 ** 60)),
            "exit_code": exit_code, "identity": ident, "applicable": True, "result": result,
            "criteria": {"a": "green", "b": b, "c": c}}


def _synth_mark(fp, verdict, by="qa", day_offset=0):
    ts = datetime.combine(date.today() - timedelta(days=day_offset), dtime(13, 0)).astimezone()
    return {"ts": ts.astimezone(timezone.utc).isoformat(timespec="seconds"), "kind": "mark",
            "fingerprint": fp, "verdict": verdict, "marked_by": by}


def _greens(n, days):
    """n 个不同身份的绿运行，均匀铺在 days 个不同本地日（最早的在前）。"""
    return [_synth_run(days + 20 - (i * days // n), f"ledger:g{i}") for i in range(n)]


def test_discipline_trace_counting(mcc):
    """升级计数规则（纯函数 ＋ 合成日志）。④⑤ 另经 main() 端到端走到打印那一步。"""
    failures = []

    def cnt(rows):
        rows = sorted(rows, key=lambda r: r["ts"])
        return mcc.count_trace(rows)

    s = cnt(_greens(10, 7))
    if not (s["count"] == 10 and s["days"] == 7 and s["suggest"]):
        failures.append(f"⑤ 10 个绿身份跨 7 个本地日应建议升级: {s}")
    if not any("已满足升级条件" in m for _, m in mcc.trace_count_findings(s)):
        failures.append("⑤ 满足条件时输出应含「已满足升级条件」")
    s = cnt(_greens(10, 6))
    if s["suggest"] or s["days"] != 6:
        failures.append(f"⑥ 10 个绿身份只跨 6 天不应建议: {s}")
    red = _synth_run(1, "ledger:r", result="red", fp="e" * 64)
    s = cnt(_greens(10, 7) + [red])
    msgs = " ".join(m for _, m in mcc.trace_count_findings(s))
    if s["suggest"] or s["pending"] != ["e" * 64] or "1 次红待确认" not in msgs \
            or "已满足升级条件" in msgs:
        failures.append(f"⑦ 有未标记的红应压住建议并提示待确认: {s} / {msgs}")
    # ④ 假红清零
    s = cnt(_greens(9, 7) + [red, _synth_mark("e" * 64, "false-positive")])
    if s["count"] != 0 or s["pending"]:
        failures.append(f"④ qa 标闸判错的红应清零: {s}")
    # 假红标在「非计入」的那次运行上（同一身份后来重跑变绿）也清零
    fp_red = _synth_run(2, "ledger:same", result="red", fp="a" * 64)
    later_green = _synth_run(1, "ledger:same", fp="b" * 64)
    s = cnt(_greens(5, 5) + [fp_red, later_green, _synth_mark("a" * 64, "false-positive")])
    if s["count"] != 1:
        failures.append(f"④b 假红标记落在非计入运行上也清零（清零后只剩重跑变绿那 1 次）: {s}")
    # ⑧ 确实漏做 +1
    s = cnt(_greens(3, 3) + [red, _synth_mark("e" * 64, "missed", by="self")])
    if s["count"] != 4 or s["pending"]:
        failures.append(f"⑧ 标确实漏做的红应 +1 且不再挂起: {s}")
    # 同一次运行重复标记取最后一条
    s = cnt(_greens(3, 3) + [red, _synth_mark("e" * 64, "missed"),
                             _synth_mark("e" * 64, "false-positive", day_offset=0)])
    if s["count"] != 0:
        failures.append(f"同一指纹后标的「闸判错」应覆盖先标的「确实漏做」: {s}")
    # K1：标记只挂在它之前同指纹的最后一次运行上。3 绿 → X 红（指纹 F）标 missed → 2 绿 →
    # Y 红（同指纹 F、未标）⇒ X 计入、Y 挂起：count=6、pending=1
    fp_same = "9" * 64
    x_red = _synth_run(10, "ledger:X", result="red", fp=fp_same)
    y_red = _synth_run(4, "ledger:Y", result="red", fp=fp_same)
    three = [_synth_run(d, f"ledger:k1-{d}") for d in (13, 12, 11)]
    two = [_synth_run(d, f"ledger:k1-{d}") for d in (7, 6)]
    s = cnt(three + [x_red, _synth_mark(fp_same, "missed", by="self", day_offset=10)] + two + [y_red])
    if s["count"] != 6 or s["pending"] != [fp_same]:
        failures.append(f"K1 标记不得套到之后同指纹的另一次红上（应 count=6、pending=1）: {s}")
    # K2：同形，标 false-positive ⇒ 在 X 处清零、Y 仍挂起：count=2、pending=1
    s = cnt(three + [x_red, _synth_mark(fp_same, "false-positive", day_offset=10)] + two + [y_red])
    if s["count"] != 2 or s["pending"] != [fp_same]:
        failures.append(f"K2 闸判错只清零被标的那一次，之后同指纹的红仍待确认（应 count=2、pending=1）: {s}")
    # ⑨ 不适用夹在绿之间：不计入不清零
    na = _synth_run(3, "ledger:na", result="na")
    s = cnt([_synth_run(4, "ledger:x"), na, _synth_run(2, "ledger:y")])
    if s["count"] != 2:
        failures.append(f"⑨ 不适用不计入不清零: {s}")
    # ⑩ 同一身份多次运行（红 → 修 → 绿）合并为一次，取最后一次
    s = cnt([_synth_run(1, "ledger:one", result="red", fp="c" * 64),
             _synth_run(1, "ledger:one", fp="d" * 64)])
    if s["count"] != 1 or s["pending"]:
        failures.append(f"⑩ 同一身份应合并为一次且取最后一次（绿）: {s}")
    # exit 1 的运行不进计数
    s = cnt([_synth_run(1, "ledger:e1", exit_code=1)])
    if s["count"] != 0:
        failures.append(f"exit 1 的运行不得进计数: {s}")

    # ⑤ 端到端：种 10 条合成绿身份（跨 7 天），再真跑一次绿收口 → 输出里真的打印建议
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp, skill_used=True)
        (_state(p) / "discipline-trace-log.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in _greens(10, 7)), encoding="utf-8", newline="\n")
        rc, out = _run_main(mcc, p)
        if rc != 0 or "已满足升级条件" not in out:
            failures.append(f"⑤ 端到端：种 10 绿跨 7 天后真跑一次，应打印升级建议（rc={rc}）")
    # ④ 端到端：9 绿 ＋ 1 红，经 bin 入口标闸判错，再真跑一次 → 计数从头开始
    with tempfile.TemporaryDirectory() as tmp:
        p = _trace_fixture(tmp, skill_used=True)
        (_state(p) / "discipline-trace-log.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in _greens(9, 7) + [red]),
            encoding="utf-8", newline="\n")
        r = _mark(p, "e" * 12, "false-positive", "qa")
        rc, out = _run_main(mcc, p)
        if r.returncode != 0 or "升级计数 1/10" not in out:
            failures.append(f"④ 端到端：标闸判错后再跑一次应从 1 起算（mark rc={r.returncode} "
                            f"{r.stderr.strip()[:120]}）")

    # ⑫ 写入返回 False（落 spill，spill 行照样计数）/ None（未计入）；退出码不变
    real = mcc.append_line
    try:
        with tempfile.TemporaryDirectory() as tmp:
            p = _trace_fixture(tmp, skill_used=True)

            def spill(path, line):
                sp = Path(path).with_name("discipline-trace-log.4242.spill.jsonl")
                with sp.open("a", encoding="utf-8", newline="") as fh:
                    fh.write(line + "\n")
                return False
            mcc.append_line = spill
            rc, out = _run_main(mcc, p)
            if rc != 0 or "落到了 spill" not in out or "升级计数 1/10" not in out:
                failures.append(f"⑫ 返回 False：应 warn、spill 行参与计数、退出码不变（rc={rc}）")
            mcc.append_line = lambda path, line: None
            rc2, out2 = _run_main(mcc, p)
            if rc2 != 0 or "本次未计入计数" not in out2 or "升级计数 1/10" not in out2:
                failures.append(f"⑫ 返回 None：应 warn「未计入」、计数不变、退出码不变（rc={rc2}）")
    finally:
        mcc.append_line = real
    return failures


def _mark(p, prefix, verdict, by):
    entry = REPO_ROOT / "plugins" / "core" / "bin" / "workframe-discipline-mark"
    return subprocess.run(
        [sys.executable, str(entry), "--project", str(p), "--fingerprint", prefix,
         "--verdict", verdict, "--by", by],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"), timeout=60)


def test_discipline_mark_command(mcc):
    """⑬ 标记命令：各拒绝分支 exit 2 且日志字节不变；合法标记 exit 0 且只追加一行。"""
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp)
        _state(p).mkdir(parents=True)
        rows = [_synth_run(1, "ledger:a", result="red", fp="abcdabcd1" + "0" * 55),
                _synth_run(1, "ledger:b", result="red", fp="abcdabcd2" + "0" * 55),
                _synth_run(1, "ledger:c", fp="eeeeeeee" + "0" * 56),
                _synth_run(1, "ledger:d", result="red", fp="1234abcd" + "0" * 56),
                _synth_run(1, "ledger:d", result="red", fp="1234abcd" + "0" * 56)]
        log = _state(p) / "discipline-trace-log.jsonl"
        log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8", newline="\n")
        cases = [
            ("前缀歧义（命中 2 个不同指纹）", "abcdabcd", "missed", "qa", "2 个不同指纹"),
            ("目标运行不是红", "eeeeeeee", "missed", "qa", "不是红"),
            ("false-positive --by self", "1234abcd", "false-positive", "self", "只收"),
            ("前缀过短", "1234", "missed", "qa", "至少 8 位"),
            ("零命中", "99999999", "missed", "qa", "命中 0 个"),
            ("取值域外（argparse）", "1234abcd", "wrong", "qa", "invalid choice"),
        ]
        for label, prefix, verdict, by, kw in cases:
            before = log.read_bytes()
            r = _mark(p, prefix, verdict, by)
            if r.returncode != 2 or log.read_bytes() != before:
                failures.append(f"⑬ {label} 应 exit 2 且零写入: rc={r.returncode}")
            if kw not in r.stderr:
                failures.append(f"⑬ {label} 的 stderr 应说清原因（缺「{kw}」）: {r.stderr.strip()[:160]}")
        before = log.read_bytes()
        r = _mark(p, "1234ABCD", "missed", "self")   # 同一指纹的两次运行不算歧义；大小写不敏感
        added = log.read_bytes()[len(before):].decode("utf-8").splitlines()
        if r.returncode != 0 or len(added) != 1 or json.loads(added[0]).get("kind") != "mark" \
                or json.loads(added[0]).get("marked_by") != "self":
            failures.append(f"⑬ 合法标记应 exit 0 且只追加一行 mark: rc={r.returncode} {added}")
        if not log.read_bytes().startswith(before):
            failures.append("⑬ 标记命令不得改动既有行（只追加）")
    return failures


# ---------- 汇总 ----------


def main():
    mcc = _load_mcc()
    mcc._load_csm(REPO_ROOT)  # 先完成唯一一次 exec（包流在此发生），后续全走重定向
    tests = [
        ("load_csm 双载回归", test_load_csm_regression),
        ("检查1 stale + reason 事件", test_stale_and_reason_event),
        ("检查2 code_paths 形态与指向", test_code_paths_health),
        ("检查3 基准同值性", test_ref_consistency),
        ("检查3 overview 提取式（真实路径）", test_overview_ref_extraction),
        ("检查4 命中已刷新", test_module_refreshed),
        ("检查5 仓状态判级", test_repo_status),
        ("检查6 code-map 覆盖率对账", test_code_map_coverage),
        ("[ledger] 账本字段", test_ledger),
        ("[yaml-parse] YAML 可解析性", test_yaml_parse),
        ("BUG-001 输入健壮性回归", test_bug001_input_robustness),
        ("run_all 结构与退化", test_run_all_shapes),
        ("[discipline-trace] 三判据与前提", test_discipline_trace_criteria),
        ("[discipline-trace] 放行 / 假绿分支", test_discipline_trace_branches),
        ("[discipline-trace] 升级计数", test_discipline_trace_counting),
        ("[discipline-trace] 标记命令", test_discipline_mark_command),
    ]
    total_failures = []
    for name, fn in tests:
        try:
            fs = fn(mcc)
        except Exception as e:
            fs = [f"用例自身异常: {type(e).__name__}: {e}"]
        mark = "✓" if not fs else "✗"
        print(f"{mark} {name}" + (f"  ({len(fs)} failures)" if fs else ""))
        for x in fs:
            print(f"    - {x}")
        total_failures.extend(fs)
    print(f"\n{'全部通过' if not total_failures else f'{len(total_failures)} 项失败'}"
          f"（{len(tests)} 组用例）")
    return 1 if total_failures else 0


if __name__ == "__main__":
    sys.exit(main())
