---
name: test-case-design
description: 测试用例设计与验证，基于 GWT 验收标准生成 Happy/Sad/Boundary 三类用例矩阵，含失败处理和签发流程。modules/ 体系下用例落盘到 requirements/<req_slug>/<sub_req_slug>/test-cases/。
when_to_use: |
  用于基于验收标准生成测试用例矩阵、覆盖 Happy/Sad/Boundary 路径、走签发流程时调用。
  典型触发："设计测试用例" / "测一下 X" / "回归覆盖范围" / "QA 验证 pending_qa 任务"。
  不用于：bug 调试（用 systematic-debugging）/ 代码审查（用 code-review）/ Prompt 评估（用 prompt-evaluation）。
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# 测试用例设计与验证技能

## 前置依赖

调用本 skill 前需读 skill: `document-norms` §1（文档归属矩阵）/ §2（frontmatter 字段标准，特别是 type=test-case + §2.6 req_slug+sub_req_slug 引用契约）。modules/ 体系下用例落盘到 `projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/test-cases/`，YAML 顶层字段 `type: test-case` + `module: <basic>/<sub>` + `req_slug: <req_slug>` + `sub_req_slug: <sub_req_slug>` 必填（`version` 字段已废除）。

## 适用场景

- @qa 收到 `pending_qa` 状态的任务时
- 代码变更后需要做功能验证时
- 回归验证时

## 五步流程

### 第 1 步：解析验收标准

读取需求来源，提取验收标准：

| 来源 | 提取内容 |
|------|---------|
| `projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md` | GWT 场景式 AC 和规则式 AC |
| Issue | Bug 的复现场景和期望修复结果 |
| @dev 交付说明 | 改动点和 QA 关注点 |
| 历史相关功能 / current-state/ | 需要回归的已有能力 |

### 第 2 步：用例矩阵生成

基于 AC 和代码变更，生成三类测试用例。

**落盘路径**：`projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/test-cases/<TC-ID>.yaml`

> **YAML 顶层字段**（不是 markdown frontmatter——test-case 文件本身就是 .yaml）：需含 `type: test-case`（document-norms §2.3 文档类型枚举）+ `case_type: happy_path|sad_path|boundary`（用例细分类）+ `module: <basic>/<sub>` + `req_slug: <req_slug>` + `sub_req_slug: <sub_req_slug>` + `case_id` + `ac_ref` + `preconditions` + `steps` + `expected` 等业务字段（三类用例的字段 schema 见本 skill 下文，不在 document-norms）。`version` 字段已废除。
>
> **schema 说明**：`type` 字段沿用 document-norms 全局文档类型分类（取值 `test-case`），与 PRD/spec/decision 等并列；`case_type` 是测试用例内部的细分类，独立字段，不与 `type` 冲突。

#### 2.1 Happy Path（正向流）
验证"一切正常时的预期行为"。

```yaml
# document-norms §2.1 通用必填字段
type: test-case          # §2.3 文档类型（固定值）
status: draft            # draft | in_progress | approved | implemented | deprecated | superseded
owner_role: qa
updated: <ISO-8601 带时区>  # §2.7
related: []
tags: []
# modules/ 体系下还需补：module: "<basic>/<sub>" / req_slug: "<req_slug>" / sub_req_slug: "<sub_req_slug>"
# test-case 细分类（独立字段，不与 §2.3 type 冲突）
case_type: happy_path    # happy_path | sad_path | boundary
case_id: TC-HP-001
ac_ref: AC-01
preconditions: "{前置条件}"
steps:
  - "{操作1}"
  - "{操作2}"
expected: "{期望结果}"
```

#### 2.2 Sad Path（异常流）
验证"异常情况的处理"。

| 异常类别 | 示例场景 |
|---------|---------|
| 输入异常 | 空值 / 非法值 / 格式错误 / 超长 |
| 权限异常 | 未登录 / 无权限 / 会话过期 |
| 资源异常 | 不存在 / 已删除 / 已过期 |
| 并发异常 | 重复提交 / 竞态条件 |
| 外部依赖异常 | API 超时 / 服务不可用 |

#### 2.3 Boundary（边界值）
验证"边界条件"。

| 边界类型 | 测试值 |
|---------|--------|
| 数值边界 | 最小值 / 最大值 / 零 / 负数 |
| 字符串边界 | 空串 / 单字符 / 最大长度 / 超长 |
| 集合边界 | 空集 / 单元素 / 最大容量 |
| 时间边界 | 过去 / 当前 / 未来 / 时区变更 |

### 第 3 步：执行验证

