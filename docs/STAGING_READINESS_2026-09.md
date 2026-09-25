# Staging readiness — measured 2026-09-24, updated 2026-09-25 (twice)

**Question:** can real counter staff use this application for staging and feature testing in daily
operation?

**Answer: GO WITH CONDITIONS, on the Mac-till path only** (`docs/runbooks/shop-till-mac.md`:
`compose.r1.yaml` + `compose.shop-local.yaml` + `compose.shop-till.yaml`). The server runbooks and
`start_demo.py` are not staging paths: the first has never been run end to end, the second hands out
owner access from a development identity provider by design.

This document is a verdict with its evidence. Findings and the decision on each are in
`context/tasks/TASK-staging-review-001.md` and `context/tasks/TASK-founder-decisions-001.md`; the
rulings are in `docs/DECISION_RECORD_FOUNDER_2026-09-25.md`; live queue status is
`uv run python scripts/report_delivery_status.py`.

## How it was measured

**2026-09-24.** Five independent reviewers, read-only, one lens each — money and domain; API
authority and data integrity; the staff console as a shift is run on it; the agentic runtime;
operations — against `main` at `9287596`, while every gate was green. Every finding had to name a
file, a concrete failure and whether it was reproduced. Fixes were built in isolated worktrees, each
with a test that fails on the previous code, then merged and re-verified by the lead.

**2026-09-25.** The owner delegated the open business decisions (`DEC-029`–`DEC-032`, recorded as
delegated, not signed). Five more engineers built them and the review's follow-ups; the lead built
the owner's range-price review list the `DEC-029` ruling depends on; every new flow was then driven
by hand against the running API as the real roles.

**2026-09-25, evening.** Every engineering follow-up listed below as "not done" that morning was
built by six engineers and merged by the lead (`context/tasks/TASK-followups-002.md`). Then every
core workflow was driven in a real browser against the real API **on video**, on a database created
empty, so the owner could watch it rather than read a log (`--video` on both real-API scripts).

| Gate | `9287596` (before) | 2026-09-24 | 2026-09-25 | 2026-09-25 evening |
|---|---|---|---|---|
| `pytest --require-postgres-integration` | 1574 passed | 1749 passed, 0 failed | 2105 passed, 0 failed | **2274 passed, 0 failed** |
| `mypy apps packages` | 249 files | 257 files | 290 files, clean | 304 files, clean |
| `verify_contracts.py` | 57 ops, 348 disclosures | 58 ops, 381 | 61 ops, 396 | 66 ops, 419 |
| Real API, shop day (`verify_daily_operations.py`) | 70 / 0 | 71 / 0 | 71 / 0 | 72 / 0, filmed; 72 / 0 again |
| Real API, every other workflow | 86 / 1 | 86 / 1 | 86 / 1 (coverage line only) | **159 / 0**, all 49 controls |
| Stubbed API, console interaction | 170 / 0 | 170 / 0 | 171 / 0 | 251 / 0 |

Every real-API run starts from a database created empty and migrated to the latest migration
(`0052` in the evening), with the remedy and promotion policies published. The "every other
workflow" column had failed its coverage line on every run since it was written: without docker
the staff-hiring scenario never got past its first database read. `--database-url` fixed that.

**What the filmed walk found that 2105 green tests had not**, each fixed with a test that fails
before the fix:

- **The owner could not decide any `DEC-031` claim.** Every loss, every refunded-order
  compensation and anything above 100.000 ₫ went to an owner envelope the Duyệt screen could not
  display, and an approved claim could only be paid out in the session that proposed it
  (`REMEDY-OWNER-DECIDE-001`).
- **Paying one claim closed a complaint about several garments**, so the lost suit could not be
  added to it (`REMEDY-INCIDENT-OUTCOME-001`).
- The owner's range-price review printed "trong khoảng [object Object]" where the band should be.
- A paid order kept a live payment form, offering to take the money twice.
- "Khách đã nhận đồ" pressed too early answered with advice about taking money.
- A paid delivery order said its goods had not arrived, after the successful trip was recorded.

**The green column on the left is the finding.** Every defect fixed here was present while it was
green. On both days the real-API browser run on the merged tree caught problems that thousands of
passing tests and the stubbed browser suite did not.

## What changed for the person at the counter

- **"Phiếu 17".** The orders screen opens on *Tìm theo số phiếu*; the order appears with
  **Phải thu: 132.000 ₫**. Open orders never fall off the board by age.
- **Paying at drop-off.** A walk-in may pay the exact total when leaving the laundry; *Khách đã nhận
  đồ* is pressed separately at pickup. Ticking "collected" for laundry not yet washed is refused.
  Part payments are still refused (`DEC-010`). *Measured live: ticket 5, 140.000 ₫.*
