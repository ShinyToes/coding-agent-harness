# 待改进清单（Backlog）

> 每个问题一个稳定 ID（`B-xxx`），只增不改号；关闭时必须同时满足：
> ① 验收命令/测试通过；② 本行状态改为 `done` 并写日期；③ [CHANGELOG.md](CHANGELOG.md) 有对应验证记录。
> 优先级：**P0** = 不补就无法支撑「本地 coding agent harness」这个说法；**P1** = 工程质量/长跑稳定性；**P2** = 让项目成为亮点的评测与观测。

## 概览

| 优先级 | 总数 | 已完成 | 进行中 | 待办 |
|---|---|---|---|---|
| P0 | 10 | 10 | 0 | 0 |
| P1 | 29 | 29 | 0 | 0 |
| P2 | 6 | 6 | 0 | 0 |
| **合计** | **46** | **46** | **0** | **0** |

全部关闭。最近八条：B-039 仓库级 bug/重构任务；B-040 失败分类；B-041 成本上限 + Ctrl+C；B-042 长会话压测；B-043 CI 离线自检（初始必须红）；B-044 可选 tiktoken；B-045 msb 降级测试；B-046 wheel 真实安装验证。

---

## P0 —— 不补就撑不起项目描述（10/10）

| ID | 问题 | 落实措施 | 状态 |
|---|---|---|---|
| B-001 | 恢复时 git 漂移提示被 `progress_tail` 覆盖，从未进入 system prompt | 新增 `cfg.drift_notice`，`loop._brief()` 合并进度与警告；补 run 级回归测试 | done 2026-11-04 |
| B-002 | token 预算只算 `content`，漏 tool_calls 的 arguments | 新增 `context.message_tokens()`，分组预算与 `stats.tokens` 均改用它 | done 2026-11-04 |
| B-003 | 主路径从未接过真模型 | 真实 key 跑 3 场景冒烟（bug/feature/edge），修 provider 前缀、重试、mcp extra | done 2026-11-04 |
| B-004 | replay 是伪确定性：S1/S2 来自文件系统且未进事件 | `model.request` 记录 `snapshot_artifact`；`build_context(..., snapshot)`；`replay.rebuild()` 逐字节比对 | done 2026-11-04 |
| B-005 | 工具输出截断后没有回读手段 | 新增 `read_artifact(artifact_id, offset, limit)`，上限 8000 字符 | done 2026-11-04 |
| B-006 | 模型无法维护任务状态（feature list 没有工具入口） | 新增 `update_task` / `progress` 工具，策略 `task.state` 自动放行 | done 2026-11-05 |
| B-007 | 写类崩溃后只能放弃，无法判定是否已执行 | `tool.call` 记 `sha_before/sha_after`，恢复时判三态：已应用补成功结果、未动重跑、未知报错 | done 2026-11-05 |
| B-008 | 策略可绕过：`bash cat .env` 命中只读白名单 | `_sensitive` 递归扫所有参数；新增 `path.read_outside` 默认 deny | done 2026-11-05 |
| B-009 | context_length 超限直接 session error | `ContextOverflow` → 强制 compact → 重试一次 → 仍失败干净收尾 | done 2026-11-05 |
| B-010 | `finish` 无验证门槛 | 改过文件后需成功 bash，或 feature list 全绿；被拒继续循环 | done 2026-11-05 |

---

## P1 —— 工程质量与长跑稳定性（19/19）

