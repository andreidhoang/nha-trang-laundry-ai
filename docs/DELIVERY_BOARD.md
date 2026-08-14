# Delivery board

Generated from `delivery/WORK_QUEUE.yaml` on **2026-08-13**. It goes stale the moment the queue
moves; when the two disagree, the queue is right. Run `uv run python scripts/run_delivery_loop.py`
for the authoritative next item and `uv run python scripts/report_delivery_status.py` for
machine-readable capability status.

[`delivery/WORK_QUEUE.yaml`](../delivery/WORK_QUEUE.yaml) is the execution source,
[`delivery/PROGRAM_PLAN.yaml`](../delivery/PROGRAM_PLAN.yaml) defines stable phases, and
[`delivery/CAPABILITY_STATUS.yaml`](../delivery/CAPABILITY_STATUS.yaml) is the production-
authorization source. A code status never authorizes a release.

**70 items: 33 complete, 16 blocked, 21 pending.**
Every capability reads `NOT_AUTHORIZED`.

**Exactly one pending item is buildable: `TEST-ISOLATION-001`**, which the controller selects. Its
only dependency, `ENV-INTEGRITY-001`, is complete and it carries no decision blocker. The other 20
pending items each trace to an owner decision or an external party; see
[`PATH_TO_PRODUCTION_REVIEW.md`](PATH_TO_PRODUCTION_REVIEW.md) §5 and §6a. Earlier revisions of this
board said no item was buildable; that was wrong, and `uv run python scripts/run_delivery_loop.py`
is the authority.

The two newest items, `MODEL-ROUTE-001` and `MULTIMODAL-PERCEPTION-001`, enter `Blocked` by
construction: they specify the tiered-inference and perception direction assessed in
[`TIERED_INFERENCE_AND_MULTIMODAL_ASSESSMENT.md`](TIERED_INFERENCE_AND_MULTIMODAL_ASSESSMENT.md)
and proposed in [ADR-0008](adr/0008-inference-topology-and-multimodal-scope.md). `EVIDENCE-REPIN-001`
stood in front of both and was completed 2026-08-13, so what remains for them is decisions: ADR-0008
acceptance and `DEC-009`.

### FOUNDATION

| ID | Task | Depends on | Status | Open decisions |
|---|---|---|---|---|
| `FOUNDATION-001` | Python workspace, contracts, context, and delivery foundation | — | Complete | — |
| `DB-001` | PostgreSQL migration and atomic transaction foundation | `FOUNDATION-001` | Complete | — |

### IDENTITY_CONTROL

| ID | Task | Depends on | Status | Open decisions |
|---|---|---|---|---|
| `CONFIG-001` | Immutable generic configuration publication primitive | `DB-001` | Complete | — |
| `IDENTITY-001` | Staff identity, server-enforced RBAC, MFA boundaries, and audit primitives | `DB-001` | Complete | — |

### DOMAIN_CORE

| ID | Task | Depends on | Status | Open decisions |
|---|---|---|---|---|
| `DOMAIN-001` | Canonical enum registry, service normalization, and legacy aliases | `CONFIG-001` | Complete | — |
| `DOMAIN-002` | Pricebook import manifest and canonical service/rule counts | `DOMAIN-001` | Complete | — |
| `DOMAIN-003` | Deterministic fixed, range, tier, aggregate, and estimate pricing engine | `DOMAIN-002` | Complete | — |
| `DOMAIN-004` | Promotion, delivery, SLA, and unresolved-policy decision boundaries | `DOMAIN-003` | Complete | — |
| `DOMAIN-005` | Immutable quote snapshots and reproducible calculation traces | `DOMAIN-003` | Complete | — |

### OPERATIONS_CONTROL

| ID | Task | Depends on | Status | Open decisions |
|---|---|---|---|---|
| `OPERATIONS-001` | Orders, approvals, inbox/outbox, idempotency, audit, and Staff PWA slice | `IDENTITY-001`, `DOMAIN-004`, `DOMAIN-005` | Complete | — |

### AGENT_SHADOW

