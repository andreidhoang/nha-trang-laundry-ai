# Nha Trang Laundry AI Growth System

Ngày nghiên cứu: 2026-07-26  
Trạng thái: research brief v1 — dùng để kiểm chứng kinh doanh trước khi tự động hóa

## 1. Quyết định lãnh đạo

Đây không nên là dự án “làm chatbot cho tiệm giặt”. Đây là dự án xây một hệ thống tăng trưởng và vận hành có AI hỗ trợ, với một mục tiêu duy nhất:

> Tạo lợi nhuận lặp lại từ khách hàng phù hợp mà không làm vỡ công suất, chất lượng hoặc uy tín của tiệm.

Quyết định chiến lược:

1. **Wedge số 1:** khách sạn/nhà nghỉ độc lập 10–40 phòng và đơn vị quản lý 5–30 căn hộ ngắn hạn.
2. **Offer mở cửa:** “giặt dự phòng/overflow cho ngày cao điểm”, không ép khách bỏ nhà cung cấp hiện tại.
3. **Wedge số 2:** spa, massage, salon và gym có nhu cầu khăn đều đặn.
4. **Kênh nhanh:** quan hệ với lễ tân/chủ homestay + Google Business Profile + QR đặt giặt đa ngôn ngữ.
5. **Gia đình/sinh viên:** lấp công suất trống theo tuyến cố định; không ưu tiên giao lẻ xa và rẻ.
6. **Khách sạn/resort lớn:** chỉ tiếp cận sau 60–90 ngày có dữ liệu SLA, công suất, hóa đơn và case study.
7. **Không chạy quảng cáo trả phí ngay.** Trước tiên phải biết contribution margin, tỷ lệ tái mua và bán kính giao có lãi.
8. **Không biến Gateway OpenClaw cá nhân hiện tại thành bot công khai.** Customer-facing system phải nằm trên Gateway/OS user riêng, bị giới hạn quyền.

## 2. First principles: AI không sửa được một business model hỏng

Hệ thống kinh doanh gồm năm khâu:

`Demand → Offer → Distribution → Fulfillment → Retention`

AI có thể:

- nghiên cứu lead công khai;
- xếp hạng lead;
- trả lời FAQ và thu thập yêu cầu 24/7;
- soạn nội dung cá nhân hóa;
- giữ kỷ luật follow-up;
- ghi CRM;
- nhắc lịch và phát hiện khách có nguy cơ rời bỏ;
- tổng hợp KPI và đề xuất thử nghiệm.

AI không thể:

- tạo thêm công suất máy/sấy;
- đảm bảo đồ trắng, sạch dầu hoặc không thất lạc;
- tự biết cost/kg nếu gia đình không đo;
- tự lái xe lấy/giao;
- ký cam kết SLA, hoàn tiền hoặc xử lý tranh chấp một cách an toàn;
- hợp pháp hóa việc spam hoặc thu thập dữ liệu cá nhân bừa bãi.

Hai công thức phải đo:

`Contribution/order = doanh thu − hóa chất − điện/nước − lao động biến đổi − giao nhận − rewash/damage allowance − phí thanh toán`

`Route contribution/hour = tổng contribution của tuyến ÷ tổng giờ tuyến`

Chủ tiệm hiện ước tính chi phí xử lý giặt khoảng 30% doanh thu dịch vụ/đơn. Đây chỉ là `PLANNING_ESTIMATE`, chưa phải cost/kg hoặc contribution margin và chưa mặc định bao gồm giao nhận bằng xe máy/ô tô, thuế, bảo trì, khấu hao hay overhead.

Nếu hai số này âm, càng nhiều agent và càng nhiều đơn thì càng lỗ nhanh.

### Business facts supplied 2026-07-27

