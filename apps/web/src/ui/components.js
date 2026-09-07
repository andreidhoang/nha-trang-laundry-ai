/**
 * The shared vocabulary every screen is built from.
 *
 * Screens are not allowed to invent their own way of showing state, refusals or unknown values,
 * because those three are exactly where an operations console misleads people. Everything that
 * carries meaning about *how much to trust a number* lives here and is used verbatim.
 *
 * @module ui/components
 */

import { isTruncated } from "../core/api.js";
import { field, h, render } from "../core/dom.js";
import { UNKNOWN, count, moneyRange, timeOnly } from "../core/format.js";
import { PRICE_STATE, REASON_NOTE, enumLabel, enumVi, warningFor } from "../core/i18n.js";

/**
 * The console's icon set: one 24×24 stroke grid, drawn with the safe `h()` builder.
 *
 * Icons here are decoration, never information — every place an icon appears, the word it
 * accompanies appears too, and the svg is `aria-hidden`. That is the same rule as the badge: state
 * and meaning are carried by text a staff member can quote, with the glyph only speeding up the
 * scan.
 */
const ICONS = {
  today: [
    h("rect", { x: "3", y: "4", width: "18", height: "18", rx: "2" }),
    h("line", { x1: "16", y1: "2", x2: "16", y2: "6" }),
    h("line", { x1: "8", y1: "2", x2: "8", y2: "6" }),
    h("line", { x1: "3", y1: "10", x2: "21", y2: "10" }),
  ],
  intake: [
    h("path", { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }),
    h("polyline", { points: "7 10 12 15 17 10" }),
    h("line", { x1: "12", y1: "15", x2: "12", y2: "3" }),
  ],
  quote: [
    h("path", { d: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" }),
    h("path", { d: "M14 2v6h6" }),
    h("path", { d: "M16 13H8" }),
    h("path", { d: "M16 17H8" }),
    h("path", { d: "M10 9H8" }),
  ],
  order: [
    h("path", {
      d: "M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z",
    }),
    h("path", { d: "M3.29 7 12 12l8.71-5" }),
    h("path", { d: "M12 22V12" }),
  ],
  incident: [
    h("path", { d: "M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" }),
    h("line", { x1: "4", y1: "22", x2: "4", y2: "15" }),
  ],
  approval: [
    h("path", { d: "M22 11.08V12a10 10 0 1 1-5.93-9.14" }),
    h("path", { d: "M22 4 12 14.01l-3-3" }),
  ],
  draft: [
    h("path", { d: "M12 20h9" }),
    h("path", { d: "M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" }),
  ],
  exception: [
    h("path", {
      d: "M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z",
    }),
    h("line", { x1: "12", y1: "9", x2: "12", y2: "13" }),
    h("line", { x1: "12", y1: "17", x2: "12.01", y2: "17" }),
  ],
  system: [h("path", { d: "M22 12h-4l-3 9L9 3l-3 9H2" })],
  staff: [
    h("path", { d: "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" }),
    h("circle", { cx: "9", cy: "7", r: "4" }),
    h("path", { d: "M23 21v-2a4 4 0 0 0-3-3.87" }),
    h("path", { d: "M16 3.13a4 4 0 0 1 0 7.75" }),
  ],
  gaps: [
    h("circle", { cx: "12", cy: "12", r: "10" }),
    h("line", { x1: "12", y1: "16", x2: "12", y2: "12" }),
    h("line", { x1: "12", y1: "8", x2: "12.01", y2: "8" }),
  ],
  refresh: [
    h("path", { d: "M23 4v6h-6" }),
    h("path", { d: "M1 20v-6h6" }),
    h("path", { d: "M3.51 9a9 9 0 0 1 14.85-3.36L23 10" }),
    h("path", { d: "M20.49 15a9 9 0 0 1-14.85 3.36L1 14" }),
  ],
  search: [
    h("circle", { cx: "11", cy: "11", r: "8" }),
    h("line", { x1: "21", y1: "21", x2: "16.65", y2: "16.65" }),
  ],
  "arrow-up": [
    h("line", { x1: "12", y1: "19", x2: "12", y2: "5" }),
    h("path", { d: "M5 12l7-7 7 7" }),
  ],
  more: [
    h("circle", { cx: "5", cy: "12", r: "1.75" }),
    h("circle", { cx: "12", cy: "12", r: "1.75" }),
    h("circle", { cx: "19", cy: "12", r: "1.75" }),
  ],
};

/**
 * A decorative icon by name. Throws on an unknown name so a typo fails at render time in
 * development rather than shipping a silently missing glyph.
 *
 * @param {keyof typeof ICONS} name
 * @returns {SVGElement}
 */
export function icon(name) {
  const shapes = ICONS[name];
  if (!shapes) throw new Error(`unknown icon: ${String(name)}`);
  return h(
    "svg",
    {
      class: "icon",
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": "2",
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": "true",
    },
    ...shapes.map((shape) => shape.cloneNode(true)),
  );
}

/**
 * A state badge: the Vietnamese gloss as the primary word, then the mandated token small.
 *
 * Vietnamese leads because it is the reading language of the counter; the token stays visible,
 * verbatim and unabbreviated, because it is what an engineer greps for and what an eval asserts
 * on. When no gloss exists the token stands alone — an unrecognised value must look unfamiliar,
 * not be quietly absorbed.
 *
 * `compact: true` keeps both spans but marks the badge as allowed to shed its token span on a
 * narrow viewport (components.css hides it under 48rem, where four full `Gloss (TOKEN)` badges
 * wrap an order card into unreadability). The full form survives in `title` and `aria-label`, and
 * print.css restores the token on paper. Only dense list rows may pass the flag — detail and
 * audit surfaces keep `Gloss (TOKEN)` always, per the UX refactor spec WS2.
 *
 * `title` overrides the tooltip. It exists for the one shape this component could not otherwise
 * express: a badge whose visible word is already the Vietnamese name (so there is no gloss to pair
 * with a token), on a screen that still owes an engineer the server's verbatim value. Without it,
 * moving a badge from `enumLabel` to `enumVi` would drop the token from the page entirely, and the
 * rule in `core/i18n` is that the token survives somewhere the reader can reach.
 *
 * @param {{token: string, gloss: string, state: string, tokenFirst?: boolean, compact?: boolean, title?: string}} spec
 * @returns {HTMLElement}
 */
export function badge(spec) {
  const primary = spec.tokenFirst ? spec.token : spec.gloss || spec.token;
  const secondary =
    primary === spec.token ? (spec.gloss && spec.gloss !== spec.token ? spec.gloss : null) : spec.token;
  const compact = Boolean(spec.compact && secondary && !spec.tokenFirst);
  return h(
    "span",
    {
      class: compact ? "badge badge--compact" : "badge",
      dataState: spec.state,
      title: spec.title || (secondary ? `${spec.token} — ${spec.gloss}` : spec.token),
      "aria-label": compact ? `${spec.gloss} (${spec.token})` : null,
    },
    primary,
    secondary
      ? h("span", { class: "badge__token mono" }, compact ? `(${secondary})` : secondary)
      : null,
  );
}

/**
 * One state dimension of an order, as a neutral badge.
 *
 * Deliberately uncoloured. Colouring `EXCEPTION` red and `PAID` green would be this console
 * ranking the severity of domain states, and nothing in the specification ranks them — the server
 * publishes the value, not an opinion about it. `components.css` already says state is never
 * conveyed by colour alone; here the word is the whole message, so the word is all there is.
 *
 * @param {string|null|undefined} value a server enum value
 * @returns {HTMLElement}
 */
export function dimensionBadge(value) {
  return badge({ token: enumVi(value), gloss: "", state: "neutral" });
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
 * A standing explanation folded behind its own question: a native `<details>`/`<summary>`.
 *
 * This is the WS2 progressive-disclosure component (`docs/STAFF_CONSOLE_UX_REFACTOR_SPEC_V1.md`).
 * What may go inside it is constrained by that spec, and the constraint is stated here because
 * this is the component that enforces it:
 *
 *   - **Context and limit education only.** What a read model does not carry, why a list is
 *     capped, which conditions a command will be checked against — the prose a repeat user no
 *     longer needs to read every visit.
 *   - **Never** a point-of-action safety disclosure (MANUAL_SEND_RECORDED ≠ delivered, settlement
 *     finality, the CONFIRMED_SENT danger meaning), never a capability refusal, never a truncation
 *     disclosure. Those stay visible, uncollapsed, next to the control or list they guard.
 *   - The summary names what the explanation covers, in plain Vietnamese as a question or a topic
 *     ("Tại sao nút Duyệt đang tắt?") — never "Xem thêm".
 *
 * There is deliberately no JS state: open/closed lives only in the DOM, so there is nothing to
 * persist (invariant 3) and nothing to restore. Data comes first — an `explain()` renders below
 * or beside what it explains, never between the operator and the first actionable control.
 * print.css flattens every `details.explain`, so a printed record keeps every explanation.
 *
 * @param {string} summary
 * @param {...unknown} body
 * @returns {HTMLElement}
 */
export function explain(summary, ...body) {
  return h(
    "details",
    { class: "explain" },
    h("summary", null, summary),
    h("div", { class: "explain__body stack stack--tight" }, ...body),
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
    { class: "stack", "aria-busy": "true", "aria-label": "Đang tải" },
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
 * Bring a just-rendered failure into view.
 *
 * A submit that fails below the fold used to leave the operator staring at an apparently dead
 * button. The scroll is `block: "nearest"` so a result already on screen does not move the page,
 * and the global reduced-motion rule in base.css forces it instant for people who ask for that.
 *
 * @param {HTMLElement} node
 */
export function revealError(node) {
  node.scrollIntoView({ block: "nearest", behavior: "smooth" });
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
 * The filter + freshness row that tops a list panel.
 *
 * Two honesty rules are built in rather than left to each screen. The reload control is a plain
 * read — never disabled, never throttled — and the stamp beside it records when the data on screen
 * was fetched, so "this list looks old" is a fact staff can read. The filter narrows the rows
 * already fetched and nothing else: it computes nothing, and the screen shows both counts while it
 * is active so a shortened list never reads as lost data.
 *
 * @param {object} spec
 * @param {() => Promise<unknown>} spec.onReload re-run the screen's reads
 * @param {{placeholder: string, label?: string, onChange: (value: string) => void}} [spec.filter]
 * @returns {{node: HTMLElement, stamp: HTMLElement, search: HTMLInputElement|null}}
 */
export function toolbar(spec) {
  const stamp = h("span", { class: "updated", role: "status" });
  const search = spec.filter
    ? /** @type {HTMLInputElement} */ (
        h("input", {
          type: "search",
          class: "toolbar__search",
          placeholder: spec.filter.placeholder,
          "aria-label": spec.filter.label || spec.filter.placeholder,
          onInput: (event) => spec.filter.onChange(event.target.value),
        })
      )
    : null;
  return {
    node: h(
      "div",
      { class: "toolbar" },
      search,
      h(
        "div",
        { class: "toolbar__meta" },
        stamp,
        h(
          "button",
          { type: "button", dataVariant: "quiet", onClick: () => void spec.onReload() },
          icon("refresh"),
          "Tải lại",
        ),
      ),
    ),
    stamp,
    search,
  };
}

/**
 * Record when the data on screen was fetched, in business time.
 *
 * @param {HTMLElement} stamp the `stamp` element from `toolbar()`
 * @param {Date} [at]
 */
export function markUpdated(stamp, at = new Date()) {
  stamp.textContent = `Cập nhật lúc ${timeOnly(at.toISOString())}`;
}

/**
 * The fetch–filter–truncate–skeleton cycle of a list panel, owned once here.
 *
 * Every list screen in the console runs the same lifecycle: show a skeleton, fetch with a limit,
 * stamp the fetch time, disclose truncation when the page came back full, render the rows or an
 * honest empty state, and offer a retry on failure. Six screens used to re-implement that with
 * small variations, and the variations are where a truncation disclosure or a "đang lọc X/Y" line
 * quietly goes missing. What stays per-screen is passed in:
 *
 *   - `fetch`, `renderItem`, `emptyText` — the data and what one row looks like.
 *   - `filter` — optional. The match itself (`matches`) stays with the screen, because which fields
 *     a filter reads is a statement about that screen's data. The status line is built here so the
 *     both-counts rule ("a shortened list never reads as lost data") cannot be forgotten.
 *   - `truncationText` — the disclosure sentence; the default covers the common no-pagination API.
 *     Pass `truncation: null` only when the screen renders its own truncation block (the unknown
 *     sends queue, whose page-size escalation control lives inside it) via `onLoadStart`/`onLoaded`.
 *   - `onLoadStart` / `onLoaded` / `onError` — hooks for state that survives the list, like the
 *     approvals countdown registry or the shadow review map. `onLoaded` runs after the meta line is
 *     updated and before the rows are rebuilt.
 *   - `clearMetaOnError` — whether a failed reload resets the count and truncation line. Screens
 *     that keep them (quotes, incidents) leave a stale count beside the error; screens that reset
 *     them (orders, approvals, shadow, exceptions) show "—". Both behaviours are preserved as found.
 *
 * The filter only ever narrows rows already fetched; it computes nothing and never refetches.
 * `reload()` resolves to whether the list on screen is now the server's current answer — the shadow
 * queue's conflict path depends on that signal.
 *
 * @param {object} spec
 * @param {() => Promise<any[]>} spec.fetch
 * @param {(item: any) => HTMLElement} spec.renderItem one row of the list
 * @param {string} spec.emptyText shown when there is nothing to list
 * @param {number|(() => number)} spec.limit the page size `fetch` asks for
 * @param {number} [spec.skeletonRows]
 * @param {(limit: number) => string} [spec.truncationText]
 * @param {null} [spec.truncation] pass `null` to manage the truncation block via the hooks
 * @param {object} [spec.filter]
 * @param {string} spec.filter.placeholder
 * @param {string} [spec.filter.label]
 * @param {string} spec.filter.noun the counted thing, for "Đang lọc X/Y <noun>"
 * @param {(item: any, needle: string) => boolean} spec.filter.matches
 * @param {string} [spec.filter.filteredEmptyText] shown when the filter hides every row
 * @param {boolean} [spec.filterStatusHiddenWhenInactive] hide the status line rather than empty it
 * @param {boolean} [spec.clearMetaOnError]
 * @param {() => void} [spec.onLoadStart]
 * @param {(items: any[]) => void} [spec.onLoaded]
 * @param {(error: unknown) => void} [spec.onError]
 * @returns {{
 *   bar: {node: HTMLElement, stamp: HTMLElement, search: HTMLInputElement|null},
 *   host: HTMLElement,
 *   count: HTMLElement,
 *   truncation: HTMLElement|null,
 *   filterStatus: HTMLElement,
 *   reload: () => Promise<boolean>,
 * }}
 */
export function listView(spec) {
  const rows = spec.skeletonRows ?? 3;
  const host = h("div", null, skeleton(rows));
  const countNode = h("span", { class: "count" }, "…");
  const truncation = spec.truncation === null ? null : h("p", { class: "hint" });
  const filterStatus = h("p", { class: "filter-status" });
  if (spec.filter && spec.filterStatusHiddenWhenInactive) filterStatus.hidden = true;

  /** The rows exactly as last fetched. The filter narrows a copy, never this list. */
  let fetched = /** @type {any[]} */ ([]);
  let filterText = "";

  function visibleItems() {
    if (!spec.filter) return fetched;
    const needle = filterText.trim().toLowerCase();
    if (!needle) return fetched;
    return fetched.filter((item) => spec.filter.matches(item, needle));
  }

  function renderVisible() {
    const visible = visibleItems();
    const active = Boolean(spec.filter && filterText.trim());
    if (spec.filter) {
      if (spec.filterStatusHiddenWhenInactive) {
        filterStatus.hidden = !active;
        if (active) {
          filterStatus.textContent = `Đang lọc ${visible.length}/${fetched.length} ${spec.filter.noun}`;
        }
      } else {
        filterStatus.textContent = active
          ? `Đang lọc ${visible.length}/${fetched.length} ${spec.filter.noun}`
          : "";
      }
    }
    render(
      host,
      visible.length
        ? h(
            "div",
            { class: "stack" },
            visible.map((item) => spec.renderItem(item)),
          )
        : empty(
            active && spec.filter.filteredEmptyText
              ? spec.filter.filteredEmptyText
              : spec.emptyText,
          ),
    );
  }

  const bar = toolbar({
    onReload: () => reload(),
    filter: spec.filter
      ? {
          placeholder: spec.filter.placeholder,
          label: spec.filter.label,
          onChange: (value) => {
            filterText = value;
            renderVisible();
          },
        }
      : undefined,
  });

  async function reload() {
    spec.onLoadStart?.();
    render(host, skeleton(rows));
    try {
      const items = await spec.fetch();
      fetched = items;
      const limit = typeof spec.limit === "function" ? spec.limit() : spec.limit;
      markUpdated(bar.stamp);
      countNode.textContent = count(items, limit);
      if (truncation) {
        truncation.textContent = isTruncated(items, limit)
          ? (spec.truncationText ||
            ((n) =>
              `Máy chủ trả tối đa ${n} bản ghi và đã trả đủ; có thể còn nữa. API này không có phân trang.`))(limit)
          : "";
      }
      spec.onLoaded?.(items);
      renderVisible();
      return true;
    } catch (error) {
      spec.onError?.(error);
      if (spec.clearMetaOnError) {
        countNode.textContent = UNKNOWN;
        if (truncation) truncation.textContent = "";
      }
      render(host, errorNotice(error, { onRetry: () => void reload() }));
      return false;
    }
  }

  return { bar, host, count: countNode, truncation, filterStatus, reload };
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
  // The id and the description belong on the thing a label can point at, which is not always the
  // element passed in. A caller that needs a field *and* a button beside it hands over a wrapper,
  // and putting the id there produced two elements carrying `intake-contact` -- the div and the
  // input inside it, which already had it. `<label for>` resolves to the first, so tapping the
  // label did not focus the field, and `aria-describedby` sat on a div a screen reader never reads
  // for that input. On a tablet, tapping the label is how a field gets focus.
  //
  // Found by driving the console in a real browser: `strict mode violation:
  // locator("#intake-contact") resolved to 2 elements`. Nothing else could see it -- duplicate ids
  // are valid JavaScript, render fine, and only misbehave when something tries to *use* the label.
  const labelable =
    spec.control.matches?.("input, select, textarea") === true
      ? spec.control
      : (spec.control.querySelector?.("input, select, textarea") ?? spec.control);
  labelable.id = spec.id;
  if (spec.hint) labelable.setAttribute("aria-describedby", `${spec.id}-hint`);
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
        h("option", { value, selected: value === selected, title: value }, enumVi(value)),
      ),
    )
  );
}

/**
 * A text field bound to one key of a draft object, marked `aria-invalid` as the operator types.
 *
 * The pattern only ever marks the field; it never decides the outcome — the form's `validate` does
 * that, in words, at submit time. The field is marked rather than the form redrawn, so the caret
 * stays where the operator put it. The value is trimmed (or `normalize`d) because a pasted value
 * regularly carries a trailing space, and never case-folded: a digest that arrives in the wrong
 * case is a real mismatch, and quietly repairing it would hide that the wrong thing was copied.
 *
 * Any edit resets the submission's idempotency key: the server hashes the payload alongside the
 * key, so replaying the old key with changed content is a 409 rather than a replay.
 *
 * @param {object} spec
 * @param {Record<string, string>} spec.target
 * @param {string} spec.key
 * @param {RegExp} spec.pattern
 * @param {string} spec.placeholder
 * @param {import("../core/api.js").Submission} spec.submission
 * @param {(value: string) => string} [spec.normalize]
 * @param {"id"|"hash"} [spec.format]
 * @returns {HTMLElement}
 */
export function boundInput(spec) {
  return h("input", {
    type: "text",
    value: spec.target[spec.key],
    autocomplete: "off",
    spellcheck: "false",
    dataFormat: spec.format || "id",
    placeholder: spec.placeholder,
    "aria-invalid":
      spec.target[spec.key] && !spec.pattern.test(spec.target[spec.key]) ? "true" : null,
    onInput: (event) => {
      const raw = spec.normalize ? spec.normalize(event.target.value) : event.target.value.trim();
      if (raw !== event.target.value) event.target.value = raw;
      spec.target[spec.key] = raw;
      spec.submission.reset();
      event.target.setAttribute("aria-invalid", raw && !spec.pattern.test(raw) ? "true" : "false");
    },
  });
}

/**
 * A paste-source value with a one-tap copy beside it.
 *
 * This is the WS6 interim-flow component (`docs/STAFF_CONSOLE_UX_REFACTOR_SPEC_V1.md`): until
 * the upstream read APIs land, identifiers and hashes travel between screens by paste — a
 * quote's snapshot hash into the order form, a staff UUID into the assignment forms. The
 * button's own label is the whole feedback channel, because the console has no toast system,
 * and nothing here is stored:
 *
 *   - **Success** — the label reads "Đã chép" for about two seconds, then
 *     returns to "Sao chép".
 *   - **Failure** — no clipboard API, a denied permission, an insecure context —
 *     the label says what to do instead ("Chép không được —
 *     bôi đen và tự chép") and the value is revealed in full with
 *     select-all styling (`.copyable--manual` in components.css): copying a shortened
 *     display would paste a truncated hash back as a 422.
 *
 * @param {object} spec
 * @param {string} spec.value the full value — what a successful copy puts on the clipboard
 * @param {string} [spec.display] what is shown before any interaction; defaults to the full value
 * @returns {HTMLElement}
 */
export function copyable(spec) {
  const text = h("span", { class: "copyable__text mono" }, spec.display ?? spec.value);
  const button = h(
    "button",
    { type: "button", dataVariant: "quiet", onClick: attempt },
    "Sao chép",
  );
  const root = h("span", { class: "copyable" }, text, button);
  let timer = 0;

  function attempt() {
    const clipboard = navigator.clipboard;
    // No API at all — an insecure context or an old WebView — skips straight to the manual
    // path; a present-but-refused write rejects and lands in the same place.
    if (!clipboard || typeof clipboard.writeText !== "function") {
      manual();
      return;
    }
    clipboard.writeText(spec.value).then(copied, manual);
  }

  function copied() {
    button.textContent = "Đã chép";
    clearTimeout(timer);
    timer = setTimeout(() => {
      button.textContent = "Sao chép";
    }, 2000);
  }

  function manual() {
    text.textContent = spec.value;
    root.classList.add("copyable--manual");
    button.textContent = "Chép không được — bôi đen và tự chép";
  }

  return root;
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
