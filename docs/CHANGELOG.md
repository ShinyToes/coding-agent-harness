# 变更记录

> 约定：任何代码/文档改动都先在本文件记一条，写清「改了什么 / 为什么 / 怎么验证 / 动了哪些文件」。
> 每条尽量带一条可复现的验证命令，发布时把 `Unreleased` 归档到日期下。
> 待改进项不写在这里，写进 [BACKLOG.md](BACKLOG.md)；两边用 `B-xxx` 互相引用。

## Unreleased

### 2026-11-06 · CI 离线评测、token 精确计数、msb 降级验证、wheel 真实安装

- **CI 离线自检**（B-043）：`evals/run.py --offline` 对每个任务在「未修改的初始仓库」上跑验收，要求**必须红**；某个任务初始就绿说明它没检查新行为，直接让 CI 失败。CI 新增一步 `uv run python evals/run.py --offline`（本地 9/9 红）。
- **token 估算**（B-044）：新增 `harness/tokens.py`，装了 `tiktoken` 就用精确计数，否则回退 `chars/ratio`；`tiktoken` 作为可选 extra（`uv sync --extra tokenizer`），不违反依赖白名单。精确后端下跳过 chars/ratio 校准。
- **msb 降级**（B-045）：补两个测试——`msb` 不在 PATH、以及 `msb` 存在但启动失败（无 WHP）——都必须降级到 `LocalSandbox`、记一条 `error` 事件，且降级后能正常执行命令。
- **wheel 真实安装**（B-046）：`uv build --out-dir .dist-out` 产出 wheel + sdist；在全新 venv 里 `uv pip install --no-cache` 安装后用项目外目录验证：`import harness` 指向 site-packages、`h --help` 正常、`h sessions` 正常、`examples/demo.py` 离线跑通。
- 环境备注：`dist/` 被沙箱写入了 DENY 删除 ACE（`icacls` 也无法修改），因此构建输出改到 `.dist-out/`；`uv` 的共享缓存目录同样被 ACL 锁住，安装时用 `--no-cache`。
- 验证：`pytest .` → 166 passed；mypy 26 文件零问题；ruff 全绿。

### 2026-11-06 · B-041 成本上限 + Ctrl+C；B-042 长会话压测

- **成本上限**：`run.max_cost`（默认 0 = 不限）+ `h run --max-cost`；每步模型调用后检查 `session.cost`，到上限即 `session.end(max_cost)` 并停止执行后续工具。实测一次调用花 $0.00058、上限 $0.00005，会话立即结束。
- **优雅中断**：`KeyboardInterrupt` 被循环捕获，落一条 `session.end(cancelled)`，`finally` 正常释放 session 锁；不再是堆栈直接炸掉。
- **失败分类**扩展：`classify()` 增加 `cost_budget` / `cancelled` 两类，单测覆盖。
- **长会话压测** `tests/test_stress.py`：2103 条事件写入 + 查询（实测 1400 条约 1.59s，≈1.14ms/条），2000+ 事件下 `build_context` 仍在预算内且耗时 < 5s。
- 验证：`pytest .` → 161 passed；mypy/ruff 全绿。

### 2026-11-06 · 评测扩充（bug 修复 / 跨文件重构）+ 失败分类 + 项目文档

- 新增 `repo-lock-ttl`：从真实项目复制后**注入锁 TTL 回归**（`ttl_seconds` 被写成常量 3600），要求模型从失败测试定位并修复；隐藏测试覆盖过期可接管、TTL 内拒绝、同 owner 重入、release。
- 新增 `repo-refactor`：12 文件合成仓库，要求把 `format_price` / `compute_total` 迁到新 `orders/money.py`、更新 service/api 调用、保留 utils 兼容导出；隐藏测试用 `__module__` 钉住「实现真的搬了」。
- `evals/classify.py`：失败分类（pass / solved_no_finish / tests_fail / budget / stopped / error），runner 输出 `category` 与 `categories` 汇总；新增 `tests/test_eval_classify.py`（8 个用例）。
- `docs/PROJECT.md`（一页说明 + mermaid 架构图 + 数据）与 `docs/STORIES.md`（漂移提示、压缩风暴、收尾提示三个 bug 复盘）。
- 实测 `repo-v7`：5 个仓库级任务 **5/5 通过**，863k tokens，$0.031；`repo-edit-lines` 43 步、`repo-sessions-filter` 23 步、`repo-lock-ttl` 14 步、`repo-pagination` 6 步、`repo-refactor` 6 步。
- 验证：`pytest .` → 155 passed；mypy/ruff 全绿。

