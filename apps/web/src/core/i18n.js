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
  PRICE_RULE_UNRESOLVED: "Không có quy tắc giá áp dụng được cho dòng này.",
  MEASUREMENT_POLICY_UNRESOLVED: "Chưa có chính sách đo lường được công bố cho cơ sở khối lượng này.",
  AMBIGUOUS_SERVICE: "Mã dịch vụ khớp nhiều mục; cần chọn rõ.",
  INCOMPATIBLE_UNIT: "Đơn vị không dùng được với dịch vụ này.",
  VALIDATION_ERROR: "Dữ liệu vào không hợp lệ với quy tắc miền.",
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
  system: "Hệ thống",
  staff: "Nhân sự",
  unsupported: "Việc chưa hỗ trợ",
};
