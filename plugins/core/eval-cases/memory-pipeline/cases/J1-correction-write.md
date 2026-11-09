# J1 纠正写入分流

## J1a 角色层纠正（supersede 全链）

- 前置：pm/MEMORY.md 存在一条当日提升的跨模块执行经验（PM 通用评审汇报只列主路径）+ 对应 sidecar entry
- 动作：`claude -p "纠正：以后的 PM 通用评审汇报都要同时列主路径与边界场景，之前只列主路径不对。这是跨模块的汇报要求…" --permission-mode acceptEdits`
- 断言：
  1. pm/MEMORY.md 高置信区出现 `[纠正] {当日}` 条目（新口径 + 原错误认知句式）
  2. 旧条目整段移出 MEMORY → pm/notes.md 留档，标注「已被 {日期} 纠正取代」
  3. sidecar：新 entry `protected:true / provenance:user-decree / source:[纠正]`，旧 entry 删除
  4. events 追加一条 `user_correction`（entry_key 匹配规范化规则：scope:date:前20字去空白）
  5. 模型确认回显含「收到纠正：X → Y」句式
- 实测 2026-08-07：✅ 全过。断言 3/4 被敏感闸拦（headless 伪影），模型留幂等补账脚本
  `tmp/apply-correction-*.py`，脚本内 OLD_KEY 与 sidecar 实际 key 逐字一致（独立按规范化规则推算）。

当前题面已改为跨模块工种知识，待真实会话复跑；上面的历史实测使用「复盘导出单任务上限由 2 万条改为 5 万条」题面，不证明新题面已过。产品定义类纠正按下面 J1c 验。

## J1b 主 Claude 层纠正（auto-memory 落点）

- 动作：`claude -p "纠正：测试汇报时间估算统一用小时粒度…"`（协作行为类纠正）
- 断言：
  1. 落点分流正确（本条实测走了**双消费**分支：shared 权威全文 + auto-memory 一行指针，
     判断依据「@qa 产出 + 主 Claude 转述」成立）
  2. auto-memory 目录（`~/.claude/projects/<munged>/memory/`）出现指针文件，
     frontmatter `type: feedback`、正文不复制全文
  3. correction-detection 第 5 步：通用性升级 local rule 须询问用户，不擅自落盘
- 实测 2026-08-07：✅ 全过。**headless 下 auto-memory 可写实证**（项目外路径不受敏感闸拦截）。

## J1c 模块业务纠正（权威文档与作业提醒）

- 前置：沙盒有 `catalog/export` 子模块、已确认 PRD 与模块共享文档；角色 MEMORY / sidecar 的工作区字节已存基线。
- 动作：用户明确纠正已记录的既有口径：「导出单任务上限应是 5 万条，PRD 中 2 万条是记错；这是既有约定的订正。」如需提醒，补一行「调整导出批次前 → 必读对应 PRD」。
- 断言：
  1. 改对应权威文档，PRD 在「变更与决策记录」留痕；若实际是新增需求范围，先出草稿确认，不借纠正绕过确认。
  2. 需要的提醒写 `module-memory.md`，只写何时做什么 / 读哪里，不复制 5 万条结论。
  3. 角色与 shared MEMORY 不新增业务纠正或指针，sidecar 字节与基线一致。
  4. 只写一条 `user_correction`，`scope=module`、`module=catalog/export`、`target_path` 为完整权威文档路径、`summary` 有事实快照，无 `entry_key`。
  5. 文档后续按版本演进，不能因 `[纠正]` 的记忆保护规则把产品口径锁死。
- 验证状态：新增用例，待真实会话复跑；文档分流、事件契约与模型使用效果分别核验。
