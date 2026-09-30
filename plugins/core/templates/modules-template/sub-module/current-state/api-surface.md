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

# {{SUB_NAME}} API 表面

<!-- 模块对外暴露的接口 / 函数 / 事件 / 路由。每条带 source path 行级 anchor。 -->

## HTTP / 云函数接口

| 路由 / 函数名 | 方法 | 入参 | 出参 | source path |
|---|---|---|---|---|
| _（待 code-to-doc 解析填充）_ | - | - | - | - |

## 内部模块导出

| 导出名 | 类型（function / class / const） | 用途 | source path |
|---|---|---|---|
| _（待 code-to-doc 解析填充）_ | - | - | - |

## 事件 / 消息

| 事件名 | 触发条件 | payload | 订阅方 | source path |
|---|---|---|---|---|
| _（如有事件总线 / pub-sub）_ | - | - | - | - |

## 配置入口

| 配置文件 | 关键字段 | source path |
|---|---|---|
| _（如 app.json / package.json / .env 中本模块相关配置）_ | - | - |

## 修订记录

- {{TODAY}} 初次解析（code-to-doc 生成）
