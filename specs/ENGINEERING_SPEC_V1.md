# Engineering Specification v1 — Giặt Là Sạch Cộng AI Operations System

**Document owner:** Product/Engineering  
**Business owner:** Giặt Là Sạch Cộng  
**Legal entity:** CÔNG TY TNHH A & T CARE — MST 4202059758  
**Version:** 1.1-runtime-and-delivery-alignment  
**Date:** 2026-07-27  
**Status:** `READY_FOR_IMPLEMENTATION_WITH_GATES`

## 1. Executive decision

Xây một **modular monolith** cho nghiệp vụ báo giá, đơn hàng, SLA, giao nhận, approval và audit; dùng OpenClaw như lớp agent/channel orchestration bị giới hạn quyền.

Không xây một “AI tự vận hành tiệm”. V1 là một hệ thống:

1. code xác định tính giá và kiểm tra policy;
2. nhân viên ra quyết định thương mại;
3. AI trích xuất yêu cầu, soạn nháp, giải thích và chuyển giao;
4. mọi hành động đều truy vết được;
5. hệ thống vẫn vận hành thủ công khi model hoặc OpenClaw lỗi.

## 2. Problem statement

Hiện dự án có pricebook, promotion, SLA, delivery rule và nhiều CSV template nhưng chưa có:

- source code;
- database canonical;
- order line items;
- immutable quote snapshot;
- state machine;
- approval/audit history;
- API contract;
- retry/idempotency;
- role-based access;
- test/eval harness;
- runtime isolation cho public agent.

CSV hiện tại phù hợp làm seed hoặc export, không phù hợp làm transactional source of truth.

## 3. Outcomes

### 3.1 Shadow Mode outcome

Trong một màn hình dùng được trên điện thoại, nhân viên phải:

1. nhập yêu cầu khách;
2. chọn dịch vụ và số lượng/khối lượng ước tính;
3. xem giá niêm yết, ưu đãi, phí giao nhận và cảnh báo;
4. chỉnh các trường `HUMAN_INPUT_REQUIRED`;
5. duyệt nội dung phản hồi do AI soạn;
6. tạo đơn;
7. cập nhật các mốc nhận đồ, bắt đầu xử lý, sẵn sàng, giao và thanh toán;
8. xem log SLA, cost và contribution.

### 3.2 Business validation outcome

Sau 7–14 ngày pilot:

- tối thiểu 30 đơn thật;
- tối thiểu 10 cycle/batch logs;
- tối thiểu 20 delivery-cost logs;
- 100% quote có snapshot và approver;
- 100% thay đổi trạng thái có audit event;
- 0 báo giá sai do LLM tự tính;
- đo được correction rate, on-time rate và contribution estimate completeness.

### 3.3 Long-term outcome

Chỉ sau khi vượt stage gates, hệ thống có thể:

- tự trả lời FAQ đã phê duyệt;
- tự tạo quote estimate cho dịch vụ giá cố định;
- tự gửi một số message rủi ro thấp;
- không bao giờ tự quyết refund, bồi thường, công nợ, giá ngoại lệ hoặc capacity vượt guardrail.

## 4. Scope

### 4.1 Must-have — Release R1 Shadow MVP

- Staff authentication và RBAC cơ bản.
- Customer/contact/account.
- Service catalog, pricebook, promotion và calendar có version.
- Quote nhiều line items.
- Pricing engine deterministic.
- Delivery rule deterministic đến 6km; manual fee trên 6km.
- SLA proposal và staff-confirmed promise.
- Human approval queue.
- Order lifecycle và event timeline.
- Payment status; invoice request tracking ở chế độ manual.
- Incident intake và human escalation.
- Agent draft generation; không tự gửi.
- Audit log, inbox/outbox, idempotency.
- CSV import/export và pilot dashboards.
- Feature flags và kill switches.

### 4.2 Should-have — Release R2 Assisted

- Official channel connector.
- Approved low-risk auto replies.
- Route grouping hỗ trợ quyết định, không tự dispatch.
- Capacity board dựa trên measured cycle data.
- Reminder/follow-up sau opt-in.
- Invoice workflow integration sau khi business/tax rules được xác minh.

