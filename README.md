# harness

单机、单进程、面向代码仓库的本地 coding agent harness。事件溯源 + 断点恢复 + 上下文预算 + 工具副作用管控 + 可回放审计。

按 `harness-spec.md`（v0.1）实现：**约 3300 行 Python（harness/）+ 约 2900 行测试/评测**，14 个工具、166 个测试，单文件 ≤ 250 行，第三方依赖只有 spec 白名单里的 6 个（`microsandbox`、`tiktoken` 为可选 extra）。真模型已用 `deepseek-chat` 跑通 3 个冒烟场景、4 个玩具任务与 5 个仓库级任务（见 [docs/CHANGELOG.md](docs/CHANGELOG.md)、[evals/README.md](evals/README.md)）。

```
harness/
├── pyproject.toml / harness.toml / uv.lock
├── .github/workflows/ci.yml   Windows + Ubuntu：ruff / mypy / pytest / 离线 demo
├── harness/
│   ├── cli.py        h run / resume / replay / trace / diff / approvals / sessions / cost / gc
│   ├── config.py     pydantic Settings，读 harness.toml
│   ├── models.py     Event / ToolCall / Completion / Ruling / ExecResult
│   ├── store.py      sqlite 事件表 + 内容寻址 artifacts（触发器禁止 UPDATE/DELETE）
│   ├── llm.py        litellm 包装、错误分类重试、成本
│   ├── context.py    build_context：S0–S5 预算装填 + 截断（纯函数）
│   ├── compact.py    最早 60% 事件 → context.compact 摘要
│   ├── journal.py    tool.result 记账、pending_calls、plan_resume、漂移检查
│   ├── loop.py       主循环：先记账后执行 + 恢复 + nudge + 压缩冷却 + 进度回调
│   ├── policy.py     有序规则表 + 审批回调 + session 白名单
│   ├── sandbox.py    local / microsandbox 两种实现，msb 不可用自动降级
│   ├── audit.py      OTel span（harness.turn / tool / compact）
│   ├── tasks.py      feature list / progress / git 查询
│   └── tools/        registry / fs(read,write,list) / artifacts / patch / shell / search / task
├── examples/demo.py  离线演示（脚本化 LLM，不联网）
├── examples/live_smoke.py  真模型冒烟：bug / feature / edge 三场景
├── evals/            9 个评测任务（5 个仓库级）+ runner + 失败分类 + 消融开关
├── deploy/langfuse/  本地 Langfuse v3 栈（镜像站 + SeaweedFS），一键起本地观测后端
├── docs/             CHANGELOG（每次修改）+ BACKLOG（每次待改进）+ 维护约定
└── tests/            166 个测试，全部不调真模型
```

## 文档

- [docs/CHANGELOG.md](docs/CHANGELOG.md) —— 每次修改：改了什么、为什么、怎么验证
- [docs/BACKLOG.md](docs/BACKLOG.md) —— 每次待改进：ID、措施、验收标准、状态（40/40 全部关闭）
- [docs/PROJECT.md](docs/PROJECT.md) —— 一页说明：问题、架构图、数据、取舍
- [docs/STORIES.md](docs/STORIES.md) —— 三个 bug 的复盘（漂移提示 / 压缩风暴 / 收尾提示）
- [deploy/langfuse/README.md](deploy/langfuse/README.md) —— 本地 Langfuse 栈的启动、凭据与 OTLP 配置
- [docs/ADR.md](docs/ADR.md) —— 8 条关键决策 + 与 spec 的偏差
- [docs/evidence/otlp-verification.md](docs/evidence/otlp-verification.md) —— OTLP 真实导出证据（Langfuse + Phoenix）
- [docs/README.md](docs/README.md) —— 维护约定、模板、发版 DoD
- [evals/README.md](evals/README.md) —— 评测集、消融数据与结论（含反例）

## 快速开始

```powershell
uv sync --extra dev          # 或 uv venv + uv pip install -e ".[dev]"
.venv\Scripts\Activate.ps1
pytest -q                    # 166 passed
python examples\demo.py      # 不联网跑完一个 session：read → patch → bash → grep → finish
uv build                     # 出 dist/harness-0.1.0-py3-none-any.whl
h --help
```

