/**
 * ＋ Nhận đồ — the counter's one flow from "a customer hands over laundry" to "order created"
 * (spec V2 §5.2, `CONSOLE-REDESIGN-001`).
 *
 * Three steps on one screen, state in memory, back without loss:
 *
 *   1. **Khách** — one press issues a counter ticket and opens the intake for it (`DEC-013`: nothing
 *      about the person is stored); a customer who wrote through a channel is bound by the code that
 *      conversation produced, typed under "Nhập mã thủ công" because no route searches contacts; a
 *      customer already waiting is resumed from the unconverted intakes.
 *   2. **Đồ & giá** — mode, lines, "Tính giá". The server prices; the screen shows its receipt.
 *      Editing after a price makes the next press a new *revision* carrying the revision, row
 *      version and `If-Match` of the last response. A banded service is closed inline (`DEC-029`).
 *   3. **Xác nhận** — where the customer heard of the shop, then one press: the customer's
 *      acceptance, then the order, each only after the previous succeeded.
 *
 * What this screen never does, because each has been a real defect somewhere in this console:
 *
 *   - **It asks for no value the server already returned.** The ticket, the intake, the quote id,
 *     its revision and snapshot hash, the fulfilment mode it was priced under and the moment the
 *     customer accepted are all carried from the responses (and `READ-ENRICH-001` reads when
 *     resuming). The one paste field is a remedy-credit code, which no route can supply.
 *   - **It computes no money.** Every figure is a server integer rendered by `format.money`.
 *   - **It never retries a write.** Each step has its own idempotency key, kept across a failed
 *     press (so pressing again replays rather than duplicates) and replaced only when what the step
 *     would send changes. A refusal stops the chain where it happened; the next press resumes there.
 *
 * @module screens/newOrder
 */

