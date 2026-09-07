# TASK-shop-pilot-003 — the pilot stack actually runs, and five reasons it did not

**Goal:** the pilot stack actually runs, and five reasons it did not.

**Domains:** `platform`

**Stable work item:** `SHOP-PILOT-003`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. Corrective changes to COMPLETE items'
artifacts, which is why this is its own item rather than an edit to closed history.

## Why this exists

Running it. Every defect here was invisible to `docker compose
config`, to a property diff of the rendered files, to mypy and to the suite, and each one
let the stack report healthy while the shop had no recoverable backup.

## Acceptance

The six commands in `delivery/WORK_QUEUE.yaml`, plus a bring-up from an
empty machine using only the runbook's own commands.
