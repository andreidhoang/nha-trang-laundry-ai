# Company Strategy and Agentic Engineering System

**Version:** 0.1

**As of:** 2026-08-10

**Status:** Strategic, non-normative, and not a release authorization
**Proposed owners:** CEO/Product and Principal Engineering

## Purpose

This directory translates the project's repository truth and 2026 market and frontier-agent research into a company-level operating system. It defines what company to build, which customers to serve, how the platform should evolve, where AI may and may not act, how evidence will be collected, and which decisions remain hypotheses.

The strategy is to build a **Vietnam-native Verified Operations OS** for SMEs and enterprises. It turns conversations, documents, and operational exceptions into typed, permission-aware, auditable workflows. Existing systems of record—accounting, point-of-sale, e-invoice, payment, channel, and enterprise systems—remain authoritative. The laundry product is the first reference vertical and proving ground, not the boundary of the company.

## Authority and conflict resolution

These documents do not supersede repository contracts, signed gates, business policy, or applicable law. The source-of-truth order is:

1. Owner-approved published configuration and signed release/capability decisions.
2. Authoritative PostgreSQL business state and immutable snapshots.
3. Machine-readable files in `specs/contracts/`, `specs/evals/`, and `delivery/`.
4. Approved Vietnamese specifications and architecture decision records.
5. `BUILD_ENGINEERING_SPEC.md` and `AGENTS.md`.
6. This company strategy suite.
7. External research and working hypotheses.

When sources conflict, the higher source wins. The conflict must be recorded; it must not be resolved implicitly in code or prompts. Unknown policy fails closed as `REQUIRE_HUMAN` or `NOT_SUPPORTED`.

## Current reality

At this version, the repository remains an evidence-driven, constrained operations system in production-hardening work. Public/customer-facing automation is **not authorized** unless a signed release gate explicitly says otherwise. This suite describes the intended company and platform direction; it does not claim that the complete platform, enterprise tenancy, or any public capability already exists.

## Document map

| Document | Governing question | Primary owner |
| --- | --- | --- |
| [01 — Vision and strategic thesis](01_VISION_AND_STRATEGIC_THESIS.md) | Why should this company exist and what will it not become? | CEO/Product |
| [02 — Product and market strategy](02_PRODUCT_AND_MARKET_STRATEGY.md) | Who has the urgent problem, what is the wedge, and how will demand be proven? | Product/GTM |
| [03 — Reference architecture](03_REFERENCE_ARCHITECTURE.md) | What enduring technical boundaries make the product safe and extensible? | Principal Engineering |
| [04 — Agent harness engineering](04_AGENT_HARNESS_ENGINEERING.md) | How are model capabilities constrained, evaluated, and improved? | AI Platform |
| [05 — Data, context, memory, and ontology](05_DATA_CONTEXT_MEMORY_AND_ONTOLOGY.md) | What may an agent know, remember, retrieve, and treat as truth? | Data/Platform |
| [06 — Evaluation, observability, and improvement](06_EVALUATION_OBSERVABILITY_AND_IMPROVEMENT.md) | How is agent quality demonstrated rather than asserted? | AI Quality |
| [07 — Security, privacy, and AI governance](07_SECURITY_PRIVACY_AND_AI_GOVERNANCE.md) | How is authority bounded and Vietnam risk managed? | Security/Legal |
| [08 — Platform, tenancy, vertical packs, and integrations](08_PLATFORM_TENANCY_VERTICAL_PACKS_AND_INTEGRATIONS.md) | What is reusable and what remains tenant- or vertical-specific? | Platform/Product |
| [09 — Operating model, economics, and go-to-market](09_OPERATING_MODEL_ECONOMICS_AND_GTM.md) | How can the company grow without becoming bespoke consulting? | CEO/Finance/GTM |
| [10 — Reliability, SRE, and delivery](10_RELIABILITY_SRE_AND_DELIVERY.md) | How are durable effects, incidents, releases, and recovery controlled? | Engineering/SRE |
| [11 — Execution roadmap](11_EXECUTION_ROADMAP.md) | What evidence unlocks each stage of investment and autonomy? | Company leadership |
| [12 — Strategic decision and assumption register](12_DECISION_AND_ASSUMPTION_REGISTER.md) | Which strategic choices are accepted, proposed, or unresolved? | Architecture Council |
| [13 — Risk register](13_RISK_REGISTER.md) | What could invalidate the strategy and how will warning signs be handled? | Risk owners |
| [14 — Research evidence register](14_RESEARCH_EVIDENCE_REGISTER.md) | Which external and internal evidence supports each claim? | Research/Product |
| [15 — Metrics and experimentation](15_METRICS_AND_EXPERIMENTATION.md) | Which measures govern product, safety, reliability, and economics? | Product/Data |

## Artifact taxonomy

Every consequential statement should be recognizable as one of:

- **Invariant:** a mandatory constraint inherited from normative repository sources.
- **Decision:** an accepted choice with owner, date, rationale, consequences, and revisit trigger.
- **Hypothesis:** an unproven belief with an experiment and disconfirmation criterion.
- **Evidence:** a traceable internal result or external source, including its limitations.
- **Proposal:** a recommended choice that has not received decision authority.
- **Open question:** missing information that must fail closed if it affects a live workflow.

“Reasoning” in this suite means concise decision rationale, evidence, alternatives, and consequences. It never means storing private model chain-of-thought.

## Non-negotiable company principles

- Models may interpret, classify, propose, summarize, and draft. Deterministic services authorize and commit money, policy, SLA, permissions, order state, and external effects.
- Every external effect travels through typed tools, policy checks, approval where required, atomic domain-event/audit/outbox persistence, and the sole sender.
- One bounded concierge is the default customer-path agent. Multi-agent execution requires measured superiority on an independent, parallelizable task.
- Context is a scarce, versioned input. Provenance, tenant, policy version, and permission scope travel with it.
- Public automation, a new vertical, a new provider, shared tenancy, or a stronger autonomy level is a capability release, not merely a software deploy.
- The company sells verified operational outcomes, not anthropomorphic autonomy.

## Review and change protocol

The suite should receive a quarterly company review and an event-driven review when a law, provider policy, model family, channel contract, vertical, tenancy model, or release posture materially changes.

A material change requires:

1. Update the research or internal evidence.
2. Record the decision or hypothesis change in Document 12.
3. Update affected risks and metrics.
4. If implementation behavior changes, update the normative contract/ADR through its own approval process.
5. Re-run the applicable repository verification and release gates.

The delivery queue remains authoritative for implementation status. This directory must never be used to mark a delivery item, legal review, capability gate, or launch gate complete.
