# Policy Risk Review

Trạng thái: **INTERNAL — không xuất bản nguyên văn**  
Mục tiêu: loại bỏ mâu thuẫn trước khi customer-facing agent sử dụng. Đây không phải tư vấn pháp lý; bản công khai cần người am hiểu pháp luật Việt Nam duyệt.

| Điều khoản hiện tại | Rủi ro vận hành/trải nghiệm | Quyết định đề xuất |
|---|---|---|
| Chỉ giải quyết khi có hóa đơn | Khách có thể có order ID, chuyển khoản hoặc bằng chứng điện tử hợp lệ; từ chối tuyệt đối dễ gây tranh chấp | Chấp nhận hóa đơn giấy **hoặc** order code/biên nhận điện tử/bằng chứng thanh toán có thể đối chiếu |
| Sau 20 ngày tính lưu kho; sau 60 ngày thanh lý | Chưa có mức phí, thông báo và quy trình pháp lý; agent không được tự xử lý tài sản | Công bố fee schedule; nhắc nhiều lần; **tạm dừng điều khoản tự động thanh lý** đến khi legal review |
| Giặt cân ký không đồng kiểm nên không chịu trách nhiệm chất lượng và số lượng trước/sau | Mâu thuẫn với cam kết chất lượng và trách nhiệm mất/hỏng; miễn trừ quá rộng | Ghi nhận số túi + tổng kg; nói rõ không inventory từng món. Cửa hàng vẫn chịu trách nhiệm về lỗi xử lý/loss được chứng minh. Đồ giá trị cao dùng itemized service |
| Đồ ướt: khách phải tự tách tối/sáng | Hợp lý như cảnh báo nhưng không nên đẩy toàn bộ lỗi cho khách nếu nhân viên thấy rủi ro | Agent hỏi trước; nhân viên gắn risk flag và không xử lý chung khi thấy nguy cơ |
| Không chịu trách nhiệm nếu khách nhận tại cửa hàng sau 24h | Không rõ 24h tính từ đâu; delay pickup không tự động xóa trách nhiệm | Đổi thành: khách kiểm tra và báo lỗi nhìn thấy được sớm, mục tiêu trong 24h sau **nhận đồ**; case tiềm ẩn vẫn review |
| Không chịu trách nhiệm do loang/co giãn/chất liệu/màu/phụ kiện kém | Có thể hợp lý khi là inherent risk nhưng cần evidence và disclosure | Kiểm tra nhãn/tình trạng; chụp/ghi nhận rủi ro; xin xác nhận trước với đồ đặc biệt |
| “Sạch, thơm, không hư hỏng, không phai màu” | Cam kết tuyệt đối không thể bảo đảm với mọi vật liệu/vết bẩn | Dùng wording có điều kiện trong `SERVICE_CATALOG_AND_SLA_V1.md` |
| Bồi thường do hai bên thỏa thuận | Thiếu phương pháp định giá, escalation và thời hạn | Tạo incident + evidence + người duyệt; xây compensation matrix sau legal/accounting review |
| Trễ >2h giảm 10% bill tiếp theo | Khách một lần không hưởng lợi; chưa có expiry/cap | Giai đoạn đầu giữ nguyên nhưng phải ghi credit ledger; cân nhắc cho chọn credit bill hiện tại/hoàn phí sau khi biết economics |
| Khiếu nại xử lý trong 24h | “Xử lý xong” có thể bất khả thi nếu cần kiểm tra | Cam kết acknowledgement ngay và initial resolution plan ≤24h; cập nhật ETA cho final resolution |
| Miễn toàn bộ phí ≤2km; tổng phí 10.000đ cho cả nhận + trả vùng >2–6km; <20kg đi xe máy, ≥20kg đi ô tô | Hai zone gần là trợ giá có chủ đích; chưa biết contribution thật; zone >6km dễ gây lỗ nếu đặt phí cứng | Cho agent áp 0đ/10.000đ khi xác minh zone; >6km bắt buộc người phụ trách thỏa thuận rồi nhập `delivery_fee_vnd`; đo cost/km + staff-minutes theo từng phương tiện |
| Có công nợ B2B | Dễ bị hiểu là mọi account đều được nợ; chưa có hạn mức, kỳ hạn và overdue rule | Human approval theo account/hợp đồng; lưu approver, limit, terms và invoice status |

## Customer-facing draft principles

1. Nói rõ cái gì được cân/đếm và cái gì không.
2. Không dùng blanket waiver.
3. Mọi phí phát sinh cần khách duyệt trước.
4. Mọi risk đặc biệt cần được ghi nhận trước xử lý.
5. Mọi credit/refund/compensation cần traceable approval.
6. Agent không tự diễn giải luật hoặc tranh luận trách nhiệm.

## Must resolve before public launch

- [ ] Chốt rewash window.
- [x] Chốt minimum giặt sấy cân ký tiêu chuẩn: dưới 1kg tính 1kg.
- [ ] Chốt phạm vi minimum 1kg cho các dịch vụ tính kg khác và quy tắc cân/làm tròn từ 1kg trở lên.
- [ ] Chốt storage fee.
- [ ] Legal review quy trình đồ không nhận sau 60 ngày.
- [ ] Chốt compensation matrix.
- [ ] Chốt complaint evidence and escalation.
- [x] Chốt production SLA 8 giờ chỉ áp dụng giặt sấy quần áo thường, kết thúc khi đồ sẵn sàng tại tiệm và không bao gồm giao nhận.
- [x] Chốt ETA trung bình 24–48 giờ cho giày, rèm cửa, chăn và ga; giờ cụ thể do nhân viên xác nhận.
- [ ] Chốt cutoff/đơn qua giờ đóng cửa và credit áp dụng riêng cho production hay delivery.
- [x] Chốt miễn toàn bộ phí nhận + trả trong phạm vi ≤2km.
- [x] Chốt tổng phí nhận + trả 10.000đ cho khoảng cách >2km đến 6km.
- [x] Chốt zone >6km dùng `delivery_fee_vnd` do người phụ trách nhập theo từng tình huống và khách duyệt trước.
- [ ] Đo cost/km, staff-minutes và contribution để người phụ trách kiểm soát biên khi nhập phí >6km.
- [ ] Chốt B2B credit terms, VAT/invoice workflow và người duyệt.
- [ ] Viết policy ngắn, dễ hiểu và đặt tại quầy/biên nhận/landing page.
