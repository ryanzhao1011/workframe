---
supersedes:
---

# 状态流转与记忆分工

## 任务状态流转与签发权限

研发类任务（`assigned_to: dev / prompt-eng` 的**编码 / 配置 / 变更类**任务，或 tags 含 `needs-qa-regression`）**必须经 QA 验证**。@dev 的技术咨询与方案评估、@prompt-eng 的非研发类咨询、PM 分析、纯文档与系统维护任务都属非研发任务，不走 pending_qa（完整判据见 skill `task-management` §pending_qa 适用范围）：

```
研发任务：pending → in_progress → pending_qa → completed
非研发任务：pending → in_progress → completed
```

| 状态变更 | 允许操作者 |
|---------|-----------|
| `in_progress → pending_qa` | @dev / @prompt-eng（研发任务完成时）；走 pending_qa 类的项目自定义角色同此；**主会话直做时代行** |
| `pending_qa → completed` | @qa（签发研发任务完成）。主会话**自签**走四段闸门那条独立通道（开关与举证见「谁签发这次收口」一节）；角色侧仍只有 @qa，签发权不随代行转移 |
| `pending_qa → blocked` | @qa（测试不通过时） |
| `in_progress → completed` | @pm / @dev（非研发任务）/ @qa（非研发任务）/ @prompt-eng（非研发类咨询）/ 分析、咨询、协调类项目自定义角色 |
| `pending → in_progress` | 该任务 `assigned_to` 的角色（认领即开工） |
| `任意 → blocked` | 任何角色（遇阻即可标，必须写 `blocked_reason`） |
| `blocked → in_progress` | 阻塞解除后由 `assigned_to` 角色自行恢复（QA 打回的由完成研发交付的角色修完直接恢复） |
| `任意 → cancelled` | @pm 或用户（必须写 `cancelled_at` + `cancel_reason`） |

主会话直做研发任务时代行上述流转，`assigned_to` 仍填对应角色（域语义不变），并打 `tags: [main-executed]`（语义：主会话实际完成了使任务进入 `pending_qa` 的研发交付；只是参与讨论 / 出方案不打）。**代行本身不含签发权**——自签是四段闸门那条独立通道，不是「代行顺带获得的」。

## 两套记忆分工

项目并行两套跨会话记忆，落点判据 = **谁消费**（归属与 D/U/R/A 准入见「Step 2 — 经验沉淀」一节）：

| 记忆 | 消费者 | 装什么 | 维护 |
|---|---|---|---|
| auto-memory（**CC 门**：官方记忆目录；索引对子 agent 也注入，但按需召回只在主会话侧发生） | 主会话 | 用户偏好 / 协作习惯 / 项目状态**指针** | 主会话自维护 |
| 角色记忆目录下的 `<role>/MEMORY.md` | 单个角色 | 该角色执行经验（D/U/R/A ≥2） | librarian + 角色 |
| 角色记忆目录下的 `shared/MEMORY.md` | ≥2 角色 | 跨角色权威事实（写入条件更严） | librarian |
