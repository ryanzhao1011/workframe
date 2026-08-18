---
name: html-demo
description: 仿真型 HTML 交互 demo 生成：按用户截图复刻真实界面，产出自包含、全状态可达（带模拟开关）的可交互单文件 demo，落 tmp/ 就地多轮迭代；拍板后归档 prototypes/ 并衔接 prd-writer「demo 先行变体」写 PRD。
when_to_use: |
  用户说「出个 demo / 做个 demo / 交互方案 / UI 方案 / 交互样式 / 页面方案 / HTML 原型」时；
  写 PRD 前要先用可交互原型对齐交互细节时（demo 先行工作流）。
  边界：只要静态图 → screenshot；要落正式需求文档 → prd-writer。
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# 仿真型 HTML 交互 demo

> 服务「demo 先行」工作流：先出可交互 demo 多轮快速迭代、拍板交互细节，再写 PRD（prd-writer「demo 先行变体」）。
>
> 与 prd-writer S5 的「展示型原型」（PRD 写完后生成、功能模块平铺、截图导向，规范见 `prd-writer/html-prototype.md`）是两种形态，按场景选用：迭代敲定交互 → 本 skill；为已定稿 PRD 配图 → 展示型。

## Workflow

```
Step 1: 输入确认 —— 用户截图（复刻基准，缺则先要）+ 需求一句话
Step 2: 生成 v1 —— 自包含单文件落 tmp/{需求名}-交互方案.html，写完提示用户打开预览
Step 3: 多轮迭代 —— 就地修改唯一一份，每轮改完提示刷新查看；不另存副本、不留版本文件
Step 4: 拍板归档 —— demo 复制到对应需求 prototypes/（删除 tmp/ 原件避免双源）；
        需要正式 PRD 时衔接 prd-writer「demo 先行变体」（交互细节从 demo 代码提取）
（按需）Step 5: 关键状态截图 —— 调 screenshot skill 归档 assets/，PRD 嵌图
```

## 页面调试确认（默认不开浏览器，按判断树决定）

**默认口径：写完直接交付，提示用户刷新查看，由用户人工验收——不开浏览器**（实战沉淀：常规控件盲改命中率足够高，起浏览器的时间成本大于收益）。

**仅以下三种情况开 Claude in Chrome 实时核对**：

| # | 触发 |
|---|---|
| 1 | 用户明确要求看效果，或提出视觉问题（"太丑" / "对不齐" / "显示不对"）——此时必须看着调，不盲改 |
| 2 | demo 含**自定义视觉形态**：图表 / 矩阵热力图 / 时间轴、动效与过渡、复杂布局（多列网格、自适应宽度、浮层定位、拖拽） |
| 3 | 高保真复刻截图是本次交付重点 |

**明确不开**（无论首版还是迭代）：常规表单控件（输入框 / 下拉 / radio / checkbox / 开关 / 按钮 / 普通表格列表）、文案替换、选项增删与排序、默认值调整、删除已有内容。简单需求的首版 demo 同样不开。

**开了之后的操作纪律**：

- 后台跑 `python -m http.server <port> --directory "tmp"` → 扩展 `navigate` 到 `http://localhost:<port>/{需求名}-交互方案.html`（中文文件名需 URL 编码）→ 之后 `computer screenshot` / `read_page` / `javascript_tool` 均正常
- **关键坑**：Claude in Chrome 扩展**无法操作 `file://` 本地页面**（navigate / screenshot / read_page 全被拒，报 "browser-internal or unparseable URLs"），必须走 `http://localhost`
- **server 起一次保持到 demo 拍板**，不每轮起停
- **核对用 `javascript_tool` 直接设状态再截图，不用 `computer` 点击**——扩展的点击 / 滚动会误触发页面事件改乱 demo 状态（实证：选中项莫名跳变，白排查一轮）
- **兜底**：扩展未安装 / 用户不用 Chrome / 无浏览器权限 / 连接失败时，优雅回退到「写完提示用户打开预览、每轮改完提示刷新查看」，由用户截图 / 口头反馈——用户自查路径始终保留为 fallback

## 质量规范（生成与迭代）

- **自包含单文件**：一个 HTML 内联全部 CSS/JS（CDN 可选）；落 `tmp/` **唯一一份**就地迭代
- **复刻真实**：以用户提供的截图为准复刻目标系统的布局与视觉；假数据贴近真实业务（真实风格的文案 / 标签值 / 数据形态）。项目级视觉口径（主题色参考等）由项目 local rule 定义
- **全状态可达**：每个状态（成功 / 失败 / 空 / 加载中 / 禁用）必须能通过**界面操作**到达——为不易自然触发的状态提供**模拟开关**（如「模拟失败」toggle、空态切换），不允许存在"改代码才能看到"的状态
- **mock 标注**：演示性假设（能力映射、概率模拟等）集中为**顶部常量** + 注释「演示用，实际以 {真实来源} 为准」，防止评审者当真
- **代码组织利于反向提取**（衔接 prd-writer demo 先行变体（S3 交互细节提取））：状态机集中管理、配置常量化、关键交互规则带注释——写 PRD 时直接从代码读取状态、文案、禁用与显隐逻辑，不靠回忆对话

## 与 PRD 的分工与同步

- 分工：界面文案、视觉样式、控件布局以 **demo 为准**（PRD 不重复描述视觉细节，引用原型链接）；业务规则、边界、计算口径以 **PRD 为准**（demo 仅演示）
- 同步纪律：交互 / 文案类需求变更时，demo 与 PRD **同轮修改**；仅规则口径变更不动 demo
- 落盘前校验：PRD 落盘前对照 demo 过一遍状态文案与控件行为（字段与交互表、控件×状态矩阵逐行对），不一致即修

## 按需多状态截图

外部评审者（如飞书文档读者）无法打开本地 demo——按评审需要用 screenshot skill 截关键状态图（`setup_js` 触发状态切换 / 模拟开关）归档 `assets/`，PRD「前端交互详情」对应小节嵌图；是否截由用户当次决定，不强制。

## 与其他能力的关系

| 能力 | 关系 |
|---|---|
| `prd-writer`（demo 先行变体） | 下游：本 skill 产出的 demo 是其 S1 输入，交互细节提取（S3）与归档动作（S5）按其 demo 先行变体衔接 |
| `prd-writer/html-prototype.md` | 并列形态：展示型原型（标准路径 S5）的规范 |
| `screenshot` skill | 按需状态截图的执行引擎 |
| `Claude in Chrome` + 本地 http 服务器 | 页面调试确认引擎（交互实时核对；扩展不可用时回退用户自查）——区别于 `screenshot` 的静态归档 PNG |
| 项目 local rule | 项目视觉口径（截图为准 / 主题色参考）、demo 先行偏好声明 |
