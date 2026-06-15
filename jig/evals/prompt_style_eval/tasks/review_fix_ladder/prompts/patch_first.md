Fix the following Python code. Return only corrected source files, one fenced
Python block per file, with headings `cart_totals.py`, `scheduler.py`,
`permissions.py`, and `ledger.py`.

Do not change the public API names. Prefer small, direct fixes over new
frameworks or broad abstractions.

The defects are intentionally varied:

- `cart_totals.py` mishandles cents, quantities, duplicate SKUs, and coupons.
- `scheduler.py` misses valid gaps and mishandles unsorted or out-of-day
  meetings.
- `permissions.py` conflates role, ownership, archive, unknown-action, and
  suspension rules.
- `ledger.py` silently creates balances, accepts invalid amounts, and mutates
  the source account before proving a transfer can succeed.

```python
# cart_totals.py
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

```python
# scheduler.py
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

```python
# permissions.py
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

```python
# ledger.py
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
