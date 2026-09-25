/**
 * The quote toolkit: everything two screens need to show and close a price the server decided.
 *
 * `＋ Nhận đồ` (`screens/newOrder.js`) prices a bag and turns it into an order; `Báo giá`
 * (`screens/quotes.js`) lists what was priced and shows one revision read-only — including the
 * revision an approver opens from `#/approvals` before deciding it. Both render the same receipt,
 * and the price-band closing (`DEC-029`) that used to live inside `screens/quotes.js` lives here so
 * no screen imports another (`CONSOLE-REDESIGN-001`).
 *
 * The rules this module inherits from the V1 quote screen, unchanged:
 *
 *   - **No total is ever computed here.** The response carries a service subtotal and, separately,
 *     a display total that is null while the delivery fee is unresolved. Adding the first to a guess
 *     at the second is the defect `ENGINEERING_SPEC_V1.md:530` forbids. Every figure below is a
 *     server integer handed to `money()`/`moneyRange()` and nothing else.
 *   - **The scalar subtotals are withheld on a band.** `net_service_subtotal_vnd` and
 *     `list_service_subtotal_vnd` are filled from the domain's range *maximum* on a `RANGE`
 *     revision; printing one would show the top of an interval as the price. The band is read from
 *     `display_total_min_vnd`/`display_total_max_vnd`, which are a real pair.
 *   - **Closing a band is the staff member's own attested choice (`DEC-029`).** One amount per
 *     banded line, one press: the server raises a `SET_RANGE_PRICE` envelope attested by the person
 *     pressing, and the card applies it at once with the same body (the envelope binds its digest).
 *     If the envelope ever comes back `REQUESTED` again, the card follows the status rather than
 *     assuming who decides.
 *   - **A refusal is a result.** A 422 `REQUIRE_HUMAN` is the engine declining to guess; its reason
 *     codes are shown intact.
 *
 * @module ui/quoting
 */

import { Submission, request } from "../core/api.js";
import { BAND, bandReadiness, bandVerdict } from "../core/bands.js";
import { h, render } from "../core/dom.js";
import {
  UNKNOWN,
  countdown,
  dateTime,
  money,
  moneyRange,
  quantity as quantityText,
  shortHash,
  shortId,
  timeOnly,
} from "../core/format.js";
import { PRICE_STATE, enumVi, warningFor } from "../core/i18n.js";
import {
  badge,
  bandInput,
  errorNotice,
  explain,
  gated,
  icon,
  reasonCodeList,
  resultLine,
  revealError,
  setResult,
  skeleton,
} from "./components.js";
import { infoButton, statusPill, techDetails } from "./kit.js";

/** `QuantityBasis`, the order the counter meets them: weighed first. */
export const BASES = ["STAFF_MEASUREMENT", "CUSTOMER_ESTIMATE", "APPROVED_MANUAL"];

/**
 * The server's `FulfillmentMode`, with the short label the segmented control shows. The long gloss
 * (`enumVi`) stays the reading everywhere else; four words do not fit four buttons on a 390 px
 * phone.
 */
export const FULFILLMENT_MODES = [
  { value: "SELF_DROP_SELF_COLLECT", label: "Tại quầy" },
  { value: "PICKUP_ONLY", label: "Lấy tận nơi" },
  { value: "RETURN_ONLY", label: "Trả tận nơi" },
  { value: "PICKUP_AND_RETURN", label: "Lấy & trả" },
];

/** At most this many lines on one quote (`QuoteCreateRequest.lines` max_length). */
export const MAX_LINES = 20;

/**
 * `QuoteLineRequest.service_code`, mirrored byte for byte from `main.py`. Nobody types a code: what
 * this catches is a published pricebook offering one the API would reject.
 */
export const SERVICE_CODE = /^[A-Z][A-Z0-9_]{1,62}$/;

/**
 * The engine's word for "this line is priced by inspection and nobody has chosen a number yet" —
 * the one refusal whose same lines would succeed asked for as a band, so the one refusal with a
 * next action the console can offer.
 */
export const NEEDS_A_HUMAN_PRICE = "RANGE_PRICE_REQUIRES_HUMAN";

/** One second, as on the approvals queue. An envelope's remaining time is the point. */
const TICK_MS = 1000;

/**
 * One published service, as `GET /internal/v1/pricebook/services` returns it.
 *
 * @typedef {{code: string, display_name: string, category: string, unit: string}} CatalogService
 */

/**
 * Group the catalog by category, in the published order. No sorting: the payload's order is the
 * pricebook the owner approved, and within a group that order stands.
 *
 * @param {CatalogService[]} services
 * @returns {Array<{category: string, services: CatalogService[]}>}
 */
