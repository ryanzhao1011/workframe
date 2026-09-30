# 项目初始化指南（workframe-launcher setup）

`/workframe-launcher:setup` 是 Workframe 的**唯一初始化入口**：在任意目录发起，创建新项目或接入存量项目，完成后由项目里的 core 插件接管日常工作。本文讲它会问什么、怎么判断、生成什么、失败了怎么办。

10 分钟上手版见 [quickstart.md](./quickstart.md)；本文是细节参考。

## 两个插件的分工

| 插件 | 级别 | 职责 |
|---|---|---|
| `workframe-launcher` | 用户级（每台机器装一次） | 「怎么开局」：目录判定、对话采集、确认页、执行初始化 |
| `core` | 项目级（launcher 自动订阅进项目） | 「项目里怎么干活」：4 角色、37 skills、hooks、会话启动时注入的必载纪律 |

launcher 通过 marketplace 注册信息定位 core，**两扇门各有自己的注册面，所以它按序试两个定位器、谁解析得到用谁**：Claude Code 门读 `~/.claude/plugins/known_marketplaces.json` 的 `installLocation`，Codex 门读 `codex plugin marketplace list --json` 的 `root`（市场**来源**另从 `<CODEX_HOME>/config.toml` 取）。它**不去探测「我现在跑在哪扇门里」**——按结果判，某个定位器跑不通就是「这扇门没有」，不是错误。你同时注册了多个 workframe 市场（如开发目录源 + GitHub 源）时，它会问你用哪个，不会自己挑。

## 触发方式

- 自然语言：`帮我建一个 workframe 项目` / `把这个项目接入 workframe`
- Slash 命令：`/workframe-launcher:setup`

它**不会**在你说「初始化一下项目」「帮我 git init 一下」这类通用脚手架请求时抢跑——launcher 只认 Workframe 相关意图。

## 目录状态判定与分流

launcher 先判断当前目录是什么（按顺序，前面命中就不往下走）：

1. **已接入**——有 `.workframe-config.json`
2. **项目目录**——根级有 `.git` 或项目清单（`package.json` / `pyproject.toml` / `Cargo.toml` 等）；这条一票否决容器判定，**monorepo 也算项目**，不会被误判成容器
3. **容器目录**——桌面 / 下载 / 文档 / 用户主目录 / 盘符根这类位置
4. **空目录**——近空（只有 `.git` / `README` 之类）
5. 拿不准 → 按项目目录处理（判错只影响推荐顺序，不删任何选项）

分流原则：**明确意图不拦路，模糊意图才出选择卡**。几个关键行为：

- **容器目录下不提供「把桌面本身变成项目」**——那会在容器里散出一堆框架文件；新项目建在 `<容器>/<项目名>` 子目录
- **项目目录里说「新建」会先问一句**：是在项目下建个工作区子目录，还是把这个项目本身接入（后者等同「接入」，会做存量分析与 CLAUDE.md 整合）
- **项目名与已有目录撞名时不硬建**：问你是要接入那个已有项目、在它下面建子目录、还是换个名字
- 兜底：**用户明确表达的意图永远高于自动判定**

## A 路径：全新创建

对话采集，逐个问、不一次倒一堵墙：

| 问题 | 用途 |
|---|---|
| 你在负责什么类型的产品？ | 业务上下文——喂给 AGENTS.md 业务背景段、role_profile 推断、名称候选 |
| 项目叫什么？ | 展示名（报告 / 启动上下文用），可与目录名不同 |
| 放在哪个目录？ | 候选按目录状态给；空目录就地新建时自动跳过；**候选路径先查是否被占**，撞上已有目录会问你接入 / 建子目录 / 换名 |
| 有没有已有资料要纳入分析？（可跳过） | 有 → 走 B 路径的分析能力；跳过 → 标准骨架起步，日后在项目内随时补 |
| 一句话目标 | 会写进 AGENTS.md，影响模块切分与角色推断——写具体一点更好 |

### role_profile 自动推断（不设问，确认页可改）

`role_profile` 决定 4 个 baseline 角色（pm / dev / qa / prompt-eng）在项目里的**默认路由优先级**——只是软提示，不禁用任何角色，你始终可以 `@角色名` 直接调用。

| Profile | 主力 | 推断条件 |
|---|---|---|
| `ai-product` | prompt-eng / pm / dev | 业务上下文**明显**以 AI / LLM / Prompt / Agent 能力为核心（「智能 X」「自动化」等含混词不算） |
| `solo-pm` | pm | 「个人 PM / 一人 / 没研发」等独立工作信号；纯文档 / 需求项目 |
| `software-team` | pm / dev / qa | 默认档 |

判据是「**谁在这个项目里真的干活**」，不是「公司里有没有这个工种」——项目里没有代码就不该落 `software-team`（那会把 @dev / @qa 设为主力，而项目里没有东西可写、可测）。确认页会写明推断依据（含「本项目内有 / 无代码」），不符合当场改。完整定义见 [`role-profile-catalog.md`](../plugins/core/reference/role-profile-catalog.md)。

## B 路径：存量项目接入

不填表——launcher 扫描证据后**带证据确认**：

| 观测维度 | 影响 |
|---|---|
| 有没有代码 | 模块树切分证据 + `submodule.yaml.code_paths` 建议 + 接入后 code-to-doc 计划 |
| 有没有存量文档 | 搬家分流计划（进确认页） |
| git 贡献者数 | 仅 role_profile 推断，不影响目录结构 |

三件事值得单独知道：

1. **CLAUDE.md / AGENTS.md 整合**。骨架脚本对已存在的 CLAUDE.md、AGENTS.md 一律不覆盖；但不整合的话，你原文里的项目内容与框架要求的两处结构（`CLAUDE.md` 里一行 `@AGENTS.md` 导入、`AGENTS.md` 的四个契约段）对不上——缺导入时落盘验收会以 error 抓出（doctor 的 claude_md 项查导入行，不只看文件在不在）。框架的角色表 / 路由规则 / 状态流转与签发权限由会话启动时的注入片投递，不写进这两个文件。所以 launcher 会按 [`claude-md-merge-guide.md`](../plugins/core/reference/claude-md-merge-guide.md) 出合并稿，确认页露出**含你原文的段落**供审（框架样板段每个项目一样，不占版面），写盘前按 git 状态决定是否落备份（`logs/CLAUDE.md.bak-<时间戳>`，AGENTS.md 同理）。
2. **已有项目级 agent 检测**。`.claude/agents/<自定义角色>.md` 存在时自动检查三项（frontmatter 用法 / body 是否引用 agent-protocols / `.workframe/agent-memory/<role>/` 是否存在）并提议 patch。
3. **层层递进地落地，不一次性塞决策**。结构闸只拍模块树与 CLAUDE.md 整合稿；骨架建好后**逐模块**深读资料、逐模块确认安置与原件处置并当场执行（外部来源只拷不动）；「整理归档」（把 docx / xls 等原始资料整理成正式需求文档）在节奏闸单独拍：现在连续做完 / 做一部分 / 重启后接力 / 暂不做。推迟的批次结构化记进 `setup-state.json` 的 `pending_work`（含节奏），重启后首个会话**主动接起**；「暂不做」则零打扰、只在体检与看板可见，随时说「继续初始化」恢复。

### 接入会碰哪些文件

**会创建**（已存在则跳过）：

- `projects/` — board.yaml（任务看板）/ modules/overview.md（模块树总图）/ specs/overview.md / specs/_meta/taxonomy.md（tag 受控词表）/ issues/TEMPLATES.md / changelog.md / dev-log.md（开发账本，自带一条以装机日为期的首条；收口检查 `[ledger]` 项读它）/ evals / proposals / archive 骨架
- `logs/` — hook 输出与报告目录（gitignore）
- `.workframe/state/` — 运行时状态（gitignore）
- `.workframe/agent-memory/` — shared + 4 个 baseline 角色的记忆骨架（**默认进 git**：记忆是跨会话资产，丢了不可重建。记忆正文常年积累业务细节，若项目含客户名 / 未公开数据，自行在 `.gitignore` 加回 `.workframe/agent-memory/`——框架不替你判断）
- `.agents/skills/prd-style/` — 项目 PRD 框架（出厂默认版实例化；已存在则不覆盖，项目可自由改）。`.agents/skills/` 是项目 skills 的真实源，`.claude/skills` 建成指向它的目录链接；你的项目原本就有 `.claude/skills/` 真目录时落在那里，链接建不成时也退回 `.claude/skills/` 真目录
- `company-context/README.md`、`my-workspace/README.md` — 公司资料与个人产出的两个起步目录，各带一份敏感内容提示（框架不为它们预设 `.gitignore` 规则，哪些进 git 由你定）

**会修改**（逐个点名）：

