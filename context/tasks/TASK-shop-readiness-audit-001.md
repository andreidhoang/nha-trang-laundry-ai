# TASK-shop-readiness-audit-001 — use the software, then fix what using it reveals

**Goal:** drive the console as a member of staff against a real API and a real database, audit every
layer against that, and fix what a shop would actually meet.

**Domains:** `platform`, `orders_audit`, `business_truth`

**Stable work item:** `SHOP-READINESS-AUDIT-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. Most changes are copy, refusal mapping and console state; two touch the order
clock and the release-gate schema, and one re-derives a hash-pinned evidence bundle.

---

## 1. Why this exists

The suite was green, the contract checks passed, and the console had never been signed into as
anything but the owner against stubbed responses. Everything found here was found by doing one of
two things: working a shop day in a browser against a real backend, or reading a layer with a
question that the tests do not ask.

The two classes it turned up are worth naming, because they explain why a green suite missed them:

**The console said things that were not true.** Not vaguely — the intake screen told staff a
walk-in could not be served and to go back to paper, twenty lines under the button that serves
them. A test that renders a screen cannot tell that its prose contradicts its own form.

**Refusals were mapped by hand, per route, and two were missed.** `StoreAccessError` is a
`PermissionError`, so a route catching `ValueError` lets it escape as a 500 — which tells a client
to retry. It had already shipped once on `create_order`; it was live on `record_settlement`, the
route that moves money.

## 2. Scope

Two audit rounds, seven and five dimensions, each finding adversarially verified by a second agent
whose default was REFUTED. 19 findings confirmed in the first round, 14 in the second — four of
which were defects in the first round's own fixes, including one HIGH.

Fixed, grouped by what a shop would notice:

| | |
|---|---|
| **Money** | a settlement typed as the total the screen renders (`132.000`) parsed to 132; a negotiated delivery fee truncated silently; the distance boundary stated one metre wrong; the delivery-fee rule stated as if it applied to all four fulfilment modes when two never use it |
| **A write whose outcome is unknown** | on TIMEOUT and NETWORK, three screens asserted the write had not happened. It may have. Creating a second order is not cheaply undone |
| **500s that told a client to retry** | a non-member settling; a duplicate OIDC subject; an empty `Idempotency-Key`; a concurrent second sign-out |
| **A retry that was not one** | the intake command put a server clock inside its own idempotency identity, so the same key for the same intent hashed differently and answered CONFLICT |
| **The SLA board** | measured against `now`, with no column able to say when the laundry was finished, so every order collected the next morning read BREACHED and MET was unreachable |
| **The offline story** | a wifi blip presented as a sign-out; a failed store read presented as "your account has no shop"; the ticket button and the price attestation stayed lit with the network down; coming back online re-armed buttons held busy by their own in-flight write |
| **The service worker** | the fingerprinted shell cache could be filled from the browser's HTTP cache, so a deploy could be served pre-deploy JavaScript on the one tablet that had the console open; and an HTTP error response was handed to the page as a module |
| **Permission** | two screens with write controls consulted no capability at all, and the network sync cleared the disabled state that the permission gate had set |
| **Deploy and recovery** | a WAL segment could be archived truncated and reported as success; two cron entries named a directory nothing creates; the restore drill halted on its own first command, on the stopwatch |
| **The gate ladder** | G3 asks for the first 200 automated sends to be human-reviewed and no numeric floor existed at the stage it governs |

## 3. What was deliberately not done

- **No AI capability moved.** All 13 stay `NOT_AUTHORIZED`; the worker still refuses to start if any
  external-capability flag is true, verified by setting each one.
- **No consent state became reachable.** `suppression_entries.CLEAR` is still unwritable and every
  marketing send is still held.
- **`AGENT-001` was not resumed.** Its evidence bundle was re-derived under the procedure
  `EVIDENCE-REPIN-001` established and the owner approved — script first, then capture, superseded
  bundle retained byte for byte — because the release-gate schema it pins had to change. Two pins
  moved and every case status, release blocker and runtime path is identical between bundles.

## 4. Acceptance checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```

Plus, and this is the point of the item:

- `scripts/verify_daily_operations.py` walks two full days in a browser against a real API and a
  real database — a walk-in who collects, and a delivery order with a failed leg then a successful
  one — and checks what the shop must refuse and what a read-only role sees;
- `scripts/verify_console_interaction.py` covers what only a browser can see: typing, focus, what
  survives a 401, and that coming back online undoes only what going offline did;
- every defect above was reproduced before it was changed and re-checked after.
