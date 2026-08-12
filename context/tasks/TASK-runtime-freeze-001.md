# TASK-runtime-freeze-001 — freeze the OpenClaw evidence track

**Goal:** stop spending engineering effort on release-grade evidence for a runtime designated for
retirement, and re-point the dependency graph so downstream work stops routing through a frozen node.

**Domains:** `runtime_architecture`, `evaluation_release`

**Stable work item:** `RUNTIME-FREEZE-001`

**Stage:** M4A
**Risk:** MEDIUM — the risk is not technical. It is that a freeze gets mistaken for a completion.

## Execution note

The four mechanical changes below **ship with the production spec-pack change that authored
ADR-0004**. This item's controller-run job is therefore to *verify* that state and record delivery
evidence with a fresh expected-generation digest — not to redo the edits. If a check below fails,
the corrective edit belongs to this item.

## Required change

Apply ADR-0004 to the machine-readable delivery state:

1. Record the freeze in the `blocking_condition` of `AGENT-001`, `OPENCLAW-REPACK-001` and
   `RUNTIME-SECURITY-001`. Their `status` stays `BLOCKED`.
2. Add `AGENT-002` as the `G1_INTERNAL_SHADOW_READY` agent-evidence carrier, starting with no
   inherited evidence.
3. Re-point `SECURITY-001.depends_on` and `RUNTIME-PARITY-001.depends_on` from `AGENT-001` to
   `AGENT-002`.
4. Rescope `RUNTIME-PARITY-001` from a two-runtime comparison to an absolute P0 bar, replacing
   `same_envelope_runtime_comparison` and `deployed_surface_and_supply_chain_comparison` with
   `degraded_mode_rollback_rehearsal`.

## Constraints

- **Nothing under `evidence/` is modified.** Not a timestamp, not a hash, not a status string.
  The frozen items' history is the record of what was attempted and why it stopped.
- Do not mark any frozen item `COMPLETE`, `PENDING` or `READY`. Freezing is a scheduling decision,
  never a completion claim.
- Do not delete `runtime/openclaw/`, the plugin, the repack manifests or the OpenClaw jobs in
  `release-supply-chain.yml`. Removal is `OPENCLAW-RETIRE-001`, and only after parity passes.
- Do not resolve a decision, authorize a capability, or alter `CAPABILITY_STATUS.yaml`.
- The separately isolated Private Owner OpenClaw trust cell is out of scope.

## Required checks

- `scripts/check_context_drift.py` passes, proving the graph is acyclic and every declared source
  and contract is reachable from the item's context domains;
- `scripts/verify_contracts.py` passes;
- `scripts/report_delivery_status.py` reports every capability `NOT_AUTHORIZED`;
- `git diff --stat` shows no change under `evidence/`;
- `AGENT-002` appears with empty evidence and its dependencies declared.

## Done when

- the queue records `AGENT-002`, the re-pointed dependencies and the rescoped parity item;
- the three frozen items are unchanged apart from their `blocking_condition` text;
- no capability, decision or release state moved;
- rollback is reverting this commit: the graph returns to routing through `AGENT-001`, and no
  evidence was destroyed to make the change.
