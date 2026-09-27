/**
 * Sao kê công nợ: one account customer's statement for one calendar month, printable
 * (`PAYMENT-002`, `DEC-035`, the B2B half).
 *
 *   - **The figures are the server's.** Opening balance, the month's charges and payments with
 *     their counts, the closing balance and the due date (the 15th of the next month) are
 *     PostgreSQL's (`GET …/customers/{id}/account/statements/{YYYY-MM}`); this screen formats and
 *     prints them. Nothing here adds a line up or checks one figure against another.
 *   - **Frozen or live.** A month the owner closed (`scripts/close_account_statements.py`) says so,
 *     with the day; a month still open says the figures are as of now.
 *   - **The paper** follows the receipt's print rules (`RECEIPT-PRINT-001`): only the statement
 *     prints, black on white, sized by the paper the print dialog chose (A5 or a roll).
 *   - **Bounded.** Each list is at most 200 lines, and says so when it stopped; the totals are over
 *     every row.
 *
 * Read-only: nothing is written from here.
 *
 * @module screens/accountStatement
 */

import { request } from "../core/api.js";
import { currentMonth, monthName, shiftMonth, statementPath } from "../core/accounts.js";
import { h, render } from "../core/dom.js";
import { UUID, calendarDay, dateOnly, dateTime, money } from "../core/format.js";
import { PAYMENT_METHOD_VI } from "../core/i18n.js";
import { navigate } from "../core/router.js";
import { snapshot, storeId } from "../core/session.js";
import { errorNotice } from "../ui/components.js";
import { actionBar, button, infoButton, page, skeletonRows, techDetails } from "../ui/kit.js";

/**
 * @param {string|null|undefined} day `YYYY-MM-DD`
 * @returns {string}
 */
function dayText(day) {
  return day ? `${calendarDay(day, { weekday: false })}/${String(day).slice(0, 4)}` : "—";
}

/**
 * @param {string} label
 * @param {string} value
 * @param {string} field
 * @param {boolean} [strong]
 */
function row(label, value, field, strong = false) {
  return h(
    "div",
    { class: ["statement-paper__row", strong && "statement-paper__row--strong"], dataField: field },
    h("span", null, label),
    h("span", { class: "statement-paper__value" }, value),
  );
}

/**
 * @param {import("../core/router.js").RouteContext} context
 * @returns {HTMLElement}
 */
