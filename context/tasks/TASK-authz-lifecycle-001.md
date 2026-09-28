# TASK-authz-lifecycle-001 — reduce authority, bind a session to one sign-in, declare every route

**Goal:** close the four gaps an adversarial audit of staff authentication and authorization
found, without changing the behaviour of any existing route.

**Domains:** `platform`

**Stable work item:** `AUTHZ-LIFECYCLE-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. One additive migration (`0062`), one new owner-only route, a refactor of the
route gates that keeps every gate's name and role set.

**Design and audit:** `docs/AUTHN_AUTHZ_ARCHITECTURE_V1.md` (findings F1–F4, open items O1–O5).

## Why this exists

- F1: no way to remove one role; a disabled account keeps its OIDC subject, so demotion was
  impossible.
- F2: one ID token could be exchanged for unlimited sessions until it expired.
- F3: route authorization lived in thirteen hand-copied functions; a route on a bare session was
  accepted by omission.
- F4: the Keycloak realm had no password policy.

## Acceptance

The six commands, plus:

- `packages/db/tests/test_identity_lifecycle.py` and `apps/api/tests/test_identity_lifecycle_http.py`
  pass against PostgreSQL.
- `apps/api/tests/test_route_authorization.py` passes; `specs/contracts/internal-api-v1.openapi.yaml`
  carries `x-authorization` for every operation.
- `realm_password_policy_applied_on_the_running_realm`: `--import-realm` skips an existing realm, so
  the policy is set once on the running R1 realm and read back. Until then this evidence is owed.