### 2026-11-06 · B-038 收尾提示（仓库任务 3/3 通过）

- `execute()` 在 bash 成功且「最后一次编辑之后还没有成功验证」时，把一句提示追加到工具结果本身：*验证已过，若完成请现在 finish*。相比另发一条 user 消息，模型一定会读到这条结果。
- 系统提示增加一条：最后一次编辑后的成功验证之后，应当在同一轮调用 `finish`，不要继续探索或重复验证。
- 复测（deepseek-chat）：
  - `repo-sessions-filter`：加提示前 45 步 max_steps（519k tokens），加提示后 **10 步通过**（50 593 tokens）
  - `repo-edit-lines`：45 步预算下仍会耗尽；放宽到 60 步后 **54 步通过**（698 547 tokens）
  - `repo-pagination`：12 步通过
- 结论：三个仓库级任务在合适预算下 3/3；「干完不交卷」用一条 in-band 提示即可解决，大任务则需要按规模配预算（建议 `--max-steps 60`）。
- 验证：`pytest .` → 147 passed；`test_loop::test_verification_hint_after_edit_points_to_finish`。

### 2026-11-06 · 仓库级评测 + B-037 预算提醒

**评测扩展**

- 新增 3 个仓库级任务：`repo-pagination`（15 文件分层服务，加功能）、`repo-sessions-filter`（真实 3k 行项目，改 CLI）、`repo-edit-lines`（真实项目，新增工具）。
- runner 支持 `setup.py` 复制本地真实项目、`EVAL_TASK_DIR` 传路径、把当前解释器目录放到 `PATH` 前（等价激活 venv）；`pyproject` 的 `norecursedirs` 排除 `evals/`，避免隐藏测试被主测试套件收集。
- 实测（deepseek-chat，45 步）：`repo-pagination` 通过（12–13 步，$0.005）；`repo-sessions-filter` **代码通过验收但整轮 0 次 `finish`**，被 max_steps 截断（519k tokens，$0.016）；`repo-edit-lines` 未完成（640k tokens，$0.020）。
- 结论写进 `evals/README.md`：玩具任务 4/4、合成仓库 1/1、真实项目 0/2；主要失败模式是「不会停」，不是「不会写」。

**B-037 预算提醒**

- `run.nudge_at`（默认 0.75）：步数接近上限时追加一条一次性 `user.message {kind: budget}`，提示立即 `finish` 或收尾；`_nudges` 不把预算提醒计入普通 nudge 配额。
- 验证：`test_loop::test_budget_nudge_fires_once_before_max_steps`。

**B-038 登记为 open**

- 预算提醒在 45 步的仓库任务里仍未让模型调用 `finish`（0 次），需要更强的收尾机制；已有实测数据，方案待验证。

**验证**

```
pytest .   → 146 passed
mypy harness → Success: no issues found in 25 source files
ruff check harness tests examples evals → All checks passed!
```

### 2026-11-05 · B-026 Langfuse 实测（Docker + 镜像站 + SeaweedFS）

- Docker daemon 可用后，Docker Hub 仍直连超时 → 镜像全部走 `docker.m.daocloud.io`；MinIO 官方镜像被镜像站拒绝 → 用 **SeaweedFS** 做 S3 兼容存储。
- 新增 `deploy/langfuse/`：`docker-compose.yml`（web + worker + postgres + clickhouse + redis + seaweedfs）、`seaweed-s3.json`、`make_bucket.py`、`verify.py`、`README.md`。
- 用 `LANGFUSE_INIT_*` 自动创建组织/项目/账号，无需页面注册；OTLP 端点必须带 `/v1/traces`，否则 404。
- 实测会话 `b7c5852f62a7`：Langfuse 中 5 条 trace，`harness.turn` 为根（识别为 GENERATION），`harness.tool` 为其子 span（SPAN），`sessionId` 归组一致，usage `input=2348 / output=89`。
- 证据与复现：`docs/evidence/otlp-verification.md`。
- 验证：`python deploy/langfuse/verify.py`；`pytest .` → 145 passed。

