/**
 * MONEY-LIFECYCLE-009 (`DEC-045`, `DEC-046`): what a cancellation without charge does to the remedy
 * credits on an order -- stated before the press on the refund sheet, and after it on the order
 * page and the receipt.
 *
 * Every figure and every sentence is the server's (`OrderViewResponse.cancellation_money`): an
 * unspent credit from the order is voided, one already spent elsewhere is netted from the refund
 * (never below 0), a credit the bill spent is issued again. This module only lays them out; it
 * computes no money and decides nothing.
 */

import { h } from "../core/dom.js";
import { money } from "../core/format.js";

/**
 * The server's plan (`PREVIEW`) or record (`DONE`) for this order, or null.
 *
 * @param {any} order an `OrderViewResponse`
 * @param {"PREVIEW"|"DONE"} stage
 * @returns {any|null}
 */
export function cancellationMoney(order, stage) {
  const plan = order?.cancellation_money;
  if (!plan || plan.stage !== stage) return null;
  return Array.isArray(plan.lines_vi) && plan.lines_vi.length ? plan : null;
}

/**
 * The block the refund sheet (before) and the order page (after) print.
 *
 * @param {any} order an `OrderViewResponse`
 * @param {"PREVIEW"|"DONE"} stage
 * @returns {HTMLElement|null}
 */
export function cancellationMoneyBlock(order, stage) {
  const plan = cancellationMoney(order, stage);
  if (!plan) return null;
  const refunding = Number(plan.refundable_vnd) > 0;
  return h(
    "div",
    { class: "stack stack--tight remedy-money", dataCancellationMoney: stage },
    h(
      "p",
      { class: "field-label" },
      stage === "PREVIEW"
        ? "Huỷ không thu tiền thì các khoản giảm trừ, bồi thường của đơn này:"
        : "Khoản giảm trừ, bồi thường khi huỷ đơn",
    ),
    h(
      "ul",
      { class: "remedy-money__lines" },
      plan.lines_vi.map((line) => h("li", null, String(line))),
    ),
    refunding && Number(plan.netted_vnd) > 0
      ? h(
          "p",
          { class: "remedy-money__refund", dataRefund: String(plan.refund_vnd) },
          `${stage === "PREVIEW" ? "Tiền hoàn cho khách" : "Đã hoàn cho khách"}: ` +
            `${money(plan.refund_vnd)} (đã trừ ${money(plan.netted_vnd)})`,
        )
      : null,
  );
}

/**
 * The receipt's rows: what went back and what was netted, and each credit sentence.
 *
 * @param {any} order an `OrderViewResponse`
 * @returns {{refund: string|null, netted: string|null, lines: string[]}|null}
 */
export function receiptCancellationMoney(order) {
  const plan = cancellationMoney(order, "DONE");
  if (!plan) return null;
  const netted = Number(plan.netted_vnd) > 0;
  return {
    refund: Number(plan.refundable_vnd) > 0 ? money(plan.refund_vnd) : null,
    netted: netted ? money(plan.netted_vnd) : null,
    lines: plan.lines_vi.map(String),
  };
}
