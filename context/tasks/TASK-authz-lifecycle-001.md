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
- Every staff session requires MFA, whatever the role (`SENSITIVE_MFA_ROLES` is every role).
- Two owners acting on each other at once leave one owner (concurrency test, fails without the lock).

## REALM-POLICY-001 (deploy day)

`--import-realm` skips an existing realm, so the running R1 realm keeps accepting any password until
the policy is set there once. `docs/runbooks/production-deploy-day.md` §2a gives the `kcadm.sh`
command and the read-back that is the evidence. It needs the host and an admin credential, which is
why it is its own item rather than a claim made from a development container.
