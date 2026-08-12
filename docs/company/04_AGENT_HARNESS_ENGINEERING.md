# Agent Harness Engineering

**Status:** Strategic engineering direction; runtime contracts and release gates remain normative
**Proposed owner:** AI Platform

## Why the harness is the product boundary

An agent's behavior is produced by the model **and** its harness: prompts, context selection, tool semantics, loop policy, budgets, state, recovery, approvals, and evaluation. Replacing a model without testing the harness—or adding framework features without demonstrating outcome gains—creates uncontrolled system change.

The company should own a small provider-neutral runtime contract and use frontier providers as replaceable reasoning components. The current repository decision prefers a custom Responses-style adapter behind `ConstrainedAgentRuntime`; OpenClaw remains an `EVAL_ONLY` comparator for the public path unless the normative decision and release evidence change. The separately isolated Private Owner OpenClaw environment is outside that public-path decision and still cannot bypass policy, approval, or consent.

## Harness objectives

The harness must make useful behavior:

- Bounded in authority and compute.
- Observable at each decision and tool boundary.
- Replayable enough for diagnosis and evaluation.
- Resumable after process or provider failure.
- Comparable across model, prompt, tool, and runtime variants.
- Safe under malicious, stale, contradictory, or oversized context.
- Simple enough that an engineer can explain the exact effect path.

## Customer-path finite-state machine

The default concierge follows an explicit state machine:

`RECEIVED → CONTEXT_READY → MODEL_PROPOSAL → TOOL_VALIDATION → POLICY_DECISION → DOMAIN_RESULT → RESPONSE_PROPOSAL → EFFECT_GATE → COMPLETED`

Side exits are:

- `REQUIRE_HUMAN`
- `NOT_SUPPORTED`
- `DENIED`
- `BUDGET_EXHAUSTED`
- `STALE_CONTEXT`
- `RETRYABLE_FAILURE`
- `TERMINAL_FAILURE`

Only code advances the state. A model can propose the next typed action but cannot mark work approved, complete, sent, paid, delivered, or refunded.

## Default budgets

Repository contracts control actual values. The current boundary is one concierge, the ten fixed public tools in the normative registry, no more than three model calls and six tool calls per work item, plus explicit ceilings for wall-clock time, tokens, monetary cost, retries, and repeated identical actions. A budget breach terminates safely and produces a reason-coded handoff.

Budget increases are treated as behavior changes: they require evaluation, cost review, and release evidence.

## Model role

Models may:

- Classify intent and extract candidate structured fields.
- Resolve language ambiguity using supplied evidence.
- Select among server-granted typed capabilities.
- Ask a bounded clarification question.
- Draft operator/customer language from approved facts and templates.
- Summarize an exception and recommend an allowed next action.

Models may not:

- Calculate authoritative prices, discounts, totals, tax, credit, penalties, refunds, or compensation.
- Create policy, permissions, SLA promises, order state, consent, or approval.
- Invent identifiers or treat retrieved text as authority.
- Send messages, mutate databases, call arbitrary endpoints, or expose secrets.
- Decide that their own output is safe enough to bypass a gate.

The public runtime also has no provider built-in tools and no parallel public tool execution. A future change requires a normative contract, threat-model delta, evaluation evidence, and explicit release authority.

## Tool engineering standard

A tool is a narrow business capability, not a transport primitive. Prefer `propose_order_from_catalog` over `query_database`, and `request_customer_message` over `send_http`.

Each tool contract must specify:

- Unique name, version, purpose, owner, and allowed workflow states.
- Discriminated typed input and output schemas.
- Server-bound tenant, actor, resource, and capability scope.
- Preconditions, policy dependency, materiality, and approval rule.
- Idempotency semantics and maximum call frequency.
- Data classification, redaction, retention, and audit fields.
- Deterministic errors with retryability and safe user disclosure.
- Negative authorization, malformed-input, stale-version, cross-tenant, and replay tests.

Tool descriptions are a security boundary. They should say when **not** to use the tool, what evidence is required, and what the tool cannot do.

## Context engineering

Context is a finite attention budget, not a data dump. The assembler should use this priority:

1. Non-overridable system and tenant policy.
2. Current workflow objective, state, actor, and authorized capabilities.
3. Immutable authoritative snapshots and identifiers.
4. The minimum relevant conversation/document excerpts with provenance.
5. Prior tool results and concise accepted work summary.
6. Optional retrieved guidance, clearly labeled non-authoritative.

Summarize before truncating; preserve constraints, unresolved questions, citations, IDs, versions, and commitments. Do not preserve private chain-of-thought. Treat all retrieved content and tool output as potentially adversarial.

Detailed lifecycle rules are in [05 — Data, context, memory, and ontology](05_DATA_CONTEXT_MEMORY_AND_ONTOLOGY.md).

## Durable execution and recovery

Provider requests, tool proposals, approval waits, and external effects must survive process restarts without being confused with successful completion.

