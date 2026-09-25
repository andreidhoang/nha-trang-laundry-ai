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
 *     order itself, offered only when the server says it is legal. The one form left is the
 *     quote-to-order hand-off (`#/orders?quote=…&hash=…`), kept for the "Tạo đơn từ báo giá này"
 *     link and under "Nhập mã thủ công" otherwise.
 *
 * @module screens/orders
 */

import { MAX_LIMIT, Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UUID, dateOnly, dateTime, money, parseInstant } from "../core/format.js";
import { ACQUISITION_SOURCE_VI, enumVi } from "../core/i18n.js";
import { inGroup, modeBadge, orderStatus, timeAgo } from "../core/orderStatus.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  enumSelect,
  errorNotice,
  explain,
  gated,
  gatedFields,
  icon,
  labelled,
  listView,
  panel,
  resultLine,
  revealError,
  setResult,
} from "../ui/components.js";
import {
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

const LIST_LIMIT = 100;

/** `FulfillmentMode`, as the strict request model spells it. */
const FULFILLMENT_MODES = [
  "SELF_DROP_SELF_COLLECT",
  "PICKUP_AND_RETURN",
  "PICKUP_ONLY",
  "RETURN_ONLY",
];

/**
 * The modes whose customer collects at the counter -- every mode outside the server's
 * `MODES_EXPECTING_RETURN`. `PICKUP_ONLY` is one: the courier fetched the laundry, the customer
 * comes in for it, and since the `DEC-032` addendum may have paid at the counter in advance.
 */
const SELF_COLLECT_MODES = new Set(["SELF_DROP_SELF_COLLECT", "PICKUP_ONLY"]);

/**
 * `AcquisitionSource`. Where the customer says they found the shop.
 *
 * The order matters and is not alphabetical: the four the counter actually hears most sit first, so
 * the common answer is one glance away, and `UNKNOWN` sits last as the resting value rather than
 * hidden in the middle.
 *
 * **`UNKNOWN` is the default and is styled like every other option.** That is deliberate and it is
 * the whole design of this field. A counter that did not get round to asking must be able to leave
 * it, without a warning colour, a confirmation, or a hint that reads as disapproval — because the
 * alternative is a busy operator picking whichever value clears the form, and a channel report
 * built from those is worse than no report at all: it looks like evidence, and the shop will spend
 * money against it. The nudge to ask lives in the field hint, where it costs nothing if ignored.
 */
const ACQUISITION_SOURCES = [
  "WALK_IN",
  "GOOGLE_MAPS",
  "ZALO",
  "FACEBOOK",
  "PARTNER_FRONT_DESK",
  "REFERRAL_CUSTOMER",
  "LEAFLET_QR",
  "RETURNING",
  "UNKNOWN",
];

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

/** `OrderCreateRequest.quote_snapshot_hash`, exactly as the server's pattern spells it. */
const SNAPSHOT_HASH = /^JCS-SHA256-V1:[0-9a-f]{64}$/;

/** A ticket number as staff type it: the counter issues 1, 2, 3… and restarts each morning. */
const TICKET_NUMBER = /^[1-9][0-9]{0,4}$/;

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
    ],
    trailing: due ? h("span", { class: due.known ? "money" : "muted" }, due.text) : null,
    trailingMeta: due ? due.label : null,
    data: { orderId: String(item.order_id), group: status.group },
  });
}

/**
 * What the create form shows once the order exists: the way to it, and -- for a resend the server
 * answered from its idempotency ledger -- that nothing new was written.
 *
 * @param {any} created an `OrderResponse`
 * @returns {HTMLElement}
 */
function createdCard(created) {
  return h(
    "div",
    { class: "stack stack--tight" },
    created.replayed
      ? h(
          "div",
          { class: "notice", dataState: "info" },
          "Lệnh này đã chạy trước đó — đây là kết quả cũ hiện lại. Không có bản ghi mới nào " +
            "được tạo.",
        )
      : null,
    linkButton({
      href: `#/orders/${encodeURIComponent(created.order_id)}`,
      label: "Mở đơn",
      variant: "primary",
      block: true,
    }),
  );
}

/**
 * @param {import("../core/router.js").RouteContext} [context]
 * @returns {HTMLElement}
 */
