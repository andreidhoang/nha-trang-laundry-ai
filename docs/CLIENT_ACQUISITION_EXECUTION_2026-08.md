# Kế hoạch tìm khách — bản thực thi

**Ngày:** 2026-08-18 · **Cho:** chủ tiệm · **Trạng thái:** đề xuất, chưa có quyết định nào được ký
**Quan hệ với `RESEARCH_BRIEF.md`:** đây **không** phải bản viết lại. Brief ngày 2026-07-26 vẫn đúng
về khung. Tài liệu này là phần *chênh lệch*: những gì kiểm chứng lại hôm nay đã thay đổi, và việc
phải làm trong 11 ngày tới.

---

## 0. Một câu

Nút thắt không phải là thiếu AI đi tìm khách. Nút thắt là **hệ thống hiện chưa ghi nhận được một
khách hàng nào, từ bất kỳ nguồn nào** — trong khi tiệm đang vô hình trên đúng những kênh mà các tiệm
hàng xóm đã có mặt, và văn phòng của xưởng giặt công nghiệp lớn nhất tỉnh nằm **cùng con đường**.

---

## 1. Bốn sự thật kiểm chứng hôm nay, mỗi cái đổi một phần chiến lược

### 1.1 Hệ thống ghi nhận được 0 khách hàng

Đo ngày 2026-08-18, chứng minh được từ bốn file:

- `CreateOrderCommand` bắt buộc có `bound_contact_id` (`packages/db/.../orders.py:43`).
- Nơi duy nhất tạo ra contact binding là `ChannelBindingRepository.resolve_or_create`
  (`.../channel.py:112`), khoá theo `(provider, provider_user_ref)` — tức phải có danh tính từ một
  kênh nhắn tin.
- Cột `provider` chỉ nhận `ZALO_OA`, `TELEGRAM_SANDBOX`, `FACEBOOK_MESSENGER`
  (`migrations/0020_channel_envelope.sql`). Không có giá trị nào cho quầy.
- Chưa kênh nào được nối (`FEATURE_PUBLIC_CHANNELS_ENABLED = "false"`, hai contract test khẳng định;
  `DEC-005` ghi rõ chưa có bot token Telegram và chưa nộp hồ sơ Zalo OA).

Nghĩa là: **khách nhắn tin thì không có chỗ để nhắn; khách đi bộ vào thì không tạo được danh tính**
(`DEC-013`). Mọi lead sinh ra hôm nay đều rơi xuống giấy.

> `DEC-013` không phải lỗi giao diện. Nó là **ràng buộc số một của tăng trưởng.**

### 1.2 Tên thương hiệu không dẫn về tiệm này

- "Giặt Là Sạch Cộng" là **chuỗi nhượng quyền toàn quốc**, trụ sở Hà Nội (`giatlasachcong.com`),
  công bố **500+ tiệm tại 48 tỉnh**, và đang bán nhượng quyền (form hỏi vốn 200 triệu – trên 800
  triệu).
- **Danh sách tiệm công khai của chuỗi không có tiệm nào ở Khánh Hòa.**
- `CÔNG TY TNHH A & T CARE`, MST `4202059758`, 3A Lê Đại Hành — **có thật**, tra được trên cổng
  đăng ký/thuế công khai.
- `BUSINESS_TRUTH_INTAKE.md` ghi tên thương hiệu là `ĐÃ XÁC NHẬN` nhưng **không ghi quan hệ nhượng
  quyền ở đâu cả**, và trong repo không có hợp đồng, licence hay territory grant nào.

**Chưa xác minh, và không giả định:** tiệm có hợp đồng nhượng quyền hay không. Đó là dữ kiện chỉ chủ
tiệm có. → `DEC-017`.

Hệ quả bán hàng, độc lập với câu trả lời: khách tra tên thương hiệu sẽ ra công ty ở Hà Nội và hàng
trăm tiệm khác. Quản lý khách sạn tra tên để kiểm tra đối tác sẽ đọc trúng trang **bán nhượng
quyền**. Nên nhận diện của tiệm phải là **địa điểm và bằng chứng**, không phải tên chuỗi.

### 1.3 Cạnh tranh ở ngay sát vách, và lớn hơn nhiều

| Đối thủ | Vị trí | Quy mô/điểm mạnh công bố |
|---|---|---|
| **Việt Khánh Laundry (VIKHACO)** | **Văn phòng 11A Lê Đại Hành** — cùng đường; **xưởng ở Cụm CN Diên Phú, Diên Khánh** | Giặt công nghiệp, **24 tấn/ngày**, **7 xe tải 800kg–3,5 tấn**. Trưng logo The Westin, Four Points, Sea Soul, Aquamarine, Costa Bella, La Vague, Annova |
| Giặt Ủi 2H | 8 Nguyễn Thị Minh Khai | **40+ khách sạn** liệt kê ở 0,2–2,4km; giặt sấy 2–4 giờ; nhận đồ trong 30 phút; VI/EN/RU/KO |
| WashInCloud | Nha Trang | Cùng bảng giá với 2H; **trả hoa hồng 25% doanh thu cho khách sạn giới thiệu**, kích hoạt bằng QR, ký hợp đồng trong 24 giờ |
| Smile Laundry | **85/7 Lê Đại Hành** — cùng đường | 30.000đ/kg, có giao nhận |
| Thủy Việt Group | Vĩnh Phương | 7 tấn/ngày |
| Five Star Laundry | Cam Ranh + Phú Quốc | Nhắm resort 4–5 sao |
| VSTAR / Linen Supply | CN 03 Mai Xuân Thưởng | Roll-up toàn quốc; bán cho cả gym và spa |

