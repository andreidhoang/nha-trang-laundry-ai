# TASK-evidence-repin-001 — release the change freeze on the core

**Goal:** make it possible to change the twenty-one files the frozen local evidence bundle pins,
without destroying the record of what that bundle attested.

**Domains:** `evaluation_release`, `runtime_architecture`

**Stable work item:** `EVIDENCE-REPIN-001`

**Stage:** M4A
**Risk:** HIGH — the risk is that releasing a freeze becomes an excuse to rewrite evidence.

## Why this exists

`evidence/agent-shadow/local-synthetic-suite-v1.json` hash-pins twenty-one files:

```
apps/worker/.../agent_runner.py          packages/contracts/.../release_manifest.py
packages/evals/.../graders.py            packages/evals/.../runner.py
scripts/capture_local_agent_evidence.py  scripts/capture_openclaw_offline_evidence.py
scripts/verify_agent_runtime.py          scripts/verify_release_candidate.py
specs/evals/eval-manifest-v1.yaml        specs/evals/assertion-registry-v1.json
specs/evals/fixture-registry-v1.json     specs/contracts/agent-tools-v1.openapi.yaml
runtime/model-registry-v1.yaml           ... and eight more
```

Editing any of them invalidates the bundle and turns the guarded suite red. That is the pinning
working as designed. The problem is the *set*: it is the core of the system, so in practice it is a
change freeze, and it silently blocks six items.

| Item | Must edit |
|---|---|
| `SIGNER-REGISTRY-001` | `release_manifest.py` — verifier already written on `spike/signer-registry-v2-verifier` |
| `EVAL-SYNTHETIC-COMBINATORIAL-001` | the manifest inventory — 669 cases already generated |
| `EVAL-CORPUS-001` | the same inventory |
| `EVAL-LANGUAGE-CORPUS-001` | the same inventory |
| `EVAL-PUBLIC-CORPUS-001` | the same inventory |
| `MODEL-PIN-001` | the runtime registry and the provider-data schema |

Two of those are on the G1 critical path. This is the cheapest unblock available anywhere in the
project: it costs a decision and a re-derivation, not weeks.

## The options, and what each costs

1. **Re-derive the bundle.** `scripts/capture_local_agent_evidence.py` exists precisely to produce
   it, and the bundle is *local synthetic* — all 33 results are `SKIP` on
   `DETERMINISTIC_DEGRADED`, it is explicitly non-release, and it attests no provider run. Re-running
   it against the current tree produces a bundle attesting the same thing about newer files.
   **Cost:** the previous bundle's pinned hashes stop matching the tree, so the old attestation must
   be retained alongside the new one rather than overwritten.
2. **Wait for `AGENT-002`.** It supersedes the 32-case bundle by design. **Cost:** all six items
   queue behind a provider credential and `DEC-006`, which is the longest pole in the project.

The 2026-08-13 tiered-inference assessment
([ADR-0008](../../docs/adr/0008-inference-topology-and-multimodal-scope.md)) added a reason to
prefer option 1 that did not exist when this packet was written. The bundle pins
`runtime/model-registry-v1.yaml` **and** `evidence/provider/openai-data-controls-review-v1.yaml`, so
the provider posture itself is frozen: **any** provider-candidate change — NVIDIA, a different
OpenAI release, anything — terminates at this decision. Option 2's cost is therefore no longer six
items. It is six items plus every future provider question, held behind the one item whose fix is a
decision and a script run.

Option 1 is recommended. Option 2 is defensible only if someone believes the current bundle carries
release weight — and it does not; it declares five of its own release blockers.

## Constraints

- **Retain the superseded bundle.** Write the new one beside it, versioned, and keep the old file.
  The point of the pin is the historical record, and that record survives re-derivation.
- Re-derive, never hand-edit. A bundle whose hashes were adjusted to match the tree is a fabricated
  attestation, and that is the one outcome worse than the freeze.
- Do not relabel any `SKIP` as a pass, and do not touch `PRIMARY_PROVIDER_RESULTS_MISSING` or any
  other declared blocker.
- ADR-0004 freezes `AGENT-001`. Re-deriving its evidence artifact is not resuming that item; if the
  owner disagrees, this item stops and option 2 applies.
- No capability, decision or release state moves.

## Required tests

- the new bundle validates against the current tree and the guarded suite is green;
- the superseded bundle is still present and its own content is unchanged;
- a bundle whose hashes were edited by hand rather than re-derived is rejected;
- every `SKIP` remains a `SKIP` and the declared release blocker list is unchanged.

## Done when

- the six blocked items can edit their files without turning the suite red;
- both bundles exist, and which one is current is unambiguous;
- `scripts/report_delivery_status.py` still reports every capability `NOT_AUTHORIZED`;
- rollback is restoring the previous bundle as current, which re-freezes the core and loses nothing.
