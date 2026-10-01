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
 *     Money and legs keep their own routes -- payments, collection, delivery legs -- because they
 *     carry what only a person can attest: the amount taken and how, the courier's outcome.
 *   - **`RECEIVE` needs the operator's word on capacity.** The server derives five of the six
 *     readiness facts itself; `slot_approved` is the one it cannot know. The confirmation sheet asks
 *     for it as an explicit tick — the button alone never asserts it.
 *   - **Hẹn trả is the server's** (`PROMISE-001`, `DEC-037`). The Nhận đồ sheet shows the time the
 *     server says pressing now would promise ("Hẹn trả: 13:00 thứ Bảy 26/09"), with the choices it
 *     offers (24/48 giờ, gấp 2 giờ, tự chọn giờ) or a date-time picker when a person must set it;
 *     the page shows the promise and its state, and "Hẹn lại" moves it with a reason.
 *   - **Giặt lại and Không nhận đồ need a person's reason** (ORDER-STEPS-002). The server never
 *     makes either primary and lists the reasons it will take; the sheet offers exactly those, and
 *     the history reads the step and its reason back ("Giặt lại · Chưa sạch"). When they are the only
 *     legal steps the bar has no big button, only "Khác".
 *   - **Thu tiền takes what the server says remains** (`PAYMENT-001`, `DEC-035`). The money card
 *     reads *Tổng · Đã trả · Còn lại* and every payment, all figures the server's. The sheet
 *     prefills the remaining amount as the server computed it -- the owner's spec -- and "Khách trả
 *     một phần" opens a field for a deposit, parsed with `parseDong`; the method (Tiền mặt /
 *     Chuyển khoản) is one tap, and a transfer asks for "Đã thấy tiền vào tài khoản". The server
 *     refuses more than remains (`OVERPAYMENT_REFUSED`) and pickup while money is owed.
 *     `VIETQR-001` (`DEC-041`): choosing Chuyển khoản shows the server's QR for exactly what
 *     remains, with the amount and the transfer code (*Nội dung*) in large type, once the owner
 *     has published the shop's account; the amount field stays editable for a part payment.
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
import {
  UNKNOWN,
  UUID,
  count,
  dateTime,
  money,
  parseDong,
  promiseTime,
  shopInstantFromInput,
} from "../core/format.js";
import {
  ACQUISITION_SOURCE_VI,
  ORDER_STEP_DONE_VI,
  PAYMENT_METHOD_VI,
  enumLabel,
  enumVi,
  stepVi,
} from "../core/i18n.js";
import { modeBadge, orderProgress, orderStatus } from "../core/orderStatus.js";
import { cancellationMoneyBlock } from "../ui/remedyMoney.js";
import { can } from "../core/rbac.js";
import { navigate } from "../core/router.js";
import { principal, storeId } from "../core/session.js";
import {
  copyable,
  empty,
  errorNotice,
  gated,
  gatedFields,
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
  segmented,
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
// RECEIPT-PRINT-001: the receipt's route, and the one-shot "just created" hand-off from ＋ Nhận đồ.
import { receiptPath, takeReceiptOffer } from "./receipt.js";
// PROMISE-001: the promised-ready time.
import {
  PROMISE_REASON_VI,
  currentPromiseInput,
  promisePill,
  receivePromise,
} from "../ui/promise.js";
// PAYMENT-002 (`DEC-035`): "Giao đồ — ghi công nợ" for an account customer's order.
import { accountHandover } from "../ui/accountHandover.js";
// UNCLAIMED-001 (DEC-036): Lưu kho -- days waiting, the storage fee, contact attempts, thanh lý.
import { readStorage, storageCharge, storageChargeLine, storageSection } from "../ui/unclaimed.js";
// EINVOICE-REQUEST-001 (DEC-040): the order's Hóa đơn row.
import { invoiceSection } from "../ui/invoice.js";
// GOODS-AND-DRAWER-009: "Thu tiền trước khi giao" (review M2) and the refund's method (review M4).
import { payFirstCaption, paysFirst, refundMethodField } from "../ui/goodsAndDrawer.js";
// SHOP-CAPTURE-001: "Máy nào?", the trip-cost fields and the order's recorded cycles and trips.
import {
  captureRows,
  loadWashMachines,
  machineChips,
  machinePicker,
  tripFields,
} from "../ui/shopCapture.js";
// VIETQR-001 (DEC-041): the exact QR for what is owed, inside Thu tiền when the method is a transfer.
import { paymentQr } from "../ui/vietqr.js";

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

/** `SettlementShape.EXACT_PAYMENT_PREPAID_DELIVERY`: paid in full before the courier leaves. */
const PREPAID_DELIVERY = "EXACT_PAYMENT_PREPAID_DELIVERY";

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
  "REWASH",
  "RELEASE",
  "HAND_OVER",
  "COMPLETE",
  "CANCEL",
  "REJECT_INTAKE",
  "REOPEN",
]);

/** Steps that stop or end work: red, and never one tap. */
const DESTRUCTIVE = new Set(["CANCEL", "HOLD", "REJECT_INTAKE"]);

/**
 * ORDER-STEPS-002: the steps that record a reason a person gives, and where the server lists the
 * reasons it will take. Presentation of the server's own `requires` / reason lists, nothing more.
 */
const REASON_STEPS = {
  REWASH: {
    field: "rewash_reason",
    list: "rewash_reasons",
    question: "Vì sao giặt lại?",
    note: "Khách không trả thêm tiền. Đồ quay lại bước giặt, rồi kiểm tra lại.",
  },
  REJECT_INTAKE: {
    field: "rejection_reason",
    list: "rejection_reasons",
    question: "Vì sao không nhận?",
    note: "Trả túi đồ cho khách. Đơn bị huỷ, không có tiền nào chuyển.",
  },
};

/** Steps that end the visit; offered straight away in a sheet's success state when primary. */
const CLOSING = new Set(["HAND_OVER", "COMPLETE"]);

// --- MONEY-LIFECYCLE-009 (M1/A2): Tất toán ---------------------------------------------------

/** What "Thu tiền" reads when nothing is left to take: 0 ₫ settles the order. */
const SETTLE_LABEL = "Tất toán";

/**
 * Whether the order's ledger already covers everything it owes while its balance still reads
 * partly paid -- the storage fee fell after a part payment covered some of it (a waiver, a hold,
 * a rewash, a withdrawn policy). Then 0 ₫ settles it on the payments route and the goods may
 * leave. Both figures are the server's; this compares them, it never computes money.
 *
 * @param {any} order
 * @returns {boolean}
 */
function settlesWithoutMoney(order) {
  return order?.balance === "PARTIALLY_PAID" && order?.remaining_vnd === 0;
}

/** COUNTER-UI-RACE-009 (C4): what a sheet says after its "tải lại" (`sheetRefusal`). */
const RELOADED_IN_SHEET = "Đã tải lại đơn mới nhất — kiểm tra lại rồi bấm.";
const STEP_GONE = "Đơn vừa đổi — việc đang làm không còn làm được. Xem trạng thái mới của đơn.";
const RELOADED_CLOSED = "Đã tải lại đơn. Xem trạng thái mới trước khi bấm lại.";

/**
 * `PAYMENT-001` / `DEC-035`: how the counter takes money. A policy statement bound to the decision
 * register (`console-disclosures-v1.yaml`, POLICY_BOUND); its text is the slot and must not be
 * reworded without the decision behind it. Re-worded by `DEC-035`, which superseded the `DEC-010`
 * deferral the previous sentence ("đặt cọc … bị từ chối") stated. Shown behind ⓘ in the payment
 * sheet, with its point-of-action summary visible beside the method.
 */