真模型冒烟（需要 key，三场景任选）：

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
python examples\live_smoke.py --scenario bug       # 修 average 的 off-by-one
python examples\live_smoke.py --scenario feature   # 补 median + 改 check.py
python examples\live_smoke.py --scenario edge      # 空列表返回 0 + 加断言
```

跑真模型（需要 `DEEPSEEK_API_KEY` 或对应 provider 的 key）：

```powershell
h run "给 parse_date 加上时区支持" --repo . --model deepseek-chat
h run "..." --max-cost 0.05      # 花到 $0.05 自动收尾，Ctrl+C 也是干净的 cancelled
h resume <session_id>            # 默认打印实时进度，--quiet 关闭
h replay <session_id> --to-seq 42 --dry-run   # 输出 recorded seq=N match
h trace <session_id>             # seq / type / tokens / cost / summary，--json 可机读
h sessions                       # 所有会话、状态、token、成本
h cost <session_id>              # 逐次调用成本 + 合计
h diff <session_id>
h approvals <session_id>
h gc --keep-days 7               # 清理无引用的 artifact（events 不动）
```

`h run --yes` 会把所有 `ask` 降级为 `deny`（非交互模式）。

评测（真实模型，需 key）：

```powershell
python evals/run.py --label baseline
python evals/run.py --label no-cache --no-cache
python evals/run.py --label long-compact --only long-context --window 4000 --max-tokens 512 --compact-at 0.5
```

## 六条不变量落在哪里

| 不变量 | 实现 | 被哪个测试钉住 |
|---|---|---|
| 事件只追加 | `store.SCHEMA` 里的 `events_no_update` / `events_no_delete` 触发器 | `test_store::test_events_are_append_only_at_the_database_level` |
| 上下文是纯函数 | `context.build_context(events, cfg)` 不碰 DB、不读文件 | `test_context::test_build_context_is_deterministic`、`test_replay` |
| 副作用先记账后执行 | `loop.execute`：`tool.call` → `policy.decision` → 执行 → `tool.result` | `test_loop::test_read_then_finish_event_order` |
| 所有副作用过策略 | `policy.RULES` 有序表，`policy.decision` 本身就是事件 | `test_policy`（4 组验收用例） |
| 工具返回全量 | `journal.record_result`：全量进 artifact，事件只存头尾 400 字；`read_artifact` 可取回全文 | `test_context::test_long_tool_output_rendered_with_artifact_ref`、`test_tools::test_read_artifact_recovers_truncated_output` |
| 每次模型调用可复现 | 请求体落 artifact，S1/S2 快照落 `model.request.snapshot_artifact`；`replay.rebuild()` 重建并与记录值比对 | `test_replay::test_rebuild_matches_recorded_request` |

## 关键设计

**事件表**只加不改：`sessions` / `events` / `artifacts`，`seq` 在 `BEGIN IMMEDIATE` 事务里 `max(seq)+1`。

**恢复**（`journal.plan_resume`）：

- 最后一条是 `session.end` → `done`
- 有 `tool.call` 没有 `tool.result` → `continue_calls`
- 纯读工具（read/grep/glob/list/repo_map/read_artifact）可安全重跑，且不会重复写 `tool.call`
- `write` / `patch` 用内容 hash 判三态：已应用 → 补成功结果；未改动 → 安全重跑；被改过 → `error` 事件 + 失败结果
- `bash` 无法判定 → 写 `error` 事件 + 一条失败 `tool.result`，**不重跑**（验收第 1 条）
- 恢复时比对 git HEAD，漂移就写 `error` 并在 S1 注入「先 git diff 确认」

**压缩**（`compact.compact`）：超 `window * 0.7` 触发，摘要固定 JSON schema（summary / decisions / open_items / files_touched / dead_ends）写入 `context.compact`，被覆盖事件不再进消息；`task.update` 与 `session.start.task` 不进摘要，永远以原文进 S1，所以**压缩丢细节不丢任务**。

**上下文预算**：装填预算为 `window - max_tokens`（必须大于压缩触发线，否则压缩永不触发），超预算从最旧的一组消息开始丢；单组 tool.call/tool.result 一起丢，不会拆出孤儿 tool 消息。

**上下文溢出**：provider 报 `context_length` 超限时抛 `ContextOverflow`（不重试）→ 强制压缩并重试一次；仍失败则以 `context_overflow` 干净收尾。

**策略**：`path.escape`(deny) → `path.read_outside`(deny) → `path.sensitive`(ask) → `cmd.destructive`(deny) → `cmd.network`(ask) → `cmd.git_write`(ask) → `cmd.chained`(ask) → `cmd.safe_readonly`(allow) → `task.state`(allow) → `fs.overwrite`(ask) → `read.default`(allow) → 按 effect 默认。首个命中生效；`always` 记入 session 内存白名单。敏感检查覆盖所有字符串参数，`bash cat .env` 也会被拦。

**审批**：`approval.request` 事件带 `diff_preview`，`approval.response` 记录 actor/decision；非 tty 或 `--yes` 一律 deny。

**finish 门槛**：改过文件后，最后一次改动之后必须有一次成功的 `bash`（或 feature list 全绿）才允许 `finish`；被拒时返回失败结果并让循环继续，不设 `finished`。可用 `run.require_verification = false` 关闭。

**replay**：`model.request` 同时记录 S1/S2 快照 artifact；`h replay` 用 `replay.rebuild()` 在目标 seq 之前重建消息，并与记录的请求逐字节比对，输出 `recorded seq=N match`。

**长跑保护**：`session_locks` 表加 TTL 锁，同一 session 不能被两个进程同时跑；`--max-cost` 到预算即 `session.end(max_cost)`；Ctrl+C 落 `session.end(cancelled)` 并释放锁；`HARNESS_LOG=DEBUG` 把异常同时写到 stderr 与 `error` 事件；`h gc` 回收无引用 artifact；resume 时从事件流重建 read 缓存。

**观测**：设 `HARNESS_OTLP_ENDPOINT` 即导出 span（`harness.turn` / `harness.tool` / `harness.compact`），`HARNESS_OTLP_HEADERS` 传鉴权头（逗号分隔 `k=v`）；turn span 带标准 `session.id`，Phoenix / Langfuse 可按会话归组。本地想直接看：`docker compose -f deploy/langfuse/docker-compose.yml up -d`，详见 [deploy/langfuse/README.md](deploy/langfuse/README.md)；实测证据见 [docs/evidence/otlp-verification.md](docs/evidence/otlp-verification.md)。

## 配置 `harness.toml`

```toml
[model]
name = "deepseek-chat"
api_base = "https://api.deepseek.com"
window = 128000
temperature = 0

