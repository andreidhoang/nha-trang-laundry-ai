/**
 * What a remedy proposal would be, read off the server's own answer before anything is sent.
 *
 * `core/bands.js` set the precedent this follows and its reasoning carries over unchanged: this
 * module *compares*, it does not compute. Every figure below arrives from
 * `GET …/incidents/{incident}/remedy-options`, which the repository builds by asking
 * `domain.remedies` itself — the item fee and 5× cap per priced line, what each line already
 * carries, the 10% late-delivery credit, the 100.000 ₫ staff ceiling, and the two windows measured
 * from the recorded handover. The one piece of arithmetic here is the sum the server itself makes:
 * what the item already carries plus what is typed now, because `DEC-004`'s two limits are about
 * the item and not about one form. Nothing is multiplied or rounded, and the server re-decides all
 * of it on the proposal.
 *
 * What it buys is the thing `TASK-remedy-001` calls the point of the whole screen: **before staff
 * type an amount** they already see the kind, the server-computed ceiling, the window and whether
 * it is still open, and whether this proposal will need the owner. A staff member must never
 * discover that the owner is required after filling the form in, with a customer waiting — which
 * is exactly what the staging review watched happen, because this module compared the typed amount
 * alone while the server compared the item's running total.
 *
 * `DEC-031` (2026-09-25) changed three answers, and each is read here from the server rather than
 * restated:
 *
 *   - **The item fee.** A per-piece line caps each piece at 5× its unit price, a weight-priced line
 *     caps against its bag; the line's `item_fee_basis` says which, and `NOT_RECORDED` means the
 *     owner decides every amount on it (`owner_always`).
 *   - **Loss.** No longer a wall. A loss names an item and an amount like damage, and **always**
 *     needs the owner, whatever the amount. `requiresOwner` is `true` on every loss plan.
 *   - **A refunded order.** Every compensation on it needs the owner; the server lists that in each
 *     line's `owner_always` (`ORDER_REFUNDED`).
 *
 * Two rules keep their teeth:
 *
 *   - **A missing figure is never a zero and never a default.** No published policy is
 *     `POLICY_UNPUBLISHED`, no recorded handover is `WINDOW_EVIDENCE_MISSING`, and a null
 *     late-delivery credit is `CREDIT_UNAVAILABLE`. A server that sends no per-line terms offers no
 *     line at all rather than a ceiling guessed from an older field.
 *   - **A ceiling refuses; it never truncates.** An item total above the line's computed cap is
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

/** The two kinds that pay against one item's ceiling. `_ITEM_KINDS` in the domain. */
const ITEM_KINDS = new Set([REMEDY_KIND.DAMAGE_COMPENSATION, REMEDY_KIND.LOST_ITEM]);

/**
 * Why the owner is needed. `OwnerReason`, verbatim, in the order the server lists them — the
 * console builds the same list so that "what the form predicted" and "what the server answered"
 * can be compared token for token.
 */
export const OWNER_REASON = {
  LOSS_CLAIM: "LOSS_CLAIM",
  ORDER_REFUNDED: "ORDER_REFUNDED",
  ITEM_FEE_NOT_RECORDED: "ITEM_FEE_NOT_RECORDED",
  ABOVE_STAFF_LIMIT: "ABOVE_STAFF_LIMIT",
};

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
  /** Nothing recorded says when this customer got their laundry back. */
  WINDOW_EVIDENCE_MISSING: "WINDOW_EVIDENCE_MISSING",
  /** The published window measured from that handover has closed. */
  WINDOW_CLOSED: "WINDOW_CLOSED",
  /** No delivery this system recorded could have been late, or no total was ever settled. */
  CREDIT_UNAVAILABLE: "CREDIT_UNAVAILABLE",
  /** Damage or loss names a priced line and none is chosen yet. */
  LINE_NOT_CHOSEN: "LINE_NOT_CHOSEN",
  /** A kind a person chooses a figure for, with no figure yet. */
  AMOUNT_MISSING: "AMOUNT_MISSING",
  /** Typed, but not an integer number of đồng. */
  NOT_AN_AMOUNT: "NOT_AN_AMOUNT",
  /** The item's total would pass the cap the server computed from the order's own snapshot. */
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
 * Loss shares the visible-defect window: `DEC-031` gives it the same 24 hours from the handover.
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
  if (ITEM_KINDS.has(kind)) {
    return {
      closesAt: options?.defect_window_closes_at ?? null,
      open: Boolean(options?.defect_window_open),
    };
  }
  return null;
}