- Tên thương hiệu/cửa hàng: Giặt Là Sạch Cộng.
- Pháp nhân vận hành/xuất hóa đơn: **CÔNG TY TNHH A & T CARE**; mã số thuế **4202059758**. Chủ tiệm xác nhận đây là công ty TNHH hai thành viên; ảnh biển công ty chỉ thể hiện loại hình “CÔNG TY TNHH”.
- Địa chỉ cửa hàng: 3A Lê Đại Hành, Phường Nha Trang, Khánh Hòa.
- Địa chỉ ghi trên biển pháp nhân: Số 3A Lê Đại Hành, Phường Nha Trang, Tỉnh Khánh Hòa, Việt Nam.
- Giờ mở cửa: 08:00–20:00 hằng ngày; đóng 06 ngày Tết Nguyên Đán, ngày 30/4, ngày 01/5 và 02 ngày phát sinh/năm. Ngày cụ thể cần calendar theo từng năm.
- Điện thoại/hotline: 0382 318 492; E.164: +84382318492.
- Công suất chủ tiệm công bố: 300–400kg/ngày.
- Thiết bị hiện có theo chủ tiệm: 2 máy giặt, 2 máy sấy, 1 máy giặt khô dung môi hydrocarbon và 1 máy giặt–sấy giày SUNMI A99 đang hoạt động.
- Chủ tiệm xác nhận model trên chứng từ là thiết bị tại tiệm: máy giặt SPINZ SZHW-320 Pre 32kg/mẻ + LG Giant C CWG27MDQRS.ASSQEML 13kg/mẻ; máy sấy SPINZ SZD-700 Pre 35kg/mẻ + LG Giant C CDG27RUQES.ASSQEML 10,2kg/mẻ; máy giặt khô SPINZ SZDC-100H 10kg/mẻ. Chưa đối chiếu tem/serial, tình trạng kỹ thuật, cycle time và trạng thái vận hành thực.
- Chủ tiệm xác nhận máy giặt–sấy giày SUNMI A99 đang có tại tiệm và đang hoạt động; chưa đo cycle time, tải thực, uptime và công suất an toàn.
- Cầu là hút chân không và bộ bàn là nồi hơi đang có và đang hoạt động; năng lực thực chưa đo.
- Tổng nhân sự hiện tại: 2 người, cùng lịch 08:00–20:00 và có thể làm thêm khi quá tải; phân công công đoạn và giới hạn overtime còn cần xác nhận.
- Giặt sấy thường: <6kg là 25.000đ/kg; từ 6kg trở lên là 20.000đ/kg. Vì vậy 6kg = 120.000đ và 6,1kg = 122.000đ. Rule tạo bước giảm giá tại ngưỡng 6kg nhưng đã được chủ tiệm xác nhận.
- Giặt sấy cân ký dưới 1kg tính tối thiểu 1kg = 25.000đ; quy tắc làm tròn từ 1kg trở lên còn thiếu.
- Toàn bộ giá trên ảnh bảng giá đã được xác nhận đang áp dụng; nguồn chuẩn hóa là `PRICEBOOK_V1.md`.
- Chương trình ưu đãi được chủ tiệm xác nhận và thể hiện trên biển: từ 17/07/2026 đến hết 31/08/2026, giảm 30% dịch vụ giặt ướt và 40% dịch vụ giặt khô. Giá pricebook giữ nguyên là giá niêm yết trước ưu đãi; scope có cấu trúc nằm tại `PROMOTION_2026_08.md`.
- Giặt sấy quần áo thường: cam kết ≤8 giờ sau khi cửa hàng nhận, cân và chấp nhận đơn đến lúc đồ sẵn sàng tại tiệm, không bao gồm giao tận nơi; nhanh nhất 2 giờ khi capacity cho phép.
- Giày, rèm cửa, chăn và ga: trung bình 24–48 giờ; nhân viên xác nhận giờ cụ thể sau kiểm tra.
- Gấu bông, túi, đồ da và các món đặc biệt khác: nhân viên báo ETA theo từng trường hợp cho đến khi có rule riêng.
- Phản hồi: 5–10 phút; complaint initial handling trong 24 giờ.
- Giao trễ >2 giờ: credit 10% cho bill tiếp theo theo policy hiện tại.
- Giao nhận do người của cửa hàng thực hiện: đơn dưới 20kg đi xe máy, từ đúng 20kg trở lên đi ô tô. Owner rule: ≤2km miễn toàn bộ phí nhận + trả; >2–6km thu tổng cộng 10.000đ cho cả nhận + trả; >6km người phụ trách thỏa thuận với khách rồi nhập `delivery_fee_vnd`.
- Thanh toán bằng tiền mặt hoặc chuyển khoản; có xuất hóa đơn và có thể xem xét công nợ B2B. Đã có tên pháp nhân và mã số thuế; trạng thái hoạt động thuế, người đại diện pháp luật, giá đã gồm/chưa gồm thuế, invoice workflow và credit terms chi tiết còn thiếu.
- Zalo theo số hotline, Facebook Page và Google Business Profile đã tồn tại; URL/quyền quản trị và trạng thái Zalo OA còn cần xác minh.

Chủ tiệm xác nhận toàn bộ thiết bị mới sắm, đang hoạt động 100% công suất/tải danh nghĩa. Đây là trạng thái thiết bị theo mẻ, không phải công suất bán được theo ngày. Engineering guardrail hiện tại: **AI tự xác nhận 0kg/ngày trong Stage 0/Shadow Mode; mọi slot cần người duyệt**. Mức 240kg/ngày và reserve 60kg chỉ là ứng viên cho Stage 3 sau khi cycle/load/downtime/labor logs qua pilot gate. Mức 300–400kg/ngày không được public promise và luôn cần người duyệt cho đến khi được thực đo.

## 3. Sự thật thị trường đã kiểm chứng

### Nhu cầu

