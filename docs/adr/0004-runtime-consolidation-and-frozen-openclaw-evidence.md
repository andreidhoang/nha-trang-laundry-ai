# ADR-0004: Runtime consolidation and frozen OpenClaw evidence

**Status:** accepted

**Date:** 2026-08-12
**Amends:** ADR-0003 §1 (OpenClaw retained as `EVAL_ONLY` comparator and rollback implementation) and
its execution sequence steps 4–6. ADR-0003 trust-boundary, business-authority, provider-data, channel
and dashboard decisions remain binding and unchanged. ADR-0002 constraints remain binding.

## Context

ADR-0003 selected a bounded custom Responses adapter as the preferred production runtime and retained
OpenClaw as an `EVAL_ONLY` comparison and rollback implementation until parity evidence exists. That
decision created three obligations which have since been attempted and measured:

1. `RESPONSES-RUNTIME-001` — complete on 2026-08-11. The adapter exists as a bounded finite state
   machine with an injectable transport and full negative-path coverage.
2. `OPENCLAW-REPACK-001` — blocked continuously since 2026-08-03 across Phase A (2026-08-06) and
   Phase B1 (2026-08-09), and still under active CI iteration on 2026-08-12.
3. `RUNTIME-PARITY-001` — never started; it depends on `AGENT-001`, which is itself blocked on
   external provider prerequisites.

The repackage work did succeed technically. Phase A produced a byte-identical derived artifact
(`openclaw-2026.7.1-2-nha-trang-r2.tgz`, SHA-256
`0c4d5d0dcdccde0290932c9baf17c1e371a12d46660ebba32dfa3b878124edab`) with a complete-tree npm audit of
zero critical and zero high findings. The blocker is not correctness and not an unfixable
vulnerability.

The blocker is what shipping it commits the project to. Reaching zero high findings required
replacing four transitive dependencies inside an embedded npm shrinkwrap
(`brace-expansion` 5.0.8→5.0.9, `fast-uri` 3.1.4→3.1.5, `ip-address` 10.2.0→10.3.1,
`undici` 8.5.0→8.9.0), where the last replacement changes upstream's exact pin. Upstream stable
remains `openclaw@2026.7.1-2`. Sustaining that state means **maintaining a derived OpenClaw fork in
perpetuity**: re-deriving replacements on every upstream release and every new advisory, proving
cross-platform byte identity on hosted Windows and Linux runners, and regenerating SBOM, SLSA
provenance and container scan evidence for each derivation.

That is a permanent operational tax on a component that ADR-0003 §1 already designates for
retirement, and which has never been a production candidate for this workload.

### Why the rollback justification does not hold

The strongest argument for keeping OpenClaw is ADR-0003's framing of it as the rollback
implementation. That argument does not survive contact with ADR-0003's own consequences section,
which records that *"public channels, dashboard operation, and manual service continue when every
model runtime is down."*

The system's actual degraded mode is already built, tested and evidenced: deterministic domain
responses, `REQUIRE_HUMAN` handoff, the staff console and manual attestation path. All 32 manifest
cases already execute as `DETERMINISTIC_DEGRADED` results in
`evidence/agent-shadow/local-synthetic-suite-v1.json`.

A second agent runtime that has never produced provider-backed evidence, has never been deployed, and
whose own release blockers are unresolved is not a rollback target. It is a second thing that can
fail. The honest rollback target for a reasoning component is *no reasoning component* plus a human.

### Why `AGENT-001` must not be completed by the custom runtime

`AGENT-001` is titled *"Isolated OpenClaw Concierge, typed Tool Facade, model registry, and Shadow"*.
Its blocked record and its evidence describe OpenClaw-cell prerequisites. Producing Responses-adapter
eval runs and recording them against `AGENT-001` would be precisely the substitution that `AGENTS.md`
§5 forbids: *"A description of prior work, existing code, or a green generic test run is not
completion evidence."*

`G1_INTERNAL_SHADOW_READY` does not have this problem. Its required evidence reads *"exact runtime
model prompt tool context and public-cell configuration pins"* and *"P0 primary fallback and
degraded-path eval pass"* — runtime-agnostic in both cases. G1 can therefore be carried by a
different, honestly-named work item.

## Decision

### 1. Freeze the OpenClaw evidence track as immutable blocked history

`AGENT-001`, `OPENCLAW-REPACK-001` and `RUNTIME-SECURITY-001` are frozen. Their status, blocking
conditions, evidence records and loop-state history are **immutable**. They are not completed, not
rewritten, not unblocked and not deleted.

