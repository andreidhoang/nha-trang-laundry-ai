# Metrics and Experimentation

**Status:** Strategic metric contract; numeric gates require owner approval and machine-readable publication  
**Proposed owners:** Product, Data, AI Quality, Finance, and SRE

## Measurement principle

Optimize verified customer outcomes under safety, reliability, adoption, and economic constraints. Conversation volume, generated tokens, tool calls, seats, and demos are activity—not success.

## North Star

**Verified workflow outcomes completed without material correction per active customer per measurement period.**

An outcome counts only when:

- The workflow's deterministic completion condition is met.
- Required authoritative state/effect evidence exists.
- No forbidden event occurred.
- Any required approval is valid and linked.
- The correction window defined by the workflow has elapsed or is accounted for.
- Duplicate/reversed outcomes are excluded through deterministic metering.

The metric is reported with total eligible workflows, human involvement, correction, and customer cohort; otherwise automation can appear to improve while hard cases are avoided.

## Metric hierarchy

### Customer value

| Metric | Definition | Why it matters |
| --- | --- | --- |
| Workflow cycle-time change | Median/p95 elapsed time versus pre-pilot baseline for comparable eligible cases | Direct operational speed |
| Manual touches avoided | Baseline valid human handoffs minus observed valid handoffs | Coordination burden |
| Material error/correction change | Comparable corrections per eligible outcome versus baseline | Quality/business risk |
| Cash/service delay change | Workflow-specific authoritative duration or aging reduction | Economic/service outcome |
| Verified outcome completion rate | Verified outcomes / eligible workflows | End-to-end value |

Business metrics are computed from deterministic data pipelines, never by an LLM.

### Activation and adoption

| Metric | Definition |
| --- | --- |
| Time to first verified outcome | Contract/signoff to first valid workflow outcome |
| Workflow activation | Tenant completed configured prerequisites and a threshold number of real eligible workflows |
| Weekly active workflow users | Distinct intended-role users taking meaningful workflow action |
| 30/90-day workflow retention | Activated tenants still completing target workflow at the defined period |
| Champion health | Named champion active, reviews attended, unresolved policy/data actions aging |
| Workaround rate | Eligible cases completed outside the product or duplicated manually |

Logins and messages are diagnostic only; they do not substitute for retained workflow use.

### Agent and workflow quality

| Metric | Definition |
| --- | --- |
| Environment outcome success | Trials reaching an allowed expected terminal state |
| `pass^k` | Fraction of cases where all k repeated trials pass |
| Material correction rate | Outcomes requiring a correction above workflow materiality threshold |
| Escalation precision | Human escalations that truly required human/policy resolution / all escalations |
| Unsafe miss rate | Cases requiring denial/human handling that the runtime attempted to advance |
| Context evidence recall | Required evidence included / required evidence for evaluated cases |
| Tool-path validity | Runs using only allowed tools/transitions with valid inputs |
| Grounded communication | Human/calibrated grader score for claims supported by supplied authoritative evidence |

Report distributions by workflow, language/noise class, model/harness version, tenant cohort, and risk class.

### Safety and governance guardrails

These are hard release/incident measures, not optimization tradeoffs:

- Unauthorized material external effects: zero tolerated.
- Cross-tenant disclosure/effect: zero tolerated.
- Model-originated authoritative money/policy/permission/SLA/state decisions: zero tolerated.
- Consent/suppression bypass: zero tolerated.
- Direct-send or undeclared capability path: zero tolerated.
- Critical unresolved effect older than workflow objective: threshold set by risk owner.
- Runs missing complete version/evidence manifest: zero for released capability.
- Required approval bypass or stale approval use: zero tolerated.

A zero observed count is not proof of zero risk; report exposure and statistical confidence where relevant.

### Reliability

- Durable inbound acceptance availability and latency.
- Work completion latency by direct/shallow/human path.
- Queue/outbox age and depth.
- Duplicate-effect and reconciliation rates.
- Provider/connector error and circuit-breaker rates.
- Crash recovery and checkpoint-resume success.
- Backup restore success and measured RPO/RTO.
- Change failure and rollback time by behavioral configuration.

Normative SLOs are approved only after baseline and operational rehearsal.

### Economics

| Metric | Formula/interpretation |
| --- | --- |
| Variable AI cost/outcome | Eligible model + runtime cost / verified outcomes |
| Human review minutes/100 outcomes | Direct review labor normalized by volume |
| Direct support cost/tenant | Attributed support labor and tooling per period |
| Contribution margin | Revenue minus model, channel, compute, review, direct support, and customer-specific operations |
| Onboarding payback | Onboarding cost divided by post-variable-margin monthly contribution |
| Implementation reuse | Reusable pack/config/connector/eval work / total deployment work |
| Net revenue retention | Cohort recurring revenue after expansion, contraction, and churn |

