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
 *
 * @module screens/orders
 */

import { MAX_LIMIT, Submission, isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { count, shortId } from "../core/format.js";
import { enumLabel } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  badge,
  empty,
  enumSelect,
  errorNotice,
  facts,
  gated,
  labelled,
  panel,
  resultLine,
  skeleton,
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

/** Loose UUID shape — matched here only to catch a mistyped identifier before a round trip. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** `OrderCreateRequest.quote_snapshot_hash`, exactly as the server's pattern spells it. */
const SNAPSHOT_HASH = /^JCS-SHA256-V1:[0-9a-f]{64}$/;

/**
 * One state dimension, as a neutral badge.
 *
 * Deliberately uncoloured. Colouring `EXCEPTION` red and `PAID` green would be this console
 * ranking the severity of domain states, and nothing in the specification ranks them — the server
 * publishes the value, not an opinion about it. `components.css` already says state is never
 * conveyed by colour alone; here the word is the whole message, so the word is all there is.
 *
 * @param {string|null|undefined} value a server enum value
 * @returns {HTMLElement}
 */
function dimensionBadge(value) {
  return badge({ token: enumLabel(value), gloss: "", state: "neutral" });
}

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
          "Kết quả được phát lại: cùng khoá thao tác và cùng nội dung đã gửi trước đó. Không có " +
            "bản ghi mới nào được tạo.",
        )
      : null,
    facts([
      ["Thương mại", dimensionBadge(item.commercial)],
      ["Tiếp nhận", dimensionBadge(item.intake)],
      ["Sản xuất", dimensionBadge(item.production)],
      ["Công nợ", dimensionBadge(item.balance)],
      ["Phiên bản dòng", `v${item.row_version}`],
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
 * @param {any[]} items
 * @param {(item: any) => void} onTransition
 * @returns {HTMLElement}
 */
function orderList(items, onTransition) {
  if (!items.length) return empty("Chưa có đơn nào trong cửa hàng này.");
  return h(
    "div",
    { class: "stack" },
    items.map((item) => orderCard(item, { onTransition })),
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
  return h(
    "div",
    { class: "notice", dataState: "info" },
    h("p", { class: "notice__title" }, "Bảng đơn chỉ mang trạng thái và phiên bản dòng"),
    h(
      "p",
      null,
      "Máy chủ trả đúng tám trường cho một đơn: hai định danh, bốn trạng thái, phiên bản dòng và " +
        "cờ phát lại. Không có khách hàng, không có mốc thời gian, không có tổng tiền, không có " +
        "hình thức giao nhận. Những cột đó chưa tồn tại ở phía máy chủ, nên màn hình này không " +
        "hiển thị chúng thay vì đoán.",
    ),
    h("p", null, h("a", { href: "#/gaps" }, "Xem danh sách khoảng trống chưa hỗ trợ")),
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

  const listHost = h("div", null, skeleton(3));
  const listCount = h("span", { class: "count" }, "…");
  const truncation = h("p", { class: "hint" });

  /** Any structural change to the transition form invalidates its key and redraws it. */
  const redrawMove = () => {
    transitionSubmission.reset();
    render(moveBody, moveForm());
  };

  async function loadList() {
    render(listHost, skeleton(3));
    try {
      const items = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/orders?limit=${LIST_LIMIT}`,
      );
      listCount.textContent = count(items, LIST_LIMIT);
      truncation.textContent = isTruncated(items, LIST_LIMIT)
        ? `Máy chủ trả tối đa ${LIST_LIMIT} bản ghi và đã trả đủ; có thể còn nữa. API này không ` +
          `có phân trang và không có con trỏ; trần cứng phía máy chủ là ${MAX_LIMIT}.`
        : "";
      render(
        listHost,
        orderList(items, (item) => {
          move.orderId = item.order_id;
          move.rowVersion = String(item.row_version);
          redrawMove();
          moveBody.scrollIntoView({ block: "start", behavior: "smooth" });
        }),
      );
    } catch (error) {
      listCount.textContent = "—";
      truncation.textContent = "";
      render(listHost, errorNotice(error, { onRetry: () => void loadList() }));
    }
  }

  // --- create ---------------------------------------------------------------------------------

  /**
   * @returns {string} an empty string when the draft is submittable
   */
  function validateCreate() {
    if (!UUID.test(draft.contactId.trim())) return "Bound contact id phải là một UUID.";
    if (!UUID.test(draft.quoteId.trim())) return "Quote id phải là một UUID.";
    const revision = Number.parseInt(draft.revision.trim(), 10);
    if (!Number.isInteger(revision) || revision < 1) return "Bản báo giá phải là số nguyên từ 1.";
    if (!SNAPSHOT_HASH.test(draft.hash.trim())) {
      return "Mã băm ảnh chụp phải đúng dạng JCS-SHA256-V1: theo sau là 64 ký tự hex thường.";
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
      createResult.dataset.state = "danger";
      createResult.textContent = writeVerdict.reason;
      return;
    }

    const problem = validateCreate();
    if (problem) {
      createResult.dataset.state = "danger";
      createResult.textContent = problem;
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

    createResult.dataset.state = "warn";
    createResult.textContent = "Đang gửi lệnh tạo đơn…";
    render(createResultHost);

    try {
      const created = await request(`/internal/v1/stores/${encodeURIComponent(store)}/orders`, {
        method: "POST",
        body: payload,
        idempotencyKey: createSubmission.key(),
      });
      // Confirmed exactly once, here. The next submission is a new intent and gets a new key.
      createSubmission.reset();
      createResult.dataset.state = "ok";
      createResult.textContent = `Đã tạo đơn ${shortId(created.order_id)}.`;
      render(createResultHost, orderCard(created));
      // The quote-bound fields are cleared and the form rebuilt, so a second tap on a form that
      // still looks armed cannot mint a second order under a fresh key. An order is not a quote
      // revision; there is no cheap way to undo a duplicate one.
      draft.quoteId = "";
      draft.hash = "";
      draft.acceptedAt = "";
      render(createBody, createForm());
      await loadList();
    } catch (error) {
      createResult.dataset.state = error.kind === "REQUIRE_HUMAN" ? "warn" : "danger";
      createResult.textContent =
        error.kind === "REQUIRE_HUMAN"
          ? "Cần người quyết định trước khi tạo đơn. Không có đơn nào được tạo."
          : "Không tạo được đơn. Máy chủ nêu lý do bên dưới, nguyên văn.";
      render(createResultHost, errorNotice(error));
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
      { type: "submit", dataVariant: "primary", disabled: !navigator.onLine },
      "Tạo đơn",
    );

    return h(
      "form",
      { class: "form", onSubmit: submitCreate },
      h(
        "div",
        { class: "notice", dataState: "warn" },
        h("p", { class: "notice__title" }, "Lệnh này sẽ bị từ chối cho tới khi có đường duyệt giá"),
        h(
          "p",
          null,
          "Máy chủ chỉ nhận một báo giá đã chốt tuyệt đối: finality APPROVED_EXACT, status " +
            "ACCEPTED_FINAL, mã băm khớp đúng bản đó, approval_id khác null, và chưa quá " +
            "valid_until. Thiếu bất kỳ điều kiện nào, câu trả lời là 409 " +
            '"accepted exact quote is missing, stale, or expired".',
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
        label: "Bound contact id",
        hint: "UUID của ràng buộc liên hệ đã xác thực. Màn hình này không tra cứu được khách hàng — API không có route đọc liên hệ.",
        control: contactInput,
      }),
      labelled({
        id: "order-quote",
        label: "Quote id",
        hint: "UUID của báo giá đã được khách chốt.",
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
        label: "Mã băm ảnh chụp báo giá",
        hint: "Chép nguyên văn từ bản báo giá, gồm cả tiền tố JCS-SHA256-V1:. Máy chủ so khớp đúng chuỗi này.",
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
        hint: "Đọc theo múi giờ của thiết bị này rồi gửi đi dưới dạng UTC, nên luôn có múi giờ. Bỏ trống thì không gửi.",
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
    if (!UUID.test(move.orderId.trim())) return "Order id phải là một UUID.";
    const version = Number.parseInt(move.rowVersion.trim(), 10);
    if (!Number.isInteger(version) || version < 1) {
      return "Phiên bản dòng phải là số nguyên từ 1. Chọn lại đơn từ bảng để lấy đúng giá trị.";
    }
    return "";
  }

  /**
   * @param {SubmitEvent} event
   */
  async function submitMove(event) {
    event.preventDefault();

    if (!writeVerdict.allowed) {
      moveResult.dataset.state = "danger";
      moveResult.textContent = writeVerdict.reason;
      return;
    }

    const problem = validateMove();
    if (problem) {
      moveResult.dataset.state = "danger";
      moveResult.textContent = problem;
      return;
    }

    moveResult.dataset.state = "warn";
    moveResult.textContent = "Đang gửi lệnh chuyển trạng thái…";
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
      moveResult.dataset.state = "ok";
      moveResult.textContent = `Đã chuyển sang ${moved.commercial}, phiên bản dòng v${moved.row_version}.`;
      render(moveResultHost, orderCard(moved));
      render(moveBody, moveForm());
      await loadList();
    } catch (error) {
      moveResult.dataset.state = error.kind === "REQUIRE_HUMAN" ? "warn" : "danger";
      moveResult.textContent =
        error.kind === "REQUIRE_HUMAN"
          ? "Cần người duyệt trước khi chuyển. Trạng thái đơn không đổi."
          : "Máy chủ từ chối chuyển trạng thái. Trạng thái đơn không đổi.";
      render(
        moveResultHost,
        errorNotice(error),
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
                    void loadList();
                  },
                },
                "Tải lại bảng",
              ),
            )
          : null,
      );
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
      { type: "submit", dataVariant: "primary", disabled: !navigator.onLine },
      "Chuyển trạng thái",
    );

    return h(
      "form",
      { class: "form", onSubmit: submitMove },
      labelled({
        id: "move-order",
        label: "Order id",
        hint: "Bấm “Chọn để chuyển trạng thái” trên một đơn ở bảng bên dưới để điền sẵn ô này và phiên bản dòng.",
        control: orderInput,
      }),
      labelled({
        id: "move-version",
        label: "Phiên bản dòng đang giữ",
        hint: "Gửi kèm dưới dạng If-Match. Nếu ai đó vừa đổi đơn này, máy chủ từ chối bằng STALE_VERSION và không có gì thay đổi.",
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
  void loadList();

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "BỐN TRỤC TRẠNG THÁI · MÁY CHỦ QUYẾT CHUYỂN ĐỔI"),
      h("h1", null, "Đơn hàng"),
      h(
        "p",
        { class: "screen__lede" },
        "Bảng này hiển thị đúng những gì máy chủ trả về cho một đơn: bốn trạng thái và một phiên " +
          "bản dòng. Không suy diễn thêm cột nào.",
      ),
    ),
    panel({
      eyebrow: "LỆNH",
      title: "Chuyển trạng thái thương mại",
      guardrail:
        "Màn hình này không biết chuyển đổi nào hợp lệ và cố ý không biết. Bảng chuyển trạng thái " +
        "là quy tắc miền (FR-ORD-002); chép nó vào trình duyệt là tạo bản sao thứ hai không ai " +
        "giữ đồng bộ được. Tất cả tám đích đều được chào, và máy chủ từ chối cái nào không hợp lệ.",
      children: h("div", { class: "stack" }, moveBody, moveResultHost),
    }),
    panel({
      eyebrow: "LỆNH",
      title: "Tạo đơn từ báo giá đã chốt",
      guardrail:
        "Đơn chỉ được tạo từ một báo giá đã chốt tuyệt đối. Máy chủ kiểm lại toàn bộ điều kiện; " +
        "màn hình này chỉ kiểm dạng dữ liệu để bắt lỗi gõ trước khi gửi.",
      children: h("div", { class: "stack" }, createBody, createResultHost),
    }),
    panel({
      eyebrow: "ĐÃ GHI",
      title: "Bảng đơn của cửa hàng",
      count: listCount,
      guardrail:
        "Bốn trục — thương mại, tiếp nhận, sản xuất, công nợ — chuyển động độc lập với nhau. Một " +
        "đơn CONFIRMED vẫn có thể đang NOT_STARTED và UNPAID cùng lúc. Đừng đọc bốn nhãn như một " +
        "chuỗi tuần tự.",
      children: h("div", { class: "stack" }, readModelNotice(), truncation, listHost),
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
