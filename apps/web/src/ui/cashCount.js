/**
 * Đếm két in words, and the owner's days on Báo cáo (`CASH-COUNT-009`, `DEC-049`).
 *
 * Shared by `screens/cashCount.js` (today's sheet) and `screens/reports.js` (every day of the
 * window, for the owner). Every figure here is the server's: what the drawer should hold, the
 * count, and the gap as a size plus a word (`EVEN`, `OVER` = thừa, `SHORT` = thiếu). This module
 * picks the word and formats the amount; it never subtracts, so no minus sign can reach a screen.
 *
 * @module ui/cashCount
 */

import { request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { calendarDay, clock, money } from "../core/format.js";
import { can } from "../core/rbac.js";
import { snapshot } from "../core/session.js";
import { errorNotice } from "./components.js";
import { inlineAlert, list, listRow, section, skeletonRows, statusPill } from "./kit.js";

/**
 * The recorded gap of a closing count, as the counter says it: "Thiếu 10.000 ₫".
 *
 * @param {any} entry a `CashCountEntryResponse` of kind CLOSING_COUNT
 * @returns {{text: string, state: "ok"|"warn"|"neutral", token: string}}
 */
export function differenceWords(entry) {
  const direction = entry?.difference_direction;
  if (direction === "SHORT") {
    return { text: `Thiếu ${money(entry.difference_vnd)}`, state: "warn", token: "SHORT" };
  }
  if (direction === "OVER") {
    return { text: `Thừa ${money(entry.difference_vnd)}`, state: "warn", token: "OVER" };
  }
  if (direction === "EVEN") return { text: "Khớp", state: "ok", token: "EVEN" };
  return { text: "Chưa so được", state: "neutral", token: String(entry?.expected_status || "") };
}

/**
 * Why there is no expected figure, or what it leaves out, in one line (tier 1). `null` when the
 * figure is complete.
 *
 * @param {any} expected a `CashExpectedResponse`
 * @returns {string|null}
 */
export function expectedNote(expected) {
  if (!expected) return null;
  if (expected.status === "FLOAT_MISSING") {
    return "Chưa ghi tiền đầu ngày nên chưa tính được két phải có.";
  }
  if (expected.status === "BOOKS_BELOW_ZERO") {
    return (
      `Sổ ghi tiền ra khỏi két nhiều hơn tiền vào ${money(expected.books_over_vnd)} — chưa tính ` +
      "được két phải có. Báo chủ tiệm kiểm tra Sổ thu chi."
    );
  }
  if (expected.status === "INCOMPLETE") {
    return (
      `Chưa tính ${expected.excluded_unknown_refunds_count} lần hoàn chưa rõ cách hoàn ` +
      `(${money(expected.excluded_unknown_refunds_vnd)}) — số phải có chưa đầy đủ.`
    );
  }
  return null;
}

/**
 * What the recorded gap is measured against, as the words that follow its pill: "so với két phải
 * có 600.000 ₫." -- or why there was nothing to compare with. The pill (`differenceWords`) carries
 * the gap itself, so the figure is said once.
 *
 * @param {any} entry
 * @returns {string}
 */
export function comparisonSentence(entry) {
  if (entry.expected_status === "FLOAT_MISSING") return "lúc đếm chưa ghi tiền đầu ngày.";
  if (entry.expected_status === "BOOKS_BELOW_ZERO") {
    return "sổ ghi tiền ra nhiều hơn tiền vào két.";
  }
  const against = `két phải có ${money(entry.expected_vnd)}.`;
  return entry.difference_direction === "EVEN" ? `với ${against}` : `so với ${against}`;
}

/**
 * Báo cáo's "Đếm két" section: one row per day of the window that has a count, for the owner.
 * Anyone else who reads the report sees the section shut, with who may open it -- never hidden.
 *
 * @returns {{node: HTMLElement, load: (store: string, span: {from: string, to: string}) => Promise<void>}}
 */
export function cashCountReport() {
  const host = h("div", { class: "stack stack--tight", dataCashCountReport: "true" });
  const node = section({ title: "Đếm két", card: false, children: host });
  /** The latest window asked for: an older read landing late never paints over a newer one. */
  let generation = 0;

  /**
   * @param {string} store
   * @param {{from: string, to: string}} span
   */
  async function load(store, span) {
    const mine = ++generation;
    const verdict = can(snapshot().principal, "CASH_COUNT_HISTORY");
    if (!verdict.allowed) {
      render(host, h("p", { class: "hint", dataRefused: "true" }, verdict.reason));
      return;
    }
    render(host, skeletonRows(2));
    const query = new URLSearchParams({ from: span.from, to: span.to }).toString();
    try {
      const found = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/cash-counts?${query}`,
      );
      if (mine !== generation) return;
      const days = Array.isArray(found?.days) ? [...found.days].reverse() : [];
      render(
        host,
        days.length
          ? list(days.map(dayRow), { label: "Đếm két từng ngày" })
          : h("p", { class: "hint" }, "Chưa có ngày nào đếm két trong khoảng này."),
        found?.truncated
          ? inlineAlert({
              state: "warn",
              title: "Quá nhiều lần ghi để hiện hết",
              body: "Chọn khoảng ngày ngắn hơn để xem đủ.",
            })
          : null,
      );
    } catch (error) {
      if (mine !== generation) return;
      render(host, errorNotice(error, { onRetry: () => void load(store, span) }));
    }
  }

  return { node, load };
}

/**
 * One day of counts: the closing count's recorded gap, or what is missing.
 *
 * @param {any} day a `CashCountDayResponse`
 * @returns {HTMLElement}
 */
function dayRow(day) {
  const closing = day.closing_count;
  const opening = day.opening_float;
  const corrections = (day.entries || []).filter((entry) => entry.supersedes_id).length;
  const facts = [
    opening ? `Đầu ngày ${money(opening.counted_vnd)}` : "Chưa ghi tiền đầu ngày",
    closing
      ? closing.expected_vnd === null || closing.expected_vnd === undefined
        ? `Đếm được ${money(closing.counted_vnd)}`
        : `Đếm được ${money(closing.counted_vnd)} · phải có ${money(closing.expected_vnd)}`
      : "Chưa đếm cuối ngày",
    corrections ? `sửa ${corrections} lần` : null,
    day.changed_since_count ? "sổ đổi sau lúc đếm" : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const words = closing ? differenceWords(closing) : null;
  return listRow({
    title: calendarDay(day.business_day),
    meta: [
      h("span", null, facts),
      closing ? h("span", { class: "hint" }, `Ghi lúc ${clock(closing.recorded_at)}`) : null,
    ],
    trailing: words
      ? statusPill({ state: words.state, text: words.text, token: words.token })
      : null,
    data: { cashCountDay: String(day.business_day) },
  });
}
