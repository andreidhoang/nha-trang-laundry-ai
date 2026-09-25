/**
 * Manual send: the panel where a named human asks for a draft to be approved for sending, locks
 * the approved envelope, and then attests the send, split out of `screens/exceptions` so each half
 * stays one readable unit.
 *
 * Step 0 is `MESSAGE-DRAFT-BINDING-001`. `API-INTEGRITY-002` made the server compute what a
 * `SEND_MESSAGE` envelope binds and exposed it nowhere, so no envelope could be raised from this
 * console at all. Step 0 reads the draft's binding, prints the exact words the envelope will bind,
 * raises the envelope from the values the server returned — nothing is typed but the draft id —
 * and carries all four values into step 1. It does not approve and it does not send: a different
 * person approves on `#/approvals`, and the send is still this panel's human manual send.
 *
 * The rules that shape this panel are documented at the top of `exceptions.js` — nothing retries
 * a send, hashes are pasted never typed, and MANUAL_SEND_RECORDED is not delivery. Both steps are
 * writes gated on the MANUAL_SEND capability, and every refusal is the server's own, rendered
 * verbatim. Nothing in this module decides policy.
 *
 * `CONSENT-TRANSACTIONAL-001` (`DEC-033`) added the service-messaging card between step 0 and step
 * 1. A manual send is a service (TRANSACTIONAL) message, and the server refuses it -- at step 0,
 * step 1 and step 2 -- when the customer wrote STOP on the channel, when an opt-out awaits review,
 * when the owner has not published the service-messaging policy, or when there is no basis (no
 * recent message from the customer, no open order). The card reads the contact's state from the
 * server and shows the server's answer; an owner or approver releases a STOP there by picking one
 * of the customer's own later messages from the server's list. Nothing here is typed, and nothing
 * here decides whether a message may go: the server does, and asks itself again at every step.
 *
 * @module screens/manualSend
 */

import { Submission, request } from "../core/api.js";
import { consentRefusalText } from "../core/errors.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, UUID, dateTime, integer, shortHash, shortId } from "../core/format.js";
import { ENUM_GLOSS, enumLabel } from "../core/i18n.js";
import {
  badge,
  boundInput,
  errorNotice,
  facts,
  gated,
  gatedFields,
  labelled,
  panel,
  reasonCodeList,
  resultLine,
  revealError,
  setResult,
} from "../ui/components.js";

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

/** The state protocol a manual send walks, explained once at the top of its panel. */
const SEND_STEPS = [
  {
    token: "DRAFT",
    state: "info",
    what: "Agent soạn bản nháp. Chưa có gì rời khỏi hệ thống.",
  },
  {
    token: "APPROVAL_REQUESTED",
    state: "info",
    what:
      "Bước 0 bên dưới đóng gói bản nháp thành phong bì và đưa đi duyệt. Sửa một ký tự là tạo " +
      "bản nháp mới và huỷ phê duyệt cũ.",
  },
  {
    token: "APPROVED_FOR_MANUAL_SEND",
    state: "warn",
    what:
      "Bước 1 bên dưới khoá phong bì đã duyệt lại cho một người gửi tay. Từ lúc đó tiến trình gửi " +
      "tự động không còn được phép chạy lệnh gửi này.",
  },
  {
    token: "MANUAL_SEND_RECORDED",
    state: "warn",
    what:
      "Bước 2 bên dưới ghi lời chứng thực của người đã gửi. Envelope bị tiêu thụ và không dùng " +
      "lại được.",
  },
];

/**
 * The `MANUAL_SEND_RECORDED` disclosure, rendered wherever an attestation is offered or shown.
 *
 * `SECURITY_RELIABILITY_SPEC_V1.md:405` separates three things a console habitually conflates: a
 * human's claim that they sent something, the provider's claim that it transmitted, and the
 * recipient's acknowledgement. Only the first exists here, and only the first may be said.
 *
 * @returns {HTMLElement}
 */
