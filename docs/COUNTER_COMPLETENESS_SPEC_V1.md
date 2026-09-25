# Counter Completeness Spec V1 — the missing pieces of a working shop day

Status: APPROVED FOR BUILD 2026-09-25 (founder delegation: "plan to spec engineering all the missing
pieces and execute implementation for a fully working app"). Builds on
`docs/STAFF_CONSOLE_REDESIGN_SPEC_V2.md`; every invariant there (§7) and in `AGENTS.md` stands.

## 1. What is missing, and what stays missing on purpose

Measured against the running app after the V2 redesign (`90c6fb3`), the `#/gaps` register, and the
readiness report's "Not done" table. The pieces are sorted by whether engineering alone can close
them.

| Gap | Who it hurts, and when | Closable by engineering? | Item |
|---|---|---|---|
| No step for "stain still there, wash again" or "we won't take this garment" | The counter worker, several times a week: today they misuse Tạm dừng / Huỷ, and a rewash cannot be counted | **Yes** | `ORDER-STEPS-002` |
| A remedy credit is applied by typing its code | The counter worker, when a customer comes back with a credit | **Yes**: credits are bearer instruments (`remedies.py` `redeem`) and `remedy_credits_open_idx` exists | `CREDIT-PICK-001` |
| A customer who wrote through a channel is bound by a pasted code | The counter worker, for every channel customer | **Partly**: hand-off from the conversation and a returning-customer list; *no* name or phone search (`DEC-015`) | `CONTACT-PICK-001` |
| Nothing to hand the customer: no printed or shown receipt | Every walk-in: the ticket number is spoken and remembered | **Yes** | `RECEIPT-PRINT-001` |
| No numbers for the owner: no funnel, on-time rate, rewash rate or money-by-period (`FR-RPT-001`, `-005`) | The owner, every evening and every month | **Yes**: every input is already a stored fact | `REPORT-DASHBOARD-001` |
| Export is one day only (`FR-RPT-003`) | The owner and the accountant, monthly | **Yes** | `EXPORT-RANGE-001` |
| Sessions cannot be listed or revoked one by one | The owner, when a phone is lost | **Yes**: `staff_sessions.id` exists, and so does the revoke route | `SESSION-LIST-001` |
| An approval of an `ORDER` envelope makes the approver compare two version numbers by eye | The approver | **Yes** | `SESSION-LIST-001` (same slice: small, touches `approvals.js`) |
| Real customer channel (Telegram/Zalo) | Everyone | **No**: `CHANNEL-TELEGRAM-001` needs a bot token and a public webhook endpoint; an agent may hold neither | stays BLOCKED |
| Customer records, names, phones | Counter | Decided 2026-09-25 (`DEC-034`, reopening `DEC-015`) | `CUSTOMER-001`, `docs/SHOP_OPERATIONS_SPEC_V1.md` |
| Part payment, deposits, debt | Counter | Decided 2026-09-25 (`DEC-035`, superseding the `DEC-010` deferral) | `PAYMENT-001`, `PAYMENT-002` |
| Unclaimed goods, storage fee | Counter | Decided 2026-09-25 (`DEC-036`; earlier text wrongly cited `DEC-005`, the channel decision) | `UNCLAIMED-001` |
| Batches, custody, machine cycles, delivery cost, distance | Owner | Capture decided 2026-09-25 (`DEC-038`); the 4–6 week baseline itself still needs real days (`SHOP-INSTRUMENT-001` stays BLOCKED on data) | `SHOP-CAPTURE-001` |
| Per-order SLA rule | Owner | Decided 2026-09-25 (`DEC-037`, from the owner-confirmed `templates/service-sla.csv`) | `PROMISE-001` |
| AI summary of the day (`FR-RPT-007`) | Owner | Decided 2026-09-25 (`DEC-039`): a deterministic template now; a model only after `DEC-006` | `DAILY-SUMMARY-001` |

**Rule for this round:** every piece either works end to end against the real API, or keeps its
entry on `#/gaps` with the true reason. Nothing is shown working that is not.

## 2. Founder rulings made for this round (delegated, recorded, reversible)

Each ruling classifies facts. None moves money beyond ratified figures or asserts a legal basis.

- **R1 — Rewash.** A garment that fails quality check (or is found not clean at the counter before
  it leaves) is washed again inside the same order. It is recorded as a named step with a reason
  from `NOT_CLEAN | MACHINE_FAULT | OTHER`. It costs the customer nothing: the order's price is
  unchanged. A garment the customer brings back *after* taking it is a complaint (`DEC-004`, the
  remedy flow), not this step. Reversal: remove the step from the vocabulary; the recorded events
  stay valid per-axis transitions.
- **R2 — Refusing goods at intake.** Staff may refuse laundry before the order is active (it is on
  the counter, not yet accepted for work), with a reason from
  `NOT_SERVICEABLE | DAMAGED_ON_ARRIVAL | OTHER`. The laundry goes back to the customer, the order
  closes as cancelled, and no money moves (none can have: prepayment needs an active order).
  Reversal: as R1.
- **R3 — Credits list.** An unused remedy credit is shown to any operations role in its store, with
  the ticket it was issued on, its amount and its date. It is already a bearer instrument; listing
  it adds no authority, and it replaces typing a code the customer may have lost.
- **R4 — Receipt content.** The printed or shown receipt carries only what the server holds: shop
  name, ticket number, the priced lines, the total and the promotion or credit applied, when the
  goods were received, and the order's short reference. **No promised-ready time**: the per-order
  SLA rule is undecided, and a receipt that promises a time the shop has not decided is a promise
  the software made. The receipt says "Tiệm sẽ báo khi đồ sẵn sàng" instead.
- **R5 — The owner's numbers** are called what they are. Money collected is "Tiền đã thu", never
  "doanh thu" or "lợi nhuận" (the takings rule since `OPS-BOARD-001`). The on-time rate is computed
  against the one stated internal rule the SLA board already uses, and says so on the tile.

## 3. Items

Each item lists its server change, its console change, and what proves it. Every write keeps
`Idempotency-Key`, `If-Match` where the resource has a row version, per-transition audit + event +
outbox rows in one transaction, and fail-closed authorisation.

### 3.1 `ORDER-STEPS-002` — rewash and refuse-at-intake as steps

**Domain** (`packages/domain/.../order_steps.py`). Two new composite steps, planned by the real
transition functions exactly like the existing ones (no second transition table):

- `REWASH`: legal from production `QUALITY_CHECK` or `READY_AT_STORE` on an `ACTIVE` order.
  Plan: production → `EXCEPTION` (interrupting the current state), then `EXCEPTION` → `IN_PROCESS`
  (backwards, which only an exception permits: `transition_production`'s DEC-024 branch). It is
  refused from any other production state, on a closed order and during cancellation review. It
  requires `rewash_reason`.
- `REJECT_INTAKE`: legal while intake is `RECEIVED_PENDING_INSPECTION`,
  `WAITING_PRICE_APPROVAL`, `WAITING_CUSTOMER_RECONFIRMATION` or `WAITING_SLOT_APPROVAL` (never
  `AWAITING_HANDOFF`: nothing was received; never `ACCEPTED`), and commercial is before `ACTIVE`.
  Plan: intake → `REJECTED`, then commercial → `CANCELLED` (direct; `REJECTED` is in
  `CUSTODY_NOT_HELD_INTAKE_STATUSES`, so the domain admits it). It requires `rejection_reason`.
- Neither is ever the **primary** step. Both are listed under "Khác", with the reason picker.
- `next_steps` lists them with `requires: ["rewash_reason"]` / `["rejection_reason"]` and
  `accepted` values, the way `CANCEL` lists `custody_resolution`.

**API.** `OrderStepRequest` gains `rewash_reason` and `rejection_reason` (typed enums, strict, each
refused with 422 when sent with a step that does not take it and required when the step needs it).
The reason is recorded on the domain event payload of the first transition of the step, and on its
audit row, so the order history shows "Giặt lại · Chưa sạch". No migration if the payload admits
the key; otherwise add one forward-only.

**Console** (`orderDetail.js`). "Giặt lại" and "Không nhận đồ" appear in "Khác" when legal. Each
opens a sheet with the three reasons as a segmented control and one confirm button. The history
names the step and its reason. `#/gaps` retires the "Sự cố sản xuất và từ chối nhận đồ" entry, and
the gaps screen's lede, the counter guide (`docs/HUONG_DAN_CA_LAM_VIEC_VI.md`) and
`docs/CORE_BUSINESS_WORKFLOWS_V1.md` §5 are corrected in the same change.

**Proof.** Domain table tests for every production and intake state (legal / refused, and the plan).
API tests: 422 on missing or misplaced reasons, 409/412 on stale `If-Match`, a replay with the same
key, and the audit and event rows written. Real-API conformance scenario: a stain found at quality
check → Giặt lại → the order walks forward again to completed; a refused bag → Không nhận đồ →
the order is cancelled and no step is offered any more.

### 3.2 `CREDIT-PICK-001` — pick a credit, don't type it

**API.** `GET /internal/v1/stores/{store_id}/remedy-credits?state=UNUSED&ticket=<n>&limit=<n>` —
unused credits in the store, newest first, bounded (default 50, max 200) with `truncated`. Each
row: `credit_id`, `amount_vnd`, `issued_at`, `issued_from_order_id`, the issuing order's
`ticket_number` and `ticket_issued_on` (null for a channel customer), and `policy_version_id`
(tier 3). Operations roles with the store assignment; an `AUDITOR` reads. There is no contact field
in the response (`DEC-015`).

**Console** (`newOrder.js` receipt, and the quote revision surface that shares it). "Dùng khoản
giảm trừ" opens a sheet listing the unused credits ("Phiếu 12 · 24/09 · 30.000 ₫"), with a
ticket-number filter when there are more than five. A tap applies the credit through the existing
redemption route with the values the list returned. "Nhập mã thủ công" stays as the fallback,
collapsed. An empty list says "Không có khoản giảm trừ nào chưa dùng".

**Proof.** API tests: scoping (another store's credits never appear), a redeemed credit
disappears, `truncated`, role refusals. Real-API: a complaint with a credit → the next order picks
it from the list → the total drops by exactly the server's figure → the credit is no longer listed.

### 3.3 `CONTACT-PICK-001` — start an order for a channel customer without pasting

No search by name or phone: no such data exists (`DEC-015`). Instead, the two places a staff
member actually meets a channel customer hand the binding over directly.

- **From the conversation.** Wherever the console shows a customer's message or a draft reply
  (approvals `SEND_MESSAGE` card, `#/shadow`, `#/exceptions` manual send), a "Tạo đơn cho khách
  này" link opens `#/new?contact=<binding>` (the binding comes from the server read already on that
  screen). `#/new` with that parameter goes straight to step 2 after creating the order request,
  and the server still decides whether the binding is valid (`REQUIRE_HUMAN` on an unknown one, as
  today).
- **Returning customers.** `GET /internal/v1/stores/{store_id}/contacts/recent?limit=<n>` lists
  the channel bindings that have an order or an order request **in this store**, newest activity
  first, bounded with `truncated`. Each row: `contact_binding_id` (tier 3), the channel (a gloss),
  `last_activity_at`, the most recent order's ticket number (if any), its total and its status
  gloss, and an open-order count. A binding with no order and no order request in this store is not
  listed: nothing server-side ties it to the store, and unknown means stop. `#/new` step 1 shows
  "Khách nhắn tin gần đây" under the walk-in button; a tap binds.

**Proof.** API: store scoping (a binding used only in another store never appears), bounds,
roles, no message text or provider handle in the response. Console stub + real API: a channel
customer from the consent walk appears in the list and binds with one tap; "Nhập mã thủ công"
remains, collapsed.

### 3.4 `RECEIPT-PRINT-001` — a receipt the customer can take

**Console only**, read from existing routes (order view, quote revision, store). `#/orders/:id/receipt`
renders a receipt sized for an 80 mm / 58 mm thermal printer and for A5, with `@media print` rules
that hide the shell. Content per R4. Buttons: "In phiếu" (`window.print()`) and, where the
browser supports it, "Chia sẻ" (Web Share API with the receipt as text). Reached from the order
page's "Khác" list and from the confirmation screen of `#/new` ("In phiếu cho khách").

**Proof.** Stub suite: print CSS hides the app bar and tab bar, the total equals the server's
figure verbatim, no UUID is visible. Real API: a screenshot of the receipt for a fresh order in
print media emulation at 80 mm width.

### 3.5 `REPORT-DASHBOARD-001` — the owner's numbers (`FR-RPT-001`, `FR-RPT-005`)

**Read model** (`packages/db`): one versioned query module, `REPORT_QUERY_VERSION = "report-v1"`,
computing for a store and a closed date window `[from, to]` (shop-local calendar days, at most 92):

| KPI | Numerator | Denominator | Data quality |
|---|---|---|---|
| Đơn mới | orders created in window | — (a count) | `COMPLETE` |
| Đơn hoàn tất | orders `COMPLETED` in window | orders created in window | `COMPLETE` |
| Đơn huỷ | orders `CANCELLED` in window | orders created in window | `COMPLETE` |
| Đúng hẹn (nội bộ) | orders reaching `READY_AT_STORE` within the SLA board's stated rule | orders reaching `READY_AT_STORE` in window | `RULE_ASSUMED` (the per-order rule is undecided) |
| Giặt lại | orders with ≥ 1 rewash in window (a production move from `EXCEPTION` back to an earlier state: counts both `REWASH` and older per-axis rewashes) | orders reaching `QUALITY_CHECK` in window | `COMPLETE` |
| Khiếu nại | incidents opened in window | orders completed in window | `COMPLETE` |
| Tiền đã thu | the takings figure summed per day, net of refunds, as the day summary computes it | — | `COMPLETE` |
| Bồi hoàn đã chi | remedies executed in window, by kind | — | `COMPLETE` |

Every KPI is returned as `{key, numerator, denominator|null, window, data_quality, query_version}`.
Rates are **never computed in the API or the console**: the console shows "12 / 40" and lets the
reader see both numbers; a percentage is shown only as `numerator` over `denominator` formatted by
`format.js`, with the fraction beside it. Money is only summed by the database from stored
integers, never by the console. Margin (`FR-RPT-002`) is **not** shown: cost is not captured
(`SHOP-INSTRUMENT-001`), and the tile says so.

**API.** `GET /internal/v1/stores/{store_id}/reports/summary?from=YYYY-MM-DD&to=YYYY-MM-DD` plus
`…/reports/daily?from&to` (one row per day for the same KPIs, for a small bar list). `OWNER_ADMIN`,
`OPS_APPROVER`, `ACCOUNTANT`, `AUDITOR` read; `OPERATOR` is refused. Window > 92 days → 422.

**Console.** `#/reports` ("Báo cáo", under "Thêm", and the owner's Hôm nay links to it): a
segmented control Hôm nay · 7 ngày · 30 ngày · Tháng này · Tự chọn, then KPI tiles (big number,
fraction, a tier-2 ⓘ with the definition and data-quality status verbatim), then a per-day list.
Tier 3 shows the query version and window.

**Proof.** DB tests on a seeded fixture with known counts for every KPI, including a rewash
recorded both ways, a refund, a cancelled order and a window boundary at shop-local midnight. API:
role refusals, window validation. Real API: the numbers after the daily walk equal what the walk
did.

### 3.6 `EXPORT-RANGE-001` — export a date range

`#/exports` today exports one day through an owner-approved envelope. Extend the envelope and the
export to a window `[from, to]` of at most 92 days: the approval binding (snapshot hash, resource
version, what the approver sees on the card) covers both dates, so approving January cannot release
February. One-day exports keep working unchanged (`from == to`). The file stays sanitised by
`SanitizedExportRepository._cell` and carries the query version and window in its header rows.

**Proof.** API tests: an envelope approved for one window cannot release another; the 92-day bound;
a one-day export behaves exactly as before. Real API: the owner exports 7 days after the walk and
the row count equals the orders created in that window.

### 3.7 `SESSION-LIST-001` — lost phone; and approving the order version you saw

- `SessionResponse` gains `session_id`. `GET /internal/v1/sessions` returns the caller's live
  sessions (`session_id`, `issued_at`, `last_seen_at`, `idle_expires_at`, `current: bool`);
  `GET /internal/v1/staff/{staff_user_id}/sessions` returns anyone's for `OWNER_ADMIN`. No secret
  or hash is ever returned. The existing `POST /internal/v1/sessions/{id}/revoke` is used as is
  (its authorisation rules are unchanged and tested).
- Console: the account sheet gets "Thiết bị đang đăng nhập" (the caller's sessions, "Thiết bị này"
  marked, "Đăng xuất thiết bị này" on the others). `#/staff/:id` gets the same list for the owner.
  `#/gaps` retires "Danh sách và thu hồi phiên khác".
- Approvals: an `ORDER` card reads the order's current `row_version` and shows, in tier 1, either
  "Đơn chưa thay đổi kể từ khi gửi duyệt" or "Đơn đã thay đổi sau khi gửi duyệt — mở đơn để xem
  lại" (with Duyệt disabled and the reason; the server refuses a mismatch anyway). `#/gaps` retires
  "Biết chắc mình đang đọc đúng phiên bản…".

**Proof.** API: a staff member cannot list another's sessions; the owner can; revoking one
session leaves the others alive; no secret in any response. Stub + real API: two browser contexts
signed in as one person; the first revokes the second, which lands on the signed-out screen at its
next request.

## 4. Acceptance for the round

1. Every item's own proof above, plus the repository gates (`ruff`, `mypy`, `verify_contracts`,
   `check_context_drift`, the full `pytest --require-postgres-integration`), 0 failed.
2. The stubbed browser suite, the daily walk and the full conformance run at **phone size and at
   desk size**, against a database created empty, 0 failed; every new control declared in
   `DECLARED_CONTROLS` and touched.
3. Every new flow filmed at phone size on the real API and reviewed as a counter worker and as an
   engineer; every finding fixed and re-filmed.
4. `#/gaps` lists exactly what is still missing, each with its real reason; no retired entry
   survives, and no entry is retired for something that does not work.
5. Delivery items recorded through the controller with evidence read from the logs.

## 5. Slicing (independent worktrees, merged by the lead)

| Slice | Items | Files it owns |
|---|---|---|
| A | `ORDER-STEPS-002` | `order_steps.py`, the steps route, `orderDetail.js` "Khác" + history |
| B | `CREDIT-PICK-001`, `CONTACT-PICK-001` | new read routes, `newOrder.js`, conversation hand-off links |
| C | `RECEIPT-PRINT-001` | new `receipt.js` screen + print CSS, one link in `orderDetail.js` and `newOrder.js` |
| D | `REPORT-DASHBOARD-001` | new report read model, routes, `reports.js`, nav entry in `more.js` |
| E | `EXPORT-RANGE-001` | export repository, envelope binding, `exports.js` |
| F | `SESSION-LIST-001` | session routes, account sheet in `app.js`, `staff.js` detail, `approvals.js` ORDER card |

Shared files (`gaps.js`, the disclosure count, `verify_workflow_conformance.py`, the counter guide,
`kit.js`/`kit.css`) are edited by each slice only in its own entries or in a block at the end named
after the slice; the lead resolves the merge.
