/**
 * Hóa đơn cần xuất: every invoice request of the store, by state (`EINVOICE-REQUEST-001`,
 * `DEC-040`).
 *
 * The bookkeeper's queue. *Cần xuất* lists the open requests oldest first, each with the buyer, what
 * it is for and the amount the shop charged — every figure the server's, read now. The one primary
 * action is *Tải danh sách cho kế toán*: the open list as a spreadsheet (UTF-8 CSV), which the
 * bookkeeper takes to the e-invoice provider's portal. Once an invoice is issued there, *Ghi số hóa
 * đơn* records its symbol, number and date and the request moves to *Đã xuất*; a request nobody
 * needs any more is cancelled with a reason and moves to *Đã huỷ*.
 *
 * On a desk the list is a table; on a phone each row stands as a card (`kit.css`, the invoices
 * block). Nothing here issues an invoice, names a tax or computes an amount.
 *
 * @module screens/invoices
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { dateOnly } from "../core/format.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { errorNotice, gated } from "../ui/components.js";
import {
  actionBar,
  button,
  emptyState,
  infoButton,
  page,
  segmented,
  show,
  skeletonRows,
  techDetails,
  toast,
} from "../ui/kit.js";
import {
  CLOSE_SHORT,
  INVOICE_CANCEL_REASON_VI,
  INVOICE_HOW,
  amountText,
  cancelSheet,
  issuedSheet,
  issuedText,
  roleVerdict,
  statusOf,
  subjectHref,
  subjectText,
} from "../ui/invoice.js";

/** One page of a tab; the server says when there are more (`truncated`). */
const LIMIT = 100;

const TABS = [
  { value: "REQUESTED", label: "Cần xuất" },
  { value: "ISSUED", label: "Đã xuất" },
  { value: "CANCELLED", label: "Đã huỷ" },
];

/**
 * Hand the file to the browser, once, without keeping it anywhere: an object URL over an in-memory
 * blob, revoked right after the click (the export screen's rule).
 *
 * @param {any} produced an `InvoiceExportResponse`
 */
