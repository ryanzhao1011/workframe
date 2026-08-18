# 项目初始化指南（workframe-launcher setup）

`/workframe-launcher:setup` 是 Workframe 的**唯一初始化入口**：在任意目录发起，创建新项目或接入存量项目，完成后由项目里的 core 插件接管日常工作。本文讲它会问什么、怎么判断、生成什么、失败了怎么办。

10 分钟上手版见 [quickstart.md](./quickstart.md)；本文是细节参考。

## 两个插件的分工

| 插件 | 级别 | 职责 |
|---|---|---|
| `workframe-launcher` | 用户级（每台机器装一次） | 「怎么开局」：目录判定、对话采集、确认页、执行初始化 |
| `core` | 项目级（launcher 自动订阅进项目） | 「项目里怎么干活」：4 角色、36 skills、hooks、rules |

launcher 通过 marketplace 注册信息定位 core（读 `~/.claude/plugins/known_marketplaces.json`）。你同时注册了多个 workframe 市场（如开发目录源 + GitHub 源）时，它会问你用哪个，不会自己挑。

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
| 你在负责什么类型的产品？ | 业务上下文——喂给 CLAUDE.md 业务背景段、role_profile 推断、名称候选 |
| 项目叫什么？ | 展示名（报告 / 启动上下文用），可与目录名不同 |
| 放在哪个目录？ | 候选按目录状态给；空目录就地新建时自动跳过；**候选路径先查是否被占**，撞上已有目录会问你接入 / 建子目录 / 换名 |
| 有没有已有资料要纳入分析？（可跳过） | 有 → 走 B 路径的分析能力；跳过 → 标准骨架起步，日后在项目内随时补 |
| 一句话目标 | 会写进 CLAUDE.md，影响模块切分与角色推断——写具体一点更好 |

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

1. **CLAUDE.md 整合**。骨架脚本对已存在的 CLAUDE.md 一律不覆盖；但不整合的话，框架一半的行为约定（角色表 / 路由规则 / 状态流转与签发权限 / 文档与结构约定）进不了你的 CLAUDE.md——落盘验收会以 error 抓出（doctor 的 claude_md 项查四个契约段的内容，不只看文件在不在）。所以 launcher 会按 [`claude-md-merge-guide.md`](../plugins/core/reference/claude-md-merge-guide.md) 出合并稿，确认页露出**含你原文的段落**供审（框架样板段每个项目一样，不占版面），写盘前按 git 状态决定是否落备份（`logs/CLAUDE.md.bak-<时间戳>`）。
2. **已有项目级 agent 检测**。`.claude/agents/<自定义角色>.md` 存在时自动检查三项（frontmatter 用法 / body 是否引用 agent-protocols / `.claude/agent-memory/<role>/` 是否存在）并提议 patch。
3. **层层递进地落地，不一次性塞决策**。结构闸只拍模块树与 CLAUDE.md 整合稿；骨架建好后**逐模块**深读资料、逐模块确认安置与原件处置并当场执行（外部来源只拷不动）；「整理归档」（把 docx / xls 等原始资料整理成正式需求文档）在节奏闸单独拍：现在连续做完 / 做一部分 / 重启后接力 / 暂不做。推迟的批次结构化记进 `setup-state.json` 的 `pending_work`（含节奏），重启后首个会话**主动接起**；「暂不做」则零打扰、只在体检与看板可见，随时说「继续初始化」恢复。

### 接入会碰哪些文件

**会创建**（已存在则跳过）：

- `projects/` — board.yaml（任务看板）/ modules/overview.md（模块树总图）/ specs/overview.md / specs/_meta/taxonomy.md（tag 受控词表）/ issues/TEMPLATES.md / changelog.md / evals / proposals / archive 骨架
- `logs/` — hook 输出与报告目录（gitignore）
- `.claude/workframe-state/` — 运行时状态（gitignore）
- `.claude/agent-memory/` — shared + 4 个 baseline 角色的记忆骨架（**默认进 git**：记忆是跨会话资产，丢了不可重建。记忆正文常年积累业务细节，若项目含客户名 / 未公开数据，自行在 `.gitignore` 加回 `.claude/agent-memory/`——框架不替你判断）
- `.claude/skills/prd-style/` — 项目 PRD 框架（出厂默认版实例化；已存在则不覆盖，项目可自由改）

