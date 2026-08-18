---
name: screenshot
description: 通用 HTML / 本地 URL 截图工具：读 JSON 配置，逐项打开 → setup → 等待 → 截图 → 输出 PNG 到 tmp/screenshots/<task-id>/。只负责出图——不上传任何平台、不写 spec、不实现业务交互；调用方通过 `setup_js` 提供 raw JS hook 控制业务逻辑。
when_to_use: |
  用户说「截图 / 生成原型截图 / HTML 截图 / 把 Mermaid 渲染成图」时；
  需要把本地 HTML 原型或 Mermaid 图转成 PNG 归档、嵌入文档时。
  也被 prd-writer / 发布器 / 报告类 skill 作为出图步骤调用。
  边界：要的是可交互 demo 而非静态图 → html-demo。
user-invocable: true
effort: low
allowed-tools: [Bash, Read, Write]
---

# Screenshot Skill — 通用 HTML 截图

> **环境依赖**：Node.js 18+ · puppeteer-core（首次运行自动装到 `tmp/screenshot-deps/`）· Edge / Chrome / Chromium（系统已有任一即可，跨平台自动探测）

## 1. 定位

只做**一件事**：把 HTML 或本地 URL 渲染成 PNG。

**做**：

- 启动 headless 浏览器
- 设置 viewport
- 打开 source（本地 HTML / file:// URL）
- 按配置依次执行 `setup_js`（可选 raw JS hook）→ `wait_selector` / `wait_ms` → 截图（全页或 `selector`）
- 自适应宽度（`fit_to_selector` 自动扩 viewport，源自原 flowchart-screenshot 能力）
- 输出 PNG 到 `tmp/screenshots/<task-id>/`

**不做**：

- 不上传任何外部文档系统（属项目配备的发布 skill，如 `feishu-publish`）
- 不生成 PRD/spec / 不写需求文档（属 `prd-writer` / `requirement-analysis`）
- 不长期保留图片——任务结束 `tmp/` 整体清理；调用方需要长期保留则显式移到 `assets/`
- **不实现业务交互 DSL**——不接受 `actions: [openWizard, goToStep:2]` 这类业务步骤抽象。复杂交互让调用方在 `setup_js` 里写 raw JS（可访问 `page` 和 `sleep`）

> **定位澄清（与 Claude in Chrome 互补，勿混）**：本 skill = HTML / URL → **静态 PNG 归档**（headless 出图，供入库 / 发布引用）；交互式页面的**实时调试核对**（边改边看视觉 / 动效 / 交互）走 Claude in Chrome 扩展 + 本地 http server 流程（见 `html-demo` / `prd-writer/html-prototype.md`）。两者互补：前者沉淀静态图，后者驱动实时调试，不互相替代。

## 2. 输入输出契约

### 2.1 配置文件（JSON）

> 示例中 `<iteration-dir>` 占位符 = 调用方的子需求目录（定义见 `prd-writer/html-prototype.md`
> 「落盘路径」）；不经 prd-writer 直接调用时，`source` 填实际 HTML 路径即可。

```json
{
  "source": "<iteration-dir>/prototypes/index.html",
  "task_id": "T-20260429-001",
  "viewport": { "width": 1440, "height": 900, "deviceScaleFactor": 2 },
  "captures": [
    {
      "name": "01-entry",
      "wait_ms": 800
    },
    {
      "name": "02-step1",
      "setup_js": "await page.evaluate(() => openWizard()); await sleep(500);"
    },
    {
      "name": "03-step2-upload",
      "setup_js": "await page.evaluate(() => goToStep(1)); await sleep(400);"
    },
    {
      "name": "00-flowchart",
      "selector": ".mermaid svg",
      "fit_to_selector": ".mermaid svg",
      "wait_selector": ".mermaid svg",
      "wait_timeout": 45000,
      "wait_ms": 2500
    }
  ]
}
```

### 2.2 字段说明

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `source` | string | △ | 默认页面：HTML 文件路径（相对 cwd 或绝对）/ `file://` URL / `http(s)://` URL。与 `captures[].source` **二选一**——两者皆无时该 capture 报错 `既无顶层 config.source，也无 cap.source` |
| `task_id` | string | 否 | 输出子目录名；省略时用 `YYYYMMDD-HHmmss-<8位hash>` |
| `viewport` | object | 否 | 默认 `{width:1440,height:900,deviceScaleFactor:2}` |
| `captures[]` | list | 是 | 每项一张截图。**字段名就是 `captures`**——写成 `items` 之类不报错，静默产出 0 张图 |
| `captures[].name` | string | 是 | 输出文件名（不含 `.png`） |
| `captures[].source` | string | △ | 覆盖顶层 `source`（与上一次不同则重新 goto）。每个 Mermaid 块各渲一个 HTML 时用它，此时顶层 `source` 可省略 |
| `captures[].selector` | string | 否 | CSS 选择器；省略则全页 (`fullPage: true`) |
| `captures[].setup_js` | string | 否 | raw JS（async body），打开页面后、截图前执行；可访问 `page` 和 `sleep(ms)` |
| `captures[].wait_selector` | string | 否 | 截图前等待该 selector 出现（`page.waitForSelector`） |
| `captures[].wait_timeout` | number | 否 | wait_selector 超时（默认 30000ms） |
| `captures[].wait_ms` | number | 否 | 截图前固定等待（默认 0） |
| `captures[].fit_to_selector` | string | 否 | 测量该 selector bounding box 后，扩大 viewport 重新渲染（自适应宽度） |

