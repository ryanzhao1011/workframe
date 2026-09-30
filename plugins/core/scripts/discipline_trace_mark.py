#!/usr/bin/env python3
"""给 `module-close-check` 的 `[discipline-trace]` 某次红灯运行打标记（`bin/workframe-discipline-mark`）。

close-check 本身不带任何标记参数：检查工具只保留「追加自己的计数日志」这一种写入，
标记是另一次、由人发起的动作。本命令只做一件事——往同一份计数日志追加一行
`{ts, kind: "mark", fingerprint, verdict, marked_by}`（写入时连带该日志的 `.lock` 锁文件，
锁超时时另落 spill 文件），不改任何既有行；计数由 close-check 每次运行时从日志现算。

**标记挂在哪一次运行上**：该指纹在标记这一刻的**最后一次**运行（须是红）。计数侧同口径——
标记只作用于日志里排在它之前、指纹相同的最后一次运行，不会套到之后同指纹的另一次红上。

**取值**：
  - `--verdict missed`（确实漏做）：`--by qa | user | self`。处置方自认真红是自证其罪，
    不存在「自己说闸判错就能把计数清零」的激励问题，所以允许 `self`。
  - `--verdict false-positive`（闸判错）：只收 `--by qa | user`。被判的一方不能自己宣布
    闸判错——那会让计数归零、同时把这次纪律缺失一并免责。

**拒绝**（exit 2，stderr 说原因，零写入）：指纹前缀不是 ≥8 位十六进制；前缀命中 0 个或
≥2 个不同指纹；该指纹最近一次运行不是红；取值域外（含 `false-positive --by self`）。
成功 exit 0；日志主文件与 spill 都没写成 exit 1。同一次运行重复标记时，计数取最后一条。

**能力边界**：`marked_by` 验证不了是谁敲的命令——与 `self_signoff` 事件同构，它是可分组
的声明，不是身份证明。
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from _harness import project_dir as _harness_project_dir  # noqa: E402
import module_close_check as mcc  # noqa: E402

MIN_PREFIX = 8
BY_ALLOWED = {"missed": ("qa", "user", "self"), "false-positive": ("qa", "user")}

EPILOG = (
    "什么时候算「闸判错」：判定对当时的状态是错的。收尾步骤还没走完就跑出的红（例如看板"
    "流转排在收口检查之后）不算闸判错——判定对当时的状态是对的；走完再重跑变绿后，同一账本"
    "条目标题下的多次运行按同一次收口合并，取最后一次。"
    "指纹前缀取自 close-check 输出里「本次运行指纹」那一行。"
)


def _reject(msg):
    print(f"workframe-discipline-mark: {msg}", file=sys.stderr)
    return 2


def build_parser():
    ap = argparse.ArgumentParser(
        prog="workframe-discipline-mark",
        description="给 module-close-check 的 [discipline-trace] 某次红灯运行打标记（只追加一行）",
        epilog=EPILOG)
    ap.add_argument("--project", default=None,
                    help="项目根；缺省按 _harness.project_dir() 解析")
    ap.add_argument("--fingerprint", required=True, help="运行指纹前缀（≥8 位十六进制）")
    ap.add_argument("--verdict", required=True, choices=sorted(BY_ALLOWED),
                    help="missed = 确实漏做；false-positive = 闸判错")
    ap.add_argument("--by", dest="by", required=True, choices=("qa", "user", "self"),
                    help="谁标的（false-positive 只收 qa / user）")
    return ap


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    if args.by not in BY_ALLOWED[args.verdict]:
        return _reject(f"--verdict {args.verdict} 只收 --by {' | '.join(BY_ALLOWED[args.verdict])}"
                       f"（被判的一方不能自己宣布闸判错）")
    prefix = args.fingerprint.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{%d,64}" % MIN_PREFIX, prefix):
        return _reject(f"指纹前缀须是至少 {MIN_PREFIX} 位的十六进制（收到 {args.fingerprint!r}）")
    project = Path(args.project).resolve() if args.project else _harness_project_dir()
    if not project.is_dir():
        return _reject(f"目标不是目录: {project}")

    runs = [r for r in mcc.read_trace_rows(project)
            if r.get("kind") == "run" and isinstance(r.get("fingerprint"), str)]
    fps = sorted({r["fingerprint"] for r in runs if r["fingerprint"].startswith(prefix)})
    if not fps:
        return _reject(f"前缀 {prefix} 在计数日志里命中 0 个运行")
    if len(fps) >= 2:
        return _reject(f"前缀 {prefix} 命中 {len(fps)} 个不同指纹（"
                       + "、".join(f[:mcc.FINGERPRINT_SHOW] for f in fps[:5])
                       + "）——给长一点的前缀")
    fp = fps[0]
    last = [r for r in runs if r["fingerprint"] == fp][-1]
    if last.get("result") != "red":
        return _reject(f"指纹 {fp[:mcc.FINGERPRINT_SHOW]} 最近一次运行的结果是 "
                       f"{last.get('result')!r}，不是红——只有红灯运行可以标记")

    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": "mark",
           "fingerprint": fp, "verdict": args.verdict, "marked_by": args.by}
    written = mcc.append_trace_row(project, row)
    if written is None:
        print("workframe-discipline-mark: 计数日志主文件与 spill 都没写成，本次标记未落盘",
              file=sys.stderr)
        return 1
    note = "" if last.get("exit_code") == 0 else "；该运行 exit_code 非 0，本就不进计数，标记对计数无影响"
    print(json.dumps({"status": "ok" if written is True else "spilled", "row": row,
                      "note": note.lstrip("；")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # exit-audited: CLI 命令不挂 hook——0 已写 / 1 没写成 / 2 拒绝（含 argparse 用法错误）
    sys.exit(main())
