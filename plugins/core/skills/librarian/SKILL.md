---
name: librarian
description: '记忆整理员：评估 notes.md 条目并按 D/U/R/A 提升到合适落点（含同主题融合）、扫描模块记忆、管理 MEMORY.md 容量候选、维护整理日志与 memory-index sidecar。用于用户要求整理记忆时（主通道，含首问维护提醒出现 `memory_backlog` 后用户说「处理」）、`workframe-maintenance` 批处理工单的 notes 积压项、会话中出现 `memory_backlog` 维护信号时、MEMORY.md 接近容量上限需要评估降级候选时、要核对 MEMORY 条目是否已被 skill / 文档承载时。典型触发：「整理一下记忆 / notes 太多了 / 记忆和 skill 有没有重复」。边界：只看记忆变动流水 → memory-log；纠正类条目由 correction-detection 按落点分流，不经本 skill。'
user-invocable: false
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion]
---

# Librarian 记忆整理技能

## 6 步整理流程

### 第 1 步：读取状态（动态角色发现 + shared 特殊角色）

**先处理 shared/（第 0 号"特殊角色"）**：
```
Read .workframe/agent-memory/shared/MEMORY.md（跨角色权威事实；不存在则跳过）
Read .workframe/agent-memory/shared/notes.md（共享缓冲；不存在则跳过）
```
shared 层的整理规则与 role 层一致（D/U/R/A 评估、容量管理、快照保存），但写入条件更严：
- 提升到 `shared/MEMORY.md` 必须满足"影响 ≥2 个角色" + D/U/R/A ≥2 项（**主 Claude 不计入角色数**，理由见 2b 三问第 3 问）
- **单条压缩到 ≤200 字符**。shared 在**每个主会话开场（CC 门）与各 subagent 启动时全量注入**——每加一句，此后每个会话、每次委派都要付一次 token；判据是「这句删掉，另一个域的人会不会做错事」，答否就删。展开论证留在 `shared/notes.md` 或权威文档，MEMORY 只留结论
- **shared 变更在整理日志中单列**（与 role 层变更分开列，标明「本轮 shared 新增/修改 N 条」+ 逐条一行摘要），供用户事后复核。不逐条弹确认——已有「≥2 消费域 + D/U/R/A ≥2 + ≤200 字符」三道门槛，再加人工确认属于冗余打扰
- 冲突以 `shared/MEMORY.md` 为权威，role MEMORY 中的同类条目降级为 notes

**再动态发现项目中其他角色**：
```
用 Glob 扫描 .workframe/agent-memory/*/  得到所有子目录，排除 shared
例如返回：[pm, dev, qa, prompt-eng]（core 默认）
          [pm, dev, qa, prompt-eng, ceo, designer, ...]（项目级扩展）
```

对发现的每个角色 `<role>`，读取其记忆文件：
```
.workframe/agent-memory/<role>/MEMORY.md
.workframe/agent-memory/<role>/notes.md
```

统计每个 MEMORY.md 的当前字符数（预算：role ≤8000 字符 / shared ≤4000 字符——字符才反映注入开销，行数可被单行超长钻空）。

**同时扫描模块记忆**：用 Glob 枚举 `projects/modules/*/module-memory.md` 与 `projects/modules/*/*/module-memory.md`，逐份 Read；只纳入有 `module.yaml` 的基础模块或有 `submodule.yaml` 的子模块。没有文件不建空文件。记录每份字符数（`len()` 口径，≤1,500）与覆盖路径；读取对应 overview 和提醒指向的文档，供下一步核对用途与去向。

**为什么动态发现**：core plugin 提供 4 通用角色（pm/dev/qa/prompt-eng），项目可手动在 `.claude/agents/` 和 `.workframe/agent-memory/` 下新增项目级角色（如 ceo、designer、content-operator 等）。Librarian 必须覆盖所有角色，因此不能硬编码列表。

### 第 2 步：评估 notes.md + 决定落点

先复查第 1 步读到的模块记忆，逐条按顺序看：① 触发条件是否具体到「做什么事时」；② 动作是否能照做；③ 依据是否还在——wikilink 目标是否存在看 `doc-graph-health` 报告的「模块记忆断链」段（没有报告或已过期就先跑；段内列为「未纳入扫描」的文件逐个核），未写成 wikilink 的路径逐个核，指到的章节是否仍在；④ 与当前权威文档是否一致，指向草稿时不把未拍板内容当现行口径。仍须是「做 X 时 → 要做 Y / 必读 Z」，不含产品或系统定义。环境与工序参数（如启动命令）可保留，核其适用环境是否仍成立；定义与结论应进 PRD/shared 文档，设计理由进 ADR。冲突、迁出、删除或超预算只列候选，不自行覆盖或删减；非冲突的提醒合并仍走第 2.5 步。改模块文档时也复查同目录提醒，跨子模块提醒放基础模块级，不建需求级模块记忆。

**2a. D/U/R/A 准入评估**

遍历每个角色的 notes.md 中的微反思条目，**跳过**下列两类已结案条目：
- 标注 `→ 已提升至 X` 的（本 SOP 自己提升过的）
- 标注 `→ 已被 {日期} 纠正取代` 的（`correction-detection` 第 3 步 supersede 时移进来的
  历史留档）——它们是**被推翻的旧口径**，重新提升等于把用户纠正过的错误再送回高置信区
- **D**urability：30 天后还重要？
- **U**niqueness：目标落点中尚未记录？
- **R**etrievable：未来需要回忆？
- **A**uthority：来源可靠？

满足 ≥2 项 → 标记为"待提升"。

> **单人/小团队项目的判定口径**：用户当面拍板的偏好，Authority 已满分，**不要因为"是否对所有人普适"而压低评分**——这类知识库只服务当前用户，普适性不是准入条件。历史上大量高价值偏好因这条误判滞留在 notes（实测某项目 181 行 notes 里全是可执行规则，却因自设"需多次验证"门槛从未提升）。

**2b. 落点分层决策**

`MEMORY.md` 不是唯一落点。先问「这是产品或系统定义吗」，再问「换模块还成立吗」；定义与规格进权威文档，只在本模块成立的作业提醒进模块记忆。一条混合内容拆开写，其余按下表与四问选：

