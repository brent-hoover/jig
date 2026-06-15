You are reviewing four small Python modules. Each has common defects. Review
the code, then return corrected implementations for all four files.

Keep the public API names exactly as shown. Standard library only. Include type
hints. Return each corrected file as its own fenced Python code block with a
markdown heading naming the file.

## 1. cart_totals.py

```python
from dataclasses import dataclass


@dataclass
class LineItem:
    sku: str
    unit_price_cents: int
    quantity: int


def subtotal_cents(items):
    total = 0
    for item in items:
        total += item.unit_price_cents
    return total / 100


def apply_coupon(subtotal_cents, code):
    if code == "SAVE10":
        return subtotal_cents * 0.9
    if code == "FIVEOFF":
        return subtotal_cents - 500
    return 0


def totals_by_sku(items):
    totals = {}
    for item in items:
        totals[item.sku] = item.unit_price_cents
    return totals
```

Expected behavior:

- Totals are integer cents, never dollars or floats.
- Quantity must be included.
- Duplicate SKUs aggregate.
- `None`, empty, and unknown coupon codes leave the subtotal unchanged.
- `SAVE10` takes 10 percent off, rounded down to integer cents.
- `FIVEOFF` subtracts 500 cents but never below zero.

## 2. scheduler.py

```python
from dataclasses import dataclass


@dataclass
class Meeting:
    start_minute: int
    end_minute: int


def next_slot(existing, duration_minutes, workday_start, workday_end):
    cursor = workday_start
    for meeting in existing:
        if meeting.start_minute - cursor > duration_minutes:
            return Meeting(cursor, cursor + duration_minutes)
        cursor = meeting.end_minute
    if cursor + duration_minutes < workday_end:
        return Meeting(cursor, cursor + duration_minutes)
    return Meeting(workday_start, workday_start + duration_minutes)
```

Expected behavior:

- Existing meetings may be unsorted.
- Meetings outside the workday should not move the search cursor.
- A gap exactly equal to the requested duration is valid.
- Return `None` if no slot fits.
- Reject non-positive durations with `ValueError`.

## 3. permissions.py

```python
from dataclasses import dataclass


@dataclass
class User:
    id: str
    role: str
    suspended: bool = False


@dataclass
class Resource:
    id: str
    owner_id: str
    archived: bool = False


def can_access(user, action, resource):
    if user.role == "admin":
        return True
    if action == "read":
        return True
    if action == "update":
        return user.role in ("editor", "admin")
    if action == "delete":
        return user.id == resource.owner_id
    return True
```

Expected behavior:

- Suspended users cannot do anything.
- Unknown actions are denied.
- Archived resources are read-only for admins and inaccessible to everyone
  else.
- Viewers can read non-archived resources.
- Editors can read non-archived resources and update only their own
  non-archived resources.
- Only admins can delete non-archived resources.

## 4. ledger.py

```python
class InsufficientFunds(Exception):
    pass


class Ledger:
    def __init__(self):
        self.accounts = {}

    def create_account(self, account_id, opening_balance_cents=0):
        self.accounts[account_id] = opening_balance_cents

    def balance(self, account_id):
        return self.accounts.get(account_id, 0)

    def deposit(self, account_id, amount_cents):
        self.accounts[account_id] += amount_cents

    def transfer(self, source_id, target_id, amount_cents):
        self.accounts[source_id] -= amount_cents
        if self.accounts[source_id] < 0:
            raise InsufficientFunds(source_id)
        self.accounts[target_id] += amount_cents
```

Expected behavior:

- Missing accounts are errors, not implicit zero-balance accounts.
- Opening balances, deposits, and transfer amounts must be positive integer
  cents, except an opening balance may be zero.
- Insufficient transfers leave both accounts unchanged.
- Successful transfers preserve total money across all accounts.
