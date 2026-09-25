# Hướng dẫn ca làm việc — Giặt Là Sạch Cộng

**Dành cho người đứng quầy.** In ra, để cạnh máy. Một trang cho cả ngày.

Bảng vận hành chạy trên chính chiếc máy ở quầy, tại địa chỉ:

> **https://console.giatlasachcong.lan:8443/staff/**

---

## Mở ca

1. Bật máy, mở Docker Desktop, đợi biểu tượng con cá voi đứng yên.
2. Mở bảng vận hành, đăng nhập bằng tài khoản của **chính mình** — không dùng chung tài khoản.
3. Mở màn hình **Hôm nay**. Đọc số **Đã thu tại quầy**, rồi mục **Cần làm**: mỗi dòng là một việc
   đang chờ (chờ duyệt, tin AI, gửi chưa rõ, sự cố, đơn quá mốc) — bấm dòng để mở. Hết việc thì mục
   này chỉ còn một dòng **“Không có gì chờ bạn”**. Hai nút lớn ở trên cùng: **＋ Nhận đồ** và
   **Khách tới lấy đồ** (tìm theo số phiếu).
4. Nếu màn hình báo **“Đang ngoại tuyến”** hoặc **“Chưa đọc được danh sách cửa hàng”** → xem mục
   *Khi có sự cố* bên dưới. Đừng nhận đơn cho tới khi màn hình bình thường trở lại.

## Một khách vào cửa

Đi đúng thứ tự này. Máy chủ sẽ từ chối nếu làm tắt bước.

| | Làm gì | Ở màn hình |
|---|---|---|
| 1 | Bấm **＋ Nhận đồ**. Khách quen: gõ số điện thoại, **4 số cuối** hoặc tên vào ô **SĐT hoặc tên khách**, bấm đúng khách — máy phát phiếu luôn. Khách lạ không muốn lưu: bấm **Khách vãng lai — phát phiếu**. Đọc to số phiếu màn hình hiện cho khách, ghi số lên túi đồ | Nhận đồ |
| 2 | Chọn cách giao nhận — khách tự mang tới và tự lấy là *Tại quầy* (đã chọn sẵn) | Nhận đồ |
| 3 | Cân đồ. Bấm **Chọn dịch vụ**, chọn món, gõ số kg ngay vào ô vừa hiện | Nhận đồ |
| 4 | Bấm **Tính giá**, đọc **Tổng khách trả** cho khách | Nhận đồ |
| 5 | Bấm **Tiếp tục**, hỏi khách biết tiệm qua đâu (chưa hỏi thì để *Chưa biết*) | Nhận đồ |
| 6 | Khách đồng ý → bấm **Khách đồng ý — tạo đơn**. Máy ghi lời đồng ý, tạo đơn, rồi mở luôn trang đơn | Nhận đồ |
| 6a | Khách cần phiếu → bấm **In phiếu cho khách** ngay đầu trang đơn, rồi **In phiếu** (máy in nhiệt 80 mm, 58 mm hoặc giấy A5). Máy có nút **Chia sẻ** thì gửi được phiếu qua điện thoại. Phiếu không ghi giờ hẹn trả đồ: khách có số điện thoại thì phiếu ghi tên khách và *Tiệm sẽ báo khi đồ sẵn sàng*; khách vãng lai thì ghi *Giữ phiếu này để nhận đồ* | Phiếu cho khách |
| 7 | Nhận túi đồ từ khách → bấm **Nhận đồ**, tích **Tiệm làm kịp đơn này**, bấm **Nhận đồ** lần nữa | Chi tiết đơn |
| 8 | Bỏ đồ vào máy → bấm **Bắt đầu giặt** | Chi tiết đơn |
| 9 | Giặt xong → **Giặt xong, kiểm tra đồ**; kiểm xong → **Báo đồ đã sẵn sàng** | Chi tiết đơn |
| 10 | Khách tới lấy → ở **Đơn hàng** gõ số vào ô **Số phiếu…** rồi bấm **Tìm** (phiếu ngày khác thì bấm **Hôm nay** để chọn ngày), mở đơn | Đơn hàng |
| 11 | Bấm **Thu tiền**, đọc cho khách số **Phải thu**, gõ đúng số khách đưa, bấm **Ghi nhận đã thu tiền** | Chi tiết đơn |
| 12 | Đưa đồ cho khách, bấm **Giao đồ & đóng đơn** (nút hiện ngay sau khi thu tiền) | Chi tiết đơn |

