---
supersedes:
  - "auto-update.md#受保护资产约束"
---

# 受保护资产清单

以下文件/目录为系统定义资产，**信号入账不直接写入**，只能回显摘要 + 在 `projects/board.yaml` 创建任务/提案，交由对应 owner 角色在用户确认后执行：

- `CLAUDE.md`
- `AGENTS.md`（两扇门共用的项目自有判据落点）
- `.codex/**`（另一扇门的**项目内**配置面。门没落地时它匹配不到任何东西、纯 no-op；门落地后内部怎么排都被整目录通配覆盖住——**方向 fail-safe，所以现在就列**。用户级 `config.toml` 的 trust 段不在此列：那在项目外、结构未知，钉之前得先知道形态）
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
> **两者的兜底不一样，落笔前知道自己站在哪一边**：`AGENTS.md` 在否决项 6 的路径表内，改它**恒封顶到用户拍板**，即使这条例外被误用，用户仍在环内；**项目 skills 不在那张表内**——直接写它**没有任何机器面会说话**，**「用户当场确认」是这条路径上唯一的控制**。这条例外是用户在知道这一点的前提下（2026-09-10）拍板放开的。

**可直接写入的资产**：

- `notes.md`、角色级 `MEMORY.md`：按信号入账的 P0/P1/P2 直接写入
- `projects/issues/`：P0 安全/故障可在用户确认后直接创建（由 P0 的「回显 → 等确认 → 写入」顺序保证）
- `projects/board.yaml`：**仅 P0 单条紧急任务**（安全 / 线上故障类）允许在用户确认后直接追加 `status: pending` + `tags: [auto-update]`；其他场景（批量任务、需求拆分产生的任务）一律走 `task-management` skill 或由主会话落盘
- **需求文档（PRD）**：**默认只输出草稿到响应**，由 @pm 或用户确认后落盘；非 PM 角色触发「需求变更」信号时只输出变更摘要 + 落盘草稿到响应，不直接写需求文件。落盘走 `prd-writer` skill；首次需求先调 `module-init` 建子模块/需求资产包

**受保护资产例外**：`.claude/settings*.json`、用户级 settings 与用户级插件市场注册表 —— **豁免写入入口是 `/core:onboard` 与装机链路两个**，其余任何路径都不得写 settings。两者的共同前提：备份 + JSON merge 保留原字段 + 写不成只输出手动补丁、不留半写文件。差别在授权形态与写入面：`/core:onboard` 由用户显式调用、每一项都须当面确认（完整前提清单与决策依据在 `onboard` skill 内）；装机链路写**两类**：①订阅声明那几个键（`extraKnownMarketplaces` / `enabledPlugins` / 市场注册条目）；②**这台机器上装了 `codex` 时**的 Codex 门那一步——用户级 `config.toml`（本插件 hook 的信任 ＋ 本项目的 trust ＋ 本插件 `enabled`）与项目 `.codex/`。第二类**代用户信任本插件的全部 hook，而受信 hook 脱离沙盒执行**，且它不再单问一层确认 ⇒ **经 launcher setup 走时，它的事前授权也落在确认页上**，与第一类同一个闸（直接敲命令那条路径没有确认页，见下）。装机链路有两个面，**授权形态不同，别混成一句**：① **经 launcher setup 走**——用户确认的是装机这件事本身，写入前由确认页对「会被修改的已存在文件」逐个点名；② **直接敲命令**（`workframe-door --backfill`，以及脱离 launcher 直接跑 `project_scaffold.py --write-subscription`——doctor 的修复指引就会让你走这条）——**没有确认页**，授权就是用户亲手敲了命令。**确认页只在 ① 那条路径上存在**：`--write-subscription` 是个独立 flag，按 ① 的写法去要求它「先有确认页」，等于把一条正确的修复路堵死。**两面的共同前提相同，写入面不相同**：`project_scaffold.py --write-subscription` 只写上面第一类；`workframe-door --backfill` 与经 launcher 的整条链路两类都写。
