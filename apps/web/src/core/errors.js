/**
 * The error taxonomy, derived from what this API actually returns.
 *
 * The specification describes an error envelope with `ok`, `trace_id` and a nested `error` object
 * (`DOMAIN_DATA_API_SPEC_V1.md:1336`). The server does not send that. It sends `{"detail": …}`
 * where `detail` is a string on most routes, an object on `create_quote`'s 422, and an array on
 * FastAPI's own validation failures. Writing a renderer against the specified shape would match
 * nothing, so this module is written against the observed one and says so.
 *
 * The distinction that matters most is between *refused* and *broken*. A refusal is the system
 * working: no store assignment, a stale row version, an unresolved policy. A fault is the system
 * failing. Only faults are worth retrying, and nothing here retries a write ever — a write that
 * may have landed must not be repeated by software.
 *
 * @module core/errors
 */

/** @typedef {"OFFLINE"|"NETWORK"|"TIMEOUT"|"SESSION_ENDED"|"DENIED"|"MISSING"|"DISPOSED"|"CONFLICT"|"STALE"|"IDEMPOTENCY_CONFLICT"|"REQUIRE_HUMAN"|"NOT_SUPPORTED"|"INVALID"|"PRECONDITION_REQUIRED"|"TOO_LARGE"|"RATE_LIMITED"|"BUSY"|"UNAVAILABLE"|"PRICEBOOK_UNAVAILABLE"|"FAULT"} ErrorKind */

/**
 * The contact a service send was refused for, as the egress refusal names it (`DEC-033`). The
 * manual-send panel reads it to open that contact's service-messaging state; the contact is the
 * opaque binding, never a phone number or a chat id.
 *
 * @typedef {object} ConsentRefusal
 * @property {string} reasonCode
 * @property {string} outcome `SUPPRESSED` or `REQUIRE_HUMAN`
 * @property {string} storeId
 * @property {string} contactBindingId
 * @property {string} channel
 */

export class ApiError extends Error {
  /**
   * @param {object} init
   * @param {ErrorKind} init.kind
   * @param {number} init.status HTTP status, or 0 when the request never reached the server
   * @param {string} init.message operator-facing Vietnamese text
   * @param {string} [init.detail] the server's own string, kept verbatim for the details panel
   * @param {string[]} [init.reasonCodes] domain reason codes, never paraphrased
   * @param {string} [init.decision] the open business decision that owns a `NOT_SUPPORTED` refusal
   * @param {{field: string, message: string}[]} [init.fieldErrors]
   * @param {number} [init.retryAfterSeconds]
   * @param {string} [init.correlationId]
   * @param {ConsentRefusal|null} [init.consent] who a refused service send was for (`DEC-033`)
   */
  constructor(init) {
    super(init.message);
    this.name = "ApiError";
    this.kind = init.kind;
    this.status = init.status;
    this.detail = init.detail || "";
    this.reasonCodes = init.reasonCodes || [];
    this.decision = init.decision || "";
    this.consent = init.consent || null;
    this.fieldErrors = init.fieldErrors || [];
    this.retryAfterSeconds = init.retryAfterSeconds ?? null;
    this.correlationId = init.correlationId || "";
  }

  /**
   * Whether re-issuing the *same request* is a sensible offer to the operator.
   *
   * Never true for a fault on a write: a 500 from this API can mean the command committed and the
   * response did not, and the idempotency ledger only protects a retry that carries the same key —
   * which the caller may or may not still hold. Offering a button is how a customer gets charged
   * twice, so the operator is told to check the board instead.
   *
   * `BUSY` is the opposite case and is retryable for the opposite reason (`API-INTEGRITY-003`): the
   * server says exactly what happened -- a statement or lock wait ran out of time and its
   * transaction rolled back, or no connection could be opened at all -- and a same-key retry of
   * anything that did commit replays rather than repeats.
   */
  get retryable() {
    return (
      this.kind === "NETWORK" ||
      this.kind === "TIMEOUT" ||
      this.kind === "RATE_LIMITED" ||
      this.kind === "BUSY"
    );
  }