### 2026-11-05 · B-026 OTLP 真实导出（Phoenix 代替 Langfuse）

- 环境限制：Docker CLI 在但 daemon 起不来（Docker Desktop 无进程、`wsl --status` 报 `E_ACCESSDENIED`），Langfuse 自托管无法运行；云端需注册。改用 **Arize Phoenix**（同协议、本地、无账号）。
- `Audit` 支持 OTLP headers（`HARNESS_OTLP_HEADERS`，逗号分隔 `k=v`），turn span 增加标准 `session.id` 属性，便于 Langfuse/Phoenix 归组。
- 实测：两次真模型会话（`7af0407241ba`、`945307712043`）导出到 Phoenix，**23 spans / 10 traces**；每条 `harness.tool` 的 `parent_id` 指向对应 `harness.turn`；属性解码出 `session.id`、`gen_ai.usage`。
- 证据与复现步骤：`docs/evidence/otlp-verification.md`（含换 Langfuse 的两行环境变量）。
- 验证：`pytest .` → 145 passed；`test_audit::test_parse_headers_for_otlp_auth`。

### 2026-11-05 · B-026 离线验证（OTLP span 树）

- 新增 `tests/test_otlp.py`：本地起 OTLP/HTTP receiver，`Audit(endpoint=...)` 真实导出，用 `opentelemetry.proto` 解码 `ExportTraceServiceRequest`，断言 `harness.turn` 2 个、`harness.tool` 带 `parent_span_id`。
- 结论：协议与 span 树已离线验证；接 Langfuse 只需把 `HARNESS_OTLP_ENDPOINT` 指向它的 OTLP 地址（仍需账号，条目保持 open）。
- 验证：`pytest .` → 144 passed。

### 2026-11-05 · B-029 reviewer + B-036 span 层级

**B-029 只读 reviewer**

- `tools/task.py` 抽出 `readonly_loop()`（explorer 与 reviewer 共用），新增 `reviewer(diff?, focus?)`：读 diff（缺省用 `git diff`）→ 只读探查 → 输出 `{verdict, risks, missing_tests, notes}` 结构化结果；非 JSON 回答退化为原文 + `parsed=false`。
- 验证：`tests/test_reviewer.py` 5 条（结构化结果、先读文件再评审、git diff 回退、无 LLM、非 JSON）。

**B-036 工具 span 挂到 turn 下**

- 重构 `loop.run` 一轮的结构：turn span 原来只包模型调用，现在覆盖「模型调用 + 工具执行」；`execute` 里的 tool span 自然成为 turn 的子 span。
- 验证：`test_audit::test_tool_spans_are_children_of_the_turn_span` 断言 `tool.parent.span_id` 属于对应 turn。

**验证**

```
pytest .   → 143 passed
mypy harness → Success: no issues found in 25 source files
ruff check harness tests examples evals → All checks passed!
```

### 2026-11-05 · B-027 / B-028（打包与 ADR）

**B-027 打包**

- `uv build` 产出 `dist/harness-0.1.0-py3-none-any.whl`（29 个文件）与 sdist；`entry_points.txt` 为 `h = harness.cli:main`。
- 验证：解包 wheel + `PYTHONPATH` 运行 `python -m harness --help` 通过（沙箱里 `uvx` 需要写 `%APPDATA%` 且要下载整套依赖，未跑通，已在 BACKLOG 写明替代验证方式）。

**B-028 ADR 与偏差表**

- 新增 `docs/ADR.md`：8 条关键决策（事件溯源、纯函数上下文、artifact 回读、策略与白名单、hash 三态、压缩冷却、finish 门槛、沙箱）与 10 条「spec vs 实现」偏差。
- 验证：README 文档区已链接。

### 2026-11-05 · P1 收尾（B-012 / B-014 / B-015 / B-016 / B-019 / B-020 / B-021 / B-022 / B-023）

**B-012 数据库迁移**

- `store` 用 `PRAGMA user_version` + `_migrate()`：旧库打开时按缺失列 `ALTER TABLE sessions ADD COLUMN`，然后写版本号。
- 验证：`test_store::test_old_database_is_migrated`（手建旧 schema → 新代码可读写）。

