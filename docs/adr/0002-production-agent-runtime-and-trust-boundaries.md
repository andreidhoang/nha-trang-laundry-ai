# ADR-0002: OpenClaw public agent runtime with a deterministic Python authority

**Status:** accepted in part; mandatory OpenClaw selection superseded by ADR-0003
**Date:** 2026-07-28

ADR-0003 makes `ConstrainedAgentRuntime` authoritative and selects a custom Responses adapter as the
preferred production target. The isolation, Python/PostgreSQL authority, provider-data, no-direct-send,
and release-evidence requirements in this ADR remain binding. OpenClaw is retained only as the current
`EVAL_ONLY` comparison/rollback implementation until retirement gates pass.

## Context

The product needs frontier-language intelligence for Vietnamese customer conversations without making
a probabilistic runtime authoritative for money, policy, permissions, order state, consent, approval,
or external sends. The approved specifications already define separate public and private OpenClaw
cells, but the build plan described OpenClaw as optional and did not pin the production runtime route.
OpenClaw can also expose capabilities—channels, memory, browser, shell, filesystem, nodes, automation,
plugins and direct messaging—that are inappropriate for untrusted customer input.

OpenClaw's supported security model is one trusted-operator boundary per Gateway. The public customer
runtime must therefore be isolated from the owner's personal Gateway and treated as an untrusted
reasoning client of the Business Control Plane.

## Decision

Use this responsibility split for M4 and later:

```text
official channel adapter
  -> durable PostgreSQL inbox
  -> authenticated Python AGENT_RUNNER
  -> isolated Public OpenClaw cell
  -> allowlisted typed Agent Tool Facade
  -> deterministic Python domain and Policy Decision Point
  -> human approval or capability-specific release policy
  -> transactional PostgreSQL outbox
  -> sole OUTBOX_WORKER sender
```

- Public OpenClaw is the primary customer-agent execution runtime.
- The Python modular monolith is the business, authorization, policy and side-effect authority.
- PostgreSQL is the durable source of truth; OpenClaw session state is recoverable and non-authoritative.
- One constrained Concierge runs in the customer hot path. Multi-agent orchestration is excluded there.
- The initial model candidate is `openai/gpt-5.6-terra` through the OpenAI Responses path with an
  explicit OpenClaw runtime selection. An exact model release, reasoning setting, prompt bundle and
  price table must be pinned by the release manifest after evals; no moving alias authorizes release.
- `openai/gpt-5.6-sol` may be evaluated for quality-first private work or a separately measured
  escalation route. A larger model receives no additional permissions.
- Production uses a dedicated provider project/service credential, not an interactive personal
  subscription identity.
- Model unavailability degrades to deterministic templates and human work. No uncertified model is a
  silent fallback.

## Public-cell capability profile

The public cell may access only:

1. the approved model endpoint;
2. the authenticated Agent Tool Facade operations present in
   `specs/contracts/agent-tools-v1.openapi.yaml`.

It has no channel plugin or credential, direct-send operation, owner workspace or memory, raw database
route, shell/exec, browser, generic web fetch, filesystem mutation tool, nodes/canvas/computer control,
cron/session-control mutation, plugin installation, Docker socket or host administration route.

The Gateway and control UI bind to loopback only. A minimal executor on the public VM accepts signed,
expiring, contact-bound jobs through a private pull/queue transport and invokes the Gateway locally.
The Internet-facing reverse proxy exposes only the official channel adapter, never the Gateway.

## Session, context and provider-data contract

- The server binds every run to one contact/conversation; a session key is routing metadata, never
  authorization.
- Public memory is ephemeral and conversation-scoped. Durable customer memory remains in PostgreSQL.
- Context is minimized to signed stage/action data, scoped structured facts, the last relevant turns,
  a sanitized summary and at most two approved public-knowledge chunks.
- Numeric commercial facts, consent, status, delivery, capacity and SLA are obtained from typed tools,
  never vector retrieval.
- Before real PII is sent, an integration test must capture and verify the effective provider request,
  including storage/retention behavior. Provider training, retention, region, deletion, subprocessors
  and incident terms require recorded Security/Privacy approval.
- If the reviewed OpenClaw/provider route cannot enforce the approved storage and retention behavior,
  real-customer model processing remains disabled.
- Traces store structured evidence and versions, never hidden chain-of-thought.

## Performance and quality profile

- Apply deterministic routing before model use.
- Start routine public work with the exact evaluated Terra release at low reasoning effort.
- Prefer one bounded agent run; normal ceilings remain two model steps, three absolute model calls and
  six tool calls.
- Preserve the 15-second p95 persisted-draft target and 20-second hard deadline.
- Evaluate Sol, alternate reasoning levels, prompt caching and any utility model only against the same
  representative and P0 suites. Latency or cost improvements count only when quality gates still pass.
- Programmatic or parallel tool execution is disabled for customer side effects unless a later ADR and
  capability-specific eval prove a bounded read-only use case.

## Release and upgrade rules

OpenClaw, model, provider route, prompt, plugin inventory, tool schema and public-cell configuration are
versioned release artifacts. Any change requires contract validation, full offline and adversarial
evals, security audit, data-retention verification, canary, rollback target and signed unexpired gate
manifest. Missing or stale evidence keeps the capability disabled.

The current personal OpenClaw installation is a private-owner environment and is not a public-cell
baseline. Public production is built from a pinned, scanned, reproducible Linux image on a separate
VM/VPS and OS identity.

## Consequences

- The project leverages OpenClaw's agent loop, provider integration, sessions and tool orchestration
  without delegating business authority or channel delivery.
- Business operation continues when OpenClaw or the model is unavailable.
- Public-agent compromise has a bounded blast radius and an independent credential-revocation path.
- Some convenient OpenClaw built-ins are intentionally unavailable in the public cell; owner-only
  workflows may use a broader reviewed profile in the separate private environment.
