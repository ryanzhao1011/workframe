---
type: overview
overview_level: req
status: planning
owner_role: {{OWNER_ROLE}}
updated: {{NOW_ISO}}
module: "{{MODULE_PATH}}"
req_slug: "{{REQ_SLUG}}"
description: ""                        # ★ 一句话摘要（1-2 句 ≤200 字，document-norms §2.1）；需求立项时填写
related: []
tags: []
auto_sections:
  - sub-requirements-index
---

# {{REQ_TITLE}}

## 需求总览

<!-- 一段话描述本需求的核心价值与演进意图。**这一段由人写**。 -->

## 子需求清单

<!-- WORKFRAME:AUTO-INDEX:START -->
> ⚙️ 此段由 `module-index-refresh` 自动维护，请勿手动编辑。基于 `<sub_req_slug>/prd.md` frontmatter 提炼。

| 子需求 | 状态 | 摘要 | PRD | 复盘 |
|---|---|---|---|---|
| _暂无子需求。运行 `/core:module-index-refresh` 同步_ | - | - | - | - |

<!-- WORKFRAME:AUTO-INDEX:END -->

## 关联资产

- 子模块 overview：`../../overview.md`
- 当前实现状态：`../../current-state/`
- 跨子需求依赖（meta.yaml 的 `related_specs`）：见 `meta.yaml`

## 命名与 slug 演进

如本需求 `req_slug` 后续重命名，**不挪目录**——在 `meta.yaml.aliases` 加旧 slug，所有形如 `<新-req_slug>/overview` 的引用仍可定位（obsidian alias 解析 + grep 兜底）。

`<sub_req_slug>/` 子需求目录默认名为 `main`；范围显著变化时新建另一个 `<sub_req_slug>/`（不挪 `main/`）。何时改 prd.md / 何时拆子需求详见 `module-architecture.md` §4.3。

## 参考

- skill: `document-norms` §1（归属）/ §2.6（req_slug + sub_req_slug 引用契约）
- skill: `prd-writer`（生成 `<sub_req_slug>/prd.md`）
- skill: `test-case-design`（生成 `<sub_req_slug>/test-cases/`）