- `CLAUDE.md` — 整合覆盖（见上文 §B 路径，落备份）；目标已有自己的 `AGENTS.md` 时同样走整合（四个契约段与项目段并进去）
- `.gitignore` — 末尾追加 managed 标记块，含 5 个必需条目：`.claude/settings.local.json` / `.workframe/state/*` / `logs/` / `tmp/` / `.tmp/`，外加一行例外 `!.workframe/state/memory-index.json`（记忆 sidecar 必须进 git，理由见 core `reference/project-architecture.md` §Git 策略）；已全齐则不动
  - `/*` 与例外行是**成对硬契约**：改成 `.workframe/state/` 会让 git 静默无视例外行，sidecar 无声失踪。多人协作时 sidecar 若冲突，删本地重建，不手工 merge JSON
- `.workframe-config.json` — merge 模式：只刷新框架字段（`framework_version` 等），用户字段完整保留
- `.claude/settings.json` — 写入两项订阅声明（`enabledPlugins` + `extraKnownMarketplaces`）。由装机脚本 merge 写入：先落一份 `logs/settings.json.bak-<时间戳>`（`logs/` 在 gitignore 内），你原有的**键与值**（`permissions` / `hooks` / `env` …）一个不动，写完回读校验，写不成就按备份还原并给手动补丁。**已是目标状态时一个字节都不写**（重复补跑零改动，也不堆备份）
  - **真要改的时候，保住的是键与值、不是文件排版**：产物恒为 无 BOM / LF 换行 / 两空格缩进。你原来的文件若是别的形态（记事本存的 BOM + CRLF、四空格缩进…），键一个不少，但 `git diff` 会显示整份文件变了（`settings.json` 进 git，所以接入已有仓时会看到这一个整文件 diff）。**只在真的需要改键值时才会发生**——两项声明已经对了的话，哪怕你的文件是 BOM + CRLF，它也一个字节不动
- `AGENTS.md` — 缺失时按出厂模板渲染（**已存在一律不覆盖**，已有内容走上面的整合）

**绝对不动**：你的代码与业务目录、`.claude/rules/` 根目录与 `local/` 下的项目专有 rules、项目级 `.claude/agents/` 与 `.claude/skills/`。

## 结构闸（写盘与订阅前的唯一闸门）

只拍**项目级三件事**：模块树（粗扫标题级证据支撑，用你的业务语言展示 + 「你以后每类东西放哪」映射表）、CLAUDE.md 整合稿（露出你的原文段落）、会被修改的已存在文件逐个点名。**点名的那份文件清单不是手写的**——渲染这一页之前先跑一次 `project_scaffold.py … --print-plan`（只读预演，一个字节不写、目标目录也不创建），它按同一份参数算出本次将要建 / 改的每一个文件、含项目外的用户级市场注册表，另有一格是**已存在、脚本一律不覆盖**的那些——你自己写的 `CLAUDE.md` / `AGENTS.md` 在这一格里**逐项报名字**（框架自己的骨架文件折叠成一行计数）；它们的合并稿由接入流程另行写回，页面会单独说。页面逐字转述它的输出。每份文件的安置与处置**不在这里拍**——骨架建好后逐模块小闸确认，一轮只核几份文件，改动也不连锁。选项：`按此执行` / `我要调整`（用自然语句说改什么，迭代到满意）/ `取消`。**确认前零写盘。**

## 确认后执行什么

1. **落骨架**——`project_scaffold.py` 确定性渲染模板（占位符替换由脚本保证并断言零残留）；全新创建带 `--require-empty` 防呆（目标非空则退出码 3，改走接入或换路径，不硬来）。骨架包含**项目 PRD 框架**（`.agents/skills/prd-style/`，出厂默认版；落点规则见上文 §接入会碰哪些文件）——写 PRD 的章节结构与风格以它为准，属于你的项目、可自由修改；接入时若你有 ≥3 份风格趋同的存量需求文档，节奏闸会提议按你的风格定制它
2. **建模块树**——`module_init.py` 按确认的树确定性落盘（骨架 + 索引段 + 反向索引，幂等可重跑）。**条件步**：仅当确认页拍定的是真实模块树时执行；无存量资料的全新项目展示的是示例树、不落盘，真实模块之后用 `/core:module-init` 一个个长出来
3. **（仅 B 路径）写入整合后的 CLAUDE.md 与 AGENTS.md**
4. **订阅 core**——由落骨架那一步的脚本一并写入，**两项声明缺一不可**：项目 `.claude/settings.json` 的 `extraKnownMarketplaces`（市场来源）与 `enabledPlugins`（启用位），只写后者会产出协作者装不上的项目。同时把这个市场补进用户级注册表 `~/.claude/plugins/known_marketplaces.json`（写不成只警告不阻断——项目级声明已够本机解析）。**这一步不依赖 `claude` CLI**（对话式入口 launcher 本身仍需要 Claude Code，见下文「Codex 门」能力边界的 launcher 一条）；装了 CLI 的机器也可以改用 `claude plugin marketplace add <源> --scope project` + `claude plugin install core@workframe --scope project`，两种写法等价
5. **git init + 首提交**——全新创建默认做（确认页可取消）；已是 git 仓则跳过 init 且不替你提交；没配 git 身份时保留 init、提示你补完
6. **逐模块深读与安置**——一次一个模块：深读该模块资料 → 确认安置与原件处置 → 当场执行（成品 md 收口引用；异构原料归位 `others/原始资料/`；大批量走「样板批 + 重启后批量迁移」逃生口；小模块合并进相邻一轮，单轮不超过约 10 份文件的决策量）
7. **节奏闸 + 整理归档段**（有原始资料批次时）——预估只给份数与需你拍板的决策点数（**不给时间**），你拍节奏与 git 提交策略；选当场做的批次逐阶段照归档 / 迁移 SOP 执行，每批完成汇报并给离场点
8. **（本机装了 `codex` 时）接 Codex 门**——`workframe-door --codex`，装机的一部分，**不再单问一层**：它改你的**用户级** `config.toml`（本插件 hook 的信任 ＋ 本项目的 trust ＋ 本插件 `enabled` 改为 `false`）与项目 `.codex/`，并**代你信任本插件的全部 hook，受信 hook 脱离沙盒执行**；以后插件升级时，未受信的本插件 hook 会在开 Codex 会话时自动补种信任、不再单问（见 [concepts.md](./concepts.md) §用户级）。这一步的事前授权在结构闸那一页的动作清单里（它在那里单独占一行、连同上面这句披露），执行时逐条列出要信任哪几条、写完报数。细节见下文 §Codex 门
9. **落盘验收**——doctor install 组：旧布局运行态目录（新建项目恒为通过；从上一版升级、没迁移的项目在这里报出，排第一）、骨架完整性、CLAUDE.md 导入与旧副本、项目 PRD 框架、项目 skills 链接形态、config 字段、订阅接线、AGENTS.md 契约段、旧镜像残留、`.gitignore` 必需条目、初始化断点、**初始化完整度**（推迟批次的落地状态）、可移植性、环境与 hook 活性。放在批次落定之后跑，「初始化完整度」读到的才是真实终态。依赖 hook 的项此刻显示「待首个会话后复查」是正常的，不是失败

全程**每完成一步立即记进 `setup-state.json`**——真断在中途时，进度已经在文件里，doctor 能告诉你装到哪一步；整理归档批次另有台账逐行记账，任何后续会话「继续初始化」精确续上。

收尾产出 `logs/creation-report.md`（报告落 `logs/`，不污染项目根），关键结论直接在响应里给你。

## 生成的项目长什么样

```
<project>/
├── CLAUDE.md                        ← 头部信息 + `@AGENTS.md` 导入 + 订阅声明 / 快速入门 / 框架指路
├── AGENTS.md                        ← 项目是什么 + 项目自有判据 + 项目级角色 + 路由偏好 + 业务目录 + 委派授权（两扇门共用；缺失时自动补建）
├── .workframe-config.json           ← project_name + project_type / dormant_profile / role_profile / close_check
├── .agents/
│   └── skills/prd-style/            ← 项目 skills 的真实源（两扇门共读）；PRD 框架出厂默认实例化在这里，随项目自由改
├── .codex/                          ← 装了 Codex 门才有：config.toml 的 managed 段注册 4 个角色 + roles/<role>.toml
├── .workframe/
│   ├── managed-files.json           ← Codex 门的产物账本（进 git）
│   ├── agent-memory/<role>/         ← shared + 4 角色记忆骨架
│   └── state/                       ← 运行时状态（gitignore，不进 git；memory-index.json 除外）
├── .claude/
│   ├── settings.json                ← 两项订阅声明（enabledPlugins + extraKnownMarketplaces）
│   └── skills                       ← 指向 .agents/skills 的目录链接（Windows junction / POSIX symlink），.gitignore 忽略它
├── projects/
│   ├── board.yaml                   ← 任务看板
│   ├── modules/overview.md          ← 模块树总图（modules/ 体系恒启用；首个模块用 /core:module-init 创建）
│   ├── specs/overview.md            ← 跨模块规范层
│   ├── specs/_meta/taxonomy.md      ← tag 受控词表（doc-graph-health 概念热点的词源）
│   ├── issues/TEMPLATES.md          ← Issue 模板（SEC / BUG）
│   ├── changelog.md
│   ├── dev-log.md                   ← 开发账本（每轮收口手写一条；收口检查 `[ledger]` 项读它）
│   └── evals/ proposals/ archive/   ← 骨架占位
├── company-context/README.md        ← 公司资料的起步目录（README 带敏感内容提示）
├── my-workspace/README.md           ← 个人产出的起步目录（同上）
├── logs/                            ← hook 输出 + 报告（gitignore）
└── <你的业务目录>                   ← 业务目录（`src/` 等）框架不预建，跟随你的实际脚手架
```

