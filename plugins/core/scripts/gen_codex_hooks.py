#!/usr/bin/env python3
"""从 CC 的 `hooks/hooks.json` 生成 Codex 的 `hooks/hooks.codex.json`（派生规则见 `_codex_hooks.py`）。

用法（在任意目录，路径按本文件自定位）：
    gen_codex_hooks.py                 # 写 hooks/hooks.codex.json（确定性、LF）
    gen_codex_hooks.py --check         # 只比对：产物与仓内文件逐字节相同 → 0，否则 1
    gen_codex_hooks.py --stdout        # 打印产物，不写盘
    gen_codex_hooks.py --accept-manifest
        # 把**当前生成结果**接受为配置审查基线 hooks/hooks.codex.manifest.json，并打印相对旧基线
        # 新增 / 配置变化 / 删除各几条——这一步是人做的显式动作，生成器平时**不**碰 manifest。
        # 这个数不是用户侧需恢复信任的条数：插件内容变化就可能让全部 hook 失信，用户升级走 workframe-door --upgrade。

为什么 manifest 不随生成器写：manifest 是维护者显式接受过的那份配置；它若与清单同一次生成，
`check_codex_hook_lines_frozen`（现清单 vs manifest）在 `check_codex_hooks_generated_in_sync`
绿时必然绿，于是「改 hooks.json → 重生成 → 提交」这条通道两道闸都拦不住，配置变化没有任何人审过。

写盘一律 bytes（LF）：Windows 文本模式会把 LF 落成 CRLF，一次写入整文件 diff。
"""

import argparse
import io
import json
import sys
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _codex_hooks as ch  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def generate():
    cc = _read_json(PLUGIN_ROOT / ch.CC_MANIFEST_REL)
    return ch.derive(cc)


def _normalized(path):
    return path.read_bytes().replace(b"\r\n", b"\n") if path.exists() else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--stdout", action="store_true")
    ap.add_argument("--accept-manifest", action="store_true")
    a = ap.parse_args(argv)

    try:
        data = generate()
    except (ch.DeriveError, OSError, ValueError) as e:
        print(f"gen_codex_hooks: 派生失败: {e}", file=sys.stderr)
        return 2
    text = ch.render(data)
    out_path = PLUGIN_ROOT / ch.CODEX_MANIFEST_REL

    if a.stdout:
        sys.stdout.write(text)
        return 0
    if a.check:
        cur = _normalized(out_path)
        if cur == text.encode("utf-8"):
            print(f"gen_codex_hooks: {ch.CODEX_MANIFEST_REL} 与生成结果逐字节相同"
                  f"（{ch.count_hooks(data)} 条）")
            return 0
        print(f"gen_codex_hooks: {ch.CODEX_MANIFEST_REL} 与生成结果不同"
              f"（{'文件不存在' if cur is None else '内容有差异'}）——重跑生成器并提交产物",
              file=sys.stderr)
        return 1
    if a.accept_manifest:
        base_path = PLUGIN_ROOT / ch.CODEX_BASELINE_REL
        new_manifest = ch.build_manifest(data)
        old = _read_json(base_path) if base_path.exists() else {"entries": []}
        old_by_key = {r.get("key"): r for r in old.get("entries") or []}
        config_changes = 0
        for row in new_manifest["entries"]:
            prev = old_by_key.get(row["key"])
            if prev is None or ch.hash_tuple(prev) != ch.hash_tuple(row):
                config_changes += 1
        base_path.write_bytes(ch.render(new_manifest).encode("utf-8"))
        removed = len(set(old_by_key) - {r["key"] for r in new_manifest["entries"]})
        print(f"gen_codex_hooks: 已接受 {len(new_manifest['entries'])} 条为配置基线 "
              f"{ch.CODEX_BASELINE_REL}；新增或配置变化 {config_changes} 条，删除 {removed} 条。"
              f"此数不是需恢复信任条数；插件内容变化可能影响全部 hook。"
              f"用户升级运行 workframe-door --upgrade；对外说明在发版时统一编写。")
        return 0

    out_path.write_bytes(text.encode("utf-8"))
    print(f"gen_codex_hooks: 已写 {ch.CODEX_MANIFEST_REL}（{ch.count_hooks(data)} 条 / "
          f"{len(data['hooks'])} 事件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