Trên trang của một đơn, **nút lớn ở cuối màn hình luôn là việc tiếp theo** — máy chủ tính ra, không
phải đoán. Các việc khác (tạm dừng, giặt lại, không nhận đồ, huỷ đơn, khách trả trước, **in lại phiếu**…)
nằm ở nút **Khác**.

### Đồ chưa sạch, hoặc tiệm không nhận đồ

| Khi nào | Làm gì | Ở màn hình |
|---|---|---|
| Kiểm tra thấy đồ **chưa sạch** (hoặc máy lỗi), đồ **chưa rời tiệm** | **Khác** → **Giặt lại**, chọn lý do (*Chưa sạch*, *Máy lỗi*, *Lý do khác*), bấm **Giặt lại**. Đồ quay lại bước giặt; giặt xong thì **Giặt xong, kiểm tra đồ** như thường | Chi tiết đơn |
| Đơn đã ghi **tiệm đang giữ đồ nhưng chưa nhận làm** (trạng thái *đã nhận, chờ kiểm* hoặc *chờ duyệt…*) và tiệm **không nhận** (tiệm không giặt loại này, đồ hỏng sẵn…) | **Khác** → **Không nhận đồ**, chọn lý do, bấm **Không nhận đồ** hai lần. Trả túi đồ cho khách; đơn bị huỷ, không có tiền nào chuyển | Chi tiết đơn |
| Chưa bấm **Nhận đồ** mà khách lấy lại túi đồ | **Khác** → **Huỷ đơn**: đơn chưa ghi tiệm giữ đồ, nên huỷ ngay | Chi tiết đơn |

- Giặt lại **không tính thêm tiền**: giá của đơn giữ nguyên.
- Khách đã lấy đồ về rồi mới quay lại vì đồ chưa sạch thì **không** bấm Giặt lại — đó là
  **khiếu nại** (xem mục khiếu nại bên dưới).
- Lịch sử của đơn ghi rõ bước và lý do, ví dụ **Giặt lại · Chưa sạch**, kèm tên bạn.

- **Khách đã nhắn tin cho tiệm qua kênh chính thức:** từ cuộc trò chuyện của khách — thẻ tin
  nhắn ở **Duyệt**, **Bản nháp AI** hoặc **Gửi tay** — bấm **Tạo đơn cho khách này**: máy mở lượt
  tiếp nhận và đi thẳng tới bước 2. Khách từng đặt ở tiệm thì chọn ở mục **Khách nhắn tin gần đây**
  ngay dưới nút phát phiếu (một dòng đang chờ báo giá thì mở lại đúng lượt đó, không tạo thêm).
  Khách nhắn tin chưa có hồ sơ thì không có tên hay số điện thoại để tìm. *Nhập mã thủ công* (gập
  sẵn) chỉ dành cho mã đọc được ở nơi khác: nhập rồi bấm **Ghi nhận tiếp nhận**. Mã lạ bị từ chối,
  không tự tạo.
- **Khách đã có phiếu nhưng chưa thành đơn** (mất mạng, khách quay lại sau): mở **＋ Nhận đồ**, chọn
  dòng *Phiếu N* ở mục **Tiếp tục một khách đang chờ** — món, giá và cách giao nhận hiện lại đủ.
  Nếu phiếu hoá ra đã thành đơn, máy báo vậy, kèm nút **Mở đơn** — không tạo lại được.
