### cart_totals.py

```python
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LineItem:
    sku: str
    unit_price_cents: int
    quantity: int


def subtotal_cents(items: Iterable[LineItem]) -> int:
    return sum(item.unit_price_cents * item.quantity for item in items)


def apply_coupon(subtotal_cents: int, code: str | None) -> int:
    match code:
        case None | "":
            return subtotal_cents
        case "SAVE10":
            return subtotal_cents - (subtotal_cents // 10)
        case "FIVEOFF":
            return max(0, subtotal_cents - 500)
        case _:
            return subtotal_cents


def totals_by_sku(items: Iterable[LineItem]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for item in items:
        totals[item.sku] = totals.get(item.sku, 0) + item.unit_price_cents * item.quantity
    return totals
```

### scheduler.py

```python
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Meeting:
    start_minute: int
    end_minute: int


def next_slot(
    existing: Iterable[Meeting],
    duration_minutes: int,
    workday_start: int,
    workday_end: int,
) -> Meeting | None:
    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be positive")

    cursor = workday_start
    meetings = sorted(existing, key=lambda meeting: meeting.start_minute)
    for meeting in meetings:
        if meeting.end_minute <= workday_start or meeting.start_minute >= workday_end:
            continue
        bounded_start = max(meeting.start_minute, workday_start)
        bounded_end = min(meeting.end_minute, workday_end)
        if bounded_start - cursor >= duration_minutes:
            return Meeting(cursor, cursor + duration_minutes)
        cursor = max(cursor, bounded_end)

    if workday_end - cursor >= duration_minutes:
        return Meeting(cursor, cursor + duration_minutes)
    return None
```

### permissions.py

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class User:
    id: str
    role: str
    suspended: bool = False


@dataclass(frozen=True, slots=True)
class Resource:
    id: str
    owner_id: str
    archived: bool = False


def can_access(user: User, action: str, resource: Resource) -> bool:
    if user.suspended:
        return False

    match action:
        case "read":
            return user.role == "admin" or not resource.archived
        case "update":
            return (
                not resource.archived
                and (user.role == "admin" or (user.role == "editor" and user.id == resource.owner_id))
            )
        case "delete":
            return not resource.archived and user.role == "admin"
        case _:
            return False
```

### ledger.py

```python
from __future__ import annotations


class InsufficientFunds(Exception):
    """Raised when a transfer would overdraw the source account."""


class Ledger:
    def __init__(self) -> None:
        self._balances: dict[str, int] = {}

    def create_account(self, account_id: str, opening_balance_cents: int = 0) -> None:
        if opening_balance_cents < 0:
            raise ValueError("opening_balance_cents must be non-negative")
        if account_id in self._balances:
            raise ValueError("account already exists")
        self._balances[account_id] = opening_balance_cents

    def balance(self, account_id: str) -> int:
        return self._balances[account_id]

    def deposit(self, account_id: str, amount_cents: int) -> None:
        self._require_positive_amount(amount_cents)
        self._balances[account_id] += amount_cents

    def transfer(self, source_id: str, target_id: str, amount_cents: int) -> None:
        self._require_positive_amount(amount_cents)
        source_balance = self._balances[source_id]
        self._balances[target_id]
        if source_balance < amount_cents:
            raise InsufficientFunds(source_id)
        self._balances[source_id] = source_balance - amount_cents
        self._balances[target_id] += amount_cents

    @staticmethod
    def _require_positive_amount(amount_cents: int) -> None:
        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")
```
