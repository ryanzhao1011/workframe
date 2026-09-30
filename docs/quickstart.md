# Quickstart

10 分钟跑通 Workframe：装一次 launcher → 对话式创建或接入项目 → 重启会话开始干活。

## 前置条件

- **Claude Code** 已安装（CLI 和/或 IDE 扩展）。参见 [claude.com/claude-code](https://claude.com/claude-code)。
- **Codex**（可选，codex-cli 0.153.4+）：想在 Codex 里也用本框架时装——一份源、两扇门，同一个项目可以同时挂两边；装法见 [setup-guide.md](./setup-guide.md) §Codex 门。
- **Python 3** 可通过命令行调用。
  - Windows：通常是 `python` 或 `py -3`
  - macOS / Linux：`python` 或 `python3`（Ubuntu 22.04+ / Fedora 等默认只有 `python3`）
  - hook 由 `plugins/core/bin/workframe-python` launcher 自动选择解释器（POSIX：`python` → `python3`；Windows：`python` → `py -3` → `python3`），无需手工改 `hooks.json`，也无需做 alias

## 第 1 步：安装 launcher（每台机器一次）

Workframe 由两个插件组成：**`workframe-launcher`**（用户级，管「怎么开局」）和 **`core`**（项目级，管「项目里怎么干活」）。你只需要手动装前者——后者由 launcher 在初始化项目时自动订阅进项目。

```bash
claude plugin marketplace add ryanzhao1011/workframe
claude plugin install workframe-launcher@workframe
```

> **本地目录源**（`marketplace add /path/to/claude-workframe`）只适合框架开发者自己用：
> 用它初始化的项目，协作者 clone 后无法自动安装 core（对方机器上没有这个路径）。
> 团队协作 / 开源场景请用 git 源。落盘验收（doctor）会对**绝对路径**目录源项目给出 warn 提醒
> （手写成相对路径、且框架仓就在项目目录里的开发者形态除外——那种随仓库走，不告警）。

装完**重启 Claude Code 会话**，让 launcher 的 setup skill 加载进来。

Codex 侧目前不把 launcher 装成 Codex 插件（那个形态还没在真机上验过，见 [setup-guide.md](./setup-guide.md) §Codex 门的能力边界），所以**对话式的建项目 / 接入目前需要 Claude Code**。装机脚本本身不依赖 `claude` 命令（两项订阅声明由它直接写进项目 `.claude/settings.json`，见下文 §Q: 初始化时 `claude` CLI 不可用）：没有 Claude Code 的机器，可以照 [README](../README.md)「无人值守 / CI 初始化」一段手工跑同一批脚本——那条路没有确认页，也没在只装 Codex 的机器上完整走过一遍。

Codex 门怎么接：**走 launcher 装 / 接入项目时它已经替你跑完了**（这台机器上装了 `codex` 就是装机的一部分，确认页会单独列一行告诉你它要改什么）。要**单独跑**——补装到上一版装的老项目、或换了台机器——在项目目录里跑一次 `workframe-door --codex`（core 插件 `bin/` 下的命令），它替你注册市场、装插件、生成角色、写 hook 信任与项目 trust，并让插件只在跑过它的项目里加载（你用户配置里本插件的 `enabled` 改为 `false`、项目里写启用表）；见 [setup-guide.md](./setup-guide.md) §Codex 门。跑完**重开这个项目的 Codex 会话**才生效。

`workframe-door` 这个命令名**只在 Claude Code 会话的 Bash 工具里在 PATH 上**——在会话里让 Claude 替你跑即可。普通终端（PowerShell / bash）里写成 `python "<core插件根>/scripts/workframe_door.py" --codex`，参数与本文各处的 `workframe-door …` 相同。`<core插件根>` 怎么找：开过会话的项目记在 `.workframe/state/plugin-root.txt`；否则，Claude Code 装过 core 的机器在 `~/.claude/plugins/cache/<市场名>/core/<版本号>/`（与 setup-guide「已装项目迁移」步骤 2 说的是同一处），Codex 装过的在 `<CODEX_HOME>/plugins/cache/<市场名>/core/<版本号>/`（`CODEX_HOME` 未设时是 `~/.codex`）。

**上一版装的、只接了一扇门的老项目**：在项目目录里跑 `workframe-door --backfill`，它先看两扇门此刻各是什么状态，**只补缺的那扇**——两扇都在就一个字节不写，你自己关掉的 Codex 门它不替你改回来。「都在」只看项目文件：这台机器上 Codex 门的信任与启用它不核，收尾会让你跑 `workframe-door --check` 核一次。补完之后，补了哪扇门就**重开这个项目在那扇门里的会话**才生效。

## 第 2 步 · 场景 A：创建一个新项目

在任意目录打开 Claude Code，说：

```
帮我建一个 workframe 项目
```

（或直接调用 `/workframe-launcher:setup`）

launcher 会先判断你所在目录的状态（空目录 / 桌面之类的容器目录 / 已有项目 / 已接入），然后逐个问几个问题：

1. **你在负责什么类型的产品**——业务上下文，决定后面所有候选与角色推断
2. **项目叫什么**——展示名，可以与目录名不同
3. **放在哪个目录**——候选按目录状态给；空目录就地新建时此问自动跳过
4. **有没有已有资料要纳入**（可跳过）+ **一句话目标**——会写进 AGENTS.md 的「这个项目是什么」段

然后给出一页**方案确认页**：模块树、角色路由偏好（`role_profile`，自动推断、可当场改）、「你以后每类东西放哪」映射表。**确认前不写任何文件。**

确认后 launcher 一气呵成：落骨架 → 建模块树（确认页拍定的是真实树时；无资料时展示的示例树不落盘，真实模块之后用 `/core:module-init` 长出来）→ 订阅 core 插件 → `git init` + 首提交（可取消）→（本机装了 `codex` 时）接 Codex 门 → 落盘验收（doctor install 组）。

确认页上那份「将要执行的动作清单」的文件面由脚本算出来（只读预演，确认前零写盘），不是模型手写的：它列全本次要建 / 改的每一个文件，**含两处写在项目外的**——用户级市场注册表 `~/.claude/plugins/known_marketplaces.json`，以及装 Codex 门那一步要改的用户级 `config.toml`（后者会代你信任本插件的全部 hook，而受信 hook 脱离沙盒执行；以后插件升级时，未受信的本插件 hook 会在开 Codex 会话时自动补种信任、不再单问）。

## 第 2 步 · 场景 B：把现有项目接入

打开你的项目目录（任何阶段、任何技术栈都可以），说：

```
把这个项目接入 workframe
```

launcher 会**就地分析**（有没有代码、有没有存量文档、git 贡献者数），带着证据出确认页，而不是让你填表。与场景 A 的差别：

- **已有 `CLAUDE.md` / `AGENTS.md` 会做整合**：你原文里项目自己的内容归进 `AGENTS.md`（项目自有判据、项目级角色、业务目录等），`CLAUDE.md` 补一行 `@AGENTS.md` 导入；框架的角色表 / 路由规则 / 状态流转约定由会话启动时的注入片投递，不再写进这两个文件。确认页会露出含你原文的段落供审。写盘前按 git 状态决定备份——已被 git 跟踪且工作区干净则免备份（git 里有原文），否则先落 `logs/CLAUDE.md.bak-<时间戳>`（`AGENTS.md` 同理）
- **已有 `.gitignore` 只在末尾追加**一个 `Workframe managed` 标记块，不动你原有的规则
- **层层递进地落地，不一次性塞决策**：先确认模块树（结构闸），骨架建好后**逐模块**确认资料安置并当场归位（外部来源只拷贝不动原件）；「整理归档」（把 docx / xls 等原始资料整理成正式需求文档）由你在节奏闸拍节奏：**现在连续做完（推荐，装完即完整状态）/ 现在做一部分 / 重启后接力 / 暂不做**——推迟的批次由重启后的会话主动接起，进度在体检的「初始化完整度」里常驻可见，随时说「继续初始化」恢复
- **已有配置不覆盖**：`.workframe-config.json` 里你配过的字段（`project_name` / `role_profile` 等）完整保留

接入会创建哪些文件、修改哪些文件、绝对不动哪些，完整清单见 [setup-guide.md](./setup-guide.md) §接入会碰哪些文件。

## 第 3 步：重启会话（必做）

初始化完成后，**用 Claude Code 重新打开项目**（新会话）——core 插件的 hooks / agents 与必载纪律都在会话启动时加载。

**重启后屏幕是空白的，这是正常的**：hook 的输出进的是 Claude 的上下文，不显示在终端。随便说句话（比如「装好了吗」），Claude 会把首个会话的运行时验收结果告诉你。

## 如何确认装好了

1. **说句话**：首个会话 Claude 应主动转述安装验收结论（hook 在首个会话自动跑运行时验收）
2. **说「看板」**：Claude 应能读到 `projects/board.yaml` 并汇报（哪怕是空看板）
3. **自己跑体检**：

   ```bash
   python "<core插件根>/scripts/workframe_doctor.py" --project "<项目>" --group install
   ```

   `<core插件根>` 记录在项目的 `.workframe/state/plugin-root.txt`。error 为 0 即通过。

## 升级框架

```bash
# 1. 刷新市场（让 Claude Code 识别新版本）
claude plugin marketplace update workframe

# 2. 更新用户级 launcher
claude plugin update workframe-launcher@workframe

# 3. 在每个使用框架的项目目录里，更新该项目的 core
cd <你的项目>
claude plugin update core@workframe -s project
```

两个注意事项：

- `claude plugin update` **必须带插件名**，裸命令会报缺参数
- core 的更新要**在项目目录内**跑（`-s project` 按当前目录解析）；若当前目录不是已注册的接入项目，CLI 可能静默落到注册表里的另一个项目上——**看输出括号里的项目路径，确认更对了地方**

然后**先迁移、再重启会话**：上一版装的项目（运行态还在 `.claude/` 下），重开会话之前先按 [setup-guide.md](./setup-guide.md) 的「已装项目迁移」一节跑一遍——本版框架只读新位置，没迁移就开会话会在新位置另建一套（计数从零、角色记忆注入为空），迁移工具随后对那一类判冲突。plan 在普通终端里出，别在被迁移项目自己的会话里出。新建或已迁移的项目直接**重启会话**。必载纪律由启动时的 hook 直接注入上下文，项目里没有需要对齐的副本，也没有要手动跑的同步步骤；生效时序与故障排查详见 [context-injection.md](./context-injection.md)。

Codex 侧另有一步：在项目目录里跑 `workframe-door --upgrade`（git 源也可以裸敲 `codex plugin marketplace upgrade workframe`——那样改动过的 hook 先失信任、下一次会话由种信任 hook 补种并明说、再下一次会话才跑；本地目录源不吃这条命令，用 `--upgrade`，它会自己改走重装那条路）。顺序要紧：`--upgrade` 拿门自己这一份去核对刷新后的 Codex 侧，门本身得已经是目标版本，比 Codex 侧旧就会停下（exit 2）——所以先做上面的 CC 更新、再跑它；只装了 Codex 的机器，停下后按文案用新版本目录下的 `scripts/workframe_door.py` 带同样的参数重跑（门开跑时就记下了自己的版本，这次升级即使删掉或刷新了它所在的目录，它也照样会停下、并点名新目录下的门）。两扇门各自升级；一边升了一边没升，doctor 会报两根版本不等。`--upgrade` 跑完同样要**重开这个项目的 Codex 会话**才生效。

## 同事加入一个已接入的项目

```bash
git clone <项目仓> && cd <项目>
claude
```

打开时 Claude Code 会提示信任项目插件——接受后 core 自动从项目 settings 里记录的市场安装，**不需要手动 `marketplace add`**。（前提：项目当初用的是 git 源，见第 1 步的目录源提醒。）

运行时状态（`.workframe/state/`）不随 clone 带过来——它在 gitignore 里，首个会话由 hook 自动补齐骨架。新建项目的 `.claude/skills` 目录链接同理不进 git（真实源 `.agents/skills/` 随仓走），首个会话由 hook 补建、并打一行「已补建 .claude/skills → .agents/skills 目录链接」。Claude Code 的 skill 清单通常在那条 hook 之前就扫过了，所以补建的那次 hook 会请求 Claude Code 在启动后重扫一次；**若那个会话里仍看不到项目 skill（如 `/prd-style`），执行 `/reload-skills` 或重开会话**。

用 Codex 的同事要在各自机器上对这个项目跑一次 `workframe-door --codex`：hook 信任、项目 trust 与本插件的用户层 `enabled = false` 写在各人的用户级 `config.toml` 里，不随仓走；没跑之前，这个项目里的 Codex 会话不加载插件。

## 常见问题

### Q: hook 不执行（说话后 Claude 不知道自己在 workframe 项目里）

- 确认重启了 Claude Code 会话
- 检查 `.claude/settings.json` 是否含 `enabledPlugins: { "core@workframe": true }`
- 检查命令行 `python --version` 或 `python3 --version` 至少一个能跑
- Codex 侧：跑 `workframe-door --check`，看 hook 是否全 `trusted`、用户层 `enabled` 是不是 `false`、有没有报「本项目 Codex 门已被你关闭」——Codex 的 hook 不受信任就一条不跑、且不给任何提示；这台机器还没对本项目跑过 `--codex` 时插件在这个项目里根本不加载

### Q: 初始化时 `claude` CLI 不可用

不影响订阅：两项订阅声明由装机脚本直接写进项目 `.claude/settings.json`（merge 保留你原有的键、先落备份、写完回读），不经过 `claude` 命令——所以订阅这一步在没有 `claude` 命令时也能完成（对话式入口 launcher 仍需要 Claude Code，见第 1 步）。真写不进去时它会把手动补丁打在终端上，补完跑一遍 doctor 确认（命令见上文 §如何确认装好了）。

### Q: 初始化中断了怎么办

进度记录在 `.workframe/state/setup-state.json`——每完成一步立即记一笔，中断不丢。重启后的首个会话会指出未完成项；doctor 也能告诉你装到哪一步，按提示补跑缺的步骤即可。

## 下一步

- 读 [concepts.md](./concepts.md) 理解 Plugin / 项目级 / 用户级分层
- 读 [setup-guide.md](./setup-guide.md) 了解初始化流程的完整细节
- 需要扩展角色 / 写新 skill？见 [`role-customization-guide.md`](../plugins/core/reference/role-customization-guide.md) / [`skill-customization-guide.md`](../plugins/core/reference/skill-customization-guide.md)