- Sửa món sau khi đã tính giá thì nút đổi thành **Tính lại**; bấm để ra giá mới rồi đọc lại cho khách.
- Nếu máy báo từ chối ở bước 6, đọc lý do ngay dưới nút. Bấm lại chỉ làm tiếp bước còn thiếu: lời
  đồng ý đã ghi thì không ghi lại, và nút đổi thành **Tạo đơn**.

### Khách quen: lưu một lần, lần sau tìm bằng 4 số cuối

Chủ tiệm đã quyết cho tiệm giữ danh sách khách (quyết định DEC-034, 25/09/2026) — **chỉ khi khách
đồng ý**.

| Khi nào | Làm gì | Ở màn hình |
|---|---|---|
| Khách mới muốn được nhớ | Ở ô **SĐT hoặc tên khách** gõ số của khách, bấm **Thêm khách mới**. **Đọc to câu trên màn hình** cho khách; khách đồng ý thì tích ô đầu. Ô tin ưu đãi **chỉ tích khi khách muốn** (mặc định là không). Bấm **Lưu và tiếp tục** — máy lưu khách, phát phiếu, sang bước 2 | Nhận đồ |
| Khách quay lại | Gõ **4 số cuối** (hoặc cả số, hoặc tên, không cần dấu) → bấm đúng tên khách. Một lần bấm là xong bước 1 | Nhận đồ |
| Cần gọi hay nhắn Zalo cho khách | **Thêm → Khách hàng** (hoặc **Tìm theo khách** ở **Đơn hàng**), mở khách, bấm **Gọi** hoặc **Zalo**. Trang của khách có đơn đang mở, lịch sử và khoản giảm trừ chưa dùng | Khách hàng |
| Khách xin xoá thông tin | Chủ tiệm hoặc người duyệt mở trang của khách, bấm **Xoá thông tin (khách yêu cầu)** hai lần. Tên, số, địa chỉ, ghi chú bị xoá; **đơn và tiền vẫn giữ** | Khách hàng |

- Màn hình báo **Chủ tiệm cần công bố thông báo bảo mật trước khi lưu khách** → chưa lưu được ai.
  Phát phiếu vãng lai như bình thường và báo chủ tiệm.
- Ô ghi chú **chỉ ghi cách giặt** ("giặt riêng đồ trắng"). Không ghi sức khoẻ, tôn giáo hay chuyện
  riêng của khách.
- Số đã có trong danh sách thì máy báo và cho **Chọn khách này** — không tạo trùng.
- Khách không đồng ý cũng không sao: bấm **Khách vãng lai — phát phiếu**.

### Khách muốn trả tiền ngay lúc gửi đồ

Được, nhưng chỉ **đúng tổng đã báo** (quyết định DEC-032, 25/09/2026). Đặt cọc hay trả một phần
vẫn bị từ chối.

| | Làm gì | Ở màn hình |
|---|---|---|
| 7a | Sau bước 7 (đã nhận đồ): bấm **Khác** → **Khách trả trước**, gõ đúng số **Phải thu**, bấm **Ghi nhận khách trả trước** | Chi tiết đơn |
| 10a | Khách tới lấy: tìm phiếu ở ô **Số phiếu…**, mở đơn, đưa đồ rồi bấm **Khách đã nhận đồ** (tên bạn được ghi), rồi **Đóng đơn** | Chi tiết đơn |

- Tiền tính vào **Đã thu tại quầy** của **ngày khách trả**, không phải ngày lấy đồ.
- Đồ chưa giặt xong thì chưa có nút **Khách đã nhận đồ**, và nút **Thu tiền** (trả lúc lấy) cũng
  chỉ hiện khi đồ đã sẵn sàng.
- **Đơn tiệm tới lấy đồ** (giao nhận *chỉ lấy*): khách chỉ trả **tại quầy**, đúng tổng đã báo — hoặc
  lúc tới lấy (bước 11, như khách tự mang tới), hoặc ghé quầy trả trước khi đồ giặt xong (bước 7a
  rồi 10a). **Người giao không nhận và không đưa tiền.**

### Món niêm yết theo khoảng giá (áo dài, vest, giày da, sofa, thảm…)

