---
name: prompt-design
description: Prompt 设计与迭代，含场景分析、分层架构、变量接口、版本管理、风险评估的完整设计流程
when_to_use: |
  用于 Prompt 设计、迭代、分层架构（system/user/few-shot）、版本管理、变量接口设计时调用。
  典型触发："设计 prompt" / "迭代 prompt" / "prompt 改一下" / "分层架构" / "Prompt 模板"。
  不用于：Prompt 效果对比测试（用 prompt-evaluation）/ AI 策略调研 / 模型动态跟踪（暂走 notes/MEMORY）。
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep, WebSearch, WebFetch]
---

# Prompt 设计技能

## 适用场景

- 为新场景设计 Prompt
- 优化现有 Prompt（准确性/稳定性/成本）
- 将业务需求转化为 AI 交互方案

## 五步流程

### 第 1 步：场景分析

在设计 Prompt 前，先澄清场景：

| 维度 | 核心问题 |
|------|---------|
| 目标用户 | 谁会使用这个 Prompt 生成的输出？ |
| 使用场景 | 在什么业务流程中被调用？ |
| 期望输出 | 输出的格式、长度、质量要求？ |
| 约束条件 | 不能输出什么？安全/合规/品牌要求？ |
| 成本/延迟 | 对响应时间和 token 成本的要求？ |

信息不足时使用 `[待确认: {说明}]` 占位。

### 第 2 步：Prompt 架构设计

按分层架构设计：

```
┌─────────────────────────────────┐
│ System Instruction（系统指令）    │
│ - 角色定义                       │
│ - 能力边界                       │
│ - 输出格式约束                    │
│ - 安全红线                       │
├─────────────────────────────────┤
│ Context Injection（上下文注入）   │
│ - 业务数据                       │
│ - 历史对话                       │
│ - 检索结果                       │
├─────────────────────────────────┤
│ User Instruction（用户指令）      │
│ - 具体任务                       │
│ - 用户输入                       │
└─────────────────────────────────┘
```

每层的设计要点：

| 层级 | 设计要点 |
|------|---------|
| System | 角色明确、能力清晰、格式严格、红线不可越 |
| Context | 结构化、去噪、按相关性排序 |
| User | 具体、可操作、避免歧义 |

### 第 3 步：变量接口定义

把 Prompt 中需要动态替换的部分抽象为变量：

```yaml
variables:
  - name: user_input
    type: string
    required: true
    description: "用户输入的原始文本"
    max_length: 2000
  
  - name: style_tone
    type: enum
    required: false
    default: "neutral"
    values: ["formal", "casual", "neutral"]
    description: "输出文本的语气"
```

设计原则：
- 变量名自解释
- 类型明确，有范围约束
- 必填/选填标注
- 默认值安全

### 第 4 步：版本管理

每个 Prompt 方案标注版本信息：

```markdown
## Prompt: {name} v{version}

- **版本**：v1.2
- **上一版本**：v1.1
- **变更理由**：{为什么改}
- **与前版差异**：
  - 新增：{...}
  - 修改：{...}
  - 删除：{...}
- **预期影响**：{对输出质量、成本、延迟的预期影响}
```

### 第 5 步：风险评估

评估 Prompt 的潜在风险：

| 风险类型 | 检查点 |
|---------|--------|
| Prompt 注入 | 用户输入能否突破 System 指令？ |
| 输出失控 | 是否可能输出违规/有害/错误内容？ |
| 幻觉 | 是否有机制防止编造事实？ |
| 边界行为 | 极端输入（超长/空/非预期格式）如何处理？ |
| 成本失控 | 是否有 token 上限？是否可能陷入循环？ |
| 敏感信息 | System Prompt 是否可能被泄露给用户？ |

## 输出模板

```markdown
# Prompt 方案：{name} v{version}

## 1. 场景分析
- 目标用户：{...}
- 使用场景：{...}
- 期望输出：{...}
- 约束条件：{...}

## 2. 架构设计
### System Instruction
```
{system prompt}
```

### Context Schema
```
{context template}
```

### User Instruction Template
```
{user prompt template}
```

## 3. 变量接口
{variables yaml}

## 4. 版本信息
{version info}

## 5. 风险评估
{risk table}

## 6. 示例
### 输入
{example input}
### 期望输出
{example output}
```

## 下游衔接

- **prompt-evaluation**：设计完成后必须评估
- **@qa test-case-design**：基于风险评估设计对抗性测试
- **board.yaml**：Prompt 变更任务需经 pending_qa

## 反模式

- ❌ 直接凭感觉写一段话当 Prompt
- ❌ 不区分 System/Context/User，混在一起
- ❌ 硬编码具体值，不做变量抽象
- ❌ 不标版本号，无法追踪迭代
- ❌ 设计完不做风险评估
