from __future__ import annotations

from books.models import Book


class BookStore:
    """内存存储；生产里会换成数据库，这里只保留读接口。"""

    def __init__(self) -> None:
        self._books = [
            Book(1, "The Pragmatic Programmer", "Hunt & Thomas", 1999),
            Book(2, "Refactoring", "Martin Fowler", 1999),
            Book(3, "Designing Data-Intensive Applications", "Martin Kleppmann", 2017),
            Book(4, "The Mythical Man-Month", "Fred Brooks", 1975),
            Book(5, "Working Effectively with Legacy Code", "Michael Feathers", 2004),
            Book(6, "Structure and Interpretation of Computer Programs", "Abelson & Sussman", 1985),
        ]

    def all(self) -> list[Book]:
        return list(self._books)

    def get(self, book_id: int) -> Book | None:
        return next((book for book in self._books if book.id == book_id), None)
