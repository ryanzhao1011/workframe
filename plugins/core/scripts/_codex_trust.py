#!/usr/bin/env python3
"""Codex 实时只读信任诊断与 door 审计辅助函数。

诊断只调 hooks/list，不启动 turn，不写信任、项目 trust 或启用位。
currentHash 是不透明值；配置基线与历史审计不能证明当前信任。
app-server 可能写运行日志；审计仅由 door 显式调用写入。
"""
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import _harness
import _codex_hooks as ch
import _codex_appserver as cas
from _state_io import RUNTIME_DIR

# 历史审计路径保留；SessionStart 自动写入与 last.json 已退役。
SEED_REL = Path(RUNTIME_DIR) / "codex-trust-seed.json"
NOT_CONNECTED = "not-connected"
DISABLED = "disabled"
TRUSTED = "trusted"
NEEDS_TRUST = "needs-trust"
INSTALL_ERROR = "install-error"
UNVERIFIED = "unverified"
RECOVERY = "运行 `workframe-door --upgrade`，成功后重开 Codex 会话"
# 「未能核实」不等于失信：出路是先让诊断跑得通，不是去重种信任
RECHECK = "确认 codex CLI 可用、Python ≥ 3.11 后重跑 `workframe-door --check` 或 doctor"


@dataclass(frozen=True)
class Diagnosis:
    state: str
    message: str
    pending: int = 0
    total: int = 0
    plugin_id: str = ""


def _norm(p):
    try:
        return os.path.normcase(str(Path(p).resolve()))
    except OSError:
        return os.path.normcase(str(p))


def codex_home():
    """`CODEX_HOME` 的定位规则（door 与诊断共用）：
    环境变量 > 从插件根上溯 5 级（`<CODEX_HOME>/plugins/cache/<mkt>/<plugin>/<ver>`，且那一级
    真有 `config.toml`）> `~/.codex`。上溯那档不认目录名、只认 `config.toml` 在不在——
    目录名是用户定的，`config.toml` 才是 Codex home 的结构事实。"""
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(env)
    for root in _harness.plugin_root_candidates():
        try:
            cand = Path(root).resolve().parents[4]
        except (IndexError, OSError):
            continue
        if (cand / "config.toml").is_file():
            return cand
    return Path.home() / ".codex"


def own_entries(hooks, plugin_roots):
    """`hooks/list` 条目里 `sourcePath` 落在本插件根内的那些。"""
    roots = [_norm(r) for r in plugin_roots]
    out = []
    for h in hooks:
        sp = h.get("sourcePath")
        if not isinstance(sp, str) or not sp:
            continue
        n = _norm(sp)
        if any(n == r or n.startswith(r.rstrip("\\/") + os.sep) for r in roots):
            out.append(h)
    return out


def describe(h):
    """清单一行：key 尾段 / 事件 / 执行什么（脚本名与参数，去掉插件根前缀）。"""
    key = str(h.get("key") or "")
    cmd = str(h.get("command") or "")
    short = cmd.split("/scripts/", 1)[-1] if "/scripts/" in cmd else cmd
    # 命令字面是 `"…/scripts/x.py" --args`：切掉前缀后首个 token 尾巴还挂着那个闭合引号，去掉它
    head = short.split(" ", 1)[0]
    if head.endswith('"'):
        short = short.replace('"', "", 1)
    return f"{key.split(':', 1)[-1]}（{h.get('eventName')}）→ {short[:120]}"


def seed_record(plugin_root, plugin_id, seeded, *, seeded_by):
    """审计记录：元组从本插件 `hooks.codex.json` 解析（不从 `hooks/list` 回显取——回显是展开后
    的绝对路径，拿它做比对第一次就全量假红）。"""
    import _codex_hooks as ch
    data = json.loads((Path(plugin_root) / ch.CODEX_MANIFEST_REL).read_text(encoding="utf-8"))
    version = None
    try:
        version = json.loads((Path(plugin_root) / ".codex-plugin" / "plugin.json")
                             .read_text(encoding="utf-8")).get("version")
    except Exception:
        pass
    rows = []
    for row in ch.entries(data):
        item = {"key": f"{plugin_id}:{row['key']}", "event": row["event"], "matcher": row["matcher"]}
        for f in ch.HOOK_HASH_FIELDS:
            item[f] = row[f]
        rows.append(item)
    return {"plugin_version": version, "plugin_id": plugin_id,
            "seeded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "seeded_by": seeded_by, "seeded_keys": seeded,
            "fields": list(ch.GROUP_HASH_FIELDS + ch.HOOK_HASH_FIELDS), "entries": rows}