export function serviceGroups(services) {
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
 * The published name of a service. Falls back to the code rather than to a friendly guess: a line
 * whose code is not in the published catalog must look unfamiliar, not be labelled plausibly.
 *
 * @param {CatalogService[]|null} catalog
 * @param {string} code
 * @returns {string}
 */
export function serviceName(catalog, code) {
  const found = catalog?.find((item) => item.code === code);
  return found ? found.display_name : String(code);
}

/**
 * Why a typed quantity cannot be sent, in the counter's words — the same sentence while typing and
 * after pressing.
 *
 * @param {string} typed
 * @param {string} unit
 * @returns {string}
 */
export function quantityRefusal(typed, unit) {
  const byCount = unit && unit !== "KG";
  return (
    `Không đọc được “${typed}”. Gõ một số, ví dụ 5,5 hoặc 5.5 — lớn hơn 0, tối đa 3 chữ số sau ` +
    "dấu thập phân, không kèm chữ." +
    (byCount ? " Món tính theo cái/đôi/bộ thì phải là số nguyên." : "")
  );
}

/** The unit as a counter says it beside a number. */
const UNIT_SHORT = { KG: "kg", ITEM: "cái", PAIR: "đôi", SET: "bộ" };

/**
 * @param {string} unit
 * @returns {string}
 */
export function unitShort(unit) {
  return UNIT_SHORT[unit] || enumVi(unit).toLowerCase();
}

/**
 * "17:54" — the counter's clock, in the pinned business timezone, without seconds.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function clock(value) {
  return timeOnly(value).slice(0, 5);
}

/**
 * The finality as a pill, in the mandated words (`ƯỚC TÍNH`, `KHOẢNG GIÁ`, `ĐÃ DUYỆT`). An unknown
 * finality shows its raw token, never a guess; the gloss is the pill's title.
 *
 * @param {string|null|undefined} finality
 * @returns {HTMLElement}
 */
export function finalityPill(finality) {
  const known = finality ? PRICE_STATE[finality] : null;
  const pill = statusPill({
    state: known ? known.state : "warn",
    text: known ? known.token : String(finality || UNKNOWN),
    token: finality || undefined,
  });
  if (known) pill.title = `${known.token} — ${known.gloss}`;
  return pill;
}

/**
 * The mandated warnings a revision raises (`warningFor`), one pill each, deduplicated — the gloss
 * as the word and the token in `title` (spec V2 §4.1). A code that states a settled fact raises
 * none; every code is still listed verbatim in the reason list.
 *
 * @param {string[]} codes
 * @returns {HTMLElement|null}
 */
export function warningPills(codes) {
  /** @type {Map<string, {token: string, gloss: string, state: string}>} */
  const unique = new Map();
  for (const code of codes || []) {
    const warning = warningFor(code);
    if (warning) unique.set(warning.token, warning);
  }
  if (!unique.size) return null;
  return h(
    "div",
    { class: "row" },
    [...unique.values()].map((warning) =>
      statusPill({
        state: /** @type {any} */ (warning.state),
        text: warning.gloss,
        token: warning.token,
      }),
    ),
  );
}

/**
 * "Phiếu 17", or the channel customer's plain-words label. Never a UUID (tier 3). The ticket
 * number restarts every morning, so a list that spans days prints the date beside it.
 *
 * @param {any} item an order request (summary or create response) with the READ-ENRICH fields
 * @returns {string}
 */
export function customerLabel(item) {
  if (!item) return "";
  const reference = Number.isInteger(item.ticket_number)
    ? `Phiếu ${item.ticket_number}`
    : "Khách nhắn qua kênh";
  // CUSTOMER-001: an intake opened for a customer record carries the name they gave, read live.
  return item.customer_name ? `${item.customer_name} · ${reference}` : reference;
}

/**
 * The short name of a fulfilment mode, for the flow's own summary line.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function modeLabel(value) {
  return FULFILLMENT_MODES.find((mode) => mode.value === value)?.label || enumVi(value);
}

// ---------------------------------------------------------------------------------------------
// The receipt
// ---------------------------------------------------------------------------------------------

/**
 * What the shop's promotion programme did to this revision, as one receipt row plus its ⓘ.
 *
 * `PROMO-WIRING-001` made a 0 ₫ discount mean something specific — the programme ended, or never
 * covered this service — so the interval is on the receipt beside the figure rather than left in
 * the response. The end bound is **exclusive** and is printed verbatim ("đến trước"), never turned
 * into "the last day" by subtracting here. `null` means no programme was evaluated at all, which is
 * `—` and not `0 ₫`.
 *
 * @param {any} promotion a `QuotePromotionResponse`, or null/undefined
 * @returns {HTMLElement}
 */
export function promotionRow(promotion) {
  if (!promotion) {
    return receiptRow(
      h(
        "span",
        { class: "receipt__label" },
        "Khuyến mãi",
        infoButton(
          "Vì sao không có khuyến mãi?",
          h(
            "p",
            { class: "hint" },
            "Không có chương trình nào được xét trên bản báo giá này. Mã lý do bên dưới nói rõ là " +
              "chưa công bố chương trình nào, hay dòng khoảng giá chưa được chốt.",
          ),
        ),
      ),
      UNKNOWN,
    );
  }
  return h(
    "div",
    { class: "receipt__group" },
    receiptRow(
      h(
        "span",
        { class: "receipt__label" },
        "Khuyến mãi",
        infoButton(
          "Chương trình khuyến mãi chạy khi nào?",
          h("p", { class: "mono" }, String(promotion.policy_code)),
          // The two bounds, verbatim from the response and in the counter's own timezone.
          h(
            "p",
            { class: "mono" },
            `${dateTime(promotion.interval_start_at)} → trước ${dateTime(
              promotion.interval_end_at_exclusive,
            )}`,
          ),
          promotion.inside_interval === false
            ? h(
                "p",
                { class: "hint" },
                "Thời điểm tính giá của bản báo giá này nằm ngoài khoảng trên, nên mức giảm là 0 ₫. " +
                  "Đây là một con số không, có lý do đi kèm — không phải một ô bỏ trống.",
              )
            : null,
          h(
            "p",
            { class: "hint" },
            "Mốc sau là mốc kết thúc không bao gồm: chương trình chạy đến ngay trước nó, và thời điểm " +
              "đó đã ở ngoài chương trình. Cả hai mốc lấy nguyên từ máy chủ; màn hình này không tự trừ " +
              "ra một ngày nào khác.",
          ),
        ),
      ),
      h("span", { class: "money" }, `giảm ${money(promotion.discount_amount_vnd)}`),
    ),
    promotion.inside_interval === false
      ? h(
          "p",
          { class: "receipt__note" },
          `Chương trình đã kết thúc trước ${dateTime(promotion.interval_end_at_exclusive)}.`,
        )
      : null,
  );
}

/**
 * @param {unknown} label
 * @param {unknown} value
 * @param {{strong?: boolean}} [options]
 * @returns {HTMLElement}
 */
function receiptRow(label, value, options = {}) {
  return h(
    "div",
    { class: ["receipt__row", options.strong && "receipt__row--strong"] },
    typeof label === "string" ? h("span", { class: "receipt__label" }, label) : label,
    typeof value === "string" ? h("span", { class: "receipt__value money" }, value) : value,
  );
}

/**
 * One revision's lines as receipt rows: name, quantity and unit, and the line's own amount — or
 * its band when the line is priced by inspection. Nothing is combined or totalled here.
 *
 * @param {any} detail a `QuoteRevisionDetailResponse`
 * @param {CatalogService[]|null} catalog
 * @returns {HTMLElement}
 */
export function receiptLines(detail, catalog) {
  const lines = detail?.lines || [];
  if (!lines.length) {
    return h("p", { class: "receipt__note" }, "Bản này không có dòng nào đọc được.");
  }
  return h(
    "ul",
    { class: "receipt__lines", "aria-label": "Các món" },
    lines.map((line) =>
      h(
        "li",
        { class: "receipt__line", dataService: line.service_code },
        h(
          "span",
          { class: "receipt__item" },
          h("span", { class: "receipt__name" }, serviceName(catalog, line.service_code)),
          h(
            "span",
            { class: "receipt__qty" },
            `${quantityText(line.quantity)} ${unitShort(line.unit)}`,
          ),
        ),
        h(
          "span",
          { class: "receipt__value money" },
          line.price_kind === "RANGE"
            ? moneyRange(line.band_minimum_vnd, line.band_maximum_vnd, "chưa có khoảng giá").text
            : money(line.net_amount_vnd, "chưa có giá"),
        ),
      ),
    ),
  );
}

/**
 * The receipt of one revision: lines, subtotal, promotion, and the **total, big**.
 *
 * Painted in two passes on purpose: the totals the server just returned paint at once, and the
 * lines (which only the revision read carries) paint when `setLines` is called — a number the
 * server already knows never sits behind a spinner.
 *
 * @param {object} spec
 * @param {any} spec.revision a `QuoteRevisionResponse` or `QuoteRevisionDetailResponse`
 * @param {any} [spec.detail] the revision read, when already in hand
 * @param {CatalogService[]|null} spec.catalog
 * @param {string|null} [spec.mode] the fulfilment mode it was priced under
 * @param {string} [spec.title]
 * @param {unknown} [spec.stale] a node shown above the total when the inputs moved since pricing
 * @param {unknown} [spec.extra] further rows among the sums (a remedy credit the server applied)
 * @param {unknown} [spec.actions] controls shown under the total (band closer, credit…)
 * @returns {HTMLElement & {setLines: (detail: any|null, error?: unknown) => void}}
 */
export function receipt(spec) {
  const revision = spec.revision;
  const band = revision.finality === "RANGE";
  const linesHost = h("div", { class: "receipt__lines-host" });
  const total = moneyRange(
    revision.display_total_min_vnd,
    revision.display_total_max_vnd,
    "chưa có tổng",
  );
  const delivers = Boolean(spec.mode && spec.mode !== "SELF_DROP_SELF_COLLECT");
  const hasScalars =
    Object.hasOwn(revision, "net_service_subtotal_vnd") ||
    Object.hasOwn(revision, "list_service_subtotal_vnd");

  const node = /** @type {HTMLElement & {setLines: (detail: any|null, error?: unknown) => void}} */ (
    h(
      "section",
      {
        class: "receipt surface",
        dataFinality: revision.finality || null,
        dataStatus: revision.status || null,
        "aria-label": "Hoá đơn tạm",
      },
      h(
        "div",
        { class: "receipt__head" },
        h("h2", { class: "receipt__title" }, spec.title || `Bản sửa đổi ${revision.revision}`),
        finalityPill(revision.finality),
      ),
      linesHost,
      h(
        "div",
        { class: "receipt__sums" },
        hasScalars
          ? receiptRow(
              h(
                "span",
                { class: "receipt__label" },
                "Giá niêm yết",
                band
                  ? infoButton(
                      "Vì sao hai dòng trên là “—”?",
                      h(
                        "p",
                        { class: "hint" },
                        "Máy chủ gửi hai số này dưới một cái tên số đơn, nhưng ruột của chúng là đầu " +
                          "trên của khoảng giá. Với một bản khoảng giá, in đầu trên ra sẽ đọc như giá " +
                          "đã chốt. Khoảng đầy đủ nằm ở dòng tổng.",
                      ),
                    )
                  : null,
              ),
              band ? UNKNOWN : money(revision.list_service_subtotal_vnd),
            )
          : null,
        hasScalars ? promotionRow(revision.promotion) : null,
        hasScalars
          ? receiptRow("Tiền dịch vụ", band ? UNKNOWN : money(revision.net_service_subtotal_vnd))
          : null,
        delivers && total.isKnown
          ? receiptRow("Phí giao", h("span", { class: "receipt__value" }, "đã gồm trong tổng"))
          : null,
        spec.extra || null,
      ),
      spec.stale || null,
      h(
        "div",
        { class: "receipt__total" },
        h("span", { class: "receipt__total-label" }, band ? "Khoảng giá" : "Tổng khách trả"),
        h(
          "span",
          { class: ["receipt__total-amount", total.isKnown && "money"], dataTotal: "true", dataRange: total.isRange ? "true" : null },
          total.text,
        ),
      ),
      total.isKnown
        ? null
        : h(
            "p",
            { class: "receipt__note", dataState: "warn" },
            "Chưa có tổng: cần quãng đường đã đo, hoặc phí giao khách đã đồng ý.",
          ),
      warningPills([...(revision.reason_codes || []), ...(revision.required_approvals || [])]),
      revision.required_approvals?.length
        ? h(
            "div",
            { class: "notice", dataState: "warn" },
            h("p", { class: "notice__title" }, "Cần duyệt trước khi trình khách"),
            h(
              "ul",
              null,
              revision.required_approvals.map((code) => h("li", { class: "mono" }, code)),
            ),
          )
        : null,
      revision.replayed
        ? h(
            "div",
            { class: "notice", dataState: "info" },
            "Kết quả được phát lại: cùng khoá thao tác và cùng nội dung đã gửi trước đó. Không có " +
              "bản ghi mới nào được tạo.",
          )
        : null,
      revision.reason_codes?.length
        ? explain(
            `Máy chủ nêu ${revision.reason_codes.length} lý do`,
            reasonCodeList(revision.reason_codes || []),
          )
        : null,
      spec.actions || null,
      techDetails([
        ["Mã báo giá", shortId(revision.quote_id), { copy: String(revision.quote_id) }],
        ["Bản sửa đổi", String(revision.revision)],
        ["Phiên bản dòng", `v${revision.row_version}`],
        ["Trạng thái", `${enumVi(revision.status)} (${revision.status})`],
        ["Độ chắc của giá", String(revision.finality)],
        revision.valid_until ? ["Hiệu lực đến", dateTime(revision.valid_until)] : null,
        [
          "Mã băm ảnh chụp",
          shortHash(revision.snapshot_hash),
          revision.snapshot_hash ? { copy: String(revision.snapshot_hash) } : undefined,
        ],
      ]),
    )
  );
  node.setLines = (detail, error) => {
    if (error) {
      render(
        linesHost,
        h(
          "p",
          { class: "receipt__note", dataState: "warn" },
          "Chưa đọc được các dòng của bản này. Tổng ở dưới là số máy chủ đã trả.",
        ),
      );
      return;
    }
    render(linesHost, detail ? receiptLines(detail, spec.catalog) : skeleton(2));
  };
  node.setLines(spec.detail || null);
  return node;
}

// ---------------------------------------------------------------------------------------------
// Acceptance: what a refusal means and whether pressing again can help
// ---------------------------------------------------------------------------------------------

/**
 * What to tell the counter when the customer's acceptance fails, and whether pressing again helps.
 *
 *   - **The price is no longer agreeable** — expired, or refused for a missing fact. The way
 *     forward is pricing again, so `final` is true and `reprice` offers the way back.
 *   - **The quote moved underneath the screen** — the server, not this screen, decides whether the
 *     next press is refused.
 *   - **The answer was lost** — timeout, network, a 5xx. The attestation may have landed; pressing
 *     again is safe because the same idempotency key replays the recorded acceptance.
 *
 * @param {any} error
 * @returns {{title: string, final: boolean, reprice: boolean, blocked: string}}
 */
export function acceptFailure(error) {
  const detail = String(error?.detail || "");
  // The server marks an expired price with a prefix, so this does not match on prose (`FR-QTE-010`).
  if (detail.startsWith("QUOTE_EXPIRED")) {
    return {
      title:
        "Báo giá này đã quá hạn (mỗi báo giá có giá trị một ngày), nên không chốt được nữa. " +
        "Không có gì được ghi. Cân lại, bấm “Tính giá” để ra giá hôm nay, rồi đọc lại cho khách.",
      final: true,
      reprice: true,
      blocked: "Không bấm được: báo giá đã quá hạn. Tính giá lại trước.",
    };
  }
  if (error?.kind === "REQUIRE_HUMAN") {
    return {
      title:
        "Chưa chốt được. Máy chủ nêu lý do bên dưới — thường là khối lượng mới là khách ước " +
        "lượng, cần cân lại rồi tính giá lại. Không có gì được ghi.",
      final: true,
      reprice: true,
      blocked: "Không bấm được: cần tính giá lại trước khi khách chốt.",
    };
  }
  const unknown =
    error?.kind === "TIMEOUT" ||
    error?.kind === "NETWORK" ||
    error?.kind === "FAULT" ||
    error?.kind === "UNAVAILABLE";
  if (unknown) {
    return {
      title:
        "Chưa biết lời xác nhận đã được ghi hay chưa. Bấm lại “Khách đồng ý — tạo đơn” — lần " +
        "bấm lại dùng cùng mã thao tác, nên máy chủ không ghi hai lần.",
      final: false,
      reprice: false,
      blocked: "",
    };
  }
  if (error?.kind === "OFFLINE") {
    return {
      title: `${error.message} Có mạng lại thì bấm lại “Khách đồng ý — tạo đơn”.`,
      final: false,
      reprice: false,
      blocked: "",
    };
  }
  return {
    title: `Chưa chốt được: ${error?.message || "máy chủ từ chối."} Không có gì được ghi.`,
    final: false,
    reprice: false,
    blocked: "",
  };
}

// ---------------------------------------------------------------------------------------------
// Closing a published band (DEC-029)
// ---------------------------------------------------------------------------------------------

/**
 * Closing a published band: choose, press once, and the price is written.
 *
 * Two commands — propose, apply — because the envelope binds a digest of the amounts and
 * application re-derives it from the amounts in hand. Since `DEC-029` the proposal is the staff
 * member's own attestation, so this card sends the second command itself as soon as the first
 * answers `APPROVED`. Two consequences are stated on screen rather than discovered: if writing the
 * price fails the amounts live only in this card until it succeeds, and the envelope expires (the
 * countdown is a control, not decoration).
 *
 * The bound comes from the server per line, off the revision the customer was read — never from
 * the live pricebook and never from anything this screen assembled.
 *
 * @param {object} spec
 * @param {any} spec.result the stored `RANGE` revision this closes
 * @param {string} spec.store
 * @param {CatalogService[]|null} spec.catalog
 * @param {{allowed: boolean, reason: string}} spec.writeVerdict
 * @param {(closed: any) => Promise<void>|void} spec.onClosed given the new exact revision
 * @param {any} [spec.detail] the revision read, when the caller already holds it
 * @returns {HTMLElement}
 */
export function bandCloser(spec) {
  const root = h("div", { class: "stack", id: "quote-band-closer" }, skeleton(2));

  /**
   * @type {{lines: any[], typed: Record<string, string>, approval: any|null}}
   */
  const state = { lines: [], typed: {}, approval: null };

  const status = resultLine();
  const errorHost = h("div");
  const clockHost = h("span", { class: "row" });

  // One key per intent, minted on first use and cleared on an edit or a commit. A key per press
  // would make a retry after a timeout a *second* envelope for one garment.
  const proposing = new Submission(`range-price-propose-${spec.result.quote_id}`);
  const applying = new Submission(`range-price-apply-${spec.result.quote_id}`);

  // One interval for this card, stopped the moment the card leaves the document.
  const timer = setInterval(() => {
    if (!root.isConnected) {
      clearInterval(timer);
      return;
    }
    if (state.approval) render(clockHost, expiryBadge(state.approval.expires_at));
  }, TICK_MS);

  /** The body of the request both commands take. Identical by construction, which is the point. */
  const body = () => ({
    expected_current_revision: spec.result.revision,
    expected_snapshot_hash: spec.result.snapshot_hash,
    choices: state.lines.map((line) => ({
      service_code: line.service_code,
      amount_vnd: bandVerdict(
        state.typed[line.service_code],
        line.band_minimum_vnd,
        line.band_maximum_vnd,
      ).amount,
    })),
  });

  /** @param {any} detail */
  function adopt(detail) {
    state.lines = (detail.lines || []).filter((line) => line.price_kind === "RANGE");
    draw();
  }

  async function load() {
    render(root, skeleton(2));
    try {
      adopt(
        await request(
          `/internal/v1/stores/${encodeURIComponent(spec.store)}/quotes/${encodeURIComponent(spec.result.quote_id)}?revision=${encodeURIComponent(String(spec.result.revision))}`,
        ),
      );
    } catch (error) {
      render(
        root,
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Chưa đọc được các dòng của bản báo giá này"),
          h(
            "p",
            null,
            "Không có các dòng thì không biết khoảng giá của từng món, và bảng vận hành không tự " +
              "đoán khoảng. Thử tải lại; nếu vẫn vậy thì báo kỹ thuật kèm mã theo dõi bên dưới.",
          ),
        ),
        errorNotice(error, { onRetry: () => void load() }),
        h(
          "div",
          { class: "form__actions" },
          h(
            "button",
            { type: "button", dataVariant: "quiet", onClick: () => void load() },
            icon("refresh"),
            "Tải lại các dòng",
          ),
        ),
      );
    }
  }

  /** Redraw the whole card. Called on a step change, never on a keystroke. */
  function draw() {
    if (!state.lines.length) {
      render(
        root,
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Bản này không có dòng nào theo khoảng giá"),
          h(
            "p",
            null,
            "Máy chủ báo đây là bản khoảng giá nhưng không dòng nào mang khoảng. Đừng gửi số nào; " +
              "tải lại báo giá và báo kỹ thuật.",
          ),
        ),
      );
      return;
    }
    render(root, state.approval ? awaitingApproval() : chooseAmounts());
  }

  /** Step one: one amount per banded line, checked while it is typed. */
  function chooseAmounts() {
    const fields = state.lines.map((line, index) =>
      bandInput({
        id: `quote-band-${index}`,
        label: serviceName(spec.catalog, line.service_code),
        hint:
          `${quantityText(line.quantity)} ${enumVi(line.unit)} · ` +
          "giá cho cả dòng, không phải đơn giá.",
        minimum: line.band_minimum_vnd,
        maximum: line.band_maximum_vnd,
        value: state.typed[line.service_code] || "",
        onInput: (value) => {
          state.typed[line.service_code] = value;
          // An edited amount is different content, so a different intent and a different key.
          proposing.reset();
          refreshReadiness();
        },
      }),
    );

    const send = h(
      "button",
      { type: "button", class: "btn btn--block", dataVariant: "primary", dataRequiresNetwork: "true" },
      "Chốt giá này",
    );
    send.addEventListener("click", () => void propose(send));

    const readinessHost = h("div");
    const refreshReadiness = () => render(readinessHost, readinessNotice());
    refreshReadiness();

    return h(
      "div",
      { class: "stack band-closer" },
      h(
        "div",
        { class: "band-closer__head" },
        h("h3", { class: "band-closer__title" }, "Chốt giá trong khoảng"),
        infoButton(
          "Chốt giá trong khoảng là gì?",
          h(
            "div",
            { class: "notice", dataState: "info" },
            h("p", { class: "notice__title" }, "Chốt một giá trong khoảng đã niêm yết"),
            h(
              "p",
              null,
              "Khoảng giá là chủ tiệm đã cho phép trước: mọi số trong khoảng đều hợp lệ, và bảng vận " +
                "hành không tự chọn giúp số nào. Bạn chọn số khách đồng ý rồi bấm chốt — không cần " +
                "chờ chủ tiệm.",
            ),
          ),
        ),
      ),
      h(
        "p",
        { class: "hint" },
        "Tên bạn được ghi cùng con số, không sửa được, và chủ tiệm xem lại được. Số ngoài " +
          "khoảng thì máy chủ từ chối.",
      ),
      h("div", { class: "stack stack--tight" }, fields),
      readinessHost,
      gated(send, spec.writeVerdict),
      status,
      errorHost,
    );
  }

  /**
   * What stands between the last keystroke and the button. The server refuses a half-closed
   * revision outright, so the console names the line that is still open.
   *
   * @returns {HTMLElement|null}
   */
  function readinessNotice() {
    const readiness = bandReadiness(state.lines, state.typed);
    if (readiness.ready) return null;
    const open = readiness.blocked.filter((item) => item.state === BAND.EMPTY);
    return h(
      "div",
      { class: "notice", dataState: "warn" },
      h("p", { class: "notice__title" }, "Chưa gửi được: còn dòng chưa có giá hợp lệ"),
      h(
        "p",
        null,
        "Máy chủ chốt cả bản một lần, không chốt từng dòng. Một bản nửa quyết nửa đoán còn tệ hơn " +
          "là chưa có tổng, nên phải điền đủ mọi dòng trước khi gửi.",
      ),
      h(
        "ul",
        null,
        readiness.blocked.map((item) =>
          h(
            "li",
            null,
            h("span", null, serviceName(spec.catalog, item.serviceCode)),
            open.includes(item) ? " — chưa nhập giá" : " — số đang nhập chưa dùng được",
          ),
        ),
      ),
    );
  }

  /**
   * Step two, reached only when the price is not written yet: the proposal came back attested and
   * writing it failed, or (if `DEC-029` is ever reversed) the envelope waits for an owner.
   */
  function awaitingApproval() {
    const attested = state.approval.status === "APPROVED";
    const apply = h(
      "button",
      { type: "button", class: "btn btn--block", dataVariant: "primary", dataRequiresNetwork: "true" },
      attested ? "Ghi giá vào báo giá" : "Áp dụng giá đã duyệt",
    );
    apply.addEventListener("click", () => void applyPrices(apply));

    render(clockHost, expiryBadge(state.approval.expires_at));

    return h(
      "div",
      { class: "stack band-closer" },
      h(
        "div",
        { class: "spread" },
        h(
          "p",
          { class: "eyebrow" },
          attested ? "Đã chốt giá, chưa ghi vào báo giá" : "Đang chờ chủ tiệm duyệt",
        ),
        clockHost,
      ),
      h(
        "div",
        { class: "stack stack--tight" },
        state.lines.map((line) =>
          h(
            "div",
            { class: "spread" },
            h("span", null, serviceName(spec.catalog, line.service_code)),
            h(
              "span",
              { class: "money" },
              money(
                bandVerdict(
                  state.typed[line.service_code],
                  line.band_minimum_vnd,
                  line.band_maximum_vnd,
                ).amount,
              ),
            ),
          ),
        ),
      ),
      h(
        "div",
        { class: "notice", dataState: "warn" },
        h("p", { class: "notice__title" }, "Đừng rời màn hình này"),
        h(
          "p",
          null,
          "Phiếu duyệt niêm phong một mã băm của đúng những con số này, chứ máy chủ không lưu " +
            "chính những con số ấy — nên chúng chỉ còn ở màn hình này. Rời đi là phải gửi lại từ " +
            "đầu. Gửi lại không mất gì: lúc đề nghị chưa có giá nào được ghi.",
        ),
        attested
          ? null
          : h(
              "p",
              null,
              "Máy chủ cũng từ chối để người đề nghị tự duyệt. Người bấm “Duyệt” ở màn hình ",
              h("a", { href: "#/approvals" }, "Duyệt"),
              " phải là người khác.",
            ),
      ),
      gated(apply, spec.writeVerdict),
      h(
        "button",
        {
          type: "button",
          dataVariant: "quiet",
          onClick: () => {
            // Not a "cancel": the envelope stays raised and simply stops matching, because a
            // different amount renders a different digest.
            state.approval = null;
            setResult(
              status,
              "warn",
              "Đã quay lại bước chọn giá. Phiếu duyệt cũ không dùng được cho số mới — đổi số " +
                "là đổi nội dung được niêm phong, nên phải gửi lại.",
            );
            draw();
          },
        },
        "Sửa lại số",
      ),
      techDetails([
        [
          "Mã phiếu duyệt",
          shortId(state.approval.approval_request_id),
          { copy: String(state.approval.approval_request_id) },
        ],
        attested
          ? ["Người chốt giá", "bạn — tên bạn đã được ghi cùng con số", { mono: false }]
          : ["Ai được quyết", enumVi(state.approval.required_role), { mono: false }],
        ["Hết hạn lúc", dateTime(state.approval.expires_at)],
      ]),
      status,
      errorHost,
    );
  }

  /** @param {HTMLButtonElement} button */
  async function propose(button) {
    const readiness = bandReadiness(state.lines, state.typed);
    if (!readiness.ready) {
      setResult(status, "danger", "Còn dòng chưa có giá hợp lệ; chưa gửi đi.");
      return;
    }
    button.disabled = true;
    setResult(status, "warn", "Đang chốt giá…");
    render(errorHost);
    let approval;
    try {
      approval = await request(
        `/internal/v1/stores/${encodeURIComponent(spec.store)}/quotes/${encodeURIComponent(spec.result.quote_id)}/range-prices`,
        { method: "POST", body: body(), idempotencyKey: proposing.key() },
      );
    } catch (error) {
      button.disabled = false;
      setResult(status, error.kind === "REQUIRE_HUMAN" ? "warn" : "danger", proposeFailure(error));
      render(errorHost, errorNotice(error));
      revealError(errorHost);
      return;
    }
    proposing.reset();
    state.approval = approval;
    if (approval.status === "APPROVED") {
      // `DEC-029`: the proposal is this staff member's own attestation, so the price is written
      // straight away. If writing fails, `draw()` has put "Ghi giá vào báo giá" up and the next
      // press retries with the same key.
      draw();
      await applyPrices(null);
      return;
    }
    setResult(
      status,
      "ok",
      `Đã gửi. Chủ tiệm mở màn hình Duyệt và bấm Duyệt cho phiếu ${shortId(approval.approval_request_id)}.`,
    );
    draw();
  }

  /** @param {HTMLButtonElement|null} button null when `propose` writes the price on its own */
  async function applyPrices(button) {
    if (!bandReadiness(state.lines, state.typed).ready) {
      setResult(status, "danger", "Số tiền đang giữ không hợp lệ; chưa gửi đi.");
      return;
    }
    if (button) button.disabled = true;
    setResult(status, "warn", "Đang ghi giá…");
    render(errorHost);
    try {
      const closed = await request(
        `/internal/v1/stores/${encodeURIComponent(spec.store)}/quotes/${encodeURIComponent(spec.result.quote_id)}/range-prices/${encodeURIComponent(String(state.approval.approval_request_id))}`,
        { method: "POST", body: body(), idempotencyKey: applying.key() },
      );
      applying.reset();
      clearInterval(timer);
      setResult(status, "ok", `Đã ghi giá vào bản sửa đổi ${closed.revision}.`);
      if (spec.onClosed) await spec.onClosed(closed);
    } catch (error) {
      if (button) button.disabled = false;
      setResult(status, error.kind === "REQUIRE_HUMAN" ? "warn" : "danger", applyFailure(error));
      render(errorHost, errorNotice(error));
      revealError(errorHost);
    }
  }

  if (spec.detail) adopt(spec.detail);
  else void load();
  return root;
}