[run]
max_steps = 120
max_nudges = 2
compact_at = 0.7
turn_keep = 8
require_verification = true   # 改过文件后必须有一次成功的 bash 才能 finish
compact_cooldown = 2          # 两次自动压缩之间至少隔几个 turn

[sandbox]
kind = "local"        # local | msb
timeout = 120
shell = "auto"        # auto | cmd | pwsh | bash
max_output = 1000000  # 单条命令输出上限（字符）

[policy]
auto_allow_read = true
approval_mode = "interactive"   # interactive | deny
```

状态默认落在 `<repo>/.harness/`：`harness.db`、`artifacts/`、`tasks/feature_list.json`、`tasks/progress.md`。

## 验收测试对照（6/6 全绿）

| # | 验收点 | 测试 |
|---|---|---|
| 1 | 崩溃恢复不重复副作用 | `test_resume::test_interrupted_write_is_not_replayed_but_records_error` |
| 2 | 重复读命中缓存 | `test_tools::test_read_second_time_hits_cache`、`test_loop::test_repeated_read_hits_cache_and_shrinks_messages` |
| 3 | 压缩后任务状态不丢 | `test_compact::test_compact_keeps_feature_list_after_resume`（600k 字符 ≈ 20 万 token 事件流） |
| 4 | 策略拦截正确 | `test_policy`（`rm -rf /` deny、`ls` allow、`curl` ask、写 workspace 外 deny） |
| 5 | 审计完整 | `test_audit::test_three_turns_and_matching_tool_spans`（3 turn span，tool span == tool.call 数） |
| 6 | 回放确定性 | `test_replay`（两次 `replay --to-seq` sha256 相同） |

## 已知限制（v0）

- 只接 OpenAI 兼容 API；本地无 key 时用 `examples/demo.py` 或测试里的脚本化 LLM 验证。
- `explorer` 子 agent 需要主 loop 绑定的 LLM；无 LLM 时返回失败而不是假装成功。
- `sandbox = "msb"` 需要 microsandbox + WHP；不可用时自动降级 `local` 并写 `error` 事件。
- 无 Web UI、无多 agent 编排、无向量记忆（spec 第 1 节非目标）。
- 压缩的摘要调用现在会写 `model.request {kind: compact}`，但成本仍只汇总在 session 上，没有单独的报表。
- 本机 Windows 沙箱下用 0o700 建的目录对当前 token 不可读，`tests/conftest.py` 覆盖了 `tmp_path` 规避；正常机器上不影响。
- 跨平台 shell 只验证到 `echo` 级别；POSIX 命令语义没有 Windows CI 覆盖（CI 矩阵有 Ubuntu，但用例仍偏少）。

## 下一步（v1 替换点）

| 想升级什么 | 换成什么 | 位置 |
|---|---|---|
| 压缩质量 | OpenHands Condenser 摘要策略 | `compact.py` |
| 检索精度 | Serena 全量符号工具 / tree-sitter repo map | `tools/search.py` |
| 沙箱强度 | microsandbox microVM / E2B | `sandbox.py` |
| 长任务恢复 | DBOS / Temporal | `loop.py` |
| 观测 | Langfuse 自托管（OTLP 直连，设 `HARNESS_OTLP_ENDPOINT`） | `audit.py` |
| 多 agent | 在 `tools/task.py` 加 explorer / reviewer | 不动主循环 |

## GitHub Actions CI

CI 配置保存在 [`.github/ci-template.yml`](.github/ci-template.yml)。当前尚未启用自动 CI；如需启用，将该文件移至 `.github/workflows/ci.yml`，并通过具备 `workflow` 权限的 GitHub 授权方式提交。模板包含 Windows / Ubuntu 下的 lint、类型检查、测试、离线评测与 demo。
