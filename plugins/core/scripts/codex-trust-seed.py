#!/usr/bin/env python3
"""Codex `SessionStart` 上的种信任 hook（命令行冻结）：升级后替用户补种本插件其余 hook 的信任。

挂载：`hooks/hooks.codex.json` 的 `SessionStart` 组末尾，
    "${CLAUDE_PLUGIN_ROOT}/bin/workframe-python" "${CLAUDE_PLUGIN_ROOT}/scripts/codex-trust-seed.py" --harness codex
**这行命令从此冻结**：Codex 按命令行字面算信任 hash，它一变，用户机器上这条 hook 就成
`modified` 而不再执行——而它正是升级后替用户补种其余 hook 信任的那一条，没有任何东西能
替它补种。版本差异一律放脚本内部，不进命令行。

**每次 Codex SessionStart 做的事**（`codex exec` 与 TUI 都走）：
  0. 会话目录（载荷 `cwd`）向上找不到 `.workframe-config.json`——不在 workframe 项目内——就什么都不做：
     不起 app-server、零输出、不写 last.json、exit 0（`_harness.hook_outside_project()`）；
  1. 读 stdin 载荷（bytes ＋ UTF-8 解码）取 `cwd`；定位项目根与状态目录；
  2. 在 hook 进程里起第二个 app-server 跑 `hooks/list`（cwd = 载荷 `cwd`）——与正在启动的
     会话共用同一 `CODEX_HOME`，安全性已实测（5 次无异常、sqlite 锁冲突时两面日志都会报，
     不失明），每次代价约 0.3 s；
  3. 只取**本插件**的条目：`sourcePath` 落在本插件根内（`_harness.plugin_root_candidates()`
     逐个试），不用 `pluginId` 前缀猜市场名；
  4. N = 非 `trusted` 条数。**N == 0 是绝大多数会话的路径**：只刷新
     `<state>/codex-trust-seed.last.json`、零 stdout、exit 0；
  5. N > 0：`config/batchWrite` 逐条 upsert（hash 只从 `hooks/list.currentHash` 读，不自己算；
     不带 `expectedVersion`）→ `hooks/list` 回读 → 以 additionalContext 明说
     「框架已升级，已代你信任 N 条 hook：<清单>；下次会话生效；受信 hook 脱离沙盒执行」
     → 写种信任记录 `<CODEX_HOME>/.workframe/codex-trust-seed.json`（审计记录，元组从本插件的
     `hooks.codex.json` 解析、字段集 = `_codex_hooks.HOOK_HASH_FIELDS`）→ 孤儿记录只计数告知。
     **「下次会话生效」是实测结论**：同一会话内补种（带不带 `reloadUserConfig` 都一样）刚补的
     hook 不执行，下一会话才执行。
  6. 不写非本插件条目；不删任何 `hooks.state`；不碰项目 trust（那是装机步 4 的事）。

**任何失败都不阻断会话**：exit 恒 0；补种失败时输出一行
「N 条 hook 待信任，自动补种失败（<原因>）；可在 TUI /hooks 批准或运行 workframe-door --check」。

**它自己第一次装上时是 `untrusted`、不会跑**——由 `workframe-door --codex` 一并种上。
**升级后它仍受信**的前提是本行命令未变（hash 盖 `${CLAUDE_PLUGIN_ROOT}` 字面、不盖展开路径）。

`<state>/codex-trust-seed.last.json` 是这条 hook「跑过」的唯一可观测量（探针面），在项目内的每次会话都写。
"""

import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402
from _state_io import RUNTIME_DIR, state_dir_of  # noqa: E402

LAST_FILE = "codex-trust-seed.last.json"
# 落在 <CODEX_HOME>/<RUNTIME_DIR>/：用户级、不含版本号；目录名沿用项目里的中立运行态目录名
SEED_REL = Path(RUNTIME_DIR) / "codex-trust-seed.json"
APPSERVER_TIMEOUT = 20.0     # SessionStart 上等不起更久；超时按「补种失败」报，会话照常起


def _stdin_payload():
    """hook 载荷；非管道 / 空 / 不合法一律当空字典。bytes 读 + 显式 UTF-8 解码——
    Windows 上 PowerShell 起的 python 其 stdin 默认按本机 ANSI 代码页解码，含中文的
    `cwd` 会 mojibake。"""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def _norm(p):
    try:
        return os.path.normcase(str(Path(p).resolve()))
    except OSError:
        return os.path.normcase(str(p))


