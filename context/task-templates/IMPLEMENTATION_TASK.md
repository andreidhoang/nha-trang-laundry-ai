# Implementation task packet

```text
Task ID: TASK-<domain>-<sequence>
Goal: [one bounded outcome]
Domain(s): [keys from context/CONTEXT_MAP.yaml]
Stage: [M0.5/M1/M2/M3/M4]
Risk: [LOW/MEDIUM/HIGH]

Authoritative context:
- [assembled sources from scripts/assemble_context.py]

Constraints:
- [applicable invariant IDs/text]
- [interfaces and files that must remain compatible]
- [explicitly prohibited changes]

Done when:
- [observable behavior]
- [unit/property/integration/negative tests]
- [commands to run]
- [migration and rollback notes, if applicable]

Evidence produced:
- [test report / fixture / trace / ADR / runbook]
```

Use one packet per reviewable vertical slice. Missing business policy is not an invitation to infer it:
record or reference a decision ID and implement the stated fail-closed behavior.

