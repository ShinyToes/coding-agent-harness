from __future__ import annotations

from orders.utils import compute_total, format_price, slugify


def test_format_price():
    assert format_price(12345) == "$123.45"
    assert format_price(0) == "$0.00"


def test_compute_total_with_qty():
    items = [{"price": 1000, "qty": 2}, {"price": 250}]
    assert compute_total(items) == 2250


def test_slugify():
    assert slugify("  Hello   World ") == "hello-world"
