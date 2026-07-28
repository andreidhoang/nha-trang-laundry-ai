# Bảng giá Giặt Là Sạch Cộng — v1

Cập nhật: 2026-07-27  
Trạng thái: **OWNER_CONFIRMED — toàn bộ giá trên ảnh bảng giá đang áp dụng; đây là giá niêm yết trước ưu đãi**

- Thương hiệu/cửa hàng: **Giặt Là Sạch Cộng**.
- Đơn vị vận hành/xuất hóa đơn: **CÔNG TY TNHH A & T CARE**.
- Mã số thuế: **4202059758**.
- Ảnh bảng giá được đối chiếu lại ngày 2026-07-27: **44/44 dòng dịch vụ đã có trong pricebook và CSV, không tạo bản ghi trùng**.
- Ảnh in “Trên 6KG”; xác nhận trực tiếp sau đó của chủ tiệm là nguồn ưu tiên: **đúng 6kg đã áp mức 20.000đ/kg**.

## 1. Quy tắc sử dụng

- Giá cuối cùng dựa trên cân thực tế, loại món, kích thước, chất liệu và tình trạng được cửa hàng xác nhận.
- Với giặt sấy cân ký tiêu chuẩn, đơn dưới 1kg được tính tối thiểu 1kg.
- Quy tắc cân/làm tròn đối với khối lượng từ 1kg trở lên vẫn `PENDING`; agent không tự làm tròn.
- Mức giá cố định được phép dùng để báo giá niêm yết. Mức giá theo khoảng chỉ được báo cả khoảng; nhân viên xác nhận mức cuối cùng sau khi kiểm tra.
- Không tự áp bảng giá này cho B2B towel/linen, express, phí giao nhận hoặc trường hợp không phân loại chắc chắn.
- Chương trình ưu đãi 17/07–31/08/2026 được áp theo `PROMOTION_2026_08.md`; không sửa đè giá niêm yết trong tài liệu này.
- Có hỗ trợ xuất hóa đơn, nhưng giá đã gồm hay chưa gồm thuế vẫn `PENDING`.

Quyền agent:

- `AUTO_ESTIMATE`: được tính giá ước tính theo công thức đã xác nhận; chưa tự xác nhận slot.
- `LIST_PRICE_ONLY`: được nêu giá niêm yết; nhân viên xác nhận loại món và đơn.
- `RANGE_ONLY_HUMAN_FINAL`: chỉ nêu toàn bộ khoảng giá; không tự chọn đầu thấp/cao.

## 2. Giặt và sấy quần áo thường

| Dịch vụ | Giá | Quyền agent |
|---|---:|---|
| Giặt sấy, tổng khối lượng dưới 6kg | 25.000đ/kg | `AUTO_ESTIMATE` |
| Giặt sấy, tổng khối lượng từ 6kg trở lên | 20.000đ/kg | `AUTO_ESTIMATE` |
| Sấy riêng quần áo ướt | 20.000đ/kg | `LIST_PRICE_ONLY` |
| Sấy riêng chăn ga | 30.000đ/kg | `LIST_PRICE_ONLY` |

Với giặt sấy cân ký tiêu chuẩn:

```text
billable_kg = max(actual_kg, 1)
Nếu actual_kg < 6:  giá = billable_kg × 25.000đ
Nếu actual_kg >= 6: giá = actual_kg × 20.000đ
```

Ví dụ: 0,6kg = 25.000đ; 5,9kg = 147.500đ; 6kg = 120.000đ; 6,1kg = 122.000đ.

## 3. Giặt sấy chăn gối

| Dịch vụ | Giá | Quyền agent |
|---|---:|---|
| Chăn | 30.000đ/kg | `LIST_PRICE_ONLY` |
| Topper | 50.000đ/kg | `LIST_PRICE_ONLY` |
| Gối | 30.000–90.000đ/cái | `RANGE_ONLY_HUMAN_FINAL` |

## 4. Ủi đồ

| Dịch vụ | Giá | Quyền agent |
|---|---:|---|
| Áo, quần thun | 15.000đ/cái | `LIST_PRICE_ONLY` |
| Quần tây, sơ mi | 20.000đ/cái | `LIST_PRICE_ONLY` |
| Áo vest | 30.000đ/cái | `LIST_PRICE_ONLY` |
| Váy thiết kế | 30.000đ/cái | `LIST_PRICE_ONLY` |

## 5. Vệ sinh, làm mới đồ da

| Dịch vụ | Giá | Quyền agent |
|---|---:|---|
| Áo da, váy da | 80.000–120.000đ/cái | `RANGE_ONLY_HUMAN_FINAL` |
| Túi xách da | 80.000–150.000đ/cái | `RANGE_ONLY_HUMAN_FINAL` |

## 6. Giặt khô