**B-014 resume 后重建 read 缓存**

- `read` 的 `tool.result.meta` 增记 `mtime_ns/size/start/end/head`；新增 `journal.rebuild_read_cache()` 从事件流恢复缓存，`loop.run` 启动时调用。
- 验证：`test_resume::test_read_cache_rebuilt_after_resume`、`test_read_cache_not_rebuilt_for_changed_file`（文件变了不会误命中）。

**B-015 输出上限与超时杀进程树**

- `LocalSandbox` 改 `Popen` + `communicate(timeout)`：超时后 Windows `taskkill /F /T`、POSIX `killpg`，再给 5 秒收尾，保证按时返回。
- `truncate_output()` 保留头 70% / 尾 30% 并标记；`ExecResult.truncated` 透传到 `bash` 的 `meta`。
- 验证：`tests/test_sandbox.py`（echo、200k 输出截断、sleep 30 超时 <15s）。

**B-016 跨平台 shell**

- `sandbox.shell = auto | cmd | pwsh | bash`；cmd/pwsh 用整串命令行并额外包一层引号（`cmd /c "..."` 会剥掉首尾引号，这是踩出来的）。`examples/demo.py` 的 bash 步骤顺手改成 `python list_src.py`，不再依赖宿主 shell 的 `ls`。
- 验证：`test_local_sandbox_explicit_cmd_shell`（Windows）。

**B-019 审批白名单跨 resume 恢复**

- `Policy(cfg, events)` 从 `approval.response(decision=always)` + 对应 `tool.call` 重建白名单；`loop` 传入 session 事件。
- 验证：`test_hardening::test_policy_restores_always_allowlist_from_events`。

**B-020 explorer 预算**

- 新增 `MAX_TOOL_CALLS=6`：超预算返回部分答案并标 `budget_exceeded`，不再无限读。
- 验证：正常回答、预算超限、无 LLM 三条。

**B-021 压缩请求记账**

- `compact()` 先写 `model.request {kind: "compact"}` 再调模型，成本与审计都能看到这次摘要调用。
- 验证：`test_compact` 断言事件与 `request_artifact` 一致。

**B-022 日志**

- 新增 `harness/log.py`（`HARNESS_LOG` 控制级别），`loop` 异常既写 `error` 事件也写 stderr 日志，`h` 启动时初始化。
- 验证：`test_hardening::test_loop_logs_failures`（caplog）。

**B-023 会话锁**

- `session_locks` 表 + `Store.acquire_lock/release_lock`（TTL 回收陈旧锁）；`loop.run` 抢锁失败直接报错，`finally` 释放。
- 验证：`test_hardening` 三条（互斥、过期接管、`run` 拒绝被锁 session）。

**验证**

```
pytest .   → 137 passed
mypy harness → Success: no issues found in 25 source files
ruff check harness tests examples evals → All checks passed!
```

### 2026-11-05 · 工程化与评测（B-011 / B-013 / B-017 / B-018 / B-024 / B-025，新修 B-034 / B-035）

**B-011 CI + 类型检查**

- `.github/workflows/ci.yml`：Windows + Ubuntu 矩阵，`uv sync --extra dev` → `ruff check` → `mypy harness` → `pytest` → 离线 demo。
- `pyproject.toml` 增加 `[tool.mypy]` 与 dev 依赖（ruff/mypy），`uv.lock` 重新锁定。
- 修了 4 个类型问题：`Ruling.decision` 收窄为 `Decision` Literal、approver 返回类型、litellm 可选导入改为 `find_spec` + `Any`。
- 验证：`mypy harness` → 24 files 零问题。

**B-017 成本与用量可见**

- `h trace` 增加 tokens/cost 列与汇总行，`--json` 输出机器可读版本。
- 新增 `h sessions`、`h cost <sid>`（逐次调用 + 合计），均支持 `--json`。
- 验证：`test_cli` 新增 4 条（trace 含成本、trace json、sessions、cost 合计）。

**B-018 运行时进度**

- `loop.run(..., on_event=...)` 推送新事件；`h run` / `h resume` 默认打印一行一事件的进度，`--quiet` 关闭。
- 验证：`test_loop::test_on_event_streams_events_in_order`。