- Trước thay đổi địa giới năm 2025, Khánh Hòa báo cáo 1.186 cơ sở lưu trú, 66.185 phòng và hơn 10,8 triệu lượt khách lưu trú trong năm 2024. Đây là số toàn tỉnh, không phải riêng Nha Trang.
- Dịp 30/4–1/5/2025, nhiều cơ sở Nha Trang/Bãi Dài đạt trên 75% công suất; một số khách sạn được nêu đạt trên 90%.
- Trong Lễ hội Văn hóa–Du lịch biển Nha Trang tháng 6/2025, công suất lưu trú toàn khu vực trên 85%, trung tâm Nha Trang trên 90%, một số nơi 100%. Đây là bằng chứng trực tiếp về “capacity shock” cho chăn ga/khăn.
- Danh bạ hỗ trợ du khách của tỉnh hiện chứa khoảng 1.193 mục lưu trú cũ, gồm hàng trăm khách sạn, nhà nghỉ và condotel. Dùng làm nguồn prospecting rồi xác minh lại; không dùng như census hiện hành.
- Nha Trang University công bố quy mô khoảng 15.000 sinh viên. Đây là tín hiệu về cluster sinh viên, chưa phải bằng chứng họ sẵn sàng trả cho pickup laundry.
- Hàn Quốc và Trung Quốc là những thị trường du lịch lớn; Nga, Kazakhstan và các thị trường quốc tế khác cũng quan trọng. Khả năng phục vụ đa ngôn ngữ có giá trị thực.

### Cạnh tranh

- Thị trường B2C đã đông và khách đã quen với lời hứa “nhanh, rẻ, giao tận nơi”.
- Một nguồn tổng hợp địa phương nêu wash-only khoảng 20.000–30.000đ/kg và wash-dry khoảng 30.000–50.000đ/kg; đây chỉ là giá tham khảo, phải mystery-shop.
- WashInCloud công bố 60.000đ/≤3kg, 90.000đ/5kg, 120.000đ/7kg và 18.000đ/kg trên 7kg; đồng thời quảng cáo giao nhận, 2–5 giờ, giặt riêng và hỗ trợ VI/EN/RU/KO.
- Giặt Ủi 2H công bố cấu trúc giá tương tự, phí giao theo khoảng cách và xây landing page riêng cho từng khách sạn/khu vực.

Kết luận: **“rẻ + nhanh + có giao” không phải moat.** Moat có thể xây là:

- đúng giờ;
- đếm và đối soát rõ;
- tách đồ theo khách/tài sản;
- đóng gói có nhãn;
- không mất đồ;
- kiểm soát trắng, mùi, dầu;
- bằng chứng pickup/delivery;
- free rewash trong điều kiện rõ ràng;
- có năng lực cứu đơn vào ngày cao điểm.

## 4. ICP được xếp hạng

| Hạng | ICP | Vì sao đáng làm | Rủi ro |
|---|---|---|---|
| 1 | Khách sạn/nhà nghỉ độc lập 10–40 phòng | Nhu cầu lặp lại, dễ gặp owner/manager, đau khi cao điểm | SLA và linen QA cao |
| 2 | Quản lý 5–30 căn hộ/condotel | Turnover gấp, cần đóng gói theo căn | Mất/nhầm theo unit |
| 3 | Spa/massage/salon/gym | Khăn đều, quyết định nhanh, ít mùa vụ hơn | Dầu/mùi cần quy trình riêng |
| 4 | Khách du lịch qua lễ tân/host | Margin và urgency cao; partner lặp lại | Khách cá nhân ít tái mua |
| 5 | Gia đình/sinh viên gần tuyến | Có thể thành subscription | Nhạy giá; delivery ăn margin |
| 6 | Resort/khách sạn lớn | LTV lý thuyết cao | Procurement, công nợ, công suất, QA |

Không nhận bệnh viện/phòng khám ở giai đoạn đầu nếu chưa có năng lực kiểm soát lây nhiễm thực sự.

## 5. Hai offer nên ra thị trường

### Offer A — Peak-Day Linen Rescue

Khách hàng: mini-hotel, motel, homestay, condotel, spa.

- Không cần thay nhà cung cấp hiện tại.
- Một túi/một ngày paid pilot.
- Khung lấy sau checkout và trả trước check-in được xác nhận.
- Cân, đếm, chụp/ghi nhận và timestamp ở hai lần bàn giao.
- Túi niêm/nhãn màu theo account hoặc unit.
- Quy trình tiếp nhận rewash/late/damage có human review; chưa hứa free rewash khi policy chưa chốt.
- Sau pilot: emergency → overflow định kỳ → fixed route → primary supplier.

Thông điệp bán hàng không phải “bên em rẻ hơn”, mà là:

> Khi máy hoặc nhà cung cấp hiện tại quá tải, bên em là năng lực dự phòng có đối soát và SLA rõ ràng.

### Offer B — Guest Laundry Partner

Khách hàng: khách sạn, hostel, homestay, tour operator, quản lý căn hộ.

- QR riêng theo partner tại quầy lễ tân/phòng.
- Khách tự đặt bằng VI/EN; có thể mở rộng RU/KO sau khi kiểm thử.
- Tiệm lấy/giao ở lễ tân theo quy định của cơ sở.
- Referral fee/credit minh bạch với chủ cơ sở; không “hoa hồng kín” cho cá nhân.
- Mỗi QR có source code để đo doanh thu theo partner.

