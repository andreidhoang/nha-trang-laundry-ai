/**
 * The shared vocabulary every screen is built from.
 *
 * Screens are not allowed to invent their own way of showing state, refusals or unknown values,
 * because those three are exactly where an operations console misleads people. Everything that
 * carries meaning about *how much to trust a number* lives here and is used verbatim.
 *
 * @module ui/components
 */

import { field, h, render } from "../core/dom.js";
import { UNKNOWN, money, moneyRange } from "../core/format.js";
import { PRICE_STATE, REASON_NOTE, WARNING, enumLabel, warningFor } from "../core/i18n.js";

/**
 * A state badge: the mandated token, then a Vietnamese gloss.
 *
 * @param {{token: string, gloss: string, state: string}} spec
 * @returns {HTMLElement}
 */
export function badge(spec) {
  return h(
    "span",
    { class: "badge", dataState: spec.state, title: spec.gloss },
    spec.token,
    spec.gloss ? h("span", { class: "sr-only" }, ` — ${spec.gloss}`) : null,
  );
}

/**
 * The price-state badge for a quote's `finality`.
 *
 * `IMPLEMENTATION_ROADMAP_V1.md:886` requires `ƯỚC TÍNH`, `KHOẢNG GIÁ` and `ĐÃ DUYỆT` to be
 * explicit. An unrecognised finality is shown raw rather than guessed at — a new value from the
 * server must look unfamiliar, not be quietly absorbed into one of the three known ones.
 *
 * @param {string|null|undefined} finality
 * @returns {HTMLElement}
 */
export function priceStateBadge(finality) {
  const known = finality ? PRICE_STATE[finality] : null;
  if (known) return badge(known);
  return badge({ token: String(finality || UNKNOWN), gloss: "trạng thái giá lạ", state: "warn" });
}

/**
 * Badges for a list of domain reason codes, deduplicated by mandated warning.
 *
 * @param {string[]} reasonCodes
 * @returns {HTMLElement|null}
 */
export function warningBadges(reasonCodes) {
  if (!reasonCodes?.length) return null;
  /** @type {Map<string, {token: string, gloss: string, state: string}>} */
  const unique = new Map();
  for (const code of reasonCodes) {
    const warning = warningFor(code);
    unique.set(warning.token, warning);
  }
  return h("div", { class: "row" }, [...unique.values()].map(badge));
}

/**
 * The reason codes themselves, verbatim, each with a plain-language note.
 *
 * The codes are never paraphrased away. They are what an engineer greps for and what an eval
 * asserts on, and an operator who reports "it said DELIVERY_FEE_UNRESOLVED" is giving far better
 * information than one who reports "it said something about delivery".
 *
 * @param {string[]} reasonCodes
 * @param {string} [title]
 * @returns {HTMLElement|null}
 */
export function reasonCodeList(reasonCodes, title = "Máy chủ nêu các lý do sau") {
  if (!reasonCodes?.length) return null;
  return h(
    "div",
    { class: "notice", dataState: "warn" },
    h("p", { class: "notice__title" }, title),
    h(
      "ul",
      null,
      reasonCodes.map((code) =>
        h(
          "li",
          null,
          h("span", { class: "mono" }, code),
          REASON_NOTE[code] ? ` — ${REASON_NOTE[code]}` : null,
        ),
      ),
    ),
  );
}

/**
 * An amount together with the label that says how far to trust it.
 *
 * Money is never rendered bare in this application. `net_service_subtotal_vnd` in particular is the
 * *maximum* of the domain's range carried under a scalar name, so presenting it without a price
 * state would show the top of a range as a settled price.
 *
 * @param {object} spec
 * @param {number|null|undefined} spec.min
 * @param {number|null|undefined} spec.max
 * @param {string|null|undefined} spec.finality
 * @param {string} [spec.unknownLabel]
 * @returns {HTMLElement}
 */
export function amount(spec) {
  const range = moneyRange(spec.min, spec.max, spec.unknownLabel || UNKNOWN);
  return h(
    "span",
    { class: "row" },
    h("span", { class: range.isKnown ? "money" : "" }, range.text),
    priceStateBadge(spec.finality),
  );
}

/**
 * The 6kg disclosure.
 *
 * `DOMAIN_DATA_API_SPEC_V1.md:798` is unusually direct: "The non-monotonic 5.9→6.0 cliff is a
 * confirmed rule, not a software defect. UI must disclose it near the boundary."
 *
 * Two decisions were needed and neither is in any spec, so both are stated here rather than buried:
 *
 *   1. **The band.** No spec defines "near". This uses 5.0–7.0 kg inclusive, and discloses for any
 *      kilogram quantity it cannot parse. Erring toward showing the notice costs a line of text;
 *      erring the other way costs the disclosure the spec demands.
 *   2. **No prices.** The notice describes the *shape* of the cliff — that a lower weight can cost
 *      more than a higher one — and never states the per-kilogram amounts. Those live in the
 *      published pricebook, which the server owns and can republish; copying them here would put a
 *      second, stale opinion about money in the browser.
 *
 * @param {string} quantityText the value exactly as typed
 * @param {string} unit
 * @returns {HTMLElement|null}
 */
