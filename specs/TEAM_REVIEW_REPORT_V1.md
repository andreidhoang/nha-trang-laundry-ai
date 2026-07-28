# Engineering Team Review Report v1

**Review date:** 2026-07-27  
**System:** Giặt Là Sạch Cộng internal operations and constrained customer-service agent  
**Review status:** `SPEC_APPROVED_WITH_EXECUTION_GATES`  
**Decision owner:** CÔNG TY TNHH A & T CARE

## 1. Executive verdict

The specification pack is approved as an implementation baseline for:

- repository and CI bootstrap;
- deterministic domain/data layer;
- catalog, pricing, promotion, delivery and SLA engines;
- internal staff PWA;
- approval queue, audit and transactional outbox;
- Shadow evaluation harness.

It is **not** an authorization to connect a public customer channel, send autonomously, promise
capacity, finalize uncertain prices, issue remedies or enable bounded autonomy.

The architecture does not need a rewrite. The review changed several security and domain contracts
from advisory prose into fail-closed, testable invariants.

## 2. Review disciplines

The pack received independent passes from:

1. architecture, platform security and reliability;
2. domain modeling, pricing, finance and laundry operations;
3. agent runtime, tool safety, evaluation and autonomy rollout;
4. final cross-spec consistency and machine-readable contract review.

Each pass used P0/P1 severity:

- **P0:** could cause wrong money, unauthorized action, privacy exposure, lost operational truth or
  an unsafe launch;
- **P1:** materially affects reliability, operability, maintainability or credibility of the plan.

## 3. P0 resolution matrix

| Area | Review finding | Resolution in v1 | Execution evidence still required |
|---|---|---|---|
| Public isolation | Same-host wording could expose the owner's OpenClaw environment | Separate public VM/VPS is mandatory before public/untrusted inbound | Host/network/credential isolation test |
| Channel authority | Public agent could appear able to send directly | Public OpenClaw has no channel credentials; only outbox worker sends | Image/config scan and egress test |
| Inbound durability | Agent could process before event persistence | Adapter → durable inbox → authenticated runner contract | Crash/replay integration test |
| Authorization | Tool/PDP boundary was drawn inside public cell | Tool Facade + PDP moved to private control plane | Network deny tests |
| Commercial calculation | LLM could be treated as calculator | All price/promo/delivery/SLA results are deterministic domain code | Golden vectors and migration parity |
| Standard wash model | 44 source rows were conflated with canonical records | 44 source rows → 43 services/rules; one aggregate wash rule with two tiers | Migration test and snapshot hash |
| Estimate vs final | Customer estimate could become a final charge | Separate immutable estimate and exact-final revisions/acceptance timestamps | State-machine integration test |
| Promotion uncertainty | Unknown eligibility could look final | Provisional response with null eligibility and mandatory approval | Boundary and unpublished-policy tests |
| Human approval | Model could influence reason/role/TTL | Server derives policy reasons, role, TTL, obligations and capability | Tamper/expiry tests |
| Post-approval edit | Approved content could be changed before send | Exact revision and JCS/SHA-256 rendered hash bound to execution | Hash mismatch and concurrent-edit tests |
| Manual Shadow send | Copy/send could bypass audit and exactly-once claims | Manual-send attestation protocol and distinct state | Attestation UI and double-execution test |
| Audit integrity | Mutation could survive an audit failure | Mutation + domain event + audit + required outbox are atomic | Failure-injection test |
| Consent/STOP | Model-mediated opt-out could race a queued send | STOP is deterministic at ingress; ambiguous opt-out blocks marketing | Concurrency test |
| Public status IDOR | Public code could be treated as authentication | ≥80-bit reference plus server-derived ownership, scoped identity and rate limits | Enumeration/cross-contact tests |
| Kill switch | Flag precedence and failure behavior were undefined | Explicit all-AND formula; missing/stale state disables automation | In-flight run/outbox drill |
| Backup RPO | Daily backup could not satisfy 15-minute RPO | PITR/continuous WAL + off-host base backup from first real order | Restore point ≤15 minutes demonstrated |
| MFA | Roadmap treated MFA as optional preparation | Owner MFA before real Shadow; all PII/approval/export roles before public | Recovery/revocation/step-up tests |
| Pilot evidence | Quote count could substitute for operating evidence | 30 real orders, 10 batch logs and 20 delivery logs | Completed evidence set |
| Bounded gate | Sample/time/security inheritance were weak | ≥30 days and ≥100 eligible cases per capability, zero critical errors | Signed capability release manifest |

