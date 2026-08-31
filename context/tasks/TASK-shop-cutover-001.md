# TASK-shop-cutover-001 — the day the shop starts using it

**Goal:** deploy R1 to the chosen host and prove it works before staff are let in.

**Domains:** `platform`

**Stable work item:** `SHOP-CUTOVER-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** HIGH, and irreducibly so. It is the first time this software holds a real customer's
laundry and a real customer's money.

## Why this exists

Everything before this item is buildable and testable without a host. This is the part that is not.

## What must be true when this is done

1. `docs/runbooks/production-deploy-day.md` is true. Today it is not:
   - §6 tells staff "delivery orders cannot be completed", false since `DEC-023` was signed and
     `FULFILMENT-001` shipped the delivery-leg writer;
   - §1 sends the operator to `scripts/apply_demo_grants.py` without saying that despite its name it
     is the production grant script, and that it must run after **every** migration because
     `GRANT ... ON ALL TABLES` is a one-shot snapshot;
   - §0 does not mention that the console hostname is internal, so no public CA can issue for it,
     and the private CA must be installed *and explicitly trusted* on every tablet — a second step
     on iOS whose failure mode is a console that will not load with no useful error.
2. The database roles exist, including a replication identity for base backups, and
   `scripts/verify_database_grants.py` proves the separation across every table rather than trusting
   it.
3. The store exists, the owner is bound to a verified OIDC subject, and the pricebook is published —
   until which `POST /quotes` returns 503 by design.
4. An external port scan reaches nothing but the console, and only from the operator network.
5. All five capability flags read false on a freshly deployed instance, and
   `scripts/report_delivery_status.py` reports all thirteen capabilities `NOT_AUTHORIZED`. That is
   the expected production state on day one, not a gap.
6. The restore drill has been run, timed, by the person who would run it under pressure.
7. One real transaction by hand at the counter: ticket, quote, acceptance, order, intake,
   production, released, settlement, `COMPLETED`.

## Boundary

No AI capability moves. No signed gate manifest is produced or requested. No channel is connected.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
