# Changelog

本文件记录 Workframe 的所有重要变更。

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
