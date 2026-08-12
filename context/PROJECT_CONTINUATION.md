# Production continuation brief

**Last reconciled:** 2026-08-12 (Asia/Ho_Chi_Minh)
**Active work item:** none; `RUNTIME-FREEZE-001` is the next controller-selected candidate
**Active branch:** `main` on `andreidhoang/nha-trang-laundry-ai` (the working repository).
`ngocduong-coder/nha-trang-laundry-ai` is read-only `upstream`; contribute there by pull request only.
**Code stage:** `PRODUCTION_HARDENING`; production spec pack authored, nothing provisioned
**Production authorization:** `NOT_AUTHORIZED` for every capability

## Read this first — 2026-08-12 planning change

`RESPONSES-RUNTIME-001` completed on 2026-08-11 and was the last dependency-free local item. The
queue drained: every remaining item routed through a blocked node, so the binding constraint became
external decisions rather than engineering.

Four ADRs and three specifications were authored to define the path to production. **None of them
provisions infrastructure, resolves a decision by fiat, or authorizes a capability.**

| ADR | Effect on the queue |
|---|---|
| [ADR-0004](../docs/adr/0004-runtime-consolidation-and-frozen-openclaw-evidence.md) | Freezes `AGENT-001`, `OPENCLAW-REPACK-001`, `RUNTIME-SECURITY-001` as immutable blocked history; creates `AGENT-002` as the G1 evidence carrier; re-points `SECURITY-001` and `RUNTIME-PARITY-001` |
| [ADR-0005](../docs/adr/0005-official-channel-selection-zalo-oa.md) | Selects official Zalo OA as the production channel; Telegram is a sandbox only |
| [ADR-0006](../docs/adr/0006-two-party-release-authorization.md) | Amends the three-signature gate to two parties with schema-required compensating controls |
| [ADR-0007](../docs/adr/0007-production-deployment-topology.md) | Fixes the three-zone, two-host topology; hosting provider still unselected |

New specifications: [`CHANNEL_ADAPTER_SPEC_V1.md`](../specs/CHANNEL_ADAPTER_SPEC_V1.md),
[`PRODUCTION_OPERATIONS_SPEC_V1.md`](../specs/PRODUCTION_OPERATIONS_SPEC_V1.md),
[`PUBLIC_CUSTOMER_POLICY_SPEC_V1.md`](../specs/PUBLIC_CUSTOMER_POLICY_SPEC_V1.md).

New contracts: channel inbound envelope, channel outbound receipt, public policy bundle, and
release-gate manifest v2 (mechanically derived from v1; `verify_contracts.py` fails if it diverges
beyond the ADR-0006 amendment).

Nineteen work items were added across four phases. The longest lead times are calendar-bound and
**start before any code**: `SHOP-INSTRUMENT-001` (4–6 weeks of real cycle and delivery-cost data,
without which `SHADOW-001` has no valid denominator), `CHANNEL-ZALO-APPLY-001` (2–8 weeks of external
OA verification) and `PROVIDER-ACCESS-001` (resolves `DEC-006`).

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

## Approved architecture direction — not queue or release authorization

ADR-0003 makes `ConstrainedAgentRuntime` the stable public-agent boundary and selects a bounded custom
OpenAI Responses adapter as the preferred production target. The current OpenClaw work remains
immutable `EVAL_ONLY` comparison/rollback evidence; it is not silently complete and must not be deleted
until custom-runtime parity, provider-data, security and rollback gates pass. Channel adapters and the
Staff PWA remain independent of either runtime. Telegram Bot API is an engineering-sandbox candidate;
official Zalo OA is a later provider candidate; DEC-005 remains open and Zalo Personal is prohibited.
The daily operations dashboard uses versioned deterministic read models, while AI may provide only a
grounded read-only explanation. This direction creates no provider call, credential, public ingress,
send, deployment or capability authority and does not replace the current machine-readable queue.

The machine plan now separates three work items: `RESPONSES-RUNTIME-001` builds the minimum adapter
with scripted transport and can proceed without external authority; `RUNTIME-PARITY-001` waits for the
custom candidate, comparator and DEC-006/provider evidence; `OPENCLAW-RETIRE-001` is the only item that
may later remove the public dependency. This separation preserves the immutable blocked history of
`AGENT-001` and `OPENCLAW-REPACK-001` while allowing independent local progress.

