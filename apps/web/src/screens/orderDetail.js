/**
 * One order: the ticket it is tracked by, what the customer owes, its four states, and
 * everything the audit log recorded against its identifier.
 *
 *   - **The order is read by its id** (`GET /internal/v1/orders/{id}`), whatever its age and
 *     whichever of the caller's stores it belongs to. Until ORDER-LOOKUP-001 this screen fetched the
 *     newest hundred rows of the selected store and searched them in the browser, so an order from
 *     four days ago could not be opened at the counter at all. The server answers a missing order
 *     and another store's order with the same 404, so "không tìm thấy" is all this screen can say
 *     about either -- and all it should.
 *   - **The amount is the server's.** `payable_total_vnd` is read from the quote revision the order
 *     is bound to -- the same stored figure the settlement checks a payment against. It is shown
 *     beside the payment field so the operator reads it to the customer, and the field is still
 *     typed rather than prefilled (see `settlementPanel`).
 *   - **The timeline is a Shadow surface wearing an order's clothes.** `GET …/shadow/audit/{id}` is
 *     the only audit read in the API, and the repository gates it on `SHADOW_READ` regardless of
 *     what the caller thinks they are looking at. An `AUDITOR` or an `OPERATOR` reaches it; a role
 *     that does not hold it is told so here rather than shown an empty list it would misread as
 *     "nothing ever happened".
 *   - **An empty timeline is genuinely ambiguous.** The server answers an unknown aggregate with
 *     `200 []`, not a `404`. So "no rows" means "no recorded events for this identifier" and
 *     nothing more — not that the order is new, not that the order is absent. The empty state says
 *     the ambiguous thing because the ambiguous thing is what is true.
 *   - **Nothing is re-sorted and nothing is totalled.** The server orders by `(occurred_at, id)`,
 *     oldest first, and that order is preserved; the one amount on this screen is displayed, never
 *     computed.
 *
 * @module screens/orderDetail
 */

