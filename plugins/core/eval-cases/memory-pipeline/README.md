# memory-pipeline 端到端用例集

记忆与数据管理主链路的全链路真实验证资产。首跑 2026-08-07（沙盒 wf-g7-sandbox，
真实 plugin 订阅 + 真实 `claude -p` 会话），首跑当时的 13 例全部通过；开源后兼作回归集。
现共 14 例——X4 随 main-led 改造于 2026-08-16 新增，尚未跑过真实会话，复跑时一并覆盖。

## 验收判据（四项，全部机器可查）

1. **文件级证据核验**——不信模型口头汇报，每条用例断言落到文件/字段
2. **事件流 schema 合规**——events.jsonl 可解析率 100%（doctor `events_parse`）
3. **doctor 全绿**——8 项检查 0 非绿（`workframe_doctor.py --json`）
4. **三账对齐**——sidecar↔MEMORY↔events 四向互查（`scripts/assert_three_ledgers.py`）

## 沙盒重建步骤（复跑前置）

1. `python <core>/scripts/project_scaffold.py --project <沙盒绝对路径> --create-missing`（scaffold）
2. 种子数据：从一个已有 workframe 项目复制 `agent-memory/*/{MEMORY,notes}.md`、
   `workframe-state/{memory-index.json,events.jsonl(tail -200)}`、`projects/board.yaml`、
   `projects/proposals/applied/*.yaml`；`activity-state.json` 重置 `session_counter: 10`。
   **复制前先脱敏**——记忆正文与 events 常年积着业务细节（客户名、未公开数据、内部口径），
   沙盒本身是一次性的，但顺手提交一次就永久进了 git 历史
3. 人造积压：给 2~3 个角色 notes.md 追加共 6 条 `### ` 条目，必须包含四类靶子——
   L1 可提升（亲测事实）/ L2 审批（建议改 skills）/ 不达标（转述未确认）/ 跨角色（≥2 角色踩过）
4. 边界脏数据用 `fixtures/` 三件（非法 provenance / 超预算 auto-memory 条目 / events 坏行）

## 用例总表

| # | 验什么 | 形态 | 文件 |
|---|---|---|---|
| J1 | 纠正写入分流（角色 supersede / 主 Claude 层双消费） | 真实会话 | cases/J1-correction-write.md |
| J2 | 写时 A.U.N. 同主题查重 | 真实会话（与 J1 合并） | cases/J2-aun-write.md |
| J3 | 开场卡闸门矩阵（首跑 8 断言；F4b 修复后含入口闸三态） | 脚本直调 + 真实 SessionStart | cases/J3-ask-gates.md |
| J4 | librarian 开场卡消费 | 真实会话 --resume | cases/J4-librarian-consume.md |
| J5 | maintenance 两阶段（judge → --commit） | 真实 --maintenance 会话 + 代码 | cases/J5-maintenance-two-phase.md |
| J6 | doctor 造脏/撤脏双向 | 脚本 | cases/J6-doctor-dirty.md |
| J7 | 自迭代 baseline 代码派生 | 真实 SessionStart | cases/J7-baseline-derive.md |
| J8 | subagent 记忆注入三态 | 真实会话双探针 | cases/J8-subagent-inject.md |
| J9 | 跨会话状态流转 | 多会话累积 + 读账 | cases/J9-cross-session.md |
| J10 | 三账对齐终查 | 脚本 | cases/J10-three-ledgers.md |
| X1 | 同消息命中多 rule 不重复记账 | 真实会话（与 J1 合并） | cases/X1-multi-rule.md |
| X2 | maintenance flag 撞车双向 | 脚本直调 | cases/X2-flag-collision.md |
| X3 | dormant 全链静默 | 脚本直调 | cases/X3-dormant-silence.md |
| X4 | step2 与 step5 同事实不双写（main-led 直做） | 真实会话 | cases/X4-step2-step5-no-double-write.md |

人工目检（不可机器断言，单列）：M1 开场卡 AskUserQuestion 交互渲染样式；
M2 judge 会话 acceptEdits 真实交互表现。宿主项目日常会话顺带看一眼即可。

## headless 形态的三条固有约束（跑用例前必读）

复跑断言时区分「链路缺陷」与「headless 伪影」，以下三条属后者（交互会话中不存在）：

1. `.claude/workframe-state/**` 敏感文件闸硬拒（Edit 工具层 denied）——模型正确行为是
   停下报告 + 留幂等补账脚本，断言应检查「报告了被拦项」而非「写入成功」
2. Skill 工具不注入正文——依赖 SKILL.md 的用例须在 prompt/工单里写明 Read 兜底路径
3. hook 脚本直调必须喂 stdin payload（如 `echo '{"source":"startup"}' | python memory-ask.py`），
   否则 `json.load(sys.stdin)` 阻塞挂起

## 首跑发现（2026-08-07，同日全部修复并复验）

F1 notes 计数口径漏列表格式条目（J4 模型自证；修复=两口径取 max）/ F3 doctor 对
零 commit git 仓库提示失真（修复=stderr 三分）/ F4b `-p` 会话撞开场卡（修复=
ENTRYPOINT 入口闸 + 文案 Read 兜底加固）/ F5 纠正落 skill 时 sidecar 记账规格
空洞（修复=skill/rule 落点不写 sidecar、事件省 entry_key）/ F6 注入段说明恒写
「两份」（修复=按实际份数拼装）。详情见框架 CHANGELOG 对应日期条目。
