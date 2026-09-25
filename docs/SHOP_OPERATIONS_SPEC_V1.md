# Shop Operations Spec V1 — customers, payments, promises, unclaimed laundry, measurement, the evening summary

Status: APPROVED FOR BUILD 2026-09-25, implementing `DEC-034`–`DEC-039`
(`docs/DECISION_RECORD_SHOP_OPERATIONS_2026-09-25.md`). Builds on
`docs/COUNTER_COMPLETENESS_SPEC_V1.md` (round 6, merged first) and keeps every invariant of
`docs/STAFF_CONSOLE_REDESIGN_SPEC_V2.md` §7 and `AGENTS.md`.

Round 6 left these on `#/gaps` as decided-not-to-build or blocked. The owner has now decided them.
This spec turns each decision into server contracts, console flows and proofs.

## 0. Rules every slice keeps

- **Fail closed on the owner's publication.** Any behaviour that charges money, promises a time or
  stores personal data under a notice runs only while the owner's published policy version exists.
  The policy is published with a `scripts/publish_*.py` script that mirrors
  `publish_messaging_policy.py` (configuration version, `decision_ref`, the actor). Without it the
  route refuses by a named code, and the console says in tier 1 what the owner must do.
- **Money:** integer VND, computed only in `packages/domain`, summed only in SQL; the console formats.
- **Personal data:** phone numbers never appear in logs, audit payloads, domain-event payloads,
  outbox payloads, exports or error messages. Audit rows carry the customer id and which field
  changed, never the value. The staff-console privacy tests are extended, not weakened.
- **Every write:** `Idempotency-Key`, `If-Match` on versioned rows, mutation, event, audit and
  outbox in one transaction, store-scoped fail-closed authorisation with the MFA rule its
  neighbours use.
- **Every list:** bounded, `truncated`, store-scoped server-side.

## 1. `CUSTOMER-001` — customer records (`DEC-034`)

**Data** (migration, forward-only):
- `customers`:
  - identity: `id`, `store_id` (the shop's one store today; a customer belongs to the store that
    recorded them);
  - phone, stored three ways: `phone_ciphertext` (the value encrypted at rest with the existing
    payload encryption), `phone_digest` (keyed HMAC of the normalised `+84…` number, unique per
    store) and `phone_last4`;
  - optional details: `display_name`, `delivery_address`, `note` (≤ 200);
  - `kind` `RETAIL|BUSINESS`;
  - service consent: `service_consent_notice_version`, `service_consent_by`,
    `service_consent_at` (all required);
  - marketing consent (all nullable): `marketing_consent_at`, `marketing_consent_by`,
    `marketing_withdrawn_at`;
  - lifecycle: `last_activity_at`, `erased_at`, `row_version`, timestamps.
  - **Erasure** nulls every personal column, sets `erased_at`, and keeps the row so orders keep
    their foreign key.
- `customer_links`: customer ↔ counter ticket / channel binding (`link_kind`, `ref_id`, `linked_by`,
  `linked_at`), unique on `(link_kind, ref_id)`.
- `orders.customer_id` and `order_requests.customer_id`, nullable (a walk-in stays a ticket).

**Domain:** Vietnamese mobile normalisation and validation (`0xxxxxxxxx`, `+84xxxxxxxxx`,
`84xxxxxxxxx`, spaces and dots tolerated; landlines allowed with their area code; anything else
refused `PHONE_INVALID`).

**API** (`/internal/v1/stores/{store}/customers`):
- **Create:** `POST` with phone, optional name, address, note and kind, and
  `service_consent: true` (required, staff attestation), `marketing_consent: bool`. It is refused
  with `PRIVACY_NOTICE_UNPUBLISHED` until the notice is published. A duplicate phone answers 409
  with the existing customer's id.
- **Search:** `GET ?q=` matches a full phone by digest, 4 digits by `phone_last4`, and anything
  else as a case- and diacritic-insensitive name search (Vietnamese `unaccent`-style folding done
  in Python or SQL). Bounded at 20, newest activity first.
- **Detail:** `GET /{id}` returns the profile (phone full or masked by role), open orders, the last
  10 orders with ticket, total and status, unused credits, links and account status (after
  `PAYMENT-002`).
