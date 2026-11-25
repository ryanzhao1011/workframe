#!/usr/bin/env python3
"""Codex 角色新旧比较单测：`plugins/core/scripts/codex_roles.py` 的 `compare()` / `describe()` / `connected()`，
以及「任一角色文件被手改或未登记 → 整批不写」的写前判定。

两个方向都静默：把落后判成一致 → 旧装项目永远停在旧角色、door 与 doctor 都报无问题；把手改或未登记的文件
拿去和源比 → 「有差异」分不清是谁造成的，升级时可能覆盖掉用户的改动。所以每组都看分类结果本身：

- 刚生成 → 全部 current；只改生成头版本号 → header-only；生成头版本号与正文里的同一个版本串一起改 →
  仍是 instructions-changed（只归一版本槽，不全局替换版本串）
- 正文有变（账本跟着记）→ instructions-changed 且列出差异行；`.local.toml` 改了 → instructions-changed
- 手改（账本不跟）→ hand-edited；账本没登记 → unledgered；登记了文件没了 → missing；源里新加的角色 → absent
- 托管段：合法关门 → user-closed；段内手改 → hand-edited；自定义市场名 → 按项目实际启用表比，仍 current
- 账本丢了而段与角色还在 → connected 为真（不按未接门跳过）；空项目 → 为假
- 写前判定：任一角色被手改 → PreflightError(role-conflict)，项目里所有文件字节不变

夹具一律用虚构角色正文，不带任何真实项目的内容。

执行：python tools/test_codex_roles_compare.py（退出 0 且打印 all passed = 通过）
"""

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "plugins" / "core" / "scripts"))
import codex_roles as cr  # noqa: E402

FAILS = []


def check(label, got, want):
    if got == want:
        print(f"[ok] {label}")
    else:
        print(f"[FAIL] {label}: got {got!r}, want {want!r}")
        FAILS.append(label)


AGENT = """---
name: {name}
description: 虚构角色 {name}
---

# 虚构角色 {name}

兼容 v0.9.0 的一句说明。
第二行正文。
"""


def make_plugin(base, version="2.0.0", names=("alpha", "beta"), body_extra=""):
    root = base / "plugin"
    (root / "agents").mkdir(parents=True, exist_ok=True)
    (root / ".claude-plugin").mkdir(exist_ok=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "core", "version": version}),
                                                         encoding="utf-8", newline="")
    for n in names:
        (root / "agents" / f"{n}.md").write_text(AGENT.format(name=n) + body_extra, encoding="utf-8", newline="")
    return root, version


def states(items):
    return {Path(it["rel"]).name: it["state"] for it in items}


def write(path, text):
    path.write_bytes(text.encode("utf-8"))


def relabel(project, rel, old, new, *, ledger_follows):
    """把落盘件里的 old 换成 new；ledger_follows=True 时账本跟着记（模拟旧版生成器的原样产物）。"""
    p = project / rel
    text = p.read_text(encoding="utf-8").replace(old, new)
    write(p, text)
    if ledger_follows:
        led = cr.load_ledger(project)
        content = cr._extract_block(text) if rel == cr.CONFIG_REL.as_posix() else text
        led["files"][rel]["sha256"] = cr.sha256_bytes(content.encode("utf-8"))
        cr.save_ledger(project, led)


def fresh(tmp, **kw):
    project = tmp / "proj"
    project.mkdir()
    root, ver = make_plugin(tmp, **kw)
    cr.generate(project, root, ver, marketplace="mkt")
    return project, root, ver


def digest_tree(d):
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file()}