Hai mươi trong bốn mươi bốn dịch vụ được niêm yết theo **khoảng giá**, vì phải nhìn món đồ mới
định giá được. Bước 4 ở trên sẽ báo là máy không tự chọn số. Làm tiếp như sau, vẫn ở màn hình
**Nhận đồ**, ngay dưới hoá đơn tạm:

| | Làm gì |
|---|---|
| 4a | Bấm **Lập bản khoảng giá**. Đọc khoảng cho khách nghe (ví dụ *80.000 – 240.000 ₫*) |
| 4b | Xem đồ, thoả thuận với khách, gõ **một số nằm trong khoảng** cho từng dòng |
| 4c | Bấm **Chốt giá này** → máy ghi giá luôn → bấm **Tiếp tục** (bước 5 ở bảng trên) |

- **Không cần chờ chủ tiệm.** Người đang trực quầy chốt giá trong khoảng chủ tiệm đã niêm yết
  (quyết định DEC-029, 25/09/2026). **Tên bạn được ghi cùng con số, không sửa được**, và chủ tiệm
  xem lại được tất cả (màn hình **Duyệt**, nút **Giá trong khoảng**) — nên chỉ gõ đúng số đã thoả
  thuận với khách.
- Nếu màn hình dừng ở **Ghi giá vào báo giá** (mất mạng giữa chừng), bấm nút đó thêm một lần.
- Gõ số ngoài khoảng thì màn hình cảnh báo **ngay khi đang gõ**, và máy chủ cũng từ chối. Đừng
  lách: muốn ra ngoài khoảng thì chủ tiệm phải công bố lại bảng giá.

### Ba điều dễ sai nhất

- **Gần 6 kg.** Dưới 6 kg tính 25.000đ/kg, từ 6 kg tính 20.000đ/kg — nên **túi nhẹ hơn có thể đắt
  hơn**. Màn hình sẽ nhắc khi khối lượng gần ngưỡng. Đó là bảng giá của tiệm, không phải máy tính
  sai. Cân cho đúng, đừng làm tròn.
- **Tiền phải đúng bằng tổng đã báo.** Trả thiếu, trả thừa, đặt cọc đều bị từ chối. Nhập số y như
  màn hình in ra — gõ cả dấu chấm cũng được (`170.000`), nhưng **không có dấu phẩy, không số lẻ**.
- **Đơn giao tận nơi**: thu tiền **trước khi đồ rời tiệm** (**Khách trả trước**), bấm **Đưa đồ đi
  giao**, rồi khi khách nhận được bấm **Đã giao đồ cho khách** → **Giao thành công**. Đơn chỉ đóng
  được khi có một chuyến **TRẢ** thành công.

## Đóng ca

1. Mở **Hôm nay**, đối chiếu **Đã thu tại quầy** với tiền mặt trong ngăn kéo.
2. Lệch thì **không sửa gì trên máy** — ghi ra sổ và báo chủ tiệm. Bản ghi tất toán không sửa được.
3. Để máy **bật qua đêm** (chỉ tắt màn hình). Bản sao lưu chạy lúc 2 giờ 30 sáng.
4. Chủ tiệm (và người duyệt, kế toán, kiểm toán) xem số của ngày, tuần, tháng ở **Báo cáo** — bấm
   **Báo cáo** cạnh ô tiền trên **Hôm nay**, hoặc vào **Thêm → Báo cáo**. Mỗi ô ghi cả hai số
   (ví dụ **3 / 5 đơn tạo**); **Đúng hẹn (nội bộ)** đo theo mốc nội bộ 8 giờ của Bảng trễ hạn, không
   phải giờ hẹn với khách. Nhân viên quầy thấy mục này bị khoá, có ghi lý do.

---

## Hai việc trước đây phải ghi ra sổ, nay làm được trên máy

Trang này trước đây dặn ghi tay hai việc. Cả hai đã làm được trên máy, và **ghi tay bây giờ là
mất bản ghi** — vì bản trên máy mới là bản không sửa được.

