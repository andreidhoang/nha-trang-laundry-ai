# Chính sách tin dịch vụ — Transactional messaging policy, v1 (`DEC-033`)

**Trạng thái: khuyến nghị, chưa có hiệu lực cho đến khi chủ tiệm công bố.**
**Status: a recommendation, not in force until the shop owner publishes it.**

Tài liệu máy đọc được: `templates/transactional-messaging-policy-dec-033.json`. Công bố bằng:
The machine-readable document is `templates/transactional-messaging-policy-dec-033.json`. It is
published with:

```bash
./scripts/shop-admin publish_messaging_policy.py --actor-id '<owner staff uuid>'
# or, where the database is reachable from the host:
DATABASE_URL=... uv run python scripts/publish_messaging_policy.py --actor-id '<owner staff uuid>'
```

Chỉ một `OWNER_ADMIN` đang hoạt động công bố được; mọi mã nhân viên khác bị từ chối và không có gì
được ghi. Only an active `OWNER_ADMIN` can publish it; any other staff id is refused and nothing is
written.

---

## Tiếng Việt

**Công bố tài liệu này là việc chủ tiệm xác nhận rằng đây là căn cứ của tiệm để gửi tin dịch vụ cho
khách.** Tin dịch vụ là tin về chính việc giặt của khách — đồ đã xong, giờ giao, hỏi lại thông tin
đơn. Tin dịch vụ không phải tin quảng cáo; tin quảng cáo cần khách đồng ý riêng.

Tiệm chỉ chủ động gửi tin dịch vụ cho một khách trên một kênh khi có ít nhất một căn cứ sau:

1. **Khách nhắn cho tiệm** trên kênh đó trong vòng **48 giờ** trước lúc gửi (`CUSTOMER_INITIATED`).
2. **Khách có đơn** chưa đóng, hoặc đã hoàn tất chưa quá **72 giờ** (`OPEN_ORDER`). Đơn đã huỷ không
   tính.

Khách đã nhắn dừng (STOP) trên kênh nào thì tiệm **không chủ động gửi gì** trên kênh đó — kể cả tin
dịch vụ — cho đến khi chủ tiệm hoặc người duyệt gỡ chặn dựa trên **một tin nhắn mới của chính khách**
trên kênh đó. Gỡ chặn chỉ áp dụng cho tin dịch vụ; tin quảng cáo vẫn bị chặn.

Chưa công bố thì hệ thống không cho gửi tin dịch vụ nào. Muốn đổi số giờ hay căn cứ: sửa tài liệu rồi
công bố lại — bản mới có hiệu lực ngay, bản cũ vẫn được lưu nguyên.

## English

**Publishing this document is the shop owner's confirmation that these are the shop's grounds for
sending service messages to a customer.** A service message is about the customer's own laundry —
it is ready, a delivery time, a question about the order. A service message is not marketing;
marketing needs the customer's separate consent.

The shop initiates a service message to a customer on a channel only when at least one ground holds:

1. **The customer wrote to the shop** on that channel within **48 hours** before the send
   (`CUSTOMER_INITIATED`).
2. **The customer has an order** that is not closed, or was completed no more than **72 hours** ago
   (`OPEN_ORDER`). A cancelled order does not count.

A customer who wrote STOP on a channel is sent **nothing the shop initiates** on that channel —
service messages included — until the owner or an approver releases the block on **a new message
from that customer** on that channel. A release lifts service messages only; marketing stays
blocked.

Until this is published, no service message may be sent. To change the hours or the grounds, edit
the document and publish again: the new version is in force at once, and every earlier version is
kept exactly as it was published.

---

## Notes for engineering

- The figures are the lead's recommendation under `DEC-033`; the grounds are the owner's to confirm,
  because they amount to the shop's legal basis for service messages under Vietnamese personal-data
  rules, which is outside the delegation (`docs/DECISION_RECORD_CONSENT_2026-09-25.md`).
- The rule is `nha_trang_laundry_domain.service_messaging.decide_transactional_egress`; the guard is
  `nha_trang_laundry_db.consent_egress.check_egress_allowed(purpose="TRANSACTIONAL", at=...)`.
- Window edges are inclusive: a message received exactly 48 hours before the send counts, one
  microsecond earlier does not. A message dated after the instant judged does not count.
