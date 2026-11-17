from __future__ import annotations

from orders.utils import compute_total, format_price


def quote(items: list[dict]) -> str:
    """一行报价：`2 items: $1,234.50`。"""
    return f"{len(items)} items: {format_price(compute_total(items))}"
