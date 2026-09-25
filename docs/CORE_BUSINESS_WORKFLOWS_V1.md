# Core business workflows — what this shop does, and what the software does about it

**Status:** describes the system as it runs on 2026-09-10, verified by driving it.
**Owner:** `WORKFLOW-CONFORMANCE-001`
**Sources:** the code, first. Where a specification and the code disagree, §9 records the
disagreement and the workflow sections describe the code.

---

## 0. How to read this

Every workflow below is written the same way: the real-world event that starts it, who may perform
it, the exact screen and control they touch, what the server does, and how it ends — including the
endings that are refusals. Where a number is fixed by code, the number is here.

Three rules govern the whole document:

1. **The screen is not the authority.** Every rule stated here lives in deterministic Python and is
   re-checked by the server on every request. The console predicts; the server decides.
2. **A refusal is a result.** Most of what follows is about what the shop must *not* be able to do
   silently. Those paths are workflows too, and they are tested as such.
3. **Nothing here is aspirational.** A workflow the software cannot perform today is in §8, not in
   §2–§7.

Verified by `scripts/verify_workflow_conformance.py`, which drives each of these in a browser
against a real API and a real database, and by `scripts/verify_daily_operations.py`, which drives
the two happy paths end to end.

---

## 1. The mental model: one order, four independent answers

An order does **not** have a status. It has four, and they move independently
(`packages/domain/src/nha_trang_laundry_domain/catalog.py`):

| Dimension | Question it answers | Values, in order |
|---|---|---|
| **commercial** | Is this a live piece of business? | `DRAFT` → `REQUESTED` → `STORE_CONFIRMATION_PENDING` → `CONFIRMED` → `ACTIVE`; then `CANCELLATION_REVIEW`, and the two endings `CANCELLED` / `COMPLETED` |
| **intake** | Does the shop physically hold the laundry? | `AWAITING_HANDOFF` → `RECEIVED_PENDING_INSPECTION` → `WAITING_PRICE_APPROVAL` → `WAITING_CUSTOMER_RECONFIRMATION` → `WAITING_SLOT_APPROVAL` → `ACCEPTED`; or `REJECTED` |
| **production** | What has been done to the laundry? | `NOT_STARTED` → `QUEUED` → `IN_PROCESS` → `QUALITY_CHECK` → `READY_AT_STORE` → `RELEASED`; plus `ON_HOLD` and `EXCEPTION` |
| **balance** | Has the money changed hands? | `UNPAID` → `PAID` (the only two an order can reach today) |

A confirmed order may hold no laundry. A washed order may be unpaid. Reading the four as one
sequence is the single commonest way to misread this system, which is why the order board labels
them separately and the transition panel makes you choose a dimension before a target.

**Two clocks matter.** `production_accepted_at` starts the production SLA when intake reaches
`ACCEPTED`; `production_ready_at` stops it when production reaches `READY_AT_STORE`. A rewash
clears the second and restamps it on the next completion, because the last completion is the one
the customer experienced. Nothing else may touch either clock.

---

## 2. The spine: a walk-in, from the door to a closed order

The commonest workflow in the shop, and the one every other workflow is a variation of.

