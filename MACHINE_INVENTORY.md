# Machine Inventory — internal v0.6

Cập nhật: 2026-07-27  
Trạng thái: **INTERNAL — không đưa giá mua hoặc thông tin hợp đồng vào customer-facing agent/RAG**

Tài liệu này tách hai lớp sự thật:

1. `OWNER_CONFIRMED`: tổng số thiết bị chủ tiệm xác nhận đang có.
2. `CONTRACT_OBSERVED`: thông số nhìn thấy trên ảnh hợp đồng.

Ngày 2026-07-27, chủ tiệm xác nhận bộ ảnh trang 3–5 là toàn bộ thông số của các máy tại tiệm. Vì vậy model trên chứng từ đã được ánh xạ ở mức `OWNER_CONFIRMED` vào inventory hiện tại. Chủ tiệm đồng thời xác nhận toàn bộ thiết bị là máy mới sắm, đang hoạt động và có thể dùng ở **100% công suất/tải danh nghĩa**. Serial, tem tài sản, cycle time, tải thực tế theo từng loại đồ, uptime dài hạn và công suất toàn tiệm theo ngày vẫn phải đo/đối chiếu tại chỗ.

## 1. Tổng số thiết bị hiện có

| Nhóm thiết bị | Số lượng | Nguồn | Giới hạn bằng chứng |
|---|---:|---|---|
| Máy giặt | 2 | `OWNER_CONFIRMED` | Máy mới, đang hoạt động ở 100% tải danh nghĩa; serial, cycle time và uptime dài hạn chưa đo |
| Máy sấy | 2 | `OWNER_CONFIRMED` | Máy mới, đang hoạt động ở 100% tải danh nghĩa; serial, cycle time và uptime dài hạn chưa đo |
| Máy giặt khô hydrocarbon | 1 | `OWNER_CONFIRMED` | Máy mới, đang hoạt động ở 100% tải danh nghĩa; serial, cycle time và uptime dài hạn chưa đo |
| Máy giặt, sấy giày SUNMI A99 | 1 | `OWNER_CONFIRMED` | Máy mới, đang hoạt động 100% công suất danh nghĩa; chưa có tải danh nghĩa theo kg, serial và cycle time |
| Cầu là hút chân không | 1 | `OWNER_CONFIRMED` | Thiết bị mới, đang hoạt động 100% công suất danh nghĩa; chưa có serial và thời gian xử lý thực |
| Bộ bàn là nồi hơi | 1 | `OWNER_CONFIRMED` | Thiết bị mới, đang hoạt động 100% công suất danh nghĩa; chưa có model/serial rõ và thời gian xử lý thực |

## 2. Thiết bị và ánh xạ chứng từ

Nguồn: ảnh hợp đồng trang 3, 4 và 5 có tiêu đề **Công ty Cổ phần Vinplus**, do chủ tiệm cung cấp ngày 2026-07-27. Vinplus là đơn vị xuất hiện trên chứng từ thiết bị, không phải pháp nhân của tiệm. Phần ảnh hiện có không hiển thị thông tin bên mua.

| Evidence ID | Machine ID | Loại | Nhãn hiệu/model | SL | Tải danh nghĩa | Giá trị dòng chứng từ | Bảo hành | Trạng thái ánh xạ |
|---|---|---|---|---:|---:|---:|---:|---|
| DOC-WASH-01 | WASH-01 | Máy giặt | SPINZ SZHW-320 Pre | 1 | 32kg/mẻ | 237.390.000đ | 30 tháng | `OWNER_CONFIRMED_ACTIVE_100_PERCENT_NOMINAL`; serial/cycle time `PENDING` |
| DOC-WASH-02 | WASH-02 | Máy giặt | LG Giant C — CWG27MDQRS.ASSQEML | 1 | 13kg/mẻ | 34.990.000đ | 24 tháng | `OWNER_CONFIRMED_ACTIVE_100_PERCENT_NOMINAL`; serial/cycle time `PENDING` |
| DOC-DRY-01 | DRY-01 | Máy sấy | SPINZ SZD-700 Pre | 1 | 35kg/mẻ | 171.360.000đ | 30 tháng | `OWNER_CONFIRMED_ACTIVE_100_PERCENT_NOMINAL`; serial/cycle time `PENDING` |
| DOC-DRY-02 | DRY-02 | Máy sấy | LG Giant C — CDG27RUQES.ASSQEML | 1 | 10,2kg/mẻ | 24.990.000đ | 24 tháng | `OWNER_CONFIRMED_ACTIVE_100_PERCENT_NOMINAL`; serial/cycle time `PENDING` |
| DOC-DC-01 | DC-01 | Máy giặt khô hydrocarbon | SPINZ SZDC-100H | 1 | 10kg/mẻ | 209.520.000đ | 30 tháng | `OWNER_CONFIRMED_ACTIVE_100_PERCENT_NOMINAL`; serial/cycle time `PENDING` |
| DOC-SHOE-01 | SHOE-01 | Máy giặt, sấy giày | SUNMI A99 | 1 | Không ghi kg/mẻ | 39.065.000đ | 12 tháng | `OWNER_CONFIRMED_ACTIVE_100_PERCENT_NOMINAL`; serial/cycle time `PENDING` |
| DOC-IRON-TABLE-01 | IRON-TABLE-01 | Cầu là hút chân không | DH 1470 | 1 | Không áp dụng | 11.780.000đ | 12 tháng | `OWNER_CONFIRMED_ACTIVE_100_PERCENT_NOMINAL`; serial/thời gian xử lý `PENDING` |
| DOC-STEAM-IRON-01 | STEAM-IRON-01 | Bộ bàn là nồi hơi 3kW | Không thấy model rõ | 1 | Không áp dụng | 4.650.000đ | 6 tháng | `OWNER_CONFIRMED_ACTIVE_100_PERCENT_NOMINAL`; model/serial/thời gian xử lý `PENDING` |

