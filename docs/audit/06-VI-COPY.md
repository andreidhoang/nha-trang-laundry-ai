# 06 — Vietnamese language & meaning audit

## 1. G-01 — IME composition: **VERIFIED PASS**

This is the check the directive singles out as invisible to `fill()`, so it was run with real CDP
composition events (`Input.imeSetComposition` per keystroke, then `Input.insertText` to commit) —
the same event sequence a Telex IME produces.

| Surface | Target | Result |
|---|---|---|
| `staff.js` name input | `Nguyễn Thị Hồng Nhung` | **PASS** — exact, NFC-equal, length 21 |
| `assistant.js` textarea | `Đơn hàng đã giao` | **PASS** — exact |
| `quotes.js` quantity (re-renders the 6kg cliff panel on every `input`) | `5,9` | **PASS** |

Captured event sequence on the name field:
`compositionstart → (compositionupdate, input) ×9 → compositionend`, final value intact.

**Why it passes, and why that is not luck.** There is no framework and no controlled-input
round-trip: `core/dom.js` writes to real DOM nodes and screens re-render *sibling* panels rather
than replacing the focused input. The class of bug the directive warns about — a controlled React
input that re-renders mid-composition and eats the diacritic — cannot occur here. The decision in
`apps/web/README.md:22-53` to ship no framework buys this for free.

## 2. G-02 (P1) — diacritic-insensitive search is absent, in four places

Status: **VERIFIED** (code path), impact **bounded today, high on first free-text field**.

All four list filters are the identical predicate with no Unicode folding:

| File | Line |
|---|---|
| `orders.js` | 347 |
| `incidents.js` | 346 |
| `quotes.js` | 781 |
| `orderRequests.js` | 196 |

```js
String(value).toLowerCase().includes(needle)
```

Searching `nguyen` will not find `Nguyễn`; searching `don` will not find `đơn`. Today the
filterable values are mostly UUIDs and English enum tokens, so the user-visible impact is small —
but `incidents.js` already filters status text, and the moment a customer name or address becomes
filterable (the `parties` aggregate `gaps.js:75` describes) this breaks silently.

*Fix:* one shared helper in `core/format.js`:
`s.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g,"d").replace(/Đ/g,"D").toLowerCase()`
applied to both needle and haystack. Note `đ`/`Đ` need explicit handling — they are not
decomposable and NFD alone will miss them. Four call sites collapse to one.

## 3. G-03 (P1) — string expansion breaks the phone layout

Three screens scroll horizontally at 390px because Vietnamese labels exceed the card width. Full
table in `04-UX-FINDINGS.md` U-04. Worst offenders are two buttons:

- `Thêm bản sửa đổi cho báo giá này` — 360px in a 390px viewport
- `Chứng thực rằng tôi đã gửi tin này` — 362px

Both are correct, natural Vietnamese; the layout is what is wrong. *Fix in CSS, not in copy* —
`overflow-wrap: anywhere` on `.copyable__text` and a wrapping `.form__actions`. Do not shorten the
labels: the long forms carry meaning the short forms lose (`Chứng thực rằng tôi đã gửi tin này`
is an attestation in the first person on purpose).

## 4. G-04 (P1) — "Trợ lý AI" names something that is not AI

Full analysis in `05-AI-HUMAN-BOUNDARY.md` F-02. This is a §9.4 meaning-correctness defect: the
label implies an action the code does not perform. Proposed `Hỏi nhanh`; **owner sign-off required**
(§14), since it is a product-naming decision.

## 5. G-05 (P2) — stale copy describing a control that is not rendered

`/approvals` shows a disclosure titled **"Tại sao nút Duyệt đang tắt?"** even when the queue is
empty and **no Duyệt button exists on the page at all** (VERIFIED: with 0 items, the only button is
`Tải lại`). The sentence is correct when an item is present and dangling when it is not.

## 6. Terminology — consistent, which is rare

Checked the drift pairs the directive names. The glossary holds:

| Concept | Term used | Drift found |
|---|---|---|
| order | `đơn` / `đơn hàng` | consistent; `order` never surfaces |
| quote | `báo giá`, revision = `bản sửa đổi` | consistent |
| intake | `tiếp nhận` | consistent |
| settlement | `tất toán` | consistent |
| incident | `sự cố` | consistent |
| approval | `duyệt` / `phê duyệt` | consistent |
| customer ref | `mã khách` / `số phiếu` | consistent, and the distinction is real |
| enum tokens | glossed via `core/i18n.js` as `Gloss (TOKEN)` | one path leaks raw — G-06 |

Untranslated English UI terms (`Draft`, `Pending`, `Sync`, `Dashboard`, `Workflow`): **none found**
in user-visible copy. `Nháp`, `Đã yêu cầu`, `Chờ cửa hàng xác nhận`, `Đang chạy` are used instead.

## 7. G-06 (P2) — raw English enums reach the user on the validation path

`422` bodies render verbatim, e.g.
`Input should be 'AWAITING_HANDOFF', 'RECEIVED_PENDING_INSPECTION', 'WAITING_PRICE_APPROVAL', …`
(`02-BACKEND.md` B-05). Everywhere the client pre-validates, the Vietnamese is good; this is the
path where it does not.

## 8. Formats — **VERIFIED PASS**

| Requirement | Observed | Source |
|---|---|---|
| `dd/MM/yyyy` | `18/09/2026` | `format.js:39-55` |
| 24-hour time | `14:16` | same |
| `Asia/Ho_Chi_Minh` | pinned in all three `Intl.DateTimeFormat` instances | `format.js:39-55` |
| Thousands `.` | `120.000 ₫` | `format.js:72` |
| Currency `1.000.000 ₫` | correct, symbol trailing with a space | `format.js:72` |
| `null` money never renders as `0` | enforced | `format.js:72` + contract test |
| Decimal `,` in quantity | `5,9` accepted and echoed verbatim; the screen states it does not round or reformat | verified |

**Address model:** the app stores no addresses at all (`gaps.js:75` — no `parties`,
`contact_points` or `addresses` aggregate, DEC-015 resolved as a deliberate deferral). So the
two-tier vs three-tier administrative question **does not arise yet**. It becomes live the day
delivery addresses land; flagged for whoever builds that so the legacy district tier is not
reintroduced. Status: **N/A today**, not "pass".

**Phone normalization (`+84` ↔ `0`):** not applicable — no phone number is stored anywhere, by
design.

## 9. Error microcopy — good, with the one gap above

Sampled every error path reachable at runtime. The standard the directive asks for — *what
happened, whether your work was lost, what to do next* — is met, in natural Vietnamese, with the
next action named. Examples verified on screen:

- stale write: *"Đơn này vừa được người khác đổi trong lúc bạn đang xem, nên lệnh của bạn bị từ
  chối và trạng thái đơn không đổi. Tải lại bảng đơn rồi làm lại theo số mới."* + a `Tải lại bảng`
  button
- create timeout: *"Chưa biết lệnh có tới máy chủ hay không… Đừng bấm lại — hãy tải lại bảng đơn và
  tìm mã khách này trước."*
- expired quote: *"Báo giá này đã quá hạn… Hãy cân lại và bấm 'Tính giá'… Không có gì được ghi."*
- unfillable incident form: *"Biểu mẫu này chưa dùng được — không phải do bạn"*

No `Something went wrong`, no bare error code as primary text, no stack trace. One correlation id
is surfaced per failure so an operator can quote one identifier that appears in the server log.
