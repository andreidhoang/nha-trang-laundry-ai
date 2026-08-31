# TASK-reverify-regressions-002 — a replay is still an authenticated request

**Goal:** close the four remaining engineering findings of the 2026-08-31 re-verification.

**Domains:** `orders_audit`

**Stable work item:** `REVERIFY-REGRESSIONS-002`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. It moves existing authorization checks earlier and widens an idempotency payload;
no new capability and no schema change.

## Why this exists

### An idempotency replay bypassed authorization on four write paths

`IdempotencyRepository.execute` short-circuits to the stored response when it finds the key, so
every check living inside the executor is skipped on a replay. A staff member revoked from a store
still received `200` and that store's full order state from a key they were holding, where a fresh
request was correctly refused `403`.

Order transition, settlement, delivery legs and counter tickets all had that shape. `accept_quote`
and `create_quote` already checked outside the wrapper for exactly this reason, and said so in
comments — so the correct pattern was in the codebase, written down, and four later paths did not
follow it.

### `create_quote` hashed a request that was not the request

Its idempotency payload recorded the lines but none of the delivery facts — mode, verified distance,
transport weight, approved manual fee, customer acknowledgement — though every one of them moves the
display total. Correcting a delivery fee and resending the same key replayed the stale total: the
acceptance derived it, the order bound it, and the shop collected **less** than the price it had
quoted, while the correct amount was refused as "not the exact total". A changed quantity conflicted
properly, which is what made the asymmetry a bug rather than a policy.

### Re-sending a shadow draft decision answered 500

`agent_draft_reviews` is UNIQUE on `agent_run_id` — one verdict per draft, which is right — and the
route declared no `Idempotency-Key` parameter, so FastAPI dropped the header `shadow.js` has always
sent. A reviewer who pressed the button twice was told the server broke, with no way to know their
verdict had in fact been recorded.

## What must be true when this is done

1. A revoked member's replay of a key that worked minutes earlier is refused.
2. The pre-check cannot be turned into an oracle for which order ids exist: a missing order returns
   quietly and the write path answers with the same opaque message a non-member gets.
3. The locked check inside each write stays. One answers "may you ask", the other "may you write
   this row", and only the second sees a revocation landing mid-transaction.
4. Changing any delivery fact and resending the same idempotency key conflicts rather than replaying.
5. Pressing a draft decision twice records one verdict and answers twice.

## Boundary

Not fixed here: the counter-day defects carried by `COUNTER-DEFECTS-001`, and the cross-store
refusal-shape leaks, which are deferred for a single-store release.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
