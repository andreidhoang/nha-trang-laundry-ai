# Staging readiness — measured 2026-09-24, updated 2026-09-25

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

| Gate | `9287596` (before) | 2026-09-24 | 2026-09-25 |
|---|---|---|---|
| `pytest --require-postgres-integration` | 1574 passed | 1749 passed, 0 failed | **2105 passed, 0 failed** |
| `mypy apps packages` | 249 files | 257 files | 290 files, clean |
| `verify_contracts.py` | 57 ops, 348 disclosures | 58 ops, 381 | 61 ops, 396 |
| Real API, shop day (`verify_daily_operations.py`) | 70 / 0 | 71 / 0 | 71 / 0 |
| Real API, every other workflow | 86 / 1 | 86 / 1 | 86 / 1 (coverage line only) |
| Stubbed API, console interaction | 170 / 0 | 170 / 0 | 171 / 0 |

Every real-API run starts from a database created empty and migrated to the latest migration
(`0051`), with the remedy and promotion policies published.

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
- **Complaints.** Staff may authorise up to 100.000 ₫ in total per item. A per-piece item is capped
  at five times its own unit price; a second damaged shirt on the same line goes to the owner rather
  than being refused. **Every lost item and every compensation on a refunded order goes to the owner**,
  whatever the amount. *Measured live: a 50.000 ₫ loss → OWNER_APPROVAL_REQUIRED, LOSS_CLAIM.*
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
| `DEC-032` | Exact prepayment at drop-off; pickup recorded separately. |

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
| Reading a message draft's approval binding | The server now computes it; nothing exposes it yet, so a `SEND_MESSAGE` envelope cannot be raised from the console. No automated send exists. |
| A timed-out statement answers 500 | Bounded timeouts exist; they are not yet mapped to 503. |
| Decision-time approval checks | Still compare against the hash the client echoes; the server-side rendering exists for agent quotes and is not yet wired into `approvals.py`. |
| Per-shirt identity | Compensation ceilings aggregate per line; the owner handles anything beyond. |
| Owner envelopes expire in ten minutes | Every loss now needs one; if the owner is away the proposal is re-raised. |
| apk `-rN` pins | Assessed, left, with a recovery step in §8. |
| Remote branch cleanup | Deleting remote refs is refused in this environment; `archive/…` and `codex/…` hold the only copy of the original lineage (ADR-0004). |
