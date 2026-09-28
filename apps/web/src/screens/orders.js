/**
 * Đơn hàng: find the order the customer in front of you is asking about, and see where every open
 * order is. Rebuilt on the V2 kit (`CONSOLE-REDESIGN-002`, spec V2 §5.3).
 *
 * This screen is mostly an exercise in not inventing things, because the order read model is thin
 * on purpose and every plausible way to thicken it is a lie:
 *
 *   - **The counter finds an order by the ticket it is tracked by.** `DEC-013`: a walk-in is known
 *     by the number the counter handed them and nothing else, so there is no customer name to
 *     search and there never will be one. "Số phiếu…" asks the server for that number on a
 *     business day (today unless the date chip says otherwise); the server decides which day
 *     "today" is, not this device's clock.
 *   - **A transfer's memo finds its order too** (`VIETQR-001`, `DEC-041`). The same field takes a
 *     transfer code (`NTL2809012`, as the bank app shows it, any case): the server resolves it to
 *     the ticket's day and number, or to the order id's first eight characters, in this store.
 *     "Mã chuyển khoản" switches the phone keyboard to letters; on a desk just type it.
 *   - **The list shows open orders by default.** Newest-first by creation, a hundred rows is three
 *     days of trade and laundry is collected later than that, so an order still in play fell off
 *     the list by age. `?open=true` keeps every order not yet completed or cancelled, any age.
 *     "Xong" and "Tất cả" read the recent list instead, which is the only one that holds closed
 *     orders.
 *   - **One status word per row, from one small mapping.** The server keeps four independent axes
 *     (commercial, intake, production, balance). The row shows the single human summary
 *     `core/orderStatus.js` folds them into; the four raw axes stay on the order page's
 *     "Chi tiết kỹ thuật". The mapping decides nothing — which step is legal next is the server's
 *     `next_steps`, shown on the order page (ORDER-STEPS-001).
 *   - **The tabs narrow, they do not interpret.** Đang làm / Sẵn sàng / Chờ giao / Xong filter the
 *     rows exactly as fetched and never refetch within a scope; "Đang lọc X/Y" says so while one is
 *     active, and the truncation line stays about the server's answer.
 *   - **The one amount shown is the server's.** `payable_total_vnd` is the accepted quote's total,
 *     read from the same stored revision the settlement checks a payment against. Nothing here
 *     adds, rounds or derives it; null is shown as "no total yet", never as 0.
 *   - **No command lives here any more.** The generic three-axis transition form and the
 *     delivery-leg form were removed with V2: every step, leg and payment is now a button on the
 *     order itself, offered only when the server says it is legal. Orders are created on
 *     ＋ Nhận đồ (`CONSOLE-REDESIGN-001`), which carries the ticket, the quote, its revision and
 *     seal, the mode and the acceptance time from the server's own answers; the manual
 *     create-from-quote form that asked a person to paste four of those is gone, and the header's
 *     "Nhận đồ" link is the one way there.
 *
 * @module screens/orders
 */

