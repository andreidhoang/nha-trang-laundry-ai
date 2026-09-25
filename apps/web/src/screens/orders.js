/**
 * Orders: the state board, and the two commands the API actually offers against it.
 *
 * This screen is mostly an exercise in not inventing things, because the order read model is far
 * thinner than an operations board usually is, and every plausible way to thicken it is a lie:
 *
 *   - **The counter finds an order by the ticket it is tracked by.** `DEC-013`: a walk-in is known
 *     by the number the counter handed them and nothing else, so there is no customer name to
 *     search and there never will be one. "Tìm theo số phiếu" asks the server for that number on a
 *     business day (today unless the slip says otherwise); the server decides which day "today"
 *     is, not this device's clock.
 *   - **The board shows open orders by default.** Newest-first by creation, a hundred rows is three
 *     days of trade and laundry is collected later than that, so an order still in play fell off
 *     the board by age. `?open=true` keeps every order not yet completed or cancelled, any age.
 *   - **The one amount shown is the server's.** `payable_total_vnd` is the accepted quote's total,
 *     read from the same stored revision the settlement checks a payment against. Nothing here
 *     adds, rounds or derives it; null is shown as "no total yet", never as 0. Beside it the
 *     label says whether it is still to collect ("Phải thu") or already collected ("Đã thu"),
 *     read off the `balance` status the server sent.
 *   - **The four dimensions are orthogonal and the screen says so once.** `commercial`, `intake`,
 *     `production` and `balance` move independently. Staff read four badges in a row as a pipeline
 *     unless told otherwise, and then treat `CONFIRMED` as meaning the washing started.
 *   - **The legal-transition table is not duplicated here.** All eight commercial targets are
 *     offered and the server refuses the illegal ones. Filtering the list in the browser would put a
 *     second copy of a domain rule in a place that cannot be kept in step with the first —
 *     `ENGINEERING_SPEC_V1.md:530`, and `FR-ORD-002` owns the table. The server's refusals name the
 *     blocking condition ("production is not released", "balance is not settled"), which is more
 *     useful than a greyed-out option that explains nothing.
 *   - **A stale version is not a retry.** Re-sending the same `If-Match` can never succeed, so the
 *     offer is to reload the board, not to try again.
 *   - **The toolbar's filter narrows, it does not interpret.** Reload is a plain re-read, stamped
 *     with its fetch time; the text box string-matches the ticket number, the id and the four state
 *     enums of rows already fetched and computes nothing, so it cannot grow into the second copy of a
 *     domain rule the transition-table note above warns against.
 *
 * @module screens/orders
 */

