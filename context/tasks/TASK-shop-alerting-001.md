# TASK-shop-alerting-001 — the alerts fire in the shop's hours and survive a broken check

**Goal:** the alerts fire in the shop's hours and survive a broken check.

**Domains:** `platform`

**Stable work item:** `SHOP-ALERTING-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. A corrective item: it changes artifacts belonging to COMPLETE items, whose planning history is immutable (ADR-0004).

## Why this exists

Five defects in `SHOP-OBSERVABILITY-001`'s own work. Quiet hours were computed on
`datetime.now(UTC)` while the shop is UTC+7, inverting the alerting window by seven hours — and the
test encoded the same hours, so it passed while pinning the bug.

## What must be true when this is done

1. Quiet hours are the shop's hours, and the runbook's named case (07:45 local) alerts.
2. A check asked for and unrunnable refuses; it does not skip and exit 0.
3. `archive_mode = off` is a pass only on a branch the operator declared.
4. One check raising cannot discard the others' events or the alert.
5. `LOG_LEVEL` cannot silence the record stream, and a later `dictConfig` cannot either.

## Acceptance

The six commands in `delivery/WORK_QUEUE.yaml`, and a reproduction for each defect that
is verified to fail without its fix.
