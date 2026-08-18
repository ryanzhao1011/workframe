# J8 subagent 记忆注入三态

- 前置：沙盒 agent-memory 有 shared + 4 角色真实 MEMORY
- 动作：一次会话双探针——
  `claude -p "派 core:dev 与 general-purpose 两个 agent，各自逐字复述「[workframe] 角色记忆注入」标记段第一行、列出包含的 MEMORY 路径、引用任一条目前 30 字，原样转述回来"`
- 断言：
  1. core:dev：MARKER 第一行逐字一致 + 两份路径（shared + dev）+ 引用的条目真实存在于
     dev/MEMORY.md（防幻觉：与文件逐字比对）
  2. general-purpose：仅 shared 一份 + 引用条目真实存在于 shared/MEMORY.md
  3. 两探针零工具调用（纯凭上下文作答）——证明是注入而非自行 Read
- 实测 2026-08-07：✅ 3/3。引用条目分别命中 dev 第一条（2026-04-14 JS 正则）与 shared
  条目（2026-05-14 agent-cc 定位），逐字符核对无误。附带收获：subagent 收尾协议 Step 0
  （咨询类跳过 wrap-up）两探针都正确执行；发现 F6（注入段说明恒写「两份」，对只注入
  shared 的内置 agent 轻微失真——探针自己指出）。
