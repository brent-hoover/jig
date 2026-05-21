You're building a small library / loans tracker. Three files: an HTTP server, a Python client, and a
persistence module. Use Python 3, FastAPI, pydantic v2, and httpx. Standard library plus those three — no
other third-party packages.

**Output format.** Emit three named code blocks, in this order:

```
### storage.py
```python
…persistence code…
```

### app.py
```python
…server code…
```

### client.py
```python
…client code…
```
```

**File contents.**

- `storage.py`: a persistence module for the working directory. Format is up to you (JSON file, sqlite,
  whatever). Used by `app.py` to load and save state. Not imported by clients.
- `app.py`: a FastAPI application. Exposes your FastAPI instance as the module-level `app` variable. Uses
  `storage.py` for persistence.
- `client.py`: a Python client. Must expose the class and exceptions below. Tests call these methods
  directly; the signatures, return shapes, and raised exceptions are the contract:

```python
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
    ) -> None: ...

    def close(self) -> None: ...

    # Books
    def add_book(self, title: str, author: str) -> dict: ...
    def list_books(self, *, available: bool | None = None) -> list[dict]: ...
    def get_book(self, book_id: str) -> dict: ...
    def delete_book(self, book_id: str) -> None: ...
    def get_history(self, book_id: str) -> list[dict]: ...

    # Loans
    def borrow(self, book_id: str, borrower: str, due_date: str | None = None) -> dict: ...
    def return_loan(self, loan_id: str) -> dict: ...
    def list_loans(self, *, returned: bool | None = None) -> list[dict]: ...
```

The wire protocol between client and server (URLs, request/response shapes, status codes) is your design.
The on-disk format and file location used by `storage.py` are your design. Just make all three pieces
agree with each other.

**Books** carry an identifier, title, author, and `available` flag (true when no open loan exists).
**Loans** carry an identifier, the book's identifier, a borrower name, `borrowed_at`, `due_date`, and
`returned_at` (`null` while open). Dates are ISO 8601 strings.

**Why we're building this.** It's a small system for a one-person library: someone wants to keep track of
the books they have, who has borrowed which, and how their books get moved around over time. Three roles
matter — managing the collection, managing active loans, and looking back at history — but the user
operating it is the same librarian throughout.

**As a librarian, I want to manage my collection.** I should be able to add a new book to the catalog,
list everything I have, look up a specific book by id, and remove books that I no longer want in the
collection. When I'm listing things I should be able to narrow down to what's available right now versus
what's currently out. I shouldn't be able to remove a book that someone's actively borrowing — that's a
data integrity problem.

**As a librarian, I want to record loans as they happen and as people return things.** When someone takes
a book, I record who they are and roughly when it's due back. If they don't say, two weeks is a
reasonable default. I shouldn't be able to lend a book that someone else already has out. When someone
brings a book back, I just need to point at the loan; the system should record that it's been returned
and reopen the book for the next borrower. Asking to return something twice, or to return something that
was never out, are obvious errors. I want to be able to list all my loans, with the option to narrow to
the ones still out or the ones that have come back.

**As a librarian, I want a book's full history.** For any book, I should be able to see every loan it's
ever been part of, oldest first, so I can spot if something keeps coming back damaged or always to the
same person. A book that's never been borrowed has no history yet; asking for the history of a book that
doesn't exist is an error.

**As a librarian, I want my records to survive when the service restarts** — none of this is useful if a
crash wipes everything I've recorded.
