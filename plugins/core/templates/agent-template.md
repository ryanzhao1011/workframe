---
name: "{{ROLE_NAME}}"
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
  - Skill                                # 别删：tools 是白名单，缺它该角色一个 skill 都调不了
  # 若需联网：加 WebSearch / WebFetch
# model 字段默认 inherit（继承主会话模型），不硬编码具体 model ID
# 如需锁定模型只用别名：`model: opus` / `sonnet` / `haiku`（不要写完整 model ID——随模型换代失效）
# 不写 memory 字段：角色记忆由 SubagentStart hook 注入（agent-protocols §1），不走 CC 官方 memory frontmatter
# skills 字段：**出厂 core agent 不写这一格**——什么条件下调哪个 skill 由各 skill 的 description
# 驱动。**你建的项目级角色不受这一条约束**：确需每次派发都预载的 skill 可以列在这里，
# 写法与代价见 role-customization-guide §Frontmatter
---

# {{ROLE_DISPLAY_NAME}} @{{ROLE_NAME}}

> 启动协议、协作边界、通用收尾协议由 `SubagentStart` 在本 agent 启动时**直接注入上下文**，不必也无处去读文件。本文件只定义 @{{ROLE_NAME}} 的角色特质。

## 角色定位

{{ROLE_POSITIONING}}

## 核心职责

{{ROLE_RESPONSIBILITIES_DETAIL}}

> 用业务术语描述职责，**不在此处写「什么条件下调哪个 skill」**——触发条件的唯一源是各 skill 自己的 description，这里另写一份就是第二个源，两者矛盾时模型按 description 判。正文提到 skill 名不禁止，但要写成「`<name>` skill」这类可识别形态。

## 特有写入边界（可选）

{{ROLE_WRITE_BOUNDARY}}

> 仅写本角色独有的写入路径约束。受保护资产的全局清单见必载片 §受保护资产清单，不在此重复。

## 特有约束

{{ROLE_CONSTRAINTS}}

> 协作边界（不派发其他 agent）、响应正文优先于文件写入、shared memory 启动读取契约等已由注入的必载片定义，不在此重复。

## Step 3 扩展 — {{ROLE_DISPLAY_NAME}} 任务流转

通用 Step 3 规则（更新已有任务 status、不修改 summary 段、跳过非看板临时工作等）见必载片 §Step 3 — 更新任务看板。@{{ROLE_NAME}} 特有：

{{ROLE_BOARD_UPDATE_RULE}}
