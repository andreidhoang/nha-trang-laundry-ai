/**
 * Ngoại lệ & gửi tay: the two surfaces where a message either already left the building without a
 * known outcome, or is about to leave it by hand.
 *
 * Rebuilt on the V2 kit (`CONSOLE-REDESIGN-005`, spec V2 §5.7) as two sections behind one
 * segmented switch: "Gửi chưa rõ" — receipts awaiting a person, each a card with its two
 * resolutions — and "Gửi tay" — the four-step manual send (`./manualSend.js`, the "Gửi thủ công"
 * panel of the V1 console). Arriving with `?draft=` from `#/shadow` opens "Gửi tay" on that draft.
 *
 * Every other screen in this console describes state. This one changes what a customer's phone
 * shows, so four of its choices are deliberate rather than incidental:
 *
 *   - **Nothing here retries a send, ever.** `CHANNEL_ADAPTER_SPEC_V1.md:145` prohibits an
 *     automatic retry on an unknown outcome rather than discouraging it, because that is precisely
 *     how a customer receives the same message twice. There is no retry control on the unknown
 *     queue and `api.js` would refuse to build one; the only exit from `UNKNOWN` is a named human
 *     recording what they saw on the provider side. That fact is one visible line above the queue.
 *   - **The unknown queue is the selected store's.** Until API-INTEGRITY-002 the route read
 *     `channel_send_receipts` with no store predicate and returned every store's receipts. It is
 *     now `GET /internal/v1/stores/{store_id}/shadow/unknown-sends`, requires membership of that
 *     store and MFA, and a reconcile is refused for a receipt of any other store.
 *   - **Values are carried, never typed.** The manual-send routes compare `rendered_hash` and
 *     `snapshot_hash` with `hmac.compare_digest`. The stepper carries every value the server
 *     returned into the next step; typing exists only under "Nhập mã thủ công", for an envelope
 *     raised or locked on another device, and is validated against the exact shape there.
 *   - **`MANUAL_SEND_RECORDED` is not delivery.** `SECURITY_RELIABILITY_SPEC_V1.md:405` — it means
 *     a human attested a manual send, not that the provider transmitted or the recipient received.
 *     The statement sits next to the attest button and next to the result, not only in a tooltip.
 *
 * There is no money on this screen, and no arithmetic of any kind on a server-decided value.
 *
 * @module screens/exceptions
 */

import { MAX_LIMIT, Submission, isTruncated, request } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { UNKNOWN, dateTime, integer, shortId } from "../core/format.js";
import { enumLabel, enumVi } from "../core/i18n.js";
import { can } from "../core/rbac.js";
import { principal, storeId } from "../core/session.js";
import {
  errorNotice,
  gated,
  labelled,
  listView,
  resultLine,
  revealError,
  setResult,
} from "../ui/components.js";
import {
  button,
  confirmButton,
  emptyState,
  infoButton,
  keyValues,
  page,
  section,
  segmented,
  statusPill,
  techDetails,
  toast,
} from "../ui/kit.js";
import { manualSendPanel } from "./manualSend.js";

/** The route's own default. Its floor is 1 and its ceiling is `MAX_LIMIT`; outside that it 409s. */
const PAGE_LIMIT = 50;

/** `ReconcileRequest.note` — the server truncates nothing and rejects a longer string. */
const NOTE_MAX = 500;

/**
 * The words for a receipt's `reconciliation_state`. Scoped here rather than read from the shared
 * map because `UNKNOWN` is also `AcquisitionSource.UNKNOWN` there ("chưa biết"), which says the
 * wrong thing about a message; the same collision `enumSelect`'s glossary parameter exists for.
 * An unrecognised value is shown raw — a new state from the server must look unfamiliar.
 */
const RECONCILIATION_VI = {
  UNKNOWN: "Chưa rõ đã gửi chưa",
  UNKNOWN_REQUIRES_HUMAN: "Chưa rõ, cần người xử lý",
};

