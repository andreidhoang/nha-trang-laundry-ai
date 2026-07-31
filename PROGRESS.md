# Production Readiness Progress

**Assessed:** 2026-07-31
**Repository:** `C:\Users\DELL\OneDrive\Desktop\nha-trang-laundry-ai`
**Branch:** `main` tracking `origin/main`
**Working tree at assessment start:** clean
**Current machine stage:** `DOMAIN_CORE_ACTIVE`
**Production authorization:** `NOT_AUTHORIZED`

## 1. Executive assessment

This is not an empty prototype. It is a Python-first modular monolith with a substantial deterministic
domain and PostgreSQL control plane, a constrained OpenClaw Tool Facade, machine-readable delivery
state, and a large synthetic evaluation suite.

Ten work items through `OPERATIONS-001` are recorded complete. `AGENT-001` is blocked because local
synthetic evidence is not equivalent to provider-backed release evidence. No public or autonomous
capability is authorized.

The repository is locally healthy for the checks that can run without PostgreSQL:

- Ruff format: pass (`184 files already formatted`)
- Ruff lint: pass
- mypy strict mode: pass (`127 source files`)
- pytest without `DATABASE_URL`: `267 passed, 47 skipped` from 314 collected tests
- contract validation: pass (`11 JSON`, `2 YAML`)
- context drift validation: pass
- pinned public-runtime artifact validation: pass with `9` release blockers
- OpenClaw TypeScript plugin: build pass, `3/3` tests pass

The database-enabled claim recorded in delivery state (`313 database-enabled tests pass`) could not be
reproduced during this assessment because Docker Desktop was not running. This is an environment
blocker, not a newly observed test failure.

## 2. Current architecture and stack

### Runtime shape

```text
Staff browser/PWA
  -> FastAPI internal API
  -> deterministic domain + PostgreSQL repositories
  -> approval / audit / inbox / outbox controls

Isolated Public OpenClaw cell (currently eval-only)
  -> fixed-name TypeScript plugin
  -> authenticated FastAPI Tool Facade
  -> server-derived identity/binding
  -> deterministic domain and policy decisions
  -> durable worker / human review

Only a controlled worker may eventually perform outbound side effects.
```

### Technology

- Python `3.12`, `uv` workspace, FastAPI, Pydantic, psycopg, PostgreSQL 16
- Pure deterministic domain package for money, quotes, pricing, promotions, delivery, SLA, and orders
- SQL-first forward migrations under `packages/db/migrations`
- TypeScript `5.9` OpenClaw plugin with fixed typed operations generated from OpenAPI
- Pytest, Hypothesis, Ruff, strict mypy, JSON Schema/OpenAPI contract validation
- GitHub Actions Python quality workflow
- Static mobile-first staff web slice
- Docker Compose currently provisions PostgreSQL only

### Sources of truth

- Normative engineering rules: `BUILD_ENGINEERING_SPEC.md` and `specs/`
- Contracts/evals: `specs/contracts/` and `specs/evals/`
- Execution queue: `delivery/WORK_QUEUE.yaml`
- Resume state: `delivery/LOOP_STATE.yaml`
- Release gates: `delivery/GATE_REGISTRY.yaml`
- Capability authorization: `delivery/CAPABILITY_STATUS.yaml`

Do not introduce a second authoritative `.openclaw/tasks.json` queue. If an OpenClaw execution mirror
is needed, it must reference stable work-item IDs from `delivery/WORK_QUEUE.yaml` and must never
override the repository state machine.

## 3. Completed capabilities

Recorded complete with local evidence:

1. Engineering workspace, contracts, context drift checks, and delivery harness
2. PostgreSQL migration and atomic transaction foundations
3. Immutable configuration publication primitives
4. Named staff identity, RBAC, MFA/session boundaries, audit and negative authorization checks
5. Canonical service registry and legacy aliases
6. Exact pricebook import manifest and count/hash validation
7. Deterministic pricing and estimate engine
8. Promotion, delivery, SLA, and fail-closed policy boundaries
9. Immutable quote snapshots and reproducible calculation traces
10. Orders, approvals, inbox/outbox, idempotency, audit, and a staff web slice
11. Local constrained-agent scaffolding: fixed Tool Facade, Runner bridge, durable agent/tool ledger,
    32-case synthetic degraded bundle, signed-manifest verification, trust-root validation, and an
    offline OpenClaw audit

## 4. Pending work and blockers

### External blockers for `AGENT-001`

These cannot be honestly completed by Codex alone:

- dedicated model-provider credential and immutable model release pin
- verified effective provider request proving the approved `store:false`/retention behavior
- PRIMARY and fallback provider runs for the release eval manifest
- Security/Privacy decision `DEC-006`
- approved signer registry and required release signatures
- immutable sandbox image digest, passing scan evidence, and hash-pinned SBOM

Until these exist, `INTERNAL_SHADOW`, public ingress, and automated outbound must remain disabled.

