---
name: setup
description: '创建一个新的 Workframe 项目，或把已有项目接入 Workframe。按用户意图与目标目录状态分流（新建 / 接入），对话采集业务上下文后落骨架、订阅 core 插件、完成落盘验收。仅用于 Workframe 框架的项目初始化，不承担 npm init / create-react-app / git init 等通用脚手架。用于用户想用 Workframe 开一个新项目，或想把手上已有的项目接入 Workframe 时。触发词：创建 workframe 项目、接入 workframe、初始化 workframe、workframe setup。**不适用**：已接入项目内的日常工作（由 core 插件的角色与技能承担）；与 Workframe 无关的通用脚手架初始化。'
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion]
user-invocable: true
---

# Workframe 项目初始化

## 定位

Workframe 的**唯一入口**：在任意目录发起，创建新项目或接入存量项目，完成后由 core 插件接管项目内的日常工作。

本 skill 属用户级 `workframe-launcher` 插件，与项目级 `core` 插件分层：launcher 管「怎么开局」，core 管「项目里怎么干活」。

## 依赖方向（硬约束）

launcher 只能**单向**依赖 core，且**只能**通过 marketplace 注册信息定位 core。
**两扇门各有自己的注册信息，所以定位器有两个**：

```
CC 门：   读 ~/.claude/plugins/known_marketplaces.json  → 取本市场 installLocation
Codex 门：读 codex plugin marketplace list --json        → 取本市场 root
  → 拼 <installLocation 或 root>/plugins/core/...
```

**不要去探测「我现在跑在哪扇门里」，按结果判**：两个定位器按序各跑一次，**谁解析得到用谁**。
框架自己就是拒绝探测的——`_harness.harness()` 的判据是命令行 `--harness`（两份 hooks.json
各自传），它的 docstring 逐字写着探测法在嵌套启动时 `CLAUDE_*` 会泄漏进 Codex 的 hook 环境、
恒判成 CC；而本 skill 根本没有 hooks.json、没人给它传 `--harness`。
定位器跑不通（命令不存在 / 文件不在 / 解析失败）就是「这扇门没有」，**不是错误，不要报错停下**。

**禁止**用 `../` 一类相对路径向上找 core：插件安装时按插件逐个复制到缓存
（`cache/<市场>/<插件>/<版本>/`），launcher 的安装目录旁边不存在 core。
目录源（开发期）碰巧有兄弟目录，GitHub 源（用户侧）没有——该写法开发期能跑、发布后必坏。

**定位脚本 A（CC 门的注册表）**——每次执行前跑一次，一次拿到 `CORE` 根路径**和市场源**：

```bash
python - <<'PY'
import json, pathlib
km = json.loads((pathlib.Path.home()/".claude/plugins/known_marketplaces.json").read_text(encoding="utf-8"))
for name, meta in km.items():
    loc = pathlib.Path(meta.get("installLocation", ""))
    mf = loc/".claude-plugin"/"marketplace.json"
    if not mf.exists():
        continue
    names = {p.get("name") for p in json.loads(mf.read_text(encoding="utf-8")).get("plugins", [])}
    if not ({"core", "workframe-launcher"} <= names):
        continue
    src = meta.get("source") or {}
    kind = src.get("source") or ("directory" if src.get("path") else "?")
    origin = src.get("repo") or src.get("url") or src.get("path") or "?"
    print(f"MARKET={name}\tCORE={loc/'plugins'/'core'}\tSOURCE={origin}\tKIND={kind}\tVIA=cc")
PY
```

**定位脚本 B（Codex 门的注册表）**——A 一条都没输出、或要核对是不是同一个市场时跑：

```bash
python - <<'PY'
import json, os, pathlib, shutil, subprocess
try:
    # 必须先 which 出全路径：Windows 上 npm 装的是 codex.CMD，而 CreateProcess 不按
    # PATHEXT 找——直接给 "codex" 会抛 FileNotFoundError，看起来就像「没装 codex」
    exe = os.environ.get("WF_CODEX_EXE") or shutil.which("codex")
    r = subprocess.run([exe, "plugin", "marketplace", "list", "--json"],
                       capture_output=True, text=True, encoding="utf-8", timeout=90)
    rows = json.loads(r.stdout).get("marketplaces", []) if r.returncode == 0 else []
except Exception:
    rows = []                      # 没装 codex / 命令失败 ⇒ 这扇门没有，不是错误
cfg = {}
try:
    import tomllib
    home = pathlib.Path(os.environ.get("CODEX_HOME") or (pathlib.Path.home()/".codex"))
    cfg = tomllib.loads((home/"config.toml").read_text(encoding="utf-8")).get("marketplaces", {})
except Exception:
    pass
UNC = chr(92)*2 + "?" + chr(92)    # Windows 扩展长度前缀，写成 chr() 免得被 shell 吃掉反斜杠
for row in rows:
    root = pathlib.Path(row.get("root") or "")
    mf = root/".claude-plugin"/"marketplace.json"
    if not mf.exists():
        continue
    names = {p.get("name") for p in json.loads(mf.read_text(encoding="utf-8")).get("plugins", [])}
    if not ({"core", "workframe-launcher"} <= names):
        continue
    name = row.get("name") or "?"
    entry = cfg.get(name) or {}
    origin = str(entry.get("source") or "?")
    if origin.startswith(UNC):
        origin = origin[len(UNC):]
    st = entry.get("source_type") or "?"
    print(f"MARKET={name}\tCORE={root/'plugins'/'core'}\tSOURCE={origin}\tKIND="
          + ("directory" if st == "local" else st) + "\tVIA=codex")
PY
```

