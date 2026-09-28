# Staff authentication and authorization — architecture, audit and hardening (V1)

**Date:** 2026-09-28 · **Work item:** `AUTHZ-LIFECYCLE-001` · **Normative sources:**
`specs/SECURITY_RELIABILITY_SPEC_V1.md` §5–§6, `docs/DECISION_REQUEST_AUTH_IDP_AND_FRONTEND_FRAMEWORK_2026-08.md`
(DEC-011), OWASP ASVS 4.0.3 V2/V3/V4, NIST SP 800-63B.

This document is the single description of how a staff member proves who they are and what they
may do. It records what already existed (most of it), what an adversarial audit found, what this
item changed, and what is still open with its owner. Machine truth outranks it: the per-route access
record is `specs/contracts/internal-api-v1.openapi.yaml` (`x-authorization`).

---

## 1. Decision: buy authentication, build authorization

| Concern | Choice | Why |
|---|---|---|
| Credentials, MFA, lockout, password storage, recovery | **Keycloak, self-hosted in Zone C** (DEC-011, ratified) | Maintained component (§5.1 mandate). Passkey/TOTP by configuration. Staff PII never leaves the owner's host — no cross-border assessment, no external login dependency. Writing any of this ourselves is the classic way to get it wrong. |
| Session after sign-in | **Our own opaque, DB-backed session** (BFF pattern) | Instantly revocable, carries an authorization version, never a bearer token in the browser (§5.2). An IdP session or a JWT in `localStorage` gives none of those. |
| Roles | **Our database** (`staff_role_assignments`), never token claims | The IdP proves *who*; the business decides *what*. A role claim would make Keycloak admin a path to money and PII. |
| Authorization engine | **In-process, typed** (`RouteGate` + repository checks) — no OPA/Cedar/Casbin/Oso | Six roles, one shop, ~110 operations. An external PDP adds a network hop, a second policy language and a failure mode on every request, and buys nothing a reviewed data structure does not. Revisit only if tenants or ABAC rules multiply. |

## 2. The request path

```
Browser ──(1) Authorization Code + PKCE S256, acr_values=mfa──▶ Keycloak (Zone C)
   │                                                           password + TOTP, brute-force lockout
   │◀─(2) code ── redirect ─────────────────────────────────────┘
   │──(3) code + verifier ─▶ Keycloak token endpoint ─▶ ID token (5 min), held in one const
   │──(4) POST /internal/v1/auth/session  Authorization: Bearer <ID token>, Origin checked
   ▼
API  IdentityPlatformVerifier: RS256 only · JWKS · iss · aud · exp · iat ≤ 300 s old · MFA claim
     IdentityRepository.create_session: subject → ACTIVE staff_users row · roles from DB ·
       MFA required for every session, whatever the role · bound to sha256(header.payload), UNIQUE
     ◀── Set-Cookie staff_session (HttpOnly, Secure, SameSite=Strict) + staff_csrf (double-submit)
Every later request:
     BrowserSecurityMiddleware: exact Origin + CSRF double-submit on unsafe methods, CSP, HSTS
     current_principal: session row by id + secret hash, not revoked, idle ≤ 8 h, absolute ≤ 24 h,
       authorization_version == user's, MFA still satisfied for sensitive roles
     RouteGate (route layer): role ∈ gate.roles AND mfa_verified          → 403 "operation denied"
     Repository (data layer): roles re-read, store membership, object → store → member   → 403/404
```

Two layers on purpose. The route gate is the one a reviewer reads; the repository is the authority,
because it is the only path to the data and a route is a place a check can be forgotten.

## 3. Audit — what held

Verified in code and tests, not assumed:

- Cookies `HttpOnly; Secure; SameSite=Strict`; CSRF double-submit with constant-time compare and exact
  `Origin`; CSP with no `unsafe-inline`; HSTS; trusted hosts; request-size cap.
- Session secret: 256 random bits, stored as SHA-256 (a slow hash buys nothing at that entropy).
- Revocation: per session, per user (disable revokes all), and implicit on any authority change via
  `authorization_version`.
- Sign-in throttle per source; opaque refusals (one body for every 401 and every 403).
- Keycloak realm: public client, PKCE S256 enforced, no implicit/password grants, exact redirect URI,
  `sslRequired: all`, no self-registration, brute-force protection, TOTP required on first sign-in,
  MFA step-up forced by `acr_values=mfa` (the realm setting alone was measured not to hold).
- Store scoping: `OWNER_ADMIN` is *not* implicitly a member of every store.
- The 14 routes gated only by "signed in" were traced to the repository: none returns data to a
  `DRIVER` or `ACCOUNTANT`; the order and Shadow reads enforce `OWNER_ADMIN/OPS_APPROVER/OPERATOR/AUDITOR`
  plus membership.

## 4. Audit — what was wrong, and what this item changed

