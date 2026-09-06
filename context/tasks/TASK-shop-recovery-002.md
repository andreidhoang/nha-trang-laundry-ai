# TASK-shop-recovery-002 — the backups can be taken, and the drill can be passed

**Goal:** the backups can be taken, and the drill can be passed.

**Domains:** `platform`, `orders_audit`

**Stable work item:** `SHOP-RECOVERY-002`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. A corrective item: it changes artifacts belonging to COMPLETE items, whose planning history is immutable (ADR-0004).

## Why this exists

Three HIGH findings and the reasons the archiving could not run at all. The drill validator could
never pass on a real database: it counted rows against a uniqueness claim the schema does not make,
and asked contiguity of writers that never promised it. An empty base backup was recorded as
archived and preferred by the restore. `restore.sh` wrote a `restore_command` PostgreSQL refuses and
exited 0. And the image had no object-store client at all, so `archive_command` failed on every
segment.

## What must be true when this is done

1. The validator passes a healthy real database and still catches a lost event, including one
   masked by a same-version pair.
2. Nothing incomplete is ever uploaded, and the restore does not prefer a truncated artifact.
3. Every command the archiving and the drill invoke exists and can run where it is invoked.

## Acceptance

The six commands in `delivery/WORK_QUEUE.yaml`, and a reproduction for each defect that
is verified to fail without its fix.
