#!/usr/bin/env python3
"""
SubagentStart Hook — 角色记忆注入

把跨角色权威事实（shared/MEMORY.md）与该角色高置信记忆（<role>/MEMORY.md）作为
additionalContext 注入 subagent 上下文，把启动协议的"必读记忆"从协议约定（靠模型
显式 Read）升级为代码保证（hook 注入）。

**本 hook 只投记忆，不投任何 skill 指引。** 子 agent 该在什么时候调哪个 skill，由会话
的 skill 清单（各 skill 的 description）驱动；唯一固定植入的那条（动看板前先调
`core:task-management`）写在子 agent 必载片里、随 `inject-context.py --scope sub` 投递，
不在本文件。

注入规则：
- shared/MEMORY.md：对所有 agent 注入（含 Explore / general-purpose 等内置 agent）——
  跨角色纪律（时间戳规范等）对任何执行者都适用。
- <role>/MEMORY.md：仅当 agent_type 映射出的角色记忆目录存在时追加注入。
  映射规则：agent_type 去掉 plugin 前缀（"core:pm" → "pm"）；项目级 agent 用裸名。
- notes.md 不注入（缓冲区，未达 D/U/R/A，维持启动协议口径）。
- 两份记忆都拿不出 → 静默退出，零注入。

stdin: Claude Code SubagentStart JSON（含 agent_type / agent_id / cwd 等）。
stdout: hookSpecificOutput.additionalContext JSON。中文内容必须显式 UTF-8 输出，
否则 Windows 默认 GBK 会乱码（实测）。

标记头 "[workframe] 角色记忆注入" 与子 agent 必载片 §记忆已经注入，不要再 Read 的兜底判据配对：
subagent 上下文有此标记 → 记忆已送达；无标记 → 按 §1 清单显式 Read 兜底。
**零记忆时这个标记头必须缺席**——它承诺的是「记忆已送达」，没送到还出标记，兜底 Read
就会被跳过。
"""

import io
import json
import os
import re
import sys
from pathlib import Path

# 同目录公共模块：运行态目录与 harness 差异各只有一份实现
# （见 _state_io.py / _harness.py 抬头）
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import _harness  # noqa: E402
from _harness import project_dir as _project_dir  # noqa: E402
from _state_io import memory_dir_of  # noqa: E402

MARKER = "[workframe] 角色记忆注入（SubagentStart hook 自动）"
ROLE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _read_memory(path: Path):
    """读取记忆文件；不存在 / 为空 / 读取失败均返回 None。

    编码用 utf-8-sig + errors="replace"：此前只捕 OSError，MEMORY.md 里混进非法字节
    就抛 UnicodeDecodeError 直接掀掉整个 hook，角色一条记忆都拿不到且没有任何提示。
    坏字节替换成 U+FFFD 至少让其余内容照常注入。
    """
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8-sig", errors="replace").strip()
        return text or None
    except OSError:
        return None


