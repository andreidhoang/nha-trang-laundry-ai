# TASK-followups-002 — the recorded follow-ups, closed; and every core workflow, filmed

**Goal:** close every engineering follow-up `docs/STAGING_READINESS_2026-09.md` listed as "not done"
on 2026-09-25, close the read paths the console's own gap register admitted, and then drive every
core workflow in a real browser against the real API on a database migrated from empty, on video,
so the owner can watch it rather than read a log.

**Domains:** `orders_audit`, `pricing`, `business_truth`, `platform`

**Stable work items:** `API-INTEGRITY-003`, `MESSAGE-DRAFT-BINDING-001`, `PICKUP-ONLY-SETTLE-001`,
`REMEDY-GARMENT-001`, `REMEDY-INCIDENT-OUTCOME-001`, `READ-PATHS-001`, `REMEDY-OWNER-DECIDE-001`,
`CONSOLE-FILMED-WALK-001`.

**Stage:** PRODUCTION_HARDENING
**Risk:** HIGH for `REMEDY-GARMENT-001` and `PICKUP-ONLY-SETTLE-001` (who may authorise money, and
when it is taken); MEDIUM for the rest.

## Source of truth

- `docs/DECISION_RECORD_FOUNDER_2026-09-25.md` and its two addenda of the same day — per garment
  and the owner window (`DEC-031`), and `PICKUP_ONLY` (`DEC-032`). Taken under the owner's
  delegation, recorded as delegated, not signed. The rule every implementation keeps: **where no
  ratified figure exists, money goes to the owner's approval, never out by rule.**
- `docs/CORE_OPERATIONS_COMPLETION_SPEC_V1.md` for the API behaviour; the console gap register
  (`apps/web/src/screens/gaps.js`) for what the counter was told does not exist.

## What each item is

| Item | What changes for the shop |
|---|---|
| `API-INTEGRITY-003` | A database that is busy answers 503 "đang bận, thử lại" and the retry is safe; an approval is refused if the thing it approves changed after it was raised. |
| `MESSAGE-DRAFT-BINDING-001` | The approver reads the exact words a `SEND_MESSAGE` envelope binds and decides it in the queue; the operator raises it from the server's read. No automated send; no capability changes. |
| `PICKUP-ONLY-SETTLE-001` | A customer whose laundry the courier fetched may pay the exact total at the counter before it is finished; no courier takes money. |
| `REMEDY-GARMENT-001` | A claim names which garment on a per-piece line; the staff limit and the 5× ceiling are per garment. Owner-only remedy envelopes stay open to the end of the next business day. |
| `REMEDY-INCIDENT-OUTCOME-001` | A complaint about several garments stays open until every claim on it has an outcome; paying the first no longer closes it (found by the filmed walk). |
| `REMEDY-OWNER-DECIDE-001` | The owner decides a loss or above-limit claim on Duyệt, and staff carry an approved claim out later from any session. Before it, every `DEC-031` owner envelope was undecidable from the console. |
| `READ-PATHS-001` | The owner sees who works in the shop; an order shows its credits; an incident shows its remedy proposals; an order shows where the customer came from. |
| `CONSOLE-FILMED-WALK-001` | The real-API browser scripts can film themselves (`--video`), read the database without docker (`--database-url`), and watch every screen for `[object Object]`/`NaN`. Every defect the filmed walk found is fixed with a guard. |

## Done when

Each item has a test that fails before its change; the full gate set is green on one commit; the
stubbed browser suite and both real-API browser scripts pass on a database migrated from empty; the
filmed walk has been watched end to end; and no capability's authorisation changes.

## Rollback

Each ruling states its own reversal. Migrations are forward-only and additive. The recording and
defect-watch code is inert unless `--video` is passed or a render defect appears.
