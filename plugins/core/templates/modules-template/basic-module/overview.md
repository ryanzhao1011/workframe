---
type: overview
overview_level: basic-module
status: planning
owner_role: {{OWNER_ROLE}}
updated: {{NOW_ISO}}
# 注：basic-module overview 不设 module 字段（document-norms §2.6 二段式 module 仅适用于 <basic>/<sub> 及以下层级）
basic_name: "{{BASIC_NAME}}"
description: ""                        # ★ 一句话摘要（1-2 句 ≤200 字，document-norms §2.1）；写完「定位」段后回填
related: []
tags: []
auto_sections:
  - submodules-index
---

# {{BASIC_NAME}} 基础模块

## 定位

<!-- 基础模块的领域定位、核心责任、与其他基础模块的边界。**这一段由人写**，是稳定信息。 -->

## 架构

<!-- 该基础模块的内部架构 / 子模块协作图 / 关键流程。建议用 mermaid。 -->

```mermaid
graph LR
    A[子模块 A] --> B[子模块 B]
```

## 子模块速览

<!-- WORKFRAME:AUTO-INDEX:START -->
> ⚙️ 此段由 `module-index-refresh` 自动维护，请勿手动编辑。

| 子模块 | 状态 | 摘要 |
|---|---|---|
| _（暂无子模块。运行 `/core:module-init` 创建第一个）_ | - | - |

<!-- WORKFRAME:AUTO-INDEX:END -->

## shared/ 共享实体

| 实体 | 路径 | 说明 |
|---|---|---|
| _（按需添加跨子模块复用的数据模型 / 设计原则等）_ | `shared/` | - |

## 修订记录

- {{TODAY}} 基础模块创建（module-init 生成）

## 参考

- skill: `document-norms` §3（三段制 overview）
- 全局 overview：`projects/modules/overview.md`