  /** Whether the operator's typed input is still valid and can simply be re-submitted. */
  get inputSurvives() {
    return this.kind !== "SESSION_ENDED";
  }
}

/** Messages are Vietnamese because the operator reads them; codes stay verbatim for the audit. */
const MESSAGES = {
  OFFLINE: "Mất kết nối mạng. Thao tác không được xếp hàng và không có gì được gửi đi.",
  NETWORK: "Không gọi được máy chủ. Không rõ lệnh đã tới nơi hay chưa.",
  TIMEOUT: "Máy chủ không trả lời kịp. Không rõ lệnh đã tới nơi hay chưa.",
  SESSION_ENDED: "Phiên đăng nhập đã kết thúc. Hãy đăng nhập lại.",
  DENIED: "Bạn không có quyền cho thao tác này.",
  MISSING: "Không tìm thấy đối tượng.",
  // Not the same thing as MISSING, and rendering it as one would be a small lie in the direction
  // that matters: "không tìm thấy" says the thing never existed, while a 410 says it existed, is
  // still on the books, and only its text was disposed of on a published schedule. The assistant
  // stream route answers this for a turn whose words `ASSISTANT_TRANSCRIPT` purged at 180 days.
  DISPOSED:
    "Phần chữ của mục này đã bị xoá theo lịch giữ dữ liệu. Bản ghi thì vẫn còn — đây là chuyện " +
    "bình thường, không phải mất dữ liệu.",
  CONFLICT: "Máy chủ từ chối vì trạng thái hiện tại.",
  STALE: "Dữ liệu đã thay đổi từ khi bạn mở màn hình. Hãy tải lại rồi làm lại.",
  IDEMPOTENCY_CONFLICT: "Cùng một khoá thao tác đã dùng cho nội dung khác. Hãy tải lại rồi nhập lại.",
  REQUIRE_HUMAN: "Cần người quyết định. Máy chủ không tự chọn.",
  // Not the same thing as invalid input, and rendering it as one is what sends a staff member
  // looking for a workaround: the shop has decided this case is not taken, or the order is not in
  // a state where it applies. `reason_code` says which, and `decision` names the owner decision
  // behind it when there is one -- there is not always: `ALREADY_SETTLED` and `ORDER_NOT_ACTIVE`
  // come with `decision: null`.
  //
  // This sentence used to add "và dữ liệu bạn nhập không sai" for every code. That is false for
  // the commonest one: `AMOUNT_IS_NOT_THE_EXACT_TOTAL` is what a cashier gets for typing "13.200"
  // against a 132.000 total, and telling them their input was not wrong -- and not to retype it --
  // kept a keystroke error on the till. That code now has its own sentence in `REFUSAL` below; this
  // one no longer claims anything about the input.
  NOT_SUPPORTED:
    "Máy chủ không ghi nhận khoản này. Lý do bên dưới nói rõ vì sao và cần làm gì; đừng nhập " +
    "kiểu khác để lách.",
  INVALID: "Dữ liệu nhập không hợp lệ.",
  PRECONDITION_REQUIRED: "Thiếu phiên bản dòng dữ liệu. Hãy tải lại màn hình.",
  TOO_LARGE: "Nội dung quá lớn.",
  RATE_LIMITED: "Đang bị giới hạn tần suất.",
  // `API-INTEGRITY-003`. A 503 whose `reason_code` is `DATABASE_BUSY`: before it, a statement that
  // outran `statement_timeout` was a 500 and the counter read FAULT's "Đừng thử lại" -- the right
  // advice when nobody knows whether the write landed, and the wrong advice here, where the server
  // does know. The retry is safe because the same idempotency key travels with it.
  BUSY:
    "Hệ thống đang bận nên chưa làm được lệnh này. Chờ vài giây rồi thử lại; bấm lại không bị " +
    "ghi hai lần.",
  UNAVAILABLE: "Dịch vụ tạm thời không sẵn sàng.",
  PRICEBOOK_UNAVAILABLE:
    "Chưa có bảng giá được duyệt cho cửa hàng này. Không có bảng giá thì không có giá.",
  FAULT: "Máy chủ gặp lỗi. Đừng thử lại — hãy kiểm tra bảng đơn để xem lệnh đã vào hay chưa.",
};

