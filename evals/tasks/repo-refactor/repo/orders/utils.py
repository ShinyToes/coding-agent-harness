"""金额格式化与聚合。"""

from __future__ import annotations


def format_price(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def compute_total(items: list[dict]) -> int:
    return sum(int(item["price"]) * int(item.get("qty", 1)) for item in items)


def slugify(name: str) -> str:
    return "-".join(part for part in name.strip().lower().split() if part)
