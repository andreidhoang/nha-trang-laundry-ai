# Staging readiness — measured 2026-09-24

**Question:** can real counter staff use this application for staging and feature testing in daily
operation?

**Answer: GO WITH CONDITIONS, on the Mac-till path only** (`docs/runbooks/shop-till-mac.md`:
`compose.r1.yaml` + `compose.shop-local.yaml` + `compose.shop-till.yaml`). The server runbooks and
`start_demo.py` are not staging paths: the first has never been run end to end, the second hands out
owner access from a development identity provider by design.

This document is a verdict with its evidence. The findings, and the decision taken on each, are in
`context/tasks/TASK-staging-review-001.md`; live queue status is
`uv run python scripts/report_delivery_status.py`.

## How it was measured

Five independent reviewers, read-only, one lens each — money and domain; API authority and data
integrity; the staff console as a shift is run on it; the agentic runtime; operations — against
`main` at `9287596`, while every gate was green. Every finding had to name a file, a concrete
failure and whether it was reproduced. Fixes were built in isolated worktrees, each with a test that
fails on the previous code, then merged and re-verified by the lead:

| Gate, on the merged tree | Before (`9287596`) | After |
|---|---|---|
| `pytest --require-postgres-integration` | 1574 passed | 1749 passed, 0 failed |
| `mypy apps packages` | 249 files clean | 257 files clean |
| `verify_contracts.py` | 57 operations, 348 disclosures | 58 operations, 381 disclosures |
| Real API + real identity provider, shop day (`verify_daily_operations.py`) | 70 / 0 | 71 / 0 |
| Real API, every other workflow (`verify_workflow_conformance.py`) | 86 / 1 | 86 / 1 (coverage line only) |
| Stubbed API, console interaction | 170 / 0 | 170 / 0 |

Both real-API runs start from a database created empty and migrated `0001`–`0047`.

**The green column on the left is the finding.** Every defect below was present while it was green.
The real-API browser run on the merged tree caught four more that 1748 passing tests and the stubbed
browser suite did not. A green suite is where this review started, not where it ended.

## What changed for the person at the counter

- **A customer comes back with "phiếu 17".** The orders screen opens on *Tìm theo số phiếu*: type the
  number, optionally the date on the slip, and the order appears with **Phải thu: 132.000 ₫**. Open
  orders never fall off the board by age. Before, an order became unreachable after about three days
  and no screen showed what the customer owed.
- **"Khách đã chốt giá" fails** — expired, timed out, someone else moved it — and the button stays,
  says why in Vietnamese, and offers the next step. Pressing again after a timeout cannot record the
  acceptance twice. Before, the control erased itself and showed nothing.
- **Re-pricing a bag twice works.** Before, the second press was refused as stale.
- **The server refuses something** — staff read a Vietnamese sentence that names the fact on record
  ("đơn này đã ghi nhận tiệm nhận đồ của khách"), with the engineer's text folded away.
- **"5,5" kg** is read as 5.5. **13.200 typed for 132.000** is told to check and retype.
- **A complaint** is recorded on *Sự cố* and its remedy proposed there. Staff may authorise up to
  100.000 ₫ **in total per item**, and cannot split a claim to avoid the owner. A credit the shop owes
  stays owed through a re-price. A lost item still refuses any amount — `DEC-004` has no figure.
- **A prepaid order is cancelled and the money handed back** — "tiền đã thu" shows the takings, the
  refund, and whether the drawer went up or down, in words. It never shows a negative number.
- **The owner republishes last month's price list** — it is in force again, as a new version.

## Conditions

1. **Take a base backup before every upgrade, and run the restore drill once on real data before
   trusting it.** `shop-till-mac.md` §8 is the upgrade procedure; `restore.sh` now runs on the
   Debian drill host and on macOS, and neither was true before. Migrations are forward-only: the
   backup *is* the rollback.
2. **Until `SHOP-ALERT-DELIVERY-001` lands, someone looks at the checks by hand, daily.** Backup and
   disk alerts cannot reach a person — the data checks run on an `internal: true` network. A disk
   that fills stops the counter with nobody told. `./scripts/shop-admin check_shop_operations.py`
   and the logs under `~/Library/Logs/giatlasachcong/` are where to look.
3. **Publish the remedy and promotion policies at setup** (`shop-till-mac.md` §4). Without the first,
   the first complaint dead-ends at the counter.
4. **The owner is reachable, with MFA, for range-priced items.** 20 of the 44 services are ranges;
   each needs the owner's approval within ten minutes. If the owner cannot be reached, those items
   cannot be sold on the machine. `DEC-029` decides whether anyone else may approve.
5. **No agent capability is switched on.** All 13 are `NOT_AUTHORIZED`, and the code enforces it with
   several independent locks. `AGENT-SHADOW-DEFECTS-001` lists what must be fixed before internal
   Shadow — none of it is reachable in staging.

## Questions only the owner can answer

| Question | Why it cannot be an engineering choice |
|---|---|
| `DEC-004` caps damage at 5× **the item's** fee; the code applies it per **line**, and a weight-priced line has no per-item fee. Which? | It sets the shop's liability per complaint. |
| Does a fully refunded item still carry a damage ceiling? | The refund and the compensation are two decisions about the same money. |
| May a self-collect customer prepay at drop-off? Today it can only be recorded by ticking "customer collected". | `DEC-010` / `DEC-023` territory: it changes what the takings figure means. |
| `DEC-029` — who besides the owner may approve a range price? | Authority over price. |
| `DEC-030` — may a remedy credit combine with a promotion? | Two discounts on one bill. |
| `DEC-004` loss — what is owed for a lost item? | No figure exists; the console refuses until one does. |

## Not done, and why

| Item | Reason |
|---|---|
| `SHOP-ALERT-DELIVERY-001` | Needs a topology change or a host relay, and nothing here can watch a Telegram message arrive. An alert path nobody has seen deliver is the defect the operations reviewer found in the existing evidence. |
| `AGENT-SHADOW-DEFECTS-001` | Blocks internal Shadow, not staging: handoffs filed as model drafts, a run deadline equal to its lease, pinned prompt versions never checked against what runs. |
| `OPS-HARDENING-002` | `/healthz` never touches the database; no statement or lock timeouts; no Docker log rotation; a superuser port published in one overlay combination. |
| `API-INTEGRITY-002` | A manual send's recipient comes from the operator, not the approved draft. No automated send exists. |
| Remote branch cleanup | `archive/codex-runtime-delivery-hardening-2026-08-03` and `codex/runtime-delivery-hardening` hold the only copy of the original ten-commit lineage (ADR-0004). Deleting remote refs is refused in this environment; the owner decides. |