Đây là kênh tốt cho tiệm mới vì tạo đơn khách du lịch mà chưa phải gánh toàn bộ linen của khách sạn.

## 6. Funnel tìm và chuyển đổi khách

### B2B

1. **Discover:** danh bạ lưu trú của tỉnh, Google Maps, website công khai, đi thực địa theo tuyến.
2. **Enrich:** loại hình, số phòng/units ước lượng, khoảng cách, ngôn ngữ, dấu hiệu pain, contact công khai.
3. **Score:** fit với công suất và tuyến; không dùng thuộc tính nhạy cảm.
4. **Permission first:** gặp trực tiếp hoặc gọi để xin đúng người và xin phép gửi one-page pilot.
5. **Diagnose:** hiện giặt in-house hay outsource; peak kg/day; lỗi hay gặp; pickup/return window; invoice/payment.
6. **Paid pilot:** một tải nhỏ, có tiêu chí pass/fail.
7. **Review:** đối soát cost, SLA, rewash, feedback.
8. **Recurring:** fixed route hoặc backup agreement.
9. **Expand:** thêm ngày/tài sản/dịch vụ sau khi dữ liệu tốt.

Cadence sau khi đã được phép:

- Day 0: gửi one-page offer đã cá nhân hóa.
- Day 2: một follow-up trả lời câu hỏi/rủi ro cụ thể.
- Day 5–7: đề xuất một slot paid pilot.
- Sau đó dừng nếu không phản hồi; không chase vô hạn.

### B2C inbound

1. Google Business Profile, QR partner, Facebook/local group hoặc referral đưa khách vào.
2. AI hỏi tối đa: vị trí, loại đồ/kg ước lượng, dịch vụ, pickup window, ngôn ngữ.
3. Quote chuẩn chỉ khi nằm trong pricebook và capacity rule.
4. Nhân viên xác nhận các đơn ngoại lệ.
5. Sau giao: hỏi hài lòng một lần.
6. Nếu tích cực: xin review Google.
7. Xin opt-in riêng cho reminder/subscription; im lặng không phải đồng ý.

## 7. Đội agent đề xuất

Không cần tám bot “có vẻ ngầu”. Bắt đầu với bốn vai trò có trách nhiệm rõ:

### 1. Lead Scout

- Tìm account từ nguồn công khai.
- Chuẩn hóa và deduplicate.
- Chấm ICP score và nêu lý do.
- Soạn hypothesis về pain.
- **Không tự gửi tin.**

### 2. Sales & Concierge

- Trả lời inbound 24/7 trong phạm vi knowledge base.
- Thu thập thông tin đơn/lead.
- Soạn outreach/follow-up cho người duyệt ở giai đoạn đầu.
- Quote đơn chuẩn trong hard limits khi đã qua giai đoạn shadow.
- Handoff khi có khiếu nại, đồ đặc biệt, discount ngoài chương trình đã cấu hình hoặc SLA ngoại lệ.

### 3. Operations Coordinator

- Tạo order/checklist.
- Kiểm tra zone, capacity và pickup window.
- Gợi ý route, nhắc bàn giao và đối soát.
- Không tự hứa slot nếu dữ liệu công suất thiếu.

### 4. Retention & Revenue Analyst

- Theo dõi reorder interval và account health.
- Nhắc follow-up chỉ với người đã opt-in.
- Tạo báo cáo ngày/tuần.
- Phát hiện margin thấp, lateness, rewash và khách có nguy cơ churn.

**Một CRM là source of truth.** Memory của agent dùng cho SOP/knowledge, không dùng làm CRM.

## 8. OpenClaw: năng lực thật và trạng thái hiện tại

Kiểm tra read-only ngày 2026-07-26 cho thấy:

- Gateway chạy local loopback bằng Windows Scheduled Task.
- Memory và browser plugin đang bật.
- Security audit trả về 0 critical và 4 warning; cấu hình vẫn là personal-assistant posture có elevated tools/browser, `gateway.controlUi.allowInsecureAuth=true`, và Codex plugin chưa được pin version.
- Có khả năng multi-agent/session, web research, browser automation, document extraction, file work, cron/heartbeat và plugin/tool integration.
- Có 0 routing binding, 0 cron job và 0 paired node.
- Telegram được cấu hình nhưng đang disabled/disconnected; không có customer messaging channel đang hoạt động.
- Browser khả dụng nhưng chưa chạy.
- Webhooks hiện bị tắt.
- Zalo theo số hotline, Facebook Page và Google Business Profile đã tồn tại nhưng chưa được nối vào workflow kinh doanh; chưa xác nhận Zalo OA. WhatsApp/CRM cũng chưa nối.

### Capability map