> `△` = 条件必填：`source` 与 `captures[].source` 至少给一个。

### 2.3 输出

```
tmp/screenshots/<task_id>/
├── 01-entry.png
├── 02-step1.png
├── ...
└── _manifest.json    # 截图清单 + 输出路径列表 + 时间戳
```

返回给调用方的结构（manifest）：

```json
{
  "task_id": "T-20260429-001",
  "started_at": "2026-04-29T16:00:00",
  "ended_at": "2026-04-29T16:00:08",
  "source": "<iteration-dir>/prototypes/index.html",
  "captures": [
    { "name": "01-entry", "path": "tmp/screenshots/T-20260429-001/01-entry.png", "status": "success" },
    { "name": "02-step1", "path": "tmp/screenshots/T-20260429-001/02-step1.png", "status": "success" }
  ],
  "errors": []
}
```

任务日志摘要写入 `.claude/workframe-state/logs/screenshot/<task_id>.json`（schema: `workframe.task-log.v1`）。

## 3. 调用方式

### 3.1 直接命令行

本 skill 随 core plugin 分发，脚本在插件目录内（项目 `.claude/skills/` 下没有副本）；插件根从 `plugin-root.txt` 取：

```bash
node "$(cat .claude/workframe-state/plugin-root.txt)/skills/screenshot/scripts/screenshot.js" --config <path/to/config.json>

# 可选参数：
#   --output-dir <path>    覆盖默认 tmp/screenshots/<task_id>/
#   --task-id <id>         覆盖配置中的 task_id
#   --keep-tmp             历史 flag，screenshot 当前**只生成不清理**——本 skill 永不删除 tmp/，
#                          清理责任完全在调用方（详见 §9 临时产物管理）。该 flag 仅作语义占位，
#                          调用方按 §9 自己实现清理逻辑（如读 WORKFRAME_KEEP_TMP 决定是否跳过清理）
```

### 3.2 从 prd-writer / 项目配备的发布 skill 调用

调用方负责：

1. 准备 `index.html`（prd-writer 已生成到 `<iteration-dir>/prototypes/index.html`）或 Mermaid HTML
2. 写 config JSON（含 `setup_js` 业务交互逻辑）
3. 按 §3.1 的命令执行 `screenshot.js`（插件根从 `plugin-root.txt` 取）
4. 读 manifest 获取 PNG 路径
5. 后续动作（移到 assets / 上传外部文档系统）由调用方完成
6. 任务结束清理 `tmp/screenshots/<task_id>/`（清理责任在调用方；SessionEnd hook 存在但**不清理截图**，调用方必须自己清。详见 §9 临时产物管理）

## 4. 自适应宽度（Mermaid / 大流程图）

`fit_to_selector` 解决 SVG / 大表格被 viewport 截断的问题：

1. 用初始 viewport 渲染
2. 测量目标元素 bounding box
3. 若 `box.width + 80 > viewport.width` 或 `box.height + 80 > viewport.height`，扩大 viewport 重新渲染一次
4. 截图（按 `selector` 截 element 而非全页）

源自原 `flowchart-screenshot.js` 的能力。

## 4A. Mermaid → PNG 标准流程（调用方：prd-writer / 项目配备的发布 skill 等）

把 Markdown 中的 Mermaid 流程图渲染成 PNG 的标准化路径，渲染样式与参数从此统一：

1. 复制 `scripts/mermaid-render-template.html`，替换两个占位符 → 写入 `tmp/mermaid-render-<slug>.html`：
   - `{{TITLE}}`：图标题（显示在 PNG 顶部）
   - `{{MERMAID_SOURCE}}`：Mermaid 源码（注入 JS 模板字符串，源码含反引号或 `${` 时需转义；节点文本含括号时用双引号包裹节点文本）
2. 写 config。**照抄下面这块改名字即可**，参数已定死，不要凭印象重编：

```json
{
  "task_id": "mermaid-<slug>",
  "viewport": { "width": 1500, "height": 900, "deviceScaleFactor": 2 },
  "captures": [
    {
      "name": "流程图-<slug>-导出流程",
      "source": "tmp/mermaid-render-导出流程.html",
      "selector": "#out svg",
      "fit_to_selector": "#out svg",
      "wait_selector": "#out svg",
      "wait_timeout": 45000,
      "wait_ms": 1500
    }
  ]
}
```

   多张图时：每个 Mermaid 块各渲一个 HTML，在 capture 里各自写 `source`（如上），顶层
   `source` 可省略；只有一张图时也可把 `source` 提到顶层、capture 里不写。

   > ⚠️ **顶层数组字段名是 `captures`，不是 `items`/`shots`/`list`。**
   > `screenshot.js` 读的是 `config.captures || []`——**写错字段名不会报错**，
   > 它安安静静地产出 0 张图、返回一个 `captures: []` 的 manifest，
   > 你要到「PNG 怎么没生成」时才回头查。实测踩过一次。
