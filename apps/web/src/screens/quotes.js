/**
 * Quotes: price a request through the deterministic engine, and read back what it decided.
 *
 * This screen is the reason the console exists, and it is also the easiest place to do harm, so a
 * few of its choices are deliberate rather than incidental:
 *
 *   - **The quantity is sent exactly as typed.** Not trimmed of a trailing zero, not parsed into a
 *     number and re-serialized. `DEC-001` — how many decimal places a weight may carry — is an open
 *     business decision, and `6` and `6.0` are different strings to a server that has not made it.
 *   - **No total is ever computed here.** The response carries a service subtotal and, separately,
 *     a display total that is usually null because the delivery fee is unresolved. Adding the first
 *     to a guess at the second is precisely the defect `ENGINEERING_SPEC_V1.md:530` forbids.
 *   - **`net_service_subtotal_vnd` is shown under a price-state badge, never bare.** The API gives
 *     it a scalar name but the service layer fills it from the domain's range *maximum*. Today
 *     range-priced services are refused outright so the two coincide; the day they do not, a bare
 *     number here would show the top of a range as a settled price.
 *   - **A refusal is a result, not an error.** A 422 carrying `REQUIRE_HUMAN` means the engine
 *     declined to guess. It is rendered as an outcome with its reason codes intact.
 *   - **The order request is picked or prefilled, not pasted.** The common path is the Tiếp nhận
 *     screen's "Báo giá ngay" link (`#/quotes?request=<id>`, resolved against the server before
 *     the form binds it) or a pick from the recent-intake list below the form. A bare UUID field
 *     remains as a collapsed fallback for recovery, because a copied id from another system is how
 *     a quote lands on the wrong customer.
 *
 * @module screens/quotes
 */

import { Submission, isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UUID, dateTime, shortHash, shortId } from "../core/format.js";
import { enumLabel } from "../core/i18n.js";
import { storeId } from "../core/session.js";
import {
  amount,
  badge,
  copyable,
  enumSelect,
  errorNotice,
  facts,
  icon,
  labelled,
  listView,
  panel,
  priceStateBadge,
  pricingCliffNotice,
  reasonCodeList,
  resultLine,
  revealError,
  setResult,
  skeleton,
  warningBadges,
} from "../ui/components.js";

const UNITS = ["KG", "ITEM", "PAIR", "SET", "ANIMAL_PLUSH_ITEM", "CASE", "M2"];
const BASES = ["STAFF_MEASUREMENT", "CUSTOMER_ESTIMATE", "APPROVED_MANUAL"];
const LIST_LIMIT = 100;
const MAX_LINES = 20;

/** `QuoteLineRequest.service_code` — matched here only to catch a typo before a round trip. */
const SERVICE_CODE = /^[A-Z][A-Z0-9_]{1,62}$/;

/**
 * How many recent intakes the picker asks for. The endpoint caps at 100, so a full picker is a
 * truncation the screen discloses rather than a total it poses as.
 */
const PICKER_LIMIT = 100;

/**
 * One editable line. Kept as plain state rather than read from the DOM at submit time, so that the
 * value sent is provably the value shown.
 *
 * @typedef {{serviceCode: string, quantity: string, unit: string, basis: string}} Line
 */

/**
 * @returns {Line}
 */
function blankLine() {
  return { serviceCode: "", quantity: "", unit: "KG", basis: "STAFF_MEASUREMENT" };
}

/**
 * The line editor.
 *
 * Two kinds of change happen here and they must not be confused, because the first version of this
 * screen confused them and the result was a form that dropped focus on every keystroke.
 *
 *   **Structural** — a line added or removed. The set of cards changes, so the editor is rebuilt.
 *   **Value** — a character typed, a unit picked. The set of cards is unchanged, so nothing is
 *   rebuilt: the line's state is updated in place and only the two nodes that actually depend on
 *   the value are refreshed — the validity flag on the input, and that line's 6 kg notice.
 *
 * Typing `STANDARD_WASH_DRY` used to rebuild the whole form seventeen times and move the caret to
 * the end after each one. A Playwright check does not catch this, because `page.fill()` sets a
 * value in one shot; only a human typing does.
 *
 * @param {object} options
 * @param {Line[]} options.lines
 * @param {() => void} options.onStructuralChange rebuild — the set of lines changed
 * @param {() => void} options.onValueChange a value changed; invalidates the idempotency key only
 * @param {() => void} options.onAddLine Enter in a quantity field — same path as "Thêm dòng"
 * @returns {HTMLElement}
 */