## Historical engineering handoff: OPENCLAW-REPACK-001 Phase B1

> **FROZEN by [ADR-0004](../docs/adr/0004-runtime-consolidation-and-frozen-openclaw-evidence.md) on
> 2026-08-12. Do not resume.** Everything from here to the end of the r1 section is preserved as the
> record of what was attempted and why it stopped. The "Exact next sequence" it contains — obtaining
> authorization for a hosted supply-chain workflow run — is exactly the effort ADR-0004 halts. G1
> agent evidence is carried by `AGENT-002` instead.

The local workflow now has the fail-closed dependency graph
`openclaw-repackage-windows + openclaw-repackage-linux -> openclaw-cross-platform-compare ->
supply-chain`. Both clean hosted build jobs pin Python `3.12.13`, uv `0.11.32`, Node `24.18.0`, the
npm lockfile, manifest v2, and the exact source/replacement materials. They emit independently named
r2 artifacts and schema-valid platform results bound to the same Git commit and full runner identity.

The comparison job downloads both artifacts, validates commit/runner/manifest/source/artifact metadata,
compares the tarballs byte-for-byte, and publishes only the compared r2 plus its typed comparison
record. The Linux supply-chain job depends on this comparison, verifies the downloaded record, uses
`cmp` against the committed r2, then copies those exact compared bytes into the Docker build context.
Final evidence hash-binds the comparison, sanitized runtime/OpenClaw audit, OCI provenance, normalized
zero-HIGH/CRITICAL scan, and CycloneDX SBOM. Every workflow/job permission remains `contents: read`.

Local Phase B1 verification passed `23` focused workflow/contract tests, `30` runtime/evidence tests,
`9` plugin/undici tests, and `543` guarded PostgreSQL tests with no final failures or skips. The r2
artifact remains byte-identical at SHA-256
`0c4d5d0dcdccde0290932c9baf17c1e371a12d46660ebba32dfa3b878124edab`; the complete npm audit remains
zero critical/high with seven visible moderate findings. Ruff, format, mypy, contracts, context drift,
runtime verification, delivery projection, and diff validation passed.

Phase B2 remains blocked pending independent Security/Supply-chain review, authorization to create the
exact commit and push the dedicated branch, authorization to run the reviewed hosted workflow, hosted
Windows/Linux byte identity, and exact r2 OCI SBOM, SLSA provenance, and zero-HIGH/CRITICAL scan
evidence. Phase B1 granted no commit, push, remote run, credential, provider/customer data, release,
deployment, or capability authority.

## Phase A r2 engineering record

The dirty working tree contains the local-only `DERIVED`/`EVAL_ONLY` r2 expansion authorized on
2026-08-06. It starts from immutable disabled r1 and replaces exactly `brace-expansion 5.0.8 ->
5.0.9`, `fast-uri 3.1.4 -> 3.1.5`, `ip-address 10.2.0 -> 10.3.1`, and `undici 8.5.0 -> 8.9.0`.
The undici change permits only its exact `package/package.json` dependency value and corresponding
allowlisted shrinkwrap records.

Two independent canonical builds of
`runtime/openclaw/repack/dist/openclaw-2026.7.1-2-nha-trang-r2.tgz` were byte-identical: SHA-256
`0c4d5d0dcdccde0290932c9baf17c1e371a12d46660ebba32dfa3b878124edab`, npm integrity
`sha512-yFjlTDU5sv+4lPR7kIM+iIsMAqkkfY2Jd07+f0wubCPK6nFl3vYFHcELtln+q3/9x1flCLtxp3G7ukAFougSgA==`,
size `19,535,080` bytes. Independent verification passed, the complete installed npm tree reported
zero critical/high findings (seven moderate), focused tests passed `30/30`, plugin tests passed `9/9`,
and the guarded PostgreSQL suite passed `535/535` with no skips. Ruff, format, mypy, contracts,
context drift, migrations, runtime verification, and capability-status reporting also passed.

Completion remains blocked on separately authorized hosted Windows/Linux reproducibility, exact r2
image SBOM, SLSA provenance, and zero-HIGH/CRITICAL scan evidence plus independent Security and
Supply-chain review. Do not push, open a pull request, trigger a hosted workflow, release, deploy, use
provider/customer data, or authorize any capability under the Phase A record.

## Historical r1 engineering handoff — superseded by r2

