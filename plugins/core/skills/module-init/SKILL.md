---
name: module-init
description: 创建 modules/ 体系下的基础模块 / 子模块 / 需求资产包。基于 templates/modules-template/ 复制骨架，填充占位符，初始化机器维护索引段，刷新上级 overview，并维护反向索引 code-paths-index.json 段。触发词：新建模块、新建子模块、初始化需求、module-init、新建 PRD 需求骨架。
when_to_use: |
  创建第一个/新的基础模块或子模块时；
  为子模块新建一个需求资产包（含默认子需求 `main/`）时；
  迁移老需求到 modules/ 体系前作为单点工具使用；
  用户主动调用 `/core:module-init` 时。
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# Module Init Skill

## 前置依赖

调用本 skill 前需读 skill: `document-norms` §1（归属矩阵）/ §2（frontmatter 字段）/ §3（三段制 overview + HTML 注释边界）/ §4（索引层级与同步）。

## 定位

modules/ 体系下三类骨架的唯一创建入口：

- **基础模块**（basic-module）= 领域级，10-20 个上限
- **子模块**（sub-module）= 功能级，按需扩展
- **需求资产包**（requirement）= `<sub>/requirements/<req_slug>/` 含 meta.yaml + overview.md + 默认子需求 `main/`（可改名）

**适用范围**：已接入 Workframe 的项目（modules/ 体系默认启用）。

**不适用**：

- 修改/重命名已有模块 → 直接 Edit + 调 `module-index-refresh`
- 一次性大规模迁移 → 调 `migrate-to-modules`
- 仅刷新索引段 → 调 `module-index-refresh`
- 创建 ADR / 调研 / 草稿 → 直接 Write 到对应目录（不需要 module-init）

## 输入

| 模式 | 必需输入（向用户问一次的字段）| 可选输入 |
|---|---|---|
| 创建基础模块 | name | owner / domain_tags |
| 创建子模块 | parent_basic + name | owner / tech_stack / code_paths |
| 创建需求 | parent_basic + parent_sub + name | owner / sub_req_name（默认 `main`；用户可改名作为首个子需求目录） |

`name` 命名约定（单字段承担路径段名与展示名，权威定义见插件根 `reference/module-architecture.md` §5.1）：

- **允许字符**：英文 / 数字 / 短横线 / 下划线 / 中文。例：`profile` / `用户档案` / `avatar-cropper` / `编辑资料`
- **3 条 OS 硬约束**（避不开）：
  - 不能含路径分隔符 `/` `\`
  - 不能含 Windows 禁字符 `< > : " | ? *`
  - 不能含空格（含中间空格——stale 索引按空白切分，带空格的名字会被截断）
- **推荐**英文短词（跨语言团队 + GitHub Pages 静态化 URL 友好），但中文也允许（subprocess / hooks 已统一 utf-8 处理）
- **内部存储**：`name` 直接填到 yaml schema 的 `name` 字段（无 `display_name` / `slug` 二字段）。模板里 basic-module / sub-module 用 `{{BASIC_NAME}}` / `{{SUB_NAME}}` 占位符，**需求资产包**仍保留 `slug` + `title`（不合并；`{{REQ_SLUG}}` + `{{REQ_TITLE}}` 占位符不动；理由见 module-architecture §5.2）；**子需求**用 `{{SUB_REQ_SLUG}}` 占位符（取值约束同 `{{REQ_SLUG}}`，允许中文）
- **二段式 module path**（用于跨文档引用 / issues `module:` / board.yaml `module:`）：两个 `name` 用 `/` 拼接，例 `profile/edit` 或 `用户档案/编辑资料`

## 对话流总则（避免反复问 / 误判 / 工程师腔）

