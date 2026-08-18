# J4 librarian 开场卡消费

- 前置：J3a 会话已出卡（拿到 session_id）；积压含四类靶子（L1/L2/不达标/跨角色）
- 动作：`claude -p --resume <session_id> "全部处理" --permission-mode acceptEdits`
- 断言（内容判断层）：
  1. notes `###` 条目被消费；不达标靶子（转述未确认）留缓冲并附评估注记
  2. L1 靶子提升到对应 role/MEMORY，格式与存量条目一致（含「（日期 从 notes 提升）」注记）
  3. 跨角色靶子（≥2 角色踩过）融合进 shared/MEMORY，注明来源角色。**2026-08-16 增三条**：
     ① 单条压缩到 **≤200 字符**（shared 是各 subagent 启动时全量注入，每加一句此后每次
     委派都要付一次 token）；② **shared 变更在整理日志中单列**（与 role 层变更分开，标明
     本轮 shared 新增/修改 N 条 + 逐条一行摘要）供事后复核，**不逐条弹确认**（已有
     「≥2 消费域 + D/U/R/A ≥2 + ≤200 字符」三道门槛）；③ 计数**不含主 Claude**——它恒为
     潜在读者，算进去则每条工种知识都自动凑够两个消费者，shared 会被稀释
  4. L2 靶子（涉及 skills/rules）**不当场执行**，攒卡 promotion-candidates.md
  5. 已处理条目原文留痕 notes-archive.md（归档制，非直接删除）
- 断言（headless 形态）：
  6. workframe-state 写入被拦时，模型**如实报告被拦清单**（sidecar/events/L2 卡），
     不静默丢失、不绕过——fail-safe 方向正确
- 实测 2026-08-07：内容判断 5/5 全对；断言 6 成立（模型列了 3 项待补清单）。
  **模型自证 F1**：汇报「巡检数出 6 条但实际 7 条——pm 有 2 条列表格式条目未被计数」。
  注意：此形态下 Skill 工具被拒 → librarian SKILL 未加载 → 快照/运行日志两步缺失
  （对照 J5：工单写明 Read SKILL.md 兜底后此两步齐全）——复跑时若走交互会话不复现。