function lineEditor({ lines, onStructuralChange, onValueChange, onAddLine }) {
  return h(
    "div",
    { class: "stack" },
    lines.map((line, index) => {
      const prefix = `quote-line-${index}`;
      // Owned by this card and refreshed in place, so the notice can follow the typed weight across
      // the 6 kg boundary without the input losing focus.
      const cliffHost = h("div");
      const refreshCliff = () => render(cliffHost, pricingCliffNotice(line.quantity, line.unit));

      const codeInput = h("input", {
        type: "text",
        value: line.serviceCode,
        autocomplete: "off",
        spellcheck: "false",
        placeholder: "STANDARD_WASH_DRY",
        dataFormat: "id",
        "aria-invalid": line.serviceCode && !SERVICE_CODE.test(line.serviceCode) ? "true" : "false",
        onInput: (event) => {
          const input = event.target;
          // Uppercasing is a convenience — the server's pattern demands it — but rewriting the
          // value unconditionally sends the caret to the end, so an operator correcting a character
          // in the middle of a code cannot. Rewrite only when the text actually changed, and put
          // the caret back where it was.
          const caret = input.selectionStart;
          const normalized = input.value.toUpperCase().replace(/\s+/g, "");
          if (normalized !== input.value) {
            input.value = normalized;
            input.setSelectionRange(caret, caret);
          }
          line.serviceCode = normalized;
          input.setAttribute(
            "aria-invalid",
            normalized && !SERVICE_CODE.test(normalized) ? "true" : "false",
          );
          onValueChange();
        },
      });

      const quantityInput = h("input", {
        // Deliberately `text`, not `number`. A number input lets the browser normalize, step and
        // localize the value, and the server is entitled to see the operator's exact keystrokes.
        type: "text",
        inputmode: "decimal",
        value: line.quantity,
        autocomplete: "off",
        maxlength: "16",
        placeholder: "6",
        onInput: (event) => {
          line.quantity = event.target.value;
          refreshCliff();
          onValueChange();
        },
        // A keydown, not an input event: it never touches the value channel above, it only takes
        // the structural path the "Thêm dòng" button takes. `preventDefault` keeps Enter inside a
        // form from submitting the quote when the operator meant another line.
        onKeydown: (event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            onAddLine();
          }
        },
      });

      const unitSelect = enumSelect("unit", UNITS, line.unit);
      unitSelect.addEventListener("change", (event) => {
        line.unit = /** @type {HTMLSelectElement} */ (event.target).value;
        refreshCliff();
        onValueChange();
      });

      const basisSelect = enumSelect("quantity_basis", BASES, line.basis);
      basisSelect.addEventListener("change", (event) => {
        line.basis = /** @type {HTMLSelectElement} */ (event.target).value;
        onValueChange();
      });

      refreshCliff();

      return h(
        "div",
        { class: "card", dataLine: String(index) },
        h(
          "div",
          { class: "spread" },
          h("p", { class: "eyebrow" }, `Dòng ${index + 1}`),
          lines.length > 1
            ? h(
                "button",
                {
                  type: "button",
                  dataVariant: "quiet",
                  onClick: () => {
                    lines.splice(index, 1);
                    onStructuralChange();
                  },
                },
                "Xoá dòng",
              )
            : null,
        ),
        h(
          "div",
          { class: "stack stack--tight" },
          labelled({ id: `${prefix}-code`, label: "Mã dịch vụ", hint: "Mã trong bảng giá chủ tiệm đã chốt (PRICEBOOK_V1, 43 mã): STANDARD_WASH_DRY, DRY_WET_CLOTHES, DRY_BEDDING, BED_BLANKET, BED_TOPPER, BED_PILLOW, IRON_KNIT, IRON_TROUSERS_SHIRT…", control: codeInput }),
          labelled({
            id: `${prefix}-qty`,
            label: "Khối lượng / số lượng",
            hint: "Gửi nguyên văn như bạn gõ. Màn hình này không làm tròn và không đổi định dạng.",
            control: quantityInput,
          }),
          labelled({ id: `${prefix}-unit`, label: "Đơn vị", control: unitSelect }),
          labelled({
            id: `${prefix}-basis`,
            label: "Cơ sở khối lượng",
            hint: "Chỉ STAFF_MEASUREMENT dưới chính sách đo lường đã công bố mới cho ra giá cuối.",
            control: basisSelect,
          }),
        ),
        cliffHost,
      );
    }),
  );
}

