# TASK-agent-001 — constrained Shadow runtime completion

**Goal:** finish the executable evaluation and release-evidence portion of `AGENT-001` without
enabling any customer-facing or real-customer capability.

**Domains:** `runtime_architecture`, `agent_tools`, `evaluation_release`, `privacy_consent`  
**Stable work item:** `AGENT-001`  
**Stage:** M4  
**Risk:** HIGH

**Continuation brief:** `context/PROJECT_CONTINUATION.md` is the reconciled cross-project handoff.
Read it after assembling this task's context packet; it cannot override contracts, queue state, or gates.

## Completed core (do not reimplement)

- `packages/contracts`: strict OpenAPI operation/response registry, one server-selected capability
  per short-lived Runner claim, and fail-closed release registry.
- `apps/public-agent-tools`: dedicated fixed-route Facade with Ed25519 Runner JWT verification,
  server-derived bindings and no backend fallback.
- `apps/worker`: bounded draft-only Runner, loopback bridge, 6-call/mutation budgets, and durable
  `agent_runs` / hash-only `agent_tool_calls` ledger via migrations `0009` and `0010`.
- `runtime/openclaw/public-cell`: pinned EVAL_ONLY OpenClaw configuration and the 10 fixed plugin
  tools. No provider call or public ingress has been made.
- `packages/evals`: one pinned, PII-free P0 prompt-injection fixture; deterministic schema/trace/
  safety graders; and a schema-valid **non-release** result record. The synthetic preflight calls the
  fixed fact-recording Facade with server-derived bindings and proves a substituted order path is
  rejected. Its status remains `SKIP` because semantic Vietnamese and PRIMARY provider grading are
  unavailable.
- `apps/worker`: the Runner applies a wall-clock deadline to synchronous runtime invocation and
  revokes its bridge before returning a timeout handoff; a late runtime tool call is rejected.
- `packages/evals` / `apps/worker`: the model-timeout P0 fixture and synthetic runner preflight are
  implemented. They verify empty tool/side-effect traces, no automatic fallback message, a timeout
  failure code of `MODEL_TIMEOUT`, and retention of the durable run for staff recovery. The result is
  deliberately `SKIP` for the non-PRIMARY path.
- `packages/evals`: bound-request and public-status IDOR preflights call their fixed Facade operation
  with a substituted server-bound path. Both receive a 403 before a backend mutation/data projection;
  traces keep only field names and hashes. Public-status stays `SKIP` additionally because the channel
  generic-unavailable renderer has not yet been implemented or graded.
- `packages/evals`: approval-reason tampering preflight sends forbidden server-owned approval fields
  through the fixed Facade; it receives 422 before its backend and leaves no approval request. Rejected
  attack field names are retained only as hash-safe test evidence.
- `packages/evals` / `packages/db`: the post-approval content-edit P0 fixture and PostgreSQL-backed
  synthetic preflight are implemented.  It approves one hash-bound message, submits an edited
  revision to the actual pre-provider execution claim, verifies the stale hash rejection leaves the
  approval in `APPROVED` with zero `approval_executions`, and records a non-release `SKIP` result.
- `packages/db` / `packages/evals`: migration `0011` adds a transactional pre-channel manual-send
  envelope and immutable attestation ledger.  The envelope locks the same approval used by the worker;
  after `MANUAL_SEND_RECORDED`, a worker claim is rejected and no `approval_executions` row exists.
  The only currently configured channel is synthetic `INTERNAL_TEST`; marketing, unconfigured channels,
  and non-Shadow stages fail closed pending owner channel policy.
- `packages/contracts` / `apps/worker`: release manifests receive runtime schema, JCS payload,
  trusted separated-signature, chronology, expiry, deployment-envelope, and referenced-artifact hash
  verification. Provider-backed Runner calls require the resulting authorization for their exact
  commit, stage, and capability; changing the runtime registry alone cannot enable provider calls.
