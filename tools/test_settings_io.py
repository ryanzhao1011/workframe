#!/usr/bin/env python3
"""`_settings_io` 的常驻单测——**框架第一次用代码写 settings**，这份文件是它唯一的常驻证据。

爆炸半径档 4：被测的三件事（读的 BOM 口径 / merge 非覆盖 / 写的备份与回滚）判错时，
装机照样 exit 0、doctor 也未必红，而用户 `.claude/settings.json` 里原有的 `hooks`、
`permissions.allow` 已经没了——那是 CC 侧「改错框架整个不加载」的同一格。

**真实形态从哪来**：`REAL_SHAPES` 里两份 fixture 抄自本机真实项目的 settings 形态
（一份带 `env` / `settings` / `hooks` 三个顶层键含三条 hook，一份带 `permissions.allow`
七条），**字面写死在本文件**——不从任何机器上的真文件读：单测要在别人的机器与 CI 上
同样成立，而「本机恰好有这个文件」不是可移植的前提。

每组断言的失败方式（交付时逐条打过）：
  1. 读：缺文件返 `{}` / BOM 被吃掉 / 空文件不当成 `{}` / 坏 JSON 与非对象各自抛
  2. merge：两份真实形态的**每一个原有顶层键与其内容**逐个比对（不是只数键的个数）
  3. merge 的不覆盖档：同名市场源不同 ⇒ 原声明一字不动 ＋ 出 note
  4. 写：备份生成 / 产物零 BOM / 换行恒 LF / 回读一致
  5. 回滚：回读不一致时原文件按备份还原；新建路径写失败时不留半写文件
"""

import copy
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "plugins" / "core" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.dont_write_bytecode = True

import _settings_io as S  # noqa: E402

FAILURES = []

SRC_DIR = {"source": "directory", "path": "C:\\framework\\claude-workframe"}
SRC_GH = {"source": "github", "repo": "owner/repo"}

# 两份真实形态（形状抄自本机现有项目，路径与业务内容已换成占位值）
REAL_SHAPES = {
    "env_settings_hooks": {
        "env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
        "settings": {"teammateMode": "always"},
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "echo a"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "echo b"}]}],
            "SubagentStop": [{"hooks": [{"type": "command", "command": "echo c"}]}],
        },
    },
    "permissions_allow7": {
        "permissions": {"allow": [f"Bash(cmd{i}:*)" for i in range(7)]},
        "enabledPlugins": {"core@workframe": True},
    },
}


def check(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'FAIL'}] {label}: got={got!r}" + ("" if ok else f" want={want!r}"))
    if not ok:
        FAILURES.append(label)


def check_raises(label, fn, needle=None):
    try:
        fn()
    except S.SettingsError as e:
        hit = needle is None or needle in str(e)
        print(f"  [{'ok' if hit else 'FAIL'}] {label}: SettingsError({str(e)[:90]!r})")
        if not hit:
            FAILURES.append(f"{label}（文案未含 {needle!r}）")
        return
    except Exception as e:
        print(f"  [FAIL] {label}: 抛了 {type(e).__name__}({e}) 而不是 SettingsError")
        FAILURES.append(label)
        return
    print(f"  [FAIL] {label}: 没抛异常")
    FAILURES.append(label)


# ---- 1. 读 ----

def test_read(tmp):
    print("[1] read_settings")
    check("缺文件 ⇒ 空对象", S.read_settings(tmp / "nope.json"), {})

    bom = tmp / "bom.json"
    bom.write_bytes("\ufeff".encode("utf-8") + b'{"a": 1}')
    check("BOM 被吃掉、内容读得到", S.read_settings(bom), {"a": 1})

    nobom = tmp / "nobom.json"
    nobom.write_bytes(b'{"a": 1}')
    check("无 BOM 同样读得到", S.read_settings(nobom), {"a": 1})

    empty = tmp / "empty.json"
    empty.write_bytes(b"")
    check_raises("空文件不当成 {}", lambda: S.read_settings(empty), "空文件")

    blank = tmp / "blank.json"
    blank.write_bytes(b"  \r\n ")
    check_raises("全空白同上", lambda: S.read_settings(blank), "空文件")

    bad = tmp / "bad.json"
    bad.write_bytes(b'{"a": }')
    check_raises("坏 JSON ⇒ 报行列", lambda: S.read_settings(bad), "JSON 语法错误")

    arr = tmp / "arr.json"
    arr.write_bytes(b'[1, 2]')
    check_raises("顶层非对象 ⇒ 抛", lambda: S.read_settings(arr), "顶层不是 JSON 对象")

    binf = tmp / "bin.json"
    binf.write_bytes(b"\xff\xfe{\x00")
    check_raises("非 UTF-8 ⇒ 抛", lambda: S.read_settings(binf), "不是 UTF-8")


