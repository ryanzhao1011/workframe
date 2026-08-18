# 项目级 Eval Cases

> 由 `project_scaffold.py` 复制到 `<project>/projects/evals/` 下作为骨架。

## 目录结构

```
projects/evals/
├── rules/      # 自定义 rules 的 eval case（正例 ≥2 + 负例 ≥1）
├── skills/     # 自定义 skills 的 eval case（成功 ≥1 + 失败 ≥1）
└── agents/     # 自定义 agents 的路由 case（≥3 样例）
```

## 何时用

- `self-iteration` 生成 L2 提案变更 **core 文件**时，必须先补 eval cases 再执行
- 本项目自定义了 agent / skill / rule，建议写 eval case 保证变更可回归

## 文件格式

每个 eval case 是一份 Markdown 文件，含四段：
1. 类型（正例 / 负例 / 回归）
2. 输入状态（文件状态 + 用户消息）
3. 期望行为（具体可验证的动作）
4. 验证命令（如何断言通过/失败）

参考 core 插件内 `skills/librarian/eval-cases/` 下的 golden cases 写法。
