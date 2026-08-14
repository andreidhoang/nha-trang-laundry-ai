/**
 * Formatting. Not arithmetic.
 *
 * Nothing in this module adds, multiplies, rounds or compares money. Every amount arrives as an
 * integer of VND that the deterministic engine already decided, and the only transformation
 * applied is grouping separators. That is a deliberate boundary: `ENGINEERING_SPEC_V1.md:530`
 * forbids duplicating a business rule in the UI, and the cheapest way to accidentally duplicate one
 * is a helper that "just" totals a couple of line amounts.
 *
 * Two rules here have teeth:
 *
 *   - `null` is never rendered as `0`. `IMPLEMENTATION_ROADMAP_V1.md:906` names this specifically,
 *     and it is currently the common case: every quote this API can produce has an unresolved
 *     delivery fee, so `display_total_min_vnd` is genuinely null rather than zero.
 *   - A non-integer amount is a contract violation, not a rounding opportunity. It is shown raw
 *     with a visible marker so the operator distrusts it, instead of being quietly coerced.
 *
 * @module core/format
 */

/** The business timezone, pinned by `specs/contracts/canonical-enums-v1.json`. */
export const TIMEZONE = "Asia/Ho_Chi_Minh";

const VND = new Intl.NumberFormat("vi-VN", {
  style: "currency",
  currency: "VND",
  maximumFractionDigits: 0,
});

const DATE_TIME = new Intl.DateTimeFormat("vi-VN", {
  timeZone: TIMEZONE,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const TIME_ONLY = new Intl.DateTimeFormat("vi-VN", {
  timeZone: TIMEZONE,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

/** What to show where a number would be if the server has not decided one. */
export const UNKNOWN = "—";

/**
 * Format an integer amount of VND.
 *
 * @param {number|null|undefined} amount
 * @param {string} [unknownLabel] shown when the server sent null; never `0`
 * @returns {string}
 */
export function money(amount, unknownLabel = UNKNOWN) {
  if (amount === null || amount === undefined) return unknownLabel;
  if (!Number.isInteger(amount)) return `${String(amount)} ⚠ không phải số nguyên VND`;
  return VND.format(amount);
}

/**
 * Format a min/max pair the way the price rules require it to be read.
 *
 * A range must be shown whole — `ENGINEERING_SPEC_V1.md:233` says the UI shows only the entire
 * range — so a single number is emitted only when the server itself collapsed the two bounds.
 *
 * @param {number|null|undefined} min
 * @param {number|null|undefined} max
 * @param {string} [unknownLabel]
 * @returns {{text: string, isRange: boolean, isKnown: boolean}}
 */
export function moneyRange(min, max, unknownLabel = UNKNOWN) {
  if (min === null || min === undefined || max === null || max === undefined) {
    return { text: unknownLabel, isRange: false, isKnown: false };
  }
  if (min === max) return { text: money(min), isRange: false, isKnown: true };
  return { text: `${money(min)} – ${money(max)}`, isRange: true, isKnown: true };
}

/**
 * Parse a server timestamp.
 *
 * This API emits two spellings — Pydantic's `…Z` for `datetime` fields and `.isoformat()`'s
 * `…+00:00` for the one field typed as `str` — and the offset itself depends on the PostgreSQL
 * session timezone, which is not pinned anywhere in the repository. So the string is handed to
 * `Date`, which understands every form, and never inspected character by character.
 *
 * @param {string|null|undefined} value
 * @returns {Date|null}
 */
export function parseInstant(value) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

/**
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function dateTime(value) {
  const parsed = parseInstant(value);
  return parsed ? DATE_TIME.format(parsed) : UNKNOWN;
}

/**
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function timeOnly(value) {
  const parsed = parseInstant(value);
  return parsed ? TIME_ONLY.format(parsed) : UNKNOWN;
}

/**
 * Time left before an approval envelope expires.
 *
 * Approval TTLs are short — ten to thirty minutes (`SECURITY_RELIABILITY_SPEC_V1.md:354`) — and an
 * expiry never extends implicitly, so an approver needs the remaining time rather than the wall
 * clock. Past expiry this returns a negative `seconds`, which the caller renders as expired rather
 * than as a small positive number.
 *
 * @param {string|null|undefined} expiresAt
 * @param {Date} [now]
 * @returns {{seconds: number, text: string, expired: boolean}}
 */
export function countdown(expiresAt, now = new Date()) {
  const parsed = parseInstant(expiresAt);
  if (!parsed) return { seconds: 0, text: UNKNOWN, expired: false };
  const seconds = Math.round((parsed.getTime() - now.getTime()) / 1000);
  if (seconds <= 0) return { seconds, text: "đã hết hạn", expired: true };
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return {
    seconds,
    expired: false,
    text: minutes > 0 ? `còn ${minutes} phút ${remainder}s` : `còn ${remainder}s`,
  };
}

/**
 * A quantity as the operator typed it and the server stored it.
 *
 * Quantities travel as base-10 strings precisely so that nobody's float turns `6` into `5.999…`,
 * and `DEC-001` — how many decimal places a weight may carry — is still open. Re-formatting one
 * here would be this application deciding an open business question, so it does not.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function quantity(value) {
  return value === null || value === undefined || value === "" ? UNKNOWN : String(value);
}

/**
 * Shorten an identifier for a list, keeping both ends so two are still distinguishable.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function shortId(value) {
  if (!value) return UNKNOWN;
  const text = String(value);
  return text.length <= 13 ? text : `${text.slice(0, 8)}…${text.slice(-4)}`;
}

/**
 * Shorten a content hash, keeping the algorithm prefix because it is load-bearing: this API uses
 * `JCS-SHA256-V1:` on approval and quote hashes and a bare `sha256:` on incident hashes, and
 * pasting one where the other belongs is a 422 the operator would otherwise not understand.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function shortHash(value) {
  if (!value) return UNKNOWN;
  const text = String(value);
  const separator = text.lastIndexOf(":");
  if (separator < 0) return shortId(text);
  const prefix = text.slice(0, separator + 1);
  const digest = text.slice(separator + 1);
  return digest.length <= 12 ? text : `${prefix}${digest.slice(0, 8)}…${digest.slice(-4)}`;
}

/**
 * Render a count that may be a floor rather than a total.
 *
 * @param {unknown[]} items
 * @param {number} limit
 * @returns {string}
 */
export function count(items, limit) {
  const size = Array.isArray(items) ? items.length : 0;
  return size >= limit ? `${size}+` : String(size);
}