| OpenClaw capability | Giá trị cho tiệm | Trạng thái |
|---|---|---|
| Multi-agent/session | Chia scout, concierge, ops, analyst | Có, chưa cấu hình cho business |
| Memory/knowledge | Pricebook, SOP, FAQ, policy | Có; phải tách khỏi CRM |
| Web search/browser | Research lead, verify listing, cập nhật GBP | Có; action bên ngoài cần duyệt |
| Cron/heartbeat | Follow-up opt-in, báo cáo, kiểm tra account risk | Có; hiện 0 job |
| Channels | Nhận/gửi chat | OpenClaw hỗ trợ nhiều kênh; hiện chưa nối customer channel |
| Zalo Bot | DM/group qua Bot API | Official plugin nhưng experimental; **không phải Zalo OA** |
| Zalo Personal | Tự động tài khoản cá nhân | Unofficial, có nguy cơ suspend/ban; không dùng production |
| Documents/media/voice | Đọc bảng giá, SOP, tạo asset, voice | Có ở mức tool/plugin |
| Nodes | Notification/camera/screen/location trên device | Có framework; hiện 0 node |
| Webhook/plugins | Nối CRM/forms/events | Có thể mở rộng; webhooks hiện off |
| Code/exec | Build integration và xử lý dữ liệu | Chỉ dành trusted owner/engineering, không cho public bot |

### Sự thật về “24/7”

Agent không “ngồi làm việc” như nhân viên. Nó chạy khi có:

- tin nhắn/event đến;
- cron/heartbeat đến hạn;
- webhook/API trigger;
- người giao nhiệm vụ.

24/7 chỉ tồn tại nếu máy/VPS luôn hoạt động, channel còn kết nối, model/API còn quota, queue có retry, dữ liệu được backup và có người nhận escalation. AI có thể là **lễ tân 24/7**; nó không biến pickup/giặt/giao thành 24/7.

## 9. Kiến trúc production

### Hai trust boundary bắt buộc

1. **Private Owner Gateway:** LEM/engineering, browser, exec, files, research, báo cáo.
2. **Public Customer Gateway:** agent nhắn tin bị sandbox, không exec/browser/nodes, chỉ được gọi các tool CRM/pricebook/order đã giới hạn.

OpenClaw nêu rõ một Gateway là một trusted-operator boundary và không phải hostile multi-tenant boundary. Không cho khách lạ chat trực tiếp với Gateway đang có quyền đọc file/chạy lệnh của chủ.

Luồng chuẩn:

`Customer channels → Public Gateway → bounded agents → CRM/order API`

`Private Owner Gateway → approval queue + analytics → humans`

Xem sơ đồ: `agent-architecture.html`.

Script permission-first, nurture, handoff và one-page pilot nằm trong `SALES_AND_NURTURE_PLAYBOOK.md`. Bảng đo business truth nằm trong `BUSINESS_TRUTH_INTAKE.md`. Agent rules nằm trong `SERVICE_CATALOG_AND_SLA_V1.md`; policy công khai đang được chuẩn hóa trong `CUSTOMER_SERVICE_POLICY_DRAFT.md` và chưa được phép publish.

## 10. Data model tối thiểu

### Accounts

- `account_id`
- `segment`
- `business_name`
- `public_source_url`
- `zone/distance`
- `rooms_or_units_estimate`
- `fit_score` + reason
- `stage`
- `owner`

### Contacts and consent

- `contact_id`
- `account_id`
- `name/role` nếu cần
- `channel`
- `contact_source`
- `consent_status`
- `consent_scope`
- `consent_timestamp/source`
- `do_not_contact`

### Interactions

- direction, time, channel, summary, next step, human approver.

### Pilots/orders

- service, kg/items, quoted price, pickup/delivery window, actual timestamps, status, route, revenue, variable cost.

### Quality/incidents

- rewash, late, lost/damaged, complaint, resolution, owner, cost.

Không lưu CCCD, danh bạ cá nhân, nội dung riêng tư hoặc dữ liệu không cần thiết.

## 11. Guardrails pháp lý và đạo đức

Đây không phải tư vấn pháp lý; trước khi mở rộng marketing automation cần người am hiểu pháp luật Việt Nam kiểm tra.

- Nghị định 91/2020/NĐ-CP yêu cầu consent trước cho quảng cáo qua tin nhắn/cuộc gọi; có cơ chế từ chối và giới hạn tần suất/thời gian. Một tin đăng ký quảng cáo đầu tiên có điều kiện không phải giấy phép để spam tiếp.
- Nghị định 13/2023/NĐ-CP quy định bảo vệ dữ liệu cá nhân; consent phải rõ và có thể chứng minh.
- Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15 có hiệu lực 01/01/2026. Điều 28 yêu cầu consent khi xử lý dữ liệu cá nhân cho quảng cáo; khách phải biết nội dung, phương thức, hình thức và tần suất, đồng thời phải có cơ chế opt-out và bằng chứng về căn cứ sử dụng dữ liệu.
- Dùng thông tin business công khai để nghiên cứu không đồng nghĩa được quyền nhắn hàng loạt.
- Không mua danh sách số điện thoại.
- Không dùng Zalo Personal automation trong production.
- Không giả review, không mạo danh người, không che giấu AI.
- Không tự hoàn tiền, thay đổi giá, ký SLA, cam kết bồi thường hoặc gửi chiến dịch lớn.
- Dừng ngay khi khách từ chối; lưu suppression list.

