# library_loans

A small library / loans tracker. Books can be added, borrowed, returned, and looked up; a loan tracks who has
a book, when they took it, when it's due, and when (if) they returned it. Three files (`app.py`, `client.py`,
`storage.py`), ~150–200 LoC reference solution.

The task is designed to put pressure on two specific failure modes Claude tends toward:

- **Completeness**: enumerated requirements (default due-date, auto-set borrowed-at/returned-at, error on
  re-borrow, error on double-return, history ordering, filter combinations) are easy to drop on the floor if
  the model only implements what the happy path strictly needs.
- **Coherence**: three separate modules that must agree on field names, IDs, and shapes; tests round-trip
  through them via the candidate's own client.

Reference for prompt authors and reviewers — never sent to the model.
