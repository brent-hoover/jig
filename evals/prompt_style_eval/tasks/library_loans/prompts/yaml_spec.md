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

**The product's capabilities** are specified below in our project-spec format (a subset — runtime metadata
fields are omitted).

```yaml
name: library_loans
summary: A small library / loans tracker.
capabilities:
  - id: manage-books
    title: Manage books
    state: planned
    user_story:
      as: librarian
      want: to track which books exist and which are out
      benefit: I always know what's in the collection
    behaviors:
      - id: add-book
        description: Add a new book to the collection.
        acceptance_criteria:
          - "After adding, the book appears in the listing."
          - "A newly added book is available (no open loan)."
      - id: list-books
        description: List books, optionally filtered.
        acceptance_criteria:
          - "The default listing contains every book that has been added."
          - "The listing can be filtered to only books that are currently available."
          - "The listing can be filtered to only books that are currently borrowed."
      - id: get-book
        description: Fetch a single book by identifier.
        acceptance_criteria:
          - "Fetching an existing book returns it."
          - "Fetching a book that doesn't exist signals not-found."
      - id: delete-book
        description: Remove a book from the collection.
        acceptance_criteria:
          - "Deleting a book that has no open loan succeeds."
          - "Deleting a book that currently has an open loan signals an error."
          - "Deleting a book that doesn't exist signals not-found."

  - id: manage-loans
    title: Manage loans
    state: planned
    user_story:
      as: librarian
      want: to record who has which book and for how long
      benefit: I can chase down overdue items and know what's been returned
    behaviors:
      - id: borrow
        description: Record a borrower taking a book.
        acceptance_criteria:
          - "Borrowing creates a loan and the book is no longer available."
          - "Borrowing a book that is already borrowed signals an error."
          - "Borrowing a book that doesn't exist signals not-found."
          - "When no due date is given, it defaults to 14 days from the borrow time."
          - "The borrow time is recorded automatically; clients do not set it."
      - id: return
        description: Record a borrower returning a book.
        acceptance_criteria:
          - "After returning, the book is available again."
          - "The return time is recorded automatically; clients do not set it."
          - "Returning a loan that doesn't exist signals not-found."
          - "Returning a loan that has already been returned signals an error."
      - id: list-loans
        description: List loans, optionally filtered.
        acceptance_criteria:
          - "The default listing contains every loan ever made."
          - "The listing can be filtered to only loans that are still open."
          - "The listing can be filtered to only loans that have been returned."

  - id: book-history
    title: Book history
    state: planned
    user_story:
      as: librarian
      want: to see every loan a book has ever been part of
      benefit: I can spot patterns of damage or abuse
    behaviors:
      - id: history
        description: Fetch a book's complete loan history.
        acceptance_criteria:
          - "The history contains every loan ever made against the book."
          - "The history is ordered oldest first."
          - "The history of a book that has never been borrowed is empty."
          - "The history of a book that doesn't exist signals not-found."

  - id: persistence
    title: Persist state
    state: planned
    user_story:
      as: librarian
      want: data to survive when the service restarts
      benefit: my records aren't wiped when something crashes
    behaviors:
      - id: persist
        description: Books, loans, and the current availability state persist across restarts.
        acceptance_criteria:
          - "Books created before a restart are still present after."
          - "Loans created before a restart are still present after."
          - "A book that was borrowed before a restart is still unavailable after."

non_goals:
  - id: no-auth
    text: User accounts, authentication, authorization
    rationale: Single-user / single-trusted-operator scope.
  - id: no-overdue-alerts
    text: Automated overdue notifications
    rationale: Out of scope; consumers can poll for overdue items.
  - id: no-reservations
    text: Holds / reservations on borrowed books
    rationale: Out of scope; only one loan state per book.
```
