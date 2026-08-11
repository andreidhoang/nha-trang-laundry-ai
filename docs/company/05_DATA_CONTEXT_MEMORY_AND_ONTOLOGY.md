# Data, Context, Memory, and Ontology

**Status:** Strategic data architecture; normative schemas and policy remain authoritative  
**Proposed owners:** Data Platform and Security

## Objective

Give agents the minimum correct, permitted, and current information needed for a workflow while preserving the distinction between authoritative state, derived interpretation, and temporary reasoning artifacts.

The central rule is:

> Retrieval may supply evidence; it cannot manufacture authority.

## Data planes and authority

| Plane | Examples | Authority | Mutation path |
| --- | --- | --- | --- |
| Authoritative domain state | Customer/account identity, order, catalog, published price snapshot, consent, payment evidence, policy version | Deterministic systems of record | Typed domain commands only |
| Execution evidence | Inbox envelope, approval, domain event, audit, outbox, delivery receipt | Immutable or append-only operational evidence | Transactional services/workers |
| Semantic derived data | Embeddings, summaries, classifications, extracted candidates, entity links | Non-authoritative and reproducible | Versioned pipelines |
| Runtime scratch | Plans, candidate actions, temporary files, branch-agent artifacts | Ephemeral, no business authority | Isolated harness |
| Analytics/evaluation | Redacted traces, labels, outcome aggregates, experiment assignments | Evidence under defined metric contracts | Controlled pipelines |

An agent answer must not silently promote derived data into authoritative state. Promotion requires validation, policy, and an explicit domain command.

## Minimum operational ontology

The shared kernel should define only concepts repeated across verified workflows:

- **Tenant:** legal/operating boundary, configuration, deployment, and data scope.
- **Actor:** customer, operator, approver, service identity, or integration identity.
- **Identity binding:** verified link between a channel/system identity and an actor.
- **Case:** bounded unit of customer or operational intent.
- **Conversation:** ordered channel interaction associated with a case.
- **Document/evidence item:** immutable source reference, checksum, classification, provenance, and extraction versions.
- **Catalog item/service:** owner-published offer and attributes.
- **Policy:** versioned deterministic rule set with scope and effective period.
- **Quote/price snapshot:** immutable result produced by approved pricing code.
- **Order/work item:** versioned state machine for committed work.
- **Task:** assigned operational action with status and evidence.
- **Approval:** durable authorization for an exact proposed effect.
- **Consent/suppression:** purpose-, channel-, subject-, and time-scoped communication state.
- **Domain event:** immutable fact that a business transition occurred.
- **Effect/outbox item:** requested external side effect and delivery lifecycle.
- **Outcome:** verified result linked to the workflow and correction history.

Vertical packs extend these concepts; they do not redefine their trust semantics.

## Provenance envelope

Every material datum presented to a model or reviewer should be attributable through:

- `tenant_id`
- `source_system` and `source_record_id`
- `source_actor` or service identity
- `captured_at`, `effective_at`, and optional `expires_at`
- `schema_version`
- `policy/configuration version`
- `content hash` or immutable snapshot reference
- `data classification` and permitted purposes
- `permission scope`
- `transformation/extractor/model version`
- `confidence` for derived fields, never for authoritative fields
- `supersedes/superseded_by` where applicable

The implementation must use typed schemas rather than inferring these fields from prose.

## Context packet contract

A context packet is a signed or integrity-checked manifest of references, not an uncontrolled prompt concatenation. It includes:

1. Work objective and terminal criteria.
2. Tenant, actor, workflow, and permission scope.
3. Authorized capability manifest.
4. Applicable policy/configuration versions.
5. Current authoritative resource versions and snapshots.
6. Relevant source excerpts with provenance and injection labels.
7. Accepted compact work summary and unresolved questions.
8. Model, prompt, harness, tool-registry, and context-assembler versions.
9. Token/time/cost budgets and expiry.

Assembly is fail-closed when required sources are missing, stale, contradictory, or unauthorized. The model does not decide which tenant or policy applies.

## Context selection

Use deterministic filters first: tenant, actor, resource, workflow state, effective time, purpose, and permission. Semantic ranking operates only inside the allowed set.

Selection quality is evaluated for:

- Evidence recall: required evidence was present.
- Context precision: irrelevant or misleading material was excluded.
- Authority correctness: authoritative and advisory material were labeled accurately.
- Freshness: current versions were supplied where required.
- Injection resistance: embedded instructions did not alter runtime authority.
- Budget efficiency: useful outcome per context token.

Larger context windows do not remove the need for selection. Excess context can obscure constraints and increase exposure.

## Memory model

“Memory” is split by purpose and lifecycle:

### Session memory

Short-lived conversation turns and tool results for one work item. It expires with the workflow retention policy and is reconstructed from authorized evidence when resumed.

### Durable business memory

Facts that affect future work—address, preference, contract term, exception outcome—become durable only through typed validation and an authoritative domain write with provenance and correction path.

### Episodic work summary

A concise model- or code-produced summary of completed steps, evidence references, open questions, and commitments. It is derived, versioned, reviewable, and never substitutes for original evidence.

### Semantic retrieval index

Embeddings and search metadata used to find permitted sources. They are derived indexes that can be deleted and rebuilt. Vector proximity is not identity, policy, permission, or truth.

### Learning memory

Redacted, permissioned evaluation examples and error labels. Production content is not converted into training or shared learning data by default.

No category stores chain-of-thought.

## Document and unstructured-data pipeline

1. Accept through an allowlisted channel with size/type limits and malware scanning.
2. Persist the immutable original or approved encrypted reference and checksum.
3. Classify sensitivity and tenant/purpose before processing.
4. Extract text/structure in an isolated worker.
5. Store candidate fields with spans, confidence, and extractor version.
6. Validate identifiers and authoritative values against systems of record.
7. Require human review for low-confidence or material fields under policy.
8. Index only permitted, redacted representations.
9. Apply retention/deletion to original, derivatives, indexes, caches, and backups according to policy.

Instructions found inside a document are content, not system or tool instructions.

## Data quality controls

For each authoritative entity, define:

- Business owner and technical steward.
- Required identifiers and uniqueness constraints.
- Validity and state-transition rules.
- Freshness objective and reconciliation source.
- Completeness and mismatch metrics.
- Correction and merge workflow.
- Downstream consumers and blast radius.

If entity resolution is ambiguous, preserve candidates and request resolution. Never let a model merge customer, account, order, or payment identities on conversational similarity alone.

## Tenant and permission isolation

Tenant scope is established before retrieval and enforced in storage, queries, caches, indexes, queues, traces, and object storage. Defense in depth should include:

- Dedicated trust cells initially for paid and enterprise deployments.
- Row-/schema-/database-level controls appropriate to the deployment tier.
- Per-tenant encryption context and secret namespace.
- Tenant-bound cache and vector-index keys.
- Service identity plus end-user/actor propagation.
- Negative cross-tenant tests for every sensitive boundary.
- Export/deletion workflows that cover derived stores.

Metadata can be sensitive even when content is encrypted or redacted.

## Privacy lifecycle

Collection, use, sharing, retention, and deletion must be tied to documented purpose and authority. Before enabling real PII with a provider, the company must verify contractual data use, retention, deletion, region/transfer, security, subprocessors, incident, and model-training terms. Unknown provider posture keeps the data path disabled.

Required capabilities include:

- Data inventory and processing-purpose register.
- Classification and minimization at ingestion.
- Consent/other lawful-basis record where applicable.
- Subject/customer access, correction, deletion, and export workflows.
- Retention schedules with legal holds.
- Provider and cross-border transfer records.
- Backup/index/cache deletion verification.
- Breach and customer-notification playbooks.

Vietnam applicability and legal interpretation require qualified counsel; see Document 07.

## Feedback and learning

Capture correction as structured evidence:

- Original proposal and versions.
- Human correction category and corrected structured result.
- Whether the cause was policy, context, data, tool, model, UX, integration, or operator training.
- Materiality and customer impact.
- Whether a regression case was created.

Do not treat every user edit as model-label truth. Some edits reflect preference, changed source data, or invalid policy. Curation and consent are required before evaluation or training use.

## Data architecture gates

No workflow advances to customer-facing effect until:

- Authoritative sources and owners are named.
- Required context has a typed, versioned manifest.
- Tenant and permission denial tests pass.
- PII/provider eligibility is recorded.
- Retention, export, correction, and deletion paths are designed.
- Derived data can be invalidated and rebuilt.
- Trace redaction is tested against representative payloads.
- Reconciliation handles source drift and duplicate identifiers.
