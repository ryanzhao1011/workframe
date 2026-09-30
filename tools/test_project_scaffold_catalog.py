#!/usr/bin/env python3
"""
单元测试 — role-profile-catalog 解析的三层「恰好一个」契约 + 形态探针族。

**为什么固化成正式单测**：这套形态探针此前被独立重建过 3 次（TASK-016 一轮里 @dev、
@qa、独立评审员各在 scratchpad 搭了一套等价的，跑完按收口纪律清除）。一次性探针的问题
不是浪费，是**下一个人不知道哪些形态已经验过**，于是要么重搭要么漏掉。

覆盖（每个判定分支用它自己的失败方式打一遍——「写完了但从没真正执行过」的检查与通过
长得一模一样；因此每条断言都核**文案要点**，不只核「抛了没有」）：

  catalog_section（文件 → 章节，围栏外整行相等恰 1 处）
    - 无注入自检：三档都切得出、块数正确
    - 标题行尾空格 / 行尾 tab / 中文后缀 / HTML 注释锚 / 前导空格 / `####` 降级
      / 只出现在围栏内 —— 一律视为「没有这个章节」（与装机 preflight 同宽）
    - 同名章节重复：副本靠前 / 靠后**都**抛（契约是「恰 1」，不是「第一个对不对」）
    - 围栏内的同名标题不计入重复（那是文档里合法的示例）
    - 末章不吞到文件尾（下一个 `## ` 截断）
    - CRLF 与 LF 原文都命中；空文件返回 None
    - 重复章节的 ParamError 文案 ≤ 120 字符（validate runner 按 `str(e)[:120]` 截断，
      超了会砍掉排在最后的动作指令）

  routing_block（章节 → 渲染块，块数恰 1）
    - 0 块 / 2 块两种失败模式**措辞不同**

  priority_table（章节 → 优先级表，围栏外恰 1 张）
    - 围栏内的诱饵表不顶替真表（真表改坏时必须读到改坏的那张，才轮得到上层判红）
    - 围栏外诱饵表靠前 → 抛「N 张」
    - 真表被删 / 被挪进围栏 / 分隔行形态坏掉 → 抛「0 张」

执行：
  python tools/test_project_scaffold_catalog.py
退出 0 = 全部通过；非 0 = 有失败用例。
"""

import importlib.util
import io
import sys
from pathlib import Path

# 成对包 UTF-8 流。本文件**不在** `check_utf8_stream_wrap_symmetric` 的豁免名单里，
# 也不该在：它 importlib 加载的 project_scaffold.py 与 validate.py 都只在各自 main()
# 里包流（模块级不碰 stdout），所以加载那一刻两条流仍是裸的，自己包不会抢 buffer。
# 用例文案里有 ✓✗ 与中文，cp936 控制台上不包就会在报错路径二次崩溃。
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD_PATH = REPO_ROOT / "plugins" / "core" / "scripts" / "project_scaffold.py"
VALIDATE_PATH = REPO_ROOT / "tools" / "validate.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── fixture：一份最小但结构齐全的 catalog ──
# 末尾的「渲染契约」段刻意也放一个 ```markdown 块与一个同名标题：切章若少了终止条件
# 或少了围栏跟踪，就会把它吞进 solo-pm 章节，于是「删掉真块」反而不报错（静默渲染错块）。
CAT = """# role-profile-catalog

## 3 个 Profile 定义

### `solo-pm`

**适用**：个人 PM 模式

**角色优先级**：
| 主力 | 按需 | 几乎不用 |
|---|---|---|
| pm | dev / qa | prompt-eng |

**AGENTS.md 路由偏好渲染文本**：

```markdown
## 路由偏好（profile: solo-pm）

- 需求调研、PRD 撰写 → @pm（主力）
- 技术可行性评估 → @dev（按需）
```

---

## 渲染契约

下面是**示例**，不是任何 profile 的正文：

### `solo-pm`

**角色优先级**：
| 主力 | 按需 | 几乎不用 |
|---|---|---|
| xx | yy | zz |

```markdown
## 路由偏好（profile: solo-pm）

- 这是契约段里的示例块
```
"""

# 上面「渲染契约」段里的示例标题与示例表在**围栏外**——那是有意的：它们同时充当
# 「章节终止条件」与「同名重复」两条断言的 fixture。需要「围栏内示例」时用下面这个。
CAT_FENCED_EXAMPLE = CAT.replace(
    "### `solo-pm`\n\n**角色优先级**：\n| 主力 | 按需 | 几乎不用 |\n|---|---|---|\n| xx | yy | zz |",
    "```text\n### `solo-pm`\n\n**角色优先级**：\n| 主力 | 按需 | 几乎不用 |\n|---|---|---|\n| xx | yy | zz |\n```")

