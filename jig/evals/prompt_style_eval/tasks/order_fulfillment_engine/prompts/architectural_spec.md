Create a Python module `fulfillment.py` implementing an in-memory order
fulfillment domain service.

## Public API

```python
Product(sku: str, unit_price_cents: int, weight_grams: int, stock: int)
OrderLine(sku: str, quantity: int)
Customer(id: str, tier: str, region: str)
Shipment(lines: list[OrderLine], weight_grams: int)
OrderQuote(
    subtotal_cents: int,
    discount_cents: int,
    shipping_cents: int,
    tax_cents: int,
    total_cents: int,
    shipments: list[Shipment],
)
Order(
    id: str,
    customer_id: str,
    lines: list[OrderLine],
    total_cents: int,
    status: str,
    shipments: list[Shipment],
)
Refund(order_id: str, amount_cents: int)
OutOfStock
OrderAlreadyCancelled
FulfillmentEngine(products)
```

`FulfillmentEngine` methods:

- `quote(customer, lines, coupon=None) -> OrderQuote`
- `place_order(customer, lines, coupon=None) -> Order`
- `cancel_order(order_id) -> Refund`
- `stock_for(sku) -> int`

## Rules

- Store all money as integer cents. Do not use floats.
- `quote` is side-effect-free for inventory.
- `place_order` is atomic: reject missing SKUs or insufficient stock before
  changing any stock.
- A placed order reserves stock and has status `"placed"`.
- Cancellation restores stock, marks the order `"cancelled"`, and refunds the
  original charged total.
- Cancelling the same order twice raises `OrderAlreadyCancelled`.
- VIP customers receive a 10 percent item-subtotal discount.
- `BULK10` receives a 10 percent item-subtotal discount only when total
  quantity is at least 10.
- VIP and `BULK10` do not stack; choose one 10 percent discount.
- `SAVE500` subtracts 500 cents after any percentage discount. Discounts cannot
  exceed item subtotal.
- Unknown coupons behave like no coupon.
- Shipment max weight is 1500 grams. Split lines if a single line cannot fit in
  the remaining shipment capacity.
- A product whose single unit weighs more than 1500 grams cannot be quoted or
  ordered.
- Local shipping is 500 cents for one shipment or 900 cents for multiple.
- Non-local shipping is 1200 cents plus 300 cents per extra shipment.
- Tax is `floor((subtotal - discount + shipping) * 0.08)`.

Keep boundaries clear enough that pricing, inventory reservation, and shipment
planning could change independently.

Return your solution as a single Python code block.
