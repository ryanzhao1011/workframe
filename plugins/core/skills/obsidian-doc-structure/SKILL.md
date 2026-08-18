---
name: obsidian-doc-structure
description: 用 Obsidian 官方 CLI 读取文档 outline、properties、property、tags、aliases、wordcount——比 Read 快且不把全文灌进上下文。CLI 不可用时 fallback 到 Read + Markdown/frontmatter 解析。
when_to_use: |
  只想先看某文档的章节结构 / frontmatter 字段 / 标签 / 字数，不需要读全文时；
  需求评估前快速理解已有文档结构、查文档归属、发布前做属性与完整性检查时。
  边界：要查引用关系 → obsidian-link-audit；要改 frontmatter → obsidian-safe-write。
user-invocable: true
allowed-tools: [Bash, Read, Grep]
---

# Obsidian Doc Structure

## 定位

本 skill 只读文档结构和属性，不写正文。它帮助 agent 在精读全文前先理解文档骨架，并检查 frontmatter / Properties 是否满足项目规范。

## 触发场景

- 需求评估前，先读取 `outline` 判断需要精读哪些章节。
- PRD / spec / plan / decision 发布前检查 `type`、`status`、`updated` 等属性完整性。
- 用户问“这份文档结构完整吗”“缺哪些属性”“大概多少字”。
- 项目配备的发布器（`<platform>-publish`）发布前读取 `status` 与该平台的同步状态字段。

## 何时不用

- 已经明确要读全文：直接 `Read`。
- 简单关键词搜索：直接 `rg`。
- 需要修改属性：调用 `obsidian-safe-write`。
- 需要检查坏链：调用 `obsidian-link-audit`。

## CLI Probe

优先读取 `.claude/workframe-state/obsidian-cli-status.json`（schema 权威定义见下方「status.json schema」）：

- `do_not_probe: true` → **永不重新 probe**（不设 TTL、不做过期自判），直接按 `cli_available` 决定走 CLI 还是 fallback；重新启用 CLI 的唯一入口 = 用户手动删除该文件后自然触发重新检测
- 缓存缺失 → 执行**非执行检测**（三步全程不运行任何 obsidian 命令）：
  1. 定位命令：`Get-Command obsidian`（Windows）/ `command -v obsidian`（POSIX）；不存在 → CLI 不可用
  2. GUI 启动器判定：命令所在目录存在 `Obsidian.exe` / `resources.pak` 等桌面程序特征文件 → 是 GUI 而非 CLI shim → CLI 不可用
  3. 判定不可用 → 写入 status.json（`cli_available: false`、`do_not_probe: true`、`reason` 注明检测依据）并走 fallback
- 仅当非执行检测确认存在独立 CLI shim 时，才允许执行 `obsidian version` / `obsidian vault info=path` 验证，并把验证通过的命令写入 `commands_verified`

> ⚠️ 未通过非执行检测时禁止执行任何 `obsidian` 命令——在「装了 GUI 但未启用 CLI」的机器上会误启动 Obsidian 窗口（官方行为：If Obsidian is not running, the first command you run launches Obsidian）。

失败时 fallback 到 `Read` + 简单解析。

### status.json schema（workframe.obsidian-cli-status.v1）

| 字段 | 类型 | 说明 |
|---|---|---|
| `__schema__` | string | 固定 `workframe.obsidian-cli-status.v1` |
| `checked_at` | ISO-8601 | 最近一次检测时间 |
| `cli_available` | bool | CLI 是否可用 |
| `commands_verified` | string[] | 已实际验证可用的命令名 |
| `do_not_probe` | bool | `true` = 永不重新 probe；重置方式 = 删除本文件 |
| `reason` / `note` | string | 检测依据与人读说明（可选） |

其余 3 个 obsidian-* skill 引用本表，不重复定义。

## 调用前检查

执行任一 Obsidian CLI 命令前，必须依序确认：

- `status.json` 存在且 `do_not_probe: true` 且 `cli_available: false` → 直接 fallback，不做任何 probe。
- 命令在 `commands_verified` 数组中 → 可以使用 CLI。
- 命令不在数组中 → 直接 fallback，不尝试执行。
- `status.json` 不存在或 schema 不匹配 → 按「CLI Probe」的非执行检测流程重新检测（禁止直接执行 obsidian 命令）。

## 官方 CLI 命令

```bash
obsidian outline path="<file>" format=json
obsidian properties path="<file>" format=json
obsidian property:read path="<file>" name=status
obsidian tags path="<file>" format=json
obsidian aliases path="<file>" format=json
obsidian wordcount path="<file>" format=json
```

以本机 `obsidian help <command>` 的参数名为准。

## Fallback

- frontmatter：读取文件开头 `---` 块。
- outline：解析 Markdown 标题 `^#{1,6}\s+`。
- tags：解析 frontmatter `tags` 和正文 `#tag`。
- wordcount：用本地文本粗略统计，不作为精确指标。

## 输出要求

- 先给出文档属性摘要：`type` / `module` / `status` / `owner_role` / `updated`（项目若有平台同步字段，一并列出）。
- 再给 outline 摘要。
- 对发布前检查，明确是否允许发布：`draft` / `deprecated` 默认不发布，需用户确认。
- 不修改文档。

## Workframe Event

使用后按 `agent-protocols.md` 记录 `skill_used` 事件，至少包含：

```json
{
  "ts": "<ISO-8601>",
  "type": "skill_used",
  "skill": "obsidian-doc-structure",
  "role": "<role>",
  "success": true,
  "source": "<file>",
  "cli_available": true,
  "fallback_used": false
}
```