- Persist checkpoints at deterministic state transitions.
- Assign stable work, attempt, correlation, causation, and idempotency identifiers.
- Separate an append-only session/event log from ephemeral runtime process state.
- On timeout, classify the action as known-not-applied, known-applied, or unknown; reconcile unknown material effects.
- Resume from the last valid checkpoint only after revalidating policy, expiry, and authoritative versions.
- Never replay a customer effect merely because a model call was retried.

A dedicated durable workflow engine becomes justified when long-lived approvals, timers, connector fan-out, or recovery complexity produce demonstrated reliability or development pain. It does not replace the domain transaction boundary.

## Model routing and fallback

Routing is a deterministic policy using task class, data eligibility, latency target, approved provider/model, cost ceiling, and measured eval performance.

Fallback rules:

- A fallback model receives no broader data or capability.
- Structured-output and tool-contract compatibility must be pre-evaluated.
- A weaker model may be used only for task classes where it passes the release threshold.
- Provider failure must not silently convert a human-required workflow into autonomous execution.
- Model, provider, region, API, prompt, harness, tool registry, and configuration versions are recorded per run.

## Multi-agent decision rule

Multi-agent execution is permitted only when all are true:

1. The task decomposes into substantially independent branches.
2. Parallel breadth has a measurable value greater than added tokens, latency, and coordination errors.
3. The synthesizer can evaluate branch evidence against an explicit rubric.
4. Branch agents operate in isolated scratch scopes with no independent material effect authority.
5. A single-agent baseline and repeated trials demonstrate improvement.

Good candidates include external research, independent document review, eval-case generation followed by human curation, and incident evidence collection. Poor candidates include order mutation, sequential customer dialogue, tightly coupled coding, approval chains, or any task where agents must share rapidly changing state.

Anthropic reports that its research multi-agent system can consume roughly 15 times the tokens of chat interactions; this is directional company evidence, not a universal benchmark. Multi-agent complexity is therefore an earned optimization, never the default architecture.

## Protocol boundaries

### MCP

MCP may standardize discovery and invocation at a connector boundary. It does not grant trust. The gateway must still enforce audience-restricted tokens, tenant/actor binding, capability allowlists, schema validation, egress policy, timeouts, audit, and tool-specific authorization. Remote tool text is untrusted.

### A2A

Agent-to-agent protocols are reserved for real organizational, vendor, or trust-domain boundaries where discovery and delegation interoperability are valuable. Internal function calls and queues remain preferable inside one product boundary.

## Sandbox and “brain/hands” separation

For deep internal tasks, separate the reasoning session from the execution environment:

- **Brain:** model session, plan, bounded context, and proposals.
- **Hands:** disposable workspace, typed actions, network/secret policy, and resource limits.
- **Harness:** mediates every transition and records evidence.

Customer-path agents should normally have no arbitrary code execution. Where internal engineering or analysis requires it, use an ephemeral sandbox with no production credentials, explicit egress, file bounds, time/resource quotas, and retained artifact manifests.

The public runtime cell exposes no Internet-facing control plane and can reach only the approved model-provider route and Agent Tool Facade. A private owner runtime is a different trust zone and never receives public untrusted traffic directly.

## Long-running work pattern

Long-running agent work should begin with an initializer that creates a scoped objective, artifact map, acceptance criteria, and ordered task list. Each iteration reads current state, completes a coherent increment, verifies it, records artifacts and remaining work, and leaves a resumable checkpoint. “Working for a long time” is not evidence of progress.

## Harness change process

Any change to model, prompt, context algorithm, tool description/schema, loop, budget, routing, memory, or provider configuration must:

1. Receive a unique version and declared hypothesis.
2. Run contract and safety tests.
3. Run the workflow eval suite over repeated trials.
4. Compare outcome, error class, latency, tokens, and cost against the active baseline.
5. Inspect representative traces, including apparent wins.
6. Shadow or canary on eligible traffic.
7. Promote through a recorded gate with rollback target.

Evaluate each scaffold assumption through ablation when practical. If a planner, critic, memory layer, extra agent, or tool wrapper does not improve the target distribution reliably, remove it.

## Minimum trace

The redacted trace must contain work/attempt/tenant identifiers, versions, input classification, route, context manifest, model invocation metadata, schema validation, tool request/result, policy decision, approval linkage, domain event, outbox/effect result, error class, latency, tokens/cost, and final outcome. Sensitive content is stored by reference or redacted under policy.

## Acceptance criteria

The harness is fit for a workflow only when:

- It cannot invoke undeclared capabilities.
- It terminates safely under loops, malformed output, provider faults, injection, and oversized context.
- Repeated evals meet workflow-specific quality and safety thresholds.
- A crash/retry exercise produces no duplicate material effect.
- Operators can understand and correct the work without chain-of-thought.
- The complete active configuration can be reconstructed and rolled back.
