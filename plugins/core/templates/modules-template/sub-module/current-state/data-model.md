---
type: current-state
status: draft
owner_role: dev
updated: "{{NOW_ISO}}"
module: "{{MODULE_PATH}}"
description: ""                          # ★ 一句话摘要（1-2 句 ≤200 字，document-norms §2.1）；code-to-doc 解析后填写
generator: manual
source_repo: ""                          # 空=本仓；项目内嵌套仓填其项目根相对路径；项目外填脱敏标识（对账整条跳过）
source_ref: ""
source_paths: []                         # 仓相对（坐标系由 source_repo 定）；与 code_paths 该仓内那部分同指一组文件；目录写 foo/** 不写 foo/
source_exported_at: null
verifier: ""                             # 这次核对了哪几项、不含哪几项；随基准一起重写
confidence: medium
related: []
tags: []
---

# {{SUB_NAME}} 数据模型

<!-- 持久化结构 + 内存数据结构。每条带 source path 行级 anchor。 -->

## 持久化表 / 集合 / 文件

| 名称 | 关键字段 | 索引 / 约束 | source path |
|---|---|---|---|
| _（数据库表 / 云开发集合 / JSON schema）_ | - | - | - |

## 内存数据结构

| 名称 | 类型 | 用途 | source path |
|---|---|---|---|
| _（关键 state / cache / session 结构）_ | - | - | - |

## 数据流向

```mermaid
graph LR
    A[输入] --> B[内存结构]
    B --> C[持久化]
    C --> D[读取/查询]
```

## 关键约束

- _（如：唯一性约束 / 并发约束 / 一致性策略 / TTL 等）_

## 已知数据风险

| 风险 | 影响 | source path | 缓解策略 |
|---|---|---|---|
| _（如：未加索引的 hot path / 缺乏迁移方案的 schema）_ | - | - | - |

## 修订记录

- {{TODAY}} 初次解析（code-to-doc 生成）
