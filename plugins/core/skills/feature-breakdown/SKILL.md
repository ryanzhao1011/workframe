---
name: feature-breakdown
description: 将需求拆解为可执行开发任务，支持 Epic→Story→Task 三层结构与5种拆分技术，INVEST 验证，≤4小时粒度
when_to_use: |
  用于已有 PRD / Spec 后，把需求拆为可开发的 Epic/Story/Task 时调用。
  典型触发："拆任务" / "Story 怎么拆" / "做 sprint 计划" / "INVEST 检查"。
  不用于：需求识别本身（应先用 requirement-analysis）/ 验收标准（用 acceptance-criteria 在拆解后）/ 测试用例（用 test-case-design 在 qa 阶段）。
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep]
---

# 功能拆解技能

## 拆解原则

1. **粒度控制**：推荐 0.5–1 天内可完成；研发执行任务优先 ≤4 小时；非研发任务（调研、内容、交付物）按"可独立验收的交付物"拆
2. **独立可测**：每条 Story 满足 INVEST 全部 6 项
3. **依赖明确**：`depends_on` 字段 + 关键路径标注
4. **价值优先**：先拆高价值 Story，再拆技术支撑任务

> **非软件项目映射**：Epic = 目标/交付物；Story = 阶段成果；Task = 行动项。INVEST 和价值矩阵同样适用，Step 3 技术层可跳过。

## 拆解流程

### 第 1 步：确认 Epic 边界

从需求文档中提取 Epic 范围：

```markdown
Epic: EPIC-{序号}
标题：{一句话描述}
范围：{包含哪些功能模块}
排除：{明确不包含的内容}
来源：modules/<basic>/<sub>/requirements/<req_slug>/（需求资产包）
```

小型需求（Quick Brief 级别）可跳过此步，直接拆 Story。

### 第 2 步：选择拆分技术

根据需求特征选择最合适的拆分方式：

| 技术 | 适用场景 | 示例 |
|------|---------|------|
| **A. 按工作流步骤** | 有明确的用户操作流程 | 操作流程：选择配置→输入内容→设定参数→提交→查看结果 |
| **B. 按用户角色** | 多角色使用同一功能 | 普通用户的操作视图 / 管理员的配置管理 / 超级管理员的全局设置 |
| **C. 按数据类型** | 涉及多种数据实体 | 内容数据 / 模板数据 / 用户配额数据 |
| **D. 按 CRUD 操作** | 数据实体的标准管理 | 模板的创建/查看/编辑/删除 |
| **E. 主路径优先** | 需要快速验证核心假设 | 先实现最小可用的核心流程，再补充配置和管理功能 |

**决策建议**：
- 有明确操作流 → 优先选 A
- 多角色场景 → 优先选 B
- 标准 CRUD → 优先选 D
- 不确定 → 默认选 E（降低风险）

### 第 3 步：按交付链路拆分 Task

每条 Story 进一步拆分为 Task，按依赖方向排列。选择最贴合项目类型的维度：

| 维度 | 适用场景 | 示例 |
|------|---------|------|
| **技术层**（软件项目）| Web/移动端研发 | 数据层 → API 层 → 业务层 → UI 层 → 集成层 |
| **交付物/里程碑** | 客户交付、研究类 | 调研报告 → 方案草稿 → 客户确认 → 终稿交付 |
| **内容生产流** | 内容创作 | 选题确认 → 初稿 → 审校 → 发布 → 数据复盘 |
| **角色流转** | 跨角色协作 | PM 需求 → Dev 实现 → QA 验收 |
| **风险假设** | 有核心不确定性 | 先验证最高风险假设，再补全路径 |

### 第 4 步：工时估算

| 复杂度 | 预估工时 | 典型任务 |
|--------|---------|---------|
| 简单 | 1-2 小时 | 简单 CRUD、UI 微调、配置变更 |
| 中等 | 2-3 小时 | 业务逻辑、数据校验、组件开发 |
| 复杂 | 3-4 小时 | 复杂交互、外部集成、性能优化 |
| 超标 | >4 小时 | **必须继续拆分** |

### 第 5 步：价值 vs 工作量矩阵

对拆解后的 Story 进行 2×2 矩阵评估：

```
                低工作量          高工作量
高价值    │  ★ 优先做        │  计划做（安排迭代）  │
低价值    │  顺手做（填充空闲）│  不做（除非必须）    │
```

评估维度：
- **价值**：用户影响力 + 战略对齐度 + 业务紧迫性
- **工作量**：开发工时 + 技术风险 + 依赖复杂度

### 第 6 步：INVEST 验证

对每条 Story 逐项检查：

| 原则 | 检查问题 | 通过？ |
|------|---------|-------|
| **I** ndependent（独立） | 不依赖未承诺的其他 Story？ | ☐ |
| **N** egotiable（可谈判） | 有多种实现方案可选？ | ☐ |
| **V** aluable（有价值） | 对用户或业务有明确价值？ | ☐ |
| **E** stimable（可估算） | 团队理解范围，能给出工时？ | ☐ |
| **S** mall（足够小） | 可在一个迭代内完成？ | ☐ |
| **T** estable（可测试） | 有可写出验收方向？（详细 AC 由 acceptance-criteria 细化） | ☐ |

未通过 → 回到第 2/3 步重新拆分。

## board.yaml 任务条目模板

```yaml
- id: "TASK-{序号}"
  title: "[{模块/阶段}] {动作}{对象}"
  description: |
    ## 目标
    {一句话描述}

    ## 详细要求
    - {要求1}
    - {要求2}

    ## 完成标准
    - [ ] {标准1}
    - [ ] {标准2}
  status: pending
  priority: P1
  assigned_to: dev        # 实现类 → dev；Prompt 变更 → prompt-eng；需求/调研 → pm；测试/签发 → qa
  created_at: "YYYY-MM-DD"
  updated_at: "YYYY-MM-DD"
  deadline: null
  depends_on: ["TASK-{依赖ID}"]
  tags: []                # 按需追加 needs-qa-regression / prompt-review / security（模块归属走 module 字段，不进 tags）
  estimate_hours: 3
  module: "<basic>/<sub>"            # ★ 二段式；modules/ 体系下必填（schema 见 task-management）
  req_slug: "<req_slug>"             # 挂需求就填，且必须与下一行同时出现
  sub_req_slug: "main"               # 与上一行同进同出；main 也要显式写
  notes: "源自 modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md"  # 跨 Epic 关联也记在此处
```

## 输出清单

拆解完成后：

1. **如有影响拆解方向的关键决策点 → 先向用户提问**，收到答复后继续；小疑点不阻塞
2. 在响应消息中呈现完整拆解结果：
   - **Epic 描述**（如有）
   - **Story 列表**（带 INVEST 验证结果）
   - **Task 列表**（含 board.yaml 条目草稿）
   - **价值 vs 工作量矩阵**
   - **依赖关系图**（文本描述）
   - **关键路径标注**（最长依赖链）
   - **总工时预估**
3. 收到用户确认信号后：
   - **仅在响应中输出"待落盘的 board.yaml task 条目草稿"**（含 title / assigned_to / priority / tags / depends_on / estimate_hours），不直接写 board.yaml
   - 落盘由用户/主 Claude 调用 `task-management` skill 执行——与 `agents/pm.md` "由用户确认后由主 Claude 落盘"边界对齐，避免 PM skills 越界改 board
   - 不修改 `summary:` 块（summary 由 SessionEnd hook 统一重算）
