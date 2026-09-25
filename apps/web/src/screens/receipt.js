/**
 * Phiếu cho khách: the receipt a walk-in takes home (`RECEIPT-PRINT-001`, counter completeness spec
 * §3.4, founder ruling R4).
 *
 * The ticket number used to be spoken and remembered. This screen puts it on paper — sized for an
 * 80 mm or 58 mm thermal roll and for A5 — or hands it to the phone's share sheet as plain text.
 *
 *   - **It prints only what the server holds.** The store's registered name, the ticket and its
 *     business day, each priced line with its quantity, the promotion or remedy credit applied, the
 *     total, when the order was taken, and a short reference. A store minted before the registry
 *     has no name, and then the receipt has no name line — it never invents one.
 *   - **No promised-ready time.** Which rule sets a per-order ready time is undecided, and a receipt
 *     that printed one would be a promise the software made on the shop's behalf. The receipt says
 *     "Tiệm sẽ báo khi đồ sẵn sàng" instead (R4) — and says something else once that sentence would
 *     be false: the laundry is already done, the order is closed, or it was cancelled.
 *   - **Every figure is a server integer through `money()`.** The lines print at their list amount
 *     and the adjustments (promotion, credit, delivery fee) under them, all read off the stored
 *     revision (`GET …/quotes/{id}?revision=`); the total is the order's own `payable_total_vnd`.
 *     Nothing here adds, subtracts or checks one against another.
 *   - **Deterministic first paint.** The order read paints the header, the total and the footer at
 *     once; the lines are the second paint. "In phiếu" stays off, with its reason, until they are
 *     in — a receipt without its lines is not one to hand over.
 *   - **No identifier on the paper.** The short reference is the first eight characters of the
 *     order's id, upper-cased; the full id stays on the order page's technical drawer.
 *   - **Read-only.** Nothing is written from here; printing and sharing never reach the server.
 *
 * @module screens/receipt
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import {
  UNKNOWN,
  UUID,
  dateOnly,
  dateTime,
  money,
  moneyRange,
  quantity as quantityText,
} from "../core/format.js";
import { QUOTE_ADJUSTMENT_VI } from "../core/i18n.js";
import { snapshot } from "../core/session.js";
import { errorNotice } from "../ui/components.js";
import { clock, serviceName, unitShort } from "../ui/quoting.js";
import { actionBar, button, infoButton, page, skeletonRows } from "../ui/kit.js";
// The same helper the order list and page use, so the paper cannot word the ticket differently.
import { orderName } from "./orders.js";

/** R4, verbatim: what the receipt says instead of a ready time. */
export const READY_NOTICE = "Tiệm sẽ báo khi đồ sẵn sàng.";

// --- the hand-off from ＋ Nhận đồ -------------------------------------------------------------

/** @type {string|null} the order `#/new` just created, until its page has offered the receipt */
let offered = null;

/**
 * `#/new` marks the order it just created, so the order page it lands on can offer "In phiếu cho
 * khách" once. In memory only (nothing is stored on a shared counter phone), and cleared the first
 * time it is read.
 *
 * @param {string} orderId
 */
export function offerReceipt(orderId) {
  offered = String(orderId);
}

/**
 * @param {string} orderId
 * @returns {boolean} whether `#/new` just created this order (consumed on read)
 */
export function takeReceiptOffer(orderId) {
  const hit = offered !== null && offered === String(orderId);
  if (hit) offered = null;
  return hit;
}

/**
 * The receipt route for an order. One place builds it, so both entry points agree.
 *
 * @param {string} orderId
 * @returns {string}
 */
export function receiptPath(orderId) {
  return `/orders/${encodeURIComponent(String(orderId))}/receipt`;
}

// --- what the paper says ---------------------------------------------------------------------

/**
 * The short reference printed on the paper: eight characters a person can read over the phone.
 *
 * @param {string} orderId
 * @returns {string}
 */
function shortReference(orderId) {
  return String(orderId || "").slice(0, 8).toUpperCase() || UNKNOWN;
}

/**
 * The closing line. R4's sentence while it is true; a plain fact once it no longer is. Wording
 * only — nothing here decides what may happen to the order.
 *
 * @param {any} order
 * @returns {string}
 */
function closingLine(order) {
  if (order.commercial === "CANCELLED") return "Đơn đã huỷ.";
  if (order.commercial === "COMPLETED") return "Đơn đã hoàn tất.";
  if (order.production === "READY_AT_STORE" || order.production === "RELEASED") {
    return "Đồ đã giặt xong.";
  }
  return READY_NOTICE;
}