Hai điều chỉnh bắt buộc với `RESEARCH_BRIEF.md`:

1. **"Offer B — QR đối tác lễ tân" đã có người làm rồi.** WashInCloud trả **25% doanh thu**. Kênh
   này không còn là ý tưởng mới; nó là một cuộc đấu hoa hồng. Trước khi hứa gì với lễ tân, phải biết
   25% có thật ngoài thực địa không.
2. **"Peak-Day Linen Rescue" cần đọc lại.** Nhà cung cấp hiện tại của một khách sạn lớn nhiều khả
   năng là VIKHACO với **24 tấn/ngày** — họ không "quá tải" vì một kỳ nghỉ. Offer overflow chỉ có
   nghĩa với **cơ sở nhỏ chưa có nhà cung cấp công nghiệp**, tức đúng phân khúc 10–40 phòng và
   5–30 căn hộ, chứ không phải khách sạn lớn.

**Và một chi tiết có lợi cho tiệm, phải đọc đúng:** hàng xóm ở 11A Lê Đại Hành là **văn phòng** của
VIKHACO, không phải xưởng. Xưởng giặt nằm ở **Cụm công nghiệp Diên Phú, Diên Khánh** — ngoài thành
phố. Nghĩa là một đơn nhỏ của khách sạn trong trung tâm phải đi xe tải ra Diên Khánh rồi quay về.
**Một tiệm cách 1,2km với cam kết 8 giờ có lợi thế tốc độ và linh hoạt thật sự ở khối lượng nhỏ** —
đó chính là khe hở mà cơ sở 10–40 phòng rơi vào: quá nhỏ để xưởng công nghiệp ưu tiên, quá lớn để tự
giặt.

**Tiệm không xuất hiện trong bất kỳ danh sách "top 10/20 tiệm giặt ủi Nha Trang" nào** — trong khi
ít nhất bảy đối thủ có mặt. Đây là lớp SEO địa phương thật sự của thị trường này.

### 1.4 Lợi thế thật sự của tiệm không phải giặt cân ký

Giặt sấy cân ký là hàng hoá phổ thông: giá thị trường 25.000–30.000đ/kg, ai cũng giao nhận, 2H làm
2–4 giờ. Tiệm không thắng ở đó.

Nhưng tiệm có **máy giặt khô dung môi hydrocarbon (SPINZ SZDC-100H)**, **máy giặt–sấy giày SUNMI
A99**, **cầu là hút chân không** và **bàn là nồi hơi**. So sánh giá công bố với Giặt Ủi 2H:

| Món | Giá tiệm (niêm yết) | Giặt Ủi 2H công bố | Chênh |
|---|---|---|---|
| Áo da / váy da | **80.000–120.000đ** | 200.000–400.000đ (áo khoác da) | rẻ hơn ~2–3 lần |
| Túi xách da | **80.000–150.000đ** | không thấy niêm yết túi da | — |
| Giày da trơn | **90.000–120.000đ** | 150.000–250.000đ | rẻ hơn |
| Áo vest giặt khô | 100.000–150.000đ → **60.000–90.000đ sau ưu đãi 40%** | 80.000–150.000đ | rẻ hơn |
| Giày thể thao | 70.000đ → **49.000đ sau ưu đãi 30%** | 60.000–100.000đ | rẻ hơn |

**Lợi thế đồ da đứng vững trên giá niêm yết, không cần ưu đãi** — nên nó không hết hạn ngày 31/8.

`templates/promotion-service-rules.csv` ghi rõ: `category dry_cleaning` giảm 40% `CONFIRMED`;
`category shoes` giảm 30% `CONFIRMED`; **`category leather` là `UNCLEAR / HUMAN_CONFIRM`** — "làm
mới đồ da không mặc định là giặt khô". Không được quảng cáo đồ da giảm 40% cho tới khi chủ tiệm chốt.

> Kết luận định vị: tiệm đang tự quảng cáo trên đúng trục mình bị hàng hoá hoá, và **bỏ quên trục
> mình rẻ hơn đối thủ 2–3 lần và có máy móc thật**.
>
> *Cảnh báo trung thực:* giá của 2H là khoảng giá trên trang marketing của họ; "vệ sinh làm mới đồ
> da" của tiệm chưa chắc là cùng một dịch vụ với "giặt khô áo da" của họ. **Một cuộc gọi mystery-shop
> xác minh được điều này trong 10 phút** và phải làm trước khi in bất cứ thứ gì.

---

## 2. Thời điểm: còn 11 ngày, và đây không phải lời hối thúc bán hàng

| Ngày | Việc gì | Nguồn |
|---|---|---|
| **29/8 – 2/9/2026** | **Nghỉ Quốc khánh 5 ngày liên tục** (thứ Bảy 29/8 → thứ Tư 2/9; hoán đổi 31/8 sang 22/8) | `chinhphu.vn` |
| **31/8/2026** | Ưu đãi 30%/40% của tiệm **hết hạn** | `PROMOTION_2026_08.md` |
| hết tháng 8 | **Đỉnh khách nội địa kết thúc** — tháng 8 là tháng công suất cao nhất năm | Sở Du lịch Khánh Hòa |
| tháng 9–đầu 12 | Mùa thấp điểm **nội địa** (định nghĩa chính thức của báo Khánh Hòa) | báo Khánh Hòa |
| tháng 10–12 | Mùa mưa; **tháng 11 mưa nhiều nhất (~330mm)** | dữ liệu khí hậu |

