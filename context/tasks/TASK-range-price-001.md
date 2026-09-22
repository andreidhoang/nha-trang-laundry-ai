# TASK-range-price-001 — let a staff member close a published price band

**Goal:** make the twenty range-priced services sellable, by letting a named staff member choose an
exact amount inside the published band and recording that choice as an attributed, immutable
attestation bound to the revision.

**Domains:** `pricing`, `orders_audit`

**Stable work item:** `RANGE-PRICE-001`

**Stage:** PRODUCTION_HARDENING
**Risk:** HIGH — it is the first path in which a human's number becomes the price of an order. The
server must own the bound absolutely, and the attestation must be as immutable as a settlement.

## Why this exists

`templates/services-pricebook.csv` publishes 44 services. Twenty of them carry
`min_price_vnd != max_price_vnd` and are marked `RANGE_ONLY_HUMAN_FINAL`. `quote_composition.py:466`
refuses to compose a revision for any of them:

```python
if result.requires_human or result.finality is not QuoteFinality.ESTIMATE:
    reasons.add(ErrorCode.RANGE_PRICE_REQUIRES_HUMAN.value)
```

Those twenty are where the margin is — áo dài truyền thống 80.000–240.000 ₫, áo lông thú
200.000–400.000 ₫, vệ sinh sofa 300.000–500.000 ₫, giày da lộn 150.000–250.000 ₫. The shop can quote
a 20.000 ₫/kg wash through this system and cannot quote any of them. Forty-five per cent of the
published catalogue is unsellable.

The refusal was right when written. `quote_composition.py:459` says composing a range revision "would
mean deciding how a range interacts with totals, orders and settlement," and that was not that item's
scope. This is that scope.

**No policy is decided here.** The published band is the owner's authorisation: publishing
80.000–240.000 ₫ under an immutable pricebook version is the owner stating that every number in that
interval is acceptable. `DEC-021` supplies the recording mechanism — a named staff member attests the
price the customer agreed to. A staff-chosen in-band price is a price read aloud.

## Required design

**The server owns the bound; the human owns the number.** One exact non-negative integer VND amount
per range line. The server validates it against `[min_price_vnd, max_price_vnd]` of the line's
*published pricebook version* — never against a client-supplied band, and never against the current
pricebook if the revision was priced against an older one. Invariant 3 holds: deterministic code
decides what is permissible, the human supplies a fact inside it.

**Use the approval that already exists, unchanged.** `ApprovalAction.SET_RANGE_PRICE` maps to
`_OWNER_FINANCIAL` and resource type `QUOTE_REVISION` (`approvals.py:103`, `:119`). Do **not** retune
`APPROVAL_POLICIES`. Who may approve a financial action is policy; the operational cost of this
mapping is raised for the owner in `docs/DECISION_REQUEST_RANGE_PRICE_AUTHORITY_2026-09.md` and is
not this item's to decide. `_authorize_decision` already admits `OWNER_ADMIN` for every action.

**The amount binds a rendered hash.** Invariant 8. The envelope binds the exact revision, its
`resource_version`, `snapshot_hash` and `rendered_hash`. Editing any line invalidates the approval.
This is what stops an amount approved for one garment being reused for another.

**Finality becomes real.** A revision whose range lines all carry an approved in-band amount is
`QuoteFinality.APPROVED_EXACT`. `QuoteFinality.RANGE` becomes persistable for a revision presented as
a band before any amount is chosen — a legitimate thing to show a customer that today cannot be
stored at all.

**The old refusal survives.** `RANGE_PRICE_REQUIRES_HUMAN` is still emitted for a range line with no
approved amount, because that is the truth. It is what lets the console say *cần nhân viên chốt giá
trong khoảng* rather than showing nothing.

## New refusal codes

Register each with the invariant or decision that causes it, following `REFUSAL_DECISIONS`
(`settlement.py:69`), whose completeness is pinned by `test_settlement_policy.py:103`.

