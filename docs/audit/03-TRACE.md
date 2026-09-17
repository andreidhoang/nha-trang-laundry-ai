# 03 — Contract linkage: UI ↔ API ↔ DB

Every interactive element on all 12 navigable screens was enumerated **at runtime** in a signed-in
browser (`inventory-demo-owner.json`, `inventory-demo-auditor.json`), not read from source. Columns
`Actor level` per `05-AI-HUMAN-BOUNDARY.md`.

## 1. Summary

| | Count |
|---|---|
| Elements inventoried at runtime (owner, 12 screens) | 344 |
| — of which app-shell nav links repeated on every screen | 240 |
| **Distinct screen-level interactive elements** | **104** |
| Elements that issue no request and change no state (**dead**) | **0** |
| Controls permanently disabled by design, with no path to enabled | **2** (F-01) |
| Forms that cannot be completed by any human from console data alone (**unfillable**) | **3** (D-01, D-04, D-05) |
| Labels that promise something the handler does not do | 1 (F-02, "Trợ lý AI") |
| Destructive actions with no confirm | 0 — `disable_staff` is two-step (`staff.js:436`) |
| Two buttons hitting the same endpoint with the same payload | 0 |

**There are no dead buttons in this application.** That is worth saying plainly, because it was the
first thing you asked and the answer is unusual for a codebase this size. What there is instead is
worse: three forms that *look* live, validate your input, and can never be submitted.

## 2. The three unfillable forms

### D-01 (P0) — `Tạo đơn` on `/orders` demands a value the console never shows

Status: **VERIFIED.**

| Field | Vietnamese label | Where the operator is told to get it | Is it obtainable? |
|---|---|---|---|
| `contact_binding_id` | Mã khách | screen says *"Chép từ màn hình Tiếp nhận"* | **NO** |
| `quote_id` | Mã báo giá | quote card, has `Sao chép` | yes |
| `revision` | Bản báo giá | quote card, read and retype | yes |
| `quote_snapshot_hash` | Mã niêm phong báo giá | quote card, has `Sao chép` | yes |

The instruction on the screen cannot be followed. `/order-requests` renders the contact id only
through `shortId()` — `orderRequests.js:91` and `:145` — i.e. `08d5ba9f…4a45`, with **no `title`
attribute and no `copyable()`**. A DOM probe across the intake screen in three states confirms it:

```
intake, before ticket     full UUIDs in visible text: []   in title attrs: [7 × order_request_id]   copy buttons: 0
intake, after "Phát phiếu" full UUIDs in input values: ['12717567-a0c8-4977-9fcc-4fec1c984a3d']     copy buttons: 0
intake, after submit       input cleared (orderRequests.js:270)                                     copy buttons: 0
```

The full contact UUID exists in the DOM for exactly one window — in the input, after `Phát phiếu`
and before `Ghi nhận tiếp nhận` — and `orderRequests.js:270` clears it on success. For any order
request created earlier in the shift, the value is unreachable.

Proof the blocker is the form and not the backend: supplying the four values out-of-band (read from
the API, which the operator cannot do) and submitting the same form returned
`201 POST /internal/v1/stores/…/orders` → order `ccc9deb4…fb18`. The route works. The screen is
what fails.

`copyable()`'s own docstring (`components.js:909-913`) names this design: *"until the upstream read
APIs land, identifiers and hashes travel between screens by paste"*. The paste chain is missing one
link, and it is the first one.

### D-04 (P0) — `Ghi sự cố` on `/incidents` cannot be submitted by anyone

Status: **VERIFIED.** The screen carries its own banner: **"Biểu mẫu này chưa dùng được — không
phải do bạn"**, and explains that `contact_scope_hash` and `evidence_summary_hash` are produced by
the agent path, which is not authorized. Pressing `Ghi sự cố` with the only obtainable value (an
order id) returns the client-side refusal *"Chưa nhập mã băm phạm vi liên hệ (contact_scope_hash)"*
and issues **no request**.

Consequence for a real shift: a customer complains at the counter and there is no way to record it
in the system. `gaps.js:154` gives the interim procedure — *"ghi khiếu nại ra sổ kèm số phiếu, và
báo chủ tiệm trong ngày"* — a paper notebook. Compounded by `gaps.js:161`: no remedy path exists,
so every incident that *could* be opened would be stuck at `OPEN` forever.

Blocked on an owner decision: `docs/DECISION_REQUEST_INCIDENT_INTAKE_2026-09.md`. **Not an
engineering fix** — §14 escalation.

### D-05 (P0) — `Khoá phong bì cho người gửi tay` on `/exceptions` — same root cause as F-01

Status: **VERIFIED.** Form 0 on `/exceptions` requires `Phiên bản tài nguyên đã quan sát`,
`Mã băm ảnh chụp đã quan sát`, `Mã băm nội dung đã quan sát` — i.e.
`observed_resource_version`, `observed_snapshot_hash`, `observed_rendered_hash`
(`main.py:291-297`). These are **the same three fields** that `ApprovalResponse` fails to project.