Mỗi consent marketing phải lưu:

- customer/channel identifier;
- timestamp và nguồn;
- đúng wording/version mà khách đã đồng ý;
- channel, mục đích và tần suất được phép;
- bằng chứng affirmative action;
- thời điểm rút consent và suppression status.

Theo Nghị định 91, absent another agreement, giới hạn thông thường là tối đa 3 SMS quảng cáo, 3 email quảng cáo và 1 cuộc gọi quảng cáo/24 giờ cho mỗi advertiser–recipient; SMS 07:00–22:00, call 08:00–17:00. Đây là trần pháp lý, **không phải cadence nên dùng**. Hệ thống của tiệm phải bảo thủ hơn nhiều.

## 12. Existing-solutions preflight

Không build CRM, form hay automation engine từ đầu ở giai đoạn này.

Khuyến nghị ban đầu:

- **Google Business Profile:** discovery, review, post, performance metrics.
- **Zalo OA:** kênh business chính thức; giai đoạn đầu human-operated/AI-assisted cho đến khi connector OA được xác minh. Không mua Growth/API trước khi chat volume chứng minh nhu cầu.
- **Meta Business Suite:** inbox Facebook/Instagram và native automated replies miễn phí.
- **WhatsApp Business:** phù hợp khách quốc tế nếu gia đình vận hành được.
- **Google Sheets + Forms:** source of truth chung trong 30 ngày đầu; đơn giản cho gia đình dùng điện thoại.
- **OpenClaw:** research, orchestration, knowledge, approval queue, reporting và bounded agent runtime.
- **HubSpot Free:** cân nhắc sau validation nếu B2B pipeline vượt khả năng Sheet.
- **Chatwoot:** cân nhắc khi nhiều channel/người xử lý làm handoff khó; không self-host ngay tuần đầu.
- **Activepieces Community:** deterministic webhook/retry/integration ở phase sau; đừng thêm chỉ vì “automation”.
- **ERPNext:** chỉ cân nhắc khi quote, order, invoice và B2B fulfillment đã ổn định.

Điểm integration chưa có:

- OpenClaw Zalo Bot không phải Zalo OA.
- Customer-facing OA cần connector chính thức, middleware hoặc custom plugin được security review.
- Google Sheets cần connector được cấp quyền tối thiểu.
- Thanh toán, dispatch và route optimization chưa có.

OpenClaw Zalo Bot và Zalo OA là hai sản phẩm khác nhau. Một bridge OA–Chatwoot mã nguồn mở mới xuất hiện (`diendh/zca-bridge`), nhưng còn trẻ; chỉ cân nhắc OA mode sau khi pin release, audit webhook/secrets/dependencies và pilot. Nếu không đạt audit, build một thin adapter thay vì tự build inbox/CRM.

Zalo OA hiện nêu rõ OA chỉ được gửi tin sau khi người dùng chủ động tương tác hoặc cho phép tương tác. Tài liệu “Tin Tư vấn” nêu OA OpenAPI có cửa sổ 7 ngày từ tương tác cuối; tin trong 48 giờ đầu có quota miễn phí theo chính sách hiện hành. Các giới hạn/phí có thể đổi, nên workflow phải đọc policy live khi triển khai, không hard-code từ research brief này.

## 13. Kế hoạch 30 ngày

### Ngày 1–3: Truth sprint

- Đo kg/day thực tế theo quần áo, khăn, linen.
- Ghi thời gian máy giặt, sấy, gấp/ủi, stain treatment.
- Tính cost/kg và cost/order.
- Ghi 20 đơn giao nhận: phương tiện, km, staff-minutes, chi phí tuyến, trợ giá và contribution.
- Xác định bán kính/tuyến có lãi.
- Xác nhận tiêu chí chọn mức trong khoảng giá, quy tắc cân/làm tròn, delivery fee và damage/rewash policy.
- Chạy 10 sample loads có checklist.

Exit gate: chưa đạt chất lượng và cost rõ thì chưa bán B2B.

### Ngày 4–7: Presence + CRM

- Kiểm tra quyền quản trị, thông tin, ảnh, review flow và tối ưu Google Business Profile hiện có.
- Chụp ảnh thật quy trình, đóng gói, bàn giao.
- Tạo một landing page/QR đơn giản, không cần app.
- Tạo CRM chung và consent fields.
- Tạo one-page “Peak-Day Linen Rescue”.
- Chọn một tuyến đầu tiên.

### Tuần 2: Prospecting có kiểm soát

- LEM/Lead Scout tạo 100 account trong bán kính.
- Người xác minh 30 account ưu tiên.
- Gia đình gặp/gọi đúng owner, manager hoặc housekeeping lead.
- Mục tiêu: 30 cuộc hội thoại thật, không phải 300 tin nhắn rác.

### Tuần 3: Paid pilots