| # | What the person does | Screen | Control | Server | Result |
|---|---|---|---|---|---|
| 1 | Customer arrives with a bag | `#/order-requests` | **Phát phiếu** | `POST /internal/v1/stores/{id}/counter-tickets` | A ticket number to say out loud. **The ticket is the customer's identity** — no name, no phone (`DEC-013`) |
| 2 | Record that a bag was taken in | same | **Ghi nhận tiếp nhận** | `POST …/order-requests` | An intake row, and a one-tap path to pricing |
| 3 | Weigh the bag, pick the service | `#/quotes` | **Dịch vụ**, **Khối lượng** | — | Near 6 kg the screen warns about the tier before anything is sent |
| 4 | Price it | same | **Tính giá** | `POST …/quotes` | A revision, its total, and its seal (`snapshot_hash`) |
| 5 | Read the price to the customer; they agree | same | **Khách đã chốt giá** | `POST …/quotes/{id}/acceptance` | An attestation naming the staff member and the exact revision |
| 6 | Open the order | `#/orders` | **Tạo đơn** | `POST …/orders` | commercial `REQUESTED`, intake `AWAITING_HANDOFF`, production `NOT_STARTED`, balance `UNPAID` |
| 7 | Take the bag over the counter | `#/orders` | dimension **nhận đồ** → `RECEIVED_PENDING_INSPECTION` | `POST /orders/{id}/intake-transition` | The shop now holds the goods, and cancelling is no longer a single press |
| 8 | Accept the order for work | same | → `ACCEPTED` + **Đã duyệt lịch** | same | `production_accepted_at` is stamped: **the SLA clock starts here** |
| 9 | Confirm the sale | same | dimension **thương mại** → `STORE_CONFIRMATION_PENDING` → `CONFIRMED` → `ACTIVE` | `POST /orders/{id}/transition` | `ACTIVE` is refused unless intake is `ACCEPTED` |
| 10 | Wash it | same | dimension **sản xuất** → `QUEUED` → `IN_PROCESS` → `QUALITY_CHECK` → `READY_AT_STORE` | `POST /orders/{id}/production-transition` | `READY_AT_STORE` stamps `production_ready_at`: **the SLA clock stops here** |
| 11 | Hand it back | same | → `RELEASED` | same | Production is now terminal |
| 12 | Take the money | `#/orders/{id}` | **Số tiền khách đã trả**, **Khách đã tự lấy đồ**, **Ghi nhận tất toán** | `POST /orders/{id}/settlement` | balance `PAID`, self-collection recorded |
| 13 | Close it | `#/orders` | dimension **thương mại** → `COMPLETED` | `POST /orders/{id}/transition` | Refused unless production is `RELEASED`, fulfilment is complete **and** the balance is settled |

**Every step carries `Idempotency-Key`; every step that changes an order carries `If-Match` with the
row version.** Pressing a button twice does not produce two orders; pressing it against a version
somebody else has already moved is refused, and the screen offers a fresh board rather than a retry.

### 2.1 Prices, exactly as the code fixes them

- `STANDARD_WASH_DRY` is tiered per kilogram: **under 6 kg, 25.000 ₫/kg with a 1 kg minimum;
  from 6 kg, 20.000 ₫/kg.**
- Therefore **5.9 kg costs 147.500 ₫ and 6.0 kg costs 120.000 ₫.** A lighter bag costs more. This is
  a real confirmed rule of the business, not a defect, and the console discloses the threshold to
  the counter before a quote is submitted.
- A bag of 0.4 kg is charged the 1 kg minimum: 25.000 ₫.
- 44 services are published. Some are per item, some per kilogram, per pair, per set, per m².
- A per-item service refuses a fractional quantity (`2.5` shirts → `MISSING_REQUIRED_FACT`).
- A service published as a price *range* (pillows, suits, soft toys) refuses to produce a single
  number: `RANGE_PRICE_REQUIRES_HUMAN`. A person must name the exact price, and no path to record
  that exists yet (§8).

### 2.2 Delivery fees, exactly as the code fixes them

Only `PICKUP_AND_RETURN` uses the distance table:

| Measured one-way distance | Total delivery fee |
|---|---|
| ≤ 2.000 m | 0 ₫ |
| > 2.000 m and ≤ 6.000 m | 10.000 ₫ |
| > 6.000 m | **staff and customer negotiate; the server refuses to invent one** |

`PICKUP_ONLY` and `RETURN_ONLY` are negotiated at *every* distance. An 8 kg bag carried 1,5 km is
160.000 ₫; the same bag at 4 km is 170.000 ₫. Where the fee is unresolved the quote's presentable
total is `null`, and `null` is never rendered as `0`.

---

## 3. The delivery trade

The same spine with three differences:

1. **The fee is inside the quoted total** (§2.2), and the order's fulfilment mode must match the
   mode the price was computed under — an order citing a different mode is refused (HTTP 409).
2. **Money is taken before the goods leave**, as `EXACT_PAYMENT_PREPAID_DELIVERY` (`DEC-023`): the
   shop is never owed money by somebody holding its laundry, and no driver carries cash. The
   settlement is recorded with **Khách đã tự lấy đồ** *unticked*, because nobody has received
   anything yet.
3. **A delivery leg closes the order, not self-collection.** A failed attempt is recorded as a
   failure, costs nothing extra, and the next attempt is a new row. Only a successful `RETURN` leg
   permits `COMPLETED`.

---

## 4. When the shop takes the money

One shape is supported, and it is the same money twice over: **the exact quoted total, in full, in
one settlement.** Anything else is refused by name, with the open decision that owns it:

| What the customer does | Reason code | Owner's decision |
|---|---|---|
| Pays less than the total | `AMOUNT_IS_NOT_THE_EXACT_TOTAL` | `DEC-010` |
| Pays more than the total | `AMOUNT_IS_NOT_THE_EXACT_TOTAL` | `DEC-010` |
| Quote has no single total (unresolved fee) | `NO_PRESENTABLE_TOTAL` | `DEC-003` |
| Quote is a range | `TOTAL_IS_A_RANGE` | `DEC-001` |
| The goods did not go back to the customer at the counter | `COLLECTION_WAS_NOT_BY_THE_CUSTOMER` | `DEC-003` |

The counter sees each of these in Vietnamese, with the reason code verbatim and the decision named,
because a staff member told only "no" goes and finds a workaround.

**Typing money.** The amount is typed as the screen prints it: `170.000` is a hundred and seventy
thousand đồng. A comma is refused (it is the decimal separator in Vietnamese and đồng have no minor
unit) and so is a decimal point that is not a thousands separator — `170000.5` is a mistake to
report, not 1.700.005 ₫ to charge.

**A settlement is written once.** The row is immutable; the balance and the collection flag move in
the same transaction.

---

## 5. When an order ends badly

| Situation | What the software does |
|---|---|
| Customer changes their mind **before** handing the bag over | Cancel in one press. Nothing has been received, washed or paid |
| Customer changes their mind **after** the shop holds the bag, or after any money | Refused: *"work has begun; cancel through cancellation review"*. The order must go `ACTIVE` → `CANCELLATION_REVIEW` → `CANCELLED`, and the cancellation must say what happened to the goods |
| Cancelling from review | Requires a **custody resolution**: `NOT_RECEIVED`, `RETURNED_UNWASHED_REFUNDED`, or `SHOP_FAULT_NO_CHARGE`. It is recorded on the order's event permanently |
| A resolution that contradicts the record | Refused. "We never received it" cannot be said about an order whose custody is recorded; "returned unwashed" cannot be said about laundry that has been in a machine |
| The shop holds the bag but has not accepted it for work (intake `RECEIVED_PENDING_INSPECTION`, `WAITING_PRICE_APPROVAL`, `WAITING_CUSTOMER_RECONFIRMATION` or `WAITING_SLOT_APPROVAL`, commercial before `ACTIVE`), and refuses it | **Không nhận đồ** (`REJECT_INTAKE`, founder ruling R2): intake → `REJECTED`, then commercial → `CANCELLED` directly, with a `rejection_reason` (`NOT_SERVICEABLE`, `DAMAGED_ON_ARRIVAL`, `OTHER`) on the first event and its audit row. The bag goes back; no money can have moved. Never offered at `AWAITING_HANDOFF` (nothing received: that is the one-press cancel) or once intake is `ACCEPTED` (that is cancellation review) |
| A stain or a machine fault found at quality check, or on the shelf before the goods leave | **Giặt lại** (`REWASH`, founder ruling R1): production → `EXCEPTION`, then `EXCEPTION` → `IN_PROCESS` (the backward resume only an exception permits), with a `rewash_reason` (`NOT_CLEAN`, `MACHINE_FAULT`, `OTHER`) on the first event and its audit row. Only on an `ACTIVE` order at `QUALITY_CHECK` or `READY_AT_STORE` whose customer is not recorded as having taken the goods. The price is unchanged; the order then walks forward again. The history reads "Giặt lại · Chưa sạch" |
| A garment brought back **after** the customer took it | A complaint (`DEC-004`, the remedy flow), never a rewash inside the order |
| Any other production interruption | `HOLD` / `RESUME` from the order page. The per-axis `EXCEPTION` route still resumes to the interrupted state **or any earlier one**; an exception is an interruption, never an ending |
| The customer's laundry is finished and they never come back | No workflow. `DEC-005` (storage fee, unclaimed goods) is open |

There is deliberately **no** resolution code for "washed, walked away, paid nothing". A customer
whose laundry has been washed pays and collects, or the goods stay with the shop; the protection is
physical custody, and inventing a fourth code would make that rule negotiable at the counter.

---

## 6. The shop day around the orders

- **`#/` Hôm nay** opens on what needs a person, then money **taken at the counter today** — named
  as money collected, never as revenue and never as profit.
- **`#/orders`** is the board: four labels per order, read as four answers.
- **`#/orders/{id}`** is the working surface: the audit timeline, the settlement panel, the delivery
  legs.
