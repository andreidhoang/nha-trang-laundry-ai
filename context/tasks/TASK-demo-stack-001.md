# TASK-demo-stack-001 — make the whole system runnable and observable on one machine

**Goal:** bring the full container topology up on a developer Mac, log into the staff console, and
watch the worker drain a job — without weakening a single production check.

**Domains:** `platform`, `runtime_architecture`

**Stable work item:** `DEMO-STACK-001`

**Stage:** M4A
**Risk:** MEDIUM — the risk is not that it fails to run. It is that someone makes it run by
disabling a check, and the local stack stops resembling the thing it is supposed to de-risk.

## Why this exists

`STAGING-001` built a genuine private topology — Caddy TLS in front of the API, a separate worker, a
one-shot migration job, non-root numeric UIDs, read-only roots, all capabilities dropped, three
networks, secrets read from files. `CONTAINER-001` built the images. Neither can be exercised on a
developer machine, so the system has never been seen running as a system. Every claim about it rests
on unit and integration tests.

Measured on 2026-08-14, four things block a local bring-up, in the order Docker reports them:

1. `database-private` is declared `external: true`. It does not exist until someone runs
   `docker network create --internal`.
2. All ten secrets are declared `external: true`, which outside Swarm cannot resolve.
   `deploy/staging/compose.smoke.yaml` already demonstrates the fix — it overrides each secret to a
   `file:` under `${STAGING_SMOKE_SECRET_DIRECTORY}`.
3. Authentication is real OIDC. `IdentityPlatformVerifier` builds a `PyJWKClient` against
   `oidc_jwks_url` and validates issuer, audience and an MFA claim. There is no development bypass,
   deliberately, and none may be added.
4. Nothing seeds a store, a staff user, or a quote. `bootstrap_owner.py` covers the owner; the rest
   has no path, and a quote has no creating route at all until `QUOTE-COMMAND-001`.

The images themselves are not a blocker: all three build clean on arm64.

## What to build

A `compose.demo.yaml` overlay, following the smoke overlay's `!override` pattern, plus the
generators that produce what it needs. Nothing in this item edits `compose.production.yaml`.

- **A local OIDC issuer.** A real one, not a stub that returns `true`. It must mint RS256 tokens
  against a keypair it generates, serve a standards-shaped JWKS document, and populate the exact
  claims the verifier requires including the MFA claim. The API's verifier is not modified, not
  subclassed and not monkeypatched — if the token is wrong the login must fail, and that failing is
  the point of building a real issuer rather than a bypass.
- **A TLS material generator.** A private CA and a leaf certificate with SAN `staging.internal`,
  written to the gitignored secret directory.
- **Three database roles.** `migrate` with DDL, `api` and `worker` with their declared grants and
  neither owning the database. The runbook already specifies the split; this makes it real locally
  so that a missing grant surfaces here rather than in production.
- **A seed command.** One store, one owner, one operations staff member with a store assignment.
  Explicitly synthetic, and it must refuse to run against a database whose URL is not local.
- **A demo runbook** under `docs/runbooks/`, stating in its header what the stack is not.

## Constraints

- **Never weaken a check to make local work.** No auth bypass, no permissive CORS, no disabled CSRF,
  no relaxed trusted hosts, no `read_only: false`, no added capability, no root user. If a security
  control makes the demo inconvenient, the demo changes, not the control.
- **No secret material in git.** Commit generators, never their output. Keys, certificates and
  database passwords are written to a gitignored directory. This is the same rule as
  `docs/runbooks/provider-credentials.md`, and it applies to synthetic material too, because a
  reader cannot tell the difference by looking.
- **The local OIDC issuer is a development component.** It must be impossible to select in the
  production overlay, and its name must say so.
- **Capability flags stay closed.** `FEATURE_AGENT_RUNTIME_ENABLED`, `FEATURE_AUTOMATED_SENDS_ENABLED`
  and `FEATURE_PUBLIC_CHANNELS_ENABLED` remain false. Do not edit the `WorkerSettings` validator that
  refuses `feature_agent_runtime_enabled=true`. If the demo appears to need it, the demo is wrong.
- **Local image tags are not release evidence.** `private-staging.md` already says so; the demo
  runbook must repeat it.
- Seed data is synthetic. No real person, no real phone number, no real address.

## Required tests

- the demo overlay's compose configuration validates and declares no external secret;
- the OIDC issuer's token is accepted by the unmodified `IdentityPlatformVerifier`, and a token with
  a wrong audience, a wrong issuer, or a missing MFA claim is rejected — three negative tests;
- the seed command refuses a non-local database URL;
- no file the generators write is tracked by git;
- the demo overlay does not set any feature flag true and does not relax any hardening key.

## Done when

- `docker compose -f compose.yaml -f compose.production.yaml -f compose.demo.yaml up -d --wait`
  succeeds from a clean machine after running the documented generator commands;
- `scripts/staging_smoke.py` passes against the local TLS endpoint;
- a human can log into the staff console in a browser and see the ten screens;
- the worker is running, leases are recovering, and the agent pipeline drains an enqueued job to a
  persisted draft in deterministic-degraded mode with `provider_backed: false`;
- `report_delivery_status.py` still reports all thirteen capabilities `NOT_AUTHORIZED`;
- rollback is deleting the overlay and the generated directory, which returns the repository to its
  current state because nothing outside them changed.
