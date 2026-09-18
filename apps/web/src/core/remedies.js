/**
 * What a remedy proposal would be, read off the server's own answer before anything is sent.
 *
 * `core/bands.js` set the precedent this follows and its reasoning carries over unchanged: this
 * module *compares*, it does not compute. Every figure below arrives from
 * `GET …/incidents/{incident}/remedy-options`, which the repository builds by asking
 * `domain.remedies` itself — the 5× damage cap per priced line, the 10% late-delivery credit, the
 * 100.000 ₫ staff ceiling, and the two windows measured from the recorded handover. Nothing here
 * multiplies, adds or rounds anything, and the server re-decides all of it on the proposal.
 *
 * What it buys is the thing `TASK-remedy-001` calls the point of the whole screen: **before staff
 * type an amount** they already see the kind, the server-computed ceiling, the window and whether
 * it is still open, and whether this proposal will need the owner. A staff member must never
 * discover that the owner is required after filling the form in, with a customer waiting.
 *
 * Three rules have teeth:
 *
 *   - **`LOST_ITEM` never reaches a figure.** It is answered first and alone, exactly as
 *     `evaluate_remedy` answers it first and alone, because every other branch reads a published
 *     number and `DEC-004` carries loss forward as undecided. There is deliberately no ceiling,
 *     no window and no owner threshold on a loss plan, so a caller cannot read one out of it.
 *   - **A missing figure is never a zero and never a default.** No published policy is
 *     `POLICY_UNPUBLISHED`, no recorded handover is `WINDOW_EVIDENCE_MISSING`, and a null
 *     late-delivery credit is `CREDIT_UNAVAILABLE`. None of the three is "0 ₫", and none is the
 *     minimum, the midpoint or the maximum of anything.
 *   - **A ceiling refuses; it never truncates.** An amount above the line's computed cap is
 *     `ABOVE_CEILING` with the cap named, which is what the server does with it
 *     (`REMEDY_CEILING_EXCEEDED`, refused with the ceiling in the body, never capped to it).
 *
 * @module core/remedies
 */

import { parseDong } from "./format.js";

/** The four outcomes an incident can reach. `RemedyKind`, verbatim; there is no fifth. */
export const REMEDY_KIND = {
  FREE_REWASH: "FREE_REWASH",
  DAMAGE_COMPENSATION: "DAMAGE_COMPENSATION",
  LATE_DELIVERY_CREDIT: "LATE_DELIVERY_CREDIT",
  LOST_ITEM: "LOST_ITEM",
};

/** The order they are offered in: the two that need no figure first, loss last. */
export const REMEDY_KIND_ORDER = [
  REMEDY_KIND.FREE_REWASH,
  REMEDY_KIND.DAMAGE_COMPENSATION,
  REMEDY_KIND.LATE_DELIVERY_CREDIT,
  REMEDY_KIND.LOST_ITEM,
];

/**
 * What the console can say about a proposal before it is sent.
 *
 * `READY` is the only state the submit path accepts, and it still means "the server may refuse
 * this" — a stale screen holds a window that closed while it was open, and when the two disagree
 * the server is right.
 */
