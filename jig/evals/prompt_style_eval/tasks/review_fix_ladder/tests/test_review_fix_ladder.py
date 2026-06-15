from cart_totals import LineItem, apply_coupon, subtotal_cents, totals_by_sku
from ledger import InsufficientFunds, Ledger
from permissions import Resource, User, can_access
from scheduler import Meeting, next_slot


def test_subtotal_uses_quantity_and_integer_cents() -> None:
    items = [LineItem("A", 199, 3), LineItem("B", 250, 2)]

    assert subtotal_cents(items) == 1097
    assert isinstance(subtotal_cents(items), int)


def test_totals_by_sku_aggregates_duplicates() -> None:
    items = [LineItem("A", 100, 2), LineItem("B", 50, 1), LineItem("A", 75, 4)]

    assert totals_by_sku(items) == {"A": 500, "B": 50}


def test_coupon_rules_preserve_unknowns_and_floor_at_zero() -> None:
    assert apply_coupon(1234, None) == 1234
    assert apply_coupon(1234, "NOPE") == 1234
    assert apply_coupon(999, "SAVE10") == 900
    assert apply_coupon(300, "FIVEOFF") == 0


def test_scheduler_finds_earliest_gap_from_unsorted_meetings() -> None:
    meetings = [Meeting(780, 840), Meeting(540, 600), Meeting(660, 720)]

    assert next_slot(meetings, 60, 540, 900) == Meeting(600, 660)


def test_scheduler_accepts_exact_fit_and_ignores_out_of_day_meetings() -> None:
    meetings = [Meeting(480, 500), Meeting(600, 660), Meeting(900, 930)]

    assert next_slot(meetings, 60, 540, 660) == Meeting(540, 600)


def test_scheduler_returns_none_when_no_slot_fits() -> None:
    meetings = [Meeting(540, 600), Meeting(600, 660), Meeting(660, 720)]

    assert next_slot(meetings, 30, 540, 720) is None


def test_permissions_deny_suspended_and_unknown_actions() -> None:
    resource = Resource("r1", owner_id="alice")

    assert not can_access(User("root", "admin", suspended=True), "delete", resource)
    assert not can_access(User("alice", "editor"), "publish", resource)


def test_permissions_apply_role_owner_and_archive_rules() -> None:
    owned = Resource("r1", owner_id="alice")
    archived = Resource("r2", owner_id="alice", archived=True)

    assert can_access(User("bob", "viewer"), "read", owned)
    assert can_access(User("alice", "editor"), "update", owned)
    assert not can_access(User("bob", "editor"), "update", owned)
    assert can_access(User("root", "admin"), "delete", owned)
    assert not can_access(User("root", "admin"), "delete", archived)
    assert can_access(User("root", "admin"), "read", archived)
    assert not can_access(User("alice", "editor"), "read", archived)


def test_ledger_transfer_is_atomic_on_insufficient_funds() -> None:
    ledger = Ledger()
    ledger.create_account("a", 100)
    ledger.create_account("b", 25)

    try:
        ledger.transfer("a", "b", 101)
    except InsufficientFunds:
        pass

    assert ledger.balance("a") == 100
    assert ledger.balance("b") == 25


def test_ledger_rejects_invalid_amounts_and_preserves_total() -> None:
    ledger = Ledger()
    ledger.create_account("a", 1000)
    ledger.create_account("b", 0)
    ledger.deposit("b", 250)

    try:
        ledger.deposit("a", 0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero deposit should fail")

    ledger.transfer("a", "b", 400)
    assert ledger.balance("a") == 600
    assert ledger.balance("b") == 650
