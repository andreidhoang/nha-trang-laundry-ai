# Hướng dẫn ca làm việc — Giặt Là Sạch Cộng

**Dành cho người đứng quầy.** In ra, để cạnh máy. Một trang cho cả ngày.

Bảng vận hành chạy trên chính chiếc máy ở quầy, tại địa chỉ:

> **https://console.giatlasachcong.lan:8443/staff/**

---

## Mở ca

1. Bật máy, mở Docker Desktop, đợi biểu tượng con cá voi đứng yên.
2. Mở bảng vận hành, đăng nhập bằng tài khoản của **chính mình** — không dùng chung tài khoản.
3. Mở màn hình **Hôm nay**. Đọc hai dòng đầu: việc đang chờ người quyết định, và tiền đã thu.
4. Nếu màn hình báo **“Đang ngoại tuyến”** hoặc **“Chưa đọc được danh sách cửa hàng”** → xem mục
   *Khi có sự cố* bên dưới. Đừng nhận đơn cho tới khi màn hình bình thường trở lại.

## Một khách vào cửa

Đi đúng thứ tự này. Máy chủ sẽ từ chối nếu làm tắt bước.

| | Làm gì | Ở màn hình |
|---|---|---|
| 1 | Bấm **Phát phiếu**, đọc to số phiếu cho khách, ghi số lên túi đồ | Tiếp nhận |
| 2 | Bấm **Ghi nhận tiếp nhận** | Tiếp nhận |
| 3 | Cân đồ, chọn dịch vụ, nhập số kg | Báo giá |
| 4 | Bấm **Tính giá**, đọc giá cho khách | Báo giá |
| 5 | Khách đồng ý → bấm **Khách đã chốt giá** | Báo giá |
| 6 | Bấm **Tạo đơn** | Đơn hàng |
| 7 | Nhận túi đồ từ khách → chuyển **nhận đồ** sang *Đã nhận, chờ kiểm* | Đơn hàng |
| 8 | Chuyển **nhận đồ** sang *Đã nhận*, tích **Đã duyệt lịch** | Đơn hàng |
| 9 | Chuyển **thương mại** lần lượt tới *Đang chạy* | Đơn hàng |
| 10 | Giặt xong thì chuyển **sản xuất**: xếp hàng → đang giặt → kiểm tra → sẵn sàng | Đơn hàng |
| 11 | Khách tới lấy → **Tìm theo số phiếu** (hỏi ngày trên phiếu nếu không phải hôm nay), bấm **Mở đơn**, rồi **Chuyển trạng thái đơn này**: **sản xuất** sang *Đã giao ra* | Đơn hàng |
| 12 | Đọc cho khách số **Phải thu** ngay trên ô tiền, nhập đúng số đó, tích **Khách đã tự lấy đồ**, bấm **Ghi nhận tất toán** | Chi tiết đơn |
| 13 | Chuyển **thương mại** sang *Hoàn tất* | Đơn hàng |

### Khách muốn trả tiền ngay lúc gửi đồ

Được, nhưng chỉ **đúng tổng đã báo** (quyết định DEC-032, 25/09/2026). Đặt cọc hay trả một phần
vẫn bị từ chối.

| | Làm gì | Ở màn hình |
|---|---|---|
| 9a | Sau bước 9 (đơn *Đang chạy*): **Mở đơn**, nhập đúng số **Phải thu**, bấm **Khách trả trước khi gửi đồ** — **không tích** ô “Khách đã tự lấy đồ” | Chi tiết đơn |
| 11a | Khách tới lấy: **Tìm theo số phiếu**, **Mở đơn**, đưa đồ rồi bấm **Khách đã nhận đồ** (tên bạn được ghi) | Chi tiết đơn |
| 13 | Chuyển **thương mại** sang *Hoàn tất* | Đơn hàng |

- Tiền tính vào **Đã thu tại quầy** của **ngày khách trả**, không phải ngày lấy đồ.
- Đồ chưa giặt xong thì máy không cho bấm đã nhận đồ — và cũng không cho tích ô “Khách đã tự lấy
  đồ” khi khách trả lúc lấy.

### Món niêm yết theo khoảng giá (áo dài, vest, giày da, sofa, thảm…)

Hai mươi trong bốn mươi bốn dịch vụ được niêm yết theo **khoảng giá**, vì phải nhìn món đồ mới
định giá được. Bước 4 ở trên sẽ báo là máy không tự chọn số. Làm tiếp như sau, vẫn ở màn hình
**Báo giá**:

