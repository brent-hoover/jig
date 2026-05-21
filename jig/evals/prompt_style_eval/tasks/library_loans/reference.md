### storage.py

```python
from __future__ import annotations

import json
from pathlib import Path

_FILE = Path("library.json")


def load() -> dict:
    if not _FILE.exists():
        return {"books": [], "loans": []}
    return json.loads(_FILE.read_text(encoding="utf-8"))


def save(data: dict) -> None:
    _FILE.write_text(json.dumps(data), encoding="utf-8")
```

### app.py

```python
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import storage


class BookIn(BaseModel):
    title: str
    author: str


class BorrowIn(BaseModel):
    book_id: str
    borrower: str
    due_date: str | None = None


app = FastAPI()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _open_loan_for(book_id: str, loans: list[dict]) -> dict | None:
    for loan in loans:
        if loan["book_id"] == book_id and loan["returned_at"] is None:
            return loan
    return None


def _book_dto(book: dict, loans: list[dict]) -> dict:
    return {
        "id": book["id"],
        "title": book["title"],
        "author": book["author"],
        "available": _open_loan_for(book["id"], loans) is None,
    }


@app.post("/books")
def add_book(payload: BookIn) -> dict:
    data = storage.load()
    book = {"id": str(uuid.uuid4()), "title": payload.title, "author": payload.author}
    data["books"].append(book)
    storage.save(data)
    return _book_dto(book, data["loans"])


@app.get("/books")
def list_books(available: bool | None = None) -> list[dict]:
    data = storage.load()
    result = [_book_dto(b, data["loans"]) for b in data["books"]]
    if available is not None:
        result = [b for b in result if b["available"] is available]
    return result


@app.get("/books/{book_id}")
def get_book(book_id: str) -> dict:
    data = storage.load()
    book = next((b for b in data["books"] if b["id"] == book_id), None)
    if book is None:
        raise HTTPException(404, "book not found")
    return _book_dto(book, data["loans"])


@app.delete("/books/{book_id}", status_code=204)
def delete_book(book_id: str) -> None:
    data = storage.load()
    book = next((b for b in data["books"] if b["id"] == book_id), None)
    if book is None:
        raise HTTPException(404, "book not found")
    if _open_loan_for(book_id, data["loans"]):
        raise HTTPException(409, "book has open loan")
    data["books"] = [b for b in data["books"] if b["id"] != book_id]
    storage.save(data)


@app.get("/books/{book_id}/history")
def get_history(book_id: str) -> list[dict]:
    data = storage.load()
    if not any(b["id"] == book_id for b in data["books"]):
        raise HTTPException(404, "book not found")
    loans = [loan for loan in data["loans"] if loan["book_id"] == book_id]
    loans.sort(key=lambda loan: loan["borrowed_at"])
    return loans


@app.post("/loans")
def borrow(payload: BorrowIn) -> dict:
    data = storage.load()
    if not any(b["id"] == payload.book_id for b in data["books"]):
        raise HTTPException(404, "book not found")
    if _open_loan_for(payload.book_id, data["loans"]):
        raise HTTPException(409, "already borrowed")
    borrowed_at = datetime.now(UTC)
    due_date = payload.due_date or (borrowed_at + timedelta(days=14)).isoformat()
    loan = {
        "id": str(uuid.uuid4()),
        "book_id": payload.book_id,
        "borrower": payload.borrower,
        "borrowed_at": borrowed_at.isoformat(),
        "due_date": due_date,
        "returned_at": None,
    }
    data["loans"].append(loan)
    storage.save(data)
    return loan


@app.get("/loans")
def list_loans(returned: bool | None = None) -> list[dict]:
    data = storage.load()
    loans = list(data["loans"])
    if returned is not None:
        loans = [loan for loan in loans if (loan["returned_at"] is not None) is returned]
    return loans


@app.post("/loans/{loan_id}/return")
def return_loan(loan_id: str) -> dict:
    data = storage.load()
    loan = next((loan for loan in data["loans"] if loan["id"] == loan_id), None)
    if loan is None:
        raise HTTPException(404, "loan not found")
    if loan["returned_at"] is not None:
        raise HTTPException(409, "already returned")
    loan["returned_at"] = _now_iso()
    storage.save(data)
    return loan
```

### client.py

```python
from __future__ import annotations

import httpx


class BookNotFound(KeyError):
    """Raised when no book exists for the given id."""


class LoanNotFound(KeyError):
    """Raised when no loan exists for the given id."""


class BookAlreadyBorrowed(Exception):
    """Raised when borrowing a book that has an open loan."""


class BookHasOpenLoan(Exception):
    """Raised when deleting a book that has an open loan."""


class LoanAlreadyReturned(Exception):
    """Raised when returning a loan that is already returned."""


class LibraryClient:
    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        http_client: httpx.Client | None = None,
    ) -> None:
        self._http = http_client or httpx.Client(base_url=base_url)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "LibraryClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def add_book(self, title: str, author: str) -> dict:
        response = self._http.post("/books", json={"title": title, "author": author})
        response.raise_for_status()
        return response.json()

    def list_books(self, *, available: bool | None = None) -> list[dict]:
        params: dict | None = None
        if available is not None:
            params = {"available": "true" if available else "false"}
        response = self._http.get("/books", params=params)
        response.raise_for_status()
        return response.json()

    def get_book(self, book_id: str) -> dict:
        response = self._http.get(f"/books/{book_id}")
        if response.status_code == 404:
            raise BookNotFound(book_id)
        response.raise_for_status()
        return response.json()

    def delete_book(self, book_id: str) -> None:
        response = self._http.delete(f"/books/{book_id}")
        if response.status_code == 404:
            raise BookNotFound(book_id)
        if response.status_code == 409:
            raise BookHasOpenLoan(book_id)
        response.raise_for_status()

    def get_history(self, book_id: str) -> list[dict]:
        response = self._http.get(f"/books/{book_id}/history")
        if response.status_code == 404:
            raise BookNotFound(book_id)
        response.raise_for_status()
        return response.json()

    def borrow(self, book_id: str, borrower: str, due_date: str | None = None) -> dict:
        body: dict = {"book_id": book_id, "borrower": borrower}
        if due_date is not None:
            body["due_date"] = due_date
        response = self._http.post("/loans", json=body)
        if response.status_code == 404:
            raise BookNotFound(book_id)
        if response.status_code == 409:
            raise BookAlreadyBorrowed(book_id)
        response.raise_for_status()
        return response.json()

    def return_loan(self, loan_id: str) -> dict:
        response = self._http.post(f"/loans/{loan_id}/return")
        if response.status_code == 404:
            raise LoanNotFound(loan_id)
        if response.status_code == 409:
            raise LoanAlreadyReturned(loan_id)
        response.raise_for_status()
        return response.json()

    def list_loans(self, *, returned: bool | None = None) -> list[dict]:
        params: dict | None = None
        if returned is not None:
            params = {"returned": "true" if returned else "false"}
        response = self._http.get("/loans", params=params)
        response.raise_for_status()
        return response.json()
```
