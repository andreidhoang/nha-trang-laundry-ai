/**
 * Gửi tay: the stepper where a named human reads the exact words, asks for them to be approved,
 * locks the approved envelope, and attests the send — split out of `screens/exceptions` so each
 * half stays one readable unit. A panel, not a screen: only `exceptions.js` mounts it.
 *
 * Rebuilt on the V2 kit (`CONSOLE-REDESIGN-005`, spec V2 §5.7) as four numbered steps —
 * 1 Đọc tin sẽ gửi → 2 Xin duyệt → 3 Khoá phong bì → 4 Ghi nhận đã gửi — each carrying every value
 * the previous server response returned, so nothing is retyped within a session.
 *
 * Across sessions too (`MANUAL-SEND-RESUME`, spec V2 principle 2 — zero paste). The binding read
 * carries `send_progress`: the latest `SEND_MESSAGE` over this draft in this store, with the
 * version and digests it bound and, once locked, its envelope's id, state and row version. So a
 * person who reopens `#/exceptions?draft=<id>` after the approver decided on another device lands
 * on the right step with every value carried: waiting → step 2 says so; approved → one press locks;
 * locked by you → step 4; recorded → done; stale or lapsed → "Xin duyệt lại". The fields under
 * "Nhập mã thủ công" remain only as a fallback for an envelope other than the latest.
 *
 * Step 1 is `MESSAGE-DRAFT-BINDING-001`: it reads a draft's binding and prints the exact words the
 * envelope will bind. The draft is picked from the store's recently approved drafts, or arrives
 * from `#/shadow` as `?draft=`; step 2 raises the envelope from the values the server returned.
 * It does not approve and it does not send: a different person approves on `#/approvals`, and the
 * send is still this panel's human manual send.
 *
 * The rules that shape this panel are documented at the top of `exceptions.js` — nothing retries
 * a send, hashes are carried never typed, and MANUAL_SEND_RECORDED is not delivery. Every step is a
 * write gated on the MANUAL_SEND capability, and every refusal is the server's own. Nothing in this
 * module decides policy.
 *
 * `CONSENT-TRANSACTIONAL-001` (`DEC-033`) added the service-messaging card after step 1. A manual
 * send is a service (TRANSACTIONAL) message, and the server refuses it -- at steps 2, 3 and 4 --
 * when the customer wrote STOP on the channel, when an opt-out awaits review, when the owner has not
 * published the service-messaging policy, or when there is no basis (no recent message from the
 * customer, no open order). The card reads the contact's state from the server and says it in one
 * human line; an owner or approver releases a STOP in a sheet by picking one of the customer's own
 * later messages — by the time it arrived — from the server's list. Nothing here is typed, and
 * nothing here decides whether a message may go: the server does, and asks itself again at every
 * step.
 *
 * @module screens/manualSend
 */

import { Submission, isTruncated, request } from "../core/api.js";
import { consentRefusalText } from "../core/errors.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, UUID, dateTime, integer, shortHash, shortId } from "../core/format.js";
import { ENUM_GLOSS, enumLabel, enumVi } from "../core/i18n.js";
import {
  boundInput,
  errorNotice,
  gated,
  gatedFields,
  labelled,
  resultLine,
  revealError,
  setResult,
  skeleton,
} from "../ui/components.js";
import {
  button,
  infoButton,
  keyValues,
  linkButton,
  list,
  listRow,
  messageBubble,
  sheet,
  statusPill,
  stepCard,
  techDetails,
  toast,
} from "../ui/kit.js";

/** `ManualSendPrepareRequest` and `ManualSendAttestationRequest` both pin this exact shape. */
const JCS_HASH = /^JCS-SHA256-V1:[0-9a-f]{64}$/;

/** A positive integer, matched as text so no browser stepper decides the value. */
const POSITIVE_INT = /^[1-9][0-9]{0,8}$/;

/**
 * The only channel `manual_sends._channel` accepts. Anything else is
 * `409 manual-send channel is not configured`, so this is a readonly field rather than a text box
 * that lets an operator invent a channel the deployment does not have.
 */
const CHANNEL = "INTERNAL_TEST";

/** How many approved drafts step 1 offers to pick from. The route's default page. */
const PICK_LIMIT = 50;

/** `ServiceMessagingStateResponse.release_evidence` is "newest first, at most 20". */
const EVIDENCE_CAP = 20;

/** The state protocol a manual send walks, explained one tap away from the steps. */
const SEND_STEPS = [
  {
    token: "DRAFT",
    what: "Agent soạn bản nháp. Chưa có gì rời khỏi hệ thống.",
  },
  {
    token: "APPROVAL_REQUESTED",
    what:
      "Bước 2 đóng gói bản nháp thành phong bì và đưa đi duyệt. Sửa một ký tự là tạo bản nháp " +
      "mới và huỷ phê duyệt cũ.",
  },
  {
    token: "APPROVED_FOR_MANUAL_SEND",
    what:
      "Bước 3 khoá phong bì đã duyệt lại cho một người gửi tay. Từ lúc đó tiến trình gửi tự động " +
      "không còn được phép chạy lệnh gửi này.",
  },
  {
    token: "MANUAL_SEND_RECORDED",
    what:
      "Bước 4 ghi lời chứng thực của người đã gửi. Phong bì bị tiêu thụ và không dùng lại được.",
  },
];

/**
 * The `MANUAL_SEND_RECORDED` disclosure, rendered at the attest button and on its result.
 *
 * `SECURITY_RELIABILITY_SPEC_V1.md:405` separates three things a console habitually conflates: a
 * human's claim that they sent something, the provider's claim that it transmitted, and the
 * recipient's acknowledgement. Only the first exists here, and only the first may be said. The
 * title is the tier-1 fact and is always visible; the full sentence is one tap away.
 *
 * @returns {HTMLElement}
 */
function notDeliveredNotice() {
  return h(
    "div",
    { class: "notice", dataState: "warn", dataNotDelivered: "true" },
    h(
      "div",
      { class: "notice__row" },
      h("p", { class: "notice__title" }, "MANUAL_SEND_RECORDED không có nghĩa là khách đã nhận"),
      infoButton(
        "Ghi nhận đã gửi nghĩa là gì?",
        h(
          "p",
          null,
          "Trạng thái này chỉ nói rằng một người đã chứng thực chính họ gửi tin đó bằng tay. Nó không " +
            "phải bằng chứng nhà cung cấp đã chuyển tin và không phải xác nhận của khách. Không được " +
            "trả lời khách hay ghi vào đơn rằng tin đã tới nơi chỉ vì dòng này tồn tại.",
        ),
      ),
    ),
  );
}

/**
 * The hint that goes under every hash field under "Nhập mã thủ công".
 *
 * @returns {string}
 */
function hashHint() {
  return (
    "Dán từ phong bì đã duyệt, đừng gõ lại. Máy chủ so từng ký tự: một mã băm gần đúng là một lời " +
    "từ chối, không phải một cảnh báo."
  );
}

/**
 * Render a `ManualSendResponse` as the step's outcome.
 *
 * @param {any} result
 * @param {{title: string, attested: boolean}} spec
 * @returns {HTMLElement}
 */
function manualSendResult(result, spec) {
  return h(
    "div",
    { class: "outcome stack stack--tight", dataOutcome: String(result.status || "") },
    h(
      "div",
      { class: "outcome__head" },
      h("p", { class: "outcome__title" }, spec.title),
      statusPill({
        state: spec.attested ? "warn" : "ok",
        text: ENUM_GLOSS[result.status] ? enumVi(result.status) : "trạng thái lạ",
        token: String(result.status || UNKNOWN),
      }),
    ),
    result.replayed
      ? h(
          "div",
          { class: "notice", dataState: "info" },
          "Kết quả được phát lại: cùng khoá thao tác và cùng nội dung đã gửi trước đó. Không có " +
            "bản ghi mới nào được tạo.",
        )
      : null,
    spec.attested ? notDeliveredNotice() : null,
    techDetails([
      [
        "Mã phong bì",
        shortId(result.manual_send_envelope_id),
        { copy: String(result.manual_send_envelope_id || "") },
      ],
      ["Phê duyệt gốc", shortId(result.approval_request_id)],
      ["Trạng thái", enumLabel(result.status)],
      ["Ràng buộc người nhận", shortId(result.recipient_binding_id)],
      ["Phiên bản dòng phong bì", integer(result.row_version)],
      ["Mã băm nội dung", shortHash(result.rendered_hash)],
    ]),
  );
}

/**
 * A suppression state as the service-messaging read sends it. `NONE` is that read's own word for
 * "no row" -- nobody wrote STOP on this channel -- and is glossed here rather than in the shared
 * enum map, where `NONE` also names a remedy next step.
 *
 * @param {string|null|undefined} value
 * @returns {string}
 */
function suppressionLabel(value) {
  return value === "NONE" ? "chưa từng yêu cầu dừng (NONE)" : enumLabel(value);
}

