# TASK-store-scoping-001 — close the store-scoping gap on the existing console

**Goal:** make every staff surface enforce store membership, not only the Shadow ones.

**Domains:** `orders_audit`, `privacy_consent`

**Stable work item:** `STORE-SCOPING-001`

**Stage:** M4B
**Risk:** MEDIUM technically, HIGH in consequence — this is a cross-customer disclosure path, which
is a zero-tolerance defect at G1 and G2.

## Why this exists

`SHADOW-CONSOLE-001` needed to satisfy an IDOR test and discovered there was no staff-to-store
membership model at all: the existing console authorizes by **role alone**. `_require_order_read`
checks that the principal holds one of `OWNER_ADMIN`, `OPS_APPROVER`, `OPERATOR` or `AUDITOR`, and
then the query filters by whatever `store_id` appeared in the URL.

An operator can therefore read any store's data by changing an identifier.

`SHADOW-CONSOLE-001` added `staff_store_assignments` and enforces membership in
`ShadowConsoleRepository`, but only for the Shadow surfaces. These routes still carry the gap:

| Route | Repository |
|---|---|
| `GET /internal/v1/stores/{store_id}/orders` | `OrderRepository.list_for_store` |
| `POST /internal/v1/stores/{store_id}/orders` | `OrderRepository.create` |
| `GET /internal/v1/stores/{store_id}/quotes` | quote listing |
| `GET`/`POST` `/internal/v1/stores/{store_id}/incidents` | incident intake and listing |
| `GET /internal/v1/approvals` | approval queue |

Fixing it on one surface and leaving five open is worse than not knowing: it reads as solved.

The zero-tolerance line at both G1 and G2 is **zero cross-customer disclosure**. Today the system
would satisfy that only because there is one store.

## Required design

- Enforce membership in the **repository**, next to the existing role check, not in the route. A
  route is a place a check can be forgotten; the repository is the only path to the data.
- Reuse `staff_store_assignments`; introduce no second membership concept.
- Return the same error for an unauthorized role and an unassigned store, as the Shadow surfaces do,
  so probing identifiers teaches a caller nothing about which stores exist.
- The approval queue is not store-keyed in its URL. Scope it by the stores the principal belongs to,
  rather than adding a client-supplied store parameter that would itself be forgeable.
- Seed membership for existing staff as part of the migration. Every current operator must keep
  working; this item closes a hole, it does not lock anyone out.

## Constraints

- No route may accept a client-supplied identifier as authority for anything.
- `OWNER_ADMIN` is not implicitly a member of every store. If the owner should see all stores, that
  is an explicit assignment or an explicit, audited role rule — never an inference.
- Do not change any existing business behaviour, status transition or response shape beyond adding
  the authorization failure.
- Existing tests must keep passing; where one relied on cross-store access, that reliance is the
  finding and the test is corrected, not the check.

## Required tests

For each of the five routes, by API rather than by UI:

- a member of store A is refused store B, with the same 403 as a role failure;
- a member of store A succeeds for store A, so the fix is not a blanket denial;
- the approval queue returns only approvals for stores the principal belongs to;
- a staff member with no assignment at all is refused everywhere;
- an expired or revoked session cannot act, unchanged from today.

## Done when

- all five routes enforce membership in their repository;
- every existing operator retains access to their own store through the seeded assignments;
- the full gate battery passes with no required skips;
- rollback is reverting the membership check, which restores the gap rather than breaking a flow —
  so rollback here is a security regression and should be recorded as one.
