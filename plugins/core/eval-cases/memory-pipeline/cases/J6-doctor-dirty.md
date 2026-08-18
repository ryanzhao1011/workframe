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
- 断言（撤脏后）：8 项检查 0 非绿
- 实测 2026-08-07：✅ 双向全过（脏态三抓三中、撤后全绿）