### 4.3 Out of scope — V1

- Fully autonomous customer agent.
- Tự xác nhận slot dựa trên tải danh nghĩa của máy.
- Dynamic pricing do model quyết định.
- AI vision tự phân loại vải/vết bẩn để cam kết xử lý.
- Tự phát hành hóa đơn.
- Tự cấp công nợ B2B.
- Tự quyết refund, compensation hoặc credit.
- Tự tối ưu tuyến xe theo thời gian thực.
- Hospital/medical laundry.
- Tự động dùng Zalo personal account không chính thức.
- Customer-facing vector RAG trên toàn bộ thư mục dự án.

## 5. Engineering principles

### P-01 — Deterministic core, probabilistic edge

LLM chỉ làm các việc có thể sai mà không gây thiệt hại trực tiếp: hiểu ngôn ngữ, trích xuất nháp, hỏi bổ sung, soạn câu trả lời và tóm tắt.

Code/domain service làm:

- giá;
- discount eligibility;
- delivery fee;
- vehicle recommendation;
- SLA clock;
- state transition;
- permission;
- approval requirement;
- margin formula;
- audit.

### P-02 — Human authority is explicit

Mọi human decision phải có:

- `decision_type`;
- `decision`;
- `actor_id`;
- `reason_code`;
- optional note;
- `decided_at`;
- object version được duyệt.

Không dùng text “đã hỏi chủ” như bằng chứng hệ thống.

### P-03 — Configuration is versioned

Pricebook, promotion, SLA, business calendar và policy không được sửa trực tiếp trên order cũ. Quote/order giữ snapshot của rule đã dùng.

### P-04 — Fail closed

Nếu thiếu dữ liệu cần thiết:

- không tự tính giá cuối cùng;
- không tự gửi;
- không tự xác nhận slot;
- không tự chuyển trạng thái;
- tạo task cho con người.

### P-05 — Operational simplicity

V1 dùng modular monolith + PostgreSQL + background worker. Không dùng microservices, Kafka, vector database hoặc Kubernetes.

### P-06 — Public/private isolation

Public agent không có quyền truy cập owner workspace, personal memory, shell, browser, nodes hoặc secret không liên quan.

### P-07 — Audit before autonomy

Một action chỉ được auto hóa sau khi:

- đã chạy Shadow Mode;
- có evaluator;
- có metric;
- có rollback;
- có kill switch;
- có owner approval.

## 6. Personas and roles

| Role | Mục tiêu | Quyền chính |
|---|---|---|
| `OWNER_ADMIN` | Quyết định business/policy | mọi cấu hình; approvals nhạy cảm; user management |
| `OPERATOR` | Nhận đơn và vận hành | customer, quote, order, SLA, intake, payment status |
| `DRIVER` | Nhận/trả đồ | delivery tasks, proof, timestamps; không sửa giá |
| `ACCOUNTANT` | Thanh toán/hóa đơn | payment reconciliation, invoice tracking; không sửa fulfillment |
| `AUDITOR` | Kiểm tra | read-only, sanitized/aggregated export, audit timeline; không mặc định export PII |
| `AGENT_SERVICE` | Soạn nháp và gọi tool | API scope hẹp; không có UI/human privilege |
| `SYSTEM_WORKER` | jobs/outbox | machine identity; action theo queue contract |

R1 có thể gán một người nhiều role nhưng permission vẫn phải tách trong code.

## 7. Core user journeys

### J-01 — B2C standard wash-dry quote

1. Nhân viên nhập contact, estimated weight, địa chỉ và nhu cầu.
2. Pricing engine tạo estimate:
   - `<6kg`: 25.000đ/kg, minimum 1kg;
   - `>=6kg`: 20.000đ/kg.
