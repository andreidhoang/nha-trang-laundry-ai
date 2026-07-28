# TASK-m1-003 — Immutable configuration publication

**Status:** Persistence primitive complete; concrete business configuration schemas remain M2 work.  
**Date:** 2026-07-28  
**Context domains:** `platform`, `pricing`, `orders_audit`

## Requirement and contract

- `BUILD_ENGINEERING_SPEC.md` invariants 3, 4, 5 and 11; §§6, 11, 12 and 13.
- `specs/IMPLEMENTATION_ROADMAP_V1.md` §11: drafts are not runtime-readable; publication creates an
  immutable published version.
- `specs/contracts/canonical-enums-v1.json`: `ConfigLifecycle` and RFC 8785 SHA-256 snapshot-hash
  normalization.

## Delivered

- Forward-only migration `0002_configuration_publication.sql` creates versioned configuration storage,
  lifecycle checks, a published-read index, and a trigger blocking update/delete after publication.
- `ConfigurationRepository` requires a registered typed validator, hashes payloads with RFC 8785 JCS
  after removing only contract-defined volatile fields, and creates draft/publish ledgers through the
  existing atomic mutation/event/audit/outbox primitive.
- Published reads filter on `lifecycle = 'PUBLISHED'`; drafts remain invisible.
- The live PostgreSQL integration suite verifies published immutability, draft invisibility, append-only
  ledger behavior and audit-write failure rollback.

## Verification

```text
DATABASE_URL=<local development value> uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy apps packages
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv lock --check
```

Result: 24 tests passed, including three live PostgreSQL integration tests. Contract and context checks
passed.

## Deployment and rollback

`0002` is additive and forward-only. Do not edit it after application; repair through a new migration.
No production configuration, capability, endpoint, credential, public channel or release authorization
was added. Concrete catalog/pricebook/promotion/SLA schemas and their semantic publication validation
remain future deterministic domain work; unknown policy continues to fail closed.