# ---- 2. merge 非覆盖：逐键比对，不只数个数 ----

def test_merge_preserves_real_shapes():
    print("[2] merge_project_subscription 对真实形态非覆盖")
    for tag, shape in REAL_SHAPES.items():
        before = copy.deepcopy(shape)
        merged, notes = S.merge_project_subscription(shape, "workframe", SRC_DIR)
        check(f"{tag}: 入参未被就地改动", shape, before)
        for key, val in before.items():
            if key == "enabledPlugins":
                # 这个键本模块会写，逐条比对留给下面的专项断言
                continue
            check(f"{tag}: 原有顶层键 {key} 原样保留", merged.get(key), val)
        check(f"{tag}: 写进了市场声明", merged["extraKnownMarketplaces"]["workframe"],
              {"source": SRC_DIR})
        check(f"{tag}: 写进了启用位", merged["enabledPlugins"]["core@workframe"], True)
        check(f"{tag}: 无多余 note", notes, [])

    # `permissions_allow7` 的 enabledPlugins 原有条目：本次只加不减
    shape = REAL_SHAPES["permissions_allow7"]
    merged, _ = S.merge_project_subscription(shape, "other", SRC_GH)
    check("已有 core@workframe 条目不被新市场挤掉",
          sorted(merged["enabledPlugins"]), ["core@other", "core@workframe"])
    check("permissions.allow 七条一条不少",
          merged["permissions"]["allow"], shape["permissions"]["allow"])


def test_merge_edges():
    print("[3] merge_project_subscription 边界")
    merged, notes = S.merge_project_subscription({}, "workframe", SRC_DIR)
    check("空 settings ⇒ 两项都写", merged,
          {"extraKnownMarketplaces": {"workframe": {"source": SRC_DIR}},
           "enabledPlugins": {"core@workframe": True}})
    check("空 settings 无 note", notes, [])

    # 幂等：同源重跑，结果一字不变、无 note
    again, notes2 = S.merge_project_subscription(merged, "workframe", SRC_DIR)
    check("同源重跑幂等", again, merged)
    check("同源重跑无 note", notes2, [])

    # 同名不同源 ⇒ 原声明一字不动 + 出 note
    conflict, notes3 = S.merge_project_subscription(merged, "workframe", SRC_GH)
    check("同名不同源：原声明不被覆盖",
          conflict["extraKnownMarketplaces"]["workframe"], {"source": SRC_DIR})
    check("同名不同源：出一条 note", len(notes3), 1)
    check("note 点名了两个源", all(x in notes3[0] for x in ("directory", "github")), True)

    # 条目上的其他键保留（未来 CC 加字段时不被抹掉）
    withextra = {"extraKnownMarketplaces": {"workframe": {"source": SRC_DIR, "pinned": "1.0"}}}
    m4, _ = S.merge_project_subscription(withextra, "workframe", SRC_DIR)
    check("条目的其他键保留", m4["extraKnownMarketplaces"]["workframe"].get("pinned"), "1.0")

    # 原条目不是对象（用户手写坏了 / 未来 CC 换了形态）⇒ 按本次声明重写，但**必须出 note**。
    # 少了这条 note 就是静默改写用户的既有条目，与本模块在槽位层立的「不静默改写」自相矛盾
    weird = {"extraKnownMarketplaces": {"workframe": "some-string"}}
    m6, notes6 = S.merge_project_subscription(weird, "workframe", SRC_DIR)
    check("原条目非对象：按本次声明重写", m6["extraKnownMarketplaces"]["workframe"],
          {"source": SRC_DIR})
    check("原条目非对象：出一条 note", len(notes6), 1)
    check("note 点名了原条目的类型", "str" in notes6[0], True)

    # 曾被显式停用 ⇒ 改回 true 且出 note
    off = {"enabledPlugins": {"core@workframe": False}}
    m5, notes5 = S.merge_project_subscription(off, "workframe", SRC_DIR)
    check("false ⇒ 改回 true", m5["enabledPlugins"]["core@workframe"], True)
    check("改回 true 出 note", len(notes5), 1)

    # 槽位不是对象 ⇒ 不静默改写
    check_raises("extraKnownMarketplaces 非对象 ⇒ 抛",
                 lambda: S.merge_project_subscription(
                     {"extraKnownMarketplaces": []}, "workframe", SRC_DIR),
                 "不是对象")
    check_raises("enabledPlugins 非对象 ⇒ 抛",
                 lambda: S.merge_project_subscription(
                     {"enabledPlugins": "yes"}, "workframe", SRC_DIR),
                 "不是对象")
    check_raises("空市场名 ⇒ 抛", lambda: S.merge_project_subscription({}, "  ", SRC_DIR),
                 "市场名")
    check_raises("源不是对象 ⇒ 抛", lambda: S.merge_project_subscription({}, "workframe", "x"),
                 "市场源对象")


