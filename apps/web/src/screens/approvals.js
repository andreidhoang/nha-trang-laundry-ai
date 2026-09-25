/**
 * Approvals: the envelopes waiting for a human, and an honest account of what may be decided here.
 *
 * `CONSOLE-REDESIGN-003` (spec V2 §5.5) changed where things sit, not what is decided or how: the
 * queue is the first thing under the title, behind a two-way switch (Chờ duyệt · Giá trong
 * khoảng); each envelope is one card titled with what it asks ("Duyệt bồi hoàn · Phiếu 17"), its
 * countdown, who may decide, the bound content exactly as below, then Từ chối / Duyệt; identifiers
 * and digests are in the card's technical drawer; the V1 limits panel is the ⓘ on the title,
 * verbatim. A recorded decision is a toast, the queue re-read, and the tab bar's badge refreshed.
 * Every binding rule below is unchanged.
 *
 * Until 2026-09-17 this screen showed a queue and then refused to let anyone work it. The refusal
 * was real rather than an oversight — `ApprovalDecisionRequest` demands `resource_version`,
 * `snapshot_hash` and `rendered_hash`, and `GET /internal/v1/approvals` returned none of the three,
 * only `envelope_hash`, which is a different value and not a substitute. No approver could build a
 * valid decision from anything any client could read, so the A2 gate had a queue and no human side:
 * an envelope expired unactioned and nobody could have prevented it.
 *
 * The three values were stored `NOT NULL` on `approval_requests` from the first migration and were
 * simply never projected. They are now, and the rules that survive are these:
 *
 *   - **Decide only what the console can show you.** Pressing "Duyệt" on a digest of content you
 *     were never shown is blind approval, and returning the hashes does not by itself fix that. So
 *     the controls enable for a resource type this console can actually render — today `ORDER`,
 *     which `#/orders/:orderId` opens — and stay disabled, with the resource type named, for every
 *     type it cannot.
 *
 *     `MESSAGE_DRAFT` used to be the example of a type it cannot, with the reason "nothing stores
 *     the message body, and `rendered_hash` is not verified server-side". Both halves stopped
 *     being true: the body is `agent_drafts` / `agent_draft_reviews`, and `API-INTEGRITY-002` made
 *     the server derive and verify all three binding values from it. What was still missing was a
 *     read, and `MESSAGE-DRAFT-BINDING-001` added it. A `SEND_MESSAGE` card now fetches the exact
 *     words from `GET /internal/v1/stores/{store}/message-drafts/{draft}/binding`, compares the
 *     version and both digests with the envelope's, prints the text above the buttons, and stays
 *     unapprovable — refusable only — when the draft moved after the envelope was raised.
 *
 *     `RANGE-APPROVAL-VISIBILITY-001` found the hole in that rule and it is worth stating plainly,
 *     because the rule read as sound while it was being broken: the gate was on the *resource
 *     type*, and one action — `SET_RANGE_PRICE` — asks for a number the linked screen does not
 *     render. The envelope binds the revision before the price was chosen, so `#/quotes` showed
 *     the published band and the owner approved an amount nobody had put in front of them. The
 *     gate is therefore on the action as well now, and a `SET_RANGE_PRICE` card fetches the
 *     proposed amounts and stays undecidable until they are on screen above the buttons.
 *
 *     `EXPORT_REQUEST` is the same rule applied to the opposite failure. `OPS-BOARD-001` built the
 *     sanitized export and `#/exports` raises its envelope, but the resource type was absent from
 *     the table below — so an `EXPORT_SANITIZED_DATA` envelope reached this queue and could never
 *     be decided by anyone. Not a locked button: a dead end, in which a staff member could request
 *     an export that no owner in the shop was able to release. Adding the type alone would have
 *     traded that for blind approval of a digest, so the card fetches the export's own business
 *     date, column list and exclusions and prints them above the buttons, and blocks when it
 *     cannot.
 *
 *     `REMEDY_PROPOSAL` was the same dead end again, and on money. `DEC-031` sends every loss,
 *     every compensation on a refunded order and anything above the staff limit to the owner as
 *     an `APPROVE_REMEDY` envelope, and the type had no entry below, so the owner could not
 *     approve a lost-item claim from the console at all. `REMEDY-OWNER-DECIDE-001` gave the card
 *     `GET /internal/v1/stores/{store}/remedy-proposals/{proposal}/approval-binding`, and the card
 *     is built like the `MESSAGE_DRAFT` one: the envelope's own store, the binding compared with
 *     the envelope, the figures above the buttons, and only "Từ chối" when they do not match.
 *   - **The server is the authority, not this list.** `_require_exact_binding` re-checks the
 *     version and both digests at decision time, `_authorize_decision` re-checks store membership,
 *     role, MFA and maker-checker separation. A stale card cannot approve anything: the decision is
 *     refused, which is why a `STALE` refusal here offers a reload rather than a retry.
 *   - **Only `REQUESTED` is listed.** The `WHERE s.status = 'REQUESTED'` clause means approved,
 *     rejected and expired envelopes are not in this list and cannot be reviewed from it.
 *   - **The countdown is the point.** Approval TTLs are 10, 15 or 30 minutes by action
 *     (`SECURITY_RELIABILITY_SPEC_V1.md:354`) — except an owner-only remedy envelope, which since
 *     the DEC-031 addendum stays open until the end of the next business day in Asia/Ho_Chi_Minh
 *     (up to 48 hours), and which `countdown` renders in hours and days rather than minutes. An
 *     expiry never extends implicitly, so remaining time matters far more than a wall-clock
 *     timestamp. The queue is ordered by expiry, so a day-long remedy envelope sits below the
 *     ten-minute ones that need an answer first. One interval drives every badge and stops
 *     itself the moment the screen leaves the document — a console is left open all day, and a
 *     leaked timer per navigation is a real bug rather than a tidiness complaint.
 *
 * The bullet this docstring used to carry first — that the queue joins through `orders` and so
 * hides every non-order resource type — was stale from migration `0034`, which gave
 * `approval_requests` its own `store_id`. The limits panel on this same screen had already been
 * corrected; the docstring had not, and the two halves of one module contradicted each other.
 *
 * @module screens/approvals
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import {
  UNKNOWN,
  count,
  countdown,
  dateOnly,
  dateTime,
  money,
  moneyRange,
  shortId,
} from "../core/format.js";
import { enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import { KIND_LABEL as REMEDY_KIND_LABEL, ownerReasonText } from "./remedies.js";
import { newOrderForContact } from "../ui/handoff.js";
import {
  errorNotice,
  explain,
  facts,
  gated,
  listView,
  markUpdated,
  resultLine,
  setResult,
  toolbar,
} from "../ui/components.js";
import {
  button,
  emptyState,
  infoButton,
  page,
  segmented,
  statusPill,
  techDetails,
  toast,
} from "../ui/kit.js";

const LIST_LIMIT = 100;

/** One second. Fast enough that a badge flipping to expired is seen, slow enough to cost nothing. */
const TICK_MS = 1000;

/** The id the disabled decision controls point at, so the refusal is announced with the control. */
const BLOCK_ID = "approval-decision-blocked";

/**
 * The remaining-time pill for one envelope.
 *
 * Rebuilt whole on every tick rather than having its text patched, because the warn → danger flip
 * is carried by `data-state` and not by the words. A tick that updated only the text would leave an
 * expired envelope wearing the colour of a live one. What the time means is in the pill's `title`
 * and in the ⓘ on the page header ("Thời hạn quyết định").
 *
 * @param {string|null|undefined} expiresAt
 * @returns {HTMLElement}
 */
function countdownPill(expiresAt) {
  const left = countdown(expiresAt);
  const pill = statusPill({
    state: left.expired ? "danger" : "warn",
    text: left.expired ? "Đã hết hạn" : left.text,
  });
  pill.title = left.expired
    ? "đã hết hạn — không tự gia hạn, phải tạo yêu cầu mới"
    : "thời gian còn lại trước khi hết hạn";
  return pill;
}

/**
 * "Phiếu 17 · 25/09/2026" — the paper ticket an order was issued, or null for an order without one.
 * The same words `#/orders` uses, kept here so this screen does not import another screen.
 *
 * @param {any} read
 * @returns {string|null}
 */
function ticketText(read) {
  if (read?.ticket_number === null || read?.ticket_number === undefined) return null;
  return `Phiếu ${read.ticket_number} · ${dateOnly(read.ticket_issued_on)}`;
}

/**
 * "24/09/2026" from the server's `business_date` ("2026-09-24"), without a time-zone round trip: a
 * business date is already a day in the shop's zone and must not be shifted by parsing it as an
 * instant.
 *
 * @param {unknown} value
 * @returns {string}
 */
function businessDay(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value ?? ""));
  return match ? `${match[3]}/${match[2]}/${match[1]}` : String(value ?? UNKNOWN);
}

/**
 * Resource types whose content this console can put in front of an approver before they decide.
 *
 * The key is the server's `resource_type`; the value builds the route that shows it. A type absent
 * from this table is not decidable here, and the card says which type it is rather than a generic
 * refusal — "không xem được nội dung loại SLOT_PROPOSAL" tells an approver what to go and fix,
 * "không quyết được" does not.
 *
 * `QUOTE_REVISION` was deliberately absent until `RANGE-PRICE-001`, and the reason it left is the
 * only legitimate one: the limitation behind it closed. The entry used to read "there is no GET
 * for a single revision and no endpoint returns `quote_lines`, so the approver would see a total
 * and a hash, never the lines being priced". `GET /internal/v1/stores/{store}/quotes/{quote}` now
 * returns the revision with its lines, each carrying `price_kind` and, for a band, the two bounds.
 * `#/quotes?quote=<id>` renders it read-only. The envelope's `resource_id` is the quote's own id
 * (`operations.py` passes `quote_id` into the approval command) and its `resource_version` is the
 * revision, so the link needs nothing the queue does not return.
 *
 * That entry used to end "— which is exactly the content a `SET_RANGE_PRICE` envelope is about".
 * It was wrong, and it is corrected rather than deleted because the mistake is instructive: the
 * banded revision is what the envelope *binds*, and the proposed amount is what it *authorises*,
 * and those are not the same content. The link is still right for every action here; it is no
 * longer sufficient for one of them. See `SET_RANGE_PRICE` below.
 *
 * `MESSAGE_DRAFT` was kept out "for a reason that has not changed: nothing stores the message
 * body, and `rendered_hash` is explicitly not verified server-side". The reason changed twice and
 * this entry did not: `agent_drafts` (migration `0021`) stores the body, `API-INTEGRITY-002` made
 * the server compute and verify all three binding values from it, and `MESSAGE-DRAFT-BINDING-001`
 * gave the console the read. It is in the table now, as a builder that returns `null` like
 * `EXPORT_REQUEST` below, because the words are printed on the card itself. See `MESSAGE_DRAFT`.
 *
 * The second argument is the envelope's `resource_version`, and the quote link carries it. Without
 * it `#/quotes?quote=<id>` opened whichever revision is *newest*, which is not necessarily the one
 * the envelope binds: an approver could read revision 3 and sign the digest of revision 1. The
 * order link does not take one, and the gap that leaves is recorded on `#/gaps` rather than
 * papered over here.
 *
 * @type {Record<string, (resourceId: string, resourceVersion: number) => string>}
 */
