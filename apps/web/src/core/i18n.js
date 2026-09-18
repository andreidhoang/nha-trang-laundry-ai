/**
 * Vietnamese labels, and the spec-mandated tokens they gloss.
 *
 * The specification pulls in two directions. `IMPLEMENTATION_ROADMAP_V1.md:883` requires Vietnamese
 * labels; `:886` and `:896-904` mandate ten specific strings that are English uppercase tokens
 * (`HUMAN REQUIRED`, `TAX UNVERIFIED`, …) alongside three Vietnamese ones (`ƯỚC TÍNH`, `KHOẢNG GIÁ`,
 * `ĐÃ DUYỆT`). Nothing says which wins, and dropping either is a defect: the tokens are what an
 * eval or an auditor greps for, the Vietnamese is what the person at the counter reads.
 *
 * So both are rendered — but Vietnamese leads, because Vietnamese is the reading language of the
 * counter. The mandated token stays verbatim as the secondary caption (`Chưa xác minh thuế · TAX
 * UNVERIFIED`), never translated away and never abbreviated: it is what an eval or an auditor
 * greps for, and what an operator quotes when reporting a problem.
 *
 * Enum values follow the same rule. A server enum is shown Vietnamese-first with the verbatim
 * value in parentheses (`Nháp (DRAFT)`): the person at the counter reads their own language, and
 * the token survives for the conversation with engineering.
 *
 * @module core/i18n
 */

/** The three price-state labels of `IMPLEMENTATION_ROADMAP_V1.md:886`. These tokens are already
 * Vietnamese — they are the mandated display strings — so they lead and the gloss follows. */
export const PRICE_STATE = {
  ESTIMATE: { token: "ƯỚC TÍNH", gloss: "chưa phải giá cuối", state: "info", tokenFirst: true },
  RANGE: { token: "KHOẢNG GIÁ", gloss: "chờ nhân viên chọn giá chính xác", state: "warn", tokenFirst: true },
  APPROVED_EXACT: { token: "ĐÃ DUYỆT", gloss: "giá chính xác đã được duyệt", state: "ok", tokenFirst: true },
};

/** The seven critical warnings of `IMPLEMENTATION_ROADMAP_V1.md:896-904`. */
export const WARNING = {
  HUMAN_REQUIRED: { token: "HUMAN REQUIRED", gloss: "Cần người quyết định", state: "warn" },
  PROMOTION_PROVISIONAL: {
    token: "PROMOTION PROVISIONAL",
    gloss: "Khuyến mãi chưa chốt",
    state: "warn",
  },
  TAX_UNVERIFIED: { token: "TAX UNVERIFIED", gloss: "Chưa xác minh thuế", state: "warn" },
  CAPACITY_NOT_CONFIRMED: {
    token: "CAPACITY NOT CONFIRMED",
    gloss: "Chưa xác nhận năng lực/lịch",
    state: "warn",
  },
  DELIVERY_FEE_MISSING: {
    token: "DELIVERY FEE MISSING",
    gloss: "Chưa có phí giao hàng",
    state: "warn",
  },
  CUSTOMER_RECONFIRMATION: {
    token: "CUSTOMER RECONFIRMATION",
    gloss: "Cần khách xác nhận lại",
    state: "warn",
  },
  INCIDENT: { token: "INCIDENT", gloss: "Đơn có sự cố", state: "danger" },
};

/**
 * Which mandated warning a domain reason code raises.
 *
 * The pairing is derived, not quoted: the spec fixes the strings in one place and the error codes
 * in another and never joins them. Each entry below is the join, and an unmapped code falls back to
 * `HUMAN REQUIRED`, which is the safe direction — over-warning costs a glance, under-warning costs
 * a wrong price.
 */
