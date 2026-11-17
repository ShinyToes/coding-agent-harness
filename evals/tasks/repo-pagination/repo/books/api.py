from __future__ import annotations

from books.service import BookService


def list_books() -> dict:
    """返回 {"items": [...], "total": N}。"""
    service = BookService()
    books = service.list_books()
    return {"items": [book.to_dict() for book in books], "total": len(books)}
