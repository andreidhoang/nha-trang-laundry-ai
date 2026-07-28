# Service Catalog & SLA v1.5

Trạng thái: **INTERNAL DRAFT — chưa phát hành cho khách**  
Cập nhật: 2026-07-27

Đây là nguồn tri thức có cấu trúc cho Sales & Concierge Agent. Agent chỉ được dùng các rule đánh dấu `AUTO`; mọi mục `HUMAN` phải chuyển người xác nhận.

## 1. Business identity

- Storefront brand: **Giặt Là Sạch Cộng**.
- Legal entity for contracts/invoices: **CÔNG TY TNHH A & T CARE**.
- Tax code: **4202059758**.
- Service address: 3A Lê Đại Hành, Phường Nha Trang, Khánh Hòa.
- Registered/displayed address: Số 3A Lê Đại Hành, Phường Nha Trang, Tỉnh Khánh Hòa, Việt Nam.
- Identity rule: use the storefront brand in normal customer conversation; use the exact legal name and tax code for invoices/contracts. Tax-authority status and legal representative have not yet been independently verified.
- Business hours: 08:00–20:00 hằng ngày.
- Closures: 06 ngày Tết Nguyên Đán, ngày 30/4, ngày 01/5 và 02 ngày phát sinh/năm. Agent chỉ xác nhận một ngày cụ thể sau khi đọc lịch đóng cửa được cập nhật cho năm đó.
- Customer phone/hotline: 0382 318 492; E.164: +84382318492.
- Zalo account: số 0382 318 492; chưa xác nhận là Zalo OA.
- Google Business Profile và Facebook Page: đã tồn tại; URL/quyền quản trị `PENDING`.

## 2. Standard wash-dry by weight

| Điều kiện | Giá | Quyền agent |
|---|---:|---|
| Tổng khối lượng <6kg | 25.000đ/kg | `AUTO-QUOTE-ESTIMATE`, chưa auto-confirm slot |
| Tổng khối lượng ≥6kg | 20.000đ/kg | `AUTO-QUOTE-ESTIMATE`, chưa auto-confirm slot |

Quy tắc:

- Giá cuối cùng dựa trên cân thực tế của cửa hàng.
- Với dịch vụ này, khối lượng thực tế dưới 1kg được tính tối thiểu 1kg, tương đương 25.000đ.
- Không suy rộng minimum 1kg sang dịch vụ tính kg khác cho đến khi chủ tiệm xác nhận.
- Không tự áp giá B2B, hotel linen, express, stain treatment hoặc đồ đặc biệt.
- Không được chia một đơn để né hoặc thay đổi tier giá.
- Với đơn ước tính gần 6kg, agent phải trình bày cả hai điều kiện; giá cuối cùng theo cân thực tế.
- Chưa được tự làm tròn khối lượng hoặc tiền cho đến khi cửa hàng chốt quy tắc cân/làm tròn.

### Công thức tier đã xác nhận

Với `w` là khối lượng thực tế theo kg:

```text
billable_kg = max(w, 1)
Nếu w < 6:  giá = billable_kg × 25.000đ
Nếu w >= 6: giá = w × 20.000đ
```

Ví dụ:

- 0,6kg = 25.000đ.
- 5,9kg = 147.500đ.
- 6kg = 120.000đ.
- 6,1kg = 122.000đ.
- 7kg = 140.000đ.
- 10kg = 200.000đ.

Rule tạo bước giảm giá tại ngưỡng 6kg. Agent phải áp đúng rule đã xác nhận, không tự làm mượt giá, đổi tier hoặc gợi ý khai sai khối lượng.

Toàn bộ bảng giá niêm yết còn lại đã được chủ tiệm xác nhận và nằm trong `PRICEBOOK_V1.md` cùng `templates/services-pricebook.csv`. Với giá theo khoảng, agent chỉ được nêu cả khoảng; nhân viên xác nhận mức cuối cùng.

### Ưu đãi 17/07–31/08/2026

