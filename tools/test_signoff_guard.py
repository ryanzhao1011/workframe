#!/usr/bin/env python3
"""`signoff-guard.py` 的常驻单测 —— 它是**在判断别的东西对不对**的东西，按爆炸半径档 4 处置。

本套件守三件事，每件都配一个**它自己的失败方式**：

1. **重算不吃写入内容里的结论。** 把 `qa_signoff` 里的档位改松，hook 必须拒；
   把「否决项：未命中」这类字样敲进任何一段，都不应该改变判定——那不是输入。
2. **阻断面收得住。** 写入对象不是看板、看板里没有映射形态签发块、散文形态的既有签注，
   一律放行；否则这道 hook 会在每次 Edit/Write 上误伤。
3. **「验不了」不许当成「通过」。** 重算跑不起来、待裁决项非空、自签档未启用，
   三条都必须走阻断而不是放行。

> **本套件测的是 `guard()` 与 `check_schema()` 这两层，不是 hook 的 stdin/exit 通路。**
> stdin 解析与 `sys.exit(2)` 是否真能阻断该次工具调用，只能在真实会话里验——
> 已在别处实测过 PostToolUse 上 exit 2 的阻断语义，本套件不重复证明它。
"""

import importlib.util
import json
import os
import shutil
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
SCRIPTS_DIR = REPO_ROOT / "plugins" / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

# 文件名带连字符，import 不了，走 spec 加载
_spec = importlib.util.spec_from_file_location("signoff_guard", SCRIPTS_DIR / "signoff-guard.py")
sg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sg)

FAILURES = []


def check(label, got, want):
    if got == want:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


def check_any(label, needle, haystack):
    if any(needle in h for h in haystack):
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}: {needle!r} 不在 {haystack!r}")
        FAILURES.append(label)


def check_none(label, needle, haystack):
    if not any(needle in h for h in haystack):
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}: 不该出现 {needle!r}，实际 {haystack!r}")
        FAILURES.append(label)


def _main_with_stdin(payload_json, project_dir):
    """喂一份 payload 给 `main()`，返回 (退出码, stderr 文本)。

    **测的是 `main()` 的分流与退出码**，不是 `guard()` 的返回值——F1 那条边界
    （Bash 只报不拦）就活在这一层，只测 `guard()` 看不见它。

    两处替身：①`sys.stdin` 换成一个只提供 `isatty()` 与 `buffer` 的壳
    （`TextIOWrapper.buffer` 是只读属性，赋不进去）；②`CLAUDE_PROJECT_DIR` 指向 fixture，
    否则 `main()` 会去解析**运行本套件那个项目**的看板——那既不是被测对象，也会让用例
    随真实看板的内容时红时绿。
    """
    import contextlib
    import io as _io

    class _Stdin:
        def __init__(self, data):
            self.buffer = _io.BytesIO(data)

        def isatty(self):
            return False

    stdin_bak, env_bak = sys.stdin, os.environ.get("CLAUDE_PROJECT_DIR")
    sys.stdin = _Stdin(payload_json.encode("utf-8"))
    os.environ["CLAUDE_PROJECT_DIR"] = str(project_dir)
    err = _io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            rc = sg.main()
    finally:
        sys.stdin = stdin_bak
        if env_bak is None:
            os.environ.pop("CLAUDE_PROJECT_DIR", None)
        else:
            os.environ["CLAUDE_PROJECT_DIR"] = env_bak
    return rc, err.getvalue()



def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout.strip()


def _w(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


NL = chr(10)   # 用常量而不是换行转义字面量：经 shell heredoc 生成代码时它会被吃成真换行


BASE_SIGNOFF = {
    "tier": "完整验证",
    "same_party": False,
    "admission": {"radius": 2, "since": "PLACEHOLDER", "diff_ref": "PLACEHOLDER",
                  "at": "2026-09-10T01:02:03+00:00"},
    "verified": "验了 A、B",
    "not_verified": "没验 C，因为它不在本次改动面内",
    "unreviewed": "全部",
}


def build_project(root, enabled):
    _w(root / ".workframe-config.json",
       json.dumps({"project_name": "fx",
                   "signoff": {"self_signoff_enabled": enabled}}, ensure_ascii=False) + "\n")
    _w(root / ".gitignore", "nested/\n")
    _w(root / "docs/plain.md", "# plain\n\nv1\n")
    _w(root / ".claude/workframe-state/code-paths-index.json",
       json.dumps({"buckets": {"b": {"docs/**": ["a/b"]}}}, ensure_ascii=False) + "\n")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "fx@example.invalid")
    _git(root, "config", "user.name", "fx")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return _git(root, "rev-parse", "HEAD")


