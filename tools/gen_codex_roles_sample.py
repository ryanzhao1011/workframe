#!/usr/bin/env python3
"""Codex 角色样本的**唯一重出入口**：`agents/*.md` → `tools/fixtures/codex-roles-sample/`。

用法（cwd 任意）：
    python tools/gen_codex_roles_sample.py

样本是什么：用 `plugins/core/scripts/codex_roles.py` 的渲染器，对当前 `agents/*.md` 以固定版本号
`SAMPLE`、无 `.local.toml` 定制、LF 行尾渲染出的 4 份 role TOML ＋ 1 份 `config.toml` managed 段
（`config-block.toml`）。它是「生成器漂移」的基线：validate 的 `check_role_twin_parity` 每次把
现渲染与它逐字节比（行尾归一后），不同即红——要么 `agents/<role>.md` 改了没重出样本，要么
渲染器本身漂了。**两种成因的处置不同**：前者跑本脚本重出并提交；后者先判渲染器的改动是不是
有意的，有意则同样重出，并在对外 CHANGELOG 未发布段写「升级后 Codex 角色配置变化」
（用户侧 `.codex/roles/<role>.toml` 会在下次 `workframe-door --upgrade` 时被重生成）。

为什么版本号固定为 `SAMPLE`：`DO_NOT_EDIT` 头标与 `render_block` 头行都带 `v{version}`，
传真实版本号的话每次 bump `plugin.json` 都要重出样本、闸都要红一次——那红与角色内容无关。

为什么放 `tools/fixtures/` 不放插件内：样本没有任何运行时消费方（`.workframe-meta/` 那些文件
各有 hook / 装机器读它们），放进插件只会随每次安装分发一份与 `agents/*.md` 同源的重复正文。

`render_sample()` 是本脚本与 validate 闸**共用**的渲染契约——闸 import 本模块调它，不另写一份，
两边渲染参数不可能分叉。本脚本只写样本目录，不删任何文件：agents 里已不存在的角色留下的旧样本
由本脚本点名报出、由人处理（闸也会红）。
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "core"
SAMPLE_DIR = REPO_ROOT / "tools" / "fixtures" / "codex-roles-sample"
SAMPLE_VERSION = "SAMPLE"
# managed 段段首启用表的插件 id 取 `core@<市场名>`；样本取定值，与真实装机时的市场名无关
SAMPLE_MARKETPLACE = "workframe"
BLOCK_FILE = "config-block.toml"

_SCRIPTS = str(PLUGIN_ROOT / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
import codex_roles  # noqa: E402  —— 渲染器唯一实现；本脚本只调用、不改


def render_sample(plugin_root=PLUGIN_ROOT):
    """{文件名: 渲染文本（LF）}。角色按 `codex_roles.load_agents` 的排序；`local_extra` 恒空。"""
    roles = codex_roles.load_agents(Path(plugin_root) / "agents")
    out = {}
    for role, _desc, body in roles:
        out[f"{role}.toml"] = codex_roles.render_role(role, body, SAMPLE_VERSION, "")
    out[BLOCK_FILE] = codex_roles.render_block(roles, SAMPLE_VERSION, SAMPLE_MARKETPLACE)
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    rendered = render_sample()
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    written = unchanged = 0
    for fname, text in rendered.items():
        p = SAMPLE_DIR / fname
        data = text.encode("utf-8")
        if p.is_file() and p.read_bytes().replace(b"\r\n", b"\n") == data:
            unchanged += 1
            continue
        p.write_bytes(data)          # 字节直写：Windows 上文本模式会把 LF 落成 CRLF
        written += 1
        print(f"  written   {p.relative_to(REPO_ROOT).as_posix()}")
    stale = sorted(p.name for p in SAMPLE_DIR.glob("*.toml") if p.name not in rendered)
    for s in stale:
        print(f"  stale     {(SAMPLE_DIR / s).relative_to(REPO_ROOT).as_posix()}"
              f"  ← agents/ 里已没有这个角色，本脚本不删文件，请自行处理（闸会红）")
    print(f"codex-roles-sample: {written} written, {unchanged} unchanged, {len(stale)} stale"
          f"（{len(rendered) - 1} 个角色 ＋ {BLOCK_FILE}，version={SAMPLE_VERSION}，LF）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
