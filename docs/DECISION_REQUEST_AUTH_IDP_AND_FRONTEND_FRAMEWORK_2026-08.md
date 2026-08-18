# Decision request — production staff identity provider and frontend framework path

**Date:** 2026-08-16
**Status:** awaiting owner answers. Nothing in this document changes behaviour.
**Trigger:** planning for production staff sign-in and the staff-console UX refactor program
(`docs/STAFF_CONSOLE_UX_REFACTOR_SPEC_V1.md`) surfaced two unregistered decisions.
**Assessments this rests on:** `apps/web/README.md:22-53` (supply-chain rationale),
`docs/adr/0007-production-deployment-topology.md` (proposed), `specs/SECURITY_RELIABILITY_SPEC_V1.md`
§5.1/§5.2/§6.2, audit of the current verifier at
`apps/api/src/nha_trang_laundry_api/auth.py:146-181`.

`SECURITY-001` (`delivery/WORK_QUEUE.yaml:697-738`) is `PENDING` with an
`oidc_provider_integration` evidence requirement. That requirement cannot be met by engineering
alone: which provider the evidence describes is a decision, and one admissible answer removes the
third party the privacy questions are about. This document turns the blocking conditions into
questions that can be answered and signed.

---

## 0. What this request does not do

- It does not add any dependency, SDK, vendor account, or credential.
- It does not modify the OIDC verifier, the session model, the CSP, or any contract test.
- It does not adopt a frontend framework or a build step.
- It authorizes no external call. The demo IdP remains the only issuer the API trusts today, and
  only in the demo/staging overlay.
- It does not change `DEC-006` or `DEC-008` — both stay `OPEN`.

## 1. What is already decided (and is not being re-opened)

The architecture is **provider-neutral OIDC with server-side authority**, and this request keeps it:

- The verifier (`auth.py:146-181`) is generic RS256/JWKS with a configurable MFA claim. Swapping
  issuers is env configuration (`oidc_issuer`, `oidc_audience`, `oidc_jwks_url`, `oidc_mfa_claim`,
  `oidc_mfa_value`), not code.
- **Roles never come from the token.** `staff_role_assignments` in PostgreSQL is the only role
  source (`packages/db/.../identity.py:51`: "without trusting OIDC role claims"). Any IdP answer
  preserves this.
- Sessions are opaque, database-backed, revocable, and fail closed on `authorization_version`
  drift (`identity.py:198-316`). MFA is the IdP's asserted claim; the system never implements MFA.
- The browser is not and cannot become an OIDC client (`apps/web/README.md:69-72`; CSP
  `connect-src 'self'`; §5.2 forbids tokens in browser storage). The integration shape stays:
  IdP-hosted login → redirect → `POST /internal/v1/auth/session` bearer exchange — exactly what
  the demo IdP already exercises end to end.
- Spec mandate: *"Password, passkey or OIDC implementation must come from a maintained auth
  component"* (SECURITY_RELIABILITY §5.1) — the demo IdP is explicitly not that; a production
  answer is owed before any real staff sign-in.

## 2. Decision A — production staff identity provider

**Owner:** `SECURITY_PRIVACY_OWNER`
**Registry:** proposed `DEC-011`, `OPEN`, fail-closed `NOT_SUPPORTED` (no production staff issuer
is configured; demo IdP stays dev/staging-only per `TASK-demo-stack-001`).

| Answer | Consequence |
|---|---|
| **Keycloak self-hosted in Zone C** (recommended) | Staff PII (names, emails, auth events) never leaves the owner-controlled host — no DEC-006-style cross-border assessment is owed for staff identity, consistent with ADR-0007 minimization criterion "PII stays in Vietnam". WebAuthn/passkey + TOTP satisfy "no SMS-only MFA for privileged roles" (§5.1). Login path has zero external dependency, matching the VPN/allowlist posture. Cost: the business operates and upgrades one more stateful service. |
| **Managed OIDC (WorkOS AuthKit / Auth0)** | No IdP operations burden. But staff PII leaves Vietnam → a DEC-006-pattern residency and retention assessment is required **before** any real staff account is created, and the production login path gains an external availability dependency. WorkOS's differentiating value (SAML/SCIM federation for enterprise customers) addresses a customer-facing need this business does not currently have; it can be adopted later for a B2B customer portal without changing this decision. |
| **Defer** | `SECURITY-001` stays `PENDING`; production staff sign-in remains undefined; nothing breaks today. |