**1. Món có khoảng giá.** Làm theo mục *Món niêm yết theo khoảng giá* ở trên. Vẫn là bạn thoả
thuận giá với khách — chỉ khác là được ghi lại kèm tên người chốt, thay vì nằm trên một tờ phiếu.

**2. Khách khiếu nại.** Vào **Thêm → Khiếu nại**, bấm **“＋ Ghi khiếu nại”**. Gõ **số phiếu**
trên giấy của khách (phiếu của ngày khác thì chọn *Ngày trên phiếu*) rồi bấm Tìm — máy tự chọn
đúng đơn. Đang mở một đơn thì bấm ghi khiếu nại ngay trên đơn đó, đơn đã được chọn sẵn. Gõ nội dung
khách phàn nàn bằng lời, bấm **“Ghi khiếu nại”**. Không phải gõ mã gì cả. Ghi xong máy mở luôn
trang của khiếu nại đó.

> Ghi khiếu nại **chỉ là ghi nhận**: máy không phán ai sai và không tự quyết bồi hoàn. Hai dòng đó
> hiện là *chưa quyết định*, và đó là đúng. Vẫn **báo chủ tiệm ngay trong ngày**.

Những việc thật sự chưa hỗ trợ được liệt kê ở màn hình **Chưa hỗ trợ** trên bảng vận hành.

---

## Bồi hoàn cho khách — làm trên máy, không thoả thuận miệng

Trước đây phần này làm bằng lời rồi quên. Nay bồi hoàn làm ngay trên **trang của khiếu nại**, và
mức chủ tiệm đã chốt được máy chủ tự áp — nhân viên **không gõ mức trần**.

**Thứ tự đúng, bốn bước:**

1. Ghi khiếu nại trước (mục trên). Không có khiếu nại thì không có bồi hoàn. Trang khiếu nại mở ra
   có sẵn mục **Bồi hoàn** bên dưới lời khách.
2. Ở **“Khách được gì?”** bấm chọn loại. **Đọc dòng tóm tắt xong mới nói gì với khách:** máy hiện
   sẵn *“Tối đa … · còn … · cần chủ tiệm duyệt nếu trên …”* — trần, hạn còn bao lâu, và **có phải
   chờ chủ tiệm duyệt hay không** — trước khi có ô nào để gõ số. Đọc bước này là để không lỡ hứa
   với khách một con số rồi mới biết phải chờ chủ tiệm.
3. Chọn món (và **“Món thứ mấy”** nếu có), gõ số tiền nếu loại đó cần, tích ô *“Tôi xác định lỗi
   thuộc về tiệm”*, bấm **“Gửi đề nghị bồi hoàn”**.
4. Đề nghị hiện ở mục **“Đề nghị đã ghi cho sự cố này”** ngay bên dưới. Dòng nào nhân viên được
   duyệt thì có nút **“Thực hiện bồi hoàn”** — bấm là xong. Khiếu nại chỉ đóng khi **mọi** đề nghị
   trên nó đã có kết cục.

**Bốn loại, và ai duyệt:**

| Loại | Máy chủ tính gì | Hạn | Ai duyệt |
|---|---|---|---|
| Giặt lại miễn phí | Không có tiền nào chuyển | 7 ngày kể từ khi khách nhận đồ | Nhân viên |
| Bồi thường món hỏng | Trần = 5 lần phí giặt **một món** (xem dưới) | 24 giờ kể từ khi khách nhận đồ | Tới 100.000đ **cho mỗi món** là nhân viên, trên mức đó **chủ tiệm** |
| Giảm trừ do giao trễ | Máy tính 10% tổng đã thu | Không tính theo hạn, tính theo mức trễ | Theo số tiền, như trên |
| **Mất đồ** | Trần như món hỏng | 24 giờ kể từ khi khách nhận đồ | **Luôn là chủ tiệm**, dù số tiền nhỏ |