| 落点 | 判据 | 加载时机 | 预算 | 权限 |
|---|---|---|---|---|
| **机器闸**（断言 / 校验脚本 / hook） | 判定是**确定性**的、能从文件系统或代码现算出来 | 运行时触发 | 不占上下文 | **L2 用户审批**（代码变更） |
| **`context/both/`** | 每次会话必须遵守，**主会话与 subagent 都要** | 两个 hook 各注一次 | **每个字符付两遍**；实际上限由 sub 片决定 | **L2 用户审批** |
| **`context/main/`** | 每次会话必须遵守，**只有主会话要**（假定能问用户 / 能派发） | SessionStart | 主片余量 | **L2 用户审批** |
| **`context/sub/`** | 每次会话必须遵守，**只有 subagent 要** | SubagentStart | **余量最紧** | **L2 用户审批** |
| **`AGENTS.md`** | **项目自有**判据，两个 harness 都读 | Codex 原生 / CC 经桥接 | **无硬闸、但单位成本最高**：没有那种静默截断（项目可覆盖文档大小上限），**但 Codex 侧每个字符付两遍**（上下文里出现两次）。**别把「无硬上限」读成「免费」** | **L2 用户审批** |
| **项目 skill**（`.claude/skills/<name>/`） | **场景触发且成套**的操作方法——特定场景才需要、一到场景就要整套用（SOP / 口径集 / 模板 / 清单） | 场景命中按需加载 | 无硬上限（不占每会话预算） | **L2 用户审批**（core plugin skill 不直接改，走 self-iteration 提案） |
| **模块记忆**（`<basic>/<sub>/module-memory.md`，跨子模块放基础模块级） | 只对在该模块动手的人有用的提醒，不写产品结论 | 文件存在且首次碰到模块时由 hook 投递；主线程压缩后再次碰到重投，子线程压缩后暂不恢复 | 每文件 ≤1,500 字符 | L1 自主；冲突、迁出与删除仍须确认 |
| **`<role>/MEMORY.md`** | 角色特有的高置信事实 | 该 subagent 被调度时；CC 门主会话调用该域 skill（dev 另含首次改模块代码路径）时 | 见 doctor 的 `memory_capacity` 检查 | L1 自主 |
| **`shared/MEMORY.md`** | 影响 ≥2 角色的权威事实 | 主会话开场（CC 门）＋各 subagent 启动时 | 见 doctor 的 `memory_capacity` 检查 | L1 自主（写入条件更严）；**agent / 主 Claude 不直写**——它们写 `shared/notes.md`，由本 skill 评估提升（对比上一行：role MEMORY 满足 D/U/R/A ≥2 可由角色直写，两行的「L1 自主」含义不同） |
| **auto-memory**（CC 官方主 Claude 记忆目录） | **主 Claude 层**内容：用户偏好 / 协作习惯 / 项目状态指针——消费者是主 Claude 而非某个角色 | 主会话启动（官方注入，subagent 不注入） | 见 doctor 的 `auto_memory` 检查 | 主 Claude 自维护；**librarian 不写**——识别到此类条目时在结果中建议归 auto-memory |
| **保留 notes** | 未定型 / 场景太具体 / 待验证 | 不加载 | — | — |
| **移出记忆体系** | 属**业务知识**（竞品分析、方案论证、领域事实）→ 应进需求文档 / shared / spec，技术决策进 ADR；不新增 role/shared 指针 | — | — | L2 |

> **前三个 `context/*` 落点在「订阅方项目」里不可直接写。** `context/` 在插件内，而插件装在带版本号的缓存目录——订阅方项目对它**要么没有写权限，要么写进去后一次插件升级就静默消失**，后者更糟，因为它看起来成功了。
>
> **项目整理不能以插件根可写取得改框架的资格**：`context/*` 对所有消费项目都不是直接写入落点；判到这里时只产出需上游采纳的说明，不修改插件。`AGENTS.md` 与项目 skill 仍按各自审批入口处理。
>
> **判到「该进 `context/`」时**：librarian 不写插件，结果摘要说明「本条需上游框架采纳才能生效，本项目内无直接落点」，由用户决定是否反馈；不为普通项目创建上游字段或候选箱。项目审批不授予修改另一个仓的权限。

**先问一次（与下面三问并行，不是第四问）**：这条能不能变成**机器闸**（断言 / 校验 / hook）？能 → **优先落闸**——纪律靠模型每次记得住才生效，闸不用。**然后再走三问**决定纪律文本放哪：闸抓违例、纪律说该怎么做，两者互补不互斥，落了闸不等于不用写。

**四问定落点**（顺序二元判断，逐问淘汰）：

1. **每次会话都必须遵守吗？** 是 → 进必载层，**转问 1b**
1b. **谁要遵守？**（本问是必载层内部的分流，退役前只有一个必载落点、没有这一问）
   - 主会话与 subagent 都要 → `context/both/`（**注意双倍代价**）
   - 只有主会话要 → `context/main/`
   - 只有 subagent 要 → `context/sub/`
   - 这是**项目自有**判据而非框架纪律 → `AGENTS.md`
2. 否——**特定场景触发、且是成套方法吗？**（≥3 条同主题相关做法，或含步骤/模板/清单结构）**先归纳**：同主题多条先试着压成少数几条原则，压得成的按第 1 / 第 3 问落点、不算成套方法；归纳后仍带步骤 / 模板 / 清单结构的 → 项目 skill。两形态：已有相关 skill → **写入该 skill**，每条按第 2.5 步第 3 条标操作（能并进、归纳的不新增），写前看第 8 条的体量信号；没有 → 同主题攒满 3 条前**留 notes**，攒满后聚类新建（PRD 风格类沉淀直接增补项目 prd-style；先例：requirement-archiving 由实战沉淀）
3. 否——先判**绑定具体模块吗**：产品或系统定义 → 移出记忆体系，按 L2 确认；作业提醒 → 该模块 `module-memory.md`。换模块仍成立的**离散事实** → 先判**谁消费**：主 Claude（用户偏好 / 协作习惯 / 项目状态指针）→ 建议归 auto-memory（主 Claude 自维护，librarian 不代写）；单个角色 → `<role>/MEMORY.md`；跨 ≥2 角色 → `shared/MEMORY.md`
   > **主 Claude 不参与「≥2 角色」的计数**。它在 main-led 模式下可能直做任何域的活，恒为潜在读者——把它算进去，每条工种知识都自动凑够两个消费者，`shared/` 会被稀释成第二个大杂烩，而 shared 是**各 subagent 启动时全量注入**的，稀释的代价由每次委派承担。<br>计数只数**角色域**：这条工种知识，除了它自己那个域，还有哪个**角色**未来在具体决策／操作时要读它？答不出第二个 → 单角色域。<br>（主 Claude 的读取路径由主会话注入片里的直做纪律与 SessionStart 记忆地图保障，不靠挤进 shared。）

