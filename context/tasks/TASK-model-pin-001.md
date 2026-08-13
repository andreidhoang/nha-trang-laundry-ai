# TASK-model-pin-001 — immutable model release pin

**Goal:** make it impossible to evaluate one model and ship another.

**Domains:** `runtime_architecture`, `evaluation_release`

**Stable work item:** `MODEL-PIN-001`

**Stage:** M4A
**Risk:** MEDIUM — the failure is silent, which is what makes it worth a dedicated item.

## Why this exists

`IMMUTABLE_MODEL_RELEASE_NOT_VERIFIED` is a declared release blocker in
`specs/evals/eval-manifest-v1.yaml`. `G1_INTERNAL_SHADOW_READY` requires "exact runtime model prompt
tool context and public-cell configuration pins". `AGENT-002` cannot produce release-relevant
evidence without this.

The `runtime_architecture` domain states it directly: **pin the evaluated model and explicit runtime
implementation/route; never authorize a moving alias.** A moving alias means the model that passed
the P0 suite and the model serving a customer are different artifacts that happen to share a name.

## Required design

- The pin is an immutable provider release identifier, recorded in the runtime registry and bound
  into the evidence a run produces.
- A moving alias is **rejected**, not warned about. If the configured value is an alias, the runtime
  refuses to start rather than resolving it silently.
- Registry artifact verification cross-checks the pin against the provider/model scope already
  validated by `scripts/verify_contracts.py`, which today validates 16 pinned public-runtime
  artifacts.
- An eval result carries the pin it ran against, so a result produced under a different release is
  identifiable as such rather than being silently comparable.

## Constraints

- Depends on `PROVIDER-TRANSPORT-001`; you cannot pin a release you cannot reach.
- Do not weaken the check to accommodate a provider that only exposes an alias. If an immutable
  identifier is genuinely unavailable, that is a finding for `DEC-006` and the path stays
  `EVAL_ONLY`.
- Changing the pin invalidates the evidence produced under the previous pin. That is correct
  behaviour; do not add a carry-forward.
- No capability moves.

## Required tests

- a configured immutable release is accepted and appears in the run evidence;
- a moving alias is rejected at startup with a typed error;
- an eval result produced under pin A is not counted toward a suite pinned to release B;
- registry artifact verification fails when the pinned release and the registry disagree;
- the pin appears in the terminal evidence without exposing any credential.

## Done when

- an immutable model release is pinned and verified;
- alias rejection is proven by test;
- `IMMUTABLE_MODEL_RELEASE_NOT_VERIFIED` is genuinely no longer applicable rather than suppressed;
- rollback is removing the pin, which returns the path to `EVAL_ONLY` and destroys no evidence.
