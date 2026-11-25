# settings 写入边界

必载片 §受保护资产清单 把 `.claude/settings*.json`、用户级 settings 与用户级插件市场注册表列为受保护资产。**豁免写入入口只有一个：装机链路**，其余任何路径都不得写——模型在会话里不改这几份文件，可选配置（如 Agent Teams）由用户自己按框架用户文档「可选配置」一篇手动改。

装机链路的前提写在它自己的实现里（`_settings_io` 的备份 ＋ JSON merge 保留原字段 ＋ 回读 ＋ 失败回滚；写不成只输出手动补丁，不留半写文件）。它写**两类**：

1. 订阅声明那几个键（`extraKnownMarketplaces` / `enabledPlugins` / 市场注册条目）
2. **这台机器上装了 `codex` 时**的 Codex 门那一步——用户级 `config.toml`（本插件 hook 的信任 ＋ 本项目的 trust ＋ 本插件 `enabled`）与项目 `.codex/`。这一类**代用户信任本插件的全部 hook，而受信 hook 脱离沙盒执行**，且不再单问一层确认

装机链路有两个面，**授权形态不同，别混成一句**：

- **经 launcher setup 走**：用户确认的是装机这件事本身，写入前由确认页对「会被修改的已存在文件」逐个点名；第 2 类的事前授权也落在这张确认页上，与第 1 类同一个闸
- **直接敲命令**（`workframe-door --backfill`，以及脱离 launcher 直接跑 `project_scaffold.py --write-subscription`——doctor 的修复指引就会让你走这条）：**没有确认页**，授权就是用户亲手敲了命令。`--write-subscription` 是个独立 flag，按前一面的写法要求它「先有确认页」，等于把一条正确的修复路堵死

**两面的共同前提相同，写入面不相同**：`project_scaffold.py --write-subscription` 只写第 1 类；`workframe-door --backfill` 与经 launcher 的整条链路两类都写。`workframe-door --upgrade` 重种 hook 信任同样写用户级 `config.toml`，它是用户亲手跑的命令，不是模型的写入路径。
