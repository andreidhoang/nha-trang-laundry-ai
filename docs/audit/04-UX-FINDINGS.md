# 04 — Operating the app as real Vietnamese users

Chrome via Playwright, `vi-VN`, `Asia/Ho_Chi_Minh`, viewports 1366×768 (SME office laptop) and
390×844 (phone). Real clicks, real typing, real IME composition.

## 1. Measured: the counter journey

The one journey that matters — a customer walks in, staff takes the bag, prices it, opens an order.

| Step | Clicks | Wall clock | Verdict |
|---|---|---|---|
| Tiếp nhận: Phát phiếu → Ghi nhận tiếp nhận | 2 | 5s | **excellent** |
| Báo giá: Dùng yêu cầu này → chọn dịch vụ → khối lượng → Tính giá | 4 | 6s | **excellent** |
| Khách đã chốt giá | 1 | 2s | **excellent** |
| **Tạo đơn** | — | **∞** | **impossible — D-01** |

Seven clicks and 13 seconds to a priced, customer-accepted quote. That is genuinely good — better
than most systems of this kind — and then the journey stops dead, because the order cannot be
created from the console. Nothing downstream (`/orders`, `/orders/:id`, settlement, delivery legs,
`Hôm nay`'s money tile) can be reached by a real operator on a real shift.

**The whole product is one missing field away from working.**

## 2. Findings

### P0

**U-01 — the counter journey cannot be completed.** See `03-TRACE.md` D-01. Screenshot:
`shots/j9-01-create-filled.png`.
*Fix:* a `Tạo đơn từ báo giá này` button on the accepted-quote card that carries all four values
forward — the exact pattern `Dùng yêu cầu này` already implements one screen earlier.

**U-02 — a customer complaint cannot be recorded.** See `03-TRACE.md` D-04. The app tells the
operator to use a paper notebook. Screenshot: `shots/dead-incidents.png`.

**U-03 — a pending approval expires with no one able to action it.** See `05-AI-HUMAN-BOUNDARY.md`
F-01. Screenshot: `shots/approvals-with-item.png`.

### P1

**U-04 — horizontal scroll on a phone, on 3 of 12 screens.** VERIFIED at 390×844:

| Screen | Overflow | Culprit |
|---|---|---|
| `/quotes` | **92px** | `button "Thêm bản sửa đổi cho báo giá này"` (360px wide), `dd.mono "JCS-SHA256-V1:2c6bc4ed…adf3 Sao chép"` |
| `/approvals` | **86px** | `div.field.field--span` at 381px |
| `/exceptions` | **82px** | `button "Chứng thực rằng tôi đã gửi tin này"` (362px) |

This is the §9 string-expansion failure exactly: Vietnamese button labels run 15–30% longer than
their English equivalents and the card grid has no wrap rule for them. `apps/web/README.md` names
phones as the primary device. A shop owner checking the day on a phone gets a page that slides
sideways.

**U-05 — `Hôm nay` cannot answer "what do I do next?" for a CSKH agent.** The day screen is
correct and empty-state-honest, but every queue it aggregates is one a CSKH agent cannot act on
(approvals — F-01; unknown sends — D-05; incidents — D-04). For the *owner* it is a good morning
screen. For the agent it is a read-only status board with no next action.

### P2

**U-06 — the auditor can type into forms that can never submit.** `03-TRACE.md` D-03.

**U-07 — English validation errors reach the user.** `02-BACKEND.md` B-05.

**U-08 — tap targets below 40px on 5 screens.** Disclosure toggles at 15–19px tall
(`/` 239×15, `/orders` 209×15, `/approvals` 255×19, `/system` 294×19). These are the
`<summary>`-style "Vì sao…?" expanders, which is exactly what a confused operator reaches for.

**U-09 — the `Tạo đơn` form's own hint is false.** `orders.js` tells the operator *"Chép từ màn
hình Tiếp nhận"* for `Mã khách`. There is nothing to copy there. A hint that sends a staffer to a
screen that cannot help is worse than no hint: it costs a round trip and teaches distrust.

### P3

**U-10 — 6 order requests accumulate in `Chọn từ tiếp nhận gần đây` with no way to dismiss one.**
Each test intake added a row; a real shift adds one per customer. There is no "done with this"
action, so by midday the picker is a scroll. Low severity today, linear growth.

## 3. What is genuinely good — and should not be refactored away

Stated because a subtraction pass that removes these would make the product worse:

- **Money and dates are correct everywhere.** `120.000 ₫`, `14:16 18/09/2026`, one formatter, one
  timezone, no `toLocaleString` on money outside `core/format.js:72`.
- **The 6kg pricing cliff is disclosed at the point of entry**, in plain Vietnamese, as a confirmed
  owner rule and not a bug — including the counter-intuitive consequence that 5,9kg can cost more
  than 6,0kg, and an explicit instruction not to split bags to game it.
- **Every price carries `ƯỚC TÍNH · chưa phải giá cuối`** until it is actually final.
- **Reason codes are glossed, not dumped**: `PROMOTION_NOT_EVALUATED — Chưa xét khuyến mãi cho bản
  báo giá này.`
- **Error microcopy names the next action.** The stale-write message tells the operator someone
  else changed the order, that their command did not apply, and to reload — plus a `Tải lại bảng`
  button. The order-create timeout message says *"Đừng bấm lại"* and explains why.
- **Offline is read-only and says so**; writes are never queued (`core/api.js:20-30`).
- **Zero console errors** across all 12 screens, both viewports, all four roles.

## 4. Adversarial checks

| Case | Result |
|---|---|
| Missing CSRF token | `403 CSRF validation failed` |
| Missing idempotency key | `422`, named header |
| Double-click submit | replayed, one row — VERIFIED |
| Two operators, one order | second gets `409 STALE_VERSION`, no data loss |
| Forged store id | `403`, indistinguishable from nonexistent |
| Auditor attempts writes | buttons disabled client-side, server refuses independently |
| Page refresh mid-flow | state is per-screen and rebuilt from the server; no half-written rows |
| Vietnamese IME (Telex) composition | **PASS — see `06-VI-COPY.md` G-01** |