/**
 * The priced lines a damage or loss proposal may name, with the terms the server will apply.
 *
 * Read from `damage_lines` only. `damage_line_ceilings_vnd` carries the ceilings alone, without
 * what each item already holds or what sends it to the owner, and a prediction made from it is the
 * one the staging review caught saying "staff can approve" over an owner-only answer — so a
 * response without the per-line terms offers no line, and the form says so.
 *
 * @param {any} options
 * @returns {Array<{
 *   lineId: string, serviceCode: string, serviceName: string|null, label: string,
 *   unit: string|null, quantity: string|null, basis: string|null, itemFee: number|null,
 *   ceiling: number|null, committed: number|null, ownerAlways: string[],
 * }>}
 */
export function damageLines(options) {
  const lines = options?.damage_lines;
  if (!Array.isArray(lines)) return [];
  return lines
    .filter((line) => line && typeof line.line_id === "string")
    .map((line) => {
      const code = typeof line.service_code === "string" ? line.service_code : "";
      const name = typeof line.service_name === "string" && line.service_name ? line.service_name : null;
      return {
        lineId: line.line_id,
        serviceCode: code,
        serviceName: name,
        // The name the pricebook the order was priced under gave it, or its code — never the bare
        // line identifier, which means nothing to the person at the counter.
        label: name || code || line.line_id,
        unit: typeof line.unit === "string" ? line.unit : null,
        quantity: typeof line.quantity === "string" ? line.quantity : null,
        basis: typeof line.item_fee_basis === "string" ? line.item_fee_basis : null,
        itemFee: Number.isInteger(line.item_fee_vnd) ? line.item_fee_vnd : null,
        ceiling: Number.isInteger(line.ceiling_vnd) ? line.ceiling_vnd : null,
        committed: Number.isInteger(line.committed_vnd) ? line.committed_vnd : null,
        ownerAlways: Array.isArray(line.owner_always) ? line.owner_always.map(String) : [],
      };
    })
    .sort((a, b) => (a.lineId < b.lineId ? -1 : a.lineId > b.lineId ? 1 : 0));
}

/**
 * Read one draft proposal against the figures the server already published for this incident.
 *
 * @param {object} draft
 * @param {any} draft.options the `RemedyOptionsResponse` for this incident
 * @param {string} draft.kind
 * @param {boolean} [draft.storeFaultAttested]
 * @param {string} [draft.lineId] the priced line a damage or loss proposal names
 * @param {string} [draft.typedAmount] what is in the money box, exactly as typed
 * @param {string} [draft.typedLateness] what is in the lateness box, exactly as typed
 * @returns {{
 *   state: string,
 *   kind: string,
 *   amountVnd: number|null,
 *   ceilingVnd: number|null,
 *   committedVnd: number|null,
 *   itemFeeVnd: number|null,
 *   itemFeeBasis: string|null,
 *   hasCeiling: boolean,
 *   windowClosesAt: string|null,
 *   windowOpen: boolean|null,
 *   ownerThresholdVnd: number|null,
 *   ownerPossible: boolean,
 *   requiresOwner: boolean|null,
 *   ownerReasons: string[],
 *   needsAmount: boolean,
 *   needsLine: boolean,
 *   needsLateness: boolean,
 * }}
 */
