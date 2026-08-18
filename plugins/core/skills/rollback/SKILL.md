---
name: rollback
description: 回滚最近一次自动变更（T1/T2）或指定 proposal 的变更；配合备份文件和 rollback 索引
user-invocable: true
disable-model-invocation: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion]
---

# /core:rollback 回滚自动变更

## 用途

用户显式 `/core:rollback` 时执行。**Claude 不会自动调用**。

回滚目标：
- **L1 Librarian 变更**：从 `logs/librarian-snapshots/{YYYY-MM-DD}/{HH-mm-ss}-<role>-MEMORY.md` 还原 MEMORY.md，**同时**从同目录的 `{HH-mm-ss}-<role>-memory-index-entries.json` 还原对应 sidecar entries（避免 MEMORY.md 与 `.claude/workframe-state/memory-index.json` 不一致）
- **L2 self-iteration 变更**：从 `{target-dir}/versions/{YYYYMMDD-HHmmss}-{target-basename}.bak` 还原；若 rollback-index entry 的 `targets` 是数组（多文件 L2 变更），逐一回滚每个 target 的备份文件
- **指定 proposal**：按 rollback-index entry 中的 `targets[]` 还原（这是当时实际执行的候选的 targets，**不含**未执行候选的 target——见 `self-iteration/SKILL.md` 阶段 5 step 7 约束）；若 entry 缺失，降级到读 `projects/proposals/applied/PROP-<id>.yaml` 的 `proposed_change.targets[]`（注意旧版可能多 candidate，需与 `applied_option` 字段交叉确认）

## 输入

- 无参数 → 列出最近可回滚的 10 项，`AskUserQuestion` 让用户选
- `PROP-20260424-001` → 直接回滚该提案
- `last` → 回滚最近一次

## 执行步骤

1. Read `.claude/workframe-state/rollback-index.json`（由 self-iteration 阶段 5 写入 entry），列出候选
   - **v2 格式（多文件 L2 变更）**：`{"id":"RB-<YYYYMMDD-NNN>","proposal_id":"<id>","targets":["<path1>","<path2>"],"backups":["<path1>","<path2>"],"applied_at":"<ISO-8601>","applied_option":"<A|B|C>"}`
   - **legacy 格式（v1 单 target，仍需兼容历史 entry）**：`{"id":...,"proposal_id":...,"target":"<path>","backup":"<path>","applied_at":...,"applied_option":...}`
   - 解析规则：优先读 `targets`/`backups` 数组；若不存在则回退到 `target`/`backup` 单字符串字段并视为单元素列表
2. 若候选为空，降级扫描：
   - `logs/librarian-snapshots/**/*-MEMORY.md` 最近 10 个
   - `projects/proposals/applied/*.yaml` 最近 10 个（反查其 `applied_option` 对应的 target + `{target-dir}/versions/{YYYYMMDD-HHmmss}-{target-basename}.bak` 备份）
3. 用 `AskUserQuestion` 让用户选一项
4. 执行回滚：
   - 读备份文件 → Write 到目标文件（多 target 时逐一执行）
   - **L1 MEMORY 类回滚**：同步还原 sidecar entries（读取与快照同目录的 `{HH-mm-ss}-<role>-memory-index-entries.json`，把其中条目合并回 `.claude/workframe-state/memory-index.json` 的 `entries` 字典，覆盖同 key）
   - 若是 proposal，将其从 `applied/` 移回 `rejected/` 并记录回滚原因
5. 追加 events.jsonl（多 target 时逐一 append）：`{"ts":"<now ISO-8601>","type":"rollback_applied","target":"<path>","source":"<backup>"}`
6. 追加 `projects/changelog.md` 一条 `## <date> Rollback` 记录
7. 提示用户已回滚 + 让其手动 verify 目标文件

## 输出

- 成功：简要确认回滚目标 + 备份出处
- 失败（备份缺失 / 索引损坏）：列出可能原因 + 保留当前状态不变

## 约束

- 破坏性操作：执行前必须用户确认
- 不自动级联回滚（用户想回滚多个要分批确认）
- 不修改 events.jsonl 历史，只 append
- `disable-model-invocation: true` 防止误触发
