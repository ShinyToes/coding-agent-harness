from __future__ import annotations

from books.service import BookService


def test_list_books_returns_all():
    books = BookService().list_books()
    assert len(books) == 6
    assert {book.year for book in books} == {1999, 2017, 1975, 2004, 1985}


def test_find_returns_book_or_none():
    service = BookService()
    assert service.find(1).title == "The Pragmatic Programmer"
    assert service.find(42) is None
