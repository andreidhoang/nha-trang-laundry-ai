# Security, Privacy, and AI Governance

**Status:** Strategic control framework; not legal advice or a certification claim
**Proposed owners:** Security, Privacy/Legal, and Executive Risk Owner

## Governance objective

Make every material AI-assisted action attributable, bounded, reversible where possible, and governed by the same or stronger controls as a human/software action with comparable impact.

AI fluency does not create authority. The control objective is to prevent a probabilistic or compromised component from gaining a path to data or effects beyond the exact workflow grant.

## Threat model

Assume:

- Customer messages, documents, web content, retrieved text, and tool output may contain adversarial instructions.
- Models can hallucinate, over-generalize, disclose context, call the wrong tool, or behave inconsistently.
- Credentials, connectors, plugins, dependencies, models, providers, operators, and tenants can be compromised.
- A valid user can attempt cross-tenant access or exceed role authority.
- Retries and partial failures can duplicate or orphan external effects.
- Logs, eval datasets, embeddings, caches, and backups can leak sensitive data.
- Configuration drift can change behavior without a code diff.
- Insider mistakes and over-broad support access are realistic risks.

## Control hierarchy

1. Remove unnecessary capability and data.
2. Isolate trust domains and identities.
3. Enforce deterministic authorization at every tool/domain/effect boundary.
4. Require durable approval for material or uncertain effects.
5. Detect through structured audit, telemetry, anomaly rules, and reconciliation.
6. Contain through kill switches, capability revocation, provider/channel isolation, and tenant-specific rollback.
7. Recover through tested backup, replay, correction, notification, and evidence preservation.

Prompt instructions are defense in depth, never the primary enforcement layer.

## Identity and authorization

- Authenticate each channel, service, operator, approver, and connector independently.
- Bind tenant and actor server-side before context assembly or tool invocation.
- Propagate end-user/actor identity and service identity; do not replace both with one global agent identity.
- Use least-privilege, short-lived, audience-bound credentials.
- Authorize resource, action, purpose, workflow state, data class, and materiality.
- Keep approval policy and allowed approvers deterministic and versioned.
- Revalidate authorization after waits, retries, context refreshes, and before commit/effect.
- Deny by default and log reason codes without leaking sensitive policy internals.

Sensitive changes require positive, negative, cross-tenant, confused-deputy, replay, stale-permission, and revoked-credential tests.

## Tool and effect security

- Expose task-specific typed tools only.
- Validate all model output as untrusted input.
- Bind identifiers and destinations from server-side context where possible.
- Separate read, propose, approve, commit, and deliver capabilities.
- Prohibit generic database, shell, browser, arbitrary network, secret, and direct-send capabilities in customer paths.
- Use egress allowlists, DNS/TLS validation, timeout/body limits, and response classification for connectors.
- Create an outbox record inside the domain transaction; only the sole sender holds channel credentials.
- Require idempotency and reconciliation for material effects.

MCP or another protocol standardizes transport, not authorization. Remote server descriptions and returned resources are untrusted.

## Prompt-injection and data-exfiltration controls

- Label provenance and trust level for every context segment.
- Keep system policy and capability grants outside retrieved content.
- Do not let retrieved text alter tools, permissions, tenant, destination, or release state.
- Scan and isolate files; render risky formats in restricted workers.
- Minimize secrets and PII before model calls; use references/tokenization where feasible.
- Detect attempts to obtain hidden prompts, credentials, other tenants, or unauthorized records.
- Apply output validation and destination policy before any disclosure.
- Test indirect injection through documents, integration fields, web pages, images, and tool errors.

## Secrets and cryptography

- Use managed secret storage; never prompts, source control, fixtures, logs, or model memory.
- Separate secrets by environment, tenant/trust cell, provider, channel, and capability.
- Rotate and revoke with tested procedures.
- Encrypt in transit and at rest; use tenant-aware key context where appropriate.
- Record key/secret identifiers and versions, not values, in audit evidence.
- Prevent production credentials from entering development, evaluation, or agent sandboxes.

## Software, model, and integration supply chain

- Pin code dependencies, containers, model/runtime IDs, prompt bundles, tool registry, and deployment configuration.
- Generate dependency and artifact inventories; verify signatures/checksums for release artifacts.
- Scan dependencies, images, secrets, licenses, and known vulnerabilities.
- Review connector scopes, provider subprocessors, data behavior, and update channels.
- Use protected release provenance and separate build from approval.
- Treat a model alias update, hosted-tool change, or provider policy change as supply-chain change.
- Preserve a rollback configuration and rehearse provider/connector disablement.

