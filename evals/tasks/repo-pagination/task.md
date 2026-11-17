仓库根目录是一个小的分层服务（`books/` 包 + `tests/`）。现在 `books.api.list_books()` 一次返回全部书，需要加分页：

- 签名改为 `list_books(limit: int | None = None, offset: int = 0) -> dict`
- `limit=None` 表示不分页（返回全部）；`limit` 为 1..100 的整数，否则 `ValueError`
- `offset` 为 >=0 的整数，否则 `ValueError`
- 返回值在原有 `items`、`total` 基础上增加 `limit` 与 `offset`：
  - `total` 是**过滤前的总数**（6），不随分页变化
  - `items` 是按 offset/limit 切出来的那一页
- 业务层（`books/service.py`）也要能接受 limit/offset 并透传，`BookStore` 不需要改
- 不传参数时行为与现在一致（返回全部 6 本）

改完运行 `python -m pytest -q` 确认现有测试仍然通过，再调用 `finish`。
