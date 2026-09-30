# 必载纪律的上下文注入

本文档解释 Workframe 的必载纪律怎么进到模型的上下文里：源放在哪、由谁投递、什么时候生效、出问题怎么查。

## 为什么是注入而不是文件

框架有一批**每次都得生效**的纪律（收口闸门、事件流协议、受保护资产清单、响应输出口径……）。它们不能靠模型「记得去读某个文件」——那等于把无条件生效的东西变成有条件的。

同时它们也不该塞进项目根的 `CLAUDE.md` / `AGENTS.md`：项目的判据落点是 `AGENTS.md`（`CLAUDE.md` 只剩 CC 侧的一行 `@AGENTS.md` 导入与几段指路），框架内容挤进去会把项目的东西顶掉，而且两扇门（Claude Code 与 Codex）对项目根文件的大小限制各不相同。

所以框架的做法是：**内容留在插件里，由 hook 在会话与子 agent 启动时把它作为上下文直接投出去**。项目目录里不落任何框架纪律文件，也就没有「镜像过期了没有」这类问题。

## 源在哪

```
plugins/core/context/
├── coverage.json          ← 段落落点台账（给闸读的，不投递）
├── both/                  ← 主会话与子 agent 都要的
│   ├── 10-closeout.md
│   ├── 20-protocol-common.md
│   ├── 30-response-common.md
│   ├── 40-protected-assets.md
│   └── 50-roles-and-flow.md    ← 任务状态流转与签发权限、两套记忆分工
├── main/                  ← 只有主会话要的
│   ├── 10-main-protocol.md    ← 含通用角色表与路由规则
│   ├── 20-signal-intake.md
│   └── 30-response-main.md
└── sub/                   ← 只有子 agent 要的
    ├── 10-sub-protocol.md
    └── 20-response-sub.md
```

每份源的 frontmatter 里写 `supersedes`，声明这一片承接了哪些段落。**那是给校验闸读的，不投递给模型**——打包时会剥掉。

## 谁投递

打包器是 `plugins/core/scripts/inject-context.py`。它读 `both/` ＋ 对应 scope 的目录，行尾归一、剥 frontmatter、按文件边界确定性装箱，输出一片的 hook JSON。

| scope | 挂哪个事件 | 收件人 |
|---|---|---|
| `main` | `SessionStart` | 主会话 |
| `sub` | `SubagentStart` | 每个被调度的子 agent |

**`sub` 只能挂 `SubagentStart`**：`SessionStart` 的注入不传给子 agent，挂错的失效是静默的——hook 照跑、标记照落，只是子 agent 一个字都收不到，表现和「模型偶尔不守纪律」完全同形。

单条 hook 的上下文有字符上限，所以一个 scope 的内容会分成若干片、各挂一条 hook。**片数与每片余量不写死在任何文档或断言里**，要看当前值就跑：

```bash
python "<core插件根>/scripts/inject-context.py" --harness cc --scope main --dry-run
```

它会打印这个 (门, scope) 的完整装箱账：片数、每片余量、每份源各占多少字符。这是这些数字的唯一事实源。

## 两扇门

打包器接 `--harness cc|codex`，两扇门取的是**同一份源**——跑的是同一套框架逻辑，不存在「完整版 / 简化版」。每扇门有自己的挂载表，各自投各自的。

| 门 | 挂载表 | 主会话片 | 子 agent 片 |
|---|---|---|---|
| Claude Code | `hooks/hooks.json` | `SessionStart` 上按片数各挂一条（`--shard i/n`） | `SubagentStart` 上同法 |
| Codex | `hooks/hooks.codex.json`——由 CC 那份派生（生成器 `scripts/gen_codex_hooks.py`，Codex 清单 17 条），每条另带 PowerShell 形态的 `commandWindows` | `SessionStart` 上一条 `--shard all`（全片拼接；片头前多一行 `本会话 harness=codex session_id=<线程号>`，模型据此给 `workframe-event --harness codex`），外加一条种信任 hook | `SubagentStart` 上一条 `--shard all`（`spawn_agent` 型子线程）；**review 型子线程不触发 `SubagentStart`**，由 `UserPromptSubmit` 上的 `--scope prompt` 判定式补投：线程号 ≠ 会话号（或缺 `transcript_path`）即按子线程发 sub 全片、同一轮只发一次；主线程每 K 个 prompt 重发一遍 main 全片（K 读 `.workframe-config.json` 的 `codex.reinject_every_turns`，缺省 30——compact 之后的保险） |

