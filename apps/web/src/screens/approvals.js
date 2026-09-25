/**
 * Approvals: the envelopes waiting for a human, and an honest account of what may be decided here.
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
 *     type it cannot. `MESSAGE_DRAFT` is the one that matters: nothing stores the message body, and
 *     `rendered_hash` is explicitly not verified server-side, so there is nothing to show and
 *     nothing to check it against. That is fail-closed in the same direction the server chose.
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
 *   - **The server is the authority, not this list.** `_require_exact_binding` re-checks the
 *     version and both digests at decision time, `_authorize_decision` re-checks store membership,
 *     role, MFA and maker-checker separation. A stale card cannot approve anything: the decision is
 *     refused, which is why a `STALE` refusal here offers a reload rather than a retry.
 *   - **Only `REQUESTED` is listed.** The `WHERE s.status = 'REQUESTED'` clause means approved,
 *     rejected and expired envelopes are not in this list and cannot be reviewed from it.
 *   - **The countdown is the point.** Approval TTLs are 10, 15 or 30 minutes by action
 *     (`SECURITY_RELIABILITY_SPEC_V1.md:354`) and an expiry never extends implicitly, so remaining
 *     time matters far more than a wall-clock timestamp. One interval drives every badge and stops
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
  countdown,
  dateTime,
  money,
  moneyRange,
  shortHash,
  shortId,
} from "../core/format.js";
import { enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal } from "../core/session.js";
import {
  badge,
  errorNotice,
  explain,
  facts,
  gated,
  listView,
  panel,
  resultLine,
  setResult,
} from "../ui/components.js";

const LIST_LIMIT = 100;

/** One second. Fast enough that a badge flipping to expired is seen, slow enough to cost nothing. */
const TICK_MS = 1000;

/** The id the disabled decision controls point at, so the refusal is announced with the control. */
const BLOCK_ID = "approval-decision-blocked";

/**
 * The remaining-time badge for one envelope.
 *
 * Rebuilt whole on every tick rather than having its text patched, because the warn → danger flip
 * is carried by `data-state` and not by the words. A tick that updated only the text would leave an
 * expired envelope wearing the colour of a live one.
 *
 * @param {string|null|undefined} expiresAt
 * @returns {HTMLElement}
 */
function countdownBadge(expiresAt) {
  const left = countdown(expiresAt);
  return badge({
    token: left.text,
    gloss: left.expired
      ? "đã hết hạn — không tự gia hạn, phải tạo yêu cầu mới"
      : "thời gian còn lại trước khi hết hạn",
    state: left.expired ? "danger" : "warn",
  });
}

/**
 * Resource types whose content this console can put in front of an approver before they decide.
 *
 * The key is the server's `resource_type`; the value builds the route that shows it. A type absent
 * from this table is not decidable here, and the card says which type it is rather than a generic
 * refusal — "không xem được nội dung loại MESSAGE_DRAFT" tells an approver what to go and fix,
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
 * `MESSAGE_DRAFT` stays out, and stays out for a reason that has not changed: nothing stores the
 * message body, and `rendered_hash` is explicitly not verified server-side, so there is nothing to
 * show and nothing to check it against.
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
 * `MESSAGE_DRAFT` sees the content reason.
 *
 * @param {any} item
 * @param {(message: string) => Promise<void>} onDecided
 * @param {{allowed: boolean, reason: string}} verdict
 * @param {string|null} [contentBlock] why this card's content cannot be shown yet, if it cannot
 * @returns {HTMLElement}
 */
