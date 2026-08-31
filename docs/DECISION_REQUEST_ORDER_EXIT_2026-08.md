# Decision request — how an order leaves the system when the normal path does not apply

**Date:** 2026-08-31
**Status:** OPEN. Registered as `DEC-024`, owner `BUSINESS_OWNER`, fail-closed `REQUIRE_HUMAN`.
**Trigger:** the R1 production release. Both cases below are week-one counter events, and today one
of them cannot be recorded at all while the other can be recorded without recording the money.
**Measured:** 2026-08-31 against `packages/domain/src/nha_trang_laundry_domain/orders.py`.

---

## 0. What this request does not do

It does not choose. It states two defects precisely, gives the options with their consequences, and
recommends one of each. Custody of a customer's laundry and the money already taken for it are the
owner's to decide; an engineer choosing here would be this system deciding money, which
`AGENTS.md` forbids.

## 1. A production exception is a trap

`transition_production` (`orders.py:181-188`) tests `current in {RELEASED, EXCEPTION}` and raises
`"production is terminal"` before any exit can be reached, and the `EXCEPTION` branch sets
`production_resume_status=None`, discarding the state there would have been to return to.

**`EXCEPTION` has zero outgoing edges.** It is reachable from every non-terminal, non-hold state.

So: a customer pays at the counter, the laundry goes in, and a machine fault or a stain found during
quality check is recorded as an exception. That order can now never reach `COMPLETED` and never
reach `CANCELLED`. It sits `ACTIVE` forever, on the board, paid, with the goods in the shop.

Compare `ON_HOLD`, which the same function models correctly: it stores `production_resume_status`
and permits exactly one exit, back to where it was held from.

### Options

| Answer | Consequence |
|---|---|
| **A. An exception is resumable, like a hold** (recommended) | `EXCEPTION` records the state it interrupted and may return to it, or move forward to `QUALITY_CHECK`, when staff resolve it. The stain is re-treated, the load is rewashed, the order completes normally. Matches `DEC-004`, which already gives staff a free-rewash decision within 7 days on store fault, and matches how `ON_HOLD` is already built. |
| **B. An exception is terminal and forces a cancellation** | The order leaves through the cancellation path, which then has to answer the refund question in §2 for every exception. Turns a stain into a cancelled order and a refund, which is not how a laundry works. |
| **C. Both: resumable, and a terminal `ABANDONED` for goods that cannot be returned** | Honest about loss and damage, which `DEC-004` caps at 5× the cleaning fee. More states, and the compensation path is already a separate incident record rather than an order state. |

**Recommendation: A.** An exception is an interruption, not an outcome. The shop's own remedy
policy (`DEC-004`) assumes the laundry gets finished.

## 2. An order can be cancelled after the laundry is washed and released

`transition_commercial` permits `CONFIRMED → CANCELLED` (`orders.py:58-77`) and the only guard on
any cancellation (`:121-126`) is conditioned on `state.commercial is CANCELLATION_REVIEW`. The four
other edges into `CANCELLED` reach the mutation with **no check of production, intake, balance, or
delivery legs**.

Two consequences, and the second is the sharper one:

1. An order that has been washed, quality-checked and `RELEASED` can be cancelled outright, and the
   money is never recorded. `COMPLETED` requires `balance in {PAID, ON_ACCOUNT}`; `CANCELLED` requires
   nothing.
2. **The guard is inverted in practice.** `cancellation_approved` and
   `custody_and_financial_resolution_recorded` both default `False`, and
   `packages/db/.../orders.py:413` never passes either — so `CANCELLATION_REVIEW → CANCELLED`, the
   one path with a guard, can *never* succeed over HTTP, while `CONFIRMED → CANCELLED`, which has no
   guard, always does. The reviewed path is the impossible one.

The database trigger (`migrations/0008:84-85`) blocks `ACTIVE → CANCELLED` but not
`CONFIRMED → CANCELLED`, and reads neither production status nor balance.

### Options

| Answer | Consequence |
|---|---|
| **A. Cancellation is free before work starts and reviewed after** (recommended) | `CONFIRMED → CANCELLED` stays open only while production is `NOT_STARTED` and nothing has been taken in. Once work has started, cancelling routes through `CANCELLATION_REVIEW`, and the two guard flags become real inputs a named staff member sets — the same attributed-attestation shape `DEC-021` and `DEC-023` already chose. |
| **B. Any cancellation requires review** | Simplest rule, one path. Costs a counter interaction on the ordinary case of a customer changing their mind thirty seconds after confirming, which staff would work around on paper. |
| **C. Leave it open and record the money separately** | Rejected on its face: it is the current behaviour, and the current behaviour loses money silently. |

**Recommendation: A**, with the question the owner still has to answer inside it:
**what must be true before a started order may be cancelled?** Concretely — is the customer refunded
in full, refunded less work done, or not refunded; and who hands the laundry back. That answer
becomes the meaning of `custody_and_financial_resolution_recorded`, which today is a boolean nobody
sets and therefore means nothing.

## 3. What is not being asked

- The compensation ceilings. `DEC-004` already sets them: 5× the cleaning fee for loss or damage,
  100,000đ approvable by staff, above which the owner approves.
- Partial payment or refunds as a settlement shape. `DEC-010` deferred those deliberately and this
  request does not reopen it. A refund here is a physical act at the counter that the order records
  having happened, not a payment instrument.

## 4. Signature block

Fill and commit. While unsigned, both defects stay as they are and R1 does not cut over: an
exception order cannot be closed, and a late cancellation loses the money.

```yaml
decision_dec_024_order_exit:
  production_exception:        # A | B | C
  late_cancellation:           # A | B | C
  cancellation_precondition:   # what must be true before a started order may be cancelled
  refund_position:             # full | less work done | none | case by case
  who_returns_the_goods:
  owner: BUSINESS_OWNER
  decided_at:
  owner_written_approval:      # true
```
