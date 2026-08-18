# Self-iteration eval · 01 Confidence < 0.5 does not produce proposal

**类型**：正例（门限过滤）

## 输入状态
- notes.md 某模式仅出现 2 次（occurrences=2），last_seen 20 天前
- 仅 1 个角色（dev）的 notes 提及
- 未被用户确认

## 期望计算
```
confidence = 0.35×min(2/5,1) + 0.30×recency(20,30) + 0.20×0.33 + 0.15×0
           = 0.35×0.4 + 0.30×0.33 + 0.20×0.33
           = 0.14 + 0.10 + 0.07
           = 0.31
```

## 期望行为
- `< 0.5` → 只写 `shared/notes.md`，不生成 proposal
- `proposals/pending/` 下无新文件