- **Edit:** `PATCH /{id}` with `If-Match`; consent changes are recorded as consent events.
- **Erase:** `POST /{id}/erase` (owner or approver, MFA, reason `CUSTOMER_REQUEST|RETENTION`).
- **Link:** `POST /{id}/links`.
- **Customer on an order:** `POST …/order-requests` accepts `customer_id` as an alternative to a
  ticket or binding; the order inherits it.
- **Retention:** a script, `scripts/run_customer_retention.py`, erases records with no activity
  for 24 months, mirroring the existing retention jobs.
- **Publication:** `scripts/publish_privacy_notice.py` with the text in
  `docs/POLICY_CUSTOMER_PRIVACY_NOTICE_V1.md` (drafted by this slice: what is kept, why, for how
  long, how to ask for deletion, the shop's legal name and hotline from `BUSINESS_TRUTH_INTAKE.md`).

**Console:**
- `#/new` step 1 becomes **one search field**, "SĐT hoặc tên khách", above the walk-in button.
  - **A match:** one tap binds the customer and goes to step 2, with their name shown on the
    receipt.
  - **No match:** "Thêm khách mới" opens a sheet with the phone prefilled, name and note fields,
    the consent sentence read aloud and a tick, and an optional marketing tick. After saving, it
    goes to step 2.
  - **Unchanged:** "Khách vãng lai — phát phiếu" stays as one tap, and the channel hand-off from
    round 6 stays.
- `#/customers` (under Thêm, and in the tab bar's search on the Đơn hàng screen): search, then
  **the customer's page**:
  - their name and phone, with **Gọi** (`tel:`) and **Zalo** (`https://zalo.me/<phone>`) links;
  - open orders first, then history and credits;
  - buttons for **Sửa**, **Xoá thông tin (khách yêu cầu)** and, after `PAYMENT-002`, the account.
- **The order page** shows the customer's name and a link to them when set.

**Proof:**
- **Domain:** phone normalisation cases.
- **Repository:**
  - uniqueness by digest per store;
  - search by each mode;
  - erasure keeps orders;
  - the retention job;
  - no phone value in any audit, event or outbox payload, asserted by scanning the rows.
- **API:** refusal before publication; roles and masking; `If-Match`; idempotent create; cross-store
  404.
- **Real API:** publish the notice; a new customer at the counter; the second visit found by the
  last 4 digits with 1 tap; history shown; erasure.

## 2. `PAYMENT-001` — part payments with a method (`DEC-035`, counter half)

**Data:** `order_payments` (`id`, `order_id`, `store_id`, `amount_vnd > 0`,
`method TIEN_MAT|CHUYEN_KHOAN`, `bank_ref_last` nullable, `recorded_by`, `recorded_at`,
`kind PAYMENT|REFUND`), append-only. The order's balance becomes
`UNPAID | PARTIALLY_PAID | PAID` (extend the enum and the projection and CHECK constraints;
existing `PAID` rows migrate as one payment of their settled amount with method `TIEN_MAT` marked
`legacy`).

**Domain** (`settlement.py`): `evaluate_payment(owed, paid_so_far, amount)` accepts 1 ≤ amount ≤
owed − paid and refuses overpayment (`OVERPAYMENT_REFUSED`, "trả lại tiền thừa cho khách"). Pickup
(`COLLECT` / `HAND_OVER` / `RELEASE` for self-collect) requires `PAID`, or an account order within
its limit (`PAYMENT-002`). `SETTLE` / `PREPAY` in `next_steps` become `TAKE_PAYMENT`, legal while
owed > paid, with the order's primary step logic unchanged in spirit (pay, then hand over). Adding
the storage fee to what is owed is `UNCLAIMED-001`'s; this slice exposes an `owed_vnd` that is the
quoted total, and a place (a list of charges) where a later charge can be added.

**API:** `POST /internal/v1/orders/{id}/payments` (amount, method, optional bank ref, If-Match) —
the existing settlement route keeps working for the exact-total case (it becomes one payment of the
total), so older clients do not break. `GET` of the order carries `payments[]`, `paid_vnd`,
`remaining_vnd` (computed in SQL or the domain, never in the console). Takings (day summary, report)
split by method.

**Console:** the order's money section shows *Tổng · Đã trả · Còn lại* and one primary *Thu tiền*
that opens a sheet: the remaining amount prefilled (tap-to-edit for a deposit), *Tiền mặt* /
*Chuyển khoản* segmented (transfer shows "Đã thấy tiền vào tài khoản" tick and optional ref). The
payment list is under the section. Hôm nay's money splits cash / transfer.

**Proof:** domain tables (deposit, two part payments, exact rest, overpayment refused, pickup
refused while partially paid); migration of existing paid orders (sum preserved); takings by method
equal the payments; real API: 50.000 ₫ deposit by transfer at drop-off, rest in cash at pickup,
hand over.

## 3. `PAYMENT-002` — account customers (công nợ) (`DEC-035`, B2B half; after `CUSTOMER-001` and `PAYMENT-001`)

**Data:** `customer_accounts` (`customer_id` BUSINESS only, `credit_limit_vnd`, `status
ACTIVE|SUSPENDED`, `overdue_block_lifted_until` nullable, set by owner only), `account_statements`
(month, opening, charges, payments, closing, due date = 15th of next month, computed by SQL,
frozen at month close by a script).

**Rules:** an order of an account customer may be handed over unpaid when outstanding + this order's
remaining ≤ limit and no statement is overdue (or the owner lifted the block); it then counts as
`ON_ACCOUNT` (a balance state or a flag — engineer's call, justified). A payment against the account
allocates to the oldest unpaid account orders first. Limit unset → `ACCOUNT_LIMIT_UNSET`.

**Console:** on a BUSINESS customer's page (owner): *Mở công nợ* with limit; the account card:
outstanding, limit, current statement, overdue banner, *Thu công nợ*; statement view printable. The
order page offers *Giao đồ — ghi công nợ* when legal.

**Proof:** limit arithmetic in the domain; overdue block; allocation order; statement totals equal
the orders and payments; real API: a homestay account with two unpaid orders and one payment.

## 4. `PROMISE-001` — promised-ready time (`DEC-037`)

**Domain:** `promise.py` — pure. Inputs: accepted_at, the order's lines' service categories mapped to
`service-sla.csv` scopes (`standard_weight` → STANDARD 8h; `shoes`; `bedding` and curtains → 24/48
staff choice; everything else → HUMAN_ETA_REQUIRED), the published opening hours and closed dates,
staff choices. Output: promised_at, or `HUMAN_ETA_REQUIRED` with which lines need it. Business-hour
arithmetic with closed days; a year with no Tết dates published → HUMAN_ETA_REQUIRED for any promise
crossing late January–February of that year (fail closed).

**Data:** `orders.promised_ready_at` (first promise, immutable), `order_promise_changes` (new time,
reason, by, at). Policy: `scripts/publish_turnaround_policy.py` from `templates/service-sla.csv` +
`templates/business-calendar-rules.csv` + this year's Tết dates argument.

**API:** `RECEIVE` step takes optional `promise_choice` (`H24|H48|EXPRESS_2H|CUSTOM` + `custom_at`)
and refuses `PROMISE_REQUIRED` when the domain says a human must set it; the order view carries
`promised_ready_at`, `current_promise_at`, `promise_state` (`ON_TRACK|DUE_SOON|LATE|MET|MISSED`
computed by the server at read time). `POST /orders/{id}/promise` changes it with a reason. SLA board
ranks by the order's promise when present (falls back to the stated rule and says so).

**Console:** Nhận đồ shows "Hẹn trả: 13:00 thứ Sáu 26/9" before the press, with 24/48h chips or a
date-time picker when required; order page and list show the promise and a LATE pill; receipt
prints it (update `RECEIPT-PRINT-001`'s R4 line accordingly); report on-time uses first promise.

**Proof:** domain table with opening-hour boundaries, closed days, Tết-unknown refusal, mixed
lines; API refusal and change audit; real API: accepted 17:00 → promised next day 13:00 (time
injected via the test clock the repo uses, or asserted on the domain + one real walk at wall time).

## 5. `UNCLAIMED-001` — waiting for pickup, storage fee, disposal (`DEC-036`; after `PAYMENT-001`)

**Data:** `order_contact_attempts` (order, channel `CALL|ZALO|SMS|VISIT`, outcome
`REACHED|NO_ANSWER|WRONG_NUMBER|PROMISED_TO_COME`, note ≤ 120, by, at); storage fee as a charge on
the order (the charges list `PAYMENT-001` exposes) computed by the domain at read and fixed at
payment; `storage_fee_waivers`; custody resolution `UNCLAIMED_DISPOSED` added to the domain enum and
CHECKs, legal only when policy published, ≥ 60 days since ready, ≥ 3 attempts on ≥ 2 distinct shop
days, by OWNER_ADMIN with MFA.

**API/Console:** `GET …/orders/awaiting-pickup` (ready, not collected, days waiting, attempts count,
fee so far, customer name/phone if linked); `#/pickup` "Đồ chờ lấy" list (Hôm nay links to it with a
count), each row: *Gọi*, *Ghi lần liên hệ*, and on the order page the fee line with *Miễn phí lưu
kho* (approver) and, when legal, *Thanh lý* (owner) with a confirm sheet stating the rule verbatim.
Receipt prints the storage rule once published.

**Proof:** fee domain table (day 20 = 0, day 21 = 5.000, cap 50%, waiver), disposal legality table,
real API with the ready date moved in the DB fixture (documented as a harness step).

## 6. `SHOP-CAPTURE-001` — machines, wash cycles, trip costs, Sổ thu chi (`DEC-038`)

**Data:** `machines` (seeded from `templates/machine-master.csv` by a script; owner edits),
`wash_cycles` (order, machine nullable, started_at, ended_at, started_by), `delivery_leg_costs`
(leg, vehicle `XE_MAY|O_TO|THUE_NGOAI`, km numeric(6,1), cost_vnd, note), `expenses` (store, date,
category `DIEN|NUOC|HOA_CHAT|TUI_NHAN|LUONG|MAT_BANG|SUA_CHUA|XANG_XE|KHAC`, amount_vnd, note, by).

**API/Console:** `START_WASH` step accepts optional `machine_id`; `QUALITY_CHECK` closes the open
cycle (both inside the step's transaction); a machine chooser sheet (big buttons, last used first,
"Bỏ qua"). Delivery leg form adds vehicle/km/cost. `#/expenses` "Sổ thu chi" (owner, accountant):
month view with totals by category (SQL), add expense sheet. Owner machines list under Hệ thống.
Report (`REPORT-DASHBOARD-001`) gains: cycles captured / cycles, average cycle minutes per machine,
delivery cost per delivered order, month spending by category, and margin **only** when the month
has DIEN, NUOC, HOA_CHAT, LUONG, MAT_BANG entries (else `INCOMPLETE` + missing list).

**Proof:** step executes cycle open/close atomically; skipping machine is allowed and counted; SQL
totals; margin completeness rule; real API: a walk with a machine chosen, a delivery with cost, two
expenses, the report lines.

## 7. `DAILY-SUMMARY-001` — the evening summary (`DEC-039`; after the report and the slices above)

`GET …/reports/daily-summary?date=` returns `{template_version, date, lines:[{key, text, figures}]}`
built server-side from the report read model and the lists (orders in/finished, money by method,
late vs promise, waiting > 20 / > 60 days, open complaints, accounts due, spending recorded). Text
is produced by a versioned Python template with the figures formatted by one money formatter; lines
whose source is not available (policy unpublished, feature empty) are omitted and listed in
`omitted`. Console: owner's Hôm nay card *Tóm tắt cuối ngày* (after 18:00, and on demand), *Sao
chép*, *Chia sẻ* (Web Share → Zalo). No language model.

**Proof:** template golden tests per line; omitted-when-unavailable; no personal data in the text
(names and phones never appear); real API after the walk.

## 8. Order of build

Wave 1 (parallel, after round 6 is merged): `CUSTOMER-001`, `PAYMENT-001`, `PROMISE-001`,
`SHOP-CAPTURE-001`. Wave 2: `PAYMENT-002`, `UNCLAIMED-001`, `DAILY-SUMMARY-001`. Then the filmed
review (`SHOP-FILMED-REVIEW-004`) as in round 6: every flow at phone size on the real API from an
empty database, reviewed as the counter worker and the owner, every finding fixed.

## 9. Still not built, and why

| | Why |
|---|---|
| Real Telegram / Zalo OA connection | Needs a bot token / OA verification and a public webhook — credentials and public ingress an agent may not create (`CHANNEL-TELEGRAM-001`, `CHANNEL-ZALO-APPLY-001`) |
| A language model writing the summary or answering customers | `DEC-006` is the owner's legal decision; all 13 capabilities stay `NOT_AUTHORIZED` until their gates |
| Automatic bank-transfer confirmation | No bank feed exists; staff confirm what they see |
| E-invoices (hoá đơn điện tử) | Tax treatment unconfirmed (`DEC-022`) |
| Automatic 10% late-delivery credit | Reported, not issued, this round (`DEC-037`) |
