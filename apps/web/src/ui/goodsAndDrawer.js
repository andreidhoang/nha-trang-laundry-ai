/**
 * GOODS-AND-DRAWER-009 on the order page: goods leave only when paid (review M2), and a refund says
 * how the money went back (review M4).
 *
 * - **Thu tiền trước khi giao.** The server now refuses every door the goods leave by -- "Cho đồ
 *   ra" (`RELEASE`) and the courier's trip (a `RETURN` leg, succeeded or failed) -- while an order
 *   is not paid in full or charged to the customer's account, and `next_steps` stops offering them.
 *   The money card of a delivery order that still owes money says so beside the payment action;
 *   a refusal that arrives anyway (a second phone paid, or refunded, under this screen) is worded
 *   the same way and offers "Thu tiền" instead of a reload.
 * - **Trả lại tiền bằng.** A cancellation that hands money back is refused unless the staff member
 *   says whether the money went back from the drawer (Tiền mặt) or by transfer (Chuyển khoản):
 *   the drawer figure on Hôm nay is cash in minus cash handed back. Nothing is preselected -- the
 *   person holding the money says which -- and the server's `requires` decides when it is asked.
 *
 * Wording only: which step is legal, and when a method is required, is the server's.
 *
 * @module ui/goodsAndDrawer
 */

import { h } from "../core/dom.js";
import { PAY_FIRST_CODES } from "../core/errors.js";
import { PAYMENT_METHOD_VI } from "../core/i18n.js";
import { choiceChips } from "./kit.js";

/**
 * The modes the shop's courier takes back to the customer (the server's `MODES_EXPECTING_RETURN`).
 * Used only to word the money card; which button is offered is `next_steps`.
 */
const DELIVERY_MODES = new Set(["PICKUP_AND_RETURN", "RETURN_ONLY"]);

/** Tier 1, beside the payment action (≤ 25 words). */
export const PAY_BEFORE_DELIVERY = "Thu tiền trước khi giao — người giao không thu tiền.";

/**
 * The money card's line for a running delivery order that still owes money, or null.
 *
 * @param {any} order an `OrderViewResponse`
 * @returns {string|null}
 */
export function payFirstCaption(order) {
  if (order?.commercial !== "ACTIVE") return null;
  if (!DELIVERY_MODES.has(String(order.fulfillment_mode))) return null;
  return ["UNPAID", "PARTIALLY_PAID"].includes(String(order.balance)) ? PAY_BEFORE_DELIVERY : null;
}

/**
 * Whether a refusal means "take the money first" (`RELEASE_REQUIRES_PAYMENT`,
 * `DELIVERY_REQUIRES_PAYMENT`).
 *
 * @param {any} error an `ApiError`
 * @returns {boolean}
 */
export function paysFirst(error) {
  const codes = Array.isArray(error?.reasonCodes) ? error.reasonCodes : [];
  return codes.some((code) => PAY_FIRST_CODES.has(String(code)));
}

/**
 * "Trả lại tiền cho khách bằng": the two methods, none chosen.
 *
 * @param {(method: string) => void} onChange
 * @returns {HTMLElement}
 */
export function refundMethodField(onChange) {
  return h(
    "div",
    { class: "stack stack--tight", dataRefundMethod: "true" },
    h("p", { class: "field-label" }, "Trả lại tiền cho khách bằng"),
    choiceChips({
      label: "Trả lại tiền cho khách bằng",
      name: "refund_method",
      options: [
        { value: "TIEN_MAT", label: PAYMENT_METHOD_VI.TIEN_MAT, title: "TIEN_MAT" },
        { value: "CHUYEN_KHOAN", label: PAYMENT_METHOD_VI.CHUYEN_KHOAN, title: "CHUYEN_KHOAN" },
      ],
      onChange,
    }),
    h(
      "p",
      { class: "hint" },
      "Đưa tiền từ két thì chọn Tiền mặt: tiền trong két hôm nay trừ đúng khoản này.",
    ),
  );
}