const VIEWABLE_RESOURCES = {
  ORDER: (resourceId) => `#/orders/${encodeURIComponent(resourceId)}`,
  QUOTE_REVISION: (resourceId, resourceVersion) =>
    `#/quotes?quote=${encodeURIComponent(resourceId)}` +
    `&revision=${encodeURIComponent(String(resourceVersion))}`,
  // `OPS-BOARD-001` built the sanitized export and `#/exports` raises its envelope, but this table
  // had no entry for the resource type that envelope names — so `EXPORT_SANITIZED_DATA` reached
  // this queue permanently undecidable. A staff member could ask for an export that no owner in
  // the shop was able to release: not a locked button but a dead end, and the one capability the
  // approval vocabulary had named since the first migration.
  //
  // `null` rather than a route, and that is the entry's real content. There is no screen that
  // renders a stored export request: `#/exports` is the form that creates one and would open blank
  // and unrelated, so a link to it would be this console pointing an owner at the wrong document
  // while telling them to read it before deciding. The card fetches the request's own contents
  // instead and prints them above the buttons, exactly as a `SET_RANGE_PRICE` card prints the
  // amounts, and stays undecidable until they are on screen. A builder that returns `null` means
  // "this console can show you this, and it shows you here" — not "there is nothing to show".
  EXPORT_REQUEST: () => null,
  // `MESSAGE-DRAFT-BINDING-001`. `null` for the reason the export entry above gives: the words the
  // envelope binds are fetched and printed on the card, and no other screen renders one draft's
  // current sendable text. The card stays shut until that text is on screen.
  MESSAGE_DRAFT: () => null,
  // `REMEDY-OWNER-DECIDE-001`. Since `DEC-031` every loss, every compensation on a refunded order
  // and anything above the staff limit waits here for the owner, and until this entry the queue
  // showed such an envelope and let nobody decide it — the dead end `EXPORT_REQUEST` above once
  // was. `null` for the same reason: the card reads the proposal's own figures and prints them
  // above the buttons, and stays shut until they are on screen and match the envelope.
  REMEDY_PROPOSAL: () => null,
};

/**
 * The one action whose content the quote screen above does *not* contain.
 *
 * `RANGE-APPROVAL-VISIBILITY-001`. Four actions carry the `QUOTE_REVISION` resource type —
 * `PRESENT_QUOTE`, `FINALIZE_QUOTE`, `APPLY_PROMOTION` and this one — and the entry above treated
 * all four alike because the queue returned only the type. For three of them that was right: the
 * revision holds the content, and `#/quotes?quote=<id>` renders it.
 *
 * `SET_RANGE_PRICE` is the exception, and it is the case where it matters most. The revision it
 * binds is the revision *before* the price was chosen, so the quote screen renders the published
 * BAND — "Áo dài truyền thống · 80.000 ₫ – 240.000 ₫" — and never the proposed number, which lived
 * on no read at all. An owner opening the queue, following the link and pressing Duyệt was
 * authorising a figure they had not been shown: a staff member could agree 150.000 ₫ with the
 * customer, propose 240.000 ₫, and this approval — the only second-party control over that number
 * — would pass it through.
 *
 * So a card for this action loads the amounts first and stays undecidable until they are on
 * screen. The card below never enables an approve control from a resource type alone.
 */
const SET_RANGE_PRICE = "SET_RANGE_PRICE";

/**
 * The resource type whose content this card fetches and prints itself.
 *
 * Same rule as `SET_RANGE_PRICE` above and a stronger reason for it. An export is the one act on
 * this queue whose result leaves every control the system has: once the CSV is on a laptop, no
 * retention schedule reaches it and no approval can be withdrawn. So the owner sees the business
 * day, the exact column list and the exclusions the file promises before the approve control is
 * reachable — and if any of those cannot be read, the control stays shut.
 */
const EXPORT_REQUEST = "EXPORT_REQUEST";

/**
 * The resource type of a `SEND_MESSAGE` envelope, whose words this card fetches and prints.
 *
 * `MESSAGE-DRAFT-BINDING-001`. A message leaves the building on a person's manual send, and the
 * approval is the second person's statement that these exact words may go to this customer. So the
 * card reads the draft's server-computed binding, refuses to show anything whose version or digests
 * are not the envelope's own, and prints the text above the buttons as untrusted text — through
 * text nodes, never markup, because a customer or a model wrote it.
 */
const MESSAGE_DRAFT = "MESSAGE_DRAFT";

/**
 * The resource type of an `APPROVE_REMEDY` envelope, whose figures this card fetches and prints.
 *
 * `REMEDY-OWNER-DECIDE-001`. The approval is the owner's statement that the shop may pay this
 * customer this amount for this item. So the card reads the proposal's server-resolved binding,
 * compares it with the envelope, and shows the kind, the amount, the ceiling, the item and why the
 * owner is needed above the buttons — or withholds all of it and offers only a refusal.
 */
const REMEDY_PROPOSAL = "REMEDY_PROPOSAL";

/** What this console records as its reason; the server only constrains the shape. */
const DECISION_REASONS = {
  APPROVED: "APPROVED_AFTER_CONSOLE_REVIEW",
  REJECTED: "REJECTED_AFTER_CONSOLE_REVIEW",
};

/**
 * Whether one queue row carries everything a valid decision needs.
 *
 * All three are required by `ApprovalDecisionRequest`, and a row missing any of them is a row this
 * client must not try to decide from — sending a partial binding would be refused server-side
 * anyway, and the refusal would read as a bug rather than as a missing field.
 *
 * @param {any} item
 * @returns {boolean}
 */
function hasBinding(item) {
  return (
    Number.isInteger(item.resource_version) &&
    typeof item.snapshot_hash === "string" &&
    typeof item.rendered_hash === "string"
  );
}

/**
 * The two controls an approver uses, enabled only when this console can show the thing being
 * approved.
 *
 * Still not wrapped in `gated()` for the disabled case: `gated()` says "your role may not", and a
 * type this screen cannot render is refused for `OWNER_ADMIN` too. The role verdict is applied on
 * top, for the enabled case only, so an auditor sees the role reason and an owner looking at a
 * `SLOT_PROPOSAL` sees the content reason.
 *
 * `refuseOnly` is the one partial state, and it exists for `MESSAGE_DRAFT`. When the draft moved
 * after the envelope was raised, approving is refused — here and by the server — but refusing is
 * not: a refusal authorises nothing, and it is how the approver takes a dead envelope out of the
 * queue instead of watching it for thirty minutes. So "Duyệt" stays shut with the reason and
 * "Từ chối" works, under the same role gate as ever.
 *
 * @param {any} item
 * @param {(message: string) => Promise<void>} onDecided
 * @param {{allowed: boolean, reason: string}} verdict
 * @param {string|null} [contentBlock] why this card's content cannot be shown yet, if it cannot
 * @param {{refuseOnly?: boolean}} [options]
 * @returns {HTMLElement}
 */
