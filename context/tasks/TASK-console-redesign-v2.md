# TASK-console-redesign-v2 — a console a counter uses all day: task-first, zero paste, one next step

**Goal:** the owner judged the V1 console "complicated, a lot of text, not user-centric". Rebuild
every screen around the staff task instead of the server command, with no identifier pasted in
daily work and one obvious next step decided by the server — without losing a single invariant,
disclosure or capability — then film every workflow at phone size against the real API, review it
as a user and as an engineer, and fix everything found. The two server follow-ups and the consent
checks the previous round left open land in the same round.

**Domains:** `platform`, `orders_audit`, `pricing`, `business_truth`, `privacy_consent`

**Stable work items:** `API-INTEGRITY-004`, `CONSENT-TRANSACTIONAL-001`, `ORDER-STEPS-001`,
`READ-ENRICH-001`, `CONSOLE-REDESIGN-000`, `CONSOLE-REDESIGN-001`, `CONSOLE-REDESIGN-002`,
`CONSOLE-REDESIGN-003`, `CONSOLE-REDESIGN-004`, `CONSOLE-REDESIGN-005`, `CONSOLE-REDESIGN-006`,
`CONSOLE-FILMED-REVIEW-002`.

**Stage:** PRODUCTION_HARDENING
**Risk:** HIGH for `CONSENT-TRANSACTIONAL-001` (who may be messaged) and `ORDER-STEPS-001` (state
and money steps); MEDIUM for the console slices (presentation over unchanged server rules).

## Source of truth

- `docs/STAFF_CONSOLE_REDESIGN_SPEC_V2.md` — principles, design system, honesty tiers, every
  screen, the server additions, the invariants kept, acceptance.
- `docs/DECISION_RECORD_CONSENT_2026-09-25.md` (`DEC-033`, delegated) and
  `docs/POLICY_TRANSACTIONAL_MESSAGING_V1.md` — publishing the policy is the owner's confirmation of
  the legal basis; until then every service send is refused.
- Founder rulings recorded in `ORDER-STEPS-001`'s follow-up commit: washing starts on an active
  order only; an unpaid counter order is released only by taking payment.

## Normative sources

- `docs/STAFF_CONSOLE_REDESIGN_SPEC_V2.md`
- `docs/DECISION_RECORD_CONSENT_2026-09-25.md`
- `docs/POLICY_TRANSACTIONAL_MESSAGING_V1.md`
- `specs/SECURITY_RELIABILITY_SPEC_V1.md` (§8.4 consent and suppression; the 503 refusal contract)
- `specs/contracts/internal-api-v1.openapi.yaml`

## Done when

Every item has a test that fails before its change; the full gate set is green on one commit; the
stubbed browser suite, the daily walk, the full conformance run and the consent walk pass against
the real API on a database migrated from empty — at desk size and at phone size; the filmed walk
has been reviewed and every finding fixed; no capability's authorisation changes.

## Rollback

Each item is its own merge; server additions are additive (new fields and routes, the per-axis
routes kept); migration 0053 is forward-only and additive. Reverting a console slice restores its
V1 screen against the same API.
