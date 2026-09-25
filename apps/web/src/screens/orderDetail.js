/**
 * Chi tiết đơn: one order — its ticket, where it is in its life, the money, and the one next thing
 * to do. Rebuilt on the V2 kit (`CONSOLE-REDESIGN-002`, spec V2 §5.4).
 *
 *   - **The next step is the server's.** `next_steps` on the order read (ORDER-STEPS-001) is
 *     computed by dry-running the domain state machines and settlement rules against the order's
 *     stored facts; exactly one entry is `primary`. This screen draws that one as the big button
 *     and the rest under "Thao tác khác". It holds no transition table and never filters or adds a
 *     step (`ENGINEERING_SPEC_V1.md:530`). A step the console does not know renders under its raw
 *     token rather than being dropped.
 *   - **Composite steps go to `POST /orders/{id}/steps`** with the row version read (`If-Match`)
 *     and an `Idempotency-Key` held for exactly one intent: the same press after a timeout replays,
 *     a different step, version or answer is a new key, and nothing is ever re-sent by software.
 *     Money and legs keep their own routes -- settlement, collection, delivery legs -- because they
 *     carry what only a person can attest: the amount taken, the courier's outcome.
 *   - **`RECEIVE` needs the operator's word on capacity.** The server derives five of the six
 *     readiness facts itself; `slot_approved` is the one it cannot know. The confirmation sheet asks
 *     for it as an explicit tick — the button alone never asserts it.
 *   - **The amount is typed, not prefilled.** The settlement compares what is typed against the
 *     immutable quote the order is bound to; a pre-filled figure a person confirms without reading is
 *     how a wrong amount gets attested, and the point of the comparison is that two independent
 *     sources agree (SETTLEMENT-001). The figure to read to the customer is on screen, large.
 *   - **Every write ends in one of two ways**: a toast and the order re-read and re-drawn; or the
 *     refusal inline at the button, in Vietnamese, with the way out. A stale version is never
 *     retried -- the offer is "Đơn vừa đổi — tải lại".
 *   - **The order is read by its id** (`GET /internal/v1/orders/{id}`), whatever its age and
 *     whichever of the caller's stores it belongs to; the side reads (incidents, credits, timeline)
 *     use the store read off the order, not the store selected in the header.
 *   - **The timeline is a Shadow surface wearing an order's clothes.** `GET …/shadow/audit/{id}` is
 *     the only audit read in the API and the repository gates it on `SHADOW_READ`; a role without it
 *     is told so rather than shown an empty list it would misread as "nothing ever happened". The
 *     server orders it `(occurred_at, id)`, oldest first; the compact view shows the latest five in
 *     that same order and nothing is re-sorted.
 *
 * @module screens/orderDetail
 */

import { Submission, isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, UUID, count, dateTime, money, parseDong } from "../core/format.js";
import {
  ACQUISITION_SOURCE_VI,
  ORDER_STEP_DONE_VI,
  enumLabel,
  enumVi,
  stepVi,
} from "../core/i18n.js";
import { modeBadge, orderProgress, orderStatus } from "../core/orderStatus.js";
import { can } from "../core/rbac.js";
import { navigate } from "../core/router.js";
import { principal, storeId } from "../core/session.js";
import {
  copyable,
  empty,
  errorNotice,
  gated,
  icon,
} from "../ui/components.js";
import {
  actionBar,
  button,
  choiceChips,
  confirmButton,
  infoButton,
  inlineAlert,
  keyValues,
  list,
  listRow,
  moneyHero,
  moneyInput,
  page,
  progress,
  section,
  sheet,
  show,
  skeletonRows,
  statusPill,
  techDetails,
  toast,
} from "../ui/kit.js";
// A screen importing a screen, deliberately: this is the WS6 order-detail-to-incidents
// carry-over channel (in-memory module state, cleared on consumption), not a shared helper.
import { setIncidentOrderPrefill } from "./incidents.js";
// The same helpers the list uses, so a row and this page cannot word the ticket or the amount
// differently.
import { amountDue, orderName, ticketLabel } from "./orders.js";

/**
 * The server's fixed audit page size. `ShadowConsoleRepository.audit_timeline` defaults to 100 and
 * the route does not expose it, so this is a ceiling the console can only disclose.
 */
const AUDIT_LIMIT = 100;

/** The four axes of an order, named as the history reads them. Presentation only. */
const AXIS_VI = {
  commercial: "Đơn",
  intake: "Tiếp nhận",
  production: "Sản xuất",
  balance: "Tiền",
};

/** How many timeline rows the compact view shows before "Xem tất cả". */
const AUDIT_COMPACT = 5;

/** The page size asked of the order's incident list. */
const INCIDENT_LIMIT = 50;

/**
 * `SettlementShape.EXACT_PAYMENT_PREPAID_SELF_COLLECTION`: paid in advance at the counter, to be
 * collected there later -- a walk-in at drop-off (`DEC-032`), or a `PICKUP_ONLY` customer who came
 * by before the laundry was finished (its addendum).
 */
const PREPAID_SELF_COLLECTION = "EXACT_PAYMENT_PREPAID_SELF_COLLECTION";

/**
 * The modes whose customer collects at the counter: every mode outside the server's
 * `MODES_EXPECTING_RETURN`. Used only for wording ("Khách chưa nhận đồ"); which button is offered
 * is `next_steps`.
 */
const SELF_COLLECT_MODES = new Set(["SELF_DROP_SELF_COLLECT", "PICKUP_ONLY"]);

/** Steps executed by `POST /orders/{id}/steps` (the server's `COMPOSITE_STEPS`). */
const COMPOSITE = new Set([
  "RECEIVE",
  "START_WASH",
  "QUALITY_CHECK",
  "MARK_READY",
  "HOLD",
  "RESUME",
  "RELEASE",
  "HAND_OVER",
  "COMPLETE",
  "CANCEL",
  "REOPEN",
]);

/** Steps that stop or end work: red, and never one tap. */
const DESTRUCTIVE = new Set(["CANCEL", "HOLD"]);

/** Steps that end the visit; offered straight away in a sheet's success state when primary. */
const CLOSING = new Set(["HAND_OVER", "COMPLETE"]);

/**
 * `SETTLEMENT-001` / `DEC-010` / `DEC-032`: the one shape of payment the counter takes. A policy
 * statement bound to the decision register (`console-disclosures-v1.yaml`, POLICY_BOUND); its text
 * is the slot and must not be reworded without the decision behind it. Shown behind ⓘ in the
 * payment sheet, with its point-of-action summary visible beside the field.
 */
