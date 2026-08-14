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

/** @typedef {"OFFLINE"|"NETWORK"|"TIMEOUT"|"SESSION_ENDED"|"DENIED"|"MISSING"|"CONFLICT"|"STALE"|"IDEMPOTENCY_CONFLICT"|"REQUIRE_HUMAN"|"INVALID"|"PRECONDITION_REQUIRED"|"TOO_LARGE"|"RATE_LIMITED"|"UNAVAILABLE"|"PRICEBOOK_UNAVAILABLE"|"FAULT"} ErrorKind */

export class ApiError extends Error {
  /**
   * @param {object} init
   * @param {ErrorKind} init.kind
   * @param {number} init.status HTTP status, or 0 when the request never reached the server
   * @param {string} init.message operator-facing Vietnamese text
   * @param {string} [init.detail] the server's own string, kept verbatim for the details panel
   * @param {string[]} [init.reasonCodes] domain reason codes, never paraphrased
   * @param {{field: string, message: string}[]} [init.fieldErrors]
   * @param {number} [init.retryAfterSeconds]
   * @param {string} [init.correlationId]
   */
  constructor(init) {
    super(init.message);
    this.name = "ApiError";
    this.kind = init.kind;
    this.status = init.status;
    this.detail = init.detail || "";
    this.reasonCodes = init.reasonCodes || [];
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
   */
  get retryable() {
    return this.kind === "NETWORK" || this.kind === "TIMEOUT" || this.kind === "RATE_LIMITED";
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
  CONFLICT: "Máy chủ từ chối vì trạng thái hiện tại.",
  STALE: "Dữ liệu đã thay đổi từ khi bạn mở màn hình. Hãy tải lại rồi làm lại.",
  IDEMPOTENCY_CONFLICT: "Cùng một khoá thao tác đã dùng cho nội dung khác. Hãy tải lại rồi nhập lại.",
  REQUIRE_HUMAN: "Cần người quyết định. Máy chủ không tự chọn.",
  INVALID: "Dữ liệu nhập không hợp lệ.",
  PRECONDITION_REQUIRED: "Thiếu phiên bản dòng dữ liệu. Hãy tải lại màn hình.",
  TOO_LARGE: "Nội dung quá lớn.",
  RATE_LIMITED: "Đang bị giới hạn tần suất.",
  UNAVAILABLE: "Dịch vụ tạm thời không sẵn sàng.",
  PRICEBOOK_UNAVAILABLE:
    "Chưa có bảng giá được duyệt cho cửa hàng này. Không có bảng giá thì không có giá.",
  FAULT: "Máy chủ gặp lỗi. Đừng thử lại — hãy kiểm tra bảng đơn để xem lệnh đã vào hay chưa.",
};

/**
 * @param {ErrorKind} kind
 * @param {Partial<ConstructorParameters<typeof ApiError>[0]>} [extra]
 * @returns {ApiError}
 */
export function apiError(kind, extra = {}) {
  return new ApiError({ kind, status: 0, message: MESSAGES[kind], ...extra });
}

/**
 * Pull `reason_codes` out of whatever shape the server used.
 *
 * @param {unknown} detail
 * @returns {string[]}
 */
function reasonCodesOf(detail) {
  if (detail && typeof detail === "object" && Array.isArray(detail.reason_codes)) {
    return detail.reason_codes.map(String);
  }
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

  if (status === 401) return of("SESSION_ENDED");
  if (status === 403) return of("DENIED");
  if (status === 404) return of("MISSING");
  if (status === 413) return of("TOO_LARGE");
  if (status === 428) return of("PRECONDITION_REQUIRED");
  if (status === 429) return of("RATE_LIMITED");

  if (status === 409) {
    if (text.startsWith("STALE_VERSION")) return of("STALE");
    if (text === "IDEMPOTENCY_CONFLICT") return of("IDEMPOTENCY_CONFLICT");
    if (text.startsWith("HUMAN_APPROVAL_REQUIRED")) {
      return of("REQUIRE_HUMAN", { reasonCodes: ["HUMAN_APPROVAL_REQUIRED"] });
    }
    return of("CONFLICT", { message: text || MESSAGES.CONFLICT });
  }

  if (status === 422) {
    // `create_quote` is the one route whose detail is an object, and its `outcome` is the domain's
    // own word for "a person has to decide this". It is not a validation failure and must not be
    // rendered as one.
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      if (detail.outcome === "REQUIRE_HUMAN") {
        return of("REQUIRE_HUMAN", { reasonCodes: reasonCodesOf(detail) });
      }
      return of("INVALID", { reasonCodes: reasonCodesOf(detail) });
    }
    if (Array.isArray(detail)) return of("INVALID", { fieldErrors: fieldErrorsOf(detail) });
    return of("INVALID", { message: text || MESSAGES.INVALID });
  }

  if (status === 503) {
    if (text === "pricebook unavailable") return of("PRICEBOOK_UNAVAILABLE");
    return of("UNAVAILABLE", { message: text || MESSAGES.UNAVAILABLE });
  }

  if (status >= 500) return of("FAULT");
  if (status === 400) return of("INVALID", { message: text || MESSAGES.INVALID });
  return of("CONFLICT", { message: text || MESSAGES.CONFLICT });
}
