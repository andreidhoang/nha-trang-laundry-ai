# TASK-quote-command-001 — give the pricing engine a way in

**Goal:** a staff member prices a real garment through the deterministic engine and the result is
committed as an immutable quote revision.

**Domains:** `pricing`, `orders_audit`

**Stable work item:** `QUOTE-COMMAND-001`

**Stage:** M4A
**Risk:** HIGH — this is the first command path that produces a monetary artefact. The risk is that
convenience creeps into the money path.

## Why this exists

`DOMAIN-003` built the pricing engine, `DOMAIN-004` the promotion, delivery and SLA boundaries, and
`DOMAIN-005` immutable quote snapshots with reproducible calculation traces. Together they are 3,365
lines and the most valuable code in this repository.

Measured on 2026-08-14: **none of it is reachable.** `QuoteRepository.create_revision` is called from
`packages/evals` synthetic harnesses and from tests, and from no application.
`apps/api` imports `QuoteRepository` and uses exactly one method, `list_for_store`. There is a
`GET /internal/v1/stores/{store_id}/quotes` and no `POST`.

The consequence is that quotes must arrive from somewhere else, and there is nowhere else. Every
downstream capability — orders, SLA, delivery advice, the agent's estimate tool — sits behind an
artefact nothing can produce.

## What to build

A command path from an authenticated staff request to a committed `quote_revisions` row.

The route does not compute money. It validates its input, calls the domain engine, and persists what
the engine returns. If a reviewer can find arithmetic in the route layer, the item is not done.

## Constraints

- **The engine is the only authority on price.** No rounding, no minimum, no tier boundary and no
  fee may be recomputed, adjusted or defaulted outside `packages/domain`. The 6 kg cliff is a
  confirmed business rule, not an edge case to smooth.
- **Unresolved policy propagates.** Where the engine returns `REQUIRE_HUMAN` or `NOT_SUPPORTED`, the
  response carries that outcome and its reason codes verbatim. It is never converted into a price,
  a default, or a silent omission. `DEC-001` (weight precision and rounding), `DEC-002` (promotion
  eligibility event) and `DEC-003` (delivery beyond 6 km) are open, and a quote that touches an
  unresolved region must say so rather than resolve it.
- **The snapshot hash is computed by the existing function.** Do not reimplement JCS canonicalisation
  or the `JCS-SHA256-V1:` prefix. The database CHECK is the second line of defence, not the first.
- **The write goes through `commit_material_change`.** Row, domain event, audit entry and outbox
  event commit together or not at all. Copy the existing pattern in `operations.py`; do not invent a
  second transaction shape.
- **Revisions are append-only.** A correction is a new revision, never an update. The existing
  `expected_revision` compare-and-swap is the concurrency control.
- **Store scoping and RBAC apply.** The same membership check the Shadow surfaces use. A staff member
  who does not belong to the store gets the same 403 as one whose role lacks the right, so probing
  identifiers teaches nothing.
- This is a staff console command. It is **not** a new agent tool; the agent tool contract is
  hash-pinned and already contains `quoteEstimate`.

## Required tests

- a quote priced through the route matches a direct call to the engine for the same input — the
  route cannot drift into computing anything;
- boundary coverage at, below and above the 6 kg tier and the billable minimum;
- an input that lands in unresolved policy returns the engine's outcome and writes no row;
- the persisted snapshot hash verifies against a recomputation from the stored payload;
- revision 2 with a stale `expected_revision` is refused;
- a staff member from another store is refused, and the refusal is indistinguishable from a role
  refusal;
- the domain event, audit row and outbox row all exist after a successful call, and none exists
  after a failed one.

## Done when

- a `POST` route creates a quote revision through the domain engine;
- the console can price a garment and show the result with its reason codes;
- no monetary arithmetic exists outside `packages/domain`;
- rollback is removing one route, one service method and one repository call — no migration, no
  schema change, no existing behaviour touched.