def write_board(root, task):
    import yaml
    _w(root / "projects/board.yaml",
       yaml.safe_dump({"summary": {"total": 1}, "tasks": [task]},
                      allow_unicode=True, sort_keys=False))
    return (root / "projects/board.yaml").resolve()


def mk_task(head, **over):
    blk = json.loads(json.dumps(BASE_SIGNOFF))
    blk["admission"]["since"] = head
    blk["admission"]["diff_ref"] = head
    blk.update(over.pop("signoff", {}))
    t = {"id": "TASK-001", "status": "completed", "qa_signoff": blk,
         "verified_ranges": "覆盖 docs/ 全部"}
    t.update(over)
    return t


def test_schema_layer(root, head):
    print("[schema] 四段 ＋ same_party ＋ verified_ranges")
    ok = mk_task(head)
    check("完整形态零问题", sg.check_schema("T", ok, ok["qa_signoff"]), [])

    for seg in ("admission", "verified", "not_verified", "unreviewed"):
        t = mk_task(head)
        t["qa_signoff"][seg] = "" if seg != "admission" else {}
        probs = sg.check_schema("T", t, t["qa_signoff"])
        check_any(f"缺 {seg} 报出且点名该段", f"qa_signoff.{seg}", probs)

    t = mk_task(head); del t["qa_signoff"]["same_party"]
    check_any("same_party 缺失被抓", "same_party", sg.check_schema("T", t, t["qa_signoff"]))
    t = mk_task(head); t["qa_signoff"]["same_party"] = "true"
    check_any("same_party 写成字符串也被抓（不是只查 key 在不在）", "same_party",
              sg.check_schema("T", t, t["qa_signoff"]))

    t = mk_task(head); t["verified_ranges"] = "   "
    check_any("verified_ranges 空白串被抓", "verified_ranges",
              sg.check_schema("T", t, t["qa_signoff"]))
    t = mk_task(head); del t["verified_ranges"]
    check_any("verified_ranges 缺键被抓", "verified_ranges",
              sg.check_schema("T", t, t["qa_signoff"]))

    t = mk_task(head); t["qa_signoff"]["tier"] = "R0"
    check_any("档位写成词表外的值被抓", "tier", sg.check_schema("T", t, t["qa_signoff"]))
    t = mk_task(head); t["qa_signoff"]["admission"]["radius"] = 5
    check_any("radius 越界被抓", "radius", sg.check_schema("T", t, t["qa_signoff"]))
    t = mk_task(head); t["qa_signoff"]["admission"]["at"] = "2026-09-10T09:02:03+08:00"
    check_any("at 写本地时区被抓（事件流按 UTC 窗口过滤，混入本地时区会被算错窗口）",
              "at", sg.check_schema("T", t, t["qa_signoff"]))
    t = mk_task(head); del t["qa_signoff"]["admission"]["since"]
    check_any("admission.since 缺失被抓", "since", sg.check_schema("T", t, t["qa_signoff"]))