const REASON_TO_WARNING = {
  TAX_TREATMENT_UNVERIFIED: WARNING.TAX_UNVERIFIED,
  DELIVERY_FEE_UNRESOLVED: WARNING.DELIVERY_FEE_MISSING,
  DELIVERY_FEE_REQUIRES_HUMAN: WARNING.DELIVERY_FEE_MISSING,
  DELIVERY_DISTANCE_UNVERIFIED: WARNING.DELIVERY_FEE_MISSING,
  PROMOTION_NOT_EVALUATED: WARNING.PROMOTION_PROVISIONAL,
  PROMOTION_ELIGIBILITY_UNRESOLVED: WARNING.PROMOTION_PROVISIONAL,
  SLOT_APPROVAL_REQUIRED: WARNING.CAPACITY_NOT_CONFIRMED,
  CUSTOMER_RECONFIRMATION_REQUIRED: WARNING.CUSTOMER_RECONFIRMATION,
  RANGE_PRICE_REQUIRES_HUMAN: WARNING.HUMAN_REQUIRED,
  // `RangePriceRefusal`, from RANGE-PRICE-001. All three are the deterministic engine refusing an
  // amount rather than pricing it, which is `HUMAN REQUIRED` in the mandated vocabulary: a person
  // has to supply a different number, or republish the pricebook. None of them is a price.
  RANGE_PRICE_OUT_OF_BAND: WARNING.HUMAN_REQUIRED,
  RANGE_PRICE_NOT_APPLICABLE: WARNING.HUMAN_REQUIRED,
  RANGE_PRICE_PRICEBOOK_MISMATCH: WARNING.HUMAN_REQUIRED,
  PRICE_RULE_UNRESOLVED: WARNING.HUMAN_REQUIRED,
  MEASUREMENT_POLICY_UNRESOLVED: WARNING.HUMAN_REQUIRED,
  HUMAN_APPROVAL_REQUIRED: WARNING.HUMAN_REQUIRED,
};

/**
 * @param {string} reasonCode
 * @returns {{token: string, gloss: string, state: string}}
 */
export function warningFor(reasonCode) {
  return REASON_TO_WARNING[reasonCode] || WARNING.HUMAN_REQUIRED;
}

/**
 * Plain-language notes for the reason codes this API actually returns today.
 *
 * Every quote produced right now carries the same three, so an operator sees them constantly and
 * needs to know which are normal and which are blocking.
 */