| | Làm gì |
|---|---|
| 4a | Bấm **Lập bản khoảng giá**. Đọc khoảng cho khách nghe (ví dụ *80.000 – 240.000 ₫*) |
| 4b | Xem đồ, thoả thuận với khách, gõ **một số nằm trong khoảng** cho từng dòng |
| 4c | Bấm **Chốt giá này** → máy ghi giá luôn → quay lại bước 5 ở bảng trên |

- **Không cần chờ chủ tiệm.** Người đang trực quầy chốt giá trong khoảng chủ tiệm đã niêm yết
  (quyết định DEC-029, 25/09/2026). **Tên bạn được ghi cùng con số, không sửa được**, và chủ tiệm
  xem lại được tất cả — nên chỉ gõ đúng số đã thoả thuận với khách.
- Nếu màn hình dừng ở **Ghi giá vào báo giá** (mất mạng giữa chừng), bấm nút đó thêm một lần.
- Gõ số ngoài khoảng thì màn hình cảnh báo **ngay khi đang gõ**, và máy chủ cũng từ chối. Đừng
  lách: muốn ra ngoài khoảng thì chủ tiệm phải công bố lại bảng giá.

### Ba điều dễ sai nhất

- **Gần 6 kg.** Dưới 6 kg tính 25.000đ/kg, từ 6 kg tính 20.000đ/kg — nên **túi nhẹ hơn có thể đắt
  hơn**. Màn hình sẽ nhắc khi khối lượng gần ngưỡng. Đó là bảng giá của tiệm, không phải máy tính
  sai. Cân cho đúng, đừng làm tròn.
- **Tiền phải đúng bằng tổng đã báo.** Trả thiếu, trả thừa, đặt cọc đều bị từ chối. Nhập số y như
  màn hình in ra — gõ cả dấu chấm cũng được (`170.000`), nhưng **không có dấu phẩy, không số lẻ**.
- **Đơn giao tận nơi**: thu tiền **trước khi đồ rời tiệm**, và **để trống** ô “Khách đã tự lấy đồ”.
  Đơn chỉ đóng được khi có một chuyến **TRẢ** thành công.

## Đóng ca

1. Mở **Hôm nay**, đối chiếu **Đã thu tại quầy** với tiền mặt trong ngăn kéo.
2. Lệch thì **không sửa gì trên máy** — ghi ra sổ và báo chủ tiệm. Bản ghi tất toán không sửa được.
3. Để máy **bật qua đêm** (chỉ tắt màn hình). Bản sao lưu chạy lúc 2 giờ 30 sáng.

---

## Hai việc trước đây phải ghi ra sổ, nay làm được trên máy

Trang này trước đây dặn ghi tay hai việc. Cả hai đã làm được trên máy, và **ghi tay bây giờ là
mất bản ghi** — vì bản trên máy mới là bản không sửa được.

**1. Món có khoảng giá.** Làm theo mục *Món niêm yết theo khoảng giá* ở trên. Vẫn là bạn thoả
thuận giá với khách — chỉ khác là được ghi lại kèm tên người chốt, thay vì nằm trên một tờ phiếu.

**2. Khách khiếu nại.** Màn hình **Sự cố** dùng được rồi: chọn đơn, gõ nội dung khách phàn nàn
bằng lời, bấm ghi nhận. Không phải gõ mã gì cả — máy chủ tự sinh.

> Mở sự cố **chỉ là ghi nhận**: máy không phán ai sai và không tự quyết bồi hoàn. Hai dòng đó hiện
> là *chưa quyết định*, và đó là đúng. Vẫn **báo chủ tiệm ngay trong ngày**.

Những việc thật sự chưa hỗ trợ được liệt kê ở màn hình **Chưa hỗ trợ** trên bảng vận hành.

---

## Bồi hoàn cho khách — làm trên máy, không thoả thuận miệng

Trước đây phần này làm bằng lời rồi quên. Nay có màn hình **Bồi hoàn**, và mức chủ tiệm đã chốt
được máy chủ tự áp — nhân viên **không gõ mức trần**.

**Thứ tự đúng, năm bước:**

1. Ghi sự cố trước (mục trên). Không có sự cố thì không có bồi hoàn.
2. Trên dòng sự cố đó bấm **“Đề xuất bồi hoàn”**. Mã sự cố tự mang sang.
3. Bấm **“Đọc mức trần và thời hạn”**. **Đọc xong mới nói gì với khách.** Màn hình hiện sẵn:
   trần tối đa, hạn chót còn hay hết, và **có phải chờ chủ tiệm duyệt hay không**. Đọc bước này
   là để không lỡ hứa với khách một con số rồi mới biết phải chờ chủ tiệm.
4. Chọn loại, tích ô *“Tôi xác định lỗi thuộc về tiệm”*, điền nốt ô còn trống, bấm
   **“Gửi đề nghị bồi hoàn”**.