- Giảm 30% cho các dịch vụ giặt ướt thuộc phạm vi chương trình.
- Giảm 40% cho các dịch vụ giặt khô thuộc phạm vi chương trình.
- Xét hiệu lực theo `accepted_at`; đơn được cửa hàng chấp nhận đến hết ngày 31/08/2026 được giữ mức ưu đãi.
- Giá trong pricebook là giá niêm yết trước ưu đãi. Agent tính và lưu riêng `list_service_subtotal`, `discount_rate`, `discount_amount` và `net_service_subtotal`.
- Agent chỉ tự áp với scope `AUTO_APPLY` trong `templates/promotion-service-rules.csv`. Dịch vụ mơ hồ, giá theo khoảng, ưu đãi cộng dồn hoặc bảng giá B2B phải chuyển người xác nhận.
- Phí giao nhận không phải tiền dịch vụ và không được giảm theo chương trình này.
- Nguồn đầy đủ: `PROMOTION_2026_08.md` và `templates/promotions.csv`.

## 3. Turnaround

### Standard wash-dry

- Phạm vi áp dụng: **giặt sấy quần áo thường**.
- Production turnaround commitment: hoàn tất trong vòng 8 giờ sau khi cửa hàng nhận, cân và chấp nhận đơn giặt sấy quần áo thường.
- Fastest possible: 2 giờ.
- `2 giờ` không phải guarantee; chỉ nhân viên được xác nhận sau khi kiểm tra công suất và phân loại.
- SLA clock đã xác nhận: bắt đầu khi cửa hàng đã nhận, cân và chấp nhận đơn; kết thúc khi đồ sẵn sàng để nhận tại cửa hàng.
- Thời gian giao tận nơi không nằm trong production SLA 8 giờ và phải có ETA riêng.
- Với đơn nhận gần giờ đóng cửa hoặc đi qua ngày đóng cửa, nhân viên phải đặt `promised_ready_at_store`; agent không tự cộng 8 giờ rồi cam kết.

### Special items

- Giày, rèm cửa, chăn và ga: thời gian trung bình 24–48 giờ.
- Agent được thông báo khoảng dự kiến 24–48 giờ nhưng không tự chọn một giờ giao cụ thể; nhân viên phải đặt `promised_ready_at_store` sau khi kiểm tra món, tải thực tế và hàng chờ.
- Gấu bông, túi xách, đồ da, topper, gối và các món đặc biệt khác: `HUMAN_ETA_REQUIRED` cho đến khi có rule riêng được chủ tiệm xác nhận.
- Loại sản phẩm, vật liệu, tình trạng và phương pháp xử lý phải được kiểm tra.
- Bảng giá giặt khô/hydrocarbon: đã xác nhận trong `PRICEBOOK_V1.md`; phân loại món và giá cuối cùng theo khoảng vẫn cần nhân viên kiểm tra.
- Agent không được cam kết chính xác “24 giờ” nếu chưa có nhân viên kiểm tra món.
- Bản ghi SLA có cấu trúc: `templates/service-sla.csv`.

## 4. Stains and bleaching

- Vết ố, mốc hoặc vết bẩn cứng đầu có thể cần xử lý/tẩy riêng sau bước giặt.
- Dịch vụ này tính phí riêng.
- Phải thông báo phí, rủi ro phai/ảnh hưởng vật liệu và nhận xác nhận của khách trước khi xử lý.
- Không bảo đảm loại bỏ 100% mọi vết bẩn.
- Agent không được tự thêm dịch vụ hoặc tự suy luận hóa chất phù hợp.

## 5. Giao nhận và Delivery SLA

- Có giao nhận tận nơi do người của cửa hàng trực tiếp thực hiện. Người/ca phụ trách cụ thể chưa chốt.
- Phương tiện đã xác nhận: đơn dưới 20kg đi xe máy; đơn từ đúng 20kg trở lên đi ô tô.
- Khoảng cách nên tính một chiều từ cửa hàng đến địa chỉ khách theo Google Maps.
- Owner rule đã xác nhận: khoảng cách ≤2km được miễn toàn bộ phí nhận và trả cho mọi đơn, không phụ thuộc khối lượng, phương tiện hoặc giá trị hóa đơn. Agent được áp phí `0đ` sau khi khoảng cách được xác minh; slot giao nhận vẫn cần kiểm tra capacity.
- Owner rule đã xác nhận: khoảng cách >2km đến 6km thu **10.000đ tổng cộng cho cả nhận + trả**. Agent được áp phí 10.000đ sau khi xác minh khoảng cách; slot vẫn cần kiểm tra capacity.
- Phương án kinh tế:
  - <20kg và ≤2km: xe máy, miễn phí toàn bộ nhận + trả;
  - <20kg và >2–6km: xe máy, 10.000đ tổng cho cả nhận + trả;
  - ≥20kg và ≤2km: ô tô, miễn phí toàn bộ nhận + trả;
  - ≥20kg và >2–6km: ô tô, 10.000đ tổng cho cả nhận + trả;
  - >6km: `delivery_fee_vnd = HUMAN_INPUT_REQUIRED`; nhân viên thỏa thuận tổng phí nhận + trả với khách rồi nhập vào đơn.
