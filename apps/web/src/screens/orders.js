/**
 * Orders: the state board, and the two commands the API actually offers against it.
 *
 * This screen is mostly an exercise in not inventing things, because the order read model is far
 * thinner than an operations board usually is, and every plausible way to thicken it is a lie:
 *
 *   - **The list carries state and version, and nothing else.** No customer, no timestamp, no
 *     total, no fulfilment mode. `OrderResponse` has exactly eight members and six of them are
 *     identifiers or enums. A "Khách" column filled from anywhere would be this console asserting a
 *     fact the server never told it, so the gap is named on screen instead and linked to `#/gaps`.
 *   - **No money appears here at all.** Not a subtotal, not a balance amount — the board's `balance`
 *     is a *status* enum, not an amount, and rendering it next to a number would invite the reading
 *     that the number is what remains owed.
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
 *     with its fetch time; the text box string-matches the id and the four state enums of rows
 *     already fetched and computes nothing, so it cannot grow into the second copy of a domain
 *     rule the transition-table note above warns against.
 *
 * @module screens/orders
 */

import { MAX_LIMIT, Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UUID, shortId } from "../core/format.js";
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
  labelled,
  listView,
  panel,
  resultLine,
  revealError,
  setResult,
} from "../ui/components.js";

const LIST_LIMIT = 100;

/** `FulfillmentMode`, as the strict request model spells it. */
const FULFILLMENT_MODES = [
  "SELF_DROP_SELF_COLLECT",
  "PICKUP_AND_RETURN",
  "PICKUP_ONLY",
  "RETURN_ONLY",
];

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

/** `OrderCreateRequest.quote_snapshot_hash`, exactly as the server's pattern spells it. */
const SNAPSHOT_HASH = /^JCS-SHA256-V1:[0-9a-f]{64}$/;

/**
 * One order on the board.
 *
 * @param {any} item an `OrderResponse`
 * @param {{onTransition?: (item: any) => void}} [options]
 * @returns {HTMLElement}
 */
