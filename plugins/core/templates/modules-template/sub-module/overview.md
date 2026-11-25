---
type: overview
overview_level: sub-module
status: planning
owner_role: "{{OWNER_ROLE}}"
updated: "{{NOW_ISO}}"
module: "{{MODULE_PATH}}"
description: ""                        # ★ 一句话摘要（1-2 句 ≤200 字，document-norms §2.1）；写完「定位」段后回填
related: []
tags: []
auto_sections:
  - current-state-summary
  - requirements-index
---

# {{SUB_NAME}} 子模块

## 定位

<!-- 子模块的功能定位 / 用户价值 / 与父基础模块的关系。**这一段由人写**，反映稳定信息。 -->

## current-state 摘要

<!-- WORKFRAME:AUTO-INDEX:START:current-state-summary -->
> ⚙️ 此段由 `module-index-refresh` 自动维护。每行「：」之后到第一个「；」由脚本按 `current-state/` 取数，「；」之后的文字由人写、刷新时原样保留。

- **架构**：_（未编写）_
- **API**：_（未编写）_
- **数据模型**：_（未编写）_
- **代码索引**：_（未编写）_
- **最近同步**：_（未同步）_

<!-- WORKFRAME:AUTO-INDEX:END:current-state-summary -->

完整内容见 `current-state/` 目录。

## 需求清单

<!-- WORKFRAME:AUTO-INDEX:START:requirements-index -->
> ⚙️ 此段由 `module-index-refresh` 自动维护，请勿手动编辑。基于 `requirements/<req_slug>/meta.yaml` 计数。

进行中 0 · 规划 0 · 已完成 0 · 已放弃 0 · 状态未知 0

完整清单见 [requirements/overview.md](<./requirements/overview.md>)。

<!-- WORKFRAME:AUTO-INDEX:END:requirements-index -->

完整需求清单分组见 `requirements/overview.md`。

## decisions / research / others

| 类别 | 路径 | 说明 |
|---|---|---|
| ADR 决策记录 | `decisions/` | 单模块决策；跨模块决策放 `projects/specs/plans/` |
| 调研 | `research/` | 调研报告 / 竞品分析 / 用户访谈 |
| 杂项 | `others/` | ≥5 份时拆出新目录 |

## 修订记录

- {{TODAY}} 子模块创建（module-init 生成）

## 参考

- skill: `document-norms` §3（三段制 overview）/ §4（索引层级与同步规则）
- 父基础模块 overview：`../overview.md`
- code_paths 配置：`submodule.yaml`
