# Remaining gaps — build spec V1 (round 8)

**Decisions:** `docs/DECISION_RECORD_REMAINING_GAPS_2026-09-28.md` (`DEC-040`–`DEC-044`).
**Audience:** the shop's desktop at the counter (1366×900) first, a phone second (`DEC` amendment
2026-09-27 in `STAFF_CONSOLE_REDESIGN_SPEC_V2.md`).
**Base:** `fe72912` (round 7 merged).

## 0. Rules every slice keeps

- Everything in `docs/SHOP_OPERATIONS_SPEC_V1.md` §0 still applies:
  - deterministic code decides money, state and permission, and the console only displays;
  - every write is idempotent, uses If-Match where versioned, and commits mutation + event + audit +
    outbox in one transaction;
  - lists are bounded, store-scoped and disclose truncation;
  - a feature that rests on an owner decision refuses by a named code until the owner publishes it;
  - no phone value appears in any log, event, audit, outbox, error or export.
- Migration numbers: `0063` e-invoice requests, `0064` late-delivery decisions, `0065` reminder
  steps. They were reserved as `0062`–`0064` and renumbered at integration, because
  `AUTHZ-LIFECYCLE-001`'s `0062` reached main first. `VIETQR-001` and `SUMMARY-ATTENTION-001` add
  none.
- Every new route goes into the contract artefacts `scripts/verify_contracts.py` checks, and into the
  console's served-route list. Every new control goes into the disclosure registry and
  `DECLARED_CONTROLS`.
- Proof per item:
  - a test that fails on `fe72912` for each behaviour;
  - the full `pytest --require-postgres-integration`;
  - the stubbed browser suite;
  - a conformance scenario that first proves the refusal and then the feature;
  - the daily walk still at 0 failed, at 1366×900 and at phone size.

---

## 1. `EINVOICE-REQUEST-001` — invoice requests (`DEC-040`, migration `0063`)

**Server.**

- Table `invoice_requests`:
  - `id`, `store_id`;
  - `subject_kind` (`ORDER` | `ACCOUNT_MONTH`), with `order_id` or (`account_id`, `period_month`
    as the first day of the month); one live request per subject (partial unique index on
    status `REQUESTED`/`ISSUED`);
  - the buyer snapshot: `buyer_unit_name` (1–200), `buyer_tax_code` (nullable;
    `^[0-9]{10}(-[0-9]{3})?$|^[0-9]{12}$`), `buyer_address` (required when a tax code is given;
    1–300), `buyer_email` (nullable, simple shape check), `buyer_name` (nullable, 1–120);
  - `status` (`REQUESTED` → `ISSUED` | `CANCELLED`);
  - on `ISSUED`: `invoice_symbol` (`^[0-9A-Z]{1,12}$`), `invoice_number` (1–8 digits) and
    `invoice_date`;
  - on `CANCELLED`: `cancel_reason` (`CUSTOMER_WITHDREW` | `DUPLICATE` | `WRONG_DETAILS` | `OTHER`
    + note);
  - `requested_by`, `requested_at`, `closed_by`, `closed_at`, `row_version`.

  Status transitions are enforced by a trigger. `ISSUED` and `CANCELLED` are terminal. An issued row
  is immutable.
- An optional invoice profile on the account customer (same five fields, nullable), pre-filled
  into a new request for that account and updated only by an explicit "lưu cho lần sau".
- **Amount** is read, never stored:
  - for an order: its quoted total as charged (storage fee included, and shown as a separate line
    when present);
  - for an account month: the sum of that month's account charges, through the same read
    `PAYMENT-002`'s statement uses.

  Amounts are integers from the existing ledgers; this module adds no arithmetic of its own beyond
  what those reads already publish.
- **Refusals:**
  - `PRIVACY_NOTICE_UNPUBLISHED` (capture);
  - `INVOICE_SUBJECT_UNAVAILABLE` (an order that is cancelled, or not in the store);
  - `INVOICE_REQUEST_EXISTS`;
  - `INVOICE_TAX_CODE_SHAPE`;
  - `INVOICE_ADDRESS_REQUIRED`;
  - `INVOICE_REQUEST_CLOSED`.
- **Routes:**
  - create (on an order; on an account month);
  - list (by status, bounded, oldest first);
  - read;
  - record issued (If-Match);
  - cancel (If-Match);
  - a CSV download of open requests `invoice-requests-export-v1` (UTF-8 with BOM for Excel).

  Columns: request code, requested date, unit name, tax code, address, email, buyer, order ticket or
  account month, one row per service line with description, quantity, unit and amount as charged,
  and total as charged. The header row says *Số tiền theo giá tiệm đã thu (chưa tách thuế)*.

  The download is owner/approver only, audited, and names its query version like the round-6
  exports.