/**
 * The value a `datetime-local` field shows for this instant, in this device's clock — what "Vừa
 * gửi xong" fills in. Built from the parts, because `toISOString` would write UTC into a field
 * the device reads as local time.
 *
 * @returns {string}
 */
function localNow() {
  const now = new Date();
  const two = (n) => String(n).padStart(2, "0");
  return (
    `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}` +
    `T${two(now.getHours())}:${two(now.getMinutes())}`
  );
}

/**
 * The manual-send stepper: read the words, raise the envelope, lock it, attest the send.
 *
 * The panel keeps its own drafts, submissions and result lines; nothing here is shared with the
 * unknown-sends queue it sits beside on the screen.
 *
 * @param {object} spec
 * @param {import("../core/rbac.js").Verdict} spec.sendVerdict
 * @param {import("../core/rbac.js").Verdict} [spec.releaseVerdict] SERVICE_MESSAGING_RELEASE
 * @param {import("../core/rbac.js").Verdict} [spec.approveVerdict] APPROVALS_DECIDE — whether
 *   "Đang chờ duyệt" offers the way to Duyệt
 * @param {string|null} [spec.store] the selected store; the draft read is scoped to it
 * @param {string} [spec.draftId] a draft to open step 1 on, from `#/exceptions?draft=<id>`
 * @returns {HTMLElement}
 */
