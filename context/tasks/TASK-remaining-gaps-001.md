# TASK-remaining-gaps-001 — e-invoice requests, VietQR, measured late credits, pickup reminders, "Cần chú ý"

**Goal:** round 7 left five gaps on `#/gaps`. The owner asked on 2026-09-28 for the best decision on
each and for what can be built now. Build the engineering half of each, working end to end against
the real API, and fail closed until the owner publishes what is theirs to publish:
- invoice requests (`DEC-040`);
- an exact VietQR on every bill (`DEC-041`);
- measured late-delivery credits (`DEC-042`);
- a pickup-reminder schedule with a two-tap send (`DEC-043`);
- a computed attention block in the evening summary (`DEC-044`).

Each other half (a provider, a credential, a feed, a Zalo Official Account, `DEC-006`) is reduced to
one switch the owner turns, and `#/gaps` says which.

**Domains:** `platform`, `orders_audit`, `privacy_consent`, `pricing`, `promotion_delivery_sla`,
`business_truth`

**Stable work items:** `EINVOICE-REQUEST-001`, `VIETQR-001`, `LATE-CREDIT-002`,
`PICKUP-REMIND-001`, `SUMMARY-ATTENTION-001`, `GAPS-FILMED-REVIEW-004`.

**Stage:** PRODUCTION_HARDENING
**Risk:**
- HIGH for `LATE-CREDIT-002` (money owed to customers).
- MEDIUM for `VIETQR-001` (the amount a customer is asked to pay), `EINVOICE-REQUEST-001` (buyer
  data and a document the bookkeeper relies on) and `PICKUP-REMIND-001` (contacting customers).
- LOW for `SUMMARY-ATTENTION-001` (reads).

## Source of truth

- `docs/DECISION_RECORD_REMAINING_GAPS_2026-09-28.md`: the five decisions, their boundaries and
  their reversals.
- `docs/REMAINING_GAPS_SPEC_V1.md`: each item's data, domain, API, console and proof; the future
  contracts for automatic matching and model wording; what stays unbuilt.

## Normative sources

- `docs/REMAINING_GAPS_SPEC_V1.md`
- `docs/SHOP_OPERATIONS_SPEC_V1.md` §0
- `docs/STAFF_CONSOLE_REDESIGN_SPEC_V2.md`
- `specs/contracts/internal-api-v1.openapi.yaml`

## Done when

- Every item has a test that fails before its change.
- The full gate set is green on one commit.
- The stubbed browser suite, the daily walk and the full conformance run pass against the real API
  on a database created empty, at desk size and at phone size.
- Every new flow is filmed at 1366×900 (refusal, publish, working) and reviewed, and every finding
  is fixed.
- `#/gaps` lists exactly what is still missing.
- No capability's authorisation changes.

## Rollback

Each item is its own merge. Routes and fields are additive, migrations `0062`–`0064` are
forward-only and additive, and each feature hides behind its owner switch.
