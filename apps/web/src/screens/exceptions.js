/**
 * Exceptions: the two surfaces where a message either already left the building without a known
 * outcome, or is about to leave it by hand.
 *
 * Every other screen in this console describes state. This one changes what a customer's phone
 * shows, so four of its choices are deliberate rather than incidental:
 *
 *   - **Nothing here retries a send, ever.** `CHANNEL_ADAPTER_SPEC_V1.md:145` prohibits an
 *     automatic retry on an unknown outcome rather than discouraging it, because that is precisely
 *     how a customer receives the same message twice. There is no retry control on the unknown
 *     queue and `api.js` would refuse to build one; the only exit from `UNKNOWN` is a named human
 *     recording what they saw on the provider side.
 *   - **The unknown queue is not store-scoped.** `GET /internal/v1/shadow/unknown-sends` reads
 *     `channel_send_receipts` with no store predicate, so it returns receipts from every store the
 *     deployment has. The screen says so instead of implying the current store filter applies.
 *   - **Hashes are pasted, never typed.** The manual-send routes compare `rendered_hash` and
 *     `snapshot_hash` with `hmac.compare_digest`. An approximate hash is a refusal, not a warning,
 *     so the fields are validated against the exact `JCS-SHA256-V1:` shape before a round trip and
 *     the value the server returns is carried into step 2 rather than retyped.
 *   - **`MANUAL_SEND_RECORDED` is not delivery.** `SECURITY_RELIABILITY_SPEC_V1.md:405` — it means
 *     a human attested a manual send, not that the provider transmitted or the recipient received.
 *     The disclosure sits next to the attest button and next to the result, not only in a tooltip.
 *
 * There is no money on this screen, and no arithmetic of any kind on a server-decided value.
 *
 * @module screens/exceptions
 */

import { MAX_LIMIT, Submission, isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, count, dateTime, shortHash, shortId } from "../core/format.js";
import { ENUM_GLOSS, enumLabel } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal } from "../core/session.js";
import {
  badge,
  empty,
  errorNotice,
  facts,
  gated,
  labelled,
  panel,
  resultLine,
  setResult,
  skeleton,
} from "../ui/components.js";

/** The route's own default. Its floor is 1 and its ceiling is `MAX_LIMIT`; outside that it 409s. */
const PAGE_LIMIT = 50;

/** `ReconcileRequest.note` — the server truncates nothing and rejects a longer string. */
const NOTE_MAX = 500;

/** Loose on case because a pasted identifier is often uppercase; the server parses either. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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
      "Bản nháp được đóng gói thành envelope và đưa đi duyệt. Sửa một ký tự là tạo bản nháp mới " +
      "và huỷ phê duyệt cũ.",
  },
  {
    token: "APPROVED_FOR_MANUAL_SEND",
    state: "warn",
    what:
      "Bước 1 bên dưới khoá envelope đã duyệt lại cho một người gửi tay. Từ lúc đó worker gửi " +
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
 * An integer the server decided.
 *
 * `null` is not `0` — `IMPLEMENTATION_ROADMAP_V1.md:906` — and `attempt_number` is a field where
 * the difference matters: a zeroth attempt and an unrecorded attempt count are different facts.
 *
 * @param {number|null|undefined} value
 * @returns {string}
 */
function integer(value) {
  return value === null || value === undefined ? UNKNOWN : String(value);
}

/**
 * The badge for a receipt's `reconciliation_state`.
 *
 * The route filters to `UNKNOWN` and `UNKNOWN_REQUIRES_HUMAN`, but an unrecognised value is shown
 * raw rather than folded into one of the two — a new state from the server must look unfamiliar.
 *
 * @param {string|null|undefined} state
 * @returns {HTMLElement}
 */
function reconciliationBadge(state) {
  const token = state || UNKNOWN;
  return badge({
    token: String(token),
    gloss: ENUM_GLOSS[token] || "trạng thái đối soát lạ",
    state: token === "UNKNOWN_REQUIRES_HUMAN" ? "danger" : "warn",
  });
}

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
    "Dán từ envelope đã duyệt, đừng gõ lại. Máy chủ so từng ký tự: một mã băm gần đúng là một lời " +
    "từ chối, không phải một cảnh báo."
  );
}