export function remedyPlan(draft) {
  const options = draft.options || null;
  const kind = draft.kind;
  const isLoss = kind === REMEDY_KIND.LOST_ITEM;

  const needsLine = ITEM_KINDS.has(kind);
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
    committedVnd: null,
    itemFeeVnd: null,
    itemFeeBasis: null,
    hasCeiling: false,
    windowClosesAt: window ? window.closesAt : null,
    windowOpen: window ? window.open : null,
    ownerThresholdVnd: threshold,
    // `DEC-031`: a loss needs the owner before anything else is known, so it is said from the
    // first render — the one fact about a loss that does not wait for a line or an amount.
    ownerPossible: isLoss,
    requiresOwner: isLoss ? true : null,
    ownerReasons: isLoss ? [OWNER_REASON.LOSS_CLAIM] : [],
    // `needsAmount` defaults to false for every kind and every state, and only the item branch
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
      ownerReasons: overOwner ? [OWNER_REASON.ABOVE_STAFF_LIMIT] : [],
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

  // DAMAGE_COMPENSATION or LOST_ITEM. The cap belongs to one priced item, so there is nothing to
  // show until a line is named: a per-order ceiling does not exist and inventing one would be a
  // figure.
  const line = damageLines(options).find((candidate) => candidate.lineId === draft.lineId);
  if (!draft.lineId || !line || line.ceiling === null || line.committed === null) {
    return plan(PLAN.LINE_NOT_CHOSEN);
  }
  const cap = line.ceiling;
  const committed = line.committed;
  // Before an amount exists: which reasons already apply whatever it will be. The server's order.
  const standing = [
    ...(isLoss ? [OWNER_REASON.LOSS_CLAIM] : []),
    ...line.ownerAlways.filter((reason) => reason !== OWNER_REASON.LOSS_CLAIM),
  ];
  const bound = {
    ceilingVnd: cap,
    committedVnd: committed,
    itemFeeVnd: line.itemFee,
    itemFeeBasis: line.basis,
    hasCeiling: true,
    // The cap is known, so the money box may exist. This is the only place it is switched on.
    needsAmount: true,
    // Before a single digit is typed: either something already sends every amount on this item to
    // the owner, or the item's cap is above what a staff member may approve, so some amounts on it
    // will. That is the sentence the packet asks for.
    ownerPossible: standing.length > 0 || (threshold !== null && cap > threshold),
    requiresOwner: standing.length > 0 ? true : null,
    ownerReasons: standing,
  };

  const typed = String(draft.typedAmount ?? "").trim();
  if (!typed) return plan(PLAN.AMOUNT_MISSING, bound);
  const amount = parseDong(typed);
  if (amount === null) return plan(PLAN.NOT_AN_AMOUNT, bound);
  // The same sum `evaluate_remedy` makes: what the item already carries plus what is asked now.
  // Both limits are about the item, so both are compared against it — comparing the typed amount
  // alone is how the form said "staff" where the server said "owner".
  const total = committed + amount;
  const overStaff = threshold !== null && total > threshold;
  const reasons = overStaff ? [...standing, OWNER_REASON.ABOVE_STAFF_LIMIT] : standing;
  // Read once, and carried onto every state below it, including the ones that refuse. What the
  // owner has to decide does not depend on whether the person at the counter has ticked a box yet.
  const read = {
    ...bound,
    amountVnd: amount,
    requiresOwner: reasons.length > 0,
    ownerReasons: reasons,
  };
  if (total > cap) return plan(PLAN.ABOVE_CEILING, read);
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
 * and this builds exactly that shape. Damage and loss share theirs.
 *
 * @param {ReturnType<typeof remedyPlan>} plan
 * @param {{lineId?: string, typedLateness?: string}} draft
 * @returns {Record<string, unknown>|null} null when the plan is not one the console may send
 */
export function remedyProposalBody(plan, draft) {
  if (plan.state !== PLAN.READY) return null;
  if (ITEM_KINDS.has(plan.kind)) {
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
 * `RemedyStatus`, verbatim. `POLICY_UNRESOLVED` is the wall for losses recorded before `DEC-031`
 * and nothing moves it out.
 *
 * @param {string|null|undefined} status
 * @returns {boolean}
 */
export function awaitsOwner(status) {
  return status === "OWNER_APPROVAL_REQUIRED";
}
