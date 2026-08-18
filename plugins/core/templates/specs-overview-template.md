---
type: overview
status: planning
owner_role: pm
updated: {{NOW_ISO}}
# 注：specs 层不参与 modules/ 索引，故不设 overview_level / auto_sections
# （document-norms §2.4 的 overview_level 枚举只覆盖 modules/ 五层）
module: ""
description: ""                        # ★ 一句话摘要（1-2 句 ≤200 字，document-norms §2.1）；写完「项目愿景」段后回填
related: []
tags: [moc]
---

# 跨模块规范层 — 根说明

> `projects/specs/` 只放**跨模块**的规范 / 方案 / 决策类文档；**单模块的需求事实源一律在
> `projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>/`**，不放这里。
> 真正的对外交付物（产品代码 / 客户交付材料 / 已发布内容）放在项目顶层的业务目录
> （`src/` / `deliverables/` / `published/` 等），不放在 `projects/` 下。

## 项目愿景

（一句话目标）

## 核心范围

- 必须做：
- 优先做：
- 暂不做：

## 子目录组织（按需选用）

```
projects/specs/
├── overview.md              # 本文件
├── _meta/taxonomy.md        # tag 受控词表（装机放入；doc-graph-health 概念热点的词源）
├── METRICS-{序号}.md        # 跨模块分析产物直接放根级，不进下面的规范子目录
├── FEEDBACK-{序号}.md       #   （指标体系 / 反馈分析 / 竞品分析，见 document-norms §1.1）
├── COMP-{序号}.md
├── design-system/           # 设计规范（视觉 / 交互基线）
├── api-conventions/         # API 约定（命名 / 错误码 / 版本策略）
├── compliance/              # 合规 / 安全约束
└── plans/                   # 跨模块实施方案（如系统级架构升级）
```

四类**规范**子目录按需创建，空目录不必预建（`_meta/` 由装机放入）；分析产物按上表放根级。单模块的 spec / plan 改放
`modules/<basic>/<sub>/decisions/` 或该模块的需求资产包；只有跨模块的才进本目录。
详见 skill: `document-norms` §1 归属矩阵。

## 相关资产

- 任务看板：`projects/board.yaml`
- 问题记录：`projects/issues/`（扁平结构 + YAML 内 area/module 字段表达归属）
- 变更日志：`projects/changelog.md`
- 自迭代提案：`projects/proposals/{pending,applied,rejected}/`