function orderCard(item, options = {}) {
  return h(
    "article",
    { class: "card" },
    h(
      "div",
      { class: "spread" },
      h("strong", { class: "mono", title: item.order_id }, shortId(item.order_id)),
      options.onTransition
        ? h(
            "button",
            { type: "button", onClick: () => options.onTransition(item) },
            "Chọn để chuyển trạng thái",
          )
        : null,
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
    h(
      "div",
      { class: "form__actions" },
      h(
        "a",
        { class: "button", href: `#/orders/${encodeURIComponent(item.order_id)}` },
        "Mở chi tiết và dòng thời gian",
      ),
    ),
  );
}

/**
 * The standing note about what the order read model does not contain.
 *
 * Kept as a component rather than a sentence in the panel because the same honesty is owed on the
 * detail screen, and two hand-written versions of it drift apart.
 *
 * @returns {HTMLElement}
 */
function readModelNotice() {
  return explain(
    "Vì sao bảng đơn không có tên khách, giờ hẹn hay số tiền?",
    h(
      "p",
      null,
      "Vì máy chủ chưa lưu những thứ đó cho một đơn. Không phải màn hình này giấu đi — chúng " +
        "chưa tồn tại. Hiện một cột “Khách” lấy từ chỗ khác là bịa ra một điều máy chủ chưa từng " +
        "nói, nên bảng để trống và ghi nhận đây là việc chưa làm được.",
    ),
    h("p", null, h("a", { href: "#/gaps" }, "Xem danh sách việc chưa hỗ trợ")),
  );
}

/**
 * @param {import("../core/router.js").RouteContext} [_context]
 * @returns {HTMLElement}
 */
export function render_(_context) {
  const store = storeId();
  const me = principal();
  const writeVerdict = can(me, "ORDERS_WRITE");

  const createSubmission = new Submission("order-create");
  const transitionSubmission = new Submission("order-transition");

  /**
   * The create form's state, held here rather than read back from the DOM at submit time so that
   * what is sent is provably what is shown.
   *
   * @type {{contactId: string, quoteId: string, revision: string, hash: string, mode: string, acceptedAt: string}}
   */
  const draft = {
    contactId: "",
    quoteId: "",
    revision: "1",
    hash: "",
    mode: FULFILLMENT_MODES[0],
    acceptedAt: "",
  };

  /** @type {{orderId: string, rowVersion: string, target: string}} */
  const move = { orderId: "", rowVersion: "", target: COMMERCIAL_TARGETS[0] };

  const createBody = h("div");
  const createResultHost = h("div", { class: "stack" });
  const createResult = resultLine();

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
      request(`/internal/v1/stores/${encodeURIComponent(store)}/orders?limit=${LIST_LIMIT}`),
    renderItem: (item) => orderCard(item, { onTransition: pickOrder }),
    emptyText: "Chưa có đơn nào trong cửa hàng này.",
    clearMetaOnError: true,
    truncationText: (limit) =>
      `Máy chủ trả tối đa ${limit} bản ghi và đã trả đủ; có thể còn nữa. API này không ` +
      `có phân trang và không có con trỏ; trần cứng phía máy chủ là ${MAX_LIMIT}.`,
    filter: {
      placeholder: "Lọc theo mã đơn hoặc trạng thái…",
      label: "Lọc bảng đơn",
      noun: "đơn",
      matches: (item, needle) =>
        [item.order_id, item.commercial, item.intake, item.production, item.balance].some(
          (value) => String(value).toLowerCase().includes(needle),
        ),
      filteredEmptyText: "Không có đơn nào khớp bộ lọc.",
    },
  });

  /** Any structural change to the transition form invalidates its key and redraws it. */
  const redrawMove = () => {
    transitionSubmission.reset();
    render(moveBody, moveForm());
  };

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
      // `datetime-local` yields a naive wall-clock string; the server requires an aware instant.
      // `Date` reads it in the device's timezone and `toISOString` emits UTC, so the offset is
      // always explicit. The device's timezone is therefore load-bearing, which is why the hint
      // under the field says so.
      customer_final_quote_accepted_at: new Date(draft.acceptedAt).toISOString(),
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
      setResult(createResult, "ok", `Đã tạo đơn ${shortId(created.order_id)}.`);
      render(createResultHost, orderCard(created));
      // The quote-bound fields are cleared and the form rebuilt, so a second tap on a form that
      // still looks armed cannot mint a second order under a fresh key. An order is not a quote
      // revision; there is no cheap way to undo a duplicate one.
      draft.quoteId = "";
      draft.hash = "";
      draft.acceptedAt = "";
      render(createBody, createForm());
      await board.reload();
    } catch (error) {
      setResult(
        createResult,
        error.kind === "REQUIRE_HUMAN" ? "warn" : "danger",
        error.kind === "REQUIRE_HUMAN"
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

    const acceptedInput = h("input", {
      type: "datetime-local",
      value: draft.acceptedAt,
      onInput: (event) => {
        draft.acceptedAt = event.target.value;
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
          "Hôm nay chưa có báo giá nào do API này tạo ra thoả cả năm điều kiện, vì đường duyệt giá " +
            "chưa tồn tại. Biểu mẫu vẫn hiện ở đây để lệnh và lý do từ chối là thật, không phải " +
            "một nút bị giấu đi.",
        ),
        h("p", null, h("a", { href: "#/gaps" }, "Xem khoảng trống: đường duyệt giá chính xác")),
      ),
      labelled({
        id: "order-contact",
        label: "Mã khách",
        hint: "Chép từ màn hình Tiếp nhận. Màn hình này chưa tra cứu được khách theo tên hay số điện thoại.",
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
        id: "order-accepted",
        label: "Thời điểm khách chốt giá",
        hint: "Đọc theo giờ của máy bạn đang dùng — kiểm lại nếu máy đặt sai múi giờ. Bỏ trống thì không gửi.",
        control: acceptedInput,
      }),
      h("div", { class: "action-bar" }, gated(submit, writeVerdict)),
      createResult,
    );
  }

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
      const moved = await request(
        `/internal/v1/orders/${encodeURIComponent(move.orderId.trim())}/transition`,
        {
          method: "POST",
          body: { target: move.target },
          idempotencyKey: transitionSubmission.key(),
          ifMatch: Number.parseInt(move.rowVersion.trim(), 10),
        },
      );
      transitionSubmission.reset();
      // The row moved, so the version held in this form is now one behind. Adopt the version the
      // server just returned rather than leaving a value that would produce a STALE on the next
      // command for no reason the operator could see.
      move.rowVersion = String(moved.row_version);
      setResult(
        moveResult,
        "ok",
        `Đã chuyển sang ${enumVi(moved.commercial)}. Bản ghi giờ là v${moved.row_version}.`,
      );
      render(moveResultHost, orderCard(moved));
      render(moveBody, moveForm());
      await board.reload();
    } catch (error) {
      setResult(
        moveResult,
        error.kind === "REQUIRE_HUMAN" ? "warn" : "danger",
        error.kind === "REQUIRE_HUMAN"
          ? "Cần người duyệt trước khi chuyển. Trạng thái đơn không đổi."
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

    const targetSelect = enumSelect("target", COMMERCIAL_TARGETS, move.target);
    targetSelect.addEventListener("change", (event) => {
      move.target = /** @type {HTMLSelectElement} */ (event.target).value;
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
        id: "move-target",
        label: "Trạng thái thương mại đích",
        hint: "Máy chủ quyết định chuyển đổi nào hợp lệ.",
        control: targetSelect,
      }),
      h("div", { class: "action-bar" }, gated(submit, writeVerdict)),
      moveResult,
    );
  }

  render(createBody, createForm());
  render(moveBody, moveForm());
  void board.reload();

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
  const legKindSelect = enumSelect("leg_kind", ["RETURN", "PICKUP"], legDraft.kind);
  legKindSelect.addEventListener("change", (event) => {
    legDraft.kind = event.target.value;
    legSubmission.reset();
  });
  const legOutcomeSelect = enumSelect("outcome", ["SUCCEEDED", "FAILED"], legDraft.outcome);
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
    h("div", { class: "form__actions" }, h("button", { type: "submit", class: "button" }, "Ghi nhận")),
  );

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
        board.bar.node,
        board.filterStatus,
        readModelNotice(),
        board.truncation,
        board.host,
      ),
    }),
    panel({
      eyebrow: "Lệnh",
      title: "Chuyển trạng thái thương mại",
      guardrail:
        "Màn hình này cố ý không biết bước nào là hợp lệ — quy tắc đó thuộc về máy chủ, và chép " +
        "nó sang trình duyệt là tạo bản thứ hai không ai giữ cho khớp được. Cả tám đích đều được " +
        "chào; máy chủ từ chối cái nào không hợp lệ và nói rõ vướng ở đâu.",
      children: h("div", { class: "stack" }, moveBody, moveResultHost),
    }),
    panel({
      eyebrow: "Lệnh · POST /internal/v1/orders/{id}/delivery-legs",
      title: "Ghi nhận chuyến giao",
      guardrail:
        "Khách đã trả đủ tại quầy trước khi đồ rời tiệm, nên ghi nhận ở đây không có tiền — chỉ " +
        "ghi đồ đã đến tay khách hay chưa. Giao hụt thì ghi thất bại, không tính thêm phí, và " +
        "lần giao sau là một dòng mới. Chỉ chuyến TRẢ ĐỒ thành công mới cho phép đóng đơn.",
      children: h("div", { class: "stack" }, legBody, legResultHost),
    }),
    panel({
      eyebrow: "Lệnh",
      title: "Tạo đơn từ báo giá đã chốt",
      guardrail:
        "Đơn chỉ được tạo từ một báo giá khách đã chốt. Bấm \u201cKhách đã chốt giá\u201d ở màn " +
        "hình Báo giá trước, rồi mới tạo đơn ở đây. Máy chủ kiểm lại toàn bộ điều kiện; màn hình " +
        "này chỉ bắt lỗi gõ trước khi gửi.",
      children: h("div", { class: "stack" }, createBody, createResultHost),
    }),
  );
}

export const screen = {
  path: "/orders",
  title: "Đơn hàng",
  capability: "ORDERS_READ",
  needsStore: true,
  render: render_,
};