**B-013 artifacts GC**

- `Store.gc(keep_days=7, dry_run=False)`：递归收集事件里的 artifact 引用，只删「无引用且够旧」的 blob 与行，events 表永远不动。
- 新增 `h gc [--keep-days N] [--dry-run]`。
- 验证：`test_store` 新增 2 条（只删孤儿、dry-run 不删、保留新旧）。

**B-024 / B-025 评测集与消融**

- `evals/run.py` + 4 个任务（bug / feature / edge / long-context），真实模型跑真实仓库，评测侧 `check.py` 独立验收；结果写 `evals/results/<label>.json`。
- 新增消融开关：`run.read_cache`、`run.compact_enabled`。
- 实测（deepseek-chat）：baseline 4/4、48 945 tokens、$0.0035；`no-cache` 4/4、48 952 tokens（完整集上看不出差异）；单跑 long-context 关缓存 +19.8% tokens；4k 窗口下压缩把 tokens 降 14% 但成本 +73%。
- 结论写进 `evals/README.md`：**压缩省上下文不省钱**，缓存只在重复读时有效。

**B-034 压缩风暴（评测中发现）**

- 小窗口下每轮都触发压缩，11 次摘要调用把成本放大 5 倍并耗尽步数。新增 `run.compact_cooldown`（默认 2 个 turn），压缩次数 11 → 7，成本 $0.0097 → $0.0081。
- 验证：`test_loop::test_compact_cooldown_limits_summary_calls`。

**B-035 瞬时 5xx 未重试（评测中发现）**

- `Server disconnected without sending a response` 属于瞬时故障但没命中重试标记，直接 `session error`。`_is_retryable` 增加 disconnected / internal server error / service unavailable。
- 验证：`test_llm` 新增 2 条断言。

**验证**

```
pytest .   → 118 passed
mypy harness → Success: no issues found in 24 source files
ruff check harness tests examples evals → All checks passed!
```

### 2026-11-05 · P0 收尾（B-006 / B-007 / B-008 / B-009）

**B-008 策略：堵住两处绕过**

- 改动：`_sensitive` 递归扫描**所有**字符串参数（此前只看 `path`，`bash cat .env` 会命中只读白名单直接放行）；新增 `path.read_outside` 规则，读操作默认禁止出 workspace，`policy.allow_read_outside=true` 可放开。
- 原因：只读命令白名单与写路径检查之间存在一条侧门，敏感文件可以被 `bash cat` 读走。
- 文件：`harness/policy.py`、`harness/config.py`、`harness.toml`
- 验证：`test_policy::test_bash_cat_env_asks_instead_of_bypassing_sensitive_rule`、`test_read_outside_workspace_is_denied`、`test_read_outside_can_be_enabled`（共 6 条）。

**B-009 上下文溢出兜底**

- 改动：新增 `ContextOverflow` 与 `_is_context_overflow()`（识别 context length / prompt too long 等文案），命中即抛、不重试；`loop` 捕获后强制压缩（`compact(force=True)`，保留最后一轮）再重试一次，仍失败则 `end("context_overflow")`；摘要请求自身超限也不会炸出循环。
- 原因：provider 报超限时旧行为是直接 `session error`，长任务在压缩触发线附近必然撞上。
- 文件：`harness/llm.py`、`harness/compact.py`、`harness/loop.py`
- 验证：`tests/test_overflow.py`（强制压缩并重试、二次超限干净结束）。

**B-006 模型可以自己维护任务状态**

- 改动：新增 `update_task(feature_id, passes, evidence)` 与 `progress(text)`；策略新增 `task.state` 自动放行（写的是 `.harness/tasks/*`，不是仓库代码）；`finish` 认可「feature list 全绿」作为验证证据。
- 原因：`tasks.set_feature` 只是函数，模型没有工具入口，S1 任务简报基本永远是空的。
- 文件：`harness/tools/task.py`、`harness/policy.py`、`harness/context.py`
- 验证：`tests/test_task_tools.py`（5 条，含「只靠 feature list 收工」）。

**B-007 副作用从「保守放弃」升级为「三态判定」**