/**
 * Render a successful revision.
 *
 * @param {any} result
 * @returns {HTMLElement}
 */
function revisionResult(result) {
  return h(
    "div",
    { class: "card stack" },
    h(
      "div",
      { class: "spread" },
      h("h3", null, `Bản sửa đổi ${result.revision}`),
      h("div", { class: "row" }, priceStateBadge(result.finality)),
    ),
    result.replayed
      ? h(
          "div",
          { class: "notice", dataState: "info" },
          "Kết quả được phát lại: cùng khoá thao tác và cùng nội dung đã gửi trước đó. Không có " +
            "bản ghi mới nào được tạo.",
        )
      : null,
    warningBadges([...(result.reason_codes || []), ...(result.required_approvals || [])]),
    facts([
      ["Mã báo giá", shortId(result.quote_id), { mono: true }],
      ["Trạng thái", enumLabel(result.status)],
      ["Phiên bản dòng", `v${result.row_version}`],
      [
        "Tổng hiển thị cho khách",
        amount({
          min: result.display_total_min_vnd,
          max: result.display_total_max_vnd,
          finality: result.finality,
          unknownLabel: "chưa có — phí giao hàng chưa chốt",
        }),
        { span: true },
      ],
      [
        "Tiền dịch vụ (chưa phải số khách trả)",
        amount({
          min: result.net_service_subtotal_vnd,
          max: result.net_service_subtotal_vnd,
          finality: result.finality,
        }),
        { span: true },
      ],
      ["Giá niêm yết trước giảm", amount({
        min: result.list_service_subtotal_vnd,
        max: result.list_service_subtotal_vnd,
        finality: result.finality,
      }), { span: true }],
      [
        "Mã băm ảnh chụp",
        // The order form needs this hash verbatim; the row shortens it for reading and the
        // copy button carries the full string.
        result.snapshot_hash
          ? copyable({
              value: String(result.snapshot_hash),
              display: shortHash(result.snapshot_hash),
            })
          : shortHash(result.snapshot_hash),
        { mono: true, span: true },
      ],
    ]),
    reasonCodeList(result.reason_codes || []),
    result.required_approvals?.length
      ? h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Cần duyệt trước khi trình khách"),
          h("ul", null, result.required_approvals.map((code) => h("li", { class: "mono" }, code))),
        )
      : null,
  );
}

/**
 * One quote on the list.
 *
 * @param {any} item
 * @param {(item: any) => void} onPickRevision
 * @returns {HTMLElement}
 */