## 4. Machine-readable review artifacts

The following are normative implementation inputs:

- [`contracts/canonical-enums-v1.json`](contracts/canonical-enums-v1.json): enums, normalization and
  canonical hash rules;
- [`contracts/agent-tools-v1.openapi.yaml`](contracts/agent-tools-v1.openapi.yaml): the only public
  agent tool/API registry;
- [`contracts/release-gate-manifest-v1.schema.json`](contracts/release-gate-manifest-v1.schema.json):
  signed, capability-specific launch evidence contract;
- [`evals/eval-manifest-v1.yaml`](evals/eval-manifest-v1.yaml): P0, deterministic, language and
  adversarial gates.

CI must reject:

- an enum in database/code/prompt/eval that is absent from the enum registry;
- an agent operation or argument absent from OpenAPI;
- a public tool accepting customer/contact/address/distance evidence chosen by the model;
- a release whose manifest does not identify config, prompt, policy, model and eval versions.

## 5. Go / no-go decision

### GO now — implementation work

- M0 repository/CI/ADR foundation;
- M1 database and immutable configuration publication;
- M2 deterministic engines and pilot instrumentation;
- M3 internal PWA and approval workflow;
- local/synthetic Shadow evaluation.

### CONDITIONAL GO — real-customer Shadow

All of the following must exist first:

- owner MFA;
- atomic audit/outbox behavior and failure-injection pass;
- PITR/WAL off-host backup and restore proof;
- staff accounts/RBAC, session revocation and sanitized exports;
- manual-send attestation or system-mediated approved outbox;
- pilot instrumentation for orders, batches, staff time and delivery cost;
- privacy/retention baseline and incident owner;
- no public agent/channel inbound.

### NO-GO — public Assisted mode

Until:

- official provider channel is selected and approved; Zalo Personal remains prohibited;
- separate hardened public host is deployed;
- public OpenClaw has no channel credential, owner mount, mutation tool or control-plane exposure;
- MFA is enforced for all roles with PII/approval/export/policy/finance/address access;
- model-provider data retention/training terms are approved;
- public customer-policy corpus is published and tested;
- G1 and G2 evidence is complete with zero wrong confirmed/sent monetary value.

### NO-GO — Bounded autonomy

Until the specific capability has:

- a published measurement, promotion, calendar/cutoff and customer-policy dependency;
- at least 100 eligible production cases over at least 30 days;
- zero critical safety, privacy or monetary errors;
- measured cost/margin and capacity completeness;
- successful kill-switch, incident, restore, canary and rollback drills;
- a signed release manifest and monitored correction workflow.

## 6. Owner decisions intentionally kept fail-closed

These do not block internal implementation, but they block the affected automated capability:

- exact weight precision and rounding policy;
- definitive promotion eligibility event across all channels;
- whether displayed prices include tax and the invoice workflow;
- one-leg delivery pricing and every route over 6km;
- holiday/cutoff rules beyond current confirmed closures;
- rewash, loss/damage, compensation and credit rules;
- B2B contract pricing, credit limits and receivable terms;
- publication of any contribution/margin model;
- customer-facing retention and privacy wording.

The system response to an unresolved decision is `REQUIRE_HUMAN` or `DENY`, never inference.

## 7. Review sign-off criteria

This report becomes `IMPLEMENTATION_APPROVED` when:

1. JSON and YAML contracts parse and cross-reference successfully;
2. exact migration counts and golden price vectors pass;
3. architecture trust paths match network deployment;
4. P0 eval cases are executable and pass at 100%;
5. the implementation pull request includes a threat-model delta and rollback plan.

Operational launch approval is separate and requires the evidence gates above.
