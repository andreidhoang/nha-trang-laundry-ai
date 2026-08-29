# TASK-pickup-only-001 — the order that took money and could never be closed

**Goal:** make a `PICKUP_ONLY` order completable, the way two ratified artifacts already say it
should be.

**Domains:** `orders_audit`, `promotion_delivery_sla`

**Stable work item:** `PICKUP-ONLY-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM-LOW. It changes a settlement branch — the most consequential module in the system —
but adds no shape, no refusal code, and no decision, and it narrows as much as it widens.

## Why this exists

A `PICKUP_ONLY` order — the shop's courier collects the laundry from the customer's home, the
customer comes to the counter for it — could be priced, agreed, taken, washed, **paid in full, and
then never closed**. Found by the verification pass on 2026-08-27 and confirmed against the current
tree on 2026-08-29.

Three files, each correct on its own:

1. `evaluate_settlement` accepted `collected_by_customer=False` for this mode as
   `EXACT_PAYMENT_PREPAID_DELIVERY`, setting `balance_status='PAID'`.
2. `transition_commercial` then requires `required_delivery_legs_succeeded OR
   self_collection_recorded`.
3. `delivery_legs.py` refuses a `RETURN` leg for `PICKUP_ONLY` — correctly, because there is no
   return trip.

So the flag that could complete the order was unreachable by any permitted action, and the other
branch — the customer collecting at the counter — was refused, because settlement compared the mode
against `SELF_DROP_SELF_COLLECT` alone.

## This is a bug, not a decision

The intended behaviour was already written down twice, both under `DEC-023`:

- Migration `0033`: *"`PICKUP_ONLY` has no return leg at all and is completed by self-collection at
  the counter, exactly as a walk-in is."*
- `delivery_legs.py`'s own comment says the same thing, and enforces its half.

It also satisfies both of `DEC-023`'s principles: no driver carries cash (the pickup courier takes
nothing), and the shop is never owed money by somebody holding its laundry (the customer pays when
they collect, which is the walk-in precedent exactly).

**No new `SettlementShape` and no new `SettlementRefusal`.** Either would be a new decision.
`PICKUP_ONLY` + collected is literally `EXACT_PAYMENT_SELF_COLLECTION`: paid at the counter, goods
handed over at the counter. The refusal of the other half reuses
`COLLECTION_WAS_NOT_BY_THE_CUSTOMER` with the same reasoning the `SELF_DROP` branch already uses.

## The one fact, stated once

The two modules disagreed while each held half of the same rule. It now lives in
`catalog.py` next to the enum:

> **A mode expects a return leg exactly when the customer does not collect in person.**

`MODES_EXPECTING_RETURN` is that set. `delivery_legs` reads it to decide which leg is legal;
settlement reads it to decide which side of the counter the order ends on. One definition, two
readers, no way for them to drift apart again.

## What must be true when this is done

1. A `PICKUP_ONLY` order runs ticket → `COMPLETED` over HTTP, through a real succeeded `PICKUP` leg.
2. That pickup leg completes nothing — `completes_fulfillment` is false, as it always was.
3. A `RETURN` leg on such an order is still refused.
4. `PICKUP_ONLY` with `collected_by_customer=False` is now **refused**, not accepted. This is the
   half that was actually taking money.
5. `self_collection_recorded` becomes TRUE — the shape is a domain answer, but what stranded the
   order was the column.
6. A one-leg order with no negotiated fee still presents no total. The fix widens an acceptance
   branch, so the refusals around it must be shown not to have moved.
7. The other three modes behave exactly as before.

## Boundary

Nothing else from the verification pass is fixed here. The expired-quote backdating, the unused
`Idempotency-Key`, unlimited orders per accepted revision, the uncancellable active order, the six
unMFA'd read routes, and the unwired promotion engine all remain recorded in
`ORDER-LIFECYCLE-001`'s evidence and open.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
