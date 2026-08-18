# X2 maintenance flag 撞车双向

- 前置：notes 积压 ≥5（保证 backlog 闸不先拦）、无 ask-state
- 动作与断言：
  1. 写新鲜 `maintenance-run.flag` → 直调 memory-ask → **静默**（撞车保护生效）
  2. `os.utime` 把 flag mtime 改 31 分钟前 → 直调 → **恢复出卡**（过期失效，
     防 flag 残留永久压制开场卡）
- 实测 2026-08-07：✅ 双向全过。flag 有效期常量 `MAINT_FLAG_MAX_AGE_MIN = 30`。