import { Submission, isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, UUID, count, dateTime, money, parseDong, shortId } from "../core/format.js";
import { enumLabel, enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { navigate } from "../core/router.js";
import {
  badge,
  copyable,
  dimensionBadge,
  empty,
  errorNotice,
  facts,
  gated,
  labelled,
  panel,
  resultLine,
  setResult,
  skeleton,
} from "../ui/components.js";
// A screen importing a screen, deliberately: this is the WS6 order-detail-to-incidents
// carry-over channel (in-memory module state, cleared on consumption), not a shared helper.
import { setIncidentOrderPrefill } from "./incidents.js";
// The same two helpers the board uses, so a card and this screen cannot word the ticket or the
// amount differently.
import { amountDue, ticketLabel } from "./orders.js";

/**
 * The server's fixed audit page size.
 *
 * `ShadowConsoleRepository.audit_timeline` takes a `limit` with a default of 100, and the route
 * does not expose it. So this is not a request the console makes; it is a ceiling the console can
 * only disclose.
 */
const AUDIT_LIMIT = 100;

/**
 * `SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION`: paid in advance at the counter, to be
 * collected there later -- a walk-in at drop-off (`DEC-032`), or a `PICKUP_ONLY` customer who came
 * by before the laundry was finished (its addendum).
 */
const PREPAID_SELF_COLLECTION = "EXACT_PAYMENT_PREPAID_SELF_COLLECTION";

/**
 * The fulfilment modes whose customer collects at the counter: every mode outside the server's
 * `MODES_EXPECTING_RETURN` (packages/domain/.../catalog.py). `PICKUP_ONLY` is one -- the courier
 * fetched the laundry, and the customer comes in for it. The server decides; this only chooses
 * which button to offer, and it offered none to a `PICKUP_ONLY` customer who had paid in advance.
 */
const SELF_COLLECT_MODES = new Set(["SELF_DROP_SELF_COLLECT", "PICKUP_ONLY"]);

/**
 * One audit row.
 *
 * @param {any} entry an `AuditEntryResponse`
 * @returns {HTMLElement}
 */
function auditRow(entry) {
  return h(
    "li",
    { class: "card" },
    facts([
      ["Thời điểm", dateTime(entry.occurred_at)],
      ["Hành động", enumLabel(entry.action), { mono: true }],
      [
        "Tác nhân",
        h(
          "span",
          { class: "row" },
          badge({ token: enumLabel(entry.actor_type), gloss: "", state: "neutral" }),
          h(
            "span",
            { class: "mono", title: entry.actor_id || "" },
            // `null` here means the server recorded no staff user for this row — the outbox worker
            // and the bootstrap have no person behind them. It is shown as unknown, never as a
            // blank that reads like a missing field.
            shortId(entry.actor_id),
          ),
        ),
        { span: true },
      ],
      ["Đối tượng", enumLabel(entry.aggregate_type), { mono: true }],
    ]),
  );
}

/**
 * @param {any[]} entries
 * @returns {HTMLElement}
 */
function auditTimeline(entries) {
  if (!entries.length) {
    return empty(
      "Không có sự kiện nào được ghi cho định danh này. Máy chủ trả mảng rỗng cả khi định danh " +
        "không tồn tại lẫn khi đơn chưa phát sinh sự kiện — hai trường hợp đó không phân biệt " +
        "được từ đây.",
    );
  }
  return h("ol", { class: "stack" }, entries.map(auditRow));
}

/**
 * @param {import("../core/router.js").RouteContext} context
 * @returns {HTMLElement}
 */
/**
 * Tất toán đơn.
 *
 * SETTLEMENT-001. Until this existed no order could reach COMPLETED: the balance was hardcoded
 * UNPAID at insert and nothing could move it, so the completion guard was correct and
 * unsatisfiable.
 *
 * The form asks for the amount rather than offering to fill it in. The server compares what is
 * typed against the immutable quote the order is bound to, and a pre-filled figure a staff member
 * confirms without reading is how a wrong amount gets attested — the point of the comparison is
 * that two independent sources agree. What changed is that the figure to read to the customer is
 * now on screen beside the field (`spec.dueHost`, filled once the order is read); before, the
 * form demanded an exact total that no order screen showed.
 *
 * Every supported shape is the exact total in one payment; they differ in when it is paid and
 * where the laundry goes. Anything else is refused with the decision that owns it, and the
 * refusal is shown verbatim rather than translated into "try again".
 *
 * @param {object} spec
 * @param {string} spec.orderId
 * @param {import("../core/rbac.js").Verdict} spec.verdict
 * @param {HTMLElement} spec.dueHost where the amount to collect is shown, next to the field
 * @param {() => void} spec.onRecorded
 * @returns {HTMLElement}
 */
function settlementPanel(spec) {
  const draft = { amount: "", collected: false };
  const result = resultLine();
  const errorHost = h("div");
  // Minted on first use, retired on any edit and after a commit: a corrected amount is a new
  // intent, and replaying the old key with new content is a 409, not a replay. A fixed key would
  // leave an operator whose first attempt the server recorded with no way forward at all.
  const submission = new Submission("settlement");
  // Which of the two presses the current key belongs to. `DEC-032` added the second one, and the
  // two send different bodies, so switching between them is a new intent and needs a new key.
  /** @type {boolean|null} */
  let keyIntent = null;

  /** @param {SubmitEvent} event */
  async function submit(event) {
    event.preventDefault();
    await send(draft.collected);
  }

  /**
   * `collected` is the one fact the two presses differ on: the checkbox for a customer paying at
   * pickup, or always `false` for "Khách trả trước khi gửi đồ" (`DEC-032`) -- paying now, taking
   * the laundry later, with the handover recorded at pickup rather than ticked in advance.
   *
   * @param {boolean} collected
   */
  async function send(collected) {
    if (keyIntent !== null && keyIntent !== collected) submission.reset();
    keyIntent = collected;
    // `parseDong` rather than `parseInt`: the total on this very screen renders as "132.000 ₫",
    // and `parseInt("132.000", 10)` is 132 -- a safe integer, so it posted, and the server refused
    // it for not equalling the quoted total. The operator had copied the number the application
    // showed them and got a refusal with no explanation available to them.
    const amount = parseDong(draft.amount);
    if (amount === null) {
      setResult(
        result,
        "danger",
        "Số tiền phải là số nguyên đồng. Chép cả dấu chấm cũng được — “132.000” đọc là 132000. " +
          "Không nhận dấu phẩy hay số lẻ.",
      );
      return;
    }
    setResult(result, "warn", "Đang ghi nhận…");
    render(errorHost);
    try {
      const recorded = await request(
        `/internal/v1/orders/${encodeURIComponent(spec.orderId)}/settlement`,
        {
          method: "POST",
          body: { paid_amount_vnd: amount, collected_by_customer: collected },
          idempotencyKey: submission.key(),
        },
      );
      submission.reset();
      keyIntent = null;
      setResult(
        result,
        "ok",
        `Đã ghi nhận ${money(recorded.paid_amount_vnd)} đúng bằng tổng đã báo. ` +
          "Công nợ chuyển sang PAID. " +
          // Read from the response, not asserted. Both prepayments leave
          // `self_collection_recorded` false on purpose -- the customer paid and nobody has
          // received anything yet -- and this line used to claim otherwise for every settlement.
          (recorded.self_collection_recorded
            ? "Đã ghi nhận khách tự lấy đồ."
            : recorded.settlement_shape === PREPAID_SELF_COLLECTION
              ? "Khách chưa nhận đồ. Khi đưa đồ cho khách, bấm “Khách đã nhận đồ”."
              : "Chưa ghi nhận giao đồ — cần một chặng giao thành công thì mới đóng được đơn."),
      );
      spec.onRecorded();
    } catch (error) {
      // A refusal names the open decision that owns it and is shown verbatim, never translated
      // into "thử lại" — a staff member told only "không được" goes and finds a workaround. The
      // key is kept so an unchanged resend replays; any edit mints a fresh one (see the inputs).
      setResult(result, "danger", error.message);
      render(errorHost, errorNotice(error));
    }
  }

  const amountInput = h("input", {
    type: "text",
    inputmode: "numeric",
    autocomplete: "off",
    placeholder: "Số tiền khách đưa",
    onInput: (event) => {
      draft.amount = /** @type {HTMLInputElement} */ (event.target).value;
      // Any edit is a new intent; the old key would be a 409 against the new payload.
      submission.reset();
    },
  });
  const collectedInput = h("input", {
    type: "checkbox",
    onChange: (event) => {
      draft.collected = /** @type {HTMLInputElement} */ (event.target).checked;
      submission.reset();
      keyIntent = null;
    },
  });

  return panel({
    eyebrow: "LỆNH · POST /internal/v1/orders/{id}/settlement",
    title: "Tất toán",
    guardrail:
      "Mọi trường hợp là cùng một khoản tiền: khách trả đúng tổng đã báo, đủ một lần, tại quầy — " +
      "lúc lấy đồ, lúc gửi đồ hoặc ghé quầy trả trước khi đồ xong (quyết định DEC-032), hoặc " +
      "trước khi tiệm giao tận nơi. Người giao không thu tiền. " +
      "Trả thiếu, trả thừa, đặt cọc, trả góp và ghi nợ đều bị từ chối kèm mã quyết định — không " +
      "làm tròn và không ghi nhận một phần. Bản ghi tất toán không sửa được.",
    children: h(
      "form",
      { class: "form", onSubmit: submit },
      spec.dueHost,
      labelled({
        id: "settlement-amount",
        label: "Số tiền khách đã trả (₫)",
        hint:
          "Nhập số khách đưa, không phải số hệ thống nghĩ. Máy chủ đối chiếu với ảnh chụp báo giá " +
          "gắn với đơn; lệch một đồng cũng bị từ chối.",
        control: amountInput,
      }),
      labelled({
        id: "settlement-collected",
        label: "Khách đã tự lấy đồ về",
        hint:
          "Chỉ tích khi khách trả tiền lúc lấy đồ và đồ đã giặt xong. Khách trả lúc gửi đồ — " +
          "hoặc khách của đơn tiệm tới lấy đồ ghé quầy trả trước khi đồ xong — thì bấm “Khách " +
          "trả trước khi gửi đồ”. Đơn giao tận nơi thì để trống. Không nhận tiền qua người giao.",
        control: collectedInput,
      }),
      h(
        "div",
        { class: "action-bar" },
        gated(
          h(
            "button",
            { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
            "Ghi nhận tất toán",
          ),
          spec.verdict,
        ),
        // `DEC-032`. Its own press rather than "leave the box unticked", so paying early is a
        // choice the operator makes on purpose and the box keeps meaning only one thing.
        gated(
          h(
            "button",
            {
              type: "button",
              dataVariant: "quiet",
              dataRequiresNetwork: "true",
              onClick: () => void send(false),
            },
            "Khách trả trước khi gửi đồ",
          ),
          spec.verdict,
        ),
      ),
      result,
      errorHost,
    ),
  });
}

/**
 * What stands where the settlement form was, once the order no longer owes anything.
 *
 * Only `UNPAID` is an order this form can act on. `PAID` is the common case; `REFUNDED` is a
 * cancelled order whose money went back; the other states in the column (`PARTIALLY_PAID`,
 * `OVERPAID`, `ON_ACCOUNT`) no command writes today (`DEC-010`), and a form offered on one of them
 * would be a guess about what the counter should do. Each says what it is and that nothing more
 * is taken here.
 *
 * @param {any} order an `OrderViewResponse`
 * @returns {HTMLElement}
 */
function settledPanel(order) {
  const paid = order.balance === "PAID";
  return panel({
    eyebrow: "Tất toán",
    title: paid ? "Đã thu đủ tiền" : `Công nợ: ${enumVi(order.balance)}`,
    children: h(
      "div",
      { class: "stack" },
      h(
        "p",
        null,
        paid
          ? "Đơn này đã được tất toán. Bản ghi tất toán không sửa được và không thu thêm lần nữa."
          : "Đơn này không ở trạng thái chưa thanh toán, nên màn hình này không thu tiền cho nó. " +
              "Có gì chưa rõ thì báo chủ tiệm.",
      ),
      paid && order.self_collection_recorded === false
        ? h(
            "p",
            { class: "hint" },
            SELF_COLLECT_MODES.has(order.fulfillment_mode)
              ? "Khách chưa nhận đồ. Khi đưa đồ cho khách, bấm “Khách đã nhận đồ” ở bên dưới."
              : // Not "the goods have not arrived": the order read carries no delivery-leg fact,
                // and the walk that found this had recorded the successful leg before the money.
                "Đơn giao tận nơi: đơn đóng được khi đã có một chặng giao thành công.",
          )
        : null,
    ),
  });
}

/**
 * Khách đã nhận đồ — the pickup of an order paid in advance at the counter. `DEC-032`.
 *
 * `POST /internal/v1/orders/{id}/collection` takes no body: the name is the session's, and the
 * precondition is the row version this screen last read (`If-Match`). The button is offered only
 * for the one case it exists for -- paid, not yet collected, collected at the counter (a walk-in,
 * or `PICKUP_ONLY` since the `DEC-032` addendum) -- because every other order reaches the customer
 * another way: a customer paying at pickup is recorded by the
 * settlement itself, and a delivery by its legs. The server re-checks all of it, including that
 * the laundry is finished, and its refusal is shown as it came.
 *
 * @param {object} spec
 * @param {string} spec.orderId
 * @param {import("../core/rbac.js").Verdict} spec.verdict
 * @param {() => void} spec.onRecorded
 * @returns {{node: HTMLElement, update: (order: any) => void}}
 */
function collectionPanel(spec) {
  const host = h("div", { class: "stack" });
  const result = resultLine();
  const errorHost = h("div");
  // One key per order version: the same press after a timeout replays, a press against a newer
  // read is a new intent (the server keys the payload on the row version it was sent).
  let submission = new Submission("collection");
  /** @type {number|null} */
  let keyVersion = null;

  /** @param {any} order */
  async function record(order) {
    if (keyVersion !== order.row_version) {
      submission = new Submission("collection");
      keyVersion = order.row_version;
    }
    setResult(result, "warn", "Đang ghi nhận…");
    render(errorHost);
    try {
      await request(`/internal/v1/orders/${encodeURIComponent(spec.orderId)}/collection`, {
        method: "POST",
        ifMatch: order.row_version,
        idempotencyKey: submission.key(),
      });
      submission.reset();
      keyVersion = null;
      setResult(result, "ok", "Đã ghi nhận khách nhận đồ. Giờ chuyển đơn sang Hoàn tất.");
      spec.onRecorded();
    } catch (error) {
      // Not `error.message`: for a refusal that is the settlement vocabulary's headline, "Máy chủ
      // không ghi nhận khoản này", which reads as a payment problem to a staff member who pressed
      // "Khách đã nhận đồ". The reason code and what to do are in the notice below either way.
      setResult(
        result,
        "danger",
        error?.kind === "NOT_SUPPORTED" || error?.kind === "CONFLICT"
          ? "Chưa ghi nhận khách nhận đồ. Lý do và việc cần làm ở ngay bên dưới."
          : error.message,
      );
      render(errorHost, errorNotice(error));
    }
  }

  /** @param {any} order an `OrderViewResponse`, or null when it could not be read */
  function update(order) {
    if (!order) {
      render(host);
      return;
    }
    const waiting =
      order.balance === "PAID" &&
      order.self_collection_recorded === false &&
      SELF_COLLECT_MODES.has(order.fulfillment_mode);
    if (!waiting) {
      render(
        host,
        h(
          "p",
          { class: "hint" },
          order.self_collection_recorded
            ? "Đã ghi nhận khách nhận đồ."
            : "Chỉ dùng khi khách đã trả trước tại quầy và tới quầy lấy đồ.",
        ),
      );
      return;
    }
    render(
      host,
      h("p", null, "Khách đã trả trước. Khi đưa đồ cho khách, bấm nút dưới — tên bạn được ghi."),
      h(
        "div",
        { class: "action-bar" },
        gated(
          h(
            "button",
            {
              type: "button",
              dataVariant: "primary",
              dataRequiresNetwork: "true",
              onClick: () => void record(order),
            },
            "Khách đã nhận đồ",
          ),
          spec.verdict,
        ),
      ),
      result,
      errorHost,
    );
  }

  const node = panel({
    eyebrow: "LỆNH · POST /internal/v1/orders/{id}/collection",
    title: "Khách tới lấy đồ",
    children: host,
  });
  return { node, update };
}

export function render_(context) {
  const orderId = String(context?.params?.orderId || "").trim();
  const shadowVerdict = can(principal(), "SHADOW_READ");
  const settlementVerdict = can(principal(), "ORDERS_WRITE");
  const incidentVerdict = can(principal(), "INCIDENTS_WRITE");
  const wellFormed = UUID.test(orderId);

  const orderHost = h("div", null, skeleton(1));
  const timelineHost = h("div", null, skeleton(3));
  const timelineCount = h("span", { class: "count" }, "…");
  const timelineTruncation = h("p", { class: "hint" });
  const heading = h("h1", null, "Chi tiết đơn");
  const dueHost = h("div");
  // The settlement form is for an order that owes money. It used to stay live after the order
  // was PAID -- "Ghi nhận tất toán" and "Khách trả trước khi gửi đồ" both still pressable on a
  // walk-in who had just paid at drop-off, directly above the pickup button that is the only thing
  // left to press. The server refuses a second settlement (`ALREADY_SETTLED`), so no money was at
  // risk; the screen was offering a counter a way to take the money twice. Found by the real-API
  // browser walk of `DEC-032`, not by any test.
  const settlementForm = settlementPanel({
    orderId,
    verdict: settlementVerdict,
    dueHost,
    onRecorded: () => void loadOrder(),
  });
  const settlementHost = h("div", null, settlementForm);
  const collection = collectionPanel({
    orderId,
    verdict: settlementVerdict,
    onRecorded: () => void loadOrder(),
  });

  /**
   * Read this order by id. The server takes the store from the order itself, so an order of any
   * age in any of the caller's stores opens here, not only the selected store's newest hundred.
   *
   * @returns {Promise<any|null>} the order, or null when it could not be read
   */
  async function loadOrder() {
    render(orderHost, skeleton(1));
    try {
      const found = await request(`/internal/v1/orders/${encodeURIComponent(orderId)}`);
      collection.update(found);
      render(settlementHost, found.balance === "UNPAID" ? settlementForm : settledPanel(found));
      const ticket = ticketLabel(found);
      const due = amountDue(found);
      heading.textContent = ticket || "Chi tiết đơn";
      render(
        dueHost,
        due
          ? h(
              "p",
              { class: "row" },
              h("span", null, `${due.label}:`),
              h("strong", { class: due.known ? "money" : "" }, due.text),
              h("span", { class: "hint" }, `(${enumVi(found.balance).toLowerCase()})`),
            )
          : null,
      );
      render(
        orderHost,
        h(
          "div",
          { class: "stack" },
          facts([
            ["Số phiếu", ticket || "Không có — khách nhắn tin, không phát phiếu"],
            due ? [due.label, h("span", { class: due.known ? "money" : "" }, due.text)] : null,
            ["Thương mại", dimensionBadge(found.commercial)],
            ["Tiếp nhận", dimensionBadge(found.intake)],
            ["Sản xuất", dimensionBadge(found.production)],
            ["Công nợ", dimensionBadge(found.balance)],
            ["Giao nhận", enumVi(found.fulfillment_mode)],
            ["Tạo lúc", dateTime(found.created_at)],
            [
              "Báo giá",
              h(
                "span",
                { class: "mono", title: found.quote_id },
                `${shortId(found.quote_id)} · bản ${found.quote_revision}`,
              ),
            ],
            ["Mã đơn", copyable({ value: orderId }), { mono: true, span: true }],
            ["Phiên bản dòng", `v${found.row_version}`],
          ]),
          h(
            "p",
            { class: "hint" },
            "Bốn trục chuyển động độc lập với nhau; đừng đọc chúng như một chuỗi tuần tự.",
          ),
          h(
            "div",
            { class: "form__actions" },
            h(
              "a",
              { class: "button", href: `#/orders?order=${encodeURIComponent(orderId)}` },
              "Chuyển trạng thái đơn này",
            ),
          ),
        ),
      );
      return found;
    } catch (error) {
      render(dueHost);
      collection.update(null);
      if (error?.status === 404) {
        render(
          orderHost,
          h(
            "div",
            { class: "notice", dataState: "warn" },
            h("p", { class: "notice__title" }, "Không tìm thấy đơn này"),
            h(
              "p",
              null,
              "Mã đơn sai, hoặc đơn thuộc cửa hàng bạn không làm. Tìm lại bằng số phiếu ở màn " +
                "hình Đơn hàng.",
            ),
            h("p", { class: "hint mono" }, orderId),
          ),
        );
        return null;
      }
      render(orderHost, errorNotice(error, { onRetry: () => void start() }));
      return null;
    }
  }

  /** @param {string} store the order's own store, read off the order */
  async function loadTimeline(store) {
    // The repository gates this on SHADOW_READ even though the screen is an order screen. A role
    // without it gets the reason, not an empty list that would read as "nothing ever happened".
    if (!shadowVerdict.allowed) {
      timelineCount.textContent = "—";
      render(
        timelineHost,
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Vai trò này không đọc được dòng thời gian"),
          h("p", null, shadowVerdict.reason),
        ),
      );
      return;
    }

    render(timelineHost, skeleton(3));
    try {
      const entries = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/shadow/audit/${encodeURIComponent(orderId)}`,
      );
      const rows = Array.isArray(entries) ? entries : [];
      timelineCount.textContent = count(rows, AUDIT_LIMIT);
      timelineTruncation.textContent =
        isTruncated(rows, AUDIT_LIMIT)
          ? `Máy chủ cố định ${AUDIT_LIMIT} dòng và đã trả đủ; có thể còn nữa. Route này không ` +
            "nhận tham số limit và không có phân trang, nên phần còn lại chỉ đọc được ở cơ sở dữ liệu."
          : "";
      render(timelineHost, auditTimeline(rows));
    } catch (error) {
      timelineCount.textContent = "—";
      timelineTruncation.textContent = "";
      render(timelineHost, errorNotice(error, { onRetry: () => void loadTimeline(store) }));
    }
  }

  /**
   * The order first, then its timeline from the order's own store -- which need not be the store
   * selected in the header, since a staff member working two shops can open either's orders.
   */
  async function start() {
    const found = await loadOrder();
    if (found) {
      void loadTimeline(found.store_id || storeId());
      return;
    }
    timelineCount.textContent = "—";
    render(timelineHost, empty("Chưa đọc dòng thời gian vì chưa đọc được đơn."));
  }

  if (wellFormed) {
    void start();
  } else {
    // A malformed identifier would be a 422 from the server and a confusing one, because the
    // failure is in the address bar rather than in anything the operator typed on this screen.
    const malformed = h(
      "div",
      { class: "notice", dataState: "danger" },
      h("p", { class: "notice__title" }, "Địa chỉ này không chứa một mã đơn hợp lệ"),
      h("p", null, "Mã đơn phải là một UUID. Không có yêu cầu nào được gửi đi."),
      h("p", { class: "hint mono" }, orderId || UNKNOWN),
    );
    render(orderHost, malformed);
    timelineCount.textContent = "—";
    render(
      timelineHost,
      empty("Chưa đọc dòng thời gian vì mã đơn trong địa chỉ không hợp lệ."),
    );
  }

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Đơn hàng"),
      heading,
      h(
        "p",
        { class: "screen__lede" },
        h("span", { class: "mono" }, orderId || UNKNOWN),
      ),
      h("p", null, h("a", { href: "#/orders" }, "← Về bảng đơn")),
    ),
    panel({
      eyebrow: "Trạng thái",
      title: "Đơn này",
      children: h(
        "div",
        { class: "stack" },
        orderHost,
        // WS6 cross-link: hand the order id to the incident form through the incidents
        // module's in-memory slot and navigate there. Shown whenever the address holds a
        // well-formed id, and gated like any other write control, with the denial reason
        // visible.
        wellFormed
          ? h(
              "div",
              { class: "row" },
              gated(
                h(
                  "button",
                  {
                    type: "button",
                    dataVariant: "quiet",
                    onClick: () => {
                      setIncidentOrderPrefill(orderId);
                      navigate("/incidents");
                    },
                  },
                  "Mở sự cố cho đơn này",
                ),
                incidentVerdict,
              ),
              // REMEDY-001's second entry point, and a link rather than a button on purpose. A
              // remedy answers one incident and is keyed by `incident_id`, not by `order_id`;
              // there is no route that lists an order's incidents, so this screen cannot pick
              // the incident for the operator. Sending them to the list they can read, with the
              // reason, beats a button that would have to guess which complaint it answers.
              h("a", { href: "#/remedies" }, "Bồi hoàn cho một sự cố của đơn này"),
            )
          : null,
        wellFormed
          ? h(
              "p",
              { class: "hint" },
              "Bồi hoàn gắn với sự cố, không gắn thẳng với đơn: mở màn hình Sự cố, tìm đúng lời " +
                "khách phàn nàn, rồi bấm “Đề xuất bồi hoàn” trên chính dòng đó. API này không có " +
                "đường nào liệt kê sự cố của riêng một đơn, nên màn hình này không chọn hộ được.",
            )
          : null,
      ),
    }),
    settlementHost,
    collection.node,
    panel({
      eyebrow: "Kiểm toán",
      title: "Dòng thời gian",
      count: timelineCount,
      guardrail:
        "Đây là bản ghi kiểm toán theo định danh, không phải lịch sử đơn hàng được biên tập. Thứ " +
        "tự do máy chủ quyết định (occurred_at, id) — cũ nhất trước — và màn hình này không sắp " +
        "xếp lại. Định danh không tồn tại cũng trả về danh sách rỗng chứ không phải lỗi 404.",
      children: h(
        "div",
        { class: "stack" },
        h(
          "p",
          { class: "hint" },
          "Tác nhân không phải STAFF — OUTBOX_WORKER, AGENT_RUNNER, BOOTSTRAP — không gắn với " +
            "người dùng nào, nên cột định danh người thực hiện hiện dấu “—”. Đó là “không có " +
            "người nào”, không phải “thiếu dữ liệu”.",
        ),
        timelineTruncation,
        timelineHost,
      ),
    }),
  );
}

export const screen = {
  path: "/orders/:orderId",
  title: "Chi tiết đơn",
  capability: "ORDERS_READ",
  needsStore: true,
  render: render_,
};