**第 0 步「先算预算」**（**只在判定要进必载层之后跑**，不是每条都跑——否则本 skill 变慢会被跳过）：

- 跑 `inject-context.py --scope <target> --dry-run` 拿当前片的实际字符数与余量。**唯一事实源是打包器输出**，不是任何文档里写死的数
- **余量 < 待写内容 + 200 字符缓冲 → 不许直接写**（待写内容按第 2.5 步第 3 条判完操作后的**净增**字符算，并进、归纳可能为负；判为不变 / 拒绝的不跑本步）：先做一轮「哪些已内化成默认行为、可以精简」的复盘。提升在必载层是**零和**的——装不下就得先挤掉别的
- 复盘挤不出空间 → 该条目**降级落点**（转第 2 问看能否进 skill），或列为 L2 候选让用户裁「挤掉哪一条」
- `both/` 写 N 个字符，主片与 sub 片各付 N；**`both/` 的实际预算上限由 sub 片决定，不由 main 片决定**
- **落点是 `AGENTS.md` 时打包器管不到它**，第 0 步改查两件：① 该项目文档大小上限的实际设置（默认值会截断，覆盖过才没有硬闸）；② 按**两倍**计入 Codex 侧上下文账。**不查就写 = 把最贵的那个落点当成免费的**

**关键判断**：必载层是**注入**资产——放这里的知识对模型直接生效，可靠性最高，但每次会话都消耗 token，且**有硬上限**；skill 行是它的**制度化泄压阀**——场景性成套内容不进必载层、不占每会话 token。`MEMORY.md` 由 SubagentStart hook 注入对应 subagent（代码保证，仅角色被调度时加载）。**定型的、跨场景的纪律往必载层搬（先过第 0 步的余量）；场景性的成套方法往 skill 搬；未定型的留 notes。**

L2 落点的提升**不自动执行**：把候选清单写入 `.workframe/state/promotion-candidates.md`（覆盖式重写，头部带生成时间；每条一行，固定格式 `- [ ] <scope> | <YYYY-MM-DD> | <一句话摘要> | 出处: <notes 条目> | 建议落点: <文件+章节>（<操作>）`，操作按第 2.5 步第 3 条标——`- [ ]` 前缀是 maintenance_workorder.py 计数依赖，勿改），然后按调用场景分流：

- **常规调用 / `/core:maintenance-review`**：用 AskUserQuestion 当场请用户确认；用户跳过时清单保留在该文件，下次运行时先读它恢复候选再继续。用户确认后执行写入并把对应行改为 `- [x]`。
- **`--maintenance` 批处理**：**不当场出卡**——只写入候选文件攒卡，在结果摘要里提示用户「有 N 条 L2 候选待拍板」。批处理场景是 print 模式（没有交互卡可用）。

### 第 2.5 步：融合整理 SOP（写入前必须执行）

后台自动作业沿用本节的读全文、同主题融合、冲突停止、边界保留与写后整章检查；执行权限见 self-iteration 的后台作业段。后台只出建议、不写记忆；notes 保留原文，控制器按来源内容指纹记录每条证据有没有结论，不执行本节第 9 条的人工整理归档动作。容量不够也只在建议里标明，不挤掉旧条目。

> **禁止无脑 append。**往任何目标文档写入前，按顺序执行以下检查。这是本 skill 最容易被跳过、也最影响质量的一步——直接追加会让目标文档不断膨胀、同义内容重复、口径互相矛盾。
> 写入侧已前置：correction-detection / auto-update 的写入路径（工序在 skill `signal-intake` §1 A.U.N.）复用第 3 条的操作标签做**写时**同主题检查；本步在批处理/提升场景兜底复查漏网。

