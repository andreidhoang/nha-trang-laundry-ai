/**
 * Chưa hỗ trợ: the catalogue of everything the specification asks for that this console cannot do.
 *
 * This screen is a deliverable, not an apology. `IMPLEMENTATION_ROADMAP_V1.md:394` makes "no hidden
 * unsupported default" an exit criterion for the M3 milestone, and the failure mode it guards
 * against is specific: a console that quietly omits payment because payment does not exist reads as
 * finished, and the missing capability gets filled with a paper note nobody reconciles. So every
 * gap is written down, by name, with what blocks it.
 *
 * Three rules govern the content:
 *
 *   - **Every entry is concrete.** Each names the aggregate, route, decision id or queue item that
 *     is actually absent. "Chưa làm" is not an entry; "không có route nào tạo một dòng
 *     `staff_store_assignments`" is.
 *   - **No entry promises a date.** A blocker is a fact about today. A date would be a commitment
 *     this screen has no standing to make, and a stale one teaches staff to distrust the rest.
 *   - **The vocabulary is the same one `unsupported()` uses** — what the spec asks, what is
 *     missing, what blocks it, what to do today — so a full-screen refusal and a line in this
 *     catalogue read as the same statement at two sizes. The cards are built here rather than by
 *     calling `unsupported()` once per entry because seventeen full screens is not a catalogue.
 *
 * Nothing here fetches. There is no request, no `Submission` and no loading state in this module,
 * because every fact on it is a fact about code that does not exist.
 *
 * @module screens/gaps
 */

import { h } from "../core/dom.js";
import { facts } from "../ui/components.js";
import { infoButton, page, section } from "../ui/kit.js";

/**
 * One gap.
 *
 * @typedef {object} Gap
 * @property {string} ref the specification pointer, shown verbatim as the card's eyebrow
 * @property {string} title
 * @property {string} what what the specification asks for
 * @property {string} missing the concrete thing that does not exist
 * @property {string} blockedBy the decision id, queue item or absent route
 * @property {string} [today] the honest interim procedure
 * @property {string} [note] a constraint that will apply when this is eventually built
 * @property {{href: string, label: string}} [link] a screen that says more about this gap
 *
 * Entries are plain data — strings, never nodes — so the table can be read, diffed and tested
 * without a document, and so nothing in it is a live DOM node shared between two renders.
 */

/**
 * The catalogue, grouped the way an operator would ask about it: first the order's life, then what
 * can be measured about it, then the channels it arrives on, then who is allowed to decide.
 *
 * @type {{heading: string, lede: string, entries: Gap[]}[]}
 */