def main():
    # Codex 门下会话不在 workframe 项目内：零写入、零输出退出（判定与门条件只在 _harness 一处）
    if _harness.hook_outside_project():
        return 0
    # 包装本身也可能失败（stdin/stdout 已被关闭或替换过），失败就用原样的流继续
    try:
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    project_dir = _project_dir(payload)
    memory_dir = memory_dir_of(project_dir)
    # 注入头里报**实际读的那个目录**（问 `_state_io` 现算），不写死字面：写死的那份与实际读的
    # 一旦漂开，兜底 Read 路径（agent-protocols §1「无标记段则显式 Read」）就指向一个不存在的文件
    mem_rel = memory_dir.relative_to(project_dir).as_posix()

    # agent_type → role：去 plugin 前缀（"core:pm" → "pm"）；防路径注入，只放行常规命名
    agent_type = payload.get("agent_type") or ""
    # 外部输入：不是字符串时（数字 / 列表 / 对象）`rsplit` 会抛，整个 hook 以 exit 1 退出、
    # **shared 那份也跟着注不进去**。按「解析不出角色」处理，shared 照常送达；说明句如实报类型错。
    type_bad = not isinstance(agent_type, str)
    if type_bad:
        agent_type = ""
    role = agent_type.rsplit(":", 1)[-1] if agent_type else ""
    if role and not ROLE_NAME_RE.match(role):
        role = ""

    sections = []
    role_memory = None
    shared = _read_memory(memory_dir / "shared" / "MEMORY.md")
    if shared is not None:
        sections.append(
            f"=== {mem_rel}/shared/MEMORY.md（跨角色权威事实）===\n" + shared
        )
    if role and role != "shared":
        role_memory = _read_memory(memory_dir / role / "MEMORY.md")
        if role_memory is not None:
            sections.append(
                f"=== {mem_rel}/{role}/MEMORY.md（本角色高置信记忆）===\n" + role_memory
            )

    if not sections:
        return  # 零注入：两份记忆都拿不出；标记头随之缺席，子 agent 走兜底 Read

    # 说明句按实际注入内容拼装——只注 shared 时明示原因，防 agent 误以为漏发角色记忆。
    #
    # **只注 shared 那一支必须报出实际看到的 `agent_type`，这不是文案润色。**
    # `agent_type` 对**具名 teammate 就是那个名字**（实测：`core:qa` 拿到 5,350 单元，
    # `qa-080-pkg2-t102` 拿到 1,857），而它照样通过 `ROLE_NAME_RE`，于是角色记忆**静默落空**。
    # **teammate 形态只在开启 agent teams 时出现**：未开启时具名派发仍是普通 subagent，
    # `agent_type` 是定义名；而 teammate 连插件内的 agent 定义本身都不应用（官方口径：只套用
    # project / user / managed scope 的定义），缺的不止角色记忆。原文案「本 agent 无角色记忆
    # 目录」对一个确实有目录的角色是假话，且读的人无从知道为什么。**CC 的 SubagentStart
    # payload 里没有第二个字段能拿到真实角色类型**（实测该 payload 只有 7 个字段，
    # `agent_type` 取到的就是自定义 agent 名）⇒ 猜不了，也**有意不猜**（前缀猜角色如
    # `qa-*` → `qa` 的假阳性代价高于现状）。能做的是把静默失效变成可见失效：原样报出这一个
    # 字符串，读的人一眼看出「这是 teammate 名不是 `core:qa`」。
    # **按「缺的是哪一份」分支，不按份数**：只剩角色记忆（shared 缺席）时份数同样是 1，
    # 按份数判会对它误报「只解析到 shared」。
    if role_memory is None:
        if type_bad:
            seen = "（payload 的 agent_type 不是字符串）"
        else:
            seen = f"`{agent_type}`" if agent_type else "（payload 未给 agent_type）"
        note = ("启动协议必读记忆已由本段送达，无需再显式 Read。\n"
                f"**按 agent_type {seen} 只解析到 shared**：没有取到本角色的记忆。"
                "若本角色本该有，常见成因有三：派发时起了名（`agent_type` 会变成那个名字，"
                "而角色解析只认它）；该角色的 `MEMORY.md` 不存在或为空；payload 没给 "
                "`agent_type`（无从解析角色）。\n\n")
    else:
        note = "启动协议必读记忆已由本段送达，无需再显式 Read 下列文件。\n\n"
    # 结尾那行闭围栏不是装饰：记忆各段用 `=== <path> ===` 作**开**围栏而没有闭围栏，
    # 而同一事件下其他 hook 的注入物（子 agent 必载片）会紧贴在后面出现。实测（真机派发
    # 一个记忆很短的角色）：紧贴 MEMORY.md 抬头出现的那段注入物，**读起来就是那个文件的
    # 正文**——两个后果：注入物的权威等级被降成「我以前记的笔记」，以及日后有人让该角色
    # 「清理自己的 MEMORY.md」时，模型会以为那几行在文件里。闭围栏一行就把辖区断开。
    context = f"{MARKER}\n" + note + "\n\n".join(sections) + "\n=== 记忆注入结束 ==="
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": context,
        }
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