/**
 * One receipt awaiting a human, with the two decisions that can end it.
 *
 * @param {object} spec
 * @param {any} spec.item the `UnknownSendResponse`
 * @param {import("../core/rbac.js").Verdict} spec.verdict the full SHADOW_DECIDE verdict
 * @param {import("../core/rbac.js").Verdict} spec.buttonVerdict the short form, see `unknownPanel`
 * @param {(state: "ok"|"warn"|"danger", text: string) => void} spec.announce
 * @param {() => void} spec.onResolved
 * @returns {HTMLElement}
 */
function unknownSendCard(spec) {
  const { item, buttonVerdict, verdict, announce, onResolved } = spec;
  const submission = new Submission("unknown-send-reconcile");
  const errorHost = h("div");

  // Switching from one resolution to the other is a different intent carrying a different payload,
  // and the server hashes the payload alongside the key. Replaying the first key would be a 409
  // rather than a replay, so the key is retired the moment the intent changes.
  /** @type {string|null} */
  let lastResolution = null;

  const note = h("textarea", {
    rows: "2",
    maxlength: String(NOTE_MAX),
    autocomplete: "off",
    placeholder: "Đã mở hội thoại phía nhà cung cấp lúc 14:05, thấy tin đã đi.",
    onInput: () => submission.reset(),
  });

  /**
   * @param {"CONFIRMED_SENT"|"CONFIRMED_NOT_SENT"} resolution
   * @returns {Promise<void>}
   */
  async function resolve(resolution) {
    if (lastResolution !== resolution) {
      submission.reset();
      lastResolution = resolution;
    }

    const text = note.value.trim();
    const body = { resolution, note: text ? text : null };

    confirmSent.disabled = true;
    confirmNotSent.disabled = true;
    announce("warn", `Đang ghi ${resolution} cho biên nhận ${shortId(item.receipt_id)}…`);
    render(errorHost);

    try {
      // 204 with no body. `api.js` returns null for it and nothing here tries to parse a result.
      await request(
        `/internal/v1/shadow/unknown-sends/${encodeURIComponent(item.receipt_id)}/reconcile`,
        { method: "POST", body, idempotencyKey: submission.key() },
      );
      submission.reset();
      announce(
        "ok",
        `Đã ghi ${resolution} cho biên nhận ${shortId(item.receipt_id)} dưới tên bạn. ` +
          "Biên nhận này rời hàng chờ.",
      );
      onResolved();
      return;
    } catch (error) {
      // There is no 404 on this route. The UPDATE is guarded on the two unknown states, so a row
      // that was already decided — by someone else, by this operator in another tab, or by this
      // operator's own first click that timed out — comes back as a 409 carrying the repository's
      // own sentence. That is an outcome, not a failure.
      const alreadyHandled =
        error.kind === "CONFLICT" && String(error.detail || "").includes("awaiting human reconciliation");
      if (alreadyHandled) {
        announce(
          "warn",
          `Biên nhận ${shortId(item.receipt_id)} đã được xử lý trước đó. Đang tải lại hàng chờ.`,
        );
        onResolved();
        return;
      }

      confirmSent.disabled = false;
      confirmNotSent.disabled = false;
      announce(
        error.kind === "DENIED" ? "warn" : "danger",
        error.kind === "DENIED"
          ? `Máy chủ từ chối quyết định của bạn cho ${shortId(item.receipt_id)}. Không có gì được ghi.`
          : `Không ghi được đối soát cho ${shortId(item.receipt_id)}.`,
      );
      // No `onRetry`: this is a write, and a write that may have landed is never repeated by
      // software. The operator re-reads the queue and decides again.
      render(errorHost, errorNotice(error));
    }
  }

  const confirmNotSent = h(
    "button",
    {
      type: "button",
      disabled: !navigator.onLine,
      onClick: () => void resolve("CONFIRMED_NOT_SENT"),
    },
    "Chưa gửi · CONFIRMED_NOT_SENT",
  );

  // `danger` is reserved for actions that attest a message left the building. This is one.
  const confirmSent = h(
    "button",
    {
      type: "button",
      dataVariant: "danger",
      disabled: !navigator.onLine,
      onClick: () => void resolve("CONFIRMED_SENT"),
    },
    "Đã gửi · CONFIRMED_SENT",
  );

  return h(
    "article",
    { class: "card stack" },
    h(
      "div",
      { class: "spread" },
      h("strong", { class: "mono", title: item.receipt_id }, shortId(item.receipt_id)),
      reconciliationBadge(item.reconciliation_state),
    ),
    facts([
      ["Trạng thái đối soát", enumLabel(item.reconciliation_state), { mono: true, span: true }],
      ["Nhà cung cấp", item.provider || UNKNOWN, { mono: true }],
      ["Loại tin", item.message_kind || UNKNOWN, { mono: true }],
      ["Lần thử thứ", integer(item.attempt_number)],
      ["Ghi nhận lúc", dateTime(item.recorded_at)],
      ["Bản ghi outbox", shortId(item.outbox_id), { mono: true, span: true }],
      ["Mã biên nhận", item.receipt_id || UNKNOWN, { mono: true, span: true }],
    ]),
    labelled({
      id: `reconcile-note-${item.receipt_id}`,
      label: "Ghi chú (tuỳ chọn)",
      hint:
        `Tối đa ${NOTE_MAX} ký tự. Ghi bạn đã kiểm ở đâu và thấy gì, để người sau không phải kiểm ` +
        "lại. Không chép nội dung tin nhắn hay thông tin khách vào đây.",
      control: note,
    }),
    h(
      "div",
      { class: "form__actions" },
      gated(confirmNotSent, buttonVerdict),
      gated(confirmSent, buttonVerdict),
    ),
    verdict.allowed
      ? h(
          "p",
          { class: "hint" },
          "Cả hai lựa chọn đều không thể rút lại và đều được ghi kèm tên bạn vào nhật ký kiểm toán.",
        )
      : null,
    errorHost,
  );
}