1. **资产类型必须在第一步明确**：调用入口（`/core:module-init` 后用户的自然语言意图，或参数携带）必须能解析出"创建基础模块 / 子模块 / 需求资产包"三选一。无法解析时**只问这一个问题问清，不问其他字段直到资产类型定下来**。一旦确定，**不再二次确认**。
2. **前提校验**（仅在 Step 1 用于决定是否拒绝调用）：
   - `.workframe-config.json` 存在即视为已接入，可继续
   - 不存在 → 拒绝并提示"当前目录不是 Workframe 项目，先用 `/workframe-launcher:setup` 接入"
   - **不要**看 CLAUDE.md 是否存在（接入已有项目时不保证生成 CLAUDE.md）
   - **不校验 `project_type` 取值**——modules/ 体系对所有 Workframe 项目默认启用
3. **modules/ 体系开启检测**（决定 Step 1 走"启用 + 建首个"还是"在已有体系下建"分支）：
   - 已开启：`projects/modules/overview.md` 存在
   - 未开启：上述文件不存在
   - **不要**用 `projects/specs/` 是否存在判断"老需求多 → 建议 migrate"——项目骨架（project_scaffold.py）默认会建空 `projects/specs/overview.md`，这不是"有老需求"信号
   - **正确判定"specs/ 含真需求"**：`projects/specs/` 下 `**/*.md` 与 `**/*.yaml` 文件总数 ≥ 2（即除 `overview.md` 之外还有 ≥1 文件）才提示 migrate 选项
