---
type: current-state
status: draft
owner_role: dev
updated: {{NOW_ISO}}
module: "{{MODULE_PATH}}"
description: ""                          # ★ 一句话摘要（1-2 句 ≤200 字，document-norms §2.1）；code-to-doc 解析后填写
generator: manual
source_repo: ""                          # 跨仓时填，本仓留空
source_ref: ""                           # commit:abc123 / branch:main / tag:v2.3.1
source_paths: []                         # glob 数组（与 submodule.yaml.code_paths 对齐）
source_exported_at: null                 # 跨仓 export 时间
verifier: ""                             # 谁核对过
confidence: medium                       # high | medium | low
related: []
tags: []
---

# {{SUB_NAME}} 架构

<!-- 由 `code-to-doc` skill 生成；正文每条关键结论必须带行级 source path（详见 skill: `document-norms` §5.3）。 -->

## 主流程

<!-- 业务核心流程，从入口到出口，逐步跟踪。每条结论带 `path:line` 锚点。 -->

```
入口：`<file>:<line>`
→ 处理：`<file>:<line>`
→ 输出：`<file>:<line>`
```

## 核心组件

| 组件 | 职责 | source path |
|---|---|---|
| _（待 code-to-doc 解析填充）_ | - | - |

## 依赖关系

| 依赖项 | 类型 | 来源 | source path |
|---|---|---|---|
| _（外部模块/服务/库依赖）_ | - | - | - |

## 关键设计决策（从代码反推）

- _（如：使用了某个特定 pattern / 折中方案 / hard-coded 配置等）_

## 已知问题（从 TODO/FIXME 提取）

| 文件 | 问题 | source path |
|---|---|---|
| _（grep TODO/FIXME 结果）_ | - | - |

## 修订记录

- {{TODAY}} 初次解析（code-to-doc 生成）
