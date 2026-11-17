# 架构决策记录（ADR）

> 每条记录「当时为什么这么选」，避免后来者（或三个月后的自己）把踩过的坑再踩一遍。
> 与 spec 的偏差统一在最后一节列出。

## ADR-001 事件溯源 + SQLite，不引入 ORM / 消息队列

**决策**：`events` 表只追加，`seq` 在 `BEGIN IMMEDIATE` 里 `max(seq)+1`；用触发器禁止 UPDATE/DELETE；artifact 内容寻址落文件。

**理由**：单机单进程，SQLite 的事务、`grep` 能力和零运维足够；ORM 会把「事件 schema 即类型」这层透明性吃掉。触发器把不变量变成数据库级事实，而不是靠代码自觉。

**代价**：手写 SQL / 迁移；并发上限低（已用 session lock + TTL 兜底）。

## ADR-002 上下文是纯函数，S1/S2 用快照记录而不是读文件

**决策**：`build_context(events, cfg, snapshot=None)` 不碰 DB、不读文件；`task_brief`（progress 末尾）和 `repo_map` 由 loop 注入，并在每次 `model.request` 时把这两个值 + `calibration` 存成 `snapshot_artifact`。

**理由**：最早直接读文件导致 replay 重建的是「换一个配置重跑」的请求，而不是模型当时看到的那份。快照让「可复现」从口号变成可断言（`recorded seq=N match`）。

**代价**：每次请求多一个 artifact（内容寻址，重复不占空间）。

## ADR-003 工具输出全量进 artifact，另配 `read_artifact`

**决策**：`tool.result` 只存头尾 400 字 + `chars` + `artifact_id`；模型需要全文时调用 `read_artifact`。

**理由**：截断发生在上下文层是必须的（否则 200k 输出直接炸窗口），但只有引用没有回读通道等于信息丢失。

**代价**：多一次工具调用；artifact 需要 GC（见 ADR-007）。

## ADR-004 策略是有序规则表，决策本身是事件；白名单从事件恢复

**决策**：`RULES` 首个命中生效；`policy.decision` 落库；`always` 白名单在 `Policy(cfg, events)` 初始化时从 `approval.response(decision=always)` 重建。

**理由**：规则表可单测、可复盘；把白名单写进事件而不是内存，resume 后不用重新问，也不引入额外的持久化表。

**代价**：白名单随事件增长，需要按 call_id 反查（线性扫描，session 级别可接受）。

## ADR-005 副作用用内容 hash 判三态，而不是「重试」或「放弃」

**决策**：写类调用执行前记录 `target_path/sha_before/sha_after`；恢复时：文件等于 after → 补成功结果；等于 before → 安全重跑；都不是 → 报 unknown。

**理由**：崩溃点可能落在「已落盘、未记账」之间。保守放弃会浪费一次完整任务，盲目重跑会重复副作用；hash 是唯一能同时避免两者的判据。

**代价**：写大文件时多读一次计算 hash；手工改过目标文件会被判 unknown（这是期望行为）。

## ADR-006 压缩：任务状态不进摘要、冷却、溢出兜底

**决策**：压缩只覆盖最早 60% 事件（保留最近 `turn_keep` 个 turn）；`task.update` 与 `session.start.task` 永远以原文进 S1；两次自动压缩至少隔 `compact_cooldown` 个 turn；provider 报超限时强制压缩并重试一次。

**理由**：摘要会丢细节，任务状态不能丢；评测发现小窗口下每轮压缩会付出 11 次摘要调用（成本 ×5），冷却把它降到 7 次。压缩省上下文但不省钱，窗口应按任务规模配置。

**代价**：压缩后 tokens 下降但成本上升；需要调 `window / compact_at / turn_keep`。

## ADR-007 `finish` 需要证据

**决策**：改过文件后，最后一次改动之后必须有一次成功的 `bash`；或者 feature list 全绿。被拒不终止循环。

**理由**：模型行为不可靠，实测中它会「自觉」跑测试，但不能假设。把验证变成机制，而不是 prompt 里的请求。

**代价**：研究型任务（只读）不受影响；纯文档修改也必须跑一次命令（可关 `run.require_verification`）。

## ADR-008 沙箱：local 默认、msb 可选、shell 显式

**决策**：`LocalSandbox` 用 `Popen` + 显式 shell（`cmd/pwsh/bash/auto`）；输出截断；超时杀进程树。`msb` 不可用时降级并写 `error` 事件。

**理由**：Windows 上没有 WHP 时 msb 起不来，必须有兜底；`shell=True` 的引号行为不可控（`cmd /c "prog" "arg"` 会剥掉首尾引号），显式命令行才可测。

**代价**：跨平台命令语义仍需各平台各自验证（CI 只覆盖 echo 级别）。

---

## 与 spec 的偏差

| 项 | spec | 实现 | 原因 |
|---|---|---|---|
| 工具数 | 8 个 | 14 个（+`read_artifact`/`update_task`/`progress`/`repo_map`/`explorer`/`reviewer`） | 回读通道、任务状态写入、符号概览、只读子 agent 都是 spec 文件里提到但工具表没列的能力 |
| 文件拆分 | 11 个模块 | +`journal.py`/`replay.py`/`log.py` | 单文件 ≤250 行的硬约束 |
| 组装预算 | `window` | `window - max_tokens` | 用 `window*compact_at` 当预算会让压缩永不触发 |
| 压缩冷却 | 无 | `run.compact_cooldown=2` | 评测实测压缩风暴 |
| finish | 直接提交 | 需要 bash 证据或 feature 全绿 | 防止空口完成 |
| 读边界 | 只限制写 | 读也默认限制在 workspace（可开 `allow_read_outside`） | 敏感文件可被 `bash cat` 读走 |
| `tool.result` | 固定字段 | 增加 `meta` / `chars` / `tail` | 回读与缓存需要 |
| `model.request` | 固定字段 | 增加 `snapshot_artifact` | replay 真确定性 |
| 沙箱配置 | `network` | `shell` / `max_output` | 跨平台与输出上限 |
| 事件类型 | 13 种 | 未新增，只加字段 | 保持 replay 兼容 |
