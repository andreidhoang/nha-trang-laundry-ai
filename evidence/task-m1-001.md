# TASK-m1-001 — PostgreSQL migration and transaction foundation

**Status:** Complete; local live-PostgreSQL validation recorded.  
**Date:** 2026-07-28  
**Context domains:** `platform`, `orders_audit`

## Requirement and contract

- `context/INVARIANTS.md` items 1, 5, 6 and 7.
- `specs/SECURITY_RELIABILITY_SPEC_V1.md` §9.1: a material command atomically commits its mutation,
  domain event, append-only audit event and all required outbox events.
- `BUILD_ENGINEERING_SPEC.md` §6.1: SQL-first forward-only migrations and typed persistence boundary.

## Delivered

- `packages/db/migrations/0001_transaction_foundation.sql` creates the event, audit and outbox
  ledgers, indexes, idempotency constraint, and database triggers that reject updates/deletes on the
  event/audit ledgers.
- `nha_trang_laundry_db.migrations` applies migrations by unique version and SHA-256 checksum; a
  changed deployed migration fails closed.
- `nha_trang_laundry_db.transactions.commit_material_change` uses one connection transaction and
  requires at least one outbox record. Any mutation, domain-event, audit-event or outbox failure rolls
  back the entire command.
- `scripts/apply_migrations.py` is the explicit migration entry point; it requires `DATABASE_URL` and
  never prints that value.

## Verification

Local unit and PostgreSQL integration evidence: migration discovery/checksum validation, injected
audit-write rollback test, append-only ledger trigger test, and live migration application.

```text
uv run ruff format --check .
uv run ruff check .
uv run mypy apps packages scripts
uv run pytest
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv run python scripts/report_delivery_status.py
uv lock --check
```

On 2026-07-28, PostgreSQL 16.10-alpine started with Docker Compose. `DATABASE_URL` was supplied only
to the local command environment and was not printed. `scripts/apply_migrations.py` applied `0001` and
`0002`; the live integration suite passed its append-only trigger and injected audit-write rollback
tests. This is local development evidence only. It is not a release gate and does not change capability
authorization.

## Deployment and rollback

`0001_transaction_foundation.sql` is additive and forward-only. Once applied, it must never be
edited; a defect is repaired by a new numbered migration. No production deployment is authorized.
Before a real deployment: take a verified backup, apply to a disposable fresh PostgreSQL database,
exercise a failing audit/outbox write, verify transaction rollback and append-only triggers, then
record the database version/checksum and test output in the deployment evidence.