- **The network drops.** The console says so, disables every control that would write, and queues
  nothing. When the network returns, the controls it disabled come back — and only those; a control
  held busy by its own in-flight write stays busy, and one disabled by the person's role stays
  disabled.
- **Somebody else moves the order you have open.** The write is refused, the order does not change,
  and the screen says a colleague changed it and offers a fresh board. Retrying cannot help and is
  not offered.
- **The session ends.** The console shows the signed-out screen and the way back in. It is not a
  fault and is not presented as one.
- **A command is sent twice.** The second returns the first result, marked as a replay.

---

## 7. Running the shop as a business

**Hiring somebody** takes three separate commands on `#/staff`, and the first two are useless
without the third:

1. **Tạo nhân sự** — an identity bound to an OIDC subject. A duplicate subject is refused.
2. **Gán vai trò** — `OWNER_ADMIN`, `OPS_APPROVER`, `OPERATOR`, `DRIVER`, `ACCOUNTANT`, `AUDITOR`.
3. **Gán cửa hàng** — **without this row every store-scoped screen refuses them**, whatever their
   role.

**Disabling somebody** revokes every live session in the same transaction. The last active owner
cannot be disabled — the shop cannot be left without one.

**What each role may actually do**, measured against the running server rather than read from the
published matrix:

| | Orders | Quotes | Takings | Incidents | Approvals | Queue health | AI drafts |
|---|---|---|---|---|---|---|---|
| `OWNER_ADMIN` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `OPS_APPROVER` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `OPERATOR` | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ✅ |
| `AUDITOR` | read | ❌ | ❌ | ❌ | ❌ | ❌ | read |

Most operational routes additionally require a second factor. A control the role may not use is
shown **disabled with the reason**, never hidden: a hidden control teaches staff the capability does
not exist, a disabled one teaches them who to ask.

---

## 8. The AI system: what it does today is refuse

This is the half of the product the specifications describe at greatest length, and in this release
its correct behaviour is to decline. That is a designed state, not an outage.

- **All 13 capabilities are `NOT_AUTHORIZED`.** The gate ladder (`delivery/GATE_REGISTRY.yaml`)
  requires a release manifest signed by three distinct actors before even the lowest AI stage, and
  no such manifest has ever existed.
- **The worker refuses to start** if any external-capability flag is true.
- **`#/shadow`** — the draft queue — states that the machine does not send, and is empty, because
  nothing in this release produces a draft.
- **`#/assistant`** answers from the shop's own records, says on screen that it calls no language
  model and computes no money, and names the question it understood before answering it.
- **`#/exceptions`** exists for messages that left the system without the system knowing — the
  reconciliation queue for a channel that is not connected.
- **Manual send** is the only way a message could leave, and it demands an approved envelope and an
  attestation with the exact content hash.

"The AI works correctly" in this release means: **the refusal is real, it is enforced in more than
one place, and no screen pretends otherwise.**

---

## 9. What the specification describes and this release does not build

Recorded so the gap is visible rather than discovered at the counter. Each is in the console's own
`#/gaps` register, which staff can read.

