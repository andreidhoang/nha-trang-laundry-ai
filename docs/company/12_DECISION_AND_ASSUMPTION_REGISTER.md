# Strategic Decision and Assumption Register

**Status:** Strategic record; existing repository decision registries remain normative  
**Proposed owner:** Architecture and Product Council  
**Last updated:** 2026-08-10

## Use of this register

`STG-*` identifiers describe company strategy. They do not replace `DEC-*` records in `context/DECISION_REGISTRY.yaml` or architecture decisions in `docs/adr/`. An implementation-affecting strategic decision must be promoted into the appropriate normative artifact before code behavior changes.

Statuses:

- **ACCEPTED:** governing direction for this strategy suite, subject to formal implementation authority.
- **PROPOSED:** recommended but awaiting accountable approval or evidence.
- **HYPOTHESIS:** must be tested; not a fact or policy.
- **DEFERRED:** intentionally postponed with trigger.
- **REJECTED:** not pursued under current evidence.

## Decisions

### STG-001 — Product category

- **Status:** ACCEPTED for strategy
- **Decision:** Build a Vietnam-native Verified Operations OS, not a generic chatbot or autonomous employee.
- **Rationale:** The valuable gap is trustworthy execution across unstructured intake and existing systems; generic generation is increasingly commoditized.
- **Alternatives:** Horizontal assistant; ERP replacement; custom AI consultancy.
- **Consequences:** Product, sales, metrics, and architecture center on verified workflow outcomes.
- **Revisit:** Customers consistently buy a different repeatable category with better retention/economics.

### STG-002 — Laundry as reference vertical

- **Status:** ACCEPTED for strategy
- **Decision:** Preserve laundry as the reference pack and operational testbed while extracting reusable kernel primitives.
- **Rationale:** It contains representative order, price, logistics, consent, payment, and exception complexity and provides local access.
- **Alternatives:** Abandon laundry for a horizontal rewrite; keep the product laundry-only.
- **Consequences:** Domain-specific policy remains explicit; abstractions require a second-pack proof.
- **Revisit:** Lighthouse demand or transferability evidence is weak.

### STG-003 — Kernel plus versioned vertical packs

- **Status:** ACCEPTED for strategy
- **Decision:** Scale through a stable verified-operations kernel and constrained pack contract.
- **Rationale:** Separates universal trust semantics from variable vocabulary, workflow, policy, UX, connectors, and evals.
- **Alternatives:** One generic ontology; fork per customer/vertical.
- **Consequences:** Compatibility, pack certification, and anti-fork metrics become core platform work.
- **Revisit:** Second vertical cannot be represented without repeated kernel distortion.

### STG-004 — Deterministic business authority

- **Status:** ACCEPTED; inherited invariant
- **Decision:** Models never calculate or authorize money, policy, SLA, permission, or order state.
- **Rationale:** These decisions require reproducibility, versioning, authorization, and audit independent of stochastic generation.
- **Alternatives:** Prompt-only constraints; model judgment plus retrospective review.
- **Consequences:** Typed policy/domain services and human approval are mandatory.
- **Revisit:** Do not revisit as a convenience optimization; any change requires normative safety/legal review.

### STG-005 — One bounded concierge by default

- **Status:** ACCEPTED for strategy
- **Decision:** Use one customer-path agent; introduce specialist/multi-agent work only after an evaluated independent-task case.
- **Rationale:** Lower coordination, token, latency, observability, and authority risk.
- **Alternatives:** Role-play agent teams for all workflows.
- **Consequences:** Task-oriented tools and deterministic routing carry most modularity.
- **Revisit:** Repeated trials show a material outcome advantage on a specific parallelizable workload.

### STG-006 — Provider-neutral runtime boundary

- **Status:** ACCEPTED; aligns with `DEC-007`
- **Decision:** Own `ConstrainedAgentRuntime`; prefer the custom Responses-style adapter; keep OpenClaw as an `EVAL_ONLY` public-path comparator unless normative evidence changes the decision. Preserve the separate Private Owner OpenClaw trust-zone boundary.
- **Rationale:** Stable control, smaller trusted surface, and model/provider comparability.
- **Alternatives:** Framework-defined domain architecture; direct provider coupling.
- **Consequences:** The company owns loop, budgets, tools, checkpoints, and eval envelope; private owner assistance gains no public or business-authority path.
- **Revisit:** A managed runtime proves superior under same-envelope quality, safety, operations, and total cost.

### STG-007 — Dedicated tenancy first

- **Status:** PROPOSED
- **Decision:** Use dedicated single-tenant trust cells for early paid and enterprise deployments; introduce shared SaaS only after isolation and economics evidence.
- **Rationale:** Minimizes early blast radius and makes privacy, connector, key, and contract boundaries clearer.
- **Alternatives:** Shared multi-tenant SaaS from first customer.
- **Consequences:** Higher initial deployment cost; invest in automated cell provisioning.
- **Approver/evidence needed:** Security/architecture/finance review and target-customer requirements.

### STG-008 — Integrate with incumbents and official channels

- **Status:** ACCEPTED for strategy
- **Decision:** Treat accounting/POS/e-invoice/payment/channel platforms as systems of record/distribution and use official APIs only.
- **Rationale:** Faster trust and adoption, lower replacement scope, sustainable operations.
- **Alternatives:** Replace incumbent suites; scrape or automate personal accounts.
- **Consequences:** Connector certification and reconciliation are strategic product capabilities.
- **Revisit:** A specific system demonstrably cannot support the selected outcome and replacement has product evidence.

### STG-009 — Evidence-gated autonomy

