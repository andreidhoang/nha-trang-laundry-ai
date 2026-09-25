# TASK-shop-operations-001 — customers, payments, promises, unclaimed laundry, measurement, the evening summary

**Goal:** the owner decided on 2026-09-25 the six things round 6 left as declined or blocked:
- a customer list (`DEC-034`);
- part payments and account customers (`DEC-035`);
- unclaimed laundry (`DEC-036`);
- a promised-ready time on every order (`DEC-037`);
- measuring the shop (`DEC-038`);
- an evening summary without a language model (`DEC-039`).

Build each so it works end to end against the real API, fails closed until the owner publishes
what is theirs to publish, and is filmed and reviewed like round 6.

**Domains:** `platform`, `orders_audit`, `privacy_consent`, `pricing`, `promotion_delivery_sla`,
`business_truth`

**Stable work items:** `CUSTOMER-001`, `PAYMENT-001`, `PAYMENT-002`, `PROMISE-001`,
`UNCLAIMED-001`, `SHOP-CAPTURE-001`, `DAILY-SUMMARY-001`, `EXPORT-PAYMENTS-001`, `SHOP-FILMED-REVIEW-004`.

**Stage:** PRODUCTION_HARDENING
**Risk:** HIGH for `CUSTOMER-001` (personal data), `PAYMENT-001`/`PAYMENT-002` (money and when goods
leave), `UNCLAIMED-001` (a fee and the disposal of customer property); MEDIUM for `PROMISE-001`
(a promise to customers); LOW for `SHOP-CAPTURE-001` and `DAILY-SUMMARY-001` (records and reads).

## Source of truth

- `docs/DECISION_RECORD_SHOP_OPERATIONS_2026-09-25.md` — the six decisions, their grounding in the
  owner-confirmed facts (`BUSINESS_TRUTH_INTAKE.md`, `templates/service-sla.csv`,
  `templates/business-calendar-rules.csv`, `templates/machine-master.csv`), boundaries, reversals.
- `docs/SHOP_OPERATIONS_SPEC_V1.md` — each item's data, domain, API, console and proof; build order.

## Normative sources

- `docs/SHOP_OPERATIONS_SPEC_V1.md`
- `docs/DECISION_RECORD_SHOP_OPERATIONS_2026-09-25.md`
- `docs/STAFF_CONSOLE_REDESIGN_SPEC_V2.md`
- `specs/SECURITY_RELIABILITY_SPEC_V1.md`
- `specs/contracts/internal-api-v1.openapi.yaml`

## Done when

- Every item has a test that fails before its change.
- The full gate set is green on one commit.
- The browser suites pass on the real API from an empty database, at desk size and at phone size.
- Every new flow is filmed at phone size and reviewed as the counter worker and the owner, and every
  finding is fixed.
- Nothing charges, promises or stores personal data before the owner's publication.
- No capability's authorisation changes.

## Rollback

- Each item is its own merge. Every migration is forward-only and additive.
- Every owner-published policy can be unpublished, which returns its feature to refusing.
- Customer erasure keeps orders and money.
