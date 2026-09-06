# TASK-shop-walkthrough-001 — one real counter transaction, through console routes, on a live stack

**Goal:** one real counter transaction, through console routes, on a live stack.

**Domains:** `platform`, `orders_audit`

**Stable work item:** `SHOP-WALKTHROUGH-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. A corrective item: it changes artifacts belonging to COMPLETE items, whose planning history is immutable (ADR-0004).

## Why this exists

Every corrective item above is a claim about behaviour. This one runs the shop's day against a
running stack and reports what happened, using only routes extracted from `apps/web/src/screens/`
at runtime — so it walks the surface a member of staff has and nothing else.

## What must be true when this is done

1. A transaction reaches `COMPLETED` with `balance=PAID` through console routes only.
2. The 6 kg pricing cliff holds on the live stack.
3. What this does not prove is stated: it is the demo stack, not a provisioned host, and the
   sign-in was a demo token rather than a browser completing password and TOTP.

## Acceptance

The six commands in `delivery/WORK_QUEUE.yaml`, and a reproduction for each defect that
is verified to fail without its fix.