Công suất phòng trung bình toàn tỉnh theo tháng — **số liệu ghép từ nhiều năm khác nhau** (Sở Du lịch
không công bố chuỗi 12 tháng của cùng một năm), nên dùng để thấy **hình dạng**, không dùng để so sánh
tuyệt đối:

| 1 | 2 | 3 | 4 | 5 | 6 | **8** | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|
| 52% | 70% | 66% | 52% | 60% | 57% | **85%** | 79% | 47% | 40% | 25% |

Bảy tháng đầu 2026: **~15,1 triệu lượt khách (+38,95%)**, trong đó **~5,5 triệu khách quốc tế
(+67,24%)**. Các dịp lễ lớn vượt 90% (Tết 2026 trên 95% kéo dài hơn hai tuần).

### Điều quan trọng nhất trong bảng trên: hai mùa chạy ngược nhau

**Khách quốc tế phản chu kỳ với khách nội địa.** Tháng 11/2025, Khánh Hòa phục vụ 727.000 lượt lưu
trú, trong đó **411.000 là khách quốc tế và chỉ 316.000 nội địa** — quốc tế *đông hơn* nội địa đúng
vào mùa thấp điểm nội địa. Báo Khánh Hòa tháng 10/2025: *"Du lịch quốc tế đã vào mùa cao điểm."*

Nên **mùa thấp điểm của khách sạn không phải mùa thấp điểm của tiệm giặt**, nếu tiệm bán đúng thứ:

| Giai đoạn | Ai là khách | Bán gì |
|---|---|---|
| **Bây giờ → 2/9** | nội địa, đỉnh điểm | overflow linen cho cơ sở nhỏ + đồ da/giày/vest cho khách lưu trú |
| **tháng 9 → 12** | **quốc tế (Nga, +454%), lưu trú dài ngày** | khách ở dài ngày = giặt lặp lại hằng tuần |
| **tháng 10 → 12, mùa mưa** | **cư dân địa phương** | **sấy.** Tháng 11 mưa ~330mm — không ai phơi khô được đồ. Tiệm có 2 máy sấy, tải danh nghĩa 45,2kg |
| **quanh năm** | mọi người | đồ da, giày, vest — **không phụ thuộc thời tiết và không phụ thuộc mùa** |

Nghĩa là: kỳ nghỉ 5 ngày sát nhất là **11 ngày nữa**, và đó là cơ hội B2B nội địa cuối cùng của năm.
Nhưng **tiệm không "hết mùa" sau 2/9** — nó *đổi khách*. Kế hoạch tháng 9 trở đi phải là khách quốc
tế lưu trú dài ngày, dịch vụ sấy mùa mưa, và đồ da quanh năm.

---

## 3. Ai đang đến Nha Trang, và họ nhắn tin bằng gì

| Thị trường | Số liệu mới nhất | Ứng dụng nhắn tin thật sự dùng |
|---|---|---|
| **Nga + CIS** | Q1/2026: 268.333 (**+454%**); 6 tháng đầu 2026 gần **620.000 khách Nga, gấp 3,7 lần** — đứng **thứ 2** toàn tỉnh | **KHÔNG phải WhatsApp.** Nga chặn WhatsApp từ 12/02/2026 và siết Telegram. Telegram vẫn là nơi cộng đồng Nga ở Nha Trang tụ họp |
| **Hàn Quốc** | Q1/2026: ~616.000 — **đi ngang/giảm nhẹ** (98,3% so với Q1/2025), số chuyến bay giảm từ 20+ xuống 14/ngày | **KakaoTalk** (~97% thị phần trong nước) |
| **Trung Quốc** | Q1/2026: 215.000 (+14%) | **WeChat** |
| **Kazakhstan** | 52.650 khách trong 5 tháng đầu 2025 (+30%) | **WhatsApp** (83,4% dân số) — *khác Nga*, WhatsApp không bị chặn ở Kazakhstan |
| **Việt Nam** | phần lớn lượng khách | **Zalo** — 79,6 triệu người dùng/tháng, ~83% thị phần |

Ba điều rút ra:

1. **Nga là thị trường tăng trưởng, không phải Hàn Quốc.** Hàn vẫn lớn nhất về tuyệt đối nhưng đang
   đi ngang và giảm tải bay. Đầu tư ngôn ngữ nên theo thứ tự **Việt → Nga → Anh → Hàn**.
2. **Đừng dựng WhatsApp làm kênh quốc tế chính.** Nó trúng Kazakhstan, trượt Nga và Hàn.
3. **Zalo là kênh Việt Nam, không bàn cãi** — và cũng là kênh đã được `DEC-005` chốt.

---

## 4. Kênh: cái gì thật, cái gì đã chết, cái gì cấm

### 4.1 Google Business Profile — ưu tiên số 1, và làm được ngay hôm nay

- **Tin nhắn qua Google đã chết** từ **31/07/2024**. Không còn inbox. Thứ duy nhất còn lại là một
  **liên kết Chat** trỏ sang WhatsApp hoặc SMS — *chỉ được chọn một*.
- Lấy lại quyền quản trị: `business.google.com/add` → tìm listing → **Request Access** → chủ hiện tại
  có **3 ngày** để phản hồi.
