# TASK-founder-decisions-001 — building what the 2026-09-25 rulings decided

**Goal:** implement `DEC-029`, `DEC-031` and `DEC-032` (`DEC-030` needs no code), and close the four
follow-ups `TASK-staging-review-001` recorded.

**Domains:** `orders_audit`, `pricing`, `business_truth`, `platform`

**Stable work items:** `REMEDY-ITEM-FEE-001`, `RANGE-COUNTER-ATTEST-001`, `PREPAID-DROPOFF-001`,
plus `SHOP-ALERT-DELIVERY-001`, `OPS-HARDENING-002`, `API-INTEGRITY-002`, `AGENT-SHADOW-DEFECTS-001`
from `TASK-staging-review-001`.

**Stage:** PRODUCTION_HARDENING
**Risk:** HIGH for the three money items — each changes who may authorise money or when it is taken.

## Source of truth

`docs/DECISION_RECORD_FOUNDER_2026-09-25.md` and the four registry entries. They were taken under
the owner's delegation of 2026-09-25 and recorded as delegated, not signed. The one rule every
implementation must keep: **where no ratified figure exists, money goes to the owner's approval,
never out by rule.**

## Done when

Each item has a test that fails before its change; the full gate set is green on one commit; both
real-API browser scripts pass on a database migrated from empty; and no capability's authorisation
changes.

## Rollback

Each ruling states its own reversal. Any migration here is forward-only and additive.
