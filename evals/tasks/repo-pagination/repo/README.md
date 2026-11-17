# books

一个很小的分层示例服务：

- `books/models.py` —— `Book` dataclass
- `books/store.py` —— 内存存储，6 本种子数据
- `books/service.py` —— 业务层
- `books/api.py` —— 对外接口，当前返回 `{"items": [...], "total": N}`
- `tests/` —— pytest，用 `python -m pytest -q` 运行
