# 共享记忆（跨角色权威事实）

> **读写契约**（workframe 应用层约定，**不是 Claude Code 官方自动加载**）
>
> - **写入条件**：影响 ≥2 个角色的事实（先过归属分流：业务知识落 modules/ 文档、此处至多留一行指针——agent-protocols Step 2）；冲突解决以本文件为权威
> - **读取契约**：本文件与 `<role>/MEMORY.md` 由 SubagentStart hook 自动注入每个 agent
>   的上下文（`subagent-memory-inject.py`）；上下文中已有「[workframe] 角色记忆注入」
>   标记段时 agent 不再显式 Read，无标记段（hook 未生效）才按 agent-protocols §1 兜底 Read
> - **优先级**（应用层）：`shared/MEMORY.md` > `<role>/MEMORY.md` > `<role>/notes.md`
> - 两份记忆都**不被 Claude Code 官方机制自动加载**（框架不使用 agent `memory`
>   frontmatter），共享语义完全由 hook 注入 + 兜底 Read 实现
>
> 格式：Markdown 列表。每条尽量简短，含上下文（日期/来源）。带 `[纠正]` 标记的条目**永不清理**。

## 高置信事实（跨角色）

<!-- 示例（请根据实际项目填写后删除示例）：
- 2026-04-10：`projects/board.yaml` 的 `summary:` 段由 SessionEnd hook 自动重算 + SessionStart drift check 兜底；其他角色更新 tasks 条目时不修改 summary 段
- [纠正] 2026-04-12：研发任务必须流转 `in_progress → pending_qa → completed`，@dev 不能直接标记 completed
-->

## 项目级共识

<!-- 写入示例：
- 2026-04-15：本项目采用 TypeScript + Vite，@dev / @qa 测试脚本统一用 TS
- 2026-04-20：Prompt 变更必须先在 `prompts-sandbox/` 跑 eval，@prompt-eng 和 @qa 协作签发
-->