| ID | Task | Depends on | Status | Open decisions |
|---|---|---|---|---|
| `AGENT-001` | Isolated OpenClaw Concierge, typed Tool Facade, model registry, and Shadow evals | `OPERATIONS-001` | Blocked | — |
| `RESPONSES-RUNTIME-001` | Minimum bounded custom Responses adapter behind ConstrainedAgentRuntime | `OPERATIONS-001` | Complete | — |
| `RUNTIME-PARITY-001` | Custom Responses runtime absolute P0, latency, cost, and rollback bar | `AGENT-002` | Pending | DEC-006 |
| `OPENCLAW-RETIRE-001` | Reversible retirement of OpenClaw from the public runtime path | `RUNTIME-PARITY-001` | Pending | — |
| `RUNTIME-FREEZE-001` | Freeze the OpenClaw evidence track and re-point the dependency graph | — | Complete | — |
| `PROVIDER-ACCESS-001` | Dedicated provider organization, credential, and data-control posture | — | Blocked | — |
| `EVIDENCE-REPIN-001` | Re-establish the local synthetic evidence pin so the core can change | — | Complete | — |
| `PROVIDER-TRANSPORT-001` | Real Responses provider transport behind the existing injectable boundary | `PROVIDER-ACCESS-001` | Pending | — |
| `EVAL-SYNTHETIC-COMBINATORIAL-001` | Synthetic combinatorial suite generated from the deterministic domain | `DOMAIN-005`, `RESPONSES-RUNTIME-001`, `EVIDENCE-REPIN-001` | Blocked | — |
| `CORPUS-CONSENT-001` | Consent basis and reviewed anonymization of real customer message history | — | Blocked | — |
| `AGENT-PIPELINE-001` | Assemble the constrained runtime into a running worker pipeline | `RESPONSES-RUNTIME-001` | Complete | — |
| `MODEL-PIN-001` | Immutable model release pin and registry artifact verification | `PROVIDER-TRANSPORT-001`, `EVIDENCE-REPIN-001` | Pending | — |
| `MODEL-ROUTE-001` | Per-role model pinning so a route is as pinned as its loosest member | `EVIDENCE-REPIN-001`, `PROVIDER-TRANSPORT-001`, `MODEL-PIN-001` | Blocked | — |
| `MULTIMODAL-PERCEPTION-001` | Perception tier that emits observations and never conclusions | `MODEL-ROUTE-001`, `CONSENT-STOP-001` | Blocked | DEC-009, DEC-006, DEC-008 |
| `EVAL-CORPUS-001` | Frozen Vietnamese regression corpus raised toward the manifest minimum | `CORPUS-CONSENT-001`, `EVIDENCE-REPIN-001` | Pending | — |
| `AGENT-002` | Custom-runtime Shadow evidence and P0 provider-backed evaluation | `AGENT-PIPELINE-001`, `EVAL-CORPUS-001`, `MODEL-PIN-001`, `PROVIDER-TRANSPORT-001`, `RUNTIME-FREEZE-001`, `EVAL-SYNTHETIC-COMBINATORIAL-001`, `EVAL-LANGUAGE-CORPUS-001` | Pending | DEC-006 |
| `EVAL-LANGUAGE-CORPUS-001` | Normal-language Vietnamese suite at the manifest distribution | `CORPUS-CONSENT-001`, `EVIDENCE-REPIN-001` | Pending | — |

### PRODUCTION_HARDENING

