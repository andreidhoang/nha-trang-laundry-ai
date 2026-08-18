# Staff Console UX Refactor Spec V1

Status: LANDED 2026-08-17 — WS1–WS6 all implemented and verified (contract tests 11/11, full suite
726 passed/0 failed, ruff + format + mypy clean, `sw.js` regenerated). §7 upstream dependencies and
§8 queue slicing remain open; the auth/framework decisions live in
`docs/DECISION_REQUEST_AUTH_IDP_AND_FRONTEND_FRAMEWORK_2026-08.md` (DEC-011/DEC-012, OPEN).
Original scope: `apps/web` only (staff console PWA). Baseline: audit of HEAD 2026-08-16.
Audience: Vietnamese shop owner and counter staff using the console all day, mostly on phones.

## 1. Goal and non-goals

**Goal:** make the console operable as the primary daily tool for a Vietnamese laundry owner — fastest path to the day's work, zero unnecessary reading, natural Vietnamese, nothing ambiguous.

**Non-goals:**

- No framework, build step, or npm tree (decided in `apps/web/README.md:22-53`; changing that is its own queue item with supply-chain evidence).
- No new backend capability. Items that need API work are listed as upstream dependencies (§7), never faked client-side.
- No removal of mandated disclosures. The honesty chrome (gap notices, "not a KPI", MANUAL_SEND_RECORDED ≠ delivered, capability refusals) is spec-mandated and contract-adjacent. This refactor **reorganizes** explanation; it never deletes a disclosure, never hides a limitation, never hides a denied control.

## 2. Invariants the refactor must preserve

Contract-tested in `apps/api/tests/test_staff_console_contract.py` and `apps/web/README.md:83-103`. Any slice that breaks one is rejected at review:

1. No arithmetic on `*_vnd`; `null` money never renders as `0`; all money via `format.money()`.
2. No raw-HTML sink anywhere; all dynamic strings as text nodes (`dom.js` self-scan stays green).
3. Device persistence stays limited to the single `localStorage` key `staff_store_id`. **Consequence: UI preferences (collapsed explanations, nav order) may live only in memory for the session — never in storage.**
4. Writes: no auto-retry, no offline queue, `Idempotency-Key` on every mutation, strong-quoted `If-Match` for CAS.
5. Denied controls shown disabled with reason, never hidden. Unsupported capabilities get a `#/gaps` entry, never silent absence.
6. Hidden list truncation is forbidden; `isTruncated` disclosure stays.
7. `sw.js` is generated — regenerate via `uv run python scripts/generate_staff_console_manifest.py` after any asset add/remove/rename.
8. Every screen declares fetch freshness (T3); list routes stay within `LIMIT ≤ 200`.
9. Timezone pinned `Asia/Ho_Chi_Minh`; enum tokens render as `Gloss (TOKEN)` via `enumLabel`.
10. Typed form input survives session end and connectivity loss (P8 regression surface).

## 3. Audit findings (evidence base)

### 3.1 Defects (fix regardless of refactor)

| # | Defect | Location |
|---|---|---|
| D1 | Typo in the `UNSUPPORTED` gloss: currently “chưa trả lứng được”. Correct Vietnamese: “chưa trả lời được” (the word is l-ờ-i, meaning “to answer”). Diff: replace `lứng` with `lời`. | `src/core/i18n.js:195` |
| D2 | `ariaBusy` camelCase prop becomes unknown attribute; busy state never exposed to AT | `src/ui/components.js:329` (must be `"aria-busy"`) |
| D3 | Result lines leak raw English enums instead of `enumLabel` | `orders.js:601`, `staff.js:339`, `exceptions.js:215,227,729` |
| D4 | RBAC denial reason shows raw `OWNER_ADMIN, OPS_APPROVER` though role glosses exist (`i18n.js:170-175`) | `src/core/rbac.js:143-144` |
| D5 | `ENUM_GLOSS` gaps: incident `OPEN/CLOSED`, incident categories (`SERVICE_QUALITY`, `AUTOMATED_MESSAGE_ERROR`), shadow `terminal_outcome`/`terminal_code`, audit `entry.action` | `src/core/i18n.js:110-196`; seen at `incidents.js:202,232-233`, `shadow.js:164-165`, `orderDetail.js:85` |
| D6 | Dead `NOT_SUPPORTED` branch: `errors.js` never emits that kind and `ApiError.detail` is a string, so `error.detail.reason_code` is unreachable | `orderDetail.js:178-186` vs `errors.js:18,139` |
| D7 | Stale copy: guardrail says "gán cửa hàng chưa có API" but the screen ships STORE-ASSIGNMENT-001 | `staff.js:253-256` |
| D8 | Settlement panel formats money inline (`toLocaleString` + ` ₫`) bypassing `money()` and its integer guard; idempotency key `settlement-${orderId}` never resets on edit — a rejected amount has no on-screen recovery if the server recorded the attempt | `orderDetail.js:167,173` |
| D9 | Countdown reads `còn 9 phút 30s` / `còn 45s` / `còn 10 phút 0s` — mixed symbols, awkward | `src/core/format.js:128-140` |
| D10 | `manifest.webmanifest` locks `orientation: portrait-primary` — bad for counter tablets | `manifest.webmanifest:10` |
| D11 | Dead code: `stateBadge` alias + re-exports nobody imports; dead CSS (`.split--wide-detail`, `.table-scroll`, `dialog`, `.row--end`, `.card--flush`, `.card__title`, `.tabular`, `[data-theme]` override blocks that nothing sets) | `components.js:603`; `components.css`, `tokens.css:176-215`, `layout.css:356-398` |
| D12 | Disabled-control labels at `opacity: 0.55/0.5` fall below AA contrast — and this console deliberately shows many disabled-with-reason controls | `base.css:242-245`, `layout.css:245-249` |
| D13 | `inputMode` camelCase at `orderDetail.js:192` (works, inconsistent with `inputmode` elsewhere) | `orderDetail.js:192` |