| Dịch vụ | Giá | Quyền agent |
|---|---:|---|
| Áo vest nam, nữ | 100.000–150.000đ/cái | `RANGE_ONLY_HUMAN_FINAL` |
| Áo ghi lê | 35.000–45.000đ/cái | `RANGE_ONLY_HUMAN_FINAL` |
| Áo dạ ngắn dưới 60cm | 75.000đ/cái | `LIST_PRICE_ONLY` |
| Áo dạ dài 60–80cm | 110.000đ/cái | `LIST_PRICE_ONLY` |
| Áo lông vũ, áo phao | 70.000–130.000đ/cái | `RANGE_ONLY_HUMAN_FINAL` |
| Váy dạ hội | 120.000đ/cái | `LIST_PRICE_ONLY` |
| Váy cưới | 400.000đ/cái | `LIST_PRICE_ONLY` |
| Áo/váy ngắn | 65.000đ/cái | `LIST_PRICE_ONLY` |
| Áo/váy dài | 80.000đ/cái | `LIST_PRICE_ONLY` |
| Áo dài trơn | 65.000đ/cái | `LIST_PRICE_ONLY` |
| Áo dài kiểu thêu, đính hạt… | 75.000–200.000đ/cái | `RANGE_ONLY_HUMAN_FINAL` |
| Áo dài truyền thống | 80.000–240.000đ/bộ | `RANGE_ONLY_HUMAN_FINAL` |
| Áo lông thú | 200.000–400.000đ/cái | `RANGE_ONLY_HUMAN_FINAL` |
| Áo khoác | 50.000–100.000đ/cái | `RANGE_ONLY_HUMAN_FINAL` |
| Áo sơ mi | 50.000đ/cái | `LIST_PRICE_ONLY` |
| Quần tây, kaki, jeans | 55.000đ/cái | `LIST_PRICE_ONLY` |
| Chân váy | 35.000–55.000đ/cái | `RANGE_ONLY_HUMAN_FINAL` |
| Khăn choàng | 75.000–150.000đ/cái | `RANGE_ONLY_HUMAN_FINAL` |
| Mũ | 35.000–120.000đ/cái | `RANGE_ONLY_HUMAN_FINAL` |

## 7. Vệ sinh, chăm sóc giày

| Dịch vụ | Giá | Quyền agent |
|---|---:|---|
| Giày thể thao các loại | 70.000đ/đôi | `LIST_PRICE_ONLY` |
| Giày trẻ em size dưới 35 | 30.000đ/đôi | `LIST_PRICE_ONLY` |
| Giày da trơn | 90.000–120.000đ/đôi | `RANGE_ONLY_HUMAN_FINAL` |
| Giày da lộn cao cấp | 150.000–250.000đ/đôi | `RANGE_ONLY_HUMAN_FINAL` |

## 8. Dịch vụ khác

| Dịch vụ | Giá | Quyền agent |
|---|---:|---|
| Giặt rèm thường | 35.000đ/kg | `LIST_PRICE_ONLY` |
| Giặt rèm tháo lắp | 45.000đ/kg | `LIST_PRICE_ONLY` |
| Vệ sinh sofa | 300.000–500.000đ/bộ | `RANGE_ONLY_HUMAN_FINAL` |
| Giặt thảm mềm | 30.000–50.000đ/kg | `RANGE_ONLY_HUMAN_FINAL` |
| Giặt thảm cứng | 100.000đ/m² | `LIST_PRICE_ONLY` |
| Giặt gấu bông | 20.000–200.000đ/con, tùy size | `RANGE_ONLY_HUMAN_FINAL` |
| Tẩy vết bẩn | 30.000–200.000đ, tùy vết; đơn vị tính do nhân viên xác nhận | `RANGE_ONLY_HUMAN_FINAL` |
| Tẩy ruột gối | 40.000–60.000đ/cái, tùy size | `RANGE_ONLY_HUMAN_FINAL` |

## 9. Chương trình ưu đãi đang hoạt động

- Từ **17/07/2026 đến hết 31/08/2026**.
- Giảm **30%** cho các dịch vụ giặt ướt thuộc phạm vi chương trình.
- Giảm **40%** cho các dịch vụ giặt khô thuộc phạm vi chương trình.
- Chi tiết phạm vi, công thức và quyền agent: `PROMOTION_2026_08.md`.
- Bản ghi hệ thống: `templates/promotions.csv` và `templates/promotion-service-rules.csv`.

## 10. Ngoài bảng giá

Các mục sau vẫn `HUMAN`:

- giá B2B towel/linen và hợp đồng định kỳ;
- express surcharge;
- phí giao nhận;
- discount khác ngoài chương trình ưu đãi đã cấu hình, refund hoặc credit;
- chọn mức cụ thể trong khoảng giá;
- món không khớp rõ một dòng dịch vụ;
- giá đã gồm/chưa gồm thuế.