- Xác minh: Google **chỉ định** phương thức, không đổi được. Với cửa hàng nhỏ thường là **quay video
  một lần liên tục**: biển hiệu ngoài, tên đường, hàng xóm, bên trong tiệm với máy móc, và bằng
  chứng quản lý (giấy tờ, chìa khoá, hệ thống).
- **Tên profile phải đúng tên thật trên biển hiệu.** Nhồi từ khoá là nguyên nhân bị khoá phổ biến
  nhất. → điều này va thẳng vào `DEC-017`: **cái gì đang trên biển thì cái đó phải là tên trên
  Google.**
- **Đánh giá:** được phép mời khách đánh giá thật, **cấm tặng quà đổi lấy đánh giá**. Google cung cấp
  sẵn link và **mã QR xin đánh giá**.
- **Services** (không phải Products) là bề mặt đúng cho tiệm giặt: tên + giá + mô tả. **Không được
  ghi giá hay số điện thoại vào phần tên**, nếu không sẽ bị từ chối âm thầm.
- **Performance** là công cụ đo miễn phí duy nhất: khách đã gõ từ khoá gì, bao nhiêu lượt gọi, chỉ
  đường, click web.

### 4.2 Zalo OA — kênh chính thức, và có một cái bẫy 14 ngày

Hồ sơ xác thực gồm **ba thứ**, và một trong ba chỉ chủ tiệm lấy được:

1. **Giấy chứng nhận đăng ký doanh nghiệp — quét ĐỦ MỌI TRANG**, có dấu và chữ ký. Quét thiếu trang
   là bị từ chối.
2. **CCCD/CMND/hộ chiếu của người đại diện pháp luật**, khớp tên trên giấy phép.
3. **Công văn xác thực (CVXT)** theo mẫu tải từ chính màn hình OA Manager — không có mẫu này từ nguồn
   khác.

**BẪY:** hồ sơ phải nộp **trong vòng 14 ngày kể từ khi tạo OA**. Hồ sơ bị từ chối mà không nộp lại
đúng hạn → **tài khoản bị khoá vĩnh viễn.**

> **Vì vậy: KHÔNG tạo OA cho tới khi ba giấy tờ trên đã quét xong và nằm sẵn trong máy.**

Xét duyệt 2–3 ngày làm việc. Sau khi xác thực, **Gói Cơ bản miễn phí**.

Chính sách gửi tin (đã sửa lại một nhầm lẫn trong `RESEARCH_BRIEF.md`):

- **Không có cold DM trên Zalo OA.** Broadcast chỉ đến người đã quan tâm OA.
- Tin Tư vấn gửi qua **giao diện OA Manager**: trong vòng **365 ngày** kể từ tương tác cuối. Qua
  **OpenAPI**: chỉ **7 ngày**. *Brief cũ ghi 7 ngày cho cả hai — sai.*
- Trong **48 giờ** kể từ tương tác cuối: **8 tin miễn phí**, sau đó 55đ/tin. Tin Giao dịch 165đ/tin.
- "Tương tác" gồm: nhắn tin, gọi, bình luận bài đăng, bấm menu/CTA/widget, quan tâm OA.

### 4.3 Kênh còn lại

- **Facebook Page + trả lời tự động của Meta Business Suite**: miễn phí, không cần lập trình. Cửa sổ
  trả lời tin do người dùng khởi tạo thường là 24 giờ.
- **Các bài "top 10/20 tiệm giặt ủi Nha Trang"**: đây là lớp SEO địa phương thật. Đối thủ có mặt,
  tiệm thì không. Rẻ và nhanh.
- **Telegram cho khách Nga**: cộng đồng Nga ở Nha Trang tụ trên Telegram. **Nhưng mọi nhóm lớn đều
  bắt đăng quảng cáo qua admin, và có nhóm thu phí.** Đăng thẳng = bị xoá bài và ban tài khoản. Phải
  hỏi luật nhóm trước.

---

## 5. Hệ thống AI: ai làm gì, và tại sao ranh giới nằm ở đó

### 5.1 Nguyên tắc phân chia

> **Tự động hoá việc lặp lại và đảo ngược được. Giữ con người ở việc quan hệ và không đảo ngược
> được.**

Gửi tin cho người lạ là **không đảo ngược** — không có nút "hủy spam". Dựng danh sách là lặp lại và
đảo ngược được. Ranh giới không tuỳ tiện; nó chạy dọc theo mức độ đảo ngược. Đây cũng đúng bằng ràng
buộc mà repo đã chốt: **mô hình không bao giờ chọn khách hàng và không bao giờ gửi.**

### 5.2 Bốn vai, mỗi vai một giới hạn cứng

| Vai | Vùng tin cậy | Đầu vào | Đầu ra | Tuyệt đối không |
|---|---|---|---|---|
| **Lead Scout** | Private | web công khai | dòng trong `templates/accounts.csv` + tuyến đi trong ngày | liên hệ ai; lưu thông tin cá nhân |
| **Lễ tân inbound** | Public cell | tin nhắn khách tự gửi | bản nháp trả lời + dữ kiện tiếp nhận | báo giá ngoài pricebook; hứa slot; tự gửi |
| **Điều phối tuyến & công suất** | Private | đơn + log công suất | thứ tự lấy/giao hôm nay | hứa công suất chưa đo |
| **Phân tích bằng chứng** | Private | đơn + nguồn khách | kênh nào ra đơn có trả tiền | đề xuất chi tiền khi chưa có contribution |

