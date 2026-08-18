# Quickstart

10 分钟跑通 Workframe：装一次 launcher → 对话式创建或接入项目 → 重启会话开始干活。

## 前置条件

- **Claude Code** 已安装（CLI 和/或 IDE 扩展）。参见 [claude.com/claude-code](https://claude.com/claude-code)。
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
> 团队协作 / 开源场景请用 git 源。落盘验收（doctor）会对目录源项目给出 warn 提醒。

装完**重启 Claude Code 会话**，让 launcher 的 setup skill 加载进来。

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
4. **有没有已有资料要纳入**（可跳过）+ **一句话目标**——会写进 CLAUDE.md 业务背景段

然后给出一页**方案确认页**：模块树、角色路由偏好（`role_profile`，自动推断、可当场改）、「你以后每类东西放哪」映射表。**确认前不写任何文件。**

确认后 launcher 一气呵成：落骨架 → 建模块树 → 订阅 core 插件 → 同步 rules 镜像 → `git init` + 首提交（可取消）→ 落盘验收（doctor install 组，11 项检查）。

## 第 2 步 · 场景 B：把现有项目接入

打开你的项目目录（任何阶段、任何技术栈都可以），说：

```
把这个项目接入 workframe
```

launcher 会**就地分析**（有没有代码、有没有存量文档、git 贡献者数），带着证据出确认页，而不是让你填表。与场景 A 的差别：

- **已有 `CLAUDE.md` 会做整合**：框架的角色表 / 路由规则 / 状态流转约定合并进你的原文；确认页会露出含你原文的段落供审。写盘前按 git 状态决定备份——已被 git 跟踪且工作区干净则免备份（git 里有原文），否则先落 `logs/CLAUDE.md.bak-<时间戳>`
- **已有 `.gitignore` 只在末尾追加**一个 `Workframe managed` 标记块，不动你原有的规则
- **层层递进地落地，不一次性塞决策**：先确认模块树（结构闸），骨架建好后**逐模块**确认资料安置并当场归位（外部来源只拷贝不动原件）；「整理归档」（把 docx / xls 等原始资料整理成正式需求文档）由你在节奏闸拍节奏：**现在连续做完（推荐，装完即完整状态）/ 现在做一部分 / 重启后接力 / 暂不做**——推迟的批次由重启后的会话主动接起，进度在体检的「初始化完整度」里常驻可见，随时说「继续初始化」恢复
- **已有配置不覆盖**：`.workframe-config.json` 里你配过的字段（`project_name` / `role_profile` 等）完整保留

接入会创建哪些文件、修改哪些文件、绝对不动哪些，完整清单见 [setup-guide.md](./setup-guide.md) §接入会碰哪些文件。

## 第 3 步：重启会话（必做）

初始化完成后，**用 Claude Code 重新打开项目**（新会话）——core 插件的 hooks / rules / agents 都在会话启动时加载。

**重启后屏幕是空白的，这是正常的**：hook 的输出进的是 Claude 的上下文，不显示在终端。随便说句话（比如「装好了吗」），Claude 会把首个会话的运行时验收结果告诉你。

## 如何确认装好了

1. **说句话**：首个会话 Claude 应主动转述安装验收结论（hook 在首个会话自动跑运行时验收）
2. **说「看板」**：Claude 应能读到 `projects/board.yaml` 并汇报（哪怕是空看板）
3. **自己跑体检**：

   ```bash
   python "<core插件根>/scripts/workframe_doctor.py" --project "<项目>" --group install
   ```

   `<core插件根>` 记录在项目的 `.claude/workframe-state/plugin-root.txt`。error 为 0 即通过。

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

然后**重启每个项目的会话**。rules 镜像由 SessionStart hook 自动对齐（自愈同步），无需手动跑脚本；生效时序与手动兜底方式详见 [rules-sync.md](./rules-sync.md)。

## 同事加入一个已接入的项目

```bash
git clone <项目仓> && cd <项目>
claude
```

打开时 Claude Code 会提示信任项目插件——接受后 core 自动从项目 settings 里记录的市场安装，**不需要手动 `marketplace add`**。（前提：项目当初用的是 git 源，见第 1 步的目录源提醒。）

运行时状态（`.claude/workframe-state/`）不随 clone 带过来——它在 gitignore 里，首个会话由 hook 自动补齐骨架。

## 常见问题

### Q: hook 不执行（说话后 Claude 不知道自己在 workframe 项目里）

- 确认重启了 Claude Code 会话
- 检查 `.claude/settings.json` 是否含 `enabledPlugins: { "core@workframe": true }`
- 检查命令行 `python --version` 或 `python3 --version` 至少一个能跑

### Q: 初始化时 `claude` CLI 不可用

launcher 会降级：把两条订阅命令输出给你手动执行，并继续后续步骤。补完后跑一遍 doctor 确认（命令见上文 §如何确认装好了）。

### Q: 我手改了 `.claude/rules/workframe/core/` 下的文件但没生效

那个目录是**框架同步的只读镜像**，会被自愈同步覆盖。项目自己的规则放 `.claude/rules/` 根目录或 `.claude/rules/local/`——框架永远不碰这些位置。详见 [rules-sync.md](./rules-sync.md)。

### Q: 初始化中断了怎么办

进度记录在 `.claude/workframe-state/setup-state.json`——每完成一步立即记一笔，中断不丢。重启后的首个会话会指出未完成项；doctor 也能告诉你装到哪一步，按提示补跑缺的步骤即可。

## 下一步

- 读 [concepts.md](./concepts.md) 理解 Plugin / 项目级 / 用户级分层
- 读 [setup-guide.md](./setup-guide.md) 了解初始化流程的完整细节
- 需要扩展角色 / 写新 skill？见 [`role-customization-guide.md`](../plugins/core/reference/role-customization-guide.md) / [`skill-customization-guide.md`](../plugins/core/reference/skill-customization-guide.md)
