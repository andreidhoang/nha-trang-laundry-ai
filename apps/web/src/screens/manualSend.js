/**
 * Manual send: the two-step panel where a named human locks an approved envelope and then attests
 * the send, split out of `screens/exceptions` so each half stays one readable unit.
 *
 * The rules that shape this panel are documented at the top of `exceptions.js` — nothing retries
 * a send, hashes are pasted never typed, and MANUAL_SEND_RECORDED is not delivery. Both steps are
 * writes gated on the MANUAL_SEND capability, and every refusal is the server's own, rendered
 * verbatim. Nothing in this module decides policy.
 *
 * @module screens/manualSend
 */

import { Submission, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, UUID, dateTime, integer, shortHash, shortId } from "../core/format.js";
import { ENUM_GLOSS, enumLabel } from "../core/i18n.js";
import {
  badge,
  boundInput,
  errorNotice,
  facts,
  gated,
  labelled,
  panel,
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
      "Bản nháp được đóng gói thành phong bì và đưa đi duyệt. Sửa một ký tự là tạo bản nháp mới " +
      "và huỷ phê duyệt cũ.",
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
 * The two-step manual-send panel: lock the approved envelope, then attest the send.
 *
 * The panel keeps its own drafts, submissions and result lines; nothing here is shared with the
 * unknown-sends queue it sits next to on the screen.
 *
 * @param {object} spec
 * @param {import("../core/rbac.js").Verdict} spec.sendVerdict
 * @returns {HTMLElement}
 */
export function manualSendPanel({ sendVerdict }) {
  const prepareSubmission = new Submission("manual-send-prepare");
  const attestSubmission = new Submission("manual-send-attest");

  /** @type {{approvalId: string, resourceVersion: string, snapshotHash: string, renderedHash: string, recipientBindingId: string}} */
  const prepare = {
    approvalId: "",
    resourceVersion: "",
    snapshotHash: "",
    renderedHash: "",
    recipientBindingId: "",
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
    if (!UUID.test(prepare.recipientBindingId)) return "Ràng buộc người nhận phải là một UUID.";
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
            recipient_binding_id: prepare.recipientBindingId,
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
      labelled({
        id: "manual-recipient-binding",
        label: "Ràng buộc người nhận (recipient_binding_id)",
        hint:
          "Máy chủ tự suy ra người nhận từ ràng buộc này. Không có ô nhập số điện thoại, và một " +
          "chuỗi liên hệ gõ tay không được nhận làm bằng chứng.",
        control: boundInput({
          target: prepare,
          key: "recipientBindingId",
          pattern: UUID,
          placeholder: "00000000-0000-0000-0000-000000000000",
          submission: prepareSubmission,
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
      h(
        "p",
        { class: "hint" },
        "Bốn giá trị trên không đọc lại được từ bất kỳ đường nào của bảng điều khiển: danh sách " +
          "duyệt chỉ trả phong bì_hash. Xin chúng từ người đã tạo yêu cầu duyệt.",
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
    render(attestBody, buildAttestForm());
  }

  render(attestBody, buildAttestForm());

  return panel({
    eyebrow: "Gửi tay · Có người chịu trách nhiệm",
    title: "Gửi thủ công",
    guardrail:
      "Hai bước bên dưới là cách duy nhất một tin đã duyệt rời khỏi hệ thống hôm nay, và cả hai " +
      "đều ghi tên bạn. Khoá phong bì xong thì tiến trình gửi tự động không còn được phép chạy lệnh " +
      "gửi đó nữa, nên đừng khoá một phong bì bạn không định tự gửi.",
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
      // No panel-level refusal notice here, unlike the unknown queue. There are exactly two
      // controls in this panel and `gated()` already states the server's rule under each of them;
      // a third copy at the top would be the same paragraph three times on one screen.
      h("div", { class: "card" }, buildPrepareForm()),
      h("div", { class: "card" }, attestBody),
    ),
  });
}
