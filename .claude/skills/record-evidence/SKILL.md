---
name: record-evidence
description: Record delivery evidence and mark a work item complete, or record a blocker — with the adversarial audit that must precede it. Use before any completion claim, gate assertion, or capability status change.
---

# Record evidence

The central risk in this repository is not a bug. It is a **false completion** — a synthetic result
relabelled as provider-backed, a check weakened to pass, a capability marked authorized without a
signed manifest. The architecture makes customer harm structurally hard; a fabricated evidence record
routes around all of it.

So recording is two steps by two different agents, and never one.

## Step 1 — produce the claim, record nothing

State, for the item:

- Which `required_evidence` entries you assert are satisfied, and **by which artifact** — a file path,
  a test name, or command output. A sentence in a document is not an artifact.
- Which are **not** satisfied, and why.
- Which of your checks are **synthetic**, by name. `status: SKIP` is a `SKIP`.
  `runtime_path: DETERMINISTIC_DEGRADED` is not provider-backed, at any count.

## Step 2 — have it audited in a fresh context

Hand the claim to the **`evidence-auditor`** subagent. Not to yourself. An agent that has spent forty
tool calls building something is the worst available judge of whether it works; the same model with
none of that investment is a good one.

The auditor opens every artifact, re-runs every command, diffs the tests for weakening, and reports
`PROVEN` / `ASSERTED ONLY` / `CONTRADICTED`. Record only what survives, and only if what survives
covers `required_evidence`.

## Step 3 — record, with a fresh generation

The digest must be fetched now, not reused from earlier in the session. A stale digest is how two
sessions overwrite each other's queue state.

```bash
uv run python scripts/run_delivery_loop.py --format controller-json    # take the generation
uv run python scripts/record_delivery_evidence.py --expected-generation <SHA-256> ...
```

Then re-sync and verify:

```bash
uv run python scripts/check_context_drift.py
uv run python scripts/report_delivery_status.py
```

## Recording a blocker instead

Same mechanism, fresh digest. Name the exact external or policy condition — not "needs more work."
Unblocking also needs a fresh digest, returns the item to `PENDING`, and never bypasses an unresolved
decision blocker.

Then continue an independent dependency-complete item. A blocked item does not stop unrelated work.

## The boundary that must not blur

**Engineering evidence** proves a local work item met its declared checks.

**Release evidence** satisfies `delivery/GATE_REGISTRY.yaml` plus the release-gate JSON Schema, and is
what a capability authorization spends.

Neither substitutes for the other. Do not mark a gate passed, a capability `AUTHORIZED`, or a release
manifest valid — those need a signature this repository does not hold. Every capability in
`delivery/CAPABILITY_STATUS.yaml` stays `NOT_AUTHORIZED`, and completing an item never changes that.