const SETTLEMENT_RULE = {
  guardrail:
    "Mọi trường hợp là cùng một khoản tiền: khách trả đúng tổng đã báo, đủ một lần, tại quầy — " +
    "lúc lấy đồ, lúc gửi đồ hoặc ghé quầy trả trước khi đồ xong (quyết định DEC-032), hoặc " +
    "trước khi tiệm giao tận nơi. Người giao không thu tiền. " +
    "Trả thiếu, trả thừa, đặt cọc, trả góp và ghi nợ đều bị từ chối kèm mã quyết định — không " +
    "làm tròn và không ghi nhận một phần. Bản ghi tất toán không sửa được.",
};

/** How the audit timeline is to be read (was the V1 panel's guardrail). */
const AUDIT_NOTE = {
  guardrail:
    "Đây là bản ghi kiểm toán theo định danh, không phải lịch sử đơn hàng được biên tập. Thứ " +
    "tự do máy chủ quyết định (occurred_at, id) — cũ nhất trước — và màn hình này không sắp " +
    "xếp lại. Định danh không tồn tại cũng trả về danh sách rỗng chứ không phải lỗi 404.",
};

/**
 * FULFILMENT-001 / DEC-023: what a delivery leg records (moved here from the removed leg form on
 * `#/orders`, with the capability).
 */
const LEG_RULE = {
  guardrail:
    "Khách đã trả đủ tại quầy trước khi đồ rời tiệm, nên ghi nhận ở đây không có tiền — chỉ " +
    "ghi đồ đã đến tay khách hay chưa. Giao hụt thì ghi thất bại, không tính thêm phí, và " +
    "lần giao sau là một dòng mới. Chỉ chuyến TRẢ ĐỒ thành công mới cho phép đóng đơn.",
};

/** `DeliveryLegKind` / leg outcome in the counter's words (scoped, as `ACQUISITION_SOURCE_VI` is). */
const LEG_KIND_VI = { PICKUP: "Lấy đồ tại nhà khách", RETURN: "Giao đồ cho khách" };
const LEG_OUTCOME_VI = { SUCCEEDED: "Thành công", FAILED: "Không thành công" };

/** `RemedyKind` in the counter's words, for the credit rows below. */
const CREDIT_KIND_LABEL = {
  DAMAGE_COMPENSATION: "Bồi thường món bị hỏng",
  LATE_DELIVERY_CREDIT: "Giảm trừ do giao trễ",
  LOST_ITEM: "Mất đồ",
};

/**
 * The recorded acquisition source, glossed through the scoped map `orders.js` writes it with. The
 * token stays in `title` (spec V2 §4.1) and in the technical drawer.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
function acquisitionSourceText(value) {
  if (!value) return UNKNOWN;
  const gloss = ACQUISITION_SOURCE_VI[value];
  if (!gloss) return String(value);
  return `${gloss.charAt(0).toUpperCase()}${gloss.slice(1)}`;
}

/**
 * One remedy credit the order issued.
 *
 * The copy control is offered only for an unused code: a spent one cannot be applied again, and a
 * copy button beside it would invite the counter to try.
 *
 * @param {any} credit an `OrderRemedyCreditResponse`
 * @returns {HTMLElement}
 */
function creditItem(credit) {
  const id = String(credit.credit_id || "");
  const unused = credit.status === "UNUSED";
  return h(
    "li",
    { class: "rows__item credit-row", dataCreditId: id, dataCreditStatus: String(credit.status) },
    h(
      "div",
      { class: "credit-row__head" },
      h("strong", { class: "money" }, money(credit.amount_vnd)),
      statusPill(
        unused
          ? { state: "ok", text: "Chưa dùng", token: "UNUSED" }
          : { state: "neutral", text: "Đã dùng", token: String(credit.status || UNKNOWN) },
      ),
    ),
    h(
      "p",
      { class: "credit-row__meta" },
      `${CREDIT_KIND_LABEL[credit.kind] || String(credit.kind || UNKNOWN)} · ` +
        `phát hành ${dateTime(credit.issued_at)}` +
        (unused ? "" : ` · dùng ${dateTime(credit.redeemed_at)}`),
    ),
    unused
      ? h("div", { class: "credit-row__code" }, copyable({ value: id }))
      : credit.redeemed_quote_id
        ? h(
            "a",
            {
              href:
                `#/quotes?quote=${encodeURIComponent(String(credit.redeemed_quote_id))}` +
                `&revision=${encodeURIComponent(String(credit.redeemed_quote_revision))}`,
            },
            `Đã trừ vào báo giá, bản ${credit.redeemed_quote_revision}`,
          )
        : null,
  );
}

/**
 * @param {import("../core/router.js").RouteContext} context
 * @returns {HTMLElement}
 */