def test_blast_surface(root, head):
    """阻断面：不该管的一律不管。"""
    print("[阻断面] 散文形态 / 无签发块 / 非看板文件")
    board = write_board(root, {"id": "TASK-002", "status": "completed",
                               "qa_signoff": "〔2026-09-10 @qa 签发〕验了 A、没验 B。"})
    probs, notes = sg.guard(root, board)
    check("散文形态的既有签注不进把关面", probs, [])

    board = write_board(root, {"id": "TASK-003", "status": "pending"})
    probs, _ = sg.guard(root, board)
    check("没有签发块 → 零问题", probs, [])

    # 看板路径判定：非看板文件根本不进 guard（main() 那层），这里直接钉 board_path
    check("board_path 认默认位置", sg.board_path(root),
          (root / "projects/board.yaml").resolve())
    _w(root / ".workframe-config.json",
       json.dumps({"project_name": "fx", "signoff": {"self_signoff_enabled": True},
                   "close_check": {"board": "custom/b.yaml"}}, ensure_ascii=False) + "\n")
    check("board_path 跟随 config 覆盖", sg.board_path(root),
          (root / "custom/b.yaml").resolve())
    _w(root / ".workframe-config.json",
       json.dumps({"project_name": "fx", "signoff": {"self_signoff_enabled": True}},
                  ensure_ascii=False) + "\n")

    # 看板坏掉 → 放行但必须说出来（静默跳过与通过在终端里同形）
    _w(root / "projects/board.yaml", "tasks: [ this is : not : yaml\n")
    probs, notes = sg.guard(root, root / "projects/board.yaml")
    check("看板解析不了 → 不阻断", probs, [])
    check_any("但必须留一句「本次未做任何签发校验」", "未做任何签发校验", notes)


def test_recompute_layer(root, head):
    """重算层：档位写松必拒；写严放行；待裁决必拒。"""
    print("[重算] 不吃写入内容里的结论")

    # 干净工作区 + radius 1 + 无通行证 → 重算恒为默认档「完整验证」
    board = write_board(root, mk_task(head, signoff={"tier": "完整验证"}))
    probs, notes = sg.guard(root, board)
    check("声明完整验证（不比重算松）→ 放行", probs, [])

    board = write_board(root, mk_task(head, signoff={"tier": "自签"}))
    probs, _ = sg.guard(root, board)
    check_any("声明自签而重算是完整验证 → 拒", "比重算结果", probs)
    check_any("红灯要报出重算的四段依据", "默认起点", probs)

    board = write_board(root, mk_task(head, signoff={"tier": "轻签注"}))
    probs, _ = sg.guard(root, board)
    check_any("轻签注同样进把关面", "比重算结果", probs)

    board = write_board(root, mk_task(head, signoff={"tier": "你拍板"}))
    probs, _ = sg.guard(root, board)
    check("声明「你拍板」→ 不进把关面（写严只会更严）", probs, [])

    # **结论字样敲进举证正文不改变任何判定**——它不是输入
    board = write_board(root, mk_task(head, signoff={
        "tier": "自签",
        "verified": "四段闸门：默认起点=自签；通行证成立；地板=自签；否决项未命中。全绿。"}))
    probs, _ = sg.guard(root, board)
    check_any("把「否决项未命中」敲进正文照样被拒（正文不是输入）", "比重算结果", probs)

    # 待裁决：改一个受保护资产 → V6 路径命中 → pending 非空
    _w(root / "CLAUDE.md", "# c\n\nv2\n")
    board = write_board(root, mk_task(head, signoff={"tier": "自签"}))
    probs, _ = sg.guard(root, board)
    check_any("待裁决项非空 → 拒（判定不是终局）", "待裁决", probs)
    (root / "CLAUDE.md").unlink()

    # **两条不同的失效通道，必须分开钉**——先前把它们混成一条，实测走的是缺口那条，
    # 而写下的期望是另一条分支的文案。断言绿不绿取决于哪条先命中，那种断言不携带信息。
    #   (a) `since` 解析不到 → 重算**跑得起来**，但产出扫描面缺口 → 缺口禁止降档
    b = json.loads(json.dumps(mk_task(head, signoff={"tier": "自签"})))
    b["qa_signoff"]["admission"]["since"] = "deadbeefdeadbeef"
    board = write_board(root, b)
    probs, _ = sg.guard(root, board)
    check_any("since 解析不到 → 走缺口通道被拒", "比重算结果", probs)
    check_any("且红灯点名缺口本身（不是含糊地说判定失败）", "扫描面缺口", probs)

    #   (b) 重算**跑不起来**（一个 git 仓都扫不到）→ 走 rerr 通道。
    #   这条分支先前没有任何探针走到——不可达的分支与不存在的分支长得一样。
    nogit = root.parent / "nogit"
    nogit.mkdir(exist_ok=True)
    _w(nogit / ".workframe-config.json",
       json.dumps({"project_name": "ng", "signoff": {"self_signoff_enabled": True}},
                  ensure_ascii=False) + "\n")
    _w(nogit / ".claude/workframe-state/code-paths-index.json",
       json.dumps({"buckets": {"b": {"docs/**": ["a/b"]}}}, ensure_ascii=False) + "\n")
    nb = write_board(nogit, mk_task(head, signoff={"tier": "自签"}))
    probs, _ = sg.guard(nogit, nb)
    check_any("重算跑不起来 → 拒，不是放行", "验不了的自签", probs)
    check_any("红灯说清是哪一步没跑成", "一个 git 仓都没扫到", probs)