/**
 * Short Vietnamese titles for the refusals this API really sends, keyed by a stable name.
 *
 * Most 409s carry `str(error)` from a repository -- English prose written for an engineer -- and
 * `classify` used to put that prose in the notice title. So a counter holding a customer's bag read
 * "this order request already has a quote; add a revision instead" as the headline of the screen.
 * The prose is still kept, verbatim, as `detail`: `errorNotice` shows it collapsed under "Chi tiết
 * kỹ thuật", because it is what an engineer greps the server for.
 *
 * Every key here is reached through `REFUSAL_TEXT` below, and every string there was copied from
 * a `raise` in `apps/api` or `packages/db`/`packages/domain`, not written from memory.
 */
const REFUSAL = {
  QUOTE_EXPIRED:
    "Báo giá này đã quá hạn nên không chốt được. Tính giá lại, rồi đọc giá mới cho khách.",
  QUOTE_ALREADY_EXISTS:
    "Yêu cầu này đã có báo giá rồi. Hãy thêm bản sửa đổi cho báo giá đó, không tạo báo giá mới.",
  QUOTE_ALREADY_ACCEPTED:
    "Bản giá này đã được chốt rồi. Tải lại danh sách báo giá để xem bản đã chốt.",
  QUOTE_MOVED:
    "Báo giá vừa đổi trong lúc bạn đang xem. Tải lại, rồi đọc lại giá cho khách.",
  QUOTE_STALE:
    "Báo giá đã có bản mới hơn hoặc đã đóng. Tải lại danh sách báo giá, chọn bản mới nhất rồi làm lại.",
  QUOTE_MISSING: "Không tìm thấy báo giá này trong cửa hàng đang chọn, hoặc báo giá đã đóng.",
  QUOTE_NOT_ACCEPTED:
    "Chưa tạo đơn được: báo giá chưa được khách chốt, đã có bản mới hơn, hoặc đã quá hạn. Mở " +
    "báo giá, chốt lại với khách rồi mới tạo đơn.",
  NEWER_PRICE_AGREED:
    "Khách đã chốt một giá mới hơn cho báo giá này. Dùng bản đã chốt mới nhất và đọc lại cho khách.",
  QUOTE_ALREADY_ORDERED:
    "Báo giá này đã được dùng để tạo đơn rồi. Mỗi báo giá chỉ tạo một đơn — tìm đơn đó trên bảng đơn.",
  BALANCE_NOT_SETTLED: "Chưa hoàn tất được: đơn chưa thu tiền. Ghi nhận tất toán trước.",
  FULFILLMENT_INCOMPLETE: "Chưa hoàn tất được: đồ chưa được giao hoặc trả cho khách.",
  PRODUCTION_NOT_RELEASED: "Chưa hoàn tất được: đồ của đơn này chưa làm xong.",
  ORDER_CLOSED: "Đơn này đã đóng, không chuyển trạng thái được nữa.",
  ORDER_NOT_ACTIVE: "Đơn này không còn đang chạy, nên không làm tiếp bước này được.",
  INTAKE_NOT_ACCEPTED: "Chưa làm bước này được: tiệm chưa nhận xong đồ của đơn này.",
  HANDOFF_FIRST: "Cần ghi nhận đã nhận đồ từ khách trước, rồi mới làm bước này.",
  CANCEL_NEEDS_REVIEW:
    "Đơn đã bắt đầu làm nên không huỷ thẳng được. Đưa đơn sang “Đang xét huỷ” trước.",
  // The two contradictions `DEC-024` lets the record check. The console used to show the server's
  // English, which at least said *which* fact; the first Vietnamese pass dropped these to the
  // generic title and hid the fact under "Chi tiết kỹ thuật". Staff choosing how to cancel need
  // to be told what the order already records, in the headline.
  CUSTODY_RECORDED:
    "Không huỷ theo “chưa từng nhận đồ” được: đơn này đã ghi nhận tiệm nhận đồ của khách. " +
    "Chọn cách xử lý đúng với việc đồ đang ở tiệm.",
  PRODUCTION_BEGUN:
    "Không huỷ theo “trả lại, chưa giặt” được: đơn này đã ghi nhận bắt đầu giặt. " +
    "Chọn cách xử lý khác, hoặc báo chủ tiệm.",
  CANCEL_RESOLUTION_MISSING:
    "Huỷ đơn cần chọn trước cách xử lý đồ và tiền của khách. Chọn một cách rồi bấm lại.",
  PAID_RESOLUTION_UNCLEAR:
    "Đơn đã thu tiền, và cách xử lý này không nói khách có được hoàn tiền hay không, nên máy " +
    "không huỷ. Báo chủ tiệm.",
  CANCEL_BALANCE_UNSUPPORTED:
    "Công nợ của đơn đang ở dạng máy chưa hỗ trợ khi huỷ (DEC-010). Báo chủ tiệm; đừng huỷ " +
    "kiểu khác để lách.",
  INVALID_STATE_TRANSITION:
    "Đơn đang ở trạng thái không cho phép bước này. Tải lại đơn để xem trạng thái hiện tại.",
  ORDER_MISSING: "Không tìm thấy đơn này trong cửa hàng đang chọn.",
  APPROVAL_EXPIRED: "Phiếu duyệt đã hết hạn. Cần tạo phiếu mới rồi xin duyệt lại.",
  APPROVAL_NOT_PENDING: "Phiếu này đã được quyết định rồi. Tải lại hàng chờ để xem.",
  APPROVAL_STALE: "Phiếu vừa đổi trong lúc bạn đang xem. Tải lại hàng chờ rồi đọc lại phiếu.",
  // `API-INTEGRITY-003`. Not APPROVAL_STALE: the phiếu is exactly as it was, and what moved is the
  // thing it is about -- a quote priced again, a draft edited, an export's columns widened. Reloading
  // the queue shows the same phiếu, so the sentence says the phiếu itself is finished.
  RESOURCE_CHANGED_SINCE_REQUEST:
    "Nội dung mà phiếu này xin duyệt đã thay đổi sau khi phiếu được tạo (ví dụ báo giá vừa được " +
    "tính lại), nên không duyệt được nữa. Cần tạo phiếu mới cho nội dung hiện tại.",
  // A 503 with `reason_code: DATABASE_UNAVAILABLE`: the API could not open a database connection,
  // so the command never ran. Kind `BUSY` either way; this is the sentence for the longer outage.
  DATABASE_UNAVAILABLE:
    "Hệ thống tạm thời không kết nối được cơ sở dữ liệu nên chưa làm được lệnh này. Chờ một lúc " +
    "rồi thử lại; nếu vẫn không được, báo chủ tiệm.",
  ROW_VERSION_INVALID: "Phiên bản dòng không hợp lệ. Tải lại màn hình rồi làm lại.",
  // The one `NOT_SUPPORTED` code that *is* a typing mistake as often as it is a customer paying
  // the wrong amount. DEC-010 is resolved: the shop takes the exact total in one payment, and
  // nothing else. So the sentence says what is true at the till -- check the figure and type it
  // again -- instead of the general refusal's "đừng nhập kiểu khác để lách".
  AMOUNT_IS_NOT_THE_EXACT_TOTAL:
    "Máy chủ không ghi nhận khoản này: số tiền phải đúng bằng tổng của đơn. Kiểm tra lại số vừa " +
    "gõ rồi nhập lại.",
  // `CONSENT-TRANSACTIONAL-001` (`DEC-033`). Why a service message was refused, and why a release
  // was. Each one is what the server's egress guard or release decided; none is a suggestion to
  // find another way of sending.
  SERVICE_SUPPRESSED:
    "Khách đã yêu cầu dừng nhận tin trên kênh này. Tiệm không chủ động gửi gì trên kênh này — kể " +
    "cả tin dịch vụ — cho đến khi chủ tiệm hoặc người duyệt gỡ chặn dựa trên một tin nhắn mới của " +
    "chính khách. Không có gì được ghi.",
  SERVICE_PENDING_REVIEW:
    "Khách vừa nhắn một câu có thể là yêu cầu dừng nhận tin, và câu đó đang chờ người đọc lại. " +
    "Chưa gửi được gì trên kênh này cho đến khi chủ tiệm hoặc người duyệt xem xong.",
  SERVICE_SUPPRESSION_UNKNOWN:
    "Trạng thái nhận tin của khách trên kênh này không rõ, nên máy không gửi. Báo chủ tiệm; " +
    "không có gì được ghi.",
  MESSAGING_POLICY_UNPUBLISHED:
    "Chủ tiệm chưa công bố chính sách tin dịch vụ, nên hệ thống chưa cho gửi tin dịch vụ nào. " +
    "Chỉ chủ tiệm công bố được; không có gì được ghi.",
  NO_SERVICE_BASIS:
    "Chưa có căn cứ để gửi tin dịch vụ cho khách này: khách không nhắn cho tiệm gần đây, và khách " +
    "không có đơn nào đang mở hay vừa đóng. Đợi khách nhắn cho tiệm trước; không có gì được ghi.",
  RELEASE_EVIDENCE_INVALID:
    "Tin nhắn được chọn không phải tin của chính khách này trên kênh này, gửi sau khi khách yêu cầu " +
    "dừng, nên không gỡ chặn được bằng tin đó. Tải lại và chọn trong danh sách máy chủ đưa ra.",
  NOTHING_TO_RELEASE:
    "Khách này không bị chặn tin dịch vụ trên kênh này, nên không có gì để gỡ. Tải lại để xem trạng " +
    "thái hiện tại.",
};