def test_known_marketplaces():
    print("[4] merge_known_marketplaces")
    now = datetime(2026, 9, 21, 8, 30, 15, 123456, tzinfo=timezone.utc)
    existing = {"claude-plugins-official": {"source": {"source": "github",
                                                       "repo": "anthropics/claude-plugins-official"},
                                            "installLocation": "C:\\x",
                                            "lastUpdated": "2026-09-21T02:03:11.075Z"}}
    merged, notes = S.merge_known_marketplaces(existing, "workframe", SRC_DIR,
                                               "C:\\framework\\claude-workframe", now)
    check("别人的市场条目原样保留", merged["claude-plugins-official"],
          existing["claude-plugins-official"])
    check("本市场三字段齐全", sorted(merged["workframe"]),
          ["installLocation", "lastUpdated", "source"])
    check("lastUpdated 是毫秒 Z 形态", merged["workframe"]["lastUpdated"],
          "2026-09-21T08:30:15.123Z")
    check("无 note", notes, [])

    m2, notes2 = S.merge_known_marketplaces(merged, "workframe", SRC_GH, "C:\\cache", now)
    check("同名不同源：注册不被改", m2["workframe"], merged["workframe"])
    check("同名不同源：出 note", len(notes2), 1)

    check_raises("installLocation 为空 ⇒ 抛",
                 lambda: S.merge_known_marketplaces({}, "workframe", SRC_DIR, "  "),
                 "installLocation")

    # lastUpdated 只在别的字段真变了的时候才刷——否则重跑永远「有变化」，
    # 于是每跑一次都真写、真备份（实证过连跑 3 次积 3 份）
    later = datetime(2026, 9, 22, 1, 2, 3, 456789, tzinfo=timezone.utc)
    same, _ = S.merge_known_marketplaces(merged, "workframe", SRC_DIR,
                                         "C:\\framework\\claude-workframe", later)
    check("同源同落地目录重跑：lastUpdated 不刷", same["workframe"]["lastUpdated"],
          merged["workframe"]["lastUpdated"])
    check("同源同落地目录重跑：整条 merge 结果逐字相同", same, merged)
    moved, _ = S.merge_known_marketplaces(merged, "workframe", SRC_DIR, "C:\\别处", later)
    check("落地目录变了：lastUpdated 刷新", moved["workframe"]["lastUpdated"],
          "2026-09-22T01:02:03.456Z")

    # 非 UTC 的 aware 时间要先归一到 UTC 再格式化（生产路径恒传 UTC，所以这一半
    # 只有专门喂一个别的时区才测得到）
    tz8 = timezone(timedelta(hours=8))
    check("非 UTC 输入按 UTC 归一", S.iso_millis(datetime(2026, 9, 21, 16, 30, 15, 123000, tzinfo=tz8)),
          "2026-09-21T08:30:15.123Z")


# ---- 5. 写与回滚 ----