export const PLAN = {
  /** Invariant 11: no `REMEDY_POLICY` version is published, so every kind fails closed. */
  POLICY_UNPUBLISHED: "POLICY_UNPUBLISHED",
  /** `DEC-004` carries loss forward as undecided. Recorded, never priced. */
  LOSS_UNRESOLVED: "LOSS_UNRESOLVED",
  /** Nothing recorded says when this customer got their laundry back. */
  WINDOW_EVIDENCE_MISSING: "WINDOW_EVIDENCE_MISSING",
  /** The published window measured from that handover has closed. */
  WINDOW_CLOSED: "WINDOW_CLOSED",
  /** No delivery this system recorded could have been late, or no total was ever settled. */
  CREDIT_UNAVAILABLE: "CREDIT_UNAVAILABLE",
  /** Damage names a priced line and none is chosen yet. */
  LINE_NOT_CHOSEN: "LINE_NOT_CHOSEN",
  /** The one kind a person chooses a figure for, with no figure yet. */
  AMOUNT_MISSING: "AMOUNT_MISSING",
  /** Typed, but not an integer number of đồng. */
  NOT_AN_AMOUNT: "NOT_AN_AMOUNT",
  /** Above the cap the server computed from the shop's own stored line amount. */
  ABOVE_CEILING: "ABOVE_CEILING",
  /** The lateness attested does not reach the published threshold. */
  BELOW_LATENESS_THRESHOLD: "BELOW_LATENESS_THRESHOLD",
  /** Nobody has said the store was at fault, and every remedy in `DEC-004` rests on that. */
  FAULT_NOT_ATTESTED: "FAULT_NOT_ATTESTED",
  /** Everything the console can check is in order. */
  READY: "READY",
};

/**
 * The window a kind is measured in, as `RemedyOptions` reports it.
 *
 * `LATE_DELIVERY_CREDIT` has no elapsed-time window at all — `DEC-004` gates it on the lateness and
 * the fault, not on how long ago it happened — so it gets `null` here rather than a borrowed one.
 *
 * @param {string} kind
 * @param {any} options a `RemedyOptionsResponse`
 * @returns {{closesAt: string|null, open: boolean}|null}
 */
export function remedyWindow(kind, options) {
  if (kind === REMEDY_KIND.FREE_REWASH) {
    return {
      closesAt: options?.rewash_window_closes_at ?? null,
      open: Boolean(options?.rewash_window_open),
    };
  }
  if (kind === REMEDY_KIND.DAMAGE_COMPENSATION) {
    return {
      closesAt: options?.defect_window_closes_at ?? null,
      open: Boolean(options?.defect_window_open),
    };
  }
  return null;
}

/**
 * The priced lines a damage proposal may name, with the cap the server computed for each.
 *
 * Returned as a sorted array rather than the map the wire carries, so the picker's order is stable
 * between renders and between staff members reading the same screen.
 *
 * @param {any} options
 * @returns {Array<{lineId: string, ceiling: number|null}>}
 */
export function damageLines(options) {
  const table = options?.damage_line_ceilings_vnd;
  if (!table || typeof table !== "object") return [];
  return Object.keys(table)
    .sort()
    .map((lineId) => {
      const cap = table[lineId];
      return { lineId, ceiling: Number.isInteger(cap) ? cap : null };
    });
}

/**
 * Read one draft proposal against the figures the server already published for this incident.
 *
 * @param {object} draft
 * @param {any} draft.options the `RemedyOptionsResponse` for this incident
 * @param {string} draft.kind
 * @param {boolean} [draft.storeFaultAttested]
 * @param {string} [draft.lineId] the priced line a damage proposal names
 * @param {string} [draft.typedAmount] what is in the money box, exactly as typed
 * @param {string} [draft.typedLateness] what is in the lateness box, exactly as typed
 * @returns {{
 *   state: string,
 *   kind: string,
 *   amountVnd: number|null,
 *   ceilingVnd: number|null,
 *   hasCeiling: boolean,
 *   windowClosesAt: string|null,
 *   windowOpen: boolean|null,
 *   ownerThresholdVnd: number|null,
 *   ownerPossible: boolean,
 *   requiresOwner: boolean|null,
 *   needsAmount: boolean,
 *   needsLine: boolean,
 *   needsLateness: boolean,
 * }}
 */