function notDeliveredNotice() {
  return h(
    "div",
    { class: "notice", dataState: "warn" },
    h("p", { class: "notice__title" }, "MANUAL_SEND_RECORDED không có nghĩa là khách đã nhận"),
    h(
      "p",
      null,
      "Trạng thái này chỉ nói rằng một người đã chứng thực chính họ gửi tin đó bằng tay. Nó không " +
        "phải bằng chứng nhà cung cấp đã chuyển tin và không phải xác nhận của khách. Không được " +
        "trả lời khách hay ghi vào đơn rằng tin đã tới nơi chỉ vì dòng này tồn tại.",
    ),
  );
}

/**
 * The hint that goes under every hash field on this screen.
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
 * Render a `ManualSendResponse`.
 *
 * @param {any} result
 * @param {{title: string, attested: boolean}} spec
 * @returns {HTMLElement}
 */
function manualSendResult(result, spec) {
  return h(
    "div",
    { class: "card stack" },
    h(
      "div",
      { class: "spread" },
      h("h3", null, spec.title),
      badge({
        token: String(result.status || UNKNOWN),
        gloss: ENUM_GLOSS[result.status] || "trạng thái lạ",
        state: spec.attested ? "warn" : "ok",
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
    facts([
      ["Mã phong bì", result.manual_send_envelope_id || UNKNOWN, { mono: true, span: true }],
      ["Phê duyệt gốc", shortId(result.approval_request_id), { mono: true }],
      ["Trạng thái", enumLabel(result.status), { mono: true }],
      ["Ràng buộc người nhận", shortId(result.recipient_binding_id), { mono: true }],
      ["Phiên bản dòng phong bì", integer(result.row_version)],
      ["Mã băm nội dung", shortHash(result.rendered_hash), { mono: true, span: true }],
    ]),
    spec.attested ? notDeliveredNotice() : null,
  );
}

/**
 * The words a draft's `SEND_MESSAGE` envelope binds, as step 0 prints them.
 *
 * Untrusted text — a customer's conversation shaped it and a model or a reviewer wrote it — so it
 * goes through `h()`, which appends text nodes: markup in a draft is shown as characters and never
 * parsed. The recipient is the opaque contact binding, shortened, exactly as the step 1 result
 * shows it; no phone number or chat id exists on a draft to show.
 *
 * @param {any} read the `MessageDraftBindingResponse` body
 * @returns {HTMLElement}
 */
function boundMessage(read) {
  const raw = typeof read.text === "string" ? read.text : "";
  const lines = raw.split(/\r?\n/).filter((line) => line.trim() !== "");
  return h(
    "div",
    { class: "notice", dataState: "warn" },
    h("p", { class: "notice__title" }, "Đúng những chữ sẽ được xin duyệt và gửi"),
    h("p", { class: "eyebrow" }, "Nội dung sẽ gửi · Văn bản không tin cậy"),
    h(
      "div",
      { class: "stack stack--tight", dataMessageBody: "true" },
      lines.length
        ? lines.map((line) => h("p", null, line))
        : h("p", { class: "hint" }, `${UNKNOWN} bản nháp không có chữ nào`),
    ),
    facts([
      ["Số ký tự", integer(raw.length)],
      ["Phiên bản bản nháp", `v${String(read.resource_version)}`, { mono: true }],
      [
        "Người nhận (mã ràng buộc)",
        h("span", { title: read.recipient_binding_id || "" }, shortId(read.recipient_binding_id)),
        { mono: true },
      ],
      ["Mã băm nội dung", shortHash(read.rendered_hash), { mono: true, span: true }],
    ]),
    h(
      "p",
      { class: "hint" },
      "Người nhận do máy chủ lấy từ chính bản nháp; không ai chọn lại được. Sửa bản nháp sau khi " +
        "xin duyệt là huỷ phiếu này — phải xin duyệt lại.",
    ),
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
 * The manual-send panel: raise the envelope from a draft, lock the approved envelope, then attest
 * the send.
 *
 * The panel keeps its own drafts, submissions and result lines; nothing here is shared with the
 * unknown-sends queue it sits next to on the screen.
 *
 * @param {object} spec
 * @param {import("../core/rbac.js").Verdict} spec.sendVerdict
 * @param {import("../core/rbac.js").Verdict} [spec.releaseVerdict] SERVICE_MESSAGING_RELEASE
 * @param {string|null} [spec.store] the selected store; the draft read is scoped to it
 * @param {string} [spec.draftId] a draft to open step 0 on, from `#/exceptions?draft=<id>`
 * @returns {HTMLElement}
 */
export function manualSendPanel({
  sendVerdict,
  releaseVerdict = { allowed: false, reason: "" },
  store = null,
  draftId = "",
}) {
  const raiseSubmission = new Submission("manual-send-raise");
  const prepareSubmission = new Submission("manual-send-prepare");
  const attestSubmission = new Submission("manual-send-attest");

  /** @type {{draftId: string}} */
  const raise = { draftId: UUID.test(draftId) ? draftId : "" };
  /** The binding read that is on screen, and the only thing step 0 raises an envelope from. */
  let bound = /** @type {any} */ (null);
  const raiseResult = resultLine();
  const raiseErrorHost = h("div");
  const boundHost = h("div", { class: "stack" });
  const raiseActionHost = h("div");

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

  const attestBody = h("div");
  const attestResult = resultLine();
  const attestErrorHost = h("div");
  const attestOutcomeHost = h("div", { class: "stack" });

  // The service-messaging card (`DEC-033`). Hidden until a contact is known -- from the step 0
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
    { class: "card stack", id: "manual-service-messaging" },
    serviceCardHost,
    serviceResult,
    serviceErrorHost,
  );
  serviceHost.hidden = true;
  /** @type {{storeId: string, contactBindingId: string, channel: string}|null} */
  let serviceSubject = null;
  /** @type {{evidenceId: string}} */
  const release = { evidenceId: "" };

  /** @param {string} value @returns {string} */
  const asHash = (value) => value.trim().replace(/\s+/g, "");

  /** @returns {string} */
  function validatePrepare() {
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
    if (!UUID.test(attest.envelopeId)) return "Mã phong bì gửi tay phải là một UUID.";
    if (!POSITIVE_INT.test(attest.resourceVersion)) {
      return "Phiên bản tài nguyên phải là số nguyên từ 1 trở lên.";
    }
    if (!JCS_HASH.test(attest.renderedHash)) {
      return "Mã băm chính xác phải đúng dạng JCS-SHA256-V1: theo sau là 64 ký tự hex thường.";
    }
    if (!POSITIVE_INT.test(attest.rowVersion)) {
      return "Phiên bản dòng phong bì phải là số nguyên từ 1 trở lên; sau bước 1 nó là 1.";
    }
    if (!attest.sentAt) return "Chưa nhập thời điểm đã gửi.";
    const parsed = new Date(attest.sentAt);
    if (Number.isNaN(parsed.getTime())) return "Không đọc được thời điểm đã gửi.";
    if (parsed.getTime() > Date.now()) {
      return "Thời điểm đã gửi nằm ở tương lai. Máy chủ từ chối, không làm tròn.";
    }
    return "";
  }

  /**
   * Step 0, first half: read what an envelope over this draft would bind. A pure read.
   *
   * @param {Event} [event]
   */
  async function readBinding(event) {
    event?.preventDefault();
    // Whatever was on screen belonged to the previous read. An envelope must never be raised from
    // words that are not the ones currently displayed, so the old read goes before the new one.
    bound = null;
    render(boundHost);
    render(raiseActionHost);
    render(raiseErrorHost);
    if (!store) {
      setResult(raiseResult, "danger", "Chưa chọn cửa hàng nên chưa đọc được bản nháp nào.");
      return;
    }
    if (!UUID.test(raise.draftId)) {
      setResult(raiseResult, "danger", "Mã bản nháp (lượt chạy agent) phải là một UUID.");
      return;
    }
    setResult(raiseResult, "warn", "Đang đọc tin nhắn sẽ gửi…");
    const storePart = encodeURIComponent(store);
    const draftPart = encodeURIComponent(raise.draftId);
    try {
      const read = await request(
        `/internal/v1/stores/${storePart}/message-drafts/${draftPart}/binding`,
      );
      bound = read;
      raiseSubmission.reset();
      setResult(
        raiseResult,
        "ok",
        "Đã đọc. Đọc kỹ đúng những chữ bên dưới trước khi xin duyệt; chưa có gì được ghi.",
      );
      render(boundHost, boundMessage(read));
      render(raiseActionHost, raiseControl());
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
      render(raiseErrorHost, errorNotice(error));
      revealError(raiseErrorHost);
    }
  }

  /** Step 0, second half: raise the `SEND_MESSAGE` envelope from the read on screen. */
  async function raiseEnvelope() {
    const read = bound;
    if (!read) return;
    setResult(raiseResult, "warn", "Đang tạo phiếu xin duyệt gửi…");
    render(raiseErrorHost);
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
      setResult(
        raiseResult,
        "ok",
        `Đã tạo phiếu xin duyệt ${shortId(approval.approval_request_id)}. Một người duyệt khác — ` +
          "không phải bạn — quyết ở màn hình Duyệt. Chưa có gì được gửi; bốn ô của bước 1 đã được " +
          "điền sẵn.",
      );
      // Carried into step 1, which needs exactly these four. The approver still has to decide
      // first: step 1 is refused until the envelope is APPROVED, and that refusal is correct.
      prepare.approvalId = String(approval.approval_request_id || "");
      prepare.resourceVersion = String(read.resource_version);
      prepare.snapshotHash = String(read.snapshot_hash);
      prepare.renderedHash = String(read.rendered_hash);
      redrawPrepare();
    } catch (error) {
      if (showConsentRefusal(error, raiseResult, raiseErrorHost)) return;
      setResult(
        raiseResult,
        error.kind === "DENIED" ? "warn" : "danger",
        error.kind === "DENIED"
          ? "Máy chủ từ chối: phiên này không được xin duyệt gửi tin cho cửa hàng này. Không có " +
              "gì được ghi."
          : "Không tạo được phiếu xin duyệt. Không có gì được ghi.",
      );
      render(raiseErrorHost, errorNotice(error));
      revealError(raiseErrorHost);
    }
  }

  /** @returns {HTMLElement} */
  function raiseControl() {
    return h(
      "div",
      { class: "form__actions" },
      gated(
        h(
          "button",
          {
            type: "button",
            dataVariant: "primary",
            dataRequiresNetwork: "true",
            onClick: () => void raiseEnvelope(),
          },
          "Xin duyệt gửi đúng tin này",
        ),
        sendVerdict,
      ),
    );
  }

  function buildRaiseForm() {
    return h(
      "form",
      { class: "form", onSubmit: (event) => void readBinding(event) },
      h("p", { class: "eyebrow" }, "Bước 0 · Đọc tin nhắn và xin duyệt gửi"),
      labelled({
        id: "manual-draft-id",
        label: "Mã bản nháp (lượt chạy agent)",
        hint:
          "Mở từ một quyết định đã ghi ở màn hình Bản nháp AI thì ô này đã được điền. Máy chủ trả " +
          "về đúng chữ đang lưu — của agent, hoặc bản người duyệt đã sửa — cùng phiên bản và hai " +
          "mã niêm phong; không ô nào bên dưới cần gõ tay.",
        control: boundInput({
          target: raise,
          key: "draftId",
          pattern: UUID,
          placeholder: "00000000-0000-0000-0000-000000000000",
          submission: raiseSubmission,
        }),
      }),
      h(
        "div",
        { class: "form__actions" },
        gated(
          h(
            "button",
            { type: "submit", dataVariant: "quiet", dataRequiresNetwork: "true" },
            "Đọc tin nhắn sẽ gửi",
          ),
          sendVerdict,
        ),
      ),
      raiseResult,
      raiseErrorHost,
      boundHost,
      raiseActionHost,
      h(
        "p",
        { class: "hint" },
        "Xin duyệt không phải là duyệt, và duyệt không phải là gửi. Máy chủ từ chối người đã xin " +
          "tự duyệt phiếu của mình; tin chỉ rời khỏi hệ thống khi một người tự gửi tay và ký tên " +
          "ở bước 2.",
      ),
    );
  }

  async function submitPrepare(event) {
    event.preventDefault();
    const problem = validatePrepare();
    if (problem) {
      setResult(prepareResult, "danger", problem);
      return;
    }

    setResult(prepareResult, "warn", "Đang khoá phong bì…");
    render(prepareErrorHost);
    // The previous outcome card goes too. A card describing an phong bì locked a minute ago,
    // sitting under a refusal for the one just attempted, is exactly the misread this console
    // exists to prevent.
    render(prepareOutcomeHost);

    try {
      const locked = await request(
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
      // Confirmed exactly once, here. Nothing toasts and nothing repeats it.
      setResult(
        prepareResult,
        "ok",
        `Đã khoá phong bì ${shortId(locked.manual_send_envelope_id)} ở trạng thái ${enumLabel(locked.status)}. ` +
          "Worker gửi tự động không còn chạy được lệnh gửi này.",
      );
      render(
        prepareOutcomeHost,
        manualSendResult(locked, { title: "Envelope đã khoá", attested: false }),
      );

      // Carry the three values step 2 needs. An operator retyping a 64-character digest by hand is
      // a refusal waiting to happen, and the server is the only source of truth for all three.
      attest.envelopeId = String(locked.manual_send_envelope_id || "");
      attest.renderedHash = String(locked.rendered_hash || "");
      attest.rowVersion = integer(locked.row_version) === UNKNOWN ? "" : String(locked.row_version);
      attest.resourceVersion = prepare.resourceVersion;
      redrawAttest();
    } catch (error) {
      if (showConsentRefusal(error, prepareResult, prepareErrorHost)) return;
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
      const recorded = await request(
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
      setResult(
        attestResult,
        "ok",
        `Đã ghi ${enumLabel(recorded.status)} cho phong bì ${shortId(recorded.manual_send_envelope_id)} dưới ` +
          "tên bạn. Đây là lời chứng thực của người gửi, không phải xác nhận đã tới khách.",
      );
      render(
        attestOutcomeHost,
        manualSendResult(recorded, { title: "Đã ghi nhận gửi tay", attested: true }),
      );
    } catch (error) {
      if (
        showConsentRefusal(
          error,
          attestResult,
          attestErrorHost,
          "Lời chứng thực không được ghi. Nếu bạn đã lỡ gửi tin này sau khi khách yêu cầu dừng, " +
            "báo chủ tiệm ngay.",
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

  /**
   * Render a `DEC-033` refusal where the step's own refusal would go, and open the contact's card.
   *
   * The headline is the server's reason in Vietnamese, not the step's generic "không khoá được":
   * "the customer asked the shop to stop" and "the envelope is stale" call for different people.
   *
   * @param {any} error
   * @param {HTMLElement} resultNode
   * @param {HTMLElement} errorHost
   * @param {string} [extra] a sentence only this step needs
   * @returns {boolean} whether the error was one
   */
  function showConsentRefusal(error, resultNode, errorHost, extra = "") {
    if (!error?.consent) return false;
    setResult(resultNode, "warn", error.message);
    render(
      errorHost,
      h(
        "div",
        { dataConsentRefusal: String(error.consent.reasonCode || "") },
        errorNotice(error),
        extra ? h("p", { class: "hint" }, extra) : null,
      ),
    );
    revealError(errorHost);
    void loadServiceMessaging(error.consent);
    return true;
  }

  /**
   * Read the contact's service-messaging state and render the card. A pure read.
   *
   * @param {{storeId: string, contactBindingId: string, channel: string}} subject
   */
  async function loadServiceMessaging(subject) {
    if (!UUID.test(subject.storeId) || !UUID.test(subject.contactBindingId)) return;
    serviceSubject = subject;
    serviceHost.hidden = false;
    render(serviceCardHost, h("p", { class: "hint" }, "Đang đọc trạng thái tin dịch vụ…"));
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
    } catch (error) {
      render(
        serviceCardHost,
        h("p", { class: "eyebrow" }, "Tin dịch vụ cho khách này"),
        errorNotice(error, { title: "Không đọc được trạng thái tin dịch vụ của khách này." }),
      );
    }
  }

  /**
   * The contact's TRANSACTIONAL state, the server's answer, and -- while releasable -- the release.
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
      h("p", { class: "eyebrow" }, "Tin dịch vụ cho khách này"),
      facts([
        ["Kênh", String(read.channel || UNKNOWN), { mono: true }],
        ["Khách chặn tin dịch vụ", suppressionLabel(read.transactional_state)],
        ["Khách chặn tin quảng cáo", suppressionLabel(read.marketing_state)],
        ["Chặn từ lúc", read.blocked_since ? dateTime(read.blocked_since) : "—"],
        ["Căn cứ gửi", egress.basis ? enumLabel(egress.basis) : "—"],
        [
          "Chính sách tin dịch vụ",
          egress.policy_version ? `v${String(egress.policy_version)}` : "chưa công bố",
          { mono: true },
        ],
      ]),
      allowed
        ? h(
            "div",
            { class: "notice", dataState: "ok", dataServiceAllowed: "true" },
            h(
              "p",
              { class: "notice__title" },
              `Lúc này gửi được tin dịch vụ cho khách này, căn cứ ${enumLabel(egress.basis)}.`,
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
            reasonCodeList([String(egress.reason_code || egress.decision || UNKNOWN)], "Mã lý do"),
          ),
      read.releasable ? releaseControl(read) : null,
    );
  }

  /**
   * The release: pick the customer's own later message from the server's list, then press.
   *
   * @param {any} read
   * @returns {HTMLElement}
   */
  function releaseControl(read) {
    const evidence = Array.isArray(read.release_evidence) ? read.release_evidence : [];
    const scope =
      "Chỉ gỡ chặn tin dịch vụ trên kênh này. Tin quảng cáo vẫn bị chặn — muốn nhận lại quảng cáo, " +
      "khách phải đồng ý riêng.";
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
          `Tin khách nhắn lúc ${dateTime(item.received_at)} · ${shortId(item.webhook_event_id)}`,
        ),
      ),
    );
    return gatedFields(
      h(
        "div",
        { class: "stack", dataReleaseEvidence: String(evidence.length) },
        labelled({
          id: "service-release-evidence",
          label: "Tin nhắn của khách làm căn cứ gỡ chặn",
          hint:
            "Danh sách do máy chủ đưa ra: chỉ gồm tin chính khách này nhắn trên kênh này sau khi " +
            "yêu cầu dừng. Máy chủ kiểm tra lại tin được chọn trước khi ghi.",
          control: select,
        }),
        h("p", { class: "hint" }, scope),
        h(
          "div",
          { class: "form__actions" },
          gated(
            h(
              "button",
              {
                type: "button",
                dataVariant: "danger",
                dataRequiresNetwork: "true",
                dataServiceRelease: "true",
                onClick: () => void submitRelease(),
              },
              "Gỡ chặn tin dịch vụ",
            ),
            releaseVerdict,
          ),
        ),
      ),
      releaseVerdict,
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
      setResult(
        serviceResult,
        "ok",
        `Đã gỡ chặn tin dịch vụ cho khách này trên kênh ${String(released.channel)}, dưới tên bạn. ` +
          "Tin quảng cáo vẫn bị chặn. Gửi tin vẫn phải qua đủ các bước bên dưới.",
      );
      await loadServiceMessaging(subject);
    } catch (error) {
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

  function buildPrepareForm() {
    const channelField = h("input", {
      type: "text",
      value: CHANNEL,
      readonly: true,
      "aria-readonly": "true",
      dataFormat: "id",
    });

    return h(
      "form",
      { class: "form", onSubmit: submitPrepare },
      h("p", { class: "eyebrow" }, "Bước 1 · Khoá envelope"),
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
      h(
        "p",
        { class: "hint" },
        "Không có ô người nhận. Máy chủ lấy người nhận từ chính bản nháp đã được duyệt và trả nó " +
          "về trong kết quả; một yêu cầu tự ghi người nhận bị từ chối.",
      ),
      labelled({
        id: "manual-channel",
        label: "Kênh",
        hint:
          "Cố định. Triển khai này chưa cấu hình kênh thật nào, nên máy chủ chỉ nhận INTERNAL_TEST " +
          "và từ chối mọi giá trị khác. Không có tin nào tới khách qua đường này.",
        control: channelField,
      }),
      // This used to say the four values "không đọc lại được từ bất kỳ đường nào" because the
      // approval list returned only `envelope_hash`. APPROVAL-DECIDE-001 projected the other
      // three, which made half of that sentence false -- and nothing caught it, because the
      // sentence is a DESCRIPTIVE disclosure with no machine binding. It is corrected by hand
      // here, and it is a live example of what the unbound majority of the registry costs.
      //
      // The remaining half is still true and is the reason this form still has no picker: the
      // queue lists `WHERE s.status = 'REQUESTED'`, and `prepare` refuses any approval that is
      // not already `APPROVED` (`manual_sends.py`). The two sets are disjoint, so the row that
      // carries these values is never the row that may be sent.
      //
      // MESSAGE-DRAFT-BINDING-001 changed the common path: step 0 raises the envelope from the
      // draft's binding and fills these four boxes itself. The sentence below says so, and keeps
      // the half that is still true for an envelope raised in another session.
      h(
        "p",
        { class: "hint" },
        "Xin duyệt ở bước 0 thì bốn ô trên đã được điền sẵn; chờ người duyệt quyết rồi bấm. " +
          "Phiếu mở ở phiên khác thì phải xin bốn giá trị từ người đã tạo phiếu: hàng chờ duyệt " +
          "chỉ hiện phiếu đang chờ quyết — mà gửi tay chỉ làm được sau khi phiếu đã được duyệt.",
      ),
      h(
        "div",
        { class: "form__actions" },
        gated(
          h(
            "button",
            { type: "submit", dataVariant: "primary", dataRequiresNetwork: "true" },
            "Khoá phong bì cho người gửi tay",
          ),
          sendVerdict,
        ),
      ),
      prepareResult,
      prepareErrorHost,
      prepareOutcomeHost,
    );
  }

  function buildAttestForm() {
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
          ? `Sẽ gửi ${iso} — thời điểm này ở tương lai và máy chủ sẽ từ chối.`
          : `Sẽ gửi ${iso} · giờ Việt Nam ${dateTime(iso)}. Hãy đối chiếu với đồng hồ trước khi ghi.`;
    };

    const sentAtInput = h("input", {
      type: "datetime-local",
      value: attest.sentAt,
      onInput: (event) => {
        attest.sentAt = event.target.value;
        attestSubmission.reset();
        updateEcho();
      },
    });

    updateEcho();

    return h(
      "form",
      { class: "form", onSubmit: submitAttest },
      h("p", { class: "eyebrow" }, "Bước 2 · Ghi nhận đã gửi"),
      attest.envelopeId
        ? h(
            "div",
            { class: "notice", dataState: "info" },
            "Ba ô đầu đã được điền từ kết quả bước 1. Không sửa chúng bằng tay.",
          )
        : null,
      labelled({
        id: "attest-envelope-id",
        label: "Mã phong bì gửi tay (manual_send_envelope_id)",
        hint:
          "Bước 1 trả về giá trị này. Nếu phong bì được khoá ở phiên khác, dán mã đó vào đây — " +
          "không có đường nào đọc lại danh sách phong bì.",
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
        hint: "Đúng bằng giá trị đã dùng ở bước 1. Máy chủ so lại với phiên bản lưu trong phong bì.",
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
          "Gửi trong header If-Match. Ngay sau bước 1 giá trị này là 1. Thiếu nó máy chủ trả 428 " +
          "và không ghi gì.",
        control: boundInput({
          target: attest,
          key: "rowVersion",
          pattern: POSITIVE_INT,
          placeholder: "1",
          submission: attestSubmission,
        }),
      }),
      labelled({
        id: "attest-sent-at",
        label: "Đã gửi lúc",
        hint:
          "Đọc theo múi giờ của thiết bị này rồi chuyển sang UTC khi gửi. Không được ở tương lai " +
          "so với đồng hồ máy chủ; nếu hai đồng hồ lệch, hãy lùi lại vài phút.",
        control: sentAtInput,
      }),
      sentAtEcho,
      notDeliveredNotice(),
      h(
        "div",
        { class: "form__actions" },
        gated(
          h(
            "button",
            { type: "submit", dataVariant: "danger", dataRequiresNetwork: "true" },
            "Chứng thực rằng tôi đã gửi tin này",
          ),
          sendVerdict,
        ),
      ),
      attestResult,
      attestErrorHost,
      attestOutcomeHost,
    );
  }

  function redrawAttest() {
    attestSubmission.reset();
    render(attestBody, gatedFields(buildAttestForm(), sendVerdict));
  }

  render(attestBody, gatedFields(buildAttestForm(), sendVerdict));

  const prepareBody = h("div");

  // Rebuilt rather than patched when step 0 fills its four boxes, because `boundInput` reads its
  // value once at build time. The result and error hosts are the panel's own nodes and move into
  // the rebuilt form with whatever they were saying.
  function redrawPrepare() {
    prepareSubmission.reset();
    render(prepareBody, gatedFields(buildPrepareForm(), sendVerdict));
  }

  render(prepareBody, gatedFields(buildPrepareForm(), sendVerdict));

  const raiseBody = gatedFields(buildRaiseForm(), sendVerdict);
  // Opened from `#/shadow` with a draft named: read it straight away, so the words are on screen
  // before anything can be asked of them. A read changes nothing, and a role that may not read is
  // not sent to be refused.
  if (raise.draftId && store && sendVerdict.allowed) void readBinding();

  return panel({
    eyebrow: "Gửi tay · Có người chịu trách nhiệm",
    title: "Gửi thủ công",
    guardrail:
      "Bước 1 và bước 2 bên dưới là cách duy nhất một tin đã duyệt rời khỏi hệ thống hôm nay, và " +
      "cả hai đều ghi tên bạn. Khoá phong bì xong thì tiến trình gửi tự động không còn được phép " +
      "chạy lệnh gửi đó nữa, nên đừng khoá một phong bì bạn không định tự gửi.",
    children: h(
      "div",
      { class: "stack" },
      h(
        "div",
        { class: "card stack stack--tight" },
        h("p", { class: "eyebrow" }, "Giao thức trạng thái"),
        h(
          "ol",
          { class: "stack stack--tight" },
          SEND_STEPS.map((step) =>
            h(
              "li",
              { class: "stack stack--tight" },
              badge({
                token: step.token,
                gloss: ENUM_GLOSS[step.token] || "",
                state: step.state,
              }),
              h("p", null, step.what),
            ),
          ),
        ),
      ),
      // No panel-level refusal notice here, unlike the unknown queue. Every control in this panel
      // is wrapped in `gated()`, which already states the server's rule under it; another copy at
      // the top would be the same paragraph once more on one screen.
      h("div", { class: "card" }, raiseBody),
      serviceHost,
      h("div", { class: "card" }, prepareBody),
      h("div", { class: "card" }, attestBody),
    ),
  });
}