/**
 * One adjustment row's amount: a credit takes money off, a debit adds it. The sign is a word
 * mark in front of a formatted server integer, never a computation.
 *
 * @param {any} item a `QuoteAdjustmentResponse`
 * @returns {string}
 */
function adjustmentAmount(item) {
  const text = moneyRange(item.amount_min_vnd, item.amount_max_vnd).text;
  return item.direction === "CREDIT" ? `−${text}` : `+${text}`;
}

/**
 * One priced line's amount: its list amount (before promotion and credit, which are printed as
 * their own rows), or its band when it is priced by inspection.
 *
 * @param {any} line a `QuoteLineResponse`
 * @returns {string}
 */
function lineAmount(line) {
  if (line.price_kind === "RANGE") {
    return moneyRange(line.band_minimum_vnd, line.band_maximum_vnd, "chưa có giá").text;
  }
  const listed = line.list_amount_vnd;
  return money(listed === undefined ? line.net_amount_vnd : listed, "chưa có giá");
}

/**
 * Everything the paper says, as plain strings: the DOM and the shared text are both drawn from
 * this, so the two can never disagree.
 *
 * @typedef {object} ReceiptModel
 * @property {string|null} store
 * @property {string} ticket
 * @property {string|null} day
 * @property {string} taken
 * @property {string} reference
 * @property {Array<{name: string, quantity: string, amount: string, code: string}>|null} lines
 * @property {Array<{label: string, amount: string, kind: string}>} adjustments
 * @property {string} total
 * @property {string} closing
 *
 * @param {any} order an `OrderViewResponse`
 * @param {any|null} detail the bound `QuoteRevisionDetailResponse`, once read
 * @param {any[]|null} catalog the published services, for their names
 * @returns {ReceiptModel}
 */
function receiptModel(order, detail, catalog) {
  const state = snapshot();
  const numbered = order.ticket_number !== null && order.ticket_number !== undefined;
  return {
    store: state.storeNames?.[String(order.store_id)] || null,
    ticket: orderName(order),
    // Numbers restart every morning, so the paper carries the ticket's business day beside it.
    day: numbered ? dateOnly(order.ticket_issued_on) : null,
    taken: dateTime(order.created_at),
    reference: shortReference(order.order_id),
    lines: detail
      ? (detail.lines || []).map((line) => ({
          name: serviceName(catalog, line.service_code),
          quantity: `${quantityText(line.quantity)} ${unitShort(line.unit)}`,
          amount: lineAmount(line),
          code: String(line.service_code),
        }))
      : null,
    adjustments: detail
      ? (detail.adjustments || []).map((item) => ({
          label: QUOTE_ADJUSTMENT_VI[item.kind] || String(item.kind),
          amount: adjustmentAmount(item),
          kind: String(item.kind),
        }))
      : [],
    total: money(order.payable_total_vnd, "Chưa có tổng"),
    closing: closingLine(order),
  };
}

/**
 * The receipt as plain text, for the share sheet.
 *
 * @param {ReceiptModel} model
 * @returns {string}
 */
export function receiptText(model) {
  return [
    model.store,
    [model.ticket, model.day].filter(Boolean).join(" · "),
    ...(model.lines || []).map((line) => `${line.name} — ${line.quantity}: ${line.amount}`),
    ...model.adjustments.map((item) => `${item.label}: ${item.amount}`),
    `Tổng cộng: ${model.total}`,
    `Nhận đơn: ${model.taken}`,
    `Mã đơn: ${model.reference}`,
    model.closing,
  ]
    .filter(Boolean)
    .join("\n");
}

/**
 * @param {string} label
 * @param {string} value
 * @param {Record<string, string>} [data]
 * @returns {HTMLElement}
 */
function row(label, value, data = {}) {
  const props = { class: "receipt-paper__row" };
  for (const [key, entry] of Object.entries(data)) {
    props[`data${key[0].toUpperCase()}${key.slice(1)}`] = entry;
  }
  return h(
    "div",
    props,
    h("span", { class: "receipt-paper__label" }, label),
    h("span", { class: "receipt-paper__value" }, value),
  );
}

/**
 * The paper's contents.
 *
 * @param {ReceiptModel} model
 * @param {unknown} linesFallback what stands where the lines go before they are read
 * @returns {Node[]}
 */
