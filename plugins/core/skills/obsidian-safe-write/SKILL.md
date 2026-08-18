---
name: obsidian-safe-write
description: 用 Obsidian 官方 CLI 做小范围、结构安全的写入：property:set / property:remove、prepend、create template，以及用户明确确认后的单文件 move/rename。大段正文仍由业务 skill + Edit/Write 完成。
when_to_use: |
  改文档 frontmatter 字段（含按 document-norms §2.7 维护 `updated`）时；
  用模板新建 spec / plan / decision / overview 时；在 frontmatter 后追加标准块时；
  用户明确要求重命名或移动某个单文件时。
  边界：要写大段正文 → 用 Edit/Write；批量多文件操作不走本 skill。
user-invocable: true
allowed-tools: [Bash, Read, Write, Edit, Glob, Grep]
---

# Obsidian Safe Write

## 定位

本 skill 只做 Obsidian-aware 的小范围安全写入，不负责业务正文创作。

适合写入标准 Properties、使用模板创建新文档、在 frontmatter 后追加标准块，以及用户明确确认后的单文件 `move` / `rename`。

## 触发场景

- 修改正式文档后更新 `updated`（格式与「何时不更新」的例外见 `document-norms` §2.7）。
- 项目配备的发布器（`<platform>-publish`）发布成功后回写该平台的同步状态字段。
- 新建 `spec` / `plan` / `decision` / `overview` 时用模板创建。
- 用户明确要求重命名或移动某个单文件。

## 何时不用

- 大段正文写作：用业务 skill + `Edit` / `Write`。
- 批量目录重命名：不要默认使用本 skill；先做方案和用户确认。
- 链接审计：用 `obsidian-link-audit`。
- 历史恢复：不用本 skill，恢复类操作必须单独确认。

## CLI Probe

优先读取 `.claude/workframe-state/obsidian-cli-status.json`（schema 权威定义见 skill: `obsidian-doc-structure` §status.json schema）：

- `do_not_probe: true` → **永不重新 probe**（不设 TTL、不做过期自判），直接按 `cli_available` 决定走 CLI 还是 fallback；重新启用 CLI 的唯一入口 = 用户手动删除该文件
- 缓存缺失 → 执行**非执行检测**（全程不运行任何 obsidian 命令）：定位命令（`Get-Command obsidian` / `command -v obsidian`）→ 判定是否 GUI 启动器（所在目录存在 `Obsidian.exe` / `resources.pak` 特征文件）→ 不可用则写入 status.json（`cli_available: false`、`do_not_probe: true`、`reason`）并 fallback
- 仅当非执行检测确认存在独立 CLI shim 时，才允许执行 `obsidian version` / `obsidian vault info=path` 验证并写入 `commands_verified`

> ⚠️ 未通过非执行检测时禁止执行任何 `obsidian` 命令——在「装了 GUI 但未启用 CLI」的机器上会误启动 Obsidian 窗口。

不可用时 fallback 到 `Edit` / `Write`，但必须先说明 fallback 原因。

## 调用前检查

执行任一 Obsidian CLI 命令前，必须依序确认：

- `status.json` 存在且 `do_not_probe: true` 且 `cli_available: false` → 直接 fallback，不做任何 probe。
- 命令在 `commands_verified` 数组中 → 可以使用 CLI。
- 命令不在数组中 → 直接 fallback，不尝试执行。
- `status.json` 不存在或 schema 不匹配 → 按「CLI Probe」的非执行检测流程重新检测（禁止直接执行 obsidian 命令）。

## 官方 CLI 命令

```bash
obsidian property:set path="<file>" name=updated value="2026-04-29T23:12:40+08:00" type=datetime
obsidian property:remove path="<file>" name="<field>"
obsidian prepend path="<file>" content="..."
obsidian create path="<file>" template="<template>"
obsidian move path="<old-file>" to="<new-file>"
obsidian rename path="<file>" name="<new-name>"
```

以本机 `obsidian help <command>` 的参数名为准。

## 模板协同

使用 `create template=` 时，不建立新的 Obsidian-only 模板体系。优先复用：

- 插件根 `templates/modules-template/` 的各层 overview / prd 骨架（经 `module-init` 实例化）
- prd-writer 写作规范与项目 PRD 框架（`.claude/skills/prd-style/`）
- `modules/<basic>/<sub>/decisions/` 下决策记录的既有格式
- `projects/specs/plans/` 跨模块方案的命名和 frontmatter 规则（document-norms §1 §2）

## 安全边界

- `move` / `rename` 必须用户明确确认。
- 默认只处理单文件，不默认移动目录。
- 执行 `move` / `rename` 前后都应调用 `obsidian-link-audit` 检查 backlinks / unresolved。
- 不暴露 `delete`、`history:restore`、`sync:restore` 默认执行路径。
- 如果 Obsidian “Automatically update internal links” 未开启，执行前必须提醒用户并使用 `rg` 复核。

## 确认门槛

`move` / `rename` 执行前必须同时满足：

- 已展示影响面，包括待移动/重命名文件、backlinks 摘要、可能受影响的 wikilink。
- 用户消息中含明确触发词：`确认` / `OK` / `可以` / `yes` / `执行` / `按这个执行`。
- 模糊表态（如“看你”“差不多吧”“应该可以”）默认中止，重新展示影响面后再问。
- 自动化场景或无对话上下文中一律不执行 `move` / `rename`，记录 fallback 原因后退出。

## Fallback

- 属性更新：用 `Edit` 修改 frontmatter。
- 新建文档：用 `Write` 写模板内容。
- move/rename：用户确认后用本地文件移动，再用 `rg` 查找旧链接并逐项修复。

## Workframe Event

使用后按 `agent-protocols.md` 记录 `skill_used` 事件，至少包含：

```json
{
  "ts": "<ISO-8601>",
  "type": "skill_used",
  "skill": "obsidian-safe-write",
  "role": "<role>",
  "success": true,
  "operation": "property:set",
  "source": "<file>",
  "cli_available": true,
  "fallback_used": false
}
```
