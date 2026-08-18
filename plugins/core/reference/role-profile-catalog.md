# Role Profile 目录

`role_profile` 决定 baseline 4 角色（pm / dev / qa / prompt-eng）在该项目里的**默认路由优先级**。本目录为 `project_scaffold.py` 的 `extract_role_profile_routing()` 抽取路由偏好文本、注入 `CLAUDE.md` 的 `{{ROLE_PROFILE_ROUTING}}` 占位符所用。

## 设计约束

- **软提示，非硬约束**：profile 只影响 `CLAUDE.md` 的"路由偏好"段，**不**禁用任何 core agent；用户始终可 `@角色名` 直接调用
- **不影响运行期**：`role_profile` 不写入 `activity-state.json`，是项目配置不是运行期状态
- **不生成附加产物**：profile 不自动生成项目级 skill / rule / 业务目录 / 起手任务
- **可选字段**：`.workframe-config.json` 中 `role_profile` 由 launcher 在创建对话中推断写入；接入已有项目时可缺省
- **二维正交**：`dormant_profile` 决定维护频率、`role_profile` 决定 baseline 路由偏好——两者独立

## 自动推荐逻辑（建议性）

`workframe-launcher` 的 setup skill 在采集完业务上下文后按以下顺序推断，**不设问**，
结果在方案确认页展示并允许用户当场改：

1. 业务上下文**明显**以 AI / LLM / Prompt / Agent 能力为核心 → `ai-product`
2. 命中「个人 PM / 一人 / 没研发 / 自己做」等独立工作信号 → `solo-pm`
3. 否则 → `software-team`（默认）

接入存量项目时另有一个可观测信号：`git shortlog -sn` 的贡献者数——仅用于第 2 条的
独立/带团队判断，**不影响目录结构**。

**关键文案约束**：仅当业务上下文**明显**以 AI 能力为核心时才推荐 `ai-product`——
「智能运营」「自动化」「智能 X」等含混词**不**归 AI 类，避免关键词列表无限扩张。

方案确认页必须含可调整提示：

> 推荐 role_profile: `<X>`。如不符合实际工作模式，可在确认前说「改成 `<Y>`」，
> 确认页会重新渲染「路由偏好」段。

---

## 3 个 Profile 定义

### `software-team`

**适用**：默认档，有研发协作的产品项目

**角色优先级**：
| 主力 | 按需 | 几乎不用 |
|---|---|---|
| pm / dev / qa | prompt-eng | — |

**CLAUDE.md 路由偏好渲染文本**：

```markdown
## 路由偏好（profile: software-team）

- 需求分析、用户故事、验收标准、竞品调研 → @pm（主力）
- 技术方案、编码、Bug 修复、部署、Schema 迁移 → @dev（主力）
- 功能测试、回归、代码审查、签发 → @qa（主力）
- 看板、迭代节奏、周报 → 主 Claude（读 board.yaml 直接处理）
- Prompt / AI 策略 → @prompt-eng（按需，仅明确涉及 AI/Prompt 时）

> 这是默认路由偏好，不限制用户直接 `@角色名` 调用任何 core agent。
```

---

### `solo-pm`

**适用**：个人 PM 模式——单人或没有研发团队的产品项目

**角色优先级**：
| 主力 | 按需 | 几乎不用 |
|---|---|---|
| pm | dev / qa | prompt-eng |

**CLAUDE.md 路由偏好渲染文本**：

```markdown
## 路由偏好（profile: solo-pm）

- 需求调研、PRD 撰写、用户访谈、竞品分析 → @pm（主力）
- 看板维护、周报、节奏管理 → 主 Claude（读 board.yaml 直接处理）
- 技术可行性评估、原型脚本 → @dev（按需）
- 上线前检查 / 验收 → @qa（按需）
- Prompt / AI 策略 → @prompt-eng（仅明确涉及 AI/Prompt 时）

> 这是默认路由偏好，不限制用户直接 `@角色名` 调用任何 core agent。
```

---


### `ai-product`

**适用**：**明显**以 AI / LLM / Prompt / Agent 能力为核心的项目

**角色优先级**：
| 主力 | 按需 | 几乎不用 |
|---|---|---|
| prompt-eng / pm / dev | qa | — |

**CLAUDE.md 路由偏好渲染文本**：

```markdown
## 路由偏好（profile: ai-product）

- Prompt 设计、AI 策略、模型评估、Prompt 实验 → @prompt-eng（主力）
- 业务需求、用户故事、AI 应用场景定义 → @pm（主力）
- 系统集成、API 实现、前后端代码、部署 → @dev（主力）
- 系统层测试、回归（AI 输出层评估由 prompt-eng 主导）→ @qa（按需）
- 看板、节奏管理 → 主 Claude（读 board.yaml 直接处理）

> 这是默认路由偏好，不限制用户直接 `@角色名` 调用任何 core agent。
> AI 输出测试与系统测试有职责重叠，建议在 board.yaml task 上明确标注由谁执行。
```

---



## 渲染契约

`project_scaffold.py` 的 `extract_role_profile_routing()` 在渲染 `CLAUDE.md` 时：

1. 按选定的 profile 定位本文件对应的 `### <profile>` 章节
2. 抽取该章节的「CLAUDE.md 路由偏好渲染文本」代码块（去掉外层 `markdown` 围栏）
3. 注入到 `claude-md-template.md` 的 `{{ROLE_PROFILE_ROUTING}}` 占位符位置

抽取由脚本确定性完成、不经模型转述；profile 名在本文件里找不到时直接报错，不静默降级。

注入后渲染示例（solo-pm 项目）：

```markdown
## 路由偏好（profile: solo-pm）

- 需求调研、PRD 撰写、用户访谈、竞品分析 → @pm（主力）
...
```

## 与项目级角色 override 的关系

profile 只是路由**软偏好**，**不限制**项目级 override 行为：

- 用户可在项目本地 `.claude/agents/<role>.md` 全量 override 任何 core agent，profile 不会"反向干预"
- 用户可新增项目级角色（如 `content-operator`），路由偏好段会建议"优先项目级"，但不影响主 Claude 在用户明确 `@xxx` 时直接路由
- profile 的"几乎不用"标记是**默认路由暗示**，不阻止任何用户主动调用

详见 `role-customization-guide.md` §项目级扩展。
