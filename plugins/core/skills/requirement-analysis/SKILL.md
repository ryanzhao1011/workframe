---
name: requirement-analysis
description: 结构化需求澄清与优先级评估：判断需求形态（Full PRD / One-Pager / Quick Brief）、6 问澄清、RICE + MoSCoW 评估；Full PRD 移交 prd-writer，中小需求输出轻量需求摘要
when_to_use: |
  用于把模糊需求 / 业务想法澄清到"可以决定做不做、做多大、先做哪个"时调用。
  典型触发："分析需求 X" / "对齐范围" / "这个需求值不值得做" / "RICE 评估" / "优先级排序"。
  不用于：正式 PRD 写作（用 prd-writer，章节结构读项目 PRD 框架 .claude/skills/prd-style/）/ 用户反馈分析（用 user-feedback-analysis）/ 竞品调研（用 competitive-analysis）/ 拆解为子任务（用 feature-breakdown）。
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep]
---

# 需求澄清与评估技能

> 本 skill 只负责"想清楚"：要不要做、做多大、先做哪个。**正式 PRD 的工作流与写作纪律唯一来源是 `prd-writer`**（章节结构与风格读项目 PRD 框架 `.claude/skills/prd-style/`），本 skill 不含 PRD 文档模板；轻量产出（One-Pager / Quick Brief）同样遵循 prd-writer `writing-guide.md` 通用纪律（精简表达一目了然 / 分点分段 / 模型疑问当场向用户确认，不自行归档）与项目框架的编号习惯（默认无 FR-x、BR-x 编号 / 不设非功能章）；写作分级见 skill: `document-norms` §11——需求摘要按 A 类，评估/论证段按 B 类（结论先行 + 分层展开，不设字数约束）。

## 第一步：识别需求形态

| 形态 | 适用场景 | 开发量 | 去向 |
|------|---------|--------|------|
| **Full PRD** | 复杂功能，涉及多角色 / 多模块 / 前后端交互 | ≥1 周 | 澄清 + 评估完成后**移交 `prd-writer`** 走完整流程 |
| **One-Pager** | 中型功能，范围明确 | 2-5 天 | 本 skill 输出轻量需求摘要 |
| **Quick Brief** | 小型迭代、配置变更、文案调整 | ≤1-2 天 | 本 skill 输出问题 + 方案 + AC（≤1 页） |

不确定时倾向升一级处理。

## 第二步：结构化澄清（6 问）

填写任何产出前，先用以下 6 条核心问题澄清需求：

1. 这个需求解决**谁**的**什么问题**？
2. 不做这个需求的**代价**是什么？
3. 用户目前如何**绕过**这个问题？
4. **成功的样子**是什么？如何衡量？
5. 有哪些已知的**约束条件**（技术 / 时间 / 预算）？
6. 这个需求影响哪些**现有功能**？

**疑问处理纪律**：

- 影响文档方向或范围的关键疑问 → **先向用户提问**，收到答复再继续
- 次要疑问用 `[待确认: {说明}]` 占位，产出前汇总一次性问清；**严禁编造内容**
- 用户明确说"你看着填"时才允许 `[待确认]` 留入产出物（与 writing-guide「待确认项」准入一致）

## 第三步：优先级评估（按需）

### RICE 计分

| 维度 | 评分 | 说明 |
|------|------|------|
| Reach（影响范围） | {N} 用户/季度 | {说明} |
| Impact（影响程度） | 0.25/0.5/1/2/3 | {说明} |
| Confidence（置信度） | 50%/80%/100% | {说明} |
| Effort（工作量） | {N} 人周 | {说明} |

**RICE 得分** = Reach × Impact × Confidence / Effort

### MoSCoW 分类

Must have（必须做）/ Should have（应该做）/ Could have（可以做）/ Won't have（不做）

**综合优先级**：P0 / P1 / P2 + 建议迭代

## 第四步：产出