export function manualSendPanel({
  sendVerdict,
  releaseVerdict = { allowed: false, reason: "" },
  approveVerdict = { allowed: false, reason: "" },
  store = null,
  draftId = "",
}) {
  const raiseSubmission = new Submission("manual-send-raise");
  const prepareSubmission = new Submission("manual-send-prepare");
  const attestSubmission = new Submission("manual-send-attest");

  /** @type {{draftId: string}} */
  const raise = { draftId: UUID.test(draftId) ? draftId : "" };
  /** The binding read that is on screen, and the only thing step 2 raises an envelope from. */
  let bound = /** @type {any} */ (null);
  /** The envelope step 2 raised in this session, if it did, and the draft it was raised over. */
  let raised = /** @type {any} */ (null);
  let raisedDraftId = "";
  /** The envelope step 3 locked in this session, if it did. */
  let locked = /** @type {any} */ (null);
  /** The attestation step 4 recorded in this session, if it did. */
  let recorded = /** @type {any} */ (null);
  /**
   * The latest `SEND_MESSAGE` over the draft on screen, as the server last said it
   * (`send_progress` on the binding read) or as this session's own writes moved it. The one thing
   * that says which step is next; null when nobody has asked yet.
   *
   * @type {{approvalId: string, status: string, expiresAt: string|null, pastExpiry: boolean,
   *   resourceVersion: number, snapshotHash: string, renderedHash: string,
   *   requestedByYou: boolean, envelopeId: string, envelopeStatus: string,
   *   envelopeRowVersion: number|null, preparedByYou: boolean}|null}
   */
  let progress = null;
  /**
   * What the contact's service-messaging read last said (`DEC-033`): the server's own prediction
   * of whether steps 2 and 3 would be refused. `decision` is empty while unknown -- loading, or the
   * read failed -- and then nothing is predicted: the server decides on the press, as always.
   */
  const service = { loading: false, decision: "", reasonCode: "" };

  const raiseResult = resultLine();
  const raiseErrorHost = h("div");
  const askResult = resultLine();
  const askErrorHost = h("div");

  // No recipient field. API-INTEGRITY-002: the server reads the recipient off the approved draft
  // and refuses a request that names one, so a box here could only ever be ignored or refused.
  /** @type {{approvalId: string, resourceVersion: string, snapshotHash: string, renderedHash: string}} */
  const prepare = {
    approvalId: "",
    resourceVersion: "",
    snapshotHash: "",
    renderedHash: "",
  };

  /** @type {{envelopeId: string, resourceVersion: string, renderedHash: string, rowVersion: string, sentAt: string}} */
  const attest = {
    envelopeId: "",
    resourceVersion: "",
    renderedHash: "",
    rowVersion: "",
    sentAt: "",
  };

  const prepareResult = resultLine();
  const prepareErrorHost = h("div");
  const prepareOutcomeHost = h("div", { class: "stack" });

  const attestResult = resultLine();
  const attestErrorHost = h("div");
  const attestOutcomeHost = h("div", { class: "stack" });

  // The service-messaging card (`DEC-033`). Hidden until a contact is known -- from the step 1
  // read, or from a refusal that names the contact it was for. The result line and error host
  // live outside the card, which is rebuilt on every read, so a release's confirmation survives
  // the re-read that follows it.
  const releaseSubmission = new Submission("service-release");
  const serviceCardHost = h("div", { class: "stack" });
  const serviceResult = resultLine();
  serviceResult.id = "service-release-result";
  const serviceErrorHost = h("div");
  const serviceHost = h(
    "div",
    { class: "consent surface stack", id: "manual-service-messaging" },
    serviceCardHost,
    serviceResult,
    serviceErrorHost,
  );
  serviceHost.hidden = true;
  /** @type {{storeId: string, contactBindingId: string, channel: string}|null} */
  let serviceSubject = null;
  /** @type {{evidenceId: string}} */
  const release = { evidenceId: "" };
  /** The release sheet on screen, so a successful release can close it. */
  let releaseSheet = /** @type {{close: () => void}|null} */ (null);

  /** The approved drafts step 1 offers, read once from the store's review log. */
  let pickable = /** @type {any[]|null} */ (null);
  let pickTruncated = false;
  let pickFailure = /** @type {any} */ (null);
  /** A binding read is in flight. */
  let reading = false;

  const stepsHost = h("div", { class: "steps" });

  /** @param {string} value @returns {string} */
  const asHash = (value) => value.trim().replace(/\s+/g, "");

  /** @returns {string} */
  function validatePrepare() {
    if (!prepare.approvalId) {
      return "Chưa có phiếu duyệt để khoá: làm bước 2 rồi chờ người duyệt quyết.";
    }
    if (!UUID.test(prepare.approvalId)) return "Mã phê duyệt phải là một UUID.";
    if (!POSITIVE_INT.test(prepare.resourceVersion)) {
      return "Phiên bản tài nguyên phải là số nguyên từ 1 trở lên.";
    }
    if (!JCS_HASH.test(prepare.snapshotHash)) {
      return "Mã băm ảnh chụp phải đúng dạng JCS-SHA256-V1: theo sau là 64 ký tự hex thường.";
    }
    if (!JCS_HASH.test(prepare.renderedHash)) {
      return "Mã băm nội dung phải đúng dạng JCS-SHA256-V1: theo sau là 64 ký tự hex thường.";
    }
    return "";
  }

  /** @returns {string} */
  function validateAttest() {
    if (!attest.envelopeId) {
      return "Chưa có phong bì để ghi nhận: khoá phong bì ở bước 3 trước.";
    }
    if (!UUID.test(attest.envelopeId)) return "Mã phong bì gửi tay phải là một UUID.";
    if (!POSITIVE_INT.test(attest.resourceVersion)) {
      return "Phiên bản tài nguyên phải là số nguyên từ 1 trở lên.";
    }
    if (!JCS_HASH.test(attest.renderedHash)) {
      return "Mã băm chính xác phải đúng dạng JCS-SHA256-V1: theo sau là 64 ký tự hex thường.";
    }
    if (!POSITIVE_INT.test(attest.rowVersion)) {
      return "Phiên bản dòng phong bì phải là số nguyên từ 1 trở lên; ngay sau khi khoá nó là 1.";
    }
    if (!attest.sentAt) return "Chưa nhập thời điểm đã gửi.";
    const parsed = new Date(attest.sentAt);
    if (Number.isNaN(parsed.getTime())) return "Không đọc được thời điểm đã gửi.";
    if (parsed.getTime() > Date.now()) {
      return "Thời điểm đã gửi nằm ở tương lai. Máy chủ từ chối, không làm tròn.";
    }
    return "";
  }

  /* ---- where the send stands (MANUAL-SEND-RESUME) ------------------------------------------ */

  /**
   * `MessageDraftSendProgressResponse` as this panel keeps it. Anything malformed is "no
   * progress": the panel then offers step 2, and the server refuses a duplicate it would not want.
   *
   * @param {any} body
   */
  function progressFrom(body) {
    if (!body || !UUID.test(String(body.approval_request_id || ""))) return null;
    const envelopeId = UUID.test(String(body.envelope_id || "")) ? String(body.envelope_id) : "";
    return {
      approvalId: String(body.approval_request_id),
      status: String(body.status || ""),
      expiresAt: body.expires_at ? String(body.expires_at) : null,
      pastExpiry: body.past_expiry === true,
      resourceVersion: Number(body.resource_version),
      snapshotHash: String(body.snapshot_hash || ""),
      renderedHash: String(body.rendered_hash || ""),
      requestedByYou: body.requested_by_you === true,
      envelopeId,
      envelopeStatus: envelopeId ? String(body.envelope_status || "") : "",
      envelopeRowVersion: envelopeId ? Number(body.envelope_row_version) : null,
      preparedByYou: envelopeId ? body.prepared_by_you === true : false,
    };
  }

  /**
   * Which step is next, from the read on screen and the latest send over it. A reading of stored
   * values, not a decision: every write re-checks all of it under its own lock.
   *
   * @returns {"none"|"waiting"|"approved"|"locked"|"lockedByOther"|"recorded"|"stale"|"lapsed"|"rejected"|"other"}
   */
  function phase() {
    if (!bound || !progress) return "none";
    if (progress.envelopeStatus === "MANUAL_SEND_RECORDED") return "recorded";
    // The draft moved past what was approved (a reviewer's edit): nothing bound to the old words
    // may go out, and a new approval is the only way forward.
    if (progress.resourceVersion !== Number(bound.resource_version)) return "stale";
    if (progress.envelopeStatus === "APPROVED_FOR_MANUAL_SEND") {
      if (progress.pastExpiry) return "lapsed";
      return progress.preparedByYou ? "locked" : "lockedByOther";
    }
    if (progress.status === "REQUESTED") return progress.pastExpiry ? "lapsed" : "waiting";
    if (progress.status === "APPROVED") return progress.pastExpiry ? "lapsed" : "approved";
    if (progress.status === "REJECTED") return "rejected";
    if (progress.status === "EXPIRED" || progress.status === "CANCELLED") return "lapsed";
    return "other";
  }

  /**
   * Carry the latest send's values into the lock and the attestation, exactly as stored. Runs
   * after a server answer, never on a keystroke, so it never overwrites what someone is typing
   * under "Nhập mã thủ công".
   */
  function adoptProgress() {
    const now = phase();
    if (now === "stale" || now === "lapsed" || now === "rejected") {
      // Values bound to an approval that can no longer be spent would only be refused.
      for (const key of Object.keys(prepare)) prepare[key] = "";
      for (const key of ["envelopeId", "resourceVersion", "renderedHash", "rowVersion"]) {
        attest[key] = "";
      }
      return;
    }
    if (progress && (now === "waiting" || now === "approved")) {
      prepare.approvalId = progress.approvalId;
      prepare.resourceVersion = String(progress.resourceVersion);
      prepare.snapshotHash = progress.snapshotHash;
      prepare.renderedHash = progress.renderedHash;
      prepareSubmission.reset();
    }
    if (progress && now === "locked") {
      attest.envelopeId = progress.envelopeId;
      attest.resourceVersion = String(progress.resourceVersion);
      attest.renderedHash = progress.renderedHash;
      attest.rowVersion =
        progress.envelopeRowVersion && progress.envelopeRowVersion > 0
          ? String(progress.envelopeRowVersion)
          : "";
      attestSubmission.reset();
    }
  }

  /**
   * Steps 2 and 3 as the server's own consent read predicts them: while that read is out, and
   * when it says the send is not allowed, the control is shut with the reason under it. A
   * prediction, never a rule -- the server re-checks on every press -- and the reason names the
   * card rather than repeating its sentence, so no refusal is said twice on one screen.
   *
   * @param {string} doing what the control would do, for the reason line
   * @returns {import("../core/rbac.js").Verdict}
   */
  function consentVerdict(doing) {
    if (!sendVerdict.allowed) return sendVerdict;
    if (service.loading) {
      return { allowed: false, reason: "Đang kiểm tra khách này có nhận tin dịch vụ không…" };
    }
    if (service.decision && service.decision !== "ALLOW") {
      return {
        allowed: false,
        reason: `Chưa ${doing} được: khách này đang không nhận tin dịch vụ — xem thẻ phía trên.`,
      };
    }
    return sendVerdict;
  }

  /**
   * `gated()` with the consent prediction. A control shut by it says so on the element, and its
   * reason is styled as the warning it is rather than as a hint.
   *
   * @param {HTMLElement} control
   * @param {string} doing
   * @returns {HTMLElement}
   */
  function consentGated(control, doing) {
    const verdict = consentVerdict(doing);
    const wrapped = gated(control, verdict);
    if (!verdict.allowed && sendVerdict.allowed) {
      control.setAttribute(
        "data-consent-blocked",
        service.loading ? "LOADING" : service.reasonCode || service.decision,
      );
      wrapped.lastElementChild?.classList.add("step__blocked");
    }
    return wrapped;
  }

  /* ---- step 1: read the words ------------------------------------------------------------- */

  /**
   * Read what an envelope over the chosen draft would bind. A pure read.
   *
   * @param {Event} [event]
   */
  async function readBinding(event) {
    event?.preventDefault();
    // Whatever was on screen belonged to the previous read. An envelope must never be raised from
    // words that are not the ones currently displayed, so the old read goes before the new one.
    bound = null;
    // Re-reading the same draft keeps the envelope already raised over it; another draft does not,
    // and neither does anything this session locked or recorded for the previous one.
    if (raisedDraftId !== raise.draftId) {
      raised = null;
      locked = null;
      recorded = null;
      render(prepareOutcomeHost);
      render(attestOutcomeHost);
    }
    progress = null;
    render(raiseErrorHost);
    render(askErrorHost);
    setResult(askResult, null, null);
    if (!store) {
      setResult(raiseResult, "danger", "Chưa chọn cửa hàng nên chưa đọc được bản nháp nào.");
      redraw();
      return;
    }
    if (!UUID.test(raise.draftId)) {
      setResult(raiseResult, "danger", "Mã bản nháp (lượt chạy agent) phải là một UUID.");
      redraw();
      return;
    }
    setResult(raiseResult, "warn", "Đang đọc tin nhắn sẽ gửi…");
    reading = true;
    redraw();
    const storePart = encodeURIComponent(store);
    const draftPart = encodeURIComponent(raise.draftId);
    try {
      const read = await request(
        `/internal/v1/stores/${storePart}/message-drafts/${draftPart}/binding`,
      );
      bound = read;
      reading = false;
      raiseSubmission.reset();
      // Where the send over this draft stands, from the same read: the step to land on, and every
      // value it needs, with nothing pasted (`MANUAL-SEND-RESUME`).
      progress = progressFrom(read.send_progress);
      adoptProgress();
      setResult(
        raiseResult,
        "ok",
        progress
          ? "Đã đọc tin và tình trạng phiếu gửi mới nhất; chưa có gì được ghi."
          : "Đã đọc. Đọc kỹ đúng những chữ bên dưới trước khi xin duyệt; chưa có gì được ghi.",
      );
      service.loading = true;
      redraw();
      // Before anything is asked of it: can this customer be sent a service message at all?
      void loadServiceMessaging({
        storeId: String(read.store_id || store),
        contactBindingId: String(read.recipient_binding_id || ""),
        channel: CHANNEL,
      });
    } catch (error) {
      setResult(
        raiseResult,
        error.kind === "MISSING" || error.kind === "DENIED" ? "warn" : "danger",
        error.kind === "MISSING"
          ? "Không có tin nào gửi được từ bản nháp này: nó không thuộc cửa hàng đang chọn, hoặc " +
              "người duyệt bản nháp đã từ chối nó."
          : "Không đọc được bản nháp. Không có gì được ghi.",
      );
      reading = false;
      render(raiseErrorHost, errorNotice(error));
      redraw();
      revealError(raiseErrorHost);
      // Arrived with a draft that cannot be sent: offer the ones that can.
      if (pickable === null) void loadPickable();
    }
  }

  /** The store's recently approved or rewritten drafts, to pick from instead of pasting an id. */
  async function loadPickable() {
    if (!store || !sendVerdict.allowed) return;
    try {
      const body = await request(
        `/internal/v1/stores/${encodeURIComponent(store)}/shadow/reviews?limit=${PICK_LIMIT}`,
      );
      const items = Array.isArray(body) ? body : [];
      pickTruncated = isTruncated(items, PICK_LIMIT);
      pickable = items.filter((item) => item.decision === "APPROVE" || item.decision === "EDIT");
      pickFailure = null;
    } catch (error) {
      pickable = [];
      pickFailure = error;
    }
    if (!bound) redraw();
  }

  /**
   * The words an envelope over this draft binds. Untrusted text — a customer's conversation shaped
   * it and a model or a reviewer wrote it — so it is a bubble of text nodes: markup in a draft is
   * shown as characters and never parsed. The recipient is the opaque contact binding, shortened,
   * in the technical drawer; no phone number or chat id exists on a draft to show.
   *
   * @param {any} read the `MessageDraftBindingResponse` body
   * @returns {HTMLElement}
   */
  function boundMessage(read) {
    const raw = typeof read.text === "string" ? read.text : "";
    return h(
      "div",
      { class: "stack stack--tight" },
      h("p", { class: "label" }, "Đúng những chữ sẽ được xin duyệt và gửi"),
      messageBubble({
        text: raw,
        side: "out",
        marker: h("p", { class: "eyebrow" }, "Nội dung sẽ gửi · Văn bản không tin cậy"),
        meta: `${integer(raw.length)} ký tự · phiên bản v${String(read.resource_version)}`,
        emptyText: `${UNKNOWN} bản nháp không có chữ nào`,
        data: { messageBody: "true" },
      }),
    );
  }

  /**
   * The bound message's identifiers, for the technical drawer.
   *
   * @param {any} read
   * @returns {HTMLElement}
   */
  function boundDetails(read) {
    return techDetails([
        ["Người nhận (mã ràng buộc)", shortId(read.recipient_binding_id)],
        ["Bản nháp", shortId(read.resource_id)],
        ["Phiên bản bản nháp", `v${String(read.resource_version)}`],
        ["Mã băm nội dung", shortHash(read.rendered_hash)],
        ["Mã băm ảnh chụp", shortHash(read.snapshot_hash)],
    ]);
  }

  /** @returns {HTMLElement} */
  function stepRead() {
    const manualForm = h(
      "form",
      { class: "form", onSubmit: (event) => void readBinding(event) },
      labelled({
        id: "manual-draft-id",
        label: "Mã bản nháp (lượt chạy agent)",
        hint:
          "Chỉ cần khi bản nháp không có trong danh sách. Máy chủ trả về đúng chữ đang lưu — của " +
          "agent, hoặc bản người duyệt đã sửa — cùng phiên bản và hai mã niêm phong.",
        control: boundInput({
          target: raise,
          key: "draftId",
          pattern: UUID,
          placeholder: "00000000-0000-0000-0000-000000000000",
          submission: raiseSubmission,
        }),
      }),
      gated(
        button({
          type: "submit",
          label: "Đọc tin nhắn sẽ gửi",
          network: true,
        }),
        sendVerdict,
      ),
    );

    if (bound) {
      return stepCard({
        number: 1,
        title: "Đọc tin sẽ gửi",
        state: "done",
        info: infoButton(
          "Tin này gửi cho ai?",
          h(
            "p",
            { class: "hint" },
            "Người nhận do máy chủ lấy từ chính bản nháp; không ai chọn lại được. Sửa bản nháp sau khi " +
              "xin duyệt là huỷ phiếu này — phải xin duyệt lại.",
          ),
        ),
        children: [
          boundMessage(bound),
          raiseResult,
          raiseErrorHost,
          h(
            "div",
            { class: "step__actions" },
            gated(
              button({
                label: "Đọc lại tin nhắn",
                variant: "quiet",
                icon: "refresh",
                network: true,
                onClick: () => void readBinding(),
              }),
              sendVerdict,
            ),
          ),
          boundDetails(bound),
        ],
      });
    }

    const loading = reading;
    const picker =
      pickable === null
        ? store && sendVerdict.allowed
          ? skeleton(2)
          : null
        : pickable.length
          ? h(
              "div",
              { class: "stack stack--tight" },
              list(
                pickable.map((item) => {
                  const words =
                    typeof item.edited_text === "string" && item.edited_text
                      ? item.edited_text
                      : item.draft_text;
                  return listRow({
                    leading: "message",
                    title: typeof words === "string" && words ? words : UNKNOWN,
                    meta: `${enumVi(item.decision)} lúc ${dateTime(item.decided_at)}`,
                    disabled: !sendVerdict.allowed,
                    reason: sendVerdict.reason,
                    data: { pickDraft: String(item.agent_run_id || "") },
                    onClick: () => {
                      raise.draftId = String(item.agent_run_id || "");
                      raiseSubmission.reset();
                      void readBinding();
                    },
                  });
                }),
                { label: "Bản nháp đã duyệt" },
              ),
              pickTruncated
                ? h(
                    "p",
                    { class: "hint" },
                    `Chỉ xét ${PICK_LIMIT} quyết định gần nhất; bản nháp cũ hơn thì nhập mã thủ công.`,
                  )
                : null,
            )
          : pickFailure
            ? errorNotice(pickFailure, { onRetry: () => void loadPickable() })
            : h(
                "p",
                { class: "empty-line" },
                "Chưa có bản nháp nào được duyệt để gửi. Duyệt ở màn hình Bản nháp AI trước.",
              );

    return stepCard({
      number: 1,
      title: "Đọc tin sẽ gửi",
      state: "current",
      summary: loading ? null : "Chọn bản nháp đã duyệt để xem đúng những chữ sẽ gửi.",
      children: [
        raiseResult,
        raiseErrorHost,
        loading ? skeleton(2) : picker,
        h(
          "details",
          { class: "manual-entry" },
          h("summary", null, "Nhập mã thủ công"),
          gatedFields(manualForm, sendVerdict),
        ),
      ],
    });
  }

  /* ---- step 2: ask for approval ------------------------------------------------------------ */

  /** Raise the `SEND_MESSAGE` envelope from the read on screen. */
  async function raiseEnvelope() {
    const read = bound;
    if (!read) return;
    setResult(askResult, "warn", "Đang tạo phiếu xin duyệt gửi…");
    render(askErrorHost);
    try {
      const approval = await request("/internal/v1/approvals", {
        method: "POST",
        // Every value is the server's, copied from the read. A console that derived its own
        // digests would be asking an approver to sign a document the server never computed.
        body: {
          store_id: read.store_id,
          action: read.action,
          resource_type: read.resource_type,
          resource_id: read.resource_id,
          resource_version: read.resource_version,
          snapshot_hash: read.snapshot_hash,
          rendered_hash: read.rendered_hash,
          policy_version: read.policy_version,
        },
        idempotencyKey: raiseSubmission.key(),
      });
      raiseSubmission.reset();
      raised = approval;
      raisedDraftId = raise.draftId;
      setResult(
        askResult,
        "ok",
        "Đã tạo phiếu xin duyệt. Một người duyệt khác — không phải bạn — quyết ở màn hình Duyệt. " +
          "Chưa có gì được gửi.",
      );
      toast("Đã tạo phiếu xin duyệt gửi");
      // Carried into step 3, which needs exactly these four. The approver still has to decide
      // first: step 3 is refused until the envelope is APPROVED, and that refusal is correct.
      // The previous session's lock and record belonged to the approval this one replaces.
      locked = null;
      recorded = null;
      render(prepareOutcomeHost);
      render(attestOutcomeHost);
      progress = progressFrom({
        approval_request_id: approval.approval_request_id,
        status: approval.status || "REQUESTED",
        expires_at: approval.expires_at,
        past_expiry: false,
        resource_version: read.resource_version,
        snapshot_hash: read.snapshot_hash,
        rendered_hash: read.rendered_hash,
        requested_by_you: true,
      });
      prepare.approvalId = String(approval.approval_request_id || "");
      prepare.resourceVersion = String(read.resource_version);
      prepare.snapshotHash = String(read.snapshot_hash);
      prepare.renderedHash = String(read.rendered_hash);
      prepareSubmission.reset();
      redraw();
    } catch (error) {
      if (showConsentRefusal(error, askResult, askErrorHost, "Chưa xin duyệt được.")) return;
      setResult(
        askResult,
        error.kind === "DENIED" ? "warn" : "danger",
        error.kind === "DENIED"
          ? "Máy chủ từ chối: phiên này không được xin duyệt gửi tin cho cửa hàng này. Không có " +
              "gì được ghi."
          : "Không tạo được phiếu xin duyệt. Không có gì được ghi.",
      );
      render(askErrorHost, errorNotice(error));
      revealError(askErrorHost);
    }
  }

  /** @returns {HTMLElement} */
  function stepAsk() {
    const info = infoButton(
      "Xin duyệt khác duyệt thế nào?",
      h(
        "p",
        { class: "hint" },
        "Xin duyệt không phải là duyệt, và duyệt không phải là gửi. Máy chủ từ chối người đã xin " +
          "tự duyệt phiếu của mình; tin chỉ rời khỏi hệ thống khi một người tự gửi tay và ký tên " +
          "ở bước 4.",
      ),
    );
    const now = phase();
    const raiseButton = (label) =>
      h(
        "div",
        { class: "step__actions" },
        consentGated(
          button({
            label,
            variant: "primary",
            network: true,
            block: true,
            data: { raiseEnvelope: "true" },
            onClick: () => void raiseEnvelope(),
          }),
          "xin duyệt",
        ),
      );
    const approvalFacts = () =>
      keyValues([
        [
          "Phiếu xin duyệt",
          h("span", { class: "mono" }, shortId(progress?.approvalId || raised?.approval_request_id)),
        ],
        ["Hết hạn lúc", dateTime(progress?.expiresAt || raised?.expires_at)],
      ]);

    if (!bound) {
      return stepCard({
        number: 2,
        title: "Xin duyệt",
        state: "todo",
        info,
        summary: "Đọc tin ở bước 1 trước.",
      });
    }

    if (now === "waiting") {
      // Somebody else decides, on Duyệt. Who may decide is offered the way there; the person who
      // asked is told it is not them -- the server refuses a self-decision.
      const mayDecide = approveVerdict.allowed && !progress?.requestedByYou;
      return stepCard({
        number: 2,
        id: "manual-step-2",
        title: "Xin duyệt",
        state: "current",
        info,
        summary: h(
          "span",
          { dataSendPhase: "waiting" },
          statusPill({ state: "warn", text: "Đang chờ duyệt", token: "REQUESTED" }),
        ),
        children: [
          askResult,
          approvalFacts(),
          progress?.requestedByYou && !raised
            ? h(
                "p",
                { class: "hint step__fact" },
                "Bạn đã xin duyệt. Một người duyệt khác — không phải bạn — quyết ở màn hình Duyệt.",
              )
            : null,
          h(
            "div",
            { class: "step__actions" },
            mayDecide
              ? linkButton({ href: "#/approvals", label: "Mở màn hình Duyệt", variant: "secondary" })
              : null,
            button({
              label: "Kiểm tra lại",
              variant: "quiet",
              icon: "refresh",
              network: true,
              data: { refreshProgress: "true" },
              onClick: () => void readBinding(),
            }),
          ),
          askErrorHost,
        ],
      });
    }

    if (now === "stale" || now === "lapsed" || now === "rejected") {
      const why =
        now === "stale"
          ? `Tin đã đổi sau khi xin duyệt: phiếu duyệt bản v${String(progress?.resourceVersion)}, ` +
            `tin giờ là v${String(bound.resource_version)}. Phải xin duyệt lại đúng tin mới.`
          : now === "lapsed"
            ? "Phiếu xin duyệt trước đã hết hạn. Phải xin duyệt lại."
            : "Người duyệt đã từ chối phiếu trước. Muốn gửi thì phải xin duyệt lại.";
      return stepCard({
        number: 2,
        id: "manual-step-2",
        title: "Xin duyệt",
        state: "current",
        info,
        summary: null,
        children: [
          h(
            "div",
            { class: "notice", dataState: "warn", dataSendPhase: now },
            h("p", { class: "notice__title" }, why),
          ),
          raiseButton("Xin duyệt lại"),
          askResult,
          askErrorHost,
        ],
      });
    }

    if (now === "other") {
      return stepCard({
        number: 2,
        id: "manual-step-2",
        title: "Xin duyệt",
        state: "current",
        info,
        children: [
          h(
            "div",
            { class: "notice", dataState: "warn", dataSendPhase: "other" },
            h(
              "p",
              { class: "notice__title" },
              `Phiếu gửi mới nhất đang ở trạng thái “${enumVi(progress?.status)}”, không gửi tay ` +
                "tiếp được từ đây.",
            ),
          ),
          approvalFacts(),
        ],
      });
    }

    if (now !== "none") {
      // Approved, locked or recorded: this step is behind us.
      return stepCard({
        number: 2,
        title: "Xin duyệt",
        state: "done",
        info,
        summary: "Đã được duyệt",
        children: [approvalFacts()],
      });
    }

    return stepCard({
      number: 2,
      id: "manual-step-2",
      title: "Xin duyệt",
      state: "current",
      info,
      children: [
        h(
          "p",
          { class: "hint step__fact" },
          "Một người khác sẽ duyệt đúng những chữ trên. Chưa có gì được gửi.",
        ),
        raiseButton("Xin duyệt gửi đúng tin này"),
        askResult,
        askErrorHost,
      ],
    });
  }

  /* ---- step 3: lock the envelope ----------------------------------------------------------- */

  async function submitPrepare(event) {
    event.preventDefault();
    const problem = validatePrepare();
    if (problem) {
      setResult(prepareResult, "danger", problem);
      return;
    }

    setResult(prepareResult, "warn", "Đang khoá phong bì…");
    render(prepareErrorHost);
    // The previous outcome goes too. A card describing a phong bì locked a minute ago, sitting
    // under a refusal for the one just attempted, is exactly the misread this console exists to
    // prevent.
    render(prepareOutcomeHost);

    try {
      const result = await request(
        `/internal/v1/approvals/${encodeURIComponent(prepare.approvalId)}/manual-send`,
        {
          method: "POST",
          body: {
            observed_resource_version: Number.parseInt(prepare.resourceVersion, 10),
            observed_snapshot_hash: prepare.snapshotHash,
            observed_rendered_hash: prepare.renderedHash,
            channel: CHANNEL,
          },
          idempotencyKey: prepareSubmission.key(),
        },
      );
      prepareSubmission.reset();
      locked = result;
      // Confirmed exactly once, here. Nothing repeats it.
      setResult(
        prepareResult,
        "ok",
        "Đã khoá phong bì. Worker gửi tự động không còn chạy được lệnh gửi này — giờ bạn tự gửi tin.",
      );
      render(
        prepareOutcomeHost,
        manualSendResult(result, { title: "Phong bì đã khoá", attested: false }),
      );

      // Carry the values step 4 needs. An operator retyping a 64-character digest by hand is a
      // refusal waiting to happen, and the server is the only source of truth for all three.
      attest.envelopeId = String(result.manual_send_envelope_id || "");
      attest.renderedHash = String(result.rendered_hash || "");
      attest.rowVersion = integer(result.row_version) === UNKNOWN ? "" : String(result.row_version);
      attest.resourceVersion = prepare.resourceVersion;
      attestSubmission.reset();
      // And where the send now stands: locked by you, which is what a re-read would say too.
      const approvalId = String(result.approval_request_id || prepare.approvalId);
      const base =
        progress && progress.approvalId === approvalId
          ? progress
          : {
              approvalId,
              status: "APPROVED",
              expiresAt: null,
              pastExpiry: false,
              resourceVersion: Number.parseInt(prepare.resourceVersion, 10),
              snapshotHash: prepare.snapshotHash,
              renderedHash: prepare.renderedHash,
              requestedByYou: false,
            };
      progress = {
        ...base,
        status: "APPROVED",
        envelopeId: attest.envelopeId,
        envelopeStatus: String(result.status || "APPROVED_FOR_MANUAL_SEND"),
        envelopeRowVersion: Number(result.row_version) || null,
        preparedByYou: true,
      };
      redraw();
      // The next thing to do is step 4, below the fold on a phone: bring it up.
      stepsHost
        .querySelector("#manual-step-4")
        ?.scrollIntoView({ block: "start", behavior: "smooth" });
    } catch (error) {
      if (showConsentRefusal(error, prepareResult, prepareErrorHost, "Chưa khoá được phong bì.")) {
        return;
      }
      setResult(
        prepareResult,
        error.kind === "DENIED" || error.kind === "REQUIRE_HUMAN" ? "warn" : "danger",
        error.kind === "DENIED"
          ? "Máy chủ từ chối: phiên này không được khoá phong bì gửi tay. Không có gì được ghi."
          : "Không khoá được phong bì. Không có gì được ghi.",
      );
      render(prepareErrorHost, errorNotice(error));
      revealError(prepareErrorHost);
    }
  }

  /** @returns {HTMLElement} */
  function stepLock() {
    const now = phase();
    // Locked already -- by you in this session, or as the server says -- leaves nothing to press.
    const done = Boolean(locked) || now === "locked" || now === "lockedByOther" || now === "recorded";
    // The lock is offered outside "Nhập mã thủ công" only for the latest approval, while it is
    // still waiting (one press once the approver has decided; the server refuses it before) or
    // approved. Everything else keeps the fallback fields and nothing more.
    const ready = !done && (now === "waiting" || now === "approved") && Boolean(prepare.approvalId);
    const channelField = h("input", {
      type: "text",
      value: CHANNEL,
      readonly: true,
      "aria-readonly": "true",
      dataFormat: "id",
    });

    const manual = h(
      "details",
      { class: "manual-entry" },
      h("summary", null, "Nhập mã thủ công"),
      h(
        "div",
        { class: "stack" },
        // This said the four values had to be asked of whoever raised the approval, because the
        // queue lists only REQUESTED rows and `prepare` takes only APPROVED ones. MANUAL-SEND-RESUME
        // made that false: the binding read carries the latest approval's id, version and digests,
        // so these boxes fill themselves. They stay for an envelope other than the latest.
        h(
          "p",
          { class: "hint" },
          "Mở bản nháp là bốn ô này tự điền từ phiếu xin duyệt mới nhất của nó. Chỉ nhập tay khi " +
            "cần khoá một phiếu khác phiếu đó.",
        ),
        labelled({
          id: "manual-approval-id",
          label: "Mã yêu cầu duyệt (approval_id)",
          hint:
            "Phê duyệt phải đang ở APPROVED, hành động SEND_MESSAGE và chưa hết hạn. Máy chủ tự đặt " +
            "mục đích TRANSACTIONAL và giai đoạn SHADOW; không có ô nào đổi hai giá trị đó.",
          control: boundInput({
            target: prepare,
            key: "approvalId",
            pattern: UUID,
            placeholder: "00000000-0000-0000-0000-000000000000",
            submission: prepareSubmission,
          }),
        }),
        labelled({
          id: "manual-resource-version",
          label: "Phiên bản tài nguyên đã quan sát",
          hint:
            "Số nguyên từ 1 trở lên, đúng bằng phiên bản mà phê duyệt đã ràng buộc. Lệch một đơn vị " +
            "là 409 nội dung đã cũ, và đó là điều đúng đắn.",
          control: boundInput({
            target: prepare,
            key: "resourceVersion",
            pattern: POSITIVE_INT,
            placeholder: "1",
            submission: prepareSubmission,
          }),
        }),
        labelled({
          id: "manual-snapshot-hash",
          label: "Mã băm ảnh chụp đã quan sát",
          hint: hashHint(),
          control: boundInput({
            target: prepare,
            key: "snapshotHash",
            pattern: JCS_HASH,
            placeholder: "JCS-SHA256-V1:0000…",
            submission: prepareSubmission,
            normalize: asHash,
          }),
        }),
        labelled({
          id: "manual-rendered-hash",
          label: "Mã băm nội dung đã quan sát",
          hint: hashHint(),
          control: boundInput({
            target: prepare,
            key: "renderedHash",
            pattern: JCS_HASH,
            placeholder: "JCS-SHA256-V1:0000…",
            submission: prepareSubmission,
            normalize: asHash,
          }),
        }),
        labelled({
          id: "manual-channel",
          label: "Kênh",
          hint:
            "Cố định. Triển khai này chưa cấu hình kênh thật nào, nên máy chủ chỉ nhận INTERNAL_TEST " +
            "và từ chối mọi giá trị khác. Không có tin nào tới khách qua đường này.",
          control: channelField,
        }),
      ),
    );

    // The lock itself. Once locked there is nothing left to press here; before anything was
    // carried in, it waits inside "Nhập mã thủ công" with the fields it would need.
    const lockButton = button({
      type: "submit",
      label: "Khoá phong bì cho người gửi tay",
      variant: "primary",
      network: true,
      block: true,
      data: { lockEnvelope: "true" },
    });
    const main = done
      ? []
      : [
          ready
            ? keyValues([
                ["Phiếu xin duyệt", h("span", { class: "mono" }, shortId(prepare.approvalId))],
                ["Phiên bản tin", `v${prepare.resourceVersion || UNKNOWN}`],
              ])
            : null,
          h(
            "p",
            { class: "hint step__fact" },
            "Chỉ khoá khi chính bạn sẽ gửi tin này — khoá xong máy không tự gửi tin đó nữa.",
          ),
          h(
            "div",
            { class: "step__actions" },
            // For the latest approval, the consent read predicts the lock too; a lock typed in by
            // hand is the server's to judge alone.
            ready ? consentGated(lockButton, "khoá phong bì") : gated(lockButton, sendVerdict),
          ),
        ];
    const results = [prepareResult, prepareErrorHost, prepareOutcomeHost];
    if (!ready && !done) manual.lastElementChild?.append(...main.filter(Boolean));
    const form = h(
      "form",
      { class: "form", onSubmit: submitPrepare },
      ready ? [main, results, manual] : done ? results : [manual, results],
    );

    return stepCard({
      number: 3,
      id: "manual-step-3",
      title: "Khoá phong bì",
      state: done ? "done" : ready ? "current" : "todo",
      info: infoButton(
        "Khoá phong bì là gì?",
        h(
          "p",
          { class: "hint" },
          "Bước 3 và bước 4 là cách duy nhất một tin đã duyệt rời khỏi hệ thống hôm nay, và cả " +
            "hai đều ghi tên bạn. Khoá phong bì xong thì tiến trình gửi tự động không còn được phép " +
            "chạy lệnh gửi đó nữa, nên đừng khoá một phong bì bạn không định tự gửi.",
        ),
        h(
          "p",
          { class: "hint" },
          "Không có ô người nhận. Máy chủ lấy người nhận từ chính bản nháp đã được duyệt và trả nó " +
            "về trong kết quả; một yêu cầu tự ghi người nhận bị từ chối.",
        ),
      ),
      summary:
        now === "lockedByOther"
          ? "Người khác đã khoá phong bì này để tự gửi"
          : now === "recorded"
            ? "Đã khoá"
            : done
              ? "Đã khoá · giờ bạn tự gửi tin"
              : now === "waiting"
                ? "Khi người duyệt đã duyệt phiếu, bấm khoá. Máy chủ từ chối nếu chưa duyệt."
                : now === "approved"
                  ? h(
                      "span",
                      { dataSendPhase: "approved" },
                      statusPill({ state: "ok", text: "Đã được duyệt", token: "APPROVED" }),
                    )
                  : "Chờ phiếu ở bước 2 được duyệt.",
      children: gatedFields(form, sendVerdict),
    });
  }

  /* ---- step 4: record the send ------------------------------------------------------------- */

  async function submitAttest(event) {
    event.preventDefault();
    const problem = validateAttest();
    if (problem) {
      setResult(attestResult, "danger", problem);
      return;
    }

    setResult(attestResult, "warn", "Đang ghi lời chứng thực…");
    render(attestErrorHost);
    // Same rule, and it matters more here: a stale MANUAL_SEND_RECORDED card left beside a failed
    // attempt reads as "the message went out" when nothing went out.
    render(attestOutcomeHost);

    try {
      const result = await request(
        `/internal/v1/manual-sends/${encodeURIComponent(attest.envelopeId)}/attest`,
        {
          method: "POST",
          body: {
            observed_resource_version: Number.parseInt(attest.resourceVersion, 10),
            exact_rendered_hash: attest.renderedHash,
            // `datetime-local` has no offset. `Date` reads it in this device's timezone and
            // `toISOString` writes it back with one, which is what the server requires: a naive
            // timestamp is refused outright.
            sent_at: new Date(attest.sentAt).toISOString(),
          },
          idempotencyKey: attestSubmission.key(),
          ifMatch: Number.parseInt(attest.rowVersion, 10),
        },
      );
      attestSubmission.reset();
      recorded = result;
      setResult(
        attestResult,
        "ok",
        "Đã ghi lời chứng thực gửi tay dưới tên bạn. Đây là lời của người gửi, không phải xác " +
          "nhận đã tới khách.",
      );
      render(
        attestOutcomeHost,
        manualSendResult(result, { title: "Đã ghi nhận gửi tay", attested: true }),
      );
      if (progress && progress.envelopeId === attest.envelopeId) {
        progress = {
          ...progress,
          envelopeStatus: String(result.status || "MANUAL_SEND_RECORDED"),
          envelopeRowVersion: Number(result.row_version) || progress.envelopeRowVersion,
        };
      }
      redraw();
    } catch (error) {
      if (
        showConsentRefusal(
          error,
          attestResult,
          attestErrorHost,
          "Lời chứng thực không được ghi.",
          "Nếu bạn đã lỡ gửi tin này sau khi khách yêu cầu dừng, báo chủ tiệm ngay.",
        )
      ) {
        return;
      }
      setResult(
        attestResult,
        error.kind === "DENIED" || error.kind === "REQUIRE_HUMAN" ? "warn" : "danger",
        error.kind === "DENIED"
          ? "Máy chủ từ chối: phiên này không được chứng thực gửi tay. Không có gì được ghi."
          : "Không ghi được lời chứng thực. Không có gì được ghi.",
      );
      render(attestErrorHost, errorNotice(error));
      revealError(attestErrorHost);
    }
  }

  /** @returns {HTMLElement} */
  function stepRecord() {
    const now = phase();
    const done = Boolean(recorded) || now === "recorded";
    // Ready for an envelope locked by you -- in this session or, as the server says, in another;
    // or, before any read, for one entered under "Nhập mã thủ công".
    const ready = !done && Boolean(attest.envelopeId) && (now === "locked" || now === "none");
    const sentAtEcho = h("p", { class: "hint" });

    const updateEcho = () => {
      if (!attest.sentAt) {
        sentAtEcho.textContent = "";
        return;
      }
      const parsed = new Date(attest.sentAt);
      if (Number.isNaN(parsed.getTime())) {
        sentAtEcho.textContent = "Không đọc được thời điểm này.";
        return;
      }
      const iso = parsed.toISOString();
      sentAtEcho.textContent =
        parsed.getTime() > Date.now()
          ? "Thời điểm này ở tương lai và máy chủ sẽ từ chối."
          : `Giờ Việt Nam ${dateTime(iso)}. Hãy đối chiếu với đồng hồ trước khi ghi.`;
    };

    const sentAtInput = /** @type {HTMLInputElement} */ (
      h("input", {
        type: "datetime-local",
        value: attest.sentAt,
        onInput: (event) => {
          attest.sentAt = event.target.value;
          attestSubmission.reset();
          updateEcho();
        },
      })
    );
    updateEcho();

    const manual = h(
      "details",
      { class: "manual-entry" },
      h("summary", null, "Nhập mã thủ công"),
      h(
        "div",
        { class: "stack" },
        attest.envelopeId && locked
          ? h(
              "div",
              { class: "notice", dataState: "info" },
              "Các ô này đã được điền từ kết quả khoá phong bì ở bước 3. Không sửa chúng bằng tay.",
            )
          : attest.envelopeId && now === "locked"
            ? h(
                "div",
                { class: "notice", dataState: "info" },
                "Các ô này đã được điền từ phong bì bạn đã khoá, theo máy chủ. Không sửa chúng " +
                  "bằng tay.",
              )
            : null,
        labelled({
          id: "attest-envelope-id",
          label: "Mã phong bì gửi tay (manual_send_envelope_id)",
          // Said "no route reads envelopes back" until MANUAL-SEND-RESUME: the binding read now
          // carries the latest approval's envelope, so the box fills itself for the one who locked.
          hint:
            "Bước 3 trả về giá trị này; mở lại bản nháp thì nó tự điền nếu chính bạn đã khoá phong " +
            "bì của phiếu mới nhất. Chỉ dán tay cho một phong bì khác.",
          control: boundInput({
            target: attest,
            key: "envelopeId",
            pattern: UUID,
            placeholder: "00000000-0000-0000-0000-000000000000",
            submission: attestSubmission,
          }),
        }),
        labelled({
          id: "attest-resource-version",
          label: "Phiên bản tài nguyên đã quan sát",
          hint: "Đúng bằng giá trị đã dùng ở bước 3. Máy chủ so lại với phiên bản lưu trong phong bì.",
          control: boundInput({
            target: attest,
            key: "resourceVersion",
            pattern: POSITIVE_INT,
            placeholder: "1",
            submission: attestSubmission,
          }),
        }),
        labelled({
          id: "attest-rendered-hash",
          label: "Mã băm nội dung chính xác",
          hint: hashHint(),
          control: boundInput({
            target: attest,
            key: "renderedHash",
            pattern: JCS_HASH,
            placeholder: "JCS-SHA256-V1:0000…",
            submission: attestSubmission,
            normalize: asHash,
          }),
        }),
        labelled({
          id: "attest-row-version",
          label: "Phiên bản dòng phong bì (If-Match)",
          hint:
            "Gửi trong header If-Match. Ngay sau khi khoá giá trị này là 1. Thiếu nó máy chủ trả 428 " +
            "và không ghi gì.",
          control: boundInput({
            target: attest,
            key: "rowVersion",
            pattern: POSITIVE_INT,
            placeholder: "1",
            submission: attestSubmission,
          }),
        }),
      ),
    );

    const main = done
      ? []
      : [
          ready
            ? keyValues([["Phong bì", h("span", { class: "mono" }, shortId(attest.envelopeId))]])
            : null,
          h(
            "div",
            null,
            labelled({
              id: "attest-sent-at",
              label: "Đã gửi lúc",
              hint:
                "Theo đồng hồ của máy này. Không được ở tương lai so với đồng hồ máy chủ; nếu hai đồng " +
                "hồ lệch, hãy lùi lại vài phút.",
              control: h(
                "div",
                { class: "sent-at" },
                sentAtInput,
                button({
                  label: "Vừa gửi xong",
                  variant: "quiet",
                  onClick: () => {
                    attest.sentAt = localNow();
                    sentAtInput.value = attest.sentAt;
                    attestSubmission.reset();
                    updateEcho();
                  },
                }),
              ),
            }),
            sentAtEcho,
          ),
          notDeliveredNotice(),
          h(
            "div",
            { class: "step__actions" },
            gated(
              button({
                type: "submit",
                label: "Chứng thực rằng tôi đã gửi tin này",
                variant: "danger",
                network: true,
                block: true,
              }),
              sendVerdict,
            ),
          ),
        ];
    const results = [attestResult, attestErrorHost, attestOutcomeHost];
    // Recorded before this session opened: the record is the server's, and what it does not mean
    // is said here exactly as it is under a record made now.
    const recordedEarlier =
      done && !recorded
        ? h(
            "div",
            { class: "stack stack--tight", dataSendPhase: "recorded" },
            keyValues([
              [
                "Phong bì",
                h("span", { class: "mono" }, shortId(progress?.envelopeId || "")),
              ],
              ["Trạng thái", enumVi("MANUAL_SEND_RECORDED")],
            ]),
            notDeliveredNotice(),
          )
        : null;
    if (!ready && !done) manual.lastElementChild?.append(...main.filter(Boolean));
    const form = h(
      "form",
      { class: "form", onSubmit: submitAttest },
      ready ? [main, results, manual] : done ? [recordedEarlier, results] : [manual, results],
    );

    return stepCard({
      number: 4,
      id: "manual-step-4",
      title: "Ghi nhận đã gửi",
      state: done ? "done" : ready ? "current" : "todo",
      info: infoButton(
        "Bốn bước gửi tay là gì?",
        h(
          "ol",
          { class: "stack stack--tight" },
          SEND_STEPS.map((step) =>
            h(
              "li",
              null,
              h("strong", null, enumVi(step.token)),
              h("span", { class: "mono" }, ` (${step.token})`),
              " — ",
              step.what,
            ),
          ),
        ),
      ),
      summary: recorded
        ? "Đã ghi lời chứng thực của bạn"
        : done
          ? "Tin này đã được ghi nhận gửi tay"
          : ready
            ? "Gửi tin bằng tay, rồi ghi lại lúc bạn gửi."
            : now === "lockedByOther"
              ? "Người khác đã khoá phong bì này — người đó tự gửi và ghi nhận."
              : "Chờ khoá phong bì ở bước 3.",
      children: gatedFields(form, sendVerdict),
    });
  }

  /* ---- consent (DEC-033) ------------------------------------------------------------------- */

  /**
   * Render a `DEC-033` refusal where the step's own refusal would go, and open the contact's card.
   *
   * The reason is the server's, in Vietnamese, not the step's generic "không khoá được": "the
   * customer asked the shop to stop" and "the envelope is stale" call for different people. It is
   * said once. The contact's card is re-read with the refusal and states the same reason, so once
   * it does, this notice keeps only what failed here and points at the card; if the card says
   * something else -- or could not be read -- the notice keeps the full sentence. The codes, the
   * owner decision and the correlation id stay in the notice's technical drawer either way.
   *
   * @param {any} error
   * @param {HTMLElement} resultNode
   * @param {HTMLElement} errorHost
   * @param {string} stepLine what did not happen at this step, e.g. "Chưa khoá được phong bì."
   * @param {string} [extra] a sentence only this step needs
   * @returns {boolean} whether the error was one
   */
  function showConsentRefusal(error, resultNode, errorHost, stepLine, extra = "") {
    if (!error?.consent) return false;
    const code = String(error.consent.reasonCode || "");
    setResult(resultNode, null, null);
    /** @param {boolean} cardSaysIt */
    const paint = (cardSaysIt) =>
      render(
        errorHost,
        h(
          "div",
          { dataConsentRefusal: code, dataReasonOnCard: cardSaysIt ? "true" : "false" },
          errorNotice(
            error,
            cardSaysIt
              ? { title: `${stepLine} Lý do ở thẻ “Tin dịch vụ cho khách này” phía trên.` }
              : {},
          ),
          extra ? h("p", { class: "hint" }, extra) : null,
        ),
      );
    paint(false);
    revealError(errorHost);
    void loadServiceMessaging(error.consent).then((read) => {
      const egress = read?.egress || {};
      if (egress.decision && egress.decision !== "ALLOW" && String(egress.reason_code) === code) {
        paint(true);
      }
    });
    return true;
  }

  /**
   * Read the contact's service-messaging state and render the card. A pure read.
   *
   * What it says is also the prediction steps 2 and 3 are drawn with (`consentVerdict`), so the
   * steps are redrawn when that prediction changes.
   *
   * @param {{storeId: string, contactBindingId: string, channel: string}} subject
   * @returns {Promise<any>} the read, or null when there was none
   */
  async function loadServiceMessaging(subject) {
    const before = `${service.loading}|${service.decision}|${service.reasonCode}`;
    const settle = () => {
      if (`${service.loading}|${service.decision}|${service.reasonCode}` !== before) redraw();
    };
    if (!UUID.test(subject.storeId) || !UUID.test(subject.contactBindingId)) {
      Object.assign(service, { loading: false, decision: "", reasonCode: "" });
      settle();
      return null;
    }
    serviceSubject = subject;
    serviceHost.hidden = false;
    render(serviceCardHost, skeleton(1));
    const storePart = encodeURIComponent(subject.storeId);
    const contactPart = encodeURIComponent(subject.contactBindingId);
    const channelPart = encodeURIComponent(subject.channel || CHANNEL);
    try {
      const read = await request(
        `/internal/v1/stores/${storePart}/contacts/${contactPart}/service-messaging?channel=${channelPart}`,
      );
      release.evidenceId = String(read.release_evidence?.[0]?.webhook_event_id || "");
      releaseSubmission.reset();
      render(serviceCardHost, serviceMessagingCard(read));
      Object.assign(service, {
        loading: false,
        decision: String(read.egress?.decision || ""),
        reasonCode: String(read.egress?.reason_code || ""),
      });
      settle();
      return read;
    } catch (error) {
      render(
        serviceCardHost,
        h("p", { class: "consent__label" }, "Tin dịch vụ cho khách này"),
        errorNotice(error, { title: "Không đọc được trạng thái tin dịch vụ của khách này." }),
      );
      // Unknown predicts nothing: the steps stay pressable and the server decides on the press.
      Object.assign(service, { loading: false, decision: "", reasonCode: "" });
      settle();
      return null;
    }
  }

  /**
   * The contact's TRANSACTIONAL state in one human line, the server's answer, and -- while
   * releasable -- the release.
   *
   * @param {any} read the `ServiceMessagingStateResponse` body
   * @returns {HTMLElement}
   */
  function serviceMessagingCard(read) {
    const egress = read.egress || {};
    const allowed = egress.decision === "ALLOW";
    return h(
      "section",
      {
        class: "stack",
        dataTransactionalState: String(read.transactional_state || ""),
        dataEgressDecision: String(egress.decision || ""),
        dataEgressReason: String(egress.reason_code || ""),
      },
      h(
        "div",
        { class: "consent__head" },
        h("p", { class: "consent__label" }, "Tin dịch vụ cho khách này"),
        statusPill({
          state: allowed ? "ok" : "warn",
          text: allowed ? "Được phép gửi" : "Không gửi được",
          token: String(egress.decision || ""),
        }),
      ),
      allowed
        ? h(
            "div",
            { class: "notice", dataState: "ok", dataServiceAllowed: "true" },
            // The read carries the basis but not the instant it rests on (the customer's message
            // time, the order's close time), so the line names the basis only -- never a time the
            // server did not send.
            h(
              "p",
              { class: "notice__title" },
              `Được phép gửi tin dịch vụ · căn cứ: ${enumVi(egress.basis).toLowerCase()}.`,
            ),
            h(
              "p",
              null,
              "Máy chủ vẫn kiểm tra lại khi khoá phong bì và khi ghi nhận đã gửi; khách nhắn dừng " +
                "trong lúc đó thì bước sau sẽ bị từ chối.",
            ),
          )
        : h(
            "div",
            { class: "notice", dataState: "warn", dataServiceRefusal: String(egress.reason_code) },
            h(
              "p",
              { class: "notice__title" },
              consentRefusalText(egress.reason_code) ||
                "Máy chủ không cho gửi tin dịch vụ cho khách này lúc này.",
            ),
            read.blocked_since
              ? h("p", { class: "consent__since" }, `Chặn từ ${dateTime(read.blocked_since)}`)
              : null,
          ),
      read.releasable ? releaseControl(read) : null,
      techDetails([
        ["Kênh", String(read.channel || UNKNOWN)],
        ["Khách chặn tin dịch vụ", suppressionLabel(read.transactional_state)],
        ["Khách chặn tin quảng cáo", suppressionLabel(read.marketing_state)],
        ["Quyết định của máy chủ", enumLabel(egress.decision)],
        ["Mã lý do", String(egress.reason_code || UNKNOWN)],
        ["Căn cứ gửi", egress.basis ? enumLabel(egress.basis) : UNKNOWN],
        [
          "Chính sách tin dịch vụ",
          egress.policy_version ? `v${String(egress.policy_version)}` : "chưa công bố",
        ],
        ["Khách (mã ràng buộc)", shortId(read.contact_binding_id)],
      ]),
    );
  }

  /**
   * The release: a button that opens a sheet, where the owner or approver picks the customer's own
   * later message — by the time it arrived — from the server's list, and confirms.
   *
   * @param {any} read
   * @returns {HTMLElement}
   */
  function releaseControl(read) {
    const evidence = Array.isArray(read.release_evidence) ? read.release_evidence : [];
    if (!evidence.length) {
      return h(
        "div",
        { class: "notice", dataState: "info", dataReleaseEvidence: "none" },
        h(
          "p",
          { class: "notice__title" },
          "Chưa gỡ chặn được: chưa có tin nhắn nào của chính khách trên kênh này sau khi khách yêu " +
            "cầu dừng.",
        ),
        h(
          "p",
          null,
          "Chỉ gỡ chặn dựa trên một tin nhắn mới của chính khách — không theo lời kể, ghi chú hay " +
            "cuộc gọi. Khi khách nhắn lại, tải lại thẻ này.",
        ),
      );
    }
    const select = h(
      "select",
      {
        onChange: (event) => {
          release.evidenceId = event.target.value;
          releaseSubmission.reset();
        },
      },
      evidence.map((item) =>
        h(
          "option",
          {
            value: String(item.webhook_event_id),
            selected: String(item.webhook_event_id) === release.evidenceId,
            title: String(item.webhook_event_id),
          },
          `Khách nhắn lúc ${dateTime(item.received_at)}`,
        ),
      ),
    );
    const confirm = gated(
      button({
        label: "Gỡ chặn tin dịch vụ",
        variant: "danger",
        network: true,
        block: true,
        data: { serviceRelease: "true" },
        onClick: () => void submitRelease(),
      }),
      releaseVerdict,
    );
    const body = gatedFields(
      h(
        "div",
        { class: "stack" },
        labelled({
          id: "service-release-evidence",
          label: "Tin khách nhắn lại cho tiệm",
          hint:
            "Danh sách do máy chủ đưa ra: chỉ gồm tin chính khách này nhắn trên kênh này sau khi " +
            "yêu cầu dừng. Máy chủ kiểm tra lại tin được chọn trước khi ghi.",
          control: select,
        }),
        evidence.length >= EVIDENCE_CAP
          ? h(
              "p",
              { class: "hint" },
              `Máy chủ chỉ đưa ${EVIDENCE_CAP} tin gần nhất; tin cũ hơn không có ở đây.`,
            )
          : null,
        h(
          "p",
          { class: "hint" },
          "Chỉ gỡ chặn tin dịch vụ trên kênh này. Tin quảng cáo vẫn bị chặn — muốn nhận lại quảng " +
            "cáo, khách phải đồng ý riêng.",
        ),
      ),
      releaseVerdict,
    );
    const dialog = sheet({
      title: "Gỡ chặn tin dịch vụ",
      body,
      actions: confirm,
    });
    releaseSheet = dialog;
    return h(
      "div",
      { class: "stack stack--tight", dataReleaseEvidence: String(evidence.length) },
      gated(
        button({
          label: "Gỡ chặn tin dịch vụ…",
          variant: "secondary",
          block: true,
          data: { serviceReleaseOpen: "true" },
          onClick: () => dialog.open(),
        }),
        releaseVerdict,
      ),
      dialog.node,
    );
  }

  async function submitRelease() {
    const subject = serviceSubject;
    if (!subject || !UUID.test(release.evidenceId)) {
      setResult(serviceResult, "danger", "Chưa chọn tin nhắn nào của khách làm căn cứ.");
      return;
    }
    setResult(serviceResult, "warn", "Đang gỡ chặn tin dịch vụ…");
    render(serviceErrorHost);
    const storePart = encodeURIComponent(subject.storeId);
    const contactPart = encodeURIComponent(subject.contactBindingId);
    try {
      const released = await request(
        `/internal/v1/stores/${storePart}/contacts/${contactPart}/service-messaging/release`,
        {
          method: "POST",
          body: {
            channel: subject.channel || CHANNEL,
            evidence_webhook_event_id: release.evidenceId,
          },
          idempotencyKey: releaseSubmission.key(),
        },
      );
      releaseSubmission.reset();
      releaseSheet?.close();
      setResult(
        serviceResult,
        "ok",
        `Đã gỡ chặn tin dịch vụ cho khách này trên kênh ${String(released.channel)}, dưới tên bạn. ` +
          "Tin quảng cáo vẫn bị chặn. Gửi tin vẫn phải qua đủ các bước.",
      );
      toast("Đã gỡ chặn tin dịch vụ");
      await loadServiceMessaging(subject);
    } catch (error) {
      releaseSheet?.close();
      setResult(
        serviceResult,
        error.kind === "DENIED" || error.kind === "REQUIRE_HUMAN" ? "warn" : "danger",
        error.kind === "DENIED"
          ? "Máy chủ từ chối: chỉ chủ tiệm hoặc người duyệt của cửa hàng này, đã xác thực hai " +
              "bước, mới gỡ chặn được. Không có gì được ghi."
          : error.message,
      );
      render(serviceErrorHost, errorNotice(error));
      revealError(serviceErrorHost);
    }
  }

  /* ---- the stepper -------------------------------------------------------------------------- */

  /**
   * Rebuild the four steps from what the server has answered so far. Called after each server
   * answer, never on a keystroke: the fields read their values from the state objects, and the
   * result lines and outcome hosts are the panel's own nodes, moved into the rebuilt steps with
   * whatever they were saying.
   */
  function redraw() {
    render(stepsHost, stepRead(), serviceHost, stepAsk(), stepLock(), stepRecord());
  }

  redraw();
  // Opened from `#/shadow` with a draft named: read it straight away, so the words are on screen
  // before anything can be asked of them. A read changes nothing, and a role that may not read is
  // not sent to be refused.
  if (raise.draftId && store && sendVerdict.allowed) void readBinding();
  else void loadPickable();

  return stepsHost;
}
