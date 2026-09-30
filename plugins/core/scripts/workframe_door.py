#!/usr/bin/env python3
"""装机器 `workframe-door`：一次把两扇门装好（`bin/workframe-door` 的实现）。

    workframe-door --codex   [--project P] [--marketplace SRC] [--marketplace-name N]
    workframe-door --check   [--live] [--project P]
    workframe-door --upgrade [--project P] [--marketplace-name N]
    workframe-door --cc      [--project P]
    workframe-door --backfill [--project P] [--marketplace-name N]       # 已装项目：只补缺的那扇门
    workframe-door --both    [...]      # 默认：探测到 codex 可执行文件就装 Codex 门；CC 门只读核
    workframe-door --migrate [--project P]                               # 已装项目迁移：只出 plan
    python <插件根>/scripts/workframe_door.py --migrate --apply --confirm <码> --project P   # 执行（用户本人跑）

**`--migrate`**：实现在 `workframe_migrate.py`（防误跑与能力边界见那里的抬头）。`--apply` / `--confirm` 只能与
`--migrate` 同用，否则参数错、零写入——漏打 `--migrate` 时不能落进默认的 `--both` 分支去装 Codex 门；
`--confirm` 还必须带 `--apply`——漏打 `--apply` 时不能静默重出 plan、让人以为执行过了。

**为什么放 core 而不是 launcher**：launcher 定位 core 的方式只读 CC 专属的市场清单，只装了
Codex 的用户跑 setup 会起不来；core 脚本恒可 `parents[1]` 自定位。launcher 只剩薄壳。

**按项目启用**：`codex plugin add` 把 `[plugins."core@<mkt>"] enabled = true` 写进**用户**配置，此后任何
目录的 Codex 会话都会加载本插件。门把用户层那一键置 `false`，并在项目 `.codex/config.toml` 的 managed 段
段首写 `enabled = true`——插件只在跑过 `--codex` 的项目里加载（项目层只在 trusted 时可见，门本来就代写
trust）。每台机器的每个项目各跑一次 `--codex`。

**`--codex` 的步骤**（每步的判据与退出码见 `EXIT_*`；任一非零都**不打印「就绪」**）：
   0 预检（**写任何东西之前**）：把 managed 段拼进项目 `.codex/config.toml` 当前文本、整份语义判定
     （段外同名声明 / 段后键被段内表吸收 / 文件解析不了）＋ 段内被手改 / 本项目 Codex 门已被用户关闭
     → 否则 3，项目树、用户 config、插件缓存一个字节不动
   1 `codex --version` ≥ 0.153.4 → 否则 2
   2 `codex plugin marketplace add <src> --json`（`alreadyAdded` 视为成功）→ 失败 2（同名市场已登记时 Codex
     可能拒绝，也可能不拒、直接换掉那条登记，见 §已知形态）；回显的市场名与
     `--marketplace-name` 不同时按回显名再预检一次，不过 → 3（此时市场已注册、插件未装）
   3 装前核对（`require_pending_version`，收在 `step_plugin_add` 入口，`--upgrade` 本地源那一路的两个重装点同样经过它）：
     `codex plugin list --json --available -m <回显的市场名>` 里本市场 core 那一行（`installed[]` 与 `available[]` 都扫）
     的 `source.path` 读版本，判据同 3'（`version_mismatch`）→ 不同 → 2，`plugin add` 不跑（插件缓存与用户层都不动；
     文案如实说步 2 是新登记还是早已登记；市场那份不比本命令新时出路见 `pending_stop_note`，不给 `--upgrade`）；读不出
     放行、打一行 `i`，由 3' 兜底。核对的那一行是步 3 之前的子行，不占步号。过了才
     `codex plugin add core@<mkt> --json`（用户层此刻被写成 enabled = true）→ 失败 2
   3' 装后核对（`require_installed_root` ＋ `require_loaded_and_echoed`，**写任何项目文件之前**）：Codex 实际加载的
     那一份（由 `hooks/list` 里本插件条目的 `sourcePath` 反推，各条须反推出同一处）与 `plugin add` 回显的那一份
     （`installedPath`），**各自独立**核：插件版本 == 本命令所在插件根的版本、hook 冻结清单形态完好 → 否则 2。两份
     是不是同一处不作要求（两个输出的路径写法能否归一没有真机样本，不押在它上面）。**任何一侧读不出版本、或反推
     不出实际加载的是哪一份，都按不一致处理**（不拿本命令自己的根顶上）。本命令这一侧的版本**开跑时读一次、存下**
     （`snapshot_own`），之后都拿存下的比：`--upgrade` 刷新 Codex 侧时，门所在的目录可能被删（安装缓存的旧版本
     目录）或被原地刷新（git 源的市场快照），再现读就不是本进程在跑的那一份。开跑时读不出就在那时停
     （`require_own_version`），什么都不动。
     「写之前」的例外**按判据切、不按格子列**。在经过步 3' 的四条路径上（`--codex`、`--upgrade`、`--both`（缺省）、
     `--backfill`），项目文件只有两个写入点：`do_backfill` 里 CC 那半的 `write_subscription`（→ `_settings_io`），与
     `do_codex` 里的 `step_roles`（→ `codex_roles.generate`）。`--migrate` 与 `--check --live` 不经过 3'，也各自会写
     项目（前者写 plan 与搬迁；后者真跑一次会话，由受信 hook 写状态目录），不在这句的范围里。`step_roles` 之前跑过的
     安装根核对只有 `require_loaded_and_echoed(visible, installed)` 与 `step_roles` 入口那一道（步 3 的装前核对核的是
     市场里将要装的那一份，不是安装根），而 Codex 实际加载的那一份只在
     `visible`（写之前那次 `hooks/list`）非空时核得到。所以先写后核的**恰是**两类：①`--backfill` 先写了 CC 那半；
     ②写之前那次 `hooks/list` 里看不见本插件的 hook——不论成因，也不论模式与回显。复算时**不预设写入点叫什么**：在本
     脚本与它调用到的每个模块里 grep 写原语（`write_text` / `write_bytes` / `open(…, "w"|"a")` / `os.replace` /
     `rename` / `mkdir` / `shutil.` / `unlink`，以及起子进程的 `subprocess`），沿调用链追到上面四条路径，筛出落在项目
     目录里的，再逐个看它之前哪些 `require_*` 已经跑过。筛掉的：`step_write` 写用户 config、`step_seed` 写
     `CODEX_HOME`；codex CLI 与 app-server 以项目为 cwd 运行，它们写不写项目没有真机样本，不算本脚本的写入点。
     下面按模式、回显与成因展开的是**例子，不是穷举**（三者的组合会随 Codex 的行为增长，逐格列举列不全；全称口径以
     上面的判据为准）：
       (a) `--upgrade` 遇旧形态项目（段里没有启用表、落段之前 `hooks/list` 为 0 条）：安装根要等段落盘后才解析
           得出，角色先按本命令自己的插件根写、再核对（本命令所在的目录已被本次重装 / 刷新删掉或改写时，这一步不
           生成角色、就此停下）；
       (b) `--backfill` 两扇门都缺：CC 那半先写订阅声明（`.claude/settings.json` 及 `logs/` 下的备份），Codex 那半
           的核对在其后——写进去的是订阅声明，与版本无关；
       (c) `--codex`，用户层没被写回 true、项目已 trust、落段之前 `hooks/list` 里看不见本插件的 hook，且 `plugin add`
           **没回显** `installedPath`：与 (a) 同形，整份先写后核；
       (d) 与 (c) 同形，但**回显了** `installedPath`：回显的那一份在写之前核过，Codex 实际加载的那一份要等段落盘后
           才看得见，在写之后核；
       (e) `--upgrade` 走本地源重装那一路，其余同 (d)：回显的那一份在写之前核，实际加载的那一份在写之后核（不回显时
           同 (a)）；
       (f) 用户层是 true、Codex 却列不出本插件的任何 hook（`hooks.codex.json` 整份没被解析）：回显给了就先核回显的
           那一份，角色照写，随后在步 5 以 3 停下（0 条成因）——实际加载的那一份没有 `sourcePath` 可反推，版本无从核起。
       (c)(d)(e) 依赖 Codex 的回显形态，按现有样本（`plugin add` 会把用户层写回 true）走不到；(f) 要 Codex 解析不了
       本插件的 hook 清单（坏发版）。
     失败时文案都点名本次写过的文件；因安装根停下的那几格（(a)(c)(d)(e)）另说明此后本项目会加载 Codex 侧那一份
     （启用表已写、项目已 trust）——实际加载的那一份反推不出时，这句说「安装根反推不出」，不点名回显的那一份
   4 角色：`agents/*.md` → `.codex/roles/*.toml` ＋ `.codex/config.toml` managed 段（段首启用表 ＋ 角色注册）
     ＋ 账本（`codex_roles`）
   5 `hooks/list`（app-server，cwd = 项目）**按 `pluginId` 过滤**后条数 == 插件自带 manifest 条数，
     每条 `command` 非空、`errors` 空 → 否则 3；其他源的条目不计、不动。0 条时按实测分辨成因
   6 **先打印**每条：事件 / trustStatus / 执行什么；明说「下面 N 条将被代你信任；受信 hook 脱离沙盒
     执行」；项目 trust 与用户层 `enabled = false` 也在这一步列出
   7 `config/batchWrite` 一次 upsert（hook 信任 N 条 ＋ 项目 trust ＋ 用户层 enabled = false），不带
     `expectedVersion`（首写没有可期望的版本；写的只是本框架自己的键）→ 非 ok 4
   8 回读：用户层 enabled 是 false（读 `layers[type=user]`，不读合成值）、从项目看的合成值是 true、
     `hooks/list` 全 `trusted`、项目 trust、角色注册 → 任一不符 5，文案点名是哪一层；回读过了才
     **明说「已代你信任 N 条 hook」**（用户 2026-09-13 拍板的字面：先列清单再写，写后明说）
   9 写种信任记录 `<CODEX_HOME>/.workframe/codex-trust-seed.json`（审计记录；G 补种 hook 也写它）
  10 验活只在 `--check --live` 做（真跑一次 `codex exec`，用本机 127.0.0.1 上立即回 400 的 HTTP 桩当
     model provider：hook 全跑、4 s 内结束、不等鉴权；Codex 仍会记一份失败的 rollout——知情项）
  11 孤儿：`config.toml` 里前缀为本插件 id、但不在本次 `hooks/list` 里的 `hooks.state` 记录，
     只计数告知，**不删**
  步 4–6 任一失败时用户层停在 true（步 3 写回的），失败文案会明说这一点。失败文案也会明说本次写过
  哪些项目文件、一个没写就说没写（`_wrote_note`，这半句只从它出）。

**hash 只从 `hooks/list.currentHash` 读，不自己算**：Codex 的 hash 盖的是 `${CLAUDE_PLUGIN_ROOT}`
字面、且 Windows 上只盖生效命令——任何本地重实现都会静默漂移。

**`--upgrade`**：先预检，再**按市场源形态分两条路**刷插件缓存 → 从步 4 起重做。源形态读
`codex plugin list --json` 的 `installed[].marketplaceSource.sourceType`（`marketplace list --json`
只在 git 登记的那一行带 `marketplaceSource`，本地登记的那一行只有 `{name, root}`——要认出的恰是本地源，它在
那里没有正面字段；实测 codex-cli 0.153.4）。
  · **git 源** → `codex plugin marketplace upgrade <mkt> --json`（一条命令同时刷快照并重物化已装插件
    缓存；改动过的 hook 随之 `modified`；**不改用户层启用值**）。用户层 false 且项目未 trust（例如
    clone 到第二台机器）时在写项目文件之前以 3 停下、出路「先跑 `--codex`」（停下之前
    `marketplace upgrade` 已经跑过、插件缓存已刷新——等同裸跑了一次那条命令；项目文件与用户配置不动）。
  · **本地目录源** → 那条命令对它**根本不适用**（实测 codex-cli 0.153.4：rc=1、**stdout 空**、stderr
    `… is not configured as a Git marketplace`），改走 `codex plugin add <id> --json` 重装（重装之前先过步 3 的装前
    核对：市场里那一份与本命令版本不同、且读得出时就在重装之前停下，用户层不会被写回 true）——实测是
    **整体重物化（替换语义）**：缓存里被删掉的文件补回、被塞进去的陌生文件清掉。**⚠️ 这一路会把用户层
    `enabled` 写回 `true`**（与步 3 同），随后步 7 再把它置回 `false`——上一行那句「不改用户层启用值」
    **只对 git 源那一路成立**；中途失败时用户层停在 true，失败文案会明说。相应地，「用户层 false 且项目
    未 trust 就以 3 停下」在这一路上也不成立：`plugin add` 已经把用户层写成 true，装机照常走完。
  源形态**分辨不出**时（命令失败 / 解析不了 / 本市场此刻没有已装插件）先试 git 那条，撞见上面那句
  stderr 再落到 `plugin add`——「分辨不出」不该变成「升不了」。
用户即使绕过本命令直接敲 `codex plugin marketplace upgrade`，下一次 Codex 会话里的
`codex-trust-seed.py` 也会补种并明说。

**`--backfill`**：**已装项目补上缺的那扇门**——装机链路只在建项目那一刻接两扇门，此前装的项目不会自己
长出第二扇。判两扇门此刻的状态（CC 门看 `check_cc` 同一条判据；Codex 门看项目 `.codex/config.toml`
managed 段的状态与段首启用表——**只看项目侧**：hook 信任、项目 trust、用户层启用值在各人的用户配置里，本命令不核，
两扇都在时收尾说「项目侧已接；本机未核，跑 --check」），**只补缺的那扇**：CC 缺 → `project_scaffold.write_subscription` 写两项
订阅声明 ＋ 用户级市场注册表；Codex 缺 → 走 `--codex` 全流程。**重跑零改动**（已接的那扇根本不调写入方）。
市场来源**只从两扇门已有的注册面读、不猜**（顺序见 `locate_market`），读不到就停下、不拿猜的值去写一份
会进 git 的声明。**不改用户自己关掉的 Codex 门**，managed 段被手改时停下报告、不覆盖。**不含删除动作。**

**`--check`**：只读。用户层 `enabled = true` 算问题（exit 5，`USER_LAYER_TRUE_IS_PROBLEM`）；用户层缺表
放行（缺表 ≡ 未启用）；段首启用表被用户改成 false 报「已关」信息、不算问题。安装根与本命令版本不同
（或任一侧读不出）、安装根的 hook 冻结清单缺失或形态坏、反推不出 Codex 实际加载的是哪一份，都算问题，且不让
其余检查项断掉。静态有问题时
`--live` 走不到。

**成功出口附带 AGENTS.md 提示**（不改退出码）：Codex 读 AGENTS.md、不读 CLAUDE.md 与 `.claude/rules/`，门装好
不等于 Codex 看得到项目内容。「这个项目是什么」只剩框架占位或 AGENTS.md 不存在时打一行 `!`，有只 CC 读得到的
内容面时打一行 `i`；在哪些出口说、为什么只在成功时说，见 `agents_md_notes`。

**`--cc`**：只读核 `.claude/settings.json` 的 `enabledPlugins` 有 `core@…`——**这个子命令不写 settings**。
写 settings 的入口有两个：`/core:onboard`（可选环境配置）与装机链路（只写订阅声明那几个键），
两者都走 `_settings_io` 的备份 ＋ merge ＋ 回读。读路径也用它，与 doctor 同一个 BOM 口径。
**装机链路自己又分两个面，授权形态不同**：**经 launcher setup 走**时由确认页对「会被修改的已存在
文件」逐个点名；**直接敲命令**时没有确认页、授权就是用户亲手敲了命令——本脚本的 `--backfill`
与脱离 launcher 直接跑的 `project_scaffold.py --write-subscription` 都属后者。
**`--backfill` 是装机链路的一个面，不是第三个入口**：它调的就是 `project_scaffold.write_subscription`。

**不做卸载**：卸载含删除动作；产物账本 `.workframe/managed-files.json` 已把「哪些文件是生成器写的」
记下来，将来卸载只删账本内且 sha 仍相符的文件。

**默认市场源**：`ryanzhao1011/workframe`（`owner/repo` 形态。隔离 `CODEX_HOME` 里对 GitHub 公开仓实测过
注册市场、装插件与 `--upgrade` 刷新（无鉴权的只读拉取；Codex 把它登记成 `source_type = "git"`、`source` 为完整的
https URL）；需要鉴权的远端未验）。装机时用 `--marketplace` 覆盖。

**已知形态：同名市场、不同源**（开发机上是常态——本地仓已登记成 `workframe`，而默认源是公开仓）。
`codex plugin marketplace add` 遇到同名的已有登记时怎么做（隔离 `CODEX_HOME` 实测 codex-cli 0.153.4，与上游 tag
`rust-v0.153.4` 的 `codex-rs/core-plugins/src/marketplace_add.rs` 对得上）：
  · **以已登记的那一份此刻仍然有效为前提**（本地登记：那个目录还在、且是合法市场；git 登记：Codex 的克隆目录
    `<CODEX_HOME>/.tmp/marketplaces/<名>/` 还在）：四种先后组合里三种被拒（rc=1、stdout 空、stderr
    `already added from a different source`，登记不变）；**只有「已登记的是本地源、这次加的是 git 源」、且 Codex
    的克隆目录 `<CODEX_HOME>/.tmp/marketplaces/<名>/` 不在时不被拒**——Codex 的 git 分支只看克隆目录在不在、不按名字
    查已有登记，于是 rc=0、`alreadyAdded` 为 false，**那条本地登记被直接换成 git 登记**（克隆目录在时——手改过登记、
    或中途失败留下的残留——同一组合照样被拒，本地登记不变）。与插件装没装无关，也不需要任何旧版本残留。
  · **前提不成立时**（本地目录被挪走、克隆目录被删），其余三种组合也不被拒、同样直接换掉登记（各实测过一格）。
  · 换掉登记这一步**不动插件缓存、也不动用户层启用值**；动它们的是随后的 `codex plugin add`：按换过的登记重装
    （旧版本目录被删）并把用户层写回 `enabled = true`。
  ⇒ **`--codex` / 缺省 `--both`（launcher setup 调门也是它，不带 `--marketplace`）在 `marketplace add` 之前不读已有
  登记**（`--backfill` 不同：它在用户没给 `--marketplace` 时先经 `locate_market` 读已有注册面、拿本项目在用的源）。
  已登记本地源的开发机上这样不带 `--marketplace` 跑（克隆目录不在时），登记会被**静默换成默认公开源**。公开那一份
  与本命令版本不同、且读得出时，装前核对（步 3）在 `plugin add` 之前停下——此时登记已换，插件缓存与用户层还没动；
  **这一态下本机其他已装过本插件的项目里 `--check` 照样全绿**（它不核登记，也不核市场里那一份；停下的那个项目本身
  还没装完，在它里面跑 `--check` 会报问题），而此后本机任何项目里跑一次 `--upgrade`（git 源
  那一路的 `marketplace upgrade`）都会把全机共用的插件缓存换成公开那一版——装前核对不在那条路上；**版本相同时装前、
  装后核对都只比版本串、分辨不出，会静默装成公开那一份并报就绪**（某版发布之后、下一次 bump 之前，开发版与公开版的
  版本串相同）。2026-09-22 真机上出现过的「装成另一个版本」与这一格的现象相同（那台机器上有没有旧版本残留一起起
  作用，已无从查证）。被换掉之后回到本地源只有一条路：你自己先 `codex plugin marketplace remove <名>`（删掉用户配置里的
  那条登记，git 源连同它的克隆目录），再带 `--marketplace <本地仓路径>` 重跑——被拒时的出路见
  `MARKETPLACE_NAME_TAKEN`，版本对不上时的出路见 `pending_stop_note`（装前）与 `_door_outlets`（装后）。
**不探测、不代改源、不代 remove**。要在开发机上用本地仓，加 `--marketplace <本地仓路径>`。

**能力边界**：只在 Windows + codex-cli 0.153.x 实测；TUI 启动审查页与 SessionStart hook 的先后
（N10）需人起交互式 codex 才看得到；`spawn_agent` 子线程的 `agent_type` 是否等于角色名（A8）
零凭据造不出。非 git 仓的项目只在项目根起的会话加载插件（子目录会话里 Codex 读不到项目层）；
项目内嵌的独立 git 仓不加载（Codex 的项目层在最近的 `.git` 截止）。**`--codex` / 缺省 `--both` 在注册市场之前
不读已有登记**（`--backfill` 没给 `--marketplace` 时例外，见 §已知形态）：已登记本地源的开发机上不带 `--marketplace`
跑，登记会被静默换成默认公开源（克隆目录不在时；版本不同且读得出时装前核对在装之前停下，但登记已换——此后
本机其他已装过本插件的项目里 `--check` 照样全绿、看不出，任何项目里的一次 `--upgrade` 都会把插件缓存换成
公开那一版）；与本命令同版本时会静默装成公开那一份并报就绪；已登记的那一份失效（本地目录被挪走、git 克隆目录
被删）时，其余先后组合也会被换（见 §已知形态）。
"""

