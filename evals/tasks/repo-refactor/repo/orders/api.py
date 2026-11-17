from __future__ import annotations

from orders.utils import format_price


def price_label(cents: int) -> str:
    return f"Price: {format_price(cents)}"