import { Submission, isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import {
  UUID,
  matchesFilter,
  money,
  moneyRange,
  parseDong,
  parseQuantity,
  quantity as quantityText,
} from "../core/format.js";
import { ACQUISITION_SOURCE_VI, enumVi, serviceCategoryVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  errorNotice,
  gated,
  gatedFields,
  icon,
  pricingCliffNotice,
  skeleton,
} from "../ui/components.js";
import {
  actionBar,
  button,
  emptyState,
  infoButton,
  inlineAlert,
  linkButton,
  list,
  listRow,
  page,
  progress,
  searchField,
  section,
  segmented,
  sheet,
  show,
  skeletonRows,
  stepperInput,
  toast,
} from "../ui/kit.js";
import {
  BASES,
  FULFILLMENT_MODES,
  MAX_LINES,
  NEEDS_A_HUMAN_PRICE,
  SERVICE_CODE,
  acceptFailure,
  bandCloser,
  clock,
  customerLabel,
  modeLabel,
  quantityRefusal,
  receipt,
  serviceGroups,
  serviceName,
  unitShort,
} from "../ui/quoting.js";

/**
 * `AcquisitionSource`, complete, in the order the counter hears the answers — and `UNKNOWN` last,
 * as the resting value (`ACQUISITION-ATTRIBUTION-001`). It is the default and it is styled exactly
 * like every other chip: a counter that did not ask can leave it, and a report built from answers
 * nobody gave is worse than none.
 */
const ACQUISITION_SOURCES = Object.keys(ACQUISITION_SOURCE_VI);

/** The intake list is read once for the "waiting" rows; the route caps at 100. */
const WAITING_LIMIT = 100;
/** How many waiting customers step 1 shows before "Xem tất cả". */
const WAITING_SHOWN = 5;
/** The quote list the resume path searches for an intake's quote; the route caps at 100. */
const QUOTE_SEARCH_LIMIT = 100;

/**
 * @typedef {{serviceCode: string, unit: string, quantity: string, basis: string}} Line
 */

/**
 * @param {import("../core/router.js").RouteContext} [context]
 * @returns {HTMLElement}
 */
export function render_(context) {
  const store = storeId();
  const who = principal();
  const quoteWrite = can(who, "QUOTES_WRITE");
  const orderWrite = can(who, "ORDERS_WRITE");
  const creditWrite = can(who, "INCIDENTS_WRITE");
  // One verdict for the last press, which writes both an acceptance and an order.
  const confirmVerdict = quoteWrite.allowed ? orderWrite : quoteWrite;

  const query = context?.query;
  const resumeRequest = String(query?.get("request") || "").trim();
  const resumeQuote = String(query?.get("quote") || "").trim();
  const revise = query?.get("revise") === "1";

  /**
   * Everything the flow knows, in memory only. Nothing here is persisted: a counter phone is shared.
   */
  const flow = {
    step: 1,
    /** @type {any|null} the intake, with READ-ENRICH's ticket fields */
    request: null,
    /** @type {any|null} a ticket issued by this screen whose intake is not written yet */
    ticket: null,
    mode: "SELF_DROP_SELF_COLLECT",
    distance: "",
    fee: "",
    feeAck: false,
    /** @type {Line[]} */
    lines: [],
    /** @type {any|null} the newest revision the server returned — the one a "Tính lại" revises */
    quote: null,
    /** @type {any|null} that revision's read, with its lines */
    detail: null,
    /** @type {string|null} the mode the current revision was priced under */
    quoteMode: null,
    /** Inputs changed since `quote` was priced: the receipt is no longer about them. */
    dirty: false,
    /** @type {any|null} a remedy credit applied to the current revision */
    credit: null,
    source: "UNKNOWN",
    /** @type {string|null} `customer_accepted_at` read back for the accepted revision */
    acceptedAt: null,
    acceptedRevision: 0,
    busy: false,
  };

  // --- idempotency keys, one per step -------------------------------------------------------
  const ticketSub = new Submission("counter-ticket");
  const walkInRequestSub = new Submission("order-request-walk-in");
  const channelRequestSub = new Submission("order-request-channel");
  const quoteSub = new Submission("quote-create");
  const orderSub = new Submission("order-create");
  const creditSub = new Submission("remedy-credit-redeem");
  /** @type {{key: string, sub: Submission}|null} */
  let accepting = null;
  /** The acceptance key belongs to one revision: a new revision is a different intent. */
  function acceptSubmission(quote) {
    const key = `${quote.quote_id}:${quote.revision}`;
    if (!accepting || accepting.key !== key) {
      accepting = { key, sub: new Submission(`quote-accept-${quote.quote_id}-${quote.revision}`) };
    }
    return accepting.sub;
  }

  /** @type {import("../ui/quoting.js").CatalogService[]|null} */
  let catalog = null;
  /** @type {unknown} */
  let catalogError = null;

  const body = h("div", { class: "stack new-order" });
  const progressHost = h("div");
  const pickerList = h("div", { class: "stack", id: "new-picker-list" });
  const search = searchField({
    id: "new-picker-search",
    label: "Tìm dịch vụ",
    placeholder: "Tìm dịch vụ…",
    onInput: (value) => renderPicker(value),
  });
  /** @type {number|null} which line the picker replaces the service of; null adds a line */
  let pickerTarget = null;
  const picker = sheet({
    id: "new-picker",
    title: "Chọn dịch vụ",
    body: h("div", { class: "stack" }, search.node, pickerList),
  });

  // --- step navigation ------------------------------------------------------------------------

  function go(step) {
    flow.step = step;
    draw();
    window.scrollTo({ top: 0 });
  }

  function draw() {
    render(
      progressHost,
      progress(
        [
          { label: "Khách", state: flow.step > 1 ? "done" : "current" },
          {
            label: "Đồ & giá",
            state: flow.step > 2 ? "done" : flow.step === 2 ? "current" : "todo",
          },
          { label: "Xác nhận", state: flow.step === 3 ? "current" : "todo" },
        ],
        { label: "Các bước nhận đồ" },
      ),
    );
    if (flow.step === 1) render(body, stepCustomer());
    else if (flow.step === 2) render(body, stepItems());
    else render(body, stepConfirm());
  }

  /** Start over for the next customer. The last one's intake stays in the waiting list. */
  function reset() {
    Object.assign(flow, {
      request: null,
      ticket: null,
      mode: "SELF_DROP_SELF_COLLECT",
      distance: "",
      fee: "",
      feeAck: false,
      lines: [],
      quote: null,
      detail: null,
      quoteMode: null,
      dirty: false,
      credit: null,
      source: "UNKNOWN",
      acceptedAt: null,
      acceptedRevision: 0,
    });
    for (const sub of [ticketSub, walkInRequestSub, channelRequestSub, quoteSub, orderSub]) {
      sub.reset();
    }
    accepting = null;
    render(resumeHost);
    go(1);
  }

  // ============================================================================================
  // Step 1 — Khách
  // ============================================================================================

  function stepCustomer() {
    if (flow.request) return currentCustomer();
    const alertHost = h("div");
    const walkIn = button({
      label: "Khách vãng lai — phát phiếu",
      variant: "primary",
      icon: "plus",
      block: true,
      network: true,
      id: "new-walk-in",
      onClick: () => void issueWalkIn(walkIn, alertHost),
    });
    const channelHost = h("div");
    const channelToggle = button({
      label: "Khách đã nhắn qua kênh",
      variant: "quiet",
      block: true,
      id: "new-channel-toggle",
      onClick: () => {
        channelToggle.hidden = true;
        render(channelHost, channelEntry());
        channelHost.querySelector("input")?.focus();
      },
    });
    return h(
      "div",
      { class: "stack" },
      section({
        card: false,
        children: h(
          "div",
          { class: "stack stack--tight" },
          gated(walkIn, quoteWrite),
          h(
            "div",
            { class: "fact-line" },
            h("p", { class: "hint" }, "Không lưu tên hay số điện thoại — chỉ một số phiếu."),
            infoButton(
              "Khách đi thẳng vào tiệm, chưa từng nhắn tin thì sao?",
              h(
                "p",
                { class: "hint" },
                "Bấm “Khách vãng lai — phát phiếu”. Quầy phát một số phiếu, đọc số đó cho khách, " +
                  "và lượt tiếp nhận được mở luôn cho số phiếu đó. Không cần khách nhắn tin trước, " +
                  "và không cần ghi tay.",
              ),
              h(
                "p",
                { class: "hint" },
                "Hệ thống không lưu tên, số điện thoại hay địa chỉ của khách vãng lai — chỉ một con số " +
                  "do quầy phát. Vì không có thông tin cá nhân nào được lưu nên cũng không cần xin phép " +
                  "khách điều gì. Đây là quyết định của chủ tiệm ngày 26/08/2026 (DEC-013), không phải " +
                  "một khoảng trống.",
              ),
              h(
                "p",
                { class: "hint" },
                "Bước này chỉ mở một phiếu nháp: chưa có giá, chưa hẹn giờ, chưa hứa gì với khách. " +
                  "Giá do máy chủ tính ở bước sau.",
              ),
            ),
          ),
          alertHost,
          channelToggle,
          channelHost,
        ),
      }),
      waitingSection(),
    );
  }

  /** The customer this flow is serving, when the person came back to step 1. */
  function currentCustomer() {
    return h(
      "div",
      { class: "stack" },
      ticketHero(),
      bar(
        button({ label: "Khách khác", variant: "quiet", onClick: reset, id: "new-restart" }),
        button({
          label: "Tiếp tục",
          variant: "primary",
          id: "new-continue-customer",
          onClick: () => go(flow.quote && flow.quote.status === "ACCEPTED_FINAL" ? 3 : 2),
        }),
      ),
    );
  }

  /**
   * One press: a ticket, then the intake bound to it. Two writes, two keys. A ticket that was
   * issued stays issued if the intake fails, and the next press resumes at the intake with the same
   * key — so the customer keeps the number already read to them.
   *
   * @param {HTMLButtonElement} control
   * @param {HTMLElement} alertHost
   */
  async function issueWalkIn(control, alertHost) {
    if (flow.busy) return;
    flow.busy = true;
    control.disabled = true;
    control.setAttribute("aria-busy", "true");
    render(alertHost, h("p", { class: "hint", role: "status" }, "Đang phát phiếu…"));
    try {
      if (!flow.ticket) {
        flow.ticket = await request(
          `/internal/v1/stores/${encodeURIComponent(store)}/counter-tickets`,
          { method: "POST", body: {}, idempotencyKey: ticketSub.key() },
        );
        ticketSub.reset();
      }
      const created = await request(`/internal/v1/stores/${encodeURIComponent(store)}/order-requests`, {
        method: "POST",
        body: { contact_binding_id: flow.ticket.ticket_id },
        idempotencyKey: walkInRequestSub.key(),
      });
      walkInRequestSub.reset();
      flow.request = {
        ...created,
        ticket_number: flow.ticket.ticket_number,
        ticket_issued_on: flow.ticket.issued_on,
        order_id: null,
      };
      flow.ticket = null;
      flow.busy = false;
      go(2);
    } catch (error) {
      flow.busy = false;
      control.removeAttribute("aria-busy");
      if (control.getAttribute("data-denied") !== "true") control.disabled = false;
      show(
        alertHost,
        h(
          "div",
          { class: "stack stack--tight" },
          flow.ticket
            ? inlineAlert({
                state: "warn",
                title: `Đã phát Phiếu ${flow.ticket.ticket_number}, chưa mở được lượt tiếp nhận`,
                body: "Bấm lại nút trên: phiếu giữ nguyên số, chỉ bước còn thiếu được gửi lại.",
              })
            : null,
          errorNotice(error),
        ),
      );
    }
  }

  /** A customer who wrote through a channel: the code that conversation produced. */
  function channelEntry() {
    const draft = { code: "" };
    const alertHost = h("div");
    const input = /** @type {HTMLInputElement} */ (
      h("input", {
        type: "text",
        id: "new-contact",
        autocomplete: "off",
        spellcheck: "false",
        dataFormat: "id",
        placeholder: "00000000-0000-0000-0000-000000000000",
        onInput: (event) => {
          draft.code = event.target.value.trim();
          event.target.setAttribute(
            "aria-invalid",
            draft.code && !UUID.test(draft.code) ? "true" : "false",
          );
          // Different content, different intent.
          channelRequestSub.reset();
        },
      })
    );
    const submit = button({
      label: "Ghi nhận tiếp nhận",
      variant: "primary",
      network: true,
      id: "new-contact-submit",
      onClick: async () => {
        if (!UUID.test(draft.code)) {
          show(
            alertHost,
            inlineAlert({
              state: "danger",
              title: "Mã khách chưa đúng dạng. Chép lại nguyên văn từ kênh chat của khách.",
            }),
          );
          return;
        }
        submit.disabled = true;
        render(alertHost, h("p", { class: "hint", role: "status" }, "Đang ghi nhận…"));
        try {
          const created = await request(
            `/internal/v1/stores/${encodeURIComponent(store)}/order-requests`,
            {
              method: "POST",
              body: { contact_binding_id: draft.code },
              idempotencyKey: channelRequestSub.key(),
            },
          );
          channelRequestSub.reset();
          flow.request = { ...created, ticket_number: null, order_id: null };
          go(2);
        } catch (error) {
          if (submit.getAttribute("data-denied") !== "true") submit.disabled = false;
          show(
            alertHost,
            errorNotice(error, {
              title:
                /** @type {any} */ (error).kind === "REQUIRE_HUMAN"
                  ? "Máy chủ không nhận mã này. Không có yêu cầu nào được tạo — mã lạ bị từ chối chứ không được tự tạo."
                  : undefined,
            }),
          );
        }
      },
    });
    return gatedFields(
      h(
        "div",
        { class: "stack stack--tight", id: "new-channel" },
        h(
          "div",
          { class: "fact-line" },
          h("p", { class: "hint" }, "Chưa tìm được khách theo tên hay số điện thoại."),
          infoButton(
            "Vì sao phải nhập mã?",
            h(
              "p",
              { class: "hint" },
              "Máy chủ chưa có đường nào để tìm một liên hệ theo tên, số điện thoại hay đoạn chat, " +
                "nên bảng vận hành không đoán giúp. Khách đã từng nhắn tin qua kênh chính thức thì " +
                "dùng mã sinh ra từ lần nhắn đó.",
            ),
          ),
        ),
        h(
          "details",
          { class: "manual-entry", open: true },
          h("summary", null, "Nhập mã thủ công"),
          h(
            "div",
            { class: "stack stack--tight" },
            h("label", { for: "new-contact" }, "Mã khách"),
            input,
            gated(submit, quoteWrite),
          ),
        ),
        alertHost,
      ),
      quoteWrite,
    );
  }

  /** "Tiếp tục một khách đang chờ": intakes that have not become an order yet. */
  function waitingSection() {
    const host = h("div", null, skeletonRows(2));
    const meta = h("div");
    const action = h("span");
    async function load() {
      render(host, skeletonRows(2));
      try {
        const items = await request(
          `/internal/v1/stores/${encodeURIComponent(store)}/order-requests?limit=${WAITING_LIMIT}`,
        );
        const waiting = (Array.isArray(items) ? items : []).filter((item) => !item.order_id);
        render(
          action,
          waiting.length > WAITING_SHOWN
            ? h("a", { href: "#/order-requests", class: "link-action" }, `Xem tất cả (${waiting.length})`)
            : null,
        );
        render(
          meta,
          isTruncated(items, WAITING_LIMIT)
            ? h(
                "p",
                { class: "hint" },
                `Chỉ xét ${WAITING_LIMIT} lượt tiếp nhận gần nhất; khách chờ lâu hơn xem ở Tiếp nhận.`,
              )
            : null,
        );
        render(
          host,
          waiting.length
            ? list(
                waiting.slice(0, WAITING_SHOWN).map((item) =>
                  listRow({
                    onClick: () => void resumeFromRequest(item),
                    leading: "intake",
                    title: customerLabel(item),
                    meta: `lúc ${clock(item.created_at)}`,
                    data: { request: String(item.order_request_id) },
                  }),
                ),
                { label: "Khách đang chờ", id: "new-waiting" },
              )
            : h("p", { class: "muted" }, "Không có khách nào đang chờ."),
        );
      } catch (error) {
        render(host, errorNotice(error, { onRetry: () => void load() }));
      }
    }
    void load();
    return section({
      title: "Tiếp tục một khách đang chờ",
      action,
      card: false,
      children: h("div", { class: "stack stack--tight" }, host, meta),
    });
  }

  // ============================================================================================
  // Resuming — every value from a read, none typed
  // ============================================================================================

  const resumeHost = h("div");

  /**
   * @param {any} item an order-request summary
   */
  async function resumeFromRequest(item) {
    if (item.order_id) {
      // Converted already: nothing to resume, and binding it would offer a second order.
      go(1);
      show(resumeHost, convertedAlert(item));
      return;
    }
    flow.request = item;
    render(resumeHost, skeleton(1));
    try {
      const quotes = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/quotes?limit=${QUOTE_SEARCH_LIMIT}`,
      );
      const found = (Array.isArray(quotes) ? quotes : []).find(
        (quote) => quote.order_request_id === item.order_request_id,
      );
      render(resumeHost);
      if (!found) {
        go(2);
        return;
      }
      await resumeFromQuote(String(found.quote_id), item);
    } catch (error) {
      go(1);
      show(resumeHost, errorNotice(error, { onRetry: () => void resumeFromRequest(item) }));
    }
  }

  /**
   * @param {string} quoteId
   * @param {any|null} known the intake, when the caller already read it
   */
  async function resumeFromQuote(quoteId, known) {
    render(resumeHost, skeleton(1));
    try {
      const detail = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/quotes/${encodeURIComponent(quoteId)}`,
      );
      let intake = known;
      if (!intake) {
        if (!detail.order_request_id) {
          show(
            resumeHost,
            inlineAlert({
              state: "warn",
              title: "Báo giá này không gắn với lượt tiếp nhận nào đọc được",
              body: "Không điền sẵn gì. Mở lại từ danh sách Tiếp nhận.",
              actions: linkButton({ href: "#/order-requests", label: "Tiếp nhận", variant: "quiet" }),
            }),
          );
          return;
        }
        intake = await request(
          `/internal/v1/stores/${encodeURIComponent(store)}/order-requests/${encodeURIComponent(detail.order_request_id)}`,
        );
      }
      if (intake.order_id) {
        flow.request = null;
        go(1);
        show(resumeHost, convertedAlert(intake));
        return;
      }
      flow.request = intake;
      adoptRevision(detail, detail);
      flow.lines = (detail.lines || []).map((line) => ({
        serviceCode: line.service_code,
        unit: line.unit,
        quantity: String(line.quantity),
        // The read does not carry the basis; the line shows the default and a re-price sends it.
        basis: "STAFF_MEASUREMENT",
      }));
      if (detail.fulfillment_mode) flow.mode = detail.fulfillment_mode;
      render(resumeHost);
      go(!revise && detail.status === "ACCEPTED_FINAL" ? 3 : 2);
    } catch (error) {
      go(1);
      show(
        resumeHost,
        /** @type {any} */ (error).kind === "MISSING"
          ? inlineAlert({
              state: "warn",
              title: "Không tìm thấy báo giá này trong cửa hàng đang chọn",
              body:
                "Mã có thể thuộc cửa hàng khác hoặc đã sai — máy chủ trả lời giống nhau cho cả hai, " +
                "nên màn hình này cũng không đoán. Không có gì được điền sẵn.",
            })
          : errorNotice(error, { onRetry: () => void resumeFromQuote(quoteId, known) }),
      );
    }
  }

  /** @param {any} item */
  function convertedAlert(item) {
    return inlineAlert({
      state: "info",
      title: `${customerLabel(item)} đã thành đơn`,
      body: "Lượt tiếp nhận này đã tạo đơn rồi; không tạo lại được.",
      actions: linkButton({
        href: `#/orders/${encodeURIComponent(String(item.order_id))}`,
        label: "Mở đơn",
        variant: "primary",
      }),
    });
  }

  // ============================================================================================
  // Step 2 — Đồ & giá
  // ============================================================================================

  /** The big number the customer is told. */
  function ticketHero() {
    const item = flow.request;
    const numbered = Number.isInteger(item?.ticket_number);
    return h(
      "div",
      { class: "ticket-hero", id: "new-ticket" },
      h(
        "p",
        { class: "ticket-hero__number" },
        numbered ? h("span", { class: "ticket-hero__word" }, "Phiếu") : null,
        numbered ? ` ${item.ticket_number}` : customerLabel(item),
      ),
      h(
        "p",
        { class: "ticket-hero__meta" },
        numbered
          ? `Đọc số này cho khách, ghi lên túi đồ · ${clock(item.created_at)}`
          : `Tiếp nhận lúc ${clock(item?.created_at)}`,
      ),
    );
  }

  const receiptHost = h("div", { class: "stack", id: "new-receipt" });
  const priceAlert = h("div");
  // `display: contents`, so the sticky bar inside sticks to the screen, not to this wrapper.
  const actionHost = h("div", { class: "host-contents" });
  const linesHost = h("div", { class: "stack stack--tight", id: "new-lines" });
  const addHost = h("div");

  function stepItems() {
    if (!flow.request) {
      flow.step = 1;
      return stepCustomer();
    }
    const content = h(
      "div",
      { class: "stack" },
      ticketHero(),
      h(
        "div",
        { class: "new-order__grid" },
        h(
          "div",
          { class: "stack" },
          modeSection(),
          section({
            title: "Đồ khách gửi",
            card: false,
            children: h(
              "div",
              { class: "stack stack--tight" },
              catalogError ? catalogRefusal(catalogError) : null,
              linesHost,
              addHost,
            ),
          }),
          priceAlert,
        ),
        h("div", { class: "new-order__side" }, receiptHost),
      ),
      actionHost,
    );
    drawLines();
    redrawAddLine();
    drawReceipt();
    drawActions();
    return gatedFields(content, quoteWrite);
  }

  // --- mode -----------------------------------------------------------------------------------

  function modeSection() {
    const fieldsHost = h("div", { class: "stack stack--tight" });
    const control = segmented({
      label: "Giao nhận",
      id: "new-mode",
      options: FULFILLMENT_MODES,
      value: flow.mode,
      onChange: (value) => {
        flow.mode = value;
        invalidate();
        render(fieldsHost, deliveryFields());
      },
    });
    control.classList.add("segmented--fit");
    render(fieldsHost, deliveryFields());
    return section({
      title: "Giao nhận",
      card: false,
      children: h("div", { class: "stack stack--tight" }, control, fieldsHost),
    });
  }

  /** The facts a delivery fee needs. Nothing for the walk-in case: there is no journey. */
  function deliveryFields() {
    if (flow.mode === "SELF_DROP_SELF_COLLECT") return null;
    const both = flow.mode === "PICKUP_AND_RETURN";
    const distance = h("input", {
      type: "text",
      id: "new-distance",
      inputmode: "numeric",
      autocomplete: "off",
      placeholder: "4000",
      value: flow.distance,
      onInput: (event) => {
        flow.distance = event.target.value.trim();
        invalidate();
      },
    });
    const fee = h("input", {
      type: "text",
      id: "new-fee",
      inputmode: "numeric",
      autocomplete: "off",
      placeholder: "45.000",
      value: flow.fee,
      onInput: (event) => {
        flow.fee = event.target.value;
        invalidate();
      },
    });
    const ack = h("input", {
      type: "checkbox",
      id: "new-fee-ack",
      checked: flow.feeAck,
      onChange: (event) => {
        flow.feeAck = event.target.checked;
        invalidate();
      },
    });
    return h(
      "div",
      { class: "stack stack--tight delivery-fields" },
      both
        ? h(
            "div",
            { class: "form-row" },
            h("label", { for: "new-distance" }, "Quãng đường đã đo (mét)"),
            distance,
          )
        : null,
      h(
        "div",
        { class: "fact-line" },
        h(
          "p",
          { class: "hint" },
          both
            ? "Đến 2.000 m miễn phí · trên 2.000 m đến 6.000 m: 10.000 ₫ · xa hơn: nhập phí đã thoả thuận."
            : "Một chiều: phí giao luôn do hai bên thoả thuận, ở mọi quãng đường.",
        ),
        infoButton(
          "Phí giao tính thế nào?",
          h(
            "p",
            { class: "hint" },
            "Đo thật, không ước lượng. Với đơn có cả lấy và trả: từ 2.000m trở xuống miễn phí, " +
              "trên 2.000m đến 6.000m thu 10.000đ, trên 6.000m nhân viên và khách thỏa thuận rồi " +
              "nhập ở ô phí. Bỏ trống thì báo giá không ra tổng tiền và không chốt được.",
          ),
          h(
            "p",
            { class: "hint" },
            "Đơn “Lấy tận nơi” hoặc “Trả tận nơi” chỉ có một chiều nên không dùng bảng quãng đường — " +
              "phí luôn do nhân viên và khách thỏa thuận, ở mọi quãng đường, và phải tích ô khách đã " +
              "đồng ý.",
          ),
        ),
      ),
      h(
        "div",
        { class: "form-row" },
        h("label", { for: "new-fee" }, "Phí giao đã thỏa thuận (₫)"),
        fee,
      ),
      h(
        "label",
        { class: "check", for: "new-fee-ack" },
        ack,
        h("span", null, "Khách đã đồng ý mức phí giao này"),
      ),
    );
  }

  // --- lines ----------------------------------------------------------------------------------

  function addLineButton() {
    if (flow.lines.length >= MAX_LINES) {
      return h("p", { class: "hint" }, `Tối đa ${MAX_LINES} món cho một báo giá.`);
    }
    return button({
      label: flow.lines.length ? "Thêm món" : "Chọn dịch vụ",
      icon: "plus",
      variant: flow.lines.length ? "quiet" : "secondary",
      block: true,
      id: "new-add-line",
      disabled: !catalog || !catalog.length,
      onClick: () => openPicker(null),
    });
  }

  /** Structural redraw: the set of lines changed. Never on a keystroke. */
  function drawLines() {
    render(
      linesHost,
      flow.lines.length
        ? flow.lines.map((line, index) => lineCard(line, index))
        : catalog || catalogError
          ? null
          : skeleton(1),
    );
  }

  /**
   * One line: the service (tap to change), the quantity with − and +, the basis, and the 6 kg notice
   * beside the quantity. Typing refreshes only the nodes that depend on the typed value.
   *
   * @param {Line} line
   * @param {number} index
   */
  function lineCard(line, index) {
    const prefix = `new-line-${index}`;
    const note = h("p", { class: "hint", "aria-live": "polite" });
    const cliffHost = h("div");
    const basisNote = h("p", { class: "hint" });
    const stepper = stepperInput({
      id: `${prefix}-qty`,
      label: serviceName(catalog, line.serviceCode),
      value: line.quantity,
      unit: unitShort(line.unit),
      decimal: line.unit === "KG",
      onChange: (value) => {
        if (line.quantity === value) return;
        line.quantity = value;
        refresh();
        invalidate();
      },
    });
    stepper.input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        void price(false);
      }
    });
    function refresh() {
      const typed = line.quantity.trim();
      const accepted = typed ? parseQuantity(typed, line.unit) : null;
      stepper.input.setAttribute("aria-invalid", typed && accepted === null ? "true" : "false");
      note.removeAttribute("data-state");
      if (!typed || accepted === typed) render(note);
      else if (accepted === null) {
        note.dataset.state = "danger";
        render(note, quantityRefusal(typed, line.unit));
      } else render(note, `Máy chủ sẽ nhận: ${accepted}`);
      // The notice reads the same `parseQuantity` the send path uses; an empty box says nothing.
      render(cliffHost, typed ? pricingCliffNotice(line.quantity, line.unit) : null);
    }
    const basis = /** @type {HTMLSelectElement} */ (
      h(
        "select",
        {
          id: `${prefix}-basis`,
          class: "select--compact",
          "aria-label": "Cách đo",
          onChange: (event) => {
            line.basis = event.target.value;
            refreshBasis();
            invalidate();
          },
        },
        BASES.map((value) =>
          h("option", { value, selected: value === line.basis, title: value }, enumVi(value)),
        ),
      )
    );
    function refreshBasis() {
      render(
        basisNote,
        line.basis === "CUSTOMER_ESTIMATE" ? "“Khách tự ước” không ra được giá cuối." : null,
      );
    }
    refresh();
    refreshBasis();
    return h(
      "div",
      { class: "line-card surface", dataLine: String(index), dataService: line.serviceCode },
      h(
        "div",
        { class: "line-card__head" },
        h(
          "button",
          {
            type: "button",
            class: "line-card__service",
            id: `${prefix}-service`,
            onClick: () => openPicker(index),
          },
          h("span", null, serviceName(catalog, line.serviceCode)),
          icon("chevron-right"),
        ),
        h(
          "button",
          {
            type: "button",
            class: "line-card__remove",
            "aria-label": "Bỏ món này",
            onClick: () => {
              flow.lines.splice(index, 1);
              invalidate();
              drawLines();
              redrawAddLine();
            },
          },
          icon("close"),
        ),
      ),
      h("div", { class: "line-card__qty" }, stepper.node, basis),
      note,
      basisNote,
      cliffHost,
    );
  }

  function redrawAddLine() {
    render(addHost, addLineButton());
  }

  // --- the service picker ---------------------------------------------------------------------

  /** @param {number|null} target */
  function openPicker(target) {
    pickerTarget = target;
    search.input.value = "";
    renderPicker("");
    // No autofocus: on a phone the keyboard would cover half the list, and a tap is faster.
    picker.open();
  }

  /** @param {string} needle */
  function renderPicker(needle) {
    const services = (catalog || []).filter(
      (service) => !needle.trim() || matchesFilter([service.display_name, service.code], needle.trim()),
    );
    if (!services.length) {
      render(
        pickerList,
        emptyState({ icon: "search", title: "Không có dịch vụ nào khớp", body: "Thử gõ ít chữ hơn." }),
      );
      return;
    }
    render(
      pickerList,
      serviceGroups(services).map((group) =>
        h(
          "div",
          { class: "stack stack--tight" },
          h("h3", { class: "picker__group" }, serviceCategoryVi(group.category)),
          list(
            group.services.map((service) =>
              listRow({
                onClick: () => pick(service),
                title: service.display_name,
                meta: `Tính theo ${unitShort(service.unit)}`,
                chevron: false,
                data: { code: service.code },
              }),
            ),
            { label: serviceCategoryVi(group.category) },
          ),
        ),
      ),
    );
  }

  /** @param {import("../ui/quoting.js").CatalogService} service */
  function pick(service) {
    let index = pickerTarget;
    if (index === null || !flow.lines[index]) {
      flow.lines.push({
        serviceCode: service.code,
        unit: service.unit,
        quantity: "",
        basis: "STAFF_MEASUREMENT",
      });
      index = flow.lines.length - 1;
    } else {
      const line = flow.lines[index];
      line.serviceCode = service.code;
      // The unit is the service's: every published service has exactly one.
      if (line.unit !== service.unit) line.quantity = "";
      line.unit = service.unit;
    }
    invalidate();
    picker.close();
    drawLines();
    redrawAddLine();
    // After the dialog has handed focus back to its opener: the next thing typed is the quantity.
    setTimeout(() => {
      const input = document.getElementById(`new-line-${index}-qty`);
      if (input instanceof HTMLInputElement) input.focus();
    }, 60);
  }

  // --- catalog ------------------------------------------------------------------------------

  /** @param {unknown} error */
  function catalogRefusal(error) {
    return h(
      "div",
      { class: "stack stack--tight" },
      error === "EMPTY"
        ? h(
            "div",
            { class: "notice", dataState: "warn" },
            h("p", { class: "notice__title" }, "Bảng giá chưa có dịch vụ nào"),
            h(
              "p",
              null,
              "Máy chủ trả về một bảng giá rỗng, nên không có dịch vụ nào để chọn và không thể " +
                "tính giá. Đây là vấn đề của bảng giá đã công bố, không phải của thao tác này.",
            ),
          )
        : errorNotice(/** @type {any} */ (error)),
      button({
        label: "Thử tải lại bảng giá",
        icon: "refresh",
        variant: "quiet",
        onClick: () => void loadCatalog(),
      }),
    );
  }

  async function loadCatalog() {
    try {
      const services = await request("/internal/v1/pricebook/services");
      catalog = Array.isArray(services) ? services : [];
      catalogError = catalog.length ? null : "EMPTY";
    } catch (error) {
      catalog = null;
      catalogError = error;
    }
    // Only a screen that names services needs the redraw, and never under a press in flight.
    if (flow.step !== 1 && !flow.busy) draw();
  }

  // --- pricing ------------------------------------------------------------------------------

  /**
   * Any edit: the key is replaced (the server hashes the body with it), and the receipt stops
   * being about what is on screen. Only the transition redraws anything — never a keystroke.
   */
  function invalidate() {
    quoteSub.reset();
    render(priceAlert);
    if (flow.quote && !flow.dirty) {
      flow.dirty = true;
      drawReceipt();
      drawActions();
    }
  }

  /** @returns {string} a sentence when the lines cannot be sent, "" when they can */
  function validate() {
    if (!flow.request) return "Chưa có khách: quay lại bước 1.";
    if (!flow.lines.length) return "Chưa có món nào. Bấm “Chọn dịch vụ”.";
    for (const [index, line] of flow.lines.entries()) {
      const name = serviceName(catalog, line.serviceCode);
      if (!SERVICE_CODE.test(line.serviceCode)) {
        return `Món ${index + 1}: bảng giá trả về mã dịch vụ không hợp lệ (${line.serviceCode}).`;
      }
      if (!line.quantity.trim()) return `${name}: chưa nhập khối lượng / số lượng.`;
      if (parseQuantity(line.quantity, line.unit) === null) {
        return `${name}: ${quantityRefusal(line.quantity.trim(), line.unit)}`;
      }
    }
    if (flow.mode === "PICKUP_AND_RETURN" && flow.distance && !/^\d{1,6}$/.test(flow.distance)) {
      return "Quãng đường phải là số mét nguyên, ví dụ 3500.";
    }
    if (flow.mode !== "SELF_DROP_SELF_COLLECT" && flow.fee.trim() && parseDong(flow.fee) === null) {
      return "Phí giao phải là số nguyên đồng. “10.000” đọc là 10000; không nhận dấu phẩy hay số lẻ.";
    }
    return "";
  }

  /**
   * Price the lines, or add a revision to the quote this flow already holds.
   *
   * @param {boolean} asBand whether to ask the server to store the band instead of refusing it
   */
  async function price(asBand) {
    if (flow.busy) return;
    const problem = validate();
    if (problem) {
      show(priceAlert, inlineAlert({ state: "danger", title: problem }));
      return;
    }
    const delivers = flow.mode !== "SELF_DROP_SELF_COLLECT";
    const revising = Boolean(flow.quote);
    const payload = {
      bound_order_request_id: flow.request.order_request_id,
      lines: flow.lines.map((line) => ({
        service_code: line.serviceCode,
        // The typed digits, with "," read as the decimal mark; `validate` refused anything else.
        quantity: parseQuantity(line.quantity, line.unit),
        unit: line.unit,
        quantity_basis: line.basis,
      })),
      fulfillment_mode: flow.mode,
      // Absent is not zero: a missing distance is REQUIRE_HUMAN, never a free delivery.
      ...(flow.mode === "PICKUP_AND_RETURN" && flow.distance
        ? { verified_distance_m: Number.parseInt(flow.distance, 10) }
        : {}),
      ...(delivers && flow.fee.trim() ? { approved_manual_fee_vnd: parseDong(flow.fee) } : {}),
      ...(delivers && flow.feeAck ? { customer_acknowledged_manual_fee: true } : {}),
      // Only because somebody pressed "Lập bản khoảng giá" — a different intent, a different key.
      ...(asBand ? { present_range_as_band: true } : {}),
      ...(revising
        ? { quote_id: flow.quote.quote_id, expected_current_revision: flow.quote.revision }
        : {}),
    };
    flow.busy = true;
    setPricing(true);
    render(priceAlert, h("p", { class: "hint", role: "status" }, asBand ? "Đang ghi bản khoảng giá…" : "Đang tính giá…"));
    try {
      const created = await request(`/internal/v1/stores/${encodeURIComponent(store)}/quotes`, {
        method: "POST",
        body: payload,
        idempotencyKey: quoteSub.key(),
        ...(revising ? { ifMatch: flow.quote.row_version } : {}),
      });
      quoteSub.reset();
      flow.busy = false;
      render(priceAlert);
      flow.credit = null;
      adoptRevision(created, null);
      flow.quoteMode = payload.fulfillment_mode;
      toast(`Đã tính giá · bản sửa đổi ${created.revision}`);
      drawReceipt();
      drawActions();
      void readLines(created);
    } catch (error) {
      flow.busy = false;
      setPricing(false);
      const failure = /** @type {any} */ (error);
      // A lost answer is not a refusal: the revision may exist.
      const unknown = failure.kind === "TIMEOUT" || failure.kind === "NETWORK";
      const bandable =
        !asBand &&
        failure.kind === "REQUIRE_HUMAN" &&
        (failure.reasonCodes || []).includes(NEEDS_A_HUMAN_PRICE);
      show(
        priceAlert,
        h(
          "div",
          { class: "stack stack--tight" },
          bandable ? bandOffer() : null,
          errorNotice(error, {
            title: unknown
              ? "Chưa biết lệnh có tới máy chủ hay không, nên chưa biết giá đã được ghi hay chưa. " +
                "Bấm lại “Tính giá” — lần bấm lại dùng cùng mã thao tác, nên máy chủ không ghi hai lần."
              : bandable
                ? "Bộ tính giá không tự chọn số trong khoảng giá. Không có bản ghi nào được tạo."
                : failure.kind === "REQUIRE_HUMAN"
                  ? "Bộ tính giá từ chối đoán. Không có bản ghi nào được tạo."
                  : undefined,
          }),
        ),
      );
    }
  }

  /** @param {boolean} busy */
  function setPricing(busy) {
    const primary = actionHost.querySelector("#new-price");
    if (primary instanceof HTMLButtonElement && primary.getAttribute("data-denied") !== "true") {
      primary.disabled = busy;
    }
  }

  /**
   * Hold the revision the server just returned as the one the next press revises or accepts.
   *
   * @param {any} revision
   * @param {any|null} detail its read, when in hand
   */
  function adoptRevision(revision, detail) {
    flow.quote = revision;
    flow.detail = detail;
    flow.dirty = false;
    if (detail?.fulfillment_mode) flow.quoteMode = detail.fulfillment_mode;
    // A new revision is a new thing to accept, and a new order body.
    orderSub.reset();
  }

  /** The revision's lines — a read, painted as a second pass under totals already on screen. */
  async function readLines(revision) {
    try {
      const detail = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/quotes/${encodeURIComponent(revision.quote_id)}?revision=${encodeURIComponent(String(revision.revision))}`,
      );
      if (flow.quote !== revision) return;
      flow.detail = detail;
      if (detail.fulfillment_mode) flow.quoteMode = detail.fulfillment_mode;
      currentReceipt?.setLines(detail);
      if (revision.finality === "RANGE") drawBand();
    } catch (error) {
      if (flow.quote === revision) currentReceipt?.setLines(null, error);
      if (revision.finality === "RANGE") drawBand();
    }
  }

  /**
   * The one pricing refusal with a next action the console can offer: the same lines, stored as a
   * band. The catalog carries no price kind, so this is only ever known after the engine says so.
   */
  function bandOffer() {
    const offer = button({
      label: "Lập bản khoảng giá",
      variant: "primary",
      network: true,
      id: "new-band-offer",
      onClick: () => {
        // A different payload needs a different key.
        quoteSub.reset();
        void price(true);
      },
    });
    return h(
      "div",
      { class: "notice", dataState: "warn" },
      h("p", { class: "notice__title" }, "Món này niêm yết theo khoảng giá"),
      h(
        "p",
        null,
        "Bảng giá công bố một khoảng cho món này chứ không công bố một đơn giá, nên bộ tính giá " +
          "không tự chọn một số — đó là việc của nhân viên đang trực quầy.",
      ),
      h(
        "p",
        null,
        "Bấm nút dưới đây để ghi lại đúng những dòng vừa nhập thành một bản khoảng giá. Đọc " +
          "khoảng cho khách nghe được ngay, và chốt một giá trong khoảng ở ngay bản đó.",
      ),
      h("div", { class: "form__actions" }, gated(offer, quoteWrite)),
    );
  }

  // --- the receipt ----------------------------------------------------------------------------

  /** @type {(HTMLElement & {setLines: (detail: any|null, error?: unknown) => void})|null} */
  let currentReceipt = null;
  const bandHost = h("div");
  const creditHost = h("div");

  function drawReceipt() {
    if (!flow.quote) {
      currentReceipt = null;
      render(
        receiptHost,
        h(
          "div",
          { class: "receipt-placeholder surface" },
          emptyState({
            icon: "quote",
            title: "Chưa có giá",
            body: "Chọn món, nhập khối lượng rồi bấm “Tính giá”. Máy chủ tính, màn hình này không tính.",
          }),
        ),
      );
      return;
    }
    const revision = flow.quote;
    currentReceipt = receipt({
      revision,
      detail: flow.detail,
      catalog,
      mode: flow.quoteMode || flow.mode,
      title: "Hoá đơn tạm",
      stale: flow.dirty
        ? inlineAlert({
            state: "warn",
            title: "Đã sửa sau khi tính giá",
            body: "Giá dưới đây là của lần tính trước. Bấm “Tính lại” để ra giá cho những gì đang nhập.",
          })
        : null,
      extra: creditRow(),
      actions: h("div", { class: "stack stack--tight" }, bandHost, creditHost),
    });
    render(receiptHost, currentReceipt);
    drawBand();
    drawCredit();
  }

  /** The credit spent on this revision, as the server reported it. */
  function creditRow() {
    const applied = flow.credit;
    if (!applied || applied.revision !== flow.quote?.revision) return null;
    return h(
      "div",
      { class: "stack stack--tight" },
      h(
        "div",
        { class: "receipt__row" },
        h("span", { class: "receipt__label" }, "Khoản giảm trừ"),
        h("span", { class: "receipt__value money" }, `giảm ${money(applied.credit_vnd)}`),
      ),
      h("p", { class: "receipt__note" }, "Đã trừ vào tổng. Đọc lại tổng mới cho khách nghe."),
    );
  }

  function drawBand() {
    const revision = flow.quote;
    if (!revision || revision.finality !== "RANGE" || flow.dirty) {
      render(bandHost);
      return;
    }
    if (!flow.detail || flow.detail.revision !== revision.revision) {
      render(bandHost, skeleton(1));
      return;
    }
    render(
      bandHost,
      // A band is not one price, so there is nothing for a customer to agree to yet: the server
      // refuses acceptance of a `RANGE` revision, and "Tiếp tục" stays off with this reason.
      h(
        "div",
        { class: "notice", dataState: "warn" },
        h("p", { class: "notice__title" }, "Bản này là một khoảng giá, chưa phải một số"),
        h(
          "p",
          null,
          "Đọc khoảng giá cho khách được, nhưng chưa chốt được: chưa có con số nào để khách đồng ý. " +
            "Chốt một giá trong khoảng ở ô bên dưới, rồi mới bấm “Tiếp tục”.",
        ),
      ),
      bandCloser({
        result: revision,
        store,
        catalog,
        writeVerdict: quoteWrite,
        detail: flow.detail,
        onClosed: async (closed) => {
          toast(`Đã ghi giá vào bản sửa đổi ${closed.revision}`);
          adoptRevision(closed, null);
          drawReceipt();
          drawActions();
          void readLines(closed);
        },
      }),
    );
  }

  // --- remedy credit on the receipt (spec §5.6) ------------------------------------------------

  function drawCredit() {
    const revision = flow.quote;
    const open =
      revision &&
      !flow.dirty &&
      revision.finality !== "RANGE" &&
      revision.status !== "ACCEPTED_FINAL" &&
      !(flow.credit && flow.credit.revision === revision.revision);
    render(
      creditHost,
      open
        ? gated(
            button({
              label: "Dùng khoản giảm trừ",
              icon: "tag",
              variant: "quiet",
              block: true,
              id: "new-credit-open",
              onClick: () => creditSheet.open(),
            }),
            creditWrite,
          )
        : null,
    );
  }

  const creditDraft = { code: "" };
  const creditAlert = h("div");
  const creditInput = h("input", {
    type: "text",
    id: "new-credit-code",
    autocomplete: "off",
    spellcheck: "false",
    dataFormat: "id",
    placeholder: "00000000-0000-0000-0000-000000000000",
    onInput: (event) => {
      creditDraft.code = event.target.value.trim();
      event.target.setAttribute(
        "aria-invalid",
        creditDraft.code && !UUID.test(creditDraft.code) ? "true" : "false",
      );
      creditSub.reset();
    },
  });
  const creditSubmit = button({
    label: "Áp dụng khoản giảm trừ",
    variant: "primary",
    network: true,
    block: true,
    id: "new-credit-submit",
    onClick: () => void redeemCredit(),
  });
  const creditSheet = sheet({
    id: "new-credit",
    title: "Dùng khoản giảm trừ",
    body: gatedFields(
      h(
        "div",
        { class: "stack stack--tight" },
        h(
          "div",
          { class: "fact-line" },
          h(
            "p",
            { class: "hint" },
            "Dùng đúng một lần, trừ vào tổng trước khi báo khách — không trừ vào số khách đã trả.",
          ),
          infoButton(
            "Tìm mã giảm trừ ở đâu?",
            h(
              "p",
              { class: "hint" },
              "Mã in trên phiếu giấy của khách lúc phát hành. Khách quên mã thì tìm đơn đã phát hành " +
                "khoản đó theo số phiếu ở màn hình Đơn hàng: mã nằm ở mục “Khoản giảm trừ của đơn này”.",
            ),
            h(
              "p",
              { class: "hint" },
              "Máy chủ chưa có đường nào liệt kê các khoản giảm trừ chưa dùng của một khách, nên ở đây " +
                "chỉ nhập được mã. Bản báo giá và dấu vân của nó được gửi kèm tự động từ bản đang hiện.",
            ),
          ),
        ),
        h("label", { for: "new-credit-code" }, "Mã khoản giảm trừ"),
        creditInput,
        gated(creditSubmit, creditWrite),
        creditAlert,
      ),
      creditWrite,
    ),
  });

  async function redeemCredit() {
    const revision = flow.quote;
    if (!revision) return;
    if (!UUID.test(creditDraft.code)) {
      show(creditAlert, inlineAlert({ state: "danger", title: "Mã giảm trừ phải đủ 36 ký tự, dạng 0000…-…." }));
      return;
    }
    creditSubmit.disabled = true;
    render(creditAlert, h("p", { class: "hint", role: "status" }, "Đang áp dụng…"));
    try {
      const applied = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/quotes/${encodeURIComponent(revision.quote_id)}/remedy-credits`,
        {
          method: "POST",
          body: {
            credit_id: creditDraft.code,
            // From the revision on screen: the evidence that the total being changed is the one read.
            expected_current_revision: revision.revision,
            expected_snapshot_hash: revision.snapshot_hash,
          },
          idempotencyKey: creditSub.key(),
        },
      );
      creditSub.reset();
      creditSubmit.disabled = false;
      render(creditAlert);
      creditSheet.close();
      toast(`Đã trừ khoản giảm trừ · bản sửa đổi ${applied.revision}`);
      // The credit wrote a new revision; read it so the next press carries its row version.
      const detail = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/quotes/${encodeURIComponent(revision.quote_id)}?revision=${encodeURIComponent(String(applied.revision))}`,
      );
      adoptRevision(detail, detail);
      flow.credit = applied;
      drawReceipt();
      drawActions();
    } catch (error) {
      if (creditSubmit.getAttribute("data-denied") !== "true") creditSubmit.disabled = false;
      const failure = /** @type {any} */ (error);
      const refused = failure.kind === "DENIED" || failure.status === 422 || failure.kind === "STALE";
      show(
        creditAlert,
        errorNotice(error, {
          title: refused
            ? "Máy chủ từ chối áp dụng khoản này. Không có gì được ghi — đọc mã lý do bên dưới."
            : undefined,
        }),
      );
    }
  }

  // --- the action bar of step 2 ---------------------------------------------------------------

  function drawActions() {
    if (flow.step !== 2) return;
    const back = backButton(() => go(1));
    const revision = flow.quote;
    if (!revision || flow.dirty) {
      render(
        actionHost,
        bar(
          back,
          gated(
            button({
              label: revision ? "Tính lại" : "Tính giá",
              variant: "primary",
              network: true,
              id: "new-price",
              onClick: () => void price(false),
            }),
            quoteWrite,
          ),
        ),
      );
      return;
    }
    const total = moneyRange(revision.display_total_min_vnd, revision.display_total_max_vnd);
    const blocked =
      revision.finality === "RANGE"
        ? "Chốt một giá trong khoảng ở trên trước."
        : !total.isKnown
          ? "Chưa có tổng để khách đồng ý: nhập quãng đường hoặc phí đã thoả thuận rồi tính lại."
          : "";
    const next = button({
      label: "Tiếp tục",
      variant: "primary",
      id: "new-next",
      disabled: Boolean(blocked),
      onClick: () => go(3),
    });
    render(
      actionHost,
      bar(blocked ? h("p", { class: "hint" }, blocked) : null, back, next),
    );
  }

  // ============================================================================================
  // Step 3 — Xác nhận
  // ============================================================================================

  function stepConfirm() {
    const revision = flow.quote;
    if (!flow.request || !revision) {
      flow.step = 2;
      return stepItems();
    }
    const accepted = revision.status === "ACCEPTED_FINAL";
    const total = moneyRange(revision.display_total_min_vnd, revision.display_total_max_vnd, "chưa có tổng");
    const alertHost = h("div", { id: "new-confirm-result" });
    const sources = segmented({
      label: "Khách biết tiệm qua đâu",
      id: "new-source",
      wrap: true,
      value: flow.source,
      options: ACQUISITION_SOURCES.map((value) => ({
        value,
        label: capitalize(ACQUISITION_SOURCE_VI[value]),
      })),
      onChange: (value) => {
        flow.source = value;
        // The order body changed, so the order step is a new intent.
        orderSub.reset();
      },
    });
    sources.classList.add("segmented--grid");
    const confirm = button({
      label: accepted ? "Tạo đơn" : "Khách đồng ý — tạo đơn",
      variant: "primary",
      network: true,
      id: "new-confirm",
      onClick: () => void confirmOrder(confirm, alertHost),
    });
    const lines = flow.detail?.lines || [];
    return h(
      "div",
      { class: "stack" },
      h(
        "div",
        { class: "confirm-card surface", id: "new-summary" },
        h(
          "div",
          { class: "confirm-card__head" },
          h("span", { class: "confirm-card__who" }, customerLabel(flow.request)),
          h("span", { class: "confirm-card__mode" }, modeLabel(flow.quoteMode || flow.mode)),
        ),
        lines.length
          ? h(
              "ul",
              { class: "confirm-card__lines" },
              lines.map((line) =>
                h(
                  "li",
                  null,
                  `${serviceName(catalog, line.service_code)} · ${quantityText(line.quantity)} ${unitShort(line.unit)}`,
                ),
              ),
            )
          : null,
        h(
          "div",
          { class: "receipt__total" },
          h("span", { class: "receipt__total-label" }, "Tổng khách trả"),
          h("span", { class: ["receipt__total-amount", total.isKnown && "money"], dataTotal: "true" }, total.text),
        ),
        accepted
          ? h("p", { class: "receipt__note", dataState: "ok" }, "Khách đã đồng ý giá này. Chỉ còn tạo đơn.")
          : null,
      ),
      section({
        title: "Khách biết tiệm qua đâu",
        card: false,
        children: h(
          "div",
          { class: "stack stack--tight" },
          sources,
          h(
            "p",
            { class: "hint" },
            "Hỏi: “Anh/chị biết tiệm qua đâu ạ?” Chưa hỏi thì để “Chưa biết”. Ghi xong không sửa được.",
          ),
        ),
      }),
      alertHost,
      bar(
        accepted
          ? null
          : h("p", { class: "hint" }, "Bấm khi khách đã nghe giá và đồng ý. Tên bạn sẽ được ghi lại."),
        backButton(() => go(2)),
        gated(confirm, confirmVerdict),
      ),
    );
  }

  /**
   * The customer's acceptance, then the order — each only after the previous succeeded, each with
   * its own key. A refusal stops here and says why; the next press resumes at the failed step,
   * because the acceptance that did land is now the revision this flow holds.
   *
   * @param {HTMLButtonElement} control
   * @param {HTMLElement} alertHost
   */
  async function confirmOrder(control, alertHost) {
    if (flow.busy || !flow.quote || !flow.request) return;
    flow.busy = true;
    control.disabled = true;
    control.setAttribute("aria-busy", "true");
    /** @type {"accept"|"read"|"order"} */
    let phase = "accept";
    const status = (text) => render(alertHost, h("p", { class: "hint", role: "status" }, text));
    try {
      let accepted = flow.quote.status === "ACCEPTED_FINAL" ? flow.quote : null;
      if (!accepted) {
        status("Đang ghi lời khách đồng ý…");
        const sub = acceptSubmission(flow.quote);
        accepted = await request(
          `/internal/v1/stores/${encodeURIComponent(store)}/quotes/${encodeURIComponent(flow.quote.quote_id)}/acceptance`,
          {
            method: "POST",
            body: {
              expected_current_revision: flow.quote.revision,
              expected_snapshot_hash: flow.quote.snapshot_hash,
            },
            idempotencyKey: sub.key(),
          },
        );
        sub.reset();
        const lines = flow.detail;
        adoptRevision(accepted, null);
        // The accepted revision carries the same lines as the one the customer was read.
        flow.detail = lines;
      }
      phase = "read";
      if (!flow.acceptedAt || flow.acceptedRevision !== accepted.revision) {
        status("Đang tạo đơn…");
        const read = await request(
          `/internal/v1/stores/${encodeURIComponent(store)}/quotes/${encodeURIComponent(accepted.quote_id)}?revision=${encodeURIComponent(String(accepted.revision))}`,
        );
        if (!read.customer_accepted_at) {
          // Never guessed: the order route needs the recorded instant, and the device clock is not it.
          throw new Error(
            "Máy chủ chưa trả thời điểm khách đồng ý của bản này, nên chưa tạo đơn. Bấm lại để đọc lại.",
          );
        }
        flow.acceptedAt = read.customer_accepted_at;
        flow.acceptedRevision = accepted.revision;
        if (read.fulfillment_mode) flow.quoteMode = read.fulfillment_mode;
      }
      phase = "order";
      status("Đang tạo đơn…");
      const created = await request(`/internal/v1/stores/${encodeURIComponent(store)}/orders`, {
        method: "POST",
        body: {
          bound_contact_id: flow.request.contact_binding_id,
          quote_id: accepted.quote_id,
          quote_revision: accepted.revision,
          quote_snapshot_hash: accepted.snapshot_hash,
          fulfillment_mode: flow.quoteMode || flow.mode,
          acquisition_source: flow.source,
          customer_final_quote_accepted_at: flow.acceptedAt,
        },
        idempotencyKey: orderSub.key(),
      });
      orderSub.reset();
      flow.busy = false;
      toast(`Đã tạo đơn · ${customerLabel(flow.request)}`);
      location.hash = `#/orders/${encodeURIComponent(String(created.order_id))}`;
    } catch (error) {
      flow.busy = false;
      control.removeAttribute("aria-busy");
      if (control.getAttribute("data-denied") !== "true") control.disabled = false;
      const failure = /** @type {any} */ (error);
      if (phase === "accept") {
        const verdict = acceptFailure(failure);
        if (verdict.final) control.disabled = true;
        show(
          alertHost,
          h(
            "div",
            { class: "stack stack--tight" },
            errorNotice(error, {
              title: verdict.title,
              actions: verdict.reprice
                ? [
                    button({
                      label: "Tính lại giá",
                      variant: "primary",
                      onClick: () => {
                        flow.dirty = true;
                        go(2);
                      },
                    }),
                  ]
                : [
                    button({
                      label: "Đọc lại báo giá",
                      icon: "refresh",
                      variant: "quiet",
                      onClick: () => void resumeFromQuote(String(flow.quote.quote_id), flow.request),
                    }),
                  ],
            }),
            verdict.final ? h("p", { class: "hint" }, verdict.blocked) : null,
          ),
        );
        return;
      }
      // The acceptance is recorded; what failed is reading it back or the order itself. The next
      // press skips the acceptance and resumes here, with the same order key.
      const unknown = failure.kind === "TIMEOUT" || failure.kind === "NETWORK";
      show(
        alertHost,
        h(
          "div",
          { class: "stack stack--tight" },
          inlineAlert({ state: "ok", title: "Khách đã đồng ý giá — đã ghi." }),
          errorNotice(error, {
            title:
              phase === "read"
                ? typeof failure.kind === "string"
                  ? "Chưa đọc lại được lời đồng ý vừa ghi, nên chưa tạo đơn. Bấm lại “Tạo đơn” để thử tiếp."
                  : failure.message
                : unknown
                  ? "Chưa biết lệnh có tới máy chủ hay không, nên chưa biết đơn đã được tạo hay chưa. " +
                    "Bấm lại “Tạo đơn”: lần bấm lại dùng cùng mã thao tác, nên máy chủ không tạo hai đơn."
                  : failure.kind === "REQUIRE_HUMAN"
                    ? "Cần người quyết định trước khi tạo đơn. Không có đơn nào được tạo."
                    : `Không tạo được đơn: ${failure.message || "máy chủ từ chối."} Không có đơn nào được tạo.`,
          }),
        ),
      );
      // Now only the order is left, and the button says so.
      const label = control.querySelector("span");
      if (label) label.textContent = "Tạo đơn";
    }
  }

  // ============================================================================================
  // Boot
  // ============================================================================================

  void loadCatalog();
  if (resumeQuote) {
    if (UUID.test(resumeQuote)) void resumeFromQuote(resumeQuote, null);
    else show(resumeHost, inlineAlert({ state: "warn", title: "Mã báo giá trong đường dẫn không hợp lệ" }));
  } else if (resumeRequest) {
    if (UUID.test(resumeRequest)) {
      render(resumeHost, skeleton(1));
      void request(
        `/internal/v1/stores/${encodeURIComponent(store)}/order-requests/${encodeURIComponent(resumeRequest)}`,
      ).then(
        (item) => resumeFromRequest(item),
        (error) =>
          show(
            resumeHost,
            /** @type {any} */ (error).kind === "MISSING"
              ? h(
                  "div",
                  { class: "notice", dataState: "warn", id: "new-request-missing" },
                  h("p", { class: "notice__title" }, "Không tìm thấy yêu cầu này trong cửa hàng đang chọn"),
                  h(
                    "p",
                    null,
                    "Mã có thể thuộc cửa hàng khác hoặc đã bị gõ sai — máy chủ trả lời cùng một cách cho " +
                      "cả hai, nên màn hình này cũng không đoán. Không có dữ kiện nào được điền sẵn; " +
                      "hãy chọn từ danh sách bên dưới.",
                  ),
                )
              : errorNotice(error),
          ),
      );
    } else {
      show(resumeHost, inlineAlert({ state: "warn", title: "Mã yêu cầu trong đường dẫn không hợp lệ" }));
    }
  }
  draw();

  return h(
    "section",
    { class: "screen" },
    page({ title: "Nhận đồ" }),
    progressHost,
    resumeHost,
    body,
    picker.node,
    creditSheet.node,
  );
}

/**
 * The flow's sticky action bar: "Quay lại" as wide as its word, the business verb taking the rest,
 * so a long primary label never wraps beside a short back button.
 *
 * @param {...unknown} children
 * @returns {HTMLElement}
 */
function bar(...children) {
  const node = actionBar(...children);
  node.classList.add("action-bar--lead");
  return node;
}

/**
 * "Quay lại" as a chevron: the previous step, nothing lost (the flow is held in memory). Its word
 * is its accessible name, so the verb beside it keeps the width.
 *
 * @param {() => void} onClick
 * @returns {HTMLButtonElement}
 */
function backButton(onClick) {
  return button({
    label: h("span", { class: "sr-only" }, "Quay lại"),
    icon: "chevron-left",
    variant: "quiet",
    data: { back: "true" },
    onClick,
  });
}

/** @param {string} text */
function capitalize(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** @type {import("../core/router.js").Route} */
export const screen = {
  path: "/new",
  title: "Nhận đồ",
  capability: "QUOTES_WRITE",
  needsStore: true,
  render: render_,
};
