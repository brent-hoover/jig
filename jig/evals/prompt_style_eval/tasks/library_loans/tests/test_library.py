"""Hidden behavioral tests for the ``library_loans`` task.

One test per stated acceptance criterion (mostly). Tests use the candidate's
own client (``from client import LibraryClient``) which talks to the
candidate's own server (``from app import app``), which persists via the
candidate's own storage module.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    for mod in ("app", "client", "storage"):
        sys.modules.pop(mod, None)
    import app as user_app  # noqa: PLC0415
    from client import LibraryClient  # noqa: PLC0415

    http = TestClient(user_app.app)
    lib = LibraryClient(http_client=http)
    yield lib
    lib.close()


# --- adding books ----------------------------------------------------------


def test_added_book_appears_in_the_listing(library) -> None:
    book = library.add_book("Sapiens", "Yuval Noah Harari")
    titles = [b["title"] for b in library.list_books()]
    assert "Sapiens" in titles
    assert any(b["id"] == book["id"] for b in library.list_books())


def test_newly_added_book_is_available(library) -> None:
    book = library.add_book("Sapiens", "Yuval Noah Harari")
    assert book["available"] is True


# --- listing books ---------------------------------------------------------


def test_default_listing_contains_every_book(library) -> None:
    for title in ("A", "B", "C"):
        library.add_book(title, "anon")
    titles = {b["title"] for b in library.list_books()}
    assert titles == {"A", "B", "C"}


def test_filter_available_excludes_borrowed_books(library) -> None:
    a = library.add_book("A", "anon")
    b = library.add_book("B", "anon")
    library.borrow(a["id"], "alice")

    available_ids = {item["id"] for item in library.list_books(available=True)}
    assert b["id"] in available_ids
    assert a["id"] not in available_ids


def test_filter_unavailable_excludes_open_books(library) -> None:
    a = library.add_book("A", "anon")
    b = library.add_book("B", "anon")
    library.borrow(a["id"], "alice")

    unavailable_ids = {item["id"] for item in library.list_books(available=False)}
    assert a["id"] in unavailable_ids
    assert b["id"] not in unavailable_ids


# --- getting a book --------------------------------------------------------


def test_get_returns_the_book(library) -> None:
    book = library.add_book("Sapiens", "Yuval Noah Harari")
    fetched = library.get_book(book["id"])
    assert fetched["title"] == "Sapiens"
    assert fetched["author"] == "Yuval Noah Harari"


def test_get_missing_book_raises(library) -> None:
    from client import BookNotFound  # noqa: PLC0415

    with pytest.raises(BookNotFound):
        library.get_book("nope-12345")


# --- deleting books --------------------------------------------------------


def test_delete_book_with_no_open_loan_succeeds(library) -> None:
    from client import BookNotFound  # noqa: PLC0415

    book = library.add_book("Sapiens", "anon")
    library.delete_book(book["id"])
    with pytest.raises(BookNotFound):
        library.get_book(book["id"])


def test_delete_book_with_open_loan_raises(library) -> None:
    from client import BookHasOpenLoan  # noqa: PLC0415

    book = library.add_book("Sapiens", "anon")
    library.borrow(book["id"], "alice")
    with pytest.raises(BookHasOpenLoan):
        library.delete_book(book["id"])


def test_delete_missing_book_raises(library) -> None:
    from client import BookNotFound  # noqa: PLC0415

    with pytest.raises(BookNotFound):
        library.delete_book("nope-12345")


# --- borrowing -------------------------------------------------------------


def test_borrowing_creates_a_loan_and_book_becomes_unavailable(library) -> None:
    book = library.add_book("Sapiens", "anon")
    loan = library.borrow(book["id"], "alice")
    assert loan["book_id"] == book["id"]
    assert loan["borrower"] == "alice"
    assert library.get_book(book["id"])["available"] is False


def test_borrowing_already_borrowed_book_raises(library) -> None:
    from client import BookAlreadyBorrowed  # noqa: PLC0415

    book = library.add_book("Sapiens", "anon")
    library.borrow(book["id"], "alice")
    with pytest.raises(BookAlreadyBorrowed):
        library.borrow(book["id"], "bob")


def test_borrowing_missing_book_raises(library) -> None:
    from client import BookNotFound  # noqa: PLC0415

    with pytest.raises(BookNotFound):
        library.borrow("nope-12345", "alice")


def test_due_date_defaults_to_14_days_from_borrow_time(library) -> None:
    book = library.add_book("Sapiens", "anon")
    before = datetime.now(UTC)
    loan = library.borrow(book["id"], "alice")
    after = datetime.now(UTC)

    due = datetime.fromisoformat(loan["due_date"])
    earliest = before + timedelta(days=14, seconds=-2)
    latest = after + timedelta(days=14, seconds=2)
    assert earliest <= due <= latest, (
        f"due_date {due} not within ~14 days of borrow time ({earliest} – {latest})"
    )


def test_borrowed_at_is_set_automatically(library) -> None:
    """Clients don't set borrowed_at — it comes from the server."""
    book = library.add_book("Sapiens", "anon")
    loan = library.borrow(book["id"], "alice")
    assert "borrowed_at" in loan
    # Must be parseable as a timestamp.
    datetime.fromisoformat(loan["borrowed_at"])


