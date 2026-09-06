# TASK-shop-pilot-001 — a week of real trading before a hosting bill

**Goal:** let the shop run the console on its own machine, on its own network, for a week of real
business, and make the end of that week the migration onto the cloud host.

**Domains:** `platform`

**Stable work item:** `SHOP-PILOT-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. No application code and no schema change, but it is the first configuration in
this repository intended to hold a real shop's money records.

## Why this exists

The owner asked whether the shop could use this for a week on its own machine before committing to
cloud hosting. It can, and there are three reasons it is better than a detour:

1. **The week ends in the restore drill.** Migrating to the cloud host means restoring the shop's
   real data onto it, which is `docs/runbooks/restore-drill.md`. `BACKUP-RESTORE-001` completes on a
   drill result and never on configuration, so the drill has to happen regardless — doing it as the
   migration means it happens once, with consequences, on data somebody cares about.
2. **It starts the clocks nothing else can start.** `SHOP-INSTRUMENT-001` needs ten timed loads,
   twenty delivery logs and a measured cost per kilo, and its packet calls that four to six weeks of
   real operation that cannot be simulated or back-filled. `G2` needs thirty completed real orders.
3. **It is free, and the rollback is switching a machine off.**

## What must be true when this is done

1. The pilot runs the R1 stack, not a variant of it. Anything that differs is a thing the week does
   not rehearse.
2. Secrets come from files rather than a swarm store, because a shop laptop has no swarm — which
   ADR-0007 §5 admits by name, "a file-based store with restrictive ownership" qualifying alongside
   a provider's secret store.
3. It is impossible to reach for `compose.demo.yaml` by mistake. That file seeds synthetic staff
   holding `OWNER_ADMIN` and wires an identity provider that mints a token for any subject asked of
   it.
4. Preparing the machine does not generate the backup identity on it. `DEC-026` places the private
   half away from the thing it protects.
5. The archive is off-site from day one. One machine in one building with no archive is one accident
   away from losing the shop's money records.

## Boundary

Not covered here: the drill itself, which needs the cloud host and stays with
`BACKUP-RESTORE-001`; and `SHOP-CUTOVER-001`, which remains blocked on a provisioned host.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