**“Phí giặt một món” là gì (DEC-031):** đồ tính theo cái, đôi, bộ thì lấy **giá một cái** — ba
áo sơ mi 50.000đ thì **mỗi áo** tối đa 250.000đ, cả ba áo cộng lại tối đa 750.000đ. Đồ tính theo
ký thì lấy **tiền cả túi** giặt chung, một trần cho cả túi. Máy hiện sẵn phí này, trần, và số đã
ghi đền cạnh từng dòng.

**Dòng có nhiều món tính theo cái thì chọn “Món thứ mấy” (bổ sung DEC-031):** mỗi áo có mức
100.000đ nhân viên được duyệt và trần riêng. Áo thứ 1 đã đền 100.000đ thì áo thứ 2 vẫn còn nguyên
100.000đ của nó. Đề nghị thứ hai cho **cùng một áo** thì cộng dồn với đề nghị trước — chia nhỏ
một áo ra nhiều lần thì phần vượt mức vẫn phải chờ chủ tiệm. Máy hiện sẵn mỗi áo đã ghi bao
nhiêu; những khoản ghi cho cả dòng từ trước khi chọn được từng món được tính cho **mọi** áo trên
dòng. Dòng chỉ có một món thì không cần chọn. Đồ tính theo ký không chọn món: cả túi là một món.

> **Mất đồ: nhân viên ghi và đề nghị số tiền, chủ tiệm duyệt mới được trả.** Màn hình hiện
> **“Mất đồ — luôn chờ chủ tiệm duyệt”** ngay khi chọn loại này. Nói trước với khách là phải chờ,
> đừng hứa con số nào như đã chốt.
>
> **Đơn đã hoàn tiền** vẫn đền được món hỏng hay mất, nhưng mọi khoản đều chờ chủ tiệm duyệt.

**Khoản chờ chủ tiệm — chủ duyệt, rồi nhân viên mới trả:**

1. **Chủ tiệm** mở màn hình **Duyệt** (thẻ **Duyệt bồi hoàn · Phiếu …**). Thẻ phiếu in
   **“Khoản bồi hoàn bạn đang được đề nghị duyệt”**: loại, số tiền, trần, món nào, vì sao cần chủ
   tiệm và khách đã phản ánh gì. Đọc xong rồi bấm **Duyệt** hoặc **Từ chối**. Người đã ghi đề nghị
   thì không tự duyệt được, kể cả chủ tiệm.
   Nếu thẻ hiện **“Khoản bồi hoàn đã đổi so với phiếu”** thì không duyệt được — từ chối, rồi nhờ
   nhân viên đề nghị lại.
2. **Duyệt xong vẫn chưa trả gì.** Người trực quầy (máy nào cũng được, hôm sau cũng được) mở lại
   khiếu nại — ở **Thêm → Khiếu nại**, hoặc **Thêm → Bồi hoàn** (chỉ liệt kê khiếu nại đang mở có
   đề nghị) — rồi ở mục **“Đề nghị đã ghi cho sự cố này”** bấm **“Thực hiện bồi hoàn”** trên đúng
   dòng đó. Nút chỉ hiện ở dòng đã được duyệt mà chưa thực hiện; dòng còn chờ thì ghi rõ là đang
   chờ chủ tiệm, kèm nút **“Chờ chủ tiệm duyệt”** mở màn hình Duyệt.
3. Phải thực hiện **trước khi phiếu duyệt hết hạn** (hết ngày hôm sau). Quá hạn hoặc bị từ chối
   thì dòng đó báo không thực hiện được nữa — bấm **“Đề xuất lại”** trên dòng đó để ghi lại từ đầu.

**Ba điều dễ sai ở màn hình này:**

- **Khách không cần nhớ mã giảm trừ.** Máy in mã to, kèm câu
  **“Chép mã giảm trừ này lại ngay”** và nút chép — chép được thì tốt. Nhưng khách làm mất mã cũng không mất khoản: lần sau,
  danh sách khoản chưa dùng ở **＋ Nhận đồ** ghi số phiếu của đơn đã phát hành từng khoản, nên chỉ
  cần hỏi số phiếu cũ của khách. Mã cũng vẫn xem được ở mục **“Khoản giảm trừ của đơn này”** trên
  trang của đơn.