/**
 * The sentence above a failed proposal. Every branch says the load-bearing thing — nothing was
 * written — because the mistake this step invites is pressing again in case it "half worked".
 *
 * @param {any} error
 * @returns {string}
 */
function proposeFailure(error) {
  if (error.kind === "TIMEOUT" || error.kind === "NETWORK") {
    return (
      "Chưa biết lệnh có tới máy chủ hay không. Bấm “Chốt giá này” lại với đúng số cũ: máy chủ " +
      "nhận ra lệnh cũ và không tạo thêm phiếu. Chưa có giá nào được ghi."
    );
  }
  if (error.kind === "REQUIRE_HUMAN") {
    return "Máy chủ từ chối số này. Không có phiếu duyệt nào được tạo và không có giá nào được ghi.";
  }
  if (error.kind === "STALE" || error.kind === "CONFLICT") {
    return (
      "Bản báo giá đã đổi từ lúc bạn mở màn hình, nên đề nghị này không còn gắn đúng bản nữa. " +
      "Tải lại danh sách báo giá và làm lại trên bản mới. Chưa có gì được ghi."
    );
  }
  return "Không gửi được đề nghị. Chưa có giá nào được ghi.";
}

/**
 * The sentence above a failed application. The server answers "not approved yet", "approved for
 * different amounts" and "expired" with one deliberately opaque message, so all three are named.
 *
 * @param {any} error
 * @returns {string}
 */
