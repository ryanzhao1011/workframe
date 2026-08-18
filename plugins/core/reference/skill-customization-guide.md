# 技能扩展规范

本文档定义如何在项目内扩展或定制 skill。

## 36 通用 skill（来自 core plugin）

订阅 core plugin 后自动可用，无需项目本地重复定义。分四类：

### Domain skills（13 个，按需 preload 给 agent）

| 类别 | Skill 列表 |
|---|---|
| Self-management | `task-management` |
| 产品分析 | `requirement-analysis`, `feature-breakdown`, `acceptance-criteria`, `competitive-analysis`, `product-metrics-design`, `user-feedback-analysis` |
| 研发支持 | `technical-design`, `systematic-debugging`, `test-case-design`, `code-review` |
| Prompt 工程 | `prompt-design`, `prompt-evaluation` |

### Maintenance/system skills（8 个，内部 / 命令触发，不 preload）

| Skill | 触发方式 | 用途 |
|---|---|---|
| `librarian` | **SessionStart 询问式开场卡**（主通道）/ `workframe-maintenance` 批处理工单 / `/core:maintenance-review` | 记忆整理；skill-metrics.yaml 由 `recompute_skill_metrics.py` 重算 |
| `self-iteration` | 内部调用（hook 触发 pending_maintenance → /core:maintenance-review 执行） | 模式识别 + 多候选提案 |
| `session-digest` | **下次 SessionStart** 见骨架简陋时二次填充 | 会话摘要（SessionEnd hook 只写骨架且**无条件覆盖**，会话末尾预写必被盖掉——详见该 skill §执行模型说明） |
| `audit` | 用户 `/core:audit` | 维护活动审计 + pending_maintenance 展示（只读） |
| `rollback` | 用户 `/core:rollback` | 回滚 L1/L2 自动变更 |
| `memory-log` | 用户 `/core:memory-log` | 记忆层活动流水 |
| `maintenance-review` | 用户 `/core:maintenance-review` | Dormant 唤醒 / 手动维护入口 |
| `onboard` | 用户 `/core:onboard`（未 onboarded 的项目 SessionStart 提示一行） | 一次性可选配置引导（默认 skip；详见 `onboard/SKILL.md`） |

写入型维护操作由 `maintenance-review` 承担，例如 `/core:maintenance-review --dismiss <PM-ID>` 关闭 pending_maintenance 条目；`audit` 保持只读。`onboard` 是受保护资产 `.claude/settings*.json` 的唯一豁免写入入口（详见 [`auto-update.md`](../rules/core/auto-update.md) §受保护资产例外）。

### 文档/发布工具（8 个）

| Skill | 用途 |
|---|---|
| `prd-writer` | PRD 创作（六段骨架：理解 → 骨架确认 → 填充 → 落盘 → 附属产物 → 发布；工序形态与章节结构读项目 `.claude/skills/prd-style/`）；落 `modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md`；平台无关 |
| `html-demo` | 仿真型 HTML 交互 demo（demo 先行工作流）：按用户截图复刻真实界面、全状态可达 + 模拟开关、tmp/ 就地迭代 → prototypes/ 归档，衔接 prd-writer「demo 先行变体」 |
| `screenshot` | 通用 HTML / URL 截图工具（被 prd-writer / html-demo / 报告类 skill 调用） |
| `obsidian-doc-structure` | Obsidian CLI 增强：读 outline / properties / tags |
| `obsidian-link-audit` | Obsidian CLI 增强：链接 / 反链 / 坏链 / 孤岛审计 |
| `obsidian-safe-write` | Obsidian CLI 增强：小范围安全写入（property / template / move） |
| `obsidian-history-check` | Obsidian CLI 增强：历史 / diff 只读检查 |
| `document-norms` | ★ 章节化 §1-§11 文档归属/frontmatter（含 §2.7 updated 时间戳）/三段制 overview/索引同步 SOP/反模式/写作质量分级 单一来源；业务 skill 引用 `§X §Y` 而非整 skill |

外部发布器（如 `feishu-publish` / `notion-publish` / `confluence-publish`）保持**项目级**，命名规范统一为 `<platform>-publish`。

### Modules-system skills（7 个）

| Skill | 用途 |
|---|---|
| `module-init` | 创建 modules/ 体系下基础模块 / 子模块 / 需求资产包；自动同步上级 overview + 反向索引 |
| `code-to-doc` | LLM 解析 `code_paths` → 生成 `current-state/` 4 文件（架构 / API / 数据模型 / 代码索引），含 `path:line` 行级 anchor |
| `module-index-refresh` | 递归刷新各层 overview 机器维护索引段（HTML 注释边界）；段外人写部分零接触 |
| `migrate-to-modules` | 一次性迁移老项目（4 步：建模 → dry-run → 用户 review → 自动搬）；强约束 dry-run 用户确认后才执行 |
| `doc-graph-health` | 知识网健康巡检（断链 / 孤儿 / hub / stale current-state / 姊妹时差 / updated 异常 / 概念热点七项检测持久报告）；热点词表读项目 taxonomy「能力域」，噪声与排除目录经 .workframe-config.json `graph_health` 块配置 |
| `requirement-archiving` | 历史需求资产归档入库九段门禁 SOP（Phase 0-8：收料冻结→通读建模→方案拍板→骨架与shared→PRD回填→反向对账→原型校准→关联收口→审查清源）；含模态盘点/无损提取与归档自查两脚本（收料解析需 6 个 Python 库，缺库友好降级） |
| `material-intake` | 存量资料盘点 / 分流 / 结构推荐（形态 × 数量 × 覆盖率台账 + 每批去向判定 + basic/sub 模块树推荐）；只出计划不做搬运，重型执行交给对应 skill |

