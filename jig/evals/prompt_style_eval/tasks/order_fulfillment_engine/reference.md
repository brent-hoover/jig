### fulfillment.py

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import count


@dataclass(frozen=True, slots=True)
class Product:
    sku: str
    unit_price_cents: int
    weight_grams: int
    stock: int


@dataclass(frozen=True, slots=True)
class OrderLine:
    sku: str
    quantity: int


@dataclass(frozen=True, slots=True)
class Customer:
    id: str
    tier: str
    region: str


@dataclass(frozen=True, slots=True)
class Shipment:
    lines: list[OrderLine]
    weight_grams: int


@dataclass(frozen=True, slots=True)
class OrderQuote:
    subtotal_cents: int
    discount_cents: int
    shipping_cents: int
    tax_cents: int
    total_cents: int
    shipments: list[Shipment]


@dataclass(slots=True)
class Order:
    id: str
    customer_id: str
    lines: list[OrderLine]
    total_cents: int
    status: str
    shipments: list[Shipment]


@dataclass(frozen=True, slots=True)
class Refund:
    order_id: str
    amount_cents: int


class OutOfStock(Exception):
    """Raised when an order cannot be fully reserved."""


class OrderAlreadyCancelled(Exception):
    """Raised when cancelling an order that is already cancelled."""


class FulfillmentEngine:
    _MAX_SHIPMENT_WEIGHT = 1500

    def __init__(self, products: Mapping[str, Product]) -> None:
        self._products = dict(products)
        self._stock = {sku: product.stock for sku, product in products.items()}
        self._orders: dict[str, Order] = {}
        self._ids = count(1)

    def quote(
        self,
        customer: Customer,
        lines: Sequence[OrderLine],
        coupon: str | None = None,
    ) -> OrderQuote:
        self._ensure_available(lines)
        subtotal = self._subtotal(lines)
        shipments = self._plan_shipments(lines)
        discount = self._discount(customer, lines, subtotal, coupon)
        shipping = self._shipping(customer, shipments)
        taxable = subtotal - discount + shipping
        tax = taxable * 8 // 100
        return OrderQuote(
            subtotal_cents=subtotal,
            discount_cents=discount,
            shipping_cents=shipping,
            tax_cents=tax,
            total_cents=taxable + tax,
            shipments=shipments,
        )

    def place_order(
        self,
        customer: Customer,
        lines: Sequence[OrderLine],
        coupon: str | None = None,
    ) -> Order:
        quote = self.quote(customer, lines, coupon)
        for line in lines:
            self._stock[line.sku] -= line.quantity
        order = Order(
            id=f"order-{next(self._ids)}",
            customer_id=customer.id,
            lines=list(lines),
            total_cents=quote.total_cents,
            status="placed",
            shipments=quote.shipments,
        )
        self._orders[order.id] = order
        return order

    def cancel_order(self, order_id: str) -> Refund:
        order = self._orders[order_id]
        if order.status == "cancelled":
            raise OrderAlreadyCancelled(order_id)
        for line in order.lines:
            self._stock[line.sku] += line.quantity
        order.status = "cancelled"
        return Refund(order_id=order.id, amount_cents=order.total_cents)

    def stock_for(self, sku: str) -> int:
        return self._stock[sku]

    def _ensure_available(self, lines: Sequence[OrderLine]) -> None:
        requested: dict[str, int] = {}
        for line in lines:
            if line.quantity <= 0:
                raise ValueError("line quantity must be positive")
            if line.sku not in self._products:
                raise OutOfStock(line.sku)
            requested[line.sku] = requested.get(line.sku, 0) + line.quantity
        for sku, quantity in requested.items():
            if quantity > self._stock[sku]:
                raise OutOfStock(sku)

    def _subtotal(self, lines: Sequence[OrderLine]) -> int:
        return sum(self._products[line.sku].unit_price_cents * line.quantity for line in lines)

    def _discount(
        self,
        customer: Customer,
        lines: Sequence[OrderLine],
        subtotal_cents: int,
        coupon: str | None,
    ) -> int:
        total_quantity = sum(line.quantity for line in lines)
        percent_discount = subtotal_cents // 10 if customer.tier == "vip" else 0
        if coupon == "BULK10" and total_quantity >= 10:
            percent_discount = max(percent_discount, subtotal_cents // 10)
        fixed_discount = 500 if coupon == "SAVE500" else 0
        return min(subtotal_cents, percent_discount + fixed_discount)

    def _shipping(self, customer: Customer, shipments: Sequence[Shipment]) -> int:
        if not shipments:
            return 0
        extra = len(shipments) - 1
        if customer.region == "local":
            return 500 if len(shipments) == 1 else 900
        return 1200 + (extra * 300)

    def _plan_shipments(self, lines: Sequence[OrderLine]) -> list[Shipment]:
        shipments: list[Shipment] = []
        current_lines: list[OrderLine] = []
        current_weight = 0
        for line in lines:
            product = self._products[line.sku]
            remaining = line.quantity
            while remaining > 0:
                capacity = self._MAX_SHIPMENT_WEIGHT - current_weight
                quantity_fit = max(1, capacity // product.weight_grams)
                quantity = min(remaining, quantity_fit)
                added_weight = quantity * product.weight_grams
                if current_weight + added_weight > self._MAX_SHIPMENT_WEIGHT and current_lines:
                    shipments.append(Shipment(lines=current_lines, weight_grams=current_weight))
                    current_lines = []
                    current_weight = 0
                    continue
                current_lines.append(OrderLine(line.sku, quantity))
                current_weight += added_weight
                remaining -= quantity
                if current_weight == self._MAX_SHIPMENT_WEIGHT:
                    shipments.append(Shipment(lines=current_lines, weight_grams=current_weight))
                    current_lines = []
                    current_weight = 0
        if current_lines:
            shipments.append(Shipment(lines=current_lines, weight_grams=current_weight))
        return shipments
```