/**
 * Server reason code -> `REFUSAL` key, for the refusals `DEC-033` introduced. The codes are the
 * server's (`TransactionalRefusal`, `TransactionalConsentStateError.reason_code`), matched exactly.
 *
 * @type {Readonly<Record<string, keyof typeof REFUSAL>>}
 */
const CONSENT_REFUSAL_KEY = {
  SUPPRESSED: "SERVICE_SUPPRESSED",
  PENDING_REVIEW: "SERVICE_PENDING_REVIEW",
  SUPPRESSION_UNKNOWN: "SERVICE_SUPPRESSION_UNKNOWN",
  MESSAGING_POLICY_UNPUBLISHED: "MESSAGING_POLICY_UNPUBLISHED",
  NO_SERVICE_BASIS: "NO_SERVICE_BASIS",
  RELEASE_EVIDENCE_INVALID: "RELEASE_EVIDENCE_INVALID",
  NOTHING_TO_RELEASE: "NOTHING_TO_RELEASE",
};

/**
 * The Vietnamese sentence for a `DEC-033` reason code, or "" for a code this console does not know
 * -- in which case the caller shows the generic sentence and the code itself.
 *
 * @param {string|null|undefined} code
 * @returns {string}
 */
export function consentRefusalText(code) {
  const key = code ? CONSENT_REFUSAL_KEY[code] : undefined;
  return key ? REFUSAL[key] : "";
}