## 重启会话 + 首会话验收

初始化完成后必须**新开一个 Claude Code 会话**打开项目——hooks / agents 与必载纪律都在会话启动时加载。

重启后**屏幕是空白的，这是正常的**（hook 输出进 Claude 上下文，不显示在终端）。说句话，Claude 会转述首个会话的运行时验收结果——core 侧 hook 在首个会话自动复跑同一组检查，纯代码确定性，不依赖模型自觉。

## 生成后如何调整

生成的都是**起手稿**，鼓励直接编辑：

- 改角色职责 → `.claude/agents/<role>.md`（**基于插件版全量复制后修改**：项目这份被派到时就是它的全部定义，不与插件版合并。它与插件角色是**并存**、不是覆盖——插件角色全名带命名空间 `core:<role>`，仍在 agent 列表里；`@<role>` 派到项目这份，没点名时主会话按 description 挑，细节见 [concepts.md](./concepts.md) §项目级同名 agent：并存，不是覆盖）
- 加 / 删项目级 skill → 项目 skills 目录下增减目录：新建项目在 `.agents/skills/`（`.claude/skills` 是指向它的链接，两条路径读到同一份文件——链接失效或被复制成真目录时不再成立，会话启动与 doctor 的「项目 skills 链接形态」会报出来）；已装项目在 `.claude/skills/` 真目录
- 项目自己的判据 → 项目根 `AGENTS.md`；成套的工序 → 项目自己的 skill
- 调整业务目录 → 随意重组
- **改不了的（能力边界）**：通用角色表与路由规则、任务状态流转与签发权限、两套记忆分工由会话启动时的注入片投递，项目侧没有副本可改；需要项目级差异只能在 `AGENTS.md` 里**补充**（项目级角色、路由偏好），不能覆盖注入片

**项目里没有框架同步的只读文件**：必载纪律由会话启动时的 hook 直接注入上下文，不落盘；`AGENTS.md` 铺一次骨架之后就归你，框架不再覆盖它。

## Codex 门（同一个项目同时挂 Claude Code 与 Codex）

框架是一份源、两扇门：hooks、纪律片、角色、skills 都是同一套，Codex 侧只多一份派生的 hook 清单（Codex 清单 17 条）与一个装机器。**只在 Windows + codex-cli 0.153.x 上实测过**；macOS / Linux 的目录链接（symlink）路径未验。

### 装机：`workframe-door --codex`

在项目目录里跑（core 插件 `bin/` 下的命令；不在 PATH 上时直接跑插件目录下的 `scripts/workframe_door.py`）：

```bash
workframe-door --codex            # 默认市场源 owner/repo；本地目录 / git URL 用 --marketplace 覆盖
```

**开发机上一定带 `--marketplace <本地仓路径>`**：本机已经以本地目录登记过同名市场时，不带它跑，Codex 通常不会拒绝，而是把那条登记直接换成默认的公开源（`--codex` 在注册之前不读已有登记；只有 Codex 的克隆目录 `<CODEX_HOME>/.tmp/marketplaces/<名>/` 恰好残留着时它才拒绝），见下「能力边界」。

它按顺序做下面这些步，任一步不过都以非零退出码停下、**不打印「就绪」**：**预检**（写任何东西之前，见下）→ 核 codex 版本 → 注册市场 → **装前核对**（Codex 登记的市场里将要装的那一份与本命令是不是同一版本；不是就在 `codex plugin add` 之前停下，插件缓存与你用户配置里的 `enabled` 都不动，文案会说上一步是新登记了市场还是它早已登记；问不出、读不出版本时照常装，交给装后核对）→ `codex plugin add` → **装后核对**（Codex 侧实际装上的那份插件与本命令是不是同一版本、它的 hook 冻结清单是否完好；Codex 实际加载的那一份（从它列出的本插件 hook 的来源路径反推）也同样独立核一遍，两份不必是同一处；版本不同、任何一侧读不出版本、或反推不出实际加载的是哪一份，都在写项目文件之前停下，文案给出两个版本、两个插件根与出路。先写后核的是两类：`--backfill` 两扇门都缺时先写的 CC 那半，以及写之前 Codex 列不出本插件任何 hook 的情形（例如 `--upgrade` 遇到段里还没有启用表的项目）——那时失败文案会点名写过的文件；其中角色已写之后才因安装根停下的那几格（抬头步 3' 的 (a)(c)(d)(e)），还会说明此后本项目加载的是哪一份；判据与例子在 core 的 `scripts/workframe_door.py` 抬头步 3'）→ 生成角色（`.codex/config.toml` managed 段段首写本插件的项目级启用表）→ 读 `hooks/list` 对账条数 → **先打印**要信任哪几条、各自执行什么，以及要改你用户配置里的哪一键 → 一次写入 hook 信任 ＋ 项目 trust ＋ 用户层 `[plugins."core@workframe"] enabled = false` → 回读（用户层是 false、从项目看是 true、hook 全 `trusted`）→ 写种信任记录 → 孤儿信任记录只计数。写完后明说「已代你信任 N 条 hook」与「已把用户配置里的 enabled 改为 false」。Codex 的 hook 默认不受信任、一条都不跑，这一步替你批准的是**本插件自己的那些 hook**（受信 hook 脱离沙盒执行）；项目 trust 也一并代写（`[projects.'<路径>'] trust_level = "trusted"`），因为项目层 `.codex/config.toml` 只在 trusted 时才被 Codex 读到。

**按项目启用**：`codex plugin add` 会把本插件在你的用户 `config.toml` 里设成 `enabled = true`——那样任何目录的 Codex 会话都会加载它。门把用户层那一键改成 `false`，插件只在跑过 `--codex` 的项目里（managed 段段首的 `[plugins."core@<市场名>"] enabled = true`）加载。所以**每台机器上的每个项目都要各跑一次 `--codex`**；`codex plugin list` 在没装过的目录里显示 `enabled: false` 是预期。

**预检**：门先把 managed 段拼进项目 `.codex/config.toml` 的当前内容、按整份文件的真实语义判一遍，命中下列任一就以 `3` 停下，项目文件、你的用户配置、插件缓存一个字节不动：段外有与段内同名的声明（例如自己写的 `[plugins."core@workframe"]`——同一张表出现两次，Codex 会拒绝加载整份项目配置；段外的子表 `[plugins."core@workframe".x]` 也算，严格是有意的）；写在 END marker 之后又没有表头的键（按 TOML 语义它们属于段内最后一张表，Codex 会整份拒载或把你的值静默吞掉——给它们加上所属表头）；文件本身解析不了；managed 段被手改过；本项目的 Codex 门已被你关闭（见下「两门并存与切换」）。

退出码：`2` 环境（codex 不在 / 版本低于 0.153.4 / 市场或插件命令失败 / app-server 起不来 / Codex 登记的市场里将要装的那一份与本命令不是同一版本（装前核对，插件还没装） / Codex 侧装上的插件与本命令不是同一版本或读不出版本 / 它的 hook 冻结清单缺失或形态坏 / 反推不出 Codex 实际加载的是哪一份（hook 没有来源路径，或各条来源不是同一处） / 门自己的插件根读不出版本——这一格开跑即停，什么都不动）、`3` 预检或对账（上面的预检各项；条数不等、某条 `command` 为空、角色产物不符；`--upgrade` 遇用户层未启用且项目未 trust）、`4` 写入被拒（服务端明确拒绝 ⇒ 未写），或没等到答复（超时 / app-server 退出 ⇒ 未确认是否写入，跑 `--check` 看 hook 是否已 trusted）、`5` 回读不符（仍有未受信、项目 trust 未生效、角色注册回读不过、用户层没变成 false、从项目看没变成 true；`--check` 时任一项不过，含用户层 `enabled = true`）、`6` 验活未见侧效应。在 `codex plugin add` 之后、写入之前失败时，用户层停在 `true`，失败文案会明说——修复后重跑 `--codex`。

**装完要重开这个项目的 Codex 会话才生效**：Codex 在会话启动时加载插件、hook 与角色，已经开着的会话不会补载（同一会话里刚被信任的 hook 也不跑，实测）。

### 装完之后

