# Task packet: RUNTIME-EVIDENCE-PORTABILITY-001

```text
Task ID: RUNTIME-EVIDENCE-PORTABILITY-001
Goal: Make pinned OpenClaw verification and hash-bound non-release evidence reproducible on Windows and POSIX checkouts.
Domain(s): platform, runtime_architecture, evaluation_release
Stage: PRODUCTION_HARDENING
Risk: HIGH
```

## Authoritative context

- `BUILD_ENGINEERING_SPEC.md`, especially Sections 4, 7, 9, 10, and 13
- `context/AUTOMATION_PROTOCOL.md`
- `context/CONTINUATION_PROTOCOL.md`
- `specs/IMPLEMENTATION_ROADMAP_V1.md`
- `specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md`
- `specs/SECURITY_RELIABILITY_SPEC_V1.md`
- `context/tasks/TASK-harden-portability-001.md`
- `context/tasks/TASK-runtime-evidence-001.md`

## Files in scope

- `.gitattributes`
- `pyproject.toml`
- `scripts/verify_agent_runtime.py`
- `scripts/capture_openclaw_offline_evidence.py`
- `packages/evals/tests/test_runtime_verifier.py`
- delivery/context state required to register and evidence this corrective item
- regenerated non-release evidence only when its declared capture command is actually rerun

## Required behavior

1. A Windows test must model the workspace-installed `openclaw.cmd` shim while proving that an
   otherwise valid global PATH shim cannot satisfy the runtime pin.
2. Text artifacts whose raw bytes are SHA-256 bound must materialize with deterministic LF endings on
   Windows and POSIX; Windows command shims remain CRLF-compatible.
3. Existing blocked runtime evidence must remain fail-closed, current, and explicitly non-release.
4. Do not change the evaluated OpenClaw version, waive an advisory, or enable provider, public,
   customer-data, capability, or send authority.

## Done when

- the runtime/evidence regression suite passes natively on Windows;
- Git attribute checks prove deterministic artifact line endings and the correct Windows shim name;
- the full PostgreSQL-backed suite and cumulative repository gates pass without mandatory skips;
- rollback is a source-only revert with no migration, delivery, capability, or external-state impact.
