# J3 开场卡闸门矩阵

> 首跑时为四闸；G7 F4b 修复后新增闸 0（交互入口：`CLAUDE_CODE_ENTRYPOINT` 为
> sdk-* 时静默，cli 与未知值照常出卡）。复跑断言含闸 0 三态（修复轮已实测 3/3 +
> 真实 `-p` 端到端静默 1 次）。

工具：`echo '{"source":"startup"}' | python memory-ask.py`（必须喂 stdin，见 README 约束 3），
`CLAUDE_PROJECT_DIR` 指向沙盒。断言 = stdout 是否含 `initialUserMessage`。

## J3a 真实端到端（唯一必须走真实会话的分支）

- 前置：notes 积压 ≥5 条 ###、无 ask-state、counter 正常
- 动作：沙盒 `claude -p "<任意正事 prompt>"`
- 断言：开场卡以**真用户消息**注入且模型先响应它（transcript 含积压数与三选项）；
  同时回答了原始 prompt；`memory-ask-state.json` 写入 `last_asked_date`
- 实测 2026-08-07：✅。headless 无 AskUserQuestion 时按 response-output rule 正确退化为
  文字编号选项（fallback 链路顺带验证）

## 脚本级矩阵（7 断言）

| 分支 | 前置 | 预期 | 实测 |
|---|---|---|---|
| backlog ≥5 | 积压 6 条、无 state | 出卡 | ✅ |
| backlog <5 | 积压 1 条 | 静默 | ✅ |
| 当日已问 | `last_asked_date=今天` | 静默 | ✅ |
| 冷却中 | `--record-refusal` 后（`last_refused_session` = **记录当时的 counter**，见 memory-ask.py `record_refusal`） | 静默 | ✅ |
| 冷却期满 | `counter - refused_session ≥ 5` | 出卡 | ✅ |
| source=resume | stdin `{"source":"resume"}` | 静默 | ✅ |
| refusal 记账 | `--record-refusal` | state 写 last_refused_session/at | ✅ |

## 现场管理教训（复跑必读）

临时造积压用 `cp notes.md notes.md.bak` 备份时，**命令中途失败重跑会把已污染文件
存成 .bak**（首跑实证：断言假阳性一次）。备份动作必须幂等或从源项目重拷还原。