def test_borrower_with_explicit_due_date(library) -> None:
    book = library.add_book("Sapiens", "anon")
    due = "2099-01-01T00:00:00+00:00"
    loan = library.borrow(book["id"], "alice", due_date=due)
    assert loan["due_date"] == due


# --- returning -------------------------------------------------------------


def test_returning_makes_book_available_again(library) -> None:
    book = library.add_book("Sapiens", "anon")
    loan = library.borrow(book["id"], "alice")
    library.return_loan(loan["id"])
    assert library.get_book(book["id"])["available"] is True


def test_returned_at_is_set_automatically(library) -> None:
    book = library.add_book("Sapiens", "anon")
    loan = library.borrow(book["id"], "alice")
    returned = library.return_loan(loan["id"])
    assert returned["returned_at"] is not None
    datetime.fromisoformat(returned["returned_at"])


def test_returning_missing_loan_raises(library) -> None:
    from client import LoanNotFound  # noqa: PLC0415

    with pytest.raises(LoanNotFound):
        library.return_loan("nope-12345")


def test_returning_already_returned_loan_raises(library) -> None:
    from client import LoanAlreadyReturned  # noqa: PLC0415

    book = library.add_book("Sapiens", "anon")
    loan = library.borrow(book["id"], "alice")
    library.return_loan(loan["id"])
    with pytest.raises(LoanAlreadyReturned):
        library.return_loan(loan["id"])


# --- listing loans ---------------------------------------------------------


def test_default_loan_listing_contains_every_loan(library) -> None:
    book = library.add_book("A", "anon")
    open_loan = library.borrow(book["id"], "alice")
    library.return_loan(open_loan["id"])
    second = library.borrow(book["id"], "bob")

    listed = library.list_loans()
    ids = {loan["id"] for loan in listed}
    assert open_loan["id"] in ids
    assert second["id"] in ids


def test_filter_open_loans_excludes_returned(library) -> None:
    book = library.add_book("A", "anon")
    returned = library.borrow(book["id"], "alice")
    library.return_loan(returned["id"])
    open_loan = library.borrow(book["id"], "bob")

    listed = library.list_loans(returned=False)
    ids = {loan["id"] for loan in listed}
    assert open_loan["id"] in ids
    assert returned["id"] not in ids


def test_filter_returned_loans_excludes_open(library) -> None:
    book = library.add_book("A", "anon")
    returned = library.borrow(book["id"], "alice")
    library.return_loan(returned["id"])
    open_loan = library.borrow(book["id"], "bob")

    listed = library.list_loans(returned=True)
    ids = {loan["id"] for loan in listed}
    assert returned["id"] in ids
    assert open_loan["id"] not in ids


# --- book history ----------------------------------------------------------


def test_history_lists_loans_oldest_first(library) -> None:
    book = library.add_book("A", "anon")
    first = library.borrow(book["id"], "alice")
    library.return_loan(first["id"])
    second = library.borrow(book["id"], "bob")
    library.return_loan(second["id"])
    third = library.borrow(book["id"], "carol")

    history = library.get_history(book["id"])
    ids_in_order = [loan["id"] for loan in history]
    assert ids_in_order == [first["id"], second["id"], third["id"]]


def test_history_of_never_borrowed_book_is_empty(library) -> None:
    book = library.add_book("Sapiens", "anon")
    assert library.get_history(book["id"]) == []


def test_history_of_missing_book_raises(library) -> None:
    from client import BookNotFound  # noqa: PLC0415

    with pytest.raises(BookNotFound):
        library.get_history("nope-12345")


# --- persistence (coherence between app and storage) -----------------------


def test_books_loans_and_state_survive_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round-trip persistence proves app.py and storage.py agree on field
    names and shapes — if they didn't, the server couldn't read back what
    it wrote."""
    monkeypatch.chdir(tmp_path)
    for mod in ("app", "client", "storage"):
        sys.modules.pop(mod, None)
    import app as first_app  # noqa: PLC0415
    from client import LibraryClient  # noqa: PLC0415

    first = LibraryClient(http_client=TestClient(first_app.app))
    book = first.add_book("Sapiens", "anon")
    open_loan = first.borrow(book["id"], "alice")
    first.close()

    for mod in ("app", "client", "storage"):
        sys.modules.pop(mod, None)
    import app as reloaded_app  # noqa: PLC0415

    second = LibraryClient(http_client=TestClient(reloaded_app.app))
    refetched = second.get_book(book["id"])
    assert refetched["title"] == "Sapiens"
    assert refetched["available"] is False  # open loan persisted

    listed_loans = second.list_loans()
    assert any(loan["id"] == open_loan["id"] for loan in listed_loans)
    second.close()
