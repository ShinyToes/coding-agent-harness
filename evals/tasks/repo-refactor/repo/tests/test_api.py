from __future__ import annotations

from orders import format_price
from orders.api import price_label


def test_price_label():
    assert price_label(999) == "Price: $9.99"


def test_package_reexports_formatter():
    assert format_price(100) == "$1.00"
