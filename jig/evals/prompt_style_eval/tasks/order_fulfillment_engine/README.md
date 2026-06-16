# order_fulfillment_engine

## What this task is for

This is the greenfield coding half of the Python agent-quality eval corpus. It
is intentionally a little too large for a single clever function: good
solutions tend to separate domain data, pricing, shipment planning, inventory,
and order lifecycle behavior.

## What the hidden tests cover

- Quote calculations with tier discounts, bulk discounts, fixed coupons,
  shipping, and tax.
- Shipment planning that respects a max weight per shipment and keeps all
  ordered quantities.
- Quotes do not mutate inventory.
- Placed orders reserve stock.
- Out-of-stock placement is rejected before partial mutation.
- Cancellation restores stock, returns the charged total, and cannot happen
  twice.

## What this task discriminates

- SOLID-ish boundaries versus one large procedural blob.
- Precise integer-money arithmetic versus floats.
- Explicit domain exceptions versus silent fallback.
- State transition correctness under edge cases.