## Privacy by design

For every workflow, document:

- Purpose and lawful basis/consent analysis.
- Data subjects and data categories.
- Required versus optional fields.
- Sources, recipients, processors/subprocessors, and transfers.
- Retention, deletion, correction, access, and export behavior.
- Automated decision/effect role and human intervention.
- Model/provider training, retention, abuse-monitoring, and regional-processing terms.
- Residual risks and owner acceptance.

Real PII remains disabled for an AI provider path until its terms and technical controls are verified for the intended use. Redaction must be tested; it is not assumed from configuration names.

Raw production PII must not appear in development fixtures, demonstrations, generic evaluation datasets, or agent sandboxes. Where a production-derived case is necessary for a controlled regression, use the approved minimization, de-identification, purpose, access, retention, and review process.

## Vietnam regulatory baseline

As of 2026-08-10, the legal review baseline includes at least:

- Law on Personal Data Protection No. 91/2025/QH15, effective 2026-01-01.
- Law on Data, effective 2025-07-01.
- Law on Artificial Intelligence No. 134/2025/QH15, effective 2026-03-01.
- Decision No. 33/2026/QD-TTg on a high-risk AI system list, effective 2026-08-15.
- The 2025 Cybersecurity Law changes effective 2026-07-01.
- Decree No. 91/2020/ND-CP for anti-spam and advertising communications.
- Decree No. 70/2025/ND-CP for relevant e-invoice obligations.

The official sources are linked in [14 — Research evidence register](14_RESEARCH_EVIDENCE_REGISTER.md). Applicability depends on role, data, workflow, customer, deployment, and subsequent guidance. Qualified Vietnamese legal counsel must produce the binding analysis before launch; this list is not a conclusion of compliance.

## AI risk classification

Classify each use case before implementation:

| Class | Description | Default posture |
| --- | --- | --- |
| A — Assistive | Draft/summarize with no independent material effect | Human uses result; standard eval and privacy controls |
| B — Operational proposal | Model proposes typed action; deterministic code/human decides | Shadow then gated use |
| C — Bounded external effect | Low-risk effect under explicit policy and revocation | Signed workflow capability gate, monitoring, rollback |
| D — High-impact/high-risk | Legal rights, health, credit, employment, biometrics, critical safety, or regulated high risk | Defer unless specialist program and legal authorization exist |

Risk cannot be reduced merely by describing a system as a copilot. Assess actual data, decision, user dependence, scale, reversibility, and effect.

## Required impact assessments

Before a new workflow, provider, vertical, or autonomy increase, complete proportionate:

- Data protection/privacy impact assessment.
- AI use-case risk and human-oversight assessment.
- Threat model and abuse-case review.
- Tenant/data-flow diagram.
- Provider and connector assessment.
- Evaluation and residual-risk report.
- Incident, rollback, and customer-notification plan.
- Legal and contractual review where required.

Assessments have named approvers, expiry/review trigger, evidence references, and unresolved conditions.

## Human oversight

The interface must show the proposed effect, authoritative facts, uncertainty or missing data, policy result, and why approval is requested. Approvers need enough context to make an independent decision; repeated “approve” clicks without comprehension are not meaningful oversight.

Track approval acceptance, edits, reversals, review time, and automation bias indicators. High acceptance is not automatically quality.

## Security monitoring and incident response

Alert on:

- Cross-tenant or denied access attempts.
- Capability/tool calls outside normal workflow paths.
- Unexpected destination, volume, cost, token, or data-class changes.
- Repeated malformed outputs, loops, or policy denials.
- Consent/suppression violations or send reconciliation mismatches.
- Configuration/model/provider changes without release evidence.
- Sensitive content in telemetry or evaluation stores.
- Credential anomalies and connector scope changes.

Incident response must support immediate workflow/tenant/provider/channel kill switches, evidence preservation, credential rotation, outbox quarantine, external-effect reconciliation, customer/legal assessment, correction, and regression creation. Never use raw sensitive production incidents as broad training data by default.

## Assurance and customer evidence

Enterprise assurance should progressively provide:

- Architecture and data-flow documentation.
- Role/capability and connector inventory.
- Subprocessor/provider register.
- Security testing and remediation evidence.
- Release/evaluation summaries with limitations.
- Audit export and access review.
- Backup/restore and incident-exercise results.
- Retention/deletion evidence.

Do not claim certification, legal compliance, data residency, zero retention, or security guarantees beyond verified contractual and technical evidence.
