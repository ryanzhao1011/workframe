---
name: "{{SKILL_NAME}}"
# description 一次装下四类：①是什么 ②触发场景 ③典型触发词 ④不用于·边界。
# 单行、单引号包裹（触发词里的半角双引号可直接写）；≤1024 字符。
# ④ 最易被当冗余删掉，删了 skill 之间会互相抢路由。详见 reference/skill-customization-guide.md
description: '{{SKILL_DESCRIPTION}}'
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# {{SKILL_DISPLAY_NAME}} 技能

## 定位

{{SKILL_POSITIONING}}

**适用场景**：
{{SKILL_WHEN_TO_USE}}

**不适用场景**：
{{SKILL_WHEN_NOT_TO_USE}}

## 输入

调用本 skill 时期望的上下文或用户输入：
{{SKILL_INPUTS}}

## 工作流

{{SKILL_WORKFLOW_STEPS}}

## 输出

skill 完成后向用户呈现的内容：
{{SKILL_OUTPUTS}}

## 质量自检

完成前自检清单：
{{SKILL_QUALITY_CHECKLIST}}

## 与其他 skill 的衔接

{{SKILL_COLLABORATION}}
