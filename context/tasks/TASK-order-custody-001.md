# TASK-order-custody-001 — custody, not acceptance, is what a cancellation resolves

**Goal:** custody, not acceptance, is what a cancellation resolves.

**Domains:** `orders_audit`, `business_truth`

**Stable work item:** `ORDER-CUSTODY-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. A corrective item: it changes artifacts belonging to COMPLETE items, whose planning history is immutable (ADR-0004).

## Why this exists

An eight-lens adversarial round found three defects in `ORDER-EXIT-001`'s own work, all reachable
from a counter on any ordinary day.

`_work_has_begun` tested `intake is ACCEPTED` and so caught only the last of six intake states. The
shop takes physical custody at `RECEIVED_PENDING_INSPECTION`, four stages earlier, so an order with
a customer's laundry on the counter cancelled outright with nothing recorded about where the goods
went. `custody_resolution` decided both the approval and the ledger entry and was absent from the
hashed idempotency payload, so two cancellations differing only in their resolution replayed each
other. And nothing compared the resolution against facts the system had already written down.

## What must be true when this is done

1. Cancelling an order the shop physically holds routes through review, from every intake state
   where custody exists.
2. Two cancellations differing only in their resolution conflict rather than replay.
3. A resolution the order's own record contradicts is refused; a judgement the record cannot
   settle is **not** decided here.
4. Each fix has a reproduction verified to fail without it.

## Acceptance

The six commands in `delivery/WORK_QUEUE.yaml`, and a reproduction for each defect that
is verified to fail without its fix.