### 5.3 Đường đi của một khách, và ranh giới pháp lý nằm ở đâu

```
PROSPECT (bảng tính)        — dữ liệu tổ chức công khai, KHÔNG có cá nhân
  → người đi hỏi xin phép   — người thật, gặp mặt hoặc gọi số business công bố
  → ĐƯỢC PHÉP               — ghi: ai, khi nào, đồng ý với câu chữ nào, phạm vi gì
  → CONTACT (bảng tính)     — giờ mới có một con người, kèm bằng chứng đồng ý
  → pilot có trả tiền       — một đơn nhỏ, tiền thật, SLA thật
  → KHÁCH HÀNG (database)   — có bound_contact_id; đơn hàng mới tạo được
  → tuyến cố định
```

> **Bảng tính giữ prospect. Database giữ khách hàng. Một dòng vượt ranh giới khi con người đồng ý.**

Đây không phải tiện lợi lưu trữ — đó là ranh giới pháp lý, và nó khớp đúng với `suppression_entries`
đã có: vắng dòng = chưa biết = chặn; `CLEAR` phải do một hành vi có chủ ý và có audit ghi vào.

`templates/accounts.csv`, `contacts-consent.csv`, `interactions.csv`, `pilots-orders.csv` **đã tồn
tại, đúng đúng mô hình `RESEARCH_BRIEF.md` §10, đang rỗng, và không code nào đọc chúng.** Đó là phía
thấp-công-nghệ của ranh giới, đúng như §12 khuyến nghị.

### 5.4 AI nhân hiệu suất ở đâu mà không chạm ranh giới

1. **Tìm và xếp hạng** — đã làm hôm nay, xem `templates/accounts.csv`.
2. **Xếp tuyến** — "9 cửa này, theo thứ tự này, sáng nay". Thuần tất định theo khoảng cách.
3. **Brief trước khi gặp** — một trang mỗi prospect: họ là ai, hỏi gì, **không được hứa gì**.
4. **Ghi lại sau khi gặp** — chủ tiệm đọc một đoạn ghi âm, agent cấu trúc thành `interactions.csv`.
   **Đây là chỗ tự động hoá có giá trị nhất mà không ai nghĩ tới** — vì đây chính là bước thật sự
   hỏng trong bán hàng nhỏ lẻ: không ai ngồi ghi CRM sau khi đi về.
5. **Kỷ luật follow-up** — đồng hồ nhắc theo cadence; agent soạn, **người gửi**.
6. **Trả lời inbound tức thì** — khi đã có kênh.
7. **Ghi nguồn khách và bảng điểm tuần** — kênh nào ra đơn trả tiền.

**Ba việc con người không giao được:** xin phép, đưa ra lời hứa, bấm nút gửi.

### 5.5 Về "24/7"

AI làm được **lễ tân 24/7**. Nó không làm cho việc lấy đồ, giặt và giao thành 24/7. Tiệm mở
08:00–20:00, và công suất mà AI được tự xác nhận hiện là **0 kg/ngày** (`BUSINESS_TRUTH_INTAKE.md`
§2). Ngoài giờ, câu trả lời trung thực là: ghi nhận, hỏi đủ thông tin, **không hứa gì**, và nói rõ
mấy giờ có người trả lời. Im lặng và hứa hão đều tệ như nhau. → `DEC-016`.

### 5.6 Cách dùng danh sách prospect

Danh sách nằm ở `templates/accounts.csv`: **184 tổ chức** đã lọc trùng từ 193 dòng thô — **125 tier A** (trong ~2km, giao nhận miễn phí), **23 tier B** (2–6km), 32 để sau, 4 ngoài phân khúc. Ba quy ước phải hiểu trước khi dùng:

**Một, `fit_score` viết là `N/45`, không phải `N`.** Thang chấm điểm của chính tiệm
(`SALES_AND_NURTURE_PLAYBOOK.md`) là 100 điểm, nhưng **chỉ 45 điểm biết được từ dữ liệu công khai**:
độ phù hợp dịch vụ/khối lượng (20), khoảng cách và hậu cần (15), tiềm năng biên lợi (10). **55 điểm
còn lại — pain, tiếp cận đúng người, thời điểm, sẵn sàng pilot — chỉ có được sau khi gặp.** Một con
số 31 trần trụi sẽ bị đọc nhầm là 31%; `31/45` thì không.

**Hai, mọi khoảng cách là ước lượng đường chim bay**, suy từ địa chỉ công bố, không phải đo bằng xe
máy. Dùng để xếp thứ tự, không dùng để tính phí giao.

**Ba, danh sách chỉ chứa tổ chức.** Không có tên người, không có số di động cá nhân, không có "ai là
quản lý". Đó là ràng buộc của `DEC-015`, không phải thiếu sót của nghiên cứu.

#### Phân khúc 10–40 phòng không phải sở thích — nó là trần công suất

Trong 43 cơ sở lưu trú có công bố số phòng, phân bố thực tế là:

| ≤20 phòng | 21–40 | 41–80 | 81–150 | >150 |
|---:|---:|---:|---:|---:|
| 8 | 7 | 12 | 10 | 6 |

**Trung vị là 56 phòng** — lớn hơn phân khúc 10–40 mà brief giả định. Nhưng phép tính sau đây cho
thấy vì sao không nên mở rộng phân khúc chỉ vì danh sách có nhiều cơ sở lớn hơn:

Một khách sạn 56 phòng, công suất 80%, thay đồ ~45 phòng/ngày. Ước lượng **3–5kg đồ vải mỗi phòng**
(ga, vỏ gối, khăn) → **khoảng 135–225 kg/ngày cho một cơ sở duy nhất**.

Công suất tiệm công bố là **300–400 kg/ngày và chưa đo**; công suất AI được tự xác nhận là **0**.

> **Một khách sạn cỡ trung có thể ăn hết một nửa công suất công bố của cả tiệm** — trước khi tính
> đến bất kỳ đơn lẻ nào của khách bình thường.
>
> Nên phân khúc 10–40 phòng không phải là sở thích chiến lược. **Nó là trần công suất.** Và với cơ sở
> lớn hơn, thứ duy nhất bán được là **overflow đúng nghĩa** — một túi vào ngày cao điểm — chứ không
> phải toàn bộ linen.
>
> *Ước lượng 3–5kg/phòng là con số ngành, chưa đo tại tiệm.* `SHOP-INSTRUMENT-001` sẽ thay nó bằng số
> thật. Nhưng kết luận không đổi dù con số nằm ở đâu trong khoảng đó.

Hệ quả: **nhóm spa/gym/salon (71 cơ sở trong danh sách) khớp công suất tốt hơn nhiều** so với linen
khách sạn — khăn nhẹ hơn, đều hơn, và không dồn vào một khung giờ trả phòng.

**14 cơ sở lưu trú đúng phân khúc ≤40 phòng nằm trong vòng 2km**, đã có tên và số phòng, trong
`templates/accounts.csv`. Đó là danh sách đi bộ của thứ Hai.

#### Chuỗi trước, đơn lẻ sau

Phát hiện có giá trị nhất trong đợt tìm kiếm: **một chuỗi là một cuộc trò chuyện cho nhiều điểm.**

Ví dụ (theo trang danh sách công bố, **chưa gọi xác minh**): **A Tài Barbershop công bố 8 chi nhánh
ở Nha Trang dưới cùng một số điện thoại, trong đó ít nhất 4 nằm trong vòng 2km giao miễn phí.**
Nguồn là các trang tổng hợp không ghi ngày, nên độ tin cậy ở mức TRUNG BÌNH — một cuộc gọi xác nhận
số chi nhánh trước khi đi. Một cuộc gặp, tám điểm lấy khăn. Tương tự:
Olympic Gym (2), Hair Salon Polo (2), Snake Barber (2), Zen Spa (2), The Ann's Nail (2), Nhất Yoga
(2), cụm Sứ Spa trên Nguyễn Thiện Thuật (2–4).

**Ngược lại, chuỗi quốc gia có chi nhánh ở Nha Trang — Seoul Spa, KAY Spa, Taza Spa, California
Fitness & Yoga — chậm hơn vẻ ngoài**, vì ở đây chỉ là một điểm và quyết định mua nằm ở tổng công ty.

Thứ tự đi vì vậy là: **chuỗi địa phương nhiều chi nhánh → cơ sở lưu trú độc lập 10–40 phòng trong
2km → spa/gym đơn lẻ trong 2km → phần còn lại.**

---

## 6. Việc phải làm — 11 ngày, theo thứ tự

Không việc nào cần viết thêm dòng code nào.

### Ngày 1 (hôm nay) — mở cửa trước

1. **Xử lý Google Business Profile — làm đầu tiên vì đây là đồng hồ đếm ngược duy nhất không rút
   ngắn được.** Vào `business.google.com/add` và tìm "3A Lê Đại Hành":
   - **Nếu listing đã tồn tại** (`BUSINESS_TRUTH_INTAKE.md` §5 nói là có, nhưng chưa ai cung cấp URL
     hay quyền quản trị) → bấm **Request Access**. Chủ hiện tại có **3 ngày** để phản hồi. 10 phút
     thao tác, rồi chờ.
   - **Nếu không có listing nào** → tạo mới. Không phải chờ 3 ngày, nhưng vẫn phải qua xác minh.
   - Cả hai đường đều dừng ở cùng một chỗ: **xác minh do Google chỉ định**, thường là quay video một
     lần liên tục. Chuẩn bị sẵn: biển hiệu, tên đường, hàng xóm, bên trong tiệm với máy móc, và bằng
     chứng quản lý.
   - **Chưa đặt tên profile trước khi trả lời `DEC-017`.** Tên phải đúng tên trên biển, và đổi tên
     sau khi đã xác minh sẽ phải xác minh lại.
2. **Quét sẵn ba giấy tờ Zalo OA** (giấy phép đủ trang có dấu, CCCD người đại diện, và tải mẫu CVXT).
   **Chưa tạo OA.**
3. **Trả lời `DEC-017`:** có hợp đồng nhượng quyền không? Câu trả lời quyết định tên trên Google, trên
   QR, trên tờ rơi và trên hợp đồng B2B.

### Ngày 2–3 — biết mình đang bán gì

4. **Mystery-shop 3 cuộc gọi:** Giặt Ủi 2H (giá đồ da và giày thật), VIKHACO `0905.239.557` (giá B2B
   linen theo kg — gọi với tư cách người mua), và một khách sạn 20 phòng hỏi xem họ đang dùng ai.
