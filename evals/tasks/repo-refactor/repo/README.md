# orders

一个小的订单示例仓库：

- `orders/utils.py` —— `format_price` / `compute_total` / `slugify`
- `orders/service.py` —— `quote(items)` 生成一行报价
- `orders/api.py` —— `price_label(cents)`
- `tests/` —— pytest，用 `python -m pytest -q` 运行
