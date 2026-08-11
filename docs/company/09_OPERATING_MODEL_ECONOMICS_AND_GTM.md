# Operating Model, Economics, and Go-to-Market

**Status:** Strategic hypotheses; no pricing, hiring, spending, or sales authorization  
**Proposed owners:** CEO, Finance, Product, and Go-to-Market

## Operating thesis

The company should behave like a vertical product company with a reusable platform—not a model reseller and not a bespoke automation consultancy. It earns the right to expand by proving one workflow's outcome, retention, safety, and contribution margin.

## Customer acquisition motion

### Lighthouse phase

Founder-led selling is appropriate because product discovery, policy mapping, and trust are still coupled. Select a small number of paid design partners that fit the ICP, share evidence, and accept a bounded shadow/approval-led rollout.

The sales artifact is a **workflow outcome charter**, not a generic AI proposal. It defines:

- Current workflow and baseline.
- Target outcome and measurement method.
- Included actors, locations, channels, systems, and data.
- Explicit exclusions and unsupported policy.
- Human roles and approval authority.
- Deployment, retention, security, and support terms.
- Pilot milestones, stop rules, and conversion criteria.

### Repeatable phase

After at least three similar retained customers, standardize discovery, data mapping, pack configuration, connector setup, training, evaluation, launch, and review. Product-led elements may reduce setup, but critical policy and authority publication remain accountable customer actions.

### Scale phase

Use local implementation partners, accounting/POS/e-invoice integrators, industry associations, and trusted technology providers. Partners receive certification, test environments, scoped tooling, and audit requirements. They cannot self-authorize capability or weaken the tenant's controls.

## Land and expand

Land with one workflow tied to a visible metric. Expand only when the first workflow is retained and governed:

1. More volume or locations for the same workflow.
2. Adjacent workflow using the same domain objects and connectors.
3. Operations workspace and reporting.
4. Dedicated enterprise control-plane features.
5. A new vertical pack only through the product investment gate.

Expansion based only on seat count or chat usage can hide a lack of business value.

## Pricing model

The recommended hypothesis is a hybrid:

- **Platform fee:** environment, controls, console, baseline support, and included capacity.
- **Verified workflow unit:** an outcome whose success, exclusions, correction, and reversal are machine-measurable.
- **Onboarding/integration:** fixed, scoped fee based on standard packages.
- **Enterprise control tier:** dedicated deployment, identity, data, audit, support, and contractual controls.

Never charge outcome fees on an ambiguous model judgment. A billable unit must be computed by deterministic metering and reconciled against authoritative state.

Initial willingness-to-pay ranges are hypotheses:

- Micro business: VND 0.5–1.0 million per month.
- SME: VND 2–5 million per month plus onboarding.
- Mid-market/enterprise: VND 15–30+ million per month plus integration/support.

The owner-approved pricebook, not this document or an LLM, controls actual pricing.

## Million-dollar path as a scenario

One illustrative—not forecast—annual recurring revenue scenario is:

- 300 SME customers × VND 3 million/month = VND 10.8 billion ARR.
- 60 mid-market customers × VND 20 million/month = VND 14.4 billion ARR.
- Combined = VND 25.2 billion ARR before onboarding/services.

The scenario tests scale requirements; it is not market evidence. At that scale the company must support 360 retained customers without linear implementation and review headcount. A credible plan therefore needs standardized packs, automated evidence, reliable connectors, tiered support, and gross-margin discipline before aggressive acquisition.

## Unit economics model

Finance should calculate, by tenant and workflow:

`Contribution margin = recognized revenue – model/inference – connector/channel – compute/storage – human review – direct support – customer-specific operations`

Also track:

- Onboarding cost and time to recover it.
- Gross and contribution margin by cohort.
- Verified outcomes per VND of variable AI cost.
- Human-review minutes per 100 outcomes.
- Support hours per tenant/month.
- Connector failure/reconciliation cost.
- Net revenue retention and logo retention.
- Customer acquisition cost and payback only after a repeatable channel exists.

