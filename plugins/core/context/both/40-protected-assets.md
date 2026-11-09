---
supersedes:
  - "auto-update.md#受保护资产约束"
---

# 受保护资产清单

以下文件/目录为系统定义资产，**信号入账不直接写入**，只能回显摘要 + 在 `projects/board.yaml` 创建任务/提案，交由对应 owner 角色在用户确认后执行：

- `CLAUDE.md`
- `AGENTS.md`（两扇门共用的项目自有判据落点）
- `.codex/**`（另一扇门的**项目内**配置面，门没落地时纯 no-op。用户级 `config.toml` 不在此列：它在项目外）
- `.claude/agents/**`
- `.claude/skills/**`、`.agents/skills/**`（项目 skills）
- `.claude/settings*.json`
- `.workframe-config.json`（项目身份与运行档位配置；由装机链路 merge 维护，字段变更须用户确认）
- `<state>/**`（events.jsonl / activity-state.json / skill-metrics.yaml / rollback-index.json / memory-index.json 等运行态；由 hooks / system skills / 专门流程维护，信号入账不参与）
- `shared/MEMORY.md`（跨角色权威事实；默认不直接写——影响 ≥2 角色的共识需用户显式确认或走 `librarian` 提升路径）
  - **例外**：用户纠正流程处理的纠正**本身就是显式授权**（用户当场说的就是权威口径），按该流程的分流直接写入高置信区，不受本条限制；它的回显步骤即是确认动作。
- `projects/proposals/**`（自迭代闭环资产；信号入账不创建/修改提案，相关变更走 `/core:self-iteration`）
- 仓库根 `README.md` / `LICENSE`

> **`AGENTS.md` 与项目 skills 的例外**：用户纠正被判定为**通用规则**时，用户当场的明确确认**就是**这里要的「显式确认」，可按信号入账的判据段直接落盘，不必再走建任务交 owner 那条；**先回显摘要、等用户明确确认**这一步一个都不能少。
>
> **两者的兜底不一样**：`AGENTS.md` 在否决项 6 的路径表内，改它**恒封顶到用户拍板**；**项目 skills 不在那张表内**——直接写它**没有任何机器面会说话**，**「用户当场确认」是这条路径上唯一的控制**。

**可直接写入的资产**：

- `notes.md`、角色级 `MEMORY.md`：按信号入账的 P0/P1/P2 直接写入
- `projects/issues/`：P0 安全/故障可在用户确认后直接创建（由 P0 的「回显 → 等确认 → 写入」顺序保证）
- `projects/board.yaml`：**仅 P0 单条紧急任务**（安全 / 线上故障类）允许在用户确认后直接追加 `status: pending` + `tags: [auto-update]`；其他场景（批量任务、需求拆分产生的任务）一律走 `task-management` skill 或由主会话落盘
- **需求文档（PRD）**：**默认只输出草稿到响应**，由 @pm 或用户确认后落盘；非 PM 角色触发「需求变更」信号时只输出变更摘要 + 落盘草稿到响应，不直接写需求文件。落盘走 `prd-writer` skill；首次需求先调 `module-init` 建子模块/需求资产包

**受保护资产例外**：`.claude/settings*.json`、用户级 settings 与用户级插件市场注册表**只有两个豁免写入入口：`/core:onboard` 与装机链路**，其余任何路径都不得写（装机链路在装了 `codex` 的机器上还写用户级 `config.toml` 与项目 `.codex/`；`workframe-door --upgrade` 重种 hook 信任同样写那份 `config.toml`，它是用户亲手跑的命令，不是模型的写入路径）。两个入口各自的前提、授权形态与写入面见 skill `onboard` §受保护资产例外。