/**
 * Server text -> `REFUSAL` key. Matched by prefix, most specific first, because several refusals
 * share a machine-readable prefix (`INVALID_STATE_TRANSITION: …`) and only the tail says which.
 *
 * @type {ReadonlyArray<readonly [string, keyof typeof REFUSAL]>}
 */
const REFUSAL_TEXT = [
  // apps/api operations.py -- accept_quote / create_quote / range prices
  ["QUOTE_EXPIRED", "QUOTE_EXPIRED"],
  ["this order request already has a quote", "QUOTE_ALREADY_EXISTS"],
  ["quote moved since it was read", "QUOTE_MOVED"],
  ["quote content changed since it was read", "QUOTE_MOVED"],
  ["quote is missing, closed, or not in this store", "QUOTE_MISSING"],
  ["quote is missing or not in this store", "QUOTE_MISSING"],
  // packages/db quotes.py
  ["this quote revision has already been accepted", "QUOTE_ALREADY_ACCEPTED"],
  ["quote is missing, closed, or stale", "QUOTE_STALE"],
  ["quote revision must be sequential", "QUOTE_STALE"],
  // packages/db orders.py -- create_order
  ["accepted exact quote is missing, stale, or expired", "QUOTE_NOT_ACCEPTED"],
  ["the customer agreed a newer price for this quote", "NEWER_PRICE_AGREED"],
  ["this quote has already been converted into an order", "QUOTE_ALREADY_ORDERED"],
  ["order is missing", "ORDER_MISSING"],
  // packages/domain orders.py -- transitions
  ["INVALID_STATE_TRANSITION: balance is not settled", "BALANCE_NOT_SETTLED"],
  ["INVALID_STATE_TRANSITION: fulfillment is incomplete", "FULFILLMENT_INCOMPLETE"],
  ["INVALID_STATE_TRANSITION: production is not released", "PRODUCTION_NOT_RELEASED"],
  ["INVALID_STATE_TRANSITION: order is closed", "ORDER_CLOSED"],
  ["INVALID_STATE_TRANSITION: order is not active", "ORDER_NOT_ACTIVE"],
  ["INVALID_STATE_TRANSITION: intake is not accepted", "INTAKE_NOT_ACCEPTED"],
  ["INVALID_STATE_TRANSITION: handoff must be recorded first", "HANDOFF_FIRST"],
  ["INVALID_STATE_TRANSITION: the order records custody of the goods", "CUSTODY_RECORDED"],
  ["INVALID_STATE_TRANSITION: production has begun on the goods", "PRODUCTION_BEGUN"],
  ["INVALID_STATE_TRANSITION", "INVALID_STATE_TRANSITION"],
  ["HUMAN_APPROVAL_REQUIRED: work has begun", "CANCEL_NEEDS_REVIEW"],
  ["HUMAN_APPROVAL_REQUIRED: cancellation resolution is incomplete", "CANCEL_RESOLUTION_MISSING"],
  ["HUMAN_APPROVAL_REQUIRED: the order was paid, and this resolution", "PAID_RESOLUTION_UNCLEAR"],
  ["NOT_SUPPORTED: cancelling an order with this balance", "CANCEL_BALANCE_UNSUPPORTED"],
  // packages/db approvals.py, apps/api operations.py
  ["approval expired", "APPROVAL_EXPIRED"],
  ["approval is not pending", "APPROVAL_NOT_PENDING"],
  ["approval decision is stale", "APPROVAL_STALE"],
  ["approval state is stale", "APPROVAL_STALE"],
  ["approval resource version or hash is stale", "APPROVAL_STALE"],
  ["RESOURCE_CHANGED_SINCE_REQUEST", "RESOURCE_CHANGED_SINCE_REQUEST"],
  ["approval policy version is stale", "APPROVAL_STALE"],
  // apps/api main.py _parse_if_match
  ["If-Match is invalid", "ROW_VERSION_INVALID"],
];

