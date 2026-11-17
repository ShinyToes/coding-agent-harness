from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Book:
    id: int
    title: str
    author: str
    year: int

    def to_dict(self) -> dict:
        return asdict(self)
