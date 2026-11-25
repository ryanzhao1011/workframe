# 查询命令：给外部程序用的只读接口

框架提供两条只读命令，让桌面应用、编辑器插件、脚本这类**外部程序**读取项目状态，不必自己解析看板、模块树和运行记录——这些文件的格式由框架定义并会演进，由框架解释才不会漂。

| 命令 | 做什么 |
|---|---|
| `workframe-query` | 按主题读取框架数据或协商能力，输出带格式版本的 JSON |
| `workframe-codex-trust` | 窄恢复入口：只为本插件的 hook 补回 Codex 的信任记录，别的什么都不写 |

两条命令都放在 core 插件里（脚本在 `scripts/`，同名入口在 `bin/`）。**日常使用框架用不到它们**；只有你在写读取框架状态的程序时才需要本文。

## 承诺与不承诺

| 项 | 内容 |
|---|---|
| 零写入 | 任何主题、任何参数下都不新建、不修改项目目录（含 `.git/`）与框架状态目录，包括日志、计数、缓存。git 子调用带 `GIT_OPTIONAL_LOCKS=0`，以 `python -B` 运行并设 `PYTHONDONTWRITEBYTECODE=1` |
| 不启动模型 | 不调用任何命令行工具的会话，不消耗额度 |
| 子进程回收 | 本命令启动的子进程在返回前全部结束；不连接、不结束其他会话共用的服务 |
| 输出 | 标准输出只有一个 JSON 对象，UTF-8，无字节序标记；提示与警告不写标准输出 |
| 依赖 | 只用 Python 标准库加框架已有的依赖。涉及 Codex 的主题要求 **Python 3.11 及以上** |
| 耗时 | `project-connection`、`project-status`、`board`、`modules`、`stats` 在一千个任务、两百个文档的项目上目标不超过 1 秒；`gates` 不设目标但可被外部终止 |
| **不在零写入承诺内** | `codex-trust` 主题与 `gates --gate doctor` 的 Codex 信任检查会启动 Codex 的后台服务，它会写 `CODEX_HOME` 下自己的日志与运行态文件。结果里用 `side_effects` 字段声明，范围见下文「`codex-trust` 的副作用范围」 |

## 调用

```
python "<插件根>/scripts/workframe_query.py" <主题> --project "<项目目录>" [主题参数…]
python "<插件根>/scripts/workframe_query.py" --capabilities
```

- `--project` 可以是任意存在的目录。未接入时 `project-connection` 和 `project-status` 返回目录状态，其余主题返回 `not_connected`。
- `--schema workframe.query.v1` 显式指定格式版本；不带时输出框架支持的最高版本。
- 退出码：`0` 成功；`1` 查询失败（标准输出仍是带 `error` 的 JSON）；`2` 用法错误或项目目录无效（同样输出带 `error` 的 JSON）。`--schema` 指定了不支持的版本按查询失败处理，退出 `1`。

**找到插件根**：按你要使用的命令行工具分别读它的市场登记与已装插件清单（Claude Code）或市场登记与插件缓存（Codex）；升级之后重新定位，不沿用升级前的结果。找到了插件根但里面没有查询脚本，说明框架版本低于本接口的最低版本，应提示升级。

## 返回信封

成功：

```json
{
  "schema": "workframe.query.v1",
  "topic": "board",
  "generated_at": "2026-11-11T12:00:00+00:00",
  "framework_version": "1.4.0",
  "project": "D:/work/shop-admin",
  "depends_on": ["projects/board.yaml"],
  "warnings": [],
  "data": {}
}
```

| 字段 | 说明 |
|---|---|
| `schema` | 格式版本，见下文「格式版本与兼容」 |
| `topic` | 回显查询主题 |
| `generated_at` | 生成时间，世界协调时，秒级 |
| `framework_version` | **正在执行本命令的这份框架**的版本，取自插件清单；判断框架版本只认它 |
| `project` | 项目根的绝对路径，正斜杠 |
| `depends_on` | 本次结果依赖的文件或目录，相对项目根；目录以 `/` 结尾，表示目录下任何变化都相关。据此决定哪些文件变化需要重新查询 |
| `valid_until` | 可选，世界协调时：结果随时间失效的时刻（如 `stats` 的「近 N 天」窗口），到点后即使文件没变也应重新查询 |
| `side_effects` | 可选，字符串数组：本次查询在零写入承诺之外产生的副作用，如 `codex_app_server_logs` |
| `external_depends_on` | 可选，`[{ kind, path }]`：项目外的依赖，只用于刷新显示 |
| `warnings` | 字符串数组，不影响结果但值得提示的情况 |
| `data` | 各主题自己的内容 |

