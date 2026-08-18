# NOTICE

本文件记录 Workframe 中来自第三方的内容、受到启发的设计，以及运行时会引用的外部资源。

Workframe 本体以 MIT 许可发布，见 [LICENSE](./LICENSE)。

---

## 一、改编自第三方的内容

### Andrej Karpathy 的工程纪律

| | |
|---|---|
| 文件 | [`plugins/core/skills/technical-design/reference/engineering-discipline.md`](./plugins/core/skills/technical-design/reference/engineering-discipline.md) |
| 来源 | [multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills)（原 `forrestchang/andrej-karpathy-skills`，仓库已迁移） |
| 上游许可 | 其 README 声明为 MIT |
| 改编内容 | 四条核心原则（先思考再编码 / 简洁优先 / 外科手术式改动 / 目标驱动执行）的框架与命名。正文条目、判定表格、反模式清单、与 workframe 其他规则的关系说明均为重写 |

> **关于上游许可的如实说明**：该仓库**没有 LICENSE 文件**，GitHub 识别的许可证字段为 `null`，MIT 仅出现在其 README 末尾的 `## License` 一节。本处归属依据的就是那两行声明——我们无法引用一份不存在的完整版权文本。

---

## 二、设计上受到启发的项目

以下项目**没有任何代码或文本被复制到本仓库**，但它们在记忆层与自迭代机制的设计上给过启发。列在这里是致谢，不是许可义务。

| 项目 | 许可 | 启发之处 |
|---|---|---|
| [Everything Claude Code (ECC)](https://github.com/affaan-m/ECC) | MIT | 记忆层与持续学习机制的整体思路 |
| [OpenClaw](https://github.com/openclaw/openclaw) | 见其仓库声明 | 自迭代与长期上下文沉淀的设计取向 |

---

## 三、运行时引用的外部资源

这些资源**不随本仓库分发**，但框架的部分功能在运行时会引用它们。列出是为了让你知道自己的机器和产出物会依赖什么。

### 按需安装的包

| 包 | 何时需要 | 装到哪 |
|---|---|---|
| `puppeteer-core` | 截图 / 原型出图（`screenshot` skill） | 首次使用时自动装到项目的 `tmp/screenshot-deps/` |
| `python-docx` `pdfplumber` `pypdfium2` `openpyxl` `xlrd` `Pillow` | 归档 docx / pdf / xls 原始资料（`requirement-archiving` skill） | 需你手动 `pip install` |

### 生成物引用的 CDN

| 资源 | 出现在 | 影响 |
|---|---|---|
| [Tailwind CSS](https://tailwindcss.com/) via `cdn.tailwindcss.com` | `prd-writer` 生成的 HTML 原型 | **离线环境下原型样式不生效** |
| [Mermaid](https://mermaid.js.org/) via `cdn.jsdelivr.net` | `screenshot` 渲染 Mermaid 图时的临时页面 | **离线环境下图渲染不出来** |

> 这两个 CDN 由**你生成的页面**去请求，框架本身不联网。详见 README「几件先知道为好的事」。

---

## 四、Claude 与 Claude Code

Workframe 是一个第三方开源项目，**与 Anthropic 无隶属关系，未获其背书**。
Claude、Claude Code 是 Anthropic PBC 的商标；本项目中对它们的提及仅用于说明兼容性。