def test_write(tmp):
    print("[5] write_settings")
    p = tmp / "proj" / ".claude" / "settings.json"
    data = {"extraKnownMarketplaces": {"workframe": {"source": SRC_DIR}},
            "enabledPlugins": {"core@workframe": True}}
    changed, backup = S.write_settings(p, data)
    check("新建路径：changed=True", changed, True)
    check("新建路径无备份", backup, None)
    check("父目录被建出来", p.is_file(), True)
    raw = p.read_bytes()
    check("产物零 BOM", raw[:3] == b"\xef\xbb\xbf", False)
    check("换行恒 LF（Windows 上不被转成 CRLF）", b"\r\n" in raw, False)
    check("回读一致", S.read_settings(p), data)
    text = raw.decode("utf-8")
    # 排版形态本身是契约的一部分：C-3 的能力边界（「保住键与值、不保文件形态」）
    # 与 docs 里那句「产物恒为 无 BOM / LF / 两空格缩进」都建在它上面
    check("缩进两空格、不是紧凑单行", '\n  "extraKnownMarketplaces"' in text, True)
    # `ensure_ascii` 这一条必须钉在 **write_settings 自己的产物**上。
    # 曾经只钉了 `manual_patch`，于是 `write_settings` 那半是空的——实测把它的
    # `ensure_ascii` 打开，整个套件仍然全绿。两条断言看起来像「覆盖产物形态的一对」，
    # 其实跨了两个函数。Windows 用户名可以含中文，用户目录路径里的非 ASCII 不是边角形态。
    nonascii = {"extraKnownMarketplaces": {"市场": {"source": {"source": "directory",
                                                               "path": "C:\\用户\\示例\\框架"}}}}
    np = tmp / "proj" / ".claude" / "nonascii.json"
    S.write_settings(np, nonascii)
    nraw = np.read_bytes()
    # 两半都要：①没有 `\uXXXX` 转义 ②CJK 以原始 UTF-8 字节落盘。
    # 只看 ① 的话，把值换成纯 ASCII 也会「通过」；只看 ② 的话，混排形态看不出来。
    # 键（`市场`）与值内部（`框架`）各取一个，覆盖 json 的两条转义路径。
    check("write_settings 自己的产物：非 ASCII 不转义（ensure_ascii=False）",
          (b"\\u" not in nraw
           and "市场".encode("utf-8") in nraw
           and "框架".encode("utf-8") in nraw), True)
    check("write_settings 产物回读仍一字不差", S.read_settings(np), nonascii)
    check("manual_patch 也不转义（与上一条是两个函数，各钉各的）",
          "\\u" not in S.manual_patch("p", {"中文键": "中文值"}), True)

    data2 = dict(data)
    data2["enabledPlugins"] = {"core@workframe": True, "core@other": True}
    changed2, backup2 = S.write_settings(p, data2)
    check("内容变了：changed=True", changed2, True)
    check("已有文件生成备份", backup2 is not None and Path(backup2).is_file(), True)
    check("备份内容是改动前的那份", json.loads(Path(backup2).read_text(encoding="utf-8")), data)
    check("新内容已落盘", S.read_settings(p), data2)

    # 「内容没变就不写」短路：补跑订阅是被鼓励的动作，必须真的零成本
    before_bytes = p.read_bytes()
    before_baks = sorted(x.name for x in p.parent.glob("*.bak-*"))
    changed3, backup3 = S.write_settings(p, data2)
    check("内容没变：changed=False", changed3, False)
    check("内容没变：不生成备份", backup3, None)
    check("内容没变：文件字节未动", p.read_bytes(), before_bytes)
    check("内容没变：备份没有多出来", sorted(x.name for x in p.parent.glob("*.bak-*")),
          before_baks)

    # 备份落点可指定（框架既有约定是落项目 logs/，那里在 managed gitignore 块内；
    # 落在原文件旁边的话 managed 块没有任何一条覆盖 `*.bak-*`）
    logs = tmp / "proj" / "logs"
    data4 = dict(data2)
    data4["enabledPlugins"] = {"core@workframe": True}
    changed4, backup4 = S.write_settings(p, data4, backup_dir=logs)
    check("指定 backup_dir：备份落那儿", Path(backup4).parent, logs)
    check("指定 backup_dir：目录被建出来且文件在", Path(backup4).is_file(), True)
    check("指定 backup_dir：备份内容仍是改动前那份",
          json.loads(Path(backup4).read_text(encoding="utf-8")), data2)
    check("指定 backup_dir：原文件旁边没多出备份",
          sorted(x.name for x in p.parent.glob("*.bak-*")), before_baks)
    # 备份名带时间戳——改成常量的话重跑会互相覆盖，历史就只剩最后一份
    check("备份名带原文件名与时间戳前缀", Path(backup4).name.startswith("settings.json.bak-"), True)
    check("备份名的时间戳段是 15 位 YYYYmmdd-HHMMSS",
          len(Path(backup4).name.rsplit(".bak-", 1)[1]) == 15, True)

    check_raises("不可序列化内容 ⇒ 抛且不落盘",
                 lambda: S.write_settings(tmp / "x.json", {"a": {1, 2}}), "不可序列化")
    check("不可序列化时没建出文件", (tmp / "x.json").exists(), False)

    # 键值相同但文件形态不同（BOM + CRLF + 四空格）⇒ **也走短路、一个字节不动**。
    # 短路判据是「消费方读到的东西一不一样」而不是字节相等，所以本函数不会为了把格式
    # 归一去动一份本来就不需要改的文件——这把「整份文件 diff」那条边界的触发面缩到最小。
    q = tmp / "shape" / "settings.json"
    q.parent.mkdir(parents=True, exist_ok=True)
    weird_bytes = ("﻿".encode("utf-8")
                   + json.dumps(data4, indent=4, ensure_ascii=False).replace("\n", "\r\n").encode("utf-8"))
    q.write_bytes(weird_bytes)
    changed5, _ = S.write_settings(q, data4)
    check("键值相同、形态不同：仍然短路", changed5, False)
    check("短路时用户的 BOM / CRLF / 四空格原样保留", q.read_bytes(), weird_bytes)
    # 而真要改键值时，产物是归一形态（「整份文件 diff」那条边界在这里兑现）
    data6 = dict(data4)
    data6["enabledPlugins"] = {"core@workframe": True, "core@x": True}
    changed6, _ = S.write_settings(q, data6)
    check("键值要改：这次真写", changed6, True)
    check("真写之后形态归一（无 BOM、无 CRLF）",
          q.read_bytes()[:3] != b"\xef\xbb\xbf" and b"\r\n" not in q.read_bytes(), True)
    check("真写之后键值一字不差", S.read_settings(q), data6)


