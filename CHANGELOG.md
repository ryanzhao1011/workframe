# Changelog

本文件记录 Workframe 的所有重要变更。

## [1.3.0] — 2026-11-14

### 新增与改进

- **后台自迭代**：框架会在后台用你已登录的 Claude Code 或 Codex，自动整理角色记忆和模块提醒，不用手动触发。它会用到你的额度，每个项目最多每七天一次；不想要，可以在 `<home>/.workframe/user-config.json` 里写 `{"version":1,"self_iteration":{"mode":"off"}}`。详见 [setup-guide](./docs/setup-guide.md) 的「后台自迭代」一节。
- **Codex 相关功能需要 Python 3.11 及以上**，版本不够时会提示升级。

## [1.2.0] — 2026-11-09

### 新增与改进

- **模块记忆**：在模块目录下放一份 `module-memory.md`，写下「在这个模块动手前要做什么、要读什么」。会话第一次碰到该模块时，框架自动把它送进上下文，Claude Code 与 Codex 都支持。
- **角色记忆按需加载**：开场只加载共享记忆和各角色的条目目录；做到哪个角色的活，才加载该角色的完整记忆，上下文压缩后自动补回。
- **主会话直接干活**：缺省由主会话完成整条链路，只在独立验证、权限隔离、可并行的大块任务、大量探索这四种情况派子 agent，减少无效派发和重复读上下文。
- **签发分两类**：研发任务按「主会话能否自己验出错」分为自理和需独立验证两类，签注字段随之精简为验了什么、没验什么、对应哪段提交，看板 hook 按此检查。
- **必载纪律更精简**：每次会话自动加载的规则约缩短一成，签注格式、状态流转等细则移到对应 skill 里按需查阅。
- **Codex 门补齐**：用 `apply_patch` 改文件时，签发检查与模块过期标记同样生效；升级后由 `workframe-door --upgrade` 恢复 hook 信任，`--check` 与 doctor 可查看信任状态。
- **派发防护**（Claude Code）：派发框架角色时带 `name` 会被自动拦下，避免开着 agent teams 时角色以队友身份启动、拿不到角色记忆。

## [1.1.0] — 2026-09-30

### 新增

- **支持 Codex**：同一个项目可以同时用 Claude Code 与 Codex，跑的是同一份框架。安装与升级见用户文档 [setup-guide](./docs/setup-guide.md) 的「Codex 门」一节。
- **从 1.0.0 升级**：运行状态与角色记忆的目录搬到了 `.workframe/`，升级后要先跑一次迁移、再开会话，步骤见 [setup-guide](./docs/setup-guide.md) 的「已装项目迁移」一节。

## [1.0.0] — 2026-08-18

首个公开版本。

### 架构

- **双插件分层，锁步发版**：用户级 `workframe-launcher`（每台机器装一次，管「怎么开局」）+ 项目级 `core`（launcher 自动订阅进项目，管「项目里怎么干活」）。launcher 只经 marketplace 注册信息定位 core，无相对路径耦合
- **对话式初始化**（`/workframe-launcher:setup`）：目录状态判定（空 / 容器 / 项目 / 已接入）→ 业务上下文采集 → 结构闸确认模块树（粗扫标题级证据支撑，确认前零写盘）→ 确定性骨架与模块树落盘（`project_scaffold.py` + `module_init.py`，占位符零残留断言）→ 订阅与 rules 同步 → git init + 首提交 → 逐模块确认并安置资料 → 节奏闸与整理归档段 → 落盘验收；全程 `setup-state.json` 增量断点，中断可续
- **存量项目接入（源文档处理进装机，层层递进）**：就地粗扫带证据确认；已有 `CLAUDE.md` 走合并规范（含备份策略）；已有 `.gitignore` 仅追加 managed 标记块；结构闸只拍模块树与整合稿，建树后**逐模块**深读、逐模块确认安置与原件处置并当场落地；整理归档（原始资料 → 正式需求文档）按节奏闸拍板（当场连续做 / 接力 / 暂不做），推迟批次经 `pending_work` 状态机由重启后首会话**主动接力**，doctor「初始化完整度」常驻可见

### core 插件

- **4 个 baseline 角色**：pm / dev / qa / prompt-eng——职责边界、路由规则、研发任务状态流转与 QA 签发权限（`pending_qa → completed` 仅 @qa 签发）、收尾协议；看板维护 / 节奏把关 / summary 兜底由主 Claude 直接承担，不设专职管理角色
- **36 个 skills**：13 domain + 8 system/maintenance + 8 docs/publishing + 7 modules-system
- **11 段 hook 链路**：SessionStart / Setup / UserPromptSubmit / PostToolUse / Stop / SubagentStart（角色记忆注入）/ SubagentStop / StopFailure / ConfigChange / SessionEnd / UserPromptExpansion（用户直敲 `/skill-name` 的调起采集）
- **4 条通用 rules**：auto-update / correction-detection / response-output / agent-protocols；经镜像同步进项目 `.claude/rules/workframe/core/`，SessionStart 自愈跟平
- **modules/ 体系**（恒启用）：`<basic>/<sub>` 模块树 + `requirements/`（文档→代码）+ `current-state/`（代码→文档）+ 机器维护索引段与反向索引
- **记忆系统**：shared + 角色级 MEMORY / notes 双层，D/U/R/A 准入，librarian 整理，`[纠正]` 条目永久保护。**记忆默认进 git**（含 sidecar `memory-index.json`）——记忆是跨会话积累的资产，误删或改坏无法重建，进 git 才有回溯与多端同步；**含客户名 / 未公开数据的项目请自行在 `.gitignore` 加回 `.claude/agent-memory/`**，框架不替项目做这个判断
- **workframe_doctor**：install 组 11 项落盘验收 + 运行时体检；首个会话由 hook 自动复跑运行时验收并转述结论

### 质量与工程

- `tools/validate.py` 单一质量闸：160 项结构 / 引用 / 口径 / 防回归检查，纯标准库零依赖
- CI：三平台（ubuntu / windows / macos）validate 矩阵
- Windows 11 真实会话 E2E 走查：全新创建、存量接入、升级、幂等重跑、断点续做、误触发、多候选市场、同事 clone（机器断言）
