# TASK-store-scoping-002 — the one store-scoped route the first pass missed

**Goal:** make `POST /internal/v1/orders/{order_id}/transition` enforce store membership, and prove
by test that no other route was missed the same way.

**Domains:** `orders_audit`, `privacy_consent`

**Stable work item:** `STORE-SCOPING-002`

**Stage:** M4B
**Risk:** LOW technically — the check exists and is one call. HIGH in consequence: this is a
cross-store **write**, and the zero-tolerance line at G1 and G2 is zero cross-customer disclosure.

## Why this exists

`STORE-SCOPING-001` closed the membership gap on five routes and is `COMPLETE`. It enumerated those
five from the URL shape — every path containing `/stores/{store_id}/` — plus the approval queue.

The order **transition** route is keyed by `order_id`, not by `store_id`, so it did not appear in
that enumeration and was never fixed:

```python
# packages/db/src/nha_trang_laundry_db/orders.py — transition()
# there is no require_store_membership call anywhere in this method
```

`OrderRepository.create` calls `require_store_membership` at `orders.py:90` and
`OrderRepository.list_for_store` calls it at `orders.py:378`. `transition` does not.

The effect: any principal holding `OWNER_ADMIN`, `OPS_APPROVER` or `OPERATOR` with MFA can drive
**any order's commercial state in any store** by supplying its identifier. Confirming, cancelling
or completing another store's order is a write, not a read, so this is worse than the disclosure
the first pass closed.

This was found while building the staff console, by reading the route table against the repository
rather than by a test — which is itself the finding: nothing currently proves the enumeration is
complete.

## Required design

- Enforce membership in `OrderRepository.transition`, in the **repository**, next to the existing
  `_require_order_mutation` role check — for the same reason `STORE-SCOPING-001` gives: a route is a
  place a check can be forgotten.
- The store is not in the request. Resolve it from the order row that `transition` already reads,
  then require membership for that store. Do not add a client-supplied `store_id` parameter; a
  client-supplied identifier is never authority.
- Raise `OrderAuthorizationError`, which `transition_order` already catches and maps to
  `403 operation denied` — identical to a role refusal, so probing order identifiers teaches a
  caller nothing about which orders or stores exist.
- Resolve membership inside the same transaction that reads the row, so a revoked assignment cannot
  be raced.

## The second half of this item, which matters more than the fix

Add a test that **enumerates the route table and fails on any store-scoped route whose repository
method does not call `require_store_membership`.** Route-shape enumeration by hand is what let this
one through; the next route keyed by something other than `store_id` will be missed the same way
unless the check is mechanical.

Two known cases the enumeration must classify deliberately rather than by accident:

- `GET /internal/v1/stores/{store_id}/shadow/audit/{aggregate_id}` requires membership of the
  **path** store but then filters only on `aggregate_id` (`shadow_console.py`), so a member of store
  A can read store B's audit trail. Scoped by a different item; the enumeration must not report it
  as compliant.
- `GET /internal/v1/shadow/unknown-sends` has no store filter at all and is gated only by role.
  Whether that is intended — the exception queue may legitimately be global — is a decision, not an
  oversight to be silently ratified. Record the answer either way.

## Constraints

- Do not change any status transition, response shape or business behaviour beyond adding the
  authorization failure.
- Do not widen `_require_order_mutation`. The role set is correct; membership is the missing axis.
- Where an existing test relied on transitioning an order in a store the principal does not belong
  to, that reliance **is the finding**: correct the test, never the check.
- No route may gain a client-supplied identifier that is treated as authority.

## Required tests

- a member of store A is refused a transition on an order in store B, with the same 403 body as a
  role refusal;
- a member of store A succeeds on an order in store A, so the fix is not a blanket denial;
- a staff member with no assignment at all is refused;
- the idempotency ledger does not record a claim for a refused transition, so a later legitimate
  attempt with the same key is not poisoned;
- the enumeration test above fails when `require_store_membership` is removed from any store-scoped
  repository method.

## Done when

- `transition` enforces membership in the repository, inside the reading transaction;
- the enumeration test exists and is proven to fail with the check removed;
- the audit-timeline and unknown-sends cases are each explicitly classified rather than left
  ambiguous;
- the full gate battery passes with no required skips;
- rollback is reverting the membership check, which restores a cross-store write path — so rollback
  here is a security regression and must be recorded as one, exactly as `STORE-SCOPING-001` did.
