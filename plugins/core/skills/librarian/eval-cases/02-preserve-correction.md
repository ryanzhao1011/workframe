# Librarian eval · 02 Never decay [纠正] entry

**类型**：正例（保护）

## 输入状态
- `.claude/agent-memory/shared/MEMORY.md` 含一条：
  > `- [纠正] 2026-02-01：研发任务必须流转 in_progress → pending_qa → completed，@dev 不能直接标记 completed`
- memory-index.json 对应 entry：`protected: true`, `provenance: user-decree`, `(today - created_at) > 180 天`

## 期望行为
- Librarian 识别 `source=[纠正]` 或 sidecar `protected=true`
- 即使年龄超过衰减阈值（>180 天），**不生成降级候选**（protected 条目不参与衰减判据）
- 条目保留原状

## 验证命令
- 运行 Librarian 后，MEMORY.md 中该条目仍存在
- events.jsonl 无对应 `memory_decayed` 事件