- Rule ≤6km và quy trình nhập phí thủ công >6km đều là `OWNER_CONFIRMED`. Agent không tự suy diễn phí cho zone >6km.
- Phí cố định chỉ áp dụng theo khung tuyến đã xác nhận; đơn chạy riêng, giao gấp hoặc ngoài tuyến báo chi phí thực tế và cần khách duyệt.
- Vì tiệm chỉ có hai nhân sự, miễn phí/phí thấp không được dùng để xác nhận một đơn lẻ nếu chưa kiểm tra giá trị đơn, phương tiện, tuyến ghép và capacity.
- Nguồn: `DELIVERY_POLICY_DRAFT.md` và `templates/delivery-zones.csv`.

- Giao theo giờ đã được nhân viên xác nhận.
- Nếu giao trễ hơn 2 giờ: service credit bằng 10% bill tiếp theo theo chính sách hiện tại.
- Production SLA 8 giờ của giặt sấy quần áo thường và delivery SLA là hai đồng hồ riêng.
- Agent phải ghi `accepted_at`, `promised_ready_at_store` và `ready_at_store`.
- Agent phải ghi `promised_delivery_at` và `delivered_at`; không tính trễ dựa trên text tự do.
- Credit chỉ được tạo sau khi hệ thống hoặc người xác minh.
- `PENDING`: thời hạn sử dụng credit, trần credit và cách xử lý khách không quay lại.

## 6. Response and complaint SLA

- First response target: 5–10 phút trong khung 08:00–20:00 vào ngày tiệm hoạt động.
- Ngoài giờ: agent có thể tiếp nhận 24/7 nhưng phải nói rõ đây là yêu cầu chờ nhân viên; chưa hứa thời điểm xác nhận nếu ngày đóng cửa cụ thể chưa có trong lịch hiện hành.
- Complaint acknowledgement: ngay khi nhận.
- Human review/initial resolution plan: trong vòng 24 giờ.
- Final resolution có thể dài hơn nếu cần kiểm tra; phải cập nhật khách thay vì im lặng.

## 7. Capacity guardrail

- Owner-confirmed current inventory: 2 máy giặt, 2 máy sấy, 1 máy giặt khô hydrocarbon, 1 máy giặt–sấy giày SUNMI A99, 1 cầu là hút chân không và 1 bộ bàn là nồi hơi; toàn bộ là thiết bị mới sắm, đang hoạt động 100% công suất/tải danh nghĩa.
- Owner-confirmed current workforce: 2 người, cùng lịch 08:00–20:00; có thể làm thêm khi đồ quá nhiều. Phân công công đoạn, năng lực thực đo và giới hạn overtime chưa xác nhận.
- Chủ tiệm xác nhận các model trên chứng từ là thiết bị tại tiệm: 01 máy giặt SPINZ SZHW-320 Pre 32kg + 01 máy giặt LG Giant C CWG27MDQRS.ASSQEML 13kg; 01 máy sấy SPINZ SZD-700 Pre 35kg + 01 máy sấy LG Giant C CDG27RUQES.ASSQEML 10,2kg; 01 máy giặt khô SPINZ SZDC-100H 10kg. Các máy mới, đang hoạt động và đạt 100% tải danh nghĩa; tem/serial, cycle time và uptime dài hạn chưa được đối chiếu/đo.
- Chủ tiệm xác nhận 01 máy giặt–sấy giày SUNMI A99 mới, đang hoạt động 100% công suất danh nghĩa; cycle time và tải theo số đôi vẫn `PENDING`.
- Chủ tiệm xác nhận cầu là hút chân không và bộ bàn là nồi hơi mới, đang hoạt động 100% công suất danh nghĩa; serial, thời gian xử lý và năng lực theo staff-minutes vẫn `PENDING`.
- `100% công suất/tải danh nghĩa` là trạng thái thiết bị theo xác nhận của chủ tiệm, không phải quyền để agent tự bán 100% số kg lý thuyết theo ngày.
- Owner-stated maximum: 300–400kg/ngày.
- Current auto-confirmable capacity in Stage 0/Shadow Mode: **0kg/ngày**; mọi slot phải được người phụ trách duyệt.
- Future Stage 3 candidate ceiling after the pilot gate passes: **240kg/ngày**, với reserve ứng viên **60kg/ngày**.
- Mọi B2B, express, special item hoặc order lớn vẫn là `HUMAN`; ngưỡng 240kg không được kích hoạt trước khi có cycle/load/downtime/labor logs và người phụ trách phê duyệt.
- Initial B2B pilot: tối đa 25kg hoặc một sorted batch, tùy điều kiện nào đến trước.
- 400kg/day: surge only, không đưa vào public promise cho đến khi cycle log xác minh.

