仓库根目录是一个小的订单服务（`orders/` 包 + `tests/`）。现在 `format_price` 和 `compute_total` 混在 `orders/utils.py` 里，和 `slugify` 这种纯文本工具放在一起。请做一次跨文件重构：

1. 新建 `orders/money.py`，把 `format_price` 和 `compute_total` 的实现**移过去**（不要在 utils 里保留重复实现）
2. `orders/service.py`、`orders/api.py` 改为从 `orders.money` 导入
3. 为兼容旧调用方，`orders/utils.py` 继续导出这两个名字（从 money re-export 即可），`slugify` 留在 utils
4. 包的公开 API 不变：`from orders import format_price` 仍然可用，行为不能变

改完运行 `python -m pytest -q` 确认现有测试仍然通过，再调用 `finish`。