def write_seed(plugin_root, plugin_id, seeded, *, seeded_by):
    try:
        path = codex_home() / SEED_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(seed_record(plugin_root, plugin_id, seeded, seeded_by=seeded_by),
                                     ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        return str(path)
    except Exception as e:
        return f"<写入失败: {type(e).__name__}: {e}>"


def orphan_count(server, plugin_id, live_keys):
    """`config.toml` 里前缀为本插件 id、但不在本次 `hooks/list` 里的 `hooks.state` 记录数。
    读不到就返回 None（不猜）。"""
    try:
        cfg = server.config_read(False).get("config") or {}
        state = ((cfg.get("hooks") or {}).get("state")) or {}
        if not isinstance(state, dict):
            return None
        return sum(1 for k in state if str(k).startswith(plugin_id + ":") and k not in live_keys)
    except Exception:
        return None


def project_plugin(project):
    """从项目 managed 段取实际 core@市场；未接门或已关门不启动 CLI。"""
    import codex_roles as cr
    try:
        text = (Path(project) / cr.CONFIG_REL).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return NOT_CONNECTED, None, "本项目未接 Codex 门"
    except (OSError, UnicodeError) as e:
        return INSTALL_ERROR, None, f"Codex 项目配置读不出：{e}"
    if cr.BLOCK_BEGIN not in text:
        return NOT_CONNECTED, None, "本项目未接 Codex 门"
    # 接了门才需要解析 TOML：框架只要求 Python 3，`tomllib` 要 3.11+；没接门的项目不能因它报错
    try:
        import tomllib
    except ImportError:
        return UNVERIFIED, None, "当前 Python 低于 3.11、没有 tomllib，读不了 Codex 项目配置"
    try:
        b, e = text.index(cr.BLOCK_BEGIN), text.index(cr.BLOCK_END)
        if e < b:
            raise ValueError("managed 标记顺序异常")
        block = tomllib.loads(text[b:e])
        whole = tomllib.loads(text)
        plugins = block.get("plugins", {})
        ids = [k for k in plugins if k.startswith("core@")]
        if len(ids) != 1:
            raise ValueError("managed 段须有唯一 core@<市场> 启用表")
        pid = ids[0]
        enabled = whole["plugins"][pid].get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("enabled 不是布尔值")
        if not enabled:
            return DISABLED, pid, "本项目 Codex 门已被用户关闭"
        return TRUSTED, pid, ""
    except (ValueError, KeyError, TypeError) as e:
        return INSTALL_ERROR, None, f"Codex managed 配置异常：{e}"


def read_manifest(root):
    """读实际加载根的配置基线，拒绝空基线及重复 key。"""
    data = json.loads((Path(root) / ch.CODEX_BASELINE_REL).read_text(encoding="utf-8-sig"))
    rows = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("manifest entries 缺失或为空")
    if any(not isinstance(r, dict) or not isinstance(r.get("key"), str) or not r["key"] for r in rows):
        raise ValueError("manifest 条目缺 key")
    keys = [r["key"] for r in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("manifest key 重复")
    # 基线与安装清单必须相符，避免把缺条目读成健康。
    installed = json.loads((Path(root) / ch.CODEX_MANIFEST_REL).read_text(encoding="utf-8-sig"))
    groups_by_event = installed.get("hooks") if isinstance(installed, dict) else None
    if not isinstance(groups_by_event, dict) or not groups_by_event:
        raise ValueError("安装清单 hooks 不是非空对象")
    for groups in groups_by_event.values():
        if not isinstance(groups, list) or any(not isinstance(g, dict) for g in groups):
            raise ValueError("安装清单事件组不是对象列表")
        for group in groups:
            hooks = group.get("hooks")
            if not isinstance(hooks, list) or any(not isinstance(h, dict) for h in hooks):
                raise ValueError("安装清单 hook 不是对象列表")
    actual = ch.entries(installed)
    fields_differ = any(
        any(x != y or type(x) is not type(y) for x, y in zip(ch.hash_tuple(a), ch.hash_tuple(b)))
        for a, b in zip(actual, rows)
    )
    if [r["key"] for r in actual] != keys or fields_differ:
        raise ValueError("manifest 与安装 hook 清单不符")
    return keys


def inspect_listing(listing, plugin_id):
    """只读回显与 sourcePath 对应的 manifest；不写配置。"""
    pending, total = 0, 0
    def result(state, why):
        return Diagnosis(state, f"Codex 门 hook 信任 {pending}/{total} 条未受信：{why}；{RECOVERY}。",
                         pending, total, plugin_id)
    try:
        hooks = cas.hooks_of(listing)
        if any(not isinstance(h, dict) for h in hooks):
            raise ValueError("hooks/list 条目不是对象")
        mine = [h for h in hooks if h.get("pluginId") == plugin_id]
        total = len(mine)
        pending = sum(h.get("trustStatus") != "trusted" for h in mine)
        if not mine:
            return result(INSTALL_ERROR, "本插件零条，可能未安装、项目未 trust 或清单未加载")
        if any(h.get("trustStatus") not in ("trusted", "modified", "untrusted") for h in mine):
            raise ValueError("未知或缺失 trustStatus")
        roots = {}
        for h in mine:
            source = h.get("sourcePath")
            if not isinstance(source, str) or not source:
                return result(INSTALL_ERROR, "缺 sourcePath，无法确认本插件来源")
            # 扩展路径前缀只影响比较，不改变读取目标。
            path = Path(source.removeprefix(chr(92) * 2 + "?" + chr(92)))
            if path.name != "hooks.codex.json" or path.parent.name != "hooks":
                return result(INSTALL_ERROR, "sourcePath 不是本插件 hook 清单")
            root = path.parent.parent
            roots[_norm(root)] = root
        if len(roots) != 1:
            return result(INSTALL_ERROR, "本插件条目来自多个安装根")
        try:
            expected = [plugin_id + ":" + key for key in read_manifest(next(iter(roots.values())))]
        except (OSError, ValueError, TypeError, KeyError) as e:
            return result(INSTALL_ERROR, f"安装清单异常：{e}")
        keys = [h.get("key") for h in mine]
        if len(set(keys)) != total or set(keys) != set(expected):
            return result(INSTALL_ERROR, f"实际 {total} 条与 manifest {len(expected)} 条 key 不符或重复")
        if any(not isinstance(h.get("command"), str) or not h["command"].strip() for h in mine):
            return result(INSTALL_ERROR, "command 为空")
        if listing["data"][0].get("errors"):
            return result(INSTALL_ERROR, f"hooks/list errors：{listing['data'][0]['errors']}")
        if pending:
            return result(NEEDS_TRUST, "这些条目不会执行")
        return Diagnosis(TRUSTED, f"Codex 门 {total}/{total} 条 hook 实时受信", 0, total, plugin_id)
    except (cas.AppServerError, ValueError, TypeError, KeyError) as e:
        return Diagnosis(UNVERIFIED, f"Codex 门 hook 信任未能核实（{type(e).__name__}: {e}）；{RECHECK}。",
                         pending, total, plugin_id)


def diagnose(project, *, home=None, exe=None, server_factory=None, timeout=10.0):
    """入口 API：以项目为 cwd、显式 home 请求 hooks/list；未知不能当健康。"""
    state, pid, why = project_plugin(project)
    if state != TRUSTED:
        tail = {INSTALL_ERROR: f"；{RECOVERY}。", UNVERIFIED: f"；{RECHECK}。"}.get(state, "")
        return Diagnosis(state, why + tail, plugin_id=pid or "")
    try:
        exe = exe or cas.find_codex_exe()
        if not exe:
            raise cas.AppServerError("找不到 codex CLI")
        with (server_factory or cas.AppServer)(exe=exe, codex_home=home or codex_home(),
                                               cwd=str(Path(project).resolve()), timeout=timeout) as server:
            return inspect_listing(server.hooks_list([str(Path(project).resolve())]), pid)
    except (cas.AppServerError, OSError, ValueError) as e:
        return Diagnosis(UNVERIFIED, f"Codex 门 hook 信任未能核实（{type(e).__name__}: {e}）；{RECHECK}。",
                         plugin_id=pid)
