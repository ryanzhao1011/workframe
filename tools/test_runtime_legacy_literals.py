#!/usr/bin/env python3
"""运行态目录旧字面闸（`validate.check_runtime_dir_legacy_literals`）判定面的常驻单测。

被测：`_legacy_literal_forms`（字面集现算）、`_legacy_literal_scan`（扫描面过滤 / 下限 / 读失败 /
命中 / 豁免命中与过期）、`_changelog_segments`（CHANGELOG 分段），以及 check 函数把命中分派到两种
红灯文案。每格一个临时 fixture，只写系统临时目录。

守的形态：
1. 字面集形状：每个旧形态三种分隔各在；只属于旧形态的末段裸写在；新形态路径一个都不命中。
2. 每种旧字面插进 docs 文件各命中一次，报出的就是插进去的那个字面。
3. 扫描面：`plugins/` `docs/` 根 `README.md` `.claude-plugin/` 在；`tools/` 与根 `.gitignore` 不在；
   无扩展名 bin 入口、`.jsonl`、`.html` 不因后缀被筛掉。
4. CHANGELOG：未发布段末行命中、已发布段首行不命中、未发布段标题行命中、文件头不命中。
5. 读失败：非 UTF-8 与清单里不存在的文件都进 unreadable。
6. 下限四个组成各缺一个，floor 恰报那一个。
7. 豁免：特征串行被豁免；同文件另一行照报；特征串行没有旧字面 ⇒ 过期；路径写错 ⇒ 过期；
   文件级豁免覆盖全部行；文件级豁免的文件没有旧字面 ⇒ 过期。
8. 红灯分派：含 `plugin-root.txt` 的行走配方文案，其余走说明文案，互不串。
9. 处置文案按族分开：「改成新形态」只跟命中族，「改豁免」只跟过期族，下限与读失败两族都不带。
"""

import importlib.util
import shutil
import stat
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("wf_validate_under_test", REPO_ROOT / "tools" / "validate.py")
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'FAIL'}] {label}: got={got!r}" + ("" if ok else f" want={want!r}"))
    if not ok:
        FAILURES.append(label)


LITS, PROBLEM = V._legacy_literal_forms()
KINDS = ("state", "memory", "archive")
LEGACY = [V._rt(k, legacy=True) for k in KINDS]
NEW = [V._rt(k) for k in KINDS]
STATE_OLD = V._rt("state", legacy=True)
BARE = [lit for lit in LITS if "/" not in lit and "\\" not in lit]

BASE = {
    "plugins/core/bin/workframe-x": "#!/usr/bin/env python3\nprint('ok')\n",
    "plugins/core/templates/gitignore-template": "logs/\n",
    "docs/a.md": "# a\n",
    "README.md": "# r\n",
    "CHANGELOG.md": ("# Changelog\n\nintro\n\n## [9.9.9] — 未发布\n\n- u1\n- u2\n\n"
                     "## [1.0.0] — 2026-01-01\n\n- r1\n"),
}


def _rm(d):
    def _onerr(func, path, _exc):
        try:
            Path(path).chmod(stat.S_IWRITE)
            func(path)
        except Exception:
            pass
    shutil.rmtree(d, onerror=_onerr)


class Fixture:
    def __init__(self, files=None, drop=(), extra_rels=()):
        self.root = Path(tempfile.mkdtemp(prefix="wf-legacy-lit-"))
        content = dict(BASE)
        content.update(files or {})
        for rel in drop:
            content.pop(rel, None)
        for rel, body in content.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
        self.rels = sorted(content) + list(extra_rels)

    def scan(self, exempt=()):
        return V._legacy_literal_scan(self.root, self.rels, LITS, exempt)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        _rm(self.root)


def t1_forms():
    print("t1 字面集形状")
    check("problem", PROBLEM, None)
    for rel in LEGACY:
        head, tail = rel.split("/", 1)
        for form in (f"{head}/{tail}", f"{head}\\{tail}", f"{head}\\\\{tail}"):
            check(f"含 {form!r}", form in LITS, True)
    check("裸段至少一个", len(BARE) >= 1, True)
    check("字面总数 = 3×kinds + 裸段", len(LITS), 3 * len(KINDS) + len(BARE))
    for rel in NEW:
        probe = f"`{rel}/plugin-root.txt` 与 `{rel.replace('/', chr(92))}` 与 {rel.split('/', 1)[1]}/"
        check(f"新形态 {rel} 不命中", [lit for lit in LITS if lit in probe], [])