Freezing is a scheduling decision, not a completion claim. Nothing about it authorizes a capability,
resolves a decision, or converts synthetic results into release evidence.

No further engineering effort is spent on hosted OpenClaw supply-chain evidence: no hosted
reproducibility run, no exact-commit branch push for that purpose, no r2 image SBOM, provenance or
scan. The existing `release-supply-chain.yml` OpenClaw jobs remain in the repository as historical
definition and must not be extended.

### 2. `AGENT-002` becomes the G1 evidence carrier

A new work item `AGENT-002` — *"Custom-runtime Shadow evidence and P0 provider-backed evaluation"* —
carries the `G1_INTERNAL_SHADOW_READY` agent evidence. It requires:

- PRIMARY, fallback and `DETERMINISTIC_DEGRADED` runs of the release eval manifest through the custom
  Responses adapter against a pinned immutable model release;
- the captured effective provider request proving the approved storage behavior;
- exact runtime, prompt, tool-contract, context-packet and public-cell configuration pins;
- structured, redacted run evidence with no chain-of-thought and no raw provider payloads.

`AGENT-002` inherits none of `AGENT-001`'s evidence. It starts empty.

### 3. Re-point the dependency graph

This is the operative change. Without it the queue still routes through a frozen node.

| Work item | Dependency before | Dependency after |
|---|---|---|
| `SECURITY-001` | `AGENT-001` | `AGENT-002` |
| `RUNTIME-PARITY-001` | `RESPONSES-RUNTIME-001`, `AGENT-001` | `AGENT-002` |
| `OPENCLAW-RETIRE-001` | `RUNTIME-PARITY-001` | `RUNTIME-PARITY-001` (unchanged) |

`SECURITY-001`'s remaining dependencies (`OBSERVABILITY-001`, `POLICY-001`, `SUPPLYCHAIN-001`,
`HTTP-SECURITY-001`, `TELEMETRY-001`, `STAGING-001`, `BACKUP-RESTORE-001`) are unchanged.

### 4. Rescope `RUNTIME-PARITY-001` to an absolute bar

`RUNTIME-PARITY-001` no longer compares two candidate runtimes. A comparison against a candidate that
cannot ship produces a number nobody will act on.

It becomes: **the custom adapter meets the absolute P0 safety, tool-accuracy, timeout/cancellation,
latency and cost bar declared in the eval manifest, with a rehearsed rollback to deterministic
degraded mode.** The required evidence changes accordingly:

- removed: `same_envelope_runtime_comparison`, `deployed_surface_and_supply_chain_comparison`;
- retained: `p0_quality_tool_handoff_latency_and_cost_results`, `provider_effective_request_and_data_review`,
  `signed_runtime_selection`, `rollback_rehearsal`;
- added: `degraded_mode_rollback_rehearsal` — a proven transition from provider-backed operation to
  deterministic degraded operation with no in-flight duplicate effect.

### 5. `OPENCLAW-RETIRE-001` is unchanged in intent and stricter in evidence

Retirement still happens only after `RUNTIME-PARITY-001` passes, still removes public routing and
deployment references before code, and still preserves all manifests, hashes, evals, audit records
and rollback documentation as immutable historical evidence. The separately isolated Private Owner
OpenClaw trust cell is out of scope and is not removed.

## Consequences

- The critical path to G1 no longer contains a hosted supply-chain run that requires external
  security review and remote-execution authorization.
- The project owns exactly one production reasoning runtime. There is no second runtime to patch,
  scan, re-derive or reason about during an incident.
- Rollback is simpler and more honest: disable the provider route, serve deterministic degraded
  responses, hand off to staff. This path is already implemented and already tested.
- The counterfactual is preserved. `AGENT-001` and `OPENCLAW-REPACK-001` remain readable as a
  complete record of what was attempted and why it was stopped.
- If the custom adapter fails its absolute P0 bar, OpenClaw is **not** the fallback. The fallback is
  deterministic degraded mode plus a new ADR, because reinstating OpenClaw would reopen every
  supply-chain obligation this decision closes.

## Required verification before this ADR takes effect

- `delivery/WORK_QUEUE.yaml` records `AGENT-002`, the re-pointed dependencies and the rescoped
  `RUNTIME-PARITY-001`, with a task packet for each.
- `scripts/check_context_drift.py` passes, proving the dependency graph is acyclic and every declared
  normative source and contract is reachable from the item's context domains.
- `scripts/report_delivery_status.py` still reports every capability `NOT_AUTHORIZED`.
- No evidence file under `evidence/agent-shadow/` or `evidence/delivery-loop/` is modified.
