/**
 * VietQR on the counter, the receipt and the account statement (`VIETQR-001`, `DEC-041`).
 *
 *   - **The QR is the server's.** `GET /orders/{id}/vietqr` (and the account month's route) returns
 *     the payload, the amount and the transfer code the domain decided, and the symbol as rows of
 *     0/1 modules drawn by the server (error correction M, quiet zone included). This module only
 *     draws those squares — one SVG path built from the matrix, never markup the server wrote — and
 *     prints the amount and the code in large type beside it.
 *   - **The amount is what the payment ledger says remains**, read by the server in the same request
 *     that built the QR. Nothing here adds, subtracts or compares money.
 *   - **Unpublished means no QR.** Until the owner publishes the shop's account every read answers
 *     `BANK_ACCOUNT_UNPUBLISHED`; the counter shows one short line and a tier-2 note naming the
 *     owner's switch, and everything else works as before.
 *
 * @module ui/vietqr
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { money } from "../core/format.js";
import { button, infoButton, skeletonRows } from "./kit.js";

/** The line under the QR at the counter: what to check before recording the money (`DEC-041`). */
export const CHECK_BANK_APP_LINE =
  "Kiểm tra app ngân hàng: đúng nội dung và số tiền rồi mới bấm Ghi nhận đã thu.";

/**
 * The QR route for an order.
 *
 * @param {string} orderId
 * @returns {string}
 */
export function orderQrPath(orderId) {
  return `/internal/v1/orders/${encodeURIComponent(String(orderId))}/vietqr`;
}

/**
 * The QR route for an account customer's month.
 *
 * @param {string} store
 * @param {string} customerId
 * @param {string} month `YYYY-MM`
 * @returns {string}
 */
export function accountMonthQrPath(store, customerId, month) {
  const [s, c, m] = [store, customerId, month].map((part) => encodeURIComponent(String(part)));
  return `/internal/v1/stores/${s}/customers/${c}/account/statements/${m}/vietqr`;
}

/**
 * Read a QR; `null` when the read itself failed (the caller shows no QR, never a guessed one).
 *
 * @param {string} path
 * @returns {Promise<any|null>}
 */
export async function readQr(path) {
  try {
    return await request(path);
  } catch {
    return null;
  }
}

/**
 * The symbol: one SVG path, a unit square per dark module, on a white square. The viewBox is the
 * matrix itself, so it scales to any size crisply (screen, thermal head, A5) without resampling.
 *
 * @param {number[][]} modules rows of 0/1, square, quiet zone included
 * @param {string} label what a screen reader says
 * @returns {SVGElement}
 */
export function qrSymbol(modules, label) {
  const size = modules.length;
  const parts = [];
  modules.forEach((row, y) => {
    let x = 0;
    while (x < row.length) {
      if (row[x] !== 1) {
        x += 1;
        continue;
      }
      let run = 1;
      while (x + run < row.length && row[x + run] === 1) run += 1;
      parts.push(`M${x} ${y}h${run}v1h-${run}z`);
      x += run;
    }
  });
  return h(
    "svg",
    {
      class: "vietqr__symbol",
      viewBox: `0 0 ${size} ${size}`,
      role: "img",
      "aria-label": label,
      "shape-rendering": "crispEdges",
      dataModules: String(size),
    },
    h("rect", { width: String(size), height: String(size), fill: "#fff" }),
    h("path", { d: parts.join(""), fill: "#000" }),
  );
}

/**
 * Why there is no QR, in words; `null` for a read that has one.
 *
 * @param {any} read a `…VietQrResponse`
 * @returns {string|null}
 */
function refusalLine(read) {
  switch (read?.refusal) {
    case null:
    case undefined:
      return null;
    case "BANK_ACCOUNT_UNPUBLISHED":
      return "Chưa có mã QR chuyển khoản.";
    case "NOTHING_OWED":
      return "Không còn tiền phải trả — không cần mã QR.";
    case "MONTH_NOT_STARTED":
      return "Tháng này chưa bắt đầu.";
    default:
      return "Đơn này chưa thu tiền được, nên chưa có mã QR.";
  }
}

