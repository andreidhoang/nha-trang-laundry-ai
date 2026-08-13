# TASK-model-route-001 — per-role model pinning so a route is as pinned as its loosest member

**Goal:** make it possible to address more than one model release in a turn without making the
release unattributable.

**Domains:** `runtime_architecture`, `evaluation_release`

**Stable work item:** `MODEL-ROUTE-001`

**Stage:** M4A
**Risk:** MEDIUM technically, HIGH in consequence — the failure is a silent one. A route whose
perception role floats produces evidence that names a release the run did not entirely use.

## Why this exists

`docs/adr/0008-inference-topology-and-multimodal-scope.md` proposes decomposing a turn into a
perception role, an execution role and an escalation role, so each step is served by a model sized
to that step's entropy rather than to the hardest step in the task.

The runtime cannot express that today, in three places:

- `runtime/model-registry-v1.yaml` has a scalar `model:` block — one `provider`, one `api_model_id`,
  one `immutable_release_id`, and `fallback_model_refs: []`.
- `ResponsesRuntimeEvidence` in `apps/worker/src/nha_trang_laundry_worker/responses_runtime.py`
  carries a single `model_id` and a single `immutable_model_release`.
- `limits.max_model_calls: 3` is the entire per-turn budget. Perception plus execution plus one
  escalation consumes it exactly, leaving nothing for a retry.

`MODEL-PIN-001` exists because "a moving alias means the model that passed the P0 suite and the model
serving a customer are different artifacts that happen to share a name." A route makes that failure
easier, not harder: it multiplies the number of places an alias can hide, and it lets a run be
recorded under the release of whichever role happened to be written to the evidence field.

The rule this item enforces is the extension of that one: **a route is only as pinned as its loosest
member.**

## Required design

- Registry schema **v2**, alongside v1 rather than replacing it in place. The `model:` block becomes
  a mapping of role → pin, where a role is `PERCEPTION`, `EXECUTION` or `ESCALATION`, and each pin
  carries its own `provider`, `api_model_id`, `immutable_release_id` and `immutable_release_verified`.
- A single-role registry is a valid route. The status quo — one model, one role — must round-trip
  through v2 unchanged, so this item is provably behaviour-preserving before any second role exists.
- Evidence schema **v2**: per-call attribution. Each model call in a run records the role that
  requested it and the immutable release that served it. The existing scalar fields are derived for
  a single-role route and absent for a multi-role one; they are not reused to name "the main model",
  because there is no such thing.
- Startup refuses an alias **in any role**, with the same typed error `MODEL-PIN-001` uses. A route
  with two pinned roles and one aliased role does not start degraded; it does not start.
- An eval result carries the whole route, not one member. A result produced under route R is not
  counted toward a suite pinned to route R'. Reuse the mechanism `MODEL-PIN-001` builds rather than
  adding a second comparability rule.
- The per-turn call budget stays at 3 until it is raised deliberately, with its own cost-ceiling and
  deadline arithmetic. A perception call is a model call and gets no exemption for being extraction.

## Constraints

- **Blocked on `EVIDENCE-REPIN-001`.** `runtime/model-registry-v1.yaml` is one of the 21 files
  hash-pinned by `evidence/agent-shadow/local-synthetic-suite-v1.json`. Do not edit it, do not add a
  commented candidate, and do not hand-edit the bundle's hashes to match — that would be a fabricated
  attestation, and ADR-0004 freezes this history.
- **Blocked on the ADR-0008 model-role decision.** If it is decided that one turn addresses one
  model, this item is closed as not-required rather than built speculatively.
- Depends on `PROVIDER-TRANSPORT-001` for anything provider-backed; a route cannot be verified
  against releases nobody can reach.
- Do not add a router, a scoring heuristic, or traffic-based model selection. This item makes a route
  *expressible and attributable*. Deciding which role handles a given step is a separate item and
  needs traffic that does not exist.
- No capability moves. Every capability stays `NOT_AUTHORIZED`.
- Changing any role's pin invalidates evidence produced under the previous route. That is correct;
  do not add a carry-forward.

## Required tests

- a v1 registry loads through the v2 path as a single-role route with identical behaviour;
- a two-role route with both roles pinned is accepted, and both releases appear in the run evidence;
- a two-role route with one aliased role is **rejected at startup**, with the same typed error as a
  fully aliased v1 registry;
- terminal evidence attributes each model call to its role, and a run cannot be recorded naming a
  release that served no call in it;
- an eval result produced under route R is not counted toward a suite pinned to route R';
- registry artifact verification fails when any role's pin and the registry disagree;
- no role's credential appears in evidence, a span attribute or a log.

## Done when

- registry v2 and evidence v2 exist, and a single-role route is provably unchanged;
- alias rejection is proven per role, not per registry;
- `IMMUTABLE_MODEL_RELEASE_NOT_VERIFIED` remains correctly applicable — this item does not clear it,
  `MODEL-PIN-001` does;
- rollback is reverting to the v1 schema, which returns the runtime to one model per turn and
  destroys no evidence.
