# Staff Console Redesign Spec V2 — task-first, zero-paste, one obvious next step

Status: APPROVED FOR BUILD 2026-09-25 (founder delegation: "redesign and rebuild entire UI until no
friction … user centric 100%"). Supersedes the presentation rules of
`STAFF_CONSOLE_UX_REFACTOR_SPEC_V1.md`; keeps every one of its invariants (§2 there) except the one
this document explicitly amends (§4.1, the dual-language rule).
Scope: `apps/web` (whole console), plus the small server additions in §6 that a task-first console
needs and that must not be faked client-side.
Audience: the counter operator at a Nha Trang laundry, on a 390 px phone, standing, one hand; the
owner at 7am reading "is anything waiting for me"; an approver deciding against a 10-minute TTL.

## 1. Diagnosis (measured 2026-09-25, real API, DB migrated from empty, one filmed day of work)

The V1 console is *correct* and *honest* and nearly unusable for daily work, because its unit of
design is the **server command**, not the **staff task**. Every screen is a stack of panels titled
`Lệnh · POST /internal/v1/…`, one form per endpoint, with the rules explained in warning boxes
above the fields.

| Measure (390×844 phone) | V1 |
|---|---|
| Walk-in order, counter to "khách đã lấy đồ" | ~16 manual state commands on 3 dropdown axes + 5 screens |
| Values a person pastes/types that the server already knows | order create: contact UUID, quote UUID, revision, 78-char hash, mode, accept time; transitions: order UUID + row version; delivery leg: order UUID (no prefill path exists); credit redemption: 4 values |
| Order detail height / words | 5 627 px / 703 words |
| Staff screen / Gaps screen | 5 293 px / 850 words · 9 962 px / 1 800 words |
| Visible engineering register | endpoint paths, UUIDs, `JCS-SHA256-V1:…`, `(ORDER_STATE_TRANSITION)`, "Bản ghi bạn đang giữ" |
| Chrome defects | "Thoát" wraps to "Tho/át"; dark gradient app bar; sticky submit overlaps field labels |
| Functional gaps found by the inventory | quote→order hand-off drops fulfilment mode (delivery orders refused unless re-picked); ON_HOLD unreachable; delivery legs need a pasted UUID; order has no link to its incidents |

## 2. Principles (every slice is reviewed against these, in this order)

1. **Task first.** A screen answers one staff question ("khách này gửi đồ", "đơn nào xong rồi",
   "có gì chờ tôi duyệt"). The unit of navigation is the *thing* (order, quote, complaint, person);
   commands live on the thing as its next actions. No screen is organised by endpoint.
2. **Zero paste.** No daily workflow asks a person to type or paste an identifier, a hash, a
   revision or a row version. The console carries what the server just returned. A paste field may
   exist only for a value no route can supply (approval id from another device), and then only
   under "Nhập mã thủ công".
3. **One obvious next step, decided by the server.** An object page shows at most one primary
   button, labelled with the business verb ("Bắt đầu giặt", "Báo sẵn sàng", "Thu tiền"). Which
   steps are legal is computed server-side from the domain state machines (§6.1) — the console never
   holds a copy of a transition table (ENGINEERING_SPEC_V1 §530 stands).
4. **Data before prose.** The first screenful is the data and the action. Every sentence of
   explanation moves into one of three tiers (§4). Target: ≤ 60 visible words above the fold on any
   daily screen, excluding data.
5. **Human words.** Operators see Vietnamese glosses, formatted money, "Phiếu 17", relative times.
   Tokens, UUIDs, hashes, versions and endpoints live in "Chi tiết kỹ thuật" (tier 3).
6. **Honest, not noisy.** Nothing the V1 console disclosed is lost: every safety fact is still
   there at the point of action, every refusal is still shown with its reason, every denied control
   is still visible with why. What changes is *where* the long form lives.
7. **Thumb reach.** Primary actions sit in a sticky action bar above the tab bar. Every target
   ≥ 44 px (48 px for primary). One-handed on a 360 px screen.
8. **Feedback you can trust.** Every write ends in exactly one of: a toast + the object re-read and
   re-rendered in its new state; or an inline refusal at the button with the reason and the way out.
   Never a bare code, never silence, never an optimistic state.

## 3. Design system V2 ("Sạch")

### 3.1 Tokens (`styles/tokens.css`)
Token *names* are kept (every existing class keeps working); *values* change.
- Neutrals: canvas `#F4F6F8`, surface `#FFFFFF`, sunken `#EEF1F4`, hairline `#E3E7EC`, strong
  border `#C9D0D8`; text `#0F1720`, secondary `#475467`, muted `#5F6B7A` (AA on white and canvas).
- Brand (laundry teal): 900 `#063B33` · 800 `#075347` · 700 `#0A6B5C` (primary) · 600 `#0E8571` ·
  400 `#5EC2A8` · 200 `#C4EADF` · 050 `#ECF8F4`. There is still no separate "success green"
  (V1 rule kept: green means confirmed).
- State sets (fg/bg/border) unchanged in meaning: ok · warn · danger · info · neutral.
- Type scale (system font, Vietnamese-safe): 12 · 14 · 16 (body) · 17 · 20 · 24 · 28 (page title)
  · 40 (money hero); weights 400/600/700; tabular numerals for all money and counts.
- Radius 8/12/16/20; elevation: hairline + one soft shadow; motion 120/200 ms, off under
  reduced-motion. Dark mode follows the OS (tokens redefined), never a toggle.

### 3.2 Shell
- **App bar** (56 px, light surface, hairline): store name (tap → store switcher sheet when
  multi-store), screen title on desktop only, avatar button with initials → account sheet (name,
  roles glossed, 2-step status, "Đăng xuất"). No gradient. "Thoát" can no longer wrap.
- **Page header** inside the screen: large title (28 px) + optional one-line subtitle + at most one
  header action. No lede paragraphs.
- **Phone tab bar** (5 slots, 64 px + safe area): Hôm nay · Đơn hàng · **＋ Nhận đồ** (raised
  primary) · Duyệt (badge) · Thêm. Desktop ≥ 64rem: left sidebar, same destinations grouped
  (Vận hành · Tin nhắn & AI · Quản trị), "＋ Nhận đồ" as the sidebar's primary button.
- **Thêm** is a real screen (`#/more`): grouped rows with icon, name, one-line purpose; a denied
  destination is a disabled row with its reason visible (V1 invariant 5).
- Standing banners (offline, session ended, unreachable, store list unread) keep their exact
  meaning; restyled as a compact strip.

### 3.3 Components (`src/ui/kit.js`, new; `components.js` keeps its exports, restyled)
| Component | Use |
|---|---|
| `page({title, subtitle, action, back})` | every screen's header |
| `section({title, action, children, inset})` | grouped content; replaces `panel()` on rebuilt screens |
| `listRow({href/onClick, leading, title, meta, trailing, status, chevron})` | every list of things; 56–72 px |
| `statusPill(state, text, token)` | one status word, colour + dot; token only in `title` |
| `moneyHero({label, amount, caption})` | the number that matters (takings, phải thu) |
| `progress(steps, current)` | the order's life at a glance (Nhận → Giặt → Sẵn sàng → Đã trả) |
| `actionBar(primary, secondary)` | sticky bottom action(s) above the tab bar |
| `sheet({title, body, actions})` | forms and confirmations (`<dialog>` bottom sheet on phone, centred on desktop) |
| `toast(text, state)` | success feedback, `aria-live=polite`, 4 s |
| `infoButton(topic, ...body)` | tier-2 explanation: ⓘ opens a sheet |
| `techDetails(entries)` | tier-3 drawer: ids, hashes, tokens, versions, copy buttons |
| `segmented(options)` | 2–4 way filters (Đang làm / Sẵn sàng / Xong) |
| `searchField` · `emptyState({icon, title, action})` · `skeletonRows` | lists |
| `stepperInput` (− qty +) · `moneyInput` (numeric keypad, "₫" suffix, live formatted echo) | forms |
| `confirmButton` (two-press for destructive) · `inlineAlert({state, title, body, actions})` | writes |

All built with `h()` (no HTML sinks), all state in memory (no storage), all keyboard- and
screen-reader-reachable, focus returned correctly after a sheet closes.

## 4. The honesty tiers (replaces "guardrail box above every form")

| Tier | Where | What goes there | Rule |
|---|---|---|---|
| **1 — at the action** | one short line under/beside the control or in the confirmation sheet | facts that change what the person does *now*: money is final; MANUAL_SEND_RECORDED ≠ delivered; the amount must be the exact total; a refusal's reason and way out; truncation; denied-control reason | ≤ 25 words, never collapsed (V1 policy kept) |
| **2 — one tap** | `infoButton` sheet (ⓘ) or `explain` | rules, scope, "why is this button off", what a figure includes/excludes | plain question as the topic; full V1 text may live here verbatim |
| **3 — technical** | `techDetails` drawer, closed by default | UUIDs, hashes, row versions, revisions, enum tokens, endpoint names, query versions, audit actor ids | copy buttons; printed expanded |

The disclosure registry keeps working unchanged: detection is lexical, so a sentence moved inside
`infoButton`/`explain` keeps its slot id (same module, same key). Each slice regenerates the
registry and reports its slot delta; a removed slot is allowed only when (a) the thing it explained
no longer exists on screen (e.g. "Bản ghi bạn đang giữ" once no one types a version), or (b) it
is merged into a statement that carries the same fact. The pinned total in
`test_console_disclosure_contract.py` is updated with an accounting comment per slice. Bound slots
(MODEL_SEAM ×2 in `assistant.js`, ABSENT_TABLE ×4 + RESPONSE_SHAPE ×1 in `gaps.js`, POLICY_BOUND in
`orderDetail.js`) must keep their exact text and module.

### 4.1 Amendment — the dual-language rule (V1 §2.9)
V1 rendered every enum as `Gloss (TOKEN)` everywhere. V2: operator surfaces render the gloss only,
through the same single map (`enumVi`) — never a per-screen hand translation; the token is kept in
the element's `title` and in tier 3. Audit/tech surfaces (timeline, tech drawer, `#/system`) keep
`Gloss (TOKEN)`. An enum with no gloss still renders its raw token (never blank), so a missing
gloss stays visible and gets fixed.

## 5. Information architecture and screens

Routes kept (deep links and tests keep working) unless marked NEW. Per-screen acceptance is in §8.

### 5.1 Hôm nay `#/`
Greeting line with date. **Tiền hôm nay** `moneyHero` (collected, count, refunded/net if any; ⓘ
for what it includes). **Cần làm** list: one `listRow` per non-empty queue (Chờ duyệt, Sự cố đang
mở, Tin AI chờ duyệt, Gửi chưa rõ kết quả, Đơn trễ mốc) with count badge → the queue; empty queues
collapse into one line "Không có gì chờ bạn". **Đơn hôm nay**: counts by status as tappable chips →
filtered order list. Two quick actions: "＋ Nhận đồ" and "Khách tới lấy đồ" (ticket search).

### 5.2 ＋ Nhận đồ `#/new` (NEW — replaces the create path through Tiếp nhận → Báo giá → Đơn hàng)
A 3-step flow, one screen, state in memory, back/forward without loss:
1. **Khách** — "Khách vãng lai" (default, one tap issues the counter ticket) or "Khách đã nhắn
   qua kênh" (contact picker when a read exists; until then the manual-code field under
   "Nhập mã thủ công"). Result: big "Phiếu 17".
