# Reliability, SRE, and Delivery

**Status:** Strategic operating model; normative SLOs and release gates control  
**Proposed owners:** Engineering and SRE

## Reliability objective

The system must prefer a visible, recoverable delay or human handoff over an untraceable or incorrect material effect. Reliability includes business correctness, authorization, and reconciliation—not merely uptime.

## Reliability invariants

- Inbound work is durably persisted before processing acknowledgement.
- Material mutation, domain event, audit, and outbox insertion are atomic.
- External effects occur only through the sole sender after commit.
- Retries are idempotent and bounded.
- Unknown external outcomes enter reconciliation, not blind retry.
- Human approvals are durable, exact, expiring, and version-bound.
- Published configuration/model/prompt/runtime/tool versions are attributable and rollbackable.
- A public capability is unavailable unless its signed capability/release gate is valid.

## Service-level model

Separate objectives by user journey:

- Inbound acceptance durability.
- Time to first operator-visible state.
- Time to deterministic response for direct paths.
- Time to proposal for shallow agent paths.
- Approval-queue delivery and expiry behavior.
- Outbox delivery and reconciliation.
- Authoritative query availability.
- Audit/evidence availability.

Also define correctness SLOs:

- Duplicate material effect rate.
- Unauthorized effect count.
- Cross-tenant exposure count.
- Stale-policy/state commit count.
- Unreconciled external-effect age.
- Material correction and rollback rate.

Initial numeric objectives must be derived from customer workflow and operational evidence, then approved in normative policy. They are not invented here or by a model.

## Durable processing pattern

Each stage records enough state to resume safely:

1. Inbox persists raw authenticated envelope and deduplication key.
2. Work queue references the immutable envelope and current attempt.
3. Runtime checkpoints terminal or externally meaningful transitions.
4. Domain transaction commits state/event/audit/outbox together.
5. Sender records request attempt and provider receipt/result.
6. Reconciler resolves unknown outcomes and stale work.
7. Dead-letter/quarantine preserves evidence and exposes an operator action.

Queues may deliver at least once. Consumers use stable idempotency keys tied to business intent, not random retry attempts.

## Failure classification

| Class | Example | Default action |
| --- | --- | --- |
| Transient known-safe | Provider 503 before accepted request | Bounded retry with jitter |
| Persistent known-safe | Invalid connector configuration | Stop, alert, require fix |
| Unknown effect | Timeout after provider accepted request | Reconcile before retry |
| Deterministic business rejection | Invalid transition or denied permission | No retry; explain/escalate |
| Stale state/policy | Version changed during wait | Reassemble context/reapprove |
| Budget exhaustion | Loop/tool/token/time ceiling | Safe terminal handoff |
| Security signal | Injection/exfiltration/cross-tenant attempt | Contain, preserve evidence, investigate |

The model does not classify an unknown material effect as safe to retry.

## Observability

Use structured logs, metrics, traces, audits, and domain events for different purposes:

- **Logs:** diagnostic events, redacted and access-controlled.
- **Metrics:** aggregate health, SLO, cost, and outcome signals.
- **Traces:** causal path across runtime, tool, policy, domain, and connector.
- **Audit:** immutable who/what/authority/result evidence.
- **Domain events:** business facts for workflows and projections.

Do not use logs as the authoritative audit trail or expose raw prompts/responses by default.

Every alert links to an owner, severity, customer impact, first safe action, dashboard/query, and runbook. Alert on symptoms and safety indicators; avoid paging on unactionable model variance.

## Runbook minimum

Each production capability has runbooks for:

- Disable workflow/capability/tenant/provider/channel.
- Drain or quarantine inbox/outbox work.
- Reconcile unknown effects.
- Rotate credentials and keys.
- Roll back model/prompt/configuration/pack/connector release.
- Restore database and validate business invariants.
- Export audit evidence and notify customers.
- Handle provider outage, rate limit, or policy change.
- Correct material state through an audited domain workflow.

Runbooks are exercised; a document alone is not readiness evidence.

## Backup, restore, and disaster recovery

- Define recovery point and recovery time objectives by data/service class.
- Back up authoritative state, configuration, signing metadata, and required evidence.
- Protect backups from the same identity/blast radius as production.
- Test restore into an isolated environment and run integrity/reconciliation checks.
- Document treatment of queued work, external receipts, expired approvals, and idempotency after recovery.
- Prove tenant export/deletion and legal-hold behavior against backup policy.

No RPO/RTO promise is published before a timed restore exercise demonstrates it.

## Incident management

An incident has one commander, one evidence timeline, clear containment authority, customer/legal/security roles, and explicit exit criteria. For AI-related incidents, capture active versions and relevant redacted traces before rollback where safe.

Sequence:

1. Detect and assign severity.
2. Contain capability and protect users/data.
3. Reconcile actual business effects.
4. Communicate verified facts and uncertainty.
5. Correct state through approved domain paths.
6. Determine earliest controllable cause.
7. Add tests/evals and improve the correct layer.
8. Review systemic actions, owner, and due date.

Do not publish speculative model explanations or chain-of-thought as root cause.

## Release engineering

A releasable unit contains:

- Immutable source revision and artifact checksums.
- Dependency/container inventory and verification results.
- Database/schema migration with forward and rollback/compatibility plan.
- Kernel, pack, connector, tool, prompt, model, policy, and configuration versions.
- Contract, security, evaluation, and operational evidence.
- Eligible tenant/workflow/channel/autonomy scope.
- Approvers, timestamps, expiry/review triggers, and rollback target.

Deploy and capability release are separate. Code can be deployed dark while a capability remains unauthorized.

## Progressive delivery

Use local/test → isolated evaluation → shadow → internal → lighthouse approval-led → canary → approved broader scope. Promotion is automatic only for pre-authorized low-risk gates whose evidence is machine-verifiable; all other transitions require the designated signer.

Rollback must revoke the behavioral configuration, not merely the application binary. Stop/kill-switch paths are tested independently from the agent runtime.

## Capacity and architecture triggers

Adopt additional infrastructure only against measured triggers:

- Durable workflow engine: approval/timer/fan-out recovery complexity creates repeat incidents or excessive custom state handling.
- Separate service: scaling, isolation, ownership, or failure domain cannot be met in the modular deployment.
- Kubernetes: deployment count, scheduling/isolation, autoscaling, or customer environment requirements justify operational cost.
- Self-hosted/GPU inference: data sovereignty, latency, availability, or unit economics beat approved managed providers after total-cost analysis.
- Dedicated event platform: database outbox/consumer throughput or retention no longer meets measured requirements.

## Delivery governance

The machine-readable `delivery/WORK_QUEUE.yaml`, `delivery/LOOP_STATE.yaml`, evidence records, and signed manifests are the implementation truth. Roadmaps and status prose do not complete work.

A delivery item is complete only when its declared contracts and checks pass and its required evidence is recorded. Skipped integrations, unavailable external services, or unresolved policy are blockers or explicit limitations—not green results.

## Operational readiness review

Before any customer-facing capability, verify:

- Ownership and on-call/support coverage.
- Capacity and dependency limits.
- SLOs and dashboards.
- Failure/retry/reconciliation behavior.
- Backup/restore and rollback exercise.
- Security/privacy/legal readiness.
- Customer communication and support runbooks.
- Evaluation and signed capability gate.
- Known limitations and human fallback.

The current repository prohibition on public automation remains in force until its normative signed gate authorizes it.
