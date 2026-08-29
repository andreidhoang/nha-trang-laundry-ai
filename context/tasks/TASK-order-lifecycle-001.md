# TASK-order-lifecycle-001 — an order can be moved through its whole life, only by its own store

**Goal:** close the defects a nine-lens adversarial verification pass found on 2026-08-27 against
the walk-in and delivery paths, the largest of which is that **no order this system creates could be
moved past `CONFIRMED` by any HTTP caller**.

**Domains:** `orders_audit`, `promotion_delivery_sla`

**Stable work item:** `ORDER-LIFECYCLE-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. It adds two state-changing routes keyed by `order_id` — the same shape as the
cross-store write `STORE-SCOPING-002` closed — and it tightens the order-creation guard, which can
refuse chains that previously succeeded.

## Why this exists

`FULFILMENT-001` recorded a delivery order reaching `COMPLETED` on 2026-08-26. That measurement was
real and it was taken **through the repository and service layer**, not through HTTP. A verification
pass afterwards drove the same lifecycle over the API the console actually calls and every order
stopped dead at `CONFIRMED`: `transition_commercial` refuses `ACTIVE` while intake is not `ACCEPTED`,
and no route could move intake or production. The domain had always supported it; the repository had
always supported it; the product had no way to ask.

That is the finding this item exists for, and the general lesson it carries: **a completion claim
proven one layer below the one a user touches is not proven.**

The same pass found five further defects and two of my own from the previous session. They are
grouped here rather than split because four of them are the same order, the same guard, and the same
test fixtures, and splitting them would mean four passes over one function.

## What must be true when this is done

1. **A staff member can complete an order using only HTTP.** Ticket → intake → quote → acceptance →
   order → every intermediate transition → settlement → `COMPLETED`, with no repository call
   anywhere. Re-measuring this rather than citing the earlier session's claim is what found the
   walk-in wall below; the claim had been broader than its measurement.
2. **The order's customer is the customer its quote was priced for.** Reached through
   `quotes → order_requests → contact_binding_id`. That chain existed and nothing checked it.
3. **The order's fulfilment mode is the mode its quote was priced under.** Read from the `DELIVERY`
   calculation trace inside the immutable snapshot, not from a second field that could disagree.
4. **Intake readiness is derived, not asserted.** Five of the six facts come from the order and its
   bound quote. Only `slot_approved` — capacity, which this system deliberately never decides —
   comes from the operator.
5. **Both new routes are classified for store scope,** and refuse a non-member with the same opaque
   403 every other store refusal produces.
6. **Deriving readiness teaches a non-member nothing.** A stranger's order and an order that does
   not exist must fail identically.
7. **Re-pricing after acceptance works.** *"Thêm cái áo này nữa"* → reprice → chốt again.
8. **The settlement response says what happened,** rather than a hardcoded `True`.

## The two money-misdirection paths, stated plainly

Both were reachable from the orders screen, which builds its request from free-text fields an
operator pastes at a busy counter.

- **Wrong customer.** Issue two counter tickets, quote only the first, create an order for the
  second against the first's accepted quote. It succeeded and settled at the stranger's price, with
  the settlement and the immutable snapshot agreeing with each other.
- **Wrong mode.** Quote a delivery at +10.000đ, create the order as self-collect. The fee for
  transport is kept and the system afterwards refuses to record the transport. The reverse drives
  two delivery legs for an order that paid for none.

## The wall that was found by measuring, not by looking

`POST /stores/{id}/order-requests` checked `contact_channel_bindings` alone, and a counter ticket is
not one. The console issues a ticket with "Phát phiếu", puts the id in the contact field exactly as
`orderRequests.js` documents, and the next request came back `CONTACT_BINDING_UNKNOWN`. **`DEC-013`'s
walk-in path was unreachable through the product**, while `COUNTER-TICKET-001` correctly recorded it
working — that measurement ran through `OrderRepository.create`, which accepts either source. Only
this route, which nothing had driven over HTTP, did not.

## Boundary

**No new decision is taken here.** Every refusal added names an existing rule or an existing
decision; nothing new is authorized, no policy is widened, and no gate moves.

**`PICKUP_ONLY` is not fixed by this item.** `FULFILMENT-001`'s evidence explicitly did not claim it
(*"Not claimed: … that `PICKUP_ONLY` orders behave sensibly beyond falling back to
self-collection"*), and the verification pass confirmed the gap is real: such an order can be paid
in full and then never closed. It is recorded as a finding and left for its own item, because the
fix is a settlement-shape question and this item deliberately touches no settlement policy.

**The remaining verification findings are not fixed here** — an expired quote reachable by
backdating a client timestamp, one accepted revision spawning unlimited orders, an active order that
can never be cancelled, the acceptance route's unused `Idempotency-Key`, and MFA on six read routes.
All are recorded; none is silently closed.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