def run(fn):
    tmp = Path(tempfile.mkdtemp(prefix="wf-roles-cmp-"))
    try:
        fn(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_fresh_current(tmp):
    project, root, ver = fresh(tmp)
    s = states(cr.compare(project, root, ver))
    check("刚生成：五份全部 current", set(s.values()), {cr.STATE_CURRENT})
    check("刚生成：覆盖两份角色＋托管段", sorted(s), ["alpha.toml", "beta.toml", "config.toml"])
    stale, conflict, other = cr.describe(cr.compare(project, root, ver), ver)
    check("刚生成：describe 无落后无冲突", (stale, conflict, other), ([], [], []))


def t_header_only(tmp):
    project, root, ver = fresh(tmp)
    relabel(project, ".codex/roles/alpha.toml", "(v2.0.0)", "(v1.5.0)", ledger_follows=True)
    relabel(project, ".codex/config.toml", "core plugin v2.0.0", "core plugin v1.5.0", ledger_follows=True)
    items = cr.compare(project, root, ver)
    s = states(items)
    check("只改生成头版本号：角色 header-only", s["alpha.toml"], cr.STATE_HEADER_ONLY)
    check("只改生成头版本号：托管段 header-only", s["config.toml"], cr.STATE_HEADER_ONLY)
    check("只改生成头版本号：未动的角色仍 current", s["beta.toml"], cr.STATE_CURRENT)
    rec = {Path(it["rel"]).name: it["recorded"] for it in items}
    check("读出落盘版本号", (rec["alpha.toml"], rec["config.toml"]), ("1.5.0", "1.5.0"))
    stale, _c, _o = cr.describe(items, ver)
    check("describe 写出停在哪一版", any("停在 v1.5.0" in ln and "只差版本头" in ln for ln in stale), True)


def t_body_version_string_not_swallowed(tmp):
    project, root, ver = fresh(tmp)
    rel = ".codex/roles/alpha.toml"
    relabel(project, rel, "(v2.0.0)", "(v1.5.0)", ledger_follows=True)
    relabel(project, rel, "兼容 v0.9.0", "兼容 v1.5.0", ledger_follows=True)
    it = next(i for i in cr.compare(project, root, ver) if i["rel"] == rel)
    check("正文版本串与生成头同改：不被归一吞掉", it["state"], cr.STATE_CHANGED)
    check("差异行只含正文那一句", sorted(it["diff"]), ["+兼容 v0.9.0 的一句说明。", "-兼容 v1.5.0 的一句说明。"])


def t_body_mentions_current_version(tmp):
    """源正文本身写着当前版本号：旧件生成头 v1.5.0、正文「升级到 v1.5.0」，新源正文「升级到 v2.0.0」。
    全局替换版本串会把两边正文都归成占位、误判 header-only。"""
    project, root, ver = fresh(tmp, body_extra="升级到 v2.0.0 后生效。\n")
    rel = ".codex/roles/alpha.toml"
    relabel(project, rel, "v2.0.0", "v1.5.0", ledger_follows=True)       # 头与正文一起回到旧版
    it = next(i for i in cr.compare(project, root, ver) if i["rel"] == rel)
    check("正文写着当前版本号：仍判 instructions-changed", it["state"], cr.STATE_CHANGED)


def t_body_changed(tmp):
    project, root, ver = fresh(tmp)
    rel = ".codex/roles/beta.toml"
    relabel(project, rel, "第二行正文。", "旧版第二行。", ledger_follows=True)
    it = next(i for i in cr.compare(project, root, ver) if i["rel"] == rel)
    check("正文有变（账本跟着记）：instructions-changed", it["state"], cr.STATE_CHANGED)
    check("差异行两行", len(it["diff"]), 2)
    # 托管段 description 有变
    relabel(project, ".codex/config.toml", "虚构角色 alpha\"", "旧描述 alpha\"", ledger_follows=True)
    s = states(cr.compare(project, root, ver))
    check("托管段 description 有变：instructions-changed", s["config.toml"], cr.STATE_CHANGED)


def t_line_order_and_dup(tmp):
    project, root, ver = fresh(tmp)
    rel = ".codex/roles/alpha.toml"
    p = project / rel
    lines = p.read_text(encoding="utf-8").split("\n")
    i = lines.index("第二行正文。")
    lines[i - 1], lines[i] = lines[i], lines[i - 1]      # 换序
    text = "\n".join(lines)
    write(p, text)
    led = cr.load_ledger(project)
    led["files"][rel]["sha256"] = cr.sha256_bytes(text.encode("utf-8"))
    cr.save_ledger(project, led)
    it = next(i for i in cr.compare(project, root, ver) if i["rel"] == rel)
    check("只换两行顺序：instructions-changed", it["state"], cr.STATE_CHANGED)


def t_local_override(tmp):
    project, root, ver = fresh(tmp)
    write(project / ".codex/roles/alpha.local.toml", 'developer_instructions = "本项目补一句"\n')
    s = states(cr.compare(project, root, ver))
    check(".local.toml 新增了定制、没重跑：alpha instructions-changed", s["alpha.toml"], cr.STATE_CHANGED)
    stale, _c, _o = cr.describe(cr.compare(project, root, ver), ver)
    check("有定制的那一份文案写明定制", any("alpha.toml" in ln and ".local.toml 定制" in ln for ln in stale), True)
    relabel(project, ".codex/roles/beta.toml", "第二行正文。", "旧版第二行。", ledger_follows=True)
    relabel(project, ".codex/config.toml", "虚构角色 beta\"", "旧描述 beta\"", ledger_follows=True)
    stale, _c, _o = cr.describe(cr.compare(project, root, ver), ver)
    plain = [ln for ln in stale if "beta.toml" in ln or "config.toml" in ln]
    check("没有定制的角色与托管段不提定制", (len(plain), any(".local.toml" in ln for ln in plain)), (2, False))
    relabel(project, ".codex/roles/beta.toml", "旧版第二行。", "第二行正文。", ledger_follows=True)
    relabel(project, ".codex/config.toml", "旧描述 beta\"", "虚构角色 beta\"", ledger_follows=True)
    s = states(cr.compare(project, root, ver))
    check(".local.toml 不影响别的角色", s["beta.toml"], cr.STATE_CURRENT)
    write(project / ".codex/roles/beta.local.toml", '# 只有注释\n')
    check(".local.toml 只有注释：仍 current", states(cr.compare(project, root, ver))["beta.toml"], cr.STATE_CURRENT)


def t_ownership(tmp):
    project, root, ver = fresh(tmp, names=("alpha", "beta"))
    # 手改：账本不跟
    relabel(project, ".codex/roles/alpha.toml", "第二行正文。", "我改的。", ledger_follows=False)
    # 未登记：从账本里去掉 beta
    led = cr.load_ledger(project)
    del led["files"][".codex/roles/beta.toml"]
    cr.save_ledger(project, led)
    s = states(cr.compare(project, root, ver))
    check("手改：hand-edited", s["alpha.toml"], cr.STATE_HAND_EDITED)
    check("账本没登记：unledgered", s["beta.toml"], cr.STATE_UNLEDGERED)
    _st, conflict, _o = cr.describe(cr.compare(project, root, ver), ver)
    check("describe 把两份都列成冲突", len(conflict), 2)


def t_missing_absent(tmp):
    project, root, ver = fresh(tmp, names=("alpha", "beta"))
    (project / ".codex/roles/beta.toml").unlink()
    (root / "agents" / "gamma.md").write_text(AGENT.format(name="gamma"), encoding="utf-8", newline="")
    s = states(cr.compare(project, root, ver))
    check("登记了文件不在：missing", s["beta.toml"], cr.STATE_MISSING)
    check("源里新加的角色：absent", s["gamma.toml"], cr.STATE_ABSENT)


def t_block_states(tmp):
    project, root, ver = fresh(tmp)
    cfg = project / ".codex/config.toml"
    write(cfg, cfg.read_text(encoding="utf-8").replace("enabled = true", "enabled = false"))
    check("合法关门：user-closed", states(cr.compare(project, root, ver))["config.toml"], cr.STATE_USER_CLOSED)
    _s, _c, other = cr.describe(cr.compare(project, root, ver), ver)
    check("关门只出说明行", len(other), 1)
    write(cfg, cfg.read_text(encoding="utf-8").replace("enabled = false", "enabled = true")
          .replace('config_file = "roles/alpha.toml"', 'config_file = "roles/alpha.toml"\nmodel = "x"'))
    check("段内手改：hand-edited", states(cr.compare(project, root, ver))["config.toml"], cr.STATE_HAND_EDITED)


def t_custom_marketplace(tmp):
    project, root, ver = fresh(tmp)          # 生成时市场名 mkt
    check("项目启用表的 id", cr.block_plugin_id(project), "core@mkt")
    check("缺省按项目实际市场比：托管段 current",
          states(cr.compare(project, root, ver))["config.toml"], cr.STATE_CURRENT)
    check("显式给错市场名：托管段判正文有变",
          states(cr.compare(project, root, ver, "workframe"))["config.toml"], cr.STATE_CHANGED)


def t_connected(tmp):
    project, root, ver = fresh(tmp)
    (project / cr.LEDGER_REL).unlink()
    check("账本丢了、段与角色还在：connected 为真", cr.connected(project), True)
    s = states(cr.compare(project, root, ver))
    check("账本丢了：角色 unledgered", s["alpha.toml"], cr.STATE_UNLEDGERED)
    check("账本丢了：托管段 unledgered", s["config.toml"], cr.STATE_UNLEDGERED)
    empty = tmp / "empty"
    empty.mkdir()
    check("空项目：connected 为假", cr.connected(empty), False)
    (empty / ".codex" / "roles").mkdir(parents=True)
    write(empty / ".codex/roles/x.local.toml", "")
    check("只有 .local.toml：connected 为假", cr.connected(empty), False)
    write(empty / ".codex/roles/x.toml", "")
    check("有生成物形态的 role TOML：connected 为真", cr.connected(empty), True)


def t_whole_batch_refusal(tmp):
    project, root, ver = fresh(tmp)
    relabel(project, ".codex/roles/alpha.toml", "第二行正文。", "我改的。", ledger_follows=False)
    root2, ver2 = make_plugin(tmp / "v3", version="3.0.0", body_extra="新版多一行。\n")
    before = digest_tree(project)
    try:
        cr.generate(project, root2, ver2, marketplace="mkt")
        check("有手改：整批拒绝", "no error", cr.KIND_ROLE_CONFLICT)
    except cr.PreflightError as e:
        check("有手改：整批拒绝（role-conflict）", e.kind, cr.KIND_ROLE_CONFLICT)
        check("拒绝文案点名那一份", ".codex/roles/alpha.toml" in str(e), True)
    check("有手改：项目里所有文件字节不变", digest_tree(project), before)
    try:
        cr.preflight(project, root2, ver2, "mkt")
        check("preflight 同样拒绝", "no error", cr.KIND_ROLE_CONFLICT)
    except cr.PreflightError as e:
        check("preflight 同样拒绝（role-conflict）", e.kind, cr.KIND_ROLE_CONFLICT)
    # 去掉手改后升级照常：只差版本头与正文有变的都被刷新
    relabel(project, ".codex/roles/alpha.toml", "我改的。", "第二行正文。", ledger_follows=False)
    cr.generate(project, root2, ver2, marketplace="mkt")
    check("冲突消除后升级：全部 current", set(states(cr.compare(project, root2, ver2)).values()), {cr.STATE_CURRENT})


def t_crlf_override_and_separator_lines(tmp):
    project, root, ver = fresh(tmp)
    # .local.toml 用基本字符串写 \r\n：渲染出的正文自己带 CR，刚生成也必须判 current
    (project / ".codex/roles/alpha.local.toml").write_bytes(b'developer_instructions = "x\\r\\ny"\n')
    cr.generate(project, root, ver, marketplace="mkt")
    check("定制值带 CRLF：刚生成判 current", states(cr.compare(project, root, ver))["alpha.toml"], cr.STATE_CURRENT)
    # 正文里以 --- 起头的行（Markdown 分隔线）有变：摘要不能为空
    (root / "agents" / "beta.md").write_text(AGENT.format(name="beta") + "\n---\n尾段。\n", encoding="utf-8", newline="")
    cr.generate(project, root, ver, marketplace="mkt")
    (root / "agents" / "beta.md").write_text(AGENT.format(name="beta") + "\n----\n尾段。\n", encoding="utf-8", newline="")
    it = next(i for i in cr.compare(project, root, ver) if i["rel"].endswith("beta.toml"))
    check("分隔线 --- 改成 ----：instructions-changed", it["state"], cr.STATE_CHANGED)
    check("分隔线行出现在差异摘要里", sorted(it["diff"]), ["+----", "----"])


def t_missing_is_stale_not_conflict(tmp):
    project, root, ver = fresh(tmp)
    (project / ".codex/roles/beta.toml").unlink()
    stale, conflict, _o = cr.describe(cr.compare(project, root, ver), ver)
    check("登记了文件不在：归落后（重新生成会补回）", (len(stale), len(conflict)), (1, 0))
    cr.generate(project, root, ver, marketplace="mkt")
    check("登记了文件不在：生成照常补回、不整批拒绝", (project / ".codex/roles/beta.toml").is_file(), True)


def t_block_edit_also_names_role_conflicts(tmp):
    project, root, ver = fresh(tmp)
    relabel(project, ".codex/roles/alpha.toml", "第二行正文。", "我改的。", ledger_follows=False)
    cfg = project / ".codex/config.toml"
    write(cfg, cfg.read_text(encoding="utf-8").replace('config_file = "roles/beta.toml"',
                                                       'config_file = "roles/beta.toml"\nmodel = "x"'))
    try:
        cr.preflight(project, root, ver, "mkt")
        check("段与角色同时手改：预检拒绝", "no error", cr.KIND_BLOCK_EDITED)
    except cr.PreflightError as e:
        check("段与角色同时手改：一次点名两处", (e.kind, ".codex/roles/alpha.toml" in str(e)),
              (cr.KIND_BLOCK_EDITED, True))


def _door():
    """import 门脚本：import 期封闭（不落到真 codex、不读真 CODEX_HOME），同 test_workframe_door 的做法。"""
    import os
    os.environ["WF_CODEX_EXE"] = str(Path(tempfile.gettempdir()) / "wf-no-such-codex.exe")
    os.environ["CODEX_HOME"] = tempfile.mkdtemp(prefix="wf-codex-home-")
    import workframe_door
    return workframe_door


def t_door_check_surfaces_unledgered(tmp):
    project, root, ver = fresh(tmp)
    led = cr.load_ledger(project)
    del led["files"][".codex/roles/beta.toml"]
    cr.save_ledger(project, led)
    door = _door()
    verified = cr.verify(project)
    problems, info = door.roles_freshness(project, root, verified)
    check("账本缺一条登记：verify 看不见它（前提）", any("beta.toml" in p for p in verified), False)
    check("账本缺一条登记：--check 的问题行点名它", any("beta.toml" in p for p in problems), True)
    check("账本缺一条登记：不再报「一致」", any("一致" in ln for ln in info), False)
    # 已关门：不给升级出路
    project2 = tmp / "p2"
    project2.mkdir()
    cr.generate(project2, root, ver, marketplace="mkt")
    relabel(project2, ".codex/roles/alpha.toml", "(v2.0.0)", "(v1.5.0)", ledger_follows=True)
    c2 = project2 / ".codex/config.toml"
    write(c2, c2.read_text(encoding="utf-8").replace("enabled = true", "enabled = false"))
    _p, info2 = door.roles_freshness(project2, root)
    check("已关门：不建议 --upgrade", any("--upgrade" in ln for ln in info2), False)
    # 账本 JSON 坏了：只报未核实，不抛
    (project / cr.LEDGER_REL).write_text("{oops", encoding="utf-8", newline="")
    p3, i3 = door.roles_freshness(project, root)
    check("账本坏了：未核实、不抛", (p3, any("未核实" in ln for ln in i3)), ([], True))


def t_door_never_says_consistent_with_conflicts(tmp):
    project, root, ver = fresh(tmp)
    relabel(project, ".codex/roles/alpha.toml", "第二行正文。", "我改的。", ledger_follows=False)
    door = _door()
    verified = cr.verify(project)
    problems, info = door.roles_freshness(project, root, verified)
    check("只有手改：verify 已点名（前提）", any("alpha.toml" in p for p in verified), True)
    check("只有手改：不重复点名、也不说「一致」", (problems, any("一致" in ln for ln in info)), ([], False))


def t_doctor_closed_door_keeps_conflicts(tmp):
    import workframe_doctor as doc
    project, root, ver = fresh(tmp)
    (project / ".workframe-config.json").write_text("{}", encoding="utf-8", newline="")
    P = doc.Paths(project)
    P.state.mkdir(parents=True, exist_ok=True)
    (P.state / "plugin-root.codex.txt").write_text(root.as_posix() + "\n", encoding="utf-8", newline="")
    run_doc = lambda: doc.check_codex_roles({"paths": P})
    check("doctor：刚生成一致", [lv for lv, _m in run_doc()], ["ok"])
    relabel(project, ".codex/roles/beta.toml", "(v2.0.0)", "(v1.5.0)", ledger_follows=True)
    cfg = project / ".codex/config.toml"
    write(cfg, cfg.read_text(encoding="utf-8").replace("enabled = true", "enabled = false"))
    rows = run_doc()
    check("doctor：已关门、只落后 → 只有说明、不建议升级",
          ([lv for lv, _m in rows], any("--upgrade" in m for _lv, m in rows)), (["info"], False))
    relabel(project, ".codex/roles/alpha.toml", "第二行正文。", "我改的。", ledger_follows=False)
    rows = run_doc()
    check("doctor：已关门＋手改 → 冲突仍 warn", any(lv == "warn" and "alpha.toml" in m for lv, m in rows), True)
    led = cr.load_ledger(project)
    del led["files"][".codex/roles/beta.toml"]
    cr.save_ledger(project, led)
    check("doctor：已关门＋缺登记 → 冲突仍 warn", any(lv == "warn" and "beta.toml" in m for lv, m in run_doc()), True)
    # 指针在、但读取抛 OSError（权限 / 占用）：报未核实，不冒泡成「检查自身异常」
    from unittest import mock
    real = Path.read_text

    def denied(self, *a, **k):
        if self.name == "plugin-root.codex.txt":
            raise PermissionError("probe")
        return real(self, *a, **k)
    with mock.patch.object(Path, "read_text", denied):
        try:
            rows = run_doc()
        except OSError as e:
            rows = [("<raised>", str(e))]
    check("doctor：指针读取被拒 → 未核实、不抛", ([lv for lv, _m in rows], "未核实" in rows[0][1]), (["info"], True))


def main():
    _door()        # 先 import：门脚本 import 期会换掉 stdout，之后再 import 会丢掉已缓冲的输出
    for fn in (t_fresh_current, t_header_only, t_body_version_string_not_swallowed, t_body_mentions_current_version,
               t_body_changed,
               t_line_order_and_dup, t_local_override, t_ownership, t_missing_absent, t_block_states,
               t_custom_marketplace, t_connected, t_whole_batch_refusal, t_crlf_override_and_separator_lines,
               t_missing_is_stale_not_conflict, t_block_edit_also_names_role_conflicts, t_door_check_surfaces_unledgered,
               t_door_never_says_consistent_with_conflicts, t_doctor_closed_door_keeps_conflicts):
        run(fn)
    if FAILS:
        print(f"{len(FAILS)} failed")
        return 1
    print("all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