export function render_(context) {
  const store = storeId();
  const me = principal();
  const writeVerdict = can(me, "ORDERS_WRITE");

  const createSubmission = new Submission("order-create");

  // The hand-off from `#/quotes`: the accepted-quote card links here carrying the four values the
  // create form needs, so none of them is typed or pasted. Nothing is trusted because it arrived
  // in a URL -- these are prefill only; the shapes are re-checked below exactly as typed input is,
  // and the server re-checks all four again.
  const handoff = context?.query;
  const prefill = (key, pattern) => {
    const value = String(handoff?.get(key) || "").trim();
    return pattern.test(value) ? value : "";
  };

  const draft = {
    contactId: prefill("contact", UUID),
    quoteId: prefill("quote", UUID),
    revision: prefill("revision", /^[1-9][0-9]{0,5}$/) || "1",
    hash: prefill("hash", SNAPSHOT_HASH),
    mode: FULFILLMENT_MODES[0],
    acceptedAt: "",
    // The instant the server recorded when staff pressed "Khách đã chốt giá", sent verbatim while
    // the field still shows it. Any edit to the field clears it and the typed value is sent instead.
    acceptedAtServer: "",
    // Rests on "nobody asked" until somebody says otherwise.
    source: "UNKNOWN",
  };
  // Arrived from "Tạo đơn từ báo giá này": the form is the reason the operator is here.
  const fromQuote = Boolean(draft.quoteId && draft.hash);

  const createBody = h("div");
  const createResultHost = h("div", { class: "stack" });
  const createResult = resultLine();

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
  search.input.maxLength = 5;
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
    h("div", { class: "orders__lookup-row orders__lookup-row--meta" }, dateChip, dateRow),
  );

  // --- create ---------------------------------------------------------------------------------

  /**
   * @returns {string} an empty string when the draft is submittable
   */
  function validateCreate() {
    if (!UUID.test(draft.contactId.trim())) return "Mã khách chưa đúng dạng.";
    if (!UUID.test(draft.quoteId.trim())) return "Mã báo giá chưa đúng dạng.";
    const revision = Number.parseInt(draft.revision.trim(), 10);
    if (!Number.isInteger(revision) || revision < 1) return "Bản báo giá phải là số nguyên từ 1.";
    if (!SNAPSHOT_HASH.test(draft.hash.trim())) {
      return "Mã niêm phong chưa đúng dạng. Chép lại nguyên văn từ bản báo giá.";
    }
    // An empty datetime is refused here rather than sent as an empty string, because the field is
    // required and timezone-aware: there is no defensible value to substitute for "not filled in".
    if (draft.acceptedAtServer) return "";
    if (!draft.acceptedAt) return "Chưa nhập thời điểm khách chốt giá.";
    if (Number.isNaN(new Date(draft.acceptedAt).getTime())) {
      return "Thời điểm khách chốt giá không đọc được.";
    }
    return "";
  }

  /**
   * @param {SubmitEvent} event
   */
  async function submitCreate(event) {
    event.preventDefault();

    if (!writeVerdict.allowed) {
      setResult(createResult, "danger", writeVerdict.reason);
      return;
    }

    const problem = validateCreate();
    if (problem) {
      setResult(createResult, "danger", problem);
      return;
    }

    const payload = {
      bound_contact_id: draft.contactId.trim(),
      quote_id: draft.quoteId.trim(),
      quote_revision: Number.parseInt(draft.revision.trim(), 10),
      quote_snapshot_hash: draft.hash.trim(),
      fulfillment_mode: draft.mode,
      acquisition_source: draft.source,
      // The server's own record of the moment when the field still shows it, verbatim. Otherwise
      // `datetime-local` yields a naive wall-clock string; the server requires an aware instant.
      // `Date` reads it in the device's timezone and `toISOString` emits UTC, so the offset is
      // always explicit. The device's timezone is therefore load-bearing, which is why the hint
      // under the field says so.
      customer_final_quote_accepted_at:
        draft.acceptedAtServer || new Date(draft.acceptedAt).toISOString(),
    };

    setResult(createResult, "warn", "Đang gửi lệnh tạo đơn…");
    render(createResultHost);

    try {
      const created = await request(`/internal/v1/stores/${encodeURIComponent(store)}/orders`, {
        method: "POST",
        body: payload,
        idempotencyKey: createSubmission.key(),
      });
      // Confirmed exactly once, here. The next submission is a new intent and gets a new key.
      createSubmission.reset();
      setResult(createResult, "ok", "Đã tạo đơn. Mở đơn để nhận đồ.");
      render(createResultHost, createdCard(created));
      // The quote-bound fields are cleared and the form rebuilt, so a second tap on a form that
      // still looks armed cannot mint a second order under a fresh key. An order is not a quote
      // revision; there is no cheap way to undo a duplicate one.
      draft.quoteId = "";
      draft.hash = "";
      draft.acceptedAt = "";
      draft.acceptedAtServer = "";
      // And the source goes back to "nobody asked", which is true again the moment this customer
      // leaves. Leaving it on the last answer would let the console supply a plausible value for
      // the next customer -- exactly the failure `UNKNOWN` exists to prevent, except committed by
      // the screen rather than by a hurried operator, and on a field that is immutable and that no
      // screen reads back. `mode` is deliberately left sticky: a wrong fulfilment mode surfaces
      // downstream when nobody comes to collect, and a wrong source surfaces nowhere, ever.
      draft.source = "UNKNOWN";
      render(createBody, gatedFields(createForm(), writeVerdict));
      await board.reload();
    } catch (error) {
      // Same rule as the transition below, and it matters more here: an order is not cheaply
      // undone. On a TIMEOUT or a NETWORK failure nobody knows whether the order exists, and
      // "Không tạo được đơn" invites the one action that mints a second one. The idempotency key
      // is kept on this path precisely so an unchanged resend replays rather than duplicates --
      // but the board is what actually answers the question, so that is what this asks for.
      const unknown = error.kind === "TIMEOUT" || error.kind === "NETWORK";
      setResult(
        createResult,
        error.kind === "REQUIRE_HUMAN" ? "warn" : "danger",
        unknown
          ? "Chưa biết lệnh có tới máy chủ hay không, nên chưa biết đơn đã được tạo hay chưa. " +
            "Đừng bấm lại — hãy tải lại bảng đơn và tìm mã khách này trước."
          : error.kind === "REQUIRE_HUMAN"
            ? "Cần người quyết định trước khi tạo đơn. Không có đơn nào được tạo."
            : "Không tạo được đơn. Máy chủ nêu lý do bên dưới, nguyên văn.",
      );
      const notice = errorNotice(error);
      render(createResultHost, notice);
      revealError(notice);
    }
  }

  /**
   * @returns {HTMLElement}
   */
  function createForm() {
    const contactInput = h("input", {
      type: "text",
      value: draft.contactId,
      autocomplete: "off",
      spellcheck: "false",
      dataFormat: "id",
      placeholder: "00000000-0000-0000-0000-000000000000",
      onInput: (event) => {
        draft.contactId = event.target.value;
        createSubmission.reset();
      },
    });

    const quoteInput = h("input", {
      type: "text",
      value: draft.quoteId,
      autocomplete: "off",
      spellcheck: "false",
      dataFormat: "id",
      placeholder: "00000000-0000-0000-0000-000000000000",
      onInput: (event) => {
        draft.quoteId = event.target.value;
        createSubmission.reset();
      },
    });

    const revisionInput = h("input", {
      type: "text",
      inputmode: "numeric",
      value: draft.revision,
      autocomplete: "off",
      maxlength: "6",
      placeholder: "1",
      onInput: (event) => {
        draft.revision = event.target.value;
        createSubmission.reset();
      },
    });

    const hashInput = h("input", {
      type: "text",
      value: draft.hash,
      autocomplete: "off",
      spellcheck: "false",
      dataFormat: "hash",
      placeholder: "JCS-SHA256-V1:…",
      "aria-invalid": draft.hash && !SNAPSHOT_HASH.test(draft.hash.trim()) ? "true" : null,
      onInput: (event) => {
        draft.hash = event.target.value;
        createSubmission.reset();
      },
    });

    const modeSelect = enumSelect("fulfillment_mode", FULFILLMENT_MODES, draft.mode);
    modeSelect.addEventListener("change", (event) => {
      draft.mode = /** @type {HTMLSelectElement} */ (event.target).value;
      createSubmission.reset();
    });

    const sourceSelect = enumSelect(
      "acquisition_source",
      ACQUISITION_SOURCES,
      draft.source,
      ACQUISITION_SOURCE_VI,
    );
    sourceSelect.addEventListener("change", (event) => {
      draft.source = /** @type {HTMLSelectElement} */ (event.target).value;
      createSubmission.reset();
    });

    const acceptedInput = h("input", {
      type: "datetime-local",
      value: draft.acceptedAt,
      onInput: (event) => {
        draft.acceptedAt = event.target.value;
        // A person changed it, so what they typed is what is sent.
        draft.acceptedAtServer = "";
        createSubmission.reset();
      },
    });

    const submit = h(
      "button",
      { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
      "Tạo đơn",
    );

    return h(
      "form",
      { class: "form", onSubmit: submitCreate },
      explain(
        "Vì sao lệnh tạo đơn đang luôn bị từ chối?",
        h(
          "p",
          null,
          "Máy chủ chỉ nhận một báo giá thoả đủ năm điều: giá đã là giá chính xác chứ không phải " +
            "ước tính, khách đã chốt bản đó, bản gửi lên đúng là bản khách chốt, đã có người " +
            "duyệt giá, và báo giá chưa hết hạn. Thiếu một điều là bị từ chối.",
        ),
        h(
          "p",
          null,
          "Báo giá phải là bản đã chốt: khách nghe giá, nhân viên bấm \u201cChốt giá\u201d ở màn " +
            "hình Báo giá, và bản chốt đó mới tạo được đơn. Một bản ước lượng hoặc một khoảng giá " +
            "sẽ bị từ chối kèm lý do. Trước ngày 25/08/2026 chưa có đường chốt giá nào nên không " +
            "đơn nào tạo được; nay màn hình Báo giá làm đúng việc đó.",
        ),
        h("p", null, h("a", { href: "#/gaps" }, "Xem khoảng trống: đường duyệt giá chính xác")),
      ),
      labelled({
        id: "order-contact",
        label: "Mã khách",
        // The old hint said "Chép từ màn hình Tiếp nhận" and was impossible to follow: that
        // screen rendered the contact id shortened, with no copy control anywhere, and cleared
        // the one input that ever held it in full. Both halves are fixed -- the intake rows are
        // copyable now, and the accepted-quote card links here with the value already filled --
        // so the hint names the path that works and keeps the copy path as the fallback.
        hint:
          "Thường không phải gõ: bấm “Tạo đơn từ báo giá này” ở màn hình Báo giá thì ô này đã " +
          "có sẵn. Cần điền tay thì chép ở màn hình Tiếp nhận. Màn hình này chưa tra cứu được " +
          "khách theo tên hay số điện thoại.",
        control: contactInput,
      }),
      labelled({
        id: "order-quote",
        label: "Mã báo giá",
        hint: "Của bản báo giá khách đã chốt.",
        control: quoteInput,
      }),
      labelled({
        id: "order-revision",
        label: "Bản báo giá",
        hint: "Số nguyên từ 1. Phải đúng bản mà khách đã chốt, không phải bản mới nhất.",
        control: revisionInput,
      }),
      labelled({
        id: "order-hash",
        label: "Mã niêm phong báo giá",
        hint: "Chép nguyên văn từ bản báo giá, đủ cả phần đầu. Máy chủ so khớp từng ký tự để chắc chắn đây đúng là bản khách đã chốt.",
        control: hashInput,
      }),
      labelled({
        id: "order-mode",
        label: "Hình thức giao nhận",
        control: modeSelect,
      }),
      labelled({
        id: "order-source",
        label: "Khách biết tiệm qua đâu",
        hint:
          "Hỏi một câu: “Anh/chị biết tiệm qua đâu ạ?” Chưa hỏi thì để nguyên “Chưa biết” — đó là " +
          "câu trả lời đúng, không phải thiếu sót. Ghi xong là không sửa được nữa.",
        control: sourceSelect,
      }),
      labelled({
        id: "order-accepted",
        label: "Thời điểm khách chốt giá",
        hint: draft.acceptedAtServer
          ? "Đã điền sẵn lúc bấm “Khách đã chốt giá”. Không cần sửa."
          : "Đọc theo giờ của máy bạn đang dùng — kiểm lại nếu máy đặt sai múi giờ. Bỏ trống thì không gửi.",
        control: acceptedInput,
      }),
      h("div", { class: "form__actions" }, gated(submit, writeVerdict)),
      createResult,
    );
  }


  // `gatedFields` as well as `gated`: a read-only role meets the refusal before spending the typing.
  render(createBody, gatedFields(createForm(), writeVerdict));
  void board.reload();

  /**
   * Fill "Thời điểm khách chốt giá" from the acceptance the server recorded a moment ago.
   *
   * The field was required and blank on arrival from the quote, so the operator typed a time from
   * memory for an event the server had just written down to the microsecond. Read, never guessed:
   * a revision no acceptance produced reads back null and the field stays empty for a person.
   */
  async function prefillAcceptedAt() {
    try {
      const quote = encodeURIComponent(draft.quoteId);
      const revision = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/quotes/${quote}?revision=${draft.revision}`,
      );
      const at = parseInstant(revision?.customer_accepted_at);
      // Somebody already typed a time while this was loading; theirs stands.
      if (!at || draft.acceptedAt) return;
      draft.acceptedAtServer = revision.customer_accepted_at;
      draft.acceptedAt = localInputValue(at);
      createSubmission.reset();
      // The form is rebuilt from `draft`; keep the cursor where the operator had it.
      const focused = createBody.contains(document.activeElement) ? document.activeElement?.id : "";
      render(createBody, gatedFields(createForm(), writeVerdict));
      if (focused) document.getElementById(focused)?.focus({ preventScroll: true });
    } catch {
      // Nothing to add: the field stays empty and its hint says how to fill it.
    }
  }

  if (fromQuote) void prefillAcceptedAt();
  if (lookup.number) void submitLookup();
  // "Khách tới lấy đồ" on Hôm nay lands here with `?lookup=1`: the number pad, ready.
  if (handoff?.get("lookup") === "1" || fromQuote) {
    setTimeout(() => {
      if (fromQuote) {
        const first = /** @type {HTMLElement|null} */ (createPanel.querySelector("#order-mode"));
        first?.scrollIntoView({ block: "center" });
        first?.focus({ preventScroll: true });
        return;
      }
      search.input.focus();
    }, 0);
  }

  const createPanel = panel({
    title: "Tạo đơn từ báo giá đã chốt",
    guardrail:
      "Đơn chỉ được tạo từ một báo giá khách đã chốt. Bấm “Khách đã chốt giá” ở màn " +
      "hình Báo giá trước, rồi mới tạo đơn ở đây. Máy chủ kiểm lại toàn bộ điều kiện; màn hình " +
      "này chỉ bắt lỗi gõ trước khi gửi.",
    children: h("div", { class: "stack" }, createBody, createResultHost),
  });

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
      info: infoButton(
        "Vì sao không có tên hay số điện thoại khách?",
        h(
          "p",
          { class: "hint" },
          "Chủ tiệm đã chốt: khách vãng lai chỉ được ghi bằng số phiếu, tiệm không lưu tên, số " +
            "điện thoại hay địa chỉ (DEC-013). Tìm đơn bằng số phiếu ở ô “Số phiếu…”.",
        ),
      ),
    }),
    fromQuote ? createPanel : null,
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
    fromQuote
      ? null
      : h(
          "details",
          { class: "manual-entry" },
          h("summary", null, "Nhập mã thủ công"),
          createPanel,
        ),
  );
}

/**
 * An instant as a `datetime-local` value in this device's timezone, to the minute.
 *
 * Only for display in the field: the instant itself is sent verbatim (`acceptedAtServer`). The
 * device's zone is used because that is the zone the field is read back in if somebody edits it.
 *
 * @param {Date} at
 * @returns {string}
 */
function localInputValue(at) {
  const pad = (value) => String(value).padStart(2, "0");
  return (
    `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}` +
    `T${pad(at.getHours())}:${pad(at.getMinutes())}`
  );
}

export const screen = {
  path: "/orders",
  title: "Đơn hàng",
  capability: "ORDERS_READ",
  needsStore: true,
  render: render_,
};
