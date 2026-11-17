# 项目文档

三个文件，各管一件事：

| 文件 | 管什么 | 什么时候写 |
|---|---|---|
| [CHANGELOG.md](CHANGELOG.md) | **每次修改**：改了什么、为什么、怎么验证、动了哪些文件 | 每次提交/每次会话结束前 |
| [BACKLOG.md](BACKLOG.md) | **每次待改进**：ID、问题、措施、验收标准、文件、估算、状态 | 发现问题的那一刻 |
| 本文件 | 维护约定与模板 | 约定变化时 |

主 README 只保留「怎么用」，过程与欠债都在这里，避免 README 变成流水账。

## 五条维护约定

1. **先记后改**：动手前在 BACKLOG 建条目（除明确的 P0 阻塞，可先修后补记，但当天必须补）。
2. **改完即记**：CHANGELOG 的 `Unreleased` 追加一条，必须包含「为什么」和一条可复现验证；只写「修了 bug」的信息量为零。
3. **关闭有门槛**：BACKLOG 条目关闭 = 验收命令/测试通过 + 状态改 `done` 并写日期 + CHANGELOG 有对应验证。三者缺一不算完成。
4. **ID 只增不改**：`B-xxx` 不复用、不重编号；拆分时新开号并在原条目写「拆为 B-0xx」。
5. **每周五清点**：P0 未清空不发版；P1 每两周至少清 3 条；P2 按里程碑推进。

## 状态机

```
open ──开始做──> in_progress ──验收通过──> done（写日期）──> verified（发版时归档）
                        └──放弃/合并──> dropped（写原因与去向）
```

## 模板

### 新增 CHANGELOG 条目

```markdown
### YYYY-MM-DD · <一句话主题>

**B-0xx <标题>**

- 改动：<具体到文件/函数，做了什么>
- 原因：<为什么原来是错的/不够的，说清触发条件>
- 文件：`path/a.py`、`path/b.py`
- 验证：`tests/test_x.py::test_y`（或命令 + 输出摘要）
```

### 新增 BACKLOG 条目

```markdown
| B-0xx | <问题：现状 + 为什么是问题> | <措施：改哪个文件、用什么办法、分几步> | <验收：可执行的断言/命令> | `file.py` | S/M/L | open |
```

估算口径：`S` ≤ 半天，`M` 1–2 天，`L` 3–5 天。

## 常用命令

```powershell
pytest .                                  # 当前 166 passed
ruff check harness tests examples evals   # 全绿
mypy harness                              # 全绿
python examples/demo.py                   # 离线跑完整 session
python examples/live_smoke.py --scenario bug   # 真模型冒烟（需 DEEPSEEK_API_KEY）
python evals/run.py --label baseline           # 评测（需 key，结果写 evals/results/）
uv run h trace <session_id>               # 复盘一次会话
uv run h cost <session_id>                # 逐次调用成本
```

## 每次会话结束前的自查

- [ ] 今天的改动都在 CHANGELOG 有记录（含验证）
- [ ] 今天新发现的问题都进了 BACKLOG（哪怕只写一行）
- [ ] `pytest` / `ruff` 全绿，或已在 BACKLOG 登记
- [ ] README 与代码行为没有冲突
