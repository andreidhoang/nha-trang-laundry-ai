# TASK-tool-backend-001 — give the Tool Facade something behind it

**Goal:** the ten agent tool operations execute against the deterministic domain instead of returning
`TOOL_UNAVAILABLE`, behind the capability flags that already exist.

**Domains:** `agent_tools`, `runtime_architecture`

**Stable work item:** `TOOL-BACKEND-001`

**Stage:** M4B
**Risk:** HIGH — this is the boundary between a model and the business. Every gate that exists here
exists because something on the other side of it is dangerous.

## Why this exists

The Tool Facade is complete as a boundary: ten fixed operations, generated from a hash-pinned
OpenAPI contract, with nine independent validation gates between model output and any mutation.
`AGENT-PIPELINE-001` wired the runtime into the worker and proved a job travels queue to persisted
evidence.

Measured on 2026-08-14, the sole production wiring is:

```python
return AgentFacadeService(UnavailableAgentToolBackend())
```

All ten operations return `TOOL_UNAVAILABLE`. The only implemented backend, `_BoundFactBackend`,
lives in `packages/evals` and serves one operation. So the agent can be run, and it can produce a
draft, but it can never look anything up — which means the drafts a reviewer sees are not the drafts
the system would really produce.

## Constraints

- **Ten operations. Not eleven.** `specs/contracts/agent-tools-v1.openapi.yaml` is one of the files
  hash-pinned by the synthetic evidence bundle. Adding an operation is a contract change and a
  re-pin, and it is not part of this item.
- **Settlement, payment and order closure are never agent tools.** `SETTLEMENT-001` is a staff
  command. If this item appears to need a balance write, it is wrong.
- **The backend computes nothing.** It dispatches to `packages/domain` and to repositories. Money,
  SLA, eligibility and capacity come from the engines, verbatim, including their `REQUIRE_HUMAN` and
  `NOT_SUPPORTED` outcomes.
- **Default remains unavailable.** The unavailable backend stays the default; a real backend is
  selected by explicit configuration. Assembling a backend is not enabling it, exactly as
  `AGENT-PIPELINE-001` established for the pipeline.
- **Do not touch the nine gates.** Bound-path authorisation, required headers, claim scoping, schema
  validation, the policy point. Every one of them stays. A test that fails because a gate refuses is
  a passing gate.
- **Do not edit the `WorkerSettings` validator** that refuses `feature_agent_runtime_enabled=true`.
- **No new capability becomes authorized.** All thirteen stay `NOT_AUTHORIZED`; this item changes
  what a tool call does, not what the system is permitted to do with it.
- Reads are reads. `publicOrderStatusGet` and `catalogResolve` do not mutate. Of the ten, only the
  order-request and draft operations write, and each writes through `commit_material_change`.

## Required tests

- each of the ten operations either executes against the domain or returns a typed unavailability —
  no operation silently succeeds with a fabricated value;
- `quoteEstimate` through the facade equals a direct call to the pricing engine for the same input;
- the bound-path check refuses a call whose claims name a different order request, and the IDOR
  refusal is indistinguishable from an authorization refusal;
- an unresolved-policy input produces the engine's outcome, and the draft that results says so;
- the default wiring is still the unavailable backend, asserted by test;
- with the backend configured, a full pipeline run produces a draft whose tool calls are recorded in
  `agent_tool_calls` with their arguments redacted per the existing rules;
- no persisted artefact contains chain-of-thought, a provider response id, or customer free text.

## Done when

- the ten operations execute against the deterministic domain behind explicit configuration;
- the default remains unavailable and is proven so;
- an end-to-end run in deterministic-degraded mode produces a draft grounded in real domain answers,
  reviewable in the Shadow console;
- rollback is reverting one dependency wiring, which returns every operation to `TOOL_UNAVAILABLE` —
  the current behaviour, not a broken one.
