# TASK-m1-003 — Immutable configuration publication

**Goal:** persist only typed, immutable configuration versions; runtime reads published versions only.

**Domains:** `platform`, `pricing`, `orders_audit`  
**Stage:** M1  
**Risk:** HIGH

## Authoritative context

- `BUILD_ENGINEERING_SPEC.md` §§4, 6, 11, 12 and 13.
- `specs/DOMAIN_DATA_API_SPEC_V1.md` §§5 and 16.
- `specs/IMPLEMENTATION_ROADMAP_V1.md` §11.
- `specs/contracts/canonical-enums-v1.json`.

## Constraints and done criteria

- Registered typed validators, RFC 8785 snapshot hashing and a forward-only migration are required.
- Drafts remain invisible to runtime reads; a publication must atomically create event, audit and outbox records.
- Published/retired rows are append-only; policy semantics not yet approved remain outside this generic primitive.
- Unit tests cover canonical hash, typed-validator rejection, draft ledger writes, stale publication rejection and draft invisibility.

## Rollback and evidence

- Migration `0002` is additive and forward-only. Repair by a subsequent migration; never edit it after application.
- Live PostgreSQL trigger/rollback verification remains blocked until Docker/PostgreSQL is available.
