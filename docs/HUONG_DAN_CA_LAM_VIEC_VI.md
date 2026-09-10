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

## Hai việc tuần này vẫn ghi ra sổ

Không phải máy hỏng — chủ tiệm chưa chốt hai câu hỏi, nên phần mềm cố ý không đoán.

**1. Món có khoảng giá.** Vest, áo khoác, áo dài thêu, giày da, giày lộn, túi da, gối, ruột gối,
thú bông, ghế sofa, thảm, và xử lý vết bẩn — 20 trong 43 dịch vụ được niêm yết theo **khoảng giá**.
Màn hình cố ý không tự chọn một con số trong khoảng.

> Thoả thuận giá với khách, **ghi giá lên phiếu, giữ lại phiếu**, và báo chủ tiệm cuối ngày.
> Giặt sấy theo kg và các món giá cố định vẫn báo giá bình thường trên máy.

**2. Khách khiếu nại.** Màn hình **Sự cố** chưa dùng được — nó đòi hai mã mà không chỗ nào trong hệ
thống sinh ra.

> Ghi khiếu nại **ra sổ kèm số phiếu**, và **báo chủ tiệm ngay trong ngày**.

Cả hai đều được ghi ở màn hình **Chưa hỗ trợ** trên bảng vận hành.

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
