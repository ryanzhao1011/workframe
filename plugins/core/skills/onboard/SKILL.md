---
name: onboard
description: workframe 一次性可选配置引导。读取 recommended-env.json，逐项询问是否启用（默认 skip），用户授权后写 settings.json 并标记 onboarded。由用户显式调用；未 onboarded 的项目由 SessionStart hook 提示一行
user-invocable: true
disable-model-invocation: true
allowed-tools: [Read, Write, Edit, Glob, Bash, AskUserQuestion]
---

# /core:onboard 一次性可选配置引导

## 用途

由用户显式 `/core:onboard` 触发。**Claude 不会自动调用**（`disable-model-invocation: true`）。

以**保守默认 + 显式 opt-in + local 优先 + 默认 skip + onboard 统一写入状态**原则，向用户呈现 workframe 推荐但不强求的可选环境配置（当前仅 1 项：Claude Code Agent Teams flag），完成后写入 `.claude/workframe-state/onboarded.json` 标记。

完成此 skill 后，SessionStart hook 不再打印 onboarding 提示。

## 设计原则（不可妥协）

- **plugin 不依赖任何被引导的可选 flag**——所有可选项默认 skip 后 plugin 仍正常工作
- **所有写入必须用户当面授权**——SKILL.md 不允许"为了流程顺滑"擅自写任何文件
- **写 settings.json / settings.local.json / ~/.claude/settings.json 必须**：备份 + JSON merge 保留原字段 + 失败兜底输出手动补丁
- **写 .gitignore 必须先询问**——动 git 行为相关文件需用户当面同意
- **skip 也是有效决策**——同样写 onboarded.json 让 hook 静默

## 输入

参数（自由文本）：

- 无参数 → 标准 onboarding 流程
- `--upgrade` → 已 onboarded 但 onboarding version 落后时的增量补齐（预留接口，落地版本待定）

## 前置检查

1. **是否已 onboarded**：Read `.claude/workframe-state/onboarded.json`
   - 文件存在且 `version` == 当前 SKILL.md 中 `ONBOARDING_VERSION` 常量 → 用 AskUserQuestion 问"已完成 onboarding，是否重走全流程"，拒绝则结束并打印当前 onboarded.json 摘要
   - 文件不存在 → 进入正式流程
   - `version` 落后 → 仅处理新增项（不动已有 status 为 enabled_* 的旧项）

2. **找到 recommended-env.json**：
   - 优先：Read `.claude/workframe-state/plugin-root.txt` 取插件根（SessionStart hook 每会话刷新）→ 拼接 `<插件根>/recommended-env.json`
   - 回退：`Glob **/plugins/core/recommended-env.json`（plugin-root.txt 缺失的开发场景）
   - 找不到 → 终止 skill，输出"plugin 安装可能不完整：core 插件根下缺 recommended-env.json"

3. Read recommended-env.json，按 `recommended_env_vars` 数组逐项处理（Step 1-7）。

> **ONBOARDING_VERSION 常量**：当前 `0.2.1`。每次新增 recommended_env_var 项时 bump，配合 `--upgrade` 增量逻辑。

## 单项 env 处理流程

对 `recommended_env_vars` 中每一项执行 Step 1-7。本 SKILL.md 的所有"该项"指代当前正在处理的 env 项。

### Step 1 — 版本检查

```bash
claude --version
```

解析输出形如 `2.1.32 (Claude Code)` 的版本号。对比该项 `min_cli_version`：

- 当前 < min_cli_version → 打印"当前 Claude Code 版本 X.Y.Z < {min_cli_version}（{env_name} 要求版本），跳过此项"，记录 status=`skipped_version`，跳到下一项
- 解析失败 → 打印 warning，按"无法判断版本"处理，记录 status=`skipped_version_check_failed`，跳到下一项

### Step 2 — 已配置检查

检查环境变量是否已被用户在系统层面设置：

PowerShell:
```powershell
[Environment]::GetEnvironmentVariable('{env_name}','User')
[Environment]::GetEnvironmentVariable('{env_name}','Machine')
```

Bash:
```bash
echo "${{env_name}}"
```

任一返回非空 → 打印"该 env 已在你的环境中设置，跳过引导"，记录 status=`skipped_already_set`，跳到下一项。

### Step 3 — 交互询问（默认 skip）

使用 AskUserQuestion 呈现该项。提问文案模板（中文）：

```
{description_zh}
官方文档：{official_doc}

写入范围（默认跳过）：
  1) {scopes[0].label_zh}：{scopes[0].path}（{scopes[0].note_zh}）
  2) {scopes[1].label_zh}：{scopes[1].path}（{scopes[1].note_zh}，需二次确认）
  3) {scopes[2].label_zh}：{scopes[2].path}（{scopes[2].note_zh}，需二次确认）
  4) 跳过
```

AskUserQuestion options 仅 4 个，不预选任何项，用户必须明确选择。

### Step 4 — 二次确认（仅当 scope.needs_confirm == true）

若用户选 user 或 team scope，再用 AskUserQuestion 问一次：

```
你选择写入 {scope.path}。
影响范围：{scope.note_zh}
确认继续？
  1) 是，继续写入
  2) 否，重新选择
  3) 跳过此项
```

选 2 → 回到 Step 3
选 3 → status=`skipped`，跳到下一项

### Step 5 — gitignore 检查（仅 scope.gitignore_check == true）

```bash
cd "<project root>" && git check-ignore -q "{scope.path}" && echo IGNORED || echo TRACKED
```

注意：项目可能不是 git repo，`git check-ignore` 会报错——按 TRACKED 处理（视为不安全）。

返回 TRACKED → AskUserQuestion 询问：

