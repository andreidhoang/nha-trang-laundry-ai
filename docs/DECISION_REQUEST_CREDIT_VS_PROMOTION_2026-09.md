# Decision request — when a remedy credit and a promotion cannot both apply
# (proposed DEC-030)

**Date:** 2026-09-18
**Owner:** `BUSINESS_OWNER`
**Status:** OPEN. The system refuses this case and tells staff; nothing is blocked while it is open.
**Trigger:** `PROMO-WIRING-001`, and an engineering error described in §4 rather than hidden.

---

## 0. What this asks

A customer is owed a remedy credit under `DEC-004`. The shop is running a promotion whose published
document says `stacking_allowed: false`. The customer brings in their next order.

**Which one do they get?**

## 1. Why the system cannot answer it

The promotion engine already answers `REQUIRE_HUMAN` here — `PROMOTION_STACKING_REQUIRES_HUMAN`. It
has said so since it was written, and it is right to: nothing in `DEC-004`, `DEC-002` or the
promotion documents says which instrument wins, so there is no rule to apply.

Both figures are real money and they point in opposite directions:

- A **remedy credit** is a debt. The shop failed this customer once and promised to make it good.
- A **promotion** is an offer the shop chose to make to everybody, and chose to say may not stack.

## 2. What the shop does today

The credit is **refused, and not spent.** Staff are told plainly: this order already carries a
promotion that cannot be combined with the credit, the credit has **not** been used and is still
available, and a person has to choose which the customer gets for this order.

Nothing is lost either way. The credit survives the refusal and can be spent on a later order.

## 3. The options

| Answer | Consequence |
|---|---|
| **A. The promotion wins; the credit waits** (current behaviour, recommended) | The customer pays the promoted price — the lower of the two in every case the shop has published so far — and keeps the credit for later. Nobody is worse off, and the debt is not cancelled. |
| **B. The credit wins; the promotion is withdrawn** | The customer spends the credit and pays the *no-programme* price. In the worked example that is 109.000 ₫ against the 84.000 ₫ the promoted quote showed. **A customer who spends a credit pays more than one who does not.** |
| **C. Staff choose, per order** | Honest, and matches what the engine already says. Needs a screen, an attributed attestation like `DEC-021`'s, and a rule for what happens when staff choose wrong. More work than either A or B. |
| **D. Let them stack after all** | Changes the promotion document rather than this rule: publish `stacking_allowed: true`. Available to the owner today, with no code change. |

## 4. An engineering error, recorded rather than buried

Option **B was briefly implemented**, on 2026-09-18, on my instruction and not the owner's.

The reasoning was that a debt outranks an offer. Read alone that is defensible. What it missed is
arithmetic: withdrawing the promotion *raises* the bill relative to the quote the customer was just
read. Spending a credit made the customer pay more. Two independent adversarial reviews caught it,
and one named the deeper fault exactly — the engine's `REQUIRE_HUMAN` had been converted into "an
automatic, unsupervised outcome", which is a policy decision an agent may not take.

It never reached `main`. It is written down because the next person to reach for B should see why it
looks right and is not.

## 5. Recommendation

**A.** It is what the system does now, it is the cheaper answer in every published case, it cancels no
debt, and it keeps the promise `DEC-004` made. If the shop later finds staff want the choice,
**C** is the upgrade and this decision is not a one-way door.

If you want **D** instead, that is not a decision for this document — publish a promotion document
with `stacking_allowed: true` and the two combine with no code change.

## Outcome — 2026-09-25

**Decided: option A**, as `DEC-030` in `context/DECISION_REGISTRY.yaml`, under the owner's delegation of 2026-09-25 (`docs/DECISION_RECORD_FOUNDER_2026-09-25.md`). It is recorded there as delegated rather than signed, so the signature block below is left blank on purpose: the owner may still sign it, or reverse it as the record describes.

## 6. Signature block

- **Decision:** A / B / C / D: ______
- **Name:** ______  **Date:** ______