- 改动：写类调用执行前计算 `target_path/sha_before/sha_after` 并写入 `tool.call`；恢复时比对当前文件 hash：等于 `sha_after` → 补一条成功 `tool.result`（不重写）；等于 `sha_before` → 安全重跑；都不是 → 报 unknown。
- 原因：崩溃在「已落盘、未记账」区间时，旧行为只能写 error 并放弃，浪费一次完整任务。
- 文件：`harness/loop.py`（`target_hashes`）、`harness/journal.py`
- 验证：`test_resume` 新增 4 条（已应用 / 未动重跑 / 被他人改动 / patch hash 计算）。

**验证**

```
pytest .   → 107 passed
ruff check → All checks passed!
```

真模型回归：`--scenario feature` → session `9c2ea9634da1`，status done，`h replay` 输出 `recorded seq=30 match`（策略收紧后读/写路径未被误伤）。

工具数 11 → 13；`tool.call` 与 `policy` 配置只加字段，未改名、未删除。

### 2026-11-04 · B-004 / B-005 / B-010（replay 真确定性、artifact 回读、finish 门槛）

**B-004 replay 从「重新生成」变成「重建当时请求」**

- 改动：`model.request` 增加 `snapshot_artifact`，快照内容包括 `task_brief` / `repo_map` / `calibration`；`build_context(events, cfg, snapshot=None)` 用快照覆盖 cfg；新增 `harness/replay.py::rebuild()`，取目标 seq 之前的事件重建，并与记录的 request artifact 逐字节比对。
- 原因：S1/S2 来自文件系统，不在事件流里；旧 `h replay` 重建的是「换一个配置重跑」的请求，不是模型当时真正看到的那份。
- 文件：`harness/replay.py` / `harness/context.py` / `harness/loop.py` / `harness/cli.py`
- 验证：`test_replay::test_rebuild_matches_recorded_request`、`test_snapshot_is_what_makes_replay_faithful`；真模型会话 `6c2c659fb2f7` 的 `h replay` 输出 `recorded seq=32 match`，两次 sha256 相同。

**B-005 新增 `read_artifact`：被截断的输出可以取回全文**

- 改动：新工具 `read_artifact(artifact_id, offset, limit)`，单次上限 8000 字符，返回 `remaining` 与下一个 offset；system prompt 明确提示用它回读。
- 原因：截断只发生在上下文层，但模型拿到 `artifact=xxx` 却没有取回手段，等于把信息截断成信息丢失。
- 文件：`harness/tools/artifacts.py`、`harness/tools/__init__.py`、`harness/context.py`
- 验证：`test_tools::test_read_artifact_recovers_truncated_output`。

**B-010 finish 需要证据**

- 改动：`finish` 在 `run.require_verification=true`（默认）时校验：改过文件 → 最后一次改动之后必须有一次成功的 `bash`；或者 feature list 全部 passes。不满足返回失败结果并提示，**不设 finished**，循环继续。
- 原因：真模型这次是自觉跑的 `check.py`，但机制上不强制，换个偷懒的模型就能空口宣布完成。
- 文件：`harness/tools/task.py`、`harness/config.py`、`harness.toml`
- 验证：新增 `tests/test_finish.py` 7 条（拒绝/通过/失败 bash 不算/无改动/feature 全绿/可关闭/循环不终止）。

**验证**

```
pytest .   → 91 passed
ruff check → All checks passed!
```

工具数 10 → 11；`model.request` 只新增 `snapshot_artifact` 字段，未改名、未删除（保持 replay 兼容）。

### 2026-11-04 · 真模型联调（关闭 B-003，新修 B-030 ~ B-033）

**目标**：把「主路径只跑过 fake LLM」这个最大缺口补上。

**改动**

- 新增 `examples/live_smoke.py`：真实仓库 + 真实 `bash`，三个场景 `bug / feature / edge`，脚本化审批回调（打印后放行），跑完打印 trace、token、成本、replay 指纹。
- `llm.resolve_model_name()`：配置里写裸模型名（`deepseek-chat`）时按 `api_base` 自动补 `deepseek/` 或 `openai/` 前缀——litellm 必须有 provider 前缀。
- `llm._is_retryable()`：只重试瞬时错误（429/超时/连接/5xx），缺依赖、坏 key 直接抛，不再白等 3 次。
- 依赖改为 `litellm[mcp]`：1.108 的 completion 路径会 `import mcp`，缺 extra 直接 `ModuleNotFoundError`。
- `litellm.suppress_debug_info = True`：1.108 在 provider 解析失败路径上直接 `print` 到 stdout，会污染 `h run` / `h trace`。
- system prompt 增加一行：shell 由宿主决定（Windows 是 `cmd.exe`），不要假设 `$?`、`$(...)`、heredoc。
- `h trace` / `examples/*` 的表格与代码输出改用 `rich.text.Text` / `markup=False`：代码里的 `ordered[mid]` 被 rich 当成样式标签吞掉过一次，看起来像模型写错了。