Finance owns revenue and cost definitions; model-generated estimates are not accounting evidence.

## Proposed stage-gate logic

Numeric thresholds should be set from workflow risk and baseline. The following logic is mandatory even before numbers are approved:

### Shadow to approval-led lighthouse

- All hard safety guardrails pass.
- Repeated-trial outcome and consistency thresholds pass.
- All material exceptions terminate in a valid human/deny state.
- Trace/version completeness and rollback exercise pass.
- Customer baseline, champion, policy, and privacy conditions are complete.

### Lighthouse to broader segment

- Outcome improves against baseline without unacceptable correction or workload shifting.
- 30/90-day workflow retention passes.
- Review/support cost and contribution margin meet approved trajectory.
- No unresolved critical incident or legal/provider condition.
- Onboarding is repeatable across multiple customers.

### First pack to second pack/platform claim

- Shared kernel remains stable.
- Most new work is pack/config/connector/eval rather than forks.
- Cross-pack regressions and upgrades pass.
- Second workflow has independent paid demand evidence.

## Metric contract

Every governing metric requires:

- Unique name and owner.
- Business question and decision it controls.
- Numerator, denominator, unit, eligible population, exclusions, and correction window.
- Authoritative sources, joins, event versions, and late-data behavior.
- Dimensions and privacy classification.
- Refresh schedule and quality checks.
- Baseline and approved threshold.
- Known gaming/failure modes.
- Change/version history.

Dashboards are projections. The underlying event/state evidence and metric version make a result auditable.

## Experiment protocol

Each experiment includes:

1. Decision to be made and one primary hypothesis.
2. Current baseline and expected mechanism.
3. Eligible tenant/workflow population and exclusions.
4. Unit of assignment and contamination risks.
5. Primary metric, guardrails, and diagnostic metrics.
6. Sample/duration method appropriate to volume and variance.
7. Predefined success, non-inferiority, stop, and rollback rules.
8. Exact model/prompt/harness/tool/context/configuration versions.
9. Privacy, customer-contract, and release eligibility.
10. Analysis with uncertainty, missing data, and adverse results.

Use offline paired evaluation first. Use shadow traffic next. Use customer-facing randomization only when risk and contract allow it. Do not expose high-materiality work to an experimental arm merely to increase sample size.

## Model/harness experiment scorecard

Compare candidates on:

- Outcome success and `pass^k`.
- Critical/major violation counts.
- Correct human escalation.
- Context evidence recall/precision.
- Tool calls, loops, malformed outputs, and recovery.
- p50/p95 latency and availability.
- Tokens and variable cost per verified outcome.
- Operator correction/review time.
- Performance by Vietnamese variation and exception stratum.

No single weighted score can compensate for a hard safety violation. Inspect trace samples from wins, losses, and changed paths.

## Data and review cadence

- **Per release:** contract/eval/safety/reliability evidence.
- **Daily in active rollout:** safety, effect integrity, queue/reconciliation, availability.
- **Weekly:** activation, corrections, escalations, review cost, customer champion actions.
- **Monthly:** outcomes, retention, contribution margin, incidents, model/provider/connector drift.
- **Quarterly:** category/ICP, vertical expansion, pricing, platform reuse, risk, regulatory and research refresh.

## Anti-gaming rules

- Report eligible volume and exclusions with every completion rate.
- Count human rework even when it occurs outside the application.
- Deduplicate and account for reversal/correction windows.
- Segment abstentions and escalations; do not improve accuracy by silently refusing valuable cases.
- Attribute model, review, support, and connector costs completely.
- Freeze metric definitions for an experiment; changes create a new version.
- Keep customer outcome claims linked to a reproducible analysis and approved language.

## Initial measurement backlog

Before lighthouse launch, implement or verify:

- Canonical eligible-workflow and verified-outcome events.
- Correction/reversal linkage.
- Human review duration and reason codes.
- Full behavioral version manifest per run.
- Model/token/cost attribution without sensitive content.
- Connector request/receipt/reconciliation evidence.
- Customer baseline import and comparable-cohort rules.
- Tenant-safe metrics and audit export.
- Dashboard quality tests against authoritative queries.

This backlog does not alter the repository delivery queue; accepted work must enter that system through its normal planning and evidence process.
