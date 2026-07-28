# Chương trình ưu đãi 17/07–31/08/2026

Cập nhật: 2026-07-27  
Trạng thái: **OWNER_CONFIRMED — phạm vi cốt lõi đã xác nhận; các dịch vụ biên cần nhân viên phân loại**

## 1. Điều khoản đã xác nhận

- Mã chương trình: `PROMO_WET30_DRY40_20260717_20260831`.
- Bắt đầu: **17/07/2026**.
- Kết thúc: **hết ngày 31/08/2026**, múi giờ `Asia/Ho_Chi_Minh`.
- Giảm **30%** cho các dịch vụ **giặt ướt**.
- Giảm **40%** cho các dịch vụ **giặt khô**.
- Nguồn: biển ưu đãi tại cửa hàng và xác nhận trực tiếp của chủ tiệm.
- Giá trong `PRICEBOOK_V1.md` và `templates/services-pricebook.csv` là **giá niêm yết trước ưu đãi**.

Hệ thống xét ngày áp dụng theo `accepted_at`: thời điểm cửa hàng đã tiếp nhận và chấp nhận đơn. Đơn được chấp nhận trong thời gian chương trình vẫn giữ ưu đãi dù hoàn tất sau ngày 31/08/2026.

## 2. Phạm vi áp dụng an toàn

### Giảm 30% — giặt ướt

Các nhóm thể hiện rõ trên biển:

- giặt sấy quần áo thường;
- chăn, ga, rèm và thảm;
- vệ sinh giày dép;
- gấu bông và gối.

### Giảm 40% — giặt khô

- Toàn bộ các dòng thuộc mục **Giặt khô** trong `PRICEBOOK_V1.md`.
- Với dòng có khoảng giá, nhân viên chọn giá niêm yết cuối cùng sau khi kiểm tra món; sau đó hệ thống mới giảm 40%.

### Chưa tự động áp

Các mục sau cần nhân viên xác nhận vì biển không thể hiện đủ rõ hoặc không thuần là giặt ướt/giặt khô:

- sấy riêng;
- ủi đồ;
- vệ sinh/làm mới đồ da;
- topper;
- vệ sinh sofa;
- tẩy vết bẩn;
- tẩy ruột gối;
- phí tháo/lắp rèm nếu cần tách khỏi phần giặt;
- phí giao nhận, phí giao gấp, phụ phí express;
- bảng giá hợp đồng/công nợ B2B.

Danh sách có cấu trúc nằm tại `templates/promotion-service-rules.csv`.

## 3. Công thức

```text
list_service_subtotal = tổng tiền dịch vụ theo giá niêm yết
discount_rate = 0,30 nếu giặt ướt; 0,40 nếu giặt khô
discount_amount = list_service_subtotal × discount_rate
net_service_subtotal = list_service_subtotal - discount_amount
amount_due = net_service_subtotal + delivery_fee_vnd + phụ phí đã được khách duyệt
```

- Ưu đãi chỉ tính trên tiền dịch vụ thuộc phạm vi áp dụng; phí giao nhận được tính riêng.
- Không tự làm tròn khối lượng hoặc tiền ngoài quy tắc đã được chủ tiệm xác nhận.
- Agent không tự cộng dồn với voucher/chương trình khác. Nếu có ưu đãi khác, chuyển người duyệt cho đến khi chủ tiệm chốt quy tắc cộng dồn.

## 4. Ví dụ giá sau ưu đãi

| Dịch vụ | Giá niêm yết | Ưu đãi | Giá sau ưu đãi |
|---|---:|---:|---:|
| Giặt sấy quần áo thường dưới 6kg | 25.000đ/kg | 30% | 17.500đ/kg |
| Giặt sấy quần áo thường từ 6kg | 20.000đ/kg | 30% | 14.000đ/kg |
| Chăn | 30.000đ/kg | 30% | 21.000đ/kg |
| Giày thể thao | 70.000đ/đôi | 30% | 49.000đ/đôi |
| Áo sơ mi giặt khô | 50.000đ/cái | 40% | 30.000đ/cái |
| Váy cưới giặt khô | 400.000đ/cái | 40% | 240.000đ/cái |

Với giặt sấy cân ký tiêu chuẩn, minimum dưới 1kg được giảm theo chương trình: giá niêm yết 25.000đ còn **17.500đ** trong thời gian ưu đãi.

## 5. Quyền của agent

- Được tự áp ưu đãi khi `accepted_at` nằm trong thời gian hiệu lực, dịch vụ có rule `AUTO_APPLY`, và giá gốc đã xác định chắc chắn.
- Với giá theo khoảng, chỉ được nêu **khoảng giá sau ưu đãi**; nhân viên vẫn chọn giá cuối cùng.
- Không tự phân loại một dịch vụ mơ hồ thành giặt ướt hoặc giặt khô.
- Không tự áp ưu đãi cho đơn B2B có bảng giá hợp đồng.
- Từ 00:00 ngày 01/09/2026, tự quay về giá niêm yết nếu không có chương trình mới.