export function render_(context) {
  const customerId = String(context?.params?.customerId || "").trim();
  const month = String(context?.params?.month || "").trim();
  const store = storeId();
  const wellFormed = UUID.test(customerId) && /^\d{4}-\d{2}$/.test(month);
  const back = { href: `#/customers/${encodeURIComponent(customerId)}`, label: "Khách hàng" };
  const info = infoButton(
    "Sao kê này tính thế nào?",
    h(
      "p",
      null,
      "Đầu kỳ là số khách còn nợ lúc bắt đầu tháng. Phát sinh là các đơn giao ghi công nợ trong " +
        "tháng; đã trả là các lần khách trả công nợ. Cuối kỳ là số còn nợ cuối tháng. Hạn thanh " +
        "toán là ngày 15 tháng sau (DEC-035). Tháng tính theo giờ Việt Nam.",
    ),
    h(
      "p",
      { class: "hint" },
      "Khi chủ tiệm chốt tháng, số liệu được lưu lại đúng như lúc chốt. Tháng chưa chốt thì số " +
        "liệu tính tới lúc đọc.",
    ),
  );
  const headHost = h("div", { class: "statement-screen__head" }, page({ back, title: "Sao kê công nợ", info }));
  const paperNode = h(
    "article",
    { class: "statement-paper", id: "statement-paper", "aria-label": "Sao kê công nợ" },
    skeletonRows(4),
  );
  const navHost = h("div", { class: "statement-screen__months" });
  const techHost = h("div", { class: "statement-screen__tech" });
  const printButton = button({
    label: "In sao kê",
    icon: "printer",
    variant: "primary",
    id: "statement-print",
    disabled: true,
    onClick: () => window.print(),
  });

  /** @param {any} read */
  function paper(read) {
    const figures = read.statement;
    // The statement read names no store; the page reads it for the selected one.
    const storeName = snapshot().storeNames?.[String(store)] || null;
    const frozenLine = read.frozen
      ? `Đã chốt ngày ${dateOnly(read.frozen.frozen_at)}.`
      : read.month_ended
        ? "Tháng đã hết, chưa chốt — số liệu tính tới lúc đọc."
        : "Tháng chưa hết — số liệu tính tới lúc đọc.";
    const charges = read.charges || [];
    const payments = read.payments || [];
    return [
      h(
        "header",
        { class: "statement-paper__head" },
        storeName ? h("p", { class: "statement-paper__store" }, storeName) : null,
        h("p", { class: "statement-paper__title" }, `Sao kê công nợ · ${monthName(figures.month)}`),
        h("p", { class: "statement-paper__customer", dataField: "customer" }, read.customer_name || "Khách đã xoá thông tin"),
      ),
      h(
        "div",
        { class: "statement-paper__sums" },
        row("Đầu kỳ", money(figures.opening_vnd), "opening"),
        row(`Phát sinh · ${figures.charge_count} đơn`, money(figures.charges_vnd), "charges"),
        row(`Đã trả · ${figures.payment_count} lần`, money(figures.payments_vnd), "payments"),
        row("Cuối kỳ (còn nợ)", money(figures.closing_vnd), "closing", true),
        row("Hạn thanh toán", dayText(figures.due_on), "due"),
      ),
      h(
        "section",
        { class: "statement-paper__lines", "aria-label": "Đơn ghi công nợ" },
        h("p", { class: "statement-paper__section" }, "Đơn ghi công nợ"),
        charges.length
          ? charges.map((item) =>
              row(
                `${dateOnly(item.charged_at)} · ${
                  Number.isInteger(item.ticket_number)
                    ? `Phiếu ${item.ticket_number}`
                    : `Đơn ${String(item.order_id).slice(0, 8).toUpperCase()}`
                }`,
                money(item.amount_vnd),
                "charge",
              ),
            )
          : h("p", { class: "statement-paper__none" }, "Không có đơn nào trong tháng."),
        read.charges_truncated
          ? h("p", { class: "statement-paper__none" }, `Chỉ in ${charges.length} dòng đầu; tổng vẫn tính đủ.`)
          : null,
      ),
      h(
        "section",
        { class: "statement-paper__lines", "aria-label": "Khách đã trả" },
        h("p", { class: "statement-paper__section" }, "Khách đã trả"),
        payments.length
          ? payments.map((item) =>
              row(
                `${dateTime(item.recorded_at)} · ${PAYMENT_METHOD_VI[item.method] || item.method}`,
                money(item.amount_vnd),
                "payment",
              ),
            )
          : h("p", { class: "statement-paper__none" }, "Chưa có lần trả nào trong tháng."),
        read.payments_truncated
          ? h("p", { class: "statement-paper__none" }, `Chỉ in ${payments.length} dòng đầu; tổng vẫn tính đủ.`)
          : null,
      ),
      h("p", { class: "statement-paper__closing", dataField: "frozen" }, frozenLine),
    ];
  }

  function monthNav() {
    const previous = shiftMonth(month, -1);
    const next = shiftMonth(month, 1);
    const latest = currentMonth();
    return h(
      "div",
      { class: "statement-screen__month-nav" },
      button({
        label: monthName(previous),
        icon: "chevron-left",
        variant: "quiet",
        id: "statement-previous",
        onClick: () => navigate(statementPath(customerId, previous).slice(1)),
      }),
      next <= latest
        ? button({
            label: monthName(next),
            variant: "quiet",
            id: "statement-next",
            onClick: () => navigate(statementPath(customerId, next).slice(1)),
          })
        : null,
    );
  }

  async function start() {
    try {
      const read = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/customers/${encodeURIComponent(customerId)}/account/statements/${encodeURIComponent(month)}`,
      );
      render(
        headHost,
        page({
          back: { ...back, label: read.customer_name || "Khách hàng" },
          title: "Sao kê công nợ",
          subtitle: monthName(month),
          info,
        }),
      );
      render(paperNode, ...paper(read));
      printButton.disabled = false;
      render(
        techHost,
        techDetails([
          ["Phiên bản truy vấn", String(read.query_version)],
          ["Mã công nợ", String(read.account_id), { copy: String(read.account_id) }],
          read.frozen ? ["Bản đã chốt", String(read.frozen.query_version)] : null,
        ].filter(Boolean)),
      );
    } catch (error) {
      render(
        paperNode,
        /** @type {any} */ (error)?.status === 404
          ? h(
              "div",
              { class: "notice", dataState: "warn" },
              h("p", { class: "notice__title" }, "Khách này chưa có công nợ"),
              h("p", null, "Sao kê chỉ có khi chủ tiệm đã mở công nợ cho khách."),
            )
          : errorNotice(error, { onRetry: () => void start() }),
      );
    }
  }

  if (wellFormed) {
    render(navHost, monthNav());
    void start();
  } else {
    render(
      paperNode,
      h(
        "div",
        { class: "notice", dataState: "danger" },
        h("p", { class: "notice__title" }, "Địa chỉ này không chứa mã khách và tháng hợp lệ"),
        h("p", null, "Không có yêu cầu nào được gửi đi."),
      ),
    );
  }

  return h(
    "section",
    { class: "screen statement-screen" },
    headHost,
    navHost,
    paperNode,
    techHost,
    actionBar(printButton),
  );
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/customers/:customerId/statement/:month",
  title: "Sao kê công nợ",
  capability: "CUSTOMERS_READ",
  needsStore: true,
  render: render_,
};