```
检测到 .gitignore 未覆盖 {scope.path}。
该文件含个人本地配置，进 git 不安全。

  1) 追加 "{scope.path}" 到 .gitignore（推荐）
  2) 跳过追加（我会自行处理）
  3) 取消此项写入
```

- 选 1 → Edit `.gitignore`（不存在则 Write 创建空文件后 Edit），尾部追加：
  ```
  # Claude Code local settings（不进 git）
  {scope.path}
  ```
  追加前 Read `.gitignore` 检查是否已含该规则（含子串匹配），含则跳过追加避免重复
- 选 2 → 继续，但在最终输出 §"完成总结" 中 warn"`{scope.path}` 未被 gitignore 覆盖，请手动处理"
- 选 3 → status=`skipped`，跳到下一项

### Step 6 — 备份 + JSON merge + 写入

1. **备份**：目标文件存在 → Bash 执行：
   ```bash
   cp "{target_path}" "{target_path}.bak.$(date +%s)"
   ```
   Windows 环境用 PowerShell：
   ```powershell
   Copy-Item "{target_path}" "{target_path}.bak.$([int][double]::Parse((Get-Date -UFormat %s)))"
   ```
   **只有「目标不存在」才算正常**，可继续；备份因权限 / 磁盘 / 路径类型失败时**中止该项写入**，
   改为输出手动补丁让用户自己合并——settings 是受保护资产，没有退路就不该动它。

2. **读现有 settings**：Read 目标路径
   - 文件不存在 → 视为 `{}`（空对象）
   - JSON 解析失败 → 终止此项，输出手动补丁（见 Step 6.5），记录 status=`write_failed_manual_required`

3. **JSON merge**（保留所有原字段）：
   - 读到的 JSON 对象记为 `existing`
   - 新建合并对象 `merged = deep_copy(existing)`
   - `merged.env = merged.env or {}`
   - `merged.env[env_name] = value`
   - **不动** `merged` 的其他顶层字段（permissions / enabledPlugins / language / ... 全保留）
   - **不动** `merged.env` 的其他键

4. **写入**：Write 目标路径，content 为格式化后的 JSON（2 空格缩进）

5. **失败兜底**（任何写入异常）：
   ```
   写入失败：{error_message}

   请手动将以下内容合并到 {target_path}：

   {
     "env": {
       "{env_name}": "{value}"
     }
   }
   ```
   记录 status=`write_failed_manual_required`，跳到下一项

### Step 7 — 单项收尾

成功写入 → 记录该项 status=`enabled_<scope_id>`（如 `enabled_local`），及 `settings_path={target_path}`
跳过 → status=`skipped`
版本不够 → status=`skipped_version`
已配置 → status=`skipped_already_set`
写失败 → status=`write_failed_manual_required`

## 全部 env 处理完后

### Step A — 写 onboarded.json

Write `.claude/workframe-state/onboarded.json`：

```json
{
  "version": "<ONBOARDING_VERSION>",
  "onboarded_at": "<ISO-8601 当前时间>",
  "items": [
    {
      "id": "<env_var.id>",
      "status": "<enabled_local | enabled_user | enabled_team | skipped | skipped_version | skipped_version_check_failed | skipped_already_set | write_failed_manual_required>",
      "scope": "<local | user | team | null>",
      "settings_path": "<目标路径 | null>"
    }
  ]
}
```

确保 `.claude/workframe-state/` 目录存在（Write 工具会自动创建父目录）。

### Step B — 输出完成总结

```markdown
## ✅ workframe onboarding 完成

| 项 | 结果 |
|---|---|
| {env_var.id} | {根据 status 文案，例：✅ 已写入 .claude/settings.local.json / ⚠️ 写入失败，请手动处理 / ⏭️ 已跳过} |

{若有任意 enabled_* 项}
> 请重启 Claude Code 会话使 env 配置生效。

{若有 write_failed_manual_required 项}
> 写入失败的项请按上方提示手动添加到目标 settings 文件。

{若 Step 5 选择 2 跳过 gitignore 追加}
> ⚠️ `.claude/settings.local.json` 未被 .gitignore 覆盖，请自行处理避免误提交。

后续 SessionStart 提示已关闭，不再打扰。
可随时重新运行 `/core:onboard` 调整选择。
```

### Step C — Wrap-up（参考 agent-protocols.md Step 1）

向 `.claude/workframe-state/events.jsonl` append 一行（文件不存在则创建）：

```json
{"ts":"<ISO-8601>","type":"skill_used","skill":"onboard","role":"main","success":true}
```

`success` 取值：

- 至少完成一项的 Step 1-7（无论 enabled / skipped / 写失败）→ true
- 前置检查阶段失败（找不到 recommended-env.json / 用户中途彻底退出且未完成任何项）→ false

## 受保护资产例外

core rule `auto-update` 把 `.claude/settings*.json` 列为受保护资产。**`/core:onboard` 是该规则的唯一豁免入口**，前提：

- 写入前必须用户当面交互式确认（Step 3 / Step 4）
- 写入必须备份 + JSON merge + 失败兜底（Step 6）
- local scope 必须先做 gitignore 检查并询问补全（Step 5）

不满足上述前提时，本 skill 自身**不写入**，输出手动补丁让用户处理。

## 与其他 skill / hook 协作

| 触点 | 协作 |
|---|---|
| `session-start-prep.py` hook | 检测 `.claude/workframe-state/onboarded.json` 不存在时打印一行温和提示，零写入副作用 |
| `recommended-env.json` | 数据源；本 skill 唯一消费者 |

## 相关文档

- 用户向文档：框架仓用户文档的 Onboarding 篇（在框架仓库 docs 目录下，不随插件分发）
- 受保护资产规则：core rule [`auto-update`](../../rules/core/auto-update.md)
