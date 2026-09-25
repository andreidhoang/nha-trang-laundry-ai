# TASK-counter-completeness-001 — the missing pieces of a working shop day

**Goal:** after the V2 redesign every core workflow ran on the real API, but a counter still
misused *Tạm dừng* for a rewash and *Huỷ* for refused goods, typed a credit code and pasted a
channel customer's code, handed the customer nothing, and the owner had no numbers, a one-day
export and no way to cut off a lost phone. Close every one of those gaps that engineering alone can
close, and leave every other one on `#/gaps` with its true reason.

**Domains:** `platform`, `orders_audit`, `pricing`, `business_truth`

**Stable work items:** `ORDER-STEPS-002`, `CREDIT-PICK-001`, `CONTACT-PICK-001`,
`RECEIPT-PRINT-001`, `REPORT-DASHBOARD-001`, `EXPORT-RANGE-001`, `SESSION-LIST-001`,
`COUNTER-FILMED-REVIEW-003`.

**Stage:** PRODUCTION_HARDENING
**Risk:** HIGH for `ORDER-STEPS-002` (order state) and `EXPORT-RANGE-001` (what an approval
releases); MEDIUM for `REPORT-DASHBOARD-001` (figures the owner decides on) and
`SESSION-LIST-001` (authentication); LOW for the rest (reads and presentation).

## Source of truth

- `docs/COUNTER_COMPLETENESS_SPEC_V1.md` — the gaps measured, the founder rulings R1–R5
  (delegated, reversible, no money beyond ratified figures, no legal basis asserted), each item's
  server and console contract and its proof, and what stays blocked and why.
- `specs/ENGINEERING_SPEC_V1.md` `FR-RPT-001`–`FR-RPT-007` for the reporting items.

## Normative sources

- `docs/COUNTER_COMPLETENESS_SPEC_V1.md`
- `docs/STAFF_CONSOLE_REDESIGN_SPEC_V2.md` (the console rules every screen keeps)
- `specs/ENGINEERING_SPEC_V1.md`
- `specs/contracts/internal-api-v1.openapi.yaml`

## Done when

Every item has a test that fails before its change; the full gate set is green on one commit; the
stubbed browser suite, the daily walk and the full conformance run pass against the real API on a
database created empty, at desk size and at phone size; every new flow is filmed at phone size,
reviewed as a counter worker and as an engineer, and every finding fixed; `#/gaps` lists exactly
what is still missing; no capability's authorisation changes.

## Rollback

Each item is its own merge. New routes and fields are additive; the per-axis order routes are
unchanged; reverting a console change restores the previous screen against the same API. Any
migration is forward-only and additive.
