# 01 — System cartography

## 1. Backend surface

44 routes, enumerated from the live `app.routes` at runtime (not from decorators — six decorators
span multiple lines and a grep misses them).

All `/internal/v1/*` routes require a staff session cookie; most additionally require
`mfa_verified` and store membership. Enforcement is at the service/repository layer, re-checked
per call — `core/rbac.js:20` states this as an invariant and §2 of `02-BACKEND.md` verifies it.

| Method | Path | Handler | Called by the console? |
|---|---|---|---|
| GET | `/healthz` | `healthz` | infra only |
| POST | `/internal/v1/auth/session` | `exchange_identity_token` | IdP page (production: Keycloak) |
| GET | `/internal/v1/session` | `get_session` | `core/session.js:85` |
| POST | `/internal/v1/auth/logout` | `logout` | `core/session.js:196` |
| GET | `/internal/v1/stores` | `list_member_stores` | `core/session.js:128` |
| POST | `/internal/v1/staff` | `create_staff` | `staff.js:141` |
| POST | `/internal/v1/staff/{id}/roles` | `assign_staff_role` | `staff.js:333` |
| POST | `/internal/v1/staff/{id}/disable` | `disable_staff` | `staff.js:517` |
| POST | `/internal/v1/staff/{id}/stores/{store}` | `assign_staff_store` | `staff.js:688` |
| DELETE | `/internal/v1/staff/{id}/stores/{store}` | `revoke_staff_store` | `staff.js:688` |
| **POST** | **`/internal/v1/sessions/{session_id}/revoke`** | `revoke_session` | **NOBODY — orphan** |
| GET | `/internal/v1/stores/{s}/orders` | `list_orders` | `orders.js:334`, `orderDetail.js:289`, `today.js:124` |
| POST | `/internal/v1/stores/{s}/orders` | `create_order` | `orders.js:416` |
| POST | `/internal/v1/orders/{id}/transition` | `transition_order` | `orders.js:150` |
| POST | `/internal/v1/orders/{id}/intake-transition` | `transition_order_intake` | `orders.js:158` |
| POST | `/internal/v1/orders/{id}/production-transition` | `transition_order_production` | `orders.js:166` |
| POST | `/internal/v1/orders/{id}/settlement` | `record_settlement` | `orderDetail.js:168` |
| POST | `/internal/v1/orders/{id}/delivery-legs` | `record_delivery_leg` | `orders.js:931` |
| GET | `/internal/v1/stores/{s}/order-requests` | `list_order_requests` | `orderRequests.js:232`, `quotes.js:891` |
| POST | `/internal/v1/stores/{s}/order-requests` | `create_order_request` | `orderRequests.js:261` |
| GET | `/internal/v1/stores/{s}/order-requests/{id}` | `get_order_request` | `quotes.js:933` |
| POST | `/internal/v1/stores/{s}/counter-tickets` | `issue_counter_ticket` | `orderRequests.js:326` |
| GET | `/internal/v1/pricebook/services` | `list_pricebook_services` | `quotes.js:747` |
| GET | `/internal/v1/stores/{s}/quotes` | `list_quotes` | `quotes.js:771` |
| POST | `/internal/v1/stores/{s}/quotes` | `create_quote` | `quotes.js:1031` |
| POST | `/internal/v1/stores/{s}/quotes/{id}/acceptance` | `accept_quote` | `quotes.js:357` |
| GET | `/internal/v1/stores/{s}/settlements/today` | `collected_today` | `today.js:241` |
| GET | `/internal/v1/stores/{s}/incidents` | `list_incidents` | `incidents.js:337`, `today.js:111` |
| POST | `/internal/v1/stores/{s}/incidents` | `open_incident` | `incidents.js:425` |
| GET | `/internal/v1/approvals` | `list_pending_approvals` | `approvals.js:313`, `today.js:74`, `app.js:106` |
| **POST** | **`/internal/v1/approvals`** | `request_approval` | **NOBODY — orphan** |
| POST | `/internal/v1/approvals/{id}/decisions` | `decide_approval` | `approvals.js` — **control exists but is permanently disabled** (see F-01) |
| POST | `/internal/v1/approvals/{id}/manual-send` | `prepare_manual_send` | `manualSend.js:248` |
| POST | `/internal/v1/manual-sends/{id}/attest` | `attest_manual_send` | `manualSend.js:310` |
| GET | `/internal/v1/queue-recovery` | `queue_recovery` | `system.js:255` |
| GET | `/internal/v1/stores/{s}/shadow/drafts` | `list_shadow_drafts` | `shadow.js:657`, `today.js:86` |
| POST | `/internal/v1/shadow/drafts/{run}/decision` | `decide_shadow_draft` | `shadow.js:437` |
| GET | `/internal/v1/stores/{s}/shadow/reviews` | `list_shadow_reviews` | `shadow.js:764,787` |
| GET | `/internal/v1/stores/{s}/shadow/audit/{agg}` | `shadow_audit_timeline` | `orderDetail.js:367` |
| GET | `/internal/v1/shadow/unknown-sends` | `list_unknown_sends` | `exceptions.js:268`, `today.js:99` |
| POST | `/internal/v1/shadow/unknown-sends/{id}/reconcile` | `reconcile_unknown_send` | `exceptions.js:124` |
| GET | `/internal/v1/stores/{s}/assistant/turns` | `list_assistant_turns` | `assistant.js:637,676` |
| POST | `/internal/v1/stores/{s}/assistant/turns` | `post_assistant_turn` | `assistant.js:489` |
| GET | `/internal/v1/stores/{s}/assistant/turns/{t}/stream` | `stream_assistant_turn` | `assistant.js:285` |