3. Nếu estimate gần ngưỡng 6kg, UI hiển thị cả hai điều kiện.
4. Promotion engine áp rule theo snapshot nếu đủ điều kiện.
5. Delivery engine tính zone và vehicle recommendation.
6. SLA engine đề xuất, nhưng staff đặt `promised_ready_at_store`.
7. AI soạn message dựa trên kết quả tool.
8. Human duyệt.
9. Khi đã tích hợp official channel, human chỉ duyệt và outbox worker là sender duy nhất.
10. Trong pre-channel Shadow, nhân viên có thể copy/send thủ công nhưng phải tạo `APPROVED_FOR_MANUAL_SEND` và `manual_send_attestation` chứa exact content hash, draft revision, actor, channel, recipient và `sent_at`. Mọi edit phải tạo revision/approval mới; trạng thái chỉ là `MANUAL_SEND_RECORDED`, không phải provider-delivered.

### J-02 — Range-priced special item

1. Agent/nhân viên nhận diện candidate service.
2. UI chỉ hiển thị toàn bộ khoảng giá.
3. Quote mang trạng thái `NEEDS_ITEM_INSPECTION`.
4. Nhân viên kiểm tra và chọn exact agreed price với reason.
5. Promotion chỉ áp nếu base-price permission và promotion scope đều cho phép.
6. Human duyệt quote cuối cùng trước khi gửi.

### J-03 — Pickup/delivery

1. Lưu địa chỉ và khoảng cách một chiều đã xác minh.
2. `<=2km`: tổng pickup + return fee = 0.
3. `>2km && <=6km`: tổng fee = 10.000đ.
4. `>6km`: yêu cầu nhập `delivery_fee_vnd` và customer consent.
5. Estimated/actual total order weight `<20kg`: xe máy; `>=20kg`: ô tô.
6. Khi estimate và actual weight đổi qua ngưỡng 20kg, tạo reapproval task.
7. Pickup và return là hai delivery legs riêng, nhưng customer-facing fee là một tổng.

### J-04 — Intake and production SLA

1. `intake_received_at`: cửa hàng nhận đồ.
2. Nhân viên cân/kiểm tra.
3. `production_accepted_at`: cửa hàng chấp nhận bắt đầu SLA.
4. Standard clothing: target tối đa 8 giờ.
5. Shoes/curtains/blankets/sheets: range 24–48 giờ; promise cụ thể do người đặt.
6. `ready_at_store`: dừng production clock.
7. Delivery clock là một đồng hồ độc lập.

### J-05 — Complaint/incident

1. Agent chỉ acknowledge và thu evidence.
2. Tạo incident linked với order/items.
3. Human đặt severity, fault decision và remedy.
4. Refund, credit, compensation hoặc rewash đều cần approval.
5. Mọi message pháp lý/nhận lỗi phải do người duyệt.

## 8. Functional requirements

### 8.1 Identity and access

- `FR-IAM-001` Hệ thống phải có user riêng; cấm shared admin account.
- `FR-IAM-002` Session cookie phải `HttpOnly`, `Secure`, `SameSite=Lax/Strict` theo flow.
- `FR-IAM-003` Mutating endpoint phải kiểm tra role và object permission phía server.
- `FR-IAM-004` Sensitive approval yêu cầu re-authentication hoặc step-up khi triển khai public access.
- `FR-IAM-005` User disable phải revoke active sessions.
- `FR-IAM-006` Mọi service account dùng scope riêng và có expiry/rotation.

### 8.2 Business configuration

- `FR-CFG-001` Business profile có effective date và status.
- `FR-CFG-002` Calendar hỗ trợ weekly hours, holiday closures và ad-hoc closures.
- `FR-CFG-003` Pricebook version chỉ được publish khi validation thành công.
- `FR-CFG-004` Published version là immutable; thay đổi tạo version mới.
- `FR-CFG-005` Promotion có timezone, start/end, eligibility event và non-stacking rule.
- `FR-CFG-006` SLA policy có scope, clock start/end, target và permission.
- `FR-CFG-007` Cấu hình draft không được dùng cho customer quote.

### 8.3 Customers, accounts and consent

- `FR-CRM-001` Hỗ trợ B2C customer và B2B account/contact.
- `FR-CRM-002` Normalize phone về E.164 khi có thể; giữ display input riêng.
- `FR-CRM-003` Deduplicate phải gợi ý, không tự merge khi chưa duyệt.
- `FR-CRM-004` Consent là append-only event, không chỉ là một boolean hiện tại.
- `FR-CRM-005` Suppression/opt-out luôn thắng campaign/follow-up.
- `FR-CRM-006` Agent chỉ đọc trường cần thiết cho session hiện tại.