const GROUPS = [
  {
    heading: "Vòng đời đơn hàng",
    // COUNTER-DEFECTS-001 corrected this lede from denying four dimensions to denying one, and
    // over-corrected: it counted the "Tiếp nhận" screen as the intake surface. That screen creates
    // an order_request and issues a counter ticket — a different aggregate, which never touches
    // the order's intake dimension. So the lede claimed the console covered "việc nhận đồ" while
    // no screen called /intake-transition, and intake is the blocking dimension: an order cannot
    // reach ACTIVE until it is ACCEPTED. A register of what is unsupported is a compliance
    // surface, and a false claim of *support* is the worse direction — staff plan a day around it.
    //
    // CONSOLE-LIFECYCLE-001 built both missing surfaces, so this lede no longer denies a
    // dimension. It says what is true now, and the reason it is worth saying is that it was
    // wrong in both directions within one week.
    //
    // CONSOLE-REDESIGN-002 removed that generic "Chuyển trạng thái đơn" form: each order's page now
    // offers the steps the server computes as legal (ORDER-STEPS-001). Two targets the old form
    // reached -- a rewash and refusing the goods -- had no step, and were listed here until
    // ORDER-STEPS-002 made them "Giặt lại" and "Không nhận đồ" under "Khác", each with a reason.
    lede:
      "Bảng vận hành hôm nay bao được cả bốn chiều của một đơn tại quầy: thương mại, nhận đồ, " +
      "sản xuất và tất toán, cùng chặng giao. Mỗi việc là một nút trên trang của đơn: nút lớn " +
      "là bước tiếp theo máy chủ cho phép, các bước khác nằm ở “Khác” — kể cả “Giặt lại” và " +
      "“Không nhận đồ”.",
    entries: [
      {
        ref: "M3 · MÀN 2",
        title: "Khách hàng / hỏi mới",
        what:
          "Tạo và tra cứu khách hàng cùng số liên hệ và địa chỉ của họ, để một hỏi mới gắn được " +
          "vào đúng người.",
        // CUSTOMER-001 (DEC-034, 25/09) built the customer record DEC-015 had declined: this entry
        // said "hệ thống không lưu tên, số điện thoại hay địa chỉ của khách", and that stopped being
        // true. What is still absent is the spec's party model -- several numbers and addresses per
        // customer -- and a console control to attach an old ticket or a chat to a record, which the
        // server does (`POST …/customers/{id}/links`) and no screen offers yet.
        missing:
          "Hồ sơ khách đã có (bảng customers, DEC-034): một số điện thoại, tên gọi, một địa chỉ và " +
          "ghi chú. Chưa có aggregate parties, contact_points hay addresses như đặc tả — một khách " +
          "chỉ có một số và một địa chỉ — và chưa có nút gắn một phiếu cũ hay một kênh chat vào hồ " +
          "sơ: máy chủ làm được, màn hình chưa có.",
        blockedBy:
          "DEC-034 (đã chốt 25/09) chọn một hồ sơ cho mỗi số điện thoại; nhiều số hay nhiều địa " +
          "chỉ cho một khách chưa có quyết định",
        today:
          "Tìm khách ở ô “SĐT hoặc tên khách” của Nhận đồ hoặc ở màn Khách hàng. Khách mới: “Thêm " +
          "khách mới” (sau khi chủ tiệm công bố thông báo bảo mật). Khách vãng lai vẫn nhận số phiếu.",
      },
      {
        ref: "M3 · MÀN 9",
        title: "Chặng giao hàng",
        what:
          "Gom đơn thành chuyến, chia chặng lấy và chặng trả, và ghi quãng đường đã đo cho từng " +
          "chặng.",
        missing:
          "Ghi nhận chuyến giao đã có: delivery_legs (DEC-023, 26/08) — ghi đồ đã đến tay khách " +
          "hay chưa, và chuyến trả thành công thì đóng được đơn. Còn thiếu delivery_bundles để " +
          "gom nhiều đơn vào một chuyến, và distance_measurements để lưu quãng đường đã đo — " +
          "hiện quãng đường do nhân viên nhập từng lần báo giá.",
        blockedBy: "Chưa có nguồn nào tạo delivery_bundles hay distance_measurements",
      },
      {
        ref: "M3 · MÀN 10",
        title: "Thanh toán / tất toán",
        what: "Ghi khoản phải thu, ghi khoản đã thu, phân bổ tiền thu vào từng khoản, và tất toán đơn.",
        missing:
          "Trả đủ đúng số khi khách tự lấy đã có: khối tất toán trong màn hình chi tiết đơn " +
          "(SETTLEMENT-001, đã hoàn thành). Còn thiếu charges, payments và payment_allocations " +
          "tổng quát, nên trả một phần, trả thừa và ghi nợ vẫn không biểu diễn được.",
        // DEC-010 is RESOLVED, not open: the owner deliberately deferred partial payment,
        // deposits, instalments and ON_ACCOUNT credit on 18/08 rather than leaving them unanswered.
        // Calling it open told staff a decision was still coming when the answer is "not yet, on
        // purpose" — which is a different thing to plan around.
        blockedBy:
          "DEC-010 (đã quyết, hoãn có chủ đích) — trả một phần, đặt cọc, trả góp và ghi nợ không " +
          "được hỗ trợ vì chủ tiệm quyết như vậy, không phải vì còn thiếu. Trả đủ đúng số không " +
          "bị chặn.",
      },
      {
        ref: "M3",
        title: "Bàn giao / custody",
        what:
          "Theo dõi từng kiện đồ của khách khi nó đổi tay hoặc đổi chỗ, để lúc nào cũng trả lời " +
          "được “đồ của khách đang ở đâu”.",
        missing: "Không có custody_units, custody_events hay batches.",
        blockedBy: "SHOP-INSTRUMENT-001",
      },
      // "Tra lại một khoản giảm trừ chưa dùng" and "Danh sách đề nghị bồi hoàn của một sự cố" were
      // here until READ-PATHS-001, and are deleted rather than reworded because both gaps closed.
      // `GET /internal/v1/stores/{store}/orders/{order}/remedy-credits` lists the credits an order
      // issued, spent or not, and the order detail shows them with a copy control on an unused
      // code -- the customer keeps that order's ticket, and the orders screen finds an order by its
      // ticket number, so a lost code is found again the way the counter already works.
      // `GET …/incidents/{incident}/remedy-proposals` lists every proposal on an incident,
      // including a pre-DEC-031 loss with no figure, and `#/remedies` shows it for the incident it
      // reads. Neither creates a customer record, so DEC-015 is untouched.
      //
      // CREDIT-PICK-001 finished the first of those: the receipt's "Dùng khoản giảm trừ" now lists
      // the store's unused credits (`GET …/stores/{store}/remedy-credits`) with the ticket each was
      // issued on, and a tap applies one -- the code is typed only under "Nhập mã thủ công".
      //
      // REMEDY-OWNER-DECIDE-001 closed a gap this register never listed, and it is recorded here so
      // nobody re-adds it: an `APPROVE_REMEDY` envelope (every loss since DEC-031, every claim on a
      // refunded order, anything above the staff limit) reached #/approvals undecidable, and an
      // approved claim could be paid only from the browser session that proposed it. The approvals
      // card now reads `GET …/stores/{store}/remedy-proposals/{proposal}/approval-binding`, and the
      // incident's proposal list on #/remedies offers "Thực hiện bồi hoàn" on every row the server
      // marks `next_step: EXECUTE`.
    ],
  },
  {
    heading: "Đo lường và báo cáo",
    // REPORT-DASHBOARD-001 built the versioned report read model this lede said did not exist, so
    // the lede now says what is still missing: the figures that need a measurement nobody has
    // taken yet, or a decision nobody has made.
    // PROMISE-001: the on-time figure now counts against each order's first promise.
    // SHOP-CAPTURE-001 (DEC-038) added the shop's own measurements to it -- machines and cycles,
    // trip costs, Sổ thu chi, margin when complete.
    lede:
      "Màn Báo cáo có số của chủ: đơn, đúng hẹn theo giờ hẹn đầu tiên với khách, giặt lại, khiếu " +
      "nại, tiền đã thu và bồi hoàn — mỗi số kèm tử số, mẫu số, khoảng ngày và phiên bản truy vấn " +
      "— cùng số tiệm tự đo: mẻ có ghi máy, phút mỗi mẻ, chi phí giao mỗi đơn, chi theo mục và " +
      "biên của tháng khi đủ số liệu. Các mục dưới đây vẫn thiếu vì cần số đo thật hoặc một quyết " +
      "định chưa ai đưa ra.",
    entries: [
      // "Xem lại nguồn khách đã ghi trên một đơn" (ACQUISITION-ATTRIBUTION-001) was here until
      // READ-PATHS-001 put `acquisition_source` on the order read model, and is deleted rather than
      // reworded because the gap closed: the order detail shows the recorded source read-only. It
      // is still immutable -- a mis-tap is now visible and still cannot be corrected, which the
      // detail says beside the value and the order form says before it is chosen. The channel
      // report remains scripts/report_acquisition_sources.py.
      // SHOP-CAPTURE-001 (DEC-038) built the machine and cycle half: "Máy nào?" at Bắt đầu giặt, the
      // cycle closed at Giặt xong, the owner's machine list, and per-machine minutes on Báo cáo. The
      // entry stays for what is still true: labour minutes are not recorded, by decision, and the
      // capacity figures need weeks of real cycles before SHOP-INSTRUMENT-001 can use them.
      {
        ref: "M3 · MÀN 12",
        title: "Ghi nhận máy / mẻ / phút công",
        what: "Ghi lại máy nào chạy mẻ nào, trong bao lâu, và tốn bao nhiêu phút công của ai.",
        missing:
          "Máy và mẻ đã được ghi: chọn máy khi bấm Bắt đầu giặt, mẻ đóng khi bấm Giặt xong, phút " +
          "mỗi mẻ ở màn Báo cáo. Phút công theo đơn thì không ghi — DEC-038 quyết không bắt hai " +
          "người bấm giờ từng việc; lương vào Sổ thu chi.",
        blockedBy: "SHOP-INSTRUMENT-001 (cần 4–6 tuần mẻ giặt ghi thật trước khi tính công suất)",
        today: "Chọn máy mỗi lần bấm Bắt đầu giặt; không kịp thì bấm Bỏ qua, mẻ vẫn được đếm.",
        link: { href: "#/machines", label: "Máy giặt, sấy" },
      },
      // SHOP-CAPTURE-001 built the capture: vehicle, km, money and a note on the leg sheet, and
      // the delivery cost per delivered order on Báo cáo. What is still missing is the comparison
      // this entry was written for, and the real trips the pilot gate counts.
      {
        ref: "M3 · MÀN 13",
        title: "Ghi nhận chi phí giao hàng",
        what: "Ghi chi phí thật của một chuyến giao, để đối chiếu với phí giao đã thu của khách.",
        missing:
          "Chi phí từng chuyến đã ghi được khi bấm Lấy được đồ hoặc Giao thành công (xe, km, " +
          "tiền), và Báo cáo có chi phí giao mỗi đơn. Chưa có: đối chiếu chi phí đó với phí giao " +
          "đã thu của từng đơn.",
        blockedBy: "SHOP-INSTRUMENT-001 (cổng thí điểm cần 20 chuyến ghi thật)",
        today: "Mở “Chi phí chuyến” trên phiếu giao và ghi tiền mỗi chuyến, cả chuyến giao hụt.",
      },
      // "Bảng điều hành hằng ngày" (M3 · MÀN 14, FR-RPT-001/-005) was here until
      // REPORT-DASHBOARD-001 built `report-v3` and the #/reports screen: the funnel, the on-time
      // rate against the board's stated rule, rewash, complaints, money collected and remedies,
      // each as numerator / denominator / window / data quality / query version. What that entry
      // still covered and the report cannot show is margin, so the entry is narrowed to it rather
      // than deleted: the owner's dashboard exists, its margin tile does not.
      // SHOP-CAPTURE-001 (DEC-038) built margin by month, only when the month is complete. The
      // entry narrows to what is left: margin per order.
      {
        ref: "M3 · MÀN 14 · FR-RPT-002",
        title: "Biên lợi nhuận",
        what: "Biên lợi nhuận theo đơn và theo kỳ, kèm cờ cho biết số liệu đã đủ hay chưa.",
        missing:
          "Biên theo tháng đã có ở Báo cáo, nhưng chỉ khi Sổ thu chi của tháng có đủ điện, nước, " +
          "hoá chất, lương và mặt bằng; thiếu mục nào thì báo chưa đủ số liệu và nêu mục thiếu. " +
          "Chưa có biên theo từng đơn: chưa có quy tắc chia chi phí chung cho từng đơn.",
        blockedBy: "SHOP-INSTRUMENT-001 (4–6 tuần số liệu thật)",
        today:
          "Ghi mọi khoản chi vào Sổ thu chi. Biên của tháng không phải lợi nhuận: chưa tính khấu " +
          "hao máy và thuế.",
        link: { href: "#/expenses", label: "Sổ thu chi" },
      },
      {
        // OPS-BOARD-001 built the narrow half of this, so the sentence changed with it. What
        // exists is one day of one cửa hàng, chủ tiệm duyệt từng lần, và chỉ những cột đã nêu.
        // What is still missing is the reporting export this entry was written about: mọi khoảng
        // thời gian, và các chỉ số của mục trên. Xoá cả mục này sẽ nói rằng phần đó đã xong.
        //
        // The CSV note went the same way, and for the better reason: it became code.
        // `SanitizedExportRepository._cell` refuses a cell beginning with =, +, - or @, so the
        // warning is enforced rather than remembered.
        //
        // EXPORT-RANGE-001 closed the range half: #/exports picks a window of up to 92 days and
        // the owner approves both ends. What is left is the KPI half -- the file is still the
        // orders' raw records, one row per order, and carries no metric. The entry stays for
        // that half only, and says so; it is not retired for something the file does not do.
        ref: "FR-RPT-003 · FR-RPT-004",
        title: "Chỉ số vận hành trong tệp xuất",
        what:
          "Xuất số liệu vận hành cho một khoảng thời gian tự chọn, kèm các chỉ số của bảng điều " +
          "hành, để gửi cho kế toán.",
        missing:
          "Khoảng ngày thì đã chọn được (tối đa 92 ngày), nhưng tệp chỉ mang hồ sơ thô của từng " +
          "đơn: mã đơn, trạng thái, mốc thời gian, tiền đã thu và đã hoàn. Tệp không kèm chỉ số " +
          "nào — không đơn mới, không tỉ lệ đúng hẹn, không giặt lại.",
        blockedBy:
          "Chưa làm: tệp xuất chỉ có một bộ dữ liệu (hồ sơ đơn); bộ mang chỉ số chưa được xây",
        today:
          "Xuất hồ sơ đơn của một ngày hoặc một khoảng tới 92 ngày; mỗi lần xuất cần chủ tiệm " +
          "duyệt đúng khoảng đó rồi mới có tệp. Tệp không kèm lời khách phàn nàn và mô tả bằng " +
          "chứng. Các chỉ số của cùng khoảng ngày xem ở màn hình Báo cáo.",
      },
      {
        // PROMISE-001 (DEC-037) built the per-order promise this entry was about: every order
        // taken after the owner publishes the turnaround rules gets a promised-ready time at Nhận
        // đồ, the SLA board ranks by it, and the report's on-time figure counts against the first
        // one. What DEC-037 deliberately left for later is the owner's late-delivery credit, so the
        // entry now names that, and nothing else.
        ref: "FR-RPT-006 · DEC-037",
        title: "Bù 10% khi giao trễ hẹn",
        what:
          "Giao tận nơi trễ hơn 2 giờ so với giờ đã hẹn thì khách được giảm 10% đơn sau (lời chủ " +
          "tiệm).",
        missing:
          "Mỗi đơn đã có giờ hẹn trả tại tiệm, nhưng chưa có giờ hẹn giao tận nơi, và khoản giảm " +
          "10% không được tạo tự động.",
        blockedBy:
          "DEC-037 đã chốt: đợt này chỉ hẹn và đo, chưa tự bù; nối vào Bồi hoàn là bước sau",
        today:
          "Đơn trễ hẹn hiện nhãn “Trễ hẹn” ở Đơn hàng và Bảng trễ hạn. Muốn bù cho khách thì tạo " +
          "khoản bù ở màn Bồi hoàn như trước.",
      },
    ],
  },
  {
    heading: "Kênh và AI",
    // REPORT-DASHBOARD-001: versioned metrics now exist for an AI to cite, so the lede's second
    // half moved from "nothing to summarise" to what really blocks it: no model may run.
    lede:
      "Chưa có kênh nào nối vào hệ thống, nên chưa có tin nhắn nào để hợp nhất. Số liệu có phiên " +
      "bản đã có ở màn Báo cáo, nhưng chưa có mô hình AI nào được phép chạy để tóm tắt chúng.",
    entries: [
      {
        ref: "FR-RPT-006",
        title: "Hộp thư hợp nhất / tình trạng kênh",
        what:
          "Một hộp thư gộp mọi kênh khách nhắn tới, kèm tình trạng kết nối của từng kênh.",
        missing: "Chưa có channel adapter nào, và chưa có nhà cung cấp nào ở đầu bên kia.",
        blockedBy: "CHANNEL-ZALO-APPLY-001 / CHANNEL-TELEGRAM-001",
      },
      {
        ref: "FR-RPT-007",
        title: "Tóm tắt vận hành bằng AI",
        what: "Một đoạn tóm tắt tình hình trong ngày, viết bằng AI, cho chủ đọc buổi tối.",
        // REPORT-DASHBOARD-001 built the versioned metrics this used to wait for; what blocks it
        // now is that no model is authorised to run at all.
        missing:
          "Chưa có mô hình AI nào được phép chạy: cả 13 năng lực AI đang ở trạng thái " +
          "NOT_AUTHORIZED. Số liệu để trích dẫn thì đã có, ở màn Báo cáo.",
        blockedBy: "DEC-006 (chưa có mô hình được uỷ quyền)",
        note:
          "Theo đặc tả, AI chỉ được trích dẫn read model có phiên bản. Nó không được tự tính chỉ " +
          "số, không được sinh SQL, không được chọn định danh và không được thay đổi trạng thái.",
      },
    ],
  },
  {
    heading: "Duyệt và phiên",
    // MESSAGE-DRAFT-BINDING-001 retired the "Duyệt một tin nhắn soạn sẵn" entry, so four became
    // three; SESSION-LIST-001 retired two more, so three became one. The lede counts its entries
    // and has been wrong about that count once already.
    lede:
      "Mục dưới đây không thiếu quyết định kinh doanh nào. Nó thiếu đúng những trường mà API " +
      "không trả về, và một bề mặt đoán bừa các trường đó sẽ là duyệt mù.",
    entries: [
      // "Biết chắc mình đang đọc đúng phiên bản của phiếu duyệt gắn với đơn hàng" was here until
      // SESSION-LIST-001 and is deleted rather than reworded, because the gap it described
      // closed: an ORDER card reads the order's current row_version and says in words whether the
      // order changed since the envelope was raised, with Duyệt shut when it did. Nobody compares
      // two version numbers by eye any more.
      //
      // "Duyệt một tin nhắn soạn sẵn" was here until MESSAGE-DRAFT-BINDING-001 and is deleted
      // rather than reworded, because the gap it described closed. Its last wording said the
      // message body was stored nowhere and rendered_hash was checked against nothing; the body is
      // agent_drafts / agent_draft_reviews, API-INTEGRITY-002 made the server derive and verify
      // all three binding values from it, and
      // GET /internal/v1/stores/{store}/message-drafts/{draft}/binding now hands the words to the
      // approvals card, which prints them above the buttons. A disclosure retires when the
      // limitation behind it ends; that is the only way one may leave.
      {
        ref: "CONSOLE",
        title: "Tạo yêu cầu duyệt",
        // Narrowed by MESSAGE-DRAFT-BINDING-001, and it was already half stale: OPS-BOARD-001 had
        // let #/exports raise its own envelope. Both kinds that can be raised from the console are
        // raised from values a server read handed back, never from anything typed.
        what:
          "Tự mở một yêu cầu duyệt cho những việc khác cần chủ hoặc người duyệt gật đầu, ngoài " +
          "gửi tin nhắn và xuất dữ liệu.",
        missing:
          "Cần một resource_type khớp đúng ánh xạ hành động của máy chủ, cùng hai mã băm JCS và " +
          "một policy_version. Nhân viên không tạo được các giá trị đó bằng tay.",
        blockedBy:
          "Chưa có route nào trả về các giá trị đó cho những loại việc còn lại",
        today:
          "Xin gửi một tin nhắn thì mở ở màn hình Ngoại lệ, phần Gửi thủ công; xin xuất dữ liệu " +
          "thì mở ở màn hình Xuất dữ liệu. Các loại khác do máy chủ tự mở khi một lệnh chạm vào " +
          "ngưỡng cần duyệt.",
      },
      // "Danh sách và thu hồi phiên khác" was here until SESSION-LIST-001 and is deleted rather
      // than reworded, because the gap it described closed: SessionResponse carries session_id,
      // GET /internal/v1/sessions and GET /internal/v1/staff/{id}/sessions list the live
      // sessions, and the account sheet and a person's sheet on Nhân sự sign one device out
      // through the revoke route that was always there. Its RESPONSE_SHAPE binding went with it.
      // "Đọc lại một bản báo giá" was here until RANGE-PRICE-001 and is deleted rather than
      // reworded, because the gap it described closed:
      // `GET /internal/v1/stores/{store}/quotes/{quote}` returns one revision with its lines,
      // each carrying price_kind and, for a banded line, both bounds. `#/quotes?quote=<id>`
      // renders it, and the approvals queue links to it. A disclosure retires when the
      // limitation behind it ends; that is the only way one may leave.
    ],
  },
];