- **`workframe-door --check`**：只读对账——用户层 `enabled` 是否是 `false`（是 `true` 算问题：最常见成因是裸敲过 `codex plugin add`，重跑 `--codex` 即可；用户层整张表都没有算正常）、hook 是否全 `trusted`、项目 trust、角色注册是否被 Codex 读到、种信任记录的版本是否等于已装版本、Codex 侧装上的插件与本命令是不是同一版本（不同或任何一侧读不出都算问题）、它的 hook 冻结清单是否完好（缺失或坏了算一项问题，其余各项照查）、Codex 实际加载的那一份（从 hook 的来源路径反推）是不是同样对得上（反推不出也算问题，不拿门自己的那份顶上）、`multi_agent` 特性是否开着；本项目的门已被你关闭时报一句「已关闭」、不算问题；同项目若有多于一个会话标记文件会提示一句（上一个会话没跑到 SessionEnd 时会留下，7 天后自清）。过滤后 0 条时按读得到的状态说成因（用户层未启用且项目未 trust / 段内没有启用表 / 已关闭），分不出时把 `hooks/list` 的 `warnings` 原文打出来——那时最常见的成因是清单里某条缺 `command` 键、整份被静默丢弃。
- **门的成功出口会说 `AGENTS.md` 的状态**（`--codex` / `--upgrade` / 缺省 / `--check` 成功时，`--backfill` 结束时 Codex 门在项目侧接着时——本次装上的或原本就接着的；不改退出码）：Codex 读 `AGENTS.md`、不读 `CLAUDE.md` 与 `.claude/rules/`，门装好不等于 Codex 看得到项目内容。「这个项目是什么」还是框架占位、或项目根没有 `AGENTS.md` 时打一行 `!`；`CLAUDE.md` 有模板之外的二级段、或 `.claude/rules/` 下有 md 时打一行 `i`，列出这些只有 Claude Code 读得到的内容面。归位按 core 插件 `reference/claude-md-merge-guide.md`。
- **`workframe-door --check --live`**：再真跑一次零凭据的 `codex exec` 验活（用本机回环上一个立即回 400 的桩当模型，模型请求不出本机，几秒内结束；验活会话仍会加载你 Codex 配置里的其余外连能力——实测见过一次向外的 MCP 传输连接，来源未查明）。Codex 仍会把它记成一次失败的会话——知情项。静态对账有问题时走不到验活。
- **`workframe-door --backfill`（已装项目回填）**：装机链路只在**装 / 接入项目那一刻**把两扇门都接上（新建与接入都走，见 launcher `setup` §6 收尾），此前装的项目不会自己长出第二扇。这条命令先看两扇门此刻各是什么状态，**只补缺的那扇**：
  - CC 门缺 → 写两项订阅声明 ＋ 用户级市场注册表（与装机时同一段代码：merge、先备份到 `logs/`、写完回读）；
  - Codex 门缺 → 走一遍上面 `--codex` 的全流程（本机没装 codex 就跳过，不算失败）；
  - **两扇都在 → 一个字节不写**，重跑安全。这里的「在」**只看项目文件**：Codex 门的 hook 信任、项目 trust、用户层 `enabled` 写在各人的用户配置里、不随仓走，本命令不核——所以收尾说的是「两扇门项目侧已接；Codex 门本机未核，跑 workframe-door --check」，不说「都已接上」（clone 到第二台机器时 `--check` 会报不过，出路是在那台机器上跑一次 `--codex`）。
  - 市场来源**只从两扇门已有的注册面读、不猜**（先问 CC 的 `known_marketplaces.json`，再问 Codex 的市场注册面）；读不到就停下告诉你，不拿猜的值写一份会进 git 的声明。Codex 那一侧只认本地目录源——别的源形态（例如 git 源，Codex 把它登记成完整的 https URL）该改写成项目 settings 里的哪一种声明没有验过，它会把原值念给你、让你自己定。
  - **不改你自己关掉的 Codex 门**；managed 段被手改过时停下报告、不覆盖。**不含任何删除动作。**
  - **补完要重开会话才生效**：补了哪扇门，就重开这个项目在那扇门里的会话（补的是 CC 那扇就重开 Claude Code 会话，补的是 Codex 那扇就重开 Codex 会话）。
- **第二台机器 / clone 之后**：hook 信任、项目 trust、用户层 `enabled = false` 都写在各人的 Codex 用户配置里、不随仓走——在那台机器上对这个项目跑一次 `--codex`。直接跑 `--upgrade` 会在写项目文件之前停下，并给出这条出路——但停下之前 `codex plugin marketplace upgrade` 已经跑过，插件缓存已刷新（等同你裸跑了一次那条命令；项目文件与用户配置不动）。**这一段只讲 git 源**：本地目录源下 `--upgrade` 走的是下一条说的重装路径，用户层会被写回 `true`，于是不会停在这里、照常装完。
- **升级**：`workframe-door --upgrade`（重生成角色 ＋ 补种信任；刷插件缓存那一步**按市场源形态分两条路**）。**前提：门本身得已经是目标版本**——`--upgrade` 先刷新 Codex 侧的缓存，再拿门自己这一份去核对它；门比刷新后的 Codex 侧旧时以 `2` 停下（缓存已换成新版，hook 信任没动；项目文件通常也没动，但段里还没有启用表的项目，角色可能已先写——写没写、写了哪些，停下时的文案会如实点名）。所以顺序是：先升级门所在的那一侧（CC 门：`claude plugin update core@<市场>`），再跑 `--upgrade`；只装了 Codex、从插件目录直接跑脚本的（门在 Codex 安装缓存的版本目录里，或在 git 源的市场快照里），停下后按文案用新版本目录下的 `scripts/workframe_door.py` 带同样的参数重跑——门开跑时就记下了自己的版本，这次升级即使删掉了它所在的目录、或把它原地刷新成了新版，也照样会停下、并点名新目录下的门，文案还会说明那个目录此刻已不是正在跑的那一份。**升级完重开这个项目的 Codex 会话才生效**（Claude Code 那侧同理，见下文「各自的升级路径与生效时机」）。
  - **git 源** → `codex plugin marketplace upgrade`，**不改用户层的 `enabled`**。
  - **本地目录源**（框架开发者、或用 `marketplace add <本地路径>` 装的） → 那条命令只认 Git 市场，对它恒失败（`not configured as a Git marketplace`）。门改走 `codex plugin add core@<市场>` 重装（重装之前同样先过装前核对：本地仓里那一份与门的版本不同、且读得出时就在重装之前停下，用户层不会被写回 `true`；本地仓比门新时，出路点名本地仓里自带的门）——实测是整体重物化：缓存里被你改动过的部分换回原样。**代价：这一步会把用户层 `[plugins."core@<市场>"] enabled` 写回 `true`**，门在写入那一步再置回 `false`；中途失败时它停在 `true`，失败文案会明说，修完重跑即可。
  - 源形态拿不到时（`codex plugin list --json` 跑不通）门先试 git 那条，被拒再落到重装。

  **绕过它直接敲 `codex plugin marketplace upgrade` 也行**（这条只对 git 源成立）：改动过的 hook 会变成 `modified` 而停跑，但下一次 Codex 会话里的种信任 hook（`SessionStart` 上命令行冻结的那一条）会检测到并直接补种、在会话里明说「框架已升级，已代你信任 N 条 hook」——**下次会话生效**（同一会话内刚补种的 hook 不跑，实测）。它自己第一次装上时也是 `untrusted`，由 `--codex` 一并种上。哪些改动会让 hook 失信任：`type` / `command` / `commandWindows` / `matcher` / `additionalContextLimit` / `timeout` / `statusMessage` / `async` 任一变、或在清单中间插条目；只改脚本正文、纪律片、版本号不会。
- **坏发版**：`--upgrade` 里刷缓存那一步（两条路都是）先把本地缓存换成新版、门才对账——对账不过（exit 3）时缓存已是坏版、门没有回滚，只能等下一个好发版，或手动 `codex plugin marketplace add` 一个旧源。装后核对不过（exit 2：新缓存与门的版本对不上，或它的 hook 冻结清单缺失、坏了）时同样是缓存已换、门没有回滚；其中版本对不上多半不是坏发版，而是门落后了，照上一条的前提与顺序处理。
- **Windows 深路径**：`CODEX_HOME` 路径很长时 `marketplace add / upgrade` 会撞 MAX_PATH（git 报 `Filename too long`）。门给自己起的 codex 子进程开了 `core.longpaths`；**你自己裸敲 `codex plugin marketplace add/upgrade` 没有这一层**，撞上就是 exit 128 且提示只说 clone failed——给 git 全局开 `core.longpaths=true`，或把 `CODEX_HOME` 挪短。
- **默认市场源** `owner/repo` 形态：对 GitHub 公开仓（无鉴权的只读拉取）实测过注册市场、装插件与 `--upgrade` 刷新；需要鉴权的远端没验。装不上就 `--marketplace` 指 git URL 或本地目录。

### 角色与项目配置

