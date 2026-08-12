# TASK-responses-runtime-001 — bounded custom Responses runtime

**Goal:** implement the smallest provider adapter satisfying `ConstrainedAgentRuntime` without
changing business authority, channel delivery or release authorization.

**Domains:** `runtime_architecture`, `agent_tools`, `evaluation_release`, `privacy_consent`

**Stable work item:** `RESPONSES-RUNTIME-001`

**Stage:** M4A
**Risk:** HIGH
## Required design

Implement a finite state machine behind the existing runtime protocol:

```text
validate immutable job/context/pins
  -> reserve registered budget
  -> call exact Responses model with store=false, strict fixed tools and parallel=false
  -> validate one serial function call
  -> execute only through the contact-bound AgentToolBridgeSession
  -> append typed result and continue inside original absolute budgets
  -> validate draft or deterministic handoff
  -> persist structured evidence, revoke bridge and settle/release budget
```

Use an injectable provider transport. The default test transport is scripted and deterministic; this
work item requires no network access, provider credential, real PII or public channel.

## Constraints

- Preserve the current `ConstrainedAgentRuntime`, Runner job/output, bridge, tool OpenAPI, budget,
  durable ledger and fail-closed policy contracts unless a separately reviewed compatibility change is
  required.
- Model output never supplies contact/customer/order identity, stage, capability, policy, permission,
  approval or send authority.
- Do not add provider built-in tools, generic tool dispatch, browser, shell, filesystem, MCP, channel
  client, plugin loader, business memory, workflow engine or multi-agent scheduling.
- Provider response/session IDs are transport metadata only. Do not store chain-of-thought or treat
  provider state as durable authority.
- A retry, repair, response continuation or tool round trip never resets model/tool/token/cost/deadline
  counters.
- Keep every capability `NOT_AUTHORIZED`; no provider-backed or release evidence may be fabricated from
  fake transport results.

## Required tests

- valid zero-tool draft and valid serial tool round trip;
- unknown tool, unknown field, malformed JSON/result and unsupported output item;
- server-owned identity/authorization substitution;
- multiple/parallel calls, duplicate call ID and per-intent/global budget exhaustion;
- timeout before model, during tool and after tool; cancellation and late call after bridge revocation;
- provider connection/response ambiguity and no hidden retry;
- invalid final output and text attempting to encode an action/send;
- cost reservation settlement/release and structured redacted evidence on every terminal path.

## Done when

- the implementation passes targeted unit/integration/negative tests with scripted transport;
- existing OpenClaw comparator behavior and immutable evidence are unchanged;
- no external call, credential, public ingress, send, deployment or capability authorization occurred;
- full repository quality, contract and context-drift gates pass;
- rollback is removal/disablement of the new adapter route while the stable protocol and comparator stay
  available.