export const REASON_NOTE = {
  TAX_TREATMENT_UNVERIFIED:
    "Mọi báo giá R1 đều mang mã này. Số tiền hiển thị không được gọi là đã gồm hay chưa gồm thuế.",
  DELIVERY_FEE_UNRESOLVED:
    "Chưa có phí giao hàng nên chưa có tổng cuối. Tiền dịch vụ bên dưới không phải số khách trả.",
  PROMOTION_NOT_EVALUATED: "Chưa xét khuyến mãi cho bản báo giá này.",
  MISSING_REQUIRED_FACT: "Thiếu dữ kiện bắt buộc; máy chủ không đoán.",
  RANGE_PRICE_REQUIRES_HUMAN: "Dịch vụ này có khoảng giá; nhân viên phải chọn giá chính xác.",
  // `RangePriceRefusal`, exactly as packages/domain/.../range_prices.py names them. Each says what
  // the counter should do next, because a staff member standing in front of a customer needs an
  // action and not only a refusal — and because none of these three is fixable by retyping harder.
  RANGE_PRICE_OUT_OF_BAND:
    "Số tiền nằm ngoài khoảng giá đã niêm yết cho dòng này. Khoảng lấy từ đúng bản báo giá khách " +
    "đã nghe, và cả hai đầu đều nhận. Không có gì được ghi: nhập lại một số trong khoảng, hoặc " +
    "báo chủ tiệm nếu khoảng giá cần đổi.",
  RANGE_PRICE_NOT_APPLICABLE:
    "Dòng này đã có giá cố định trong bảng giá, nên không chốt giá trong khoảng cho nó được. " +
    "Kiểm lại xem bạn có gửi số cho đúng dòng hay không.",
  RANGE_PRICE_PRICEBOOK_MISMATCH:
    "Khoảng giá được đọc từ một phiên bản bảng giá khác với phiên bản đã dùng để tính bản báo giá " +
    "này. Bảng giá đã công bố là bất biến, nên bản cũ vẫn giữ khoảng cũ. Tải lại bản báo giá.",
  PRICE_RULE_UNRESOLVED: "Không có quy tắc giá áp dụng được cho dòng này.",
  MEASUREMENT_POLICY_UNRESOLVED: "Chưa có chính sách đo lường được công bố cho cơ sở khối lượng này.",
  // `core/errors.js` mints this code itself for a 409 whose detail starts `HUMAN_APPROVAL_REQUIRED`,
  // and the domain emits it for a price submitted with no approval envelope behind it. It reached
  // `reasonCodeList` as a bare English token in both cases, which is the one thing this table
  // exists to stop.
  HUMAN_APPROVAL_REQUIRED:
    "Việc này cần một người thứ hai duyệt trước, và chưa có phiếu duyệt nào hợp lệ gắn với đúng " +
    "nội dung đang gửi. Không có gì được ghi. Mở màn hình Duyệt để xem phiếu, hoặc gửi lại đề nghị.",
  AMBIGUOUS_SERVICE: "Mã dịch vụ khớp nhiều mục; cần chọn rõ.",
  INCOMPATIBLE_UNIT: "Đơn vị không dùng được với dịch vụ này.",
  VALIDATION_ERROR: "Dữ liệu vào không hợp lệ với quy tắc miền.",
  // `SettlementRefusal`, exactly as packages/domain/.../settlement.py names them. Each says what
  // the counter should do now, because a staff member holding a customer's money needs a next
  // action and not only a refusal.
  AMOUNT_IS_NOT_THE_EXACT_TOTAL:
    "Chỉ nhận đúng tổng đã báo, đủ một lần. Trả thiếu, trả thừa, đặt cọc hay trả góp đều chưa " +
    "được hỗ trợ. Hãy thu đúng tổng, hoặc báo giá lại nếu con số đã thay đổi.",
  COLLECTION_WAS_NOT_BY_THE_CUSTOMER:
    "Ô “khách đã tự lấy đồ” phải khớp với hình thức của đơn: khách tự lấy thì tích, đơn giao tận " +
    "nơi thì để trống và chặng giao mới là thứ đóng đơn.",
  NO_PRESENTABLE_TOTAL:
    "Báo giá gắn với đơn này chưa có tổng cuối, thường vì phí giao chưa chốt. Chưa có tổng thì " +
    "chưa thu được tiền.",
  TOTAL_IS_A_RANGE:
    "Báo giá này là một khoảng giá, chưa phải một số. Nhân viên phải chốt giá chính xác trước.",
  // The rest of what `record_settlement` can answer. These are not open decisions -- they are
  // states -- and they arrive with `decision: null`. Leaving them unglossed put a bare English
  // token in front of a counter holding a customer's money.
  ORDER_NOT_FOUND:
    "Không có đơn nào mang mã này ở cửa hàng đang chọn. Kiểm tra lại mã đơn trên bảng đơn.",
  ORDER_NOT_ACTIVE:
    "Chỉ đơn đang chạy mới tất toán được. Đơn chưa xác nhận thì xác nhận trước; đơn đã đóng hoặc " +
    "đã huỷ thì không thu tiền qua màn hình này.",
  ALREADY_SETTLED:
    "Đơn này đã được tất toán rồi — không phải lỗi của bạn, và không thu thêm lần nữa. Mở lại đơn " +
    "để xem lần tất toán đã ghi.",
  STALE_VERSION:
    "Có người vừa đổi đơn này trong lúc bạn đang xem. Tải lại đơn rồi ghi nhận theo số mới.",
  // `RemedyRefusal`, exactly as packages/domain/.../remedies.py names them, plus the codes
  // `RemedyProposalRepository` raises that are states rather than policy answers. The rule is the
  // settlement vocabulary's: a refusal a staff member meets with a customer in front of them may
  // not arrive as a bare English token, and each note says what to do next rather than only what
  // went wrong. `_raise_remedy_error` sends these as a 422 whose detail is an object carrying
  // `reason_code`, which `classify` reads through `reasonCodesOf`.
  REMEDY_POLICY_UNPUBLISHED:
    "Chưa có bản chính sách bồi hoàn nào được công bố, nên không có con số nào để áp dụng và " +
    "không được suy ra con số nào — kể cả cho loại không chuyển tiền, vì cửa sổ 7 ngày cũng là " +
    "một con số đã công bố. Báo chủ tiệm công bố chính sách; đừng hứa mức nào ở quầy.",
  LOSS_POLICY_UNRESOLVED:
    "Chủ tiệm chưa quyết chính sách cho trường hợp mất đồ, và mức của hàng hỏng không được mượn " +
    "sang. Sự cố vẫn được ghi — đó mới là việc phải làm ở quầy. Báo chủ tiệm trong ngày.",
  REMEDY_CEILING_EXCEEDED:
    "Số tiền vượt trần máy chủ tính từ chính dòng đã có giá của đơn (5 lần phí giặt món đó). " +
    "Máy chủ từ chối kèm con số trần và không tự hạ xuống — hạ xuống là trả cho khách ít hơn số " +
    "bạn vừa thoả thuận. Nhập lại trong trần, hoặc báo chủ tiệm nếu vụ này cần khác đi.",
  REMEDY_WINDOW_CLOSED:
    "Đã quá cửa sổ chủ tiệm công bố cho loại này, đo từ lúc khách nhận đồ. Nói với khách đúng mốc " +
    "đã qua chứ không chỉ nói là hết hạn. Muốn làm ngoài cửa sổ thì phải hỏi chủ tiệm.",
  REMEDY_WINDOW_EVIDENCE_MISSING:
    "Tiệm không có bản ghi nào cho biết khách đã nhận đồ lúc nào, nên không đo được cửa sổ nào " +
    "cả. Đây không phải lỗi máy và cũng không phải mất dữ liệu: đơn này được trả trước khi hệ " +
    "thống bắt đầu ghi mốc giao đồ. Lấy hôm nay hay ngày tạo đơn thay vào là bịa ra một phép đo.",
  REMEDY_STORE_FAULT_NOT_ATTESTED:
    "Chưa có nhân viên nào xác định lỗi thuộc về tiệm, mà DEC-004 đặt mọi khoản bồi hoàn lên đúng " +
    "việc đó. Xác định rồi hãy gửi; tên người xác định được ghi kèm.",
  REMEDY_AMOUNT_NOT_APPLICABLE:
    "Có ô không thuộc về loại bồi hoàn đang chọn, hoặc thiếu ô bắt buộc của loại đó. Chỉ bồi " +
    "thường món hỏng mới có người gõ số tiền: giặt lại không chuyển tiền, giảm trừ giao trễ do " +
    "máy chủ tính, mất đồ thì không có số nào.",
  REMEDY_LINE_NOT_PRICED:
    "Dòng được chọn không có trong bản báo giá hiện tại của đơn, nên không có phí giặt nào để lấy " +
    "5 lần. Mở lại đơn, đọc đúng mã dòng trên bản giá.",
  REMEDY_ORDER_NOT_SETTLED:
    "Đơn chưa tất toán nên không có tổng nào để lấy 10%. Thu tiền xong rồi mới đề nghị giảm trừ " +
    "cho lần sau được.",
  REMEDY_DELIVERY_NOT_RECORDED:
    "Đơn này không có chuyến giao nào đã giao thành công, nên không thể có chuyến giao trễ. Khách " +
    "tự lấy ở quầy thì dùng loại khác.",
  REMEDY_LATENESS_BELOW_THRESHOLD:
    "Số phút khai chưa vượt ngưỡng chủ tiệm công bố, nên theo chính sách đây chưa phải giao trễ. " +
    "Không có gì được ghi.",
  REMEDY_CREDIT_UNALLOCATABLE:
    "Khoản giảm trừ lớn hơn phần còn có thể giảm trên bản báo giá định trừ vào. Máy chủ từ chối " +
    "và giữ nguyên phiếu chứ không cắt bớt — cắt bớt là xoá một phần nợ tiệm đang nợ khách. Trừ " +
    "vào một hoá đơn lớn hơn.",
  REMEDY_CREDIT_REVISION_NOT_OPEN:
    "Bản báo giá định trừ vào đang là khoảng giá hoặc khách đã chốt rồi. Giảm trừ chỉ đổi tổng " +
    "trước khi báo cho khách, không đổi tổng đã thoả thuận. Lập bản mới rồi trừ vào bản đó.",
  REMEDY_CREDIT_ALREADY_REDEEMED:
    "Phiếu giảm trừ này đã dùng rồi. Phiếu là vật cầm tay, dùng đúng một lần — không phải lỗi của " +
    "bạn, và không trừ thêm lần nữa. Mở lại báo giá cũ để xem lần đã trừ.",
  REMEDY_APPROVAL_REQUIRED:
    "Khoản này trên mức nhân viên duyệt được, nên phải có phiếu duyệt của chủ tiệm trước khi thực " +
    "hiện. Mở màn hình Duyệt; chưa duyệt mà bấm thực hiện thì máy chủ từ chối.",
  REMEDY_APPROVAL_EXPIRED:
    "Phiếu duyệt của chủ tiệm đã hết hạn. Phiếu có thời hạn ngắn và không tự gia hạn. Gửi lại đề " +
    "nghị để lập phiếu mới, đừng chờ thêm.",
  REMEDY_APPROVAL_NOT_BOUND:
    "Phiếu duyệt không gắn đúng với đề nghị đang thực hiện — khác cửa hàng, khác đề nghị, hoặc nội " +
    "dung đã đổi từ lúc chủ tiệm ký. Không có gì được ghi. Gửi lại đề nghị để lập phiếu mới.",
  REMEDY_ALREADY_EXECUTED:
    "Đề nghị này đã được thực hiện rồi — không phải lỗi của bạn, và không làm lại lần nữa. Mở lại " +
    "sự cố để xem kết quả đã ghi.",
  REMEDY_PROPOSAL_NOT_FOUND:
    "Không có đề nghị bồi hoàn nào mang mã này. Kiểm tra lại mã đã chép.",
  REMEDY_CREDIT_NOT_FOUND:
    "Không có khoản giảm trừ nào mang mã này ở cửa hàng đang chọn. Phiếu phát ở tiệm nào thì dùng " +
    "ở tiệm đó.",
  REMEDY_INCIDENT_NOT_FOUND:
    "Không có sự cố nào mang mã này ở cửa hàng đang chọn, hoặc sự cố không gắn với đơn nào. Kiểm " +
    "tra lại mã sự cố trên màn hình Sự cố.",
  REMEDY_ORDER_REVISION_UNREADABLE:
    "Máy chủ không đọc được bản báo giá của đơn này nên không tính được trần nào. Đây là lỗi dữ " +
    "liệu, không phải chữ bạn gõ: chụp màn hình và báo kỹ thuật, đừng gõ lại kiểu khác.",
  REMEDY_ORDER_TIMESTAMP_INVALID:
    "Mốc thời gian lưu trên đơn không đọc được nên không đo được cửa sổ nào. Đây là lỗi dữ liệu, " +
    "không phải chữ bạn gõ: chụp màn hình và báo kỹ thuật.",
  CONTACT_BINDING_UNKNOWN:
    "Không có liên hệ nào mang mã này. Liên hệ chỉ được tạo từ một hội thoại kênh đã xác minh; " +
    "màn hình tiếp nhận không tạo liên hệ mới.",
};

