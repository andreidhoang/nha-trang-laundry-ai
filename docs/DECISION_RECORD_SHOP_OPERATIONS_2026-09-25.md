# Decision record — customer records, payments, unclaimed laundry, turnaround, measurement, the owner's evening summary

**Date:** 2026-09-25. **Decided under:** the business owner's explicit instruction of 2026-09-25:
"solve all these issues by reasoning as founder and senior software engineer … Customer names and
phone numbers … Now we should store them as customer database for business purpose. Part payments …
Unclaimed laundry and storage fees … Batches, machine logs, delivery costs … Per-order turnaround
rule … AI daily summary … you make decision best based on Vietnamese business thinking."

This record is written by the lead engineer acting on that instruction. It is **delegated, not
signed**. Two boundaries are kept, as they were for `DEC-029`–`DEC-033`:

1. **No legal basis is asserted by the software.** Where a rule rests on law (personal data, the
   notice a customer is given), the rule ships with a drafted text the owner confirms by
   *publishing* it with a script. Until then the feature refuses, by name.
2. **No money figure the owner has not given is enforced silently.** Every fee, limit and term below
   is written into a policy file as a recommendation; the owner's publication makes it live. Figures
   the owner already confirmed in `BUSINESS_TRUTH_INTAKE.md` are used as they stand.

Each decision is grounded first in what the owner already confirmed (`BUSINESS_TRUTH_INTAKE.md`,
`templates/service-sla.csv`, `templates/business-calendar-rules.csv`,
`templates/machine-master.csv`), then in how a Vietnamese neighbourhood laundry actually runs.

**A correction first.** `docs/CORE_BUSINESS_WORKFLOWS_V1.md` §5 and
`docs/COUNTER_COMPLETENESS_SPEC_V1.md` §1 cited "`DEC-005` (storage fee, unclaimed goods)". `DEC-005`
is the customer-channel decision (resolved 2026-08-18). Unclaimed laundry never had a decision id;
it gets `DEC-036` here, and both documents are corrected.

---

## DEC-034 — The shop keeps a customer list (reopens `DEC-015`)

**What changed.** `DEC-015` said "not yet": no use and no consent event. Both now exist. The owner
wants a customer list for the business, and every Vietnamese laundry that runs well runs on one:
*khách quen* are recognised by phone number, called when their laundry is ready, and remembered
("chị Lan, giặt riêng đồ trắng"). A counter that asks a regular for their ticket every visit loses
to the shop next door that says "dạ chị Lan, như mọi lần ạ".

**Decision.**

