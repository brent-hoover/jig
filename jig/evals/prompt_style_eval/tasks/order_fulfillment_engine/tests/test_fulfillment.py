import pytest

from fulfillment import (
    Customer,
    FulfillmentEngine,
    OrderAlreadyCancelled,
    OrderLine,
    OutOfStock,
    Product,
)


def _engine() -> FulfillmentEngine:
    return FulfillmentEngine(
        {
            "book": Product("book", unit_price_cents=1200, weight_grams=400, stock=8),
            "lamp": Product("lamp", unit_price_cents=3500, weight_grams=1200, stock=2),
            "pen": Product("pen", unit_price_cents=150, weight_grams=50, stock=50),
        }
    )


def test_quote_applies_vip_discount_shipping_and_tax_without_mutating_stock() -> None:
    engine = _engine()
    customer = Customer("c1", tier="vip", region="local")

    quote = engine.quote(customer, [OrderLine("book", 2), OrderLine("pen", 1)])

    assert quote.subtotal_cents == 2550
    assert quote.discount_cents == 255
    assert quote.shipping_cents == 500
    assert quote.tax_cents == 223
    assert quote.total_cents == 3018
    assert engine.stock_for("book") == 8


def test_bulk_coupon_uses_percentage_discount_only_when_quantity_threshold_is_met() -> (
    None
):
    engine = _engine()
    customer = Customer("c1", tier="standard", region="local")

    too_small = engine.quote(customer, [OrderLine("pen", 9)], coupon="BULK10")
    eligible = engine.quote(customer, [OrderLine("pen", 10)], coupon="BULK10")

    assert too_small.discount_cents == 0
    assert eligible.discount_cents == 150


def test_save500_subtracts_after_best_percentage_discount_and_never_below_zero() -> (
    None
):
    engine = _engine()

    vip = engine.quote(
        Customer("c1", "vip", "remote"), [OrderLine("book", 1)], "SAVE500"
    )
    tiny = engine.quote(
        Customer("c2", "standard", "local"), [OrderLine("pen", 1)], "SAVE500"
    )

    assert vip.discount_cents == 620
    assert tiny.discount_cents == 150


def test_shipments_split_lines_by_weight_limit_and_preserve_quantity() -> None:
    engine = _engine()

    quote = engine.quote(
        Customer("c1", "standard", "remote"),
        [OrderLine("book", 4)],
    )

    assert len(quote.shipments) == 2
    assert all(shipment.weight_grams <= 1500 for shipment in quote.shipments)
    shipped = sum(
        line.quantity
        for shipment in quote.shipments
        for line in shipment.lines
        if line.sku == "book"
    )
    assert shipped == 4
    assert quote.shipping_cents == 1500


def test_item_heavier_than_shipment_limit_is_rejected() -> None:
    engine = FulfillmentEngine(
        {"anvil": Product("anvil", unit_price_cents=10_000, weight_grams=1600, stock=1)}
    )

    with pytest.raises(ValueError):
        engine.quote(Customer("c1", "standard", "local"), [OrderLine("anvil", 1)])

    assert engine.stock_for("anvil") == 1


def test_place_order_reserves_stock_and_returns_placed_order() -> None:
    engine = _engine()

    order = engine.place_order(
        Customer("c1", "standard", "local"),
        [OrderLine("book", 3), OrderLine("pen", 4)],
    )

    assert order.status == "placed"
    assert order.customer_id == "c1"
    assert engine.stock_for("book") == 5
    assert engine.stock_for("pen") == 46


def test_out_of_stock_order_does_not_partially_mutate_inventory() -> None:
    engine = _engine()

    with pytest.raises(OutOfStock):
        engine.place_order(
            Customer("c1", "standard", "local"),
            [OrderLine("book", 2), OrderLine("lamp", 3)],
        )

    assert engine.stock_for("book") == 8
    assert engine.stock_for("lamp") == 2


def test_unknown_sku_order_does_not_partially_mutate_inventory() -> None:
    engine = _engine()

    with pytest.raises(OutOfStock):
        engine.place_order(
            Customer("c1", "standard", "local"),
            [OrderLine("book", 2), OrderLine("missing", 1)],
        )

    assert engine.stock_for("book") == 8
    assert engine.stock_for("lamp") == 2
    assert engine.stock_for("pen") == 50


def test_duplicate_sku_over_reservation_does_not_mutate_inventory() -> None:
    engine = _engine()

    with pytest.raises(OutOfStock):
        engine.place_order(
            Customer("c1", "standard", "local"),
            [OrderLine("book", 5), OrderLine("book", 5)],
        )

    assert engine.stock_for("book") == 8


def test_cancellation_restores_stock_and_refunds_original_total() -> None:
    engine = _engine()
    order = engine.place_order(Customer("c1", "vip", "local"), [OrderLine("book", 2)])

    refund = engine.cancel_order(order.id)

    assert refund.order_id == order.id
    assert refund.amount_cents == order.total_cents
    assert order.status == "cancelled"
    assert engine.stock_for("book") == 8


def test_cancelling_twice_raises() -> None:
    engine = _engine()
    order = engine.place_order(
        Customer("c1", "standard", "local"), [OrderLine("pen", 1)]
    )
    engine.cancel_order(order.id)

    with pytest.raises(OrderAlreadyCancelled):
        engine.cancel_order(order.id)
