# Business Truth Intake — operating profile v0.9

Cập nhật: 2026-07-27  
Quy ước: `ĐÃ XÁC NHẬN` là dữ liệu chủ tiệm cung cấp; `CHỨNG TỪ QUAN SÁT` là dữ liệu đọc được từ tài liệu nhưng chưa ánh xạ chắc chắn vào từng tài sản hiện tại; `CẦN ĐO` chưa được dùng để agent tự cam kết.

## 1. Cơ sở và tuyến

- Tên thương hiệu/cửa hàng: **Giặt Là Sạch Cộng — ĐÃ XÁC NHẬN**
- Pháp nhân vận hành/xuất hóa đơn: **CÔNG TY TNHH A & T CARE — ĐÃ XÁC NHẬN bởi chủ tiệm; QUAN SÁT TRÊN ẢNH BIỂN CÔNG TY**
- Mã số thuế: **4202059758 — ĐÃ XÁC NHẬN bởi chủ tiệm; QUAN SÁT TRÊN ẢNH BIỂN CÔNG TY**
- Loại hình: **công ty TNHH hai thành viên — ĐÃ XÁC NHẬN bởi chủ tiệm; biển công ty chỉ thể hiện “CÔNG TY TNHH”**
- Địa chỉ cửa hàng: **3A Lê Đại Hành, Phường Nha Trang, Khánh Hòa — ĐÃ XÁC NHẬN**
- Địa chỉ ghi trên biển pháp nhân: **Số 3A Lê Đại Hành, Phường Nha Trang, Tỉnh Khánh Hòa, Việt Nam — QUAN SÁT TRÊN ẢNH**
- Quy tắc nhận diện: **dùng “Giặt Là Sạch Cộng” khi giao tiếp thương hiệu; dùng tên pháp nhân và mã số thuế trên hợp đồng/hóa đơn/chứng từ cần danh tính pháp lý**
- Trạng thái tra cứu độc lập: **chưa đối chiếu cổng đăng ký doanh nghiệp/cơ quan thuế; người đại diện pháp luật và trạng thái hoạt động thuế CẦN XÁC MINH trước tích hợp hóa đơn**
- Bản ghi cấu hình có cấu trúc: `templates/business-profile.csv`
- Giờ mở cửa: **08:00–20:00 hằng ngày — ĐÃ XÁC NHẬN**
- Lịch đóng cửa: **06 ngày Tết Nguyên Đán, ngày 30/4, ngày 01/5 và 02 ngày phát sinh/năm — ĐÃ XÁC NHẬN về quy tắc; ngày cụ thể phải cập nhật theo từng năm**
- Điện thoại/hotline: **0382 318 492**; E.164: **+84382318492 — ĐÃ XÁC NHẬN**
- Người trực inbound:
- Phương tiện pickup/delivery: **đơn dưới 20kg đi xe máy; đơn từ đúng 20kg trở lên đi ô tô — ĐÃ XÁC NHẬN**
- Bán kính dự kiến: **có nhận giao tận nơi; trên 6km báo phí theo từng trường hợp**
- Các phường/khu ưu tiên:
- Khung pickup cố định:
- Khung delivery cố định:
- Rule khoảng cách hiện có: **≤2km miễn toàn bộ phí nhận + trả; >2km đến 6km thu tổng cộng 10.000đ cho cả nhận + trả — ĐÃ XÁC NHẬN**
- >6km: **nhân viên thỏa thuận với khách và nhập tổng phí nhận + trả vào `delivery_fee_vnd`; `HUMAN_INPUT_REQUIRED`**

## 2. Công suất

| Thiết bị/công đoạn | Số lượng | Kg/mẻ | Phút/mẻ | Mẻ/ngày an toàn | Bottleneck |
|---|---:|---:|---:|---:|---|
| Máy giặt | 2 — ĐÃ XÁC NHẬN; máy mới, hoạt động 100% tải danh nghĩa | SPINZ SZHW-320 Pre 32kg + LG Giant C CWG27MDQRS.ASSQEML 13kg; serial CẦN ĐỐI CHIẾU | CẦN ĐO | CẦN ĐO | CẦN ĐO |
| Máy sấy | 2 — ĐÃ XÁC NHẬN; máy mới, hoạt động 100% tải danh nghĩa | SPINZ SZD-700 Pre 35kg + LG Giant C CDG27RUQES.ASSQEML 10,2kg; serial CẦN ĐỐI CHIẾU | CẦN ĐO | CẦN ĐO | CẦN ĐO |
| Máy giặt khô dung môi hydrocarbon | 1 — ĐÃ XÁC NHẬN; máy mới, hoạt động 100% tải danh nghĩa | SPINZ SZDC-100H 10kg; serial CẦN ĐỐI CHIẾU | CẦN ĐO | CẦN ĐO | Xử lý đồ đặc biệt |
| Máy giặt, sấy giày SUNMI A99 | 1 — ĐÃ XÁC NHẬN; máy mới, hoạt động 100% công suất danh nghĩa | CẦN ĐO | CẦN ĐO | CẦN ĐO | Đồ đặc biệt |
| Cầu là hút chân không | 1 — ĐÃ XÁC NHẬN; thiết bị mới, hoạt động 100% công suất danh nghĩa | Không áp dụng | CẦN ĐO | CẦN ĐO | Ủi/hoàn thiện |
| Bộ bàn là nồi hơi | 1 — ĐÃ XÁC NHẬN; thiết bị mới, hoạt động 100% công suất danh nghĩa | Không áp dụng | CẦN ĐO | CẦN ĐO | Ủi/hoàn thiện |
| Ủi/gấp | | | | | |
| Stain treatment | | | | | |