export function remedyPlan(draft) {
  const options = draft.options || null;
  const kind = draft.kind;

  /** Nothing below may read a figure out of a loss, so it is answered before any figure is read. */
  if (kind === REMEDY_KIND.LOST_ITEM) {
    return {
      state: PLAN.LOSS_UNRESOLVED,
      kind,
      amountVnd: null,
      ceilingVnd: null,
      hasCeiling: false,
      windowClosesAt: null,
      windowOpen: null,
      ownerThresholdVnd: null,
      ownerPossible: false,
      requiresOwner: null,
      needsAmount: false,
      needsLine: false,
      needsLateness: false,
    };
  }

  const needsLine = kind === REMEDY_KIND.DAMAGE_COMPENSATION;
  const needsLateness = kind === REMEDY_KIND.LATE_DELIVERY_CREDIT;
  const threshold = Number.isInteger(options?.staff_approval_ceiling_vnd)
    ? options.staff_approval_ceiling_vnd
    : null;
  const window = remedyWindow(kind, options);

  /** @type {(state: string, extra?: object) => any} */
  const plan = (state, extra = {}) => ({
    state,
    kind,
    amountVnd: null,
    ceilingVnd: null,
    hasCeiling: false,
    windowClosesAt: window ? window.closesAt : null,
    windowOpen: window ? window.open : null,
    ownerThresholdVnd: threshold,
    ownerPossible: false,
    requiresOwner: null,
    // `needsAmount` defaults to false for every kind and every state, and only the damage branch
    // turns it on, once it holds a line whose cap the server computed. The screen renders the
    // money box from this flag, so defaulting it to "damage wants an amount" put the box on screen
    // while the ceiling above it was still `—`: a person could type 400.000 ₫ into a form that had
    // not yet told them the cap was 90.000 ₫, which is the exact ordering `TASK-remedy-001` says
    // is the point of the screen. No ceiling on screen, no box to type into.
    needsAmount: false,
    needsLine,
    needsLateness,
    ...extra,
  });

  if (!options || options.policy_published !== true) return plan(PLAN.POLICY_UNPUBLISHED);

  // A window can only be measured from a recorded handover. `orders.production_released_at` is
  // null for every order released before migration 0042, and the honest reading of that is "the
  // shop has no record of when this customer collected" — not a window starting now.
  if (window !== null && !options.goods_returned_at) return plan(PLAN.WINDOW_EVIDENCE_MISSING);
  if (window !== null && !window.open) return plan(PLAN.WINDOW_CLOSED);

  // From here down the missing facts are reported in the order the form asks for them, and the
  // fault attestation is asked last on purpose. It is not one more required field: it attests to
  // the figures above it, so it cannot honestly be ticked before they are on screen. Checking it
  // first — as this module did until the browser run caught it — also swallowed everything the
  // amount decides, because `FAULT_NOT_ATTESTED` returned before the typed amount was ever read.
  // A staff member could type 150.000 ₫ against a 100.000 ₫ staff ceiling and be told only that
  // nobody had attested fault; the owner warning arrived after they ticked the box, which is
  // precisely the "discovering the owner is required after filling the form in" the packet forbids.

  if (kind === REMEDY_KIND.FREE_REWASH) {
    // No money moves, so there is no ceiling to show and no threshold to cross. Saying "0 ₫" here
    // would be a figure nobody published.
    if (draft.storeFaultAttested !== true) return plan(PLAN.FAULT_NOT_ATTESTED);
    return plan(PLAN.READY, { requiresOwner: false });
  }

  if (kind === REMEDY_KIND.LATE_DELIVERY_CREDIT) {
    const computed = options.late_delivery_credit_vnd;
    if (!Number.isInteger(computed)) return plan(PLAN.CREDIT_UNAVAILABLE);
    const overOwner = threshold !== null && computed > threshold;
    const ready = {
      amountVnd: computed,
      ceilingVnd: computed,
      hasCeiling: true,
      ownerPossible: overOwner,
      requiresOwner: overOwner,
    };
    const minutes = readMinutes(draft.typedLateness);
    if (minutes === null) return plan(PLAN.AMOUNT_MISSING, ready);
    const limit = options.late_delivery_threshold_minutes;
    if (Number.isInteger(limit) && minutes <= limit) {
      return plan(PLAN.BELOW_LATENESS_THRESHOLD, ready);
    }
    if (draft.storeFaultAttested !== true) return plan(PLAN.FAULT_NOT_ATTESTED, ready);
    return plan(PLAN.READY, ready);
  }

  // DAMAGE_COMPENSATION. The cap belongs to one priced line, so there is nothing to show until a
  // line is named: a per-order damage ceiling does not exist and inventing one would be a figure.
  const line = damageLines(options).find((candidate) => candidate.lineId === draft.lineId);
  if (!draft.lineId || !line || line.ceiling === null) return plan(PLAN.LINE_NOT_CHOSEN);
  const cap = line.ceiling;
  const bound = {
    ceilingVnd: cap,
    hasCeiling: true,
    // The cap is known, so the money box may exist. This is the only place it is switched on.
    needsAmount: true,
    // Before a single digit is typed: this line's cap is above what a staff member may approve, so
    // some amounts on it will need the owner. That is the sentence the packet asks for.
    ownerPossible: threshold !== null && cap > threshold,
  };

  const typed = String(draft.typedAmount ?? "").trim();
  if (!typed) return plan(PLAN.AMOUNT_MISSING, bound);
  const amount = parseDong(typed);
  if (amount === null) return plan(PLAN.NOT_AN_AMOUNT, bound);
  // Read once, and carried onto every state below it, including the ones that refuse. What the
  // owner has to decide does not depend on whether the person at the counter has ticked a box yet.
  const read = { ...bound, amountVnd: amount, requiresOwner: threshold !== null && amount > threshold };
  if (amount > cap) return plan(PLAN.ABOVE_CEILING, read);
  if (draft.storeFaultAttested !== true) return plan(PLAN.FAULT_NOT_ATTESTED, read);
  return plan(PLAN.READY, read);
}