import argparse
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
import _codex_appserver as cas  # noqa: E402
import _codex_hooks as ch  # noqa: E402
import _harness  # noqa: E402
import _settings_io  # noqa: E402
import codex_roles  # noqa: E402
from _state_io import state_dir_of  # noqa: E402

PLUGIN_ROOT = _SCRIPTS_DIR.parent
MIN_CODEX = (0, 153, 4)
DEFAULT_MARKETPLACE = "ryanzhao1011/workframe"
DEFAULT_MARKETPLACE_NAME = "workframe"
PLUGIN_NAME = codex_roles.PLUGIN_NAME

EXIT_OK = 0
EXIT_ENV = 2          # codex 不在 / 版本过低 / 市场或插件命令失败 / app-server 起不来 /
                      # 市场里将要装的那一份与本命令不是同一版本（装前核对，`plugin add` 之前）/
                      # 安装根不是本命令这一份（版本不同或任一侧读不出）/ 安装根的 hook 冻结清单缺失或形态坏 /
                      # 反推不出 Codex 实际加载的是哪一份 / 本命令自己的插件根读不出版本（开跑即停）
EXIT_COUNT = 3        # 过滤后条数 ≠ manifest（含 0 条的各成因）、command 为空、errors 非空、账本 / 角色对账不过；
                      # 写任何东西之前的预检不过：段外同名声明 / 段后键被段内表吸收 / 项目配置解析不了 /
                      # managed 段被手改 / 本项目 Codex 门已被用户关闭 / 回显市场名与参数不同且按它预检不过；
                      # `--upgrade` 遇用户层未启用且项目未 trust（**只在 git 源那一路**：本地源那一路
                      # 走 `plugin add`，用户层已被写回 true，这一格走不到）。
                      # 「写之前停下」不全归 3：安装根不对归 2——3 管项目配置与对账，2 管 Codex 侧装了什么
EXIT_WRITE = 4        # batchWrite 非 ok（服务端拒绝 ⇒ 未写；超时 / 退出 ⇒ 未确认是否写入，文案分开说）
EXIT_READBACK = 5     # 回读：仍有非 trusted / 条数不对 / 项目 trust 未生效 / 角色注册不过 / 用户层 enabled 不是
                      # false / 从项目看的合成值不是 true；`--check`：任一项不过（含用户层 enabled = true）
EXIT_LIVE = 6         # 验活：受信 hook 未产生侧效应

# `--check` 遇用户层 `[plugins."core@<mkt>"] enabled = true` 是算问题（exit 5）还是只提示。
# 用户 2026-09-14 拍板：算问题。改口只动这一行。
USER_LAYER_TRUE_IS_PROBLEM = True

USER_LAYER_STILL_TRUE = ("用户层此刻为 true，任何目录的 Codex 会话都会加载本插件（非 workframe 目录由 hook 早退兜住）；"
                         "修复后重跑 workframe-door --codex")

# `codex plugin marketplace add` 因同名已有登记而被拒（隔离 `CODEX_HOME` 实测 codex-cli 0.153.4：rc=1、stdout 空、
# stderr 为 `marketplace '<名>' is already added from a different source; remove it before adding this source`，
# 登记不变）时的文案。**被拒是有前提的**：已登记的那一份此刻仍然有效（本地目录还在、git 克隆目录还在），且不是
# 「已登记本地源、这次加 git 源、克隆目录不在」——那一格 Codex 不拒、直接换掉登记（克隆目录在时它同样被拒，见模块
# 抬头 §已知形态）。所以走到这里只说明
# **这一次**没改动它，不说明它还是你原来登记的那一份：之前某次不带 `--marketplace` 的运行可能已经把本地登记换成
# 了公开源，这时 `marketplace list` 给的 root 是 Codex 自己的克隆目录，拿它当本地源再 add 同样被拒。
# 本常量只管**文案**：不探测、不代改源、不代 remove（remove 是删除动作）。
MARKETPLACE_NAME_TAKEN = ("本机已登记同名市场，但它的源与本次要加的不是同一个，Codex 拒绝再加一份"
                          "（**这一次没有改动已登记的那份**——但它未必还是你原来登记的：你原本登记的是本地仓、它此刻"
                          "却是 git 源时，多半是之前某次不带 `--marketplace` 的运行把它换掉了）。先看它现在是什么："
                          "跑 `codex plugin marketplace list --json`（文本输出只有名字与 root 两列，看不出源形态）——"
                          "那一行带 `marketplaceSource`（`sourceType` 为 `git`）、或 root 落在 "
                          "`<CODEX_HOME>/.tmp/marketplaces/` 下，登记的就是 git 源，root 是 Codex 自己的克隆目录；"
                          "不带 `marketplaceSource` 的是本地登记，root 就是那个本地目录。"
                          "① 登记的正是你要用的那一份 → 重跑本命令并带上它：本地登记给 `--marketplace <该 root>`，"
                          "git 登记给 `--marketplace <marketplaceSource 里的 source>`（**别拿克隆目录当 `--marketplace`**，"
                          "Codex 照样拒绝）；② 登记的不是你要用的那一份（包括本地登记已被换成 git 源、你要换回本地仓）"
                          "→ 需要你自己先跑 `codex plugin marketplace remove <名>` 去掉已登记的那份"
                          "（**这会从你的 Codex 用户配置里删掉那条市场注册**，git 源连同它的克隆目录），本命令不代做；"
                          "再带 `--marketplace <你要的源>` 重跑")


