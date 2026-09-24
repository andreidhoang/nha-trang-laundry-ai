# TASK-staging-review-001 — what five reviewers found before real staff touch it

**Goal:** make the application safe to put in front of real counter staff for staging and feature
testing in daily operation. Not a new capability: every item here is a defect in something already
marked COMPLETE, found by reading, reproduced before it was fixed, and fixed with a test that fails
on the previous code.

**Domains:** `orders_audit`, `pricing`, `business_truth`, `platform`

**Stable work items:** `STAGING-REVIEW-001`, `REMEDY-CUMULATIVE-001`, `CREDIT-RESERVATION-001`,
`CANCEL-REFUND-001`, `CONFIG-REVERT-001`, `ORDER-LOOKUP-001`, `CONSOLE-DEADENDS-001`, and the
follow-ups `SHOP-ALERT-DELIVERY-001`, `AGENT-SHADOW-DEFECTS-001`, `OPS-HARDENING-002`,
`API-INTEGRITY-002`.

**Stage:** PRODUCTION_HARDENING
**Risk:** HIGH — three of these move money the shop owes, has refunded, or may not authorise.

## How it was found

2026-09-24, on `main` at `9287596`, with every gate green (1574 passed, 0 failed) and both
real-API browser scripts passing (70/0 and 86/1). Five independent read-only reviewers, one lens
each: money and domain; API authority and data integrity; the staff console as a shift is run on
it; the agentic runtime and tool boundary; operations and the project's own readiness claim. Each
finding had to name a file, a concrete failure, and whether it was reproduced.

Findings reported by more than one reviewer independently: the remedy repeat (money + API), the
refund still counted as takings (money + API), and the order a customer comes back for being
unreachable (API + console + operations — three of five).

**A green suite was not evidence of any of this.** The browser check that claimed to prove the
price-seal guard passed because a *different* guard refused first; no test anywhere exercised the
seal on an open quote. That is the pattern this packet exists to stop: a check that passes for a
reason other than the one it names.

## Items and the decisions taken on them

A decision below is the lead's, taken under the invariants and the decision register. Each is the
fail-closed reading. None changes a figure the owner ratified; where the owner's figure is
ambiguous the item says so and does not resolve it.

### `REMEDY-CUMULATIVE-001` — one complaint, unlimited credits

Reproduced on PostgreSQL: three proposals of 80.000 ₫ on one incident, each `STAFF_AUTHORIZED`,
each executed: `(3, 240000)`, no owner involved. Splitting a claim below 100.000 ₫ evades the
owner's approval entirely; a proposal against a CLOSED incident is still accepted.

Decision: `DEC-004`'s 100.000 ₫ staff limit and 5× ceiling apply **cumulatively per order line**
(everything live or paid out counts), under a row lock taken before the sum; a proposal needs an
OPEN or UNDER_REVIEW incident; at most one late-delivery credit per order.

### `CREDIT-RESERVATION-001` — a redeemed credit is lost on reprice or expiry

Redemption spends the credit when it writes the credited revision. A reprice composes the next
revision without it; an expired or abandoned quote keeps it spent. The customer loses money the
shop owes under `DEC-004`.

Decision: a quote **reserves** a credit and the order that uses it **spends** it, in the order's
transaction. A reprice carries the reservation; expiry releases it by never having spent it; two
quotes reserving one credit cannot both convert.

### `CANCEL-REFUND-001` — refunded money still counted as collected

A paid order cancelled under a resolution `DEC-024` says is not charged kept its settlement and
read PAID; "tiền đã thu hôm nay" (`DEC-014`) stayed above the drawer by the refunded amount.

Decision: the refund is recorded in the cancellation's transaction, equal to the settled amount
(`DEC-024` says "in full" — no typed figure), and takings are net of refunds on each one's own
Asia/Ho_Chi_Minh business day. **Not done:** `DEC-024`'s instruction that a shop-fault
cancellation opens a `DEC-004` incident. That is a new automated side effect and waits for its
own item.

### `CONFIG-REVERT-001` — the owner cannot go back to an earlier price list

Publishing content identical to an older version returned "already published" and the newer
version stayed in force — for the pricebook, the promotion programme and the remedy policy.

