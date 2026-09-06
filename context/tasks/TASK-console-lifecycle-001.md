# TASK-console-lifecycle-001 — the counter can run a whole day on the console

**Goal:** the counter can run a whole day on the console.

**Domains:** `platform`, `orders_audit`

**Stable work item:** `CONSOLE-LIFECYCLE-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. A corrective item: it changes artifacts belonging to COMPLETE items, whose planning history is immutable (ADR-0004).

## Why this exists

Two lenses reached the same finding from opposite ends: `/intake-transition` and
`/production-transition` had existed on the server since the beginning and no console screen called
either. A console-only operator could create, quote and confirm an order and then dead-end on
`409 INVALID_STATE_TRANSITION: intake is not accepted`, because intake is the blocking dimension and
every later step hangs off ACTIVE.

R1's premise is that the shop runs its day on this console, so this is the gap that decides whether
the release is real.

## What must be true when this is done

1. A member of staff can drive an order from creation to `COMPLETED` using only the console.
2. `#/gaps` describes what is true — it was wrong in both directions inside one week.
3. A settled decision can no longer be presented as a pending blocker without a check failing.

## Acceptance

The six commands in `delivery/WORK_QUEUE.yaml`, and a reproduction for each defect that
is verified to fail without its fix.