Codex 侧每条注入 hook 都带 `additionalContextLimit: 0`——不带时 Codex 按「limit × 4 字节」截断，中文片会被腰斩（有 warning，但模型拿到的是半份纪律）。另有两条纯文本的 `SessionStart` hook（`heartbeat-check.py` / `session-start-prep.py`）在 Codex 清单里多带 `--output hook-json`：Codex 对以 `[` 或 `{` 开头的 stdout 按 JSON 对象解析、解析不到期望形态就**整条丢弃**，而它们的输出以 `[<项目名>] …` 标签行起头；该参数让脚本把输出包成一条 `hookSpecificOutput`。Claude Code 那份清单不带它，CC 侧的 stdout 逐字节不变。Codex 的 hook 默认不受信任、一条都不跑，信任由装机器写入、升级后由种信任 hook 补种，见 [setup-guide.md](./setup-guide.md) §Codex 门。

**Codex 侧的已知成本**：review 型子线程的每一轮 `UserPromptSubmit` 都重发一次 sub 全片（约 15.8k 字符）——同一轮内幂等、跨轮不抑制，这是有意的取舍（宁多投不漏投）；一个多轮的 review 子线程每轮都付这个成本。

## 生效时序

注入发生在**会话启动**与**子 agent 启动**那一刻。改了 `context/` 下的源之后：

- 主会话侧：**下一次会话**才拿到新版
- 子 agent 侧：**下一次调度**才拿到新版（同一会话内后续派出的子 agent 就已经是新版）

升级插件后同样是重启会话生效（Codex 侧再多一层：升级后失信任的 hook 由种信任 hook 在下一次会话补种，**再下一次会话**才跑）。纪律本身没有需要手动跑的同步步骤——项目目录里本来就没有框架纪律的副本；但上一版装的项目（运行态还在 `.claude/` 下）要先迁移运行态目录、再重启会话，见 [setup-guide.md](./setup-guide.md)「已装项目迁移」。

## 项目自己的纪律放哪

框架不占用项目的判据落点。项目自有的判据写在项目根的 `AGENTS.md`（两扇门都读），更细的工序写成项目自己的 skill。框架的注入片与项目的 `AGENTS.md` 是两层，互不覆盖（这条的代价见文末「能力边界」）。

## 故障排查

### 模型看起来没有遵守某条纪律

先确认它**收到**了。CC 侧在会话里能直接看到注入进来的上下文块；子 agent 侧看它的上下文里有没有片头标记。

**验收口径要按「整个上下文里子串命中」写**，不要按「某个块的首行以什么开头」写：平台会在块前加自己的前缀行，按首行判会稳定假阴性。

### 片数对不上

跑一次 `--dry-run` 看装箱账，再对照挂载表里该 scope 的 hook 条数。两个数必须一致——挂载表少一条，那一片的内容就整片不到达，而没有任何一处会报错。

### 改了源没生效

- 确认改的是 `plugins/core/context/` 下的源
- 确认重启了会话（子 agent 侧是重新调度）
- 确认 `.claude/settings.json` 里 `enabledPlugins` 包含 `core@workframe`

### 单份源装不下

打包器对「单文件超策略上限」直接判红，不截断也不降级。那说明这份源该拆了——按消费者拆（`both/` / `main/` / `sub/`）或按主题拆成两份，**不要指望打包器帮你截**：模型拿到半份纪律是察觉不到的。

## 能力边界

- 字符上限是 **CC 侧实测值**（单条 hook 10,000 UTF-16 单元）。Codex 侧在 `additionalContextLimit: 0` 下实测 4,000,000 字符全量抵达、测不到上限，所以 Codex 清单用 `--shard all` 一条投全片；打包器的分片上限只对 CC 那份挂载表有意义。
- 打包器只保证「片装得下、内容确定」，**不保证片里的文字是对的**。段落有没有落点由 `coverage.json` 与对应的校验闸钉住，而「落点里的文字是否忠实承接原意」没有机器判据，靠人工双检。
- **注入片投递的那几块，项目改不了**：通用角色表与路由规则、任务状态流转与签发权限、两套记忆分工由注入片投递，项目侧没有副本可改。项目需要差异时只能在 `AGENTS.md` 里**补充**（项目级角色、路由偏好、项目自有判据），不能覆盖注入片——两处说法冲突时模型按哪份做不可判，所以别在 `AGENTS.md` 里重写一份流转表或角色表。
- **子 agent 自动压缩之后，拿到的可能是主会话那份纪律片**：Claude Code 子 agent 的上下文自动压缩后会重跑 `SessionStart`，投进来的是主会话片，子 agent 片与角色记忆不补发（单次实测）。收口检查看的是落盘产物，**看不见**这一形态；投递记录（`inject-log.jsonl`）能不能区分它**未验**——取决于那次触发的载荷带不带 `agent_id`、`transcript_path` 指向哪份记录，而实测那次的投递记录早于记这两个字段的版本。
