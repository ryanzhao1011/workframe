---
name: obsidian-history-check
description: 用 Obsidian 官方 CLI 做文档历史与 diff 的**只读**检查。默认不执行 history:restore——恢复必须单独确认。
when_to_use: |
  怀疑文档被误改、想看改动前后差异时；大改之前先看近期历史时；
  用户问「这个文档之前是什么样 / 什么时候改的 / 改了什么」时。
  边界：要回滚框架资产（agents/rules/skills）→ rollback skill，本 skill 只看不改。
user-invocable: true
allowed-tools: [Bash, Read, Grep]
---

# Obsidian History Check

## 定位

本 skill 只读历史、版本和 diff，不恢复、不覆盖、不删除文件。

它用于在文档疑似误改或大改前查看 Obsidian 本地历史。如果 CLI 不可用，则 fallback 到 git diff/log；项目没有 git 时，提示用户使用 Obsidian GUI 文件恢复。

## 触发场景

- 用户问“刚才改了什么”“能不能看之前版本”。
- agent 准备大改正式文档前，需要看近期历史。
- 外部评审反馈回写本地后，需要对比修改前后。
- 文档内容疑似被误改。

## 何时不用

- 普通读取当前正文：直接 `Read`。
- 查看当前工作区修改且项目有 git：优先 `git diff`。
- 要恢复历史版本：本 skill 只做只读检查，恢复必须用户单独确认。

## CLI Probe

优先读取 `.claude/workframe-state/obsidian-cli-status.json`（schema 权威定义见 skill: `obsidian-doc-structure` §status.json schema）：

- `do_not_probe: true` → **永不重新 probe**（不设 TTL、不做过期自判），直接按 `cli_available` 决定走 CLI 还是 fallback；重新启用 CLI 的唯一入口 = 用户手动删除该文件
- 缓存缺失 → 执行**非执行检测**（全程不运行任何 obsidian 命令）：定位命令（`Get-Command obsidian` / `command -v obsidian`）→ 判定是否 GUI 启动器（所在目录存在 `Obsidian.exe` / `resources.pak` 特征文件）→ 不可用则写入 status.json（`cli_available: false`、`do_not_probe: true`、`reason`）并 fallback
- 仅当非执行检测确认存在独立 CLI shim 时，才允许执行 `obsidian version` / `obsidian vault info=path` 验证并写入 `commands_verified`

> ⚠️ 未通过非执行检测时禁止执行任何 `obsidian` 命令——在「装了 GUI 但未启用 CLI」的机器上会误启动 Obsidian 窗口。

不可用时 fallback 到 git 或 Obsidian GUI 提示。

## 调用前检查

执行任一 Obsidian CLI 命令前，必须依序确认：

- `status.json` 存在且 `do_not_probe: true` 且 `cli_available: false` → 直接 fallback，不做任何 probe。
- 命令在 `commands_verified` 数组中 → 可以使用 CLI。
- 命令不在数组中 → 直接 fallback，不尝试执行。
- `status.json` 不存在或 schema 不匹配 → 按「CLI Probe」的非执行检测流程重新检测（禁止直接执行 obsidian 命令）。

## 官方 CLI 命令

```bash
obsidian history path="<file>" format=json
obsidian history:list path="<file>" format=json
obsidian history:read path="<file>" version="<version>"
obsidian diff path="<file>" from="<version>"
```

以本机 `obsidian help <command>` 的参数名为准。

## 禁止事项

- 默认不执行 `history:restore`。
- 不执行 `sync:restore`。
- 不覆盖当前文件。
- 不把历史版本直接写回源文件。

如果用户明确要求恢复，必须先：

1. 展示 `diff` 摘要。
2. 说明会覆盖哪些内容。
3. 等用户明确确认。
4. 优先建议另存为临时文件供人工对照，而不是直接覆盖事实源。

即使用户确认，本 skill 仍**不执行** `history:restore`。恢复流程拆成两步：

- 本 skill 只把历史版本导出到 `tmp/obsidian-history/<task-id>/restored-<version>.md`。
- 用户审阅后，如确实要覆盖事实源，再单独调用 `obsidian-safe-write` 或使用 `Edit` 执行覆盖。

这样保证“读取历史版本”和“覆盖事实源”是两次显式动作。

## Fallback

```bash
git diff -- <file>
git log --oneline -- <file>
git show <sha>:<file>
```

如果项目没有 git，则提示用户打开 Obsidian GUI 的文件恢复功能。

## Workframe Event

使用后按 `agent-protocols.md` 记录 `skill_used` 事件，至少包含：

```json
{
  "ts": "<ISO-8601>",
  "type": "skill_used",
  "skill": "obsidian-history-check",
  "role": "<role>",
  "success": true,
  "source": "<file>",
  "cli_available": true,
  "fallback_used": false
}
```