失败：

```json
{
  "schema": "workframe.query.v1",
  "topic": "board",
  "depends_on": ["projects/board.yaml"],
  "error": { "code": "parse_error", "message": "…", "detail": { "file": "projects/board.yaml", "line": 214 } }
}
```

失败响应同样带 `depends_on`：能确定的依赖都列出，解析失败的那个文件一定在内——文件修好后据此自动重查。

| `code` | 含义 |
|---|---|
| `not_connected` | 目录没有接入框架 |
| `legacy_layout` | 旧版布局，需要迁移 |
| `parse_error` | 某个项目文件无法解析；`detail` 里有文件与行号 |
| `not_found` | 请求的对象不存在（如指定的检查名） |
| `bad_usage` | 参数错误 |
| `schema_unsupported` | `--schema` 指定的版本本框架不支持；`detail.supported` 列出支持的版本 |
| `internal` | 未预料的错误；`message` 是异常摘要 |

## 查询主题

### `project-connection`

用于巡检项目接入状态。`data` 中的 `connected`、`layout`、`project_name`、`setup`、`support`、`ancestor_project` 字段沿用 [`project-status`](#project-status) 的定义。旧布局仍返回 `layout: "legacy"`，其他功能主题的 `legacy_layout` 错误照常返回。

`depends_on` 覆盖接入标记、状态目录里的 `setup-state.json`、`AGENTS.md`、`.claude/settings.json`、`.claude/settings.local.json`、`.codex/config.toml` 和旧布局路径。缺失的文件也会列出。目录未接入时另列经过的各级祖先标记，直到最近已接入的祖先或盘符根。查询只读取这些接入数据，项目根和 Git 目录无需作为依赖。

调用方在 `--capabilities` 的 `topics` 中找不到本主题时，可使用聚合 `project-status`，并监听它实际返回的依赖。已声明本主题的框架返回失败时，直接处理该错误。

### `project-status`

一次读取接入状态、目录分类、Git 状态和执行插件的来源，适用于打开目录或确认插件来源。查询复用 `project-connection` 的项目读取规则。

| `data` 字段 | 说明 |
|---|---|
| `connected` | 目录根下是否有接入标记 |
| `layout` | `current` / `legacy` / `null`（未接入） |
| `project_name` | 项目配置里的名称，或 `null` |
| `setup` | 初始化状态，未接入为 `null`：`state` 取 `none`（已接入但无初始化记录，视同完成）、`in_progress`、`paused`、`failed`（优先于 `paused`）、`complete`、`unknown`；另有 `steps`、`completed_count`、`last_acceptance` |
| `support` | `{ claude, codex }`：项目文件里是否配置了对应工具的支持，只看项目文件 |
| `ancestor_project` | 目录自身未接入、某个上级目录已接入时，给出那个上级的路径 |
| `directory_kind` | `container` / `near_empty` / `project` / `plain` / `unknown`，按此顺序判定、命中即停：桌面、下载、文档、用户主目录、盘符根为 `container`；除 `.git`、README 之外没有其他内容为 `near_empty`；有版本库或项目清单为 `project`；其余为 `plain`；目录枚举失败为 `unknown` |
| `git` | `{ is_repo, dirty }` |
| `marketplace_source` | `{ kind: directory / git / unknown, source }`：执行本命令的这份框架来自本地文件夹还是 git 来源 |

`depends_on`：接入标记、初始化进度文件、两家工具的项目配置文件、`AGENTS.md`、旧版布局的运行数据目录、项目根（`./`）与 `.git/`；未接入时另列向上查找接入标记经过的各级路径（以 `../` 开头）。

### `codex-trust`

检查本机对该项目的 Codex 授权是否到位。会启动 Codex 的后台服务，较慢（数秒）。

**启动上下文**。预检、恢复、实际会话三处应使用同一份：

| 参数 | 规则 |
|---|---|
| `--codex-exe <路径>` | 给定时精确使用，不回落到 PATH；能解析到原生程序的入口与已知的 npm 入口才接受，解析不了的返回 `unverified`（`entry_unresolved`），不执行来历不明的 shell 字符串 |
| `--codex-home <目录>` | 必须是已存在的绝对路径，不创建；不传时按调用进程的 `CODEX_HOME` 与既有定位规则 |
| `--codex-cwd <目录>` | 随后会话的真实工作目录，缺省为 `--project`；子目录有自己的配置层时必须传 |
| `--codex-profile <名称>` | 可选，原样用于检查与会话 |
| `--codex-config <key=value>` | 可重复，按顺序构造成参数数组，不经 shell；含敏感值时不回显原值 |
| `--timeout-ms <n>` | 默认且最大 30000；超时后不在后台继续 |

`data`：

| 字段 | 说明 |
|---|---|
| `state` | 六态之一，见下表；所有非 `ready` 状态都不应阻止启动会话 |
| `context` | `{ requested_exe, resolved_exe, codex_version, codex_home, cwd, profile, context_id }`；`context_id` 是冻结上下文的摘要，只用于匹配，不是授权凭证 |
| `plugin` | `{ id, version, source, install_root }` 或 `null`：Codex 实际会加载的那份插件；`version` 是实际加载的版本，与信封的 `framework_version` 不同 |
| `hooks` | `[{ key, status, current_hash, enabled, event, source_path }]`：本插件的每条 hook；`status` 取 `trusted` / `modified` / `untrusted` / `unknown` |
| `effective` | `{ project_trusted, plugin_enabled, hooks_enabled, disabled_hooks, disabled_origin }`：有效合成后的结果；`disabled_origin` 取 `user` / `policy` / `none` / `unknown` |
| `problems` | `[{ code, message, component?, detail? }]`，`message` 不含凭据 |
| `snapshot_id` | `sha256:<64 位小写十六进制>`：本次完整目标的摘要，不含生成时间与信任状态；无法完整核实时为 `null` |
| `change_token` | 插件的当前观测摘要，用来判断「插件有没有变」；不代替 `snapshot_id`，也不能用来缓存放行 |
| `recovery` | `{ eligible, reason, required_action }`：只反映技术条件，`eligible` 不等于用户已授权 |

六态按下表顺序判定、命中即停：

| 顺序 | 状态 | 判据 |
|---|---|---|
| 1 | `version_unsupported` | Python 低于 3.11，或已确认 Codex 缺所需协议；后台服务起不来不算此态 |
| 2 | `disabled_by_user` | 能核实有效配置里插件、hooks 总开关或任一 hook 被显式关闭；管理员策略关闭时 `disabled_origin` 为 `policy` |
| 3 | `not_installed` | 能从有效的安装与市场登记证实目标插件不存在；不能用 hook 清单为零条来证明 |
| 4 | `unverified` | 超时、后台服务不可达、来源不明、多个安装根、缺或重复 key、清单不一致、信任状态或 hash 未知、上下文无法保持一致、目录重叠、副作用范围无法确定等核实失败 |
| 5 | `needs_trust` | 结构与来源链已核实，至少一条 hook 为 `modified` 或 `untrusted`，或项目未受信使项目层未生效；只有 hook 未受信、其余前提齐备时 `recovery.eligible` 为真 |
| 6 | `ready` | 完整非空清单一致、有效启用、每条 hook 的当前 hash 都受信；只证明本次配置快照，不证明正在运行的会话已经执行了 hook |

`problems[].code` 取值：`not_installed`、`version_too_low`、`project_not_trusted`、`hooks_untrusted`、`plugin_version_mismatch`、`unknown`、`entry_unresolved`、`context_unverifiable`、`source_unverified`、`manifest_mismatch`、`duplicate_key`、`multiple_roots`、`hook_status_unknown`、`disabled_by_user`、`policy_disabled`、`app_server_unavailable`、`timeout`、`protocol_unsupported`、`protected_path_overlap`、`side_effect_scope_unknown`。以后新增属于兼容变更。

**退出码**：六态都是一次成功完成的分类，退出 `0`，必须读 `data.state`；项目文件解析失败等通用失败退出 `1`，用法错误退出 `2`。

**`codex-trust` 的副作用范围**。`side_effects` 含 `codex_app_server_logs`，覆盖 Codex 的 `log/` 目录、后台服务的控制目录、日志库 `logs_*.sqlite` 与其 `-wal`、`-shm`，以及按版本实测登记的运行态文件：codex-cli 0.167.2 为 `tmp/` 与 `.tmp/` 下的临时目录、`.sandbox/` 下的日志、任意 `*.sqlite-shm`（前提是对应数据库本体与 `-wal` 字节不变）。每次查询只在前后对比信任判断读到的输入：`config.toml`、`CODEX_HOME/.hook-trust/`、各市场已安装的 `plugins/cache/<市场>/core/`（路径上或其中的链接会跟进：链接本身与它指向的内容都比对）；这些有任何变化（不论是本次查询还是别的进程造成）都返回 `unverified`（`side_effect_scope_unknown`）。窄恢复入口用同一范围，另加它自己的精确字段与信任文件回读；不在 `plugins/cache/<市场>/core/` 下的插件根（如从本地市场目录直接加载的，不论是否位于 `CODEX_HOME` 内）不在对比范围内。会话与历史库、其他数据库、缓存、认证、其他插件随时被正在运行的 Codex 改写，不在每次查询的对比范围内：已登记版本的后台服务在这些位置会写什么，由每个版本一次的隔离实测界定，运行时不再逐次检出。未登记的 Codex 版本返回 `unverified`（`side_effect_scope_unknown`）并不启动后台服务。

启动任何后台服务之前：

- 先用真实路径核对 `CODEX_HOME`、用户配置文件、日志、控制目录、模型缓存和远程精选插件的缓存与安装暂存目录。项目目录、框架状态目录和实际 Workframe 插件安装根有重叠时返回 `unverified`（`protected_path_overlap`）。同在 `plugins/cache/` 下的独立子树可以通过。副作用路径无法确定时返回 `unverified`（`side_effect_scope_unknown`），包括用户配置读不了以及守卫查询遇到未登记的日志或数据库重定向。
- 目标 Codex 版本从未在这个 home 里运行过时，它第一次启动会创建初始化文件（codex-cli 0.167.2：`installation_id`、`skills/.system/`、`state_5.sqlite`、`goals_1.sqlite`、`memories_1.sqlite`、`queue_1.sqlite`，以及插件功能开启时的 `.tmp/plugins.sync.lock`）。这些都不在上面的范围内，所以缺任何一个时同样返回 `unverified`（`side_effect_scope_unknown`），不启动后台服务；先在该 home 里运行一次 Codex 再查询即可。

桌面客户端在用户主动打开或继续普通 Codex 会话时使用独立的启动观察。每次用这次会话的最终环境、目录、profile 和覆写参数向 Codex 查询。主动观察允许 Codex 在自己的目录写日志、缓存、数据库及配套临时文件，检查已知目标以及显式重定向的真实路径。发生重叠或路径无法核实时返回 `unverified`。检查范围限于这些路径及其父目录，更深层的链接由使用方管理。查询在确认自己启动的服务已经退出后才返回观察结果。

| Windows Codex CLI | 主动启动观察 | `codex-trust` 守卫查询与窄恢复 |
|---|---|---|
| 0.167.2 | 支持 | 支持 |
| 0.169.0 | 支持 | 未核实 |

0.169.0 的启动观察可以报告 hook 信任状态，恢复仍须由已验证的入口执行。已登录的隔离实测中，过期模型缓存在 0.167.2 启动之后自行刷新，0.169.0 在观察窗口内保留过期值，显式查询模型之后刷新。

### 窄恢复入口 `workframe-codex-trust restore`

```
workframe-codex-trust restore --project <根目录> --codex-exe <入口> --codex-home <目录> --codex-cwd <目录> \
  --expect-snapshot <snapshot_id> --expect-source <来源身份 JSON> --schema workframe.codex-trust-restore.v1 \
  [--codex-profile <名称>] [--codex-config <key=value> ...] [--timeout-ms <n>]
```

用于 hook 因插件升级而变成未受信时，在**调用方已取得用户同意**之后补回信任。框架不保存任何授权记录，用户是否同意由调用方自己记录。

固定顺序：①核对参数与目录重叠；②按冻结上下文新建后台服务，读有效配置与完整 hook 清单，重算 `snapshot_id` 并与 `--expect-snapshot` 比对、来源与 `--expect-source` 比对，任何一项不一致都不写；③确认没有被用户或策略关闭、项目已受信；④只为尚未受信的 hook 生成写入，每条只写 `hooks.state.<完整 key>.trusted_hash`，任一条缺 key 或 hash 则整批不写；⑤向用户层配置发一次带版本条件的写入；⑥回读，逐条 key 与 hash 一致且全部受信才算恢复成功，只收到「已接受」不算；⑦回收自己启动的子进程，输出唯一一个 JSON。

输出是独立信封 `{ schema: "workframe.codex-trust-restore.v1", operation, generated_at, framework_version, project, side_effects, data }`，`data` 含 `result`（`restored` / `unchanged` / `not_restored` / `write_unconfirmed`）、`state_before`、`state_after`、`context_id`、`snapshot_before`、`snapshot_after`、`write_outcome`（`none` 可证明没写 / `confirmed` 已回读确认 / `partial` 只能证实部分 / `unknown` 发出后无法核实）、`attempted_keys`、`verified_keys`、`remaining_keys`、`error`、`restart_required`。不能从「没收到成功响应」推出 `none`。

| 退出码 | 含义 |
|---|---|
| `0` | 已恢复或无需恢复、当前目标已确认 `ready` |
| `1` | 确定的拒绝或失败：快照或来源不符、被关闭、项目未受信、条件写冲突、回读不符等；写入可能已部分生效时仍以 `write_outcome` 为准 |
| `2` | 用法错误或不支持的恢复协议（含不支持的 `--schema`） |
| `3` | 写请求发出后超时、失联或无法回读，`message` 写「写入结果未确认」；只做只读复查，不要盲目重试 |

写入边界：只允许 Codex 用户配置里本插件当前完整 key 的 `trusted_hash` 改变，以及 Codex 写信任时同步维护的信任存储（codex-cli 0.167.2 为同一完整 key 下的 `trusted_content_hash` 与 `CODEX_HOME/.hook-trust/` 下按用户配置文件路径与插件 ID 确定的那一个文件），加上与查询相同的日志类副作用；项目目录（含 `.git/`）、框架状态目录、插件目录、其他插件与用户自己的 hook 零写入。

### `board`

| `data` 字段 | 说明 |
|---|---|
| `statuses` | `[{ key, order }]`：状态的全集与显示顺序，不认识的 `key` 原样显示 |
| `tasks` | 任务数组 |

每个任务含：`id`、`title`（缺失为空字符串）、`status`（缺失为 `"unknown"`）、`priority`、`assigned_to`、`tags`、`module`、`created_at` / `updated_at` / `completed_at` / `cancelled_at`、`notes`、`description`、`blocked_reason`、`cancel_reason`、`actual_output`、`depends_on`、`signoff_text`。除特别说明外，可缺的字段为 `null`。日期原值是日期时输出 `YYYY-MM-DD`（日历日，不补时区），是时间点时输出带时区的 ISO-8601，无法识别时为 `null` 并在 `warnings` 里说明。`signoff_text` 是签发记录整理成的可读文本，每个键、每个列表项、多行文字的每一行都出现在文本里。

`depends_on`（信封）：看板文件。看板路径取项目配置里的自定义值，没有则用默认路径；配置带 UTF-8 字节序标记时同样读取。

### `modules`

返回文档目录所需的全部信息，只给目录与元数据，不给正文。

| `data` 字段 | 说明 |
|---|---|
| `basics` | 基础模块数组，每项 `{ name, status, overview_path, shared, subs }` |
| `ledgers` | 项目账本类文档 |
| `cross_module` | 项目级的方案与计划 |

子模块：`{ name, path, status, overview_path, stale, docs, requirements, error }`。文档：`{ kind, title, path, type, status, updated, unwritten, meta_error }`，`kind` 取 `overview` / `architecture` / `api-surface` / `data-model` / `code-map` / `decision` / `research` / `other` / `ledger` / `plan` / `shared`；`title` 取文档元数据，没有则取元数据区之后的第一个一级标题（元数据区里的注释不算标题）。单篇元数据无法解析时该篇照常返回，`meta_error` 写原因；子模块自身的 `submodule.yaml` 无法解析时该子模块照常返回，`error` 写原因，整个主题不因此失败。

`unwritten`（新增字段，旧版框架输出里没有，缺省按 `false` 读）：文档仍是建骨架时的原样、没人写过正文时为 `true`；元数据无法解析或读不到时恒为 `false`，坏文档照常可导航。`requirements` 每项 `{ slug, title, status, subs }`，`subs` 每项 `{ slug, prd_path, status, description, unwritten }`，其中 `unwritten` 判的是该子需求的 `prd.md`。只认布尔 `true`，字符串 `"false"` 之类不当真值。

`stale` 为 `null` 或 `{ reasons, files }`：`reasons` 是 `[{ kind, path, message }]`，`files` 是去重后的代码文件路径列表。

`depends_on`（信封）：模块目录（以 `/` 结尾）、待更新标记文件、账本文件。

### `gates`

参数 `--gate doctor` 或 `--gate close-check`，一次运行一类。

| `data` 字段 | 说明 |
|---|---|
| `gate` | 回显 |
| `summary` | `{ ok, info, skipped, warn, error }`：各级别的条数 |
| `checks` | `[{ id, name, level, message, detail, doc_path, files }]`；同一项检查给出多条结论时每条一项，共用同一个 `id`。结论针对单份文件时（`stale-clean` 里挂着待更新标记的子模块、`nav-index` 的各条），`files` 只含那份文件，`doc_path` 是它（不是 Markdown 时为 `null`）；其余结论的 `files` 是该项检查读取的输入 |

`level` 取 `ok` / `info` / `skipped` / `warn` / `error`；`skipped` 表示该项无对象可查或不适用，**不等于通过**。以后新增的级别属于兼容变更，调用方对不认识的级别应按「未识别」显示、不计为通过。两类检查都复用现有的纯判定函数，不追加计数日志、不写初始化进度；`doctor` 的 Codex 信任检查会启动后台服务，结果的 `side_effects` 声明它。

`depends_on`（信封）：空数组。闸的结果不随文件变化自动重查，应每次实际执行。

### `stats`

参数：`--days <天数>`（7、30 或 90，默认 30）、`--weeks <周的划分>`（必填）。周的划分由调用方按本机时区算好，是一个 JSON 数组，从新到旧，每项 `{ "date": "2026-11-09", "start": "2026-11-09T08:00:00Z", "end": "2026-11-16T08:00:00Z" }`：`date` 是该周周一在本机的日历日，`start` / `end` 是该周起止时刻换算成的世界协调时。

`data` 分三组，每组独立成败：

| 组 | 字段 | 说明 |
|---|---|---|
| `board` | `{ state, error?, tasks_by_status, tasks_by_role }` | 来自看板；不含已取消 |
| `completion` | `{ state, error?, weeks, without_date }` | 按 `--weeks` 的划分归属；没有任务的周也返回，`count` 为 0；`without_date` 为已完成但没有完成日期的任务数 |
| `events` | `{ state, error?, range, skill_usage, skill_invoked, signals, unparsed_lines }` | 来自事件记录；`skill_usage` 是模型自报的使用次数、`skill_invoked` 是 hook 记的调起次数，两者**不相加**（Codex 门没有调起的采集通路），技能名已把裸名与 `core:` 名归一；`signals` 含 `user_correction`、`turn_failed`、`task_blocked`，各带 `count` 与 `sources`（该事件在哪些工具里有采集通路，`turn_failed` 目前只有 `["claude"]`——不要把 0 解释成「没有发生」） |

`state` 取 `ok` 或 `error`，`events` 组另有 `unrecorded`：项目里一份事件记录文件都没有（不是「有文件、零条」），此时不给数字，调用方应显示「未记录」而不是 0；`error` 时不给该组的数字，`error` 为 `{ code, message, detail }`。统计口径由框架按它的事件定义实现，调用方不重算。事件读取覆盖主文件 `events.jsonl`、尚未合并的补记文件与合并时改名留下的副本，三处合起来按整行内容去重；已知限制：同一秒内同一 skill 的两条逐字相同的记录会被当成一条。

`depends_on`（信封）：看板文件、状态目录下的事件文件（目录形式）、项目 skills 的两种目录布局（`.agents/skills/` 与 `.claude/skills/`，目录形式；技能名归一要看项目里有没有同名技能）。`valid_until`：时间范围起点越过最早一条已计入事件的时刻，与 `--weeks` 第一项的 `end`，取较早者。

### `--capabilities`

不需要 `--project`，返回这份框架支持的能力，供调用方启动时协商：

```json
{ "schema": "workframe.query.v1", "topic": "capabilities", "framework_version": "1.4.0", "project": null,
  "depends_on": [], "warnings": [],
  "data": { "schemas": ["workframe.query.v1"],
            "topics": ["project-status", "gates", "codex-trust", "board", "modules", "stats", "project-connection"],
            "stats_stream": "workframe.stats-stream.v1",
            "nav_refresh": { "schema": "workframe.nav.v1", "command": "module_init.py",
                             "operations": ["refresh-index", "check", "convert-layout"] },
            "codex_trust": { "states": ["ready", "needs_trust", "disabled_by_user", "not_installed", "version_unsupported", "unverified"],
                             "recovery": { "schema": "workframe.codex-trust-restore.v1", "command": "workframe-codex-trust", "operations": ["restore"] } } } }
```

`topics` 只列这份框架**已实现**的主题；窄恢复入口是否存在按 `codex_trust.recovery` 判断，不凭版本号猜。支持长连接的统计消费者可在 `stats_stream` 为 `workframe.stats-stream.v1` 时运行 `python -B <脚本> --stats-stream`。每行输入一个以 `stats` 开始的 UTF-8 JSON 参数数组，每行输出 `{exit_code,result}`，其中 `result` 为普通查询信封。输入结束后进程退出。无此能力时继续逐次运行 `stats`。导航段刷新是否由框架脚本承担按 `nav_refresh` 判断：有它时，`scripts/module_init.py --project <dir> --refresh-index | --check | --convert-layout [--scope <路径>…] --json` 输出 `nav_refresh.schema` 那一版的结果（`{schema, command, results: [{path, segment, status, reason, line}], confirm?}`，成功与失败都输出）；没有它的旧框架只能由模型按规程刷新。

## 调用约定（建议）

- 一次性查询以 `python -B` 启动，环境带 `PYTHONDONTWRITEBYTECODE=1`、`GIT_OPTIONAL_LOCKS=0`。轻量主题超时 10 秒，`gates` 120 秒，`codex-trust` 30 秒。超时或取消时结束该子进程的整个进程树，树外的共享服务可继续运行。
- 支持流式统计时，子进程可在多个请求之间复用增量读取索引。索引只保存在该进程的有限内存里，超出索引容量的事件流回到完整读取。保持有限并发并在空闲或退出时回收该进程。
- 结果按「项目 + 主题 + 参数」缓存；监听 `depends_on` 里的路径，变化或到了 `valid_until` 就标为过期并重查。失败响应的 `depends_on` 同样要监听。
- 迟到的旧结果不要覆盖界面上更新的结果；同一「项目 + 主题 + 参数」在同一时刻只跑一个。`gates` 不缓存。
- 界面若展示上次运行的闸结果，可独立监听项目文件变化并标记结果过期。每次实际运行闸仍由用户发起。
- 标准输出按 UTF-8 解码后解析 JSON，解析失败按内部错误处理。

## 格式版本与兼容

| 规则 | 说明 |
|---|---|
| 版本标识 | `workframe.query.v<整数>` |
| 兼容变更（不提高版本） | 新增字段、新增主题、新增枚举值（调用方对不认识的枚举值要有退化显示）、新增错误码 |
| 不兼容变更（提高版本） | 删除字段、改变字段的类型或含义、改变已有枚举值的含义 |
| 同时支持的版本 | 框架发布新版本格式后，旧版本格式至少保留一个次版本周期；用 `--schema` 显式指定。不带 `--schema` 时输出框架支持的最高版本；指定了不支持的版本时返回 `schema_unsupported`，退出 `1` |
| 调用方的行为 | 启动时用 `--capabilities` 取得支持的版本列表，选双方都支持的最高版本，之后每次查询都带 `--schema`；没有交集时，框架更旧则提示升级框架，框架更新则提示升级调用方 |
| 返回结构 | 调用方除格式版本外还应校验返回结构：必填字段与类型、`topic` 与 `project` 与请求一致、计数非负 |