### 3.2 Noise (the core problem for daily use)

Every screen leads with 2–5 explanation blocks before any data; 18 `guardrail:` notices across 10 screens. Worst offenders:

- **approvals.js** (`limitsPanel()`, lines 164-279): 4 notices + 3 definition rows + guardrail before the queue — the day's most time-critical list sits several phone-screens down.
- **orders.js**: create-form opens with a 2-paragraph warn notice (489-507); the board (primary content) is the third panel.
- **today.js**: 5 tiles each repeating a long scope sentence (72-135) — the landing screen is small print.
- **staff.js**: 4 panels × guardrail + ordering notice (841-851).
- Long uppercase Vietnamese eyebrows (e.g. `orders.js:718`) scan poorly; dimension badges render full `Gloss (TOKEN)` ×4 per order card and wrap heavily at 360px.
- No collapse/dismiss mechanism exists for repeat users.

### 3.3 Vietnamese terminology

Strong base (đơn hàng, tất toán, đối soát, bản nháp AI…). Fix list:

- `MFA` untranslated in user-facing spots → "xác thực hai bước" (`system.js:207`, `app.js:159,166`, `rbac.js:149`, `staff.js:402`).
- Engineering register leaks into operator copy: `route`, `API`, `read model`, `aggregate`, `endpoint` (`orderDetail.js:407`, `staff.js:394,421`); keep jargon only on `#/gaps`.
- Calques: `bộ chạy agent` (i18n.js:186), `worker gửi` (187), `phong bì`/`envelope` (consistently explained — keep term, shorten explanation), `Kết cục lượt chạy` → "Kết quả lượt chạy" (shadow.js:164), `quá hạn giữ chỗ` → "quá hạn khóa tạm" (system.js:49), nav `Chưa hỗ trợ` → "Việc chưa hỗ trợ" (i18n.js:241).
- incidents.js field labels are literal `contact_scope_hash` / `evidence_summary_hash` (498,506) — need Vietnamese labels with token as hint.

### 3.4 Navigation & mobile

- 11 bottom-nav items on phones with hidden scrollbar and no scroll affordance — `Hệ thống`, `Nhân sự`, `Chưa hỗ trợ` live past the edge undiscoverable (`app.js:37-73`, `layout.css:189-195`).
- `.field` grid keeps 34% label column on phones; `Gloss (TOKEN)` values wrap to 3+ lines (`components.css:178`).
- Denial reasons on nav live only in `title` tooltip — unreachable on touch (`app.js:137`).
- Zero screens use `<table>` despite full table CSS; dense data renders as stacked cards — defensible on phones, but orders board at 100 rows is heavy scroll with no sort control.

### 3.5 Structural duplication

List+filter+truncation+skeleton re-implemented ~6× with variations; `UUID` regex in 5 files; `dimensionBadge` duplicated verbatim (orders.js:99, orderDetail.js:69); `integer()` duplicated; `guardedInput`/`boundInput` near-twins; inconsistent `setResult()` usage. Files: exceptions.js 1058 lines, staff.js 864, orders.js 771.