/**
 * One gap, as an accordion row: the title and one line saying what blocks it; the full entry on
 * expand (spec V2 §5.8).
 *
 * The four field labels are lifted verbatim from `unsupported()` so that a full-screen refusal and
 * an entry here say the same four things under the same four names. A `note` is rendered under the
 * fields rather than as a fifth field, because it is a constraint on the future build and not a
 * fact about today. The collapsed line is the `blockedBy` text itself, cut by CSS rather than by
 * code, so nothing in it is paraphrased; print expands every row.
 *
 * @param {Gap} gap
 * @returns {HTMLElement}
 */
function gapRow(gap) {
  return h(
    "details",
    { class: "accordion", dataRef: gap.ref },
    h(
      "summary",
      { class: "accordion__summary" },
      h("span", { class: "accordion__title" }, gap.title),
      h("span", { class: "accordion__meta" }, gap.blockedBy),
    ),
    h(
      "div",
      { class: "accordion__body stack stack--tight" },
      facts([
        ["Đặc tả yêu cầu", gap.what, { span: true }],
        ["Thiếu", gap.missing, { span: true }],
        ["Bị chặn bởi", gap.blockedBy, { span: true }],
        gap.today ? ["Hiện tại làm thế nào", gap.today, { span: true }] : null,
      ]),
      gap.note ? h("p", { class: "hint" }, gap.note) : null,
      gap.link ? h("p", null, h("a", { href: gap.link.href }, gap.link.label)) : null,
      h("p", { class: "accordion__ref" }, `Tham chiếu: ${gap.ref}`),
    ),
  );
}