1. **先读目标文档全文**——不能只读插入位置附近。不了解全貌就无法判断归属与重复
2. **定位归属章节**——找语义最匹配的现有章节；只有确认现有章节都不合适时才新建，并说明理由
3. **标操作**——每条候选标一个操作，按下表自上而下判，先命中先用。**新增排在最后**：它是唯一只加不减的操作，前面几种都判不成立才轮到它

   | 操作 | 判据 | 怎么落 |
   |---|---|---|
   | **拒绝** | 已失效（前提被推翻、对象已退役），或用户拍板不提升 | 不写入；理由与当时条件写进归档标注（第 9 条） |
   | **不变** | 目标里已有一条规则覆盖它：照那条做，就不会犯这条记录的错 | 不写入；写出覆盖它的那条在哪（文件 + 章节） |
   | **并进** | 目标里有讲同一件事（同一场景、同一动作）的条目，只缺这条带来的细节或反例 | **改写合并成一条**，不是追加第二条：保留信息量更大的表述，补充缺失的细节 |
   | **归纳** | 目标里有条目与它同根因、只是场景不同，合起来能写成一条上位原则 | 写上位原则，原条目改成它的例子或并进它；原条目从原位置消失的按删除报批。落点是 MEMORY 时：并入一条存量条目照第 7 条；几条存量条目彼此归纳的，存量那部分见第 3 步「容量管理流程」第 4 条 |
   | **迁移** | 内容（这条，或目标里已有的）是支撑材料——长表、骨架、逐例清单——不是原则或入口；或根本属于别的落点 | 移到同 skill 的 `reference/` 或该去的落点；skill 内迁移在原位置留一句「什么情形必读它」，移出记忆体系的按 2b 表不留 role/shared 指针 |
   | **新增** | 以上都不成立，且现有章节确有这块空缺 | 追加一条；新建章节按第 2 条说明理由 |

   未达准入、未定型、待验证的**不标操作、留 notes**（2a / 2b）——拒绝是终局，留 notes 不是。
   - **判之前先查历史**：在**全部** scope 的 `notes-archive.md` 里找同一根因的旧决定。同一根因可能换了说法、也可能记在别的域，至少换一组关键词再查一次；存量标注写法不一（`用户拍板不提升`、`归档：已被 X 承接` 等），按内容找、不按标注字样找。命中只当**复核线索、不当否决**——旧决定只在当时的条件下成立：条件今天仍成立 → 沿用，理由里引用那条归档（日期 + 一句结论）；条件变了 → 照常判，理由里写「旧决定（日期）基于 X，现在 Y」；**条件没记下来**（`当时条件：未记录`，或存量标注本来就没写）→ 不沿用，按今天的事实照常判，理由里引用旧决定。「不变」的条件就是覆盖它的那条：那条已不在或已改写，算条件变了。未命中 → 照常判
   - **两档报告成本**，分界看有没有动到既有条目的位置：
     - **小改**——只新增一条或并进改写一条，没有既有条目移位或消失（不变 / 拒绝同档）：整理日志或候选清单里写一行「操作 + 理由」；并进另附那一条的改前改后
     - **改结构**——归纳、迁移、新建或拆分章节，或任何既有条目移位、消失：写入前展示**整章前后对照**与**逐条去向表**（每条旧内容 → 新位置，改写过的列原文）。原位置消失的条目**逐项**交用户批准，不要一揽子授权——落点是 L1 的 MEMORY 也一样，消失即删除
4. **查冲突**——新内容与现有内容矛盾时**停止写入**，向用户报告冲突点，不自行裁决谁对
5. **压缩表述**——notes 里的场景描述与论证**不搬**（留原处作为出处），只搬可执行规则；原条目里不留就无法照做的步骤和参数要保留，或写明归档位置；**说明规则边界的反例跟规则放在一起**，不当作场景描述省掉、也不单独下沉；粒度跟随目标文档现有风格（一条一句 vs 一条一段）
6. **格式对齐**——列表 / 表格 / 标题层级跟随目标文档现有约定
7. **标注出处**——写明「YYYY-MM-DD 从 `<role>` notes 提升」，便于回溯原始论证。**跨 scope 搬迁**（auto-memory → 角色域，或改域）写「YYYY-MM-DD 从 `<来源>` 迁移」并 append `memory_migrated` 事件——两种标注**不可混用**：三账本 check A/A' 按字样分别要求 `memory_promoted` / `memory_migrated`，写错字样等于报错了另一种事件。**写进一条已有的 MEMORY 条目**（并进、归纳）时：沿用该条的行首日期、原有出处标注与 entry_key，在原标注之后追加「（YYYY-MM-DD 从 `<role>` notes 提升，<操作>）」，事件照第 3 步「事件写入」第 1 条写 `memory_promoted`、`entry_key` 填沿用的那个（批处理时在 manifest 该条填 `into: <沿用的 entry_key>`，由代码记账）；`[纠正]` 条目不改写，同主题的新内容另起一条，日志按「新增」记
8. **体积预算**——写入必载层前走 2b 的**第 0 步**拿打包器现算的余量，**不再用「约 150 行」这种软阈值**（它没有强制力，也与实际上限无关）；余量不够先做一轮"哪些已内化成默认行为、可以精简"的复盘，复盘时场景性成套内容优先迁往项目 skill（按 2b 四问定落点，泄压不丢内容）。**落点是项目 skill 时**，写前现数两个观察信号（`len(text.splitlines())`；不是闸，不设增长百分比阈值）：① 该 `SKILL.md` 超过 500 行 → 本批照常写、操作优先选并进 / 归纳 / 迁移，另在候选清单附一条「精炼该 skill」交用户决定（做法见本步末「精炼项目 skill」）；② 它的某份 reference 超过 100 行却没有目录 → 同一张卡附「补目录、查导航」，不等于要压正文
9. **notes 侧处理（归档制）**——提升/处理完成后，把该条目**整段移动**到同目录 `notes-archive.md`（追加到文件尾，条目标题后加标注），并从 notes.md 删除该段。标注按第 3 条的操作写，第 3 条「查历史」读的就是它：
   - 写入了目标（并进 / 归纳 / 迁移 / 新增）→ `→ 已提升至 <目标文件+章节>（<操作>，YYYY-MM-DD）`
   - 不变 → `→ 不变：已由 <文件+章节> 覆盖（YYYY-MM-DD）`
   - 拒绝 → `→ 拒绝：<理由>；当时条件：<这个判断依赖的现状，如「X 尚未退役」「用户以 Y 为由不提升」>（YYYY-MM-DD）`。条件写不出来就写 `当时条件：未记录`，不要空着——以后查到它的人据此知道不能直接沿用
   - **为什么移动而不是原地标注**：notes.md 的语义是「待处理缓冲区」，`memory_backlog` 积压信号按它的内容量判定——已处理条目留在原地会让缓冲区只增不减、信号永不消解。archive 保留完整论证作为出处，不参与积压统计与后续评估
   - **混合段允许条目级拆分**：一个日期段里部分子条目已处理、部分未处理时，只移已处理的子条目，段内留一行注记 `（本段部分条目已处理，见 notes-archive.md 同日条目）`
   - `notes-archive.md` 首次创建时写文件头：`# <role> notes 归档区（已处理条目）` + 一行说明
   - 移动 = 删除 + 写入，但内容零删减，归类为 **L1**（不需要 L2 审批）
