---
type: overview
overview_level: requirements
status: planning
owner_role: pm
updated: {{NOW_ISO}}
module: "{{MODULE_PATH}}"
description: ""                        # ★ 一句话摘要（1-2 句 ≤200 字，document-norms §2.1）；子模块需求方向明确后回填
related: []
tags: []
auto_sections:
  - requirements-by-status
---

# {{SUB_NAME}} 需求清单

<!-- WORKFRAME:AUTO-INDEX:START -->
> ⚙️ 此段由 `module-index-refresh` 自动维护，请勿手动编辑。基于 `<req_slug>/meta.yaml` 提炼。

## active 进行中

| req_slug | 子需求数 | owner | 摘要 |
|---|---|---|---|
| _（暂无）_ | - | - | - |

## planning 规划中

| req_slug | owner | 摘要 |
|---|---|---|
| _（暂无）_ | - | - |

## done 已完成

| req_slug | 子需求数 | 完成时间 |
|---|---|---|
| _（暂无）_ | - | - |

## dropped 已放弃

| req_slug | 放弃时间 | 原因 |
|---|---|---|
| _（暂无）_ | - | - |

## _未知_ 状态缺失 / 非法

> `meta.yaml` 缺 `status` 或取值不在四态内的需求渲染到这张表。
> 不是长期归置区——补上 `meta.yaml.status` 后重跑索引即归位。

| req_slug | 实际取值 | owner |
|---|---|---|
| _（暂无）_ | - | - |

<!-- WORKFRAME:AUTO-INDEX:END -->

## _draft 草稿区

`_draft/` 下放未立项的需求探索（一个 .md 文件 / 一个想法）。立项后必须改建为 `<req_slug>/<sub_req_slug>/` 目录结构（默认子需求名 `main`）。

## 参考

- skill: `document-norms` §1（归属）/ §2（meta.yaml 字段）
- 父子模块 overview：`../overview.md`
