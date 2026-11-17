"""repo-refactor：迁移后的模块位置、兼容导出与行为。"""

from __future__ import annotations

import orders.money
import orders.utils
from orders import format_price
from orders.api import price_label
from orders.service import quote


def test_money_module_owns_implementation():
    assert orders.money.format_price(12345) == "$123.45"
    assert orders.money.compute_total([{"price": 100, "qty": 3}]) == 300
    # 实现必须搬到 money，utils 只能 re-export
    assert orders.money.format_price.__module__ == "orders.money"
    assert orders.money.compute_total.__module__ == "orders.money"


def test_utils_keeps_backwards_compatible_reexports():
    assert orders.utils.format_price(100) == "$1.00"
    assert orders.utils.compute_total([{"price": 250}]) == 250
    assert orders.utils.format_price.__module__ == "orders.money"


def test_slugify_stays_in_utils():
    assert orders.utils.slugify("  Hello   World ") == "hello-world"


def test_package_and_layers_unchanged():
    assert format_price(100) == "$1.00"
    assert price_label(999) == "Price: $9.99"
    assert quote([{"price": 1000, "qty": 2}, {"price": 250}]) == "2 items: $22.50"