### 8.4 Quote and pricing

- `FR-QTE-001` Quote có nhiều lines và version.
- `FR-QTE-002` Mọi line lưu service/version, quantity, unit, list price, discount và net amount.
- `FR-QTE-003` Estimate quote và final quote là hai loại khác nhau.
- `FR-QTE-004` Range price không được customer-accept như final nếu chưa có exact approved price.
- `FR-QTE-005` Pricing engine dùng integer VND và normalized quantity; cấm float binary cho tiền.
- `FR-QTE-006` Promotion engine lưu rule/snapshot đã áp và reason khi không áp.
- `FR-QTE-007` Delivery fee không được promotion discount.
- `FR-QTE-008` Quote edit sau approval tạo revision mới và vô hiệu approval cũ.
- `FR-QTE-009` Quote send/accept phải idempotent.
- `FR-QTE-010` Quote hết hạn không được accept nếu chưa reprice/reapprove.

### 8.5 Orders and fulfillment

- `FR-ORD-001` Order chỉ tạo từ quote snapshot hoặc manual order có audit reason.
- `FR-ORD-002` State transition phải qua command handler; cấm UI update status trực tiếp.
- `FR-ORD-003` Mọi transition ghi order event.
- `FR-ORD-004` Order line snapshot không thay đổi theo pricebook mới.
- `FR-ORD-005` Hỗ trợ bag/item identifiers và chain-of-custody ở mức pilot.
- `FR-ORD-006` Hỗ trợ một batch chứa nhiều orders và một order qua nhiều operations.
- `FR-ORD-007` Ready/delivery/close không được backdate mà không có elevated business approval.
- `FR-ORD-008` Cancellation lưu reason và actor; không xóa order.

### 8.6 SLA and capacity

- `FR-SLA-001` Tách `customer_quote_accepted_at`, `store_commercial_accepted_at`, `intake_received_at` và `production_accepted_at`.
- `FR-SLA-002` Production SLA standard bắt đầu tại `production_accepted_at`.
- `FR-SLA-003` Promotion eligibility dùng một field riêng, không tái dùng SLA timestamp.
- `FR-SLA-004` Promise phải lưu absolute timestamp và timezone.
- `FR-SLA-005` Closed-day/near-closing quote không được auto-promise.
- `FR-SLA-006` R1 auto-confirmable capacity = 0; mọi promise cần human approval.
- `FR-SLA-007` Capacity model tương lai phải dựa trên measured operations, không chỉ nominal kg.
- `FR-SLA-008` Vi phạm SLA phải được tính từ structured timestamps.

### 8.7 Delivery

- `FR-DEL-001` Lưu distance source, checked_at và checker.
- `FR-DEL-002` Dùng meters integer cho zone boundary.
- `FR-DEL-003` Fee đến 6km deterministic; trên 6km fail closed.
- `FR-DEL-004` Customer fee và allocated internal delivery cost là hai trường khác nhau.
- `FR-DEL-005` Pickup và return là hai legs có status/proof riêng.
- `FR-DEL-006` Vehicle recommendation dùng order weight; unknown weight tạo human task.
- `FR-DEL-007` Extra trip không được âm thầm gộp vào fee đã chốt.

### 8.8 Payment, invoice and B2B credit

- `FR-PAY-001` Payment hỗ trợ cash và bank transfer.
- `FR-PAY-002` Payment status không đồng nghĩa invoice status.
- `FR-PAY-003` R1 invoice là request/manual tracking, không auto issue.
- `FR-PAY-004` Bank account chỉ được gửi từ approved public configuration.
- `FR-PAY-005` B2B credit mặc định disabled.
- `FR-PAY-006` Credit account cần limit, terms, approver và effective dates.

### 8.9 Incident, rewash, refund and credit