- **What is stored.** A customer has a **phone number** (Vietnamese mobile, normalised to
  `+84…`; the key the counter searches by), an optional **name as the customer gives it** ("chị
  Lan", "anh Tuấn — homestay Biển Xanh"), an optional **delivery address**, an optional **short
  note** (≤ 200 characters, for laundry preferences only — the screen says not to write health,
  religion or anything sensitive), a **kind** (`RETAIL` or `BUSINESS`), and two separate consents.
- **Two consents, never merged.**
  - *Lưu thông tin để phục vụ đơn* (required for a record): the shop may keep name, phone and
    address to serve, contact about and deliver orders. The staff member reads one sentence aloud
    and ticks it; the record stores who ticked, when, and the notice version.
  - *Nhận tin ưu đãi* (optional, default **off**): marketing messages. Separate because Vietnamese
    anti-spam rules treat advertising messages separately, and because a customer who wants a call
    when the laundry is ready has not asked for promotions.
  A customer who declines keeps being served as a walk-in with a ticket (`DEC-013` stands).
- **Search.** By full phone, by its last 4 digits, or by name. Operations roles search; the full
  number is shown to the roles that call customers (`OPERATOR`, `OPS_APPROVER`, `OWNER_ADMIN`); an
  `AUDITOR` sees it masked.
- **Linking.** A customer record may be linked to counter tickets and channel bindings, so a
  regular's history, credits and open orders appear in one place. This is exactly the unification
  `DEC-015` declined, and is now intended. The link is made by a staff member, and a channel
  binding is linked only by an explicit staff action.
- **Rights.** A customer may ask to see, correct or delete their record. Deletion erases the
  personal fields and keeps the orders (the 10-year financial schedule of `DEC-008` governs orders,
  not the person's name and number). A record with no order for **24 months** is erased the same
  way by the retention job.
- **Protection.** The phone number is stored encrypted, with a keyed digest for exact search (the
  `NTL_HASH_KEY` pattern the inbox already uses); the last four digits are stored separately for
  the last-4 search. The customer list cannot be exported in bulk except through an
  owner-approved export envelope, which the console never offers by default.
- **The legal step is the owner's.** Vietnam's personal-data rules (Nghị định 13/2023/NĐ-CP and
  the Personal Data Protection Law in force from 2026) require that the customer is told what is
  kept and why, and agrees. The notice text is drafted in
  `docs/POLICY_CUSTOMER_PRIVACY_NOTICE_V1.md`; the owner confirms it by running
  `scripts/publish_privacy_notice.py`. **Until then, creating a customer record is refused with
  `PRIVACY_NOTICE_UNPUBLISHED`**, and the counter keeps working with tickets. Whether the business
  must also file a processing-impact dossier, or is exempt as a small business, is for the owner
  and their adviser to confirm; the software does not claim either.

**Reversal.** Stop creating records (unpublish the notice); run the erasure job over every record.
Orders and money are untouched.

## DEC-035 — Payments as a Vietnamese counter takes them (supersedes the `DEC-010` deferral)

**Grounding.** Owner-confirmed: payment is **cash or bank transfer to the shop's account**; B2B
credit is **supported after approval**, with term, limit, approver and overdue handling to be
decided. `DEC-010` deferred everything beyond exact payment because no B2B relationship existed.
Nha Trang's hotels, homestays and spas are the obvious bulk customers, and they pay monthly.

**Decision.**

- **Methods:** `TIEN_MAT` (cash) and `CHUYEN_KHOAN` (bank transfer). A transfer is recorded when
  the staff member has seen it arrive in the shop's bank app. The optional last digits of the bank
  reference are kept, but nothing is verified automatically, because no bank feed exists.
- **Deposits and part payments (đặt cọc).** Any amount from 1 ₫ up to the amount still owed may be
  recorded, as many times as needed, each with its method. The order shows *Đã trả* and *Còn
  lại*. It is common and expected for dry-cleaning, wedding and áo dài items, and for large bags.
- **No overpayment.** The counter gives change; a payment above what is owed is refused. No store
  credit is created from change.
- **Goods leave only when paid**, except for an account customer (below). This keeps the
  2026-09-25 ruling ("an unpaid counter order leaves the shop only by taking the payment") and
  extends it to "fully paid".
- **Refunds** of money taken (a cancellation after a deposit) go through the existing refund path,
  up to what was paid, with the approval that path already requires.
- **Account customers (khách công nợ).** Only a `BUSINESS` customer whom the **owner** marks as an
  account, with a **credit limit in VND** that the owner types. The terms follow the norm for Vietnamese
  hotel suppliers:
  - **Statement period:** the calendar month.
  - **Due date:** payment is due **by the 15th of the following month**.
  - **Unpaid orders:** an account customer's goods may leave unpaid while the account's
    outstanding total plus this order stays within the limit.
  - **Overdue accounts:** when a statement is overdue, new orders for that account must be paid at
    the counter until it is settled. The owner can lift this, and every lift is recorded.
  - **Recommended starting limit (not enforced):** 3.000.000 ₫. The owner sets each account's
    limit, and until one is set the account is refused as `ACCOUNT_LIMIT_UNSET`.
- **Invoices (hoá đơn điện tử)** stay out of scope: price-includes-tax is unconfirmed (`DEC-022`).

**Reversal.** Unmark accounts; the exact-payment path remains the default shape.

## DEC-036 — Laundry nobody comes back for (lưu kho và thanh lý)

**Grounding.** The shop's own customer terms, as the owner supplied them: *"Lấy đồ trong 20 ngày;
sau đó tính phí lưu kho; sau 60 ngày hiện có điều khoản thanh lý."* The days are the owner's. The
fee amount was never written down.

**Decision.**

- **Days 1–20 after the laundry is ready:** free.
- **From day 21:** a storage fee of **5.000 ₫ per order per day**, capped at **50% of the order's
  total**, so a 60.000 ₫ bag never owes more in storage than half its washing. It is computed by the
  domain from the published policy and added to what is owed at pickup. An `OPS_APPROVER` or the
  owner may waive it in one press, and the waiver is recorded. Waiving is common for regulars, and
  the software makes it easy rather than awkward.
- **The shop must try to reach the customer before anything else happens.** Every contact attempt
  (call, Zalo, SMS, visit) is recorded with its outcome. The "Đồ chờ lấy" list shows each
  ready-but-uncollected order with its days waiting and its attempts.
- **Day 60 and later:** the owner may approve *thanh lý* (in practice, donation to people in need,
  the norm locally), but only when **at least 3 attempts on at least 2 different days** are
  recorded. The order closes with the custody resolution `UNCLAIMED_DISPOSED`. Money already paid
  is kept; money owed is written off.
- **The customer is told at drop-off.** The receipt prints the rule in one line once the policy is
  published. A fee the customer was never told about is a fee the shop should not charge.
- **The fee and the disposal wait for the owner.** They run only once the owner publishes the
  storage policy (`scripts/publish_storage_policy.py`). Until then the list and the attempts work,
  no fee is charged and no disposal is offered.

**Reversal.** Unpublish or republish with different figures; recorded fees stay on the orders they
were charged to.

## DEC-037 — Every order gets a promised-ready time ("hẹn trả")

**Grounding.** Every rule below is already owner-confirmed in `templates/service-sla.csv` and
`BUSINESS_TRUTH_INTAKE.md`:

- ordinary clothing, washed and dried: **≤ 8 hours** from acceptance to ready at the shop;
- the fastest, 2 hours, only after a capacity check, and never auto-promised;
- shoes, curtains, blankets and sheets: **24–48 hours**, with the staff member setting the promise
  after inspection;
- plush toys, bags, leather, toppers, pillows and other special items: **the staff member must set
  the time** (`HUMAN_ETA_REQUIRED`);
- open **08:00–20:00** daily; closed for 6 days at Tết, 30/4 and 1/5, and on 2 ad-hoc days a year.

It was never undecided. It was never wired.

**Decision.**

- **Computed at *Nhận đồ*** (production acceptance, the moment the SLA clock already starts), from
  the order's service categories, and stored on the order. It is the **latest** of its lines'
  promises.
- **Counted in opening hours.** Machines and staff run 08:00–20:00, so "8 hours" means 8 opening
  hours. Laundry accepted at 17:00 is promised at 13:00 the next open day, not 01:00. Closed days
  in the published calendar are skipped. A year whose Tết days are not yet entered makes the
  counter ask the staff member to set the time, rather than promising through Tết.
  **24 h / 48 h are calendar days, rolled into opening hours** (founder ruling, 2026-09-25):
  "24 giờ / 48 giờ" is one or two days as a Vietnamese customer hears it, not 24/48 opening hours.
  Only the 8 h and the express 2 h are opening hours, because they are machine and staff work inside
  the working day. A 24/48 h promise is accepted-at plus that clock time; landing before 08:00
  moves it to 08:00 that day, after 20:00 to 08:00 the next day, and on a published closed day to
  08:00 of the next open day (Friday 17:00 + 48 h → Sunday 17:00; a landing on 30/4 → 08:00 on
  2/5). The Tết-unknown rule applies to that calendar span too.
- **24–48 h items:** the counter offers *24 giờ* or *48 giờ* (default 48: promise late, deliver
  early). **Special items:** the staff member must pick the day and hour. **Express (2 h):** only
  when the staff member chooses it after checking the machines; there is no express surcharge,
  because none is confirmed.
- **Changing a promise (hẹn lại)** needs a reason and is recorded. The on-time figure always counts
  against the **first** promise, so re-promising cannot improve the shop's score.
- **Shown** on the order page, the SLA board (which now ranks by the order's own promise, not the
  one generic rule), the receipt ("Hẹn trả: 13:00 thứ Sáu 26/9"), and the owner's on-time figure,
  whose data quality changes from `RULE_ASSUMED` to `COMPLETE` for promised orders.
- **The late-delivery credit is not applied.** The owner's "late > 2 h vs the confirmed time →
  credit 10% of the next bill" is a delivery commitment. This round records it as a report line
  (deliveries late by more than 2 hours) and does **not** issue credits automatically; wiring it
  to the remedy flow is the next step, and the report says so.

**Reversal.** Unpublish the turnaround policy; orders then carry no promise, as today.

## DEC-038 — Start measuring the shop now, with the least friction possible

**Grounding.** `SHOP-INSTRUMENT-001` is blocked on 4–6 weeks of real cycle, delivery-cost and cost
data, and nothing records any of it. The pilot gate in `BUSINESS_TRUTH_INTAKE.md` §7 lists exactly
what is missing: 10 timed sample loads, 20 logged deliveries, cost/kg, and delivery cost per order.
Nobody in a two-person shop fills in a spreadsheet at 19:30. The capture has to live inside taps
the staff already make.

**Decision.**

- **Machines.** The five confirmed machines (`templates/machine-master.csv`) are seeded as the
  machine list; the owner can add, rename or retire one.
- **Mẻ giặt (a wash cycle) is one tap.** *Bắt đầu giặt* asks "Máy nào?" with the machines as big
  buttons, remembering the last one used. *Giặt xong, kiểm tra đồ* closes the cycle. The start and
  end times come from those taps. Skipping the machine is allowed and counts as "not captured", so
  capture is visible in the data rather than forced at the counter. Each customer's laundry is
  washed separately (the Vietnamese customer expectation, *giặt riêng từng khách*), so a cycle
  belongs to one order.
- **Delivery trips.** When a pickup or return is recorded, the driver adds the vehicle (motorbike
  under 20 kg, car from 20 kg — owner-confirmed), the kilometres, and the money spent (fuel,
  parking, or a Grab/Ahamove fare). This is the `templates/delivery-cost-log.csv` columns, captured
  where the event already happens.
- **Sổ thu chi.** The owner records the shop's spending: electricity, water, chemicals, bags and
  labels, wages, rent, repairs, other. The date, amount and category are required; a note is
  optional. It is how a Vietnamese shop owner already thinks about money, so it is how the software
  asks for it.
- **Margin, only when complete.** The owner's report shows margin for a month only when that
  month's spending has all its core categories (electricity, water, chemicals, wages, rent).
  Otherwise it shows "chưa đủ số liệu" and lists what is missing, following `FR-RPT-002`: the
  remaining 70% is never called profit.
- **Labour minutes per order are not captured.** Asking two people to clock every task would cost
  more than it tells. Wages enter through Sổ thu chi.
- `SHOP-INSTRUMENT-001` stays BLOCKED until the data exists. This decision builds what collects it.

**Reversal.** The capture is optional at every tap; stop asking by retiring the machine list.

## DEC-039 — The owner's evening summary, without a language model

**Grounding.** `DEC-006` (which model provider may see shop data, and on what legal terms) is open
and is the owner's. Sending customer data to a foreign model provider is a cross-border transfer
of personal data under Vietnamese law, a legal act this record will not assert. All 13 AI
capabilities are `NOT_AUTHORIZED` and move only through their gates. `FR-RPT-007` already says an
AI may only restate figures that versioned queries computed.

**Decision.**

- **Build the summary now, deterministically.** At the end of the day (and on demand), the owner's
  Hôm nay shows *Tóm tắt cuối ngày*, generated by a versioned template from the report read model
  and the operational lists. It covers:
  - the orders taken in and finished;
  - the money collected, split into cash and transfer;
  - orders late against their promise;
  - laundry waiting over 20 days, and anything past 60;
  - open complaints;
  - account balances coming due;
  - the day's spending recorded.
  Every sentence names its figure; nothing is inferred. A template makes no mistakes a template
  cannot make.
- **One press to Zalo.** *Sao chép* and *Chia sẻ* hand the text to Zalo, where the owner already
  reads everything. There is no automatic push, because the alert relay's phone test
  (`SHOP-ALERT-DELIVERY-001`) has not passed.
- **A language model is a later, gated upgrade.** Rephrasing the same figures with a model waits for
  `DEC-006` (the owner's) and the capability gates. The template is the baseline it must beat.
- **Recommendation to the owner on `DEC-006`, not a decision:** choose a provider that offers zero
  data retention and no training on shop data, send it **only aggregate figures** (never names or
  phone numbers), and confirm with an adviser whether aggregate business figures count as personal
  data (they should not).

**Reversal.** Hide the card.

---

## What the owner does to switch these on

| Run once | Turns on |
|---|---|
| `scripts/publish_privacy_notice.py` (after reading `docs/POLICY_CUSTOMER_PRIVACY_NOTICE_V1.md`) | Creating customer records |
| `scripts/publish_storage_policy.py` | The storage fee and disposal approval |
| `scripts/publish_turnaround_policy.py` (and entering this year's Tết days) | Promised-ready times |
| Mark an account customer and type its limit on the customer's page | Công nợ for that customer |

Everything else (part payments with a method, the waiting list, machine taps, trip costs, Sổ thu
chi, the evening summary) works as soon as it is deployed, because it records facts rather than
charging or promising anything.
