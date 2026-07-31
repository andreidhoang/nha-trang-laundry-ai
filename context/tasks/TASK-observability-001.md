# Task packet: OBSERVABILITY-001

```text
Task ID: OBSERVABILITY-001
Goal: Add a minimum typed observability layer with correlation propagation and fail-safe redaction.
Domain(s): platform, runtime_architecture, privacy_consent, orders_audit
Stage: PRODUCTION_HARDENING
Risk: HIGH
```

## Authoritative context

- `BUILD_ENGINEERING_SPEC.md`, especially Sections 4, 7, 8, 9, and 13
- `specs/SECURITY_RELIABILITY_SPEC_V1.md`
- `specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md`
- `packages/observability/`
- API, worker, Runner bridge, and Tool Facade boundaries under `apps/`

## Files in scope

- `packages/observability/src/nha_trang_laundry_observability/`
- `packages/observability/tests/` (create)
- the narrow API/worker/Tool Facade integration points required for request/job correlation
- package manifests only when a justified, maintained dependency is necessary

Do not add production telemetry exporters, vendor credentials, customer data, or broad framework
rewrites.

## Required behavior

1. Define typed structured event fields and a stable correlation/trace identifier interface.
2. Propagate correlation across an HTTP boundary and a worker/agent job boundary without accepting
   model-controlled authority fields.
3. Redact or reject secrets, authorization headers, cookies, phone numbers, addresses, raw provider
   payloads, prompt bodies, tool payloads containing PII, and chain-of-thought-like fields.
4. Prevent exception formatting from reintroducing a redacted value.
5. Keep logging failure from changing a domain decision or authorizing a side effect.
6. Default to local structured output; external exporters remain deployment configuration.

## Tests first

Add positive tests for stable event/correlation fields and negative tests that inject:

- bearer tokens, cookies, API keys, Vietnamese phone numbers, and addresses;
- nested mappings/lists and exception strings;
- attacker-controlled field names and oversized values;
- chain-of-thought/reasoning field names.

Assert on the complete serialized output so the original sensitive value cannot survive elsewhere.

## Done when

- declared targeted tests, Ruff, format, mypy, and context validation pass;
- API and worker examples share one correlation identifier;
- no raw PII/secret fixture is committed (use unmistakably synthetic values);
- no capability, model, provider, public ingress, or outbound flag is enabled;
- rollback removes instrumentation without requiring schema or data rollback.