def codex_home():
    """`CODEX_HOME` 的定位规则（与装机器 `workframe_door.codex_home()` 同一规则，两侧一致）：
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


def seed_record(plugin_root, plugin_id, seeded):
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
            "seeded_by": "codex-trust-seed.py", "seeded_keys": seeded,
            "fields": list(ch.GROUP_HASH_FIELDS + ch.HOOK_HASH_FIELDS), "entries": rows}


def write_seed(plugin_root, plugin_id, seeded):
    try:
        path = codex_home() / SEED_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(seed_record(plugin_root, plugin_id, seeded),
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


def run(payload):
    """返回 (additionalContext 文本或 None, last.json 内容)。永不抛。"""
    import _codex_appserver as cas
    t0 = time.monotonic()
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    roots = _harness.plugin_root_candidates()
    last = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        with cas.AppServer(cwd=cwd, timeout=APPSERVER_TIMEOUT) as s:
            listing = s.hooks_list([cwd] if cwd else [])
            mine = own_entries(cas.hooks_of(listing), roots)
            if not mine:
                last.update({"checked": 0, "note": "hooks/list 里没有落在本插件根内的条目"})
                return None, last
            plugin_id = str(mine[0].get("pluginId") or "")
            pending = [h for h in mine if h.get("trustStatus") != "trusted"]
            last.update({"checked": len(mine), "pending": len(pending), "plugin_id": plugin_id})
            if not pending:
                last["elapsed_s"] = round(time.monotonic() - t0, 3)
                return None, last
            listed = "\n".join(f"- {describe(h)}" for h in pending)
            res = s.config_batch_write(cas.trust_edits(pending))
            if not isinstance(res, dict) or res.get("status") != "ok":
                raise cas.AppServerError(f"batchWrite 返回 {str(res)[:120]}")
            back = own_entries(cas.hooks_of(s.hooks_list([cwd] if cwd else [])), roots)
            still = [h for h in back if h.get("trustStatus") != "trusted"]
            orphans = orphan_count(s, plugin_id, {h.get("key") for h in back})
    except Exception as e:
        n = last.get("pending")
        head = f"{n} 条 hook 待信任，自动补种失败" if n else "hook 信任状态未能读取，自动补种未执行"
        last.update({"error": f"{type(e).__name__}: {str(e)[:200]}",
                     "elapsed_s": round(time.monotonic() - t0, 3)})
        return (f"[workframe] {head}（{type(e).__name__}: {str(e)[:200]}）；"
                f"可在 TUI /hooks 批准或运行 workframe-door --check"), last

    seeded = [h.get("key") for h in pending if h.get("key") not in {x.get("key") for x in still}]
    seed_path = write_seed(roots[-1], plugin_id, seeded)
    last.update({"seeded": len(seeded), "still_untrusted": len(still), "orphans": orphans,
                 "seed_file": seed_path, "elapsed_s": round(time.monotonic() - t0, 3)})
    lines = [f"[workframe] 框架已升级，已代你信任 {len(seeded)} 条 hook（写入你的 Codex 用户配置 "
             f"config.toml 的 [hooks.state.*]）：", listed,
             "这些 hook **下次会话生效**（本会话内 Codex 不重读信任状态）；受信 hook 脱离沙盒执行。"]
    if still:
        lines.append(f"仍有 {len(still)} 条未受信（回读后仍非 trusted）："
                     + "；".join(describe(h) for h in still)
                     + "——可在 TUI /hooks 批准或运行 workframe-door --check")
    if orphans:
        lines.append(f"config.toml 里另有 {orphans} 条不对应当前 hook 的旧信任记录，可安全删除；"
                     f"框架不代删。")
    return "\n".join(lines), last


def main():
    # Codex 门下会话不在 workframe 项目内：零写入、零输出退出（判定与门条件只在 _harness 一处）
    if _harness.hook_outside_project():
        return 0
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass
    payload = _stdin_payload()
    text, last = None, {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        text, last = run(payload)
    except Exception as e:      # run() 自己兜了；这里兜的是 import 一类的意外
        last["error"] = f"{type(e).__name__}: {str(e)[:200]}"
    try:
        state = state_dir_of(_harness.project_dir(payload))
        state.mkdir(parents=True, exist_ok=True)
        (state / LAST_FILE).write_bytes(json.dumps(last, ensure_ascii=False).encode("utf-8"))
    except Exception:
        pass
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                 "additionalContext": text}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # exit-audited: main() 只有一个 return 0；run() 把一切异常收成文案，SessionStart 上没有非零路径
    sys.exit(main())