2. **Đồ & giá** — delivery mode as `segmented` (Tự mang tới & tự lấy / Lấy tận nơi / Trả tận nơi /
   Lấy & trả); for delivery: distance or agreed fee. Lines: service picker grouped by category with
   search; quantity `stepperInput` with unit; basis (default "Nhân viên đã cân"). The server price
   renders live after "Tính giá" as a receipt: lines, subtotal, promotion, **total**. The 6 kg cliff
   notice stays tier 1 beside the quantity. RANGE services: the band input and "Chốt giá trong
   khoảng" inline (DEC-029 flow unchanged).
3. **Xác nhận** — receipt summary, "Khách biết tiệm qua đâu" (chips), and one primary button
   **"Khách đồng ý — tạo đơn"** which runs, in order and each only after the previous succeeded:
   quote acceptance → order create (all values from the responses just received) → shows the
   order page. If any step is refused, the flow stops there, shows the refusal, and the next press
   resumes from that step (idempotency keys per step, reset only when the input changes).
The order's own "Nhận đồ" step (§6.1) is offered on the order page, pre-selected when the goods are
on the counter.

`#/order-requests` and `#/quotes` remain as **lists** under Thêm (history, resume a quote, add a
revision, owner read-only view from Duyệt), rebuilt on the kit; "Tiếp tục" on an unfinished quote
re-enters `#/new` at step 2/3 with everything restored from the reads in §6.2.