function paper(model, linesFallback) {
  return [
    h(
      "header",
      { class: "receipt-paper__head" },
      model.store ? h("p", { class: "receipt-paper__store", dataField: "store" }, model.store) : null,
      h("p", { class: "receipt-paper__ticket", dataField: "ticket" }, model.ticket),
      model.day ? h("p", { class: "receipt-paper__day" }, model.day) : null,
    ),
    model.lines
      ? model.lines.length
        ? h(
            "ul",
            { class: "receipt-paper__lines", "aria-label": "Các món" },
            model.lines.map((line) =>
              h(
                "li",
                { class: "receipt-paper__line", dataService: line.code },
                h(
                  "span",
                  { class: "receipt-paper__item" },
                  h("span", { class: "receipt-paper__name" }, line.name),
                  h("span", { class: "receipt-paper__qty" }, line.quantity),
                ),
                h("span", { class: "receipt-paper__value" }, line.amount),
              ),
            ),
          )
        : h("p", { class: "receipt-paper__note" }, "Bản giá này không có món nào đọc được.")
      : h("div", { class: "receipt-paper__lines-host" }, linesFallback),
    model.adjustments.length
      ? h(
          "div",
          { class: "receipt-paper__adjustments" },
          model.adjustments.map((item) => row(item.label, item.amount, { adjustment: item.kind })),
        )
      : null,
    h(
      "div",
      { class: "receipt-paper__total" },
      h("span", null, "Tổng cộng"),
      h("span", { class: "receipt-paper__total-amount", dataTotal: "true" }, model.total),
    ),
    h(
      "div",
      { class: "receipt-paper__meta" },
      row("Nhận đơn", model.taken, { field: "taken" }),
      row("Mã đơn", model.reference, { field: "reference" }),
    ),
    h("p", { class: "receipt-paper__closing", dataField: "closing" }, model.closing),
  ];
}

/**
 * @param {import("../core/router.js").RouteContext} context
 * @returns {HTMLElement}
 */
