# J10 三账对齐终查

- 工具：`python scripts/assert_three_ledgers.py --project <沙盒> --since <本轮起始日>`
- 五向核对：
  - A 提升标注 → memory_promoted 事件，**按条目的 key 配**（--since 前的标注降 info，规避种子 events 截断误报）：
    MEMORY 行不带 key，脚本从行文本反推 sidecar key（同 scope；key 日期取行首或任一标注日；片段三种取法都试；
    几行撞同一前缀、靠 `-2/-3` 后缀消歧的并成一组），组内标注数 ≤ 按 key 认领的事件数即对上，**不看日期**。
    不足的部分、以及反推不到 key 的行，只认同 scope、日期差 ≤1 天、一对一配对的日期级证据（无主的带 key 事件，
    或 2026-08-18 之前不带 key 的旧事件），配上报 info「未解析」，配不上报违例。写在搬迁标注之前的提升标注
    （看行内先后，不看日期），沿 memory_migrated 的 `from_entry_key` 往回走它后面那几次搬迁，找当时那个域的事件；
    这几跳里有一跳缺这个字段报「未解析」（更早一跳从 auto-memory 迁入、合法省略它，不影响之后的提升）。
    摘要不足 20 字的短 key 按前缀认领；一行被几个 key 认领时只留片段最长的（`安装` 与 `安装（2号方案）` 两个 key
    都在时各认各的行）。长条目自己的 key 不在 sidecar 时会被短 key 认领，那时它的事件 key 悬空、由 D 报出。
    无主的带 key 事件（迁出、降级或行已改写的条目）也算日期级证据：同期有一批时，缺事件的行只降成「未解析」。
    `--since` 下组内新旧标注分档：日期早于 since 前一天的事件不算新标注的，旧档缺口降 info。
    事件文件（含 spill）读不了时报违例，不当空文件。
    **已知限制**：组内一个标注漏记、另一个多记一条时总数相等，判不出来；「未解析」只说明有同期证据，没按条目核过
  - **A' 搬迁条目 → memory_migrated 事件**（2026-08-16 增。标注字样决定要哪种事件：
    「从 X 提升」找 promoted、「从 X 迁移」找 migrated，**两者不可互换**——用 promoted
    顶替搬迁会让消费方误以为该域刚消化过 notes 积压）。判法与 A 相同
  - B [纠正] 条目 → protected sidecar entry
  - C sidecar → MEMORY 对应条目（**匹配须同时含 promoted_date 与 migrated_date**——
    sidecar created_at 可能是提升日/迁移日，MEMORY 行首是经验发生日，三种日期都参与）
  - D 带 entry_key 的事件 → sidecar 有该 key（supersede 豁免按 **entry_key 首段命名空间**
    比较——粗比事件 scope 字段会把 skill 落点悬空误豁免；经确认迁出后，同源 key 的 `memory_moved_out` 可作合法去向证据；经确认降级或同族归纳后，同源 key、排在原事件之后的 `memory_decayed` 同样可作证据；改域搬迁后，`from_entry_key` 指名原 key 的更晚 `memory_migrated` 可作证据，缺这个字段、只靠来源域与摘要片段对上的报「未解析」）
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
| M5 合规迁出不得误判悬空 | 原 MEMORY / sidecar 条目均已移出，保留原纠正或提升事件；新增同源 `scope` / `entry_key` 的 `memory_moved_out`，`archive_path` 指向原文归档，`target_path` 指向文档或模块记忆 | 零违例；迁出不冒充跨记忆域迁入 |
| M6 无关迁出不得豁免 | 同 M5，但迁出事件的 `entry_key` 属另一个条目 | 原条目仍报 D 违例 |
| M7 模块纠正无 sidecar | `user_correction` 为 `scope=module`，含 `module`、完整 `target_path` 与事实快照，不带 `entry_key` | 不要求 sidecar，不报 D 违例 |
| M8 合规降级 / 归纳不得误判悬空 | 非 protected 条目（`[纠正]` 条目不降级、不被归纳并掉）：原 MEMORY / sidecar 条目均已移除，保留原提升或搬迁事件；新增同源 `scope` / `entry_key`、排在其后、快照字段齐全的 `memory_decayed`。另跑 key 不同、scope 不同、排在原事件之前、缺快照字段四种 | 第一种零违例；其余四种原条目仍报 D 违例 |
| M9 写进已有条目的每个提升标注都要有事件 | ① 一条 MEMORY 条目保留原提升标注、又追加一个本次的（并进 / 归纳），sidecar 沿用原 key，分别缺本次、缺原次的 `memory_promoted`（当日同域没有别的提升事件）；② 当日只有另一角色域的提升事件 | ① 两个事件都在零违例、缺任一个报 A；② 报 A |
| M10 按条目配、不按日期配 | ① 标注日 D、带 key 事件落在 UTC 次日或更晚；② 同域同日两条条目，两条事件都带其中一条的 key；③ 同日只有不带 key 的事件（skill 落点或漏写 key）；④ 两条摘要一短一长、短的是长的前缀（`安装` / `安装器`），两条事件都带长的 key | ① 零违例；② 报 A，点名缺事件的那条；③ 报 A；④ 报 A，点名短的那条 |
| M11 改域搬迁 | 条目先在源域提升（源域 key 的 `memory_promoted`），再改域迁入：目标域 sidecar、`memory_migrated` 带 `from_entry_key`=源 key；另跑缺该字段、该字段指向别的 key、搬迁记录缺目标 `entry_key`、同日先迁入后提升（提升标注写在搬迁标注之后）四种 | 第一种零违例；缺字段时 A、D 各报一条「未解析」info、零违例；指向别的 key、缺目标 key 时报 D 违例（前者另报 A）；先迁后升而缺提升事件时报 A 违例 |

- **M1 的两种 `created_at` 形态都要跑**：承接原创建日（=行首）与退用迁移日（≠行首），
  规格上两者都合法（auto-memory 无 `created` 字段时只能退用迁移日），三账本必须都认。
- 实测 2026-08-16：四场景全过。
- 新增的 M5–M9 待配套脚本与夹具复跑；上面的带日期实测只覆盖当时的 M1–M4。
