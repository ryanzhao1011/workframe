# auto-update eval · 02 P1 技术决策落 decisions/ ADR + 记忆留指针

**类型**：正例

## 输入
用户说："后端这次选 PostgreSQL，因为需要强事务 + JSONB 字段。"

## 期望行为
- 命中 P1「技术决策（架构选型 / 数据库 / API 设计 / 依赖选择）」
- 判定为陈述新事实（非提问 / 非假设讨论），**P1 即时更新**，不需要二次确认
- 先过归属分流：这是团队级技术共识，**决策本身落文档**——单模块进
  `projects/modules/<basic>/<sub>/decisions/`，跨模块进 `projects/specs/plans/`
- `.claude/agent-memory/dev/MEMORY.md` **只留一行指针**，不复制决策全文
  （文档能承载的不进记忆；判据见 `agent-protocols.md` Step 2）
- 如需实施：**响应中输出 board task 草稿**，由 `task-management` / 主 Claude 落盘，
  不由本 rule 直接写 board.yaml

## 反例（不应出现）
- 把决策全文直接塞进 `dev/MEMORY.md` 而不落文档——那样其他角色查这个模块时看不到
- 直接追加 board.yaml 任务条目（P0 安全 / 线上故障才有直写豁免）