function quoteCard(item, onPickRevision) {
  return h(
    "article",
    { class: "card" },
    h(
      "div",
      { class: "spread" },
      h("strong", { class: "mono", title: item.quote_id }, shortId(item.quote_id)),
      priceStateBadge(item.finality),
    ),
    facts([
      ["Bản", `r${item.revision} · dòng v${item.row_version}`],
      ["Trạng thái", enumLabel(item.status)],
      [
        "Tổng hiển thị",
        amount({
          min: item.display_total_min_vnd,
          max: item.display_total_max_vnd,
          finality: item.finality,
          unknownLabel: "chưa có tổng",
        }),
        { span: true },
      ],
      ["Hiệu lực đến", dateTime(item.valid_until)],
      [
        "Ảnh chụp",
        item.snapshot_hash
          ? copyable({
              value: String(item.snapshot_hash),
              display: shortHash(item.snapshot_hash),
            })
          : shortHash(item.snapshot_hash),
        { mono: true, span: true },
      ],
    ]),
    h(
      "div",
      { class: "form__actions" },
      h(
        "button",
        { type: "button", onClick: () => onPickRevision(item) },
        "Thêm bản sửa đổi cho báo giá này",
      ),
    ),
  );
}

/**
 * @param {import("../core/router.js").RouteContext} [context]
 * @returns {HTMLElement}
 */
