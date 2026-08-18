# Librarian eval · 03 Dormant project freezes all maintenance

**类型**：回归（失败/冻结）

## 输入状态
- `.claude/workframe-state/activity-state.json`: `{"dormant": true, "dormant_profile": "low-frequency"}`
- notes.md 有大量待评估条目
- MEMORY.md 接近容量

## 期望行为
- Librarian 第 3 步读到 `dormant=true` 后**本步跳过**（不生成衰减提案、不触发 promote）
- 第 5 步 skill-metrics 重算仍可执行（只读操作，不违反 dormant 冻结约定）
- 提示用户 `/core:maintenance-review` 可恢复

## 验证命令
- MEMORY.md / notes.md 运行后字节大小不变
- events.jsonl 无 `memory_promoted` / `memory_decayed` 事件
