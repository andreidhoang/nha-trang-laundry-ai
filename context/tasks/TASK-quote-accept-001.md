# TASK-quote-accept-001 — a customer can agree to a price, and the shop can take the order

**Goal:** implement `DEC-021` as the owner ratified it — a named staff member records that the
customer accepted an exact price — and `DEC-022`, which takes tax out of the acceptance gate.

**Domains:** `pricing`, `orders_audit`

**Stable work item:** `QUOTE-ACCEPT-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** HIGH by position, not by size. This is the first path that produces a revision order
creation will accept, and therefore the first that can commit the shop to a price.

## Why this exists

The owner ratified questions 1, 2 and 4 of `docs/DECISION_REQUEST_QUOTE_APPROVAL_2026-08.md` on
2026-08-25, in Vietnamese. `DEC-021` and `DEC-022` record the words verbatim. Question 3 needed no
decision — the delivery schedule was already decided and `QUOTE-DELIVERY-FEE-001` wired it in.

Three independent conditions refused every order before this item, and they had to clear together:

| | Condition | Cleared by |
|---|---|---|
| 1 | no path produces `APPROVED_EXACT` | this item |
| 2 | `delivery_fee_vnd` is NULL, so no total | `QUOTE-DELIVERY-FEE-001` (2026-08-24) |
| 3 | `TAX_TREATMENT_UNVERIFIED` in `required_approvals` | `DEC-022`, this item |

## The design, and the attempt that was wrong

**First attempt: route the attestation through an approval envelope.** It failed at
`approvals.py:542` — `requester cannot approve their own action`. That rule is right and protects
every approval in the system: an approval is two parties, one asking and one authorising.

What the owner ratified is one party recording a fact they witnessed. Forcing it through the
envelope would have meant weakening separation of duty for sends, cancellations and every financial
action. The resolution itself named the right precedent: `SETTLEMENT-001`, which records money
received in its own table. So acceptance gets `quote_acceptances`, the same shape.

**The accepted revision is derived, never re-priced.** `quote_revisions` is immutable, so acceptance
writes revision N+1 carrying N's exact lines, totals and traces with only finality, status and the
approval fields changed. Re-pricing would let a pricebook republished between reading a price aloud
and the customer's answer bind them to a number they never heard.

**Deriving needs the stored revision back**, and nothing could read one. `parse_quote_revision` does,
and proves itself: whatever it reconstructs is rebuilt through `build_quote_snapshot` and compared
with the digest stored beside the payload, so a wrong parse cannot be used — only fail.

## What must be true when this is done

1. A named staff member's acceptance is recorded once, attributed, append-only, naming the revision,
   its digest, and the revision it produced.
2. The accepted revision carries the same money as the priced one.
3. A quote priced from the customer's own estimate is refused, and the refusal names that fact.
4. Accepting twice is a conflict, not a second attestation.
5. An order can be created against an accepted quote, and cannot be created without one.
6. `TAX_TREATMENT_UNVERIFIED` stays a reason code and stops being a gate.
7. The `#/gaps` entry and order-screen guardrail claiming no writer exists come down, because they
   become false the moment this lands.

## Boundary

Separation of duty is not weakened anywhere. `approvals.py` is unchanged.

This does not make the shop able to serve a walk-in customer: `CreateOrderCommand.bound_contact_id`
is still required and the only writer of a binding is keyed on a channel identity. That is
`DEC-013`/`DEC-015`, still open. An order can be created for a customer who already has a binding;
a walk-in still has no way to get one.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```

Plus a quote priced, accepted and turned into an order against the live database.