5. Bấm **“Thực hiện bồi hoàn”**. Xong bước này sự cố mới đóng lại.

**Bốn loại, và ai duyệt:**

| Loại | Máy chủ tính gì | Hạn | Ai duyệt |
|---|---|---|---|
| Giặt lại miễn phí | Không có tiền nào chuyển | 7 ngày kể từ khi khách nhận đồ | Nhân viên |
| Bồi thường món hỏng | Trần = 5 lần phí giặt **đúng món đó** | 24 giờ kể từ khi khách nhận đồ | Tới 100.000đ là nhân viên, trên mức đó **chủ tiệm** |
| Giảm trừ do giao trễ | Máy tính 10% tổng đã thu | Không tính theo hạn, tính theo mức trễ | Theo số tiền, như trên |
| **Mất đồ** | **Không có mức nào** | — | **Chưa ai** — xem bên dưới |

> **Mất đồ thì màn hình không mở biểu mẫu**, và đó là đúng. Chủ tiệm chưa quyết chính sách cho
> trường hợp này, nên máy hiện **“Mất đồ — chưa có chính sách để áp dụng”**. Đừng mượn mức của
> hàng hỏng. Ghi sự cố, **báo chủ tiệm ngay trong ngày**, đừng hứa con số nào ở quầy.

**Ba điều dễ sai ở màn hình này:**

- **Chép mã giảm trừ ngay lúc phát.** Máy hiện **“Chép mã giảm trừ này lại ngay”** kèm nút chép.
  Chép vào phiếu giấy của khách **trước khi rời màn hình** — không có màn hình nào tra lại được mã
  đó, và không có mã thì khách không dùng được phiếu.
- **“Chờ chủ tiệm duyệt” nghĩa là chưa xong.** Nói với khách là phải chờ. Bấm thực hiện trước khi
  chủ tiệm duyệt thì máy từ chối, và phiếu duyệt có hạn ngắn — đừng để qua ngày.
- **Vượt trần thì máy từ chối, không tự hạ xuống.** Nếu màn hình báo vượt trần, nói đúng con số
  trần cho khách nghe. Gõ một số khác cho lọt là tự quyết thay chủ tiệm.

**Khách quay lại dùng phiếu giảm trừ:** tính giá như bình thường, rồi ở mục *Dùng một khoản giảm
trừ* nhập mã phiếu, mã báo giá, số bản sửa đổi và dấu vân của bản đó, rồi bấm
**“Áp dụng khoản giảm trừ”**. Phiếu dùng **đúng một lần**. Đọc lại tổng mới cho khách nghe rồi mới
thu tiền.

---

## Khi có sự cố

| Màn hình nói | Nghĩa là | Làm gì |
|---|---|---|
| **Đang ngoại tuyến** | Máy mất mạng. Không có lệnh nào được gửi đi | Kiểm tra mạng. Trong lúc đó ghi tay, nhập lại sau khi có mạng |
| **Chưa biết lệnh có tới máy chủ hay không** | Lệnh có thể đã chạy | **Đừng bấm lại.** Tải lại bảng đơn và xem trạng thái thật |
| **Đơn này vừa được người khác đổi** | Người kia bấm trước | Tải lại bảng đơn, làm lại theo số mới |
| **Máy chủ không ghi nhận khoản này** | Số tiền hoặc tình huống không được hỗ trợ | Đọc mã lý do ngay bên dưới — nó nói rõ phải làm gì |
| **Cần người duyệt trước khi chuyển** | Đơn đã bắt đầu làm, không huỷ thẳng được | Đưa đơn sang *Đang xét huỷ*, rồi huỷ kèm lý do về đồ và tiền |
| **Phiên đăng nhập đã kết thúc** | Hết phiên | Đăng nhập lại. Không mất dữ liệu |
| **Máy chủ gặp lỗi** | Lỗi thật | **Đừng thử lại.** Xem bảng đơn để biết lệnh đã vào hay chưa, chụp màn hình, báo chủ tiệm |

**Quy tắc chung khi hoang mang:** không bấm lại lệnh vừa hỏng. Mở bảng đơn, đọc trạng thái thật của
đơn, rồi mới quyết định. Mỗi lỗi đều có **mã theo dõi** — chụp lại, đó là thứ tra ra được chuyện gì
đã xảy ra.

---

## Trong tuần thử nghiệm, nhớ ghi lại

Những số này không có cách nào đo lại về sau, và chúng mở khoá phần tiếp theo của hệ thống:

- **10 mẻ giặt sấy có bấm giờ** — từ lúc bắt đầu tới lúc xong, giờ thật.
- **20 chuyến giao** — quãng đường và thời gian.
- Mỗi ngày: có gì trên máy làm chậm việc ở quầy hơn là làm tay.
