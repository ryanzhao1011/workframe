---
supersedes:
---

# 状态流转与记忆分工

## 任务状态流转与签发权限

研发类任务（`assigned_to: dev / prompt-eng` 的**编码 / 配置 / 变更类**任务，或 tags 含 `needs-qa-regression`）**必须经签发**（走 `pending_qa`；要不要独立验证、谁来验见 §谁签发这次收口）。@dev 的技术咨询与方案评估、@prompt-eng 的非研发类咨询、PM 分析、纯文档与系统维护任务都属非研发任务，不走 pending_qa（完整判据见 skill `task-management` §pending_qa 适用范围）：

```
研发任务：pending → in_progress → pending_qa → completed
非研发任务：pending → in_progress → completed
```

**谁能做哪一次状态变更**（认领、流转到 `pending_qa`、签发、打回、阻塞、取消）见 skill `task-management` §签发权限——建任务、改状态、签发或打回之前先调 `core:task-management`。要点：研发任务的 `pending_qa → completed` 在角色侧只有 @qa；主会话自签走 §谁签发这次收口 那条独立通道。

主会话直做研发任务时代行研发流转，`assigned_to` 仍填对应角色（域语义不变），并打 `tags: [main-executed]`（语义：主会话实际完成了使任务进入 `pending_qa` 的研发交付；只是参与讨论 / 出方案不打）。**代行本身不含签发权**——自签是 §谁签发这次收口 那条独立通道，不是「代行顺带获得的」。

## 记忆分工

项目的跨会话记忆按 **谁消费** 分工（归属与 D/U/R/A 准入见「Step 2 — 经验沉淀」一节）：

| 记忆 | 消费者 | 装什么 | 维护 |
|---|---|---|---|
| auto-memory（**CC 门**：官方记忆目录；索引对子 agent 也注入，但按需召回只在主会话侧发生） | 主会话 | 用户偏好 / 协作习惯 / 项目状态**指针** | 主会话自维护 |
| 角色记忆目录下的 `<role>/MEMORY.md` | 单个角色 | 该角色执行经验（D/U/R/A ≥2） | librarian + 角色 |
| 角色记忆目录下的 `shared/MEMORY.md` | ≥2 角色 | 跨角色权威事实（写入条件更严） | librarian |
| `<basic>/<sub>/module-memory.md` 或基础模块级同名文件 | 在该模块动手的人 | 作业提醒与必读入口，不写产品结论；按存在与命中投递 | 改模块文档时复查；librarian 定期扫描 |