/** Tier 2: the owner's switch, and what the counter does meanwhile. */
function unpublishedInfo() {
  return infoButton(
    "Sao chưa có mã QR?",
    h(
      "p",
      null,
      "Chủ tiệm chưa công bố tài khoản nhận chuyển khoản, nên chưa in được mã QR có sẵn số tiền và " +
        "nội dung. Chủ tiệm bật bằng lệnh publish_bank_account: in thử mã 1.000 ₫, tự chuyển thử " +
        "từ điện thoại của mình, thấy tiền về rồi mới công bố (DEC-041).",
    ),
    h(
      "p",
      { class: "hint" },
      "Trong lúc chờ, quầy vẫn nhận chuyển khoản như trước: xem tiền vào trong app ngân hàng rồi ghi " +
        "nhận.",
    ),
  );
}

/**
 * The QR with the amount and the code, sized for the counter (`size: "counter"`, ≥ 240 px on a
 * desk) or for paper (`size: "paper"`).
 *
 * @param {any} read a `…VietQrResponse` with `refusal` null
 * @param {{size?: "counter"|"paper", amountLabel?: string, note?: string|null, check?: boolean}}
 *   [options] `check` puts the check-the-bank-app line beside the QR (the counter's sheet)
 * @returns {HTMLElement}
 */
export function qrCard(read, options = {}) {
  const size = options.size || "counter";
  return h(
    "div",
    { class: ["vietqr", `vietqr--${size}`], dataVietqr: "ready", dataTransferCode: read.transfer_code },
    qrSymbol(read.modules, `Mã QR chuyển khoản ${money(read.amount_vnd)}, nội dung ${read.transfer_code}`),
    h(
      "div",
      { class: "vietqr__facts" },
      h("p", { class: "vietqr__label" }, options.amountLabel || "Số tiền"),
      h("p", { class: "vietqr__amount money", dataField: "qr-amount" }, money(read.amount_vnd)),
      h("p", { class: "vietqr__label" }, "Nội dung"),
      h("p", { class: "vietqr__code", dataField: "qr-code" }, read.transfer_code),
      h(
        "p",
        { class: "vietqr__account" },
        [read.account_name, read.bank_display_name].filter(Boolean).join(" · "),
      ),
      options.note ? h("p", { class: "vietqr__note" }, options.note) : null,
      options.check
        ? h("p", { class: "vietqr__check", dataField: "qr-check" }, CHECK_BANK_APP_LINE)
        : null,
    ),
  );
}

/**
 * What stands where the QR goes when there is none: one short line, and for an unpublished account
 * the tier-2 note. `null` when there is nothing worth saying.
 *
 * @param {any|null} read
 * @returns {HTMLElement|null}
 */
export function qrAbsent(read) {
  const line = refusalLine(read);
  if (!line) return null;
  return h(
    "p",
    { class: "vietqr__absent hint", dataVietqr: String(read.refusal) },
    line,
    read.refusal === "BANK_ACCOUNT_UNPUBLISHED" ? unpublishedInfo() : null,
  );
}

/**
 * The QR inside *Thu tiền* when the method is *Chuyển khoản*: read once when first shown, then
 * the QR, the amount and the code in large type, and the check-the-bank-app line.
 *
 * @param {string} orderId
 * @returns {{node: HTMLElement, show: () => void}}
 */
export function paymentQr(orderId) {
  const node = h("div", { class: "vietqr-host", id: "payment-qr" });
  let started = false;

  async function load() {
    render(node, skeletonRows(2));
    const read = await readQr(orderQrPath(orderId));
    // A read with neither a refusal nor a matrix is not a QR: said as a failed read, never drawn.
    if (read === null || (!read.refusal && !Array.isArray(read.modules))) {
      render(
        node,
        h(
          "p",
          { class: "vietqr__absent hint", dataVietqr: "error" },
          "Chưa đọc được mã QR. ",
          button({ label: "Đọc lại", variant: "quiet", icon: "refresh", onClick: () => void load() }),
        ),
      );
      return;
    }
    if (read.refusal) {
      render(node, qrAbsent(read));
      return;
    }
    render(node, qrCard(read, { check: true }));
  }

  return {
    node,
    show() {
      if (started) return;
      started = true;
      void load();
    },
  };
}