def test_enable_switch(head):
    """**缺键即开启**（默认 true）；显式 false 才关，关了写 `tier: 自签` 必拒。

    默认值在 2026-09-10 由用户拍板从 false 翻成 true。这组断言**必须跟着翻**——
    留着旧的「缺键即关闭」，它保护的就是已经不成立的行为，而套件照样全绿。
    """
    print("[开关] 缺键即开启 / 显式 false 才关")
    tmp = Path(tempfile.mkdtemp(prefix="wf-guard-off-"))
    try:
        root = tmp / "proj"
        root.mkdir()
        h = build_project(root, enabled=False)

        # ① 缺键 → 默认开启。**这一条是本次翻转的承重断言**
        cfg = root / ".workframe-config.json"
        base = json.loads(cfg.read_text(encoding="utf-8"))
        base.pop("signoff", None)
        _w(cfg, json.dumps(base, ensure_ascii=False) + NL)
        check("缺 signoff 键 → 开启（默认 true）", sg.self_signoff_enabled(root), True)
        # 配置文件整个不存在时同样落回默认，别只测缺键那一格
        check("默认值常量与实现同源（不是两处手写）",
              sg.self_signoff_enabled(root), sg.SELF_SIGNOFF_DEFAULT)

        # ② 显式 false → 关闭，且红灯指向配置
        _w(cfg, json.dumps({**base, "signoff": {"self_signoff_enabled": False}},
                           ensure_ascii=False) + NL)
        check("显式 false → 关闭", sg.self_signoff_enabled(root), False)
        board = write_board(root, mk_task(h, signoff={"tier": "自签"}))
        probs, _ = sg.guard(root, board)
        check_any("关闭时写自签 → 拒", "显式关闭", probs)
        check_any("红灯指向配置，不是让人改档位", "不要改这一行档位", probs)
        # 关闭状态下**不应该**再报重算类问题——两条红灯叠在一起会把人引向错误的排查方向
        check_none("关闭时不叠加重算类红灯", "比重算结果", probs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_internal_error_leaves_trace(head):
    """BUG-013 B9：hook 内部出错时，`signoff_guard_error` 必须真的落盘。

    这条事件是「静默失败与通过在终端里同形」的**唯一区分处**（`event-schema.json`
    原话）。它自己写不进去，那道区分就整个没了——而这正是本项目发生过的：
    `append_event` 曾用 `event_json(**fields)` 调一个首参为位置参数的函数，**恒抛
    TypeError**，再被自己的裸 `except` 吞掉；表面行为（exit 0 ＋ stderr 一行提示）
    与修好之后**一模一样**，唯一可分辨的就是事件计数。

    所以这组断言必须钉**计数**，不能钉 stderr。
    """
    print("[内部错误] 出错要留痕，不能静默")
    tmp = Path(tempfile.mkdtemp(prefix="wf-guard-err-"))
    try:
        root = tmp / "proj"
        root.mkdir()
        build_project(root, enabled=False)
        ev = sg.state_dir_of(root) / "events.jsonl"
        ev.parent.mkdir(parents=True, exist_ok=True)
        ev.write_text("", encoding="utf-8", newline="")

        sg.append_event(root, "signoff_guard_error", error="RuntimeError: probe")
        lines = [l for l in ev.read_text(encoding="utf-8").splitlines()
                 if "signoff_guard_error" in l]
        check("内部错误事件真的落盘（钉计数，不钉 stderr）", len(lines), 1)

        rec = json.loads(lines[-1]) if lines else {}
        # `ts` 是 schema 标 required 的字段。修 TypeError 之后仍可能缺它——两个缺陷
        # 在同一个函数里，只修一个，事件写出来也是不合 schema 的。
        check("事件含 schema 标 required 的 ts", "ts" in rec, True)
        check("事件 type 正确", rec.get("type"), "signoff_guard_error")
        check("事件带 harness（由 event_json 统一盖章）", "harness" in rec, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_enable_switch_non_mapping(head):
    """`signoff` 非映射时：**不抛、不 fail-open，落回默认值并在 stderr 点名**。

    `(cfg.get("signoff") or {}).get(...)` 只兜得住 None 与假值；写成字符串／数组／数字时
    `.get` 抛 AttributeError，而 `main()` 对内部异常按 fail-open 处理 ⇒ 声明 `tier: 自签`
    的看板被放行。那条路径必须堵死——**这一条与默认值是 true 还是 false 无关**。

    默认翻成 true 之后，「非映射 ⇒ 关闭」反而会让一个手误静默产生与缺键相反的行为，
    所以改成落回默认 ＋ 报一句。**这一格没有 fail-safe 方向可选**，选的是可观测。
    """
    print("[开关] signoff 非映射 → 落回默认并点名，不抛不 fail-open")
    tmp = Path(tempfile.mkdtemp(prefix="wf-guard-shape-"))
    try:
        root = tmp / "proj"
        root.mkdir()
        build_project(root, enabled=False)
        cfg = root / ".workframe-config.json"
        base = json.loads(cfg.read_text(encoding="utf-8"))
        import contextlib, io as _io
        for label, val in (("字符串", "yes"), ("数组", ["a"]), ("数字", 1)):
            base["signoff"] = val
            _w(cfg, json.dumps(base, ensure_ascii=False) + NL)
            err = _io.StringIO()
            # **抛异常也要算这条断言不合格**，不能让它把套件整个打断：缺陷的真实症状
            # 正是「抛 → main() fail-open 放行」；让异常冒泡的话，突变注入时套件崩在
            # traceback 上，红来自崩溃而不是这条断言，断言是死是活就分辨不出来了。
            try:
                with contextlib.redirect_stderr(err):
                    got = sg.self_signoff_enabled(root)
            except Exception as e:
                got = f"抛 {type(e).__name__} → main() 会 fail-open 放行"
            check(f"signoff 是{label} → 落回默认（不抛、不 fail-open）",
                  got, sg.SELF_SIGNOFF_DEFAULT)
            check_any(f"signoff 是{label} → stderr 点名 misconfiguration",
                      "不是映射", [err.getvalue()])
        # 反向对照：正常映射两个方向都读得出，别把开关一并判死
        base["signoff"] = {"self_signoff_enabled": True}
        _w(cfg, json.dumps(base, ensure_ascii=False) + NL)
        check("正常映射读得出 true", sg.self_signoff_enabled(root), True)
        base["signoff"] = {"self_signoff_enabled": False}
        _w(cfg, json.dumps(base, ensure_ascii=False) + NL)
        check("正常映射读得出 false（上面三条不是把开关判死）",
              sg.self_signoff_enabled(root), False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bash_advisory_and_prose_hint(root, head):
    """F1/F2 两条能力边界的断言层。

    - **F1**：Bash 侧只报不拦。`main()` 走 payload 里的 `tool_name` 分流，
      所以这一层直接喂 payload 跑 `main()`，测的是**退出码**而不是 `guard()` 的返回。
    - **F2**：散文形态不进把关面，但「main-executed ＋ 散文 ＋ completed」要报一句提示。
    """
    print("[F1/F2] Bash 只报不拦 ＋ 散文形态提示")

    # ---- F2：提示存在，且三个条件缺一即不报（不是恒真） ----
    base = {"id": "T-9", "status": "completed", "tags": ["main-executed"],
            "qa_signoff": "〔主 Claude 代签〕验了 A。"}
    check_any("main-executed ＋ 散文 ＋ completed → 报提示", "成色最低",
              sg.prose_signoff_hints([base]))
    for label, over in (("无 main-executed tag", {"tags": ["framework"]}),
                        ("状态不是 completed", {"status": "pending_qa"}),
                        ("签发块是映射形态（走把关面，不走提示）",
                         {"qa_signoff": {"tier": "自签"}})):
        t = dict(base); t.update(over)
        check("上述条件缺「" + label + "」→ 不报（提示不是恒真）",
              sg.prose_signoff_hints([t]), [])

    # ---- 前置过滤：映射形态才放行到完整解析 ----
    bp = root / "projects/board.yaml"
    _w(bp, "tasks:" + chr(10) + "  - id: T" + chr(10) + '    qa_signoff: "散文签注"' + chr(10))
    check("前置过滤：散文形态 → 不触发完整解析",
          sg.board_may_have_mapping_signoff(bp), False)
    _w(bp, "tasks:" + chr(10) + "  - id: T" + chr(10) + "    qa_signoff:" + chr(10)
       + "      tier: 自签" + chr(10))
    check("前置过滤：映射形态 → 放行到完整解析",
          sg.board_may_have_mapping_signoff(bp), True)
    _w(bp, "tasks:" + chr(10) + "  - id: T" + chr(10) + "    qa_signoff: |" + chr(10)
       + "      块标量也是散文" + chr(10))
    check("前置过滤：块标量 `|` 也算散文", sg.board_may_have_mapping_signoff(bp), False)

    # ---- F1：同一份违规看板，Edit 拦 / Bash 不拦 ----
    board = write_board(root, mk_task(head, signoff={"tier": "自签"}))
    def run_main(tool, **extra):
        payload = {"tool_name": tool}
        payload.update(extra)
        return _main_with_stdin(json.dumps(payload, ensure_ascii=False), root)

    rc_edit, err_edit = run_main("Write", tool_input={"file_path": str(board)})
    check("Edit/Write 侧：违规 → exit 2（拦）", rc_edit, 2)
    check_any("拦的文案说「被拒」", "本次写入被拒", [err_edit])

    rc_bash, err_bash = run_main("Bash", tool_input={"command": "python x.py"})
    check("Bash 侧：同一份违规看板 → exit 0（**只报不拦**）", rc_bash, 0)
    check_any("但问题必须报出来", "本次未阻断", [err_bash])
    check_any("且报的是同一批具名问题", "比重算结果", [err_bash])

    # 干净看板：两侧都 exit 0，且 Bash 侧不打任何警告（不是恒报）
    write_board(root, mk_task(head, signoff={"tier": "完整验证"}))
    rc, err = run_main("Bash", tool_input={"command": "ls"})
    check("Bash 侧：合规看板 → exit 0", rc, 0)
    check("且不打警告（提示不是恒报）", "本次未阻断" in err, False)


def main():
    print(f"[test] signoff-guard.py @ {SCRIPTS_DIR / 'signoff-guard.py'}")
    if shutil.which("git") is None:
        print("  ! 环境无 git，整套跳过（本套件全部用例都要真实 git 仓）")
        return 0
    try:
        import yaml  # noqa: F401
    except Exception:
        print("  ! 环境无 PyYAML，整套跳过——**这不是通过**")
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="wf-guard-test-"))
    try:
        root = tmp / "proj"
        root.mkdir()
        head = build_project(root, enabled=True)
        test_schema_layer(root, head)
        test_blast_surface(root, head)
        test_recompute_layer(root, head)
        test_bash_advisory_and_prose_hint(root, head)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    test_enable_switch(head)
    test_enable_switch_non_mapping(head)
    test_internal_error_leaves_trace(head)

    print()
    if FAILURES:
        print(f"✗ {len(FAILURES)} 个用例失败：")
        for f in FAILURES:
            print(f"    - {f}")
        return 1
    print("✓ All signoff-guard.py tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