Recommendation on record: **Keycloak self-hosted**, because it is the only answer that creates no
new counterparty, no new data-export question, and no new runtime dependency, while satisfying the
§5.1 "maintained auth component" mandate and the MFA requirements with configuration alone.

Whichever answer is signed, the engineering slice is the same shape: realm/tenant configuration,
MFA policy for `SENSITIVE_MFA_ROLES` (`identity.py:25-27`), JWKS availability behaviour of
`PyJWKClient` under IdP outage (cache/fail-closed policy must be stated), and the
`oidc_provider_integration` evidence record under `SECURITY-001`.

## 3. Decision B — frontend framework adoption timing

**Owner:** `PRODUCT_ENGINEERING_OWNER`
**Registry:** proposed `DEC-012`, `OPEN`, fail-closed: the console stays framework-free.

Context: the current no-build stack is a recorded, evidence-backed deviation from ADR-0001's
"React/Vite staff PWA" note. `apps/web/README.md:22-53` names four supply-chain blind spots
(single-lockfile licence audit, pinned `npm audit` prefix, hardcoded evidence lockfile list,
Trivy/SBOM invisibility of bundled assets) that would certify a release over an unscanned
dependency tree if a framework were added today. The UX audit
(`docs/STAFF_CONSOLE_UX_REFACTOR_SPEC_V1.md`) found no defect that a framework would fix; the
console's friction is information hierarchy and upstream read APIs (P3–P7), not rendering.

| Answer | Consequence |
|---|---|
| **Stay framework-free until P3–P7 land** (recommended) | WS1–WS6 refactor proceeds on the current stack. Framework adoption is revisited when pickers/dashboards/pagination make the interactivity genuinely needed, and then only as its own queue item that first fixes the four supply-chain call sites and adds a digest-pinned Node builder stage, per the README's stated condition. |
| **Adopt React/Vite now** | Blocks on a prerequisite queue item: licence audit, `npm audit`, evidence lockfile list, and image SBOM must all cover the second tree **before** any framework code merges. No UX benefit is delivered sooner, because the blocking issues are upstream APIs. |
| **Defer** | Same as the recommended answer in practice; the question returns at P3–P7 completion. |

## 4. What each answer unblocks

```
Decision A (DEC-011) ─┬─ Keycloak self-hosted → SECURITY-001 buildable (config + evidence);
                      │                        no privacy assessment prerequisite
                      ├─ Managed OIDC        → SECURITY-001 buildable only AFTER a staff-PII
                      │                        residency/retention record (DEC-006 pattern)
                      └─ Defer                → SECURITY-001 stays PENDING
Decision B (DEC-012) ─┬─ stay framework-free → CONSOLE-UX-001…005 proceed as specced
                      └─ adopt now           → a supply-chain prerequisite item enters the queue
                                               ahead of any framework code
```

Neither decision blocks the WS1–WS6 console refactor; both unblock paths keep it valid.

## 5. Recommended sequence

1. **Decision B** ("stay framework-free") — one sentence, removes pressure from the UX program.
2. **Decision A** ("Keycloak self-hosted") — makes `SECURITY-001` the next buildable security item.
3. Only if Decision A selects a managed provider: commission the staff-PII residency/retention
   record first; do not create real staff accounts before it is signed.

## 6. Signature block

Fill and commit. Unsigned rows keep their fail-closed defaults, which is the current behaviour.

```yaml
decision_a_staff_idp:        # proposed DEC-011
  answer:            # KEYCLOAK_SELF_HOSTED_ZONE_C | MANAGED_OIDC | DEFER
  owner:             # SECURITY_PRIVACY_OWNER
  decided_at:
  provider:          # required for MANAGED_OIDC
  residency_record:  # required for MANAGED_OIDC — path to the staff-PII assessment
  rationale:
decision_b_frontend_framework:  # proposed DEC-012
  answer:            # STAY_FRAMEWORK_FREE_UNTIL_P3_P7 | ADOPT_REACT_VITE_NOW | DEFER
  owner:             # PRODUCT_ENGINEERING_OWNER
  decided_at:
  rationale:
```

Fail-closed defaults while unsigned: no production staff issuer is configured (demo IdP remains
dev/staging-only), and the staff console remains framework-free with no second dependency tree.
