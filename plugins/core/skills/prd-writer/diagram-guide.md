# PRD 图件规范（能力库）

> **适用条件**：项目 PRD 框架（`.claude/skills/prd-style/SKILL.md` §1）声明启用图件时，
> 由 SKILL.md S2（结构图草稿）与 S4（落盘渲染）读取。项目未启用图件则本文件整体不适用。
>
> 本文件是图件纪律的**单一实现**——选型、双格式落点、同步纪律、组织纪律都只在这里，
> SKILL 与项目框架只引用不复写。

## 选型

| 条件 | 选型 |
|------|------|
| 单角色流程 / 有分支判断 / 状态流转为主 | `flowchart TD` |
| 2 个及以上参与方，消息传递是表达重点 | `sequenceDiagram` |

选型理由在草稿确认时向用户说明即可，**不写入 PRD 正文**（书写依据类元信息与需求无关）。

## 落点与双格式（全部集中流程章）

- 本需求的**全部流程图**（主流程 + 关键分支）集中放在流程章（项目框架的「业务流程与逻辑」
  章或其对应章），不外置、不"另见其他文件"
- 每张图两段相邻放置，**PNG 截图在上、Mermaid 源码在下**，各加小标题：
  - `**流程图**：` + PNG（`assets/流程图-{req_slug}-{图名}.png`，方便真人查看）
  - `**Mermaid 源码**（留档 / AI 可读，与上图同步维护）：` + 代码块
- **PRD 是流程图唯一事实源**（不建独立 flowchart.md，不留第二份漂移源）
- **同步纪律**：改流程 = 改流程章 Mermaid 块 → 重渲对应 PNG 覆盖同名文件（引用路径不变）

**PNG 产出**：按 skill: `screenshot` §4A「Mermaid → PNG 标准流程」（模板 HTML 注入源码 →
截图 → 移入 `assets/`）；screenshot 不可用时降级提示用户手工截图，PRD 中先嵌好图片路径。

> **config 别凭印象编**：顶层结构是 `{ "task_id", "viewport", "captures": [...] }`——
> 数组字段叫 **`captures`**（不是 `items`）。完整可照抄的 config 块在 screenshot §4A
> 第 2 步，**先去那儿复制再改名字**，不要照着这段描述反推字段。
> 写错字段名 `screenshot.js` 不报错，只静默产出 0 张图。

## 图的组织纪律（实战沉淀，默认遵守）

- **默认一张大图，不主动拆分**；分支用 mermaid 内置控制结构（alt / opt / loop / rect）
  表达，不用分图表达
- **lifeline 控制 4-6 个**：服务类（Storage / Parser / Auth / RateLimit 等）聚合成统一
  Backend，用 message label 标注具体调用（如 `Backend->>Backend: [AttachmentParser]
  解析 PDF`）；LLM 也只用单一 lifeline，用 label 标注调哪个 prompt
- **仅当以下三选一成立才拆图**：单图消息 > 40 条 / 单 alt 分支 > 5 个 / 用户明确要求分开看
- **用 rect rgba 分色块**提升可读性（如 绿=happy / 黄=多轮补全 / 橙=fallback / 红=reject /
  紫=流式 / 蓝=终态）
- **保持高抽象**：校验项细节用「详见对应章节」指向，不在图内罗列；图的职责是全局路径感知，
  不是详细设计
- 用户明确说「X 不画」的就不画
