# Workframe

> 让 Claude Code 成为一个专属于你的产品团队，干活：需求、研发、测试、Prompt 各有专职角色，项目自带记忆和看板。装一次插件，新老项目都能用。

**Status:** v1.0.0 · [CHANGELOG](./CHANGELOG.md) · [![validate](https://github.com/ryanzhao1011/workframe/actions/workflows/validate.yml/badge.svg)](https://github.com/ryanzhao1011/workframe/actions/workflows/validate.yml)

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

这些能力由 36 个 skills 支撑，完整清单与分层原理见 [docs/concepts.md](./docs/concepts.md)。

## 前置依赖

- **Claude Code**（2.1.x 上验证过）：框架挂了 11 个 hook 事件，其中五个较新，旧版本会静默忽略它们、对应能力不可用。
- **Python 3**（3.8+）：能从命令行调用。
- **git**：用来判断哪些代码改过（据此标记文档待更新）、以及检查 `.gitignore` 配置。没有也能跑，这两项降级但不报错。macOS 需先装 Xcode Command Line Tools。

以下是部分按需功能

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

**③ 再重启一次**，随便说句话，Claude 会转述验收结果。

这次重启后屏幕是空白的，正常——hook 的输出进了 Claude 的上下文，不显示在终端。细节见 [quickstart.md](./docs/quickstart.md)。

## 日常命令

平时不用记这些——正常干活直接说话就行，@pm / @dev / @qa / @prompt-eng 会自己出场。下面几个是需要时才用的：

| 命令 | 用途 |
|---|---|
| `/core:audit` | 看框架最近自动做了哪些维护 |
| `/core:rollback` | 回滚某次自动变更 |
| `/core:memory-log` | 看记忆层的提升 / 衰减 / 纠正流水 |
| `/core:maintenance-review` | 维护流程入口（librarian 整理、提案审批，每步你确认） |

## 几件先知道为好的事

- **记忆默认进 git**：框架会把对话里沉淀的经验写进 `.claude/agent-memory/`，并随项目一起提交。含客户名 / 未公开数据的项目，请自行在 `.gitignore` 里加回这一行——git 历史一旦写入难以抹除。
- **截图功能首次使用会自动装包**：在项目的 `tmp/screenshot-deps/` 下执行一次 `npm install puppeteer-core`。
- **回滚记录会被定期清理**：未验证的变更一直保留；已验证的在 30 天后清理；总量上限 100 条。
- **不想用了**：在项目目录内执行 `claude plugin uninstall core@workframe -s project`，自动运行的脚本即刻停止。但**留在项目里的产物仍会影响 Claude**——`.claude/rules/workframe/` 下的规则照常被加载，`CLAUDE.md` 里的框架契约段也还在。要清掉这些**框架自身的产物**：删 `.claude/workframe-state/`、`.claude/rules/workframe/`、`.workframe-config.json`，并移除 `CLAUDE.md` 里的框架段落与 `.gitignore` 末尾的 managed 标记块。
  > ⚠️ `projects/` 与 `.claude/agent-memory/` **装的是你自己的东西**——需求文档、看板、issues、积累下来的经验。框架只是帮你组织它们，删掉就没了。这两个目录请单独判断，别跟着一起删。


## 升级

```bash
claude plugin marketplace update workframe
claude plugin update workframe-launcher@workframe
cd <你的项目> && claude plugin update core@workframe -s project
```

两个实测踩过的坑：

- `claude plugin update` **必须带插件名**，裸命令会报错
- 第三条要**在项目目录里跑**。不在已接入项目里时，CLI 可能静默更新到别的项目——看输出括号里的路径确认

改完重启各项目会话。

## 平台支持

面向 Windows 11 / macOS / Linux 设计。

Windows 11 已完成真实会话全流程 E2E（覆盖全新创建、存量接入、升级、断点续做等九场景）与 `validate.py` 全量验证（当前 160 项检查，随新增检查增长）。

macOS / Linux 的兼容优化尚不够完整，后续会持续改进——遇到问题欢迎提 issue。

<details>
<summary><b>无人值守 / CI 初始化（没有交互会话时）</b></summary>

launcher 是对话式的。需要从脚本初始化项目时，直接调用同一批底层步骤——`<core>` 指已安装的
core 插件根（已接入的项目把它记录在 `.claude/workframe-state/plugin-root.txt`）：

```bash
# 1. 落骨架。加 --params <json> 可一并渲染 CLAUDE.md 与 modules/ 总图。
python "<core>/scripts/project_scaffold.py" --project "<target>" --create-missing

# 2. 订阅——两条命令都要，在 <target> 目录内执行。
#    只跑第二条会产出协作者装不上的项目（settings 里没有市场声明）。
claude plugin marketplace add "<source>" --scope project
claude plugin install core@workframe --scope project

# 3. 镜像 core rules（此后由 SessionStart hook 自愈保持同步）。
python "<core>/scripts/sync-rules.py" --project "<target>"

# 4. 落盘验收。存在任何 error 时以非零退出码返回。
python "<core>/scripts/workframe_doctor.py" --project "<target>" --group install
```

依赖重启的检查（hook 活性）在项目首个 Claude Code 会话跑完之前按参考信息（info）报告。

</details>

## 仓库结构与文档

```
workframe/
├── plugins/
│   ├── workframe-launcher/       用户级入口插件——每台机器装一次
│   └── core/                     项目级插件——agents、skills、hooks、rules
├── tools/                        sync-rules.py / validate.py（贡献者工具）
└── docs/                         用户文档
```

用户文档：[上手](./docs/quickstart.md) · [核心概念](./docs/concepts.md) · [初始化细节](./docs/setup-guide.md) · [可选配置](./docs/onboarding.md) · [rules 同步机制](./docs/rules-sync.md)

## 参与贡献

欢迎 Issue 与 PR。**提 PR 前跑一次 `python tools/validate.py`，必须全绿**——它是本仓唯一的质量闸。

如果你的改动需要放宽某道护栏，多半说明护栏该重新瞄准而不是删掉，请在 PR 里说明理由。其余仓库约定（发版锁步、出货资产的路径约束等）见 [CLAUDE.md](./CLAUDE.md)。

## 许可证

[MIT](./LICENSE)
