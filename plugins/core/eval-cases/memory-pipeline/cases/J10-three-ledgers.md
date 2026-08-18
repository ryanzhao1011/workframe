# J10 三账对齐终查

- 工具：`python scripts/assert_three_ledgers.py --project <沙盒> --since <本轮起始日>`
- 五向核对：
  - A 提升条目 → memory_promoted 事件（--since 前的历史降 info，规避种子 events 截断误报）
  - **A' 搬迁条目 → memory_migrated 事件**（2026-08-16 增。标注字样决定要哪种事件：
    「从 X 提升」找 promoted、「从 X 迁移」找 migrated，**两者不可互换**——用 promoted
    顶替搬迁会让消费方误以为该域刚消化过 notes 积压）
  - B [纠正] 条目 → protected sidecar entry
  - C sidecar → MEMORY 对应条目（**匹配须同时含 promoted_date 与 migrated_date**——
    sidecar created_at 可能是提升日/迁移日，MEMORY 行首是经验发生日，三种日期都参与）
  - D 带 entry_key 的事件 → sidecar 有该 key（supersede 豁免按 **entry_key 首段命名空间**
    比较——粗比事件 scope 字段会把 skill 落点悬空误豁免）
- 断言：违例数 = 已知白名单数（首跑白名单：F5 一条——纠正落 skill 的 sidecar 规格空洞）
- 实测 2026-08-07：✅ 违例 1（=F5）+ info 6（全部为已解释的历史/supersede 项）。
  MEMORY 49 条 / sidecar 43 / events 245，坏行 0。
- 脚本口径沿革（首跑三轮迭代）：C 匹配补 promoted_date（消 5 条误报）→ D 补 supersede
  豁免（消 1 条误杀）→ 豁免收窄到 key 首段（救回 F5 真缺口）。改判定规则时先想清
  「谁是提升日谁是发生日、谁豁免谁不豁免」。

## 迁移场景断言（2026-08-16 增，防批次 4 同类回归）

这两条**必须造夹具跑**，不能只看真实项目——真实项目里没有反例。

| 场景 | 夹具 | 期望 |
|---|---|---|
| **M1 合规搬迁不得误判幽灵** | MEMORY 行首 `2026-08-10`（经验发生日）+ 标注「2026-08-16 从 auto-memory 迁移」+ sidecar `created_at=2026-08-16`（**迁移日，与行首不同**）+ `memory_migrated` 事件齐全 | **零违例**。这正是批次 4 漏掉的：当时沙盒夹具用「行首=迁移日」的简化条目，把两个日期的天然分叉抹平了，C 的缺口因此没暴露 |
| **M2 冒充搬迁须被抓** | 同上，但事件类型写成 `memory_promoted` | **报 A' 违例**（找不到对应 memory_migrated） |
| M3 sidecar 缺失 | 搬迁三件套缺 sidecar entry | 报 D 违例 |
| M4 真幽灵仍要抓 | sidecar 有一条 MEMORY 里查无对应的 entry | 报 C 违例——**验证 A'/C 的放宽没有削弱本职** |

- **M1 的两种 `created_at` 形态都要跑**：承接原创建日（=行首）与退用迁移日（≠行首），
  规格上两者都合法（auto-memory 无 `created` 字段时只能退用迁移日），三账本必须都认。
- 实测 2026-08-16：四场景全过。