- Công suất tổng chủ tiệm công bố: **300–400kg/ngày — ĐÃ XÁC NHẬN, CHƯA CÓ CYCLE LOG**
- Trạng thái thiết bị: **toàn bộ máy mới sắm, đang hoạt động và đạt 100% công suất/tải danh nghĩa — ĐÃ XÁC NHẬN bởi chủ tiệm**
- Công suất được AI tự xác nhận slot ở Stage 0/Shadow Mode hiện tại: **0kg/ngày; mọi slot do người phụ trách duyệt**
- Guardrail ứng viên cho Stage 3 sau khi qua pilot gate: **tối đa 240kg/ngày**
- Emergency reserve ứng viên sau validation: **60kg/ngày**
- Surge capacity 300–400kg/ngày: **human approval only**
- Công suất quần áo an toàn/ngày:
- Công suất towel an toàn/ngày:
- Công suất hotel linen an toàn/ngày:
- Tổng nhân sự hiện tại: **2 người — ĐÃ XÁC NHẬN**
- Lịch làm việc của cả hai: **08:00–20:00; có thể làm thêm khi đồ quá nhiều — ĐÃ XÁC NHẬN; phân công theo công đoạn và giới hạn overtime CẦN XÁC NHẬN/ĐO**

### Capacity engineering note

- Chủ tiệm đã xác nhận các model trên chứng từ là thiết bị tại tiệm. Tổng tải danh nghĩa mỗi lượt giặt đồng thời là 45kg và mỗi lượt sấy đồng thời là 45,2kg.
- Với xác nhận máy chạy 100% tải danh nghĩa, trần tải thiết bị lý thuyết cho một lượt đồng thời là 45kg giặt và 45,2kg sấy. Đây là tải theo mẻ, không phải kg/ngày.
- Ở planning load 80%, một lượt chạy đồng thời tương ứng khoảng 36kg giặt và 36,16kg sấy; 300–400kg cần khoảng 8,3–11,1 lượt lý thuyết trước khi tính cycle time, changeover, downtime và rewash.
- Planning load 80% là reserve vận hành để bảo vệ SLA, không phải đánh giá máy chỉ đạt 80%.
- Đã biết tổng số thiết bị, trạng thái máy mới/hoạt động 100% tải danh nghĩa, khung giờ làm của hai nhân sự và khả năng làm thêm khi quá tải. Chưa biết cycle time, changeover, drying time, tải thực tế theo loại đồ, downtime, phân công và giới hạn overtime nên 400kg chưa được dùng làm guaranteed SLA.
- Khung mở cửa 12 giờ không đồng nghĩa mọi máy chạy đủ 12 giờ hoặc cả hai nhân sự cùng trực đủ 12 giờ.
- Ảnh hợp đồng thể hiện 02 model máy giặt và 02 model máy sấy, mỗi dòng số lượng 01; chủ tiệm xác nhận đây là toàn bộ model tại tiệm, đều mới và hoạt động 100% tải danh nghĩa. Vẫn cần đối chiếu tem/serial và đo cycle/uptime thực. Chi tiết nằm trong `MACHINE_INVENTORY.md` và `templates/machine-master.csv`.

## 3. Unit economics

- Chủ tiệm ước tính **chi phí xử lý giặt ≈30% doanh thu dịch vụ/đơn**.
- Trạng thái: **PLANNING_ESTIMATE — CHƯA ĐO**. Không được suy ra lợi nhuận 70%.
- Chưa biết 30% đã bao gồm những cấu phần nào; không mặc định bao gồm giao nhận bằng xe máy/ô tô, phí thanh toán, thuế, rewash, bảo trì, khấu hao hoặc overhead.

