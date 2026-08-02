# Task packet: RELEASE-BASELINE-001

Goal: reconcile the completed hardening implementation into one immutable, fully verified engineering
baseline without granting release authority.

## Required behavior

- Inspect and preserve every existing worktree change; do not discard or rewrite unrelated work.
- Run the complete PostgreSQL, Python, contract, context, and OpenClaw plugin gates.
- Synchronize machine delivery state and human status projections.
- Confirm every capability remains `NOT_AUTHORIZED` and every external automation flag remains false.
- Record engineering evidence before creating the reviewed baseline commit.

## Done when

All declared checks pass, evidence is recorded with generation CAS, and the resulting commit contains
no credential, raw PII, release signature, provider call, public ingress, or automatic-send authority.