/** Server enum values, glossed. The value itself is always displayed too. */
export const ENUM_GLOSS = {
  // CommercialOrderStatus
  DRAFT: "nháp",
  REQUESTED: "đã yêu cầu",
  STORE_CONFIRMATION_PENDING: "chờ cửa hàng xác nhận",
  CONFIRMED: "đã xác nhận",
  ACTIVE: "đang chạy",
  CANCELLATION_REVIEW: "đang xét huỷ",
  CANCELLED: "đã huỷ",
  COMPLETED: "đã hoàn tất",
  // IntakeStatus
  AWAITING_HANDOFF: "chờ nhận đồ",
  RECEIVED_PENDING_INSPECTION: "đã nhận, chờ kiểm",
  WAITING_PRICE_APPROVAL: "chờ duyệt giá",
  WAITING_CUSTOMER_RECONFIRMATION: "chờ khách xác nhận lại",
  WAITING_SLOT_APPROVAL: "chờ duyệt lịch",
  ACCEPTED: "đã nhận",
  REJECTED: "đã từ chối",
  // ProductionStatus
  NOT_STARTED: "chưa bắt đầu",
  QUEUED: "đã xếp hàng",
  IN_PROCESS: "đang giặt",
  QUALITY_CHECK: "đang kiểm tra",
  READY_AT_STORE: "sẵn sàng tại cửa hàng",
  RELEASED: "đã giao ra",
  ON_HOLD: "tạm dừng",
  EXCEPTION: "sự cố",
  // DEC-024. `CustodyResolution`: what a named staff member says happened to the laundry and the
  // money when an order is cancelled after work began.
  NOT_RECEIVED: "chưa nhận đồ",
  RETURNED_UNWASHED_REFUNDED: "đã trả đồ chưa giặt và hoàn tiền",
  SHOP_FAULT_NO_CHARGE: "lỗi tiệm, không thu tiền",
  // OrderBalanceStatus
  UNPAID: "chưa thanh toán",
  PARTIALLY_PAID: "thanh toán một phần",
  PAID: "đã thanh toán",
  OVERPAID: "thu thừa",
  ON_ACCOUNT: "ghi nợ",
  // OrderRequestStatus — `order_requests.status CHECK (status IN ('DRAFT','SUBMITTED','CANCELLED'))`;
  // DRAFT and CANCELLED share the glosses declared above with the same meaning here
  SUBMITTED: "đã gửi",
  // QuoteRevisionStatus
  PROVISIONAL: "tạm thời",
  REVIEW_REQUIRED: "cần xem xét",
  APPROVED: "đã duyệt",
  PRESENTED: "đã gửi khách",
  ACKNOWLEDGED_ESTIMATE: "khách đã nhận ước tính",
  ACCEPTED_FINAL: "khách đã chốt",
  SUPERSEDED: "đã bị thay thế",
  EXPIRED: "đã hết hạn",
  // FulfillmentMode
  SELF_DROP_SELF_COLLECT: "khách tự mang tới và tự lấy",
  PICKUP_AND_RETURN: "lấy và trả tận nơi",
  PICKUP_ONLY: "chỉ lấy",
  RETURN_ONLY: "chỉ trả",
  // Unit
  KG: "kilôgam",
  ITEM: "cái",
  PAIR: "đôi",
  SET: "bộ",
  ANIMAL_PLUSH_ITEM: "thú bông",
  CASE: "vỏ",
  M2: "mét vuông",
  // QuantityBasis
  CUSTOMER_ESTIMATE: "khách tự ước",
  STAFF_MEASUREMENT: "nhân viên đã cân",
  APPROVED_MANUAL: "nhập tay có duyệt",
  // Roles
  OWNER_ADMIN: "chủ / quản trị",
  OPS_APPROVER: "người duyệt vận hành",
  OPERATOR: "nhân viên vận hành",
  DRIVER: "tài xế",
  ACCOUNTANT: "kế toán",
  AUDITOR: "kiểm toán",
  // Reconciliation and sends
  UNKNOWN: "chưa rõ kết quả",
  UNKNOWN_REQUIRES_HUMAN: "chưa rõ, cần người xử lý",
  CONFIRMED_SENT: "xác nhận đã gửi",
  CONFIRMED_NOT_SENT: "xác nhận chưa gửi",
  APPROVED_FOR_MANUAL_SEND: "đã khoá để gửi thủ công",
  MANUAL_SEND_RECORDED: "đã ghi nhận người gửi tay",
  // Actor types on the audit timeline
  STAFF: "nhân viên",
  AGENT_RUNNER: "tiến trình chạy agent",
  OUTBOX_WORKER: "tiến trình gửi",
  BOOTSTRAP: "khởi tạo hệ thống",
  // Assistant intents
  GREETING: "lời chào",
  TODAY_OVERVIEW: "tình hình hôm nay",
  SLA_RISK: "nguy cơ trễ SLA",
  PENDING_APPROVALS: "chờ phê duyệt",
  ORDER_LOOKUP: "tra cứu đơn",
  REVENUE_UNAVAILABLE: "doanh thu chưa kết nối",
  UNSUPPORTED: "chưa trả lời được",
  // Incident status — `customer_incidents.status CHECK (status IN ('OPEN','UNDER_REVIEW','CLOSED'))`
  OPEN: "đang mở",
  UNDER_REVIEW: "đang xem xét",
  CLOSED: "đã đóng",
  // Incident categories — `IncidentRepository` admits exactly these two
  SERVICE_QUALITY: "chất lượng dịch vụ",
  AUTOMATED_MESSAGE_ERROR: "lỗi tin nhắn tự động",
  // Shadow draft review decisions — `agent_draft_reviews.decision`. Deliberately kept apart from
  // the approval-envelope vocabulary above (`APPROVED`/`REJECTED`), which decides a different
  // aggregate entirely; `screens/shadow.js` explains why the two must never be mixed. These are
  // the imperative a reviewer presses, not the state a request ends in.
  APPROVE: "dùng được",
  EDIT: "sửa lại",
  REJECT: "không dùng được",
  // Agent run terminal outcome — `agent_drafts.terminal_outcome IN ('DRAFT','REQUIRE_HUMAN')`;
  // DRAFT shares the order-status gloss "nháp" above, which is what it means here too
  REQUIRE_HUMAN: "cần người quyết định",
  // Agent run terminal codes — the fixed vocabulary `responses_runtime` and the worker pipeline
  // emit. Model-supplied reason codes pass through untouched and render raw, which is deliberate:
  // a code this map does not know must look unfamiliar.
  DRAFT_REQUIRES_HUMAN: "bản nháp chờ người duyệt",
  VALIDATED_DRAFT: "bản nháp hợp lệ",
  MODEL_REQUESTED_HANDOFF: "mô hình xin chuyển cho người",
  PROVIDER_CONNECTION_FAILURE: "không kết nối được nhà cung cấp",
  PROVIDER_OUTCOME_AMBIGUOUS: "kết quả từ nhà cung cấp không rõ",
  PROVIDER_REQUEST_CANCELLED: "yêu cầu tới nhà cung cấp bị huỷ",
  PROVIDER_TIMEOUT: "nhà cung cấp không trả lời kịp",
  CONTEXT_REJECTED: "gói ngữ cảnh bị từ chối",
  BUDGET_EXHAUSTED: "hết hạn mức cho lượt chạy",
  TOOL_BRIDGE_REJECTED: "cầu nối công cụ từ chối",
  INVALID_PROVIDER_OUTPUT: "kết quả nhà cung cấp không hợp lệ",
  UNEXPECTED_RUNTIME_FAILURE: "tiến trình chạy agent gặp lỗi không mong đợi",
  // Audit actions visible on the order timeline — the `audit_action` of every material change
  // whose aggregate id is the order's
  ORDER_CREATE_FROM_FINAL_QUOTE: "tạo đơn từ báo giá đã chốt",
  ORDER_STATE_TRANSITION: "chuyển trạng thái đơn",
  ORDER_SETTLEMENT_RECORD: "ghi nhận tất toán",
};