/**
 * The Vietnamese title for a refusal the server phrased in English, or "" when it is not one this
 * console recognises -- in which case the caller uses the generic sentence for the kind.
 *
 * @param {string} text
 * @returns {string}
 */
export function refusalTitle(text) {
  if (!text) return "";
  for (const [prefix, key] of REFUSAL_TEXT) {
    if (text.startsWith(prefix)) return REFUSAL[key];
  }
  return "";
}

/**
 * @param {ErrorKind} kind
 * @param {Partial<ConstructorParameters<typeof ApiError>[0]>} [extra]
 * @returns {ApiError}
 */
export function apiError(kind, extra = {}) {
  return new ApiError({ kind, status: 0, message: MESSAGES[kind], ...extra });
}

/**
 * Pull reason codes out of whatever shape the server used.
 *
 * Two shapes exist and both are load-bearing. `create_quote` and the assistant send
 * `reason_codes` as an array; `record_settlement` sends a single `reason_code` alongside the
 * `decision` that owns it (`main.py:1125-1131`). Reading only the plural one threw the singular
 * away, and the route it belongs to is the one that takes money: a customer paying 165.000 against
 * a 170.000 total produced `AMOUNT_IS_NOT_THE_EXACT_TOTAL`/`DEC-010` on the wire and the words
 * "Dữ liệu nhập không hợp lệ" on the screen — which is not true, and tells the operator to correct
 * a keystroke that was never wrong.
 *
 * @param {unknown} detail
 * @returns {string[]}
 */
