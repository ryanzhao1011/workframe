# 共享记忆（跨角色权威事实）

> **读写契约**（workframe 应用层约定，**不是 Claude Code 官方自动加载**）
>
> - **写入条件**：影响 ≥2 个角色的执行经验（先过归属分流：业务知识落 modules/ 权威文档，模块作业提醒写对应 `module-memory.md`，均不新增 role/shared 指针——必载片 Step 2）；冲突解决以本文件为权威
> - **读取契约**：子 agent——本文件与 `<role>/MEMORY.md` 由 SubagentStart hook 自动注入
>   上下文（`subagent-memory-inject.py`）；「角色记忆注入」「共享记忆注入」标记段给了全文的那份不再
>   显式 Read，没有出现或标「未完整加载」的那份按子 agent 必载片兜底 Read。主会话——由 hook 在开场投递本文件
>   全文、在工作入口投递角色记忆（两门相同）
> - **优先级**（应用层）：`shared/MEMORY.md` > `<role>/MEMORY.md` > `<role>/notes.md`
> - 两份记忆都**不被 Claude Code 官方机制自动加载**（框架不使用 agent `memory`
>   frontmatter），共享语义完全由 hook 注入 + 兜底 Read 实现
>
> 格式：Markdown 列表。每条尽量简短，含上下文（日期/来源）。带 `[纠正]` 标记的条目**永不清理**。

## 高置信事实（跨角色）

<!-- 示例（请根据实际项目填写后删除示例）：
- 2026-04-10：`projects/board.yaml` 的 `summary:` 段由 SessionEnd hook 自动重算 + SessionStart drift check 兜底；其他角色更新 tasks 条目时不修改 summary 段
- [纠正] 2026-04-12：研发任务必须流转 `in_progress → pending_qa → completed`；`pending_qa → completed` 在角色侧仅 @qa（@dev 不能直接标记 completed）。主 Claude 自签是另一条通道，判据见必载片 §谁签发这次收口
-->

## 项目级共识
