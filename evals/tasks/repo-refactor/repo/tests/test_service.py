from __future__ import annotations

from orders.service import quote


def test_quote_one_line():
    items = [{"price": 1000, "qty": 2}, {"price": 250}]
    assert quote(items) == "2 items: $22.50"
