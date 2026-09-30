# {{PROJECT_NAME}}

> 基于 Workframe {{FRAMEWORK_VERSION}} 搭建的多角色协作项目
> 项目类型：{{PROJECT_TYPE}}
> 创建日期：{{CREATED_DATE}}

<!-- 项目自有的内容（这个项目是什么 / 项目自有判据 / 项目级角色 / 路由偏好 / 业务目录）与委派授权
     都在 AGENTS.md，两扇门共用一份。这一行导入是 CC 侧读到它的唯一入口，删了它 AGENTS.md 在 CC
     会话里一行都读不到；另一扇门原生读 AGENTS.md，不经这一行。**别把 AGENTS.md 的内容抄回这里**。 -->
@AGENTS.md

## 订阅声明

本项目订阅 `core@workframe` plugin，订阅配置见 `.claude/settings.json`。核心通用能力（4 通用角色 + 全套 skills + 启动时注入的必载纪律 + hook 链路）通过 plugin 自动获得，**不**在项目本地 `.claude/` 下重复定义；系统级 skills 不绑任何角色。

skill 名称与用途由会话启动时的 available-skills 列表给出，本文件不复述——**枚举会随 skill 增减漂移，而列表恒为当前态**。

## 快速入门

- `看板` / `项目进度` — 直接问主 Claude，读 `projects/board.yaml` 汇报
- `@pm 需求分析 [需求]` / `@pm 写需求文档` / `@pm 拆解功能`
- `@dev 做技术方案 [需求]` / `@dev 修 bug：[现象]`
- `@qa 测试 [任务ID]` / `@qa 审查这个 PR / 这段代码`
- `@prompt-eng 设计 prompt [场景]`
- `/workframe-launcher:setup`（另建新项目 / 接入已有项目）/ `/core:audit`（看维护活动）/ `/core:maintenance-review`（进维护流程）
- 直接说任务 — 主 Claude 自行决定直做或委派（指定 `@角色名` 则强制委派）

## 框架相关

- 框架文档：框架仓 `docs/`（quickstart / setup-guide / concepts / onboarding / context-injection）；**仓库位置读 `.workframe-config.json` 的 `framework_path`**（marketplace 安装的见框架 GitHub 仓库）
- 项目 PRD 框架：项目 skills 目录下的 `prd-style/`——**两个候选路径都要认**，该目录按存在性解析（与 `_harness.project_skills_dir()` 同一规则）：新建项目是 `.agents/skills/prd-style/`（真实源；`.claude/skills` 只是指向它的链接，**链接建不成时只有前者可达**），已装项目是 `.claude/skills/prd-style/` 真目录。装机放入出厂默认版，属于项目、可自由修改；`prd-writer` 写 PRD 时读取
- 记忆文件：`.workframe/agent-memory/<role>/{MEMORY.md, notes.md}`
- 运行时状态：`.workframe/state/`

**升级框架后请重启 Claude Code 会话**，确保最新的必载纪律与 hooks 生效。
