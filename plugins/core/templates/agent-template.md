---
name: {{ROLE_NAME}}
description: |
  {{ROLE_ONE_LINER}}。{{ROLE_MAIN_RESPONSIBILITIES}}。
  触发场景：{{ROLE_TRIGGERS}}。
  {{ROLE_KEY_CONSTRAINT}}。
tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - AskUserQuestion
  # 若需联网：加 WebSearch / WebFetch
# model 字段默认 inherit（继承主会话模型），不硬编码具体 model ID
# 如需锁定模型只用别名：`model: opus` / `sonnet` / `haiku`（不要写完整 model ID——随模型换代失效）
# 不写 memory 字段：角色记忆由 SubagentStart hook 注入（agent-protocols §1），不走 CC 官方 memory frontmatter
skills:
{{ROLE_SKILLS_LIST}}
---

# {{ROLE_DISPLAY_NAME}} @{{ROLE_NAME}}

> 启动协议、协作边界、通用收尾协议（Step 0-3 通用骨架）见 workframe core rule: `agent-protocols`（项目内同步路径 `.claude/rules/workframe/core/agent-protocols.md`）。本文件只定义 @{{ROLE_NAME}} 的角色特质。

## 角色定位

{{ROLE_POSITIONING}}

## 核心职责

{{ROLE_RESPONSIBILITIES_DETAIL}}

> 用业务术语描述职责，**不在此处引用具体业务 skill 名**——frontmatter `skills:` 列表 + 各 skill 的 description 字段会让 Claude 在 subagent 内自动匹配最合适的 skill 调用。

## 特有写入边界（可选）

{{ROLE_WRITE_BOUNDARY}}

> 仅写本角色独有的写入路径约束。受保护资产的全局清单见 `auto-update.md` §受保护资产约束，不在此重复。

## 特有约束

{{ROLE_CONSTRAINTS}}

> 协作边界（不派发其他 agent）、响应正文优先于文件写入、shared memory 启动读取契约等已由 `agent-protocols.md` / `response-output.md` 定义，不在此重复。

## Step 3 扩展 — {{ROLE_DISPLAY_NAME}} 任务流转

通用 Step 3 规则（更新已有任务 status、不修改 summary 段、跳过非看板临时工作等）见 `agent-protocols.md`。@{{ROLE_NAME}} 特有：

{{ROLE_BOARD_UPDATE_RULE}}