- **Who may act:**
  - operations roles create and cancel their own store's requests;
  - owner and approver record an issued invoice and download;
  - erasing a customer blanks the buyer fields of that customer's `REQUESTED` rows and leaves
    `ISSUED` rows alone, under `DEC-008`'s schedule (this is stated, not a tax claim).

**Console.**

- Order page: a *Hóa đơn* row with *Khách cần hóa đơn* opens a sheet with the five fields; an
  account order pre-fills them.
- Account page: *Hóa đơn tháng* per month.
- New screen *Hóa đơn cần xuất* under *Thêm* (desktop: a table), with:
  - tabs *Cần xuất* / *Đã xuất* / *Đã huỷ*;
  - per row, *Ghi số hóa đơn* (symbol, number and date);
  - one primary *Tải danh sách cho kế toán*.
- Tier 1 copy never says the software issues invoices. Tier 2 explains that the bookkeeper issues
  them in the provider's portal.
- `#/gaps`: the "Hóa đơn điện tử" entry changes to "tự xuất qua nhà cung cấp — chờ chủ tiệm chọn
  nhà cung cấp và kế toán xác nhận loại hóa đơn, thuế suất".

## 2. `VIETQR-001` — an exact QR for what is owed (`DEC-041`, no migration)

**Server.**

- `packages/domain/.../vietqr.py` is pure. It builds the EMVCo merchant-presented payload, NAPAS
  profile, with the elements in this order:

  | Element | Value |
  |---|---|
  | `00` | `01` |
  | `01` | `12` (dynamic, an amount is present) |
  | `38` | `00`=`A000000727`, then `01`=(`00`=BIN, `01`=account), then `02`=`QRIBFTTA` |
  | `53` | `704` |
  | `54` | amount: integer VND, digits only, > 0 |
  | `58` | `VN` |
  | `62` | `08`=transfer code |
  | `63` | CRC-16/CCITT-FALSE (poly `0x1021`, init `0xFFFF`) over everything including `6304`, 4 uppercase hex |

  Each field is id + 2-digit length + value.

  Golden vectors, which must reproduce byte-for-byte (the static one has `01`=`11` and no `54`/`62`):
  - `00020101021138530010A0000007270123000697041601092576788590208QRIBFTTA53037045802VN6304AE9F`
  - `00020101021238530010A0000007270123000697041601092576788590208QRIBFTTA53037045405100005802VN62150811Chuyen tien630453E6`
  - `00020101021238530010A0000007270123000697041601092576788590208QRIBFTTA5303704540410005802VN62150811Chuyen tien6304BBB8`

  Plus: CRC of `123456789` = `29B1`; a round-trip parser test; and refusals for a BIN that is not 6
  digits, an account that is not 6–19 alphanumerics, an amount ≤ 0 or not an integer, and a code
  outside `^[A-Z0-9]{1,25}$`.
- **Transfer code** (pure function):
  - `NTL` + `ddmm` of the ticket's business day + 3-digit ticket number;
  - else `NTL` + the first 8 hex digits of the order id, upper-cased;
  - account month: `NTLCN` + first 6 hex of the account id + `mmyy`.
- **Published config** `BANK_TRANSFER_ACCOUNT` (`bank-transfer-account-v1`): `bank_bin`,
  `account_number`, `account_name` (upper-case ASCII, as banks show it), `bank_display_name` and
  `test_transfer_confirmed_at`.

  `scripts/publish_bank_account.py`, mirroring `publish_storage_policy.py`, supports:
  - `--preview` (writes `vietqr-test-1000.svg` and prints the payload; publishes nothing);
  - publishing, which requires `--test-transfer-confirmed`;
  - `--withdraw`.
- **Route** `GET` order VietQR, which returns one of:
  - `{payload, modules: [[0/1…]…], amount_vnd, transfer_code, account_name, bank_display_name, amount_source: "BALANCE_DUE"}`;
  - `BANK_ACCOUNT_UNPUBLISHED`;
  - `NOTHING_OWED`, when the balance is `PAID`, `ON_ACCOUNT` or `REFUNDED`.

  The amount is the payment ledger's remaining balance read in the same request (the storage fee is
  included when accrued). A second route does the same for an account month (the statement total
  still unpaid).

  The QR matrix is produced on the server (add `segno`, a pure-Python library, to the API's
  dependencies; quiet zone 4; error correction M). The console only draws squares, and no HTML or
  SVG string crosses the API.
- **Search:** the order search accepts a transfer code (`NTL…` case-insensitive) and resolves it by
  ticket day and number, or by id prefix within the store.

**Console.**

- Order page, *Thu tiền* sheet, when the method is *Chuyển khoản*: the QR (≥ 240 px on desktop),
  *Số tiền* and *Nội dung* in large type, the account name, and a line *Kiểm tra app ngân hàng:
  đúng nội dung và số tiền rồi mới bấm Đã nhận*. The amount field stays editable, because a customer
  may pay part.