def _load_seed_module():
    """复用 `codex-trust-seed.py` 里的 `codex_home()` / `seed_record()` / `own_entries()`——
    两侧必须是同一份定位规则与同一份记录形态（文件名带连字符，走 spec 加载）。"""
    spec = importlib.util.spec_from_file_location("_wf_seed", _SCRIPTS_DIR / "codex-trust-seed.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


seed = _load_seed_module()


class DoorError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


class InstalledRootError(DoorError):
    """「Codex 侧装的不是本命令这一份」那一族（版本不同 / 读不出 / 清单坏 / hook 来源对不上），恒为 `EXIT_ENV`。
    `root` 是被核的安装根（反推不出时为 None）。单列一类，是因为 `do_codex` 的收尾要认出它、补说「此后本项目
    会加载哪一版」——那句只对这一族成立。"""

    def __init__(self, msg, root=None):
        super().__init__(EXIT_ENV, msg)
        self.root = root


class WriteUnconfirmedError(DoorError):
    """batchWrite 没等到答复（超时 / 进程退出 / 请求没送出去），恒为 `EXIT_WRITE`：写入可能已经生效，也可能没有。
    单列一类，是因为 `do_codex` 的收尾接到它时不能再说「用户层此刻为 true」——那一键也在这次写入里，同样未确认。"""

    def __init__(self, msg):
        super().__init__(EXIT_WRITE, msg)


def say(msg=""):
    print(msg)
    sys.stdout.flush()


def _wrote_note(wrote, none="未写任何项目文件"):
    """失败文案里「写了 / 没写」那半句的**唯一出处**。`wrote` 是本次运行实际写过的项目文件（相对项目根），
    由写入方写完当场追加（`step_roles` / `do_backfill`）。凡是**在一次写入之后还可能走到**的 raise 点，都从
    这里取、不手写：手写的那句不会跟着后来插进去的一步写入一起变——`--upgrade` 那条先写后核的路径上，「未写
    任何项目文件」就这样说过假话。写入必然还没发生的地方（`--backfill` 开头的状态判定、CC 那半写之前）手写的
    「未写任何文件」是真话，没有改道。"""
    if not wrote:
        return none
    return f"本次已写入的项目文件：{'、'.join(wrote)}（门不代删，修好后直接重跑即可）"


# ---- codex CLI ----

def codex_exe():
    exe = cas.find_codex_exe()
    if not exe:
        raise DoorError(EXIT_ENV, "codex 未找到（PATH 上没有 codex，也未设 WF_CODEX_EXE）")
    return exe


def _git_longpaths_env(env=None):
    """Windows：给 codex 起的 git 子进程打开 `core.longpaths`（进程级，不动用户 git 配置）。

    `codex plugin marketplace add <git 源>` 的暂存路径是
    `<CODEX_HOME>/.tmp/marketplaces/.staging/marketplace-add-XXXXXX/.git/objects/tmp_pack_<40hex>.idx.temp`
    ——`CODEX_HOME` 稍深一点就撞 MAX_PATH，git 报 `Filename too long`、codex 只转述「git clone
    failed with status exit code: 128」（实测）。`GIT_CONFIG_PARAMETERS` 只作用于本进程树。
    """
    env = dict(os.environ if env is None else env)
    if os.name == "nt":
        cur = env.get("GIT_CONFIG_PARAMETERS", "")
        if "core.longpaths" not in cur:
            env["GIT_CONFIG_PARAMETERS"] = (cur + " " if cur else "") + "'core.longpaths=true'"
    return env


def run_codex(exe, args, cwd=None, timeout=180, env=None):
    p = subprocess.run([exe] + list(args), cwd=str(cwd) if cwd else None, env=_git_longpaths_env(env),
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def parse_version(text):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def step_version(exe):
    rc, out, err = run_codex(exe, ["--version"], timeout=30)
    v = parse_version(out) or parse_version(err)
    if rc != 0 or v is None:
        raise DoorError(EXIT_ENV, f"`codex --version` 失败（rc={rc}）: {(out or err)[:120]}")
    if v < MIN_CODEX:
        raise DoorError(EXIT_ENV, f"codex {'.'.join(map(str, v))} 无 hooks/list，需 ≥ "
                                  f"{'.'.join(map(str, MIN_CODEX))}")
    say(f"[1/11] codex {'.'.join(map(str, v))}（{exe}）")
    return v


def _json_out(out):
    try:
        return json.loads(out)
    except ValueError:
        return None


def step_marketplace_add(exe, source, cwd):
    rc, out, err = run_codex(exe, ["plugin", "marketplace", "add", source, "--json"], cwd=cwd)
    data = _json_out(out)
    if rc != 0 or not isinstance(data, dict):
        stream = err or out
        if "already added from a different source" in stream:
            raise DoorError(EXIT_ENV, f"`codex plugin marketplace add {source}` 被拒。{MARKETPLACE_NAME_TAKEN}")
        raise DoorError(EXIT_ENV, f"`codex plugin marketplace add {source}` 失败（rc={rc}）: "
                                  f"{stream[:200]}")
    name = data.get("marketplaceName")
    already = bool(data.get("alreadyAdded"))
    say(f"[2/11] 市场 {name!r} {'已存在' if already else '已注册'}（源 {source}）")
    return name, already


# 装前核对问 Codex 的那条命令。超时不沿用 `run_codex` 的 180 s：读不出就放行，等久了只是拖慢装机
PENDING_LIST_TIMEOUT = 60


def market_core_row(exe, mkt_name, cwd):
    """`codex plugin list --json --available -m <mkt_name>` 里本市场 core 那一行 → `(行, None)`；拿不到 → `(None, 原因)`。

    **两个数组都扫**：还没装时那一行只在 `available[]`，装上之后只在 `installed[]`（实测 codex-cli 0.153.4）——只扫
    `available[]` 会在「已装、市场被换过」那一态（装前核对最该起作用的那一态）找不到它、静默放行。**按市场名与插件名
    双重过滤**：真实输出里同一市场的 `workframe-launcher` 排在 `core` 前面，别的市场也可能有同名插件。
    **不抛**：命令起不来 / 超时 / 失败 / 不是 JSON / 找不到那一行，都只返回原因，由调用方放行。"""
    try:
        rc, out, _err = run_codex(exe, ["plugin", "list", "--json", "--available", "-m", mkt_name], cwd=cwd,
                                  timeout=PENDING_LIST_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"命令没跑成（{type(e).__name__}）"
    data = _json_out(out) if rc == 0 else None
    if not isinstance(data, dict):
        return None, f"命令失败或输出不是 JSON（rc={rc}）"
    for key in ("installed", "available"):
        rows = data.get(key)
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("marketplaceName") == mkt_name and row.get("name") == PLUGIN_NAME:
                return row, None
    return None, f"输出里没有市场 {mkt_name!r} 的 {PLUGIN_NAME} 那一行"


def pending_stop_note(mkt_name, registered, pending_v):
    """装前核对停下时、接在判读句（`version_mismatch(pending=True)`）之后的那段：出路，以及事实（`plugin add` 没跑、
    缓存与用户层没动；步 2 对市场登记做了什么）。`registered` 见 `require_pending_version`；`pending_v` 是市场里将要装的
    那一份的版本。

    市场那份**更新**时出路已由 `version_mismatch` 给出（用市场里那份自带的门重跑，那时步 2 会回 `alreadyAdded`），这里
    只说事实。**不更新时不借 `_door_outlets`，先给眼下走得通的那条**：它的 ② `--upgrade` 按现有登记刷新 Codex 侧——
    登记的源本身就是这一旧版时（步 2 刚按它取回的恰是这一版），git 源那一路会把本机已装的插件缓存（全机共用）换成
    这一旧版，而本机其他已装过本插件的项目里 `--check` 看不出这一态（真机实测）；它的 ① 直接带别的 `--marketplace`
    重跑，在这条登记有效时会被 Codex 以「同名市场、不同源」拒绝（本地登记遇 git 源、克隆目录不在那一格除外，见模块
    抬头 §已知形态）。所以只给：自己 remove、再带 `--marketplace <本命令所属的那份源>`；登记在步 2 之前就有时，另给「把那个本地目录更新到本命令
    这一版」（`--upgrade` 本地源那一路也属此类）。"""
    s = own()
    facts = "装前核对在 `plugin add` 之前停下：插件缓存与你 Codex 用户配置里的 enabled 都没动"
    already = True              # 没有步 2（`--upgrade` 本地源那一路）：登记早就在，且是本地目录
    if registered is not None:
        already, source = registered
        if already:
            facts += f"；市场 {mkt_name!r} 在本次之前就已按同一个源登记（`alreadyAdded`），第 2 步没有改动它"
        else:
            facts += (f"；**第 2 步把市场 {mkt_name!r} 登记成了 {source}**（Codex 回报 `alreadyAdded` 为 false）——这条登记"
                      f"已经在你的 Codex 用户配置里（[marketplaces.{mkt_name}]）；若你之前以本地目录登记过同名市场，那条登记"
                      f"已被它换掉（Codex 这一步没有拒绝，说明它要么是新加的，要么已把同名的旧登记换掉）")
    if _newer(pending_v, s["version"]):
        return facts
    hint = f"（本命令就来自市场目录 {s['market']}）" if s["market"] else "（例如本地框架仓）"
    way = (f"出路：先自己跑 `codex plugin marketplace remove {mkt_name}`（**这会从你的 Codex 用户配置里删掉这条市场登记**，"
           f"git 源连同它的克隆目录；本命令不代做），再跑 `workframe-door --codex --marketplace <本命令所属的那份源>`{hint}")
    if already:
        way += f"；Codex 登记的是你的本地目录、只是它落后于本命令时，也可以把那个目录更新到 v{s['version']} 后原样重跑本命令"
    if registered is None:
        return f"{way}。{facts}"
    got = "第 2 步刚按它取回的就是" if not already else "这个源此刻若仍是"
    return (f"{way}。{facts}。**这时别拿 `workframe-door --upgrade` 来补**：它按这条登记刷新 Codex 侧，{got} v{pending_v}，"
            f"登记是 git 源时它会把本机已装的插件缓存（全机共用，每个跑过 `--codex` 的项目都加载它）换成这一版；"
            f"本机其他已装过本插件的项目里，`--check` 此刻照样报通过、看不出登记已换")


def require_pending_version(exe, mkt_name, cwd, registered=None):
    """装前核对（步 3 之前）：Codex 登记的市场里**将要装**的那一份与本命令是不是同一版本，不是就在 `plugin add` 之前
    停下（`EXIT_ENV`）——插件缓存与用户层 enabled 都还没动。

    **判据与步 3' 同一份**（`version_mismatch`），不另写比对。读的是那一行的 `source.path`（市场里的插件目录）的
    版本，**不是行上的 `version`**：已装时行上的 `version` 是已装缓存的版本，市场里那一份可能早已换了（实测）。
    **读不出就放行**（命令没跑成 / 失败 / 不是 JSON / 没有那一行 / `source.path` 缺或读不出版本），打一行 `i`：
    「分辨不出」不该变成「装不上」，后面还有步 3' 兜底。它只读本次命令刚注册或确认过的那个市场，结论只用来决定
    停不停，不改道。
    `registered`：本次步 2 的 `(alreadyAdded, 源)`；`--upgrade` 那一路没有步 2，给 None。停下时据此如实说出步 2 对
    你 Codex 用户配置里的市场登记做了什么（`pending_stop_note`）。"""
    row, why = market_core_row(exe, mkt_name, cwd)
    if row is not None:
        src = row.get("source")
        path = src.get("path") if isinstance(src, dict) else None
        if not isinstance(path, str) or not path:
            row, why = None, f"{PLUGIN_NAME} 那一行没有 source.path"
    if row is None:
        say(f"   i 装前核对没核成（`codex plugin list --json --available -m {mkt_name}`：{why}）——照常装，"
            f"装后核对（步 3'）照常核")
        return
    root = Path(_unc_strip(path))
    v, vwhy = plugin_version(root)
    if v is None:
        say(f"   i 装前核对没核成（市场里将要装的那一份 {root} 读不出版本：{vwhy}）——照常装，装后核对（步 3'）照常核")
        return
    prob = version_mismatch(root, pending=True)
    if prob is None:
        say(f"       装前核对：市场 {mkt_name!r} 里将要装的 {PLUGIN_NAME} 是 v{v}（{root}），与本命令同版本")
        return
    raise DoorError(EXIT_ENV, f"{prob}。{pending_stop_note(mkt_name, registered, v)}")


def step_plugin_add(exe, mkt_name, cwd, announce=None, registered=None):
    """`codex plugin add core@<mkt_name>`。**装前核对收在这个入口、在调 Codex 之前**（`require_pending_version`）：
    三个调用点（`--codex` 的步 3、`--upgrade` 本地源那一路、源形态判不出而被拒后落回的那一路）都经这里，谁也漏不掉。
    `announce` 是调用方要在真的 `plugin add` 之前说的那句（「这一步会把 enabled 写回 true」）：核对过了才说——核对
    不过时它并没有被写。`registered` 原样交给核对。
    终端上步 3 只占一个步号：核对那一行（`i` 或「同版本」）与 `announce` 都是子行，`[3/11]` 只打在 `plugin add` 那一行；
    `--upgrade` 的选路那一行（`[2'/11]`）由调用方在进来之前打，所以步号不倒序。"""
    plugin_id = f"{PLUGIN_NAME}@{mkt_name}"
    require_pending_version(exe, mkt_name, cwd, registered)
    if announce:
        say(announce)
    rc, out, err = run_codex(exe, ["plugin", "add", plugin_id, "--json"], cwd=cwd)
    data = _json_out(out)
    if rc != 0 or not isinstance(data, dict):
        raise DoorError(EXIT_ENV, f"`codex plugin add {plugin_id}` 失败（rc={rc}）: {(err or out)[:200]}")
    installed = data.get("installedPath")
    say(f"[3/11] 插件 {plugin_id} 版本 {data.get('version')} → {installed}")
    return Path(installed) if installed else None


def marketplace_source_type(exe, mkt_name, cwd):
    """本市场在 Codex 侧的源形态，取自 `codex plugin list --json` 的
    `installed[].marketplaceSource.sourceType`（实测取值：本地目录源为 `"local"`）。

    **必须走 `plugin list`，不能走 `marketplace list`**：后者只在 git 登记的那一行带 `marketplaceSource`，
    本地登记的那一行只有 `{name, root}`（实测 codex-cli 0.153.4）——要认出的恰是本地源，拿它判会把本地源判成
    「判不出」，而「判不出」会被当成「不是本地源」。

    拿不到就返回 `None`——命令失败、JSON 解析不了、本市场此刻一个已装插件都没有，三种都是
    「分辨不出」，由调用方按「先试 git 那条、撞见拒绝再改走 `plugin add`」处置。**不抛**：
    源形态只是选路依据，判不出不该让升级整个失败。
    """
    rc, out, _err = run_codex(exe, ["plugin", "list", "--json"], cwd=cwd, timeout=120)
    data = _json_out(out) if rc == 0 else None
    if not isinstance(data, dict):
        return None
    for row in data.get("installed") or []:
        if row.get("marketplaceName") != mkt_name:
            continue
        st = ((row.get("marketplaceSource") or {}).get("sourceType"))
        if st:
            return str(st)
    return None


# `codex plugin marketplace upgrade` 对非 Git 市场的拒绝字面（实测 codex-cli 0.153.4：rc=1、
# **stdout 空**、stderr 为 `marketplace '<名>' is not configured as a Git marketplace`）。
# 只用来**认出这一态**，不用来判定成功——认不出时照常按通用失败报错。
UPGRADE_NOT_GIT = "not configured as a Git marketplace"


def step_upgrade(exe, mkt_name, plugin_id, cwd):
    """刷新已装插件缓存。返回 `(installedPath 或 None, 用户层是否被写回 true)`。

    两条路的分岔与代价见模块抬头 §`--upgrade`。这里只补一句实现上的取舍：**本地源那条路不先试
    `marketplace upgrade`**——源形态已经分辨出来时先跑一条必然失败的命令，只会在终端上留下一段
    看起来像故障的输出。分辨不出时才「先试再落」。
    """
    kind = marketplace_source_type(exe, mkt_name, cwd)
    if kind is not None and kind != "git":
        # 选路那一行先说（步号 2' 在 3 之前）；那句「会把 enabled 写回 true」交给 step_plugin_add 在装前核对过了之后
        # 再说：核对不过时并没有写
        say(f"[2'/11] 市场 {mkt_name!r} 的源形态是 {kind!r}，`marketplace upgrade` 只认 Git 市场"
            f"——改走 `plugin add {plugin_id}` 重装（实测为整体重物化：缓存被改动过的部分会被换回）")
        return step_plugin_add(exe, mkt_name, cwd, announce=(
            f"       **这一步会把你的 Codex 用户配置里 [plugins.\"{plugin_id}\"] enabled 写回 true**，"
            f"步 7 再置回 false")), True

    rc, out, err = run_codex(exe, ["plugin", "marketplace", "upgrade", mkt_name, "--json"], cwd=cwd)
    data = _json_out(out)
    if rc != 0:
        if UPGRADE_NOT_GIT in (err or "") or UPGRADE_NOT_GIT in (out or ""):
            # 源形态没分辨出来（`kind is None`）而它其实是本地源——落到重装那一路，别把「升不了」
            # 报成环境故障。分辨得出时走不到这里。
            say(f"[2'/11] `marketplace upgrade {mkt_name}` 被拒（该市场不是 Git 源），改走 `plugin add {plugin_id}` 重装")
            return step_plugin_add(exe, mkt_name, cwd, announce=(
                "       **这一步会把用户层 enabled 写回 true**，步 7 再置回 false")), True
        raise DoorError(EXIT_ENV, f"`codex plugin marketplace upgrade {mkt_name}` 失败（rc={rc}）: "
                                  f"{(err or out)[:200]}")
    errors = (data or {}).get("errors") if isinstance(data, dict) else None
    if errors:
        raise DoorError(EXIT_ENV, f"marketplace upgrade 报错: {errors}")
    say(f"[2'/11] 市场 {mkt_name!r} 已升级：{json.dumps(data, ensure_ascii=False)[:200]}")
    return None, False


# ---- 预检（写任何东西之前）----

def step_preflight(project, marketplace, label="[0/11]", wrote=None):
    """把 managed 段拼进项目 `.codex/config.toml` 当前文本做整份语义判定，外加段内手改 / 用户已关两态。
    渲染用门自己的插件根：段里只有启用表与角色注册，判的是段外内容与段的冲突，与装到哪个版本无关。
    `wrote` 给了就在失败文案末尾说写没写（`_wrote_note`）；不给（`do_codex` 的 try 内那一次）由调用方的
    统一收尾去说，这里不说——两处都说就是同一件事的两份手写。"""
    plugin_id = f"{PLUGIN_NAME}@{marketplace}"
    try:
        codex_roles.preflight(project, PLUGIN_ROOT, installed_version(PLUGIN_ROOT), marketplace)
    except codex_roles.RoleError as e:
        raise DoorError(EXIT_COUNT, f"项目配置预检不过（插件 id {plugin_id}）：{e}"
                        + ("" if wrote is None else "。" + _wrote_note(wrote, none="未写任何文件")))
    say(f"{label} 预检：{codex_roles.CONFIG_REL.as_posix()} 拼上 managed 段（段首 [plugins.\"{plugin_id}\"] "
        f"enabled = true）后整份语义无冲突")


# ---- 角色 ----

def plugin_version(root):
    """插件根 `root` 的版本 → `(版本串, None)`；读不出 → `(None, 原因)`。

    先读 `.codex-plugin/plugin.json`，**这份不存在**才退到 `.claude-plugin/plugin.json`：1.1.0 之前的插件没有
    前者，而 Codex 装机缓存是整个插件目录原样复制——只读前者的话，恰恰在旧版缓存那一格（最需要说出「装的是
    哪一版」的那一格）读成「不知道」。**排在前面的那份在、却读不出（解析不了 / 没有 version 串）就算读不出，
    不往后退**：坏掉的清单不该由另一份替它作证。

    与 doctor 的 `_plugin_version_at` 同一契约、先后相反（它先读 `.claude-plugin`）；两份清单同版本由 validate
    的 `check_version_consistency` 管，顺序只在副本本身损坏时才有区别。不 import doctor 来复用：两个脚本都在
    模块级重包 `sys.stdout`，同一进程里加载第二个会关掉底层 buffer。
    """
    for rel in (".codex-plugin", ".claude-plugin"):
        p = Path(root) / rel / "plugin.json"
        if not p.exists():          # 「在不在」按 exists 判：是个目录也算「在、却读不出」，不往后退
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception as e:
            return None, f"{rel}/plugin.json 解析不了（{type(e).__name__}）"
        v = data.get("version") if isinstance(data, dict) else None
        if isinstance(v, str) and v:
            return v, None
        return None, f"{rel}/plugin.json 里没有 version 串"
    return None, ".codex-plugin/plugin.json 与 .claude-plugin/plugin.json 都不在"


def installed_version(installed_root):
    """显示用：读不出时是 `"?"`。**判定一律用 `plugin_version`**——两边都是 `"?"` 时字符串相等，拿这个值判
    一致就是把「不知道」当成「一致」。"""
    return plugin_version(installed_root)[0] or "?"


# 本命令这一份在开跑时的样子。`--upgrade` 先刷新 Codex 侧、后核对，而门本身可能就住在被刷新的那一处：Codex 安装
# 缓存的旧版本目录会被删掉，git 源的市场快照会被原地刷新。核对时再现读 `PLUGIN_ROOT`，读到的就已经不是本进程在跑的
# 那一份——前者读不出、后者读成新版而判「一致」。所以开跑时读一次存下（`snapshot_own`），之后一律用存下的（`own`）。
_OWN = None


def snapshot_own():
    """开跑时（`do_codex` / `do_check` / `do_backfill` 的第一步）读一次本命令这一份——版本、读不出的原因、市场目录
    提示——存下来，返回存下的那份。之后「本命令是哪个版本」一律问 `own()`，不再现读 `PLUGIN_ROOT`。"""
    global _OWN
    v, why = plugin_version(PLUGIN_ROOT)
    _OWN = {"root": str(PLUGIN_ROOT), "version": v, "why": why, "market": _door_market_root_now()}
    return _OWN


def own():
    """本命令这一份：开跑时存下的那份；没存过、或存的不是当前 `PLUGIN_ROOT`（例如测试里换了根）时现读一次、不存。"""
    if _OWN is not None and _OWN["root"] == str(PLUGIN_ROOT):
        return _OWN
    v, why = plugin_version(PLUGIN_ROOT)
    return {"root": str(PLUGIN_ROOT), "version": v, "why": why, "market": _door_market_root_now()}


def door_drift_note():
    """开跑之后本命令所在的目录变没变：没变返回 ""；被删、或被刷新成别的版本 → 一句说明，附在「本命令来自 X」后面，
    免得读者把 X 此刻的样子当成本进程在跑的那一份。"""
    s = own()
    if not Path(s["root"]).exists():
        return ("——这个目录此刻已经不在了（多半是本次 `plugin add` / `marketplace upgrade` 重新物化插件缓存时删掉了"
                "旧版本目录），括号里的版本是本进程开跑时读到的；"
                "它已不是任何一份可以再跑的门")
    live, _why = plugin_version(s["root"])
    if live != s["version"]:
        return (f"——这个目录此刻已被刷新成 {('v' + live) if live else '读不出版本'}，已不是本进程在跑的那一份；"
                f"括号里的版本是开跑时读到的")
    return ""


def require_own_version(wrote=None):
    """本命令自己所在插件根的版本读不出 ⇒ **在动任何东西之前**停下（`EXIT_ENV`）。装后核对要拿它当比对的一侧，
    这一侧读不出时后面必停，而那时市场注册、插件安装、用户层 enabled 都已经被动过了——出路里的
    `--marketplace` / `--upgrade` 也修不了门自己。这一侧在开跑时就读得到，所以在开跑时就核，**并把读到的存下**
    （`snapshot_own`）：之后的核对都拿这份比，不再现读。"""
    s = snapshot_own()
    v, why = s["version"], s["why"]
    if v is None:
        raise DoorError(EXIT_ENV, f"本命令自己所在的插件根 {PLUGIN_ROOT} 读不出版本（{why}）——装后核对没有可比的这一侧，"
                                  f"在动任何东西之前停下（Codex 侧的市场注册、插件安装、用户配置都还没碰）。出路：这份插件"
                                  f"本身不完整或被改坏过，先把它修好（CC 门：`claude plugin update {PLUGIN_NAME}@<市场>`；"
                                  f"在框架仓里跑的，把它的 plugin.json 恢复出来）再重跑。"
                                  + _wrote_note(wrote or [], none="未写任何文件"))


def _cached_doors(marketplace):
    """Codex 安装缓存里现有的各版本门：`<CODEX_HOME>/plugins/cache/<市场>/core/*/scripts/workframe_door.py`，排除本命令
    自己所在的那一份。只列真实存在的文件（结构事实），读不到就是空表。"""
    try:
        base = seed.codex_home() / "plugins" / "cache" / marketplace / PLUGIN_NAME
        mine = _norm_path(PLUGIN_ROOT)
        return [p for p in sorted(base.glob("*/scripts/workframe_door.py"))
                if _norm_path(p.parents[1]) != mine]
    except Exception:
        return []


def loads_other_copy_note(root, upgrade):
    """先写后核那几格（见模块抬头步 3'）失败之后的现状：managed 段（含段首启用表 `enabled = true`）已在本次写进项目、
    项目已 trust ⇒ 此后在本项目起的 Codex 会话会加载 Codex 侧装着的那一份，而它与本命令对不上、hook 信任本次没有重种。
    只由 `do_codex` 的收尾在「安装根类失败 ＋ 本次写过 config.toml ＋ 用户层不是 true」时追加。"""
    if root is None:
        where = "安装根反推不出"
    else:
        v, _why = plugin_version(root)
        where = f"安装根 {root}，{('v' + v) if v else '版本读不出'}"
    cmd = "workframe-door --upgrade" if upgrade else "workframe-door --codex"
    return (f"注意：本次已把 managed 段（含段首启用表 enabled = true）写进本项目，而本项目已 trust——**此后在本项目起的 "
            f"Codex 会话会加载 Codex 侧装着的那一份（{where}），不是本命令这一份**，它的 hook 信任本次也没有重种。"
            f"按上面的出路让两边对上之后重跑 `{cmd}`：那时段已在，门会在写之前核对，核对过了才重生成角色、补种信任")


def step_roles(project, installed_root, marketplace, wrote=None):
    """返回 (角色名列表, 渲染所用的插件根)。

    `installed_root` 给了就**先过 `require_installed_root`，在写任何项目文件之前**。`do_codex` 里安装根的
    四个产生点（`plugin add` 回显 / 本地源重装回显 / 两次从 `hooks/list` 反推）都是在这里第一次被消费，所以
    守卫放在被调方入口而不是四个调用点；另两个产生点（`step_hooks_list` 自己反推的、`check_static` 的）各自
    过同一个判定。「Codex 实际加载的是哪一份」（`require_loaded_and_echoed`）要一份 `hooks/list` 读数，这里没有——
    由 `do_codex` 在调本函数之前、此刻看得见 hook 时核。`wrote` 给了就把本次实际写过的项目文件（相对项目根）追加进去，
    供失败文案如实说写了什么。"""
    if installed_root is not None:
        require_installed_root(installed_root)
    src_root = Path(installed_root) if installed_root and (Path(installed_root) / "agents").is_dir() \
        else PLUGIN_ROOT
    if src_root == PLUGIN_ROOT:
        drift = door_drift_note()
        if drift:
            # 要按「本命令这一份」生成角色，而那个目录已被本次重装 / 刷新删掉或改写：再读就是在按别人的文件生成。
            # 这里只说「在生成角色之前」：`--backfill` 先写过 CC 那半时这里之前已有写入，写没写由收尾的 `_wrote_note` 说
            raise InstalledRootError(
                f"要按本命令自己的插件根生成角色，但本命令来自 {PLUGIN_ROOT}（v{own()['version']}）{drift}——在按它"
                f"生成角色之前停下。出路：用 Codex 侧新装上的那一份自带的门、带上本次同样的参数重跑"
                + ("：" + "、".join(f"`python \"{p}\" <本次同样的参数>`" for p in _cached_doors(marketplace))
                   if _cached_doors(marketplace) else
                   "（它在 `<CODEX_HOME>/plugins/cache/<市场>/core/<版本>/scripts/workframe_door.py`；`codex plugin "
                   "marketplace list` 可看市场指向哪里，只读）"), None)
    ledger = Path(project) / codex_roles.LEDGER_REL
    ledger_before = ledger.read_bytes() if ledger.is_file() else None
    try:
        report, roles = codex_roles.generate(project, src_root, installed_version(src_root), marketplace=marketplace)
    except codex_roles.RoleError as e:
        raise DoorError(EXIT_COUNT, f"角色生成失败: {e}")
    if wrote is not None:
        done = [rel for rel, action, _note in report if action == "written"]
        if (ledger.read_bytes() if ledger.is_file() else None) != ledger_before:
            done.append(codex_roles.LEDGER_REL.as_posix())
        wrote.extend(rel for rel in done if rel not in wrote)
    say(f"[4/11] 角色 {len(roles)} 个（{', '.join(r for r, _d, _b in roles)}）→ "
        f"{codex_roles.ROLES_DIR_REL.as_posix()}/ ＋ {codex_roles.CONFIG_REL.as_posix()} managed 段（段首启用表 "
        f"[plugins.\"{PLUGIN_NAME}@{marketplace}\"] ＋ 角色注册；插件根 {src_root} v{installed_version(src_root)}）；"
        f"账本 {codex_roles.LEDGER_REL.as_posix()}")
    for rel, action, note in report:
        if action != "unchanged":
            say(f"       {action:8} {rel}{('  ' + note) if note else ''}")
    problems = codex_roles.verify(project)
    if problems:
        raise DoorError(EXIT_COUNT, "角色产物对账不过: " + "；".join(problems))
    return [r for r, _d, _b in roles], src_root


def roles_registered_problems(server, project, roles):
    """真实消费方回读：`config/read`（cwd = 项目）里 `config.agents` 是否登记了每个角色，且其
    `config_file` 解析到项目 `.codex/roles/<role>.toml` 且文件存在。**项目层只在项目已 trust 时可见**，
    所以本函数在项目 trust 写入之后调。返回问题列表。

    读不到时的文案要说真因，不能把人送去查 trust：走到这里时项目 trust 已回读为 trusted、
    `codex_roles.verify()` 已判过 `.codex/config.toml` 整份可解析且每个角色都有注册段——所以按
    `config/read` 的 `layers` 里有没有项目层分两种说法。"""
    try:
        res = server.config_read(True, cwd=str(project))
        cfg = res.get("config") or {}
    except Exception as e:
        return [f"config/read(cwd) 失败: {type(e).__name__}: {str(e)[:120]}"]
    agents = cfg.get("agents") or {}
    project_layers = [str((ly.get("name") or {}).get("dotCodexFolder"))
                      for ly in (res.get("layers") or []) if isinstance(ly, dict)
                      and isinstance(ly.get("name"), dict) and ly["name"].get("type") == "project"]
    cfg_rel = codex_roles.CONFIG_REL.as_posix()
    problems = []
    for role in roles:
        entry = agents.get(role) if isinstance(agents, dict) else None
        if not isinstance(entry, dict):
            if project_layers:
                problems.append(f"[agents.{role}] 未被 Codex 读到：config/read(cwd) 有项目层 {project_layers}，"
                                f"但其中没有这个角色——{cfg_rel} 已核过可解析且含注册段，Codex 读到的却不是"
                                f"这份？（同名的另一个 .codex 目录？）")
            else:
                problems.append(f"[agents.{role}] 未被 Codex 读到：config/read(cwd) 的 layers 里没有项目层"
                                f"——项目 trust 已回读为 trusted、{cfg_rel} 已核过，Codex 仍未把 "
                                f"{project} 当作项目（cwd 解析不同？）")
            continue
        cf = entry.get("config_file")
        want = Path(project) / codex_roles.ROLES_DIR_REL / f"{role}.toml"
        if not cf or os.path.normcase(str(Path(cf).resolve())) != os.path.normcase(str(want.resolve())):
            problems.append(f"[agents.{role}] config_file 回显 {cf!r}，不是 {want}")
        elif not Path(cf).is_file():
            problems.append(f"[agents.{role}] config_file {cf} 不存在")
    return problems


# ---- 启用层：用户层 / 从项目看的合成值 ----

def _quoted(key):
    """keyPath 里的键段用**双引号 ＋ 反斜杠转义**：实测 `config/batchWrite` 的 keyPath 只认 TOML 基本字符串——
    单引号会连引号一起当键名，裸反斜杠会被吃掉。"""
    return key.replace("\\", "\\\\").replace('"', '\\"')


def plugin_disable_edit(plugin_id):
    """用户层 `[plugins."<id>"] enabled = false`。**写值不删表**：缺表 ≡ 未启用（实测），但显式 false 在
    `codex plugin list` 里可观测，且不涉及删除。"""
    return {"keyPath": f'plugins."{_quoted(plugin_id)}".enabled', "mergeStrategy": "upsert", "value": False}


def user_layer_enabled(server, plugin_id):
    """**用户层**（`config/read` 的 `layers[name.type == "user"].config.plugins`）里本插件的 enabled：
    True / False / None（无该表）。**不读合成值**——合成值还叠了 system 层。读不到 user 层抛 AppServerError。"""
    res = server.config_read(True)
    for ly in res.get("layers") or []:
        name = ly.get("name") if isinstance(ly, dict) else None
        if isinstance(name, dict) and name.get("type") == "user":
            cfg = ly.get("config") if isinstance(ly.get("config"), dict) else {}
            plugins = cfg.get("plugins") if isinstance(cfg.get("plugins"), dict) else {}
            entry = plugins.get(plugin_id)
            v = entry.get("enabled") if isinstance(entry, dict) else None
            return v if isinstance(v, bool) else None
    raise cas.AppServerError("config/read 的 layers 里没有 user 层——无法判定用户层是否启用本插件")


def project_merged_enabled(server, project, plugin_id):
    """从项目看的合成值（`config/read(cwd=项目)` 的 `config.plugins`）里本插件的 enabled；无表 None。"""
    res = server.config_read(False, cwd=str(project))
    plugins = (res.get("config") or {}).get("plugins")
    entry = plugins.get(plugin_id) if isinstance(plugins, dict) else None
    v = entry.get("enabled") if isinstance(entry, dict) else None
    return v if isinstance(v, bool) else None


def zero_hooks_cause(server, project, plugin_id, user_now="?"):
    """`hooks/list` 里本插件 0 条时，按读得到的三样东西（用户层值 / 项目 trust / 段首启用表）分辨成因；
    分不出返回 None（调用方用「整份未被解析」那句）。"""
    try:
        user = user_layer_enabled(server, plugin_id) if user_now == "?" else user_now
    except Exception:
        return None
    rel = codex_roles.CONFIG_REL.as_posix()
    table = codex_roles.block_enable_value(project, plugin_id)
    if user is True:
        if table is False:
            # 项目层优先：用户层 true、段首表 false 时 Codex 在本项目不加载。带行尾注释的写法门判「被手改」（严格），
            # 这里只说成因，不把人送去查「缺 command 键」
            return (f"用户层是 true，但本项目 {rel} managed 段段首的启用表是 enabled = false（项目层优先，所以本项目不加载）"
                    + ("——那一行带了行尾注释：门只认不带注释的 `enabled = false` 为「已关」，带注释按被手改报；只改值、不加注释"
                       if codex_roles.commented_close_line(project) else ""))
        return None
    trust = project_trust_state(server, project_key(project))
    if trust != "trusted":
        return (f"用户层未启用本插件（enabled = {user!r}）且项目未 trust——项目 {rel} 里的启用表 Codex 读不到"
                f"（clone 到另一台机器、或这台机器上还没对本项目跑过装机时就是这样）。出路：先跑 workframe-door --codex")
    if table is False:
        return f"本项目 Codex 门已被你关闭（{rel} managed 段 enabled = false）"
    if table is None:
        return (f"用户层未启用本插件、项目已 trust，但 {rel} 的 managed 段里没有 [plugins.\"{plugin_id}\"] 启用表"
                f"（旧形态的段，或市场名不是 {plugin_id.split('@', 1)[-1]}）。出路：先跑 workframe-door --codex")
    return None


# ---- hooks/list 对账 ----

def manifest_count(installed_root):
    """安装根的 hook 冻结清单（`CODEX_BASELINE_REL`）→ `(条数, 清单)`。

    形态不对一律 `InstalledRootError`（`EXIT_ENV`），**各给一句成因不同的人话、不抛裸异常**：文件不存在 / 读不出 / 不是合法
    JSON / `entries` 不是列表（含缺这个键、顶层不是对象）/ `entries` 是空列表 / 某条不是带 `key` 串的对象。
    空列表也算坏：装得对的插件不可能 0 条，放行会让预期条数变 0、随后落进「0 条成因」那几句，把人指到错的
    方向。条目形态也在这里验：对账不符时 `step_hooks_list` 要逐条取 `key`，坏条目在那里就是一条 traceback。
    """
    root = Path(installed_root)
    rel = ch.CODEX_BASELINE_REL
    if _norm_path(root) == _norm_path(PLUGIN_ROOT):
        who, fix = "本命令自己所在的插件根", ("本插件自己的安装不完整或被改坏过——先把它修好（重装本插件；在框架仓里"
                                          "跑的就把这个文件恢复出来）再重跑")
    else:
        who, fix = "Codex 侧的安装根", ("Codex 侧这份安装不完整或被改坏过——跑 `workframe-door --upgrade` "
                                     "重新物化它，再重跑")

    def bad(cause):
        return InstalledRootError(f"{who} {root} 的 hook 冻结清单 {rel}：{cause}。{fix}", root)

    p = root / rel
    if not p.exists():
        raise bad("文件不存在")
    try:
        text = p.read_text(encoding="utf-8-sig")     # 与 plugin_version 同一口径：BOM 不算坏
    except (OSError, UnicodeDecodeError) as e:
        raise bad(f"读不出（{type(e).__name__}: {e}）")
    try:
        data = json.loads(text)
    except ValueError as e:
        raise bad(f"不是合法 JSON（{e}）")
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        found = (f"顶层是 {type(data).__name__}、不是对象" if not isinstance(data, dict)
                 else "没有这个键" if "entries" not in data else f"实为 {type(entries).__name__}")
        raise bad(f"`entries` 不是列表（{found}）")
    if not entries:
        raise bad("`entries` 是空列表——装得对的插件不可能 0 条 hook")
    for i, row in enumerate(entries, 1):
        if not (isinstance(row, dict) and isinstance(row.get("key"), str)):
            raise bad(f"`entries` 第 {i} 条不是带 `key` 串的对象")
    return len(entries), data


def _norm_path(p):
    """比较两个路径是不是同一处之前的归一：剥 `\\\\?\\` 前缀（`resolve()` 不剥它）→ `resolve()`（8.3 短名、`..`、大小写之外
    的差异）→ `normcase`。只用于比较，不用来打开文件。"""
    return os.path.normcase(str(Path(_unc_strip(p)).resolve()))


def _door_market_root_now():
    """本命令所在插件根往上两级若是一个市场目录（有 `.claude-plugin/marketplace.json`）就返回它，否则 None。
    跑的是本地框架仓里的门时它就是那个仓——出路里给得出一条能照抄的 `--marketplace`。只读结构事实，不猜。
    **只在开跑时由 `snapshot_own` 调一次**（出路里用的是存下的那份）：刷新之后再读，读到的是刷新后的目录。
    落在 `CODEX_HOME` 里面的不算——那是 Codex 自己的市场快照（git 源的克隆落点），不是一个该拿去 `--marketplace`
    登记的源，照抄只会撞上「同名市场、不同源」。"""
    cand = PLUGIN_ROOT.parent.parent
    if not (cand / ".claude-plugin" / "marketplace.json").is_file():
        return None
    try:
        home = _norm_path(seed.codex_home())
        if (_norm_path(cand) + os.sep).startswith(home.rstrip("\\/") + os.sep):
            return None
    except Exception:
        pass
    return cand


def _newer(a, b):
    """版本串 `a` 是否比 `b` 新；任一侧解析不出返回 False。出路按方向给时的唯一判法（`version_mismatch` 与装前核对共用）。"""
    ia, ib = parse_version(a or ""), parse_version(b or "")
    return bool(ia and ib and ia > ib)


def version_mismatch(root, pending=False):
    """安装根 `root` 与本命令所在插件根不是同一版本时返回一句人话（两个版本、两个根、出路）；同版本返回 None。
    `pending=True`：`root` 是 Codex 登记的市场里**将要装**的那一份（装前核对），判据不变，只把措辞从「装上的」换成
    「将要装的」；市场那份更新时，出路点名的是市场目录里自带的门（本地源就是本地仓里的门）；不更新时**只返回判读句**，
    出路交给 `pending_stop_note`——`_door_outlets` 那两条在装前那一态走不通：`--marketplace` 在登记有效时通常被拒，
    `--upgrade` 可能把全机缓存换成旧版（理由见那里）。

    **任何一侧读不出版本都返回人话、不返回 None**：「不知道」不是「一致」。
    出路按方向给：Codex 侧更新 → 升本命令这一侧；否则（装后那一态）两类读者对号入座——本机另有本命令所属那份源的开发者
    （带 `--marketplace`），与用的就是公开源、只是 Codex 侧那份没跟上的用户（`--upgrade`）。不先建议 remove
    （那是删用户配置里的市场注册）；只在这两条都走不通的那一态——Codex 登记的已是 git 源、而你要本地源（本地登记
    被换掉之后就是这样）——由 `_door_outlets` 点明只能你自己先 remove，`MARKETPLACE_NAME_TAKEN` 同样说清后果。
    **本命令这一侧用开跑时存下的版本**（`own()`），不现读 `PLUGIN_ROOT`：`--upgrade` 刷新 Codex 侧时，门所在的目录
    可能已被删或已被原地刷新（见 `snapshot_own`）。那种时候「本命令来自 X」后面附一句 X 此刻的实况。
    """
    s = own()
    mine, mine_why = s["version"], s["why"]
    inst, inst_why = plugin_version(root)
    if mine is not None and mine == inst:
        return None

    def ver(v, why):
        return f"v{v}" if v is not None else f"版本读不出：{why}"

    side = "Codex 登记的市场里将要装的插件" if pending else "Codex 侧装上的插件"
    pair = (f"{side}在 {root}（{ver(inst, inst_why)}），本命令来自 {s['root']}（{ver(mine, mine_why)}）"
            f"{door_drift_note()}")
    if inst is None or mine is None:
        head = f"无法确认 Codex 侧{'将要装' if pending else '装'}的就是本命令这一份——{pair}；读不出版本不按一致放行"
    else:
        head = f"{side}与本命令不是同一版本：{pair}"
    if _newer(inst, mine):
        new_door = Path(root) / "scripts" / "workframe_door.py"
        where = "Codex 登记的市场里那份" if pending else "Codex 侧那份"
        way = (f"出路：{where}更新（v{inst}），落后的是本命令——"
               + (f"用 {where}自带的门、带上本次同样的参数重跑：`python \"{new_door}\" <本次同样的参数>`"
                  f"（只装了 Codex 的机器走这条）；" if new_door.is_file() else "")
               + f"CC 门也在用的，先把 CC 那一侧升到 v{inst}（`claude plugin update {PLUGIN_NAME}@<市场>`），"
                 f"再用升级后的 workframe-door 重跑")
    elif pending:
        return head             # 出路由装前核对按步 2 做了什么给（`pending_stop_note`）：`_door_outlets` 的 ② 在这一态会降级缓存
    else:
        way = _door_outlets()
    return f"{head}。{way}"


def _door_outlets():
    """「Codex 侧装的不是本命令这一份、且不是它更新」时的两条出路（对号入座）。版本核对与 hook 来源核对共用。
    只用在装后那一态；装前核对停下时不用它，见 `pending_stop_note`。**装后那一态 ② 也不保证安全**：② 按 Codex 此刻的
    登记刷新全机共用的插件缓存，只有登记的源与已装缓存同源时才不会把它换旧；登记已被换成公开源、缓存仍是之前从
    本地源装的那一版时（装前核对停下之后正是这一态，见模块抬头 §已知形态），只要门与缓存版本不同，`--check`、步 3'
    或 hook 来源核对照样给出这条 ②，照着跑一次 `--upgrade` 就会把全机缓存换成公开那一版（真机实测，公开那一版可以
    比已装的更旧）。这一态下能把人引开的只有文案后半段「分不清是哪种」那句：git 登记而你要本地源时，② 刷新的也还是
    那个 git 源、只能 remove。"""
    hint = own()["market"]
    return ("出路（对号入座）：① 本机有本命令所属的那份市场源"
            + (f"（本命令就来自市场目录 {hint}）" if hint else "（例如本地框架仓）")
            + " → 重跑 `workframe-door --codex --marketplace <那份源>`；② 你用的就是公开源、只是 Codex 侧那份"
              "还没跟上 → 先跑 `workframe-door --upgrade` 刷新 Codex 侧的副本，再重跑装机。分不清是哪种："
              "`codex plugin marketplace list --json` 看 Codex 登记的市场指向哪里（只读）——那一行带 "
              "`marketplaceSource`、`sourceType` 为 `git`（root 在 `<CODEX_HOME>/.tmp/marketplaces/` 下），而你要的是"
              "本地源（本地登记被不带 `--marketplace` 的运行换掉之后就是这样）：① 会被 Codex 以「同名市场、不同源」拒绝，"
              "② 刷新的也还是那个 git 源——只能你自己先跑 `codex plugin marketplace remove <名>`（**这会从你的 Codex "
              "用户配置里删掉那条市场注册**，本命令不代做），再按 ① 带 `--marketplace <本地源>` 重跑")


def loaded_root(mine):
    """由 `hooks/list` 里本插件条目的 `sourcePath` 反推「Codex 实际加载的那一份」→ `(根, None)`；反推不出 →
    `(None, 一句人话)`；`mine` 为空 → `(None, None)`（0 条交给 0 条成因那几句）。

    反推不出有两种：有条目没有 `sourcePath`（`mine` 全缺时尤甚），或各条反推出的根不是同一处。两种都按「读不出」
    处理，**不退回本命令自己的根**：那是拿自己跟自己比，恒一致，正是「不知道」冒充「一致」。

    **这里只拿 `hooks/list` 这一个输出里的条目互相比，不拿它去跟 `plugin add` 回显的 `installedPath` 比。** 两个输出
    的路径写法能不能归一成同一处，没有真机样本（两者从未在同一次运行里并排读过），主装机路径不押在那个等式上。
    调用方对这里得出的根与 `installedPath` **各自独立**核版本与清单（`require_installed_root`），两边都过即放行——
    hook 实际来自旧版那份时，挡住它的是这份根自己的版本核对，不需要两个路径相等。
    """
    if not mine:
        return None, None
    s = own()
    door = (f"本命令这一份（{s['root']}，{('v' + s['version']) if s['version'] else '版本读不出：' + s['why']}）"
            f"{door_drift_note()}")
    roots, no_sp, bad_sp = {}, 0, []
    for h in mine:
        sp = h.get("sourcePath")
        if not isinstance(sp, str) or not sp:
            no_sp += 1
            continue
        r = installed_root_from([h])
        if r is None:                       # 有这个字段，但形态反推不出插件根（例如在盘符根下）
            bad_sp.append(sp)
        else:
            roots.setdefault(_norm_path(r), r)
    if no_sp == len(mine):
        return None, (f"hooks/list 里本插件有 {len(mine)} 条 hook，一条都没有 sourcePath——反推不出 Codex 实际加载的"
                      f"是哪一份，无法确认就是{door}；读不出不按一致放行。{_door_outlets()}")
    if no_sp or bad_sp or len(roots) > 1:
        parts = ([f"{no_sp} 条没有 sourcePath"] if no_sp else []) \
            + ([f"{len(bad_sp)} 条的 sourcePath 反推不出插件根（例：{bad_sp[0]}）"] if bad_sp else []) \
            + ([f"反推出 {len(roots)} 个不同的根（{'、'.join(str(r) for r in list(roots.values())[:3])}）"]
               if len(roots) > 1 else [])
        return None, (f"hooks/list 里本插件的 {len(mine)} 条 hook 中，" + "、".join(parts)
                      + f"——说不清 Codex 实际加载的是哪一份，无法确认就是{door}；读不出不按一致放行。"
                      + _door_outlets())
    return next(iter(roots.values())), None


def require_loaded_and_echoed(mine, echoed):
    """写项目文件 / 对账之前，把「Codex 实际加载的那一份」（`loaded_root`）与 `plugin add` 回显的那一份（`echoed`，
    可为 None）**各自独立**核版本与清单；两者是不是同一处不作要求。反推不出、或任一份不过，抛 `InstalledRootError`。
    返回 `(实际加载的根或 None, 那份根的 (条数, 清单) 或 None)`；`mine` 为空时前者为 None，只核 `echoed`（若有）。"""
    loaded, prob = loaded_root(mine)
    if prob:
        # 根记 None 而不是回显的那一份：此刻并不知道 Codex 加载的是哪一份，收尾那句「此后会加载哪一份」不能点名回显的
        raise InstalledRootError(prob, None)
    counted = require_installed_root(loaded) if loaded is not None else None
    if echoed is not None and (loaded is None or _norm_path(echoed) != _norm_path(loaded)):
        # 看起来不是同一处（也可能只是写法不同，这里分不清也不需要分清）：回显那份照样独立核一次
        require_installed_root(echoed)
    return loaded, counted


def require_installed_root(root):
    """消费安装根（写项目文件 / 对账 / 种信任）之前：它得是本命令这一份——同版本（`version_mismatch`），且
    hook 冻结清单形态完好（`manifest_count`）。任一不过抛 `InstalledRootError`（`EXIT_ENV`）；过了返回 `(条数, 清单)`。
    「Codex 实际加载的是哪一份」要一份 `hooks/list` 读数，由拿到它的调用方经 `require_loaded_and_echoed` 核。"""
    prob = version_mismatch(root)
    if prob:
        raise InstalledRootError(prob, root)
    return manifest_count(root)


def installed_root_from(mine):
    """本插件条目的 `sourcePath` 指向 `<installed>/hooks/hooks.codex.json`。形态反推不出（例如在盘符根下、往上
    没有两级）时按「没有」处理，返回 None，不抛裸异常——调用方（`loaded_root`）据此说「反推不出」。"""
    for h in mine:
        sp = h.get("sourcePath")
        if isinstance(sp, str) and sp:
            try:
                return Path(sp).resolve().parents[1]
            except (IndexError, OSError, ValueError):
                return None
    return None


def step_hooks_list(server, project, plugin_id, installed_root=None, user_now="?"):
    listing = server.hooks_list([str(project)])
    all_hooks = cas.hooks_of(listing)
    mine = cas.plugin_hooks(listing, plugin_id)
    others = len(all_hooks) - len(mine)
    # Codex 实际加载的那一份（由 sourcePath 反推）与回显的那一份各自独立核；反推不出就停，不拿本命令自己的根顶上
    loaded, counted = require_loaded_and_echoed(mine, installed_root)
    if loaded is not None:
        root, (expected, _manifest) = loaded, counted       # 对账与种信任都用实际加载的那一份
    else:                                                    # 只可能是 0 条：拿回显的（或本命令的）清单说「预期 N 条」
        root = installed_root or PLUGIN_ROOT
        expected, _manifest = require_installed_root(root)
    entry = (listing.get("data") or [{}])[0]
    if not mine:
        cause = zero_hooks_cause(server, project, plugin_id, user_now)
        raise DoorError(EXIT_COUNT, f"{plugin_id} 预期 {expected} 条、实得 0 条：" + (cause or (
            f"插件 hooks.codex.json 整份未被解析（最常见成因：某条缺 command 键）；warnings="
            f"{entry.get('warnings')} errors={entry.get('errors')}")))
    if len(mine) != expected:
        keys_have = {h.get("key", "").split(":", 1)[-1] for h in mine}
        keys_want = {r["key"] for r in _manifest.get("entries") or []}
        missing = sorted(keys_want - keys_have)
        raise DoorError(EXIT_COUNT, f"{plugin_id} 预期 {expected} 条、实得 {len(mine)} 条"
                                    + (f"：缺 {missing[:5]}" if missing else "")
                                    + f"；另有 {others} 条非本插件 hook，未动")
    bad = [h.get("key") for h in mine if not (h.get("command") or "").strip()]
    if bad:
        raise DoorError(EXIT_COUNT, f"{len(bad)} 条 command 为空: {bad[:5]}")
    if entry.get("errors"):
        raise DoorError(EXIT_COUNT, f"hooks/list 报 errors: {entry.get('errors')}")
    say(f"[5/11] hooks/list：{plugin_id} {len(mine)} 条 == manifest {expected} 条；"
        f"command 全非空、errors 空；另有 {others} 条非本插件 hook，未动"
        + (f"（此刻用户层 enabled = {user_now!r}）" if user_now != "?" else "")
        + (f"；warnings={entry.get('warnings')}" if entry.get("warnings") else ""))
    return mine, root


def print_plan(mine, project_key, project_trusted_now, plugin_id=None, user_now=None):
    pending = [h for h in mine if h.get("trustStatus") != "trusted"]
    say(f"[6/11] 下面 {len(pending)} 条将被代你信任（另 {len(mine) - len(pending)} 条已受信）；"
        f"受信 hook 脱离沙盒执行：")
    for h in mine:
        mark = "  " if h.get("trustStatus") == "trusted" else "→ "
        say(f"   {mark}{h.get('trustStatus'):9} {seed.describe(h)}")
    say(f"   项目 trust：[projects.'{project_key}'] trust_level = \"trusted\""
        f"（当前 {'已是 trusted' if project_trusted_now else '未设置，将写入'}）")
    if plugin_id:
        if user_now is True:
            say(f"   用户配置：[plugins.\"{plugin_id}\"] enabled = true → false（将写入你的 Codex 用户配置 config.toml）"
                f"——改完之后本插件只在跑过 workframe-door --codex 的项目里加载，本项目的启用表在 "
                f"{codex_roles.CONFIG_REL.as_posix()} managed 段段首")
        else:
            say(f"   用户配置：[plugins.\"{plugin_id}\"] enabled = {user_now!r}（不改）")
    return pending


# ---- 项目 trust ----

def project_key(project):
    """Codex 自己写进 `config.toml` 的形态：解析后的长路径；Windows 上小写、反斜杠。"""
    p = str(Path(project).resolve())
    return p.lower() if os.name == "nt" else p


def project_trust_edit(key):
    """keyPath 里的路径段用**双引号 ＋ 反斜杠转义**（`_quoted`）：实测单引号会连引号一起当键名（写出
    `[projects."'c:\\…'"]`），裸反斜杠会被吃掉（`c:usersxf…`）；转义形态写出的是 Codex 自己的
    `[projects.'c:\\users\\…']` 字面，`config/read` 回读键 == 路径。"""
    return {"keyPath": f'projects."{_quoted(key)}".trust_level', "mergeStrategy": "upsert", "value": "trusted"}


def project_trust_state(server, key):
    """`config/read` 里该项目的 trust_level；读不到返回 None。"""
    try:
        cfg = server.config_read(False).get("config") or {}
        projects = cfg.get("projects") or {}
        if not isinstance(projects, dict):
            return None
        for k, v in projects.items():
            if os.path.normcase(str(k)) == os.path.normcase(key) and isinstance(v, dict):
                return v.get("trust_level")
        return None
    except Exception:
        return None


def step_write(server, pending, key, need_project, plugin_id=None, disable_user=False):
    edits = cas.trust_edits(pending)
    if need_project:
        edits.append(project_trust_edit(key))
    if disable_user:
        edits.append(plugin_disable_edit(plugin_id))
    if not edits:
        say("[7/11] 无需写入（全部已受信、项目已 trusted、用户层已不是 true）")
        return 0
    try:
        res = server.config_batch_write(edits)
    except cas.AppServerRpcError as e:          # 服务端明确答了「不行」：这一种才说得出「没写成」
        raise DoorError(EXIT_WRITE, f"写入被拒：{e}；未信任任何 hook")
    except cas.AppServerError as e:             # 超时 / 进程退出 / 请求没送出去：写入可能已经生效
        raise WriteUnconfirmedError(f"config/batchWrite 没等到答复（{e}）——**未确认是否写入**：请求可能已经"
                                    f"生效。查看办法：跑 `workframe-door --check`，看 hook 是否已 trusted、项目 trust "
                                    f"与用户层 enabled 各是什么；重跑本命令是安全的（已受信的 hook 不会再写一遍）")
    if not isinstance(res, dict) or res.get("status") != "ok":
        raise DoorError(EXIT_WRITE, f"写入被拒：{str(res)[:200]}；未信任任何 hook")
    say(f"[7/11] config/batchWrite {len(edits)} 条 upsert → status ok（ok 不算成功，下一步回读）")
    return len(edits)


def step_readback(server, project, plugin_id, key, want_project, expected=None):
    user = user_layer_enabled(server, plugin_id)
    if user is True:
        raise DoorError(EXIT_READBACK, f"用户层 [plugins.\"{plugin_id}\"] enabled 回读仍为 true——任何目录的 Codex 会话"
                                       f"都会加载本插件（非 workframe 目录由 hook 早退兜住）；重跑 workframe-door --codex")
    merged = project_merged_enabled(server, project, plugin_id)
    if merged is not True:
        raise DoorError(EXIT_READBACK, f"用户层 enabled = {user!r}，但从项目看的合成值 enabled = {merged!r}（应为 true）"
                                       f"——项目 {codex_roles.CONFIG_REL.as_posix()} 段首的启用表没被 Codex 读到，"
                                       f"本项目的 hook 不会加载")
    mine = cas.plugin_hooks(server.hooks_list([str(project)]), plugin_id)
    still = [h for h in mine if h.get("trustStatus") != "trusted"]
    trust = project_trust_state(server, key)
    if expected is not None and len(mine) != expected:
        raise DoorError(EXIT_READBACK, f"hooks/list 回读 {plugin_id} {len(mine)} 条（写入前 {expected} 条）——"
                                       f"用户层置 false 之后项目层没接上")
    if still:
        raise DoorError(EXIT_READBACK, f"已信任 {len(mine) - len(still)}/{len(mine)}；未信任："
                        + "；".join(f"{h.get('key')} ({h.get('trustStatus')}, hash "
                                    f"{str(h.get('currentHash'))[:20]})" for h in still[:6]))
    if want_project and trust != "trusted":
        raise DoorError(EXIT_READBACK, f"hook 已全部受信，但项目 trust 回读为 {trust!r}（键 {key!r}）")
    say(f"[8/11] 回读：用户层 enabled = {user!r}；从项目看 enabled = true；{len(mine)}/{len(mine)} trusted；"
        f"项目 trust = {trust}")
    return mine, trust


def step_seed(installed_root, plugin_id, mine):
    path = seed.write_seed(installed_root, plugin_id, [h.get("key") for h in mine])
    say(f"[9/11] 种信任记录 → {path}")


def step_orphans(server, plugin_id, mine):
    n = seed.orphan_count(server, plugin_id, {h.get("key") for h in mine})
    if n:
        say(f"[11/11] config.toml 里有 {n} 条不对应当前 hook 的记录，可安全删除；删除后下次装机/升级"
            f"会重批。框架不代删。")
    else:
        say(f"[11/11] 孤儿信任记录 {0 if n == 0 else '（未能读取）'}")


# ---- --check ----

def features_multi_agent(exe):
    try:
        rc, out, err = run_codex(exe, ["features", "list"], timeout=60)
        for ln in (out + err).splitlines():
            if "multi_agent" in ln:
                return ln.strip()
        return f"（`codex features list` 无 multi_agent 行，rc={rc}）"
    except Exception as e:
        return f"（`codex features list` 未能执行: {type(e).__name__}）"


def user_layer_finding(plugin_id, user):
    """用户层启用值 → (是否算问题, 一句话)。算不算问题收在 `USER_LAYER_TRUE_IS_PROBLEM` 一处。"""
    if user is True:
        return USER_LAYER_TRUE_IS_PROBLEM, (
            f"用户配置 [plugins.\"{plugin_id}\"] enabled = true：任何目录的 Codex 会话都会加载本插件（非 workframe "
            f"目录由 hook 早退兜住）。出路：重跑 workframe-door --codex；最常见成因：裸敲过 codex plugin add")
    if user is None:
        return False, f"用户配置里没有 [plugins.\"{plugin_id}\"] 表（≡ 未启用；插件只在带启用表且已 trust 的项目里加载）"
    return False, f"用户层 enabled = false（插件只在跑过 workframe-door --codex 的项目里加载）"


def check_static(exe, project, mkt_name):
    """`--check` 的只读半：返回 (问题列表, 信息列表, mine)。安装根版本与本命令不同、它的 hook 冻结清单
    缺失或坏了，都只进问题列表，不让其余检查项断掉。"""
    problems, info = [], []
    plugin_id = f"{PLUGIN_NAME}@{mkt_name}"
    key = project_key(project)
    try:
        closed = codex_roles.managed_block_state(project) == codex_roles.BLOCK_USER_CLOSED
    except Exception:
        closed = False
    if closed:
        info.append(f"本项目 Codex 门已被你关闭（{codex_roles.CONFIG_REL.as_posix()} managed 段 enabled = false）"
                    f"——hook 不加载是预期；要重新开启把它改回 true 后重跑 workframe-door --codex")
    with cas.AppServer(exe=exe, cwd=str(project)) as s:
        try:
            is_problem, line = user_layer_finding(plugin_id, user_layer_enabled(s, plugin_id))
            (problems if is_problem else info).append(line)
        except cas.AppServerError as e:
            problems.append(f"用户层启用值读不到: {e}")
        listing = s.hooks_list([str(project)])
        mine = cas.plugin_hooks(listing, plugin_id)
        root, src = loaded_root(mine)          # Codex 实际加载的那一份；反推不出时 root 为 None、src 是那句人话
        if not mine:
            if not closed:
                entry = (listing.get("data") or [{}])[0]
                problems.append(f"hooks/list 里没有 {plugin_id} 的条目：" + (zero_hooks_cause(s, project, plugin_id) or (
                    f"未安装，或整份清单未被解析——最常见成因：某条缺 command 键；warnings={entry.get('warnings')} "
                    f"errors={entry.get('errors')}")))
        else:
            # 反推不出、版本、清单形态各进 problems、互不短路；清单坏了只少掉条数比对这一项，下面各项照跑。
            # 反推不出实际加载的那一份时不拿本命令自己的根顶上（那是拿自己跟自己比）：只报「反推不出」，版本与清单无从核
            if src:
                problems.append(src)
            if root is not None:
                prob = version_mismatch(root)
                if prob:
                    problems.append(prob)
                try:
                    expected, _m = manifest_count(root)
                except DoorError as e:
                    problems.append(str(e))
                else:
                    if len(mine) != expected:
                        problems.append(f"{plugin_id} {len(mine)} 条 ≠ manifest {expected} 条")
            for h in mine:
                if h.get("trustStatus") != "trusted":
                    problems.append(f"{h.get('key')}: {h.get('trustStatus')}（hash {str(h.get('currentHash'))[:20]}）")
                if not (h.get("command") or "").strip():
                    problems.append(f"{h.get('key')}: command 为空")
            entry = (listing.get("data") or [{}])[0]
            if entry.get("errors"):
                problems.append(f"hooks/list errors: {entry.get('errors')}")
            info.append(f"hooks：{len(mine)} 条，{sum(1 for h in mine if h.get('trustStatus') == 'trusted')} trusted；"
                        f"安装根 {root}")
        trust = project_trust_state(s, key)
        (info if trust == "trusted" else problems).append(f"项目 trust = {trust!r}（键 {key!r}）")
        n = seed.orphan_count(s, plugin_id, {h.get("key") for h in mine}) if mine else None
        if n:
            info.append(f"孤儿信任记录 {n} 条（可安全删除，框架不代删）")
    problems += codex_roles.verify(project)
    if trust == "trusted":
        try:
            roles = [r for r, _d, _b in codex_roles.load_agents((root or PLUGIN_ROOT) / "agents")]
        except Exception:
            roles = []
        with cas.AppServer(exe=exe, cwd=str(project)) as s:
            reg = roles_registered_problems(s, project, roles)
        (problems if reg else info).extend(reg or [f"角色注册：config/read(cwd=项目) 见 {len(roles)} 个 [agents.*]"])
    seed_path = seed.codex_home() / seed.SEED_REL
    if seed_path.is_file():
        try:
            rec = json.loads(seed_path.read_text(encoding="utf-8"))
            ver = installed_version(root) if root else "?"
            (info if rec.get("plugin_version") == ver or (closed and not mine) else problems).append(
                f"种信任记录 {seed_path}：plugin_version {rec.get('plugin_version')} vs 已装 {ver}")
        except Exception as e:
            problems.append(f"种信任记录 {seed_path} 读不出: {e}")
    else:
        info.append(f"种信任记录不存在（{seed_path}）——尚未经本命令或 G hook 种过")
    markers = sorted(state_dir_of(project).glob("current-session-codex-*.json"))
    if len(markers) > 1:
        info.append(f"同项目有 {len(markers)} 个 current-session-codex-*.json（上一个会话没跑到 "
                    f"SessionEnd？模型手写事件会因分不清而省略 session_id；7 天后自清）")
    info.append(f"features: {features_multi_agent(exe)}")
    return problems, info, mine


class _Stub400(threading.Thread):
    """本机 127.0.0.1 上对任何请求立即回 400 的 HTTP 桩：让 `codex exec` 在 hook 跑完后于首次模型
    请求处失败并退出（4 s 量级），不等鉴权、不重连、不花任何额度。"""

    def __init__(self):
        super().__init__(daemon=True)
        from http.server import BaseHTTPRequestHandler, HTTPServer
        hits = self.hits = []

        class H(BaseHTTPRequestHandler):
            def _reply(self):
                hits.append(self.path)
                body = json.dumps({"error": {"message": "workframe-door live check stub",
                                             "type": "invalid_request_error"}}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            do_GET = do_POST = _reply

            def log_message(self, *a):
                pass
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]

    def run(self):
        self.srv.serve_forever()

    def stop(self):
        self.srv.shutdown()


def _rows(path):
    if not path.exists():
        return []
    out = []
    for ln in path.read_bytes().decode("utf-8", "replace").splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def _activity_counter(state):
    try:
        return json.loads((state / "activity-state.json").read_text(encoding="utf-8")).get("session_counter")
    except Exception:
        return None


def session_counter_ticked(c0, c1):
    """验活表 session-start-prep 那一行：会话前后的 `session_counter` 读数 `c0` / `c1` 说明它跑过没有。

    `c0 is None`（会话前读不到计数：`activity-state.json` 还不存在——第二台机器 / clone 之后状态目录被 gitignore、
    第一次跑必是这一态——或读不出、或没有这个键）时按「计数被建出且等于 1」判：session-start-prep 读不到时从出厂值
    0 起加 1。不这样判的话第一次 `--check --live` 恒报「受信但未执行」，而会话其实已经把计数建成了 1。
    `c1 is None`（会话后仍读不到）恒判没跑。
    **能力边界**：文件在、hook 读得出而本命令读不出（`_activity_counter` 按 utf-8 读，带 BOM 的文件读不出；hook 侧
    按 utf-8-sig 读）时，hook 从原值加 1、不是 1，这一行仍报没跑——方向是报错、不是放行。"""
    return c1 == 1 if c0 is None else c1 == c0 + 1


def check_live(exe, project):
    """真跑一次会话，按**侧效应**逐条验活。`trusted` ＋ 列得出来都不构成「跑起来了」。
    只有无条件产生侧效应的 hook 才能判；条件输出的（heartbeat 提醒 / stale 扫描 / 待问项 /
    首个 prompt 的维护上下文）写明「本次不判」。"""
    state = state_dir_of(project)
    inj0 = _rows(state / "inject-log.jsonl")
    ev0 = _rows(state / "events.jsonl")
    c0 = _activity_counter(state)
    last = state / seed.LAST_FILE
    last0 = last.read_bytes() if last.exists() else None
    stub = _Stub400()
    stub.start()
    args = ["exec", "-C", str(project), "-s", "read-only",
            "-c", "model_provider=wfstub", "-c", 'model_providers.wfstub.name="wfstub"',
            "-c", f'model_providers.wfstub.base_url="http://127.0.0.1:{stub.port}/v1"',
            "-c", 'model_providers.wfstub.wire_api="responses"', "workframe-door live check"]
    # **验活会话零外网**：Codex 启动时异步向远端预热插件目录，网络对那台主机不通时该请求会把进程退出
    # 拖到分钟级（同一 home 上实测 3.8 s / 180 s / 221 s 交替，turn 早已结束）。把一切非回环请求经
    # 一个立刻拒绝的回环代理打掉：三次实测 3.4–3.5 s、三条注入全部抵达；桩在 NO_PROXY 里不受影响。
    # 这也把「验活会话不会往外发任何东西」从假设变成结构保证。
    env = dict(os.environ)
    env.update({"HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    t0 = time.monotonic()
    p = subprocess.Popen([exe] + args, cwd=str(project), stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    try:
        _out, err = p.communicate(timeout=120)
        timed_out = False
    except subprocess.TimeoutExpired:
        cas.kill_tree(p)
        _out, err = p.communicate()
        timed_out = True
    stub.stop()
    elapsed = round(time.monotonic() - t0, 1)
    inj = _rows(state / "inject-log.jsonl")[len(inj0):]
    ev = _rows(state / "events.jsonl")[len(ev0):]
    c1 = _activity_counter(state)
    last1 = last.read_bytes() if last.exists() else None
    table = [
        ("session-start-prep.py", "activity-state.session_counter +1（会话前没有这个计数时：建出且为 1）",
         session_counter_ticked(c0, c1)),
        ("inject-context.py --scope main --shard all", "inject-log 新增 scope=main shard=all",
         any(r.get("scope") == "main" and r.get("shard") == "all" for r in inj)),
        ("codex-trust-seed.py", "codex-trust-seed.last.json 内容更新",
         last1 is not None and last1 != last0),
        ("inject-context.py --scope prompt", "inject-log 新增 scope=prompt",
         any(r.get("scope") == "prompt" for r in inj)),
        ("session-end-flush.py", "events.jsonl 新增 session_ended（harness=codex）",
         any(r.get("type") == "session_ended" and r.get("harness") == "codex" for r in ev)),
    ]
    say(f"[10/11] 验活：codex exec rc={p.returncode} {elapsed}s{'（超时被杀）' if timed_out else ''}；"
        f"桩收到 {len(stub.hits)} 次请求；stderr 尾: {err.decode('utf-8', 'replace').strip().splitlines()[-1:] }")
    say("   （知情：Codex 仍会把这次记成一份失败的 rollout）")
    failed = []
    for script, evidence, ok in table:
        say(f"   {'ok ' if ok else 'NO '} {script:44} {evidence}")
        if not ok:
            failed.append(script)
    say("   本次不判（只在条件成立时才有输出 / 侧效应）：heartbeat-check.py、"
        "check-stale-modules.py scan-git-diff、memory-ask.py、user-prompt-inject.py；"
        "需模型轮次：PostToolUse×4、Stop、SubagentStart×2、SubagentStop")
    stray = [r for r in inj if r.get("harness") not in (None, "codex")]
    if stray:
        failed.append(f"inject-log 出现非 codex 门的行: {stray[:2]}")
    if failed:
        raise DoorError(EXIT_LIVE, "受信但未执行（侧效应缺席）: " + "；".join(map(str, failed)))


# ---- CC 门 ----

def check_cc(project):
    settings = Path(project) / ".claude" / "settings.json"
    if not settings.is_file():
        return [f"{settings} 不存在——CC 门未订阅；已装项目跑 `workframe-door --backfill` 补上，"
                f"或跑 launcher setup / 带 `--write-subscription` 的 project_scaffold.py"
                f"（**`--check` 与 `--cc` 自己只读、不写 settings**）"], []
    try:
        data = _settings_io.read_settings(settings)
    except Exception as e:
        return [f"{settings} 解析失败: {e}"], []
    enabled = data.get("enabledPlugins") or {}
    core = [k for k, v in enabled.items() if k.startswith(f"{PLUGIN_NAME}@") and v]
    mkts = list((data.get("extraKnownMarketplaces") or {}).keys())
    if not core:
        return [f"{settings.name} 的 enabledPlugins 没有 {PLUGIN_NAME}@<市场>（**`--check` 与 `--cc` 自己"
                f"只读、不写 settings**；写它的是装机链路与 /core:onboard 两个入口，已装项目用 "
                f"`workframe-door --backfill`）"], [f"extraKnownMarketplaces: {mkts}"]
    return [], [f"CC 门：enabledPlugins {core}；extraKnownMarketplaces {mkts}"]


# ---- 存量项目回填（--backfill）：检测哪扇门未接 → 只补缺的那扇 → 重跑零改动 ----

def cc_door_state(project, mkt_name):
    """CC 门此刻接没接上：`"ok"` / `"missing"`。判据与 `check_cc` 同一条（`enabledPlugins` 里
    有没有可用的 `core@<市场>`），**不另立第二套**——两套判据会各自漂。

    settings 解析不了时按 `"broken"` 返回：那不是「没接」，不能拿写入去盖（`_settings_io` 也会拒）。

    **`mkt_name` 收下但不参与判定，这是判过的、不是漏用**：判据整个委托给 `check_cc`，而它只认
    `core@` 前缀、不比市场名。于是带 `--marketplace-name X` 回填一个订阅了 `core@Y` 的项目时，
    CC 门算 `ok`。这是有意的——**已经订阅上的项目不该因为你这次敲的名字不同就被再写一份声明**，
    那只会在 `enabledPlugins` 里留下两个 `core@*`。形参留着是为了「要不要比市场名」这个决定有地方
    落；真要改成比，改这里一处即可，别去改 `check_cc`（`--check` 那条路径不该跟着变严）。
    """
    settings = Path(project) / ".claude" / "settings.json"
    if settings.is_file():
        try:
            _settings_io.read_settings(settings)
        except _settings_io.SettingsError:
            return "broken"
    problems, _info = check_cc(project)
    return "missing" if problems else "ok"


CODEX_MACHINE_UNCHECKED = ("本机那一侧（hook 信任、项目 trust、用户层启用值，存在各人的 Codex 用户配置里、不随仓走）"
                           "没核：clone 到这台机器、还没在这里跑过 --codex 时它仍是没接上的——跑 workframe-door --check 核")


def codex_door_state(project, mkt_name):
    """Codex 门此刻**在项目侧**接没接上，返回 `(状态, 人话)`。

    **只看项目文件**：hook 信任、项目 trust、用户层启用值存在各人的用户级配置里、不随仓走，这里一个都不读——
    所以 `"ok"` 只说明项目侧接好了。clone 到第二台机器时它照样是 `"ok"`，而那台机器上 `--check` 不过；
    用到它的收尾文案必须说「本机未核」（`CODEX_MACHINE_UNCHECKED`），不许说成「都已接上」。

    `"ok"`（段在、启用表是 true）/ `"closed"`（用户自己关的——**不补**，那是他的决定）/
    `"edited"`（段被手改——停下报告，不覆盖）/ `"missing"`（段不在、或启用表读不出）。

    判据全部复用 `codex_roles`（`managed_block_state` / `block_enable_value`），不自己解析 TOML。
    """
    plugin_id = codex_roles.plugin_id_for(mkt_name)
    state = codex_roles.managed_block_state(project)
    if state == codex_roles.BLOCK_ABSENT:
        return "missing", f"{codex_roles.CONFIG_REL.as_posix()} 里没有 managed 段"
    if state == codex_roles.BLOCK_USER_CLOSED:
        return "closed", "本项目的 Codex 门已被你关闭（段首启用表 enabled = false）"
    if state == codex_roles.BLOCK_EDITED:
        return "edited", "managed 段被手改过"
    if codex_roles.block_enable_value(project, plugin_id) is not True:
        return "missing", f"managed 段里没有 [plugins.\"{plugin_id}\"] enabled = true"
    return "ok", f"managed 段在、[plugins.\"{plugin_id}\"] enabled = true"


def _unc_strip(s):
    """Windows 扩展长度前缀 `\\\\?\\`——原样写进 JSON 就是一条解析不到的坏路径，必须剥掉。"""
    prefix = "\\\\?\\"
    s = str(s or "")
    return s[len(prefix):] if s.startswith(prefix) else s


def _project_declared_source(project, mkt_name):
    """本项目 `.claude/settings.json` 里 `extraKnownMarketplaces[<市场>].source`，没有则 None。

    这是**项目自己对「我的 core 从哪来」的声明**，随仓走、进 git。它只有 source、没有
    `installLocation`（那是本机落地路径，按设计不进仓）。
    """
    if not project:
        return None
    try:
        data = _settings_io.read_settings(Path(project) / ".claude" / "settings.json")
    except Exception:
        return None
    entry = (data.get("extraKnownMarketplaces") or {}).get(mkt_name)
    src = entry.get("source") if isinstance(entry, dict) else None
    return src if isinstance(src, dict) and src else None


def locate_market(mkt_name, exe=None, project=None):
    """按序问已有的注册面，拿 `(source 对象, installLocation | None, 出处)`；都问不到返回 `(None, None, 说明)`。

    **只读已有注册，绝不猜**：写进项目 settings 的必须是市场**来源**（`{"source":"directory","path":…}`
    / `{"source":"github","repo":"owner/repo"}`），不是本机落地路径——后者对 github / git 源是每台机器
    各不相同的缓存目录，协作者 clone 后解析不到、core 装不上。

    顺序：① CC 用户级 `~/.claude/plugins/known_marketplaces.json`（条目本来就是 source ＋
    installLocation 成对的，最省事）→ ② Codex 侧：root 取 `marketplace list --json`、source 取
    `<CODEX_HOME>/config.toml` 的 `[marketplaces.<名>]` → ③ **本项目自己的 settings**。

    **③ 为什么在，又为什么排最后**：它是**唯一随仓走**的那个声明，所以在「clone 到第二台机器」
    这一格里它常常是全场唯一还在的答案（项目订阅声明随仓来，用户级注册表与 Codex 市场都还是空的）
    ——这一格此前会让调用方退回默认公开仓源，把本项目悄悄接到另一个框架副本上。
    排最后是因为它**只有 source、没有 `installLocation`**：够补 Codex 那扇门（那边只要一个源串），
    不够补 CC 那扇门（用户级注册表要 installLocation）。⇒ 返回值的第二位**可能是 None**，
    调用方必须自己判要不要它。

    **② 只认 `source_type = "local"`**：git 形态现有的样本是 `source_type = "git"`、`source` 为完整的 https URL
    （`owner/repo` 输入也被 Codex 写成这样，实测 codex-cli 0.153.4）；它该改写成 settings 里哪一种市场源对象
    （`github` 的 `repo`，还是 `git` 的 `url`）、Claude Code 那一侧认不认，都没有验过——自己改写等于拿猜的值去写
    一份会进 git 的声明。遇到就把原值原样报出来、让用户自己定，**不代填**。

    **`\\\\?\\` 前缀在 ① ② 两路都要剥**：`plugin list` 给的 `marketplaceSource.source` 带它、
    `marketplace list` 给的 `root` 不带（本机 codex-cli 0.153.4 实测）——两个读数来自同一个子系统
    而形态不同，所以两处都过 `_unc_strip`，而不是只剥「今天确实带前缀的那一个」。
    """
    entry = _cc_registry_entry(mkt_name)
    if isinstance(entry, dict) and isinstance(entry.get("source"), dict) and entry.get("installLocation"):
        return entry["source"], _unc_strip(entry["installLocation"]), "CC 用户级市场注册表"

    declared = _project_declared_source(project, mkt_name)
    if exe is None:
        if declared is not None:
            return declared, None, "本项目 settings 的 extraKnownMarketplaces（没有 installLocation）"
        return None, None, ("CC 用户级市场注册表与本项目 settings 里都没有这个市场，"
                            "而本机没有 codex 可执行文件——所有注册面都拿不到市场来源")
    root = None
    rc, out, _err = run_codex(exe, ["plugin", "marketplace", "list", "--json"], cwd=project, timeout=120)
    data = _json_out(out) if rc == 0 else None
    for row in (data or {}).get("marketplaces") or []:
        if row.get("name") == mkt_name:
            root = _unc_strip(row.get("root")) or None
            break
    cfg = {}
    try:
        import tomllib
        cfg = tomllib.loads((seed.codex_home() / "config.toml").read_text(encoding="utf-8")) \
            .get("marketplaces") or {}
    except Exception:
        cfg = {}
    ent = cfg.get(mkt_name) or {}
    kind = ent.get("source_type")
    raw = _unc_strip(ent.get("source"))
    if not root or not raw:
        if declared is not None:
            return declared, None, "本项目 settings 的 extraKnownMarketplaces（没有 installLocation）"
        return None, None, ("所有注册面都拿不到市场来源（CC 注册表没有这个市场；"
                            f"Codex 侧 root={root!r} source={raw!r}；本项目 settings 里也没有声明）")
    if kind != "local":
        if declared is not None:
            # 项目自己的声明是**它自己写下的**，不是我替它猜的 ⇒ 用它，不落进「不代填」那一档
            return declared, None, (f"本项目 settings 的 extraKnownMarketplaces（Codex 侧的 source_type 是 "
                                    f"{kind!r}：非本地形态不代改写成 settings 的市场源对象，未采用）")
        return None, None, (f"Codex 侧这个市场的 source_type 是 {kind!r}、source 是 {raw!r}——"
                            f"非本地形态该改写成 settings 里哪一种市场源对象、Claude Code 认不认都没有验过，"
                            f"**不代你改写**。"
                            f"请自己确认来源后跑 launcher setup，或用 `--marketplace` 明确给出")
    return {"source": "directory", "path": raw}, str(root), "Codex 市场注册面"


def source_to_cli(source):
    """市场源**对象** → `codex plugin marketplace add` 要的那个**字符串**；认不出返回 None。

    两种形态不是一回事：settings 里存的是对象（`{"source":"directory","path":…}`），CLI 吃的是裸串。
    认不出时返回 None 而不是瞎拼一个。**调用方收到 None 必须停下**——早先这里写的是「调用方会
    退回默认源」，那句话本身就是一个缺陷：退回默认源＝把本项目悄悄接到另一个框架副本上，
    而那正是本函数存在的理由所要避免的事。
    """
    if not isinstance(source, dict):
        return None
    for key in ("path", "repo", "url"):
        v = source.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return None


def _cc_registry_entry(mkt_name):
    """CC 用户级市场注册表里本市场的条目（只读；读路径走 `_settings_io`，与 doctor 同一 BOM 口径）。"""
    try:
        reg = _settings_io.read_settings(Path.home() / ".claude" / "plugins" / "known_marketplaces.json")
    except Exception:
        return None
    entry = reg.get(mkt_name)
    return entry if isinstance(entry, dict) else None


# `project_scaffold.write_subscription` 在项目里的写入面：订阅声明本身 ＋ 备份落点 `logs/`（该函数 docstring 写明
# 「两处的备份都落项目的 logs/」）。回填按它前后比对得出「本次写过哪些项目文件」，不手写清单——手写会漏掉备份。
# 项目外的用户级注册表不在这里：它不是项目文件，那一笔由 write_subscription 自己打印的行交代。
SUBSCRIPTION_WRITE_SURFACE = (Path(".claude") / "settings.json", Path("logs"))


def _stat_under(project, rels):
    """项目里这几处（文件，或递归的目录）此刻的 `{相对路径: (大小, mtime_ns)}`；前后两次一比即得其间写过哪些文件。"""
    base = Path(project)
    out = {}
    for rel in rels:
        p = base / rel
        files = [p] if p.is_file() else (sorted(f for f in p.rglob("*") if f.is_file()) if p.is_dir() else [])
        for f in files:
            st = f.stat()
            out[f.relative_to(base).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


def do_backfill(a, project):
    """已装项目补上缺的那扇门。**只补缺的那扇；两扇都在时零写入；不含任何删除动作。**

    为什么需要它：装机链路只在**建项目那一刻**把两扇门都接上，而更早装的项目不会自己长出第二扇门
    ——在本命令之前，这类项目一条回填路径都没有，只能重走一遍装机。

    **幂等**靠两处叠加：本函数先判状态、已接的那扇**根本不调用写入方**；真调用到的那两个写入方
    （`project_scaffold.write_subscription` / `do_codex`）各自也是「已是目标状态就一个字节不写」。
    两层都要，缺上面那层时 `do_codex` 仍会去起 app-server、重跑 `plugin add`——那不是零改动。

    **不做**：不动用户已经关掉的 Codex 门（那是他的决定）；managed 段被手改时停下报告、不覆盖；
    不装插件本体与安装登记表（同装机链路，交各扇门自己的首个会话）。

    **「拿不到市场来源就停下」对两扇门都成立，没有例外。** 这条曾经只写在 CC 那一支里，于是
    「CC 已接 ＋ Codex 缺 ＋ 注册面落空」这一格会一路往下走，让 `do_codex` 拿 `DEFAULT_MARKETPLACE`
    （公开仓）去注册 Codex 市场——**把本项目悄悄接到另一个框架副本上**，而两处用户可见文案都承诺
    它会停下。那一格不是理论的：clone 到第二台机器正是它（订阅声明随仓走 ⇒ CC ok；用户级注册表
    与 Codex 市场都还是空的）。现在 `locate_market` 多了「本项目 settings」这个来源，那一格通常
    有答案；真没有时**两扇门都硬停**，绝不退回默认源。
    """
    mkt = a.marketplace_name
    cc = cc_door_state(project, mkt)
    codex_state, codex_why = codex_door_state(project, mkt)
    say(f"[1/3] 现状：CC 门 {cc}；Codex 门 {codex_state}（{codex_why}）")
    if cc == "broken":
        raise DoorError(EXIT_COUNT, f"{Path(project) / '.claude' / 'settings.json'} 解析不了——"
                                    f"回填不拿写入去盖一份坏文件；先手工修好它再重跑")
    if codex_state == "edited":
        raise DoorError(EXIT_COUNT, f"{codex_roles.CONFIG_REL.as_posix()} 的 managed 段被手改过——"
                                    f"回填不覆盖它；把段恢复成生成态（或整段删掉让门重写）再重跑。未写任何文件")

    exe = cas.find_codex_exe()
    if codex_state == "missing" and exe:
        require_own_version()      # Codex 那半要跑装机器：门自己的根坏了就在写 CC 那半之前停下
    wrote = []
    wrote_files = []   # 本次实际写过的项目文件；Codex 那半也往里追加。失败文案说写没写只从它说（`_wrote_note`）
    # 要不要去问注册面：CC 缺就要（它得有 source ＋ installLocation）；Codex 缺**且用户没有当面
    # 给 `--marketplace`** 也要（那时市场源只能从注册面来）。两个都不缺就不问——那一格的断言是
    # 「连注册面都没去问」，不是「问了但没用上」。
    user_gave_source = a.marketplace != DEFAULT_MARKETPLACE
    need_market = (cc != "ok") or (codex_state == "missing" and exe and not user_gave_source)
    source, install_location, via = locate_market(mkt, exe, project) if need_market \
        else (None, None, "本次不需要问注册面")

    if cc == "ok":
        say("[2/3] CC 门已接，跳过（零写入）")
    else:
        if source is None:
            raise DoorError(EXIT_ENV, f"CC 门要补，但拿不到市场来源：{via}。未写任何文件")
        if install_location is None:
            raise DoorError(EXIT_ENV, f"CC 门要补，但只拿到市场来源、拿不到它在本机的落地目录"
                                      f"（出处：{via}）——用户级市场注册表要的是 installLocation，"
                                      f"猜一个写进去等于给这台机器编了一条注册。先在本机注册一次市场"
                                      f"（`claude plugin marketplace add <源>` 或 `codex plugin marketplace add <源>`）"
                                      f"再重跑。未写任何文件")
        say(f"[2/3] CC 门未接，补两项订阅声明（市场 {mkt!r}，来源取自{via}：{source}）")
        import project_scaffold as ps       # 延迟 import：只在真要写订阅声明时付它
        before = _stat_under(project, SUBSCRIPTION_WRITE_SURFACE)
        ok, lines, detail = ps.write_subscription(Path(project), {
            "marketplace_name": mkt, "source": source, "install_location": install_location})
        after = _stat_under(project, SUBSCRIPTION_WRITE_SURFACE)
        wrote_files.extend(r for r in after if before.get(r) != after[r] and r not in wrote_files)
        for ln in lines:
            say(f"       {ln}")
        if not ok:
            raise DoorError(EXIT_WRITE, f"订阅声明未写入（见上）{'；' + detail if detail else ''}。"
                                        f"{_wrote_note(wrote_files, none='未写任何文件')}")
        wrote.append("CC 门订阅声明")

    left = []          # 跑完仍然没接上的门 —— 收尾那句话不许把它们说成「都已接上」
    if codex_state == "ok":
        say(f"[3/3] Codex 门项目侧已接，跳过（零写入）——{CODEX_MACHINE_UNCHECKED}")
    elif codex_state == "closed":
        say(f"[3/3] Codex 门跳过：{codex_why}——**这是你自己的设置，回填不替你改回来**；"
            f"要重新开启就把那一行改回 true 后跑 workframe-door --codex")
        left.append("Codex 门（你自己关的）")
    elif not exe:
        say("[3/3] Codex 门未接，但本机没有 codex 可执行文件——跳过（装了 codex 后跑 workframe-door --codex）")
        left.append("Codex 门（本机没有 codex）")
    elif user_gave_source:
        # 用户当面给了 `--marketplace` ⇒ 以他的为准，注册面一个字都不问、也不顶替。
        say(f"[3/3] Codex 门未接，走 --codex 装它（市场源用你显式给的 --marketplace：{a.marketplace}）")
        do_codex(a, project, wrote=wrote_files)
        wrote.append("Codex 门")
    else:
        # 用本项目**已经在用**的那个源去注册 Codex 市场，别拿默认的公开仓顶替：顶替本身就是把本项目接到
        # 另一个框架副本上。而且本机若已登记本地源（克隆目录不在时），拿默认源（git）去 add 并不会被拒——Codex 会直接把那条本地
        # 登记换成公开源（见模块抬头 §已知形态）；拒不拒，顶替都不对。
        if source is None:
            raise DoorError(EXIT_ENV, f"Codex 门要补，但拿不到市场来源：{via}。"
                                      f"**不退回默认公开仓源**——那会把本项目接到另一个框架副本上。"
                                      f"用 `--marketplace <源>` 明确给出，或先在本机注册一次市场。"
                                      f"{_wrote_note(wrote_files, none='未写任何文件')}")
        cli_src = source_to_cli(source)
        if cli_src is None:
            raise DoorError(EXIT_ENV, f"认不出市场源形态 {source!r}（出处：{via}），拼不出 "
                                      f"`codex plugin marketplace add` 要的那个串。"
                                      f"**不退回默认公开仓源**；用 `--marketplace <源>` 明确给出。"
                                      f"{_wrote_note(wrote_files, none='未写任何文件')}")
        say(f"       Codex 市场源取本项目已在用的来源（{via}）：{cli_src}")
        a.marketplace = cli_src
        say("[3/3] Codex 门未接，走 --codex 装它")
        do_codex(a, project, wrote=wrote_files)
        wrote.append("Codex 门")

    done = ("补了 " + "、".join(wrote)) if wrote else "零改动"
    if left:
        tail = f"；**仍未接上**：{'、'.join(left)}"
    elif codex_state == "ok":
        # Codex 那半只看了项目文件，本机那一侧没核（见 `codex_door_state`）——不许说成「都已接上」
        tail = "；两扇门项目侧已接；Codex 门本机未核，跑 workframe-door --check"
    else:
        tail = "；两扇门都已接上"          # Codex 门是本次装的：do_codex 已回读核过本机那一侧
    say(f"--backfill 完成：{done}{tail}")
    return not left


# ---- 主流程 ----

def do_codex(a, project, upgrade=False, wrote=None):
    # 本次运行实际写过的项目文件（相对项目根；`--backfill` 把它自己写过的也带进来）。try 内任何失败的文案末尾
    # 都由下面的统一收尾说写没写（`_wrote_note`），各 raise 点不手写这半句
    wrote = [] if wrote is None else wrote
    require_own_version(wrote)                                 # 门自己的根坏了：连预检都不必做，更不动用户配置
    step_preflight(project, a.marketplace_name, wrote=wrote)   # 写任何东西之前：项目树 / 用户 config / 插件缓存都还没动
    user_true_now = False          # 失败文案要不要带「用户层此刻为 true」：--codex 在步 3 plugin add 之后才成立
                                   # （实测写回 true）；--upgrade 只在**本地源重装**那一路成立，git 源那一路恒 false
    trust_written = False          # [7/11] 的 batchWrite 写进去没有：app-server 中途失联时那句文案据此说写没写
    try:
        exe = codex_exe()          # 这两步也在 try 内：`--backfill` 先写过 CC 那半时，它们的失败同样要说写没写
        step_version(exe)
        if upgrade:
            mkt_name = a.marketplace_name
            installed, user_true_now = step_upgrade(exe, mkt_name, f"{PLUGIN_NAME}@{mkt_name}", project)
        else:
            echoed, already = step_marketplace_add(exe, a.marketplace, project)
            mkt_name = echoed or a.marketplace_name
            if mkt_name != a.marketplace_name:
                try:
                    step_preflight(project, mkt_name, label="[2'/11]")
                except DoorError as e:
                    raise DoorError(e.code, f"{e}——市场已注册（你的 Codex 用户配置里已有 [marketplaces.{mkt_name}]），"
                                            f"插件未安装")
                user_true_now = False                  # 这一步失败时 plugin add 还没跑
            # 装前核对在它的入口里、按回显的市场名问（重新预检之后）；停下时 user_true_now 仍是 False（plugin add 没跑）
            installed = step_plugin_add(exe, mkt_name, project, registered=(already, a.marketplace))
            user_true_now = True
        plugin_id = f"{PLUGIN_NAME}@{mkt_name}"
        key = project_key(project)
        try:
            with cas.AppServer(exe=exe, cwd=str(project)) as s:
                user_now = user_layer_enabled(s, plugin_id)
                user_true_now = user_now is True
                if user_now is not True and project_trust_state(s, key) != "trusted":
                    raise DoorError(EXIT_COUNT, f"{plugin_id}：" + (zero_hooks_cause(s, project, plugin_id, user_now) or
                                                                   "用户层未启用本插件且项目未 trust"))
                visible = cas.plugin_hooks(s.hooks_list([str(project)]), plugin_id)
                # 此刻看得见 hook 就在写之前核：实际加载的那一份与回显的那一份各自独立核版本与清单；看不见的留给步 5
                loaded, _counted = require_loaded_and_echoed(visible, installed)
                if installed is None:        # --upgrade / 回显没给路径：安装根取实际加载的那一份
                    installed = loaded
                role_names, role_root = step_roles(project, installed, mkt_name, wrote)
                if upgrade and installed is None:
                    # 用户层 false 的旧形态项目：段落进去之前 hooks/list 为 0 条，角色只能先按门自己的插件根渲染；
                    # 段落盘后重读取安装根，它与首轮所用的根不是同一处时交给 step_roles：核对过了才按它重新生成，
                    # 核对不过就在那里停下（失败文案点名首轮写过的文件）。「重新生成」那句只在真的生成之后才说
                    visible = cas.plugin_hooks(s.hooks_list([str(project)]), plugin_id)
                    installed, prob = loaded_root(visible)      # 落段后看得见了：取实际加载的那一份
                    if prob:
                        raise InstalledRootError(prob, None)
                    if installed is not None and _norm_path(installed) != _norm_path(role_root):
                        first_v = installed_version(role_root)
                        role_names, role_root = step_roles(project, installed, mkt_name, wrote)
                        say(f"       角色首轮取自门自己的插件根 v{first_v}（当时 hooks/list 为 0 条："
                            f"用户层未启用、段内原无启用表）；落段后重读得安装根 {installed} "
                            f"v{installed_version(installed)}，核对通过，已按它重新生成（见上面第二个 [4/11]）")
                mine, root = step_hooks_list(s, project, plugin_id, installed, user_now)
                trusted_now = project_trust_state(s, key) == "trusted"
                pending = print_plan(mine, key, trusted_now, plugin_id, user_now)
                try:
                    trust_written = step_write(s, pending, key, not trusted_now, plugin_id,
                                               disable_user=user_now is True) > 0
                except WriteUnconfirmedError:
                    # 用户层置 false 那一键也在这次没等到答复的写入里：不再断言「此刻为 true」，文案已让人去 --check 看
                    user_true_now = False
                    raise
                user_true_now = False
                mine, trust = step_readback(s, project, plugin_id, key, True, expected=len(mine))
                reg = roles_registered_problems(s, project, role_names)
                if reg:
                    raise DoorError(EXIT_READBACK, "角色注册回读不过（config/read cwd=项目）: " + "；".join(reg))
                say(f"       角色回读：config/read(cwd=项目) 见 {len(role_names)} 个 [agents.*]，config_file 各解析到 "
                    f"{codex_roles.ROLES_DIR_REL.as_posix()}/<role>.toml")
                # 用户拍板的字面（先列清单再写，写后明说）：清单在 [6/11]，这里只报本次真写进去的条数
                say(f"       已代你信任 {len(pending)} 条 hook（清单见 [6/11] 带 → 的行；写入你的 Codex 用户配置 "
                    f"config.toml 的 [hooks.state.*]）"
                    + ("" if pending else f"——本次没有新写，{len(mine)} 条早已受信"))
                if user_now is True:
                    say(f"       已把你的 Codex 用户配置 [plugins.\"{plugin_id}\"] enabled 改为 false：本插件只在跑过 "
                        f"workframe-door --codex 的项目里加载（本项目已写启用表）；其他项目各跑一次 --codex")
                step_seed(root, plugin_id, mine)
                say("[10/11] 验活默认不做（真跑一次会话；用 --check --live 显式做）")
                step_orphans(s, plugin_id, mine)
        except cas.AppServerError as e:
            raise DoorError(EXIT_ENV, f"app-server 未响应（{str(e)[:200]}）；hook 信任状态无法读取，"
                            + ("[7/11] 的 config/batchWrite 已写入你的 Codex 用户配置、回读没做完——重跑 "
                               "workframe-door --codex" if trust_written else "未写入任何 hook 信任"))
    except DoorError as e:
        msg = f"{e}。{_wrote_note(wrote)}"
        if isinstance(e, InstalledRootError) and codex_roles.CONFIG_REL.as_posix() in wrote and not user_true_now:
            # 段首启用表已在本次写进项目、用户层不是 true ⇒ 走到这里时项目必已 trust（否则上面早以 3 停下）⇒
            # Codex 此后在本项目加载的是它侧装着的那一份，而它与本命令对不上、hook 信任本次没有重种
            msg += "。" + loads_other_copy_note(e.root, upgrade)
        if user_true_now and USER_LAYER_STILL_TRUE not in msg:
            msg += f"。{USER_LAYER_STILL_TRUE}"
        raise DoorError(e.code, msg)
    say(f"Codex 门就绪：{len(mine)} 条 hook 受信；项目 trust = {trust}；本插件只在跑过 --codex 的项目里加载；角色见 "
        f"{codex_roles.CONFIG_REL.as_posix()}。受信 hook 脱离沙盒执行；升级用 workframe-door --upgrade"
        f"（绕过它直接升级也行——下次 Codex 会话里的种信任 hook 会补种并明说）。")


def agents_md_notes(project):
    """成功出口附带的两行（**不改退出码**）：`!` 确定性——AGENTS.md「这个项目是什么」只剩框架占位，或 AGENTS.md
    不存在；`i` 启发式——CLAUDE.md 有模板之外的二级段 / `.claude/rules/` 有 md（只有 CC 读得到）。

    **两行各自独立判**（2×2：占位与否 × 有无 CC 专属面），判定式与文案都在 doctor（`agents_md_goal_of` /
    `cc_only_surfaces`），这里不另写。任一判定出异常时那一行降成一行 `i`「没判成」，另一行照判——门此刻已经装好，
    提示判不出来不该让它非零退出。
    **只在成功出口说**，逐个点名：`--codex`、`--upgrade`、`--both`（缺省；Codex 那半装上时，CC 那半有问题也说）、
    `--backfill`（结束时 Codex 门在项目侧是接上的：本次装的，或原本就接着）、`--check`（通过时）。失败时不说——
    失败文案已经够长，写没写由 `_wrote_note` 负责。挂在 `main()` 里、不挂在 `do_codex` 里：`--backfill` 经它装
    Codex 门，挂在里面会说两遍。"""
    out = []
    try:
        import workframe_doctor as doc
        state = doc.agents_md_goal_of(project)
        if state in ("absent", "unfilled"):
            out.append(f"! {doc.agents_md_goal_fact(state)}。出路：{doc.agents_md_goal_outlet()}")
    except Exception as e:
        out.append(f"i AGENTS.md「这个项目是什么」是不是占位没判成（{type(e).__name__}: {e}）——不影响本命令的结论；"
                   f"跑 workframe-doctor --group install 看 agents_md 项")
    try:
        import workframe_doctor as doc
        line = doc.cc_only_line(doc.cc_only_surfaces(project))
        if line:
            out.append(f"i 只有 Claude Code 读得到的项目内容面：{line}——Codex 看不到它们；两扇门都要用的按 "
                       f"{doc.MERGE_GUIDE} 归位进 AGENTS.md，只对 Claude Code 成立的可以留")
    except Exception as e:
        out.append(f"i 只有 Claude Code 读得到的内容面没数成（{type(e).__name__}: {e}）——不影响本命令的结论")
    return out


def say_agents_md_notes(project):
    for ln in agents_md_notes(project):
        say(ln)


def do_check(a, project):
    snapshot_own()             # --check 不刷新任何东西；存一份只为「本命令是哪个版本」全程同一个口径
    exe = codex_exe()
    try:
        problems, info, _mine = check_static(exe, project, a.marketplace_name)
    except cas.AppServerError as e:
        raise DoorError(EXIT_ENV, f"app-server 未响应（{str(e)[:200]}）；hook 信任状态无法读取")
    for ln in info:
        say(f"   i {ln}")
    for ln in problems:
        say(f"   x {ln}")
    if problems:
        raise DoorError(EXIT_READBACK, f"{len(problems)} 项不过（见上）")
    if a.live:
        check_live(exe, project)
    say(f"--check 通过：{'含验活' if a.live else '静态对账（验活加 --live）'}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--codex", action="store_true", help="装 Codex 门（按项目启用）")
    mode.add_argument("--cc", action="store_true", help="只读核 CC 门订阅")
    mode.add_argument("--both", action="store_true", help="默认：探测到 codex 就装 Codex 门，CC 门只读核")
    mode.add_argument("--check", action="store_true", help="只读对账；--live 再真跑一次会话验活")
    mode.add_argument("--upgrade", action="store_true", help="marketplace upgrade 后重种信任、重生成角色")
    mode.add_argument("--backfill", action="store_true",
                      help="已装项目回填：检测哪扇门未接，只补缺的那扇（重跑零改动）")
    mode.add_argument("--migrate", action="store_true",
                      help="已装项目迁移：只出 plan（写 logs/workframe-migrate/plan.json）；带 --apply --confirm <码> 才执行")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--apply", action="store_true", help="只与 --migrate 同用：照 plan 执行（由你本人跑，模型不得传）")
    ap.add_argument("--confirm", default=None, help="只与 --migrate --apply 同用：plan 打印的确认码")
    ap.add_argument("--project", default=None, help="项目根；缺省从 cwd 向上找 .workframe-config.json")
    ap.add_argument("--marketplace", default=DEFAULT_MARKETPLACE, help="市场源（owner/repo、git URL 或本地目录）")
    ap.add_argument("--marketplace-name", default=DEFAULT_MARKETPLACE_NAME)
    a = ap.parse_args(argv)
    # `--apply` 单独出现时不能落进下面的默认分支——那一支探测到 codex 就装 Codex 门、写用户配置
    if (a.apply or a.confirm is not None) and not a.migrate:
        ap.error("--apply / --confirm 只能与 --migrate 同用")
    if a.confirm is not None and not a.apply:
        # 漏打 `--apply` 时不静默重出 plan——那会让人以为执行过了
        ap.error("--confirm 只能与 --migrate --apply 同用")
    if a.migrate and a.apply and not a.project:
        ap.error("--migrate --apply 必须显式给 --project <项目根>")
    project = Path(a.project).resolve() if a.project else (_harness.find_root() or Path.cwd().resolve())
    if not (project / _harness.ROOT_MARKER).is_file():
        say(f"{project} 不是 workframe 项目根（没有 {_harness.ROOT_MARKER}）——先跑 launcher setup / "
            f"project_scaffold.py")
        return EXIT_ENV
    say(f"workframe-door：项目 {project}")
    if a.migrate:
        import workframe_migrate       # 延迟 import：只在迁移时付它（它连带 import doctor 与 scaffold）
        return (workframe_migrate.cli_apply(project, a.confirm, out=say) if a.apply
                else workframe_migrate.cli_plan(project, out=say))
    try:
        if a.codex:
            do_codex(a, project)
            say_agents_md_notes(project)
        elif a.upgrade:
            do_codex(a, project, upgrade=True)
            say_agents_md_notes(project)
        elif a.backfill:
            if do_backfill(a, project):
                say_agents_md_notes(project)
        elif a.check:
            do_check(a, project)
            say_agents_md_notes(project)
        elif a.cc:
            problems, info = check_cc(project)
            for ln in info:
                say(f"   i {ln}")
            for ln in problems:
                say(f"   x {ln}")
            return EXIT_COUNT if problems else EXIT_OK
        else:
            problems, info = check_cc(project)
            for ln in info + [f"x {p}" for p in problems]:
                say(f"   {ln}")
            if cas.find_codex_exe():
                do_codex(a, project)
                say_agents_md_notes(project)
            else:
                say("未探测到 codex 可执行文件——Codex 门跳过（装了 codex 后跑 workframe-door --codex）")
            return EXIT_COUNT if problems else EXIT_OK
    except DoorError as e:
        say(f"workframe-door 失败（exit {e.code}）：{e}")
        return e.code
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