### Full PRD → 移交 prd-writer

输出一段**澄清结论**（形态判断 + 6 问答案摘要 + 优先级结论 + 已拿到的用户拍板），作为 prd-writer S1 的输入；**不在本 skill 内写任何 PRD 章节**。

### One-Pager 轻量模板

```markdown
# {需求名} — One-Pager

## 背景与目标
- 现状：{一句}
- 痛点：{一句；可多条}
- 目标：{一句}
- 边界：{真边界，如有}

## 方案
1. **{要点 1}**：{一两句}
2. **{要点 2}**：{一两句}

## 验收标准
{AC-序号 + GWT / 规则式清单，格式见 acceptance-criteria skill}

## 优先级
RICE {N} · MoSCoW {分类} · 建议 {排期}
```

### Quick Brief 模板

```markdown
# {需求名} — Quick Brief

- 问题：{一句}
- 方案：{分点}
- 验收：{1-3 条 AC}
```

## 完成度核查清单

- [ ] 问题陈述清晰，团队成员理解一致
- [ ] 成功指标可量化（或明确说明不可量化的原因）
- [ ] 功能范围已明确边界（做什么 + 真边界）
- [ ] 所有需求条目可测试
- [ ] `[待确认]` 已全部向用户问清（仅用户明确授权保留的除外）

## 执行步骤

1. 判断需求形态（Full PRD / One-Pager / Quick Brief）
2. 执行 6 问澄清，识别关键不确定项；**关键疑问先问用户**，次要疑问 `[待确认]` 占位
3. 按需执行 RICE + MoSCoW 评估
4. 按形态产出：Full PRD 输出澄清结论并移交 `prd-writer`；One-Pager / Quick Brief 按上述模板在响应消息中呈现完整内容
5. 执行完成度核查清单
6. 收到用户确认信号后落盘：
   - **早期需求探索 / 一句话需求 / Quick Brief / One-Pager**：写入 `projects/modules/<basic>/<sub>/requirements/_draft/<slug>.md`（草稿区；属于 @pm 输出根目录范畴，可直接落盘）。即使是草稿，**也需 minimal frontmatter**：`type: concept` + `module: <basic>/<sub>` + `owner_role: pm` + `updated: <ISO-8601 带时区，见 document-norms §2.7>` + `status: draft`（缺 frontmatter 的草稿不会被 librarian / module-index-refresh 扫到，等于知识黑洞）
   - **Full PRD / 已立项需求**：接 `prd-writer` skill 走完整 PRD 流程到 `projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md`（首次默认子需求 `main/`）；如对应子模块/需求资产包未建，先调 `module-init` 创建骨架
   - 文档归属与 frontmatter 字段标准查 skill: `document-norms` §1 §2
   - **board.yaml 任务条目仅输出草稿**到响应中，不直接写 board.yaml。落盘由用户/主 Claude 调用 `task-management` skill 执行（与 `agents/pm.md` "由用户确认后由主 Claude 落盘"边界对齐）；task 草稿必填二段式 `module: <basic>/<sub>`；挂需求的 `req_slug` + `sub_req_slug` **一起填**（`main` 也显式写）
   - 草稿格式（必填二段式 `module` + 可选 `req_slug` / `sub_req_slug` / `affected_modules`）：

     ```yaml
     # 待落盘的 board.yaml task 条目草稿
     - id: "TASK-<下一个序号，由 task-management 分配>"
       title: "..."
       assigned_to: "..."
       priority: "..."
       tags: ["..."]
       module: "<basic>/<sub>"            # ★ 二段式，对应 projects/modules/<basic>/<sub>/
       req_slug: "<req_slug>"             # 挂需求就填，且必须与 sub_req_slug 同时出现
       sub_req_slug: "main"               # 与上一行同进同出；main 也要显式写
       # affected_modules: ["a/b", "c/d"] # 可选，横切多模块用
       notes: "源自 modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/prd.md"
     ```