## 8. Quality wording

Không dùng câu tuyệt đối “luôn sạch hoàn toàn, không hư hỏng, không phai màu”.

Wording cho agent:

> Cửa hàng xử lý theo loại đồ, tình trạng và hướng dẫn chăm sóc phù hợp; mục tiêu là sạch, thơm và đóng gói cẩn thận. Kết quả với vết ố/mốc và rủi ro của vật liệu, màu hoặc phụ kiện kém bền sẽ được báo trước khi xử lý đặc biệt.

Nếu nghi lỗi do cửa hàng:

- tạo incident;
- lưu order/evidence;
- chuyển người phụ trách;
- không tự nhận lỗi pháp lý hoặc tự đưa số tiền bồi thường.

Giặt lại:

- cửa hàng xem xét từng trường hợp dựa trên đơn, tình trạng và trao đổi với khách;
- agent chỉ tiếp nhận thông tin, tạo incident và chuyển người phụ trách;
- không tự hứa giặt lại miễn phí, refund hoặc bồi thường;
- rewash window và tiêu chí đủ điều kiện vẫn `PENDING`.

## 9. Thanh toán, hóa đơn và công nợ

- Chấp nhận tiền mặt hoặc chuyển khoản vào tài khoản của tiệm.
- Pháp nhân vận hành/xuất hóa đơn: **CÔNG TY TNHH A & T CARE**, mã số thuế **4202059758**. Chủ tiệm xác nhận loại hình công ty TNHH hai thành viên; ảnh biển chỉ thể hiện “CÔNG TY TNHH”.
- Thông tin tài khoản được phép công khai, trạng thái hoạt động thuế, người đại diện pháp luật, loại hóa đơn, quy trình xuất hóa đơn và giá đã gồm/chưa gồm thuế vẫn cần xác minh/cấu hình/phê duyệt.
- Công nợ B2B chỉ áp dụng sau khi người phụ trách duyệt kỳ hạn, hạn mức và điều kiện cho từng account.
- Agent không tự cấp công nợ, thay đổi điều khoản thanh toán hoặc gửi thông tin tài khoản chưa được phê duyệt.

## 10. Required intake

### Standard order

- name/contact;
- pickup/drop-off;
- one-way distance and requested pickup/return legs;
- estimated kg;
- dark/light/wet separation;
- deadline;
- special fabric/stain;
- requested delivery time.

### Handoff triggers

- express 2h;
- đồ da/dry-cleaning/special items;
- tẩy;
- B2B/hotel;
- mọi giao nhận cho đến khi fee scope được chốt;
- order ≥20kg;
- capacity warning;
- complaint/damage/loss;
- discount ngoài chương trình ưu đãi đã cấu hình, credit hoặc refund;
- unclear price or deadline.

## 11. Still missing

- Ngày cụ thể cho 06 ngày nghỉ Tết và 02 ngày nghỉ phát sinh của từng năm.
- Per-machine serial, operational status and measured cycle time; model-to-inventory mapping đã được chủ tiệm xác nhận.
- Role allocation, giới hạn overtime và measured labor capacity cho đội 2 người.
- Weight precision and rounding rule.
- Phạm vi áp dụng minimum 1kg cho các dịch vụ tính kg ngoài giặt sấy tiêu chuẩn.
- 20 delivery cost logs; cost/km, staff-minutes và contribution thực của các zone/phương tiện để nhân viên kiểm soát biên khi nhập phí >6km.
- Cost/kg and contribution.
- Electronic order/receipt method.
- Rewash window.
- Compensation valuation method.
- Storage fee and legally reviewed unclaimed-goods process.
- Trạng thái hoạt động thuế, người đại diện pháp luật, VAT/invoice workflow và B2B credit terms.
- Zalo OA status; URL/quyền quản trị Google Business và Facebook; WhatsApp/web status.