10. **跨 scope 顺带检测**——上面几步已把目标文档全文读了一遍，这是**唯一天然的跨域视野**，顺手做一次不额外读文件的检查：本轮处理的条目，是否与**其他域**已有条目讲同一件事？
   - 命中 → 出 **shared 候选**（进 `promotion-candidates.md`，走 L2 由用户拍板），**不自行搬**
   - 判据仍是「另一个域未来在什么决策／操作时要读它」——**两个域碰巧都提过某个工具不算**，工具重叠不构成消费证据
   - 这是**兜底**通道不是主通道：shared 候选的主发现路径是各角色收尾时的自反问（`agent-protocols` Step 2）。本步只捞那些当时没想到、事后从全文里才看出来的
   - 检测不到不算失职——**宁可漏报也不要凑数**：shared 每多一条，此后每个会话开场与每次 subagent 启动都要付一次 token

**自检**：写完后回读目标文档被修改的整个章节，确认 ① 没有同义重复 ② 没有前后矛盾 ③ 新旧内容的粒度和口吻一致。再现量每个目标文件的**净增减**（行 / 字符，与写入前比），记进整理日志。

**精炼项目 skill**（第 8 条的信号触发、用户同意后才做；本身就是一次「改结构」，按第 3 条那一档报）：

- 只做并进、归纳、迁移到 `reference/`；不借精炼改口径
- 正文留原则、入口和必读条件。入口写明**什么情形**必读哪份 reference，不写成「用这个 skill 前先读完」——那等于每次都触发，迁移就白做了
- **须同读的不拆开**：除第 5 条的规则与反例，同一机制的两个方向（如一个工具既会造成假红、也会造成假绿）也留在同一处
- **零损对账双向做**：旧版每条内容在新版有去向，新版每条能指回出处或登记为导航新增，逐条可追溯（并进、归纳允许多条对一条）；改写过的逐条人工核。对账只证明文本没丢，不证明模型照新版做得更好
- 逐项报批（第 3 条）时写明批准对应的是哪一版草稿——草稿再改，批准不自动沿用
- 500 行是目标不是门槛：压不到又不想丢边界时，报告卡在哪一节、为什么，交用户定

### 第 3 步：容量管理 + memory-index sidecar 维护 + 量化衰减

**前置动作：快照保存（L1 变更前必须执行）**

执行任何 MEMORY.md 变更前，先将当前版本快照到：
```
logs/librarian-snapshots/{YYYY-MM-DD}/{HH-mm-ss}-{role}-MEMORY.md
```

文件名带秒级时间前缀（`HH-mm-ss`），避免同分钟内多次运行覆盖。每个角色最多保留 5 个快照（按时间排序），超出时删除最旧的快照。此快照机制确保 L1 变更可回滚。shared/ 也生成快照：`logs/librarian-snapshots/{YYYY-MM-DD}/{HH-mm-ss}-shared-MEMORY.md`。

**同时**为受影响的 sidecar entries 生成同目录快照（确保 rollback 时 MEMORY.md 与 memory-index.json 一致）：
```
logs/librarian-snapshots/{YYYY-MM-DD}/{HH-mm-ss}-{role}-memory-index-entries.json
```
内容是从 `.workframe/state/memory-index.json` 中筛出 `scope=<role>`（或 `scope=shared` 处理 shared 角色时）的所有 entry 子集，格式：
```json
{"<entry-key>": {"scope":"...","created_at":"...","provenance":"...",...}}
```
若本次 librarian 运行未触及任何 sidecar entry（仅做 changelog 补漏 / metrics 重算），跳过 sidecar 快照。

**sidecar 维护**（`.workframe/state/memory-index.json`）：

若 `memory-index.json` 不存在，先按 `templates/memory-index-template.json` 的最小结构初始化：
```json
{"__schema__":"workframe.memory-index.v2","entries":{}}
```

每条 MEMORY.md 写入/删除时同步维护 sidecar。**MEMORY.md 保持人类可读**，元数据放 sidecar：
```json
{
  "entries": {
    "shared:2026-04-10:发版前必须跑脱敏终检": {
      "scope": "shared",              // 实际角色名（pm/dev/…）或 shared——填具体值，不照抄占位符
      "created_at": "2026-04-10",
      "provenance": "user-decree",    // 来源类型四选一，见下表；不打分、不写数字
      "protected": true,              // [纠正] 条目自动 true
      "source": "[纠正]"               // 枚举见下表；与 memory_promoted 事件的 source 同口径
    }
  }
}
```

**provenance 来源类型（归类判据——只归类，不打分，禁止写数字）**：

| 值 | 判据 | 赋值方 |
|---|---|---|
| `user-decree` | 用户明确纠正/拍板（`[纠正]` 条目） | correction-detection 固定写入，librarian 不产 |
| `user-confirmed` | 用户当面确认过 / 亲测实证 | 提升默认档 |
| `inferred` | 推断、单次观察，未经用户确认 | librarian 归类 |
| `external` | 外部转述、时效性强的信息（竞品动态 / 行业消息 / 他人说法） | librarian 归类 |

归类只需回答两个二元问题：①用户确认过吗？是 → `user-confirmed`；②否的话，信息源在项目外且有时效性吗？是 → `external`，否 → `inferred`。

**source 来源枚举**（与 `memory_promoted` / `memory_migrated` 事件的 `source` 字段同一套；doctor 的 `sidecar_health` 按此校验，写枚举外的值会被报出来）：

| 值 | 含义 |
|---|---|
| `[纠正]` | correction-detection 写入的用户纠正条目 |
| `notes.md` | 从本域 notes.md 提升 |
| `shared/notes.md` | 从 shared 缓冲区提升 |
| `auto-memory` / `<role>/MEMORY.md` / `shared/MEMORY.md` | 跨 scope 搬迁的来源域 |
| `manual backfill` | 人工补录 |
| `selfiter-evidence` | 后台控制器依据冻结证据归纳；`source_path` 保留证据来源，不算 notes 提升 |
| `librarian-promoted` | **历史值，只读不新写**——早期由 librarian 提升的条目用它。它答的是「谁做的」而非「从哪来」，与其余值不同轴；存量条目保持原样不回改，新写入一律用上面几种 |


Key 规则：`{scope}:{YYYY-MM-DD}:{条目前 20 字规范化}`。规范化三条**缺一不可**（与 skill `signal-intake` §5 同源，否则两边为同一条目算出不同 key、sidecar 出现重复项）：**先删除全部空白字符**（空格 / 制表符 / 换行）**→ 再取前 20 字**（Unicode 字符数，非字节）→ 同日同前 20 字碰撞时追加序号后缀（`-2`、`-3`…）。