5. **Xác minh hoa hồng 25%** của WashInCloud với 2–3 lễ tân. Con số này định giá toàn bộ kênh lễ tân.
6. **Chốt bảng giá B2B của chính tiệm** cho khăn spa/gym, ga giường và đồng phục — theo kg và theo
   món. **Không đối thủ nào ở Nha Trang công bố giá B2B**; tất cả đều giấu sau chữ "liên hệ". Công bố
   giá cố định là một khác biệt thật, và nó miễn phí.

### Ngày 4–7 — có mặt

7. **Hoàn thiện Google Business Profile** sau khi có quyền: đúng tên trên biển, giờ 08:00–20:00, ảnh
   thật (mặt tiền, máy móc, đóng gói, bàn giao), Services có giá, một liên kết Chat.
8. **Xin đánh giá thật** từ khách hiện có bằng QR chính thức của Google. **Không tặng quà.**
9. **Liên hệ các trang "top tiệm giặt ủi Nha Trang"** để được đưa vào danh sách.
10. **Tạo Zalo OA và nộp hồ sơ ngay trong ngày đó** (đồng hồ 14 ngày bắt đầu chạy khi tạo).
11. **Bật trả lời tự động Facebook** trong Meta Business Suite.

### Ngày 8–11 — gõ cửa, trước kỳ nghỉ

12. **Đi 30 cửa** — chọn từ **125 cơ sở tier A** trong bán kính 2km ở `templates/accounts.csv`, ưu tiên theo thứ tự đã xếp (gần nhất trước, chuỗi nhiều chi nhánh trước). Mang theo:
    - **Offer đúng:** *không* phải "giặt rẻ hơn". Mà là **"29/8–2/9 nghỉ 5 ngày, phòng kín trên 90%.
      Khi kẹt, bên em cách đây 1,2km, giặt xong trả trong 8 giờ, có cân, có đếm, có nhãn theo phòng."*
    - **Và offer thứ hai, mạnh hơn:** đồ da, giày da, vest, túi xách của **khách lưu trú** — rẻ hơn
      đối thủ 2–3 lần, tiệm có máy giặt khô và máy giày thật, lễ tân có thứ để giúp khách.
13. **Ghi lại từng cuộc** vào `templates/interactions.csv` ngay trong ngày.
14. **Mục tiêu: 30 cuộc trò chuyện thật, không phải 300 tin nhắn.** Con số này lấy từ
    `RESEARCH_BRIEF.md` §13 và không đổi.

---

## 7. Cái gì KHÔNG làm, và vì sao

| Không làm | Vì sao |
|---|---|
| **Gọi điện hoặc nhắn tin chào hàng từ số 0382 318 492** | **Nghị định 91/2020 Điều 13 khoản 8 (đã đối chiếu nguyên văn):** *"Chỉ được gửi tin nhắn quảng cáo, gọi điện thoại quảng cáo khi đã được cấp tên định danh và không được phép sử dụng số điện thoại để gửi tin nhắn quảng cáo hoặc gọi điện thoại quảng cáo."* Tức là **cấm dùng số điện thoại thường để gọi/nhắn quảng cáo** — phải có tên định danh được cấp. Xem §7.1. |
| Nhắn tin hàng loạt cho khách sạn | Zalo OA **không có cold DM**. Nghị định 91/2020 vẫn còn hiệu lực và yêu cầu đồng ý trước, cấm gửi tới số trong **Danh sách không quảng cáo** quốc gia, và bắt **dừng ngay lập tức** khi khách từ chối (Điều 13 khoản 4 — không có thời gian ân hạn). Và `MarketingDeliveryRepository` sẽ giữ lại mọi tin marketing với `MARKETING_AUTHORIZATION_UNAVAILABLE` — đúng như thiết kế. |
| Chạy quảng cáo trả phí | Chưa biết contribution margin. `SHOP-INSTRUMENT-001` đang `BLOCKED`. Quảng cáo khi chưa biết lãi/đơn là đốt tiền nhanh hơn. |
| Đăng bài vào nhóm Telegram/Facebook Nga | Mọi nhóm lớn bắt qua admin, có nhóm thu phí. Đăng thẳng = mất tài khoản. |
| Xây CRM trong PostgreSQL | `RESEARCH_BRIEF.md` §12 cấm ở giai đoạn này, và bảng tính đã có sẵn đúng hình. Xem `DEC-015`. |
| Hứa công suất | AI được tự xác nhận **0 kg/ngày**. Mọi slot phải người duyệt. |
| Lưu tên/số điện thoại cá nhân của quản lý khách sạn | `DEC-015` chưa ký. Danh sách prospect chỉ chứa **tổ chức**. |
| In tờ rơi/QR ngay | `DEC-017` chưa trả lời — chưa biết tên nào là tên đúng. |

### 7.1 Ba điều chỉnh pháp lý bắt buộc với `RESEARCH_BRIEF.md` §11

Brief tháng 7 dẫn **Nghị định 13/2023/NĐ-CP** như văn bản đang áp dụng. **Không còn đúng.**

