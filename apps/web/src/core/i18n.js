/**
 * Vietnamese labels, and the spec-mandated tokens they gloss.
 *
 * The specification pulls in two directions. `IMPLEMENTATION_ROADMAP_V1.md:883` requires Vietnamese
 * labels; `:886` and `:896-904` mandate ten specific strings that are English uppercase tokens
 * (`HUMAN REQUIRED`, `TAX UNVERIFIED`, …) alongside three Vietnamese ones (`ƯỚC TÍNH`, `KHOẢNG GIÁ`,
 * `ĐÃ DUYỆT`). Nothing says which wins, and dropping either is a defect: the tokens are what an
 * eval or an auditor greps for, the Vietnamese is what the person at the counter reads.
 *
 * So both are rendered — the mandated token verbatim, then a Vietnamese gloss. `TAX UNVERIFIED ·
 * Chưa xác minh thuế`. The token is never translated away and never abbreviated.
 *
 * Enum values are a different matter. A server enum is shown verbatim, always, with the gloss
 * beside it rather than instead of it: an operator who learns that `WAITING_SLOT_APPROVAL` is the
 * thing the API says can talk to an engineer about it, and a translated-only UI makes that
 * conversation impossible.
 *
 * @module core/i18n
 */

/** The three price-state labels of `IMPLEMENTATION_ROADMAP_V1.md:886`. */
export const PRICE_STATE = {
  ESTIMATE: { token: "ƯỚC TÍNH", gloss: "chưa phải giá cuối", state: "info" },
  RANGE: { token: "KHOẢNG GIÁ", gloss: "chờ nhân viên chọn giá chính xác", state: "warn" },
  APPROVED_EXACT: { token: "ĐÃ DUYỆT", gloss: "giá chính xác đã được duyệt", state: "ok" },
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
  // OrderBalanceStatus
  UNPAID: "chưa thanh toán",
  PARTIALLY_PAID: "thanh toán một phần",
  PAID: "đã thanh toán",
  OVERPAID: "thu thừa",
  ON_ACCOUNT: "ghi nợ",
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
  AGENT_RUNNER: "bộ chạy agent",
  OUTBOX_WORKER: "worker gửi",
  BOOTSTRAP: "khởi tạo hệ thống",
};

/**
 * `VALUE · gloss`, or just the value when no gloss exists. Never gloss-only.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function enumLabel(value) {
  if (!value) return "—";
  const gloss = ENUM_GLOSS[value];
  return gloss ? `${value} · ${gloss}` : String(value);
}

/** Screen and navigation titles. */
export const NAV = {
  today: "Hôm nay",
  quotes: "Báo giá",
  orders: "Đơn hàng",
  approvals: "Duyệt",
  shadow: "Bản nháp AI",
  exceptions: "Ngoại lệ",
  incidents: "Sự cố",
  system: "Hệ thống",
  staff: "Nhân sự",
  unsupported: "Chưa hỗ trợ",
};
