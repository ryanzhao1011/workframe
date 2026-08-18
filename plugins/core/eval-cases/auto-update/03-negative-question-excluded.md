# auto-update eval · 03 Negative — question form excluded

**类型**：反例（应当不触发）

## 输入（任一均不触发）
- "最近竞品有什么新动态吗？"（提问）
- "如果换成 GPT-4 会怎样"（假设）
- "有人说竞品改版了"（转述未采纳）
- "之前我们讨论过..."（历史回顾）

## 期望行为
- `auto-update.md` 的排除规则命中 → **不触发任何写入**
- 不创建 issues / specs / board 任务
- 不写 MEMORY.md / notes.md
- 用户未陈述新事实 / 未下达指令

## 为什么重要
- 防止 Claude 对提问作为事实误记录，避免 MEMORY 噪声污染
- 歧义时应 Claude 先反问澄清（规则正文第 2 段）
