/**
 * Công nợ in the counter's words (`PAYMENT-002`, `DEC-035`, the B2B half): the refusal sentences,
 * the account's status, and the month names a statement is read under.
 *
 * Nothing here decides anything. Every refusal is the server's code (`AccountRefusal` and the
 * counter's `PaymentRefusal`), shown as the sentence below or, for a code this console does not
 * know, as the generic sentence with the code in the drawer. No amount is computed: a month is
 * calendar arithmetic on `YYYY-MM`, never money.
 *
 * @module core/accounts
 */

import { businessDate, monthLabel, shiftMonth as shiftMonthOf } from "./format.js";

/**
 * The sentence for each account refusal code. Each says what to do at the counter now.
 *
 * @type {Readonly<Record<string, string>>}
 */
export const ACCOUNT_REFUSAL_VI = {
  ACCOUNT_TERMS_UNPUBLISHED:
    "Chủ tiệm chưa công bố điều khoản công nợ, nên chưa mở hay ghi công nợ được. Thu tiền tại quầy.",
  ACCOUNT_REQUIRES_BUSINESS: "Chỉ khách doanh nghiệp mới mở công nợ. Sửa loại khách trước.",
  ACCOUNT_ALREADY_OPEN: "Khách này đã có công nợ. Tải lại trang.",
  NOT_AN_ACCOUNT_CUSTOMER: "Khách của đơn này không có công nợ. Thu tiền tại quầy.",
  ACCOUNT_SUSPENDED: "Chủ tiệm đã ngưng công nợ của khách này. Thu tiền tại quầy.",
  ACCOUNT_LIMIT_UNSET:
    "Chủ tiệm chưa đặt hạn mức cho khách này, nên chưa ghi công nợ được. Thu tiền tại quầy.",
  ACCOUNT_LIMIT_INVALID: "Hạn mức là số nguyên đồng, ít nhất 1 ₫.",
  ACCOUNT_LIMIT_EXCEEDED:
    "Ghi đơn này sẽ vượt hạn mức. Thu tiền tại quầy, hoặc khách trả bớt công nợ trước.",
  ACCOUNT_OVERDUE:
    "Khách có kỳ sao kê quá hạn chưa trả đủ. Đơn mới trả tại quầy cho tới khi khách trả kỳ đó.",
  ACCOUNT_CHARGE_IS_THE_HANDOVER: "Ghi công nợ là lúc khách nhận đồ. Tải lại đơn rồi làm lại.",
  LIFT_EXPIRY_INVALID: "Ngày mở chặn từ hôm nay tới tối đa 31 ngày nữa.",
  LIFT_REASON_REQUIRED: "Ghi lý do mở chặn, từ 3 tới 200 ký tự.",
  MONTH_NOT_ENDED: "Tháng này chưa hết nên chưa chốt sao kê.",
  NOTHING_TO_CHANGE: "Không có gì thay đổi để lưu.",
  CUSTOMER_ERASED: "Thông tin khách này đã bị xoá; không mở công nợ được.",
  OVERPAYMENT_REFUSED:
    "Số tiền lớn hơn số khách đang nợ — trả lại tiền thừa cho khách. Không có gì được ghi.",
  NOTHING_OWED: "Khách không còn nợ gì. Tải lại trang.",
};

/**
 * One line under a control that is not offered, for the order page (≤ 25 words each). The code
 * stays in the drawer; these name the fact and what to do.
 *
 * @type {Readonly<Record<string, string>>}
 */
export const HANDOVER_WHY_NOT_VI = {
  ACCOUNT_TERMS_UNPUBLISHED: "Chưa ghi công nợ được: chủ tiệm chưa công bố điều khoản công nợ.",
  ACCOUNT_SUSPENDED: "Chưa ghi công nợ được: chủ tiệm đã ngưng công nợ của khách này.",
  ACCOUNT_LIMIT_UNSET: "Chưa ghi công nợ được: chủ tiệm chưa đặt hạn mức cho khách này.",
  ACCOUNT_LIMIT_EXCEEDED: "Chưa ghi công nợ được: đơn này làm vượt hạn mức.",
  ACCOUNT_OVERDUE: "Chưa ghi công nợ được: khách có kỳ quá hạn chưa trả. Thu tiền tại quầy.",
  GOODS_NOT_READY_FOR_HANDOVER: "Khách công nợ: khi đồ xong, giao đồ và ghi vào công nợ.",
  NO_PRESENTABLE_TOTAL: "Chưa ghi công nợ được: báo giá chưa có một tổng duy nhất.",
};

/** `AccountStatus`, as the account card's pill says it. */
export const ACCOUNT_STATUS_VI = {
  ACTIVE: "Đang dùng",
  SUSPENDED: "Đã ngưng",
};

/**
 * The first reason code of an account refusal, as its sentence, or "" when this console does not
 * know it (then the caller shows the generic sentence and the code in the drawer).
 *
 * @param {any} error an `ApiError`
 * @returns {string}
 */
export function accountRefusalText(error) {
  const codes = Array.isArray(error?.reasonCodes) ? error.reasonCodes : [];
  for (const code of codes) {
    if (Object.hasOwn(ACCOUNT_REFUSAL_VI, code)) return ACCOUNT_REFUSAL_VI[code];
  }
  return "";
}

/**
 * `YYYY-MM` as the counter says it: "Tháng 9/2026".
 *
 * @param {string|null|undefined} month
 * @returns {string}
 */
export function monthName(month) {
  return monthLabel(month);
}

/**
 * A `YYYY-MM` month moved by whole months (calendar arithmetic, not money).
 *
 * @param {string} month
 * @param {number} delta
 * @returns {string}
 */
export function shiftMonth(month, delta) {
  return shiftMonthOf(month, delta);
}

/**
 * The shop's current month, `YYYY-MM`, in `Asia/Ho_Chi_Minh` whatever the device's zone.
 *
 * @param {Date} [now]
 * @returns {string}
 */
export function currentMonth(now = new Date()) {
  return businessDate(now).slice(0, 7);
}

/**
 * The statement page for one customer and month, as a link (`#/…`); `.slice(1)` is the router's
 * path for `navigate`.
 *
 * @param {string} customerId
 * @param {string} month `YYYY-MM`
 * @returns {string}
 */
export function statementPath(customerId, month) {
  return `#/customers/${encodeURIComponent(customerId)}/statement/${encodeURIComponent(month)}`;
}
