仓库根目录是一个 Python 项目（`harness` 包 + `tests`）。给 `h sessions` 命令加两件事：

1. `--status` 过滤：例如 `h sessions --status done` 只列出该状态的会话。
2. `--json` 输出里每个会话对象增加 `last_commit` 字段。

相关位置：`harness/cli.py` 的 `sessions()` 命令、`harness/models.py` 的 `Session`。
依赖已装好，改完直接跑 `python -m pytest tests/test_cli.py tests/test_store.py -q` 验证，通过后调用 `finish`。
