# TASK-store-registry-002 — the dangling transaction, everywhere, and a test that looked

**Goal:** the dangling transaction, everywhere, and a test that looked.

**Domains:** `platform`, `orders_audit`

**Stable work item:** `STORE-REGISTRY-002`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. A corrective item: it changes artifacts belonging to COMPLETE items, whose planning history is immutable (ADR-0004).

## Why this exists

`STORE-REGISTRY-001` found that a bare cursor leaves an implicit transaction open, fixed it in
`stores.py`, and in the same commit added a new instance of the same pattern two functions away in
`shadow_console.py`. The worst reachable consequence there is a revocation of store access
returning 204 and silently staying live.

Its cited FK test inserted three fresh UUIDs, so three foreign keys could fire; dropping the store
constraint left the test green.

## What must be true when this is done

1. No pre-check in `shadow_console.py` leaves a transaction open.
2. The FK test fails when — and only when — the store constraint is absent.
3. The typed refusal the evidence claimed is asserted at the HTTP boundary.

## Acceptance

The six commands in `delivery/WORK_QUEUE.yaml`, and a reproduction for each defect that
is verified to fail without its fix.
