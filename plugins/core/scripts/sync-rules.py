#!/usr/bin/env python3
"""
SessionStart Hook (self-healing) — 从 plugin 内部同步 core rules 到项目

从 plugin 内 rules/core/*.md 复制到项目 .claude/rules/workframe/core/，作为会话级自愈同步。

严格约束：
- 只覆盖 .claude/rules/workframe/core/ 子目录
- 不碰 .claude/rules/ 根目录下的项目专有 rules
- 不碰 .claude/rules/local/ 下的项目专有 rules
- 框架侧删除的 rule 文件通过 prune 步骤同步移除（就地覆盖 + 清理多余，无空窗——见 sync_rules docstring）

首次接入由 launcher 调本脚本（带 --project）完成主同步；此后每次 SessionStart
自愈兜底，框架升级时自动跟平。
"""

import argparse
import io
import os
import shutil
import stat
import sys
from pathlib import Path


# Windows 上强制 UTF-8 输出
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass


PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()

# Plugin 内脚本：优先用 ${CLAUDE_PLUGIN_ROOT}，降级用 __file__ 推导
plugin_root_env = os.environ.get("CLAUDE_PLUGIN_ROOT")
if plugin_root_env:
    PLUGIN_ROOT = Path(plugin_root_env).resolve()
else:
    # __file__ = plugins/core/scripts/sync-rules.py → plugins/core/
    PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def sync_rules(project_dir: Path, plugin_root: Path = None):
    """把 plugin 内 rules/core/*.md 同步到 <project>/.claude/rules/workframe/core/。

    返回 (同步份数, 错误消息或 None)。只碰 workframe/core 子目录，不动项目专有 rules。

    写法是**就地覆盖 + 清理多余**，不是「清空再复制」——SessionStart 的多个 hook
    **并行执行**，清空与重写之间存在空窗；首会话验收（run_first_session_acceptance
    的 rules_mirror 项）恰好在空窗读目录时会误报「镜像缺 N 份」，且正中新用户第一印象
    （2026-08-11 积分中心真机走查实证）。就地覆盖没有空窗：并发读者任一时刻看到的
    都是完整文件集；框架侧删除的 rule 仍由 prune 步骤移除，语义与旧实现一致。
    """
    src = (plugin_root or PLUGIN_ROOT) / "rules" / "core"
    dst = project_dir / ".claude" / "rules" / "workframe" / "core"

    if not src.exists():
        return 0, f"source rules not found in plugin: {src}"

    try:
        dst.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return 0, (f"failed to prepare target dir {dst}: {e} "
                   f"(check file locks / read-only attributes)")

    src_files = sorted(src.glob("*.md"))
    src_names = {f.name for f in src_files}
    count = 0
    failures = []
    for rule_file in src_files:
        target = dst / rule_file.name
        try:
            if target.exists():
                # Windows 只读位会让 copy2 覆盖失败，先解除。
                # 用「原 mode 或上写位」而不是 `S_IWRITE | S_IREAD`——后者等于 0o600，
                # 在 POSIX 上会把组与其他人的读权限一并清掉（多用户 / CI 共享 checkout
                # 是真实场景），而本意只是解除只读属性。
                try:
                    os.chmod(target, os.stat(target).st_mode | stat.S_IWRITE)
                except OSError:
                    pass
            shutil.copy2(rule_file, target)
            count += 1
        except Exception as e:
            print(f"[workframe sync-rules] failed to copy {rule_file.name}: {e}", file=sys.stderr)
            failures.append(f"copy {rule_file.name}: {e}")
    # prune：框架侧已删除的 rule 从镜像移除（放在覆盖之后，全程无空窗）
    for existing in sorted(dst.glob("*.md")):
        if existing.name in src_names:
            continue
        try:
            try:
                os.chmod(existing, os.stat(existing).st_mode | stat.S_IWRITE)
            except OSError:
                pass
            existing.unlink()
        except Exception as e:
            print(f"[workframe sync-rules] failed to prune {existing.name}: {e}", file=sys.stderr)
            failures.append(f"prune {existing.name}: {e}")
    if failures:
        return count, f"{count} synced, {len(failures)} failed — " + "; ".join(failures)
    return count, None


def main():
    # 显式 argparse：--project 之前被静默忽略（无参数解析），从别处调用时会把 rules
    # 同步到调用方当前目录却照常报 success。未知参数一律 error 退出，不再静默吞掉。
    parser = argparse.ArgumentParser(
        description="Sync core rules from the plugin into a project's .claude/rules/workframe/core/"
    )
    parser.add_argument(
        "--project",
        help="目标项目目录；缺省用 $CLAUDE_PROJECT_DIR，再缺省用当前目录",
    )
    args = parser.parse_args()

    # 退出码分化：显式 --project = CLI/launcher 调用（「非零即停」语义，失败必须可截获）；
    # 无 --project = SessionStart hook 调用（失败只报 stderr，exit 0 不阻塞会话）
    strict = args.project is not None
    project_dir = Path(args.project).resolve() if args.project else PROJECT_DIR
    if not project_dir.is_dir():
        print(f"[workframe sync-rules] target is not a directory: {project_dir}", file=sys.stderr)
        # strict 仅在显式传 --project 时为真（CLI / launcher 调用）；
        # exit-audited: SessionStart hook 不带该参数，hook 路径恒为 exit 0
        sys.exit(1 if strict else 0)

    count, error = sync_rules(project_dir)
    if error:
        print(f"[workframe sync-rules] {error}", file=sys.stderr)
        # exit-audited: 同上——hook 路径 strict=False，同步失败只报 stderr 不阻断会话
        sys.exit(1 if strict else 0)

    print(f"[workframe sync-rules] synced {count} rules to "
          f"{project_dir / '.claude' / 'rules' / 'workframe' / 'core'}")
    sys.exit(0)


if __name__ == "__main__":
    main()
