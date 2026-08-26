# TASK-fulfilment-001 — a delivery order can be paid for and closed

**Goal:** implement `DEC-023` as ratified — the customer pays the exact total at the counter before
the laundry leaves, and a named staff member records whether it arrived.

**Domains:** `promotion_delivery_sla`, `orders_audit`

**Stable work item:** `FULFILMENT-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. It adds the second settlement shape this system has ever had, and the code asked
for that to be a visible decision.

## Why this exists

Measured 2026-08-26 by running the full lifecycle for both fulfilment modes against the live
database. Self-collection completed. A delivery order was priced, agreed, taken and washed and then
stopped at two refusals — `settlement shape is not supported`, and `INVALID_STATE_TRANSITION:
fulfillment is incomplete`.

`FULFILMENT-001` had been decision-clear on `DEC-003` since 2026-08-18 and was deliberately not
built, because building the leg writer without deciding the payment question would have produced a
delivery order that reaches "delivered, unpaid, permanently ACTIVE" — worse than not building it,
because it looks finished. Both halves were asked as `DEC-023` and ratified together.

## What the answer changed

**Payment.** `SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY` joins the enum whose comment said
"an enum of one, so adding a second is a visible decision". Both shapes are the same money — the
exact total, in full, in one payment — so `DEC-010` is untouched. `evaluate_settlement` now takes
the order's `fulfillment_mode` rather than inferring from a bool, because two ways to state one fact
can disagree.

**Arrival.** `delivery_legs` records one attempt: which leg, whether it arrived, who says so. **No
money column exists**, and that is the decision showing in the schema — had the answer been "the
driver collects cash", the table would need an amount, a payment method and a reconciliation.

## What must be true when this is done

1. A delivery order reaches `COMPLETED` end to end, through a failed attempt and a retry.
2. `self_collection_recorded` stays FALSE for a prepaid delivery — the customer paid, nobody
   collected, and a leg attests arrival separately.
3. Only a succeeded `RETURN` leg completes fulfilment. A pickup does not; a failure does not.
4. A walk-in order cannot have a delivery recorded at all.
5. One success per leg kind; failures are unlimited, because a retry is a new row.
6. `DEC-010` still refuses part payment on a delivery order exactly as on a walk-in.

## Boundary

No `delivery_bundles`, no `distance_measurements`. Grouping orders into a run and storing measured
distances are separate aggregates nothing has asked for; the `#/gaps` entry is narrowed to name them
rather than deleted.

No driver identity and no driver-facing surface. Staff record the leg, which is what `DEC-023`'s
derived answer to question 2 says and why no mobile surface was built.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```

Plus a delivery order driven to `COMPLETED` against the live database.
