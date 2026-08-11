# Platform, Tenancy, Vertical Packs, and Integrations

**Status:** Strategic product-platform design; not an implemented-capability claim  
**Proposed owners:** Platform Engineering and Product

## Platform thesis

The company scales through a reusable verified-operations kernel plus versioned vertical packs. It does not scale by giving a generic agent more tools, and it does not scale by cloning a codebase for each customer.

```text
Customer configuration
        ↓
Vertical pack: ontology extension + policy + workflow + UX + evals
        ↓
Verified operations kernel: identity + case + state + approval + event + audit + outbox
        ↓
Controlled connectors: channels + systems of record + documents + payments/e-invoice
        ↓
Infrastructure trust cell: compute + database + keys + telemetry + deployment controls
```

## Shared kernel

The kernel owns behavior that must be consistent across verticals:

- Tenant, actor, identity binding, role, and capability.
- Durable inbox/work item and idempotency.
- Case/conversation/evidence primitives.
- Policy decision and durable approval.
- Domain command/event/audit/outbox semantics.
- Consent, suppression, purpose, and official-channel controls.
- Context manifest and provenance.
- Agent-runtime/tool boundary and budgets.
- Evaluation/trace envelope and release-gate linkage.
- Configuration publication, compatibility, rollback, and evidence.

Kernel changes require backward-compatibility and cross-pack regression review.

## Vertical pack contract

A pack is a signed, versioned product artifact containing:

- Pack identity, version, supported kernel range, owner, and lifecycle state.
- Vocabulary and ontology extensions with migration rules.
- Workflow/state-machine definitions and allowed transitions.
- Typed capability/tool declarations mapped to kernel controls.
- Business policy schema and required owner-published configuration.
- Templates and customer/operator UX components.
- Connector mappings and required external capabilities.
- Context sources, retrieval rules, and data classifications.
- Evaluation suite, adversarial cases, and release thresholds.
- Observability metrics and outcome definitions.
- Data retention, privacy, legal, and risk classification.
- Rollback/migration procedure and known limitations.

A pack cannot loosen kernel invariants. An unsupported or missing policy is `REQUIRE_HUMAN` or `NOT_SUPPORTED`.

## Laundry reference pack

Laundry remains the first reference pack because it exercises reusable operational primitives:

- Customer/channel identity and consent.
- Service catalog and immutable price snapshot.
- Intake, item/weight evidence, quote/order lifecycle.
- Pickup/delivery and SLA policy.
- Payment evidence and reconciliation.
- Rewash, loss, damage, credit, and exception approval.
- Operator/customer message templates and outbox delivery.

Open decisions on weight precision, promotion evidence, distance policy, and exception compensation remain in `context/DECISION_REGISTRY.yaml`. The platform strategy cannot invent them.

## Pack maturity levels

| Level | Evidence | Allowed use |
| --- | --- | --- |
| Experimental | Synthetic/local cases, incomplete policy | Research only |
| Shadow | Representative evals and eligible real inputs, no effects | Internal comparison |
| Lighthouse | Signed customer scope, dedicated trust cell, human approval | Bounded paid pilot |
| General availability | Repeatable outcomes, support, legal/security, rollback, full gate evidence | Approved segment/workflows |
| Maintained | Ongoing adoption, regression, provider, connector, and policy review | Continued sale |
| Retired | Migration/export and effect shutdown complete | No new use |

Maturity is per pack, workflow, channel, deployment, and autonomy class—not a global product label.

## Tenant model

### Stage 1 — Dedicated single-tenant trust cell

Use separate tenant deployment/data/key/connector scopes for lighthouse and early enterprise customers. This lowers isolation ambiguity, simplifies contractual controls, and limits blast radius while schemas and operational patterns mature.

### Stage 2 — Standardized dedicated cells

Automate provisioning, policy publication, connector certification, backups, monitoring, upgrade, and evidence export. The same release manifest must reproduce each cell.

### Stage 3 — Shared services with isolated data planes

Share selected stateless control services only after tenant propagation, caching, queues, telemetry, quotas, and failure modes have automated isolation tests.

### Stage 4 — Shared multi-tenant SaaS

Consider only when demand and unit economics require it and the company has demonstrated:

