# J6 doctor 造脏 / 撤脏双向

- 工具：`CLAUDE_PROJECT_DIR=<沙盒> python workframe_doctor.py --json`，输出落文件后
  utf-8 读（直接管道在 Windows 控制台会显示乱码，非 doctor 问题）
- 动作（造脏，fixtures/ 三件）：
  1. `bad-provenance-entry.json` 合入 sidecar（provenance=model-guess 非法枚举）
  2. `bad-event-line.txt` 追加 events.jsonl（`\s` 未转义的坏 JSON 行）
  3. `oversized-memory.md`（3683 字符）放入 auto-memory 目录 + 索引行
- 断言（脏态）：三项被**对应**检查项抓获、零误归——
  `sidecar_health` WARN 定位到 key 与非法值 / `events_parse` ERROR 定位到行号 /
  `auto_memory` WARN 报字符数与预算并引用治理口径
- 断言（撤脏后）：**runtime 组全部检查** 0 非绿
  ——造脏的三件都落在 runtime 组，故断言限定该组：跑 `--group runtime`，或跑上面那条
  不带 `--group` 的命令后只看 runtime 组那几行（**不带 `--group` 会把 install 与
  runtime 两组都跑一遍**）。**这里有意不写项数**——项数随 doctor 增删检查而变，写死
  就会变成假话；要看当前项数跑 `workframe_doctor.py --list`
- 实测 2026-08-07：✅ 双向全过（脏态三抓三中、撤后全绿）