/**
 * `AcquisitionSource`, glossed separately from `ENUM_GLOSS` because it collides with it.
 *
 * `ENUM_GLOSS` is one flat map across every server enum, which works only while no two enums share
 * a member name. `AcquisitionSource.UNKNOWN` is the first that does: the map already binds
 * `UNKNOWN` to "chưa rõ kết quả" for send reconciliation, where it means the shop does not know
 * whether a message arrived. Here it means nobody asked the customer where they came from. Those
 * are different facts and no single gloss serves both — folding them together would put "chưa rõ
 * kết quả" into a form field about how someone found the shop.
 *
 * A later duplicate key in a JavaScript object literal silently wins over the earlier one, so this
 * collision would never have failed anywhere. It would have quietly relabelled the send column.
 * Hence a scoped map passed explicitly, and not a second `UNKNOWN`.
 */
export const ACQUISITION_SOURCE_VI = {
  WALK_IN: "đi ngang qua, vào luôn",
  GOOGLE_MAPS: "tìm trên Google",
  ZALO: "qua Zalo",
  FACEBOOK: "qua Facebook",
  PARTNER_FRONT_DESK: "lễ tân đối tác giới thiệu",
  REFERRAL_CUSTOMER: "khách cũ giới thiệu",
  LEAFLET_QR: "tờ rơi hoặc mã QR",
  // Reported speech, and the wording says so. No customer record exists (`DEC-015`), so nothing
  // here verified it; a bare "khách cũ" would read as a fact the database had checked.
  RETURNING: "khách nói đã từng dùng",
  // A state of knowledge, not a missing entry.
  UNKNOWN: "chưa biết",
};

