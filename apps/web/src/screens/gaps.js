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
 *     calling `unsupported()` nineteen times because nineteen full screens is not a catalogue.
 *
 * Nothing here fetches. There is no request, no `Submission` and no loading state in this module,
 * because every fact on it is a fact about code that does not exist.
 *
 * @module screens/gaps
 */

import { h } from "../core/dom.js";
import { facts, panel } from "../ui/components.js";

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
    lede:
      "Bảng vận hành hôm nay dừng lại ở chiều thương mại của một đơn: tạo, xác nhận, huỷ. Các " +
      "chiều còn lại của cùng một đơn — nhận đồ, sản xuất, giao, thu tiền — không có bề mặt nào.",
    entries: [
      {
        ref: "DEC-013 · QUẦY",
        title: "Khách vãng lai (chưa từng nhắn tin)",
        what:
          "Tiếp nhận một người đi thẳng vào tiệm, chưa từng nhắn tin qua Zalo hay Telegram, và " +
          "vẫn báo giá được cho họ.",
        missing:
          "Màn hình Tiếp nhận cần một mã liên hệ đã tồn tại, và nguồn duy nhất sinh ra mã đó là " +
          "một tin nhắn khách đã gửi qua kênh chính thức rồi được máy chủ xác minh. Không có " +
          "đường nào tạo liên hệ tại quầy, nên khách vãng lai chưa tiếp nhận được trong hệ thống.",
        blockedBy: "DEC-013 — chủ tiệm quyết cách nhận diện khách tại quầy",
        today:
          "Nhận đồ và ghi tay như trước. Đây là khoảng trống quy trình thật, không phải lỗi giao " +
          "diện: tạo liên hệ tại quầy đồng nghĩa với lưu thông tin cá nhân của khách mà chưa có " +
          "cơ sở đồng ý nào, nên hệ thống từ chối thay vì tự làm.",
        note:
          "Khi mở, quyết định phải nêu rõ lưu gì (tên? số điện thoại? không gì cả?), giữ bao lâu, " +
          "và khách đồng ý bằng cách nào — trước khi có ô nhập nào được thêm vào màn hình Tiếp nhận.",
        link: { href: "#/order-requests", label: "Mở màn hình Tiếp nhận" },
      },
      {
        ref: "CONSOLE",
        title: "Tạo đơn từ một báo giá",
        what: "Mở một đơn hàng từ báo giá khách đã chốt.",
        missing:
          "Máy chủ chỉ nhận báo giá ở trạng thái APPROVED_EXACT / ACCEPTED_FINAL kèm approval_id. " +
          "Bộ định giá chỉ sinh ra được ESTIMATE / REVIEW_REQUIRED, và không có đường nào trong " +
          "hệ thống nâng một bản báo giá lên trạng thái đó. Nên hôm nay mọi lệnh tạo đơn đều bị " +
          "từ chối.",
        blockedBy: "Chưa có nguồn nào đặt quote_revisions.finality = APPROVED_EXACT",
        today:
          "Báo giá vẫn tạo và tra cứu được. Đơn hàng thì ghi tay như trước. Đây là mục thứ hai " +
          "chặn cùng một việc: kể cả khi khách vãng lai nhận diện được, đơn vẫn chưa tạo được.",
        note:
          "Khi mở, quyết định phải nêu rõ lúc nào một báo giá ràng buộc tiệm — ai duyệt, duyệt " +
          "cái gì, và khách chốt bằng cách nào — chứ không chỉ thêm một route.",
        link: { href: "#/quotes", label: "Mở màn hình Báo giá" },
      },
      {
        ref: "M3 · MÀN 2",
        title: "Khách hàng / hỏi mới",
        what:
          "Tạo và tra cứu khách hàng cùng số liên hệ và địa chỉ của họ, để một hỏi mới gắn được " +
          "vào đúng người.",
        missing:
          "Không tồn tại aggregate parties, contact_points hay addresses nào. Không có gì để đọc " +
          "và không có gì để ghi. Hệ quả rộng hơn: không có nguồn nào sinh ra bound_contact_id, " +
          "nên hiện tại hệ thống không ghi nhận được khách hàng từ bất kỳ nguồn nào.",
        blockedBy: "DEC-015 — chủ tiệm quyết hồ sơ khách hàng là gì và khi nào một người trở thành khách",
        today:
          "Liên hệ được nhận diện phía máy chủ qua contact binding; bảng vận hành không tạo khách.",
      },
      {
        ref: "M3 · MÀN 7",
        title: "Nhận đồ / cân đo (intake)",
        what:
          "Ghi nhận việc nhận đồ tại quầy, cân đo thật, và đưa đơn đi qua các trạng thái intake " +
          "cho tới khi được chấp nhận hoặc từ chối.",
        missing:
          "Các chuyển trạng thái intake không có route HTTP nào. Route /transition chỉ mở ra chiều " +
          "thương mại của đơn, nên IntakeStatus hiển thị được nhưng không đổi được.",
        blockedBy: "Chưa có lệnh advance-intake",
      },
      {
        ref: "M3 · MÀN 8",
        title: "Sản xuất / sẵn sàng",
        what:
          "Đưa đơn qua hàng đợi giặt, kiểm tra chất lượng và trạng thái sẵn sàng tại cửa hàng.",
        missing:
          "Giống hệt intake: trạng thái sản xuất không có route nào. ProductionStatus chỉ đọc được.",
        blockedBy: "Chưa có route chuyển trạng thái sản xuất (cùng nguyên nhân với intake)",
      },
      {
        ref: "M3 · MÀN 9",
        title: "Chặng giao hàng",
        what:
          "Gom đơn thành chuyến, chia chặng lấy và chặng trả, và ghi quãng đường đã đo cho từng " +
          "chặng.",
        missing: "Không có delivery_bundles, delivery_legs hay distance_measurements.",
        blockedBy: "DEC-003 (chính sách phí giao trên 6km và chặng đơn)",
      },
      {
        ref: "M3 · MÀN 10",
        title: "Thanh toán / tất toán",
        what: "Ghi khoản phải thu, ghi khoản đã thu, phân bổ tiền thu vào từng khoản, và tất toán đơn.",
        missing:
          "Trả đủ đúng số khi khách tự lấy đã có: khối tất toán trong màn hình chi tiết đơn " +
          "(SETTLEMENT-001, đã hoàn thành). Còn thiếu charges, payments và payment_allocations " +
          "tổng quát, nên trả một phần, trả thừa và ghi nợ vẫn không biểu diễn được.",
        // DEC-010 is registered and OPEN, and its title is "Settlement shapes beyond exact payment
        // in full at handover". The ordinary case shipped with SETTLEMENT-001; what this entry
        // still discloses is exactly the shapes that decision gates.
        blockedBy:
          "DEC-010 (đang mở) — chỉ chặn trả một phần, trả thừa và ghi nợ. Trả đủ đúng số không " +
          "còn bị chặn.",
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
      {
        ref: "M3",
        title: "Bồi hoàn sự cố",
        what:
          "Quyết định và ghi nhận bồi hoàn cho một sự cố: giảm trừ, giặt lại, hoặc cấp tín dụng " +
          "cho khách.",
        missing:
          "Không có remedies, credit_grants hay credit_ledger_entries. Bồi hoàn là một lệnh có " +
          "duyệt, không phải một trạng thái tự do, nên không thể thay bằng một ô ghi chú.",
        blockedBy: "DEC-004",
      },
    ],
  },
  {
    heading: "Đo lường và báo cáo",
    lede:
      "Mọi chỉ số vận hành đều cần một read model có phiên bản đứng sau. Chưa có cái nào, nên bảng " +
      "vận hành không hiển thị KPI — đếm tạm vài con số rồi gọi là KPI là cách nhanh nhất để một " +
      "quyết định kinh doanh dựa trên số bịa.",
    entries: [
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
        ref: "FR-RPT-003 · FR-RPT-004",
        title: "Xuất CSV",
        what: "Xuất dữ liệu vận hành ra tệp để chủ mở bằng bảng tính hoặc gửi cho kế toán.",
        missing: "Không có endpoint xuất dữ liệu nào.",
        blockedBy: "Chưa có route xuất dữ liệu; phụ thuộc read model báo cáo ở mục trên",
        note:
          "Khi làm: CSV phải chống chèn công thức bảng tính — một ô bắt đầu bằng =, +, - hay @ là " +
          "mã chạy được khi khách hàng mở tệp.",
      },
      {
        ref: "FR-RPT-006",
        title: "Rủi ro SLA",
        what: "Danh sách đơn đang có nguy cơ trễ hẹn, xếp theo mức rủi ro.",
        missing:
          "Read model đã tồn tại trong kho dữ liệu nhưng chưa có route nào phơi nó ra. Ngoài ra nó " +
          "cần một ProductionSlaPolicy cho mỗi đơn, và đó là một quyết định kinh doanh chứ không " +
          "phải một mặc định kỹ thuật để lấp vào.",
        blockedBy: "Chọn chính sách SLA cho từng đơn (chưa có nguồn cấu hình)",
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
    lede:
      "Bốn mục dưới đây không thiếu quyết định kinh doanh nào. Chúng thiếu đúng những trường mà API " +
      "không trả về, và một bề mặt đoán bừa các trường đó sẽ là duyệt mù hoặc thao tác nhầm người.",
    entries: [
      {
        ref: "CONSOLE",
        title: "Quyết định duyệt trong console",
        what: "Duyệt hoặc từ chối một phong bì đang chờ, ngay tại hàng chờ.",
        missing:
          "Hàng chờ trả về envelope_hash nhưng không trả resource_version, snapshot_hash hay " +
          "rendered_hash, nên không dựng được một quyết định hợp lệ. Và duyệt theo mã băm mà không " +
          "xem được nội dung đứng sau nó là duyệt mù.",
        blockedBy: "Route hàng chờ duyệt không trả resource_version, snapshot_hash và rendered_hash",
        today:
          "Màn hình Duyệt hiển thị hàng chờ và thời gian còn lại; các nút quyết định hiện ra nhưng " +
          "bị vô hiệu hoá kèm lý do.",
      },
      {
        ref: "CONSOLE",
        title: "Tạo yêu cầu duyệt",
        what: "Tự mở một yêu cầu duyệt cho một việc cần chủ hoặc người duyệt gật đầu.",
        missing:
          "Cần một resource_type khớp đúng ánh xạ hành động của máy chủ, cùng hai mã băm JCS và " +
          "một policy_version. Nhân viên không tạo được các giá trị đó bằng tay.",
        blockedBy:
          "Chưa có ánh xạ resource_type dùng được từ giao diện và chưa có nguồn policy_version",
        today: "Yêu cầu duyệt do máy chủ tự mở khi một lệnh chạm vào ngưỡng cần duyệt.",
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
      {
        ref: "CONSOLE",
        title: "Đọc lại một bản báo giá",
        what: "Mở lại một bản báo giá đã tạo và xem đúng các dòng dịch vụ của nó.",
        missing:
          "Không có route GET cho một revision, và không endpoint nào trả các dòng của báo giá, " +
          "nên không dựng lại được chính thứ vừa tạo ra.",
        blockedBy: "Không có GET cho một quote revision và không có endpoint trả quote_lines",
        today:
          "Danh sách báo giá của cửa hàng trả về bản mới nhất kèm tổng, trạng thái giá và mã băm " +
          "ảnh chụp — nhưng không có dòng chi tiết nào.",
      },
    ],
  },
];

/**
 * One gap, at catalogue size.
 *
 * The four field labels are lifted verbatim from `unsupported()` so that a full-screen refusal and
 * a card here say the same four things under the same four names. A `note` is rendered as a hint
 * under the fields rather than as a fifth field, because it is a constraint on the future build and
 * not a fact about today.
 *
 * @param {Gap} gap
 * @returns {HTMLElement}
 */
function gapCard(gap) {
  return h(
    "article",
    { class: "card stack stack--tight" },
    h("p", { class: "eyebrow" }, gap.ref),
    h("h3", null, gap.title),
    facts([
      ["Đặc tả yêu cầu", gap.what, { span: true }],
      ["Thiếu", gap.missing, { span: true }],
      ["Bị chặn bởi", gap.blockedBy, { mono: true, span: true }],
      gap.today ? ["Hiện tại làm thế nào", gap.today, { span: true }] : null,
    ]),
    gap.note ? h("p", { class: "hint" }, gap.note) : null,
    gap.link ? h("p", null, h("a", { href: gap.link.href }, gap.link.label)) : null,
  );
}

/**
 * @param {{heading: string, lede: string, entries: Gap[]}} group
 * @returns {HTMLElement}
 */
function gapGroup(group) {
  return panel({
    eyebrow: "Nhóm",
    title: group.heading,
    count: String(group.entries.length),
    children: h(
      "div",
      { class: "stack" },
      h("p", { class: "screen__lede" }, group.lede),
      group.entries.map(gapCard),
    ),
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
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Chưa hỗ trợ · Danh sách đầy đủ"),
      h("h1", null, "Chưa hỗ trợ"),
      h(
        "p",
        { class: "screen__lede" },
        `${total} năng lực mà đặc tả yêu cầu và bảng vận hành này chưa làm được, cùng lý do cụ ` +
          "thể cho từng cái. Danh sách được viết ra để không ai phải đoán bảng vận hành có làm " +
          "được một việc hay không.",
      ),
    ),
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
    GROUPS.map(gapGroup),
  );
}

export const screen = {
  path: "/gaps",
  title: "Chưa hỗ trợ",
  render: render_,
};
