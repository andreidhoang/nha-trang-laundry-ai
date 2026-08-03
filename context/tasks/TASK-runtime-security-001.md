# Task packet: RUNTIME-SECURITY-001

```text
Task ID: RUNTIME-SECURITY-001
Goal: Restore fail-closed, pinned OpenClaw runtime verification after newly disclosed dependency advisories.
Domain(s): runtime_architecture, evaluation_release
Stage: PRODUCTION_HARDENING
Risk: HIGH
```

## Authoritative context

- `BUILD_ENGINEERING_SPEC.md`, especially Sections 4, 5, 7, 9, 10, and 13
- `specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md`
- `specs/SECURITY_RELIABILITY_SPEC_V1.md`
- `specs/contracts/supply-chain-evidence-v1.schema.json`
- `specs/contracts/container-scan-evidence-v1.schema.json`
- the pinned Public OpenClaw model/runtime registry and plugin lockfile

## Required behavior

1. Offline verification must execute the workspace-installed, lockfile-pinned OpenClaw binary; a
   global installation must never satisfy the version gate.
2. Audit the complete dependency tree used to verify the runtime, including the pinned OpenClaw
   development dependency, and fail on every high or critical advisory.
3. Apply only registry-published patch-level transitive resolutions compatible with the pinned direct
   dependency ranges. Do not change the evaluated OpenClaw release in this slice.
4. Refresh every affected artifact hash through deterministic repository tooling.
5. Keep the runtime `EVAL_ONLY`, all capabilities `NOT_AUTHORIZED`, and all provider/public/send flags
   disabled.

## Done when

- plugin build/tests and a full high-severity npm audit pass;
- pinned offline runtime verification passes without using a global OpenClaw installation;
- runtime artifacts, contracts, and local evidence hashes are internally consistent;
- the PostgreSQL-backed repository suite and cumulative static gates pass;
- rollback is limited to lockfile/verifier/evidence changes with no external or customer state.