| Capability | Status |
|---|---|
| Customer records, names, phone numbers, addresses | Not built. A walk-in is a ticket number (`DEC-013`), and no customer aggregate exists (`DEC-015`) |
| ~~Opening an incident at the counter~~ | **Built, 2026-09-18.** `DEC-028` derives the contact scope on the server from the order's binding and stores the complaint in a disposable side table on the `INCIDENT_EVIDENCE` schedule. The two sha256 fields left the request model entirely: a staff member who could name a contact scope could file against a customer of their choosing. `INCIDENT-INTAKE-001` |
| Remedies: rewash, discount, credit | No tables. Every incident stays `OPEN`. The policy is settled (`DEC-004`) and is applied on paper |
| Naming the exact price inside a published range | No path. Range-priced services refuse to quote |
| Promotions | The engine exists and is not wired into the quote path. Every revision discounts 0 |
| ~~Deciding an approval from the console~~ | **Built.** `list_pending` projects `resource_version`, `snapshot_hash` and `rendered_hash`, so a decision can bind exactly what it approved. `ORDER` is decidable with a link to the resource; `QUOTE_REVISION` and `MESSAGE_DRAFT` stay disabled by name, because approving what the console cannot show you is blind approval in a politer font. **Since superseded for both:** `RANGE-PRICE-001` made a quote revision readable, and `MESSAGE-DRAFT-BINDING-001` (2026-09-25) added `GET /internal/v1/stores/{store_id}/message-drafts/{agent_run_id}/binding`, so a `SEND_MESSAGE` card prints the exact stored words above its buttons, refuses to approve a draft edited or rejected after the envelope was raised (the server refuses too), and `#/exceptions` raises the envelope from that read. The send itself is still a named person's manual send; nothing sends automatically |
| ~~An SLA board~~ | **Built, 2026-09-22.** The read model was never the missing half: `ShadowConsoleRepository.sla_risk_board` already existed with migration `0037`'s clock fix, and building a second one would have re-introduced a bug already paid for once. What was missing was a surface — `GET /internal/v1/stores/{store_id}/sla-board` and `#/sla-board`, ordered by the server, each row carrying the query version and the one stated rule that produced it. Per-order `ProductionSlaPolicy` is still undecided and the board says so in the assistant's own words. `OPS-BOARD-001` |
| Payment methods, part payments, deposits, credit | One shape only (§4) |
| Batches, chain of custody, machine cycles, delivery cost capture | Blocked on `SHOP-INSTRUMENT-001` |
| ~~A daily operations dashboard and CSV export~~ | **Partly built, 2026-09-22.** The export exists and is versioned: `#/exports` raises an `EXPORT_SANITIZED_DATA` envelope, an owner who is not the staff member that defined it approves it, and the file is released once with its query version and a digest recorded in `data_exports`. It carries no incident free text, no evidence summary and no contact key. One day at a time, cut on `orders.created_at` — which is **not** the boundary the takings figure uses, and both surfaces now say so. A date range, and any dashboard with a rate in it, remain unbuilt: `FR-RPT-005` still needs a denominator nothing supplies. `OPS-BOARD-001`, `EXPORT-FIX-001` |

### 9.1 Where a specification and the code disagree

These are documentation defects, not behaviour defects — the code is the authority and behaves as
§2–§7 describe. They are listed so the next reader is not misled by the older document.

| Document | Says | Actually |
|---|---|---|
| `docs/STAFF_CONSOLE_ENGINEERING_SPEC_V1.md` §4 | Payment is not built and no order can reach `COMPLETED` | Both landed; orders complete daily |
| same, §4 | Intake, production and delivery have no surface | All three shipped (`CONSOLE-LIFECYCLE-001`, `FULFILMENT-001`) |
| same, §11 | Store assignment has no route | `POST /internal/v1/staff/{id}/stores/{id}` exists |
| `specs/SECURITY_RELIABILITY_SPEC_V1.md:295` | `AUDITOR` reads everything | `AUDITOR` is refused quotes, incidents, approvals, takings and queue health |
| `specs/DOMAIN_DATA_API_SPEC_V1.md:1336` | An error envelope with `ok`, `trace_id`, nested `error` | The server sends `{"detail": …}` in three shapes |
| `specs/ENGINEERING_SPEC_V1.md` §10.6 | TypeScript strict mode | The console is plain ES modules, deliberately, with the reasoning recorded in `apps/web/README.md` |
| `specs/AGENT_SYSTEM_AND_EVAL_SPEC_V1.md` | An agent opens incidents with server-computed hashes | The agent path is unauthorised, so nothing computes them, and the staff path inherited the requirement |
| `docs/STAFF_CONSOLE_ENGINEERING_SPEC_V1.md` §2 | The SLA risk read model is unrouted | Routed since `OPS-BOARD-001`: `GET /internal/v1/stores/{store_id}/sla-board` behind `#/sla-board`. The per-order policy it also names is genuinely still undecided |
| same, §14 | No versioned read models, so there is no dashboard and no export | The day summary and the sanitized export are both versioned queries with pinned identifiers. The row is right about the dashboard `FR-RPT-005` asks for, which needs a denominator nothing supplies, and wrong that nothing is exported |

---

## 9.2 Every control on every screen, and which ones are driven

The console's fifteen modules present **218 distinct interactive controls** — buttons, links, form
fields, selects, toggles, copy buttons, the navigation, the store picker. They were enumerated by
reading each module rather than by clicking around, so conditionally-rendered controls are in the
count.

| Screen | Controls | of which write |
|---|---:|---:|
| `app.js` (the shell, on every route) | 49 | 2 |
| `orders.js` | 29 | 3 |
| `quotes.js` | 28 | 2 |
| `staff.js` | 17 | 5 |
| `manualSend.js` | 13 | 2 |
| `assistant.js` | 10 | 5 |
| `orderRequests.js` | 10 | 2 |
| `shadow.js` | 10 | 3 |
| `today.js` | 10 | 0 |
| `approvals.js` | 9 | 0 |
| `orderDetail.js` | 9 | 1 |
| `incidents.js` | 8 | 1 |
| `components.js` (shared factories) | 7 | 0 |
| `exceptions.js` | 6 | 2 |
| `system.js` | 3 | 0 |
| **total** | **218** | **28** |

