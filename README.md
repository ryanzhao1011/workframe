# Workframe

> 让 Claude Code（或 Codex）成为一个专属于你的产品团队，干活：需求、研发、测试、Prompt 各有专职角色，项目自带记忆和看板。装一次插件，新老项目都能用；一份源、两扇门，同一个项目可以同时挂两边。

**Status:** v1.1.0 · [CHANGELOG](./CHANGELOG.md) · [![validate](https://github.com/ryanzhao1011/workframe/actions/workflows/validate.yml/badge.svg)](https://github.com/ryanzhao1011/workframe/actions/workflows/validate.yml)

## 肺腑之言

我是一名toB的AI产品经理，已经3个月没有手搓过需求文档和原型。

这两年 AI 把我的工作方式翻了个底朝天，AI提效这件事，过程真的相当痛苦。

我自己摸索了大半年，参考了很多优秀的 agent 架构和 skills 设计，在日常工作里一点点升级，才形成一套适合自己的工作框架。但这是一个越沉淀越轻松的过程。现在，从需求分析、PRD 到交互 demo，我已经全部通过 Claude Code 加这套框架聊天产出，质量符合真实工作的交付要求。

Workframe 就是这套沉淀的开源版。希望它能帮你少走弯路，通过这套框架沉淀一套适合自己的工作方式。

最后，祝愿你把提效的时间，都能留给工作之外的你，好好爱自己。

## 项目特点和作用

- **即插即用**——把项目资料直接丢给它，自动解析、分类、归档、相互关联索引，快速产出一个完善可用的项目文件夹。
- **写文档再也不操心**——内置我实践提炼的 PRD 框架，能根据你需求文档的写法进行技能自动优化，对齐你的交付标准。
- **4 个内置角色，各司其职**——@pm 拆/写需求，@dev 写代码，@qa 把控质量，@prompt-eng 调 prompt，每个人都有专属技能包。
- **越用越懂你**——自动从对话里提炼有价值的经验沉淀为记忆，agents 和 skills 跟着你不断进化，纠正过的错不会再犯第二遍。
- **产研双向互通**——支持把代码解析成文档、和需求关联起来，写需求快到飞起；快去找你的领导开放代码权限，体验不一样的产研协作。

这些能力由 37 个 skills 支撑，完整清单与分层原理见 [docs/concepts.md](./docs/concepts.md)。

## 前置依赖

- **Claude Code**（2.1.x 上验证过）：框架挂了 11 个 hook 事件，其中五个较新，旧版本会静默忽略它们、对应能力不可用。
- **Codex**（可选，codex-cli 0.153.4+，Windows 上实测）：同一个项目可以同时挂 Claude Code 与 Codex 两扇门，跑的是同一份框架；装法 `workframe-door --codex`，见 [docs/setup-guide.md](./docs/setup-guide.md) §Codex 门。
- **Python 3**（3.8+）：能从命令行调用。
- **git**：用来判断哪些代码改过（据此标记文档待更新）、以及检查 `.gitignore` 配置。没有也能跑，这两项降级但不报错。macOS 需先装 Xcode Command Line Tools。

以下是部分按需功能

- **PyYAML**（`pip install pyyaml`）：收口检查与安装自检里的「YAML 可解析性」两项要用真解析器读一遍模块文档与 `board.yaml`。**没装时这两项报红而不是跳过**——「这一项没跑」和「这一项通过」不是一回事，静默报绿等于替你声称验过。不想装就在 `.workframe-config.json` 里写一句显式声明关掉：`"close_check": {"yaml_parse": false}`。**装到你机器上的那部分**不依赖它；仓库自带的质量闸 `tools/validate.py` 会跑一组需要它的单测，那是给框架贡献者的，用户用不到。
- **Obsidian和 Obsidian CLI**：在使用 Claude Code时，能根据链接关系进行查找、管理文档、审计坏链
- **Node.js 18+**：截图与原型出图用（把 HTML 原型、Mermaid 图渲染成 PNG）。首次使用会自动装 `puppeteer-core`，另需系统已有 Edge / Chrome / Chromium 任一。
- **Python 包**：把 docx / pdf / xls 原始资料归档入库时使用

## 快速开始

**① 装插件**（每台机器一次）

```bash
claude plugin marketplace add ryanzhao1011/workframe
claude plugin install workframe-launcher@workframe
```

**② 重启 Claude Code，说一句话**

- 开新项目：在任意目录说「帮我建一个 workframe 项目」
- 接入已有项目：在项目目录里说「把这个项目接入 workframe」

**③ 在项目目录里重新打开 Claude Code**（新建的项目可能建在你当初所在目录下的子目录里，以确认页上的路径为准；接入的就是原项目目录），随便说句话，Claude 会转述验收结果。

这次重启后屏幕是空白的，正常——hook 的输出进了 Claude 的上下文，不显示在终端。细节见 [quickstart.md](./docs/quickstart.md)。

**④ 挂上 Codex 门**：这台机器上装了 `codex` 时，②那一步（launcher）**已经替你跑完了**——它是装机的一部分，会在确认页上单独列一行告诉你要改什么。要单独跑或补装到老项目上，在项目目录里跑 `workframe-door --codex`（core 插件 `bin/` 下的命令，只在 Claude Code 会话的 Bash 工具里在 PATH 上；普通终端里的写法见 [quickstart.md](./docs/quickstart.md) 第 1 步），它替你注册市场、装插件、生成角色、写 hook 信任——Codex 的 hook 默认不受信任、一条不跑，这一步是必须的。跑完重开这个项目的 Codex 会话才生效。

## 日常命令

平时不用记这些——正常干活直接说话就行，@pm / @dev / @qa / @prompt-eng 会自己出场。下面几个是需要时才用的：

| 命令 | 用途 |
|---|---|
| `/core:audit` | 看框架最近自动做了哪些维护 |
| `/core:rollback` | 回滚某次自动变更 |
| `/core:memory-log` | 看记忆层的提升 / 衰减 / 纠正流水 |
| `/core:maintenance-review` | 维护流程入口（librarian 整理、提案审批，每步你确认） |

## 几件先知道为好的事

- **记忆默认进 git**：框架会把对话里沉淀的经验写进 `.workframe/agent-memory/`，并随项目一起提交。含客户名 / 未公开数据的项目，请自行在 `.gitignore` 里加回这一行——git 历史一旦写入难以抹除。
- **截图功能首次使用会自动装包**：在项目的 `tmp/screenshot-deps/` 下执行一次 `npm install puppeteer-core`。
- **回滚记录会被定期清理**：未验证的变更一直保留；已验证的在 30 天后清理；总量上限 100 条。
- **不想用了**：在项目目录内执行 `claude plugin uninstall core@workframe -s project`，Claude Code 侧自动运行的脚本即刻停止。**接过 Codex 门的项目另有一步**：Codex 那侧的 hook 信任与启用写在你的用户级 `config.toml` 和项目 `.codex/config.toml` 里，卸掉 Claude Code 侧的插件不影响它——把项目 `.codex/config.toml` managed 段段首那一行 `enabled = true` 改成 `false`，这个项目的 Codex 会话就不再加载本插件（只改这一行的值，注意事项见 [setup-guide](./docs/setup-guide.md)「只用一门时怎么关另一门」）；用户级 `config.toml` 里本插件那几项怎么清，框架没有实测过的命令，这里不写。但**留在项目里的产物仍会影响 Claude**——`CLAUDE.md` 里的框架段落（含那行 `@AGENTS.md` 导入）与 `AGENTS.md` 里的框架契约段还在。要清掉这些**框架自身的产物**：删 `.workframe/state/`、`.workframe-config.json`，移除 `CLAUDE.md` 里的框架段落与 `.gitignore` 末尾的 managed 标记块；接过 Codex 门的，还有 `.codex/`（managed 段与 `roles/`；段外你自己写的配置留着）和 `.workframe/managed-files.json`；早先版本装过的项目可能还留着一个 `.claude/rules/workframe/` 目录，有就一并删掉。新建项目的项目 skills 在 `.agents/skills/`（`.claude/skills` 是指向它的链接，删之前先读 setup-guide「安全删法」）——其中 `prd-style/` 是装机时放进来、之后归你的，和你自己写的 skill 在一起，请单独判断。
  > ⚠️ `projects/` 与 `.workframe/agent-memory/` **装的是你自己的东西**——需求文档、看板、issues、积累下来的经验。框架只是帮你组织它们，删掉就没了。这两个目录请单独判断，别跟着一起删。


## 升级

```bash
claude plugin marketplace update workframe
claude plugin update workframe-launcher@workframe
cd <你的项目> && claude plugin update core@workframe -s project
```

两个实测踩过的坑：

- `claude plugin update` **必须带插件名**，裸命令会报错
- 第三条要**在项目目录里跑**。不在已接入项目里时，CLI 可能静默更新到别的项目——看输出括号里的路径确认

改完重启各项目会话——上一版装的项目（运行态还在 `.claude/` 下）先按 [setup-guide](./docs/setup-guide.md) 的「已装项目迁移」迁移、再重启会话。

## 平台支持

面向 Windows 11 / macOS / Linux 设计。

Windows 11 已完成真实会话全流程 E2E（覆盖全新创建、存量接入、升级、断点续做等九场景）与 `validate.py` 全量验证（当前 218 项检查，随新增检查增长）。

macOS / Linux 的兼容优化尚不够完整，后续会持续改进——遇到问题欢迎提 issue。

<details>
<summary><b>无人值守 / CI 初始化（没有交互会话时）</b></summary>

launcher 是对话式的。需要从脚本初始化项目时，直接调用同一批底层步骤——`<core>` 指已安装的
core 插件根（已接入的项目把它记录在 `.workframe/state/plugin-root.txt`；没有这份记录时去哪找，见
[quickstart](./docs/quickstart.md) 第 1 步「`<core插件根>` 怎么找」那段。两扇门都没装过 core 的机器上，
用本仓库克隆下来的 `plugins/core` 当 `<core>`——这一形态未实测）。

`<params.json>` 是一个 JSON 文件的路径（不是 JSON 字符串），必填 `project_name` / `one_line_goal` /
`business_context` 三个字段：

```bash
# 1. 落骨架。AGENTS.md 恒渲染；加 --params <params.json> 时对话参数填进 AGENTS.md，并一并渲染 CLAUDE.md 与 modules/ 总图。
#    **对话参数在这一步就带上**：AGENTS.md 只在不存在时渲染一次——这一步不带、第 2 步再带参重跑时它已存在，
#    params 里的项目目标与背景不会写进去（退出码仍是 0），AGENTS.md 停在「（待填）」占位（doctor 报 warn）。
python "<core>/scripts/project_scaffold.py" --project "<target>" --create-missing --params "<params.json>"

# 2. 订阅——两项声明（enabledPlugins + extraKnownMarketplaces）缺一不可，只写前者会
#    产出协作者装不上的项目。无人值守场景用脚本写，不依赖 claude CLI：
#    在 <params.json> 里加一个 subscription 块（marketplace_name / source 对象 / install_location），
#    然后带上 --write-subscription 重跑第 1 步那条命令（幂等）。
python "<core>/scripts/project_scaffold.py" --project "<target>" --params "<params.json>" --write-subscription
#    装了 Claude Code CLI 的机器上，下面两条是等价写法（在 <target> 目录内执行）：
#    claude plugin marketplace add "<source>" --scope project
#    claude plugin install core@workframe --scope project

# 3. 这台机器装了 codex 时，接 Codex 门（注册市场、装插件、生成角色、写 hook 信任与项目 trust）。
#    不跑这一步，项目在 Codex 下一条 hook 都不加载。它会改你 Codex 的用户级 config.toml，
#    见 docs/concepts.md §用户级；详见 docs/setup-guide.md §Codex 门。
python "<core>/scripts/workframe_door.py" --codex --project "<target>"

# 4. 落盘验收。存在任何 error 时以非零退出码返回。
python "<core>/scripts/workframe_doctor.py" --project "<target>" --group install
```

依赖重启的检查（hook 活性）在项目首个 Claude Code 会话跑完之前按参考信息（info）报告；接了 Codex 门的，重开这个项目的 Codex 会话才生效。

</details>

## 仓库结构与文档

```
workframe/
├── plugins/
│   ├── workframe-launcher/       用户级入口插件——每台机器装一次
│   └── core/                     项目级插件——agents、skills、hooks、必载纪律注入源
├── tools/                        validate.py 与配套单测（贡献者工具）
└── docs/                         用户文档
```

用户文档：[上手](./docs/quickstart.md) · [核心概念](./docs/concepts.md) · [初始化细节](./docs/setup-guide.md) · [可选配置](./docs/onboarding.md) · [上下文注入](./docs/context-injection.md)

## 参与贡献

欢迎 Issue 与 PR。**提 PR 前跑一次 `python tools/validate.py`，必须全绿**——它是本仓唯一的质量闸。

如果你的改动需要放宽某道护栏，多半说明护栏该重新瞄准而不是删掉，请在 PR 里说明理由。其余仓库约定（发版锁步、出货资产的路径约束等）见 [CLAUDE.md](./CLAUDE.md)。

## 许可证

[MIT](./LICENSE)
