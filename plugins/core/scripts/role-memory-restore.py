#!/usr/bin/env python3
"""SessionStart(matcher `compact`) Hook — 压缩后补投主会话已投过的角色记忆（两门）。

压缩后此前 hook 注入的记忆正文不在上下文里了（实测摘要只剩一句主题概括），而去重状态还记着「已投」，
不补投的话下一次同域触发会被当成重复跳过。本脚本按最近使用顺序补投本压缩周期内投过的角色，并把周期号
+1（`_role_memory.restore_after_compact`）。shared 与条目目录由同一事件下的 `session-start-prep.py` 照常投。

**为什么单独一条 hook 而不并进 session-start-prep**：CC 单条 hook 输出上限 1 万字，开场那条已经装了
shared、条目目录与启动信息，再塞角色全文必超。本 hook 只在压缩时跑，不在每次工具调用的路径上。
**不挂 PostCompact**：实测它返回 `hookSpecificOutput` 被 CC 校验拒绝。

stdout：CC 清单为纯文本（SessionStart 的纯文本 stdout 进上下文）；Codex 清单带 `--output hook-json`（派生规则 7），
改投 `hookSpecificOutput` JSON——Codex 把以 `[` 起头的纯文本 stdout 当 JSON 解析、失败即整条丢弃，而本脚本的输出
正以 `[workframe]` 起头。
"""
import io
import json
import sys
from pathlib import Path

try:
    sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402
try:
    import _role_memory  # noqa: E402
except Exception:  # 加载器坏了：补投做不了，但 SessionStart 这条 hook 不能非零退出
    _role_memory = None


def main():
    # Codex 门下会话不在 workframe 项目内：零写入、零输出退出（判定与门条件只在 _harness 一处）
    if _harness.hook_outside_project():
        return 0
    if _role_memory is None:
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    if not isinstance(payload, dict) or payload.get("source") != "compact":
        return 0
    try:
        text = _role_memory.restore_after_compact(_harness.project_dir(payload), payload)
    except Exception as e:
        print(f"[warn] role memory restore failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 0
    if text and _harness.output_mode() == _harness.OUTPUT_HOOK_JSON:
        print(_role_memory.hook_output("SessionStart", text))
    elif text:
        # 不做换行翻译：Windows 文本流默认把每个 `\n` 写成 `\r\n`，CC 收到的串按行数变长，预算算的是 `\n` 那份
        try:
            sys.stdout.reconfigure(newline="\n")
        except Exception:
            pass
        print(text)
    return 0


if __name__ == "__main__":
    # exit-audited: main() 的全部 return 均为 0——补投失败不能挡住会话
    sys.exit(main())
