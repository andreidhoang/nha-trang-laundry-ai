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
 *   - **The service is picked by name, never typed as a code.** The picker offers the published
 *     pricebook's Vietnamese display names (`GET /internal/v1/pricebook/services`), grouped by
 *     category; the unit follows the chosen service because every service has exactly one
 *     canonical unit. If the catalog cannot be read, the form refuses too — the same digest gate
 *     that prices guards the picker, and a memorized code is not a fallback worth keeping.
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
import { enumVi, serviceCategoryVi } from "../core/i18n.js";
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

const BASES = ["STAFF_MEASUREMENT", "CUSTOMER_ESTIMATE", "APPROVED_MANUAL"];
const LIST_LIMIT = 100;
const MAX_LINES = 20;

/**
 * `QuoteLineRequest.service_code`, mirrored byte for byte from `main.py`'s `Field(pattern=...)`.
 *
 * It used to catch an operator's typo. Nobody can type a service code any more, so what it catches
 * now is a published pricebook offering a code the API would reject — a pricebook problem, and the
 * refusal message says so rather than blaming the person who only picked from a list.
 */
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
 * One published service, as the catalog route returns it.
 *
 * @typedef {{code: string, display_name: string, category: string, unit: string}} CatalogService
 */

/**
 * Group the catalog by category, in the order the published payload lists them.
 *
 * No sorting happens here on purpose. The payload's order is the published pricebook's order, and
 * a picker that re-sorts puts the services in a different sequence than the document the owner
 * approved — a difference nobody can see and everybody has to relearn. Grouping is the one
 * rearrangement worth making, because "Ủi" and "Giặt khô" are how the counter thinks about the
 * work; within a group the published order stands.
 *
 * @param {CatalogService[]} services
 * @returns {Array<{category: string, services: CatalogService[]}>}
 */
function serviceGroups(services) {
  /** @type {Array<{category: string, services: CatalogService[]}>} */
  const groups = [];
  for (const service of services) {
    let group = groups.find((item) => item.category === service.category);
    if (!group) {
      group = { category: service.category, services: [] };
      groups.push(group);
    }
    group.services.push(service);
  }
  return groups;
}

/**
 * @returns {Line}
 */
function blankLine() {
  // No unit, because the unit is the chosen service's and no service is chosen yet. The old `"KG"`
  // default was a guess the operator could neither see nor change once the unit picker was gone.
  return { serviceCode: "", quantity: "", unit: "", basis: "STAFF_MEASUREMENT" };
}

/**
 * The line editor.
 *
 * Two kinds of change happen here and they must not be confused, because the first version of this
 * screen confused them and the result was a form that dropped focus on every keystroke.
 *
 *   **Structural** — a line added or removed. The set of cards changes, so the editor is rebuilt.
 *   **Value** — a character typed, a service picked. The set of cards is unchanged, so nothing is
 *   rebuilt: the line's state is updated in place and only the nodes that actually depend on the
 *   value are refreshed — the unit echo, and that line's 6 kg notice.
 *
 * The weight field is where this still matters. Typing into it used to rebuild the whole form on
 * every keystroke and move the caret to the end after each one; a Playwright check does not catch
 * that, because `page.fill()` sets a value in one shot and only a human typing does. The service
 * field no longer has the problem at all, because picking from a list is one event rather than
 * seventeen — which is a second, quieter reason the picker replaced the typed code.
 *
 * @param {object} options
 * @param {CatalogService[]} options.catalog the published services the picker offers
 * @param {Line[]} options.lines
 * @param {() => void} options.onStructuralChange rebuild — the set of lines changed
 * @param {() => void} options.onValueChange a value changed; invalidates the idempotency key only
 * @param {() => void} options.onAddLine Enter in a quantity field — same path as "Thêm dòng"
 * @returns {HTMLElement}
 */
