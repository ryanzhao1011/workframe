#!/usr/bin/env python3
"""`settings.json` 家族的读 / merge / 写——**全仓唯一一份实现**，三个消费方共用。

**为什么会有这份文件**：在此之前，框架从来没有用代码写过 settings——`onboard` 的
「备份 + JSON merge + 失败兜底」是**模型照 SKILL 正文执行的工序**，不是可被单测钉住的函数。
装机链路要在两扇门下都写出订阅声明（CC 门有 `claude` CLI、纯 Codex 机器没有），这一步
必须落成代码，于是这三件事第一次需要一个确定性实现。

**消费方三处，各要一半**：
  - `project_scaffold.py`：写（订阅声明 ＋ 用户级市场注册表）
  - `workframe_doctor.check_subscription`：读项目 settings
  - `workframe_door.check_cc`：读项目 settings

**BOM 口径在这里统一**：此前 `check_cc` 用 `utf-8-sig`、doctor 用 `utf-8`，同一份带 BOM 的
settings 在一侧读得到、在另一侧被判「解析失败」报 error。两边都改成问本模块 ⇒ 口径只剩一份。
**读一律 `utf-8-sig`（容忍 BOM）、写一律不带 BOM**：BOM 是 Windows 记事本与 PowerShell 5.1
`Out-File -Encoding utf8` 的默认产物，用户手改过 settings 就可能带上它。

**本模块 import 无副作用**（不碰 stdout / stderr，不读任何文件），要给用户看的话由调用方
print，本模块只抛 `SettingsError` 与返回 notes。

> **这条契约今天没有任何闸在守，靠人。** 相关的那道闸叫 `check_utf8_stream_wrap_symmetric`
> （`tools/validate.py`），它的**操作判据是「文件里有 `def main(` 或 `__name__ == "__main__"`」**
> ——本模块两者皆无，所以**根本不在它的扫描面内**，不是「被豁免」。
> 「下划线开头的模块按 library 豁免」是它 docstring 里列的豁免**理由**之一，不是判据。
> 这个区别有后果：哪天本模块长出一个 `main()` 入口，它会**立刻进入扫描面**并要求成对包装
> stdout / stderr，而那正是本契约禁止的事——届时要改的是「要不要给它加 CLI 入口」这个决定，
> 不是去找豁免。

**能力边界（写出来是因为「没做」与「判过不用做」在读者眼里同形）**：
  - **不做并发控制**。两个进程同时写同一份 settings 时后写的赢；框架没有跨进程锁，
    装机是用户在场的一次性动作，这一档风险知情接受。
  - **不校验 settings 的业务 schema**。本模块只保证「合法 JSON 对象进、合法 JSON 对象出、
    该留的键一个不少」；`enabledPlugins` 的值该不该是布尔、市场源对象有哪些合法形态，
    由调用方判（`merge_project_subscription` 只对**本模块自己要写的那两个键**做形态校验）。
  - **不判断市场源是否解析得到**。那是 `workframe_doctor.check_subscription` 的活。
"""

import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


class SettingsError(Exception):
    """settings 读写失败——**一律让调用方当场看见，不静默降级**。

    settings 写坏的后果是「框架整个不加载」（doctor 的 subscription 项直接 error），
    所以这里没有「尽力而为」这一档：读不出来就抛，写不成就回滚再抛。
    """