/**
 * The Vietnamese name of a server enum, capitalized for standalone display.
 *
 * Vietnamese is the reading language of this console; the raw token remains available wherever
 * precision matters (selects keep it in parentheses, badges keep it as a secondary caption, and
 * the app bar keeps it in the tooltip), because it is what an engineer greps for.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function enumVi(value) {
  if (!value) return "—";
  const gloss = ENUM_GLOSS[value];
  if (!gloss) return String(value);
  return gloss.charAt(0).toUpperCase() + gloss.slice(1);
}

/**
 * Vietnamese headings for the published pricebook's service categories.
 *
 * These are not server enums, which is why they live apart from `ENUM_GLOSS`. They are values of a
 * free-text column in the owner's own pricebook CSV (`templates/services-pricebook.csv`), and they
 * arrive lowercase and English — `dry_cleaning`, `standard_weight`. The service *names* in that
 * file are already Vietnamese; only the grouping key is not, so only the grouping key is mapped.
 *
 * An unmapped category falls through to its raw value on purpose. The picker must not invent a
 * heading for a category the owner added after this map was written — an unfamiliar-looking group
 * label is a prompt to update this file, whereas a plausible guess would hide the omission.
 */
const SERVICE_CATEGORY = {
  standard_weight: "Giặt sấy theo ký",
  drying: "Sấy riêng",
  bedding: "Chăn ga gối",
  ironing: "Ủi",
  leather: "Đồ da",
  shoes: "Giày dép",
  dry_cleaning: "Giặt khô",
  other: "Dịch vụ khác",
};

/**
 * The Vietnamese heading for one pricebook service category.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function serviceCategoryVi(value) {
  if (!value) return "Khác";
  return SERVICE_CATEGORY[value] || String(value);
}

/**
 * `Gloss (VALUE)` — Vietnamese first, the verbatim token in parentheses. Never gloss-only: an
 * operator who learns that `WAITING_SLOT_APPROVAL` is the thing the API says can talk to an
 * engineer about it, and a translated-only UI makes that conversation impossible.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function enumLabel(value) {
  if (!value) return "—";
  const gloss = ENUM_GLOSS[value];
  return gloss ? `${enumVi(value)} (${value})` : String(value);
}

/** Screen and navigation titles. */
export const NAV = {
  today: "Hôm nay",
  orderRequests: "Tiếp nhận",
  quotes: "Báo giá",
  orders: "Đơn hàng",
  approvals: "Duyệt",
  shadow: "Bản nháp AI",
  assistant: "Trợ lý AI",
  exceptions: "Ngoại lệ",
  incidents: "Sự cố",
  remedies: "Bồi hoàn",
  system: "Hệ thống",
  staff: "Nhân sự",
  unsupported: "Việc chưa hỗ trợ",
};