/**
 * @param {any[]} items
 * @param {object} spec
 * @param {import("../core/rbac.js").Verdict} spec.verdict
 * @param {import("../core/rbac.js").Verdict} spec.buttonVerdict
 * @param {(state: "ok"|"warn"|"danger", text: string) => void} spec.announce
 * @param {() => void} spec.onResolved
 * @returns {HTMLElement}
 */
function unknownSendList(items, spec) {
  if (!items.length) {
    return empty("Không có lần gửi nào đang chờ người xác nhận. Đây là trạng thái mong muốn.");
  }
  return h(
    "div",
    { class: "stack" },
    items.map((item) => unknownSendCard({ item, ...spec })),
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
      ["Mã envelope", result.manual_send_envelope_id || UNKNOWN, { mono: true, span: true }],
      ["Phê duyệt gốc", shortId(result.approval_request_id), { mono: true }],
      ["Trạng thái", enumLabel(result.status), { mono: true }],
      ["Ràng buộc người nhận", shortId(result.recipient_binding_id), { mono: true }],
      ["Phiên bản dòng envelope", integer(result.row_version)],
      ["Mã băm nội dung", shortHash(result.rendered_hash), { mono: true, span: true }],
    ]),
    spec.attested ? notDeliveredNotice() : null,
  );
}

/**
 * @returns {HTMLElement}
 */