- `FR-INC-001` Incident tách khỏi remedy/credit ledger.
- `FR-INC-002` Agent có thể tạo incident nhưng không quyết fault.
- `FR-INC-003` Remedy là command được duyệt, không phải free-text status.
- `FR-INC-004` Credit phải có amount, balance, expiry policy, source incident và ledger entries.
- `FR-INC-005` Không tự áp quy tắc thanh lý đồ quá hạn cho đến khi legal approval.

### 8.10 Approvals and audit

- `FR-APR-001` Approval target phải chứa object id + immutable revision/hash.
- `FR-APR-002` Approval cũ không hợp lệ sau khi target thay đổi.
- `FR-APR-003` Một người không được tự duyệt một số action nhạy cảm khi policy yêu cầu separation of duties.
- `FR-APR-004` Audit event append-only; correction là event mới.
- `FR-APR-005` Audit phải bao gồm human, agent, worker và integration actors.
- `FR-APR-006` AI-generated draft phải link tới agent run, prompt version và tool results.

### 8.11 Agent integration

- `FR-AI-001` Public agent chỉ dùng allowlisted business tools.
- `FR-AI-002` Tool schemas phải strict; reject unknown fields.
- `FR-AI-003` Model không được trực tiếp gọi database.
- `FR-AI-004` Outbound message ở R1 luôn cần human approval.
- `FR-AI-005` Tool call và model call có trace id.
- `FR-AI-006` Prompt injection hoặc unsupported request phải handoff/fail closed.
- `FR-AI-007` Agent context chỉ lấy approved structured policy.
- `FR-AI-008` Agent response phải phân biệt estimate, final price và human-confirmed promise.

### 8.12 Reporting and export

- `FR-RPT-001` Dashboard phải hiển thị order funnel, on-time, rewash/incidents và revenue.
- `FR-RPT-002` Margin phải gắn completeness flag; không gọi 70% còn lại là profit.
- `FR-RPT-003` Export CSV phải chống spreadsheet formula injection.
- `FR-RPT-004` Cycle log và delivery cost log phải export tương thích template pilot.
- `FR-RPT-005` KPI có numerator, denominator, time window và data quality status.

## 9. System architecture

### 9.1 Components

1. **Operator Web/PWA**
   - mobile-first;
   - staff-only;
   - no direct DB access.

2. **Business Control Plane**
   - modular monolith;
   - REST/JSON command-query API;
   - pricing, order, SLA, approval, policy modules.

3. **PostgreSQL**
   - system of record;
   - transactions, constraints, inbox/outbox, audit.

4. **Worker**
   - outbox delivery;
   - reminders;
   - exports;
   - retry/DLQ.

5. **Public OpenClaw Cell**
   - separate profile/state/workspace; separate VM/VPS là bắt buộc trước mọi public/untrusted inbound;
   - sandboxed;
   - không expose filesystem mutation tool; runtime chỉ ghi vào dedicated state/log directories trên cell riêng;
   - no host exec/browser/nodes, owner workspace hoặc Docker socket;
   - không giữ channel credential và không gọi channel provider;
   - narrow service credential.
   - selected customer-agent runtime from the agent-integration phase onward;
   - exact provider/model and explicit OpenClaw runtime route pinned by release evidence;
   - provider request storage/retention behavior verified before real customer data.

6. **Agent Tool Facade**
   - scoped endpoints;
   - schema validation;
   - rate limits;
   - idempotency;
   - no generic SQL or arbitrary HTTP.

7. **Channel Adapters**
   - official channels only;
   - normalize inbound;
   - deduplicate;
   - enqueue outbound.

8. **Observability**
   - structured logs;
   - metrics;
   - distributed traces;
   - alerts;
   - privacy-aware redaction.

9. **Private Owner OpenClaw**
   - remains owner-only;
   - may read approved analytics through a scoped API/export;
   - never receives untrusted public channel traffic directly.

### 9.2 Deployment decision

R1 production target:

- Linux host/VM;
- containerized app, worker, PostgreSQL and reverse proxy;
- admin UI reachable only by private network/VPN or strongly authenticated HTTPS;
- same-host profile/OS-user isolation chỉ dùng cho development/internal pre-channel Shadow;
- public OpenClaw cell bắt buộc chạy trên VM/VPS riêng với OS identity, state, secrets, model key và network policy riêng;
- database not exposed publicly;
- backups outside primary host.