import { MAX_LIMIT, Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import {
  UUID,
  dateOnly,
  dateTime,
  matchesFilter,
  money,
  shortId,
} from "../core/format.js";
import { enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  dimensionBadge,
  enumSelect,
  errorNotice,
  explain,
  facts,
  gated,
  gatedFields,
  labelled,
  listView,
  panel,
  resultLine,
  revealError,
  setResult,
} from "../ui/components.js";

const LIST_LIMIT = 100;

/**
 * The modes whose customer collects at the counter -- every mode outside the server's
 * `MODES_EXPECTING_RETURN`. `PICKUP_ONLY` is one: the courier fetched the laundry, the customer
 * comes in for it, and since the `DEC-032` addendum may have paid at the counter in advance.
 */
const SELF_COLLECT_MODES = new Set(["SELF_DROP_SELF_COLLECT", "PICKUP_ONLY"]);

/**
 * `CommercialOrderStatus`, complete and unfiltered.
 *
 * Every value the enum has is offered as a target. Which of them is reachable from the order's
 * current state is a domain question, and the domain answers it — see the module note.
 */
const COMMERCIAL_TARGETS = [
  "DRAFT",
  "REQUESTED",
  "STORE_CONFIRMATION_PENDING",
  "CONFIRMED",
  "ACTIVE",
  "CANCELLATION_REVIEW",
  "CANCELLED",
  "COMPLETED",
];

/**
 * `IntakeStatus`, complete and unfiltered, and `ProductionStatus` likewise.
 *
 * Both routes have existed on the server since the beginning and no screen called either, so the
 * console could take an order to CONFIRMED and no further: `ACTIVE` refuses while intake is not
 * ACCEPTED, and every later step hangs off ACTIVE. Staff could create, quote and confirm an order
 * and then had nowhere to record that the customer's laundry was in their hands.
 *
 * As with the commercial list, every value is offered and the domain decides which is reachable.
 * `REJECTED` is here because refusing goods at the counter is a real counter action; the server
 * refuses it from AWAITING_HANDOFF, where custody was never received.
 */
const INTAKE_TARGETS = [
  "RECEIVED_PENDING_INSPECTION",
  "WAITING_PRICE_APPROVAL",
  "WAITING_CUSTOMER_RECONFIRMATION",
  "WAITING_SLOT_APPROVAL",
  "ACCEPTED",
  "REJECTED",
];

const PRODUCTION_TARGETS = [
  "QUEUED",
  "IN_PROCESS",
  "QUALITY_CHECK",
  "READY_AT_STORE",
  "RELEASED",
  "EXCEPTION",
];

/**
 * The three orthogonal dimensions of one order, as the domain models them.
 *
 * One command, one route each. They are separate on the server because they move independently --
 * an order can be washed while its commercial state sits at ACTIVE -- and presenting them as one
 * list of targets would invent a state machine the domain does not have.
 */
const DIMENSIONS = [
  {
    key: "commercial",
    label: "Thương mại",
    field: "commercial",
    targets: COMMERCIAL_TARGETS,
    path: (id) => `/internal/v1/orders/${encodeURIComponent(id)}/transition`,
    legend: "Trạng thái thương mại đích",
  },
  {
    key: "intake",
    label: "Nhận đồ",
    field: "intake",
    targets: INTAKE_TARGETS,
    path: (id) => `/internal/v1/orders/${encodeURIComponent(id)}/intake-transition`,
    legend: "Trạng thái nhận đồ đích",
  },
  {
    key: "production",
    label: "Sản xuất",
    field: "production",
    targets: PRODUCTION_TARGETS,
    path: (id) => `/internal/v1/orders/${encodeURIComponent(id)}/production-transition`,
    legend: "Trạng thái sản xuất đích",
  },
];

/** @param {string} key */
const dimensionOf = (key) => DIMENSIONS.find((entry) => entry.key === key) ?? DIMENSIONS[0];

/**
 * `CustodyResolution`, exactly as `DEC-024` settled it.
 *
 * Three members and deliberately not four: there is no code for "washed, walked away, no money",
 * because a customer whose laundry has been washed does not cancel — they pay and collect, or the
 * goods stay with the shop. The absence is the rule, so it cannot be negotiated at the counter.
 */
const CUSTODY_RESOLUTIONS = [
  "NOT_RECEIVED",
  "RETURNED_UNWASHED_REFUNDED",
  "SHOP_FAULT_NO_CHARGE",
];

/** A ticket number as staff type it: the counter issues 1, 2, 3… and restarts each morning. */
const TICKET_NUMBER = /^[1-9][0-9]{0,4}$/;

/**
 * `DeliveryLegKind` and the leg `outcome`, in the words the counter uses.
 *
 * Scoped maps passed to `enumSelect`, for the reason `ACQUISITION_SOURCE_VI` is scoped: `ENUM_GLOSS`
 * is one flat map across every server enum, and `RETURN`/`FAILED` are generic enough to collide
 * with a member of some later enum. The raw token stays as each option's title.
 */
const LEG_KIND_VI = {
  RETURN: "trả đồ cho khách",
  PICKUP: "lấy đồ của khách",
};

const LEG_OUTCOME_VI = {
  SUCCEEDED: "thành công",
  FAILED: "không thành công",
};

/**
 * "Phiếu 17 · 24/09/2026", or null when the order's customer reference is not a counter ticket.
 *
 * The date is always printed: numbers restart every morning, so "phiếu 17" alone names one
 * customer per day the shop has been open.
 *
 * @param {any} item an `OrderViewResponse`
 * @returns {string|null}
 */
export function ticketLabel(item) {
  if (item?.ticket_number === null || item?.ticket_number === undefined) return null;
  return `Phiếu ${item.ticket_number} · ${dateOnly(item.ticket_issued_on)}`;
}

/**
 * The amount line: what the server says this order's accepted quote totals, and whether it is
 * still to collect.
 *
 * Returns null when the item is a command result rather than a read -- a transition's reply
 * carries the eight command fields only, and "no total" there would be a false statement about
 * an order that has one.
 *
 * @param {any} item an `OrderViewResponse`
 * @returns {{label: string, text: string, known: boolean}|null}
 */
export function amountDue(item) {
  if (!item || !("payable_total_vnd" in item)) return null;
  const known = item.payable_total_vnd !== null && item.payable_total_vnd !== undefined;
  const label =
    item.balance === "UNPAID" ? "Phải thu" : item.balance === "PAID" ? "Đã thu" : "Tổng tiền";
  return { label, text: money(item.payable_total_vnd, "Chưa có tổng"), known };
}

/**
 * One order on the board.
 *
 * Headed by what the counter says out loud -- the ticket number -- and the amount, because those
 * are the two things a staff member needs with a customer standing in front of them. The id stays
 * underneath for the case where somebody reads it off another screen.
 *
 * @param {any} item an `OrderViewResponse`, or an `OrderResponse` from a command
 * @param {{onTransition?: (item: any) => void}} [options]
 * @returns {HTMLElement}
 */
function orderCard(item, options = {}) {
  const ticket = ticketLabel(item);
  const due = amountDue(item);
  return h(
    "article",
    { class: "card" },
    h(
      "div",
      { class: "spread" },
      h("strong", null, ticket || `Đơn ${shortId(item.order_id)}`),
      due
        ? h(
            "span",
            { class: "row" },
            h("span", { class: "hint" }, due.label),
            h("span", { class: due.known ? "money" : "" }, due.text),
          )
        : null,
    ),
    h(
      "p",
      { class: "hint" },
      [
        item.created_at ? `Tạo ${dateTime(item.created_at)}` : null,
        item.fulfillment_mode ? enumVi(item.fulfillment_mode) : null,
        "Mã ",
      ]
        .filter(Boolean)
        .join(" · "),
      h("span", { class: "mono", title: item.order_id }, shortId(item.order_id)),
    ),
    item.replayed
      ? h(
          "div",
          { class: "notice", dataState: "info" },
          "Lệnh này đã chạy trước đó — đây là kết quả cũ hiện lại. Không có bản ghi mới nào " +
            "được tạo.",
        )
      : null,
    facts([
      ["Thương mại", dimensionBadge(item.commercial)],
      ["Tiếp nhận", dimensionBadge(item.intake)],
      ["Sản xuất", dimensionBadge(item.production)],
      ["Công nợ", dimensionBadge(item.balance)],
      ["Bản ghi", `v${item.row_version}`, { mono: true }],
    ]),
    // `DEC-032`: paid in advance, not collected yet. Said on the card because this is the list
    // the counter searches at pickup, and "Đã thu" alone reads as "nothing left to do".
    item.balance === "PAID" &&
    item.self_collection_recorded === false &&
    SELF_COLLECT_MODES.has(item.fulfillment_mode)
      ? h(
          "p",
          { class: "hint" },
          "Khách đã trả trước, chưa nhận đồ — mở đơn và bấm “Khách đã nhận đồ” khi đưa đồ.",
        )
      : null,
    h(
      "div",
      { class: "form__actions" },
      h(
        "a",
        { class: "button", href: `#/orders/${encodeURIComponent(item.order_id)}` },
        "Mở đơn",
      ),
      options.onTransition
        ? h(
            "button",
            { type: "button", onClick: () => options.onTransition(item) },
            "Chọn để chuyển trạng thái",
          )
        : null,
    ),
  );
}

/**
 * The standing note about what the order read model deliberately does not contain.
 *
 * @returns {HTMLElement}
 */
function readModelNotice() {
  return explain(
    "Vì sao không có tên hay số điện thoại khách?",
    h(
      "p",
      null,
      "Chủ tiệm đã chốt: khách vãng lai chỉ được ghi bằng số phiếu, tiệm không lưu tên, số " +
        "điện thoại hay địa chỉ (DEC-013). Tìm đơn bằng số phiếu ở ô “Tìm theo số phiếu”.",
    ),
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

  const transitionSubmission = new Submission("order-transition");

  // Deep links into this screen (`?ticket=`, `?order=`): prefill only, re-checked like typed input.
  const handoff = context?.query;
  const prefill = (key, pattern) => {
    const value = String(handoff?.get(key) || "").trim();
    return pattern.test(value) ? value : "";
  };

  // "Tìm theo số phiếu". The date is optional and empty means "today" -- decided by the server's
  // clock on the shop's business day, not by this device, whose clock and timezone are not the
  // authority on which day it is at the counter.
  const lookup = { number: prefill("ticket", TICKET_NUMBER), date: "" };
  const lookupSubmission = { seq: 0 };
  const lookupHost = h("div", { class: "stack" });
  const lookupResult = resultLine();

  // Open orders by default: an order still in play must not fall off the board because newer ones
  // arrived. "Gần đây" is the old newest-first view, kept for looking something up after it closed.
  let boardScope = "open";

  /** @type {{orderId: string, rowVersion: string, target: string}} */
  // DEC-024. `custodyResolution` is sent only when the target is CANCELLED, and the server decides
  // whether it was needed: a cancellation before any work has begun does not require one, and one
  // after work has begun is refused without it. The console does not try to predict which, because
  // that would be duplicating the legal-transition table the note at the top of this file warns
  // against -- it offers the field and lets the refusal explain itself.
  const move = {
    orderId: "",
    rowVersion: "",
    dimension: "commercial",
    target: COMMERCIAL_TARGETS[0],
    custodyResolution: "",
    // The one fact intake carries that the server cannot read for itself. `evaluate_delivery`
    // returns REQUIRE_HUMAN for every slot at this stage, so it is the operator's word or nothing.
    slotApproved: false,
  };

  const moveBody = h("div");
  const moveResultHost = h("div", { class: "stack" });
  const moveResult = resultLine();

  /** @param {any} item an `OrderResponse` picked off the board for a transition */
  const pickOrder = (item) => {
    move.orderId = item.order_id;
    move.rowVersion = String(item.row_version);
    redrawMove();
    moveBody.scrollIntoView({ block: "start", behavior: "smooth" });
  };

  /**
   * The board. The filter narrows the rows exactly as last fetched and never refetches, so the
   * "M fetched" in the status line and the truncation disclosure stay about the server's answer,
   * not about what happens to be visible. The match is a case-insensitive substring test over the
   * values the row already renders — the order id and the four state enums, verbatim.
   */
  const board = listView({
    limit: LIST_LIMIT,
    fetch: () =>
      request(
        `/internal/v1/stores/${encodeURIComponent(store)}/orders?limit=${LIST_LIMIT}` +
          (boardScope === "open" ? "&open=true" : ""),
      ),
    renderItem: (item) => orderCard(item, { onTransition: pickOrder }),
    emptyText: "Không có đơn nào ở đây.",
    clearMetaOnError: true,
    truncationText: (limit) =>
      boardScope === "open"
        ? `Đang có hơn ${limit} đơn chưa xong; bảng chỉ hiện ${limit} đơn mới nhất. ` +
          "Đơn cũ hơn vẫn tìm được bằng số phiếu hoặc mở thẳng theo mã đơn."
        : `Chỉ hiện ${limit} đơn mới nhất; đơn cũ hơn tìm bằng số phiếu hoặc mở theo mã đơn. ` +
          `Trần cứng phía máy chủ là ${MAX_LIMIT}.`,
    filter: {
      placeholder: "Lọc theo số phiếu, mã đơn hoặc trạng thái…",
      label: "Lọc bảng đơn",
      noun: "đơn",
      matches: (item, needle) =>
        matchesFilter(
          [
            item.ticket_number === null || item.ticket_number === undefined
              ? ""
              : `phiếu ${item.ticket_number}`,
            item.order_id,
            item.commercial,
            item.intake,
            item.production,
            item.balance,
          ],
          needle,
        ),
      filteredEmptyText: "Không có đơn nào khớp bộ lọc.",
    },
  });

  const scopeSelect = /** @type {HTMLSelectElement} */ (
    h(
      "select",
      { name: "board_scope" },
      h("option", { value: "open", selected: true }, "Đơn chưa xong"),
      h("option", { value: "recent" }, "Mọi đơn gần đây"),
    )
  );
  scopeSelect.addEventListener("change", (event) => {
    boardScope = /** @type {HTMLSelectElement} */ (event.target).value;
    void board.reload();
  });

  // --- find by ticket -------------------------------------------------------------------------

  /**
   * @param {SubmitEvent} event
   */
  async function submitLookup(event) {
    event.preventDefault();
    const number = lookup.number.trim();
    if (!TICKET_NUMBER.test(number)) {
      setResult(lookupResult, "danger", "Nhập số phiếu, ví dụ 17.");
      render(lookupHost);
      return;
    }
    const day = lookup.date ? `ngày ${dateOnly(lookup.date)}` : "hôm nay";
    const query = new URLSearchParams({ ticket: number, limit: "20" });
    if (lookup.date) query.set("ticket_date", lookup.date);
    // A slower earlier answer must not overwrite a later one when staff retype quickly.
    const seq = ++lookupSubmission.seq;
    setResult(lookupResult, "warn", `Đang tìm phiếu ${number} ${day}…`);
    render(lookupHost);
    try {
      const items = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/orders?${query}`,
      );
      if (seq !== lookupSubmission.seq) return;
      const found = Array.isArray(items) ? items : [];
      if (!found.length) {
        setResult(
          lookupResult,
          "warn",
          `Không có đơn nào mang phiếu ${number} ${day}. Có thể phiếu chưa được tạo đơn, ` +
            "hoặc phiếu của ngày khác — chọn ngày ghi trên phiếu.",
        );
        render(lookupHost);
        return;
      }
      setResult(
        lookupResult,
        "ok",
        found.length === 1
          ? `Phiếu ${number} ${day}:`
          : `Phiếu ${number} ${day} có ${found.length} đơn:`,
      );
      render(
        lookupHost,
        found.map((item) => orderCard(item, { onTransition: pickOrder })),
      );
    } catch (error) {
      if (seq !== lookupSubmission.seq) return;
      setResult(lookupResult, "danger", "Chưa tìm được. Máy chủ nêu lý do bên dưới.");
      const notice = errorNotice(error);
      render(lookupHost, notice);
      revealError(notice);
    }
  }

  const ticketInput = h("input", {
    type: "text",
    inputmode: "numeric",
    autocomplete: "off",
    maxlength: "5",
    placeholder: "17",
    value: lookup.number,
    onInput: (event) => {
      lookup.number = /** @type {HTMLInputElement} */ (event.target).value;
    },
  });
  const ticketDateInput = h("input", {
    type: "date",
    onInput: (event) => {
      lookup.date = /** @type {HTMLInputElement} */ (event.target).value;
    },
  });
  const lookupForm = h(
    "form",
    { class: "form", onSubmit: submitLookup },
    labelled({ id: "lookup-ticket", label: "Số phiếu", control: ticketInput }),
    labelled({
      id: "lookup-date",
      label: "Ngày trên phiếu",
      hint: "Để trống là hôm nay.",
      control: ticketDateInput,
    }),
    h(
      "div",
      { class: "form__actions" },
      h(
        "button",
        // A read. `data-intent="read"` is how the role checks tell it from a write control;
        // `test_staff_console_contract.py` pins which buttons may carry it.
        { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true", dataIntent: "read" },
        "Tìm",
      ),
    ),
    lookupResult,
  );

  /** Any structural change to the transition form invalidates its key and redraws it. */
  const redrawMove = () => {
    transitionSubmission.reset();
    render(moveBody, gatedFields(moveForm(), writeVerdict));
  };

  // --- transition -----------------------------------------------------------------------------

  /**
   * @returns {string} an empty string when the command is submittable
   */
  function validateMove() {
    if (!UUID.test(move.orderId.trim())) return "Mã đơn chưa đúng dạng.";
    const version = Number.parseInt(move.rowVersion.trim(), 10);
    if (!Number.isInteger(version) || version < 1) {
      return "Chọn lại đơn từ bảng bên dưới để lấy đúng bản ghi.";
    }
    return "";
  }

  /**
   * @param {SubmitEvent} event
   */
  async function submitMove(event) {
    event.preventDefault();

    if (!writeVerdict.allowed) {
      setResult(moveResult, "danger", writeVerdict.reason);
      return;
    }

    const problem = validateMove();
    if (problem) {
      setResult(moveResult, "danger", problem);
      return;
    }

    setResult(moveResult, "warn", "Đang gửi lệnh chuyển trạng thái…");
    render(moveResultHost);

    try {
      const dimension = dimensionOf(move.dimension);
      let body = { target: move.target };
      if (dimension.key === "commercial" && move.target === "CANCELLED" && move.custodyResolution) {
        body = { target: move.target, custody_resolution: move.custodyResolution };
      } else if (dimension.key === "intake") {
        body = { target: move.target, slot_approved: move.slotApproved };
      }
      const moved = await request(dimension.path(move.orderId.trim()), {
        method: "POST",
        body,
        idempotencyKey: transitionSubmission.key(),
        ifMatch: Number.parseInt(move.rowVersion.trim(), 10),
      });
      transitionSubmission.reset();
      // The row moved, so the version held in this form is now one behind. Adopt the version the
      // server just returned rather than leaving a value that would produce a STALE on the next
      // command for no reason the operator could see.
      move.rowVersion = String(moved.row_version);
      setResult(
        moveResult,
        "ok",
        `${dimension.label}: đã chuyển sang ${enumVi(moved[dimension.field])}. ` +
          `Bản ghi giờ là v${moved.row_version}.`,
      );
      render(moveResultHost, orderCard(moved));
      render(moveBody, gatedFields(moveForm(), writeVerdict));
      await board.reload();
    } catch (error) {
      // "Trạng thái đơn không đổi" is a claim about the server, and for a TIMEOUT or a NETWORK
      // failure nobody knows whether it is true: the request may have been applied and the answer
      // lost on the way back. Asserting it for those two told an operator at the counter that a
      // transition had not happened when it might have, and the reasonable next move -- press it
      // again -- is the one that produces a second effect. `Idempotency-Key` protects an exact
      // resend, but the operator has no way to know that from this sentence, and the board is what
      // actually settles it.
      const unknown = error.kind === "TIMEOUT" || error.kind === "NETWORK";
      setResult(
        moveResult,
        error.kind === "REQUIRE_HUMAN" ? "warn" : "danger",
        unknown
          ? "Chưa biết lệnh có tới máy chủ hay không, nên chưa biết đơn đã chuyển hay chưa. " +
            "Đừng bấm lại — hãy tải lại bảng đơn và xem trạng thái thật."
          : error.kind === "REQUIRE_HUMAN"
            ? "Cần người duyệt trước khi chuyển. Trạng thái đơn không đổi."
            : // The commonest refusal in a shop with two tablets, and the one with a different
              // next action: nothing is wrong with the command, somebody else got there first.
              // "Máy chủ từ chối" reads as "you did something wrong" and sends staff to re-check a
              // form that was correct, when the only thing that helps is a fresh board.
              error.kind === "STALE" || error.kind === "PRECONDITION_REQUIRED"
              ? "Đơn này vừa được người khác đổi trong lúc bạn đang xem, nên lệnh của bạn bị từ " +
                "chối và trạng thái đơn không đổi. Tải lại bảng đơn rồi làm lại theo số mới."
              : "Máy chủ từ chối chuyển trạng thái. Trạng thái đơn không đổi.",
      );
      const notice = errorNotice(error);
      render(
        moveResultHost,
        notice,
        // A stale version can never be fixed by repeating the same command: the `If-Match` in hand
        // is already wrong. The only useful offer is a fresh board.
        error.kind === "STALE" || error.kind === "PRECONDITION_REQUIRED"
          ? h(
              "div",
              { class: "form__actions" },
              h(
                "button",
                {
                  type: "button",
                  onClick: () => {
                    move.rowVersion = "";
                    redrawMove();
                    void board.reload();
                  },
                },
                "Tải lại bảng",
              ),
            )
          : null,
      );
      revealError(notice);
    }
  }

  /**
   * @returns {HTMLElement}
   */
  function moveForm() {
    const orderInput = h("input", {
      type: "text",
      value: move.orderId,
      autocomplete: "off",
      spellcheck: "false",
      dataFormat: "id",
      placeholder: "00000000-0000-0000-0000-000000000000",
      onInput: (event) => {
        move.orderId = event.target.value;
        transitionSubmission.reset();
      },
    });

    const versionInput = h("input", {
      type: "text",
      inputmode: "numeric",
      value: move.rowVersion,
      autocomplete: "off",
      maxlength: "9",
      placeholder: "1",
      onInput: (event) => {
        move.rowVersion = event.target.value;
        transitionSubmission.reset();
      },
    });

    const dimension = dimensionOf(move.dimension);

    const dimensionSelect = /** @type {HTMLSelectElement} */ (
      h(
        "select",
        { name: "dimension" },
        DIMENSIONS.map((entry) =>
          h(
            "option",
            { value: entry.key, selected: entry.key === move.dimension, title: entry.key },
            entry.label,
          ),
        ),
      )
    );
    dimensionSelect.addEventListener("change", (event) => {
      move.dimension = /** @type {HTMLSelectElement} */ (event.target).value;
      // A target from the previous dimension is meaningless against the new route, so it is
      // replaced rather than carried across where the server would refuse it as a bad enum.
      move.target = dimensionOf(move.dimension).targets[0];
      move.custodyResolution = "";
      move.slotApproved = false;
      transitionSubmission.reset();
      redrawMove();
    });

    const targetSelect = enumSelect("target", dimension.targets, move.target);
    targetSelect.addEventListener("change", (event) => {
      move.target = /** @type {HTMLSelectElement} */ (event.target).value;
      transitionSubmission.reset();
      redrawMove();
    });

    const slotInput = h("input", {
      type: "checkbox",
      checked: move.slotApproved,
      onChange: (event) => {
        move.slotApproved = /** @type {HTMLInputElement} */ (event.target).checked;
        transitionSubmission.reset();
      },
    });

    const custodySelect = enumSelect(
      "custody_resolution",
      ["", ...CUSTODY_RESOLUTIONS],
      move.custodyResolution,
    );
    custodySelect.addEventListener("change", (event) => {
      move.custodyResolution = /** @type {HTMLSelectElement} */ (event.target).value;
      transitionSubmission.reset();
    });

    const submit = h(
      "button",
      { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
      "Chuyển trạng thái",
    );

    return h(
      "form",
      { class: "form", onSubmit: submitMove },
      labelled({
        id: "move-order",
        label: "Mã đơn hàng",
        hint: "Bấm “Chọn để chuyển trạng thái” trên một đơn ở bảng bên dưới để điền sẵn ô này.",
        control: orderInput,
      }),
      labelled({
        id: "move-version",
        label: "Bản ghi bạn đang giữ",
        hint: "Nếu ai đó vừa đổi đơn này trong lúc bạn đang xem, máy chủ sẽ từ chối và không có gì thay đổi — khi đó hãy tải lại bảng.",
        control: versionInput,
      }),
      labelled({
        id: "move-dimension",
        label: "Chiều cần chuyển",
        hint:
          "Ba chiều của một đơn chạy độc lập nhau: thương mại, nhận đồ, sản xuất. " +
          "Một đơn không thể sang “Đang chạy” khi chưa nhận đồ, và không thể giặt khi chưa chạy.",
        control: dimensionSelect,
      }),
      labelled({
        id: "move-target",
        label: dimension.legend,
        hint: "Máy chủ quyết định chuyển đổi nào hợp lệ.",
        control: targetSelect,
      }),
      ...(dimension.key === "intake"
        ? [
            labelled({
              id: "move-slot",
              label: "Đã duyệt lịch cho đơn này",
              hint:
                "Chỉ tích khi bạn đã xác nhận tiệm còn chỗ làm đơn này. Máy chủ không tự quyết " +
                "được điều đó, nên đây là lời của bạn — và nó được ghi lại kèm tên bạn.",
              control: slotInput,
            }),
          ]
        : []),
      ...(dimension.key === "commercial" && move.target === "CANCELLED"
        ? [
            labelled({
              id: "move-custody",
              label: "Đồ và tiền đã xử lý thế nào",
              hint:
                "Chỉ cần khi đơn đã bắt đầu làm: đã nhận đồ, đã vào máy, hay đã thu tiền. " +
                "Khách đổi ý ngay tại quầy khi chưa nhận đồ thì để trống. " +
                "Không có mục nào cho “đã giặt rồi khách bỏ đi mà không trả tiền”: " +
                "đồ đã giặt thì khách trả tiền và lấy về, hoặc đồ ở lại tiệm (DEC-024).",
              control: custodySelect,
            }),
          ]
        : []),
      h("div", { class: "action-bar" }, gated(submit, writeVerdict)),
      moveResult,
    );
  }

  // `gatedFields` as well as `gated`: the submit was disabled with a reason and every field above
  // it stayed live, so an AUDITOR could fill in an order id, a version and a seal and only then
  // meet the refusal. Both forms are rebuilt on state changes, so this wraps the render rather
  // than running once.
  render(moveBody, gatedFields(moveForm(), writeVerdict));
  void board.reload();

  /**
   * `#/orders?order=<id>`, from the order screen: read the order by id and pick it for a transition
   * with the version just read, so an order that left the board is as movable as one on it.
   *
   * @param {string} id
   */
  async function pickById(id) {
    try {
      pickOrder(await request(`/internal/v1/orders/${encodeURIComponent(id)}`));
    } catch (error) {
      setResult(moveResult, "danger", "Không đọc được đơn này để chuyển trạng thái.");
      const notice = errorNotice(error);
      render(moveResultHost, notice);
      revealError(notice);
    }
  }

  const orderToMove = prefill("order", UUID);
  if (orderToMove) void pickById(orderToMove);
  if (lookup.number) void submitLookup(/** @type {any} */ ({ preventDefault() {} }));

  // FULFILMENT-001 / DEC-023. No amount field, deliberately: the money was taken at the counter.
  const legDraft = { orderId: "", kind: "RETURN", outcome: "SUCCEEDED" };
  const legResultHost = h("div", { class: "stack" });
  const legResult = resultLine();
  const legSubmission = new Submission("delivery-leg");
  const legOrderInput = h("input", {
    type: "text",
    autocomplete: "off",
    dataFormat: "id",
    placeholder: "00000000-0000-0000-0000-000000000000",
    onInput: (event) => {
      legDraft.orderId = event.target.value;
      legSubmission.reset();
    },
  });
  const legKindSelect = enumSelect("leg_kind", ["RETURN", "PICKUP"], legDraft.kind, LEG_KIND_VI);
  legKindSelect.addEventListener("change", (event) => {
    legDraft.kind = event.target.value;
    legSubmission.reset();
  });
  const legOutcomeSelect = enumSelect(
    "outcome",
    ["SUCCEEDED", "FAILED"],
    legDraft.outcome,
    LEG_OUTCOME_VI,
  );
  legOutcomeSelect.addEventListener("change", (event) => {
    legDraft.outcome = event.target.value;
    legSubmission.reset();
  });
  const legBody = h(
    "form",
    {
      class: "form",
      onSubmit: async (event) => {
        event.preventDefault();
        if (!UUID.test(legDraft.orderId.trim())) {
          setResult(legResult, "danger", "Mã đơn chưa đúng dạng.");
          render(legResultHost, legResult);
          return;
        }
        setResult(legResult, "warn", "Đang ghi nhận…");
        render(legResultHost, legResult);
        try {
          const recorded = await request(
            `/internal/v1/orders/${encodeURIComponent(legDraft.orderId.trim())}/delivery-legs`,
            {
              method: "POST",
              body: { leg_kind: legDraft.kind, outcome: legDraft.outcome },
              idempotencyKey: legSubmission.key(),
            },
          );
          legSubmission.reset();
          setResult(
            legResult,
            "ok",
            recorded.completes_fulfillment
              ? "Đã ghi. Khách đã nhận đồ — đơn này đóng được rồi."
              : "Đã ghi chuyến giao.",
          );
          render(legResultHost, legResult);
        } catch (error) {
          setResult(legResult, error.kind === "REQUIRE_HUMAN" ? "warn" : "danger", "Không ghi được chuyến giao.");
          render(legResultHost, legResult, errorNotice(error));
          revealError(legResultHost);
        }
      },
    },
    labelled({ id: "leg-order", label: "Mã đơn hàng", control: legOrderInput }),
    labelled({ id: "leg-kind", label: "Chuyến", control: legKindSelect }),
    labelled({ id: "leg-outcome", label: "Kết quả", control: legOutcomeSelect }),
    // Gated like every other write on this screen. It was not, until the console was driven as an
    // AUDITOR: a read-only session was shown a live "Ghi nhận" that records a delivery leg, and the
    // server answered 403. The two forms above it were disabled and explained; this one was simply
    // missed, which is the failure mode `core/rbac.js` opens by naming -- a button that always
    // fails is worse than no button.
    h(
      "div",
      { class: "form__actions" },
      gated(
        h(
          "button",
          { type: "submit", class: "button", dataRequiresNetwork: "true" },
          "Ghi nhận",
        ),
        writeVerdict,
      ),
    ),
  );

  // Orders are created on ＋ Nhận đồ (`CONSOLE-REDESIGN-001`), which carries the ticket, the
  // quote, its revision and seal, the mode and the acceptance time from the server's own answers.
  // The manual form that stood here asked a person to paste four of those.
  const createPanel = panel({
    eyebrow: "Khách gửi đồ",
    title: "Tạo đơn mới",
    children: h(
      "p",
      null,
      "Đơn mới được tạo ở ",
      h("a", { href: "#/new" }, "＋ Nhận đồ"),
      ": phát phiếu, tính giá, rồi “Khách đồng ý — tạo đơn”. Không phải chép mã nào.",
    ),
  });

  const lookupPanel = panel({
    eyebrow: "Khách tới lấy đồ",
    title: "Tìm theo số phiếu",
    children: h("div", { class: "stack" }, lookupForm, lookupHost),
  });

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Đơn của cửa hàng"),
      h("h1", null, "Đơn hàng"),
      h(
        "p",
        { class: "screen__lede" },
        "Đơn nào đang ở đâu. Mỗi đơn có bốn phần chạy riêng — bán hàng, nhận đồ, làm đồ, tiền — " +
          "nên hãy đọc từng phần một.",
      ),
    ),
    lookupPanel,
    panel({
      eyebrow: "Đã ghi",
      title: "Bảng đơn của cửa hàng",
      count: board.count,
      guardrail:
        "Bốn phần của một đơn — bán hàng, nhận đồ, làm đồ, tiền — chạy riêng với nhau. Đơn đã " +
        "xác nhận vẫn có thể chưa bắt đầu giặt và chưa thu tiền. Đọc bốn nhãn như bốn câu trả " +
        "lời riêng, không phải một chuỗi nối tiếp.",
      children: h(
        "div",
        { class: "stack" },
        labelled({ id: "board-scope", label: "Hiện", control: scopeSelect }),
        board.bar.node,
        board.filterStatus,
        readModelNotice(),
        board.truncation,
        board.host,
      ),
    }),
    panel({
      eyebrow: "Lệnh",
      title: "Chuyển trạng thái đơn",
      guardrail:
        "Một đơn có ba chiều chạy độc lập: thương mại, nhận đồ, sản xuất. Chọn chiều trước, rồi " +
        "chọn đích. Màn hình này cố ý không biết bước nào là hợp lệ — quy tắc đó thuộc về máy " +
        "chủ, và chép nó sang trình duyệt là tạo bản thứ hai không ai giữ cho khớp được. Mọi " +
        "đích của chiều đang chọn đều được chào; máy chủ từ chối cái nào không hợp lệ và nói rõ " +
        "vướng ở đâu.",
      children: h("div", { class: "stack" }, moveBody, moveResultHost),
    }),
    panel({
      eyebrow: "Lệnh · POST /internal/v1/orders/{id}/delivery-legs",
      title: "Ghi nhận chuyến giao",
      guardrail:
        "Khách đã trả đủ tại quầy trước khi đồ rời tiệm, nên ghi nhận ở đây không có tiền — chỉ " +
        "ghi đồ đã đến tay khách hay chưa. Giao hụt thì ghi thất bại, không tính thêm phí, và " +
        "lần giao sau là một dòng mới. Chỉ chuyến TRẢ ĐỒ thành công mới cho phép đóng đơn.",
      // Gated like the other two forms on this screen. Driving the console as an AUDITOR caught
      // that the submit was disabled here while the order id and both selects stayed live -- the
      // same half-measure `gated()` alone leaves everywhere, and the third form is where it is
      // easiest to miss because it is built once rather than re-rendered.
      children: h("div", { class: "stack" }, gatedFields(legBody, writeVerdict), legResultHost),
    }),
    createPanel,
  );
}

export const screen = {
  path: "/orders",
  title: "Đơn hàng",
  capability: "ORDERS_READ",
  needsStore: true,
  render: render_,
};