export function pricingCliffNotice(quantityText, unit) {
  if (unit !== "KG") return null;
  const parsed = Number.parseFloat(String(quantityText).replace(",", "."));
  const nearBoundary = !Number.isFinite(parsed) || (parsed >= 5 && parsed <= 7);
  if (!nearBoundary) return null;
  return h(
    "div",
    { class: "notice", dataState: "info" },
    h("p", { class: "notice__title" }, "Gần ngưỡng 6kg — bậc giá đổi tại đây"),
    h(
      "p",
      null,
      "Dưới 6kg và từ 6kg trở lên là hai bậc đơn giá khác nhau, và bậc dưới tính tối thiểu 1kg. " +
        "Hệ quả: một đơn 5,9kg có thể đắt hơn một đơn 6,0kg. Đây là quy tắc chủ đã xác nhận, " +
        "không phải lỗi, và không được làm trơn.",
    ),
    h(
      "p",
      { class: "hint" },
      "Không tách bao hay tách dòng để lách bậc giá. Số tiền do máy chủ tính; màn hình này không tính.",
    ),
  );
}

/**
 * A panel: the standard container for one list or one form.
 *
 * @param {object} spec
 * @param {string} [spec.eyebrow]
 * @param {string} spec.title
 * @param {string} [spec.count]
 * @param {string} [spec.guardrail] a standing rule that applies to everything in the panel
 * @param {unknown} [spec.children]
 * @param {unknown} [spec.actions]
 * @returns {HTMLElement}
 */
export function panel(spec) {
  return h(
    "section",
    { class: "card" },
    h(
      "div",
      { class: "card__header" },
      h(
        "div",
        null,
        spec.eyebrow ? h("p", { class: "eyebrow" }, spec.eyebrow) : null,
        h("h2", null, spec.title),
      ),
      h(
        "div",
        { class: "row" },
        spec.count !== undefined ? h("span", { class: "count" }, spec.count) : null,
        spec.actions,
      ),
    ),
    spec.guardrail ? h("p", { class: "notice", dataState: "warn" }, spec.guardrail) : null,
    spec.children,
  );
}

/**
 * @param {string} text
 * @returns {HTMLElement}
 */
export function empty(text) {
  return h("p", { class: "empty" }, text);
}

/**
 * @param {number} [rows]
 * @returns {HTMLElement}
 */
export function skeleton(rows = 3) {
  return h(
    "div",
    { class: "stack", ariaBusy: "true", "aria-label": "Đang tải" },
    Array.from({ length: rows }, () => h("div", { class: "skeleton" })),
  );
}

/**
 * Render an `ApiError` as something an operator can act on.
 *
 * The correlation id is always shown. It is the one string that appears both here and in the
 * server log, so "lỗi khi tạo báo giá" becomes a searchable event rather than a story.
 *
 * @param {import("../core/errors.js").ApiError|Error} error
 * @param {{onRetry?: () => void}} [options]
 * @returns {HTMLElement}
 */
export function errorNotice(error, options = {}) {
  const api = /** @type {import("../core/errors.js").ApiError} */ (error);
  const isApi = typeof api?.kind === "string";
  const state = isApi && (api.kind === "REQUIRE_HUMAN" || api.kind === "DENIED") ? "warn" : "danger";

  return h(
    "div",
    { class: "notice", dataState: state, role: "alert" },
    h("p", { class: "notice__title" }, error.message),
    isApi && api.detail && api.detail !== error.message
      ? h("p", { class: "mono" }, api.detail)
      : null,
    isApi && api.reasonCodes.length ? reasonCodeList(api.reasonCodes, "Mã lý do") : null,
    isApi && api.fieldErrors.length
      ? h(
          "ul",
          null,
          api.fieldErrors.map((item) =>
            h("li", null, h("span", { class: "mono" }, item.field), ` — ${item.message}`),
          ),
        )
      : null,
    isApi && api.kind === "RATE_LIMITED" && api.retryAfterSeconds
      ? h("p", null, `Thử lại sau ${api.retryAfterSeconds} giây.`)
      : null,
    isApi && api.correlationId
      ? h("p", { class: "hint mono" }, `mã theo dõi: ${api.correlationId}`)
      : null,
    options.onRetry && isApi && api.retryable
      ? h(
          "div",
          { class: "form__actions" },
          h("button", { type: "button", onClick: options.onRetry }, "Thử lại"),
        )
      : null,
  );
}

