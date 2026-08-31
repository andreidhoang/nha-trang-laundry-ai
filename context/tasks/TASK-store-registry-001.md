# TASK-store-registry-001 — a store is a row, not a UUID somebody typed

**Goal:** give `store_id` a table and a foreign key, and give production a way to create the first
store.

**Domains:** `orders_audit`

**Stable work item:** `STORE-REGISTRY-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. One migration adding a table and foreign keys to existing columns; no behaviour
change for any valid row.

## Why this exists

Sixteen migrations reference `store_id`. **None creates a `stores` table and no foreign key exists
anywhere.** `scripts/seed_demo_data.py:6-11` says so plainly: "The store is a bare UUID because
there is no `stores` table: `store_id` appears in eight columns across six migrations with no
foreign key anywhere."

Everything this system does to protect one shop from another rests on that identifier:
`staff_store_assignments`, `require_store_membership`, the approval store binding added by `0034`,
the audit-timeline ownership union, the reprice scoping added by `REVERIFY-REGRESSIONS-001`. All of
it compares an unvalidated value against another unvalidated value.

Two consequences:

1. **A typo creates a store.** Assigning a staff member to a mistyped UUID succeeds, and that
   member is then a member of a store that does not exist, seeing nothing and refused everywhere,
   with no error that says why.
2. **There is no production path to create a store at all.** The only code that mints one is
   `scripts/seed_demo_data.py`, whose `require_local_database()` refuses any DSN whose host is not
   local. A production deployment has no way to bring its first store into existence except by
   inventing a UUID and pasting it into the console.

## What must be true when this is done

1. A `stores` table exists, holding what a shop actually needs to be identified by a person: a name,
   and the timestamp and actor that created it.
2. Every `store_id` column that means "this shop" references it. The migration backfills from the
   distinct values already present, so an existing database migrates without loss.
3. `scripts/bootstrap_store.py` creates the first store against a production DSN, attributed to a
   named actor, in the shape of `scripts/bootstrap_owner.py` — which it must run before, since an
   owner is assigned to a store.
4. `scripts/seed_demo_data.py` uses it rather than a hardcoded constant, and stays local-only.
5. `docs/runbooks/production-deploy-day.md` gains the step.

## Boundary

This is not a multi-store feature and not a `parties` layer. `DEC-015` declined a customer-record
aggregate; this is about the shop, not the customer. One store is the expected production state.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