- **Status:** ACCEPTED; inherited invariant
- **Decision:** Progress draft → shadow → internal → approval-led external → narrowly bounded automation per workflow/capability.
- **Rationale:** Agent performance and risk vary by workflow and harness; broad labels are misleading.
- **Alternatives:** Product-wide autonomy switch.
- **Consequences:** Release manifests and evaluation scope include tenant, workflow, channel, model, data, and autonomy class.
- **Revisit:** Only the exact thresholds may change through evidence; the gated structure remains.

### STG-010 — Hybrid outcome-oriented pricing

- **Status:** HYPOTHESIS
- **Decision:** Test platform fee plus deterministic verified-outcome/volume and scoped onboarding.
- **Rationale:** Aligns price with measurable operational value while preserving predictability.
- **Alternatives:** Per seat, token markup, pure services, pure success fee.
- **Consequences:** Requires precise outcome/meter/reversal contract and margin reporting.
- **Disconfirm:** Buyers reject the unit, attribution is ambiguous, or cost/revenue volatility is unacceptable.

### STG-011 — Initial segment and workflow priority

- **Status:** HYPOTHESIS
- **Decision:** Start with service order-to-cash/exception operations, then hospitality suppliers and wholesale reconciliation.
- **Rationale:** Local access, high message-to-system friction, reusable primitives, and lower risk than regulated decisions.
- **Alternatives:** Horizontal knowledge assistant; healthcare/finance; manufacturing first.
- **Disconfirm:** Discovery, paid pilots, or outcome economics rank another workflow materially higher.

### STG-012 — No proprietary-model or GPU moat initially

- **Status:** ACCEPTED for strategy
- **Decision:** Invest in workflow, ontology/policy, data/evals, integrations, trust, and deployment; buy frontier models behind adapters.
- **Rationale:** Current bottlenecks are customer evidence and trustworthy execution, not base-model training.
- **Alternatives:** Train a foundation model; early GPU/on-prem platform.
- **Consequences:** Maintain provider portability and data rights; self-host only on measured trigger.
- **Revisit:** Sovereignty, latency, availability, or economics clearly outweigh total ownership cost.

### STG-013 — Adoption is a product surface

- **Status:** ACCEPTED for strategy
- **Decision:** Require workflow champion, baseline, role training, weekly pilot review, and 30/90-day retention evidence.
- **Rationale:** Vietnam SME adoption evidence shows early training-driven usage can decay.
- **Alternatives:** Self-serve installation as the only adoption motion.
- **Consequences:** Customer success and operator UX are included in product design and margin.
- **Revisit:** A segment demonstrates durable self-serve activation at scale.

### STG-014 — No shared raw production learning by default

- **Status:** PROPOSED
- **Decision:** Production content is not reused across tenants or for training without explicit legal, contractual, purpose, consent, and technical authority.
- **Rationale:** Privacy, confidentiality, provenance, and tenant trust.
- **Alternatives:** Broad opt-out improvement pool.
- **Consequences:** Use redacted curated evals and tenant-isolated learning paths.
- **Approver/evidence needed:** Privacy/legal/security and customer contract model.

## Existing unresolved normative decisions

The following remain governed by `context/DECISION_REGISTRY.yaml` and must fail closed:

| ID | Topic | Strategic impact |
| --- | --- | --- |
| DEC-001 | Weight precision | Price/order evidence and pack policy |
| DEC-002 | Promotion event/evidence | Pricing and audit semantics |
| DEC-003 | Delivery distance/leg policy | Eligibility, charge, and SLA |
| DEC-004 | Rewash/loss/damage/credit | Exception workflow and financial authority |
| DEC-005 | Official production channel | Channel launch and connector investment |
| DEC-006 | Model-provider data terms | Real-PII eligibility and deployment |
| DEC-007 | Runtime target/OpenClaw comparator | Runtime engineering and retirement evidence |

This table is a projection for strategy context. Always inspect the machine-readable registry for current status.

## Assumption register

| ID | Assumption | Evidence now | Test | Failure response |
| --- | --- | --- | --- | --- |
| ASM-001 | A narrow workflow can show value in 7–30 days | Market/adoption research; no project proof stated | Paid lighthouse baseline experiment | Narrow or change workflow |
| ASM-002 | Customers will name a champion and publish policy | Required by operating design | Discovery and pilot charter conversion | Disqualify or remain assistive |
| ASM-003 | Laundry primitives transfer to a second vertical | Architectural inference | Build second pack without kernel fork | Redesign boundary or remain vertical |
| ASM-004 | Official integrations expose sufficient state/effects | Provider documentation varies | Connector spike and contract test | Human workflow or different wedge |
| ASM-005 | Hybrid outcome pricing is legible and profitable | Frontier-company precedent, not local proof | Pricing interviews and paid cohorts | Switch unit/packaging |
| ASM-006 | Dedicated cells are viable at early price points | Security inference | Full-cost deployment model | Raise segment/price or improve automation |
| ASM-007 | Frontier-model portability is practical | Stable adapter design | Same-envelope parity and migration drill | Support narrower provider set |
| ASM-008 | Human review declines with product maturity | Common product expectation | Cohort review-minutes metric | Reprice, narrow, or stop workflow |
| ASM-009 | Vietnamese language/channel fit differentiates | Local market/channel evidence | Win/loss and outcome comparison | Refocus moat on workflow/integrations |
| ASM-010 | Customers prefer integration over replacement | Competitive/operational inference | Discovery and sales evidence | Reassess scope per vertical |

## Decision record template

New entries should include ID, status, owner/approver, decision, date, context, evidence, alternatives, rationale, consequences, risks, normative artifacts affected, review date/trigger, and rollback. Do not record private chain-of-thought; record enough rationale for an independent reviewer to understand and challenge the choice.