function decisionControls(item, onDecided, verdict, contentBlock = null, options = {}) {
  const viewer = VIEWABLE_RESOURCES[String(item.resource_type)];
  const decidable = Boolean(viewer) && hasBinding(item) && !contentBlock;
  const refusable =
    !decidable &&
    Boolean(viewer) &&
    hasBinding(item) &&
    Boolean(contentBlock) &&
    options.refuseOnly === true;
  const host = resultLine();

  /**
   * A shut control: visible, disabled, and pointing at the explanation of why (V1 invariant 5).
   *
   * @param {string} label
   * @param {"primary"|"secondary"} variant
   */
  const shut = (label, variant) => {
    const control = button({ label, variant, block: true, disabled: true });
    control.setAttribute("aria-disabled", "true");
    control.setAttribute("aria-describedby", BLOCK_ID);
    return control;
  };

  if (!decidable && !refusable) {
    return h(
      "div",
      { class: "approval__decide" },
      h(
        "p",
        { class: "hint approval__why" },
        !viewer
          ? `Không bấm được: bảng vận hành chưa mở được nội dung loại ${item.resource_type} để ` +
              "bạn xem trước khi quyết. Duyệt một nội dung chưa xem là duyệt mù. Xem “Tại sao nút " +
              "Duyệt đang tắt?” ở nút ⓘ đầu trang."
          : // The content block is checked before the binding, because it is the more specific
            // answer: a `SET_RANGE_PRICE` card whose amounts have not arrived is blocked for that
            // reason and not for a missing hash it does in fact have.
            contentBlock ||
              "Không bấm được: phiếu này thiếu phiên bản hoặc mã niêm phong, nên không dựng được " +
                "một quyết định hợp lệ. Tải lại hàng chờ.",
      ),
      h(
        "div",
        { class: "approval__buttons" },
        shut("Từ chối", "secondary"),
        shut("Duyệt", "primary"),
      ),
      host,
    );
  }

  // Kept across a failure, like every write key on this console: a second press after a lost answer
  // carries the same key, so the server replays the recorded decision instead of deciding twice.
  const submission = new Submission(`approval-decision-${item.approval_request_id}`);
  // Where a failure is explained: a sibling of the buttons, never their parent. This used to be
  // `render(host.parentElement)`, which emptied the container holding Duyệt, Từ chối and the status
  // line -- so on a 504 both buttons vanished and the notice was the only thing left on the card.
  const failureHost = h("div");

  /** @param {"APPROVED"|"REJECTED"} decision */
  const send = async (decision, button, sibling) => {
    button.disabled = true;
    sibling.disabled = true;
    button.setAttribute("aria-busy", "true");
    render(failureHost);
    setResult(host, "warn", decision === "APPROVED" ? "Đang ghi phê duyệt…" : "Đang ghi từ chối…");
    try {
      await request(
        `/internal/v1/approvals/${encodeURIComponent(item.approval_request_id)}/decisions`,
        {
          method: "POST",
          body: {
            decision,
            reason_code: DECISION_REASONS[decision],
            // Sent back exactly as the queue gave them. The server re-checks all three against the
            // stored row, so a card that went stale while the approver was reading is refused
            // rather than silently deciding about an older version.
            resource_version: item.resource_version,
            snapshot_hash: item.snapshot_hash,
            rendered_hash: item.rendered_hash,
          },
          idempotencyKey: submission.key(),
        },
      );
      submission.reset();
      button.removeAttribute("aria-busy");
      setResult(host, "ok", decision === "APPROVED" ? "Đã phê duyệt." : "Đã từ chối.");
      // Reported to the screen as a toast, not only to this card: `onDecided()` reloads the queue
      // and a decided envelope is no longer `REQUESTED`, so the card this line lives in is removed
      // a moment later. The toast is the statement that the decision was recorded.
      await onDecided(
        decision === "APPROVED"
          ? `Đã duyệt: ${cardTitle(item)}. Phiếu rời khỏi hàng chờ.`
          : `Đã từ chối: ${cardTitle(item)}. Phiếu rời khỏi hàng chờ.`,
      );
    } catch (error) {
      button.removeAttribute("aria-busy");
      // A refuse-only card's approve control was never pressable and must not become so because
      // a refusal failed: it is re-enabled only on a card that could approve in the first place.
      button.disabled = button === approve && !decidable;
      sibling.disabled = sibling === approve && !decidable;
      const stale = error.kind === "STALE" || error.kind === "PRECONDITION_REQUIRED";
      // A lost answer is not a refusal. The decision may have been recorded, so "không có gì được
      // ghi" would be a guess; what is certain is that a second press cannot decide twice, because
      // it carries the same key and an envelope can only leave REQUESTED once.
      const unknown =
        error.kind === "TIMEOUT" ||
        error.kind === "NETWORK" ||
        error.kind === "FAULT" ||
        error.kind === "UNAVAILABLE";
      setResult(host, null, null);
      render(
        failureHost,
        errorNotice(error, {
          title: stale
            ? "Phiếu này vừa đổi trong lúc bạn đang xem, nên quyết định của bạn bị từ chối và " +
              "không có gì được ghi. Tải lại hàng chờ rồi đọc lại phiếu mới."
            : unknown
              ? "Không ghi được quyết định: máy chủ không trả lời được, nên chưa biết quyết định " +
                "đã vào hay chưa. Tải lại hàng chờ — phiếu đã rời hàng chờ thì quyết định đã được " +
                "ghi. Phiếu còn đó thì bấm lại; máy chủ không ghi một phiếu hai lần."
              : `Không ghi được quyết định: ${error.message} Không có gì được ghi.`,
          actions: [
            h(
              "button",
              { type: "button", dataVariant: "quiet", onClick: () => void onDecided("") },
              "Tải lại hàng chờ",
            ),
          ],
        }),
      );
    }
  };

  const approve = decidable
    ? button({ label: "Duyệt", variant: "primary", block: true, network: true })
    : shut("Duyệt", "primary");
  const reject = button({ label: "Từ chối", block: true, network: true });
  if (decidable) approve.addEventListener("click", () => void send("APPROVED", approve, reject));
  reject.addEventListener("click", () => void send("REJECTED", reject, approve));

  if (!decidable) {
    return h(
      "div",
      { class: "approval__decide" },
      h("p", { class: "hint approval__why" }, contentBlock),
      h(
        "p",
        { class: "hint" },
        "Từ chối vẫn bấm được: từ chối không cho phép gửi gì, và là cách gỡ một phiếu đã cũ " +
          "khỏi hàng chờ thay vì chờ nó hết hạn.",
      ),
      h("div", { class: "approval__buttons" }, gated(reject, verdict), approve),
      host,
      failureHost,
    );
  }

  // `null` is a legitimate answer here and means "the content is already on this card", which is
  // the shape an `EXPORT_REQUEST` takes: there is no screen that renders a stored export request,
  // so a link would send the approver somewhere that is not the document they are signing. That
  // the server re-checks the version and both digests at the press is said once, in the ⓘ on the
  // page header, rather than under every card.
  const href = viewer(String(item.resource_id), item.resource_version);

  return h(
    "div",
    { class: "approval__decide" },
    typeof href === "string"
      ? h("a", { class: "approval__open", href }, "Mở nội dung này trước khi quyết")
      : null,
    h("div", { class: "approval__buttons" }, gated(reject, verdict), gated(approve, verdict)),
    h(
      "p",
      { class: "hint" },
      "Máy chủ từ chối quyết định của chính người đã tạo yêu cầu, nên phiếu do bạn mở sẽ bị từ " +
        "chối ở bước này.",
    ),
    host,
    failureHost,
  );
}

/**
 * The amounts a `SET_RANGE_PRICE` envelope is asking for, with the bound each was checked against.
 *
 * Both numbers per line, always. The band alone is what this screen used to show and is what made
 * the approval blind; the amount alone would be a number with no statement of what authorised it.
 * The pair is the disclosure, and `moneyRange` renders the band whole because a range is shown
 * whole everywhere else on this console.
 *
 * Nothing here is computed. The server sends three integers per line and this formats them.
 *
 * @param {any} proposal the `RangePriceProposalContentResponse` body
 * @param {any[]} lines its lines, already known to be non-empty
 * @returns {HTMLElement}
 */
function proposedAmounts(proposal, lines) {
  return h(
    "div",
    { class: "notice", dataState: "warn" },
    h(
      "p",
      { class: "notice__title" },
      "Số tiền bạn đang được đề nghị duyệt — bản báo giá ",
      // The revision is its own node rather than part of the sentence: a string built by
      // concatenating a value registers in the disclosure contract as the literal half of itself,
      // which would put a sentence ending in "v" on the console's honesty register.
      h("span", { class: "mono" }, `v${String(proposal.revision)}`),
    ),
    h(
      "dl",
      { class: "fields" },
      lines.map((line) =>
        h(
          "div",
          { class: "field field--span" },
          h("dt", null, h("span", { class: "mono" }, String(line.service_code))),
          h(
            "dd",
            null,
            h(
              "div",
              { class: "stack stack--tight" },
              h(
                "div",
                { class: "row" },
                "Khoảng chủ tiệm đã công bố: ",
                h(
                  "span",
                  { class: "mono" },
                  moneyRange(line.band_minimum_vnd, line.band_maximum_vnd).text,
                ),
              ),
              h(
                "div",
                { class: "row" },
                "Nhân viên đề nghị: ",
                h("strong", { class: "money" }, money(line.proposed_amount_vnd)),
              ),
            ),
          ),
        ),
      ),
    ),
    h(
      "p",
      { class: "hint" },
      "Bấm Duyệt là duyệt đúng con số này, trên đúng bản báo giá đang niêm phong. Đây chưa phải " +
        "tiền đã thu: khách vẫn phải đồng ý và thanh toán sau.",
    ),
  );
}

/**
 * What one export envelope actually releases, in the owner's own language.
 *
 * Four things, and the order is the argument. The day first, because an export is chosen by day
 * and the wrong day is the commonest mistake. Then the exact columns, then the exclusions — a
 * reader told only what a file contains cannot tell "the complaint text is not here" from "no
 * complaint was recorded", which is the difference the word *sanitized* is claiming. Then the
 * sentence the server hashed into `rendered_hash`, verbatim: that string is the document, and a
 * console paraphrasing it would be showing an owner something other than what they sign.
 *
 * The day boundary is on the card beside the money columns for a reason worth stating. This file
 * is cut on `orders.created_at` and the takings figure on `#/today` is cut on when money was
 * taken, so the same words — *tiền đã thu* — name two different numbers in this console. Naming
 * which one this is costs a line here and saves an argument about which spreadsheet is wrong.
 *
 * Nothing is computed. Every value is rendered as the server sent it.
 *
 * @param {any} record the `ExportRequestContentResponse` body
 * @returns {HTMLElement}
 */
function exportContents(record) {
  const columns = Array.isArray(record.columns) ? record.columns : [];
  const excludes = Array.isArray(record.excludes) ? record.excludes : [];
  return h(
    "div",
    { class: "notice", dataState: "warn" },
    h("p", { class: "notice__title" }, "Dữ liệu bạn đang được đề nghị cho rời khỏi hệ thống"),
    facts([
      ["Ngày làm việc", String(record.business_date)],
      ["Múi giờ", record.business_timezone || UNKNOWN, { mono: true }],
      ["Bộ dữ liệu", record.dataset || UNKNOWN, { mono: true }],
      ["Cắt ngày theo", record.day_boundary || UNKNOWN, { mono: true, span: true }],
      ["Có trong tệp", h("span", { class: "mono" }, columns.join(", ")), { span: true }],
      ["Cố ý không có", h("span", { class: "mono" }, excludes.join(", ")), { span: true }],
      ["Truy vấn", record.query_version || UNKNOWN, { mono: true, span: true }],
    ]),
    h("p", null, record.statement_vi || UNKNOWN),
    h(
      "p",
      { class: "hint" },
      "Bấm Duyệt là cho đúng danh sách cột này, của đúng ngày này, rời khỏi hệ thống. Tệp tải về " +
        "nằm ngoài mọi lịch xoá dữ liệu và không thu hồi lại được.",
    ),
  );
}