- `packages/contracts` / `scripts`: the three-function trusted signer registry is public-key-only,
  checksum-pinned out of band, and separation-of-duty validated. The candidate verification CLI emits
  only a sanitized verified envelope and cannot generate or record release authority.
- `runtime/model-registry-v1.yaml` / `packages/contracts`: provider data-control evidence is now a
  schema-valid, hash-pinned runtime artifact. Its scope, DEC-006 state, required policy, release
  effects, and verification statuses must exactly match the registry or startup verification fails.
- `scripts/verify_agent_runtime.py` now verifies the observed OpenClaw executable version and build
  revision rather than echoing configuration. Its config, plugin, security and npm checks are captured
  as hash-pinned, explicitly non-release offline evidence with no provider request.
- `runtime/model-registry-v1.yaml` / `packages/contracts`: the public-cell sandbox image now has a
  typed immutable digest pin. Release verification parses the JSON5 configuration and requires
  schema-valid scan evidence plus a hash-pinned SBOM; the placeholder remains unverified and blocked.
- `context/CONTEXT_MAP.yaml` / `scripts/check_context_drift.py`: work-item normative sources and
  contracts must be reachable from their declared context domains. The active packet now includes
  all signer, provider-data, and sandbox scan schemas used at the release boundary.
- `delivery/CAPABILITY_STATUS.yaml` / `scripts`: authorization status and reporting now fail closed
  unless the complete release manifest and hash-pinned trust root cryptographically verify for the
  exact deployed commit, stage, capability, artifact set, activation window, and current time.

## Constraints

- The public runtime is a reasoning cell only: no pricing, policy, permissions, order state, browser,
  shell, generic tool, raw DB, customer-ID selection or direct send.
- All unknown business policy, malformed tool output and unavailable provider paths return a safe
  human handoff. Keep `automatic_send_authorized=false` and all capability authorizations disabled.
- Preserve atomic domain-event/audit/outbox behavior and forward-only migrations. Never persist
  chain-of-thought, raw provider response, secrets or raw PII test fixtures.
- Do not claim P0, provider-storage or release gates pass without durable, reviewable evidence.

## Current bounded slice

All seed-manifest fixture payloads and declared assertions now have pinned local implementations.
PostgreSQL-backed preflights cover quote revision invalidation, estimate acknowledgment, personalized
approval binding, correction containment, incident intake, and server-bound intake drafts. Typed
domain preflights cover tax finality, zero-authority R1 capacity, pricing, promotions, catalog and
delivery. `evidence/agent-shadow/local-synthetic-suite-v1.json` captures sanitized coverage for all
32 cases and pins the evaluator, runtime, and release-boundary artifacts;
`evidence/agent-shadow/rollback-assessment-v1.yaml`
documents forward-only rollback. Every synthetic result remains a non-release `SKIP`.
The manifest/registry implementation statuses are synchronized with computed coverage, and validation
rejects any stale fixture/assertion blocker instead of allowing contradictory release evidence.

No further provider-independent manifest backlog remains. The next work is the controlled integrated
runtime evaluation, which cannot execute until the external provider/runtime prerequisites below are
resolved. Do not synthesize PRIMARY results or turn a `SKIP` into a `PASS`:

1. exact immutable model release;
2. OpenClaw `store:false` route;
3. effective provider storage/retention verification;
4. dedicated credential verification and Security/Privacy approvals;
5. immutable scanned sandbox image digest, passing scan evidence, and hash-pinned SBOM;
6. required primary/fallback/degraded provider evaluations and dataset minima.

## Acceptance before completion

- `uv run pytest`
- `uv run mypy apps packages`
- `uv run python scripts/verify_contracts.py`
- `uv run python scripts/check_context_drift.py`
- P0 integrated eval result plus tool-escape report, runtime pin, provider-storage verification and
  rollback assessment recorded under `evidence/`.

`AGENT-001` remains **IN_PROGRESS** until all queue-declared evidence exists; `INTERNAL_SHADOW`
remains **NOT_AUTHORIZED** regardless of local code or synthetic test success.