- *Phiếu cho khách*: the QR at print size with the amount and code while money is owed.
- Account statement: the QR for the unpaid month.
- Unpublished: no QR, and a tier 2 note naming the owner's switch. The rest of the counter is
  unchanged.
- `#/gaps`: "Tự xác nhận chuyển khoản" stays and names what is needed (the owner chooses a
  notification service or bank API, and its credential and network path).
- **Future contract (spec only, not built):**
  - table `bank_transactions`, holding normalised incoming credits (amount, time, memo, bank
    reference);
  - a matcher that proposes (order, amount) only on an exact transfer code where the amount is at
    most the balance;
  - one-tap confirmation that writes an ordinary `TAKE_PAYMENT CHUYEN_KHOAN`, never automatically.

## 3. `LATE-CREDIT-002` — measured late deliveries (`DEC-042`, migration `0064`)

**Server.**

- A pure domain function `late_delivery_clock(first_promise, changes, legs)` returns the following
  (all clocks in UTC, displayed in Asia/Ho_Chi_Minh):
  - `deadline`: the first promise, or the newest change whose reason is `CUSTOMER_REQUEST`;
  - `delivered_at`: the earliest `SUCCEEDED` RETURN leg;
  - `late_by_minutes`: floor, and not negative;
  - `failed_attempts_before_deadline`.
- Scope: orders whose fulfilment mode expects a return leg, have a promise, and have delivered.
- Table `late_delivery_decisions`:
  - `order_id` unique, `store_id`;
  - `decision`: `STORE_FAULT_CREDITED` | `NOT_STORE_FAULT`;
  - `reason_code`, for `NOT_STORE_FAULT`: `CUSTOMER_ABSENT` | `CUSTOMER_WRONG_ADDRESS` |
    `CUSTOMER_ASKED_LATER` | `OTHER`, with a note required for `OTHER`;
  - `late_by_minutes` measured at the decision;
  - `remedy_proposal_id` (nullable);
  - `decided_by`, `decided_at`.

  Append-only.
- **List route** *Giao trễ cần xử lý*:
  - delivered orders with `late_by_minutes > policy.late_delivery_threshold_minutes` and no
    decision, bounded, oldest first;
  - each row includes the measured minutes, the deadline, the arrival, the failed attempts, the
    settled total and the would-be credit;
  - the credit comes from `evaluate_remedy`'s probe, as `RemedyOptions` does today, and is null on a
    refunded bill.
- **Decide route:**
  - `STORE_FAULT` opens, in one transaction, the late-delivery incident (existing incident kind or
    the nearest existing one; follow the code), then the `LATE_DELIVERY_CREDIT` proposal with
    `attested_late_by_minutes` = the server's measurement (never a client value) and
    `store_fault_attested=true`, then the decision row;
  - the proposal then follows the existing authority and execution path unchanged (staff ≤ 100.000 ₫,
    owner above; one credit per order);
  - `NOT_STORE_FAULT` writes only the decision.
  - Refusals: `REMEDY_POLICY_UNPUBLISHED`, `NOT_LATE`, `ALREADY_DECIDED` and the remedy refusals as
    they are.
- **Report** (`report-v4`): late deliveries, credited (count and amount), not the shop's fault,
  undecided.

**Console.**

- *Hôm nay*: a *Giao trễ* card when the list is non-empty.
- The list screen (desktop: a table): per row, the ticket, *hẹn* → *giao* times, *trễ 3 giờ 10
  phút*, the failed-attempt hint, and two buttons:
  - *Lỗi của tiệm — giảm {credit}*;
  - *Không phải lỗi tiệm*, which opens the reason picker.

  After a credit above the staff limit, the row shows *Chờ chủ tiệm duyệt*, linking to the approval.
- Guide: the *Giảm trừ do giao trễ* row says the server measures the lateness.

## 4. `PICKUP-REMIND-001` — the reminder schedule and the two-tap send (`DEC-043`, migration `0065`)

**Server.**

- The pure domain function `reminder_steps(ready_on, today, storage_policy)` returns the due
  steps:
  - `READY` on day 0;
  - `DAY_3`, `DAY_7`, `DAY_14`;
  - `BEFORE_FEE` on the last shop day before `fee_starts_day`, only when a storage policy is
    published;
  - days are counted in `Asia/Ho_Chi_Minh` calendar days from the day the order became ready for
    pickup (the same "ready" fact `UNCLAIMED-001` counts from);
  - a step is due from its day until an attempt is recorded for it, and a later step supersedes an
    earlier one that was never done, so only the newest due step shows.