def t2_each_literal_hits():
    print("t2 每种旧字面各命中一次")
    for lit in LITS:
        with Fixture({"docs/a.md": f"# a\n前文\n见 `{lit}/x` 这里\n"}) as fx:
            r = fx.scan()
            check(f"{lit!r} hits", [(h[0], h[1], h[2]) for h in r["hits"]], [("docs/a.md", 3, lit)])


def t3_face():
    print("t3 扫描面")
    line = f"`{STATE_OLD}/events.jsonl`\n"
    files = {
        "tools/x.py": line,
        ".gitignore": line,
        ".claude-plugin/marketplace.json": line,
        "plugins/core/bin/workframe-y": "#!/usr/bin/env python3\n" + line,
        "plugins/core/templates/events-template.jsonl": line,
        "plugins/core/skills/s/demo.html": line,
    }
    with Fixture(files) as fx:
        r = fx.scan()
        got = sorted(h[0] for h in r["hits"])
        check("命中文件", got, [".claude-plugin/marketplace.json", "plugins/core/bin/workframe-y",
                               "plugins/core/skills/s/demo.html",
                               "plugins/core/templates/events-template.jsonl"])


def t4_changelog():
    print("t4 CHANGELOG 分段")
    lit = STATE_OLD
    cl = ("# Changelog\n\n" + f"文件头 {lit}\n\n"            # 3: 文件头
          + f"## [9.9.9] — 未发布 {lit}\n\n"                 # 5: 未发布段标题
          + "- u1\n"                                         # 7
          + f"- 未发布末行 {lit}\n"                          # 8: 已发布标题正上一行
          + "## [1.0.0] — 2026-01-01\n"                      # 9
          + f"- 已发布首行 {lit}\n")                         # 10: 已发布标题正下一行
    with Fixture({"CHANGELOG.md": cl}) as fx:
        r = fx.scan()
        check("命中行", sorted(h[1] for h in r["hits"] if h[0] == "CHANGELOG.md"), [5, 8])
    seg = V._changelog_segments(cl)
    check("分段标注", [(i, h, u) for i, _l, h, u in seg if i in (3, 5, 8, 9, 10)],
          [(3, False, False), (5, True, True), (8, False, True), (9, True, False), (10, False, False)])


def t5_unreadable():
    print("t5 读失败")
    with Fixture({"docs/b.md": b"\xff\xfe not utf8 \x80"}, extra_rels=["docs/missing.md"]) as fx:
        r = fx.scan()
        check("unreadable 文件", sorted(u.split("（")[0] for u in r["unreadable"]),
              ["docs/b.md", "docs/missing.md"])


def t6_floor():
    print("t6 扫描面下限")
    expect = {
        "plugins/core/bin/workframe-x": "plugins/*/bin/ 下无扩展名的入口",
        "plugins/core/templates/gitignore-template": "plugins/core/templates/gitignore-template",
        "docs/a.md": "docs/ 下的文件",
        "README.md": "根 README.md",
    }
    with Fixture() as fx:
        check("基线无缺", fx.scan()["floor"], [])
    for rel, msg in expect.items():
        with Fixture(drop=[rel]) as fx:
            check(f"缺 {rel}", fx.scan()["floor"], [msg])


