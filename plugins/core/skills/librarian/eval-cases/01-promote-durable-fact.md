# Librarian eval · 01 Promote durable fact to MEMORY

**类型**：正例（成功）

## 输入状态
- `.claude/agent-memory/dev/notes.md` 含一条：
  > `2026-04-10: API 网关选型最终使用 Envoy，理由是 xDS 动态配置成熟度`（出现 3 次在不同 session）

## 期望行为
- D/U/R/A ≥2（D: 30 天后还重要 ✓；U: MEMORY 未记录 ✓；A: 已用户确认 ✓）
- 提升到 `.claude/agent-memory/dev/MEMORY.md`
- 同步在 `.claude/workframe-state/memory-index.json` 写 entry（source=librarian-promoted, protected=false, provenance=user-confirmed——本例 Authority 来自用户确认；归类判据见 SKILL「provenance 来源类型」表）
- append events.jsonl `memory_promoted`

## 验证命令（未来 eval harness）
- 检查 MEMORY.md 是否包含 "Envoy"
- 检查 memory-index.json 的 entry key 是否形如 `dev:2026-04-10:API网关...`
