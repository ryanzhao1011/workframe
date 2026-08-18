# HTML 原型生成规范（能力库）

> **适用条件**：项目 PRD 框架（`.claude/skills/prd-style/SKILL.md` §1）声明启用 HTML
> 原型工序、且 V1 开启时，由 SKILL.md S5 引用。原型生成保留在 prd-writer，截图能力
> 委托给 `screenshot` skill。
>
> **本文件不绑定任何特定的外部文档系统**。PRD 完成后用户是否触发发布、发布到何处（飞书 /
> Notion / Confluence / Wiki 等），由用户在 SKILL.md S6 显式指令，并由项目配备的发布
> skill 接管，本文件不调用。

## 落盘路径

下文所有 `<iteration-dir>` 占位符 = 子需求目录
`projects/modules/<basic>/<sub>/requirements/<req_slug>/<sub_req_slug>`。

PRD 文件与 modules-template `requirement/main/prd.md` 的链接保持一致：HTML 原型固定 `prototypes/index.html`，截图归档固定 `<iteration-dir>/assets/`。

## 两种原型形态

| 形态 | 场景 | 规范位置 |
|------|------|---------|
| **仿真型 demo** | demo 先行：先出可交互 demo 多轮迭代拍板交互，再写 PRD（SKILL.md「demo 先行变体」）| core skill `html-demo` |
| **展示型原型** | 标准路径 S5：PRD 写完后生成功能模块展示页（截图导向） | 本文件 §S5.1-S5.5 |

## 展示型原型的定位：精化原型，不是从零草图

S2 已确认页面骨架（区域布局 + 核心元素），S3 已写出交互细节（toast 文案、按钮禁用态、空 / 错误状态、权限差异、字段约束）。S5 把这两份输入合成为**完整交互级 HTML demo**。

**输入来源**：

- S2 确认的页面骨架
- S3 写出的交互细节

**输出目标**：每个功能模块的 HTML 能可视化展示交互细节，而非静态布局。

---

## S5.1 生成整体交互 HTML

根据 S2 骨架 + S3 交互细节，生成 `index.html`（与 PRD 模板内 `./prototypes/index.html` 链接对齐）。

**保存路径**：`<iteration-dir>/prototypes/index.html`

**HTML 模板结构**：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>{需求名称} - 原型演示</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 p-6">

  <nav class="mb-6 flex gap-3 flex-wrap">
    <a href="#section-{功能A}" class="px-3 py-1 bg-blue-100 text-blue-700 rounded text-sm">{功能A}</a>
    <a href="#section-{功能B}" class="px-3 py-1 bg-blue-100 text-blue-700 rounded text-sm">{功能B}</a>
  </nav>

  <section id="section-{功能A}" class="mb-12 bg-white rounded-xl shadow p-6">
    <h2 class="text-lg font-bold mb-4 text-gray-700 border-b pb-2">{功能A名称}</h2>
    <!-- 模块 UI 内容，使用静态 mock 数据，中文文案 -->
  </section>

  <section id="section-{功能B}" class="mb-12 bg-white rounded-xl shadow p-6">
    <h2 class="text-lg font-bold mb-4 text-gray-700 border-b pb-2">{功能B名称}</h2>
  </section>

</body>
</html>
```

**内容规范**：

- Tailwind CDN 引入，无需安装任何依赖
- 每个功能模块用 `<section id="section-{功能名}">` 包裹，id 唯一且与截图配置一致
- 使用静态 mock 数据，中文 UI 文案，贴近实际业务场景
- 每个 section 视觉上独立完整（截图时不依赖其他 section）
- 不实现真实逻辑，仅展示界面结构和交互状态
- **必须呈现 S3 写出的交互细节**：按钮禁用态、空状态占位、错误 / 警告样式、权限差异、字段约束提示

生成后提示用户：

> 「`index.html` 已生成，请在浏览器打开 `<iteration-dir>/prototypes/index.html` 预览，确认后告知我，我会调用 screenshot skill 截图。」

> **可选（需实时核对交互 / 动效时优先）**：起本地 http server + Claude in Chrome 扩展自查——后台跑 `python -m http.server <port> --directory "<iteration-dir>/prototypes"` → 扩展 `navigate` 到 `http://localhost:<port>/index.html` → `computer screenshot` / `read_page` 实时核对（扩展**无法操作 `file://`**，会报 "browser-internal or unparseable URLs"，必须走 `http://localhost`）。扩展未安装 / 不用 Chrome / 无浏览器权限 / 连接失败时，回退到上面的「用户浏览器打开预览」。

---

## S5.2 准备 screenshot 配置（用户确认 HTML 后）

截图不在 `<iteration-dir>` 内放脚本、不安装依赖——只生成 **screenshot config JSON**，渲染执行统一由 core `screenshot` skill 承担。

生成 `<iteration-dir>/prototypes/screenshot-config.json`，内容根据实际功能模块 id 自动填充：

```json
{
  "source": "<iteration-dir>/prototypes/index.html",
  "task_id": "prd-{sub_req_slug 或 迭代名}-prototype",
  "viewport": { "width": 1440, "height": 900, "deviceScaleFactor": 2 },
  "captures": [
    {
      "name": "section-{功能A}",
      "selector": "#section-{功能A}",
      "wait_ms": 800
    },
    {
      "name": "section-{功能B}",
      "selector": "#section-{功能B}",
      "wait_ms": 200
    }
  ]
}
```