export function render_() {
  const who = principal();
  const decideVerdict = can(who, "SHADOW_DECIDE");
  const sendVerdict = can(who, "MANUAL_SEND");

  // `gated()` prints the verdict's reason under every control it disables. The SHADOW_DECIDE reason
  // is a paragraph naming where enforcement happens, and the unknown queue can hold fifty cards
  // with two controls each — so the full reason is stated once at the top of the panel and the
  // buttons carry a one-line form of it.
  const decideButtonVerdict = decideVerdict.allowed
    ? decideVerdict
    : { allowed: false, reason: "Vai trò của bạn không đối soát được — xem ghi chú đầu mục." };

  /* --- Panel 1: unknown sends ---------------------------------------------------------------- */

  let limit = PAGE_LIMIT;

  const unknownCount = h("span", { class: "count" }, "…");
  const unknownHost = h("div", null, skeleton(2));
  const truncation = h("div", { class: "stack stack--tight" });

  // One result line for the whole panel rather than one per card. A successful reconcile removes
  // the card that triggered it, and a confirmation that disappears with its own form has not
  // confirmed anything; this line survives the reload and names the receipt it is talking about.
  const unknownResult = resultLine();

  /**
   * @param {"ok"|"warn"|"danger"} state
   * @param {string} text
   */
  const announce = (state, text) => setResult(unknownResult, state, text);

  async function loadUnknownSends() {
    render(unknownHost, skeleton(2));
    render(truncation);
    try {
      const items = await request(`/internal/v1/shadow/unknown-sends?limit=${limit}`);
      unknownCount.textContent = count(items, limit);

      if (isTruncated(items, limit)) {
        // A full page is the only truncation signal this API has: no cursor, no offset, no total.
        // Asking for a bigger page is the only way to see more, and at the ceiling there is no way
        // at all — which the operator has to be told rather than left to infer from a round number.
        render(
          truncation,
          h(
            "p",
            { class: "hint" },
            limit < MAX_LIMIT
              ? `Máy chủ trả tối đa ${limit} biên nhận và đã trả đủ; gần như chắc chắn còn nữa. ` +
                  "Route này không có phân trang và không có con trỏ, nên cách duy nhất để thấy " +
                  "thêm là xin một trang lớn hơn."
              : `Đã xin trang lớn nhất máy chủ cho phép (${MAX_LIMIT}) và trang vẫn đầy. Hàng chờ ` +
                  "dài hơn những gì API này đọc được trong một lần; hãy xử lý bớt rồi tải lại.",
          ),
          limit < MAX_LIMIT
            ? h(
                "div",
                { class: "form__actions" },
                h(
                  "button",
                  {
                    type: "button",
                    onClick: () => {
                      limit = MAX_LIMIT;
                      void loadUnknownSends();
                    },
                  },
                  `Đọc tối đa ${MAX_LIMIT} biên nhận`,
                ),
              )
            : null,
        );
      }

      render(
        unknownHost,
        unknownSendList(items, {
          verdict: decideVerdict,
          buttonVerdict: decideButtonVerdict,
          announce,
          onResolved: () => void loadUnknownSends(),
        }),
      );
    } catch (error) {
      unknownCount.textContent = UNKNOWN;
      render(unknownHost, errorNotice(error, { onRetry: () => void loadUnknownSends() }));
    }
  }

  const unknownPanel = panel({
    eyebrow: "TOÀN HỆ THỐNG · KHÔNG BAO GIỜ GỬI LẠI",
    title: "Gửi chưa rõ kết quả",
    count: unknownCount,
    guardrail:
      "Tự động gửi lại một tin không rõ đã đi hay chưa chính là cách khách nhận hai lần cùng một " +
      "tin nhắn. Việc đó bị cấm, không phải là không khuyến khích. Bảng điều khiển này không gửi " +
      "lại và không có nút gửi lại; lối ra duy nhất khỏi trạng thái này là một người có tên xác " +
      "nhận điều đã thực sự xảy ra, bằng cách mở cuộc hội thoại bên phía nhà cung cấp và nhìn tận mắt.",
    children: h(
      "div",
      { class: "stack" },
      h(
        "div",
        { class: "notice", dataState: "info" },
        h("p", { class: "notice__title" }, "Hàng chờ này không theo cửa hàng"),
        h(
          "p",
          null,
          "Route trả biên nhận của mọi cửa hàng trong hệ thống, không lọc theo cửa hàng bạn đang " +
            "chọn ở thanh trên. Một biên nhận ở đây có thể thuộc cửa hàng khác, và người bên đó có " +
            "thể đang xử lý nó cùng lúc với bạn — nên hãy đọc lại danh sách trước khi quyết định.",
        ),
      ),
      decideVerdict.allowed
        ? null
        : h(
            "div",
            { class: "notice", dataState: "warn" },
            h("p", { class: "notice__title" }, "Bạn đọc được hàng chờ nhưng không quyết định được"),
            h("p", null, decideVerdict.reason),
          ),
      unknownResult,
      truncation,
      unknownHost,
    ),
  });

  /* --- Panel 2: manual send ------------------------------------------------------------------ */

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

  /**
   * A text field bound to one key of a draft object.
   *
   * @param {object} spec
   * @param {Record<string, string>} spec.target
   * @param {string} spec.key
   * @param {RegExp} spec.pattern used only to mark the field, never to decide the outcome
   * @param {string} spec.placeholder
   * @param {Submission} spec.submission
   * @param {(value: string) => string} [spec.normalize]
   * @returns {HTMLElement}
   */
  function boundInput(spec) {
    return h("input", {
      type: "text",
      value: spec.target[spec.key],
      autocomplete: "off",
      spellcheck: "false",
      dataFormat: "id",
      placeholder: spec.placeholder,
      "aria-invalid":
        spec.target[spec.key] && !spec.pattern.test(spec.target[spec.key]) ? "true" : null,
      onInput: (event) => {
        const raw = spec.normalize ? spec.normalize(event.target.value) : event.target.value.trim();
        if (raw !== event.target.value) event.target.value = raw;
        spec.target[spec.key] = raw;
        // Any edit is a new intent; the old key would be a 409 against the new payload.
        spec.submission.reset();
        event.target.setAttribute("aria-invalid", raw && !spec.pattern.test(raw) ? "true" : "false");
      },
    });
  }

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
    if (!UUID.test(attest.envelopeId)) return "Mã envelope phải là một UUID.";
    if (!POSITIVE_INT.test(attest.resourceVersion)) {
      return "Phiên bản tài nguyên phải là số nguyên từ 1 trở lên.";
    }
    if (!JCS_HASH.test(attest.renderedHash)) {
      return "Mã băm chính xác phải đúng dạng JCS-SHA256-V1: theo sau là 64 ký tự hex thường.";
    }
    if (!POSITIVE_INT.test(attest.rowVersion)) {
      return "Phiên bản dòng envelope phải là số nguyên từ 1 trở lên; sau bước 1 nó là 1.";
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

    setResult(prepareResult, "warn", "Đang khoá envelope…");
    render(prepareErrorHost);
    // The previous outcome card goes too. A card describing an envelope locked a minute ago,
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
        `Đã khoá envelope ${shortId(locked.manual_send_envelope_id)} ở trạng thái ${locked.status}. ` +
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
          ? "Máy chủ từ chối: phiên này không được khoá envelope gửi tay. Không có gì được ghi."
          : "Không khoá được envelope. Không có gì được ghi.",
      );
      render(prepareErrorHost, errorNotice(error));
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
        `Đã ghi ${recorded.status} cho envelope ${shortId(recorded.manual_send_envelope_id)} dưới ` +
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
      h("p", { class: "eyebrow" }, "BƯỚC 1 · KHOÁ ENVELOPE"),
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
        "Bốn giá trị trên không đọc lại được từ bất kỳ route nào của bảng điều khiển: danh sách " +
          "duyệt chỉ trả envelope_hash. Xin chúng từ người đã tạo yêu cầu duyệt.",
      ),
      h(
        "div",
        { class: "form__actions" },
        gated(
          h(
            "button",
            { type: "submit", dataVariant: "primary", disabled: !navigator.onLine },
            "Khoá envelope cho người gửi tay",
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
      h("p", { class: "eyebrow" }, "BƯỚC 2 · GHI NHẬN ĐÃ GỬI"),
      attest.envelopeId
        ? h(
            "div",
            { class: "notice", dataState: "info" },
            "Ba ô đầu đã được điền từ kết quả bước 1. Không sửa chúng bằng tay.",
          )
        : null,
      labelled({
        id: "attest-envelope-id",
        label: "Mã envelope (manual_send_envelope_id)",
        hint:
          "Bước 1 trả về giá trị này. Nếu envelope được khoá ở phiên khác, dán mã đó vào đây — " +
          "không có route nào đọc lại danh sách envelope.",
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
        hint: "Đúng bằng giá trị đã dùng ở bước 1. Máy chủ so lại với phiên bản lưu trong envelope.",
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
        label: "Phiên bản dòng envelope (If-Match)",
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
            { type: "submit", dataVariant: "danger", disabled: !navigator.onLine },
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
  void loadUnknownSends();

  const manualPanel = panel({
    eyebrow: "GỬI TAY · CÓ NGƯỜI CHỊU TRÁCH NHIỆM",
    title: "Gửi thủ công",
    guardrail:
      "Hai bước bên dưới là cách duy nhất một tin đã duyệt rời khỏi hệ thống hôm nay, và cả hai " +
      "đều ghi tên bạn. Khoá envelope xong thì worker gửi tự động không còn được phép chạy lệnh " +
      "gửi đó nữa, nên đừng khoá một envelope bạn không định tự gửi.",
    children: h(
      "div",
      { class: "stack" },
      h(
        "div",
        { class: "card stack stack--tight" },
        h("p", { class: "eyebrow" }, "GIAO THỨC TRẠNG THÁI"),
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

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "TIN RỜI KHỎI HỆ THỐNG · KHÔNG TỰ ĐỘNG"),
      h("h1", null, "Ngoại lệ"),
      h(
        "p",
        { class: "screen__lede" },
        "Hai việc trên màn hình này chạm tới điện thoại của khách. Không có gì ở đây tự gửi, tự " +
          "gửi lại hay tự đóng một trạng thái chưa rõ — mỗi lần đều phải có một người ký tên.",
      ),
    ),
    unknownPanel,
    manualPanel,
  );
}

export const screen = {
  path: "/exceptions",
  title: "Ngoại lệ",
  capability: "SHADOW_READ",
  render: render_,
};
