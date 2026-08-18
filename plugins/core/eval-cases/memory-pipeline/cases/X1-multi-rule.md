# X1 同消息命中多 rule 不重复记账

- 动作：一条用户消息同时含①纠正信号（J1a）+ ②新事实陈述（J2），一次会话送达
- 断言：
  1. step 1（correction-detection）消费①：`user_correction` 事件**仅 1 条**
  2. step 2（auto-update）只处理②，不为①重复写 MEMORY/事件（写前自检「step 1 已消费」）
  3. 两个落点互不污染：①进 MEMORY 高置信区 + supersede，②进 notes 缓冲
- 实测 2026-08-07：✅ 全过。模型响应结构清晰分列「① 纠正（correction-detection 流程）/
  ② CSV 新格式（auto-update P2 即时归档）」，事件准备清单中 user_correction 仅 1 条。