- 4 个角色由插件 `agents/*.md` 生成到 `.codex/roles/<role>.toml`，并在 `.codex/config.toml` 的 managed 段（两行 marker 之间；段首是本插件的项目级启用表）注册。**定制角色写 `.codex/roles/<role>.local.toml`**（`developer_instructions = "…"`），生成时追加在正文之后；直接改生成的 `<role>.toml` 会被下次装机 / 对账当成「被手改」跳过并报出来。
- **你自己已有 `.codex/config.toml`**：无 marker 时门只在末尾追加 managed 段、段外一个字节不动；有 marker 但产物账本里没登记它（例如账本被删）时门拒绝写任何文件，文案给两条出路（恢复账本 / 把两处 marker 连同段一起删掉后重跑）。
- 产物账本 `.workframe/managed-files.json` 记录门写过的每个文件与 sha，**有意进 git**——它是「哪些文件是生成的」的唯一记录，将来卸载只删账本内且未被改过的文件。

### 能力边界（写在这里免得你把绿读过头）

- **插件按项目启用**：跑过 `--codex` 的项目才加载本插件（见上「按项目启用」）。用户层 `enabled` 被写回 `true` 之后（裸敲一次 `codex plugin add` 就会），任何目录的 Codex 会话都会加载它——这时本插件的 hook 在会话目录向上找不到 `.workframe-config.json` 的地方**直接早退**（零写入、零输出），不建 `.workframe/state/`、不补 `AGENTS.md`、不注入；`--check` 会把用户层 `true` 报成问题。代价：每个挂载点仍会各起一个 python 进程再退出（零凭据会话实测会话启动与一次提问共 8 条、比用户层 `false` 时多约 2.5 s）。早退只看会话目录向上有没有标记、**没有上限**：家目录这类祖先目录带标记时，其下所有目录都算「在项目内」（实测：祖先目录带标记时，子目录会话的状态写进祖先目录）。Claude Code 门没有这道早退——若把 core 装在 Claude Code 的用户作用域，它在非 workframe 目录同样会建状态目录（按代码推断，未实测）。
- **子目录、非 git 项目、嵌套仓**：git 仓里的项目，从任何子目录起的 Codex 会话都加载本插件，状态统一写到项目根；**非 git 仓的项目只在项目根起的会话加载**（子目录会话里 Codex 读不到项目层）；项目里内嵌的独立 git 仓不加载（Codex 的项目层在最近的 `.git` 截止）——但用户层 `enabled` 被写回 `true` 时它会加载，hook 向上找到外层项目的标记，状态与注入都记在**外层项目**名下（实测）。
- **签发把关**：`signoff-guard` 在 Codex 门下实测不起把关作用——用 `apply_patch` 改看板时不拦也不报，只有经 shell 改看板那条路径有提示（见本节下面 `PostToolUse` 那一条）。自签照样生效，四段举证纯靠自觉；关法见下一节「自签档默认开启」。
- **项目 skill 的受保护前缀两个都认**：链接形态下 git 只报 `.agents/skills/…`，在 Claude Code 里经链接编辑时路径是 `.claude/skills/…`——签发把关与 doctor 的受保护资产对账两个前缀都认。两者都只是提示级，不封顶到用户拍板。
- **装后核对只比版本串，`--codex`（及缺省模式、launcher 装机）在注册市场之前也不读已有登记**（`--backfill` 没给 `--marketplace` 时会先读已有注册面，不在此列）：本机已经以本地目录登记过同名市场、这次不带 `--marketplace` 跑时，只要 Codex 的克隆目录 `<CODEX_HOME>/.tmp/marketplaces/<名>/` 不在，Codex 对「已登记本地源、这次加 git 源」就不拒绝，直接把登记换成默认公开源，随后插件被装成公开那一份（克隆目录残留着时这一组合同样被拒；其余先后组合在已登记的那一份仍然有效时会被拒；本地目录被挪走、或 git 源的克隆目录被删之后，其余组合也会被直接换掉）。公开那一份与门的版本不同、且读得出版本时，装前核对在 `codex plugin add` 之前停下——此时登记已换，插件缓存与用户层还没动。**这一态下，本机其他已装过本插件的项目里 `workframe-door --check` 照样全绿**（它不核市场登记；停下的那个项目本身还没装完，在它里面跑 `--check` 会报问题），**而此后在本机任何项目里跑一次 `--upgrade`，都会按这条公开源登记把全机共用的插件缓存换成公开那一版**（git 源那一路不经过装前核对，换完才由装后核对停下）——所以停下之后先照文案 remove 再带 `--marketplace` 重跑，别先跑 `--upgrade`。**版本相同时（例如某版发布之后、下一次 bump 之前，框架开发版与公开版的版本串相同）装前、装后核对都分辨不出，会静默装成公开那一份并报就绪**。要装本地那一份，就显式给 `--marketplace <本地仓路径>`；登记已经被换掉的，先自己跑 `codex plugin marketplace remove workframe`（会删掉你 Codex 用户配置里的那条市场登记），再带 `--marketplace <本地仓路径>` 重跑。
- **Codex 读 `AGENTS.md` 的两处边界**：项目根有 `AGENTS.override.md` 时 Codex 只读它、不读 `AGENTS.md`；`AGENTS.md` 超过约 32 KiB 的部分会被 Codex 静默截掉（实测读数，官方文档未核），排在末尾的段先丢。doctor 看不见这两种情形，照样报四段齐全。
- **两扇门各自升级**：一边升了一边没升时会各自执行不同版本的脚本；doctor 的「环境与 hook 活性」项读两份 `plugin-root.<门>.txt` 对账版本，不等报 error。
- **`disable-model-invocation` 在 Codex 门下不生效**：它是 Claude Code 的 skill frontmatter 字段，Codex 的 skill 规范不认它（Codex 侧的对应物是各 skill 目录下的 `agents/openai.yaml`，本框架没有写）。于是本插件里带这个字段、只该由你显式触发的那几个 skill（`rollback`、`onboard`、`audit`、`maintenance-review`、`memory-log`）在 Codex 门下对模型可见；按 Codex 的 skill 规范推断它们可被隐式调用，**未实测**（`disable-model-invocation` 是 Claude Code 语义，Codex 侧没见到对等字段生效的记录）。当前决定是先忽略、不做门差补偿；要不要补是产品决策。
- **launcher 装成 Codex 插件这个形态没验过**：本文讲的 Codex 门全部围绕 core——`workframe-door` 是 core 插件带的命令。launcher（装机入口）在 Codex 下能不能被发现、装上之后它的 skill 正文与 `reference/` 相对引用能不能被读到、`allowed-tools` 这类 Claude Code frontmatter 在 Codex 的语义是什么，**都没有真机结论**。所以本文没有「在 Codex 里装 launcher」的步骤，**对话式的建项目 / 接入目前需要 Claude Code**。装机脚本本身不依赖 `claude` 命令（见 §确认后执行什么 步骤 4）：没有 Claude Code 的机器，可以照根 README「无人值守 / CI 初始化」一段手工跑同一批脚本——那条路没有确认页，也没在只装 Codex 的机器上完整走过一遍。
- **`PostToolUse` 四条 Claude Code matcher 在 Codex 的实测命中面**（Windows、codex-cli 0.153.4；一次带凭据的会话，再按同一载荷直调脚本复证）：
  - `Bash` → `signoff-guard`：**命中**，行为与 Claude Code 相同（经 shell 改看板时只提示、不拦）
  - `Edit|Write|NotebookEdit` → `check-stale-modules`、`signoff-guard`：**matcher 命中，但两个脚本都空转**。Codex 改文件走 `apply_patch`：载荷里 `tool_name` 是 `apply_patch`、`tool_input.command` 是 patch 文本、没有 `file_path`；`check-stale-modules` 只认 Claude Code 的编辑工具名，`signoff-guard` 取不到 `file_path`，两个都直接返回。后果：**Codex 门下用 `apply_patch` 改看板，自签把关不拦也不报**；改到模块 `code_paths` 覆盖的文件时不会当场标 stale，要等下一次会话启动时的 git 改动补扫
  - `Skill` → `log-skill-invoked`：**不命中**——Codex 没有 Skill 工具（模型用 shell 读 `SKILL.md`），`skill_invoked` 事件在 Codex 门下不产生
- 需要凭据才能验的：`spawn_agent` 子线程上 `agent_type` 是否等于角色名（角色记忆按它命中）、TUI 启动审查页与 `SessionStart` 的先后。

### 安全删法：`.claude/` 里的 `skills` 是链接