### 3.6 Upstream-limited (frontend cannot fix alone)

UUID-paste workflows everywhere (no pickers), orders board shows no customer/time/money, approvals queue deliberately inert, orders beyond newest 100 unreachable. These are API gaps with named program items — see §7. The refactor must not paper over them; it should make the *interim* workflow as smooth as possible (copy buttons, carry-over hand-offs like staff.js already does).

## 4. Workstreams

### WS1 — Correctness quick wins (P0, one slice)

D1–D10, D13 from §3.1. Each is a small, independently reviewable edit. Add the missing `ENUM_GLOSS` entries by measuring actual server values (same method as `rbac.js` header), not by guessing. For D6: remove the dead branch and let the settlement refusal render the standard `errorNotice`; if a `NOT_SUPPORTED` taxonomy entry is genuinely needed it is an `errors.js` change with tests, not a screen patch. For D8: use `money()` and give the settlement form an explicit "Sửa lại" path that mints a fresh idempotency key via the existing `Submission` class, with copy explaining why a new key is required. D11 dead-code removal ships in WS4 to keep WS1 purely behavioral fixes.

**Acceptance:** contract tests pass; `sw.js` regenerated; no visual change except the corrected strings; new gloss entries verified against `apps/api` enum definitions.

### WS2 — Progressive disclosure for standing explanations (P1, the noise fix)

Introduce one new component in `src/ui/components.js`: `explain(summary, ...body)` rendering a native `<details>`/`<summary>` — no JS state, no storage (invariant 3), prints expanded (print.css: ensure `details` content prints), collapses animations under reduced-motion. Rules:

1. **Never** collapse: safety disclosures required at point of action (MANUAL_SEND_RECORDED ≠ delivered near the attest button; settlement finality; CONFIRMED_SENT danger meaning), capability refusals, truncation disclosures.
2. Collapse: context/limit education (approvals `limitsPanel`, orders create-form preamble, read-model notices, gaps prose). Default state: collapsed **after** the operator has the data on screen — i.e. data first, explanation below or beside; on approvals the queue renders above the fold.
3. Summary line is plain Vietnamese naming what the explanation covers ("Tại sao nút Duyệt đang tắt?", "Giới hạn của bảng đơn này") — never "More info".

Also in WS2:

- today.js tiles: scope sentence becomes a one-line hint under the count; full sentence moves into `explain`.
- Replace uppercase eyebrows with sentence-case section labels (keep the eyebrow style only where it marks a true group, max 3 words).
- Badge density: dimension badges on order cards switch to gloss-only at <48rem viewport, `Gloss (TOKEN)` retained ≥48rem and on detail screens (audit surfaces keep tokens always). Implement in `components.js` `badge()` via a `compact` option, not per-screen forks.

**Acceptance:** on a 360×740 viewport, the first actionable control of Hôm nay, Duyệt, and Đơn hàng is reachable within one scroll; every previously-visible disclosure is still present in DOM and reachable/printable; contract tests pass.

### WS3 — Navigation and daily-loop priority (P1)

- Bottom nav (phone): 5 primary slots by daily frequency — Hôm nay, Duyệt (badge), Đơn hàng, Bản nháp AI, Báo giá — plus a "Thêm" overflow sheet listing Sự cố, Trợ lý AI, Ngoại lệ, Hệ thống, Nhân sự, Việc chưa hỗ trợ with group labels. Overflow = one new component using the existing dialog CSS (currently dead — D11 resolves by *using* it). Sidebar (≥64rem) keeps all 11 in the 3 existing groups. Role-aware: primary slots never reorder; overflow only hides nothing (invariant 5 — denied entries still render disabled with reason inside the sheet).
- Nav denial reasons: overflow sheet and sidebar show the reason as visible small text under the entry, not only `title` (fixes the touch-toolTip gap).
- Add a scroll-fade affordance if the final design keeps any horizontal scrolling region.

**Acceptance:** all 11 destinations reachable within 2 taps on a 360px phone; denied entries still discoverable with reasons; `aria-current` and focus-on-route-change unchanged; no new storage.

### WS4 — Component consolidation (P2, pure refactor)

- Extract shared `listView({fetch, renderItem, filter, empty, truncated})` replacing the ~6 local reimplementations (quotes, orders, approvals, shadow, exceptions, incidents); keep per-screen filter wiring.
- Move `UUID` regex, `integer()`, `dimensionBadge` into `core/format.js`/`ui/components.js`; delete duplicates.
- Standardize all result lines on `setResult()`; standardize input helpers (`boundInput` wins, `guardedInput` folds in).
- Remove dead exports/CSS (D11) — but keep `dialog` CSS if WS3's overflow consumes it; decide `[data-theme]` blocks: either wire to `prefers-color-scheme` custom properties correctly or delete. Split exceptions.js manual-send half into `screens/manualSend.js` only if the split keeps imports acyclic and the route table in `screens/index.js` unchanged in shape.

