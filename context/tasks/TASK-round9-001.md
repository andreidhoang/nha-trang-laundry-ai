# TASK-round9-001 — the whole-app review fixes, round 9b, the cash count and the final review

**Goal:** after round 8 three read-only reviews of the whole app (money and order lifecycle, staff
console, platform) produced 27 findings (`docs/audit/10-APP-REVIEW-2026-09-29.md`: M1–M7, C1–C11,
P1–P9). The owner wrote on 2026-09-30 "make the best decision and finish"
(`docs/DECISION_RECORD_ROUND9_2026-09-30.md`, `DEC-045`–`DEC-052`, delegated, not signed). Fix every
finding at its cause with a test that fails before the change, add the end-of-day cash count the
review found missing (`DEC-049`), then verify the result by independent verifier rounds and fix what
they find. No new price, rate or fee is invented; nothing here decides `DEC-001`–`DEC-006`.

**Domains:** `platform`, `orders_audit`, `pricing`, `business_truth`, `privacy_consent`,
`promotion_delivery_sla`

**Stable work items:** `MONEY-LIFECYCLE-009` (M1, M3, M7; `DEC-045`–`DEC-047`),
`GOODS-AND-DRAWER-009` (M2, M4; `DEC-048`), `INVOICE-TRUTH-009` (M5, M6), `COUNTER-UI-RACE-009`
(C2–C4), `CONSOLE-SHELL-009` (C1, C5–C7, C11), `CONSOLE-COPY-ACCESS-009` (C8–C10),
`PLATFORM-SECURITY-009` (P1, P4, P5, P8, P9), `OPS-OBSERVABILITY-009` (P2, P3, P6, P7; `DEC-051`),
`CASH-COUNT-009` (`DEC-049`), `ROUNDNINE-RESIDUALS-009` (`DEC-050`, `DEC-052`; slices J money,
K console, L platform), `ROUNDNINE-REVIEW-LOOP-009` (the verifier rounds and their fixes) and
`ROUNDNINE-FILMED-009` (one continuous real-browser film of the daily walk and the conformance
chapters, recorded last).

**Stage:** PRODUCTION_HARDENING
**Risk:**
- HIGH for `MONEY-LIFECYCLE-009`, `GOODS-AND-DRAWER-009`, `CASH-COUNT-009`,
  `ROUNDNINE-RESIDUALS-009` (money owed to or by customers, and the drawer figure).
- MEDIUM for `INVOICE-TRUTH-009`, `PLATFORM-SECURITY-009`, `COUNTER-UI-RACE-009`,
  `CONSOLE-SHELL-009` (a document the bookkeeper relies on, grants and keys, a price on screen).
- LOW for `CONSOLE-COPY-ACCESS-009`, `OPS-OBSERVABILITY-009`, `ROUNDNINE-REVIEW-LOOP-009`,
  `ROUNDNINE-FILMED-009`.

## Source of truth

- `docs/audit/10-APP-REVIEW-2026-09-29.md`: each finding, where it was verified, and the
  Resolution section mapping each to its fix.
- `docs/DECISION_RECORD_ROUND9_2026-09-30.md`: `DEC-045`–`DEC-052`, their boundaries and reversals.

## Normative sources

- `docs/audit/10-APP-REVIEW-2026-09-29.md`
- `docs/DECISION_RECORD_ROUND9_2026-09-30.md`
- `docs/STAFF_CONSOLE_REDESIGN_SPEC_V2.md`
- `specs/contracts/internal-api-v1.openapi.yaml`

## Done when

- Every finding has a test that fails before its change.
- The full gate set is green on one commit.
- The stubbed browser suite, the daily walk and the full conformance run pass against the real API
  on a database created empty, at desk size and at phone size.
- The verifier rounds' findings are fixed or recorded as a decision.
- One film of the final tree is recorded and every chapter result is reported as it is.
- No capability's authorisation changes (all 13 stay `NOT_AUTHORIZED`).

## Rollback

Each slice is its own merge. Migrations `0066`–`0072` (`0069` was reserved and never used) are forward-only; the cash count and the
invoice snapshots hide behind their screens and the records stay. Rolling data back is
`docs/runbooks/restore-drill.md` from the base backup `shop-till-mac.md` section 8 takes before every
upgrade.