- **“Chờ chủ tiệm duyệt” nghĩa là chưa xong.** Nói với khách là phải chờ. Bấm thực hiện trước khi
  chủ tiệm duyệt thì máy từ chối. Phiếu duyệt bồi hoàn mở tới **hết ngày hôm sau** (nửa đêm, giờ
  Việt Nam) rồi tự hết hạn — chủ tiệm đi vắng cũng kịp duyệt, nhưng đừng để lâu hơn thế.
- **Vượt trần thì máy từ chối, không tự hạ xuống.** Nếu màn hình báo vượt trần, nói đúng con số
  trần cho khách nghe. Gõ một số khác cho lọt là tự quyết thay chủ tiệm.

**Khách quay lại dùng phiếu giảm trừ:** tính giá như bình thường ở **＋ Nhận đồ**, rồi — **trước
khi khách đồng ý** — bấm **Dùng khoản giảm trừ** dưới hoá đơn tạm. Máy liệt kê các khoản chưa dùng
của tiệm, mỗi dòng là *Phiếu N · ngày · số tiền*; nhiều hơn năm khoản thì gõ số phiếu cũ của khách vào
ô lọc. Bấm đúng dòng là xong — tổng mới hiện ngay trên hoá đơn. Chỉ khi khách đọc được mã mà không
nhớ phiếu mới mở *Nhập mã thủ công* và bấm **“Áp dụng khoản giảm trừ”**. Bản báo giá được máy gửi
kèm, không phải chép gì khác. Phiếu dùng **đúng một lần**. Đọc lại tổng mới cho khách nghe rồi mới
bấm **Tiếp tục**.

---

## Khách đã nhắn “dừng” — gửi tin dịch vụ (`DEC-033`)

Khách nhắn **dừng / STOP** trên kênh nào thì tiệm **không chủ động gửi gì** trên kênh đó — kể cả tin
báo đồ đã xong. Ở *Ngoại lệ & gửi tay → Gửi tay*, thẻ *Tin dịch vụ cho khách này* nói rõ lý do và nút
**Xin duyệt** bị khoá, lý do ghi ngay dưới nút (máy vẫn kiểm tra lại ở mỗi bước):

| Máy nói | Làm gì |
|---|---|
| **Khách đã yêu cầu dừng nhận tin trên kênh này** | Không gửi. Nếu khách nhắn lại cho tiệm, chủ tiệm hoặc người duyệt bấm **“Gỡ chặn tin dịch vụ”**, chọn **đúng tin nhắn mới đó** (theo giờ khách nhắn) trong danh sách máy đưa ra, rồi bấm **“Gỡ chặn tin dịch vụ”** lần nữa để xác nhận. Không gỡ theo lời kể hay cuộc gọi. Tin quảng cáo vẫn bị chặn |
| **Khách vừa nhắn một câu có thể là yêu cầu dừng** | Chủ tiệm hoặc người duyệt đọc lại rồi quyết |
| **Chủ tiệm chưa công bố chính sách tin dịch vụ** | Báo chủ tiệm. Chỉ chủ tiệm công bố được |
| **Chưa có căn cứ để gửi tin dịch vụ** | Khách không nhắn gần đây và không có đơn đang mở. Đợi khách nhắn cho tiệm trước |

Nếu lỡ gửi tay rồi mới thấy máy từ chối ghi nhận: **báo chủ tiệm ngay**, đừng tìm cách ghi lại.

**Gửi tay một tin đã duyệt** — bốn bước trên cùng một trang, không phải gõ mã nào:

1. **Đọc tin sẽ gửi** — chọn bản nháp đã duyệt (hoặc bấm *Xin duyệt gửi tay* ở *Bản nháp AI*).
2. **Xin duyệt** — một người khác (không phải bạn) duyệt ở màn *Duyệt*.
3. **Khoá phong bì** — chỉ khoá khi chính bạn sẽ gửi tin đó.
4. **Ghi nhận đã gửi** — gửi xong bấm *Vừa gửi xong*, rồi chứng thực. Ghi nhận **không có nghĩa là
   khách đã nhận**.