**顺序不可颠倒**（2026-08-16 统一，同 skill `signal-intake` §5）：颠倒会让同一条目在两处算出不同 key。改序前的历史 key 不回改。

**事件写入（供 `/core:memory-log` 展示历史流水）**：

1. notes.md → MEMORY.md 提升成功后，先写 MEMORY.md 和 sidecar entry，再 append `.workframe/state/events.jsonl`：
   ```json
   {"ts":"<ISO-8601>","type":"memory_promoted","scope":"<角色名或shared，填实际值>","role":"<role-if-any>","entry_key":"<memory-index-key>","summary":"<条目摘要/原文前80字>","source":"notes.md","protected":<sidecar.protected>,"provenance":"<sidecar.provenance>"}
   ```
   落点为 skill / rule 文件时（L2 经用户批准后执行）：**不写 sidecar**（sidecar 只索引 MEMORY 条目），事件仍写但**省略 entry_key**，落点文件路径写进 summary——与 correction-detection 第 4 步同规格。notes 提升到模块记忆同样不写 sidecar，事件用 `scope: module`，在 summary 写明模块与目标路径，省略 `entry_key`。
2. MEMORY.md → notes.md 降级或容量腾挪经用户确认后，**必须在删除 sidecar entry 前** append `.workframe/state/events.jsonl`，把即将删除的元数据快照写入事件：
   ```json
   {"ts":"<ISO-8601>","type":"memory_decayed","scope":"<角色名或shared，填实际值>","role":"<role-if-any>","entry_key":"<memory-index-key>","summary":"<降级条目摘要/原文前80字>","source":"<sidecar.source>","age_days":<按 created_at 现算的整数>,"provenance":"<sidecar.provenance>"}
   ```

3. **跨 scope 搬迁**（auto-memory → role/shared，或域判错后改域）——先在目标域写好条目与 sidecar entry，再 append `.workframe/state/events.jsonl`：
   ```json
   {"ts":"<ISO-8601>","type":"memory_migrated","scope":"<迁入的目标域：角色名或shared>","role":"<role-if-any>","entry_key":"<目标域新建的 memory-index-key>","summary":"<条目摘要/原文前80字>","source":"<迁出方：auto-memory 或 <role>/MEMORY.md>","from_ref":"<源文件名或条目标识>","from_entry_key":"<源条目的 sidecar key，原样照抄；源是 auto-memory 等没有 sidecar 的来源时省略>","protected":<sidecar.protected>,"provenance":"<原样承接，不重新判定>"}
   ```
   **改域时 `from_entry_key` 必填**：源 key 随源条目离开 sidecar，没有它，三账核对会把源域的旧事件判成悬空，也追不到搬迁前打的提升标注。
   **与 `memory_promoted` 的分工**：提升是**同一 scope 内** notes → MEMORY（过 D/U/R/A）；搬迁是**已合格条目跨 scope 移动**（不重判准入）。混用会污染 notes 陈化信号——消费方靠 `memory_promoted` 的时间戳判"这个域多久没消化 notes 了"，而搬迁并没有消化任何 notes。
   `provenance` **原样承接不重新判定**：搬迁不是新的信息来源，把 `user-decree` 在搬家途中降成 `inferred` 会让该条目失去衰减豁免。
   **三个日期各有出处，别混**：

   | 位置 | 取值 | 理由 |
   |---|---|---|
   | MEMORY 条目行首 | **经验发生日**（原条目的日期，搬迁不改） | 它回答「这条知识是什么时候的」，与搬家无关 |
   | 出处标注 `（YYYY-MM-DD 从 … 迁移）` | **迁移日** | 它回答「什么时候搬的」 |
   | sidecar `created_at` 与 `entry_key` 里的日期 | **优先承接原创建日**；源头查不到（如 auto-memory 只有 `modified`）时退用迁移日 | 衰减时钟按 `created_at` 走——重置它等于让一条两年前的老知识搬个家就「变年轻」，与 `provenance` 原样承接同理 |

   两种取值三账本都认（`check C` 同时收行首日期与迁移日），但**同一条目内三处必须自洽**：`entry_key` 的日期段要与 `created_at` 相同。

4. **移出角色记忆到文档、模块记忆或 skill**——先逐条列原文、建议落点、目标是否已有与动作，交用户确认；新模块投递尚未验证可用时，不迁出仍在起提醒作用的条目。批准后依次：前置快照 → 补齐目标 → 把源条目原文归档到同目录 `notes-archive.md`（标注承接位置）→ 写 `memory_moved_out` → 最后才移出 MEMORY 与 sidecar。事件字段：保留源 `scope` / `entry_key`，`archive_path` 指向归档位置，`target_path` 写完整目标路径（承接它的文档、`module-memory.md` 或 skill 文件），`summary` 写事实快照。源条目没有 sidecar entry 时照样写事件，`entry_key` 按 skill `signal-intake` §5 的归一规则由条目原文现算，并在汇报里写明「源无 sidecar」。不借用 `memory_migrated`，后者的 key 属目标记忆域。存量指针条目不自行删除，由下面的「反向对账」出候选。

   事件在**改 MEMORY 与 sidecar 之前**写——它是可恢复证据，先于被它记录的改动落盘；同一次迁出中途失败要重试时，先查本次那条是否已经写下——同 `scope`、同 `entry_key`、同 `archive_path` 与 `target_path`，且晚于该 key 最近一次写入事件（提升 / 纠正 / 搬迁）；已有就不再追加。更早的同 key 迁出（例如条目回滚恢复后再次迁出）不算本次证据，照常追加。下例 `dev` 替换为源条目的实际域，原 `entry_key` 照抄，不重新归一：
   ```bash
   python "$WF_PLUGIN_ROOT/bin/workframe-event" memory-moved-out \
       --scope dev --entry-key "<原 entry_key>" \
       --archive-path "<原文归档路径>" --target-path "<完整目标文档路径>" \
       --summary "<迁出事实的完整快照>"
   ```

`summary` 是历史快照字段，memory-log 优先展示事件里的 summary；不要依赖当前 sidecar 仍能查到已降级条目。

