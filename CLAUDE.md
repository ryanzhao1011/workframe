# Workframe — 仓库维护约定

> 面向本仓的贡献者与协作 AI。**改这个仓之前读这份**；使用框架的说明在 [docs/](./docs/README.md)。

## 这是什么

一个 Claude Code 插件市场仓，含两个插件：`workframe-launcher`（用户级，管「怎么开局」）与 `core`（项目级，管「项目里怎么干活」）。用户装的是插件，不是这个仓——**仓库结构对用户不可见，插件内容才可见**。这条区分决定了下面多数约束。

```
plugins/workframe-launcher/   用户级入口插件
plugins/core/                 项目级插件：agents / skills / rules / hooks / scripts / templates / reference
tools/                        validate.py（唯一质量闸）、sync-rules.py
docs/                         用户文档
.claude-plugin/               市场元数据
```

## 四条硬约束

1. **`python tools/validate.py` 必须全绿**才能提交。它是本仓唯一的质量闸，纯标准库零依赖。如果你的改动需要放宽某道护栏，多半说明护栏该重新瞄准而不是删掉——在 PR 里说明理由。
2. **发版锁步**：两个插件、市场元数据、README 状态行同版本一起 bump。`version` 是用户感知「有更新」的唯一信号，且按插件各自独立，锁步是为了不让任何一边的用户被静默落下（`check_version_consistency` 强制）。
3. **`plugins/core/{rules,templates,skills}/` 下的资产会装到用户机器，不得引用仓内路径**（`check_core_assets_no_repo_internal_path` 强制）。用户那里没有 `tools/`、没有 `docs/`，写了就是死引用。
4. **新增检查必须用它自己的失败方式打一遍**（二检）。「写完了但从没被真正执行过」的检查与通过长得一模一样——本仓踩过多次，包括一次改动误删赋值导致某检查全程失效、而当时全部闸显示绿灯。

## 两份 CHANGELOG

本仓的 `CHANGELOG.md` 是**对外发布说明**，回答用户一个问题：**升级后我要改变什么行为**。

| | 写 | 不写 |
|---|---|---|
| 内容 | 破坏性变更（置顶）、新能力、升级要做的动作、已知限制 | 内部里程碑代号、审查过程、失败的尝试、提交号、内部文档路径 |
| 粒度 | 一个版本一段，按用户可感知的能力分组 | 逐提交流水 |
| 时机 | **只在发版时写一次**——平时的改动记在维护者账本里，发版时从中提炼 | 每改一次就往对外版追加（这样必然碎、必然漂） |

**未发布段的数字必须跟着代码走。** 判据是段标题有没有 ISO 日期：

- `## [Unreleased]` / `## [1.0.0] — 未发布` → **未发布**，段内的 hook 段数、skills 数、检查数、角色数由 `check_unreleased_changelog_numbers` 逐个对账
- `## [1.0.0] — 2026-09-01` → 已发布，历史账，**永久豁免**——它记的是当时的状态，倒改等于篡改历史

打 tag 当天把「未发布」换成实际日期，该段自动转为历史账。不需要改任何检查代码。

> 引号内的数字按引述处理，不参与对账：讲「模板里删掉了『36 skills』这个数字」时，说的是被删掉的旧内容，不是对当前状态的断言。

## 提交与平台

- **改了框架源，记得同步项目镜像**——`.claude/rules/workframe/core/` 是 `sync-rules` 维护的镜像，而模型每会话实际读的是镜像不是源。只改源等于没改。
- **Windows 上写文件一律钉 `newline=""`**。Python 文本模式默认把 LF 转成 CRLF，一次写入就让整个文件产生 diff，真实改动被淹没。`check_text_writes_pin_newline` 扫 `plugins/` 与 `tools/` 下所有写入点。
- **hooks 保持 shell-form**，不要迁 exec form——双包装器设计依赖 shell 的扩展名解析，exec form 无法用一份跨平台 `hooks.json` 表达。
- **`${CLAUDE_PLUGIN_ROOT}` 只注入 hook / MCP / LSP 子进程**，Bash 工具子进程不在官方承诺内。skill 里定位插件根一律用 `plugin-root.txt` 配方（`check_no_plugin_root_env_in_skills` 强制）。

## 改动前先查

| 你要改 | 先做 |
|---|---|
| 任何东西 | `python tools/validate.py` 拿到绿基线，改完再跑一次 |
| 计数类表述（skills / hook 段 / 检查数） | 别手写数字——先看有没有对账闸管着它；正向断言（从源现算）优于枚举历史错值 |
| 检查逻辑 | 写完用失败方式打一遍，确认它真的会红 |
| 跨多个文件的同一事实 | 找出唯一实现，其余改成引用；两处手写必漂 |
