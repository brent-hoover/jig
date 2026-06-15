Build `fulfillment.py`, a small in-memory order fulfillment engine for Python
3. Use the standard library only.

The module should expose these names:

- `Product(sku: str, unit_price_cents: int, weight_grams: int, stock: int)`
- `OrderLine(sku: str, quantity: int)`
- `Customer(id: str, tier: str, region: str)`
- `Shipment(lines: list[OrderLine], weight_grams: int)`
- `OrderQuote(subtotal_cents, discount_cents, shipping_cents, tax_cents,
  total_cents, shipments)`
- `Order(id, customer_id, lines, total_cents, status, shipments)`
- `Refund(order_id: str, amount_cents: int)`
- exceptions `OutOfStock` and `OrderAlreadyCancelled`
- class `FulfillmentEngine(products)` with methods:
  - `quote(customer, lines, coupon=None) -> OrderQuote`
  - `place_order(customer, lines, coupon=None) -> Order`
  - `cancel_order(order_id) -> Refund`
  - `stock_for(sku) -> int`

Business behavior:

- Money is always integer cents.
- `quote` computes prices but does not reserve inventory.
- `place_order` rejects missing SKUs or insufficient stock before mutating any
  inventory.
- `place_order` reserves stock and returns an order with status `"placed"`.
- `cancel_order` restores reserved stock, changes status to `"cancelled"`, and
  returns a refund for the order total.
- Cancelling an already-cancelled order raises `OrderAlreadyCancelled`.
- Customer tier `"vip"` gets 10 percent off the item subtotal.
- Coupon `"BULK10"` gives 10 percent off only when total ordered quantity is at
  least 10.
- If both percentage discounts apply, use only the better percentage discount,
  not both.
- Coupon `"SAVE500"` subtracts 500 cents after percentage discounts, but total
  item discount cannot exceed the subtotal.
- Unknown or absent coupons give no coupon discount.
- Shipping for region `"local"` is 500 cents for one shipment and 900 cents
  total for multiple shipments.
- Shipping for any other region is 1200 cents plus 300 cents for each shipment
  after the first.
- Tax is 8 percent of `(subtotal - discount + shipping)`, rounded down.
- Shipment planning must keep each shipment at or under 1500 grams. Split a
  line across shipments when needed.

Return your solution as a single Python code block.