| Chi phí | Đơn vị | Giá trị | Nguồn số liệu |
|---|---|---:|---|
| Điện | /kg hoặc /mẻ | | |
| Nước | /kg hoặc /mẻ | | |
| Hóa chất | /kg hoặc /mẻ | | |
| Lao động trực tiếp | /giờ | | |
| Túi/nhãn | /order | | |
| Pickup/delivery | /km hoặc /order | | |
| Payment fee | /order | | |
| Rewash/damage reserve | % doanh thu | | |
| Khấu hao/bảo trì | /kg | | |

- Minimum profitable order:
- Minimum profitable kg:
- Contribution mục tiêu/order:
- Route contribution/hour mục tiêu:

## 4. Pricebook và policy

- Toàn bộ bảng giá trong ảnh được chủ tiệm xác nhận đang áp dụng ngày 2026-07-27; nguồn chuẩn hóa: `PRICEBOOK_V1.md` và `templates/services-pricebook.csv`.
- Chương trình ưu đãi đang hoạt động: **17/07/2026–31/08/2026 (bao gồm cả ngày cuối), giảm 30% dịch vụ giặt ướt và 40% dịch vụ giặt khô — ĐÃ XÁC NHẬN bởi chủ tiệm và quan sát trên biển ưu đãi**.
- Giá trong pricebook là giá niêm yết trước ưu đãi; cấu hình chương trình nằm tại `PROMOTION_2026_08.md`, `templates/promotions.csv` và `templates/promotion-service-rules.csv`.
- Mốc xét ưu đãi của hệ thống: `accepted_at`; các dịch vụ biên chưa phân loại chắc chắn vẫn `HUMAN_CONFIRM`.
- Giá wash-only riêng: **không có dòng rõ trong ảnh; CẦN XÁC NHẬN nếu cửa hàng có bán**
- Giá giặt sấy thường <6kg: **25.000đ/kg — ĐÃ XÁC NHẬN**
- Giá giặt sấy thường từ 6kg trở lên: **20.000đ/kg — ĐÃ XÁC NHẬN**
- Giá đúng 6kg: **120.000đ**; 122.000đ tương ứng **6,1kg**.
- Công thức đã chốt: `bill = max(actual_kg, 1) × 25.000đ` khi `actual_kg < 6`; `bill = actual_kg × 20.000đ` khi `actual_kg >= 6`.
- Pricing cliff là hệ quả của rule đã xác nhận: **5,9kg = 147.500đ nhưng 6kg = 120.000đ**. Agent không được tự làm mượt giá hoặc chuyển tier.
- Giá chăn/ga/gối, ủi, đồ da, giặt khô, giày và dịch vụ khác: **ĐÃ XÁC NHẬN theo `PRICEBOOK_V1.md`**
- Giá towel/linen B2B: **CẦN CHỐT theo account/khối lượng**
- Express surcharge: **CẦN CHỐT**
- Delivery fee theo zone: **≤2km = 0đ; >2–6km = 10.000đ tổng cho cả nhận + trả; >6km do nhân viên thỏa thuận rồi nhập `delivery_fee_vnd` — ĐÃ XÁC NHẬN**
- Minimum bill giặt sấy cân ký tiêu chuẩn: **dưới 1kg tính 1kg = 25.000đ — ĐÃ XÁC NHẬN**
- Phạm vi áp dụng minimum 1kg cho các dịch vụ tính kg khác: **CẦN XÁC NHẬN**
- Độ chính xác cân và quy tắc làm tròn tiền/khối lượng: **CẦN XÁC NHẬN**
- Turnaround giặt sấy quần áo thường: **≤8 giờ từ lúc cửa hàng nhận, cân và chấp nhận đơn đến khi đồ sẵn sàng tại tiệm; không bao gồm giao tận nơi — ĐÃ XÁC NHẬN**
- Turnaround giặt cân nhanh nhất: **2 giờ, phải kiểm tra capacity; không auto-promise**
- Turnaround giày, rèm cửa, chăn và ga: **trung bình 24–48 giờ; nhân viên xác nhận `promised_ready_at_store` theo món, tải thực tế và hàng chờ — ĐÃ XÁC NHẬN**
- Turnaround gấu bông, túi xách, đồ da, topper, gối và các món đặc biệt khác: **HUMAN_ETA_REQUIRED cho đến khi chủ tiệm chốt rule riêng**
- Tẩy vết ố/mốc/cứng đầu: **dịch vụ riêng, tính phí; phải được khách duyệt trước**
- Cam kết giao đúng giờ: **trễ >2 giờ so với giờ đã xác nhận → credit 10% bill tiếp theo**
- Rewash policy hiện tại: **nhân viên xem xét và làm việc với khách theo từng trường hợp; `HUMAN` — chưa phải cam kết giặt lại miễn phí; điều kiện và thời hạn vẫn CẦN CHỐT**
- Lost/damage policy: **nếu xác định do cửa hàng, hai bên thỏa thuận; CẦN chuẩn hóa cách định giá/escalation**
- Loại đồ từ chối:
- Hóa chất/bleach policy: **không tẩy tự động; cần khách duyệt phí và rủi ro**
- Thanh toán: **tiền mặt hoặc chuyển khoản vào tài khoản của tiệm — ĐÃ XÁC NHẬN**
- Pháp nhân: **CÔNG TY TNHH A & T CARE; MST 4202059758 — ĐÃ XÁC NHẬN bởi chủ tiệm và quan sát trên ảnh biển công ty**
- Loại hình hai thành viên: **ĐÃ XÁC NHẬN bởi chủ tiệm; chưa được ảnh biển hoặc tra cứu độc lập chứng minh**
- Khả năng xuất hóa đơn: **có — ĐÃ XÁC NHẬN; quy trình và trạng thái giá đã gồm/chưa gồm thuế CẦN CHỐT**
- Công nợ B2B: **có hỗ trợ sau phê duyệt; kỳ hạn, hạn mức, điều kiện, người duyệt và xử lý quá hạn CẦN CHỐT**