The current branch contains a **dirty, uncommitted implementation that passes every local acceptance
gate**. Preserve it; do not restart the work item, discard its files, mark it complete, or substitute
local results for hosted evidence. Delivery recorded the hosted-run blocker and the controller now
selects no independent ready item. Obtain a fresh generation before any later unblock or completion.

### Proven during this attempt

- The exact upstream `openclaw@2026.7.1-2` tarball remained bound to registry integrity
  `sha512-ycF3yPcbjN6bUPeaUx6Mh6vze1hQWoD3CT/wWcmD7a8xaHHHRUaAlaq+lFxMHf1ssEgODVAwjlzYqp2twkYZ7g==`, SHA-256
  `5bb525f36f471a41239615d321c441778c7e1c007018ed6d84b795be77803276`, and size
  `19,728,152` bytes.
- Exact registry tarballs were reviewed for `brace-expansion` `5.0.7 -> 5.0.8` and `fast-uri`
  `3.1.2 -> 3.1.4`. Both replacements satisfy the upstream caret ranges (`^5.0.5`, `^3.0.1`).
- Two independent repackage builds were byte-identical. The candidate output is
  `runtime/openclaw/repack/dist/openclaw-2026.7.1-2-nha-trang-r1.tgz`, SHA-256
  `8478f9110425449a7162a8fefd0ca866594e91a584dc681f9a382b8cd0454dcc`, integrity
  `sha512-8Mx+tv9tYy53lIhvZM9aMGF8OATg/kovktAJkkWlYFnZAJ5DClmXsflBl3moPZjMMiNAfbXdnQColWuasg+Rlw==`, size `19,728,669` bytes.
- After binding the plugin lock to the local repackage and reconciling npm's two stale same-version
  nested lock entries, a clean `npm ci --ignore-scripts` installed 304 packages. A complete-tree
  `npm audit --audit-level=high --json` then reported `critical=0`, `high=0`, `moderate=7` and exited
  successfully. No omission, waiver, or severity reclassification was used.
- The plugin candidate tarball was regenerated successfully. The tracked rollback-safe candidate now
  hashes to `sha256:617dcbdede123cb76cb845fb1cdb823fdf9375f6e629320347461d76c0306eb1`.
- Docker Buildx resolved the pinned Node base index
  `node:24.15.0-alpine3.23@sha256:d1b3b4da11eefd5941e7f0b9cf17783fc99d9c6fc34884a665f40a06dbdfc94f`.
- A local Linux OCI build produced BuildKit SLSA v1 provenance. The OCI verifier binds the root image
  index, platform manifest, config, and attestation digests. Pinned Trivy `0.72.0` produced a CycloneDX
  SBOM and zero critical/high SARIF results after the final stage pinned `libcrypto3`/`libssl3`
  `3.5.7-r0` and removed the unused global npm tree.
- The final repository gates passed: 521 PostgreSQL tests, 16 focused runtime tests, 3 plugin tests,
  Ruff, format, mypy, migrations, contracts, context drift, delivery status, reproducible packaging,
  independent verification, complete-tree npm audit, and runtime verification.

### Implemented and locally verified

- A strict repackage manifest schema, deterministic builder, independent registry/tar/lock verifier,
  OCI SLSA-provenance verifier, non-root EVAL_ONLY Dockerfile, rollback README, and negative tests.
- Plugin package/lock binding to the repacked tarball and runtime-registry/inventory fields separating
  upstream integrity from repacked integrity.
- A typed public-cell runtime-image pin which remains `verified: false`; release blocking now includes
  `PUBLIC_CELL_RUNTIME_IMAGE_NOT_VERIFIED`.
- Hosted supply-chain workflow steps for reproducible rebuild, full npm/OpenClaw audits, BuildKit
  provenance, CycloneDX SBOM, Trivy critical/high scan, and digest-bound evidence upload.

### Remaining blocked state

- The reviewed GitHub workflow has not run on an exact commit, so hosted provenance, SBOM, normalized
  zero-high scan evidence, and the hosted evidence bundle are absent.
- Creating/pushing a remote branch, opening a pull request, or triggering the workflow requires
  explicit external authorization. Local OCI results are engineering diagnostics only.
- Delivery correctly records `OPENCLAW-REPACK-001` as `BLOCKED`; no schema-valid completion evidence
  exists, the runtime image pin remains `verified: false`, and every capability remains
  `NOT_AUTHORIZED`.