**Acceptance:** zero behavioral change; contract tests + `test_staff_console_contract.py` green; file sizes: exceptions.js < 700 lines; no screen imports a helper from another screen.

### WS5 — Vietnamese polish pass (P2)

Apply §3.3 list; add `format.dateOnly()` (vi-VN, Asia/Ho_Chi_Minh); fix countdown (D9) to "còn 9 phút 30 giây" / "còn 45 giây", dropping seconds once ≥10 phút; incidents hash field labels become Vietnamese with the token as `aria-describedby` hint; quotes service-code field gains a hint listing owner's actual service codes from `PRICEBOOK_V1.md` as static hint text (no catalog API yet — §7 U2). Add a one-line note in `apps/web/README.md` documenting the dual-language rule addition: role names gloss via the existing role map.

**Acceptance:** no raw English in any user-facing string except deliberate verbatim tokens already governed by the dual-language rule; typo D1 verified fixed in the assistant intent badge.

### WS6 — Interim workflow smoothing for UUID-paste flows (P2)

Until §7 APIs land, reduce friction without pretending the gaps are closed:

- Copy-to-clipboard buttons next to every UUID/hash the console itself displays (staff creation already does this — generalize the pattern into a `copyable` component; `navigator.clipboard` with manual-select fallback, disclosed).
- Order-create and incident forms: keep pasted-UUID fields but add client-side format pre-validation with Vietnamese error text (orders.js:554 style message: make it fully Vietnamese — D-list item).
- Cross-link: order detail shows "Mở sự cố cho đơn này" that pre-fills the incident form's order UUID (in-memory hand-off only, same pattern as staff.js carry-over).

**Acceptance:** no new persistence; clipboard failure degrades to selectable text; contract tests pass.

## 5. Explicitly out of scope

- Customer-facing surfaces, public automation, channel send capability (gated; unchanged).
- Any metric/dashboard computation client-side (prohibited — dashboards are deterministic server-side).
- Theme switcher UI, framework migration, offline writes, pagination UX (blocked on P5 keyset cursors), approval decision UX (blocked on P7), any picker backed by a read API that does not exist yet.

## 6. Verification plan

Every slice runs, in order:

```text
uv run pytest apps/api/tests/test_staff_console_contract.py
uv run python scripts/generate_staff_console_manifest.py   # when assets change
uv run pytest
uv run ruff check . && uv run mypy apps packages
uv run python scripts/verify_contracts.py
```

Plus per-slice manual checks on a 360px-wide viewport: first actionable control within one scroll (WS2), all destinations ≤2 taps (WS3), print output of order detail still complete (WS2 details-print rule). Evidence recorded per `scripts/record_delivery_evidence.py` once slices are queued.

## 7. Upstream dependencies (not this program's work, tracked here for sequencing)

| ID | Need | Program item |
|---|---|---|
| U1 | `GET /orders/{id}` read-back + widened order fields (customer/time/money) so the board stops being 4 bare badges | P4 (read-back bundle) |
| U2 | Catalog/service-code read so quote builder gets a picker | P3 |
| U3 | Approval queue widening (`resource_version`, `snapshot_hash`, `rendered_hash`) to un-inert Duyệt/Từ chối | P7 |
| U4 | Keyset pagination so lists stop being LIMIT-200 ceilings | P5 |
| U5 | Incident state machine + fault enums into `canonical-enums-v1.json` (currently spec-only) so ENUM_GLOSS has an authoritative source | registry gap — raise as decision |
| U6 | Owner decision on intake readiness attestation authority (P1 blocker) | unregistered decision |

## 8. Queue slicing (proposal)

When approved, enter as separate work items in dependency order:

1. `CONSOLE-UX-001` — WS1 quick wins (no design risk).
2. `CONSOLE-UX-002` — WS2 progressive disclosure (behavioral; needs the print/details check).
3. `CONSOLE-UX-003` — WS3 navigation.
4. `CONSOLE-UX-004` — WS4 consolidation (pure refactor).
5. `CONSOLE-UX-005` — WS5 + WS6 polish and interim flows.

Each keeps the §2 invariants as its check list; none touches `apps/api`, `packages/`, or `specs/contracts/`.