function decisionControls(item, onDecided, verdict, contentBlock = null) {
  const viewer = VIEWABLE_RESOURCES[String(item.resource_type)];
  const decidable = Boolean(viewer) && hasBinding(item) && !contentBlock;
  const host = resultLine();

  if (!decidable) {
    const control = (label) =>
      h(
        "button",
        { type: "button", disabled: true, "aria-disabled": "true", "aria-describedby": BLOCK_ID },
        label,
      );
    return h(
      "div",
      { class: "stack stack--tight" },
      h("div", { class: "form__actions" }, control("Duyệt"), control("Từ chối")),
      h(
        "p",
        { class: "hint" },
        !viewer
          ? `Không bấm được: bảng vận hành chưa mở được nội dung loại ${item.resource_type} để ` +
            "bạn xem trước khi quyết. Duyệt một nội dung chưa xem là duyệt mù. Xem “Tại sao nút " +
            "Duyệt đang tắt?” bên dưới."
          : // The content block is checked before the binding, because it is the more specific
            // answer: a `SET_RANGE_PRICE` card whose amounts have not arrived is blocked for that
            // reason and not for a missing hash it does in fact have.
            contentBlock ||
            "Không bấm được: phiếu này thiếu phiên bản hoặc mã niêm phong, nên không dựng được " +
              "một quyết định hợp lệ. Tải lại hàng chờ.",
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
      // Reported to the screen, not to this card. `onDecided()` reloads the queue and a decided
      // envelope is no longer `REQUESTED`, so the card this line lives in is removed a moment
      // later -- the operator would watch the row vanish with no statement that their decision
      // was recorded, which is the one thing they need to know.
      setResult(host, "ok", decision === "APPROVED" ? "Đã phê duyệt." : "Đã từ chối.");
      await onDecided(
        decision === "APPROVED"
          ? `Đã phê duyệt phiếu ${shortId(item.approval_request_id)}. Phiếu rời khỏi hàng chờ.`
          : `Đã từ chối phiếu ${shortId(item.approval_request_id)}. Phiếu rời khỏi hàng chờ.`,
      );
    } catch (error) {
      button.removeAttribute("aria-busy");
      button.disabled = false;
      sibling.disabled = false;
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

  const approve = h("button", { type: "button", dataRequiresNetwork: "true" }, "Duyệt");
  const reject = h("button", { type: "button", dataRequiresNetwork: "true" }, "Từ chối");
  approve.addEventListener("click", () => void send("APPROVED", approve, reject));
  reject.addEventListener("click", () => void send("REJECTED", reject, approve));

  // `null` is a legitimate answer here and means "the content is already on this card", which is
  // the shape an `EXPORT_REQUEST` takes: there is no screen that renders a stored export request,
  // so a link would send the approver somewhere that is not the document they are signing. The
  // sentence about the server re-checking still belongs on every card, so only the anchor is
  // conditional.
  const href = viewer(String(item.resource_id), item.resource_version);

  return h(
    "div",
    { class: "stack stack--tight" },
    h(
      "p",
      { class: "hint" },
      typeof href === "string"
        ? h("a", { href }, "Mở nội dung này trước khi quyết")
        : "Nội dung cần duyệt đã in ngay trên thẻ này",
      " — máy chủ kiểm lại phiên bản và cả hai mã niêm phong khi bạn bấm.",
    ),
    h("div", { class: "form__actions" }, gated(approve, verdict), gated(reject, verdict)),
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
 * @returns {Promise<void>}
 */
async function loadExportRequest(item, contentHost, controlsHost, onDecided, verdict) {
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
 * One pending envelope.
 *
 * @param {any} item
 * @param {(host: HTMLElement, expiresAt: string) => void} registerClock
 * @param {(message: string) => Promise<void>} onDecided
 * @param {{allowed: boolean, reason: string}} verdict
 * @returns {HTMLElement}
 */
function approvalCard(item, registerClock, onDecided, verdict) {
  const clockHost = h("span", { class: "row" }, countdownBadge(item.expires_at));
  registerClock(clockHost, item.expires_at);

  // Two hosts rather than one card built in one pass, because the amounts arrive after the card
  // does. The controls start blocked and are replaced only by the success branch below, so every
  // path that is not "the amounts are on screen" leaves the approve control shut — including a
  // request that never comes back.
  const contentHost = h("div", { class: "stack stack--tight" });
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
    void loadExportRequest(item, contentHost, controlsHost, onDecided, verdict);
  } else {
    render(controlsHost, decisionControls(item, onDecided, verdict));
  }

  return h(
    "article",
    { class: "card stack" },
    h(
      "div",
      { class: "spread" },
      h(
        "strong",
        { class: "mono", title: item.approval_request_id },
        shortId(item.approval_request_id),
      ),
      clockHost,
    ),
    facts([
      ["Trạng thái", enumVi(item.status)],
      ["Ai được quyết", enumVi(item.required_role), { span: true }],
      // What is actually being approved. Absent until the decision binding was projected, which is
      // why the queue read as a list of opaque envelope hashes rather than a list of decisions.
      // The action is beside the resource type rather than instead of it: four actions share
      // `QUOTE_REVISION`, so the type alone does not say what is being authorised.
      ["Việc cần duyệt", item.action || "—", { mono: true }],
      ["Loại nội dung", item.resource_type || "—", { mono: true }],
      [
        "Phiên bản",
        item.resource_version == null ? "—" : `v${item.resource_version}`,
        { mono: true },
      ],
      ["Hết hạn lúc", dateTime(item.expires_at)],
      [
        "Mã niêm phong",
        h("span", { title: item.envelope_hash || "" }, shortHash(item.envelope_hash)),
        { mono: true, span: true },
      ],
    ]),
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
 * Everything this screen deliberately does not offer, folded behind the questions it answers.
 *
 * The queue renders above this panel — the day's most time-critical list is the data, and the
 * education about the data comes after it (UX refactor spec WS2). Nothing here is deleted: each
 * standing notice keeps its exact text, moved into an `explain()` whose summary names what it
 * explains. The read-only guardrail stays visible because it states what the whole screen cannot
 * do, and the refusal reason for the disabled decision buttons stays visible under them.
 *
 * @returns {HTMLElement}
 */
function limitsPanel() {
  return panel({
    eyebrow: "Giới hạn",
    title: "Giới hạn của màn hình này",
    guardrail:
      "Quyết định ở đây ghi thẳng vào máy chủ và không hoàn tác được. Trước khi ghi, máy chủ " +
      "kiểm lại phiên bản, cả hai mã niêm phong, quyền của bạn, và quy tắc người tạo yêu cầu " +
      "không được tự duyệt.",
    children: h(
      "div",
      { class: "stack" },
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
            "Loại nào bảng vận hành chưa mở ra xem được thì nút vẫn khoá, và đó là cố ý. " +
              "MESSAGE_DRAFT là loại đáng nói nhất: hệ thống không lưu nội dung tin nhắn, và máy " +
              "chủ cũng không đối chiếu được mã niêm phong nội dung với bất cứ thứ gì. Bấm duyệt " +
              "một tin sắp gửi cho khách mà chưa ai đọc được nó chính là duyệt mù — có đủ ba mã " +
              "cũng không làm điều đó thành an toàn.",
          ),
          h(
            "p",
            null,
            h("a", { href: "#/gaps" }, "Xem danh mục việc chưa hỗ trợ"),
          ),
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
              "10, 15 hoặc 30 phút tuỳ hành động, và hết hạn thì không bao giờ được gia hạn ngầm. " +
                "Hết giờ nghĩa là phải tạo lại yêu cầu mới, không phải xin thêm thời gian.",
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
  });
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  /**
   * The live countdown badges, rebuilt every time the list reloads.
   *
   * Held as a list rather than read out of the DOM so that one interval — started once, in this
   * function — drives every badge no matter how many times the list is refreshed. The registry is
   * cleared around each reload (the queue's hooks below) and refilled as the new cards are built.
   *
   * @type {Array<{host: HTMLElement, expiresAt: string}>}
   */
  const clocks = [];

  // Read once per screen build rather than per card: the principal cannot change under a rendered
  // screen without the router replacing it, and `can()` is not free enough to run per row.
  const decideVerdict = can(principal(), "APPROVALS_DECIDE");

  // Lives above the queue, so a decision's confirmation outlives the card that carried the button.
  const decisionStatus = resultLine();

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
          // recorded that this line could truthfully announce.
          if (message) setResult(decisionStatus, "ok", message);
          await queue.reload();
        },
        decideVerdict,
      ),
    // "gắn với đơn hàng" was true before migration 0034, when the queue joined through `orders` and
    // could only ever show order-linked approvals. Since 0034 the server joins on the approval's
    // own `store_id`, so every resource type in the caller's stores appears -- and the limits panel
    // on this same screen already said so, which made the two halves contradict each other.
    emptyText:
      "Không có việc nào đang chờ bạn quyết định trong các cửa hàng bạn được gán. Đây không " +
      "phải bằng chứng là hàng chờ trống — xem bảng giới hạn bên dưới.",
    clearMetaOnError: true,
    onLoadStart: () => {
      clocks.length = 0;
    },
    onLoaded: () => {
      clocks.length = 0;
    },
    onError: () => {
      clocks.length = 0;
    },
  });

  const root = h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Cần bạn quyết"),
      h("h1", null, "Duyệt"),
      h(
        "p",
        { class: "screen__lede" },
        "Việc đang chờ duyệt và còn bao lâu nữa hết hạn. Quyết được ngay tại đây với loại nội " +
          "dung bảng vận hành mở ra xem được; loại nào chưa xem được thì nút vẫn khoá và phiếu " +
          "nói rõ đó là loại nào.",
      ),
    ),
    panel({
      eyebrow: "Đang chờ",
      title: "Việc chờ bạn quyết",
      count: queue.count,
      children: h(
        "div",
        { class: "stack" },
        queue.bar.node,
        decisionStatus,
        queue.truncation,
        queue.host,
      ),
    }),
    limitsPanel(),
  );

  // One interval for the whole screen. It checks that the screen is still in the document before
  // doing any work and clears itself when it is not, so navigating away — or the router replacing
  // this screen on a session change — cannot leave a timer running against a detached tree.
  const timer = setInterval(() => {
    if (!root.isConnected) {
      clearInterval(timer);
      return;
    }
    for (const clock of clocks) render(clock.host, countdownBadge(clock.expiresAt));
  }, TICK_MS);

  void queue.reload();

  return root;
}

export const screen = {
  path: "/approvals",
  title: "Duyệt",
  capability: "APPROVALS_READ",
  render: render_,
};