> `<iteration-dir>` 占位符按本文件顶部"落盘路径"展开为实际路径。

**业务交互**（多步流程的原型）通过 `setup_js` 字段提供 raw JS hook，例如：

```json
{
  "name": "step2-upload",
  "setup_js": "await page.evaluate(() => openWizard()); await sleep(500); await page.evaluate(() => goToStep(1)); await sleep(400);"
}
```

`setup_js` 可访问 `page`（puppeteer Page）和 `sleep(ms)`。详见 skill: `screenshot` §5。

---

## S5.3 执行截图（调用 screenshot skill）

screenshot skill 随 core plugin 分发，脚本位于插件目录内（项目 `.claude/skills/` 下没有副本）；插件根从 `plugin-root.txt` 取：

```bash
node "$(cat .claude/workframe-state/plugin-root.txt)/skills/screenshot/scripts/screenshot.js" \
  --config "<iteration-dir>/prototypes/screenshot-config.json"
```

输出：

- PNG 文件 → `tmp/screenshots/<task_id>/section-{功能A}.png` 等
- manifest → `tmp/screenshots/<task_id>/_manifest.json`
- 任务日志 → `.claude/workframe-state/logs/screenshot/<task_id>.json`

> screenshot skill 自动处理：浏览器探测、puppeteer-core 自动安装到 `tmp/screenshot-deps/`、截图后清理控制由调用方决定。

### 长期保留 vs 临时使用

- **图片本地长期引用**（PRD 直接 `![]()` 引用）→ 把 PNG 从 `tmp/screenshots/<task_id>/` 移到 `<iteration-dir>/assets/`：
  ```bash
  mkdir -p "<iteration-dir>/assets"
  cp tmp/screenshots/<task_id>/*.png "<iteration-dir>/assets/"
  ```
- **仅作外部发布插图**（PRD 不引用，发布完即用即清）→ 留在 `tmp/screenshots/<task_id>/`，发布完后**由调用方 / 用户手动清理**（SessionEnd hook 不清理截图，详见 skill: `screenshot` §9 临时产物管理）

按 skill: `document-norms` §6（资源位置）的归属约定。

### 降级预案（环境失败时）

screenshot skill 可能因以下原因失败：

- 找不到 Edge / Chrome 且 puppeteer-core 内置 Chromium 未下载
- npm install puppeteer-core 网络问题
- 浏览器启动失败

**失败处理**：

1. 不反复尝试修复环境
2. 报告一次失败原因，告知用户：「自动截图失败，原因：{错误摘要}。HTML 原型已保存在 `<iteration-dir>/prototypes/index.html`，请在浏览器打开后手动截图，把 PNG 放到 `<iteration-dir>/assets/`（与 `prototypes/`、`prd.md` 平级），后续如需发布到外部文档系统再单独触发对应发布 skill。」
3. 不向任何外部文档系统插入空图 / 错图

> **优先尝试（screenshot 失败时的自动补救）**：若有 Claude in Chrome 扩展，可起本地 http server（`python -m http.server <port> --directory "<iteration-dir>/prototypes"`）→ 扩展 `navigate` 到 `http://localhost:<port>/index.html` → `computer screenshot` 直接出图，移入 `<iteration-dir>/assets/`（扩展无法操作 `file://`，必须走 `http://localhost`）。此路径不可用（扩展未装 / 不用 Chrome / 无权限 / 连接失败）时，仍回退到上面第 2 步的用户手动截图作为最终 fallback。

---

## S5.4 在 PRD 中引用图片

按 skill: `document-norms` §5（链接与资源引用），PRD 用 **Markdown 标准图片语法**引用：

```markdown
### 功能 A

#### 原型示例

![功能 A 原型](assets/section-{功能A}.png)
```

`#### 原型示例` 是约定锚点。当项目配备的发布 skill 支持锚点定位时（如 `feishu-publish` 用 `--selection-by-title "#### 原型示例"`），可把图片定位到外部文档对应章节；不支持锚点的发布 skill 则按 Markdown 顺序整体上传。

---

## S5.5 收尾（不直接发布）

prd-writer **不直接调用任何外部文档系统的图片上传 API**。S5 完成后只需提示用户：

> 「截图已生成在 `<iteration-dir>/assets/`（或 `tmp/screenshots/<task_id>/` 临时区）。」
> 走 PRD 插图分支时补一句「PRD 已引用」；**外部发布分支不要这么说**——那条路径明确不被 PRD 引用，断言引用了会让人误以为文档里已经有图。

是否进一步发布到外部文档系统由用户在 SKILL.md S6 显式触发，并由项目配备的发布 skill 接管。弱提醒文案（如「需要的话，我可以继续把这份文档同步到 {外部系统}」）由调用方按项目配备的发布 skill 自行决定，本 skill 不强制。

---

## 与其他 skill 的关系

| 能力 | 关系 |
|---|---|
| `screenshot` skill | 接管所有 HTML/URL → PNG 渲染逻辑（含浏览器探测、puppeteer 管理、自适应宽度、selector 截图） |
| 项目配备的发布 skill（如 `feishu-publish` / Notion / Confluence 等） | 接管外部文档创建、图片上传、锚点定位、内容写入；prd-writer 不直接调用 |
| `document-norms` skill §5 §6 | 决定 PRD 中图片引用约定 + 资源位置归属（assets / prototypes / tmp）|
| 本 skill（prd-writer） | 专注 PRD 内容生成、原型 HTML 设计、章节预埋 anchor 标题 |