**反向对账（MEMORY 条目是否已被权威资产承载）**——每次运行都对第 1 步读到的全部 MEMORY 条目做一遍，不只看本轮新提升的。带去向标注的条目（「已进 X」「已固化进 X」「口径见 X」「权威见 X」等）先看，但标注只是检索线索：没标的也可能已被承载，标了的也可能不承载。逐条读去向原文核对，判成三类出候选：

| 判定 | 条件 | 动作 |
|---|---|---|
| **退役** | 以下四条**同时**成立：① 目标完整承载规则、适用条件与例外——照它做就不会犯这条记录的错；② 这条的**全部**使用场景里目标都有加载入口（同场景 skill 自动叠加、`AGENTS.md` 或必载纪律每会话加载算；只在别的流程才读到的 skill 或 reference 不算）；③ 不是 `[纠正]` 条目，也不是有意常驻注入的红线；④ 独有的增量与出处已有去处 | 按上面第 4 条移出，`target_path` 写承接它的文件 |
| **订正标注** | 标注声称的去向不承载或只承载一部分 | 改写标注，或把未承载的部分补进去向后再判退役 |
| **保留** | 四条有任一不成立 | 不动；候选清单里写一句不成立的是哪一条 |

退役与订正都是「改结构」那档（第 2.5 步第 3 条），逐项交用户批准。**写入侧同理**：往 MEMORY 或 notes 写「已进 X / 已固化进 X」这类去向标注前，先读 X 核对它真的承载了这条——没核过的标注会让后来的人把仍有用的条目当重复清掉。

本节靠 librarian 被调起才运行，没有独立触发器：notes 积压为零的项目里它不会自动跑，要用户点名「核对记忆和 skill / 文档有没有重复」。

**量化衰减**（基于 sidecar 的 provenance 来源类型 + age 判断——归类不打分）：

- `protected = false` 且 `provenance = external` → 生成**待审查降级候选**（外部转述/时效性信息，不论年龄；不自动执行——在下次 `/core:maintenance-review` 或维护批处理时由用户确认后执行；执行时按上面的时序先 append `memory_decayed`，再删除 sidecar entry）
- `protected = false` 且 `provenance = inferred` 且 `(today - created_at) > 180 天` → 同上生成待审查降级候选（长期未被重申的未确认推断）
- `user-confirmed` / `user-decree` 条目**永不自动进候选**——用户确认过的事实不因年龄过期，要变只能被新的确认/纠正 supersede
- **dormant 项目**：所有衰减动作**和** notes→MEMORY promotion **均冻结**（读 `activity-state.json` 的 `dormant` 字段，true 则本步全部跳过；`wake_up_pending=true` 时同等处理）。除非用户通过 `/core:maintenance-review` 明确确认，否则不改 MEMORY / notes

**容量管理流程**：

如果 MEMORY.md 已接近字符预算（role 8000 / shared 4000）：
1. 识别低价值条目（从 sidecar 衰减信号识别：provenance=external / provenance=inferred 且 age>180 天 / protected=false 且非 [纠正]）；另看同族：同根因、能写成一条上位原则的几条，出「同族归纳」候选（按第 2.5 步第 3 条「改结构」那档展示；`protected=true` 的条目不被并掉，下面「绝对禁止清理」照旧）。**不把条目统一压成指针**；shared 里每次会话都必须在场的红线保留原文
2. 生成**容量整理候选**，写入本次 Librarian 日志和 `/core:maintenance-review` 待用户确认
3. 用户未确认前，不移动 MEMORY 条目、不删除 sidecar entry、不为了腾空间写入新条目
4. 用户确认后，才将候选条目移至 notes.md 底部（标注"已从 MEMORY.md 降级，source=<原 source>"），同步删除 sidecar entry，再写入待提升条目。**同族归纳候选不走降级**：把上位原则改写进这几条里最早那一条的位置（沿用它的行首日期、原有出处标注与 entry_key，sidecar 字段不动——标注丢了，三账会把它的 sidecar 判成幽灵条目）；其余几条原文移入同目录 `notes-archive.md`（标注 `→ 已提升至 <MEMORY 文件>「<上位原则条目前 20 字>」（归纳，YYYY-MM-DD）`），删它们的 sidecar entry 前按上文「事件写入」第 2 条各 append 一条 `memory_decayed`，`summary` 写「归纳进 <沿用的 entry_key>：<原文前 80 字>」

**绝对禁止清理的内容**：
- 带 `[纠正]` 标记的条目 — **永不清理**（sidecar `protected=true`）
- 用户明确要求保留的条目（`protected=true`）

### 第 4 步：日志补漏

检查 `projects/changelog.md`（如存在）是否有缺失的记录：
- 对比 MEMORY.md / notes.md / module-memory.md 的最近写入与 changelog 条目
- 补齐缺失的 changelog 记录
- 若项目未维护 changelog.md，跳过此步

### 第 5 步：刷新 `skill-metrics.yaml`（调用 deterministic 脚本）

**默认路径**：SessionEnd hook 会调用 `recompute_skill_metrics.py` 自动重算 `.workframe/state/skill-metrics.yaml`。如果本次 Librarian 需要立即刷新，调用插件内兜底命令（插件根取 `$WF_PLUGIN_ROOT`，即会话开场注入的「本会话插件根」，不依赖 PATH）：

```bash
python "$WF_PLUGIN_ROOT/bin/workframe-recompute-skill-metrics"
```

`$WF_PLUGIN_ROOT` 是会话开场注入的「本会话插件根」，执行前换成那个路径。若环境里 `workframe-recompute-skill-metrics` 恰好已在 PATH（CC plugin `bin/` 注入生效的环境），裸调等价。

**脚本职责**：