/**
 * Fetch what one `EXPORT_REQUEST` envelope releases, and unblock its controls — or not.
 *
 * Every failure path leaves the controls where they started: shut, with a reason. Same design as
 * `loadProposedAmounts`, and there is no failure here whose right answer is to let the press
 * through — an approved export produces a file, and a file cannot be unapproved.
 *
 * One branch is not a failure at all and still blocks: `requested_by_you`. `_OWNER_FINANCIAL`
 * carries `SEPARATION_OF_DUTY`, and for an export the server binds it to the person who DEFINED
 * the export rather than to whoever raised the envelope — so an owner who asked for this file
 * cannot approve it, however the envelope came to be. That is a refusal the server will make
 * anyway; making it here means the owner reads it beside the shut control instead of meeting an
 * opaque 403 after pressing.
 *
 * @param {any} item
 * @param {HTMLElement} contentHost
 * @param {HTMLElement} controlsHost
 * @param {(message: string) => Promise<void>} onDecided
 * @param {{allowed: boolean, reason: string}} verdict
 * @param {(detail: string) => void} [retitle] names the day on the card's title once it is read
 * @returns {Promise<void>}
 */
async function loadExportRequest(item, contentHost, controlsHost, onDecided, verdict, retitle) {
  /** @param {string} reason @param {HTMLElement} explanation */
  const block = (reason, explanation) => {
    render(contentHost, explanation);
    render(controlsHost, decisionControls(item, onDecided, verdict, reason));
  };
  try {
    const record = await request(
      `/internal/v1/approvals/${encodeURIComponent(item.approval_request_id)}/export-request`,
    );
    const columns = Array.isArray(record.columns) ? record.columns : [];
    if (!columns.length) {
      block(
        "Không bấm được: máy chủ không trả về cột nào cho yêu cầu xuất này.",
        h(
          "div",
          { class: "notice", dataState: "danger" },
          h("p", { class: "notice__title" }, "Không có danh sách cột để xem"),
          h(
            "p",
            null,
            "Phiếu này xin cho dữ liệu rời khỏi hệ thống, nhưng máy chủ không nói được tệp sẽ " +
              "mang những cột nào. Không duyệt. Báo kỹ thuật và để phiếu tự hết hạn.",
          ),
        ),
      );
      return;
    }
    if (record.rendered_hash !== item.rendered_hash) {
      // The server re-derives this digest from the column list in force right now. A disagreement
      // means the export's columns moved after the envelope was raised, so the file would carry
      // something the owner never saw -- and the release itself would refuse with
      // `EXPORT_APPROVAL_NOT_BOUND`. Nothing here is fixable by pressing.
      block(
        "Không bấm được: nội dung đọc được không khớp mã niêm phong của phiếu trong hàng chờ.",
        h(
          "div",
          { class: "notice", dataState: "danger" },
          h("p", { class: "notice__title" }, "Nội dung không khớp phiếu"),
          h(
            "p",
            null,
            "Danh sách cột của bản xuất đã đổi kể từ lúc phiếu này được mở, nên tệp sẽ không " +
              "giống bản mô tả mà phiếu đang niêm phong. Máy chủ cũng sẽ từ chối xuất. Tạo lại " +
              "yêu cầu xuất và xin duyệt lại.",
          ),
        ),
      );
      return;
    }
    render(contentHost, exportContents(record));
    retitle?.(`ngày ${businessDay(record.business_date)}`);
    if (record.requested_by_you === true) {
      render(
        controlsHost,
        decisionControls(
          item,
          onDecided,
          verdict,
          "Không bấm được: yêu cầu xuất này do chính bạn tạo, và người chọn dữ liệu nào rời khỏi " +
            "hệ thống không được tự duyệt. Nhờ một chủ tiệm khác quyết.",
        ),
      );
      return;
    }
    render(controlsHost, decisionControls(item, onDecided, verdict));
  } catch (error) {
    block(
      "Không bấm được: chưa đọc được nội dung yêu cầu xuất. Chưa thấy dữ liệu thì chưa quyết.",
      h(
        "div",
        { class: "stack stack--tight" },
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Chưa xem được dữ liệu sắp rời khỏi hệ thống"),
          h(
            "p",
            null,
            "Phiếu này xin cho một bản sao hồ sơ của cửa hàng rời khỏi hệ thống. Chừng nào chưa " +
              "đọc được ngày và danh sách cột của bản xuất thì nút Duyệt vẫn khoá. Máy chủ nêu " +
              "lý do bên dưới, nguyên văn.",
          ),
        ),
        errorNotice(error),
      ),
    );
  }
}

/**
 * The words one `SEND_MESSAGE` envelope binds, exactly as the server stores them.
 *
 * Untrusted text: a customer's conversation shaped it and a model or a reviewer wrote it. Every
 * line goes through `h()`, which appends text nodes, so markup in a draft is shown as characters
 * and never parsed. Line breaks are kept by splitting into paragraphs rather than by styling
 * whitespace, the way `#/shadow` renders the same drafts.
 *
 * The recipient is the opaque contact binding and nothing more — no phone number or chat id exists
 * on a draft. It is shown shortened, as the manual-send result shows it, so the approver can see
 * that the envelope names one recipient without the console disclosing who.
 *
 * Nothing is computed. The server sends the text and the identifiers, and this lays them out.
 *
 * @param {any} read the `MessageDraftBindingResponse` body
 * @returns {HTMLElement}
 */
function messageContents(read) {
  const raw = typeof read.text === "string" ? read.text : "";
  const lines = raw.split(/\r?\n/).filter((line) => line.trim() !== "");
  return h(
    "div",
    { class: "notice", dataState: "warn" },
    h("p", { class: "notice__title" }, "Tin nhắn bạn đang được đề nghị cho gửi"),
    h("p", { class: "eyebrow" }, "Nội dung sẽ gửi · Văn bản không tin cậy"),
    h(
      "div",
      { class: "stack stack--tight", dataMessageBody: "true" },
      lines.length
        ? lines.map((line) => h("p", null, line))
        : h("p", { class: "hint" }, `${UNKNOWN} bản nháp không có chữ nào`),
    ),
    facts([
      ["Số ký tự", String(raw.length)],
      ["Phiên bản bản nháp", h("span", { class: "mono" }, `v${String(read.resource_version)}`)],
      [
        "Người nhận (mã ràng buộc)",
        h(
          "span",
          { class: "mono", title: read.recipient_binding_id || "" },
          shortId(read.recipient_binding_id),
        ),
      ],
      [
        "Bản nháp",
        h("span", { class: "mono", title: read.resource_id || "" }, shortId(read.resource_id)),
      ],
    ]),
    h(
      "p",
      { class: "hint" },
      "Bấm Duyệt là cho phép đúng những chữ này tới đúng người nhận này. Duyệt chưa gửi gì cả: " +
        "một nhân viên vẫn phải tự gửi tay và ký tên ở màn hình Ngoại lệ.",
    ),
    // CONTACT-PICK-001: the customer this message is for, handed to ＋ Nhận đồ from this read.
    newOrderForContact(read.recipient_binding_id, read.store_id),
  );
}

/**
 * Fetch the words one `SEND_MESSAGE` envelope binds, and unblock its controls — or not.
 *
 * Same design as `loadExportRequest`: every failure leaves the approve control shut with a reason.
 * Two outcomes are not failures of the read and still keep it shut, with "Từ chối" left usable:
 *
 *   - the read answers a different version or different digests. A reviewer edited the draft after
 *     the envelope was raised, so the words on screen would not be the words the press hands back.
 *     The text is withheld rather than shown with a caveat — the approver must not read revision 2
 *     and sign revision 1 — and the server refuses the approval anyway;
 *   - the read answers 404. The draft has no sendable content any more: a reviewer rejected it.
 *
 * The store in the URL is the envelope's own, from the queue row. The queue spans every store the
 * approver is assigned to, so the store selected in the top bar would be the wrong one whenever
 * the two differ; a row without a store is refused rather than guessed.
 *
 * @param {any} item
 * @param {HTMLElement} contentHost
 * @param {HTMLElement} controlsHost
 * @param {(message: string) => Promise<void>} onDecided
 * @param {{allowed: boolean, reason: string}} verdict
 * @returns {Promise<void>}
 */
async function loadMessageDraft(item, contentHost, controlsHost, onDecided, verdict) {
  /** @param {string} reason @param {HTMLElement} explanation @param {boolean} [refuseOnly] */
  const block = (reason, explanation, refuseOnly = false) => {
    render(contentHost, explanation);
    render(controlsHost, decisionControls(item, onDecided, verdict, reason, { refuseOnly }));
  };
  const envelopeStore = typeof item.store_id === "string" ? item.store_id : "";
  if (!envelopeStore) {
    block(
      "Không bấm được: phiếu này không mang mã cửa hàng, nên không đọc được tin nhắn của nó.",
      h(
        "div",
        { class: "notice", dataState: "danger" },
        h("p", { class: "notice__title" }, "Không biết tin nhắn thuộc cửa hàng nào"),
        h("p", null, "Tải lại hàng chờ. Chưa đọc được chữ nào thì chưa quyết."),
      ),
    );
    return;
  }
  const storePart = encodeURIComponent(envelopeStore);
  const draftPart = encodeURIComponent(String(item.resource_id));
  try {
    const read = await request(
      `/internal/v1/stores/${storePart}/message-drafts/${draftPart}/binding`,
    );
    if (
      read.resource_id !== item.resource_id ||
      read.resource_version !== item.resource_version ||
      read.snapshot_hash !== item.snapshot_hash ||
      read.rendered_hash !== item.rendered_hash
    ) {
      block(
        "Không bấm Duyệt được: tin nhắn đã đổi sau khi phiếu này được mở.",
        h(
          "div",
          { class: "notice", dataState: "danger" },
          h("p", { class: "notice__title" }, "Tin nhắn đã đổi so với phiếu"),
          h(
            "p",
            null,
            "Bản nháp đã được sửa sau khi có người xin duyệt, nên chữ đang lưu không còn là chữ " +
              "mà phiếu này niêm phong. Màn hình không hiện bản mới để bạn khỏi đọc bản này mà " +
              "ký bản kia; máy chủ cũng từ chối duyệt. Từ chối phiếu này, rồi xin duyệt lại từ " +
              "bản nháp mới.",
          ),
          h(
            "p",
            { class: "hint" },
            "Phiên bản trong phiếu: ",
            h("span", { class: "mono" }, `v${String(item.resource_version)}`),
            " · phiên bản đang lưu: ",
            h("span", { class: "mono" }, `v${String(read.resource_version)}`),
          ),
        ),
        true,
      );
      return;
    }
    render(contentHost, messageContents(read));
    render(controlsHost, decisionControls(item, onDecided, verdict));
  } catch (error) {
    if (error && error.kind === "MISSING") {
      block(
        "Không bấm Duyệt được: bản nháp này không còn nội dung nào được phép gửi.",
        h(
          "div",
          { class: "notice", dataState: "danger" },
          h("p", { class: "notice__title" }, "Bản nháp không còn gửi được"),
          h(
            "p",
            null,
            "Người duyệt bản nháp đã từ chối nó sau khi phiếu này được mở, nên không có chữ nào " +
              "để cho phép gửi. Máy chủ cũng từ chối duyệt. Từ chối phiếu này để gỡ nó khỏi " +
              "hàng chờ.",
          ),
        ),
        true,
      );
      return;
    }
    block(
      "Không bấm được: chưa đọc được tin nhắn sẽ gửi. Chưa thấy chữ thì chưa quyết.",
      h(
        "div",
        { class: "stack stack--tight" },
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Chưa xem được tin nhắn sẽ gửi cho khách"),
          h(
            "p",
            null,
            "Phiếu này xin cho một tin nhắn được gửi tay tới khách. Chừng nào chưa đọc được đúng " +
              "chữ của tin đó thì nút Duyệt vẫn khoá. Máy chủ nêu lý do bên dưới, nguyên văn.",
          ),
        ),
        errorNotice(error),
      ),
    );
  }
}