function download(produced) {
  const blob = new Blob([produced.content_csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = h("a", { href: url, download: String(produced.filename || "yeu-cau-hoa-don.csv") });
  anchor.click();
  URL.revokeObjectURL(url);
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const who = principal();
  const readVerdict = can(who, "INVOICES_READ");
  const writeVerdict = can(who, "INVOICES_WRITE");
  const closeVerdict = can(who, "INVOICES_CLOSE");
  let tab = "REQUESTED";
  /** @type {any|null} */
  let last = null;
  const tabsHost = h("div", { class: "invoices__tabs" });
  const listHost = h("div", { class: "stack stack--tight" }, skeletonRows(4));
  const sheetsHost = h("div");
  const exportHost = h("div", { class: "invoices__export-result" });
  const techHost = h("div");
  const subtitle = h("span", { dataField: "invoices-count" });
  const exportSubmission = new Submission("invoice-export");

  const tabs = segmented({
    label: "Trạng thái yêu cầu",
    id: "invoices-tabs",
    options: TABS,
    value: tab,
    onChange: (value) => {
      tab = value;
      void load();
    },
  });

  async function load() {
    render(listHost, skeletonRows(4));
    try {
      const payload = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/invoice-requests?status=${encodeURIComponent(tab)}&limit=${LIMIT}`,
      );
      last = payload;
      draw(payload);
    } catch (error) {
      show(listHost, errorNotice(/** @type {any} */ (error), { onRetry: () => void load() }));
    }
  }

  /** @param {any} payload */
  function drawTabs(payload) {
    const counts = payload?.counts || {};
    const next = segmented({
      label: "Trạng thái yêu cầu",
      id: "invoices-tabs",
      options: TABS.map((item) => ({ ...item, count: String(counts[item.value] ?? 0) })),
      value: tab,
      onChange: (value) => {
        tab = value;
        void load();
      },
    });
    render(tabsHost, next);
  }

  /** @param {any} payload */
  function draw(payload) {
    const items = Array.isArray(payload?.requests) ? payload.requests : [];
    const open = payload?.counts?.REQUESTED ?? 0;
    subtitle.textContent = open ? `${open} yêu cầu chờ kế toán xuất` : "Không có yêu cầu nào chờ xuất";
    drawTabs(payload);
    exportButton.disabled = !open || !closeVerdict.allowed;
    render(
      techHost,
      techDetails([["Phiên bản truy vấn", String(payload?.query_version || "")]]),
    );
    if (!items.length) {
      render(
        listHost,
        emptyState({
          icon: "quote",
          title:
            tab === "REQUESTED"
              ? "Không có yêu cầu nào chờ xuất"
              : tab === "ISSUED"
                ? "Chưa ghi hóa đơn nào đã xuất"
                : "Không có yêu cầu nào đã huỷ",
          body:
            tab === "REQUESTED"
              ? "Khi khách cần hóa đơn, bấm “Khách cần hóa đơn” trên trang của đơn hoặc của khách công nợ."
              : null,
        }),
      );
      return;
    }
    render(
      listHost,
      table(items),
      payload.truncated
        ? h(
            "p",
            { class: "hint", dataTruncated: "true" },
            `Chỉ hiện ${items.length} yêu cầu trong ${payload.total_count}.`,
          )
        : null,
    );
  }

  /** @param {any[]} items */
  function table(items) {
    const last = tab === "REQUESTED" ? "" : tab === "ISSUED" ? "Hóa đơn" : "Lý do huỷ";
    return h(
      "div",
      { class: "invoices__table-wrap surface" },
      h(
        "table",
        { class: "invoices__table", id: "invoices-table", "aria-label": "Yêu cầu hóa đơn" },
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            h("th", { scope: "col" }, "Mã"),
            h("th", { scope: "col" }, "Ngày ghi"),
            h("th", { scope: "col" }, "Đơn vị"),
            h("th", { scope: "col" }, "Cho"),
            h("th", { scope: "col", class: "invoices__num" }, "Số tiền"),
            h("th", { scope: "col" }, last),
          ),
        ),
        h("tbody", null, items.map((item) => row(item))),
      ),
    );
  }

  /** @param {any} item */
  function row(item) {
    const href = subjectHref(item);
    const buyer = item.buyer || {};
    // Each cell's value is one element, so on a phone (a card, label beside value) the lines under
    // a value stay under it.
    return h(
      "tr",
      { dataInvoice: String(item.invoice_request_id), dataStatus: String(item.status) },
      h("td", { dataLabel: "Mã", class: "invoices__code" }, h("span", null, item.request_code)),
      h(
        "td",
        { dataLabel: "Ngày ghi", class: "invoices__nowrap" },
        h("span", null, dateOnly(item.requested_at)),
      ),
      h(
        "td",
        { dataLabel: "Đơn vị", class: "invoices__buyer" },
        h(
          "div",
          null,
          h(
            "span",
            { class: "invoices__unit" },
            buyer.unit_name || (buyer.erased ? "Khách đã xoá thông tin" : "—"),
          ),
          buyer.tax_code ? h("span", { class: "invoices__sub" }, `MST ${buyer.tax_code}`) : null,
          buyer.email ? h("span", { class: "invoices__sub" }, buyer.email) : null,
        ),
      ),
      h(
        "td",
        { dataLabel: "Cho", class: "invoices__nowrap" },
        h(
          "div",
          null,
          href ? h("a", { href }, subjectText(item)) : subjectText(item),
          item.subject_kind === "ACCOUNT_MONTH" && item.amount?.month_ended === false
            ? h("span", { class: "invoices__sub" }, "Tháng chưa hết")
            : null,
        ),
      ),
      h(
        "td",
        { dataLabel: "Số tiền", class: "invoices__num" },
        h(
          "div",
          null,
          h("span", { class: "money" }, amountText(item.amount)),
          item.amount?.storage_fee_vnd
            ? h("span", { class: "invoices__sub" }, "gồm phí lưu kho")
            : null,
        ),
      ),
      h("td", { dataLabel: "", class: "invoices__last" }, lastCell(item)),
    );
  }

  /** @param {any} item */
  function lastCell(item) {
    if (item.status === "ISSUED") {
      return h(
        "span",
        { class: "invoices__issued" },
        statusOf(item),
        h("span", null, issuedText(item)),
      );
    }
    if (item.status === "CANCELLED") {
      return h(
        "div",
        null,
        h(
          "span",
          null,
          INVOICE_CANCEL_REASON_VI[item.cancel_reason] || String(item.cancel_reason || ""),
        ),
        item.cancel_note ? h("span", { class: "invoices__sub" }, item.cancel_note) : null,
      );
    }
    const mount = (made) => {
      render(sheetsHost, made.node);
      made.open();
    };
    return h(
      "div",
      { class: "invoices__actions" },
      gated(
        button({
          label: "Ghi số hóa đơn",
          variant: "secondary",
          network: true,
          data: { recordIssued: String(item.invoice_request_id) },
          onClick: () => mount(issuedSheet({ store, item, onDone: () => void load() })),
        }),
        roleVerdict(closeVerdict, CLOSE_SHORT),
      ),
      gated(
        button({
          label: "Huỷ",
          variant: "quiet",
          network: true,
          data: { cancelInvoice: String(item.invoice_request_id) },
          onClick: () => mount(cancelSheet({ store, item, onDone: () => void load() })),
        }),
        writeVerdict,
      ),
    );
  }

  const exportButton = button({
    label: "Tải danh sách cho kế toán",
    icon: "download",
    variant: "primary",
    network: true,
    id: "invoices-download",
    onClick: () => void exportOpen(),
  });

  async function exportOpen() {
    render(exportHost);
    exportButton.disabled = true;
    try {
      // One key per press: the server keeps no copy of the file, so a second press is a second
      // download, recorded as one.
      const produced = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/invoice-requests/export`,
        { method: "POST", idempotencyKey: exportSubmission.key() },
      );
      exportSubmission.reset();
      download(produced);
      toast(`Đã tải ${produced.request_count} yêu cầu`);
      render(
        exportHost,
        h(
          "p",
          { class: "hint", dataField: "invoices-exported" },
          `Đã tải ${produced.request_count} yêu cầu · ${produced.filename}`,
          produced.truncated ? " · chưa hết danh sách, tải tiếp sau khi ghi số" : "",
        ),
      );
    } catch (error) {
      exportSubmission.reset();
      show(exportHost, errorNotice(/** @type {any} */ (error)));
    } finally {
      exportButton.disabled = !(last?.counts?.REQUESTED ?? 0) || !closeVerdict.allowed;
    }
  }

  if (!readVerdict.allowed) {
    render(listHost, h("p", { class: "hint" }, readVerdict.reason));
  } else if (!store) {
    render(listHost, h("p", { class: "hint" }, "Chọn một cửa hàng để xem yêu cầu hóa đơn."));
  } else {
    render(tabsHost, tabs);
    void load();
  }

  return h(
    "section",
    { class: "screen invoices" },
    page({
      title: "Hóa đơn cần xuất",
      subtitle,
      info: infoButton(
        "Hóa đơn cần xuất là gì?",
        ...INVOICE_HOW.map((text) => h("p", null, text)),
        h(
          "p",
          { class: "hint" },
          "Tệp tải về là bảng tính (CSV, mở bằng Excel): mỗi dòng dịch vụ một hàng, cột số tiền ghi " +
            "“Số tiền theo giá tiệm đã thu (chưa tách thuế)”. Mỗi lần tải đều được ghi lại.",
        ),
      ),
      action: button({ label: "Tải lại", icon: "refresh", variant: "quiet", onClick: () => void load() }),
    }),
    tabsHost,
    listHost,
    exportHost,
    techHost,
    sheetsHost,
    actionBar(
      gated(
        exportButton,
        roleVerdict(closeVerdict, "Chủ tiệm hoặc người duyệt tải danh sách cho kế toán."),
      ),
    ),
  );
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/invoices",
  title: "Hóa đơn cần xuất",
  capability: "INVOICES_READ",
  needsStore: true,
  render: render_,
};
