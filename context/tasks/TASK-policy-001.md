# Task packet: POLICY-001

```text
Task ID: POLICY-001
Goal: Implement a typed, deterministic, fail-closed capability policy decision point.
Domain(s): runtime_architecture, privacy_consent, evaluation_release
Stage: PRODUCTION_HARDENING
Risk: HIGH
```

## Authoritative context

- `BUILD_ENGINEERING_SPEC.md`, especially invariants 3, 9, 10, 11, and Section 9.3
- `specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md`
- `specs/SECURITY_RELIABILITY_SPEC_V1.md`
- `delivery/GATE_REGISTRY.yaml`
- `delivery/CAPABILITY_STATUS.yaml`
- `specs/contracts/capability-status-v1.schema.json`
- current automation and authorization code in `packages/db/`, `apps/public-agent-tools/`, and
  `apps/worker/`

## Files in scope

- `packages/policy/src/nha_trang_laundry_policy/`
- `packages/policy/tests/` (create)
- the smallest existing runtime call sites required to consume the decision
- typed package exports and manifests

Do not implement a second release-authority store, duplicate capability registry, business pricing
rule, or model-selectable permission mechanism.

## Required behavior

The effective result must be deterministic and conjunctive:

```text
ALLOW only when
  all required global controls are explicitly enabled
  AND the exact capability is explicitly enabled
  AND required cumulative stage gates are verified
  AND current server-derived identity/binding is authorized
  AND approval/suppression obligations are satisfied
```

Missing, stale, malformed, unavailable, mismatched, or unverified input returns a typed `DENY` or
`REQUIRE_HUMAN` reason. A flag for one capability can never enable another. Model/tool input cannot
supply or override actor, tenant, contact binding, stage, release authorization, or approval state.

All existing public and automatic capabilities must remain disabled by default.

## Tests first

Add table/property tests for:

- every required term independently missing or false;
- stale and mismatched deployment/release metadata;
- capability substitution and cross-contact/cross-tenant attempts;
- suppression and approval invalidation;
- policy store unavailable/malformed;
- an explicitly allowed synthetic internal-only path.

Add a negative integration test proving the Tool Facade cannot promote a denied operation by adding
extra fields.

## Done when

- all declared acceptance commands pass;
- reason codes are typed, stable, and suitable for audit without PII;
- authorization remains server-derived and fail-closed;
- no release/capability status is changed to `AUTHORIZED`;
- rollback is code/config revert only and cannot accidentally leave automation enabled.