**会修改**（逐个点名）：

- `CLAUDE.md` — 整合覆盖（见上文 §B 路径，落备份）
- `.gitignore` — 末尾追加 managed 标记块，含 5 个必需条目：`.claude/settings.local.json` / `.claude/workframe-state/*` / `logs/` / `tmp/` / `.tmp/`，外加一行例外 `!.claude/workframe-state/memory-index.json`（记忆 sidecar 必须进 git，理由见 core `reference/project-architecture.md` §Git 策略）；已全齐则不动
  - `/*` 与例外行是**成对硬契约**：改成 `.claude/workframe-state/` 会让 git 静默无视例外行，sidecar 无声失踪。多人协作时 sidecar 若冲突，删本地重建，不手工 merge JSON
- `.workframe-config.json` — merge 模式：只刷新框架字段（`framework_version` 等），用户字段完整保留
- `.claude/settings.json` — 由官方 CLI 写入两项订阅声明（`enabledPlugins` + `extraKnownMarketplaces`）
- `.claude/rules/workframe/core/` — 4 份 core rule 镜像（只动这个框架专属子目录）

**绝对不动**：你的代码与业务目录、`.claude/rules/` 根目录与 `local/` 下的项目专有 rules、项目级 `.claude/agents/` 与 `.claude/skills/`。

## 结构闸（写盘与订阅前的唯一闸门）

只拍**项目级三件事**：模块树（粗扫标题级证据支撑，用你的业务语言展示 + 「你以后每类东西放哪」映射表）、CLAUDE.md 整合稿（露出你的原文段落）、会被修改的已存在文件逐个点名。每份文件的安置与处置**不在这里拍**——骨架建好后逐模块小闸确认，一轮只核几份文件，改动也不连锁。选项：`按此执行` / `我要调整`（用自然语句说改什么，迭代到满意）/ `取消`。**确认前零写盘。**

## 确认后执行什么

1. **落骨架**——`project_scaffold.py` 确定性渲染模板（占位符替换由脚本保证并断言零残留）；全新创建带 `--require-empty` 防呆（目标非空则退出码 3，改走接入或换路径，不硬来）。骨架包含**项目 PRD 框架**（`.claude/skills/prd-style/`，出厂默认版）——写 PRD 的章节结构与风格以它为准，属于你的项目、可自由修改；接入时若你有 ≥3 份风格趋同的存量需求文档，节奏闸会提议按你的风格定制它
2. **建模块树**——`module_init.py` 按确认的树确定性落盘（骨架 + 索引段 + 反向索引，幂等可重跑）
3. **（仅 B 路径）写入整合后的 CLAUDE.md**
4. **订阅 core**——两条命令缺一不可：`claude plugin marketplace add <源> --scope project` + `claude plugin install core@workframe --scope project`。只跑第二条会产出协作者装不上的项目（settings 里没有市场声明）。CLI 不可用时降级为输出命令让你手动执行
5. **同步 rules 镜像**——4 份 core rule 复制到 `.claude/rules/workframe/core/`
6. **git init + 首提交**——全新创建默认做（确认页可取消）；已是 git 仓则跳过 init 且不替你提交；没配 git 身份时保留 init、提示你补完
7. **逐模块深读与安置**——一次一个模块：深读该模块资料 → 确认安置与原件处置 → 当场执行（成品 md 收口引用；异构原料归位 `others/原始资料/`；大批量走「样板批 + 重启后批量迁移」逃生口；小模块合并进相邻一轮，单轮不超过约 10 份文件的决策量）
8. **节奏闸 + 整理归档段**（有原始资料批次时）——预估只给份数与需你拍板的决策点数（**不给时间**），你拍节奏与 git 提交策略；选当场做的批次逐阶段照归档 / 迁移 SOP 执行，每批完成汇报并给离场点
9. **落盘验收**——doctor install 组 11 项：骨架完整性、CLAUDE.md 契约段、项目 PRD 框架、config 字段、订阅接线、rules 镜像、`.gitignore` 必需条目、初始化断点、**初始化完整度**（推迟批次的落地状态）、可移植性、环境与 hook 活性。放在批次落定之后跑，「初始化完整度」读到的才是真实终态。依赖 hook 的项此刻显示「待首个会话后复查」是正常的，不是失败

