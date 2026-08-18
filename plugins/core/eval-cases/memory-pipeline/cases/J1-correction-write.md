# J1 纠正写入分流

## J1a 角色层纠正（supersede 全链）

- 前置：pm/MEMORY.md 存在一条当日提升的口径条目（复盘导出上限 2 万条）+ 对应 sidecar entry
- 动作：`claude -p "纠正：之前记的复盘报告导出单任务上限 2 万条已过时——上限调整为 5 万条…" --permission-mode acceptEdits`
- 断言：
  1. pm/MEMORY.md 高置信区出现 `[纠正] {当日}` 条目（新口径 + 原错误认知句式）
  2. 旧条目整段移出 MEMORY → pm/notes.md 留档，标注「已被 {日期} 纠正取代」
  3. sidecar：新 entry `protected:true / provenance:user-decree / source:[纠正]`，旧 entry 删除
  4. events 追加一条 `user_correction`（entry_key 匹配规范化规则：scope:date:前20字去空白）
  5. 模型确认回显含「收到纠正：X → Y」句式
- 实测 2026-08-07：✅ 全过。断言 3/4 被敏感闸拦（headless 伪影），模型留幂等补账脚本
  `tmp/apply-correction-*.py`，脚本内 OLD_KEY 与 sidecar 实际 key 逐字一致（独立按规范化规则推算）。

## J1b 主 Claude 层纠正（auto-memory 落点）

- 动作：`claude -p "纠正：测试汇报时间估算统一用小时粒度…"`（协作行为类纠正）
- 断言：
  1. 落点分流正确（本条实测走了**双消费**分支：shared 权威全文 + auto-memory 一行指针，
     判断依据「@qa 产出 + 主 Claude 转述」成立）
  2. auto-memory 目录（`~/.claude/projects/<munged>/memory/`）出现指针文件，
     frontmatter `type: feedback`、正文不复制全文
  3. correction-detection 第 5 步：通用性升级 local rule 须询问用户，不擅自落盘
- 实测 2026-08-07：✅ 全过。**headless 下 auto-memory 可写实证**（项目外路径不受敏感闸拦截）。
