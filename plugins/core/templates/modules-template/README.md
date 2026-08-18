# modules/ 体系骨架模板

本目录是 `module-init` / `migrate-to-modules` / `project_scaffold.py` 共用的模板池。结构对应 `reference/module-architecture.md` 的 `projects/modules/` 树。

## 目录结构

```
modules-template/
├── README.md                                    # 本文件（不复制到目标项目）
├── overview-template.md                         # 全局 modules/overview.md
├── basic-module/                                # 基础模块骨架（复制为 <basic>/）
│   ├── module.yaml
│   ├── overview.md
│   └── shared/.gitkeep
├── sub-module/                                  # 子模块骨架（复制为 <sub>/）
│   ├── submodule.yaml
│   ├── overview.md
│   ├── current-state/{architecture,api-surface,data-model,code-map}.md
│   ├── requirements/overview.md
│   ├── requirements/_draft/.gitkeep
│   ├── decisions/.gitkeep
│   ├── research/.gitkeep
│   └── others/.gitkeep
└── requirement/                                 # 需求资产包骨架（复制为 <req_slug>/）
    ├── meta.yaml
    ├── overview.md
    └── main/                                    # 默认子需求目录；用户可在 module-init 时改名
        ├── prd.md
        ├── assets/.gitkeep                      # 流程图 PNG 等 PRD 附件
        ├── prototypes/.gitkeep
        ├── test-cases/.gitkeep
        └── reviews/.gitkeep
```

## 占位符约定

模板内统一用 `{{XXX}}` 双花括号占位符，由 module-init / `project_scaffold.py` 在生成时替换。常见占位符：

| 占位符 | 含义 |
|---|---|
| `{{PROJECT_NAME}}` | 项目名 |
| `{{BASIC_NAME}}` | 基础模块名（路径段名 + 展示名同源；允许中文 / 英文 / 数字 / 短横线 / 下划线，不能含路径分隔符 `/` `\`、Windows 禁字符 `< > : " \| ? *`、**空格**（含中间空格））|
| `{{SUB_NAME}}` | 子模块名（命名约束同 `{{BASIC_NAME}}`）|
| `{{MODULE_PATH}}` | 二段式 `<basic>/<sub>`（值为两个 `name` 用 `/` 拼接）|
| `{{REQ_SLUG}}` | 需求 slug（仍用 `slug` 命名——需求级 frontmatter 引用契约 `req_slug:` 在 board.yaml / issues / prd.md / test-cases 等 10+ 处使用，故保留；取值约束与模块 `name` 一致，允许中文）|
| `{{REQ_TITLE}}` | 需求标题（人读名；与 `{{REQ_SLUG}}` 是不同字段，不重复）|
| `{{SUB_REQ_SLUG}}` | 子需求 slug（默认 `main`；用户在 module-init 时可改名；取值约束同 `{{REQ_SLUG}}`，允许中文）|
| `{{TODAY}}` | YYYY-MM-DD |
| `{{NOW_ISO}}` | ISO 8601 timestamp |
| `{{OWNER_ROLE}}` | pm / dev / qa / prompt-eng |

## 复制后必须做

- 替换所有 `{{XXX}}` 占位符（module-init Step 3 自动完成；手动复制时需手工替换）
- 子模块创建后跑 `python "$(cat .claude/workframe-state/plugin-root.txt)/scripts/check-stale-modules.py" init-submodule <basic>/<sub>` 初始化反向索引
- 用 module-index-refresh 同步上级 overview 的机器维护段
- 检查 `module` 字段二段式 `<basic>/<sub>`（basic-module overview 除外，详见各模板 frontmatter 注释）

## 不复制的文件

- `README.md`（本文件，模板说明，不进目标项目）
- `.gitkeep`（仅占位用，module-init / migrate-to-modules 复制后保留以维持目录结构）

## 反模式

- ❌ 直接手写 modules/ 子目录而不调用 module-init（容易漏 frontmatter / overview 三段制 / HTML 注释边界）
- ❌ 修改本目录的模板时不同步更新 reference/module-architecture.md
- ❌ 三层及以上嵌套（modules/ 两层封顶）

## 参考

- skill: `document-norms` §1（归属）/ §2（frontmatter）/ §3（三段制 overview）
- `reference/module-architecture.md`（modules/ 体系完整设计）
- skill: `module-init`（创建模块/子模块/需求时的工作流）