- Migration `0065`:
  - `order_contact_attempts` gains `reminder_step` (nullable, the five values);
  - the `outcome` check gains `MESSAGE_SENT`;
  - existing rows are untouched.
- **Due list route** *Nhắc khách lấy đồ*: self-collect orders ready for pickup and not collected,
  bounded, oldest ready first. Each row carries:
  - the step;
  - the days waiting;
  - the customer's display name when there is a record;
  - `reachable`: `PHONE` | `CHAT` | `NONE`;
  - `zalo_url`, `https://zalo.me/<digits>`, derived on the server and returned only to operations
    roles, exactly as the unclaimed list returns its call link;
  - the balance due.
- **Message route:**
  - returns the fixed text `pickup-reminder-v1` for (order, step), after `check_egress_allowed(TRANSACTIONAL)`;
  - refuses `MESSAGING_POLICY_UNPUBLISHED`, `SUPPRESSED` or `NO_CONTACT`;
  - the text is built from the shop name, ticket and day, ready day, balance and opening hours, plus,
    for `BEFORE_FEE`, the published fee per day and start day, copied from the policy;
  - no name or phone is in the text;
  - golden tests cover each step.
- **Record:** the existing contact-attempt route gains an optional `reminder_step`. Channel `ZALO`
  or `SMS` with outcome `MESSAGE_SENT` is legal only when the message route would allow; a call keeps
  its outcomes.

**Console.**

- *Hôm nay*: a *Nhắc khách lấy đồ (n)* card.
- The list screen (desktop: a table). Per row:
  - the ticket, the step as words (*Báo đồ đã xong*, *Nhắc lần 2 (ngày 3)*, …), and the days
    waiting;
  - buttons *Mở Zalo*, *Chép tin nhắn* and *Gọi*, then *Đã nhắc*, a one-tap outcome picker whose
    default is *Đã gửi tin*.

  Unreachable orders are counted at the bottom. Refusals are shown in words, with the owner's switch
  in tier 2.
- The unclaimed list keeps working, and its disposal rule now also counts the reminder attempts.

## 5. `SUMMARY-ATTENTION-001` — *Cần chú ý* (`DEC-044`; after §1, §3 and §4 merge)

**Server.**

- The daily-summary read model gains a `comparisons` input, computed by a versioned query:
  - today's collected money and orders received;
  - the same figures for the same weekday in each of the previous 4 weeks;
  - `weeks_with_data`;
  - the mean as an integer, rounded half up in the read model, never in the template;
  - last month's core cost categories with no Sổ thu chi line (asked after the 10th, only when the
    shop took an order last month; `missing_core_categories`, the rule the month margin refuses on);
  - the comparison waits for closing time (20:00) on the day itself.
- Attention inputs: late deliveries undecided (§3), overdue-not-ready count (SLA board), reminders
  due, orders reaching the storage fee within 3 days (§4 and `UNCLAIMED-001`), and invoice requests
  older than 3 days (§1).
- Template `daily-summary-v3`: a *Cần chú ý* block first, holding at most 5 lines in the
  `DEC-044` order, each printed only when its condition holds.
  - The comparison prints both figures ("Tiền thu hôm nay 1.200.000đ — trung bình 4 thứ Hai trước
    1.850.000đ") and only when `weeks_with_data ≥ 3` and the ratio is outside 70–130%. The
    ratio test is done on integers in the read model: `100*today < 70*mean` or
    `100*today > 130*mean`, and the template receives a boolean.
  - An unavailable source is left out with its reason.
- Golden tests: every line alone, all five, none, unavailable sources, and no personal data.
  Identifier and digest moved.

**Console.** The summary card shows the block first. Copy-to-Zalo includes it.

**Future contract (spec only, not built):** a model may reword the summary only after `DEC-006` is
decided by its owner:
- its input is the computed facts object only;
- a verifier extracts every number and money figure from the returned text and refuses the text
  unless each one equals a value in the facts;
- on refusal, or on any provider failure, the template text is used.

## 6. `GAPS-FILMED-REVIEW-004`

Film every new flow at 1366×900 against the real API on a database created empty:
- the refusal first;
- the owner's publish;
- the working flow.

Review each film as the counter worker and as an engineer, fix what is found, and re-run the full
proof.

## 7. Still not built after this round, and what each waits for

| Gap | Waits for |
|---|---|
| Issuing e-invoices automatically | Owner: provider choice, account and credential; accountant: invoice type and rate |
| Confirming transfers automatically | Owner: notification service or bank API, its credential and network path |
| Sending reminders automatically on Zalo | Owner: Zalo Official Account, approved ZNS templates, token, per-message cost |
| Model-written summary wording | `DEC-006` (security and privacy owner) |
| Credit for self-collect orders not ready on time | Not in the ratified rule; owner would have to add it |
