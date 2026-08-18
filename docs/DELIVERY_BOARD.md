# Delivery board

Generated from `delivery/WORK_QUEUE.yaml` on **2026-08-18**. It goes stale the moment the queue
moves; when the two disagree, the queue is right. Run `uv run python scripts/run_delivery_loop.py`
for the authoritative next item and `uv run python scripts/report_delivery_status.py` for
machine-readable capability status.

[`delivery/WORK_QUEUE.yaml`](../delivery/WORK_QUEUE.yaml) is the execution source,
[`delivery/PROGRAM_PLAN.yaml`](../delivery/PROGRAM_PLAN.yaml) defines stable phases, and
[`delivery/CAPABILITY_STATUS.yaml`](../delivery/CAPABILITY_STATUS.yaml) is the production-
authorization source. A code status never authorizes a release.

**77 items: 42 complete, 15 blocked, 20 pending.**
Every capability reads `NOT_AUTHORIZED`. Resolving a decision removes one fail-closed reason for the
capabilities it names — it authorizes nothing by itself; G1–G4 evidence still governs authorization.

~~**One pending item is now buildable: `RETENTION-001`.**~~ **Superseded 2026-08-18 — it was built.**
`DEC-008` resolving was its sole blocker; the mechanism had been written on 2026-08-13 and parked with
draft evidence. `RETENTION-001` is now `COMPLETE`, and completing it unblocked
`EVAL-SYNTHETIC-COMBINATORIAL-001` in the same session (see below). Every other pending item still traces to an owner decision or an external party;
see [`PATH_TO_PRODUCTION_REVIEW.md`](PATH_TO_PRODUCTION_REVIEW.md) §5 and §6a.
Since 2026-08-14 the queue recorded `TEST-ISOLATION-001`, `DEMO-STACK-001`, `QUOTE-COMMAND-001`,
`STORE-SCOPING-002`, `STORE-ASSIGNMENT-001`, `SETTLEMENT-001` and `TOOL-BACKEND-001` complete, and
on 2026-08-16 the owner-directed internal assistant was registered after the fact as
`ASSISTANT-001` (deterministic brain, no model call, append-only transcript; no capability
authorized). On 2026-08-18 nine open decisions resolved in one session — `DEC-001`, `DEC-002`,
`DEC-003`, `DEC-004`, `DEC-005`, `DEC-009`, `DEC-010`, `DEC-011`, `DEC-012` — and the same session's
console rebuild opened two new ones, `DEC-013` (walk-in customer identification) and `DEC-014`
(which staff roles see the day's takings), and a later client-acquisition session opened three more —
`DEC-015` (the customer record; the CRM decision previously recommended as `DEC-011`), `DEC-016`
(inbound channel custody and staffing) and `DEC-017` (market identity and the franchise
relationship). **17 registered, 6 `OPEN` at that point; 20 registered, 9 `OPEN` now** — `DEC-018`–`DEC-020` were opened by the retention-execution work later the same day. See "Reading this board" below. No queue item's *status*
moved as a result — only the "Open decisions" column changed, plus `RETENTION-001` becoming
selectable — because only the controller flips a status, and it has run once since (selecting
`RETENTION-001`), not enqueued anything new.

`MODEL-ROUTE-001` and `MULTIMODAL-PERCEPTION-001` entered `Blocked` by construction: they specify
the tiered-inference and perception direction assessed in
[`TIERED_INFERENCE_AND_MULTIMODAL_ASSESSMENT.md`](TIERED_INFERENCE_AND_MULTIMODAL_ASSESSMENT.md)
and proposed in [ADR-0008](adr/0008-inference-topology-and-multimodal-scope.md). `EVIDENCE-REPIN-001`
stood in front of both and was completed 2026-08-13, so what remained for them was decisions:
ADR-0008 acceptance (still unanswered — see
[`docs/DECISION_REQUEST_TIERED_INFERENCE_2026-08.md`](DECISION_REQUEST_TIERED_INFERENCE_2026-08.md)
Decision A) and `DEC-009`, resolved 2026-08-18 (staff-supplied media only). `MULTIMODAL-PERCEPTION-001`
still needs `MODEL-ROUTE-001`, which still needs ADR-0008 accepted, so neither is actually closer to
buildable — DEC-009's resolution removed a decision, not a blocker on this particular path.

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
| `QUOTE-COMMAND-001` | Create-quote command path from staff request to immutable quote revision | `DOMAIN-005`, `OPERATIONS-001`, `STORE-SCOPING-001` | Complete | — |
| `SETTLEMENT-001` | Exact-payment and self-collection attestation so an order can reach COMPLETED | `QUOTE-COMMAND-001` | Complete | — |
| `ASSISTANT-001` | Owner-directed internal assistant with deterministic brain and append-only transcript | `OPERATIONS-001`, `STORE-SCOPING-001` | Complete | — |

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
| `EVAL-SYNTHETIC-COMBINATORIAL-001` | Synthetic combinatorial suite generated from the deterministic domain | `DOMAIN-005`, `RESPONSES-RUNTIME-001`, `EVIDENCE-REPIN-001` | **Complete 2026-08-18** | — (was `Blocked` by a condition its own text said had lifted; unblocked on `EVIDENCE-REPIN-001` being complete. Corpus **0 → 669 of 1,300**; no release blocker removed) |
| `CORPUS-CONSENT-001` | Consent basis and reviewed anonymization of real customer message history | — | Blocked | — |
| `AGENT-PIPELINE-001` | Assemble the constrained runtime into a running worker pipeline | `RESPONSES-RUNTIME-001` | Complete | — |
| `MODEL-PIN-001` | Immutable model release pin and registry artifact verification | `PROVIDER-TRANSPORT-001`, `EVIDENCE-REPIN-001` | Pending | — |
| `MODEL-ROUTE-001` | Per-role model pinning so a route is as pinned as its loosest member | `EVIDENCE-REPIN-001`, `PROVIDER-TRANSPORT-001`, `MODEL-PIN-001` | Blocked | — |
| `MULTIMODAL-PERCEPTION-001` | Perception tier that emits observations and never conclusions | `MODEL-ROUTE-001`, `CONSENT-STOP-001` | Blocked | DEC-006 |
| `EVAL-CORPUS-001` | Frozen Vietnamese regression corpus raised toward the manifest minimum | `CORPUS-CONSENT-001`, `EVIDENCE-REPIN-001` | Pending | — |
| `AGENT-002` | Custom-runtime Shadow evidence and P0 provider-backed evaluation | `AGENT-PIPELINE-001`, `EVAL-CORPUS-001`, `MODEL-PIN-001`, `PROVIDER-TRANSPORT-001`, `RUNTIME-FREEZE-001`, `EVAL-SYNTHETIC-COMBINATORIAL-001`, `EVAL-LANGUAGE-CORPUS-001` | Pending | DEC-006 |
| `EVAL-LANGUAGE-CORPUS-001` | Normal-language Vietnamese suite at the manifest distribution | `CORPUS-CONSENT-001`, `EVIDENCE-REPIN-001` | Pending | — |
| `TOOL-BACKEND-001` | Deterministic backend for the ten fixed Tool Facade operations | `QUOTE-COMMAND-001`, `AGENT-PIPELINE-001` | Complete | — |

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
| `TEST-ISOLATION-001` | Isolate database integration tests from each other | `ENV-INTEGRITY-001` | Complete | — |
| `DEMO-STACK-001` | Runnable local demo topology with a real OIDC issuer and synthetic seeds | `STAGING-001`, `CONTAINER-001`, `HTTP-SECURITY-001` | Complete | — |
| `STORE-SCOPING-002` | Enforce store membership on the order transition route the first pass missed | `STORE-SCOPING-001` | Complete | — |
| `STORE-ASSIGNMENT-001` | A governed, audited write path for staff store membership | `IDENTITY-001`, `STORE-SCOPING-001` | Complete | — |
| `DEPLOY-TARGET-001` | Production topology, isolated agent cell host, and closed capability flags | `DECISION-HOSTING-001` | Pending | — |
| `MONITORING-001` | Telemetry collector, retention, and paging alert contracts | `DEPLOY-TARGET-001` | Pending | — |
| `SIGNER-REGISTRY-001` | Two-party release schema, verifier enforcement, and signer key ceremony | `EVIDENCE-REPIN-001` | Blocked | — |
| `RETENTION-001` | Customer data retention, redaction, and deletion jobs | `DB-001`, `OBSERVABILITY-001` | **Complete 2026-08-18** | — (`DEC-008` resolved the same day; the mechanism was already built and parked on 2026-08-13) |
| `OPS-RUNBOOK-001` | The five G1 runbooks, each executed once by its operator | `DEPLOY-TARGET-001` | Pending | — |
| `RETENTION-STORE-001` | Separate disposable payload from the append-only ledger | `RETENTION-001` | **Blocked 2026-08-18** | `DEC-018`, `DEC-020` — designed and adversarially reviewed, not implemented. The ledger trigger is *not* the obstacle (DROP COLUMN was probed live and succeeds); `DEC-008` collides with itself on payload-that-is-also-consent-evidence, the assistant answer is stored twice in a permanently immutable record, and no database identity holds `DELETE` |
| `SLO-VERIFY-001` | Measured verification of the declared Shadow-stage SLOs | `DEPLOY-TARGET-001`, `MONITORING-001` | Pending | — |

### REAL_SHADOW_READINESS

| ID | Task | Depends on | Status | Open decisions |
|---|---|---|---|---|
| `BACKUP-RESTORE-001` | Continuous PostgreSQL recovery and restore-drill automation | `DB-001`, `DEPLOY-TARGET-001`, `STAGING-001` | Blocked | — |
| `SECURITY-001` | Identity, privacy, PITR, incident, kill-switch, and release-readiness gates | `AGENT-002`, `OBSERVABILITY-001`, `POLICY-001`, `SUPPLYCHAIN-001`, `HTTP-SECURITY-001`, `TELEMETRY-001`, `STAGING-001`, `BACKUP-RESTORE-001`, `DEPLOY-TARGET-001`, `MONITORING-001` | Pending | DEC-006 |
| `SHADOW-001` | Internal real-order Shadow pilot and G1 evidence | `SECURITY-001`, `SHADOW-CONSOLE-001`, `SHOP-INSTRUMENT-001`, `SIGNER-REGISTRY-001`, `OPS-RUNBOOK-001`, `SLO-VERIFY-001`, `RETENTION-001`, `STORE-SCOPING-001`, `RETENTION-STORE-001` | Pending | — |
| `SHOP-INSTRUMENT-001` | Physical shop instrumentation: cycle, capacity, and delivery cost baselines | — | Blocked | — |
| `DECISION-BUSINESS-001` | Owner decision session closing DEC-001 through DEC-004 | — | Blocked | — (DEC-001–004 resolved 2026-08-18 in `docs/DECISION_REQUEST_PRICING_POLICY_2026-08.md`; item status is the controller's to move, not this board's — its substance is done) |

### PUBLIC_ASSISTED

| ID | Task | Depends on | Status | Open decisions |
|---|---|---|---|---|
| `CHANNEL-001` | Official channel and isolated public-cell Assisted entry | `CHANNEL-ZALO-001`, `CONSENT-STOP-001`, `PUBLIC-POLICY-001`, `SHADOW-001`, `EVAL-PUBLIC-CORPUS-001` | Pending | DEC-006 |
| `CHANNEL-ZALO-APPLY-001` | Official Zalo OA registration and business verification | — | Blocked | — |
| `CHANNEL-ZALO-001` | Official Zalo OA adapter with verified provider behavior | `CHANNEL-ENVELOPE-001`, `CHANNEL-ZALO-APPLY-001`, `CONSENT-STOP-001` | Pending | — (DEC-005 resolved 2026-08-18: Telegram for Shadow, Zalo OA for production; still waits on `CHANNEL-ZALO-APPLY-001`'s external verification) |
| `PUBLIC-POLICY-001` | Published PUBLIC_CUSTOMER bundle and tested correction workflow | `DECISION-BUSINESS-001` | Pending | — |
| `EVAL-PUBLIC-CORPUS-001` | Public corpus suite with exact fact-citation grading | `PUBLIC-POLICY-001`, `EVIDENCE-REPIN-001` | Pending | — (DEC-005 resolved) |

### BOUNDED_AUTONOMY

| ID | Task | Depends on | Status | Open decisions |
|---|---|---|---|---|
| `AUTONOMY-001` | Capability-specific bounded automation canary | `CHANNEL-001` | Pending | — (DEC-001–004 all resolved 2026-08-18; this item's distance is now the G1–G4 gate ladder and `CHANNEL-001`, not a decision) |

## Reading this board

Three of the blocked items — `AGENT-001`, `OPENCLAW-REPACK-001` and `RUNTIME-SECURITY-001` — are
frozen by [ADR-0004](adr/0004-runtime-consolidation-and-frozen-openclaw-evidence.md) as immutable
blocked history. They are not resumed, rewritten or deleted. G1 agent evidence is carried by
`AGENT-002`.

The rest are blocked on named external actions. `EVIDENCE-REPIN-001` was the cheapest and was
completed 2026-08-13; the six items it held no longer wait on the pin, though each keeps its own
remaining blocker. `DEC-008` was the next-cheapest and resolved 2026-08-18; `RETENTION-001` was built the
same day and `EVAL-SYNTHETIC-COMBINATORIAL-001` followed it. The cheapest decision left on the board is `DEC-006` — it is the sole remaining
decision blocker on `RUNTIME-PARITY-001`, `AGENT-002`, `SECURITY-001`, and (alongside external Zalo
verification) `CHANNEL-001`; a stance toward resolving it (`PROCEED_TOWARD_VERIFICATION`) is recorded
in [`docs/DECISION_REQUEST_PROVIDER_DATA_2026-08.md`](DECISION_REQUEST_PROVIDER_DATA_2026-08.md),
but the registry entry stays `OPEN` until a real OpenAI account setting is verified and a legal check
on cross-border customer PII lands. Everything else on this board is calendar-bound.

A client-acquisition session on 2026-08-18 opened `DEC-015`, `DEC-016` and `DEC-017`
(`docs/DECISION_REQUEST_ACQUISITION_2026-08.md`). None is enqueued as work: `ACQUISITION-001` exists
as a packet at `context/tasks/TASK-acquisition-001.md` but **was deliberately not added to the
queue**, because enqueueing is a scheduling act and its first slice is gated on `DEC-013`.

Two decisions opened 2026-08-18 by the console rebuild, `DEC-013` (how a walk-in customer with no
prior channel message is identified at the counter) and `DEC-014` (which staff roles may see the
day's takings), currently name no queue item as blocked on them — they surfaced from running code,
not from a specification gap, and nothing in this board changes until a future item is enqueued
against them. See
[`docs/DECISION_REQUEST_WALKIN_IDENTITY_2026-08.md`](DECISION_REQUEST_WALKIN_IDENTITY_2026-08.md).
Separately, `DEC-HOSTING` (the hosting-provider choice behind `DECISION-HOSTING-001`) has never been
a registry entry — ADR-0007 topology plus admissibility framework is drafted in
[`docs/DECISION_REQUEST_HOSTING_2026-08.md`](DECISION_REQUEST_HOSTING_2026-08.md), but no candidate
is selectable by an agent at all, and every cost/residency figure in that framework is explicitly
unverified pending a real account.

Every actionable item has a task packet under `context/tasks/` except the frozen `AGENT-001`, and
must attach its declared checks, rollback impact and unresolved assumptions before completion.
Unknown policy remains fail-closed; public capability flags remain disabled without a valid signed
manifest.