全程**每完成一步立即记进 `setup-state.json`**——真断在中途时，进度已经在文件里，doctor 能告诉你装到哪一步；整理归档批次另有台账逐行记账，任何后续会话「继续初始化」精确续上。

收尾产出 `logs/creation-report.md`（报告落 `logs/`，不污染项目根），关键结论直接在响应里给你。

## 生成的项目长什么样

```
<project>/
├── CLAUDE.md                        ← 项目说明 + 角色体系 + 路由规则 + 状态流转 + 文档约定
├── .workframe-config.json           ← project_name + project_type / dormant_profile / role_profile
├── .claude/
│   ├── settings.json                ← 两项订阅声明（enabledPlugins + extraKnownMarketplaces）
│   ├── rules/workframe/core/        ← 4 份 core rule 镜像（勿手改）
│   ├── skills/prd-style/            ← 项目 PRD 框架（出厂默认实例化，随项目自由改）
│   ├── agent-memory/<role>/         ← shared + 4 角色记忆骨架
│   └── workframe-state/             ← 运行时状态（gitignore，不进 git；memory-index.json 除外）
├── projects/
│   ├── board.yaml                   ← 任务看板
│   ├── modules/overview.md          ← 模块树总图（modules/ 体系恒启用；首个模块用 /core:module-init 创建）
│   ├── specs/overview.md            ← 跨模块规范层
│   ├── specs/_meta/taxonomy.md      ← tag 受控词表（doc-graph-health 概念热点的词源）
│   ├── issues/TEMPLATES.md          ← Issue 模板（SEC / BUG）
│   ├── changelog.md
│   └── evals/ proposals/ archive/   ← 骨架占位
├── logs/                            ← hook 输出 + 报告（gitignore）
└── <你的业务目录>                   ← 框架不预建业务目录，跟随你的实际脚手架
```

## 重启会话 + 首会话验收

初始化完成后必须**新开一个 Claude Code 会话**打开项目——hooks / rules / agents 在会话启动时加载。

重启后**屏幕是空白的，这是正常的**（hook 输出进 Claude 上下文，不显示在终端）。说句话，Claude 会转述首个会话的运行时验收结果——core 侧 hook 在首个会话自动复跑同一组检查，纯代码确定性，不依赖模型自觉。

## 生成后如何调整

生成的都是**起手稿**，鼓励直接编辑：

- 改角色职责 → `.claude/agents/<role>.md`（项目级 override 时**基于插件版全量复制后修改**，同名是完全覆盖不是合并）
- 加 / 删项目级 skill → `.claude/skills/` 下增减目录
- 项目自己的规则 → `.claude/rules/local/*.md`
- 调整业务目录 → 随意重组

**唯一别改的**：`.claude/rules/workframe/core/*.md`（框架同步的只读镜像，自愈同步会覆盖手改）。

## 降级与故障

| 情况 | 行为 |
|---|---|
| `claude` CLI 不在 PATH | 输出两条订阅命令让你手动执行，继续后续步骤 |
| 骨架脚本非零退出 | 当场停下报错，不跳过继续（退出码 3 = 目标目录非空） |
| rules 同步失败 | 提示重试命令；此后每次会话 SessionStart hook 也会自愈 |
| 找不到 workframe 市场 | 提示先 `claude plugin marketplace add <源>` |
| 中途中断 | `setup-state.json` 已记录进度；重启后首会话指出未完成项，按 doctor 提示补跑 |

## 初始化之后：与 core skills 的衔接

| 场景 | 用什么 |
|---|---|
| 建第一个业务模块 / 需求资产包 | `/core:module-init` |
| 存量规范 md 批量搬进 modules/ | `migrate-to-modules`（重启后项目内执行） |
| 异构原料（docx / xls / 截图）归档入库 | `requirement-archiving`（重启后项目内执行） |
| 代码反解为现状文档 | `code-to-doc`（重启后项目内执行） |
| 一堆资料不知道怎么进来 | `material-intake`（先出台账与分流计划） |