| # | Finding | Severity | Change |
|---|---|---|---|
| F1 | **No way to remove a role.** Demotion required disabling the account, and a disabled row keeps its unique OIDC subject, so the person could never be re-added with less. Least privilege could not be restored (ASVS V4.1.3, joiner-mover-leaver). | High | `IdentityRepository.revoke_role` + `DELETE /internal/v1/staff/{id}/roles/{role}` (owner only, MFA). Bumps `authorization_version` in the same transaction → every session of that person is refused at its next request. Last-owner guard. Event + audit + outbox atomically. |
| F2 | **ID-token replay.** One ID token could be exchanged for unlimited 24-hour sessions until it expired. A token leaked once (proxy log, extension, crash dump) was a session factory invisible to its owner. | Medium | Session bound to the SHA-256 of the token's JWS signing input (`header.payload`) — not its raw text, which is malleable: all 16 spellings of an RS256 signature's spare bits, and a `==`-padded one, verify (measured, and pinned by a test); `UNIQUE` index (migration `0062`) refuses a second session in the database, across replicas, with no purge job. Plus our own `iat` age bound (`OIDC_MAX_TOKEN_AGE_SECONDS`, default 300) independent of whatever lifetime the realm is later edited to. |
| F3 | **Authorization policy was emergent.** 13 hand-copied `require_*` functions; the only way to answer "who can call X" was to read each one. A new route on a bare session was accepted silently. | Medium (assurance) | `RouteGate` values + `staff_gate()` factory; every operation's effective roles written to the contract as `x-authorization`; `verify_contracts.py` fails on drift, and generation **fails** for any route that is neither gated nor on a named allow-list with a reason. Gates are recognised by the enforcing function's identity, not its name. Tests pin: every gate needs MFA, only `OWNER_ADMIN` writes `/staff`, no gate admits `DRIVER` until an assigned-route surface exists. Role sets and reason codes of all 13 gates are unchanged; the one behaviour delta is that `require_owner` now also checks `mfa_verified`, which was already guaranteed because an `OWNER_ADMIN` session without MFA is refused at authentication. The console's mirror (`apps/web/src/core/rbac.js` `STAFF_ADMIN`, `SESSIONS_REVOKE_OTHER`) now says so, as the console-parity contract requires. |
| F5 | **Two owners could remove each other and leave none** (pre-existing in `disable_staff`). The last-owner guard locks only the target row. Measured on PostgreSQL 16 with both past the guard before either wrote: disable-vs-disable and revoke-vs-disable both committed, **0 active owners**; revoke-vs-revoke deadlocked on the `revoked_by` FK. | High | `assign_role`, `revoke_role` and `disable_staff` take one transaction-scoped advisory lock on the owner set before any check. Every pairing now leaves one owner and the loser gets a clean refusal. Regression test holds the lock from a third connection and requires both writers to queue on it (verified to fail with the lock removed). |
| F4 | **No password policy** in the realm: a one-character first factor was accepted. | Medium | `length(12) and maxLength(128) and notUsername and notEmail and passwordHistory(3)` — NIST 800-63B shape (length, no composition rules). **Only applies to a fresh realm import** (`--import-realm` skips an existing realm); on a running R1 it is converged by the `kcadm.sh` step and read-back in `docs/runbooks/production-deploy-day.md` §2a — tracked as `REALM-POLICY-001`, because it needs the host and an admin credential. |
| F6 | **`OPERATOR` and `DRIVER` sessions were exempt from MFA**, leaving six PII-bearing reads (order board/detail, promise, Shadow reviews/drafts/audit, SLA board) open to an operator session without a second factor. | Medium | Every staff session must be MFA-proven — at issue, at every request, and in the live-session list — regardless of role, including a user with no role. The realm already forced MFA at every sign-in, so no real user is affected; a pre-existing non-MFA row stops at its next request (tested). |

## 5. Still open

| # | Gap | Why not fixed in this item | Owner / next step |
|---|---|---|---|
| ~~O1~~ | Closed by F6. | | |
| O2 | §6.1 requires owner-only **plus re-authentication** to export PII. | **No PII export exists.** The one export is the sanitized day ledger (`STORE_DAY_ORDERS_V1`), which carries no personal-data column by construction (DEC-027), so the rule has nothing to bind to; step-up auth for a surface that does not exist would be dead code. | When a personal-data export is proposed it is a new `ExportDataset` plus a decision (the enum says so); that item must add owner-only roles and a recent-authentication check (session `issued_at` within minutes, the realm forcing a fresh login via `acr_values=mfa`). |
| O3 | Sign-in throttle keys on the proxy address, so all tablets share a bucket (documented in `auth.py`). | Correct for R1 (no public ingress); wrong once Zone P exists. | Revisit with public ingress: key on the trusted proxy's client address. |
| O4 | `DRIVER` has no surface. | Spec wants assigned-route data only; building it is a feature. | When built: its own gate + object scoping on the delivery leg; `test_no_route_gate_admits_a_driver_yet` names it. |
| O5 | Service identities (§5.2) and DB role separation (§5.3) are out of scope here. | Separate items (`SECURITY-001`, runtime). | Unchanged. |

## 6. How to change authority safely

- **Add a route:** give it `Depends(require_<x>)`. If no existing gate fits, declare one with
  `staff_gate(RouteGate("require_<x>", ROLES, "REASON_CODE"))`, re-using the repository's role
  constant so the two layers cannot disagree. Regenerate the contract and read the `x-authorization` diff.
- **A route on a bare session** needs an entry in `authorization.SESSION_ROUTES` naming what bounds it.
  Growing that list is a review decision; generation fails without it.
- **Demote someone:** `DELETE /internal/v1/staff/{id}/roles/{role}`. **Remove someone:**
  `POST /internal/v1/staff/{id}/disable` *and* disable them in Keycloak.
- **Never** put roles in token claims, a token in browser storage, or a role check only in the console.

## 7. Rollback

Code: revert the commit; gates keep their names, so routes and console are unaffected. Migration
`0062` is additive (nullable column + partial unique index); a reverted API writes `NULL` digests,
which the index ignores. The realm password policy only affects future password changes.