| ID | 问题 | 落实措施 | 状态 |
|---|---|---|---|
| B-011 | 无 CI、无类型检查 | GitHub Actions（Windows/Ubuntu）：ruff → mypy → pytest → 离线 demo | done 2026-11-05 |
| B-012 | `sessions` 加过列却无迁移 | `PRAGMA user_version` + `_migrate()` 按缺失列 ALTER；旧库可打开 | done 2026-11-05 |
| B-013 | artifacts 无 GC | `Store.gc()` 扫事件引用，只删无引用且够旧的 blob；`h gc` | done 2026-11-05 |
| B-014 | resume 后 read 缓存丢失 | `tool.result.meta` 记 mtime/size/行段；`rebuild_read_cache()` 恢复 | done 2026-11-05 |
| B-015 | bash 输出无上限、超时不杀进程树 | `Popen` + 截断 + Windows `taskkill /T` / POSIX `killpg` | done 2026-11-05 |
| B-016 | Windows 上 bash 走 cmd.exe，语义未验证 | `sandbox.shell = auto|cmd|pwsh|bash`，显式构造命令行 | done 2026-11-05 |
| B-017 | trace 无 token/cost，成本无法复盘 | trace 加列 + `--json`；新增 `h sessions`、`h cost` | done 2026-11-05 |
| B-018 | `h run` 运行时几乎无输出 | `loop.run(on_event=...)` + CLI 实时一行一事件，`--quiet` 关闭 | done 2026-11-05 |
| B-019 | 审批 `always` 只存内存，resume 后重新问 | `Policy(cfg, events)` 从 `approval.response(always)` 重建白名单 | done 2026-11-05 |
| B-020 | explorer 无测试、无预算 | 补 4 条测试；`MAX_TOOL_CALLS=6`，超限返回部分答案 | done 2026-11-05 |
| B-021 | 压缩摘要调用不写 model.request | `compact()` 先写 `model.request {kind: compact}` 再调模型 | done 2026-11-05 |
| B-022 | 无 logging | `harness/log.py`，`HARNESS_LOG` 控制级别，异常同时进事件与 stderr | done 2026-11-05 |
| B-023 | 同 DB 双进程互相干扰 | `session_locks` + `acquire/release_lock`（TTL），`loop` 抢锁失败即报错 | done 2026-11-05 |
| B-030 | litellm 1.108 缺 mcp extra 直接 ModuleNotFoundError | 依赖改为 `litellm[mcp]` 并重新 lock | done 2026-11-04 |
| B-031 | 非瞬时错误也被重试 3 次 | `_is_retryable()` 只放行瞬时故障 | done 2026-11-04 |
| B-032 | prompt 没说宿主 shell，模型用 `$?` 浪费 turn | system prompt 增加平台/shell 说明 | done 2026-11-04 |
| B-033 | rich markup 吞掉代码里的 `ordered[mid]` | `Text` / `markup=False` | done 2026-11-04 |
| B-034 | 小窗口下每轮触发压缩，成本放大 5 倍 | `run.compact_cooldown`（默认 2 turn），压缩 11 → 7 次 | done 2026-11-05 |
| B-035 | `Server disconnected`（500）未重试 | `_is_retryable` 增加 disconnected / internal server error / service unavailable | done 2026-11-05 |
| B-036 | 工具 span 不是 turn span 的子节点（spec 要求 child span） | turn span 改为覆盖「模型调用 + 工具执行」，工具 span 自然成为子节点 | done 2026-11-05 |
| B-037 | 模型接近步数上限时不会收尾，直接耗尽预算 | `run.nudge_at`（默认 0.75）+ 一次性预算提醒事件；测试覆盖 | done 2026-11-06 |
| B-038 | 仓库级任务里模型「代码改对了但从不调用 finish」（45 步 0 次），预算提醒无效 | 最后一次编辑后的第一次成功 bash，在工具结果尾部直接提示 finish；系统提示同步强化 | done 2026-11-06（H1 45 步失败 → 10 步通过；H4 60 步预算内 54 步通过） |
| B-039 | 仓库级任务只有「加功能」，缺 bug 定位与跨文件重构 | 新增 `repo-lock-ttl`（注入 TTL 回归，从失败测试定位）与 `repo-refactor`（迁移实现 + 兼容导出，`__module__` 钉住真迁移） | done 2026-11-06（repo-v7 5/5，两个新任务分别 14 / 6 步通过） |
| B-040 | `passed=false` 把「没写完 / 写对没停 / 测试挂 / 环境错」混在一起，无法统计 | `evals/classify.py` 六类分类 + runner 输出 `category` / `categories`；单测覆盖 | done 2026-11-06（155 tests；`solved_no_finish` 就是 B-038 的来源） |
| B-041 | 只有步数上限，钱和 Ctrl+C 都不可控 | `run.max_cost` / `h run --max-cost`；`KeyboardInterrupt` 落 `session.end(cancelled)` 并释放锁；classify 增加 `cost_budget` / `cancelled` | done 2026-11-06（真实调用 $0.00058 触发上限后立即结束；161 tests） |
| B-042 | 长会话（数千事件）下的写入与上下文构建没有验证 | `tests/test_stress.py`：2103 事件写入/查询 + 2000+ 事件的 `build_context` 预算与耗时 | done 2026-11-06（≈1.14ms/条，1400 条 1.59s；context build < 5s） |
| B-043 | 评测只在本地手动跑，CI 不验证「任务初始必须是红的」 | `evals/run.py --offline`：初始仓库跑验收必须失败；CI 增加该步骤 | done 2026-11-06（本地 9/9 红） |
| B-044 | token 估算只有 `chars/ratio`，对代码/中文偏差大 | `harness/tokens.py`：可选 tiktoken，未安装回退启发式；精确后端跳过校准 | done 2026-11-06（`uv sync --extra tokenizer`；3 个单测） |
| B-045 | msb 降级路径只有实现，没有测试钉住 | 两个测试：msb 不在 PATH / 启动失败（无 WHP）都降级到 LocalSandbox、记 error 事件、降级后可执行 | done 2026-11-06（test_sandbox 9 passed） |
| B-046 | 打包只做过「解包 + PYTHONPATH」验证 | `uv build --out-dir .dist-out` + 全新 venv 安装 wheel，项目外验证 import / `h --help` / `h sessions` / 离线 demo | done 2026-11-06（`--no-cache` 绕过被锁的 uv 缓存） |

