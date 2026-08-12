# TASK-agent-pipeline-001 — assemble the runtime into a running pipeline

**Goal:** connect the bounded Responses runtime to the durable agent queue so a job can actually
execute end to end. Today the runtime is complete and unreachable.

**Domains:** `runtime_architecture`, `agent_tools`, `orders_audit`

**Stable work item:** `AGENT-PIPELINE-001`

**Stage:** M4A
**Risk:** MEDIUM — no new authority is created; existing components are wired together.

## Why this exists

The 2026-08-12 readiness assessment found `responses_runtime.py` to be an orphan. It is imported in
exactly one place — `apps/worker/src/nha_trang_laundry_worker/__init__.py` — to re-export it.
`AgentCycle` in `host.py` is a bare `Callable` type alias defaulting to `None`, so the supervisor
accepts a hole where the agent pipeline should be. Nothing constructs a runtime, binds a context
loader, opens a bridge session, or drains the agent queue.

`AGENT-002` cannot produce provider-backed evidence until this exists, and `AGENT-002` carries the
G1 agent evidence. This is therefore on the critical path and is **safe local work available now**.

## Required design

Compose existing parts; write no new authority:

```text
DurableAgentRunWorker claims a job under lease
  -> BoundResponsesContextLoader loads the pinned context packet
  -> AgentToolBridgeSession opens, scoped to the run's binding
  -> BoundedResponsesRuntime executes the FSM (Figure 2 of the harness teardown)
  -> terminal outcome persisted as structured redacted evidence
  -> bridge revoked, budget settled, lease released
```

The concrete deliverable is an `AgentCycle` implementation injected into `WorkerSupervisor`, plus the
construction code that builds it from settings.

## Constraints

- **Use the deterministic transport.** This item makes no provider call and needs no credential.
  The real transport arrives in `PROVIDER-TRANSPORT-001`.
- Do not modify the FSM, the bridge, the tool contract, budgets or the ledger.
- `worker_agent_queue_enabled` and `feature_agent_runtime_enabled` stay `false` by default. Wiring a
  pipeline is not enabling it.
- A crashed or interrupted cycle must release its lease and leave no partial effect. Recovery uses
  the existing worker lease and write-ahead machinery.
- Every capability stays `NOT_AUTHORIZED`.

## Required tests

- a queued job runs to `DRAFT` with the deterministic transport and persists redacted evidence;
- a job that exhausts model, tool, token or deadline budget lands `REQUIRE_HUMAN` with the correct
  terminal code;
- the bridge is revoked on every terminal path, including exceptions;
- worker crash mid-cycle releases the lease and does not double-claim or double-effect;
- two supervisors racing the same job claim it exactly once;
- with the queue flag `false`, no job is claimed at all;
- no credential, prompt content, chain-of-thought or raw payload appears in logs or evidence.

## Done when

- a job flows from queue claim to persisted evidence with no manual intervention;
- the full gate battery passes with no required skips;
- flags remain closed and `scripts/report_delivery_status.py` still reports every capability
  `NOT_AUTHORIZED`;
- rollback is setting the queue flag `false` — the supervisor returns to accepting a `None` cycle and
  the system to degraded mode.
