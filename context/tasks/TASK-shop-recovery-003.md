# TASK-shop-recovery-003 — a real point-in-time restore, and two reasons it could not run

**Goal:** a real point-in-time restore, and two reasons it could not run.

**Domains:** `platform`, `orders_audit`

**Stable work item:** `SHOP-RECOVERY-003`

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