def test_rollback(tmp):
    print("[6] 回滚")
    p = tmp / "rb" / "settings.json"
    good = {"permissions": {"allow": ["Bash(ls:*)"]}}
    S.write_settings(p, good)
    before = p.read_bytes()
    # 坏文件不走短路：短路的前提是「读得到且相同」，读不到时那个前提根本没成立
    bad = tmp / "rb" / "broken.json"
    bad.write_bytes(b'{"a": ')
    changed_bad, backup_bad = S.write_settings(bad, {"a": 1})
    check("原文件读不出来：不短路，照常写", changed_bad, True)
    check("原文件读不出来：仍然先落备份", Path(backup_bad).is_file(), True)
    check("备份里是那份坏内容", Path(backup_bad).read_bytes(), b'{"a": ')

    real_read = S.read_settings
    S.read_settings = lambda path: {"回读": "被污染"}     # 制造「回读与写入不一致」
    try:
        check_raises("回读不一致 ⇒ 抛", lambda: S.write_settings(p, {"new": 1}), "回读")
    finally:
        S.read_settings = real_read
    check("原文件按备份还原（字节级相同）", p.read_bytes(), before)

    newp = tmp / "rb" / "brand-new.json"
    S.read_settings = lambda path: {"回读": "被污染"}
    try:
        check_raises("新建路径写失败 ⇒ 抛", lambda: S.write_settings(newp, {"new": 1}), "回读")
    finally:
        S.read_settings = real_read
    check("新建路径写失败后不留半写文件", newp.exists(), False)


def test_manual_patch():
    print("[7] manual_patch")
    text = S.manual_patch("C:/p/.claude/settings.json", {"enabledPlugins": {"core@workframe": True}})
    check("补丁点名目标文件", "C:/p/.claude/settings.json" in text, True)
    check("补丁带上要写的键", '"core@workframe": true' in text, True)
    check("补丁说明是合并不是替换", "合并进" in text, True)


def main():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_read(tmp)
        test_merge_preserves_real_shapes()
        test_merge_edges()
        test_known_marketplaces()
        test_write(tmp)
        test_rollback(tmp)
        test_manual_patch()
    if FAILURES:
        print(f"\n{len(FAILURES)} 条失败: {FAILURES}")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