- 5 paid pilots nhỏ.
- Ghi đầy đủ cost, thời gian, rewash, lateness, feedback.
- Không tăng volume nếu on-time/quality chưa đạt gate.

### Tuần 4: Retention + decision

- Cố gắng chuyển ít nhất 2 account thành recurring route.
- Xin review/case study từ khách hài lòng.
- Đánh giá ICP nào cho margin và repeat tốt nhất.
- Chỉ lúc này mới quyết định connector, cron và level tự động hóa tiếp.

Các con số 100/30/5/2 là mục tiêu validation nội bộ, không phải dự báo doanh thu.

## 14. Automation maturity

### Stage 0 — Manual truth

Con người làm sales/ops; AI nghiên cứu, soạn, tổng hợp.

### Stage 1 — Shadow mode

AI draft mọi reply/quote/follow-up; người duyệt và gửi. Đo hallucination, override và response time.

### Stage 2 — Assisted

AI tự trả FAQ, thu thập order data, tạo CRM/task; người xác nhận giá, slot và ngoại lệ.

### Stage 3 — Bounded autonomy

AI tự quote/confirm đơn chuẩn trong zone, capacity và price limits; chỉ chương trình ưu đãi đã cấu hình mới được tự áp, còn discount khác, complaint, refund và B2B SLA vẫn handoff.

Không nhảy thẳng Stage 3.

Customer-facing agent phải nói rõ: “Em là trợ lý tự động của Giặt Là Sạch Cộng.” Khi không chắc về giá, công suất, damage, deadline hoặc loại vải, nó phải nói “đang chờ nhân viên xác nhận”, không được đoán.

## 15. KPI scoreboard

### Fulfillment

- On-time pickup/delivery.
- Rewash rate.
- Lost/damaged items.
- Complaint acknowledgement/resolution time.
- Capacity utilization theo giờ.

### Unit economics

- Contribution/order.
- Contribution/kg.
- Delivery cost/order.
- Route contribution/hour.
- Revenue và contribution theo segment/source.

### Growth

- Qualified lead → permission.
- Permission → paid pilot.
- Paid pilot → recurring.
- 30-day B2C repeat rate.
- Active partner count.
- CAC theo kênh.

### Agent quality

- First-response time.
- Human handoff rate.
- Human override/correction rate.
- Quote error rate.
- Opt-out/compliance incident.
- AI cost per converted account.

Suggested validation gates, không phải benchmark thị trường:

- on-time ≥95%;
- rewash ≤2%;
- lost items = 0;
- contribution sau giao nhận >0;
- không có compliance incident.

## 16. Dữ liệu gia đình phải cung cấp trước build

Đã có tên thương hiệu, tên pháp nhân, mã số thuế, địa chỉ, giờ hoạt động tổng quát, hotline, full bảng giá niêm yết, minimum 1kg cho giặt sấy cân ký, stated capacity, model/tổng thiết bị chính, thiết bị ủi, lịch làm chung của hai nhân sự, phương tiện giao nhận, payment methods, khả năng xuất hóa đơn/công nợ và SLA cơ bản. Còn thiếu:

1. Ngày cụ thể cho 06 ngày nghỉ Tết và 02 ngày nghỉ phát sinh theo từng năm.
2. Đối chiếu tem/serial và đo cycle time, tải thực theo loại đồ, số mẻ/ngày, uptime/downtime của từng máy/công đoạn; trạng thái máy mới và hoạt động 100% tải danh nghĩa đã được chủ tiệm xác nhận.
3. Phân công công đoạn, giới hạn overtime và năng lực thực đo của hai nhân sự.
4. Độ chính xác cân/quy tắc làm tròn, phạm vi minimum 1kg, tiêu chí chọn mức trong khoảng giá, giá B2B và express fee.
5. Điện, nước, hóa chất, lao động, bao bì, giao nhận bằng xe máy/ô tô, rewash, thuế, bảo trì và khấu hao. Ước tính 30% hiện chưa đủ.
6. Cost/km, staff-minutes, tuyến/cutoff và contribution thực để kiểm soát biên khi nhập phí thủ công cho zone >6km.
7. Trạng thái hoạt động thuế, người đại diện pháp luật, giá đã gồm/chưa gồm thuế, invoice workflow, kỳ hạn/hạn mức/người duyệt công nợ B2B.
8. URL/quyền quản trị Google Business/Facebook, xác minh Zalo OA và trạng thái WhatsApp/web.
9. Số khách/đơn hiện tại, repeat rate, review và lỗi thường gặp.
10. Rewash, compensation, storage và unclaimed-goods policy đã legal review.

Không có mười dữ liệu này thì revenue forecast chỉ là tưởng tượng.

## 17. Nguồn chính

### Market

