# Execution Roadmap

**Status:** Strategic sequencing; `delivery/` is authoritative for implementation status  
**Planning horizon:** 0–18 months from strategy approval  
**Proposed owner:** Company Leadership

## Roadmap rule

Advance by evidence, not calendar. Dates are planning windows; entry and exit criteria control investment and autonomy. A phase can be narrowed, repeated, or stopped.

The current repository is in production-hardening with public/customer-facing capabilities not authorized. Existing queue state, including the custom Responses runtime work and unresolved OpenClaw evidence, must be read from `delivery/` and `context/`, not inferred from this roadmap.

## Phase 0 — Truth baseline and company alignment (days 0–30)

### Objective

Convert strategy into an approved, measurable, legally reviewable operating boundary while completing safe local engineering already selected by the delivery system.

### Work

- Approve or revise category, ICP, first workflow, and non-goals.
- Assign owners for strategic decisions, metrics, risk, security, and release authority.
- Resolve or explicitly carry forward the open laundry business-policy decisions.
- Complete provider data-use/retention/region/deletion assessment before real PII.
- Inventory actual capabilities versus desired platform capabilities.
- Define lighthouse discovery script, workflow charter, and baseline method.
- Preserve the current custom runtime target and the public-path OpenClaw `EVAL_ONLY` comparator decision unless normative evidence changes them; keep the separately isolated Private Owner OpenClaw boundary distinct.
- Establish research, decision, risk, and metric review cadence.

### Exit evidence

- Signed company thesis and first ICP/workflow choice.
- Named customer/problem discovery pipeline.
- Named owners and decision rights.
- Provider/privacy decision or explicit disabled data path.
- No mismatch among this suite, normative contracts, and published capability posture.

## Phase 1 — Reference workflow in internal shadow (days 31–90)

### Objective

Demonstrate a reliable bounded workflow in the laundry/service-order reference pack without unauthorized public effects.

### Work

- Complete the approved custom runtime adapter behind the stable interface.
- Exercise typed tool, policy, transaction, approval, audit, outbox, and sender boundaries.
- Build representative Vietnamese scenario/adversarial/regression datasets.
- Implement operator-visible context, proposed action, policy result, and correction capture.
- Run provider/runtime parity under the same evaluation envelope.
- Establish trace redaction, dashboards, kill switches, reconciliation, and rollback.
- Observe target customers' real workflow and baseline measures.

### Exit evidence

- Declared repository gates pass with recorded evidence.
- Repeated end-to-end eval thresholds pass with no critical authorization violation.
- Crash/retry/reconciliation exercises produce no duplicate material effect.
- Operators can resolve all unsupported/ambiguous paths through explicit human states.
- One or more qualified design partners sign a bounded paid-pilot charter.

### Prohibited shortcut

Internal technical success does not authorize public messaging or customer effects.

## Phase 2 — Paid lighthouse deployments (months 3–6)

### Objective

Prove that a customer can adopt one verified workflow and receive measurable value in a dedicated trust cell.

### Work

- Deploy to a very small number of design partners with dedicated tenant/data/key scopes.
- Start in shadow, then approval-led external effects only after signed workflow gates.
- Integrate only official, supportable channels and systems of record.
- Run weekly adoption/correction and monthly outcome/risk reviews.
- Measure onboarding effort, human review, support, connector cost, and contribution margin.
- Convert failures into curated regression cases and product/pack improvements.

### Exit evidence

- At least three paying customers share the same workflow shape, or leadership explicitly revises the threshold with evidence.
- Target outcome improves against a credible baseline.
- 30- and 90-day workflow retention and champion engagement meet approved targets.
- No unresolved critical safety/privacy incident.
- Deployment/support effort is understood and trending toward a repeatable package.
- Actual willingness-to-pay and unit cost support a viable pricing revision.

## Phase 3 — Platform abstraction and second pack (months 6–9)

### Objective

Show that the reference system contains a reusable platform rather than a laundry-specific codebase.

### Work