| 验证方式 | 适用场景 |
|---------|---------|
| 自动化测试 | 有测试框架的项目，运行测试命令 |
| 代码审查 | 阅读代码逻辑，推演输入/输出 |
| 手动构造 | 通过脚本或调试工具构造测试数据 |
| 文档审查 | 纯文档类任务，检查内容完整性和准确性 |

产出：带证据的测试结论（命令行输出、代码审查结论、推演过程）。

### 第 4 步：失败处理

测试不通过时，按以下流程创建 Issue：

#### 4.1 选择 Issue 类型
- **SEC**：权限绕过、越权、敏感信息泄露、Prompt 泄露、注入漏洞类问题
- **BUG**：功能异常、报错、数据丢失、性能问题、体验问题

不确定时默认用 BUG；如后续升级为安全问题，可转为 SEC 模板重建。

#### 4.2 按 TEMPLATES.md 填写字段

Issue 字段定义的**唯一权威来源**：`projects/issues/TEMPLATES.md`

- **status 枚举**（5 种：open / in_progress / fixed / wontfix / closed）、**severity 枚举**（SEC/BUG 不同）、**状态流转图** 均在 TEMPLATES.md 中定义
- 严禁自造字段或修改 status/severity 取值

#### 4.3 写入 Issue 文件

- **文件路径**：`projects/issues/SEC-{序号}.yaml` 或 `projects/issues/BUG-{序号}.yaml`（扁平结构，**不**按模块分子目录——避免 ID 歧义和横切问题难安置）
- **序号分配**：扫描 `projects/issues/` 目录下同类型现有 Issue，取最大序号 + 1（全局唯一，跨归属维度）
- **字段填写**：按 `projects/issues/TEMPLATES.md` 的字段约定，包括：
  - 内容字段（preconditions / steps / expected / actual / fix_strategy / verified_by 等）
  - 归属字段（`area` / `module` / `component`）：
    - **modules/ 体系下**（`projects/modules/` 存在）：`module` 必填二段式 `<basic>/<sub>`（如 `profile/edit`），不合规请先调 `module-init` 建对应子模块；挂需求就填 `req_slug` + `sub_req_slug` **两个一起**（`main` 也显式写；不挂需求的两个都留空）/ 可选 `affected_modules`（横切多模块二段式数组）
    - **非 modules/ 体系**：至少填 `area`（如 backend / frontend / infra / 平台名 / 业务线 / 客户阶段），`module` 可单值或留空
  - 关联字段（`spec_ref` / `related_task` / `source`）：尽量填，便于回溯与统计；modules/ 体系下 `spec_ref` 推荐写 `modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md`
- **status 与 severity**：枚举值固定，**严禁自造**；扩展请走 self-iteration L2 提案

#### 4.4 更新任务看板 + 写 task_blocked 事件
- `board.yaml` 对应任务状态从 `pending_qa` 改为 `blocked`
- 任务 `notes` 字段引用新创建的 Issue ID
- 响应中明确告知用户 Issue ID 和 severity，以便后续跟踪
- **必须** append `.claude/workframe-state/events.jsonl`（这是 self-iteration `problem` 加权分权重 2.0 的唯一可靠来源）：
  ```json
  {"ts":"<ISO-8601>","type":"task_blocked","task_id":"<TASK-ID>","role":"qa"}
  ```
  缺这条事件会导致 task_blocked 在 skill-metrics / self-iteration 触发器中永远是 0。

### 第 5 步：签发结单

全部通过时：

1. 更新 board.yaml 任务状态：`pending_qa → completed`
2. 生成测试报告（供后续审查）：

```markdown
## 测试报告：{Task-ID}

### 覆盖范围
- Happy Path: {N} 个用例
- Sad Path: {N} 个用例
- Boundary: {N} 个用例

### 执行结果
- 通过：{N}
- 失败：{N}（见 Issue: {IDs}）
- 跳过：{N}

### 执行证据
{命令行输出 / 审查结论 / 推演记录}

### 结论
✅ 通过 / ⚠️ 有条件通过 / ❌ 不通过

### 回归建议
- {建议后续回归的场景}
```

## 下游衔接

- **失败 → @dev systematic-debugging**：修复后重新 pending_qa
- **通过 → 下一个任务**：`completed` 后从 pending_qa 列表移除
- **code-review**：与代码审查技能协同，全面验证质量

## 反模式

- ❌ 只测 Happy Path，跳过异常和边界
- ❌ 测试结论只说"通过"，不给执行证据
- ❌ 发现问题不按 TEMPLATES.md 格式创建 Issue
- ❌ 测试未通过就签发 completed
- ❌ 用 @dev 提供的测试结论直接结单，不做独立验证