Local development on Windows is allowed, nhưng production business gateway không được dùng chung trust boundary với personal Gateway hiện tại.

## 10. Non-functional requirements

### 10.1 Availability and degradation

- `NFR-AVL-001` Internal control plane target: 99.5% during 08:00–20:00.
- `NFR-AVL-002` AI outage không được ngăn staff tạo/cập nhật đơn thủ công.
- `NFR-AVL-003` Channel outage phải queue/reconcile hoặc chuyển manual workflow.
- `NFR-AVL-004` Database migration phải có rollback/forward-fix plan.

### 10.2 Performance

- `NFR-PERF-001` Deterministic quote preview p95 < 500ms trong LAN/normal load.
- `NFR-PERF-002` Staff mutation p95 < 1s, không tính external channel latency.
- `NFR-PERF-003` Agent draft p95 target < 15s; timeout tạo manual task.
- `NFR-PERF-004` Danh sách 10.000 orders vẫn tải trang p95 < 2s với pagination/index.

### 10.3 Consistency

- `NFR-CON-001` Pricing + quote snapshot commit trong một transaction.
- `NFR-CON-002` State change + outbox event commit trong một transaction.
- `NFR-CON-003` Payment/invoice/delivery là bounded contexts riêng, không dùng một status chung.
- `NFR-CON-004` Optimistic concurrency trên aggregate version.

### 10.4 Security and privacy

- `NFR-SEC-001` TLS cho mọi network hop không-local.
- `NFR-SEC-002` Secrets không nằm trong repo/log/prompt.
- `NFR-SEC-003` PII encryption at rest theo capability của database/host và backup encryption.
- `NFR-SEC-004` Least privilege cho user, worker, gateway và channel.
- `NFR-SEC-005` Sensitive fields phải redacted trong telemetry.
- `NFR-SEC-006` Dependency/container scanning trước release.

### 10.5 Recovery

- `NFR-DR-001` RPO target 15 phút áp dụng từ đơn khách thật đầu tiên.
- `NFR-DR-002` RTO target 4 giờ.
- `NFR-DR-003` RPO 15 phút phải dùng managed PITR hoặc continuous WAL archiving + base backups; daily snapshot đơn lẻ không đủ.
- `NFR-DR-004` Nếu database nằm cùng application host, off-host encrypted backup là bắt buộc.
- `NFR-DR-005` Restore test phải chứng minh recovery point không cũ hơn 15 phút; chạy trước đơn thật, sau schema milestone lớn và tối thiểu hằng quý.

### 10.6 Maintainability

- TypeScript strict mode.
- Schema validation ở mọi boundary.
- No business rule duplicated in UI/prompt.
- Domain modules có unit tests.
- Migration, seed và rollback/forward-fix được version control.
- Structured ADRs cho quyết định khó đảo ngược.

## 11. Autonomy stages

| Stage | Agent được làm | Human bắt buộc |
|---|---|---|
| `MANUAL_TRUTH` | Không customer automation | mọi action |
| `SHADOW` | extract + draft + tool preview | mọi outbound và commitment |
| `ASSISTED` | auto FAQ/intake và `LIST_PRICE_INFO` bằng deterministic template; tạo draft personalized quote | subtotal/total/promotion result/final price/slot/special/delivery >6km/remedy |
| `BOUNDED` | auto estimate fixed-price trong envelope; approved status replies | final special price, capacity exception, credit/refund/B2B |

Stage là feature flag theo capability, không phải một nút “bật AI toàn hệ thống”.

Gate IDs, cumulative dependencies and capability mapping are machine-readable in
`delivery/GATE_REGISTRY.yaml`. That registry must remain identical to the release-gate JSON Schema;
prose headings do not create a second gate contract.

## 12. Launch gates

### Gate G1 — R1 internal app

