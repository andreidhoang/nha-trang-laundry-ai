# TASK-ops-board-001 — the shop's day, as versioned deterministic queries

**Goal:** show every open order against its SLA deadline, summarise the day, and let the owner export
the shop's own records — each number produced by a named, versioned query.

**Domains:** `orders_audit`, `promotion_delivery_sla`

**Stable work item:** `OPS-BOARD-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM — read-only except the export, which moves shop data out of the system and is
therefore owner-approved and audited.

## Why this exists

The SLA engine exists (`packages/domain/.../sla.py`) and nothing asks it about more than one order.
Staff can see an individual order's state; nobody can see which of today's orders is about to breach.
The shop runs its production queue from memory and from the physical rail of bagged laundry.

## Constraint first

Invariant 18: *dashboard numbers, SLA flags, and operational priorities are computed by versioned
deterministic queries/rules; AI may explain them but cannot originate or mutate them.*

So the board is not a screen that adds things up. Each figure is produced by a **named, versioned
query**, and the version identifier travels with the result, so a number on a printout can be traced
to the rule that produced it. A test pins each identifier; changing a rule changes its version.

## Scope, in value order

**1. SLA board.** Every open order against its deadline, ordered by time remaining, breaches first.
The SLA clock starts at `ACCEPTED` and stops at `READY_AT_STORE`; that is already the engine's rule
and this item must not restate it. State whether SLA state is computed on read or stored, and if
computed, prove the board stays correct when the clock crosses a deadline between two reads.

**2. Day summary.** Counts across the four independent status dimensions — commercial, intake,
production, balance — plus the takings figure `#/today` already shows.

The `DEC-014` role gate is preserved exactly: `OWNER_ADMIN`, `OPS_APPROVER`, `OPERATOR`. Do not widen
it and do not add a role. The wording rules are non-negotiable and already house style: **tiền đã
thu**, never *doanh thu*, never *lợi nhuận*. A figure that counts money taken at the counter today is
not revenue and must never be labelled as such.

**3. Export.** `ApprovalAction.EXPORT_SANITIZED_DATA` already exists, maps to `_OWNER_FINANCIAL` and
resource type `EXPORT_REQUEST` (`approvals.py:110`, `:128`). An export is an owner-approved, audited
act, not a button. Use the mapping unchanged.

The word *sanitized* in the enum is a requirement, not a label. The export carries order, money and
status facts. It carries **no incident free text and no evidence summary** — those live in disposable
payload side tables under `RETENTION-STORE-001` precisely because they are the sensitive part, and an
exported copy would escape every retention guarantee the purge machinery provides.

## What this must not become

No trend lines, no forecasts, no *doanh thu*, no per-staff productivity metric, no "orders per hour".
A number the shop cannot act on today is a number that will be wrong tomorrow and believed anyway.
The board answers *what needs a person right now* and *what did we take today*, and stops.

## Constraints

- Every figure carries its query version. No figure is computed in JavaScript.
- Pagination is required, not optional. Demonstrate index coverage with a populated table; do not
  assert it.
- The export writes an audit event naming the approval that authorised it, atomically with the export
  record (invariant 5).
- No new personal-data column. `DEC-027` rests on there being none.
- `APPROVAL_POLICIES` is not edited.

## Required tests

- Each board figure's query version identifier is pinned by a test; changing the rule fails the test
  until the version is changed.
- A year of synthetic orders does not degrade the board beyond a stated bound, measured.
- An order crossing its deadline between two reads shows as breached on the second, with no write.
- A role outside the `DEC-014` set cannot see the takings figure, verified against a real API and not
  only by hiding it in the console.
- An export without an `EXPORT_SANITIZED_DATA` approval is refused.
- An approved export contains no incident free text and no evidence summary — asserted by searching
  the produced bytes for a known incident string.
- The export's audit event names the approval.

## Done when

All of the above pass, `uv run mypy apps packages` is clean, `verify_contracts.py` and
`check_context_drift.py` pass, and a browser run loads the SLA board against a real API with a
breached order visible and correctly ordered.
