# TASK-shop-first-start-001 — the first time the stack was actually started

**Goal:** bring the R1 topology up as a whole, for the first time, and fix what that reveals.

**Domains:** `platform`

**Stable work item:** `SHOP-FIRST-START-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. Corrective changes to two COMPLETE items' artifacts, which is why this is its own
item rather than an edit to closed history.

## Why this exists

`SHOP-DEPLOY-001` and `SHOP-IDENTITY-001` are COMPLETE and their evidence is honest about what it
proved: `docker compose config` validated, contract tests passed, and Keycloak was verified
standalone. **Nobody had ever run the five services together.** `docker ps -a` for the project name
returned nothing.

Configuration that validates is not configuration that starts, and every defect below was invisible
to `config`, to `mypy`, and to 1118 passing tests.

## What must be true when this is done

1. `docker compose up` brings every service to a running state from an empty machine.
2. The console, the sign-in surface and the identity provider all answer over TLS on the console
   hostname, and nothing else does.
3. The admin console and the master realm are unreachable.
4. All five capability flags read false on the **running containers**, not in a file.
5. Each defect is fixed where it belongs rather than worked around in a runbook step.

## Boundary

Not covered: a browser sign-in end to end, which needs the CA trusted in a real browser profile;
and the counter transaction, which belongs to `SHOP-CUTOVER-001` on a provisioned host.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
