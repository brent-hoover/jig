# review_fix_ladder

## What this task is for

This is the code review/fix half of the Python agent-quality eval corpus. It
measures whether an agent can find common defects, explain them, and produce
minimal corrected Python across several independent modules.

## What the hidden tests cover

- Line-item totals stay in cents, include quantity, and aggregate duplicate
  SKUs.
- Coupons preserve unknown codes and never produce negative totals.
- Meeting scheduling handles unsorted input, exact-fit gaps, and no-slot cases.
- Permission checks deny suspended users, respect ownership, and keep archive
  rules separate from role rules.
- Ledger transfers are atomic, reject invalid money amounts, and preserve total
  balance across accounts.

## What this task discriminates

- Agents that only patch the visible example versus agents that infer the
  general invariant.
- Agents that overfit one module and ignore the harder later modules.
- Agents that preserve APIs while improving internals.
- Agents that use typed, cohesive fixes instead of defensive catch-all code.