function reasonCodesOf(detail) {
  if (!detail || typeof detail !== "object") return [];
  if (Array.isArray(detail.reason_codes)) return detail.reason_codes.map(String);
  if (typeof detail.reason_code === "string" && detail.reason_code) return [detail.reason_code];
  return [];
}

/**
 * FastAPI's own validation 422 is an array of `{type, loc, msg}`. Turn it into field errors that a
 * form can attach to inputs, keeping the server's message rather than inventing a friendlier one.
 *
 * @param {unknown} detail
 * @returns {{field: string, message: string}[]}
 */
function fieldErrorsOf(detail) {
  if (!Array.isArray(detail)) return [];
  return detail.map((item) => {
    const location = Array.isArray(item?.loc) ? item.loc : [];
    const named = location.filter((part) => typeof part === "string" && part !== "body");
    return { field: named.join(".") || "—", message: String(item?.msg || "không hợp lệ") };
  });
}

/**
 * Classify a non-OK response into the taxonomy.
 *
 * Several 409s carry a machine-readable prefix in an otherwise human string — `STALE_VERSION:`,
 * `IDEMPOTENCY_CONFLICT`, `INVALID_STATE_TRANSITION:`, `HUMAN_APPROVAL_REQUIRED:`. Those prefixes
 * are the closest thing this API has to an error code, so they are matched on rather than the rest
 * of the sentence, which is prose and may change.
 *
 * @param {number} status
 * @param {unknown} detail parsed `detail` member, or "" when the body was not JSON
 * @param {{retryAfterSeconds?: number|null, correlationId?: string}} context
 * @returns {ApiError}
 */