- **Range-priced items (20 of 44 services).** The staff member on duty chooses the price inside the
  band the owner published and it is final in one press — no owner, no ten-minute window. A price
  outside the band is refused. *Measured live: áo dài 160.000 ₫ inside 80.000–240.000 ₫.*
- **The owner reviews those prices** on *Duyệt → Giá trong khoảng nhân viên đã chốt hôm nay*: who
  chose, which item, how much, inside which band.
- **Complaints.** Staff may authorise up to 100.000 ₫ in total **per garment**: on a line of three
  shirts, "Món thứ mấy" asks which, and each has its own limit and its own ceiling of five times its
  unit price. **Every lost item and every compensation on a refunded order goes to the owner**,
  whatever the amount; the owner decides it on **Duyệt**, with until the end of the next day, and
  the counter pays it out later from the complaint's list. The complaint stays open until every
  claim on it has an outcome. *Filmed: three suits, one lost and one faded — 25 / 25.*
- **Looking things up.** The owner sees who works in the shop; an order shows its credit codes (a
  customer who lost one keeps it) and where the customer came from.
- **A busy moment.** If the database is held up, the counter reads "Hệ thống đang bận… thử lại" and
  pressing again records the payment exactly once. *Filmed.*
- **A credit and a promotion.** The promotion applies; the credit is kept, unspent, for the next
  order (`DEC-030`) — including when the credit was put on the quote first.
- **"Khách đã chốt giá" fails, a re-price, a server refusal, "5,5" kg, a mistyped payment, a refund,
  last month's price list** — as fixed on 2026-09-24.

## Conditions

1. **Take a base backup before every upgrade, and run the restore drill once on real data before
   trusting it.** `shop-till-mac.md` §8 is the procedure; migrations are forward-only and the backup
   *is* the rollback.
2. **Do the alert test on first install — `shop-till-mac.md` §5c — and until it passes, look at the
   checks by hand daily.** Alerts are now relayed by the host (the database network stays internal)
   and are proven against a local stand-in for Telegram, but nobody has watched one arrive on the
   owner's phone. `SHOP-ALERT-DELIVERY-001` is recorded **BLOCKED** on exactly that step.
3. **Publish the remedy and promotion policies at setup** (`shop-till-mac.md` §4).
4. **No agent capability is switched on.** All 13 are `NOT_AUTHORIZED`. The runtime defects that
   blocked internal Shadow are fixed; Telegram sandbox contacts count as real customers and are
   refused a model call until Shadow is authorised through its gate.

## Decided on the owner's behalf, 2026-09-25

Recorded as delegated, not signed, each with its reversal, in
`docs/DECISION_RECORD_FOUNDER_2026-09-25.md`. Where no ratified figure exists, money goes to the
owner's approval rather than out by rule.

| | Ruling |
|---|---|
| `DEC-029` | Staff on duty choose inside a published band; owner reviews afterwards. |
| `DEC-030` | Promotion applies; the credit waits, unspent. |
| `DEC-031` | Per-piece item fee is the unit price; loss and refunded-order compensation always to the owner. |
| `DEC-032` | Exact prepayment at drop-off; pickup recorded separately. A courier-fetched (`PICKUP_ONLY`) customer may do the same at the counter; the courier never takes money. |
| `DEC-031` addendum | The staff limit and ceiling are per garment; the owner has until the end of the next day to decide; a complaint closes when every claim on it has an outcome. |

## Still the owner's, and why

| Question | Why it is not delegated |
|---|---|
| `DEC-006` — the model provider and its legal check | Names an organisation and asserts a legal basis. |
| `DEC-016` — who staffs the inbound channel | Names a person. |
| Signing (or reversing) `DEC-029`–`DEC-032` | They stand as delegated until signed. |

## Not done, and why

| Item | Reason |
|---|---|
| `SHOP-ALERT-DELIVERY-001` | Blocked on the owner's phone test (condition 2). |
| A message-draft decision on film | No draft exists without an authorised agent run, and all 13 capabilities are `NOT_AUTHORIZED`. It is proven over HTTP against PostgreSQL and in the stubbed browser suite. Consent and suppression checks for manual sends are still to be built before any send. |
| Deadlocks answer 500 | Timeouts and an unopenable database answer 503 with a safe retry; a deadlock is rarer and outside that item. |
| The worker's execution claim | Still compares the echoed hash; decisions and attestations re-resolve the resource. |
| apk `-rN` pins | Assessed, left, with a recovery step in §8. |
| Remote branch cleanup | Deleting remote refs is refused in this environment; `archive/…` and `codex/…` hold the only copy of the original lineage (ADR-0004). |

Closed since the morning: a timed-out statement (now 503), decision-time approval checks, reading a
message draft's binding, per-shirt identity, the ten-minute owner window (now to the end of the
next day), `PICKUP_ONLY` prepayment, and the four read paths the console's gap register listed.
