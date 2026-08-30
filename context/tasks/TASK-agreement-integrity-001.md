# TASK-agreement-integrity-001 — one agreement, one price, one order

**Goal:** close the three money defects a 62-agent adversarial verification found on 2026-08-29,
the first of which this repository introduced the day before.

**Domains:** `orders_audit`

**Stable work item:** `AGREEMENT-INTEGRITY-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM-HIGH. It tightens the order-creation guard and restructures the acceptance
command. Both are on the only path that takes money.

## Why this exists

Nine lenses, then two skeptics per finding who defaulted to *refuted* unless they personally reran
the reproduction. 25 of 26 findings survived. These are the three that let the shop collect the
wrong amount.

### 1. An order could cite a price the customer had moved on from — **and I caused it**

```
chot #1: rev2 = 100000d
chot #2: rev4 =  50000d   <- what the customer actually agreed to
order against superseded rev2  -> 201 Created
pay the agreed      50000d     -> 422 AMOUNT_IS_NOT_THE_EXACT_TOTAL
pay the superseded 100000d     -> 201 PAID
*** COMPLETED ***
```

The shop collects 100.000đ for a quote re-agreed at 50.000đ, and the system **actively refuses the
amount the customer agreed to**. The order, the settlement and the immutable snapshot all agree
with each other on the stale number, so nothing internally looks wrong.

**It became reachable on 2026-08-29, in `ORDER-LIFECYCLE-001`.** `outbox_events.idempotency_key` is
`UNIQUE`, and the acceptance key was `quote:{quote_id}:acceptance` for *every* acceptance of a
quote. A second acceptance therefore always collided and rolled back — so a quote could never have
two accepted revisions, and a superseded one could not exist. That collision was accidentally the
only thing guarding this. Revision-scoping the key fixed a real 500 on the reprice path (*"thêm cái
áo này nữa"* genuinely did not work) and removed the accident with it. The guard at `orders.py`
asked only that *some* acceptance named the revision, never that it was still the current one.

The lesson worth keeping: **a bug can be load-bearing.** Fixing one exposed a worse one it had been
masking, and the test suite could not see either.

### 2. One agreement could back unlimited orders

Three `POST /orders` with three fresh idempotency keys produced three orders against one
attestation, each independently settleable — 100.000đ agreed once, 300.000đ recorded as collected.
The replay control works; it keys on the caller's header, so a double-submit or a retry with a
regenerated key is a genuinely new order.

`CONVERTED` has been in the `quotes.lifecycle` CHECK since migration `0005` and **nothing ever wrote
it**. Spending the agreement is exactly what that value was for.

### 3. The acceptance route demanded an `Idempotency-Key` and threw it away

The header never reached `self._idempotency.execute`, so nothing was claimed. A retry of a timed-out
acceptance re-executed from scratch, tripped the `expected_current_revision` guard, and told the
operator *"quote moved since it was read; read it to the customer again"* — when their own first
attempt is what moved it. At a counter with a customer waiting, that reads as the system losing the
agreement it just recorded. Two simultaneous acceptances gave one 201 and one **HTTP 500**, from the
`(quote_id, final_revision)` unique constraint the INSERT did not guard.

## What must be true when this is done

1. An order may cite only the customer's most recent agreement on a quote.
2. An agreement authorises exactly one order; the quote is `CONVERTED` when it is spent.
3. Spending is atomic with creating the order, so two concurrent creates cannot both pass.
4. A replayed acceptance returns the stored attestation with `replayed=true`.
5. Losing an acceptance race is a 409, never a 500.
6. An **unresolved** acceptance does not consume the key — staff reweigh and retry with the same one.
7. Every one of the four attacker probes now refuses.

## A judgement call worth naming

Spending the quote means a **cancelled order's quote cannot be re-ordered** — the customer needs a
fresh quote. That is the honest reading of `CONVERTED` (the agreement *did* become an order) and it
is recoverable, since re-quoting is always available. The alternative — a partial unique index
excluding cancelled orders, mirroring `delivery_legs` — is one line away if the owner prefers it.
Recorded rather than assumed.

## Boundary

The other 22 surviving findings are untouched: four authorization HIGHs (approvals, manual sends and
the shadow audit timeline have no membership check at all), two dead console commands, the settlement
panel's false Vietnamese copy, four routes returning 500 on integers ≥ 2⁵³, the unreachable
promotion, and the uncancellable active order. All are recorded and open.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