| ID | Task | Depends on | Status | Open decisions |
|---|---|---|---|---|
| `SUPPLYCHAIN-CI-PORTABILITY-001` | Entitlement-free immutable container scanning in release CI | `SUPPLYCHAIN-001`, `RUNTIME-EVIDENCE-PORTABILITY-001` | Complete | — |
| `RUNTIME-EVIDENCE-PORTABILITY-001` | Cross-platform pinned-runtime shim and artifact-hash portability | `HARDEN-PORTABILITY-001`, `RUNTIME-EVIDENCE-001` | Complete | — |
| `HARDEN-PORTABILITY-001` | Cross-platform type-safe delivery and automation mutexes | `HARDEN-CI-001` | Complete | — |
| `RUNTIME-SECURITY-001` | Pinned OpenClaw verifier and transitive advisory remediation | `HARDEN-PORTABILITY-001`, `SUPPLYCHAIN-001` | Blocked | — |
| `RUNTIME-EVIDENCE-001` | Explicit fail-closed evidence for blocked pinned-runtime verification | `HARDEN-PORTABILITY-001`, `SUPPLYCHAIN-001` | Complete | — |
| `OPENCLAW-REPACK-001` | Immutable EVAL_ONLY OpenClaw transitive-security repackage | `RUNTIME-EVIDENCE-PORTABILITY-001`, `SUPPLYCHAIN-CI-PORTABILITY-001` | Blocked | — |
| `HARDEN-CI-001` | Non-skippable PostgreSQL integration and OpenClaw plugin checks in CI | `OPERATIONS-001` | Complete | — |
| `OBSERVABILITY-001` | Redacted structured logs and correlation propagation | `OPERATIONS-001` | Complete | — |
| `POLICY-001` | Typed fail-closed policy decision point and capability isolation | `OPERATIONS-001` | Complete | — |
| `CONTAINER-001` | Reproducible non-root production images for internal services | `OPERATIONS-001` | Complete | — |
| `SUPPLYCHAIN-001` | SBOM, secret, dependency, license, and container scan release gates | `HARDEN-CI-001`, `CONTAINER-001` | Complete | — |
| `RELEASE-BASELINE-001` | Immutable reviewed production-hardening baseline | `SUPPLYCHAIN-001` | Complete | — |
| `WORKER-HOST-001` | PostgreSQL-backed worker supervisor, readiness, leases, and recovery | `RELEASE-BASELINE-001`, `OPERATIONS-001`, `OBSERVABILITY-001`, `POLICY-001` | Complete | — |
| `STAFF-OPS-001` | Complete staff Shadow operations and manual-send console | `RELEASE-BASELINE-001`, `OPERATIONS-001` | Complete | — |
| `HTTP-SECURITY-001` | Browser security, CSRF, OIDC integration, and abuse controls | `STAFF-OPS-001`, `IDENTITY-001`, `OBSERVABILITY-001` | Complete | — |
| `TELEMETRY-001` | OpenTelemetry metrics, traces, SLOs, and alert contracts | `WORKER-HOST-001`, `OBSERVABILITY-001` | Complete | — |
| `STAGING-001` | Private TLS staging topology, secrets, and deployment rollback | `WORKER-HOST-001`, `HTTP-SECURITY-001`, `TELEMETRY-001`, `CONTAINER-001`, `SUPPLYCHAIN-001` | Complete | — |
| `ENV-INTEGRITY-001` | Workspace import integrity independent of host file attributes | — | Complete | — |
| `DECISION-HOSTING-001` | Hosting decision packet and admissibility verdict for ADR-0007 | — | Blocked | — |
| `CHANNEL-ENVELOPE-001` | Canonical inbound envelope and outbound receipt wired to inbox/outbox | — | Complete | — |
| `CHANNEL-TELEGRAM-001` | Telegram sandbox adapter proving the channel contract end to end | `CHANNEL-ENVELOPE-001` | Blocked | — |
| `SHADOW-CONSOLE-001` | Staff Shadow console: draft review, exception queue, audit timeline | `STAFF-OPS-001`, `CHANNEL-ENVELOPE-001` | Complete | — |
| `CONSENT-STOP-001` | Consent capture, STOP suppression, and opt-out versus in-flight send race | `CHANNEL-ENVELOPE-001` | Complete | — |
| `STORE-SCOPING-001` | Enforce staff store membership on the pre-existing console routes | `SHADOW-CONSOLE-001` | Complete | — |
| `TEST-ISOLATION-001` | Isolate database integration tests from each other | `ENV-INTEGRITY-001` | Pending | — |
| `DEPLOY-TARGET-001` | Production topology, isolated agent cell host, and closed capability flags | `DECISION-HOSTING-001` | Pending | — |
| `MONITORING-001` | Telemetry collector, retention, and paging alert contracts | `DEPLOY-TARGET-001` | Pending | — |
| `SIGNER-REGISTRY-001` | Two-party release schema, verifier enforcement, and signer key ceremony | `EVIDENCE-REPIN-001` | Blocked | — |
| `RETENTION-001` | Customer data retention, redaction, and deletion jobs | `DB-001`, `OBSERVABILITY-001` | Pending | DEC-008 |
| `OPS-RUNBOOK-001` | The five G1 runbooks, each executed once by its operator | `DEPLOY-TARGET-001` | Pending | — |
| `RETENTION-STORE-001` | Separate disposable payload from the append-only ledger | `RETENTION-001` | Pending | DEC-008 |
| `SLO-VERIFY-001` | Measured verification of the declared Shadow-stage SLOs | `DEPLOY-TARGET-001`, `MONITORING-001` | Pending | — |

