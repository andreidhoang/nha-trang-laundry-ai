/**
 * The shop's customer list (`CUSTOMER-001`, `DEC-034`): the words and the links, never a decision.
 *
 * The server decides everything about a customer -- whether a typed string is a phone, which
 * search it asks for, whether a record may be created (the owner's notice), who sees the number.
 * This module only turns its answers into what a counter reads: a number grouped the way it is
 * said aloud, a `tel:` and a Zalo link built from the number the server returned, and one sentence
 * per refusal code. Nothing here is stored: the number lives in the page that asked for it.
 *
 * @module core/customers
 */

/** `GET …/customers` answers at most this many, newest activity first (`SEARCH_LIMIT`). */
export const CUSTOMER_SEARCH_LIMIT = 20;

/**
 * A national number grouped as it is read aloud: `0905 123 456`, or a landline `0258 381 2345`.
 * Anything that is not the server's national form is returned unchanged.
 *
 * @param {string|null|undefined} national
 * @returns {string}
 */
export function formatPhone(national) {
  const digits = String(national || "");
  if (/^0\d{9}$/.test(digits)) return `${digits.slice(0, 4)} ${digits.slice(4, 7)} ${digits.slice(7)}`;
  if (/^0\d{10}$/.test(digits)) return `${digits.slice(0, 4)} ${digits.slice(4, 7)} ${digits.slice(7)}`;
  return digits;
}

/**
 * The masked form an auditor reads: the last four digits only.
 *
 * @param {string|null|undefined} last4
 * @returns {string}
 */
export function maskedPhone(last4) {
  return last4 ? `••• ${last4}` : "—";
}

/**
 * What a row or a page calls a customer: the name they gave, or their last four digits.
 *
 * @param {any} customer a `CustomerSummaryResponse` or `CustomerProfileResponse`
 * @returns {string}
 */
export function customerTitle(customer) {
  if (!customer) return "";
  if (customer.erased_at) return "Khách đã xoá thông tin";
  if (customer.display_name) return String(customer.display_name);
  return customer.phone_last4 ? `Khách số đuôi ${customer.phone_last4}` : "Khách";
}

/**
 * The number as the person may see it: grouped for the roles that call customers, masked else.
 *
 * @param {any} customer
 * @returns {string}
 */
export function phoneText(customer) {
  if (!customer) return "";
  return customer.phone ? formatPhone(customer.phone) : maskedPhone(customer.phone_last4);
}

/**
 * `tel:` in international form, from the national number the server returned; `null` when the
 * number is not visible to this role.
 *
 * @param {string|null|undefined} national
 * @returns {string|null}
 */
export function telHref(national) {
  const digits = String(national || "");
  return /^0\d{9,10}$/.test(digits) ? `tel:+84${digits.slice(1)}` : null;
}

/**
 * Zalo's public profile link for a number (`https://zalo.me/<số điện thoại>`); `null` when hidden.
 *
 * @param {string|null|undefined} national
 * @returns {string|null}
 */
export function zaloHref(national) {
  const digits = String(national || "");
  return /^0\d{9,10}$/.test(digits) ? `https://zalo.me/${digits}` : null;
}

/**
 * The sentence for a customer refusal code (`CustomerRefusal`), or "" for one this console does
 * not know -- then the caller shows the generic sentence and the code in the drawer.
 *
 * @type {Readonly<Record<string, string>>}
 */
const REFUSAL = {
  PRIVACY_NOTICE_UNPUBLISHED:
    "Chủ tiệm chưa công bố thông báo bảo mật nên chưa lưu được khách. Phát phiếu vãng lai; không có gì được ghi.",
  SERVICE_CONSENT_REQUIRED: "Cần đánh dấu khách đã nghe và đồng ý trước khi lưu.",
  PHONE_INVALID: "Số điện thoại chưa đúng. Nhập 10 số di động (09…, 03…) hoặc số bàn có mã vùng.",
  CUSTOMER_PHONE_EXISTS: "Số này đã có trong danh sách khách. Chọn khách đó bên dưới.",
  CUSTOMER_ERASED: "Thông tin khách này đã bị xoá; không sửa hay gắn được nữa.",
  DISPLAY_NAME_INVALID: "Tên gọi dài quá 80 ký tự.",
  NOTE_TOO_LONG: "Ghi chú dài quá 200 ký tự.",
  ADDRESS_TOO_LONG: "Địa chỉ dài quá 300 ký tự.",
  NOTHING_TO_CHANGE: "Không có gì thay đổi để lưu.",
  CUSTOMER_UNKNOWN: "Không tìm thấy khách này trong cửa hàng (có thể vừa bị xoá). Tìm lại.",
};

/**
 * @param {any} error an `ApiError`
 * @returns {string}
 */
export function customerRefusalText(error) {
  const codes = Array.isArray(error?.reasonCodes) ? error.reasonCodes : [];
  for (const code of codes) {
    if (REFUSAL[code]) return REFUSAL[code];
  }
  // A duplicate arrives as a 409 whose detail is an object; its code is in the raw detail.
  const raw = String(error?.detail || "");
  for (const code of Object.keys(REFUSAL)) {
    if (raw.includes(`"${code}"`)) return REFUSAL[code];
  }
  return "";
}

/**
 * The existing record a duplicate-phone refusal names, or "".
 *
 * @param {any} error
 * @returns {string}
 */
export function existingCustomerId(error) {
  if (error?.status !== 409) return "";
  try {
    const detail = JSON.parse(String(error.detail || ""));
    return detail?.reason_code === "CUSTOMER_PHONE_EXISTS" && typeof detail.customer_id === "string"
      ? detail.customer_id
      : "";
  } catch {
    return "";
  }
}

/**
 * What a search that searched nothing says, by the server's `mode`; "" for a real search.
 *
 * @param {string} mode
 * @returns {string}
 */
export function searchModeHint(mode) {
  if (mode === "PHONE_INCOMPLETE") return "Gõ đủ số điện thoại, hoặc đúng 4 số cuối.";
  if (mode === "PHONE_INVALID") return "Số này không phải số điện thoại Việt Nam.";
  if (mode === "TOO_SHORT") return "Gõ ít nhất 2 chữ của tên.";
  return "";
}
