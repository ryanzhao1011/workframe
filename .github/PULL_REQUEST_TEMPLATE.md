## 改了什么

<!-- 一两句说明这个 PR 解决什么问题。如果关联 issue，写 Closes #123 -->

## 自检

- [ ] `python tools/validate.py` 全绿（本仓唯一的质量闸，不绿的 PR 无法合并）
- [ ] 改动涉及 `plugins/core/rules/` 时，已同步项目镜像 `.claude/rules/workframe/core/`
- [ ] 新增或修改了 validate 检查时，**已用它自己的失败方式打一遍**，确认它真的会红
- [ ] 改动涉及计数类表述（skills 数 / hook 段数 / 角色数）时，已确认有对账闸管着，没有手写数字

## 需要 bump 版本吗

- [ ] 不需要（纯文档 / 内部重构）
- [ ] 需要 —— 已锁步 bump：两份 `plugin.json` + `marketplace.json`（三处）+ README 状态行

<!--
放宽某道护栏之前请先读 CLAUDE.md：多数情况下说明护栏该重新瞄准，而不是删掉。
如果确实要放宽，请在上面「改了什么」里说明理由。
-->