Tổng giá trị của tám dòng thiết bị trên ảnh: **733.745.000đ**. Đây là tổng giá trị dòng chứng từ, chưa được coi là nguyên giá kế toán, giá trị còn lại, chi phí đã gồm thuế/lắp đặt hay cơ sở khấu hao cho đến khi kế toán đối chiếu hợp đồng và hóa đơn đầy đủ.

### Thông số kỹ thuật nhìn rõ

- `DOC-WASH-01`: tải 32kg/mẻ; kích thước lồng Ø774 × 600mm; độ ồn khi vắt được ghi 70dB; tốc độ giặt 45rpm; tốc độ vắt 750rpm; G-force 243; động cơ 3,5kW; biến tần 2,2kW; mức tiêu hao nước sạch được ghi 320 lít; kích thước máy 980 × 1130 × 1465mm; trọng lượng 560kg; hệ thống điện tiêu chuẩn CE; chứng từ ghi “Hãng TW Trung Quốc”; sản phẩm mới 100%.
- `DOC-WASH-02`: tải 13kg/mẻ; dung tích lồng 102,7 lít; kích thước lồng Ø560 × 419,3mm; độ ồn khi vắt ≤62dB; tốc độ giặt 40–50rpm; tốc độ vắt 1.200rpm; G-force 451; kích thước máy 686 × 983 × 767mm; trọng lượng 88,9kg; hệ thống điện tiêu chuẩn CE; sản phẩm mới 100%.
- `DOC-DRY-01`: tải 35kg/mẻ; dung tích lồng được ghi 700 lít; kích thước lồng Ø903 × 1030mm; động cơ 0,75kW; quạt 1,1kW; công suất đốt nóng 36kW; độ ồn ≤70dB; kích thước 978 × 1480 × 1840mm; trọng lượng 250kg; hệ thống điện tiêu chuẩn CE; chứng từ ghi “Hãng TW Trung Quốc”; sản phẩm mới 100%.
- `DOC-DRY-02`: tải 10,2kg/mẻ; dung tích lồng 207 lít; kích thước lồng Ø663 × 570,6mm; điện tiêu thụ được ghi 0,35kW; công suất làm nóng 5,4kW; kích thước máy 686 × 983 × 764mm; trọng lượng 59,4kg; điện áp 220–240V/50–60Hz/2,4A; hệ thống điện tiêu chuẩn CE; sản phẩm mới 100%.
- `DOC-DC-01`: tải 10kg/mẻ; kích thước lồng Ø532 × 405mm; độ ồn khi vắt ≤70dB; tốc độ giặt 50rpm; tốc độ vắt 999rpm; G-force 300; chương trình giặt được ghi `4CH`; động cơ giặt 0,8kW; động cơ bơm 0,37kW; tank chứa 170 lít; điện áp 220–240V/50Hz, 1 pha; kích thước 774 × 1090 × 1516mm (±5%); trọng lượng 450kg; hệ thống điện tiêu chuẩn CE; chứng từ ghi “Hãng TW Trung Quốc”; sản phẩm mới 100%.
- `DOC-SHOE-01`: kích thước 1200 × 650 × 1250mm; vật liệu inox 304; công suất 5kW; điện áp 220V/50Hz; trọng lượng 80kg; sản phẩm mới 100%.
- `DOC-IRON-TABLE-01`: kích thước bàn 1400 × 700mm; loại có tay gối; lực hút được ghi ≥150Pa; nguồn 220V; công suất motor 750W; độ ồn trên chứng từ được ghi ≤150dB; trọng lượng 70kg; xuất xứ được ghi “Liên doanh Việt-Hàn”; sản phẩm mới 100%.
- `DOC-STEAM-IRON-01`: kích thước 36 × 32 × 53cm; điện tiêu thụ được ghi 900W; điện áp đầu vào 220V; trọng lượng 15kg; công suất được ghi 3kW; xuất xứ Trung Quốc; sản phẩm mới 100%.

