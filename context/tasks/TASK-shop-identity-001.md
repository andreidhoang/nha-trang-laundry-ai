# TASK-shop-identity-001 — the identity provider the owner chose, built

**Goal:** stand up Keycloak in Zone C as the production staff issuer, per `DEC-011`.

**Domains:** `platform`, `orders_audit`

**Stable work item:** `SHOP-IDENTITY-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** MEDIUM. A new stateful service and a committed realm; the API's verifier is unchanged.

## Why this exists

`DEC-011` was ratified on 2026-08-18 — Keycloak self-hosted in Zone C, chosen because staff PII
never leaves the owner-controlled host, so no `DEC-006`-pattern residency assessment is owed, and
because WebAuthn/passkey plus TOTP satisfy the no-SMS-only-MFA requirement through configuration
alone.

**Nothing was built.** There is no Keycloak service, no realm export, no client configuration. The
only working issuer in this repository is `scripts/demo_identity_provider.py`, which is
development-only by construction, and `SECURITY_RELIABILITY_SPEC_V1` §5.1 explicitly excludes it:
"Password, passkey or OIDC implementation must come from a maintained auth component."

Until this exists, no real staff member can sign in to a production deployment.

## What must be true when this is done

1. **Keycloak is same-origin with the console.** The API sets `connect-src 'self'` and
   `form-action 'self'` on every response it serves, including `/staff/*`, and `apps/web/README.md`
   records that the browser is not and cannot become an OIDC client of a foreign origin. Keycloak is
   therefore mounted behind the proxy on the console origin, and the flow is the same
   redirect-then-bearer-exchange the demo IdP already proves end to end.
2. **The realm is a committed artifact**, imported at start, with zero users and zero client
   secrets — so the file is reviewable and carries nothing a secret scanner should find. Staff
   accounts are created by the owner afterwards, exactly as `bootstrap_owner.py` already assumes.
3. **The MFA claim is a string.** `_claim_value` (`auth.py:264-270`) walks a dotted path and returns
   the value only `if isinstance(current, str)`. Keycloak's `amr` is a JSON array, so binding to it
   silently yields `mfa_verified = False` and every privileged sign-in dies with a generic 401
   indistinguishable from a bad token. The claim must be one Keycloak emits as a string.
4. **The second factor is required for everyone, and that is forced rather than chosen.** Keycloak
   cannot know who is privileged: roles live only in `staff_role_assignments`, and
   `identity.py:51` records that they are read "without trusting OIDC role claims". So the IdP has
   no basis for a conditional policy. The database remains the enforcement point; the IdP asserts.
5. **No hardcoded-claim mapper.** A mapper emitting a constant would make the system's only MFA
   proof a constant. The negative test that signs in without completing the second factor and
   asserts refusal is what distinguishes a real mapper from a hardcoded one.
6. **The JWKS-outage behaviour is decided and written down**, not discovered. Sessions are opaque
   database rows, so an issuer outage blocks new sign-ins only and leaves signed-in staff working.
7. The admin console and the master realm are unreachable from the shop network.

## Boundary

`oidc_provider_integration` remains an evidence line on `SECURITY-001`, which is G1-scoped and
depends on `AGENT-002`. That item is not re-wired here; it later satisfies the line by reference to
this item's evidence record.

## Acceptance

```
uv run ruff check .
uv run ruff format --check .
uv run mypy apps packages
DATABASE_URL=... uv run pytest --require-postgres-integration
uv run python scripts/verify_contracts.py
uv run python scripts/check_context_drift.py
```
