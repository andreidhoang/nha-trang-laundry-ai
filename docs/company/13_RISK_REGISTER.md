# Company and Platform Risk Register

**Status:** Strategic risk baseline; not a substitute for workflow threat models or legal assessments  
**Proposed owner:** Executive Risk Owner  
**Last reviewed:** 2026-08-10

## Method

Risks are rated qualitatively before controls: **Critical**, **High**, **Medium**, or **Low**. “Residual target” is the posture required before the affected capability advances; it is not a claim that the target has been reached.

Every active risk needs a role owner, leading indicator, prevention, contingency, and a decision/release connection. Owners should replace role labels with accountable named people during strategy approval.

## Register

| ID | Risk and cause | Inherent | Leading indicator | Prevention / reduction | Contingency | Role owner | Residual target |
| --- | --- | --- | --- | --- | --- | --- | --- |
| RSK-001 | **Adoption decays after setup** because the workflow adds work, lacks a champion, or misses immediate value | High | Low weekly use; champion inactive; parallel spreadsheet persists; correction backlog | One painful workflow, named champion, observed baseline, role UX, paid pilot, weekly review | Narrow workflow, retrain, change process, or stop deployment | Product/Customer Outcomes | Medium before expansion |
| RSK-002 | **Consulting drift** makes revenue depend on custom code and manual operations | High | Branches/prompts per customer; rising onboarding/support hours; low reusable work | Pack/config contracts, scoped onboarding, reuse review, margin accounting | Reprice, productize, reject non-repeatable work | CEO/Product | Medium before scale |
| RSK-003 | **Laundry overfitting** creates abstractions that fail outside the reference domain | High | Second workflow requires kernel changes or domain terms leak into kernel | Minimal ontology, pack boundary, second-vertical test before platform claims | Remain focused vertical or redesign interfaces | Principal Engineering/Product | Medium before platform marketing |
| RSK-004 | **Incorrect or unauthorized material effect** from model error, stale state, bypass, or retry | Critical | Policy denials, correction/reversal, unknown effects, alternate send path | Deterministic authority, typed tools, approval, atomic event/audit/outbox, idempotency, negative tests | Kill switch, quarantine, reconcile, audited correction, incident response | Engineering/Security | Low; zero critical violations for release |
| RSK-005 | **Cross-tenant or permission data exposure** through queries, caches, indexes, logs, tools, or support access | Critical | Denied access anomalies; tenantless records; scope mismatch | Dedicated cells first, identity propagation, tenant-bound stores/caches, least privilege, negative tests | Isolate tenant/service, revoke credentials, preserve evidence, legal/customer process | Security/Data | Low before any shared tenancy |
| RSK-006 | **Privacy or regulatory non-compliance in Vietnam** due to evolving law, unclear roles, or excessive processing | Critical | Unknown purpose/retention; missing assessment; provider or law change | Counsel review, data inventory, minimization, assessments, lifecycle controls, release gate | Disable data/effect path, contain, notify/remediate under advice | Privacy/Legal | Low for intended scope |
| RSK-007 | **Provider data-use/retention/region terms are unsuitable or change** | High | Unverified terms, alias/config change, subprocessor notice | Provider assessment, exact config pins, minimization, contractual controls, alternate adapter | Disable real PII, route only eligible data, migrate/return to human path | Security/Procurement | Low before real PII |
| RSK-008 | **Official channel dependency or policy/pricing change** disrupts workflow or economics | High | Deprecation, quota/rate errors, cost spike, policy notice | Canonical envelopes, official API, version monitoring, channel-neutral outbox, cost model | Disable connector, manual alternate, customer notification, migrate | Integrations/GTM | Medium |
| RSK-009 | **Evaluation illusion**: benchmark/eval passes while real outcomes or consistency fail | High | Live correction rises; grader disagreement; high pass@k but low pass^k | Production-like cases, repeated trials, mixed graders, holdout, shadow, trace review | Roll back, return to shadow, repair dataset/grader | AI Quality/Product | Medium before lighthouse, Low for critical safety |
| RSK-010 | **Model/harness/configuration drift** changes behavior without attributable release | High | Unpinned alias, trace missing versions, unexplained distribution shift | Exact manifests, config publication, drift checks, promotion and rollback gates | Freeze route, restore last known manifest, re-evaluate | AI Platform/Release | Low |
| RSK-011 | **Multi-agent/framework complexity** increases cost, latency, security surface, and coordination failure | Medium | Token/cost multiple rises; contradictory branches; unclear owner/effect | One agent default, ablation, single-agent baseline, isolated research-only agents | Remove scaffold, fall back to bounded single loop | AI Platform | Low in customer path |
| RSK-012 | **Poor source data or identity resolution** produces plausible wrong work | High | Mismatch/duplicate rates; operator edits identifiers; stale snapshots | Data ownership, validation, reconciliation, immutable provenance, human resolution | Fail closed, queue cleanup, correct authoritative source | Data/Customer Outcomes | Medium |
| RSK-013 | **Connector partial failure duplicates or loses business effects** | Critical | Aging unknown effects, receipt mismatch, retry storm | Inbox/outbox, idempotency, attempt ledger, reconcile-before-retry, circuit breakers | Quarantine, provider lookup/manual verification, audited correction | SRE/Integrations | Low |
| RSK-014 | **Economics fail** because inference, review, support, or dedicated tenancy scale linearly | High | Negative contribution margin; review minutes/outcome flat; long payback | Per-workflow cost telemetry, smaller models/direct routing, productized onboarding, pricing tests | Reprice, narrow segment/workflow, automate safe layer, stop | Finance/Product | Medium before growth spend |
| RSK-015 | **Security/supply-chain compromise** through dependency, plugin, model, connector, secret, or artifact | Critical | Unverified artifact; leaked secret; dependency alert; unexpected egress | Pin/sign/scan, SBOM/inventory, isolated build, secret manager, egress allowlist, provider review | Revoke/rotate, isolate, rollback, forensic and notification process | Security/Release | Low |
| RSK-016 | **Operator automation bias or approval fatigue** makes human control nominal | High | Near-100% approvals, short review time, repeat reversals, skipped evidence | Materiality-based approval, evidence UI, sampling, role training, automation-bias review | Reduce autonomy, require second review, redesign workflow | Product/Risk | Medium |
| RSK-017 | **Incident or disaster recovery cannot reconstruct effects** | High | Restore untested; audit gaps; unknown outbox state | Backup/restore exercises, causal IDs, immutable evidence, reconciliation/runbooks | Freeze effects, restore isolated, reconcile system/provider/customer records | SRE | Low before contractual SLO |
| RSK-018 | **Market wedge is too crowded or low-value** and incumbents add sufficient AI | High | Low paid conversion; weak measured outcome; losses to incumbent configuration | Workflow-level differentiation, integrations, rapid paid evidence, falsification criteria | Narrow/change vertical or stop thesis investment | CEO/Product/GTM | Medium |
| RSK-019 | **Key-person and policy-knowledge concentration** blocks safe scale | High | One person approves/configures/supports; undocumented exceptions | RACI, policy publication, runbooks, pair ownership, training, access review | Limit capability/tenants, succession and knowledge-transfer plan | CEO/Operations | Medium |
| RSK-020 | **OpenClaw or frontier-tech distraction** consumes effort without customer outcome | Medium | Framework work exceeds selected delivery need; parity evidence absent | Stable runtime contract, public-path `EVAL_ONLY` boundary, same-envelope comparisons, separate Private Owner trust zone, investment review | Stop/retire the public-path integration reversibly while preserving required evidence/private-owner separation; focus on workflow bottleneck | Principal Engineering | Low |
| RSK-021 | **High-risk vertical expansion outruns governance** | Critical | Sales requests healthcare/credit/employment effects; no specialist owner | Vertical risk screen, explicit deferral, legal/domain assurance gate | Decline scope; remain assistive or human-only | Executive Risk/Legal | Low |
| RSK-022 | **Revenue or AI claims create trust/legal exposure** | High | Unqualified “autonomous/compliant/accurate” messaging; vendor figures treated as proof | Evidence-linked claims review, limitation labels, capability truth inventory | Correct claim, notify affected customers, retrain sales | CEO/Legal | Low |

## Top risks for immediate leadership attention

1. RSK-004/013 — material-effect integrity.
2. RSK-005 — tenancy and permission isolation.
3. RSK-006/007 — Vietnam legal and provider-data eligibility.
4. RSK-001/002/014 — adoption, productization, and economics.
5. RSK-009/010 — evaluation and configuration truth.

These priorities reflect severity and near-term dependency, not a claim that other risks are controlled.

## Review triggers

Review the register at least monthly during lighthouse delivery and immediately after:

- A security, privacy, authorization, or material-effect incident.
- A new customer segment, vertical, channel, connector, model/provider, or tenant mode.
- A capability/autonomy increase.
- A material law, provider term, pricing, or API change.
- A failed release/restore exercise or a major adoption/economics deviation.

Closed risks remain in history with evidence; do not delete them. Accepted residual risk requires the designated business authority, not only the engineering owner.