/**
 * @param {{heading: string, lede: string, entries: Gap[]}} group
 * @returns {HTMLElement}
 */
function gapGroup(group) {
  return section({
    title: `${group.heading} · ${group.entries.length}`,
    info: infoButton(`Nhóm “${group.heading}” thiếu gì?`, h("p", null, group.lede)),
    children: h("div", { class: "accordion-list" }, group.entries.map(gapRow)),
  });
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const total = GROUPS.reduce((sum, group) => sum + group.entries.length, 0);

  return h(
    "section",
    { class: "screen" },
    page({
      title: "Việc chưa hỗ trợ",
      subtitle: `Chưa hỗ trợ: ${total} việc đặc tả yêu cầu mà bảng vận hành chưa làm được, mỗi việc một lý do cụ thể.`,
      info: infoButton(
        "Cách đọc trang này",
        h(
          "div",
          { class: "notice", dataState: "info" },
          h("p", { class: "notice__title" }, "Cách đọc trang này"),
          h(
            "p",
            null,
            "Mỗi mục nêu đúng thứ đang thiếu: một aggregate, một route, một quyết định hoặc một hạng " +
              "mục công việc. Không mục nào hẹn ngày — một lời hẹn ở đây sẽ cũ đi và làm mất tin vào " +
              "phần còn lại.",
          ),
          h(
            "p",
            null,
            "Nếu một việc cần làm hôm nay và không có trong danh sách này, đừng suy ra là bảng vận " +
              "hành làm được. Hãy hỏi trước khi ghi tay.",
          ),
        ),
      ),
    }),
    h(
      "p",
      { class: "hint" },
      "Việc không có trong danh sách chưa chắc đã làm được — hỏi trước khi ghi tay. Bấm một dòng để xem đủ.",
    ),
    GROUPS.map(gapGroup),
  );
}

export const screen = {
  path: "/gaps",
  title: "Chưa hỗ trợ",
  render: render_,
};