**实测结果（deepseek-chat，2026-11-04）**

| 场景 | session | 任务 | 结果 | events | tool calls | tokens | 成本 |
|---|---|---|---|---|---|---|---|
| bug | `e0e1fda9a911` | 修 `average` 分母 off-by-one | done | 37 | 6 | 8 043 | $0.000328 |
| feature | `ccf6b812bc51` | 补 `median` + 改 `check.py` | done | 30 | 5 | 6 497 | $0.000335 |
| edge | `51b03b6d9d2c` | 空列表返回 0 + 加断言 | done | 35 | 6 | 6 578 | $0.000326 |

- 更早的两次 bug 场景（加 shell 提示前 / 关 litellm 噪音前）分别是 12 441 与 10 038 tokens：提示词修好后同一任务 tokens 降了约 35%。
- 三次不同任务均通过 `python check.py`（模型自己跑的，工具结果是 `ok`），改动落在真实文件上。
- replay 指纹同一 session 两次一致（例如 `d257604dcd03` → `2eb67c10400c5398…`）。
- 成本来自 `litellm.completion_cost`，量级与 DeepSeek 定价吻合。
- 会话历史保留在 `.live-demo/state/harness.db`，可直接 `h trace <id> --db .live-demo\state\harness.db` 复盘。

**验证**

```
python examples/live_smoke.py --scenario bug
python examples/live_smoke.py --scenario feature
python examples/live_smoke.py --scenario edge
pytest .   → 81 passed
ruff check → All checks passed!
```

**备注**：API key 只注入本次进程环境变量，未写入任何文件；对话中出现过，建议用完吊销。

### 2026-11-04 · 修复 B-001 / B-002（代价最小、影响最大的两个 P0）

**B-001 恢复时的漂移提示被覆盖**

- 改动：`check_drift` 不再写 `cfg.task_brief`，改为写新字段 `cfg.drift_notice`；`loop.run` 每轮用 `_brief(progress, drift_notice)` 合并 S1。
- 原因：`run()` 每轮都会 `cfg.task_brief = progress_tail(...)`，把之前写入的「先 git diff 确认」冲掉；原单测只测了 `check_drift`，没测 `run`，所以一直是绿的。
- 文件：`harness/config.py`、`harness/journal.py`、`harness/loop.py`
- 验证：`test_resume::test_drift_notice_reaches_system_prompt`（断言 system prompt 里出现「## 漂移警告」与 `git diff`）。

**B-002 token 预算漏算工具调用参数**

- 改动：新增 `context.message_tokens()`，同时统计 `content` 与 assistant `tool_calls[].function`；分组预算与 `stats.tokens` 都改用它。
- 原因：patch 正文常几千字符，只算 `content` 会把预算显著低估，压缩触发偏晚；`calibration` 比率也跟着偏。
- 文件：`harness/context.py`
- 验证：`test_context::test_token_estimate_counts_tool_call_arguments`；预算/压缩相关测试保持全绿。

**流程**

- 新增 `docs/CHANGELOG.md`（本文件）、`docs/BACKLOG.md`、`docs/README.md`；主 README 增加文档入口。

**验证**

```
pytest .        → 79 passed in 13.89s
ruff check      → All checks passed!
```

---

## 2026-11-04 · 首次交付（v0.1）

### Added