---

## P2 —— 让它成为简历亮点（5/6）

| ID | 问题 | 落实措施 | 状态 |
|---|---|---|---|
| B-024 | 没有评测集 | `evals/run.py` + 4 任务（bug/feature/edge/long-context），独立 check.py 验收 | done 2026-11-05 |
| B-025 | 没有消融数据 | `--no-cache / --no-compact` + 实测表（结论含反例：压缩省 token 不省钱） | done 2026-11-05 |
| B-026 | 审计只跑到内存 exporter | OTLP 导出到真实后端，验证 turn/tool span 树与 session 归组 | done 2026-11-05（本地 Langfuse v3 实测：5 traces、`turn → tool` 父子、session 归组、usage 正确；镜像走 daocloud，S3 用 SeaweedFS，见 `deploy/langfuse/` 与 `docs/evidence/otlp-verification.md`） |
| B-027 | 无打包发布 | `uv build` 出 wheel/sdist；解包校验 entry point 与 `python -m harness --help` | done 2026-11-05（uvx 受沙箱下载限制未跑通，改用解包 + PYTHONPATH 验证） |
| B-028 | 缺 ADR 与 spec 偏差表 | `docs/ADR.md`：8 条决策 + 10 条 spec 偏差 | done 2026-11-05 |
| B-029 | 只有 explorer，没有 reviewer | `tools/task.py` 加 `reviewer`（读 diff + 风险清单），复用只读循环，不动主循环 | done 2026-11-05 |

---

## 关闭与评审节奏

- **关闭一条**：跑验收命令 → 改状态为 `done + 日期` → 在 CHANGELOG 当前日期写「改了什么 / 为什么 / 验证」。
- **新增一条**：发现即登记，不带着「我知道但没记」的隐性债务；ID 顺序递增，不重用。
- **每周五**：清点一次。P0 未清空不得发版；P1 每两周至少清 3 条；P2 按里程碑推进。
- **发版前 DoD**：P0 = 0；`pytest` 全绿；`ruff` 全绿；`mypy` 全绿；CHANGELOG 归档；README 与代码行为一致；demo 可离线跑通。
