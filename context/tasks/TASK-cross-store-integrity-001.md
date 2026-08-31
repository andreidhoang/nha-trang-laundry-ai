# TASK-cross-store-integrity-001 — an approval belongs to a shop, and the buttons reach the server

**Goal:** close the four authorization defects and the two dead console commands the 62-agent
adversarial verification found on 2026-08-29.

**Domains:** `orders_audit`

**Stable work item:** `CROSS-STORE-INTEGRITY-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** HIGH. It adds a required column to an append-only ledger, changes who may decide an
approval, and touches the manual-send path — the only surface in this system that authorises a
message to a real customer.

## Why this exists

Six findings, each reproduced twice by skeptics who defaulted to *refuted*.

### The approval model had no store

`approval_requests` carried no `store_id`. `list_pending` inferred one by joining
`orders ON o.id = r.resource_id`, which works only when the resource happens to be an order — and
of the thirteen types in `APPROVAL_RESOURCE_TYPES`, **two** have a backing table. That single fact
caused three separate defects:

1. **`decide` had no store to check.** A member of store B, with no assignment to store A,
   permanently approved store A's approval. The three distinct refusals also leaked whether an id
   existed and what role it required, across every store.
2. **Manual send was worse.** Store B's operator could `prepare` and `attest` against store A's
   one-time `SEND_MESSAGE` approval — burning it permanently, and recording their own staff id as
   having sent store A's approved message to a recipient store A never named.
3. **The queue hid the manual-send workflow entirely.** Every `MESSAGE_DRAFT` approval was
   invisible to the people meant to action it, because its resource is not an order.

### The envelope was bound to nothing

`request` never resolved `resource_id`, never compared the digests against the stored resource, and
never checked the policy version. An immutable, audited artifact could assert that an owner approved
a specific content digest of a specific resource where **neither the resource nor the digest nor the
policy existed**.

### The audit timeline was an unscoped read

`_require_store_access` proved the caller belonged to the store they *named*; the query then
filtered on `aggregate_id` alone. Naming your own store while supplying another store's order id
returned that store's full timeline — creation, every transition, the settlement, its operator's id.
It doubled as an existence oracle over any UUID in the system.

### Two console buttons never reached the server

`core/api.js` throws before opening a request when a mutating call has no idempotency key.
"Phát phiếu" and "Ghi nhận chuyến giao" both omitted one. So `DEC-013`'s walk-in was unreachable
from the console *even after the server route was fixed the day before*, and a delivery order could
not be closed.

## What must be true when this is done

1. An approval names its store; a non-member can neither request nor decide one.
2. A member of the owning store still can — a guard that refuses everyone is not a fix.
3. Manual send prepare and attest both check membership against the approval's own store.
4. The audit timeline returns nothing for an aggregate outside the named store, and returns the
   timeline for one inside it.
5. An approval naming a resource that does not exist is refused, where the resource is resolvable.
6. Every mutating console call carries an idempotency key, enforced by a test rather than by review.

## Three judgement calls, named

**`QUOTE_REVISION` is deliberately not resource-checked.** The first attempt included it and was
wrong: migration `0029` makes `quote_revisions.approval_id` a foreign key, so **the approval is
written before the revision it authorises exists**. Requiring the revision to resolve refuses the
only order those writes can happen in. For that type the store binding is the whole check, and the
digest is verified later by `_require_exact_binding`.

**The agent path is exempt from staff membership.** It requests as `AGENT_RUNNER` with
`requested_by = claims.run_id` — a run, not a person, never a row in `staff_store_assignments`.
Requiring membership there would refuse every agent approval rather than secure anything. It is
bound differently and not less: verified claims carrying the store, plus `_bound_request` checking
the store/contact/conversation tuple.

**`rendered_hash` is not verified and cannot be.** It is a digest of a rendering this system does
not store. Comparing it against anything would be theatre. `snapshot_hash` is verified where a
stored digest exists; the gap is stated rather than papered over.

## Boundary

Not fixed here: the settlement panel's false Vietnamese copy, the four routes returning 500 on
integers ≥ 2⁵³, `CONFIRMED → CANCELLED` being unguarded, and the unreachable promotion. All remain
recorded and open.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