### Local production-hardening gaps

- Docker Desktop is not running, so PostgreSQL integration tests were skipped in this assessment.
- CI has no PostgreSQL service/migration step; its default `pytest` command can silently skip database
  integration coverage.
- CI does not build or test the TypeScript OpenClaw plugin.
- There are no production Dockerfiles for API, worker, or Tool Facade; `compose.yaml` contains only
  PostgreSQL.
- `packages/observability` is a one-line placeholder and is not integrated into runtime code.
- `packages/policy` is a one-line placeholder and is not integrated into runtime code.
- No deployment target, production environment, secret manager, backup provider, public host, or
  rollback endpoint is configured.
- No automated SBOM, dependency/license, secret, or container scan is present in the checked-in CI
  workflow.
- Real-customer Shadow readiness, PITR/restore drill, incident drill, kill-switch drill, and production
  monitoring evidence are absent.

## 5. First five Codex-executable tasks

The following are safe, local, reviewable tasks. They improve production readiness without pretending
to resolve external approvals or authorize public operation.

### `HARDEN-CI-001` — Make database and plugin checks non-skippable in CI

**Files:** `.github/workflows/quality.yml`, test configuration or a small verification script if
needed, and CI documentation.

**Goal:** provision PostgreSQL 16 in CI, apply migrations, set a synthetic `DATABASE_URL`, run the full
database-enabled suite, and build/test the TypeScript OpenClaw plugin.

**Constraints:** no real credentials; fail if required integration tests are skipped; preserve existing
Ruff/mypy/contracts/context gates.

**Done when:** a clean CI run executes migrations, all Python tests including PostgreSQL integration
tests, and `npm test` for the plugin.

### `OBSERVABILITY-001` — Implement the minimum redacted observability foundation

**Files:** `packages/observability/`, API/worker/Tool Facade integration points, and tests.

**Goal:** structured logs, correlation IDs, explicit redaction of secrets/PII, and stable event fields
for API requests and worker jobs.

**Constraints:** never log raw provider payloads, credentials, addresses, phone numbers, prompt
contents, or chain-of-thought; typed boundaries; no business decisions in logging code.

**Done when:** unit and integration tests prove correlation propagation and redaction, followed by
Ruff, mypy, and relevant pytest checks.

### `POLICY-001` — Implement a typed fail-closed policy decision point

**Files:** `packages/policy/`, existing runtime call sites, contracts and tests.

**Goal:** centralize effective capability evaluation using the existing global/capability flags,
stage gates, authorization, and reason codes.

**Constraints:** missing/stale/malformed state returns `DENY` or `REQUIRE_HUMAN`; model arguments cannot
set identity, stage, approval, contact binding, or capability; no public capability becomes enabled.

**Done when:** positive internal-only cases and negative tests for missing flags, stale authority,
cross-capability escalation, and unavailable policy state pass.

### `CONTAINER-001` — Add reproducible non-root production images

**Files:** production Dockerfiles, `.dockerignore`, Compose production profile or deployment build
configuration, and smoke tests/docs.

**Goal:** build pinned images for API, worker, and Tool Facade with non-root users, minimal runtime
contents, health checks, and explicit entrypoints.

**Constraints:** no secrets in layers; no owner workspace mount; Public OpenClaw remains separately
isolated; immutable image digests are deployment outputs, not invented source values.

**Done when:** all images build locally, health checks pass against synthetic infrastructure, and image
contents contain no `.env`, git metadata, test evidence, or private workspace data.

### `SUPPLYCHAIN-001` — Add release supply-chain gates

**Files:** `.github/workflows/quality.yml` or a dedicated release workflow, verification scripts,
schemas/evidence adapters, and runbook.

**Goal:** secret scan, dependency/license audit, SBOM generation, container vulnerability scan, and
hash binding into the existing release-candidate verification path.

**Constraints:** pinned maintained actions/tools; high/critical findings fail closed; generated
evidence must be schema-valid and bound to exact artifacts; never fabricate the sandbox digest.

**Done when:** CI produces verifiable SBOM/scan artifacts for each image and the release verifier
rejects missing, stale, mismatched, or failing evidence.

## 6. Orchestration recommendation

After explicit approval:

1. Add the five items above to the existing delivery state model with stable IDs and dependencies.
2. Use one durable TaskFlow as the owner/resume context.
3. Let the scheduler select only one dependency-ready item at a time.
4. Give Codex a bounded prompt containing `Goal / Context / Constraints / Done when`.
5. Use a short-lived branch per task and run targeted checks plus the full repository gates.
6. Retry a genuine implementation failure up to three times with the exact failing output.
7. Never merge a skipped required test, weaken a gate, or mark external evidence complete.
8. Stop at external credentials, approvals, public deployment, production data, or destructive
   migrations and request explicit authority.

The scheduler should be on-demand first. A 15-minute cron is appropriate only after one complete
task has successfully passed the branch, verification, evidence, and merge lifecycle.
