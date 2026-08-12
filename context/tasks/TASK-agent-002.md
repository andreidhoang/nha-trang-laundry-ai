# TASK-agent-002 — custom-runtime Shadow evidence

**Goal:** produce the `G1_INTERNAL_SHADOW_READY` agent evidence through the custom Responses runtime,
honestly and from scratch.

**Domains:** `runtime_architecture`, `agent_tools`, `evaluation_release`, `privacy_consent`

**Stable work item:** `AGENT-002`

**Stage:** M4B
**Risk:** HIGH — this is the item most likely to be completed dishonestly under schedule pressure.

## Why this item exists

`AGENT-001` is titled *"Isolated OpenClaw Concierge…"* and its blocked history is OpenClaw-cell
evidence. Recording Responses-adapter runs against it would be the substitution `AGENTS.md` §5
forbids. ADR-0004 froze it and created `AGENT-002` instead.

`G1_INTERNAL_SHADOW_READY` requires *"exact runtime model prompt tool context and public-cell
configuration pins"* and *"P0 primary fallback and degraded-path eval pass"* — both runtime-agnostic,
which is why a differently-named item can legitimately carry them.

**`AGENT-002` inherits no evidence.** Every result is produced by this item or it does not exist.

## Required evidence

Run the release eval manifest through the custom runtime across all three declared runtime paths:

| Path | Meaning |
|---|---|
| `PRIMARY` | pinned immutable model release, provider-backed |
| `FALLBACK` | declared fallback route, provider-backed |
| `DETERMINISTIC_DEGRADED` | no model; deterministic domain plus handoff |

Plus: the captured effective provider request proving the approved storage behavior, and exact pins
for runtime implementation, model release, prompt, tool contract, context-packet schema and
public-cell configuration.

## Constraints — the honesty rules

- A `DETERMINISTIC_DEGRADED` result is **never** relabelled as `PRIMARY` or as provider-backed. The
  existing 32-case synthetic bundle is non-release evidence and stays that way.
- A grader is never loosened, a case never removed, and a threshold never lowered to reach a pass.
  A failing P0 case is a finding, not an obstacle.
- No real customer PII in any eval run. Fixtures come from `EVAL-CORPUS-001`, which depends on the
  reviewed anonymization in `CORPUS-CONSENT-001`.
- No chain-of-thought, no raw provider payloads, no prompt contents in stored evidence.
- Passing this item authorizes nothing. G1 additionally requires `SECURITY-001` drills, a signed
  manifest under ADR-0006, and the owner's signature.
- If P0 cannot pass, the fallback is **deterministic degraded mode plus a new ADR** — not
  reinstating OpenClaw, which would reopen every supply-chain obligation ADR-0004 closed.

## Required checks

- P0 suite: required pass rate 1, maximum failures 0, on `PRIMARY` and `FALLBACK`;
- degraded path produces correct deterministic answers and correct handoff, with no fabricated
  commitment;
- the pinned model release is immutable; a moving alias is refused at startup;
- effective-request capture is schema-valid against `provider-data-evidence-v1.schema.json`;
- prompt-injection, model-timeout, bound-request IDOR, public-status IDOR, approval-field tamper,
  post-approval edit and kill-switch-in-flight cases pass on the provider-backed path, not only
  synthetically;
- Ruff, format, mypy, contracts, context drift and the PostgreSQL suite pass with no required skips.

## Done when

- all three runtime paths have recorded, schema-valid results produced by this item;
- every result's provenance is traceable to a pinned model release and a pinned prompt;
- `scripts/report_delivery_status.py` still reports every capability `NOT_AUTHORIZED`;
- rollback is disabling the provider route and returning to deterministic degraded mode, which the
  degraded-path evidence has already proven works.