HEAD = "### `solo-pm`"


def _first_only(text):
    """只保留第一个 solo-pm 章节（把「渲染契约」段整段删掉），得到单章节 fixture。"""
    return text.split("\n## 渲染契约")[0]


SINGLE = _first_only(CAT)


def test_catalog_section_baseline(sc, v, fs):
    sec = sc.catalog_section(SINGLE, "solo-pm")
    if sec is None:
        fs.append("无注入自检：solo-pm 切不出章节")
        return
    if "**适用**" not in sec or "@pm（主力）" not in sec:
        fs.append("无注入自检：章节正文缺内容")
    if "渲染契约" in sec:
        fs.append("无注入自检：章节吞到了下一个 ## 段")
    blk = sc.routing_block(sec, "solo-pm")
    if "@pm（主力）" not in blk or "```" in blk:
        fs.append(f"无注入自检：routing_block 取出的块不对: {blk[:60]!r}")


def test_heading_shapes(sc, v, fs):
    """标题形态族：一律视为「没有这个章节」，与装机 preflight 同宽。"""
    shapes = {
        "行尾空格": HEAD + " ",
        "行尾 tab": HEAD + "\t",
        "中文后缀": HEAD + "（默认）",
        "HTML 注释锚": HEAD + " <!-- anchor -->",
        "前导空格": " " + HEAD,
        "#### 降级": "#" + HEAD,
        "反引号缺失": "### solo-pm",
    }
    for label, bad in shapes.items():
        got = sc.catalog_section(SINGLE.replace(HEAD, bad, 1), "solo-pm")
        if got is not None:
            fs.append(f"标题形态「{label}」应视为无此章节，实际切出了 {len(got)} 字符——"
                      f"闸会绿而装机 preflight 抛 ParamError")


def test_heading_only_in_fence(sc, v, fs):
    """标题只出现在代码围栏内 → 不算章节。"""
    fenced = SINGLE.replace(HEAD, "```text\n" + HEAD + "\n```", 1)
    if sc.catalog_section(fenced, "solo-pm") is not None:
        fs.append("围栏内的标题被当成了真章节")


def test_duplicate_sections(sc, v, fs):
    """同名章节重复：靠前 / 靠后都必须抛——契约是「恰 1」不是「第一个对不对」。"""
    for label, text in (("副本靠前", CAT), ("副本靠后", CAT)):
        try:
            sc.catalog_section(text, "solo-pm")
            fs.append(f"{label}：重复章节未抛 ParamError（靠前那份会静默顶替真章节）")
        except sc.ParamError as e:
            msg = str(e)
            if "2 处" not in msg:
                fs.append(f"{label}：文案未点明处数: {msg[:80]!r}")
            if len(msg) > 120:
                fs.append(f"{label}：文案 {len(msg)} 字符 > 120，validate runner 会砍掉"
                          f"排在最后的动作指令")
            break   # 两个 label 用同一 fixture，抛一次即可


def test_fenced_duplicate_not_counted(sc, v, fs):
    """围栏内的同名标题是示例，不计入重复。"""
    try:
        sec = sc.catalog_section(CAT_FENCED_EXAMPLE, "solo-pm")
    except sc.ParamError as e:
        fs.append(f"围栏内的示例标题被算成了重复（假红）: {e}")
        return
    if sec is None or "@pm（主力）" not in sec:
        fs.append("围栏内示例存在时切出的不是真章节")


def test_eol_and_empty(sc, v, fs):
    if sc.catalog_section(SINGLE.replace("\n", "\r\n"), "solo-pm") is None:
        fs.append("CRLF 原文切不出章节")
    if sc.catalog_section(SINGLE, "solo-pm") is None:
        fs.append("LF 原文切不出章节")
    if sc.catalog_section("", "solo-pm") is not None:
        fs.append("空文件应返回 None")
    if sc.catalog_section(SINGLE, "no-such-profile") is not None:
        fs.append("不存在的 profile 应返回 None")


def test_routing_block_modes(sc, v, fs):
    """0 块 / 2 块两种失败模式的措辞必须分开。"""
    sec = sc.catalog_section(SINGLE, "solo-pm")
    no_block = sec.replace("```markdown", "```", 1)
    try:
        sc.routing_block(no_block, "solo-pm")
        fs.append("删掉围栏语言标注后未抛")
    except sc.ParamError as e:
        if "markdown" not in str(e):
            fs.append(f"0 块文案未指向围栏语言标注: {e}")
    two = sec + "\n```markdown\n## 陈旧块\n```\n"
    try:
        sc.routing_block(two, "solo-pm")
        fs.append("2 个块时未抛（会取第一个、静默顶替真块）")
    except sc.ParamError as e:
        if "2" not in str(e):
            fs.append(f"≥2 块文案未点明块数: {e}")


