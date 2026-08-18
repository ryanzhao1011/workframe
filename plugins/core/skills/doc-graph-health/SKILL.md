---
name: doc-graph-health
description: 知识网健康巡检：跑 skill 内 scripts/graph_health.py 产出持久报告 projects/modules/graph-health.md（断链 / 孤儿 / hub / stale current-state / 姊妹时差 / updated 异常 / 概念热点七项检测），并按处置指引推进修复。触发词：知识库体检、图谱健康、巡检知识网、graph-health、查断链、查孤儿文档、文档过期检查。
when_to_use: |
  - 大规模文档变更后（批量回填 / 迁移 / 织网 / 归档收口）；
  - 月度周期性体检（无 hook 自动触发，靠人/会话建议）；
  - 用户问「知识库健康吗 / 有没有断链 / 哪些文档过期了」。
  扫描对象是 projects/modules + projects/specs 的链接图（workframe 项目骨架恒有）。
user-invocable: true
allowed-tools: [Bash, Read, Grep, Glob, Edit, AskUserQuestion]
---

# Doc Graph Health 知识网健康巡检

## 定位

对应 LLM Wiki 闭环的「Audit 体检」阶段：让知识库能自我发现断链、孤岛、过期与缺口。

**事实源分工**：检测逻辑与阈值在本 skill `scripts/graph_health.py`；报告 `projects/modules/graph-health.md` 由脚本重算，**勿手改正文**；本 SKILL.md 只管「何时跑、怎么读、如何处置」。

## 怎么跑

从项目根目录执行（插件根路径由 SessionStart hook 写入 `plugin-root.txt`，不依赖 PATH）：

```bash
python "$(cat .claude/workframe-state/plugin-root.txt)/skills/doc-graph-health/scripts/graph_health.py"          # 全量巡检 + 覆写报告
python "$(cat .claude/workframe-state/plugin-root.txt)/skills/doc-graph-health/scripts/graph_health.py" --dry    # 只看摘要不写报告
```

跑完对比上次：`git diff -- projects/modules/graph-health.md`（收敛趋势比绝对数字重要）。

## 七项检测与处置

| 检测 | 含义 | 处置 |
|---|---|---|
| 断链 | wikilink / md 链接 / frontmatter `related` 目标不存在 | 少量直接修；成批查根因（改名未同步等） |
| 孤儿 | 入链=0 的文档（三源计入度；结构性文件已豁免） | 有价值 → 补上级 related / 正文链接；过时 → 归档 `projects/archive/`；两者都不做时留在报告里可见 |
| Hub | 被引 top10 | 只读参考——横向核心资产，重构/改名时优先评估影响面 |
| stale current-state | 超 45 天未同步 | stub 形态 → 排期 code-to-doc；有内容 → 让 @dev 核对代码后刷新 |
| **姊妹时差** | req overview 落后同 req prd >7 天 | **必须逐对抽读确认**：比对 overview 定位段与 prd「变更与决策记录」，确认口径矛盾（如字段列数、规则枚举）→ 修 overview 正文（bump updated）；无矛盾 → 报告中该对会随 overview 下次更新自然消失 |
| updated 缺失/格式错 | 有 frontmatter 但 `updated` 不可解析——这些文件游离在 stale 判定之外 | 按 `document-norms` §2.7 补齐（实时 ISO-8601） |
| 概念热点 | 词表词高频提及但未链接 | 织网候选（补 wikilink）或评估是否立跨模块 spec |

## 概念热点的词表来源（项目自配）

优先级：`.workframe-config.json` 的 `graph_health.hotspot_words`（显式覆盖）→ `projects/specs/_meta/taxonomy.md`「## 能力域」表格（推荐正源：词表随项目 tags 纪律自然生长）→ 两者皆无则**跳过本维度**并在报告中给启用指引。

报告会标注词表来源与 taxonomy 的 updated 时间——**词表停更 = 本维度漏检新概念**，看到来源时间明显陈旧时先更新词表再看结论。

## 排除纪律（与巡检口径一致，勿放宽）

- 基线排除目录：`prototypes/`（含 demo 整仓）、`node_modules/`、`.claude/`、`tmp/`、`_draft/`、`assets/`；项目可经 config `graph_health.extra_exclude_dirs` 追加
- 结构性豁免：`current-state/` 四件套、`requirements/overview.md` 清单页（位置约定即入口，不计孤儿）
- 行级噪声：骨架模板的固定 checklist 行会虚增热点计数——把该行的特征子串配进 config `graph_health.noise_line_markers`，命中行不计热点

## 矛盾确认后的修复边界

- 修 overview 正文 = 实质变化 → 实时 bump `updated`（`document-norms` §2.7）
- 修完重跑脚本，确认该时差对从报告消失
- 跨 PRD 的口径矛盾（同一规则两处定义打架）超出本 skill 自动能力，发现后走 Issue / board 任务由 owner 处理

## 与其他能力的协作

| 对象 | 关系 |
|---|---|
| `scripts/graph_health.py`（本 skill 内） | 检测与阈值唯一事实源（STALE_CS_DAYS=45 / SIBLING_GAP_DAYS=7 / 白名单），调阈值改脚本头部常量 |
| `obsidian-link-audit` | 单文件级链接审计；本 skill 是全库级，互补不替代 |
| `module-index-refresh` | 索引重算不会修复本报告问题；孤儿补链后可按需限定路径刷新 |
| `requirement-archiving` | 其 Phase 7 关联收口以本 skill 巡检通过为门禁 |

## Workframe Event

使用后按 `agent-protocols.md` 记录 `skill_used` 事件：

```json
{"ts":"<ISO-8601>","type":"skill_used","skill":"doc-graph-health","role":"<role>","success":true,"source":"projects/modules/graph-health.md"}
```
