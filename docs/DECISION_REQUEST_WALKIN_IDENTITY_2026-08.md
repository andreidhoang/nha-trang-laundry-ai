# Decision request — the walk-in customer, and who may see the day's takings

**Opened:** 2026-08-18 · **Owner:** `BUSINESS_OWNER` · **Decisions:** `DEC-013`, `DEC-014`
**Status:** unsigned. Both fail closed today, and the console says so on the screens where it matters.

These two came out of the owner's own review of the staff console. Neither is an engineering
preference; both are questions only the shop can answer, and both are cheap to leave open.

---

## DEC-013 — How is a walk-in customer identified at the counter?

### What happens today

The **Tiếp nhận** screen asks for a *mã khách*. The only thing that produces one is a message the
customer has already sent through a verified channel — the contact binding is created by the
channel envelope, never by staff. So the flow is:

- Customer messaged the shop first → they have a mã khách → intake works.
- Customer walked in off the street → **no mã khách exists, and none can be made** → intake is
  impossible.

That second row is not a UI defect. There is no route that creates a contact, on purpose.

### Why the system refuses instead of just adding a field

A "tên khách" and "số điện thoại" box on the intake form would be the fastest possible fix and the
wrong one. It would record a real person's personal data with no consent event behind it — no
record of what they were told, what they agreed to, or how long it is kept. Adding the field is
choosing a privacy policy by writing a form, which is exactly the class of decision this project
holds back for the owner.

### What the owner is being asked

1. **What may be stored** when somebody walks in — a name? a phone number? a counter-issued ticket
   number and nothing else? "Nothing at all" is a legitimate answer and the cheapest to honour.
2. **How long it is kept**, and whether it is deleted when the order finishes.
3. **How the customer agrees** — a sign at the counter, a spoken line staff must say, a printed
   slip. Whatever it is, it has to be something a person actually does, because the record of it is
   what makes the storage lawful.

### Options

| Option | What it means |
|---|---|
| **Ticket-only, store no personal data** (recommended) | The counter issues a number; the order is tracked by that number. Nothing about the person is stored, so there is no consent question and no retention schedule. Loses the ability to message the customer about their order. |
| Name + phone, with a stated consent line | Staff read a short line aloud and record agreement. Enables order-status messages. Requires a retention schedule and a deletion path, and pulls walk-ins into the same privacy regime as messaged customers. |
| Keep paper for walk-ins | Today's behaviour, made deliberate rather than accidental. Costs nothing to implement; the shop keeps two systems. |

### Until it is signed

Walk-ins are handled on paper. The intake screen says so where staff hit the problem, and the
unsupported-capability register (`#/gaps`) carries the entry. Nothing guesses.

---

## DEC-014 — Which staff roles may see the day's counter takings?

### What happens today

This session added a deterministic money read — the sum of settlements attested at this store
today — and put it at the top of **Hôm nay**. It is gated by `require_operations_staff`, the same
gate the pricing surfaces use, which admits `OWNER_ADMIN`, `OPS_APPROVER` and `OPERATOR`.

That gate was **inherited, not chosen for money.** It is the right default in the sense that it
matches what the server already allowed on adjacent reads, and the console mirrors it exactly
rather than inventing a stricter browser-side rule — a console that hides what the API would answer
teaches staff a boundary that does not exist, and the first person to try `curl` learns it was a
decoration.

### What the owner is being asked

Should a counter operator see the shop's total takings for the day?

| Option | What it means |
|---|---|
| **Leave as-is: operations staff see it** | Matches the current server gate. Staff who take the money can see what has been taken. |
| Restrict to owner and approvers | The figure disappears from Hôm nay for operators, and the console shows the "không đủ quyền" note with the server's reason, as it does for every other refused read. |

### Until it is signed

Nothing is blocked and nothing is at risk — the read is store-scoped, membership-checked, and
refuses a non-member with the same opaque 403 as every other store read. If the answer is
"restrict", the change belongs in the server gate; the console follows automatically.

---

## What the figure is, precisely — so the decision is made about the real thing

`collected_vnd` is `SUM(paid_amount_vnd)` over `order_settlements` for one store, on today's
`Asia/Ho_Chi_Minh` date. That table is append-only, holds one row per order, and the database
enforces `paid_amount_vnd = expected_total_vnd`. Every row is therefore a customer who paid the
quoted total in full and took their goods.

It is **not revenue.** It excludes work in progress, goods delivered but unpaid, and every form of
partial payment, deposit, refund and B2B account — none of which exist in the schema, all of which
are `DEC-010`. The card on Hôm nay says this in Vietnamese, and the assistant still refuses revenue
questions rather than answering them with this number.
