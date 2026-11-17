from __future__ import annotations

from books.api import list_books


def test_list_books_shape():
    payload = list_books()
    assert {"items", "total"} <= set(payload)
    assert payload["total"] == 6
    assert len(payload["items"]) == 6
    assert set(payload["items"][0]) == {"id", "title", "author", "year"}


def test_list_books_first_item():
    payload = list_books()
    assert payload["items"][0]["title"] == "The Pragmatic Programmer"
