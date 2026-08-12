# TASK-runtime-parity-001 — same-envelope runtime selection evidence

**Goal:** determine whether the bounded custom Responses adapter is genuinely better for the public
Laundry Concierge than the retained OpenClaw comparator.

**Domains:** `runtime_architecture`, `agent_tools`, `evaluation_release`, `privacy_consent`

**Stable work item:** `RUNTIME-PARITY-001`

**Stage:** M4B
**Risk:** HIGH
## Comparison contract

Both candidates must use the same exact model release/reasoning settings, prompt and context packet
hashes, tool schemas and bridge, budgets/deadlines, frozen/rotating/adversarial datasets, graders,
public-cell network policy and P0 denominator. Any comparison that changes permissions, data, tools or
safety envelope is invalid.

Record P0 safety, non-P0 quality, grounding, high-risk handoff recall, tool selection/schema accuracy,
p50/p95 latency, tokens/cost, timeouts/cancellations, provider ambiguity recovery, effective provider
request/storage behavior, deployed packages/images/endpoints/credentials and rollback rehearsal.

## Constraints

- Synthetic/fake-transport results remain explicitly non-provider evidence.
- Provider-backed execution requires the exact dedicated credential, DEC-006 approval and release
  preconditions; otherwise record a blocker, never a pass.
- Custom wins only when every zero-tolerance gate passes, no critical regression exists, registered
  latency/cost budgets pass, provider-data controls are approved and reviewers confirm a simpler
  deployed control/dependency surface.
- A failed or inconclusive comparison leaves OpenClaw `EVAL_ONLY` and public automation
  `NOT_AUTHORIZED`; repair the candidate or create a new ADR.

## Done when

- a schema-validated, hash-bound runtime-selection record and rollback assessment exist;
- every declared evaluation and operational comparison is reproducible from pinned artifacts;
- Security/Privacy and Product/Runtime owners approve the conclusion without granting an unrelated
  capability release;
- repository contract, eval and context-drift gates pass.
