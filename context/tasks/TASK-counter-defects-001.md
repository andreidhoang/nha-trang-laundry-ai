# TASK-counter-defects-001 — the defects a counter day reaches

**Goal:** close the findings of the 2026-08-31 re-verification that a single shop reaches in an
ordinary day of trading and that involve money or a crash.

**Domains:** `orders_audit`

**Stable work item:** `COUNTER-DEFECTS-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. Four refusals that should already have existed, one status write, one copy
correction, one test that was passing vacuously.

## Why this exists

R1 puts a real shop on this software. The triage rule for what must be fixed before that is
**reachable in a single-store counter day, and involving money or a crash.** Six findings qualify.

1. **HTTP 500 on an intake or production transition against a cancelled order.** The `0008`
   projection trigger raises on any UPDATE of a row whose `commercial_status` is `CANCELLED`,
   including an intake-only or production-only column change. `transition_intake` and
   `transition_production` never read `state.commercial`, so the domain returns a valid next state
   and the mutation trips the trigger. `psycopg.errors.RaiseException` is outside the routes' except
   tuples (`main.py:891`, `:923`) and escapes as a 500.
2. **Quote expiry is compared against a client-supplied timestamp.** `packages/db/.../orders.py:230-242`
   compares `command.customer_final_quote_accepted_at` — the request body field, validated only for
   timezone-awareness — against `valid_until`. `datetime.now()` is never consulted on this path, so
   an expired quote is orderable by claiming an earlier acceptance time. This is money.
3. **A `PICKUP` leg is accepted on a `RETURN_ONLY` order.** `delivery_legs.py:105` checks only the
   `RETURN` case; there is no `MODES_EXPECTING_PICKUP` counterpart to `MODES_EXPECTING_RETURN`. The
   row and its outbox event are written and close nothing.
4. **Every `order_requests` row is `DRAFT` for its whole life.** No code writes `status`; the CHECK
   values `SUBMITTED` and `CANCELLED` are unreachable. A request whose order was cancelled stays in
   "Tiếp nhận gần đây" indistinguishable from live intake.
5. **`#/gaps` denies three surfaces the console ships.** Its lede says intake, production, delivery
   and payment "have no surface"; three of the four do. Compliance copy that states the opposite of
   the truth is the same defect class as the settlement panel closed in `BOUNDS-AND-TRUTH-001`.
6. **The console-contract test passes vacuously on one call.** It classifies a call as mutating only
   via the literal regex `method:\s*"(POST|PATCH|PUT|DELETE)"`. `staff.js:689` supplies the method
   as a ternary and is therefore invisible to it — and that call is the only writer of
   `staff_store_assignments`, which every membership check in the system depends on.

## What must be true when this is done

1. An intake or production transition against a cancelled order refuses with a typed 4xx naming the
   cause, not a 500. The refusal is decided in the domain, where the other lifecycle rules live.
2. Quote expiry is decided against the server's clock.
3. A leg kind the order's fulfilment mode does not expect is refused, in both directions.
4. An order request reaches a terminal status when its order does.
5. `#/gaps` denies only what is actually unbuilt.
6. The contract test sees a call whose method is an expression.

## Boundary

Not fixed here: `EXCEPTION` recovery and late cancellation, which need `DEC-024` and are carried by
`ORDER-EXIT-001`. Deferred for a single-store release, with the reason recorded: the cross-store
refusal-shape leaks (there is no second store to probe), the six read routes that skip MFA,
`policy_version` verification, and the unwired promotion engine.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