Decision: identical to the version **in force** is still an idempotent no-op; identical to an
**older** version is a new version with that content. No published version is ever changed
(invariant 4); history reads v1 → v2 → v3(=v1).

### `ORDER-LOOKUP-001` — the customer comes back and the order cannot be found

The board is the newest 100, there was no read by id, and a transition needs a row version only
that list returns: at thirty orders a day an order is unworkable after about three days. The
counter ticket (`DEC-013`, "the order is tracked by it") was shown once and never again, and no
order screen showed the amount the settlement form demands to the đồng. Counter-ticket numbering
also used the UTC day, rolling over at 07:00 local.

Decision: read one order by id, find today's order by ticket number, an open-orders filter so age
never hides active work, the ticket and the amount due (read from the stored snapshot — the
console computes nothing) on the board, the detail and the payment panel.

### `CONSOLE-DEADENDS-001` — places a shift gets stuck

"Khách đã chốt giá" (`DEC-021`) erased itself on any failure and showed nothing; a second
re-price was refused as stale while the screen showed the new revision; server refusals reached
staff in English; a Vietnamese-typed weight ("5,5") was refused as a missing fact; a mistyped
payment was told not to retype and cited `DEC-010` as open (it is resolved); the export flow's own
instructions discarded its progress.

### `STAGING-REVIEW-001` — the lead's share

- `restore.sh` could not run on the drill host the runbook provisions (dash rejects `10#`) or on
  the owner's Macs (BSD `date` has no `-d`). Pure POSIX arithmetic now, held by a test that runs it.
- `base-backup.sh` encrypted a truncated stream and wrote a success marker when gzip died part-way.
- CSRF and Origin checks were skipped when one neighbouring cookie was malformed.
- Request fields coerced `true` and `"120000"` into money and distance.
- No test proved an order citing a forged seal on an open quote is refused (invariant 8).
- Runbooks: range prices and complaints described as paper-only after both shipped; the remedy
  policy published by no runbook; no upgrade procedure for a ledger under forward-only migrations;
  the server runbooks created fourteen of sixteen secrets and connected as a role that does not exist.
- Stale status documents that contradicted the queue, removed rather than refreshed.

## Deliberately not done here

| Follow-up | Why it waits |
|---|---|
| `SHOP-ALERT-DELIVERY-001` — backup and disk alerts cannot reach a person (the data checks run on an `internal: true` network; `install.sh` passes no alert credentials) | The fix changes network topology or adds a host-side relay, and nothing here can exercise a Telegram delivery. An alert path nobody has watched deliver is the defect the operations reviewer found in the existing evidence; building another would repeat it. |
| `AGENT-SHADOW-DEFECTS-001` — handoffs filed as model drafts; run deadline equal to the lease; pinned prompt/registry versions never checked against what executes; model-chosen approval hashes bound to no content; the real-customer lock a self-declared label | No agent path is reachable in the deployed system (five independent locks). Every one of these blocks `G1_INTERNAL_SHADOW_READY`, none blocks staging. |
| `OPS-HARDENING-002` — `/healthz` never touches the database; no statement or lock timeouts; no Docker log rotation; the superuser port published on `0.0.0.0` in one overlay combination; apk pins that break uncached rebuilds | Real, individually small, none reachable on the till path in its first weeks. |
| `API-INTEGRITY-002` — a manual send's recipient is the operator's, not the approved draft's; the unknown-send queue ignores store boundaries | No automated send exists and there is one store. Record integrity, not money. |
| The 5× damage ceiling is computed per **line**, `DEC-004` says per **item** | A weight-priced line has no per-item fee. This is a question for the owner, not an engineering choice. |
| A self-collect order paid at drop-off can only be recorded by ticking "collected" | Whether prepayment at drop-off is a counter practice is `DEC-010` / `DEC-023` territory. |

## Done when

Every item above that is not a follow-up has: a test that fails on `9287596` and passes after;
the full gate set green on one commit (`ruff`, `ruff format`, `mypy`, `pytest
--require-postgres-integration`, `verify_contracts.py`, `check_context_drift.py`); and both
real-API browser scripts re-run against a fresh database migrated from empty.

## Rollback

Migrations `0045`–`0047` are forward-only. Rolling back past them is a restore
(`docs/runbooks/restore-drill.md`), which is why `shop-till-mac.md` §8 now takes a base backup
before every upgrade.