/**
 * What one `APPROVE_REMEDY` envelope asks the owner to authorise, laid out as the server sent it.
 *
 * `REMEDY-OWNER-DECIDE-001`. Every figure is the server's and is only formatted here: the amount
 * the staff member proposed, the ceiling the server checked it against, and the staff limit of the
 * policy version it was checked under. Why the owner is needed is the list the domain recorded
 * with the proposal, glossed in the counter's words — never inferred from the amount, because a
 * 20.000 ₫ loss waits for the owner too. A missing list is said as missing, not guessed.
 *
 * The incident summary is what a staff member typed while a customer described the problem, so it
 * is untrusted text and goes through `h()` — text nodes, never markup — like a message draft.
 *
 * @param {any} read the `RemedyApprovalBindingResponse` body
 * @returns {HTMLElement}
 */
function remedyContents(read) {
  const reasons = Array.isArray(read.owner_reasons) ? read.owner_reasons : null;
  const ticket = ticketText(read);
  const service = read.service_code
    ? read.service_name
      ? `${read.service_name} (${read.service_code})`
      : String(read.service_code)
    : null;
  const summary = typeof read.incident_summary === "string" ? read.incident_summary : "";
  return h(
    "div",
    { class: "notice", dataState: "warn", dataRemedyBinding: String(read.resource_id || "") },
    h("p", { class: "notice__title" }, "Khoản bồi hoàn bạn đang được đề nghị duyệt"),
    facts([
      [
        "Loại",
        h(
          "span",
          { dataField: "remedy-kind" },
          `${REMEDY_KIND_LABEL[read.kind] || String(read.kind || UNKNOWN)} (${String(read.kind || UNKNOWN)})`,
        ),
        { span: true },
      ],
      ["Số tiền đề nghị", h("span", { dataField: "remedy-amount" }, money(read.amount_vnd))],
      ["Trần máy chủ đã kiểm", h("span", { dataField: "remedy-ceiling" }, money(read.ceiling_vnd))],
      [
        "Mức nhân viên được tự duyệt",
        h("span", { dataField: "remedy-staff-limit" }, money(read.staff_approval_ceiling_vnd)),
      ],
      [
        "Vì sao cần chủ tiệm",
        h(
          "span",
          { dataField: "remedy-why" },
          reasons && reasons.length
            ? `${ownerReasonText(reasons)}.`
            : "Máy chủ không có lý do đã ghi cho đề nghị này — hỏi người đề nghị trước khi quyết.",
        ),
        { span: true },
      ],
      [
        "Đơn",
        h(
          "span",
          { class: "row", dataField: "remedy-order" },
          ticket ? h("span", null, ticket) : h("span", null, "Đơn không gắn phiếu giấy"),
          h(
            "a",
            {
              href: `#/orders/${encodeURIComponent(String(read.order_id || ""))}`,
              title: String(read.order_id || ""),
            },
            `mở đơn ${shortId(read.order_id)}`,
          ),
        ),
        { span: true },
      ],
      [
        "Món",
        h(
          "span",
          { dataField: "remedy-item" },
          service ? `${service} · dòng ${String(read.order_line_id)}` : "Không ghi dòng nào",
          " · ",
          Number.isInteger(read.garment_index)
            ? `món thứ ${String(read.garment_index)}`
            : "cả dòng, không chọn món",
        ),
        { span: true },
      ],
      [
        "Khách phản ánh",
        summary
          ? h("span", { dataField: "remedy-summary" }, summary)
          : h(
              "span",
              { class: "hint", dataField: "remedy-summary" },
              "Mô tả sự cố không còn được lưu (đã quá hạn giữ) hoặc chưa từng được ghi.",
            ),
        { span: true },
      ],
      [
        "Người đề nghị",
        `${String(read.proposed_by_name || UNKNOWN)} · ${dateTime(read.proposed_at)}`,
        { span: true },
      ],
    ]),
    h(
      "p",
      { class: "hint" },
      "Bấm Duyệt là cho phép trả đúng số tiền này cho đúng món này. Duyệt chưa trả gì cả: một " +
        "nhân viên phải bấm “Thực hiện bồi hoàn” trên trang khiếu nại trước khi phiếu duyệt hết hạn.",
    ),
  );
}

/**
 * Fetch what one `APPROVE_REMEDY` envelope binds, and unblock its controls — or not.
 *
 * `REMEDY-OWNER-DECIDE-001`, built the way `loadMessageDraft` is and for the same reasons. The
 * store in the URL is the envelope's own, from the queue row, never the one selected in the top
 * bar; a row without one is refused. The read's proposal, approval, version and both digests are
 * compared with the envelope's, and the server's own `envelope_matches` must agree. Any mismatch
 * withholds the figures — the owner must not read today's order and sign the envelope's — and
 * leaves only "Từ chối", which authorises nothing and takes the dead envelope out of the queue.
 *
 * @param {any} item
 * @param {HTMLElement} contentHost
 * @param {HTMLElement} controlsHost
 * @param {(message: string) => Promise<void>} onDecided
 * @param {{allowed: boolean, reason: string}} verdict
 * @param {(detail: string) => void} [retitle] names the ticket on the card's title once matched
 * @returns {Promise<void>}
 */
async function loadRemedyProposal(item, contentHost, controlsHost, onDecided, verdict, retitle) {
  /** @param {string} reason @param {HTMLElement} explanation @param {boolean} [refuseOnly] */
  const block = (reason, explanation, refuseOnly = false) => {
    render(contentHost, explanation);
    render(controlsHost, decisionControls(item, onDecided, verdict, reason, { refuseOnly }));
  };
  const envelopeStore = typeof item.store_id === "string" ? item.store_id : "";
  if (!envelopeStore) {
    block(
      "Không bấm được: phiếu này không mang mã cửa hàng, nên không đọc được khoản bồi hoàn của nó.",
      h(
        "div",
        { class: "notice", dataState: "danger" },
        h("p", { class: "notice__title" }, "Không biết khoản bồi hoàn thuộc cửa hàng nào"),
        h("p", null, "Tải lại hàng chờ. Chưa đọc được số tiền thì chưa quyết."),
      ),
    );
    return;
  }
  const storePart = encodeURIComponent(envelopeStore);
  const proposalPart = encodeURIComponent(String(item.resource_id));
  try {
    const read = await request(
      `/internal/v1/stores/${storePart}/remedy-proposals/${proposalPart}/approval-binding`,
    );
    if (
      read.resource_id !== item.resource_id ||
      read.approval_id !== item.approval_request_id ||
      read.resource_version !== item.resource_version ||
      read.snapshot_hash !== item.snapshot_hash ||
      read.rendered_hash !== item.rendered_hash ||
      read.envelope_matches !== true
    ) {
      block(
        "Không bấm Duyệt được: đơn hoặc đề nghị bồi hoàn đã đổi sau khi phiếu này được mở.",
        h(
          "div",
          { class: "notice", dataState: "danger", dataRemedyStale: "true" },
          h("p", { class: "notice__title" }, "Khoản bồi hoàn đã đổi so với phiếu"),
          h(
            "p",
            null,
            "Bản giá của đơn hoặc chính đề nghị không còn là thứ mà phiếu này niêm phong, nên màn " +
              "hình không hiện số tiền để bạn khỏi đọc bản này mà ký bản kia; máy chủ cũng từ chối " +
              "duyệt. Từ chối phiếu này, rồi nhờ nhân viên đề nghị lại từ sự cố.",
          ),
        ),
        true,
      );
      return;
    }
    render(contentHost, remedyContents(read));
    retitle?.(read.ticket_number == null ? "" : `Phiếu ${read.ticket_number}`);
    render(controlsHost, decisionControls(item, onDecided, verdict));
  } catch (error) {
    if (error && error.kind === "MISSING") {
      block(
        "Không bấm Duyệt được: máy chủ không tìm thấy khoản bồi hoàn mà phiếu này nói tới.",
        h(
          "div",
          { class: "notice", dataState: "danger" },
          h("p", { class: "notice__title" }, "Không tìm thấy đề nghị bồi hoàn"),
          h(
            "p",
            null,
            "Không có đề nghị nào chờ chủ tiệm khớp với phiếu này ở cửa hàng của phiếu. Máy chủ " +
              "cũng từ chối duyệt. Từ chối phiếu này để gỡ nó khỏi hàng chờ.",
          ),
        ),
        true,
      );
      return;
    }
    block(
      "Không bấm được: chưa đọc được khoản bồi hoàn. Chưa thấy số tiền thì chưa quyết.",
      h(
        "div",
        { class: "stack stack--tight" },
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Chưa xem được khoản bồi hoàn cần duyệt"),
          h(
            "p",
            null,
            "Chừng nào chưa đọc được loại, số tiền, trần và món bị hỏng hay mất thì nút Duyệt vẫn " +
              "khoá. Máy chủ nêu lý do bên dưới, nguyên văn.",
          ),
        ),
        errorNotice(error),
      ),
    );
  }
}

