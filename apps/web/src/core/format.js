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

/**
 * Loose UUID shape.
 *
 * Matched in the browser only to catch a mistyped or mis-pasted identifier before a round trip —
 * the server remains the authority on whether the identifier exists. Loose on case because a pasted
 * identifier is often uppercase; the server parses either.
 */
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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

const DATE_ONLY = new Intl.DateTimeFormat("vi-VN", {
  timeZone: TIMEZONE,
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
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
 * Read an amount of đồng the way a person writes one, including the way this console prints one.
 *
 * `Number.parseInt("132.000", 10)` is 132, and 132 is a safe integer, so a settlement typed as the
 * exact total the screen had just rendered -- `132.000 ₫`, copied -- posted as one hundred and
 * thirty-two đồng. The server refuses it, because a settlement must equal the quoted total to the
 * đồng, so no money moved; what the operator got was a refusal they had no way to explain, for
 * having copied the number the application showed them. The same parse sits behind the negotiated
 * delivery fee, where a fractional entry was silently truncated instead of refused.
 *
 * So: spacing and `₫` are decoration and are removed; `.` is accepted only where it is doing the
 * job of a grouping mark, meaning exactly three digits follow it. A `,` is rejected rather than
 * stripped -- in Vietnamese it is the decimal separator, and đồng have no minor unit here, so
 * "132,5" is a mistake to report and not a number to round. `170000.5` is the same mistake wearing
 * the other separator, and stripping the dot from it produced 1.700.005: ten times the amount, with
 * no sign that anything had been reinterpreted. Both are now refused, which is also what the
 * settlement field's own hint has always promised.
 *
 * @param {string} value what the operator typed
 * @returns {number|null} the amount, or null when it is not one
 */
export function parseDong(value) {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return null;
  if (trimmed.includes(",")) return null;
  const bare = trimmed.replace(/[₫\s\u00a0]/g, "");
  // A dot is a *grouping* mark here, and grouping has a shape: three digits after every dot. That
  // shape is what separates "170.000" from "170000.5". Stripping every dot unconditionally read
  // the second as 1.700.005 -- ten times the amount, silently -- while the field's own hint says
  // decimals are not accepted. A typed decimal is now refused and says so, rather than becoming a
  // different number that the server then rejects for a reason the operator cannot see.
  if (!/^\d{1,3}(\.\d{3})*$/.test(bare) && !/^\d+$/.test(bare)) return null;
  const digits = bare.replace(/\./g, "");
  const amount = Number.parseInt(digits, 10);
  return Number.isSafeInteger(amount) ? amount : null;
}

/**
 * `QUANTITY_PATTERN` in `packages/domain/.../pricing.py`, mirrored byte for byte. The domain is the
 * authority and still checks; this copy exists so the counter hears about a bad weight while typing
 * rather than as `MISSING_REQUIRED_FACT` after pressing.
 */
const QUANTITY_PATTERN = /^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$/;

/**
 * Units the domain prices by whole count (`COUNT_UNITS` in `pricing.py`): a fraction is refused.
 */
const COUNT_UNITS = new Set(["ITEM", "PAIR", "SET", "ANIMAL_PLUSH_ITEM", "CASE"]);

/**
 * Read a typed weight or count into the exact string the pricing engine accepts, or refuse it.
 *
 * A Vietnamese counter writes five and a half kilos as "5,5". The engine only reads "5.5", and it
 * answered "5,5" with `MISSING_REQUIRED_FACT` -- while the 6 kg notice on the same line had already
 * read it as 5.5 and warned about the cliff, so the screen treated one keystroke as two different
 * weights. So there is one reader, used by both:
 *
 *   - Surrounding spaces are dropped (a phone keyboard adds them after autocomplete).
 *   - A single "," with no "." is read as the decimal mark and becomes "." -- but only when the
 *     result is a quantity the domain's own pattern accepts. Nothing else is rewritten: digits are
 *     never added, removed or rounded, so the number sent is the number typed.
 *   - Everything the domain's `_quantity` refuses is refused here too, with no guess: not the
 *     pattern, zero, more than three decimals, more than nine whole digits, or a fraction on a unit
 *     priced by count. "5.500,5", "1,2,3" and "5 kg" are mistakes to report, not numbers to fix.
 *
 * This is the opposite choice from `parseDong`, deliberately: a comma in money is a decimal the
 * đồng does not have, so it is refused; a comma in a weight is the ordinary way to write one.
 *
 * @param {string} value what the operator typed
 * @param {string} [unit] the line's unit, when known; decides whether a fraction is allowed
 * @returns {string|null} the string to send, or null when it is not a quantity
 */
export function parseQuantity(value, unit = "") {
  const trimmed = String(value ?? "").trim();
  if (!trimmed) return null;
  const commas = trimmed.split(",").length - 1;
  const candidate = commas === 1 && !trimmed.includes(".") ? trimmed.replace(",", ".") : trimmed;
  if (!QUANTITY_PATTERN.test(candidate)) return null;
  const [whole, fraction = ""] = candidate.split(".");
  if (whole.length > 9 || fraction.length > 3) return null;
  if (!/[1-9]/.test(candidate)) return null;
  if (fraction && /[1-9]/.test(fraction) && COUNT_UNITS.has(unit)) return null;
  return candidate;
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
 * @param {string|null|undefined} value
 * @returns {string}
 */
export function dateOnly(value) {
  const parsed = parseInstant(value);
  return parsed ? DATE_ONLY.format(parsed) : UNKNOWN;
}

/**
 * Time left before an approval envelope expires.
 *
 * Most approval TTLs are short — ten to thirty minutes (`SECURITY_RELIABILITY_SPEC_V1.md:354`) —
 * and an expiry never extends implicitly, so an approver needs the remaining time rather than the
 * wall clock. Past expiry this returns a negative `seconds`, which the caller renders as expired
 * rather than as a small positive number.
 *
 * One action is long: since the DEC-031 addendum an owner-only remedy envelope stays open until the
 * end of the next business day, up to 48 hours. "còn 1800 phút" is a number an owner has to divide
 * before it means anything, so from an hour up the text is hours and minutes, and from a day up it
 * is days and hours — the same units `duration` uses for the SLA board.
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
  const hours = Math.floor(minutes / 60);
  if (hours >= 24) {
    const days = Math.floor(hours / 24);
    const restHours = hours % 24;
    return {
      seconds,
      expired: false,
      text: restHours > 0 ? `còn ${days} ngày ${restHours} giờ` : `còn ${days} ngày`,
    };
  }
  if (hours >= 1) {
    const restMinutes = minutes % 60;
    return {
      seconds,
      expired: false,
      text: restMinutes > 0 ? `còn ${hours} giờ ${restMinutes} phút` : `còn ${hours} giờ`,
    };
  }
  // Whole Vietnamese units, never a bare "s". Seconds are noise once the wait reaches ten
  // minutes, so they drop out there; below one minute there is no minute part to show.
  const text =
    minutes >= 10
      ? `còn ${minutes} phút`
      : minutes > 0
        ? remainder > 0
          ? `còn ${minutes} phút ${remainder} giây`
          : `còn ${minutes} phút`
        : `còn ${remainder} giây`;
  return { seconds, expired: false, text };
}

/**
 * A duration the server measured, in microseconds, as whole Vietnamese units.
 *
 * The SLA board's "how long is left" and "how far past" are produced by `evaluate_production_sla`
 * and arrive as integers. This converts units for display and decides nothing: the magnitude, the
 * clock it was measured against and which of the two fields is non-zero were all settled by the
 * domain engine before the browser saw them. Dividing here is the same act as `countdown` turning
 * a server timestamp into "còn 5 phút" — it is not the console forming an opinion about risk.
 *
 * Three rules, each because the alternative misreads:
 *
 *   - A non-integer is `—`, never `0`. "No mark was set for this order" and "no time is left" are
 *     opposite facts and a shift would act on them differently.
 *   - Below a minute reads "dưới 1 phút", not "0 phút", while the magnitude is genuinely positive.
 *   - Days appear once the figure passes 48 hours, because "73 giờ" is a number a reader has to do
 *     arithmetic on before it means anything.
 *
 * @param {unknown} microseconds a non-negative integer, as the server sent it
 * @returns {string}
 */
export function duration(microseconds) {
  if (!Number.isInteger(microseconds) || microseconds < 0) return UNKNOWN;
  const totalMinutes = Math.floor(microseconds / 60_000_000);
  if (totalMinutes === 0) return microseconds === 0 ? "0 phút" : "dưới 1 phút";
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours >= 48) {
    const days = Math.floor(hours / 24);
    const restHours = hours % 24;
    return restHours > 0 ? `${days} ngày ${restHours} giờ` : `${days} ngày`;
  }
  if (hours === 0) return `${minutes} phút`;
  return minutes > 0 ? `${hours} giờ ${minutes} phút` : `${hours} giờ`;
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
 * An integer the server decided, rendered only when it actually is one.
 *
 * `null` is not `0` — `IMPLEMENTATION_ROADMAP_V1.md:906` — and this is the guard for fields where
 * the difference matters: a zeroth attempt and an unrecorded attempt count are different facts. A
 * non-integer is a contract violation, so it reads as unknown rather than being coerced.
 *
 * @param {unknown} value
 * @returns {string}
 */
export function integer(value) {
  return Number.isInteger(value) ? String(value) : UNKNOWN;
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

/**
 * Fold a string for Vietnamese-insensitive comparison: case, diacritics and đ/Đ.
 *
 * Four screens had grown the same predicate independently —
 * `String(value).toLowerCase().includes(needle)` in `orders`, `incidents`, `quotes` and
 * `orderRequests` — and all four carried the same defect: typing `nguyen` did not match `Nguyễn`,
 * and typing `don` did not match `đơn`. A Vietnamese operator types without tone marks because it
 * is faster, so the filter missed the row that was on screen.
 *
 * Two steps, and the second is the one that gets forgotten. NFD splits a base letter from its
 * combining marks, so stripping `U+0300–U+036F` removes the tones and the circumflex/breve/horn.
 * But `đ` is **not** a decomposable form — it is its own codepoint with no combining mark — so NFD
 * leaves it untouched and it has to be mapped explicitly. A folder that only does NFD looks right
 * on `Nguyễn` and silently fails on `đơn hàng`, which is the more common word here.
 *
 * @param {unknown} value
 * @returns {string}
 */
export function fold(value) {
  return String(value ?? "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase();
}

/**
 * Whether any of `values` contains `needle`, compared with Vietnamese folding on both sides.
 *
 * The needle is folded here rather than at the call site so a caller cannot forget: `listView`
 * hands its filter a `.trim().toLowerCase()` string, which is folded for ASCII and not for
 * Vietnamese, and that half-normalisation is exactly what made the old predicates look correct.
 *
 * @param {unknown[]} values
 * @param {string} needle
 * @returns {boolean}
 */
export function matchesFilter(values, needle) {
  const wanted = fold(needle);
  if (!wanted) return true;
  return values.some((value) => value != null && fold(value).includes(wanted));
}
