# Onboarding（可选配置引导）

> Workframe 提供一个**可选**的配置引导命令 `/core:onboard`，用来呈现一些 Claude Code 实验功能的开关。**workframe 本身不依赖任何此类配置**——什么都不开，所有 agents / skills / rules / hooks 仍正常工作。

## TL;DR

- 项目接入 workframe 后，SessionStart hook 会在会话启动时打印一行温和提示：
  > `[workframe] 可运行 /core:onboard 查看可选配置；选择跳过后将不再提示。`
- 跑一次 `/core:onboard`（无论选择启用还是跳过）后，SessionStart 提示永久关闭。
- 不想跑也没关系，不影响 plugin 任何功能。

## 当前可选项

### Agent Teams（实验功能）

`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`

- **是什么**：Claude Code 的实验功能，允许多个独立 Claude Code 实例作为团队协作（共享任务列表 + 队友间通信）。具体交互形态随 Claude Code 版本演进，以 [官方文档](https://code.claude.com/docs/zh-CN/agent-teams) 为准。
- **workframe 是否依赖**：否。workframe 多角色协作（@pm / @dev / @qa / @prompt-eng）建立在 subagent 之上，flag 关闭时不影响功能。
- **版本要求**：Claude Code v2.1.32+
- **令牌成本**：启用后多 Claude Code 实例并行会显著增加令牌消耗，仅推荐有明确并行协作需求时启用。

**为什么默认 skip**：workframe 的多角色协作建立在 subagent 之上，与 Agent Teams 没有依赖关系——不开也不缺任何功能；而开启后多个 Claude Code 实例并行会显著抬高令牌消耗。既然「不开无损失、开了有成本」，默认就该是 skip，由你在确有并行协作需求时主动开。这也是 `/core:onboard` 全程零默认写入的原因：所有条目都要你当面确认才落盘。

## 通过 `/core:onboard` 启用

在已订阅 `core@workframe` 的项目里，对 Claude Code 输入：

```
/core:onboard
```

流程：

1. 检查 Claude Code 版本（< 2.1.32 自动跳过 Agent Teams 项）
2. 检查环境变量是否已被你手动设置过（已设置则跳过引导）
3. 询问写入范围：
   1. **最小范围启用**：`.claude/settings.local.json`（仅本项目本机，不进 git）
   2. **当前用户全局**：`~/.claude/settings.json`（影响所有项目，需二次确认）
   3. **团队共享**：`.claude/settings.json`（进 git，需团队同意，需二次确认）
   4. **跳过（默认）**
4. 选项 1 写入前会检查 `.gitignore` 是否覆盖 `.claude/settings.local.json`，未覆盖会询问是否补
5. 写入前自动备份目标文件为 `<file>.bak.<unix-ts>`
6. 写入采用 JSON merge，**保留所有原有字段**
7. 写入完成后写 `.claude/workframe-state/onboarded.json`，SessionStart 提示静默
8. 提示重启 Claude Code 会话使 env 配置生效

写入失败（权限不足 / JSON 解析错误等）会输出手动补丁让你自己加，**不会留下半成品**。

## 完全手动启用（不通过 onboard）

如果你不想跑 `/core:onboard`，可以直接编辑 settings 文件。

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
if (-not $j.env) { $j | Add-Member -MemberType NoteProperty -Name env -Value (@{}) }
$j.env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS = "1"
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

### 重新触发 onboard 引导

删除 `.claude/workframe-state/onboarded.json`，下次 SessionStart 会重新提示。或直接跑 `/core:onboard`，它会询问是否重走全流程。

## 与其他 workframe 命令的关系

| 命令 | 关系 |
|---|---|
| `/workframe-launcher:setup` | 初始化不联动 onboard；接入后由 SessionStart 提示引路 |
| `/core:audit` | 不读 `onboarded.json`，不影响 onboarding 状态 |
| `/core:maintenance-review` | 与 onboarding 无关 |

## 故障排查

| 现象 | 处理 |
|---|---|
| SessionStart 一直打印 onboarding 提示 | 跑一次 `/core:onboard`（即使全部选 skip 也会写 `onboarded.json` 终止提示） |
| `/core:onboard` 找不到 `recommended-env.json` | plugin 安装可能不完整，重新执行 `claude plugin install core@workframe` 后重启会话 |
| 写入 settings 后没生效 | 重启 Claude Code 会话；或检查 settings.json 是否被多个层级（项目 / 用户 / settings.local）相互覆盖 |
| 想看 onboard 当时做了什么决策 | Read `.claude/workframe-state/onboarded.json` 查看 `items[].status` |

## 相关文档

- 受保护资产例外：[`plugins/core/rules/core/auto-update.md`](../plugins/core/rules/core/auto-update.md) §"受保护资产例外"
- onboard skill 完整规范：[`plugins/core/skills/onboard/SKILL.md`](../plugins/core/skills/onboard/SKILL.md)