| Điều chỉnh | Sự thật hôm nay |
|---|---|
| **NĐ 13/2023 đã hết hiệu lực** | Hết hiệu lực **01/01/2026**, thay bằng **Nghị định 356/2025/NĐ-CP** (ban hành 31/12/2025, 5 chương 42 điều) — nghị định hướng dẫn duy nhất cho tới nay của Luật 91/2025/QH15 |
| **Cấm quảng cáo bằng số điện thoại thường** | NĐ 91/2020 Điều 13 khoản 8: phải **được cấp tên định danh**; không được dùng số điện thoại để gửi tin/gọi quảng cáo. SMS quảng cáo còn phải gắn nhãn **`[QC]`** hoặc **`[AD]`** ở **đầu** tin (Điều 14–16) |
| **Agent phải tự khai là AI — nay là nghĩa vụ luật** | Luật 134/2025/QH15 Điều 11 buộc hệ thống AI tương tác trực tiếp với con người phải công bố. Câu *"Em là trợ lý tự động của…"* trong `SALES_AND_NURTURE_PLAYBOOK.md` từ nay là **yêu cầu pháp lý**, không chỉ là đạo đức |

**Một điều giảm nhẹ có lợi cho tiệm:** Luật 91/2025 Điều 38 cho **doanh nghiệp nhỏ và startup hoãn 5
năm** nghĩa vụ hồ sơ đánh giá tác động (Điều 21, 22) và nhân sự bảo vệ dữ liệu chuyên trách (khoản 2
Điều 33); **hộ kinh doanh và doanh nghiệp siêu nhỏ được miễn**. Đây là hoãn **giấy tờ**, không phải
hoãn nghĩa vụ xin đồng ý.

**Mức phạt không nhỏ:** tối đa **10 lần khoản thu** từ hành vi mua/bán dữ liệu cá nhân, và **5% doanh
thu** với xử lý dữ liệu trái phép (Luật 91/2025 Điều 8).

> **Và đây là điều nên đọc kỹ nhất trong cả tài liệu này.**
>
> Luật cấm gọi và nhắn quảng cáo bằng số thường. Zalo OA cấm cold DM. Nhóm Telegram/Facebook bắt qua
> admin. **Mọi con đường "nhắn hàng loạt cho người lạ" đều bị chặn — bởi luật, bởi nền tảng, hoặc bởi
> chính hệ thống này.**
>
> Con đường còn lại là **đi bộ tới cửa và nói chuyện với một con người.** Đó không phải quảng cáo
> theo nghĩa của nghị định; đó là một cuộc gặp. Nó vừa là con đường **hợp pháp nhất**, vừa là con
> đường **chuyển đổi cao nhất**, và nó chính là thứ `SALES_AND_NURTURE_PLAYBOOK.md` đã viết sẵn kịch
> bản từ đầu.
>
> *Chưa xác minh, cần luật sư:* một lời chào dịch vụ B2B gửi tới **số business công bố của doanh
> nghiệp** có được coi là "quảng cáo" theo NĐ 91/2020 hay không. Nghiên cứu hôm nay không tìm được
> câu trả lời dứt khoát. **Cho tới khi có, mặc định là không gọi chào hàng.**

---

## 8. Quyết định đang chờ chủ tiệm

| ID | Câu hỏi | Chặn cái gì |
|---|---|---|
| **`DEC-017`** | Có hợp đồng nhượng quyền không, và tên nào lên tài sản khách hàng thấy? | Google, QR, tờ rơi, offer B2B — **làm trước tiên** |
| **`DEC-013`** | Khách vãng lai được nhận diện thế nào ở quầy? | Mọi khách không nhắn tin trước |
| **`DEC-015`** | Hồ sơ khách hàng là gì, khi nào một người trở thành khách? | `ACQUISITION-001`, ranh giới bảng tính↔database |
| **`DEC-016`** | Ai trực inbound, tài khoản của ai? | Nối kênh — **đừng nối trước khi trả lời** |

Cộng thêm **pilot gate** trong `BUSINESS_TRUTH_INTAKE.md` §7: 11 trong 12 ô còn trống. Gate đó vẫn là
cơ chế điều nhịp; tài liệu này không tạo gate mới.

---

## 9. Đo cái gì, từ tuần đầu

Từ `RESEARCH_BRIEF.md` §15, rút xuống còn những gì đo được ngay:

- **Google Performance**: khách gõ từ khoá gì, bao nhiêu lượt gọi và chỉ đường. Miễn phí, có sẵn.
- **Số cuộc trò chuyện thật** (không phải tin nhắn gửi đi) → `interactions.csv`.
- **Tỉ lệ: prospect → được phép → pilot có trả tiền → tuyến lặp lại.**
- **Nguồn của mỗi đơn** — ghi bằng tay đến khi `ACQUISITION-001` được duyệt.

Và một điều chưa đo được, phải nói thẳng: **contribution/đơn và contribution/kg vẫn chưa biết.**
`SHOP-INSTRUMENT-001` đang `BLOCKED`. Cho tới khi đo xong, mọi con số tăng trưởng ở trên là *lượt*,
không phải *lãi*.

---

## 10. Hồ sơ kỹ thuật liên quan

- `context/tasks/TASK-acquisition-001.md` — packet `ACQUISITION-001`, **chưa đưa vào hàng đợi** (đó
  là hành vi lập lịch, và slice đầu bị chặn bởi `DEC-013`).
- `docs/DECISION_REQUEST_ACQUISITION_2026-08.md` — `DEC-015`, `DEC-016`, `DEC-017`.
- `templates/accounts.csv` — danh sách prospect đã xếp hạng.
- `SALES_AND_NURTURE_PLAYBOOK.md` — **kịch bản đã có sẵn, không cần viết lại.** Dùng "In-person
  opener" và "Xin permission để follow-up" nguyên văn.
