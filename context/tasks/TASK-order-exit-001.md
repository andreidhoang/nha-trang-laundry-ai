# TASK-order-exit-001 — an order that cannot finish and an order that finishes without paying

**Goal:** make production `EXCEPTION` recoverable and guard late cancellation, per the owner's
answer to `DEC-024`.

**Domains:** `orders_audit`

**Stable work item:** `ORDER-EXIT-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** HIGH. It changes the order state machine on the two paths that decide whether the shop
keeps the money and whether the customer gets their laundry back.

## Why this exists

Both defects are stated precisely, with line numbers and options, in
`docs/DECISION_REQUEST_ORDER_EXIT_2026-08.md`. In summary:

1. `EXCEPTION` has zero outgoing edges (`orders.py:181-188`), so a paid order interrupted by a
   stain or a machine fault can never reach any terminal state.
2. `CONFIRMED -> CANCELLED` has no guard, and the one guarded cancellation path is unreachable
   because its two flags default `False` and no caller passes them. The reviewed path always
   refuses; the unreviewed path always succeeds.

## What must be true when this is done

Determined by the signed `DEC-024`. Under the recommended answers:

1. `EXCEPTION` records the state it interrupted, exactly as `ON_HOLD` does, and permits a return to
   it or a move forward once staff resolve the exception.
2. `CONFIRMED -> CANCELLED` is permitted only while production is `NOT_STARTED` and nothing has
   been taken in; otherwise cancellation routes through `CANCELLATION_REVIEW`.
3. `cancellation_approved` and `custody_and_financial_resolution_recorded` are real inputs a named
   staff member sets, carried from the request through `packages/db/.../orders.py:413`, recorded as
   an attributed append-only attestation the way `DEC-021` and `DEC-023` are.
4. The `0008` projection trigger agrees with the domain rather than contradicting it.

## Boundary

This does not add a refund instrument. `DEC-010` deferred partial payment and credit deliberately;
a refund here is a physical act at the counter that the order records having happened.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