| 模块 | 内容 |
|---|---|
| 事件与存储 | `models.py`（Event/ToolCall/Completion/Ruling/ExecResult）、`store.py`（SQLite + `BEGIN IMMEDIATE` 取 seq + 触发器禁止 UPDATE/DELETE + 内容寻址 artifacts） |
| 配置 | `config.py`（harness.toml 四段配置、`budget_tokens`、`config_hash`、状态目录） |
| 工具层 | `tools/registry.py`（ToolCtx/Tool/JSON schema/tools_hash）、`fs.py`（read 缓存、write、list、diff 预览）、`patch.py`（SEARCH/REPLACE + unified diff）、`shell.py`、`search.py`（grep/glob/repo_map）、`task.py`（finish/explorer） |
| 策略与沙箱 | `policy.py`（9 条有序规则 + 审批 + session 白名单 + 非交互 deny）、`sandbox.py`（local/msb + 自动降级并写 error 事件） |
| 模型接入 | `llm.py`（litellm、3 次重试、429 退避、成本、原始响应 artifact） |
| 上下文 | `context.py`（S0–S5 装填、预算丢弃、1200 字截断渲染、token 校准） |
| 压缩 | `compact.py`（最早 60% 覆盖 + 保留最近 `turn_keep` 个 turn + 固定 JSON 摘要 schema） |
| 恢复 | `journal.py`（`record_result`、`pending_calls`、`plan_resume`、`recover_pending`、`check_drift`） |
| 主循环 | `loop.py`（先记账后执行、审批、nudge、max_steps、压缩触发、OTel span、异常落 `error` 事件） |
| 审计 | `audit.py`（harness.turn / harness.tool / harness.compact，OTel 缺失时 Noop 降级） |
| 任务状态 | `tasks.py`（feature list / progress / git head / diff） |
| CLI | `cli.py`（run / resume / replay / trace / diff / approvals） |
| 演示 | `examples/demo.py`（脚本化 LLM，离线跑完整 session） |
| 测试 | 12 个测试文件；全部不调真模型 |

### Fixed（开发过程中暴露并修掉的）

| # | 问题 | 处理 |
|---|---|---|
| 1 | 组装预算取 `window*compact_at`，等于压缩触发线，压缩永远不触发 | 预算改为 `window - max_tokens`（`config.budget_tokens`） |
| 2 | 上下文分组把 `context.compact` 算进上一组，已压缩事件仍被保留 | 只对「会渲染成消息」的事件分组（`context.rendered_types`） |
| 3 | 被策略拒绝的调用没有 tool span，审计数量对不上 `tool.call` | span 覆盖 policy 决策 + 执行（`loop.execute`） |
| 4 | `tasks.diff` 用 `base..HEAD`，看不到工作区未提交改动 | 改为 `git diff <base>` |
| 5 | `cache_hit` 平铺在 `tool.result.data` | 按 spec 收进 `tool.result.meta`（`journal.record_result`） |
| 6 | `loop.py` 290 行，超过 250 行预算 | 拆出 `journal.py` |
| 7 | `bash ls` 因 exec 默认 ask 被拦，与验收第 4 条冲突 | 增加 `cmd.safe_readonly` 白名单规则 |
| 8 | 本机 Windows 沙箱下 pytest 用 0o700 建的临时目录不可读 | `tests/conftest.py` 覆盖 `tmp_path`（仅测试环境，非产品代码） |

### 决策（记录理由，防止后来者改回去）

1. **纯函数上下文**：`build_context(events, cfg)` 不碰 DB、不读文件；S1/S2 由 loop 注入。代价是 replay 语义不完整（见 B-004）。
2. **工具输出全量进 artifact**：事件只存头尾 400 字 + `chars` + `artifact_id`，截断只发生在渲染层。
3. **全量返回 + 引用**意味着模型目前**没有回读通道**，这是已知缺口（B-005）。
4. **恢复对写类工具选择「不重跑」**：宁可写 `error` 也不重复副作用；更优解是 hash 判定三态（B-007）。
5. **压缩摘要调用不记 `model.request`**：请求体落在 `context.compact.request_artifact`，成本只进 session 汇总（B-021）。

### Verified

```
pytest .                    → 77 passed（首次交付时）
python examples/demo.py     → read → patch(ask→allow) → bash → grep → finish，30 条事件
h replay <sid> --dry-run    → 两次 sha256 相同
h trace / h approvals / h diff 均实跑通过
ruff check                  → All checks passed!
```

### 已知问题

全部转入 [BACKLOG.md](BACKLOG.md)，其中 P0 共 10 条（含本轮已修的 B-001/B-002）。