function applyFailure(error) {
  if (error.kind === "TIMEOUT" || error.kind === "NETWORK") {
    return (
      "Chưa biết lệnh có tới máy chủ hay không, nên chưa biết giá đã được ghi hay chưa. Tải lại " +
      "danh sách báo giá và xem bản mới nhất trước khi bấm lại."
    );
  }
  return (
    "Chưa ghi được giá. Thường là một trong ba: phiếu chưa được xác nhận, phiếu niêm phong " +
    "những con số khác với số đang ở đây, hoặc phiếu đã quá hạn. Máy chủ trả lời chung một câu " +
    "cho cả ba. Bấm “Sửa lại số” rồi chốt lại từ đầu."
  );
}

/**
 * Remaining time on the raised envelope, rebuilt whole each tick so the warn → danger flip is
 * carried by `data-state` as well as by the words.
 *
 * @param {string|null|undefined} expiresAt
 * @returns {HTMLElement}
 */
function expiryBadge(expiresAt) {
  const left = countdown(expiresAt);
  return badge({
    token: left.text,
    gloss: left.expired
      ? "đã hết hạn — gửi lại từ đầu, không xin gia hạn được"
      : "thời gian còn lại để ghi giá này vào báo giá",
    state: left.expired ? "danger" : "warn",
  });
}
