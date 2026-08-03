# Task packet: RUNTIME-EVIDENCE-001

```text
Task ID: RUNTIME-EVIDENCE-001
Goal: Represent a blocked pinned-runtime verification as current, sanitized, fail-closed evidence.
Domain(s): runtime_architecture, evaluation_release
Stage: PRODUCTION_HARDENING
Risk: HIGH
```

## Authoritative context

- `BUILD_ENGINEERING_SPEC.md`, especially Sections 4, 7, 9, 10, and 13
- `specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md`
- `specs/SECURITY_RELIABILITY_SPEC_V1.md`
- `context/tasks/TASK-runtime-security-001.md`
- the pinned runtime registry, offline verifier, and current non-release evidence contract

## Required behavior

1. The verifier must emit a typed, sanitized result for both successful verification and a known
   high-severity dependency block; a blocked result returns non-zero to direct callers.
2. Evidence capture may persist the known blocked result, but must never convert it into a success or
   release-eligible record.
3. A blocked record must contain current artifact hashes, zero real-customer/public/send authority,
   explicit high/critical counts, and the exact fail-closed blocker code.
4. Malformed output, unknown failures, inconsistent counts, stale hashes, or a false verified claim
   must be rejected.
5. Do not waive, suppress, or reclassify any advisory and do not change the evaluated runtime.

## Done when

- blocked evidence is reproducibly captured from the actual pinned verifier;
- negative tests reject inconsistent verified/blocked results and stale hashes;
- all repository tests and cumulative gates pass while runtime security remains blocked;
- rollback restores the previous evidence code without changing external or customer state.
