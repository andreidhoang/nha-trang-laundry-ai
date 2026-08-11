# Evaluation, Observability, and Improvement

**Status:** Strategic quality system; `specs/evals/` and release manifests remain normative  
**Proposed owner:** AI Quality

## Principle

Agent quality is demonstrated by repeatable environment outcomes under the complete harness, not by an attractive transcript or a single model score.

Evaluation must cover the system that actually runs: model, prompt, context assembler, tools, policy facade, state machine, runtime configuration, provider behavior, and recovery path.

## Quality model

Every workflow is assessed across six dimensions:

1. **Outcome correctness:** the intended valid end state was reached.
2. **Process compliance:** required tools, approvals, and state transitions were used.
3. **Safety and authorization:** forbidden effects and data access did not occur.
4. **Consistency:** repeated trials remain within the accepted result envelope.
5. **Operational performance:** latency, reliability, tokens, and cost are viable.
6. **Human usability:** operators can understand, correct, and complete exceptions.

A composite score cannot hide a critical safety failure. Release gates use hard blockers plus dimension-specific thresholds.

## Evaluation stack

| Layer | Purpose | Typical mechanism |
| --- | --- | --- |
| Schema/contract | Reject invalid boundaries | Type/schema/property tests |
| Deterministic domain | Prove business invariants | Unit/state-machine/transaction tests |
| Tool and policy | Prove capability and authorization | Positive/negative/injection/replay tests |
| Scenario | Test end-to-end workflow outcomes | Production-like isolated environment |
| Adversarial | Exercise abuse and ambiguity | Red-team and generated cases, human curated |
| Shadow | Compare against real eligible traffic without effects | Paired outcomes and trace review |
| Canary/live | Validate bounded production behavior | Progressive rollout with guardrails |
| Business outcome | Prove customer value | Baseline/control/time-series measures |

## Evaluation dataset

Each workflow suite should contain:

- Happy-path representative cases.
- Common ambiguity and missing-data cases.
- Long-tail operational exceptions.
- Boundary values for price/catalog/state inputs, evaluated by deterministic code.
- Permission and cross-tenant denial cases.
- Consent, STOP, suppression, and quiet-hour cases.
- Prompt-injection and malicious-document cases.
- Stale/conflicting context and identifier-collision cases.
- Provider timeout, malformed output, retry, crash, and duplicate-delivery cases.
- Previously observed regressions and incidents.
- Vietnamese language variation, abbreviations, code-switching, and channel noise.

Every case declares initial state, authorized capabilities, hidden ground truth, expected terminal states, forbidden events, grader, repeat count, and maximum budgets.

Dataset partitions prevent tuning against the release set:

- Development set: visible and used during iteration.
- Regression set: visible, append-only from known failures.
- Release holdout: access-controlled and versioned.
- Live shadow set: sampled under privacy and eligibility policy.

Synthetic cases broaden coverage but do not replace real, consented, curated workflow evidence.

## Graders

Use the least subjective reliable grader:

1. Deterministic database/state assertions.
2. Schema and event-sequence assertions.
3. Policy/permission oracle.
4. Reference-code calculation.
5. Human domain rubric with blinded review.
6. Model grader for semantic qualities, calibrated against humans.

Model graders may assess relevance, groundedness, or communication quality. They may not be the sole judge of authorization, money, policy, permission, state, or whether an external effect occurred.

## Repeated trials and consistency

Agent behavior is stochastic. Report distributions, not only means:

- Success rate and confidence interval.
- `pass@k`: at least one of k trials succeeds, useful for assisted exploration.
- `pass^k`: all k trials succeed, more relevant to consistency-sensitive execution.
- Critical violation count and upper confidence bound.
- Outcome variance, tool-path variance, and escalation variance.
- p50/p95 latency, tokens, and cost.

Release thresholds should favor `pass^k` and zero critical violations for customer effects. Multiple trials must use controlled seeds/configuration where supported and record unavoidable provider variance.

## Trace and observability contract

Capture structured events rather than indiscriminate prompt logging. A trace links:

- Tenant-safe work, run, attempt, correlation, and experiment identifiers.
- Model/provider/API, prompt, harness, tool-registry, policy, data, and configuration versions.
- Route and budgets.
- Redacted context manifest and provenance references.
- Model-call timings, token counts, structured-output validity, and safe error metadata.
- Tool proposals/results and policy decisions.
- Approval and domain-event links.
- Outbox/delivery result and reconciliation state.
- Final outcome, correction, escalation, and human review.

Prompts, responses, and logs may contain sensitive data. Default telemetry is metadata and redacted references; content capture requires purpose, access, retention, and provider review.

Use open telemetry standards where practical, but keep the internal event schema stable and exporter-independent.

## Error taxonomy

Every failure should be labeled at its earliest controllable cause:

- `INTENT_OR_EXTRACTION`
- `MISSING_OR_WRONG_CONTEXT`
- `SOURCE_DATA_QUALITY`
- `POLICY_OR_CONTRACT_GAP`
- `TOOL_SELECTION`
- `TOOL_IMPLEMENTATION`
- `AUTHORIZATION_OR_TENANCY`
- `MODEL_REASONING_OR_FORMAT`
- `RUNTIME_OR_BUDGET`
- `PROVIDER_OR_NETWORK`
- `DOMAIN_OR_TRANSACTION`
- `DELIVERY_OR_INTEGRATION`
- `OPERATOR_OR_UX`
- `EVAL_OR_GRADER_DEFECT`

This prevents every incident from becoming a prompt-tuning exercise.

## Improvement loop

1. Observe a production, shadow, or eval failure.
2. Triage severity and contain any active effect path.
3. Preserve redacted evidence and determine the earliest cause.
4. Add a minimal regression case before the fix when practical.
5. Choose the smallest correct layer: data, policy, tool, context, UI, prompt, model, or process.
6. Run affected and global safety suites over repeated trials.
7. Inspect representative successes and failures manually.
8. Shadow/canary under an experiment identifier.
9. Promote or rollback from predefined thresholds.
10. Publish the outcome and update risk/decision records.

## Model and harness promotion gate

A candidate cannot replace the active configuration unless it has:

- Exact version pins and a reproducible evaluation manifest.
- Contract and authorization suite parity.
- No new critical safety violation.
- Workflow outcome performance meeting non-inferiority or declared improvement thresholds.
- Reviewed changes in escalation, correction, latency, tokens, and cost.
- Provider privacy/security eligibility for the intended data class.
- Shadow or canary evidence appropriate to risk.
- A tested rollback target.
- Signed release evidence from the designated authority.

A model benchmark score or vendor announcement is not a release artifact.

## Experiment design

An experiment declares one primary hypothesis, unit of randomization, eligible population, guardrails, sample/decision method, maximum duration, stopping rules, and rollback. Do not change model, prompt, tools, context, and UI simultaneously when causal learning matters.

Customer-facing A/B tests must respect tenant contracts, user expectations, and safety. High-materiality workflows use shadow or paired review rather than exposing users to an inferior arm.

## Evaluation governance

- Product owns outcome semantics.
- Domain owners own valid policy and edge cases.
- Engineering owns deterministic and integration oracles.
- AI Quality owns dataset integrity, harness reproducibility, and reporting.
- Security owns abuse, privacy, and authorization cases.
- Release authority accepts residual risk; the implementation team cannot self-waive a blocker.

Release reports include failures and limitations, not only aggregate wins. Holdout access and labels are separated from day-to-day tuning where feasible.

## Frontier engineering lessons adopted

- Anthropic: treat the model plus harness as the evaluated system; use production-like environments, repeated trials, mixed graders, trace inspection, and eval-driven development.
- NVIDIA: emit effective configuration, intermediate steps, model use, latency, tokens, errors, and profiling through an observable, model/framework-neutral layer.
- Google: view simulation, live evaluation, observability, and optimization as a lifecycle, not a one-time launch test.
- Sierra: use gradual rollout and experiment design for customer experiences.
- Tau-bench research: plan for weak multi-turn consistency even when single-turn performance appears strong.

These are design inputs, not endorsements or evidence that this repository already implements every capability.