### 5.3 Đơn hàng `#/orders`
Search field "Số phiếu…" at the top (digits → ticket lookup, today by default, date chip to
change). `segmented`: Đang làm · Sẵn sàng · Chờ giao · Xong · Tất cả (client filter over the server
list; "open" scope from the server). Rows: **Phiếu 17** · status pill · mode icon · time ago ·
trailing money (Phải thu / Đã thu). Truncation line kept (tier 1). The generic transition form and
the delivery-leg form are **removed** — their capabilities move onto the order page.

### 5.4 Chi tiết đơn `#/orders/:id`
- Header: "Phiếu 17", created time, status pill, mode.
- `progress`: Nhận đồ → Đang giặt → Sẵn sàng → Đã trả (from the four axes; presentation only).
- **Money card**: Phải thu / Đã thu / Đã hoàn, big.
- **Next step** (`actionBar`): the server's `next_steps` (§6.1) → one primary, the rest in "Thao
  tác khác". Money steps open a sheet: *Thu tiền* — amount due shown large, `moneyInput` (typed on
  purpose: SETTLEMENT-001 two-sources rule kept), "Khách lấy đồ luôn" switch; *Khách trả trước*;
  *Khách đã nhận đồ* (prepaid collection). Delivery modes: "Giao thành công / Giao không thành
  công" (legs, from `next_steps`). Cancellation: two-press, with the custody resolution chips when
  the server says review is required.