export function render_(context) {
  const orderId = String(context?.params?.orderId || "").trim();
  const me = principal();
  const shadowVerdict = can(me, "SHADOW_READ");
  const writeVerdict = can(me, "ORDERS_WRITE");
  const incidentVerdict = can(me, "INCIDENTS_WRITE");
  // The credit and incident reads use the remedy surface's gate: an operations role with MFA.
  const readIncidentsVerdict = can(me, "INCIDENTS_READ");
  const wellFormed = UUID.test(orderId);
  // Every route below is written out in full: the contract test reads path literals, and a path
  // assembled from a variable is one it cannot see.
  const id = encodeURIComponent(orderId);

  const headHost = h("div", null, page({ back: { href: "#/orders", label: "Đơn hàng" }, title: "Chi tiết đơn" }));
  const summaryHost = h("div", { class: "stack" }, skeletonRows(2));
  const infoHost = h("div");
  const legsHost = h("div");
  const techHost = h("div");
  // `display: contents`, so the sticky bar inside sticks to the screen, not to this wrapper.
  const actionHost = h("div", { class: "order__actions" });
  const incidentsHost = h("div", { class: "stack stack--tight" }, skeletonRows(1));
  const creditHost = h("div", { id: "order-remedy-credits" }, skeletonRows(1));
  const timelineHost = h("div", { class: "stack stack--tight" }, skeletonRows(2));
  const timelineTruncation = h("p", { class: "hint" });
  // Sheets live inside the screen, so leaving the screen takes them with it (no stray dialogs on
  // `document.body`, no duplicate field ids on the next order).
  const sheetsHost = h("div", { class: "order__sheets" });

  /** @type {any|null} the order as last read */
  let current = null;

  // --- idempotency: one key per intent --------------------------------------------------------

  /** @type {{intent: string, submission: Submission}|null} */
  let held = null;
  /**
   * The key for one intent. The same intent (same step, same version, same answer) keeps its key so
   * a resend after a timeout replays; any change of intent mints a new one.
   *
   * @param {string} intent
   * @returns {string}
   */
  function keyFor(intent) {
    if (!held || held.intent !== intent) held = { intent, submission: new Submission("order-step") };
    return held.submission.key();
  }
  /** After a write the server confirmed, the next press is a new intent. */
  function releaseKey() {
    held = null;
  }

  // --- reads ----------------------------------------------------------------------------------

  /** @returns {Promise<any|null>} */
  async function loadOrder() {
    try {
      const found = await request(`/internal/v1/orders/${id}`);
      current = found;
      draw(found);
      return found;
    } catch (error) {
      current = null;
      render(actionHost);
      render(summaryHost);
      if (error?.status === 404) {
        render(
          headHost,
          page({ back: { href: "#/orders", label: "Đơn hàng" }, title: "Không tìm thấy đơn" }),
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
          ),
        );
        return null;
      }
      show(summaryHost, errorNotice(error, { onRetry: () => void start() }));
      return null;
    }
  }

  /** Re-read after a write; the side lists that a step can change are re-read too. */
  async function reread() {
    const found = await loadOrder();
    if (found) void loadTimeline(found.store_id || storeId());
    return found;
  }

  // --- drawing --------------------------------------------------------------------------------

  /** @param {any} order */
  function draw(order) {
    const status = orderStatus(order);
    const mode = modeBadge(order.fulfillment_mode);
    render(
      headHost,
      page({
        back: { href: "#/orders", label: "Đơn hàng" },
        title: orderName(order),
        subtitle: h(
          "span",
          { class: "order__subtitle" },
          statusPill({ state: status.state, text: status.text, token: status.token }),
          h("span", { class: "order__mode", title: order.fulfillment_mode }, icon(mode.icon), mode.text),
          h("span", null, `Tạo ${dateTime(order.created_at)}`),
        ),
      }),
    );
    const steps = orderProgress(order);
    render(
      summaryHost,
      steps ? progress(steps, { label: "Tiến trình đơn" }) : null,
      moneyCard(order),
    );
    render(infoHost, infoSection(order));
    render(legsHost, legsSection(order));
    render(techHost, technical(order));
    drawActions(order);
  }

  /** @param {any} order */
  function moneyCard(order) {
    const due = amountDue(order);
    const paid = order.balance === "PAID";
    const waiting =
      paid &&
      order.self_collection_recorded === false &&
      SELF_COLLECT_MODES.has(order.fulfillment_mode) &&
      order.commercial === "ACTIVE";
    const cancelled = order.commercial === "CANCELLED";
    const label =
      order.balance === "UNPAID"
        ? cancelled
          ? "Tổng đơn"
          : "Phải thu"
        : paid
          ? "Đã thu"
          : order.balance === "REFUNDED"
            ? "Tổng đơn · đã hoàn"
            : "Tổng đơn";
    const caption = paid
      ? `Đã thu đủ tiền.${waiting ? " Khách chưa nhận đồ." : ""}`
      : order.balance === "REFUNDED"
        ? "Đã hoàn tiền cho khách."
        : order.balance === "UNPAID"
          ? cancelled
            ? "Đơn đã huỷ — không thu tiền."
            : due && !due.known
            ? "Báo giá chưa có một tổng duy nhất, nên chưa thu được."
            : null
          : `Công nợ: ${enumVi(order.balance)}. Quầy không thu tiền cho đơn này; có gì chưa rõ ` +
            "thì báo chủ tiệm.";
    return h(
      "div",
      { class: "surface order__money" },
      moneyHero({
        label,
        amount: due ? due.text : UNKNOWN,
        caption,
        state: paid ? "ok" : null,
      }),
    );
  }

  /** @param {any} order */
  function infoSection(order) {
    const ticket = ticketLabel(order);
    return section({
      title: "Thông tin",
      children: keyValues([
        // The ticket with its business day (numbers restart every morning); the header says
        // "Phiếu 17" only.
        ["Phiếu", ticket || "Không có — khách nhắn tin, không phát phiếu"],
        ["Giao nhận", enumVi(order.fulfillment_mode)],
        [
          "Nguồn khách",
          h(
            "span",
            { class: "order__source" },
            h(
              "span",
              { dataField: "acquisition-source", title: String(order.acquisition_source || "") },
              acquisitionSourceText(order.acquisition_source),
            ),
            infoButton(
              "Sửa được nguồn khách không?",
              h(
                "p",
                { class: "hint" },
                "Nguồn khách là điều nhân viên ghi lúc tạo đơn. Chỉ để xem lại: ghi rồi thì không sửa " +
                  "được, kể cả khi thấy bấm nhầm.",
              ),
            ),
          ),
        ],
        [
          "Báo giá",
          h(
            "a",
            {
              href:
                `#/quotes?quote=${encodeURIComponent(String(order.quote_id))}` +
                `&revision=${encodeURIComponent(String(order.quote_revision))}`,
            },
            `Xem báo giá (bản ${order.quote_revision})`,
          ),
        ],
      ]),
    });
  }

  /** @param {any} order */
  function legsSection(order) {
    const legs = Array.isArray(order.delivery_legs) ? order.delivery_legs : [];
    if (order.fulfillment_mode === "SELF_DROP_SELF_COLLECT" && !legs.length) return null;
    return section({
      title: "Giao nhận",
      card: false,
      info: infoButton("Chuyến giao ghi những gì?", h("p", null, LEG_RULE.guardrail)),
      children: legs.length
        ? list(
            legs.map((leg) =>
              listRow({
                leading: "truck",
                title: LEG_KIND_VI[leg.leg_kind] || String(leg.leg_kind),
                meta: dateTime(leg.recorded_at),
                trailing: statusPill({
                  state: leg.outcome === "SUCCEEDED" ? "ok" : "danger",
                  text: LEG_OUTCOME_VI[leg.outcome] || String(leg.outcome),
                  token: String(leg.outcome),
                }),
              }),
            ),
            { label: "Các chuyến giao nhận" },
          )
        : h("p", { class: "surface muted order__empty" }, "Chưa có chuyến giao nhận nào."),
    });
  }

  /** @param {any} order */
  function technical(order) {
    const steps = Array.isArray(order.next_steps) ? order.next_steps : [];
    return techDetails([
      ["Mã đơn", order.order_id, { copy: String(order.order_id) }],
      ["Cửa hàng", order.store_id, { copy: String(order.store_id) }],
      ["Báo giá", `${order.quote_id} · bản ${order.quote_revision}`, { copy: String(order.quote_id) }],
      ["Phiên bản dòng", `v${order.row_version}`],
      ["Thương mại", enumLabel(order.commercial)],
      ["Nhận đồ", enumLabel(order.intake)],
      ["Sản xuất", enumLabel(order.production)],
      ["Công nợ", enumLabel(order.balance)],
      ["Tất toán", order.settlement_shape || "—"],
      [
        "Bước máy chủ cho phép",
        steps.length
          ? steps.map((entry) => `${entry.step}${entry.primary ? " (chính)" : ""}`).join(", ")
          : "—",
      ],
      [
        "Đọc bốn trục thế nào",
        h(
          "p",
          { class: "hint" },
          "Bốn trục chuyển động độc lập với nhau; đừng đọc chúng như một chuỗi tuần tự.",
        ),
        { mono: false },
      ],
    ]);
  }

  // --- the next step --------------------------------------------------------------------------

  const actionAlert = h("div", { class: "order__action-alert" });

  /** @param {any} order */
  function drawActions(order) {
    render(actionAlert);
    const steps = Array.isArray(order.next_steps) ? order.next_steps : [];
    if (!steps.length) {
      render(
        actionHost,
        h(
          "p",
          { class: "muted order__closed" },
          ["COMPLETED", "CANCELLED"].includes(order.commercial)
            ? "Đơn đã đóng — không còn bước nào."
            : "Chưa có bước nào làm được lúc này.",
        ),
      );
      return;
    }
    const primary = steps.find((entry) => entry.primary) || steps[0];
    // Presentation only: the server's order, with the steps that stop or end work moved last so a
    // thumb reaching for "Khách trả trước" does not land on "Huỷ đơn".
    const rest = steps.filter((entry) => entry !== primary);
    const others = [
      ...rest.filter((entry) => !DESTRUCTIVE.has(String(entry.step))),
      ...rest.filter((entry) => DESTRUCTIVE.has(String(entry.step))),
    ];
    render(
      actionHost,
      actionBar(
        actionAlert,
        stepControl(primary, { primary: true }),
        others.length
          ? moreButton(others)
          : null,
      ),
    );
  }

  /** @param {any[]} others */
  function moreButton(others) {
    const node = button({
      label: "Khác",
      icon: "more",
      onClick: () => openMore(others),
      data: { moreSteps: String(others.length) },
    });
    node.setAttribute("aria-label", "Thao tác khác");
    node.title = "Thao tác khác";
    return node;
  }

  /**
   * The control for one step: a direct press for a plain composite step, a sheet for anything that
   * needs a person's word (the slot, the amount, the courier's outcome, the custody answer), and a
   * two-press for HOLD.
   *
   * @param {any} entry a `NextStepResponse`
   * @param {{primary?: boolean, inMore?: boolean}} [options]
   * @returns {HTMLElement}
   */
  function stepControl(entry, options = {}) {
    const step = String(entry.step);
    const label = stepVi(step);
    const variant = DESTRUCTIVE.has(step) ? "danger" : options.primary ? "primary" : "secondary";
    let control;
    if (step === "HOLD") {
      control = confirmButton({
        label,
        confirmLabel: "Bấm lần nữa để tạm dừng",
        block: true,
        onConfirm: () => {
          closeMore();
          void runComposite(entry, {}, actionAlert);
        },
      });
    } else {
      control = button({
        label,
        variant,
        block: true,
        network: true,
        data: { step },
        onClick: (event) => {
          closeMore();
          const opener = /** @type {HTMLButtonElement} */ (event.currentTarget);
          if (step === "RECEIVE") openReceive(entry);
          else if (step === "SETTLE" || step === "PREPAY") openPayment(entry);
          else if (step === "COLLECT") openCollect();
          else if (step === "DELIVERY_PICKUP" || step === "DELIVERY_RETURN") openLeg(entry);
          else if (step === "CANCEL") openCancel(entry);
          else if (COMPOSITE.has(step)) void runComposite(entry, {}, actionAlert, opener);
          else show(actionAlert, unknownStep(step));
        },
      });
    }
    control.dataset.step = step;
    return gated(control, writeVerdict);
  }

  /** @param {string} step */
  function unknownStep(step) {
    return inlineAlert({
      state: "warn",
      title: `Màn hình này chưa biết bước “${step}”.`,
      body: "Máy chủ cho phép bước này nhưng bảng điều khiển chưa có nút cho nó. Báo kỹ thuật.",
    });
  }

  // --- writes ---------------------------------------------------------------------------------

  /**
   * The refusal shown at the control. Stale and unknown outcomes get their own words because their
   * way out is different: reload, never press again.
   *
   * @param {any} error
   * @returns {HTMLElement}
   */
  function refusal(error) {
    const reload = button({
      label: "Đơn vừa đổi — tải lại",
      icon: "refresh",
      onClick: () => void reread(),
    });
    if (error?.kind === "STALE" || error?.kind === "PRECONDITION_REQUIRED") {
      return errorNotice(error, {
        title:
          "Đơn này vừa được người khác đổi trong lúc bạn đang xem, nên bước của bạn chưa được ghi. " +
          "Tải lại đơn rồi làm tiếp theo bước mới.",
        actions: [reload],
      });
    }
    if (error?.kind === "TIMEOUT" || error?.kind === "NETWORK") {
      return errorNotice(error, {
        title:
          "Chưa biết lệnh có tới máy chủ hay không. Đừng bấm lại — tải lại đơn để xem trạng thái thật.",
        actions: [
          button({ label: "Tải lại đơn", icon: "refresh", onClick: () => void reread() }),
        ],
      });
    }
    return errorNotice(error, {
      actions:
        error?.kind === "CONFLICT"
          ? [button({ label: "Tải lại đơn", icon: "refresh", onClick: () => void reread() })]
          : [],
    });
  }

  /**
   * Hold a button down while its request is in flight, so a second tap cannot send a second copy.
   *
   * @param {HTMLButtonElement|null|undefined} control
   * @param {() => Promise<void>} work
   */
  async function pressing(control, work) {
    if (control) {
      control.disabled = true;
      control.setAttribute("aria-busy", "true");
    }
    try {
      await work();
    } finally {
      if (control?.isConnected) {
        control.disabled = false;
        control.removeAttribute("aria-busy");
      }
    }
  }

  /**
   * One composite step on `POST /orders/{id}/steps`.
   *
   * @param {any} entry
   * @param {{slot_approved?: boolean, custody_resolution?: string}} extra
   * @param {HTMLElement} alertHost where a refusal is shown
   * @param {HTMLButtonElement} [control]
   * @returns {Promise<any|null>} the re-read order, or null when refused
   */
  async function runComposite(entry, extra, alertHost, control) {
    const order = current;
    if (!order) return null;
    const step = String(entry.step);
    const body = { step, ...extra };
    render(alertHost);
    let result = null;
    await pressing(control, async () => {
      try {
        await request(`/internal/v1/orders/${id}/steps`, {
          method: "POST",
          body,
          idempotencyKey: keyFor(`${step}|${order.row_version}|${JSON.stringify(extra)}`),
          ifMatch: order.row_version,
        });
        releaseKey();
        toast(`${ORDER_STEP_DONE_VI[step] || stepVi(step)} · ${orderName(order)}`);
        result = await reread();
      } catch (error) {
        show(alertHost, refusal(error));
      }
    });
    return result;
  }

  // --- sheets ---------------------------------------------------------------------------------

  /** @type {ReturnType<typeof sheet>|null} */
  let openSheet = null;

  /**
   * Open a sheet built for this press. Only one exists at a time; it lives in the screen.
   *
   * @param {{title: string, body: unknown, actions?: unknown, id?: string}} spec
   */
  function openFresh(spec) {
    openSheet?.close();
    const made = sheet({ ...spec, onClose: () => made.node.remove() });
    render(sheetsHost, made.node);
    openSheet = made;
    made.open();
    return made;
  }

  function closeMore() {
    if (openSheet && openSheet.node.id === "order-more") openSheet.close();
  }

  /** @param {any[]} others */
  function openMore(others) {
    openFresh({
      id: "order-more",
      title: "Thao tác khác",
      body: h(
        "div",
        { class: "btn-stack" },
        others.map((entry) => stepControl(entry, { inMore: true })),
      ),
    });
  }

  /**
   * After a step in a sheet landed: say so there, and when the server's next primary step ends the
   * visit (HAND_OVER / COMPLETE), offer it right away instead of sending the person back to the
   * page for one more tap.
   *
   * @param {ReturnType<typeof sheet>} made
   * @param {string} title
   * @param {unknown} body
   * @param {any|null} order the order re-read after the step
   */
  function successState(made, title, body, order) {
    const next = (order?.next_steps || []).find((entry) => entry.primary);
    const alertHost = h("div");
    const offer =
      next && CLOSING.has(String(next.step))
        ? gated(
            button({
              label: stepVi(String(next.step)),
              variant: "primary",
              block: true,
              network: true,
              data: { step: String(next.step) },
              onClick: async (event) => {
                const done = await runComposite(
                  next,
                  {},
                  alertHost,
                  /** @type {HTMLButtonElement} */ (event.currentTarget),
                );
                if (done) made.close();
              },
            }),
            writeVerdict,
          )
        : null;
    render(
      made.body,
      inlineAlert({ state: "ok", title, body }),
      alertHost,
      h(
        "div",
        { class: "btn-stack" },
        offer,
        button({ label: offer ? "Để sau" : "Xong", block: true, onClick: () => made.close() }),
      ),
    );
    const actions = made.node.querySelector(".sheet__actions");
    if (actions) actions.remove();
  }

  /** Nhận đồ: the one fact only the operator can give, asked for explicitly. */
  function openReceive(entry) {
    const alertHost = h("div");
    const confirm = button({
      label: stepVi("RECEIVE"),
      variant: "primary",
      block: true,
      network: true,
      disabled: true,
      id: "receive-submit",
      onClick: async () => {
        const done = await runComposite(entry, { slot_approved: tick.checked }, alertHost, confirm);
        if (done) made.close();
      },
    });
    const tick = /** @type {HTMLInputElement} */ (
      h("input", {
        type: "checkbox",
        id: "receive-slot",
        onChange: () => {
          // A denied role's button stays shut whatever is ticked (`gated` owns that).
          if (writeVerdict.allowed) confirm.disabled = !tick.checked;
        },
      })
    );
    const made = openFresh({
      id: "order-receive",
      title: "Nhận đồ",
      body: h(
        "div",
        { class: "stack" },
        h(
          "label",
          { class: "check-line", for: "receive-slot" },
          tick,
          h("span", null, "Tiệm làm kịp đơn này — đã hẹn giờ trả đồ với khách"),
        ),
        h(
          "p",
          { class: "hint" },
          "Máy không tự biết tiệm còn chỗ hay không; dấu tích này là lời của bạn, ghi kèm tên bạn.",
        ),
        alertHost,
      ),
      actions: gated(confirm, writeVerdict),
    });
  }

  /**
   * Thu tiền / Khách trả trước. The same exact total either way; they differ only in whether the
   * customer takes the laundry now (`collected_by_customer`).
   *
   * @param {any} entry
   */
  function openPayment(entry) {
    const order = current;
    if (!order) return;
    const collected = String(entry.step) === "SETTLE";
    const due = amountDue(order);
    const alertHost = h("div");
    let typed = "";
    const field = moneyInput({
      id: "settlement-amount",
      label: "Số tiền khách đưa",
      placeholder: "Gõ số tiền khách đưa",
      echo: (text) => {
        const parsed = parseDong(text);
        return text.trim() && parsed !== null ? `= ${money(parsed)}` : "";
      },
      onInput: (text) => {
        typed = text;
      },
    });
    const submit = button({
      label: collected ? "Ghi nhận đã thu tiền" : "Ghi nhận khách trả trước",
      variant: "primary",
      block: true,
      network: true,
      id: "settlement-submit",
      onClick: () => void send(),
    });

    async function send() {
      // `parseDong` rather than `parseInt`: the total on this very screen renders as "132.000 ₫",
      // and `parseInt("132.000", 10)` is 132.
      const amount = parseDong(typed);
      if (amount === null) {
        show(
          alertHost,
          inlineAlert({
            state: "danger",
            title:
              "Số tiền phải là số nguyên đồng. Chép cả dấu chấm cũng được — “132.000” đọc là " +
              "132000. Không nhận dấu phẩy hay số lẻ.",
          }),
        );
        return;
      }
      render(alertHost);
      await pressing(submit, async () => {
        try {
          const recorded = await request(`/internal/v1/orders/${id}/settlement`, {
            method: "POST",
            body: { paid_amount_vnd: amount, collected_by_customer: collected },
            // A corrected amount is a new intent; replaying the old key with new content is a 409.
            idempotencyKey: keyFor(`settle|${collected}|${amount}`),
          });
          releaseKey();
          toast(`${collected ? "Đã thu tiền" : "Đã thu tiền trả trước"} · ${orderName(order)}`);
          const view = await reread();
          successState(
            made,
            `Đã ghi nhận ${money(recorded.paid_amount_vnd)} đúng bằng tổng đã báo.`,
            // Read from the response, not asserted: a prepayment leaves the handover unrecorded.
            recorded.self_collection_recorded
              ? "Đã ghi nhận khách tự lấy đồ."
              : recorded.settlement_shape === PREPAID_SELF_COLLECTION
                ? "Khách chưa nhận đồ. Khi đưa đồ cho khách, bấm “Khách đã nhận đồ”."
                : "Chưa ghi nhận giao đồ — cần một chuyến giao thành công thì mới đóng được đơn.",
            view,
          );
        } catch (error) {
          // A refusal names the decision that owns it and is shown as the server gave it, never
          // translated into "thử lại". The key is kept so an unchanged resend replays.
          show(alertHost, refusal(error));
        }
      });
    }

    const made = openFresh({
      id: "order-payment",
      title: collected ? "Thu tiền" : "Khách trả trước",
      body: h(
        "div",
        { class: "stack" },
        moneyHero({
          label: "Phải thu",
          amount: due ? due.text : UNKNOWN,
          caption: collected
            ? "Khách trả đủ và lấy đồ luôn."
            : "Khách trả đủ bây giờ, nhận đồ sau.",
        }),
        h(
          "div",
          { class: "stack stack--tight" },
          h(
            "div",
            { class: "fact-line" },
            h("label", { for: "settlement-amount", class: "field-label" }, "Số tiền khách đưa"),
            infoButton(
              "Vì sao phải tự gõ số tiền?",
              h(
                "p",
                null,
                "Nhập số khách đưa, không phải số hệ thống nghĩ. Máy chủ đối chiếu với ảnh chụp báo " +
                  "giá gắn với đơn; lệch một đồng cũng bị từ chối.",
              ),
              h(
                "p",
                null,
                "Ô này cố ý không điền sẵn: một con số điền sẵn mà người bấm không đọc là cách một " +
                  "khoản sai được ghi nhận. Hai nguồn độc lập — số bạn gõ và tổng đã báo — phải khớp " +
                  "nhau (SETTLEMENT-001).",
              ),
            ),
          ),
          field.node,
        ),
        h(
          "div",
          { class: "fact-line" },
          h("p", { class: "hint" }, "Thu đúng tổng, đủ một lần. Ghi rồi không sửa được."),
          infoButton("Quầy được thu những khoản nào?", h("p", null, SETTLEMENT_RULE.guardrail)),
        ),
        alertHost,
      ),
      actions: gated(submit, writeVerdict),
    });
    setTimeout(() => field.input.focus(), 50);
  }

  /** Khách đã nhận đồ: the pickup of an order paid in advance at the counter (`DEC-032`). */
  function openCollect() {
    const order = current;
    if (!order) return;
    const alertHost = h("div");
    const submit = button({
      label: stepVi("COLLECT"),
      variant: "primary",
      block: true,
      network: true,
      id: "collection-submit",
      onClick: () =>
        void pressing(submit, async () => {
          try {
            // No body: the name is the session's; the precondition is the version last read.
            await request(`/internal/v1/orders/${id}/collection`, {
              method: "POST",
              ifMatch: order.row_version,
              idempotencyKey: keyFor(`collect|${order.row_version}`),
            });
            releaseKey();
            toast(`${ORDER_STEP_DONE_VI.COLLECT} · ${orderName(order)}`);
            const view = await reread();
            successState(made, "Đã ghi nhận khách nhận đồ.", null, view);
          } catch (error) {
            show(alertHost, refusal(error));
          }
        }),
    });
    const made = openFresh({
      id: "order-collect",
      title: "Khách tới lấy đồ",
      body: h(
        "div",
        { class: "stack" },
        h("p", null, "Khách đã trả trước. Khi đưa đồ cho khách, bấm nút dưới — tên bạn được ghi."),
        alertHost,
      ),
      actions: gated(submit, writeVerdict),
    });
  }

  /**
   * One delivery attempt (`DEC-023`): no amount, because the customer paid at the counter before
   * the laundry left; a failed attempt is its own row and costs nothing.
   *
   * @param {any} entry
   */
  function openLeg(entry) {
    const order = current;
    if (!order) return;
    const kind = String(entry.step) === "DELIVERY_PICKUP" ? "PICKUP" : "RETURN";
    const alertHost = h("div");
    /** @param {"SUCCEEDED"|"FAILED"} outcome @param {HTMLButtonElement} control */
    async function record(outcome, control) {
      render(alertHost);
      await pressing(control, async () => {
        try {
          const recorded = await request(`/internal/v1/orders/${id}/delivery-legs`, {
            method: "POST",
            body: { leg_kind: kind, outcome },
            idempotencyKey: keyFor(`leg|${kind}|${outcome}|${order.row_version}`),
          });
          releaseKey();
          toast(`${ORDER_STEP_DONE_VI[String(entry.step)]} · ${orderName(order)}`);
          const view = await reread();
          successState(
            made,
            outcome === "SUCCEEDED"
              ? kind === "RETURN"
                ? "Đã ghi: khách đã nhận đồ."
                : "Đã ghi: đã lấy đồ của khách."
              : "Đã ghi chuyến không thành công. Lần sau là một chuyến mới.",
            recorded.completes_fulfillment ? "Đơn này đóng được rồi." : null,
            view,
          );
        } catch (error) {
          show(alertHost, refusal(error));
        }
      });
    }
    const ok = button({
      label: kind === "RETURN" ? "Giao thành công" : "Lấy được đồ",
      variant: "primary",
      block: true,
      network: true,
      data: { legOutcome: "SUCCEEDED" },
      onClick: () => void record("SUCCEEDED", ok),
    });
    const failed = button({
      label: kind === "RETURN" ? "Không giao được" : "Không lấy được đồ",
      block: true,
      network: true,
      data: { legOutcome: "FAILED" },
      onClick: () => void record("FAILED", failed),
    });
    const made = openFresh({
      id: "order-leg",
      title: stepVi(String(entry.step)),
      body: h(
        "div",
        { class: "stack" },
        h(
          "div",
          { class: "fact-line" },
          h(
            "p",
            { class: "hint" },
            "Người giao không thu tiền. Không giao được thì ghi lại — không tính thêm phí.",
          ),
          infoButton("Chuyến giao ghi những gì?", h("p", null, LEG_RULE.guardrail)),
        ),
        alertHost,
      ),
      actions: h("div", { class: "btn-stack" }, gated(ok, writeVerdict), gated(failed, writeVerdict)),
    });
  }

  /**
   * Huỷ đơn. When the server says the cancellation goes through review it also lists exactly the
   * custody answers it will accept (`DEC-024`), each dry-run; only those are offered.
   *
   * @param {any} entry
   */
  function openCancel(entry) {
    const needs = Array.isArray(entry.requires) && entry.requires.includes("custody_resolution");
    const choices = Array.isArray(entry.custody_resolutions) ? entry.custody_resolutions : [];
    const alertHost = h("div");
    let custody = "";
    const confirm = confirmButton({
      label: "Huỷ đơn",
      confirmLabel: "Bấm lần nữa để huỷ đơn",
      block: true,
      onConfirm: async () => {
        const done = await runComposite(
          entry,
          needs ? { custody_resolution: custody } : {},
          alertHost,
          confirm,
        );
        if (done) made.close();
      },
    });
    if (needs) confirm.disabled = true;
    const made = openFresh({
      id: "order-cancel",
      title: "Huỷ đơn",
      body: h(
        "div",
        { class: "stack" },
        needs
          ? h(
              "div",
              { class: "stack stack--tight" },
              h("p", { class: "field-label" }, "Đồ và tiền của khách đã xử lý thế nào?"),
              choiceChips({
                label: "Đồ và tiền đã xử lý thế nào",
                name: "custody_resolution",
                options: choices.map((value) => ({
                  value: String(value),
                  label: enumVi(String(value)),
                  title: String(value),
                })),
                onChange: (value) => {
                  custody = value;
                  if (writeVerdict.allowed) confirm.disabled = false;
                },
              }),
              h(
                "p",
                { class: "hint" },
                "Không có mục “đã giặt rồi khách bỏ đi không trả tiền”: đồ đã giặt thì khách trả " +
                  "tiền và lấy về, hoặc đồ ở lại tiệm (DEC-024).",
              ),
            )
          : h("p", null, "Khách đổi ý trước khi tiệm làm gì với đồ: đơn được huỷ ngay."),
        alertHost,
      ),
      actions: gated(confirm, writeVerdict),
    });
  }

  // --- side reads -----------------------------------------------------------------------------

  /** @param {string} store the order's own store, read off the order */
  async function loadIncidents(store) {
    if (!readIncidentsVerdict.allowed) {
      render(
        incidentsHost,
        inlineAlert({
          state: "warn",
          title: "Vai trò này không đọc được khiếu nại",
          body: readIncidentsVerdict.reason,
        }),
      );
      return;
    }
    try {
      const body = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/orders/${encodeURIComponent(orderId)}/incidents?limit=${INCIDENT_LIMIT}`,
      );
      const items = Array.isArray(body) ? body : [];
      render(
        incidentsHost,
        items.length
          ? list(
              items.map((item) =>
                listRow({
                  // CONSOLE-REDESIGN-004: each complaint has its own page, with the remedy flow on it.
                  href: `#/incidents/${encodeURIComponent(String(item.incident_id))}`,
                  leading: "incident",
                  title: enumVi(item.category),
                  // The complaint as the counter typed it -- untrusted text, a text node.
                  meta: [
                    h(
                      "span",
                      { class: "order-row__meta" },
                      statusPill({
                        state: item.status === "CLOSED" ? "neutral" : "warn",
                        text: enumVi(item.status),
                        token: String(item.status),
                      }),
                      h("span", null, dateTime(item.opened_at)),
                    ),
                    item.evidence_summary
                      ? h("span", { class: "incident-summary" }, String(item.evidence_summary))
                      : null,
                  ],
                  data: { incidentId: String(item.incident_id) },
                }),
              ),
              { label: "Khiếu nại của đơn này" },
            )
          : h("p", { class: "surface muted order__empty" }, "Chưa có khiếu nại nào cho đơn này."),
        isTruncated(items, INCIDENT_LIMIT)
          ? h("p", { class: "hint" }, `Chỉ hiện ${INCIDENT_LIMIT} khiếu nại mới nhất; có thể còn nữa.`)
          : null,
      );
    } catch (error) {
      show(incidentsHost, errorNotice(error, { onRetry: () => void loadIncidents(store) }));
    }
  }

  /** @param {string} store the order's own store, read off the order */
  async function loadCredits(store) {
    if (!readIncidentsVerdict.allowed) {
      render(
        creditHost,
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Vai trò này không đọc được khoản giảm trừ"),
          h("p", null, readIncidentsVerdict.reason),
        ),
      );
      return;
    }
    try {
      const body = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/orders/${encodeURIComponent(orderId)}/remedy-credits`,
      );
      const credits = Array.isArray(body?.credits) ? body.credits : [];
      render(
        creditHost,
        credits.length
          ? h("ul", { class: "rows", "aria-label": "Khoản giảm trừ" }, credits.map(creditItem))
          : h("p", { class: "surface muted order__empty" }, "Đơn này chưa phát hành khoản giảm trừ nào."),
        body?.truncated
          ? h("p", { class: "hint" }, "Máy chủ cắt danh sách; có thể còn khoản khác.")
          : null,
      );
    } catch (error) {
      render(creditHost, errorNotice(error, { onRetry: () => void loadCredits(store) }));
    }
  }

  /**
   * Consecutive rows with the same action, actor and minute, folded into one "× N" row. A composite
   * step writes one audit row per transition (ORDER-STEPS-001), so "Nhận đồ" alone is five identical
   * lines. Folding keeps the server's order and drops nothing: the count says how many rows are in
   * the fold, and the timestamps are the first row's.
   *
   * @param {any[]} rows
   * @returns {Array<{entry: any, times: number}>}
   */
  function foldAudit(rows) {
    /** @type {Array<{entry: any, times: number, key: string}>} */
    const out = [];
    for (const entry of rows) {
      const key =
        `${entry.action}|${entry.transition_target || ""}|${entry.actor_type}|` +
        `${entry.actor_id}|${dateTime(entry.occurred_at)}`;
      const last = out[out.length - 1];
      if (last && last.key === key) last.times += 1;
      else out.push({ entry, times: 1, key });
    }
    return out;
  }

  /** @param {{entry: any, times: number}} folded */
  function auditRow(folded) {
    const entry = folded.entry;
    return listRow({
      // A transition row names what moved and where to (from its domain event), so a composite
      // step reads as its real states — "Tiếp nhận · Đã nhận đồ" — not "Chuyển trạng thái đơn × 5".
      title: h(
        "span",
        {
          title: entry.transition_target
            ? `${entry.action} ${entry.transition_dimension}→${entry.transition_target}`
            : String(entry.action),
        },
        entry.transition_target
          ? `${AXIS_VI[entry.transition_dimension] || enumVi(entry.transition_dimension)} · ` +
              enumVi(entry.transition_target)
          : enumVi(entry.action),
        folded.times > 1 ? h("span", { class: "muted" }, ` × ${folded.times}`) : null,
      ),
      meta: h(
        "span",
        // The staff id is kept for whoever needs to quote it, one hover or long-press away.
        { title: entry.actor_id || "" },
        // `null` means the server recorded no staff user for this row -- the outbox worker and the
        // bootstrap have no person behind them. Shown as such, never as a blank.
        `${dateTime(entry.occurred_at)} · ${enumVi(entry.actor_type)}` +
          (entry.actor_id ? "" : " —"),
      ),
    });
  }

  /** @param {string} store the order's own store, read off the order */
  async function loadTimeline(store) {
    // The repository gates this on SHADOW_READ even though the screen is an order screen. A role
    // without it gets the reason, not an empty list that would read as "nothing ever happened".
    if (!shadowVerdict.allowed) {
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
    try {
      const entries = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/shadow/audit/${encodeURIComponent(orderId)}`,
      );
      const rows = Array.isArray(entries) ? entries : [];
      timelineTruncation.textContent = isTruncated(rows, AUDIT_LIMIT)
        ? `Máy chủ cố định ${AUDIT_LIMIT} dòng và đã trả đủ; có thể còn nữa. Route này không ` +
          "nhận tham số limit và không có phân trang, nên phần còn lại chỉ đọc được ở cơ sở dữ liệu."
        : "";
      if (!rows.length) {
        render(
          timelineHost,
          empty(
            "Không có sự kiện nào được ghi cho định danh này. Máy chủ trả mảng rỗng cả khi định danh " +
              "không tồn tại lẫn khi đơn chưa phát sinh sự kiện — hai trường hợp đó không phân biệt " +
              "được từ đây.",
          ),
        );
        return;
      }
      const drawRows = (all) => {
        const folded = foldAudit(rows);
        const shown = all ? folded : folded.slice(-AUDIT_COMPACT);
        render(
          timelineHost,
          list(shown.map(auditRow), { label: "Lịch sử đơn", id: "order-timeline" }),
          folded.length > AUDIT_COMPACT
            ? button({
                label: all ? "Thu gọn" : `Xem tất cả (${count(rows, AUDIT_LIMIT)})`,
                variant: "quiet",
                onClick: () => drawRows(!all),
              })
            : null,
        );
      };
      drawRows(false);
    } catch (error) {
      timelineTruncation.textContent = "";
      render(timelineHost, errorNotice(error, { onRetry: () => void loadTimeline(store) }));
    }
  }

  /**
   * The order first, then its side lists from the order's own store -- which need not be the store
   * selected in the header, since a staff member working two shops can open either's orders.
   */
  async function start() {
    const found = await loadOrder();
    if (found) {
      const store = found.store_id || storeId();
      void loadIncidents(store);
      void loadCredits(store);
      void loadTimeline(store);
      return;
    }
    render(incidentsHost, empty("Chưa đọc khiếu nại vì chưa đọc được đơn."));
    render(creditHost, empty("Chưa đọc khoản giảm trừ vì chưa đọc được đơn."));
    render(timelineHost, empty("Chưa đọc dòng thời gian vì chưa đọc được đơn."));
  }

  if (wellFormed) {
    void start();
  } else {
    // A malformed identifier would be a 422 from the server and a confusing one, because the
    // failure is in the address bar rather than in anything the operator typed on this screen.
    render(
      summaryHost,
      h(
        "div",
        { class: "notice", dataState: "danger" },
        h("p", { class: "notice__title" }, "Địa chỉ này không chứa một mã đơn hợp lệ"),
        h("p", null, "Mã đơn phải là một UUID. Không có yêu cầu nào được gửi đi."),
        h("p", { class: "hint mono" }, orderId || UNKNOWN),
      ),
    );
    render(incidentsHost, empty("Chưa đọc khiếu nại vì mã đơn trong địa chỉ không hợp lệ."));
    render(creditHost, empty("Chưa đọc khoản giảm trừ vì mã đơn trong địa chỉ không hợp lệ."));
    render(timelineHost, empty("Chưa đọc dòng thời gian vì mã đơn trong địa chỉ không hợp lệ."));
  }

  const reportIncident = gated(
    button({
      label: "Ghi khiếu nại",
      icon: "plus",
      variant: "quiet",
      onClick: () => {
        // WS6 cross-link: hand the order id to the incident form through the incidents module's
        // in-memory slot and navigate there.
        setIncidentOrderPrefill(orderId);
        navigate("/incidents");
      },
    }),
    incidentVerdict,
  );

  return h(
    "section",
    { class: "screen order" },
    headHost,
    summaryHost,
    infoHost,
    legsHost,
    section({
      title: "Khiếu nại",
      card: false,
      action: wellFormed ? reportIncident : null,
      info: infoButton(
        "Bồi hoàn cho khách làm ở đâu?",
        h(
          "p",
          { class: "hint" },
          "Bồi hoàn gắn với khiếu nại, không gắn thẳng với đơn: mở một khiếu nại trong danh sách " +
            "này; bồi hoàn làm ngay trên trang của khiếu nại đó.",
        ),
      ),
      children: incidentsHost,
    }),
    section({
      // The name the receipt's and the remedy page's "lost the code?" hints point at.
      title: "Khoản giảm trừ của đơn này",
      card: false,
      // CONSOLE-REDESIGN-001/004: a credit is spent on the next bill, on the receipt in ＋ Nhận đồ
      // (the tab bar's centre button) -- said in the ⓘ below rather than as a second header
      // control, which wrapped the title at 390 px.
      info: infoButton(
        "Khoản giảm trừ dùng thế nào?",
        h(
          "p",
          { class: "hint" },
          "Khoản giảm trừ là phiếu cầm tay: ai đọc đúng mã thì dùng được, đúng một lần, ở cửa " +
            "hàng này, cho hoá đơn lần sau. Khách quên mã thì tìm lại đơn theo số phiếu rồi đọc " +
            "mã ở đây. Máy chủ không ghi hạn dùng cho khoản giảm trừ.",
        ),
        h(
          "p",
          { class: "hint" },
          "Dùng mã ở ＋ Nhận đồ: tính giá xong, bấm “Dùng khoản giảm trừ” trên hoá đơn rồi nhập mã.",
        ),
      ),
      children: creditHost,
    }),
    section({
      title: "Lịch sử",
      card: false,
      info: infoButton(
        "Lịch sử này đọc thế nào?",
        h("p", null, AUDIT_NOTE.guardrail),
        h(
          "p",
          { class: "hint" },
          "Tác nhân không phải STAFF — OUTBOX_WORKER, AGENT_RUNNER, BOOTSTRAP — không gắn với " +
            "người dùng nào, nên cột định danh người thực hiện hiện dấu “—”. Đó là “không có " +
            "người nào”, không phải “thiếu dữ liệu”.",
        ),
      ),
      children: h("div", { class: "stack stack--tight" }, timelineTruncation, timelineHost),
    }),
    techHost,
    actionHost,
    sheetsHost,
  );
}

export const screen = {
  path: "/orders/:orderId",
  title: "Chi tiết đơn",
  capability: "ORDERS_READ",
  needsStore: true,
  render: render_,
};
