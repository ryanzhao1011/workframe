---
name: test-case-design
description: '测试用例设计与验证，先按四档判据定测试选型（类型/深度/广度/是否隔离），再基于 GWT 验收标准生成 Happy/Sad/Boundary 三类用例矩阵，含失败处理和签发流程。modules/ 体系下用例落盘到 requirements/<req_slug>/<sub_req_slug>/test-cases/。用于判断本次改动该测什么类型、多深、多广、要不要隔离环境，以及基于验收标准生成测试用例矩阵、覆盖 Happy/Sad/Boundary 路径、走签发流程。典型触发："设计测试用例" / "测一下 X" / "这次该测到什么程度" / "要不要跑全套" / "回归覆盖范围" / "QA 验证 pending_qa 任务"。不用于：bug 调试（用 systematic-debugging）/ 代码审查（用 code-review）/ Prompt 评估（用 prompt-evaluation）。'
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

## 六步流程

### 第 0 步：选型（这次测什么类型、多深、多广、要不要隔离）

**怎么用这一步：下面三问全部问完，命中哪档取哪档，多个命中取最高。答「是」或「说不准」都算命中。**

1. 错了会不会传出这个文件之外？ → 命中 = **至少档 2**
2. 有没有东西会因为**你改的那部分内容**产出可比较的结果（通过/不通过、分数、diff、快照）？ → 命中 = **至少档 3**
3. 改的对象本身的职责就是「判断别的东西对不对」吗？ → 命中 = **档 4**

三问全不命中 → 档 1。**三问要全问完，不是答否就停**——档 4 与前三档不构成阶梯：一份 PRD 完整性自查清单，第 1 问答否（错误止于本文），却是不折不扣的档 4。**升一档最多多花半天；降一档漏掉的那次，没有人会告诉你。**

| 档 | 什么情况 | 类型 | 深度 | 广度 | 隔离 |
|---|---|---|---|---|---|
| **1** | 纯内容改动，错误止于本文 | 主路径读一遍 + 一致性核对 | 只验「写的和实际一致」 | 改动点本身 | 不需要 |
| **2** | 错误会传到别的文件或别的人，但**没有东西会因此产出结果** | + 引用面核对 | + 空 / 缺失 / 异常格式 | + 同族边界三问 | 不需要，但改前先提交 |
| **3** | **有消费方会因你改的内容产出可比较的结果** | + 跑真实消费方并排对照 | + 边界矩阵；证据拿不到时如实标注强度 | + 相邻分支 | 要写进被验对象时**用副本** |
| **4** | **被改的东西自己的职责就是判断别的东西对不对**（测试、校验、断言、自查清单、评分标准；**不论靠机器还是靠人执行**）| + **用它自己的失败方式打一遍** | + 先跑基线确认是绿的 + 结论的反面要能被打出来 | + 同契约的其他实现 | **必须副本** |

升档是**累加**不是替换——档 3 要做的包含档 2 的全部动作。**档位是爆炸半径，不是用心程度**：判在档 1 就按档 1 交付，多做的部分不算加分，只是把本该留给档 3/4 的力气花在了不需要的地方。

**第 2 问怎么答**（判错最多的一处，三条都要过）：

- 判**存不存在**，不判**你现在跑不跑得动**——跑不动（要 CI / 要凭证 / 要真机）是**证据问题**，按证据强度如实写进「不含」，不是降档理由
- 判**它对你改的那部分敏不敏感**——某道闸只读 frontmatter 而你改的是正文措辞，它对本次改动不敏感，不因它升档；否则在任何带 lint 的仓里每个文件都是档 3，档 1 与档 2 被抽空
- **不看对象是不是代码**：一份配置有脚本读它并报错 → 档 3；一份文档的措辞被另一份文档引用、没有东西会因此产出结果 → 档 2。按对象类型分会把这两个判反

**档 4 为什么单列**：前三档改错了会炸，有信号；档 4 改错了**静默不报**——写完但从没真正执行过的检查，和通过长得一模一样。写 PRD 完整性自查清单、写输出质量的评分标准，同样落这一档。

**同族边界三问**（广度的判据）：这个事实在别处还有几份副本？这个约定还有几个使用方？这次改动周边还有哪些同类条目没碰到？
> 与 skill: `systematic-debugging` 第 3 步影响面五维**互补不重合**——五维按技术维度切（代码依赖 / 数据 / 接口 / 性能 / 安全）、只适用于修 bug、产出的是影响清单；三问按「同一事实的多处存在」切，产出的是测试范围。修 bug 时两组都要过。

**横切三条**（不分档，全适用）：

