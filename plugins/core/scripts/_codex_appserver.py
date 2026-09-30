#!/usr/bin/env python3
"""Codex app-server 的最小客户端：起进程、`initialize`、发请求收响应、杀进程树。

为什么单独成模块：装机器（`workframe-door`）、种信任 hook（`codex-trust-seed.py`）与
探针都要问 Codex 同几个问题（`hooks/list` / `config/batchWrite` / `config/read`），
各写一份 JSON-RPC 收发就会各踩一遍同一批坑：
  - 顶层键是 `data` 不是 `hooks`，读错键会把「有 N 条」读成「0 条」；
  - 子进程 `stdin` 必须是我们自己持有的管道，不能继承——继承了未关闭的管道时
    `codex exec` 会阻塞到 EOF，症状与「hook 不触发」同形；
  - 经 `cmd /c` 包装起的 app-server，`terminate()` 只杀包装、孤儿 `codex.exe` 留着卡住
    `stderr.read()`——要直指 exe 并按进程树杀；
  - 响应 `status: ok` 不等于写对了值，写完必须回读。
这些只在这里处理一次。

**协议**：JSON-RPC 2.0、按行分隔、stdio。方法与字段以
`codex app-server generate-json-schema --out <dir>` 产出的 schema 为准，本模块不复述。

**能力边界**：
  - 只在本机 Windows + codex-cli 0.153.x 上实测；`find_codex_exe()` 定位 npm 包内
    exe 的路径形态是本机读数，别的安装方式（brew / 官方安装器）走 `WF_CODEX_EXE` 或 PATH。
  - 不处理服务端发起的请求（审批类），本模块的调用方都是不起 turn 的只读 / 配置写路径；
    `thread/start` + `turn/start` 可以发，但审批请求会被忽略（探针用途）。
  - 不判断该不该写用户配置——那是调用方（装机器 / 种信任 hook）的事。

被 CLI 脚本与 hook 以同目录 import 方式使用，因此：
  - 模块 import 必须无副作用（不碰 stdout、不读环境、不建目录）
  - 文件名用下划线而非连字符——连字符的模块名 import 不进来
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

CLIENT_NAME = "workframe"
CLIENT_VERSION = "1"
DEFAULT_TIMEOUT = 30.0

# npm 包内真正的可执行文件，相对 `codex.cmd` 所在目录（本机读数；别的平台由 PATH / env 给）
_NPM_VENDOR_REL = ("node_modules", "@openai", "codex", "node_modules", "@openai")


class AppServerError(Exception):
    """app-server 起不来 / 超时 / 返回 error 对象。文案带原因，调用方直接透传给用户。"""


class AppServerRpcError(AppServerError):
    """其中**服务端明确回了 error 对象**的那一种：请求送达、对方答了「不行」。超时、进程退出、写请求失败
    都不是这一种——那几种下请求可能已经生效，只是答复没回来。要对用户说「没写成」的调用方只能认这一种。"""


def find_codex_exe(env=None):
    """定位 codex 可执行文件：`WF_CODEX_EXE` > PATH 上的 `codex`（若是 npm 包装则改指包内 exe）。

    返回字符串路径；找不到返回 None（调用方决定怎么报）。
    """
    env = os.environ if env is None else env
    override = env.get("WF_CODEX_EXE")
    if override:
        return override if Path(override).exists() else None
    which = shutil.which("codex", path=env.get("PATH"))
    if not which:
        return None
    p = Path(which)
    if p.suffix.lower() == ".exe":
        return str(p)
    # npm 的 `codex` / `codex.cmd` 是 node 包装；直指包内 exe 才能干净地按进程树杀
    base = p.parent.joinpath(*_NPM_VENDOR_REL)
    if base.is_dir():
        for cand in sorted(base.glob("codex-*/vendor/*/bin/codex.exe")):
            return str(cand)
        for cand in sorted(base.glob("codex-*/vendor/*/bin/codex")):
            return str(cand)
    return str(p)


def kill_tree(proc):
    """按进程树杀掉 app-server；任何异常都吞掉（调用方在收尾路径上，不能再抛）。"""
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class AppServer:
    """`with AppServer(...) as s: s.hooks_list(cwd)`。退出 with 时按进程树杀。"""

    def __init__(self, exe=None, codex_home=None, cwd=None, env=None, timeout=DEFAULT_TIMEOUT):
        self.exe = exe or find_codex_exe(env)
        if not self.exe:
            raise AppServerError("找不到 codex 可执行文件（PATH 上没有 codex，也未设 WF_CODEX_EXE）")
        self.env = dict(os.environ if env is None else env)
        if codex_home:
            self.env["CODEX_HOME"] = str(codex_home)
        self.cwd = str(cwd) if cwd else None
        self.timeout = timeout
        self.proc = None
        self.notifications = []
        self.stderr_lines = []
        self._pending = {}
        self._lock = threading.Lock()
        self._next_id = 0
        self._reader = None
        self._stderr_reader = None
        self._dead = threading.Event()

    # ---- 生命周期 ----
    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def start(self):
        self.proc = subprocess.Popen(
            [self.exe, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=self.cwd, env=self.env)
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_reader.start()
        self.initialize_result = self.request(
            "initialize", {"clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION}})
        self.notify("initialized", {})
        return self

    def close(self):
        kill_tree(self.proc)
        self._dead.set()

    # ---- 收发 ----
    def _read_stdout(self):
        try:
            for raw in self.proc.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if isinstance(msg, dict) and "id" in msg and ("result" in msg or "error" in msg):
                    with self._lock:
                        self._pending[msg["id"]] = msg
                else:
                    self.notifications.append(msg)
        except Exception:
            pass
        finally:
            self._dead.set()

    def _read_stderr(self):
        try:
            for raw in self.proc.stderr:
                self.stderr_lines.append(raw.decode("utf-8", errors="replace").rstrip("\n"))
        except Exception:
            pass

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _send(self, obj):
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except Exception as e:
            raise AppServerError(f"向 app-server 写请求失败: {type(e).__name__}: {e}")

    def request(self, method, params=None, timeout=None):
        """发一条请求、等它的响应；`error` 对象转 `AppServerError`。返回 `result`。"""
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            with self._lock:
                msg = self._pending.pop(rid, None)
            if msg is not None:
                if "error" in msg:
                    err = msg["error"]
                    raise AppServerRpcError(f"{method} 返回错误: {err.get('code')} {err.get('message')}"
                                            if isinstance(err, dict) else f"{method} 返回错误: {err}")
                return msg.get("result")
            if self._dead.is_set() and self.proc.poll() is not None:
                tail = "\n".join(self.stderr_lines[-5:])
                raise AppServerError(f"app-server 已退出（exit {self.proc.returncode}），"
                                     f"{method} 无响应；stderr 尾: {tail[:200]}")
            if time.monotonic() > deadline:
                raise AppServerError(f"{method} 超时（{timeout or self.timeout}s 无响应）")
            time.sleep(0.01)

    # ---- 常用请求 ----
    def hooks_list(self, cwds=None):
        return self.request("hooks/list", {"cwds": [str(c) for c in (cwds or [])]})

    def config_read(self, include_layers=True, cwd=None):
        """`cwd` 给定时返回**从该目录看到的有效配置**（含项目层 `.codex/config.toml`，须项目已 trust）；
        不给只有 user / system 两层——项目层的 `[agents.*]` 在不带 cwd 的读法里恒为 null（实测）。"""
        params = {"includeLayers": bool(include_layers)}
        if cwd:
            params["cwd"] = str(cwd)
        return self.request("config/read", params)

    def config_batch_write(self, edits, reload_user_config=None, file_path=None,
                           expected_version=None):
        params = {"edits": list(edits)}
        if reload_user_config is not None:
            params["reloadUserConfig"] = bool(reload_user_config)
        if file_path:
            params["filePath"] = str(file_path)
        if expected_version:
            params["expectedVersion"] = expected_version
        return self.request("config/batchWrite", params)


# ---- 纯函数：对 hooks/list 结果的读法 ----
def hooks_of(resp, cwd_index=0):
    """`hooks/list` 结果 → 该 cwd 条目的 `hooks` 列表。顶层键是 `data`（不是 `hooks`）。

    结构不对时**抛**而不是返回空列表——空列表会被读成「0 条」，与真的 0 条同形。
    """
    data = resp.get("data") if isinstance(resp, dict) else None
    if not isinstance(data, list) or len(data) <= cwd_index:
        raise AppServerError(f"hooks/list 响应无 data[{cwd_index}]：{str(resp)[:200]}")
    entry = data[cwd_index]
    hooks = entry.get("hooks") if isinstance(entry, dict) else None
    if not isinstance(hooks, list):
        raise AppServerError(f"hooks/list 的 data[{cwd_index}] 缺 hooks 列表：{str(entry)[:200]}")
    return hooks


def plugin_hooks(resp, plugin_id, cwd_index=0):
    """只取本插件的条目（`pluginId == plugin_id`）：`hooks/list` 返回全部源（用户 / 项目 /
    每个插件），装机对账不能拿总数比。"""
    return [h for h in hooks_of(resp, cwd_index) if h.get("pluginId") == plugin_id]


def trust_edits(hooks):
    """把一批 hook 条目变成 `config/batchWrite` 的 edits：只从回显的 `currentHash` 取值，不自己算。"""
    edits = []
    for h in hooks:
        key, cur = h.get("key"), h.get("currentHash")
        if not key or not cur:
            continue
        edits.append({"keyPath": f'hooks.state."{key}".trusted_hash',
                      "mergeStrategy": "upsert", "value": cur})
    return edits


def _main(argv):
    """探针用 CLI：`_codex_appserver.py hooks <cwd> [--home H]` 打印 hooks/list。"""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["hooks", "config"])
    ap.add_argument("cwd")
    ap.add_argument("--home", default=None)
    ap.add_argument("--exe", default=None)
    a = ap.parse_args(argv)
    with AppServer(exe=a.exe, codex_home=a.home, cwd=a.cwd) as s:
        out = s.hooks_list([a.cwd]) if a.what == "hooks" else s.config_read(True)
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