- Downloaded source materials, OCI archives, SBOMs, scans, and Trivy caches remain outside tracked
  repository content. The reviewed package artifact is the named tarball under `repack/dist`.

### Exact next sequence

1. Obtain explicit authorization to create/push the exact branch/commit and run the reviewed GitHub
   supply-chain workflow; do not use a moving or unrelated commit.
2. Retain and validate the workflow's exact OCI provenance, CycloneDX SBOM, normalized zero-high scan,
   complete npm/OpenClaw audits, and evidence bundle. A workflow definition alone is not evidence.
3. If hosted checks pass, create schema-valid delivery evidence, obtain a fresh controller generation,
   unblock the item, and complete it through `record_delivery_evidence.py`.
4. Reconcile `RUNTIME-SECURITY-001` through a new controller-selected corrective path; never rewrite its
   immutable blocked history or treat this EVAL_ONLY correction as release authority.

## Architecture that must remain true

```text
untrusted language -> authenticated adapter -> durable inbox
                                            |
                                            v
                         ConstrainedAgentRuntime (reasoning/draft only)
                           custom Responses target
                           OpenClaw EVAL_ONLY comparator
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

## Historical reconciled implementation state through 2026-08-02

The following predates the active repackage slice and is local engineering evidence only, not release
evidence. Current branch verification must be rerun before relying on these baselines:

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
packet, this brief, the relevant machine status, and tests.

1. **`RUNTIME-FREEZE-001` — apply ADR-0004 to the machine state.** Its packet is
   `context/tasks/TASK-runtime-freeze-001.md`. Nothing under `evidence/` may be modified; the three
   frozen items keep `BLOCKED` status and gain only a `blocking_condition` note. Freezing is a
   scheduling decision, never a completion claim.

2. **Start the three calendar-bound external items immediately** — `SHOP-INSTRUMENT-001`,
   `CHANNEL-ZALO-APPLY-001`, `PROVIDER-ACCESS-001`. They need no code and gate everything
   downstream. Every week they are not started is a week added to the end of the project.

3. **OpenClaw evidence track is frozen, not resumed.** Do not advance `OPENCLAW-REPACK-001` or
   `RUNTIME-SECURITY-001`. Do not rewrite their immutable history. The rollback target for the
   custom runtime is deterministic degraded mode plus human handling, which is already built and
   tested — not a second agent runtime (ADR-0004).

4. **`RUNTIME-PARITY-001` — absolute bar, not a bake-off.** Rescoped by ADR-0004 to prove the custom
   adapter meets the declared P0, latency and cost bar with a rehearsed rollback to degraded mode.
   No synthetic result may be relabeled as PRIMARY, provider-backed or release-ready.

4. **External/provider prerequisites — still blocked, not improvable by code alone.**
   - `DEC-006`: obtain Security/Privacy decisions for training, retention, region, deletion,
     subprocessors, incident terms, and dedicated credential use.
   - Capture/assert the effective `store:false` request for each compared runtime route using a
     non-production dedicated API credential and no PII/secrets.
   - Pin an immutable model release ID. Moving aliases remain EVAL_ONLY.
   - Supply immutable candidate deployment inventories/image digests with schema-valid scan/SBOM
     evidence; checked-in placeholders cannot be used for release.

5. **`OPENCLAW-RETIRE-001` only after accepted parity.** Remove OpenClaw from public routing/deployment
   first, rehearse rollback, then remove mutable build inputs while retaining immutable evidence and
   Private Owner OpenClaw. Do not combine cleanup with launch or capability authorization.

6. **`SECURITY-001` only after both tracks are complete.** It depends on declared `AGENT-001`
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
npm --prefix runtime/openclaw/public-cell/plugin test
```

For `OPENCLAW-REPACK-001`, also run the acceptance commands declared in `delivery/WORK_QUEUE.yaml`,
including the reproducible builder, independent verifier, complete-tree npm audit, runtime verifier,
and `pytest --require-postgres-integration`. The hosted image/SBOM/provenance/scan result is separately
required. A signed release manifest and capability gate evidence remain distinct and absent.

## Known blockers and decisions

| ID / blocker | Effect | Required owner/external action |
|---|---|---|
| hosted OpenClaw image evidence absent | `OPENCLAW-REPACK-001` cannot complete | Run the reviewed GitHub workflow and retain digest-bound provenance/SBOM/zero-high scan evidence |
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