/**
 * What one envelope asks, in the approver's words: the action's gloss ("Duyệt bồi hoàn",
 * "Cho gửi tin nhắn"), or the raw action token when the console has no gloss for it — an
 * unfamiliar word is a prompt to add one, a plausible guess would hide the gap.
 *
 * @param {any} item
 * @returns {string}
 */
function cardTitle(item) {
  return item.action ? enumVi(item.action) : enumVi(item.resource_type);
}

/**
 * One pending envelope, as a card: what is asked, how long is left, who may decide, the bound
 * content exactly as it will be signed, then Từ chối / Duyệt. Identifiers, versions and digests are
 * in the technical drawer at the bottom (tier 3).
 *
 * @param {any} item
 * @param {(host: HTMLElement, expiresAt: string) => void} registerClock
 * @param {(message: string) => Promise<void>} onDecided
 * @param {{allowed: boolean, reason: string}} verdict
 * @returns {HTMLElement}
 */
function approvalCard(item, registerClock, onDecided, verdict) {
  const clockHost = h("span", { class: "approval__clock" }, countdownPill(item.expires_at));
  registerClock(clockHost, item.expires_at);

  // The title gains a detail ("· Phiếu 17", "· ngày 24/09/2026") only from content that was read
  // and matched the envelope; a withheld card keeps the bare action.
  const detailHost = h("span", { class: "approval__detail" });
  /** @param {string} detail */
  const retitle = (detail) => render(detailHost, detail ? ` · ${detail}` : null);

  // Two hosts rather than one card built in one pass, because the amounts arrive after the card
  // does. The controls start blocked and are replaced only by the success branch below, so every
  // path that is not "the amounts are on screen" leaves the approve control shut — including a
  // request that never comes back.
  const contentHost = h("div", { class: "approval__content stack stack--tight" });
  const controlsHost = h("div", { class: "stack stack--tight" });
  if (String(item.action) === SET_RANGE_PRICE) {
    render(
      controlsHost,
      decisionControls(
        item,
        onDecided,
        verdict,
        "Không bấm được: đang tải số tiền được đề nghị. Chưa thấy số thì chưa quyết.",
      ),
    );
    render(contentHost, h("p", { class: "hint" }, "Đang tải số tiền được đề nghị…"));
    void loadProposedAmounts(item, contentHost, controlsHost, onDecided, verdict);
  } else if (String(item.resource_type) === EXPORT_REQUEST) {
    // Keyed on the resource type rather than the action, unlike the branch above: one action maps
    // to `EXPORT_REQUEST` and the type is what says there is a stored request to read. The
    // range-price branch has to key on the action because four actions share `QUOTE_REVISION`.
    render(
      controlsHost,
      decisionControls(
        item,
        onDecided,
        verdict,
        "Không bấm được: đang tải nội dung bản xuất. Chưa thấy dữ liệu thì chưa quyết.",
      ),
    );
    render(contentHost, h("p", { class: "hint" }, "Đang tải nội dung bản xuất…"));
    void loadExportRequest(item, contentHost, controlsHost, onDecided, verdict, retitle);
  } else if (String(item.resource_type) === MESSAGE_DRAFT) {
    // Keyed on the resource type, like the export branch: `SEND_MESSAGE` is the only action that
    // maps to it, and the type is what says there is a stored draft to read.
    render(
      controlsHost,
      decisionControls(
        item,
        onDecided,
        verdict,
        "Không bấm được: đang tải tin nhắn sẽ gửi. Chưa thấy chữ thì chưa quyết.",
      ),
    );
    render(contentHost, h("p", { class: "hint" }, "Đang tải tin nhắn sẽ gửi…"));
    void loadMessageDraft(item, contentHost, controlsHost, onDecided, verdict);
  } else if (String(item.resource_type) === REMEDY_PROPOSAL) {
    // `REMEDY-OWNER-DECIDE-001`. Keyed on the resource type like the two branches above:
    // `APPROVE_REMEDY` is the only action that maps to it.
    render(
      controlsHost,
      decisionControls(
        item,
        onDecided,
        verdict,
        "Không bấm được: đang tải khoản bồi hoàn. Chưa thấy số tiền thì chưa quyết.",
      ),
    );
    render(contentHost, h("p", { class: "hint" }, "Đang tải khoản bồi hoàn cần duyệt…"));
    void loadRemedyProposal(item, contentHost, controlsHost, onDecided, verdict, retitle);
  } else {
    render(controlsHost, decisionControls(item, onDecided, verdict));
  }

  return h(
    "article",
    { class: "card approval", dataApprovalId: String(item.approval_request_id || "") },
    h(
      "div",
      { class: "approval__head" },
      h(
        "h2",
        { class: "approval__title", title: String(item.action || "") },
        cardTitle(item),
        detailHost,
      ),
      clockHost,
    ),
    h(
      "p",
      { class: "approval__meta" },
      // The queue names the role that may decide, not the person who asked: no read returns the
      // requester for every type. Where the content read has a name (a remedy's proposer, a
      // range price's reviewer), it is in the content below.
      `Người quyết: ${enumVi(item.required_role)} · hết hạn ${dateTime(item.expires_at)}`,
    ),
    item.replayed
      ? h(
          "div",
          { class: "notice", dataState: "info" },
          "Lệnh này đã chạy trước đó — đây là bản ghi cũ hiện lại.",
        )
      : null,
    // Content above controls, always and on purpose: the thing being approved has to be readable
    // before the control that approves it is reachable, and on a narrow screen order is the only
    // thing that guarantees that.
    contentHost,
    controlsHost,
    techDetails([
      ["Mã phiếu duyệt", shortId(item.approval_request_id), { copy: item.approval_request_id }],
      // What is actually being approved, as the server names it. The action is beside the resource
      // type rather than instead of it: four actions share `QUOTE_REVISION`.
      ["Việc cần duyệt", item.action || UNKNOWN],
      ["Loại nội dung", item.resource_type || UNKNOWN],
      item.resource_id
        ? ["Mã nội dung", shortId(item.resource_id), { copy: item.resource_id }]
        : null,
      ["Phiên bản", item.resource_version == null ? UNKNOWN : `v${item.resource_version}`],
      ["Trạng thái", `${enumVi(item.status)} (${String(item.status || UNKNOWN)})`],
      ["Vai trò được quyết", String(item.required_role || UNKNOWN)],
      item.store_id ? ["Cửa hàng", shortId(item.store_id), { copy: item.store_id }] : null,
      ["Hết hạn lúc", dateTime(item.expires_at)],
      item.envelope_hash
        ? ["Mã niêm phong", item.envelope_hash, { copy: item.envelope_hash }]
        : null,
      item.snapshot_hash ? ["Mã ảnh chụp", item.snapshot_hash, { copy: item.snapshot_hash }] : null,
      item.rendered_hash
        ? ["Mã nội dung hiển thị", item.rendered_hash, { copy: item.rendered_hash }]
        : null,
    ]),
  );
}

/**
 * Fetch the amounts behind one `SET_RANGE_PRICE` envelope and unblock its controls — or not.
 *
 * Every failure path leaves the controls exactly as they started: blocked, with a reason. That is
 * the whole design. A card that cannot show the number must not offer a way to approve it, and the
 * safe direction under a network error, a 404, a stale row or a server refusal is identically "no
 * approve button" — there is no failure here whose right answer is to let the press through.
 *
 * The digests are compared before the amounts are shown. The server has already re-derived its own
 * and refused a mismatch; this second comparison is about a different pair — the queue row the
 * decision will be built from, and the proposal body just fetched. If the queue card went stale
 * while it was on screen, those two disagree, and the amounts on display would belong to a
 * different rendering than the one the press would hand back.
 *
 * @param {any} item
 * @param {HTMLElement} contentHost
 * @param {HTMLElement} controlsHost
 * @param {(message: string) => Promise<void>} onDecided
 * @param {{allowed: boolean, reason: string}} verdict
 * @returns {Promise<void>}
 */
async function loadProposedAmounts(item, contentHost, controlsHost, onDecided, verdict) {
  /** @param {string} reason @param {HTMLElement} explanation */
  const block = (reason, explanation) => {
    render(contentHost, explanation);
    render(controlsHost, decisionControls(item, onDecided, verdict, reason));
  };
  try {
    const proposal = await request(
      `/internal/v1/approvals/${encodeURIComponent(item.approval_request_id)}/range-price-proposal`,
    );
    const lines = Array.isArray(proposal.lines) ? proposal.lines : [];
    if (!lines.length) {
      block(
        "Không bấm được: máy chủ không trả về dòng tiền nào cho phiếu này.",
        h(
          "div",
          { class: "notice", dataState: "danger" },
          h("p", { class: "notice__title" }, "Không có số tiền nào để xem"),
          h(
            "p",
            null,
            "Phiếu này xin duyệt một mức giá trong khoảng, nhưng máy chủ không trả về con số nào. " +
              "Không duyệt. Báo kỹ thuật và để phiếu tự hết hạn.",
          ),
        ),
      );
      return;
    }
    if (proposal.rendered_hash !== item.rendered_hash) {
      block(
        "Không bấm được: số tiền đọc được không khớp với mã niêm phong của phiếu trong hàng chờ.",
        h(
          "div",
          { class: "notice", dataState: "danger" },
          h("p", { class: "notice__title" }, "Số tiền không khớp phiếu"),
          h(
            "p",
            null,
            "Số tiền máy chủ trả về thuộc về một bản nội dung khác với bản phiếu này đang niêm " +
              "phong. Nhiều khả năng hàng chờ đã cũ. Tải lại hàng chờ rồi đọc lại phiếu mới.",
          ),
        ),
      );
      return;
    }
    render(contentHost, proposedAmounts(proposal, lines));
    render(controlsHost, decisionControls(item, onDecided, verdict));
  } catch (error) {
    block(
      "Không bấm được: chưa đọc được số tiền được đề nghị. Chưa thấy số thì chưa quyết.",
      h(
        "div",
        { class: "stack stack--tight" },
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Chưa xem được số tiền được đề nghị"),
          h(
            "p",
            null,
            "Phiếu này xin duyệt một mức giá nhân viên đã chốt trong khoảng đã công bố. Màn hình " +
              "báo giá chỉ hiện khoảng, không hiện con số ấy, nên khi chưa đọc được số thì nút " +
              "Duyệt vẫn khoá. Máy chủ nêu lý do bên dưới, nguyên văn.",
          ),
        ),
        errorNotice(error),
      ),
    );
  }
}

