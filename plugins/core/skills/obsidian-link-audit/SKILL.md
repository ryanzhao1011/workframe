---
name: obsidian-link-audit
description: 用 Obsidian 官方 CLI 做链接、反链、坏链与孤岛文档审计——改文档前查谁引用了它，改完后确认没留下断链。CLI 不可用时 fallback 到 rg/Read。
when_to_use: |
  正式文档修改前后、单文件 rename/move 前后、发布到外部平台前；
  用户问「谁引用了这个文档 / 有没有断链 / 哪些是孤岛文档」时；
  删改概念后要确认无落空引用时（document-norms §8.1 Step 3 的 L2 反链查漏）。
user-invocable: true
allowed-tools: [Bash, Read, Grep, Glob]
---

# Obsidian Link Audit

## 定位

本 skill 是 Obsidian CLI 增强层，只做链接语义审计，不写文件。

主链路仍是本地 Markdown + `rg` / `Read`。当 Obsidian CLI 可用时，用官方 CLI 获取 Obsidian 视角下的 backlinks、outgoing links、unresolved、orphans、deadends；CLI 不可用时回退到 `rg` 近似查询。

## 触发场景

- 修改正式 `spec` / `plan` / `decision` 前后，需要确认影响面。
- 发布到外部平台前检查坏链（由项目配备的 `<platform>-publish` 发布器调用）。
- 单文件 `rename` / `move` 前后检查引用是否更新。
- 用户问“哪些文档引用了它”“有没有坏链”“有没有孤岛文档”。

## 何时不用

- 只读单篇正文：直接 `Read`。
- 简单关键词全文检索：直接 `rg` 更快。
- 写正文、改 frontmatter：不用本 skill，交给业务 skill / `obsidian-safe-write`。
- 外部平台发布：本 skill 不负责发布，只做发布前校验。

## CLI Probe

优先读取 `.claude/workframe-state/obsidian-cli-status.json`（schema 权威定义见 skill: `obsidian-doc-structure` §status.json schema）：

- `do_not_probe: true` → **永不重新 probe**（不设 TTL、不做过期自判），直接按 `cli_available` 决定走 CLI 还是 fallback；重新启用 CLI 的唯一入口 = 用户手动删除该文件
- 缓存缺失 → 执行**非执行检测**（全程不运行任何 obsidian 命令）：定位命令（`Get-Command obsidian` / `command -v obsidian`）→ 判定是否 GUI 启动器（所在目录存在 `Obsidian.exe` / `resources.pak` 特征文件）→ 不可用则写入 status.json（`cli_available: false`、`do_not_probe: true`、`reason`）并 fallback
- 仅当非执行检测确认存在独立 CLI shim 时，才允许执行 `obsidian version` / `obsidian vault info=path` 验证并写入 `commands_verified`

> ⚠️ 未通过非执行检测时禁止执行任何 `obsidian` 命令——在「装了 GUI 但未启用 CLI」的机器上会误启动 Obsidian 窗口。

若不可用，记录 fallback 原因并改用 `rg` / `Read`。

## 调用前检查

执行任一 Obsidian CLI 命令前，必须依序确认：

- `status.json` 存在且 `do_not_probe: true` 且 `cli_available: false` → 直接 fallback，不做任何 probe。
- 命令在 `commands_verified` 数组中 → 可以使用 CLI。
- 命令不在数组中 → 直接 fallback，不尝试执行。
- `status.json` 不存在或 schema 不匹配 → 按「CLI Probe」的非执行检测流程重新检测（禁止直接执行 obsidian 命令）。

## 官方 CLI 命令

以本机 `obsidian help <command>` 输出为准，常用命令：

```bash
obsidian backlinks path="<file>" format=json
obsidian links path="<file>" format=json
obsidian unresolved verbose format=json
obsidian orphans format=json
obsidian deadends format=json
```

若本机参数名是 `file=` 而不是 `path=`，按 `obsidian help backlinks` / `obsidian help links` 调整。

## Fallback

```bash
rg '\[\[[^\]]*<目标文件名>[^\]]*\]\]' projects docs
rg '\[\[' "<当前文件>"
rg "\]\([^)]*\.md\)" projects docs
```

孤岛文档可用 `rg --files -g "*.md" projects docs` 列出候选，再与 overview / wikilink 引用结果对照。

## 输出要求

- 先列出 CLI 是否可用和使用的命令。
- 区分 `backlinks`、`outgoing links`、`unresolved`、`orphans/deadends`。
- 发现坏链时给出文件路径和建议修复方向。
- 不自动修复；需要修复时交给用户确认或调用相应写入 skill。

## Workframe Event

使用后按 `agent-protocols.md` 记录 `skill_used` 事件，至少包含：

```json
{
  "ts": "<ISO-8601>",
  "type": "skill_used",
  "skill": "obsidian-link-audit",
  "role": "<role>",
  "success": true,
  "source": "<file or scope>",
  "cli_available": true,
  "fallback_used": false
}
```
