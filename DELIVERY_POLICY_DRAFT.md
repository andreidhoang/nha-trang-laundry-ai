# Chính sách giao nhận — v0.4

Cập nhật: 2026-07-27  
Trạng thái: **OWNER_CONFIRMED — phí trên 6km do người phụ trách nhập theo từng trường hợp**

## 1. Sự thật đã xác nhận

- Đơn dưới 20kg: giao nhận bằng xe máy.
- Đơn từ đúng 20kg trở lên: giao nhận bằng ô tô.
- Người của cửa hàng trực tiếp giao nhận.
- Khoảng cách owner rule: ≤2km miễn toàn bộ phí nhận và trả cho mọi đơn, không phụ thuộc khối lượng, phương tiện hoặc giá trị hóa đơn.
- Khoảng cách >2km đến 6km: tổng phí 10.000đ cho cả nhận + trả.
- Khoảng cách >6km: nhân viên thỏa thuận với khách và nhập tổng phí nhận + trả vào `delivery_fee_vnd`.
- Production SLA theo dịch vụ kết thúc khi đồ sẵn sàng tại tiệm: giặt sấy quần áo thường ≤8 giờ; giày, rèm cửa, chăn và ga trung bình 24–48 giờ. Delivery có ETA riêng.
- Chi phí xử lý giặt hiện chỉ được ước tính bằng 30% doanh thu dịch vụ.

## 2. Hai zone trợ giá đã được chủ tiệm chọn

Nếu mỗi lượt bắt đầu và kết thúc tại tiệm:

```text
route_km_single_order ≈ 4 × one_way_distance_km
```

Một đơn cách 6km có thể làm phương tiện chạy khoảng 24km cho hai công đoạn nhận và trả. Thu 10.000đ cho cả đơn tương đương tối đa khoảng 417đ/km, trước thời gian nhân sự, nhiên liệu, bảo trì và khấu hao. Đây là quyết định trợ giá có chủ đích để khách nhận nhiều giá trị hơn.

Đơn giặt sấy 1kg:

```text
doanh thu dịch vụ = 25.000đ
chi phí xử lý ước tính = 7.500đ
còn lại trước giao nhận và overhead = 17.500đ
```

Chi phí của cả hai zone phải được log riêng để biết mức trợ giá thực và tránh mở rộng phí thấp sang zone xa mà làm mất biên lợi nhuận.

## 3. Phí đã chốt đến 6km

Khoảng cách tính một chiều từ cửa hàng đến địa chỉ khách theo Google Maps. Pickup và return là hai lượt riêng.

| Khoảng cách một chiều | Tổng phí cho 01 lượt nhận + 01 lượt trả |
|---|---:|
| ≤2km | 0đ |
| >2km đến 6km | 10.000đ |

Khách luôn có lựa chọn tự giao/nhận tại tiệm để không phát sinh phí.

Phí trên bao gồm một lần nhận thành công và một lần trả thành công. Chuyến phát sinh do đổi địa chỉ, khách vắng mặt, giao lại, giao gấp, ngoài tuyến, phí đường/đỗ xe hoặc địa chỉ khó tiếp cận phải được báo riêng và khách đồng ý trước.

## 4. Một tham số duy nhất cho phí giao nhận

```text
delivery_fee_vnd
```

- Kiểu dữ liệu: số nguyên VND, không âm.
- Ý nghĩa: **tổng phí cho 01 lượt nhận thành công + 01 lượt trả thành công**, không phải phí mỗi chiều.
- Đây là trường phí giao nhận duy nhất trên đơn hàng.

Quy tắc:

```text
if distance_km <= 2:
    delivery_fee_vnd = 0
elif distance_km <= 6:
    delivery_fee_vnd = 10000
else:
    delivery_fee_vnd = HUMAN_INPUT_REQUIRED
```

Với khoảng cách >6km:

- nhân viên cân nhắc khoảng cách, khối lượng, phương tiện, khả năng ghép tuyến và tình huống của khách;
- nhân viên trao đổi tổng phí nhận + trả với khách rồi nhập `delivery_fee_vnd`;
- khách phải đồng ý trước khi cửa hàng thực hiện giao nhận;
- agent không tự suy diễn, tự đặt phí hoặc tự gửi báo giá khi trường này còn trống.

Để bảo vệ biên mà vẫn giữ ưu đãi:

- giao theo khung giờ và ghép tuyến;
- không cam kết giờ chính xác cho giao tiêu chuẩn;
- giao gấp/chạy riêng/giao lại do khách vắng báo phí riêng;
- khách tự giao/nhận tại tiệm luôn không mất phí.

Wording cho khách ở zone >6km:

> Với địa chỉ trên 6km, cửa hàng sẽ kiểm tra tuyến và báo một mức phí trọn gói cho cả nhận và trả. Cửa hàng chỉ thực hiện sau khi anh/chị đồng ý.

## 5. Cách đo để thay guardrail bằng số thật

Trong 20 đơn giao nhận đầu tiên, ghi:

- khoảng cách một chiều;
- phương tiện sử dụng;
- tổng km đồng hồ xe;
- số đơn ghép trên tuyến;
- phút nhân sự rời tiệm;
- nhiên liệu, phí đường/đỗ xe và phí đối tác;
- số lượt pickup/return;
- doanh thu dịch vụ và phí giao nhận;
- đơn trễ hoặc công việc tại tiệm bị ảnh hưởng.

Sau 20 đơn:

```text
allocated_delivery_cost
= (actual_route_cost / orders_on_route)
+ allocated_staff_cost

contribution_after_delivery
= service_revenue
- measured_processing_cost
- allocated_delivery_cost
- payment_fee
- rewash_damage_allowance
```

Phí >6km tiếp tục là `HUMAN_INPUT_REQUIRED` cho đến khi chủ tiệm chủ động phê duyệt một quy tắc mới. Delivery cost log chỉ dùng để kiểm tra biên lợi nhuận và hỗ trợ nhân viên ra quyết định, không tự thay đổi phí đã thỏa thuận với khách.

## 6. Quyền agent

- ≤2km: agent được áp tổng phí `0đ` sau khi xác minh khoảng cách.
- >2–6km: agent được áp tổng phí `10.000đ` sau khi xác minh khoảng cách.
- >6km: agent chuyển người phụ trách; chỉ tiếp tục khi `delivery_fee_vnd` đã được người phụ trách nhập và khách đồng ý.
- Slot, chuyến phát sinh, giao gấp, B2B credit, discount ngoài chương trình ưu đãi đã cấu hình và ngoại lệ luôn `HUMAN`.