### Service response and complaints

- Phản hồi mục tiêu: **5–10 phút trong khung 08:00–20:00 vào ngày tiệm hoạt động**
- Tiếp nhận và đưa phương án xử lý khiếu nại: **trong 24 giờ**
- Bồi thường/refund/discount ngoài chương trình ưu đãi đã cấu hình: **human approval only**

## 5. Current channels and proof

- Google Business Profile: **đã có — ĐÃ XÁC NHẬN; URL/quyền quản trị CẦN CUNG CẤP**
- Zalo tài khoản theo số hotline: **0382 318 492 — ĐÃ XÁC NHẬN; chưa xác nhận là Zalo OA**
- Facebook Page: **đã có — ĐÃ XÁC NHẬN; URL/quyền quản trị CẦN CUNG CẤP**
- WhatsApp Business:
- Website/landing page:
- Số review và rating hiện tại:
- Ảnh quy trình có sẵn:
- Khách/đơn trung bình mỗi ngày:
- Tỷ lệ khách quay lại ước tính:
- Ba lỗi/khiếu nại phổ biến:

## 6. Chính sách hiện tại do chủ tiệm cung cấp

### Trách nhiệm khách

1. Giữ hóa đơn sau khi nhận đồ.
2. Lấy đồ trong 20 ngày; sau đó tính phí lưu kho; sau 60 ngày hiện có điều khoản thanh lý.
3. Giặt cân ký không đồng kiểm từng món.
4. Đồ ướt cần tách tối/sáng để giảm rủi ro lem màu.

### Trách nhiệm/miễn trừ cửa hàng đang dùng

1. Điều khoản hiện tại nêu cửa hàng không chịu trách nhiệm khi khách nhận sau 24 giờ hoặc khi hư hại do đặc tính co giãn, loang màu, chất liệu/màu/phụ kiện kém chất lượng.
2. Có đồng kiểm khi giao/nhận với khách, nhưng điều này đang mâu thuẫn với mục giặt cân ký không đồng kiểm.
3. Đảm bảo đồ sạch, thơm và đóng gói cẩn thận.

Các điều khoản trên đã được chuyển sang `POLICY_RISK_REVIEW.md`; chưa được đưa nguyên văn vào customer-facing agent.

## 7. Pilot gate

Chỉ mở paid B2B pilot khi:

- [x] Đã ghi tên pháp nhân và mã số thuế từ xác nhận chủ tiệm + ảnh biển công ty.
- [ ] Đã xác minh trạng thái hoạt động thuế, người đại diện pháp luật, giá đã gồm/chưa gồm thuế và invoice workflow.
- [ ] Đã chốt kỳ hạn, hạn mức, người duyệt và xử lý quá hạn cho công nợ B2B.
- [ ] Đã chạy 10 sample load có timestamp.
- [ ] Đã ghi đủ 20 đơn giao nhận theo `templates/delivery-cost-log.csv`.
- [ ] Biết cost/kg và delivery cost/order.
- [ ] Có item-count/handoff checklist.
- [ ] Có tách túi/nhãn account.
- [ ] Có rewash/loss/damage policy.
- [ ] Có người xác nhận capacity.
- [ ] Có phương án khi máy hỏng/mất điện/quá tải.
- [ ] Có consent và suppression fields trong CRM.