def t7_exempt():
    print("t7 豁免")
    lit = STATE_OLD
    body = f"# m\n旧布局的项目仍在 `{lit}/` 原地\n另一行 `{lit}/x`\n只有特征串 布局对照\n"
    with Fixture({"docs/m.md": body}) as fx:
        r = fx.scan([("docs/m.md", "旧布局的项目仍在", "M", "t")])
        check("行级：特征串行豁免、另一行照报", [(h[0], h[1]) for h in r["hits"]], [("docs/m.md", 3)])
        check("行级：未过期", r["expired"], [])
        r = fx.scan([("docs/m.md", "布局对照", "M", "t")])
        check("特征串行无旧字面 ⇒ 过期", r["expired"], [("docs/m.md", "布局对照", "M")])
        r = fx.scan([("docs/M.md", "旧布局的项目仍在", "M", "t")])
        check("路径写错 ⇒ 过期", r["expired"], [("docs/M.md", "旧布局的项目仍在", "M")])
        r = fx.scan([("docs/m.md", None, "O", "t")])
        check("文件级：全部覆盖", (r["hits"], r["expired"]), ([], []))
    with Fixture({"docs/m.md": "# m\n干净\n"}) as fx:
        r = fx.scan([("docs/m.md", None, "O", "t")])
        check("文件级：无旧字面 ⇒ 过期", r["expired"], [("docs/m.md", None, "O")])


def t8_messages():
    print("t8 红灯分派")
    saved = (V.FRAMEWORK_ROOT, V._repo_text_files, V._LEGACY_LITERAL_EXEMPT)
    recipe = f'python "$(cat {STATE_OLD}/plugin-root.txt)/scripts/x.py"\n'
    prose = f"读 `{STATE_OLD}/events.jsonl`\n"
    try:
        for label, docs, want_recipe, want_prose in (
                ("只有配方", recipe, True, False),
                ("只有说明", prose, False, True),
                ("两种都有", recipe + prose, True, True),
                ("干净", "# a\n", False, False)):
            with Fixture({"docs/a.md": docs}) as fx:
                V.FRAMEWORK_ROOT = fx.root
                V._repo_text_files = lambda fx=fx: list(fx.rels)
                V._LEGACY_LITERAL_EXEMPT = ()
                ok, msg = V.check_runtime_dir_legacy_literals()
                msg = msg or ""
                check(f"{label}: ok", ok, not (want_recipe or want_prose))
                check(f"{label}: 配方文案", V._LEGACY_MSG_RECIPE in msg, want_recipe)
                check(f"{label}: 说明文案", V._LEGACY_MSG_PROSE in msg, want_prose)
    finally:
        V.FRAMEWORK_ROOT, V._repo_text_files, V._LEGACY_LITERAL_EXEMPT = saved


def t9_advice_per_family():
    print("t9 处置文案按族分开（不把非命中族送去改字面）")
    saved = (V.FRAMEWORK_ROOT, V._repo_text_files, V._LEGACY_LITERAL_EXEMPT)
    fix_hit = "改成 `_state_io.new_rel` 给出的新形态"
    fix_exp = "对着原句现状改这条豁免"
    try:
        cases = (
            ("只缺下限", {}, ["README.md"], (), False, False),
            ("只有读失败", {"docs/b.md": b"\xff\x80"}, [], (), False, False),
            ("只有过期", {}, [], (("docs/a.md", None, "O", "t"),), False, True),
            ("只有命中", {"docs/a.md": f"`{STATE_OLD}/x`\n"}, [], (), True, False),
        )
        for label, files, drop, exempt, want_hit, want_exp in cases:
            with Fixture(files, drop=drop) as fx:
                V.FRAMEWORK_ROOT = fx.root
                V._repo_text_files = lambda fx=fx: list(fx.rels)
                V._LEGACY_LITERAL_EXEMPT = exempt
                ok, msg = V.check_runtime_dir_legacy_literals()
                msg = msg or ""
                check(f"{label}: 红", ok, False)
                check(f"{label}: 改字面处置", fix_hit in msg, want_hit)
                check(f"{label}: 改豁免处置", fix_exp in msg, want_exp)
    finally:
        V.FRAMEWORK_ROOT, V._repo_text_files, V._LEGACY_LITERAL_EXEMPT = saved


def main():
    for t in (t1_forms, t2_each_literal_hits, t3_face, t4_changelog, t5_unreadable, t6_floor,
              t7_exempt, t8_messages, t9_advice_per_family):
        try:
            t()
        except Exception as e:
            print(f"  [FAIL] {t.__name__} 抛异常: {type(e).__name__}: {e}")
            FAILURES.append(t.__name__)
    if FAILURES:
        print(f"\n{len(FAILURES)} failed: {FAILURES}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