新建项目的 `.claude/skills` 指向 `.agents/skills`。本机实测（Windows 11，PowerShell 5.1.26100）`rmdir /s /q`、`Remove-Item -Recurse -Force`、Git Bash `rm -rf`、Python `shutil.rmtree` 删 `.claude/` 整目录或直接删链接本身，**都不穿透**——`.agents/skills/` 原样留着（`rmtree` 对链接本身拒删，其余三种删掉链接、留下目标）。更老的 PowerShell 版本有穿透到目标的记载，删之前先 `dir /a .claude` 看它是不是 `<JUNCTION>`，拿不准就 `rmdir .claude\skills`（不带 `/s`，只解链接）再删其余。只删了链接、`.agents/skills/` 还在时，下一次会话（两扇门都算）会把链接补建回来并打一行提示，Claude Code 门同时请求在本次启动后重扫 skill 清单（若那个会话里仍看不到项目 skill，执行 `/reload-skills` 或重开会话）——这与 clone 后首个会话补建是同一条路径。Windows 上把项目整体改名或移动后，junction 仍指着旧位置：下一次会话会把这根目标不存在的链接解开、重指到本项目的 `.agents/skills/`（只解链接本身，不碰任何目录）。反过来只删了 `.agents/skills/`、链接留着时不会自动补，每次会话打一行提示，装机脚本重跑会报「链接悬空」并给出路。删掉 `.claude/` 后 Codex 门照常工作（hooks 与纪律不依赖它）；删掉 `.codex/` 后 Claude Code 门照常工作——两次都要留着 `AGENTS.md`，它是两扇门共读的项目判据。

### 两门并存与切换

**共享的（同一份，两门都读写）**

- `projects/`（看板、模块树、账本）与 `AGENTS.md`（两门共读的项目判据；Claude Code 经 `CLAUDE.md` 的 `@AGENTS.md` 导入读到它，Codex 原生读）。
- 运行态目录：`.workframe/state/`（角色记忆在 `.workframe/agent-memory/`）。两门经同一个定位函数落到同一处，所以 `events.jsonl`、注入记录、会话标记都只有一份，两门混写同一份 `events.jsonl`，靠每行的 `harness` 字段区分来自哪扇门。上一版装的项目运行态还在 `.claude/workframe-state/`（`.claude/agent-memory/`），**框架不再读它**——开会话之前先按下面「已装项目迁移」搬过来。
- `.workframe/managed-files.json`：Codex 门生成物的账本，进 git。

**各自独有的**

- Claude Code：`.claude/settings.json`（订阅声明 `enabledPlugins` ＋ `extraKnownMarketplaces`）；`.claude/skills`——新建项目是指向 `.agents/skills/` 的链接，已装项目是真目录。
- Codex：`.codex/config.toml` 的 managed 段 ＋ `.codex/roles/<role>.toml`（由插件 `agents/*.md` 生成，见上「角色与项目配置」）。
- 插件根记录：`plugin-root.<门>.txt` 两门各一份（`plugin-root.txt` 是兼容位，存最后一个写的那门）；doctor 对账两根的插件版本。

**只用一门时怎么关另一门**

- 关 Claude Code：从 `.claude/settings.json` 的 `enabledPlugins` 里去掉 `core@workframe`。`.codex/`、`AGENTS.md` 与运行态目录都不要动。
- 关 Codex：把项目 `.codex/config.toml` managed 段段首那一行 `enabled = true` 改成 `enabled = false`——只改这一行、只改值，**不要在行尾加注释**（`enabled = false  # …` Codex 照样按关闭处理，但门认作「段被手改」、`--codex` 以 `3` 停下）。门认得这个状态：`--check` 报「本项目 Codex 门已被你关闭」、不算问题；`--codex` / `--upgrade` 以 `3` 停下、不会把它改回去。要重新开启就改回 `true` 后重跑 `--codex`。**不要**在段外另写一张 `[plugins."core@workframe"]`：同一张表出现两次，Codex 会拒绝加载整份项目配置。**用户层卸载或禁用的命令本文未实测，不写具体命令**。
- 要删目录，先读上一节「安全删法」。

**各自的升级路径与生效时机**

- Claude Code：按它的插件更新入口更新之后，**hook 清单与角色定义要 `/reload-plugins` 或重启会话才生效**；hook 调用的脚本与 skill 正文在下一次被调用时就是新版。
- Codex：`workframe-door --upgrade`（刷插件缓存 ＋ 重生成角色 ＋ 补种信任；刷缓存那一步按市场源形态走 `marketplace upgrade` 或重装，两条路与各自代价见上「装完之后」的升级一条）——门本身得已经是目标版本，先升门所在的那一侧再跑它，否则它在核对时以 `2` 停下（前提与顺序同见那一条）；git 源也可以直接 `codex plugin marketplace upgrade`——改动过的 hook 变 `modified` 而停跑，下一次 Codex 会话里的种信任 hook 自动补种，**再下一次会话生效**。
- 一边升了一边没升：两门各跑一个版本；doctor 读两份 `plugin-root.<门>.txt` 报 error。
- 上一版装的项目（运行态还在 `.claude/` 下）：两门一样，升级后先按下面「已装项目迁移」迁移、再开会话。

**两门不共享的**

- 主会话记忆：Claude Code 用 auto-memory（官方记忆目录），Codex 用它自己的原生机制，两边互不可见。角色记忆（`agent-memory/`）是框架自有机制，两门共用一份。
- 模型手写的事件（`skill_used`、`user_correction` 等）：写入指令随注入的纪律片投递。Codex 侧主线程片头带一行 `本会话 harness=codex session_id=…`，`workframe-event` 另有会话标记文件兜底。事件注册表（`.workframe-meta/event-schema.json`）里 `__delivery_established__.codex` 仍是 `false`：它表示「Codex 门下模型照做写事件」这一环还没被观察到——Codex 侧这类事件为零时先怀疑投递，再怀疑模型。

**两门对等怎么验**

- 静态：`tools/validate.py` 的 `hooks_twin_parity`（两门 hook 清单在事件 × matcher × 脚本 × 固定参数上对等）与 `role_twin_parity`（角色生成器对 `agents/*.md` 的渲染 == 仓内样本）。**验的是声明面**：`PostToolUse` 四条 hook 的 matcher 是 Claude Code 的工具名，它们在 Codex 的命中面不在这两道闸的视野内（实测结论见上「能力边界」里 `PostToolUse` 那一条）。
- 动态：`tools/twin_parity_probe.py`（框架仓根工具，不随插件分发）比对两门在同一项目里的 scope 级注入指纹、角色记忆注入产出与事件类型集合。需要两门凭据，由维护者跑；配方见脚本的 `--recipe`。

## 升级到含「四段闸门」的版本：已装项目要做什么

**这一版改了一个架构不变量**：`pending_qa → completed` 从「无条件仅 @qa」变成
「角色侧仅 @qa，主 Claude 另有一条走四段闸门的自签通道」。**升级后这条通道会自动生效**
——请先读第 1 条再决定要不要关掉它，另外两件也值得过一遍。

**升级之后重开这个项目的会话才生效**：新契约随会话启动时注入的纪律到达，已经开着的会话仍按旧版；上一版装的项目（运行态还在 `.claude/` 下）先按下文「已装项目迁移」迁移、再开会话。

### 1. 自签档默认开启——不想要就显式关掉

`.workframe-config.json` 里**没有** `signoff` 键 = **开着**。要关：

```json
"signoff": { "self_signoff_enabled": false }
```

**升级会让这条通道对你的项目生效，你不需要做任何动作。** 这是有意的：一个要先发现
某个配置键才生效的能力，对多数项目等于不存在。但它的代价请你知情后再决定：

- **主 Claude 在收口时可以自己签发**（`pending_qa → completed`），不再必须调度 @qa。
  降档不是无条件的——要拿得出「覆盖本轮改动的独立结论」，档位还受改动爆炸半径卡下限、
  命中六条否决项之一则一律抬到找你拍板；写松了会被 hook 当场拒绝。
- **那道 hook 在 Codex 门下实测不起把关作用。** 它挂在 `PostToolUse` 上、按工具名匹配
  （`Bash` / `Edit|Write|NotebookEdit`）。Codex 改文件走的是 `apply_patch`，载荷里没有 `file_path`，
  hook 进程起了却取不到要查的文件、直接放行——**用 `apply_patch` 改看板，自签写松了不拦也不报**；
  只有经 shell 改看板那条路径有提示（与 Claude Code 一样只提示不拦）。所以那一门下自签照样
  生效，四段举证纯靠自觉（Windows、codex-cli 0.153.4 实测，细节见上文「Codex 门」的能力边界）。
  **在 Codex 下用本框架、又对签发质量敏感的项目，建议显式关掉。**
- 校验还有两处看得见的边界：用 shell 命令（脚本 / `sed` / 重定向）改看板时它只提示不拦；
  历史上写成一段散文的签发记录不进把关面。

关掉之后，看板里写 `qa_signoff.tier: 自签` 会被 hook 拒绝，红灯会告诉你要改的是配置
而不是那一行档位。

### 2. 检查 `shared/MEMORY.md` 里有没有一条过期的 `[纠正]`

老版本的模板在**示例注释里**给过这么一条：

> `[纠正] …：研发任务必须流转 in_progress → pending_qa → completed，@dev 不能直接标记 completed`

**全新装机不会把它带成活条目**（它一直在 `<!-- -->` 里，本版实测确认）。有风险的是
**照着示例把它抄进正文的项目**：`shared/MEMORY.md` 是应用层最高权威、`[纠正]` 条目
永不清理，那条会以最高优先级压过新契约。