This materially enlarges F-01's blast radius. One missing projection on one read model blocks:

1. the approval decision (`Duyệt` / `Từ chối`), **and**
2. the manual-send envelope that is the only sanctioned way a human-approved message reaches a
   customer.

Together that is the entire human-approved customer-communication path. Fixing the three fields on
`ApprovalResponse` unblocks both surfaces at once.

## 3. Per-screen trace

Abbreviated; `→` is the handler reached. All rows VERIFIED at runtime unless marked.

| Screen | Element (VN label) | API | Handler | Side effect | Reversible | Level | Status |
|---|---|---|---|---|---|---|---|
| `/` | Tải lại | 5 × GET | read models | none | n/a | H0 | OK |
| `/order-requests` | Phát phiếu (khách vãng lai) | `POST …/counter-tickets` | `issue_counter_ticket` | mints ticket no. | no | H0 | OK |
| | Ghi nhận tiếp nhận | `POST …/order-requests` | `create_order_request` | draft request row | no | H0 | OK |
| | Lọc danh sách tiếp nhận | — | client filter | none | yes | H0 | OK, no diacritic fold (G-02) |
| `/quotes` | Dùng yêu cầu này | `GET …/order-requests/{id}` | `get_order_request` | none | yes | H0 | OK — **the picker pattern that `/orders` lacks** |
| | Tính giá | `POST …/quotes` | `create_quote` | quote revision | no | H0 | OK |
| | Khách đã chốt giá | `POST …/quotes/{id}/acceptance` | `accept_quote` | finality `APPROVED_EXACT` | no | H0 | OK |
| | Sao chép ×2 | — | clipboard | none | yes | H0 | OK |
| `/orders` | Chọn để chuyển trạng thái | — | prefills move form | none | yes | H0 | OK — correct pattern |
| | Chuyển trạng thái | `POST …/transition` ×3 | 3 handlers | state + `row_version` | no | H0 | OK, `If-Match` enforced |
| | Ghi nhận (chặng giao) | `POST …/delivery-legs` | `record_delivery_leg` | leg row | no | H0 | OK |
| | **Tạo đơn** | `POST …/orders` | `create_order` | order | no | H0 | **D-01 unfillable** |
| `/orders/:id` | Tất toán | `POST …/settlement` | `record_settlement` | money recorded | no | H0 | OK |
| `/approvals` | **Duyệt / Từ chối** | `POST …/decisions` | `decide_approval` | approval decision | no | **A2** | **F-01 permanently disabled** |
| `/assistant` | Gửi + 4 chips | `POST …/assistant/turns` | `post_assistant_turn` | logged turn | no | H0 | OK, **mislabelled (F-02)** |
| `/shadow` | (draft decision) | `POST /shadow/drafts/{run}/decision` | `decide_shadow_draft` | draft verdict | no | A2 | BLOCKED — no drafts to exercise |
| `/exceptions` | **Khoá phong bì…** | `POST …/manual-send` | `prepare_manual_send` | envelope | no | A2 | **D-05 unfillable** |
| | Chứng thực rằng tôi đã gửi tin này | `POST /manual-sends/{id}/attest` | `attest_manual_send` | attestation | no | A2 | reachable only if D-05 is |
| `/incidents` | **Ghi sự cố** | `POST …/incidents` | `open_incident` | incident | no | H0 | **D-04 unfillable** |
| `/system` | Tải lại | `GET /queue-recovery` | `queue_recovery` | none | n/a | H0 | OK |
| `/staff` | Tạo nhân sự | `POST /staff` | `create_staff` | staff row | no | H0 | OK |
| | Gán vai trò | `POST /staff/{id}/roles` | `assign_staff_role` | role | yes | H0 | OK, needs pasted UUID (D-02) |
| | Gán cửa hàng / Thu hồi | `POST`/`DELETE …/stores/{s}` | assign/revoke | membership | yes | H0 | OK, needs 2 pasted UUIDs (D-02) |
| | Vô hiệu hoá nhân sự | `POST /staff/{id}/disable` | `disable_staff` | disables + revokes sessions | no | H0 | OK — two-step |
| `/gaps` | — | none | — | none | n/a | H0 | OK — static by design |

## 4. D-02 (P2) — the paste tax on `/staff`

`Gán vai trò`, `Gán cửa hàng` and `Vô hiệu hoá nhân sự` each require a staff UUID. Unlike D-01 this
one *is* satisfiable: `staff.js:274` renders `copyable({ value: staffUserId })` after creation, and
the screen carries the value forward between its own forms. But there is no staff **list** route, so
for anyone created on a previous day the UUID has no source inside the console. Same class as D-01,
lower severity because the owner does this rarely.

## 5. D-03 (P2) — auditor can fill forms they can never submit

On `/orders` and `/exceptions` the auditor sees submit buttons correctly `disabled` with a reason,
but **every input in those forms remains enabled**. An auditor can type a UUID, a version and a
64-character hash into a form that will never submit. `core/rbac.js:20` states the rule as
*"Controls are disabled and explained, never hidden"*; the rule was applied to buttons and not to
the fields above them.