Người duyệt duyệt ở máy khác cũng không sao: mở lại bản nháp là trang tự nhảy tới đúng bước, mọi mã
đã điền sẵn — chờ duyệt, bấm **Khoá phong bì cho người gửi tay**, hay ghi nhận. Tin đã đổi hoặc phiếu hết
hạn thì bấm **Xin duyệt lại**. *Nhập mã thủ công* chỉ dùng cho một phiếu khác phiếu mới nhất.

---

## Khi có sự cố

| Màn hình nói | Nghĩa là | Làm gì |
|---|---|---|
| **Đang ngoại tuyến** | Máy mất mạng. Không có lệnh nào được gửi đi | Kiểm tra mạng. Trong lúc đó ghi tay, nhập lại sau khi có mạng |
| **Chưa biết lệnh có tới máy chủ hay không** | Lệnh có thể đã chạy | **Đừng bấm lại.** Tải lại đơn và xem trạng thái thật |
| **Đơn này vừa được người khác đổi** | Người kia bấm trước | Bấm **Đơn vừa đổi — tải lại**, rồi làm theo nút mới |
| **Máy chủ không ghi nhận khoản này** | Số tiền hoặc tình huống không được hỗ trợ | Đọc lý do ngay bên dưới — nó nói rõ phải làm gì |
| **Chưa nhận đồ được** | Đơn còn thiếu điều kiện (thường là chưa tích *Tiệm làm kịp đơn này*) | Đọc từng dòng lý do ngay bên dưới, làm đúng việc đó rồi bấm lại |
| Huỷ một đơn đã nhận đồ | Phải nói đồ và tiền đã xử lý thế nào | **Khác** → **Huỷ đơn**, chọn một cách xử lý, bấm hai lần |
| **Phiên đăng nhập đã kết thúc** | Hết phiên | Đăng nhập lại. Không mất dữ liệu |
| Mất điện thoại, hoặc quên thoát ở máy khác | Máy đó vẫn đang đăng nhập | Báo chủ tiệm. Chủ mở **Nhân sự** → bấm tên người đó → **Thiết bị đang đăng nhập** → **Đăng xuất thiết bị này** (bấm hai lần). Các máy khác của người đó vẫn làm việc bình thường. Chủ tự mất máy: bấm tên mình ở thanh trên cùng → cùng danh sách, dòng **Thiết bị này** là máy đang cầm |
| **Đơn đã thay đổi sau khi gửi duyệt — mở đơn để xem lại** (thẻ duyệt) | Đơn đã chuyển bước sau khi xin duyệt, nên phiếu này không còn đúng | Nút **Duyệt** khoá. Bấm **Từ chối**, mở đơn xem lại; còn cần thì xin duyệt lại. Thẻ ghi **Đơn chưa thay đổi kể từ khi gửi duyệt** thì duyệt bình thường |
| **Máy chủ gặp lỗi** | Lỗi thật | **Đừng thử lại.** Xem bảng đơn để biết lệnh đã vào hay chưa, chụp màn hình, báo chủ tiệm |

**Quy tắc chung khi hoang mang:** không bấm lại lệnh vừa hỏng. Mở bảng đơn, đọc trạng thái thật của
đơn, rồi mới quyết định. Mỗi lỗi đều có **mã theo dõi** trong ô *Chi tiết kỹ thuật* của lỗi — mở ra, chụp lại, đó là thứ tra ra được chuyện gì
đã xảy ra.

---

## Trong tuần thử nghiệm, nhớ ghi lại

Những số này không có cách nào đo lại về sau, và chúng mở khoá phần tiếp theo của hệ thống:

- **10 mẻ giặt sấy có bấm giờ** — từ lúc bắt đầu tới lúc xong, giờ thật.
- **20 chuyến giao** — quãng đường và thời gian.
- Mỗi ngày: có gì trên máy làm chậm việc ở quầy hơn là làm tay.