/**
 * A capability the console deliberately does not offer.
 *
 * `IMPLEMENTATION_ROADMAP_V1.md:394` makes "no hidden unsupported default" an exit criterion for
 * this milestone. A screen that quietly omits payment because payment does not exist satisfies the
 * letter of a working console and fails its purpose: the operator learns nothing, and the gap gets
 * filled with a paper note nobody reconciles. So every missing capability has a screen, and the
 * screen names what is missing, what blocks it, and what to do instead today.
 *
 * @param {object} spec
 * @param {string} spec.title
 * @param {string} spec.what what the specification asks for
 * @param {string} spec.missing the concrete reason it cannot be built yet
 * @param {string} spec.blockedBy the queue item, decision id, or absent aggregate
 * @param {string} [spec.today] the honest interim procedure
 * @param {string} [spec.specRef]
 * @returns {HTMLElement}
 */
export function unsupported(spec) {
  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "CHƯA HỖ TRỢ · KHÔNG CÓ DỮ LIỆU GIẢ"),
      h("h1", null, spec.title),
    ),
    h(
      "div",
      { class: "card" },
      h(
        "div",
        { class: "notice", dataState: "warn" },
        h("p", { class: "notice__title" }, "Màn hình này cố ý trống"),
        h(
          "p",
          null,
          "Không có API phía sau. Hiển thị một biểu mẫu ở đây sẽ tạo cảm giác dữ liệu đã được lưu " +
            "trong khi không có gì được lưu cả.",
        ),
      ),
      h(
        "dl",
        { class: "fields" },
        field("Đặc tả yêu cầu", spec.what, { span: true }),
        field("Thiếu", spec.missing, { span: true }),
        field("Bị chặn bởi", spec.blockedBy, { mono: true, span: true }),
        spec.today ? field("Hiện tại làm thế nào", spec.today, { span: true }) : null,
        spec.specRef ? field("Tham chiếu", spec.specRef, { mono: true, span: true }) : null,
      ),
    ),
  );
}

/**
 * Wrap a control the current role may not use.
 *
 * The control stays visible and disabled, with the server's own rule as the explanation.
 *
 * @param {HTMLElement} control
 * @param {{allowed: boolean, reason: string}} verdict
 * @returns {HTMLElement}
 */
export function gated(control, verdict) {
  if (verdict.allowed) return control;
  control.setAttribute("disabled", "");
  control.setAttribute("aria-disabled", "true");
  return h("div", { class: "stack stack--tight" }, control, h("p", { class: "hint" }, verdict.reason));
}

/**
 * A `role="status"` line under a form. Announced on change, cleared on resubmit.
 *
 * @returns {HTMLElement}
 */
export function resultLine() {
  return h("output", { class: "form__result", role: "status", "aria-live": "polite" });
}

/**
 * @param {HTMLElement} node
 * @param {"ok"|"warn"|"danger"|null} state
 * @param {unknown} content
 */
export function setResult(node, state, content) {
  if (state === null) {
    node.removeAttribute("data-state");
    render(node);
    return;
  }
  node.dataset.state = state;
  render(node, content);
}

/**
 * A labelled input row.
 *
 * @param {object} spec
 * @param {string} spec.id
 * @param {string} spec.label
 * @param {string} [spec.hint]
 * @param {HTMLElement} spec.control
 * @returns {HTMLElement}
 */
export function labelled(spec) {
  spec.control.id = spec.id;
  if (spec.hint) spec.control.setAttribute("aria-describedby", `${spec.id}-hint`);
  return h(
    "div",
    null,
    h("label", { for: spec.id }, spec.label),
    spec.control,
    spec.hint ? h("p", { class: "hint", id: `${spec.id}-hint` }, spec.hint) : null,
  );
}

/**
 * @param {string} name
 * @param {string[]} values
 * @param {string} [selected]
 * @returns {HTMLSelectElement}
 */
export function enumSelect(name, values, selected) {
  return /** @type {HTMLSelectElement} */ (
    h(
      "select",
      { name },
      values.map((value) =>
        h("option", { value, selected: value === selected }, enumLabel(value)),
      ),
    )
  );
}

/**
 * A row of key/value facts.
 *
 * @param {Array<[string, unknown, object?]|null>} entries
 * @returns {HTMLElement}
 */
export function facts(entries) {
  return h(
    "dl",
    { class: "fields" },
    entries.filter(Boolean).map(([term, value, options]) => field(term, value, options || {})),
  );
}

export { money, moneyRange, UNKNOWN, WARNING, badge as stateBadge };