export function render_(context) {
  const store = storeId();
  const submission = new Submission("quote-create");
  // The Tiếp nhận screen's "Báo giá ngay" lands here. The id is a claim until the server confirms
  // it — `prefill` below resolves it before the form is allowed to rely on it.
  const prefillId = String(context?.query?.get("request") || "").trim();

  /** @type {{lines: Line[], orderRequestId: string, quoteId: string, expectedRevision: string, rowVersion: string, requestSummary: any|null}} */
  const draft = {
    lines: [blankLine()],
    orderRequestId: "",
    quoteId: "",
    expectedRevision: "0",
    rowVersion: "",
    requestSummary: null,
  };

  const builderBody = h("div");
  const resultHost = h("div", { class: "stack" });
  const result = resultLine();
  const summaryHost = h("div");
  const pickerHost = h("div", null, skeleton(2));

  // Any edit invalidates the idempotency key: the server hashes the payload alongside it, so
  // replaying the old key with changed content is a 409 rather than a replay. This is the whole
  // response to a typed character — no rebuild.
  const invalidateKey = () => submission.reset();

  // Reserved for changes to the *set* of lines, or to which quote is being revised. Anything that
  // only changes a value must call `invalidateKey` instead, or the caret moves as the operator types.
  const redrawBuilder = () => {
    invalidateKey();
    render(builderBody, buildForm());
  };

  // The one structural path for adding a line — the button below and Enter in a quantity field
  // both come through here.
  const addLine = () => {
    if (draft.lines.length >= MAX_LINES) return;
    draft.lines.push(blankLine());
    redrawBuilder();
  };

  const pickRevision = (item) => {
    draft.quoteId = item.quote_id;
    draft.expectedRevision = String(item.revision);
    draft.rowVersion = String(item.row_version);
    redrawBuilder();
    builderBody.scrollIntoView({ block: "start", behavior: "smooth" });
  };

  // The recorded-quotes list. The filter narrows the rows already fetched and computes nothing —
  // string matching over raw field values only (id, revision, finality); money fields are never
  // read by it. While it narrows, the status line keeps both counts so a shortened list never
  // reads as lost data.
  const list = listView({
    limit: LIST_LIMIT,
    fetch: () =>
      request(`/internal/v1/stores/${encodeURIComponent(store)}/quotes?limit=${LIST_LIMIT}`),
    renderItem: (item) => quoteCard(item, pickRevision),
    emptyText: "Chưa có báo giá nào trong cửa hàng này.",
    skeletonRows: 2,
    filterStatusHiddenWhenInactive: true,
    filter: {
      placeholder: "Lọc theo mã, bản, trạng thái…",
      noun: "báo giá",
      matches: (item, needle) =>
        [item.quote_id, String(item.revision), item.finality].some(
          (value) => value && String(value).toLowerCase().includes(needle),
        ),
    },
  });

  /**
   * The intake the form is bound to, re-rendered in place rather than through `redrawBuilder` —
   * a typed character in a line editor must never rebuild the form, and picking an intake is not
   * a structural change to the lines.
   */
  function refreshSummary() {
    const item = draft.requestSummary;
    render(
      summaryHost,
      item
        ? h(
            "div",
            { class: "notice", dataState: "info", id: "quote-request-summary" },
            h(
              "p",
              { class: "notice__title" },
              `Đang báo giá cho yêu cầu ${shortId(item.order_request_id)}`,
            ),
            h(
              "p",
              null,
              `${enumLabel(item.status)} · tiếp nhận lúc ${dateTime(item.created_at)} · ` +
                `liên hệ ${shortId(item.contact_binding_id)}`,
            ),
            h(
              "div",
              { class: "form__actions" },
              h(
                "button",
                {
                  type: "button",
                  dataVariant: "quiet",
                  onClick: () => {
                    draft.orderRequestId = "";
                    draft.requestSummary = null;
                    invalidateKey();
                    refreshSummary();
                  },
                },
                "Bỏ chọn yêu cầu này",
              ),
            ),
          )
        : null,
    );
  }

  /** Bind the form to an intake the server already returned. No fetch needed — the row is real. */
  const pickRequest = (item) => {
    draft.orderRequestId = item.order_request_id;
    draft.requestSummary = item;
    invalidateKey();
    refreshSummary();
    summaryHost.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };

  /**
   * The recent-intake picker. Its rows come from the same list endpoint the Tiếp nhận screen
   * shows, so a row here is never a guess at an identifier.
   */
  function pickerList(items) {
    if (!items.length) {
      return h(
        "div",
        { class: "notice", dataState: "info" },
        h(
          "p",
          null,
          "Chưa có lượt tiếp nhận nào trong cửa hàng này. Tạo một lượt ở màn hình ",
          h("a", { href: "#/order-requests" }, "Tiếp nhận"),
          " rồi quay lại đây.",
        ),
      );
    }
    return h(
      "div",
      { class: "stack stack--tight" },
      items.map((item) =>
        h(
          "div",
          { class: "spread" },
          h(
            "span",
            null,
            h(
              "strong",
              { class: "mono", title: item.order_request_id },
              shortId(item.order_request_id),
            ),
            ` · ${enumLabel(item.status)} · ${dateTime(item.created_at)}`,
          ),
          h(
            "button",
            { type: "button", dataVariant: "quiet", onClick: () => pickRequest(item) },
            "Dùng yêu cầu này",
          ),
        ),
      ),
    );
  }

  async function loadPicker() {
    render(pickerHost, skeleton(2));
    try {
      const items = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/order-requests?limit=${PICKER_LIMIT}`,
      );
      render(
        pickerHost,
        h(
          "div",
          { class: "stack stack--tight" },
          pickerList(items),
          isTruncated(items, PICKER_LIMIT)
            ? h(
                "p",
                { class: "hint" },
                `Chỉ ${PICKER_LIMIT} lượt tiếp nhận gần nhất hiện ở đây; máy chủ có thể còn nữa.`,
              )
            : null,
        ),
      );
    } catch (error) {
      render(pickerHost, errorNotice(error, { onRetry: () => void loadPicker() }));
    }
  }

  /**
   * Resolve a `?request=` claim against the server before binding the form to it. A 404 here is
   * honest: the id may belong to another store or may have been mistyped, and the console cannot
   * tell which — so it says exactly that and fills nothing in.
   */
  async function prefill(id) {
    if (!UUID.test(id)) {
      render(
        summaryHost,
        h(
          "div",
          { class: "notice", dataState: "warn", id: "quote-request-summary" },
          h("p", { class: "notice__title" }, "Mã yêu cầu trong đường dẫn không hợp lệ"),
          h("p", null, "Không có dữ kiện nào được điền sẵn; hãy chọn từ danh sách bên dưới."),
        ),
      );
      return;
    }
    try {
      const item = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/order-requests/${encodeURIComponent(id)}`,
      );
      draft.orderRequestId = item.order_request_id;
      draft.requestSummary = item;
      invalidateKey();
      refreshSummary();
    } catch (error) {
      if (/** @type {any} */ (error).kind === "MISSING") {
        render(
          summaryHost,
          h(
            "div",
            { class: "notice", dataState: "warn", id: "quote-request-summary" },
            h("p", { class: "notice__title" }, "Không tìm thấy yêu cầu này trong cửa hàng đang chọn"),
            h(
              "p",
              null,
              "Mã có thể thuộc cửa hàng khác hoặc đã bị gõ sai — máy chủ trả lờ cùng một cách cho " +
                "cả hai, nên màn hình này cũng không đoán. Không có dữ kiện nào được điền sẵn; " +
                "hãy chọn từ danh sách bên dưới.",
            ),
          ),
        );
      } else {
        render(summaryHost, errorNotice(/** @type {any} */ (error)));
      }
    }
  }

  function validate() {
    if (!draft.orderRequestId.trim()) {
      return "Chọn một yêu cầu từ danh sách tiếp nhận, hoặc mở mục nâng cao để nhập mã.";
    }
    if (draft.lines.length === 0) return "Cần ít nhất một dòng.";
    for (const [index, line] of draft.lines.entries()) {
      if (!SERVICE_CODE.test(line.serviceCode)) {
        return `Dòng ${index + 1}: mã dịch vụ phải viết hoa, dạng A-Z 0-9 _.`;
      }
      if (!line.quantity.trim()) return `Dòng ${index + 1}: chưa nhập khối lượng.`;
      if (line.quantity.length > 16) return `Dòng ${index + 1}: khối lượng quá 16 ký tự.`;
    }
    if (draft.quoteId && !draft.rowVersion) {
      return "Thêm bản sửa đổi cần phiên bản dòng hiện tại; hãy chọn lại báo giá từ danh sách.";
    }
    return "";
  }

  async function submit(event) {
    event.preventDefault();
    const problem = validate();
    if (problem) {
      setResult(result, "danger", problem);
      return;
    }

    const revisionMode = Boolean(draft.quoteId);
    const payload = {
      bound_order_request_id: draft.orderRequestId.trim(),
      lines: draft.lines.map((line) => ({
        service_code: line.serviceCode,
        // Verbatim. See the module note.
        quantity: line.quantity,
        unit: line.unit,
        quantity_basis: line.basis,
      })),
      ...(revisionMode
        ? {
            quote_id: draft.quoteId,
            expected_current_revision: Number.parseInt(draft.expectedRevision, 10) || 0,
          }
        : {}),
    };

    setResult(result, "warn", "Đang gửi cho bộ tính giá…");
    render(resultHost);

    try {
      const created = await request(`/internal/v1/stores/${encodeURIComponent(store)}/quotes`, {
        method: "POST",
        body: payload,
        idempotencyKey: submission.key(),
        ...(revisionMode ? { ifMatch: Number.parseInt(draft.rowVersion, 10) } : {}),
      });
      // Confirmed exactly once, in one place. The next submission is a new intent.
      submission.reset();
      setResult(result, "ok", `Đã ghi bản sửa đổi ${created.revision}.`);
      render(resultHost, revisionResult(created));
      await list.reload();
    } catch (error) {
      setResult(
        result,
        error.kind === "REQUIRE_HUMAN" ? "warn" : "danger",
        error.kind === "REQUIRE_HUMAN"
          ? "Bộ tính giá từ chối đoán. Không có bản ghi nào được tạo."
          : "Không tạo được bản sửa đổi.",
      );
      render(resultHost, errorNotice(error));
      revealError(resultHost);
    }
  }

  function buildForm() {
    const revisionMode = Boolean(draft.quoteId);
    const orderRequestInput = h("input", {
      type: "text",
      value: draft.orderRequestId,
      autocomplete: "off",
      dataFormat: "id",
      placeholder: "00000000-0000-0000-0000-000000000000",
      onInput: (event) => {
        draft.orderRequestId = event.target.value;
        draft.requestSummary = null;
        refreshSummary();
        submission.reset();
      },
    });

    return h(
      "form",
      { class: "form", onSubmit: submit },
      revisionMode
        ? h(
            "div",
            { class: "notice", dataState: "info" },
            h("p", { class: "notice__title" }, `Thêm bản sửa đổi cho ${shortId(draft.quoteId)}`),
            h(
              "p",
              null,
              `Sẽ gửi kèm bản hiện tại r${draft.expectedRevision} và phiên bản dòng v${draft.rowVersion}. ` +
                "Nếu ai đó vừa sửa báo giá này, máy chủ sẽ từ chối và bạn tải lại.",
            ),
            h(
              "div",
              { class: "form__actions" },
              h(
                "button",
                {
                  type: "button",
                  dataVariant: "quiet",
                  onClick: () => {
                    draft.quoteId = "";
                    draft.expectedRevision = "0";
                    draft.rowVersion = "";
                    redrawBuilder();
                  },
                },
                "Bỏ, tạo báo giá mới",
              ),
            ),
          )
        : null,
      // The bare-UUID path survives as a collapsed recovery hatch, not the default: picking from
      // Tiếp nhận or arriving with `?request=` binds a row the server returned, while a typed id
      // is a claim nobody checked. Typing here clears any resolved summary, because the claim and
      // the summary would no longer be about the same request.
      h(
        "details",
        null,
        h(
          "summary",
          { id: "quote-manual-toggle" },
          "Nhập mã yêu cầu thủ công (nâng cao — thường không cần)",
        ),
        h(
          "div",
          { class: "stack stack--tight" },
          labelled({
            id: "quote-order-request",
            label: "Mã yêu cầu đơn hàng (UUID)",
            hint: "Mỗi yêu cầu đơn hàng chỉ có đúng một báo giá. Lần thứ hai máy chủ báo trùng và yêu cầu thêm bản sửa đổi.",
            control: orderRequestInput,
          }),
        ),
      ),
      lineEditor({
        lines: draft.lines,
        onStructuralChange: redrawBuilder,
        onValueChange: invalidateKey,
        onAddLine: addLine,
      }),
      h(
        "div",
        { class: "form__actions" },
        draft.lines.length < MAX_LINES
          ? h(
              "button",
              {
                type: "button",
                onClick: addLine,
              },
              "Thêm dòng",
            )
          : h("p", { class: "hint" }, `Tối đa ${MAX_LINES} dòng cho một báo giá.`),
      ),
      h(
        "div",
        { class: "action-bar" },
        h(
          "button",
          { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
          "Tính giá",
        ),
      ),
      result,
    );
  }

  render(builderBody, buildForm());
  void loadPicker();
  void list.reload();
  if (prefillId) void prefill(prefillId);

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Ảnh chụp bất biến · Giá do máy chủ quyết"),
      h("h1", null, "Báo giá"),
      h(
        "p",
        { class: "screen__lede" },
        "Màn hình gửi dữ kiện và hiển thị kết quả. Không cộng, không làm tròn, không đoán khối lượng.",
      ),
    ),
    panel({
      eyebrow: "Lệnh",
      title: "Tính giá cho một yêu cầu",
      guardrail:
        "Mọi con số bên dưới do bộ tính giá xác định. Nếu thiếu dữ kiện, máy chủ trả REQUIRE_HUMAN " +
        "thay vì đoán, và không có dòng nào được ghi.",
      children: h(
        "div",
        { class: "stack" },
        summaryHost,
        h(
          "div",
          { class: "card" },
          h(
            "div",
            { class: "spread" },
            h("p", { class: "eyebrow" }, "Chọn từ tiếp nhận gần đây"),
            h(
              "button",
              { type: "button", dataVariant: "quiet", onClick: () => void loadPicker() },
              icon("refresh"),
              "Tải lại",
            ),
          ),
          pickerHost,
        ),
        builderBody,
        resultHost,
      ),
    }),
    panel({
      eyebrow: "Đã ghi",
      title: "Báo giá của cửa hàng",
      count: list.count,
      children: h(
        "div",
        { class: "stack" },
        list.bar.node,
        list.filterStatus,
        list.truncation,
        list.host,
      ),
    }),
  );
}

export const screen = {
  path: "/quotes",
  title: "Báo giá",
  capability: "QUOTES_READ",
  needsStore: true,
  render: render_,
};
