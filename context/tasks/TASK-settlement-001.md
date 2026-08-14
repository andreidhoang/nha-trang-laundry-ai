# TASK-settlement-001 — let an order finish, for the one case that is not a policy question

**Goal:** a staff member records that the customer paid the quoted total and collected the goods, and
the order reaches `COMPLETED` — for the exact-payment self-collection case only.

**Domains:** `orders_audit`, `business_truth`

**Stable work item:** `SETTLEMENT-001`

**Stage:** M4A
**Risk:** HIGH — this touches money and order closure. The risk is scope creep into policy that
belongs to the business owner.

## Why this exists

Measured on 2026-08-14 and provable from three files: **no order this system creates can ever reach
`COMPLETED`.**

- `OrderRepository.create` hardcodes `balance_status = 'UNPAID'` and lets
  `required_delivery_legs_succeeded` and `self_collection_recorded` take their `DEFAULT FALSE`.
  `CreateOrderCommand` exposes none of the three, so a caller cannot supply them.
- The only `UPDATE orders` statement sets the three status dimensions, the resume status, the two
  timestamps and the row version. It touches none of the three fields.
- `transition_commercial` refuses `COMPLETED` unless the order is `ACTIVE`, production is `RELEASED`,
  one of the two fulfilment facts is true, and balance is `PAID` or `ON_ACCOUNT`. Migration
  `0007_operations_control.sql` repeats all four as a table CHECK.

Every order is therefore born unpaid and uncollected, and nothing can change that. The guard is
correct — the system refuses to call an order finished with no evidence the customer paid or
collected. What is missing is the command that supplies the evidence the guard asks for.

## The scope boundary, and why it is drawn here

The guard's condition is `required_delivery_legs_succeeded OR self_collection_recorded`. Only one is
needed. That makes a clean separation available:

**In scope — a fact a staff member witnesses at the counter:**

- the customer paid the exact quoted total, in full, in one settlement, at handover;
- the customer collected the goods themselves.

Neither decides anything. The amount is compared deterministically against the immutable quote
snapshot the order already references; the staff member attests to an event they observed. This is
the same shape as `manual_send_attestations`, which is the pattern to copy.

**Out of scope — every case that is a policy question, and each stays `NOT_SUPPORTED`:**

- partial payment, overpayment, deposits, instalments (`DEC-010`, newly opened, `BUSINESS_OWNER`);
- `ON_ACCOUNT` and B2B credit terms (`DEC-010`);
- refunds, credits and compensation (`DEC-004`);
- delivery legs, dispatch and rider assignment (`DEC-003`) — deferred entirely, which is exactly why
  the self-collection branch is the one being built.

An agent may not decide any of these, and neither may this item. A payment that is not exactly the
quoted total is refused with a typed outcome naming the open decision, not rounded, not accepted
partially, and not recorded as something else.

## Constraints

- **The model never touches this.** Settlement is a staff console command. It is not added to the
  agent tool contract, which is hash-pinned, and no agent-reachable path may write a balance.
- **Deterministic comparison only.** The expected total comes from the quote revision the order
  references, by its snapshot hash. No recomputation, no re-pricing, no tolerance.
- **Attested, attributed, audited.** Who recorded it, when, and against which quote snapshot. Through
  `commit_material_change`, so the row, its domain event, its audit entry and its outbox event commit
  together.
- **Append-only.** A settlement record is never updated or deleted. A mistake is a new incident, not
  an edit. The existing `reject_ledger_mutation` trigger applies.
- **Idempotent.** The same settlement recorded twice is one settlement, enforced by constraint rather
  than by the console hiding a button.
- **Do not widen the guard.** `transition_commercial` and the table CHECK are unchanged. This item
  makes their conditions satisfiable; it does not make them weaker. If a test needs the guard relaxed,
  the test is wrong.

## Required tests

- an order created through the normal path reaches `COMPLETED` after settlement and collection are
  recorded, exercising every intermediate transition;
- payment of an amount other than the quoted total is refused, and the refusal names `DEC-010`;
- the balance and collection fields cannot be written by any agent-reachable path — asserted, not
  assumed;
- recording the same settlement twice yields one record and one audit row;
- an order missing production `RELEASED` is still refused at `COMPLETED`, proving the guard was not
  widened;
- the settlement row cannot be updated or deleted;
- store scoping and RBAC refuse a staff member from another store, with an indistinguishable error.

## Done when

- the exact-payment self-collection path takes an order from creation to `COMPLETED`;
- every other settlement shape returns `NOT_SUPPORTED` naming its open decision;
- `DEC-010` is registered `OPEN` with the `BUSINESS_OWNER` as owner and `NOT_SUPPORTED` as its
  fail-closed behaviour;
- rollback is removing one table, one repository, one route and one console panel; no existing
  table, guard, route or behaviour changed, so reverting returns the order lifecycle to its current
  state rather than breaking it.