- Freeze and publish kernel/pack/connector interface versions.
- Select a second vertical by workflow-shape score and paid design-partner evidence.
- Build its ontology extensions, policy/config schema, UX, connectors, and eval suite without weakening kernel invariants.
- Automate dedicated trust-cell provisioning, upgrade, rollback, evidence, and support diagnostics.
- Measure reusable versus customer-/vertical-specific effort.

### Exit evidence

- Second pack reaches at least shadow and preferably paid lighthouse use.
- Most implementation is pack/config/adapter/eval work, not kernel forks.
- Cross-pack regression, compatibility, migration, and rollback pass.
- Reference customers upgrade without material workflow regression.
- Product economics remain viable after added support surface.

## Phase 4 — Enterprise readiness (months 9–15)

### Objective

Sell controlled multi-workflow deployments to mid-market and enterprise customers.

### Work

- Enterprise identity/provisioning and role review.
- Dedicated deployment, networking, key, retention, and provider options.
- Policy/capability administration with separation of duties.
- Audit/evaluation export and customer assurance package.
- Contractual support, incident, maintenance, backup, and recovery operations.
- Certified integration program and version/deprecation monitoring.
- Multi-workflow operations workspace and outcome reporting.

### Exit evidence

- Independent security/privacy/legal review for intended scope.
- Tested tenant restore, export, deletion, incident containment, and release rollback.
- Enterprise pilot meets outcome, adoption, reliability, and support objectives.
- Procurement/security evidence is repeatable rather than assembled ad hoc.
- Contribution margin includes real enterprise support and deployment cost.

## Phase 5 — Controlled scale and optional shared tenancy (months 15–18+)

### Objective

Scale distribution and infrastructure only after repeatability.

### Work

- Standardize partner certification, implementation, and quality oversight.
- Automate pack/catalog distribution and tenant-safe release waves.
- Evaluate shared control/data plane only against demand, isolation evidence, and total economics.
- Add model/provider or self-hosting options only for measured quality, sovereignty, latency, availability, or cost needs.
- Introduce narrowly autonomous low-risk effects only per workflow evidence.

### Exit evidence

- Retained cohorts and repeatable acquisition channels.
- Declining onboarding/support load per customer.
- Tested multi-tenant isolation if shared SaaS is proposed.
- Sustained contribution margin and incident capacity.
- No capability scope outruns governance and support.

## Cross-phase workstreams

| Workstream | Continuous deliverable |
| --- | --- |
| Customer evidence | Observed workflows, baselines, outcomes, retention, willingness to pay |
| Domain/product | Versioned pack, operator UX, policy gaps, adoption workflow |
| Runtime/harness | Stable boundary, bounded execution, routing, model portability |
| Data/context | Provenance, quality, lifecycle, permissioned context, corrections |
| Evaluation | Regression, holdout, shadow/live, repeated trials, release reports |
| Trust | Identity, authorization, privacy, threat model, incident/assurance |
| Platform | Kernel, tenancy, configuration, connector lifecycle, compatibility |
| Reliability | Durable state, reconciliation, SLOs, runbooks, backup/restore |
| Economics | Pricing, metering, review/support cost, margin, capacity |

## First 90-day leadership scorecard

By day 90, leadership should review:

- Number of observed target workflows and qualified design partners.
- One selected workflow with approved authority/policy map.
- Provider/privacy eligibility decision.
- Current eval pass distributions and critical failures.
- Operator correction and handoff quality in internal shadow.
- Actual onboarding and integration effort estimates.
- Open decisions with owners and due dates.
- Risks whose indicators worsened.
- Repository delivery evidence and remaining blockers.

## Stop/pivot triggers

Pause expansion when:

- A critical authorization, tenant, consent, or effect-integrity failure is unresolved.
- Customer policy cannot be made explicit.
- A channel/integration lacks an official sustainable path.
- Workflow retention falls despite successful technical onboarding.
- Human review or customization prevents viable contribution margin.
- A new vertical requires changing core invariants rather than extending a pack.
- Legal/provider posture is unknown for the intended data/effect.

The safe response is to narrow, return to shadow, change the workflow, or stop—not to hide the condition in a prompt.

## What is intentionally absent

This roadmap does not mark repository tasks complete, set production dates, authorize sales claims, choose unresolved business policy, or promise revenue. Those actions require their respective evidence and owners.
