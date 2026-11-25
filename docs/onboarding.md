# 可选配置

> 这里列出 workframe 推荐知道、但**不需要**开启的 Claude Code 配置。**workframe 本身不依赖任何一项**——什么都不开，所有 agents / skills / hooks 与必载纪律注入照常工作。框架不代你写这些配置：要开就照下文自己改 settings 文件。

## TL;DR

- 默认什么都不用开；会话启动时也不会提示。
- 想开某一项时，按「手动启用」一节改对应的 settings 文件，然后重启 Claude Code 会话。
- 以前跑过配置引导留下的 settings 与 `.workframe/state/onboarded.json` 原样保留，框架不再读写它们。

## 当前可选项

### Agent Teams（实验功能）

`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`

- **是什么**：Claude Code 的实验功能，允许多个独立 Claude Code 实例作为团队协作（共享任务列表 + 队友间通信）。具体交互形态随 Claude Code 版本演进，以 [官方文档](https://code.claude.com/docs/zh-CN/agent-teams) 为准。
- **workframe 是否依赖**：否。workframe 的角色（@pm / @dev / @qa / @prompt-eng）缺省由主会话直接承担，只在独立验证等少数情形派子 agent（见 [concepts.md](./concepts.md) §角色是领域，不是派出去的人），派出去的也是普通 subagent，flag 关闭时不影响功能；续派某个子 agent 用 `SendMessage` 发给它的 agentId，同样不需要开它。
- **开启后的派发**：带 `name` 的派发会以队友形态启动，SubagentStart 拿到的 `agent_type` 变成那个名字、角色记忆投不到。所以 workframe 会拦下给出厂角色（`core:*`）起名的派发，提示去掉 `name` 重派；项目级角色起名不拦。这道闸认的是 Claude Code 2.1.63 起的工具名 `Agent`，更早的版本上不触发。
- **版本要求**：Claude Code v2.1.32+
- **令牌成本**：启用后多 Claude Code 实例并行会显著增加令牌消耗，仅推荐有明确并行协作需求时启用。

**为什么默认不开**：workframe 与 Agent Teams 没有依赖关系——角色的活缺省由主会话做，需要派出去时用的是普通 subagent，不开也不缺任何功能；而开启后多个 Claude Code 实例并行会显著抬高令牌消耗。既然「不开无损失、开了有成本」，默认就该是不开，由你在确有并行协作需求时主动开。

## 手动启用

直接编辑 settings 文件。三个范围任选其一；写之前先备份原文件，改完确认仍是合法 JSON。

### 选项 1：最小范围（项目本机）

编辑 `<project>/.claude/settings.local.json`，顶层加 `env` 字段：

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  },
  "permissions": { "...": "原有内容不动" }
}
```

确保 `<project>/.gitignore` 包含一行 `.claude/settings.local.json`。

### 选项 2：当前用户全局

编辑 `~/.claude/settings.json`，顶层加同样的 `env` 字段。

PowerShell 一键命令（不用编辑器）：

```powershell
$f = "$HOME\.claude\settings.json"
$j = if (Test-Path $f) { Get-Content $f -Raw | ConvertFrom-Json } else { @{} }
if (-not $j.env) { $j | Add-Member -MemberType NoteProperty -Name env -Value ([pscustomobject]@{}) }
$j.env | Add-Member -MemberType NoteProperty -Name CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS -Value "1" -Force
$j | ConvertTo-Json -Depth 20 | Set-Content $f -Encoding UTF8
```

macOS / Linux（需要 `jq`）：

```bash
f="$HOME/.claude/settings.json"
[ -f "$f" ] || echo '{}' > "$f"
tmp=$(mktemp)
jq '.env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS = "1"' "$f" > "$tmp" && mv "$tmp" "$f"
```

### 选项 3：团队共享

编辑 `<project>/.claude/settings.json`（不是 `settings.local.json`），同样加 `env` 字段。这个文件**会进 git**，确保团队成员有共识再这么做。

## 撤销 / 关闭

### 关闭 Agent Teams flag

编辑对应的 settings 文件，删除 `env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` 字段（或把值改为 `"0"`）。

PowerShell 一键删除（用户级）：

```powershell
$f = "$HOME\.claude\settings.json"
$j = Get-Content $f -Raw | ConvertFrom-Json
if ($j.env) { $j.env.PSObject.Properties.Remove("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS") }
$j | ConvertTo-Json -Depth 20 | Set-Content $f -Encoding UTF8
```

重启 Claude Code 会话生效。

## 故障排查

| 现象 | 处理 |
|---|---|
| 写入 settings 后没生效 | 重启 Claude Code 会话；或检查 settings.json 是否被多个层级（项目 / 用户 / settings.local）相互覆盖 |

## 相关文档

- settings 由谁、在什么前提下写：core 插件 `reference/settings-write-boundary.md`（框架自己只在装机链路里写订阅声明那几个键）
