#!/usr/bin/env python3
"""
SessionStart Hook — HEARTBEAT 周期检查

每次会话启动时自动执行，检查项目内逾期项并输出提醒。stdout 会注入 Claude 上下文。

路径策略（v7.1 plugin 化后）：
- 当前项目根目录：$CLAUDE_PROJECT_DIR（官方 hook 环境变量），降级为当前工作目录
- State 文件：<project>/.claude/workframe-state/heartbeat-state.json（项目内，plugin 目录是只读缓存）
- Board 文件：<project>/projects/board.yaml
- 项目名：从 <project>/.workframe-config.json 的 project_name 字段读取，降级用目录名
"""

import io
import json
import os
import sys
from datetime import date
from pathlib import Path


# Windows 上强制 UTF-8 输出，防止 GBK/UTF-8 编码冲突
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
STATE_DIR = PROJECT_DIR / ".claude" / "workframe-state"
STATE_FILE = STATE_DIR / "heartbeat-state.json"
ACTIVITY_FILE = STATE_DIR / "activity-state.json"
BOARD_FILE = PROJECT_DIR / "projects" / "board.yaml"
CONFIG_FILE = PROJECT_DIR / ".workframe-config.json"


def load_activity():
    if ACTIVITY_FILE.exists():
        try:
            return json.loads(ACTIVITY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"dormant": False, "dormant_profile": "normal", "pending_maintenance": []}


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


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"weekly_monday": "", "weekly_friday": "", "monthly": ""}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8", newline="")


def _yaml_scalar(raw):
    """取 YAML 行尾标量：剥掉行尾注释与包裹引号。

    `status: completed  # 已验收` 此前整串参与比较，与 "completed" 不相等——于是
    board 模板自带的注释写法会让已完成任务被算成逾期。
    引号内的 `#` 是值的一部分，不剥。
    """
    v = raw.strip()
    if v[:1] in ("'", '"'):
        end = v.find(v[0], 1)
        if end != -1:
            return v[1:end]
    return v.split("#", 1)[0].strip().strip('"').strip("'")


def count_overdue_tasks():
    """解析 board.yaml 中的逾期任务（按键值对解析，不依赖行顺序）。

    返回 `(count, error)`：读不出来时 count 为 0 但 error 非空——**「没有逾期」和
    「没能检查」必须分开**，此前统一吞成 0，board 非法 UTF-8 / 不可读 / 路径是目录时
    照样输出 `HEARTBEAT_OK — 无待处理事项`。
    """
    if not BOARD_FILE.exists():
        return 0, None

    count = 0
    today_str = date.today().isoformat()

    try:
        content = BOARD_FILE.read_text(encoding="utf-8")
        # 按任务块分割（每个 "- id:" 开始一个新任务）
        task_blocks = content.split("- id:")
        for block in task_blocks[1:]:  # 跳过第一个（summary 区）
            has_deadline = False
            is_overdue = False
            is_open = True

            for line in block.split("\n"):
                stripped = line.strip()
                if stripped.startswith("deadline:"):
                    deadline_val = _yaml_scalar(stripped.split(":", 1)[1])
                    if deadline_val and deadline_val < today_str:
                        has_deadline = True
                        is_overdue = True
                if stripped.startswith("status:"):
                    status_val = _yaml_scalar(stripped.split(":", 1)[1])
                    if status_val in ("completed", "cancelled"):
                        is_open = False

            if has_deadline and is_overdue and is_open:
                count += 1
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"

    return count, None


def main():
    today = date.today()
    state = load_state()
    activity = load_activity()
    state_changed = False

    current_week = today.isocalendar()[1]
    week_key = f"{today.year}-W{current_week:02d}"
    month_key = f"{today.year}-{today.month:02d}"
    weekday = today.weekday()  # 0=Mon, 4=Fri

    project_name = get_project_name()
    reminders = []

    # 系统启动横幅
    print(f"[{project_name}] {today.strftime('%Y-%m-%d')} 会话启动")

    # dormant / 首次唤醒：只展示状态，不检查逾期与周期提醒。
    # wake_up_pending 此前没纳入判据，于是久违回归的第一个会话在看 wake-up 摘要的同时
    # 还要挨一顿周期提醒，与「本次跳过自动维护」的口径不一致——其余三个消费方
    # （check-iteration-trigger / user-prompt-inject / memory-ask）都是双判据。
    if activity.get("dormant") or activity.get("wake_up_pending"):
        profile = activity.get("dormant_profile", "normal")
        if activity.get("dormant"):
            print(f"[dormant] 项目处于 dormant（profile={profile}），跳过周期检查。")
            print("  执行 /core:maintenance-review 恢复正常维护。")
        else:
            print("[wake-up] 首次唤醒会话，本次跳过周期检查。")
            print("  执行 /core:maintenance-review 可当场处理积压；否则下个会话自动恢复。")
        sys.exit(0)

    # 检查逾期任务
    overdue, board_err = count_overdue_tasks()
    if board_err:
        reminders.append(f"[注意] board.yaml 读不出来（{board_err}）——逾期任务这次没查成，"
                         f"不代表没有逾期")
    elif overdue > 0:
        reminders.append(f"[注意] 有 {overdue} 个任务可能逾期，请检查 board.yaml")

    # 周计划（周一-周四窗口）。与周复盘在周四重叠是有意的：本周首次开会话若已是周四，
    # 计划与复盘都该提一次。标签用职能名而非星期几，重叠时读起来才不别扭。
    if weekday <= 3 and state.get("weekly_monday") != week_key:
        reminders.append("[HEARTBEAT 周计划] 请检查：本周开发计划、上周未完成任务")
        state["weekly_monday"] = week_key
        state_changed = True

    # 周复盘（周四-周日窗口）
    if weekday >= 3 and state.get("weekly_friday") != week_key:
        reminders.append("[HEARTBEAT 周复盘] 请检查：本周任务统计、日志完整性")
        state["weekly_friday"] = week_key
        state_changed = True

    # 月度检查（1-7日提醒）
    if today.day <= 7 and state.get("monthly") != month_key:
        reminders.append(
            "[HEARTBEAT 月度] 请处理：任务归档、问题跟踪；"
            "执行 /core:maintenance-review 可触发 Librarian + self-iteration 一次完整整理"
        )
        state["monthly"] = month_key
        state_changed = True

    if reminders:
        for r in reminders:
            print(r)
    else:
        print("HEARTBEAT_OK — 无待处理事项")

    # 提醒输出后持久化状态（防止同一周期重复提醒）
    if state_changed:
        try:
            save_state(state)
        except Exception as e:
            print(f"[warn] failed to save heartbeat state: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