## 2. Frontend surface

13 screens in `apps/web/src/screens/index.js`; 12 are directly navigable, `orderDetail`
(`/orders/:orderId`) is reached from a card. The PWA is vanilla ES modules with no build step and
no framework — mounted by the API itself at `main.py:2415`.

| Path | Title | Capability | Store-scoped | Writes |
|---|---|---|---|---|
| `/` | Hôm nay | — | yes | none (aggregates 5 read routes) |
| `/order-requests` | Tiếp nhận | `ORDER_REQUESTS_*` | yes | counter-ticket, order-request |
| `/quotes` | Báo giá | `QUOTES_*` | yes | quote, acceptance |
| `/orders` | Đơn hàng | `ORDERS_*` | yes | create, 3 transitions, delivery leg |
| `/orders/:id` | Chi tiết đơn | `ORDERS_READ` | yes | settlement |
| `/approvals` | Duyệt | `APPROVALS_READ` | no | **none — see F-01** |
| `/assistant` | Trợ lý AI | `ASSISTANT` | yes | assistant turn |
| `/shadow` | Bản nháp AI | `SHADOW_READ` | yes | draft decision |
| `/exceptions` | Ngoại lệ | `SHADOW_READ` | no | manual-send prepare + attest, reconcile |
| `/incidents` | Sự cố | `INCIDENTS_READ` | yes | open incident |
| `/system` | Hệ thống | `QUEUE_READ` | no | none |
| `/staff` | Nhân sự | owner-only | no | create, role, store grant/revoke, disable |
| `/gaps` | Chưa hỗ trợ | — | no | none — static catalogue, no fetch |

## 3. Agent surface

**There is no live agent.** All 13 capabilities in `delivery/CAPABILITY_STATUS.yaml` are
`NOT_AUTHORIZED`; no provider is configured and none was called during this audit. The Tool Facade
(`apps/public-agent-tools`) and the bounded Responses runtime (`apps/worker`) exist and are tested,
but nothing in the console reaches them.

The screen named **"Trợ lý AI"** does not call a model — it is a deterministic intent classifier
over the store's own rows, and says so on screen. See `05-AI-HUMAN-BOUNDARY.md` §3.

## 4. Orphans

### 4.1 Endpoints no frontend calls (2)

| Route | Why it is unreachable | Evidence |
|---|---|---|
| `POST /internal/v1/sessions/{session_id}/revoke` | `SessionResponse` (`main.py:208`) carries no `session_id`, so the console never learns an id to revoke | `gaps.js:322-333` states this; `main.py:208-213` confirms the model |
| `POST /internal/v1/approvals` | Requires a `resource_type` that must exactly match a server-side mapping (`packages/domain/.../approvals.py:114-131`) plus two JCS hashes and a `policy_version`, none of which the UI can produce | `gaps.js:311-321`; verified — a request with `resource_type: CUSTOMER_MESSAGE` was refused `422 VALIDATION_ERROR: invalid approval binding`, and only `MESSAGE_DRAFT` was accepted (`201`) |

### 4.2 Dead UI elements

**None found.** Every button, form and control on all 12 screens issues a request or performs a
local state change. Two controls are *permanently disabled by design* rather than dead — see F-01
(Duyệt/Từ chối) — which is a worse condition than dead, not a better one.

One form is **unfillable rather than dead**: the `Tạo đơn` form on `/orders` requires a value the
console never displays. See `03-TRACE.md` D-01.

### 4.3 Agent tools unreachable from any flow

All of them, by design and by gate. Not a defect.

## 5. Duplicates

Actively looked for; **the codebase is clean here**:

- One money formatter: `core/format.js:72 money()`. No `toLocaleString` on money outside it
  (the three `₫` hits elsewhere are field *labels*, not formatting).
- One date/time formatter set: three `Intl.DateTimeFormat` instances, all in `core/format.js:39-55`,
  all pinned `vi-VN` / `Asia/Ho_Chi_Minh`.
- One API client: `core/api.js` is the only module issuing `fetch` to `/internal/v1`.
- One enum gloss table: `core/i18n.js`.
- One idempotency/`If-Match` policy, enforced centrally in `core/api.js:96-120`.

The only duplication worth naming is **four independent copies of the same list-filter predicate**
(`orders.js:347`, `incidents.js:346`, `quotes.js:781`, `orderRequests.js:196`), each
`String(value).toLowerCase().includes(needle)`. They are identical, and all four carry the same
defect — no Vietnamese diacritic folding. One shared `matchesFilter()` in `core/format.js` fixes
four screens at once. See `06-VI-COPY.md` G-02.