- 每项验证注明**证据强度**——①真实实跑 ②契约文档 / 官方规范 ③等价替身推演；拿不到就如实写「未验」，别把③记成通过（「不含哪几项」的口径见必载片 §陈述可证伪）
- **打回重做要三版并列**（原始基线 / 被打回版 / 返工版）——只比两版，会把「返工新引入的」和「首轮就在的」糊成一片
- **方向错阻断，尺度严登记**——假绿（该拦没拦）阻断，假红（多拦了、方向 fail-safe）登记不阻断；完整判据见 skill: `code-review` 第 3 步

> 各档判据详解、反例与非代码场景对照，见 `reference/test-scoping.md`——**Read 按会话工作目录解析相对路径，不按 skill 目录**，实际要读的是 `"$(cat .workframe/state/plugin-root.txt)/skills/test-case-design/reference/test-scoping.md"`。插件目录在项目外，读不到时上面的三问与四档表已够定档。

### 第 1 步：解析验收标准

读取需求来源，提取验收标准：

| 来源 | 提取内容 |
|------|---------|
| `projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md` | GWT 场景式 AC 和规则式 AC |
| Issue | Bug 的复现场景和期望修复结果 |
| @dev 交付说明 | 改动点和 QA 关注点 |
| 历史相关功能 / current-state/ | 需要回归的已有能力 |

### 第 2 步：用例矩阵生成

基于 AC 和代码变更，**按第 0 步选出的档位**生成用例：档 1 只出主路径用例，档 2 起补 Sad / Boundary，档 3 起补边界矩阵。下面三类是**档位选中时**各自的写法，不是无条件全做。

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

**每条 error / warn 必须归类为四者之一，不留「不确定」**：真缺陷 / 设计预期 / 环境不完整 / 测试数据不规范。归类是压缩动作——留着「不确定」这一格，最难判的那几条会全部滞留在里面，而结论写的是「已全部处理」。当场判不了的，写清**判不了什么、缺什么才能判**，登记为待裁；**登记是一种归类，「不确定」不是**。

> **与 skill `code-review` 第 3 步「处置轴」的分界**：本条分的是「跑出来这条 error **该不该算数**」；处置轴（假绿 / 假红）分的是「这道判定**本身写错的方向**」。两者不同轴，别互相套——归为「设计预期」说的是被检对象没问题（不改闸），判为「假红」说的是闸多拦了（要改闸）。

> **验证自己参与过的改动时**：一检（构建者正向）与二检（审计者反向）的完整判据见必载片 §验证纪律（必载，无需另找）。**二检换视角不能替代独立验证**——两者串联，不是二选一；本 skill 反模式末条说的就是这件事。

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
- **必须** append `.workframe/state/events.jsonl`（这是 self-iteration `problem` 加权分权重 2.0 的唯一可靠来源）：
  ```json
  {"ts":"<ISO-8601>","type":"task_blocked","task_id":"<TASK-ID>","role":"qa"}
  ```
  缺这条事件会导致 task_blocked 在 skill-metrics / self-iteration 触发器中永远是 0。

### 第 5 步：签发结单

> **本步走哪一档由四段闸门定，不由本 skill 定**：默认起点就是这一档（@qa 完整验证），
> 只有拿得出「覆盖本轮 delta 的独立结论」才可能降档，降到哪由爆炸半径卡住下限。
> 判据见必载片 §谁签发这次收口。
> **@qa 自己签发时不受它影响**——那条通道是给主 Claude 的；本步要留意的是反过来那半：
> 上一轮你出的结论会不会被下一轮当成通行证，而它**只覆盖你真正看过的那部分**。

全部通过时：

1. 更新 board.yaml 任务状态：`pending_qa → completed`
2. 生成测试报告（供后续审查）：

```markdown
## 测试报告：{Task-ID}

### 覆盖范围（第 0 步判定：档 {1-4}）
- Happy Path: {N} 个用例
- Sad Path: {N} 个用例
- Boundary: {N} 个用例

<!-- 档位未选中的类型写 `—（档 N 不要求）`，不写 0——写 0 会让按档交付的报告长得像漏做 -->


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

- ❌ 跳过第 0 步：任何改动都无条件跑满全套，或反过来一律只跑主路径
- ❌ 第 0 步判到档 2 及以上，却只测 Happy Path，跳过异常和边界
- ❌ 按「是不是代码」分档，或因为「我现在跑不动它」而降档——第 2 问问的是**有没有东西会因你改的内容产出可比较的结果**，跑不动是证据问题
- ❌ 测试结论只说"通过"，不给执行证据
- ❌ 发现问题不按 TEMPLATES.md 格式创建 Issue
- ❌ 测试未通过就签发 completed
- ❌ 用 @dev 提供的测试结论直接结单，不做独立验证