/**
 * Read an attested lateness in whole minutes.
 *
 * Deliberately not `parseDong`: minutes are not money, a grouping dot in them is a mistake rather
 * than a thousand, and the server bounds the field at a non-negative integer.
 *
 * @param {string|null|undefined} typed
 * @returns {number|null}
 */
export function readMinutes(typed) {
  const text = String(typed ?? "").trim();
  if (!/^\d{1,7}$/.test(text)) return null;
  const parsed = Number.parseInt(text, 10);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

/**
 * The exact body `RemedyProposalRequest` declares for this kind, and no other key.
 *
 * It is a `StrictRequest`, so a key that does not belong to the kind is a 422 rather than a field
 * quietly ignored — and `_refuse_wrong_shape` refuses a stray one with
 * `REMEDY_AMOUNT_NOT_APPLICABLE` even when it would have been harmless. Each kind owns one shape
 * and this builds exactly that shape.
 *
 * @param {ReturnType<typeof remedyPlan>} plan
 * @param {{lineId?: string, typedLateness?: string}} draft
 * @returns {Record<string, unknown>|null} null when the plan is not one the console may send
 */
export function remedyProposalBody(plan, draft) {
  if (plan.state !== PLAN.READY) return null;
  if (plan.kind === REMEDY_KIND.DAMAGE_COMPENSATION) {
    return {
      kind: plan.kind,
      store_fault_attested: true,
      order_line_id: draft.lineId,
      amount_vnd: plan.amountVnd,
    };
  }
  if (plan.kind === REMEDY_KIND.LATE_DELIVERY_CREDIT) {
    return {
      kind: plan.kind,
      store_fault_attested: true,
      attested_late_by_minutes: readMinutes(draft.typedLateness),
    };
  }
  return { kind: plan.kind, store_fault_attested: true };
}

/**
 * Whether a recorded proposal still needs somebody before it can be carried out.
 *
 * `RemedyStatus`, verbatim. `POLICY_UNRESOLVED` is the loss wall and nothing moves it out.
 *
 * @param {string|null|undefined} status
 * @returns {boolean}
 */
export function awaitsOwner(status) {
  return status === "OWNER_APPROVAL_REQUIRED";
}