- Formal tenant boundary and threat model.
- Cross-tenant negative/property tests at every storage and execution boundary.
- Tenant-scoped encryption, indexing, caching, queues, rate limits, billing, and telemetry.
- Noisy-neighbor and denial-of-service controls.
- Tenant-specific export, deletion, restore, incident containment, and rollback.
- Independent security review and launch authority.

Shared SaaS is not assumed to be the cheapest early path once assurance cost is included.

## Configuration over customization

Customer differences should be represented as validated, owner-published configuration:

- Roles and permissions.
- Catalog and policy values.
- Workflow enablement and approval thresholds.
- Official channel and connector mappings.
- Templates and branding within controlled variables.
- Retention and notification schedules.
- Evaluation eligibility and deployment flags.

Configuration is schema-validated, versioned, effective-dated, audited, promoted by environment, and rollbackable. Arbitrary customer code or prompt overrides are not accepted into the runtime path.

A customization that appears for three customers should be evaluated for a kernel/pack abstraction. A one-off that weakens invariants should be rejected.

## Connector architecture

Connectors are controlled adapters around an internal canonical contract. Each connector defines:

- Official support status and provider terms.
- Authentication and tenant/account binding.
- Granted scopes and data classifications.
- Inbound verification, ordering, duplication, and replay behavior.
- Outbound idempotency, rate limits, receipts, and reconciliation.
- Error taxonomy, retry/backoff, circuit breaking, and expiry.
- Sandbox/test environment and contract tests.
- Version/deprecation monitoring.
- Data retention, deletion, region, and subprocessor behavior.
- Disable/rotation/runbook and owner.

Use official APIs and approved integrations. Screen scraping, personal-account automation, browser sessions, or credential sharing are not production connector strategies.

## Channel strategy

Zalo is a major Vietnam distribution surface and provides an official OA/OpenAPI path. The platform must nevertheless remain channel-neutral through the canonical envelope and outbox contracts.

Each outbound message requires deterministic consent/suppression/quiet-hour/template/destination policy. The public runtime never receives raw channel credentials and never sends directly. Channel pricing, quotas, and policies are versioned external dependencies with operational alerts.

## Systems-of-record strategy

Integrate rather than duplicate authoritative accounting, POS, e-invoice, payment, and ERP state. For each mapped field, declare:

- System of record and conflict winner.
- Direction and frequency of synchronization.
- Identifier and version mapping.
- Create/update/delete authority.
- Reconciliation and mismatch workflow.
- Historical snapshot requirement.

If two systems both appear authoritative, the integration is not ready for autonomous effects.

## Enterprise control-plane capabilities

Enterprise readiness eventually requires:

- SSO/federation, SCIM or controlled provisioning, and role review.
- Dedicated deployment/key/network options.
- Policy and capability administration with separation of duties.
- Connector catalog, approval, and secret rotation.
- Data classification, retention, export, deletion, and legal hold.
- Version and release visibility with maintenance windows.
- Audit/trace export with redaction.
- Tenant-specific SLO, incident, backup, and recovery controls.
- Model/provider eligibility and routing policy.
- Pack lifecycle and evaluation reports.

Each capability must be listed as unavailable until implemented and evidenced.

## Compatibility and lifecycle

Use semantic versions for kernel, pack, connector, schemas, tool registry, prompts, and model configurations. A release manifest pins the compatible set.

- Backward-compatible schema additions require consumers that tolerate unknown optional fields.
- Breaking changes require migration, dual-read/dual-write only when explicitly controlled, and rollback analysis.
- Policy and price/catalog changes are effective-dated; history remains explainable.
- A pack cannot auto-upgrade across a capability or risk-class boundary.
- Retired versions have a customer migration and evidence-retention plan.

## Productization metrics

The platform abstraction is succeeding when:

- Time to deploy the nth customer declines.
- At least 80% of deployment work is reusable configuration, pack content, certified adapters, and eval cases rather than kernel forks. This is a proposed target, not current evidence.
- Kernel changes per customer decline while shared regression coverage grows.
- Pack upgrades are repeatable and rollbackable.
- Support incidents are attributable to known layers.
- A second vertical reaches lighthouse use without weakening core invariants.

## Expansion rule

Do not add a connector, tool, vertical, tenant mode, or protocol because a platform checklist suggests it. Add it when a paid workflow requires it, authority is explicit, the security/evaluation burden is funded, and the capability can be operated throughout its lifecycle.
