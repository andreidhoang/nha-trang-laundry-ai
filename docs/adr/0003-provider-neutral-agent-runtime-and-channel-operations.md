# ADR-0003: Provider-neutral agent runtime and channel-independent operations

**Status:** accepted

**Date:** 2026-08-10
**Supersedes:** the mandatory OpenClaw runtime selection in ADR-0002; ADR-0002 trust-boundary,
business-authority, provider-data, and egress constraints remain binding.

## Context

The Python control plane already exposes a `ConstrainedAgentRuntime` boundary. Channel ingress,
durable state, business rules, approvals, dashboard queries, and channel egress do not require a
specific agent framework. Making OpenClaw mandatory couples public-channel delivery to an additional
runtime, plugin, image, and supply-chain release without adding business authority.

OpenAI Responses supports application-defined function tools with JSON Schema and the normal tool
loop: the application sends tools, receives a function call, executes application code, returns a
function-call result, and obtains a final response. This is sufficient to implement the bounded
Concierge loop behind the existing Python runtime protocol. See the official
[function-calling guide](https://developers.openai.com/api/docs/guides/function-calling).

The product also needs an internal daily-operations dashboard and may later integrate Telegram Bot
API and official Zalo OA. These are control-plane and provider-adapter concerns, not agent-runtime
responsibilities.

### First-principles selection test

The runtime decision starts from required behavior and trust boundaries, not framework feature count.
A candidate is admissible only if it preserves every business, security, data and release invariant.
Among admissible candidates, choose the smallest implementation that minimizes the combined cost of:

- attack surface and delegated authority;
- failure modes and recovery paths;
- dependency, image, plugin and credential supply chain;
- context/state ambiguity and provider-data uncertainty;
- operations, upgrade, audit, evaluation and rollback work;
- application code that the project must own and maintain.

The public workload currently requires one Concierge, ten fixed typed tools, at most three model calls,
at most six tool calls, a twenty-second wall-clock ceiling, and a draft-or-handoff result. It explicitly
does not require browser, shell, filesystem, generic web, runtime plugin installation, channel send,
multi-agent delegation or framework-owned business state. OpenClaw supplies many of those broader
capabilities; they are valuable for trusted owner workflows but are unused or prohibited in this public
path. The Responses function-calling loop is therefore the smaller sufficient mechanism.

This is a workload-specific architectural hypothesis, not a claim that custom code is universally
safer or cheaper. A custom adapter transfers loop correctness, cancellation, idempotency, evidence and
upgrade responsibility to this project. It wins only while it remains small, bounded, typed and better
supported by comparative evidence. If requirements expand to broad personal-assistant channels,
browser/host tools, dynamic plugins, resumable multi-agent orchestration or many independent agent
profiles, OpenClaw or another maintained agent framework must be reconsidered through a new ADR.

## Decision

### 1. Runtime contract, not framework, is authoritative

`ConstrainedAgentRuntime` is the stable runtime boundary. A custom Python Responses adapter is the
preferred production target. It must:

- use only strict function tools derived from `specs/contracts/agent-tools-v1.openapi.yaml`;
- disable provider built-in web, file, code, computer, MCP, and direct-send tools;
- disable parallel tool execution for the public customer path;
- pass all tool calls through the contact-bound `AgentToolBridgeSession`;
- enforce the existing model/tool/token/cost/deadline budgets;
- request non-storage explicitly and remain disabled for real PII until the effective provider
  request and retention behavior are captured and approved;
- store only structured run evidence, never hidden chain-of-thought;
- return only a draft or `REQUIRE_HUMAN`; it has no channel credential or send client.

The adapter is a finite state machine, not a generic agent platform:

```text
VALIDATE_JOB_AND_CONTEXT
  -> RESERVE_BUDGET
  -> REQUEST_MODEL(store=false, strict fixed tools, parallel=false)
  -> if tool call: VALIDATE_CALL -> AGENT_TOOL_BRIDGE -> APPEND_TYPED_RESULT -> REQUEST_MODEL
  -> if final draft: VALIDATE_OUTPUT -> PERSIST_STRUCTURED_EVIDENCE -> RETURN_DRAFT
  -> on invalid call, limit, timeout, cancellation, provider ambiguity or policy denial:
       REVOKE_BRIDGE -> RELEASE/SETTLE_BUDGET -> RETURN_REQUIRE_HUMAN
```

Every transition is bounded and testable. Provider response IDs and reasoning/session artifacts are
transport state only; PostgreSQL records, signed context facts and deterministic policy remain the
authority. The adapter must not grow a plugin loader, generic tool registry, channel router, workflow
engine, business memory store or multi-agent scheduler.

OpenClaw remains an `EVAL_ONLY` comparison and rollback implementation while the custom runtime is
built and evaluated. It is not a production dependency after the custom runtime passes parity,
security, provider-data, and signed release gates. Removing its code and artifacts is a separate,
reversible cleanup after those gates; existing evidence remains immutable historical evidence.

The preference is falsified—and OpenClaw remains or is reconsidered—if the custom adapter cannot meet
the same P0 safety denominator, tool accuracy, timeout/cancellation behavior, provider-data policy,
latency/cost budgets, observability, recovery and rollback requirements with lower operational burden.
No prose decision can substitute for that evidence.

### 2. Preserve the isolated public reasoning cell

Replacing OpenClaw does not collapse the trust boundary. Before public/untrusted traffic, the public
agent runtime runs under a separate service/OS identity and network policy. It may reach only the
approved model endpoint and Agent Tool Facade. The Internet cannot invoke its control endpoint, and
it cannot reach PostgreSQL, channel APIs, the owner workspace, shell, browser, or host administration.

### 3. Channel adapters are independent of AI

Each supported provider has a narrow official adapter:

```text
official provider webhook
  -> authenticate, bound, normalize, deduplicate
  -> deterministic STOP/suppression handling
  -> durable PostgreSQL inbox and audit
  -> asynchronous agent or human processing
```

The adapter acknowledges only after durable acceptance and never waits for a model response. Provider
payloads normalize to one canonical inbound envelope. Provider identity is mapped server-side to an
internal contact; the model never supplies that binding.

Outbound delivery always follows:

```text
approved or capability-authorized content
  -> transactional outbox
  -> provider-specific sender worker
  -> provider attempt/receipt and reconciliation
```

Telegram Bot API is the preferred engineering sandbox candidate because it provides an official
HTTP Bot API and webhook secret header. Official Zalo OA is a later production candidate. Zalo
Personal automation remains prohibited. Neither provider is production-authorized until DEC-005,
provider-specific contracts, consent/security tests, and cumulative release gates pass.

### 4. Dashboard is the daily operations control surface

The Staff PWA reads typed, role-scoped API projections from PostgreSQL. It includes the unified inbox,
approval queue, order board, exception/recovery queue, SLA risk, deterministic revenue/operations
metrics, channel health, AI-quality metrics, and audit timeline.

AI may summarize or explain already-computed dashboard facts through narrow read-only tools. SQL and
domain code compute every number, denominator, freshness timestamp, SLA flag, and priority rule. The
model cannot execute SQL, choose arbitrary identifiers, mutate dashboard state, or turn a narrative
recommendation into an external action.

### 5. Context is compiled per task

The context assembler emits a minimal, versioned packet containing server-signed stage/capability,
contact/conversation binding metadata, scoped verified facts, relevant recent turns, a sanitized
summary, approved public knowledge, tool schemas, budgets, and handoff rules. Raw webhook bodies,
unscoped history, dashboard exports, secrets, internal risk material, and arbitrary database rows are
excluded. Packet schema/version/hash and fact provenance are trace evidence.

## Consequences

- Public channels, dashboard operation, and manual service continue when every model runtime is down.
- The project owns a small bounded tool loop but removes OpenClaw as a mandatory production dependency.
- OpenClaw-specific supply-chain work can be retired only after custom-runtime parity evidence exists;
  it is not silently reclassified as complete.
- Telegram and Zalo share domain behavior but retain separate ingress/egress security contracts,
  credentials, rate limits, receipts, and reconciliation.
- Adding a channel adapter or dashboard screen grants no automation authority.

## Execution and retirement sequence

1. Preserve the existing `ConstrainedAgentRuntime`, `AgentToolBridgeSession`, budgets and durable run
   ledger; do not fork domain or policy behavior by runtime.
2. Implement the minimum Responses adapter with an injectable provider client and deterministic fake
   transport so all state-machine paths can be tested without credentials or network access.
3. Add negative tests for unknown tools/fields, identity substitution, parallel/multiple calls,
   malformed arguments/results, deadline exhaustion, late calls after bridge revocation, cancellation,
   provider ambiguity and attempts to return an unauthorized action.
4. Run custom and OpenClaw candidates against the same immutable prompt, context packets, tool schemas,
   model release, budgets, frozen/rotating/adversarial cases and graders. Do not compare different
   safety envelopes or relabel synthetic evidence as provider-backed evidence.
5. Record a signed runtime-selection result that includes P0 results, non-P0 quality, handoff recall,
   tool-call correctness, p50/p95 latency, tokens/cost, timeouts, recovery, effective provider request,
   deployment inventory and rollback rehearsal.
6. Select the custom adapter only if every zero-tolerance gate passes, no critical regression exists,
   registered latency/cost budgets pass, provider-data controls are approved, and reviewers confirm the
   deployed dependency/control surface is simpler. Otherwise keep OpenClaw `EVAL_ONLY`, repair the
   custom candidate or issue a new ADR.
7. Retire OpenClaw from the public production dependency graph in a separate work item. First remove
   provider/channel routing and deployment references, exercise rollback, then remove runtime code and
   mutable build inputs. Preserve manifests, hashes, evals, audit records and rollback documentation as
   immutable historical evidence. Private Owner OpenClaw is a separate trust cell and is not removed by
   this decision.

## Required verification before activation

- runtime parity across all P0 deterministic, tool, timeout, injection, and fallback cases;
- captured effective Responses request, including storage behavior, plus Security/Privacy approval;
- webhook authentication, replay, duplicate, payload-limit, STOP-race, and attachment tests per provider;
- provider-sender unknown-outcome and duplicate-send reconciliation tests;
- dashboard RBAC/IDOR, metric-query version, freshness, and AI-summary grounding tests;
- signed, unexpired, capability-specific release evidence and exercised rollback.
