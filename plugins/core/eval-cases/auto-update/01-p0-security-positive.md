# auto-update eval · 01 P0 security triggers SEC issue + board task

**类型**：正例

## 输入
用户说："新接口在未授权场景下能直接返回用户列表，这是权限绕过。"

## 期望行为
- 命中 `P0-1 安全问题` 触发词（"权限绕过"）
- 非排除场景（陈述新事实）
- 先**回显摘要**等用户确认
- 确认后：
  - 新建 `projects/issues/SEC-<序号>.yaml`
  - board.yaml 追加 P0 任务：`assigned_to: dev, tags: [auto-update, P0, security, needs-qa-regression]`