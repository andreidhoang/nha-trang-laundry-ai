# Executable engineering invariants

Every implementation task must preserve these constraints. CI and review use this file as a concise
checklist; the approved specifications provide the complete detail.

1. PostgreSQL is the transactional source of truth.
2. Money is non-negative integer VND; no float arithmetic.
3. Deterministic code—not an LLM—decides money, policy, state, permissions, capacity, and SLA.
4. Published configurations and historical snapshots are immutable.
5. Mutation, domain event, audit event, and required outbox record are atomic.
6. Inbound provider events are durable and deduplicated before any model call.
7. Only the outbox worker sends; every send has a stable idempotency key.
8. Approval binds an exact rendered-content hash and revision; editing invalidates approval.
9. Server-derived identity, stage, authorization, policy, and contact binding are never model inputs.
10. Suppression is checked deterministically before model use and before marketing send.
11. Missing/stale/unpublished policy or config fails closed.
12. Public automation remains disabled unless a signed, unexpired release manifest authorizes a named
    capability.
13. Never log secrets, raw unnecessary PII, or chain-of-thought.
14. No public agent has direct channel credentials, shell, browser, filesystem, or database access.
15. The public agent runtime is replaceable behind `ConstrainedAgentRuntime`; Python policy/domain code
    remains the business and security authority, and no runtime control plane is Internet-exposed.
16. The exact model, runtime implementation/route, prompt, provider-data behavior and public-cell config
    are versioned release evidence; implicit runtime selection or an unreviewed storage mode fails closed.
17. Official channel adapters persist and deduplicate before AI; they never share credentials with the
    public runtime, and Zalo Personal automation is prohibited.
18. Dashboard numbers, SLA flags, and operational priorities are computed by versioned deterministic
    queries/rules; AI may explain them but cannot originate or mutate them.
19. The custom public runtime is a bounded finite state machine behind `ConstrainedAgentRuntime`; it
    cannot acquire channel routing, plugins, generic tools/memory, business workflow or multi-agent
    responsibilities.
20. Runtime parity compares the same exact model, prompt, context, tools, budgets, datasets, graders,
    isolation and P0 denominator; synthetic evidence is never relabeled as provider-backed evidence.
21. OpenClaw public-path retirement is a separate reversible work item after accepted parity,
    provider-data, security and rollback evidence; immutable historical evidence and Private Owner
    OpenClaw are preserved.
