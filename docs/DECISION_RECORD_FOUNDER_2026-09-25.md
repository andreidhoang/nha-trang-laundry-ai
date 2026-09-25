# Decision record — four rulings taken as founder, 2026-09-25

**Authority.** The business owner wrote on 2026-09-25: *"continue with the remaining follow-up items
and make decisions as real founder of the business and lead engineer."* This extends the delegation
recorded under `DEC-028` (2026-09-18). Each ruling below is registered as **delegated**, not as an
owner signature, so the register stays true about who decided what.

**The boundary kept.** `DEC-028` drew it: a delegated decision commits no money, accepts no terms,
asserts no legal basis and names no person. Today's instruction is broader, and the boundary is
kept anyway where it costs the business nothing to keep: **wherever no ratified figure exists, a
decision here routes the money to the owner's approval rather than letting it move by rule.** No
ruling below pays a customer anything the owner did not already ratify, or has not personally
approved in the moment.

Each ruling is reversible, and says how.

---

## DEC-029 — who may choose the price inside a published band: **B**

`docs/DECISION_REQUEST_RANGE_PRICE_AUTHORITY_2026-09.md`.

**Ruling.** The staff member on duty chooses the exact price inside a band the owner has published,
under the same counter attestation `DEC-021` uses to finalise a quote: attributed, immutable, no MFA,
owner-reviewable. The server still refuses every figure outside the band.

**Why, as founder.** 20 of the 44 services are ranges. Under A, each one needs me at the counter with
MFA within ten minutes, per garment. On a day I am out, those customers are turned away or priced on
paper, and a price on paper is a price the business never sees. The band I publish *is* my
authorisation; choosing inside it is using it. `DEC-021` already trusted the same person with the
larger act of committing to a whole quote.

**What would make me change it.** A pattern of careless pricing in the owner review. Then C: staff
below a threshold, owner above — one number and one line.

**Reverse:** `ApprovalAction.SET_RANGE_PRICE` back to `_OWNER_FINANCIAL`.

## DEC-030 — a remedy credit meets a non-stacking promotion: **A**

`docs/DECISION_REQUEST_CREDIT_VS_PROMOTION_2026-09.md`.

**Ruling.** The promotion applies; the credit is not spent and waits for a later order. Staff say so.

**Why, as founder.** It is the cheaper bill for the customer in every programme published so far, and
it cancels no debt. B would make a customer who spends a credit pay more than one who does not,
which is the kind of thing that loses a regular. It is what the system already does, so this ruling
changes no code — it turns a `REQUIRE_HUMAN` that had no answer into one that does.

**Reverse:** C (staff choose per order) is the upgrade; D (publish `stacking_allowed: true`) needs no
code at all.

## DEC-031 — reading `DEC-004` where its words stop

`DEC-004` is ratified and stays exactly as written. It says *"loss/damage compensation is capped at
5x the item's cleaning fee"*, that staff may approve up to 100.000 ₫, and that *"loss ... still
defaults to case-by-case negotiation"*. Four questions its words do not answer, each answered here in
the direction that keeps the owner in control of money nobody ratified.

1. **"The item's cleaning fee."** For a service priced **per piece**, it is the unit price — a line of
   three shirts at 50.000 ₫ caps each shirt at 250.000 ₫, not 750.000 ₫. For a service priced **by
   weight**, no item has its own fee, so it is the fee charged for the bag the item was washed in.
   The first is what the words say; the second is the only number that exists without inventing one.
   The first **lowers** today's exposure; the second leaves it where it is.
2. **Loss.** The 5× ceiling already applies — the ratified text says "loss/damage". What "case-by-case"
   adds is that **every loss claim needs the owner**, whatever the amount: staff record it and propose
   a figure, the owner approves or refuses it. A loss is never staff-authorised. The report window
   is the same 24 hours `DEC-004` gives a visible defect, counted from the handover.