/**
 * Everything this screen deliberately does not offer, folded behind the questions it answers —
 * one ⓘ on the page header (spec V2 §5.5), so the queue is the first thing under the title.
 *
 * Nothing here is deleted: the V1 "Giới hạn của màn hình này" panel moved whole into the sheet,
 * its guardrail sentence first and each standing notice with its exact text inside the `explain()`
 * that names what it explains. What stays visible on every card is what changes the press itself:
 * the refusal reason beside a shut control, the "Từ chối vẫn bấm được" line on a stale card, the
 * maker-checker line under live buttons, and each content block's "Bấm Duyệt là …" sentence.
 *
 * The notice with `id=approval-decision-blocked` is in this sheet: the disabled buttons point
 * their `aria-describedby` at it, and a closed `<dialog>` is still in the document, so a screen
 * reader hears the full reason with the control.
 *
 * @returns {HTMLElement}
 */
function limitsInfo() {
  // A keyed property on purpose: the sentence is the V1 panel's `guardrail`, and keeping the key
  // keeps its slot in the disclosure registry (`specs/contracts/console-disclosures-v1.yaml`).
  const limits = {
    guardrail:
      "Quyết định ở đây ghi thẳng vào máy chủ và không hoàn tác được. Trước khi ghi, máy chủ " +
      "kiểm lại phiên bản, cả hai mã niêm phong, quyền của bạn, và quy tắc người tạo yêu cầu " +
      "không được tự duyệt.",
  };
  return infoButton(
    "Giới hạn của màn hình này",
    h(
      "div",
      { class: "stack" },
      h("p", { class: "approval__limits-lead" }, limits.guardrail),
      explain(
        "Danh sách này có phải toàn bộ hàng chờ không?",
        h(
          "div",
          { class: "notice", dataState: "warn" },
          h("p", { class: "notice__title" }, "Hiện mọi phê duyệt của cửa hàng bạn thuộc về"),
          h(
            "p",
            null,
            "Máy chủ tìm hàng chờ theo cửa hàng ghi trên chính phiếu duyệt, nên mọi loại đều hiện " +
              "ở đây: đơn hàng, báo giá, tin nhắn soạn sẵn, khung giờ và phí giao đề xuất. Trước " +
              "ngày 31/08/2026 máy chủ tìm theo đơn hàng, nên toàn bộ hàng chờ gửi tin nhắn — " +
              "thứ mà chính những người phải xử lý nó cần thấy — không bao giờ hiện ra.",
          ),
          h(
            "p",
            { class: "hint" },
            "Chỉ hiện phê duyệt của cửa hàng bạn được phân công. Phiếu của cửa hàng khác không " +
              "hiện ở đây và cũng không duyệt được.",
          ),
        ),
        h(
          "div",
          { class: "notice", dataState: "info" },
          h("p", { class: "notice__title" }, "Chỉ hiện việc đang chờ quyết"),
          h(
            "p",
            null,
            "Việc đã duyệt, đã từ chối hoặc đã hết hạn không nằm trong danh sách này và không tra " +
              "cứu lại được từ đây.",
          ),
        ),
      ),
      explain(
        "Tại sao nút Duyệt đang tắt?",
        h(
          "div",
          { class: "notice", dataState: "warn", id: BLOCK_ID },
          h("p", { class: "notice__title" }, "Khoá theo loại nội dung, không phải theo vai trò"),
          h(
            "p",
            null,
            "Máy chủ đòi phiên bản và hai mã niêm phong để chứng minh bạn quyết đúng nội dung đó. " +
              "Hàng chờ nay trả về đủ cả ba, nên phiếu nào bảng vận hành mở ra xem được thì bấm " +
              "quyết được ngay — hôm nay là phiếu gắn với một đơn hàng, và phiếu chốt giá trong " +
              "khoảng của một bản báo giá.",
          ),
          h(
            "p",
            null,
            // DEC-029 (2026-09-25): the counter now attests its own range prices, so such an
            // envelope reaches this queue only if it was raised before the ruling or after a
            // reversal of it. The rendering rule below still holds for any that does.
            "Từ 25/09/2026 nhân viên trực quầy tự chốt giá trong khoảng, có ghi tên, không cần " +
              "chủ tiệm duyệt trước (DEC-029), nên phiếu SET_RANGE_PRICE thường không vào hàng " +
              "chờ này nữa. Nếu vẫn có một phiếu như vậy, nó còn một bước nữa. Bản báo giá mà " +
              "phiếu ấy niêm phong là " +
              "bản trước khi chốt giá, nên màn hình báo giá chỉ hiện khoảng đã công bố chứ không " +
              "hiện con số nhân viên đề nghị. Vì vậy thẻ phiếu tự đọc con số ấy và in ngay trên " +
              "hai nút; chừng nào chưa đọc được thì nút vẫn khoá. Duyệt một con số chưa ai cho " +
              "bạn xem cũng là duyệt mù, dù có đủ cả ba mã.",
          ),
          h(
            "p",
            null,
            "Phiếu xin xuất dữ liệu cũng vậy, và còn chặt hơn: thẻ phiếu tự đọc ngày làm việc, " +
              "danh sách cột và những phần cố ý không mang theo, rồi in ngay trên hai nút. Tệp " +
              "đã tải về thì không thu hồi được, nên chưa đọc được nội dung là chưa quyết.",
          ),
          h(
            "p",
            null,
            // MESSAGE-DRAFT-BINDING-001. This paragraph used to say the system stores no message
            // body and the server checks the content digest against nothing. Both were false by
            // then: the draft is stored, the server derives all three binding values from it, and
            // the card now reads the words through the route this item added.
            "Phiếu gửi tin nhắn (MESSAGE_DRAFT) cũng vậy: thẻ phiếu tự đọc đúng chữ của tin sẽ " +
              "gửi, đối chiếu phiên bản và hai mã niêm phong với phiếu, rồi in chữ đó ngay trên " +
              "hai nút. Nếu bản nháp bị sửa hoặc bị từ chối sau khi phiếu được mở, thẻ không hiện " +
              "chữ, nút Duyệt khoá và chỉ còn Từ chối — máy chủ cũng từ chối duyệt một phiếu như " +
              "vậy. Duyệt xong vẫn chưa có gì được gửi: một nhân viên phải tự gửi tay và ký tên.",
          ),
          h(
            "p",
            null,
            // REMEDY-OWNER-DECIDE-001. Before it, an APPROVE_REMEDY envelope reached this queue and
            // nobody could decide it here: the type had no entry in the viewable table.
            "Phiếu duyệt bồi hoàn (REMEDY_PROPOSAL) — mất đồ, đền trên đơn đã hoàn tiền, hoặc vượt " +
              "mức nhân viên được tự duyệt — cũng vậy: thẻ phiếu tự đọc loại, số tiền, trần máy " +
              "chủ đã kiểm, món nào, vì sao cần chủ tiệm và khách đã phản ánh gì, đối chiếu với " +
              "phiếu, rồi in ngay trên hai nút. Đơn hoặc đề nghị đổi sau khi phiếu được mở thì " +
              "thẻ không hiện số, nút Duyệt khoá và chỉ còn Từ chối. Duyệt xong vẫn chưa trả gì: " +
              "nhân viên bấm thực hiện ở màn hình Bồi hoàn.",
          ),
          h(
            "p",
            null,
            "Loại nào bảng vận hành chưa mở ra xem được thì nút vẫn khoá, và đó là cố ý — ví dụ " +
              "khung giờ hay phí giao đề xuất. Bấm duyệt một nội dung chưa ai đọc được chính là " +
              "duyệt mù, có đủ ba mã cũng không làm điều đó thành an toàn.",
          ),
          h("p", null, h("a", { href: "#/gaps" }, "Xem danh mục việc chưa hỗ trợ")),
        ),
      ),
      explain(
        "Vì sao không tạo được yêu cầu duyệt ở đây?",
        h(
          "div",
          { class: "notice", dataState: "info" },
          h("p", { class: "notice__title" }, "Tạo yêu cầu duyệt cũng không có ở đây"),
          h(
            "p",
            null,
            "Cùng một lý do: mở một yêu cầu duyệt cần đúng loại việc theo bảng của máy chủ, cộng " +
              "hai mã niêm phong và phiên bản chính sách đang áp dụng. Không giá trị nào trong số " +
              "đó nhân viên gõ tay ra được, nên màn hình không mời bạn thử.",
          ),
          h(
            "p",
            null,
            "Phiếu được mở ở nơi có sẵn nội dung máy chủ trả về: xin gửi một tin nhắn ở màn hình " +
              "Ngoại lệ, phần Gửi thủ công; xin xuất dữ liệu ở màn hình Xuất dữ liệu.",
          ),
        ),
      ),
      explain(
        "Phê duyệt còn những quy tắc nào khác?",
        h(
          "dl",
          { class: "fields" },
          h(
            "div",
            { class: "field field--span" },
            h("dt", null, "Tách vai người làm / người duyệt"),
            h(
              "dd",
              null,
              "Máy chủ từ chối quyết định đến từ chính nhân viên đã tạo yêu cầu. Danh sách này không " +
                "trả về ai là người yêu cầu, nên bạn chỉ biết mình vướng quy tắc đó khi máy chủ từ chối. " +
                "Riêng phiếu xin xuất dữ liệu thì thẻ phiếu nói trước: quy tắc ở đó tính theo người " +
                "đã tạo YÊU CẦU XUẤT — người chọn dữ liệu nào rời khỏi hệ thống — chứ không phải " +
                "người bấm mở phong bì duyệt, nên nếu yêu cầu xuất là của bạn thì nút khoá kèm lý do.",
            ),
          ),
          h(
            "div",
            { class: "field field--span" },
            h("dt", null, "Thời hạn quyết định"),
            h(
              "dd",
              null,
              "10, 15 hoặc 30 phút tuỳ hành động; riêng phiếu duyệt bồi hoàn mở tới hết ngày làm " +
                "việc hôm sau (giờ Việt Nam). Hết hạn thì không bao giờ được gia hạn ngầm: hết giờ " +
                "nghĩa là phải tạo lại yêu cầu mới, không phải xin thêm thời gian.",
            ),
          ),
          h(
            "div",
            { class: "field field--span" },
            h("dt", null, "Phạm vi cửa hàng"),
            h(
              "dd",
              null,
              "Hàng chờ này gồm mọi cửa hàng mà tài khoản của bạn được gán, không lọc theo cửa hàng " +
                "đang chọn ở thanh phía trên.",
            ),
          ),
        ),
      ),
    ),
  );
}