4. **命名只问一次**：用 §输入 的 `name` 单字段询问（yaml 只有 name 一个名字字段）。直接用普通对话提问"这个模块叫什么名字？"，**不要用 AskUserQuestion 多选组件硬编码候选名（如 user/demo/content/auth）误导用户**——除非候选基于本项目实际上下文（如 .workframe-config.json `project_name` / 已有模块的语义类比）合理推荐，否则一律开放输入
5. **大白话**：禁止在对话里出现 `slug` / `display_name` / `正则 ^[a-z]...` 等术语（基础/子模块层无此二字段；需求层 `req_slug` 仅作为内部 frontmatter 引用契约存在，不必跟用户提）。规则失败时给具体反例（如"不能含 `/`，输入 `foo/bar` 不允许"），不要贴正则
6. **报告末尾不输出盲目警告**：通过规则 2 的前提校验后，**不要**再在报告尾部提示"本项目可能不该启用 modules/"一类的话
7. **询问 name 的文案分层**（让 PM 一眼看到"现在该回什么"，不被可选项淹没）：
   - **核心问题独占一行 + 视觉突出**（粗体或 ❓ 起头）：例 `**这个子模块叫什么名字？**`
   - **命名约束折叠**到 `>` 引用块的小字注释，不跟核心问题混段
   - **智能默认前置**（如果适用）：例 `父基础模块默认为 用户档案（当前唯一）`，让 PM 知道这一项不必管
   - **可选项明确标注"可跳过 / 留空用默认"**，避免 PM 误以为必答
   - **基础模块标准文案模板**：
     ```
     **这个基础模块叫什么名字？**

     > 英文 / 数字 / 短横线 / 下划线 / 中文都可（如 `profile` / `用户档案` / `avatar-cropper`）；不能含 `/` `\` `< > : " | ? *` 与空格（含中间）。

     可选（不答即用默认）：
     - owner（默认 `pm`）
     - domain_tags（默认空）
     ```
   - **子模块标准文案模板**（单基础模块时）：
     ```
     **这个子模块叫什么名字？**

     > 命名约束同基础模块（英文 / 数字 / 短横线 / 下划线 / 中文；不含 `/` `\` `< > : " | ? *`；不含空格）。

     父基础模块默认为 `用户档案`（当前唯一），可不答。

     可选（不答即用默认）：
     - owner（默认 `pm`）
     - tech_stack / code_paths（留空我会跳过反向索引初始化，可后填）
     ```
   - **需求标准文案模板**（注意：需求目录不表达版本，默认在 `<req_slug>/` 下建一个子需求目录 `main/`；不强问 `sub_req_name`，事后想改名直接重命名目录即可）：
     ```
     **这个需求叫什么名字？**（用作目录名 + 跨文档引用标识）

     > 命名约束同模块（英文 / 数字 / 短横线 / 下划线 / 中文；不含 `/` `\` `< > : " | ? *`；不含空格）。
     >
     > **两个占位符都从这一个答案派生，不再单独问**：`{{REQ_SLUG}}` = 目录名（原样）；
     > `{{REQ_TITLE}}` = 展示标题——**默认等于目录名**，仅当用户答的是英文 slug
     > （如 `avatar-cropper`）时才顺带问一句中文标题，中文名直接两处同值。
     > 骨架里两个占位符都必须替换，留一个没换会在 PRD 标题里露出 `{{REQ_TITLE}}`。

     可选（不答即用默认）：
     - owner（默认 `pm`）
     - sub_req_name（默认 `main`；想给首个子需求另起名时填，例如 `phase-1`、`核心流程`；后续拆子需求直接新建即可，不必现在决定）
     ```

## 工作流

### Step 1: 输入校验 + 路径解析

按 §对话流总则 第 2-3 条做项目类型 + modules/ 开启状态检测，然后分支：

- **未接入 Workframe**（无 `.workframe-config.json`）：拒绝调用，提示先跑 `/workframe-launcher:setup`
- **modules/ 体系未开启**（按总则第 3 条）：进一步看 specs/
  - specs/ **没**真需求（按总则第 3 条判定）：默认进入"启用 modules/ 并创建首个基础模块"流程，**直接走资产类型 = basic-module**，不再问"哪类资产"
  - specs/ **有**真需求（≥2 文件）：提示用户"已有 X 个 specs/ 老需求，走 `/core:migrate-to-modules` 一次性迁移更高效；本次仍要建独立模块吗？"
- **modules/ 体系已开启**：按总则第 1 条问资产类型（basic-module / sub-module / requirement 三选一），**不要**默认走 basic-module
- 验证 `name` 命名（按 §输入 命名约定 3 条 OS 硬约束）
- 验证父级存在（创建子模块时父基础模块必须存在；创建需求时子模块必须存在）
- 检查目标路径不冲突（同 `name` 已存在时拒绝并提示已有路径）

### Step 2: 落盘（basic / sub 模式 → 调脚本；requirement 模式 → 模型执行）

#### 2a. 基础模块 / 子模块：调 `module_init.py` 确定性落盘

把采集到的值写成树 JSON（schema 见脚本 docstring；单建一个 basic 或 sub 也是同一结构），交给脚本一次完成：

```bash
# 插件根从 plugin-root.txt 取（SessionStart hook 每会话刷新），不依赖环境变量
python "$(cat .claude/workframe-state/plugin-root.txt)/scripts/module_init.py" --project "<项目根>" --params "<tree.json>"
```

脚本承包：骨架复制 + 占位符替换（**断言零残留**）+ `positioning` 写入定位段 + `code_paths` 写入 submodule.yaml + 反向索引初始化（内部调 `check-stale-modules.py init-submodule`，复用其文件锁与原子写）+ global `basic-modules-index` 与 basic `submodules-index` 两层索引段重建。**幂等**：已存在文件一律跳过，重跑索引无变化。

- 退出码 2 = 参数 / 命名校验失败，把 stderr 报错原样转述给用户（不要自行猜测修正）
- 退出码非 0 时不要手工补文件——修正输入后重跑脚本
- 脚本 stderr 的 ⚠ 警告（如 code_paths 与文件现值不一致）必须转述，不吞

#### 2b. 需求资产包：模型按下列步骤执行（暂不脚本化）

1. 从 `<插件根>/templates/modules-template/requirement/`（插件根 = `cat .claude/workframe-state/plugin-root.txt`）用 Read + Write 逐文件复制 → `<sub>/requirements/<req_slug>/`（含 `meta.yaml` + `overview.md` + `main/` 子目录及其下文件），保持 .gitkeep
2. 用户给定的 `sub_req_name` 不是默认值 `main` 时，把 `<req_slug>/main/` 重命名为 `<req_slug>/<sub_req_name>/`
3. 逐文件替换 `{{XXX}}` 占位符（表见 `templates/modules-template/README.md`；`{{SUB_REQ_SLUG}}` 无论目录是否重命名都要替换）。`{{NOW_ISO}}` = ISO-8601 带时区时间戳（格式见 `document-norms` §2.7）；`{{TODAY}}` = `YYYY-MM-DD`
4. 调 `module-index-refresh` skill 同步需求层索引段：`<sub>/overview.md`（requirements-index）+ `<sub>/requirements/overview.md`（requirements-by-status）+ `<req_slug>/overview.md`（sub-requirements-index，初次同步含 `main/` 一行）

### Step 3: frontmatter 校验 + 报告

- 校验所有新建文件的 frontmatter 字段齐全（§2 必填字段；basic/sub 由脚本模板渲染保证，抽查即可）
- `module` 字段二段式（仅子模块和需求）
- 输出报告：
  ```
  ✅ 已创建：
    - projects/modules/<basic>/<sub>/submodule.yaml
    - projects/modules/<basic>/<sub>/overview.md
    - ... (其他文件)

  ✅ 已同步索引段：
    - projects/modules/overview.md
    - projects/modules/<basic>/overview.md

  ⚠️ 待人工填充：
    - submodule.yaml 的 code_paths（项目根相对，如 miniprogram/pages/<sub>/**）
    - submodule.yaml 的 tech_stack（数组对象）

  下一步建议：
    - 配置 code_paths 后跑 `python "$(cat .claude/workframe-state/plugin-root.txt)/scripts/check-stale-modules.py" init-submodule <basic>/<sub>` 触发反向索引重建（或 `... rebuild-index` 全量重建）
    - 调 `code-to-doc` skill 解析现有代码生成 current-state/
  ```

## 输出

- 新建的目录与文件
- 同步刷新的上级 overview 机器维护索引段
- 待人工填充清单（code_paths / tech_stack）
- 下一步建议（reindex / code-to-doc）

## 质量自检

- [ ] 项目类型按 §对话流总则 第 2 条判定（先 .workframe-config.json，不只看 CLAUDE.md）
- [ ] 资产类型在 Step 1 单次确定后不再二次确认（§对话流总则 第 1 条）
- [ ] 用户输入 `name` 只问一次（§对话流总则 第 4 条；yaml 无 slug + display_name 二字段）
- [ ] `name` 符合 §输入 命名约定（3 条 OS 硬约束）
- [ ] 父级存在（创建子模块/需求时）
- [ ] 所有 `{{XXX}}` 占位符已替换（basic / sub 由脚本断言零残留；需求模式手工替换 `{{REQ_SLUG}}` + `{{REQ_TITLE}}` + `{{SUB_REQ_SLUG}}` 后自查；**需求类型相关占位符已废弃**，不再有效）
- [ ] frontmatter `updated` = 当前 ISO 时间
- [ ] `module` 字段二段式（子模块/需求）
- [ ] 上级 overview 机器维护段已重建（basic/sub 两层由脚本收尾自动做；需求层已调 module-index-refresh）
- [ ] 反向索引段已初始化（子模块且 code_paths 非空时——脚本自动，⚠ 警告已转述）
- [ ] HTML 注释边界格式正确（§3.3）
- [ ] 报告末尾未输出"本项目可能不该启用 modules/"的盲目警告（前提校验已合规时；§对话流总则 第 6 条）
- [ ] 询问 `name` 时按 §对话流总则 第 7 条文案模板：核心问题独占一行 + 粗体 / ❓ 突出，命名约束折叠到 `>` 引用块小字，可选项明确标注"不答即用默认"

## 反模式

- ❌ 三层及以上嵌套（`<basic>/<sub>/<sub-sub>/` 拒绝；超出拆基础模块）
- ❌ 创建子模块时跳过父基础模块（必须父级先存在）
- ❌ 跳过上级 overview 同步（`module-index-refresh` 必须调用）
- ❌ 手动编辑机器维护段（HTML 注释 START/END 之间）
- ❌ `code_paths` 写小程序根相对路径（必须项目根相对）
- ❌ 用 module-init 修改已存在模块（应 Edit + module-index-refresh）
- ❌ 在对话流里出现已废字段 `slug` / `display_name`（基础/子模块只有单字段 `name`）；也别因此误用旧模板占位符 `{{BASIC_SLUG}}` / `{{BASIC_DISPLAY_NAME}}` 等（已替换为 `{{BASIC_NAME}}` / `{{SUB_NAME}}`）
- ❌ 用 AskUserQuestion 给硬编码候选名（如 user / demo / content / auth）—— 除非候选基于本项目上下文合理推荐
- ❌ 把 `projects/specs/` 是否存在 当作"已有老需求"信号（项目骨架默认建空 overview.md，不算真需求）
- ❌ 项目类型只看 CLAUDE.md 不读 .workframe-config.json（scaffold 不保证 CLAUDE.md 已渲染）
- ❌ 前提校验已通过还输出"本项目可能不该启用 modules/"盲目警告（只在校验失败时才提示）
- ❌ 已确定要建子模块 / 需求后又重复问"创建哪类资产"（资产类型一旦确定不再二次确认）
- ❌ 询问 `name` 时把核心问题（"叫什么名字？"）和命名约束 + 可选项**混在同一段**——核心问题被淹没，PM 不知道现在该回什么；正确做法见 §对话流总则 第 7 条文案模板
- ❌ 强问 `sub_req_name`——默认建 `main/` 即可，立项前 PM 往往无法判断要不要拆子需求；事后想改名或拆分都好办（直接新建另一个 `<sub_req_slug>/`）
- ❌ 立项时主动建多个子需求目录——除非用户明确说要拆，默认就一个 `main/`；多余的子需求目录是噪音
- ❌ 在需求层 frontmatter / yaml 里写已废除字段（需求版本字段、需求类型枚举不使用；版本演进进 prd.md「变更与决策记录」，目录结构不表达需求类型）

完整反模式见 skill: `document-norms` §10。

## 与其他 skill 的衔接

- **document-norms** §1 §2 §3 §4：归属与字段标准（前置）
- **module_init.py**（core script）：basic/sub 落盘 + 两层索引段 + 反向索引的机械承包方（Step 2a）
- **module-index-refresh**：需求层索引段同步（Step 2b 第 4 步必调）
- **code-to-doc**：创建子模块后建议调用，从代码反向生成 current-state/
- **prd-writer**：创建需求资产包后产出 `<sub_req_slug>/prd.md`（业务方）；默认覆盖 `main/prd.md` 占位符内容
- **migrate-to-modules**：大规模迁移时为内部子例程被调用
- **check-stale-modules.py**（hook script）：反向索引并发安全的最终承担方（由 module_init.py 内部调用）

## 失败模式与降级

| 失败 | 降级 |
|---|---|
| `module_init.py` 退出码 2（参数/命名校验） | 转述 stderr 原文，修正输入后重跑；不手工补文件 |
| `module_init.py` 退出码 1（异常） | 转述报错；确认 `plugin-root.txt` 指向的插件根有效后重试 |
| 反向索引初始化失败（脚本 ⚠ 警告） | 转述警告 + 提示用户事后跑 `python "$(cat .claude/workframe-state/plugin-root.txt)/scripts/check-stale-modules.py" rebuild-index` |
| 需求模式模板复制失败 | 报错 + 不写部分文件；用户重试 |
| 需求模式占位符替换不完全 | 报告未替换的占位符位置，请用户手动填充 |
| 需求层索引同步失败 | 报告"骨架已建但索引未同步"，提示手动 `/core:module-index-refresh` |
