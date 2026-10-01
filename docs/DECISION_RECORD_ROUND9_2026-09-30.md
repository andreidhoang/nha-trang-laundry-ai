# Decision record — round 9 fixes (2026-09-30)

**Authority.** After the whole-app review (`docs/audit/10-APP-REVIEW-2026-09-29.md`) the business
owner wrote on 2026-09-30: "make the best decision and finish". This extends the delegation recorded
under `DEC-028`, `DEC-034`–`DEC-044`. It is **delegated, not signed**, and is recorded that way.

**Boundaries every ruling keeps:** no new price, rate or fee is invented; the only amounts moved are
amounts the ledger already holds or credits already issued under `DEC-004`/`DEC-031`. Nothing here
decides `DEC-001`–`DEC-006`.

The principle behind all five: **a customer ends each order at exactly what the shop's own records
say they owe — never charged twice, never compensated twice, and no order can be stranded.**

## DEC-045 — A no-charge cancellation never compensates twice

**Grounding.** Review finding M3: an order that earned a late-delivery credit could then be cancelled
"no charge" and refunded in full, leaving the credit live — the customer paid nothing *and* kept
15.000 ₫. The domain's own text (`domain/remedies.py`) names this as the place money is paid twice;
only the opposite order of events was guarded.

**Decision.** When an order for which a money remedy was executed is cancelled with a refund:
an **unspent** credit issued from this order is **voided** in the same transaction (audited, the
reason names the cancellation); a credit from this order already **spent** elsewhere, and any other
remedy money already paid out, is **netted** — the refund is what was paid minus that amount, never
below 0, and the netting line is shown on the refund sheet, the receipt and the export.

**Reversal.** Refuse such cancellations instead (the fail-closed behaviour built first).

## DEC-046 — A credit spent on a cancelled no-charge order comes back

**Grounding.** Finding M7: a customer who used a credit on an order the shop then cancelled without
charge got their cash back but lost the credit.

**Decision.** A new credit of the same face value is issued to the same customer, linked by audit to
the refund and to the original credit. When one order both spent and generated a credit, DEC-045
applies first, then DEC-046.

**Reversal.** Refuse such cancellations (fail-closed).

## DEC-047 — A hold pauses the storage fee; it never erases it

**Grounding.** Found while fixing M1: putting a waiting order on hold made the accrued storage fee
disappear, so an operator could hold it, take only the washing price and settle — getting round the
approver-only waiver (`DEC-036`).

**Decision.** Fee accrued up to a hold stays owed; days on hold do not accrue; resuming continues
the count. A rewash (the shop's fault) still restarts the free days, and a withdrawn policy stops
accrual — but in every case a part of the fee **already paid** stays owed-for (returning money is
an explicit refund, never a side effect).

**Reversal.** Previous behaviour (the fee is computed only while the order is waiting).

## DEC-048 — Refunds of unknown method stay out of the drawer figure

**Grounding.** Finding M4: "Tiền trong két" counted bank transfers as drawer cash. Refunds now
record how the money went back; refunds recorded before that have no method.

**Decision.** Those legacy refunds are never guessed: the drawer figure excludes them, says how many
and how much, and is marked incomplete while any are excluded. A failed return trip of unpaid goods
stays refused (the goods left the shop either way).

**Reversal.** An owner-only, audited attestation of each legacy refund's method.

## DEC-049 — End-of-day cash count (đếm két)

**Grounding.** The review found no open/close-shift cash count anywhere; the drawer figure is only
useful if someone counts against it.

**Decision.** Staff record the opening float (counted cash at the start of the business day) and the
closing count. The app shows **expected = float + cash taken − cash refunded − expenses marked "trả
từ két"** (a new yes/no on a Sổ thu chi line, default no), the counted amount and the difference.
The difference is recorded and shown to the owner in the report and the evening summary. Nothing
happens automatically: no adjustment, no blame, no money moved.

**Reversal.** Hide the screen; the records stay.

---

## Round 9b rulings (same delegation)

## DEC-050 — Held days are not waiting days

**Grounding.** DEC-047 made a hold pause the storage fee; the verifier found that "days waiting",
disposal eligibility and the pickup-reminder steps still counted the held days.

**Decision.** While the shop holds an order, the held days count for nothing that measures the
customer's lateness: not the fee, not days waiting, not disposal eligibility, not reminder steps.
One shared clock computes all of them.

**Reversal.** Count held days for reminders and disposal (the fee stays paused).

## DEC-051 — Record-only outbox rows are kept

**Grounding.** Review P6: the outbox counted ~56 never-claimable event types as pending work. The
count is fixed; the rows themselves are refused deletion by the outbox trigger.

**Decision.** Keep them (no deletion). The ops check reports the outbox row count and warns past
1 000 000 rows, when retention is decided again.

**Reversal.** A retention migration with an owner-approved window.

## DEC-052 — Shop CA custody: keep no CA key

**Grounding.** Review P4: the shop's private CA key lived on the serving host and could mint a
certificate for any domain the tablets would trust.

**Decision.** The CA is name-constrained to the console host and its private key is not kept after
issuing; near the leaf's expiry a new CA is minted and re-trusted on each device (runbook).

**Reversal.** Keep the CA key offline (owner's custody) to reissue without re-trusting devices.