/**
 * One price a staff member chose inside a published band, as the owner reviews it (`DEC-029`).
 *
 * `DEC-029` gave the choice to the staff member on duty and kept this review as its control, so
 * the card leads with who chose, then the band and the number. Nothing is computed: the server
 * sends the band and the amount as integers of đồng and they are formatted here. A row whose
 * stored amounts no longer match what its envelope bound arrives with `withheld` and no lines;
 * it is shown as exactly that, never as a number and never left out.
 *
 * @param {any} item a `RangePriceReviewItemResponse`
 * @returns {HTMLElement}
 */
function rangeReviewCard(item) {
  if (item.withheld) {
    return h(
      "div",
      { class: "review" },
      h(
        "div",
        { class: "notice", dataState: "danger" },
        h("p", { class: "notice__title" }, "Không hiện số tiền của lần chốt giá này"),
        h(
          "p",
          null,
          "Số tiền đang lưu không khớp với nội dung phiếu đã ghi, nên màn hình không hiện con số " +
            "nào. Người chốt: ",
          h("strong", null, String(item.proposed_by_name)),
          ". Hãy xem lại cơ sở dữ liệu cùng người phụ trách kỹ thuật.",
        ),
      ),
    );
  }
  return h(
    "div",
    { class: "review" },
    h(
      "div",
      { class: "review__head" },
      h(
        "p",
        { class: "review__who" },
        h("strong", null, String(item.proposed_by_name)),
        h("span", { class: "review__when" }, dateTime(item.proposed_at)),
      ),
      statusPill({
        state: item.approval_status === "APPROVED" ? "ok" : "neutral",
        text: enumVi(item.approval_status),
        token: String(item.approval_status || ""),
      }),
    ),
    h(
      "ul",
      { class: "review__lines" },
      item.lines.map((/** @type {any} */ line) =>
        h(
          "li",
          { class: "review__line" },
          h(
            "span",
            { class: "review__service", title: String(line.service_code) },
            String(line.service_code),
          ),
          h(
            "span",
            { class: "review__amount" },
            h("strong", { class: "money" }, money(line.proposed_amount_vnd)),
            h(
              "span",
              { class: "review__band" },
              " trong khoảng ",
              moneyRange(line.band_minimum_vnd, line.band_maximum_vnd).text,
            ),
          ),
        ),
      ),
    ),
  );
}

/**
 * Today's prices chosen inside a band, for the owner's review (`DEC-029`) — the second tab.
 *
 * The ruling moved the choice to the counter on the strength of this review; before this panel
 * the only read was keyed by approval id and nothing could discover one, so the review the ruling
 * relies on could not actually be done. Today only, in the shop's time zone, as the server defines
 * it; the server refuses anyone who is not an approver with MFA, and says so.
 *
 * The one store-scoped read on this exempt screen (`STORE_GATE_EXEMPT`): it is built only with a
 * selected store, and without one the tab says so instead of calling.
 *
 * @param {(text: string) => void} onCount
 * @returns {HTMLElement}
 */
function reviewPane(onCount) {
  const store = storeId();
  const reviews = listView({
    limit: LIST_LIMIT,
    fetch: async () => {
      const body = await request(
        `/internal/v1/stores/${encodeURIComponent(String(store))}/range-price-reviews` +
          `?limit=${LIST_LIMIT}`,
      );
      return Array.isArray(body?.items) ? body.items : [];
    },
    renderItem: rangeReviewCard,
    emptyText:
      "Hôm nay chưa có món nào được chốt giá trong khoảng ở cửa hàng này. Danh sách này chỉ " +
      "gồm các lần nhân viên tự chốt giá theo DEC-029.",
    clearMetaOnError: true,
    onLoaded: (items) => onCount(count(items, LIST_LIMIT)),
    onError: () => onCount(UNKNOWN),
  });
  if (store) void reviews.reload();
  return h(
    "div",
    { class: "stack" },
    h(
      "div",
      { class: "approvals__pane-head" },
      h("h2", { class: "group__title" }, "Nhân viên tự chốt hôm nay"),
      infoButton(
        "Danh sách này là gì?",
        h(
          "p",
          { class: "hint" },
          "Theo DEC-029, nhân viên trực quầy tự chốt giá trong khoảng chủ tiệm đã niêm yết. Đây là " +
            "chỗ chủ tiệm xem lại: ai chốt, món gì, bao nhiêu, trong khoảng nào.",
        ),
      ),
    ),
    store
      ? h("div", { class: "stack" }, reviews.bar.node, reviews.truncation, reviews.host)
      : h("p", { class: "hint" }, "Chưa chọn cửa hàng nên chưa có gì để xem lại."),
  );
}

/**
 * A little event the shell listens for, so the Duyệt badge in the tab bar follows a decision now
 * rather than at the next minute's poll. It carries nothing; the shell re-reads the queue itself.
 */
function announceQueueChanged() {
  window.dispatchEvent(new CustomEvent("console:approvals-changed"));
}

export function render_() {
  /**
   * The live countdown pills, rebuilt every time the list reloads.
   *
   * Held as a list rather than read out of the DOM so that one interval — started once, in this
   * function — drives every pill no matter how many times the list is refreshed. The registry is
   * cleared around each reload (the queue's hooks below) and refilled as the new cards are built.
   *
   * @type {Array<{host: HTMLElement, expiresAt: string}>}
   */
  const clocks = [];

  // Read once per screen build rather than per card: the principal cannot change under a rendered
  // screen without the router replacing it, and `can()` is not free enough to run per row.
  const decideVerdict = can(principal(), "APPROVALS_DECIDE");

  /** The rows of the last successful read, so an empty queue can be drawn as an empty state. */
  let lastItems = /** @type {any[]} */ ([]);

  /**
   * The queue. One `listView` owns the fetch–truncate–skeleton cycle; what stays here is the clock
   * registry above. Server order is `ORDER BY expires_at, id` — the envelope dying soonest is
   * first — and it is not re-sorted here: the order is itself information about what to look at.
   */
  const queue = listView({
    limit: LIST_LIMIT,
    fetch: () => request(`/internal/v1/approvals?limit=${LIST_LIMIT}`),
    renderItem: (item) =>
      approvalCard(
        item,
        (host, expiresAt) => clocks.push({ host, expiresAt }),
        async (message) => {
          // Empty when a card asks only for a re-read after a failed decision -- nothing was
          // recorded that a toast could truthfully announce.
          if (message) toast(message);
          await reloadQueue();
          announceQueueChanged();
        },
        decideVerdict,
      ),
    // Replaced by the empty state below once a read succeeds; kept as the list's own fallback.
    emptyText: "Không có việc nào đang chờ bạn quyết định trong các cửa hàng bạn được gán.",
    clearMetaOnError: true,
    skeletonRows: 2,
    onLoadStart: () => {
      clocks.length = 0;
    },
    onLoaded: (items) => {
      clocks.length = 0;
      markUpdated(bar.stamp);
      lastItems = Array.isArray(items) ? items : [];
      tabs.setCount("queue", count(lastItems, LIST_LIMIT));
    },
    onError: () => {
      clocks.length = 0;
      tabs.setCount("queue", UNKNOWN);
    },
  });

  // The screen's own reload bar rather than the list's, so "Tải lại" also redraws the empty state.
  const bar = toolbar({ onReload: () => reloadQueue() });

  /** Reload, and draw an empty queue as a calm empty state rather than a bare line. */
  async function reloadQueue() {
    const ok = await queue.reload();
    if (ok && lastItems.length === 0) {
      render(
        queue.host,
        emptyState({
          icon: "check",
          title: "Không có việc nào chờ duyệt",
          // "gắn với đơn hàng" was true before migration 0034, when the queue joined through
          // `orders`. Since 0034 the server joins on the approval's own `store_id`, so every
          // resource type in the caller's stores appears; what the list leaves out (decided and
          // expired envelopes) is in the ⓘ on the title.
          body: "Trong mọi cửa hàng bạn được gán. Phiếu đã quyết hoặc đã hết hạn không hiện ở đây.",
        }),
      );
    }
  }

  const panes = {
    queue: h(
      "div",
      { class: "stack approvals__pane", dataPane: "queue" },
      bar.node,
      queue.truncation,
      queue.host,
    ),
    reviews: h(
      "div",
      { class: "approvals__pane", dataPane: "reviews", hidden: true },
      reviewPane((text) => tabs.setCount("reviews", text)),
    ),
  };

  const tabs = (() => {
    const control = segmented({
      label: "Chọn danh sách",
      value: "queue",
      options: [
        { value: "queue", label: "Chờ duyệt", count: "…" },
        { value: "reviews", label: "Giá trong khoảng", count: "…" },
      ],
      onChange: (value) => {
        panes.queue.hidden = value !== "queue";
        panes.reviews.hidden = value !== "reviews";
      },
    });
    return {
      node: control,
      /** @param {string} value @param {string} text */
      setCount(value, text) {
        const option = control.querySelector(`[data-value="${value}"] .segmented__count`);
        if (option) option.textContent = text;
      },
    };
  })();

  const root = h(
    "section",
    { class: "screen approvals" },
    page({ title: "Duyệt", info: limitsInfo() }),
    tabs.node,
    panes.queue,
    panes.reviews,
  );

  // One interval for the whole screen. It checks that the screen is still in the document before
  // doing any work and clears itself when it is not, so navigating away — or the router replacing
  // this screen on a session change — cannot leave a timer running against a detached tree.
  const timer = setInterval(() => {
    if (!root.isConnected) {
      clearInterval(timer);
      return;
    }
    for (const clock of clocks) render(clock.host, countdownPill(clock.expiresAt));
  }, TICK_MS);

  void reloadQueue();

  return root;
}

export const screen = {
  path: "/approvals",
  title: "Duyệt",
  capability: "APPROVALS_READ",
  render: render_,
};
