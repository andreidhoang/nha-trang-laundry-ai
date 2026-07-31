# Production continuation brief

**Last reconciled:** 2026-07-31 (Asia/Ho_Chi_Minh)
**Active work item:** none; next local item is `HARDEN-CI-001`
**Code stage:** `AGENT_SHADOW` blocked externally / `PRODUCTION_HARDENING` pending locally
**Production authorization:** `NOT_AUTHORIZED` for every capability

This brief is the human-readable entry point for an engineer or coding agent resuming implementation.
It is navigation only; machine-readable contracts, the work queue, capability status, and immutable
PostgreSQL state remain authoritative in the order defined by `context/CONTINUATION_PROTOCOL.md`.

## Resume safely

```text
1. Read AGENTS.md and BUILD_ENGINEERING_SPEC.md.
2. Read delivery/LOOP_STATE.yaml, delivery/WORK_QUEUE.yaml, delivery/CAPABILITY_STATUS.yaml.
3. Run `uv run python scripts/run_delivery_loop.py` to select and assemble the authoritative packet.
4. Read the selected `task_packet` and this brief.
5. Implement one bounded slice, then run its targeted tests and the full verification suite.
```

`continue execute` authorizes only safe repository engineering. It does not authorize a provider call,
real-customer data, public ingress, public automation, a credential, a deployment, or a release gate.

## Architecture that must remain true

```text
untrusted language
      |
      v
Public OpenClaw (reasoning/draft only; EVAL_ONLY)
      |
      v
typed Tool Facade -> deterministic domain + policy -> approval
      |                                            |
      +---------------- no direct send ------------+
                                                   v
                                   transactional outbox / controlled manual attestation
                                                   |
                                                   v
                                      sole sender worker (not yet provider-integrated)
```

- PostgreSQL is the source of truth; every material mutation requires atomic mutation + domain event +
  audit + outbox semantics.
- The model never calculates money, decides policy/SLA/state/permission, selects a customer/contact,
  or sends a message.
- No generic tool, raw database route, shell, browser, web fetch, channel credential, or direct-send
  capability may be added to the public runtime.
- Unknown/stale business policy, configuration, feature flag, provider state, or authorization fails
  closed to `REQUIRE_HUMAN`, `DENY`, or `NOT_SUPPORTED`.
- Never record secrets, raw PII fixtures, raw provider payloads, or chain-of-thought.

## Reconciled implementation state

The following is local engineering evidence only, not release evidence:

- Full verification on 2026-07-29: **313 tests passed** with local PostgreSQL; Ruff, formatting,
  strict mypy, contract validation, and context-drift checks passed.
- Migrations through `0015_order_request_drafts.sql` are forward-only and applied by the local test
  database. Do not rewrite deployed migration files.
- The fixed Tool Facade has ten contract-defined tools, strict unknown-field rejection, Ed25519 Runner
  claims, and server-bound contact/order/public-code preflights.
- The runner has a hard 20-second runtime deadline, bridge revocation, hash-only tool ledger, and durable
  `MODEL_TIMEOUT` recovery record.
- The release-gate boundary validates the normative JSON Schema, deployed commit/stage/capability,
  JCS payload hash, three separated trusted signatures, chronology, expiry, and every referenced
  artifact hash. Provider-backed Runner calls additionally require the resulting exact authorization.
- Trusted release signers load from a public-key-only registry bound to an out-of-band SHA-256 pin.
  `scripts/verify_release_candidate.py` provides a sanitized fail-closed intake command; it never
  creates keys, signatures, approvals, capability state, or release evidence.
- The runtime registry now hash-pins the schema-valid provider data-control review. Artifact
  verification cross-checks its provider/model/OpenClaw scope, DEC-006 lifecycle, required policy,
  release effects, and every registry status; the pinned review remains explicitly incomplete.
- Offline OpenClaw verification now binds the observed CLI version and build revision to the registry,
  validates config/plugin/security/npm boundaries, and records sanitized non-release evidence at
  `evidence/agent-shadow/openclaw-offline-verification-v1.json` with zero critical audit findings.
- The OpenClaw JSON5 configuration is parsed structurally and its sandbox image is governed by a
  typed registry pin plus schema-validated scan/SBOM evidence. The placeholder digest remains
  deliberately unverified, so it contributes a ninth release blocker and is EVAL_ONLY.
- Context drift validation now proves every work item's declared sources and contracts are reachable
  from its selected context domains. The `AGENT-001` packet includes signer, provider-data, and
  container-scan schemas before sensitive release-boundary work begins.
- Capability status and its human-readable reporter now revalidate any `AUTHORIZED` entry against
  the signed manifest, hash-pinned signer registry, deployed commit/stage/capability, artifact hashes,
  activation window, and current time. `NOT_AUTHORIZED` entries cannot retain stale authority fields.
- Implemented P0 local paths are prompt/tool injection, model timeout, bound-request IDOR,
  public-status IDOR, approval-field tamper, post-approval edit, and manual-attestation/worker mutual
  exclusion. Each synthetic result stays `SKIP` because it is not a PRIMARY provider evaluation.
- Manual-send storage is restricted to `SHADOW` plus synthetic `INTERNAL_TEST`; marketing and any
  unconfigured real channel remain blocked until the owner policy/channel decision exists.
- `P0-KILL-SWITCH-INFLIGHT` has a PostgreSQL-backed, pinned-fixture degraded-path preflight. Disabled,
  missing, and expired gate state holds a pending automated envelope before execution; it remains a
  synthetic `SKIP`, not provider or release evidence.
