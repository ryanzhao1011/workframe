# X1 同消息命中多 rule 不重复记账

- 动作：一条用户消息同时含①跨模块工种知识纠正（J1a 当前题面）+ ②通用参考新事实陈述（J2 当前题面），一次会话送达
- 断言：
  1. step 1（correction-detection）消费①：`user_correction` 事件**仅 1 条**
  2. step 2（auto-update）只处理②，不为①重复写 MEMORY/事件（写前自检「step 1 已消费」）
  3. 两个落点互不污染：①进 MEMORY 高置信区 + supersede，②进 notes 缓冲
- 实测 2026-08-07：✅ 全过。模型响应结构清晰分列「① 纠正（correction-detection 流程）/
  ② CSV 新格式（auto-update P2 即时归档）」，事件准备清单中 user_correction 仅 1 条。

这条带日期的实测是旧版题面记录；当前题面随 J1a/J2 改为工种知识与通用参考后，待真实会话复跑。