- Sections: Thông tin (source, quote link, mode), Giao nhận (legs), Khiếu nại (incidents of this
  order, "Ghi khiếu nại"), Khoản giảm trừ (credits, copy code), Lịch sử (5 latest, "Xem tất cả").
- `techDetails`: order id, quote id/revision, row version, 4 raw axes with tokens.

### 5.5 Duyệt `#/approvals`
Queue first, nothing above it but the title. Each item is a card: what is being approved in human
words (the exact bound content rendered — money, message text, export columns, remedy — exactly as
V1 renders and compares it), countdown pill, requester, and two large buttons **Từ chối** /
**Duyệt**. All V1 approval invariants unchanged (envelope-store reads, binding comparison before
render, content above an enabled Duyệt, refuse-only when stale, decisions echo the queue row).
`segmented`: Chờ duyệt · Giá trong khoảng hôm nay. "Giới hạn của màn hình này" becomes an ⓘ.

### 5.6 Khiếu nại (Sự cố) `#/incidents`, `#/incidents/:id` (NEW detail route)
List rows: Phiếu N · summary · status · age. Detail page: complaint text, order link, and the
remedy flow inline — options read automatically, kind chips, the server ceiling shown before any
number is typed, garment picker, amount `moneyInput`, proposals list with the server's `next_step`
as the button ("Thực hiện bồi hoàn" / "Chờ chủ tiệm duyệt"). `#/remedies` stays as a route (deep
links, `?incident=`) and redirects into the detail view; credit redemption moves onto the quote
receipt in `#/new` ("Dùng khoản giảm trừ" → pick from the customer's unused credits when readable,
else code entry) with revision/hash carried from the quote read.

### 5.7 Tin nhắn & AI
- **Bản nháp AI** `#/shadow`: draft cards as chat bubbles (untrusted text as text nodes), three
  clear actions (Duyệt / Sửa rồi duyệt / Từ chối), reason as chips + free code.
- **Trợ lý AI** `#/assistant`: chat layout kept (it is already the right pattern); restyled.
- **Ngoại lệ & gửi tay** `#/exceptions`: unknown sends as cards with the two resolutions (danger
  styling kept for CONFIRMED_SENT); manual send as a 3-step stepper (Đọc tin → Xin duyệt → Khoá →
  Ghi nhận đã gửi) carrying every value forward; MANUAL_SEND_RECORDED ≠ delivered stays tier 1 at
  the attest button and on its result. Consent refusals (CONSENT-TRANSACTIONAL-001) render as a
  human alert with the release path.

### 5.8 Quản trị
- **Nhân sự** `#/staff`: people list (avatar initials, name, role pills, status); "＋ Thêm nhân
  sự" sheet; tap a person → person sheet with "Thêm vai trò", "Gán cửa hàng / Thu hồi" (store
  picker from the session's stores; manual UUID only under "Nhập mã thủ công"), "Vô hiệu hoá"
  (two-press). No UUID typing for anyone listed.
- **Hệ thống** `#/system`, **Xuất dữ liệu** `#/exports`: restyled on the kit; exports as a 3-step
  stepper (Chọn ngày → Xin duyệt → Xuất tệp).
- **Việc chưa hỗ trợ** `#/gaps`: grouped accordion, one row per gap with title + one-line status;
  full entry on expand. Bound `missing:` sentences unchanged.

## 6. Server additions (not faked client-side)

### 6.1 `ORDER-STEPS-001` — server-derived next steps and composite business steps
- `OrderViewResponse.next_steps: list[{step, primary}]`, computed by dry-running the **domain**
  transition functions against the order's current state (one source of truth; no new table).
- `POST /internal/v1/orders/{id}/steps` `{step, slot_approved?, custody_resolution?}`, `If-Match:
  row_version`, `Idempotency-Key`. Executes a named business step as the sequence of existing
  domain transitions **in one transaction**, each still written as its own event + audit + outbox
  row (the per-transition audit trail is unchanged). All-or-nothing.
