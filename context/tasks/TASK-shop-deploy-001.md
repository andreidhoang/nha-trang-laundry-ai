# TASK-shop-deploy-001 — a production topology that can actually be reached

**Goal:** a one-host Zone C deployment manifest for the R1 release, with a TLS listener that exists.

**Domains:** `platform`

**Stable work item:** `SHOP-DEPLOY-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. A new manifest and a new contract test; no application code and no schema change.

## Why this exists

### The production TLS listener does not exist

`compose.production.yaml` attaches the `tls` service only to `ingress-private`, which is
`internal: true`. Docker accepts the `127.0.0.1:8443` port declaration and silently discards it:
`HostConfig.PortBindings` asks for the binding, `NetworkSettings.Ports` comes back empty, the
network has no gateway, and nothing reports an error. The one host listener
`docs/runbooks/private-staging.md` promises does not exist.

This is already recorded. `compose.demo.yaml:131-142` documents the defect, works around it for the
demo with an additional non-internal edge network, and states: *"The equivalent production fix is
not made here: changing the deployed topology belongs to its own reviewed item."* This is that item.

Until it lands, `docs/runbooks/production-deploy-day.md` describes a deploy that cannot serve a
single request.

### The file is a private-staging artifact and should stay one

`compose.production.yaml` is `name: nha-trang-laundry-private-staging`, with `staging.internal` as
its origin and trusted host and `staging_*` on all eleven secrets. That is honest rather than
wrong. It is also cited by the acceptance commands of three COMPLETE items and by their recorded
evidence, and asserted service-by-service by
`packages/evals/tests/test_staging_deployment_contract.py`. Renaming it would rewrite closed
evidence to describe a topology it never ran.

R1 therefore gets its own standalone `compose.r1.yaml`.

## What must be true when this is done

1. `tls` is attached to a non-internal edge network and is the only service on it, so the published
   port is a real listener. A new contract test asserts both halves.
2. The bind address is a variable defaulting to `127.0.0.1`, so an unset value produces an
   unreachable console rather than an exposed one. The staff console is never on a public hostname
   (ADR-0007): staff reach it over the shop network or a VPN address.
3. `STAFF_ALLOWED_ORIGINS` and `API_TRUSTED_HOSTS` name the real console host. `127.0.0.1` stays in
   the trusted hosts or the container healthcheck is rejected and the container is unhealthy forever.
4. PostgreSQL appears as a compose profile, not a second file, so the branch the hosting decision
   picks — provider-managed with PITR, or self-managed with continuous WAL archiving — is selected
   rather than forked.
5. Every hardening invariant is preserved: non-root numeric users, `read_only` root filesystems,
   `cap_drop: ALL`, `no-new-privileges`, sized `noexec,nosuid` tmpfs, external secrets only, the
   one-shot `migrate` job gating `api` and `worker`, and all five capability flags false.
6. The staging file and its contract test are left exactly as they are, and the R1 file says in its
   header why the asymmetry is deliberate.

## Boundary

Not proven here: that the listener works on a real Linux host. The pattern is proven by the demo;
the production binding is untested by definition until a host exists, and that proof belongs to
`SHOP-CUTOVER-001`.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
