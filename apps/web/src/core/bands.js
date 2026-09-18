/**
 * The bound a staff member is choosing inside, checked here before the number is sent.
 *
 * This is the one place in the console that *compares* two amounts, and it needs saying why that
 * is not the thing `core/format.js` refuses to do. Formatting refuses arithmetic because adding
 * two server-decided amounts would make the browser a second opinion about what a customer owes.
 * Nothing is decided here: the interval comes from the server, per line, on the revision the
 * customer was actually shown (`band_minimum_vnd` / `band_maximum_vnd` on
 * `GET /internal/v1/stores/{store}/quotes/{quote}`), and the server re-checks the amount against
 * that same stored interval and refuses `RANGE_PRICE_OUT_OF_BAND` regardless of what this says.
 * `ENGINEERING_SPEC_V1.md:530` forbids duplicating a business rule in the UI; reading a bound the
 * server just published is not duplicating it, and `CORE_OPERATIONS_COMPLETION_SPEC_V1.md` §3.5
 * asks for exactly this — "one money input validated against it client-side *and* server-side".
 *
 * What it buys is the `pricingCliffNotice` property: the operator is warned *before* pressing,
 * with the band named, instead of typing a number, holding a customer's attention through a round
 * trip, and reading a refusal. The refusal still renders correctly if one gets through, because
 * this check can be wrong — a republished pricebook cannot move a stored band, but a stale screen
 * can hold an old one, and when the two disagree the server is right.
 *
 * Two rules have teeth here:
 *
 *   - **Both ends are inside.** `resolve_range_prices` accepts `minimum` and `maximum` themselves,
 *     so 80.000 ₫ and 240.000 ₫ are prices and 79.999 ₫ and 240.001 ₫ are not. A strict comparison
 *     here would warn about a number the server would have taken, which teaches staff to distrust
 *     the warning.
 *   - **Nothing is ever defaulted.** An empty box is `EMPTY`, not the minimum, not the maximum and
 *     not the midpoint. The whole item exists to keep a named person responsible for the number,
 *     and a helpful default is how that responsibility quietly moves to the software.
 *
 * @module core/bands
 */

import { parseDong } from "./format.js";

/**
 * What the typed text is, relative to the line's published band.
 *
 * `UNBOUNDED` is the honest answer when the server did not send an interval for this line. It is
 * not "fine" and not "out of band": the console does not know, so it must not colour the field
 * either way, and the submit path refuses rather than guessing.
 */
export const BAND = {
  EMPTY: "EMPTY",
  NOT_AN_AMOUNT: "NOT_AN_AMOUNT",
  UNBOUNDED: "UNBOUNDED",
  BELOW: "BELOW",
  ABOVE: "ABOVE",
  INSIDE: "INSIDE",
};

/**
 * Read one typed amount against one published band.
 *
 * `parseDong` does the reading, which is why `150.000` and `150000` are the same number and
 * `150,000` and `170000.5` are neither — both are mistakes to report rather than numbers to
 * repair, and a price is the last field in this application where a silent reinterpretation is
 * acceptable.
 *
 * @param {string|null|undefined} typed what the staff member has in the box
 * @param {number|null|undefined} minimum the line's published floor, inclusive
 * @param {number|null|undefined} maximum the line's published ceiling, inclusive
 * @returns {{state: string, amount: number|null}} `amount` is null unless the text parsed
 */
export function bandVerdict(typed, minimum, maximum) {
  const text = String(typed ?? "").trim();
  if (!text) return { state: BAND.EMPTY, amount: null };

  const amount = parseDong(text);
  if (amount === null) return { state: BAND.NOT_AN_AMOUNT, amount: null };

  if (!Number.isInteger(minimum) || !Number.isInteger(maximum)) {
    return { state: BAND.UNBOUNDED, amount };
  }
  if (amount < minimum) return { state: BAND.BELOW, amount };
  if (amount > maximum) return { state: BAND.ABOVE, amount };
  return { state: BAND.INSIDE, amount };
}

/**
 * Whether every open band on a revision has been closed with an amount this console believes.
 *
 * The server refuses a half-priced revision outright — `close_range_prices` answers
 * `RANGE_PRICE_REQUIRES_HUMAN` for the whole revision when one band is still open, because a total
 * that is half decision and half guess is worse than no total. So the console asks the same
 * question before it sends, and names the line that is missing rather than the rule.
 *
 * @param {Array<{service_code: string, band_minimum_vnd: number|null, band_maximum_vnd: number|null}>} lines
 * @param {Record<string, string>} typed keyed by service code, as the operator typed it
 * @returns {{ready: boolean, blocked: Array<{serviceCode: string, state: string}>}}
 */
export function bandReadiness(lines, typed) {
  const blocked = [];
  for (const line of lines) {
    const verdict = bandVerdict(
      typed[line.service_code],
      line.band_minimum_vnd,
      line.band_maximum_vnd,
    );
    if (verdict.state !== BAND.INSIDE) {
      blocked.push({ serviceCode: line.service_code, state: verdict.state });
    }
  }
  return { ready: blocked.length === 0 && lines.length > 0, blocked };
}