**B 的三条已知边界，念给用户之前自己先知道**：①`marketplace list --json` **只在 git 登记的那一行带
`marketplaceSource`，本地登记的那一行只有 `{name, root}`**——`SOURCE` 统一从 `<CODEX_HOME>/config.toml` 的
`[marketplaces.<名字>].source` 取，那正是上面那段 toml 解析在做的事；②本地源的 `source` 带 `\\?\` 扩展长度前缀，
**原样写进 JSON 就是一条解析不到的坏路径**，必须按上面那样剥掉；③**git 形态的 Codex 市场源**：`source_type` 是
`git`、`source` 是完整的 https URL（`owner/repo` 也被 Codex 写成这样），`CORE` 落在 Codex 自己的克隆目录里——
拿到非 `local` 的值时把原值念给用户确认，不要自己改写。

两个脚本的输出合起来按 `MARKET` 去重（同一个市场两扇门都注册时，`CORE` 应当指向同一处；
**不同**就把两条都摆给用户，不要自己挑）：

命中 0 个 → 停下告知用户「未找到 workframe 市场，请先注册市场源」（CC 门是
`claude plugin marketplace add <源>`，Codex 门是 `codex plugin marketplace add <源>`）。
命中 ≥2 个（用户同时有开发目录源与 GitHub 源）→ 按 §交互纪律 出选择题让用户选一个，不要自己挑。

**`SOURCE` 就是 §5 步骤 2 写进项目 settings 的那个市场来源值——不要用
`CORE` 或 `installLocation` 顶替**。三种形态：`KIND=github` 时 `SOURCE` 是 `owner/repo`；
`KIND=git` 时是仓库 URL；`KIND=directory` 时是本地绝对路径。
`installLocation` 对 github / git 源是**本地缓存目录**（`~/.claude/plugins/marketplaces/<市场名>`），
拿它当市场源写进项目 settings，等于把每台机器各不相同的缓存路径当成插件来源——
协作者 clone 后解析不到，core 装不上（doctor 的 `subscription` 项会报本地目录源告警，
但那时项目已经建好了）。`KIND=directory` 时同样要提醒用户该项目对协作者不可自动安装（见 §5.5）——
两扇门的注册表里读出来的目录源都是绝对路径，照着写进 settings 落的就是 §5.5 那张分档表里报 `!` 的那一档
（框架仓嵌套在项目内、手写成项目内相对路径的开发者形态除外——那一档随仓库走，不报警）。

## 交互纪律（本 skill 内生效，不依赖项目侧任何文件）

launcher 常在**尚未接入 Workframe 的目录**运行，此时项目根还没有 `AGENTS.md`，以下纪律必须由本 skill 自带：

1. **选择题一律出卡**：手上有 `AskUserQuestion` 工具就用它，不要在正文里列 A/B/C 让用户敲字。
   **是不是选择题看「答案空间」，不看问题措辞**——「有没有 X」「要不要 Y」是二元选择题，
   该出卡，哪怕它被顺嘴问出来（2026-08-11 走查教训：Q4 被当开放题混进了文字段）。
   一次调用可承载多个相关问题（组件分隔渲染），别为省往返把选择题揉进正文。

   **工具不在手上时的降级——这段话逐字写在这里，不引用任何别处的文件。**
   本 skill 常跑在**尚未订阅 core 的目录**，那里没有项目侧文件、也没有任何注入进来的纪律片
   可读；去引用它们等于引用一份此刻不存在的东西。所以规则原地写全：

   工具调用报「不存在」、或这扇门根本没有这个工具时，**不要因此停下，也不要把选择题咽回去
   替用户决定**。改为在响应正文里——

   - 编号列出选项 `A` / `B` / `C`，每项一行，写清**选了它会发生什么**；
   - 标出**推荐项**，给一句理由；
   - 明说「这一项需要你来定，回复编号即可」；
   - 继续执行**不依赖这个答案**的部分，答案回来再接上。

   **降级只改呈现形态，不改闸门本身**：§4 结构闸的「确认前零写盘」在降级形态下一字不变——
   用户回复之前一个字节都不写。本段其余各条（复杂内容先在正文展示、说人话、输出分档）
   同样照旧适用，它们本来就不依赖那个工具。
2. **复杂内容先在正文完整展示**（模块树、路径映射表、文件清单），AskUserQuestion 的选项只做轻量判断（「符合，按此执行」/「需调整」）——选项 preview 不承载关键信息。
3. **确认前零写盘**。方案确认页是唯一闸门，用户明确确认前不创建任何目录或文件。
4. 用户已表达倾向时正面回应：同意给理由，不同意给具体替代方案，不含糊带过。
5. **说人话**。来初始化项目的多半是 PM，不是工程师。技术名词（monorepo / git init / scaffold / 暂存区…）该用就用，但第一次出现时用一句日常话带出**它对用户意味着什么**——「已 init 但 0 次提交的 monorepo」不如「多包代码仓刚建好、还没有任何提交记录」。目标是把「发生了什么、对你意味着什么、可以怎么选」讲到初次使用的人能直接决策；具体怎么说不设模板，由你按对象发挥（2026-08-11 用户反馈：扫描汇报判定全对，但口径偏工程师）。
   解释**首现一次到位**：同一概念第一次出现时讲透，之后直接用，不逐轮重复——多轮交互里
   反复解释是内容墙的另一来源。
6. **输出密度：分档呈现，表格优先**（2026-08-11 用户反馈：每步确认内容偏多，核对费劲费时）。
   确认类输出把内容分两档：**等用户拍板的**（会写进或改动用户文件的内容、可当场驳的推断结论）
   完整展示；**供用户了解的**（框架介绍、日后随时可查的速查内容）压到最短、给一句指路。
   呈现表格优先——每行一个可核对的事实，散文只用来说「为什么」。压缩幅度与排版按现场判断，
   不设模板；底线一条：**决策对象不因简洁缺席**。

## §1 入口分流

### 1.1 判定目录状态

按顺序判，前面命中就不往下走：

| 步 | 判据 | 结论 |
|---|---|---|
| 1 | 有 `.workframe-config.json` | **已接入** |
| 2 | **根级**有 `.git`，或根级有项目清单（`package.json` / `pyproject.toml` / `Cargo.toml` / `go.mod` / `pom.xml` / `*.sln` …） | **项目目录** —— 这条**一票否决容器**，monorepo 里塞多少个子包都走这条 |
| 3 | 是众所周知的系统容器位置：`~/Desktop` `~/Downloads` `~/Documents` `~` 本身、盘符根、`/Users/<name>` `/home/<name>`，含各语言本地化名（桌面 / 下载 / 文档 / デスクトップ …） | **容器目录** |
| 4 | 近空（只有 `.git` / `README` 之类） | **空目录** |
| 5 | 综合判断：内容彼此无关（几个不同主题的目录 + 散落的截图安装包）且无「这是一个整体」的标志 → 容器；内容有主题一致性、README 在描述"这个东西是什么" → 项目 | 按判断走 |
| 6 | **拿不准** | **按项目目录处理** —— 判错只影响推荐顺序，不删任何选项 |

> **为什么第 2 步要一票否决**：早期草案用「子项里有 ≥2 个独立项目标志」判容器，这会把
> **monorepo 直接判成容器**，而 monorepo 恰恰最需要「接入」——那不是不便，是功能没了。
>
> **目录扫描的结果只用于判断目录状态**，不得直接拿去推断用户的业务或项目名（见 §2 约束）。

### 1.2 分流

**原则：明确意图不拦路（至多一句轻确认），模糊意图才出选择卡。**

先分清两个容易混的说法：

- **就地建** = 当前目录**本身**成为项目根
- **在此处建** = 在当前目录**下面**建一个子目录当项目根 ← 容器目录下的正常做法

| 用户表达 ↓ \ 目录状态 → | 空目录 | **容器目录** | 项目目录（未接入） | 已接入 |
|---|---|---|---|---|
| **明确「新建」** | 轻确认：就用这个空目录，还是另选位置？ | 在此处建子目录（Q3 首选 `<容器>/<项目名>`） | 见 §1.3 | 轻确认：「这里已接入，是要在其他位置新建吧？」 |
| **明确「接入」** | 说明无可接入 → 顺势转就地新建 | **说明容器目录不适合整个接入**，问是要接入它下面的某个项目，还是在此处新建 | 走 §3 B 路径 | 提示已接入；可顺带跑 doctor 体检 |
| **模糊**（「初始化」「用上 workframe」） | 默认就地新建，确认一句 | 选择卡：**在此处建一个项目目录（推荐）/ 换个位置建**——**不出现「接入当前」** | 选择卡：接入当前（推荐）/ 在此处建子目录 / 另外建 | 提示已接入 + 问是否另建 |

**容器目录下「接入当前」不出现的唯一理由**：把桌面 / 下载夹本身变成 workframe 项目，在任何
情况下都不是用户想要的——那会在容器里散出 `projects/` `logs/` `.claude/` `CLAUDE.md` 一堆东西。

**兜底：用户意图永远高于自动判定。** 用户明确说「就接入当前这个目录」时照做，哪怕判定为容器。

### 1.3 项目目录里说「新建」时先问一句

这时「新建」有两种意思，差别很大，**不要替用户选**：

> 检测到当前目录是一个已有项目。你说的「新建」是指——
> 1. 在它下面建一个子目录当 workframe 工作区（如 `<repo>/pm/`）
> 2. 把**这个项目本身**接入 workframe

选 2 时必须提示：**这等同于「接入」**，会顺带做存量资料分析与 CLAUDE.md / AGENTS.md 整合。跳过整合的话，
你原文里的项目内容**不会归进 AGENTS.md**，CLAUDE.md 也没有那行 `@AGENTS.md` 导入——落盘验收会以
error 抓出（doctor 的 claude_md 项查导入行，不只看文件在不在）。用户坚持只要纯骨架也可以，
但要让他知道跳过了什么。

## §2 A 路径：全新创建

四个问题，全部遵循「候选 + 自填」，逐个问、不要一次性倒给用户。

| 步 | 问题 | 形式 | 要点 |
|---|---|---|---|
| Q1 | 你在负责什么类型的产品？ | 选择卡，**选项即格式示范** | 示例选项：`toC 社交（海外陌生人交友）` / `toC 内容社区（短视频、图文）` / `toB SaaS（客服系统、CRM）`，选项写法本身示范回答颗粒度（产品形态 + 业务域）。这是**业务上下文采集**，喂给 AGENTS.md 业务背景段、role_profile 推断与名称候选 |
| Q2 | 项目叫什么？ | 基于 Q1 生成 2-3 个候选 + 自填 | 这是展示名（启动横幅、报告用），可以与目录名不同 |
| Q3 | 放在哪个目录？ | 候选 + 自填 | 首选候选按目录状态给：空目录→当前目录本身；**容器目录→`<容器>/<项目名>`**；项目目录→`<当前项目>/<项目名>`。**就地新建（空目录）时本问自动跳过**。**给候选前必须先查该路径存不存在**——见下 |
| Q4 | 有没有已有资料或在维护的目录要纳入分析？ | **选择卡**（本质是「有 / 没有」二元题——选择题看**答案空间**不看措辞）| 选项措辞自定，但须含「没有」的快捷出口与「有 → 给路径」的自填通道。有 → 走 §3 的分析能力；没有 → 标准骨架起步，日后在项目内随时用 `migrate-to-modules`（规范 md）/ `requirement-archiving`（异构原料）补充 |

### 必填字段从哪来

scaffold 有三个必填参数，上表只直接产出其中一个——**别把另外两个凭空编出来**：

| 字段 | 来源 |
|---|---|
| `project_name` | Q2 直接产出 |
| `one_line_goal` | **必须问用户**（见下 Q5），Q1 派生不出来——Q1 答的是**品类**（「toC 内容工具」），这里要的是**这个项目做成什么**（「做一个多平台自媒体内容生产与分发工具」） |
| `business_context` | 由 Q1 + Q5 合成即可（两个来源都是用户亲口说的，不算编） |

**Q5 一句话目标**：也用**选择卡**——基于 Q1 的回答派生 2-3 个候选目标供快速选择
（候选即格式示范，方向、颗粒度贴着用户的业务写），用户不满意随时自填。问的时候顺带
说清用途——「会写进 AGENTS.md 的「这个项目是什么」段，影响模块切分与角色推断；写具体一点更好」。
写进 AGENTS.md 的是用户**选中或自填的原文**，候选只是快选通道。

**Q4 与 Q5 可并入同一次 AskUserQuestion 调用**（组件天然分隔多个问题，不会变成文字墙；
2026-08-11 用户反馈：旧的「合并成一段文字问」正是墙的来源，已废止）。**Q1 必须单独问**，
它的答案决定后面所有候选。

三个字段**都必须在确认页的「项目基本信息」里展示**：它们会原样进 CLAUDE.md（项目名）与 AGENTS.md（目标与业务背景），用户在确认页
不过目，之后就没人看了。

### Q3 候选必须先查路径是否被占（硬约束）

项目名默认会变成目录名，而**用户起的名字很可能跟已有目录撞上**——尤其是他选了那个从当前
目录推断出来的候选时（选「my-blog」→ 默认目录 `<桌面>/my-blog` → 正是那个代码仓）。

给出候选前，对每个候选路径判一次：

| 路径状态 | 怎么办 |
|---|---|
| 不存在 | 正常作候选 |
| 存在但近空（只有 `.git` / `README` 等） | 可作候选，提一句「该目录已存在但基本是空的」 |
| **存在且有实质内容** | **不能作候选**。摆出来问用户：是想**接入**这个已有项目（走 §3，会做存量分析与 CLAUDE.md 整合）／在它**下面**建子目录／还是换个名字？ |

最后一种情况**别自作主张**——「新建」和「接入」是两条不同的路，选错了后果是静默的：
按新建硬来虽然不会破坏文件，但会跳过整合，他原文里的项目内容归不进 AGENTS.md，CLAUDE.md 也补不上 `@AGENTS.md` 导入。
脚本侧有 `--require-empty` 兜底（退出码 3），但那是最后一道，不该指望它替你问用户。

### 候选从哪来（硬约束）

**Q2 / Q5 的候选主体必须从 Q1 的回答派生，不是从当前目录的内容派生。** A 路径是「另外建一个
新项目」，当前目录只是你敲命令的地方，跟新项目没有关系——拿它推断业务是类别错误。

允许**至多 1 个**候选来自当前目录内容，但三条同时满足才行：

1. **标注推断依据**——「看到你桌面上有 `my-blog`，如果是给它建的话…」，让用户知道你为什么
   这么猜。不标注的话，错误推断会被误读成"系统知道什么内情"
2. **不作为推荐项**（不是默认高亮那个）
3. 其余候选照常从 Q1 派生

> 实测教训（2026-08-10 E2E）：在桌面发起时，Q2 的三个候选**全是**从桌面上一个代码目录推断出的
> 变体，用户只能选「自填」才逃得出去——推断变成了**默认值**。而「桌面上有 X」推不出
> 「我要为 X 建项目」，这个推断即使碰巧猜对也不成立。
>
> Q4 用户明确给出的资料路径不受此限——那是他主动交给你分析的。

**role_profile 自动推断，不设问**（结果在确认页展示、可调）：

- Q1 文本明显以 AI / LLM / Prompt / Agent 能力为核心 → `ai-product`（「智能 X」「自动化」等含混词**不**算）
- 文本命中「个人 PM / 一人 / 没研发 / 自己做」等 → `solo-pm`
- 否则 → `software-team`（默认）

**判据是「谁在这个项目里真的干活」，不是「公司里有没有这个工种」。**

B 路径能直接观测到有没有代码（见 §3 观测维度），**观测到的事实优先于文本推断**：项目里没有
代码时不要落进 `software-team` 默认档——那会把 @dev / @qa 设为主力，而这个项目里没有东西
可写、也没有东西可测。纯文档 / 需求项目按 `solo-pm`（pm 主力）判。

> 实测教训（2026-08-10）：一个零代码的产品文档项目被判成 `software-team`，依据是原文里的
> 「研发给的需求文档」「和研发对齐用飞书」。推断执行得没错，是判据本身把「有研发协作」
> 当成了「研发在本项目内」——而那些研发在 TAPD 和飞书那头，从不进这个仓库。
> 确认页已要求写明推断依据，**依据里必须出现「本项目内有/无代码」这一条**，让用户一眼能驳。

## §3 B 路径：存量项目接入

**就地分析，扫描证据后带证据确认，不设问**——用户从「填表」变成「确认」。

| 观测维度 | 观测方式 | 影响 |
|---|---|---|
| 有没有代码 | Glob 源码 / 识别脚手架 | 模块树切分证据 + `submodule.yaml.code_paths` 建议 + 接入后 code-to-doc 计划 |
| 有没有存量文档 | Glob md / docx / xls 等 | 搬家分流计划（进确认页） |
| 带不带团队 | `git shortlog -sn` 贡献者数 | 仅 role_profile 推断，**不影响目录结构**；确认页可调 |

补充几条：

- **扫描汇报讲结论不铺过程**：三个观测维度一张表结清（看到什么 / 对你意味着什么），
  判定结论先行；分析细节留给用户追问再展开。

- **项目已有 `CLAUDE.md`（或 `AGENTS.md`）时必须做整合**——这是 B 路径最容易漏、后果最隐蔽的一步。
  scaffold 对已存在的 CLAUDE.md、AGENTS.md 一律跳过不覆盖，不整合的话：hooks 装上了、必载纪律注入了、
  4 个 agent 能路由，但项目 CLAUDE.md 里**没有那行 `@AGENTS.md` 导入**（AGENTS.md 在 CC 会话里一行都
  读不到），已有的 AGENTS.md 也**缺四个契约段**、用户原文里的项目内容没归位。落盘验收会以 error
  抓出（claude_md 项查导入行、agents_md 项查四个契约段，不是只看文件存在；「这个项目是什么」仍是框架占位时 agents_md 报 warn）。框架的角色表 / 路由规则 /
  状态流转与签发权限由注入片投递，**不写进这两个文件**。
  规范见 `<CORE>/reference/claude-md-merge-guide.md`，**本阶段只做到"分类原文 + 出合并稿"**，
  写盘在 §5。
  **合并稿必须在确认页正文露出——但只露含用户原文的段落**（业务背景 / 项目自有判据 /
  业务目录速查等）。框架样板段（订阅声明 / 快速入门 / 框架相关 / 委派子 agent / 两扇门的差异）
  每个项目一模一样，贴出来是噪音。合并质量只体现在用户原文那几段，不给就等于让用户盲签一份即将覆盖自己文件的东西。
  「全文太长会盖掉方案页」是真的，但解法是**挑段落**，不是改用结构摘要代替。
- 已有 `.claude/agents/<custom-role>.md` 时自动检测四项并提议 patch：是否误用 `memory:` frontmatter、body 是否引用 agent-protocols、`.workframe/agent-memory/<role>/` 是否存在、**`tools:` 是否含 `Skill`**（`tools` 是白名单，缺 `Skill` 则该角色**完全无法调用任何 skill**，而纪律片里点名要调的 skill 会静默空转）。**这是装机 / 接入时的一次性检测，不是常驻闸**——它覆盖那一刻已存在的自定义 agent，装机之后新增或改坏的不在其内（项目级 `.claude/agents/**` 无机器闸，validate 是框架仓工具、不随插件分发）。**这四项是最低集、不是全集**——完整的 frontmatter 基线在 `<CORE>/reference/role-customization-guide.md` 的角色模板，体检时该照它过一遍，别让这份枚举把开放式检查收窄成闭合清单。
- 存量文档的处理**层层递进，不一次性塞给用户**（2026-08-11 走查后定稿）：本阶段只做
  **粗扫**（形态计数 + 标题级抽样：md 读 H1/H2、docx 读标题与首段、xlsx 读表头，
  **不逐字深读**——供模块树提议用，见 `material-intake` §两级盘点）。每份文件的安置与
  原件处置在建树后的**逐模块小闸**（§5 步骤 4）深读后逐模块确认、当场执行；
  **整理归档**（原始资料 → 正式需求文档，即 requirement-archiving）的节奏在节奏闸
  （§5 步骤 4.5）拍。结构闸只拍项目级的事——用户核对量小，改动也不连锁。
- 项目已有 `project_name` / `role_profile` 等配置时，确认页必须展示既有值：scaffold 对已有用户字段不覆盖，但展示名会按确认结果刷新。
- **会被修改的已存在文件必须逐个点名**，不能只说「你现有 N 个文件一个不动」。
  **但这份清单你不要手写**——渲染确认页之前先跑 §4 那条 `--print-plan`，逐字贴它的输出；
  本段只说明**它算出来的东西各是什么意思**，不是让你照着再列一遍。
  两处各列一份时，不一致了没人规定按哪份贴。
  脚本**会改**的项目内那几类在前两格（项目外那一处在第三格，见下一条）：
  已存在的 `.gitignore`（末尾追加 managed block，不动用户原有规则）、
  已存在的 `.claude/settings.json`（**受保护资产**；订阅那一步 merge 写两个键
  `extraKnownMarketplaces` / `enabledPlugins`，你原有的键一个不动，先落备份到 `logs/`）。
  已存在的 `CLAUDE.md` / `AGENTS.md` **不在这两格里**——脚本对它们一律跳过不覆盖，
  它们在**第四格**（`已存在·本脚本不覆盖`）里逐项报出名字，而那一格只断言一件事：本脚本不动它。
  **这两个文件最后确实会被改写，改写的人是你**（§5 步骤 1.5 按合并稿写回、按 merge-guide 判备份），
  脚本预演不到那一步 ⇒ 贴完第四格之后**再自己补一句**「合并稿由我写回，备份去向是 X」，
  与本段上面「整合」那条是同一件事的两半。
  实测教训：确认页报「现有 6 个文件一个不动」时，那个 6 悄悄没算 `.gitignore`——数字没说谎，
  但用户读到的是「什么都不碰」，而它确实被改了。**统计口径排除掉的东西，必须在同一处说明白**。
- **还有一处写在项目外，同样必须点名**：用户级市场注册表
  `~/.claude/plugins/known_marketplaces.json`（订阅那一步补一条本市场的注册条目，同样 merge、
  同样先落备份）。它**不是项目级文件**，所以不在 §4「只拍项目级三件事」那三件里——
  **正因为如此才要在动作清单里单独列一行**，否则它就成了整条链路上唯一一处
  「会改用户既有文件、却从没在确认页出现过」的写入。（这一处**在**脚本输出里，在第三格。）
- **装 Codex 门那一步还会写两处，它们不在脚本输出里**（探测到 `codex` 时才有）：**用户级**
  `config.toml`（本插件 hook 的信任 ＋ 本项目的 trust ＋ 本插件 `enabled` 改为 `false`）与项目 `.codex/`。
  `--print-plan` 只预演骨架脚本，算不到这一步 ⇒ 动作清单里那一行**要你自己加**，
  连同「会代你信任本插件的全部 hook、受信 hook 脱离沙盒执行，且以后插件升级时未受信的本插件 hook
  会在开 Codex 会话时自动补种信任、不再单问」那句披露；
  措辞与「不写死条数」的理由见 `reference/proposal-page.md` 第 8 项第三条展开。

## §4 结构闸（写盘与订阅前的唯一闸门）

在正文完整渲染，三条硬性标准见 [`./reference/proposal-page.md`](./reference/proposal-page.md)：用 PM 自己的业务语言展示模块树、给「你以后每类东西放哪」映射表、框架术语必须带业务例子。

**只拍项目级三件事**：模块树（粗扫标题级证据支撑）、CLAUDE.md / AGENTS.md 合并稿（只露用户原文段落）、
会被修改的已有文件点名。**每份文件的去向与处置不在这里拍**——那是逐模块小闸（§5 步骤 4）
的事：一次性塞给用户既难核对，改树时又全部连锁重算。树也不是一锤定音：逐模块深读发现
更合理切分时回来提议微调（连锁已被逐模块机制隔离，微调便宜）。

本页本质是把确认从「审文件清单」升级为「审认知」——PM 确认的是「你们对我业务的分层理解对不对」。

**渲染本页之前先跑一次只读预演**——第 8 项动作清单的**文件面取自它的输出，逐字贴，不手写**：

```bash
# A 路径（新建）——与 §5 真跑那条同形，只多一个 --print-plan
python "<CORE>/scripts/project_scaffold.py" --project "<目标>" --params "<params.json>" \
    --create-missing --require-empty --write-subscription --print-plan

# B 路径（接入已有项目）——不带 --require-empty
python "<CORE>/scripts/project_scaffold.py" --project "<目标>" --params "<params.json>" \
    --write-subscription --print-plan
```

**`<params.json>` 必须写在目标目录之外**（系统临时目录即可）——与 §5 步骤 1 真跑那两条用的是
同一份参数文件，理由与踩到之后的后果（A 路径退 3、照那段报错处置会错走接入流程）见那里。
本页这一步比 §5 早，所以规则在这里先说一次。

它按同一份 params、同一个目标目录算出本次将要建 / 改的**每一个文件**（含**项目外**的用户级
市场注册表），分四格打印：`项目内·新建` / `项目内·改已有` / `项目外·改已有` /
`已存在·本脚本不覆盖`。**第四格的判据是「已存在 ⇒ 本脚本一律跳过」**，不是「这次没动」——
前三格里也有「本次不改」的行（项目 settings、用户级注册表在已是目标状态时），
别把「不在第四格」读成「会被改」。用户自己写的 `CLAUDE.md` / `AGENTS.md` 就在第四格，
**逐项报名字**（框架自己的骨架文件折叠成一行计数）。**它只断言「本脚本不动它」**，
**谁在之后按合并稿写回它们不归脚本管**（B 路径里那是你，见 §5 步骤 1.5）——那发生在
预演之外、预演跑完之后，脚本看不到，所以要你自己补那一句。
上面这两条命令**都带 `--params`**；不带 `--params` 跑时对话产物层整支不参与预演，
`CLAUDE.md` 一格都不进（按本页给的命令走就不会撞上）。
**一个字节都不写，目标目录也不创建**，所以在确认之前跑它是安全的。
预演与真跑走**同一套 preflight**：params 不合法、目标非空这类情况它**同样拒、同样的退出码**
（语义见 §5 步骤 1），此时按那里的处置办——不要带着一份跑不通的计划去让用户确认。

**「逐字贴」只管文件面那四格。** 动作清单里还有一行**不在脚本输出里**——装 Codex 门那一步
（它改用户级 `config.toml` 并代用户信任本插件的全部 hook）。预演只预演骨架脚本，算不到它，
**那一行要你自己加**，判据与措辞见 `reference/proposal-page.md` 第 8 项第三条展开。

**能力边界**：这是把「凭记忆复述清单」换成「跑一条命令并转述」，**降低失败概率，不是一道闸**——
确认页是终端文本，机器看不见它渲染了什么，跑没跑、贴没贴都没有任何机器面看得到。

确认用 AskUserQuestion，选项：`按此执行` / `我要调整`（用户用自然语句说改什么，迭代到满意）/ `取消`。

## §5 执行（确认后一气呵成）

先按 §依赖方向 拿到 `CORE` 路径，再依次执行。

**1. 写参数文件并落骨架**

把对话采集的值写成 JSON（必填 `project_name` / `one_line_goal` / `business_context`；可选 `project_type` / `dormant_profile` / `role_profile` / `project_level_roles` / `project_specific_constraints` / `business_directories` / `close_check`；**订阅块 `subscription` 见步骤 2**），交给脚本确定性渲染——占位符替换由脚本保证并断言零残留，不要手工替换模板：

```bash
# A 路径（新建）——必须带 --require-empty
python "<CORE>/scripts/project_scaffold.py" --project "<目标>" --params "<params.json>" \
    --create-missing --require-empty --write-subscription

# B 路径（接入已有项目）——不带 --require-empty
python "<CORE>/scripts/project_scaffold.py" --project "<目标>" --params "<params.json>" \
    --write-subscription
```

**A 路径必须带 `--require-empty`**：它保证目标是不存在或近空的。少了这个参数，用户把项目名
起成跟已有目录同名时，会**静默把一个已有代码仓当成新项目 scaffold**——文件不会被破坏，但
接入流程（存量分析 + CLAUDE.md / AGENTS.md 整合）被整个跳过，用户原文里的项目内容归不了位——
**已经跳过的整合补不回来**（目标已有 CLAUDE.md 时验收会报缺 `@AGENTS.md` 导入，但那时返工成本更高；
目标没有 CLAUDE.md 时模板渲染的导入行在位、验收看不出走错路径）。退出码 3 = 目标非空，此时**改走接入
流程或换个不存在的路径**，不要去掉参数硬来。

**参数文件必须写在目标目录之外**（系统临时目录即可，`tree.json` 同理）。写进目标目录时它自己
就是"实质内容"，A 路径会被 `--require-empty` 判为目标非空退出 3——而那条报错说的是「目标已有
内容」，照上一段的处置就会**错走接入流程**，把新建项目专属的存量分析与 CLAUDE.md 整合整个跳过。

**自由文本参数（目标 / 业务背景 / 项目级角色 / 项目特殊约束 / 业务目录）里别出现 `@` 顶格、也别出现 `@<路径>`**：
它们原样渲染进 AGENTS.md，而 `@<名字>` 在那里是导入语法，脚本 preflight 会直接拒绝（退出码 1、
不写任何文件）。**两条各拒一次**：①任一行以 `@` 顶格；②任一行出现路径形的 `@<路径>`（带 `/` 或
带文件扩展名）——CC 把它当导入处理的位置是**行内任意处**，不只是行首，所以「挪到行中」不是修法。
改写那一行、把这个符号连同路径一起去掉（例如「设计角色 designer：负责视觉」「约定见 docs/rules.md」），
不要删用户写的内容。

非零退出即停下报错，不要跳过继续。退出码语义：**3** = 目标非空（见下段处置）；**1** = 模板文件缺失（插件安装不完整，stdout 会列出缺哪些）——这时重装 core 插件再重跑，别在半成品骨架上继续初始化。

**`close_check` 与其他可选键不同，别当普通字段填**：它默认开启（脚本自带默认值，装机即写进 `.workframe-config.json` 并落盘 `projects/dev-log.md`），所以**正常情况下不要在 params 里写它**。只有用户明确说不想记账时，才传空壳 `"close_check": {}` ——那是装机期的关闭路径，会让收口检查的账本项整项跳过。**关掉要用空壳，不能靠不传或删键**：脚本按「本地缺失才写」合并配置，缺了它照样写回默认值。

**1.2 建模块树（结构闸确认过模块树时）**

把确认页拍定的树写成 JSON（schema 见脚本 docstring：basics[] → subs[]，各带
name / owner / positioning / code_paths），交给脚本确定性落盘：

```bash
python "<CORE>/scripts/module_init.py" --project "<目标>" --params "<tree.json>"
```

脚本承包骨架复制、占位符零残留断言、positioning 写入、code_paths 与反向索引、
两层索引段重建，且幂等（重跑安全）。退出码 2 = 命名 / 参数校验失败——转述 stderr、
修正后重跑，不要手工补文件。**只建 basic / sub 两层**：需求资产包是整理归档阶段的产物。

**1.5 写入合并后的 CLAUDE.md 与 AGENTS.md（仅 B 路径：目标已有 CLAUDE.md 或 AGENTS.md 时）**

scaffold 见到已存在的 CLAUDE.md / AGENTS.md 会跳过——**它跳过的这一份，由你按 §3 产出的合并稿写回**
（目标没有 AGENTS.md 时 scaffold 刚按对话参数渲染了一份，合并稿里的 AGENTS.md 以它为底）。
写盘前按 `<CORE>/reference/claude-md-merge-guide.md` §第 4 步做备份判断（两条确定性命令，
不是凭感觉；以 CLAUDE.md 为例，已存在的 AGENTS.md 同样逐个判）：

```bash
cd "<目标>"
git ls-files --error-unmatch CLAUDE.md   # 被跟踪吗
git status --porcelain CLAUDE.md          # 输出空才算干净
```

两条都过 → 免备份（git 里有原文）；任一不过 → 先写 `logs/CLAUDE.md.bak-<YYYYMMDD-HHmmss>`。
写完在响应里说清备份去哪了，或为什么没备份、怎么用 git 回退。

A 路径（新建项目）没有这一步——scaffold 已按模板渲染好。

**2. 订阅 core —— 由步骤 1 的脚本一并写入，两项声明缺一不可**

订阅不走 `claude` CLI：那条路只有 CC 门有，纯 Codex 的机器上根本没有这个命令。改为在
`params.json` 里给一个 `subscription` 块，由步骤 1 的 `--write-subscription` 一次写好：

```json
"subscription": {
  "marketplace_name": "<定位脚本输出的 MARKET>",
  "source":           {"source": "directory", "path": "<SOURCE>"},
  "install_location": "<CORE 去掉末尾的 plugins/core>"
}
```

`source` 是**对象**，按 `KIND` 取形态：`directory` → `{"source":"directory","path":"<SOURCE>"}`；
`github` → `{"source":"github","repo":"<SOURCE>"}`（`owner/repo`）。
**开关与参数块必须成对**：只给一个会在写任何文件之前退 1（那条报错会告诉你缺哪半）。

`source` 里那个值 **取定位脚本输出的 `SOURCE` 字段，不是 `CORE` 也不是 installLocation**（理由见
§依赖方向 末段：后两者是本地缓存路径，写进项目 settings 会让协作者装不上）。
`install_location` 反过来**就是**那个本机落地目录——它只进用户级注册表、不进项目 settings。

脚本写两处，两处的后果不同：

| 写哪儿 | 内容 | 写不成的后果 |
|---|---|---|
| 项目 `.claude/settings.json` | `extraKnownMarketplaces` ＋ `enabledPlugins` | **承重**：core 插件不加载 ⇒ 脚本非零退出，当场停下修 |
| 用户级 `~/.claude/plugins/known_marketplaces.json` | 本市场的注册条目 | 补强：项目级声明已够本机解析 ⇒ 只出 WARNING ＋ 手动补丁，流程继续 |

**只写 `enabledPlugins` 会产出协作者用不了的项目**：没有 `extraKnownMarketplaces`，别人 clone 后
无法解析插件来源——所以两项必须同在，脚本也是一次写两项。
两处都是 **merge 不是覆盖**：先落备份（`settings.json.bak-<时间戳>`）、保留你原有的全部键、
写完回读校验，任一步不对就按备份还原并打出手动补丁。**同名市场已存在且源不同时不覆盖**，
只把冲突念给用户——那种情况下由用户决定用哪个源。

漏了这一步、或要单独补跑时——**补跑是安全的，但「安全」的含义要说准**：骨架那一半
「已有文件一律不覆盖」，订阅这一半**不是**不覆盖、而是 **merge 后整份重写**（你原有的键
一个不动，但文件会被重排成统一格式）；`--write-subscription` 带了「两项声明已是目标状态
就一个字节都不写」的短路，所以**重复补跑零改动、也不会堆备份**：

```bash
python "<CORE>/scripts/project_scaffold.py" --project "<目标>" --params "<params.json>" --write-subscription
```

装了 Claude Code CLI 的机器上，`claude plugin marketplace add <SOURCE> --scope project` ＋
`claude plugin install core@workframe --scope project` 仍然是等价的写法，但**它不是默认路径**，
也不要在纯 Codex 的机器上建议用户去跑它。

**3. git init + 首提交（A 路径默认做，确认页可取消）**

**必须排在 scaffold 与订阅之后**——这样 `.gitignore` 已就位，首提交才不会把
`.workframe/state/` 与 `logs/` 一起提交进去。

```bash
cd "<目标>"
git rev-parse --git-dir >/dev/null 2>&1 || git init
git add -A
git commit -m "chore: 初始化 Workframe 项目骨架"
```

- 目标已经是 git 仓（B 路径接入常见）→ **跳过 init，也不要替用户提交**，他自己的工作区可能有
  未提交改动
- `git commit` 失败（多半是没配 `user.name` / `user.email`）→ **保留 init 不回滚**，在响应里
  告诉用户怎么补完，继续后面的步骤
- 用户在确认页取消了这一项 → 整步跳过

为什么默认做：没有 git，`.gitignore` 是废的、doctor 的可移植性检查直接跳过、
「同事 clone 接入好的项目」这条路径根本不成立。

**4. 逐模块深读与安置（小闸 × N——只对有资料的模块开）**

骨干已完整（树在、订阅在、git 在），现在按模块逐个处理资料——**深读发生在这里，不在粗扫**：

- 每轮取一个有资料的模块：深读该模块名下的文件 → 给出安置 + 原件处置方案
  （处置五条边界与 A 路径外部资料档位变体见 `material-intake` §原件处置）→
  AskUserQuestion 确认 → **当场执行该模块安置**，再进下一个模块
- **粒度弹性**：1-2 份文件的小模块合并进相邻一轮；单轮确认不超过约 10 份文件的决策量
  ——既不一次塞，又不过度分散。无资料的模块直接跳过
- **呈现纪律**：每轮方案一张表结清（文件 / 一句话结论 / 建议去向 / 原件处置），需要展开的
  行在表下另起短段说「为什么」；引用收口、外部源只拷不搬这类通用纪律**首轮讲清，
  后续轮只报例外**，不逐轮重复
- 深读中发现更合理的切分 → 按结构闸口径向用户**提议树微调**（改动只影响相关模块）
- 安置纪律：成品 md 搬入 / 拷入后补最小 frontmatter，**移动后收口三类引用**
  （frontmatter `related` / wikilink / 相对路径资源）；**成品需求类 md 的落点优先
  `requirements/_draft/`（未立项的需求 / 方案）或 `<basic>/shared/`（跨 sub 事实源），
  `others/` 是杂项区、不作成品文档的默认落点**（2026-08-11 走查：两轮归属选择不一致，
  据此收紧）；异构原料进 `<sub>/others/原始资料/<批次>/`（跨 sub 共用进
  `<basic>/shared/assets/原始资料/`，分组进目录不平铺）；**外部源一律拷不搬**
  （material-intake §2.1 红线：源目录只读）
- **大批量（约 >15 份 md）走逃生口**：只安置样板批证明结构可用，其余留给重启后
  `/core:migrate-to-modules` 批量迁移（记入步骤 5 的批次）
- 在线文档链接（飞书 / Notion 等）本地无副本，装机做不了——列入「待你导出」清单，进步骤 5 批次

**4.5 节奏闸（有待整理归档的批次时才出现）**

安置完成后，把剩余重型活摆到桌面上让用户拍节奏。**整理归档** = 把 docx / xls / 截图等
原始资料读懂后整理成正式需求文档（即 requirement-archiving 九段流水线）。
预估**只给客观量**：每批几份文件、含几个需要用户拍板的决策点（如原文里的未决口径）——
**不给时间预估**：模型执行速度与人力经验完全两码事，时间数字只会误导（2026-08-11 用户反馈）。
用 AskUserQuestion：

1. **现在连续做完**（推荐——装完即完整状态）
2. **现在做一部分**（选批次；其余接力）
3. **全部留到重启后**（首个会话主动接起）
4. **暂不做**（只在体检 / 看板可见，随时说「继续初始化」恢复）

同一张卡（或紧接的第二问）把 **git 提交策略**一并拍掉，之后全程沿用不再问：
每批完成自动提交（推荐；A 路径新建项目默认此档）/ 全部完成一次提交 / 用户自己管。
**用户中途挂起剩余批次时，视作当前阶段完成**——主动提议把已产出内容提交（经确认执行，
不强制）；否则「全部完成一次提交」会因挂起让产物无限期滞留未提交（2026-08-11 实测现场）。

盘量超预期时主动建议档位 2（结构 + 样板批当场做）——不硬撑长会话，也不擅自缩水。

**PRD 风格定制提议（条件触发）**：粗扫与逐模块深读中识别到 **≥3 份章节骨架趋同**的存量
需求文档时，节奏闸顺带多问一项——本项目的 PRD 框架（装机已放入项目 skills 目录的 `prd-style/`——新建项目在 `.agents/skills/`、经 `.claude/skills` 链接同样读得到；接入时项目已有 `.claude/skills/` 真目录的就落在那里，
当前为出厂默认版）要不要**按你现有 PRD 风格定制**？选定制 → 作为一个批次执行（当场做或
接力均可）：读 `<CORE>/skills/prd-writer/SKILL.md` §风格定制与萃取照做（样例抽取 → 差异与
冲突表逐条拍 → 过目确认 → 改写项目 prd-style）。不满 3 份或用户选默认 → 不动，一句带过
「以后随时说『按我的风格写 PRD』再定制」。

**4.7 整理归档段（用户选了当场做的批次）**

逐批执行，**SOP 从磁盘读、逐阶段照做**——skill 未加载不等于拿不到，这与 §3 读
material-intake SOP 是同一模式：

| 批次去向 | 照做的 SOP | 解析脚本 |
|---|---|---|
| 异构原料归档 | `<CORE>/skills/requirement-archiving/SKILL.md`（九段 Phase 0-8，含各阶段门禁） | 该 skill `scripts/` 下直接 `python` 跑 |
| 大批量 md 迁移 | `<CORE>/skills/migrate-to-modules/SKILL.md` | — |
| 代码反解 current-state | `<CORE>/skills/code-to-doc/SKILL.md` | — |

纪律：

- 九段的**每个阶段门禁照过**（收料冻结 / 拍板集中问 / 反向对账缺一不可），装机场景不减配
- **每批完成主动汇报 + 按拍板的提交策略提交**，并给用户离场点（「继续下一批还是今天到这？」）
- 中断不怕：阶段产物步步落盘 + 台账逐行记账，任何后续会话说「继续初始化」精确续上
- 收尾跑一次 `python "<CORE>/scripts/check-stale-modules.py" rebuild-index` 补齐 hook 缺席期间的索引

**5. 断点标记——不是最后才写，是每步紧跟着记**

`setup-state.json` 是**为中断准备的**，所以它必须在中断发生**之前**就记下进度。
scaffold 成功时会自己落 `scaffold: ok`（代码保证，不靠你记得）；**从第 2 步起，每完成一步
立刻记一笔**，用 core 的 `mark_setup_step()`：

```bash
python -c "import sys; sys.path.insert(0, r'<CORE>/scripts'); \
from project_scaffold import mark_setup_step; from pathlib import Path; \
mark_setup_step(Path(r'<目标>'), 'subscribe')"
```

失败时把原因一起记下（第三、四个参数）：

```bash
python -c "import sys; sys.path.insert(0, r'<CORE>/scripts'); \
from project_scaffold import mark_setup_step; from pathlib import Path; \
mark_setup_step(Path(r'<目标>'), 'subscribe', 'failed', '订阅声明写入失败')"
```

**四步各自的落笔人**——第三列是套进上面那个 `python -c` 外壳的调用，紧跟在该步做完之后执行：

| step | 什么时候记 | 怎么记 |
|---|---|---|
| `scaffold` | 步骤 1 | **脚本自动落，不用你记** |
| `subscribe` | 步骤 2 的两项声明落盘 | **带 `--write-subscription` 时脚本自动落（成败都落），不用你记**；用 CLI 手工订阅的话仍按第三列写：`mark_setup_step(Path(r'<目标>'), 'subscribe')` |
| `agents_md` | 步骤 1 的 scaffold 跑完、确认目标根下 `AGENTS.md` 已在 | `mark_setup_step(Path(r'<目标>'), 'agents_md')` |
| `acceptance` | 步骤 5.5 验收跑完 | **doctor 自己落笔（含失败原因），不用你记** |

`acceptance` 之所以不由你记：它的语义是「落盘验收做完了」，只能在验收之后写，而验收查的就是
这份文件——让你来记就成了自指，第一次跑必然缺它、必然报一条机制自造的 error。

条件步做了就记：B 路径 `claude_md_merge`；做了 git 的 `git_init`；建了树的 `module_tree`；
安置了资料的 `placement`（全部模块安置完才记 ok）；整理归档段做完的 `transform`。

**有剩余批次时必须记 `pending_work`**——用户在节奏闸拍了接力 / 暂不的批次、未定处置的
资料、待导出的在线文档，不记下来就再没人提起（收尾报告在 gitignore 的 `logs/` 下，
重启后是空白屏）。这是「第二幕」接力链的起点：

```bash
python -c "import sys, json; sys.path.insert(0, r'<CORE>/scripts'); \
from project_scaffold import mark_pending_work; from pathlib import Path; \
mark_pending_work(Path(r'<目标>'), json.loads(r'''<batches JSON>'''), '<一句话安排>')"
```

batches 每项含 `name` / `files`（项目根相对）/ `target_skill` / `pace`——pace 用用户拍的节奏：
`relay`（重启后做→首会话强接力）/ `paused`（暂不做→零打扰，仅体检可见）/
`undecided`（没做处置决策→软提醒 3 会话 + 文件搬走自清除）。
只填资料批次，不填 `CLAUDE.md` / `.gitignore` 这类项目约定文件。
当场全部做完则**不写此键**——doctor 的 init_completeness 直接报「完整落地」。

**5.5 落盘验收（批次落定之后跑——时序是关键）**

```bash
python "<CORE>/scripts/workframe_doctor.py" --project "<目标>" --group install
```

验收放在 pending_work 落定之后，「初始化完整度」读到的才是真实终态——放在安置前会
误报「完整落地」（2026-08-11 实测教训）。

14 项覆盖：旧布局运行态目录（排第一；新建项目恒为通过，从上一版升级、没迁移的项目在这里报 error）、骨架完整性（硬/软两档 + 占位符残留）、**CLAUDE.md 导入与旧副本**（缺 `@AGENTS.md`
导入报 error、框架段落旧副本报 warn）、**AGENTS.md 四契约段**（两扇门共用的项目自有判据落点；「这个项目是什么」只剩框架占位报 warn）、项目 PRD 框架
（prd-style 在位性，缺失仅 info）、**项目 skills 链接形态**（`.claude/skills` 是普通文件或目标不存在的链接报 error，两份各读各的报 warn）、config 三字段、订阅接线（市场解析 + 来源形态）、
旧镜像残留（上一版留在项目里的纪律镜像目录必须已删，报 error）、
`.gitignore` 必需条目、初始化断点、**初始化完整度**（pending_work 批次状态，纯 info）、
可移植性、环境与 hook 活性。

> 向用户报项数时**以实际输出为准，不要照抄本行**。本行到 2026-08-10 一直写「8 项」且漏了
> `claude_md`——新增该检查时没同步这里，而它恰是 B 路径最该被验的一项。清单与代码有两处
> 事实源就会漂，`check_doctor_install_group_contract` 已对账两边。

**此刻还没重启**，所以依赖 hook 的项会显示 `i`「待首个会话后复查」——这是正常的，不要当失败。
重启后由 core 侧自动跑同一组做运行时验收，那时它们才有真结果。

**来源形态是本地目录路径时按路径形态分档提示**——doctor 已经分好了，照它的输出念，别一律说成不可移植：

- **绝对路径**（`C:\...` / `/home/...` / `~/...` / `\\server\share`）→ 报 `!`：该路径只在这台机器上存在，协作者不可自动安装，开源/团队协作场景应改用 git 源
- **盘符相对路径**（`C:` 后面直接跟名字、当中不带斜杠）→ 报 `✗`：**不是**机器局限性的问题，是这个写法本身没有确定含义——它的基准是「该盘符的当前工作目录」，同一份配置换个 cwd 就指向别处。改成完整绝对路径，或项目内的相对写法
- **相对路径含 `..`**（以 `..` 开头指向上一级的那种写法）→ 报 `!`：可能指到项目目录外面，clone 后那一级未必存在。判据是「出现 `..` 即收紧」，中间夹一个 `..` 、规范化后其实还在项目内的写法也会被一并收进来，属有意从严
- **相对路径不含 `..`**（`./framework` 这类，框架仓嵌套在项目内时的形态）→ **不报警**：它进 git、随仓库走，协作者 clone 后同样解析得到
- 除盘符相对路径外，各档都查目录**存在性**，指向的目录不在就报 `✗`——插件确实装不上。盘符那档不查，因为没有确定基准可查

> 「不报警」那档有两处**不检测**的边界：目录是 junction / 符号链接（git 带不走链接目标）、相对路径大小写与磁盘不符（Windows 解析得到、Linux 协作者解析不到）。遇到这两种形态别只信 doctor 的绿。

订阅接线还会查 `enabledPlugins` 里 `core@<市场>` 的那个市场名到底解析不解析得到：项目级 `extraKnownMarketplaces` 有 → 正常；只有用户级注册有 → `!`（本机装得上、协作者装不上）；只差大小写 → `!`（本机可能仍解析得到，大小写敏感的环境不行）；两级都没有 → `✗`（拼错市场名或压根没注册，插件装不上）。两样都缺（scaffold 跑了、订阅那一步没跑或写失败）会出**两条** `✗`，两项声明都要补——补法就是带
`--write-subscription` 重跑步骤 1 那条命令（见 §5 步骤 2）。

退出码 1（有 error）时**当场对话修复**（重试 / 给手工命令），修完才放行——不要把待办写进文件让用户日后自己发现。仅有 `!` warn 时可继续，但要把每条 warn 念给用户听。

**失败要当场记，而不是等流程末尾统一写**——真断在中途时，末尾那次写入根本不会发生。
早期设计正是把它放在全流程最后一次性写：**为中断设计的机制反过来要求流程别中断**，
自我否定（2026-08-10 走查发现）。

> 顺带一个时序问题也随之解决：doctor 的 `setup_state` 项读的就是这份文件。按增量写法，
> 第 5.5 步落盘验收时此前各步已在文件里，它才验得到「装到哪一步」；旧写法下验收跑在写入之前，
> 那一项**永远**只能报「无 setup-state.json」。

## §6 收尾

1. 写 `<目标>/logs/creation-report.md`：基本信息、对话摘要、最终结构、订阅与验收状态、后续建议。**报告落 `logs/` 不落项目根**，避免污染业务目录。
2. **在响应正文里直接输出**关键结论，不依赖用户去打开报告文件：

按有无剩余批次分两种话术——**不给用户「装完了」的错觉是硬要求**：

全部落地（无 pending_work）：

```
✅ 项目「<name>」已初始化并**完整落地**：模块树 <N> 个模块、<M> 份资料已归位<、K 份原料已归档>。

⚠️ 请现在用 Claude Code 打开 <target>（新开一个会话）：
1. core 的 hook 链路与必载纪律需要新会话才激活（装了 Codex 门的话，Codex 那边同样要新会话）
2. **屏幕会是空白的，这是正常的**——hook 的输出进的是 Claude 的上下文，不显示在终端。
   随便说句话（比如「装好了吗」），它会把首个会话的安装验收结果告诉你
3. 想自己确认：说「看板」让它读 `projects/board.yaml` 汇报，或跑
   `python "<CORE>/scripts/workframe_doctor.py" --group install`
```

有剩余批次（记了 pending_work）：

```
✅ 第一幕完成：模块树已建好、<已安置摘要>。

⏭️ 初始化还有**第二幕**（你拍的节奏）：<批次 × 节奏清单，含「待你导出」项>

⚠️ 请重启 Claude Code 打开 <target> 继续：屏幕是空白的（正常），**随便说句话**——
   我会按你拍的节奏接着带你走完第二幕。进度随时可查：说「看板」，或体检里的「初始化完整度」。
```

两种话术后都补一句（告知档，一行带过）：「本项目的 PRD 规范在 `.agents/skills/prd-style/`
（`.claude/skills` 是指向它的链接，两条路径读到同一份文件），写 PRD 按它执行——想按自己习惯改，
直接改这份文件」；本次做过风格定制的项目改说「PRD 规范已按你的风格定制，在 `.agents/skills/prd-style/`」。
接入的项目若本来就有 `.claude/skills/` 真目录，PRD 规范落在那里，话术里的路径照实说。

**Codex 门（探测到 `codex` 可执行文件即作为装机的一部分执行，不再单问一层——它的事前授权在
§4 结构闸那一页的动作清单里，见 `reference/proposal-page.md` 第 8 项第三条展开）**：

```bash
python "<CORE>/scripts/workframe_door.py" --codex --project "<目标>"      # 默认市场源 owner/repo；--marketplace 可覆盖
```

它写用户级 `config.toml`（本插件 hook 的信任 ＋ 项目 trust ＋ 本插件的 `enabled` 改为 `false`，插件从此只在跑过它的项目里加载）与项目 `.codex/`（managed 段段首的项目级启用表 ＋ 角色注册 ＋ roles），写任何东西之前先预检项目 `.codex/config.toml`（段外同名声明这类冲突以 exit 3 停下、零写入），输出**先列**要信任哪几条、要改用户配置里的哪一键，写完**明说**「已代你信任 N 条 hook」；任一步非零退出就当场对话修复（退出码含义见 core 的 `scripts/workframe_door.py` 抬头）。launcher 只是薄壳：定位 core、转述输出，逻辑全在 core。

**这一步不带 `--marketplace`，开发机上要先知道一件事**：本机 Codex 已经以本地目录登记过同名市场、且 Codex 的克隆目录（`<CODEX_HOME>/.tmp/marketplaces/<名>/`）不在时，门注册默认源那一步不会被拒，Codex 会把那条本地登记直接换成默认公开源（`--codex` 在注册之前不读已有登记；克隆目录残留着时则被拒，文案给出路），公开那一份与门的版本不同、且读得出版本时门在装插件之前以非零停下（登记已换）、文案给出路；版本相同时它会装成公开那一份并报就绪。**停下之后登记已是公开源、插件缓存还是原来那一份：这时本机其他已装过本插件的项目里 `--check` 照样全绿（停下的这个新项目本身还没装完，在它里面跑 `--check` 会报问题），而本机任何项目里跑一次 `--upgrade` 都会把全机共用的插件缓存换成公开那一版**——转述时不要建议用户先跑 `--upgrade`，照门给的出路走。转述门的输出时照实说这件事，不要自己改传别的 `--marketplace`（用哪个源由用户定）。

**失败文案里的出路含 `codex plugin marketplace remove` 时**（它会删掉用户 Codex 配置里的那条市场登记，git 源连同它的克隆目录）：**只能在用户本人明确确认之后执行**——先把这条命令和它删什么念给用户，等他明确说执行（或他自己敲）；**你不得自行执行**，也不得为了「对话修复」把它并进别的步骤里顺手跑掉。

**不要写「看启动横幅是否出现 `[<name>]`」**——那句话在终端里看不见（2026-08-10 走查实证：
用户重启后面对空白屏，以为 hook 没跑，实际全部正常）。hook stdout 只进模型上下文。

首次会话由 core 侧自动做运行时验收：`session-start-prep` hook 在 `session_counter == 1` 时
内联跑同一组检查，结果直接打进启动上下文——纯代码确定性，不依赖模型自觉，本 skill 不再介入。

## 质量自检

- [ ] 目录状态按 §1.1 六步判过（根级 .git / 项目清单一票否决容器；拿不准按项目处理）
- [ ] 容器目录下没给出「就地建」「接入当前」，Q3 首选是 `<容器>/<项目名>`
- [ ] Q1/Q2 候选主体来自 Q1 的回答；来自当前目录的至多 1 个且标了推断依据、不是推荐项
- [ ] Q3 候选路径逐个查过存不存在；已存在有内容的没直接拿来当候选
- [ ] A 路径的 scaffold 命令带了 `--require-empty`（B 路径不带）
- [ ] 选择题都出了卡：`AskUserQuestion` 在手时一律走它；工具不在手上时按 §交互纪律 的降级形态出（编号 + 推荐项 + 一句「这需要你定」），**没有把选择题咽回去替用户决定**
- [ ] 确认页在正文完整展示过，且用户明确确认后才开始写盘
- [ ] 动作清单的文件面来自 `--print-plan` 的输出（逐字贴，没手写）：四格齐全、**项目外那一格没被省掉**、第四格里已存在的 `CLAUDE.md` / `AGENTS.md` 逐项在，且它们的合并稿写回与备份去向由你另说了一句（脚本不管那一步）
- [ ] 探测到 `codex` 时，动作清单里有 Codex 门那一行（改用户级 `config.toml` ＋ 代你信任本插件全部 hook ＋ 受信 hook 脱离沙盒执行 ＋ 以后升级时自动补种、不再单问），且**没有写死 hook 条数**
- [ ] core 路径经**两个定位器之一**（CC 的 known_marketplaces / Codex 的 marketplace list ＋ config.toml）拿到，没有探测「跑在哪扇门」，全程无 `../` 向上引用
- [ ] 订阅**两项声明**都落了盘（`--write-subscription` 一次写两项；备份与 merge 结果已念给用户），落盘验收确认两项声明齐全
- [ ] scaffold / doctor 任一非零退出都已停下处理，没有跳过继续
- [ ] 报告落 `logs/`，且关键结论已在响应正文输出
- [ ] 没在任何生成文件里硬编码本机用户名或桌面路径（`--print-plan` 的输出**不在本条射程内**：它是运行时打给你看的，项目外那一行打绝对路径正是为了让你核对改的是哪个文件）
- [ ] 结构闸只拍项目级三件事（树 / 合并稿 / 文件点名），粗扫未逐字深读全文
- [ ] 确认类输出分了档：拍板项完整展示、告知项精简指路；同一概念未逐轮重复解释
- [ ] 逐模块小闸：有资料的模块才开、单轮 ≤ 约 10 份决策量、确认后当场执行该模块安置
- [ ] 模块树经 `module_init.py` 落盘（不手搓树）；安置执行了引用收口；外部源只拷未动；大批量走了逃生口
- [ ] 节奏闸预估只给份数与待拍板决策点数、**没给时间**；git 提交策略一次拍板全程沿用
- [ ] 整理归档段逐阶段照 SOP（门禁不减配），每批完成有汇报与离场点
- [ ] 剩余批次已记 `pending_work`（pace 按用户拍板）；全部落地则未写此键
- [ ] 落盘验收跑在批次落定之后（「初始化完整度」读到真实终态）
- [ ] 收尾话术按有无第二幕分形态，没给「装完了」的错觉

## 与其他 skill 的衔接

| 场景 | 交给谁 |
|---|---|
| 存量资料盘点 / 分流 / 结构推荐 | core `material-intake`（launcher 读其 SOP 照做） |
| basic / sub 建树 | core `module_init.py`（§5 步骤 1.2 直接调） |
| 规范 md 批量搬家 | core `migrate-to-modules`（装机整理归档段读 SOP 照做，或按节奏接力到重启后） |
| 异构原料整理归档 | core `requirement-archiving`（同上，九段门禁装机不减配） |
| 代码反解为现状文档 | core `code-to-doc`（同上） |
| 需求资产包（整理归档产物） | core `module-init`（requirement 模式，项目内执行） |

## 注意事项

- 生成内容不追求一次完美，鼓励用户后续迭代完善。
- 整理归档节奏由用户在节奏闸拍板，不替用户决定；推迟的批次必须记 `pending_work`，
  否则「第二幕」再没人提起——装完 ≠ 完整落地，完整落地 = pending_work 清零。