3. **A refunded order.** A refund returns the fee for a service the customer did not receive;
   compensation is for a garment the shop damaged or lost. Both can be owed. So compensation stays
   available, still capped against the fee the shop quoted — but **the owner approves it, whatever the
   amount**, because refunding and compensating on one order is exactly where money can be paid twice.
   The late-delivery credit stays refused on a refunded bill (10% of nothing).
4. **Several items, one complaint.** The staff limit and the ceiling are cumulative per item, as
   `REMEDY-CUMULATIVE-001` already enforces; nothing here changes that.

**Reverse:** each is a rule in `packages/domain/.../remedies.py` with its own test; none is a
migration.

## DEC-032 — may a counter customer pay at drop-off? **Yes, the exact total, nothing else**

**Ruling.** A self-collect customer may pay the exact quoted total when they drop the laundry off,
exactly as `DEC-023` already lets a delivery customer do. Collecting the goods is then recorded
separately, at pickup, by the named staff member who hands them over. An order still completes only
when it is paid, released **and** collected.

**Why, as founder.** Customers here pay when they drop off, often. Today the only way to record that
payment is to tick "customer collected" while the shirts are still in the machine — so staff either
lie to the system or turn the money away. Neither is acceptable.

**What it does not reopen.** `DEC-010` deferred partial payment, deposits, instalments and account
credit — shapes about *amount* or *credit*. This is exact payment in full, the shape `DEC-010` kept,
at a different moment. `DEC-023` made the same distinction for delivery.

**Reverse:** refuse `collected_by_customer: false` on a self-collect order again.

### Addendum — PICKUP_ONLY (2026-09-25)

**The gap.** The ruling above spoke of a customer who *drops laundry off*. A `PICKUP_ONLY` customer
does not: the shop's courier fetches the laundry and the customer comes to the counter for it. The
code read the ruling literally and kept refusing that customer's advance payment
(`COLLECTION_WAS_NOT_BY_THE_CUSTOMER`). Paying when collecting already worked end to end; paying
at the counter before the laundry was finished did not, so the counter had to turn the money away.

**Ruling.** A `PICKUP_ONLY` customer pays **at the shop counter only**, the **exact quoted total**
(`DEC-010` unchanged), at one of two moments:

- **when collecting**, in one step, exactly as a walk-in does (`EXACT_PAYMENT_SELF_COLLECTION`); or
- **in advance**, when present at the counter before the laundry is finished. The payment is the
  walk-in's prepayment (`EXACT_PAYMENT_PREPAID_SELF_COLLECTION`), and the handover is recorded later
  on the `DEC-032` collection command by the named staff member who makes it. The same guards
  apply: no handover until the laundry is `READY_AT_STORE` or `RELEASED`, no completion without the
  collection record, part payments and deposits refused.

**No payment by or to the courier, ever** (`DEC-023`). The order completes only when paid, released
**and** collected. There is still no `RETURN` leg for this mode, and the courier's `PICKUP` leg
closes nothing.

**Why, as founder.** It is the same money at the same counter as a walk-in's prepayment; the only
difference is who carried the bag in. Refusing it gave the customer standing at the counter with
the money no honest option, which is the situation `DEC-032` was ruled to end. The earlier reason for
the refusal, that a paid `PICKUP_ONLY` order had no permitted action left to close it, stopped being
true when `DEC-032` built the collection record.

**Reverse:** in `evaluate_settlement`, refuse `collected_by_customer: false` for `PICKUP_ONLY` again
(`packages/domain/.../settlement.py`), and offer the console's "Khách đã nhận đồ" to walk-ins only.
No migration is involved either way.

---

## What is not decided here

- **A figure for loss above 5×**, a replacement-value policy, or insurance. Not needed while every loss
  goes to the owner.
- **Any agent capability.** All 13 stay `NOT_AUTHORIZED`; this record authorises nothing.
- **`DEC-006`** (model provider and its legal check) and **`DEC-016`** (who staffs the inbound channel)
  name an organisation and a person, which is outside any delegation.