```bash
grep -n "\[纠正\]" <你的项目>/.workframe/agent-memory/shared/MEMORY.md
```

命中且内容是上面那条 → 按 `correction-detection` 的 supersede 流程更新它
（保留最新、旧的移 `shared/notes.md` 留档）。**框架没有任何机制能替你改它**，
这是有意的：那个区域的写入权只在用户手里。

### 3. `CLAUDE.md` 里的框架段落：旧副本报 warn，缺 `@AGENTS.md` 导入报 error

本版起，出厂 `CLAUDE.md` 里原来那几块框架段落（通用角色表与路由规则 / 任务状态流转与签发权限 /
两套记忆分工与落点判据 / 文档约定与收口清单）改由会话启动时的注入片投递，自签约定跟着
流转表一起进了注入片。已装项目 `CLAUDE.md` 里的那几块因此成了旧副本：

- `workframe-doctor` 对旧副本报 **warn**（与注入片形成双份：注入片随插件升级，副本不会）。
  逐块确认后从 `CLAUDE.md` 删掉；夹在里面的项目自有内容（项目级角色、项目特殊约束等）
  先挪进 `AGENTS.md`。删除要你本人确认，框架不替你删。
- **`CLAUDE.md` 里的项目内容不会自动进 `AGENTS.md`**：上一版把项目目标 / 业务背景 / 项目特殊约束写在
  `CLAUDE.md` 里（前两段不在框架段落里面，上一条管不到它们）。升级后的第一个会话（或 `--migrate`）补建出来的
  `AGENTS.md` 是占位骨架，「这个项目是什么」一段写着 `（待填）`——而 Codex 读 `AGENTS.md`、不读 `CLAUDE.md` 与
  `.claude/rules/`。按 core 插件 `reference/claude-md-merge-guide.md` 逐段归位（别把 `CLAUDE.md` 整段复制过去，
  那会把框架段落的旧副本一起带进去）；没归位之前 `workframe-doctor` 的 `agents_md` 项报 **warn**，Codex 门装好时
  也会提示。改 `AGENTS.md` 要你本人确认，模型不代填。
- `CLAUDE.md` 里没有一行有效的 `@AGENTS.md` 导入时报 **error**：CC 只读 `CLAUDE.md`，
  `AGENTS.md` 里的内容（含今后按信号入账写进去的通用规则）在 CC 会话里读不到。
  修法是加一行 `@AGENTS.md`——纯新增、不动原文。

### 回滚

**只想回到「只有 @qa 能签」**：不必回退插件版本，把自签档显式关掉即可（`.workframe-config.json` 里写
`"signoff": { "self_signoff_enabled": false }`，见上）。下面的逆序 revert 只适用于从源码仓跟踪开发分支、
要把代码层面整体退回去的维护者。

这是一次跨多个提交的契约变更，回滚必须**逆序**且不得跳过任何一个承载提交。
**提交清单现算，不许手抄**——手抄漏掉一个，逆序 revert 链会静默跳过它，
而各道闸照样全绿、失效不可观测：

```bash
# 列出区间内的全部提交及**各自触碰的文件**。`git log` 默认就是新→旧，
# 而那正是 revert 要走的顺序——别加 `--reverse`（会把顺序倒过来）。
git log --oneline --name-only <上一个发版 tag>..HEAD
```

**不要给它加 `-- <路径>` 过滤。** 这一条是踩出来的：本文档初版给的正是一份带七条手写
路径的过滤命令，它**在形式上是「现算」的，缺陷只是上移了一层——路径面本身是手抄的**。
实测两个方向同时错：

- **超选**：路径面里含 `tools/validate.py`（全仓改动最频繁的文件之一），把区间内几乎
  所有提交都捞了进来，**返回 66 个提交**而不是该批的那几个
- **漏批**：改「消费方 skill 细则」的那一批触碰六个文件、**一个都不在那七条路径里**；
  只改对外 `CHANGELOG.md` 的那一批同样不在 ⇒ 命令会**静默漏掉整整两批**

⇒ **过滤面只要是手写的，它就会漏掉你当初没想到的那一批**，而那正是「提交清单必须
现算」这条规矩要防的东西。宁可多列几个提交让人自己挑，也不要一份看起来精确的漏清单。

回滚后 `python tools/validate.py` 必须重新全绿。**但别把这句读成「漏了会报红」**：
成对的断言（A2/A3、`veto_list_alignment`、`signoff_consumers_carry_pointer`）能抓到
它们各自那一半被回滚，**纯文本细则那几批没有对应的闸**——只改 `CHANGELOG.md` 的那一批
回滚漏掉时，至今没有任何东西会报红。

## 已装项目迁移：`workframe-door --migrate`

升级前就装好的项目，运行态还在 `.claude/` 下，项目 skills 是 `.claude/skills/` 真目录。迁移把它们搬到两扇门共用的中立位置，并顺手收掉同一批升级遗留。

**先迁移，再开这个项目的会话。** 框架只读写新位置、不回退到旧目录：没迁移就开会话，hook 会在新位置从零另建一套——会话照常起，但计数从零、角色记忆注入为空，旧目录里的状态与记忆此后没有读者；再去迁移时，迁移工具对已经两边都有的那一类判冲突、不动。首个这样的会话里安装验收会报 `[legacy_state_dir]`，照它的出路走，**别照同一次验收里其余缺项的提示重跑装机脚本**（那会在新位置再建一套，把冲突扩到记忆那一类）。

| 旧位置 / 旧状态 | 迁移后 |
|---|---|
| `.claude/workframe-state/` | `.workframe/state/` |
| `.claude/agent-memory/` | `.workframe/agent-memory/` |
| `.claude/memory-archive/` | `.workframe/memory-archive/` |
| `.claude/skills/` 真目录 | `.agents/skills/` 真目录 ＋ `.claude/skills` 指向它的目录链接（`.gitignore` 忽略链接） |
| `.gitignore` 缺新位置的条目 | 补上（有 Workframe managed marker 就重写 marker 之间，没有就在文件尾追加；行尾与 BOM 保持原样） |
| `rollback-index.json` 里的旧前缀路径 | 换成新前缀（先存原件） |
| `CLAUDE.md` / `AGENTS.md` / 项目 skills 下的 md 里的旧运行态路径 | 换成新路径（先存原件；文件要先提交；只换项目相对的写法与 `$CLAUDE_PROJECT_DIR/…` 一族，`./…`、`~/…`、`$HOME/…`、`<项目根>/…` 这类占位、绝对路径、别的项目的路径原样不动；改写之后仍留着的旧路径 plan 里单列）；其余文件里的只列出来，不改 |
| `CLAUDE.md` 缺 `@AGENTS.md` 导入 | 末尾加一行（`AGENTS.md` 不在就先按模板建出——建出来的是占位骨架，`CLAUDE.md` 里的项目内容**不搬**；plan 与 apply 收尾都会提示，归位见步骤 8） |
| 上一版同步进 `.claude/rules/` 的纪律镜像目录（doctor 报「纪律镜像目录仍在」的那一个） | **删除，不留备份**——里面未提交的改动与未跟踪文件删掉就找不回来；已提交的版本能从 git 找回（git 找回还原不了原字节的已提交文件，例如 `.gitattributes` 强制了行尾的，plan 里同样算「会丢」并点名）。`.claude/rules/local/` 不碰 |
| `CLAUDE.md` 里的框架段落旧副本 | **不改本体**：生成一份去掉旧副本的候选文件，你对比后自己替换 |
| `.claude/rules/local/` 里提到被删纪律文件名的行 | 同上，生成候选文件，本体不动 |

新旧目录**同时存在**的那一类不动，plan 里标「冲突：需人工判断」并列出两边的文件与会话计数。这一态多半是没迁移就开了会话：新目录里只有那几次会话的数据，旧目录里才是原来的状态与记忆。确认新目录里没有要留的内容后，把它挪出项目（或改名），再重出 plan。

旧记忆目录同时是 Claude Code 子 agent `memory: project` 的落点，CC 只认原位置。所以它里面**没有框架出厂形态**（没有 `shared/`，也没有出厂角色的 `MEMORY.md` / `notes.md`）时，工具判它是 CC 原生的记忆：不搬、新目录在也不算冲突，plan 里列一行说明——判据与安装验收同一份。框架形态与这类子目录**混在一起**时整个目录照搬（形态上分不出哪个子目录是 CC 的、哪个是项目级角色的），plan 的报告项列出非出厂子目录，其中属于 CC 的那几个搬完手工挪回原位置。

### 步骤

