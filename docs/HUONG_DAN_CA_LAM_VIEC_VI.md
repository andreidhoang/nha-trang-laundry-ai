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
| 11 | Khách tới lấy → chuyển **sản xuất** sang *Đã giao ra* | Đơn hàng |
| 12 | Thu tiền → nhập số tiền, tích **Khách đã tự lấy đồ**, bấm **Ghi nhận tất toán** | Chi tiết đơn |
| 13 | Chuyển **thương mại** sang *Hoàn tất* | Đơn hàng |

### Món niêm yết theo khoảng giá (áo dài, vest, giày da, sofa, thảm…)

Hai mươi trong bốn mươi bốn dịch vụ được niêm yết theo **khoảng giá**, vì phải nhìn món đồ mới
định giá được. Bước 4 ở trên sẽ báo là máy không tự chọn số. Làm tiếp như sau, vẫn ở màn hình
**Báo giá**:

| | Làm gì |
|---|---|
| 4a | Bấm **Lập bản khoảng giá**. Đọc khoảng cho khách nghe (ví dụ *80.000 – 240.000 ₫*) |
| 4b | Xem đồ, thoả thuận với khách, gõ **một số nằm trong khoảng** cho từng dòng |
| 4c | Bấm **Gửi giá cho chủ duyệt**. Gọi chủ tiệm — phiếu duyệt chỉ sống **mười phút** |
| 4d | Chủ tiệm mở màn hình **Duyệt**, mở bản báo giá xem, rồi bấm **Duyệt** |
| 4e | Bạn bấm **Áp dụng giá đã duyệt** → quay lại bước 5 ở bảng trên |

- **Người gửi không tự duyệt được.** Máy chủ từ chối. Phải là hai người.
- **Đừng rời màn hình Báo giá** giữa 4c và 4e: những con số chỉ còn ở màn hình đó. Rời đi thì gửi
  lại từ đầu — không mất gì, vì lúc đề nghị chưa có giá nào được ghi.
- **Quá mười phút** thì phiếu chết hẳn, không gia hạn được. Gửi lại từ đầu.
- Gõ số ngoài khoảng thì màn hình cảnh báo **ngay khi đang gõ**, trước khi gửi. Đừng lách: muốn ra
  ngoài khoảng thì chủ tiệm phải công bố lại bảng giá.

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
thuận giá với khách và chủ tiệm gật đầu — chỉ khác là được ghi lại kèm tên người chốt, thay vì
nằm trên một tờ phiếu.

**2. Khách khiếu nại.** Màn hình **Sự cố** dùng được rồi: chọn đơn, gõ nội dung khách phàn nàn
bằng lời, bấm ghi nhận. Không phải gõ mã gì cả — máy chủ tự sinh.

> Mở sự cố **chỉ là ghi nhận**: máy không phán ai sai và không tự quyết bồi hoàn. Hai dòng đó hiện
> là *chưa quyết định*, và đó là đúng. Vẫn **báo chủ tiệm ngay trong ngày**.

Những việc thật sự chưa hỗ trợ được liệt kê ở màn hình **Chưa hỗ trợ** trên bảng vận hành.

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