import { MAX_LIMIT, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { dateOnly, dateTime, money } from "../core/format.js";
import { enumVi } from "../core/i18n.js";
import { inGroup, modeBadge, orderStatus, timeAgo } from "../core/orderStatus.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { errorNotice, gated, icon, listView, revealError } from "../ui/components.js";
import {
  button,
  infoButton,
  inlineAlert,
  linkButton,
  list,
  listRow,
  page,
  segmented,
  searchField,
  show,
  statusPill,
} from "../ui/kit.js";
// PROMISE-001: "Hẹn 13:00 thứ Sáu 26/9" and the LATE pill on a row.
import { promiseMeta } from "../ui/promise.js";

const LIST_LIMIT = 100;

/**
 * The modes whose customer collects at the counter -- every mode outside the server's
 * `MODES_EXPECTING_RETURN`. `PICKUP_ONLY` is one: the courier fetched the laundry, the customer
 * comes in for it, and since the `DEC-032` addendum may have paid at the counter in advance.
 */
const SELF_COLLECT_MODES = new Set(["SELF_DROP_SELF_COLLECT", "PICKUP_ONLY"]);

/** `CommercialOrderStatus`: the values `?status=` from Hôm nay may name. */
const COMMERCIAL_STATUSES = new Set([
  "DRAFT",
  "REQUESTED",
  "STORE_CONFIRMATION_PENDING",
  "CONFIRMED",
  "ACTIVE",
  "CANCELLATION_REVIEW",
  "CANCELLED",
  "COMPLETED",
]);

/** A ticket number as staff type it: the counter issues 1, 2, 3… and restarts each morning. */
const TICKET_NUMBER = /^[1-9][0-9]{0,4}$/;

/**
 * VIETQR-001: what a transfer code starts with. The console only routes the text to the server's
 * `transfer_code` search; which order it names is the server's to decide.
 */
const TRANSFER_PREFIX = /^NTL/i;

/** A business date as `?date=` may carry it. */
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * The list tabs. The first three are the open orders (`?open=true`); "Xong" and "Tất cả" need
 * closed orders too, which only the recent list holds.
 */
const SEGMENTS = [
  { value: "active", label: "Đang làm", scope: "open" },
  { value: "ready", label: "Sẵn sàng", scope: "open" },
  { value: "delivery", label: "Chờ giao", scope: "open" },
  { value: "done", label: "Xong", scope: "recent" },
  { value: "all", label: "Tất cả", scope: "recent" },
];

/**
 * "Phiếu 17 · 24/09/2026", or null when the order's customer reference is not a counter ticket.
 *
 * The date is always printed: numbers restart every morning, so "phiếu 17" alone names one
 * customer per day the shop has been open. Used by the approvals card as well.
 *
 * @param {any} item an `OrderViewResponse`
 * @returns {string|null}
 */
export function ticketLabel(item) {
  if (item?.ticket_number === null || item?.ticket_number === undefined) return null;
  return `Phiếu ${item.ticket_number} · ${dateOnly(item.ticket_issued_on)}`;
}

/**
 * The short name a person says out loud: "Phiếu 17", or — for a customer who reached the shop by
 * message and was never handed a ticket — "Đơn nhắn tin". Never an identifier (spec V2 §2.5).
 *
 * @param {any} item
 * @returns {string}
 */
export function orderName(item) {
  if (item?.ticket_number === null || item?.ticket_number === undefined) return "Đơn nhắn tin";
  return `Phiếu ${item.ticket_number}`;
}

/**
 * The amount line: what the server says this order's accepted quote totals, and whether it is
 * still to collect.
 *
 * Returns null when the item is a command result rather than a read -- a command's reply carries
 * the eight command fields only, and "no total" there would be a false statement about an order
 * that has one.
 *
 * @param {any} item an `OrderViewResponse`
 * @returns {{label: string, text: string, known: boolean}|null}
 */
export function amountDue(item) {
  if (!item || !("payable_total_vnd" in item)) return null;
  const known = item.payable_total_vnd !== null && item.payable_total_vnd !== undefined;
  // A cancelled order that was never paid owes nothing: "Phải thu" beside it would be false.
  const cancelled = item.commercial === "CANCELLED";
  // PAYMENT-001 (DEC-035): a deposit was taken, so what the counter reads at pickup is what remains
  // -- the server's `remaining_vnd`, never the total less anything computed here.
  if (item.balance === "PARTIALLY_PAID" && !cancelled) {
    return { label: "Còn lại", text: money(item.remaining_vnd, "Chưa có tổng"), known };
  }
  // UNCLAIMED-001 (DEC-036): laundry left past the free days owes a storage fee on top of the
  // quote, so what is still to collect is the server's `remaining_vnd`, which includes it.
  if (item.balance === "UNPAID" && !cancelled && Number.isInteger(item.remaining_vnd)) {
    return { label: "Phải thu", text: money(item.remaining_vnd), known };
  }
  // PAYMENT-002 (round 7 wave 2 integration): an order that left on the customer's account is owed,
  // not paid -- what is still owed on it is the server's `remaining_vnd` (a storage fee included).
  if (item.balance === "ON_ACCOUNT" && Number.isInteger(item.remaining_vnd)) {
    return { label: "Ghi công nợ", text: money(item.remaining_vnd), known };
  }
  // Paid in full, a storage fee included when there was one: what was taken is the server's
  // `paid_vnd`, which the quoted total alone would understate.
  if (item.balance === "PAID" && Number.isInteger(item.paid_vnd)) {
    return { label: "Đã thu", text: money(item.paid_vnd), known };
  }
  const label =
    item.balance === "UNPAID"
      ? cancelled
        ? "Không thu"
        : "Phải thu"
      : item.balance === "PAID"
        ? "Đã thu"
        : "Tổng tiền";
  return { label, text: money(item.payable_total_vnd, "Chưa có tổng"), known };
}

/**
 * Paid in advance at the counter and not yet handed the bag (`DEC-032`). Said on the row because
 * this is the list the counter searches at pickup, and "Đã thu" alone reads as "nothing left to do".
 *
 * @param {any} item
 * @returns {boolean}
 */
function prepaidWaiting(item) {
  return (
    item.balance === "PAID" &&
    item.self_collection_recorded === false &&
    SELF_COLLECT_MODES.has(item.fulfillment_mode) &&
    item.commercial === "ACTIVE"
  );
}

/**
 * One order in a list: Phiếu N · status · mode · how long ago, and the money on the right.
 *
 * @param {any} item an `OrderViewResponse`
 * @returns {HTMLElement}
 */
export function orderRow(item) {
  const status = orderStatus(item);
  const mode = modeBadge(item.fulfillment_mode);
  const due = amountDue(item);
  return listRow({
    href: `#/orders/${encodeURIComponent(item.order_id)}`,
    leading: h(
      "span",
      { class: "row-item__icon", role: "img", "aria-label": mode.text, title: mode.text },
      icon(mode.icon),
    ),
    title: [
      orderName(item),
      h(
        "span",
        { class: "order-row__age", title: `Tạo ${dateTime(item.created_at)}` },
        timeAgo(item.created_at, { short: true }),
      ),
    ],
    meta: [
      statusPill({ state: status.state, text: status.text, token: status.token }),
      prepaidWaiting(item)
        ? h("span", { class: "hint" }, "Khách đã trả trước, chưa nhận đồ.")
        : null,
      // PROMISE-001: the time the customer was told, and a pill when it is late or due soon.
      promiseMeta(item),
    ],
    trailing: due ? h("span", { class: due.known ? "money" : "muted" }, due.text) : null,
    trailingMeta: due ? due.label : null,
    data: { orderId: String(item.order_id), group: status.group },
  });
}

/**
 * @param {import("../core/router.js").RouteContext} [context]
 * @returns {HTMLElement}
 */
export function render_(context) {
  const store = storeId();
  // ＋ Nhận đồ is gated on the capability its route needs; a person without it sees the button
  // disabled with the reason rather than a link that dead-ends.
  const newOrderVerdict = can(principal(), "QUOTES_WRITE");

  // Deep links into this screen (`?ticket=`, `?date=`, `?status=`, `?lookup=1`): prefill only,
  // re-checked exactly as typed input is.
  const handoff = context?.query;
  const prefill = (key, pattern) => {
    const value = String(handoff?.get(key) || "").trim();
    return pattern.test(value) ? value : "";
  };

  // --- the list -------------------------------------------------------------------------------

  // `?status=<CommercialOrderStatus>` from Hôm nay's status chips: narrow to exactly that commercial
  // state. A closed state is only on the recent list, so it chooses the scope as well.
  const statusFilter = (() => {
    const value = String(handoff?.get("status") || "").trim().toUpperCase();
    return COMMERCIAL_STATUSES.has(value) ? value : "";
  })();
  let segment = statusFilter ? "all" : "active";
  let commercialFilter = statusFilter;
  const scopeOf = () =>
    commercialFilter
      ? ["COMPLETED", "CANCELLED"].includes(commercialFilter)
        ? "recent"
        : "open"
      : (SEGMENTS.find((entry) => entry.value === segment)?.scope ?? "open");
  let fetchedScope = scopeOf();
  /** @type {any[]} */
  let lastItems = [];

  const board = listView({
    limit: LIST_LIMIT,
    fetch: () => {
      fetchedScope = scopeOf();
      return request(
        `/internal/v1/stores/${encodeURIComponent(store)}/orders?limit=${LIST_LIMIT}` +
          (fetchedScope === "open" ? "&open=true" : ""),
      );
    },
    renderItem: orderRow,
    renderList: (rows) => list(rows, { label: "Đơn hàng", id: "order-board" }),
    emptyText: "Chưa có đơn nào ở đây.",
    clearMetaOnError: true,
    skeletonRows: 4,
    truncationText: (limit) =>
      fetchedScope === "open"
        ? `Đang có hơn ${limit} đơn chưa xong; chỉ hiện ${limit} đơn mới nhất. ` +
          "Đơn cũ hơn vẫn tìm được bằng số phiếu."
        : `Chỉ hiện ${limit} đơn mới nhất; đơn cũ hơn tìm bằng số phiếu. ` +
          `Trần cứng phía máy chủ là ${MAX_LIMIT}.`,
    filter: {
      placeholder: "",
      noun: "đơn",
      // The needle is a tab, or `status:<TOKEN>` from Hôm nay. Nothing here is a rule about what
      // may happen to an order; it only groups what the rows already say.
      matches: (item, needle) =>
        needle.startsWith("status:")
          ? String(item.commercial || "").toLowerCase() === needle.slice("status:".length)
          : inGroup(item, needle),
      filteredEmptyText: "Không có đơn nào ở mục này.",
    },
    onLoaded: (items) => {
      lastItems = Array.isArray(items) ? items : [];
      drawSegments();
    },
  });

  const segmentHost = h("div", { class: "orders__tabs" });
  const statusChipHost = h("div");

  /** The tabs, redrawn after each read so the open-scope counts are the server's latest. */
  function drawSegments() {
    const counts = new Map();
    if (fetchedScope === "open") {
      for (const item of lastItems) {
        const group = orderStatus(item).group;
        counts.set(group, (counts.get(group) || 0) + 1);
      }
    }
    render(
      segmentHost,
      segmented({
        label: "Lọc đơn theo tình trạng",
        id: "order-tabs",
        // Five tabs with counts do not fit one row at 390 px; chips wrap rather than scroll, so no
        // tab is hidden off the edge of the screen.
        wrap: true,
        value: commercialFilter ? "" : segment,
        options: SEGMENTS.map((entry) => ({
          value: entry.value,
          label: entry.label,
          count:
            entry.scope === "open" && fetchedScope === "open" && counts.get(entry.value)
              ? String(counts.get(entry.value))
              : undefined,
        })),
        onChange: (value) => {
          const before = fetchedScope;
          segment = value;
          commercialFilter = "";
          render(statusChipHost);
          board.setFilter(value === "all" ? "" : value);
          if (scopeOf() !== before) void board.reload();
          else drawSegments();
        },
      }),
    );
  }

  function drawStatusChip() {
    if (!commercialFilter) {
      render(statusChipHost);
      return;
    }
    render(
      statusChipHost,
      h(
        "p",
        { class: "orders__status-chip" },
        h("span", null, "Chỉ đơn: "),
        statusPill({ state: "info", text: enumVi(commercialFilter), token: commercialFilter }),
        h(
          "button",
          {
            type: "button",
            dataVariant: "quiet",
            "aria-label": "Bỏ lọc tình trạng",
            onClick: () => {
              const before = fetchedScope;
              commercialFilter = "";
              segment = "active";
              render(statusChipHost);
              board.setFilter(segment);
              if (scopeOf() !== before) void board.reload();
              else drawSegments();
            },
          },
          icon("close"),
        ),
      ),
    );
  }

  board.setFilter(commercialFilter ? `status:${commercialFilter.toLowerCase()}` : segment);
  drawSegments();
  drawStatusChip();

  // --- find by ticket -------------------------------------------------------------------------

  // "Số phiếu…". The date is optional and empty means "today" -- decided by the server's clock on
  // the shop's business day, not by this device, whose clock and timezone are not the authority on
  // which day it is at the counter.
  const lookup = {
    number: prefill("ticket", TICKET_NUMBER),
    date: prefill("date", ISO_DATE),
  };
  const lookupSubmission = { seq: 0 };
  const lookupHost = h("div", { class: "stack stack--tight", id: "lookup-result" });

  /** @param {Event} [event] */
  async function submitLookup(event) {
    event?.preventDefault();
    const typed = lookup.number.replace(/\s+/g, "");
    if (codeMode || TRANSFER_PREFIX.test(typed)) {
      await submitTransferCode(typed.toUpperCase());
      return;
    }
    const number = lookup.number.trim();
    if (!TICKET_NUMBER.test(number)) {
      show(lookupHost, inlineAlert({ state: "warn", title: "Nhập số phiếu, ví dụ 17." }));
      return;
    }
    const day = lookup.date ? `ngày ${dateOnly(lookup.date)}` : "hôm nay";
    const query = new URLSearchParams({ ticket: number, limit: "20" });
    if (lookup.date) query.set("ticket_date", lookup.date);
    // A slower earlier answer must not overwrite a later one when staff retype quickly.
    const seq = ++lookupSubmission.seq;
    render(lookupHost, h("p", { class: "muted" }, `Đang tìm phiếu ${number} ${day}…`));
    try {
      const items = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/orders?${query}`,
      );
      if (seq !== lookupSubmission.seq) return;
      const found = Array.isArray(items) ? items : [];
      if (!found.length) {
        show(
          lookupHost,
          inlineAlert({
            state: "warn",
            title: `Không có đơn nào mang phiếu ${number} ${day}.`,
            body:
              "Có thể phiếu chưa được tạo đơn, hoặc phiếu của ngày khác — chọn ngày ghi trên phiếu.",
          }),
        );
        return;
      }
      render(
        lookupHost,
        h(
          "p",
          { class: "orders__lookup-head" },
          found.length === 1 ? `Phiếu ${number} ${day}` : `Phiếu ${number} ${day} có ${found.length} đơn`,
        ),
        list(found.map(orderRow), { label: `Kết quả tìm phiếu ${number}` }),
      );
    } catch (error) {
      if (seq !== lookupSubmission.seq) return;
      const notice = errorNotice(error);
      show(lookupHost, notice);
      revealError(notice);
    }
  }

  /**
   * VIETQR-001: the order a transfer code names, on the server's `transfer_code` search.
   *
   * @param {string} code
   */
  async function submitTransferCode(code) {
    if (!TRANSFER_PREFIX.test(code)) {
      show(lookupHost, inlineAlert({ state: "warn", title: "Nhập mã chuyển khoản, ví dụ NTL2809012." }));
      return;
    }
    const seq = ++lookupSubmission.seq;
    render(lookupHost, h("p", { class: "muted" }, `Đang tìm mã ${code}…`));
    try {
      const items = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/orders?${new URLSearchParams({
          transfer_code: code,
          limit: "20",
        })}`,
      );
      if (seq !== lookupSubmission.seq) return;
      const found = Array.isArray(items) ? items : [];
      if (!found.length) {
        show(
          lookupHost,
          inlineAlert({
            state: "warn",
            title: `Không có đơn nào mang mã ${code}.`,
            body: "Xem lại mã trong app ngân hàng, hoặc tìm theo số phiếu.",
          }),
        );
        return;
      }
      render(
        lookupHost,
        h("p", { class: "orders__lookup-head" }, found.length === 1 ? `Mã ${code}` : `Mã ${code} có ${found.length} đơn`),
        list(found.map(orderRow), { label: `Kết quả tìm mã ${code}` }),
      );
    } catch (error) {
      if (seq !== lookupSubmission.seq) return;
      const codes = /** @type {any} */ (error)?.reasonCodes || [];
      const reason = ["TRANSFER_CODE_ACCOUNT_MONTH", "TRANSFER_CODE_INVALID"].find((code) =>
        codes.includes(code),
      );
      if (reason) {
        show(
          lookupHost,
          inlineAlert({
            state: "warn",
            title:
              reason === "TRANSFER_CODE_ACCOUNT_MONTH"
                ? "Đây là mã công nợ tháng (NTLCN…), không phải một đơn."
                : `Mã ${code} không phải mã chuyển khoản của tiệm.`,
            body:
              reason === "TRANSFER_CODE_ACCOUNT_MONTH"
                ? "Khách công nợ trả theo sao kê: mở trang khách, bấm Thu công nợ."
                : "Mã đơn có dạng NTL + ngày tháng + số phiếu, ví dụ NTL2809012.",
          }),
        );
        return;
      }
      const notice = errorNotice(error);
      show(lookupHost, notice);
      revealError(notice);
    }
  }

  const search = searchField({
    id: "lookup-ticket",
    label: "Số phiếu",
    placeholder: "Số phiếu…",
    inputmode: "numeric",
    onInput: (value) => {
      lookup.number = value;
    },
  });
  search.input.value = lookup.number;
  // VIETQR-001: long enough for a transfer code typed on a desk; a ticket is still checked as one.
  search.input.maxLength = 25;
  let codeMode = false;
  const codeChip = h(
    "button",
    {
      type: "button",
      class: "chip orders__code-chip",
      id: "lookup-code-mode",
      "aria-pressed": "false",
      onClick: () => {
        codeMode = !codeMode;
        codeChip.setAttribute("aria-pressed", codeMode ? "true" : "false");
        search.input.inputMode = codeMode ? "text" : "numeric";
        search.input.placeholder = codeMode ? "Mã chuyển khoản…" : "Số phiếu…";
        search.input.setAttribute("autocapitalize", codeMode ? "characters" : "off");
        dateChip.hidden = codeMode;
        if (codeMode) dateRow.hidden = true;
        search.input.focus();
      },
    },
    "Mã chuyển khoản",
  );
  // Enter submits the form below; the kit's own Enter handler is not wired (no `onSubmit`).

  const dateInput = /** @type {HTMLInputElement} */ (
    h("input", {
      id: "lookup-date",
      type: "date",
      class: "orders__date-input",
      value: lookup.date,
      "aria-label": "Ngày trên phiếu",
      onInput: (event) => {
        lookup.date = /** @type {HTMLInputElement} */ (event.target).value;
        drawDateChip();
      },
    })
  );
  const dateChip = h("button", {
    type: "button",
    class: "chip orders__date-chip",
    "aria-expanded": "false",
    onClick: () => {
      const open = dateRow.hidden;
      dateRow.hidden = !open;
      dateChip.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) dateInput.focus();
    },
  });
  function drawDateChip() {
    render(dateChip, icon("clock"), h("span", null, lookup.date ? dateOnly(lookup.date) : "Hôm nay"));
  }
  drawDateChip();
  const dateRow = h(
    "div",
    { class: "orders__date-row" },
    dateInput,
    h(
      "button",
      {
        type: "button",
        dataVariant: "quiet",
        onClick: () => {
          lookup.date = "";
          dateInput.value = "";
          drawDateChip();
        },
      },
      "Hôm nay",
    ),
  );
  dateRow.hidden = !lookup.date;

  const lookupForm = h(
    "form",
    { class: "orders__lookup", role: "search", onSubmit: submitLookup },
    h(
      "div",
      { class: "orders__lookup-row" },
      search.node,
      h(
        "button",
        // A read. `data-intent="read"` is how the role checks tell it from a write control;
        // `test_staff_console_contract.py` pins which buttons may carry it.
        { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true", dataIntent: "read" },
        "Tìm",
      ),
    ),
    h(
      "div",
      { class: "orders__lookup-row orders__lookup-row--meta" },
      dateChip,
      dateRow,
      codeChip,
      // CUSTOMER-001: a regular is found by phone or name, on the customer's own page.
      can(principal(), "CUSTOMERS_READ").allowed
        ? h("a", { href: "#/customers", class: "link-action", id: "orders-customers" }, "Tìm theo khách")
        : null,
    ),
  );

  void board.reload();
  if (lookup.number) void submitLookup();
  // "Khách tới lấy đồ" on Hôm nay lands here with `?lookup=1`: the number pad, ready.
  if (handoff?.get("lookup") === "1") {
    setTimeout(() => search.input.focus(), 0);
  }

  const meta = h(
    "div",
    { class: "orders__meta" },
    board.filterStatus,
    board.bar.stamp,
    h(
      "button",
      { type: "button", dataVariant: "quiet", onClick: () => void board.reload() },
      icon("refresh"),
      "Tải lại",
    ),
  );

  return h(
    "section",
    { class: "screen orders" },
    page({
      title: "Đơn hàng",
      // The one create path (`CONSOLE-REDESIGN-001`): ＋ Nhận đồ issues the ticket, prices and
      // creates the order from the server's own answers — nothing to copy here.
      action: newOrderVerdict.allowed
        ? linkButton({ href: "#/new", label: "Nhận đồ", icon: "plus", variant: "primary" })
        : gated(button({ label: "Nhận đồ", icon: "plus", variant: "primary" }), newOrderVerdict),
      info: infoButton(
        "Tìm đơn của một khách thế nào?",
        h(
          "p",
          { class: "hint" },
          "Khách vãng lai chỉ có số phiếu, tiệm không lưu gì khác (DEC-013): tìm đơn bằng số phiếu " +
            "ở ô “Số phiếu…”. Khách quen có hồ sơ (DEC-034): bấm “Tìm theo khách”, tìm bằng số " +
            "điện thoại, 4 số cuối hoặc tên, rồi mở đơn từ trang của khách.",
        ),
      ),
    }),
    lookupForm,
    lookupHost,
    segmentHost,
    statusChipHost,
    h(
      "div",
      { class: "stack stack--tight" },
      board.truncation,
      board.host,
      meta,
    ),
  );
}

export const screen = {
  path: "/orders",
  title: "Đơn hàng",
  capability: "ORDERS_READ",
  needsStore: true,
  render: render_,
};