- Steps: `RECEIVE` (intake → RECEIVED_PENDING_INSPECTION, commercial → STORE_CONFIRMATION_PENDING
  → CONFIRMED, intake → ACCEPTED with the caller's explicit `slot_approved`, commercial → ACTIVE —
  refused, not partially applied, if any readiness fact is missing), `START_WASH` (QUEUED →
  IN_PROCESS), `QUALITY_CHECK`, `MARK_READY` (READY_AT_STORE), `HOLD` / `RESUME`, `RELEASE`,
  `COMPLETE`, `HAND_OVER` (RELEASE + COMPLETE when the settlement/collection/delivery facts allow),
  `CANCEL` (direct, or into review with `custody_resolution` as today).
- Existing per-axis transition routes stay (compatibility, tests, audit tooling).
### 6.2 `READ-ENRICH-001` — reads the task-first console needs
- Quote reads: `order_request_id`, `contact_binding_id`, `fulfillment_mode` (fixes the dropped-mode
  hand-off and lets a listed quote resume).
- Order-request list: `ticket_number`, `order_id` when converted (picker hides converted ones).
- Order read: `delivery_legs[]`, `required_delivery_legs_succeeded`, `settlement_shape`.
- SLA items and incident list/detail: `ticket_number` of the order.
- `GET /internal/v1/stores/{s}/orders/{id}/incidents` (order page "Khiếu nại").
- `GET /internal/v1/stores/{s}/incidents/{id}` if not already served (detail route).
Each new route gets a `ROUTE_SCOPE` entry and membership enforcement in its repository.

## 7. Invariants (unchanged, re-checked by every slice)
V1 §2 items 1–10 and the B8 list of `docs/…/test constraints`: no `_vnd` arithmetic, `money()`
only; no HTML sinks; only `localStorage["staff_store_id"]`; `Idempotency-Key` on every write, no
auto-retry, no offline queue; `If-Match` on CAS writes; denied controls visible with reason;
truncation disclosed; `needsStore` or internal store guard; approvals binding rules; manual send
raised from server read values with no recipient; every path literal is a served route.

## 8. Acceptance (per slice, measured in a real browser against the real API)
- 390×844: the primary action of every daily screen is visible without scrolling.
- Walk-in order from "＋ Nhận đồ" to order created: ≤ 8 taps + the typed quantities, zero pasted
  values. Order from created to completed: one primary tap per real-world event (nhận đồ, bắt đầu
  giặt, xong, sẵn sàng, thu tiền & giao).
- No UUID, hash, token, endpoint path or row version visible outside tier 3.
- No `[object Object]`, `undefined ₫`, `NaN` anywhere (existing defect watch).
- Contract tests, disclosure registry (regenerated, delta accounted), stub browser suite, both
  real-API scripts (updated for the new structure) green; `sw.js` regenerated.
- Filmed end to end and reviewed twice — as the person at the counter and as a reviewing senior
  engineer — with every friction or defect found fixed and re-filmed (`CONSOLE-FILMED-REVIEW-002`).

## 9. Slicing
| Item | Owner | Depends on |
|---|---|---|
| `CONSOLE-REDESIGN-000` spec + design system + shell + nav + kit | lead | — |
| `ORDER-STEPS-001`, `READ-ENRICH-001` | domain engineer | — |
| `CONSOLE-REDESIGN-001` ＋ Nhận đồ flow, quotes & requests lists | console | 000, READ-ENRICH |
| `CONSOLE-REDESIGN-002` orders list + order page (steps, money, legs, cancel) | console | 000, ORDER-STEPS, READ-ENRICH |
| `CONSOLE-REDESIGN-003` Hôm nay, Duyệt, Bảng trễ hạn | console | 000 |
| `CONSOLE-REDESIGN-004` Khiếu nại + bồi hoàn | console | 000, READ-ENRICH |
| `CONSOLE-REDESIGN-005` AI & tin nhắn (shadow, assistant, exceptions, manual send, consent) | console | 000, CONSENT-TRANSACTIONAL-001 |
| `CONSOLE-REDESIGN-006` Nhân sự, Hệ thống, Xuất dữ liệu, Việc chưa hỗ trợ, Thêm | console | 000 |
| `CONSOLE-FILMED-REVIEW-002` film, review, fix, re-film | lead | all |

Rollback: each slice is a revert of its own commits; server additions are additive (new field, new
route) and the old routes stay served.
