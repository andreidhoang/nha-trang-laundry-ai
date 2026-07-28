# TASK-m1-002 — Staff identity, RBAC, and audit primitives

**Status:** Local identity-control slice complete; provider integration remains a release-readiness gate.  
**Date:** 2026-07-28  
**Context domains:** `platform`, `orders_audit`, `privacy_consent`

## Requirement and contract

- `BUILD_ENGINEERING_SPEC.md` invariants 5, 9, 11 and 13; §12 Codex operating contract.
- `specs/ENGINEERING_SPEC_V1.md` `FR-IAM-001` through `FR-IAM-006`.
- `specs/SECURITY_RELIABILITY_SPEC_V1.md` §§5–6: named accounts, server-side RBAC, MFA and machine-role separation.
- `specs/contracts/canonical-enums-v1.json`: canonical `ActorRole` values.

## Delivered

- Migration `0003_staff_identity.sql` adds named OIDC-subject staff, database-authoritative role assignments
  and opaque hashed revocable sessions.
- Identity Platform token exchange is unavailable unless issuer, audience, JWKS URL and MFA claim/value are
  present; no endpoint falls back to a trusted default.
- Sessions are `HttpOnly`, `Secure`, `SameSite=Lax`, idle-expire after eight hours and absolute-expire after
  twenty-four hours. Sensitive roles require configured MFA proof.
- Owner-only role/disable operations, self-or-owner session revoke, secure logout and one-time audited owner
  bootstrap CLI are implemented. OIDC claims never grant application roles.
- Owner-authorized named-staff creation is implemented. Repository authorization prevents internal
  callers from bypassing the HTTP role check, and aggregate event versions advance across mutations.
- Sliding idle expiry, absolute expiry, last-active-owner protection, and disable-user revocation are
  covered without destructive test-database cleanup.

## Verification

```text
DATABASE_URL=<local development value> uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run mypy apps packages scripts
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
uv lock --check
```

The current machine evidence is [`delivery-loop/IDENTITY-001.yaml`](delivery-loop/IDENTITY-001.yaml).
It records 13 targeted identity/PostgreSQL tests plus full lint, format, type and context checks. Live
PostgreSQL verifies one-time bootstrap, owner-only mutation, MFA denial, sliding timeout, session revoke,
disable-user invalidation, ordered aggregate versions and the atomic event/audit/outbox path. HTTP tests
verify unconfigured identity returns `503` and non-owner staff/role operations return `403`.

## Remaining release gate and rollback

- Before any real customer/staff data gate, configure a maintained non-production Identity Platform
  tenant and record signed JWKS-token integration, configured MFA claim, recovery, CSRF/session hardening,
  owner bootstrap and revocation evidence under `SECURITY-001`. This does not block deterministic local
  domain work and does not authorize a release.
- `0003` is additive and forward-only. Disable/revoke identities rather than deleting records; repair schema
  defects with a subsequent migration. No production deployment, credential, public channel or release
  authorization was added.
