from __future__ import annotations

from books.models import Book
from books.store import BookStore


class BookService:
    def __init__(self, store: BookStore | None = None) -> None:
        self.store = store or BookStore()

    def list_books(self) -> list[Book]:
        return self.store.all()

    def find(self, book_id: int) -> Book | None:
        return self.store.get(book_id)