| Condition | Code |
|---|---|
| amount outside the published band | `RANGE_PRICE_OUT_OF_BAND` |
| amount supplied for a non-range service | `RANGE_PRICE_NOT_APPLICABLE` |
| band read from a pricebook version other than the revision's | `RANGE_PRICE_PRICEBOOK_MISMATCH` |

## Console

`#/quotes` already renders a band via `format.moneyRange` and `components.priceStateBadge`. Add, per
range line: the published band shown as the bound, a money input validated client-side against it,
and the line and order totals that result. Follow `components.pricingCliffNotice` — warn *before*
submitting, not after refusing. Server refusals render through the existing `reasonCodeList` path so
staff see the code and a Vietnamese reason.

Money input must use `format.parseDong`, which already refuses a comma and treats `170000.5` as a
typing error rather than a number.

## Constraints

- No arithmetic on money in the route layer. `TASK-quote-command-001` says the item is not done if a
  reviewer can find any.
- Integer VND only; invariant 2.
- The chosen amount is never defaulted to the midpoint, the minimum or the maximum. A caller that
  supplies nothing gets `RANGE_PRICE_REQUIRES_HUMAN`, not a guess.
- `APPROVAL_POLICIES` and `APPROVAL_RESOURCE_TYPES` are not edited.
- Do not widen `enforce_order_projection_update`.

## Required tests

- A quote for `DC_AO_DAI_TRADITIONAL` at 150.000 ₫ persists, presents a total, reaches
  `ACCEPTED_FINAL`, converts to an order, and settles through the existing exact-payment path.
- The same at 250.000 ₫ and at 79.999 ₫ is refused `RANGE_PRICE_OUT_OF_BAND`; nothing is persisted.
- A range line with no amount yields `UnresolvedQuote` carrying `RANGE_PRICE_REQUIRES_HUMAN`.
- An amount for `STD_WASH_DRY_LT6` is refused `RANGE_PRICE_NOT_APPLICABLE`.
- Editing a line after approval invalidates the approval.
- An amount validated against a *different* pricebook version is refused.
- Property test: for every one of the twenty range services, `min` and `max` are accepted and
  `min - 1` and `max + 1` are refused.
- Console: the band is shown, an out-of-band entry is warned before submit, and a server refusal
  renders its code and Vietnamese reason.

## Done when

All of the above pass, `uv run mypy apps packages` is clean, `verify_contracts.py` and
`check_context_drift.py` pass, and a browser run of `scripts/verify_console_interaction.py` completes
a range-priced quote end to end against a real API.

---

## Follow-up: `RANGE-APPROVAL-VISIBILITY-001`

Recorded 2026-09-22, after adversarial review of the shipped item.

This packet made the owner's `SET_RANGE_PRICE` approval the only second-party control over the
amount a staff member picks inside a band — and shipped without the owner being able to *see* that
amount. The envelope held only `rendered_hash`, the proposed amounts were persisted nowhere, and the
approvals card linked to `#/quotes`, which renders the published **band**, because the revision the
envelope binds is the one before any price was chosen. Every sentence on that screen was true and the
owner still could not see the number.

So a staff member could agree 150.000 ₫ with the customer, propose 240.000 ₫, and the approval passed
it through. Two independent lenses found it; it was the oldest unfixed finding in the project.

The original reasoning was not wrong, only incomplete. `approvals.py` states that `rendered_hash` is a
digest of a rendering this system does not store, and that comparing it against anything stored would
be theatre. That is correct about **verification** and says nothing about **display**. Invariant 8
binds an approval to exact rendered content — and for a human to approve content, they must see it.

`RANGE-APPROVAL-VISIBILITY-001` stores the proposed amounts for the approver to read, keyed to the
envelope, and leaves the application path untouched: it still re-derives the digest from the amounts
in hand and refuses unless it equals the approved one. A test forces the immutability trigger aside,
rewrites a stored 150.000 ₫ to 240.000 ₫, and proves applying 240.000 ₫ is still refused while
150.000 ₫ still prices at 150.000 ₫. If the stored copy ever became the thing the hash is checked
against, those two assertions would swap.

Evidence: `evidence/delivery-loop/RANGE-APPROVAL-VISIBILITY-001.yaml`.
