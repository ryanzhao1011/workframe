# Docs

面向 Workframe 使用者的文档。如果你想给框架本身提改动，请看根 README 的 [参与贡献](../README.md#参与贡献)。

## 从这里开始

| 文档 | 内容 |
|---|---|
| [quickstart.md](./quickstart.md) | 10 分钟上手：装 launcher → 对话式创建 / 接入项目 → 重启验证 → 升级与协作 |
| [concepts.md](./concepts.md) | 核心概念：Plugin / 项目级 / 用户级 三层原理，项目级同名 agent 与插件角色并存，modules/ 体系，记忆与看板 |
| [setup-guide.md](./setup-guide.md) | 初始化流程完整细节：目录判定、A/B 路径、确认页、执行步骤、降级与故障 |
| [onboarding.md](./onboarding.md) | `/core:onboard` 可选配置引导（默认全部跳过；workframe 不依赖任何被引导项） |
| [context-injection.md](./context-injection.md) | 必载纪律怎么进上下文：源在哪、两扇门各自的挂载、生效时序、片数怎么查 |
| [one-round.md](./one-round.md) | **装完之后怎么用**：一轮改动从动手到收口的顺序、基准引用要刷哪几处、`module-close-check` 何时跑与九项各查什么 |

## 深入了解

角色扩展、skill 编写、模块体系设计——这些规范文档是 launcher 与 core skills 运行时的知识源，放在插件目录内（plugin 打包分发时整套知识随插件一起走），你在仓库里也可以直接阅读：

- [`plugins/core/reference/project-architecture.md`](../plugins/core/reference/project-architecture.md) — 项目目录结构规范
- [`plugins/core/reference/module-architecture.md`](../plugins/core/reference/module-architecture.md) — modules/ 体系设计
- [`plugins/core/reference/role-customization-guide.md`](../plugins/core/reference/role-customization-guide.md) — 角色扩展规范
- [`plugins/core/reference/skill-customization-guide.md`](../plugins/core/reference/skill-customization-guide.md) — 技能扩展规范
- [`plugins/core/reference/role-profile-catalog.md`](../plugins/core/reference/role-profile-catalog.md) — 角色路由偏好目录
- [`plugins/core/reference/claude-md-merge-guide.md`](../plugins/core/reference/claude-md-merge-guide.md) — 接入存量项目时的 CLAUDE.md / AGENTS.md 合并规范

## 关键提示

**每次安装或升级框架后，请重启会话**（Claude Code 与 Codex 各自重启；Codex 侧升级后失信任的 hook 由种信任 hook 在下一次会话补种、再下一次会话才跑），确保插件的 hooks、必载纪律与 agents 全部加载生效。上一版装的项目（运行态还在 `.claude/` 下）升级后**先迁移、再重启会话**，见 [setup-guide.md](./setup-guide.md)「已装项目迁移」。参见 [context-injection.md](./context-injection.md) 与 [quickstart.md](./quickstart.md) §升级框架。
