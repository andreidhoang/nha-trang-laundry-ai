# Thông báo về thông tin khách hàng — bản nháp V1 (`DEC-034`)

**Trạng thái: BẢN NHÁP, CHƯA CÓ HIỆU LỰC.** Bản này do đội kỹ thuật soạn từ những gì chủ tiệm đã xác
nhận trong `BUSINESS_TRUTH_INTAKE.md` (tên pháp nhân, mã số thuế, địa chỉ, hotline) và từ quyết định
`DEC-034`. Nó chỉ có hiệu lực khi **chủ tiệm tự chạy** `scripts/publish_privacy_notice.py`. Trước lúc
đó, máy chủ từ chối tạo hồ sơ khách với mã `PRIVACY_NOTICE_UNPUBLISHED` và quầy vẫn phát phiếu cho
khách như bình thường.

Phần nằm giữa hai dấu `notice:start` / `notice:end` là **đúng nguyên văn** điều khách được đọc (trên
màn hình quầy, và in/dán ở quầy nếu chủ tiệm muốn). Ba câu ngắn bên dưới là câu nhân viên đọc cho khách
và hai ô đánh dấu trên màn hình. Kịch bản công bố đọc đúng các phần này từ tệp này, không đọc chỗ nào
khác.

## Thông báo (khách đọc)

<!-- notice:title -->
Thông tin của anh/chị ở tiệm Giặt Là Sạch Cộng
<!-- /notice:title -->

<!-- notice:start -->
**Ai giữ thông tin.** CÔNG TY TNHH A & T CARE (MST 4202059758), vận hành tiệm Giặt Là Sạch Cộng, 3A Lê
Đại Hành, Phường Nha Trang, Khánh Hòa. Hotline và Zalo: 0382 318 492.

**Tiệm lưu gì.** Số điện thoại của anh/chị; tên gọi anh/chị cho biết (nếu có); địa chỉ giao đồ (nếu
có); một ghi chú ngắn về cách giặt đồ (ví dụ "giặt riêng đồ trắng"). Tiệm không ghi điều gì về sức
khoẻ, tôn giáo hay chuyện riêng tư khác.

**Để làm gì.** Để nhận ra anh/chị khi quay lại, gọi hoặc nhắn khi đồ xong, giao đồ tận nơi, và xem lại
các đơn trước của anh/chị. Tiệm chỉ gửi tin ưu đãi khi anh/chị đồng ý riêng việc đó, và anh/chị thôi
nhận lúc nào cũng được.

**Giữ an toàn.** Số điện thoại được mã hoá khi lưu. Chỉ nhân viên của tiệm xem được. Tiệm không bán
và không đưa thông tin của anh/chị cho ai để quảng cáo.

**Giữ bao lâu.** Đến khi anh/chị yêu cầu xoá, hoặc tiệm tự xoá sau 24 tháng anh/chị không có đơn nào.
Khi xoá, đơn hàng và sổ tiền của tiệm vẫn còn, nhưng không còn tên, số điện thoại hay địa chỉ của
anh/chị.

**Anh/chị có quyền.** Xem, sửa, hoặc yêu cầu xoá thông tin của mình bất cứ lúc nào: nói với nhân viên
ở quầy, hoặc gọi/nhắn Zalo 0382 318 492. Không đồng ý cũng không sao — tiệm vẫn nhận đồ và phát phiếu
cho anh/chị như bình thường.
<!-- notice:end -->

## Câu nhân viên đọc cho khách

<!-- notice:consent-sentence -->
Dạ, tiệm xin lưu số điện thoại (và tên, địa chỉ nếu anh/chị cho) để lần sau nhận ra anh/chị, báo khi đồ xong và giao đồ. Anh/chị xoá lúc nào cũng được. Anh/chị đồng ý không ạ?
<!-- /notice:consent-sentence -->

<!-- notice:service-consent-label -->
Khách đã nghe và đồng ý cho tiệm lưu thông tin để phục vụ đơn
<!-- /notice:service-consent-label -->

<!-- notice:marketing-consent-label -->
Khách muốn nhận tin ưu đãi của tiệm (không bắt buộc)
<!-- /notice:marketing-consent-label -->

## Ghi chú cho chủ tiệm (không phải phần khách đọc)

- **Công bố là chủ tiệm xác nhận nội dung trên.** Phần mềm không tự khẳng định căn cứ pháp lý nào.
  Nghị định 13/2023/NĐ-CP và Luật Bảo vệ dữ liệu cá nhân (hiệu lực từ 2026) yêu cầu khách được báo
  lưu gì, vì sao, và đồng ý. Việc doanh nghiệp có phải lập hồ sơ đánh giá tác động xử lý dữ liệu cá
  nhân hay được miễn theo quy mô nhỏ là việc của chủ tiệm và người tư vấn — phần mềm không nói là có
  hay không.
- **Cam kết trong thông báo là cam kết của tiệm.** "Không bán và không đưa cho ai để quảng cáo",
  "chỉ nhân viên xem được" và thời hạn 24 tháng: phần mềm làm đúng ba điều này (mã hoá số điện thoại,
  chỉ vai trò vận hành xem được số, không có nút xuất danh sách khách, xoá tự động sau 24 tháng không
  có đơn khi chủ tiệm chạy `scripts/run_customer_retention.py`). Nếu tiệm muốn làm khác, sửa thông báo
  trước khi công bố.
- **Cách công bố.** `DATABASE_URL=... uv run python scripts/publish_privacy_notice.py --actor-id
  <mã nhân sự của chủ tiệm>`. Chỉ tài khoản chủ tiệm đang hoạt động công bố được. Công bố lại đúng bản
  đang có hiệu lực thì không thay đổi gì; sửa chữ rồi công bố lại là một phiên bản mới, và từ đó hồ sơ
  mới ghi theo phiên bản mới.
- **Thời hạn 24 tháng** là của `DEC-034`; kịch bản công bố từ chối một thông báo nói thời hạn khác.
