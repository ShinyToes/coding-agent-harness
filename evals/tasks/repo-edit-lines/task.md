仓库根目录是一个 Python 项目（`harness` 包 + `tests`）。请新增一个工具 `edit_lines`：

- 签名：`edit_lines(path, start, end, content)`，`effect = "write"`
- 语义：把文件第 `start` 到第 `end` 行（1-based，含两端）替换为 `content` 的内容，其余行保持不动；文件原本以换行结尾时结果也保留结尾换行
- 错误：文件不存在、`start < 1`、`end < start` 都返回 `ok=False`，不能抛异常
- 必须注册到工具注册表（`from harness.tools import TOOLS` 能查到，`tool_schemas()` 里能看到），并遵守现有策略（`effect` 为 write 时，覆盖已存在文件会触发审批、写 workspace 外会被拒绝）

相关位置：`harness/tools/`（新增文件后要在 `harness/tools/__init__.py` 里 import 才会注册）、`harness/tools/registry.py`、参考 `harness/tools/fs.py` 的写法。
依赖已装好，改完直接跑 `python -m pytest tests/test_tools.py tests/test_policy.py -q` 验证，通过后调用 `finish`。