export function render_(context) {
  const orderId = String(context?.params?.orderId || "").trim();
  const wellFormed = UUID.test(orderId);
  const id = encodeURIComponent(orderId);
  const back = (label) => ({ href: `#/orders/${id}`, label });

  const info = infoButton(
    "Phiếu này in gì?",
    h(
      "p",
      null,
      "Phiếu in đúng những gì máy chủ đã lưu: tên cửa hàng, số phiếu, từng món với số lượng và " +
        "giá, khuyến mãi hoặc khoản giảm trừ đã áp, tổng khách trả, lúc nhận đơn và mã đơn.",
    ),
    h(
      "p",
      { class: "hint" },
      "Phiếu không ghi giờ hẹn trả đồ: tiệm chưa quyết định quy tắc hẹn giờ cho từng đơn, nên " +
        "phiếu ghi “Tiệm sẽ báo khi đồ sẵn sàng”.",
    ),
    h(
      "p",
      { class: "hint" },
      "Nút “Chia sẻ” chỉ có trên máy có mục chia sẻ (thường là điện thoại). Máy không có thì in " +
        "phiếu, hoặc cho khách xem màn hình này.",
    ),
  );
  const headHost = h("div", { class: "receipt-screen__head" }, page({ back: back("Đơn"), title: "Phiếu cho khách", info }));
  const paperNode = h("article", { class: "receipt-paper", id: "receipt-paper", "aria-label": "Phiếu cho khách" }, skeletonRows(4));
  const statusHost = h("div", { class: "receipt-screen__status" });
  const actionHost = h("div", { class: "receipt-screen__actions" });

  /** @type {any|null} */
  let order = null;
  /** @type {any|null} */
  let detail = null;
  /** @type {any[]|null} */
  let catalog = null;
  /** @type {unknown} */
  let linesError = null;
  /** @type {string} why the receipt cannot be printed yet, when it is not loading */
  let blocked = "";

  const printReason = h("p", { class: "hint", id: "receipt-print-reason" });
  const printButton = button({
    label: "In phiếu",
    icon: "printer",
    variant: "primary",
    id: "receipt-print",
    disabled: true,
    onClick: () => window.print(),
  });
  printButton.setAttribute("aria-describedby", "receipt-print-reason");
  const shareSupported = typeof navigator.share === "function";
  const shareButton = shareSupported
    ? button({
        label: "Chia sẻ",
        id: "receipt-share",
        disabled: true,
        onClick: () => void share(),
      })
    : null;

  function drawActions() {
    const ready = Boolean(order && detail);
    printButton.disabled = !ready;
    if (shareButton) shareButton.disabled = !ready;
    printReason.textContent = ready
      ? ""
      : blocked || (linesError ? "Chưa in được: chưa đọc được các món của đơn." : "Đang đọc đơn…");
    render(actionHost, actionBar(printReason.textContent ? printReason : null, printButton, shareButton));
  }

  async function share() {
    if (!order || !detail) return;
    const model = receiptModel(order, detail, catalog);
    try {
      await navigator.share({ title: `${model.ticket} · Phiếu cho khách`, text: receiptText(model) });
      render(statusHost);
    } catch (error) {
      // Closing the share sheet is a choice, not a failure.
      if (/** @type {any} */ (error)?.name === "AbortError") return;
      render(
        statusHost,
        h("p", { class: "hint", role: "status" }, "Máy này chưa chia sẻ được phiếu. Bấm “In phiếu” hoặc cho khách xem màn hình."),
      );
    }
  }

  function drawPaper() {
    if (!order) return;
    const model = receiptModel(order, detail, catalog);
    const fallback = linesError
      ? errorNotice(linesError, {
          title: "Chưa đọc được các món của đơn. Tổng ở dưới là số máy chủ đã trả.",
          // A read, so pressing again is always safe -- whatever `retryable` says about writes.
          actions: [rereadButton("Đọc lại các món", loadLines)],
        })
      : skeletonRows(2);
    render(paperNode, ...paper(model, fallback));
    drawActions();
  }

  async function loadLines() {
    if (!order) return;
    linesError = null;
    detail = null;
    drawPaper();
    const store = encodeURIComponent(String(order.store_id));
    const quote = encodeURIComponent(String(order.quote_id));
    const revision = encodeURIComponent(String(order.quote_revision));
    const [read, services] = await Promise.all([
      request(`/internal/v1/stores/${store}/quotes/${quote}?revision=${revision}`).catch((error) => {
        linesError = error;
        return null;
      }),
      catalog
        ? Promise.resolve(catalog)
        : request("/internal/v1/pricebook/services").catch(() => null),
    ]);
    catalog = Array.isArray(services) ? services : null;
    detail = read;
    drawPaper();
  }

  /**
   * @param {string} label
   * @param {() => Promise<void>} again
   */
  function rereadButton(label, again) {
    return button({ label, icon: "refresh", variant: "quiet", onClick: () => void again() });
  }

  async function start() {
    blocked = "";
    try {
      order = await request(`/internal/v1/orders/${id}`);
    } catch (error) {
      blocked = "Chưa in được: chưa đọc được đơn.";
      render(
        paperNode,
        /** @type {any} */ (error)?.status === 404
          ? h(
              "div",
              { class: "notice", dataState: "warn" },
              h("p", { class: "notice__title" }, "Không tìm thấy đơn này"),
              h("p", null, "Mã đơn sai, hoặc đơn thuộc cửa hàng bạn không làm."),
            )
          : errorNotice(error, { actions: [rereadButton("Đọc lại", start)] }),
      );
      drawActions();
      return;
    }
    render(headHost, page({ back: back(orderName(order)), title: "Phiếu cho khách", info }));
    render(statusHost, h("p", { class: "hint", dataField: "read-at" }, `Đọc lúc ${clock(new Date().toISOString())}`));
    await loadLines();
  }

  if (wellFormed) {
    void start();
  } else {
    blocked = "Chưa in được: địa chỉ không chứa mã đơn.";
    render(
      paperNode,
      h(
        "div",
        { class: "notice", dataState: "danger" },
        h("p", { class: "notice__title" }, "Địa chỉ này không chứa một mã đơn hợp lệ"),
        h("p", null, "Không có yêu cầu nào được gửi đi."),
      ),
    );
  }
  drawActions();

  return h("section", { class: "screen receipt-screen" }, headHost, paperNode, statusHost, actionHost);
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/orders/:orderId/receipt",
  title: "Phiếu cho khách",
  // The lines are the quote read's, which the operations gate serves; an AUDITOR reads the order
  // but not its quote, and is told so by the guard rather than handed half a receipt.
  capability: "QUOTES_READ",
  needsStore: true,
  render: render_,
};
