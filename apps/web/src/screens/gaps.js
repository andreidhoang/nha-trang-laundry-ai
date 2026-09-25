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
    lede:
      "Bảng vận hành hôm nay bao được cả bốn chiều của một đơn tại quầy: thương mại, nhận đồ, " +
      "sản xuất và tất toán, cùng chặng giao. Ba chiều trạng thái nằm chung ở lệnh “Chuyển " +
      "trạng thái đơn” trên màn hình Đơn hàng — chọn chiều trước, rồi chọn đích.",
    entries: [
      {
        ref: "M3 · MÀN 2",
        title: "Khách hàng / hỏi mới",
        what:
          "Tạo và tra cứu khách hàng cùng số liên hệ và địa chỉ của họ, để một hỏi mới gắn được " +
          "vào đúng người.",
        // The second half of this used to read "không có nguồn nào sinh ra bound_contact_id, nên
        // hiện tại hệ thống không ghi nhận được khách hàng từ bất kỳ nguồn nào" — no source
        // produces a bound_contact_id, so no customer can be recorded at all. COUNTER-TICKET-001
        // shipped one under DEC-013 on 26/08: the counter issues a number and that number is the
        // order's customer reference. What DEC-015 still declines is a customer *record*, which is
        // a different thing and is what this entry now says.
        missing:
          "Không tồn tại aggregate parties, contact_points hay addresses nào: hệ thống không lưu " +
          "tên, số điện thoại hay địa chỉ của khách. Khách vãng lai được nhận diện bằng số phiếu " +
          "do quầy phát (DEC-013, 26/08), và số phiếu đó là bound_contact_id của đơn — nên đơn " +
          "tạo được, còn hồ sơ khách thì chưa có.",
        // DEC-015 is RESOLVED (26/08), not pending: the owner decided *not* to build a customer
        // record layer yet, and named the trigger that reopens it. "Still being decided" and
        // "decided not to, on purpose" are different things to plan a shop around — the sentence
        // COUNTER-DEFECTS-001 wrote when it fixed the same defect on DEC-010, in the very commit
        // that left this one.
        blockedBy:
          "DEC-015 (đã chốt 26/08) — chủ tiệm quyết chưa xây lớp hồ sơ khách hàng; mở lại khi có " +
          "kênh liên lạc chính thức",
        today:
          "Liên hệ được nhận diện phía máy chủ qua contact binding; bảng vận hành không tạo khách.",
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
    lede:
      "Mọi chỉ số vận hành đều cần một read model có phiên bản đứng sau. Chưa có cái nào, nên bảng " +
      "vận hành không hiển thị KPI — đếm tạm vài con số rồi gọi là KPI là cách nhanh nhất để một " +
      "quyết định kinh doanh dựa trên số bịa.",
    entries: [
      // "Xem lại nguồn khách đã ghi trên một đơn" (ACQUISITION-ATTRIBUTION-001) was here until
      // READ-PATHS-001 put `acquisition_source` on the order read model, and is deleted rather than
      // reworded because the gap closed: the order detail shows the recorded source read-only. It
      // is still immutable -- a mis-tap is now visible and still cannot be corrected, which the
      // detail says beside the value and the order form says before it is chosen. The channel
      // report remains scripts/report_acquisition_sources.py.
      {
        ref: "M3 · MÀN 12",
        title: "Ghi nhận máy / mẻ / phút công",
        what: "Ghi lại máy nào chạy mẻ nào, trong bao lâu, và tốn bao nhiêu phút công của ai.",
        missing: "Không có kho dữ liệu nào ghi nhận máy, mẻ giặt hay phút công.",
        blockedBy: "SHOP-INSTRUMENT-001 (4–6 tuần đo thật)",
      },
      {
        ref: "M3 · MÀN 13",
        title: "Ghi nhận chi phí giao hàng",
        what: "Ghi chi phí thật của một chuyến giao, để đối chiếu với phí giao đã thu của khách.",
        missing: "Không có kho dữ liệu nào ghi nhận chi phí của một chuyến giao.",
        blockedBy: "SHOP-INSTRUMENT-001",
      },
      {
        ref: "M3 · MÀN 14 · FR-RPT-001..006",
        title: "Bảng điều hành hằng ngày",
        what:
          "Một bảng số hằng ngày cho chủ: phễu đơn, tỉ lệ đúng hẹn, tỉ lệ giặt lại, doanh thu.",
        missing:
          "Không có read model nào tính funnel, on-time, rewash hay doanh thu. FR-RPT-005 buộc mọi " +
          "KPI phải có tử số, mẫu số, khung thời gian và trạng thái chất lượng dữ liệu — và không " +
          "có gì cung cấp bốn thứ đó.",
        blockedBy: "Chưa có read model báo cáo có phiên bản làm nguồn cho FR-RPT-005",
        today:
          "Màn hình Hôm nay chỉ có số bản ghi đang chờ. Đó là số đếm, không phải KPI, và không " +
          "được đọc như KPI.",
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
        ref: "FR-RPT-003 · FR-RPT-004",
        title: "Xuất báo cáo theo khoảng thời gian",
        what:
          "Xuất số liệu vận hành cho một khoảng thời gian tự chọn, kèm các chỉ số của bảng điều " +
          "hành, để gửi cho kế toán.",
        missing:
          "Chỉ xuất được đúng hồ sơ thô của một ngày làm việc, ở màn hình Xuất dữ liệu: mã đơn, " +
          "trạng thái, mốc thời gian và tiền đã thu. Không chọn được khoảng ngày, và không có chỉ " +
          "số nào — vì chưa có read model nào tính chúng.",
        blockedBy: "Chưa có read model báo cáo có phiên bản (mục Bảng điều hành hằng ngày)",
        today:
          "Xuất một ngày thì được, và mỗi lần xuất đều cần chủ tiệm duyệt rồi mới có tệp. Tệp đó " +
          "không kèm lời khách phàn nàn và không kèm mô tả bằng chứng.",
      },
      {
        // OPS-BOARD-001 phơi read model ra thành màn hình Bảng trễ hạn, nên nửa đầu của câu cũ
        // không còn đúng. Nửa sau vẫn đúng nguyên: quy tắc SLA của từng đơn vẫn chưa ai chốt, và
        // bảng đang dùng đúng một quy tắc đã nêu cho mọi đơn.
        ref: "FR-RPT-006",
        title: "Quy tắc SLA riêng cho từng đơn",
        what:
          "Mỗi đơn có mốc riêng theo loại dịch vụ và theo điều đã hẹn với khách, thay vì một mốc " +
          "chung cho tất cả.",
        missing:
          "Chưa có nguồn cấu hình nào gán ProductionSlaPolicy cho từng đơn. Bảng trễ hạn vì vậy " +
          "đang áp đúng một quy tắc đã nêu cho mọi đơn, và nói rõ điều đó ngay trên màn hình.",
        blockedBy: "Chọn chính sách SLA cho từng đơn (chưa có nguồn cấu hình)",
        today:
          "Xem danh sách đơn đang sản xuất ở màn hình Bảng trễ hạn. Mốc ở đó là mốc rủi ro nội bộ " +
          "của tiệm, không phải giờ đã hẹn với khách.",
      },
    ],
  },
  {
    heading: "Kênh và AI",
    lede:
      "Chưa có kênh nào nối vào hệ thống, nên chưa có tin nhắn nào để hợp nhất và chưa có gì để AI " +
      "tóm tắt.",
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
        missing: "Chưa có metric có phiên bản nào để AI trích dẫn.",
        blockedBy: "Phụ thuộc read model báo cáo có phiên bản (mục Bảng điều hành hằng ngày)",
        note:
          "Theo đặc tả, AI chỉ được trích dẫn read model có phiên bản. Nó không được tự tính chỉ " +
          "số, không được sinh SQL, không được chọn định danh và không được thay đổi trạng thái.",
      },
    ],
  },
  {
    heading: "Duyệt và phiên",
    // MESSAGE-DRAFT-BINDING-001 retired the "Duyệt một tin nhắn soạn sẵn" entry, so four became
    // three. The lede counts its entries and has been wrong about that count once already.
    lede:
      "Ba mục dưới đây không thiếu quyết định kinh doanh nào. Chúng thiếu đúng những trường mà API " +
      "không trả về, và một bề mặt đoán bừa các trường đó sẽ là duyệt mù hoặc thao tác nhầm người.",
    entries: [
      {
        ref: "CONSOLE",
        title: "Biết chắc mình đang đọc đúng phiên bản của phiếu duyệt gắn với đơn hàng",
        what:
          "Mở một phong bì ORDER và thấy ngay rằng đơn hàng đang hiện đúng là phiên bản mà " +
          "phong bì niêm phong, chứ không phải một phiên bản mới hơn.",
        // RANGE-APPROVAL-VISIBILITY-001 closed the money half of this and left the rest standing,
        // so the entry is written for what remains rather than for what was fixed. A
        // SET_RANGE_PRICE envelope now prints its proposed amounts on the card, and the quote
        // link carries &revision=<n> so the panel opens the bound revision. The ORDER link does
        // not: #/orders/:id shows the order as it is now.
        missing:
          "Đường dẫn tới đơn hàng không mang theo số phiên bản, và màn hình đơn hàng luôn hiện " +
          "trạng thái mới nhất. Thẻ phiếu in “Phiên bản v…” còn màn hình đơn hàng in “Phiên bản " +
          "dòng v…”, nên người duyệt phải tự so hai con số bằng mắt; không có gì bắt họ so.",
        blockedBy: "Màn hình đơn hàng chưa đọc được một phiên bản cũ của đơn",
        today:
          "Máy chủ vẫn từ chối một quyết định gửi kèm phiên bản không khớp bản đã lưu, nên không " +
          "ai duyệt nhầm được vào một phiên bản khác. Điều còn thiếu là ở phía người đọc: hai con " +
          "số phiên bản đều hiện ra, nhưng phải tự đối chiếu. Phiếu báo giá thì đã hết vấn đề này " +
          "— đường dẫn mang sẵn số bản sửa đổi.",
      },
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
      {
        ref: "CONSOLE",
        title: "Danh sách và thu hồi phiên khác",
        what: "Xem các phiên đang mở của mình hoặc của người khác, và đóng một phiên cụ thể.",
        missing:
          "/internal/v1/session không trả session_id, nên bảng vận hành không biết định danh nào " +
          "để gửi tới route thu hồi.",
        blockedBy: "SessionResponse không có trường session_id",
        today:
          "Nút Thoát chỉ kết thúc phiên đang dùng. Muốn cắt mọi phiên của một người thì vô hiệu " +
          "hoá người đó ở màn hình Nhân sự — lệnh ấy thu hồi tất cả phiên của họ.",
      },
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
