# TASK-ops-board-001 — the shop's day, as versioned deterministic queries

**Goal:** show every open order against its SLA deadline, summarise the day, and let the owner export
the shop's own records — each number produced by a named, versioned query.

**Domains:** `orders_audit`, `promotion_delivery_sla`

**Stable work item:** `OPS-BOARD-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM — read-only except the export, which moves shop data out of the system and is
therefore owner-approved and audited.

## Why this exists — and what already exists

**Scoped 2026-09-18. A first reading of this gap said "no SLA board exists". That was wrong, and
building on it would have produced a second, divergent SLA engine.**

What is already built:

| Piece | Where |
|---|---|
| SLA engine, a pure function evaluated on read | `packages/domain/src/nha_trang_laundry_domain/sla.py`, `evaluate_production_sla` |
| the board query | `ShadowConsoleRepository.sla_risk_board`, `shadow_console.py:762` |
| its clock fix | migration `0037` — stops the clock at `ready_at_store` |
| day status counts | `today_status_counts`, already rendered by `#/today` |

`sla_risk_board` already selects in-production, non-cancelled orders and evaluates each through the
domain engine, returning its reason codes verbatim. Migration `0037` fixed a real bug in it: before
that fix "a washed order waiting overnight for its owner accrued elapsed time until it read
`SLA_BREACHED`, and `SLA_MET` was unreachable from this surface entirely." A second board would
re-introduce a bug that has already been found and paid for once.

What is missing is narrower: **a surface a person can work from.** The board's only reachable path
today is `#/assistant`, where `assistant.py:366` answers an SLA question with two counts — how many
orders are in production and how many passed the internal risk mark. A count is not actionable. Staff
cannot see *which* order, *how long* is left, or *what to do first*.

## Required design

**Reuse the query. Do not write SLA logic.** Add a list endpoint and a screen over `sla_risk_board`,
ordered by time remaining with breaches first. If the board needs a field the query does not return,
extend the query in place so `#/assistant` and the board keep answering from one source. A second
SQL statement that computes SLA is a defect, not an optimisation.

**Carry the existing honesty forward.** `SLA_POLICY` is one stated rule, and the assistant already
says so in Vietnamese: *"quy tắc SLA riêng của từng đơn là quyết định kinh doanh chưa được chốt, nên
con số này dùng đúng một quy tắc đã nêu."* Per-order SLA policy is an unresolved business decision.
The board states which rule produced its numbers, in those same words, and never implies the shop
promised a customer anything.

`SlaPolicyType.GUIDANCE_RANGE` carries `GUIDANCE_DOES_NOT_CREATE_BREACH` for exactly this reason.
Guidance is not a promise, and a board that renders guidance as a broken promise lies to its own
staff about what the shop owes.

**Day summary — verify before building.** `#/today` already renders `today_status_counts`. Establish
what is genuinely absent before adding anything, and report it. The `DEC-014` role gate on the
takings figure is preserved exactly — `OWNER_ADMIN`, `OPS_APPROVER`, `OPERATOR` — not widened, no role
added. Wording is house style and non-negotiable: **tiền đã thu**, never *doanh thu*, never *lợi
nhuận*.

**Export — genuinely absent.** `ApprovalAction.EXPORT_SANITIZED_DATA` exists and maps to
`_OWNER_FINANCIAL` with resource type `EXPORT_REQUEST` (`approvals.py:110`, `:128`). An export is an
owner-approved, audited act, not a button. Use the mapping unchanged; do not edit `APPROVAL_POLICIES`.

The word *sanitized* is a requirement, not a label. The export carries order, money and status facts.
It carries **no incident free text and no evidence summary** — those live in disposable payload side
tables under `RETENTION-STORE-001` precisely because they are the sensitive part, and an exported copy
would escape every retention guarantee the purge machinery provides.

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
- The board and `#/assistant` report the same numbers for the same store at the same instant, proving
  there is one query and not two.
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
