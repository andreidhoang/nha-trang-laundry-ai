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
import { UNKNOWN, dateTime, integer, shortId } from "../core/format.js";
import { ENUM_GLOSS, enumLabel } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal } from "../core/session.js";
import {
  badge,
  errorNotice,
  facts,
  gated,
  labelled,
  listView,
  panel,
  resultLine,
  revealError,
  setResult,
} from "../ui/components.js";
import { manualSendPanel } from "./manualSend.js";

/** The route's own default. Its floor is 1 and its ceiling is `MAX_LIMIT`; outside that it 409s. */
const PAGE_LIMIT = 50;

/** `ReconcileRequest.note` — the server truncates nothing and rejects a longer string. */
const NOTE_MAX = 500;

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
    announce("warn", `Đang ghi ${enumLabel(resolution)} cho biên nhận ${shortId(item.receipt_id)}…`);
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
        `Đã ghi ${enumLabel(resolution)} cho biên nhận ${shortId(item.receipt_id)} dưới tên bạn. ` +
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
      revealError(errorHost);
    }
  }

  const confirmNotSent = h(
    "button",
    {
      type: "button",
      dataRequiresNetwork: "true",
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
      dataRequiresNetwork: "true",
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

  // Reloading is the panel's only read-side control; the stamp records when the queue was last
  // fetched so a stale list is seen as stale rather than trusted.
  const unknown = listView({
    limit: () => limit,
    fetch: () => request(`/internal/v1/shadow/unknown-sends?limit=${limit}`),
    renderItem: (item) =>
      unknownSendCard({
        item,
        verdict: decideVerdict,
        buttonVerdict: decideButtonVerdict,
        announce,
        onResolved: () => void unknown.reload(),
      }),
    emptyText: "Không có lần gửi nào đang chờ người xác nhận. Đây là trạng thái mong muốn.",
    skeletonRows: 2,
    clearMetaOnError: true,
    // The truncation block stays local: this queue's disclosure carries the page-size escalation
    // control, which the shared helper does not render.
    truncation: null,
    onLoadStart: () => render(truncation),
    onLoaded: (items) => {
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
                      void unknown.reload();
                    },
                  },
                  `Đọc tối đa ${MAX_LIMIT} biên nhận`,
                ),
              )
            : null,
        );
      }
    },
  });

  const unknownPanel = panel({
    eyebrow: "Toàn hệ thống · Không bao giờ gửi lại",
    title: "Gửi chưa rõ kết quả",
    count: unknown.count,
    guardrail:
      "Tự động gửi lại một tin không rõ đã đi hay chưa chính là cách khách nhận hai lần cùng một " +
      "tin nhắn. Việc đó bị cấm, không phải là không khuyến khích. Bảng điều khiển này không gửi " +
      "lại và không có nút gửi lại; lối ra duy nhất khỏi trạng thái này là một người có tên xác " +
      "nhận điều đã thực sự xảy ra, bằng cách mở cuộc hội thoại bên phía nhà cung cấp và nhìn tận mắt.",
    children: h(
      "div",
      { class: "stack" },
      unknown.bar.node,
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
      unknown.host,
    ),
  });

  void unknown.reload();

  return h(
    "section",
    { class: "screen" },
    h(
      "div",
      { class: "screen__header" },
      h("p", { class: "eyebrow" }, "Tin rời khỏi hệ thống · Không tự động"),
      h("h1", null, "Ngoại lệ"),
      h(
        "p",
        { class: "screen__lede" },
        "Hai việc trên màn hình này chạm tới điện thoại của khách. Không có gì ở đây tự gửi, tự " +
          "gửi lại hay tự đóng một trạng thái chưa rõ — mỗi lần đều phải có một người ký tên.",
      ),
    ),
    unknownPanel,
    // The "Gửi thủ công" panel: the two-step manual send, built by `./manualSend.js` and mounted
    // here unchanged. Its guardrails (MANUAL_SEND_RECORDED ≠ delivered, no retry of an unknown
    // outcome) travel with it.
    manualSendPanel({ sendVerdict }),
  );
}

export const screen = {
  path: "/exceptions",
  title: "Ngoại lệ",
  capability: "SHADOW_READ",
  render: render_,
};