- Audit-write rollback, stale flag storage, STOP/outbox race, generic unavailable status rendering,
  ambiguous opt-out, and forged-consent denial have executable local preflights. Their safety assertions
  pass, but every result remains a non-primary `SKIP`.
- Standard-wash tier/minimum pricing, promotion expiry/unresolved eligibility, range pricing, sheet
  ambiguity, and delivery distance/vehicle boundaries use pinned fixtures and deterministic domain
  engines. Their exact assertions pass without granting provider or release evidence.
- Quote lifecycle, unresolved tax, R1 capacity, personalized-price approval, correction containment,
  incident intake, deterministic list-price disclosure, and bound intake creation have executable
  local preflights. The fixture and assertion registries have no unimplemented entries.
- `evidence/agent-shadow/local-synthetic-suite-v1.json` covers all 32 manifest cases as sanitized
  `DETERMINISTIC_DEGRADED` / `SKIP` summaries and pins evaluator/runtime/release-boundary artifact
  hashes. It is
  explicitly non-release and not PRIMARY-provider evidence. The fail-closed rollback procedure is in
  `evidence/agent-shadow/rollback-assessment-v1.yaml`.
- Eval manifest and registry implementation statuses now match their computed zero-unimplemented
  counts. Validation rejects stale implementation blockers, and the local evidence bundle derives its
  remaining blocker list directly from the normative manifest.

## Next implementation sequence

Work in this order unless a higher-authority contract changes it. After each slice, update its task
packet, this brief, the relevant machine status, and tests. Do not mark `AGENT-001` complete from local
hardening evidence.

1. **Local production hardening.** Execute `HARDEN-CI-001`, `OBSERVABILITY-001`, `POLICY-001`, and
   `CONTAINER-001` as independent reviewable tasks. Run `SUPPLYCHAIN-001` only after its CI/container
   dependencies complete. These tasks may proceed while `AGENT-001` is externally blocked, but their
   engineering evidence does not authorize real data or release.

2. **Integrated runtime evaluation.** The provider-independent fixture/assertion backlog is complete.
   After the external prerequisites are resolved, run a separately
   controlled non-production integration harness for PRIMARY, fallback, and deterministic-degraded
   paths. It must pin the runtime/model/prompt/tool/config artifacts, validate result schema, and retain
   only permitted hash-safe evidence. No synthetic result may be relabeled as PRIMARY or release-ready.

3. **External/provider prerequisites — blocked, not improvable by code alone.**
   - `DEC-006`: obtain Security/Privacy decisions for training, retention, region, deletion,
     subprocessors, incident terms, and dedicated credential use.
   - Prove a supported OpenClaw Responses `store:false` route with a non-production dedicated API
     credential, then capture/assert the effective request without PII or secrets.
   - Pin an immutable model release ID. Moving aliases remain EVAL_ONLY.
   - Supply an immutable sandbox image digest with schema-valid vulnerability scan evidence and a
     hash-pinned SBOM; the checked-in placeholder cannot be used for release.

4. **`SECURITY-001` only after both tracks are complete.** It depends on declared `AGENT-001`
   acceptance plus observability, policy, and supply-chain hardening. It requires real security, OIDC,
   PITR/restore, incident, and kill-switch drills and is not authorized by passing unit tests.
   `SHADOW-001`, `CHANNEL-001`, and customer-facing automation remain downstream.

## Acceptance and verification

For every code slice, run the targeted tests first. Before handoff, run:

```text
uv sync --all-packages --all-groups
uv run ruff check .
uv run ruff format --check .
$env:DATABASE_URL='postgresql://app:app@localhost:5432/nha_trang_laundry'
uv run pytest
uv run mypy apps packages
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv run python scripts/report_delivery_status.py
```

`AGENT-001` is complete only when its queue-declared evidence exists: P0 evaluated across required
runtime paths, tool-escape evidence, immutable runtime pin, provider-storage verification, rollback
assessment, and the declared acceptance commands. A signed release manifest and gate evidence are still
separate requirements.

## Known blockers and decisions

| ID / blocker | Effect | Required owner/external action |
|---|---|---|
| `DEC-006` provider data governance | No real-customer model use, public ingress, or automated send | Security/Privacy approval and verified provider configuration |
| immutable model release unset | Cannot identify a release candidate | Provider/runtime release selection and verification |
| OpenClaw `store:false` unproven | Cannot satisfy data policy | Supported route plus effective-request integration evidence |
| dedicated service credential unverified | No production provider integration | Create and verify dedicated non-personal credential |
| scanned sandbox image digest absent | Public-cell container cannot be released | Supply immutable digest, passing scan evidence, and hash-pinned SBOM |
| `DEC-005` official channel | No public channel/manual real channel | Business owner selects supported official channel and policy |
| PRIMARY/fallback provider datasets incomplete | No G1 P0 pass | Execute integrated provider paths and calibrated grading |
| PITR, incident, kill-switch drills | No G1 readiness | `SECURITY-001` controlled operations work |

## Handoff template

Every continuation response or PR handoff states:

1. requirement/contract touched;
2. code and test evidence, including command results;
3. migration/rollback impact;
4. unresolved assumptions and decision IDs;
5. confirmation that authorization remains `NOT_AUTHORIZED` unless a signed gate manifest proves
   otherwise.
