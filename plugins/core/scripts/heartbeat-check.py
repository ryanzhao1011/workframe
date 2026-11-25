#!/usr/bin/env python3
"""
SessionStart Hook — 启动横幅

每次会话启动时打印一行 `[<项目名>] <日期> 会话启动`，再打一行「本会话插件根」（`_harness.plugin_root_line`：
skill 命令里的 `$WF_PLUGIN_ROOT` 靠它替换）。stdout 会注入模型上下文。

逾期任务提醒不在这里：它要等 session-start-prep 判完本次是否休眠 / 唤醒之后才能决定提不提，而两个 SessionStart
hook 的先后不定，所以由 session-start-prep 在判完之后调用 `_overdue.reminder_line`。

路径策略：
- 当前项目根目录：$CLAUDE_PROJECT_DIR（官方 hook 环境变量），降级为当前工作目录
- 项目名：从 <project>/.workframe-config.json 的 project_name 字段读取，降级用目录名
"""

import io
import json
import sys
from datetime import date
from pathlib import Path


# Windows 上强制 UTF-8 输出，防止 GBK/UTF-8 编码冲突
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


# 同目录公共模块：harness 差异只有一份实现（见 _harness.py 抬头）
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402
from _harness import project_dir as _project_dir  # noqa: E402

PROJECT_DIR = _project_dir()
CONFIG_FILE = PROJECT_DIR / ".workframe-config.json"


def get_project_name():
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            name = data.get("project_name")
            if name:
                return name
        except Exception:
            pass
    return PROJECT_DIR.name


def main():
    # Codex 门下会话不在 workframe 项目内：零写入、零输出退出（判定与门条件只在 _harness 一处）
    if _harness.hook_outside_project():
        return 0
    # Codex 清单带 `--output hook-json`：本脚本的 stdout 以 `[<项目名>]` 起头，Codex 会把它
    # 按 JSON 解析失败后整条丢弃；该模式下把全部输出收成一条 hookSpecificOutput。CC 不带此参数。
    _harness.hook_json_stdout("SessionStart")
    print(f"[{get_project_name()}] {date.today().strftime('%Y-%m-%d')} 会话启动")
    print(_harness.plugin_root_line())
    sys.exit(0)


if __name__ == "__main__":
    main()
