# 评测（evals）

真实模型 + 真实仓库 + 独立验收脚本。任务目录约定：

```
tasks/<name>/
├── task.md      # 给模型的自然语言任务
├── repo/        # 初始仓库（可选 check.py 供模型自检）
├── setup.py     # 可选：准备仓库（生成文件，或从本地复制真实项目）
└── check.py     # 评测侧验收，模型看不到；rc=0 视为通过
```

## 任务集

| 任务 | 规模 | 类型 | 考察点 |
|---|---|---|---|
| `bug-average` | 4 文件 / 10 行 | 玩具级修 bug | 读文件 → 改代码 → 跑检查 |
| `feature-median` | 4 文件 / 13 行 | 玩具级加功能 | 补函数 + 边界 |
| `edge-empty` | 4 文件 / 9 行 | 玩具级防御性修改 | 改实现 + 补断言 |
| `long-context` | 24 模块 / 35 行 | 玩具级检索 | 多文件定位 |
| `repo-pagination` | 15 文件 / 分层服务 | 仓库级加功能 | 跨 api/service 层 + 分页校验 + 保持旧测试绿 |
| `repo-sessions-filter` | 真实项目 3k 行 | 仓库级 CLI 功能 | 读多模块 → 改 CLI → 跑既有测试 |
| `repo-edit-lines` | 真实项目 3k 行 | 仓库级新增工具 | 新建工具 + 注册 + 策略 + 测试 |
| `repo-lock-ttl` | 真实项目 3k 行 | 仓库级修 bug（注入回归） | 从失败测试定位 TTL 语义并修复，不破坏其他锁行为 |
| `repo-refactor` | 12 文件合成仓库 | 仓库级跨文件重构 | 迁移实现 + 更新调用点 + 保留兼容导出 |

`repo-*` 任务通过 `setup.py` 把本地 harness 项目复制进工作区（跳过 `.venv` / 缓存 / 评测自身），验收脚本把隐藏测试放进 `tests/` 复用项目的 `tmp_path` 规避。

## 用法

```powershell
$env:DEEPSEEK_API_KEY = "sk-..."
python evals/run.py --offline                        # CI 自检：初始仓库必须验收失败（不需要 key）
python evals/run.py --label baseline                 # 玩具级 4 任务
python evals/run.py --label repo-v5 --only repo- --max-steps 60
python evals/run.py --label no-cache --no-cache
python evals/run.py --label long-compact --only long-context --window 4000 --max-tokens 512 --compact-at 0.5
```

`--offline` 是防「任务白给」的闸门：如果某个任务在未修改的初始仓库上就能通过验收，说明它没检查任何新行为，CI 直接失败。

runner 会把当前解释器目录放在 `PATH` 前面（等价于激活 venv），否则 agent 的 `bash python ...` 会用到系统 Python。

## 实测（deepseek-chat）

### 仓库级任务（2026-11-06，label `repo-v7`）

| 任务 | 结果 | 步数 | tokens | 成本 | 说明 |
|---|---|---|---|---|---|
| `repo-edit-lines` | **pass** | 43 | 600 210 | $0.016 | 新增工具 + 注册 + 策略 + 测试 |
| `repo-sessions-filter` | **pass** | 23 | 158 132 | $0.007 | 收尾提示前 45 步失败；提示后稳定通过 |
| `repo-lock-ttl` | **pass** | 14 | 64 740 | $0.004 | 注入锁 TTL 回归，从失败测试定位 |
| `repo-pagination` | **pass** | 6 | 20 788 | $0.003 | 15 文件分层服务加分页 |
| `repo-refactor` | **pass** | 6 | 19 337 | $0.002 | 跨文件迁移 + 兼容导出 |

合计 5/5，863 207 tokens，$0.031。

结论：

1. **仓库级任务能区分能力**：玩具任务 4/4，合成仓库 2/2，真实 3k 行项目 3/3（edit-lines / sessions-filter / lock-ttl）。
2. **「干完不交卷」是真实失败模式**：加「验证通过后直接提示 finish」前，sessions-filter 45 步 0 次 `finish`；加之后同任务 10–23 步通过。
3. **大任务要按规模给预算**：`edit_lines` 需要 43–54 步；45 步预算下曾有一次刚好截断，建议 `--max-steps 60`。
4. 真实项目任务单次 $0.002–0.016，是玩具任务的 5–30 倍。

### 失败分类

`evals/classify.py` 把结果映射成可统计类别，避免「passed=false」把不同问题混在一起：

| 类别 | 含义 |
|---|---|
| `pass` | 正常结束且验收通过 |
| `solved_no_finish` | 验收通过但没正常结束（典型：不调用 finish 被截断） |
| `tests_fail` | 正常结束但验收不通过 |
| `budget` | 步数耗尽且验收不通过 |
| `cost_budget` | 成本上限到达且验收不通过 |
| `cancelled` | 用户中断（Ctrl+C） |
| `stopped` | 模型不再调用工具且验收不通过 |
| `error` | 运行异常 |

JSON 结果里每条都有 `category`，汇总里有 `categories` 计数；`solved_no_finish` 是 B-038 的来源。

### 玩具任务与消融（2026-11-05）

| label | 通过 | tokens | 成本 | 压缩次数 |
|---|---|---|---|---|
| `baseline`（4 任务） | 4/4 | 48 945 | $0.003541 | 0 |
| `no-cache`（4 任务） | 4/4 | 48 952 | $0.003509 | 0 |
| `long-base` | 1/1 | 15 170 | $0.000892 | 0 |
| `long-nocache` | 1/1 | 18 180 | $0.001817 | 0 |
| `long-compact`（4k 窗口） | 1/1 | 15 631 | $0.001881 | 2 |
| `long-nocompact`（4k 窗口） | 1/1 | 18 171 | $0.001089 | 0 |

- read 缓存：单任务关缓存 +19.8% tokens；完整集无差异（每个文件只读一次）
- 压缩：4k 窗口下 tokens −14%，成本 +73%——**省上下文不省钱**

## 限制

- 只有 deepseek-chat 一个模型；任务数少，不能当基准分数看
- 隐藏测试只覆盖契约，不覆盖代码质量
- 真实项目任务会复制整个 `harness/` + `tests/`，单次 20–130 秒