3. 执行 `screenshot.js` → PNG 移入目标 `assets/`（文件名由调用方约定，如 `流程图-{req_slug}-{图名}.png`）→ 清理 tmp 渲染件与 `tmp/screenshots/<task_id>/`
4. 失败降级：报告原因一次，提示用户手工截图（同 §7）

> 模板依赖 mermaid CDN（jsdelivr）；离线环境直接走降级。

## 5. 业务交互的 setup_js 示例

```javascript
// 触发 wizard / 步骤切换
"setup_js": "await page.evaluate(() => openWizard()); await sleep(500);"

// 模拟 AI 填充并跳转
"setup_js": "await page.evaluate(() => { aiParsed = true; if (typeof fillAIData === 'function') fillAIData(); goToStep(2); }); await sleep(500);"

// 等待 ajax + 切换 tab
"setup_js": "await page.waitForSelector('.tab-loaded'); await page.click('#tab-2'); await sleep(300);"
```

`setup_js` 是 **raw JS 字符串**，调用方完全控制。skill 内部用 `new AsyncFunction('page', 'sleep', body)` 执行，可访问 `page`（puppeteer Page 对象）和 `sleep(ms)` 工具函数。

## 6. 浏览器探测顺序（跨平台）

| 平台 | 候选路径 |
|---|---|
| win32 | `C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe` → `C:/Program Files/Google/Chrome/Application/chrome.exe` |
| darwin | `/Applications/Microsoft Edge.app/.../Microsoft Edge` → `/Applications/Google Chrome.app/.../Google Chrome` |
| linux | `/usr/bin/microsoft-edge` → `/usr/bin/google-chrome` → `/usr/bin/chromium` |

任一存在即用其执行 puppeteer-core，不下载 Chromium。

## 7. 失败降级

skill 失败时（环境问题 / 浏览器找不到 / 选择器不匹配 / setup_js 抛错）：

1. 写 `errors[]` 到 manifest
2. 已成功的 PNG 保留在 tmp/，不回滚
3. 整体退出码非 0，调用方决定降级（prd-writer / html-demo 走 html-prototype.md §S5.3 的"降级预案"——告知用户手动截图 + 不向外部文档系统插入空图）

## 8. 与其他能力的关系

| 能力 | 关系 |
|---|---|
| `prd-writer` S5（HTML 原型截图） | 作为调用方；提供 `prototypes/index.html` + config，screenshot 返回 PNG manifest |
| `html-demo`（仿真 demo 关键状态截图） | 作为调用方；`setup_js` 触发状态切换 / 模拟开关后截图，PNG 归档 `assets/` 供 PRD 嵌图 |
| 项目配备的发布 skill（如 `feishu-publish`，Mermaid → 图片） | 作为调用方；本地 Markdown 中的 Mermaid 块由发布 skill 渲染成临时 HTML，调 screenshot 转 PNG，再上传 |
| skill: `document-norms` §6 | 资源位置约束：截图 PNG 默认进 tmp/，调用方决定是否长期化 |
| 报告类 skill | 同样可调用 |

## 9. 临时产物管理（与 tmp/ 规则一致）

**职责边界**：

- 本 skill **只生成产物**，不主动清理 `tmp/screenshots/<task_id>/`
- 清理责任完全在**调用方**：
  - 调用方在确认输出已成功消费（移到 `assets/` / 上传飞书 / 嵌入报告等）后，**立即清理** `tmp/screenshots/<task_id>/`
  - 调用方失败 / 中断时也应清理（除非显式保留现场）
- `WORKFRAME_KEEP_TMP=1` 仅是**调用方约定**：调用方应当读取该环境变量，**为 `1` 时跳过清理**（保留 PNG 便于排障），其他值或未设时执行清理；本 skill 不读取该变量，只负责生成

**反例**：

- ❌ 不要假设 SessionEnd hook 会兜底清理（该 hook 存在、做 events flush / digest / GC，但**不清理 `tmp/screenshots/`**——口径同 document-norms §6.2）
- ❌ 不要在 screenshot.js 内 process.exit 之前 `rm -rf` 输出目录（破坏调用方拿走 PNG 的语义）
- ✅ 调用方典型清理片段（任务收尾时执行）：
  ```bash
  if [[ "$WORKFRAME_KEEP_TMP" != "1" ]]; then
    rm -rf "tmp/screenshots/<task_id>"
  fi
  ```

## 10. 执行清单

- [ ] 调用方准备 source（HTML 文件 / URL）
- [ ] 调用方写 config JSON（含 captures 和必要的 setup_js / wait_selector）
- [ ] 按 §3.1 的命令执行 `screenshot.js`（插件根从 `plugin-root.txt` 取）
- [ ] 检查 manifest 与退出码
- [ ] 调用方按需把 PNG 移到 `assets/` 长期保留 / 上传到外部文档系统
- [ ] 任务结束清理 `tmp/screenshots/<task_id>/`