- Pricing deterministic tests pass 100%.
- Delivery boundary tests pass 100%.
- State transition tests pass.
- RBAC negative tests pass.
- Audit coverage 100% mutating commands.
- Material mutation + domain event + audit event + required outbox event commit atomically; failure-injection test proves audit failure rolls back mutation.
- MFA enforced for `OWNER_ADMIN`; session revoke/disable-user recovery tests pass.
- PITR/WAL backup and <=15-minute restore-point drill pass before the first real order.
- Pre-channel manual-send attestation flow tested if staff will copy/send outside the system.
- No public channel connected.

### Gate G2 — Shadow pilot

- At least 30 real orders; 10 cycle logs; 20 delivery logs.
- Zero wrong confirmed/sent monetary values from import, configuration, calculation, stale revision, manual edit or post-approval mutation.
- 100% outbound is either worker-executed from an approved envelope or manual-attested against an approved exact content hash.
- 100% delivery >6km có manual fee + consent.
- AI unsupported-commitment rate = 0 in reviewed sample.

### Gate G3 — Assisted replies

- Inherits every G1/G2 requirement plus all `before public channel` security gates.
- Golden eval set pass theo thresholds trong agent spec.
- 14 consecutive pilot days không có P0 safety incident.
- Kill switch tested.
- Separate public VM/VPS and Agent Tool Facade contracts passed security review.
- Official channel security review passed.
- MFA enforced for every role that can view PII, approve, export, publish policy, access finance or see delivery addresses.
- Owner approves exact auto-send intents.
- Auto-send list price is limited to deterministic `LIST_PRICE_INFO`; personalized subtotal/quote/promotion/total remains human-approved throughout Assisted.

### Gate G4 — Bounded quotes

- Inherits G1–G3 and capability-specific security/eval gates.
- At least 30 observation days and at least 100 eligible capability-specific cases.
- Zero critical safety, authorization or confirmed-price errors across primary and fallback paths.
- Measured processing/delivery economics available.
- Capacity model validated.
- Pricebook/promotion publishing workflow stable.
- Relevant measurement policy, promotion eligibility event, calendar/cutoff and public customer policy are published.
- Material factual/tool-argument correction rate <=5% across eligible cases; numerator excludes style-only edits and denominator is all eligible predictions.
- Kill-switch, incident-response, rollback, audit replay and canary tests pass.

## 13. Definition of Done — system level

Một feature chỉ “done” khi:

1. requirement và acceptance criteria rõ;
2. domain rule chỉ tồn tại một nguồn trong code;
3. unit/integration/security tests pass;
4. audit và telemetry có;
5. failure path và retry path đã test;
6. permissions negative tests pass;
7. documentation/runbook cập nhật;
8. migration/rollback hoặc forward-fix có;
9. owner-facing behavior được demo;
10. feature flag/kill switch được xác minh nếu có automation.

## 14. Open decisions that do not block R1 build

Các mục sau giữ `HUMAN_ONLY` cho đến khi phê duyệt:

- weight precision/rounding ngoài minimum 1kg;
- minimum 1kg cho dịch vụ per-kg khác;
- exact promotion eligibility event;
- tax-inclusive/exclusive price;
- invoice workflow;
- B2B credit terms;
- rewash window;
- compensation matrix;
- storage fee/unclaimed goods;
- exact price inside price ranges;
- promotion scope cho dịch vụ mơ hồ;
- fee >6km;
- capacity auto-confirm;
- public channel choice và OA/API access.

## 15. Mandatory ADRs

- `ADR-001`: Modular monolith, không microservices.
- `ADR-002`: PostgreSQL là canonical source.
- `ADR-003`: Deterministic pricing/policy; LLM không tính tiền.
- `ADR-004`: Public OpenClaw cell tách private owner Gateway.
- `ADR-005`: Transactional inbox/outbox + idempotency.
- `ADR-006`: All outbound human-approved trong Shadow Mode.
- `ADR-007`: Structured approved knowledge, không broad RAG.
- `ADR-008`: Official customer channels only.
- `ADR-009`: Append-only business/audit events.
- `ADR-010`: Unknown business rule fails closed to human.
- `ADR-011`: Public OpenClaw is the constrained agent runtime; Python remains business/security
  authority and provider-data behavior is a release gate.