### REAL_SHADOW_READINESS

| ID | Task | Depends on | Status | Open decisions |
|---|---|---|---|---|
| `BACKUP-RESTORE-001` | Continuous PostgreSQL recovery and restore-drill automation | `DB-001`, `DEPLOY-TARGET-001`, `STAGING-001` | Blocked | — |
| `SECURITY-001` | Identity, privacy, PITR, incident, kill-switch, and release-readiness gates | `AGENT-002`, `OBSERVABILITY-001`, `POLICY-001`, `SUPPLYCHAIN-001`, `HTTP-SECURITY-001`, `TELEMETRY-001`, `STAGING-001`, `BACKUP-RESTORE-001`, `DEPLOY-TARGET-001`, `MONITORING-001` | Pending | DEC-006 |
| `SHADOW-001` | Internal real-order Shadow pilot and G1 evidence | `SECURITY-001`, `SHADOW-CONSOLE-001`, `SHOP-INSTRUMENT-001`, `SIGNER-REGISTRY-001`, `OPS-RUNBOOK-001`, `SLO-VERIFY-001`, `RETENTION-001`, `STORE-SCOPING-001`, `RETENTION-STORE-001` | Pending | — |
| `SHOP-INSTRUMENT-001` | Physical shop instrumentation: cycle, capacity, and delivery cost baselines | — | Blocked | — |
| `DECISION-BUSINESS-001` | Owner decision session closing DEC-001 through DEC-004 | — | Blocked | — |

### PUBLIC_ASSISTED

| ID | Task | Depends on | Status | Open decisions |
|---|---|---|---|---|
| `CHANNEL-001` | Official channel and isolated public-cell Assisted entry | `CHANNEL-ZALO-001`, `CONSENT-STOP-001`, `PUBLIC-POLICY-001`, `SHADOW-001`, `EVAL-PUBLIC-CORPUS-001` | Pending | DEC-005, DEC-006 |
| `CHANNEL-ZALO-APPLY-001` | Official Zalo OA registration and business verification | — | Blocked | — |
| `CHANNEL-ZALO-001` | Official Zalo OA adapter with verified provider behavior | `CHANNEL-ENVELOPE-001`, `CHANNEL-ZALO-APPLY-001`, `CONSENT-STOP-001` | Pending | DEC-005 |
| `PUBLIC-POLICY-001` | Published PUBLIC_CUSTOMER bundle and tested correction workflow | `DECISION-BUSINESS-001` | Pending | — |
| `EVAL-PUBLIC-CORPUS-001` | Public corpus suite with exact fact-citation grading | `PUBLIC-POLICY-001`, `EVIDENCE-REPIN-001` | Pending | DEC-005 |

### BOUNDED_AUTONOMY

| ID | Task | Depends on | Status | Open decisions |
|---|---|---|---|---|
| `AUTONOMY-001` | Capability-specific bounded automation canary | `CHANNEL-001` | Pending | DEC-001, DEC-002, DEC-003, DEC-004 |

## Reading this board

Three of the blocked items — `AGENT-001`, `OPENCLAW-REPACK-001` and `RUNTIME-SECURITY-001` — are
frozen by [ADR-0004](adr/0004-runtime-consolidation-and-frozen-openclaw-evidence.md) as immutable
blocked history. They are not resumed, rewritten or deleted. G1 agent evidence is carried by
`AGENT-002`.

The rest are blocked on named external actions. `EVIDENCE-REPIN-001` was the cheapest and was
completed 2026-08-13; the six items it held no longer wait on the pin, though each keeps its own
remaining blocker. The cheapest left is `DEC-008`, which unblocks retention and costs minutes.
Everything else is calendar-bound.

Every actionable item has a task packet under `context/tasks/` except the frozen `AGENT-001`, and
must attach its declared checks, rollback impact and unresolved assumptions before completion.
Unknown policy remains fail-closed; public capability flags remain disabled without a valid signed
manifest.