1. **关掉这个项目的全部 Claude Code / Codex 会话**，以及打开着项目目录的编辑器、Obsidian、资源管理器窗口——Windows 上目录里有文件被打开时搬不动，会话开着则会边搬边写。
2. **出 plan**（只算，只写 `logs/workframe-migrate/plan.json`）——在普通终端里跑，或在**别的项目**的 Claude Code 会话里让模型跑；**不要在被迁移项目自己的会话里出**，那个会话一起，hook 就已经在新位置另建了一套：
   ```bash
   python "<插件根>/scripts/workframe_door.py" --migrate --project "<项目根>"   # 普通终端
   workframe-door --migrate --project <项目根>                                 # 别的项目的 Claude Code 会话里
   ```
   `<插件根>` 是升级后那一版 core 插件的目录（Claude Code 下本机实测在 `~/.claude/plugins/cache/<市场名>/core/<版本号>/`，取升级后的版本号）；已经开过会话的话，安装验收的 `[legacy_state_dir]` 那一条里印着填好路径的完整命令。输出是一份动作摘要、一个 8 位**确认码**，以及一行可以直接复制的 apply 命令（插件根的绝对路径已经填好）。逐条读摘要。确认码里含工具脚本自身的指纹：**plan 与 apply 要用同一份插件检出**——之间升级、合并或重新检出过插件（同一版本在 LF 与 CRLF 两种检出下指纹也不同），盘上的 plan 就作废，重出 plan 拿新码。
3. **apply 由你本人跑**：
   ```bash
   python "<插件根>/scripts/workframe_door.py" --migrate --apply --confirm <确认码> --project "<项目根>"
   ```
   推荐在普通终端（PowerShell / Windows Terminal）里跑；`workframe-door` 这个命令名只在 Claude Code 的 Bash 工具里在 PATH 上，终端里用上面这种写法。也可以在**另一个项目**的 Claude Code 会话里加 `!` 前缀跑——**不要用被迁移项目自己的会话**。在 `!` 前缀、Git Bash（mintty）或 AI 工具调用里跑时，工具会打印「当前不在普通终端里」的醒目提示后照常执行：`!` 前缀与 AI 的工具调用在本机分不开，工具**挡不住 AI 照 plan 打印的命令自己执行**，所以模型不得替你传 `--apply`（plan.json 的 `apply_by` 字段也写着这句）。确认码不符时工具只说不符、不告诉你正确的码。
4. **按输出的两步提交**：先只提交索引里的纯改名与删除（输出给了核对命令），**不要**写成 `git commit -- <路径>`——带路径的 commit 取工作树内容，会把随目录带过去的未提交改动一并提交、改名断成删除 ＋ 新增；再用 `git status` 看其余改写，自行分批提交。
5. 候选文件（`logs/workframe-migrate/<时间戳>/candidates/`）按输出的 `git diff --no-index` 命令对比，想要就替换进去。
6. **退出码是 `0` 才重开会话**。**退出码不是 `0` 就先别开这个项目的会话，看输出末尾的块**：`8` 表示没做完，块里逐项写明哪些还留在旧位置、哪些只是文件没改，见下面「中途失败」「有一类没迁」「跳过了改写」三条。
7. **auto-memory 里的旧路径手改**：主会话记忆（Claude Code 的 auto-memory）里指向旧记忆目录的指针，迁移工具只列不改（plan 的报告项逐行列出；迁移之后体检的 auto-memory 一项也会报）——把指针里的旧记忆目录前缀换成新的（上表第二行）。
8. **把项目内容归位进 `AGENTS.md`**：迁移不搬 `CLAUDE.md` 里的项目目标 / 业务背景 / 项目特殊约束；`AGENTS.md`「这个项目是什么」还是 `（待填）` 时，plan 的报告项与 apply 收尾都会说一句。按 core 插件 `reference/claude-md-merge-guide.md` 逐段归位（见上面「升级到含『四段闸门』的版本」第 3 条）。

### 它怎么防误跑、能力边界

- **plan 之后项目一变就拒**：apply 按项目当前状态重算动作，与 plan 逐条比、确认码也按重算结果核；不一致就拒绝、零写入（退出码 `7`），重出 plan 即可。参与比对的只有决策输入（目录在不在、跟踪文件清单、要删 / 要改写的文件内容），运行态文件的内容与时间戳不参与——所以关会话时 SessionEnd 的写入不会让 plan 作废。
- **越界即拒**：每条动作的类型与路径都限定在上表的对象里。
- **被搬的目录里有已暂存的改动**：那一类不搬、plan 列出这些路径——`git mv` 会带着暂存内容一起走，小文件在索引里断成删除 ＋ 新增、`git log --follow` 断开。先提交它们或移出索引（`git restore --staged -- <路径>`），再重出 plan。
- **项目嵌在上层 git 仓的子目录里**也照常判「要改写的文件有没有提交」。
- **中途失败**（多半是文件被占用）：停在失败的那一步，之前完成的保持原样，退出码 `8`，回执 `logs/workframe-migrate/<时间戳>/receipt.json` 如实记录；处理掉原因后重出 plan，只剩没做完的条目。**退出码不是 `0` 就先别开这个项目的会话**：收尾块列出失败那一步与其后没执行的类，按下一条的两种说法写明哪些还留在旧位置（「未迁完」）、哪些只是文件没改（「没做完」），已完成的步骤不列。
- **有一类没迁**（plan 里标 `✗` 被挡住或 `!` 冲突的，例如记忆目录里有已暂存的改动）：其余各类照做，收尾打印醒目的块，退出码同样是 `8`。处理掉原因、重出 plan 做剩下的。块里分两种说法：运行态三类、项目 skills、`.gitignore` 这几类标「未迁完」——**处理完之前别开这个项目的会话**，框架只读新位置，开了会在新位置另建一套、与留在旧位置的那一类冲突；其余的（例如 `@AGENTS.md` 导入加不上）标「没做完」——只是文件没改、不涉及运行态目录的位置，补做之前别开这个项目的会话。
- **跳过了改写**（要改写的 `CLAUDE.md` / `AGENTS.md` / skills md 有未提交改动或未跟踪，按「先提交」整份跳过）：apply 摘要与收尾块都列出跳过的文件与原因，退出码 `8`。先提交那个文件，再重出 plan、apply 补做；**补做之前别开这个项目的会话**——`CLAUDE.md` / `AGENTS.md` 每个会话都加载、skills 调用时读到，跳过的那几份此刻仍指着已经搬走的旧目录。
- **回滚**：输出末尾按倒序打印逆操作（搬回去、从 `preimage/` 拷回原件、解链接、从 git 找回镜像目录的已提交版本、删掉 apply 新建的文件），**在 PowerShell 里从上到下逐行粘贴执行**（POSIX 上在 sh 里；`#` 开头的行是说明）。从 git 找回的命令按 apply 那一刻逐文件试算的行尾形态带 `-c core.autocrlf=…`（两种形态混着时分两条），干净的已提交文件逐字节回到 apply 前；有未提交改动的那几份只能找回已提交版本。判断回滚是否完整以回执里 `pre_manifest` 的逐文件 sha256 为准，**不以 `git status` 为准**——被 `git rm -f` 丢掉的未提交改动在 `git status` 里看不出来。
- **会话是否还开着检测不了**：状态目录十分钟内有写入、有 Codex 会话标记、或探到别的进程持锁时只打印警告（刚关掉的会话同样会触发前一条）。
- **不在本工具范围内**：`.gitignore` 若有整目录规则把新运行态根（`.workframe/`）或记忆目录一起忽略，plan 只提示、不改；auto-memory 里指向旧记忆目录的行（迁移后手改，见步骤 7）、`projects/` 等其余文件里的旧路径、`.codex/` 下的生成物（由 Codex 门 `--upgrade` 重生成）——plan 只列出来。按 `.claude/skills/` 圈文件的模块清单，搬到中立目录之后 git 只报 `.agents/skills/` 的路径，需要改口（签发把关前缀框架已同时认两个）。

## 降级与故障

| 情况 | 行为 |
|---|---|
| `claude` CLI 不在 PATH | **不影响订阅**：两项声明由装机脚本直接写进项目 `.claude/settings.json`（merge 保留你原有的键、先落备份到 `logs/`、写完回读），不经过 `claude` 命令。真写不进去时把手动补丁打在终端上，补完跑 doctor 确认 |
| 骨架脚本非零退出 | 当场停下报错，不跳过继续（退出码 3 = 目标目录非空） |
| 找不到 workframe 市场 | 提示先注册市场源——Claude Code 门是 `claude plugin marketplace add <源>`，Codex 门是 `codex plugin marketplace add <源>`（两个定位器都解析不到时才会走到这里） |
| 中途中断 | `setup-state.json` 已记录进度；重启后首会话指出未完成项，按 doctor 提示补跑 |

## 初始化之后：与 core skills 的衔接

| 场景 | 用什么 |
|---|---|
| 建第一个业务模块 / 需求资产包 | `/core:module-init` |
| 存量规范 md 批量搬进 modules/ | `migrate-to-modules`（重启后项目内执行） |
| 异构原料（docx / xls / 截图）归档入库 | `requirement-archiving`（重启后项目内执行） |
| 代码反解为现状文档 | `code-to-doc`（重启后项目内执行） |
| 一堆资料不知道怎么进来 | `material-intake`（先出台账与分流计划） |