export function classify(status, detail, context = {}) {
  const text = typeof detail === "string" ? detail : "";
  const base = {
    status,
    detail: text || (detail ? JSON.stringify(detail) : ""),
    correlationId: context.correlationId || "",
    retryAfterSeconds: context.retryAfterSeconds ?? null,
  };

  /** @param {ErrorKind} kind @param {object} [more] */
  const of = (kind, more = {}) =>
    new ApiError({ kind, message: MESSAGES[kind], ...base, ...more });

  // A recognised refusal gets its Vietnamese title; anything else gets the kind's generic one. The
  // server's own text is never the title any more -- it stays in `detail`, shown collapsed.
  const titled = (/** @type {ErrorKind} */ kind) => refusalTitle(text) || MESSAGES[kind];

  if (status === 401) return of("SESSION_ENDED");
  if (status === 403) return of("DENIED");
  if (status === 404) return of("MISSING");
  if (status === 410) return of("DISPOSED");
  if (status === 413) return of("TOO_LARGE");
  if (status === 428) return of("PRECONDITION_REQUIRED");
  if (status === 429) return of("RATE_LIMITED");

  if (status === 409) {
    if (text.startsWith("STALE_VERSION")) return of("STALE");
    if (text === "IDEMPOTENCY_CONFLICT") return of("IDEMPOTENCY_CONFLICT");
    if (text.startsWith("HUMAN_APPROVAL_REQUIRED")) {
      return of("REQUIRE_HUMAN", {
        message: titled("REQUIRE_HUMAN"),
        reasonCodes: ["HUMAN_APPROVAL_REQUIRED"],
      });
    }
    return of("CONFLICT", { message: titled("CONFLICT") });
  }

  if (status === 422) {
    // `create_quote` is the one route whose detail is an object, and its `outcome` is the domain's
    // own word for "a person has to decide this". It is not a validation failure and must not be
    // rendered as one.
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      // `DEC-033`. A service send the egress guard refused, or a release the server would not
      // record. Matched on the decision the server names rather than on `outcome`, because a
      // SUPPRESSED refusal is neither `REQUIRE_HUMAN` nor a validation failure, and the generic
      // "Cần người quyết định" would hide that the customer asked the shop to stop.
      if (detail.decision === "DEC-033" && typeof detail.reason_code === "string") {
        const text = consentRefusalText(detail.reason_code);
        return of("REQUIRE_HUMAN", {
          message: text || MESSAGES.REQUIRE_HUMAN,
          reasonCodes: [detail.reason_code],
          decision: "DEC-033",
          consent:
            typeof detail.contact_binding_id === "string"
              ? {
                  reasonCode: detail.reason_code,
                  outcome: String(detail.outcome || ""),
                  storeId: String(detail.store_id || ""),
                  contactBindingId: detail.contact_binding_id,
                  channel: String(detail.channel || ""),
                }
              : null,
        });
      }
      if (detail.outcome === "REQUIRE_HUMAN") {
        return of("REQUIRE_HUMAN", { reasonCodes: reasonCodesOf(detail) });
      }
      // `NOT_SUPPORTED` is the settlement route's word for "the shop has not decided this case".
      // It shares the 422 status with validation failures and is the opposite of one.
      //
      // The second arm is the same refusal wearing a thinner envelope. `_raise_remedy_error` sends
      // `{reason_code, authority, ceiling_vnd?, window_closes_at?, threshold_minutes?}` and no
      // `outcome` at all, so every remedy refusal fell through to `INVALID` and reached the
      // counter as "Dữ liệu nhập không hợp lệ" — which is the precise sentence WORKFLOW-
      // CONFORMANCE-001 removed from the settlement path, for the precise reason that it is untrue
      // and sends a staff member to retype a number that was never wrong. `REMEDY_WINDOW_CLOSED`
      // is not bad input; it is the shop's own published window having closed, and the person
      // holding the customer's damaged shirt needs to be told that and not told to try again.
      //
      // Matched on the shape rather than on a `REMEDY_` prefix: what makes this a policy answer is
      // that the server named a single machine-readable reason for refusing, and a prefix test
      // would silently stop covering the next route that does the same thing.
      if (
        detail.outcome === "NOT_SUPPORTED" ||
        (!detail.outcome && typeof detail.reason_code === "string" && detail.reason_code)
      ) {
        const codes = reasonCodesOf(detail);
        // One of that route's codes is not a policy question at all: somebody else changed the
        // order while this screen had it open. It arrives wearing the same envelope as the rest,
        // and under the NOT_SUPPORTED heading the operator is told the shop does not do this --
        // when the only useful next move is the one `STALE` already offers, a reload.
        if (codes.includes("STALE_VERSION")) return of("STALE", { reasonCodes: codes });
        return of("NOT_SUPPORTED", {
          // A mistyped payment is the one refusal here whose honest answer is "type it again".
          ...(codes.includes("AMOUNT_IS_NOT_THE_EXACT_TOTAL")
            ? { message: REFUSAL.AMOUNT_IS_NOT_THE_EXACT_TOTAL }
            : {}),
          reasonCodes: codes,
          decision: typeof detail.decision === "string" ? detail.decision : "",
        });
      }
      return of("INVALID", { reasonCodes: reasonCodesOf(detail) });
    }
    if (Array.isArray(detail)) return of("INVALID", { fieldErrors: fieldErrorsOf(detail) });
    return of("INVALID", { message: titled("INVALID") });
  }

  if (status === 503) {
    if (text === "pricebook unavailable") return of("PRICEBOOK_UNAVAILABLE");
    // `API-INTEGRITY-003`: `{"reason_code": "DATABASE_BUSY" | "DATABASE_UNAVAILABLE"}` with a
    // `Retry-After`. Matched on the code, never on a sentence, and only on these two: any other
    // 503 keeps the generic UNAVAILABLE, which promises nothing about whether a retry is safe.
    const codes = reasonCodesOf(detail);
    if (codes.includes("DATABASE_BUSY")) return of("BUSY", { reasonCodes: codes });
    if (codes.includes("DATABASE_UNAVAILABLE")) {
      return of("BUSY", { message: REFUSAL.DATABASE_UNAVAILABLE, reasonCodes: codes });
    }
    return of("UNAVAILABLE", { message: titled("UNAVAILABLE") });
  }

  if (status >= 500) return of("FAULT");
  if (status === 400) return of("INVALID", { message: titled("INVALID") });
  return of("CONFLICT", { message: titled("CONFLICT") });
}
