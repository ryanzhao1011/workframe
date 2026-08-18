# Issues 模板

`projects/issues/` 是项目级问题记录的**扁平存放区**。每个 Issue 一个 YAML 文件，命名 `{TYPE}-{seq}.yaml`（例：`BUG-001.yaml` / `SEC-001.yaml`）。

## 设计原则

- **扁平结构**：不按模块/平台/业务线分子目录。归属维度通过 YAML 内字段 `area` / `module` / `component` 表达
- **全局序号**：扫描同类型现有 Issue 最大值 + 1，跨归属维度全局唯一（避免 ID 歧义）
- **横切支持**：SEC / 线上故障常横跨多模块，扁平结构 + `area` 字段（可数组）天然支持
- **非软件项目适配**：`area` 是中性归属字段；内容项目可填平台名、经营项目可填业务线名

## SEC 模板（安全 / 合规问题）

```yaml
id: SEC-001
type: sec
status: open                    # open | in_progress | fixed | wontfix | closed
severity: critical              # critical | high | medium | low

title: "<简述>"
description: |
  <发现路径 / 影响面 / 复现方式>

# 归属字段（按需选用，可留空）
area: ""                        # 中性归属：模块/平台/业务线/客户项目
module: ""                      # ★ 二段式必填（如 profile/edit）；尚未迁入 modules/ 的存量 issue 可单值或留空
component: ""                   # 更细粒度组件，可选

# 关联引用
spec_ref: ""                    # 相关 spec 路径，例 "modules/auth/login/requirements/<req_slug>/<sub_req_slug>/prd.md"
related_task: ""                # 关联的 TASK-ID
source: ""                      # qa | auto-update | user | monitoring | client | <other>

# 需求级叠加字段（需求相关 issue 用）
req_slug: ""                    # 挂需求就填，且与 sub_req_slug 同进同出
sub_req_slug: ""                # 与 req_slug 同进同出；main 也要显式写
affected_modules: []            # 可选，横切 issue 用二段式数组（如 [profile/edit, payment/checkout]）

# 内容字段
preconditions: ""
steps: []
expected: ""
actual: ""
fix_strategy: ""
verified_by: ""

created_at: "<YYYY-MM-DD>"
updated_at: "<YYYY-MM-DD>"
```

## BUG 模板（功能缺陷 / 异常）

```yaml
id: BUG-001
type: bug
status: open                    # open | in_progress | fixed | wontfix | closed
severity: P1                    # P0 | P1 | P2 | P3

title: "<简述>"
description: |
  <详细描述>

# 归属字段（按需选用，可留空）
area: ""
module: ""                      # ★ 二段式必填（如 profile/edit）；尚未迁入 modules/ 的存量 issue 可单值或留空
component: ""

# 关联引用
spec_ref: ""                    # 相关 spec 路径，例 "modules/auth/login/requirements/<req_slug>/<sub_req_slug>/prd.md"
related_task: ""
source: ""                      # qa | auto-update | user | monitoring | client | <other>

# 需求级叠加字段（需求相关 issue 用）
req_slug: ""                    # 挂需求就填，且与 sub_req_slug 同进同出
sub_req_slug: ""                # 与 req_slug 同进同出；main 也要显式写
affected_modules: []            # 可选，横切 issue 用二段式数组

# 内容字段
preconditions: ""
steps: []
expected: ""
actual: ""
root_cause: ""
fix_strategy: ""
verified_by: ""

created_at: "<YYYY-MM-DD>"
updated_at: "<YYYY-MM-DD>"
```

## area / module 取值参考

| 字段 | 示例 |
|---|---|
| area | platform / api / web / backend / frontend / infra |
| module | **二段式** `user-management/profile`、`search/index`、`auth/login` |

**modules/ 归属约束**：

- `module` 必填二段式 `<basic>/<sub>`（如 `profile/edit`）
- 横切多模块的 issue 用 `affected_modules` 数组（如 `[profile/edit, payment/checkout]`）
- 需求级 issue 加 `req_slug` + `sub_req_slug` **两个字段一起**（如 `avatar-cropper` + `main`；`main` 也显式写）。
  「缺省按 `main` 解释」只对**存量**条目成立，是兼容条款不是写法
- 老 issue 不必迁移；新 issue 默认采用新格式（新写入推荐二字段 `req_slug` + `sub_req_slug`）

## 状态流转

```
open ──→ in_progress ──→ fixed ──→ closed
   └────────────────→ wontfix ──→ closed
```

## 关联 skill

- **创建**：`test-case-design`（测试失败）/ `auto-update`（P0 安全/故障）
- **调试读取**：`systematic-debugging` 按 ID 读取 `projects/issues/{issue-id}.yaml`
- **看板关联**：`board.yaml` 的 `notes` 字段引用 Issue ID（例：`"see BUG-001"`）

## 反模式

- ❌ 改成 `projects/issues/<module>/BUG-001.yaml` 子目录组织：会引发 ID 歧义、横切问题难安置、跨模块统计成本上升
- ❌ 自造 `status` / `severity` 取值：枚举固定，扩展请走 self-iteration 提案