def test_priority_table(sc, v, fs):
    sec = sc.catalog_section(SINGLE, "solo-pm")
    hdr, row = v.priority_table(sec, "solo-pm")
    if tuple(hdr) != ("主力", "按需", "几乎不用"):
        fs.append(f"无注入自检：表头取错 {hdr}")
    if row[0] != "pm":
        fs.append(f"无注入自检：首数据行取错 {row}")

    # 围栏内的诱饵表在真表之前 + 真表改坏 → 必须读到**改坏的真表**（诱饵不顶替）
    decoy = sec.replace(
        "**角色优先级**：",
        "```text\n**角色优先级**：\n| 主力 | 按需 | 几乎不用 |\n|---|---|---|\n"
        "| pm | dev / qa | prompt-eng |\n```\n\n**角色优先级**：", 1)
    decoy = decoy.replace("| pm | dev / qa | prompt-eng |\n\n**AGENTS.md",
                          "| XX | dev / qa | prompt-eng |\n\n**AGENTS.md")
    try:
        _h, r = v.priority_table(decoy, "solo-pm")
        if r[0] != "XX":
            fs.append(f"围栏内诱饵表顶替了真表（读到 {r[0]!r} 而非改坏的 'XX'）"
                      f"——上层对账闸会因此报绿，事实源已分叉却无人说话")
    except ValueError as e:
        fs.append(f"围栏内诱饵表不该被计数，却抛了: {e}")

    # 围栏外诱饵表靠前 → 抛「2 张」
    outside = sec.replace(
        "**角色优先级**：",
        "**角色优先级**：\n| 主力 | 按需 | 几乎不用 |\n|---|---|---|\n"
        "| zz | yy | xx |\n\n**角色优先级**：", 1)
    try:
        v.priority_table(outside, "solo-pm")
        fs.append("围栏外两张表时未抛（靠前那张会顶替事实源）")
    except ValueError as e:
        if "2 张" not in str(e):
            fs.append(f"≥2 张文案未点明张数: {e}")

    # 真表被删 / 挪进围栏 / 分隔行坏掉 → 0 张
    cases = {
        "表被删": sec.replace("**角色优先级**：\n| 主力 | 按需 | 几乎不用 |\n|---|---|---|\n"
                              "| pm | dev / qa | prompt-eng |\n", ""),
        "表挪进围栏": sec.replace(
            "**角色优先级**：", "```text\n**角色优先级**：", 1).replace(
            "| pm | dev / qa | prompt-eng |", "| pm | dev / qa | prompt-eng |\n```", 1),
        # 注意：`| - - - |` **不是**坏分隔行——空格与连字符都在允许字符集内，
        # 拿它当 fixture 会得到一个「本该通过却被当成失败」的假阳性用例（初版踩过）。
        "分隔行被删": sec.replace("|---|---|---|\n", "", 1),
        "分隔行非法字符": sec.replace("|---|---|---|", "|===|===|===|", 1),
    }
    for label, bad in cases.items():
        try:
            v.priority_table(bad, "solo-pm")
            fs.append(f"「{label}」应判 0 张却取到了表——若读到的是围栏内示例，"
                      f"上层会拿示例当事实源报绿")
        except ValueError as e:
            if "0 张" not in str(e):
                fs.append(f"「{label}」文案未点明 0 张: {e}")


def main():
    sc = _load(SCAFFOLD_PATH, "project_scaffold_under_test")
    v = _load(VALIDATE_PATH, "validate_under_test")
    tests = [
        ("catalog_section 无注入自检", test_catalog_section_baseline),
        ("标题形态族（七种）", test_heading_shapes),
        ("标题只在围栏内", test_heading_only_in_fence),
        ("同名章节重复（含文案 ≤120）", test_duplicate_sections),
        ("围栏内同名标题不算重复", test_fenced_duplicate_not_counted),
        ("CRLF / LF / 空文件 / 不存在的档", test_eol_and_empty),
        ("routing_block 0 块与 2 块", test_routing_block_modes),
        ("priority_table 围栏外恰 1 张", test_priority_table),
    ]
    total = []
    for name, fn in tests:
        fs = []
        try:
            fn(sc, v, fs)
        except Exception as e:
            fs.append(f"用例自身异常: {type(e).__name__}: {e}")
        print(f"{'✓' if not fs else '✗'} {name}" + (f"  ({len(fs)} failures)" if fs else ""))
        for x in fs:
            print(f"    - {x}")
        total.extend(fs)
    print(f"\n{'全部通过' if not total else f'{len(total)} 项失败'}（{len(tests)} 组用例）")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