1. 读取 `.workframe/state/events.jsonl`（逐行 JSON 解析；跳过 `__schema__` 描述行和 malformed 行）
2. 按默认窗口 30 天过滤（`generated_at - ts ≤ 30d`），也可由调用方指定
3. 分类聚合：
   - `skills[<name>]`: 键是规范技能身份（裸名与 `core:` 名归一，判定见脚本目录 `_skill_identity.py`）；按 `type=skill_used`（模型自报）汇总 `invocations`（计数）、`successes`（`success=true` 计数）、`last_used`（最新 ts 的日期），另按 `type=skill_invoked`（hook 记的调起）汇总 `invoked`——两列**不相加**，只有调起没有自报的技能 `invocations: 0`
   - `rules`: **整段空对象**——`recompute_skill_metrics.py` 直接写 `rules: {}`（`rule_triggered` 无 deterministic producer，不生成占位 entry；保留 `rules` 顶层 key 仅为 schema 结构兼容）。下游（check-iteration-trigger.py）不再消费此字段；见 `.workframe-meta/event-schema.json` 中 `reliability: removed_v0_2_1`
   - `corrections_count`: `type=user_correction` 事件总数
   - `blocks_count`: `type=task_blocked` 事件总数
   - `proposal_failures_count`: `type=proposal_verified` 且 `signal_met=false` 的事件总数
4. 整个重写 `.workframe/state/skill-metrics.yaml`（不是 append），更新 `generated_at` 和 `window_days`
5. 不写入任何 skill/rule frontmatter — frontmatter 不再承担统计职责（Phase 0 已清理非官方字段 `usage_count` / `last_used` / `trigger_count`）

> 为什么 L1：脚本重算是 deterministic 的派生文件操作，不改动用户可读资产（MEMORY.md / notes.md / rules / skills 本体），失败影响面仅限 skill-metrics.yaml 自身，旧版本可随时从 events.jsonl 重新生成。

**下游消费者**：
- `self-iteration` 读 `skill-metrics.yaml` 作人工判读线索（无需解析全量 events；成功率不作自动触发信号）
- 审计/追因需要时读 `events.jsonl`
- `/core:audit` 等维护命令汇总展示

### 第 6 步：关闭积压信号 + 写整理日志

**关闭 `memory_backlog` 信号**（本次整理覆盖了触发该信号的 notes 时）：

1. Read `.workframe/state/activity-state.json`，找 `pending_maintenance` 中
   `kind == "memory_backlog"` 且 `status == "open"` 的条目，记下它们的 `id`
2. 用代码通道关闭（**不要自己改这个文件**）——它一并写 `pending_maintenance_dismissed` 事件：

   ```bash
   python "$WF_PLUGIN_ROOT/scripts/maintenance_workorder.py" \
       --close-pm <PM-ID> [<PM-ID> ...]
   ```

> **为什么不自己写这份文件**：`activity-state.json` 同时装着 `session_counter` /
> `recent_drift_repairs` / 注入标记等一点点攒出来的状态。整份重写时漏掉任何一个都不会
> 报错，只会静默丢掉一段历史；而上面那个命令走文件锁 + 原子替换 + 三方合并，只动
> `pending_maintenance` 一处。同理，`--commit` 路径的记账也一律由代码提交。

> 归档制（第 2.5 步第 9 条）保证已处理条目移出 notes.md——若整理后 notes 仍超阈值（还有大量未处理条目），下次 Stop hook 会重新产生信号，这是**正确行为**，不是重复报警。

在 `logs/librarian/{YYYY-MM}/{YYYY-MM-DD}.md` 追加本次 Librarian 运行摘要：
```
## {日期} Librarian 整理

- 覆盖角色：{第 1 步发现的角色列表}
- 覆盖模块记忆：{路径列表}；超预算 / 冲突 / 失效去向候选：{n} 条
- 评估 notes.md 条目：{n} 条（跳过已标注已提升：{n} 条）
- 提升到 MEMORY.md：{n} 条
- 写入模块记忆：{n} 条；经确认从 MEMORY 移出到文档或模块记忆：{n} 条
- 提升到 AGENTS.md / 项目 skill（L2，已确认）：{n} 条；待确认清单：{n} 条
- 操作统计：拒绝 {n} / 不变 {n} / 并进 {n} / 归纳 {n} / 迁移 {n} / 新增 {n}（其中新建章节 {n}）/ 因冲突暂停 {n}；历史检索命中 {n}（沿用 {n} / 条件已变重判 {n} / 条件未记录重判 {n}）
- 各目标文件净增减：{文件}：{+a/−b 行，+c/−d 字符}；体量信号：{命中的 skill 与已附的精炼 / 补目录候选，无则写「无」}
- 从 MEMORY.md 降级：{n} 条（仅统计用户已确认执行的降级；候选另列）
- 待审查降级/容量候选：{n} 条
- 快照保存：{n} 个角色
- skill-metrics.yaml 重算：window_days={N}，events 条数={n}，统计了 {m} 个 skill / {k} 个 rule
```

重大结构变更（如清理过期条目）同步追加到 `projects/changelog.md`（如维护）。

## 变更权限

- **L1 变更（自主执行）**：非冲突 notes.md → `MEMORY.md` / `shared/MEMORY.md` / `module-memory.md` 条目提升、模块作业提醒新增或合并、已处理条目整段移入同目录 `notes-archive.md`（归档制，见第 2.5 步第 9 条——**不是**原地加标注，那是被它取代的旧做法）、changelog 补漏、快照保存、`skill-metrics.yaml` 脚本重算
- **L1 候选（须用户在 `/core:maintenance-review` 确认后执行）**：MEMORY.md → notes.md 降级、容量不足时的腾挪整理
- **L2 变更（须用户审批）**：删除任何内容、**提升/增补到框架的注入源 `context/**`、`AGENTS.md` 或项目 skill（`.claude/skills/**`、`.agents/skills/**`）**、把业务知识移出记忆体系到需求文档、把现有 MEMORY 条目迁出到文档或模块记忆、修改 `board.yaml` 任务结构、改 core plugin 内源文件（含 core skill——走 self-iteration 提案）

> L2 落点（`AGENTS.md` / 注入源 / skills）是主 Claude 直接消费的资产（前两者每会话必载、skill 场景加载），误写影响面大，因此**一律先出清单等用户确认**，不自主执行。清单形式：每条给「一句话摘要 + 出处 + 建议落点 + 建议插入的章节 + 操作」，让用户一次过目即可决策；改结构的条目另附第 2.5 步第 3 条要求的整章对照与去向表。
