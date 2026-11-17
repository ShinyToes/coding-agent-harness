from __future__ import annotations

from books.store import BookStore


def test_all_returns_seeded_books():
    store = BookStore()
    assert len(store.all()) == 6
    assert store.all()[0].title == "The Pragmatic Programmer"


def test_get_by_id():
    store = BookStore()
    assert store.get(3).author == "Martin Kleppmann"
    assert store.get(999) is None