Do not hide implementation labor in R&D or treat founder labor as free when evaluating repeatability.

## Services boundary

Services may discover and enable the product, but each engagement must yield reusable assets: configuration schema, pack capability, connector, eval case, onboarding playbook, or explicit rejection rationale.

Warning signs of consulting drift:

- Customer-specific branches or prompts.
- Unbounded data cleanup included in subscription.
- Unique workflows with no reusable ontology.
- Manual review that grows linearly but is excluded from margin.
- Sales commitments before policy/product review.
- Revenue recognized from work the supported product cannot operate.

Set a target that customer-specific code tends toward zero and review exceptions quarterly.

## Customer success and adoption

Customer success owns operational change, not just ticket resolution:

- Validate source-data readiness.
- Train process owner, operator, approver, and administrator roles.
- Review weekly activation and correction during rollout.
- Publish a monthly outcome and risk report.
- Run 30/90-day retention and champion-health reviews.
- Escalate missing policy, inactive users, or workaround behavior early.

A customer is not live because integration succeeded. It is live when the workflow is used, governed, and producing the contracted outcome.

## Support model

| Severity | Example | Initial posture |
| --- | --- | --- |
| Critical | Unauthorized effect, cross-tenant exposure, widespread incorrect material state | Immediate kill switch and incident command |
| High | Workflow unavailable or material reconciliation backlog | On-call response and customer update |
| Medium | Bounded degradation with safe human fallback | Business-hours triage under SLO |
| Low | Usability/configuration question | Queue and product feedback loop |

Contractual response objectives are published only after staffing, tooling, rehearsal, and owner approval. The model cannot promise an SLA.

## Organization design

The smallest credible cross-functional units are:

- **Workflow product pod:** product/domain lead, backend/platform engineer, AI/eval engineer, product designer/implementation lead.
- **Trust platform:** security/privacy, identity/tenancy, release/evidence, reliability.
- **Customer outcomes:** discovery, onboarding, training, success, support.
- **Commercial:** founder/vertical seller, partnerships, finance/operations.

Early team members may cover several roles, but decision rights remain explicit. Domain policy comes from accountable owners and design partners, not engineers or models.

## Investment allocation hypothesis

For product and engineering investment over the next platform-building stage:

| Area | Working allocation | Intended output |
| --- | ---: | --- |
| Vertical workflow and operator UX | 25% | Fast measurable value and adoption |
| Kernel and tenancy | 20% | Reusable controlled platform |
| Evaluation, context, and data quality | 20% | Compounding reliability and evidence |
| Official connectors and reconciliation | 15% | Real workflow completion |
| Security, privacy, deployment, assurance | 10% | Enterprise trust and bounded risk |
| Runtime/model efficiency | 10% | Quality, latency, and cost improvement |

These are portfolio guardrails, not an approved budget. Reallocate based on bottleneck evidence. Do not imitate frontier-company GPU or framework spending when customer outcome, data, and integrations are the constraint.

## Forecast discipline

Maintain three separate models:

- **Demand model:** qualified workflows, conversion, activation, and retention.
- **Capacity model:** onboarding, review, support, integrations, and incidents.
- **Financial model:** revenue recognition, variable cost, headcount, cash, and scenario risk.

Use base, downside, and upside assumptions with named evidence and dates. Avoid multiplying broad SME counts by an assumed subscription price and calling the result a forecast.

## Board-level proof points

Before funding aggressive growth, leadership should be able to show:

- Three or more retained customers for one repeatable workflow.
- Credible baseline-to-outcome improvement.
- 90-day usage and champion continuity.
- Declining deployment/support effort per customer.
- No unresolved critical control failure.
- Positive or clearly improving contribution margin.
- Second-pack evidence that the kernel generalizes.
- A hiring and partner model that does not weaken release authority.