modules/ 体系随项目骨架恒启用（`projects/modules/` 总是存在），7 个 skill 开箱即用。详见 `module-architecture.md`。

**合计：13 domain + 8 system/maintenance + 8 docs/publishing + 7 modules-system = 36 skills**。

## 何时写 skill vs rule vs agent

| 选哪个 | 触发条件 |
|---|---|
| **写 agent** | 你要定义一个新的"角色"，有独立职责域和工作流（见 role-customization-guide.md） |
| **写 skill** | 你要定义一个"可复用的方法论/工作流"，被若干 agent 按需调用；skill 可控可预期、有输入输出格式 |
| **写 rule** | 你要定义一条"全局/项目级的强约束"，由 Claude Code 自动加载到上下文（如 auto-update 触发词、response-output 约定） |

**经验法则**：
- 「做某件事的 SOP」→ skill
- 「永远要遵守的原则」→ rule
- 「谁来做这件事」→ agent

## Skill 文件结构

每个 skill 是一个目录，内含 `SKILL.md` 和可选的支持文件：

```
.claude/skills/<skill-name>/
├── SKILL.md                       ← 必填，skill 的入口和工作流
├── reference/                     ← 可选，skill 运行时需要读取的知识源
│   └── <supporting>.md
├── templates/                     ← 可选，skill 生成时用的模板
│   └── <template>.md
└── examples/                      ← 可选，示例（建议放脱敏/假数据）
    └── <example>.md
```

目录命名可自由（`reference/` / `templates/` / `examples/` 仅为常见约定）。SKILL.md 正文用**相对路径**（`./reference/xxx.md`）引用支持文件。Plugin 打包时整个 skill 目录一起分发。

## SKILL.md Frontmatter

```yaml
---
name: <skill-name>                     # 必填，小写+短横线
description: <一句话描述 skill 做什么、产物是什么>  # 必填
when_to_use: |                          # 推荐填，路由准确性的关键
  用于 <典型触发场景> 时调用。
  典型触发："<关键词 1>" / "<关键词 2>" / "<关键词 3>"。
  不用于：<反例 1>（用 <其他 skill>）/ <反例 2>。
user-invocable: true                    # 可选，默认 true；设为 false 则用户不能 /<skill-name> 直接调用
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]  # 可选，skill 执行时可用的工具白名单
argument-hint: "[<参数提示>]"          # 可选，用户调用时显示的参数提示
---
```

> `description` + `when_to_use` 合计字符上限 1536（官方 Skills 文档约定）。`description` 偏"是什么"，`when_to_use` 偏"什么时候调"，二者配合让主 Claude 路由更准；只写 `description` 时容易触发 routing 摇摆（如多个 skill 描述都覆盖同一关键词）。

## SKILL.md 正文

### 推荐结构

```markdown
# <skill 中文名> 技能

## 定位
<一段话讲清：这个 skill 解决什么问题、适用场景、不适用场景>

## 输入
<调用 skill 时期望的上下文或用户输入，如需求描述、代码片段、业务场景>

## 工作流
按步骤说明 skill 如何执行。每一步：
- 做什么
- 读什么文件
- 输出什么

### 第 1 步：<步骤名>
...

### 第 2 步：...
...

## 输出
<skill 完成后向用户/调用方呈现的内容格式>

## 质量自检
<skill 完成前自检清单，确保输出质量>

## 与其他 skill 的衔接
<如果这个 skill 通常在哪些 skill 之前/之后执行，说明协作关系>
```

### 工作流书写要点

- **具体化**：不要只说"分析需求"，要说"读 projects/modules/overview.md → 提取 3 个核心目标 → 用 RICE 打分"。
- **可重入**：同样输入调用同一 skill，产出应基本一致（降低 AI 发挥的不确定性）。
- **明确边界**：skill 的输入和输出要有清晰格式约定；不要在 skill 里做"未在描述中声明的副作用"。
- **引用文件用相对路径**：`./reference/xxx.md`，不写绝对路径。

## 常见扩展 skill 示例

### 按职能扩展（示例）

| Skill | 做什么 |
|---|---|
| `api-design` | REST/GraphQL API 设计规范 |
| `database-schema-design` | 数据库 schema 建模 |
| `deployment-pipeline` | CI/CD 流水线建立 |
| `competitor-analysis` | 竞品档案建立和对比分析 |
| `customer-segmentation` | 客户分群模型 |
| `compliance-review` | 合规检查清单（按业务所在行业定制） |
| `brand-voice` | 维护品牌调性文档，生成符合调性的文案 |
| `weekly-status-report` | 周报模板 |