**Twenty-eight of them change the business.** Those are the ones worth accounting for one by one:

| Driven end to end in a browser | By |
|---|---|
| `orderRequests.issue-ticket`, `orderRequests.create-submit`, `quotes.accept`, `orders.create-submit`, `orders.leg-submit` | `verify_daily_operations.py` |
| `quotes.create-submit` | both drivers |
| `orders.move-submit`, `orderDetail.settlement.submit`, `staff.create.submit`, `staff.role.submit`, `staff.store.grant`, `staff.store.revoke`, `staff.disable.submit`, `assistant.send`, `shell.sign-out` | `verify_workflow_conformance.py` |

**Fifteen of the twenty-eight are driven; the other thirteen cannot be reached in this release**, and
each for a stated reason rather than an omission:

- `shadow.card.approve` / `.edit` / `.reject` — no agent draft exists, because every AI capability
  is `NOT_AUTHORIZED`.
- `exceptions.unknown.confirm-sent` / `.confirm-not-sent` — no channel is connected, so no send has
  an unknown outcome.
- `manualSend.prepare.submit` / `.attest.submit` — both need an approved envelope, and nothing in
  this release approves one.
- `incidents.form.submit` — blocked; §9 and the decision packet.
- `assistant.suggestion.*` (four) — rendered only when the turn history is empty.
- `shell.appbar.signout` — the same command as `shell.sign-out`, drawn a second time in the phone
  app bar.

The remaining 190 controls are reads, navigation and local interactions. Many are exercised
incidentally by the drivers; that is not the same as being asserted, and this document does not
claim it is.

## 10. What driving these workflows found

`WORKFLOW-CONFORMANCE-001`, 2026-09-10. Every one of these was invisible to 1.168 passing tests, to
the contract checks, and to a happy-path browser run.

| Finding | Severity | Fixed |
|---|---|---|
| A part payment — the commonest till mistake — reached the counter as "Dữ liệu nhập không hợp lệ". The server had answered `NOT_SUPPORTED` / `AMOUNT_IS_NOT_THE_EXACT_TOTAL` / `DEC-010`; the console's classifier read only the *plural* `reason_codes` of a different route and dropped all three | HIGH | ✅ |
| `parseDong("170000.5")` returned 1.700.005 — ten times the amount — while the field's own hint promised decimals were refused | MEDIUM | ✅ |
| The store picker listed shortened UUIDs to an owner with two shops, months after every shop got a name | MEDIUM | ✅ |
| A commercial move on an order whose laundry was already finished restamped `production_ready_at` to the moment a button was pressed | MEDIUM | ✅ |
| An expired quote could still be accepted; the refusal arrived one step later, at order creation, after the customer had agreed | MEDIUM | ✅ |
| A stale-version refusal was reported to the counter as a generic server rejection rather than "somebody else changed this order" | LOW | ✅ |
| Holding a finished order — for a shelf audit, a disputed item — cleared its completion time and restamped it when the hold was lifted, though nothing had happened to the laundry. `0037`'s own comment says a hold does not reset it | MEDIUM | ✅ |
| A settlement refused for a lost race (`STALE_VERSION`) arrived under the "not supported" heading, telling the operator the shop does not do this when the only useful move is a reload | MEDIUM | ✅ |
| Two panels on the staff screen disagreed about a departed member: "Gán vai trò" refused them, "Gán cửa hàng" wrote them a live membership | MEDIUM | ✅ |
| A second reviewer deciding the same AI draft received HTTP 500 — an ordinary race reported as a broken server, on a queue the console already had the right words for. Latent: no draft exists in this release | MEDIUM | ✅ |
| Incident intake demands two hashes nothing produces, while the field hints told staff to copy them from somewhere | HIGH | Decision packet + honest UI |

Two of those were found in the *first round's own fixes* — the settlement gloss covered only the
refusals that name a decision and not the four that name a state, and the ready-clock fix stopped a
commercial move rewriting the completion time while leaving a hold doing the same thing. That is
the third consecutive round in this repository where the recheck found defects in the round before
it, which is the argument for running one.
