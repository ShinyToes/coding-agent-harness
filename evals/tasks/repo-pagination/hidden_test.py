"""SYN1：分页契约（现有测试 + 隐藏契约测试）。"""

from __future__ import annotations

import pytest
from books.api import list_books


def test_default_returns_everything():
    payload = list_books()
    assert payload["limit"] is None
    assert payload["offset"] == 0
    assert payload["total"] == 6
    assert [book["id"] for book in payload["items"]] == [1, 2, 3, 4, 5, 6]


def test_limit_and_offset_slice_without_changing_total():
    payload = list_books(limit=2, offset=1)
    assert [book["id"] for book in payload["items"]] == [2, 3]
    assert payload["total"] == 6
    assert payload["limit"] == 2
    assert payload["offset"] == 1


def test_limit_only_starts_at_zero():
    payload = list_books(limit=3)
    assert [book["id"] for book in payload["items"]] == [1, 2, 3]
    assert payload["offset"] == 0


def test_offset_past_end_returns_empty_page():
    payload = list_books(limit=3, offset=10)
    assert payload["items"] == []
    assert payload["total"] == 6


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 101}, {"limit": -1}, {"offset": -1}])
def test_invalid_arguments_raise(kwargs):
    with pytest.raises(ValueError):
        list_books(**kwargs)


def test_service_accepts_pagination():
    from books.service import BookService

    service = BookService()
    page = service.list_books(limit=2, offset=4)
    assert [book.id for book in page] == [5, 6]
    assert len(service.list_books()) == 6