const SETTLEMENT_RULE = {
  guardrail:
    "Khách trả bằng tiền mặt hoặc chuyển khoản vào tài khoản của tiệm, một lần hay nhiều lần " +
    "(đặt cọc), mỗi lần từ 1 ₫ tới số còn lại (quyết định DEC-035). Khách đưa dư thì trả lại " +
    "tiền thừa: số lớn hơn số còn lại bị từ chối. Chuyển khoản chỉ ghi khi đã thấy tiền vào tài " +
    "khoản; máy không tự kiểm tra ngân hàng. Đồ chỉ giao khi đã trả đủ. Người giao không thu " +
    "tiền. Mỗi lần thu là một dòng không sửa được.",
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
 * "Hẹn lại" is offered while the server calls the promise unfinished (`promise_state`); a finished
 * or closed order reads MET / MISSED / null, and the server refuses a Hẹn lại there anyway.
 *
 * @param {any} order
 * @returns {boolean}
 */
function promiseOpen(order) {
  return ["ON_TRACK", "DUE_SOON", "LATE"].includes(String(order?.promise_state || ""));
}

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
  // MONEY-LIFECYCLE-009: voided with the cancellation of this order (DEC-045), or a credit issued
  // again because an order that spent the earlier one was cancelled without charge (DEC-046).
  const voided = credit.status === "VOIDED";
  const again = credit.reissue_of ? " · cấp lại" : "";
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
          : voided
            ? { state: "neutral", text: "Đã huỷ cùng đơn", token: "VOIDED" }
            : { state: "neutral", text: "Đã dùng", token: String(credit.status || UNKNOWN) },
      ),
    ),
    h(
      "p",
      { class: "credit-row__meta" },
      `${CREDIT_KIND_LABEL[credit.kind] || String(credit.kind || UNKNOWN)}${again} · ` +
        `phát hành ${dateTime(credit.issued_at)}` +
        (unused ? "" : voided ? ` · huỷ ${dateTime(credit.voided_at)}` : ` · dùng ${dateTime(credit.redeemed_at)}`),
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
  // The receipt prints the bound quote's lines, which the operations gate serves.
  const receiptVerdict = can(me, "QUOTES_READ");
  const wellFormed = UUID.test(orderId);
  // Arrived straight from ＋ Nhận đồ: the customer is still at the counter, so the receipt is one
  // tap away at the top of the page (read once; a reload does not offer it again).
  const justCreated = wellFormed && takeReceiptOffer(orderId);
  // Every route below is written out in full: the contract test reads path literals, and a path
  // assembled from a variable is one it cannot see.
  const id = encodeURIComponent(orderId);

  const headHost = h("div", null, page({ back: { href: "#/orders", label: "Đơn hàng" }, title: "Chi tiết đơn" }));
  const createdHost = h(
    "div",
    { id: "order-created" },
    justCreated
      ? inlineAlert({
          state: "ok",
          title: "Đã tạo đơn.",
          // Secondary: the page's one primary is still the server's next step.
          actions: receiptControl("In phiếu cho khách"),
        })
      : null,
  );
  const summaryHost = h("div", { class: "stack" }, skeletonRows(2));
  const infoHost = h("div");
  const legsHost = h("div");
  // SHOP-CAPTURE-001: the order's wash cycles and trip costs, when it has any.
  const captureHost = h("div", { id: "order-capture" });
  // UNCLAIMED-001: the order's storage, when it is waiting for pickup or has a fee on record.
  const storageHost = h("div", { id: "order-storage-host" });
  // EINVOICE-REQUEST-001: the Hóa đơn row, read from the order's own store once the order is in.
  const invoiceHost = h("div", { id: "order-invoice-host" });
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

  // PAYMENT-002: the account offer under the money card; the server decides, this page's own
  // sheet, refusal and success machinery carries it out.
  const accountOffer = accountHandover({
    orderId,
    verdict: can(me, "ACCOUNTS_COLLECT"),
    hooks: {
      openFresh: (spec) => openFresh(spec),
      successState: (made, title, body, order) => successState(made, title, body, order),
      reread: () => reread(),
      refusal: (error) => refusal(error),
      sheetRefusal: (error, where) => sheetRefusal(error, where),
      pressing: (control, work) => pressing(control, work),
      keyFor: (intent) => keyFor(intent),
      releaseKey: () => releaseKey(),
    },
  });

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
    if (found) {
      void loadTimeline(found.store_id || storeId());
      void loadCapture();
      void loadStorage(found);
      void loadInvoice(found);
    }
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
      accountOffer.node,
    );
    accountOffer.refresh(order);
    render(infoHost, infoSection(order));
    render(legsHost, legsSection(order));
    render(techHost, technical(order));
    drawActions(order);
  }

  /**
   * The money section (`PAYMENT-001`): *Còn lại* large, *Tổng · Đã trả* under it, then every
   * payment. Every figure is the server's (`owed_vnd`, `paid_vnd`, `remaining_vnd`, `payments`);
   * this only formats. When money is owed but paying is not the page's big button (a deposit at
   * drop-off, while washing is), "Thu tiền" sits here instead of under "Khác".
   *
   * @param {any} order
   */
  function moneyCard(order) {
    const due = amountDue(order);
    const paid = order.balance === "PAID";
    const partly = order.balance === "PARTIALLY_PAID";
    const waiting =
      paid &&
      order.self_collection_recorded === false &&
      SELF_COLLECT_MODES.has(order.fulfillment_mode) &&
      order.commercial === "ACTIVE";
    const cancelled = order.commercial === "CANCELLED";
    // PAYMENT-002 (`DEC-035`): the goods left on the customer's account -- money owed, not taken.
    const onAccount = order.balance === "ON_ACCOUNT";
    const label = onAccount
      ? "Ghi công nợ"
      : order.balance === "UNPAID"
        ? cancelled
          ? "Tổng đơn"
          : "Phải thu"
        : partly
          ? "Còn lại"
          : paid
            ? "Đã thu"
            : order.balance === "REFUNDED"
              ? "Tổng đơn · đã hoàn"
              : "Tổng đơn";
    const amount = partly || onAccount
      ? money(order.remaining_vnd, "Chưa có tổng")
      : due
        ? due.text
        : UNKNOWN;
    const plainCaption = onAccount
      ? "Tiền ghi vào công nợ của khách, không thu tại quầy. Thu ở trang của khách."
      : paid
      ? `Đã thu đủ tiền.${waiting ? " Khách chưa nhận đồ." : ""}`
      : order.balance === "REFUNDED"
        ? "Đã hoàn tiền cho khách."
        : partly
          ? settlesWithoutMoney(order)
            ? "Khách đã trả đủ — bấm “Tất toán” rồi giao đồ."
            : "Khách trả đủ phần còn lại thì mới giao đồ."
          : order.balance === "UNPAID"
            ? cancelled
              ? "Đơn đã huỷ — không thu tiền."
              : due && !due.known
                ? "Báo giá chưa có một tổng duy nhất, nên chưa thu được."
                : null
            : `Công nợ: ${enumVi(order.balance)}. Quầy không thu tiền cho đơn này; có gì chưa ` +
              "rõ thì báo chủ tiệm.";
    // GOODS-AND-DRAWER-009 (review M2): a delivery that still owes money leaves only once paid,
    // said beside the payment action -- unless the quote has no single total to take.
    const payFirst = partly || (due && due.known) ? payFirstCaption(order) : null;
    const caption = payFirst || plainCaption;
    const payments = Array.isArray(order.payments) ? order.payments : [];
    // Tổng · Đã trả once something is paid; before that "Phải thu" already is the total.
    const split =
      payments.length && order.owed_vnd !== null && order.owed_vnd !== undefined
        ? h(
            "div",
            { class: "order__money-split", dataMoneySplit: "true" },
            keyValues([
              ["Tổng", money(order.owed_vnd)],
              // UNCLAIMED-001: the storage fee is one of the server's charges; said, not added.
              // Labelled "Trong đó" (round-7 desk review): as a bare "Phí lưu kho" row under
              // "Tổng" it read as a sum on top of the total, 165.000 + 25.000.
              storageCharge(order)
                ? ["Trong đó phí lưu kho", money(storageCharge(order))]
                : null,
              ["Đã trả", money(order.paid_vnd)],
              // Partly paid, the large figure above already is "Còn lại"; said once.
              partly ? null : ["Còn lại", money(order.remaining_vnd)],
            ]),
          )
        : null;
    const ledger = payments.length
      ? h(
          "div",
          { class: "order__payments", dataPayments: String(payments.length) },
          h("p", { class: "field-label" }, "Các lần thu"),
          list(
            payments.map((item) =>
              listRow({
                title: paymentMethod(item),
                meta: paymentMeta(item),
                trailing: money(item.amount_vnd),
                data: { payment: String(item.payment_id) },
              }),
            ),
            { label: "Các lần thu" },
          ),
          order.payments_truncated
            ? h(
                "p",
                { class: "hint" },
                `Chỉ hiện ${payments.length} lần thu đầu; số đã trả vẫn tính đủ mọi lần.`,
              )
            : null,
        )
      : null;
    const take = (order.next_steps || []).find(
      (entry) => String(entry.step) === "TAKE_PAYMENT" && !entry.primary,
    );
    // UNCLAIMED-001: what "Phải thu" / "Còn lại" includes, in the server's own figure.
    const storageLine = paid ? null : storageChargeLine(order);
    return h(
      "div",
      { class: "surface order__money" },
      moneyHero({
        label,
        amount,
        caption: [caption, storageLine].filter(Boolean).join(" · ") || null,
        state: paid ? "ok" : null,
      }),
      split,
      ledger,
      // MONEY-LIFECYCLE-009 (DEC-045/046): what the cancellation did to the remedy credits.
      cancellationMoneyBlock(order, "DONE"),
      take ? stepControl(take) : null,
    );
  }

  /**
   * PROMISE-001: "Hẹn trả" — the time the customer was last told, its state now (the server's),
   * the first promise when it was moved, and "Hẹn lại" while the laundry is not finished.
   *
   * @param {any} order
   */
  function promiseRow(order) {
    if (!order.current_promise_at) return null;
    const moved = order.promised_ready_at && order.promised_ready_at !== order.current_promise_at;
    // Offered only in the states the server calls unfinished; a finished or closed order reads
    // MET / MISSED / null, and the server refuses a Hẹn lại there anyway.
    const open = promiseOpen(order);
    return [
      "Hẹn trả",
      h(
        "span",
        { class: "order__promise", dataField: "promise" },
        h("strong", { dataPromiseAt: String(order.current_promise_at) }, promiseTime(order.current_promise_at)),
        " ",
        promisePill(order.promise_state),
        moved
          ? h("span", { class: "hint order__promise-first" }, `Hẹn đầu: ${promiseTime(order.promised_ready_at)}`)
          : null,
        open
          ? gated(
              button({
                label: "Hẹn lại",
                variant: "quiet",
                icon: "clock",
                id: "promise-change",
                onClick: () => openPromiseChange(),
              }),
              writeVerdict,
            )
          : null,
      ),
    ];
  }

  /**
   * How one payment came (a row's title). A payment recorded before the method was asked for says
   * so rather than claiming cash was seen.
   *
   * @param {any} item a `PaymentViewResponse`
   * @returns {string}
   */
  function paymentMethod(item) {
    const how = PAYMENT_METHOD_VI[item.method] || String(item.method);
    return item.legacy ? `${how} (không ghi cách trả)` : how;
  }

  /**
   * When, the reference tail and who took it (a row's second line) -- all the server's.
   *
   * @param {any} item a `PaymentViewResponse`
   * @returns {string}
   */
  function paymentMeta(item) {
    return [
      dateTime(item.recorded_at),
      item.bank_ref_last ? `…${item.bank_ref_last}` : null,
      item.recorded_by_name || null,
    ]
      .filter(Boolean)
      .join(" · ");
  }

  /** @param {any} order */
  function infoSection(order) {
    const ticket = ticketLabel(order);
    return section({
      title: "Thông tin",
      children: keyValues([
        // CUSTOMER-001: the customer record the order was taken for, read live -- an erased
        // record keeps its link and loses its name.
        order.customer_id
          ? [
              "Khách",
              h(
                "a",
                {
                  href: `#/customers/${encodeURIComponent(String(order.customer_id))}`,
                  dataField: "customer",
                },
                order.customer_name || (order.customer_has_phone ? "Khách quen" : "Đã xoá thông tin"),
              ),
            ]
          : null,
        promiseRow(order),
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
        actionBar(
          h(
            "p",
            { class: "hint order__closed" },
            ["COMPLETED", "CANCELLED"].includes(order.commercial)
              ? "Đơn đã đóng — không còn bước nào."
              : "Chưa có bước nào làm được lúc này.",
          ),
          receiptControl("In phiếu"),
        ),
      );
      return;
    }
    // ORDER-STEPS-002: a list may have no primary -- when the only legal steps are ones the server
    // never makes primary (Giặt lại, Không nhận đồ). Then nothing is promoted to the big button;
    // the steps stay under "Khác", and the bar says there is no ordinary next step.
    const primary = steps.find((entry) => entry.primary) || null;
    // Presentation only: the server's order, with the steps that stop or end work moved last so a
    // thumb reaching for "Thu tiền" does not land on "Huỷ đơn". A "Thu tiền" that is not the big
    // button is on the money card already (`moneyCard`), so it is not listed twice.
    const rest = steps.filter(
      (entry) => entry !== primary && String(entry.step) !== "TAKE_PAYMENT",
    );
    const others = [
      ...rest.filter((entry) => !DESTRUCTIVE.has(String(entry.step))),
      ...rest.filter((entry) => DESTRUCTIVE.has(String(entry.step))),
    ];
    render(
      actionHost,
      actionBar(
        actionAlert,
        primary
          ? stepControl(primary, { primary: true })
          : h("p", { class: "muted order__no-primary" }, "Chưa có bước tiếp theo."),
        // With nothing else to offer, the receipt takes the secondary place; otherwise it is the
        // first thing under "Khác" that is not a step.
        others.length ? moreButton(others) : receiptControl("In phiếu"),
      ),
    );
  }

  /**
   * "In phiếu": opens the receipt (a read, never a write). Shut with its reason for a role that
   * cannot read the quote the receipt prints.
   *
   * @param {string} label
   * @param {"primary"|"secondary"|"quiet"} [variant]
   * @returns {HTMLElement}
   */
  function receiptControl(label, variant = "secondary") {
    return gated(
      button({
        label,
        icon: "printer",
        variant,
        block: variant !== "quiet",
        data: { receipt: "true" },
        onClick: () => {
          closeMore();
          navigate(receiptPath(orderId));
        },
      }),
      receiptVerdict,
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
    const label =
      step === "TAKE_PAYMENT" && settlesWithoutMoney(current) ? SETTLE_LABEL : stepVi(step);
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
          else if (step === "TAKE_PAYMENT") {
            if (settlesWithoutMoney(current)) openSettle();
            else openPayment();
          }
          else if (step === "COLLECT") openCollect();
          else if (step === "DELIVERY_PICKUP" || step === "DELIVERY_RETURN") openLeg(entry);
          else if (step === "CANCEL") openCancel(entry);
          // SHOP-CAPTURE-001: "Máy nào?" before the wash starts; "Bỏ qua" is always offered.
          else if (step === "START_WASH") openMachine(entry);
          else if (Object.hasOwn(REASON_STEPS, step)) openReason(entry);
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
   * @param {() => Promise<unknown>} [again] what the reload buttons do; the page re-read by default
   * @returns {HTMLElement}
   */
  function refusal(error, again = reread) {
    // GOODS-AND-DRAWER-009 (review M2): the goods may not leave yet -- take the money, not a reload.
    if (paysFirst(error)) {
      return errorNotice(error, {
        actions: [
          button({
            label: "Thu tiền",
            variant: "primary",
            data: { payFirst: "true" },
            onClick: () => void payFromRefusal(),
          }),
        ],
      });
    }
    const reload = button({
      label: "Đơn vừa đổi — tải lại",
      icon: "refresh",
      onClick: () => void again(),
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
          button({ label: "Tải lại đơn", icon: "refresh", onClick: () => void again() }),
        ],
      });
    }
    return errorNotice(error, {
      actions:
        error?.kind === "CONFLICT"
          ? [button({ label: "Tải lại đơn", icon: "refresh", onClick: () => void again() })]
          : [],
    });
  }

  /**
   * "Thu tiền" from a pay-first refusal -- on the page or inside a sheet, which it closes: the
   * order as it is now, then its payment sheet (or "Tất toán" when nothing is left to take).
   */
  async function payFromRefusal() {
    openSheet?.close();
    const view = await reread();
    if ((view?.next_steps || []).some((entry) => String(entry.step) === "TAKE_PAYMENT")) {
      if (settlesWithoutMoney(view)) openSettle();
      else openPayment();
    }
  }

  // --- COUNTER-UI-RACE-009 (C4): a refusal inside a sheet -------------------------------------

  /**
   * @param {any} order
   * @param {string} step
   * @returns {boolean} whether the server still offers `step` on this order
   */
  function offers(order, step) {
    return stepEntry(order, step) !== null;
  }

  /**
   * @param {any} order
   * @param {string} step
   * @returns {any|null} the order's own `next_steps` entry for `step`, as the server listed it
   */
  function stepEntry(order, step) {
    return (
      (Array.isArray(order?.next_steps) ? order.next_steps : []).find(
        (entry) => String(entry?.step) === step,
      ) || null
    );
  }

  /**
   * What a reason sheet is built from, so a reload can tell whether the fresh entry
   * still asks the same question with the same answers (COUNTER-UI-RACE-009, C4).
   *
   * @param {any} entry
   */
  function entryShape(entry) {
    return JSON.stringify([
      entry?.requires || [],
      entry?.custody_resolutions || [],
      entry?.rewash_reasons || [],
      entry?.rejection_reasons || [],
    ]);
  }

  /**
   * The refusal shown inside a sheet. Its "tải lại" re-reads the order and then:
   *
   *   - after a refusal that wrote nothing (a stale version, a missing one, a conflict), keeps the
   *     sheet open on the fresh read -- `redraw(fresh)` puts the new figures where the old ones
   *     were and makes the next press carry the version just read, while what the person typed or
   *     picked stays -- or closes it when `redraw` answers `false` (the fresh order no longer
   *     offers what the sheet does);
   *   - after an unknown outcome (timeout, network), closes the sheet: the press may have landed,
   *     so the person reads the page before pressing anything again.
   *
   * Before this, the reload refreshed the page behind the sheet and the sheet kept the order it
   * was opened with, so every further press sent the old `If-Match` and was refused again.
   *
   * @param {any} error
   * @param {{made: {node: HTMLElement, close: () => void}, alertHost: HTMLElement,
   *   redraw?: (fresh: any) => boolean|Promise<boolean>}} where
   * @returns {HTMLElement}
   */
  function sheetRefusal(error, where) {
    const unknown = error?.kind === "TIMEOUT" || error?.kind === "NETWORK";
    return refusal(error, async () => {
      const fresh = await reread();
      if (!fresh || !where.made.node.isConnected) return;
      const kept = !unknown && (where.redraw ? await where.redraw(fresh) : true);
      if (!where.made.node.isConnected) return;
      if (!kept) {
        where.made.close();
        show(actionAlert, inlineAlert({ state: "info", title: unknown ? RELOADED_CLOSED : STEP_GONE }));
        return;
      }
      show(where.alertHost, inlineAlert({ state: "info", title: RELOADED_IN_SHEET }));
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
   * @param {{slot_approved?: boolean, custody_resolution?: string, rewash_reason?: string, rejection_reason?: string}} extra
   * @param {HTMLElement} alertHost where a refusal is shown
   * @param {HTMLButtonElement} [control]
   * @param {{node: HTMLElement, close: () => void}} [made] the sheet the press is in, if any: a
   *   refusal there reloads in place (`sheetRefusal`), and closes it when the step is gone
   * @param {(fresh: any, order: any) => void} [refit] redraws the sheet from the fresh read's entry
   *   for this step (and the fresh order it came with), for a sheet whose body is built from the
   *   entry (its requires, its reason lists) or states the order's figures
   * @returns {Promise<any|null>} the re-read order, or null when refused
   */
  async function runComposite(entry, extra, alertHost, control, made, refit) {
    // The order as last read, at send time: a reload inside the sheet has already replaced it.
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
        show(
          alertHost,
          made
            ? sheetRefusal(error, {
                made,
                alertHost,
                redraw: (fresh) => {
                  const again = stepEntry(fresh, step);
                  if (!again) return false;
                  refit?.(again, fresh);
                  return true;
                },
              })
            : refusal(error),
        );
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
        // The steps that stop or end work stay last (see `drawActions`); the receipt sits above
        // them, beside the ordinary steps.
        others
          .filter((entry) => !DESTRUCTIVE.has(String(entry.step)))
          .map((entry) => stepControl(entry, { inMore: true })),
        receiptControl("In phiếu"),
        others
          .filter((entry) => DESTRUCTIVE.has(String(entry.step)))
          .map((entry) => stepControl(entry, { inMore: true })),
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
                  made,
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

  /**
   * Nhận đồ: the one fact only the operator can give, asked for explicitly -- and, since
   * PROMISE-001, the promised-ready time the server will store, shown before the press.
   */
  function openReceive(entry) {
    const alertHost = h("div");
    const promise = receivePromise({ orderId, onChange: () => sync() });
    const confirm = button({
      label: stepVi("RECEIVE"),
      variant: "primary",
      block: true,
      network: true,
      disabled: true,
      id: "receive-submit",
      onClick: async () => {
        const done = await runComposite(
          entry,
          { slot_approved: tick.checked, ...promise.extra() },
          alertHost,
          confirm,
          made,
        );
        if (done) made.close();
      },
    });
    const tick = /** @type {HTMLInputElement} */ (
      h("input", {
        type: "checkbox",
        id: "receive-slot",
        onChange: () => sync(),
      })
    );
    function sync() {
      // A denied role's button stays shut whatever is ticked (`gated` owns that).
      if (writeVerdict.allowed) confirm.disabled = !tick.checked || !promise.ready();
    }
    void promise.load();
    const made = openFresh({
      id: "order-receive",
      title: "Nhận đồ",
      body: h(
        "div",
        { class: "stack" },
        promise.node,
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
   * Hẹn lại (PROMISE-001): a new day and hour the customer is told, with a reason. The first
   * promise never moves -- the owner's on-time figure counts against it -- so this changes only
   * "what the customer was last told". The server checks the time (later than now, open day,
   * opening hours) and refuses by name.
   */
  function openPromiseChange() {
    // The order whose promise the sheet shows; a reload inside the sheet replaces it (C4).
    let shown = current;
    if (!shown) return;
    const alertHost = h("div");
    let reason = "";
    const picker = /** @type {HTMLInputElement} */ (
      h("input", {
        type: "datetime-local",
        id: "promise-change-at",
        step: "900",
        value: currentPromiseInput(shown),
        onInput: () => sync(),
      })
    );
    const note = /** @type {HTMLInputElement} */ (
      h("input", {
        type: "text",
        id: "promise-change-note",
        maxLength: 120,
        placeholder: "Vài chữ, ví dụ: khách đi công tác",
        onInput: () => sync(),
      })
    );
    const noteRow = h(
      "label",
      { class: "promise-field", for: "promise-change-note" },
      h("span", { class: "promise-field__label" }, "Ghi chú (bắt buộc khi chọn Khác)"),
      note,
    );
    const save = button({
      label: "Lưu giờ hẹn mới",
      variant: "primary",
      block: true,
      network: true,
      disabled: true,
      id: "promise-change-submit",
      onClick: async () => {
        const body = {
          promise_at: shopInstantFromInput(picker.value),
          reason,
          ...(note.value.trim() ? { note: note.value.trim() } : {}),
        };
        render(alertHost);
        await pressing(save, async () => {
          try {
            await request(`/internal/v1/orders/${id}/promise`, {
              method: "POST",
              body,
              idempotencyKey: keyFor(`PROMISE|${shown.row_version}|${JSON.stringify(body)}`),
              ifMatch: shown.row_version,
            });
            releaseKey();
            toast(`Đã hẹn lại · ${promiseTime(body.promise_at)}`);
            made.close();
            await reread();
          } catch (error) {
            show(
              alertHost,
              sheetRefusal(error, {
                made,
                alertHost,
                redraw: (fresh) => {
                  if (!promiseOpen(fresh)) return false;
                  shown = fresh;
                  render(nowHost, promiseNow());
                  return true;
                },
              }),
            );
          }
        });
      },
    });
    function sync() {
      const ready =
        Boolean(shopInstantFromInput(picker.value)) &&
        Boolean(reason) &&
        (reason !== "OTHER" || Boolean(note.value.trim()));
      if (writeVerdict.allowed) save.disabled = !ready;
    }
    const promiseNow = () =>
      `Đang hẹn: ${promiseTime(shown.current_promise_at)}. Hẹn đầu vẫn được giữ để tính đúng hẹn.`;
    const nowHost = h("p", { class: "hint", dataField: "promise-now" }, promiseNow());
    const made = openFresh({
      id: "order-promise-change",
      title: "Hẹn lại",
      body: h(
        "div",
        { class: "stack" },
        nowHost,
        h(
          "label",
          { class: "promise-field", for: "promise-change-at" },
          h("span", { class: "promise-field__label" }, "Giờ trả mới (08:00–20:00)"),
          picker,
        ),
        h(
          "div",
          { class: "promise-field promise-reasons" },
          h("span", { class: "promise-field__label" }, "Lý do"),
          choiceChips({
            label: "Vì sao hẹn lại",
            name: "promise-change-reason",
            options: Object.entries(PROMISE_REASON_VI).map(([value, label]) => ({
              value,
              label,
              title: value,
            })),
            onChange: (value) => {
              reason = value;
              sync();
            },
          }),
        ),
        noteRow,
        alertHost,
      ),
      actions: gated(save, writeVerdict),
    });
  }

  /**
   * Thu tiền (`PAYMENT-001`, `DEC-035`): a deposit, a part payment or the rest, in cash or by
   * transfer, on `POST /orders/{id}/payments` with the version read (`If-Match`).
   *
   * The server's `remaining_vnd` is the amount unless the person taps "Khách trả một phần" and
   * types another; the console parses the typed text and compares nothing. When the server says
   * the settling payment may also hand the goods over (`payment_may_hand_over`), "Khách lấy đồ
   * luôn" is offered, ticked, beside the full amount -- the old "Thu tiền" did both in one press.
   *
   * COUNTER-UI-RACE-009. The QR asks for exactly the amount the payment will record: the whole
   * remaining, or -- while "một phần" is open -- the typed amount, which the server checks against
   * what remains (no QR while nothing usable is typed) (C3). The sheet shows one order read
   * (`shown`) and sends that read's version; "tải lại" after a stale refusal replaces it and
   * redraws the figures and the QR in place, so the next press can land (C4).
   */
  function openPayment() {
    let shown = current;
    if (!shown) return;
    const alertHost = h("div");
    let editing = false;
    let typed = "";
    let method = "TIEN_MAT";
    let seen = false;
    /**
     * The amount the payment would record when "Đã thấy tiền vào tài khoản" was last valid: the
     * tick is said of that amount, and any other one asks it again (round-9 verifier, P2).
     * @type {number|null|undefined}
     */
    let seenFor;
    // Shown under the tick once a change of amount has cleared it.
    const seenNote = h("p", { class: "hint", id: "payment-seen-cleared", role: "status" });
    let reference = "";
    let handOver = shown.payment_may_hand_over === true;
    // VIETQR-001: read when Chuyển khoản is shown, for exactly the amount the payment will record
    // (COUNTER-UI-RACE-009, C3); the amount field stays editable.
    const transferQr = paymentQr(orderId);

    const field = moneyInput({
      id: "payment-amount",
      label: "Số tiền khách trả lần này",
      placeholder: "Ví dụ 50.000",
      echo: (text) => {
        const parsed = parseDong(text);
        return text.trim() && parsed !== null ? `= ${money(parsed)}` : "";
      },
      onInput: (text) => {
        typed = text;
        askQr();
      },
    });
    const amountHost = h("div", { class: "stack stack--tight" });
    const handOverHost = h("div");
    const transferHost = h("div", { class: "stack stack--tight" });

    function drawAmount() {
      render(
        amountHost,
        editing
          ? h(
              "div",
              { class: "stack stack--tight" },
              h("label", { for: "payment-amount", class: "field-label" }, "Khách trả lần này"),
              field.node,
              button({
                label: `Thu đủ ${money(shown.remaining_vnd)}`,
                variant: "quiet",
                id: "payment-full",
                onClick: () => {
                  editing = false;
                  typed = "";
                  field.input.value = "";
                  drawAmount();
                },
              }),
            )
          : button({
              label: "Khách trả một phần (đặt cọc)",
              variant: "quiet",
              id: "payment-edit",
              onClick: () => {
                editing = true;
                drawAmount();
                setTimeout(() => field.input.focus(), 30);
              },
            }),
      );
      drawHandOver();
      askQr();
    }

    /**
     * The amount the QR is for: the whole remaining (null), or while "một phần" is open the typed
     * amount as the server will read it -- undefined, and no QR, while nothing usable is typed.
     */
    function askQr() {
      const part = editing ? parseDong(typed) : null;
      unseeIfMoved(editing ? part : shown.remaining_vnd);
      transferQr.ask(!editing ? null : part === null ? undefined : part);
    }

    /**
     * The amount the press would record changed ("một phần" opened or closed, the part retyped, a
     * reload): "Đã thấy tiền" was said of the one before, so it is asked again -- never sent beside
     * an amount nobody looked for in the account.
     *
     * @param {number|null} amount
     */
    function unseeIfMoved(amount) {
      if (amount === seenFor) return;
      seenFor = amount;
      if (!seen) return;
      seen = false;
      const tick = transferHost.querySelector("#payment-transfer-seen");
      if (tick instanceof HTMLInputElement) tick.checked = false;
      seenNote.textContent = "Số tiền vừa đổi — xem lại tài khoản rồi đánh dấu lại.";
    }

    function drawHandOver() {
      if (editing || shown?.payment_may_hand_over !== true) {
        render(handOverHost);
        return;
      }
      const tick = h("input", {
        type: "checkbox",
        id: "payment-hand-over",
        checked: handOver,
        onChange: (event) => {
          handOver = /** @type {HTMLInputElement} */ (event.target).checked;
        },
      });
      render(
        handOverHost,
        h(
          "label",
          { class: "check-line", for: "payment-hand-over" },
          tick,
          h("span", null, "Khách lấy đồ luôn"),
        ),
      );
    }

    function drawTransfer() {
      if (method !== "CHUYEN_KHOAN") {
        transferQr.hide();
        render(transferHost);
        return;
      }
      const tick = h("input", {
        type: "checkbox",
        id: "payment-transfer-seen",
        checked: seen,
        onChange: (event) => {
          seen = /** @type {HTMLInputElement} */ (event.target).checked;
          if (seen) seenNote.textContent = "";
        },
      });
      const ref = h("input", {
        id: "payment-bank-ref",
        type: "text",
        class: "input",
        autocomplete: "off",
        autocapitalize: "characters",
        maxlength: "40",
        placeholder: "Mã giao dịch (tuỳ chọn)",
        "aria-label": "Mã giao dịch, vài số cuối, không bắt buộc",
        value: reference,
        onInput: (event) => {
          reference = /** @type {HTMLInputElement} */ (event.target).value;
        },
      });
      transferQr.show();
      render(
        transferHost,
        transferQr.node,
        h(
          "label",
          { class: "check-line", for: "payment-transfer-seen" },
          tick,
          h("span", null, "Đã thấy tiền vào tài khoản"),
        ),
        seenNote,
        ref,
      );
    }

    const submit = button({
      label: "Ghi nhận đã thu",
      variant: "primary",
      block: true,
      network: true,
      id: "payment-submit",
      onClick: () => void send(),
    });

    async function send() {
      const amount = editing ? parseDong(typed) : shown.remaining_vnd;
      if (amount === null || amount === undefined) {
        show(
          alertHost,
          inlineAlert({
            state: "danger",
            title: editing
              ? "Gõ số tiền khách trả, số nguyên đồng — ví dụ 50.000. Không nhận dấu phẩy hay số lẻ."
              : "Đơn chưa có số còn lại để thu. Tải lại đơn.",
          }),
        );
        return;
      }
      const transfer = method === "CHUYEN_KHOAN";
      const body = {
        amount_vnd: amount,
        method,
        transfer_seen: transfer && seen,
        bank_ref_last: transfer && reference.trim() ? reference.trim() : null,
        collected_by_customer: !editing && handOver && shown?.payment_may_hand_over === true,
      };
      render(alertHost);
      await pressing(submit, async () => {
        try {
          const recorded = await request(`/internal/v1/orders/${id}/payments`, {
            method: "POST",
            body,
            ifMatch: shown?.row_version,
            // A changed amount, method or tick is a new intent; the same press after a timeout
            // replays the first answer.
            idempotencyKey: keyFor(`pay|${shown?.row_version}|${JSON.stringify(body)}`),
          });
          releaseKey();
          toast(`Đã thu ${money(recorded.amount_vnd)} · ${orderName(shown)}`);
          const view = await reread();
          successState(
            made,
            `Đã ghi nhận ${money(recorded.amount_vnd)} · ${
              PAYMENT_METHOD_VI[recorded.method] || recorded.method
            }.`,
            // Read from the response, not asserted: who has the goods is the settlement's shape.
            recorded.balance_status === "PAID"
              ? recorded.self_collection_recorded
                ? "Đã trả đủ. Đã ghi khách nhận đồ."
                : recorded.settlement_shape === PREPAID_DELIVERY
                  ? "Đã trả đủ. Đơn đóng khi có một chuyến giao thành công."
                  : "Đã trả đủ. Khi đưa đồ cho khách, bấm “Khách đã nhận đồ”."
              : `Còn lại ${money(recorded.remaining_vnd)}.`,
            view,
          );
        } catch (error) {
          // A refusal is shown as the server gave it -- "trả lại tiền thừa cho khách" for an
          // amount above what remains -- and the key is kept so an unchanged resend replays.
          show(
            alertHost,
            sheetRefusal(error, {
              made,
              alertHost,
              redraw: (fresh) => {
                // A fresh order with nothing left to take is settled by "Tất toán", its own sheet
                // (MONEY-LIFECYCLE-009): this one closes rather than offer "Thu đủ 0 ₫".
                if (!offers(fresh, "TAKE_PAYMENT") || settlesWithoutMoney(fresh)) return false;
                // "Đã thấy tiền" was said of the figures read before. Money recorded meanwhile
                // (maybe this very transfer, from another phone) changes them: the tick is asked
                // again, never carried over to an amount nobody looked for (C4).
                if (fresh.paid_vnd !== shown.paid_vnd || fresh.remaining_vnd !== shown.remaining_vnd) {
                  seen = false;
                  const tick = made.node.querySelector("#payment-transfer-seen");
                  if (tick instanceof HTMLInputElement) tick.checked = false;
                }
                shown = fresh;
                drawHero();
                drawAmount();
                transferQr.reload();
                return true;
              },
            }),
          );
        }
      });
    }

    const heroHost = h("div", { dataField: "payment-hero" });
    function drawHero() {
      render(
        heroHost,
        moneyHero({
          label: "Còn lại",
          amount: money(shown.remaining_vnd, "Chưa có tổng"),
          caption:
            [
              shown.paid_vnd && shown.owed_vnd !== null
                ? `Tổng ${money(shown.owed_vnd)} · đã trả ${money(shown.paid_vnd)}`
                : null,
              // UNCLAIMED-001: what "Còn lại" includes, in the server's figure.
              storageChargeLine(shown),
            ]
              .filter(Boolean)
              .join(" · ") || null,
        }),
      );
    }

    const made = openFresh({
      id: "order-payment",
      title: "Thu tiền",
      body: h(
        "div",
        { class: "stack" },
        heroHost,
        amountHost,
        h(
          "div",
          { class: "stack stack--tight" },
          h(
            "div",
            { class: "fact-line" },
            h("p", { class: "field-label" }, "Khách trả bằng"),
            infoButton("Quầy thu tiền thế nào?", h("p", null, SETTLEMENT_RULE.guardrail)),
          ),
          segmented({
            label: "Khách trả bằng",
            id: "payment-method",
            options: [
              { value: "TIEN_MAT", label: PAYMENT_METHOD_VI.TIEN_MAT },
              { value: "CHUYEN_KHOAN", label: PAYMENT_METHOD_VI.CHUYEN_KHOAN },
            ],
            value: method,
            onChange: (value) => {
              method = value;
              drawTransfer();
            },
          }),
          transferHost,
        ),
        handOverHost,
        h("p", { class: "hint" }, "Khách đưa dư thì trả lại tiền thừa. Ghi rồi không sửa được."),
        alertHost,
      ),
      actions: gated(submit, writeVerdict),
    });
    drawHero();
    drawAmount();
    drawTransfer();
  }

  /**
   * Tất toán (`MONEY-LIFECYCLE-009`, M1/A2): the ledger already covers everything the order owes
   * but the balance still reads partly paid. 0 ₫ on the payments route settles it -- no money is
   * taken and no ledger row is written -- with "Khách lấy đồ luôn" when the server says the goods
   * may be handed over in the same press. Every figure shown is the server's.
   *
   * COUNTER-UI-RACE-009 (C4): the sheet shows one order read (`shown`) and sends that read's
   * version; "tải lại" after a stale refusal replaces it, redraws the figures and the hand-over
   * tick in place, and closes the sheet when the fresh order no longer settles at 0 ₫.
   */
  function openSettle() {
    let shown = current;
    if (!shown) return;
    const alertHost = h("div");
    const heroHost = h("div", { dataField: "settle-hero" });
    const tickHost = h("div");
    let handOver = shown.payment_may_hand_over === true;
    const submit = button({
      label: SETTLE_LABEL,
      variant: "primary",
      block: true,
      network: true,
      id: "settle-submit",
      onClick: () => void send(),
    });

    function drawHero() {
      render(
        heroHost,
        moneyHero({
          label: "Còn lại",
          amount: money(shown.remaining_vnd),
          caption: `Tổng ${money(shown.owed_vnd)} · đã trả ${money(shown.paid_vnd)}`,
          state: "ok",
        }),
      );
    }

    /**
     * "Khách lấy đồ luôn" while the server says the goods may leave with this press. A tick
     * already on screen keeps its answer; one newly offered starts ticked, as when the sheet opens.
     */
    function drawTick() {
      if (shown.payment_may_hand_over !== true) {
        handOver = false;
        render(tickHost);
        return;
      }
      if (tickHost.firstChild) return;
      handOver = true;
      render(
        tickHost,
        h(
          "label",
          { class: "check-line", for: "settle-hand-over" },
          h("input", {
            type: "checkbox",
            id: "settle-hand-over",
            checked: handOver,
            onChange: (event) => {
              handOver = /** @type {HTMLInputElement} */ (event.target).checked;
            },
          }),
          h("span", null, "Khách lấy đồ luôn"),
        ),
      );
    }

    async function send() {
      // The order the sheet shows: after "Đơn vừa đổi — tải lại" it is the version just read.
      const now = shown;
      const body = {
        amount_vnd: 0,
        method: "TIEN_MAT",
        transfer_seen: false,
        bank_ref_last: null,
        collected_by_customer: handOver && now.payment_may_hand_over === true,
      };
      render(alertHost);
      await pressing(submit, async () => {
        try {
          const recorded = await request(`/internal/v1/orders/${id}/payments`, {
            method: "POST",
            body,
            ifMatch: now.row_version,
            idempotencyKey: keyFor(`settle|${now.row_version}|${JSON.stringify(body)}`),
          });
          releaseKey();
          toast(`Đã tất toán · ${orderName(now)}`);
          const view = await reread();
          successState(
            made,
            "Đã tất toán — không thu thêm tiền.",
            recorded.self_collection_recorded
              ? "Đã ghi khách nhận đồ."
              : "Khi đưa đồ cho khách, bấm “Khách đã nhận đồ”.",
            view,
          );
        } catch (error) {
          show(
            alertHost,
            sheetRefusal(error, {
              made,
              alertHost,
              redraw: (fresh) => {
                // Settling is 0 ₫ only while the fresh read says nothing is left to take.
                if (!offers(fresh, "TAKE_PAYMENT") || !settlesWithoutMoney(fresh)) return false;
                shown = fresh;
                drawHero();
                drawTick();
                return true;
              },
            }),
          );
        }
      });
    }

    drawHero();
    drawTick();
    const made = openFresh({
      id: "order-settle",
      title: SETTLE_LABEL,
      body: h(
        "div",
        { class: "stack" },
        heroHost,
        inlineAlert({
          state: "ok",
          title: "Khách đã trả đủ — bấm “Tất toán”, không thu thêm tiền.",
        }),
        tickHost,
        alertHost,
      ),
      actions: gated(submit, writeVerdict),
    });
  }

  /** Khách đã nhận đồ: the pickup of an order paid in advance at the counter (`DEC-032`). */
  function openCollect() {
    // The order read the press is against; a reload inside the sheet replaces it (C4).
    let shown = current;
    if (!shown) return;
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
              ifMatch: shown.row_version,
              idempotencyKey: keyFor(`collect|${shown.row_version}`),
            });
            releaseKey();
            toast(`${ORDER_STEP_DONE_VI.COLLECT} · ${orderName(shown)}`);
            const view = await reread();
            successState(made, "Đã ghi nhận khách nhận đồ.", null, view);
          } catch (error) {
            show(
              alertHost,
              sheetRefusal(error, {
                made,
                alertHost,
                redraw: (fresh) => {
                  if (!offers(fresh, "COLLECT")) return false;
                  shown = fresh;
                  return true;
                },
              }),
            );
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
    let shown = current;
    if (!shown) return;
    const kind = String(entry.step) === "DELIVERY_PICKUP" ? "PICKUP" : "RETURN";
    const alertHost = h("div");
    // SHOP-CAPTURE-001: the trip's cost, optional, on the same press (DEC-038).
    const trip = tripFields();
    void loadCapture().then((capture) => trip.setCapture(capture));
    /** @param {"SUCCEEDED"|"FAILED"} outcome @param {HTMLButtonElement} control */
    async function record(outcome, control) {
      render(alertHost);
      const cost = trip.read();
      if (cost.problem) {
        show(alertHost, inlineAlert({ state: "warn", title: "Chưa ghi được", body: cost.problem }));
        return;
      }
      await pressing(control, async () => {
        try {
          const recorded = await request(`/internal/v1/orders/${id}/delivery-legs`, {
            method: "POST",
            body: { leg_kind: kind, outcome, ...cost.fields },
            idempotencyKey: keyFor(
              `leg|${kind}|${outcome}|${shown.row_version}|${trip.intent()}`,
            ),
          });
          releaseKey();
          toast(`${ORDER_STEP_DONE_VI[String(entry.step)]} · ${orderName(shown)}`);
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
          show(
            alertHost,
            sheetRefusal(error, {
              made,
              alertHost,
              redraw: (fresh) => {
                if (!offers(fresh, String(entry.step))) return false;
                shown = fresh;
                return true;
              },
            }),
          );
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
        trip.node,
        alertHost,
      ),
      actions: h("div", { class: "btn-stack" }, gated(ok, writeVerdict), gated(failed, writeVerdict)),
    });
  }

  // --- SHOP-CAPTURE-001 (DEC-038): the machine at Bắt đầu giặt, the trip cost on a leg ------------

  /** The order's wash machines, read once per page; [] when the list cannot be read. */
  let washMachines = /** @type {Promise<any[]>|null} */ (null);
  function machinesOnce() {
    if (!washMachines) {
      washMachines = loadWashMachines(String(current?.store_id || storeId())).catch(() => []);
    }
    return washMachines;
  }

  /**
   * Bắt đầu giặt: "Máy nào?" as big buttons, then the step with the chosen machine, or without
   * one on "Bỏ qua". With no machine on the list (none seeded yet, or the list could not be read)
   * the step runs as it always did: capture is optional, never a gate on the wash.
   *
   * @param {any} entry
   */
  function openMachine(entry) {
    const alertHost = h("div");
    const pickHost = h("div", null, skeletonRows(2));
    const made = openFresh({
      id: "order-machine",
      title: stepVi("START_WASH"),
      body: h("div", { class: "stack" }, pickHost, alertHost),
    });
    void machinesOnce().then((machines) => {
      if (!made.node.isConnected) return;
      if (!machines.length) {
        made.close();
        void runComposite(entry, {}, actionAlert);
        return;
      }
      render(
        pickHost,
        gatedFields(
          machinePicker({
            machines,
            onPick: async (machineId, control) => {
              const done = await runComposite(
                entry,
                machineId ? { machine_id: machineId } : {},
                alertHost,
                control,
                made,
              );
              if (done) {
                washMachines = null;
                made.close();
              }
            },
          }),
          writeVerdict,
        ),
      );
    });
  }

  /**
   * UNCLAIMED-001: the order's storage facts -- a side read, re-read after every write. The waiver,
   * the contact sheet and thanh lý re-read the order when they land.
   *
   * @param {any} order
   */
  async function loadStorage(order) {
    const storage = await readStorage(orderId);
    render(
      storageHost,
      storage
        ? storageSection({
            order,
            storage,
            sheetsHost,
            title: orderName(order),
            onChanged: () => void reread(),
            reread: () => reread(),
          })
        : null,
    );
  }

  /**
   * EINVOICE-REQUEST-001 (`DEC-040`): *Hóa đơn* — the order's invoice request, and *Khách cần hóa
   * đơn*. Read from the order's own store; a role that does not read requests sees nothing here.
   *
   * @param {any} order
   */
  async function loadInvoice(order) {
    if (!can(me, "INVOICES_READ").allowed) return;
    const store = encodeURIComponent(String(order.store_id || storeId()));
    const made = invoiceSection({
      readPath: `/internal/v1/stores/${store}/orders/${id}/invoice`,
      createPath: `/internal/v1/stores/${store}/orders/${id}/invoice-requests`,
      store: String(order.store_id || storeId()),
      title: orderName(order),
      sheetsHost,
    });
    render(invoiceHost, made.node);
    await made.load();
  }

  /** What the order recorded: its cycles and trip costs, re-read after every write. */
  async function loadCapture() {
    try {
      const capture = await request(`/internal/v1/orders/${id}/capture`);
      const rows = captureRows(capture);
      render(
        captureHost,
        rows ? section({ title: "Máy và chi phí chuyến", card: false, children: rows }) : null,
      );
      return capture;
    } catch {
      // A side read: the order page stands without it, as it did before it existed.
      render(captureHost);
      return null;
    }
  }

  /**
   * Huỷ đơn. When the server says the cancellation goes through review it also lists exactly the
   * custody answers it will accept (`DEC-024`), each dry-run; only those are offered.
   *
   * @param {any} entry
   */
  function openCancel(entry) {
    const alertHost = h("div");
    const questionHost = h("div");
    const refundHost = h("div");
    const moneyHost = h("div", { dataField: "cancel-money" });
    /** The entry the sheet shows: the one it opened with, or the one a reload brought (C4). */
    let shown = entry;
    let custody = "";
    // GOODS-AND-DRAWER-009 (review M4): money goes back, so the server asks how.
    let refundMethod = "";
    /** @param {string} field */
    const asks = (field) => Array.isArray(shown.requires) && shown.requires.includes(field);
    const needs = () => asks("custody_resolution");
    const refunds = () => asks("refund_method");
    /** Every answer the server asks for is given (and nothing it no longer asks is counted). */
    const ready = () => (!needs() || custody !== "") && (!refunds() || refundMethod !== "");
    const sync = () => {
      confirm.disabled = !writeVerdict.allowed || !ready();
    };
    const confirm = confirmButton({
      label: "Huỷ đơn",
      confirmLabel: "Bấm lần nữa để huỷ đơn",
      block: true,
      onConfirm: async () => {
        // The answers the shown entry asks for, exactly: never one it does not ask.
        const done = await runComposite(
          shown,
          {
            ...(needs() ? { custody_resolution: custody } : {}),
            ...(refunds() ? { refund_method: refundMethod } : {}),
          },
          alertHost,
          confirm,
          made,
          refit,
        );
        if (done) made.close();
      },
    });

    /** The custody question, nothing picked. */
    function drawQuestion() {
      const choices = Array.isArray(shown.custody_resolutions) ? shown.custody_resolutions : [];
      custody = "";
      render(
        questionHost,
        needs()
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
                  sync();
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
      );
      sync();
    }

    /**
     * "Trả lại tiền cho khách bằng", asked exactly while the shown entry requires it. A field
     * already on screen is kept with what was picked; one no longer asked is removed and its
     * answer dropped, so the press never sends a method the server did not ask for.
     */
    function drawRefund() {
      if (!refunds()) {
        refundMethod = "";
        render(refundHost);
      } else if (!refundHost.firstChild) {
        refundMethod = "";
        render(
          refundHost,
          refundMethodField((value) => {
            refundMethod = value;
            sync();
          }),
        );
      }
      sync();
    }

    /**
     * MONEY-LIFECYCLE-009 (DEC-045/046): stated before the press, in the server's words -- always
     * from the order as last read, so after "tải lại" it is the fresh figure, never the old one.
     *
     * @param {any} order
     */
    function drawMoney(order) {
      render(moneyHost, cancellationMoneyBlock(order, "PREVIEW"));
    }

    /**
     * After "tải lại": the same custody question with the same answers keeps what was picked; a
     * different one (the fresh order now asks where the goods and money went, or offers other
     * answers) is drawn afresh, nothing picked (COUNTER-UI-RACE-009, C4). The refund method is
     * kept while the fresh entry still asks for it, and the money preview is redrawn from the
     * fresh order whatever else changed.
     *
     * @param {any} fresh the fresh order's CANCEL entry
     * @param {any} order the fresh order
     */
    function refit(fresh, order) {
      const shape = (/** @type {any} */ of) =>
        JSON.stringify([
          Array.isArray(of?.requires) && of.requires.includes("custody_resolution"),
          of?.custody_resolutions || [],
        ]);
      const same = shape(fresh) === shape(shown);
      shown = fresh;
      if (!same) drawQuestion();
      drawRefund();
      drawMoney(order);
    }

    drawQuestion();
    drawRefund();
    drawMoney(current);
    const made = openFresh({
      id: "order-cancel",
      title: "Huỷ đơn",
      body: h("div", { class: "stack" }, questionHost, moneyHost, refundHost, alertHost),
      actions: gated(confirm, writeVerdict),
    });
  }

  /**
   * Giặt lại / Không nhận đồ (ORDER-STEPS-002). Each needs the person's reason, picked from exactly
   * the reasons the server listed for this order; the press stays shut until one is picked. The
   * refusal ends the order, so it is red and takes two presses, as Huỷ đơn does.
   *
   * @param {any} entry
   */
  function openReason(entry) {
    const step = String(entry.step);
    const spec = REASON_STEPS[step];
    /** The entry the sheet shows: the one it opened with, or the one a reload brought (C4). */
    let shown = entry;
    const alertHost = h("div");
    const reasonHost = h("div");
    // Không nhận đồ ends the order without charge: the same statement as Huỷ đơn, redrawn from
    // the fresh order after "tải lại" (MONEY-LIFECYCLE-009 x COUNTER-UI-RACE-009).
    const moneyHost = h("div", { dataField: "reason-money" });
    const drawMoney = (/** @type {any} */ order) =>
      render(moneyHost, DESTRUCTIVE.has(step) ? cancellationMoneyBlock(order, "PREVIEW") : null);
    let reason = "";
    // SHOP-CAPTURE-001: a rewash opens a new wash cycle; the machine is asked, never required.
    let machine = "";
    const machineHost = h("div");
    if (step === "REWASH") {
      void machinesOnce().then((machines) => {
        if (!machines.length) return;
        render(
          machineHost,
          machineChips({
            machines,
            onChange: (value) => {
              machine = value;
            },
          }),
        );
      });
    }
    const send = async () => {
      const done = await runComposite(
        shown,
        { [spec.field]: reason, ...(machine ? { machine_id: machine } : {}) },
        alertHost,
        confirm,
        made,
        refit,
      );
      if (done) {
        washMachines = null;
        made.close();
      }
    };
    const confirm = DESTRUCTIVE.has(step)
      ? confirmButton({
          label: stepVi(step),
          confirmLabel: "Bấm lần nữa để trả đồ và huỷ đơn",
          block: true,
          onConfirm: () => void send(),
        })
      : button({
          label: stepVi(step),
          variant: "primary",
          block: true,
          network: true,
          onClick: () => void send(),
        });
    confirm.id = "step-reason-submit";

    /** Exactly the reasons the server listed for this order; the press waits for one. */
    function drawReasons() {
      const choices = Array.isArray(shown[spec.list]) ? shown[spec.list].map(String) : [];
      reason = "";
      confirm.disabled = true;
      render(
        reasonHost,
        segmented({
          label: spec.question,
          id: "step-reason",
          value: "",
          wrap: true,
          options: choices.map((value) => ({ value, label: enumVi(value) })),
          onChange: (value) => {
            reason = value;
            if (writeVerdict.allowed) confirm.disabled = false;
          },
        }),
      );
      // The token beside each gloss, for whoever needs to quote it (spec V2 §4.1).
      for (const option of reasonHost.querySelectorAll("#step-reason [data-value]")) {
        option.setAttribute("title", String(option.getAttribute("data-value")));
      }
    }

    /**
     * After "tải lại": the same reasons keep the one picked; other reasons are drawn afresh with
     * none picked, so the next press never sends a reason the fresh order does not take (C4).
     * The money statement is the fresh order's.
     *
     * @param {any} fresh the fresh order's entry for this step
     * @param {any} order the fresh order
     */
    function refit(fresh, order) {
      const same = entryShape(fresh) === entryShape(shown);
      shown = fresh;
      if (!same) drawReasons();
      drawMoney(order);
    }

    drawReasons();
    drawMoney(current);
    const made = openFresh({
      id: "order-reason",
      title: stepVi(step),
      body: h(
        "div",
        { class: "stack" },
        h("p", { class: "field-label" }, spec.question),
        reasonHost,
        h("p", { class: "hint" }, spec.note),
        machineHost,
        moneyHost,
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
        `${entry.action}|${entry.transition_target || ""}|${entry.transition_step || ""}|` +
        `${entry.actor_type}|` +
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
    // ORDER-STEPS-002: the transition that starts a rewash or a refusal carries the step and the
    // reason staff gave, so the history reads "Giặt lại · Chưa sạch" instead of "Sản xuất · Sự cố".
    if (entry.transition_step) {
      return listRow({
        title: h(
          "span",
          {
            title:
              `${entry.action} ${entry.transition_dimension}→${entry.transition_target} ` +
              `${entry.transition_step} ${entry.transition_reason || ""}`.trim(),
          },
          `${stepVi(String(entry.transition_step))} · ${enumVi(entry.transition_reason)}`,
        ),
        meta: h(
          "span",
          { title: entry.actor_id || "" },
          `${dateTime(entry.occurred_at)} · ${enumVi(entry.actor_type)}` +
            (entry.actor_id ? "" : " —"),
        ),
      });
    }
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
      void loadCapture();
      void loadStorage(found);
      void loadInvoice(found);
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
    createdHost,
    summaryHost,
    storageHost,
    infoHost,
    invoiceHost,
    legsHost,
    captureHost,
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
          "Khoản giảm trừ là phiếu cầm tay: dùng được đúng một lần, ở cửa hàng này, cho hoá " +
            "đơn lần sau — khách không cần nhớ mã. Máy chủ không ghi hạn dùng cho khoản giảm trừ.",
        ),
        h(
          "p",
          { class: "hint" },
          "Dùng ở ＋ Nhận đồ: tính giá xong, bấm “Dùng khoản giảm trừ” trên hoá đơn rồi chọn " +
            "dòng mang số phiếu của đơn này.",
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