- [VOV — du lịch Khánh Hòa 2024 và cơ sở lưu trú](https://vov.vn/du-lich/chuyen-gia-quoc-te-danh-gia-cao-ve-du-lich-khanh-hoa-post1147628.vov)
- [Báo Khánh Hòa — công suất dịp 30/4–1/5/2025](https://news.baokhanhhoa.vn/tourism/202504/nha-trang-khanh-hoa-ready-for-peak-tourist-season-on-april-30-and-may-1-bdb4f09/)
- [Tuổi Trẻ — cao điểm Lễ hội biển 6/2025](https://tuoitre.vn/le-hoi-van-hoa-du-lich-bien-nha-trang-2025-thu-hut-hon-660-000-luot-du-khach-20250624191802039.htm)
- [Danh bạ cơ sở lưu trú Khánh Hòa](https://ttdhsdl.khanhhoa.gov.vn/Accommodation?pageType=list)
- [Bộ VHTTDL — du lịch Khánh Hòa 2025, cần đọc cùng caveat sáp nhập](https://bvhttdl.gov.vn/Pages/chi-tiet.aspx?url=/khanh-hoa-don-164-trieu-luot-khach-du-lich-trong-nam-2025-20251225091707833.htm)
- [Chính phủ — thay đổi đơn vị hành chính 2025](https://xaydungchinhsach.chinhphu.vn/chi-tiet-34-don-vi-hanh-chinh-cap-tinh-tu-12-6-2025-119250612141845533.htm)
- [Nha Trang University — quy mô sinh viên](https://www.ntu.edu.vn/Gioi-thieu/Gioi-thieu-chung/Nh%C3%A2ns%E1%BB%B1)
- [WashInCloud — giá và offer tự công bố](https://washincloud.com/)
- [Giặt Ủi 2H — giá, giao nhận và hotel landing pages tự công bố](https://giatui2h.com/khach-san-giao-nhan/)
- [LaundryAtlas — competition-density signal, cần xác minh](https://laundryatlas.com/vn/laundry-service/nha-trang)
- [Grab Việt Nam — bảng giá dịch vụ, dùng làm benchmark động cho giao nhận; giá thực tế phải đọc trên ứng dụng](https://www.grab.com/vn/en/blog/bang-thong-tin-cac-dich-vu-tren-ung-dung-grab/)

### Go-to-market and compliance

- [Google — manage Business Profile reviews](https://support.google.com/business/answer/3474050)
- [Google — Business Profile posts](https://support.google.com/business/answer/7342169)
- [Google — Business Profile performance](https://support.google.com/business/answer/9918094)
- [Chính phủ — Nghị định 91/2020/NĐ-CP](https://vanban.chinhphu.vn/default.aspx?docid=200773&pageid=27160)
- [Chính phủ — Nghị định 13/2023/NĐ-CP](https://vanban.chinhphu.vn/default.aspx?docid=207759&pageid=27160)

### OpenClaw

- [OpenClaw security/trust boundary](https://docs.openclaw.ai/gateway/security)
- [OpenClaw channels](https://docs.openclaw.ai/channels)
- [OpenClaw Zalo Bot status and limits](https://docs.openclaw.ai/channels/zalo)
- [OpenClaw Zalo Personal warning](https://docs.openclaw.ai/channels/zalouser)
- [OpenClaw multi-agent routing](https://docs.openclaw.ai/concepts/multi-agent)
- [OpenClaw cron](https://docs.openclaw.ai/automation/cron-jobs)
- [OpenClaw memory](https://docs.openclaw.ai/concepts/memory)
- [OpenClaw managed browser](https://docs.openclaw.ai/tools/browser)
- [OpenClaw nodes](https://docs.openclaw.ai/cli/nodes)

### Current 2026 privacy and channel references

- [Chính phủ — Luật Bảo vệ dữ liệu cá nhân 91/2025/QH15](https://chinhphu.vn/?pageid=27160&docid=214590)
- [Bộ Công an — hiệu lực và quyền của chủ thể dữ liệu](https://bocongan.gov.vn/chinh-sach-phap-luat/bai-viet/luat-bao-ve-du-lieu-ca-nhan-chinh-thuc-co-hieu-luc-thi-hanh-tu-ngay-01-01-2026-1767186124)
- [Zalo OA — chính sách gửi tin](https://oa.zalo.me/home/resources/news/thong-bao-chinh-sach-gui-tin-va-quy-dinh-phi-gui-tin_1433049880779375099)
- [Zalo OA — tin tư vấn](https://oa.zalo.me/home/documents/guides/tin-tu-van)
- [Zalo OA — broadcast](https://oa.zalo.me/home/documents/guides/huong-dan-gui-tin-broadcast_71)
- [Chatwoot — API channel](https://www.chatwoot.com/docs/product/channels/api/create-channel)
- [Activepieces Community](https://github.com/activepieces/activepieces)
- [ERPNext](https://github.com/frappe/erpnext)

## 18. Confidence labels

- **High confidence:** tourism creates large and spiky laundry demand; B2C convenience claims are already commoditized; current OpenClaw host is not ready/safe as a public customer bot.
- **Medium confidence:** 10–40-room properties, apartment managers and spas are the best first ICPs.
- **Hypothesis requiring field evidence:** exact outsourcing rate, accepted B2B price/kg, willingness to try backup supplier, best route, actual repeat rate and unit economics.
