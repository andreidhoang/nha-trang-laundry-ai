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
 *
 * @module screens/quotes
 */

import { Submission, isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { count, dateTime, shortHash, shortId } from "../core/format.js";
import { storeId } from "../core/session.js";
import {
  amount,
  empty,
  enumSelect,
  errorNotice,
  facts,
  labelled,
  panel,
  priceStateBadge,
  pricingCliffNotice,
  reasonCodeList,
  resultLine,
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
 * @param {object} options
 * @param {() => void} options.onChange
 * @param {Line[]} options.lines
 * @returns {HTMLElement}
 */
function lineEditor({ lines, onChange }) {
  return h(
    "div",
    { class: "stack" },
    lines.map((line, index) => {
      const prefix = `quote-line-${index}`;
      const codeInput = h("input", {
        type: "text",
        value: line.serviceCode,
        autocomplete: "off",
        spellcheck: "false",
        placeholder: "STANDARD_WASH_DRY",
        dataFormat: "id",
        "aria-invalid": line.serviceCode && !SERVICE_CODE.test(line.serviceCode) ? "true" : null,
        onInput: (event) => {
          line.serviceCode = event.target.value.trim().toUpperCase();
          event.target.value = line.serviceCode;
          onChange();
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
          onChange();
        },
      });

      const unitSelect = enumSelect("unit", UNITS, line.unit);
      unitSelect.addEventListener("change", (event) => {
        line.unit = /** @type {HTMLSelectElement} */ (event.target).value;
        onChange();
      });

      const basisSelect = enumSelect("quantity_basis", BASES, line.basis);
      basisSelect.addEventListener("change", (event) => {
        line.basis = /** @type {HTMLSelectElement} */ (event.target).value;
        onChange();
      });

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
                    onChange();
                  },
                },
                "Xoá dòng",
              )
            : null,
        ),
        h(
          "div",
          { class: "stack stack--tight" },
          labelled({ id: `${prefix}-code`, label: "Mã dịch vụ", control: codeInput }),
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
        pricingCliffNotice(line.quantity, line.unit),
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
      ["Trạng thái", result.status, { mono: true }],
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
      ["Mã băm ảnh chụp", shortHash(result.snapshot_hash), { mono: true, span: true }],
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
 * @param {any[]} items
 * @param {(item: any) => void} onPickRevision
 * @returns {HTMLElement}
 */
function quoteList(items, onPickRevision) {
  if (!items.length) return empty("Chưa có báo giá nào trong cửa hàng này.");
  return h(
    "div",
    { class: "stack" },
    items.map((item) =>
      h(
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
          ["Trạng thái", item.status, { mono: true }],
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
          ["Ảnh chụp", shortHash(item.snapshot_hash), { mono: true, span: true }],
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
      ),
    ),
  );
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const store = storeId();
  const submission = new Submission("quote-create");

  /** @type {{lines: Line[], orderRequestId: string, quoteId: string, expectedRevision: string, rowVersion: string}} */
  const draft = {
    lines: [blankLine()],
    orderRequestId: "",
    quoteId: "",
    expectedRevision: "0",
    rowVersion: "",
  };

  const builderBody = h("div");
  const resultHost = h("div", { class: "stack" });
  const result = resultLine();
  const listHost = h("div", null, skeleton(2));
  const listCount = h("span", { class: "count" }, "…");
  const truncation = h("p", { class: "hint" });

  const redrawBuilder = () => {
    // Any edit invalidates the idempotency key: the server hashes the payload alongside it, so
    // replaying the old key with changed content is a 409 rather than a replay.
    submission.reset();
    render(builderBody, buildForm());
  };

  async function loadList() {
    render(listHost, skeleton(2));
    try {
      const items = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/quotes?limit=${LIST_LIMIT}`,
      );
      listCount.textContent = count(items, LIST_LIMIT);
      truncation.textContent = isTruncated(items, LIST_LIMIT)
        ? `Máy chủ trả tối đa ${LIST_LIMIT} bản ghi và đã trả đủ; có thể còn nữa. API này không có phân trang.`
        : "";
      render(
        listHost,
        quoteList(items, (item) => {
          draft.quoteId = item.quote_id;
          draft.expectedRevision = String(item.revision);
          draft.rowVersion = String(item.row_version);
          redrawBuilder();
          builderBody.scrollIntoView({ block: "start", behavior: "smooth" });
        }),
      );
    } catch (error) {
      render(listHost, errorNotice(error, { onRetry: () => void loadList() }));
    }
  }

  function validate() {
    if (!draft.orderRequestId.trim()) return "Nhập Order request UUID.";
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
      result.dataset.state = "danger";
      result.textContent = problem;
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

    result.dataset.state = "warn";
    result.textContent = "Đang gửi cho bộ tính giá…";
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
      result.dataset.state = "ok";
      result.textContent = `Đã ghi bản sửa đổi ${created.revision}.`;
      render(resultHost, revisionResult(created));
      await loadList();
    } catch (error) {
      result.dataset.state = error.kind === "REQUIRE_HUMAN" ? "warn" : "danger";
      result.textContent =
        error.kind === "REQUIRE_HUMAN"
          ? "Bộ tính giá từ chối đoán. Không có bản ghi nào được tạo."
          : "Không tạo được bản sửa đổi.";
      render(resultHost, errorNotice(error));
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
      labelled({
        id: "quote-order-request",
        label: "Order request UUID",
        hint: "Mỗi order request chỉ có đúng một báo giá. Lần thứ hai máy chủ báo trùng và yêu cầu thêm bản sửa đổi.",
        control: orderRequestInput,
      }),
      lineEditor({ lines: draft.lines, onChange: redrawBuilder }),
      h(
        "div",
        { class: "form__actions" },
        draft.lines.length < MAX_LINES
          ? h(
              "button",
              {
                type: "button",
                onClick: () => {
                  draft.lines.push(blankLine());
                  redrawBuilder();
                },
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
  void loadList();

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "ẢNH CHỤP BẤT BIẾN · GIÁ DO MÁY CHỦ QUYẾT"),
      h("h1", null, "Báo giá"),
      h(
        "p",
        { class: "screen__lede" },
        "Màn hình gửi dữ kiện và hiển thị kết quả. Không cộng, không làm tròn, không đoán khối lượng.",
      ),
    ),
    panel({
      eyebrow: "LỆNH",
      title: "Tính giá cho một yêu cầu",
      guardrail:
        "Mọi con số bên dưới do bộ tính giá xác định. Nếu thiếu dữ kiện, máy chủ trả REQUIRE_HUMAN " +
        "thay vì đoán, và không có dòng nào được ghi.",
      children: h("div", { class: "stack" }, builderBody, resultHost),
    }),
    panel({
      eyebrow: "ĐÃ GHI",
      title: "Báo giá của cửa hàng",
      count: listCount,
      children: h("div", { class: "stack" }, truncation, listHost),
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
