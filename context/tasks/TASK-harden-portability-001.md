# Task packet: HARDEN-PORTABILITY-001

```text
Task ID: HARDEN-PORTABILITY-001
Goal: Restore strict cross-platform type safety for the delivery and automation mutexes.
Domain(s): platform, runtime_architecture
Stage: PRODUCTION_HARDENING
Risk: HIGH
```

## Authoritative context

- `BUILD_ENGINEERING_SPEC.md`, especially Sections 4, 10, 12, and 13
- `context/AUTOMATION_PROTOCOL.md`
- `context/CONTINUATION_PROTOCOL.md`
- `specs/IMPLEMENTATION_ROADMAP_V1.md`
- existing delivery-state and automation interruption tests

## Files in scope

- `scripts/delivery_state.py`
- `scripts/manage_automation_state.py`
- existing delivery-state and automation-state tests when behavior changes
- this task packet and delivery evidence

## Required behavior

1. Keep the same bounded, fail-closed locking behavior on Windows and POSIX.
2. Resolve platform-specific lock APIs dynamically so strict type checking does not bind a foreign
   platform stub.
3. Preserve non-blocking acquisition, timeout, unlock, journal recovery, and automation fencing.
4. Do not weaken the delivery mutex, modify customer/business state, or broaden automation authority.

## Done when

- delivery-state and automation-state regression tests pass;
- strict mypy passes across the declared repository scope;
- the full PostgreSQL-backed suite and cumulative repository gates pass;
- rollback is a source-only revert with no migration, delivery, capability, or external-state impact.
