#!/usr/bin/env python3
"""
tools/sync-rules.py — 手动同步通用 rules 到某个项目（框架仓侧入口）

用法：
    python tools/sync-rules.py --project "/path/to/project"

实现复用 `plugins/core/scripts/sync-rules.py` 的 `sync_rules()`——单一实现、双入口，
marketplace 订阅的用户只有插件本体、没有仓根 tools/，同步能力必须长在插件里；
本文件是给 clone 框架仓的贡献者用的薄壳。

使用场景：
- 框架升级后手动同步（正常情况由 SessionStart hook 自愈同步，本入口是故障排查手段）
- Plugin hook 同步失败时的补救

严格约束（由被复用的实现保证）：
- 只覆盖 <project>/.claude/rules/workframe/core/，不动项目专有 rules
- 就地覆盖 + 清理多余（无空窗，防并行 hook 竞态）；框架侧删除的 rule 由 prune 步骤移除
"""

import argparse
import importlib.util
import sys
from pathlib import Path

# 本文件**刻意不包 UTF-8 流**（`check_utf8_stream_wrap_symmetric` 豁免 importlib 加载方）：
# 下面加载的 plugins/core/scripts/sync-rules.py 是模块级包装，加载那一刻两条流已包好。
# 再包一层就是两个 TextIOWrapper 抢同一个 buffer——先被回收的把 buffer 关掉，
# 另一个当场 `ValueError: I/O operation on closed file` + `lost sys.stderr`。加过一次，
# 本脚本立刻全崩，而 validate 当时全绿（没有闸跑它）——静态检查看不见这类坏法。

FRAMEWORK_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_CORE = FRAMEWORK_ROOT / "plugins" / "core"

# 插件内脚本名含连字符，不是合法模块名，按文件路径加载
_spec = importlib.util.spec_from_file_location(
    "workframe_sync_rules", PLUGIN_CORE / "scripts" / "sync-rules.py"
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
sync_rules = _mod.sync_rules


def main():
    # 不在此处包 UTF-8 stdout：被导入的插件模块在 import 时已经包过一层，
    # 再包一层会有两个 wrapper 抢同一个 buffer，先被回收的那个把 buffer 关掉，
    # 另一个随即抛 "I/O operation on closed file"。
    parser = argparse.ArgumentParser(description="Sync workframe core rules to a project")
    parser.add_argument("--project", required=True, help="Absolute path to the target project")
    args = parser.parse_args()

    project_dir = Path(args.project).resolve()
    if not project_dir.is_dir():
        print(f"[sync-rules] ERROR: project directory not found: {project_dir}", file=sys.stderr)
        sys.exit(1)

    count, error = sync_rules(project_dir, plugin_root=PLUGIN_CORE)
    if error:
        print(f"[sync-rules] ERROR: {error}", file=sys.stderr)
        sys.exit(1)

    print(f"[sync-rules] synced {count} rules to "
          f"{project_dir / '.claude' / 'rules' / 'workframe' / 'core'}")
    sys.exit(0)


if __name__ == "__main__":
    main()
