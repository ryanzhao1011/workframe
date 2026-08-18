# Rules 同步机制

本文档解释 Workframe 的 rules 同步机制：为什么需要同步、有哪几条同步路径、哪些目录是安全的、升级后 rules 什么时候生效。

## 为什么需要同步

Claude Code 的 rules 加载由标准 `.claude/rules/*.md` 机制驱动，**不会自动读取 plugin 内的 rules 目录**。

但本框架的 4 份通用 rules（`agent-protocols.md`、`auto-update.md`、`correction-detection.md`、`response-output.md`）放在 plugin 内（`plugins/core/rules/core/`），原因：

- Plugin 自包含原则（安装后 plugin 被复制到缓存目录，不能依赖外部路径）
- rules 作为 plugin 资产随 plugin 升级一起演进

因此需要一个「搬运工」把 plugin 内 rules 同步到项目 `.claude/rules/` 下 Claude Code 能自动加载的位置。

## 目录约定

```
<project>/.claude/rules/
├── <project-rule-1>.md              ← 项目专有 rule（项目自己维护）
├── <project-rule-2>.md              ← 项目专有 rule
├── local/                           ← (可选) 项目专有 rule 的子目录组织
│   └── <project-rule>.md
└── workframe/
    └── core/                        ← ★ 框架同步的只读镜像
        ├── auto-update.md
        ├── agent-protocols.md
        ├── correction-detection.md
        └── response-output.md
```

| 路径 | 归属 | 规则 |
|---|---|---|
| `.claude/rules/*.md`（根目录） | 项目专有 | 项目自己维护，**框架永远不碰** |
| `.claude/rules/local/*.md` | 项目专有 | 项目自己维护，**框架永远不碰** |
| `.claude/rules/workframe/core/*.md` | 框架同步（只读镜像） | **用户勿手改**，同步时整目录覆盖 |

## 三条同步路径（单一实现，多入口）

同步逻辑只有一份，长在插件里：`plugins/core/scripts/sync-rules.py` 的 `sync_rules()`。它做三件事：读 plugin 内全部 rule 文件 → **就地覆盖**到 `<project>/.claude/rules/workframe/core/` → 清理框架侧已删除的多余文件。全程无「目录为空」的瞬间——SessionStart 的多个 hook 并行执行，清空式写法会让同时读目录的检查（如首会话验收）看到空目录而误报。只操作这一个子目录，根目录和 `local/` 完全不碰。

| 入口 | 什么时候跑 | 谁在用 |
|---|---|---|
| **初始化首次同步** | launcher setup 执行阶段（订阅 core 之后） | 所有用户，自动 |
| **SessionStart 自愈同步** | 每次会话启动，由 hook 自动执行 | 所有用户，自动——**升级后的主路径** |
| **手动兜底** | 故障排查时手动跑 | 见下方命令 |

手动兜底命令（marketplace 装的用户，`<core插件根>` 记录在项目的 `.claude/workframe-state/plugin-root.txt`）：

```bash
python "<core插件根>/scripts/sync-rules.py" --project "<项目>"
```

clone 了框架仓的贡献者也可以用仓根薄壳 `python tools/sync-rules.py --project <项目>`——它复用同一份实现。

## 生效时序：文件对齐 ≠ 上下文加载

rules 进入 Claude 上下文发生在**会话启动阶段**。自愈同步把磁盘文件对齐后，当前会话加载的仍可能是旧版——**新 rules 从下一次会话起生效**。

因此升级框架后的标准流程是：先更新两个插件（完整命令与注意事项见 [quickstart.md](./quickstart.md) §升级框架），然后**重启每个使用框架的项目的会话**——SessionStart 自愈把镜像文件对齐，rules 上下文从其后的会话取新版。

急着让新 rules 当轮生效时，先手动跑一次同步命令（见上方 §三条同步路径），再重启会话。

agents / skills / hooks 不需要同步——它们是 plugin 资产，由 Claude Code 的 plugin 机制自动更新，重启会话即加载新版。

## 如果我想修改 core rules

场景：你发现某条 `auto-update.md` 的触发词不适用于你的项目。

**不要直接改** `.claude/rules/workframe/core/auto-update.md`（下次同步会覆盖）。

**正确做法**：

- **选项 A（最稳）**：在项目专有 rules 里补充/覆盖，如 `.claude/rules/local/auto-update-project-overrides.md`，内容里说明「本项目对 core auto-update 的补充/调整条款」。Claude Code 会把 core 版和 local 版都加载，综合考虑
- **选项 B（社区贡献）**：如果你认为改动应该反哺框架，在框架仓库提 issue/PR，由维护者评估

## 故障排查

### `workframe/core/` 目录是空的

- 手动跑一次同步命令（见上方 §三条同步路径）
- 若还不行，检查 `.claude/workframe-state/plugin-root.txt` 是否存在、路径是否有效

### rules 里的改动没生效

- 确认你改的是 `.claude/rules/` 根目录或 `local/`，不是 `workframe/core/`
- 确认重启了 Claude Code 会话
- 检查 `.claude/settings.json` 里 `enabledPlugins` 包含 `core@workframe`

### `sync-rules.py` 报 "project directory not found"

- 确认 `--project` 参数是绝对路径
- Windows 路径含中文或空格时用双引号包起来：`--project "C:\Users\<用户名>\My Project"`
