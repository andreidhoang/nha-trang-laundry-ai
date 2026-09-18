# Decision request — who may close a published price band at the counter
# (proposed DEC-029)

**Date:** 2026-09-18
**Owner:** `BUSINESS_OWNER`
**Status:** OPEN — `RANGE-PRICE-001` ships and works whichever way this is answered.
**Trigger:** `RANGE-PRICE-001`, specified in `docs/CORE_OPERATIONS_COMPLETION_SPEC_V1.md` §3.

---

## 0. What this request does not do

It does not block `RANGE-PRICE-001`. That item is built against the approval mapping already in the
code — `SET_RANGE_PRICE → _OWNER_FINANCIAL` — and works today under it. This asks whether that mapping
is the one the shop wants, now that it is about to be used twenty times a day instead of never.

It does not change what a price may be. The published band is the bound in either case, enforced by
the server, and an out-of-band number is refused whoever asks for it.

## 1. The situation

Twenty of the forty-four published services carry a price band: staff look at the garment and choose.
Áo dài truyền thống 80.000–240.000 ₫. Áo lông thú 200.000–400.000 ₫. Vệ sinh sofa
300.000–500.000 ₫.

`RANGE-PRICE-001` makes those sellable. The choice is recorded as an approval, and the approval
policy table already maps it:

```
ApprovalAction.SET_RANGE_PRICE: _OWNER_FINANCIAL
    required_role  OWNER_ADMIN
    obligations    MFA_REQUIRED, SEPARATION_OF_DUTY, RECHECK_RESOURCE_VERSION
    maximum_ttl    10 minutes
```

So under the current table, quoting an áo dài needs the owner, with MFA, inside ten minutes, at the
counter, per garment.

That mapping was written when nothing used it. It is now on the shop's commonest high-value path.

## 2. The question

**Who may choose the exact price inside a band the owner has already published?**

| Answer | Consequence |
|---|---|
| **A. Leave it: `OWNER_ADMIN` + MFA** (current code) | Every range-priced garment needs the owner at the counter within ten minutes. In a one-person shift, or when the owner is out, the shop either turns the customer away or writes the price on paper — and a price on paper is a price the system never sees. |
| **B. `_COUNTER_ATTESTATION`** — `OPERATOR`, attribution, 30 min, no MFA (recommended) | The staff member on duty chooses, and the record is their name against the number, immutable, owner-reviewable. Identical to how `DEC-021` already lets that same person finalise a quote. |
| **C. A threshold** — staff below some amount, owner above | Matches `DEC-004`'s shape, where staff may approve compensation to 100.000 ₫. Needs one number this document does not supply, and the number would sit oddly against bands whose *minimum* already exceeds it. |

## 3. The argument for B

**The band is already the owner's authorisation.** `min_price_vnd` and `max_price_vnd` are published
columns under an immutable pricebook version. Publishing 80.000–240.000 ₫ is the owner stating that
every number in that interval is an acceptable price for that garment. Choosing inside it exercises an
authority already granted rather than creating one. The server refuses everything outside it.

**`DEC-021` already decided the harder version of this question.** The owner ratified, in their own
words, that *"nhân viên đang trực quầy được chốt giá"* — the staff member on duty may finalise a
quote — and that the control is attribution and immutability rather than a second signature. Finalising
a quote commits the shop to a total. Choosing inside a published band is a smaller act than that.

**The reasoning `DEC-021` gave applies here unchanged:**

> a shift with one person on duty is a real shift and a system that blocks it would be worked around
> on paper — which would cost the shop both the control and the record.

Option A's failure mode is exactly that. It does not prevent a bad price; it prevents the system from
knowing the price.

## 4. The argument for A

It is the only place in the system where a staff member sets a price with real money attached and no
formula behind it. The bands are wide — áo dài truyền thống spans 160.000 ₫, giặt gấu bông spans
180.000 ₫ — so the difference between a careless choice and a careful one is larger than the whole
value of a standard wash. MFA and owner review are a real control over a real risk.

The counter-argument is that the control it buys is availability-limited: it works when the owner is
present, and when they are not it buys nothing at all, because the transaction leaves the system.

## 5. Engineering position

Recommend **B**, with the band enforced by the server exactly as specified, the choosing staff member
named on the immutable attestation, and owner review over the whole set. If the shop later finds a
pattern of careless pricing, C becomes available with one number and one line of code.

This is a recommendation, not a decision. Changing who may approve a financial action is policy, and
`RANGE-PRICE-001` deliberately ships against the table as it stands so that this signature is not on
its critical path.

## 6. What changes if you sign B

One line in `packages/domain/src/nha_trang_laundry_domain/approvals.py`:

```python
ApprovalAction.SET_RANGE_PRICE: _COUNTER_ATTESTATION,
```

plus its test. No migration, no data change, no console change. It is reversible in the same one line.

## 7. Signature block

- **Decision:** A / B / C: ______
- **If C, the threshold:** ______ ₫
- **Name:** ______  **Date:** ______
