# Nha Trang Laundry AI — Engineering Specification Pack v1

**Ngày phát hành:** 2026-07-27  
**Trạng thái:** `SPEC_APPROVED_WITH_EXECUTION_GATES`  
**Phạm vi:** Internal Shadow Mode MVP → Assisted Mode → Bounded Autonomy  
**Chủ thể kinh doanh:** Giặt Là Sạch Cộng / CÔNG TY TNHH A & T CARE

## Bộ tài liệu

1. [`ENGINEERING_SPEC_V1.md`](./ENGINEERING_SPEC_V1.md)  
   Product scope, nguyên tắc thiết kế, functional/non-functional requirements, kiến trúc tổng thể và acceptance criteria.

2. [`DOMAIN_DATA_API_SPEC_V1.md`](./DOMAIN_DATA_API_SPEC_V1.md)  
   Canonical domain model, kiểu dữ liệu, state machines, pricing/SLA/delivery algorithms, API và event contracts.

3. [`AGENT_SYSTEM_AND_EVAL_SPEC_V1.md`](./AGENT_SYSTEM_AND_EVAL_SPEC_V1.md)  
   Agent topology, tool boundaries, prompt/context policy, human approval, evals, red-team và autonomy gates.

4. [`SECURITY_RELIABILITY_SPEC_V1.md`](./SECURITY_RELIABILITY_SPEC_V1.md)  
   Trust boundaries, threat model, RBAC, secrets, audit, observability, SLO, backup/DR và incident response.

5. [`IMPLEMENTATION_ROADMAP_V1.md`](./IMPLEMENTATION_ROADMAP_V1.md)  
   Build-vs-buy preflight, repository layout, milestones, backlog, test strategy, rollout và Definition of Done.

6. [`production-architecture-v1.html`](./production-architecture-v1.html)  
   Sơ đồ kiến trúc production dạng standalone HTML/SVG.

   Preview tĩnh: [`production-architecture-v1.png`](./production-architecture-v1.png).

7. [`TEAM_REVIEW_REPORT_V1.md`](./TEAM_REVIEW_REPORT_V1.md)  
   Kết quả review đa chuyên môn, P0 resolution matrix và quyết định go/no-go theo từng stage.

8. [`../docs/adr/0002-production-agent-runtime-and-trust-boundaries.md`](../docs/adr/0002-production-agent-runtime-and-trust-boundaries.md)  
   Trust boundary, Python authority, model route và provider-data gate ban đầu.

9. [`../docs/adr/0003-provider-neutral-agent-runtime-and-channel-operations.md`](../docs/adr/0003-provider-neutral-agent-runtime-and-channel-operations.md)
   Supersede việc bắt buộc OpenClaw; ghi decision function từ first principles, bounded Responses
   state machine, same-envelope parity/retirement gates, channel adapter độc lập và AI operations
   dashboard.

10. [`../docs/PROJECT_ENGINEERING_FIRST_PRINCIPLES_VI.md`](../docs/PROJECT_ENGINEERING_FIRST_PRINCIPLES_VI.md)
    Giải thích tiếng Việt từ big picture xuống runtime/context/harness, bao gồm lý do chọn custom
    Responses cho workload hiện tại và cách chứng minh trước khi retire OpenClaw.

## Contracts có thể chạy bằng máy

- [`contracts/canonical-enums-v1.json`](./contracts/canonical-enums-v1.json) — enum, canonicalization
  và snapshot-hash contract.
- [`contracts/pricebook-import-manifest-v1.json`](./contracts/pricebook-import-manifest-v1.json) —
  exact source hash, canonical snapshot hash, alias map và `44 → 43/43/2` import counts.
- [`contracts/agent-tools-v1.openapi.yaml`](./contracts/agent-tools-v1.openapi.yaml) — nguồn duy nhất
  để sinh tool schema/validator/SDK cho public agent.
- [`contracts/release-gate-manifest-v1.schema.json`](./contracts/release-gate-manifest-v1.schema.json)
  — schema ký duyệt release theo stage/capability và evidence hash.
- [`contracts/openclaw-repackage-manifest-v2.schema.json`](./contracts/openclaw-repackage-manifest-v2.schema.json)
  — contract EVAL_ONLY bất biến cho artifact OpenClaw derived r2, bốn dependency replacements và
  rollback r1 được pin đầy đủ.
- [`evals/eval-manifest-v1.yaml`](./evals/eval-manifest-v1.yaml) — release gates, P0 cases,
  deterministic cases và adversarial evaluation.
- [`../delivery/PROGRAM_PLAN.yaml`](../delivery/PROGRAM_PLAN.yaml) — stable phases và dependency order.
- [`../delivery/GATE_REGISTRY.yaml`](../delivery/GATE_REGISTRY.yaml) — cumulative gate/capability mapping.

Prose trong các tài liệu giải thích ý định thiết kế. Nếu tên operation, enum hoặc field khác contract
có cấu trúc thì contract có cấu trúc thắng và CI phải báo drift.

## Quy tắc nguồn sự thật

Khi có xung đột, dùng thứ tự ưu tiên sau:

1. Quyết định đã được chủ doanh nghiệp phê duyệt và lưu trong bản cấu hình có version.
2. Snapshot dữ liệu có cấu trúc trong database production.
3. Engineering spec và policy đã được phê duyệt.
4. CSV seed trong `../templates/`.
5. Tài liệu nghiên cứu hoặc playbook.
6. Ảnh/media gốc và ghi chú chưa chuẩn hóa.

`POLICY_RISK_REVIEW.md` là tài liệu nội bộ để nhận diện rủi ro. File này **không được đưa vào customer-facing RAG corpus**.

## Nguyên tắc khóa

- PostgreSQL là system of record; mọi agent runtime đều không phải database nghiệp vụ.
- LLM không tính tiền, không tự chọn giá trong khoảng, không xác nhận capacity và không phát hành refund/credit.
- Mọi phép tính giá, ưu đãi, phí giao nhận, SLA và trạng thái đơn phải do deterministic domain code thực hiện.
- Shadow Mode yêu cầu con người duyệt mọi outbound message và mọi cam kết thương mại.
- Public agent runtime phải ở security cell riêng trước mọi public/untrusted inbound; control endpoint
  không được public/reverse-proxy.
- Public agent runtime không có channel credential; chỉ transactional outbox worker được gửi.
- Mọi thay đổi trạng thái hoặc gửi tin phải có idempotency key và audit event.
- Không kết nối Zalo cá nhân không chính thức vào production customer service.

## Điều kiện để bắt đầu code

Không cần hoàn tất toàn bộ business policy để xây Shadow Mode. Các trường chưa chốt phải được biểu diễn bằng:

- `HUMAN_INPUT_REQUIRED`;
- `HUMAN_APPROVAL_REQUIRED`;
- hoặc `NOT_SUPPORTED`.

Không được “điền bằng suy đoán” để làm workflow chạy qua.

## Phạm vi được phép bắt đầu

Bộ spec cho phép bắt đầu M0–M3: nền tảng repo/CI, database, deterministic engines, pilot
instrumentation, Staff PWA và approval workflow. Nó chưa cho phép kết nối public channel hoặc bật
autonomy. Điều kiện launch chi tiết nằm trong
[`TEAM_REVIEW_REPORT_V1.md`](./TEAM_REVIEW_REPORT_V1.md).