Các số trên là thông số được in trên chứng từ, chưa phải số đo vận hành thực tế.

Lưu ý dữ liệu:

- Một số tiêu đề trên chứng từ ghi “thể tích/dung tích lồng” nhưng giá trị thực tế lại là kích thước Ø × chiều sâu; giữ nguyên dữ liệu nguồn và không tự đổi thành lít.
- Dòng máy sấy LG dùng nhãn “kích thước lồng giặt” dù thiết bị là máy sấy.
- Máy sấy LG đồng thời ghi công suất làm nóng 5,4kW và nguồn 220–240V/2,4A; hai trường có dấu hiệu không tương thích nếu hiểu là cùng tải điện. Cần đối chiếu tem máy/catalogue trước khi tính điện năng.
- Mức ồn `≤150dB` của cầu là có dấu hiệu bất thường; giữ nguyên như ảnh nhưng không dùng cho quyết định an toàn/vận hành trước khi đối chiếu catalogue hoặc đo thực tế.
- Bộ bàn là nồi hơi đồng thời ghi `tiêu thụ điện năng 900W` và `công suất 3kW`; không cộng hoặc chọn một số làm tải điện cho đến khi kỹ thuật xác nhận ý nghĩa từng trường.
- Ảnh không cho thấy ngày bắt đầu bảo hành, vì vậy số tháng bảo hành trên chứng từ không chứng minh thiết bị hiện còn bảo hành.

## 3. Không được suy diễn

- Chủ tiệm đã xác nhận các model trên chứng từ là toàn bộ thiết bị tại tiệm; tất cả đều mới, đang hoạt động và đạt 100% công suất/tải danh nghĩa. Xác nhận này chưa thay thế việc đối chiếu tem/serial và đo vận hành từng tài sản.
- `100% công suất danh nghĩa` nghĩa là thiết bị được chủ tiệm xác nhận có thể vận hành đủ tải ghi trên chứng từ. Nó **không đồng nghĩa** công suất bán được theo ngày, uptime 100%, hiệu suất nhân sự 100% hoặc không cần reserve.
- SUNMI A99 được tính vào inventory đang vận hành 100% công suất danh nghĩa theo xác nhận của chủ tiệm; không suy diễn số đôi/mẻ, cycle time hoặc khả năng giữ SLA khi chưa có log thực tế.
- Cầu là hút chân không và bộ bàn là nồi hơi được tính vào inventory đang vận hành 100% công suất danh nghĩa; chưa suy diễn năng lực ủi/hoàn thiện khi chưa đo thời gian và staff-minutes.
- Không dùng tổng tải danh nghĩa để chứng minh công suất 300–400kg/ngày khi chưa có cycle time, changeover, downtime, tải thực và năng lực nhân sự.
- Planning load/reserve dùng để bảo vệ SLA là quyết định vận hành riêng, không phủ nhận trạng thái máy mới và khả năng chạy 100% tải danh nghĩa.
- Khung giờ mở cửa 08:00–20:00 không đồng nghĩa máy chạy liên tục 12 giờ.
- Hai nhân sự không đồng nghĩa cả hai cùng trực toàn bộ khung giờ.

## 4. Việc xác minh tiếp theo

1. Gắn và chụp nhãn tài sản: `WASH-01`, `WASH-02`, `DRY-01`, `DRY-02`, `DC-01`, `SHOE-01`, `IRON-TABLE-01`, `STEAM-IRON-01`.
2. Đối chiếu brand/model trên tem máy với model chủ tiệm đã xác nhận; ghi serial, năm lắp đặt và tình trạng hiện tại.
3. Đo ít nhất 10 mẻ: tải thực, wash/dry cycle, changeover, staff minutes, rewash và downtime.
4. Ghi một dòng cho mỗi công đoạn trong `templates/capacity-cycle-log.csv` để một `batch_id` có thể liên kết đúng máy giặt, máy sấy và công đoạn gấp/đóng gói khác nhau.
5. Dùng ngay các `machine_id` ổn định ở trên trong log; gắn cùng ID lên máy và bổ sung serial sau khi đối chiếu tại chỗ.
