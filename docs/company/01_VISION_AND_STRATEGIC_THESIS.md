# Vision and Strategic Thesis

**Status:** Strategic, non-normative, and not a release authorization  
**Decision horizon:** 2026–2030  
**Proposed owner:** CEO/Product

## Vision

Vietnamese businesses should be able to operate with enterprise-grade control without needing enterprise-scale software teams.

## Mission

Build the Vietnam-native verified operations layer that converts natural-language requests, documents, and exceptions into governed business workflows with visible evidence, predictable economics, and accountable human control.

## Category thesis

The winning category is not a general chatbot, an autonomous employee, or another system of record. It is a **Verified Operations OS**:

- **Conversational at intake:** understands Vietnamese, channel context, documents, images, and incomplete requests.
- **Deterministic at authority:** policy, money, SLA, permission, and state transition decisions are made by versioned code and owner-approved data.
- **Agentic in execution:** can gather context, select bounded tools, propose plans, recover from reversible failures, and escalate exceptions.
- **Evidence-native:** every material decision and effect is attributable to input, policy, actor, tool, version, and outcome.
- **Composable by vertical:** reusable operational kernel plus versioned domain packs, starting with laundry and service-order operations.

## First-principles problem statement

SMEs and mid-market enterprises do not primarily lack AI text generation. They lack a low-friction path between fragmented customer messages and trustworthy execution:

1. Requests arrive in Vietnamese through chat, calls, images, forms, and informal documents.
2. Knowledge is divided among people, spreadsheets, accounting/POS systems, and unwritten exception policy.
3. Existing software records transactions but often does not resolve ambiguous cross-system work.
4. Generic models are fluent but probabilistic; business effects require explicit authority and auditability.
5. Adoption collapses when a product adds setup work, lacks a local champion, or cannot prove value quickly.

The product therefore owns the **interpret–verify–execute–learn loop**, while integrating with systems that remain authoritative.

## Customer promise

For one high-friction workflow, the customer should receive a verified result with less manual coordination, no hidden policy invention, and a complete review trail. A user must always be able to answer:

- What did the system understand?
- Which source and policy did it use?
- What will happen next?
- What requires human approval?
- What actually happened?
- How can it be corrected or rolled back?

## Strategic choices

### 1. Win a workflow before selling a platform

The initial offer is one painful, frequent, measurable workflow completed end to end. Platform breadth follows repeated proof, not presentation breadth.

### 2. Use laundry as a reference implementation

Laundry exposes the hard primitives—multichannel intake, service catalog, price snapshot, order state, pickup/delivery, exceptions, consent, payment evidence, and customer communication. Those primitives become reusable kernel contracts. Laundry-specific vocabulary and policy remain in a vertical pack.

### 3. Expand through adjacent operational shapes

Prioritize workflow families with similar primitives and manageable regulatory exposure:

1. Service order-to-cash and exception handling.
2. Hospitality supplier and guest-service operations.
3. Distributor/wholesale order and reconciliation workflows.
4. Household-business compliance-to-cash workflows, only with strong integrations.
5. Manufacturing supplier-document and quality workflows.

Defer high-risk healthcare, credit/banking decisions, employment decisions, biometric surveillance, and safety-critical control until the company has legal, security, domain, and assurance capacity appropriate to them.

### 4. Integrate with incumbents

Accounting, POS, e-invoice, payment, identity, and official messaging providers are systems of record or distribution. The company should complement—not casually replace—MISA, KiotViet, Sapo, Base, banks, tax/e-invoice providers, and official channel infrastructure.

### 5. Earn autonomy progressively

The autonomy sequence is draft → shadow → internal execute → approved external execute → narrowly bounded low-risk automation. Every transition is gated by workflow-specific evidence, not model reputation.

## Defensible advantage

The durable moat is a compound system, not access to a frontier model:

- Versioned Vietnam-specific operational ontology and policy packs.
- High-quality, permissioned, outcome-linked workflow traces.
- A deterministic authorization and effect layer.
- Evaluation sets built from real exceptions and regressions.
- Reliable official-channel and system-of-record integrations.
- Fast deployment templates with measurable time-to-value.
- Trust earned through audit, local operations, and customer control.

Model providers will improve and commoditize capabilities. The platform must benefit from model progress without transferring business authority to the model.

## North Star and guardrails

The North Star is **verified workflow outcomes completed without material correction per active customer**. It is constrained by:

- Zero unauthorized material external effects.
- Zero cross-tenant data exposure.
- No model-originated money, policy, permission, SLA, or state decision.
- Stable or improving customer correction and escalation rates.
- Demonstrable workflow time, error, or cash-cycle improvement.
- Sustainable gross margin after model, integration, support, and review cost.

Definitions and decision thresholds are in [15 — Metrics and experimentation](15_METRICS_AND_EXPERIMENTATION.md).

## What the company will not build

- A generic tool-using agent with unrestricted shell, browser, database, messaging, or credentials.
- A “digital employee” whose authority cannot be represented as explicit policy.
- A prompt-only product that hides state, policy, or model drift.
- An all-in-one ERP replacement before proving an integration-led wedge.
- A multi-agent customer workflow merely because multiple agents look sophisticated.
- A data business based on undisclosed secondary use of customer content.
- A services organization in which each customer requires a private codebase.

## Strategic horizons

### Horizon 1 — Prove verified execution

Complete internal and paid lighthouse workflows in the laundry/service-order reference pack. Establish outcome evidence, operator trust, a stable harness, official-channel boundaries, and repeatable onboarding.

### Horizon 2 — Prove the platform abstraction

Launch a second vertical pack using the same kernel, demonstrate upgrade-safe tenant configuration, and show that most implementation work is configuration, adapters, eval cases, and policy—not forks.

### Horizon 3 — Enterprise control plane

Offer dedicated deployment, enterprise identity, data controls, audit export, policy administration, connector governance, and multi-workflow orchestration. Shared SaaS is introduced only after tenant-isolation evidence and economics justify it.

## Conditions that would falsify the thesis

Leadership should reconsider the strategy if rigorous pilots show any two of the following after sufficient iterations:

- Target workflows cannot deliver measurable value within 30 days.
- Customers will not assign a process owner or maintain required source data.
- More than half of deployments require kernel code forks after the second vertical.
- Human correction and exception-handling cost prevents viable gross margin.
- Incumbent systems can deliver the same outcome through configuration at materially lower switching cost.
- Regulatory or channel constraints make the intended data and execution model impractical.

Falsification is a useful result: it prevents scaling an attractive narrative without a viable operating system underneath it.