/**
 * One receipt awaiting a human, with the two decisions that can end it.
 *
 * @param {object} spec
 * @param {any} spec.item the `UnknownSendResponse`
 * @param {import("../core/rbac.js").Verdict} spec.verdict the full SHADOW_DECIDE verdict
 * @param {import("../core/rbac.js").Verdict} spec.buttonVerdict the short form, see `render_`
 * @param {(state: "ok"|"warn"|"danger", text: string) => void} spec.announce
 * @param {() => void} spec.onResolved
 * @returns {HTMLElement}
 */
function unknownSendCard(spec) {
  const { item, buttonVerdict, verdict, announce, onResolved } = spec;
  const submission = new Submission("unknown-send-reconcile");
  const errorHost = h("div");
  const state = String(item.reconciliation_state || "");

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

    const text = /** @type {HTMLTextAreaElement} */ (note).value.trim();
    const body = { resolution, note: text ? text : null };

    confirmSent.disabled = true;
    confirmNotSent.disabled = true;
    announce("warn", `Đang ghi "${enumVi(resolution)}" cho một biên nhận…`);
    render(errorHost);

    try {
      // 204 with no body. `api.js` returns null for it and nothing here tries to parse a result.
      // The key is required by the route and replays: a resend after a dropped response answers
      // 204 again instead of 409, and the receipt is not touched twice.
      await request(
        `/internal/v1/shadow/unknown-sends/${encodeURIComponent(item.receipt_id)}/reconcile`,
        { method: "POST", body, idempotencyKey: submission.key() },
      );
      submission.reset();
      announce(
        "ok",
        `Đã ghi "${enumVi(resolution)}" dưới tên bạn (${enumLabel(resolution)}). ` +
          "Biên nhận này rời hàng chờ.",
      );
      toast(`Đã ghi: ${enumVi(resolution)}`);
      onResolved();
      return;
    } catch (error) {
      // There is no 404 on this route. The UPDATE is guarded on the two unknown states, so a row
      // that was already decided — by someone else, by this operator in another tab, or by this
      // operator's own first click that timed out — comes back as a 409 carrying the repository's
      // own sentence. That is an outcome, not a failure.
      const alreadyHandled =
        error.kind === "CONFLICT" &&
        String(error.detail || "").includes("awaiting human reconciliation");
      if (alreadyHandled) {
        announce("warn", "Biên nhận này đã được xử lý trước đó. Đang tải lại hàng chờ.");
        onResolved();
        return;
      }

      confirmSent.disabled = false;
      confirmNotSent.disabled = false;
      announce(
        error.kind === "DENIED" ? "warn" : "danger",
        error.kind === "DENIED"
          ? "Máy chủ từ chối quyết định của bạn cho biên nhận này. Không có gì được ghi."
          : "Không ghi được đối soát cho biên nhận này.",
      );
      // No `onRetry`: this is a write, and a write that may have landed is never repeated by
      // software. The operator re-reads the queue and decides again.
      render(errorHost, errorNotice(error));
      revealError(errorHost);
    }
  }

  const confirmNotSent = button({
    label: "Chưa gửi",
    network: true,
    block: true,
    data: { resolution: "CONFIRMED_NOT_SENT" },
    onClick: () => void resolve("CONFIRMED_NOT_SENT"),
  });
  confirmNotSent.title = "CONFIRMED_NOT_SENT";

  // `danger` is reserved for actions that attest a message left the building. This is one, and
  // it cannot be taken back, so it asks for a second press.
  const confirmSent = confirmButton({
    label: "Đã gửi",
    confirmLabel: "Bấm lần nữa: tin đã đi",
    variant: "danger",
    block: true,
    onConfirm: () => void resolve("CONFIRMED_SENT"),
  });
  confirmSent.dataset.resolution = "CONFIRMED_SENT";
  confirmSent.title = "CONFIRMED_SENT";

  return h(
    "article",
    { class: "receipt surface", dataReceipt: String(item.receipt_id || "") },
    h(
      "div",
      { class: "receipt__head" },
      statusPill({
        state: state === "UNKNOWN_REQUIRES_HUMAN" ? "danger" : "warn",
        text: RECONCILIATION_VI[state] || state || "trạng thái đối soát lạ",
        token: state || UNKNOWN,
      }),
      h("span", { class: "receipt__time" }, dateTime(item.recorded_at)),
    ),
    keyValues([
      ["Nhà cung cấp", enumVi(item.provider)],
      ["Loại tin", enumVi(item.message_kind)],
      ["Lần thử thứ", integer(item.attempt_number)],
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
      "p",
      { class: "receipt__question" },
      "Bạn đã mở hội thoại phía nhà cung cấp. Tin này đã đi chưa?",
    ),
    h(
      "div",
      { class: "receipt__actions" },
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
    techDetails([
      ["Mã biên nhận", shortId(item.receipt_id), { copy: String(item.receipt_id || "") }],
      ["Trạng thái đối soát", enumLabel(item.reconciliation_state)],
      ["Nhà cung cấp", String(item.provider || UNKNOWN)],
      ["Loại tin", String(item.message_kind || UNKNOWN)],
      ["Bản ghi outbox", shortId(item.outbox_id), { copy: String(item.outbox_id || "") }],
    ]),
  );
}

/**
 * @param {{query?: URLSearchParams}} [context] the router's context; `?draft=<agent_run_id>` opens
 *   the manual send on that draft, which is how `#/shadow` hands a reviewed draft over
 * @returns {HTMLElement}
 */
export function render_(context) {
  const who = principal();
  const store = storeId();
  const readVerdict = can(who, "UNKNOWN_SENDS_READ");
  const decideVerdict = can(who, "SHADOW_DECIDE");
  const sendVerdict = can(who, "MANUAL_SEND");
  const releaseVerdict = can(who, "SERVICE_MESSAGING_RELEASE");
  const approveVerdict = can(who, "APPROVALS_DECIDE");
  const draftId = String(context?.query?.get("draft") || "").trim();

  // `gated()` prints the verdict's reason under every control it disables. The SHADOW_DECIDE reason
  // is a paragraph naming where enforcement happens, and the unknown queue can hold fifty cards
  // with two controls each — so the full reason is stated once above the queue and the buttons
  // carry a one-line form of it.
  const decideButtonVerdict = decideVerdict.allowed
    ? decideVerdict
    : { allowed: false, reason: "Vai trò của bạn không đối soát được — xem ghi chú đầu mục." };

  /* --- Section 1: unknown sends ------------------------------------------------------------- */

  let limit = PAGE_LIMIT;

  const truncation = h("div", { class: "stack stack--tight" });

  // One result line for the whole section rather than one per card. A successful reconcile removes
  // the card that triggered it, and a confirmation that disappears with its own form has not
  // confirmed anything; this line survives the reload.
  const unknownResult = resultLine();

  /**
   * @param {"ok"|"warn"|"danger"} state
   * @param {string} text
   */
  const announce = (state, text) => setResult(unknownResult, state, text);

  const emptyHost = h(
    "div",
    null,
    emptyState({
      icon: "check",
      title: "Không có lần gửi nào đang chờ xác nhận",
      body: "Đây là trạng thái mong muốn.",
    }),
  );
  emptyHost.hidden = true;

  // Reloading is the section's only read-side control; the stamp records when the queue was last
  // fetched so a stale list is seen as stale rather than trusted.
  const unknown = listView({
    limit: () => limit,
    fetch: () =>
      request(
        `/internal/v1/stores/${encodeURIComponent(store)}/shadow/unknown-sends?limit=${limit}`,
      ),
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
    onLoadStart: () => {
      render(truncation);
      emptyHost.hidden = true;
      unknown.host.hidden = false;
    },
    onLoaded: (items) => {
      const rows = Array.isArray(items) ? items : [];
      emptyHost.hidden = rows.length > 0;
      unknown.host.hidden = rows.length === 0;
      setCount(rows.length ? unknown.count.textContent || "" : "");
      if (isTruncated(rows, limit)) {
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
            ? button({
                label: `Đọc tối đa ${MAX_LIMIT} biên nhận`,
                variant: "quiet",
                onClick: () => {
                  limit = MAX_LIMIT;
                  void unknown.reload();
                },
              })
            : null,
        );
      }
    },
  });

  const unknownSection = section({
    title: "Gửi chưa rõ kết quả",
    card: false,
    info: infoButton(
      "Vì sao không có nút gửi lại?",
      h(
        "div",
        { class: "notice", dataState: "warn" },
        h(
          "p",
          null,
          "Tự động gửi lại một tin không rõ đã đi hay chưa chính là cách khách nhận hai lần cùng một " +
            "tin nhắn. Việc đó bị cấm, không phải là không khuyến khích. Bảng điều khiển này không gửi " +
            "lại và không có nút gửi lại; lối ra duy nhất khỏi trạng thái này là một người có tên xác " +
            "nhận điều đã thực sự xảy ra, bằng cách mở cuộc hội thoại bên phía nhà cung cấp và nhìn tận mắt.",
        ),
      ),
      h(
        "div",
        { class: "notice", dataState: "info" },
        h("p", { class: "notice__title" }, "Chỉ biên nhận của cửa hàng đang chọn"),
        h(
          "p",
          null,
          "Hàng chờ này lọc theo cửa hàng bạn đang chọn ở thanh trên. Người của cửa hàng khác " +
            "không thấy và không đối soát được các biên nhận này, và bạn cũng không thấy của họ.",
        ),
      ),
    ),
    children: h(
      "div",
      { class: "stack" },
      unknown.bar.node,
      h(
        "p",
        { class: "hint section__fact" },
        "Không bao giờ gửi lại tự động. Chỉ một người xem bên nhà cung cấp rồi ghi điều đã xảy ra.",
      ),
      readVerdict.allowed
        ? null
        : h(
            "div",
            { class: "notice", dataState: "warn" },
            h("p", { class: "notice__title" }, "Bạn không đọc được hàng chờ này"),
            h("p", null, readVerdict.reason),
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
      readVerdict.allowed ? emptyHost : null,
      readVerdict.allowed ? unknown.host : null,
    ),
  });

  // Not fetched when the server would refuse it: a 403 in place of the queue says less than the
  // notice above, which names the reason.
  if (readVerdict.allowed) void unknown.reload();

  /* --- Section 2: the manual send ------------------------------------------------------------ */

  const sendSection = section({
    title: "Gửi tay",
    card: false,
    children: manualSendPanel({ sendVerdict, releaseVerdict, approveVerdict, store, draftId }),
  });

  /* --- The switch ---------------------------------------------------------------------------- */

  /** @param {string} value */
  function showPart(value) {
    unknownSection.hidden = value !== "unknown";
    sendSection.hidden = value !== "send";
  }

  const initial = draftId ? "send" : "unknown";
  const parts = segmented({
    label: "Phần của màn hình",
    value: initial,
    options: [
      { value: "unknown", label: "Gửi chưa rõ", count: " " },
      { value: "send", label: "Gửi tay" },
    ],
    onChange: showPart,
  });
  const countBadge = parts.querySelector(".segmented__count");

  /** @param {string} text */
  function setCount(text) {
    if (!countBadge) return;
    countBadge.textContent = text;
    /** @type {HTMLElement} */ (countBadge).hidden = !text;
  }
  setCount("");
  showPart(initial);

  return h(
    "section",
    { class: "screen" },
    page({
      title: "Ngoại lệ & gửi tay",
      subtitle: "Không có gì ở đây tự gửi hay tự gửi lại.",
      info: infoButton(
        "Màn hình này làm gì?",
        h(
          "p",
          { class: "screen__lede" },
          "Hai việc trên màn hình này chạm tới điện thoại của khách. Không có gì ở đây tự gửi, tự " +
            "gửi lại hay tự đóng một trạng thái chưa rõ — mỗi lần đều phải có một người ký tên.",
        ),
      ),
    }),
    parts,
    unknownSection,
    sendSection,
  );
}

export const screen = {
  path: "/exceptions",
  title: "Ngoại lệ & gửi tay",
  capability: "SHADOW_READ",
  needsStore: true,
  render: render_,
};
