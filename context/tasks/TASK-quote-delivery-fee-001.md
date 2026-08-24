# TASK-quote-delivery-fee-001 — no quote this shop can produce shows a customer what they pay

**Goal:** call `evaluate_delivery` from `compose_quote_revision`, so a quote carries the delivery fee
the owner already decided and the total the customer actually pays.

**Domains:** `pricing`, `promotion_delivery_sla`

**Stable work item:** `QUOTE-DELIVERY-FEE-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. This is the first change that puts a customer-facing total into an immutable
revision. It moves no policy: every number comes from a deterministic engine the owner already
ratified.

## Why this exists

Measured 2026-08-24 while triaging `DEC-021`'s four questions. Every one of the five quote revisions
in the live demo database had `delivery_fee_vnd = NULL` and therefore `display_total_min_vnd = NULL`.
**The shop can price laundry and cannot tell a customer the price.**

The cause is not an open decision. `evaluate_delivery`
(`packages/domain/src/nha_trang_laundry_domain/delivery.py:80`) already resolves every case:

| Case | Fee | Outcome |
|---|---:|---|
| `SELF_DROP_SELF_COLLECT` | 0 | `ALLOW` |
| ≤ 2 km | 0 | `ALLOW` |
| 2–6 km | 10,000đ | `ALLOW` |
| > 6 km, fee recorded and acknowledged | staff-negotiated | `ALLOW` |
| > 6 km, not acknowledged | — | `REQUIRE_HUMAN` |
| distance unverified, or one-leg | — | `REQUIRE_HUMAN` |

`DEC-003` ratified the >6km behaviour on 2026-08-18, and the ≤6km schedule is recorded as
**owner-confirmed** in `docs/DECISION_REQUEST_PRICING_POLICY_2026-08.md:34-35`. So the whole schedule
is decided.

**`compose_quote_revision` never called it.** It hardcoded `delivery_fee_vnd=None` and stamped
`DELIVERY_FEE_UNRESOLVED` on every revision — including the walk-in case, where the engine returns a
resolved zero. The snapshot validator then correctly refused to present a total, because an
unresolved fee must not produce a number that reads like a final price. The validator was right; the
input was wrong.

`evaluate_delivery` had no caller on the staff command path at all — only eval fixtures and the agent
tool backend, which production wires to `UnavailableAgentToolBackend`.

**Two stale comments.** `quote_composition.py:75` said "DEC-003 (delivery beyond 6 km) is open" and
:77 said "DEC-002 (promotion eligibility event) is open". Both resolved 2026-08-18.

## Why this is on the critical path, not a side quest

`quotes.py:500` refuses `APPROVED_EXACT` when `totals.delivery_fee_vnd is None`. So **no quote can
become orderable under any `DEC-021` option until the fee resolves.** Whatever the owner answers
about who may chốt and when, this had to land first.

## What must be true when this is done

1. A walk-in quote carries `delivery_fee_vnd = 0` and a display total equal to the service subtotal.
2. The owner-confirmed zone schedule reaches the revision: 0đ under 2km, 10,000đ from 2 to 6km, as an
   exact `DELIVERY` debit adjustment that reconciles against the total.
3. A fee that genuinely needs a human still produces **no total** — the invariant is preserved, it
   just stops being unconditional.
4. `DELIVERY_FEE_UNRESOLVED` appears only when the engine actually returns `REQUIRE_HUMAN`.
5. The revision records which rule produced the fee, in a `DELIVERY` calculation trace.
6. The console asks the mode. `fulfillment_mode` is required with no server default.

## Boundary

**No policy is decided.** Every amount is the engine's. The composer selects nothing, defaults
nothing, and rounds nothing; where the engine says `REQUIRE_HUMAN` the quote stays unresolved.

**`fulfillment_mode` gets no default.** Whether the shop is carrying this laundry is a fact about the
customer's order, and a default would be the server inventing it.

**`DEC-021` is untouched**, and so is `TAX_TREATMENT_UNVERIFIED` — that flag still blocks
`APPROVED_EXACT`, has no registered decision, and is question 4 of the packet. This item removes one
of the three independent reasons no order can be created, not all three.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```

Plus a quote created through `OperationsService.create_quote` against the live database, carrying a
total.