function lineEditor({ catalog, lines, onStructuralChange, onValueChange, onAddLine }) {
  // Grouped once, not once per line. Twenty lines × forty-three services is a rearrangement the
  // browser would otherwise redo on every structural rebuild, for an answer that cannot differ.
  const groups = serviceGroups(catalog);

  return h(
    "div",
    { class: "stack" },
    lines.map((line, index) => {
      const prefix = `quote-line-${index}`;
      // Owned by this card and refreshed in place, so the notice can follow the typed weight across
      // the 6 kg boundary without the input losing focus.
      const cliffHost = h("div");
      // The 6 kg notice is about a *priced* weight, so it says nothing until there is a service to
      // price. Before a pick there is no unit to be near a boundary of, and an empty quantity
      // parses to NaN — which the notice treats as "near the cliff" and would announce on every
      // blank line the operator adds.
      const refreshCliff = () =>
        render(cliffHost, line.serviceCode ? pricingCliffNotice(line.quantity, line.unit) : null);

      // The unit is shown, never chosen. Every published service has exactly one canonical unit,
      // so offering a second control was offering the operator a way to contradict the pricebook —
      // and the server refuses that contradiction (INCOMPATIBLE_UNIT) rather than pricing it.
      const unitEcho = h("p", { class: "hint" });
      const refreshUnit = () =>
        render(unitEcho, line.serviceCode ? `Tính theo: ${enumVi(line.unit)}` : null);

      // The picker. One tap chooses by the name the pricebook was approved with; the code travels
      // as the option's value, and the unit follows the service.
      const serviceSelect = h(
        "select",
        {
          name: "service_code",
          onChange: (event) => {
            const select = event.target;
            line.serviceCode = select.value;
            const service = catalog.find((item) => item.code === select.value);
            // Back to the placeholder clears the unit too. Keeping the previous service's unit on
            // a line with no service is a value nobody set, and the 6 kg notice reads it.
            line.unit = service ? service.unit : "";
            refreshUnit();
            refreshCliff();
            onValueChange();
          },
        },
        h("option", { value: "", selected: !line.serviceCode }, "— Chọn dịch vụ —"),
        groups.map((group) =>
          h(
            "optgroup",
            { label: serviceCategoryVi(group.category) },
            group.services.map((service) =>
              h(
                "option",
                { value: service.code, selected: service.code === line.serviceCode, title: service.code },
                service.display_name,
              ),
            ),
          ),
        ),
      );

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

      const basisSelect = enumSelect("quantity_basis", BASES, line.basis);
      basisSelect.addEventListener("change", (event) => {
        line.basis = /** @type {HTMLSelectElement} */ (event.target).value;
        onValueChange();
      });

      // Both derived nodes are painted once here, so a line that already has a service keeps its
      // unit and its 6 kg notice through a structural rebuild.
      refreshUnit();
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
          labelled({ id: `${prefix}-code`, label: "Dịch vụ", control: serviceSelect }),
          // Directly under the field it describes: the unit is a consequence of the service, and
          // reading it two fields away is reading it as a separate decision.
          unitEcho,
          labelled({
            id: `${prefix}-qty`,
            label: "Khối lượng / số lượng",
            hint: "Gửi nguyên văn như bạn gõ. Màn hình này không làm tròn và không đổi định dạng.",
            control: quantityInput,
          }),
          labelled({
            id: `${prefix}-basis`,
            label: "Cơ sở khối lượng",
            hint: "Chỉ khối lượng nhân viên đã cân mới tính ra giá cuối.",
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
      ["Trạng thái", enumVi(result.status)],
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
      ["Trạng thái", enumVi(item.status)],
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
    // Without a catalog there is no form to redraw. Saying so matters because `pickRevision` is
    // reachable from the recorded-quotes list, which loads independently: without this branch the
    // draft would silently take on a quote id and then scroll the operator to an error notice.
    if (catalog && catalog.length) render(builderBody, buildForm());
    else render(builderBody, catalogRefusal());
  };

  // The one structural path for adding a line — the button below and Enter in a quantity field
  // both come through here.
  const addLine = () => {
    if (draft.lines.length >= MAX_LINES) return;
    draft.lines.push(blankLine());
    redrawBuilder();
  };

  /** @type {CatalogService[]|null} the picker's data, once the server has answered */
  let catalog = null;

  /**
   * What stands where the form would be when the catalog is unusable.
   *
   * `errorNotice` withholds its retry button unless the error is retryable, and the likeliest
   * failure here — a 503 `PRICEBOOK_UNAVAILABLE` from an unpublished or digest-failing pricebook —
   * is deliberately not. That rule is right for a write; here it would leave the operator with a
   * dead screen and no control anywhere on it, because the form's host has no reload of its own
   * the way the intake picker does. So the refusal carries its own "Thử tải lại" — which asks the
   * server again and changes nothing if the answer is the same, unlike retrying a write.
   *
   * @param {unknown} [error] the failure, when there was one; absent means the catalog was empty
   * @returns {HTMLElement}
   */
  function catalogRefusal(error) {
    return h(
      "div",
      { class: "stack stack--tight" },
      error
        ? errorNotice(error)
        : h(
            "div",
            { class: "notice", dataState: "warn" },
            h("p", { class: "notice__title" }, "Bảng giá chưa có dịch vụ nào"),
            h(
              "p",
              null,
              "Máy chủ trả về một bảng giá rỗng, nên không có dịch vụ nào để chọn và không thể " +
                "tính giá. Đây là vấn đề của bảng giá đã công bố, không phải của thao tác này.",
            ),
          ),
      h(
        "div",
        { class: "form__actions" },
        h(
          "button",
          { type: "button", dataVariant: "quiet", onClick: () => void loadCatalog() },
          icon("refresh"),
          "Thử tải lại bảng giá",
        ),
      ),
    );
  }

  /**
   * The form cannot be built before the picker has its names, and it must not fall back to typed
   * codes when the catalog is unreadable — the same published payload prices and labels, so one
   * refusal covers both. An empty catalog counts as unreadable: a picker offering nothing but
   * "— Chọn dịch vụ —" is a form that cannot be completed, and it should say so rather than look
   * available.
   */
  async function loadCatalog() {
    render(builderBody, skeleton(2));
    try {
      const services = await request("/internal/v1/pricebook/services");
      catalog = Array.isArray(services) ? services : null;
      render(builderBody, catalog && catalog.length ? buildForm() : catalogRefusal());
    } catch (error) {
      catalog = null;
      render(builderBody, catalogRefusal(error));
    }
  }

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
              `${enumVi(item.status)} · tiếp nhận lúc ${dateTime(item.created_at)} · ` +
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
            ` · ${enumVi(item.status)} · ${dateTime(item.created_at)}`,
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
              "Mã có thể thuộc cửa hàng khác hoặc đã bị gõ sai — máy chủ trả lời cùng một cách cho " +
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
      if (!line.serviceCode) return `Dòng ${index + 1}: chưa chọn dịch vụ.`;
      // The picker can only yield a published code, so this can fail only if the catalog itself
      // carries a malformed one. That is a pricebook problem, and it says so rather than blaming
      // the operator for a field they cannot type into.
      if (!SERVICE_CODE.test(line.serviceCode)) {
        return `Dòng ${index + 1}: bảng giá trả về mã dịch vụ không hợp lệ (${line.serviceCode}).`;
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
        catalog,
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

  void loadCatalog();
  void loadPicker();
  void list.reload();
  if (prefillId) void prefill(prefillId);

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Giá do máy chủ quyết"),
      h("h1", null, "Báo giá"),
      h(
        "p",
        { class: "screen__lede" },
        "Chọn yêu cầu, chọn dịch vụ, nhập khối lượng — máy chủ tính theo bảng giá đã chốt. Màn hình này không tự cộng tiền, không làm tròn.",
      ),
    ),
    panel({
      eyebrow: "Lệnh",
      title: "Tính giá cho một yêu cầu",
      guardrail:
        "Thiếu dữ kiện thì máy chủ từ chối đoán và không ghi gì cả — người quyết, không phải máy.",
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