def read_settings(path):
    """读一份 settings 家族的 JSON 对象。

    返回 dict。**文件不存在 → `{}`**（「还没有」是合法起点，不是错误）；
    解析失败 / 顶层不是对象 → `SettingsError`（那是坏文件，不能当成空对象往上盖，
    盖上去等于把用户原有的键一次性抹掉）。
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise SettingsError(f"{p} 读取失败（{type(e).__name__}: {e}）")
    if not raw.strip():
        # 零字节 / 全空白：**不当成 `{}`**。它与「文件不存在」信息量不同——
        # 有这么个文件说明有人写过，内容没了是异常，直接盖上去会掩盖掉截断事故。
        raise SettingsError(f"{p} 是空文件——这不是「还没配置」，是写到一半断了；"
                            f"确认内容后再重跑，或先把它删掉")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as e:
        raise SettingsError(f"{p} 不是 UTF-8 文本（{e}）")
    except json.JSONDecodeError as e:
        raise SettingsError(f"{p} JSON 语法错误：第 {e.lineno} 行第 {e.colno} 列 {e.msg}")
    if not isinstance(data, dict):
        raise SettingsError(f"{p} 顶层不是 JSON 对象（实际 {type(data).__name__}）")
    return data


def _merge_slot(merged, key, note_prefix):
    """取 `merged[key]` 这个字典槽位，缺则建。

    槽位存在但不是对象时**不静默改写**——那是用户手写坏了或格式换了，覆盖它等于
    把人家的内容删掉（删除权不在代码手里）。抛出来让调用方转述给用户。
    """
    slot = merged.get(key)
    if slot is None:
        slot = {}
        merged[key] = slot
        return slot
    if not isinstance(slot, dict):
        raise SettingsError(f"{note_prefix}`{key}` 现在不是对象（实际 "
                            f"{type(slot).__name__}）——本工具不覆盖它；"
                            f"请先手工修好这个键再重跑")
    return slot


def merge_project_subscription(existing, market, source):
    """项目 `.claude/settings.json` 的两项订阅声明 —— merge，**不是覆盖**。

    写两个键，其余**一个不动**（实测现场：本机有的项目 settings 里装着 `env` /
    `settings` / `hooks` 三个顶层键含三条 hook，有的装着 `permissions.allow` 七条，
    整写即毁）：

        extraKnownMarketplaces[<market>] = {"source": <source>}
        enabledPlugins["core@<market>"]  = true

    **同名市场已存在且源不同 ⇒ 不覆盖、只记一条 note**。同名不同源是开发机上的常态，
    而「被悄悄改成另一个源」的后果是插件从另一个仓加载——宁可让用户看见冲突自己决断。

    返回 `(merged, notes)`：`merged` 是全新对象（`existing` 不被修改），`notes` 是要
    转述给用户的人话列表（可能为空）。
    """
    if not isinstance(existing, dict):
        raise SettingsError(f"existing 必须是 dict，实际 {type(existing).__name__}")
    if not isinstance(market, str) or not market.strip():
        raise SettingsError("market（市场名）不能为空")
    if not isinstance(source, dict) or not source:
        raise SettingsError("source（市场源对象）必须是非空 JSON 对象，"
                            "形如 {\"source\":\"directory\",\"path\":\"…\"} 或 "
                            "{\"source\":\"github\",\"repo\":\"owner/repo\"}")
    merged = copy.deepcopy(existing)
    notes = []

    markets = _merge_slot(merged, "extraKnownMarketplaces", "项目 settings 的 ")
    old = markets.get(market)
    if isinstance(old, dict) and isinstance(old.get("source"), dict) \
            and old["source"] != source:
        notes.append(f"项目 settings 里市场 {market} 已声明为 {old['source']}，"
                     f"与本次的 {source} 不同——**保留原声明不覆盖**；"
                     f"两个源指向不同的框架副本时请自行确认要用哪个")
    elif isinstance(old, dict):
        # 已有条目：只替 source 子键，条目上别的键（未来 CC 可能加字段）原样留着
        old = dict(old)
        old["source"] = source
        markets[market] = old
    else:
        if old is not None:
            notes.append(f"项目 settings 里市场 {market} 的原条目不是对象"
                         f"（{type(old).__name__}），已按本次声明重写")
        markets[market] = {"source": source}

    enabled = _merge_slot(merged, "enabledPlugins", "项目 settings 的 ")
    key = f"core@{market}"
    if key in enabled and enabled[key] is False:
        notes.append(f"项目 settings 里 {key} 原为 false（此前被显式停用过），"
                     f"本次装机把它改回 true")
    enabled[key] = True
    return merged, notes


def landed_project_source(merged, market):
    """merge 之后项目 settings 里这个市场**实际落地**的 source（取不到时 None）。

    **判「为什么不改」的唯一实现**，`write_subscription` 与只读预演（`--print-plan`）都问这里。
    「改不改」共用 `would_change` 还不够——**「为什么不改」这个派生结论一样要共用**：
    它曾被预演那侧第二次手写，当场就漂成了「已是目标状态」，而真实语义是
    「这个名字已被另一个框架副本占用，你的项目会订阅到你没选的那一个」。
    调用方拿它与本次的 `source` 比，不相等即冲突档。
    """
    return ((merged.get("extraKnownMarketplaces") or {}).get(market) or {}).get("source")


def landed_registry_source(merged, market):
    """merge 之后用户级市场注册表里这个市场**实际落地**的 source（取不到时 None）。理由同上一个函数。"""
    return (merged.get(market) or {}).get("source")


def merge_known_marketplaces(existing, market, source, install_location, now=None):
    """用户级 `~/.claude/plugins/known_marketplaces.json` 的市场注册条目 —— 同样 merge。

    这一格正是「纯 Codex 机器上没有、而 CC 机器上早就有」的那一格：补上它之后，
    目标场景退化成已被实测覆盖的「clone 出来首个会话自动装上」。

    schema 取自真机现有文件：

        {"<market>": {"source": <source>, "installLocation": "<绝对路径>",
                      "lastUpdated": "<ISO-8601 毫秒 Z>"}}

    **同名不同源同样不覆盖**（理由同 `merge_project_subscription`）。

    **`lastUpdated` 只在别的字段真变了的时候才刷**：无条件刷新会让「内容没变」这个状态
    永远不成立，于是每跑一次就真写一次、真备一次份，备份在用户主目录里无人清理地堆积
    （实测连跑 3 次积 3 份），而重跑本该是零改动。时间戳本身不承载任何判定，
    让它跟着实质内容走才对得上它的语义（「这条注册上次**变成现在这样**是什么时候」）。

    返回 `(merged, notes)`。
    """
    if not isinstance(existing, dict):
        raise SettingsError(f"existing 必须是 dict，实际 {type(existing).__name__}")
    if not isinstance(source, dict) or not source:
        raise SettingsError("source（市场源对象）必须是非空 JSON 对象")
    if not str(install_location or "").strip():
        raise SettingsError("installLocation 不能为空——它是市场本体在本机的落地目录")
    merged = copy.deepcopy(existing)
    notes = []
    old = merged.get(market)
    if isinstance(old, dict) and isinstance(old.get("source"), dict) \
            and old["source"] != source:
        notes.append(f"用户级市场注册表里 {market} 已指向 {old['source']}，"
                     f"与本次的 {source} 不同——**保留原注册不覆盖**；"
                     f"本机上这个名字已被占用，要换源请自行处理")
        return merged, notes
    entry = dict(old) if isinstance(old, dict) else {}
    unchanged = (entry.get("source") == source
                 and entry.get("installLocation") == str(install_location)
                 and entry.get("lastUpdated"))
    entry["source"] = source
    entry["installLocation"] = str(install_location)
    if not unchanged:
        entry["lastUpdated"] = iso_millis(now)
    merged[market] = entry
    return merged, notes


def iso_millis(now=None):
    """`lastUpdated` 的字面形态：UTC、毫秒、`Z` 结尾（与真机现有条目同形）。"""
    dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def backup_name(path, now=None, backup_dir=None):
    """备份文件名：`<原名>.bak-<YYYYmmdd-HHMMSS>`，与 onboard 工序里的形态同族。

    **`backup_dir` 给了就落那儿，不落原文件旁边。** 框架的既有备份约定是落项目的
    `logs/`（`logs/CLAUDE.md.bak-<时间戳>`，见 `reference/claude-md-merge-guide.md`），
    而 `logs/` 在 managed gitignore 块内。落在原文件旁边时不一样：managed 块里
    **没有任何一条覆盖 `*.bak-*`**，于是 `.claude/settings.json.bak-…` 会进用户的未跟踪面，
    `git status` 里多出一堆东西（实测 `git check-ignore` rc=1，确实不被忽略）。
    """
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    p = Path(path)
    name = p.name + f".bak-{stamp}"
    return (Path(backup_dir) / name) if backup_dir else p.with_name(name)


def would_change(path, data):
    """`write_settings` 落不落笔的**唯一判据**——它自己和只读预演（`project_scaffold --print-plan`）
    都问这里，两处各写一份必漂。

    返回 `(will_write, existed)`：
      - 文件不存在 ⇒ `(True, False)`；
      - 存在且 `read_settings` 读出来与 `data` 相等 ⇒ `(False, True)`（write_settings 的短路）；
      - 存在但**读不出来**（坏 JSON / 空文件）⇒ `(True, True)`——短路的前提是「读得到且相同」，
        读不到时那个前提根本没成立，不能当成「相同」放行。

    **判据是「消费方读到的东西一不一样」，不比字节**（理由见 `write_settings` docstring：
    带 BOM / CRLF / 四空格而键值已对的文件不该仅仅因为跑了一次装机就被重排一遍）。
    """
    p = Path(path)
    existed = p.exists()
    if not existed:
        return True, False
    try:
        return read_settings(p) != data, True
    except SettingsError:
        return True, True


def write_settings(path, data, now=None, backup_dir=None):
    """**内容没变就不写** → 备份 → 写 → 回读校验 → 不一致就回滚。任何一步失败都抛，不留半写文件。

    为什么回读不是多余的一步：settings 写坏的后果是 CC 整个不加载插件，而「写进去了」
    与「写进去的是别的东西」（编码转换、磁盘满截断、杀软回写）在 `write_text` 的返回值
    上长得一模一样。回读用的是与消费方同一条读路径（`read_settings`），所以它证的是
    「消费方现在能读到我要它读到的东西」，不是「我刚才调了写函数」。

    **为什么要「内容没变就不写」这条短路**：没有它，重跑同一条装机命令就不是零改动——
    每跑一次都真写、真备份，备份无界累积（实测连跑 3 次积 3 份）。补跑订阅是被明确
    鼓励的动作（SKILL §5 步骤 2 给了单行命令），它必须真的便宜。
    **判据是「消费方读到的东西一不一样」**（拿 `read_settings` 的结果比，**不比字节**）。
    这条选择有一个重要后果，是有意的：原文件带 BOM / CRLF / 四空格缩进、而键值已经对了的时候
    **也走短路、一个字节不动**——本函数不会为了「把格式归一」去动一份本来就不需要改的文件。
    ⇒ 下面那条「整份文件 diff」的边界**只在真的需要改键值时**才兑现。

    `newline=""`：Windows 上 Python 文本写入默认 LF→CRLF，一次写入就让整份文件的 diff
    面目全非、真实改动被淹没。

    **能力边界：一旦真的落笔，merge 保住的是「键与值」，不是「文件形态」。** 产物恒为
    无 BOM / LF / 两空格缩进 / `ensure_ascii=False`；用户原文件若是 BOM + CRLF + 四空格，
    键一个不少但**整份文件的 diff 会变**。`.claude/settings.json` 进 git，所以 B 路径用户
    会在自己的仓里看到一个整文件 diff——这是知情取舍（单靠 json 模块没法既 merge 又保排版），
    不是缺陷，但必须说出来。**上面那条短路把它的触发面缩到了最小**：不需要改键值时不落笔，
    也就不会仅仅因为跑了一次装机就把人家的文件重排一遍。

    > **登记一条由此产生的耦合**：短路的代价是「装机顺手把用户 settings 里的 BOM 抹掉」
    > 这个副作用**没有了**——键值已经对的带 BOM 文件会原样留着 BOM。而「Claude Code 自己
    > 认不认带 BOM 的 settings」是框架控制面之外、且**至今未验**的一格。此前那个副作用
    > 碰巧遮住了这个未验面（跑一次装机 BOM 就没了），现在遮不住了。
    > 两件事本来就该分开看：框架保证**自己不制造** BOM（写端恒不带），
    > 保证不了**别人写的** BOM 能不能被 CC 读。

    返回 `(changed, backup)`：`changed=False` ⇒ 一个字节没动、也没生成备份，`backup` 为 None。
    """
    p = Path(path)
    try:
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    except (TypeError, ValueError) as e:
        raise SettingsError(f"待写入内容不可序列化为 JSON：{e}")
    # 落不落笔的判据只有一份，在 `would_change`（只读预演问的也是它）
    will_write, existed = would_change(p, data)
    if not will_write:
        return False, None
    backup = None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if existed:
            backup = backup_name(p, now, backup_dir)
            Path(backup).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, backup)
    except OSError as e:
        raise SettingsError(f"{p} 备份失败（{type(e).__name__}: {e}）——"
                            f"没有退路就不动它；改用手动补丁")
    try:
        p.write_text(text, encoding="utf-8", newline="")
        back = read_settings(p)
        if back != data:
            raise SettingsError(f"{p} 回读与写入内容不一致——文件可能被别的进程改动或写入被截断")
    except Exception as e:
        _rollback(p, backup, existed)
        raise SettingsError(f"{p} 写入失败（{type(e).__name__}: {e}）；"
                            f"原文件已" + ("按备份还原" if existed else "清理，未留半写文件"))
    return True, backup


def _rollback(p, backup, existed):
    """写失败后的还原：有原文件就从备份拷回，本来没有就把半写的删掉。

    「本来没有就删掉」不算违反删除权——被删的是**本函数这一次刚创建的**半写文件，
    不是任何人的既有内容；留着它反而是把「framework 整个不加载」的坏 settings 留在原地。
    """
    try:
        if existed and backup and Path(backup).exists():
            shutil.copy2(backup, p)
        elif not existed and p.exists():
            p.unlink()
    except OSError:
        pass  # 还原本身再失败就没有下一级退路了，让上层的 SettingsError 带着原因往外走


def manual_patch(path, data):
    """写不进去时给用户的手动补丁文本——**合并进去，不是整份替换**。"""
    return (f"--- 手动补丁：把下面这份内容合并进 {path} ---\n"
            f"（只需保证其中的键存在且取值一致，文件里其余的键原样保留）\n"
            + json.dumps(data, indent=2, ensure_ascii=False) + "\n"
            + "--- 补丁结束 ---")
